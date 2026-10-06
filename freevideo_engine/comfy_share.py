"""Explicit local sharing exports. Original videos are opened read-only.

The browser supplies a small, text-only presentation frame using its own fonts.
Video composition uses CPU PyAV and copies the existing audio packets. No model,
CUDA context, cloud service or full report is involved. The saved ComfyUI
workflow travels with both exports, so dropping one on the canvas restores it.
"""
import asyncio
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import threading
import uuid

from .comfy_library import _video, _report


def saved_graph(path):
    """The workflow and prompt a saved video carries, as ComfyUI stores them (JSON text)."""
    import av
    try:
        with av.open(str(path)) as reader:
            graph = {key: value for key, value in reader.metadata.items() if key in ('workflow', 'prompt') and value}
    except Exception:
        graph = {}
    if 'workflow' not in graph:
        # Videos saved before the workflow was embedded keep it next to them.
        from .comfy_metadata import saved_workflow
        workflow = saved_workflow(path.with_name('workflow.json'))
        if workflow is not None:
            graph['workflow'] = json.dumps(workflow)
    return graph


def details(root, identity):
    import av
    from .comfy_assets import output_summary
    path = _video(root, identity)
    report = _report(path)
    summary = output_summary(report, Path('FreeVideo')/identity/'video.mp4')
    hardware = report.get('profile', {}).get('policy', {}).get('hardware', {})
    numeric = ('sample_seconds', 'request_seconds', 'vram_peak_bytes', 'ram_peak_bytes', 'unified_total_bytes')
    result = {key: summary[key] if type(summary.get(key)) in (int, float)
              and math.isfinite(summary[key]) and summary[key] >= 0 else None for key in numeric}
    result['gpu'] = str(hardware.get('gpu_name') or '')[:120]
    result['memory_model'] = 'unified' if summary.get('memory_model') == 'unified' else 'dedicated'
    result['geometry'] = {k: v for k, v in (summary.get('geometry') or {}).items()
                          if k in ('width', 'height', 'frames', 'fps', 'seconds')
                          and type(v) in (float, int) and math.isfinite(v) and v > 0}
    # Old library items may not have geometry in their report. Read only the
    # container header; the saved video determines the presentation's shape.
    with av.open(str(path)) as reader:
        stream = reader.streams.video[0]
        result['geometry'].update(width=stream.width, height=stream.height)
        if stream.duration is not None and stream.time_base is not None:
            result['geometry']['seconds'] = float(stream.duration * stream.time_base)
    plan = summary.get('sampling_plan') or {}
    result['sampling_plan'] = {k: v for k, v in plan.items() if k in ('enabled', 'base_steps', 'refine_steps')
                               and type(v) in (int, bool)}
    result['id'] = identity
    result['graph'] = saved_graph(path)
    return result


def first_frame(root, identity):
    import av
    from PIL import Image
    path = _video(root, identity)
    _report(path)
    with av.open(str(path)) as reader:
        stream = reader.streams.video[0]; stream.thread_count = 1
        frame = next(reader.decode(stream)).to_image()
    frame.thumbnail((1600, 1800), Image.Resampling.LANCZOS)
    data = io.BytesIO(); frame.save(data, 'JPEG', quality=94)
    return data.getvalue()


def layout(data):
    """Bound dimensions, decoded bytes and media placement before encoding."""
    from PIL import Image
    value = data.get('template')
    if not isinstance(value, str) or len(value) > 3 * 1024 * 1024:
        raise ValueError('Invalid sharing frame')
    encoded = base64.b64decode(value, validate=True)
    with Image.open(io.BytesIO(encoded)) as image:
        width, height = image.size
        if image.format != 'PNG' or not (320 <= width <= 1920 and 320 <= height <= 2400
                                        and width * height <= 4_000_000 and width % 2 == height % 2 == 0):
            raise ValueError('Invalid sharing frame size')
        image.load(); template = image.convert('RGBA')
    rect = data.get('rect')
    if not isinstance(rect, list) or len(rect) != 4 or any(type(v) is not int for v in rect):
        raise ValueError('Invalid video placement')
    x, y, w, h = rect
    if x < 0 or y < 0 or min(w, h) < 32 or x + w > width or y + h > height:
        raise ValueError('Video placement exceeds sharing frame')
    return template, (x, y, w, h), hashlib.sha256(encoded + json.dumps(rect).encode()).hexdigest()[:20]


