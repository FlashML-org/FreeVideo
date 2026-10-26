"""A screenplay in, a finished short drama out: the reference director loop for agents.

    ./freevideo agent drama plan film.json             # preview prompts and take counts
    ./freevideo agent drama start film.json            # start a durable film
    ./freevideo agent drama get ID --since 1 --wait 25
    ./freevideo agent drama sheet ID SHOT              # review the takes of one shot
    ./freevideo agent drama choose ID SHOT --take 2
    ./freevideo agent drama reshoot ID SHOT --note "..."
    ./freevideo agent drama finish ID                  # join the chosen takes
    ./freevideo agent drama cancel ID

The screenplay is structured (characters, shots, lines), so a planner model only
has to decide what happens. This module writes the H3 prompts, links shots that
continue each other, asks the local VLM to score every shot against its own
description, retakes weak shots with new seeds (with the shots that continue
from them), keeps the best take of each and assembles the cut.
"""
import json
import re
import time

from . import spec as specs
from ..chain_plan import CONTINUE_SECONDS

EXAMPLE = {
    'title': '末班车', 'name': 'the-last-train', 'aspect': '9:16', 'quality': 'light',
    'style': 'Live-action, cinematic, rainy night in a modern Chinese city, teal and amber light',
    'characters': {'lin': {'name': 'the young woman', 'look': 'in her twenties with shoulder-length black hair, a '
                           'mustard-yellow raincoat over a white shirt, carrying a small brown leather suitcase',
                           'voice': 'soft, clear'}},
    'shots': [
        {'id': 'platform', 'seconds': 6, 'framing': 'vertical medium-wide shot of an empty elevated platform',
         'action': 'Lin runs in through shallow puddles and stops under a flickering lamp, catching her breath and '
                   'looking up at the departure board.', 'camera': 'The camera tracks her with small amplitude, then '
         'holds still.', 'lines': [{'who': 'lin', 'line': '还来得及吗？', 'how': 'says breathlessly'}],
         'sound': 'Heavy rain on the platform roof, splashing footsteps, the buzz of the lamp.'},
        {'id': 'arrival', 'seconds': 10, 'continue': True, 'framing': 'vertical medium close-up',
         'action': 'A train headlight sweeps across Lin\'s face; the wind of the arriving train lifts her wet hair and '
                   'her worry turns into a relieved smile.', 'camera': 'The camera pushes in slowly toward her face.',
         'lines': [{'who': 'lin', 'line': '来了。', 'how': 'says quietly'}],
         'sound': 'A rising rumble and screech of brakes, a rush of wind, a pneumatic hiss.'},
        {'id': 'window', 'seconds': 6, 'framing': 'vertical medium shot inside a night subway car',
         'action': 'Lin sits by the rain-streaked window with the suitcase on her knees, watching city lights '
                   'slide past.', 'camera': 'The camera pushes in slowly to her profile.',
         'lines': [{'who': 'lin', 'line': '再见了。', 'how': 'says softly'}],
         'sound': 'The hum of the subway, rain tapping on glass.'},
    ],
    'review': {'min_score': 7, 'retakes': 1, 'frames': 6},
    'transition': 'crossfade', 'transition_seconds': .4,
}


class Screenplay(ValueError):
    """All validation problems; the string remains the first legacy message."""
    def __init__(self, problems):
        self.problems = problems if isinstance(problems, list) else [{'path': '', 'message': problems}]
        super().__init__(self.problems[0]['message'])


CAPTIONED = 3  # the score of a take with unwanted captions
CARD = re.compile(r'c[0-9a-f]{12}')  # characters.<key>.card names a cast library card
# How a shot carries its cast cards. Change these after the reference tests; nothing else needs to.
# Each person is two pictures: one sheet of every view (made by the cast library when the card has none), then
# the face close-up. A continuing shot with several people has no room for two each: it carries one picture a person.
FACE_ORDER = ('face', 'front', 'three_quarter')  # the one picture standing for a person, then any other label, in card order
MAX_VOICES = 3      # voice clips per shot, the model limit
MAX_REFERENCES = 32


