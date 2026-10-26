"""ComfyUI launcher control plane. No Torch, package installs in the host, or models.

The existing Setup service owns the engine plan, approval, downloads and repair.
Only our node entry point and our template are installed in the selected ComfyUI.
An existing server is never stopped by this launcher.
"""
import errno
import hashlib
import ipaddress
import re
import json
import logging
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

from . import execution_paths
from .comfy_bridge import installation
from . import disk_space, launch_view
from .comfy_environment import isolated_environment
from .comfy_setup import Setup, SetupRunner
from .comfy_source import RECEIPT, SOURCE_DISK_BYTES, new_layout, validate_target
from .desktop_runtime import materialize_source, source_release
from .monitoring import save
from .long_paths import io_path, resolved, plain, plain_text, plain_error, plain_errors
from . import engine_binding, processes, lan_password

PROTOCOL = 1
TEMPLATE = 'FreeVideo-All-in-One.json'
# Best-effort cancellation must not hold application shutdown hostage. The
# daemon's join is a wall-clock bound even if a server trickles HTTP bytes.
SHUTDOWN_CANCEL_SECONDS = 3.
SHUTDOWN_REQUEST_SECONDS = .5
# After this long the card says ComfyUI is still loading; the wait goes on while the process lives, up to
# the limit. The log is no signal: importing Torch and its libraries can print nothing for minutes.
START_WAIT_SECONDS = 180
START_WAIT_LIMIT_SECONDS = 600
SHUTDOWN_POLL_SECONDS = .1


def cancel_running_generation(url):
    """Use the page's request-local cancel route, then wait until those requests end.

    The bridge writes its existing durable cancellation receipt before stopping
    the CLI, so a request has its receipt once it leaves the running queue. No
    receipt is invented here when ComfyUI cannot answer. Requests still waiting
    in the queue never launched a worker; stopping the server discards them.
    """
    deadline = time.monotonic() + SHUTDOWN_CANCEL_SECONDS
    stopped = threading.Event()

    def timeout():
        remaining = deadline - time.monotonic()
        if stopped.is_set() or remaining <= 0:
            raise TimeoutError('Shutdown cancellation deadline')
        return min(SHUTDOWN_REQUEST_SECONDS, remaining)

    def cancel_and_wait():
        try:
            requested = set()
            while True:
                queue = get_json(url + '/queue', timeout=timeout())
                running = queue['queue_running']
                targets = [(row[1], str(node)) for row in running
                           for node, value in row[2].items()
                           if value.get('class_type') == 'FreeVideoGenerate']
                fresh = [target for target in targets if target not in requested]
                # Done once nothing new is generating and every cancelled request has
                # ended. An idle queue (or another node type) needs no wait at all.
                if not fresh and not any(target in requested for target in targets):
                    return
                for prompt_id, node_id in fresh:
                    request = Request(url + '/freevideo/cancel',
                        data=json.dumps(dict(prompt_id=prompt_id, node_id=node_id)).encode(),
                        headers={'Content-Type': 'application/json'}, method='POST')
                    with build_opener(ProxyHandler({})).open(request, timeout=timeout()) as reply:
                        reply.read(4096)
                    requested.add((prompt_id, node_id))
                stopped.wait(min(SHUTDOWN_POLL_SECONDS, timeout()))
        except Exception:
            # Missing route, malformed queue, timeout and HTTP errors all fall
            # back to the same process-tree stop. Shutdown never depends on it.
            return

    worker = threading.Thread(target=cancel_and_wait, name='FreeVideo shutdown cancellation', daemon=True)
    worker.start()
    worker.join(max(0., deadline - time.monotonic()))
    stopped.set()


class OlderLauncher(ValueError):
    def __init__(self, launcher_label, installed_label):
        self.launcher_label = launcher_label
        self.installed_label = installed_label
        super().__init__(f'This launcher ({launcher_label}) is older than the installed FreeVideo '
                         f'({installed_label}); nothing was changed.')


@plain_errors
def check_source_release(source, installed):
    """Reject only a provably older source, before any installation writes."""
    from .release_notes import label, older
    if not isinstance(installed, (str, Path)) or not installed:
        return
    source, installed = resolved(source), resolved(installed)
    if not installed.is_dir() or source == installed:
        return
    candidate, current = source_release(source), source_release(installed)
    if older(candidate, current):
        raise OlderLauncher(label(candidate), label(current))


@plain_errors
def check_installed_sources(source, root, engine):
    """Either the node receipt or machine record can identify the installed code."""
    for receipt in (io_path(node_target(root)) / 'freevideo-launcher.json', io_path(engine) / 'machine.json'):
        try:
            value = json.loads(receipt.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict):
            check_source_release(source, value.get('source'))


@plain_errors
def disk_review(plan, engine, comfy, *, separate, new_comfy):
    """Add frontend costs to the right volume, including cross-drive installs."""
    frontend = plan.get('frontend')
    if frontend and frontend['root'] == plain(resolved(comfy)) and frontend['separate'] == separate:
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
        disk = disks.setdefault(disk_space.disk_key(parent), dict(paths=[plain(parent)], needed_bytes=0))
        disk['free_bytes'] = disk_space.free_bytes(parent)
        disk['needed_bytes'] += amount
        extra += amount
    from .install_disk import errors
    return extra, list(disks.values()), errors(disks.values())


def _is_comfy_root(path):
    return (path / 'main.py').is_file() and (path / 'folder_paths.py').is_file()


def nearby_comfy(selected):
    """Name of a ComfyUI folder just above or one level below the chosen one, else ''.

    Only a name is returned: the launcher hides absolute paths in its messages.
    """
    try:
        for parent in (selected, *list(selected.parents)[:3]):
            if _is_comfy_root(parent) or _is_comfy_root(parent / 'ComfyUI'):
                return parent.name
        for child in sorted(p for p in selected.iterdir() if p.is_dir())[:200]:
            if _is_comfy_root(child) or _is_comfy_root(child / 'ComfyUI'):
                return child.name
    except OSError:
        pass
    return ''


