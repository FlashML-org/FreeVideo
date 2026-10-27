"""The cast library: durable character and setting cards shared by every film.

One JSON file per card under <jobs>/cast, written atomically under Jobs' lock,
the same way films are kept. Pictures and voices enter a card only as input
references (a file under ComfyUI input, or a saved library video); a card never
stores or shows a local path. Deleting moves the file to cast/.deleted.
"""
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import uuid

from . import assets, errors, operation_receipts, schema, spec
from .jobs import ACTIVE, DONE, JobError, iso

CARD = re.compile(r'c[0-9a-f]{12}')
KINDS = ('character', 'setting')
ORIGINS = ('user', 'agent', 'builtin')
LABELS = schema.LABELS
EDITABLE = ('name', 'description', 'images', 'voice')
MAX_IMAGES = 6
MAX_CANDIDATES = 6
MAX_RECEIPTS = 64
VOICE_SECONDS = (2, 15)
FINISHED = ('finished', 'cancelled')
LIST_NEXT = 'GET /freevideo/agent/v1/cast to find an existing card ID.'

# Reference pictures made by the server (POST /cast/{id}/generate). Remy's consistency tests set the
# defaults; the clip prompts live in reference_prompt and the labels follow its half turn.
COUNT = 4
PROMPT_VERSION = 1
TURN = dict(aspect='9:16', seconds=6)
CLOSE_UP = dict(aspect='1:1', seconds=4)
SCENE = dict(aspect='16:9', seconds=6)

# The sheet of every view a shot carries for each person, put together here from the card's own pictures: up to six
# equal cells (three to a row) on a light grey ground, each picture scaled whole and centred, no text.
SHEET_VERSION = 1
SHEET_ORDER = ('front', 'three_quarter', 'side', 'back', 'face')  # then any other label, in card order
SHEET_CELL = (736, 828)
SHEET_GAP = 48
SHEET_GROUND = (238, 238, 238)
SHEET_COLUMNS = 3
SHEET_MAX = 6
SHEET_SIZE = (SHEET_COLUMNS * SHEET_CELL[0] + (SHEET_COLUMNS + 1) * SHEET_GAP, 2 * SHEET_CELL[1] + 3 * SHEET_GAP)  # 2400 x 1800
_SHEETS_SEEN = set()


def reference_prompt(card, *, face=False):
    """The one place the reference clip prompts are written."""
    who = (card.get('description') or card['name']).strip().rstrip('.')
    if card['kind'] == 'setting':
        return ('%s. A slow, steady camera pan across the empty place, no people, even daylight, '
                'sharp focus, no text.' % who)
    if face:
        return ('%s. A close-up of the face and shoulders, looking straight at the camera with a neutral '
                'expression, in front of a plain light grey studio backdrop. The person stays still. '
                'Even soft studio light, a static camera, sharp focus, no text.' % who)
    return ('%s. The person stands alone in the centre of the frame, the whole body from head to feet, in front '
            'of a plain light grey studio backdrop, in a neutral pose with the arms relaxed. The person turns '
            'slowly in place from facing the camera to facing away from it. Even soft studio light, a static '
            'camera, sharp focus, no other people, no text.' % who)


def turn_label(progress):
    """The view at this point of the half turn the prompt asks for (0 faces the camera, 1 faces away)."""
    angle = 180 * progress
    return ('front' if angle < 30 else 'three_quarter' if angle < 75 else 'side' if angle < 120
            else 'three_quarter' if angle < 150 else 'back')


class CastError(JobError):
    """A card error; conflicts carry the current card so the caller can retry from it."""

    def __init__(self, code, message, next_action, *, card=None, job=None, details=None):
        super().__init__(code, message)
        self.status = errors.ERRORS[code][0]
        self.next_action, self.card, self.job, self.details = next_action, card, job, details or []

    def view(self):
        value = errors.build(self.code, validation=True, details=deepcopy(self.details), message=self.message,
                             next_action=self.next_action)
        if self.code in errors.IMAGE_REASONS:
            value.update(errors.image_failure(self.code))
        if self.card is not None:
            value['card'] = deepcopy(self.card)
        if self.job is not None:
            value['job'] = self.job
        return value


def media_view(item):
    if 'library' in item:
        return dict(library=item['library'], url=schema.PREFIX + '/library/%s/file' % item['library'])
    return assets.view(item['file'])


def view(card):
    """The public card: input references with URLs, no internal bookkeeping, no local paths."""
    value = {k: deepcopy(v) for k, v in card.items() if not k.startswith('_')}
    for key in ('images', 'candidates'):
        value[key] = [dict(deepcopy(row), media=media_view(row['media'])) for row in card.get(key, [])]
    if card.get('voice'):
        value['voice'] = dict(deepcopy(card['voice']), media=media_view(card['voice']['media']),
                              clip=assets.view(card['voice']['clip']['file']))
    value['generate_error'] = errors.with_reason(value.get('generate_error'))
    if card.get('_sheet'):
        value['sheet'] = media_view(card['_sheet'])  # the sheet made from the pictures, when the card has none of its own
    return value


def thumbnail(card):
    images = card['images']
    # The close-up when the card has one, else the front picture; shown small, a full-length picture is only a coat.
    chosen = next((row for label in ('face', 'front') for row in images if row['label'] == label), images[0] if images else None)
    return media_view(chosen['media']) if chosen else None


def row_view(card):
    """One list row: identity, short text and one thumbnail reference (never the picture itself)."""
    return dict(id=card['id'], kind=card['kind'], name=card['name'], description=card['description'],
                origin=card['origin'], revision=card['revision'], updated_at=card['updated_at'],
                images=len(card['images']), voice=card['voice'] is not None,
                generating=card.get('generating') is not None, thumbnail=thumbnail(card),
                generate_error=errors.with_reason(card.get('generate_error')))


