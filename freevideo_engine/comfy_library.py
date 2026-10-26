"""Browse completed FreeVideo outputs across ComfyUI/browser restarts."""
import asyncio
from contextlib import nullcontext
import io
import json
import logging
import os
import re
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .agent import enabled as agent_enabled
from .comfy_assets import output_summary
from .runtime_alias import is_junction, checked_real_path, AliasUnavailable
from .long_paths import io_path, plain
from .system import windows

_ID = re.compile(r'\d{4}-\d{2}-\d{2}/[0-9a-f]{32}')
# The prompt box takes 20000 characters; a character is at most 4 bytes.
_PROMPT_BYTES = 4 * 20000


def _video(root, identity):
    if not isinstance(identity, str) or not _ID.fullmatch(identity):
        raise ValueError('Invalid saved video')
    root = Path(root).resolve()
    path = (root / 'FreeVideo' / identity / 'video.mp4').resolve()
    if not path.is_relative_to(root / 'FreeVideo'):
        raise ValueError('Saved video is outside the output folder')
    return path


class _Unfinished(ValueError):
    """A run whose summary says it did not finish: not a damaged record."""


def _report(path):
    # Read only the small request summary, never tensors, prompts or media.
    with path.with_suffix('.request.json').open('rb') as stream:
        data = stream.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError('Request summary is too large')
    report = json.loads(data)
    if not isinstance(report, dict):
        raise ValueError('Request summary is not an object')
    if report.get('success') is not True:
        raise _Unfinished('Video is not complete')
    return report


def _agent(folder):
    """The agent job that made this run, from the small record agent requests leave in workflow.json."""
    try:
        with (folder / 'workflow.json').open('rb') as stream:
            data = stream.read(64 * 1024 + 1)
        if len(data) > 64 * 1024 or b'"freevideo_agent"' not in data:
            return None
        value = json.loads(data).get('freevideo_agent')
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(value, dict):
        return None
    return {key: str(value[key])[:120] for key in ('job', 'label', 'project', 'client') if value.get(key)}


def _favorite(path):
    """A missing or damaged optional sidecar does not hide a saved video."""
    saved = path.with_suffix('.favorite.json')
    try:
        if not _plain(saved):
            return False
        with os.fdopen(os.open(saved, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                                | getattr(os, 'O_BINARY', 0)), 'rb') as stream:
            data = stream.read(1025)
        value = json.loads(data) if len(data) <= 1024 else None
        return isinstance(value, dict) and value.get('favorite') is True
    except (OSError, ValueError):
        return False


def set_favorite(output_directory, identity, favorite):
    """Change only a completed creation's favorite sidecar, under the delete locks."""
    if type(favorite) is not bool:
        raise ValueError('Invalid favorite state')
    output_directory = checked_real_path(output_directory)
    root = Path(output_directory).resolve() / 'FreeVideo'
    with _PENDING, _RELEASING:
        path = _video(output_directory, identity)
        expected = root / identity / 'video.mp4'
        if path != expected or not all(_plain(p) for p in (root, path.parent.parent, path.parent, path)) or not path.is_file():
            raise ValueError('Saved video is not a regular library creation')
        report = path.with_suffix('.request.json')
        saved = path.with_suffix('.favorite.json')
        if not _plain(report) or saved.is_symlink() or (saved.exists() and (not _plain(saved) or not saved.is_file())):
            raise ValueError('Saved video sidecar is not a regular file')
        _report(path)
        if not path.stat().st_size:
            raise ValueError('Video is empty')
        if favorite:
            from .monitoring import save
            save(saved, dict(favorite=True))
        else:
            saved.unlink(missing_ok=True)
    return dict(id=identity, favorite=favorite)


def list_videos(output_directory, *, before=None, limit=24, favorite=None):
    """Return a stable newest-first page, with only relative output paths."""
    if not 1 <= limit <= 48:
        raise ValueError('Invalid page size')
    if favorite is not None and type(favorite) is not bool:
        raise ValueError('Invalid favorite filter')
    cursor = None
    if before:
        stamp, identity = before.split(':', 1)
        if not stamp.isdigit() or not _ID.fullmatch(identity):
            raise ValueError('Invalid page cursor')
        cursor = (int(stamp), identity)
    root = Path(output_directory).resolve()
    from .comfy_chain import hidden_outputs
    hidden = hidden_outputs(root)
    candidates = []
    for path in (root / 'FreeVideo').glob('*/*/video.mp4'):
        identity = path.parent.relative_to(root / 'FreeVideo').as_posix()
        try:
            path = _video(root, identity)
            info = path.stat()
            key = (info.st_mtime_ns, identity)
            if info.st_size and (cursor is None or key < cursor):
                candidates.append((key, path, info))
        except (OSError, ValueError):
            continue
    rows, unreadable = [], []
    for key, path, info in sorted(candidates, key=lambda item: item[0], reverse=True):
        relative = Path('FreeVideo') / key[1] / 'video.mp4'
        if relative.as_posix() in hidden:
            continue
        is_favorite = _favorite(path)
        if favorite is not None and is_favorite != favorite:
            continue
        try:
            try:
                report = _report(path)
            except (FileNotFoundError, _Unfinished):
                # A generation still writing its video has no summary yet; a failed run is not listed.
                continue
            except (OSError, ValueError):
                # Summaries are replaced atomically, so one that cannot be read is damaged.
                unreadable.append(key)
                continue
            summary = output_summary(report, relative)
            # Geometry may gain input metadata in future reports. Keep this API
            # an explicit allowlist, not an export of the retained report.
            summary['geometry'] = {k: v for k, v in summary['geometry'].items()
                                   if k in ('width', 'height', 'frames', 'fps', 'seconds')
                                   and type(v) in (int, float)}
            if 'encoder_file' in summary:
                # The library API exposes only relative paths.
                summary['encoder_file'] = dict(summary['encoder_file'])
                summary['encoder_file'].pop('path', None)
            if not (root / summary['report']).is_file():
                summary['report'] = None
            try:
                agent = _agent(path.parent) if agent_enabled() else None
            except Exception as error:
                # Optional attribution must not hide an otherwise valid saved video.
                logging.getLogger(__name__).warning('[FreeVideo] Agent label unavailable: %s', error)
                agent = None
            if agent:
                summary['agent'] = agent
            rows.append(dict(summary, id=key[1], bytes=info.st_size, favorite=is_favorite,
                             created_at=datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                             cursor=str(key[0]) + ':' + key[1]))
        except (OSError, ValueError, TypeError, AttributeError):
            # A partially written or old malformed report must not hide the
            # rest of the library or publish an unfinished generation.
            continue
        if len(rows) > limit:
            break
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = rows[-1]['cursor'] if more else None
    for row in rows:
        row.pop('cursor')
    # Each page counts the damaged summaries in its own range, so the pages add up.
    last = (int(next_cursor.split(':', 1)[0]), next_cursor.split(':', 1)[1]) if next_cursor else None
    return dict(items=rows, next=next_cursor, unreadable=sum(last is None or key > last for key in unreadable))


