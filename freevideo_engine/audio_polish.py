"""Gentle post-decode soundtrack polish. Does not load MiniMax H3.

H3 audio often arrives with DC/rumble, a 50/60 Hz buzz, and dialog that is
much quieter or louder than the next clip. This pass is CPU-only: high-pass,
optional mains notch, a light presence/harshness tilt, then RMS + peak
normalize with a limiter. It cannot remove model buzzing the way a denoise
model would; it only stops rumble and wild level swings.
"""
import math
import os

import numpy as np

HIGHPASS_HZ = 80
HUM_CANDIDATES = (50, 60)
HUM_RATIO = 8.
TARGET_RMS = 10 ** (-20 / 20)  # about -20 dBFS, spoken-dialog range
PEAK_CEILING = 10 ** (-1 / 20)  # -1 dBTP
MAX_BOOST_DB = 10
MAX_CUT_DB = 8
PRESENCE_HZ = 2800
PRESENCE_GAIN_DB = 1.5
HARSH_HZ = 7500
HARSH_GAIN_DB = -2.5


def _biquad_highpass(fs, f0, q=0.707):
    w0 = 2 * math.pi * f0 / fs
    cosw, sinw = math.cos(w0), math.sin(w0)
    alpha = sinw / (2 * q)
    b0, b1, b2 = (1 + cosw) / 2, -(1 + cosw), (1 + cosw) / 2
    a0, a1, a2 = 1 + alpha, -2 * cosw, 1 - alpha
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def _biquad_notch(fs, f0, q=30):
    w0 = 2 * math.pi * f0 / fs
    cosw, sinw = math.cos(w0), math.sin(w0)
    alpha = sinw / (2 * q)
    b0, b1, b2 = 1, -2 * cosw, 1
    a0, a1, a2 = 1 + alpha, -2 * cosw, 1 - alpha
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def _biquad_peaking(fs, f0, gain_db, q=0.9):
    a = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / fs
    cosw, sinw = math.cos(w0), math.sin(w0)
    alpha = sinw / (2 * q)
    b0 = 1 + alpha * a
    b1 = -2 * cosw
    b2 = 1 - alpha * a
    a0 = 1 + alpha / a
    a1 = -2 * cosw
    a2 = 1 - alpha / a
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def _biquad_highshelf(fs, f0, gain_db, q=0.707):
    a = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / fs
    cosw, sinw = math.cos(w0), math.sin(w0)
    alpha = sinw / (2 * q)
    two_sqrt = 2 * math.sqrt(a) * alpha
    b0 = a * ((a + 1) + (a - 1) * cosw + two_sqrt)
    b1 = -2 * a * ((a - 1) + (a + 1) * cosw)
    b2 = a * ((a + 1) + (a - 1) * cosw - two_sqrt)
    a0 = (a + 1) - (a - 1) * cosw + two_sqrt
    a1 = 2 * ((a - 1) - (a + 1) * cosw)
    a2 = (a + 1) - (a - 1) * cosw - two_sqrt
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def apply_biquad(samples, coeffs):
    b0, b1, b2, a1, a2 = coeffs
    x1 = x2 = y1 = y2 = 0.
    out = np.empty_like(samples)
    for i, x in enumerate(samples):
        y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        out[i] = y
        x2, x1 = x1, x
        y2, y1 = y1, y
    return out


def _mono(array):
    return array.mean(axis=0) if array.ndim == 2 and array.shape[0] > 1 else array.reshape(-1)


