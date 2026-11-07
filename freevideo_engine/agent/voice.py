"""Voice change jobs: the lines of a selection, and new voice for the lines the user edited.

The lines come from the separated voice (where each one starts and ends) and the recogniser (what it says).
A replacement keeps every unedited sample of the separated voice and puts each new line into the window of the
original one; a line that cannot fit before the next original line keeps its original voice.
"""
import hashlib
import json
import re
import secrets
import shutil
import threading
import time
from collections import OrderedDict
from math import gcd
from pathlib import Path

import numpy as np

from .jobs import JobError
from .stems import FOLDER as STEMS_FOLDER, _busy, run_stems

FOLDER = 'freevideo-voice'
FPS = 24
RATE = 44100
GAP_BEFORE = .05        # quiet kept before the first sound of a line, at most (half the gap to the line before)
GAP_AFTER = .1          # quiet kept after the last sound of a line, at most (half the gap to the next line)
CLEARANCE = .1          # a new voice stops this long before the next original line starts
MIN_WINDOW = .3
MARGIN = .1             # extra file read around a line when its sound is measured
PREEMPTIONS = 8
BUSY_WAITS = 8
SPEED = dict(zh=4.5, en=2.8)      # units per second of a voice, when the line gave no rate of its own
RECOGNITION_CODES = {'no_sound': 'invalid_request', 'too_long': 'invalid_request', 'language': 'speech_language'}
DUB_CODES = {'out_of_memory': 'out_of_memory', 'model_missing': 'voice_model_missing',
             'invalid_request': 'invalid_request', 'disk_space': 'storage_unavailable'}
SELECTION_MESSAGE = 'The selection must be a sound clip played at normal speed, forwards'


