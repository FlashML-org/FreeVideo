"""Pure central scheduling; executors own process startup and exit confirmation.

Callers use this only when gpu_pool.pool_enabled() is true. Single-device
installations keep their existing entry points, planning, environment and locks.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from threading import Lock
from typing import TYPE_CHECKING

from .admission import AdmissionLedger, Envelope

if TYPE_CHECKING:
    from .gpu_pool import Device


@dataclass
class Task:
    id: str
    kind: str
    envelope: Envelope | None
    depends_on: tuple = ()
    chain_id: str | None = None
    client_id: str = ''
    min_device_bytes: int = 0


class Scheduler:
    """Dispatch the earliest ready, fitting tasks in submission order.

    Devices are already eligible; their order is stable, with the installation
    default first. Dependencies require success, and each chain has at most
    one running block. A ready unknown-envelope task drains the running set
    before starting exclusively, so later arrivals cannot starve it.
    """

    def __init__(self, devices: list[Device], ledger: AdmissionLedger):
        self._devices = tuple(devices)
        if len({device.uuid for device in self._devices}) != len(self._devices):
            raise ValueError('Device UUIDs must be unique')
        self._ledger = ledger
        self._lock = Lock()
        self._pending = {}
        self._running = {}
        self._known = set()
        self._succeeded = set()
        self._cancelled = set()
        self._bindings = {}
        self._down = set()

    def submit(self, task: Task) -> None:
        if not isinstance(task.id, str) or not task.id:
            raise ValueError('Task id must be a nonempty string')
        if task.envelope is not None and not isinstance(task.envelope, Envelope):
            raise TypeError('Task envelope must be an Envelope or None')
        if type(task.min_device_bytes) is not int or task.min_device_bytes < 0:
            raise ValueError('Minimum device capacity must be nonnegative integer bytes')
        if isinstance(task.depends_on, str):
            raise ValueError('Dependencies must be a sequence of task ids')
        task = replace(task, depends_on=tuple(task.depends_on))
        if any(not isinstance(item, str) or not item or item == task.id for item in task.depends_on):
            raise ValueError('Dependencies must name other tasks')
        with self._lock:
            if task.id in self._known:
                raise ValueError('Task ids cannot be reused')
            self._known.add(task.id)
            self._pending[task.id] = task

    def cancel(self, task_id) -> str:
        """Running cancellations hold their device and peak until finished()."""
        with self._lock:
            if task_id in self._pending:
                del self._pending[task_id]
                self._bindings.pop(task_id, None)
                return 'queued'
            if task_id in self._running:
                self._cancelled.add(task_id)
                return 'running'
            return 'unknown'

    def mark_unavailable(self, device_uuid) -> None:
        """Stop placing work on a device whose executor exited; waiting tasks bound to it may move."""
        with self._lock:
            if device_uuid not in {device.uuid for device in self._devices}:
                return
            self._down.add(device_uuid)
            for task_id in [task_id for task_id, bound in self._bindings.items()
                            if bound == device_uuid and task_id in self._pending]:
                del self._bindings[task_id]

    def unplaceable(self) -> list[str]:
        """Waiting tasks that no remaining device can ever run."""
        with self._lock:
            alive = [device for device in self._devices if device.uuid not in self._down]
            return [task.id for task in self._pending.values()
                    if not any(device.total_bytes >= task.min_device_bytes for device in alive)]

    def reprioritize(self, task_ids) -> None:
        """Atomically reorder pending tasks without recycling their identities."""
        order = tuple(task_ids)
        with self._lock:
            if len(order) != len(self._pending) or set(order) != set(self._pending):
                raise ValueError('Reprioritization must name every pending task once')
            self._pending = {identity: self._pending[identity] for identity in order}

    def dispatch(self) -> list[tuple[Task, Device]]:
        with self._lock:
            if any(task.envelope is None for task, _ in self._running.values()):
                return []
            busy = {device.uuid for _, device in self._running.values()}
            chains = {task.chain_id for task, _ in self._running.values() if task.chain_id is not None}
            free = [device for device in self._devices if device.uuid not in busy and device.uuid not in self._down]
            dispatched = []
            for task in list(self._pending.values()):
                if not free:
                    break
                if not all(dependency in self._succeeded for dependency in task.depends_on):
                    continue
                if task.chain_id is not None and task.chain_id in chains:
                    continue
                binding = self._bindings.get(task.id)
                device = next((item for item in free
                               if item.total_bytes >= task.min_device_bytes
                               and (binding is None or item.uuid == binding)), None)
                if device is None:
                    continue
                if task.envelope is None:
                    if self._running:
                        break
                elif not self._ledger.try_reserve(task.id, task.envelope):
                    continue
                self._running[task.id] = (task, device)
                self._bindings[task.id] = device.uuid
                del self._pending[task.id]
                free.remove(device)
                if task.chain_id is not None:
                    chains.add(task.chain_id)
                dispatched.append((replace(task), device))
                if task.envelope is None:
                    break
            return dispatched

    def finished(self, task_id, ok: bool, retry: bool = False) -> None:
        """Confirm the attempt has stopped before releasing its resources.

        A failed retry joins the queue tail, rechecks admission, and stays on
        its original UUID. Cancel wins over success/retry. Executors must
        reject stale attempt callbacks before calling this task-id-only API.
        """
        with self._lock:
            running = self._running.pop(task_id, None)
            if running is None:
                return
            task, _ = running
            if task.envelope is not None:
                self._ledger.release(task_id)
            cancelled = task_id in self._cancelled
            self._cancelled.discard(task_id)
            if retry and not ok and not cancelled:
                self._pending[task_id] = task
                if self._bindings.get(task_id) in self._down:
                    del self._bindings[task_id]
            else:
                self._bindings.pop(task_id, None)
                if ok and not cancelled:
                    self._succeeded.add(task_id)

    def view(self) -> dict:
        """Return isolated Task snapshots; the facade serializes queue rows."""
        with self._lock:
            return {'running': [{'task': replace(task), 'device': device.uuid}
                                for task, device in self._running.values()],
                    'pending': [replace(task) for task in self._pending.values()]}
