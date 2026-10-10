"""ComfyUI launcher control plane. No Torch, package installs in the host, or models.

The existing Setup service owns the engine plan, approval, downloads and repair.
Only our node entry point and our template are installed in the selected ComfyUI.
An existing server is never stopped by this launcher.
"""
import errno
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

from .comfy_bridge import installation
from . import disk_space
from .comfy_environment import isolated_environment
from .comfy_setup import Setup, SetupRunner
from .comfy_source import RECEIPT, SOURCE_DISK_BYTES, new_layout, validate_target
from .desktop_runtime import materialize_source
from .monitoring import save
from . import processes

PROTOCOL = 1
TEMPLATE = 'FreeVideo-All-in-One.json'


def disk_review(plan, engine, comfy, *, separate, new_comfy):
    """Add frontend costs to the right volume, including cross-drive installs."""
    frontend = plan.get('frontend')
    if frontend and frontend['root'] == str(Path(comfy).resolve()) and frontend['separate'] == separate:
        from .install_disk import errors
        disks = [dict(disk, paths=list(disk['paths']), free_bytes=disk_space.free_bytes(disk['paths'][0]))
                 for disk in plan.get('disks', [])]
        return 0, disks, errors(disks)  # Already included before automatic mode selection.
    disks = {}
    for disk in plan.get('disks', []):
        disks[disk_space.disk_key(disk['paths'][0])] = dict(disk, paths=list(disk['paths']))
    extra = 0
    from .install_disk import frontend_gib
    frontend_bytes = frontend_gib(engine, comfy, sys.platform == 'darwin') * 2**30
    for path, amount in ((engine, frontend_bytes if separate else 0), (comfy, SOURCE_DISK_BYTES if new_comfy else 0)):
        if not amount:
            continue
        parent = disk_space.existing(path)
        disk = disks.setdefault(disk_space.disk_key(parent), dict(paths=[str(parent)], needed_bytes=0))
        disk['free_bytes'] = disk_space.free_bytes(parent)
        disk['needed_bytes'] += amount
        extra += amount
    from .install_disk import errors
    return extra, list(disks.values()), errors(disks.values())


def layout(path):
    selected = Path(path).expanduser().resolve()
    root = selected / 'ComfyUI' if (selected / 'ComfyUI' / 'main.py').is_file() else selected
    if not (root / 'main.py').is_file() or not (root / 'folder_paths.py').is_file():
        raise ValueError('Select the ComfyUI folder, or the portable folder containing ComfyUI.')
    if not (root / 'comfy_api' / 'latest').is_dir():
        raise ValueError('This ComfyUI is too old for native VIDEO / V3 nodes. Update ComfyUI first; its files have not been changed.')
    candidates = []
    for parent in (root, root.parent):
        for name in ('python_embeded', 'python_embedded', '.venv', 'venv', 'env', 'python'):
            directory = parent / name
            candidates.extend([directory / 'python.exe', directory / 'Scripts/python.exe', directory / 'bin/python'])
    python = next((p.resolve() for p in candidates if p.is_file()), None)
    return dict(root=str(root), python=str(python) if python else None,
                portable=bool(python and python.parent.name in ('python_embeded', 'python_embedded')))


def managed_python(engine, comfy):
    """Find our previously completed frontend without requiring its full disk budget again."""
    try:
        record = json.loads((engine / 'launcher/comfy-host.json').read_text(encoding='utf-8'))
        identity = record['identity']
        environment, python = Path(record['environment']), Path(record['python'])
        machine = json.loads((engine / 'machine.json').read_text(encoding='utf-8'))
        if (identity['comfy'] != str(comfy) or identity['engine_python'] != machine['python']
                or identity['requirements'] != hashlib.sha256((comfy / 'requirements.txt').read_bytes()).hexdigest()
                or environment.resolve().parent != (engine / 'envs').resolve()
                or python != environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
                or json.loads((environment / 'freevideo-host.json').read_text(encoding='utf-8')) != identity
                or not python.is_file()):
            return None
        return str(python)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def managed_frontend(selected):
    """Identify our frontend even after reopening makes `separate` false.

    Do not resolve the executable: venv Python is a symlink on Linux, but its
    environment and installed packages still belong to the venv directory.
    """
    python = Path(os.path.abspath(selected['python']))
    environment = python.parent.parent
    return (python.parent.name in ('Scripts', 'bin')
            and environment.name.startswith('comfyui-')
            and environment.parent.resolve() == (Path(selected['engine']) / 'envs').resolve())


