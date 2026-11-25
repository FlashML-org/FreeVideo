"""Memory readings and a fixed-word reading of how a generation worker ended.

A worker that the system ends cannot report its own memory, so the process that
started it takes the readings: once when the request starts and once when the
exit is seen. Only numbers and fixed words leave this module, never text from
the worker, so a diagnostic report stays free of user data.
"""
import math
import os
import re
import tempfile

from .system import windows

# Exit codes of a Windows process that the system, not the program, ended.
_SYSTEM_STATUS = {
    0xC000012D: 'commit_limit',      # STATUS_COMMITMENT_LIMIT
    0xC0000017: 'no_memory',         # STATUS_NO_MEMORY
    0xC00000FD: 'stack_overflow',    # STATUS_STACK_OVERFLOW
}
_CRASH_PREFIXES = (0xC0000000, 0x80000000)
_FIELDS = ('physical_available_bytes', 'commit_available_bytes', 'commit_limit_bytes', 'total_bytes')
OWN_MEMORY_KEYS = ('own_private_working_set_bytes', 'own_processes', 'own_unreadable_processes',
                   'host_private_working_set_bytes', 'resident_private_working_set_bytes')


def _count(value):
    return value if type(value) is int and value >= 0 else None


def memory_sample(pid=None):
    """Free physical memory, commit headroom and the working set of one process.

    The process is this one unless a pid is given. A value the system does not
    report is None; the function never raises.
    """
    import os
    sample = dict.fromkeys(_FIELDS)
    sample.update(process_working_set_bytes=None, process_private_commit_bytes=None)
    try:
        from .system import system_memory
        sample.update({key: _count(value) for key, value in system_memory().items() if key in _FIELDS})
    except Exception:
        pass
    pid = os.getpid() if pid is None else pid
    try:
        if windows():
            from .win32 import process_memory
            row = process_memory(pid)
            if row and 'rss_bytes' in row:
                sample['process_working_set_bytes'] = _count(row['rss_bytes'])
                sample['process_private_commit_bytes'] = _count(row['private_commit_bytes'])
        else:
            # Linux has no private commit figure: statm's first column is the whole address
            # space, tens of GB for a CUDA process, so only the resident size is recorded.
            with open('/proc/%d/statm' % pid, encoding='ascii') as stream:
                resident = stream.read().split()[1]
            sample['process_working_set_bytes'] = int(resident) * os.sysconf('SC_PAGE_SIZE')
    except Exception:
        pass
    return {key: sample[key] for key in sample if sample[key] is not None}


def own_memory(pid=None):
    """Physical RAM held by this process tree and by the largest process above it (Windows).

    A refusal says how much memory the system had left; this says how much of
    what was taken is FreeVideo's. The numbers are private working sets, which
    exclude mapped files, so they can be compared with free physical RAM. The
    tree is the generation process and its children; the resident worker is
    started by the host and is added by the caller from its own status. The
    host figure is the largest of up to four ancestors (a launcher gate is
    small, the program that started it is not). Processes that cannot be read
    are counted, never taken as zero; if the process itself cannot be read
    nothing is returned. Other platforms give nothing; the function never raises.
    """
    if not windows():
        return {}
    pid = os.getpid() if pid is None else pid
    result = {}
    try:
        from .win32 import ProcessMemory, processes, process_memory
        reading = ProcessMemory().sample(pid)
        unreadable = reading.get('unreadable_memory_pids') or []
        if not reading.get('processes') or pid in unreadable:
            return {}
        result.update(own_private_working_set_bytes=reading['private_working_set_bytes'],
                      own_processes=reading['processes'])
        if unreadable:
            result['own_unreadable_processes'] = len(unreadable)
    except Exception:
        return {}
    try:
        parents = processes()
        seen, node, largest = {pid}, parents.get(pid), None
        for _ in range(4):
            if not node or node in seen:
                break
            seen.add(node)
            try:
                row = process_memory(node)
            except Exception:
                row = None
            if row and _count(row.get('private_working_set_bytes')) is not None:
                largest = max(largest or 0, row['private_working_set_bytes'])
            node = parents.get(node)
        if largest is not None:
            result['host_private_working_set_bytes'] = largest
    except Exception:
        pass
    return {key: value for key, value in result.items() if _count(value) is not None}


