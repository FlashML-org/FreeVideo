"""Private local IPC for a ComfyUI-owned, reusable engine worker.

The worker receives the active controller's real GPU lease by descriptor/handle
transfer. It closes that lease before going idle. The controller still measures
and can stop the physical worker, so caching does not bypass RAM guards, attempt
history, cancellation, or the Windows process tree supervisor. No public port.
"""
import atexit
from dataclasses import replace
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time

from . import processes
from .monitoring import save

ENV = 'FREEVIDEO_RESIDENT_SESSION'


class SessionRestarted(RuntimeError):
    """No new GPU work started; the old worker was confirmed stopped."""


class SessionBusy(RuntimeError):
    """A running request owns this worker; its allocations are not idle credit."""


def send(connection, value):
    connection.send_bytes(json.dumps(value, allow_nan=False).encode('utf-8'))


def receive(connection, timeout=None):
    if timeout is not None and not connection.poll(timeout):
        raise TimeoutError('Resident engine did not respond')
    return json.loads(connection.recv_bytes(2**20))


def _send_lease(connection, descriptor, destination_pid):
    if os.name != 'nt':
        from multiprocessing.reduction import send_handle
        send_handle(connection, descriptor, destination_pid)
        return
    import _winapi
    import msvcrt
    # CPython 3.9/3.12 send_handle passes DUPLICATE_SAME_ACCESS as the desired
    # access mask, not DuplicateHandle's options. For files that becomes only
    # FILE_WRITE_DATA, losing the permissions needed to validate/use the lease.
    # Duplicate explicitly with unchanged access and transfer only its integer
    # value over our authenticated JSON channel. The receiver owns the copy;
    # RemoteProcess stops it on any failed/ambiguous handoff, closing that copy.
    target = _winapi.OpenProcess(_winapi.PROCESS_DUP_HANDLE, False, destination_pid)
    try:
        handle = _winapi.DuplicateHandle(_winapi.GetCurrentProcess(),
            msvcrt.get_osfhandle(descriptor), target, 0, False, _winapi.DUPLICATE_SAME_ACCESS)
        send(connection, dict(command='lease', handle=handle))
    finally:
        _winapi.CloseHandle(target)


def _receive_lease(connection):
    if os.name != 'nt':
        from multiprocessing.reduction import recv_handle
        return recv_handle(connection)
    import _winapi
    import msvcrt
    message = receive(connection, 10.)
    handle = message.get('handle')
    if message.get('command') != 'lease' or type(handle) is not int or handle <= 0:
        raise ValueError('Invalid resident runtime lease transfer')
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        _winapi.CloseHandle(handle)
        raise


def alive(pid, created):
    import psutil
    try:
        process = psutil.Process(pid)
        return process.create_time() == created and process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, ValueError):
        return False


def connect(path):
    from multiprocessing.connection import Client
    record = json.loads(Path(path).read_text(encoding='utf-8'))
    ready = json.loads(Path(path).with_name('ready.json').read_text(encoding='utf-8'))
    if not alive(ready['pid'], ready['created']):
        raise SessionRestarted('The previous resident worker has stopped; resume in a fresh worker')
    if ready.get('idle') is False:
        raise SessionBusy('Resident engine is executing another request')
    connection = Client(record['address'], family=record['family'], authkey=bytes.fromhex(record['key']))
    hello = receive(connection, 5.)
    if (hello['pid'] != ready['pid'] or hello['created'] != ready['created'] or
            not alive(hello['pid'], hello['created'])):
        connection.close()
        raise RuntimeError('Resident engine process changed')
    return connection, hello


def status(environment=None):
    path = (os.environ if environment is None else environment).get(ENV)
    if not path:
        return None
    try:
        connection, _ = connect(path)
        try:
            send(connection, {'command': 'status'})
            return receive(connection, 5.)
        finally:
            connection.close()
    except (OSError, ValueError, EOFError, TimeoutError, SessionRestarted, SessionBusy):
        return None