def check(play):
    """Validate every independent field, mutating only missing shot ids."""
    problems = []

    def problem(path, message):
        problems.append(dict(path=path, message=message))

    def validate(function, *args, **kwargs):
        try:
            function(*args, **kwargs)
        except specs.SpecError as error:
            problems.extend(error.errors)
        except (TypeError, ValueError, OverflowError):
            problem(args[1] if len(args) > 1 else '', 'must contain valid JSON field values')

    if not isinstance(play, dict) or not isinstance(play.get('shots'), list) or not 1 <= len(play['shots']) <= 40:
        problem('/shots', 'A screenplay needs 1 to 40 shots')
    if not isinstance(play, dict):
        raise Screenplay(problems)
    characters = play.get('characters', {})
    if not isinstance(characters, dict):
        problem('/characters', 'must be an object')
        characters = {}
    for key, person in characters.items():
        path = '/characters/' + str(key).replace('~', '~0').replace('/', '~1')
        if not isinstance(key, str) or not key:
            problem(path, 'character names must be non-empty strings')
        elif '\x00' in key:
            problem(path, 'character names cannot contain NUL')
        if not isinstance(person, dict):
            problem(path, 'must be an object')
            continue
        for field in ('name', 'look', 'voice', 'language'):
            if field in person:
                validate(specs._text, person[field], path + '/' + field, 20000, empty=True)
        if 'card' in person and (not isinstance(person['card'], str) or not CARD.fullmatch(person['card'])):
            problem(path + '/card', 'must be a cast card ID like c0123456789ab')
    for field in ('title', 'style', 'music', 'language'):
        if field in play:
            validate(specs._text, play[field], '/' + field, 20000, empty=True)
    if 'name' in play and (not isinstance(play['name'], str) or not specs.SLUG.fullmatch(play['name'])):
        problem('/name', 'must be a short project name')
    if 'captions' in play:
        validate(specs._bool, play['captions'], '/captions')
    if 'quality' in play and (not isinstance(play['quality'], str) or play['quality'] not in specs.QUALITY):
        problem('/quality', 'must be one of ' + ', '.join(specs.QUALITY))
    # Check aspect and megapixels independently before their combined canvas.
    if 'aspect' in play:
        aspect = play['aspect']
        match = re.fullmatch(r'(\d{1,3}):(\d{1,3})', aspect) if isinstance(aspect, str) else None
        if not match or not int(match[1]) or not int(match[2]):
            problem('/aspect', 'must be like "16:9", "9:16" or "1:1"')
    if 'megapixels' in play:
        validate(specs._number, play['megapixels'], '/megapixels', .1, 16)
    if not any(p['path'] in ('/aspect', '/megapixels') for p in problems):
        validate(specs.canvas_size, {key: play[key] for key in ('aspect', 'megapixels') if key in play})
    if 'seconds' in play:
        validate(specs._number, play['seconds'], '/seconds', 1.625, 60.)
    names = set()
    for i, shot in enumerate(play.get('shots', []) if isinstance(play.get('shots'), list) else []):
        path = '/shots/%d' % i
        if not isinstance(shot, dict):
            problem(path, '/shots/%d must be an object' % i)
            continue
        shot.setdefault('id', 'shot-%d' % (i + 1))
        if not isinstance(shot['id'], str) or not specs.SLUG.fullmatch(shot['id']) or shot['id'] in names:
            problem(path + '/id', '/shots/%d/id must be a unique short name' % i)
        if isinstance(shot['id'], str):
            names.add(shot['id'])
        if not any(isinstance(shot.get(key), str) and shot[key].strip() for key in ('action', 'idea', 'prompt')):
            problem(path, '/shots/%d needs action, idea or prompt' % i)
        if shot.get('continue') and i == 0:
            problem(path + '/continue', '/shots/0 cannot continue a previous shot')
        if 'continue' in shot:
            validate(specs._bool, shot['continue'], path + '/continue')
        for field in ('action', 'idea', 'prompt', 'camera', 'framing', 'sound', 'music'):
            if field in shot:
                validate(specs._text, shot[field], path + '/' + field, 20000, empty=True)
        for field, low, high in (('takes', 1, 8), ('seed', 0, specs.MAX_SEED)):
            if field in shot:
                validate(specs._int, shot[field], path + '/' + field, low, high)
        if 'seconds' in shot:
            validate(specs._number, shot['seconds'], path + '/seconds', 1.625, 60.)
        # A continuing shot starts from the previous one and may add reference pictures, never a first frame.
        if shot.get('first') is not None and (shot.get('continue') or shot.get('references')):
            problem(path, 'first is exclusive with continue and references')
        # Continuing a shot, or one longer than one 15 s block, needs two-pass text generation.
        seconds = shot.get('seconds', play.get('seconds'))
        long = isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 15
        if ((shot.get('continue') or long) and play.get('quality') in specs.QUALITY
                and not specs.QUALITY[play['quality']][0] and all(p['path'] != '/quality' for p in problems)):
            problem('/quality', '"light" or "medium" (two-pass) when a shot continues another or is longer than 15 s')
        if long and (shot.get('first') is not None or shot.get('references') and not shot.get('continue')):
            problem(path + '/seconds', 'at most 15 with a first frame or references')
        elif long and not shot.get('continue') and _carded(play, shot):
            # Cast cards travel as references, which one generation of at most 15 s carries.
            problem(path + '/seconds', 'at most 15 when a character in the shot has a cast card '
                    '(a following shot with continue=true carries the same people further)')
        if shot.get('first') is not None:
            validate(specs.media_item, shot['first'], path + '/first', image=True)
        if 'references' in shot:
            refs = shot['references']
            if not isinstance(refs, list) or len(refs) > 32:
                problem(path + '/references', 'must be a list of at most 32 items')
            elif shot.get('continue') and len(refs) > specs.CONTINUED_PICTURES:
                problem(path + '/references', 'at most %d pictures when the shot continues' % specs.CONTINUED_PICTURES)
            if isinstance(refs, list):
                for j, ref in enumerate(refs):
                    validate(specs.media_item, ref, path + '/references/%d' % j, image=bool(shot.get('continue')))
                    if shot.get('continue') and not specs.still_picture(ref):
                        problem(path + '/references/%d' % j, 'must be a picture when the shot continues')
        if 'cast' in shot:
            if not isinstance(shot['cast'], list):
                problem(path + '/cast', 'must be a list of character names')
            else:
                for j, key in enumerate(shot['cast']):
                    if not isinstance(key, str) or key not in characters:
                        problem(path + '/cast/%d' % j, 'must name a character')
        lines = shot.get('lines', [])
        if not isinstance(lines, list):
            problem(path + '/lines', 'must be a list')
            continue
        for j, line in enumerate(lines):
            line_path = path + '/lines/%d' % j
            if not isinstance(line, dict):
                problem(line_path, 'must be an object')
                continue
            if not isinstance(line.get('who'), str) or line['who'] not in characters:
                problem(line_path + '/who', '/shots/%d/lines/%d/who must name a character' % (i, j))
            validate(specs._text, line.get('line'), line_path + '/line', 20000)
            for field in ('how', 'language'):
                if field in line:
                    validate(specs._text, line[field], line_path + '/' + field, 20000, empty=True)
    if problems:
        raise Screenplay(problems)
    return play