def units(text):
    """Spoken units of a text: every Chinese, Japanese or Korean character, and every other word."""
    text = re.sub(r'[　-〿＀-／]', ' ', text)
    wide = re.findall(r'[぀-ヿ㐀-鿿가-힯]', text)
    rest = re.sub(r'[぀-ヿ㐀-鿿가-힯]', ' ', text)
    return len(wide) + len(re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", rest))


def edited(lines):
    """The lines whose text differs from the original after trimming."""
    return [line for line in lines if line['text'].strip() != line['original_text'].strip()]


def _room(lines, limit):
    """{id: seconds a new voice may take from the line's start}, for lines that came with their times.

    The same rule as the placing: the new voice may use the quiet after the line up to CLEARANCE before the next line
    (or the end of the selection); the line's recognised start and end stand in for the measured onset and offset."""
    timed = sorted((line for line in lines if line.get('start') is not None and line.get('end') is not None),
                   key=lambda line: line['start'])
    room = {}
    for position, line in enumerate(timed):
        start, end = line['start'], line['end']
        if position + 1 < len(timed):
            after = timed[position + 1]['start']
        elif limit is not None:
            after = limit + CLEARANCE
        else:
            after = end + 2 * GAP_AFTER
        trail = min(GAP_AFTER, max(0., (after - end) / 2))
        last = max(end + trail, start + MIN_WINDOW)
        room[line['id']] = max(last, after - CLEARANCE) - start
    return room


def estimate(lines, limit=None):
    """Seconds each edited line takes to say: its units over the rate of the original line (or a default).

    A line that came with its times also gets `available` (seconds from its start to the latest end of its new voice) and
    `overrun` (seconds by which the new voice, at the fastest tempo, still passes that end; 0 when it fits)."""
    from ..dubbing import align
    room = _room(lines, limit)
    result = []
    for line in edited(lines):
        text = line['text'].strip()
        rate = line.get('rate')
        if not rate or rate <= 0:
            rate = SPEED['zh'] if re.search(r'[぀-ヿ㐀-鿿가-힯]', text) else SPEED['en']
        seconds = round(max(units(text), 1) / rate, 2)
        available = room.get(line['id'])
        result.append(dict(id=line['id'], seconds=seconds,
                           available=None if available is None else round(available, 2),
                           overrun=None if available is None else round(max(0., seconds / align.MAX_STRETCH - available), 2)))
    return dict(seconds=round(sum(row['seconds'] for row in result), 2), lines=result)


# ---------------------------------------------------------------------------------------------- what was recognised

_RECOGNIZED = OrderedDict()     # token -> dict(project, clip, lines={id: (start, end, text)}, at)
_RECOGNIZED_LOCK = threading.Lock()
RECOGNIZED_KEEP = 64
RECOGNIZED_SECONDS = 3600
LINE_TOLERANCE = .011           # the lines are sent back rounded to milliseconds


def remember(spec, ctx, language, lines):
    """Keep what voice_lines recognised under a token: a hash of the project, the sound it read and every line.

    The ids are positions in this one recognition, so a new recognition (or any change of the sound) gives a new token."""
    rows = {line['id']: (line['start'], line['end'], line['text']) for line in lines}
    source = ctx['source']
    text = json.dumps([spec['editor_project'], ctx['clip']['id'], source['kind'], str(source['id']), ctx['base'],
                       ctx['start'], ctx['end'], language, sorted(rows.items())], ensure_ascii=False)
    token = hashlib.sha256(text.encode()).hexdigest()[:16]
    with _RECOGNIZED_LOCK:
        _RECOGNIZED[token] = dict(project=spec['editor_project'], clip=ctx['clip']['id'], lines=rows, at=time.monotonic())
        _RECOGNIZED.move_to_end(token)
        while len(_RECOGNIZED) > RECOGNIZED_KEEP:
            _RECOGNIZED.popitem(last=False)
    return token


def check(token, project, clip_id, lines):
    """None when the lines agree with the recognition behind the token, else (path, expected, received) of the first
    disagreement: a token that is unknown or too old (the service restarted, or other recognitions pushed it out), an id the
    recognition never had, a line of the recognition that is not sent, another start or end, or another original text. Edited text may be anything; punctuation and width changes count as edits."""
    with _RECOGNIZED_LOCK:
        found = _RECOGNIZED.get(token)
        if found is not None and time.monotonic() - found['at'] > RECOGNIZED_SECONDS:
            del _RECOGNIZED[token]
            found = None
    if found is None:
        return '/recognition', 'a recognition that is still kept; run voice_lines again for this clip', token
    if found['project'] != project or found['clip'] != clip_id:
        return '/recognition', 'a recognition of this clip', token
    for index, line in enumerate(lines):
        known = found['lines'].get(line['id'])
        if known is None:
            return '/lines/%d/id' % index, 'a line id from the recognition', line['id']
        if abs(line['start'] - known[0]) > LINE_TOLERANCE:
            return '/lines/%d/start' % index, 'the recognised start %s' % known[0], line['start']
        if abs(line['end'] - known[1]) > LINE_TOLERANCE:
            return '/lines/%d/end' % index, 'the recognised end %s' % known[1], line['end']
        if line['original_text'].strip() != known[2].strip():
            return '/lines/%d/original_text' % index, 'the recognised text', None
    sent = {line['id'] for line in lines}
    left = [name for name in found['lines'] if name not in sent]
    if left:
        # a new voice that runs on past a changed line must not land on a line that was left out
        return '/lines', 'every line of the recognition (%d lines); missing %s' % (len(found['lines']), ', '.join(left[:3])), len(lines)
    return None


def signature(clip):
    """What a result placed on a clip depends on: where it is, which sound it plays and how. None when it is gone."""
    if not clip:
        return None
    source = clip.get('source') or {}
    return dict(start=clip['start'], frames=clip['frames'], at=clip.get('in', 0), source=[source.get('kind'), source.get('id')],
                speed=clip.get('speed') or 1, reverse=bool(clip.get('reverse')))


# ---------------------------------------------------------------------------------------------- the selection

def _tracks(project):
    return [track for track in project['tracks'] if track['kind'] == 'audio']


def context(jobs, spec):
    """Where the sound of a selection is: {clip, parent, dialogue, separated, base, start, end, source, wav}."""
    from ..editor_export import resolve_source
    try:
        project = jobs.projects.get(spec['editor_project'])['project']
    except FileNotFoundError as error:
        raise JobError('not_found', 'The project does not exist') from error
    found = {}
    for track in _tracks(project):
        for clip in track['clips']:
            found[clip['id']] = (track, clip)
    selection = spec['selection']
    if selection['clip_id'] not in found:
        raise JobError('invalid_request', 'The selection names a clip that is not in this project')
    track, clip = found[selection['clip_id']]
    if track.get('stem') in ('music', 'ambience'):
        raise JobError('invalid_request', 'The music and ambience parts have no voice to change')
    if not clip.get('source'):
        raise JobError('invalid_request', 'The clip is not a sound clip with media')
    if (clip.get('speed') or 1) != 1 or clip.get('reverse'):
        raise JobError('invalid_request', SELECTION_MESSAGE)
    if clip['source']['kind'] == 'generated' and str(clip['source']['id']).startswith('freevideo-dubbing/'):
        raise JobError('invalid_request', 'A written line is dubbed again from its text, not by changing its voice')
    parent, dialogue = clip, None
    if track.get('stem') == 'dialogue':
        dialogue = clip
        if clip.get('stem_of') not in found:
            raise JobError('invalid_request', 'The voice part has lost the clip it was split from')
        parent = found[clip['stem_of']][1]
    else:
        for other, candidate in found.values():
            if (other.get('stem') == 'dialogue' and candidate.get('stem_of') == clip['id']
                    and candidate['start'] <= clip['start'] and candidate['start'] + candidate['frames'] >= clip['start'] + clip['frames']):
                dialogue = candidate
                break
    length = clip['frames'] / FPS
    start = float(selection.get('start', 0.))
    end = float(selection.get('end', length))
    if not 0 <= start < end <= length + 1e-6:
        raise JobError('invalid_request', 'The selection must lie inside the clip')
    carrier = dialogue or clip
    try:
        found_source = resolve_source('%s:%s' % (carrier['source']['kind'], carrier['source']['id']))
    except (OSError, ValueError) as error:
        raise JobError('voice_failed', 'The clip media is not readable') from error
    path = found_source['audio']
    if path is None:
        raise JobError('voice_failed', 'The clip has no sound')
    wav = Path(path).with_suffix('.wav')
    return dict(clip=clip, parent=parent, dialogue=dialogue, separated=dialogue is not None,
                base=carrier.get('in', 0) / FPS, start=start, end=min(end, length),
                source=carrier['source'], path=wav if wav.is_file() else Path(path))


def original_of(ctx, port):
    """The wav of the voice before any change (a second change takes its references from it), and its source."""
    if not ctx['separated']:
        return ctx['path'], None
    note = ctx['path'].parent / 'voice.json'
    if ctx['source']['kind'] == 'generated' and note.is_file():
        try:
            original = json.loads(note.read_text(encoding='utf-8'))['original']
            from ..editor_export import resolve_source
            path = Path(resolve_source(original)['audio'])
            wav = path.with_suffix('.wav')
            return (wav if wav.is_file() else path), original
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return ctx['path'], '%s:%s' % (ctx['source']['kind'], ctx['source']['id'])


# ---------------------------------------------------------------------------------------------- helpers

class _Row:
    """The real job row, with the request spec swapped: lets the stem job run inside another job without laying."""
    def __init__(self, row, spec):
        self._row, self.spec = row, spec

    def __getitem__(self, key):
        return self.spec if key == 'spec' else self._row[key]

    def __setitem__(self, key, value):
        self._row[key] = value

    def get(self, key, default=None):
        return self.spec if key == 'spec' else self._row.get(key, default)


def _cancelled(row):
    if row.get('_cancel'):
        raise JobError('cancelled', 'Cancelled')


def _phase(row, name, **detail):
    row['_phase'] = name
    row['_detail'] = json.dumps(detail) if detail else None


def _separate(jobs, row, ctx, poll=.5):
    """Split the sound of the parent clip without laying the parts; returns the sources and the dialogue wav."""
    from ..editor_export import resolve_source
    spec = dict(row['spec'], clip_id=ctx['parent']['id'], lay=False)
    _phase(row, 'separating')
    result = run_stems(jobs, _Row(row, spec), poll=poll)
    sources = result['sources']
    mp4 = Path(resolve_source(sources['dialogue'])['audio'])
    return sources, (mp4.with_suffix('.wav') if mp4.with_suffix('.wav').is_file() else mp4)


def _drop_folder(jobs, sources):
    if not sources:
        return
    first = next(iter(sources.values()))
    relative = first.split(':', 1)[1].rsplit('/', 1)[0]
    if relative.startswith(STEMS_FOLDER + '/') and relative.count('/') == 1:
        shutil.rmtree(jobs.port.output_path(relative + '/x.mp4', create=False).parent, ignore_errors=True)


def _read(path, first, last):
    """Mono float32 samples at 44.1 kHz of [first, last) seconds of a file, and the rate."""
    from ..stems.separate import read_audio
    audio, _ = read_audio(path)
    low, high = max(0, int(first * RATE)), min(audio.shape[1], int(last * RATE))
    return audio[:, low:high].mean(axis=0)


def _resample(samples, rate):
    if rate == RATE:
        return np.asarray(samples, dtype=np.float32)
    from scipy.signal import resample_poly
    divisor = gcd(RATE, int(rate))
    return resample_poly(np.asarray(samples, dtype=np.float32), RATE // divisor, int(rate) // divisor).astype(np.float32)


# ---------------------------------------------------------------------------------------------- voice_lines

def run_voice_lines(jobs, row):
    """The lines of a selection: where each one is (from the voice) and what it says (from the recogniser)."""
    from .. import subtitle_ears
    from .. import subtitle_sherpa  # noqa: F401  registers the recogniser
    from ..voice import script, sentences, speakers
    spec = row['spec']
    ctx = context(jobs, spec)
    sources = None
    path = ctx['path']
    try:
        if spec.get('separate_first') and not ctx['separated']:
            sources, path = _separate(jobs, row, ctx)
        _cancelled(row)
        try:
            engine = subtitle_ears.engine_for('transcribe')
        except subtitle_ears.EarsError as error:
            raise JobError('no_speech_model', 'No speech model is installed on this machine') from error
        _phase(row, 'recognizing', fraction=0.)

        def cancelled():
            return bool(row.get('_cancel'))

        def progress(fraction):
            _phase(row, 'recognizing', fraction=round(float(fraction), 3))
        try:
            whole = subtitle_ears.decode_mono(path, cancelled=cancelled)
            rate = subtitle_ears.RATE
            low, high = int((ctx['base'] + ctx['start']) * rate), int((ctx['base'] + ctx['end']) * rate)
            audio = np.ascontiguousarray(whole[low:high])
            heard = engine.transcribe(audio, spec.get('language', 'auto'), progress, cancelled) if len(audio) > rate * .2 \
                else dict(language=spec.get('language', 'auto'), lines=[])
        except subtitle_ears.Cancelled as error:
            raise JobError('cancelled', 'Cancelled') from error
        except subtitle_ears.EarsError as error:
            raise JobError(RECOGNITION_CODES.get(error.reason, 'voice_failed'),
                           'The selection could not be recognised (%s)' % error.reason) from error
        words = [word for line in heard['lines'] for word in line['words']]
        rows = sentences.lines(words, audio, rate)
        known = spec.get('script') or []
        rows, entries = script.merge(rows, known) if known else (rows, [None] * len(rows))
        for line, entry in zip(rows, entries):
            if entry is not None:
                # the text is known: it is what the line says, and the recogniser only gives the times
                line['words'] = script.retime(line['words'], entry['text'], line['start'], line['end']) or line['words']
                line['text'] = sentences._join(line['words'])
        slices = [audio[int(line['start'] * rate):int(line['end'] * rate)] for line in rows]
        why = []
        numbers = speakers.label(slices, speakers.embedder(why=why), why)
        if known:
            numbers = speakers.by_role(numbers, [None if entry is None or not (entry.get('speaker') or '').strip()
                                                 else script.roles(known)[entry['speaker'].strip()] for entry in entries])
        lines = []
        for index, line in enumerate(rows, 1):
            lines.append(dict(id='l%03d' % index, start=round(line['start'], 3), end=round(line['end'], 3), text=line['text'],
                              speaker=numbers[index - 1], words=line['words'],
                              rate=round(max(len(line['words']), 1) / max(line['end'] - line['start'], .1), 2)))
        kind = 'dialogue' if ctx['separated'] or sources else 'a1'
        return dict(editor_project=spec['editor_project'], revision=spec['revision'], separated=ctx['separated'],
                    language=heard.get('language'), speech=bool(lines), lines=lines, source=kind,
                    **(dict(speakers_not_told_apart=why[0]) if why and lines else {}),
                    recognition=remember(spec, ctx, spec.get('language', 'auto'), lines) if lines else None)
    finally:
        _drop_folder(jobs, sources)


# ---------------------------------------------------------------------------------------------- voice_replace



def _seed(spec, lines):
    if spec.get('seed') is not None:
        return spec['seed']
    text = '|'.join([spec['editor_project']] + ['%s=%s' % (line['id'], line['text']) for line in lines])
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], 'big') % (2 ** 30)


