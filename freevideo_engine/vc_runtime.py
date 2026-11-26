"""Whether the Microsoft Visual C++ runtime that PyTorch needs on Windows loads in this process.

PyTorch's core library (c10.dll) links the C++ runtime MSVCP140.dll and VCRUNTIME140.dll and VCRUNTIME140_1.dll,
and its wheels carry none of them: Windows has them from the Visual C++ Redistributable. Without them the first
``import torch`` fails with Windows error 126 (a file is missing) or 1114 (a library did not initialize), and the
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
LIBRARIES = ('VCRUNTIME140.dll', 'VCRUNTIME140_1.dll', 'MSVCP140.dll')


def architecture(platform_tag=None):
    """The Redistributable that fits this Python: ARM64 for a native Arm build, x64 otherwise (an x64 Python emulated
    on Windows on Arm loads x64 libraries)."""
    return 'ARM64' if (platform_tag or sysconfig.get_platform()).lower().endswith('arm64') else 'x64'


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


def check(load=_load):
    """[(name, path, version)] of the libraries that loaded, and [(name, Windows error)] of those that did not."""
    loaded, failures = [], []
    for name in LIBRARIES:
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


def require(*, windows=None, load=_load, out=None, platform_tag=None):
    """On Windows: write where each library came from, and raise RuntimeUnavailable when one does not load."""
    if not (sys.platform == 'win32' if windows is None else windows):
        return
    out = out or sys.stdout
    loaded, failures = check(load)
    for name, path, version in loaded:
        print('Visual C++ runtime: %s %s %s %s' % (name, version or 'version-unknown', where(path), path or 'path-unknown'),
              file=out, flush=True)
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