def cast(play, shot):
    """Characters in this shot, speakers first, in order of appearance."""
    order = [line['who'] for line in shot.get('lines', [])]
    order += [key for key in play.get('characters', {}) if key in (shot.get('cast') or [])
              or play['characters'][key].get('name', key).lower() in shot.get('action', '').lower()
              or key.lower() in shot.get('action', '').lower()]
    return list(dict.fromkeys(order))


def _carded(play, shot):
    """Whether check() can already see a cast card on someone in this shot; malformed fields are reported elsewhere."""
    try:
        return any(isinstance(play['characters'][key].get('card'), str) for key in cast(play, shot))
    except (AttributeError, KeyError, TypeError):
        return False


SHOWN_TEXT = re.compile(r'["“「][^"”」]+["”」]')


def prompt(play, shot):
    """The H3 prompt of one shot, from its structured fields."""
    if shot.get('prompt'):
        return shot['prompt']
    characters = play.get('characters', {})
    people = cast(play, shot)
    lead = ', '.join(x for x in (play.get('style'), shot.get('framing')) if x)
    parts = ['[Shot 1] ' + (lead + '.' if lead else '')]
    if shot.get('continue'):
        parts.append('Continuing from the opening frame, the scene keeps its exact layout, lighting and characters.')
    for key in people:
        person = characters[key]
        if person.get('look'):
            parts.append('%s is %s.' % ((person.get('name') or key).capitalize(), person['look'].rstrip('.')))
    parts.append(named(play, people, shot.get('action', '')))
    if shot.get('camera'):
        parts.append(shot['camera'])
    speakers = {}
    for line in shot.get('lines', []):
        who = line['who']
        person = characters[who]
        first = who not in speakers
        speakers.setdefault(who, len(speakers) + 1)
        voice = (' with a %s voice' % person['voice']) if first and person.get('voice') else ''
        language = line.get('language') or person.get('language') or play.get('language') or guess(line['line'])
        parts.append('%s%s (S%d) %s: <d>[%s] %s</d>' % ((person.get('name') or who).capitalize(), voice,
                     speakers[who], line.get('how') or 'says', language, line['line']))
    if not play.get('captions'):
        # Quoted words are text the shot shows (a sign, a menu); then only captions are ruled out.
        scene = ' '.join(shot.get(k) or '' for k in ('framing', 'action', 'camera')) + ' ' + (play.get('style') or '')
        parts.append('No subtitles or captions.' if SHOWN_TEXT.search(scene) else 'No subtitles, no on-screen text.')
    sound = shot.get('sound') or 'Natural ambience of the scene.'
    # Only a shot's own music is written; a score for the whole film is imported and laid on the sound track.
    music = shot.get('music') or 'N/A'
    return ('integrated_multimodal_description: ' + ' '.join(p for p in parts if p) +
            '\n\noverall_soundscape: ' + sound + '\n\nnon_diegetic_music: ' + music)


