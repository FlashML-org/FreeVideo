"""Subtitle-style timed lines in a description, turned into MiniMax H3's timed prompt notation.

`0:02 Lin: You are late.` becomes `At 00:02.000, Lin (S1) begins saying: <d>[English] You are late.</d>`,
`0:07 cut: A corridor.` starts `[Shot 2] At 00:07.000, A corridor.` A description without timed lines, or one
already written in the H3 notation, is returned unchanged. web/timed_prompt.js is the same function; both
are checked against tests/fixtures/timed_prompt_cases.json.
"""
import re
from functools import lru_cache

from .i18n import keyword_list, keyword_words

# The same space set as ECMAScript's \s and trim(), written out so Python and JavaScript read a line alike.
SP = '\t\n\v\f\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
SPACES = '\t\n\v\f\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
TIME = re.compile(r'^[ \t　]*([0-9]{1,3}):([0-5][0-9])(?:\.([0-9]{1,3}))?(?![0-9.:])[ \t　]*(.*)$')
LOOSE = re.compile(r'^[ \t　]*[0-9]{1,3}:[0-9]{1,2}(?![0-9:])')
SPEECH = re.compile(r'^@?([^:：' + SP + r'][^:：]{0,11}?)[ \t　]*[:：][ \t　]*([^' + SP + r'].*)$')
NAME_BREAK = re.compile(r'[,.;!?，。；！？]')
WRITTEN = re.compile(r'\[Shot [0-9]+\]|\bAt [0-9]{2,}:[0-9]{2}\.[0-9]{3}', re.ASCII)
CJK = re.compile('[぀-ヿ㐀-鿿]')
ENDS = '.!?。！？…"”'


# Cyrillic lines: Ukrainian or Russian, decided by weighing the words (tests/test_prompt_vlm_dialogue.py lists cases).
# Ukrainian words: those with і ї є ґ, and common words only Ukrainian writes (що, цей, вже, дуже ...). Russian words:
# those with ы э ъ ё, and common Russian words (и, что, это, как, он, нет ...). The line is Ukrainian only when it has
# more Ukrainian words; a tie is Russian. A name inside a sentence (a capitalised word after no sentence end, colon or
# semicolon: "Завтра я еду в Київ") and a Roman numeral (Пётр І, Екатерина ІІ) count for neither side; a line written
# in capitals is read in lower case first, so its words are not names. Belarusian ў, Kazakh ә ғ қ ң ө ұ ү һ and the
# Serbian and Macedonian ђ ѓ ѕ ј љ њ ћ ќ џ keep the Russian tag, the closest of the two.
# Known limits: Ukrainian with neither kind of word (Я тебе кохаю, Я чекаю на тебе) reads as Russian, and Belarusian or
# Kazakh with і but none of their own letters (Мен білемін) as Ukrainian.
UKRAINIAN_LETTERS = re.compile('[\u0404\u0406\u0407\u0454\u0456\u0457\u0490\u0491]')
RUSSIAN_LETTERS = re.compile('[\u042b\u044b\u042d\u044d\u042a\u044a\u0401\u0451]')
OTHER_CYRILLIC = re.compile('[\u040e\u045e\u0492\u0493\u049a\u049b\u04a2\u04a3\u04ae\u04af\u04b0\u04b1\u04ba\u04bb\u04d8\u04d9'
                            '\u04e8\u04e9\u0402\u0403\u0405\u0408-\u040c\u040f\u0452\u0453\u0455\u0458-\u045c\u045f]')
UKRAINIAN_WORDS = frozenset('що щоб цей цього ця цю ці вже дуже щось немає тобі зараз дякую'.split())
RUSSIAN_WORDS = frozenset('и что чтобы это этот эта как он она они его меня тебя где когда если нет есть но или только '
                          'еще мне с к'.split())
CYRILLIC_WORD = re.compile("[\u0400-\u04ff](?:['\u2019\u02bc]?[\u0400-\u04ff])*")
# What may stand between the end of a sentence (or a colon) and the next word.
OPENING = ' \t\n\r"\'\u00ab\u00bb\u201c\u201d\u201e\u2018\u2019()[]\u2014\u2013-'
ROMAN = frozenset('\u0406\u0425')


def ukrainian(words):
    if OTHER_CYRILLIC.search(words):
        return False
    if not re.search('[\u0430-\u045f\u0491]', words):
        words = words.lower()
    ukrainian_words = russian_words = 0
    for match in CYRILLIC_WORD.finditer(words):
        word = match.group(0)
        lower = word.lower()
        if RUSSIAN_LETTERS.search(word) or lower in RUSSIAN_WORDS:
            russian_words += 1
        elif lower in UKRAINIAN_WORDS:
            ukrainian_words += 1
        elif UKRAINIAN_LETTERS.search(word):
            before = words[:match.start()].rstrip(OPENING)
            inside = bool(before) and before[-1] not in '.!?:;\u2026'
            if set(word) <= ROMAN and before:
                continue
            if inside and word[0].isupper() and (len(word) == 1 or any(c.islower() for c in word[1:])):
                continue
            ukrainian_words += 1
    return ukrainian_words > russian_words


