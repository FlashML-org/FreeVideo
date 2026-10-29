"""Private Comfy executors owned by the single public Comfy control process."""
from __future__ import annotations

import atexit
import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import AsyncIterator, TYPE_CHECKING
from urllib.parse import quote
from urllib.request import build_opener, ProxyHandler, Request
import uuid

from . import processes
from .comfy_environment import EXECUTOR_DEVICE, POOL_CONTROL

if TYPE_CHECKING:
    from .gpu_pool import Device

READY_SECONDS = 180


@dataclass
class Worker:
    device: Device
    port: int
    process: object
    base_url: str
    log: object = None


def _request(worker, path, value=None):
    request = Request(worker.base_url + path,
                      data=None if value is None else json.dumps(value).encode('utf-8'),
                      headers={'Content-Type': 'application/json'})
    # Private execution traffic must never inherit an HTTP proxy.
    with build_opener(ProxyHandler({})).open(request, timeout=3) as reply:
        body = reply.read()
    return json.loads(body) if body else None


def _client(worker):
    return 'freevideo-executor:' + worker.device.uuid


def run_prompt(worker, prompt: dict, *, client_id: str, task_id: str, extra: dict) -> str:
    """Submit once. A lost response is an unknown acceptance, never an automatic retry.

    Native targeted WS messages use the executor's observer session. The public
    client and task identities travel in extra_data.freevideo_pool for the front.
    Other extra_data (including chain context) is preserved.
    """
    metadata = dict(extra, freevideo_pool=dict(task_id=task_id, client_id=client_id,
                                              executor=worker.device.uuid))
    response = _request(worker, '/prompt', dict(prompt=prompt, client_id=_client(worker), extra_data=metadata))
    if not isinstance(response, dict) or not isinstance(response.get('prompt_id'), str) or not response['prompt_id']:
        raise RuntimeError('Executor did not acknowledge a prompt id')
    return response['prompt_id']


def interrupt(worker, prompt_id) -> str:
    """Returns 'removed' (never started, so no history will follow), 'running' or 'missing'."""
    # The private route checks/deletes under the queue mutex, closing the native
    # /queue-delete then global-/interrupt race on older Comfy versions too.
    reply = _request(worker, '/freevideo/executor/interrupt', dict(prompt_id=prompt_id))
    state = reply.get('state') if isinstance(reply, dict) else None
    return state if state in ('removed', 'running', 'missing') else 'missing'


def worker_lookup(worker, task_id) -> str | None:
    """The executor's own prompt ID for a task whose acknowledgement was lost, else None."""
    def owner(row):
        extra = row[3] if isinstance(row, (list, tuple)) and len(row) > 3 and isinstance(row[3], dict) else {}
        return (extra.get('freevideo_pool') or {}).get('task_id')
    queue = _request(worker, '/queue')
    if not isinstance(queue, dict):
        raise RuntimeError('Invalid executor queue')
    for row in list(queue.get('queue_running') or []) + list(queue.get('queue_pending') or []):
        if owner(row) == task_id and isinstance(row[1], str):
            return row[1]
    history = _request(worker, '/history')
    if not isinstance(history, dict):
        raise RuntimeError('Invalid executor history')
    for identity, entry in history.items():
        if isinstance(entry, dict) and owner(entry.get('prompt')) == task_id:
            return identity
    return None


def worker_history(worker, prompt_id) -> dict | None:
    value = _request(worker, '/history/' + quote(str(prompt_id), safe=''))
    if not isinstance(value, dict):
        raise RuntimeError('Invalid executor history')
    return value.get(prompt_id)


async def worker_progress_stream(worker) -> AsyncIterator[dict]:
    """One observer per executor; preserve JSON and binary preview payloads.

    EOF/errors propagate to the front, which must reconcile history/queue before
    reconnecting or making this device available again.
    """
    import aiohttp
    async with aiohttp.ClientSession(trust_env=False) as session:
        async with session.ws_connect(worker.base_url + '/ws',
                                      params={'clientId': _client(worker)}, heartbeat=20) as ws:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    value = json.loads(message.data)
                    if not isinstance(value, dict):
                        raise RuntimeError('Invalid executor event')
                    yield dict(value, executor=worker.device.uuid)
                elif message.type == aiohttp.WSMsgType.BINARY:
                    yield dict(type='binary', data=message.data, executor=worker.device.uuid)
                elif message.type == aiohttp.WSMsgType.ERROR:
                    raise RuntimeError('Executor progress connection failed') from ws.exception()
    raise ConnectionError('Executor progress connection closed')


