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

from . import __version__, launch_view, processes
from .monitoring import save
from .long_paths import io_path, resolved, plain, plain_text, plain_errors
from .system import install_root, windows


def parse_progress_line(line):
    """Decode the worker's UI protocol; ordinary terminal output stays a log."""
    if line.startswith('{'):
        try:
            message = json.loads(line)
            if message.get('event') == 'freevideo_ui':
                return message
            if message.get('event') == 'local_model_progress':
                return local_model_ui(message)
        except (ValueError, AttributeError):
            pass
    return None


def launcher_root():
    if os.environ.get('FREEVIDEO_LAUNCHER_HOME'):
        return Path(os.environ['FREEVIDEO_LAUNCHER_HOME'])
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/FreeVideo/launcher'
    home = os.environ.get('LOCALAPPDATA') or str(Path.home())
    return Path(home) / 'FreeVideo-launcher'


def foreign_environment(root):
    """Whether an x64 environment is installed on a Windows on Arm computer."""
    from .environments import WINDOWS_ARM64_PYTHON, environment_native_arm64, windows_arm64
    folder = Path(root) / 'envs' / 'unified'
    return windows_arm64() and io_path(folder).is_dir() and not environment_native_arm64(folder, WINDOWS_ARM64_PYTHON)


NOTICE_FILES = ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md')


def source_files(root):
    from freevideo_source_bootstrap import TOUR_FILES
    names = ['pyproject.toml', 'README.md', 'MANIFEST.in', '__init__.py',
             'freevideo', 'setup.sh', 'test.sh',
             'freevideo.ps1', 'setup.ps1', 'test.ps1', 'freevideo.cmd', 'setup.cmd', 'test.cmd',
             'scripts/bootstrap_linux.sh', 'scripts/windows_app.py', 'scripts/build_windows.py',
             'scripts/check_installation.py', 'scripts/build_launcher_notices.py']
    # Older deployed Windows payloads may predate the optional Mac entry points.
    files_optional = ('scripts/build_macos.py', 'scripts/macos_app.py',
                      'freevideo_source_bootstrap.py', 'freevideo_ed25519.py', 'freevideo_update_keys.py',
                      'scripts/source_build.py', 'scripts/build_source_delta.py', 'scripts/sign_update_manifest.py',
                      'scripts/prune_qt_bundle.py', 'scripts/deduplicate_bundle_data.py', 'scripts/pyi_rth_bundle_data.py')
    names += [name for name in files_optional if (root / name).is_file()]
    # Notice and licence files are carried when present; a launcher never fails to start without one.
    names += [name for name in NOTICE_FILES if (root / name).is_file()]
    names += ['web/vendor/three/LICENSE'] if (root / 'web/vendor/three/LICENSE').is_file() else []
    files = [root / name for name in names]
    files += [p for folder in ('freevideo_engine', 'constraints', 'web', 'example_workflows')
              for p in (root / folder).rglob('*')
              if p.is_file() and (not p.relative_to(root).as_posix().startswith('web/assets/tour/') or p.relative_to(root).as_posix() in TOUR_FILES) and (p.suffix in ('.py', '.json', '.txt', '.ps1', '.sh', '.js', '.css', '.svg', '.png', '.ico', '.qml',
                                             '.woff2', '.otf', '.webp')
                 or folder == 'web' and (p.relative_to(root).as_posix() in TOUR_FILES or p.parent.name == 'tutorial' and p.suffix == '.mp4'))]
    # The small preview decoder's weights are the one binary file the engine reads from its own folder.
    decoder = root / 'freevideo_engine/frame_preview_decoder.safetensors'
    files += [decoder] if decoder.is_file() else []
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
    bundled, installed = resolved(bundled), resolved(installed)
    if bundled == installed:
        return True
    try:
        for path in source_files(bundled):
            if path.name == 'comfyui.json' or path.relative_to(bundled).as_posix() in NOTICE_FILES:
                continue
            other = installed / path.relative_to(bundled)
            if not other.is_file() or path.read_bytes() != other.read_bytes():
                return False
        return True
    except OSError:
        return False