def layout(path):
    try:
        selected = resolved(Path(path).expanduser())
        root = selected / 'ComfyUI' if (selected / 'ComfyUI' / 'main.py').is_file() else selected
        if not _is_comfy_root(root):
            found = nearby_comfy(selected)
            raise ValueError('Select the ComfyUI folder, or the portable folder containing ComfyUI.'
                             + (f' Found one at "{found}"; select that folder.' if found else ''))
        if not (root / 'comfy_api' / 'latest').is_dir():
            raise ValueError('This ComfyUI is too old for native VIDEO / V3 nodes. Update ComfyUI first; its files have not been changed.')
        candidates = []
        for parent in (root, root.parent):
            for name in ('python_embeded', 'python_embedded', '.venv', 'venv', 'env', 'python'):
                directory = parent / name
                candidates.extend([directory / 'python.exe', directory / 'Scripts/python.exe', directory / 'bin/python'])
        python = next((p.resolve() for p in candidates if p.is_file()), None)
        return dict(root=plain(root), python=plain(python) if python else None,
                    portable=bool(python and python.parent.name in ('python_embeded', 'python_embedded')))
    except (OSError, ValueError) as error:
        plain_error(error)
        raise


def host_repair_pending(python):
    """A host repair started in this environment and has not finished (comfy_host's marker)."""
    return bool(python) and (io_path(python).parent.parent / 'freevideo-host-repair.json').exists()


def _managed_environment(engine, comfy):
    """(python, recorded identity) of our previously completed frontend, or None."""
    engine, comfy = io_path(engine), io_path(comfy)
    try:
        record = json.loads((engine / 'launcher/comfy-host.json').read_text(encoding='utf-8'))
        identity = record['identity']
        environment, python = io_path(record['environment']), io_path(record['python'])
        machine = json.loads((engine / 'machine.json').read_text(encoding='utf-8'))
        if (identity['comfy'] != plain(comfy) or identity['engine_python'] != machine['python']
                or identity['requirements'] != hashlib.sha256((comfy / 'requirements.txt').read_bytes()).hexdigest()
                or environment.resolve().parent != (engine / 'envs').resolve()
                or python != environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
                or json.loads((environment / 'freevideo-host.json').read_text(encoding='utf-8')) != identity
                or not python.is_file()):
            return None
        return plain(python), identity
    except (OSError, ValueError, KeyError, TypeError):
        return None


def managed_python(engine, comfy):
    """Find our previously completed frontend without requiring its full disk budget again.

    Packages our own nodes gained since (node_packages_pending) do not make it unusable:
    ComfyUI and generation run without them, and only what needs them is not offered.
    """
    found = _managed_environment(engine, comfy)
    return found and found[0]


NODE_PACKAGES_RETRY = 6 * 3600


def node_packages_pending(engine, comfy, *, now=None):
    """Our frontend runs, but lacks packages our nodes gained in an update, and it is time to try adding them.

    A failed attempt (offline, say) is tried again after NODE_PACKAGES_RETRY, not at every start.
    """
    from .comfy_host import NODE_PACKAGES
    found = _managed_environment(engine, comfy)
    if not found or found[1].get('nodes', []) == list(NODE_PACKAGES):
        return False
    try:
        failed = json.loads((io_path(engine) / 'launcher/node-packages.json').read_text(encoding='utf-8'))
        recent = failed['nodes'] == list(NODE_PACKAGES) and 0 <= (now or time.time()) - failed['failed'] < NODE_PACKAGES_RETRY
    except (OSError, ValueError, KeyError, TypeError):
        recent = False
    return not recent


def node_packages_failed(engine):
    """Remember that adding the node packages failed just now; the rest of FreeVideo carries on."""
    from .comfy_host import NODE_PACKAGES
    try:
        save(io_path(engine) / 'launcher/node-packages.json', dict(nodes=list(NODE_PACKAGES), failed=time.time()))
    except OSError:
        pass


