"""On-demand download of the automatic-subtitle models and speech runtime.

Nothing here ships in the installer. The catalog (`subtitle_pack.json`) lists
every file with its size and sha256; a file is used only after it matched.
Layout under `<model_root>/subtitles/`: `en/` and `silero_vad_v5.onnx` (speech),
`lid/` (spoken-language detection), `licenses/`, `runtime/<platform>/` (the wheel as
downloaded) and `unpacked/<sha12>/` where that wheel is unpacked and put on `sys.path` before `import sherpa_onnx`.

A catalog without a `revision` is not published yet: `status()` says so and
`available()` of the speech engine stays False, so subtitles remain manual.
"""
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import threading
import time
import zipfile

from . import disk_space

GROUPS = ('runtime', 'speech', 'language')
RESERVE = 256 * 2**20
MAX_UNPACKED = 256 * 2**20
LEDGER = 'download-verified.json'
COMPLETE = 'complete.json'


class PackError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def catalog():
    return json.loads(Path(__file__).with_name('subtitle_pack.json').read_text(encoding='utf-8'))


def platform_key():
    machine = platform.machine().lower()
    if sys.platform == 'win32':
        return 'windows-x64' if machine in ('amd64', 'x86_64') else None
    if sys.platform == 'darwin':
        return 'macos-arm64' if machine in ('arm64', 'aarch64') else 'macos-x86_64' if machine == 'x86_64' else None
    if sys.platform.startswith('linux'):
        return 'linux-x86_64' if machine in ('x86_64', 'amd64') else None
    return None


def base(root=None):
    if root is None:
        from .paths import model_root
        root = model_root()
    return Path(root) / 'subtitles'


def wheel_row(spec=None):
    """This machine's runtime wheel row, or None (other platform, Python or not built yet)."""
    spec = spec or catalog()
    tag = 'cp%d%d' % sys.version_info[:2]
    if spec.get('python') != tag:
        return None
    return (spec.get('wheels') or {}).get(platform_key())


def rows(group, spec=None):
    spec = spec or catalog()
    found = list(spec['groups'][group])
    if group == 'runtime':
        wheel = wheel_row(spec)
        if wheel:
            found.append(wheel)
    return found


def runtime_folder(root=None, spec=None):
    wheel = wheel_row(spec)
    return base(root) / 'unpacked' / wheel['sha256'][:12] if wheel else None