def source_release(source):
    """The product version, build time and build number stamped into an engine source.

    A source checkout has no stamp and returns an empty identity."""
    from .release_notes import product_version
    try:
        stamp = json.loads((io_path(source) / 'freevideo_engine' / 'build-identity.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(stamp, dict):
        return {}
    result = {}
    if product_version(stamp.get('product_version')):
        result['product_version'] = stamp['product_version']
    if type(stamp.get('built_at')) is int and stamp['built_at'] > 0:
        result['built_at'] = stamp['built_at']
    if isinstance(stamp.get('version'), str) and len(stamp['version']) <= 64:
        result['version'] = stamp['version']
    return result


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


@plain_errors
def check_launcher_payload(source, destination):
    """Exercise installation metadata in the actual EXE, without installing anything."""
    from . import comfy_source, network
    package = io_path(Path(__file__).parent)
    source, destination = io_path(source), io_path(destination)
    files = package_data_files(package.parent)
    # A missing manifest must fail even if the frozen package contains no data.
    for name in ('dependencies.json', 'bootstrap_versions.json', 'model_files.json', 'prepared_models.json', 'test_prompts.json'):
        json.loads((package / name).read_text(encoding='utf-8'))
    for path in files:
        relative = path.relative_to(package.parent)
        if path.read_bytes() != (source / relative).read_bytes():
            raise ValueError('Packaged resource differs from installation source: ' + str(relative))
    layout = comfy_source.new_layout(plain(destination / 'new-install'))
    if io_path(layout['root']).exists():
        raise ValueError('Launcher smoke inspection created a ComfyUI installation')
    return dict(resources=[p.relative_to(package.parent).as_posix() for p in files],
                comfy_source=layout['source_spec'], model_repo=network.model_spec()['vdn_repo'])


RENAME_WAIT_SECONDS = 8.0


def _held(error):
    """A Windows refusal to rename a folder whose files were just written.

    Error 5 (access denied) and 32 (in use) are what Windows returns while a virus scanner or the
    search indexer still has one of the new files open; it usually lets go within seconds."""
    return windows() and (getattr(error, 'winerror', None) in (5, 32) or isinstance(error, PermissionError))


def _publish_stage(stage, target, complete):
    """Move the finished staging folder to its final name, however Windows treats the rename.

    A held folder is retried for a few seconds. If it stays held, the files are copied into the
    final folder instead (it is created here, so nothing existing is overwritten) and the result
    is checked like any other deployment. Other errors, such as a name that already exists, are
    raised for the caller to handle."""
    deadline = time.monotonic() + RENAME_WAIT_SECONDS
    delay = 0.1
    while True:
        try:
            stage.rename(target)
            return
        except OSError as error:
            if not _held(error) or target.exists():
                raise
            if time.monotonic() + delay < deadline:
                time.sleep(delay)
                delay = min(delay * 2, 1.0)
                continue
            held = error
            break
    try:
        # Plain copies, like the staging itself: no file times or attributes to be refused.
        shutil.copytree(stage, target, copy_function=shutil.copyfile)
        if not complete(target):
            raise OSError('The copied launcher source did not verify')
    except FileExistsError:
        raise
    except OSError:
        shutil.rmtree(target, ignore_errors=True)
        raise held from None


def materialize_source(bundle=None, destination=None):
    """A one-file executable's temporary extraction must never back an editable install."""
    if bundle is None:
        from .top_level_modules import update_modules
        active = update_modules()[2].ACTIVE
        if active:
            return Path(plain(active['source']))
    if bundle is None and not getattr(sys, 'frozen', False):
        return Path(plain(resolved(__file__).parents[1]))
    bundle = io_path(bundle or Path(sys._MEIPASS) / 'engine-source')
    rows = {p.relative_to(bundle).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files(bundle)}
    identity = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]
    parent = io_path(destination or launcher_root() / 'source')
    target = parent / (__version__ + '-' + identity)

    def complete(path):
        try:
            return path.is_dir() and all((path / p).is_file() and
                hashlib.sha256((path / p).read_bytes()).hexdigest() == digest for p, digest in rows.items())
        except OSError:
            return False

    if complete(target):
        return Path(plain(target))
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
                    return Path(plain(target))
                # Retain edits and incomplete deployments without overwriting them.
                target = parent / (__version__ + '-' + identity + '-' + uuid.uuid4().hex[:8])
                continue
            try:
                _publish_stage(stage, target, complete)
                return Path(plain(target))
            except OSError as error:
                # Another launcher can publish between exists() and rename().
                # Windows reports ERROR_ALREADY_EXISTS; POSIX uses ENOTEMPTY.
                if error.errno not in (errno.EEXIST, errno.ENOTEMPTY) and getattr(error, 'winerror', None) != 183:
                    raise
                # Recheck the winner's contents, then reuse or choose a new name.
    finally:
        shutil.rmtree(stage, ignore_errors=True)


ENTRY_SCRIPT = 'freevideo.ps1'


def entry_ready(source, name=ENTRY_SCRIPT):
    try:
        return (io_path(source) / name).is_file() and (io_path(source) / name).stat().st_size > 0
    except OSError:
        return False


def _builtin_files(source):
    """What the launcher can put back into a deployed source folder: ({name: sha256}, where(name) -> file).

    Only the launcher's own built-in files (or the verified update overlay) are ever the origin, and each one
    is used only when its hash equals the recorded one; nothing is downloaded. None when there is no such origin."""
    bootstrap = None
    if getattr(sys, 'frozen', False):
        from .top_level_modules import update_modules
        bootstrap = update_modules()[2]
    active = getattr(bootstrap, 'ACTIVE', None)
    if active and resolved(Path(plain(active['source']))) == resolved(Path(plain(source))):
        recorded = {name: row.get('sha256') for name, row in active['manifest']['files'].items()}

        def where(name):
            held = io_path(active['folder']) / 'files' / name
            return held if held.is_file() else io_path(Path(sys._MEIPASS) / 'engine-source') / name
        return recorded, where
    if getattr(sys, 'frozen', False):
        record = json.loads((io_path(source) / 'launcher-source.json').read_text(encoding='utf-8'))
        return dict(record['sha256']), lambda name: io_path(Path(sys._MEIPASS) / 'engine-source') / name
    return None


def _inside(name):
    """A recorded name is a plain relative path of forward-slash parts: no drive, absolute path, '..' or backslash."""
    parts = name.split('/') if isinstance(name, str) else ()
    return bool(parts) and all(part not in ('', '.', '..') and not set(part) & set('\\:') for part in parts)


def _put_back(source, name, recorded, where):
    expected = recorded.get(name)
    if not expected or not _inside(name):
        return False
    origin = where(name)
    if not origin.is_file():
        return False
    data = origin.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        return False
    target = io_path(source) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(target.name + '.' + uuid.uuid4().hex[:8] + '.tmp')
    try:
        stage.write_bytes(data)
        os.replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)
    return True


