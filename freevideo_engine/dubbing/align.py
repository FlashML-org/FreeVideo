"""Fit a synthesized line to a target duration, whatever backend made it.

A backend exposes `synth(speed)` -> (mono float32 samples, sample rate); `speed`
above 1 makes the line shorter. The first pass is natural speed, the second
pass uses the measured ratio, and a third corrects what is still outside the
tolerance. The voiced part (first to last loud 5 ms frame) is then closed to the wanted length by a
pitch-preserving tempo change within `max_stretch`, and placed at the wanted onset in a file of exactly
the target number of samples: silence fills what is short. Voice that cannot fit is never cut: the file
is then longer than the target and reports `overrun` seconds (`fitted` false). A few milliseconds of the quiet
edge around the voiced part are kept, so soft consonants and the decay of the last sound survive.
"""
from fractions import Fraction

import numpy as np

TOLERANCE = 0.02
FIT_SECONDS = 0.04
FADE = 0.003
EDGE = 0.02
MEASURE_FRAME = 0.005
MAX_STRETCH = 1.25
SPEED_RANGE = (0.6, 1.8)


LEAD_KEEP = 0.05
TRAIL_KEEP = 0.12
SILENCE_LEVEL = 0.02
SPAN_SHARE = 0.02   # a line starts and ends where the smoothed energy crosses this share of its peak
NOISE_MARGIN = 4.   # the voiced level is at least this many times the noise floor (10th percentile of the frames)
NOISE_CAP = 0.1     # ... but never above this share of the loudest frame


def seconds(samples, rate):
    return len(samples) / rate


def trim_silence(samples, rate, lead_keep=LEAD_KEEP, trail_keep=TRAIL_KEEP, level=SILENCE_LEVEL):
    """Cut the quiet ends a model leaves around a line, keeping a short margin of each.

    The language model sometimes opens with seconds of silence; measuring that as speech would make
    every pass miss the target.  Quiet is below `level` times the loudest 10 ms frame.
    """
    samples = np.asarray(samples, dtype=np.float32)
    frame = max(1, rate // 100)
    count = len(samples) // frame
    if count < 2:
        return samples
    rms = np.sqrt((samples[:count * frame].reshape(count, frame) ** 2).mean(axis=1))
    loud = np.nonzero(rms > level * rms.max())[0]
    if not len(loud):
        return samples
    start = max(0, int(loud[0] * frame - lead_keep * rate))
    end = min(len(samples), int((loud[-1] + 1) * frame + trail_keep * rate))
    return samples[start:end]


def stretch(samples, rate, ratio):
    """Return `samples` played `ratio` times faster (ratio > 1 shortens), pitch kept."""
    import av
    graph = av.filter.Graph()
    source = graph.add_abuffer(format='flt', sample_rate=rate, layout='mono', time_base=Fraction(1, rate))
    tempo = graph.add('atempo', '%.6f' % ratio)
    sink = graph.add('abuffersink')
    source.link_to(tempo)
    tempo.link_to(sink)
    graph.configure()
    frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(samples, dtype=np.float32)[None, :],
                                       format='flt', layout='mono')
    frame.sample_rate, frame.pts = rate, 0
    graph.push(frame)
    graph.push(None)
    parts = []
    while True:
        try:
            parts.append(sink.pull().to_ndarray()[0])
        except (av.error.EOFError, av.error.BlockingIOError):
            break
    return np.concatenate(parts).astype(np.float32) if parts else np.zeros(0, np.float32)


def voiced_span(samples, rate, level=SILENCE_LEVEL):
    """(first, end) sample of the voiced part: from the first to the last loud 5 ms frame, or None."""
    samples = np.asarray(samples, dtype=np.float32)
    frame = max(1, int(rate * MEASURE_FRAME))
    count = len(samples) // frame
    if count < 1:
        return None
    rms = np.sqrt((samples[:count * frame].reshape(count, frame) ** 2).mean(axis=1))
    if not rms.max() > 0:
        return None
    # A recording with music or room noise comes back with that noise floor: the level rises above it
    # (never past NOISE_CAP of the loudest frame), and a lone loud frame is not voice.
    threshold = min(max(level * rms.max(), NOISE_MARGIN * np.percentile(rms, 10)), NOISE_CAP * rms.max())
    above = rms > threshold
    joined = above & (np.concatenate([above[1:], [False]]) | np.concatenate([[False], above[:-1]]))
    loud = np.nonzero(joined if joined.any() else above)[0]
    if not len(loud):
        return None
    return int(loud[0] * frame), int((loud[-1] + 1) * frame)


def energy_span(samples, rate, share=SPAN_SHARE):
    """(first, end) sample where the 5 ms smoothed energy stays above `share` of its peak: the definition
    the timeline of an original line is taken with. None for silence."""
    data = np.asarray(samples, dtype=np.float64)
    window = max(1, int(rate * MEASURE_FRAME))
    if len(data) < window:
        return None
    total = np.concatenate([[0.], np.cumsum(data * data)])
    half = window // 2
    centre = np.arange(len(data))
    low, high = np.clip(centre - half, 0, len(data)), np.clip(centre - half + window, 0, len(data))
    level = np.sqrt((total[high] - total[low]) / window)
    if not level.max() > 0:
        return None
    loud = np.nonzero(level > share * level.max())[0]
    return int(loud[0]), int(loud[-1]) + 1


