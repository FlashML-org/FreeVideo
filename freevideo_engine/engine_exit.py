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

from .exit_diagnostics import clean_reading, exit_reading, memory_sample
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


def build(code, log=None, *, now=None, system=None):
    record = dict(at=time.time() if now is None else now, code_text=code_text(code, system))
    record.update(exit_reading(code, system=system))
    # The computer's memory when the exit was seen; the readings of this process are not the engine's.
    record['memory'] = {key: value for key, value in memory_sample().items() if not key.startswith('process_')}
    record['log_tail'] = log_tail(log) if log else []
    return record


def record(engine, code, log=None, *, now=None, system=None):
    """Write the record of an exit and return it. A failed write loses the file, never the notice."""
    value = build(code, log, now=now, system=system)
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
    return result


def describe(value, zh):
    """The launcher's one sentence about a stopped engine, in the language of the window.

    The exit status is not in it: it is in the record, the logs and the diagnostic report.
    """
    clock = time.strftime('%H:%M', time.localtime(value['at']))
    if zh:
        return 'ComfyUI 于 %s 意外停止。请点击“重新连接”重新启动。' % clock
    return 'ComfyUI stopped unexpectedly at %s. Click "Connect again" to restart it.' % clock
