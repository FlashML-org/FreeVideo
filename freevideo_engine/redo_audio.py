"""Make new sound for a clip and leave its picture as it is.

The clip is read whole at the model's canvas and encoded with the video encoder. Every picture token
is kept (the video mask selects nothing) and every sound frame is redone, so the sampler draws only the
sound, listening to the held picture. The saved clip is the original's own video packets with the new
track beside them; the picture is never decoded or encoded again.
"""
import math
from fractions import Fraction
from pathlib import Path

import numpy as np

from .continuation import validate_path
from .continuation_tail import (AUDIO_RATE, FPS, TICK_SAMPLES, UNREADABLE, _fit, _picture, _rate)

MIN_FRAMES = 39
FRAME_STEP = 17
TOO_SHORT = 'The video is too short to make new sound for; it needs to be at least 1.7 s long.'
TOO_LONG = 'The video is too long to make new sound for in one go.'
MAX_FRAMES = 243
SIZE_MISMATCH = 'The video has a different shape than this canvas.'


def usable_frames(count):
    """The longest length the model draws that fits in `count` frames (5 + 17k), at 24 fps."""
    if count < MIN_FRAMES:
        raise ValueError(TOO_SHORT)
    return min(MAX_FRAMES, MIN_FRAMES + (count - MIN_FRAMES) // FRAME_STEP * FRAME_STEP)


def sound_ticks(frames):
    return round(frames * 40 / 24)


def probe(video_path):
    """How many frames of the video the new sound covers; reads the file's packets and decodes nothing."""
    import av
    try:
        with av.open(str(validate_path(video_path))) as container:
            if not container.streams.video:
                raise ValueError(UNREADABLE)
            stream = container.streams.video[0]
            count = sum(1 for packet in container.demux(stream) if packet.size)
            rate = _rate(stream)
    except (av.FFmpegError, OSError) as error:
        raise ValueError(UNREADABLE) from error
    return usable_frames(count if abs(rate - FPS) <= .01 else round(count * FPS / rate))


def read(video_path, canvas, directory):
    """The first usable frames of the video at the canvas size, as a file of this run.

    A video at another frame rate gives the pictures a 24 fps copy of it. Runs without a GPU.
    """
    import av
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with av.open(str(validate_path(video_path))) as container:
            if not container.streams.video:
                raise ValueError(UNREADABLE)
            rate = _rate(container.streams.video[0])
            pictures = [_picture(frame) for frame in container.decode(video=0)]
    except (av.FFmpegError, OSError) as error:
        raise ValueError(UNREADABLE) from error
    if not pictures:
        raise ValueError(UNREADABLE)
    width, height = pictures[0].size
    if abs(width * canvas['height'] - height * canvas['width']) > 32 * max(width, height):
        raise ValueError(SIZE_MISMATCH)
    total = len(pictures) if abs(rate - FPS) <= .01 else round(len(pictures) * FPS / rate)
    frames = usable_frames(total)
    picked = (range(frames) if abs(rate - FPS) <= .01
              else [min(len(pictures) - 1, round(index * rate / FPS)) for index in range(frames)])
    target = directory / 'redo-audio-clip.npy'
    np.save(target, np.stack([np.asarray(_fit(pictures[index], canvas['width'], canvas['height'])) for index in picked]),
            allow_pickle=False)
    return {'frames': frames, 'clip': str(target), 'source': {'width': width, 'height': height, 'fps': rate}}


def encode(clip, base, *, device='cuda', before_upload=None):
    """The clip's video latent (1, C, F, H/16, W/16) as a float tensor on the CPU."""
    import gc
    import torch
    from .media_encoding import encode_visual
    from .vae_weights import load_video_encoder
    vae, _ = load_video_encoder(base, before_upload=None if before_upload is None else
                                lambda model: before_upload(model, False), device=device)
    pixels = np.load(clip, mmap_mode='r', allow_pickle=False)
    latent = encode_visual(vae, pixels, device=device, posterior='mode')
    del vae, pixels
    gc.collect()
    if torch.device(device).type == 'cuda':
        torch.cuda.empty_cache()
    return latent


def mask_data(latent):
    """The sampler's input for sound only: no picture token is redrawn and no sound is held."""
    rows, cols = latent.shape[3] // 2, latent.shape[4] // 2
    return dict(video=latent, video_mask=np.zeros((latent.shape[2], rows, cols), dtype=bool),
                audio=None, audio_mask='redo')


def decode_sound(audio_latents, base, frames, *, device='cuda'):
    """Float samples (2, n) at 32 kHz for `frames` picture frames, from the sampler's sound latents."""
    import gc
    import torch
    from .vae_weights import load_audio_vae
    vae, _ = load_audio_vae(base)
    vae.eval().requires_grad_(False)
    mean = torch.tensor(vae.config.latents_mean, device=device).view(1, -1, 1)
    std = torch.tensor(vae.config.latents_std, device=device).view(1, -1, 1)
    sound = vae.decode(audio_latents.to(device) * std + mean, return_dict=False)[0]
    sound = sound.float().permute(1, 0, 2)[0].cpu()
    rate = vae.config.sampling_rate
    del vae, mean, std
    gc.collect()
    if torch.device(device).type == 'cuda':
        torch.cuda.empty_cache()
    if rate != AUDIO_RATE:
        raise ValueError('New sound requires the pinned 32 kHz H3 VAE')
    return sound[:, :math.floor(frames * AUDIO_RATE / FPS)].numpy()


def mux(video_path, samples, output, frames):
    """The original's picture packets, untouched, with `samples` (2, n) as the sound.

    Packets are kept in decode order up to the last one that shows a frame inside the first `frames`
    frames. A kept B-frame may refer to a later frame that the decoder reads first, so a handful of frames
    past the cut (at most one group of B-frames) come along; the sound is padded with silence to cover them.
    Returns the length of the picture in seconds, whatever the original's frame rate.
    """
    import av
    from .comfy_metadata import _copy_stream
    output = Path(output)
    with av.open(str(video_path)) as reader:
        source = reader.streams.video[0]
        step = Fraction(1, 24) / source.time_base
        packets = [packet for packet in reader.demux(source)
                   if packet.dts is not None and packet.pts is not None and packet.size]
        begin = source.start_time if source.start_time is not None else min(packet.pts for packet in packets)
        inside = [index for index, packet in enumerate(packets) if (packet.pts - begin) // step < frames]
        kept = packets[:inside[-1] + 1]
        rate = _rate(source)
        last = max(kept, key=lambda packet: packet.pts)
        length = (last.pts + (last.duration or round(1 / rate / source.time_base)) - begin) * source.time_base
        seconds = float(length)
        with av.open(str(output), 'w', format='mp4', options={'movflags': 'use_metadata_tags+faststart'}) as writer:
            picture = _copy_stream(writer, source)
            audio = writer.add_stream('aac', rate=AUDIO_RATE)
            audio.layout = 'stereo'
            for packet in kept:
                packet.stream = picture
                writer.mux(packet)
            data = np.ascontiguousarray(samples, dtype=np.float32)
            wanted = math.floor(seconds * AUDIO_RATE)
            if data.shape[1] < wanted:
                data = np.pad(data, ((0, 0), (0, wanted - data.shape[1])))
            chunk, position = 1024, 0
            while position < data.shape[1]:
                frame = av.AudioFrame.from_ndarray(data[:, position:position + chunk], format='fltp', layout='stereo')
                frame.sample_rate = AUDIO_RATE
                frame.pts = position
                for packet in audio.encode(frame):
                    writer.mux(packet)
                position += chunk
            for packet in audio.encode(None):
                writer.mux(packet)
    return seconds
