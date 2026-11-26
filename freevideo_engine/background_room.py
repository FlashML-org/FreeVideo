"""Whether the user's other programs leave room for a background compile.

The compile covers only the first sampling block and one decode clip. It
starts only with its own measured host peak plus RAM_MARGIN available, using
a measured baseline until a matching compile has completed. The GPU must be nearly idle
and other programs hold little of its memory. While it runs it gives way when
availability falls under RAM_FLOOR, or when another program starts using the
GPU, grows its memory there or keeps its cores busy. Measured on the machine,
never assumed: NVML for the GPU and the engine's own RAM accounting for the host.

Our own plan fills the GPU and keeps it busy, so while compiling only
per-process readings can tell another program apart. Windows often reports
no per-process memory, and a game in a browser that was already open adds no
process; per-process utilization covers both where the driver reports it.
Our processes are this one, its descendants (workers, kernel checks, their
interpreters behind a venv launcher, compiler processes) and the
interactive owner; never a process merely running the same Python.
"""
import collections
import ctypes
import os
from pathlib import Path
import sys
import time

GiB = 2**30
RAM_MARGIN = 2 * GiB
RAM_FLOOR = 2 * GiB
# Cold 1344x768/243-frame, two-pass precompile: 5.22 GiB working PSS for
# supervisor + workers on Linux, under a 12 GiB/no-swap cgroup, with no host
# weight retention and one compiler. Round up; Windows needs physical checks.
# RAM_MARGIN keeps the existing running floor available beyond this peak.
DEFAULT_RAM_NEED = 6 * GiB
GPU_IDLE_PERCENT = 10
GPU_SAMPLES = 5
GPU_OTHER_SHARE = .2
GPU_OTHER_MINIMUM = 2 * GiB
GPU_GROWTH = GiB
# Another program's share of the GPU's cores that makes the compile give way.
OTHER_BUSY_PERCENT = 20
NOT_FOUND = 6
# Consecutive checks a new program must stay on the GPU before the compile
# gives way; a desktop process touching the GPU once does not count.
NEW_PROCESS_CHECKS = 3
# Busy samples further apart than this start a new count: sparse moments of
# work are not a program keeping the GPU busy.
BUSY_GAP_MICROSECONDS = 3_000_000
# Checks in a row without a process list before the compile gives way: it
# could no longer tell another program from its own work.
UNLISTED_CHECKS = 10
# Other programs' per-process samples kept for precompile.log. A busy browser
# tab on Windows took about 14 s to make the compile give way; these show how
# often Windows reports such a process and how busy, before the rule changes.
TRACE_SAMPLES = 240
CHECK_SECONDS = 1.
NOT_AVAILABLE = 0xFFFFFFFFFFFFFFFF
INSUFFICIENT_SIZE = 7


class ProcessInfo(ctypes.Structure):
    _fields_ = [('pid', ctypes.c_uint), ('used', ctypes.c_ulonglong),
                ('gpu_instance', ctypes.c_uint), ('compute_instance', ctypes.c_uint)]


class UtilizationSample(ctypes.Structure):
    _fields_ = [('pid', ctypes.c_uint), ('timestamp', ctypes.c_ulonglong), ('sm', ctypes.c_uint),
                ('memory', ctypes.c_uint), ('encoder', ctypes.c_uint), ('decoder', ctypes.c_uint)]


