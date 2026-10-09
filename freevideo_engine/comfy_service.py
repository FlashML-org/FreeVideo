"""FreeVideo in ComfyUI from a Linux terminal: install once, then open, serve and stop.

The graphical launcher's control plane does the work: the same ComfyUI
application and environment, the same node installation and the same startup
checks. ComfyUI keeps running in a background service after the command
returns, and `freevideo stop` ends it. ComfyUI itself listens on this computer
only. Other computers reach it through an SSH tunnel, or through the
token-protected proxy that `freevideo server --listen ADDRESS` puts in front of
it; nothing is reachable from outside when that proxy is not running.
"""
import argparse
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

from .monitoring import save

DEFAULT_PORT = 8188
RECORD = 'comfyui-cli.json'
STATE = 'service.json'
TOKEN = 'access-token'
UNIT = 'freevideo.service'
WRAPPER_MARK = '# FreeVideo command, written by FreeVideo setup.'
DESKTOP_ID = 'freevideo.desktop'
LOOPBACK = ('127.0.0.1', 'localhost', '::1')
COMMANDS = ('open', 'stop', 'status', 'restart', 'server')


def say(*args):
    print(*args, flush=True)


def source_root():
    return Path(__file__).resolve().parents[1]


def folder(root):
    return Path(root) / 'launcher'


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def source_stamp(source):
    """What the running node loaded, to restart it after `git pull`."""
    from .desktop_runtime import source_files
    source = Path(source)
    digest = hashlib.sha256()
    for path in source_files(source):
        digest.update(path.relative_to(source).as_posix().encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:16]


def ready_machine(root):
    machine = read_json(Path(root) / 'machine.json')
    return machine if machine and machine.get('ready') else None


def installed(root):
    """The ComfyUI this command installed for the engine at root."""
    record = read_json(folder(root) / RECORD)
    keys = ('root', 'engine', 'source', 'python', 'url')
    if not record or not all(isinstance(record.get(k), str) and record[k] for k in keys):
        return None
    return dict(record, separate=True)


def port_of(url):
    from urllib.parse import urlsplit
    return urlsplit(url).port or 80


def loopback(address):
    if address in LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def install(root, source, machine, port, out=say):
    """The pinned ComfyUI in <root>/ComfyUI with its own environment, and our node in it."""
    from .comfy_launcher_runtime import deploy, managed_python
    from .comfy_source import new_layout
    layout = new_layout(str(root))
    comfy = Path(layout['root'])
    python = managed_python(Path(root), comfy) if (comfy / 'main.py').is_file() else None
    if python is None:
        out('Installing ComfyUI for FreeVideo. This happens once; the next start takes seconds.')
        # The engine's Python reads the CUDA packages the ComfyUI environment must match.
        code = ("import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
                "runpy.run_module('freevideo_engine.comfy_host',run_name='__main__')")
        command = [machine['python'], '-B', '-c', code, str(source), '--root', str(root), '--comfy', str(comfy)]
        if not (comfy / 'main.py').is_file():
            command.append('--download-comfy')
        env = dict(os.environ, FREEVIDEO_HOME=str(root), PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        for name in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'FREEVIDEO_UI_EVENTS'):
            env.pop(name, None)
        if subprocess.run(command, cwd=str(root), env=env, stdin=subprocess.DEVNULL).returncode:
            raise RuntimeError('ComfyUI could not be installed. Run the same command again to continue; '
                               'downloaded files are kept.')
        python = managed_python(Path(root), comfy)
        if python is None:
            raise RuntimeError('ComfyUI was installed, but its environment is incomplete. Run the same command again.')
    deploy(comfy, source, root)
    record = dict(root=str(comfy), engine=str(Path(root)), source=str(source), python=python,
                  url='http://127.0.0.1:%d' % port)
    save(folder(root) / RECORD, record)
    return dict(record, separate=True)


# --- the background service -------------------------------------------------

def lock_path(root):
    return folder(root) / 'service.lock'