def prompt_text(output_directory, identity):
    """The text a saved video was made from, or None for a run that kept none."""
    path = _video(output_directory, identity)
    root = Path(output_directory).resolve() / 'FreeVideo'
    saved = path.with_name('prompt.txt')
    if saved.is_symlink() or not saved.resolve().is_relative_to(root):
        raise ValueError('Saved prompt is outside the library or is a symbolic link')
    _report(path)
    try:
        with os.fdopen(os.open(saved, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)), 'rb') as stream:
            data = stream.read(_PROMPT_BYTES + 1)
    except FileNotFoundError:
        return None
    # Longer than the prompt box takes: its beginning alone is not the prompt.
    return data.decode('utf-8').strip() if len(data) <= _PROMPT_BYTES else None


def thumbnail(output_directory, identity):
    path = _video(output_directory, identity)
    saved = path.with_suffix('.thumbnail.jpg')
    root = Path(output_directory).resolve() / 'FreeVideo'
    def check_sidecar(sidecar):
        if sidecar.is_symlink() or not sidecar.resolve().is_relative_to(root):
            raise ValueError('Saved thumbnail sidecar is outside the library or is a symbolic link')
    check_sidecar(path.with_suffix('.request.json'))
    check_sidecar(saved)
    _report(path)
    if saved.is_file() and saved.stat().st_mtime_ns >= path.stat().st_mtime_ns:
        with os.fdopen(os.open(saved, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)), 'rb') as stream:
            data = stream.read(512 * 1024 + 1)
        if data.startswith(b'\xff\xd8') and len(data) <= 512 * 1024:
            return data
    # CPU decoding only. Grid thumbnails must not take CUDA memory from a
    # generation, and we only decode the first frame of each requested video.
    import av
    from PIL import Image
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_count = 1
        image = next(container.decode(stream)).to_image()
    image.thumbnail((384, 256), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=80)
    data = buffer.getvalue()
    check_sidecar(saved)
    try:
        with os.fdopen(os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0)
                                | getattr(os, 'O_BINARY', 0), 0o666), 'wb') as stream:
            stream.write(data)
    except OSError:
        check_sidecar(saved)
        pass  # A read-only output directory still supports browsing.
    return data


def video_download(output_directory, identity):
    """Name a saved download without renaming the engine's retained artifacts."""
    path = _video(output_directory, identity)
    _report(path)
    info = path.stat()
    if not info.st_size:
        raise ValueError('Video is empty')
    stamp = datetime.fromtimestamp(info.st_mtime, timezone.utc).strftime('%Y%m%d_%H%M%S')
    return path, 'FreeVideo_%s_%s.mp4' % (stamp, identity.split('/')[-1][:12])


# What a FreeVideo request writes into its own run folder, including the
# prompt-enhancement record and the staging copy monitoring.save keeps when
# Windows would not let it replace a JSON record. A creation whose folder
# holds anything else, or any link, is not deleted at all.
_OWNED_FILE = re.compile(r'(?:video\.[A-Za-z0-9._-]+|prompt\.txt|workflow\.json|comfy-request\.json|generate\.log'
                         r'|continuation-source\.safetensors'
                         r'|media\.json|prompt-rewrite\.json|input-conditioning\.pt|(?:first|last)\.png'
                         r'|reference-\d{2}\.(?:png|mp4|wav)'
                         r'|\.(?:share|editor-export)-[0-9a-f]{32}\.mp4)(?:\.[a-z0-9_]{8}\.tmp)?')
_OWNED_FOLDER = 'video.artifacts'
_REPARSE_POINT = 0x400


class DeleteRefused(ValueError):
    def __init__(self, reason, kept=()):
        super().__init__(reason)
        self.reason, self.kept = reason, list(kept)


def _plain(path):
    """A regular file or directory: never a symlink, junction or other reparse point."""
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & _REPARSE_POINT:
        return False
    return stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)


def _foreign(folder):
    """Names in a run folder that FreeVideo did not write, or that are links."""
    folder = io_path(checked_real_path(folder))
    kept = []
    for entry in sorted(folder.iterdir()):
        if not _plain(entry):
            kept.append(entry.name)
        elif entry.is_dir():
            if entry.name != _OWNED_FOLDER:
                kept.append(entry.name + '/')
                continue
            for parent, folders, files in os.walk(entry):
                for name in folders + files:
                    if not _plain(Path(parent) / name):
                        kept.append((Path(parent) / name).relative_to(folder).as_posix())
                folders[:] = [name for name in folders if not is_junction(Path(parent) / name)]
        elif not _OWNED_FILE.fullmatch(entry.name):
            kept.append(entry.name)
    return kept


def _remove(folder):
    """Remove a verified folder bottom-up; return bytes freed and paths left behind."""
    try:
        folder = io_path(checked_real_path(folder))
    except AliasUnavailable:
        return 0, [plain(folder)]
    freed, left = 0, []
    if is_junction(folder):
        return 0, [plain(folder)]
    if windows():
        # Bottom-up os.walk cannot prune before entering a Windows junction.
        # Collect a pruned listing, then keep os.walk's original bottom-up
        # sibling order when removing ordinary rows.
        rows = {}
        for parent, folders, files in os.walk(folder):
            linked = [name for name in folders if is_junction(Path(parent) / name)]
            left.extend(plain(Path(parent) / name) for name in linked)
            folders[:] = [name for name in folders if name not in linked]
            rows[Path(parent)] = (parent, folders[:], files)
        ordered, pending = [], [(Path(folder), False)]
        while pending:
            current, visited = pending.pop()
            row = rows.get(current)
            if row is None:
                continue
            if visited:
                ordered.append(row)
            else:
                pending.append((current, True))
                pending.extend((current / name, False) for name in reversed(row[1]))
        walk = ordered
    else:
        walk = os.walk(folder, topdown=False)
    for parent, folders, files in walk:
        for name in files:
            path = Path(parent) / name
            try:
                if not _plain(path):
                    left.append(plain(path)); continue
                size = path.stat().st_size
                if name == 'continuation-source.safetensors':
                    # Request snapshots are read-only while used; Windows
                    # requires clearing that flag when deleting their run.
                    path.chmod(0o600)
                path.unlink()
                freed += size
            except OSError:
                left.append(plain(path))
        for name in folders:
            try:
                (Path(parent) / name).rmdir()
            except OSError:
                left.append(plain(Path(parent) / name))
    try:
        Path(folder).rmdir()
    except OSError:
        left.append(plain(folder))
    return freed, left


