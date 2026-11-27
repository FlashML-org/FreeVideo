"""Which module Windows names when a process FreeVideo started ends with a crash status (0xC0000005, 0xC0000409, ...).

Windows Error Reporting writes one "Application Error" entry (event 1000) to the Application log for such a process.
This reads that log only, through Windows' own wevtutil.exe (by its full path), which needs no administrator rights. It takes the entry of this run's
process: one of its process ids, or a process created after the start whose program is inside FreeVideo's own folders.
Nothing here raises: when the log cannot be read or holds no such entry, the result says so.
"""
import datetime
import ntpath
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ElementTree
from pathlib import PureWindowsPath

EVENT_NS = '{http://schemas.microsoft.com/win/2004/08/events/event}'
# The entry's fields in the order Windows writes them when they carry no names.
FIELDS = ('AppName', 'AppVersion', 'AppTimeStamp', 'ModuleName', 'ModuleVersion', 'ModuleTimeStamp', 'ExceptionCode',
          'FaultingOffset', 'ProcessId', 'ProcessCreationTime', 'AppPath', 'ModulePath')
# Ctrl+C or a closed console window: the user ended it, nothing crashed.
USER_ENDED = 0xC000013A
# Windows writes the entry a moment after the process ends.
WAIT_SECONDS = 8.
SAFE_NAME = re.compile(r'^[\w.+ -]{1,80}$')
SAFE_HEX = re.compile(r'^(?:0x)?[0-9a-fA-F]{1,16}$')
SAFE_VERSION = re.compile(r'^[\w.+ -]{1,40}$')


def native(code):
    """True for a Windows crash status (severity "error", 0xC...), except the user's own Ctrl+C."""
    if type(code) is not int:
        return False
    value = code & 0xFFFFFFFF
    return value & 0xC0000000 == 0xC0000000 and value != USER_ENDED


def _iso(at):
    return datetime.datetime.fromtimestamp(at, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def _filetime(text):
    """Seconds since 1970 from a FILETIME written in hex, or None."""
    try:
        value = int(text, 16)
    except (TypeError, ValueError):
        return None
    return value / 1e7 - 11644473600 if value > 0 else None


def entries(xml_text):
    """Every event in wevtutil's /f:xml output as {field: value}."""
    rows = []
    # wevtutil writes the events one after another with no enclosing element.
    for match in re.finditer(r'<Event\b.*?</Event>', xml_text or '', re.S):
        try:
            event = ElementTree.fromstring(match.group(0))
        except ElementTree.ParseError:
            continue
        data = event.find(EVENT_NS + 'EventData')
        values = [] if data is None else data.findall(EVENT_NS + 'Data')
        row = {}
        for index, item in enumerate(values):
            name = item.get('Name') or (FIELDS[index] if index < len(FIELDS) else None)
            if name:
                row[name] = (item.text or '').strip()
        rows.append(row)
    return rows


def _inside(path, roots):
    try:
        path = str(PureWindowsPath(path)).lower()
    except (TypeError, ValueError):
        return False
    for root in roots:
        try:
            base = str(PureWindowsPath(root)).lower().rstrip('\\') + '\\'
        except (TypeError, ValueError):
            continue
        if path.startswith(base) or path.startswith('\\\\?\\' + base):
            return True
    return False


def pick(rows, *, pids=(), since=0., roots=()):
    """The newest entry of this run's process: one of its ids, or created after `since` with its program in a root."""
    for row in rows:
        try:
            pid = int(row.get('ProcessId', ''), 16)
        except ValueError:
            pid = None
        if pid is not None and pid in set(pids):
            return row
        created = _filetime(row.get('ProcessCreationTime'))
        if created is not None and created >= since - 2 and roots and _inside(row.get('AppPath', ''), roots):
            return row
    return None


def wevtutil(env=None):
    """Windows' own wevtutil.exe by its full path, never one found through PATH."""
    env = os.environ if env is None else env
    return ntpath.join(env.get('SystemRoot') or env.get('WINDIR') or 'C:\\Windows', 'System32', 'wevtutil.exe')


def query(since, *, run=subprocess.run, env=None, timeout=10.):
    """The Application log's "Application Error" entries since `since`, newest first, as wevtutil's XML."""
    path = ("*[System[Provider[@Name='Application Error'] and (EventID=1000) and TimeCreated[@SystemTime>='%s']]]"
            % _iso(since - 5))
    options = dict(capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if os.name == 'nt':
        # Called from the launcher window: no console window flashes up.
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    result = run([wevtutil(env), 'qe', 'Application', '/q:' + path, '/f:xml', '/rd:true', '/c:20'], **options)
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout or 'wevtutil exit %s' % result.returncode).strip()[:200])
    return result.stdout


