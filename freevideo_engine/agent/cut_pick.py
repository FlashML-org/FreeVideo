"""Cut detection, per-segment checks and stitching of same-shot takes.

Pure CPU helpers: `detect_cuts` finds the picture cuts of a take, `segment_qa` applies the take checks to one
segment, `seam` measures how a join between two takes sounds and `stitch` picks the best take for every
segment. Thresholds are those of take_qa.
"""
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import take_qa
from .take_qa import _db, _runs

CUT_DIFF = 25.0          # 0-255 mean change between consecutive frames of a hard cut
CUT_SPIKE = 3.0          # a cut stands out this much from the frame changes around it
MATCH_DIFF = 12.0        # weakest change accepted as an expected cut
SEAM_LEVEL_DB = 20.0     # level step at which the sound penalty is full
SEAM_CLICK = 0.5         # sample jump at which the click penalty is full
SEAM_SPEECH = 0.4        # penalty added for a join inside speech
SEAM_QUIET = 0.25        # weight of level and click at a join in silence


@lru_cache(maxsize=4)
def _decoded(path, stamp):
    return take_qa._pictures(path)


@lru_cache(maxsize=4)
def _heard(path, stamp):
    return take_qa._samples(path)


def _stamp(path):
    stat = Path(path).stat()
    return stat.st_mtime_ns, stat.st_size


def _pictures(path):
    """take_qa's frames, decoded once while the same file is checked part by part."""
    return _decoded(str(path), _stamp(path))


def _samples(path):
    return _heard(str(path), _stamp(path))


def _changes(small):
    return [0.0] + [float(np.abs(small[i] - small[i - 1]).mean()) for i in range(1, len(small))]


def _spike(diffs, index, lo, hi, floor):
    """Whether frame `index` is a cut: a large change that stands out from its neighbours in lo..hi."""
    around = [diffs[i] for i in range(lo, hi) if i != index]
    return diffs[index] >= floor and diffs[index] >= CUT_SPIKE * (float(np.median(around)) if around else 0.0)


def cuts_in(small, times):
    """Seconds of the hard picture cuts in decoded frames, ascending, excluding 0 and the end."""
    diffs = _changes(small)
    return [round(times[i], 3) for i in range(1, len(times))
            if (diffs[i] >= CUT_DIFF and diffs[i] >= diffs[i - 1] and diffs[i] > (diffs[i + 1] if i + 1 < len(diffs) else -1)
                and _spike(diffs, i, max(1, i - 3), min(len(diffs), i + 4), CUT_DIFF))]


def detect_cuts(path, expected=None, *, tolerance=0.6):
    """Seconds of the hard picture cuts, ascending, excluding 0 and the end. With `expected`, each expected cut
    takes the strongest frame change within `tolerance` s; one with no such change is None, not guessed."""
    _, small, times = _pictures(path)
    if not expected:
        return cuts_in(small, times)
    diffs = _changes(small)
    found = []
    for wanted in expected:
        near = [i for i in range(1, len(times)) if abs(times[i] - wanted) <= tolerance]
        best = max(near, key=lambda i: (diffs[i], -abs(times[i] - wanted)), default=None)
        if best is not None and _spike(diffs, best, max(1, min(near) - 1), min(len(diffs), max(near) + 2), MATCH_DIFF):
            found.append(round(times[best], 3))
        else:
            found.append(None)
    return found


def segment_qa(path, start, end):
    """Black picture, frozen picture and a long silence in the middle of speech, inside [start, end)."""
    problems, notes = [], []
    means, small, times = _pictures(path)
    keep = [i for i, t in enumerate(times) if start <= t < end]
    if keep:
        means, small, times = [means[i] for i in keep], [small[i] for i in keep], [times[i] for i in keep]
        black = _runs([m < take_qa.BLACK_LUMA and float(g.std()) < take_qa.BLACK_SPREAD for m, g in zip(means, small)],
                      times, take_qa.BLACK_SECONDS)
        flags = [False] * len(small)
        for index in range(1, len(small)):
            if float(np.abs(small[index] - small[index - 1]).mean()) <= take_qa.FREEZE_DIFF:
                flags[index] = flags[index - 1] = True
        frozen = _runs(flags, times, take_qa.FREEZE_SECONDS)
        if black:
            problems.append('black picture at %s' % ', '.join('%.1f-%.1f s' % s for s in black))
        if frozen:
            problems.append('frozen picture at %s' % ', '.join('%.1f-%.1f s' % s for s in frozen))
    samples = _samples(path)[int(start * take_qa.SAMPLE_RATE):int(end * take_qa.SAMPLE_RATE)]
    step = int(take_qa.SAMPLE_RATE * take_qa.WINDOW)
    count = samples.size // step
    if count:
        levels = [_db(np.sqrt(np.mean(samples[i * step:(i + 1) * step] ** 2))) for i in range(count)]
        moments = [start + i * take_qa.WINDOW for i in range(count)]
        if any(level > take_qa.SPEECH_DB for level in levels):
            gaps = [(a, b) for a, b in _runs([level < take_qa.GAP_DB for level in levels], moments, take_qa.GAP_SECONDS)
                    if a > start + take_qa.GAP_EDGE and b < moments[-1] - take_qa.GAP_EDGE]
            if gaps:
                problems.append('silent gap at %s' % ', '.join('%.1f-%.1f s' % g for g in gaps))
    return dict(problems=problems, notes=notes, ok=not problems)


