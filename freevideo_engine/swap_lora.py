"""On-demand download of the Replace person LoRA into the LoRA folder.

Nothing here ships in the installer. The catalog (`swap_lora.json`) pins the
Hugging Face revision, and every file's size and sha256; its licence asks that
the licence text travel with the weights, so the licence file is fetched first
and the weights land beside it under the name Replace person looks for.

The transfer is the image pack's: the same measured source order, the user's
download source and proxy settings (Hugging Face, its mirror, or our ModelScope
copy of the same files), resumable partials and sha256 checks. A copy of the
LoRA that is already registered, at any depth of the LoRA folders, is used as it
is and never downloaded again.
"""
import json
from pathlib import Path, PurePosixPath
import threading
import time

from . import disk_space

RESERVE = 256 * 2**20


class PackError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def catalog():
    return json.loads(Path(__file__).with_name('swap_lora.json').read_text(encoding='utf-8'))


def present(names, spec=None):
    """Whether a registered LoRA name is the Replace person LoRA (by its file name, in any subfolder)."""
    wanted = (spec or catalog())['name'].lower()
    return any(isinstance(n, str) and PurePosixPath(n.replace('\\', '/')).name.lower() == wanted for n in names or ())


def remote(spec, row):
    """One catalog file on the pinned Hugging Face revision (official host or its mirror) or our ModelScope copy."""
    from .network import source_url
    path = '/%s/resolve/%s/%s' % (spec['repo'], spec['revision'], row['file'])
    sources = [dict(id=name, url=source_url('models', name) + path) for name in ('official', 'hf-mirror')]
    sources.append(dict(id='modelscope', url=spec['modelscope'] + '/' + row['target']))
    return dict(repo=spec['repo'], revision=spec['revision'], file=row['file'], family='image',
                sha256=row['sha256'], bytes=row['bytes'], sources=sources)


def _intact(path, row, check):
    from .depth_reference import digest
    try:
        return path.is_file() and path.stat().st_size == row['bytes'] and digest(path, check) == row['sha256']
    except OSError:
        return False


def _partial(path):
    return path.with_name(path.name + '.partial')


def install(folder, names=(), progress=None, check=None, *, engine_root=None, spec=None):
    """Fetch the licence and the LoRA into folder; skip when the LoRA is already registered or on disk."""
    from . import network
    from .image_gen.image_pack import PackError as TransferError, _download_plan, _transfer
    from .failure_details import image_pack_error_code
    from .locking import runtime_lock
    from .paths import data_root
    spec = spec or catalog()
    progress = progress or (lambda value: None)
    check = check or (lambda: None)
    folder = Path(folder)
    weights = folder / spec['files'][-1]['target']
    if present(names, spec) or weights.exists():
        return 'present'
    try:
        lock_root = Path(engine_root) if engine_root is not None else data_root()
        with runtime_lock(lock_root / 'swap-lora-download.lock', inherit=False):
            folder.mkdir(parents=True, exist_ok=True)
            todo = []
            for row in spec['files']:
                check()
                path = folder / row['target']
                if row is spec['files'][-1] and path.exists():
                    return 'present'
                if _intact(path, row, check):
                    continue
                if path.exists() or path.is_symlink():
                    # Not the pinned file: kept under another name, never overwritten.
                    network.retain_partial(path, 'rejected')
                todo.append(row)
            total = sum(row['bytes'] for row in todo)
            retained = sum(min(_partial(folder / r['target']).stat().st_size, r['bytes'])
                           for r in todo if _partial(folder / r['target']).is_file())
            needed = total - retained + RESERVE
            if total and disk_space.free_bytes(folder) < needed:
                raise PackError('disk_space')
            plan = _download_plan(engine_root)
            plan.update(quiet=True, resource_check=check)
            done = 0
            progress(dict(done=0, total=total))
            for row in todo:
                check()

                def moved(current, size, speed, **kwargs):
                    progress(dict(done=done + current, total=total))
                    check()
                _transfer(plan, remote(spec, row), folder / row['target'], moved, check)
                # The record of which source the partial came from is only for resuming it.
                _partial(folder / row['target']).with_name(row['target'] + '.partial.source.json').unlink(missing_ok=True)
                done += row['bytes']
                progress(dict(done=done, total=total))
            return 'installed'
    except (PackError, InterruptedError):
        raise
    except TransferError as error:
        raise PackError(error.reason) from error
    except BlockingIOError as error:
        raise PackError('busy') from error
    except Exception as error:
        raise PackError(image_pack_error_code(error)) from error


def lora_folder():
    import folder_paths
    return Path(folder_paths.get_folder_paths('loras')[0])


def lora_names():
    import folder_paths
    return folder_paths.get_filename_list('loras')


class Installer:
    """One background download at a time; the Studio polls `state()`."""

    def __init__(self, folder=None, names=None, engine_root=None):
        self.folder, self.names, self.engine_root = folder or lora_folder, names or lora_names, engine_root
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.value = dict(state='idle', done=0, total=0, reason=None)

    def state(self):
        with self.lock:
            return dict(self.value)

    def _set(self, **kwargs):
        with self.lock:
            self.value.update(kwargs)

    def status(self):
        spec = catalog()
        try:
            found = present(self.names(), spec)
        except Exception:
            found = False
        return dict(name=spec['name'], bytes=sum(r['bytes'] for r in spec['files']), present=found, install=self.state())

    def start(self):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return dict(self.value)
            self.stop = threading.Event()
            self.value = dict(state='running', done=0, total=0, reason=None, started=time.time())
            self.thread = threading.Thread(target=self._run, args=(self.stop,), name='freevideo-swap-lora', daemon=True)
            self.thread.start()
            return dict(self.value)

    def cancel(self):
        self.stop.set()
        return self.state()

    def _run(self, stop):
        def check():
            if stop.is_set():
                raise InterruptedError('cancelled')

        def progress(value):
            self._set(done=value.get('done', 0), total=value.get('total', 0))
        try:
            install(self.folder(), self.names(), progress, check, engine_root=self.engine_root)
            self._set(state='done', reason=None)
        except InterruptedError:
            self._set(state='cancelled', reason=None)
        except PackError as error:
            self._set(state='failed', reason=error.reason)
        except Exception:
            self._set(state='failed', reason='failed')


def register():
    """GET /freevideo/swap-lora: whether the LoRA is in place and the download state; POST {action: install|cancel}."""
    import asyncio
    from aiohttp import web
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_swap_lora', None) is not None:
        return
    installer = server._freevideo_swap_lora = Installer()

    def reply(value, status=200):
        return web.json_response(value, status=status, headers={'Cache-Control': 'no-store'})

    @server.routes.get('/freevideo/swap-lora')
    async def swap_lora_status(request):
        return reply(await asyncio.to_thread(installer.status))

    @server.routes.post('/freevideo/swap-lora')
    async def swap_lora_install(request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {'action'} or body['action'] not in ('install', 'cancel'):
                raise ValueError('Invalid request')
        except (ValueError, TypeError):
            return reply({'reason': 'failed'}, 400)
        if body['action'] == 'cancel':
            installer.cancel()
        elif not (await asyncio.to_thread(installer.status))['present']:
            installer.start()
        return reply(await asyncio.to_thread(installer.status))
