"""Desktop orchestration and durable source deployment; independent of widgets."""
import errno
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid

from . import __version__, processes
from .monitoring import save
from .system import install_root, windows


def launcher_root():
    if os.environ.get('FREEVIDEO_LAUNCHER_HOME'):
        return Path(os.environ['FREEVIDEO_LAUNCHER_HOME'])
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/FreeVideo/launcher'
    home = os.environ.get('LOCALAPPDATA') or str(Path.home())
    return Path(home) / 'FreeVideo-launcher'


def source_files(root):
    names = ['pyproject.toml', 'README.md', 'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'MANIFEST.in', '__init__.py',
             'freevideo', 'setup.sh', 'test.sh',
             'freevideo.ps1', 'setup.ps1', 'test.ps1', 'freevideo.cmd', 'setup.cmd', 'test.cmd',
             'scripts/bootstrap_linux.sh', 'scripts/windows_app.py', 'scripts/build_windows.py',
             'scripts/check_installation.py', 'scripts/build_launcher_notices.py']
    # Older deployed Windows payloads may predate the optional Mac entry points.
    files_optional = ('scripts/build_macos.py', 'scripts/macos_app.py')
    names += [name for name in files_optional if (root / name).is_file()]
    files = [root / name for name in names]
    files += [p for folder in ('freevideo_engine', 'constraints', 'web', 'example_workflows')
              for p in (root / folder).rglob('*')
              if p.is_file() and p.suffix in ('.py', '.json', '.txt', '.ps1', '.sh', '.js', '.css', '.svg', '.png', '.ico', '.qml')]
    return sorted(files)


def package_data_files(root):
    """Resources also read by the frozen GUI itself, outside engine-source."""
    return sorted(p for p in (root / 'freevideo_engine').rglob('*')
                  if p.is_file() and (p.suffix in ('.json', '.ps1', '.svg', '.png', '.ico', '.qml')
                     or p.name == 'build-version.txt' or (p.suffix == '.txt' and 'licenses' in p.parts)))


def matching_source(bundled, installed):
    """Compare deployed code, ignoring the per-installation ComfyUI binding.

    Read source only: neither editable metadata nor an old receipt is proof
    that the running launcher and the selected engine contain the same code.
    """
    bundled, installed = Path(bundled).resolve(), Path(installed).resolve()
    if bundled == installed:
        return True
    try:
        for path in source_files(bundled):
            if path.name == 'comfyui.json':
                continue
            other = installed / path.relative_to(bundled)
            if not other.is_file() or path.read_bytes() != other.read_bytes():
                return False
        return True
    except OSError:
        return False


def check_launcher_dependencies():
    """Exercise the native process extension, including inside the frozen EXE."""
    import psutil
    process = psutil.Process(os.getpid())
    if process.create_time() <= 0 or process.memory_info().rss <= 0:
        raise RuntimeError('Launcher process inspection returned invalid counters')
    return dict(psutil_version=psutil.__version__, process_inspection=True)


def check_launcher_reopen(destination):
    """Smoke an existing installation's history path without user files or GPU work."""
    dependencies = check_launcher_dependencies()
    from .compatibility import check_installation
    from .resource_history import ResourceHistory
    import platform
    with tempfile.TemporaryDirectory(prefix='reopen-', dir=destination) as folder:
        root = Path(folder)
        identity = dict(gpu_uuid='GPU-LAUNCHER-CPU-SMOKE', system=platform.system())
        save(root / 'machine.json', identity)
        history = ResourceHistory(root / 'resource-history.sqlite3')
        attempt = history.begin(identity, {'engine': {'head_chunk': 2, 'steps': 8}},
                                dict(width=1344, height=768, frames=243, steps=8))
        state = check_installation(root)
        if state['level'] != 0 or history.attempt(attempt) is None:
            raise RuntimeError('Launcher reopening misclassified its live process')
        history.finish(attempt, 'cancelled')
    return dict(dependencies, installed_history=True)


