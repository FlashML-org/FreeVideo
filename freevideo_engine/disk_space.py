"""Free space as the system's file manager reports it, and the disks a user can pick.

On macOS, statfs (shutil.disk_usage) leaves out purgeable space: APFS local
snapshots, iCloud files kept in the cloud and caches the system removes on
demand. Finder adds it, and so does the volume's capacity for important
usage, which Apple recommends for downloads the user asked for. Volumes of
one APFS container share their space, so they are counted as one disk.
Other systems keep shutil.disk_usage, which matches Explorer and df.
"""
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time

_HIDDEN_MOUNTS = ('/System/Volumes/', '/private/var/vm', '/Library/Developer/CoreSimulator', '/Volumes/.timemachine')


def existing(path):
    path = Path(path).expanduser()
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


if sys.platform == 'darwin':
    import ctypes
    import ctypes.util

    MNT_RDONLY, MNT_LOCAL, MNT_DONTBROWSE = 0x1, 0x1000, 0x00100000

    class _StatFS(ctypes.Structure):
        # struct statfs with 64-bit inodes, the only layout on Apple Silicon.
        _fields_ = [('f_bsize', ctypes.c_uint32), ('f_iosize', ctypes.c_int32),
                    ('f_blocks', ctypes.c_uint64), ('f_bfree', ctypes.c_uint64),
                    ('f_bavail', ctypes.c_uint64), ('f_files', ctypes.c_uint64),
                    ('f_ffree', ctypes.c_uint64), ('f_fsid', ctypes.c_int32 * 2),
                    ('f_owner', ctypes.c_uint32), ('f_type', ctypes.c_uint32),
                    ('f_flags', ctypes.c_uint32), ('f_fssubtype', ctypes.c_uint32),
                    ('f_fstypename', ctypes.c_char * 16), ('f_mntonname', ctypes.c_char * 1024),
                    ('f_mntfromname', ctypes.c_char * 1024), ('f_flags_ext', ctypes.c_uint32),
                    ('f_reserved', ctypes.c_uint32 * 7)]

    _libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
    _statfs = getattr(_libc, 'statfs$INODE64' if hasattr(_libc, 'statfs$INODE64') else 'statfs')
    _statfs.argtypes = (ctypes.c_char_p, ctypes.POINTER(_StatFS))
    _getfsstat = getattr(_libc, 'getfsstat$INODE64' if hasattr(_libc, 'getfsstat$INODE64') else 'getfsstat')
    _getfsstat.argtypes = (ctypes.POINTER(_StatFS), ctypes.c_int, ctypes.c_int)

    _cf = None

    def _corefoundation():
        global _cf
        if _cf is None:
            cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
            void = ctypes.c_void_p
            cf.CFURLCreateFromFileSystemRepresentation.restype = void
            cf.CFURLCreateFromFileSystemRepresentation.argtypes = (void, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool)
            cf.CFURLCopyResourcePropertyForKey.restype = ctypes.c_bool
            cf.CFURLCopyResourcePropertyForKey.argtypes = (void, void, ctypes.POINTER(void), ctypes.POINTER(void))
            cf.CFNumberGetValue.restype = ctypes.c_bool
            cf.CFNumberGetValue.argtypes = (void, ctypes.c_int, void)
            cf.CFStringGetCString.restype = ctypes.c_bool
            cf.CFStringGetCString.argtypes = (void, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32)
            cf.CFGetTypeID.restype = ctypes.c_ulong
            cf.CFGetTypeID.argtypes = (void,)
            cf.CFNumberGetTypeID.restype = ctypes.c_ulong
            cf.CFStringGetTypeID.restype = ctypes.c_ulong
            cf.CFBooleanGetValue.restype = ctypes.c_bool
            cf.CFBooleanGetValue.argtypes = (void,)
            cf.CFBooleanGetTypeID.restype = ctypes.c_ulong
            cf.CFRelease.argtypes = (void,)
            _cf = cf
        return _cf

    def _resource(path, key):
        """One NSURL resource value of the volume holding path (number, string or bool)."""
        cf = _corefoundation()
        name = ctypes.c_void_p.in_dll(cf, key).value
        raw = os.fsencode(str(path))
        url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), True)
        if not url or not name:
            return None
        value, error = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            if not cf.CFURLCopyResourcePropertyForKey(url, name, ctypes.byref(value), ctypes.byref(error)):
                if error.value:
                    cf.CFRelease(error)
                return None
            if not value.value:
                return None
            try:
                kind = cf.CFGetTypeID(value)
                if kind == cf.CFNumberGetTypeID():
                    number = ctypes.c_int64()
                    return number.value if cf.CFNumberGetValue(value, 4, ctypes.byref(number)) else None
                if kind == cf.CFStringGetTypeID():
                    buffer = ctypes.create_string_buffer(1024)
                    return buffer.value.decode('utf-8') if cf.CFStringGetCString(value, buffer, 1024, 0x08000100) else None
                if kind == cf.CFBooleanGetTypeID():
                    return bool(cf.CFBooleanGetValue(value))
                return None
            finally:
                cf.CFRelease(value)
        finally:
            cf.CFRelease(url)

    def _stat(path):
        record = _StatFS()
        if _statfs(os.fsencode(str(path)), ctypes.byref(record)) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(path))
        return record

    def _describe(record, path=None, *, details=True):
        filesystem = record.f_fstypename.decode('utf-8', 'replace')
        mount = os.fsdecode(record.f_mntonname)
        device = os.fsdecode(record.f_mntfromname)
        free = record.f_bavail * record.f_bsize
        # APFS volumes named diskNsM (and their snapshots, diskNsMsK) share
        # the space of container diskN.
        container = re.match(r'/dev/(disk\d+)s\d+', device) if filesystem == 'apfs' else None
        key = 'apfs:' + container.group(1) if container else 'fs:%d:%d' % tuple(record.f_fsid)
        probe = path or mount
        important = None
        name = None
        if details:
            try:
                important = _resource(probe, 'kCFURLVolumeAvailableCapacityForImportantUsageKey')
                # The Data volume holds the home folder; Finder names it after the system volume.
                name = _resource('/' if mount == '/System/Volumes/Data' else probe, 'kCFURLVolumeLocalizedNameKey')
            except (OSError, ValueError, AttributeError):
                pass
        return dict(key=key, mount=mount, name=name or Path(mount).name or mount, filesystem=filesystem,
                    total_bytes=record.f_blocks * record.f_bsize, statfs_free_bytes=free,
                    important_free_bytes=important, free_bytes=max(free, important or 0),
                    read_only=bool(record.f_flags & MNT_RDONLY), local=bool(record.f_flags & MNT_LOCAL),
                    hidden=bool(record.f_flags & MNT_DONTBROWSE))

    def _volume(path):
        return _describe(_stat(path), path)

    def _mounts():
        count = _getfsstat(None, 0, 2)
        if count <= 0:
            return []
        records = (_StatFS * (count + 8))()
        count = _getfsstat(records, ctypes.sizeof(records), 2)  # MNT_NOWAIT
        return list(records[:max(0, count)])
