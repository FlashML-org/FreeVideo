"""On-demand download of the Fast level's files into the model folder.

Nothing here ships in the installer: the files are fetched when the level is first chosen. The catalog (`fast_pack.json`)
is the one list of files: it pins each one's size and sha256, and adding a file means adding a row. The level reads the
int8 build of the four-step pixel-output adapter (Lakonik/pi-H3@4a864589, unmodified) and the shared timestep constants.
A row whose `revision` is null is not on our copies yet: it is never fetched, and the level stays unavailable until it is. A row may name its own `repo`, `revision` and `modelscope` copy, and a
`target` under the model folder when that differs from `file`. The licence and notes are fetched before the weights. The transfer is the image pack's: the measured source order, the user's
download source and proxy settings (our ModelScope copy or the same files on Hugging Face), resumable partials and
sha256 checks.
"""
import json
from pathlib import Path, PurePosixPath
import re
import threading
import time

from . import disk_space
from .long_paths import io_path
from .swap_lora import PackError

RESERVE = 256 * 2**20
MISSING = 'FAST_MODE_FILES_MISSING: '
# The receipt beside the files: what each one was when its sha256 last matched (size, times, file id). A file whose identity
# is unchanged is not hashed again; one that changed is checked again before it counts.
LEDGER = 'download-verified.json'
# A copy that did not pass, as network.retain_partial and its rejections name it: <file>[.partial].<reason>-<time_ns>,
# with the reasons they use and nothing else, so a name such as weights.bin.backup-1 is never taken for one.
FAILED = re.compile(r'(?:\.partial)?\.(?:rejected|hash-rejected|oversized|oversized-response|resume-unavailable|restart-approved)'
                    r'-[0-9]{16,20}')


def catalog():
    return json.loads(Path(__file__).with_name('fast_pack.json').read_text(encoding='utf-8'))


def rows(spec=None):
    """The catalog rows, in the order they are fetched (the licence and notes before the weights)."""
    return list((spec or catalog())['files'])


def model_folder():
    """The model folder the queue check and the engine process read (comfy_bridge.engine_model_root).

    ComfyUI started without the launcher has no FREEVIDEO_HOME, where paths would name the default installation folder
    instead of the one comfyui.json selects: the files would land where the engine never looks.
    """
    from .comfy_bridge import engine_model_root
    return engine_model_root()


def engine_folder():
    """The installation whose download settings, measured sources and lock the download uses."""
    from .comfy_bridge import installation_root
    return installation_root()


def remote(spec, row):
    """One catalog file on our ModelScope copy (its branch; the sha256 pins it) or the pinned Hugging Face revision."""
    from .network import source_url
    repo, revision = row.get('repo', spec['repo']), row.get('revision', spec['revision'])
    if revision is None:
        raise ValueError('Not on our copies yet: ' + row['file'])
    # A row from another repository has a ModelScope copy only if it names one.
    copy = row.get('modelscope') if 'repo' in row else row.get('modelscope', spec['modelscope'])
    path = '/%s/resolve/%s/%s' % (repo, revision, row['file'])
    sources = [dict(id='modelscope', url=copy + '/' + row['file'])] if copy else []
    sources.append(dict(id='official', url=source_url('models', 'official') + path))
    return dict(repo=repo, revision=revision, file=row['file'], family='image',
                sha256=row['sha256'], bytes=row['bytes'], sources=sources)


def _target(root, row):
    """Where a row's file lives under the model folder; a catalog row never leaves it."""
    name = PurePosixPath(row.get('target', row['file']))
    if name.is_absolute() or '..' in name.parts:
        raise ValueError('Invalid catalog path')
    return Path(root) / name


def _ours(spec, row):
    """Whether the file lies in this pack's own folder; a file elsewhere belongs to the rest of the installation."""
    return PurePosixPath(row.get('target', row['file'])).parts[0] == spec['folder']


def _in_place(root, row):
    path = _target(root, row)
    return path.is_file() and path.stat().st_size == row['bytes']


def published(spec=None):
    """Whether every file is on our copies: a page that sees False does not offer the level at all."""
    spec = spec or catalog()
    return all(row.get('revision', spec['revision']) is not None for row in rows(spec))


