"""16-bit PCM wav files, read and written without an audio library."""
import wave

import numpy as np


def read(path):
    """Float32 samples shaped (frames, channels) and the sample rate of a 16-bit PCM wav."""
    with wave.open(str(path), 'rb') as file:
        if file.getsampwidth() != 2:
            raise ValueError('unsupported_wav')
        channels, rate = file.getnchannels(), file.getframerate()
        raw = file.readframes(file.getnframes())
    data = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.
    return data.reshape(-1, channels), rate


def write(path, samples, rate):
    """Write mono (n,) or (n, channels) float samples in [-1, 1] as 16-bit PCM."""
    data = np.asarray(samples, dtype=np.float32)
    channels = 1 if data.ndim == 1 else data.shape[1]
    pcm = np.round(np.clip(data, -1., 1.) * 32767.).astype('<i2')
    with wave.open(str(path), 'wb') as file:
        file.setnchannels(channels)
        file.setsampwidth(2)
        file.setframerate(int(rate))
        file.writeframes(pcm.tobytes())