class Pace:
    """Remaining seconds of the job: the average of the lines done so far, or the estimate before the first."""
    def __init__(self, lines):
        self.guess = {row['id']: row['seconds'] for row in estimate(lines)['lines']}
        self.order = [line['id'] for line in lines]
        self.times, self.mark_time = [], time.monotonic()

    def mark(self):
        now = time.monotonic()
        self.times.append(now - self.mark_time)
        self.mark_time = now

    def left(self, done):
        if self.times:
            return round(sum(self.times) / len(self.times) * max(0, len(self.order) - done), 1)
        return round(sum(self.guess.get(key, 0.) for key in self.order[done:]), 1)


def _round(jobs, row, items, reference, seed, pace, base, count, done, poll=.25):
    """One dubbing job for `items` ([{key, text, seconds, onset, offset, reference?}]).

    Returns ({key: line row with 'data' (mono float32, 44.1 kHz) when a file was made}, cancelled)."""
    from ..dubbing import wavio
    service = getattr(jobs.port.server, '_freevideo_dubbing', None)
    if service is None:
        raise JobError('voice_model_missing', 'The dubbing model is not available on this machine')
    names = {'d%d' % index: item['key'] for index, item in enumerate(items)}
    lines = [dict({k: v for k, v in item.items() if k != 'key'}, id=name) for name, item in zip(names, items)]
    value = dict(lines=lines, reference=reference, seed=seed)
    waits, began = 0, None
    while True:
        _cancelled(row)
        try:
            key = service.start('dub', value, trusted=True, keep=True)['job']
            began = None
        except ValueError as error:
            if str(error) == 'busy':
                began = began or time.monotonic()
                _phase(row, 'waiting', waited_seconds=int(time.monotonic() - began))
                time.sleep(poll * 4)
                continue
            raise JobError(DUB_CODES.get(str(error), 'voice_failed'), 'The voice could not be made (%s)' % error) from None
        cancelled, seen = False, 0
        while True:
            if row.get('_cancel') and not cancelled:
                service.cancel(key)
                cancelled = True
            try:
                state = service.status(key)
            except ValueError:
                raise JobError('voice_failed', 'The dubbing service lost the job') from None
            phase = state.get('phase')
            folder = service.folder
            made = state.get('lines') or []
            while seen < len(made):
                pace.mark()
                seen += 1
            if phase in ('complete', 'failed', 'cancelled'):
                break
            if not cancelled:
                if phase in ('starting', 'preparing'):
                    _phase(row, 'preparing')
                elif phase in ('loading', 'loaded'):
                    _phase(row, 'loading')
                else:
                    index = min(count, base + seen + 1)
                    _phase(row, 'dubbing', index=index, count=count, remaining_seconds=pace.left(base + seen),
                           line=pace.order[index - 1], done=done + [names.get(item['id']) for item in made])
            time.sleep(poll)
        waited = time.monotonic()
        while service.busy() and time.monotonic() - waited < 60:
            time.sleep(.1)
        try:
            if phase == 'failed':
                if state.get('error') == 'busy' and waits < BUSY_WAITS:
                    waits += 1
                    waited = began = time.monotonic()
                    while _busy(jobs) and time.monotonic() - waited < 600:
                        _cancelled(row)
                        _phase(row, 'waiting', waited_seconds=int(time.monotonic() - began))
                        time.sleep(poll * 4)
                    began = None
                    continue
                error = state.get('error')
                raise JobError(DUB_CODES.get(error, 'voice_failed'), 'The voice could not be made (%s)' % error)
            rows = {}
            root = Path(service.input_directory())
            for item in made:
                target = dict(item, key=names[item['id']])
                if item.get('file') and not item.get('code'):
                    samples, rate = wavio.read(root / item['file'])
                    target['data'] = _resample(samples[:, 0], rate)
                rows[target['key']] = target
            return rows, cancelled
        finally:
            if folder:
                shutil.rmtree(folder, ignore_errors=True)