# Decoded frames and audio the engine writes to build and check the MP4.
# Nothing reads them once a creation is saved: Creations, sharing and the
# workflow read the MP4, and an upscale reads the preview's first pass. The
# sampling result, conditioning and first pass stay.
_DECODED = ('rgb.npy', 'audio.npy', 'audio.wav')
_ATTEMPT = re.compile(r'\d{2}-[0-9A-Za-z_-]+')
_RELEASED = '.decoded-released.json'
# The startup release, a generation re-arming it and a library delete take
# turns, so none of them reads a folder or the marker while another writes it.
_RELEASING = threading.Lock()


def release_decoded(folder):
    """Delete a saved creation's decoded frames and audio, retried attempts' included.

    Only where the engine writes them: video.artifacts itself and its
    attempts/NN-id folders. A folder holding anything FreeVideo did not write,
    or any link, is left alone, as delete_video leaves it. Paths Windows still
    has open are listed.
    """
    try:
        folder = io_path(checked_real_path(folder))
        artifacts = folder / _OWNED_FOLDER
        if not _plain(folder) or _foreign(folder):
            return dict(freed_bytes=0, left=[], skipped='foreign-files')
        if not artifacts.is_dir():
            return dict(freed_bytes=0, left=[], skipped='no-artifacts')
        places, freed, left = [artifacts], 0, []
        try:
            if (artifacts / 'attempts').is_dir():
                places += [path for path in sorted((artifacts / 'attempts').iterdir())
                           if _ATTEMPT.fullmatch(path.name) and path.is_dir()]
        except OSError:
            left.append(_OWNED_FOLDER + '/attempts')  # retried at a later start
        for place in places:
            for name in _DECODED:
                path = place / name
                try:
                    if not _plain(path):
                        continue
                    size = path.stat().st_size
                    path.unlink()
                    freed += size
                except FileNotFoundError:
                    continue
                except OSError:
                    left.append(path.relative_to(folder).as_posix())
        return dict(freed_bytes=freed, left=left)
    except OSError:
        # Listed as left, so a later start tries the whole folder again.
        return dict(freed_bytes=0, left=['.'], skipped='unreadable')


# What an interrupted share or editor export leaves beside a creation: its working copy,
# written under a random name and renamed only when finished.
_INTERRUPTED = re.compile(r'\.(?:share|editor-export)-[0-9a-f]{32}\.mp4')


def clean_interrupted(output_directory, *, older=3600., now=None):
    """Delete working copies of shares and exports that a closed program left behind.

    Only files not written to for ``older`` seconds, so one being written now stays. A folder
    that holds nothing else afterwards (an export that never finished) goes with it.
    Returns the bytes freed.
    """
    try:
        output_directory = checked_real_path(output_directory)
    except AliasUnavailable:
        return 0
    root = Path(output_directory).resolve() / 'FreeVideo'
    now = time.time() if now is None else now
    freed = 0
    for path in sorted(root.glob('*/*/.*.mp4')):
        try:
            folder = path.parent
            if (not _INTERRUPTED.fullmatch(path.name) or not _ID.fullmatch(folder.relative_to(root).as_posix())
                    or not _plain(folder.parent) or not _plain(folder) or not _plain(path)):
                continue
            info = path.stat()
            if now - info.st_mtime < older:
                continue
            path.unlink()
            freed += info.st_size
            try:
                folder.rmdir()
            except OSError:
                pass
        except OSError:
            continue
    return freed


def release_later(output_directory):
    """Have the next start finish a release that Windows kept open."""
    from .monitoring import save
    output_directory = checked_real_path(output_directory)
    with _RELEASING:
        save(Path(output_directory).resolve() / 'FreeVideo' / _RELEASED, dict(schema=1, pending=True))


def release_earlier(output_directory):
    """Once after updating, release the creations saved before completion did it.

    Only finished creations of the creative workspace, which writes
    comfy-request.json, and none that is being generated. Later starts repeat
    it while a file Windows kept open is left.
    """
    from .monitoring import save
    try:
        output_directory = checked_real_path(output_directory)
    except AliasUnavailable:
        return None
    root = Path(output_directory).resolve() / 'FreeVideo'
    marker = root / _RELEASED
    if not root.is_dir():
        return None
    with _RELEASING:
        # A marker Windows would not let save replace stays beside it as a
        # staging copy; the release it asked for is still owed.
        staged = sorted(root.glob(_RELEASED + '.*.tmp'))
        try:
            if not staged and json.loads(marker.read_text(encoding='utf-8')).get('pending') is not True:
                return None
        except (OSError, ValueError, AttributeError):
            pass
        totals = dict(videos=0, freed_bytes=0, left=0, skipped=0)
        for path in sorted(root.glob('*/*/video.mp4')):
            folder = path.parent
            try:
                if (not _ID.fullmatch(folder.relative_to(root).as_posix())
                        or not _plain(folder.parent) or not _plain(folder)):
                    continue
                _report(path)
                state = folder / 'comfy-request.json'
                if state.stat().st_size > 1024 * 1024:
                    continue
                state = json.loads(state.read_text(encoding='utf-8'))
                if not isinstance(state, dict) or state.get('status') in ('starting', 'running'):
                    continue
            except (OSError, ValueError, TypeError):
                continue
            result = release_decoded(folder)
            totals['videos'] += result['freed_bytes'] > 0
            totals['freed_bytes'] += result['freed_bytes']
            totals['left'] += len(result['left'])
            totals['skipped'] += result.get('skipped') == 'foreign-files'
        save(marker, dict(schema=1, pending=totals['left'] > 0, released_at=time.time(), **totals))
        for path in staged:
            try:
                path.unlink()
            except OSError:
                pass
    return totals


def _queued_continuation_uses(path, output_directory, queue):
    if queue is None:
        return False
    from .comfy_continuation import resolve_continue_from
    for rows in queue.get_current_queue():
        for entry in rows:
            prompt = entry[2] if isinstance(entry, (list, tuple)) and len(entry) > 2 else None
            if not isinstance(prompt, dict):
                continue
            for node in prompt.values():
                if not isinstance(node, dict) or node.get('class_type') != 'FreeVideoGenerate':
                    continue
                inputs = node.get('inputs', {})
                source = inputs.get('continue_from') if isinstance(inputs, dict) else None
                if isinstance(source, (list, tuple)):
                    # A connected path is resolved only by execution. Until
                    # then the queue can still depend on this creation.
                    return True
                if not isinstance(source, str) or not source:
                    continue
                try:
                    # Saved creations live under output; its absolute paths
                    # and relative history names share the node's resolver.
                    resolved = resolve_continue_from(source, output_directory, output_directory)
                except ValueError:
                    continue
                if Path(resolved) == path:
                    return True
    return False


