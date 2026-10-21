"""Exclude setup, tests and GPU work; children retain their parent's lease.

GPU work is device-scoped, so it takes the machine lease shared and its own
device lease exclusively. Setup still takes the machine lease exclusively and
therefore still excludes every device. Without this split one measurement
occupied an entire multi-GPU host.
"""
from contextlib import contextmanager
import math
import os
from pathlib import Path
import re
import sys
import threading
import time


from .paths import data_root
from .system import windows
from .processes import inherited_fd
LOCK_PATH = Path(os.environ.get('FREEVIDEO_LOCK_PATH', data_root() / 'engine.lock'))
LOCK_ENV = 'FREEVIDEO_RUNTIME_LOCK_FD'
ENGINE_BUSY = 'Setup, testing or generation is already using this installation/GPU lock. Retry after it finishes.'


def lock_holders(paths, *, deadline=None):
    """Read-only Linux diagnostics, including orphans retaining inherited FDs."""
    if windows() or not Path('/proc/self/fd').is_dir():
        return []
    identities = set()
    for path in paths:
        if deadline is not None and time.monotonic() >= deadline:
            break
        try:
            item = Path(path).stat()
            identities.add((item.st_dev, item.st_ino))
        except OSError:
            pass
    holders = []
    if not identities:
        return []
    for directory in Path('/proc').iterdir():
        if deadline is not None and time.monotonic() >= deadline:
            break
        if not directory.name.isdigit():
            continue
        try:
            for fd in (directory / 'fd').iterdir():
                if deadline is not None and time.monotonic() >= deadline:
                    break
                try:
                    item = fd.stat()
                    if (item.st_dev, item.st_ino) in identities:
                        holders.append(int(directory.name))
                        break
                except OSError:
                    continue
        except OSError:
            continue
    return sorted(holders)


# Only these small, public command identifiers may leave process diagnostics.
_COMMAND_WORD = re.compile(r'[a-z][a-z0-9_.-]{0,40}\Z')
_EXECUTABLE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}\Z')
_HOLDER_TIMEOUT = 2.0


def _executable_basename(value):
    name = value.replace('\\', '/').rsplit('/', 1)[-1]
    return name if _EXECUTABLE.fullmatch(name) else 'unknown'


def _lock_command(cmdline):
    """Pure allowlist parser: no scripts, option values, prompts or environment."""
    arguments = cmdline.decode('utf-8', errors='replace').rstrip('\0').split('\0')
    executable = _executable_basename(arguments[0])
    index = 1
    # Do not mistake a script/-c argument or an option's value for Python's -m.
    while index < len(arguments) and arguments[index] in (
            '-B', '-u', '-E', '-s', '-S', '-I', '-O', '-OO', '-q', '-P'):
        index += 1
    words = []
    if index < len(arguments) and arguments[index] == '-m':
        for word in arguments[index + 1:index + 4]:
            if not _COMMAND_WORD.fullmatch(word):
                break  # Never skip an option/path to expose its value as a word.
            words.append(word)
    return executable, ' '.join(words)


def _holder_line(pid, executable, command='', seconds=None):
    line = f'Lock holder: pid {int(pid)}, {_executable_basename(executable)}'
    if command:
        line += f' ({command})'
    if seconds is not None and math.isfinite(seconds) and seconds >= 0:
        line += f', running {int(seconds)} s'
    return line


def _linux_holder(pid):
    executable, command, seconds = 'unknown', '', None
    try:
        with (Path('/proc') / str(pid) / 'cmdline').open('rb') as stream:
            executable, command = _lock_command(stream.read(65536))
    except Exception:
        pass
    try:
        # comm may itself contain spaces and parentheses; starttime is field 22.
        stat = (Path('/proc') / str(pid) / 'stat').read_text()
        ticks = int(stat.rsplit(')', 1)[1].split()[19])
        uptime = float(Path('/proc/uptime').read_text().split()[0])
        seconds = uptime - ticks / os.sysconf('SC_CLK_TCK')
    except Exception:
        pass
    return _holder_line(pid, executable, command, seconds)