def exit_reading(code, *, requested_stop=False, system=None):
    """The exit code as given, a fixed-word class and whether the system probably ended it.

    requested_stop is true when this program asked the worker to stop: its own
    stop is never counted as the system ending it.
    """
    on_windows = windows() if system is None else system == 'Windows'
    result = dict(exit_code=code if type(code) is int else None)
    if type(code) is not int:
        result.update(exit_class='unknown', suspected_system_kill=False)
        return result
    unsigned = code & 0xFFFFFFFF
    if on_windows or code > 255:
        result['exit_code_unsigned'] = unsigned
    if code == 0:
        kind, suspected = 'clean', False
    elif requested_stop:
        kind, suspected = 'stopped_by_us', False
    elif on_windows or code > 255:
        if unsigned == 0xFFFFFFFF:
            # TerminateProcess(-1): our own stop ends workers with 1, so this came from outside.
            kind, suspected = 'terminated_externally', True
        elif unsigned in _SYSTEM_STATUS:
            kind, suspected = 'system_' + _SYSTEM_STATUS[unsigned], True
        elif unsigned == 0xC000013A:
            kind, suspected = 'interrupted', False
        elif on_windows and unsigned == 3:
            # The C runtime's abort(): a native library ended the process (PyTorch does this when the driver
            # reports an error where it cannot raise one). Python's crash handler may have left a stack.
            kind, suspected = 'abort', False
        elif unsigned & 0xF0000000 in _CRASH_PREFIXES:
            kind, suspected = 'native_crash', False
        else:
            kind, suspected = 'error_exit', False
    elif code < 0 or code > 128:
        # Python reports a signal as -N; a shell-style status is 128 + N.
        signal = -code if code < 0 else code - 128
        if signal in (9, 15):
            kind, suspected = 'killed_by_signal', True
        elif signal in (4, 6, 7, 8, 11):
            kind, suspected = 'native_crash', False
        else:
            kind, suspected = 'signal', False
    else:
        kind, suspected = 'error_exit', False
    result.update(exit_class=kind, suspected_system_kill=suspected)
    return result


CRASH_FILE_ENV = 'FREEVIDEO_CRASH_FILE'
_FRAME = re.compile(r'^\s+File "(?P<file>[^"]*)", line (?P<line>\d+) in (?P<name>[A-Za-z0-9_<>.]{1,80})\s*$')
_FRAME_OUT = re.compile(r'^[A-Za-z0-9_.-]{1,80}:\d{1,7} [A-Za-z0-9_<>.]{1,80}$')
MAX_FRAMES = 40


def new_crash_file():
    """An empty file the worker's crash handler writes the native crash's Python stack to."""
    handle, path = tempfile.mkstemp(prefix='freevideo-crash-', suffix='.txt')
    os.close(handle)
    return path


def read_crash_stack(path):
    """Frames of the crashed thread as 'file.py:line function': no folder, no other text."""
    try:
        with open(path, encoding='utf-8', errors='replace') as stream:
            text = stream.read(64 * 1024)
    except (OSError, TypeError):
        return []
    frames = []
    for line in text.splitlines():
        match = _FRAME.match(line)
        if match:
            name = re.split(r'[\\/]', match.group('file'))[-1]
            frames.append('%s:%s %s' % (name[-80:], match.group('line'), match.group('name')))
        if len(frames) >= MAX_FRAMES:
            break
    return [frame for frame in frames if _FRAME_OUT.match(frame)]


# What a worker's last words said about a failure, as fixed words: the text itself can hold a prompt or a folder.
STDERR_KINDS = tuple((name, re.compile(pattern, re.I)) for name, pattern in (
    ('cuda_out_of_memory', r'cuda error: out of memory|cudaerrormemoryallocation|cublas_status_alloc_failed|cudnn_status_alloc_failed'),
    ('allocator_out_of_memory', r'cuda out of memory|outofmemoryerror'),
    ('cuda_launch_timeout', r'launch timed out|launch timeout|cudaerrorlaunchtimeout'),
    ('cuda_illegal_address', r'illegal memory access|cudaerrorillegaladdress'),
    ('cuda_launch_failure', r'unspecified launch failure|device-side assert'),
    ('cuda_device_lost', r'device is lost|device lost|gpu is lost|device has been removed'),
    ('cuda_unknown_error', r'cuda error: unknown error|cudaerrorunknown'),
    ('pinned_memory', r'cudahostalloc|cudahostregister|pinned memory|pin_memory'),
    ('memory_error', r'\bMemoryError\b'),
    ('commit_limit', r'paging file is too small|not enough memory resources|os error 1455')))
_STDERR_WORDS = frozenset(name for name, _ in STDERR_KINDS)


def stderr_kinds(text):
    """The fixed words that name what ``text`` (a worker's last lines) complained about."""
    return [name for name, pattern in STDERR_KINDS if pattern.search(text or '')]