def _plain(item):
    """The input file an image item names unchanged (a returned card file), else None."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and set(item) <= {'file', 'label', 'url'} and isinstance(item.get('file'), str):
        return item['file']
    return None


def _check(record, stem):
    card = record['card']
    if (card['id'] != stem or not CARD.fullmatch(stem) or card['kind'] not in KINDS
            or card['origin'] not in ORIGINS or card['by'] not in ORIGINS
            or type(card['revision']) is not int or card['revision'] < 1
            or not isinstance(card['name'], str) or not isinstance(card['description'], (str, type(None)))):
        raise ValueError('Invalid card identity')
    marks = card.get('edited_by_user', {})
    if not isinstance(marks, dict):
        raise ValueError('Invalid edit marks')
    normalized = {k: marks.get(k, False) for k in EDITABLE}
    if not all(type(v) is bool for v in normalized.values()):
        raise ValueError('Invalid edit marks')
    if marks != normalized:
        logging.getLogger(__name__).warning('Normalized edit marks on a readable cast card')
    card['edited_by_user'] = normalized
    unknown_labels = False
    for key, limit in (('images', MAX_IMAGES), ('candidates', MAX_CANDIDATES)):
        if not isinstance(card[key], list) or len(card[key]) > limit:
            raise ValueError('Invalid pictures')
        for row in card[key]:
            if not isinstance(row['label'], str) or not isinstance(row['media']['file'], str):
                raise ValueError('Invalid picture')
            unknown_labels |= row['label'] not in LABELS
    if card['voice'] is not None:
        if not isinstance(card['voice']['clip']['file'], str) or not (
                isinstance(card['voice']['media'].get('file'), str) or isinstance(card['voice']['media'].get('library'), str)):
            raise ValueError('Invalid voice')
    if not isinstance(card['used_by'], list) or not all(isinstance(v, str) for v in card['used_by']):
        raise ValueError('Invalid film list')
    record['receipts'] = operation_receipts.load(record['receipts'])
    json.dumps(record, allow_nan=False)
    if unknown_labels:
        logging.getLogger(__name__).warning('Kept unknown picture labels on a readable cast card')


def _revision(body, header):
    """The expected revision from the body or If-Match; both must agree when both are sent."""
    if header is not None and not isinstance(header, int):
        text = str(header).strip()
        text = text[2:] if text.startswith('W/') else text
        text = text.strip('"')
        if not re.fullmatch(r'[1-9][0-9]{0,15}', text):
            raise spec.SpecError([schema.diagnostic('/If-Match', 'a card revision number', text)])
        header = int(text)
    if body is not None and header is not None and body != header:
        raise spec.SpecError([schema.diagnostic('/revision', 'equal to If-Match when both are supplied', body)])
    return body if body is not None else header


class Cast:
    def __init__(self, jobs):
        self.jobs = jobs
        self.directory = jobs.directory / 'cast'
        self.records = {}
        self.deleted = {}
        self._unreadable = set()  # Bad cards whose quarantine rename failed.
        self.changed = threading.Condition(jobs.lock)
        self.landing = set()  # Cards whose reference pictures a tick is landing now (under the lock).
        self.load()

    # Records ------------------------------------------------------------------------------------------
    def load(self):
        for folder, target in ((self.directory / '.deleted', self.deleted), (self.directory, self.records)):
            for path in sorted(folder.glob('*.json')):
                if not CARD.fullmatch(path.stem):
                    continue
                try:
                    record = json.loads(path.read_text(encoding='utf-8'))
                    _check(record, path.stem)
                    if target is self.records and path.stem in self.deleted:
                        # A deletion stopped after its tombstone was written: the deletion wins.
                        path.unlink()
                        continue
                    target[path.stem] = record
                    self._unreadable.discard(path)
                except FileNotFoundError:
                    continue  # A missing record is not an unreadable card.
                except (ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError, OSError):
                    try:
                        path.replace(path.with_name(path.name + '.bad-' + uuid.uuid4().hex))
                        self._unreadable.discard(path)
                    except OSError:
                        if target is self.records:
                            self._unreadable.add(path)
                    logging.getLogger(__name__).warning('Isolated an invalid cast card; other cards remain available')

    def _write(self, folder, record):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (record['card']['id'] + '.json')
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(record, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _save(self, record):
        self._write(self.directory, record)
        self.records[record['card']['id']] = record
        self._unreadable.discard(self.directory / (record['card']['id'] + '.json'))
        self.changed.notify_all()

    def _record(self, identity):
        record = self.records.get(identity) if isinstance(identity, str) else None
        if record is None:
            raise CastError('not_found', 'The card does not exist.', LIST_NEXT)
        return record

    def _receipt(self, scope, payload, ref, client=None):
        if ref is None:
            return None, None
        schema.validate(schema.REF, ref)
        digest = hashlib.sha256(json.dumps(dict(scope=scope, body=payload), sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        for record in list(self.records.values()) + list(self.deleted.values()):
            for old in record['receipts']:
                if old['key'] != ref or old.get('client') != client:
                    continue
                if old['digest'] != digest:
                    raise JobError('idempotency_conflict', 'Key already used')
                return digest, deepcopy(old['result'])
        return digest, None

    @staticmethod
    def _remember(record, ref, digest, result, client=None):
        if ref is None:
            return
        receipts = record['receipts']
        receipts.append(dict(key=ref, digest=digest, result=deepcopy(result),
                             **({'client': client} if client else {})))
        while len(receipts) > MAX_RECEIPTS:
            receipts.pop(0)

    def _stale(self, card):
        return CastError('stale_revision', 'The card changed since you read it.',
                         'Apply your change to error.card and send its revision.', card=view(card))

    def _now(self):
        return iso(self.jobs.clock())

    # Media --------------------------------------------------------------------------------------------
    def _picture_at(self, identity, item, by, index):
        """`_picture` for the request's images[index]; a refused picture names its place, so the dialog marks it."""
        try:
            return self._picture(identity, item, by)
        except CastError as error:
            if error.code == 'invalid_media' and not error.details:
                error.details = [dict(field='/images/%d/media' % index)]
            raise

    def _picture(self, identity, item, by):
        """Resolve one picture to an input file in this card's folder (frames, imports, edits)."""
        from ..comfy_assets import media_kind
        if isinstance(item, dict):
            item = {k: v for k, v in item.items() if k != 'url'}
        source = spec.media_item(item, '/media', image=True)
        relative = self.jobs.media_file(dict(id=identity), source, 'image-' + uuid.uuid4().hex[:8])
        try:
            kind = media_kind(relative)
        except ValueError:
            kind = None
        if kind != 'image':
            raise CastError('invalid_media', 'A card picture must be an image.',
                            'Upload an image, or name a video frame with from_job or library and frame.')
        # The extension alone lets a renamed or cut-off file in; decode it once so a broken picture is refused here,
        # not when a video first uses the card.
        try:
            from PIL import Image
            with Image.open(self.jobs.port.input_path(relative)) as image:
                image.load()
        except Exception:  # noqa: BLE001 - any decoder failure means the picture cannot be used
            raise CastError('invalid_media', 'A card picture must be a readable image.',
                            'Upload another image.') from None
        entry = dict(media=dict(file=relative), by=by)
        if isinstance(source, dict) and source.get('from_job'):
            entry['from_job'] = source['from_job']
        return entry

    def _voice(self, identity, value):
        """A 2 to 15 second voice sample: the stable source reference plus a trimmed WAV clip."""
        from . import media
        item = value['media']
        item = {'file': item} if isinstance(item, str) else {k: v for k, v in item.items() if k not in ('url', 'label')}
        port, folder = self.jobs.port, 'freevideo/agent/' + identity
        name = 'voice-' + uuid.uuid4().hex[:8]
        if 'file' in item:
            source, kept = port.input_path(item['file']), dict(file=item['file'])
            if not source.is_file():
                raise JobError('invalid_media', 'Upload the media file first')
        elif 'path' in item:
            source = Path(item['path']).expanduser()
            if not source.is_absolute() or not source.is_file():
                raise JobError('invalid_media', 'Local file not found')
            kept = dict(file=folder + '/' + name + '-source' + source.suffix.lower())
            port.import_file(source, port.input_path(kept['file'], create=True))
        else:
            source = self.jobs.source_video(item)
            library = item.get('library') or (self.jobs.get(item['from_job']).get('result') or {}).get('library_id')
            if library:
                kept = dict(library=library)
            else:
                kept = dict(file=folder + '/' + name + '-source' + source.suffix.lower())
                port.import_file(source, port.input_path(kept['file'], create=True))
        start = value.get('start', 0)
        with self.jobs.cpu:
            try:
                info = media.inspect(source)
                if info['kind'] not in ('audio', 'video') or not info.get('has_audio'):
                    raise ValueError('No audio')
                end = value.get('end')
                if end is None:
                    end = min(info['seconds'], start + VOICE_SECONDS[1]) if info.get('seconds') else start + VOICE_SECONDS[1]
                if not VOICE_SECONDS[0] <= round(end - start, 3) <= VOICE_SECONDS[1]:
                    raise ValueError('Voice length')
                clip = folder + '/' + name + '.wav'
                written = media.trim_audio(source, port.input_path(clip, create=True), start, end)
            except ValueError:
                written = 0
        if written < VOICE_SECONDS[0] - .05:
            raise CastError('invalid_media', 'The voice must be readable audio from 2 to 15 seconds long.',
                            'Send voice.media with audio, and voice.start and voice.end 2 to 15 seconds apart.')
        if written < end - start - .01:
            end = start + written  # The audio ended before the requested end: the card shows the clip it has.
        return dict(media=kept, start=start, end=round(end, 3), clip=dict(file=clip))

    # Operations ---------------------------------------------------------------------------------------
    def create(self, value, *, by='builtin', ref=None, made_for=None, local=True, client=None):
        shape = schema.cast_schema('create', local=local)
        schema.validate(shape, value)
        ref = ref if ref is not None else value.get('ref')
        value = schema.defaults(shape, {k: v for k, v in value.items() if k != 'ref'})
        with self.changed:
            digest, replay = self._receipt('cast:create', value, ref, client)
            if replay is not None:
                return replay
        identity = 'c' + uuid.uuid4().hex[:12]
        images = []
        for index, row in enumerate(value.get('images', [])):
            entry = self._picture_at(identity, row['media'], by, index)
            images.append(dict(entry, label=row.get('label', 'other')))
        voice = self._voice(identity, value['voice']) if value.get('voice') else None
        with self.changed:
            digest, replay = self._receipt('cast:create', value, ref, client)
            if replay is not None:
                return replay
            stamp = self._now()
            supplied = dict(name=True, description=value.get('description') is not None,
                            images=bool(images), voice=voice is not None)
            card = dict(id=identity, kind=value['kind'], name=value['name'], description=value.get('description'),
                        images=images, voice=voice, revision=1, created_at=stamp, updated_at=stamp, by=by,
                        origin=by, made_for=made_for, used_by=[],
                        edited_by_user={k: by == 'user' and supplied[k] for k in EDITABLE},
                        candidates=[], generating=None, generate_error=None, generated=None, _user_images=0)
            record = dict(card=card, receipts=[])
            result = view(card)
            self._remember(record, ref, digest, result, client)
            self._save(record)
            return result

    def get(self, identity):
        with self.changed:
            card = deepcopy(self._record(identity)['card'])
        if card.get('kind') == 'character':
            sheet = self.sheet(card)
            if sheet is not None:
                card['_sheet'] = sheet
        return view(card)

    def list(self, *, q=None, kind=None, origin=None, production=None, limit=50):
        with self.changed:
            cards = [r['card'] for r in self.records.values()]
            text = q.casefold() if q else None
            rows = [c for c in cards if (not kind or c['kind'] == kind) and (not origin or c['origin'] == origin)
                    and (not production or production in c['used_by'] or c['made_for'] == production)
                    and (not text or text in c['name'].casefold() or text in (c['description'] or '').casefold())]
            rows.sort(key=lambda c: (c['updated_at'], c['id']), reverse=True)
            unreadable = 0
            if production is None:
                quarantined = {path for path in self.directory.glob('*.json.bad-*') if path.is_file()}
                unreadable = len(quarantined | {path for path in self._unreadable if path.exists()})
            return dict(cards=[row_view(c) for c in rows[:limit]], total=len(rows), unreadable=unreadable)

    def update(self, identity, value, *, by='builtin', ref=None, revision=None, local=False, client=None):
        shape = schema.cast_schema('update', local=local)
        shape['properties']['images']['items']['properties']['label'] = schema.enum(LABELS)
        checked = deepcopy(value)
        with self.changed:
            record = self.records.get(identity) if isinstance(identity, str) else None
            if record is not None and isinstance(checked, dict) and isinstance(checked.get('images'), list):
                known = {(row['media']['file'], row['label']) for key in ('images', 'candidates')
                         for row in record['card'][key] if row['label'] not in LABELS}
                for row in checked['images']:
                    if isinstance(row, dict) and isinstance(row.get('label'), str):
                        if (_plain(row.get('media')), row['label']) in known:
                            # Let callers return a saved label unchanged; new labels still use the request schema.
                            row.pop('label')
        schema.validate(shape, checked)
        ref = ref if ref is not None else value.get('ref')
        value = {k: v for k, v in value.items() if k != 'ref'}
        expected = _revision(value.pop('revision', None), revision)
        payload = dict(value, revision=expected)
        scope = 'cast:update:' + str(identity)
        with self.changed:
            digest, replay = self._receipt(scope, payload, ref, client)
            if replay is not None:
                return replay
            card = self._record(identity)['card']
            if expected is None:
                raise spec.SpecError([schema.diagnostic('/revision', 'the card revision you read (or an If-Match header)',
                                                        None, missing=True)])
            if expected != card['revision']:
                raise self._stale(card)
            changes = self._changes(card, value, by)
        resolved = {}
        for key, rows in changes.items():
            if key == 'images':
                resolved['images'] = [entry if entry is not None else dict(self._picture_at(identity, row['media'], by, index),
                                      label=row.get('label', 'other')) for index, (row, entry) in enumerate(rows)]
            elif key == 'voice':
                resolved['voice'] = None if rows is None else self._voice(identity, rows)
            else:
                resolved[key] = rows
        with self.changed:
            digest, replay = self._receipt(scope, payload, ref, client)
            if replay is not None:
                return replay
            record = deepcopy(self._record(identity))
            card = record['card']
            if card['revision'] != expected:
                raise self._stale(card)
            if resolved:
                if 'images' in resolved:
                    files = {row['media']['file'] for row in resolved['images']}
                    card['candidates'] = [row for row in card['candidates'] if row['media']['file'] not in files]
                    if by == 'user':
                        card['_user_images'] = card.get('_user_images', 0) + 1
                card.update(resolved, revision=card['revision'] + 1, updated_at=self._now(), by=by)
                if by == 'user':
                    for key in resolved:
                        card['edited_by_user'][key] = True
            result = view(card)
            self._remember(record, ref, digest, result, client)
            if resolved or ref is not None:
                self._save(record)
            return result

    def _changes(self, card, value, by):
        """What this request changes, checked against the user's edits before any media work."""
        changes = {}
        for key in ('name', 'description'):
            if key in value and value[key] != card[key]:
                changes[key] = value[key]
        if 'images' in value:
            known = {row['media']['file']: row for row in card['images']}
            offered = {row['media']['file']: row for row in card['candidates']}
            rows, kept, added, fresh = [], set(), set(), False
            for row in value['images']:
                file = _plain(row['media'])
                entry = deepcopy(known.get(file) or offered.get(file)) if file else None
                if entry is not None:
                    (kept if file in known else added).add(file)
                    if row.get('label', entry['label']) != entry['label']:
                        entry.update(label=row['label'], by=by)
                        entry.pop('label_guess', None)
                        if by == 'user':
                            entry.pop('generated', None)  # The user's label makes it the user's picture.
                else:
                    fresh = True
                rows.append((row, entry))
            current = [(r['media']['file'], r['label']) for r in card['images']]
            proposed = [(e['media']['file'], e['label']) for _, e in rows if e is not None]
            if fresh or proposed != current:
                changes['images'] = rows
                if by != 'user' and card['edited_by_user']['images']:
                    # The user's pictures may only be joined by candidates the server made; never replaced.
                    untouched = all((f, l) in proposed for f, l in current) and len(kept) == len(current)
                    if fresh or not untouched or not added:
                        raise self._by_user(card, 'images')
        if 'voice' in value:
            voice, current = value['voice'], card['voice']
            if voice is None:
                if current is not None:
                    changes['voice'] = None
            else:
                item = voice['media'] if isinstance(voice['media'], dict) else {'file': voice['media']}
                item = {k: v for k, v in item.items() if k != 'url'}
                same = current is not None and item == current['media'] and voice.get('start', 0) == current['start'] \
                    and voice.get('end', current['end']) == current['end']
                if not same:
                    changes['voice'] = voice
        if by != 'user':
            for key in changes:
                if key != 'images' and card['edited_by_user'][key]:
                    raise self._by_user(card, key)
        return changes

    def _by_user(self, card, key):
        return CastError('changed_by_user', 'The user changed %s on this card.' % key,
                         "Keep the user's change; do not change it back. Ask the user if it must change.",
                         card=view(card))

    def delete(self, identity, value=None, *, by='builtin', ref=None, revision=None, client=None):
        value = value or {}
        schema.validate(schema.cast_schema('delete'), value)
        ref = ref if ref is not None else value.get('ref')
        value = {k: v for k, v in value.items() if k != 'ref'}
        expected = _revision(value.pop('revision', None), revision)
        payload = dict(value, revision=expected)
        with self.changed:
            digest, replay = self._receipt('cast:delete:' + str(identity), payload, ref, client)
            if replay is not None:
                return replay
            record = deepcopy(self._record(identity))
            card = record['card']
            if expected is not None and expected != card['revision']:
                raise self._stale(card)
            if by != 'user':
                if card['origin'] != by or any(card['edited_by_user'].values()):
                    raise CastError('not_allowed', 'Only the user may delete this card.',
                                    'Ask the user before deleting this card.', card=view(card))
                if any(self._unfinished(film) for film in card['used_by']):
                    raise CastError('card_in_use', errors.ERRORS['card_in_use'][2],
                                    errors.ERRORS['card_in_use'][3], card=view(card))
            self._before_delete(card)
            card.update(deleted_at=self._now(), deleted_by=by)
            result = dict(id=card['id'], deleted=True)
            self._remember(record, ref, digest, result, client)
            self._write(self.directory / '.deleted', record)
            (self.directory / (card['id'] + '.json')).unlink(missing_ok=True)
            self.records.pop(card['id'], None)
            self.deleted[card['id']] = record
            self.changed.notify_all()
            return result

    def _before_delete(self, card):
        """A generation ends with its card; Jobs also refuses to start it once the card is gone."""
        for job in _generation_jobs(card.get('generating')):
            row = self.jobs.jobs.get(job)
            if row is not None and row['status'] in ACTIVE:
                try:
                    self.jobs.cancel(job)
                except (JobError, KeyError):
                    pass

    def _unfinished(self, film):
        record = self.jobs.productions.records.get(film)
        return record is not None and record['state'].get('status') not in FINISHED

    # Filming ------------------------------------------------------------------------------------------
    def snapshot(self, identity):
        """The current card for a submission, or None once it is deleted or unknown. A card with several pictures
        and no sheet of its own carries the one made from them as `_sheet` (never part of the public card)."""
        with self.changed:
            record = self.records.get(identity)
            card = deepcopy(record['card']) if record else None
        if card is not None and card.get('kind') == 'character':
            sheet = self.sheet(card)
            if sheet is not None:
                card['_sheet'] = sheet
        return card

    def sheet(self, card):
        """The sheet of every view made from this card's pictures, as a media item, or None when it has no sheet to
        make (fewer than two pictures, or one cannot be read). The file is named by what it is made from, so a
        picture changed gives a new one and the same pictures reuse the old."""
        if any(row['label'] == 'sheet' for row in card.get('images') or []):
            return None
        try:
            port = self.jobs.port
            rank = {label: i for i, label in enumerate(SHEET_ORDER)}
            rows = sorted(enumerate(card.get('images') or []), key=lambda r: (rank.get(r[1]['label'], len(rank)), r[0]))
            files = [port.input_path(row['media']['file']) for _, row in rows[:SHEET_MAX]
                     if isinstance(row['media'].get('file'), str)]
            if len(files) < 2:
                return None
            pictures = [path.read_bytes() for path in files]  # the same bytes are named and drawn, so a picture
            digest = hashlib.sha256(str(SHEET_VERSION).encode())  # changed meanwhile cannot sit under the old name
            for data in pictures:
                digest.update(hashlib.sha256(data).digest())
            relative = 'freevideo/agent/%s/sheet-%s.png' % (card['id'], digest.hexdigest()[:16])
            target = port.input_path(relative, create=True)
            if not _sheet_intact(target):
                temporary = target.with_name(target.name + '.' + uuid.uuid4().hex[:8] + '.tmp')
                try:
                    compose_sheet(pictures, temporary)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
                # A sheet is never deleted here: a queued shot or an earlier answer may still name it.
            return dict(file=relative)
        except Exception:  # A sheet is a help: without one the person carries the face alone.
            logging.getLogger(__name__).warning('no sheet for card %s', card.get('id'), exc_info=True)
            return None

    def used(self, identities, film):
        """Record that a film uses these cards; a film never leaves used_by once it is there."""
        with self.changed:
            for identity in identities:
                record = self.records.get(identity)
                if record is None or film in record['card']['used_by']:
                    continue
                record = deepcopy(record)
                card = record['card']
                if card['made_for'] is None and not card['used_by'] and card['origin'] != 'user':
                    card['made_for'] = film
                card['used_by'].append(film)
                self._save(record)

    # Reference pictures -------------------------------------------------------------------------------
    def generate(self, identity, value=None, *, by='builtin', ref=None, revision=None, client=None):
        """Submit reference pictures or clips for this card; the scheduler lands the pictures."""
        value = value or {}
        shape = schema.cast_schema('generate')
        schema.validate(shape, value)
        ref = ref if ref is not None else value.get('ref')
        value = schema.defaults(shape, {k: v for k, v in value.items() if k != 'ref'})
        expected = _revision(value.pop('revision', None), revision)
        payload = dict(value, revision=expected)
        with self.changed:
            digest, replay = self._receipt('cast:generate:' + str(identity), payload, ref, client)
            if replay is not None:
                return replay
            record = deepcopy(self._record(identity))
            card = record['card']
            if expected is not None and expected != card['revision']:
                raise self._stale(card)
            if card['generating']:
                job = card['generating']['job']
                raise CastError('already_generating', errors.ERRORS['already_generating'][2],
                                'GET /freevideo/agent/v1/jobs/%s; when it ends, get the card.' % job,
                                card=view(card), job=job)
            if value['face'] and card['kind'] != 'character':
                raise spec.SpecError([schema.diagnostic('/face', 'false for a setting card', True)])
            ready = value.get('source') != 'video' and getattr(self.jobs.port, 'image_ready', lambda: False)()
            source = value.get('source', 'image' if ready else 'video')
            # An explicit image request goes through the image service even when
            # its pack is absent: that failure owns a diagnostic report and a
            # durable job/card, just like a picture requested directly.
            seed = secrets.randbelow(spec.MAX_SEED + 1)
            token = uuid.uuid4().hex[:12]
            if source == 'image':
                from ..image_gen.cast_views import VIEWS, SETTING_VIEWS
                # A picture takes seconds, so a character always gets every view, the face close-up included.
                views = list(SETTING_VIEWS if card['kind'] == 'setting' else VIEWS)
                items = self._image_items(card, views[:1], seed)
            else:
                parts = [('turn', SCENE if card['kind'] == 'setting' else TURN, False)]
                if value['face']:
                    parts.append(('face', CLOSE_UP, True))
                items = [dict(kind='generate', prompt=reference_prompt(card, face=face), seed=seed,
                              label=('Cast · %s · %s' % (card['name'], part))[:120],
                              metadata=dict(cast_card=identity, cast_part=part), **shot)
                         for part, shot, face in parts]
            rows, _ = self.jobs.submit_request(dict(jobs=items), client='cast:' + identity,
                                               parent=dict(cast=identity, ref=token))
            jobs = [row['id'] for row in rows]
            card['generating'] = dict(job=jobs[0], face_job=jobs[1] if len(jobs) > 1 else None,
                                      jobs=list(jobs), source=source, replace=value['replace'], at=self._now(), by=by)
            card['_generating'] = dict(ref=token, seed=seed, kind=card['kind'], user_images=card.get('_user_images', 0))
            if source == 'image':
                card['_generating'].update(source=source, stage='front', views=views[1:],
                                            job_views={jobs[0]: views[0]}, name=card['name'],
                                            description=card['description'])
            result = dict(job=jobs[0], jobs=jobs, card=view(card))
            self._remember(record, ref, digest, result, client)
            self._save(record)
            return result

    def _image_items(self, card, views, seed, front=None, from_photo=False):
        from ..image_gen.cast_views import size, uses_front, view_prompt, photo_prompt
        # A picture somebody put on the card (the user, or an agent passing on the user's photo) is the person.
        photo = next((row['media'] for row in card.get('images', []) if not row.get('generated')), None)
        items = []
        for part in views:
            width, height = size(part)
            images = [front] if uses_front(part) else [photo] if part == 'front' and photo else []
            # When the front was made from a photo, the photo and not the text says what the person looks like.
            prompt = (photo_prompt() if part == 'front' and photo else
                      view_prompt(dict(card, photo=True) if from_photo else card, part))
            items.append(dict(kind='image', prompt=prompt, width=width, height=height, seed=seed,
                              images=images, label=('Cast · %s · %s' % (card['name'], part))[:120],
                              metadata=dict(cast_card=card['id'], cast_part=part)))
        return items

    def _image_views(self, identity, generating, hidden, rows):
        """Submit edits of the finished front once, while this tick owns the card's claim."""
        front = rows[generating['job']]
        if hidden['stage'] != 'front' or not hidden['views'] or not front or front['status'] != 'succeeded':
            return False
        with self.changed:
            record = self.records.get(identity)
            if record is None or record['card'].get('_generating') != hidden:
                return True
            record = deepcopy(record)
            card = record['card']
            basis = dict(id=identity, kind=hidden['kind'], name=hidden['name'], description=hidden['description'])
            items = self._image_items(basis, hidden['views'], hidden['seed'], dict(file=front['result']['file']),
                                      from_photo=bool(front['spec'].get('images')))
            jobs, _ = self.jobs.submit_request(dict(jobs=items, ref='cast-' + hidden['ref'] + '-views'),
                client='cast:' + identity, parent=dict(cast=identity, ref=hidden['ref']))
            card['generating']['jobs'].extend(row['id'] for row in jobs)
            card['_generating']['job_views'].update({row['id']: part for row, part in zip(jobs, hidden['views'])})
            card['_generating'].update(stage='views', views=[])
            self._save(record)
        return True

    def allows(self, parent):
        """May Jobs still start this generation? Only while its card waits for it."""
        with self.changed:
            record = self.records.get(parent.get('cast'))
            hidden = record['card'].get('_generating') if record else None
            return bool(hidden) and hidden['ref'] == parent.get('ref')

    def _finished(self):
        """Cards ready to submit more views or land, with their current jobs as they ended."""
        ready = []
        with self.changed:
            for identity, record in self.records.items():
                card = record['card']
                if not card.get('generating') or not card.get('_generating'):
                    continue
                rows = {job: self.jobs.jobs.get(job) for job in _generation_jobs(card['generating'])}
                if all(row is None or row['status'] in DONE for row in rows.values()):
                    ready.append((identity, deepcopy(card['generating']), deepcopy(card['_generating']),
                                  {job: deepcopy(row) for job, row in rows.items()}))
        return ready

    def pending(self):
        return bool(self._finished())

    def tick(self):
        """Land finished reference clips on their cards. Runs off Jobs' lock: frames take a moment to pick.

        Several callers tick at once (the scheduler, job submissions, resumes); each card is landed by one
        of them at a time. Never raises: a landing that cannot be saved is recorded on its card."""
        try:
            finished = self._finished()
        except Exception as error:  # noqa: BLE001 - submissions that tick must not fail because of a card
            logging.getLogger(__name__).warning('Reference pictures could not be checked (%s)', type(error).__name__)
            return
        for identity, generating, hidden, rows in finished:
            if not self._claim(identity, hidden):
                continue  # Landed already, or another tick is landing it now.
            try:
                if hidden.get('source') == 'image':
                    if not self._image_views(identity, generating, hidden, rows):
                        self._land_image(identity, generating, hidden, rows)
                else:
                    self._land(identity, generating, hidden, rows)
            except Exception as error:  # noqa: BLE001 - one card must never stop the others
                logging.getLogger(__name__).warning('Reference pictures could not be saved (%s)', type(error).__name__)
                self._fail(identity, hidden, dict(code='internal_error', job=generating['job'],
                                                  message='The reference pictures could not be saved.'))
            finally:
                with self.changed:
                    self.landing.discard(identity)

    def _claim(self, identity, hidden):
        """Take this card's landing if it still waits for this generation and nobody else is landing it."""
        with self.changed:
            record = self.records.get(identity)
            if (identity in self.landing or record is None
                    or record['card'].get('_generating') != hidden):
                return False
            self.landing.add(identity)
            return True

    def _land_image(self, identity, generating, hidden, rows):
        pictures, failure, staged = [], None, []
        try:
            for job in _generation_jobs(generating):
                row = rows[job]
                if row is None or row['status'] != 'succeeded':
                    code = (row.get('error') or {}).get('code') if row else 'not_found'
                    code = code or (row['status'] if row else 'not_found')
                    detail = errors.image_failure(code, (row.get('error') or {}).get('report_id') if row else None)
                    failure = failure or dict(code=code, job=job, error_zh=detail['reason']['zh'], error_en=detail['reason']['en'], message=(
                        'The generation was cancelled.' if row and row['status'] == 'cancelled'
                        else detail['reason']['en']), **detail)
                    continue
                folder = 'freevideo/agent/%s/ref-%s' % (identity, job)
                file = folder + '-0.png'
                temporary = self.jobs.port.input_path(folder + '-0.landing-' + uuid.uuid4().hex[:8] + '.png', create=True)
                for left in temporary.parent.glob('ref-%s-*.landing-*.png' % job):
                    if left != temporary:
                        left.unlink(missing_ok=True)
                try:
                    shutil.copyfile(self.jobs.port.input_path(row['result']['file']), temporary)
                except Exception:  # noqa: BLE001 - the other views still land
                    temporary.unlink(missing_ok=True)
                    failure = failure or dict(code='invalid_media', job=job,
                                              message='The reference picture could not be read.')
                    continue
                staged.append((temporary, file))
                part = hidden['job_views'][job]
                pictures.append(dict(media=dict(file=file), by=generating['by'], from_job=job,
                                     generated=True, label='other' if hidden['kind'] == 'setting' else part))
            self._settle(identity, hidden, pictures, failure, staged)
        finally:
            for out, _ in staged:
                out.unlink(missing_ok=True)

    def _land(self, identity, generating, hidden, rows):
        from . import media
        pictures, failure, staged = [], None, []
        try:
            for job in _generation_jobs(generating):
                row = rows[job]
                face = job == generating.get('face_job')
                if row is None or row['status'] != 'succeeded':
                    code = (row.get('error') or {}).get('code') if row else 'not_found'
                    failure = failure or dict(code=code or row['status'], job=job,
                        **errors.reasons((row.get('error') or dict(code=code or row['status'])) if row else dict(code=code)), message=(
                        'The generation was cancelled.' if row and row['status'] == 'cancelled'
                        else 'The reference clip did not finish.'))
                    continue
                folder = 'freevideo/agent/%s/ref-%s' % (identity, job)
                count = 1 if face else COUNT
                # Frames go to new names first and replace the card's files only once the card takes them,
                # so a file a card already names is never missing, even for a moment.
                mark = uuid.uuid4().hex[:8]
                temporary = [self.jobs.port.input_path('%s-%d.landing-%s.png' % (folder, n, mark), create=True)
                             for n in range(count)]
                for left in temporary[0].parent.glob('ref-%s-*.landing-*.png' % job):
                    if left not in temporary:
                        left.unlink(missing_ok=True)  # Left by a landing that stopped before the card was saved.
                try:
                    video = self.jobs.job_video(job)
                    with self.jobs.cpu:
                        picked = media.sharpest_frames(video, temporary)
                except Exception as error:  # noqa: BLE001 - the other clip's frames still land
                    for out in temporary:
                        out.unlink(missing_ok=True)
                    if _unreadable(error):
                        failure = failure or dict(code='clip_unreadable', job=job,
                                                  message='The reference clip could not be read.')
                    else:
                        logging.getLogger(__name__).warning('Reference frames could not be picked (%s)',
                                                            type(error).__name__)
                        failure = failure or dict(code='internal_error', job=job,
                                                  message='The reference pictures could not be saved.')
                    continue
                setting = hidden.get('kind') == 'setting'
                for n, row_frame in enumerate(picked):
                    if row_frame is None:
                        temporary[n].unlink(missing_ok=True)
                        continue
                    file = '%s-%d.png' % (folder, n)
                    staged.append((temporary[n], file))
                    entry = dict(media=dict(file=file), by=generating['by'], from_job=job, generated=True)
                    if face:
                        entry['label'] = 'face'
                    elif setting:
                        entry['label'] = 'other'
                    else:
                        entry.update(label=turn_label((n + .5) / count), label_guess=True)
                    pictures.append(entry)
                if not any(picked):
                    failure = failure or dict(code='no_sharp_frame', job=job,
                                              message='The reference clip had no sharp frame to keep.')
            self._settle(identity, hidden, pictures, failure, staged)
        finally:
            for out, _ in staged:
                out.unlink(missing_ok=True)  # Not taken by the card (published ones are gone already).

    def _settle(self, identity, hidden, pictures, failure, staged=()):
        """Write the pictures into the card once; a later generation or a deletion makes this one stale."""
        with self.changed:
            record = self.records.get(identity)
            if record is None or (record['card'].get('_generating') or {}).get('ref') != hidden['ref']:
                return
            record = deepcopy(record)
            card = record['card']
            generating = card['generating']
            by = generating['by']
            protected = by != 'user' and card['edited_by_user']['images']
            if card.get('_user_images', 0) != hidden['user_images'] or protected:
                # The user changed the pictures meanwhile, or owns them: offer, never add or overwrite.
                images, spare = card['images'], pictures
            elif generating['replace']:
                kept = [r for r in card['images'] if r.get('by') == 'user' and not r.get('generated')]
                room = MAX_IMAGES - len(kept)
                images, spare = kept + pictures[:room], pictures[room:]
            else:
                room = MAX_IMAGES - len(card['images'])
                images, spare = card['images'] + pictures[:room], pictures[room:]
            files = {r['media']['file'] for r in images}
            candidates, seen = [], set()
            for row in spare + card['candidates']:
                file = row['media']['file']
                if file not in files and file not in seen:
                    seen.add(file)
                    candidates.append(row)
            for out, file in staged:
                os.replace(out, self.jobs.port.input_path(file, create=True))
            card.update(images=images, candidates=candidates[:MAX_CANDIDATES], generating=None,
                        generate_error=failure, revision=card['revision'] + 1, updated_at=self._now(), by=by,
                        generated=dict(job=generating['job'], face_job=generating.get('face_job'),
                                       seed=hidden['seed'], prompt_version=PROMPT_VERSION))
            if hidden.get('source') == 'image':
                card['generated']['source'] = 'image'
            card.pop('_generating', None)
            self._save(record)

    def _fail(self, identity, hidden, failure):
        """Record a landing that failed on its card, keeping the pictures it has."""
        try:
            self._settle(identity, hidden, [], failure)
            return
        except Exception as error:  # noqa: BLE001 - the card file cannot be written now
            logging.getLogger(__name__).warning('A cast card could not be saved (%s)', type(error).__name__)
        # Shown until the next restart, so ticks do not land it again and again; the saved card still waits
        # for this generation and lands it again after a restart.
        with self.changed:
            record = self.records.get(identity)
            if record is None or (record['card'].get('_generating') or {}).get('ref') != hidden['ref']:
                return
            record = deepcopy(record)
            card = record['card']
            card.update(generating=None, generate_error=failure, revision=card['revision'] + 1,
                        updated_at=self._now())
            card.pop('_generating', None)
            self.records[identity] = record
            self.changed.notify_all()


