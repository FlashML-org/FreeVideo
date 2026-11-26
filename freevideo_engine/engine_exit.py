"""The last time the ComfyUI that the launcher started ended while it was open.

The launcher notices the exit, writes this record into the installation's
launcher folder and tells the user. The next ComfyUI reads the record back, so
the page, a feedback message and a diagnostic report can say why the earlier
one stopped. The file keeps the last lines of the engine log for the user's own
machine; everything that leaves it (`public`) is numbers and fixed words only.
"""
import json
import math
import re
import signal
import time
from pathlib import Path

from .exit_diagnostics import clean_reading, exit_reading, memory_sample, stderr_kinds
from .long_paths import io_path
from .monitoring import save
from .system import windows

FILE = 'last-exit.json'
LOG_LINES = 20
LOG_BYTES = 16 * 1024
LINE_CHARS = 300
# A Windows status in hex, a signal name or a plain exit status: nothing else is let through.
_CODE_TEXT = re.compile(r'^(?:0x[0-9A-F]{8}|SIG[A-Z0-9]{2,10}|[0-9]{1,3})$')


def location(engine):
    return io_path(engine) / 'launcher' / FILE


def signal_name(number):
    try:
        return signal.Signals(number).name
    except ValueError:
        return 'SIG%d' % number


def code_text(code, system=None):
    """The exit status as a person reads it: 0xC0000005 on Windows, SIGKILL for a signal, else the number."""
    if type(code) is not int:
        return ''
    on_windows = windows() if system is None else system == 'Windows'
    if on_windows or code > 255:
        return '0x%08X' % (code & 0xFFFFFFFF)
    if code < 0:
        return signal_name(-code)
    if code > 128:
        return signal_name(code - 128)
    return str(code)


def by_the_user(code, system=None):
    """An exit the user made: a normal exit, Ctrl+C or a closed console window (Windows 0xC000013A, SIGINT)."""
    if code == 0:
        return True
    return exit_reading(code, system=system)['exit_class'] == 'interrupted' or code_text(code, system) == 'SIGINT'


def log_tail(log, lines=LOG_LINES):
    """The last lines of the engine log, for this computer only; an unreadable log gives an empty list."""
    try:
        path = io_path(log)
        with path.open('rb') as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - LOG_BYTES))
            text = stream.read().decode('utf-8', errors='replace')
    except (OSError, TypeError, ValueError):
        return []
    rows = [row.rstrip()[:LINE_CHARS] for row in text.splitlines() if row.strip()]
    return rows[-lines:]


def oom_kills(path='/proc/vmstat', name='oom_kill'):
    """How many processes the Linux kernel has ended for want of memory since it started (oom_kill), or None elsewhere."""
    try:
        with open(path, encoding='ascii') as stream:
            for line in stream:
                key, _, value = line.partition(' ')
                if key == name:
                    return int(value)
    except (OSError, ValueError):
        pass
    return None


def cgroup_events(pid, *, proc='/proc', root='/sys/fs/cgroup'):
    """The memory.events file of the Linux cgroup (v2) a process runs in, or None (another system, cgroup v1, unreadable)."""
    try:
        with open(f'{proc}/{int(pid)}/cgroup', encoding='utf-8') as stream:
            lines = stream.read().splitlines()
    except (OSError, TypeError, ValueError):
        return None
    for line in lines:
        if line.startswith('0::/'):
            relative = line[3:].strip().lstrip('/')
            if '..' in relative.split('/'):
                return None
            return str(Path(root) / relative / 'memory.events') if relative else str(Path(root) / 'memory.events')
    return None


def cgroup_oom_kills(events):
    """How many processes of this cgroup and those under it the kernel ended for want of memory (memory.events oom_kill)."""
    return oom_kills(events, name='oom_kill') if events else None


def oom_baseline(pid, *, proc='/proc', root='/sys/fs/cgroup', vmstat='/proc/vmstat'):
    """The kernel's out-of-memory counts when ComfyUI started: its own cgroup's, which counts only that group of
    processes, and the whole computer's, for when the cgroup cannot be read at the exit."""
    events = cgroup_events(pid, proc=proc, root=root)
    return dict(cgroup=events, cgroup_count=cgroup_oom_kills(events), system=oom_kills(vmstat))


def oom_since(before, *, vmstat='/proc/vmstat', system_now=None):
    """(count, 'cgroup' | 'system') of out-of-memory kills since the baseline, or (None, None) when neither can be read.
    The cgroup count is preferred: the whole computer's also counts other programs the kernel ended."""
    if isinstance(before, dict):
        events, start = before.get('cgroup'), before.get('cgroup_count')
        now = cgroup_oom_kills(events) if type(start) is int else None
        if type(now) is int:
            return max(0, now - start), 'cgroup'
        before = before.get('system')
    after = oom_kills(vmstat) if system_now is None else system_now
    if type(before) is int and type(after) is int:
        return max(0, after - before), 'system'
    return None, None