def _ledger(folder):
    try:
        value = json.loads((folder / LEDGER).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _intact(folder, row, ledger):
    """Size and the stamp recorded after the last hash match; no hashing here."""
    from . import provision
    path = folder / row['file']
    try:
        if not path.is_file() or path.stat().st_size != row['bytes']:
            return False
        prior = ledger.get(str(path))
        return isinstance(prior, dict) and all(
            prior.get(key) == value for key, value in provision.file_identity(path, row).items())
    except OSError:
        return False


def _unpacked(root, spec=None):
    folder = runtime_folder(root, spec)
    if folder is None:
        return False
    try:
        receipt = json.loads((folder / COMPLETE).read_text(encoding='utf-8'))
        return receipt.get('sha256') == wheel_row(spec)['sha256'] and (folder / 'sherpa_onnx' / '__init__.py').is_file()
    except (OSError, ValueError, KeyError, TypeError):
        return False


def ready(group, root=None, spec=None):
    spec = spec or catalog()
    folder = base(root)
    if group == 'runtime' and wheel_row(spec) is None:
        return False
    ledger = _ledger(folder)
    if not all(_intact(folder, row, ledger) for row in rows(group, spec)):
        return False
    return _unpacked(root, spec) if group == 'runtime' else True


def missing_bytes(group, root=None, spec=None):
    spec = spec or catalog()
    folder = base(root)
    ledger = _ledger(folder)
    return sum(row['bytes'] for row in rows(group, spec) if not _intact(folder, row, ledger))


def status(root=None):
    """What a page may show: whether the pack can be installed here and what is already in place."""
    spec = catalog()
    runtime_here = wheel_row(spec) is not None
    published = bool(spec.get('revision'))
    state = {}
    for group in GROUPS:
        state[group] = dict(ready=ready(group, root, spec), bytes=missing_bytes(group, root, spec))
    return dict(
        supported=runtime_here, published=published,
        installable=runtime_here and published,
        ready=state['runtime']['ready'] and state['speech']['ready'],
        language_ready=state['language']['ready'],
        bytes=sum(item['bytes'] for item in state.values()), groups=state)


def activate(root=None):
    """Put the unpacked runtime on sys.path (once). True when sherpa_onnx can be imported from it."""
    try:
        spec = catalog()
        if not _unpacked(root, spec):
            return False
        folder = runtime_folder(root, spec)
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
        if sys.platform == 'win32' and hasattr(os, 'add_dll_directory'):
            for item in {folder} | {path.parent for path in folder.rglob('*.dll')}:
                os.add_dll_directory(str(item))
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _unpack(root, row, check):
    """Extract the verified wheel into unpacked/<sha12>/ and drop older unpacked runtimes."""
    folder = runtime_folder(root)
    wheel = base(root) / row['file']
    parent = folder.parent
    parent.mkdir(parents=True, exist_ok=True)
    work = parent / ('.unpack-' + row['sha256'][:12])
    shutil.rmtree(work, ignore_errors=True)
    total = 0
    try:
        with zipfile.ZipFile(wheel) as archive:
            for item in archive.infolist():
                check()
                name = item.filename
                parts = Path(name).parts
                if (name.startswith('/') or '\\' in name or ':' in name or not parts or '..' in parts
                        or (item.external_attr >> 16) & 0o170000 == 0o120000):
                    raise PackError('failed')
                total += item.file_size
                if total > MAX_UNPACKED:
                    raise PackError('failed')
                target = work.joinpath(*parts)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open('wb') as stream:
                    shutil.copyfileobj(source, stream, 1024 * 1024)
        if not (work / 'sherpa_onnx' / '__init__.py').is_file():
            raise PackError('failed')
        (work / COMPLETE).write_text(json.dumps(dict(sha256=row['sha256'], bytes=total)), encoding='utf-8')
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(work, folder)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for other in parent.iterdir():
        if other.is_dir() and other != folder and not other.name.startswith('.unpack-'):
            shutil.rmtree(other, ignore_errors=True)


def install(groups=GROUPS, root=None, progress=None, check=None):
    """Download and verify the missing files of the groups; unpack the runtime. Resumable."""
    from . import network, provision
    from .adaln_assets import _download_plan
    from .monitoring import save
    spec = catalog()
    progress = progress or (lambda value: None)
    check = check or (lambda: None)
    if not spec.get('revision'):
        raise PackError('not_published')
    if 'runtime' in groups and wheel_row(spec) is None:
        raise PackError('unsupported')
    folder = base(root)
    folder.mkdir(parents=True, exist_ok=True)
    ledger = _ledger(folder)
    # A file without a stamp (a copied folder) is hashed once here and stamped.
    todo = [row for group in groups for row in rows(group, spec)
            if not provision.verified(folder / row['file'], row, ledger)]
    save(folder / LEDGER, ledger)
    total = sum(row['bytes'] for row in todo)
    retained = 0
    for row in todo:
        partial = (folder / row['file']).with_suffix((Path(row['file']).suffix) + '.partial')
        if partial.is_file() and partial.stat().st_size < row['bytes']:
            retained += partial.stat().st_size
    if total and disk_space.free_bytes(folder) < total - retained + RESERVE:
        raise PackError('disk_space')
    plan = _download_plan(root)
    # No verified ModelScope pair exists for this prefix; never guess a mirror revision.
    plan['sources'] = dict(plan.get('sources', {}), models=[
        row for row in plan.get('sources', {}).get('models', []) if row['id'] in ('official', 'hf-mirror')])
    if not plan['sources']['models']:
        plan['sources']['models'] = [{'id': 'official'}, {'id': 'hf-mirror'}]
    plan.update(quiet=True, resource_check=check)
    done = 0
    try:
        for row in todo:
            check()
            path = folder / row['file']
            remote = dict(repo=spec['repo'], revision=spec['revision'], file=spec['prefix'] + '/' + row['file'])

            def moved(current, size, speed, **kwargs):
                progress(dict(phase='download', done=done + current, total=total))
            network.download(network.model_urls(plan, remote), path, row['sha256'], moved,
                             network=plan, size=row['bytes'], keep_partial=True, stall_seconds=30,
                             slow_seconds=15, low_speed_limit=64 * 1024)
            ledger[str(path)] = provision.file_identity(path, row)
            save(folder / LEDGER, ledger)
            done += row['bytes']
        if 'runtime' in groups and not _unpacked(root, spec):
            progress(dict(phase='unpack', done=total, total=total))
            _unpack(root, wheel_row(spec), check)
    except PackError:
        raise
    except InterruptedError:
        raise
    except ValueError as error:
        raise PackError('disk_space' if str(error) == 'disk_space' else 'download_failed') from error
    except Exception as error:
        raise PackError('download_failed') from error


class Installer:
    """One background installation at a time; a page polls `state()`."""

    def __init__(self, root=None):
        self.root = root
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

    def start(self, groups=GROUPS):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return dict(self.value)
            self.stop = threading.Event()
            self.value = dict(state='running', done=0, total=0, reason=None, started=time.time())
            self.thread = threading.Thread(target=self._run, args=(tuple(groups), self.stop),
                                           name='freevideo-subtitle-pack', daemon=True)
            self.thread.start()
            return dict(self.value)

    def cancel(self):
        self.stop.set()
        return self.state()

    def _run(self, groups, stop):
        def check():
            if stop.is_set():
                raise InterruptedError('cancelled')

        # The phase lets the page say it is unpacking once every byte is in, instead of standing at 100%.
        def progress(value):
            self._set(done=value.get('done', 0), total=value.get('total', 0), phase=value.get('phase'))
        try:
            install(groups, self.root, progress, check)
            self._set(state='done', reason=None)
        except InterruptedError:
            self._set(state='cancelled', reason=None)
        except PackError as error:
            self._set(state='failed', reason=error.reason)
        except Exception:
            self._set(state='failed', reason='failed')
