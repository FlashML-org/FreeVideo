"""MiniMax H3 rewrite contract, derived from the official writing guides.

https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing
The public guide describes a prompt format, not a released Context-IR model.
"""
import math
import re

BASE_FIELDS = ('integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music')
REF_FIELDS = ('subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
              'overall_soundscape', 'non_diegetic_music')


def request(value):
    if not isinstance(value, dict):
        raise ValueError('invalid_request')
    prompt, seconds, media = value.get('text'), value.get('seconds'), value.get('media', [])
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
        raise ValueError('invalid_prompt')
    if type(seconds) not in (float, int) or not math.isfinite(seconds) or not 1 <= seconds <= 60:
        raise ValueError('invalid_duration')
    if not isinstance(media, list) or len(media) > 8:
        raise ValueError('too_many_images')
    roles = []
    for row in media:
        if (not isinstance(row, dict) or set(row) != {'file', 'role'} or
                not isinstance(row['file'], str) or row['role'] not in ('first', 'last', 'reference')):
            raise ValueError('unsupported_media')
        roles.append(row['role'])
    if 'reference' in roles and any(r in roles for r in ('first', 'last')):
        raise ValueError('mixed_media')
    if roles.count('first') > 1 or roles.count('last') > 1:
        raise ValueError('mixed_media')
    mode = ('ref2va' if 'reference' in roles else 'fl2va' if 'first' in roles and 'last' in roles
            else 'i2va' if 'first' in roles else 'l2va' if 'last' in roles else 't2va')
    # Anchor numbering must match the encoder even when the UI added last first.
    if mode != 'ref2va':
        media = sorted(media, key=lambda r: r['role'] != 'first')
    return dict(text=prompt, seconds=float(seconds), media=media, mode=mode)


def instruction(value):
    rule = """Rewrite the user scene as an English MiniMax H3 video prompt. Follow the requested
actions and camera movement exactly. Image references describe appearance, not whether an
object moves. Keep dialogue, lyrics and visible writing verbatim in their original language.
Add concrete motion, framing, lighting and sounds without changing the intended scene.
Do not invent dialogue, captions or music. Return only the sections listed below, each starting
on its own line with its heading and a colon, separated by one blank line. No preface,
commentary, markdown fences or JSON.
Use [Shot 1] for the opening; if the user wants cuts, add [Shot 2] At MM:SS.mmm, etc.
Keep unbroken shots unbroken.
Spoken words never go in quotation marks (quotes mean text visible on screen). Write each spoken
line as a speaker ID, then the exact words with their punctuation inside <d>[Language] ...</d>,
untranslated; each speaker keeps one ID and different speakers get different IDs, e.g.
The young woman (S1) says softly: <d>[Chinese] 再见了。</d> The man (S2) replies: <d>[English] Wait.</d>
Dialogue belongs only in the first section, not in the sound sections.
"""
    if value['mode'] == 'ref2va':
        rule += """subject_definitions: Name each visible subject literally <Subject 1>, <Subject 2>, etc.,
with its appearance and source <Picture N>. Preserve the supplied picture numbering.
summary: [reference generation] State what happens in the requested video.
retention_analysis: Mark each subject fully_preserved, partially_preserved, attribute_transfer,
or weak_reference. This describes retained visual attributes, not motion restrictions.
detailed_description: [Shot 1] Describe the target video, using <Subject N> identifiers.
"""
    else:
        rule += 'integrated_multimodal_description: [Shot 1] Describe the target video.\n'
        for i, row in enumerate(value['media'], 1):
            timestamp = '0.00' if row['role'] == 'first' else f'{value["seconds"]:.2f}'
            rule += (f'Anchor <Picture {i}> to the {row["role"]} frame at {timestamp} seconds; '
                     'keep its pictured layout at that anchor.\n')
    rule += """overall_soundscape: Environmental sounds, or N/A.
non_diegetic_music: Music only if requested, otherwise N/A.
Use only supplied media labels; do not invent audio or video references.
"""
    fields = REF_FIELDS if value['mode'] == 'ref2va' else BASE_FIELDS
    return (rule + f'Cover {value["seconds"]:.3f} seconds. All sections are required, in this order: '
            + ', '.join(fields) + f'. Begin with "{fields[0]}:".')


