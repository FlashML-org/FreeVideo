"""Short Windows runtime roots; persistent configuration keeps the real paths.

The private backend is replaceable in tests. Selected roots are resolved using
extended I/O; alias descendants are translated only after authentication.
"""
import ctypes
from contextlib import contextmanager
import errno
from functools import lru_cache
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import stat
import struct

from .long_paths import extended, io_path, path_chars, plain, resolved
from .package_paths import ALIAS_ROOT_RESERVE, KNOWN_RUNTIME_PATH_CHARS, WINDOWS_PATH_LIMIT

RESERVE = 40
ONLINE_LOADED_PATH_CHARS = 100  # below the engine folder: envs\\unified\\Lib\\site-packages\\nvidia\\cudnn\\bin\\<dll>
PROGRAMS = ('.exe',)
LOADED = PROGRAMS + ('.dll', '.pyd')  # What Windows starts or loads, never from beyond the limit.
# The licence texts a package carries are a runtime's deepest files, and no program opens them.
LICENCES = '.dist-info/licenses/'
_NAME = re.compile(r'(?:[0-9a-f]{8}|[0-9a-f]{12}|[0-9a-f]{16})', re.IGNORECASE)


class AliasUnavailable(OSError):
    """An alias cannot be used; original_error and __cause__ retain the cause."""
    def __init__(self, reason, original_error=None):
        if reason not in ('location', 'filesystem', 'other', 'invalid'):
            raise ValueError('Unknown alias failure reason')
        self.reason = reason
        self.original_error = original_error
        super().__init__(getattr(original_error, 'errno', None),
                         'Runtime alias unavailable (%s): %s' % (reason, original_error or reason))


def _rtl_long_paths():
    from .long_paths import system_setting
    if system_setting() is not None:
        # This process changed its own answer (long_paths.long_path_process); the question
        # here is the system's, which every other program this one starts still gets.
        return system_setting()
    function = ctypes.WinDLL('ntdll').RtlAreLongPathsEnabled
    function.argtypes = []
    function.restype = ctypes.c_ubyte  # BOOLEAN, not Win32 BOOL
    return bool(function())


def _registry_long_paths():
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                        r'SYSTEM\CurrentControlSet\Control\FileSystem') as key:
        value, kind = winreg.QueryValueEx(key, 'LongPathsEnabled')
    return kind == winreg.REG_DWORD and value == 1


# Windows SDK: winnt.h, winbase.h, fileapi.h and winioctl.h. Fixed-width
# fields keep the layouts testable on hosts where C long is not a DWORD.
_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int32
_HANDLE = ctypes.c_void_p
_DIRECTORY = 0x10
_REPARSE = 0x400
_MOUNT_POINT = 0xA0000003
_READ_ATTRIBUTES = 0x80
_LIST_DIRECTORY = 0x1
_DELETE = 0x10000
_GENERIC_WRITE = 0x40000000
_SHARE_READ = 1
_OPEN_EXISTING = 3
_OPEN_REPARSE = 0x00200000
_BACKUP_SEMANTICS = 0x02000000
_GET_REPARSE = 0x000900A8
_SET_REPARSE = 0x000900A4
_MAX_REPARSE = 16384
_FILE_DISPOSITION_INFO = 4
_FILE_ID_INFO = 18
_FILE_FULL_DIRECTORY_INFO = 14
_FILE_FULL_DIRECTORY_RESTART_INFO = 15
_NO_MORE_FILES = 18


class _FileTime(ctypes.Structure):
    _fields_ = [('low', _DWORD), ('high', _DWORD)]


class _FileInformation(ctypes.Structure):
    _fields_ = [('attributes', _DWORD), ('creation', _FileTime),
                ('access', _FileTime), ('write', _FileTime), ('volume', _DWORD),
                ('size_high', _DWORD), ('size_low', _DWORD), ('links', _DWORD),
                ('id_high', _DWORD), ('id_low', _DWORD)]


class _FileId(ctypes.Structure):
    _fields_ = [('volume', ctypes.c_uint64), ('id', ctypes.c_ubyte * 16)]


class _Disposition(ctypes.Structure):
    _fields_ = [('DeleteFile', ctypes.c_ubyte)]  # BOOLEAN (one byte)


