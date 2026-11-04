"""Local editor records, imported previews and bounded CPU media inspection.

All retained data lives under output/FreeVideo/.editor. Imports never become
creations, and their names, contents and decoding errors are never logged.
"""
import asyncio
import copy
import errno
import hashlib
import json
import logging
import math
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlencode, urlsplit

from .origin_scheme import effective_scheme
from .comfy_library import _ID, _plain, _video
from .system import windows
from .editor_frames import frame_not_after, last_frame_end
from .editor_limits import PROJECT_BYTES
from .editor_speed import validate_clip, register_reverse
from .editor_transport import marker_fields
from .editor_audio import STEM_TRACKS, clip_fields, track_fields

FPS = 24
MAX_IMPORT_BYTES = 8 * 1024**3
BODY_BYTES = 8 * 1024 * 1024
_PROJECT = re.compile(r'e-[0-9a-f]{32}')
_IMPORT = re.compile(r'[A-Za-z0-9_-]{1,80}')
_CLIP = re.compile(r'c[1-9][0-9]{0,8}')
_TOKEN = re.compile(r'[A-Za-z0-9_-]{1,128}')
_REASON = re.compile(r'[a-z_]{1,80}')
_REF = re.compile(r'[A-Za-z0-9_-]{1,128}')
# A note is one line: the page refuses the same characters (editor_model.js NOTE_BREAK) and the same ends.
_NOTE_BREAK = re.compile('[\x00-\x1f\x7f-\x9f\u2028\u2029]')
_STATE = threading.RLock()
_SESSIONS = {}  # (output root, project id) -> {session token: monotonic expiry}
_SESSION_SECONDS = 60
_TRANSCODING = threading.Lock()
_FAILED_IMPORTS = {}  # terminal statuses awaiting writable storage
_HEADERS = {'Cache-Control': 'no-store'}
_LOG = logging.getLogger(__name__)


def _disk_full(error):
    # The page names a full disk; other write errors stay a plain retry.
    return {'reason': 'disk_full'} if getattr(error, 'errno', None) == errno.ENOSPC else {}


class Conflict(ValueError):
    def __init__(self, revision, identity=None):
        super().__init__('Editor project has changed')
        self.revision = revision
        self.identity = identity


class MissingProject(FileNotFoundError):
    """An existing project lookup failed, rather than a later disk write."""


class TooLarge(ValueError):
    pass