else:
    def _volume(path):
        usage = shutil.disk_usage(path)
        return dict(key='fs:%d' % os.stat(path).st_dev, mount=None, name=None, filesystem=None,
                    total_bytes=usage.total, statfs_free_bytes=usage.free, important_free_bytes=None,
                    free_bytes=usage.free, read_only=False, local=True, hidden=False)

    def _mounts():
        return []


def _disconnected(path):
    """The name of the external disk path points to when that disk is not mounted, or ''.

    Writing there would create /Volumes/<name> on the startup disk instead.
    """
    parts = Path(path).expanduser().parts
    if sys.platform != 'darwin' or len(parts) < 3 or parts[:2] != ('/', 'Volumes'):
        return ''
    return '' if os.path.ismount(os.path.realpath(os.path.join('/Volumes', parts[2]))) else parts[2]


def volume(path):
    """The disk holding path (or its nearest existing folder)."""
    folder = existing(path)
    value = dict(_volume(folder), path=str(folder))
    missing = _disconnected(path)
    if missing:
        value.update(name=missing, disconnected=True)
    return value


def free_bytes(path):
    """Bytes that can be written at path, counting space the system frees on demand."""
    if sys.platform != 'darwin':
        return shutil.disk_usage(existing(path)).free
    try:
        return volume(path)['free_bytes']
    except (OSError, ValueError):
        return shutil.disk_usage(existing(path)).free


def disk_key(path):
    """Paths with the same key draw on the same free space."""
    path = existing(path)
    if sys.platform != 'darwin':
        return path.stat().st_dev
    try:
        return _describe(_stat(path), str(path), details=False)['key']
    except (OSError, ValueError):
        return path.stat().st_dev


def install_problem(disk):
    """Why this disk cannot hold an installation ('disconnected', 'read-only', 'fat32' or 'exfat'), or ''."""
    if disk.get('disconnected'):
        return 'disconnected'
    if disk['read_only']:
        return 'read-only'
    # FAT32 stores files up to 4 GB; the text encoder alone is a 15.7 GB file.
    if disk['filesystem'] == 'msdos':
        return 'fat32'
    # exFAT keeps no extended attributes, so macOS writes a "._" file beside every
    # file that has one; uv cannot install Python there, and the "._" twins of
    # package metadata break importlib.metadata in the environment.
    if disk['filesystem'] == 'exfat':
        return 'exfat'
    return ''


def problem(path):
    try:
        return install_problem(volume(path))
    except (OSError, ValueError):
        return ''