def acquire(path, *, wait=0.):
    """Hold the service lock for this process's life. A probe from `status` may hold it for a moment."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, 'a+b')
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return stream
        except BlockingIOError:
            if time.monotonic() >= deadline:
                stream.close()
                raise
            time.sleep(.1)


def running(root):
    """The service's state while its process holds the service lock, else None."""
    import fcntl
    path = lock_path(root)
    try:
        stream = open(path, 'rb')
    except OSError:
        return None
    with stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return None
        except BlockingIOError:
            pass
    state = read_json(folder(root) / STATE) or {}
    return state if state.get('status') in ('starting', 'open') else dict(state, status='starting')


def service_pids(root):
    from .locking import lock_holders
    pids = []
    for pid in lock_holders([lock_path(root)]):
        try:
            if b'freevideo_engine.comfy_service' in Path('/proc/%d/cmdline' % pid).read_bytes():
                pids.append(pid)
        except OSError:
            pass
    return pids


def first_free_port(start=DEFAULT_PORT, count=12):
    """The first installation takes 8188, or the next free port when another ComfyUI already has it."""
    for port in range(start, start + count):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # As ComfyUI binds.
            try:
                probe.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    return start


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def token(root, renew=False):
    """The access token, readable by this account only, kept across restarts."""
    path = folder(root) / TOKEN
    if not renew:
        try:
            value = path.read_text(encoding='utf-8').strip()
            if len(value) >= 32:
                return value
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(32)
    temporary = path.with_name(TOKEN + '.' + secrets.token_hex(4))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(value + '\n')
    os.replace(temporary, path)
    return value


