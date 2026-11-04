"""Split a soundtrack into dialogue, music, and ambience and effects.

The model is a band-split recurrent network (Bandit, cocktail-party version) that works on 44.1 kHz mono
signals. A track is cut into overlapping 6 s chunks (3 s hop, Hann window), a few chunks go through the
model at a time, and the windowed outputs are added back, so the peak GPU memory does not depend on the
length of the track and every output has exactly the length of the input.
"""
from fractions import Fraction
import logging
import os
from pathlib import Path
import wave

import numpy as np

SAMPLE_RATE = 44100
CHUNK_SECONDS = 6.0
HOP_SECONDS = 3.0
BATCH = 4
# The network leaves speech in the music part when the whole track is quiet (an old recording at -37 dBFS comes
# back with an empty voice part). A quiet track is lifted to this level for the model; the parts are scaled back.
QUIET_RMS = .04
TARGET_RMS = .08
MAX_LIFT = 12.
MIX_NAME = 'mix.wav'

# Model output name -> track name. The order is the order of the sub-tracks in the editor.
STEMS = (('speech', 'dialogue'), ('music', 'music'), ('effects', 'ambience'))
MODEL_KWARGS = dict(
    in_channel=1, stems=[name for name, _ in STEMS], band_specs='musical', n_bands=64, fs=SAMPLE_RATE,
    require_no_overlap=False, require_no_gap=True, normalize_channel_independently=False,
    treat_channel_as_feature=True, n_sqm_modules=8, emb_dim=128, rnn_dim=256, bidirectional=True,
    rnn_type='GRU', mlp_dim=512, hidden_activation='Tanh', complex_mask=True)


class SeparationError(Exception):
    pass


def load_model(weights, device='cuda'):
    """The network with its weights; weights is a plain state-dict file (loaded without unpickling objects)."""
    import torch
    from .bandit.wrapper import MultiMaskMultiSourceBandSplitRNN
    model = MultiMaskMultiSourceBandSplitRNN(**MODEL_KWARGS)
    state = torch.load(weights, map_location='cpu', weights_only=True)
    model.load_state_dict(state)
    return model.to(device).eval()


def model_function(model, device):
    """Chunks [n, 1, samples] -> {model stem: [n, 1, samples]}, run without gradients."""
    import torch

    def run(chunks):
        with torch.inference_mode():
            _, output = model({'audio': {'mixture': chunks.to(device)}})
        return {name: output['audio'][name].float().cpu() for name, _ in STEMS}
    return run


def lift_for_model(audio):
    """The gain that brings a quiet track (float [channels, samples]) to the level the model separates well; 1 otherwise."""
    level = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64)))) if audio.size else 0.
    if level <= 1e-5 or level >= QUIET_RMS:
        return 1.
    return min(TARGET_RMS / level, MAX_LIFT)


def separate_array(function, audio, *, chunk=None, hop=None, batch=BATCH, progress=lambda fraction: None):
    """audio: float tensor [channels, samples]; function maps chunks [n, 1, chunk] to {stem: [n, 1, chunk]}.

    Returns {stem: tensor [channels, samples]} with the length of the input. Every channel is separated on its own."""
    import torch
    chunk = chunk or int(CHUNK_SECONDS * SAMPLE_RATE)
    hop = hop or int(HOP_SECONDS * SAMPLE_RATE)
    channels, length = audio.shape
    if length == 0:
        raise SeparationError('empty')
    if chunk != 2 * hop:
        raise SeparationError('chunk must be twice the hop')
    # Half a chunk of mirrored context on both sides keeps the edges as well covered as the middle.
    edge = chunk - hop
    padded_length = max(length + 2 * edge, chunk)
    count = -(-(padded_length - chunk) // hop) + 1
    total = (count - 1) * hop + chunk
    padded = torch.zeros(channels, total)
    padded[:, edge:edge + length] = audio
    for left in range(1, edge + 1):  # mirror as far as the signal reaches; the rest stays silent
        if left < length:
            padded[:, edge - left] = audio[:, left]
            padded[:, edge + length - 1 + left] = audio[:, length - 1 - left]
    window = torch.hann_window(chunk, periodic=True)
    starts = [index * hop for index in range(count)]
    outputs = {name: torch.zeros(channels, total) for name, _ in STEMS}
    jobs = [(channel, start) for channel in range(channels) for start in starts]
    for first in range(0, len(jobs), batch):
        part = jobs[first:first + batch]
        pieces = torch.stack([padded[channel, start:start + chunk] for channel, start in part])[:, None, :]
        if torch.any(pieces != 0):
            result = function(pieces)
            for name, _ in STEMS:
                for row, (channel, start) in enumerate(part):
                    outputs[name][channel, start:start + chunk] += result[name][row, 0] * window
        progress(min(1., (first + len(part)) / len(jobs)))
    return {name: value[:, edge:edge + length].contiguous() for name, value in outputs.items()}


def read_audio(path):
    """Decode any audio or video file to float32 [2, samples] at 44.1 kHz; returns (audio, seconds)."""
    import av
    chunks = []
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise SeparationError('no_audio')
        resampler = av.AudioResampler(format='fltp', layout='stereo', rate=SAMPLE_RATE)
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray())
        for out in resampler.resample(None) or ():
            chunks.append(out.to_ndarray())
    if not chunks:
        raise SeparationError('no_audio')
    audio = np.concatenate(chunks, axis=1).astype(np.float32)
    return audio, audio.shape[1] / SAMPLE_RATE