@plain_errors
def managed_frontend(selected):
    """Identify our frontend even after reopening makes `separate` false.

    Do not resolve the executable: venv Python is a symlink on Linux, but its
    environment and installed packages still belong to the venv directory.
    """
    python = io_path(os.path.abspath(selected['python']))
    environment = python.parent.parent
    return (python.parent.name in ('Scripts', 'bin')
            and environment.name.startswith('comfyui-')
            and environment.parent.resolve() == (io_path(selected['engine']) / 'envs').resolve())


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
            result = execution_paths.run([str(python), '-I', '-B', '-c', HOST_PROBE, descriptor['root']],
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
    host = parsed.hostname
    try:
        ip = ipaddress.ip_address(host) if host != 'localhost' else None
    except ValueError:
        raise ValueError('Enter localhost or an IP address of this computer, for example http://127.0.0.1:8188.') from None
    if (parsed.scheme != 'http' or not host or (ip is not None and ip.is_multicast)
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError('Enter localhost or an IP address of this computer, for example http://127.0.0.1:8188.')
    port = parsed.port if parsed.port is not None else 80
    if not 1 <= port <= 65535:
        raise ValueError('Invalid ComfyUI port')
    return urlunsplit(('http', parsed.netloc, '', '', ''))


def loopback_url(value):
    """The launcher's browser and control requests always use IPv4 loopback."""
    parsed = urlsplit(value)
    return urlunsplit(('http', '127.0.0.1:%d' % (parsed.port or 80), parsed.path, parsed.query, parsed.fragment))


def loopback_host(hostname):
    try:
        return hostname == 'localhost' or ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def listen_hosts(hostname):
    # A specific interface (including IPv6 loopback) does not accept IPv4
    # loopback. ComfyUI supports a comma-separated list of listening addresses.
    if hostname in ('localhost', '127.0.0.1', '0.0.0.0'):
        return (hostname,)
    return ('127.0.0.1', hostname)


def matches_listener(info, url):
    """An external server must already listen on the requested interfaces."""
    wanted = urlsplit(url).hostname
    hosts = set((info.get('listen') or '').split(','))
    if hosts == {''}:
        return wanted in ('localhost', '127.0.0.1', '::1')
    hosts = {'127.0.0.1' if host == 'localhost' else host for host in hosts}
    wanted = '127.0.0.1' if wanted == 'localhost' else wanted
    return wanted in hosts or ('::' if ':' in wanted else '0.0.0.0') in hosts


def get_json(url, timeout=2):
    # Proxy settings must never send loopback requests to an external proxy.
    with build_opener(ProxyHandler({})).open(Request(loopback_url(url), headers={'Accept': 'application/json'}), timeout=timeout) as reply:
        value = reply.read(2 * 1024 * 1024 + 1)
        if len(value) > 2 * 1024 * 1024:
            raise ValueError('ComfyUI response is too large')
        return json.loads(value)


def server_info(url):
    url = loopback_url(url)
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
    hosts = ('127.0.0.1', '::1') if hostname == 'localhost' else listen_hosts(hostname)
    if hostname in ('0.0.0.0', '::'):
        hosts = (*hosts, '127.0.0.1' if hostname == '0.0.0.0' else '::1')
    for host in hosts:
        try:
            with socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, socket.SOCK_STREAM) as check:
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


def lan_access_links(engine, port, *, password_set=None):
    """Use the same phone entry and authentication policy for launcher and page."""
    from .comfy_service import token
    from .proxy import local_addresses
    hosts = local_addresses()
    if not hosts:
        return []
    if password_set is None:
        password_set = lan_password.is_set(engine)
    suffix = '/m/' if password_set else '/m/?freevideo=launch&token=' + token(engine)
    return [address(host, port) + suffix for host in hosts]


def reserved_port(error):
    """Whether no program may listen on this port at all. Windows with WSL or Hyper-V reserves
    whole blocks of ports (often 100 or more in a row), and binding one fails with WSAEACCES."""
    return error.errno == errno.EACCES or getattr(error, 'winerror', None) == 10013


def next_free_address(url, count=12):
    """The next local address after this one that nothing listens or answers on, as setup.sh chooses.
    Reserved ports are skipped without counting, so a reserved block does not end the search."""
    parsed = urlsplit(url)
    port = parsed.port or 80
    while count > 0 and port < 65535:
        port += 1
        try:
            bind_check(parsed.hostname, port)
        except OSError as error:
            if not reserved_port(error):
                count -= 1
            continue
        count -= 1
        candidate = address(parsed.hostname, port)
        if server_info(loopback_url(candidate))['status'] == 'offline':
            return candidate
    return None


@plain_errors
def launcher_comfy(selected, info):
    """Whether the launcher downloaded this ComfyUI and runs it in its own environment, and the
    server at its address is not this ComfyUI. Such a server belongs to someone else, so this
    ComfyUI can move to another port instead of asking for that server to be closed."""
    root = io_path(selected['root'])
    if not (selected.get('python') and managed_frontend(selected) and (root / RECEIPT).is_file()):
        return False
    if info['status'] == 'freevideo':
        return resolved(info['comfy_root']) != root.resolve()
    if info['status'] == 'offline':
        return True
    main = info.get('main') or ''
    if Path(main).is_absolute():
        return resolved(main) != (root / 'main.py').resolve()
    try:
        # ComfyUI answers {kind: [absolute folders]}; its custom_nodes folder names its installation.
        folders = get_json(selected['url'] + '/internal/folder_paths', timeout=1)
        nodes = folders.get('custom_nodes') if isinstance(folders, dict) else None
        if (not isinstance(nodes, list) or not nodes
                or not all(isinstance(path, str) and Path(path).is_absolute() for path in nodes)):
            return False
        return all(resolved(path) != (root / 'custom_nodes').resolve() for path in nodes)
    except (OSError, ValueError):
        return False


def queue_busy(url):
    """Whether ComfyUI is running or holding jobs. An unreadable queue counts as idle."""
    try:
        value = get_json(loopback_url(url) + '/queue', timeout=3)
        return bool(value.get('queue_running') or value.get('queue_pending'))
    except (OSError, ValueError, AttributeError):
        return False


@plain_errors
def matches_server(info, root, engine, source):
    return (info.get('status') == 'freevideo'
            and all(info.get(k) and resolved(info[k]) == resolved(p)
                    for k, p in (('comfy_root', root), ('engine_root', engine), ('source', source))))


def node_target(root):
    parent = io_path(root) / 'custom_nodes'
    found = []
    if parent.is_dir():
        for path in parent.iterdir():
            if path.name.endswith('.disabled') or not path.is_dir():
                continue
            code = path / 'freevideo_engine' / 'comfy_nodes.py'
            if (path / 'freevideo-launcher.json').is_file() or (code.is_file() and 'FreeVideoGenerate' in code.read_text(encoding='utf-8')):
                found.append(path)
    if len(found) > 1:
        managed = [path for path in found if (path / 'freevideo-launcher.json').is_file()]
        if len(managed) != 1:
            raise ValueError('Multiple FreeVideo node installations found. Keep one enabled before installing.')
        logging.getLogger(__name__).warning('Multiple FreeVideo node installations found; using %s with '
            'freevideo-launcher.json and leaving the other folders unchanged.', plain(managed[0]))
        found = managed
    target = found[0] if found else parent / 'FreeVideo'
    if target.exists() and not found and any(target.iterdir()):
        raise ValueError('custom_nodes/FreeVideo already contains unrelated files. Choose another ComfyUI or rename that folder first.')
    return Path(plain(target))


GIT_IGNORE = ('# FreeVideo engine: environments, models and caches, not part of any git checkout.\n'
              '# Keeps an update of a surrounding ComfyUI from copying, moving or deleting them.\n*\n')



def launcher_downloaded(root):
    """Whether this ComfyUI folder is the copy the launcher itself downloaded (its source receipt)."""
    try:
        value = json.loads((io_path(root) / RECEIPT).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(value, dict) and bool(value.get('url')) and bool(value.get('commit'))


def _plugin_names(root):
    try:
        return sorted(p.name for p in (io_path(root) / 'custom_nodes').iterdir() if p.name != '__pycache__')
    except OSError:
        return []


SCOPED_RECORD = 'launcher/scoped-nodes.json'


def remembered_scope(engine, root):
    """The user's plugins stopped FreeVideo from loading with this very set of plugins before."""
    try:
        value = json.loads((io_path(engine) / SCOPED_RECORD).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return (isinstance(value, dict) and value.get('root') == plain(resolved(root))
            and value.get('nodes') == _plugin_names(root))


def remember_scope(engine, root):
    try:
        save(Path(engine) / SCOPED_RECORD, dict(root=plain(resolved(root)), nodes=_plugin_names(root)))
    except OSError:
        pass


def freevideo_import_failed(path, name, offset):
    """(failed, new offset): ComfyUI's own import failure line for exactly our node folder, read incrementally."""
    pattern = re.compile(r'[\\/]custom_nodes[\\/]' + re.escape(name) + r'(?![\w.\-])')
    try:
        with io_path(path).open('rb') as handle:
            handle.seek(offset)
            data = handle.read(1 << 20)
    except OSError:
        return False, offset
    for line in data.decode('utf-8', 'replace').splitlines():
        if ('Cannot import' in line or 'IMPORT FAILED' in line) and pattern.search(line):
            return True, offset + len(data)
    return False, offset + len(data)

def shield_engine(engine):
    """Keep a surrounding git checkout's operations away from the engine.

    Inside an existing ComfyUI the engine is an untracked folder of tens of
    gigabytes. An updater that stashes or cleans untracked files (git stash -u,
    git add -A, git clean) would copy it into git or move it away. Only the
    launcher's engine folders get the marker, never a source checkout.
    """
    engine = io_path(engine)
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
            and (io_path(folder) / 'freevideo_engine' / 'comfy_nodes.py').is_file())


def deploy(root, source, engine):
    """Update only our entry point; preserve source edits, libraries and templates."""
    root, source, engine = map(resolved, (root, source, engine))
    target = io_path(node_target(root))
    receipt = target / 'freevideo-launcher.json'
    previous = json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {}
    check_source_release(source, previous.get('source'))
    shield_engine(engine)
    target.mkdir(parents=True, exist_ok=True)
    entry = target / '__init__.py'
    old = entry.read_bytes() if entry.is_file() else None
    if (old is not None and previous.get('entry_sha256') and hashlib.sha256(old).hexdigest() != previous['entry_sha256']
            and not checkout_entry(old, target)):
        logging.getLogger(__name__).warning('The managed FreeVideo entry point (%s) was edited; '
            'backing it up in launcher/backups before restoring the loader.', plain(entry))
    # The small loader may be replaced; all original checkout files remain intact.
    code = ("# Installed by FreeVideo launcher. Original entry points are retained in the engine launcher/backups folder.\n"
            "import importlib.util as _util\nimport json as _json\nfrom pathlib import Path as _Path\nimport sys as _sys\n"
            "_record = _json.loads((_Path(__file__).parent / 'freevideo-launcher.json').read_text(encoding='utf-8'))\n"
            "_source = _Path(_record['source'])\n"
            # The receipt keeps the real folder; beyond the path limit the source is read from the short root.
            + launch_view.entry_lines(launch_view.for_engine(engine)) +
            "_spec = _util.spec_from_file_location(__name__ + '._engine', _source / '__init__.py', submodule_search_locations=[str(_source)])\n"
            "_engine = _util.module_from_spec(_spec)\n_sys.modules[_spec.name] = _engine\n_spec.loader.exec_module(_engine)\n"
            "WEB_DIRECTORY = str(_source / 'web')\ncomfy_entrypoint = _engine.comfy_entrypoint\n").encode()
    if old is not None and old != code:
        backup = engine / 'launcher' / 'backups' / (target.name + '-' + hashlib.sha256(old).hexdigest()[:12] + '-init.py')
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            backup.write_bytes(old)
    save(source / 'comfyui.json', dict(installation=plain(engine)))
    save(receipt, dict(protocol=PROTOCOL, source=plain(source), engine_root=plain(engine), entry_sha256=hashlib.sha256(code).hexdigest()))
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
        saved.append(plain(file))
    return dict(node=plain(target), workflows=saved)


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
        self._access_poll_thread = None
        self.cancelled = threading.Event()
        self.server = None
        self.server_url = None
        self.server_address = None
        self.access_proxy = None
        self.access_configuration = None
        # Shared only by the ComfyUI this controller starts and the access proxy in front of it.
        self.hop_secret = secrets.token_urlsafe(32)
        self.access_links = []
        self.access_polled = 0
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
        self._scope_retry = False

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

    @plain_errors
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
        # Anything that installs into this environment stops our idle server first
        # and never runs beside a job, including a host repair resumed here.
        if (repair or host_repair_pending(selected.get('python'))
                or managed_python(Path(selected['engine']), Path(selected['root'])) is None
                or node_packages_pending(Path(selected['engine']), Path(selected['root']))):
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
        descriptor = json.loads((io_path(selected['engine']) / 'launcher' / 'comfy-host.json').read_text(encoding='utf-8'))
        selected['python'] = plain(descriptor['python'])

    def owns_server(self):
        return self.server is not None and self.server.poll() is None

    def stop_owned_server(self, *, grace=5):
        with self._server_lock:
            self.stop_access_proxy()
            if self.server is not None:
                if self.server.poll() is None and self.server_url:
                    try:
                        cancel_running_generation(self.server_url)
                    except Exception:
                        # Even failure to start the bounded helper must not
                        # prevent the original process-tree cleanup.
                        pass
                processes.stop(self.server, grace=grace)
                self.server = None
                self.server_address = None

    def stop_access_proxy(self):
        with self._server_lock:
            if self.access_proxy is not None:
                processes.stop(self.access_proxy, grace=3)
                self.access_proxy = None
            self.access_configuration = None
            self.access_links = []
            self.state = dict(self.state, lan_status='')

    def _access_stopped(self):
        self.stop_access_proxy()
        self.selection = dict(self.selection, lan_access=False)
        self.state = dict(self.state, selection=dict(self.selection), lan_status='stopped')

    def refresh_access_links(self):
        self.access_polled = time.monotonic()
        password_set = lan_password.is_set(self.selection['engine'])
        self.access_links = lan_access_links(self.selection['engine'], self.selection['lan_port'],
                                            password_set=password_set)
        self.state = dict(self.state, lan_status='' if self.access_links else 'no-address', lan_password_set=password_set)

    def check_access_proxy(self):
        """Poll child lifetimes and refresh network links without moving ComfyUI."""
        if self.access_proxy is None:
            return
        if self.server is not None and self.server.poll() is not None:
            self._access_stopped()
            self.state = dict(self.state, status='ready')
        elif self.access_proxy.poll() is not None:
            self._access_stopped()
        elif time.monotonic() - self.access_polled >= 5:
            if self.server is None and server_info(self.selection['url'])['status'] == 'offline':
                self._access_stopped()
                self.state = dict(self.state, status='ready')
            else:
                self.refresh_access_links()

    def poll_access_proxy(self):
        """Keep network/file probes and bounded process cleanup off the UI thread.

        The check is not a launcher task: the window stays as it is while it
        runs. A task started meanwhile waits for it in run().
        """
        poll = self._access_poll_thread
        if self._closed or self.busy or (poll is not None and poll.is_alive()):
            return
        child = getattr(self, 'comfy_controller', None) or self
        proxy = child.access_proxy
        if proxy is None:
            return
        exited = ((child.server is not None and child.server.poll() is not None)
                  or proxy.poll() is not None)
        if not exited and time.monotonic() - child.access_polled < 5:
            return
        def work():
            try:
                self.check_access_proxy()
            except Exception:
                # A failed token/interface read withdraws the unusable link, keeps
                # the local page and lets the user turn access on again.
                child._access_stopped()
                if child is not self:
                    self._sync_access(False)
        self._access_poll_thread = threading.Thread(target=work, name='freevideo-access-poll', daemon=True)
        self._access_poll_thread.start()

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
            root = io_path(engine) / 'launcher'
            value = json.loads((root / 'comfy-console.json').read_text(encoding='utf-8'))
            path = (root / value['log']).resolve()
            path.relative_to((root / 'comfy-runs').resolve())
            if path.is_file():
                self.server_log = Path(plain(path))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def attach_console(self, info):
        if info.get('console_log'):
            try:
                root = io_path(self.selection['engine']) / 'launcher'
                path = resolved(info['console_log'])
                path.relative_to((root / 'comfy-runs').resolve())
                self.server_log = Path(plain(path))
                save(root / 'comfy-console.json', dict(log=str(path.relative_to(root.resolve()))))
            except (OSError, ValueError) as error:
                plain_error(error)
                raise
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
            engine = Path(plain(resolved(Path(selected_engine).expanduser())))
            receipt_path = io_path(node_target(descriptor['root'])) / 'freevideo-launcher.json'
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            source = Path(plain(resolved(receipt['source'])))
            if (receipt.get('protocol') != PROTOCOL or Path(plain(resolved(receipt['engine_root']))) != engine
                    or not io_path(source / 'freevideo_engine/comfy_nodes.py').is_file()
                    or not io_path(source / '__init__.py').is_file()):
                return False
            entry = receipt_path.parent / '__init__.py'
            if hashlib.sha256(entry.read_bytes()).hexdigest() != receipt.get('entry_sha256'):
                logging.getLogger(__name__).warning('The managed FreeVideo entry point (%s) differs from '
                    'entry_sha256; continuing with the saved installation.', plain(entry))
            installation(source, {'FREEVIDEO_HOME': str(engine)})
            python = (record.get('python') or values.get('python') or
                      managed_python(engine, Path(descriptor['root'])) or descriptor.get('python'))
            if not python or not io_path(python).is_file():
                return False
            self.selection = dict(descriptor, source=str(source), engine=str(engine), python=plain(python),
                url=local_url(record.get('url') or values.get('url', '')), ready=True,
                lan_access=record.get('lan_access', values.get('lan_access', False)) is True,
                lan_port=record.get('lan_port'),
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

    def open_ceiling(self):
        """How far through the last section the start-up wait may show: the whole bar stays under 97%."""
        total = sum(count for _, count in self.sections)
        offset = 0
        for key, count in self.sections:
            if key == 'open':
                return max(0., min(.9, (.97 * total - offset) / count)) if count else 0.
            offset += count
        return 0.

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def run(self, operation, *args):
        if self._closed:
            raise RuntimeError('The launcher is closing')
        if self.busy:
            raise ValueError('A launcher task is already running')
        if operation not in ('inspect', 'install', 'connect', 'launch', 'shortcut', 'configure_access'):
            raise ValueError('Unknown launcher action')
        if operation == 'install' and (self.state.get('status') != 'review' or self.state.get('errors')):
            raise ValueError('Inspect and review a valid installation plan first')
        poll = self._access_poll_thread
        if poll is not None:
            # A link check in flight finishes first, so it cannot overwrite this task's state.
            poll.join()
        self.cancelled.clear()
        self.state = dict(self.state, status='running', action=operation, error=None, exception=None, task={}, overall={},
                          model_groups=[] if operation == 'inspect' else self.state.get('model_groups', []))
        if operation in ('connect', 'launch', 'configure_access'):
            self.sections = [('open', 1)]
        def work():
            try:
                getattr(self, '_' + operation)(*args)
            except Exception as error:
                from .diagnostic_resources import exception_details
                opened = self.state.get('status') == 'open'
                if operation == 'configure_access':
                    # The address change may already have stopped the old server.
                    # A remembered URL alone cannot prove the local page is usable.
                    opened = bool(self.state.get('url')) and server_info(self.state['url'].split('/?')[0])['status'] == 'freevideo'
                status = 'open' if opened else 'failed'
                self.state = dict(self.state, status='cancelled' if self.cancelled.is_set() else status,
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
            self.stop_owned_server(grace=3)

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

    @plain_errors
    def _inspect(self, values):
        self.selection = None
        self.sections, self.section = [], None
        self.state = dict(status='running', action='inspect')
        fresh = values.get('new_comfy', False)
        descriptor = new_layout(values.get('destination', '')) if fresh else layout(values['comfy'])
        default_parent = Path(descriptor['root']).parent if fresh else Path(descriptor['root'])
        engine = Path(plain(resolved(Path(values.get('engine') or default_parent / 'FreeVideo-engine').expanduser())))
        check_installed_sources(self.source, descriptor['root'], engine)
        if fresh and Path(descriptor['root']) in engine.parents:
            raise ValueError('Place the engine beside the new ComfyUI folder, so ComfyUI can be downloaded atomically.')
        cached_python = managed_python(engine, Path(descriptor['root']))
        host = (dict(ready=False, python=None, libraries=[str(Path(descriptor['root']) / 'models')])
                if fresh and not cached_python else probe_host(descriptor, values.get('python') or descriptor.get('python') or cached_python))
        descriptor.update(python=host.get('python'), separate=values.get('separate', False) or not host.get('ready'))
        # A repair also reinstalls the packages of the ComfyUI environment this
        # launcher made, in place; a user's own ComfyUI Python is never changed.
        repair_pending = host_repair_pending(cached_python)
        repair_comfy = bool((values.get('repair') or repair_pending) and cached_python and host.get('python')
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
        if not values.get('repair') and not repair_comfy:
            try:
                _, machine = installation(source, {'FREEVIDEO_HOME': str(engine)})
                # With every quality level requested, set up again only while some are missing.
                from .sampling_assets import installed as sampling_installed
                ready = not values.get('sampling_caches') or sampling_installed(machine)
            except (OSError, ValueError, KeyError):
                pass
        self.selection = dict(descriptor, engine=str(engine), source=str(source), url=url, ready=ready,
                              repair_comfy=repair_comfy, lan_access=values.get('lan_access') is True)
        self.state = dict(self.state, selection=dict(self.selection), host=host)
        # Third-party downloads (manual models, no offline packages) are checked
        # file by file, so files the user downloaded are found on a ready engine too.
        checked = bool(values.get('download_list') or (
            values.get('model_method') == 'manual' and not values.get('offline_models')))
        if not ready or checked:
            extra = values.get('model_dirs', [])
            if not isinstance(extra, list) or any(not isinstance(p, str) for p in extra):
                raise ValueError('Model folders must be a list of directory paths')
            from .launcher_settings import extra_model_directories
            extra = extra + ([values['models']] if values.get('models') else []) + extra_model_directories()
            # A saved folder that no longer exists (for example a removed offline
            # package) has nothing to reuse; it must not stop the installation.
            extra = [p for p in dict.fromkeys(extra) if p.strip() and io_path(Path(p).expanduser()).is_dir()]
            self.setup.inspect(dict(root=str(engine), extra_libraries=extra, copy=False,
                sampling_caches=bool(values.get('sampling_caches')), prepared_format=values.get('prepared_format'),
                **({'download_list': True} if checked else {}),
                frontend=dict(root=descriptor['root'], separate=descriptor['separate'], download=fresh,
                              repair_python=host['python'] if repair_comfy else None)))
            row = self._wait_setup()
            self.state = dict(self.state, plan=row['plan'])
            # A ready receipt covers the engine, not every model file: when the
            # file-by-file check finds models to place or download, accepting the
            # plan prepares them before connecting.
            plan = row['plan']
            if ready and checked and (
                    plan.get('download_files') or (plan.get('local_models') or {}).get('matches')):
                self.selection = dict(self.selection, ready=False)
                self.state = dict(self.state, selection=dict(self.selection))
        extra_disk, disks, disk_errors = disk_review(self.state.get('plan', {}), engine, descriptor['root'],
            separate=descriptor['separate'], new_comfy=fresh and not io_path(descriptor['root']).exists())
        errors = list(self.state.get('plan', {}).get('errors', []))
        self.state = dict(self.state, status='review', extra_disk_bytes=extra_disk, disks=disks,
                          errors=list(dict.fromkeys(errors + disk_errors)))

    def _install(self, accepted):
        if not accepted or not self.selection:
            raise ValueError('Review and accept the current installation plan first')
        selected = dict(self.selection)
        check_installed_sources(self.source, selected['root'], selected['engine'])
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
        # The engine's Python imports the source its editable install names (commands, direct runs):
        # move it to this source too. Only a real switch stops our idle ComfyUI first.
        machine = json.loads((io_path(selected['engine']) / 'machine.json').read_text(encoding='utf-8'))
        if engine_binding.pending(machine, selected['source']):
            # Whoever started it (another launcher, the freevideo command), a ComfyUI of this installation
            # that is generating keeps its packages: the switch waits for the job.
            if queue_busy(loopback_url(self.server_address or self.selection['url'])):
                raise RuntimeError('ComfyUI is running a job, so the engine update waits. Try again after it finishes; '
                                   'the job is left running.')
            self._stop_server_for_setup(selected['url'])
        engine_binding.rebind(selected['engine'], machine, selected['source'])
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
            raise ValueError('This port and the next ones are in use or reserved by the system. Set a different local ComfyUI address.')
        if self.port_move is None:
            self.port_move = urlsplit(selected['url']).port or 80
        selected = self.selection = dict(selected, url=url)
        self.state = dict(self.state, selection=dict(selected))
        return loopback_url(url), selected

    def _stop_server_for_setup(self, url):
        """Setup or source rebinding replaces packages that our ComfyUI's resident worker
        keeps loaded after a video (Windows refuses to replace SageAttention's
        _fused.pyd), and a model switch must not leave the old model in that
        worker. Stop our own idle server; connecting after setup starts it
        again. A running job is never stopped."""
        if not self.owns_server():
            return
        if queue_busy(loopback_url(self.server_address or url)):
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

    def _configure_access(self, values, *, connect=False):
        if not self.selection or not self.selection.get('ready'):
            raise ValueError('Choose a completed installation first')
        enabled = values.get('lan_access') is True
        url = local_url(values.get('url') or self.selection['url'])
        if enabled and not loopback_host(urlsplit(url).hostname):
            url = loopback_url(url)
        changed = url != self.selection['url'] or (self.owns_server() and self.server_address != url)
        self.selection = dict(self.selection, url=url, lan_access=False)
        self.state = dict(self.state, selection=dict(self.selection))
        if changed:
            self.stop_access_proxy()
        if changed or connect:
            self._connect_comfy()
        if self.state.get('status') not in ('open', 'running'):
            return
        if enabled:
            self._start_access_proxy()
        else:
            self.stop_access_proxy()
        self.state = dict(self.state, status='open', selection=dict(self.selection))

    @plain_errors
    def _connect(self):
        self._configure_access(dict(lan_access=(self.selection or {}).get('lan_access')), connect=True)

    def _start_access_proxy(self):
        from .comfy_service import TOKEN, check_proxy, folder, start_proxy, token
        self.selection = selected = dict(self.selection, lan_access=False)
        self.state = dict(self.state, selection=dict(selected))
        if not self.owns_server():
            info = server_info(selected['url'])
            hosts = (info.get('listen') or '').split(',')
            if not hosts or not all(loopback_host(host) for host in hosts):
                raise ValueError('The ComfyUI already running is open to the network directly. After its jobs finish, restart it on 127.0.0.1, then turn on LAN access.')
        configuration = tuple(selected[k] for k in ('root', 'engine', 'source', 'python', 'url'))
        if (self.access_configuration != configuration or
                self.access_proxy is not None and self.access_proxy.poll() is not None):
            self.stop_access_proxy()
        if self.access_proxy is None:
            port = selected.get('lan_port')
            if type(port) is not int or not 1 <= port <= 65535:
                port = (urlsplit(selected['url']).port or 80) + 1
            candidate = next_free_address(address('0.0.0.0', port - 1)) if port <= 65535 else None
            if candidate is None:
                raise ValueError('The ports for LAN access are in use or reserved by the system. Choose another ComfyUI port.')
            port = urlsplit(candidate).port
            self.selection = selected = dict(selected, lan_port=port)
            self.state = dict(self.state, selection=dict(selected))
            secret = token(selected['engine'])
            from .windows_ux import external_python
            with self._server_lock, external_python():
                if self._closed or self.cancelled.is_set():
                    raise RuntimeError('LAN access startup cancelled')
                self.access_proxy = start_proxy(selected, listen='0.0.0.0', port=port,
                    upstream=loopback_url(selected['url']), token_file=folder(selected['engine']) / TOKEN, tls=None,
                    hop_secret=self.hop_secret if self.owns_server() else None)
            try:
                check_proxy(self.access_proxy, scheme='http', port=port, secret=secret)
                if self._closed or self.cancelled.is_set():
                    raise RuntimeError('LAN access startup cancelled')
            except Exception:
                self._access_stopped()
                raise
            self.access_configuration = configuration
        self.selection = dict(selected, lan_access=True)
        self.state = dict(self.state, selection=dict(self.selection))
        self.refresh_access_links()

    def _connect_comfy(self):
        retry, self._scope_retry = self._scope_retry, False
        self.state = {key: value for key, value in self.state.items()
                      if key != 'moved_from' and (retry or key != 'plugin_scoped')}
        if not self.selection or not self.selection['ready']:
            raise ValueError('Choose and inspect ComfyUI first')
        selected = self.selection
        from .desktop_runtime import matching_source
        self.state = dict(self.state, engine_update_available=not matching_source(self.source, selected['source']))
        # Covers old installations, deleted links and directly connecting to an
        # existing server, as well as the first installation.
        self.ensure_shortcut()
        shield_engine(selected['engine'])
        url = loopback_url(selected['url'])
        frontend_error = None
        managed = bool(selected.get('python') and managed_frontend(selected))
        if managed and (managed_python(Path(selected['engine']), Path(selected['root'])) is None
                        or host_repair_pending(selected['python'])):
            # Requirements changed, or a previous host repair was interrupted.
            self.sections = [('comfy', 5), ('open', 1)]
            self.stage('comfy', label='Update ComfyUI packages')
            try:
                self._prepare_frontend(selected)
            except Exception as error:
                if self.cancelled.is_set():
                    raise
                # Offline, say: the previous environment may still run this ComfyUI.
                frontend_error = str(error)
        elif (managed and node_packages_pending(Path(selected['engine']), Path(selected['root']))
              # Never beside a running job: adding the package stops our idle server first.
              and not matches_server(server_info(url), selected['root'], selected['engine'], selected['source'])):
            # An update gave our nodes a package. Without it everything but what needs it still works,
            # so a failure (offline, say) is not an error here and is tried again later.
            self.sections = [('comfy', 5), ('open', 1)]
            self.stage('comfy', label='Update ComfyUI packages')
            try:
                self._prepare_frontend(selected)
            except Exception:
                if self.cancelled.is_set():
                    raise
                node_packages_failed(Path(selected['engine']))
        self.stage('open', label='Open ComfyUI')
        info = server_info(url)
        matched = matches_server(info, selected['root'], selected['engine'], selected['source'])
        if matched and not self.owns_server() and not matches_listener(info, selected['url']):
            self.state = dict(self.state, status='restart-required', url=url,
                error='The ComfyUI already running does not use this address. After its jobs finish, restart it with this address and connect again.')
            return
        if (self.owns_server() and self.server_address is not None
                and self.server_address != selected['url']):
            if queue_busy(loopback_url(self.server_address or self.selection['url'])):
                raise RuntimeError('A generation is running. Change the address after it finishes.')
            self.stop_owned_server()
            info = dict(status='offline')
            matched = False
        if matched:
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
            if queue_busy(loopback_url(self.server_address or self.selection['url'])):
                self.state = dict(self.state, status='restart-required', url=url,
                    error='ComfyUI is still running a job. Click Connect after it finishes to restart with the update; the job is left running.')
                return
            # Our own idle server still runs the previous source: restart it.
            self.stage('open', label='Restart ComfyUI')
            self.stop_owned_server()
        parsed = urlsplit(selected['url'])
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
                    parsed = urlsplit(selected['url'])
                    continue
                if error.errno == errno.EADDRNOTAVAIL:
                    raise ValueError('This address does not belong to this computer. Enter one of its IP addresses or 0.0.0.0.') from error
                raise ValueError('This port is in use by another application. Set a different local ComfyUI address.') from error
        if not selected.get('python') or not io_path(selected['python']).is_file():
            raise ValueError('ComfyUI Python is missing. Inspect again to prepare a separate environment.')
        directory = io_path(selected['engine']) / 'launcher' / 'comfy-runs' / (time.strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(4))
        directory.mkdir(parents=True)
        self.server_log = Path(plain(directory / 'comfy.log'))
        save(Path(selected['engine']) / 'launcher/comfy-console.json',
             dict(log=str(self.server_log.relative_to(Path(selected['engine']) / 'launcher'))))
        env = (self.child_environment() if self.child_environment is not None else
               isolated_environment(selected['engine'], selected['source']))
        env.pop('PYTHONPATH', None)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env.update(PYTHONUNBUFFERED='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        from .proxy import bypass_local
        if not loopback_host(parsed.hostname):
            env = bypass_local(env, parsed.hostname)
        env['FREEVIDEO_COMFY_LOG'] = str(self.server_log)
        from .proxy_identity import ENV as HOP
        self.hop_secret = env.get(HOP) or self.hop_secret
        env[HOP] = self.hop_secret
        from .launcher_bridge import ENV as BRIDGE
        env.pop(BRIDGE, None)
        if self.update_bridge:
            env[BRIDGE] = str(self.update_bridge)
        command = [selected['python'], '-u', '-B', str(Path(selected['root']) / 'main.py'), '--listen', ','.join(listen_hosts(parsed.hostname)),
                   '--port', str(parsed.port or 80), '--disable-auto-launch']
        managed = managed_frontend(selected)
        # Only the ComfyUI this launcher downloaded loads FreeVideo alone. The user's own ComfyUI keeps
        # every plugin, unless they stopped FreeVideo from loading (retry below, or remembered).
        ours = launcher_downloaded(selected['root'])
        scoped = ours or retry or remembered_scope(selected['engine'], selected['root'])
        if scoped and not ours:
            self.state = dict(self.state, plugin_scoped=True)
        if scoped:
            # Scope both prestartup scripts and imports; a plugin can otherwise run pip,
            # replace Torch, or call sys.exit.
            command += ['--disable-all-custom-nodes', '--whitelist-custom-nodes', node_target(selected['root']).name]
        if managed and not scoped:
            # The user's plugins run in our environment: they may not install into it or replace Torch.
            env.update(PIP_NO_INDEX='1', UV_OFFLINE='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
        context = dict(environment='freevideo-managed' if managed else 'existing-comfyui',
                       custom_nodes='FreeVideo' if scoped else 'all')
        if selected.get('portable') and not selected['separate']:
            command.append('--windows-standalone-build')
        view = launch_view.for_engine(selected['engine'])
        started = command
        if view.used:
            # ComfyUI otherwise finds its folders from its own resolved file,
            # which leads back to the long path.
            command += ['--base-directory', selected['root']]
            from . import runtime_alias
            # Its main imports a library that loads by a resolved path before any FreeVideo node.
            started = (execution_paths.engine_first(command, selected['source'])
                       if runtime_alias.system_long_paths() else command)
        env = launch_view.environment_view(env, view)
        from .windows_ux import external_python
        with self._server_lock, external_python(), io_path(self.server_log).open('wb') as log:
            if self._closed or self.cancelled.is_set():
                raise RuntimeError('ComfyUI startup cancelled')
            if scoped:
                log.write(b'[FreeVideo] Separate environment: loading FreeVideo custom nodes only. '
                          b'Use your original ComfyUI launcher for other plugins.\n' if ours else
                          b'[FreeVideo] Your other plugins stopped FreeVideo from loading: '
                          b'loading FreeVideo custom nodes only this time.\n')
                log.flush()
            # Keep durable embedded output, but own the whole tree: Windows
            # Job / Linux supervisor also reclaim independently grouped model
            # workers when the launcher dies without running Python cleanup.
            server = processes.popen(started, cwd=selected['root'], env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True, supervise=True)
            self.server = server
            self.server_url = url
            self.server_address = selected['url']
        save(directory / 'launch.json', dict(command=command, pid=server.pid, root=selected['root'], url=url, **context))
        self.state = dict(self.state, task=dict(progress=dict(label='Starting ComfyUI', detail='Loading nodes and the web interface'), log=plain(directory)))
        waiting_since = time.monotonic()
        log_offset = 0
        plugin_name = node_target(selected['root']).name

        def fall_back():
            # Once: the user's other plugins stopped FreeVideo; start again with FreeVideo's nodes only.
            with self._server_lock:
                if self.server is server:
                    self.stop_owned_server(grace=3)
            self._scope_retry = True
            result = self._connect_comfy()
            # Only a start that worked with FreeVideo alone says the plugins were the cause.
            if self.state.get('status') == 'open':
                remember_scope(selected['engine'], selected['root'])
            return result
        extended = False
        shown = 0.
        # Start-up has no countable steps. The bar creeps through the last section and stays below the
        # displayed 97%, so a long start never reads as nearly done and then waits there.
        ceiling = self.open_ceiling()
        while True:
            now = time.monotonic()
            fraction = round(ceiling * (1 - 1 / (1 + (now - waiting_since) / 30)), 2)
            if self.section == 'open' and fraction >= shown + .02:
                shown = fraction
                self.stage('open', done=fraction, label=(self.state.get('overall') or {}).get('label', 'Open ComfyUI'))
            if self.cancelled.is_set():
                with self._server_lock:
                    if self.server is server:
                        self.stop_owned_server(grace=3)
                raise RuntimeError('ComfyUI startup cancelled')
            if not scoped:
                failed, log_offset = freevideo_import_failed(self.server_log, plugin_name, log_offset)
                if failed:
                    return fall_back()
            if server.poll() is not None:
                if not scoped:
                    return fall_back()
                from .failure_details import startup_failure
                message = startup_failure('ComfyUI could not start.', directory / 'comfy.log',
                                          exit_code=server.returncode, context=context)
                if frontend_error:
                    message += '\n\nComfyUI packages could not be updated first: ' + frontend_error
                if managed:
                    # Only on this failure path: it reads every package's RECORD.
                    from .failure_details import ENVIRONMENT_FILES
                    from .runtime_libraries import incomplete
                    changed = incomplete(selected['python'])
                    if changed:
                        message += '\n\n' + ENVIRONMENT_FILES + ', '.join(changed)
                raise RuntimeError(message)
            info = server_info(url)
            if matches_server(info, selected['root'], selected['engine'], selected['source']):
                self.stage('open', done=1, label='Ready')
                self.state = dict(self.state, status='open', url=url + '/?freevideo=launch')
                if self.port_move is not None:
                    self.state = dict(self.state, moved_from=self.port_move)
                    self.port_move = None
                return
            if now - waiting_since >= START_WAIT_SECONDS:
                if now - waiting_since >= START_WAIT_LIMIT_SECONDS:
                    if not scoped:
                        return fall_back()
                    break
                if not extended:
                    extended = True
                    self.state = dict(self.state, task=dict(progress=dict(
                        label='Starting ComfyUI', detail='Still loading. This may take a few more minutes.'), log=plain(directory)))
            self.cancelled.wait(.5)
        from .failure_details import startup_failure
        raise RuntimeError(startup_failure('ComfyUI is still starting or could not load FreeVideo; retry Connect.', directory / 'comfy.log'))
