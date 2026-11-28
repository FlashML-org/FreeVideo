"""Whether the Microsoft Visual C++ runtime that PyTorch needs on Windows loads in this process.

PyTorch's libraries link the C++ runtime (VCRUNTIME140.dll, MSVCP140.dll and a few more, which differ between x64 and
ARM64), and its wheels carry none of them: Windows has them from the Visual C++ Redistributable. Without them the
first ``import torch`` fails with Windows error 126 (a file is missing) or 1114 (a library did not initialize), and the
step's exit code is all a person sees. Before that import, setup loads them in a child process of the same Python
(require_in_child), so it finds them where torch would and the process that imports torch loads nothing early. The child
writes where each one came from to the step's log, and a library that does not load stops setup with MARKER, which the
launcher turns into the next step.
"""
import re
import sys
import sysconfig

MARKER = 'VC_RUNTIME_UNAVAILABLE'
CHILD_FAILED = 3  # The child's exit code when a library did not load.
# The runtime libraries the pinned PyTorch wheels import, per processor type: scripts/vc_runtime_imports.py reads them
# from the wheels and tests/test_vc_runtime.py compares. ARM64 has no VCRUNTIME140_1.dll: its PyTorch does not import it
# (and torch's own import skips it on ARM64), and the copies beside the ARM64 Python and in Microsoft's runtime packages
# are x64 files, which a native ARM64 process cannot load (Windows error 193).
LIBRARIES = {
    'x64': ('VCRUNTIME140.dll', 'VCRUNTIME140_1.dll', 'MSVCP140.dll', 'MSVCP140_ATOMIC_WAIT.dll'),
    'ARM64': ('VCRUNTIME140.dll', 'MSVCP140.dll', 'MSVCP140_ATOMIC_WAIT.dll', 'VCOMP140.dll'),
}
WRONG_PROCESSOR = 193  # Windows: the file found is not built for this processor type.
MACHINES = {0x8664: 'x64', 0xAA64: 'ARM64', 0x14C: 'x86', 0xA641: 'ARM64EC'}


def architecture(platform_tag=None):
    """The Redistributable that fits this Python: ARM64 for a native Arm build, x64 otherwise (an x64 Python emulated
    on Windows on Arm loads x64 libraries)."""
    return 'ARM64' if (platform_tag or sysconfig.get_platform()).lower().endswith('arm64') else 'x64'


def libraries(platform_tag=None):
    """The libraries PyTorch needs on this Python's build."""
    return LIBRARIES[architecture(platform_tag)]


class RuntimeUnavailable(RuntimeError):
    def __init__(self, arch, failures):
        self.arch, self.failures = arch, list(failures)
        super().__init__('The Microsoft Visual C++ runtime could not be loaded [%s %s]: %s' % (
            MARKER, arch, '; '.join('%s (Windows error %s)' % row for row in self.failures)))


def _module_path(handle):
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetModuleFileNameW.argtypes = (wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD)
    kernel32.GetModuleFileNameW.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    size = kernel32.GetModuleFileNameW(handle, buffer, len(buffer))
    return buffer.value[:size] if size else ''


def _file_version(path):
    import ctypes
    from ctypes import wintypes
    version = ctypes.WinDLL('version', use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p)
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wintypes.UINT))
    version.VerQueryValueW.restype = wintypes.BOOL
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return ''
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, data):
        return ''
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not version.VerQueryValueW(data, '\\', ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
        return ''
    # VS_FIXEDFILEINFO: signature, struct version, then dwFileVersionMS and dwFileVersionLS.
    fields = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 4)).contents
    high, low = fields[2], fields[3]
    return '%d.%d.%d.%d' % (high >> 16, high & 0xFFFF, low >> 16, low & 0xFFFF)


def _load(name):
    import ctypes
    handle = ctypes.WinDLL(name)._handle
    try:
        path = _module_path(handle)
        return path, _file_version(path) if path else ''
    except Exception:  # Only for the log: a library that loaded never stops setup over its path or version.
        return '', ''


def check(load=_load, platform_tag=None):
    """[(name, path, version)] of the libraries that loaded, and [(name, Windows error)] of those that did not."""
    loaded, failures = [], []
    for name in libraries(platform_tag):
        try:
            path, version = load(name)
        except OSError as error:
            code = getattr(error, 'winerror', None)
            if code is None and isinstance(error, FileNotFoundError):
                # ctypes reports Windows' "module not found" (126) as a FileNotFoundError without the code;
                # every other failure, such as 1114, keeps it.
                code = 126
            failures.append((name, code or error.errno or 'unknown'))
            continue
        loaded.append((name, path, version))
    return loaded, failures