def _request_states(root, cache):
    """The small request records of every run, re-read only when their file changed."""
    rows = []
    for state_path in root.glob('*/*/comfy-request.json'):
        try:
            info = state_path.stat()
            key = (info.st_mtime_ns, info.st_size, getattr(info, 'st_ino', 0))
            known = cache.get(state_path)
            if known is None or known[0] != key:
                state = json.loads(state_path.read_text(encoding='utf-8')) if info.st_size <= 1024 * 1024 else {}
                known = cache[state_path] = (key, state)
        except (OSError, ValueError):
            continue
        rows.append(known[1])
    return rows


class RecordUnsaved(Exception):
    """The record that names a folder before it moves could not be written, so nothing moved."""


def _trash_creation(output_directory, identity, queue, cache, announce=None):
    """Check that a creation may go, and rename its folder into FreeVideo/.deleted.

    Returns the trash folder and the content identifier of its video. Nothing is
    deleted yet: the caller removes the folder when it is sure it is not wanted back.
    ``announce(target, identifier)`` runs under the same locks just before the move, so a
    caller can write down where the folder is going first; if it raises, nothing moves.
    """
    path = _video(output_directory, identity)
    _report(path)
    root = Path(output_directory).resolve() / 'FreeVideo'
    folder = path.parent
    if folder.parent.parent != root or not _plain(folder.parent) or not _plain(folder):
        raise DeleteRefused('not-freevideo')
    # Not while the startup release is deleting files in this folder. _PENDING comes first,
    # as in undo_deleted, so the move and its in-flight mark cannot interleave with a purge.
    with _PENDING, _RELEASING, (queue.mutex if queue is not None else nullcontext()):
        from .comfy_editor import reference_users
        try:
            users = reference_users(output_directory, identity)
        except (OSError, ValueError):
            raise DeleteRefused('editor-unavailable') from None
        if users:
            raise DeleteRefused('editor-in-use', users)
        kept = _foreign(folder)
        if kept:
            raise DeleteRefused('foreign-files', kept)
        if _queued_continuation_uses(path, output_directory, queue):
            raise DeleteRefused('continuation-in-use')
        # A second pass of this preview may be reading its first pass right now.
        for state in _request_states(root, cache):
            if not isinstance(state, dict) or state.get('status') not in ('queued', 'starting', 'running'):
                continue
            source = state.get('upscaled_preview')
            if isinstance(source, str) and Path(source).resolve() == path:
                raise DeleteRefused('in-use')
            command = state.get('command', [])
            if isinstance(command, list) and '--continue-from' in command:
                index = command.index('--continue-from') + 1
                if index < len(command) and isinstance(command[index], str) and Path(command[index]).resolve() == path:
                    raise DeleteRefused('continuation-in-use')
        # Content addressing needs the MP4 while it still exists. Optional
        # cleanup may fail, including in lightweight CI without PyAV.
        identifier = None
        try:
            from .continuation_store import video_id
            identifier = video_id(path)
        except ModuleNotFoundError as error:
            if error.name not in ('av', __package__ + '.continuation_store'):
                raise
        except Exception as error:
            logging.warning('[FreeVideo] Continuation cleanup failed (%s).', type(error).__name__)
        trash = root / '.deleted'
        trash.mkdir(exist_ok=True)
        if not _plain(trash):
            raise DeleteRefused('not-freevideo')
        target = trash / ('%s-%d' % (identity.replace('/', '-'), time.time_ns()))
        if announce is not None:
            announce(target, identifier)
        try:
            folder.rename(target)
        except OSError as error:
            raise DeleteRefused('in-use') from error
        _INFLIGHT.add(target.name)
    return target, identifier


def _forget(identifiers, root):
    """Drop what only a deleted creation needed: its continuation data and reusable results."""
    for identifier in identifiers:
        if identifier is None:
            continue
        try:
            from .continuation_store import delete_id
            delete_id(identifier)
        except Exception as error:
            logging.warning('[FreeVideo] Continuation cleanup failed (%s).', type(error).__name__)


def _forget_results(identities, root):
    """A result-cache row naming a deleted creation can no longer be reused."""
    for index in (root / '.result-cache').glob('*.json'):
        try:
            if index.stat().st_size <= 1024 * 1024 and json.loads(index.read_text(encoding='utf-8')).get('id') in identities:
                index.unlink()
        except (OSError, ValueError, AttributeError, TypeError):
            pass


def delete_video(output_directory, identity, *, queue=None):
    """Delete one saved creation: its whole run folder, only when FreeVideo wrote everything in it.

    The folder is first renamed into FreeVideo/.deleted, so it leaves the
    library at once, or, when Windows still has a file open, nothing changes.
    """
    try:
        output_directory = checked_real_path(output_directory)
    except AliasUnavailable as error:
        raise DeleteRefused('not-freevideo') from error
    root = Path(output_directory).resolve() / 'FreeVideo'
    target, identifier = _trash_creation(output_directory, identity, queue, {})
    try:
        _forget([identifier], root)
        freed, left = _remove(target)
        _forget_results({identity}, root)
    finally:
        with _PENDING:
            _INFLIGHT.discard(target.name)
    # Finish earlier deletions that Windows had kept open.
    freed += _purge_trash(root / '.deleted', keep={target.name})
    return dict(id=identity, freed_bytes=freed, left=len(left))


# Several creations deleted together stay in FreeVideo/.deleted for a few seconds, so
# one click can bring them back. A manifest beside them names what to restore or remove.
UNDO_SECONDS = 10
_UNDO_GRACE = 3          # the server waits a little longer than the page shows
_MANIFEST = '.pending-'
_BATCH_NAME = re.compile(r'[0-9]{1,20}-[0-9a-f]{6}')
# A folder in the trash is named by the id it had, with its slash a dash, and the time it moved.
_TRASH_NAME = re.compile(r'\d{4}-\d{2}-\d{2}-[0-9a-f]{32}-\d+')
_PENDING = threading.Lock()
MAX_BATCH = 500
MAX_EXTEND = 200
_SESSION = os.urandom(6).hex()
# Batch names taken in the last hour, and those whose deletion is still moving folders.
# A name is taken before the first folder moves, so a repeated or parallel request with it
# is refused instead of overwriting the other's record. Guarded by _PENDING.
_CLAIMED = {}
_ACTIVE = set()
_CLAIM_SECONDS = 3600
_CLAIM_LIMIT = 4096
# Folders already moved into FreeVideo/.deleted whose batch record is not written yet.
# A purge running at the same time must leave them alone. Guarded by _PENDING.
_INFLIGHT = set()


