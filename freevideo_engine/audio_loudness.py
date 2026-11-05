"""Loudness of a whole cut (ITU-R BS.1770-4 / EBU R128) and a true-peak limiter for export.

Planar float arrays [channels, samples]; channels all weigh 1 (mono and stereo, which is all the editor writes).
"""
import math

import numpy as np

ABSOLUTE_GATE = -70.
RELATIVE_GATE = -10.
BLOCK_SECONDS, STEP_SECONDS = .4, .1  # 400 ms blocks with 75 % overlap
SHORT_SECONDS = 3.
OVERSAMPLE = 4


def _k_filters(rate):
    """The two K-weighting stages (high shelf, then high pass) for any sample rate, as (b, a) pairs."""
    # Bilinear form of the BS.1770 prototypes (as in libebur128), which reproduces the 48 kHz table exactly.
    gain, q, fc = 3.999843853973347, .7071752369554196, 1681.974450955533
    k = math.tan(math.pi * fc / rate)
    vh = 10 ** (gain / 20)
    vb = vh ** .4996667741545416
    shelf_b = [vh + vb * k / q + k * k, 2 * (k * k - vh), vh - vb * k / q + k * k]
    shelf_a = [1 + k / q + k * k, 2 * (k * k - 1), 1 - k / q + k * k]
    q, fc = .5003270373238773, 38.13547087602444
    k = math.tan(math.pi * fc / rate)
    pass_b = [1., -2., 1.]
    pass_a = [1., 2 * (k * k - 1) / (1 + k / q + k * k), (1 - k / q + k * k) / (1 + k / q + k * k)]
    return [(np.array(shelf_b) / shelf_a[0], np.array(shelf_a) / shelf_a[0]), (np.array(pass_b), np.array(pass_a))]


def _planar(samples):
    samples = np.asarray(samples, dtype=np.float64)
    if samples.ndim == 1:
        samples = samples[None, :]
    if samples.ndim != 2 or not np.isfinite(samples).all():
        raise ValueError('Expected finite planar audio')
    return samples


def _block_powers(samples, rate, seconds, step=STEP_SECONDS):
    """Mean square of the K-weighted signal, summed over channels, for each window of `seconds` every `step`."""
    from scipy.signal import lfilter
    weighted = _planar(samples)
    for b, a in _k_filters(rate):
        weighted = lfilter(b, a, weighted, axis=1)
    size, hop = round(seconds * rate), round(step * rate)
    if weighted.shape[1] < size:
        return np.zeros(0)
    energy = np.concatenate([np.zeros(1), np.cumsum(np.square(weighted).sum(axis=0))])
    starts = np.arange(0, weighted.shape[1] - size + 1, hop)
    return (energy[starts + size] - energy[starts]) / size


def _lufs(power):
    return -.691 + 10 * np.log10(np.maximum(power, 1e-20))


def integrated(samples, rate):
    """Gated integrated loudness in LUFS; -inf when nothing is over the absolute gate."""
    powers = _block_powers(samples, rate, BLOCK_SECONDS)
    loud = powers[_lufs(powers) > ABSOLUTE_GATE]
    if not loud.size:
        return -math.inf
    gate = _lufs(loud.mean()) + RELATIVE_GATE
    kept = loud[_lufs(loud) > gate]
    return float(_lufs(kept.mean()))


def short_term(samples, rate, step=STEP_SECONDS):
    """Short-term loudness (3 s windows) every `step` seconds; value k covers [k*step, k*step + 3 s)."""
    return _lufs(_block_powers(samples, rate, SHORT_SECONDS, step))


def true_peak(samples, rate):
    """Highest absolute sample after 4x oversampling, in dBTP (-inf for silence)."""
    from scipy.signal import resample_poly
    samples = _planar(samples)
    if not samples.size:
        return -math.inf
    peak = float(np.max(np.abs(resample_poly(samples, OVERSAMPLE, 1, axis=1))))
    return 20 * math.log10(peak) if peak > 0 else -math.inf


