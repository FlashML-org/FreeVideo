"""Lay a generated piece under a cut: the cut's exact length, a level well under the video's own sound,
lower again while someone speaks, and a fade at each end. There is no volume or fade edit on the
timeline, so all of it is written into the file."""
from fractions import Fraction
import os
from pathlib import Path

import numpy as np

RATE = 48000
# Generated video sound sits near -13 dBFS RMS; the score stays about 14 dB under it.
LEVEL_DB = -27.
PEAK_DB = -6.
QUIET_DB = -8.
RAMP_SECONDS = .3
FADE_IN_SECONDS, FADE_OUT_SECONDS = 1., 2.


def _db(value):
    return 10. ** (value / 20.)


def envelope(length, quiet=(), fps=24):
    """Gain per sample: 1, and QUIET_DB lower over each (start, frames) range, with short ramps."""
    gain = np.ones(length, np.float32)
    ramp = max(1, round(RAMP_SECONDS * RATE))
    low = _db(QUIET_DB)
    for start, frames in quiet:
        first, last = round(start * RATE / fps), round((start + frames) * RATE / fps)
        begin, end = max(0, first - ramp), min(length, last + ramp)
        if begin >= end:
            continue
        position = np.arange(begin, end, dtype=np.float64)
        # Low inside, straight lines across the ramp just before and just after.
        into = np.clip((position - (first - ramp)) / ramp, 0, 1)
        out_of = np.clip(((last + ramp) - position) / ramp, 0, 1)
        gain[begin:end] = np.minimum(gain[begin:end], 1 - (1 - low) * np.minimum(into, out_of))
    return gain


def finish(samples, frames, fps=24, quiet=()):
    """float32 [2, n] at RATE → float32 [2, frames / fps seconds], levelled, lowered and faded."""
    length = round(frames * RATE / fps)
    samples = np.asarray(samples, np.float32)[:, :length]
    if samples.shape[1] < length:
        samples = np.pad(samples, ((0, 0), (0, length - samples.shape[1])))
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    peak = float(np.max(np.abs(samples))) if length else 0.
    if rms <= 0 or peak <= 0:
        raise ValueError('silent')
    gain = min(_db(LEVEL_DB) / rms, _db(PEAK_DB) / peak)
    shape = envelope(length, quiet, fps)
    # A short cut keeps most of its length at full level: each fade takes at most a quarter of it.
    fade_in = min(round(FADE_IN_SECONDS * RATE), length // 4)
    fade_out = min(round(FADE_OUT_SECONDS * RATE), length // 4)
    if fade_in:
        shape[:fade_in] *= np.linspace(0, 1, fade_in, endpoint=False, dtype=np.float32)
    if fade_out:
        shape[length - fade_out:] *= np.linspace(1, 0, fade_out, dtype=np.float32)
    return samples * np.float32(gain) * shape


def write_mp4(path, samples, bit_rate=192_000):
    """AAC in an audio-only MP4, the container the timeline takes for generated media; replaced atomically."""
    import av
    path = Path(path)
    samples = np.asarray(samples, np.float32)
    partial = path.with_name('.' + path.name + '.partial')
    try:
        with av.open(str(partial), 'w', format='mp4', options={'movflags': 'faststart'}) as writer:
            stream = writer.add_stream('aac', rate=RATE, layout='stereo')
            stream.codec_context.bit_rate = bit_rate
            step = 1024 * 32
            for offset in range(0, samples.shape[1], step):
                frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(samples[:, offset:offset + step]),
                                                   format='fltp', layout='stereo')
                frame.sample_rate, frame.pts, frame.time_base = RATE, offset, Fraction(1, RATE)
                for packet in stream.encode(frame):
                    writer.mux(packet)
            for packet in stream.encode():
                writer.mux(packet)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