def _pending_manifests(trash):
    rows = []
    try:
        names = sorted(path for path in trash.iterdir() if path.name.startswith(_MANIFEST) and path.suffix == '.json')
    except OSError:
        return rows
    for path in names:
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(value, dict) and isinstance(value.get('items'), list):
                rows.append((path, value))
        except (OSError, ValueError):
            continue
    return rows


def _claim(token, manifest):
    with _PENDING:
        now = time.monotonic()
        for name in [name for name, taken in _CLAIMED.items() if now - taken > _CLAIM_SECONDS]:
            del _CLAIMED[name]
        while len(_CLAIMED) >= _CLAIM_LIMIT:
            del _CLAIMED[next(iter(_CLAIMED))]
        if token in _CLAIMED or manifest.exists():
            raise ValueError('Invalid selection')
        _CLAIMED[token] = now
        _ACTIVE.add(token)


def _hold(root, tokens, due):
    """Set the end of the undo time of batches an earlier request of the same click already made.

    Returns the batches whose record could not be written; those keep the time they had.
    """
    from .monitoring import save
    failed = []
    with _PENDING:
        for token in tokens:
            path = root / '.deleted' / (_MANIFEST + token + '.json')
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                current = float(value['due'])
                if value.get('restore') or time.time() >= current:
                    continue
                value['due'] = due
                save(path, value)
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            except OSError:
                if path.exists():
                    failed.append(token)
    return failed


def _recover(root, path, value):
    """Try again to put back what a failed delete could not; never removes anything.

    The caller holds _PENDING and _RELEASING."""
    from .monitoring import save
    rows = [item for item in value['items'] if isinstance(item, dict) and isinstance(item.get('id'), str)
            and _ID.fullmatch(item['id']) and isinstance(item.get('name'), str)
            and _TRASH_NAME.fullmatch(item['name'])]
    failed = set(_restore_moved(root, rows))
    left = [row for row in rows if row['id'] in failed]
    try:
        if left:
            if len(left) != len(value['items']):
                save(path, dict(value, items=left))
        else:
            path.unlink()
    except OSError:
        logging.warning('FreeVideo could not update the record of creations it still has to put back.')
        return
    _INFLIGHT.difference_update(row['name'] for row in rows if row['id'] not in failed)


def _purge_trash(trash, keep=()):
    """Remove folders that earlier deletions left behind, except those a batch may still restore.

    The folders to remove are chosen while no batch is moving one in, so a folder
    that is on its way into a batch record is never taken for left-over rubbish.
    """
    freed = 0
    try:
        with _PENDING:
            held = set(keep) | _INFLIGHT
            for _, value in _pending_manifests(trash):
                held.update(str(item.get('name')) for item in value['items'] if isinstance(item, dict))
            leftovers = [earlier for earlier in trash.iterdir() if earlier.name not in held]
        for earlier in leftovers:
            try:
                if _plain(earlier) and earlier.is_dir():
                    freed += _remove(earlier)[0]
            except OSError:
                continue
    except OSError:
        pass
    return freed


def _restore_moved(root, rows):
    """Put folders back where they were taken from; returns the ids that could not be restored."""
    trash, failed = root / '.deleted', []
    for row in rows:
        try:
            source, folder = trash / row['name'], root / row['id']
            # Named before it moved: a folder that never left, or is already back, needs nothing.
            if not os.path.lexists(source):
                continue
            if folder.exists():
                raise OSError
            folder.parent.mkdir(parents=True, exist_ok=True)
            source.rename(folder)
        except OSError:
            failed.append(row['id'])
    return failed


def delete_videos(output_directory, identities, *, queue=None, batch=None, extend=()):
    """Delete several creations at once, each under the single delete's checks.

    A creation that is in use is skipped with its reason, never forced. The rest
    leave the library at once and are removed for good ``UNDO_SECONDS`` later;
    ``undo_deleted`` brings them back until then. ``batch`` lets the page name the batch
    before asking, so a page that is reloaded while the request runs can still undo it. A name
    is used once: a second request with it is refused. ``extend`` names the earlier batches of
    the same click; their undo time is kept open while this request runs and ends with it.
    """
    if batch is not None and not (isinstance(batch, str) and _BATCH_NAME.fullmatch(batch)):
        raise ValueError('Invalid selection')
    if (not isinstance(identities, list) or not identities or len(identities) > MAX_BATCH
            or not all(isinstance(identity, str) for identity in identities)):
        raise ValueError('Invalid selection')
    if (not isinstance(extend, (list, tuple)) or len(extend) > MAX_EXTEND
            or not all(isinstance(name, str) and _BATCH_NAME.fullmatch(name) for name in extend)):
        raise ValueError('Invalid selection')
    try:
        output_directory = checked_real_path(output_directory)
    except AliasUnavailable as error:
        raise DeleteRefused('not-freevideo') from error
    root = Path(output_directory).resolve() / 'FreeVideo'
    finish_deleted(output_directory)
    cache, moved, skipped = {}, [], []
    from .monitoring import save
    token = batch or '%d-%s' % (time.time_ns(), os.urandom(3).hex())
    manifest = root / '.deleted' / (_MANIFEST + token + '.json')
    _claim(token, manifest)
    if extend:
        _hold(root, extend, time.time() + 120)

    def announce(row):
        # Written before the folder moves, as a record that only puts things back: if the move, the next
        # step, a later write or this process fails, whatever sits in the trash is named and never removed.
        # The caller holds _PENDING.
        def write(target, identifier):
            try:
                save(manifest, dict(schema=1, due=0, restore=True, session=_SESSION,
                                    items=moved + [dict(row, name=target.name, continuation=identifier)]))
            except OSError as error:
                raise RecordUnsaved from error
        return write

    def publish(due):
        # Every folder is in the trash and named; from now on the record removes them when the time is over.
        with _PENDING:
            save(manifest, dict(schema=1, due=due, born=time.time(), session=_SESSION, items=moved))
            _INFLIGHT.difference_update(row['name'] for row in moved)

    try:
        for identity in dict.fromkeys(identities):
            try:
                target, identifier = _trash_creation(output_directory, identity, queue, cache,
                                                     announce=announce(dict(id=identity)))
            except DeleteRefused as error:
                skipped.append(dict(id=identity, reason=error.reason, kept=error.kept[:20]))
            except RecordUnsaved:
                skipped.append(dict(id=identity, reason='unsaved', kept=[]))
            except (FileNotFoundError, NotADirectoryError, ValueError):
                # The creation is already gone from disk (or was never complete): it only has to leave the page.
                skipped.append(dict(id=identity, reason='unavailable', kept=[]))
            except OSError:
                # Still there but not readable or writable (permissions, read-only disk): it stays, and says so.
                skipped.append(dict(id=identity, reason='failed', kept=[]))
            else:
                moved.append(dict(id=identity, name=target.name, continuation=identifier))
        if moved:
            # The undo time starts when the last creation has left the library.
            publish(time.time() + UNDO_SECONDS + _UNDO_GRACE)
        else:
            with _PENDING:
                try:
                    manifest.unlink()
                except OSError:
                    pass
    except BaseException:
        with _PENDING, _RELEASING:
            failed = set(_restore_moved(root, moved))
            _INFLIGHT.difference_update(row['name'] for row in moved)
            try:
                if not failed:
                    manifest.unlink()
                else:
                    logging.warning('FreeVideo could not put back %d creations after a failed delete.', len(failed))
                    # What could not be put back stays named by a record that only restores it, until it can be.
                    save(manifest, dict(schema=1, due=0, restore=True, session=_SESSION,
                                        items=[row for row in moved if row['id'] in failed]))
            except OSError:
                # The record already on disk was written before the moves and says the same.
                pass
        raise
    finally:
        # The earlier chunks get their normal time before this one is reported as finished.
        if extend:
            _hold(root, extend, time.time() + UNDO_SECONDS + _UNDO_GRACE)
        with _PENDING:
            _ACTIVE.discard(token)
    if moved:
        timer = threading.Timer(UNDO_SECONDS + _UNDO_GRACE + 1, _finish_quietly, (output_directory,))
        timer.daemon = True
        timer.start()
    return dict(batch=token if moved else None, undo_seconds=UNDO_SECONDS,
                deleted=[row['id'] for row in moved], skipped=skipped)


