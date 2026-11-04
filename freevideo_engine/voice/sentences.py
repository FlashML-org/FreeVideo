"""Sentences of a separated voice: where each line starts and ends, and what it says.

The recogniser's word times are only a few tens of milliseconds sharp, and its word ends are capped, so they cannot
mark a pause. The boundaries come from the separated voice itself: stretches of speech energy, joined across gaps
shorter than `GAP`. The recogniser gives the words; a word belongs to the stretch that holds its middle.
"""
import numpy as np

GAP = .25            # a silence at least this long ends a line (the pauses a replacement must keep)
LONGEST = 29.        # longest line, seconds (the longest reference the voice model takes)
SHORTEST = .3        # a stretch shorter than this is a click or a breath, unless it holds a word
FRAME = .01
LEVEL = .06          # speech is above this share of the loud part of the voice (voiced RMS), ...
FLOOR = 4.           # ... and above this many times the quiet floor (10th percentile of the frames)
PAD = .03            # soft onsets and the tail of the last sound sit just outside the loud frames


def frame_levels(samples, rate):
    """RMS of every 10 ms frame of a mono signal."""
    size = max(1, int(round(rate * FRAME)))
    count = len(samples) // size
    if not count:
        return np.zeros(0, dtype=np.float32)
    return np.sqrt((np.asarray(samples[:count * size], dtype=np.float32).reshape(count, size) ** 2).mean(axis=1))


def speech_runs(samples, rate, *, gap=GAP):
    """[(start, end)] seconds of the voiced stretches; silences shorter than `gap` do not split a stretch."""
    levels = frame_levels(samples, rate)
    if not len(levels):
        return []
    loud = float(np.percentile(levels, 95))
    if loud <= 1e-4:
        return []
    floor = float(np.percentile(levels, 10))
    # a clip that is nearly all speech has a loud 10th percentile, so the floor term never passes a third of the loud part
    threshold = max(LEVEL * loud, min(FLOOR * floor, .3 * loud))
    voiced = levels > threshold
    runs, begin = [], None
    for index, flag in enumerate(voiced):
        if flag and begin is None:
            begin = index
        elif not flag and begin is not None:
            runs.append([begin, index])
            begin = None
    if begin is not None:
        runs.append([begin, len(voiced)])
    merged = []
    for first, last in runs:
        if merged and (first - merged[-1][1]) * FRAME < gap:
            merged[-1][1] = last
        else:
            merged.append([first, last])
    total = len(samples) / rate
    return [(max(0., first * FRAME - PAD), min(total, last * FRAME + PAD)) for first, last in merged]


def _join(words):
    text = ''
    for word in words:
        piece = word['text']
        if text and piece[:1].isascii() and piece[:1].isalnum() and text[-1].isascii():
            text += ' '
        text += piece
    return text


def _split_long(runs, samples, rate, longest):
    """A stretch longer than `longest` is cut at its quietest 10 ms near the middle of what is left."""
    levels = frame_levels(samples, rate)
    result = []
    for start, end in runs:
        while end - start > longest:
            low, high = int((start + longest * .5) / FRAME), int((start + longest) / FRAME)
            window = levels[low:high]
            cut = (low + int(np.argmin(window))) * FRAME if len(window) else start + longest
            result.append((start, cut))
            start = cut
        result.append((start, end))
    return result


def lines(words, samples, rate, *, gap=GAP, longest=LONGEST):
    """The lines of one voice: [{start, end, text, words}], in order, from word times and the voice samples.

    `words` are [{text, start, end}]. A stretch without a word is not a line; a word outside every stretch
    joins the nearest one, because the recogniser's times can lead or lag a little."""
    runs = _split_long(speech_runs(samples, rate, gap=gap), samples, rate, longest)
    held = [[] for _ in runs]
    for word in words:
        middle = (word['start'] + word['end']) / 2
        if not runs:
            break
        best = min(range(len(runs)), key=lambda i: 0. if runs[i][0] <= middle <= runs[i][1]
                   else min(abs(middle - runs[i][0]), abs(middle - runs[i][1])))
        held[best].append(word)
    result = []
    for (start, end), group in zip(runs, held):
        if not group:
            continue
        group.sort(key=lambda word: word['start'])
        result.append(dict(start=round(start, 3), end=round(end, 3), text=_join(group), words=group))
    # a stretch too short to dub joins the neighbour it is closest to when that one is near enough
    index = 0
    while index < len(result):
        row = result[index]
        if row['end'] - row['start'] < SHORTEST and len(result) > 1:
            before = result[index - 1] if index else None
            after = result[index + 1] if index + 1 < len(result) else None
            gaps = [(row['start'] - before['end'], index - 1) if before else None,
                    (after['start'] - row['end'], index + 1) if after else None]
            gaps = [item for item in gaps if item]
            near, neighbour = min(gaps)
            if near < .6 and (row['end'] - result[neighbour]['start'] if neighbour < index
                              else result[neighbour]['end'] - row['start']) <= longest:
                other = result[neighbour]
                first, second = (other, row) if neighbour < index else (row, other)
                merged = dict(start=first['start'], end=second['end'], text=_join(first['words'] + second['words']),
                              words=first['words'] + second['words'])
                low = min(index, neighbour)
                result[low:low + 2] = [merged]
                index = max(0, low)
                continue
        index += 1
    return result