def credit(hardware, environment=None, *, reuse_encoder=False):
    """Plan whole-worker budgets using only authenticated idle allocations."""
    state = status(environment)
    if not state or not hardware.gpu_uuid or state.get('gpu_uuid') != hardware.gpu_uuid or not state.get('idle'):
        return hardware, None
    gpu = state.get('reclaimable_gpu_bytes')
    ram = state.get('reclaimable_ram_bytes', 0)
    if type(gpu) is not int or gpu < 0 or type(ram) is not int or ram < 0:
        return hardware, None
    # Nominal or explicit user capacities still cap choose(). Active allocations
    # from ComfyUI or unrelated applications are never included in this credit.
    available = min(hardware.ram_total, hardware.ram_available + ram)
    owned_encoding_ram = 0
    memory = state.get('memory') or {}
    if hardware.system == 'Windows':
        # Windows global availability excludes our resident mapped weights.
        # Reuse the worker guard's effective availability, capped by physical
        # RAM and commit headroom. Never add mapped credit to min(RAM, commit)
        # directly: that would manufacture commit capacity.
        keys = ('effective_available_bytes', 'system_physical_available_bytes',
                'system_commit_available_bytes', 'reclaimable_mapped_bytes', 'guard_bytes')
        if all(type(memory.get(k)) is int and memory[k] >= 0 for k in keys):
            available = min(hardware.ram_total, memory['system_commit_available_bytes'],
                memory['effective_available_bytes'] + min(ram, memory['guard_bytes']),
                memory['system_physical_available_bytes'] + memory['reclaimable_mapped_bytes'] + min(ram, memory['guard_bytes']))
            if (reuse_encoder and memory.get('guard_metric') == 'tree private working set'
                    and any(model.get('role') == 'encoder' for model in state.get('models', []))):
                # The encoder is staying in this same worker. Its measured
                # private working set is already charged to both RAM and
                # Commit; a whole-worker budget must not charge it again as
                # newly allocated memory. Replace the pin credit, do not add
                # both. No RSS, mapped section or nonresident commit is added.
                owned_encoding_ram = memory['guard_bytes']
                available = min(hardware.ram_total,
                    memory['effective_available_bytes'] + owned_encoding_ram,
                    memory['system_commit_available_bytes'] + owned_encoding_ram,
                    memory['system_physical_available_bytes'] + memory['reclaimable_mapped_bytes'] + owned_encoding_ram)
        else:
            available = hardware.ram_available  # Unknown commit/mapped accounting earns no RAM credit.
    state = dict(state, ram_accounting=dict(raw_available_bytes=hardware.ram_available,
        credited_available_bytes=available, pinned_model_bytes=ram,
        reused_encoder_working_bytes=owned_encoding_ram,
        scope=('Windows whole-worker encoding allowance: effective physical/commit headroom plus '
               'already resident private working set, counted once; no new system capacity'
               if owned_encoding_ram else
               'Windows worker effective physical availability bounded by commit; Linux available RAM plus owned pins')))
    gpu_available = min(hardware.vram_total, hardware.vram_free + gpu)
    capacity = state.get('gpu_capacity')
    if hardware.system == 'Windows' and isinstance(capacity, dict):
        limit = capacity.get('allocator_limit_bytes')
        if type(limit) is int and 0 <= limit <= hardware.vram_total:
            gpu_available = min(gpu_available, limit)
    return replace(hardware, vram_free=gpu_available,
                   ram_available=available), state


