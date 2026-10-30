"""Installer filesystem paths; runtime aliases belong to a separate change.

Only absolute drive/UNC paths enter the Windows extended namespace. Keep the
ordinary spelling at configuration, UI and subprocess boundaries.
"""
import ntpath
import os
import re
from functools import wraps
from pathlib import Path

from .system import windows as is_windows

PREFIX = '\\\\?\\'
UNC_PREFIX = PREFIX + 'UNC\\'
# launch_view marks with this the children of a runtime that starts from its short folder.
VIEW_ENV = 'FREEVIDEO_RUNTIME_VIEW'
_system_setting = None  # What Windows said for this process before long_path_process changed it.


def long_path_process():
    """Have Windows accept ordinary paths beyond 259 characters from this process.

    Windows decides this once, when a process starts, from the program's manifest
    and the system setting, and keeps the answer in the process. A runtime deeper
    than the limit starts from a short folder (runtime_alias), but its processes
    read the real location back from settings and from resolving paths. With the
    answer changed those paths open like any other, in the libraries as well.
    Returns whether Windows now says so.
    """
    global _system_setting
    if not is_windows():
        return False
    try:
        import ctypes
        ntdll = ctypes.WinDLL('ntdll')
        ntdll.RtlAreLongPathsEnabled.argtypes = []
        ntdll.RtlAreLongPathsEnabled.restype = ctypes.c_ubyte  # BOOLEAN, not Win32 BOOL
        ntdll.RtlGetCurrentPeb.argtypes = []
        ntdll.RtlGetCurrentPeb.restype = ctypes.c_void_p
        if _system_setting is None:
            _system_setting = bool(ntdll.RtlAreLongPathsEnabled())
        # PEB.BitField, bit 7: IsLongPathAwareProcess (Windows 10 1607 on; unused before).
        field = ctypes.c_ubyte.from_address(ntdll.RtlGetCurrentPeb() + 3)
        field.value |= 0x80
        return bool(ntdll.RtlAreLongPathsEnabled())
    except (AttributeError, OSError, ValueError, TypeError):
        return False


def system_setting():
    """What Windows said before long_path_process; None in a process that never called it."""
    return _system_setting


def long_paths_unavailable():
    """In a process started from a short folder: whether its real paths still cannot be opened."""
    return is_windows() and bool(os.environ.get(VIEW_ENV)) and not long_path_process()


def libraries_through_the_view():
    """Load a library named by its real path under the runtime through the short folder.

    Windows loads no library from an ordinary path beyond 259 characters, whatever the process
    accepts elsewhere, and resolving a library's path before loading it, as some packages do,
    gives exactly that in a process long_path_process has marked. The short folder names the
    same file; any other name is passed on as given.
    """
    import ctypes
    from . import launch_view
    view = launch_view.current()
    start = ctypes.CDLL.__init__
    if view is None or getattr(start, 'through_view', False):
        return

    def __init__(self, name, *arguments, **options):
        start(self, launch_view.value(name, view), *arguments, **options)
    __init__.through_view = True
    ctypes.CDLL.__init__ = __init__


def start_process():
    """For the package's import: a process started from a runtime's short folder accepts its real paths."""
    if is_windows() and os.environ.get(VIEW_ENV):
        long_path_process()
        libraries_through_the_view()


def plain(path, *, windows=None):
    value = os.fspath(path)
    if not (is_windows() if windows is None else windows):
        return value
    if value[:len(UNC_PREFIX)].upper() == UNC_PREFIX.upper():
        return '\\\\' + value[len(UNC_PREFIX):]
    return value[len(PREFIX):] if value.startswith(PREFIX) else value


def extended(path, *, windows=None):
    value = os.fspath(path)
    if not (is_windows() if windows is None else windows):
        return value
    original = value
    explicit = value.startswith(PREFIX)
    value = plain(value, windows=True).replace('/', '\\')
    drive, tail = ntpath.splitdrive(value)
    unc = (drive.startswith('\\\\') and len(drive[2:].split('\\')) == 2
           and all(part and part not in ('.', '..') for part in drive[2:].split('\\')))
    disk = len(drive) == 2 and drive[0] in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz' and drive[1] == ':' and tail.startswith('\\')
    if value.startswith(('\\\\.\\', '\\\\?\\')) or '\0' in value or (explicit and not (unc or disk)):
        raise ValueError('An absolute drive or UNC path is required')
    # Prefixing disables Win32 spelling rules. Keep those inputs in their
    # original namespace, including drive-relative and other relative paths.
    devices = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$',
               *('COM' + n for n in '123456789¹²³'), *('LPT' + n for n in '123456789¹²³')}
    parts = value.split('\\')
    if not explicit and (not (unc or disk) or any(
            part not in ('', '.', '..') and (part.endswith(('.', ' ')) or
            part.split('.')[0].rstrip(' ').upper() in devices) for part in parts)):
        return original
    value = ntpath.normpath(value)
    return UNC_PREFIX + value[2:] if unc else PREFIX + value