class DeleteRefused(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _whole(value, low=0, high=2**53 - 1):
    return type(value) in (int, float) and low <= value <= high and value == int(value)


def _text(value, limit):
    return isinstance(value, str) and len(value) <= limit and not any(0xd800 <= ord(c) <= 0xdfff for c in value)


def _made(raw, out):
    """Copy the order a version was made in and the description it was made from."""
    if 'take' in raw:
        if not _whole(raw['take'], 1, 10**6):
            raise ValueError('Invalid version number')
        out['take'] = int(raw['take'])
    if 'made_text' in raw:
        if not _text(raw['made_text'], 8000):
            raise ValueError('Invalid version description')
        out['made_text'] = raw['made_text']


def _work(value):
    return isinstance(value, str) and value.isascii() and _ID.fullmatch(value) is not None


def _relative(value):
    # Check before resolving: resolve() alone would conceal an internal link.
    if (not isinstance(value, str) or len(value) > 1024 or not value
            or any(ord(c) < 32 for c in value) or '\\' in value or ':' in value
            or value.startswith('/') or any(p in ('', '.', '..') for p in value.split('/'))
            or PureWindowsPath(value).is_absolute()):
        raise ValueError('Invalid media path')
    if PurePosixPath(value).suffix.lower() != '.mp4' or '.editor' in value.split('/'):
        raise ValueError('Invalid generated video')
    return value


def _source(value):
    if not isinstance(value, dict) or value.get('kind') not in ('work', 'generated', 'import'):
        raise ValueError('Invalid source')
    kind, identity = value['kind'], value.get('id')
    if kind == 'work' and not _work(identity):
        raise ValueError('Invalid creation id')
    if kind == 'generated':
        _relative(identity)
    if kind == 'import' and (not isinstance(identity, str) or not _IMPORT.fullmatch(identity)):
        raise ValueError('Invalid import id')
    return dict(kind=kind, id=identity)


def source_key(value):
    value = _source(value)
    return value['kind'] + ':' + value['id']


def _key(key):
    if not isinstance(key, str) or ':' not in key:
        raise ValueError('Invalid source key')
    kind, identity = key.split(':', 1)
    return _source(dict(kind=kind, id=identity))


from .editor_text import has_text_data, validate_text_project


def validate(record):
    if has_text_data(record):
        return validate_text_project(record, validate)
    """Bound the wire format; editing and generation policy belongs to the model."""
    from .editor_picture import picture_fit
    from .editor_picture_fields import picture_fields
    if not isinstance(record, dict) or not _whole(record.get('version'), 1, 1):
        raise ValueError('Invalid editor version')
    if 'id' not in record:
        raise ValueError('Project id missing')
    if record.get('id') is not None and (not isinstance(record['id'], str) or not _PROJECT.fullmatch(record['id'])):
        raise ValueError('Invalid project id')
    if not _whole(record.get('revision')) or not _whole(record.get('serial'), 0, 999999999):
        raise ValueError('Invalid revision or serial')
    if record.get('id') is None and record['revision'] != 0:
        raise ValueError('Invalid new project revision')
    if not _whole(record.get('fps'), FPS, FPS):
        raise ValueError('Invalid frame rate')
    if any(not _whole(record.get(side), 2, 8192) or record[side] % 2 for side in ('width', 'height')):
        raise ValueError('Invalid picture size')
    if not _text(record.get('name'), 8000):
        raise ValueError('Invalid project name')
    settings = record.get('settings')
    if settings is not None and not isinstance(settings, dict):
        raise ValueError('Invalid settings')
    # Settings are opaque JSON, preserved without interpreting the panel policy.
    def bounded(value, depth=0):
        if depth > 32:
            raise ValueError('Settings are too deep')
        if isinstance(value, dict):
            if any(not isinstance(k, str) for k in value):
                raise ValueError('Invalid settings')
            for child in value.values():
                bounded(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                bounded(child, depth + 1)
        elif value is not None and type(value) not in (str, int, float, bool):
            raise ValueError('Invalid settings')
        elif type(value) is float and not math.isfinite(value):
            raise ValueError('Invalid settings')
    bounded(settings)
    if not _text(json.dumps(settings, ensure_ascii=False, allow_nan=False, separators=(',', ':')), 1024 * 1024):
        raise ValueError('Settings are too large')
    raw_tracks = record.get('tracks')
    # v1 and a1, then an optional second sound track (a2), the recording sub-track under a1 (a3) and, only together with both, the three separated-sound tracks (a4-a6), in that order.
    if not isinstance(raw_tracks, list) or len(raw_tracks) not in (2, 3, 4, 7):
        raise ValueError('Invalid tracks')
    later = [raw.get('id') if isinstance(raw, dict) else None for raw in raw_tracks[2:]]
    if later not in ([], ['a2'], ['a3'], ['a2', 'a3']):
        raise ValueError('Invalid tracks')
    expected = [('v1', 'video'), ('a1', 'audio')] + [(track_id, 'audio') for track_id in later]
    tracks, seen = [], {}
    for raw_track, (track_id, kind) in zip(raw_tracks, expected):
        if (not isinstance(raw_track, dict) or raw_track.get('id') != track_id or raw_track.get('kind') != kind
                or not isinstance(raw_track.get('clips'), list) or len(raw_track['clips']) > 500):
            raise ValueError('Invalid track')
        if (raw_track.get('stem') != (STEM_TRACKS[len(tracks) - 4] if len(tracks) >= 4 else None)):
            raise ValueError('Invalid track')
        clips, end = [], 0
        for raw in raw_track['clips']:
            if not isinstance(raw, dict):
                raise ValueError('Invalid clip')
            if 'text_kind' in raw or 'text_style' in raw:
                raise ValueError('Text fields require a text track')
            identity = raw.get('id')
            if (not isinstance(identity, str) or not _CLIP.fullmatch(identity) or identity in seen
                    or int(identity[1:]) > record['serial']):
                raise ValueError('Invalid clip id or count')
            for key, low in (('start', 0), ('frames', 1), ('in', 0), ('length', 1)):
                if not _whole(raw.get(key), low, 10**7):
                    raise ValueError('Invalid clip range')
            speed_fields = validate_clip(raw)
            if raw['start'] < end:
                raise ValueError('Invalid clip overlap or range')
            end = raw['start'] + raw['frames']
            if end > 10**7:
                raise ValueError('Invalid clip end')
            if 'source' not in raw:
                raise ValueError('Source missing')
            source = _source(raw['source']) if raw.get('source') is not None else None
            if source is None and (raw['in'] != 0 or raw['length'] != raw['frames']):
                raise ValueError('Invalid planned clip range')
            pending = raw.get('pending')
            if pending not in (None, 'draft', 'queued', 'running', 'failed', 'stopped') or source is None and pending is None:
                raise ValueError('Invalid clip state')
            if raw.get('reason') is not None and (not isinstance(raw['reason'], str) or not _REASON.fullmatch(raw['reason'])):
                raise ValueError('Invalid failure reason')
            if raw.get('reason') is not None and pending != 'failed':
                raise ValueError('Invalid failure state')
            for key in ('part', 'job'):
                if raw.get(key) is not None and (not isinstance(raw[key], str) or not (_CLIP if key == 'part' else _TOKEN).fullmatch(raw[key])):
                    raise ValueError('Invalid clip ' + key)
            if not _text(raw.get('text', ''), 8000) or type(raw.get('extend')) is not bool:
                raise ValueError('Invalid clip description or extension')
            link = raw.get('link')
            if link is not None and (not isinstance(link, str) or not _CLIP.fullmatch(link) or link == identity):
                raise ValueError('Invalid clip link')
            versions = raw.get('versions')
            if not isinstance(versions, list) or len(versions) > 50:
                raise ValueError('Invalid clip versions')
            history = []
            for version in versions:
                previous = _source(version)
                if not _whole(version.get('in'), 0, 10**7) or not _whole(version.get('length'), version['in'] + 1, 10**7):
                    raise ValueError('Invalid version range')
                previous.update({'in': int(version['in']), 'length': int(version['length'])})
                if 'splice' in version:
                    previous['splice'] = _splice(version['splice'])
                _made(version, previous)
                if kind == 'video':
                    previous.update(picture_fields(version))
                    if picture_fit(version.get('fit', 'cover')) != 'cover':
                        previous['fit'] = 'contain'
                elif 'fit' in version:
                    raise ValueError('Audio picture fit is invalid')
                history.append(previous)
            if len({(v['kind'], v['id']) for v in history}) != len(history):
                raise ValueError('Duplicate versions')
            clip = dict(id=identity, start=int(raw['start']), frames=int(raw['frames']), length=int(raw['length']),
                        source=source, link=link, text=raw.get('text', ''), part=raw.get('part'), pending=pending,
                        reason=raw.get('reason'), job=raw.get('job'), extend=raw.get('extend', source is not None),
                        versions=history, **{'in': int(raw['in'])})
            clip.update(speed_fields)
            clip.update(clip_fields(raw, kind))
            if kind == 'video':
                clip.update(picture_fields(raw))
                if picture_fit(raw.get('fit', 'cover')) != 'cover':
                    clip['fit'] = 'contain'
            elif 'fit' in raw:
                raise ValueError('Audio picture fit is invalid')
            if kind == 'video' and 'volume' in raw:
                raise ValueError('Video volume is invalid')
            if kind == 'audio':
                volume = raw.get('volume')
                if type(volume) not in (int, float) or not 0 <= volume <= 2:
                    raise ValueError('Invalid volume')
                clip['volume'] = volume
            if 'splice' in raw:
                if source is None:
                    raise ValueError('Splice requires source')
                clip['splice'] = _splice(raw['splice'])
            if 'follows' in raw:
                # The part this one continues; a range drawn on its own has none.
                if kind != 'video' or not isinstance(raw['follows'], str) or not _CLIP.fullmatch(raw['follows']) or raw['follows'] == identity:
                    raise ValueError('Invalid clip continuation')
                clip['follows'] = raw['follows']
            if 'target_frames' in raw:
                if not _whole(raw['target_frames'], 1, 10**7):
                    raise ValueError('Invalid target frame count')
                clip['target_frames'] = int(raw['target_frames'])
            # Who last wrote the description, and an outside reference (a shot id) for callers.
            if 'text_by' in raw:
                if raw['text_by'] not in ('user', 'agent'):
                    raise ValueError('Invalid description author')
                clip['text_by'] = raw['text_by']
            if 'ref' in raw:
                if not isinstance(raw['ref'], str) or not _REF.fullmatch(raw['ref']):
                    raise ValueError('Invalid block reference')
                clip['ref'] = raw['ref']
            if 'note' in raw:
                note = raw['note']
                if (not _text(note, 120) or not note or note != note.strip() or '\ufeff' in (note[0], note[-1])
                        or _NOTE_BREAK.search(note)):
                    raise ValueError('Invalid block note')
                clip['note'] = note
            if source is None and ('take' in raw or 'made_text' in raw):
                raise ValueError('Invalid clip versions')
            _made(raw, clip)
            clip.update(clip_fields(raw, kind))
            clips.append(clip)
            seen[identity] = (kind, clip)
        tracks.append(dict(id=track_id, kind=kind, clips=clips, **track_fields(raw_track)))
    for kind, clip in seen.values():
        if clip['link'] is not None:
            other = seen.get(clip['link'])
            if (other is None or kind == other[0] or other[1]['link'] != clip['id']
                    or any(clip[k] != other[1][k] for k in ('start', 'frames', 'in'))):
                raise ValueError('Invalid linked pair')
    result = dict(version=1, id=record.get('id'), revision=int(record['revision']), name=record['name'], fps=FPS,
                  width=int(record['width']), height=int(record['height']), settings=copy.deepcopy(settings),
                  serial=int(record['serial']), tracks=tracks)
    size_mode = record.get('size_mode', 'first')
    if size_mode not in ('first', 'fixed'):
        raise ValueError('Invalid size mode')
    if size_mode == 'fixed':
        result['size_mode'] = size_mode
    if 'keep_empty' in record:
        if type(record['keep_empty']) is not bool:
            raise ValueError('Invalid empty edit retention')
        if record['keep_empty']:
            result['keep_empty'] = True
    result.update(marker_fields(record))
    if 'replacements' in record:
        replacements = record['replacements']
        if not isinstance(replacements, list) or not 1 <= len(replacements) <= 500:
            raise ValueError('Invalid replacements')
        transactions, parts = [], set()
        for raw in replacements:
            if (not isinstance(raw, dict) or not isinstance(raw.get('part'), str) or not _CLIP.fullmatch(raw['part'])
                    or raw['part'] in parts or type(raw.get('rolledBack')) is not bool):
                raise ValueError('Invalid replacement identity')
            parts.add(raw['part'])
            def read(rows):
                if (not isinstance(rows, list) or len(rows) != 2
                        or any(not isinstance(t, dict) or not t.get('clips') for t in rows)):
                    raise ValueError('Invalid replacement tracks')
                return validate(dict(result, tracks=rows))['tracks']
            originals, clips = read(raw.get('originals')), read(raw.get('clips'))
            if any(not c['source'] or not c['link'] or c['source']['kind'] not in ('work', 'generated')
                   for t in originals for c in t['clips']):
                raise ValueError('Invalid replacement originals')
            if any(c['part'] != raw['part'] or not c['link'] for t in clips for c in t['clips']):
                raise ValueError('Invalid replacement attempt')
            def bounds(rows):
                return [(t['clips'][0]['start'], t['clips'][-1]['start'] + t['clips'][-1]['frames']) for t in rows]
            if bounds(originals) != bounds(clips):
                raise ValueError('Invalid replacement bounds')
            transactions.append(dict(part=raw['part'], originals=originals, clips=clips, rolledBack=raw['rolledBack']))
        result['replacements'] = transactions
    return result


def _splice(value):
    if not isinstance(value, dict) or not _whole(value.get('frames'), 0, 10**7) or not _whole(value.get('audio_samples')):
        raise ValueError('Invalid splice')
    return {k: int(value[k]) for k in ('frames', 'audio_samples')}


def _safe(output_directory, relative):
    root = Path(output_directory).resolve()
    relative = PurePosixPath(relative)
    if relative.is_absolute() or any(p in ('.', '..') for p in relative.parts):
        raise ValueError('Invalid retained path')
    path = root
    for part in relative.parts:
        path = path / part
        if os.path.lexists(path) and not _plain(path):
            raise ValueError('Retained path contains a link')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Retained path is outside output')
    return path


def _editor_path(root, relative):
    return _safe(root, 'FreeVideo/.editor/' + relative)


def _project_path(root, identity):
    if not isinstance(identity, str) or not _PROJECT.fullmatch(identity):
        raise ValueError('Invalid project id')
    return _editor_path(root, 'projects/' + identity + '/project.json')


def _import_path(root, identity, filename='import.json'):
    if not isinstance(identity, str) or not _IMPORT.fullmatch(identity):
        raise ValueError('Invalid import id')
    return _editor_path(root, 'imports/' + identity + '/' + filename)


def _read_json(path, limit=BODY_BYTES):
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Retained record is too large')
    return json.loads(data)


def _read_project(root, identity):
    record = validate(_read_json(_project_path(root, identity), PROJECT_BYTES))
    if record['id'] != identity:
        raise ValueError('Project id does not match its folder')
    return record


def _write(path, record, *, limit=None):
    data = (json.dumps(record, ensure_ascii=True, allow_nan=False) + '\n').encode('utf-8')
    if limit is not None and len(data) > limit:
        raise ValueError('Project is too large')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb',
                prefix=path.name + '.', suffix='.tmp', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if not windows() or attempt == 7:
                    raise
                time.sleep(.1 * (attempt + 1))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save(output_directory, record, create=None, operation=None):
    from .comfy_library import _RELEASING
    record = validate(record)
    if create is not None and (not isinstance(create, str) or not re.fullmatch(r'[0-9a-f]{32}', create)):
        raise ValueError('Invalid create operation')
    if operation is not None and (not isinstance(operation, str) or not re.fullmatch(r'[0-9a-f]{32}', operation)):
        raise ValueError('Invalid save operation')
    # Bind an operation to the exact validated request, including its base revision.
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    fresh = record['id'] is None
    if fresh:
        record['id'] = 'e-' + uuid.uuid4().hex
    # Deletion cannot pass its reference check between lookup and publication.
    with _RELEASING, _STATE:
        if fresh and create is not None:
            folder = _editor_path(output_directory, 'projects')
            for existing in folder.iterdir() if folder.is_dir() else ():
                if not _PROJECT.fullmatch(existing.name):
                    continue
                receipt = _editor_path(output_directory, 'projects/' + existing.name + '/create.json')
                if not receipt.is_file() or _read_json(receipt).get('create') != create:
                    continue
                stored = _read_project(output_directory, existing.name)
                content = lambda r: {**r, 'id': None, 'revision': 0}
                if operation is None and content(stored) == content(record):
                    return stored
                record['id'] = stored['id']
                fresh = False
                break
        path = _project_path(output_directory, record['id'])
        if fresh:
            path.parent.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=path.parent.parent))
            record['revision'] = 1
        else:
            try:
                stored = _read_project(output_directory, record['id'])
            except FileNotFoundError as error:
                raise MissingProject('Saved project unavailable') from error
            receipt = _read_json(path, PROJECT_BYTES).get('_save', {})
            if operation is not None and receipt.get('operation') == operation:
                if receipt.get('digest') != digest:
                    raise ValueError('Save operation reused with different content')
                return stored
            if stored['revision'] != record['revision']:
                raise Conflict(stored['revision'], stored['id'])
            record['revision'] += 1
        # The last receipt and the project commit in one atomic replace. One receipt
        # suffices: clients resolve an uncertain write before sending the next one.
        # A different writer replaces it, so a stale replay still conflicts.
        retained = validate(record)
        if operation is not None:
            retained['_save'] = dict(operation=operation, digest=digest)
        try:
            if fresh:
                _write(staging / 'project.json', retained, limit=PROJECT_BYTES)
                if create is not None:
                    _write(staging / 'create.json', dict(create=create))
                _project_path(output_directory, record['id'])
                os.rename(staging, path.parent)
            else:
                _write(path, retained, limit=PROJECT_BYTES)
        finally:
            if fresh and staging.exists():
                shutil.rmtree(staging)
    return record