def subject_definitions(subjects):
    """The prompt prefix tying cast cards to their reference tags; the one place its wording lives.

    subjects: [{'name', 'description', 'pictures': (first, last), a list of such pairs, or None, 'audio': number or None}].
    The page writes the same prompt from the same wording (web/cast_mention.js); tests/fixtures/subject_definitions.json
    holds the cases both sides must reproduce exactly."""
    parts = []
    for number, subject in enumerate(subjects, 1):
        text = '<Subject %d> is %s' % (number, subject['name'])
        description = (subject.get('description') or '').strip().rstrip('.')
        text += (': %s.' % description) if description else '.'
        tags = []
        if subject.get('pictures'):
            # One (first, last) pair, or several when the person's pictures are not side by side.
            ranges = [subject['pictures']] if isinstance(subject['pictures'][0], int) else subject['pictures']
            pictures = ', '.join('<Picture %d>' % a if a == b else '<Picture %d> to <Picture %d>' % (a, b)
                                 for a, b in ranges)
            single = len(ranges) == 1 and ranges[0][0] == ranges[0][1]
            tags.append('%s show%s its appearance' % (pictures, 's' if single else ''))
        if subject.get('audio'):
            tags.append('<Audio %d> is its voice' % subject['audio'])
        if tags:
            text += ' ' + '; '.join(tags) + '.'
        parts.append(text)
    return 'subject_definitions: ' + ' '.join(parts)


def _views(card):
    """The card's pictures other than a sheet, best stand-in first: the face, the front, the three-quarter view, the rest."""
    rank = {label: i for i, label in enumerate(FACE_ORDER)}
    rows = [(i, row) for i, row in enumerate(card.get('images') or []) if row['label'] != 'sheet']
    rows.sort(key=lambda r: (rank.get(r[1]['label'], len(rank)), r[0]))
    return [row['media'] for _, row in rows]