class RemoteProcess:
    """Popen-shaped active request; pid is the measured GPU worker, not an RPC shim."""
    def __init__(self, endpoint, command, environment, descriptor, log, *, required_capabilities=(), operation='run'):
        import psutil
        if operation not in ('run', 'release'):
            raise ValueError('Invalid resident operation')
        self.connection, hello = connect(endpoint)
        self.pid, self.created = hello['pid'], hello['created']
        self.returncode = None
        self.settled = False
        self.args = command
        if not set(required_capabilities).issubset(hello.get('capabilities', [])):
            # A previous worker must not interpret a weights-only request as
            # ordinary prompt encoding. Complete a read-only exchange and keep
            # it idle, without transferring a lease or beginning CUDA work.
            try:
                send(self.connection, {'command': 'status'})
                receive(self.connection, 5.)
            finally:
                self.connection.close()
            raise RuntimeError('Resident worker does not support the requested idle preparation; restart the app')
        selected = environment.get('PYTORCH_ALLOC_CONF', environment.get('PYTORCH_CUDA_ALLOC_CONF', ''))
        if hello.get('allocator_config') is not None and hello['allocator_config'] != selected:
            self.kill()
            deadline = time.monotonic()+5.
            while alive(self.pid, self.created) and time.monotonic() < deadline:
                time.sleep(.025)
            self.connection.close()
            if alive(self.pid, self.created):
                raise RuntimeError('Could not stop the old resident allocator; refusing concurrent GPU work')
            raise SessionRestarted('Allocator settings changed; cleared idle cache and started a fresh worker')
        try:
            send(self.connection, dict(command=operation, argv=list(command), environment=dict(environment),
                log=str(Path(log).resolve()), owner_pid=os.getpid(), owner_created=psutil.Process().create_time()))
            _send_lease(self.connection, descriptor, self.pid)
            try:
                accepted = receive(self.connection, 10.)
            except (EOFError, OSError) as error:
                raise RuntimeError('Resident worker disconnected during runtime lease handoff; see ' +
                    str(Path(endpoint).with_name('session.log'))) from error
            if accepted.get('status') != 'accepted':
                raise RuntimeError('Resident worker rejected request during ' +
                    str(accepted.get('phase', 'handoff')) + ': ' + str(accepted.get('error')))
        except BaseException as error:
            # A failed handoff must not leave a worker waiting for a descriptor,
            # or executing work which the controller cannot monitor/cancel.
            try:
                self.stop_request(grace=.25)
            except BaseException as cleanup:
                error.worker_stop_confirmed = False
                error.worker_stop_error = repr(cleanup)
            self.connection.close()
            raise

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        try:
            if self.connection.poll(0):
                result = receive(self.connection)
                self.returncode = int(result['returncode'])
                self.settled = True
                self.connection.close()
            elif not alive(self.pid, self.created):
                self.returncode = -1
                self.connection.close()
        except (EOFError, OSError):
            self.returncode = -1
            self.connection.close()
        return self.returncode

    def wait(self, timeout=None):
        started = time.monotonic()
        while self.poll() is None:
            if timeout is not None and time.monotonic() - started >= timeout:
                raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(.025)
        return self.returncode

    def send_signal(self, value):
        if alive(self.pid, self.created):
            os.kill(self.pid, value)

    def kill(self):
        import psutil
        if alive(self.pid, self.created):
            process = psutil.Process(self.pid)
            for child in reversed(process.children(recursive=True)):
                try:
                    child.kill()
                except psutil.Error:
                    pass
            process.kill()

    def terminate(self):
        self.send_signal(signal.SIGTERM)

    def stop_request(self, grace=10):
        self.poll()
        if self.settled:
            return
        self.terminate()
        deadline = time.monotonic()+grace
        while alive(self.pid, self.created) and time.monotonic() < deadline:
            time.sleep(.025)
        if alive(self.pid, self.created):
            self.kill()
            deadline = time.monotonic()+5.
            while alive(self.pid, self.created) and time.monotonic() < deadline:
                time.sleep(.025)
        if alive(self.pid, self.created):
            raise subprocess.TimeoutExpired(self.args, grace+5)
        self.poll()
        self.connection.close()


def release_idle_cache(environment, descriptor):
    """Release only our authenticated idle process, under the runtime lease.

    Used once when cached models prevent admitting the next stage. Exiting the
    process releases encoder mappings, CUDA allocations and WDDM commit together.
    Conditioning already saved on disk is unaffected; no generation is repeated.
    """
    endpoint = environment[ENV]
    log = Path(endpoint).with_name('release-%d.log' % time.time_ns())
    worker = RemoteProcess(endpoint, ['release-idle-cache'], environment, descriptor, log,
        required_capabilities=('idle-cache-release',), operation='release')
    try:
        code = worker.wait(timeout=5.)
        if code:
            raise RuntimeError('Idle cache release failed with exit %s' % code)
        deadline = time.monotonic()+5.
        while alive(worker.pid, worker.created) and time.monotonic() < deadline:
            time.sleep(.025)
        if alive(worker.pid, worker.created):
            worker.kill()
            deadline = time.monotonic()+3.
            while alive(worker.pid, worker.created) and time.monotonic() < deadline:
                time.sleep(.025)
        if alive(worker.pid, worker.created):
            raise RuntimeError('Idle worker has not exited; memory release is not confirmed')
        return dict(released=True, pid=worker.pid, reason='Release idle models before replanning; saved conditioning retained')
    except BaseException:
        # The release was accepted under our lease. Do not leave a half-stopped
        # process or assume its memory is available after an IPC timeout.
        if alive(worker.pid, worker.created):
            worker.stop_request(grace=1.) if not worker.settled else worker.kill()
        raise
    finally:
        worker.connection.close()