def detect_hum(array, sample_rate):
    mono = _mono(array)
    if mono.size < sample_rate // 4:
        return None, 0.
    window = mono[: min(mono.size, int(sample_rate * 4))]
    window = window - window.mean()
    spec = np.abs(np.fft.rfft(window * np.hanning(window.size)))
    freqs = np.fft.rfftfreq(window.size, 1 / sample_rate)
    best, ratio = None, 0.
    for hz in HUM_CANDIDATES:
        if hz >= sample_rate / 2:
            continue
        band = (freqs >= hz - 1.5) & (freqs <= hz + 1.5)
        near = (freqs >= hz - 12) & (freqs <= hz + 12) & ~band
        if not band.any() or not near.any():
            continue
        peak = float(spec[band].max())
        floor = float(np.median(spec[near]) + 1e-12)
        value = peak / floor
        if value > ratio:
            best, ratio = hz, value
    if ratio >= HUM_RATIO:
        return best, ratio
    return None, ratio


def _rms(array):
    return float(np.sqrt(np.mean(np.square(array), dtype=np.float64)))


def _peak(array):
    return float(np.max(np.abs(array))) if array.size else 0.


def limiter(array, ceiling=PEAK_CEILING):
    peak = _peak(array)
    if peak <= ceiling or peak <= 0:
        return array
    return np.tanh(array * (1.2 / peak)) * (ceiling / math.tanh(1.2))


def enabled():
    value = os.environ.get('FREEVIDEO_AUDIO_POLISH', '1').strip().lower()
    return value not in ('0', 'off', 'false', 'no')


def polish(audio, sample_rate, *, apply=None):
    """Return (audio, metrics). Artifacts should keep the unpolished decode."""
    apply = enabled() if apply is None else bool(apply)
    torch_audio = hasattr(audio, 'detach')
    array = audio.detach().cpu().float().numpy() if torch_audio else np.asarray(audio, dtype=np.float32)
    if array.ndim == 1:
        array = array[None, :]
    array = np.ascontiguousarray(array, dtype=np.float32)
    metrics = dict(applied=False, sample_rate=int(sample_rate),
                   peak_in=round(_peak(array), 5), rms_in=round(_rms(array), 5))
    if not apply or array.size == 0 or not np.isfinite(array).all():
        return audio, metrics
    if metrics['peak_in'] < 1e-4:
        metrics['skipped'] = 'silence'
        return audio, metrics
    work = array - array.mean(axis=1, keepdims=True)
    for c in range(work.shape[0]):
        work[c] = apply_biquad(work[c], _biquad_highpass(sample_rate, HIGHPASS_HZ))
    hum_hz, hum_ratio = detect_hum(work, sample_rate)
    metrics['hum_hz'] = hum_hz
    metrics['hum_ratio'] = round(float(hum_ratio), 2)
    if hum_hz:
        for c in range(work.shape[0]):
            work[c] = apply_biquad(work[c], _biquad_notch(sample_rate, hum_hz))
    for c in range(work.shape[0]):
        work[c] = apply_biquad(work[c], _biquad_peaking(sample_rate, PRESENCE_HZ, PRESENCE_GAIN_DB))
        work[c] = apply_biquad(work[c], _biquad_highshelf(sample_rate, HARSH_HZ, HARSH_GAIN_DB))
    rms, peak = _rms(work), _peak(work)
    gain = TARGET_RMS / max(rms, 1e-8)
    boost_cap = 10 ** (MAX_BOOST_DB / 20)
    cut_cap = 10 ** (-MAX_CUT_DB / 20)
    if peak * min(gain, boost_cap) > PEAK_CEILING and peak > 0:
        gain = min(gain, PEAK_CEILING / peak)
    gain = min(max(gain, cut_cap), boost_cap)
    work = limiter(work * gain)
    fade = min(int(0.005 * sample_rate), work.shape[1] // 8)
    if fade > 1:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)
        work[:, :fade] *= ramp
        work[:, -fade:] *= ramp[::-1]
    metrics.update(applied=True, peak_out=round(_peak(work), 5), rms_out=round(_rms(work), 5),
                   gain_db=round(20 * math.log10(max(gain, 1e-8)), 2),
                   highpass_hz=HIGHPASS_HZ)
    if torch_audio:
        import torch
        return torch.from_numpy(np.ascontiguousarray(work)), metrics
    return work, metrics