def _measure(audio, first, last):
    """(onset, offset) in file seconds of the sound in [first, last) of a mono 44.1 kHz file, or None."""
    from ..dubbing import align
    low, high = max(0, int(first * RATE)), min(len(audio), int(last * RATE))
    span = align.energy_span(audio[low:high], RATE)
    if span is None:
        return None
    return (low + span[0]) / RATE, (low + span[1]) / RATE


def _level(audio, first, last):
    """RMS of the sound in [first, last) of a mono 44.1 kHz file: the loudness the new voice is to come back with."""
    import numpy as np
    low, high = max(0, int(first * RATE)), min(len(audio), int(last * RATE))
    part = np.asarray(audio[low:high], dtype=np.float64)
    return round(float(np.sqrt(np.mean(np.square(part)))), 6) if len(part) else None


def _windows(every, todo, spans, base, limit):
    """Where each changed line's new voice goes: the original line plus a little quiet each side, and how far it may reach."""
    windows, failures = {}, {}
    for position, line in enumerate(every):
        if line not in todo:
            continue
        onset, offset = spans[line['id']]
        before = spans[every[position - 1]['id']][1] if position else None
        after = spans[every[position + 1]['id']][0] if position + 1 < len(every) else limit + CLEARANCE
        lead = min(GAP_BEFORE, max(0., (onset - before) / 2)) if before is not None else GAP_BEFORE
        trail = min(GAP_AFTER, max(0., (after - offset) / 2))
        first, last = max(0., onset - lead), offset + trail
        if offset - onset < MIN_WINDOW / 2:
            failures[line['id']] = 'failed'
            continue
        # nothing of this window reaches the quiet kept before the next line (where that one's own window starts)
        hard = after if position + 1 == len(every) else after - min(GAP_BEFORE, max(0., (after - offset) / 2))
        last = min(max(last, first + MIN_WINDOW), hard)
        windows[line['id']] = dict(first=first, last=last, onset=onset, offset=offset, hard=hard,
                                   deadline=max(last, after - CLEARANCE))
    return windows, failures