def _finish_quietly(output_directory):
    try:
        finish_deleted(output_directory)
    except Exception:
        logging.warning('FreeVideo could not finish removing deleted creations.', exc_info=True)


def finish_deleted(output_directory, *, everything=False):
    """Remove for good the batches whose undo time is over (all of them with ``everything``).

    Also what the continuation store and the result cache kept for them, and any
    folder an earlier deletion could not remove. Returns the bytes freed.
    """
    root = Path(checked_real_path(output_directory)).resolve() / 'FreeVideo'
    trash = root / '.deleted'
    if not trash.is_dir() or not _plain(trash):
        return 0
    claimed, recover = [], []
    with _PENDING:
        for path, value in _pending_manifests(trash):
            try:
                # A batch still moving folders is not touched.
                if path.name[len(_MANIFEST):-len('.json')] in _ACTIVE or any(
                        isinstance(item, dict) and item.get('name') in _INFLIGHT for item in value['items']):
                    continue
                if value.get('restore'):
                    recover.append((path, value))
                # Starting up, only what an earlier session left is finished; a batch of this one is still undoable.
                elif (everything and value.get('session') != _SESSION) or time.time() >= float(value.get('due', 0)):
                    path.unlink()
                    claimed.append(value['items'])
            except (OSError, ValueError, TypeError):
                continue
    if recover:
        with _PENDING, _RELEASING:
            for path, value in recover:
                _recover(root, path, value)
    freed = 0
    for items in claimed:
        rows = [item for item in items if isinstance(item, dict) and isinstance(item.get('name'), str)
                and _TRASH_NAME.fullmatch(item['name'])]
        _forget([item.get('continuation') for item in rows], root)
        for item in rows:
            folder = trash / item['name']
            if folder.name == item['name'] and _plain(folder) and folder.is_dir():
                freed += _remove(folder)[0]
        _forget_results({item.get('id') for item in rows}, root)
    return freed + _purge_trash(trash)


class UndoBusy(ValueError):
    """The batch is still being deleted; asking again once that is done works."""


def undo_deleted(output_directory, token):
    """Put a batch back while its undo time lasts; raises ValueError('expired') afterwards."""
    output_directory = checked_real_path(output_directory)
    root = Path(output_directory).resolve() / 'FreeVideo'
    trash = root / '.deleted'
    if not isinstance(token, str) or not _BATCH_NAME.fullmatch(token):
        raise ValueError('Invalid batch')
    manifest = trash / (_MANIFEST + token + '.json')
    restored, lost = [], []
    from .monitoring import save
    with _PENDING, _RELEASING:
        if token in _ACTIVE:
            raise UndoBusy('busy')
        try:
            value = json.loads(manifest.read_text(encoding='utf-8'))
            if value.get('restore') or time.time() >= float(value['due']):
                raise ValueError('expired')
            items = [item for item in value['items'] if isinstance(item, dict)]
        except (OSError, KeyError, TypeError, AttributeError):
            raise ValueError('expired') from None
        rows = [item for item in items if isinstance(item.get('id'), str) and _ID.fullmatch(item['id'])
                and isinstance(item.get('name'), str) and _TRASH_NAME.fullmatch(item['name'])]
        # From here the record only puts things back, so whatever fails or stops half way is never removed.
        try:
            save(manifest, dict(value, restore=True, due=0, items=items))
        except OSError:
            logging.warning('FreeVideo could not record an undo, so nothing was restored.')
            return dict(restored=[], lost=[item.get('id') for item in items])
        for item in items:
            if item not in rows:
                lost.append(item.get('id'))
                continue
            try:
                source, folder = trash / item['name'], root / item['id']
                if folder.exists() or not _plain(source):
                    raise OSError
                folder.parent.mkdir(parents=True, exist_ok=True)
                source.rename(folder)
                restored.append(item['id'])
            except OSError:
                lost.append(item['id'])
        # What could not come back stays named by the record, to be put back later and never removed.
        kept = [item for item in items if item.get('id') in lost]
        try:
            if kept:
                save(manifest, dict(value, restore=True, due=0, items=kept))
            else:
                manifest.unlink()
        except OSError:
            pass
    return dict(restored=restored, lost=lost)


def _checked_tokens(tokens):
    if (not isinstance(tokens, list) or len(tokens) > MAX_EXTEND
            or not all(isinstance(name, str) and _BATCH_NAME.fullmatch(name) for name in tokens)):
        raise ValueError('Invalid selection')
    return list(dict.fromkeys(tokens))


