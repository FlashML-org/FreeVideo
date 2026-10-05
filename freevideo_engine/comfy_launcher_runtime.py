"""ComfyUI launcher control plane. No Torch, package installs in the host, or models.

The existing Setup service owns the engine plan, approval, downloads and repair.
Only our node entry point and our template are installed in the selected ComfyUI.
An existing server is never stopped by this launcher.
"""
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
from .comfy_environment import isolated_environment
from .comfy_setup import Setup, SetupRunner
from .comfy_source import SOURCE_DISK_BYTES, new_layout, validate_target
from .desktop_runtime import materialize_source
from .monitoring import save
from . import processes

PROTOCOL = 1
TEMPLATE = 'FreeVideo-All-in-One.json'


def disk_review(plan, engine, comfy, *, separate, new_comfy):
    """Add frontend costs to the right volume, including cross-drive installs."""
    frontend = plan.get('frontend')
    if frontend and frontend['root'] == str(Path(comfy).resolve()) and frontend['separate'] == separate:
        from .install_disk import existing, errors
        disks = [dict(disk, paths=list(disk['paths']),
                      free_bytes=shutil.disk_usage(existing(disk['paths'][0])).free)
                 for disk in plan.get('disks', [])]
        return 0, disks, errors(disks)  # Already included before automatic mode selection.
    def existing(path):
        path = Path(path)
        while not path.exists() and path != path.parent:
            path = path.parent
        return path
    disks = {}
    for disk in plan.get('disks', []):
        parent = existing(disk['paths'][0])
        disks[parent.stat().st_dev] = dict(disk, paths=list(disk['paths']))
    extra = 0
    for path, amount in ((engine, 12 * 2**30 if separate else 0), (comfy, SOURCE_DISK_BYTES if new_comfy else 0)):
        if not amount:
            continue
        parent = existing(path)
        disk = disks.setdefault(parent.stat().st_dev, dict(paths=[str(parent)], needed_bytes=0))
        disk['free_bytes'] = shutil.disk_usage(parent).free
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
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
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
            return dict(status='restart-required')
    except (OSError, ValueError):
        pass
    return dict(status='offline')


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


def deploy(root, source, engine):
    """Update only our entry point; preserve source edits, libraries and templates."""
    root, source, engine = map(lambda p: Path(p).resolve(), (root, source, engine))
    target = node_target(root)
    target.mkdir(parents=True, exist_ok=True)
    receipt = target / 'freevideo-launcher.json'
    previous = json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {}
    entry = target / '__init__.py'
    old = entry.read_bytes() if entry.is_file() else None
    if old and previous.get('entry_sha256') and hashlib.sha256(old).hexdigest() != previous['entry_sha256']:
        raise ValueError('The managed FreeVideo entry point was edited. Changes are retained; restore it before updating.')
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
        if not values.get('repair') and not values.get('sampling_caches'):
            try:
                installation(source, {'FREEVIDEO_HOME': str(engine)})
                ready = True
            except (OSError, ValueError):
                pass
        self.selection = dict(descriptor, engine=str(engine), source=str(source), url=url, ready=ready)
        self.state = dict(self.state, selection=dict(self.selection), host=host)
        if not ready:
            extra = values.get('model_dirs', [])
            if not isinstance(extra, list) or any(not isinstance(p, str) for p in extra):
                raise ValueError('Model folders must be a list of directory paths')
            extra = extra + ([values['models']] if values.get('models') else [])
            self.setup.inspect(dict(root=str(engine), extra_libraries=extra, copy=False,
                sampling_caches=bool(values.get('sampling_caches')),
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
        self.sections = ([('engine', 8)] if not selected['ready'] else []) + (
            [('comfy', 6 if selected.get('new_comfy') else 5)] if selected['separate'] else []) + [('nodes', 1), ('open', 1)]
        if self.cancelled.is_set():
            raise RuntimeError('Stopped; files retained')
        if selected.get('new_comfy'):
            validate_target(selected['root'])
        if not selected['ready']:
            self.stage('engine', label='Prepare FreeVideo')
            current = self.setup.status()
            self.setup.install(dict(plan_id=current.get('plan_id'), accept_licenses=True))
            self._wait_setup()
        if selected['separate']:
            if self.cancelled.is_set():
                raise RuntimeError('Stopped; files retained')
            self.setup.state = dict(status='running', action='comfy-host')
            self.setup.events.reset()
            self.stage('comfy', label='Prepare ComfyUI')
            arguments = ['comfy-host', '--comfy', selected['root']]
            if selected.get('new_comfy'):
                arguments.append('--download-comfy')
            self.setup.runner.start('comfy-host', selected['engine'], arguments)
            self._wait_setup()
            descriptor = json.loads((Path(selected['engine']) / 'launcher' / 'comfy-host.json').read_text(encoding='utf-8'))
            selected['python'] = descriptor['python']
        if self.cancelled.is_set():
            raise RuntimeError('Stopped; files retained')
        self.stage('nodes', label='Install FreeVideo workflow')
        deployed = deploy(selected['root'], selected['source'], selected['engine'])
        selected['ready'] = True
        self.selection = selected
        self.state = dict(self.state, selection=dict(selected), deployed=deployed)
        self._connect()

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
        if not self.selection or not self.selection['ready']:
            raise ValueError('Choose and inspect ComfyUI first')
        selected = self.selection
        from .desktop_runtime import matching_source
        self.state = dict(self.state, engine_update_available=not matching_source(self.source, selected['source']))
        # Covers old installations, deleted links and directly connecting to an
        # existing server, as well as the first installation.
        self.ensure_shortcut()
        self.stage('open', label='Open ComfyUI')
        url = selected['url']
        info = server_info(url)
        if matches_server(info, selected['root'], selected['engine'], selected['source']):
            self.attach_console(info)
            self.stage('open', done=1, label='Ready')
            self.state = dict(self.state, status='open', url=url + '/?freevideo=launch')
            return
        if info['status'] != 'offline':
            if not self.owns_server():
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
            with socket.socket(socket.AF_INET6 if parsed.hostname == '::1' else socket.AF_INET, socket.SOCK_STREAM) as check:
                if sys.platform == 'darwin':
                    # Reopening our own server must tolerate TIME_WAIT on macOS.
                    check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    check.bind((parsed.hostname, parsed.port or 80))
                    break
                except OSError as error:
                    # A server this launcher just stopped can hold the port briefly.
                    if info['status'] != 'offline' and time.monotonic() < deadline:
                        self.cancelled.wait(.5)
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
                raise RuntimeError(startup_failure('ComfyUI could not start.', directory / 'comfy.log',
                                                  exit_code=server.returncode, context=context))
            info = server_info(url)
            if matches_server(info, selected['root'], selected['engine'], selected['source']):
                self.stage('open', done=1, label='Ready')
                self.state = dict(self.state, status='open', url=url + '/?freevideo=launch')
                return
            self.cancelled.wait(.5)
        from .failure_details import startup_failure
        raise RuntimeError(startup_failure('ComfyUI is still starting or could not load FreeVideo; retry Connect.', directory / 'comfy.log'))