def sheet_of(card):
    """The card's sheet of every view: one it carries itself, else the one the cast library made from its pictures."""
    own = next((row['media'] for row in card.get('images') or [] if row['label'] == 'sheet'), None)
    return own or card.get('_sheet')


def person_pictures(card, *, alone=True, continues=False):
    """The pictures one person carries into a shot, in order: the sheet, then the face close-up (the front view
    when there is no face). Two of them on every device. A card that has no sheet (one picture, or none could be
    made) carries its face alone (else the front view). A shot that continues the previous one with several
    people carries only the face (else the front view) of each, with no sheet."""
    views = _views(card)
    if continues and not alone:
        return views[:1] or ([sheet_of(card)] if sheet_of(card) else [])
    sheet = sheet_of(card)
    if sheet is None:
        return views[:1]
    return [sheet] + [view for view in views[:1] if view.get('file') != sheet.get('file')]


def cast_references(play, shot, cards, *, continues=False):
    """Which cast cards a shot carries, as references and a prompt prefix.

    cards maps a card ID to the current card, or None once it is gone. The same
    function serves plan previews and the moment a take is submitted.
    pictures_each is the most pictures one person carries in this shot.
    cast_reference: None (no character in the shot has a card), 'own' (the shot
    names its own first frame or references; an empty list means no pictures), 'missing' (a card was deleted),
    'partial' (not everyone fit) or 'cards'. 'omitted' lists people left out
    entirely; 'voice_omitted' lists people whose pictures are carried but whose
    voice did not fit. A shot that continues the previous one carries no voice
    clip (its voices carry on from the previous shot) and at most CONTINUED_PICTURES
    pictures: two for a single person, else one for each of up to that many people."""
    characters = play.get('characters', {})
    keys = [k for k in cast(play, shot) if isinstance(characters[k].get('card'), str)]
    result = dict(cast_reference=None, people=[], omitted=[], voice_omitted=[], missing=[], references=[],
                  prefix=None, subjects=[], cards={}, pictures_each=None, clip_seconds=[])
    if not keys:
        return result
    # A references list, even an empty one, is the shot's own choice: cards are added only when it is left out.
    if shot.get('first') is not None or isinstance(shot.get('references'), list):
        result['cast_reference'] = 'own'
        return result
    spoken = {}
    for line in shot.get('lines', []):
        spoken[line['who']] = spoken.get(line['who'], 0) + 1
    # People with more lines come first when not everyone fits; ties keep the order of appearance.
    chosen, pictures, voices = [], 0, 0
    alone = sum(1 for k in keys if cards.get(characters[k]['card']) is not None
                and (cards[characters[k]['card']].get('images') or [])) == 1
    for key in sorted(keys, key=lambda k: -spoken.get(k, 0)):
        identity = characters[key]['card']
        card = cards.get(identity)
        if card is None:
            result['missing'].append(key)
            continue
        images = person_pictures(card, alone=alone, continues=continues)
        if continues:
            if images and len(chosen) < specs.CONTINUED_PICTURES:
                chosen.append((key, card, images, False))
            else:
                result['omitted'].append(key)
            continue
        voice = card.get('voice') is not None and voices < MAX_VOICES
        if pictures + len(images) + voices + voice > MAX_REFERENCES:
            result['omitted'].append(key)
            continue
        if card.get('voice') is not None and not voice:
            result['voice_omitted'].append(key)  # shown, without the voice that did not fit
        pictures, voices = pictures + len(images), voices + voice
        chosen.append((key, card, images, voice))
    chosen.sort(key=lambda row: keys.index(row[0]))
    subjects, clips, picture = [], [], 0
    for key, card, images, voice in chosen:
        result['cards'][card['id']] = card['revision']
        result['references'].extend(dict(item) for item in images)
        if voice:
            clips.append(dict(card['voice']['clip']))
            result['clip_seconds'].append(card['voice'].get('end', 15.) - card['voice'].get('start', 0.))
        person = characters[key]
        subject = dict(name=person.get('name') or key, description=card.get('description'),
                       pictures=(picture + 1, picture + len(images)) if images else None,
                       audio=len(clips) if voice else None)
        picture += len(images)
        if images or voice:
            subjects.append(subject)
        result['people'].append(dict(character=key, card=card['id'], revision=card['revision'],
                                     pictures=len(images), voice=voice))
    result['references'].extend(clips)
    result['pictures_each'] = max((len(images) for _, _, images, _ in chosen), default=0) or None
    if subjects:
        result['prefix'] = subject_definitions(subjects)
        result['subjects'] = subjects
    result['cast_reference'] = ('missing' if result['missing'] else
                                'partial' if result['omitted'] or result['voice_omitted'] else 'cards')
    return result