def restore_entry(source, name=ENTRY_SCRIPT):
    """Put back one entry script that vanished from a deployed source folder after the launcher started.

    The copy comes only from the launcher's own built-in files (or the verified update overlay),
    checked against the recorded hash; nothing is downloaded. Returns whether the script is usable."""
    if entry_ready(source, name):
        return True
    try:
        found = _builtin_files(source)
        if found is None or not _put_back(source, name, *found):
            return False
    except Exception:
        return False
    return entry_ready(source, name)


def restore_missing_files(source):
    """Put back every file of a deployed source folder that is gone (or was emptied), such as one a virus
    scanner removed, so a setup step does not stop on the first file it reads.

    Files that are present are never touched, so edits stay. Returns (restored names, names that could not be
    restored); both are empty when the folder is complete or has no built-in origin to restore from."""
    restored, failed = [], []
    try:
        found = _builtin_files(source)
        if found is None:
            return restored, failed
        empty = hashlib.sha256(b'').hexdigest()
        for name, expected in sorted(found[0].items()):
            try:
                if not _inside(name):
                    failed.append(name)
                    continue
                target = io_path(source) / name
                if target.is_file() and (target.stat().st_size or expected == empty):
                    continue
                (restored if _put_back(source, name, *found) else failed).append(name)
            except OSError:
                failed.append(name)
    except Exception:
        pass
    return restored, failed


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
    scanned = done if phase == 'scan' else 0
    if phase == 'scan':
        detail = '%d files / folders scanned' % done
        done = 0
    elif phase in ('verify', 'copy'):
        detail = '%.1f / %.1f MiB · %.1f MiB/s · %s' % (done/2**20, total/2**20, (rate or 0)/2**20, detail)
    return dict(kind='task_end' if phase == 'ready' else 'progress', key='local-models',
                activity={'verify': 'check', 'copy': 'copy', 'scan': 'note'}.get(phase, ''),
                name=((message.get('name') or message.get('file', '')) if phase in ('verify', 'copy')
                      else 'Finding existing models' if phase == 'scan' else ''), unit='bytes',
                label=label, detail=detail, state='complete' if phase == 'ready' else 'running',
                done=done, total=total, rate=rate, scanned=scanned, elapsed_seconds=message.get('elapsed_seconds', 0),
                remaining_seconds=(total-done)/rate if total and rate and phase in ('verify', 'copy') else None)


def start_failure_note(failure, command, source):
    """One line for a child that could not be started: the system's error number and the files it was given."""
    code = getattr(failure, 'winerror', None)
    parts = ['The task could not be started']
    if isinstance(code, int):
        parts.append('Windows error %d' % code)
    elif isinstance(failure.errno, int):
        parts.append('error %d' % failure.errno)
    if failure.filename:
        parts.append('path %s' % failure.filename)
    if command:
        parts.append('program %s' % command[0])
    parts.append('folder %s' % source)
    return ', '.join(parts)