def check_launcher_payload(source, destination):
    """Exercise installation metadata in the actual EXE, without installing anything."""
    from . import comfy_source, network
    package = Path(__file__).parent
    files = package_data_files(package.parent)
    # A missing manifest must fail even if the frozen package contains no data.
    for name in ('dependencies.json', 'bootstrap_versions.json', 'model_files.json', 'prepared_models.json', 'test_prompts.json'):
        json.loads((package / name).read_text(encoding='utf-8'))
    for path in files:
        relative = path.relative_to(package.parent)
        if path.read_bytes() != (source / relative).read_bytes():
            raise ValueError('Packaged resource differs from installation source: ' + str(relative))
    layout = comfy_source.new_layout(str(destination / 'new-install'))
    if Path(layout['root']).exists():
        raise ValueError('Launcher smoke inspection created a ComfyUI installation')
    return dict(resources=[p.relative_to(package.parent).as_posix() for p in files],
                comfy_source=layout['source_spec'], model_repo=network.model_spec()['vdn_repo'])


def materialize_source(bundle=None, destination=None):
    """A one-file executable's temporary extraction must never back an editable install."""
    if bundle is None and not getattr(sys, 'frozen', False):
        return Path(__file__).resolve().parents[1]
    bundle = Path(bundle or Path(sys._MEIPASS) / 'engine-source')
    rows = {p.relative_to(bundle).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files(bundle)}
    identity = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]
    parent = Path(destination or launcher_root() / 'source')
    target = parent / (__version__ + '-' + identity)

    def complete(path):
        try:
            return path.is_dir() and all((path / p).is_file() and
                hashlib.sha256((path / p).read_bytes()).hexdigest() == digest for p, digest in rows.items())
        except OSError:
            return False

    if complete(target):
        return target
    parent.mkdir(parents=True, exist_ok=True)
    # Not mkdtemp: on Windows its folder is readable only by its owner, and a
    # run as administrator makes Administrators the owner, so the same person's
    # normal runs could no longer read the source. This one inherits the parent's.
    stage = parent / ('source-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        for relative in rows:
            output = stage / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundle / relative, output)
        save(stage / 'launcher-source.json', {'version': __version__, 'sha256': rows})
        while True:
            if target.exists():
                if complete(target):
                    return target
                # Retain edits and incomplete deployments without overwriting them.
                target = parent / (__version__ + '-' + identity + '-' + uuid.uuid4().hex[:8])
                continue
            try:
                stage.rename(target)
                return target
            except OSError as error:
                # Another launcher can publish between exists() and rename().
                # Windows reports ERROR_ALREADY_EXISTS; POSIX uses ENOTEMPTY.
                if error.errno not in (errno.EEXIST, errno.ENOTEMPTY) and getattr(error, 'winerror', None) != 183:
                    raise
                # Recheck the winner's contents, then reuse or choose a new name.
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def preflight_json(output):
    decoder = json.JSONDecoder()
    for offset, character in enumerate(output):
        if character == '{':
            try:
                value, _ = decoder.raw_decode(output[offset:])
                if isinstance(value, dict) and 'inventory' in value and 'policy_estimate' in value:
                    return value
            except ValueError:
                pass
    raise ValueError('No complete setup plan was returned; inspect the retained launcher log.')


def local_model_ui(message):
    phase = message.get('phase')
    done, total = message.get('done_bytes', 0), message.get('total_bytes', 0)
    rate = message.get('bytes_per_second')
    label = {'scan': 'Find existing models', 'verify': 'Verify local model', 'copy': 'Copy local model',
             'link': 'Reuse local model', 'ready': 'Existing models checked'}.get(phase, 'Check local models')
    detail = message.get('file', '')
    if phase == 'scan':
        detail = '%d files / folders scanned' % done
        done = 0
    elif phase in ('verify', 'copy'):
        detail = '%.1f / %.1f MiB · %.1f MiB/s · %s' % (done/2**20, total/2**20, (rate or 0)/2**20, detail)
    return dict(kind='task_end' if phase == 'ready' else 'progress', key='local-models',
                label=label, detail=detail, state='complete' if phase == 'ready' else 'running',
                done=done, total=total, elapsed_seconds=message.get('elapsed_seconds', 0),
                remaining_seconds=(total-done)/rate if total and rate and phase in ('verify', 'copy') else None)