def video_export(root, identity, data, stop):
    import av
    from PIL import Image
    path = _video(root, identity)
    _report(path)
    info = path.stat()
    template, rect, key = layout(data)
    key = hashlib.sha256(f'{key}:{info.st_size}:{info.st_mtime_ns}:v2'.encode()).hexdigest()[:20]
    target = path.with_name('video.share-' + key + '.mp4')
    if target.is_symlink():
        raise ValueError('Invalid sharing destination')
    if target.is_file() and target.stat().st_size:
        return target, key
    temporary = path.with_name('.share-' + uuid.uuid4().hex + '.mp4')
    x, y, width, height = rect
    try:
        with av.open(str(path)) as reader, av.open(str(temporary), 'w', format='mp4',
                                                   options={'movflags': 'use_metadata_tags+faststart'}) as writer:
            for name, value in saved_graph(path).items():
                writer.metadata[name] = value
            source = reader.streams.video[0]; source.thread_count = 1
            if abs(width / height - source.width / source.height) > 2 / height:
                raise ValueError('Sharing must preserve the original aspect ratio')
            rate = source.average_rate or source.guessed_rate
            if not rate or not 1 <= float(rate) <= 120:
                raise ValueError('Unsupported video frame rate')
            video = writer.add_stream('libx264', rate=rate)
            video.width, video.height = template.size
            video.pix_fmt = 'yuv420p'; video.codec_context.thread_count = 2
            video.options = {'crf': '18', 'preset': 'fast'}
            audio_source = next(iter(reader.streams.audio), None)
            audio = writer.add_stream_from_template(audio_source) if audio_source else None
            streams = [source] + ([audio_source] if audio_source else [])
            base = Image.new('RGB', template.size, '#111720'); base.paste(template, mask=template.getchannel('A'))
            count = 0
            for packet in reader.demux(streams):
                if stop.is_set():
                    raise InterruptedError('Sharing export cancelled')
                if packet.stream == source:
                    for frame in packet.decode():
                        if stop.is_set(): raise InterruptedError('Sharing export cancelled')
                        image = base.copy()
                        image.paste(frame.to_image().resize((width, height), Image.Resampling.LANCZOS), (x, y))
                        rendered = av.VideoFrame.from_image(image)
                        rendered.pts, rendered.time_base = frame.pts, frame.time_base
                        for output in video.encode(rendered): writer.mux(output)
                        count += 1
                elif packet.dts is not None and audio is not None:
                    packet.stream = audio; writer.mux(packet)
            for packet in video.encode(): writer.mux(packet)
            if not count: raise ValueError('Source video has no frames')
        if stop.is_set(): raise InterruptedError('Sharing export cancelled')
        temporary.replace(target)
        return target, key
    finally:
        temporary.unlink(missing_ok=True)


def register():
    from aiohttp import web
    import folder_paths
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_share', False): return
    server._freevideo_share = True
    exports = asyncio.Semaphore(1)

    @server.routes.get('/freevideo/share')
    async def metadata(request):
        try:
            result = await asyncio.to_thread(details, folder_paths.get_output_directory(), request.query.get('id'))
            return web.json_response(result, headers={'Cache-Control': 'no-store'})
        except (OSError, ValueError, TypeError, KeyError):
            raise web.HTTPNotFound(text='Saved video unavailable') from None

    @server.routes.get('/freevideo/share/frame')
    async def preview(request):
        async with exports:
            try:
                data = await asyncio.to_thread(first_frame, folder_paths.get_output_directory(), request.query.get('id'))
            except Exception:
                raise web.HTTPNotFound(text='First frame unavailable') from None
        return web.Response(body=data, content_type='image/jpeg', headers={'Cache-Control': 'private, no-store'})

    @server.routes.post('/freevideo/share/video')
    async def export(request):
        if exports.locked(): raise web.HTTPConflict(text='Another sharing export is running')
        if request.content_length is None or request.content_length > 3 * 1024 * 1024:
            raise web.HTTPBadRequest(text='Invalid sharing request size')
        try: data = await request.json()
        except (ValueError, TypeError): raise web.HTTPBadRequest(text='Invalid sharing request') from None
        if not isinstance(data, dict): raise web.HTTPBadRequest(text='Invalid sharing request')
        identity = data.get('id'); stop = threading.Event()
        async with exports:
            task = asyncio.create_task(asyncio.to_thread(video_export, folder_paths.get_output_directory(), identity, data, stop))
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=.25)
                    if request.transport is None or request.transport.is_closing(): stop.set()
                _, key = await task
                return web.json_response({'id': identity, 'key': key})
            except asyncio.CancelledError:
                stop.set()
                try: await asyncio.shield(task)
                except (InterruptedError, OSError): pass
                raise
            except (ValueError, TypeError, OSError, InterruptedError):
                raise web.HTTPBadRequest(text='Sharing export could not complete') from None

    @server.routes.get('/freevideo/share/video')
    async def download(request):
        import re
        identity, key = request.query.get('id'), request.query.get('key', '')
        try:
            path = _video(folder_paths.get_output_directory(), identity); _report(path)
            if not re.fullmatch('[0-9a-f]{20}', key): raise ValueError('Invalid export')
            path = path.with_name('video.share-' + key + '.mp4')
            if not path.is_file() or path.is_symlink(): raise ValueError('Missing export')
        except (ValueError, TypeError, OSError):
            raise web.HTTPNotFound(text='Sharing export unavailable') from None
        return web.FileResponse(path, headers={'Content-Type': 'video/mp4', 'Cache-Control': 'private, no-store',
            'Content-Disposition': 'attachment; filename="FreeVideo_share_%s.mp4"' % identity.split('/')[-1][:12]})
