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
        # kind and number say which <Picture N> / <Video N> label the scene uses for the row; connected
        # nodes and audio are left out of the list, so the numbers can skip.
        if (not isinstance(row, dict) or not {'file', 'role'} <= set(row) <= {'file', 'role', 'kind', 'number'} or
                not isinstance(row['file'], str) or row['role'] not in ('first', 'last', 'reference') or
                row.get('kind', 'image') not in ('image', 'video') or
                (row.get('kind') == 'video' and row['role'] != 'reference') or
                (type(row.get('number', 1)) is not int or not 1 <= row.get('number', 1) <= 99)):
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
    if len({'number' in row for row in media}) > 1:
        raise ValueError('unsupported_media')
    counters, rows = {}, []
    for row in media:
        kind = row.get('kind', 'image')
        counters[kind] = counters.get(kind, 0) + 1
        rows.append(dict(file=row['file'], role=row['role'], kind=kind, number=row.get('number', counters[kind])))
    media = rows
    if len({label(row) for row in media}) != len(media):
        raise ValueError('unsupported_media')
    return dict(text=prompt, seconds=float(seconds), media=media, mode=mode)


LABEL = re.compile(r'<(Picture|Video|Audio) (\d+)>')


def label(row):
    return '<%s %d>' % ('Video' if row['kind'] == 'video' else 'Picture', row['number'])


def unseen_labels(value):
    """Labels the scene uses for media the model cannot look at (audio, connected nodes)."""
    shown = {label(row) for row in value['media']}
    found = []
    for match in LABEL.finditer(value['text']):
        if match.group(0) not in shown and match.group(0) not in found:
            found.append(match.group(0))
    return found


def required_labels(value):
    return [label(row) for row in value['media']] + unseen_labels(value)


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
with its appearance and source <Picture N> or <Video N>. Preserve the supplied numbering.
A video is shown as one still frame.
summary: [reference generation] State what happens in the requested video.
retention_analysis: Mark each subject fully_preserved, partially_preserved, attribute_transfer,
or weak_reference. This describes retained visual attributes, not motion restrictions.
detailed_description: [Shot 1] Describe the target video, using <Subject N> identifiers.
"""
    else:
        rule += 'integrated_multimodal_description: [Shot 1] Describe the target video.\n'
        for row in value['media']:
            timestamp = '0.00' if row['role'] == 'first' else f'{value["seconds"]:.2f}'
            rule += (f'Anchor {label(row)} to the {row["role"]} frame at {timestamp} seconds; '
                     'keep its pictured layout at that anchor.\n')
    rule += """overall_soundscape: Environmental sounds, or N/A.
