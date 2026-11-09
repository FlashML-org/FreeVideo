"""Top-bar system status: GPU memory, load and temperature, free disk, and an immediate stop.

Stopping ends what is generating and pauses the waiting requests; it deletes none of them.
Readings that the driver cannot give are left out of the answer rather than reported as unknown.
"""
import ctypes
import logging
import os
import threading
import time

from .monitoring import Memory, Utilization, nvml_device


class Reader:
    """One open NVML handle, read on demand; a machine without NVML answers with no GPU rows."""
    def __init__(self, open_device=nvml_device):
        self.open_device, self.lock, self.state, self.failed = open_device, threading.Lock(), None, False

    def _open(self):
        if self.state is None and not self.failed:
            try:
                lib, handle, _ = self.open_device(0)
                name = ctypes.create_string_buffer(96)
                label = name.value.decode('utf-8', 'replace') if lib.nvmlDeviceGetName(handle, name, 96) == 0 else ''
                self.state = (lib, handle, label)
            except (OSError, RuntimeError, ValueError, AttributeError):
                self.failed = True
        return self.state

    def read(self):
        with self.lock:
            state = self._open()
            if state is None:
                return {}
            lib, handle, label = state
            result = {}
            if label:
                result['name'] = label
            memory = Memory()
            if lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(memory)) == 0 and memory.total and memory.used <= memory.total:
                result['memory_used'], result['memory_total'] = int(memory.used), int(memory.total)
            load = Utilization()
            if lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(load)) == 0 and load.gpu <= 100:
                result['utilization'] = int(load.gpu)
            degrees = ctypes.c_uint()
            if lib.nvmlDeviceGetTemperature(handle, 0, ctypes.byref(degrees)) == 0 and 0 < degrees.value < 150:
                result['temperature'] = int(degrees.value)
            return result


class Gate:
    """Holds the waiting requests back while paused; the running one is not touched."""
    def __init__(self):
        self.paused = False
        self.changed = threading.Event()

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False
        self.changed.set()
        self.changed.clear()

    def hold(self, timeout):
        """True when a paused queue spent the wait, so the caller reports nothing to run."""
        if not self.paused:
            return False
        self.changed.wait(1.0 if timeout is None else min(timeout, 1.0))
        return True


def install_gate(queue, gate):
    """Make a Comfy PromptQueue take no new request while the gate is paused."""
    if getattr(queue, '_freevideo_gate', None) is not None:
        return queue._freevideo_gate
    original = queue.get

    def get(timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while gate.hold(timeout):
            if deadline is not None and time.monotonic() >= deadline:
                return None
        return original(timeout)

    queue.get = get
    queue._freevideo_gate = gate
    return gate


def disk_free(path):
    from .disk_space import free_bytes
    try:
        free = free_bytes(path)
    except (OSError, ValueError):
        return None
    return int(free) if isinstance(free, (int, float)) and free >= 0 else None


def snapshot(reader, root, busy, paused):
    result = {'busy': bool(busy), 'paused': bool(paused)}
    result.update(reader.read())
    free = disk_free(root)
    if free is not None:
        result['disk_free'] = free
    return result


def register():
    from aiohttp import web
    import asyncio
    import folder_paths
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_monitor', None) is not None:
        return
    reader, gate = Reader(), Gate()
    server._freevideo_monitor = gate
    install_gate(server.prompt_queue, gate)
    root = folder_paths.base_path

    def front():
        return getattr(server, '_freevideo_front', None)

    def running():
        found = front()
        if found is not None:
            return found.current_queue()[0]
        return server.prompt_queue.get_current_queue()[0]

    def busy():
        return bool(running())

    def reply(value, status=200):
        return web.json_response(value, status=status, headers={'Cache-Control': 'no-store'})

    @server.routes.get('/freevideo/monitor')
    async def read(request):
        return reply(await asyncio.to_thread(snapshot, reader, root, busy(), gate.paused))

    @server.routes.post('/freevideo/monitor/stop')
    async def stop(request):
        gate.pause()
        found = front()
        if found is not None:
            found.paused = True
            for row in running():
                found.cancel(row[1])
        else:
            import nodes
            nodes.interrupt_processing()
        from . import comfy_chain
        runner = comfy_chain.RUNNER
        if runner is not None:
            with runner.lock:
                active = [key for key, row in runner.records.items() if row['state'] in ('queued', 'running')]
            for key in active:
                try:
                    runner.cancel(key)
                except Exception:
                    logging.getLogger(__name__).warning('Could not stop chain %s', key, exc_info=True)
        return reply({'paused': True})

    @server.routes.post('/freevideo/monitor/resume')
    async def resume(request):
        gate.resume()
        found = front()
        if found is not None:
            found.paused = False
        return reply({'paused': False})
