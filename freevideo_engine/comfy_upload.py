"""Stream browser media to ComfyUI input without buffering a clip in RAM."""
import asyncio
import errno
import json
import logging
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import uuid

from .comfy_assets import media_kind

MAX_BYTES = 2 * 1024 ** 3
CHUNK_BYTES = 256 * 1024


class UploadError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


async def receive(reader, input_directory, *, store_root=None):
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
    from . import private_store
    base = Path(input_directory).resolve()
    # One copy per content: the folder is a keyed hash of the bytes and the file has a plain name, so neither
    # says what the user called it. The page keeps that name with the item that uses the file.
    suffix = PurePosixPath(name).suffix.lower()
    staging = base / 'freevideo' / uuid.uuid4().hex
    staging.resolve().relative_to(base)
    staging.mkdir(parents=True, exist_ok=False)
    # An upload that fails or is interrupted leaves nothing behind.
    try:
        temporary = staging / ('media' + suffix + '.partial')
        digest = private_store.hasher('upload' + suffix, root=store_root)
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
                digest.update(chunk)
        if size == 0:
            raise UploadError('Media file is empty', 'empty')
        if await reader.next() is not None:
            raise UploadError('Upload one media file at a time', 'multiple')
        folder = base / 'freevideo' / digest.hexdigest()[:32]
        destination = folder / ('media' + suffix)
        from .file_ops import publish
        with private_store.LOCK:
            existing, _ = private_store.check(base, destination.relative_to(base).as_posix())
            if existing is not None and existing[1].st_size == size:
                # The same bytes are already here: keep that one copy, newly used.
                os.utime(destination)
            else:
                if os.path.lexists(destination) or (os.path.lexists(folder) and not folder.is_dir()):
                    # Something FreeVideo would not reuse holds that name: this upload gets a folder of its own.
                    folder = base / 'freevideo' / uuid.uuid4().hex
                    destination = folder / ('media' + suffix)
                folder.mkdir(exist_ok=True)
                try:
                    publish(temporary, destination)
                except BaseException:
                    try:
                        folder.rmdir()
                    except OSError:
                        pass
                    raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    result = {'file': destination.relative_to(base).as_posix(), 'bytes': size, 'name': name}
    if media_kind(name) == 'video':
        # Whether the clip has a sound track; the Replace person option is greyed out without one.
        try:
            from .guide_audio import has_audio
            result['audio'] = await asyncio.to_thread(has_audio, destination)
        except Exception:
            pass
    return result


def failure_body(error):
    """The JSON body and status for a failed upload. An OS error's raw text can hold the user's path, so the page gets a reason it can act on."""
    if isinstance(error, OSError):
        from .disk_space import ran_out
        code = 'disk_full' if ran_out(error) else 'not_writable' if isinstance(error, PermissionError) or error.errno in (errno.EACCES, errno.EROFS) else 'failed'
        logging.getLogger(__name__).warning('Media upload failed (%s, %s).', type(error).__name__, code, exc_info=error if code == 'failed' else None)
        return {'error': 'Upload failed', 'code': code}, 507 if code == 'disk_full' else 400
    return {'error': str(error), 'code': getattr(error, 'code', 'failed')}, 400


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
            body, status = failure_body(error)
            return web.json_response(body, status=status)
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
            if request.query.get('kind') == 'image':
                from .image_gen.loras import check_named_file
                result = await asyncio.to_thread(check_named_file, name, folders=folder_paths)
            else:
                path = resolve_loras(json.dumps([dict(name=name)]), folder_paths.get_filename_list('loras'),
                                     lambda n: folder_paths.get_full_path_or_raise('loras', n))[0]['path']
                result = await asyncio.to_thread(check_installed_file, path)
        except (OSError, ValueError, TypeError) as error:
            if request.query.get('kind') == 'image':
                from .image_gen.loras import MESSAGE_ZH
                return web.json_response({'error': str(error),
                                          'message_zh': MESSAGE_ZH.get(str(error), MESSAGE_ZH['invalid_loras'])}, status=400)
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

    from . import private_refs, private_store

    @server.routes.post('/freevideo/private/pin')
    async def private_pin(request):
        # The files an open page shows: only the page knows them, and they stay while it has them.
        try:
            value = await request.json()
            if not isinstance(value, dict):
                raise ValueError('A JSON object is required')
            count = await asyncio.to_thread(private_store.pin, value.get('page'), value.get('files'))
        except (OSError, ValueError, TypeError) as error:
            return web.json_response({'error': str(error)}, status=400)
        return web.json_response({'pinned': count}, headers={'Cache-Control': 'no-store'})

    @server.routes.get('/freevideo/private/cleanup')
    async def private_cleanup(request):
        result = await asyncio.to_thread(private_store.last_sweep)
        return web.json_response(dict(result, enabled=private_store.deletion_enabled()), headers={'Cache-Control': 'no-store'})

    def sweep_context():
        image = getattr(server, '_freevideo_image_gen', None)
        return dict(input_root=folder_paths.get_input_directory(), output_root=folder_paths.get_output_directory(),
                    user_root=folder_paths.get_user_directory(), queue=getattr(server, 'prompt_queue', None),
                    extra=image.held_tokens() if image is not None and hasattr(image, 'held_tokens') else ())
    private_refs.start_sweeps(sweep_context)
    private_refs.register_routes(server, web, folder_paths, sweep_context)

    server._freevideo_upload_registered = True