class Gpu:
    """NVML readings of the whole device, including other programs."""
    def __init__(self, device):
        from .monitoring import nvml_device
        self.lib, self.handle, self.device = nvml_device(device)

    def utilization(self):
        from .monitoring import Utilization
        value = Utilization()
        return value.gpu if self.lib.nvmlDeviceGetUtilizationRates(self.handle, ctypes.byref(value)) == 0 else None

    def memory(self):
        from .monitoring import Memory
        value = Memory()
        if self.lib.nvmlDeviceGetMemoryInfo(self.handle, ctypes.byref(value)):
            return None
        return value.used, value.total

    def processes(self):
        """{pid: bytes or None} of compute and graphics processes; None when NVML cannot list them."""
        found = {}
        for name in ('nvmlDeviceGetComputeRunningProcesses', 'nvmlDeviceGetGraphicsRunningProcesses'):
            function = next((getattr(self.lib, name + suffix) for suffix in ('_v3', '_v2')
                             if hasattr(self.lib, name + suffix)), None)
            if function is None:
                return None
            count = ctypes.c_uint(0)
            rc = function(self.handle, ctypes.byref(count), None)
            for _ in range(3):
                # The list can grow between asking its size and reading it.
                if rc != INSUFFICIENT_SIZE:
                    break
                count = ctypes.c_uint(count.value + 16)
                rows = (ProcessInfo * count.value)()
                rc = function(self.handle, ctypes.byref(count), rows)
                if rc == 0:
                    for row in rows[:count.value]:
                        used = None if row.used == NOT_AVAILABLE else row.used
                        found[row.pid] = used if found.get(row.pid) is None else found[row.pid]
            if rc:
                return None
        return found

    def busy(self, since):
        """({pid: (highest % of cores, encoder or decoder, its newest sample's stamp)}, newest stamp)
        for samples after `since`, in microseconds; ({}, since) without new ones; None if unsupported."""
        function = getattr(self.lib, 'nvmlDeviceGetProcessUtilization', None)
        if function is None:
            return None
        count = ctypes.c_uint(0)
        rc = function(self.handle, None, ctypes.byref(count), ctypes.c_ulonglong(since))
        if rc == NOT_FOUND:
            return {}, since
        if rc != INSUFFICIENT_SIZE:
            return None
        count = ctypes.c_uint(count.value + 16)
        rows = (UtilizationSample * count.value)()
        rc = function(self.handle, rows, ctypes.byref(count), ctypes.c_ulonglong(since))
        if rc == NOT_FOUND:
            return {}, since
        if rc:
            return None
        found, newest = {}, since
        for row in rows[:count.value]:
            values = [value for value in (row.sm, row.encoder, row.decoder) if value <= 100]
            if values:
                value, stamp = found.get(row.pid, (0, 0))
                found[row.pid] = (max(value, *values), max(stamp, row.timestamp))
            newest = max(newest, row.timestamp)
        return found, newest

    def close(self):
        lib, self.lib = self.lib, None
        if lib is not None:
            lib.nvmlShutdown()