def read(since, *, pids=(), roots=(), run=subprocess.run, wait=WAIT_SECONDS, sleep=time.sleep, clock=time.monotonic,
         env=None):
    """{module, version, exception, offset, path} for this run's crash, or {unreadable: reason}. Never raises."""
    try:
        deadline = clock() + wait
        while True:
            # The whole read stays within the wait (and at least 2 s for one query), not a query's time on top of it.
            left = max(2., min(10., deadline - clock()))
            row = pick(entries(query(since, run=run, env=env, timeout=left)), pids=pids, since=since, roots=roots)
            if row is not None or clock() >= deadline:
                break
            sleep(1)
    except Exception as error:  # noqa: BLE001 - an unreadable log must not become the launcher's failure
        return dict(unreadable=type(error).__name__ + (': ' + str(error)[:160] if str(error) else ''))
    if row is None:
        return dict(unreadable='no Application Error entry for this process')
    return clean(row, roots)


def _relative(path, roots):
    """The path below the FreeVideo folder that holds it (which package shipped the module), or None."""
    try:
        full = PureWindowsPath(str(path).removeprefix('\\\\?\\'))
    except (TypeError, ValueError):
        return None
    for root in roots:
        try:
            return str(full.relative_to(PureWindowsPath(root)))
        except (TypeError, ValueError):
            continue
    return None


def clean(row, roots=()):
    """Only what may leave the computer: the module's own name, its version, the code and the offset; where the module
    is, below FreeVideo's own folder, or with the user's folder taken out elsewhere."""
    module = PureWindowsPath(row.get('ModuleName') or '').name
    found = dict(module=module if SAFE_NAME.match(module) else '')
    version = row.get('ModuleVersion', '')
    if SAFE_VERSION.match(version):
        found['version'] = version
    code = row.get('ExceptionCode', '')
    if SAFE_HEX.match(code):
        found['exception'] = '0x%08X' % (int(code, 16) & 0xFFFFFFFF)
    offset = row.get('FaultingOffset', '')
    if SAFE_HEX.match(offset):
        found['offset'] = '0x' + offset.lower().removeprefix('0x')
    path = row.get('ModulePath', '')
    inside = _relative(path, roots) if path else None
    if inside and all(SAFE_NAME.match(part) for part in PureWindowsPath(inside).parts):
        found['path'] = 'FreeVideo folder\\' + inside
    elif path:
        from .failure_details import Redactor
        found['path'] = Redactor().text(path)[:260]
    if not found['module']:
        return dict(unreadable='the entry names no module')
    return found


def line(found):
    """The line the report and the technical details carry."""
    if not isinstance(found, dict):
        return ''
    if found.get('unreadable') or not found.get('module'):
        return 'Faulting module: could not be read (%s)' % (found.get('unreadable') or 'no module')
    parts = [found['module'] + (' (version %s)' % found['version'] if found.get('version') else '')]
    if found.get('exception'):
        parts.append('exception ' + found['exception'])
    if found.get('offset'):
        parts.append('offset ' + found['offset'])
    text = 'Faulting module: ' + ', '.join(parts)
    if found.get('path'):
        text += '\nFaulting module path: ' + found['path']
    return text


def module_from(text):
    """The module name a "Faulting module:" line gives, or ''."""
    match = re.search(r'^Faulting module: (?!could not be read)([\w.+ -]{1,80}?)(?: \(version [^)\r\n]*\))?(?:,|$)',
                      text or '', re.M)
    return match.group(1).strip() if match else ''