class SessionOwner:
    """One persistent worker owned by the interactive ComfyUI process."""
    def __init__(self):
        self.process = None
        self.endpoint = None
        self.identity = None
        self.active = False
        self.prewarm = None
        atexit.register(self.close)

    def start(self, root, source, python, environment):
        import hashlib
        import psutil
        from .triton_compat import COMPILER_ENVIRONMENT_KEYS, environment as compiler_environment
        environment = compiler_environment(root, environment)
        self.stop_prewarm()
        source = Path(source).resolve()
        signature = hashlib.sha256()
        for file in sorted((source/'freevideo_engine').glob('*.py')):
            signature.update(file.name.encode())
            signature.update(file.read_bytes())
        compiler = tuple(environment.get(key) for key in COMPILER_ENVIRONMENT_KEYS)
        identity = (str(source), str(python), signature.hexdigest(), environment.get('CUDA_VISIBLE_DEVICES'), compiler)
        if self.process is not None and self.process.poll() is None and self.identity == identity:
            return str(self.endpoint)
        self.close()
        directory = Path(root)/'sessions'/('interactive-' + secrets.token_hex(8))
        directory.mkdir(parents=True, mode=0o700)
        self.endpoint = directory/'endpoint.json'
        family = 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX'
        address = (r'\\.\pipe\FreeVideo-' + secrets.token_hex(16) if os.name == 'nt'
                   else str(directory/'worker.sock'))
        # Unix-domain paths have a short OS limit; use a private short directory.
        if os.name != 'nt' and len(address.encode()) >= 100:
            import tempfile
            socket_directory = Path(tempfile.mkdtemp(prefix='freevideo-session-'))
            address = str(socket_directory/'worker.sock')
        save(self.endpoint, dict(address=address, family=family, key=secrets.token_hex(32),
            owner_pid=os.getpid(), owner_created=psutil.Process().create_time(), source_sha256=identity[2]))
        if os.name != 'nt':
            self.endpoint.chmod(0o600)
        env = dict(environment, PYTHONPATH=str(source), PYTHONUNBUFFERED='1')
        env.pop(ENV, None)
        env.pop('FREEVIDEO_RUNTIME_LOCK_FD', None)
        env.pop(processes.HANDLE_ENV, None)
        with (directory/'session.log').open('wb') as log:
            self.process = processes.popen([str(python), '-B', '-m', 'freevideo_engine.resident_worker',
                '--endpoint', str(self.endpoint)], env=env, cwd=source, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True, supervise=True)
        self.identity = identity
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Resident worker could not start; see ' + str(directory/'session.log'))
            if (directory/'ready.json').exists():
                return str(self.endpoint)
            time.sleep(.05)
        self.close()
        raise TimeoutError('Resident worker startup timed out')

    def stop_prewarm(self):
        receipt = {}
        if self.prewarm is not None:
            stopped = self.prewarm.stop()
            from .encoder_diagnostics import prewarm_receipt
            receipt = prewarm_receipt(self.prewarm.report, gpu=True)
            receipt['worker_released'] = False
            self.prewarm = None
            # An interrupted native loader can be stuck in IO. Never grant
            # memory credit or start another request until it yielded/exited.
            if self.process is not None and self.process.poll() is None:
                try:
                    ready = json.loads(self.endpoint.with_name('ready.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    ready = {}
                if not stopped or ready.get('idle') is not True:
                    processes.stop(self.process, grace=1.)
                    self.process = None
                    receipt['worker_released'] = True
        return receipt

    def warm_encoder(self, output, python, environment, *, busy=None, notify=None):
        if (self.process is None or self.process.poll() is not None or self.active or
                environment.get('FREEVIDEO_ENCODER_PREWARM', 'auto').lower() in ('0', 'off', 'false')):
            return False
        self.stop_prewarm()
        if self.process is None:
            return False
        from .idle_encoder import IdleEncoder
        self.prewarm = IdleEncoder()
        return self.prewarm.schedule(self.endpoint, output, python, environment, busy=busy, notify=notify)

    def close(self):
        self.stop_prewarm()
        if self.process is not None:
            processes.stop(self.process, grace=2)
            self.process = None


OWNER = SessionOwner()


def serve(endpoint, runner, snapshot):
    """Shared transport with a CPU-injectable runner for lifecycle tests."""
    from multiprocessing.connection import Listener
    import psutil
    record = json.loads(Path(endpoint).read_text(encoding='utf-8'))
    hello = dict(pid=os.getpid(), created=psutil.Process().create_time())
    done = threading.Event()
    def owner_watch():
        while not done.wait(.5):
            if not alive(record['owner_pid'], record['owner_created']):
                os._exit(72)  # Owner crash must not leave a hidden CUDA worker.
    threading.Thread(target=owner_watch, daemon=True).start()
    try:
        with Listener(record['address'], family=record['family'], authkey=bytes.fromhex(record['key'])) as listener:
            ready_path = Path(endpoint).with_name('ready.json')
            save(ready_path, dict(hello, idle=True))
            while True:
                with listener.accept() as connection:
                    state = snapshot()
                    send(connection, dict(hello, allocator_config=state.get('allocator_config'),
                                          capabilities=list(state.get('capabilities', []))+['idle-cache-release']))
                    request = receive(connection, 10.)
                    if request.get('command') == 'status':
                        send(connection, dict(snapshot(), idle=True))
                        continue
                    if request.get('command') not in ('run', 'release'):
                        raise ValueError('Unknown resident worker command')
                    save(ready_path, dict(hello, idle=False))
                    descriptor = None
                    accepted = False
                    phase = 'runtime lease transfer'
                    stopped = threading.Event()
                    def request_watch():
                        while not stopped.wait(.25):
                            if not alive(request['owner_pid'], request['owner_created']):
                                os._exit(73)  # Preserve the unsettled attempt/lease evidence.
                    watcher = threading.Thread(target=request_watch, daemon=True)
                    previous = dict(os.environ)
                    try:
                        descriptor = _receive_lease(connection)
                        phase = 'runtime lease validation'
                        os.environ.clear()
                        os.environ.update(request['environment'])
                        os.environ.pop(processes.HANDLE_ENV, None)
                        os.environ['FREEVIDEO_RUNTIME_LOCK_FD'] = str(descriptor)
                        from .locking import runtime_lock
                        with runtime_lock():
                            send(connection, {'status': 'accepted'})
                            accepted = True
                            watcher.start()
                            code = runner(request) if request['command'] == 'run' else 0
                    except BaseException as error:
                        if not accepted:
                            failure = dict(status='rejected', phase=phase,
                                           error=type(error).__name__ + ': ' + str(error))
                            # Send the cause before closing the pipe. No argv,
                            # environment, IPC key or model data is logged.
                            try:
                                with Path(request['log']).open('a', encoding='utf-8') as log:
                                    log.write(json.dumps(failure, ensure_ascii=False) + '\n')
                            except OSError:
                                pass
                            try:
                                send(connection, failure)
                            except (EOFError, OSError):
                                pass
                        raise
                    finally:
                        stopped.set()
                        if watcher.is_alive():
                            watcher.join(timeout=1)
                        if descriptor is not None:
                            os.close(descriptor)
                        os.environ.clear()
                        os.environ.update(previous)
                    save(ready_path, dict(hello, idle=request['command'] != 'release'))
                    send(connection, {'returncode': code})
                    if request['command'] == 'release':
                        return
    finally:
        done.set()