PROMPT_LIMIT = 20000
DESCRIPTION_CUTS = (500, 250, 120, 0)


def with_cast(text, resolved, *, limit=PROMPT_LIMIT):
    """The submitted prompt: the subject definitions, then the shot's own prompt.

    Cards can grow after a plan; when the whole prompt would pass the limit, the card descriptions are
    shortened (the pictures still show each person). Plan previews and submissions both call this."""
    if not resolved.get('prefix'):
        return text
    full = resolved['prefix'] + '\n\n' + text
    if len(full) <= limit or not resolved.get('subjects'):
        return full
    for cut in DESCRIPTION_CUTS:
        subjects = [dict(s, description=(s.get('description') or '')[:cut].rstrip() or None)
                    for s in resolved['subjects']]
        short = subject_definitions(subjects) + '\n\n' + text
        if len(short) <= limit:
            return short
    return full  # Too long even without descriptions: the prompt check reports it.


def named(play, people, text):
    """Screenplays use short names; prompts and the reviewer see the full description's name."""
    for key in people:
        name = play.get('characters', {})[key].get('name')
        if name and key.lower() != name.lower():
            text = text.replace(key.capitalize(), name.capitalize() if text.startswith(key.capitalize())
                                else name).replace(key, name)
    return text


def guess(text):
    if any('぀' <= c <= 'ヿ' for c in text):
        return 'Japanese'
    if any('가' <= c <= '힯' for c in text):
        return 'Korean'
    if any('一' <= c <= '鿿' for c in text):
        return 'Chinese'
    return 'English'


def intent(play, shot):
    """What the shot should show, in plain words, for the reviewer."""
    lines = ' '.join('%s says "%s".' % (play['characters'][l['who']].get('name', l['who']), l['line'])
                     for l in shot.get('lines', []))
    looks = ' '.join('%s: %s.' % (play['characters'][k].get('name', k), play['characters'][k].get('look', ''))
                     for k in cast(play, shot))
    action = named(play, cast(play, shot), shot.get('action') or shot.get('idea') or '')
    return ' '.join(x.strip().rstrip('.') + '.' for x in (shot.get('framing'), action,
                                                          shot.get('camera'), lines, looks) if x and x.strip())


def shot_seconds(play, shot):
    """A shot says its length; else the film does; else 6 s, and CONTINUE_SECONDS for a shot that continues the
    one before (a full continuing part, which keeps the video to the full number of parts)."""
    return shot.get('seconds', play.get('seconds', CONTINUE_SECONDS if shot.get('continue') else 6))


def shot_job(play, shot, index, ref):
    job = dict(ref=ref, label='%s · %s' % (play.get('title') or 'drama', shot['id']), project=play.get('name'),
               aspect=play.get('aspect', '9:16'), quality=play.get('quality', 'light'),
               seconds=shot_seconds(play, shot), metadata={'drama_shot': shot['id']})
    if play.get('megapixels'):
        job['megapixels'] = play['megapixels']
    if shot.get('seed') is not None:
        job['seed'] = shot['seed']
    if shot.get('idea') and not shot.get('action') and not shot.get('prompt'):
        job['prompt'] = {'from_job': '@%s-idea' % ref}
    else:
        job['prompt'] = prompt(play, shot)
    if shot.get('continue'):
        job['media'] = {'first': {'from_job': '@s%d' % (index - 1), 'frame': 'last'}}
    elif shot.get('first'):
        job['media'] = {'first': shot['first']}
    return job