def _projects(root, *, strict=False, unreadable=None):
    folder = _editor_path(root, 'projects')
    if not folder.exists():
        return []
    records = []
    for path in folder.iterdir():
        if not _PROJECT.fullmatch(path.name):
            continue
        try:
            if not _project_path(root, path.name).exists():
                continue  # No committed record means no references.
            record = _read_project(root, path.name)
            info = _project_path(root, path.name).stat()
            records.append((info.st_mtime_ns, path.name, record, info))
        except FileNotFoundError:
            if strict:
                raise ValueError('Editor references unavailable') from None
        except (OSError, ValueError, TypeError, RecursionError):
            if strict:
                raise ValueError('Editor references unavailable') from None
            if unreadable is not None:
                unreadable.append(path.name)
    return sorted(records, key=lambda row: row[:2], reverse=True)


def references(record):
    tracks = [*record['tracks'], *(t for r in record.get('replacements', []) for t in [*r['originals'], *r['clips']])]
    return list(dict.fromkeys(source_key(source) for track in tracks for clip in track['clips']
                             for source in [clip['source'], *clip['versions']] if source is not None))


def reference_users(root, work):
    """Compare actual file identities, including aliases and historical sources."""
    folder = _video(root, work).parent
    def identity(path):
        info = path.stat()
        inode = getattr(info, 'st_ino', 0)
        return ('inode', info.st_dev, inode) if inode else ('path', os.path.normcase(os.path.realpath(path)))
    files = {identity(_safe(root, path.relative_to(Path(root).resolve()).as_posix()))
             for path in folder.rglob('*') if path.is_file()}
    users = []
    with _STATE:
        for _, name, record, _ in _projects(root, strict=True):
            for key in references(record):
                kind, value = key.split(':', 1)
                try:
                    paths = [_video(root, value)] if kind == 'work' else (
                        [_safe(root, value)] if kind == 'generated' else
                        [_import_path(root, value, file) for file in ('source.bin', 'preview.mp4', 'preview.m4a')])
                    if any(path.is_file() and identity(path) in files for path in paths):
                        users.append(record['name'] or name)
                        break
                except FileNotFoundError:
                    continue
    return users