def write_wav(path, audio, *, exact=False):
    """Atomically write float [2, samples] as 16-bit PCM at 44.1 kHz. `exact` scales by 32768, the divisor the readers use,
    so samples read from a 16-bit file are written back unchanged (the default scales by 32767)."""
    path = Path(path)
    audio = np.asarray(audio)
    pcm = (np.clip(np.rint(audio.T * 32768), -32768, 32767) if exact else np.rint(np.clip(audio.T, -1, 1) * 32767)).astype('<i2')
    temporary = path.with_name('.' + path.name + '.partial')
    try:
        with wave.open(str(temporary), 'wb') as output:
            output.setnchannels(audio.shape[0])
            output.setsampwidth(2)
            output.setframerate(SAMPLE_RATE)
            output.writeframes(pcm.tobytes())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_mp4(path, audio, bit_rate=192_000):
    """AAC in an audio-only MP4, the container the timeline takes for generated media; replaced atomically."""
    import av
    path = Path(path)
    audio = np.asarray(audio, np.float32)
    partial = path.with_name('.' + path.name + '.partial')
    try:
        with av.open(str(partial), 'w', format='mp4', options={'movflags': 'faststart'}) as writer:
            stream = writer.add_stream('aac', rate=SAMPLE_RATE, layout='stereo')
            stream.codec_context.bit_rate = bit_rate
            step = 1024 * 32
            for offset in range(0, audio.shape[1], step):
                frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(audio[:, offset:offset + step]),
                                                   format='fltp', layout='stereo')
                frame.sample_rate, frame.pts, frame.time_base = SAMPLE_RATE, offset, Fraction(1, SAMPLE_RATE)
                for packet in stream.encode(frame):
                    writer.mux(packet)
            for packet in stream.encode():
                writer.mux(packet)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def separate_file(source, out_dir, weights, *, device='cuda', progress=lambda fraction: None):
    """Write <out_dir>/dialogue, music and ambience as .wav (export) and .mp4 (timeline), plus the original mix as
    mix.wav; returns {track: wav path} and the log numbers."""
    import time
    import torch
    began = time.monotonic()
    audio, seconds = read_audio(source)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model = load_model(weights, device)
    if device == 'cuda':
        torch.cuda.reset_peak_memory_stats()
    lift = lift_for_model(audio)
    stems = separate_array(model_function(model, device), torch.from_numpy(audio * np.float32(lift)), progress=progress)
    if lift != 1.:
        stems = {name: track / lift for name, track in stems.items()}
    peak = torch.cuda.max_memory_allocated() / 2 ** 30 if device == 'cuda' else None
    write_wav(out_dir / MIX_NAME, audio)  # the original mix, exported next to the separated tracks
    paths = {}
    for name, track in STEMS:
        paths[track] = out_dir / (track + '.wav')
        write_wav(paths[track], stems[name].numpy())
        write_mp4(paths[track].with_suffix('.mp4'), stems[name].numpy())
    numbers = {'seconds': round(seconds, 3), 'elapsed': round(time.monotonic() - began, 2),
               'peak_gib': None if peak is None else round(peak, 2)}
    logging.info('[FreeVideo] Separated %.1f s of sound in %.1f s, GPU peak %s GiB', seconds, numbers['elapsed'],
                 numbers['peak_gib'])
    return paths, numbers


def export_tracks(source_dir, destination):
    """Copy the separated tracks and the original mix (wav) of one separation into destination; returns the names.

    Nothing is written unless all four files exist, and existing files of the same names are not overwritten."""
    import shutil
    source_dir, destination = Path(source_dir), Path(destination)
    names = [track + '.wav' for _, track in STEMS] + [MIX_NAME]
    missing = [name for name in names if not (source_dir / name).is_file()]
    if missing:
        raise SeparationError('missing: ' + ', '.join(missing))
    destination.mkdir(parents=True, exist_ok=True)
    clash = [name for name in names if (destination / name).exists()]
    if clash:
        raise SeparationError('exists: ' + ', '.join(clash))
    for name in names:
        shutil.copyfile(source_dir / name, destination / (name + '.partial'))
    for name in names:
        os.replace(destination / (name + '.partial'), destination / name)
    return names
