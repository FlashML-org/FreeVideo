"""Windows memory/process primitives. No CUDA, psutil, admin rights or shell."""
import ctypes as C
from functools import lru_cache
import os

DWORD, BOOL, HANDLE, SIZE_T = C.c_uint32, C.c_int32, C.c_void_p, C.c_size_t
QWORD = C.c_uint64


@lru_cache(None)
def kernel32():
    lib = C.WinDLL('kernel32', use_last_error=True)
    declarations = {
        'CloseHandle': (BOOL, [HANDLE]),
        'GetCurrentProcess': (HANDLE, []),
        'OpenProcess': (HANDLE, [DWORD, BOOL, DWORD]),
        'GetExitCodeProcess': (BOOL, [HANDLE, C.POINTER(DWORD)]),
        'CreateToolhelp32Snapshot': (HANDLE, [DWORD, DWORD]),
        'Process32FirstW': (BOOL, [HANDLE, C.c_void_p]),
        'Process32NextW': (BOOL, [HANDLE, C.c_void_p]),
        'GetProcessIoCounters': (BOOL, [HANDLE, C.c_void_p]),
        'GetProcessTimes': (BOOL, [HANDLE, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]),
        'GlobalMemoryStatusEx': (BOOL, [C.c_void_p]),
        'CreateJobObjectW': (HANDLE, [C.c_void_p, C.c_wchar_p]),
        'SetInformationJobObject': (BOOL, [HANDLE, C.c_int, C.c_void_p, DWORD]),
        'AssignProcessToJobObject': (BOOL, [HANDLE, HANDLE]),
        'TerminateJobObject': (BOOL, [HANDLE, DWORD]),
        'CreateEventW': (HANDLE, [C.c_void_p, BOOL, BOOL, C.c_wchar_p]),
        'SetEvent': (BOOL, [HANDLE]),
        'WaitForSingleObject': (DWORD, [HANDLE, DWORD]),
        'DuplicateHandle': (BOOL, [HANDLE, HANDLE, HANDLE, C.POINTER(HANDLE), DWORD, BOOL, DWORD]),
        'SetHandleInformation': (BOOL, [HANDLE, DWORD, DWORD]),
        'GetConsoleMode': (BOOL, [HANDLE, C.POINTER(DWORD)]),
        'SetConsoleMode': (BOOL, [HANDLE, DWORD]),
        'CreateFileW': (HANDLE, [C.c_wchar_p, DWORD, DWORD, C.c_void_p, DWORD, DWORD, HANDLE]),
        'GetFileInformationByHandle': (BOOL, [HANDLE, C.c_void_p]),
        'GetFileInformationByHandleEx': (BOOL, [HANDLE, C.c_int, C.c_void_p, DWORD]),
    }
    for name, (result, arguments) in declarations.items():
        function = getattr(lib, name)
        function.restype, function.argtypes = result, arguments
    return lib


def checked(value):
    if not value:
        raise C.WinError(C.get_last_error())
    return value


def process_exited(pid):
    """True/False/None for exited/running/uninspectable, without waiting.

    A terminated Windows process can retain its PID/object while handles remain
    open. Its process object is already signaled, even if metadata enumeration
    still finds it or reports access denied. Do not infer exit from either of
    those metadata results, or from an exit code that could equal STILL_ACTIVE.
    """
    if type(pid) is not int or not 0 < pid <= 0xffffffff:
        return None
    lib = kernel32()
    handle = lib.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE only; no admin access.
    if not handle:
        return True if C.get_last_error() == 87 else None  # ERROR_INVALID_PARAMETER: PID absent.
    try:
        result = lib.WaitForSingleObject(handle, 0)
        if result == 0:  # WAIT_OBJECT_0: all process threads have terminated.
            return True
        if result == 0x102:  # WAIT_TIMEOUT: the process is not yet signaled.
            return False
        return None  # WAIT_FAILED or any unexpected status is not death evidence.
    except OSError:
        return None
    finally:
        lib.CloseHandle(handle)