def undo_state(output_directory, tokens):
    """What the server says about each batch: ``busy`` (folders still moving), ``ready`` with the
    seconds left to undo it, or ``gone`` (finished, restored, expired or never made)."""
    tokens = _checked_tokens(tokens)
    root = Path(checked_real_path(output_directory)).resolve() / 'FreeVideo'
    states = {}
    with _PENDING:
        for token in tokens:
            if token in _ACTIVE:
                states[token] = dict(state='busy')
                continue
            try:
                value = json.loads((root / '.deleted' / (_MANIFEST + token + '.json')).read_text(encoding='utf-8'))
                left = float(value['due']) - time.time() - _UNDO_GRACE
                if value.get('restore') or left <= 0:
                    raise ValueError
                states[token] = dict(state='ready', seconds=left, count=len(value['items']))
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                states[token] = dict(state='gone')
    return states


def hold_deleted(output_directory, tokens, *, running):
    """Keep earlier batches of one click undoable while its next request runs (``running``), or
    give them the normal undo time again once it is over; returns ``undo_state``.

    Raises OSError when a batch's record could not be written: the caller must not go on as if it was held.
    """
    tokens = _checked_tokens(tokens)
    if not isinstance(running, bool):
        raise ValueError('Invalid selection')
    root = Path(checked_real_path(output_directory)).resolve() / 'FreeVideo'
    seconds = 120 if running else UNDO_SECONDS + _UNDO_GRACE
    failed = _hold(root, tokens, time.time() + seconds)
    states = undo_state(output_directory, tokens)
    if any(row['state'] == 'ready' for row in states.values()):
        # The request that would have ended their time may never come: check once the time is over.
        timer = threading.Timer(seconds + 1, _finish_quietly, (output_directory,))
        timer.daemon = True
        timer.start()
    if failed:
        raise OSError('Could not keep the batches')
    return states


def folder_sizes(output_directory, identities):
    """Bytes each creation's whole run folder holds, links not followed; unknown ids are left out."""
    if not isinstance(identities, list) or len(identities) > MAX_BATCH:
        raise ValueError('Invalid selection')
    root = Path(checked_real_path(output_directory)).resolve() / 'FreeVideo'
    sizes = {}
    for identity in identities:
        if not isinstance(identity, str) or not _ID.fullmatch(identity):
            continue
        folder = root / identity
        total = 0
        try:
            if not _plain(folder):
                continue
            for parent, folders, files in os.walk(folder):
                folders[:] = [name for name in folders if _plain(Path(parent) / name)]
                for name in files:
                    try:
                        info = os.lstat(Path(parent) / name)
                        total += info.st_size if stat.S_ISREG(info.st_mode) else 0
                    except OSError:
                        pass
        except OSError:
            continue
        sizes[identity] = total
    return sizes