def cleanup_projects(root, now=None):
    """Remove old uncommitted folders; corrupt committed records stay protected."""
    now = time.time() if now is None else now
    folder = _editor_path(root, 'projects')
    if not folder.is_dir():
        return
    with _STATE:
        for path in folder.iterdir():
            if not (_PROJECT.fullmatch(path.name) or path.name.startswith('.staging-')):
                continue
            try:
                checked = _safe(root, path.relative_to(Path(root).resolve()).as_posix())
                if checked.is_dir() and not (checked / 'project.json').exists() and now - checked.stat().st_mtime > 3600:
                    shutil.rmtree(checked)
            except (OSError, ValueError):
                _LOG.warning('Editor uncommitted directory cleanup failed; continuing.')



def duration(record):
    return max((clip['start'] + clip['frames'] for track in record['tracks'] for clip in track['clips']), default=0)


def list_projects(root, unreadable=None):
    """Readable projects, newest first; damaged records are named in the optional `unreadable` list."""
    rows = []
    with _STATE:
        records = _projects(root, unreadable=unreadable)
    for _, identity, record, info in records:
        first = next(iter(record['tracks'][0]['clips']), None)
        cover = sources(root, [source_key(first['source'])]) if first and first['source'] else {}
        rows.append(dict(id=identity, name=record['name'], updated_at=datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                         frames=duration(record), fps=FPS, width=record['width'], height=record['height'],
                         thumbnail=next(iter(cover.values()), {}).get('thumbnail'),
                         **({'keep_empty': True} if record.get('keep_empty') else {})))
    return rows


def load(root, identity):
    with _STATE:
        record = _read_project(root, identity)
    return dict(project=record, sources=sources(root, references(record)))


def project_session(root, identity, session, release=False):
    """A tab renews its short lease while the edit is open; crashed tabs expire."""
    with _STATE:
        path = _project_path(root, identity)
        if not isinstance(session, str) or not re.fullmatch(r'[0-9a-f]{32}', session):
            raise ValueError('Invalid editor session')
        key = (str(Path(root).resolve()), identity)
        now = time.monotonic()
        for old, owners in list(_SESSIONS.items()):
            live = {token: until for token, until in owners.items() if until > now}
            if live:
                _SESSIONS[old] = live
            else:
                _SESSIONS.pop(old, None)
        if release:
            _SESSIONS.get(key, {}).pop(session, None)
        else:
            if not path.is_file():
                raise MissingProject('Saved project unavailable')
            _SESSIONS.setdefault(key, {})[session] = now + _SESSION_SECONDS


def delete_project(root, identity, session=None):
    """Delete only this edit's retained directory, never its source media."""
    with _STATE:
        path = _project_path(root, identity)
        if session is not None and (not isinstance(session, str) or not re.fullmatch(r'[0-9a-f]{32}', session)):
            raise ValueError('Invalid editor session')
        if not path.parent.exists():
            return
        key = (str(Path(root).resolve()), identity)
        if any(token != session and until > time.monotonic() for token, until in _SESSIONS.get(key, {}).items()):
            raise DeleteRefused('project_in_use')
        if path.exists():
            record = _read_project(root, identity)
            tracks = [*record['tracks'], *(track for tx in record.get('replacements', []) for track in [*tx['originals'], *tx['clips']])]
            clips = [clip for track in tracks for clip in track['clips']]
            from .comfy_chain import RUNNER
            for clip in clips:
                active = clip.get('pending') in ('queued', 'running')
                if RUNNER is not None and clip.get('job'):
                    try:
                        active = RUNNER.get(clip['job'])['state'] in ('queued', 'running', 'cancelling')
                    except KeyError:
                        pass
                if active:
                    raise DeleteRefused('generation_active')
        # _project_path rejects links in the owned path. rmtree does not follow
        # links inside it, and the state lock excludes publication by save().
        shutil.rmtree(path.parent)
        _SESSIONS.pop(key, None)


def load_by_work(root, work):
    if not _work(work):
        raise ValueError('Invalid creation id')
    with _STATE:
        for _, identity, record, _ in _projects(root):
            clips = record['tracks'][0]['clips']
            if clips and clips[0]['start'] == 0 and clips[0]['source'] == dict(kind='work', id=work):
                return load(root, identity)
    raise FileNotFoundError('Saved project unavailable')


def media_path(root, key):
    source = _key(key)
    if source['kind'] == 'work':
        return _safe(root, 'FreeVideo/' + source['id'] + '/video.mp4')
    if source['kind'] == 'generated':
        return _safe(root, source['id'])
    metadata = _read_import(root, source['id'])
    if metadata['state'] != 'ready':
        raise FileNotFoundError('Import preview unavailable')
    return _import_path(root, source['id'], 'preview.mp4' if metadata['kind'] == 'video' else 'preview.m4a')