def _reference(jobs, spec, voice, every, original_wav, base):
    """The job's reference (one that is always there), and whether lines keep their own."""
    from ..editor_export import resolve_source
    ref = voice.get('reference')
    if ref:
        project = jobs.projects.get(spec['editor_project'])['project']
        other = next((clip for track in _tracks(project) for clip in track['clips'] if clip['id'] == ref['clip_id']), None)
        if other is None or not other.get('source'):
            raise JobError('invalid_request', 'The reference clip is not in this project')
        try:
            path = resolve_source('%s:%s' % (other['source']['kind'], other['source']['id']))['audio']
        except (OSError, ValueError):
            path = None
        if path is None:
            raise JobError('invalid_request', 'The reference clip has no sound')
        picked = dict(file=str(path), text=ref.get('text', ''), start=other.get('in', 0) / FPS + float(ref.get('start', 0.)))
        if ref.get('end') is not None:
            picked['end'] = other.get('in', 0) / FPS + float(ref['end'])
        return picked, False
    longest = max(every, key=lambda line: line['end'] - line['start'])
    return dict(file=str(original_wav), text=longest['original_text'].strip(), start=base + longest['start'],
                end=base + longest['end']), True


def run_voice_replace(jobs, row, *, poll=.25):
    from ..dubbing import align
    from ..stems.separate import read_audio, write_mp4, write_wav
    from ..voice import assemble
    started = time.monotonic()
    spec, port = row['spec'], jobs.port
    voice = spec.get('voice') or {}
    if voice.get('preset'):
        raise JobError('invalid_request', 'Preset voices are not offered yet')
    ctx = context(jobs, spec)
    every = sorted(spec['lines'], key=lambda line: line['start'])
    todo = edited(every)
    if not todo:
        raise JobError('invalid_request', 'No line was changed')
    sources, separate_seconds, out = None, None, None
    try:
        separated_now = not ctx['separated']
        expect = signature(ctx['parent'] if separated_now else ctx['dialogue'])
        if separated_now:
            began = time.monotonic()
            sources, dialogue_wav = _separate(jobs, row, ctx)
            separate_seconds = round(time.monotonic() - began, 1)
            original_wav, original_source = dialogue_wav, sources['dialogue']
        else:
            dialogue_wav = ctx['path']
            original_wav, original_source = original_of(ctx, port)
        _cancelled(row)
        _phase(row, 'preparing')
        audio, total = read_audio(dialogue_wav)
        mono = audio.mean(axis=0)
        base = ctx['base']
        spans = {}
        for position, line in enumerate(every):
            first, last = base + line['start'], base + line['end']
            low, high = first - MARGIN, last + MARGIN
            # a neighbour closer than the margin is not this line's sound: stop halfway between the two lines
            if position and base + every[position - 1]['end'] > low:
                low = min(first, (base + every[position - 1]['end'] + first) / 2)
            if position + 1 < len(every) and base + every[position + 1]['start'] < high:
                high = max(last, (last + base + every[position + 1]['start']) / 2)
            spans[line['id']] = _measure(mono, low, high) or (first, last)
        windows, failures = _windows(every, todo, spans, base, min(base + ctx['end'], total))
        work = [line for line in todo if line['id'] in windows]
        if not work:
            raise JobError('voice_failed', 'None of the changed lines can be dubbed')
        reference, own = _reference(jobs, spec, voice, every, original_wav, base)
        seed = _seed(spec, todo)
        pace = Pace(work)
        # a text that cannot be said in the room its line has, even at the fastest tempo, is known before anything is made
        expected = {found['id']: found['overrun'] for found in estimate(every, ctx['end'])['lines'] if found['overrun']}
        if expected:
            _phase(row, 'preparing', too_long=sorted(expected))

        def item(line, length, offset):
            window = windows[line['id']]
            made = dict(key=line['id'], text=line['text'].strip(), seconds=round(length, 3),
                        onset=round(window['onset'] - window['first'], 3), offset=round(offset - window['first'], 3))
            level = _level(mono, windows[line['id']]['onset'], windows[line['id']]['offset'])
            if level:
                made['level'] = level
            if own and line['end'] - line['start'] >= 1. and line['original_text'].strip():
                made['reference'] = dict(file=str(original_wav), text=line['original_text'].strip(),
                                         start=base + line['start'], end=base + line['end'])
            return made

        first_round, cancelled = _round(jobs, row, [item(line, windows[line['id']]['last'] - windows[line['id']]['first'],
                                                         windows[line['id']]['offset']) for line in work],
                                        reference, seed, pace, 0, len(work), [], poll)
        # a line that is too long for its own window may reach into the quiet before the next original line
        second_round, retry = {}, []
        if not cancelled:
            for line in work:
                made, window = first_round.get(line['id']), windows[line['id']]
                if made and 'data' in made and made.get('warning') == 'too_long' and window['deadline'] > window['last'] + .02:
                    shortest = window['offset'] - window['onset'] + float(made.get('overrun') or 0.)
                    room = window['deadline'] - window['onset']
                    if room >= shortest * .99:
                        natural = shortest * float(made.get('stretch') or 1.) * float(made.get('speed') or 1.)
                        retry.append((line, min(natural, room)))
        if retry:
            count = len(work) + len(retry)
            pace.order = [line['id'] for line in work] + [line['id'] for line, _ in retry]
            second_round, cancelled = _round(
                jobs, row, [item(line, windows[line['id']]['deadline'] - windows[line['id']]['first'],
                                 windows[line['id']]['onset'] + span) for line, span in retry],
                reference, seed, pace, len(work), count, list(first_round), poll)
        unfinished = {line['id'] for line, _ in retry} - set(second_round) if cancelled else set()
        # what was made goes into the separated voice, and every changed line gets its measured result
        _phase(row, 'placing')
        edits, results = [], []
        for line in every:
            if line not in todo:
                continue
            result = dict(id=line['id'], text=line['text'].strip(), start=line['start'], end=line['end'],
                          replaced=False, overrun=0., fitted=False, warning=None)
            results.append(result)
            if line['id'] in failures:
                result['code'] = failures[line['id']]
                continue
            window = windows[line['id']]
            made = second_round.get(line['id']) or first_round.get(line['id'])
            if made is None:
                result['code'] = 'cancelled'
                continue
            if line['id'] in expected:
                result['expected_overrun'] = expected[line['id']]
            result.update(available=round((window['deadline'] if line['id'] in second_round else window['last']) - window['onset'], 3),
                          speed=made.get('speed'), stretch=made.get('stretch'), gain_db=made.get('gain_db'),
                          overrun=round(float(made.get('overrun') or 0.), 3), warning=made.get('warning'))
            if made.get('code') or 'data' not in made:
                result['code'] = made.get('code') or 'failed'
                continue
            if made.get('overrun') or made.get('warning') == 'too_long':
                result['warning'] = 'too_long'
                continue
            position = int(round(window['first'] * RATE))
            data = made['data'][:max(0, int(window['hard'] * RATE) - position)]
            span = align.energy_span(data, RATE)
            if span is None:
                result['code'] = 'failed'
                continue
            first, last = (position + span[0]) / RATE, (position + span[1]) / RATE
            onset_error, offset_error = first - window['onset'], last - window['offset']
            # the fades stay in the quiet: the original sound is before its onset and after its offset
            fades = (min(assemble.CROSSFADE, max(0., window['onset'] - window['first'])),
                     min(assemble.CROSSFADE, max(0., (position + len(data)) / RATE - window['offset'])))
            edits.append((position, data, fades))
            result.update(replaced=True, onset=round(first - base, 3), offset=round(last - base, 3),
                          onset_error=round(onset_error, 3), offset_error=round(offset_error, 3),
                          fitted=abs(onset_error) <= align.FIT_SECONDS and abs(offset_error) <= align.FIT_SECONDS)
            if not result['fitted'] and not result['warning'] and offset_error < -align.FIT_SECONDS:
                # the line as placed ends before its slot does, whatever the dubbing measured before the cut
                result['warning'] = 'too_short'
        replaced = [item for item in results if item['replaced']]
        if cancelled and not replaced:
            raise JobError('cancelled', 'Cancelled')
        if not replaced and not any(item['warning'] == 'too_long' for item in results):
            code = next((item['code'] for item in results if item.get('code')), 'failed')
            raise JobError(DUB_CODES.get(code, 'voice_failed'), 'No changed line could be dubbed (%s)' % code)
        if not replaced:
            # every changed line was too long: the voice would be the original again, so nothing is written or placed,
            # and a separation made for this job goes too (shorten the lines, then change again)
            _drop_folder(jobs, sources)
            seconds = round(time.monotonic() - started, 1)
            return dict(editor_project=spec['editor_project'], revision=spec['revision'],
                        clip_id=ctx['dialogue']['id'] if ctx['dialogue'] else None,
                        sources=dict(new=None, original=None if separated_now else original_source), separated_now=False, parts=[],
                        placed=False, reason='too_long', cancelled=bool(cancelled),
                        partial=any(item.get('code') == 'cancelled' for item in results) or bool(unfinished),
                        kept=len(every) - len(todo), failed=sum(1 for item in results if item.get('code') and item['code'] != 'cancelled'),
                        lines=results, seed=seed, apply=None, seconds=seconds, separate_seconds=separate_seconds,
                        dub_seconds=round(seconds - (separate_seconds or 0.), 1))
        stitched = assemble.stitch(audio.T, edits, RATE).T
        folder = '%s/%s-%s' % (FOLDER, row['id'], secrets.token_hex(2))
        out = port.output_path(folder + '/dialogue.mp4', create=True).parent
        write_wav(out / 'dialogue.wav', stitched, exact=True)
        write_mp4(out / 'dialogue.mp4', stitched)
        new_source = 'generated:%s/dialogue.mp4' % folder
        (out / 'voice.json').write_text(json.dumps(dict(version=1, original=original_source, base=base, seed=seed,
                                                       lines=results), ensure_ascii=False), encoding='utf-8')
        revision, parts = spec['revision'], []
        clip_id = ctx['dialogue']['id'] if ctx['dialogue'] else None
        laid = dict(sources, dialogue=new_source) if separated_now else None
        apply = dict(op='placeStems', clip_id=ctx['parent']['id'], sources=laid) if separated_now \
            else dict(op='replaceSource', clip_id=clip_id, source=new_source)
        placed, reason = False, None
        if spec.get('lay', True):
            # the project may have moved on while the voice was made: it is placed only if the clip it goes on is as it was
            try:
                if separated_now:
                    saved, clip_ids = jobs.projects.lay_stems(spec['editor_project'], ctx['parent']['id'], laid, expect=expect)
                    fresh = set(clip_ids)
                    parts = [dict(track=track['id'], name=track['stem'], clip_id=clip['id'], start=clip['start'],
                                  frames=clip['frames'], end=clip['start'] + clip['frames'])
                             for track in saved['tracks'] if track.get('stem') for clip in track['clips'] if clip['id'] in fresh]
                    clip_id = next((part['clip_id'] for part in parts if part['name'] == 'dialogue'), None)
                else:
                    saved = jobs.projects.replace_voice_source(spec['editor_project'], clip_id, new_source, expect=expect)
                revision, placed, apply = saved['revision'], True, None
            except JobError as error:
                if error.code != 'project_changed':
                    raise
                # nothing was changed: the files stay, and the result says how to put them on the project as it is now
                try:
                    revision = jobs.projects.get(spec['editor_project'])['project']['revision']
                except FileNotFoundError as missing:
                    raise JobError('not_found', 'The project does not exist') from missing
                reason = 'project_changed'
        else:
            # the page places it itself: the same check, read only, tells it whether the clip is still as it was
            from .projects import _as_expected
            try:
                current = jobs.projects.get(spec['editor_project'])['project']
            except FileNotFoundError as missing:
                raise JobError('not_found', 'The project does not exist') from missing
            if not _as_expected(current, ctx['parent']['id'] if separated_now else ctx['dialogue']['id'], expect):
                revision, reason = current['revision'], 'project_changed'
        seconds = round(time.monotonic() - started, 1)
        return dict(editor_project=spec['editor_project'], revision=revision, clip_id=clip_id,
                    sources=dict(new=new_source, original=original_source), separated_now=separated_now, parts=parts,
                    placed=placed, reason=reason, cancelled=bool(cancelled),
                    partial=any(item.get('code') == 'cancelled' for item in results) or bool(unfinished), kept=len(every) - len(todo),
                    failed=sum(1 for item in results if item.get('code') and item['code'] != 'cancelled'),
                    lines=results, seed=seed, apply=apply, seconds=seconds, separate_seconds=separate_seconds,
                    dub_seconds=round(seconds - (separate_seconds or 0.), 1))
    except BaseException:
        if out is not None:
            shutil.rmtree(out, ignore_errors=True)
        _drop_folder(jobs, sources)
        raise