def _ledger_path(root, spec):
    return Path(root) / spec['folder'] / LEDGER


def _ledger(root, spec, *, retain=False):
    """The receipt; a damaged one reads as empty. Only an install renames a damaged one aside (``retain``): reading the
    state leaves the folder as it is."""
    from .monitoring import load_ledger
    return load_ledger(_ledger_path(root, spec), retain=retain)


def _identity(path, row):
    from .storage import fingerprint
    return dict(fingerprint(path), sha256=row['sha256'])


def _receipted(root, row, ledger):
    """There at its size, and the same file the receipt says passed its sha256."""
    path = _target(root, row)
    try:
        return _in_place(root, row) and ledger.get(row['file']) == _identity(path, row)
    except (OSError, ValueError):
        return False


def _receive(root, spec, ledger, row):
    """Note a file that has just passed its sha256."""
    from .monitoring import save
    try:
        ledger[row['file']] = _identity(_target(root, row), row)
        save(_ledger_path(root, spec), ledger)
    except (OSError, ValueError):
        pass  # without a receipt it is only hashed once more


def failed_copies(path):
    """The copies of one catalog file that did not pass, newest first."""
    path = Path(path)
    found = []
    try:
        entries = list(path.parent.iterdir())
    except OSError:
        return []
    for entry in entries:
        rest = entry.name[len(path.name):] if entry.name.startswith(path.name) else ''
        if rest and FAILED.fullmatch(rest) and entry.is_file() and not entry.is_symlink():
            found.append((int(rest.rsplit('-', 1)[1]), entry))
    return [entry for _, entry in sorted(found, key=lambda item: item[0], reverse=True)]


def prune_failed(root, spec, row, keep):
    """Keep at most `keep` failed copies of one of this pack's files (the newest, for diagnosis): one while it is not
    in place, none once it is. Nothing but this catalog's own names in its own folder is touched."""
    if not _ours(spec, row):
        return
    for entry in failed_copies(_target(root, row))[keep:]:
        try:
            entry.unlink()
        except OSError:
            pass


def missing_bytes(root, spec=None):
    """The bytes a download would fetch now: every row not there at its size as the receipt knows it."""
    spec = spec or catalog()
    root = io_path(root)
    ledger = _ledger(root, spec)
    return sum(row['bytes'] for row in rows(spec) if not _receipted(root, row, ledger))


def present(root, spec=None):
    """Whether the files the engine reads are the ones that passed their sha256, unchanged since (the receipt).

    The licence and notes are fetched with them, and nothing depends on reading them.
    """
    spec = spec or catalog()
    root = io_path(root)
    ledger = _ledger(root, spec)
    return all(_receipted(root, row, ledger) for row in rows(spec) if row.get('engine'))


def files(root=None, spec=None):
    """{file name in the catalog: path} of what the engine reads, each there at its pinned size; otherwise the error the
    page knows by its FAST_MODE_FILES_MISSING: prefix, naming what is missing or incomplete."""
    spec = spec or catalog()
    root = io_path(root if root is not None else model_folder())
    ledger = _ledger(root, spec)
    found, absent = {}, []
    for row in rows(spec):
        if not row.get('engine'):
            continue
        path = _target(root, row)
        there = _receipted(root, row, ledger)
        if not there and _intact(path, row, lambda: None):  # changed since its receipt: checked again before it counts
            _receive(root, spec, ledger, row)
            there = True
        if there:
            found[row['file']] = path
        else:
            absent.append(row['file'])
    if absent:
        raise FileNotFoundError(MISSING + 'install the fastest mode files (missing or incomplete: ' + ', '.join(absent) + ')')
    return found


def _intact(path, row, check):
    from .depth_reference import digest
    try:
        return path.is_file() and path.stat().st_size == row['bytes'] and digest(path, check) == row['sha256']
    except OSError:
        return False


def _partial(path):
    return path.with_name(path.name + '.partial')


