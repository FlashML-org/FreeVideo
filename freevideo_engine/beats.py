"""Beats of a song: tempo, beat times and the first beat of each bar, measured with numpy alone.

Adapted from the beat analysis of Imagination Studio by keys+Wasserman (https://github.com/drowzeys, Apache-2.0,
Copyright 2026 Keys and Sam Wasserman): onset strength is the rise of a log-scaled mel-like band spectrum, the tempo
the lag where that envelope repeats best (a bar of four counted in), the beats tracked by dynamic programming. Only
the analysis is taken; the sound is decoded here with PyAV.
"""

from __future__ import annotations

import math

RATE, N_FFT, HOP = 22050, 1024, 256
BPM_RANGE = (60.0, 200.0)
BANDS = 40
LOW_HZ = 200.0  # the bands under this are the kick and the bass, which mark the bar
VERSION = 1


def decode(path, rate: int = RATE):
    """The file's sound as mono float32 at ``rate``."""

    import av
    import numpy as np

    pieces = []
    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == 'audio'), None)
        if stream is None:
            raise ValueError('no sound')
        resampler = av.AudioResampler(format='fltp', layout='mono', rate=rate)
        for frame in container.decode(stream):
            pieces += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
        pieces += [f.to_ndarray().reshape(-1) for f in resampler.resample(None) or []]
    return np.concatenate(pieces).astype(np.float32) if pieces else np.zeros(0, np.float32)


def band_matrix(rate: int = RATE, n_fft: int = N_FFT, bands: int = BANDS):
    """(bins x bands) triangles on a mel scale from 30 Hz to 8 kHz, and which bands are the low end."""

    import numpy as np

    mel = lambda hz: 2595.0 * np.log10(1.0 + hz / 700.0)
    hz = lambda m: 700.0 * (10 ** (m / 2595.0) - 1.0)
    edges = hz(np.linspace(mel(30.0), mel(min(8000.0, rate / 2)), bands + 2))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / rate)
    out = np.zeros((len(freqs), bands), np.float32)
    for b in range(bands):
        lo, mid, hi = edges[b], edges[b + 1], edges[b + 2]
        rise = (freqs - lo) / max(mid - lo, 1e-9)
        fall = (hi - freqs) / max(hi - mid, 1e-9)
        out[:, b] = np.clip(np.minimum(rise, fall), 0, None)
    out /= np.maximum(out.sum(axis=0, keepdims=True), 1e-9)
    return out, edges[1:-1] < LOW_HZ