class PoolSupervisor:
    def __init__(self, devices, *, root, source, start_worker=None):
        """start_worker(command, **Popen_options) -> process; defaults to processes.popen.

        root is the engine installation, source the FreeVideo source checkout.
        The caller is the running control Comfy, whose folder_paths supplies the
        frontend, shared model/input/output paths and current Python interpreter.
        """
        self.devices = tuple(devices)
        self.root, self.source = Path(root), Path(source)
        self._start_worker = start_worker or processes.popen
        self._workers = []
        self._lock = threading.RLock()

    def workers(self) -> list[Worker]:
        with self._lock:
            return list(self._workers)

    def _launch(self, device, port):
        import folder_paths
        from .gpu_pool import device_environment
        from .comfy_environment import isolated_environment
        comfy = Path(folder_paths.__file__).resolve().parent
        directory = self.root / 'launcher' / 'executors' / uuid.uuid4().hex
        plugin = directory / 'custom_nodes' / 'FreeVideoExecutor'
        plugin.mkdir(parents=True)
        (directory / 'user').mkdir()
        # Do not import the public plugin entrypoint: it restores durable jobs,
        # chains and project state, and registers installation/cleanup services.
        (plugin / '__init__.py').write_text(
            'from freevideo_engine.comfy_pool import executor_entrypoint as comfy_entrypoint\n', encoding='utf-8')
        models = {name: '\n'.join(str(Path(p).resolve()) for p in paths)
                  for name, (paths, _) in folder_paths.folder_names_and_paths.items()
                  if name != 'custom_nodes'}
        config = directory / 'model-paths.json'
        config.write_text(json.dumps({'shared': models}), encoding='utf-8')
        env = isolated_environment(self.root, self.source)
        # Never reuse a control/another executor's owner endpoint or runtime lease.
        for name in list(env):
            if name.startswith(('FREEVIDEO_RUNTIME_LOCK_', 'FREEVIDEO_RESIDENT_')) or name == POOL_CONTROL:
                env.pop(name)
        env.update(device_environment(device))
        env.update({EXECUTOR_DEVICE: device.uuid,
                    'FREEVIDEO_BACKGROUND_COMPILE': 'off', 'FREEVIDEO_PROMPT_PRESTART': 'off',
                    'FREEVIDEO_COMFY_LOG': str(directory / 'comfy.log'), 'PYTHONUNBUFFERED': '1'})
        # Insert source explicitly: Windows portable Python ignores PYTHONPATH.
        code = ('import runpy,sys;sys.path.insert(0,sys.argv.pop(1));'
                'p=sys.argv.pop(1);sys.path.insert(0,__import__("os").path.dirname(p));'
                'runpy.run_path(p,run_name="__main__")')
        command = [sys.executable, '-u', '-B', '-c', code, str(self.source), str(comfy / 'main.py'),
                   '--listen', '127.0.0.1', '--port', str(port), '--disable-auto-launch',
                   '--base-directory', str(directory), '--user-directory', str(directory / 'user'),
                   '--temp-directory', str(directory), '--input-directory', folder_paths.get_input_directory(),
                   '--output-directory', folder_paths.get_output_directory(),
                   '--extra-model-paths-config', str(config),
                   '--disable-all-custom-nodes', '--whitelist-custom-nodes', 'FreeVideoExecutor']
        with (directory / 'comfy.log').open('wb') as log:
            process = self._start_worker(command, cwd=str(comfy), env=env, stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=subprocess.STDOUT,
                                         start_new_session=True, supervise=True)
        return process, directory / 'comfy.log'

    def start(self) -> None:
        with self._lock:
            if len(self.devices) < 2:
                return
            if self._workers:
                if len(self._workers) != len(self.devices) or any(w.process.poll() is not None for w in self._workers):
                    raise RuntimeError('Executor pool requires confirmed shutdown before restart')
                return
            if len({device.uuid for device in self.devices}) != len(self.devices):
                raise ValueError('Duplicate executor device')
            atexit.register(self.stop)
            try:
                for device in self.devices:
                    with socket.socket() as listener:
                        listener.bind(('127.0.0.1', 0))
                        port = listener.getsockname()[1]
                    process, log = self._launch(device, port)
                    worker = Worker(device, port, process, 'http://127.0.0.1:%d' % port, log)
                    self._workers.append(worker)
                    deadline = time.monotonic() + READY_SECONDS
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError('Executor exited during startup: ' + device.uuid)
                        try:
                            identity = _request(worker, '/freevideo/executor')
                            if (isinstance(identity, dict) and identity.get('device') == device.uuid
                                    and type(identity.get('pid')) is int):
                                break
                            raise RuntimeError('Executor identity mismatch: ' + device.uuid)
                        except (OSError, ValueError):
                            if time.monotonic() >= deadline:
                                raise TimeoutError('Executor startup timed out: ' + device.uuid) from None
                            time.sleep(.1)
            except BaseException:
                self.stop()
                raise

    def stop(self) -> None:
        with self._lock:
            failed = []
            for worker in reversed(self._workers):
                try:
                    processes.stop(worker.process)
                    worker.process.wait(timeout=5)
                    if worker.process.poll() is None:
                        raise RuntimeError('Executor exit is unconfirmed')
                except Exception:
                    failed.append(worker)
            # Retain unconfirmed owners so a subsequent stop can finish cleanup.
            self._workers = list(reversed(failed))
            if failed:
                raise RuntimeError('Executor shutdown is unconfirmed: ' + ', '.join(w.device.uuid for w in failed))
            atexit.unregister(self.stop)

    def status(self, server) -> dict:
        """Lifecycle only. The front owns queue/history aggregation and dispatch.

        The front attaches its Scheduler as server._freevideo_scheduler before
        advertising readiness. Missing/stale execution state is never idle.
        """
        confirmed, busy, rows = True, False, []
        workers = self.workers()
        if len(workers) != len(self.devices):
            confirmed = False
        for worker in workers:
            state = 'unknown'
            try:
                if worker.process.poll() is None:
                    value = _request(worker, '/queue')
                    if isinstance(value.get('queue_running'), list) and isinstance(value.get('queue_pending'), list):
                        state = 'busy' if value['queue_running'] or value['queue_pending'] else 'idle'
                else:
                    # A confirmed exit runs nothing; the front has moved its work to the other devices.
                    state = 'exited'
            except (OSError, ValueError, AttributeError):
                pass
            confirmed = confirmed and state != 'unknown'
            busy = busy or state not in ('idle', 'exited')
            rows.append(dict(device=worker.device.uuid, state=state))
        try:
            view = server._freevideo_scheduler.view()
            busy = busy or bool(view['running'] or view['pending'])
            running, pending = server.prompt_queue.get_current_queue()
            busy = busy or bool(running or pending)
            agent = getattr(server, '_freevideo_agent', None)
            busy = busy or bool(agent is not None and agent.active())
            from .comfy_chain import RUNNER
            if RUNNER is not None:
                with RUNNER.lock:
                    busy = busy or bool(RUNNER.active or RUNNER.queue)
                    busy = busy or any(row.get('output_state') == 'joining' for row in RUNNER.records.values())
        except Exception:
            confirmed = False
        return dict(confirmed=confirmed, busy=busy or not confirmed, workers=rows)