def where(path, *, system_root=None, base=None):
    """Which folder a library came from, in one word that survives a report's path redaction: system, python or other."""
    import ntpath
    import os
    if not path:
        return 'unknown'
    folder = ntpath.normcase(ntpath.dirname(path))
    system = ntpath.join(system_root or os.environ.get('SystemRoot') or 'C:\\Windows', 'System32')
    python = ntpath.dirname(base or getattr(sys, '_base_executable', '') or sys.executable)
    return 'system' if folder == ntpath.normcase(system) else 'python' if folder == ntpath.normcase(python) else 'other'


def machine(path):
    """The processor type a PE file is built for ('x64', 'ARM64', ...), or '' when it cannot be read."""
    try:
        with open(path, 'rb') as file:
            head = file.read(4096)
        at = int.from_bytes(head[0x3C:0x40], 'little')
        if head[:2] != b'MZ' or head[at:at + 4] != b'PE\0\0':
            return ''
        code = int.from_bytes(head[at + 4:at + 6], 'little')
        return MACHINES.get(code, hex(code))
    except (OSError, ValueError):
        return ''


def found_elsewhere(name, *, system_root=None, base=None):
    """[(path, processor type)] of the copies of a library in the two folders Windows looks in first for this Python."""
    import ntpath
    import os
    folders = (ntpath.dirname(base or getattr(sys, '_base_executable', '') or sys.executable),
               ntpath.join(system_root or os.environ.get('SystemRoot') or 'C:\\Windows', 'System32'))
    paths = [ntpath.join(folder, name) for folder in folders if folder]
    return [(path, machine(path)) for path in paths if os.path.isfile(path)]


def require(*, windows=None, load=_load, out=None, platform_tag=None):
    """On Windows: write where each library came from, and raise RuntimeUnavailable when one does not load."""
    if not (sys.platform == 'win32' if windows is None else windows):
        return
    out = out or sys.stdout
    loaded, failures = check(load, platform_tag)
    for name, path, version in loaded:
        print('Visual C++ runtime: %s %s %s %s' % (name, version or 'version-unknown', where(path), path or 'path-unknown'),
              file=out, flush=True)
    for name, code in failures:
        if code == WRONG_PROCESSOR:
            # Which copy Windows refused, for the log: the card cannot tell a file for another processor from a missing one.
            for path, kind in found_elsewhere(name):
                print('Visual C++ runtime: %s for %s, this Python is %s: %s %s' % (
                    name, kind or 'unknown', architecture(platform_tag), where(path), path), file=out, flush=True)
    if failures:
        raise RuntimeUnavailable(architecture(platform_tag), failures)


def require_in_child(*, windows=None, run=None, out=None):
    """require() in a child process of this Python, for a process that imports torch next: it loads nothing itself.

    Only the child's own verdict stops setup. A child that could not run or check is written to the log, and setup goes
    on to torch's import, which then says what is wrong as before.
    """
    if not (sys.platform == 'win32' if windows is None else windows):
        return
    import subprocess
    out = out or sys.stdout
    options = dict(capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    if sys.platform == 'win32':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    try:
        result = (run or subprocess.run)([sys.executable, '-m', 'freevideo_engine.vc_runtime'], **options)
    except (OSError, subprocess.SubprocessError) as error:
        print('Visual C++ runtime check did not run: %r' % (error,), file=out, flush=True)
        return
    text = (result.stdout or '') + (result.stderr or '')
    if text.strip():
        print(text.rstrip(), file=out, flush=True)
    found = re.search(r'\[%s (x64|ARM64)\]: ([^\r\n]+)' % MARKER, text)
    if result.returncode == CHILD_FAILED and found:
        raise RuntimeUnavailable(found.group(1), re.findall(r'([\w.-]+\.dll) \(Windows error (\w+)\)', found.group(2)))
    if result.returncode:
        print('Visual C++ runtime check did not finish (exit code %s)' % result.returncode, file=out, flush=True)


if __name__ == '__main__':
    try:
        require()
    except RuntimeUnavailable as error:
        print(error, flush=True)
        sys.exit(CHILD_FAILED)