def parents():
    """{pid: parent pid} of every process on the machine."""
    if sys.platform == 'win32':
        from .win32 import processes
        return processes()
    found = {}
    for path in Path('/proc').iterdir():
        if path.name.isdigit():
            try:
                found[int(path.name)] = int((path / 'stat').read_text(encoding='utf-8').rsplit(')', 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
    return found


def family(root, table=None):
    """`root` and every process descended from it; OSError when processes cannot be listed.

    `table`, a parents() reading, lets several families come from one listing."""
    table = parents() if table is None else table
    pids, frontier = {root}, {root}
    while frontier:
        frontier = {pid for pid, parent in table.items() if parent in frontier} - pids
        pids |= frontier
    return pids


def available_memory(sample=None):
    """Host memory left for the compile: the request's accounting, within any cgroup limit."""
    from .hardware import cgroup_memory
    from .system import effective_available, inference_headroom
    available = inference_headroom(sample) if sample is not None else effective_available()
    group = cgroup_memory()
    # Clean page cache inside the group is reclaimed before the limit is hit.
    group = group.get('reclaimable_available_bytes', group.get('available_bytes'))
    return min(available, group) if group is not None else available


def compile_need(previous, *, identity, requested, inputs):
    """Own completed compile peak for the same workload, never a generation peak.

    Changed code, packages, device, placement inputs or shapes use the baseline;
    an incomplete sample cannot establish a smaller memory requirement.
    """
    if (not isinstance(previous, dict) or identity is None or previous.get('success') is not True
            or previous.get('identity') != identity or previous.get('requested') != requested
            or previous.get('inputs') != inputs):
        return DEFAULT_RAM_NEED
    memory = previous.get('host_memory')
    if not isinstance(memory, dict) or memory.get('complete') is not True:
        return DEFAULT_RAM_NEED
    metric = 'tree private working set' if sys.platform == 'win32' else 'inference working PSS'
    peak = memory.get('peak_bytes')
    if memory.get('metric') != metric or type(peak) is not int or peak <= 0:
        return DEFAULT_RAM_NEED
    return max(DEFAULT_RAM_NEED, peak)


class Room:
    def __init__(self, device, *, need_bytes, owner=None, gpu=None):
        self.need = need_bytes
        self.owner = owner
        self.ours = set()
        self.gpu = gpu
        if self.gpu is None and device:
            try:
                self.gpu = Gpu(device)
            except Exception:
                self.gpu = None  # Without NVML only memory is checked.
        self.baseline = None
        self.baseline_memory = 0
        self.seen = {}
        self.busy = {}
        self.unlisted = 0
        self.checked = 0.
        self.sampled = int(time.time() * 1e6)
        self.started = self.sampled / 1e6
        self.trace = collections.deque(maxlen=TRACE_SAMPLES)
        self.host_peak = 0
        self.host_samples = 0
        self.host_complete = True

    def refresh(self):
        """Our processes now: this one, its descendants and the owner.

        When the system cannot list processes, the last known ones stay ours:
        a worker must never turn into another program for want of a list.
        """
        try:
            ours = family(os.getpid())
        except OSError:
            ours = self.ours or {os.getpid()}
        self.ours = ours | ({self.owner} if self.owner else set())

    def own(self, pid):
        return pid in self.ours

    def start_reason(self):
        """Why the compile should not start now, or None; also records the GPU's current users."""
        available = available_memory()
        if available < self.need + RAM_MARGIN:
            return 'memory: %.1f GiB available, %.1f GiB needed' % (available / GiB, (self.need + RAM_MARGIN) / GiB)
        if self.gpu is None:
            return None
        samples = []
        for index in range(GPU_SAMPLES):
            if index:
                time.sleep(.5)
            value = self.gpu.utilization()
            if value is not None:
                samples.append(value)
        if samples and sum(samples) / len(samples) > GPU_IDLE_PERCENT:
            return 'gpu busy: %.0f%% utilization' % (sum(samples) / len(samples))
        listed = self.gpu.processes()
        if listed is None:
            # Without the list nothing could tell another program from ours later.
            return 'gpu: its processes cannot be listed now'
        self.refresh()
        memory = self.gpu.memory()
        if memory is not None:
            used, total = memory
            own = sum(value for pid, value in listed.items() if value is not None and self.own(pid))
            if used - own > max(GPU_OTHER_MINIMUM, GPU_OTHER_SHARE * total):
                return 'gpu memory: %.1f GiB held by other programs' % ((used - own) / GiB)
        self.baseline = {pid for pid in listed if not self.own(pid)}
        self.baseline_memory = sum(value for pid, value in listed.items() if value is not None and not self.own(pid))
        self.sampled = int(time.time() * 1e6)
        return None

    def run_reason(self, sample=None):
        """Why a running compile should give way now, or None. Call it often; the GPU is read once a second."""
        if sample is not None:
            from .system import inference_memory_sample
            peak = inference_memory_sample(sample)
            self.host_samples += 1
            if type(peak) is int and peak >= 0:
                self.host_peak = max(self.host_peak, peak)
            else:
                self.host_complete = False
        available = available_memory(sample)
        if available < RAM_FLOOR:
            return 'memory: %.1f GiB available' % (available / GiB)
        if self.gpu is None or self.baseline is None or time.monotonic() - self.checked < CHECK_SECONDS:
            return None
        self.checked = time.monotonic()
        listed = self.gpu.processes()
        self.refresh()
        if listed is None:
            self.unlisted += 1
            if self.unlisted >= UNLISTED_CHECKS:
                return 'gpu: its processes cannot be listed'
        else:
            self.unlisted = 0
            # A program that left and comes back, or a reused PID, is new again.
            self.baseline &= set(listed)
            other = {pid: value for pid, value in listed.items() if not self.own(pid)}
            self.seen = {pid: self.seen.get(pid, 0) + 1 for pid in other if pid not in self.baseline}
            if any(count >= NEW_PROCESS_CHECKS for count in self.seen.values()):
                return 'gpu: another program started using it'
            grown = sum(value for value in other.values() if value is not None) - self.baseline_memory
            if grown > GPU_GROWTH:
                return 'gpu memory: other programs took %.1f GiB more' % (grown / GiB)
        sampled = self.gpu.busy(self.sampled)
        if sampled is not None and sampled[0]:
            # Only new samples move the count; a quiet second keeps it, and
            # busy samples more than BUSY_GAP apart count from one again.
            cores, self.sampled = sampled
            busy = {}
            for pid, (value, stamp) in cores.items():
                if not self.own(pid):
                    self.trace.append((time.time() - self.started, pid, value, stamp))
                if value >= OTHER_BUSY_PERCENT and not self.own(pid):
                    count, last = self.busy.get(pid, (0, 0))
                    busy[pid] = (count + 1 if stamp - last <= BUSY_GAP_MICROSECONDS else 1, stamp)
            self.busy = busy
            if any(count >= NEW_PROCESS_CHECKS for count, _ in self.busy.values()):
                return 'gpu: another program keeps it busy'
        return None

    def memory_result(self):
        """The supervisor and every worker/compiler, excluding reclaimable mapped pages."""
        return dict(peak_bytes=self.host_peak, samples=self.host_samples,
                    complete=self.host_complete and self.host_samples > 0,
                    metric='tree private working set' if sys.platform == 'win32' else 'inference working PSS')

    def samples(self):
        """Other programs' samples for the log: [seconds since this room started,
        pid, % of cores, seconds after the first sample's own stamp]."""
        first = self.trace[0][3] if self.trace else 0
        return [[round(seconds, 1), pid, value, round((stamp - first) / 1e6, 2)]
                for seconds, pid, value, stamp in self.trace]

    def close(self):
        if self.gpu is not None:
            self.gpu.close()
