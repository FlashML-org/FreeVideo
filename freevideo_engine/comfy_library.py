"""Browse completed FreeVideo outputs across ComfyUI/browser restarts."""
import asyncio
import io
import json
import math
import re
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


def _finite_numbers(value):
    # Retained reports can contain non-standard JSON numbers. Keep the saved
    # video browsable, treating unknown measurements as null rather than
    # emitting NaN/Infinity that the browser cannot parse.
    if isinstance(value, dict):
        return {key: _finite_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite_numbers(item) for item in value]
    return None if isinstance(value, float) and not math.isfinite(value) else value


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
                                   and type(v) in (int, float)
                                   and (type(v) is int or math.isfinite(v))}
            if not (root / summary['report']).is_file():
                summary['report'] = None
            summary = _finite_numbers(summary)
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


def register():
    from aiohttp import web
    import folder_paths
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_library', False):
        return
    server._freevideo_library = True
    thumbnails = asyncio.Semaphore(1)

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

    @server.routes.get('/freevideo/library/download')
    async def download(request):
        try:
            path, name = await asyncio.to_thread(video_download, folder_paths.get_output_directory(), request.query.get('id'))
        except (OSError, ValueError, TypeError):
            raise web.HTTPNotFound(text='Saved video unavailable') from None
        return web.FileResponse(path, headers={'Content-Type': 'video/mp4',
            'Content-Disposition': 'attachment; filename="%s"' % name,
            'Cache-Control': 'private, no-store'})
