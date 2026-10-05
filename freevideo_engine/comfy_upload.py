"""Stream browser media to ComfyUI input without buffering a clip in RAM."""
from pathlib import Path, PureWindowsPath
import uuid

from .comfy_assets import media_kind

MAX_BYTES = 2 * 1024 ** 3
CHUNK_BYTES = 256 * 1024


async def receive(reader, input_directory):
    part = await reader.next()
    if part is None or part.name != 'file' or not part.filename:
        raise ValueError('Choose a media file')
    name = PureWindowsPath(part.filename).name
    if not name or name in ('.', '..') or any(c in name for c in ('/', '\\', ':', '\x00')):
        raise ValueError('Invalid media filename')
    media_kind(name)
    base = Path(input_directory).resolve()
    folder = base / 'freevideo' / uuid.uuid4().hex
    folder.resolve().relative_to(base)
    folder.mkdir(parents=True, exist_ok=False)
    temporary = folder / (name + '.partial')
    destination = folder / name
    published = False
    try:
        size = 0
        with temporary.open('xb') as output:
            while True:
                chunk = await part.read_chunk(size=CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('Media file exceeds 2 GiB; trim the clip before uploading')
                output.write(chunk)
        if size == 0:
            raise ValueError('Media file is empty')
        if await reader.next() is not None:
            raise ValueError('Upload one media file at a time')
        from .file_ops import publish
        publish(temporary, destination)
        published = True
        return {'file': destination.relative_to(base).as_posix(), 'bytes': size}
    finally:
        if not published:
            # This UUID folder belongs to this upload. Remove its partial,
            # including on cancellation, but preserve any unrelated files.
            try:
                temporary.unlink(missing_ok=True)
                folder.rmdir()
            except OSError:
                pass  # Cleanup must not replace the original stream error.



def register():
    from aiohttp import web
    import folder_paths
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_upload_registered', False):
        return

    @server.routes.post('/freevideo/media/upload')
    async def upload(request):
        try:
            result = await receive(await request.multipart(), folder_paths.get_input_directory())
        except (OSError, ValueError) as error:
            return web.json_response({'error': str(error)}, status=400)
        return web.json_response(result)

    server._freevideo_upload_registered = True