def request(url, *, headers=None, timeout=3):
    """Status of a GET without following redirects; loopback requests skip any proxy."""
    import ssl
    import urllib.error
    from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

    class Stay(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    context = ssl.create_default_context()
    context.check_hostname, context.verify_mode = False, ssl.CERT_NONE  # Our own certificate, possibly self-signed.
    opener = build_opener(ProxyHandler({}), Stay(), HTTPSHandler(context=context))
    try:
        with opener.open(Request(url, headers=headers or {}), timeout=timeout) as reply:
            return reply.status
    except urllib.error.HTTPError as error:
        return error.code


def start_proxy(record, *, listen, port, upstream, token_file, tls):
    """Run the access proxy with ComfyUI's Python, which brings aiohttp."""
    from . import processes
    code = ("import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
            "runpy.run_module('freevideo_engine.access_proxy',run_name='__main__')")
    command = [record['python'], '-B', '-c', code, record['source'], '--listen', listen, '--port', str(port),
               '--upstream', upstream, '--token-file', str(token_file)]
    if tls:
        command += ['--tls-cert', str(tls[0]), '--tls-key', str(tls[1])]
    env = dict(os.environ, PYTHONUTF8='1', PYTHONNOUSERSITE='1')
    for name in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
        env.pop(name, None)
    log = folder(record['engine']) / 'access-proxy.log'
    with log.open('ab') as output:
        return processes.popen(command, cwd=record['engine'], env=env, stdin=subprocess.DEVNULL,
                               stdout=output, stderr=subprocess.STDOUT, start_new_session=True, supervise=True)


def check_proxy(proxy, *, scheme, port, secret, timeout=30):
    """Refuse to serve unless requests without the token are turned away."""
    base = '%s://127.0.0.1:%d' % (scheme, port)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proxy.poll() is not None:
            raise RuntimeError('The access proxy stopped while starting; see %s.' % 'launcher/access-proxy.log')
        try:
            refused = request(base + '/system_stats')
            allowed = request(base + '/system_stats', headers={'Authorization': 'Bearer ' + secret})
        except OSError:
            time.sleep(.3)
            continue
        if refused == 401 and allowed == 200:
            return
        raise RuntimeError('The access proxy did not protect ComfyUI (status %s without the token, %s with it).' % (refused, allowed))
    raise RuntimeError('The access proxy did not start within %d s.' % timeout)


def serve(root, *, port=DEFAULT_PORT, listen='127.0.0.1', tls=None):
    """Run ComfyUI until stopped. Exactly one service per installation."""
    from .comfy_launcher_runtime import Controller
    root = Path(root).resolve()
    state_file = folder(root) / STATE
    stopping = threading.Event()
    try:
        lease = acquire(lock_path(root), wait=3)
    except BlockingIOError:
        print('FreeVideo is already running for %s.' % root, file=sys.stderr)
        return 1
    with lease:
        state = dict(status='starting', pid=os.getpid(), port=port, listen=listen, started=time.time(),
                     tls=[str(p) for p in tls] if tls else None)
        save(state_file, state)
        controller = proxy = None
        previous = {}
        try:
            record = installed(root)
            if record is None:
                raise RuntimeError('ComfyUI is not installed for this installation yet. Run freevideo once to install it.')
            proxied = not loopback(listen)
            internal = free_port() if proxied else port
            controller = Controller(Path(record['source']))
            for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                previous[name] = signal.signal(name, lambda *_: (stopping.set(), controller.cancelled.set()))
            if not controller.restore({'installation': dict(record, url='http://127.0.0.1:%d' % internal)}):
                raise RuntimeError('This installation is incomplete. Run ./setup.sh again; downloaded files are reused.')
            stamp = source_stamp(record['source'])
            try:
                controller._connect()
            except ValueError as error:
                if 'port is in use' not in str(error):
                    raise
                raise RuntimeError('Port %d is in use by another program. Start FreeVideo on another port, for '
                                   'example: freevideo open --port %d' % (internal, internal + 2)) from None
            if controller.state.get('status') == 'restart-required':
                raise RuntimeError('Another ComfyUI already uses port %d. Stop it, or start FreeVideo on another port, '
                                   'for example: freevideo open --port %d' % (internal, internal + 2))
            if controller.state.get('status') != 'open':
                raise RuntimeError(controller.state.get('error') or 'ComfyUI did not open')
            local = 'http://127.0.0.1:%d' % internal
            state.update(url=local, source_stamp=stamp, comfy_log=str(controller.server_log or ''),
                         owned=controller.owns_server())
            if not controller.owns_server():
                # Our nodes already run in a ComfyUI started elsewhere, such as the desktop launcher.
                save(state_file, dict(state, status='external'))
                return 0
            if proxied:
                secret = token(root)
                scheme = 'https' if tls else 'http'
                proxy = start_proxy(record, listen=listen, port=port, upstream=local,
                                    token_file=folder(root) / TOKEN, tls=tls)
                check_proxy(proxy, scheme=scheme, port=port, secret=secret)
                state.update(access=dict(scheme=scheme, port=port))
            save(state_file, dict(state, status='open'))
            while not stopping.wait(1):
                if controller.server is None or controller.server.poll() is not None:
                    from .failure_details import startup_failure
                    raise RuntimeError(startup_failure('ComfyUI stopped.', Path(controller.server_log),
                                                       exit_code=getattr(controller.server, 'returncode', None)))
                if proxy is not None and proxy.poll() is not None:
                    raise RuntimeError('The access proxy stopped; ComfyUI was stopped with it. See launcher/access-proxy.log.')
            save(state_file, dict(state, status='stopped', stopped=time.time()))
            return 0
        except BaseException as error:
            if stopping.is_set():
                save(state_file, dict(state, status='stopped', stopped=time.time()))
                return 0
            save(state_file, dict(state, status='failed', error=str(error), stopped=time.time()))
            print('FreeVideo service failed: %s' % error, file=sys.stderr, flush=True)
            return 1
        finally:
            if proxy is not None:
                from . import processes
                processes.stop(proxy, grace=3)
            if controller is not None:
                controller.close()
            for name, handler in previous.items():
                signal.signal(name, handler)


def spawn(root, *, port, listen='127.0.0.1', tls=None):
    """Start the service so that it outlives this command and its terminal."""
    root = Path(root).resolve()
    folder(root).mkdir(parents=True, exist_ok=True)
    python = (ready_machine(root) or {}).get('python') or sys.executable
    command = [python, '-B', '-X', 'utf8', '-m', 'freevideo_engine.comfy_service', '--root', str(root),
               'run', '--port', str(port), '--listen', listen]
    if tls:
        command += ['--tls-cert', str(tls[0]), '--tls-key', str(tls[1])]
    env = dict(os.environ, PYTHONPATH=str(source_root()), PYTHONNOUSERSITE='1')
    for name in ('FREEVIDEO_RUNTIME_LOCK_FD', 'FREEVIDEO_LOCK_PATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
        env.pop(name, None)
    with (folder(root) / 'service.log').open('ab') as output:
        output.write(('\n[%s] freevideo service starting\n' % time.strftime('%Y-%m-%d %H:%M:%S')).encode())
        output.flush()
        return subprocess.Popen(command, cwd=str(root), env=env, stdin=subprocess.DEVNULL, stdout=output,
                                stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)


def wait_until_open(root, process, *, timeout=300, out=say):
    state_file = folder(root) / STATE
    deadline = time.monotonic() + timeout
    shown = time.monotonic()
    interactive = sys.stdout.isatty()
    out('Starting ComfyUI ...')
    while time.monotonic() < deadline:
        state = read_json(state_file) or {}
        mine = state.get('pid') == process.pid
        if mine and state.get('status') in ('open', 'external'):
            return state
        if mine and state.get('status') == 'failed':
            raise RuntimeError(state.get('error') or 'ComfyUI could not start.')
        if process.poll() is not None:
            raise RuntimeError(state.get('error') if mine and state.get('error') else
                               'The FreeVideo service stopped while starting. See %s.' % (folder(root) / 'service.log'))
        if interactive and time.monotonic() - shown >= 15:
            shown = time.monotonic()
            out('  still starting (%d s) ...' % (timeout - (deadline - time.monotonic())))
        time.sleep(.3)
    raise RuntimeError('ComfyUI is still starting after %d s. See %s.' % (timeout, folder(root) / 'service.log'))


def respawn(root, state, *, port=None):
    """Start again as the service last ran: same address, port and certificate."""
    tls = tuple(Path(p) for p in state['tls']) if isinstance(state.get('tls'), list) else None
    listen = state.get('listen') or '127.0.0.1'
    return spawn(root, port=port or state.get('port') or DEFAULT_PORT, listen=listen, tls=tls)


def stop(root, *, force=False, out=say):
    from .comfy_launcher_runtime import queue_busy
    state = running(root)
    if state is None:
        out('FreeVideo is not running.')
        return 0
    if not force and state.get('url') and queue_busy(state['url']):
        out('ComfyUI is running a job. Run freevideo stop again after it finishes, or freevideo stop --force to stop it now.')
        return 1
    pids = service_pids(root)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and running(root) is not None:
        time.sleep(.2)
    if running(root) is not None:
        for pid in service_pids(root):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(1)
    out('FreeVideo stopped.' if running(root) is None else 'FreeVideo is still stopping; check freevideo status.')
    return 0 if running(root) is None else 1


# --- what the person sees ------------------------------------------------------

def desktop_session():
    return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


def browser_allowed():
    """A local desktop gets a browser window; an SSH session gets the address instead."""
    if os.environ.get('FREEVIDEO_NO_BROWSER') == '1' or not desktop_session():
        return False
    return not os.environ.get('SSH_CONNECTION')


def open_browser(url):
    import webbrowser
    try:
        return webbrowser.open(url, new=2)
    except (OSError, webbrowser.Error):
        return False


def ssh_target():
    """How this computer was reached over SSH, for a tunnel command the person can paste."""
    user = getpass.getuser()
    parts = os.environ.get('SSH_CONNECTION', '').split()
    if len(parts) == 4:
        host, port = parts[2], parts[3]
        host = '[%s]' % host if ':' in host else host
        return ('-p %s ' % port if port != '22' else '') + '%s@%s' % (user, host)
    return '%s@%s' % (user, socket.getfqdn())


def addresses(listen):
    """Addresses other computers can use for a proxy that listens on all interfaces."""
    if listen not in ('0.0.0.0', '::', ''):
        return [listen]
    found = []
    parts = os.environ.get('SSH_CONNECTION', '').split()
    if len(parts) == 4:
        found.append(parts[2])
    for family, target in ((socket.AF_INET, '192.0.2.1'), (socket.AF_INET6, '2001:db8::1')):
        try:
            # connect() on UDP only selects a route; no packet is sent.
            with socket.socket(family, socket.SOCK_DGRAM) as probe:
                probe.connect((target, 9))
                found.append(probe.getsockname()[0])
        except OSError:
            pass
    found = [a for a in dict.fromkeys(found) if not loopback(a)]
    return found or [socket.getfqdn()]


def host_url(scheme, address, port):
    return '%s://%s:%d' % (scheme, '[%s]' % address if ':' in address else address, port)


def describe(root, state, *, browser_opened=False, out=say):
    url = state['url'] + '/?freevideo=launch'
    access = state.get('access')
    if access:
        secret = token(root)
        out('FreeVideo is running. Open it from another computer with:')
        for address in addresses(state.get('listen', '')):
            out('  %s/?freevideo=launch&token=%s' % (host_url(access['scheme'], address, access['port']), secret))
        out('Anyone with this link can use FreeVideo on this computer; freevideo server --new-token replaces it.')
        if access['scheme'] == 'http':
            out('The connection is not encrypted. Use it on a network you trust, add --tls-cert/--tls-key, or use an SSH tunnel.')
        out('On this computer: %s' % url)
    elif browser_opened:
        out('FreeVideo is open in your browser: %s' % url)
    else:
        out('FreeVideo is running: %s' % url)
        if os.environ.get('SSH_CONNECTION') or not desktop_session():
            port = port_of(state['url'])
            out('Only this computer can connect. From your own computer, run:')
            out('  ssh -N -L %d:127.0.0.1:%d %s' % (port, port, ssh_target()))
            out('then open http://127.0.0.1:%d/?freevideo=launch in your browser.' % port)
            out('To connect from your local network instead: freevideo server --listen 0.0.0.0')
    if unit_path().exists():
        out('It starts with your user session; to stop that: freevideo server --disable')
    else:
        out('Stop it with: freevideo stop')


# --- the freevideo command and the menu entry -----------------------------------

def command_line(root, source):
    return [str(Path(source) / 'freevideo'), '--root', str(Path(root).resolve())]


def write_wrapper(root, source, *, home=None):
    """~/.local/bin/freevideo, so typing freevideo opens this installation from any folder."""
    home = Path(home or Path.home())
    path = home / '.local' / 'bin' / 'freevideo'
    content = '#!/bin/sh\n%s\nexec %s "$@"\n' % (WRAPPER_MARK, ' '.join(shlex.quote(p) for p in command_line(root, source)))
    try:
        if path.is_symlink() or (path.exists() and WRAPPER_MARK not in path.read_text(encoding='utf-8', errors='replace')):
            return dict(status='kept', path=str(path))  # Someone else's freevideo command.
        if path.is_file() and path.read_text(encoding='utf-8') == content:
            return dict(status='present', path=str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name('.freevideo.' + secrets.token_hex(4))
        temporary.write_text(content, encoding='utf-8')
        temporary.chmod(0o755)
        os.replace(temporary, path)
    except OSError as error:
        return dict(status='failed', path=str(path), error=str(error))
    on_path = any(Path(p).expanduser().resolve() == path.parent.resolve()
                  for p in os.environ.get('PATH', '').split(os.pathsep) if p)
    return dict(status='created', path=str(path), on_path=on_path)


def write_desktop_entry(root, source, *, home=None):
    """An application-menu entry on Linux desktops; servers have no menu to add it to."""
    data = Path(os.environ.get('XDG_DATA_HOME') or Path(home or Path.home()) / '.local' / 'share')
    path = data / 'applications' / DESKTOP_ID
    icon = Path(source) / 'freevideo_engine' / 'assets' / 'icon.png'
    from .desktop_shortcut import desktop_quote
    entry = ' '.join(desktop_quote(p) for p in command_line(root, source) + ['open'])
    content = ('[Desktop Entry]\nType=Application\nName=FreeVideo\nComment=Create videos with FreeVideo in ComfyUI\n'
               'Comment[zh_CN]=在 ComfyUI 中用 FreeVideo 生成视频\nExec=%s\nIcon=%s\nTerminal=false\n'
               'Categories=AudioVideo;Video;Graphics;\nStartupNotify=false\n' % (entry, icon))
    try:
        if path.is_file() and path.read_text(encoding='utf-8') == content:
            return dict(status='present', path=str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    except OSError as error:
        return dict(status='failed', path=str(path), error=str(error))
    return dict(status='created', path=str(path))


def shortcuts(root, source, out=say):
    wrapper = write_wrapper(root, source)
    if wrapper['status'] == 'created':
        out('Added the freevideo command: %s' % wrapper['path'])
        if not wrapper.get('on_path'):
            out('  It works in new terminals. In this one, run: export PATH="$HOME/.local/bin:$PATH"')
    elif wrapper['status'] == 'kept':
        out('Kept the existing %s; run %s to open FreeVideo.' % (wrapper['path'], ' '.join(command_line(root, source))))
    if desktop_session() and os.environ.get('XDG_CURRENT_DESKTOP'):
        write_desktop_entry(root, source)


# --- commands ----------------------------------------------------------------------

def ensure_installed(root, port, out=say):
    root = Path(root).expanduser().resolve()
    source = source_root()
    machine = ready_machine(root)
    if machine is None:
        # ./setup.sh also installs the system tools it needs, then continues here.
        raise RuntimeError('FreeVideo is not set up in %s yet. Run ./setup.sh: it installs FreeVideo and ComfyUI, '
                           'then opens FreeVideo.' % root)
    record = installed(root)
    if record is None or Path(record['source']).resolve() != source or not Path(record['python']).is_file():
        record = install(root, source, machine, port or (port_of(record['url']) if record else first_free_port()), out=out)
    else:
        from .comfy_launcher_runtime import deploy
        deploy(record['root'], source, root)
        if port and port_of(record['url']) != port:
            record = dict(record, url='http://127.0.0.1:%d' % port)
            save(folder(root) / RECORD, {k: record[k] for k in ('root', 'engine', 'source', 'python', 'url')})
    return root, source, record


def open_command(root, *, port=None, browser=True, restart=False, out=say):
    root, source, record = ensure_installed(root, port, out=out)
    shortcuts(root, source, out=out)
    state = running(root)
    if state and state.get('status') == 'starting':
        state = wait_for_running(root, out=out)
    moved = bool(port and state and state.get('port') != port and not state.get('access'))
    if state and (restart or moved or state.get('source_stamp') != source_stamp(source)):
        from .comfy_launcher_runtime import queue_busy
        if queue_busy(state['url']) and not restart:
            out('FreeVideo was updated; it loads the update when ComfyUI restarts after the current job '
                '(freevideo restart).')
        else:
            out('Restarting ComfyUI ...' if restart or moved else 'Restarting ComfyUI to load the updated FreeVideo ...')
            previous = state
            if stop(root, force=restart, out=lambda *_: None):
                raise RuntimeError('ComfyUI did not stop; check freevideo status.')
            state = wait_until_open(root, respawn(root, previous, port=port if moved else None), out=out)
    if state is None:
        state = wait_until_open(root, spawn(root, port=port_of(record['url'])), out=out)
    opened = False
    if browser and browser_allowed():
        opened = open_browser(state['url'] + '/?freevideo=launch')
    describe(root, state, browser_opened=opened, out=out)
    return 0


def wait_for_running(root, timeout=300, absent=0., out=say):
    """Wait while the service starts; `absent` allows for one that a service manager starts shortly."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        state = running(root)
        if state is not None and state.get('status') == 'open':
            return state
        if state is None and time.monotonic() - started >= absent:
            previous = read_json(folder(root) / STATE) or {}
            if previous.get('status') == 'failed':
                raise RuntimeError(previous.get('error') or 'ComfyUI could not start.')
            return None
        time.sleep(.3)
    raise RuntimeError('ComfyUI is still starting. Check freevideo status.')


def status_command(root, out=say):
    root = Path(root).expanduser().resolve()
    record = installed(root)
    if ready_machine(root) is None:
        out('FreeVideo is not set up in %s. Run ./setup.sh.' % root)
        return 1
    if record is None:
        out('FreeVideo is set up; ComfyUI is not installed yet. Run freevideo to install and open it.')
        return 0
    state = running(root)
    if state is None:
        previous = read_json(folder(root) / STATE) or {}
        out('FreeVideo is not running. Start it with: freevideo')
        reason = (previous.get('error') or '').strip().splitlines()
        if previous.get('status') == 'failed' and reason:
            out('The last start failed: %s' % reason[0][:300])
        out('ComfyUI: %s' % record['root'])
        return 0
    describe(root, state, out=out)
    out('Service: process %s, started %s; logs in %s' % (
        state.get('pid'), time.strftime('%Y-%m-%d %H:%M', time.localtime(state.get('started', 0))), folder(root)))
    return 0


def tls_pair(cert, key):
    if not cert and not key:
        return None
    if not (cert and key):
        raise ValueError('--tls-cert and --tls-key go together')
    cert, key = Path(cert).expanduser().resolve(), Path(key).expanduser().resolve()
    for path in (cert, key):
        if not path.is_file():
            raise ValueError('File not found: %s' % path)
    return cert, key


def unit_text(root, source, listen, port, tls):
    command = command_line(root, source) + ['server', '--foreground', '--listen', listen, '--port', str(port)]
    if tls:
        command += ['--tls-cert', str(tls[0]), '--tls-key', str(tls[1])]
    return ('[Unit]\nDescription=FreeVideo (ComfyUI with FreeVideo)\nAfter=network-online.target\n\n'
            '[Service]\nType=simple\nExecStart=%s\nRestart=on-failure\nRestartSec=10\nTimeoutStopSec=60\n\n'
            '[Install]\nWantedBy=default.target\n') % ' '.join(shlex.quote(p) for p in command)


def unit_path():
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'systemd' / 'user' / UNIT


def systemctl(*arguments):
    return subprocess.run(['systemctl', '--user', *arguments], capture_output=True, text=True)


def enable_unit(root, source, listen, port, tls, out=say):
    if not shutil.which('systemctl') or systemctl('show-environment').returncode:
        raise RuntimeError('systemd user services are not available here. Run freevideo server --foreground '
                           'from your own service manager instead.')
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit_text(root, source, listen, port, tls), encoding='utf-8')
    if running(root) is not None:
        stop(root, out=lambda *_: None)
    for arguments in (('daemon-reload',), ('enable', '--now', UNIT)):
        result = systemctl(*arguments)
        if result.returncode:
            raise RuntimeError('systemctl --user %s failed: %s' % (' '.join(arguments), (result.stderr or result.stdout).strip()))
    out('FreeVideo now starts with your user session (%s).' % path)
    linger = subprocess.run(['loginctl', 'show-user', getpass.getuser(), '-P', 'Linger'], capture_output=True, text=True)
    if linger.stdout.strip() != 'yes':
        out('To keep it running after you log out and start it at boot, run once: sudo loginctl enable-linger %s'
            % getpass.getuser())


def disable_unit(out=say):
    path = unit_path()
    if shutil.which('systemctl'):
        systemctl('disable', '--now', UNIT)
    if path.exists():
        path.unlink()
        systemctl('daemon-reload')
    out('FreeVideo no longer starts with your user session.')


def server_command(root, *, listen='127.0.0.1', port=None, tls=None, renew=False, foreground=False,
                   enable=False, disable=False, force=False, out=say):
    if disable:
        disable_unit(out=out)
        return 0
    root, source, record = ensure_installed(root, port, out=out)
    port = port or port_of(record['url'])
    if not loopback(listen):
        token(root, renew=renew)
    if enable:
        enable_unit(root, source, listen, port, tls, out=out)
        state = wait_for_running(root, absent=20, out=out)
        if state is None:
            out('The service did not start. Check: systemctl --user status %s' % UNIT)
            return 1
        describe(root, state, out=out)
        return 0
    if foreground:
        if running(root) is not None:
            raise RuntimeError('FreeVideo is already running; stop it first (freevideo stop).')
        return serve(root, port=port, listen=listen, tls=tls)
    state = running(root)
    if state and state.get('status') == 'starting':
        state = wait_for_running(root, out=out)
    wanted = dict(listen=listen, port=port, tls=[str(p) for p in tls] if tls else None)
    if state and (any(state.get(k) != v for k, v in wanted.items()) or renew
                  or state.get('source_stamp') != source_stamp(source)):
        from .comfy_launcher_runtime import queue_busy
        if queue_busy(state['url']) and not force:
            out('ComfyUI is running a job. Run this again after it finishes, or add --force to restart now.')
            return 1
        stop(root, force=True, out=lambda *_: None)
        state = None
    if state is None:
        state = wait_until_open(root, spawn(root, port=port, listen=listen, tls=tls), out=out)
    describe(root, state, out=out)
    return 0


def cli(command, root, arguments, out=say):
    """`./freevideo open|stop|status|restart|server` on Linux."""
    if not sys.platform.startswith('linux'):
        out('This command is for Linux. On Windows and macOS, open the FreeVideo app.')
        return 1
    parser = argparse.ArgumentParser(prog='freevideo ' + command)
    if command in ('open', 'restart', 'server'):
        parser.add_argument('--port', type=int, help='Port for ComfyUI (default: the last one used, first %d)' % DEFAULT_PORT)
    if command in ('open', 'restart'):
        parser.add_argument('--no-browser', action='store_true', help='Only start ComfyUI and print its address')
    if command == 'stop':
        parser.add_argument('--force', action='store_true', help='Stop even while ComfyUI is running a job')
    if command == 'server':
        parser.add_argument('--listen', default='127.0.0.1', help='Address to accept connections on. The default '
                            'accepts this computer only (use an SSH tunnel); 0.0.0.0 accepts other computers with the access link.')
        parser.add_argument('--tls-cert', type=Path, help='Certificate for HTTPS on --listen')
        parser.add_argument('--tls-key', type=Path, help='Private key for --tls-cert')
        parser.add_argument('--new-token', action='store_true', help='Replace the access link')
        parser.add_argument('--foreground', action='store_true', help='Run in this terminal or under a service manager')
        parser.add_argument('--enable', action='store_true', help='Start with your user session (systemd)')
        parser.add_argument('--disable', action='store_true', help='Remove the systemd user service')
        parser.add_argument('--force', action='store_true', help='Restart even while ComfyUI is running a job')
    args = parser.parse_args(arguments)
    if getattr(args, 'port', None) is not None and not 1024 <= args.port <= 65535:
        parser.error('--port must be between 1024 and 65535')
    try:
        if command == 'open':
            return open_command(root, port=args.port, browser=not args.no_browser, out=out)
        if command == 'restart':
            return open_command(root, port=args.port, browser=not args.no_browser, restart=True, out=out)
        if command == 'stop':
            return stop(root, force=args.force, out=out)
        if command == 'status':
            return status_command(root, out=out)
        if args.enable and args.disable:
            parser.error('--enable and --disable cannot be combined')
        if (args.tls_cert or args.tls_key) and loopback(args.listen):
            parser.error('--tls-cert/--tls-key need --listen with an address other computers use')
        return server_command(root, listen=args.listen, port=args.port, tls=tls_pair(args.tls_cert, args.tls_key),
                              renew=args.new_token, foreground=args.foreground, enable=args.enable,
                              disable=args.disable, force=args.force, out=out)
    except (RuntimeError, ValueError, OSError) as error:
        print('FreeVideo could not %s: %s' % ('start' if command != 'stop' else 'stop', error), file=sys.stderr)
        return 1


def continues_after_setup(*, no_launch=False, approved_plan=None, environ=None, system=None):
    """Whether ./setup.sh goes on to open FreeVideo: a terminal setup on Linux does.

    The desktop launcher and the ComfyUI settings panel run setup with UI events
    and an approved plan, and continue on their own.
    """
    import platform
    environ = os.environ if environ is None else environ
    return ((system or platform.system()) == 'Linux' and not no_launch and not approved_plan
            and environ.get('FREEVIDEO_UI_EVENTS') != '1')


def after_setup(root, out=say):
    """The end of ./setup.sh: install ComfyUI, start it and open FreeVideo."""
    out('')
    try:
        return open_command(root, out=out)
    except (RuntimeError, ValueError, OSError) as error:
        print('\nThe engine is set up, but FreeVideo could not open: %s\nRun freevideo to try again.' % error, file=sys.stderr)
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run', help='Run the service in this process')
    run.add_argument('--port', type=int, default=DEFAULT_PORT)
    run.add_argument('--listen', default='127.0.0.1')
    run.add_argument('--tls-cert', type=Path)
    run.add_argument('--tls-key', type=Path)
    args = parser.parse_args(argv)
    return serve(args.root, port=args.port, listen=args.listen, tls=tls_pair(args.tls_cert, args.tls_key))


if __name__ == '__main__':
    raise SystemExit(main())