class Runner:
    def __init__(self, source, events, logs=None):
        self.source, self.events = Path(plain(source)), events
        self.logs = Path(plain(logs or launcher_root() / 'runs'))
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

    def entry_script(self):
        """The file in the source folder that command() starts, or None when it starts no script."""
        if windows():
            return ENTRY_SCRIPT
        return None if sys.platform == 'darwin' else 'freevideo'

    def command(self, root, arguments):
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        return [str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                str(self.source / 'freevideo.ps1'), '--root', str(root), *arguments]

    def environment(self, root):
        from .linux_bundle import child_environment
        values = dict(child_environment(), PYTHONUTF8='1', PYTHONIOENCODING='utf-8', NO_COLOR='1', FREEVIDEO_UI_EVENTS='1')
        if foreign_environment(root):
            # Setup makes this environment again, and Windows cannot delete the folder of the Python that runs setup.
            values['FREEVIDEO_FORCE_BOOTSTRAP'] = '1'
        return values

    def prepare(self, action, root, env, progress):
        return env

    def _run_awake(self, action, root, arguments):
        run = io_path(self.logs) / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8])
        code, output, error = None, '', None
        # what a report can say when the child left no text: how much it printed, and what stopped the run
        lines, failed_with, winerror, command, refused = 0, None, None, None, None
        started = time.monotonic()
        try:
            run.mkdir(parents=True)
            command = self.command(root, arguments)
            entry = self.entry_script()
            # Only a command that really starts the script needs it (the ComfyUI host runs from Python directly).
            if entry and str(self.source / entry) in command and not restore_entry(self.source, entry):
                raise RuntimeError('FreeVideo could not find %s in %s' % (entry, plain(self.source)))
            restored, unrestored = restore_missing_files(self.source)
            restore_note = ''
            if restored or unrestored:
                restore_note = 'Source files missing from %s: restored %d%s' % (plain(self.source), len(restored),
                    ('; could not restore ' + ', '.join(unrestored[:5])) if unrestored else '')
            save(run / 'status.json', dict(action=action, command=command, root=str(root), status='running'))
            self.events.put(('started', plain(run)))
            env = self.environment(root)
            with (run / 'launcher.log').open('w', encoding='utf-8', buffering=1) as log:
                if restore_note:
                    log.write(json.dumps({'event': 'source_files', 'message': restore_note}) + '\n')
                def plain_message(value):
                    if isinstance(value, str):
                        return plain_text(value)
                    if isinstance(value, dict):
                        return {key: plain_message(item) for key, item in value.items()}
                    if isinstance(value, list):
                        return [plain_message(item) for item in value]
                    return value

                def progress(message):
                    nonlocal output
                    message = plain_message(message)
                    line = json.dumps(message) + '\n'
                    log.write(line)
                    output = (output + line)[-2 * 1024 * 1024:]
                    self.events.put(('log', line))
                    if message.get('event') == 'freevideo_ui':
                        self.events.put(('progress', message))
                env = self.prepare(action, root, env, progress)
                view = launch_view.for_engine(root)
                env = launch_view.environment_view(env, view)
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
                    except OSError as failure:
                        refused = failure   # only the system's refusal to start the child gets the start note
                        raise
                    finally:
                        if dll_directory is not None:
                            ctypes.windll.kernel32.SetDllDirectoryW(dll_directory)
                    process = self.process
                    if self.cancelled.is_set():
                        processes.stop(process)
                for line in process.stdout:
                    lines += 1
                    line = launch_view.line_back(plain_text(line), view)
                    log.write(line)
                    output = (output + line)[-2 * 1024 * 1024:]
                    self.events.put(('log', line))
                    message = parse_progress_line(line)
                    if message is not None:
                        message = plain_message(message)
                        if message.get('kind') == 'failure' and isinstance(message.get('error'), str):
                            error = message['error']
                        self.events.put(('progress', message))
                code = process.wait(timeout=15)
                process.stdout.close()
        except BaseException as failure:
            failed_with = type(failure).__name__
            error = plain_text(repr(failure))
            if failure is refused:
                # The system refused to start the child, so repr() alone names neither the code nor the path
                # it refused; a report that keeps only the first lines must still say both.
                winerror = getattr(failure, 'winerror', None)
                error += plain_text('\n' + start_failure_note(failure, command, self.source))
            detail = plain_text('\n' + traceback.format_exc())
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
                returncode=code, error=error, wall_seconds=time.monotonic()-started, root=str(root), log=plain(run),
                lines=lines, failed_with=failed_with, winerror=winerror)
            try:
                save(run / 'status.json', result)
            except OSError as failure:
                result.update(status='failed', error=plain_text(failure))
            with self.lock:
                self.process = None
            self.events.put(('done', dict(result, output=output)))