def _windows_lock_holders(paths, deadline, lines):
    """Restart Manager is read-only here: never request a shutdown or restart."""
    import ctypes
    from ctypes import POINTER, c_int32, c_uint32, c_void_p, c_wchar, c_wchar_p

    class FileTime(ctypes.Structure):
        _fields_ = [('low', c_uint32), ('high', c_uint32)]

    class UniqueProcess(ctypes.Structure):
        _fields_ = [('pid', c_uint32), ('started', FileTime)]

    class ProcessInfo(ctypes.Structure):
        _fields_ = [('process', UniqueProcess), ('app', c_wchar * 256),
                    ('service', c_wchar * 64), ('kind', c_int32),
                    ('status', c_uint32), ('session', c_uint32), ('restartable', c_int32)]

    manager = ctypes.WinDLL('rstrtmgr.dll', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True)
    signatures = (
        (manager.RmStartSession, [POINTER(c_uint32), c_uint32, c_wchar_p], c_uint32),
        (manager.RmRegisterResources, [c_uint32, c_uint32, POINTER(c_wchar_p), c_uint32,
                                      POINTER(UniqueProcess), c_uint32, POINTER(c_wchar_p)], c_uint32),
        (manager.RmGetList, [c_uint32, POINTER(c_uint32), POINTER(c_uint32),
                           POINTER(ProcessInfo), POINTER(c_uint32)], c_uint32),
        (manager.RmEndSession, [c_uint32], c_uint32),
        (kernel.OpenProcess, [c_uint32, c_int32, c_uint32], c_void_p),
        (kernel.QueryFullProcessImageNameW, [c_void_p, c_uint32, c_wchar_p, POINTER(c_uint32)], c_int32),
        (kernel.CloseHandle, [c_void_p], c_int32),
    )
    for function, arguments, result in signatures:
        function.argtypes, function.restype = arguments, result
    session = c_uint32()
    if time.monotonic() >= deadline or manager.RmStartSession(ctypes.byref(session), 0, ctypes.create_unicode_buffer(33)):
        return
    try:
        resources = (c_wchar_p * len(paths))(*(str(Path(path).absolute()) for path in paths))
        if not resources or time.monotonic() >= deadline:
            return
        if manager.RmRegisterResources(session, len(resources), resources, 0, None, 0, None):
            return
        needed, count, reason = c_uint32(), c_uint32(), c_uint32()
        items = None
        # The list can grow between calls. Bound both allocation and retries.
        for _ in range(3):
            if time.monotonic() >= deadline:
                return
            status = manager.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), items, ctypes.byref(reason))
            if status == 0:
                break
            if status != 234 or not 0 < needed.value <= 4096:  # ERROR_MORE_DATA
                return
            count.value = needed.value
            items = (ProcessInfo * count.value)()
        else:
            return
        holders = {item.process.pid: item for item in (items or ())[:count.value]
                   if item.process.pid and item.process.pid != os.getpid()}
        for pid in sorted(holders)[:5]:
            if time.monotonic() >= deadline:
                return
            item = holders[pid]
            executable = _executable_basename(item.app)
            seconds = None
            try:
                ticks = (item.process.started.high << 32) | item.process.started.low
                if ticks:
                    seconds = time.time() - (ticks / 10_000_000 - 11_644_473_600)
            except Exception:
                pass
            # RM's app name is the fallback when the process has exited or access
            # is denied. QueryFullProcessImageNameW returns no command arguments.
            position = len(lines)
            lines.append(_holder_line(pid, executable, seconds=seconds))
            handle = None
            try:
                handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
                if handle and time.monotonic() < deadline:
                    image = ctypes.create_unicode_buffer(32768)
                    size = c_uint32(len(image))
                    if kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                        lines[position] = _holder_line(pid, image.value, seconds=seconds)
            except Exception:
                pass
            finally:
                if handle:
                    kernel.CloseHandle(handle)
        if len(holders) > 5:
            lines.append(f'Lock holder: and {len(holders) - 5} more')
    finally:
        manager.RmEndSession(session)