def probe(path):
    import av
    from .media_encoding import clip_seconds
    from .editor_frames import display_frame
    with av.logging.Capture(local=True), av.open(str(path)) as container:
        video = next(iter(container.streams.video), None)
        audio = next(iter(container.streams.audio), None)
        if video is None and audio is None:
            raise ValueError('Media has no playable streams')
        stream = video if video is not None else audio
        seconds = clip_seconds(container, stream)
        fps = float(video.average_rate or video.guessed_rate or FPS) if video is not None else FPS
        frames = video.frames if video is not None else 0
        if not frames:
            if seconds is None or not math.isfinite(seconds) or seconds <= 0:
                raise ValueError('Media duration unavailable')
            frames = max(1, math.ceil(seconds * fps - 1e-6))
        width = height = 0
        if video is not None:
            video.thread_count = 1
            picture = display_frame(next(container.decode(video)))
            width, height = (max(2, side // 2 * 2) for side in (picture.width, picture.height))
        return dict(frames=int(frames), fps=fps, width=width,
                    height=height, has_video=video is not None,
                    has_audio=audio is not None, sample_rate=audio.rate if audio is not None else None)


def _view(path):
    relative = PurePosixPath(path)
    return '/view?' + urlencode(dict(filename=relative.name, subfolder=str(relative.parent), type='output'))


def sources(root, keys):
    result = {}
    for key in dict.fromkeys(keys):
        source = _key(key)
        relative = ('FreeVideo/' + source['id'] + '/video.mp4' if source['kind'] == 'work'
                    else source['id'] if source['kind'] == 'generated' else None)
        info = dict(video=None, audio=None, frames=0, fps=FPS, width=0, height=0, has_video=False,
                    has_audio=False, thumbnail=None, missing=True, path=relative, sample_rate=None)
        try:
            path = media_path(root, key)
            if source['kind'] in ('work', 'generated'):
                relative = path.relative_to(Path(root).resolve()).as_posix()
                info['path'] = relative
            elif path.suffix == '.mp4':
                # An imported video is continued from its upright 24 fps copy; it is still shown through the import.
                info['path'] = path.relative_to(Path(root).resolve()).as_posix()
            if path.stat().st_size == 0:
                raise ValueError('Empty media')
            if source['kind'] == 'work':
                report = _safe(root, Path(relative).with_suffix('.request.json').as_posix())
                if _read_json(report, 4 * 1024**2).get('success') is not True:
                    raise ValueError('Creation is incomplete')
            media = probe(path)
            if source['kind'] == 'work':
                text = _work_text(root, relative)
                if text:
                    media['text'] = text
            if source['kind'] in ('work', 'generated') and _is_preview(root, relative):
                media['preview'] = True
            if source['kind'] == 'import':
                metadata = _read_import(root, source['id'])
                media.update(preview_width=media['width'], preview_height=media['height'])
                if metadata.get('original_size'):
                    media.update(width=metadata['width'], height=metadata['height'])
            address = (_view(relative) if relative else '/freevideo/editor/media?' +
                       urlencode(dict(id=source['id'], file='preview')))
            cover = None
            if media['has_video']:
                library_id = relative[len('FreeVideo/'):-len('/video.mp4')] if relative and relative.startswith('FreeVideo/') and relative.endswith('/video.mp4') else None
                if source['kind'] == 'work':
                    library_id = source['id']
                if library_id and _work(library_id):
                    # The existing endpoint requires a complete request receipt.
                    receipt = _safe(root, Path(relative).with_suffix('.request.json').as_posix())
                    _safe(root, Path(relative).with_suffix('.thumbnail.jpg').as_posix())
                    if receipt.is_file() and _read_json(receipt, 4 * 1024**2).get('success') is True:
                        cover = '/freevideo/library/thumbnail?' + urlencode(dict(id=library_id))
                if cover is None:
                    cover = '/freevideo/editor/thumbnail?' + urlencode(dict(key=key))
            info.update(media, video=address if media['has_video'] else None, audio=address if media['has_audio'] else None,
                        thumbnail=cover, missing=False)
        except Exception:
            # Missing/unreadable/linked media is a local placeholder, not a failed project load.
            pass
        result[key] = info
    return result


def _is_preview(root, relative):
    """A finished first-pass preview whose second pass can be run from its saved request."""
    try:
        from .comfy_bridge import preview_request
        preview_request(root, relative)
        return True
    except (ImportError, OSError, ValueError, TypeError):
        return False


def _work_text(root, relative):
    """The text a saved video was generated from; a block made from it starts with this description."""
    try:
        path = _safe(root, (PurePosixPath(relative).parent / 'prompt.txt').as_posix())
        with path.open('rb') as stream:
            data = stream.read(4 * 8000 + 1)
        if len(data) > 4 * 8000:
            # Longer than any description a block keeps; its beginning alone is not the description.
            return ''
        text = data.decode('utf-8').strip()
    except (OSError, ValueError):
        # Optional: an old, joined or hand-copied video has none.
        return ''
    # The same limit as a block's own description, so the project it lands in still saves.
    return text if _text(text, 8000) else ''


def _read_import(root, identity):
    key = (str(Path(root).resolve()), identity)
    with _STATE:
        if key in _FAILED_IMPORTS:
            value = _FAILED_IMPORTS[key]
            try:
                _write(_import_path(root, identity), value)
                del _FAILED_IMPORTS[key]
            except (OSError, ValueError):
                pass
            return copy.deepcopy(value)
    value = _read_json(_import_path(root, identity), 64 * 1024)
    if (not isinstance(value, dict) or value.get('id') != identity or not _text(value.get('name'), 255)
            or value.get('kind') not in ('video', 'audio') or value.get('state') not in ('preparing', 'ready', 'failed')
            or type(value.get('fraction')) not in (float, int) or not 0 <= value['fraction'] <= 1):
        raise ValueError('Invalid import record')
    # Old ready imports stored proxy dimensions. Backfill once, without changing
    # any saved project. The lock also prevents concurrent first reads probing twice.
    with _STATE:
        value = _read_json(_import_path(root, identity), 64 * 1024)
        if value['state'] == 'ready' and value['kind'] == 'video' and not value.get('original_size'):
            original = _import_path(root, identity, 'source.bin')
            if original.is_file():
                try:
                    media = probe(original)
                    if media['has_video']:
                        value.update(preview_width=value.get('width'), preview_height=value.get('height'),
                                     width=media['width'], height=media['height'], original_size=True)
                        _write(_import_path(root, identity), value)
                except Exception:
                    # A legacy preview stays usable when its original cannot be read.
                    return _read_json(_import_path(root, identity), 64 * 1024)
    return value


def _import_update(root, identity, **fields):
    with _STATE:
        value = _read_import(root, identity)
        if 'fraction' in fields:
            fields['fraction'] = max(value['fraction'], min(1., max(0., fields['fraction'])))
        value.update(fields)
        try:
            _write(_import_path(root, identity), value)
        except (OSError, ValueError):
            if value.get('state') == 'failed':
                _FAILED_IMPORTS[(str(Path(root).resolve()), identity)] = copy.deepcopy(value)
            raise
        return value


def list_imports(root, unreadable=None):
    """Imports, newest first; damaged records are named in the optional `unreadable` list. A folder whose
    record was never written is an upload that did not finish, not a damaged one."""
    folder = _editor_path(root, 'imports')
    if not folder.exists():
        return []
    rows = []
    with _STATE:
        for child in folder.iterdir():
            if not _IMPORT.fullmatch(child.name):
                continue
            try:
                value = _read_import(root, child.name)
                stamp = _import_path(root, child.name).stat().st_mtime_ns
                fields = ('id', 'name', 'kind', 'state', 'fraction', 'reason', 'frames', 'fps', 'width', 'height',
                          'preview_width', 'preview_height', 'has_audio')
                row = {k: value.get(k) for k in fields}
                row['started'] = bool(value.get('started'))
                for side in ('width', 'height'):
                    row['preview_' + side] = value.get('preview_' + side, value.get(side))
                row['preview'] = '/freevideo/editor/media?' + urlencode(dict(id=child.name, file='preview')) if value['state'] == 'ready' else None
                row['thumbnail'] = '/freevideo/editor/media?' + urlencode(dict(id=child.name, file='thumbnail')) if value['state'] == 'ready' else None
                rows.append((stamp, child.name, row))
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError):
                if unreadable is not None:
                    unreadable.append(child.name)
    return [row for _, _, row in sorted(rows, key=lambda item: item[:2], reverse=True)]


def _display_name(value):
    if not isinstance(value, str) or not value or re.search(r'%(?![0-9a-fA-F]{2})', value):
        raise ValueError('Invalid import filename')
    try:
        name = PureWindowsPath(unquote(value, encoding='utf-8', errors='strict')).name
    except (ValueError, UnicodeError):
        raise ValueError('Invalid import filename') from None
    if not name or name in ('.', '..') or not _text(name, 255) or any(ord(c) < 32 for c in name):
        raise ValueError('Invalid import filename')
    return name


async def receive(request, root, *, receipt=None):
    if request.content_length is not None and request.content_length > MAX_IMPORT_BYTES:
        raise TooLarge('Import exceeds 8 GiB')
    name = _display_name(request.headers.get('X-FreeVideo-Filename'))
    kind = 'audio' if PureWindowsPath(name).suffix.lower() in ('.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.aiff', '.aif') else 'video'
    identity = 'i-' + uuid.uuid4().hex
    destination = _import_path(root, identity, 'source.bin')
    folder = destination.parent
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=False)
    temporary = _import_path(root, identity, 'upload.partial')
    stream = None
    try:
        stream = await asyncio.to_thread(temporary.open, 'xb')
        size = 0
        async for chunk in request.content.iter_chunked(256 * 1024):
            size += len(chunk)
            if size > MAX_IMPORT_BYTES:
                raise TooLarge('Import exceeds 8 GiB')
            await asyncio.to_thread(stream.write, chunk)
        if not size:
            raise ValueError('Import is empty')
        await asyncio.to_thread(stream.flush)
        await asyncio.to_thread(os.fsync, stream.fileno())
        await asyncio.to_thread(stream.close)
        stream = None
        await asyncio.to_thread(os.replace, temporary, destination)
        value = dict(id=identity, name=name, kind=kind, state='preparing', fraction=0., reason=None,
                     frames=None, fps=FPS, width=None, height=None, has_audio=False)
        if receipt is not None:
            # Keep the Agent replay key in the same atomic record as the import.
            value['agent_receipt'] = receipt
        await asyncio.to_thread(_write, _import_path(root, identity), value)
        return {key: value[key] for key in ('id', 'name', 'kind', 'state', 'fraction')}
    except BaseException:
        if stream is not None:
            await asyncio.to_thread(stream.close)
        await asyncio.to_thread(shutil.rmtree, folder)
        raise