def review_job(play, refs, ref='review'):
    """One VLM job (one model load) scoring every listed shot against its intent."""
    frames = play.get('review', {}).get('frames', 6)
    questions = []
    for shot, shot_ref in refs:
        questions.append({'text': 'These frames are from one shot of a short drama. The shot should show: %s '
                          'Score 0-10 how well the frames match this and how good the shot looks (faces, hands, '
                          'motion, consistent character appearance). Also say whether any subtitle, caption or '
                          'other text is drawn over the picture (signs and screens in the scene do not count). '
                          'Answer only JSON {"score": integer, "captions": boolean, "issues": [string]}.'
                          % intent(play, shot),
                          'video': {'from_job': shot_ref, 'frames': frames}})
    return dict(ref=ref, kind='vlm', format='json', max_tokens=300, label='%s · review' % (play.get('title') or 'drama'),
                project=play.get('name'), questions=questions)


def batch(play):
    """The whole first pass as one batch: ideas, shots, one review."""
    check(play)
    rows = []
    for i, shot in enumerate(play['shots']):
        if shot.get('idea') and not shot.get('action') and not shot.get('prompt'):
            rows.append(dict(ref='s%d-idea' % i, kind='enhance', text=shot['idea'],
                             seconds=shot_seconds(play, shot), project=play.get('name'),
                             label='%s · %s idea' % (play.get('title') or 'drama', shot['id'])))
        rows.append(shot_job(play, shot, i, 's%d' % i))
    rows.append(review_job(play, [(shot, '@s%d' % i) for i, shot in enumerate(play['shots'])]))
    return rows