def problem_messages(paths):
    """Plan errors for installation folders on disks that cannot hold one (filesystems are read on macOS)."""
    messages, seen = [], set()
    if sys.platform != 'darwin':
        return messages
    for path in paths:
        try:
            disk = volume(path)
        except (OSError, ValueError):
            continue
        issue = install_problem(disk)
        if not issue or disk['key'] in seen:
            continue
        seen.add(disk['key'])
        messages.append({
            'fat32': 'The installation folder is on a FAT32 disk, which stores files up to 4 GB; the text encoder '
                     'alone is 15.7 GB. Choose a folder on an APFS or Mac OS Extended disk: %s',
            'exfat': 'The installation folder is on an exFAT disk, where the Python environment cannot work '
                     '(macOS adds a hidden "._" copy of every file). Choose a folder on an APFS or Mac OS Extended disk: %s',
            'read-only': 'The installation folder is on a read-only disk. Choose a folder on another disk: %s',
            'disconnected': 'The installation folder is on a disk that is not connected. '
                            'Connect it, or choose another folder: %s'}[issue] % path)
    return messages


def frontend_gib(mac):
    """Space for a separate ComfyUI environment: 1.4 GB measured on a Mac, CUDA ones need far more."""
    return 2 if mac else 12


def disks():
    """Writable local disks a user could install to, one per shared space."""
    found = {}
    for record in _mounts():
        try:
            disk = _describe(record)
        except (OSError, ValueError):
            continue
        mount = disk['mount']
        system = mount == '/'
        if not system and (disk['read_only'] or not disk['local'] or disk['hidden']
                           or any(mount.startswith(prefix) for prefix in _HIDDEN_MOUNTS)):
            continue
        if system:
            # Folders in the home folder live on the Data volume of this container.
            try:
                data = volume(Path.home())
                disk = dict(disk, free_bytes=max(disk['free_bytes'], data['free_bytes']),
                            statfs_free_bytes=data['statfs_free_bytes'],
                            important_free_bytes=data['important_free_bytes'], read_only=False)
            except (OSError, ValueError):
                pass
        previous = found.get(disk['key'])
        if previous is None or system:
            found[disk['key']] = dict(disk, system=system)
    return sorted(found.values(), key=lambda d: (not d['system'], d['name'].lower()))


def size_text(n):
    """Sizes as the system's file manager shows them: decimal GB on macOS, GiB elsewhere."""
    n = max(0, int(n or 0))
    if sys.platform != 'darwin':
        return '%.1f GiB' % (n / 2**30)
    for unit, scale, digits in (('TB', 1e12, 2), ('GB', 1e9, 2), ('MB', 1e6, 1)):
        if n >= scale:
            # Finder rounds to two decimals and drops trailing zeros: 1.26 TB, 64.4 GB.
            return ('%.*f' % (digits, n / scale)).rstrip('0').rstrip('.') + ' ' + unit
    return '%d KB' % round(n / 1e3)


class Cache:
    """Look up disks off the interface thread; the important-usage query can take a second."""

    def __init__(self, ttl=15.):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.values = {}
        self.pending = set()

    def get(self, key, compute):
        now = time.monotonic()
        with self.lock:
            value = self.values.get(key)
            if value and now - value[0] < self.ttl or key in self.pending:
                return value[1] if value else None
            self.pending.add(key)

        def work():
            try:
                result = compute()
            except Exception:
                result = None
            with self.lock:
                self.values[key] = (time.monotonic(), result)
                self.pending.discard(key)
        threading.Thread(target=work, daemon=True, name='freevideo-disk-space').start()
        return value[1] if value else None


_cache = Cache()


def location(destination, *, need_bytes=0, suggest=True):
    """The disk under an installation folder and the other disks, for the launcher.

    Only macOS lists disks; elsewhere this returns None and the page is unchanged.
    The values come from a background lookup, so the first call can return None.
    """
    if sys.platform != 'darwin' or not str(destination or '').strip():
        return None
    try:
        folder = Path(destination).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    here = _cache.get(('volume', str(folder)), lambda: volume(folder))
    if not here:
        return None
    others = (_cache.get(('disks',), disks) or []) if suggest else []
    need = max(0, int(need_bytes or 0))
    choices = []
    for disk in others:
        if disk['key'] == here['key'] and not here.get('disconnected'):
            continue
        # A disk in a format FreeVideo cannot use is listed, unavailable, so its absence is not a puzzle.
        unusable = install_problem(disk)
        target = Path.home() / 'FreeVideo' if disk.get('system') else Path(disk['mount']) / 'FreeVideo'
        choices.append(dict(name=disk['name'], free_bytes=disk['free_bytes'], free=size_text(disk['free_bytes']),
                            path=str(target), problem=unusable,
                            enough=not unusable and (not need or disk['free_bytes'] >= need)))
    choices.sort(key=lambda d: (bool(d['problem']), -d['free_bytes']))
    issue = install_problem(here)
    return dict(name=here['name'], free_bytes=here['free_bytes'], free=size_text(here['free_bytes']),
                need_bytes=need, need=size_text(need) if need else '', short=bool(need and here['free_bytes'] < need),
                problem=issue, others=choices[:8])