def _poster(image, destination):
    from PIL import Image
    image.thumbnail((384, 256), Image.Resampling.LANCZOS)
    image.convert('RGB').save(destination, format='JPEG', quality=80)


def transcode(root, identity):
    """Stream one video frame/audio packet at a time with software codecs only."""
    import av
    from PIL import Image, ImageDraw
    from .media_encoding import clip_seconds
    from .editor_frames import display_frame
    original = _import_path(root, identity, 'source.bin')
    partial = _import_path(root, identity, 'preview.partial')
    poster = _import_path(root, identity, 'thumbnail.partial')
    try:
        with _TRANSCODING, av.logging.Capture(local=True), av.open(str(original)) as reader:
            original_info = dict(width=0, height=0)
            vin = next(iter(reader.streams.video), None)
            ain = next(iter(reader.streams.audio), None)
            if vin is None and ain is None:
                raise ValueError('No media')
            kind = 'video' if vin is not None else 'audio'
            _import_update(root, identity, kind=kind, started=True)
            seconds = max((clip_seconds(reader, s) or 0 for s in (vin, ain) if s is not None), default=0)
            origin = Fraction(vin.start_time or 0) * vin.time_base if vin is not None else Fraction(ain.start_time or 0) * ain.time_base
            with av.open(str(partial), 'w', format='mp4' if vin is not None else 'ipod', options={'movflags': 'faststart'}) as writer:
                vout = aout = resampler = None
                if vin is not None:
                    vin.thread_count = 1
                    if min(vin.width, vin.height) < 2 or vin.width * vin.height > 64_000_000:
                        raise ValueError('Invalid video dimensions')
                    original_info = probe(original)
                    scale = min(1., 1920 / max(original_info['width'], original_info['height']))
                    width, height = (max(2, int(side * scale) // 2 * 2) for side in (original_info['width'], original_info['height']))
                    vout = writer.add_stream('libx264', rate=FPS)
                    vout.width, vout.height, vout.pix_fmt = width, height, 'yuv420p'
                    vout.codec_context.thread_count = 2
                    vout.options = {'crf': '20', 'preset': 'fast'}
                if ain is not None:
                    ain.thread_count = 1
                    aout = writer.add_stream('aac', rate=32000)
                    aout.layout = 'stereo'
                    aout.bit_rate = 128000
                    aout.codec_context.thread_count = 1
                    resampler = av.AudioResampler(format='fltp', layout='stereo', rate=32000)
                next_video, previous, previous_time, previous_end = 0, None, 0., 0.
                audio_pos, decoded_audio = None, 0
                last_update, fraction = 0., 0.

                def emit_video(until):
                    nonlocal next_video
                    while previous is not None and not frame_not_after(until, next_video):
                        frame = previous.reformat(width=width, height=height, format='yuv420p')
                        frame.pts, frame.time_base = next_video, Fraction(1, FPS)
                        for packet in vout.encode(frame):
                            writer.mux(packet)
                        next_video += 1

                def emit_audio(frame):
                    nonlocal audio_pos, decoded_audio
                    if audio_pos < 0:
                        skip = min(-audio_pos, frame.samples)
                        audio_pos += skip
                        if skip == frame.samples:
                            return
                        frame = av.AudioFrame.from_ndarray(frame.to_ndarray()[:, skip:].copy(), format='fltp', layout='stereo')
                        frame.sample_rate = 32000
                    frame.pts, frame.time_base = audio_pos, Fraction(1, 32000)
                    audio_pos += frame.samples
                    decoded_audio += frame.samples
                    for packet in aout.encode(frame):
                        writer.mux(packet)

                for packet in reader.demux([s for s in (vin, ain) if s is not None]):
                    for frame in packet.decode():
                        frame_time = Fraction(frame.pts) * frame.time_base - origin if frame.pts is not None else 0.
                        if packet.stream == vin:
                            picture = display_frame(frame)
                            if previous is not None:
                                emit_video(max(previous_time, frame_time))
                            else:
                                _poster(picture.to_image(), poster)
                            # Keep one scaled frame for dropping/duplicating to CFR.
                            previous = picture.reformat(width=width, height=height, format='yuv420p')
                            previous_time = max(Fraction(0), frame_time)
                            previous_end = last_frame_end(previous_time, frame, vin.average_rate or vin.guessed_rate or FPS)
                        else:
                            if audio_pos is None:
                                audio_pos = round(frame_time * 32000)
                            frame.pts = None  # Normalize the retained preview clock.
                            for item in resampler.resample(frame):
                                emit_audio(item)
                        now = time.monotonic()
                        progress = min(.98, max(0., frame_time / seconds * .98)) if seconds > 0 else .5
                        if progress > fraction + .02 and now - last_update > .2:
                            _import_update(root, identity, fraction=progress)
                            last_update, fraction = now, progress
                if vout is not None:
                    emit_video(previous_end)
                    if not next_video:
                        raise ValueError('No decoded video')
                    for packet in vout.encode():
                        writer.mux(packet)
                if aout is not None:
                    for frame in resampler.resample(None):
                        emit_audio(frame)
                    for packet in aout.encode():
                        writer.mux(packet)
                    if not decoded_audio:
                        raise ValueError('No decoded audio')
                if vin is None:
                    # An audio-only preview gets a neutral local poster, without text.
                    image = Image.new('RGB', (384, 216), '#18222f')
                    draw = ImageDraw.Draw(image)
                    for x in range(80, 304, 8):
                        amplitude = 10 + round(42 * abs(math.sin(x / 23)))
                        draw.line((x, 108 - amplitude, x, 108 + amplitude), fill='#8096ae', width=3)
                    _poster(image, poster)
            target = _import_path(root, identity, 'preview.mp4' if kind == 'video' else 'preview.m4a')
            os.replace(partial, target)
            os.replace(poster, _import_path(root, identity, 'thumbnail.jpg'))
            info = probe(target)
            info.update(preview_width=info['width'], preview_height=info['height'],
                        width=original_info['width'], height=original_info['height'], original_size=True)
            if kind == 'video':
                info['frames'] = next_video
            _import_update(root, identity, **info, state='ready', fraction=1., reason=None)
    finally:
        partial.unlink(missing_ok=True)
        poster.unlink(missing_ok=True)


class ImportManager:
    """One daemon worker per server; restarted preparations are retried once."""
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.thread = threading.Thread(target=self.run, name='freevideo-editor-import', daemon=True)

    def start(self):
        self.thread.start()

    def submit(self, identity):
        self.queue.put(identity)

    def prepare(self, identity):
        try:
            transcode(self.root, identity)
        except Exception as error:
            # A full disk is not the file's fault, so the page says so instead of listing formats.
            reason = _disk_full(error).get('reason', 'unreadable')
            try:
                _import_update(self.root, identity, state='failed', reason=reason)
            except (OSError, ValueError):
                pass  # The terminal snapshot is retained and retried by the worker/reads.

    def recover(self):
        try:
            cleanup_projects(self.root)
        except (OSError, ValueError):
            _LOG.warning('Editor uncommitted directory cleanup failed; continuing.')
        try:
            rows = list_imports(self.root)
        except (OSError, ValueError):
            _LOG.warning('Editor import recovery failed; retrying later.')
            self.recovery_pending = True
            return
        self.recovery_pending = False
        for row in rows:
            if row['state'] == 'preparing':
                self.prepare(row['id'])

    def run(self):
        self.recover()
        while True:
            try:
                identity = self.queue.get(timeout=5)
            except queue.Empty:
                if self.recovery_pending:
                    self.recover()
                for root, identity in list(_FAILED_IMPORTS):
                    if root == str(Path(self.root).resolve()):
                        _read_import(self.root, identity)
                continue
            try:
                if _read_import(self.root, identity)['state'] == 'preparing':
                    self.prepare(identity)
            except (OSError, ValueError):
                pass
            finally:
                self.queue.task_done()


def _cache(root, category, key, path, extra=''):
    info = path.stat()
    stamp = hashlib.sha256(f'v1:{key}:{info.st_size}:{info.st_mtime_ns}:{extra}'.encode()).hexdigest()
    return _editor_path(root, category + '/' + stamp)


def thumbnail(root, key):
    import av
    path = media_path(root, key)
    source = _key(key)
    if source['kind'] == 'import':
        return _import_path(root, source['id'], 'thumbnail.jpg')
    cached = _cache(root, 'thumbnails', key, path).with_suffix('.jpg')
    cached = _safe(root, cached.relative_to(Path(root).resolve()).as_posix())
    if not cached.exists():
        cached.parent.mkdir(parents=True, exist_ok=True)
        with av.logging.Capture(local=True), av.open(str(path)) as reader:
            stream = reader.streams.video[0]
            stream.thread_count = 1
            _poster(next(reader.decode(stream)).to_image(), cached)
    return cached


def waveform(root, key, points=200):
    import av
    import numpy as np
    if not _whole(points, 1, 2000):
        raise ValueError('Invalid waveform point count')
    points = int(points)
    path = media_path(root, key)
    info = probe(path)
    cached = _cache(root, 'waveforms', key, path, str(points)).with_suffix('.json')
    cached = _safe(root, cached.relative_to(Path(root).resolve()).as_posix())
    try:
        peaks = _read_json(cached, 128 * 1024)['peaks']
        if (len(peaks) == points and all(type(x) in (int, float) and 0 <= x <= 1 for x in peaks)):
            return peaks
    except (OSError, ValueError, TypeError, KeyError):
        pass
    peaks = np.zeros(points, dtype=np.float32)
    if info['has_audio']:
        with av.logging.Capture(local=True), av.open(str(path)) as reader:
            stream = reader.streams.audio[0]
            stream.thread_count = 1
            from .media_encoding import clip_seconds
            seconds = clip_seconds(reader, stream) or info['frames'] / info['fps']
            count = max(1, math.ceil(seconds * stream.rate))
            offset = 0
            for frame in reader.decode(stream):
                array = frame.to_ndarray()
                if np.issubdtype(array.dtype, np.integer):
                    limits = np.iinfo(array.dtype)
                    if limits.min == 0:
                        middle = (limits.max + 1) / 2
                        array = (array.astype(np.float32) - middle) / middle
                    else:
                        array = array.astype(np.float32) / max(abs(limits.min), limits.max)
                if frame.format.is_planar:
                    values = np.max(np.abs(array), axis=0)
                else:
                    values = np.max(np.abs(array.reshape(-1, len(frame.layout.channels))), axis=1)
                if not np.isfinite(values).all():
                    raise ValueError('Invalid decoded audio')
                bins = np.minimum(points - 1, (np.arange(len(values), dtype=np.int64) + offset) * points // count)
                np.maximum.at(peaks, bins, np.clip(values, 0, 1))
                offset += len(values)
    result = peaks.tolist()
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        _write(cached, dict(peaks=result))
    except OSError:
        pass  # Waveforms still work on a read-only output disk.
    return result


def _local_page(request):
    """Keep normal same-origin requests; reject cross-site browser requests."""
    headers = request.headers
    if headers.get('Sec-Fetch-Site') == 'cross-site':
        return False
    origin = headers.get('Origin')
    if origin is None:
        return True  # Same-origin GET/video requests usually have no Origin.
    try:
        parsed = urlsplit(origin)
        host = urlsplit('//' + request.host)
        scheme = effective_scheme(request, parsed.scheme)
        return (parsed.scheme in ('http', 'https') and parsed.scheme == scheme and not parsed.username
                and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment
                and parsed.hostname == host.hostname
                and (parsed.port or (443 if parsed.scheme == 'https' else 80)) == (host.port or (443 if scheme == 'https' else 80)))
    except ValueError:
        return False


def _same_origin_audio(request):
    if request.headers.get('Sec-Fetch-Site') in ('same-site', 'cross-site'):
        return False
    if not _local_page(request):
        return False
    referer = request.headers.get('Referer')
    if referer:
        try:
            page = urlsplit(referer)
            origin = urlsplit(effective_scheme(request, page.scheme) + '://' + request.host)
            return (page.scheme == origin.scheme and page.hostname == origin.hostname
                    and (page.port or (443 if page.scheme == 'https' else 80)) ==
                        (origin.port or (443 if origin.scheme == 'https' else 80))
                    and not page.username and not page.password)
        except ValueError:
            return False
    return True  # Non-browser clients are not authenticated by these headers.


async def _body(request):
    if request.content_length is not None and request.content_length > BODY_BYTES:
        raise TooLarge('Project body is too large')
    data = bytearray()
    async for chunk in request.content.iter_chunked(64 * 1024):
        data.extend(chunk)
        if len(data) > BODY_BYTES:
            raise TooLarge('Project body is too large')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('Invalid editor request')
    return value


def register():
    from aiohttp import web
    import folder_paths
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_editor', False):
        return
    manager = ImportManager(folder_paths.get_output_directory())
    server._freevideo_editor = manager
    previews = asyncio.Semaphore(1)

    def endpoint(method, path):
        def decorate(handler):
            async def guarded(request):
                if not _local_page(request):
                    return web.json_response(dict(error='Local page required'), status=403, headers=_HEADERS)
                try:
                    return await handler(request)
                except DeleteRefused as error:
                    return web.json_response(dict(error='Cannot delete this edit', reason=error.reason), status=409, headers=_HEADERS)
                except Conflict as error:
                    return web.json_response(dict(revision=error.revision, **({'id': error.identity} if error.identity else {})), status=409, headers=_HEADERS)
                except TooLarge:
                    return web.json_response(dict(error='Editor request is too large'), status=413, headers=_HEADERS)
                except FileNotFoundError:
                    return web.json_response(dict(error='Editor item unavailable'), status=404, headers=_HEADERS)
                except OSError as error:
                    return web.json_response(dict(saved=False, retry=True, **_disk_full(error)), status=503, headers=_HEADERS)
                except (ValueError, TypeError, KeyError, RecursionError):
                    return web.json_response(dict(error='Invalid editor request'), status=400, headers=_HEADERS)
                except Exception:
                    return web.json_response(dict(error='Editor media unavailable'), status=404, headers=_HEADERS)
            getattr(server.routes, method)(path)(guarded)
            return guarded
        return decorate

    def root():
        return folder_paths.get_output_directory()

    @endpoint('get', '/freevideo/editor/projects')
    async def projects(request):
        unreadable = []
        items = await asyncio.to_thread(list_projects, root(), unreadable)
        return web.json_response(dict(items=items, unreadable=len(unreadable)), headers=_HEADERS)

    @endpoint('get', '/freevideo/editor/project')
    async def project(request):
        if ('id' in request.query) == ('work' in request.query):
            raise ValueError('Provide either id or work')
        lookup = load if 'id' in request.query else load_by_work
        identity = request.query.get('id') if 'id' in request.query else request.query.get('work')
        return web.json_response(await asyncio.to_thread(lookup, root(), identity), headers=_HEADERS)

    @endpoint('post', '/freevideo/editor/project')
    async def persist(request):
        body = await _body(request)
        try:
            result = await asyncio.to_thread(save, root(), body.get('project'), body.get('create'), body.get('operation'))
        except MissingProject:
            raise
        except OSError as error:
            # A missing staging directory is a retryable write error, not 404.
            return web.json_response(dict(saved=False, retry=True, **_disk_full(error)), status=503, headers=_HEADERS)
        return web.json_response(dict(project=result), headers=_HEADERS)

    @endpoint('post', '/freevideo/editor/project/delete')
    async def remove_project(request):
        body = await _body(request)
        await asyncio.to_thread(delete_project, root(), body.get('id'), body.get('session'))
        return web.json_response(dict(deleted=True), headers=_HEADERS)

    @endpoint('post', '/freevideo/editor/project/session')
    async def use_project(request):
        body = await _body(request)
        if type(body.get('release', False)) is not bool:
            raise ValueError('Invalid session action')
        await asyncio.to_thread(project_session, root(), body.get('id'), body.get('session'), body.get('release', False))
        return web.json_response(dict(ok=True), headers=_HEADERS)

    @endpoint('get', '/freevideo/editor/sources')
    async def source_lookup(request):
        keys = request.query.getall('key', [])
        if len(keys) > 1000:
            raise ValueError('Too many sources')
        return web.json_response(dict(sources=await asyncio.to_thread(sources, root(), keys)), headers=_HEADERS)

    @endpoint('post', '/freevideo/editor/audio-chains')
    async def audio_chains(request):
        if not _same_origin_audio(request):
            return web.json_response(dict(error='Same origin required'), status=403, headers=_HEADERS)
        from .editor_playback import prepare
        body = await _body(request)
        stopped = threading.Event()
        connected = hasattr(request, 'transport')
        def cancelled():
            transport = getattr(request, 'transport', None)
            return stopped.is_set() or (connected and (transport is None or transport.is_closing()))
        worker = asyncio.create_task(asyncio.to_thread(prepare, root(), body.get('project'), cancelled))
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            stopped.set()
            try:
                await worker
            except Exception:
                pass
            raise
        return web.json_response(result, headers=_HEADERS)

    @endpoint('get', '/freevideo/editor/audio-chain')
    async def audio_chain(request):
        if not _same_origin_audio(request):
            return web.json_response(dict(error='Same origin required'), status=403, headers=_HEADERS)
        from .editor_playback import lease_audio
        path = await asyncio.to_thread(lease_audio, root(), request.query.get('id'), request.query.get('key'))
        if not path.is_file():
            raise FileNotFoundError('Playback audio unavailable')
        return web.FileResponse(path, headers={'Content-Type': 'audio/wav', 'Cross-Origin-Resource-Policy': 'same-origin', **_HEADERS})

    @endpoint('post', '/freevideo/editor/import')
    async def upload(request):
        value = await receive(request, root())
        manager.submit(value['id'])
        return web.json_response({'import': value}, headers=_HEADERS)

    @endpoint('get', '/freevideo/editor/imports')
    async def imports(request):
        unreadable = []
        items = await asyncio.to_thread(list_imports, root(), unreadable)
        return web.json_response(dict(items=items, unreadable=len(unreadable)), headers=_HEADERS)

    @endpoint('get', '/freevideo/editor/waveform')
    async def peaks(request):
        async with previews:
            result = await asyncio.to_thread(waveform, root(), request.query.get('key'), int(request.query.get('points', '200')))
        return web.json_response(dict(peaks=result), headers=_HEADERS)

    @endpoint('get', '/freevideo/editor/thumbnail')
    async def cover(request):
        async with previews:
            path = await asyncio.to_thread(thumbnail, root(), request.query.get('key'))
        return web.FileResponse(path, headers={'Content-Type': 'image/jpeg', **_HEADERS})

    @endpoint('get', '/freevideo/editor/media')
    async def imported_media(request):
        identity, filename = request.query.get('id'), request.query.get('file')
        if filename not in ('preview', 'thumbnail'):
            raise ValueError('Invalid import media')
        value = await asyncio.to_thread(_read_import, root(), identity)
        if value['state'] != 'ready':
            raise FileNotFoundError('Import preview unavailable')
        path = (media_path(root(), 'import:' + identity) if filename == 'preview'
                else _import_path(root(), identity, 'thumbnail.jpg'))
        if not path.is_file():
            raise FileNotFoundError('Import preview unavailable')
        content_type = 'image/jpeg' if filename == 'thumbnail' else 'video/mp4' if value['kind'] == 'video' else 'audio/mp4'
        return web.FileResponse(path, headers={'Content-Type': content_type, **_HEADERS})

    register_reverse(endpoint, root, server)
    manager.start()
