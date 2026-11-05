"""Which other programs hold memory on the GPU, so the page can say what to close.

Bytes come from NVML; Windows (WDDM) usually reports none per process, so
there they come from the "GPU Process Memory" performance counters that Task
Manager shows. Those are summed over every adapter the process uses. FreeVideo's
own processes and the desktop's system components are left out: the user
cannot close them. Names are shown on the page only, never written to reports.
"""
import ctypes as C
import os
from pathlib import Path
import re
import sys

MiB = 2**20
# Smaller holders do not free enough to matter.
FLOOR = 128 * MiB
LIMIT = 3
# Desktop compositors and shells on Linux; Windows ones are found by location.
LINUX_DESKTOP = {'Xorg', 'Xwayland', 'gnome-shell', 'kwin_x11', 'kwin_wayland', 'plasmashell', 'cinnamon', 'mutter'}


def counter_bytes():
    """{pid: dedicated GPU bytes} from Windows performance counters, or None."""
    from ctypes import wintypes as W

    class Value(C.Structure):
        _fields_ = [('status', W.DWORD), ('value', C.c_longlong)]

    class Item(C.Structure):
        _fields_ = [('name', C.c_wchar_p), ('value', Value)]

    pdh = C.WinDLL('pdh')
    query, counter = C.c_void_p(), C.c_void_p()
    if pdh.PdhOpenQueryW(None, None, C.byref(query)):
        return None
    try:
        if pdh.PdhAddEnglishCounterW(query, r'\GPU Process Memory(*)\Dedicated Usage', None, C.byref(counter)):
            return None
        if pdh.PdhCollectQueryData(query):
            return None
        large = 0x400
        for _ in range(3):
            # Instances come and go between asking the size and reading them.
            size, count = W.DWORD(0), W.DWORD(0)
            pdh.PdhGetFormattedCounterArrayW(counter, large, C.byref(size), C.byref(count), None)
            buffer = C.create_string_buffer(size.value + 4096)
            size = W.DWORD(len(buffer))
            if pdh.PdhGetFormattedCounterArrayW(counter, large, C.byref(size), C.byref(count), buffer) == 0:
                break
        else:
            return None
        found = {}
        for item in C.cast(buffer, C.POINTER(Item))[:count.value]:
            match = re.match(r'pid_(\d+)_', item.name or '')
            if match and item.value.status == 0 and item.value.value > 0:
                pid = int(match.group(1))
                found[pid] = found.get(pid, 0) + item.value.value
        return found
    finally:
        pdh.PdhCloseQuery(query)


def description(path):
    """The executable's own product description (what Task Manager lists), or None."""
    from ctypes import wintypes as W
    version = C.WinDLL('version')
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    data = C.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, data):
        return None
    pointer, length = C.c_void_p(), W.UINT()
    if not version.VerQueryValueW(data, r'\VarFileInfo\Translation', C.byref(pointer), C.byref(length)) or length.value < 4:
        return None
    language, page = C.cast(pointer, C.POINTER(W.WORD * 2)).contents
    key = r'\StringFileInfo\%04x%04x\FileDescription' % (language, page)
    if not version.VerQueryValueW(data, key, C.byref(pointer), C.byref(length)) or length.value < 2:
        return None
    return C.wstring_at(pointer, length.value).rstrip('\0').strip() or None


def label(process):
    """The name the user knows the program by, or None for a system component."""
    import psutil
    try:
        name = process.name()
    except psutil.Error:
        return None
    if sys.platform != 'win32':
        return None if name in LINUX_DESKTOP else name
    try:
        path = process.exe()
    except psutil.Error:
        return None  # Protected system processes do not show their location.
    system = Path(os.environ.get('SystemRoot', r'C:\Windows')).resolve()
    if not path or Path(path).resolve().is_relative_to(system):
        return None
    try:
        return description(path) or Path(name).stem
    except OSError:
        return Path(name).stem


def holders(device):
    """[{'name', 'bytes'}] of other programs on the GPU, largest first; None when they cannot be listed.

    Programs of unknown size are left out: on Windows every desktop program
    draws on the GPU, and naming them all would not say what to close."""
    import psutil
    from .background_room import Gpu, family
    gpu = Gpu(device)
    try:
        listed = gpu.processes()
    finally:
        gpu.close()
    if listed is None:
        return None
    if sys.platform == 'win32' and all(size is None for size in listed.values()):
        try:
            measured = counter_bytes()
        except (OSError, AttributeError):
            measured = None
        if measured:
            listed = {pid: measured.get(pid) for pid in set(listed) | set(measured)}
    try:
        own = family(os.getpid())
    except OSError:
        own = {os.getpid()}
    found = {}
    for pid, size in listed.items():
        if pid in own or size is None:
            continue
        try:
            name = label(psutil.Process(pid))
        except psutil.Error:
            continue
        if name:
            # One program often runs as several processes; list it once.
            found[name] = found.get(name, 0) + size
    rows = sorted((dict(name=name, bytes=size) for name, size in found.items() if size >= FLOOR),
                  key=lambda row: -row['bytes'])
    return rows[:LIMIT]