non_diegetic_music: Music only if requested, otherwise N/A.
Use only supplied media labels; do not invent audio or video references.
"""
    unseen = unseen_labels(value)
    if unseen:
        rule += ('The scene also uses ' + ', '.join(unseen) + ', which you cannot see. '
                 'Keep each of those labels exactly as written, where the scene uses it.\n')
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


class InvalidOutput(ValueError):
    """Stable rejection metadata; never retain the prompt or rejected draft."""
    def __init__(self, condition, text, value):
        super().__init__('invalid_output')
        draft = text if isinstance(text, str) else ''
        required = required_labels(value)
        pictures = [int(each[len('<Picture '):-1]) for each in required if each.startswith('<Picture ')]
        indices = {int(i) for i in re.findall(r'<Picture (\d+)>', draft)}
        self.details = dict(condition=condition, output_characters=len(draft),
                            missing_pictures=sorted(i for i in pictures if f'<Picture {i}>' not in draft),
                            missing_references=[each for each in required if each not in draft],
                            out_of_range_pictures=sorted(i for i in indices if i not in pictures),
                            retried=False)


def validate_output(text, value):
    if not isinstance(text, str) or not 100 <= len(text.strip()) <= 20000:
        raise InvalidOutput('type_or_length', text, value)
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = '\n'.join(text.splitlines()[1:-1]).strip()
    fields = REF_FIELDS if value['mode'] == 'ref2va' else BASE_FIELDS
    text = sections(text, fields)
    found = re.findall(r'(?m)^([a-z_]+):', text)
    if found != list(fields) or not text.startswith(fields[0] + ':'):
        raise InvalidOutput('headings', text, value)
    if not re.search(r'\[Shot \d+\]', text):
        name = 'detailed_description' if value['mode'] == 'ref2va' else fields[0]
        text = text.replace(name + ':', name + ': [Shot 1]', 1)
    if '[Shot 1]' not in text:
        raise InvalidOutput('missing_shot_1', text, value)
    for marker in fields:
        section = text.split(marker + ':', 1)[1].split('\n\n', 1)[0].strip()
        if not section:
            raise InvalidOutput('empty_section', text, value)
    for minutes, seconds in re.findall(r'\bAt (\d{2}):(\d{2}\.\d{3})', text):
        if int(minutes) * 60 + float(seconds) >= value['seconds']:
            raise InvalidOutput('timestamp_not_before_duration', text, value)
    # Only the labels the scene itself uses: the media given, plus audio and connected media it cannot see.
    required = required_labels(value)
    for found in LABEL.finditer(text):
        if found.group(0) not in required:
            raise InvalidOutput('picture_index_out_of_range' if found.group(1) == 'Picture'
                                else 'unsupported_audio_video_reference', text, value)
    for each in required:
        if each not in text:
            raise InvalidOutput('missing_picture_reference', text, value)
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


def split_source(text):
    """Description, sound sections and spoken lines, using only the prompt's text."""
    sound = re.search(r'(?m)^[ \t]*overall_soundscape[ \t]*:', text)
    scene, soundscape = (text[:sound.start()], text[sound.start():]) if sound else (text, '')
    tagged = re.findall(r'<(?:d|en|zh|ja|ko|cn)>\s*(.*?)\s*</(?:d|en|zh|ja|ko|cn)>', scene, re.S)
    lines = [line for line in tagged if plain(line)] + spoken_lines(scene)
    return scene, soundscape, list(dict.fromkeys(lines))


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


ASK_SYSTEM = """You are a precise visual assistant for video production. The user supplies images
labelled <Image 1>, <Image 2>, etc. in the order given, then a question about them. Answer the
question directly and concretely from what is actually visible: subjects, faces, clothing,
colours, setting, framing, camera angle, lighting, motion cues and any visible text. Say plainly
when something is not visible or cannot be determined from the images; never invent details.
Answer in the language of the question unless it asks for another language."""
ASK_JSON = ('Return only one valid JSON value that answers the question. No markdown fences, '
            'no commentary before or after it.')
ASK_JSON_RETRY = ('Your previous reply was not valid JSON. Return only the corrected JSON value, '
                  'with no markdown fences or commentary.')


def ask_request(value):
    """Questions about images for agents; images are ComfyUI input files."""
    if not isinstance(value, dict) or set(value) - {'questions', 'format', 'max_tokens', 'system', 'mode'}:
        raise ValueError('invalid_request')
    if value.get('mode', 'ask') != 'ask':
        raise ValueError('invalid_request')
    questions = value.get('questions')
    if not isinstance(questions, list) or not 1 <= len(questions) <= 8:
        raise ValueError('invalid_request')
    answer_format = value.get('format', 'text')
    if answer_format not in ('text', 'json'):
        raise ValueError('invalid_request')
    max_tokens = value.get('max_tokens', 600)
    if type(max_tokens) is not int or not 16 <= max_tokens <= 1500:
        raise ValueError('invalid_request')
    system = value.get('system')
    if system is not None and (not isinstance(system, str) or not system.strip() or len(system) > 4000):
        raise ValueError('invalid_prompt')
    rows, total = [], 0
    for question in questions:
        if not isinstance(question, dict) or set(question) - {'text', 'images'}:
            raise ValueError('invalid_request')
        text, images = question.get('text'), question.get('images', [])
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise ValueError('invalid_prompt')
        if not isinstance(images, list) or len(images) > 16:
            raise ValueError('too_many_images')
        selected = []
        for image in images:
            if (not isinstance(image, dict) or set(image) - {'file', 'label'}
                    or not isinstance(image.get('file'), str) or not image['file']):
                raise ValueError('unsupported_media')
            label = image.get('label')
            if label is not None and (not isinstance(label, str) or len(label) > 80):
                raise ValueError('invalid_request')
            # One line: a label cannot add instructions after its image.
            label = ' '.join(label.split()) if label else ''
            selected.append(dict(file=image['file'], **({'label': label} if label else {})))
        total += len(selected)
        rows.append(dict(text=text, images=selected))
    if total > 24:
        raise ValueError('too_many_images')
    return dict(mode='ask', questions=rows, format=answer_format, max_tokens=max_tokens,
                system=system.strip() if system else None)