async def register_pool(server, *, root, source):
    """Called once by the control node extension; never by a private executor."""
    from aiohttp import web
    from .comfy_environment import control_process
    from .gpu_pool import eligible_devices, pool_enabled
    if (not control_process() or not pool_enabled()
            or getattr(server, '_freevideo_front', None) is not None):
        return
    from .admission import AdmissionLedger
    from .gpu_scheduler import Scheduler
    from .pool_resources import PromptEnvelope, probe_host_memory
    from .comfy_front import register
    devices = eligible_devices()
    if len(devices) < 2:
        return
    ledger = AdmissionLedger(probe_host_memory)
    scheduler = Scheduler(devices, ledger)
    pool = PoolSupervisor(devices, root=root, source=source)
    await asyncio.to_thread(pool.start)
    server._freevideo_pool = pool
    try:
        register(server, scheduler=scheduler, supervisor=pool,
                 envelope=PromptEnvelope(root, devices))
    except BaseException:
        await asyncio.to_thread(pool.stop)
        del server._freevideo_pool
        raise

    @server.routes.get('/freevideo/pool')
    async def status(request):
        return web.json_response(await asyncio.to_thread(pool.status, server))

    async def cleanup(app):
        # Cleanup contexts first stop front dispatch/streams, then owned workers.
        await asyncio.to_thread(pool.stop)
    server.app.on_cleanup.append(cleanup)


async def executor_entrypoint():
    """The private plugin registers execution/progress only, no durable services."""
    from aiohttp import web
    from server import PromptServer
    from .comfy_environment import executor_device
    from .comfy_nodes import FreeVideoExtension, register_prompt_cleanup
    from .comfy_progress import register
    device = executor_device()
    if not device or os.environ.get('CUDA_VISIBLE_DEVICES') != device:
        raise RuntimeError('Executor device binding is missing')
    server = PromptServer.instance
    register()
    register_prompt_cleanup()

    @server.routes.get('/freevideo/executor')
    async def identity(request):
        return web.json_response(dict(device=device, pid=os.getpid()))

    @server.routes.get('/freevideo/executor/hardware')
    async def hardware(request):
        from .hardware import detect
        from dataclasses import asdict
        return web.json_response(asdict(await asyncio.to_thread(detect)))

    @server.routes.post('/freevideo/executor/interrupt')
    async def cancel(request):
        import nodes
        value = await request.json()
        prompt_id = value.get('prompt_id')
        if not isinstance(prompt_id, str) or not prompt_id:
            raise web.HTTPBadRequest()
        queue = server.prompt_queue
        with queue.mutex:
            removed = bool(queue.delete_queue_item(lambda row: row[1] == prompt_id))
            running, _ = queue.get_current_queue()
            active = any(row[1] == prompt_id for row in running)
            if active:
                nodes.interrupt_processing()
        # A queue item deleted before the executor took it leaves no history.
        state = 'running' if active else 'removed' if removed else 'missing'
        return web.json_response(dict(ok=True, state=state))
    return FreeVideoExtension()