def _mount_buffer(root):
    target = plain(root, windows=True)
    drive, tail = ntpath.splitdrive(target)
    if (len(drive) != 2 or drive[1] != ':' or not tail.startswith('\\')
            or '\0' in target):
        raise AliasUnavailable('filesystem', OSError(errno.ENOTSUP, 'A local drive target is required'))
    substitute = ('\\??\\' + target).encode('utf-16-le')
    display = target.encode('utf-16-le')
    paths = substitute + b'\0\0' + display + b'\0\0'
    size = 8 + len(paths)  # mount fields, excluding the common 8-byte header
    if 8 + size > _MAX_REPARSE:
        raise AliasUnavailable('filesystem', OSError(errno.ENOTSUP, 'Mount point data exceeds 16 KB'))
    return struct.pack('<IHHHHHH', _MOUNT_POINT, size, 0, 0, len(substitute),
                       len(substitute) + 2, len(display)) + paths


def _mount_target(data):
    if len(data) < 16:
        raise OSError(errno.EIO, 'Truncated reparse buffer')
    tag, size, _, offset, length, print_offset, print_length = struct.unpack_from('<IHHHHHH', data)
    if tag != _MOUNT_POINT:
        raise OSError(errno.ENOTSUP, 'Not a mount point')
    if size < 8 or size + 8 > len(data) or size + 8 > _MAX_REPARSE:
        raise OSError(errno.EIO, 'Invalid reparse data length')
    for start, count in ((offset, length), (print_offset, print_length)):
        if start % 2 or count % 2 or start + count > size - 8:
            raise OSError(errno.EIO, 'Invalid mount point name range')
    try:
        target = data[16 + offset:16 + offset + length].decode('utf-16-le')
    except UnicodeError as error:
        raise OSError(errno.EIO, 'Invalid mount point encoding') from error
    if not target.startswith('\\??\\'):
        raise OSError(errno.ENOTSUP, 'Unsupported mount point namespace')
    target = target[4:]
    if target.startswith('UNC\\'):
        target = '\\\\' + target[4:]
    if '\0' in target or not ntpath.isabs(target):
        raise OSError(errno.EIO, 'Invalid mount point target')
    return target