class Limiter:
    """Streaming true-peak limiter: the output stays under `ceiling_db` (4x oversampled) and quiet passages pass unchanged.

    `push` returns the samples that are ready and `flush` the rest; together they return exactly the input length, with
    the output lagging the input inside the limiter. The gain ramps down over the look-ahead before a peak (no step, so
    no click) and comes back over `release_seconds`."""

    CONTEXT = 16  # input samples each side that the 4x interpolation filter reaches

    def __init__(self, ceiling_db, rate, channels=2, *, lookahead_seconds=.005, release_seconds=.1):
        # Aim 0.1 dB under the ceiling: the gain moves inside the interpolation filter's reach, which can add a few mdB.
        self.ceiling = 10 ** ((ceiling_db - .1) / 20)
        self.window = max(1, round(lookahead_seconds * rate)) | 1
        self.log_release = -1 / max(1., release_seconds * rate)
        self.past = np.zeros((channels, self.CONTEXT))
        self.pending = np.zeros((channels, 0))
        self.required = np.zeros(0)
        self.ramp = None  # the last window-1 look-ahead minimums; at the start, the first one repeated
        self.gain = 1.

    def _measure(self, final):
        """Extend `required` (the gain each pending sample needs on its own) as far as the filter context allows."""
        from scipy.signal import resample_poly
        start, size, edge = self.required.size, self.pending.shape[1], self.CONTEXT
        end = size if final else size - edge
        if end <= start:
            return
        context = np.concatenate([self.past, self.pending], axis=1)[:, start:end + 2 * edge]
        up = np.abs(resample_poly(context, OVERSAMPLE, 1, axis=1)).max(axis=0)
        up = np.pad(up, (0, max(0, (end - start + edge) * OVERSAMPLE - up.size)))
        peaks = up[edge * OVERSAMPLE:(end - start + edge) * OVERSAMPLE].reshape(end - start, OVERSAMPLE).max(axis=1)
        peaks = np.maximum(peaks, np.abs(self.pending[:, start:end]).max(axis=0))
        self.required = np.concatenate([self.required, np.minimum(1., self.ceiling / np.maximum(peaks, 1e-12))])

    def push(self, block):
        block = _planar(block)
        self.pending = np.concatenate([self.pending, block], axis=1)
        self._measure(False)
        return self._emit(max(0, self.required.size - (self.window - 1)))

    def flush(self):
        self._measure(True)
        return self._emit(self.pending.shape[1])

    def _release(self, target):
        """g[i] = min(target[i], 1 - (1 - g[i-1]) * r), in closed form chunk by chunk."""
        out = np.empty_like(target)
        held = 1 - self.gain
        chunk = max(1, int(30 / -self.log_release))
        for start in range(0, target.size, chunk):
            dip = np.maximum(1 - target[start:start + chunk], 0.)
            steps = np.arange(dip.size) * self.log_release
            with np.errstate(divide='ignore'):
                depth = np.exp(np.maximum.accumulate(np.log(dip) - steps) + steps)
            depth = np.maximum(depth, held * np.exp(steps + self.log_release))
            out[start:start + dip.size] = 1 - depth
            held = depth[-1]
        self.gain = 1 - held
        return out

    def _emit(self, count):
        from scipy.ndimage import minimum_filter1d
        channels = self.pending.shape[0]
        if count <= 0:
            return np.zeros((channels, 0))
        half = self.window // 2
        need = np.pad(self.required, (0, max(0, count + self.window - 1 - self.required.size)), constant_values=1.)
        lowest = minimum_filter1d(need[:count + self.window - 1], self.window, mode='nearest')[half:half + count]
        if self.ramp is None:
            self.ramp = np.full(self.window - 1, lowest[0])
        joined = np.concatenate([self.ramp, lowest])
        total = np.concatenate([[0.], np.cumsum(joined)])
        target = (total[self.window:] - total[:-self.window])[:count] / self.window
        self.ramp = joined[-(self.window - 1):] if self.window > 1 else self.ramp
        out = self.pending[:, :count] * self._release(target)[None, :]
        self.past = np.concatenate([self.past, self.pending[:, :count]], axis=1)[:, -self.CONTEXT:]
        self.pending = self.pending[:, count:]
        self.required = self.required[count:]
        return out
