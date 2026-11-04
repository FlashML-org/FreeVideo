"""Automatic checks on a finished take: picture, sound and dialogue.

The result is plain data stored next to the take. `problems` lists what makes a take unusable, `notes`
what is only worth a look; the director's judge drops takes with problems and mentions the notes.
"""
import difflib
import re

import numpy as np

from . import media

DURATION_TOLERANCE = 1.5      # s, against the shot's planned length
BLACK_LUMA = 26               # 0-255 mean luma of a black frame
BLACK_SECONDS = 0.3
BLACK_SPREAD = 10.0           # a dark but detailed scene is not a black frame
FREEZE_DIFF = 0.05            # 0-255 mean change between consecutive frames
FREEZE_SECONDS = 1.5
SILENT_DB = -45.0             # whole-take RMS below this is near silence
GAP_DB = -50.0                # 50 ms windows below this are a gap
GAP_SECONDS = 2.0
GAP_EDGE = 0.5                # a gap counts as "in the middle" this far from both ends
SPEECH_DB = -35.0
CLIP_PEAK = 0.999
LINE_MATCH = 0.6
ANALYSIS_WIDTH = 64
SAMPLE_RATE = 16000
WINDOW = 0.05


def _words(text):
    return re.findall(r"[\w']+", str(text).lower())


def _pictures(path):
    """Mean luma and a small gray frame for every video frame."""
    means, small, times = [], [], []
    with media._reading(media._file(path)) as container:
        stream = media._video_stream(container)
        for frame in container.decode(stream):
            height = max(2, round(ANALYSIS_WIDTH * frame.height / frame.width))
            gray = frame.reformat(width=ANALYSIS_WIDTH, height=height, format='gray').to_ndarray().astype(np.float32)
            means.append(float(gray.mean()))
            small.append(gray)
            times.append(float(frame.time) if frame.time is not None else len(times) / 24.0)
    return means, small, times


def _samples(path):
    import av
    with media._reading(media._file(path)) as container:
        stream = next(iter(container.streams.audio), None)
        if stream is None:
            return np.zeros(0, np.float32)
        resampler = av.AudioResampler(format='flt', layout='mono', rate=SAMPLE_RATE)
        chunks = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, np.float32)


def _runs(flags, times, minimum):
    """Spans (start, end) where `flags` stays true for at least `minimum` seconds."""
    spans, start = [], None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        if start is not None and (not flag or index == len(flags) - 1):
            last = index if flag else index - 1
            if times[last] - times[start] >= minimum:
                spans.append((round(times[start], 2), round(times[last], 2)))
            start = None
    return spans


def _db(value):
    return float(20 * np.log10(max(float(value), 1e-9)))


def check(path, *, seconds=None, size=None, lines=None, transcribe=None):
    """Inspect one take. `seconds` is the planned length, `size` the expected (width, height),
    `lines` the spoken lines the shot should contain and `transcribe(path)` returns the heard text."""
    problems, notes = [], []
    info = media.inspect(path)
    if size and (info['width'], info['height']) != tuple(size):
        problems.append('size %dx%d, expected %dx%d' % (info['width'], info['height'], *size))
    if seconds and abs(info['seconds'] - seconds) > DURATION_TOLERANCE:
        problems.append('duration %.1f s, planned %.1f s' % (info['seconds'], seconds))
    result = dict(width=info['width'], height=info['height'], seconds=round(info['seconds'], 2), audio=info['has_audio'])

    means, small, times = _pictures(path)
    black = _runs([m < BLACK_LUMA and float(g.std()) < BLACK_SPREAD for m, g in zip(means, small)], times, BLACK_SECONDS) if means else []
    flags = [False] * len(small)
    for index in range(1, len(small)):
        if float(np.abs(small[index] - small[index - 1]).mean()) <= FREEZE_DIFF:
            flags[index] = flags[index - 1] = True
    frozen = _runs(flags, times, FREEZE_SECONDS)
    if black:
        problems.append('black picture at %s' % ', '.join('%.1f-%.1f s' % s for s in black))
    if frozen:
        problems.append('frozen picture at %s' % ', '.join('%.1f-%.1f s' % s for s in frozen))
    result.update(black=black, frozen=frozen)

    if not info['has_audio']:
        problems.append('no audio track')
    else:
        samples = _samples(path)
        if samples.size:
            rms, peak = float(np.sqrt(np.mean(samples ** 2))), float(np.abs(samples).max())
            result.update(rms_db=round(_db(rms), 1), peak=round(peak, 3))
            if _db(rms) < SILENT_DB:
                problems.append('audio nearly silent (%.0f dBFS)' % _db(rms))
            elif peak >= CLIP_PEAK:
                notes.append('audio clips')
            step = int(SAMPLE_RATE * WINDOW)
            count = samples.size // step
            levels = [_db(np.sqrt(np.mean(samples[i * step:(i + 1) * step] ** 2))) for i in range(count)]
            moments = [i * WINDOW for i in range(count)]
            if any(level > SPEECH_DB for level in levels):
                gaps = [(a, b) for a, b in _runs([level < GAP_DB for level in levels], moments, GAP_SECONDS)
                        if a > GAP_EDGE and b < moments[-1] - GAP_EDGE]
                if gaps:
                    notes.append('silent gap at %s' % ', '.join('%.1f-%.1f s' % g for g in gaps))
                result['gaps'] = gaps

    if lines and transcribe is not None and info['has_audio']:
        heard = _words(transcribe(path))
        missing = []
        for line in lines:
            wanted = _words(line)
            if not wanted:
                continue
            best = max([difflib.SequenceMatcher(None, wanted, heard[i:i + len(wanted) + 2]).ratio()
                        for i in range(max(1, len(heard) - len(wanted) + 3))] or [0.0])
            if best < LINE_MATCH:
                missing.append(line)
        result['heard'] = ' '.join(heard)
        if missing:
            problems.append('line not heard: %s' % ' | '.join(missing))
    return dict(result, problems=problems, notes=notes, ok=not problems)


def summary(qa):
    """One line for the judge's prompt."""
    if not qa:
        return ''
    if qa.get('error'):
        return 'QA unavailable'
    parts = qa['problems'] + qa['notes']
    return 'QA: ' + ('; '.join(parts) if parts else 'clean')