# Run with the host's Python, without importing Torch or creating __pycache__.
# Reading the same folder registry supports extra_model_paths.yaml correctly.
HOST_PROBE = r'''
import importlib.util, json, pathlib, sys
root = pathlib.Path(sys.argv[1]); sys.path.insert(0, str(root))
needed = ('aiohttp', 'yaml', 'numpy', 'torch', 'safetensors', 'comfyui_frontend_package')
missing = [n for n in needed if importlib.util.find_spec(n) is None]
libraries = [str(root / 'models')]; error = None
try:
 import folder_paths
 from utils.extra_config import load_extra_path_config
 extra = root / 'extra_model_paths.yaml'
 if extra.is_file(): load_extra_path_config(str(extra))
 for name in ('diffusion_models', 'unet', 'checkpoints', 'text_encoders', 'clip', 'vae'):
  try: libraries.extend(folder_paths.get_folder_paths(name))
  except KeyError: pass
except Exception as e: error = str(e)
print('FREEVIDEO_HOST=' + json.dumps(dict(python=sys.executable, version=list(sys.version_info[:2]), missing=missing, libraries=list(dict.fromkeys(libraries)), library_error=error)))
'''


def probe_host(descriptor, python=None):
    python = python or descriptor.get('python')
    if not python:
        return dict(ready=False, python=None, libraries=[str(Path(descriptor['root']) / 'models')],
                    reason='No ComfyUI Python found; prepare a separate environment.')
    from .linux_bundle import child_environment
    env = dict(child_environment(), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    # SystemRoot and Windows crypto variables must survive even an isolated test.
    for name in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
        env.pop(name, None)
    try:
        from .windows_ux import external_python
        with external_python():
            result = subprocess.run([str(python), '-I', '-B', '-c', HOST_PROBE, descriptor['root']],
                                    cwd=descriptor['root'], env=env, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=45,
                                    **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        line = next(s for s in reversed(result.stdout.splitlines()) if s.startswith('FREEVIDEO_HOST='))
        data = json.loads(line.split('=', 1)[1])
        data['ready'] = result.returncode == 0 and not data['missing'] and tuple(data['version']) >= (3, 10)
        return data
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration) as error:
        return dict(ready=False, python=str(python), libraries=[str(Path(descriptor['root']) / 'models')],
                    reason='ComfyUI Python could not start: ' + str(error))


def local_url(value):
    value = value.strip() or 'http://127.0.0.1:8188'
    parsed = urlsplit(value if '://' in value else 'http://' + value)
    if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError('Use a local ComfyUI address, for example http://127.0.0.1:8188.')
    port = parsed.port or 80
    if not 1 <= port <= 65535:
        raise ValueError('Invalid ComfyUI port')
    return urlunsplit(('http', parsed.netloc, '', '', ''))


def get_json(url, timeout=2):
    # Proxy settings must never send loopback requests to an external proxy.
    with build_opener(ProxyHandler({})).open(Request(url, headers={'Accept': 'application/json'}), timeout=timeout) as reply:
        value = reply.read(2 * 1024 * 1024 + 1)
        if len(value) > 2 * 1024 * 1024:
            raise ValueError('ComfyUI response is too large')
        return json.loads(value)


def server_info(url):
    try:
        value = get_json(url + '/freevideo/launcher')
        if (isinstance(value, dict) and value.get('protocol') == PROTOCOL
                and all(isinstance(value.get(k), str) and value[k] for k in ('source', 'engine_root', 'comfy_root'))):
            return dict(value, status='freevideo')
    except (OSError, ValueError):
        pass
    try:
        value = get_json(url + '/system_stats')
        if isinstance(value, dict) and 'system' in value:
            argv = value['system'].get('argv') if isinstance(value['system'], dict) else None
            main = argv[0] if isinstance(argv, list) and argv and isinstance(argv[0], str) else ''
            return dict(status='restart-required', main=main)
    except (OSError, ValueError):
        pass
    return dict(status='offline')


def bind_check(hostname, port):
    """Raise OSError unless a new server could listen on this local address now."""
    hosts = ('127.0.0.1', '::1') if hostname == 'localhost' else (hostname,)
    for host in hosts:
        try:
            with socket.socket(socket.AF_INET6 if host == '::1' else socket.AF_INET, socket.SOCK_STREAM) as check:
                if sys.platform != 'win32':
                    # Tolerate TIME_WAIT, as ComfyUI does. On Windows this
                    # option would let two servers share the port.
                    check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                check.bind((host, port))
        except OSError as error:
            if hostname == 'localhost' and host == '::1' and error.errno in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
                continue
            raise


def address(hostname, port):
    return 'http://%s:%d' % ('[%s]' % hostname if ':' in hostname else hostname, port)


def next_free_address(url, count=12):
    """The next local address after this one that nothing listens or answers on, as setup.sh chooses."""
    parsed = urlsplit(url)
    for port in range((parsed.port or 80) + 1, min((parsed.port or 80) + 1 + count, 65536)):
        try:
            bind_check(parsed.hostname, port)
        except OSError:
            continue
        candidate = address(parsed.hostname, port)
        if server_info(candidate)['status'] == 'offline':
            return candidate
    return None


def launcher_comfy(selected, info):
    """Whether the launcher downloaded this ComfyUI and runs it in its own environment, and the
    server at its address is not this ComfyUI. Such a server belongs to someone else, so this
    ComfyUI can move to another port instead of asking for that server to be closed."""
    root = Path(selected['root'])
    if not (selected.get('python') and managed_frontend(selected) and (root / RECEIPT).is_file()):
        return False
    if info['status'] == 'freevideo':
        return Path(info['comfy_root']).resolve() != root.resolve()
    if info['status'] == 'offline':
        return True
    main = info.get('main') or ''
    if Path(main).is_absolute():
        return Path(main).resolve() != (root / 'main.py').resolve()
    try:
        # ComfyUI answers {kind: [absolute folders]}; its custom_nodes folder names its installation.
        folders = get_json(selected['url'] + '/internal/folder_paths', timeout=1)
        nodes = folders.get('custom_nodes') if isinstance(folders, dict) else None
        if (not isinstance(nodes, list) or not nodes
                or not all(isinstance(path, str) and Path(path).is_absolute() for path in nodes)):
            return False
        return all(Path(path).resolve() != (root / 'custom_nodes').resolve() for path in nodes)
    except (OSError, ValueError):
        return False


def queue_busy(url):
    """Whether ComfyUI is running or holding jobs. An unreadable queue counts as idle."""
    try:
        value = get_json(url + '/queue', timeout=3)
        return bool(value.get('queue_running') or value.get('queue_pending'))
    except (OSError, ValueError, AttributeError):
        return False


def matches_server(info, root, engine, source):
    return (info.get('status') == 'freevideo'
            and all(info.get(k) and Path(info[k]).resolve() == Path(p).resolve()
                    for k, p in (('comfy_root', root), ('engine_root', engine), ('source', source))))


def node_target(root):
    parent = Path(root) / 'custom_nodes'
    found = []
    if parent.is_dir():
        for path in parent.iterdir():
            if path.name.endswith('.disabled') or not path.is_dir():
                continue
            code = path / 'freevideo_engine' / 'comfy_nodes.py'
            if (path / 'freevideo-launcher.json').is_file() or (code.is_file() and 'FreeVideoGenerate' in code.read_text(encoding='utf-8')):
                found.append(path)
    if len(found) > 1:
        raise ValueError('Multiple FreeVideo node installations found. Keep one enabled before installing.')
    target = found[0] if found else parent / 'FreeVideo'
    if target.exists() and not found and any(target.iterdir()):
        raise ValueError('custom_nodes/FreeVideo already contains unrelated files. Choose another ComfyUI or rename that folder first.')
    return target


GIT_IGNORE = ('# FreeVideo engine: environments, models and caches, not part of any git checkout.\n'
              '# Keeps an update of a surrounding ComfyUI from copying, moving or deleting them.\n*\n')


def shield_engine(engine):
    """Keep a surrounding git checkout's operations away from the engine.

    Inside an existing ComfyUI the engine is an untracked folder of tens of
    gigabytes. An updater that stashes or cleans untracked files (git stash -u,
    git add -A, git clean) would copy it into git or move it away. Only the
    launcher's engine folders get the marker, never a source checkout.
    """
    engine = Path(engine)
    marker = engine / '.gitignore'
    if (not engine.is_dir() or os.path.lexists(marker) or (engine / '.git').exists()
            or (engine / 'freevideo_engine').is_dir()):
        return False
    try:
        marker.write_text(GIT_IGNORE, encoding='utf-8')
    except OSError:
        return False
    return True


def checkout_entry(data, folder):
    """A FreeVideo checkout's own entry point, put back by git, a node updater or a copied repository."""
    text = data.decode('utf-8', 'replace')
    return ('async def comfy_entrypoint' in text and 'from .freevideo_engine.' in text
            and (Path(folder) / 'freevideo_engine' / 'comfy_nodes.py').is_file())


def deploy(root, source, engine):
    """Update only our entry point; preserve source edits, libraries and templates."""
    root, source, engine = map(lambda p: Path(p).resolve(), (root, source, engine))
    shield_engine(engine)
    target = node_target(root)
    target.mkdir(parents=True, exist_ok=True)
    receipt = target / 'freevideo-launcher.json'
    previous = json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {}
    entry = target / '__init__.py'
    old = entry.read_bytes() if entry.is_file() else None
    if (old and previous.get('entry_sha256') and hashlib.sha256(old).hexdigest() != previous['entry_sha256']
            and not checkout_entry(old, target)):
        raise ValueError(f'The managed FreeVideo entry point ({entry}) was edited. Changes are retained; '
                         'undo them or delete that file before updating.')
    # The small loader may be replaced; all original checkout files remain intact.
    code = ("# Installed by FreeVideo launcher. Original entry points are retained in the engine launcher/backups folder.\n"
            "import importlib.util as _util\nimport json as _json\nfrom pathlib import Path as _Path\nimport sys as _sys\n"
            "_record = _json.loads((_Path(__file__).parent / 'freevideo-launcher.json').read_text(encoding='utf-8'))\n"
            "_source = _Path(_record['source'])\n"
            "_spec = _util.spec_from_file_location(__name__ + '._engine', _source / '__init__.py', submodule_search_locations=[str(_source)])\n"
            "_engine = _util.module_from_spec(_spec)\n_sys.modules[_spec.name] = _engine\n_spec.loader.exec_module(_engine)\n"
            "WEB_DIRECTORY = str(_source / 'web')\ncomfy_entrypoint = _engine.comfy_entrypoint\n").encode()
    if old and old != code:
        backup = engine / 'launcher' / 'backups' / (target.name + '-' + hashlib.sha256(old).hexdigest()[:12] + '-init.py')
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            backup.write_bytes(old)
    save(source / 'comfyui.json', dict(installation=str(engine)))
    save(receipt, dict(protocol=PROTOCOL, source=str(source), engine_root=str(engine), entry_sha256=hashlib.sha256(code).hexdigest()))
    temporary = entry.with_suffix('.py.launcher-tmp')
    temporary.write_bytes(code)
    os.replace(temporary, entry)
    template = (source / 'example_workflows' / TEMPLATE).read_bytes()
    digest = hashlib.sha256(template).hexdigest()[:12]
    saved = []
    for parent in (target / 'example_workflows', root / 'user' / 'default' / 'workflows' / 'FreeVideo'):
        parent.mkdir(parents=True, exist_ok=True)
        file = parent / TEMPLATE
        if file.exists() and file.read_bytes() != template:
            file = parent / ('FreeVideo-All-in-One-' + digest + '.json')
        if not file.exists():
            file.write_bytes(template)
        saved.append(str(file))
    return dict(node=str(target), workflows=saved)


class LauncherRunner(SetupRunner):
    def command(self, root, arguments):
        if arguments and arguments[0] == 'comfy-host':
            _, machine = installation(self.source, {'FREEVIDEO_HOME': str(root)})
            code = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('freevideo_engine.comfy_host',run_name='__main__')"
            return [machine['python'], '-B', '-c', code, str(self.source), '--root', str(root), *arguments[1:]]
        return super().command(root, arguments)


class Controller:
    def __init__(self, source, *, child_environment=None):
        self.source = Path(source)
        self.child_environment = child_environment
        self.state = dict(status='idle')
        self.selection = self.setup = None
        self.thread = None
        self.cancelled = threading.Event()
        self.server = None
        self._server_lock = threading.RLock()
        self._closed = False
        self.server_log = None
        self.sections = []
        self.section = None
        # Set by the Qt session: lets the server it starts hand browser update
        # requests back to this launcher.
        self.update_bridge = None
        # Earlier FreeVideo copies removed in the background once ComfyUI runs
        # this version; kept out of `state`, which the task thread replaces.
        self.old_versions = dict(released_bytes=0, download_bytes=0)
        self._old_versions_thread = None
        # A ComfyUI the launcher downloaded moves to the next free port when
        # another program holds its address. setup.sh's service keeps the port
        # it was asked to serve.
        self.move_when_taken = True
        self.port_move = None

    def retire_old_versions(self, selected):
        """Once ComfyUI runs this version, earlier FreeVideo copies are no longer read."""
        if self._old_versions_thread is not None and self._old_versions_thread.is_alive():
            return
        def work():
            from .desktop_runtime import launcher_root
            from .launcher_update import current_build
            from .version_cleanup import run
            try:
                result = run(launcher_root=launcher_root(), launcher_source=self.source, launcher_build=current_build(),
                             running=[sys.executable] if getattr(sys, 'frozen', False) else [],
                             engine=selected['engine'], comfy_root=selected['root'],
                             engine_source=selected['source'], server_source=server_info(selected['url']).get('source'))
            except Exception as error:  # Cleanup never affects using ComfyUI.
                result = dict(released_bytes=0, error=type(error).__name__ + ': ' + str(error))
            self.old_versions = dict(result, released_bytes=self.old_versions['released_bytes'] + result['released_bytes'],
                                     download_bytes=self.old_versions.get('download_bytes', 0) + result.get('download_bytes', 0))
        self._old_versions_thread = threading.Thread(target=work, name='freevideo-old-versions', daemon=True)
        self._old_versions_thread.start()

    def _prepare_frontend(self, selected, *, download=False, repair=False):
        """Bring the separate ComfyUI environment up to date with this ComfyUI.

        After ComfyUI itself is updated, its requirements change; the same
        environment is updated in place and keeps its PyTorch. Our own idle
        server is stopped first: Windows cannot replace packages it has loaded.
        """
        if self.setup is None:
            folders = SimpleNamespace(base_path=selected['root'], models_dir=str(Path(selected['root']) / 'models'),
                                      get_folder_paths=lambda _: [])
            self.setup = Setup(Path(selected['source']), folders, LauncherRunner)
        if managed_python(Path(selected['engine']), Path(selected['root'])) is None:
            self._stop_server_for_setup(selected['url'])
        self.setup.state = dict(status='running', action='comfy-host')
        self.setup.events.reset()
        arguments = ['comfy-host', '--comfy', selected['root']]
        if download:
            arguments.append('--download-comfy')
        if repair:
            arguments.append('--repair')
        self.setup.runner.start('comfy-host', selected['engine'], arguments)
        self._wait_setup()
        descriptor = json.loads((Path(selected['engine']) / 'launcher' / 'comfy-host.json').read_text(encoding='utf-8'))
        selected['python'] = descriptor['python']

    def owns_server(self):
        return self.server is not None and self.server.poll() is None

    def stop_owned_server(self):
        with self._server_lock:
            if self.server is not None:
                processes.stop(self.server, grace=5)
                self.server = None

    def terminal_sources(self):
        sources = []
        setup_log = self.setup.state.get('log') if self.setup else None
        if setup_log:
            sources.append(('setup', str(Path(setup_log) / 'launcher.log')))
        if self.server_log:
            sources.append(('comfy', str(self.server_log)))
        return sources

    def terminal_running(self, path):
        if path and self.server_log and Path(path) == self.server_log:
            return self.server is None or self.server.poll() is None
        return self.busy

    def restore_terminal(self, engine):
        # This is log viewing only: a persisted path grants no process-control
        # authority over an existing server. Never scan entire run directories.
        self.server_log = None
        try:
            root = Path(engine) / 'launcher'
            value = json.loads((root / 'comfy-console.json').read_text(encoding='utf-8'))
            path = (root / value['log']).resolve()
            path.relative_to((root / 'comfy-runs').resolve())
            if path.is_file():
                self.server_log = path
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def attach_console(self, info):
        if info.get('console_log'):
            root = Path(self.selection['engine']) / 'launcher'
            path = Path(info['console_log']).resolve()
            path.relative_to((root / 'comfy-runs').resolve())
            self.server_log = path
            save(root / 'comfy-console.json', dict(log=str(path.relative_to(root.resolve()))))
        elif info.get('console_error'):
            self.state = dict(self.state, error=info['console_error'])

    def restore(self, values):
        """Read a completed installation without probing a GPU or installing anything."""
        record = values.get('installation') or {}
        try:
            if record:
                selected_root, selected_engine = record['root'], record['engine']
            elif values.get('new_comfy'):
                selected_root = str(Path(values['destination']) / 'ComfyUI')
                selected_engine = values.get('engine') or str(Path(values['destination']) / 'FreeVideo-engine')
            else:
                selected_root = values.get('comfy')
                if not selected_root:
                    return False
                selected_engine = values.get('engine') or str(Path(layout(selected_root)['root']) / 'FreeVideo-engine')
            descriptor = layout(selected_root)
            engine = Path(selected_engine).expanduser().resolve()
            receipt_path = node_target(descriptor['root']) / 'freevideo-launcher.json'
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            source = Path(receipt['source']).resolve()
            if (receipt.get('protocol') != PROTOCOL or Path(receipt['engine_root']).resolve() != engine
                    or not (source / 'freevideo_engine/comfy_nodes.py').is_file()
                    or not (source / '__init__.py').is_file()
                    or hashlib.sha256((receipt_path.parent / '__init__.py').read_bytes()).hexdigest() != receipt.get('entry_sha256')):
                return False
            installation(source, {'FREEVIDEO_HOME': str(engine)})
            python = (record.get('python') or values.get('python') or
                      managed_python(engine, Path(descriptor['root'])) or descriptor.get('python'))
            if not python or not Path(python).is_file():
                return False
            self.selection = dict(descriptor, source=str(source), engine=str(engine), python=str(python),
                url=local_url(record.get('url') or values.get('url', '')), ready=True,
                separate=record.get('separate', values.get('separate', False)))
            from .desktop_runtime import matching_source
            self.state = dict(status='ready', engine_update_available=not matching_source(self.source, source),
                              selection=dict(self.selection))
            self.restore_terminal(engine)
            from .linux_bundle import appimage
            from .system import windows
            if getattr(sys, 'frozen', False) and (windows() or appimage() is not None):
                # Opening the launcher repairs a missing, broken or stale shortcut
                # even when no launch follows, such as one that failed to start.
                self.ensure_shortcut()
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def _launch(self, values):
        if not self.restore(values):
            raise ValueError('The saved installation is unavailable or incomplete. Return to Installation settings to locate or repair it; saved paths and model folders are retained.')
        self.state = dict(self.state, status='running', action='launch')
        self._connect()

    def stage(self, name, *, done=0, total=1, label=''):
        self.section = name
        offset = 0
        for key, count in self.sections:
            if key == name:
                fraction = min(1., max(0., done / total)) if total else 0.
                self.state = dict(self.state, overall=dict(done=offset + count * fraction,
                    total=sum(row[1] for row in self.sections), label=label))
                return
            offset += count

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def run(self, operation, *args):
        if self._closed:
            raise RuntimeError('The launcher is closing')
        if self.busy:
            raise ValueError('A launcher task is already running')
        if operation not in ('inspect', 'install', 'connect', 'launch', 'shortcut'):
            raise ValueError('Unknown launcher action')
        if operation == 'install' and (self.state.get('status') != 'review' or self.state.get('errors')):
            raise ValueError('Inspect and review a valid installation plan first')
        self.cancelled.clear()
        self.state = dict(self.state, status='running', action=operation, error=None, exception=None, task={}, overall={},
                          model_groups=[] if operation == 'inspect' else self.state.get('model_groups', []))
        if operation in ('connect', 'launch'):
            self.sections = [('open', 1)]
        def work():
            try:
                getattr(self, '_' + operation)(*args)
            except Exception as error:
                from .diagnostic_resources import exception_details
                self.state = dict(self.state, status='cancelled' if self.cancelled.is_set() else 'failed',
                                  error=str(error), exception=exception_details(error))
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancelled.set()
        if self.setup:
            self.setup.runner.cancel()

    def close(self):
        """Stop only the process tree created by this launcher instance."""
        with self._server_lock:
            self._closed = True
        try:
            self.cancel()
        finally:
            with self._server_lock:
                if self.server is not None:
                    processes.stop(self.server, grace=3)
                    self.server = None

    def _wait_setup(self):
        # The Setup service and its runner stay the single source of truth.
        while True:
            row = self.setup.status()
            self.state = dict(self.state, task=row,
                              model_groups=row.get('model_groups') or self.state.get('model_groups', []))
            phase = row.get('phase_progress', {})
            if phase.get('total') and self.section:
                self.stage(self.section, done=phase.get('done', 0), total=phase['total'], label=phase.get('label', ''))
            if self.cancelled.is_set():
                self.setup.runner.cancel()
            if not row['busy'] and row['status'] != 'running':
                if row['status'] != 'complete' and not (row.get('action') == 'plan' and row.get('plan')):
                    raise RuntimeError(row.get('error') or row.get('tail', '') or 'Installation stopped; completed files are retained.')
                if self.cancelled.is_set():
                    raise RuntimeError('Stopped; files retained')
                return row
            self.cancelled.wait(.1)

    def _inspect(self, values):
        self.selection = None
        self.sections, self.section = [], None
        self.state = dict(status='running', action='inspect')
        fresh = values.get('new_comfy', False)
        descriptor = new_layout(values.get('destination', '')) if fresh else layout(values['comfy'])
        node_target(descriptor['root'])
        default_parent = Path(descriptor['root']).parent if fresh else Path(descriptor['root'])
        engine = Path(values.get('engine') or default_parent / 'FreeVideo-engine').expanduser().resolve()
        if fresh and Path(descriptor['root']) in engine.parents:
            raise ValueError('Place the engine beside the new ComfyUI folder, so ComfyUI can be downloaded atomically.')
        cached_python = managed_python(engine, Path(descriptor['root']))
        host = (dict(ready=False, python=None, libraries=[str(Path(descriptor['root']) / 'models')])
                if fresh and not cached_python else probe_host(descriptor, values.get('python') or descriptor.get('python') or cached_python))
        descriptor.update(python=host.get('python'), separate=values.get('separate', False) or not host.get('ready'))
        # A repair also reinstalls the packages of the ComfyUI environment this
        # launcher made, in place; a user's own ComfyUI Python is never changed.
        repair_comfy = bool(values.get('repair') and cached_python and host.get('python')
                            and Path(host['python']) == Path(cached_python))
        # Validate before copying even the small launcher payload.
        folders = SimpleNamespace(base_path=descriptor['root'], models_dir=str(Path(descriptor['root']) / 'models'),
                                  get_folder_paths=lambda _: host.get('libraries', []))
        validator = Setup(self.source, folders, LauncherRunner)
        validator.validate_root(str(engine))
        source = materialize_source(self.source, engine / 'launcher' / 'source')
        self.setup = Setup(source, folders, LauncherRunner)
        from .hf_auth import validate
        self.setup.runner.token = validate(values.get('token', ''))
        url = local_url(values.get('url', ''))
        ready = False
        if not values.get('repair'):
            try:
                _, machine = installation(source, {'FREEVIDEO_HOME': str(engine)})
                # With every quality level requested, set up again only while some are missing.
                from .sampling_assets import installed as sampling_installed
                ready = not values.get('sampling_caches') or sampling_installed(machine)
            except (OSError, ValueError, KeyError):
                pass
        self.selection = dict(descriptor, engine=str(engine), source=str(source), url=url, ready=ready, repair_comfy=repair_comfy)
        self.state = dict(self.state, selection=dict(self.selection), host=host)
        if not ready:
            extra = values.get('model_dirs', [])
            if not isinstance(extra, list) or any(not isinstance(p, str) for p in extra):
                raise ValueError('Model folders must be a list of directory paths')
            extra = extra + ([values['models']] if values.get('models') else [])
            # A saved folder that no longer exists (for example a removed offline
            # package) has nothing to reuse; it must not stop the installation.
            extra = [p for p in dict.fromkeys(extra) if p.strip() and Path(p).expanduser().is_dir()]
            self.setup.inspect(dict(root=str(engine), extra_libraries=extra, copy=False,
                sampling_caches=bool(values.get('sampling_caches')), prepared_format=values.get('prepared_format'),
                frontend=dict(root=descriptor['root'], separate=descriptor['separate'], download=fresh)))
            row = self._wait_setup()
            self.state = dict(self.state, plan=row['plan'])
        extra_disk, disks, disk_errors = disk_review(self.state.get('plan', {}), engine, descriptor['root'],
            separate=descriptor['separate'], new_comfy=fresh and not Path(descriptor['root']).exists())
        errors = list(self.state.get('plan', {}).get('errors', []))
        self.state = dict(self.state, status='review', extra_disk_bytes=extra_disk, disks=disks,
                          errors=list(dict.fromkeys(errors + disk_errors)))

    def _install(self, accepted):
        if not accepted or not self.selection:
            raise ValueError('Review and accept the current installation plan first')
        selected = dict(self.selection)
        frontend = selected['separate'] or selected.get('repair_comfy')
        self.sections = ([('engine', 8)] if not selected['ready'] else []) + (
            [('comfy', 6 if selected.get('new_comfy') else 5)] if frontend else []) + [('nodes', 1), ('open', 1)]
        if self.cancelled.is_set():
            raise RuntimeError('Stopped; files retained')
        if selected.get('new_comfy'):
            validate_target(selected['root'])
        if not selected['ready']:
            self.stage('engine', label='Prepare FreeVideo')
            self._stop_server_for_setup(selected['url'])
            current = self.setup.status()
            self.setup.install(dict(plan_id=current.get('plan_id'), accept_licenses=True))
            self._wait_setup()
        if frontend:
            if self.cancelled.is_set():
                raise RuntimeError('Stopped; files retained')
            self.stage('comfy', label='Prepare ComfyUI')
            self._prepare_frontend(selected, download=bool(selected.get('new_comfy')), repair=bool(selected.get('repair_comfy')))
        if self.cancelled.is_set():
            raise RuntimeError('Stopped; files retained')
        self.stage('nodes', label='Install FreeVideo workflow')
        deployed = deploy(selected['root'], selected['source'], selected['engine'])
        selected['ready'] = True
        self.selection = selected
        self.state = dict(self.state, selection=dict(selected), deployed=deployed)
        self._connect()

    def _move_address(self, selected):
        """Take the next free port for this ComfyUI and keep it in the selection the launcher saves."""
        url = next_free_address(selected['url'])
        if url is None:
            raise ValueError('Other programs use this port and the next ones. Set a different local ComfyUI address.')
        if self.port_move is None:
            self.port_move = urlsplit(selected['url']).port or 80
        selected = self.selection = dict(selected, url=url)
        self.state = dict(self.state, selection=dict(selected))
        return url, selected

    def _stop_server_for_setup(self, url):
        """Setup reinstalls engine packages that our ComfyUI's resident worker
        keeps loaded after a video (Windows refuses to replace SageAttention's
        _fused.pyd), and a model switch must not leave the old model in that
        worker. Stop our own idle server; connecting after setup starts it
        again. A running job is never stopped."""
        if not self.owns_server():
            return
        if queue_busy(url):
            raise RuntimeError('ComfyUI is running a job. Try again after it finishes; the job is left running.')
        self.stop_owned_server()

    def ensure_shortcut(self):
        from .desktop_shortcut import create_after_install
        self.state = dict(self.state, shortcut=create_after_install(
            self.selection['engine'], self.selection['source'], portable_root=getattr(self, 'shortcut_root', None)))

    def _shortcut(self, previous_status='ready'):
        if not self.selection or not self.selection.get('ready'):
            raise ValueError('A completed installation is required to create its shortcut')
        self.ensure_shortcut()
        self.state = dict(self.state, status='open' if previous_status == 'open' else 'ready')

    def _connect(self):
        self.state = {key: value for key, value in self.state.items() if key != 'moved_from'}
        if not self.selection or not self.selection['ready']:
            raise ValueError('Choose and inspect ComfyUI first')
        selected = self.selection
        from .desktop_runtime import matching_source
        self.state = dict(self.state, engine_update_available=not matching_source(self.source, selected['source']))
        # Covers old installations, deleted links and directly connecting to an
        # existing server, as well as the first installation.
        self.ensure_shortcut()
        shield_engine(selected['engine'])
        url = selected['url']
        frontend_error = None
        if (selected.get('python') and managed_frontend(selected)
                and managed_python(Path(selected['engine']), Path(selected['root'])) is None):
            # ComfyUI was updated since its separate environment was prepared.
            self.sections = [('comfy', 5), ('open', 1)]
            self.stage('comfy', label='Update ComfyUI packages')
            try:
                self._prepare_frontend(selected)
            except Exception as error:
                if self.cancelled.is_set():
                    raise
                # Offline, say: the previous environment may still run this ComfyUI.
                frontend_error = str(error)
        self.stage('open', label='Open ComfyUI')
        info = server_info(url)
        if matches_server(info, selected['root'], selected['engine'], selected['source']):
            self.attach_console(info)
            self.stage('open', done=1, label='Ready')
            self.state = dict(self.state, status='open', url=url + '/?freevideo=launch')
            if self.port_move is not None:
                self.state = dict(self.state, moved_from=self.port_move)
                self.port_move = None
            return
        movable = self.move_when_taken and not self.owns_server() and launcher_comfy(selected, info)
        moves = 0
        if info['status'] != 'offline':
            if movable:
                url, selected = self._move_address(selected)
                moves += 1
                info = dict(status='offline')
            elif not self.owns_server():
                self.state = dict(self.state, status='restart-required', url=url,
                    error='ComfyUI is already running. Restart it once to load the installed FreeVideo nodes, then click Connect. Existing jobs are left running.')
                return
            if queue_busy(url):
                self.state = dict(self.state, status='restart-required', url=url,
                    error='ComfyUI is still running a job. Click Connect after it finishes to restart with the update; the job is left running.')
                return
            # Our own idle server still runs the previous source: restart it.
            self.stage('open', label='Restart ComfyUI')
            self.stop_owned_server()
        parsed = urlsplit(url)
        deadline = time.monotonic() + 10
        while True:
            try:
                bind_check(parsed.hostname, parsed.port or 80)
                break
            except OSError as error:
                # A server this launcher just stopped can hold the port briefly.
                if info['status'] != 'offline' and time.monotonic() < deadline:
                    self.cancelled.wait(.5)
                    continue
                if movable and moves < 12:
                    # A program that is not ComfyUI holds the address.
                    url, selected = self._move_address(selected)
                    moves += 1
                    parsed = urlsplit(url)
                    continue
                raise ValueError('This port is in use by another application. Set a different local ComfyUI address.') from error
        if not selected.get('python') or not Path(selected['python']).is_file():
            raise ValueError('ComfyUI Python is missing. Inspect again to prepare a separate environment.')
        directory = Path(selected['engine']) / 'launcher' / 'comfy-runs' / (time.strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(4))
        directory.mkdir(parents=True)
        self.server_log = directory / 'comfy.log'
        save(Path(selected['engine']) / 'launcher/comfy-console.json',
             dict(log=str(self.server_log.relative_to(Path(selected['engine']) / 'launcher'))))
        env = (self.child_environment() if self.child_environment is not None else
               isolated_environment(selected['engine'], selected['source']))
        env.pop('PYTHONPATH', None)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env.update(PYTHONUNBUFFERED='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        env['FREEVIDEO_COMFY_LOG'] = str(self.server_log)
        from .launcher_bridge import ENV as BRIDGE
        env.pop(BRIDGE, None)
        if self.update_bridge:
            env[BRIDGE] = str(self.update_bridge)
        command = [selected['python'], '-u', '-B', str(Path(selected['root']) / 'main.py'), '--listen', parsed.hostname,
                   '--port', str(parsed.port or 80), '--disable-auto-launch']
        managed = managed_frontend(selected)
        if managed:
            # This environment has ComfyUI core dependencies, not the user's
            # custom-node packages. Scope both prestartup scripts and imports;
            # a plugin can otherwise run pip, replace Torch, or call sys.exit.
            command += ['--disable-all-custom-nodes', '--whitelist-custom-nodes', node_target(selected['root']).name]
        context = dict(environment='freevideo-managed' if managed else 'existing-comfyui',
                       custom_nodes='FreeVideo' if managed else 'all')
        if selected.get('portable') and not selected['separate']:
            command.append('--windows-standalone-build')
        from .windows_ux import external_python
        with self._server_lock, external_python(), self.server_log.open('wb') as log:
            if self._closed or self.cancelled.is_set():
                raise RuntimeError('ComfyUI startup cancelled')
            if managed:
                log.write(b'[FreeVideo] Separate environment: loading FreeVideo custom nodes only. '
                          b'Use your original ComfyUI launcher for other plugins.\n')
                log.flush()
            # Keep durable embedded output, but own the whole tree: Windows
            # Job / Linux supervisor also reclaim independently grouped model
            # workers when the launcher dies without running Python cleanup.
            server = processes.popen(command, cwd=selected['root'], env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True, supervise=True)
            self.server = server
        save(directory / 'launch.json', dict(command=command, pid=server.pid, root=selected['root'], url=url, **context))
        self.state = dict(self.state, task=dict(progress=dict(label='Starting ComfyUI', detail='Loading nodes and the web interface'), log=str(directory)))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.cancelled.is_set():
                with self._server_lock:
                    processes.stop(server, grace=3)
                raise RuntimeError('ComfyUI startup cancelled')
            if server.poll() is not None:
                from .failure_details import startup_failure
                message = startup_failure('ComfyUI could not start.', directory / 'comfy.log',
                                          exit_code=server.returncode, context=context)
                if frontend_error:
                    message += '\n\nComfyUI packages could not be updated first: ' + frontend_error
                raise RuntimeError(message)
            info = server_info(url)
            if matches_server(info, selected['root'], selected['engine'], selected['source']):
                self.stage('open', done=1, label='Ready')
                self.state = dict(self.state, status='open', url=url + '/?freevideo=launch')
                if self.port_move is not None:
                    self.state = dict(self.state, moved_from=self.port_move)
                    self.port_move = None
                return
            self.cancelled.wait(.5)
        from .failure_details import startup_failure
        raise RuntimeError(startup_failure('ComfyUI is still starting or could not load FreeVideo; retry Connect.', directory / 'comfy.log'))
