"""Atomic host-memory admission for the central, in-process GPU queue."""
from dataclasses import dataclass
from threading import Lock


def _bytes(value):
    return type(value) is int and value >= 0


@dataclass(frozen=True)
class Envelope:
    """Future whole-task peaks supplied by the resource planner.

    host_ram_bytes includes resident pinned storage. commit_bytes includes
    non-pinned host and future GPU/WDDM commit; pinned_bytes is charged to
    commit separately, exactly once. These are peaks, not planner free budgets.
    """
    host_ram_bytes: int
    commit_bytes: int
    pinned_bytes: int

    def __post_init__(self):
        if not all(_bytes(value) for value in
                   (self.host_ram_bytes, self.commit_bytes, self.pinned_bytes)):
            raise ValueError('Envelope sizes must be nonnegative integer bytes')
        if self.pinned_bytes > self.host_ram_bytes:
            raise ValueError('Pinned storage must be included in the host RAM peak')


@dataclass(frozen=True)
class HostMemory:
    """Fresh usable headroom after system reserves and applicable host caps.

    The probe must bound physical RAM by cgroup/user limits and supply Windows
    commit headroom separately. A platform without a commit counter may use
    its usable physical headroom as a conservative commit bound. Missing or
    invalid measurements deny admission; swap never extends physical RAM.
    """
    free_ram_bytes: int
    commit_headroom_bytes: int


class AdmissionLedger:
    """Reserve full peaks until confirmed exit; do not infer allocation credit.

    The two-counter probe cannot attribute live allocations to tasks. Keeping
    their full envelopes against live free memory can underutilize the host,
    but never credits another task's working set or idle cache. The caller
    owns probe freshness, system reserves and the single central coordinator;
    this lock is not a cross-process or cross-installation resource lease.
    """

    def __init__(self, probe):
        self._probe = probe
        self._lock = Lock()
        self._reservations = {}

    def try_reserve(self, task_id, envelope) -> bool:
        if not isinstance(envelope, Envelope):
            raise TypeError('Admission requires a complete Envelope')
        with self._lock:
            previous = self._reservations.get(task_id)
            if previous is not None:
                if previous != envelope:
                    raise ValueError('Release a task before changing its envelope')
                return True
            try:
                memory = self._probe()
                free, commit = memory.free_ram_bytes, memory.commit_headroom_bytes
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
                return False
            if not _bytes(free) or not _bytes(commit):
                return False
            ram_need = envelope.host_ram_bytes
            commit_need = envelope.commit_bytes + envelope.pinned_bytes
            for reserved in self._reservations.values():
                ram_need += reserved.host_ram_bytes
                commit_need += reserved.commit_bytes + reserved.pinned_bytes
            if ram_need > free or commit_need > commit:
                return False
            self._reservations[task_id] = envelope
            return True

    def release(self, task_id) -> None:
        with self._lock:
            self._reservations.pop(task_id, None)