def describe_lock_holders(paths) -> list[str]:
    """Best-effort, redacted diagnostics; even a stalled OS call gets two seconds.

    A daemon thread is deliberate: native calls have no timeout, and an attempt
    to cancel Restart Manager must not delay the busy exit or interpreter exit.
    Completed rows remain available if a later process/API query fails or hangs.
    """
    lines = []

    def query():
        try:
            if windows():
                _windows_lock_holders(paths, deadline, lines)
            else:
                holders = sorted(set(lock_holders(paths, deadline=deadline)) - {os.getpid()})
                for pid in holders[:5]:
                    if time.monotonic() >= deadline:
                        return
                    lines.append(_linux_holder(pid))
                if len(holders) > 5:
                    lines.append(f'Lock holder: and {len(holders) - 5} more')
        except Exception:
            pass
        finally:
            done.set()

    try:
        deadline = time.monotonic() + _HOLDER_TIMEOUT
        done = threading.Event()
        threading.Thread(target=query, daemon=True, name='lock-holder-diagnostics').start()
        done.wait(max(0, deadline - time.monotonic()))
    except Exception:
        pass
    return lines[:]


def device_lock_path(device, path=None):
    """The lease for one device, beside the machine lease it shares a root with."""
    base = Path(path or os.environ.get('FREEVIDEO_LOCK_PATH', data_root() / 'engine.lock'))
    token = re.sub(r'[^A-Za-z0-9-]', '_', str(device))[:64] or 'unknown'
    return base.with_name(base.stem + '-gpu-' + token + base.suffix)


@contextmanager
def runtime_lock(path=None, *, inherit=True, shared=False, wait_seconds=0):
    """Take a lease, optionally waiting a bounded time for acquisition only."""
    if not math.isfinite(wait_seconds) or wait_seconds < 0:
        raise ValueError('Runtime lock wait must be finite and nonnegative')
    path = Path(path or os.environ.get('FREEVIDEO_LOCK_PATH', data_root() / 'engine.lock'))
    path.parent.mkdir(parents=True, exist_ok=True)
    inherited = inherited_fd() if inherit else None
    if inherited is None:
        stream = path.open('a+b')
    else:
        descriptor = int(inherited)
        actual, expected = os.fstat(descriptor), path.stat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError('Inherited runtime lock descriptor does not match the GPU-work lock')
        stream = os.fdopen(os.dup(descriptor), 'a+b')
    try:
        # An inherited descriptor shares the parent's open-file description.
        # Close our descriptor on exit; do not unlock the parent's live work.
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                if windows():
                    if inherited is None:
                        import msvcrt
                        if path.stat().st_size == 0:
                            stream.write(b'\0')
                            stream.flush()
                        stream.seek(0)
                        try:
                            msvcrt.locking(stream.fileno(),
                                           msvcrt.LK_NBRLCK if shared else msvcrt.LK_NBLCK, 1)
                        except OSError as error:
                            raise BlockingIOError('Another process holds the installation lease') from error
                    # The parent holds the byte-range lock; its Job Object owns this
                    # worker tree. Closing a duplicated child handle must not unlock it.
                else:
                    import fcntl
                    fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(0.075, remaining))
                # A delayed wakeup must not start another attempt after budget.
                if time.monotonic() >= deadline:
                    raise
        # The body is outside the retry loop: a failed request must never rerun.
        yield stream.fileno()
    finally:
        # Releasing the lease must not discard completed work. Taking it still
        # fails loudly, but once the request has run, a close that fails has
        # nothing left to protect: the kernel drops the descriptor when this
        # process exits, and the lease is advisory, so the next holder waits on
        # the file rather than on this handle. Observed once in 120 concurrent
        # eight-GPU measurements on a WekaFS share, as OSError EBADFD raised by
        # the close itself after a full video had been sampled, decoded and
        # written; the exit code threw that video away.
        try:
            stream.close()
        except OSError as error:
            print('Could not release the runtime lease at %s: %s. The request is '
                  'unaffected; the descriptor is dropped when this process exits.'
                  % (path, error), file=sys.stderr, flush=True)