def install(root, progress=None, check=None, *, engine_root=None, spec=None):
    """Fetch what is missing into root; a file that is not the pinned one is kept under another name."""
    from . import network
    from .image_gen.image_pack import PackError as TransferError, _download_plan, _transfer
    from .failure_details import image_pack_error_code
    from .locking import runtime_lock
    from .paths import data_root
    spec = spec or catalog()
    progress = progress or (lambda value: None)
    check = check or (lambda: None)
    root = io_path(root)
    try:
        lock_root = Path(engine_root) if engine_root is not None else data_root()
        with runtime_lock(lock_root / 'fast-pack-download.lock', inherit=False):
            ledger = _ledger(root, spec, retain=True)
            todo = []
            for row in rows(spec):
                check()
                path = _target(root, row)
                if _receipted(root, row, ledger) or _intact(path, row, check):
                    if not _receipted(root, row, ledger):
                        _receive(root, spec, ledger, row)
                    prune_failed(root, spec, row, 0)  # the right copy is here: the failed ones go
                    continue
                if path.exists() or path.is_symlink():
                    # Another feature's file is never moved: a different copy there stops the download.
                    if not _ours(spec, row):
                        raise PackError('integrity_failed')
                    network.retain_partial(path, 'rejected')
                prune_failed(root, spec, row, 1)  # at most the newest failed copy stays while it is not in place
                todo.append(row)
            if not todo:
                return 'present'
            if any(row.get('revision', spec['revision']) is None for row in todo):
                raise PackError('not_published')  # nothing is fetched from anywhere but our copies
            root.mkdir(parents=True, exist_ok=True)
            total = sum(row['bytes'] for row in todo)
            retained = sum(min(_partial(_target(root, r)).stat().st_size, r['bytes'])
                           for r in todo if _partial(_target(root, r)).is_file())
            if disk_space.free_bytes(root) < total - retained + RESERVE:
                raise PackError('disk_space')
            plan = _download_plan(engine_root)
            plan.update(quiet=True, resource_check=check)
            done = 0
            progress(dict(done=0, total=total))
            for row in todo:
                check()
                path = _target(root, row)
                path.parent.mkdir(parents=True, exist_ok=True)

                def moved(current, size, speed, **kwargs):
                    progress(dict(done=done + current, total=total))
                    check()
                try:
                    _transfer(plan, remote(spec, row), path, moved, check)
                finally:
                    if not path.is_file():
                        prune_failed(root, spec, row, 1)  # each failed copy is the whole file: one is enough to look at
                _receive(root, spec, ledger, row)  # network.download checked its sha256 before naming it
                prune_failed(root, spec, row, 0)
                # The record of which source the partial came from is only for resuming it.
                _partial(path).with_name(path.name + '.partial.source.json').unlink(missing_ok=True)
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


class Installer:
    """One background download at a time; the page polls `status()`."""

    def __init__(self, root=None, engine_root=None):
        self.root, self.engine_root = root or model_folder, engine_root
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
        root = self.root()
        return dict(bytes=missing_bytes(root, spec), present=present(root, spec), published=published(spec), install=self.state())

    def start(self):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return dict(self.value)
            self.stop = threading.Event()
            self.value = dict(state='running', done=0, total=0, reason=None, started=time.time())
            self.thread = threading.Thread(target=self._run, args=(self.stop,), name='freevideo-fast-pack', daemon=True)
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
            install(self.root(), progress, check,
                    engine_root=self.engine_root if self.engine_root is not None else engine_folder())
            self._set(state='done', reason=None)
        except InterruptedError:
            self._set(state='cancelled', reason=None)
        except PackError as error:
            self._set(state='failed', reason=error.reason)
        except Exception:
            self._set(state='failed', reason='failed')


def register():
    """GET /freevideo/fast-pack: whether the files are in place and the download state; POST {action: install|cancel}."""
    import asyncio
    from aiohttp import web
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_fast_pack', None) is not None:
        return
    installer = server._freevideo_fast_pack = Installer()

    def reply(value, status=200):
        return web.json_response(value, status=status, headers={'Cache-Control': 'no-store'})

    @server.routes.get('/freevideo/fast-pack')
    async def fast_pack_status(request):
        return reply(await asyncio.to_thread(installer.status))

    @server.routes.post('/freevideo/fast-pack')
    async def fast_pack_install(request):
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
