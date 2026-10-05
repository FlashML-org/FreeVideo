"""Native Mach/sysctl memory observations, usable before installing packages.

Apple's vm_statistics64.free_count already includes speculative pages. Available
memory is free + inactive (the same convention as psutil on macOS). Compressor
contents and swap are reported separately and never treated as extra RAM.
"""
import ctypes
from functools import lru_cache
import os
import sys


class VMStatistics(ctypes.Structure):
    # Stable prefix through HOST_VM_INFO64 revision 1. Query into a larger buffer
    # so later kernel revisions can append fields without overrunning our struct.
    _fields_ = [(name, ctypes.c_uint32) for name in
                ('free_count', 'active_count', 'inactive_count', 'wire_count')]
    _fields_ += [(name, ctypes.c_uint64) for name in
                 ('zero_fill_count', 'reactivations', 'pageins', 'pageouts', 'faults',
                  'cow_faults', 'lookups', 'hits', 'purges')]
    _fields_ += [(name, ctypes.c_uint32) for name in ('purgeable_count', 'speculative_count')]
    _fields_ += [(name, ctypes.c_uint64) for name in
                 ('decompressions', 'compressions', 'swapins', 'swapouts')]
    _fields_ += [(name, ctypes.c_uint32) for name in
                 ('compressor_page_count', 'throttled_count', 'external_page_count', 'internal_page_count')]
    _fields_ += [('total_uncompressed_pages_in_compressor', ctypes.c_uint64)]


class SwapUsage(ctypes.Structure):
    _fields_ = [('total', ctypes.c_uint64), ('available', ctypes.c_uint64),
                ('used', ctypes.c_uint64), ('page_size', ctypes.c_uint32),
                ('encrypted', ctypes.c_int)]


class ResourceUsage(ctypes.Structure):
    """Public rusage_info_v0 ABI from Darwin sys/resource.h (96 bytes)."""
    _fields_ = [('uuid', ctypes.c_ubyte * 16)] + [(name, ctypes.c_uint64) for name in
        ('user_time', 'system_time', 'pkg_idle_wkups', 'interrupt_wkups', 'pageins',
         'wired_size', 'resident_size', 'phys_footprint', 'proc_start_abstime', 'proc_exit_abstime')]


@lru_cache(maxsize=1)
def _process_library():
    if sys.platform != 'darwin':
        raise RuntimeError('Native process footprint observations require macOS')
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.proc_pid_rusage.restype = ctypes.c_int
    return lib


def process_footprint(pid):
    """Kernel memory charge, including compressed and accounted IOKit memory.

    This is not RSS or extra GPU memory. Never sum it with Metal driver bytes.
    Read the stable v0 ABI rather than guessing the latest struct size.
    """
    if type(pid) is not int or pid <= 0:
        raise ValueError('A positive process ID is required')
    usage = ResourceUsage()
    if _process_library().proc_pid_rusage(pid, 0, ctypes.byref(usage)):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), 'proc_pid_rusage: ' + str(pid))
    return usage.phys_footprint


@lru_cache(maxsize=1)
def _library():
    if sys.platform != 'darwin':
        raise RuntimeError('Mach memory observations require macOS')
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    lib.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    lib.sysctlbyname.restype = ctypes.c_int
    lib.mach_host_self.argtypes = []
    lib.mach_host_self.restype = ctypes.c_uint32
    lib.host_statistics64.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_uint32)]
    lib.host_statistics64.restype = ctypes.c_int
    lib.mach_port_deallocate.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    lib.mach_port_deallocate.restype = ctypes.c_int
    return lib


def _sysctl(name, value):
    size = ctypes.c_size_t(ctypes.sizeof(value))
    if _library().sysctlbyname(name.encode('ascii'), ctypes.byref(value), ctypes.byref(size), None, 0):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), name)
    if size.value != ctypes.sizeof(value):
        raise RuntimeError('Unexpected sysctl field size: ' + name)
    return value


def chip_name():
    lib = _library()
    size = ctypes.c_size_t(256)
    buffer = ctypes.create_string_buffer(size.value)
    if lib.sysctlbyname(b'machdep.cpu.brand_string', buffer, ctypes.byref(size), None, 0):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), 'machdep.cpu.brand_string')
    return buffer.value.decode('utf-8')


def summarize(total, page_size, stats, swap=None):
    if type(total) is not int or total <= 0 or type(page_size) is not int or page_size <= 0:
        raise ValueError('Mac memory requires positive physical capacity and page size')
    available = min(total, (stats.free_count + stats.inactive_count) * page_size)
    return dict(total_bytes=total, available_bytes=available, physical_available_bytes=available,
        commit_available_bytes=None, memory_model='unified', source='Mach host_statistics64',
        free_bytes=max(0, stats.free_count - stats.speculative_count) * page_size,
        inactive_bytes=stats.inactive_count * page_size, active_bytes=stats.active_count * page_size,
        wired_bytes=stats.wire_count * page_size,
        compressor_physical_bytes=stats.compressor_page_count * page_size,
        compressor_logical_bytes=stats.total_uncompressed_pages_in_compressor * page_size,
        swap_total_bytes=None if swap is None else swap.total,
        swap_free_bytes=None if swap is None else swap.available)


def memory_status():
    lib = _library()
    total = _sysctl('hw.memsize', ctypes.c_uint64()).value
    storage = (ctypes.c_uint32 * 1024)()
    count = ctypes.c_uint32(len(storage))
    host = lib.mach_host_self()
    if not host:
        raise RuntimeError('mach_host_self returned no host port')
    try:
        code = lib.host_statistics64(host, 4, storage, ctypes.byref(count))  # HOST_VM_INFO64
        if code:
            raise OSError('host_statistics64 failed: ' + str(code))
        if count.value * ctypes.sizeof(ctypes.c_uint32) < ctypes.sizeof(VMStatistics):
            raise RuntimeError('Incomplete Mach memory counters')
        stats = VMStatistics.from_buffer_copy(storage)
    finally:
        task = ctypes.c_uint32.in_dll(lib, 'mach_task_self_').value
        lib.mach_port_deallocate(task, host)
    try:
        swap = _sysctl('vm.swapusage', SwapUsage())
    except OSError:
        swap = None
    return summarize(total, os.sysconf('SC_PAGE_SIZE'), stats, swap)