def sections(text, fields):
    """Each expected heading on its own paragraph, as H3 expects.

    The model sometimes runs every section into one line ("... overall_soundscape: N/A
    non_diegetic_music: N/A"), mostly when pictures are attached; only the expected
    heading names are split out, so prose is never cut.
    """
    names = '|'.join(fields)
    text = re.sub(r'(?m)^(?:#{1,3} +)?\*{0,2}(%s)\*{0,2}:\*{0,2}' % names, r'\1:', text)
    text = re.sub(r'(?<=\S)[ \t]+(%s):' % '|'.join(fields[1:]), r'\n\n\1:', text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    return re.sub(r'\n+(?=(?:%s):)' % names, '\n\n', text).strip()


def validate_output(text, value):
    if not isinstance(text, str) or not 100 <= len(text.strip()) <= 20000:
        raise ValueError('invalid_output')
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = '\n'.join(text.splitlines()[1:-1]).strip()
    fields = REF_FIELDS if value['mode'] == 'ref2va' else BASE_FIELDS
    text = sections(text, fields)
    found = re.findall(r'(?m)^([a-z_]+):', text)
    if found != list(fields) or not text.startswith(fields[0] + ':'):
        raise ValueError('invalid_output')
    if not re.search(r'\[Shot \d+\]', text):
        name = 'detailed_description' if value['mode'] == 'ref2va' else fields[0]
        text = text.replace(name + ':', name + ': [Shot 1]', 1)
    if '[Shot 1]' not in text:
        raise ValueError('invalid_output')
    for marker in fields:
        section = text.split(marker + ':', 1)[1].split('\n\n', 1)[0].strip()
        if not section:
            raise ValueError('invalid_output')
    for minutes, seconds in re.findall(r'\bAt (\d{2}):(\d{2}\.\d{3})', text):
        if int(minutes) * 60 + float(seconds) >= value['seconds']:
            raise ValueError('invalid_output')
    for index in re.findall(r'<Picture (\d+)>', text):
        if not 1 <= int(index) <= len(value['media']):
            raise ValueError('invalid_output')
    if re.search(r'<(?:Audio|Video) \d+>', text):
        raise ValueError('invalid_output')
    for i in range(1, len(value['media']) + 1):
        if f'<Picture {i}>' not in text:
            raise ValueError('invalid_output')
    return dialogue(text, value['text'])


# Spoken lines -------------------------------------------------------------------------------------------
# H3 reads double-quoted text as writing visible on screen; speech must be (S1) <d>[Language] ...</d>.
QUOTES = {'\u201c': '\u201d', '\u300c': '\u300d', '\u300e': '\u300f', '"': '"', '\uff02': '\uff02'}
QUOTED = re.compile('|'.join('%s([^%s\n]{1,400})%s' % (re.escape(a), re.escape(b), re.escape(b))
                             for a, b in QUOTES.items()))
# Verbs that end the words right before a spoken quote. Bare 叫/道/喝 also mean "named",
# "road" or "drink", so only their speech compounds count; the strong verbs may be followed
# by a few characters (说了一句：).
CJK_SPEECH = ('说道', '喊道', '叫道', '问道', '答道', '笑道', '叹道', '骂道', '吼道', '念道', '喝道', '唤道', '回道',
              '大叫', '尖叫', '叫喊', '吆喝', '呼喊', '惊呼', '高呼', '呼唤', '念叨', '嘀咕', '嘟囔', '低语', '耳语',
              '回应', '回复', '告诉', '宣布', '感叹', '自语', '叮嘱', '嘱咐', '曰',
              '言う', '言っ', '叫ぶ', '叫ん', '囁', 'つぶや', '呟', '答え', '尋ね', '歌う', '話す',
              '말하', '외치', '묻', '대답', '속삭')
CJK_STRONG = ('说', '喊', '问', '答', '吼', '嚷', '唱', '讲', '骂')
EN_SPEECH = re.compile(r"\b(?:says?|said|saying|asks?|asked|asking|repl(?:y|ies|ied|ying)|answers?|answered|"
                       r"shouts?|shouted|shouting|yells?|yelled|yelling|whispers?|whispered|whispering|"
                       r"calls? out|called out|cries|cried|crying out|screams?|screamed|mutters?|"
                       r"muttered|murmurs?|murmured|adds?|added|exclaims?|exclaimed|sings?|sang|singing|"
                       r"tells?|told|announces?|announced|begs?|begged|pleads?|pleaded|laughs?|sighs?|sighed|"
                       r"speaks?|spoke|speaking|goes)\b[^\n.!?]{0,24}$", re.I)
NOUNS = (r'写着|写有|写的|印着|刻着|标着|显示|字样|字幕|招牌|牌子|标语|横幅|海报|屏幕|标题|文字|书名|名叫|叫做|叫作|'
         r'名为|名字|称为|号称|看板|書かれ|表示|간판|\bsigns?|\bbanners?|\bcaptions?|\btitles?|\bposters?|'
         r'\bscreens?|\blabels?|\bboards?|\bneon|\btext|\bplacards?|\bgraffiti|\bheadlines?|\bsubtitles?|'
         r'\blettering|\bnamed|\bcalled|\btitled|\bnicknamed|\bbrand\w*|\breads?|\breading|\bwritten|'
         r'\bprinted|\bpainted|\bspelled')
# Writing, not speech: a sign "OPEN", 写着“老街馄饨”, a sign that says "OPEN", a shop called "Kiko".
WRITING = re.compile(NOUNS, re.I)
SIGN_SAYS = re.compile(r'(?:%s)\w*\s+(?:that\s+|which\s+)?(?:says|said|reads|shows|displays)$' % NOUNS, re.I)


def language(words):
    if re.search('[\u3040-\u30ff]', words):
        return 'Japanese'
    if re.search('[\uac00-\ud7af]', words):
        return 'Korean'
    if re.search('[\u4e00-\u9fff]', words):
        return 'Chinese'
    if re.search('[\u0400-\u04ff]', words):
        return 'Russian'
    return 'English'


def plain(words):
    """Comparable form: no tags, speaker IDs, quotes, punctuation, spacing or case."""
    words = re.sub(r'<[^>]{1,24}>|\(S\d+(?:,S\d+)*\)|\[[A-Za-z]+\]', '', words)
    return re.sub(r'[\W_]+', '', words).lower()


def spoken_lines(scene):
    """Quoted spans the user introduced as speech (not signs or captions), in order."""
    lines = []
    for match in QUOTED.finditer(scene):
        words = next(group for group in match.groups() if group is not None).strip()
        before = scene[max(0, match.start() - 40):match.start()]
        after = scene[match.end():match.end() + 12]
        if not plain(words):
            continue
        lead = before.rstrip(' :：,，\u3000')
        if SIGN_SAYS.search(lead):
            continue
        writing = max((m.end() for m in WRITING.finditer(lead)), default=-1)
        cues = [len(lead) for verb in CJK_SPEECH if lead.endswith(verb)]
        cues += [lead.rfind(verb) + len(verb) for verb in CJK_STRONG if verb in lead[-6:]]
        cues += [m.start() + 1 for m in EN_SPEECH.finditer(lead)]
        if cues:
            # Whichever cue is nearer the quote decides: 招牌亮着，老板说：“…” is speech.
            speech = max(cues) > writing
        else:
            speech = (writing < 0 or writing < len(lead) - 8) and bool(
                re.match(r'[\s,，。.!?！？]*(?:と|って)?\s*(?:[^\s,，。.!?！？]{0,6}?)(?:%s)'
                         % '|'.join(CJK_SPEECH + CJK_STRONG), after)
                or re.match(r'[\s,]*(?:\w+\s+){0,3}(?:says|said|asks|asked|replies|replied|shouts|shouted|'
                            r'whispers|whispered|calls|called|cries|cried)\b', after, re.I))
        if speech:
            lines.append(words)
    return lines


def dialogue(text, scene):
    """Turn the user's spoken lines that came back quoted into H3 dialogue, verbatim.

    Matching is by words only, so a changed full stop or added <en> tag still matches;
    the user's own words and punctuation are restored. On-screen text stays quoted.
    """
    lines = spoken_lines(scene) if isinstance(scene, str) else []
    by_words = {plain(line): line for line in lines}
    single = len(set(by_words)) == 1
    def tag(match):
        body = match.group(1).strip()
        return '<d>[%s] %s</d>' % (language(body), body)
    # <d>speech</d> without a language, or <en>speech</en>-style tags.
    text = re.sub(r'<d>(?!\s*\[)\s*([^<\n]{1,400}?)\s*</d>', tag, text)
    text = re.sub(r'<(?:en|zh|ja|ko|cn)>\s*([^<\n]{1,400}?)\s*</(?:en|zh|ja|ko|cn)>', tag, text)
    if not by_words:
        return text
    # Speech belongs in the description; the sound sections (last in every mode) only summarise.
    found = re.search(r'(?m)^overall_soundscape:', text)
    body_end = found.start() if found else len(text)
    def match_line(words):
        key = plain(words)
        if key in by_words:
            return by_words[key]
        for other, line in by_words.items():
            if len(key) >= 2 and (key in other or other in key) and min(len(key), len(other)) >= .8 * max(
                    len(key), len(other)):
                return line
        return None
    pieces, position = [], 0
    for match in QUOTED.finditer(text):
        words = next(group for group in match.groups() if group is not None)
        line = match_line(words)
        if line is None:
            continue
        pieces.append(text[position:match.start()])
        if match.start() >= body_end:
            # Sound sections only summarise; keep the words without on-screen quotes.
            pieces.append(line)
        else:
            lead = ''.join(pieces)[-24:]
            speaker = '' if re.search(r'\(S\d+(?:,S\d+)*\)[^()<]{0,16}$', lead) else '(S1) ' if single else ''
            replacement = '%s<d>[%s] %s</d>' % (speaker, language(line), line)
            gloss = re.match(r'\s*\([A-Za-z][^()\n]{0,80}\)', text[match.end():])
            if gloss and language(line) != 'English':
                # A translation gloss after the line would be read as more speech.
                pieces.append(replacement)
                position = match.end() + gloss.end()
                continue
            pieces.append(replacement)
        position = match.end()
    pieces.append(text[position:])
    text = ''.join(pieces)
    if len(by_words) == 1 and not re.search(r'\(S\d+', text):
        # One speaker in the scene: give the line its stable ID.
        text = re.sub(r'(?<!\) )<d>\[', '(S1) <d>[', text)
    missing = missing_lines(text, scene)
    if missing:
        # Last resort after the worker's retry: never drop the user's dialogue silently.
        ids = [int(n) for n in re.findall(r'\(S(\d+)', text)]
        speaker = 'S%d' % (max(ids) + 1 if ids else 1)
        said = ' '.join('The speaker (%s) says: <d>[%s] %s</d>' % (speaker, language(line), line) for line in missing)
        found = re.search(r'(?m)^overall_soundscape:', text)
        end = found.start() if found else len(text)
        text = text[:end].rstrip() + ' ' + said + ('\n\n' + text[end:] if end < len(text) else '')
    return text


def missing_lines(text, scene):
    """The user's spoken lines that the rewrite does not contain at all."""
    have = plain(text)
    return [line for line in dict.fromkeys(spoken_lines(scene)) if plain(line) not in have]


DIALOGUE_RETRY = ('Your rewrite left out these spoken lines. Rewrite the whole prompt again in the same format and '
                  'include each line once, verbatim, at the moment it is spoken, as (S1) <d>[Language] ...</d>:\n')


def user_text(value):
    """The user's scene, plus the spoken lines the rewrite must keep verbatim."""
    text = f'User scene to rewrite (preserve every requested action):\n{value["text"]}'
    lines = spoken_lines(value['text'])
    if lines:
        text += '\n\nSpoken lines to keep verbatim inside <d>[Language] ...</d>:\n' + '\n'.join(
            '- [%s] %s' % (language(line), line) for line in lines)
    return text