def _side(samples, at, span, before):
    rate = take_qa.SAMPLE_RATE
    n = max(1, int(span * rate))
    cut = min(samples.size, max(0, int(round(at * rate))))
    return samples[max(0, cut - n):cut] if before else samples[cut:cut + n]


def _seam(a, a_end, b, b_start, span):
    left, right = _side(a, a_end, span, True), _side(b, b_start, span, False)
    if not left.size or not right.size:
        return dict(level_db=0.0, click=0.0, in_speech=False, penalty=0.0)
    levels = [_db(np.sqrt(np.mean(x ** 2))) for x in (left, right)]
    level_db = abs(levels[0] - levels[1])
    click = float(abs(right[0] - left[-1]))
    in_speech = any(level > take_qa.SPEECH_DB for level in levels)
    base = 0.6 * min(1.0, level_db / SEAM_LEVEL_DB) + 0.4 * min(1.0, click / SEAM_CLICK)
    penalty = base + SEAM_SPEECH if in_speech else base * SEAM_QUIET
    return dict(level_db=round(level_db, 2), click=round(click, 4), in_speech=in_speech,
                penalty=round(min(1.0, penalty), 4))


def seam(a_path, a_end, b_path, b_start, *, span=0.1):
    """How a join sounds: level step over `span` s each side, sample jump, whether it falls in speech, penalty 0..1."""
    return _seam(_samples(a_path), a_end, _samples(b_path), b_start, span)


def _whole(candidates):
    best = min(candidates, key=lambda c: (-sum(c.get('scores') or [0]), c['take']))
    return dict(pieces=[dict(take=best['take'], start=0.0, end=best['seconds'])],
                score=round(float(sum(best.get('scores') or [0])), 6), fallback=True)


def stitch(candidates, *, seam_weight=0.5):
    """Choose a take for every segment, maximizing segment scores minus seam_weight * 10 * seam penalties.
    A join between different takes inside speech is not allowed. Falls back to the best whole take when cut counts
    differ, a segment has no usable candidate or no allowed sequence of takes exists."""
    if not candidates:
        return dict(pieces=[], score=0.0, fallback=True)
    candidates = sorted(candidates, key=lambda c: c['take'])
    count = len(candidates[0]['cuts']) + 1
    if any(len(c['cuts']) + 1 != count or len(c['scores']) != count or len(c['qa']) != count for c in candidates):
        return _whole(candidates)
    bounds = {c['take']: [0.0] + [float(t) for t in c['cuts']] + [float(c['seconds'])] for c in candidates}
    usable = [[c for c in candidates if c['qa'][k]] for k in range(count)]
    if not all(usable):
        return _whole(candidates)
    audio, joins = {}, {}

    def penalty(a, b, k):
        """The seam penalty, or None when the join is not allowed: a join between takes inside speech, or unreadable."""
        key = (a['take'], b['take'], k)
        if key not in joins:
            try:
                for c in (a, b):
                    if c['take'] not in audio:
                        audio[c['take']] = _samples(c['path'])
                row = _seam(audio[a['take']], bounds[a['take']][k + 1], audio[b['take']], bounds[b['take']][k + 1], 0.1)
                joins[key] = None if row['in_speech'] else row['penalty']
            except Exception:
                joins[key] = None
        return joins[key]

    # state: candidate take -> (score, takes so far); ties keep the smaller take sequence
    state = {c['take']: (float(c['scores'][0]), [c['take']]) for c in usable[0]}
    for k in range(1, count):
        step = {}
        for c in usable[k]:
            options = []
            for p in usable[k - 1]:
                if p['take'] not in state:
                    continue
                score, path = state[p['take']]
                cost = 0.0
                if p['take'] != c['take']:
                    seam_penalty = penalty(p, c, k - 1)
                    if seam_penalty is None:
                        continue
                    cost = seam_weight * 10 * seam_penalty
                options.append((round(score + float(c['scores'][k]) - cost, 9), path + [c['take']]))
            if options:
                step[c['take']] = min(options, key=lambda o: (-o[0], o[1]))
        if not step:
            return _whole(candidates)
        state = step
    score, takes = min(state.values(), key=lambda o: (-o[0], o[1]))
    pieces = []
    for k, take in enumerate(takes):
        start, end = bounds[take][k], bounds[take][k + 1]
        if pieces and pieces[-1]['take'] == take:
            pieces[-1]['end'] = end
        else:
            pieces.append(dict(take=take, start=start, end=end))
    return dict(pieces=pieces, score=round(score, 6), fallback=False)
