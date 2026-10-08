"""Browse completed FreeVideo outputs across ComfyUI/browser restarts."""
import asyncio
import io
import json
import os
import re
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from .comfy_assets import output_summary

_ID = re.compile(r'\d{4}-\d{2}-\d{2}/[0-9a-f]{32}')


def _video(root, identity):
    if not isinstance(identity, str) or not _ID.fullmatch(identity):
        raise ValueError('Invalid saved video')
    root = Path(root).resolve()
    path = (root / 'FreeVideo' / identity / 'video.mp4').resolve()
    if not path.is_relative_to(root / 'FreeVideo'):
        raise ValueError('Saved video is outside the output folder')
    return path


def _report(path):
    # Read only the small request summary, never tensors, prompts or media.
    with path.with_suffix('.request.json').open('rb') as stream:
        data = stream.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError('Request summary is too large')
    report = json.loads(data)
    if not isinstance(report, dict) or report.get('success') is not True:
        raise ValueError('Video is not complete')
    return report


def list_videos(output_directory, *, before=None, limit=24):
    """Return a stable newest-first page, with only relative output paths."""
    if not 1 <= limit <= 48:
        raise ValueError('Invalid page size')
    cursor = None
    if before:
        stamp, identity = before.split(':', 1)
        if not stamp.isdigit() or not _ID.fullmatch(identity):
            raise ValueError('Invalid page cursor')
        cursor = (int(stamp), identity)
    root = Path(output_directory).resolve()
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
    rows = []
    for key, path, info in sorted(candidates, key=lambda item: item[0], reverse=True):
        try:
            report = _report(path)
            relative = Path('FreeVideo') / key[1] / 'video.mp4'
            summary = output_summary(report, relative)
            # Geometry may gain input metadata in future reports. Keep this API
            # an explicit allowlist, not an export of the retained report.
            summary['geometry'] = {k: v for k, v in summary['geometry'].items()
                                   if k in ('width', 'height', 'frames', 'fps', 'seconds')
                                   and type(v) in (int, float)}
            if not (root / summary['report']).is_file():
                summary['report'] = None
            rows.append(dict(summary, id=key[1], bytes=info.st_size,
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
    return dict(items=rows, next=next_cursor)


def thumbnail(output_directory, identity):
    path = _video(output_directory, identity)
    _report(path)
    saved = path.with_suffix('.thumbnail.jpg')
    if saved.is_file() and saved.stat().st_mtime_ns >= path.stat().st_mtime_ns:
        data = saved.read_bytes()
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
    try:
        saved.write_bytes(data)
    except OSError:
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
                         r'|media\.json|prompt-rewrite\.json|input-conditioning\.pt|(?:first|last)\.png'
                         r'|reference-\d{2}\.(?:png|mp4|wav))(?:\.[a-z0-9_]{8}\.tmp)?')
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
        elif not _OWNED_FILE.fullmatch(entry.name):
            kept.append(entry.name)
    return kept


def _remove(folder):
    """Remove a verified folder bottom-up; return bytes freed and paths left behind."""
    freed, left = 0, []
    for parent, folders, files in os.walk(folder, topdown=False):
        for name in files:
            path = Path(parent) / name
            try:
                if not _plain(path):
                    left.append(str(path)); continue
                size = path.stat().st_size
                path.unlink()
                freed += size
            except OSError:
                left.append(str(path))
        for name in folders:
            try:
                (Path(parent) / name).rmdir()
            except OSError:
                left.append(str(Path(parent) / name))
    try:
        Path(folder).rmdir()
    except OSError:
        left.append(str(folder))
    return freed, left


def delete_video(output_directory, identity):
    """Delete one saved creation: its whole run folder, only when FreeVideo wrote everything in it.

    The folder is first renamed into FreeVideo/.deleted, so it leaves the
    library at once, or, when Windows still has a file open, nothing changes.
    """
    path = _video(output_directory, identity)
    _report(path)
    root = Path(output_directory).resolve() / 'FreeVideo'
    folder = path.parent
    if folder.parent.parent != root or not _plain(folder.parent) or not _plain(folder):
        raise DeleteRefused('not-freevideo')
    kept = _foreign(folder)
    if kept:
        raise DeleteRefused('foreign-files', kept)
    # A second pass of this preview may be reading its first pass right now.
    for state_path in root.glob('*/*/comfy-request.json'):
        try:
            state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.stat().st_size <= 1024 * 1024 else {}
        except (OSError, ValueError):
            continue
        source = state.get('upscaled_preview') if isinstance(state, dict) else None
        if state.get('status') in ('starting', 'running') and isinstance(source, str) and Path(source).resolve() == path:
            raise DeleteRefused('in-use')
    trash = root / '.deleted'
    trash.mkdir(exist_ok=True)
    if not _plain(trash):
        raise DeleteRefused('not-freevideo')
    target = trash / ('%s-%d' % (identity.replace('/', '-'), time.time_ns()))
    try:
        folder.rename(target)
    except OSError as error:
        raise DeleteRefused('in-use') from error
    freed, left = _remove(target)
    # A result-cache row naming this creation can no longer be reused.
    for index in (root / '.result-cache').glob('*.json'):
        try:
            if index.stat().st_size <= 1024 * 1024 and json.loads(index.read_text(encoding='utf-8')).get('id') == identity:
                index.unlink()
        except (OSError, ValueError, AttributeError):
            pass
    # Finish earlier deletions that Windows had kept open.
    for earlier in trash.iterdir():
        if earlier != target and _plain(earlier) and earlier.is_dir():
            freed += _remove(earlier)[0]
    return dict(id=identity, freed_bytes=freed, left=len(left))


def register():
    from aiohttp import web
    import folder_paths
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_library', False):
        return
    server._freevideo_library = True
    thumbnails = asyncio.Semaphore(1)

    @server.routes.get('/freevideo/sampling-estimate')
    async def sampling_estimate(request):
        from .comfy_bridge import installation
        from .effort_forecast import estimate, local_records
        from .geometry import geometry
        try:
            canvas = geometry(int(request.query['width']), int(request.query['height']),
                              seconds=float(request.query['seconds']))
            _, machine = installation()
            device = machine.get('device_identity') if machine.get('device_backend') == 'mps' else machine.get('gpu_uuid')
            rows = await asyncio.to_thread(local_records, folder_paths.get_output_directory(), device)
            task = request.query.get('task', 't2va')
            adapters = request.query.get('adapters') == '1'
            result = {name: {str(steps): estimate(rows, canvas, base_steps=steps, two_pass=enabled,
                                                 task=task, adapters=adapters)
                              for steps in ((8,) if enabled else (8, 12, 16, 20))}
                      for name, enabled in (('single', False), ('two_pass', True))}
            return web.json_response(result, headers={'Cache-Control': 'no-store'})
        except (OSError, ValueError, TypeError, KeyError):
            # Estimation is advisory; installation and generation remain usable.
            return web.json_response({}, headers={'Cache-Control': 'no-store'})

    @server.routes.get('/freevideo/library')
    async def library(request):
        try:
            rows = await asyncio.to_thread(list_videos, folder_paths.get_output_directory(),
                                           before=request.query.get('before'),
                                           limit=int(request.query.get('limit', '24')))
            return web.json_response(rows, headers={'Cache-Control': 'no-store'})
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error)) from error

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
                                             body.get('id') if isinstance(body, dict) else None)
        except DeleteRefused as error:
            return web.json_response(dict(deleted=False, reason=error.reason, kept=error.kept[:20]), status=409)
        except (OSError, ValueError, TypeError):
            raise web.HTTPNotFound(text='Saved video unavailable') from None
        return web.json_response(dict(result, deleted=True))

    @server.routes.get('/freevideo/library/download')
    async def download(request):
        try:
            path, name = await asyncio.to_thread(video_download, folder_paths.get_output_directory(), request.query.get('id'))
        except (OSError, ValueError, TypeError):
            raise web.HTTPNotFound(text='Saved video unavailable') from None
        return web.FileResponse(path, headers={'Content-Type': 'video/mp4',
            'Content-Disposition': 'attachment; filename="%s"' % name,
            'Cache-Control': 'private, no-store'})
