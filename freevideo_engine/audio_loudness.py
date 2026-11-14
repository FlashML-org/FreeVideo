"""Loudness of a whole cut (ITU-R BS.1770-4 / EBU R128) and a true-peak limiter for export.

Planar float arrays [channels, samples]; channels all weigh 1 (mono and stereo, which is all the editor writes).
"""
import math

import numpy as np

from .app_control import scipy_module

ABSOLUTE_GATE = -70.
RELATIVE_GATE = -10.
BLOCK_SECONDS, STEP_SECONDS = .4, .1  # 400 ms blocks with 75 % overlap
SHORT_SECONDS = 3.
OVERSAMPLE = 4


# The 4x interpolation filter, scipy.signal.firwin(81, 1/4, window=('kaiser', 5.0)) * 4 (SciPy 1.18.1), first half and
# centre; the second half mirrors it exactly. Kept as numbers so exports do not need SciPy, and checked against SciPy
# by the tests.
_HALF = (
    -1.4319646207876959e-18, -0.001130597579184462, -0.0021031678617178763,
    -0.0019016679348927714, 3.709041094023897e-18, 0.002926514108961888,
    0.005018530923471822, 0.004252319066855107, -6.989122304361602e-18,
    -0.005932051843783716, -0.009790833971198894, -0.008026125461265495,
    1.1214237028997423e-17, 0.010605090076805452, 0.017115155028976117,
    0.013753959070631754, -1.6183952382363033e-17, -0.017579165251157446,
    -0.027983277010958117, -0.02221988556769976, 2.1564830647503023e-17,
    0.02786691635952088, 0.044051484099836266, 0.034795254750361125,
    -2.6922559134588193e-17, -0.04342308644211485, -0.06868853498568138,
    -0.05442549106878252, 3.1772982945983186e-17, 0.06897233879817498,
    0.11058973007292547, 0.08928334853204617, -3.564465655764782e-17,
    -0.12013435015454692, -0.20188442282305535, -0.17397774297154053,
    3.8143133424221195e-17, 0.2965428196043752, 0.6334760868798734,
    0.8996325710102085, 1.000636565089112,
)
OVERSAMPLE_TAPS = np.array(_HALF + _HALF[-2::-1])


REACH = (OVERSAMPLE_TAPS.size - 1) // 2 // OVERSAMPLE  # input samples the filter looks ahead and behind
BLOCK = 4096  # input samples per pass: small enough to stay in the processor cache


def _interpolate(samples):
    channels, size = samples.shape
    out = np.empty((channels, size * OVERSAMPLE))
    for phase in range(OVERSAMPLE):
        total = np.zeros((channels, size))
        for j in range((OVERSAMPLE_TAPS.size - 1 - phase) // OVERSAMPLE, -1, -1):
            shift = REACH - j
            low, high = max(0, -shift), min(size, size - shift)
            if low < high:
                total[:, low:high] += OVERSAMPLE_TAPS[phase + OVERSAMPLE * j] * samples[:, low + shift:high + shift]
        out[:, phase::OVERSAMPLE] = total
    return out


def oversample(samples):
    """4x interpolation of planar float64 audio, the same values SciPy's `resample_poly(samples, 4, 1, axis=1)` gives.

    Each output is the sum of its filter taps in descending tap order, which is the order SciPy adds them in, so the
    result matches it bit for bit; only the taps that meet a sample are added (the others would add zero). Blocks
    carry the neighbouring samples the filter reaches, so the join between two blocks is the same as inside one."""
    channels, size = samples.shape
    if size <= BLOCK:
        return _interpolate(samples)
    out = np.empty((channels, size * OVERSAMPLE))
    for start in range(0, size, BLOCK):
        end = min(size, start + BLOCK)
        low, high = max(0, start - REACH), min(size, end + REACH)
        block = _interpolate(samples[:, low:high])
        out[:, start * OVERSAMPLE:end * OVERSAMPLE] = block[:, (start - low) * OVERSAMPLE:(end - low) * OVERSAMPLE]
    return out


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
    lfilter = scipy_module('scipy.signal').lfilter
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
    samples = _planar(samples)
    if not samples.size:
        return -math.inf
    peak = float(np.max(np.abs(oversample(samples))))
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
        start, size, edge = self.required.size, self.pending.shape[1], self.CONTEXT
        end = size if final else size - edge
        if end <= start:
            return
        context = np.concatenate([self.past, self.pending], axis=1)[:, start:end + 2 * edge]
        up = np.abs(oversample(context)).max(axis=0)
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
        minimum_filter1d = scipy_module('scipy.ndimage').minimum_filter1d
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
