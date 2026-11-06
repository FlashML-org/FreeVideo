"""A script given with a selection: what each line is to say, and who says it.

The recogniser misspells and misses (a name, a number, a word shorter than a third of a second). When the text of a line
is known, it replaces the recognised text and keeps the recogniser only for the times: the letters of the known text are
laid on the letters that were heard, and the ones that were not heard are spread between their neighbours.
"""
import re
from difflib import SequenceMatcher

FLOOR = .55          # least likeness of a recognised line to a script line for them to be the same line
MINIMUM = .06        # no word is given less time than this, seconds
_CJK = '぀-ヿ㐀-鿿가-힯'
_PIECE = re.compile('[%s]|[^\\W_%s]+(?:\'[^\\W_%s]+)?' % (_CJK, _CJK, _CJK))


def letters(text):
    return ''.join(char for char in text.lower() if char.isalnum())


def pieces(text):
    """The words of a text (a character each for Chinese, Japanese and Korean)."""
    return [found.group(0) for found in _PIECE.finditer(text)]


def match(lines, script):
    """For each recognised line, the script entry it is (or None), in order; an entry is used once.

    `lines` are [{text}], `script` [{text, speaker?}]."""
    wanted = [letters(entry['text']) for entry in script]
    used, found = set(), []
    for line in lines:
        heard = letters(line['text'])
        best, likeness = None, FLOOR
        for index, text in enumerate(wanted):
            if index in used or not heard or not text:
                continue
            ratio = SequenceMatcher(None, heard, text, autojunk=False).ratio()
            if ratio >= likeness:
                best, likeness = index, ratio
        if best is not None:
            used.add(best)
        found.append(None if best is None else script[best])
    return found


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