class Runner:
    def __init__(self, source, events, logs=None):
        self.source, self.events = Path(source), events
        self.logs = Path(logs or launcher_root() / 'runs')
        self.process = self.thread = None
        self.cancelled = threading.Event()
        self.lock = threading.Lock()

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, action, root, arguments):
        if self.busy:
            raise RuntimeError('A launcher task is already running')
        self.cancelled.clear()
        self.thread = threading.Thread(target=self._run, args=(action, Path(root), list(arguments)), daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancelled.set()
        def stop():
            with self.lock:
                if self.process is not None:
                    processes.stop(self.process)
        threading.Thread(target=stop, daemon=True).start()

    def _run(self, action, root, arguments):
        from .windows_ux import awake
        with awake():
            self._run_awake(action, root, arguments)

    def command(self, root, arguments):
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        return [str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                str(self.source / 'freevideo.ps1'), '--root', str(root), *arguments]

    def environment(self, root):
        from .linux_bundle import child_environment
        return dict(child_environment(), PYTHONUTF8='1', PYTHONIOENCODING='utf-8', NO_COLOR='1', FREEVIDEO_UI_EVENTS='1')

    def prepare(self, action, root, env, progress):
        return env

    def _run_awake(self, action, root, arguments):
        run = self.logs / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8])
        code, output, error = None, '', None
        started = time.monotonic()
        try:
            run.mkdir(parents=True)
            command = self.command(root, arguments)
            save(run / 'status.json', dict(action=action, command=command, root=str(root), status='running'))
            self.events.put(('started', str(run)))
            env = self.environment(root)
            with (run / 'launcher.log').open('w', encoding='utf-8', buffering=1) as log:
                def progress(message):
                    nonlocal output
                    line = json.dumps(message) + '\n'
                    log.write(line)
                    output = (output + line)[-2 * 1024 * 1024:]
                    self.events.put(('log', line))
                    if message.get('event') == 'freevideo_ui':
                        self.events.put(('progress', message))
                env = self.prepare(action, root, env, progress)
                if self.cancelled.is_set():
                    raise RuntimeError('Stopped; files retained')
                with self.lock:
                    # External Python must not inherit the frozen GUI's DLL
                    # directory. Restore it immediately for subsequent Tk loads.
                    dll_directory = None
                    if getattr(sys, 'frozen', False) and windows():
                        import ctypes
                        buffer = ctypes.create_unicode_buffer(32768)
                        ctypes.windll.kernel32.GetDllDirectoryW(len(buffer), buffer)
                        dll_directory = buffer.value
                        ctypes.windll.kernel32.SetDllDirectoryW(None)
                    try:
                        self.process = processes.popen(command, cwd=self.source, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                            start_new_session=True, supervise=True)
                    finally:
                        if dll_directory is not None:
                            ctypes.windll.kernel32.SetDllDirectoryW(dll_directory)
                    process = self.process
                    if self.cancelled.is_set():
                        processes.stop(process)
                for line in process.stdout:
                    log.write(line)
                    output = (output + line)[-2 * 1024 * 1024:]
                    self.events.put(('log', line))
                    if line.startswith('{'):
                        try:
                            message = json.loads(line)
                            if message.get('event') == 'freevideo_ui':
                                if message.get('kind') == 'failure' and isinstance(message.get('error'), str):
                                    error = message['error']
                                self.events.put(('progress', message))
                            elif message.get('event') == 'local_model_progress':
                                self.events.put(('progress', local_model_ui(message)))
                        except (ValueError, AttributeError):
                            pass
                code = process.wait(timeout=15)
                process.stdout.close()
        except BaseException as failure:
            error = repr(failure)
            detail = '\n' + traceback.format_exc()
            output += detail
            try:
                with (run / 'launcher.log').open('a', encoding='utf-8') as log:
                    log.write(detail)
            except OSError:
                pass
            self.events.put(('log', error + '\n'))
            if self.process is not None:
                processes.stop(self.process)
        finally:
            result = dict(action=action, status='cancelled' if self.cancelled.is_set() else 'complete' if code == 0 else 'failed',
                returncode=code, error=error, wall_seconds=time.monotonic()-started, root=str(root), log=str(run))
            try:
                save(run / 'status.json', result)
            except OSError as failure:
                result.update(status='failed', error=str(failure))
            with self.lock:
                self.process = None
            self.events.put(('done', dict(result, output=output)))