def file_change_time_ns(path):
    """NTFS ChangeTime, unlike Windows stat().st_ctime (creation time).

    None means the filesystem/API cannot establish a reusable file identity.
    The caller must hash the content or retain the source in that case.
    """
    class BasicInfo(C.Structure):
        _fields_ = [(name, C.c_int64) for name in ('created', 'accessed', 'written', 'changed')] + [('attributes', DWORD)]
    lib = kernel32()
    handle = lib.CreateFileW(str(path), 0x80, 7, None, 3, 0x02000000, None)
    if handle == C.c_void_p(-1).value:
        return None
    try:
        info = BasicInfo()
        if not lib.GetFileInformationByHandleEx(handle, 0, C.byref(info), C.sizeof(info)) or info.changed <= 116444736000000000:
            return None
        return (info.changed - 116444736000000000) * 100
    finally:
        lib.CloseHandle(handle)


def open_regular(path):
    """Open the link itself, reject reparse points, then transfer handle to CRT."""
    import msvcrt
    class FileInfo(C.Structure):
        _fields_ = [('attributes', DWORD), ('created', DWORD * 2), ('accessed', DWORD * 2),
                    ('written', DWORD * 2), ('volume', DWORD), ('size_high', DWORD),
                    ('size_low', DWORD), ('links', DWORD), ('index_high', DWORD), ('index_low', DWORD)]
    lib = kernel32()
    handle = lib.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x00200000 | 0x08000000, None)
    if handle == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    try:
        info = FileInfo()
        checked(lib.GetFileInformationByHandle(handle, C.byref(info)))
        if info.attributes & (0x400 | 0x10):
            raise ValueError('Not a regular file; links and directories are excluded from diagnostics')
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        handle = None
        return descriptor
    finally:
        if handle:
            lib.CloseHandle(handle)


class MemoryStatus(C.Structure):
    _fields_ = [('length', DWORD), ('load', DWORD)] + [(n, QWORD) for n in
        ('total_physical', 'available_physical', 'commit_limit', 'commit_available',
         'total_virtual', 'available_virtual', 'available_extended')]


def memory_status():
    value = MemoryStatus()
    value.length = C.sizeof(value)
    checked(kernel32().GlobalMemoryStatusEx(C.byref(value)))
    return {'total_bytes': value.total_physical,
            'available_bytes': min(value.available_physical, value.commit_available),
            'physical_available_bytes': value.available_physical,
            'commit_limit_bytes': value.commit_limit, 'commit_available_bytes': value.commit_available,
            'swap_total_bytes': None, 'swap_free_bytes': None,
            'scope': 'GlobalMemoryStatusEx: available = min(physical RAM, commit headroom); pagefile is not extra physical RAM.'}


class ProcessEntry(C.Structure):
    _fields_ = [('dwSize', DWORD), ('cntUsage', DWORD), ('pid', DWORD), ('heap', SIZE_T),
                ('module', DWORD), ('threads', DWORD), ('parent', DWORD),
                ('priority', C.c_int32), ('flags', DWORD), ('exe', C.c_wchar * 260)]


class ProcessCounters(C.Structure):
    _fields_ = [('cb', DWORD), ('faults', DWORD)] + [(n, SIZE_T) for n in
        ('peak_working_set', 'working_set', 'peak_paged_pool', 'paged_pool',
         'peak_nonpaged_pool', 'nonpaged_pool', 'pagefile', 'peak_pagefile', 'private_usage')]