def onset_strength(y, rate: int = RATE, n_fft: int = N_FFT, hop: int = HOP):
    """(onset envelope, low-end envelope), one value a frame (frame i centred at i * hop samples): the positive rise
    of each band's level in dB from the frame before, summed over all bands and over the low bands."""

    import numpy as np

    y = np.asarray(y, np.float32)
    if len(y) < n_fft:
        y = np.pad(y, (0, n_fft - len(y)))
    padded = np.pad(y, (n_fft // 2, n_fft // 2), mode="reflect" if len(y) > n_fft // 2 else "constant")
    count = 1 + (len(padded) - n_fft) // hop
    window = np.hanning(n_fft).astype(np.float32)
    weights, low = band_matrix(rate, n_fft)
    levels = np.empty((count, weights.shape[1]), np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(padded, n_fft)[::hop][:count]
    for start in range(0, count, 4096):  # in pieces: a five-minute song is 26 000 frames
        chunk = frames[start:start + 4096] * window
        power = np.abs(np.fft.rfft(chunk, axis=1)) ** 2
        levels[start:start + len(chunk)] = power @ weights
    db = 10.0 * np.log10(np.maximum(levels, 1e-10))
    db = np.maximum(db, db.max() - 80.0)  # 80 dB below the loudest: silence does not flicker
    # the first frame rises from the quietest of the first quarter second, not from itself: a song that starts on its
    # first beat keeps that beat, and one that starts mid-sound gains no false one
    before = db[:max(1, int(0.25 * rate / hop))].min(axis=0, keepdims=True)
    rise = np.maximum(0.0, np.diff(db, axis=0, prepend=before))
    env, low_env = rise.mean(axis=1), rise[:, low].mean(axis=1) if low.any() else rise.mean(axis=1)

    def detrend(e):
        width = max(3, int(round(0.4 * rate / hop)))  # what rises above the last 0.4 s, not a slow swell
        width = max(1, min(width, len(e)))  # a clip shorter than the window still has a length-matched average
        local = np.convolve(e, np.ones(width) / width, mode="same")
        e = np.maximum(0.0, e - local)
        spread = e.std()
        return e / spread if spread > 0 else e
    return detrend(env), detrend(low_env)


def autocorrelation(env):
    import numpy as np

    n = len(env)
    spectrum = np.fft.rfft(env - env.mean(), 2 * n)
    return np.fft.irfft(np.abs(spectrum) ** 2)[:n]


def tempo(env, fps: float, low: float = BPM_RANGE[0], high: float = BPM_RANGE[1]) -> tuple[float, float]:
    """(period in frames, confidence 0..1): the lag between 60 and 200 BPM where the envelope repeats best, the lag
    of its 4/4 bar counted in, leaning gently toward 120 BPM."""

    import numpy as np

    ac = autocorrelation(env)
    if not len(ac) or ac[0] <= 0:
        return fps * 60.0 / 120.0, 0.0
    ac = ac / ac[0]
    # smoothed over a frame or two: a beat that falls between frames shares its peak between two lags
    kernel = np.exp(-0.5 * (np.arange(-4, 5) / 1.5) ** 2)
    ac = np.convolve(ac, kernel / kernel.sum(), mode="same")
    lags = np.arange(fps * 60.0 / high, fps * 60.0 / low, 0.02)
    at = lambda lag: np.interp(lag, np.arange(len(ac)), ac, right=0.0)
    # a beat's own lag and its bar's (four beats): an accented first beat backs the beat, and halving the tempo gains
    # nothing from it, since the half tempo's bar of eight beats is as strong
    comb = at(lags) + 0.5 * at(4 * lags)
    bpm = fps * 60.0 / lags
    lean = np.exp(-0.5 * (np.log2(bpm / 120.0) / 1.4) ** 2)  # a gentle lean toward 120 BPM: half or double tempo
    best = int(np.argmax(comb * lean))
    return float(lags[best]), float(np.clip(comb[best] / 1.5, 0.0, 1.0))


def track(env, period: float, tightness: float = 100.0):
    """Beat frames by dynamic programming: each beat's score is its onset plus the best earlier beat's score, less a
    penalty for a gap other than one period (on a log scale)."""

    import numpy as np

    n = len(env)
    score, back = np.array(env, np.float64), np.full(n, -1)
    lo, hi = int(round(period / 2)), int(round(2 * period))
    gaps = np.arange(lo, hi + 1)
    penalty = -tightness * np.log(gaps / period) ** 2
    for t in range(lo, n):
        first = max(0, t - hi)
        window = score[first:t - lo + 1][::-1]  # gaps lo .. lo + len - 1
        if not len(window):
            continue
        candidate = window + penalty[:len(window)]
        best = int(np.argmax(candidate))
        if candidate[best] > 0:
            score[t] = env[t] + candidate[best]
            back[t] = t - (lo + best)
    tail = max(0, n - int(round(period)))
    beat = tail + int(np.argmax(score[tail:])) if n else -1
    beats = []
    while beat >= 0:
        beats.append(beat)
        beat = int(back[beat])
    beats = np.array(beats[::-1], int)
    if len(beats):  # no beats before the music starts or after it stops
        strong = np.asarray(env)[beats]
        floor = 0.1 * np.median(strong) if len(strong) else 0
        keep = np.nonzero(strong > floor)[0]
        beats = beats[keep[0]:keep[-1] + 1] if len(keep) else beats[:0]
    return beats


def refine(env, frames):
    """Each beat frame moved to the top of its onset peak, between frames (a parabola through three values)."""

    import numpy as np

    out = []
    for f in frames:
        f = int(f)
        if 0 < f < len(env) - 1:
            a, b, c = env[f - 1], env[f], env[f + 1]
            bend = a - 2 * b + c
            shift = 0.5 * (a - c) / bend if bend < 0 else 0.0
            out.append(f + float(np.clip(shift, -0.5, 0.5)))
        else:
            out.append(float(f))
    return np.array(out)


def analyse_wave(y, rate: int = RATE) -> dict:
    import numpy as np

    duration = len(y) / rate
    env, low_env = onset_strength(y, rate)
    fps = rate / HOP
    period, confidence = tempo(env, fps)
    frames = track(env, period)
    if len(frames) < 2:
        return {"bpm": round(fps * 60 / period, 2), "beats": [], "downbeats": [], "bars": 0, "duration": round(duration, 3),
                "confidence": 0.0, "first": 0.0, "beatsPerBar": 4, "version": VERSION}
    times = refine(env, frames) * HOP / rate
    # the tempo from every beat at once: the slope of the beat times against their count
    index = np.arange(len(times))
    slope = float(np.polyfit(index, times, 1)[0]) if len(times) >= 4 else float(np.median(np.diff(times)))
    bpm = 60.0 / slope if slope > 0 else fps * 60 / period
    # the bar's first beat: the phase of four where the low end (kick, bass) and the onsets are strongest
    phases = []
    for k in range(min(4, len(frames))):
        chosen = frames[k::4]
        phases.append(float(low_env[chosen].mean() + 0.5 * env[chosen].mean()))
    # the strongest phase, a tie (within 2 %) to the earliest
    phase = next(k for k, v in enumerate(phases) if v >= max(phases) - 0.02 * abs(max(phases))) if phases else 0
    downbeats = times[phase::4]
    salience = float(np.clip(env[frames].mean() / (env.mean() * 4 + 1e-9), 0, 1)) if env.mean() > 0 else 0.0
    return {"bpm": round(bpm, 2), "beats": [round(float(t), 3) for t in times], "downbeats": [round(float(t), 3) for t in downbeats],
            "bars": int(len(downbeats)), "duration": round(duration, 3), "confidence": round(0.6 * confidence + 0.4 * salience, 2),
            "first": round(float(downbeats[0]), 3) if len(downbeats) else 0.0, "beatsPerBar": 4, "version": VERSION}


def analyse(path) -> dict:
    return analyse_wave(decode(path))