def place(voice, rate, total, onset, lead=0, trail=0):
    """`voice` (`lead` quiet samples, the voiced part, `trail` quiet samples) laid into silence of
    round(total*rate) samples so that the voiced part starts at `onset`; returns (samples, overrun).

    `overrun` is the number of voiced samples past the end of the line. The file is then longer by that
    much and nothing is cut; otherwise the quiet tail is trimmed to the exact length. Also returns the
    voiced part's first and end sample in the result: the placement is known, so it is not measured again
    (a line that fills its slot has no silence left, and the measure's noise floor would then be speech)."""
    size = int(round(total * rate))
    voiced = len(voice) - lead - trail
    start = int(round(onset * rate)) - lead
    take = np.array(voice, dtype=np.float32)
    if start < 0:
        take, start, lead = take[-start:], 0, lead + start
    overrun = max(0, start + lead + voiced - size)
    if overrun <= int(MEASURE_FRAME * rate):  # under the resolution of the measurement
        overrun = 0
        take = take[:size - start]
    length = max(size, start + len(take))
    first = start + max(lead, 0)
    last = min(first + voiced, start + len(take))
    for width, edge in ((int(FADE * rate), 'in'), (int(FADE * rate), 'out')):
        width = min(width, len(take))
        if width > 1:
            ramp = np.linspace(0, 1, width, dtype=np.float32)
            if edge == 'in':
                take[:width] *= ramp
            else:
                take[-width:] *= ramp[::-1]
    out = np.zeros(length, np.float32)
    out[start:start + len(take)] = take
    return out, overrun, first, last


def fit(synth, target, *, onset=0., offset=None, tolerance=TOLERANCE, max_stretch=MAX_STRETCH,
        speed_range=SPEED_RANGE, check=lambda: None):
    """Run the passes; return dict(samples, rate, seconds, onset, offset, error, onset_error,
    offset_error, speed, passes, stretch,
    fitted, warning, overrun, natural). `samples` has exactly round(target*rate) samples unless the voice overruns; the voiced part is
    wanted from `onset` to `offset` (default: the whole line)."""
    if not target > 0:
        raise ValueError('Target duration must be positive')
    offset = target if offset is None else offset
    wanted = offset - onset
    if not 0 <= onset < offset <= target + 1e-9:
        raise ValueError('The wanted voiced part must lie inside the line')
    low, high = speed_range
    tried = []

    def run(speed):
        check()
        samples, rate = synth(speed)
        span = voiced_span(samples, rate)
        if span is None:
            raise ValueError('The dubbing backend returned no audio')
        samples = np.asarray(samples, dtype=np.float32)
        edge = int(EDGE * rate)
        voice = samples[span[0]:span[1]]
        # quiet samples around the voiced part, kept as they are (not stretched)
        edges = (samples[max(0, span[0] - edge):span[0]], samples[span[1]:span[1] + edge])
        tried.append((speed, voice, rate, seconds(voice, rate), edges))
        return tried[-1]

    # A line that is too long is first closed by the tempo change, which keeps the voice natural; the model
    # speed only takes what the tempo change cannot (a faster model speed costs naturalness quickly).
    # A line that is too short is slowed by the tempo change in the same way, inside the same range.
    def aim(length):
        return min(max(length, wanted / (max_stretch * .96)), wanted * max_stretch * .96)   # inside this range no model speed is needed

    def off(entry):
        return abs(entry[3] - aim(entry[3])) / wanted

    speed = 1.0
    entry = run(speed)
    while off(entry) > tolerance and len(tried) < 3:
        # the length scales with 1/speed, so the next speed is the measured ratio
        speed = min(max(speed * entry[3] / aim(entry[3]), low), high)
        if speed == tried[-1][0]:
            break
        entry = run(speed)
    speed, voice, rate, length, edges = min(tried, key=off)
    padded_edges = edges

    def compose(start, width):
        """The voiced part closed to `width` seconds and placed from `start`; (out, overrun, first, last, ratio)."""
        source, ratio, current, length_now = voice, 1.0, voice, length
        for _ in range(2):
            want = min(max(ratio * length_now / width, 1 / max_stretch), max_stretch)
            if abs(want - ratio) < 0.001 and ratio != 1.0 or abs(length_now - width) <= 0.004 and ratio == 1.0:
                break
            ratio = want
            current = stretch(source, rate, ratio)
            length_now = seconds(current, rate)
        padded = np.concatenate([padded_edges[0], current, padded_edges[1]])
        out, overrun, first, last = place(padded, rate, target, start, lead=len(padded_edges[0]),
                                          trail=len(padded_edges[1]))
        return out, overrun, first, last, ratio

    # The placed file is measured the way the originals' timeline is (smoothed energy above a share of its
    # peak): the tempo change and the quiet edges spread a little energy past the voiced part, so the first
    # placement is corrected by what is measured, twice at most.
    start, width = onset, wanted
    best = None
    for attempt in range(3):
        out, overrun, first, last, ratio = compose(start, width)
        span = energy_span(out, rate)
        first, last = (span[0], span[1]) if span else (first, last)
        first, last = first / rate, last / rate
        miss = max(abs(first - onset), abs(last - offset)) + (overrun > 0)
        if best is None or miss < best[0]:
            best = (miss, out, overrun, first, last, ratio)
        if miss <= 0.004 or attempt == 2:
            break
        start = max(0., start + (onset - first))
        width = max(0.25 * wanted, width + (wanted - (last - first)))
    _, out, overrun, first, last, ratio = best
    error = (last - first) - wanted
    onset_error, offset_error = first - onset, last - offset
    overrun = overrun / rate
    fitted = abs(onset_error) <= FIT_SECONDS and abs(offset_error) <= FIT_SECONDS and not overrun
    warning = None if fitted else 'too_long' if (overrun or offset_error > 0) else 'too_short'
    return dict(samples=out, rate=rate, seconds=len(out) / rate, onset=first, offset=last, error=error,
                onset_error=onset_error, offset_error=offset_error,
                speed=speed, passes=len(tried), stretch=ratio, fitted=fitted, warning=warning, overrun=overrun,
                natural=tried[0][3])
