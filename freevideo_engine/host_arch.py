"""The architecture of the computer itself, as opposed to the one this process runs as.

On Windows ARM the x64 launcher runs emulated, so platform.machine() and the process
environment name AMD64. IsWow64Process2 reports the native machine; computers without
it fall back to the PROCESSOR_ARCHITEW6432 / PROCESSOR_ARCHITECTURE variables.
"""
import os
import platform
import sys

IMAGE_FILE_MACHINE_I386 = 0x014C
IMAGE_FILE_MACHINE_ARM64 = 0xAA64
IMAGE_FILE_MACHINE_AMD64 = 0x8664

_MACHINES = {IMAGE_FILE_MACHINE_ARM64: 'arm64', IMAGE_FILE_MACHINE_AMD64: 'x64', IMAGE_FILE_MACHINE_I386: 'x86'}
_NAMES = {'ARM64': 'arm64', 'AMD64': 'x64', 'X86': 'x86', 'IA64': 'ia64'}


def _native_machine_code():
    """IMAGE_FILE_MACHINE_* of the physical computer, or None when IsWow64Process2 is unavailable."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    function = getattr(kernel32, 'IsWow64Process2', None)
    if function is None:
        return None
    function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_ushort), ctypes.POINTER(ctypes.c_ushort)]
    function.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    process, native = ctypes.c_ushort(0), ctypes.c_ushort(0)
    if not function(kernel32.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
        return None
    return native.value


def _own_machine():
    name = platform.machine().lower()
    return {'amd64': 'x64', 'x86_64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(name, name or 'unknown')


def detect():
    """{'native': 'arm64'|'x64'|..., 'emulated': bool, 'source': str}; never raises."""
    try:
        if not sys.platform.startswith('win'):
            return dict(native=_own_machine(), emulated=False, source='platform')
        own = _NAMES.get(os.environ.get('PROCESSOR_ARCHITECTURE', '').upper())
        try:
            code = _native_machine_code()
        except Exception:
            code = None
        if code is not None and code in _MACHINES:
            native = _MACHINES[code]
            return dict(native=native, emulated=bool(own and own != native), source='IsWow64Process2')
        raw = os.environ.get('PROCESSOR_ARCHITEW6432') or os.environ.get('PROCESSOR_ARCHITECTURE', '')
        native = _NAMES.get(raw.upper())
        if native:
            return dict(native=native, emulated=bool(os.environ.get('PROCESSOR_ARCHITEW6432')), source='environment')
    except Exception:
        pass
    return dict(native='unknown', emulated=False, source='unavailable')


def windows_arm():
    """True on a Windows computer whose native machine is ARM64, whatever this process runs as."""
    return sys.platform.startswith('win') and detect()['native'] == 'arm64'


def native_arm_process():
    """True when this process itself is a native ARM64 Windows process (not x64 running emulated)."""
    return sys.platform.startswith('win') and _own_machine() == 'arm64'


def platform_key():
    """'win-arm64' for a native ARM64 Windows process, else 'win-x64' on Windows; the key for per-platform download tables."""
    if sys.platform.startswith('win'):
        return 'win-arm64' if native_arm_process() else 'win-x64'
    return sys.platform