def build(code, log=None, *, now=None, system=None, oom_before=None, oom_now=None):
    record = dict(at=time.time() if now is None else now, code_text=code_text(code, system))
    record.update(exit_reading(code, system=system))
    # The computer's memory when the exit was seen; the readings of this process are not the engine's.
    record['memory'] = {key: value for key, value in memory_sample().items() if not key.startswith('process_')}
    record['log_tail'] = log_tail(log) if log else []
    # The kernel's count of processes it ended for want of memory, against its count when this ComfyUI started: ComfyUI's
    # own cgroup's when it can be read (only that group of processes), else the whole computer's. A rise with SIGKILL
    # means the kernel most likely ended it for memory; with SIGTERM, systemd stopping the rest of the group after the
    # kernel ended one of its processes. The memory sample above was taken after the exit, when the memory was free again.
    count, scope = oom_since(oom_before, system_now=oom_now)
    if type(count) is int:
        record['oom_kills_since_start'] = count
        record['oom_scope'] = scope
    # What the engine's last lines complained about, as fixed words: the lines stay on this computer, the words may leave it.
    kinds = stderr_kinds('\n'.join(record['log_tail']))
    if kinds:
        record['stderr_kinds'] = kinds
    return record


def record(engine, code, log=None, *, now=None, system=None, oom_before=None):
    """Write the record of an exit and return it. A failed write loses the file, never the notice."""
    value = build(code, log, now=now, system=system, oom_before=oom_before)
    try:
        save(location(engine), value, plain_paths=False)
    except Exception:
        pass
    return value


def read(engine):
    """The stored record, or None when there is none or it cannot be read."""
    try:
        value = json.loads(location(engine).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def public(value):
    """What may leave this computer: the time, the status and the fixed words of exit_diagnostics; no log text."""
    if not isinstance(value, dict):
        return None
    at = value.get('at')
    if type(at) not in (int, float) or not math.isfinite(at) or at <= 0:
        return None
    result = dict(at=at)
    text = value.get('code_text')
    if isinstance(text, str) and _CODE_TEXT.match(text):
        result['code_text'] = text
    result.update(clean_reading(value))
    count = value.get('oom_kills_since_start')
    if type(count) is int and 0 <= count < 2**31:
        result['oom_kills_since_start'] = count
        if value.get('oom_scope') in ('cgroup', 'system'):
            result['oom_scope'] = value['oom_scope']
    return result


def out_of_memory(value):
    """The system ended it for want of memory: a Windows memory status, or SIGKILL while the kernel's out-of-memory count rose."""
    if not isinstance(value, dict):
        return False
    kind = value.get('exit_class')
    if kind in ('system_commit_limit', 'system_no_memory'):
        return True
    count = value.get('oom_kills_since_start')
    return kind == 'killed_by_signal' and value.get('code_text') == 'SIGKILL' and type(count) is int and count > 0


def maybe_out_of_memory(value):
    """SIGTERM while the kernel's out-of-memory count rose: under systemd, the rest of a group is stopped this way after
    the kernel ended one of its processes for memory. Said as "possibly", with the same next step."""
    if not isinstance(value, dict):
        return False
    count = value.get('oom_kills_since_start')
    # Only ComfyUI's own group's count: the whole computer's also rises for other programs, and a SIGTERM the user sent
    # after one of those must stay "ended" (dataroot-5x review, 11-25).
    return (value.get('exit_class') == 'killed_by_signal' and value.get('code_text') == 'SIGTERM'
            and value.get('oom_scope') == 'cgroup' and type(count) is int and count > 0)


def describe(value, zh):
    """The launcher's sentence about a stopped engine, in the language of the window: when, why as far as known, what next.

    The exit status itself is not in it: it is in the record, the logs and the diagnostic report.
    """
    clock = time.strftime('%H:%M', time.localtime(value['at']))
    kind = value.get('exit_class')
    memory = out_of_memory(value)
    maybe = not memory and maybe_out_of_memory(value)
    ended = kind in ('killed_by_signal', 'terminated_externally')
    crashed = kind in ('native_crash', 'abort', 'system_stack_overflow')
    if zh:
        cause = ('，原因是电脑内存不足' if memory else '，可能是因为电脑内存不足' if maybe
                 else '，被系统或其他程序结束' if ended else '，程序自身崩溃' if crashed else '')
        return ('FreeVideo 于 %s 意外停止%s。请点击“重新连接”重新启动；如再次停止，请导出报告，再点击“提交问题”并附上报告。' % (clock, cause))
    cause = (': the computer ran out of memory' if memory else ', possibly because the computer ran out of memory' if maybe
             else ': the system or another program ended it' if ended
             else ': it crashed' if crashed else '')
    return 'FreeVideo stopped unexpectedly at %s%s. Choose "Connect again" to restart it; if it stops again, export the report and choose "Report a problem".' % (clock, cause)