@lru_cache(maxsize=64)
def _cuts(own):
    """(line, bare) patterns for the built-in cut words ("Cut:", "切：") and a language's own (_meta.keywords.cut)."""
    source = '(?:' + '|'.join(re.escape(word) for word in keyword_words('cut', own)) + ')'
    return re.compile('^' + source + r'[ \t　]*[:：][ \t　]*(.*)$'), re.compile('^' + source + '$')


def language(words):
    if re.search('[぀-ヿ]', words):
        return 'Japanese'
    if re.search('[가-힯]', words):
        return 'Korean'
    if re.search('[一-鿿]', words):
        return 'Chinese'
    if re.search('[Ѐ-ӿ]', words):
        return 'Ukrainian' if ukrainian(words) else 'Russian'
    return 'English'


def clock(ms):
    return '%02d:%02d.%03d' % (ms // 60000, ms // 1000 % 60, ms % 1000)


def sentence(words):
    words = words.strip(SPACES)
    if not words or words[-1] in ENDS:
        return words
    return words + ('。' if CJK.search(words) else '.')


def parse(text, cut_words=()):
    """(head lines, timed lines, errors); a timed line is {n, ms, kind, who, words}. `cut_words`: the language's own
    words for a new shot, besides "Cut" and "切" (language_keywords(tag)['cut'] in freevideo_engine/i18n.py)."""
    head, lines, errors = [], [], []
    CUT, CUT_BARE = _cuts(tuple(keyword_list(cut_words)))
    for n, raw in enumerate(re.split(r'\r\n|\r|\n', text), 1):
        found = TIME.match(raw)
        if not found:
            if LOOSE.match(raw):
                errors.append({'line': n, 'code': 'bad_time'})
            elif raw.strip(SPACES):
                head.append(raw.strip(SPACES))
            continue
        minutes, seconds, fraction, rest = found.groups()
        ms = (int(minutes) * 60 + int(seconds)) * 1000 + int((fraction or '').ljust(3, '0'))
        rest = rest.strip(SPACES)
        row = {'n': n, 'ms': ms, 'kind': 'event', 'who': '', 'words': rest}
        cut, speech = CUT.match(rest), SPEECH.match(rest)
        if cut or CUT_BARE.match(rest):
            row.update(kind='cut', words=(cut.group(1) if cut else '').strip(SPACES))
        elif speech and not NAME_BREAK.search(speech.group(1)):
            row.update(kind='speech', who=speech.group(1).strip(SPACES), words=speech.group(2).strip(SPACES))
        if not row['words']:
            errors.append({'line': n, 'code': 'empty_cut' if row['kind'] == 'cut' else 'bad_time'})
            continue
        lines.append(row)
    return head, lines, errors


def convert(text, seconds, speakers=None, offset=0, cut_words=()):
    """{text, errors, timed}: errors name the lines left out ({line, code}); the caller stops on any.
    Times are read on a ruler that starts `offset` seconds before this text's first second. `cut_words` as in parse."""
    if not isinstance(text, str) or WRITTEN.search(text):
        return {'text': text, 'errors': [], 'timed': False}
    head, lines, errors = parse(text, cut_words)
    if not lines and not errors:
        return {'text': text, 'errors': [], 'timed': False}
    limit = round(seconds * 1000)
    kept = []
    shift = round(offset * 1000)
    for row in lines:
        row['ms'] -= shift
        if row['ms'] < 0 or row['ms'] >= limit:
            errors.append({'line': row['n'], 'code': 'out_of_range'})
        else:
            kept.append(row)
    errors.sort(key=lambda e: e['line'])
    ids = dict(speakers or {})
    shots = [{'ms': 0, 'open': ' '.join(head), 'parts': []}]
    for row in sorted(kept, key=lambda r: (r['ms'], r['kind'] != 'cut', r['kind'] == 'speech')):
        if row['kind'] == 'cut' and row['ms'] > 0:
            if shots[-1]['ms'] == row['ms']:
                shots[-1]['open'] = (shots[-1]['open'] + ' ' + sentence(row['words'])).strip(SPACES)
            else:
                shots.append({'ms': row['ms'], 'open': sentence(row['words']), 'parts': []})
            continue
        at = 'At %s, ' % clock(row['ms']) if row['ms'] > 0 else ''
        if row['kind'] == 'cut':
            shots[0]['open'] = (shots[0]['open'] + ' ' + sentence(row['words'])).strip(SPACES)
        elif row['kind'] == 'event':
            shots[-1]['parts'].append(at + sentence(row['words']))
        else:
            if row['who'] not in ids:
                ids[row['who']] = max(ids.values(), default=0) + 1
            shots[-1]['parts'].append('%s%s (S%d) begins saying: <d>[%s] %s</d>'
                                      % (at, row['who'], ids[row['who']], language(row['words']), row['words']))
    out = []
    for index, shot in enumerate(shots, 1):
        label = '[Shot 1]' if index == 1 else '[Shot %d] At %s,' % (index, clock(shot['ms']))
        out.append(' '.join(part for part in (label, shot['open'], *shot['parts']) if part))
    return {'text': ' '.join(out), 'errors': errors, 'timed': True}