def run(client, play, *, log=print):
    """Generate, review, retake weak shots, assemble; returns a report."""
    started = time.monotonic()
    rows = batch(play)
    made = client.batch(rows)
    ids = {row['ref']: job['id'] for row, job in zip(rows, made)}
    shots = play['shots']
    takes = {i: [ids['s%d' % i]] for i in range(len(shots))}
    # Which take of the previous shot each take of a continuing shot starts from.
    parents = {ids['s%d' % i]: ids['s%d' % (i - 1)] for i in range(1, len(shots)) if shots[i].get('continue')}
    scores = {}
    log('submitted %d jobs' % len(made))

    def score(review_id, refs):
        view = client.wait(review_id)
        answers = (view.get('result') or {}).get('answers') or []
        if len(answers) != len(refs):
            # A failed review is not a bad review: leave these takes unscored instead of retaking them.
            log('review %s: %s, %d of %d answers; takes stay unscored' % (review_id, view.get('status'),
                                                                         len(answers), len(refs)))
        for (i, job), answer in zip(refs, answers):
            value = (answer.get('json') or {}).get('score')
            scores[job] = value if isinstance(value, (int, float)) else None
            # H3 sometimes draws spoken lines as captions even when told not to; a new seed usually avoids it.
            if not play.get('captions') and (answer.get('json') or {}).get('captions') is True and scores[job]:
                scores[job] = min(scores[job], CAPTIONED)
                answer['json'].setdefault('issues', []).insert(0, 'captions drawn on the picture')
            log('shot %s take %s: score %s %s' % (shots[i]['id'], job, scores[job],
                                                  '; '.join((answer.get('json') or {}).get('issues') or [])[:200]))
    for i in range(len(shots)):
        view = client.wait(ids['s%d' % i])
        log('shot %s: %s' % (shots[i]['id'], view['status']))
    score(ids['review'], [(i, ids['s%d' % i]) for i in range(len(shots))])
    review = play.get('review', {})
    for _ in range(review.get('retakes', 1)):
        chosen = best(shots, takes, scores, parents)
        weak = [i for i in range(len(shots)) if scores.get(chosen[i]) is not None
                and scores[chosen[i]] < review.get('min_score', 7)]
        if not weak:
            break
        # A retaken shot changes its last frame: the shots that continue from it are retaken too.
        redo = set()
        for i in weak:
            redo.add(i)
            j = i + 1
            while j < len(shots) and shots[j].get('continue'):
                redo.add(j)
                j += 1
        rows = []
        for i in sorted(redo):
            job = shot_job(play, dict(shots[i], seed=None), i, 'r%d' % i)
            if shots[i].get('continue'):
                job['media']['first']['from_job'] = ('@r%d' % (i - 1)) if i - 1 in redo else chosen[i - 1]
            if isinstance(job['prompt'], dict):
                job['prompt'] = {'from_job': ids['s%d-idea' % i]}
            rows.append(job)
        rows.append(review_job(play, [(shots[i], '@r%d' % i) for i in sorted(redo)]))
        made = client.batch(rows)
        new = {row['ref']: job['id'] for row, job in zip(rows, made)}
        log('retaking %s' % ', '.join(shots[i]['id'] for i in sorted(redo)))
        for i in sorted(redo):
            client.wait(new['r%d' % i])
            takes[i].append(new['r%d' % i])
            if shots[i].get('continue'):
                parents[new['r%d' % i]] = new['r%d' % (i - 1)] if i - 1 in redo else chosen[i - 1]
        score(new['review'], [(i, new['r%d' % i]) for i in sorted(redo)])
    chosen = best(shots, takes, scores, parents)
    final = client.submit(dict(kind='assemble', clips=chosen, transition=play.get('transition', 'crossfade'),
                               transition_seconds=play.get('transition_seconds', .4), name=play.get('name') or 'drama',
                               label=play.get('title') or 'drama', project=play.get('name')))
    final = client.wait(final['id'])
    report = dict(title=play.get('title'), status=final['status'], video=(final.get('result') or {}).get('video'),
                  seconds=(final.get('result') or {}).get('seconds'), elapsed_seconds=time.monotonic() - started,
                  shots=[dict(id=shot['id'], takes=[dict(job=j, score=scores.get(j)) for j in takes[i]], chosen=chosen[i])
                         for i, shot in enumerate(shots)], assemble=final['id'],
                  unreviewed=[shot['id'] for i, shot in enumerate(shots) if scores.get(chosen[i]) is None])
    return report


def best(shots, takes, scores, parents):
    """The takes to assemble. Shots joined by "continue" are chosen together: of the chains of takes in which
    each take starts from the previous one, the one whose weakest take scores highest (then the highest total,
    then the earliest)."""
    chosen, i = [], 0
    while i < len(shots):
        end = i
        while end + 1 < len(shots) and shots[end + 1].get('continue'):
            end += 1
        def chains(k, parent):
            options = takes[k] if k == i else [j for j in takes[k] if parents.get(j) == parent]
            for take in options:
                if k == end:
                    yield [take]
                else:
                    for rest in chains(k + 1, take):
                        yield [take] + rest
        found = list(chains(i, None))
        if not found:   # a broken chain cannot happen through run(); keep each shot's best take
            found = [[max(takes[k], key=lambda j: scores.get(j) or 0) for k in range(i, end + 1)]]
        order = {take: n for k in range(i, end + 1) for n, take in enumerate(takes[k])}
        value = lambda chain: (min(scores.get(j) or 0 for j in chain), sum(scores.get(j) or 0 for j in chain),
                               -sum(order[j] for j in chain))
        chosen += max(found, key=value)
        i = end + 1
    return chosen


def main(client, path, *, dry_run=False):
    play = EXAMPLE if path == 'example' else json.loads(open(path, encoding='utf-8').read())
    if dry_run:
        return dict(prompts={shot['id']: prompt(play, shot) for shot in check(play)['shots']
                             if not (shot.get('idea') and not shot.get('action'))}, batch={'jobs': batch(play)})
    import sys
    return run(client, play, log=lambda text: print(text, file=sys.stderr, flush=True))
