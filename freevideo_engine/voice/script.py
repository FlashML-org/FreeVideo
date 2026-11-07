"""A script given with a selection: what each line is to say, and who says it.

The recogniser misspells and misses (a name, a number, a word shorter than a third of a second). When the text of a line
is known, it replaces the recognised text and keeps the recogniser only for the times: the letters of the known text are
laid on the letters that were heard, and the ones that were not heard are spread between their neighbours.
"""
import re
from difflib import SequenceMatcher

FLOOR = .55          # least likeness of a recognised line to a script line for them to be the same line
BALANCE = .6         # a recognised line is the script line only if neither is much longer than the other
SPAN = 4             # most recognised lines (a pause cuts one sentence in pieces) that one script line may cover
COVER = .85          # the lines a script line covers must say at least this much of it
GAP = 2.             # longest silence, seconds, inside one script line cut by a pause
LONGEST = 29.        # longest line, seconds (as in sentences.LONGEST)
MINIMUM = .06        # no word is given less time than this, seconds
_CJK = '぀-ヿ㐀-鿿가-힯'
_PIECE = re.compile('[%s]|(?:[^\\W\\d_%s]\\.){2,}(?:[^\\W\\d_%s](?!\\w))?|[^\\W_%s]+(?:[\'’.,-][^\\W_%s]+)*%%?' % (_CJK, _CJK, _CJK, _CJK, _CJK))


def letters(text):
    return ''.join(char for char in text.lower() if char.isalnum())


def pieces(text):
    """The words of a text (a character each for Chinese, Japanese and Korean)."""
    return [found.group(0) for found in _PIECE.finditer(text)]


def match(lines, script):
    """For each recognised line, the script entry it is (or None), in order; an entry is used once.

    `lines` are [{text}], `script` [{text, speaker?}]. Of equally alike entries the first unused one is taken."""
    return [entry for _, entry in _assign(lines, script)]


def _assign(lines, script):
    """[(number of lines covered, entry)] for each line: the first of a covered run holds the run, the rest (0, None)."""
    wanted = [letters(entry['text']) for entry in script]
    used, result, at = set(), [], 0
    while at < len(lines):
        best, likeness, span = None, FLOOR, 1
        for count in range(1, min(SPAN, len(lines) - at) + 1):
            group = lines[at:at + count]
            heard = letters(' '.join(line['text'] for line in group))
            for index, text in enumerate(wanted):
                if index in used or not heard or not text or min(len(heard), len(text)) < BALANCE * max(len(heard), len(text)):
                    continue
                if count > 1 and not _one_line(group, text):
                    continue
                ratio = SequenceMatcher(None, heard, text, autojunk=False).ratio()
                if ratio >= FLOOR and (best is None or ratio > likeness):
                    best, likeness, span = index, ratio, count
        if best is not None:
            used.add(best)
        result.append((span, None if best is None else script[best]))
        result.extend([(0, None)] * (span - 1))
        at += span
    return result


def _one_line(group, text):
    """Whether the recognised `group` of lines is one script line (letters `text`) cut by pauses: each line is the part
    of it that stands in its place, together they say all of it, and no silence or length is more than one line has."""
    parts = [letters(line['text']) for line in group]
    if sum(map(len, parts)) < COVER * len(text):
        return False
    here = 0
    for part in parts:
        if SequenceMatcher(None, part, text[here:here + len(part)], autojunk=False).ratio() < FLOOR:
            return False
        here += len(part)
    if all('start' in line and 'end' in line for line in group):
        return (group[-1]['end'] - group[0]['start'] <= LONGEST
                and all(after['start'] - before['end'] <= GAP for before, after in zip(group, group[1:])))
    return True


def merge(lines, script):
    """(lines, entries) where the recognised lines that one script line covers are joined into one line.

    A sentence cut by a pause is two recognised lines; joined, it is said once. `lines` are [{text, words, start, end}]."""
    rows, entries, at = [], [], 0
    for span, entry in _assign(lines, script):
        if span == 0:
            continue
        group = lines[at:at + span]
        at += span
        row = dict(group[0])
        if span > 1:
            row.update(words=[word for line in group for word in line['words']], end=group[-1]['end'],
                       text=' '.join(line['text'] for line in group))
        rows.append(row)
        entries.append(entry)
    return rows, entries


def retime(words, text, start, end):
    """The words of `text` with times taken from the recognised `words` ([{text, start, end}]) of the same line.

    Letters that were heard keep their time; the others share what lies between the heard ones, in proportion to their
    count, inside [start, end]."""
    heard, spans = [], []
    for word in words:
        chars = letters(word['text'])
        for index, char in enumerate(chars):
            share = (word['end'] - word['start']) / max(len(chars), 1)
            heard.append(char)
            spans.append((word['start'] + index * share, word['start'] + (index + 1) * share))
    given = pieces(text)
    chars = []
    for index, piece in enumerate(given):
        chars.extend((char, index) for char in letters(piece))
    times = [None] * len(chars)
    for block in SequenceMatcher(None, ''.join(heard), ''.join(char for char, _ in chars), autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            times[block.b + offset] = spans[block.a + offset]
    anchors = [-1] + [index for index, found in enumerate(times) if found] + [len(chars)]
    for before, after in zip(anchors, anchors[1:]):
        if after - before <= 1:
            continue
        first = times[before][1] if before >= 0 else start
        last = times[after][0] if after < len(chars) else end
        last = max(last, first)
        for offset in range(before + 1, after):
            step = (last - first) / (after - before - 1)
            times[offset] = (first + (offset - before - 1) * step, first + (offset - before) * step)
    result = []
    for index, piece in enumerate(given):
        own = [times[at] for at, (_, word) in enumerate(chars) if word == index]
        if not own:
            continue
        result.append(dict(text=piece, start=round(min(span[0] for span in own), 3), end=round(max(span[1] for span in own), 3)))
    previous = start
    for word in result:
        word['start'] = round(max(word['start'], previous), 3)
        word['end'] = round(max(word['end'], word['start'] + MINIMUM), 3)
        previous = word['start']
    return result


def roles(script):
    """Role name -> number, in order of first appearance."""
    numbers = {}
    for entry in script:
        name = (entry.get('speaker') or '').strip()
        if name and name not in numbers:
            numbers[name] = len(numbers)
    return numbers
