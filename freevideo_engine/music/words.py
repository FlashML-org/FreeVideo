"""The user's words for a piece of music: what it should be, and the things it should leave out.

"不要鼓", "no drums": the music model reads a ruled-out thing in a description as one more thing to play, so the
phrase is taken out of the words and the thing is named to the planner as what the music should not sound like.
Only a ruling-out that begins with a thing this module knows is taken out; any other words stay as written.
"""
import re

_NEG_ZH = re.compile(r'(?:不要|没有|不用|别用|去掉|无)([^，,。；;、\s]+)')
_NEG_EN = re.compile(r'\b(?:with\s+)?(?:no|without|not any)\s+([a-z][a-z \-]*?)(?=[,.;]|$)', re.I)
# A ruling-out ends where the sentence turns: "不要鼓只要钢琴", "no drums but soft piano".
_TURN_ZH = re.compile(r'只|而|但|却|就')
_TURN_EN = re.compile(r'\s+(?:but|and|or|instead|just|only|with)\b.*$', re.I)
_LEAD_ZH = re.compile(r'^(?:任何|什么|一点)?')
# What a ruled-out word names, in the English the planner reads.
_THINGS = (
    (('鼓', '打击'), r'drums?|drum kit|percussion|beats?',
     ('drums', 'percussion', 'drum kit', 'beat', 'cymbals', 'snare', 'kick')),
    (('钢琴',), r'pianos?', ('piano',)),
    (('人声', '唱'), r'vocals?|voices?|singing', ('vocals', 'singing')),
    (('吉他',), r'guitars?', ('guitar',)),
    (('贝斯',), r'bass(?:es)?', ('bass',)),
    (('弦乐',), r'strings?', ('strings',)),
)


def _names(phrase, chinese):
    """The English names of the known things in a ruled-out phrase, when the phrase begins with one."""
    found, first = [], None
    for keys, pattern, english in _THINGS:
        if chinese:
            at = [phrase.find(key) for key in keys if key in phrase]
            at = min(at) if at else None
        else:
            match = re.search(r'\b(?:%s)\b' % pattern, phrase, re.I)
            at = match.start() if match else None
        if at is None:
            continue
        found += english
        first = at if first is None else min(first, at)
    if first is None:
        return []
    # "无尽的草原", "毫无压力": the negation has to name the thing straight away (two describing words allowed in English).
    if chinese and first != _LEAD_ZH.match(phrase).end():
        return []
    if not chinese and len(phrase[:first].split()) > 2:
        return []
    return found


def split(words):
    """(the words without the ruled-out phrases, the English names of the ruled-out things it knows).

    When no known thing is ruled out, the words come back as written and the names are empty."""
    spans, names = [], []
    for pattern, chinese in ((_NEG_ZH, True), (_NEG_EN, False)):
        for match in pattern.finditer(words):
            if chinese:
                phrase = _TURN_ZH.split(match.group(1), 1)[0]
            else:
                phrase = _TURN_EN.sub('', match.group(1))
            found = _names(phrase, chinese)
            if not found:
                continue
            spans.append((match.start(), match.start(1) + len(phrase)))
            names += [name for name in found if name not in names]
    if not spans:
        return words.strip(), []
    kept, at = '', 0
    for start, end in sorted(spans):
        if start >= at:
            kept, at = kept + words[at:start], end
    kept += words[at:]
    kept = re.sub(r'[ \t]+', ' ', kept)
    kept = re.sub(r'\s+([，,。；;])', r'\1', kept)
    kept = re.sub(r'([，,。；;])([，,。；;])+', r'\1', kept).strip(' ，,。；;')
    return kept, names


def _stem(name):
    word = name.split()[0].lower()
    return word[:-1] if word.endswith('s') and not word.endswith('ss') else word


def drop_clauses(description, names):
    """The description without its clauses that name a ruled-out thing as something to play.

    A clause that rules the thing out ("no drums") stays. If every clause names a ruled-out thing, removing them
    would leave nothing, so the description is returned unchanged."""
    if not names or not description:
        return description
    stems = '|'.join(sorted({re.escape(_stem(name)) for name in names}))
    named = re.compile(r'\b(?:%s)(?:s|es)?\b' % stems, re.I)
    ruled = re.compile(r'\b(?:no|without|not any)\s+(?:[a-z\-]+\s+){0,2}(?:%s)(?:s|es)?\b' % stems, re.I)
    parts = re.split(r'(?<=[,.;])\s*', description)
    kept = [part for part in parts if not named.search(part) or ruled.search(part)]
    left = ' '.join(kept).strip().rstrip(',;')
    if not left:
        return description
    return left + '.' if description.rstrip().endswith('.') and not left.endswith('.') else left


def negative(names):
    """The planner's prompt for what the music should not sound like."""
    return '# Caption\n%s\n\n# Lyric\n[Instrumental]\n' % ', '.join(names)