_SAMPLE_KEYS = _FIELDS + ('process_working_set_bytes', 'process_private_commit_bytes')
_CLASSES = frozenset({'clean', 'stopped_by_us', 'terminated_externally', 'interrupted', 'native_crash', 'abort',
                      'error_exit', 'killed_by_signal', 'signal', 'unknown'}
                     | {'system_' + name for name in _SYSTEM_STATUS.values()})


def clean_sample(value):
    value = value if isinstance(value, dict) else {}
    return {key: value[key] for key in _SAMPLE_KEYS if _count(value.get(key)) is not None}


def clean_reading(value):
    """Only the numbers and fixed words of an exit reading, for any report."""
    value = value if isinstance(value, dict) else {}
    result = {}
    for key in ('exit_code', 'exit_code_unsigned', 'earlier_exit_code'):
        if type(value.get(key)) is int and -2**63 <= value[key] < 2**64:
            result[key] = value[key]
    if value.get('exit_class') in _CLASSES:
        result['exit_class'] = value['exit_class']
    if type(value.get('suspected_system_kill')) is bool:
        result['suspected_system_kill'] = value['suspected_system_kill']
    memory = clean_sample(value.get('memory'))
    if memory:
        result['memory'] = memory
    kinds = value.get('stderr_kinds')
    if isinstance(kinds, list):
        words = [word for word in kinds[:16] if word in _STDERR_WORDS]
        if words:
            result['stderr_kinds'] = words
    stack = value.get('native_stack')
    if isinstance(stack, list):
        frames = [row for row in stack[:MAX_FRAMES] if isinstance(row, str) and _FRAME_OUT.match(row)]
        if frames:
            result['native_stack'] = frames
    return result


def _number(value):
    return (type(value) is int and value >= 0
            or type(value) is float and math.isfinite(value) and value >= 0)


MEMORY_DURING_STAGES = frozenset(('start', 'load', 'text', 'encode', 'denoise', 'decode'))

_DURING_REQUEST_COUNTS = frozenset((
    'min_physical_available_bytes',
    'min_commit_available_bytes',
    'peak_worker_private_commit_bytes',
    'peak_worker_working_set_bytes',
    'peak_private_commit_bytes',
    'peak_working_set_bytes',
))

_DURING_REQUEST_STAGES = frozenset((
    'min_physical_available_stage',
    'min_commit_available_stage',
    'peak_worker_private_commit_stage',
    'peak_worker_working_set_stage',
    'peak_private_commit_stage',
    'peak_working_set_stage',
))

_WORKER_SAMPLE_COUNTS = frozenset((
    'worker_private_commit_bytes',
    'worker_working_set_bytes',
    'private_commit_bytes',
    'working_set_bytes',
    'process_private_commit_bytes',
    'process_working_set_bytes',
))

_SAMPLE_ROW_COUNTS = _WORKER_SAMPLE_COUNTS | frozenset((
    'physical_available_bytes',
    'commit_available_bytes',
))


def clean_memory_during_request(value):
    """Whitelist cleaner for memory readings sampled during an image request.

    Rejects arbitrary text, unknown keys and invalid values. Keeps only bounded
    non-negative integers, finite seconds and fixed stage words.
    """
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in _DURING_REQUEST_COUNTS:
        if key in value and _count(value[key]) is not None:
            result[key] = value[key]
    for key in _DURING_REQUEST_STAGES:
        if value.get(key) in MEMORY_DURING_STAGES:
            result[key] = value[key]

    last = value.get('last_worker_reading')
    if isinstance(last, dict):
        cleaned_last = {}
        if 'seconds' in last and _number(last['seconds']):
            cleaned_last['seconds'] = round(float(last['seconds']), 1)
        if last.get('stage') in MEMORY_DURING_STAGES:
            cleaned_last['stage'] = last['stage']
        for key in _WORKER_SAMPLE_COUNTS:
            if key in last and _count(last[key]) is not None:
                cleaned_last[key] = last[key]
        if cleaned_last:
            result['last_worker_reading'] = cleaned_last

    samples = value.get('samples')
    if isinstance(samples, (list, tuple)):
        cleaned_samples = []
        for row in samples[-5:]:
            if isinstance(row, dict):
                cleaned_row = {}
                if 'seconds' in row and _number(row['seconds']):
                    cleaned_row['seconds'] = round(float(row['seconds']), 1)
                if row.get('stage') in MEMORY_DURING_STAGES:
                    cleaned_row['stage'] = row['stage']
                for key in _SAMPLE_ROW_COUNTS:
                    if key in row and _count(row[key]) is not None:
                        cleaned_row[key] = row[key]
                if cleaned_row:
                    cleaned_samples.append(cleaned_row)
        if cleaned_samples:
            result['samples'] = cleaned_samples

    return result