def _sheet_intact(path):
    """Whether this is a whole sheet: a file left half written or cut short is made again. A file already checked
    and unchanged is not decoded twice."""
    try:
        status = path.stat()
        key = (str(path), status.st_size, status.st_mtime_ns)
        if key in _SHEETS_SEEN:
            return True
        from PIL import Image
        with Image.open(path) as opened:
            if opened.size != SHEET_SIZE:
                return False
            opened.load()
    except Exception:
        return False
    if len(_SHEETS_SEEN) > 256:
        _SHEETS_SEEN.clear()
    _SHEETS_SEEN.add(key)
    return True


def compose_sheet(pictures, out):
    """Write one PNG of these pictures (two to six, as file bytes or paths): always three columns by two rows of
    equal cells, a light grey ground (cells with no picture stay empty), each picture scaled whole to fit its cell
    and centred, nothing cropped, no text."""
    import io
    from PIL import Image, ImageOps
    (width, height), gap = SHEET_CELL, SHEET_GAP
    sheet = Image.new('RGB', SHEET_SIZE, SHEET_GROUND)
    for index, source in enumerate(pictures[:SHEET_MAX]):
        with Image.open(io.BytesIO(source) if isinstance(source, bytes) else source) as opened:
            picture = ImageOps.exif_transpose(opened)
            if picture.mode in ('RGBA', 'LA', 'P'):
                flat = Image.new('RGB', picture.size, SHEET_GROUND)
                picture = picture.convert('RGBA')
                flat.paste(picture, mask=picture.getchannel('A'))
                picture = flat
            picture = ImageOps.contain(picture.convert('RGB'), (width, height), Image.LANCZOS)
        row, column = divmod(index, SHEET_COLUMNS)
        sheet.paste(picture, (gap + column * (width + gap) + (width - picture.width) // 2,
                              gap + row * (height + gap) + (height - picture.height) // 2))
    sheet.save(out, format='PNG')


def continued_pictures(cards):
    """The pictures a shot that continues the previous one carries for these cards (the current cards, from
    `Cast.snapshot`), at most three: a single person carries the sheet then the face close-up; two or three people
    carry one each, the face (else the front view, the three-quarter view, the first other picture), no sheet.
    Each is a media item."""
    from . import drama
    cards = list(cards)[:spec.CONTINUED_PICTURES]
    return [media for card in cards
            for media in drama.person_pictures(card, alone=len(cards) == 1, continues=True)][:spec.CONTINUED_PICTURES]


def _unreadable(error):
    """A clip that is gone or cannot be decoded, rather than a fault of ours."""
    if isinstance(error, (JobError, ValueError, OSError)):
        return True
    try:
        import av
    except ImportError:
        return False
    return isinstance(error, av.error.FFmpegError)


def _generation_jobs(generating):
    if not generating:
        return []
    return generating.get('jobs') or [generating['job']] + ([generating['face_job']] if generating.get('face_job') else [])