def register():
    from aiohttp import web
    import folder_paths
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_library', False):
        return
    server._freevideo_library = True
    thumbnails = asyncio.Semaphore(1)

    def release():
        try:
            result = release_earlier(folder_paths.get_output_directory())
            if result and result['freed_bytes']:
                logging.info('FreeVideo freed %.1f GB of decoded frames and audio from %d earlier videos.',
                             result['freed_bytes'] / 1e9, result['videos'])
        except Exception:
            logging.warning('FreeVideo could not free the decoded files of earlier videos.', exc_info=True)
        try:
            freed = clean_interrupted(folder_paths.get_output_directory())
            if freed:
                logging.info('FreeVideo removed %.1f MB left by an interrupted share or export.', freed / 1e6)
        except Exception:
            logging.warning('FreeVideo could not remove files left by an interrupted share or export.', exc_info=True)
        try:
            # A deletion the last session did not finish: its undo time is long over.
            finish_deleted(folder_paths.get_output_directory(), everything=True)
        except Exception:
            logging.warning('FreeVideo could not finish removing deleted creations.', exc_info=True)
    threading.Thread(target=release, name='freevideo-release-decoded', daemon=True).start()

    @server.routes.get('/freevideo/sampling-estimate')
    async def sampling_estimate(request):
        from .comfy_bridge import installation
        from .effort_forecast import estimate, local_records
        from .geometry import geometry
        try:
            canvas = geometry(int(request.query['width']), int(request.query['height']),
                              seconds=float(request.query['seconds']))
            from .two_pass import SCALES, plan, sampling_canvas
            canvas = sampling_canvas(canvas)[0]
            task = request.query.get('task', 't2va')
            _, machine = installation()
            device = machine.get('device_identity') if machine.get('device_backend') == 'mps' else machine.get('gpu_uuid')
            rows = await asyncio.to_thread(local_records, folder_paths.get_output_directory(), device)
            adapters = request.query.get('adapters') == '1'
            # A small canvas runs in one pass whatever the switch says. A cached
            # level is keyed "<steps>:<cache>", a two-pass upscale factor other
            # than 2 "<steps>x<factor>". Without a run of it, plain runs stand
            # in: the first-block cache is counted at full time, since the
            # planner may not keep it on this card.
            share = {'fbcache': 1.}

            def one(steps, cache, enabled, factor):
                sampled = plan(canvas, enabled, task, base_steps=steps, scale=factor)
                return estimate(rows, canvas, base_steps=steps, two_pass=sampled['enabled'], task=task,
                                adapters=adapters, step_cache=cache, cache_share=share.get(cache),
                                upscale_factor=sampled.get('scale', 2))
            result = {name: {(str(steps) + (':' + cache if cache else '') + ('x%g' % factor if factor != 2 else '')):
                             one(steps, cache, enabled, factor)
                             for steps, cache, factor in ([(8, None, factor) for factor in SCALES] if enabled else
                                                          ((8, None, 2), (12, None, 2), (16, None, 2), (20, None, 2),
                                                           (20, 'easycache-av', 2), (20, 'fbcache', 2)))}
                      for name, enabled in (('single', False), ('two_pass', True))}
            return web.json_response(result, headers={'Cache-Control': 'no-store'})
        except (OSError, ValueError, TypeError, KeyError):
            # Estimation is advisory; installation and generation remain usable.
            return web.json_response({}, headers={'Cache-Control': 'no-store'})

    @server.routes.get('/freevideo/request-assets')
    async def request_assets(request):
        """What this request still needs downloaded; advisory and read-only."""
        from .comfy_bridge import installation
        from .request_assets import inspect
        query = request.query
        try:
            root, machine = installation()
            value = dict(width=int(query['width']), height=int(query['height']), seconds=float(query['seconds']),
                         task=query.get('task', 't2va'), base_steps=int(query.get('base_steps', '8')),
                         refine_steps=int(query.get('refine_steps', '3')), two_pass=query.get('two_pass', '1') == '1',
                         enhance=query.get('enhance') == '1', loras=query.getall('lora', [])[:32])
            refs = []
            for item in query.getall('ref', [])[:32]:
                kind, _, name = item.partition(':')
                parts = Path(name).parts
                silent = kind == 'video-silent'
                if (kind in ('image', 'video', 'audio') or silent) and parts and not Path(name).is_absolute() and '..' not in parts:
                    refs.append(dict(kind='video', path=name, sound=False) if silent else dict(kind=kind, path=name))
            if refs:
                # The generation path's own classification decides the tables (audio track in a video).
                value.update(references=refs, media_base=folder_paths.get_input_directory())
            names = await asyncio.to_thread(folder_paths.get_filename_list, 'loras') if value['loras'] else None
            result = await asyncio.to_thread(inspect, root, machine, value, lora_names=names)
            return web.json_response(result, headers={'Cache-Control': 'no-store'})
        except (OSError, ValueError, TypeError, KeyError):
            # Advisory: generation still reports whatever it needs.
            return web.json_response({}, headers={'Cache-Control': 'no-store'})

    @server.routes.get('/freevideo/library')
    async def library(request):
        try:
            favorite = request.query.get('favorite')
            if favorite is not None:
                if favorite not in ('true', 'false', '1', '0'):
                    raise ValueError('Invalid favorite filter')
                favorite = favorite in ('true', '1')
            rows = await asyncio.to_thread(list_videos, folder_paths.get_output_directory(),
                                           before=request.query.get('before'),
                                           limit=int(request.query.get('limit', '24')), favorite=favorite)
            return web.json_response(rows, headers={'Cache-Control': 'no-store'})
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error)) from error

    @server.routes.get('/freevideo/library/prompt')
    async def prompt(request):
        try:
            text = await asyncio.to_thread(prompt_text, folder_paths.get_output_directory(), request.query.get('id'))
        except (OSError, ValueError, UnicodeDecodeError):
            raise web.HTTPNotFound(text='Prompt unavailable') from None
        return web.json_response(dict(prompt=text), headers={'Cache-Control': 'no-store'})

    @server.routes.post('/freevideo/library/favorite')
    async def favorite(request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or type(body.get('favorite')) is not bool:
                raise ValueError('Invalid favorite state')
            result = await asyncio.to_thread(set_favorite, folder_paths.get_output_directory(),
                                             body.get('id'), body['favorite'])
        except (ValueError, TypeError) as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        except OSError:
            raise web.HTTPNotFound(text='Saved video unavailable') from None
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    @server.routes.get('/freevideo/library/thumbnail')
    async def preview(request):
        async with thumbnails:
            try:
                data = await asyncio.to_thread(thumbnail, folder_paths.get_output_directory(), request.query.get('id'))
            except Exception:
                # The full video remains playable if its thumbnail is missing.
                raise web.HTTPNotFound(text='Thumbnail unavailable') from None
        return web.Response(body=data, content_type='image/jpeg',
                            headers={'Cache-Control': 'private, max-age=86400'})

    @server.routes.post('/freevideo/library/delete')
    async def delete(request):
        try:
            body = await request.json()
            result = await asyncio.to_thread(delete_video, folder_paths.get_output_directory(),
                                             body.get('id') if isinstance(body, dict) else None,
                                             queue=getattr(server, 'prompt_queue', None))
        except DeleteRefused as error:
            return web.json_response(dict(deleted=False, reason=error.reason, kept=error.kept[:20]), status=409)
        except (OSError, ValueError, TypeError):
            raise web.HTTPNotFound(text='Saved video unavailable') from None
        return web.json_response(dict(result, deleted=True))

    @server.routes.post('/freevideo/library/delete-batch')
    async def delete_batch(request):
        try:
            body = await request.json()
            result = await asyncio.to_thread(delete_videos, folder_paths.get_output_directory(),
                                             body.get('ids') if isinstance(body, dict) else None,
                                             queue=getattr(server, 'prompt_queue', None),
                                             batch=body.get('batch') if isinstance(body, dict) else None,
                                             extend=body.get('extend') or () if isinstance(body, dict) else ())
        except (DeleteRefused, OSError, ValueError, TypeError):
            raise web.HTTPBadRequest(text='Invalid selection') from None
        return web.json_response(result)

    @server.routes.post('/freevideo/library/undo')
    async def undo(request):
        try:
            body = await request.json()
            result = await asyncio.to_thread(undo_deleted, folder_paths.get_output_directory(),
                                             body.get('batch') if isinstance(body, dict) else None)
        except UndoBusy:
            return web.json_response(dict(restored=[], lost=[], busy=True), status=409)
        except (OSError, ValueError, TypeError):
            return web.json_response(dict(restored=[], lost=[], expired=True), status=410)
        return web.json_response(result)

    @server.routes.post('/freevideo/library/undo-state')
    async def undo_status(request):
        try:
            body = await request.json()
            result = await asyncio.to_thread(undo_state, folder_paths.get_output_directory(),
                                             body.get('batches') if isinstance(body, dict) else None)
        except (OSError, ValueError, TypeError):
            raise web.HTTPBadRequest(text='Invalid selection') from None
        return web.json_response(dict(batches=result), headers={'Cache-Control': 'no-store'})

    @server.routes.post('/freevideo/library/hold')
    async def hold(request):
        try:
            body = await request.json()
            result = await asyncio.to_thread(hold_deleted, folder_paths.get_output_directory(),
                                             body.get('batches') if isinstance(body, dict) else None,
                                             running=body.get('running', False) if isinstance(body, dict) else None)
        except (OSError, ValueError, TypeError):
            raise web.HTTPBadRequest(text='Invalid selection') from None
        return web.json_response(dict(batches=result), headers={'Cache-Control': 'no-store'})

    @server.routes.post('/freevideo/library/sizes')
    async def sizes(request):
        try:
            body = await request.json()
            result = await asyncio.to_thread(folder_sizes, folder_paths.get_output_directory(),
                                             body.get('ids') if isinstance(body, dict) else None)
        except (OSError, ValueError, TypeError):
            raise web.HTTPBadRequest(text='Invalid selection') from None
        return web.json_response(dict(sizes=result), headers={'Cache-Control': 'no-store'})

    @server.routes.get('/freevideo/library/download')
    async def download(request):
        try:
            path, name = await asyncio.to_thread(video_download, folder_paths.get_output_directory(), request.query.get('id'))
        except (OSError, ValueError, TypeError):
            raise web.HTTPNotFound(text='Saved video unavailable') from None
        return web.FileResponse(path, headers={'Content-Type': 'video/mp4',
            'Content-Disposition': 'attachment; filename="%s"' % name,
            'Cache-Control': 'private, no-store'})