class VmCountersEx2(C.Structure):
    """ProcessVmCounters (Win8+). Private working set excludes mapped files.

    GetProcessMemoryInfo offers no equivalent: its working set counts resident
    mapped file pages and its PrivateUsage counts nonresident commit.
    """
    _fields_ = ([(n, SIZE_T) for n in ('peak_virtual', 'virtual_size')] + [('faults', DWORD)] +
                [(n, SIZE_T) for n in ('peak_working_set', 'working_set', 'peak_paged_pool',
                 'paged_pool', 'peak_nonpaged_pool', 'nonpaged_pool', 'pagefile',
                 'peak_pagefile', 'private_usage', 'private_working_set', 'shared_commit')])


class IoCounters(C.Structure):
    _fields_ = [(n, QWORD) for n in ('reads', 'writes', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]


@lru_cache(None)
def psapi():
    lib = C.WinDLL('psapi', use_last_error=True)
    lib.GetProcessMemoryInfo.argtypes = [HANDLE, C.POINTER(ProcessCounters), DWORD]
    lib.GetProcessMemoryInfo.restype = BOOL
    return lib


@lru_cache(None)
def ntdll():
    lib = C.WinDLL('ntdll', use_last_error=True)
    lib.NtQueryInformationProcess.argtypes = [HANDLE, C.c_int, C.c_void_p, DWORD, C.POINTER(DWORD)]
    lib.NtQueryInformationProcess.restype = C.c_long
    return lib


def private_working_set(handle):
    """Resident private bytes, or None when this kernel cannot report them.

    PROCESS_QUERY_LIMITED_INFORMATION is sufficient; no admin rights.
    """
    counters, written = VmCountersEx2(), DWORD()
    status = ntdll().NtQueryInformationProcess(handle, 3, C.byref(counters),
                                               C.sizeof(counters), C.byref(written))
    if status < 0 or written.value < C.sizeof(counters):
        return None
    return counters.private_working_set


def processes():
    lib = kernel32()
    snapshot = lib.CreateToolhelp32Snapshot(2, 0)
    if snapshot == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    try:
        entry = ProcessEntry()
        entry.dwSize = C.sizeof(entry)
        more = lib.Process32FirstW(snapshot, C.byref(entry))
        if not more:
            checked(more)
        result = {}
        while more:
            result[entry.pid] = entry.parent
            more = lib.Process32NextW(snapshot, C.byref(entry))
        return result
    finally:
        lib.CloseHandle(snapshot)


class InaccessibleProcess(PermissionError):
    """OpenProcess refused even PROCESS_QUERY_LIMITED_INFORMATION for this PID."""


def process_names():
    """Executable names by PID for diagnostics; empty when a snapshot is unavailable."""
    try:
        lib = kernel32()
        snapshot = lib.CreateToolhelp32Snapshot(2, 0)
        if snapshot == C.c_void_p(-1).value:
            return {}
        try:
            entry = ProcessEntry()
            entry.dwSize = C.sizeof(entry)
            names, more = {}, lib.Process32FirstW(snapshot, C.byref(entry))
            for _ in range(1 << 16):
                if not more:
                    break
                names[entry.pid] = entry.exe
                more = lib.Process32NextW(snapshot, C.byref(entry))
            return names
        finally:
            lib.CloseHandle(snapshot)
    except (OSError, AttributeError, ValueError):
        return {}


def process_memory(pid, *, parent_start_tick=None):
    """Read counters; a tree query also returns identity for old/exited children."""
    lib = kernel32()
    # Current Windows supports these queries without PROCESS_VM_READ. Keep a
    # synchronized handle so exit during a query is checked on this same process,
    # not on a potentially recycled PID.
    handle, synchronized = lib.OpenProcess(0x1000 | 0x100000, False, pid), True
    if not handle:
        error = C.get_last_error()
        if error == 87:  # PID exited between enumeration and OpenProcess.
            return None
        if error != 5:
            raise C.WinError(error)
        # Another account's process can grant PROCESS_QUERY_LIMITED_INFORMATION
        # but not SYNCHRONIZE; read it with that right and judge exit by its code.
        handle, synchronized = lib.OpenProcess(0x1000, False, pid), False
        if not handle:
            error = C.get_last_error()
            if error == 87:
                return None
            if error == 5:
                raise InaccessibleProcess(5, 'Access is denied.', None, 5)
            raise C.WinError(error)
    started = None
    def signaled():
        if synchronized:
            return lib.WaitForSingleObject(handle, 0) == 0
        code = DWORD()
        # An exit code equal to STILL_ACTIVE reads as running, never as exited.
        return bool(lib.GetExitCodeProcess(handle, C.byref(code))) and code.value != 259
    def exited_row():
        return {'start_tick': started, 'exited': True} if parent_start_tick is not None and started is not None else None
    try:
        times = [QWORD() for _ in range(4)]
        checked(lib.GetProcessTimes(handle, *(C.byref(t) for t in times)))
        started = times[0].value
        # Toolhelp retains a child's original parent PID after the parent exits.
        # A new worker can reuse it. Reject that entire old branch before asking
        # for memory counters (which may be inaccessible for system processes).
        if parent_start_tick is not None and started < parent_start_tick:
            return {'start_tick': started, 'excluded': 'predates-parent'}
        if signaled():
            return exited_row()
        memory, io = ProcessCounters(), IoCounters()
        memory.cb = C.sizeof(memory)
        checked(psapi().GetProcessMemoryInfo(handle, C.byref(memory), memory.cb))
        io_ok = lib.GetProcessIoCounters(handle, C.byref(io))
        resident = private_working_set(handle)
        if resident is None:
            raise OSError('ProcessVmCounters did not report a private working set')
        return {'rss_bytes': memory.working_set, 'private_commit_bytes': memory.private_usage,
                'private_working_set_bytes': resident,
                'read_bytes': io.read_bytes if io_ok else 0, 'write_bytes': io.write_bytes if io_ok else 0,
                'io_complete': bool(io_ok), 'start_tick': started}
    except OSError:
        if signaled():
            return exited_row()
        raise
    finally:
        lib.CloseHandle(handle)


class ProcessMemory:
    """Working set, private commit and private working set, never called PSS.

    The RAM guard is the private working set: the other two each include memory
    the process does not hold in physical RAM. All three remain in the report.
    """
    def __init__(self):
        self.peak_rss = self.peak_private = self.peak_guard = self.samples = 0
        self.peak_resident = 0
        self.minimum_available = self.minimum_physical = self.minimum_commit = None
        self.minimum_effective = None
        self.complete = self.io_complete = True
        self.io = {}
        self.errors = []
        self.excluded = []

    def sample(self, root_pid):
        parents = processes()
        children = {}
        for pid, parent in parents.items():
            children.setdefault(parent, []).append(pid)
        pending, visited = [(root_pid, None)], set()
        rss = private = resident = count = 0
        incomplete, errors, excluded = [], [], []
        names = None
        # Parents first: each edge must agree with the processes' creation times.
        for pid, parent_started in pending:
            if pid in visited:
                continue
            visited.add(pid)
            started = parent_started
            try:
                row = process_memory(pid, parent_start_tick=parent_started)
                if row is None:
                    # An exiting intermediate may still have live children in
                    # this snapshot. Check those against the known ancestor.
                    if parent_started is not None:
                        pending.extend((child, parent_started) for child in children.get(pid, []))
                    continue
                started = row['start_tick']
                if row.get('excluded'):
                    excluded.append(dict(pid=pid, parent_pid=parents.get(pid),
                        start_tick=started, parent_start_tick=parent_started, reason=row['excluded']))
                    continue
                if row.get('exited'):
                    pending.extend((child, started) for child in children.get(pid, []))
                    continue
                count += 1
                rss += row['rss_bytes']
                private += row['private_commit_bytes']
                resident += row['private_working_set_bytes']
                self.io[(pid, row['start_tick'])] = (row['read_bytes'], row['write_bytes'])
                self.io_complete &= row['io_complete']
            except InaccessibleProcess as error:
                names = process_names() if names is None else names
                if parent_started is None:
                    # The request's own worker must stay measurable.
                    incomplete.append(pid)
                    errors.append(dict(pid=pid, parent_pid=parents.get(pid), exe=names.get(pid),
                        error=str(error), winerror=5, exited=None))
                else:
                    # FreeVideo starts its helpers under the same account, so it
                    # can always open them. A descendant refused even limited
                    # query was started by Windows in another context (a crash
                    # reporter, for one); its pages are not this request's
                    # working set, and the system availability floor still
                    # applies. One such child used to abort a generation.
                    excluded.append(dict(pid=pid, parent_pid=parents.get(pid), exe=names.get(pid),
                        start_tick=None, parent_start_tick=parent_started, reason='inaccessible'))
                    pending.extend((child, parent_started) for child in children.get(pid, []))
                    continue
            except OSError as error:
                exited = process_exited(pid)
                if exited is not True:
                    names = process_names() if names is None else names
                    incomplete.append(pid)
                    error_row = dict(pid=pid, parent_pid=parents.get(pid),
                        error=str(error), winerror=getattr(error, 'winerror', None), exited=exited)
                    if names.get(pid):
                        error_row['exe'] = names[pid]
                    errors.append(error_row)
            pending.extend((child, started) for child in children.get(pid, []))
        self.errors.extend(errors[:max(0, 16-len(self.errors))])
        self.excluded.extend(excluded[:max(0, 16-len(self.excluded))])
        memory = memory_status()
        self.samples += 1
        self.complete &= not incomplete
        self.peak_rss, self.peak_private = max(self.peak_rss, rss), max(self.peak_private, private)
        self.peak_resident = max(self.peak_resident, resident)
        # Neither GetProcessMemoryInfo counter measures held physical RAM.
        # Measured on an RTX 4080 Laptop: 7 GiB of VRAM moved private commit by
        # 7.01 GiB but the working set by 48 KiB, because WDDM charges GPU
        # allocations to PrivateUsage without residency; faulting in a 14.61 GiB
        # mapped model file moved the working set by the touched amount while
        # private commit stayed flat. A budget compared against either one
        # rejects requests that fit, and the working set grows with free RAM
        # because clean mapped pages are only reclaimed under pressure.
        # Private working set excludes mapped files and nonresident commit.
        # Commit exhaustion stays bounded by the system floor, where available
        # is min(physical RAM, commit headroom).
        guard = resident
        if not incomplete:
            self.peak_guard = max(self.peak_guard, guard)
        # GlobalMemoryStatusEx counts only free and standby pages, so resident
        # clean mapped pages are missing from availability. Linux MemAvailable
        # includes reclaimable page cache, which is why offloaded weights cost
        # nothing there. Offloaded weights are mapped read-only from the
        # prepared cache and can be dropped under pressure: measured a 21.85 GiB
        # working set holding 4.30 GiB of private bytes while availability read
        # 1.62 GiB, which stopped a request using 4.30 GiB against an 8.50 GiB
        # budget. Credit only this tree's own resident non-private pages, never
        # system-wide cache, and stay bounded by commit headroom and total RAM.
        reclaimable = max(0, rss - resident) if not incomplete else 0
        effective = min(memory['total_bytes'], memory['commit_available_bytes'],
                        memory['physical_available_bytes'] + reclaimable)
        for attribute, field in (('minimum_available', 'available_bytes'),
                                 ('minimum_physical', 'physical_available_bytes'),
                                 ('minimum_commit', 'commit_available_bytes')):
            old = getattr(self, attribute)
            setattr(self, attribute, memory[field] if old is None else min(old, memory[field]))
        self.minimum_effective = effective if self.minimum_effective is None else min(self.minimum_effective, effective)
        return {'rss_bytes': rss, 'pss_bytes': None, 'private_commit_bytes': private,
                'private_working_set_bytes': resident,
                'guard_bytes': guard if not incomplete else None,
                'guard_metric': 'tree private working set',
                'processes': count, 'unreadable_memory_pids': incomplete,
                'memory_read_errors': errors, 'excluded_processes': excluded,
                'reclaimable_mapped_bytes': reclaimable,
                'effective_available_bytes': effective,
                'system_available_bytes': memory['available_bytes'],
                'system_physical_available_bytes': memory['physical_available_bytes'],
                'system_commit_available_bytes': memory['commit_available_bytes']}

    def result(self):
        return {'process_tree_peak_rss_bytes': self.peak_rss, 'process_tree_peak_pss_bytes': None,
                'process_tree_pss_complete': False, 'process_tree_peak_private_commit_bytes': self.peak_private,
                'process_tree_peak_private_working_set_bytes': self.peak_resident,
                'process_tree_peak_guard_bytes': self.peak_guard, 'process_tree_guard_complete': self.complete,
                'process_tree_memory_errors': list(self.errors),
                'process_tree_excluded_processes': list(self.excluded),
                'ram_guard_metric': 'tree private working set',
                'system_min_available_bytes': self.minimum_available,
                'system_min_physical_available_bytes': self.minimum_physical,
                'system_min_commit_available_bytes': self.minimum_commit,
                'effective_min_available_bytes': self.minimum_effective,
                'ram_observation_samples': self.samples,
                'process_tree_disk_read_bytes': sum(v[0] for v in self.io.values()),
                'process_tree_disk_write_bytes': sum(v[1] for v in self.io.values()),
                'process_tree_io_complete': self.io_complete, 'process_tree_io_version': 3,
                'process_tree_io_scope': 'Windows per-process transfer counters, retained by PID/creation time; includes cached I/O, not physical disk traffic.',
                'ram_observation_scope': 'RAM guard is the summed private working set (ProcessVmCounters): resident private bytes, excluding mapped files and nonresident commit. PSS unavailable; this is not PSS and does not apportion shared pages. Working set and private commit are retained for diagnosis only and must not be compared against a RAM budget: the working set counts resident mapped file pages, which grow with free RAM, and WDDM charges GPU allocations to PrivateUsage without residency while per-PID VRAM attribution is unavailable. Effective availability adds back this tree own resident non-private pages, which are clean mapped weight pages the kernel can drop, because GlobalMemoryStatusEx counts only free and standby pages unlike Linux MemAvailable; it stays bounded by commit headroom and total RAM and never credits system-wide cache. Pagefile not added to physical RAM.'}


class JobBasicLimits(C.Structure):
    _fields_ = [('process_time', C.c_int64), ('job_time', C.c_int64), ('flags', DWORD),
                ('min_working_set', SIZE_T), ('max_working_set', SIZE_T), ('active_processes', DWORD),
                ('affinity', SIZE_T), ('priority', DWORD), ('scheduling', DWORD)]


class JobLimits(C.Structure):
    _fields_ = [('basic', JobBasicLimits), ('io', IoCounters), ('process_memory', SIZE_T),
                ('job_memory', SIZE_T), ('peak_process_memory', SIZE_T), ('peak_job_memory', SIZE_T)]


class Job:
    """Uninherited owner handle: controller death kills its entire worker tree."""
    def __init__(self):
        self.handle = checked(kernel32().CreateJobObjectW(None, None))
        limits = JobLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            checked(kernel32().SetInformationJobObject(self.handle, 9, C.byref(limits), C.sizeof(limits)))
        except BaseException:
            self.close()
            raise

    def assign(self, process_handle):
        checked(kernel32().AssignProcessToJobObject(self.handle, int(process_handle)))

    def kill(self):
        if self.handle:
            checked(kernel32().TerminateJobObject(self.handle, 1))

    def close(self):
        if self.handle:
            kernel32().CloseHandle(self.handle)
            self.handle = None
