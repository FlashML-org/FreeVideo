"""Stream browser media to ComfyUI input without buffering a clip in RAM."""
import asyncio
import json
from pathlib import Path, PureWindowsPath
import shutil
import uuid

from .comfy_assets import media_kind

MAX_BYTES = 2 * 1024 ** 3
CHUNK_BYTES = 256 * 1024


class UploadError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


async def receive(reader, input_directory):
    part = await reader.next()
    if part is None or part.name != 'file' or not part.filename:
        raise UploadError('Choose a media file', 'unsupported')
    name = PureWindowsPath(part.filename).name
    if not name or name in ('.', '..') or any(c in name for c in ('/', '\\', ':', '\x00')):
        raise UploadError('Invalid media filename', 'invalid')
    try:
        media_kind(name)
    except ValueError as error:
        raise UploadError(str(error), 'unsupported') from error
    base = Path(input_directory).resolve()
    folder = base / 'freevideo' / uuid.uuid4().hex
    folder.resolve().relative_to(base)
    folder.mkdir(parents=True, exist_ok=False)
    # An upload that fails or is interrupted leaves nothing behind.
    try:
        temporary = folder / (name + '.partial')
        destination = folder / name
        size = 0
        with temporary.open('xb') as output:
            while True:
                chunk = await part.read_chunk(size=CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise UploadError('Media file exceeds 2 GiB; trim the clip before uploading', 'too_large')
                output.write(chunk)
        if size == 0:
            raise UploadError('Media file is empty', 'empty')
        if await reader.next() is not None:
            raise UploadError('Upload one media file at a time', 'multiple')
        from .file_ops import publish
        publish(temporary, destination)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    result = {'file': destination.relative_to(base).as_posix(), 'bytes': size}
    if media_kind(name) == 'video':
        # Whether the clip has a sound track; the Replace person option is greyed out without one.
        try:
            from .guide_audio import has_audio
            result['audio'] = await asyncio.to_thread(has_audio, destination)
        except Exception:
            pass
    return result


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
            return web.json_response({'error': str(error), 'code': getattr(error, 'code', 'failed')}, status=400)
        return web.json_response(result)

    from .lora_triggers import get_trigger, set_trigger

    @server.routes.get('/freevideo/lora/triggers')
    async def lora_triggers(request):
        try:
            result = await asyncio.to_thread(get_trigger, request.query.get('name'), folders=folder_paths)
        except (OSError, ValueError) as error:
            return web.json_response({'error': str(error)}, status=400)
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    @server.routes.post('/freevideo/lora/triggers')
    async def save_lora_triggers(request):
        try:
            value = await request.json()
            if not isinstance(value, dict):
                raise ValueError('A JSON object is required')
            result = await asyncio.to_thread(set_trigger, value.get('name'), value.get('words'),
                                             folders=folder_paths)
        except (OSError, ValueError, TypeError) as error:
            return web.json_response({'error': str(error)}, status=400)
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    @server.routes.get('/freevideo/lora/check')
    async def lora_check(request):
        from .comfy_assets import resolve_loras
        from .lora_check import check_installed_file
        try:
            name = request.query.get('name')
            path = resolve_loras(json.dumps([dict(name=name)]), folder_paths.get_filename_list('loras'),
                                 lambda n: folder_paths.get_full_path_or_raise('loras', n))[0]['path']
            result = await asyncio.to_thread(check_installed_file, path)
        except (OSError, ValueError, TypeError) as error:
            return web.json_response({'error': str(error)}, status=400)
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    @server.routes.post('/freevideo/media/audio-use')
    async def audio_use(request):
        from .audio_use_request import selection
        from .original_audio import AudioUseError
        try:
            value = await request.json()
            result = await asyncio.to_thread(selection, value, folder_paths.get_input_directory())
        except AudioUseError as error:
            return web.json_response(error.as_dict(), status=400)
        except (OSError, ValueError) as error:
            return web.json_response({'error': str(error)}, status=400)
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    server._freevideo_upload_registered = True