def ask_instruction(value):
    system = value.get('system') or ASK_SYSTEM
    return system + ('\n' + ASK_JSON if value['format'] == 'json' else '')


def image_tokens(count):
    """Vision tokens per image, so up to 16 frames fit the input limit."""
    return max(64, min(512, 4800 // max(1, count)))


def parse_json(text):
    """The JSON value in a reply, tolerating fences or a short preface."""
    if not isinstance(text, str):
        raise ValueError('invalid_json')
    text = text.strip()
    if text.startswith('```'):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith('```'):
            lines = lines[:-1]
        text = '\n'.join(lines).strip()
    import json
    try:
        return text, json.loads(text)
    except ValueError:
        pass
    starts = [index for index in (text.find('{'), text.find('[')) if index >= 0]
    if starts:
        start = min(starts)
        end = text.rfind('}' if text[start] == '{' else ']')
        if end > start:
            try:
                return text[start:end + 1], json.loads(text[start:end + 1])
            except ValueError:
                pass
    raise ValueError('invalid_json')


ANSWER_KEYS = {'text', 'json', 'tokens', 'seconds', 'error', 'truncated', 'retried'}


def validate_answers(answers, value):
    """Worker output shape for an ask job: one answer per question, in order."""
    if not isinstance(answers, list) or len(answers) != len(value['questions']):
        raise ValueError('invalid_output')
    for answer in answers:
        if not isinstance(answer, dict) or set(answer) - ANSWER_KEYS or not isinstance(answer.get('text', ''), str):
            raise ValueError('invalid_output')
        if 'error' not in answer and not answer.get('text', '').strip():
            raise ValueError('invalid_output')
        if 'error' in answer and answer['error'] not in ('invalid_json', 'context_length'):
            raise ValueError('invalid_output')
    return answers


# Long videos: one description per part --------------------------------------------------------------------
# Every part of a long video is generated from its own text. Handing each part the whole description makes
# the model play the story from the start again in every part: the opening pause, the first line and the
# first action come back after each seam. Each part instead gets the shared look and sound plus only what
# happens during its own seconds.
#
# The small local model only sorts the events: spoken lines become placeholders it places, and the shared
# description, the sound sections and the words themselves are put back by code, so they cannot drift.
SPLIT_MAX_PARTS = 8
# Asking for no pause, or for everything in progress to keep going, makes the model keep talking past the
# seam with invented words; the plain sentence keeps the picture going without that.
CONTINUE = 'Continuing from the previous moment.'
SPEAKER = re.compile(r'(?:\(S\d+(?:,S\d+)*\)\s*)?<d>(\[[A-Za-z]+\])?\s*([^<\n]{1,400}?)\s*</d>')


def split_request(value):
    if not isinstance(value, dict) or set(value) - {'text', 'seconds', 'mode', 'continued'}:
        raise ValueError('invalid_request')
    text, seconds = value.get('text'), value.get('seconds')
    if type(value.get('continued', False)) is not bool:
        raise ValueError('invalid_request')
    if not isinstance(text, str) or not text.strip() or len(text) > 12000:
        raise ValueError('invalid_prompt')
    if (not isinstance(seconds, list) or not 2 <= len(seconds) <= SPLIT_MAX_PARTS or
            any(type(s) not in (float, int) or not math.isfinite(s) or not 1 <= s <= 60 for s in seconds)):
        raise ValueError('invalid_duration')
    return dict(mode='split', text=text, seconds=[float(s) for s in seconds],
                continued=value.get('continued', False))


def speech_seconds(words):
    """Rough speaking time: about 2.6 English words or 4.5 CJK characters a second."""
    cjk = len(re.findall(r'[぀-ヿ㐀-鿿가-힯]', words))
    return round(cjk / 4.5 + len(re.findall(r'[A-Za-z0-9À-ɏ]+', words)) / 2.6, 1)


def part_source(text):
    """(body, sound sections, lines): the description with each spoken line as [L1], [L2], ...

    H3 prompts keep their sound sections apart; they describe the whole video and are repeated as written.
    """
    body, sound = text.strip(), ''
    found = re.search(r'(?m)^overall_soundscape:', body)
    if body.startswith('integrated_multimodal_description:') and found:
        body, sound = body[:found.start()].strip(), body[found.start():].strip()
        body = body[len('integrated_multimodal_description:'):].strip()
        body = re.sub(r'^\[Shot 1\]\s*', '', body)
    lines = []
    def tagged(match):
        lines.append(match.group(0))
        return '[L%d]' % len(lines)
    body = SPEAKER.sub(tagged, body)
    spoken = set(spoken_lines(body))
    def quoted(match):
        words = next(group for group in match.groups() if group is not None).strip()
        if words not in spoken:
            return match.group(0)
        lines.append(match.group(0))
        return '[L%d]' % len(lines)
    if spoken:
        body = QUOTED.sub(quoted, body)
    numbers = [int(n) for n in re.findall(r'\[L(\d+)\]', body)]
    if numbers != list(range(1, len(lines) + 1)):
        raise ValueError('invalid_prompt')
    return body, sound, lines


def split_instruction(value):
    count = len(value['seconds'])
    rows, start = [], 0.
    for i, length in enumerate(value['seconds'], 1):
        rows.append(f'Part {i}: {start:.0f}-{start + length:.0f} s')
        start += length
    opening = 'continues an earlier video' if value.get('continued') else 'is the opening'
    return f"""A long video is made of {count} parts generated one after another; each part continues where the
previous one ended. Sort the user's description into what stays the same and what happens in each part:
{chr(10).join(rows)}
Shared: only what stays the same in every part: who is in the scene and how they look, the place, the
lighting, the camera framing, the style and the background sound. Copy these words from the description.
Leave out every action, movement, event and change, camera moves, anything that only appears later, every
[L] marker and all talking: how someone speaks belongs next to their marker. Shared never retells the story.
Part N: only what happens during that part, in the order of the description, so each action happens
once. Part 1 {opening}; each later part picks up where the previous part ended. When the story is
shorter than the video, the later parts carry it on with a simple new development that follows from it,
such as a next small action or a new view of the scene; they never repeat an earlier action and add no
speech. Every part has something of its own.
The markers [L1], [L2], ... stand for spoken lines; their speaking time is given. Put each marker in
exactly one part, in order, where it is said, as "he says [L2]" or "她说 [L2]"; spread them so each
part's speech fits its seconds. Do not write any other speech: nobody talks, keeps talking or goes
on speaking except at a marker.
Example for a 2-part video. Description: A girl in a yellow raincoat stands on a wooden pier at dawn,
filmed in a wide shot, gulls crying. She waves and shouts [L1], runs to the end of the pier and jumps
into the sea. Speaking time: [L1] 2 s
Shared: A girl in a yellow raincoat on a wooden pier at dawn, filmed in a wide shot, gulls crying.
Part 1: She waves and shouts [L1], then starts running along the pier.
Part 2: She reaches the end of the pier and jumps into the sea.
Write in the language of the description. Answer with exactly these lines and nothing else:
Shared: ...
{chr(10).join(f'Part {i}: ...' for i in range(1, count + 1))}"""


def split_user_text(value):
    body, _, lines = part_source(value['text'])
    text = f'Description of the whole {sum(value["seconds"]):.0f}-second video:\n{body}'
    if lines:
        text += '\n\nSpeaking time: ' + ', '.join('[L%d] %.0f s' % (i, max(1, speech_seconds(line)))
                                                 for i, line in enumerate(lines, 1))
    return text


def split_answer(text, count):
    if not isinstance(text, str):
        raise ValueError('invalid_output')
    text = text.strip().strip('`').strip()
    found = list(re.finditer(r'(?m)^[ \t*#]*(Shared|Part[ \t]+(\d+))[ \t*]*[:：]', text))
    names = [m.group(2) or 'shared' for m in found]
    if names != ['shared'] + [str(i) for i in range(1, count + 1)] or text[:found[0].start()].strip(' \t\n*#'):
        raise ValueError('invalid_output')
    ends = [m.start() for m in found[1:]] + [len(text)]
    values = [' '.join(text[m.end():end].split()).strip('* ') for m, end in zip(found, ends)]
    return values[0], values[1:]


def place_lines(body, lines, seconds):
    """Spoken lines to parts, in order, when the model did not place them.

    A line starts halfway between where an even spread of the speech puts it and where it stands in the
    story between the first and the last line; a line that would run into the next part starts early enough to fit.
    """
    total, lengths = sum(seconds), [max(1., speech_seconds(line)) for line in lines]
    ends = [sum(seconds[:i + 1]) for i in range(len(seconds))]
    gap = max(0., total - sum(lengths)) / len(lines)
    first = body.index('[L1]')
    span = max(1, body.index('[L%d]' % len(lines)) - first)  # Scenery after the last line is not story time.
    placed, clock = [], 0.
    for index, length in enumerate(lengths):
        even = sum(lengths[:index]) + index * gap
        story = (body.index('[L%d]' % (index + 1)) - first) / span * total
        start = max(clock, min((even + story) / 2, total - sum(lengths[index:])))
        part = next((n for n, end in enumerate(ends) if start < end), len(ends) - 1)
        opening = ends[part - 1] if part else 0.
        if start + length > ends[part] and part < len(ends) - 1:
            if ends[part] - length >= max(clock, opening):
                start = ends[part] - length
            elif start - opening > seconds[part] / 2:  # A long line starting early in a part stays there.
                part, start = part + 1, max(start, ends[part])
        placed.append(part)
        clock = start + length
    return placed


def spoken(line, words):
    """A line put back by code: H3 lines carry their own speaker tag; plain quotes need a speech verb."""
    if '<d>' in line:
        return line
    return ('说：' if language(words) in ('Chinese', 'Japanese') else 'says: ') + line


def clauses(text):
    """Sentences, and the comma-separated pieces inside them, in order. A period before a digit (00:04.000)
    or a letter does not end one."""
    pieces = re.findall(r'.+?(?:[。！？!?；;，,]+[”"’』」)]*|\.(?![\w])[”"’』」)]*|$)', text, re.S)
    return [piece.strip() for piece in pieces if piece.strip()]


def sentences(body):
    """Sentences in order; a spoken line [L1] ends one. A period before a digit or a letter does not."""
    return [piece.strip() for piece in re.findall(
        r'.+?(?:\[L\d+\][。！？!?.，,]*|[。！？!?]+[”"’』」)]*|\.(?![\w])[”"’』」)]*|$)', ' '.join(body.split()), re.S)
        if piece.strip()]


def wide(words):
    """Mostly Chinese, Japanese or Korean letters."""
    letters = re.findall(r'[^\W\d_]', words)
    return sum(bool(re.match('[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]', c)) for c in letters) * 2 > len(letters)


def action(words):
    """Long enough to be an action rather than a name: six Chinese or Japanese characters, or twelve letters."""
    return len(words) >= (6 if re.search('[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]', words) else 12)


def quiet(body):
    return '画面继续，没有人说话。' if language(body) == 'Chinese' else 'The scene goes on and nobody speaks.'


def validate_split(text, value, *, strict=False):
    """Part texts built from the model's sorting; every spoken line kept once, in order.

    Shared is what every part repeats, so an action in it is played again in every part: words of Shared
    that also stand in a part, or in the description after its opening sentence, are story and are left
    out, and a Shared that holds a spoken line or a whole part is the story itself and fails, as does an
    answer in another language than the description. Strict also fails on such words, on a part with
    nothing of its own and on lines not placed once and in order, so the model can be asked again;
    otherwise a misplaced line is placed by speaking time and an empty later part carries on quietly.
    """
    body, sound, lines = part_source(value['text'])
    shared, events = split_answer(text, len(value['seconds']))
    if re.search(r'\[L\d+\]', shared) or wide(body) != wide(shared + ' '.join(events)):
        raise ValueError('invalid_output')
    used = set()
    def once(match):
        # A marker written twice stays where it first stands.
        if match.group(0) in used:
            return ''
        used.add(match.group(0))
        return match.group(0)
    events = [' '.join(re.sub(r'\[L\d+\]', once, event).split()) for event in events]
    keys = [plain(re.sub(r'\[L\d+\]', '', event)) for event in events]
    if any(len(event) > 4000 for event in events) or any(key and (key in plain(shared) or key == plain(body))
                                                         for key in keys):
        raise ValueError('invalid_output')
    # Clauses after the opening sentence, each with whether it stands in the first half of the description.
    told_at, story = 0, []
    for index, sentence in enumerate(sentences(body)):
        for piece in clauses(sentence):
            words = plain(re.sub(r'\[L\d+\]', '', piece))
            if index and action(words):
                story.append((words, told_at < len(body) / 2))
            told_at += len(piece)
    if not 20 <= len(shared) <= 4000:
        raise ValueError('invalid_output')
    def placed_twice(piece):
        return action(plain(piece)) and any(plain(piece) in key for key in keys)
    def told(piece):
        return next((early for words, early in story if action(plain(piece))
                     and (plain(piece) in words or words in plain(piece))), None)
    if strict and any(placed_twice(piece) for piece in clauses(shared)):
        raise ValueError('invalid_output')
    join = '' if wide(shared) else ' '
    kept = [piece for piece in clauses(shared) if not placed_twice(piece) and told(piece) is None]
    # Story the model left in Shared alone: its first half opens the video, the rest is not repeated.
    opening = [piece for piece in clauses(shared) if not placed_twice(piece) and told(piece)]
    shared = ' '.join(join.join(kept).split()).rstrip(',，;；')
    if opening:
        lead = ' '.join(join.join(opening).split()).rstrip(',，;；。.') + ('。' if join == '' else '.')
        events[0] = join.join([lead[:1].upper() + lead[1:], events[0]]).strip()
        keys[0] = plain(re.sub(r'\[L\d+\]', '', events[0]))
    seen, cleaned = [], []
    for event, key in zip(events, keys):
        if any(key in earlier for earlier in seen):
            key = ''
        seen.append(key)
        cleaned.append(event if key else ' '.join(re.findall(r'\[L\d+\]', event)))
    events = cleaned
    if not re.sub(r'\[L\d+\]', '', events[0]).strip() or strict and not all(
            re.sub(r'\[L\d+\]', '', event).strip() for event in events):
        raise ValueError('invalid_output')
    placed = [int(n) for event in events for n in re.findall(r'\[L(\d+)\]', event)]
    if placed != list(range(1, len(lines) + 1)) and strict:
        raise ValueError('invalid_output')
    if placed != list(range(1, len(lines) + 1)):
        # The model lost or reordered a line: keep its events, place the lines by time.
        events = [' '.join(re.sub(r'\s*\[L\d+\]\s*[:：,，]?', ' ', event).split()).rstrip(':：,，') for event in events]
        for number, part in enumerate(place_lines(body, lines, value['seconds']), 1):
            events[part] = (events[part] + ' [P%d]' % number).strip()
    def restore(words):
        # [L] stands where the model put a line in its own words; [P] where code placed it.
        return re.sub(r'\[([LP])(\d+)\]', lambda m: (lines[int(m.group(2)) - 1] if m.group(1) == 'L'
                                                      else spoken(lines[int(m.group(2)) - 1], shared)), words)
    parts = []
    for index, event in enumerate(events):
        words = ' '.join(([CONTINUE] if index or value.get('continued') else []) + ([shared] if shared else []) +
                         [restore(event) if re.sub(r'\[[LP]\d+\]', '', event).strip() else
                          (restore(event) + ' ' + quiet(body)).strip()])
        if sound:
            words = 'integrated_multimodal_description: [Shot 1] ' + words + '\n\n' + sound
        parts.append(words)
    return parts


def order_split(value):
    """Part texts when the prompt model cannot sort the description: its sentences in order, each in one part.

    Nothing is repeated, so no part plays the story again; a spoken line ends its sentence. A part between
    sentences carries on from the previous one without speech. Shot labels and their times describe the whole video and
    are left out.
    """
    body, sound, lines = part_source(value['text'])
    body = re.sub(r'\[Shot \d+\]\s*|\bAt \d{2}:\d{2}(?:\.\d+)?\s*[:,]?\s*', '', body)
    pieces = sentences(body)
    count = len(value['seconds'])
    if not pieces:
        raise ValueError('invalid_prompt')
    owner = []
    if len(pieces) <= count:
        # A short story is spread over the video instead of ending early.
        owner = [index * count // len(pieces) for index in range(len(pieces))]
    else:
        # Each part takes the sentences whose middle falls in its share of the video, at least one each.
        # Spoken lines take time: a sentence weighs as much as its words with the lines put back.
        sizes = [len(re.sub(r'\[L(\d+)\]', lambda m: lines[int(m.group(1)) - 1], piece)) for piece in pieces]
        total, ends = sum(sizes), []
        clock = 0.
        for length in value['seconds']:
            clock += length
            ends.append(clock / sum(value['seconds']))
        done = 0
        for index, size in enumerate(sizes):
            middle = (done + size / 2) / total
            done += size
            part = next(n for n, end in enumerate(ends) if middle <= end or n == count - 1)
            low = owner[-1] if owner else 0
            # Room for every later part to get one sentence, and no part skipped while sentences remain.
            part = max(low, count - (len(pieces) - index), min(part, low + 1 if owner else 0))
            owner.append(part)
    events = [[] for _ in range(count)]
    for piece, part in zip(pieces, owner):
        events[part].append(piece)
    parts = []
    for index, event in enumerate(events):
        # A part with no sentence of its own goes on quietly instead of inventing speech.
        words = re.sub(r'\[L(\d+)\]', lambda m: lines[int(m.group(1)) - 1], ' '.join(event)) or quiet(body)
        words = ' '.join(([CONTINUE] if index or value.get('continued') else []) + [words])
        if sound:
            words = 'integrated_multimodal_description: [Shot 1] ' + words + '\n\n' + sound
        parts.append(words)
    return parts


SOUND_SECTIONS = re.compile(r'\n\n(?:overall_soundscape|non_diegetic_music):')
SENTENCE_END = '.。!！?？'


def own_words(texts):
    """Where each split part's own events stand in its text: [start, end] in UTF-16 units, or None.

    The continuation sentence, the shared description, subject definitions, LoRA trigger words and sound
    sections are put there by code and are the same in every part; the page shows and edits only the rest.
    The shared description is what the parts start with in common, up to a sentence end before a space.
    """
    starts, ends = [], []
    for text in texts:
        found = SOUND_SECTIONS.search(text)
        ends.append(found.start() if found else len(text))
        at = text.find(CONTINUE + ' ')
        starts.append(at + len(CONTINUE) + 1 if at >= 0 else None)
    # The opening part has no continuation sentence; what stands before it elsewhere comes first there too.
    lead = next((texts[i][:at - len(CONTINUE) - 1] for i, at in enumerate(starts) if at is not None), None)
    if lead is not None and starts and starts[0] is None and texts[0].startswith(lead):
        starts[0] = len(lead)
    known = [texts[i][at:ends[i]] for i, at in enumerate(starts) if at is not None and at <= ends[i]]
    if len(known) < 2:
        return [None] * len(texts)
    common = known[0]
    for words in known[1:]:
        common = common[:next((i for i, (a, b) in enumerate(zip(common, words)) if a != b), min(len(common), len(words)))]
    shared = next((k for k in range(len(common), 19, -1) if common[k - 1] in SENTENCE_END
                   and all(len(words) == k or words[k] == ' ' for words in known)), None)
    if shared is None:
        return [None] * len(texts)
    def units(text, index):
        return len(text[:index].encode('utf-16-le')) // 2
    spans = []
    for text, at, end in zip(texts, starts, ends):
        if at is None or at > end or not text[at:end].startswith(common[:shared]):
            spans.append(None)
            continue
        begin = at + shared + 1
        # A part with nothing of its own shows the shared description it repeats.
        spans.append([units(text, at), units(text, end)] if begin >= end
                     else [units(text, begin), units(text, end)])
    return spans


def validate_parts(parts, value):
    """Worker output shape for a split job: one non-empty text per part."""
    if (not isinstance(parts, list) or len(parts) != len(value['seconds'])
            or any(not isinstance(part, str) or not 40 <= len(part) <= 20000 for part in parts)):
        raise ValueError('invalid_output')
    return parts


SPLIT_RETRY = ('Answer again with exactly one "Shared:" line and the "Part N:" lines, every [L] marker exactly once '
               'and in order, and no marker in Shared. Shared holds no action, and every part has something of its '
               'own that no other part repeats.')
