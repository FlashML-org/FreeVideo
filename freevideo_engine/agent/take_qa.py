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
TAIL_NOTE_SECONDS = 0.5       # a take that stops moving this long before its end is worth a look
TAIL_FREEZE_SECONDS = 1.0     # ...and this long it ended early (a held last beat is shorter)
FLICKER_JUMP = 10.0           # 0-255 mean change that is a cut, not motion
FLICKER_RATIO = 3.0           # ...and this many times larger than the motion that follows
FLICKER_LOOK = 6              # frames after the jump that set "the motion that follows"
SILENT_DB = -45.0             # whole-take RMS below this is near silence
GAP_DB = -50.0                # 50 ms windows below this are a gap
GAP_SECONDS = 2.0
GAP_EDGE = 0.5                # a gap counts as "in the middle" this far from both ends
SPEECH_DB = -35.0
CLIP_PEAK = 0.999
LINE_MATCH = 0.6
CUT_MATCH = 0.6               # s, a detected cut this close to a planned one is that cut
ASPECT_TOLERANCE = 0.03       # shape difference up to which a given frame is compared
HOLD_DIFF = 12.0              # 0-255 mean difference between a given frame and the take's own end frame
ANALYSIS_WIDTH = 64
SAMPLE_RATE = 16000
WINDOW = 0.05


_CJK = '\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af'
_WORD = re.compile("[%s]|(?:(?![%s])[\\w'])+" % (_CJK, _CJK))


def _words(text):
    """Words of a line; Chinese, Japanese and Korean have no spaces, so each of their characters is one."""
    return _WORD.findall(str(text).lower())


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


def recognise(path):
    """The words heard in a take, Chinese or English (the language is identified from the sound)."""
    from .. import subtitle_ears
    from .. import subtitle_sherpa  # noqa: F401  registers the recogniser
    engine = subtitle_ears.engine_for('transcribe')
    audio = subtitle_ears.decode_mono(path)
    if len(audio) <= subtitle_ears.RATE * .2:
        return ''
    heard = engine.transcribe(audio, 'auto', lambda fraction: None, lambda: False)
    return ' '.join(word['text'] for line in heard['lines'] for word in line['words'])


def _still(path, shape):
    """A given frame image as a small gray array of the analysis size, or None when it cannot be read."""
    try:
        from PIL import Image
        with Image.open(media._file(path)) as image:
            # A different shape is cropped or padded by the generator, so the two do not compare pixel for pixel.
            if abs(image.width / image.height - shape[1] / shape[0]) > ASPECT_TOLERANCE:
                return None
            return np.asarray(image.convert('L').resize((shape[1], shape[0])), np.float32)
    except Exception:  # noqa: BLE001 - an unreadable frame image only skips its check
        return None


def _edge_flicker(changes, times):
    """Seconds of a single odd frame at the start or the end: a jump to or from the neighbour that the
    motion beside it does not explain."""
    found = []
    if len(changes) > FLICKER_LOOK:
        after = max(changes[1:1 + FLICKER_LOOK])
        if changes[0] >= FLICKER_JUMP and changes[0] >= FLICKER_RATIO * max(after, 1e-3):
            found.append(('start', round(times[0], 2)))
        before = max(changes[-1 - FLICKER_LOOK:-1])
        if changes[-1] >= FLICKER_JUMP and changes[-1] >= FLICKER_RATIO * max(before, 1e-3):
            found.append(('end', round(times[-1], 2)))
    return found


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


def _shots(result, notes, small, times, planned):
    """Hard cuts inside the take: those the prompt did not ask for, planned ones that are missing, and the
    longest stretch without a cut."""
    from . import cut_pick
    cuts = cut_pick.cuts_in(small, times)
    edges = [0.0] + cuts + [times[-1]]
    result.update(cuts=cuts, longest_shot=round(max(b - a for a, b in zip(edges, edges[1:])), 2))
    if planned is None:
        return
    extra = [c for c in cuts if all(abs(c - p) > CUT_MATCH for p in planned)]
    lost = [p for p in planned if p > 0 and all(abs(c - p) > CUT_MATCH for c in cuts)]
    if extra:
        notes.append('picture cuts at %s that the prompt does not ask for' % ', '.join('%.1f s' % c for c in extra))
    if lost:
        notes.append('no picture cut at %s where the prompt has one' % ', '.join('%.1f s' % c for c in lost))


def _db(value):
    return float(20 * np.log10(max(float(value), 1e-9)))


def check(path, *, seconds=None, size=None, lines=None, transcribe=None, planned=None, first=None, last=None):
    """Inspect one take. `seconds` is the planned length, `size` the expected (width, height),
    `lines` the spoken lines the shot should contain and `transcribe(path)` returns the heard text.
    `planned` lists the seconds of the cuts the prompt asks for (empty: a single shot; None: not known),
    `first` and `last` are the given first and last frame images, if any."""
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
    changes = [float(np.abs(small[i] - small[i - 1]).mean()) for i in range(1, len(small))]
    flicker = _edge_flicker(changes, times[1:]) if changes else []
    # The tail: still for a while at the very end although the take moved before (shorter than a freeze).
    tail = 0
    while tail < len(changes) and changes[-1 - tail] <= FREEZE_DIFF:
        tail += 1
    tail_seconds = times[-1] - times[-1 - tail] if tail else 0.0
    ended = (round(times[-1 - tail], 2), round(times[-1], 2)) \
        if FREEZE_SECONDS > tail_seconds >= TAIL_NOTE_SECONDS and len(changes) > tail and max(changes[:-tail]) > FREEZE_DIFF * 20 else None
    if black:
        problems.append('black picture at %s' % ', '.join('%.1f-%.1f s' % s for s in black))
    if frozen:
        problems.append('frozen picture at %s' % ', '.join('%.1f-%.1f s' % s for s in frozen))
    if ended and ended[1] - ended[0] < TAIL_FREEZE_SECONDS:
        notes.append('picture still for the last %.1f s' % (ended[1] - ended[0]))
    elif ended:
        problems.append('picture stops at %.1f s, %.1f s before the end' % (ended[0], ended[1] - ended[0]))
    if flicker:
        notes.append('single odd frame at the %s (trim %s)' % (' and '.join(w for w, _ in flicker), ' and '.join('the first frame' if w == 'start' else 'the last frame' for w, _ in flicker)))
    result.update(black=black, frozen=frozen, flicker=[w for w, _ in flicker], stopped=ended)
    if len(small) > 2:
        _shots(result, notes, small, times, planned)
        for name, given, frame in (('first', first, small[0] if small else None), ('last', last, small[-1])):
            still = _still(given, frame.shape) if given else None
            if still is not None:
                held = float(np.abs(still - frame).mean())
                result[name + '_frame_diff'] = round(held, 1)
                if held > HOLD_DIFF:
                    notes.append('the %s frame is not the given one' % name)

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
        try:
            heard = _words(transcribe(path))
        except Exception as error:  # no speech model is no reason to drop the take
            notes.append('lines not checked (%s)' % (getattr(error, 'reason', None) or type(error).__name__))
            return dict(result, problems=problems, notes=notes, ok=not problems)
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