class _WinAPI:
    """Lazy bindings: importing this module on Linux/Mac has no Windows effects."""
    def __init__(self):
        dll = ctypes.WinDLL('kernel32', use_last_error=True)
        declarations = {
            'GetACP': ([], _DWORD),
            'WideCharToMultiByte': ([_DWORD, _DWORD, ctypes.c_wchar_p, ctypes.c_int,
                                     ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                     ctypes.POINTER(_BOOL)], ctypes.c_int),
            'CreateDirectoryW': ([ctypes.c_wchar_p, ctypes.c_void_p], _BOOL),
            'CreateFileW': ([ctypes.c_wchar_p, _DWORD, _DWORD, ctypes.c_void_p,
                             _DWORD, _DWORD, _HANDLE], _HANDLE),
            'CloseHandle': ([_HANDLE], _BOOL),
            'DeviceIoControl': ([_HANDLE, _DWORD, ctypes.c_void_p, _DWORD,
                                 ctypes.c_void_p, _DWORD, ctypes.POINTER(_DWORD), ctypes.c_void_p], _BOOL),
            'GetFileInformationByHandle': ([_HANDLE, ctypes.POINTER(_FileInformation)], _BOOL),
            'GetFileInformationByHandleEx': ([_HANDLE, ctypes.c_int, ctypes.c_void_p, _DWORD], _BOOL),
            'SetFileInformationByHandle': ([_HANDLE, ctypes.c_int, ctypes.c_void_p, _DWORD], _BOOL),
            'CreateMutexW': ([ctypes.c_void_p, _BOOL, ctypes.c_wchar_p], _HANDLE),
            'WaitForSingleObject': ([_HANDLE, _DWORD], _DWORD),
            'ReleaseMutex': ([_HANDLE], _BOOL),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(dll, name)
            function.argtypes, function.restype = arguments, result
            setattr(self, name, function)

    def check(self, result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    def open(self, path, access=_READ_ATTRIBUTES, share=7, reparse=True):
        handle = self.CreateFileW(str(path), access, share, None, _OPEN_EXISTING,
                                  _BACKUP_SEMANTICS | (_OPEN_REPARSE if reparse else 0), None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def info(self, handle):
        info = _FileInformation()
        self.check(self.GetFileInformationByHandle(handle, ctypes.byref(info)))
        return info

    def identity(self, path, follow=True):
        handle = self.open(extended(path, windows=True), reparse=not follow)
        try:
            info = _FileId()
            self.check(self.GetFileInformationByHandleEx(handle, _FILE_ID_INFO,
                                                        ctypes.byref(info), ctypes.sizeof(info)))
            return info.volume, bytes(info.id)
        finally:
            self.CloseHandle(handle)

    def empty(self, handle):
        # FILE_FULL_DIR_INFO enumeration stays on the opened object, even if
        # its name was replaced. FileName starts at byte 68 (fixed SDK fields).
        header = struct.Struct('<II6qIII')
        kind = _FILE_FULL_DIRECTORY_RESTART_INFO
        while True:
            buffer = ctypes.create_string_buffer(16384)
            if not self.GetFileInformationByHandleEx(handle, kind, buffer, len(buffer)):
                error = ctypes.get_last_error()
                if error == _NO_MORE_FILES:
                    return True
                raise ctypes.WinError(error)
            offset = 0
            while True:
                if offset + header.size > len(buffer):
                    raise OSError(errno.EIO, 'Invalid directory information')
                fields = header.unpack_from(buffer.raw, offset)
                following, length = fields[0], fields[-2]
                end = offset + header.size + length
                if not length or length % 2 or end > len(buffer):
                    raise OSError(errno.EIO, 'Invalid directory filename')
                if buffer.raw[offset + header.size:end] not in (b'.\0', b'.\0.\0'):
                    return False
                if not following:
                    break
                if following % 8 or following < header.size + length:
                    raise OSError(errno.EIO, 'Invalid directory entry offset')
                offset += following
            kind = _FILE_FULL_DIRECTORY_INFO

    def target(self, handle):
        buffer = ctypes.create_string_buffer(_MAX_REPARSE)
        count = _DWORD()
        self.check(self.DeviceIoControl(handle, _GET_REPARSE, None, 0,
                                       buffer, len(buffer), ctypes.byref(count), None))
        return _mount_target(buffer.raw[:count.value])

    def dispose(self, handle):
        disposition = _Disposition(1)
        self.check(self.SetFileInformationByHandle(handle, _FILE_DISPOSITION_INFO,
                                                  ctypes.byref(disposition), ctypes.sizeof(disposition)))


@lru_cache(maxsize=1)
def _win_api():
    return _WinAPI()


class _Backend:
    """Windows effects only; tests substitute symlinks and temporary locations."""
    def windows(self):
        return os.name == 'nt'

    def long_paths(self):
        try:
            return _rtl_long_paths()
        except (AttributeError, OSError):
            try:
                return _registry_long_paths()
            except (ImportError, AttributeError, OSError, ValueError):
                return False

    def ansi_path(self, path):
        """Whether the system code page spells this path without substitution."""
        try:
            api = _win_api()
            code_page = api.GetACP()
            if code_page == 65001:
                return True
            substituted = _BOOL()
            # WC_NO_BEST_FIT_CHARS: even a similar character names a different file.
            api.check(api.WideCharToMultiByte(code_page, 0x400, str(path), -1,
                                            None, 0, None, ctypes.byref(substituted)))
            return not substituted.value
        except (AttributeError, OSError, ValueError, TypeError, ctypes.ArgumentError):
            # Not knowing is no reason for a short folder: the length decides, as it did.
            return True

    def locations(self):
        return [Path(plain(value, windows=True)) / 'FV' for key in ('ProgramData', 'LOCALAPPDATA')
                if (value := os.environ.get(key)) and Path(plain(value, windows=True)).is_absolute()]

    def io(self, path):
        return io_path(path)

    def mkdir(self, path):
        path.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def coordinate(self, root):
        # Kernel mutexes disappear with the last process handle; no lock files.
        api = _win_api()
        name = 'Local\\FreeVideo.RuntimeAlias.' + hashlib.sha1(_normal(root).encode('utf-8')).hexdigest()
        mutex = api.check(api.CreateMutexW(None, False, name))
        acquired = False
        try:
            result = api.WaitForSingleObject(mutex, 10000)
            acquired = result in (0, 0x80)  # WAIT_OBJECT_0 / WAIT_ABANDONED
            if not acquired:
                if result == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                raise OSError(errno.EBUSY, 'Runtime alias creation is busy')
            yield
        finally:
            if acquired:
                api.ReleaseMutex(mutex)
            api.CloseHandle(mutex)

    @contextmanager
    def parent(self, path):
        api = _win_api()
        # Do not follow FV. Deny writes/deletion while using its children, so
        # FV cannot be renamed or converted to a redirect after the check.
        # Attribute-only access is exempt from share checks. Include directory
        # read access so this handle really excludes other writers/deleters.
        handle = api.open(self.io(path), _READ_ATTRIBUTES | _LIST_DIRECTORY, share=_SHARE_READ)
        try:
            attributes = api.info(handle).attributes
            if not attributes & _DIRECTORY or attributes & _REPARSE:
                raise FileExistsError(errno.EEXIST, 'Alias parent is redirected or not a directory', str(path))
            yield
        finally:
            api.CloseHandle(handle)

    def validate_root(self, root):
        _mount_buffer(root)  # Unsupported UNC/oversize targets fail before I/O.
        api = _win_api()
        handle = api.open(extended(root, windows=True))
        try:
            attributes = api.info(handle).attributes
            if not attributes & _DIRECTORY or attributes & _REPARSE:
                raise OSError(errno.ENOTDIR, 'Runtime root must be an ordinary directory', str(root))
        finally:
            api.CloseHandle(handle)

    def create(self, root, alias):
        data = _mount_buffer(root)  # Reject UNC / >16 KB before creating anything.
        self.validate_root(root)
        api = _win_api()
        with self.parent(alias.parent):
            api.check(api.CreateDirectoryW(str(alias), None))
            try:
                handle = api.open(alias, _GENERIC_WRITE | _DELETE | _READ_ATTRIBUTES | _LIST_DIRECTORY,
                                  share=_SHARE_READ)
            except OSError as error:
                # CreateDirectoryW does not return an object handle. With no
                # held identity a path-based cleanup could delete a replacement.
                error.add_note('No alias handle was acquired; the placeholder was retained: ' + str(alias))
                raise
            ordinary = False
            try:
                attributes = api.info(handle).attributes
                if not attributes & _DIRECTORY or attributes & _REPARSE:
                    raise OSError(errno.EIO, 'New alias directory changed before opening')
                if not api.empty(handle):
                    raise OSError(errno.ENOTEMPTY, 'New alias directory is not empty')
                ordinary = True
                buffer = ctypes.create_string_buffer(data, len(data))
                count = _DWORD()
                api.check(api.DeviceIoControl(handle, _SET_REPARSE, buffer, len(data),
                                              None, 0, ctypes.byref(count), None))
                return handle  # Held through verification, including rollback.
            except OSError as error:
                try:
                    if ordinary:
                        self.remove_created(handle, root)
                except OSError as cleanup_error:
                    error.add_note('Could not remove the new directory: ' + str(cleanup_error))
                api.CloseHandle(handle)
                raise

    def readlink(self, path):
        api = _win_api()
        handle = api.open(path)
        try:
            return api.target(handle)
        finally:
            api.CloseHandle(handle)

    def target(self, handle):
        return _win_api().target(handle)

    def identity(self, path, follow=True):
        return _win_api().identity(path, follow)

    def close(self, handle):
        _win_api().CloseHandle(handle)

    def junction(self, path):
        info = os.lstat(path)
        # lstat reads the reparse point itself, including dangling junctions.
        return (getattr(info, 'st_reparse_tag', 0) == stat.IO_REPARSE_TAG_MOUNT_POINT
                or (getattr(info, 'st_reparse_tag', 0) == stat.IO_REPARSE_TAG_SYMLINK
                    and bool(getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_DIRECTORY)))

    def stat(self, path):
        return path.stat()

    def _delete_checked(self, handle, root, *, created=False):
        api = _win_api()
        attributes = api.info(handle).attributes
        if not attributes & _DIRECTORY:
            return False
        if attributes & _REPARSE:
            # target() rejects every tag except MOUNT_POINT. Check and dispose
            # use this same exclusive / non-delete-shared object handle.
            if not _same_target(api.target(handle), root):
                return False
        elif not created:
            return False
        api.dispose(handle)
        return True

    def remove_created(self, handle, root):
        # Only create()'s continuously held handle can authorize a plain
        # placeholder. There is deliberately no path-based rollback fallback.
        return self._delete_checked(handle, root, created=True)

    def remove(self, path, root):
        api = _win_api()
        with self.parent(path.parent):
            handle = api.open(path, _DELETE | _READ_ATTRIBUTES, share=0)
            try:
                return self._delete_checked(handle, root)
            finally:
                api.CloseHandle(handle)


_backend = _Backend()


@lru_cache(maxsize=1)
def system_long_paths() -> bool:
    return not _backend.windows() or _backend.long_paths()


def _ordinary(path):
    return Path(plain(path, windows=True))


def _normal(path):
    # Lexical normalization only; ntpath also makes Windows comparisons testable
    # on Linux. Absolute conversion never looks up the target on disk.
    value = os.path.abspath(plain(path, windows=True))
    return ntpath.normcase(ntpath.normpath(value)).rstrip('\\/').lower()


def _same_target(left, right):
    if _normal(left) == _normal(right):
        return True
    try:
        left_id, right_id = _backend.identity(_ordinary(left)), _backend.identity(_ordinary(right))
        return left_id is not None and left_id == right_id
    except (OSError, ValueError):
        return False  # No physical identity is never permission to reuse/delete.


def _same_object(left, right):
    """The same directory entry itself; a redirect is compared, never followed."""
    try:
        left_id, right_id = (_backend.identity(_ordinary(item), follow=False) for item in (left, right))
        return left_id is not None and left_id == right_id
    except (OSError, ValueError):
        return False


def _leads_to(directory, location):
    """Whether a directory may be the alias location reached some other way.

    A link straight to the location is followed, as file operations through it
    are. The location itself is not: the target of a redirected location stays
    ordinary. Without an identity only the folder's own name is left to go by.
    """
    try:
        there = _backend.identity(_ordinary(location), follow=False)
        here = _backend.identity(_ordinary(directory))
    except (FileNotFoundError, NotADirectoryError):
        return False  # One of them is not there: nothing leads from one into the other.
    except (OSError, ValueError):
        here = there = None
    if here is None or there is None:
        return directory.name.lower() == location.name.lower()
    return here == there


def _candidates(root, parent):
    identity = hashlib.sha1(_normal(root).encode('utf-8')).hexdigest()
    return [parent / identity[:length] for length in (8, 12, 16)]


@lru_cache(maxsize=32)
def _manifest_longest(path, mtime_ns, size, suffixes=()):
    try:
        value = json.loads(_backend.io(Path(path)).read_text(encoding='utf-8'))
        rows = value['files']
        if not isinstance(rows, list) or not rows:
            return KNOWN_RUNTIME_PATH_CHARS
        names = [row['path'] for row in rows]
        if any(not isinstance(name, str) or not name for name in names):
            return KNOWN_RUNTIME_PATH_CHARS
        if suffixes:
            names = [name for name in names if name.lower().endswith(suffixes)]
        else:
            names = [name for name in names if LICENCES not in name.replace('\\', '/').lower()]
        return max(map(path_chars, names), default=0)
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return KNOWN_RUNTIME_PATH_CHARS


def _longest(root, suffixes=()):
    """The longest path among the files a running installation opens; with suffixes, among those that end in one."""
    path = _backend.io(root / 'portable.json')
    try:
        info = path.stat()
    except OSError:
        return KNOWN_RUNTIME_PATH_CHARS
    return _manifest_longest(str(path), info.st_mtime_ns, info.st_size, suffixes)


def needs_view(root, longest=None, spelling_only=False) -> bool:
    """spelling_only: only a folder the system code page cannot spell, never one that is merely long."""
    if not _backend.windows():
        return False
    root = _ordinary(resolved(checked_real_path(root)))
    if os.environ.get('FREEVIDEO_RUNTIME_ALIAS') == '0':
        return False
    if os.environ.get('FREEVIDEO_RUNTIME_ALIAS') == '1':
        return True
    # The kernel compiler reads its cache folder and finds its own headers through the system's
    # ANSI code page. A folder that code page cannot spell is one it cannot use, whatever its
    # length; the short folder is plain letters.
    if not _backend.ansi_path(root):
        return True
    if spelling_only:
        return False
    # An installation that runs from where it is keeps doing so; only one beyond its budget is
    # given the short folder.
    if longest is None and system_long_paths():
        # With the system's long-path support on, files open at any length. A program or a
        # library beyond the limit still does not start or load: those decide, with no margin.
        return path_chars(root) + 1 + _longest(root, LOADED) > WINDOWS_PATH_LIMIT
    # With it off, every file the installation opens counts, and RESERVE for those it makes.
    longest = _longest(root) if longest is None else longest
    return path_chars(root) + 1 + longest + RESERVE > WINDOWS_PATH_LIMIT


def online_loaded_too_long(engine) -> bool:
    """An online installation whose deepest program or library is beyond what Windows loads.

    The loader ignores the system's long-path setting for a DLL such as torch\\lib\\c10.dll.
    """
    if not _backend.windows() or os.environ.get('FREEVIDEO_RUNTIME_ALIAS') == '0':
        return False
    engine = _ordinary(resolved(checked_real_path(engine)))
    return path_chars(engine) + 1 + ONLINE_LOADED_PATH_CHARS > WINDOWS_PATH_LIMIT


def is_junction(path) -> bool:
    """Any Windows directory junction or directory symlink, wherever it lives."""
    if not _backend.windows():
        return False
    try:
        return _backend.junction(_backend.io(_ordinary(path)))
    except OSError:
        return False


def _view_location(path):
    path = Path(os.path.abspath(_ordinary(path)))
    # Keep the input lexical: resolving it would erase an invalid alias boundary.
    # Ordinary names incur no filesystem calls. Check outer digest ancestors first.
    for candidate in reversed((path, *path.parents)):
        if not _NAME.fullmatch(candidate.name):
            continue
        parents = [_ordinary(parent).absolute() for parent in _backend.locations()]
        for parent in parents:
            if _normal(candidate.parent) == _normal(parent):
                return candidate, parent / candidate.name
        for parent in parents:
            try:
                # Both spellings must exist and themselves be ordinary directories.
                # Ancestor junctions, 8.3 names and subst may still name the same FV.
                with _backend.parent(candidate.parent), _backend.parent(parent):
                    if _same_target(candidate.parent, parent):
                        return candidate, parent / candidate.name
            except (OSError, ValueError):
                # A redirected FV is never trusted, and its target never becomes alias
                # space. Another spelling of that same redirect is still this location,
                # so the name goes on to authentication, which refuses it.
                if _same_object(candidate.parent, parent):
                    return candidate, parent / candidate.name
        if any(_leads_to(candidate.parent, parent) for parent in parents):
            # Not a spelling of the location, and not known to be elsewhere:
            # never translated, and never taken for an ordinary folder.
            return candidate, None
    return None, None


def _view_root(path):
    return _view_location(path)[0]


def is_view(path) -> bool:
    if not _backend.windows():
        return False
    root, target = _authenticated_view(path)
    return target is not None and _normal(path) == _normal(root)


def _authenticated_view(path):
    # Membership must precede target resolution. Lexical slots remain alias
    # inputs even when FV is redirected/unreadable or the occupant is ordinary.
    root, slot = _view_location(path)
    if root is None or slot is None:
        return root, None
    try:
        with _backend.parent(slot.parent):
            if not _backend.junction(_backend.io(slot)):
                return root, None
            # Native readlink opens the object and accepts only MOUNT_POINT.
            target = _ordinary(_backend.readlink(_backend.io(slot)))
            if not target.is_absolute():
                target = slot.parent / target
            _backend.validate_root(target)  # Exists, ordinary directory, no chain.
            candidates = _candidates(target, slot.parent)
            if not any(_normal(slot) == _normal(item) for item in candidates):
                # GetFinalPathName (via extended resolve) expands an 8.3 target.
                # Alternate spelling alone must never authorize a different ID.
                physical = _ordinary(resolved(target))
                if not _same_target(target, physical) or not any(
                        _normal(slot) == _normal(item) for item in _candidates(physical, slot.parent)):
                    return root, None
            return root, target
    except (OSError, ValueError):
        return root, None


def real_path(path) -> Path:
    if not _backend.windows():
        return Path(path)
    original = Path(path)
    absolute = Path(os.path.abspath(_ordinary(path)))
    root, target = _authenticated_view(absolute)
    if target is not None:
        return target / absolute.relative_to(root)
    return original


def checked_real_path(path) -> Path:
    """Translate authenticated aliases; reject occupants of invalid alias slots."""
    if not _backend.windows():
        return Path(path)
    root, target = _authenticated_view(path)
    if root is None:
        return Path(path)
    if target is None:
        raise AliasUnavailable('invalid')
    absolute = Path(os.path.abspath(_ordinary(path)))
    return target / absolute.relative_to(root)


def _matches(alias, root):
    if not is_junction(alias):
        return False
    target = _ordinary(_backend.readlink(_backend.io(alias)))
    if not target.is_absolute():
        target = alias.parent / target
    return _same_target(target, root)


def _reusable(alias, root):
    _, target = _authenticated_view(alias)
    return target is not None and _same_target(target, root)


def _probe(root):
    for name in ('portable.json', 'runtime.json', 'python/python.exe',
                 'python/bin/python', 'ComfyUI/main.py', 'engine/machine.json'):
        if _backend.io(root / name).is_file():
            return Path(name)
    with os.scandir(_backend.io(root)) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False):
                return Path(entry.name)
    raise FileNotFoundError(errno.ENOENT, 'No known runtime file to verify through the alias', str(root))


def _reason(error, default='other'):
    if getattr(error, 'winerror', None) in (1, 50, 87, 4390, 4392, 4393) or error.errno in (
            errno.ENOSYS, errno.ENOTSUP):
        return 'filesystem'
    if isinstance(error, (PermissionError, FileExistsError)) or error.errno in (
            errno.EACCES, errno.EPERM, errno.EROFS, errno.ENOSPC):
        return 'location'
    return default


def _verify(alias, root, probe, created):
    try:
        matches = (_same_target(_backend.target(created), root) if created is not None
                   else _matches(alias, root))
        if not matches:
            raise OSError(errno.EIO, 'Junction target changed during creation', str(alias))
        _backend.stat(alias / probe)  # Deliberately ordinary runtime spelling.
    except OSError as error:
        failure = AliasUnavailable(_reason(error), error)
        if created is not None:
            try:
                _backend.remove_created(created, root)
            except OSError as cleanup_error:
                failure.cleanup_error = cleanup_error
                failure.add_note('Could not remove the new junction: ' + str(cleanup_error))
        raise failure from error


def _starts_as_it_is(root):
    return (os.environ.get('FREEVIDEO_RUNTIME_ALIAS') != '1'
            and path_chars(root) + 1 + _longest(root, PROGRAMS) <= WINDOWS_PATH_LIMIT)


@lru_cache(maxsize=1)
def _reclaim_once():
    try:
        reclaim_views()
    except Exception:  # Tidying up must never stop a launch.
        pass


def runtime_view(root, longest=None) -> Path:
    if not _backend.windows():
        return Path(root)
    try:
        root = _ordinary(resolved(checked_real_path(root)))
        if not needs_view(root, longest):
            return root
        try:
            with _backend.coordinate(root):
                view = _runtime_view(root)
            _reclaim_once()
            return view
        except OSError:
            # No short folder can be made. A runtime whose programs start from where they are
            # runs as it did before there was a short folder; only one that cannot is refused.
            if longest is None and _starts_as_it_is(root):
                return root
            raise
    except AliasUnavailable:
        raise
    except OSError as error:
        raise AliasUnavailable(_reason(error), error) from error


def _runtime_view(root):
    try:
        _backend.validate_root(root)
        probe = _probe(root)
    except AliasUnavailable:
        raise
    except OSError as error:
        raise AliasUnavailable(_reason(error), error) from error
    last = OSError(errno.ENOENT, 'No alias location is configured')
    reason = 'location'
    for parent in _backend.locations():
        parent = _ordinary(parent)
        if is_junction(parent):
            last = FileExistsError(errno.EEXIST, 'Alias parent is redirected', str(parent))
            reason = 'location'
            continue
        for alias in _candidates(root, parent):
            if path_chars(alias) > ALIAS_ROOT_RESERVE:
                last = OSError(errno.ENAMETOOLONG, 'Alias root exceeds its reserved path budget', str(alias))
                reason = 'location'
                break
            created = None
            try:
                if not _backend.io(parent).exists():
                    _backend.mkdir(parent)
                with _backend.parent(parent):
                    # Pin FV before inspecting children, including reuse.
                    if _reusable(alias, root):
                        _verify(alias, root, probe, None)
                        return _ordinary(alias)
                    if os.path.lexists(_backend.io(alias)):
                        last = FileExistsError(errno.EEXIST, 'Alias name is occupied', str(alias))
                        reason = 'location'
                        continue
                    try:
                        created = _backend.create(root, alias)
                    except OSError:
                        # Another launcher may have won between lexists/create.
                        if not _reusable(alias, root):
                            raise
                    try:
                        _verify(alias, root, probe, created)
                        return _ordinary(alias)
                    finally:
                        if created is not None:
                            _backend.close(created)
            except AliasUnavailable:
                raise  # A failed verification is not a name/location collision.
            except OSError as error:
                last, reason = error, _reason(error)
                continue
    raise AliasUnavailable(reason, last) from last


def _orphan_target(alias):
    """The folder a short folder of ours points to, when that folder is gone."""
    with _backend.parent(alias.parent):
        io = _backend.io(alias)
        if not _backend.junction(io):
            return None
        target = _ordinary(_backend.readlink(io))
    if not target.is_absolute() or not any(_normal(alias) == _normal(item) for item in _candidates(target, alias.parent)):
        return None
    try:
        # Only a folder that is missing from a place that can be read counts as gone.
        if not _backend.io(target.parent).is_dir():
            return None
        _backend.io(target).lstat()
    except FileNotFoundError:
        return target
    except (OSError, ValueError):
        pass
    return None


def reclaim_views():
    """Remove short folders whose runtime folder no longer exists; returns how many."""
    if not _backend.windows():
        return 0
    removed = 0
    for parent in _backend.locations():
        parent = _ordinary(parent)
        try:
            if is_junction(parent):
                continue
            names = [name for name in os.listdir(_backend.io(parent)) if _NAME.fullmatch(name)]
        except (OSError, ValueError):
            continue
        for name in names:
            alias = parent / name
            try:
                target = _orphan_target(alias)
                if target is None:
                    continue
                # Look again under the lock that creating a view of this folder takes:
                # a reinstall may have brought the folder back since the first look.
                with _backend.coordinate(target):
                    if _orphan_target(alias) == target and _backend.remove(alias, target):
                        removed += 1
            except (OSError, ValueError):
                # Busy, unreadable or changed objects remain untouched.
                pass
    return removed


def drop_view(root):
    if not _backend.windows():
        return
    given = Path(os.path.abspath(real_path(_ordinary(root))))
    roots = [given]
    try:
        # runtime_view names the junction after the physical folder; the caller may
        # spell the same folder differently (an 8.3 name, a link of the user's own).
        physical = _ordinary(resolved(given))
        if _normal(physical) != _normal(given):
            roots.insert(0, physical)
    except (OSError, ValueError):
        pass  # The folder is gone; only the given spelling can be matched.
    for parent in _backend.locations():
        parent = _ordinary(parent)
        if is_junction(parent):
            continue
        for target in roots:
            for alias in _candidates(target, parent):
                try:
                    _backend.remove(alias, target)
                except OSError:
                    # Missing, busy or unreadable objects remain untouched.
                    pass