def io_path(path, *, windows=None):
    """Normalize once at an I/O entry; descendants keep the extended prefix.

    Do not resolve an unprefixed Windows path: resolve itself accesses disk.
    Non-Windows callers retain pathlib's existing relative-path semantics.
    """
    path = Path(path)
    return Path(extended(path, windows=True)) if (is_windows() if windows is None else windows) else path


def resolved(path):
    return io_path(io_path(Path(path).absolute()).resolve())


def plain_data(value):
    """Configuration paths include absolute ledger keys as well as values."""
    if isinstance(value, str):
        return plain(value)
    if isinstance(value, dict):
        return {plain(k) if isinstance(k, str) else k: plain_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain_data(v) for v in value]
    if isinstance(value, tuple):
        return tuple(plain_data(v) for v in value)
    return value


def plain_text(value):
    """Also remove prefixes embedded in errors, including repr-escaped names."""
    value = str(value)
    if not is_windows():
        return value
    # Keep the UNC leading separators at the same escaping level, including
    # repr strings nested in JSON output. Only the namespace prefix changes.
    for escaping in (4, 2, 1):
        prefix = PREFIX.replace('\\', '\\' * escaping)
        unc = UNC_PREFIX.replace('\\', '\\' * escaping)
        replacement = '\\' * (2 * escaping)
        value = re.sub(re.escape(unc), lambda match: replacement, value, flags=re.IGNORECASE)
        value = value.replace(prefix, '')
    return value


def plain_error(error):
    """Best-effort, in-place Windows display cleanup; never replace the error.

    Exception subclasses may have read-only fields or arbitrary getters/setters.
    A failure to clean one field must not hide the original exception or prevent
    cleaning other fields and shared/cyclic cause/context chains.
    """
    def clean(value, active=None):
        if isinstance(value, str):
            return plain_text(value)
        if isinstance(value, Path):
            return type(value)(plain(value))
        if isinstance(value, (tuple, list)):
            active = set() if active is None else active
            if id(value) in active:
                return value
            active.add(id(value))
            try:
                items = [clean(item, active) for item in value]
                return tuple(items) if isinstance(value, tuple) else items
            finally:
                active.remove(id(value))
        return value

    try:
        if not is_windows():
            return error
        pending, seen = [error], set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            for name in ('args', 'filename', 'filename2', 'strerror', '__notes__'):
                try:
                    value = getattr(current, name)
                    cleaned = clean(value)
                    if cleaned is not value:
                        setattr(current, name, cleaned)
                except BaseException:
                    pass  # Even an unusual setter must not replace the error.
            for name in ('__cause__', '__context__'):
                try:
                    link = getattr(current, name)
                    if link is not None:
                        pending.append(link)
                except BaseException:
                    pass
    except BaseException:
        pass
    return error


def plain_errors(function):
    """Keep filesystem namespace spelling out of exceptions at I/O boundaries."""
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (OSError, ValueError, RuntimeError) as error:
            plain_error(error)
            raise
    return call


def path_chars(path):
    """Windows budgets count UTF-16 code units (a non-BMP character uses two)."""
    return len(str(path).encode('utf-16-le', errors='surrogatepass')) // 2


ABSOLUTE_PATH = re.compile(
    # The preceding character guard prevents URL slashes from being treated
    # as local paths while still matching a drive path after prose such as
    # ``at C:\\Users\\...`` or a quoted traceback location.  A slash followed by
    # whitespace is prose (``8.0 / SM80``), not the start of a path.
    # Consume explicit relative paths too, spaces included, so an interior
    # slash (after e.g. a hyphen) cannot subsequently be mistaken for an
    # absolute path. Only our own ``./setup.sh`` command stays as written.
    r'''(?<![A-Za-z0-9_:/])(?:(?P<command>\./setup\.sh(?![\w/\\-]|\.\w))|\.{1,2}/[^"'<>;\r\n,\)\]}]*|'''
    r'''(?:(?:[A-Za-z]:[\\/])|(?:\\\\[^\\/\s"'<>]+[\\/])|/(?![/\s]))[^"'<>;\r\n,\)\]}]*)''')


def path_placeholder(value):
    """``<PATH>/`` and the last component: no usernames, drives, shares or parent directories."""
    normalized = str(value).rstrip('\\/')
    name = re.split(r'[\\/]', normalized)[-1]
    return '<PATH>/' + name if name else '<PATH>'


def path_match(match):
    """Replacement for an ``ABSOLUTE_PATH`` match. Spaces, UNC shares and ``\\\\?\\`` prefixes count."""
    value = match.group(0)
    if match.group('command') is not None:
        return value
    # Text is often redacted again (a worker's log inside a report, the
    # launcher terminal).  The ``/name`` after an earlier ``<PATH>`` is that
    # pass's own output, unless it still has a directory in it.
    if match.string.endswith('<PATH>', 0, match.start()) and not re.search(r'[\\/]', value[1:].rstrip('\\/')):
        return value
    return path_placeholder(value)
