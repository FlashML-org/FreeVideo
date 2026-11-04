"""Subtitle-style timed lines in a description, turned into MiniMax H3's timed prompt notation.

`0:02 Lin: You are late.` becomes `At 00:02.000, Lin (S1) begins saying: <d>[English] You are late.</d>`,
`0:07 cut: A corridor.` starts `[Shot 2] At 00:07.000, A corridor.` A description without timed lines, or one
already written in the H3 notation, is returned unchanged. web/timed_prompt.js is the same function; both
are checked against tests/fixtures/timed_prompt_cases.json.
"""
import re

# The same space set as ECMAScript's \s and trim(), written out so Python and JavaScript read a line alike.
SP = '\t\n\v\f\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
SPACES = '\t\n\v\f\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
TIME = re.compile(r'^[ \t　]*([0-9]{1,3}):([0-5][0-9])(?:\.([0-9]{1,3}))?(?![0-9.:])[ \t　]*(.*)$')
LOOSE = re.compile(r'^[ \t　]*[0-9]{1,3}:[0-9]{1,2}(?![0-9:])')
CUT = re.compile(r'^(?:cut|CUT|Cut|切)[ \t　]*[:：][ \t　]*(.*)$')
CUT_BARE = re.compile(r'^(?:cut|CUT|Cut|切)$')
SPEECH = re.compile(r'^@?([^:：' + SP + r'][^:：]{0,11}?)[ \t　]*[:：][ \t　]*([^' + SP + r'].*)$')
NAME_BREAK = re.compile(r'[,.;!?，。；！？]')
WRITTEN = re.compile(r'\[Shot [0-9]+\]|\bAt [0-9]{2,}:[0-9]{2}\.[0-9]{3}', re.ASCII)
CJK = re.compile('[぀-ヿ㐀-鿿]')
ENDS = '.!?。！？…"”'


def language(words):
    if re.search('[぀-ヿ]', words):
        return 'Japanese'
    if re.search('[가-힯]', words):
        return 'Korean'
    if re.search('[一-鿿]', words):
        return 'Chinese'
    if re.search('[Ѐ-ӿ]', words):
        return 'Russian'
    return 'English'


def clock(ms):
    return '%02d:%02d.%03d' % (ms // 60000, ms // 1000 % 60, ms % 1000)


def sentence(words):
    words = words.strip(SPACES)
    if not words or words[-1] in ENDS:
        return words
    return words + ('。' if CJK.search(words) else '.')


def parse(text):
    """(head lines, timed lines, errors); a timed line is {n, ms, kind, who, words}."""
    head, lines, errors = [], [], []
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


def convert(text, seconds, speakers=None, offset=0):
    """{text, errors, timed}: errors name the lines left out ({line, code}); the caller stops on any.
    Times are read on a ruler that starts `offset` seconds before this text's first second."""
    if not isinstance(text, str) or WRITTEN.search(text):
        return {'text': text, 'errors': [], 'timed': False}
    head, lines, errors = parse(text)
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
