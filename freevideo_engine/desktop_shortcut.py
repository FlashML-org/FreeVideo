"""A durable, per-user Windows shortcut; no shell scripts or registry writes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

from .monitoring import save
from .system import windows


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def desktop_directory():
    import ctypes
    from ctypes import wintypes
    buffer = ctypes.create_unicode_buffer(32768)
    call = ctypes.WinDLL('shell32').SHGetFolderPathW
    call.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR]
    call.restype = ctypes.c_long
    if call(None, 0x8010, None, 0, buffer) != 0 or not buffer.value:
        raise OSError('Windows could not locate your desktop folder')
    return Path(buffer.value)


def write_link(path, target, arguments, working_directory, icon):
    """Use IShellLinkW / IPersistFile, including Unicode and redirected desktops."""
    import ctypes as c
    class GUID(c.Structure):
        _fields_ = [('data', c.c_ubyte * 16)]
        def __init__(self, value):
            super().__init__()
            self.data[:] = uuid.UUID(value).bytes_le
    ole = c.WinDLL('ole32')
    ole.CoInitializeEx.argtypes = [c.c_void_p, c.c_uint32]; ole.CoInitializeEx.restype = c.c_long
    ole.CoUninitialize.argtypes = []; ole.CoUninitialize.restype = None
    ole.CoCreateInstance.argtypes = [c.POINTER(GUID), c.c_void_p, c.c_uint32, c.POINTER(GUID), c.POINTER(c.c_void_p)]
    ole.CoCreateInstance.restype = c.c_long
    initialized = ole.CoInitializeEx(None, 2)
    if initialized < 0 and initialized != -2147417850:  # Already initialized in another COM mode is usable.
        raise OSError('Windows shortcut initialization failed: 0x%08x' % (initialized & 0xffffffff))
    link, persist = c.c_void_p(), c.c_void_p()
    def checked(result):
        if result < 0:
            raise OSError('Windows shortcut failed: 0x%08x' % (result & 0xffffffff))
    def method(obj, index, types, *values):
        table = c.cast(obj, c.POINTER(c.POINTER(c.c_void_p))).contents
        result = c.WINFUNCTYPE(c.c_long, c.c_void_p, *types)(table[index])(obj, *values)
        checked(result)
    try:
        cls = GUID('00021401-0000-0000-c000-000000000046')
        iid = GUID('000214f9-0000-0000-c000-000000000046')
        checked(ole.CoCreateInstance(c.byref(cls), None, 1, c.byref(iid), c.byref(link)))
        method(link, 20, [c.c_wchar_p], str(target))
        method(link, 11, [c.c_wchar_p], subprocess.list2cmdline(arguments))
        method(link, 9, [c.c_wchar_p], str(working_directory))
        method(link, 7, [c.c_wchar_p], 'FreeVideo')
        method(link, 17, [c.c_wchar_p, c.c_int], str(icon), 0)
        persist_iid = GUID('0000010b-0000-0000-c000-000000000046')
        method(link, 0, [c.POINTER(GUID), c.POINTER(c.c_void_p)], c.byref(persist_iid), c.byref(persist))
        method(persist, 6, [c.c_wchar_p, c.c_int], str(path), 1)
    finally:
        if persist: method(persist, 2, [])
        if link: method(link, 2, [])
        if initialized >= 0: ole.CoUninitialize()


def read_link_targets(paths):
    """The programs Windows shortcuts start, keyed by shortcut; unreadable ones are skipped."""
    import ctypes as c
    class GUID(c.Structure):
        _fields_ = [('data', c.c_ubyte * 16)]
        def __init__(self, value):
            super().__init__()
            self.data[:] = uuid.UUID(value).bytes_le
    ole = c.WinDLL('ole32')
    ole.CoInitializeEx.argtypes = [c.c_void_p, c.c_uint32]; ole.CoInitializeEx.restype = c.c_long
    ole.CoUninitialize.argtypes = []; ole.CoUninitialize.restype = None
    ole.CoCreateInstance.argtypes = [c.POINTER(GUID), c.c_void_p, c.c_uint32, c.POINTER(GUID), c.POINTER(c.c_void_p)]
    ole.CoCreateInstance.restype = c.c_long
    initialized = ole.CoInitializeEx(None, 2)
    if initialized < 0 and initialized != -2147417850:
        raise OSError('Windows shortcut initialization failed: 0x%08x' % (initialized & 0xffffffff))
    def method(obj, index, types, *values):
        table = c.cast(obj, c.POINTER(c.POINTER(c.c_void_p))).contents
        result = c.WINFUNCTYPE(c.c_long, c.c_void_p, *types)(table[index])(obj, *values)
        if result < 0:
            raise OSError('Windows shortcut failed: 0x%08x' % (result & 0xffffffff))
    targets = {}
    try:
        cls = GUID('00021401-0000-0000-c000-000000000046')
        iid = GUID('000214f9-0000-0000-c000-000000000046')
        persist_iid = GUID('0000010b-0000-0000-c000-000000000046')
        for path in paths:
            link, persist = c.c_void_p(), c.c_void_p()
            try:
                if ole.CoCreateInstance(c.byref(cls), None, 1, c.byref(iid), c.byref(link)) < 0:
                    continue
                method(link, 0, [c.POINTER(GUID), c.POINTER(c.c_void_p)], c.byref(persist_iid), c.byref(persist))
                method(persist, 5, [c.c_wchar_p, c.c_uint32], str(path), 0)  # Load, read-only.
                buffer = c.create_unicode_buffer(32768)
                method(link, 3, [c.c_wchar_p, c.c_int, c.c_void_p, c.c_uint32], buffer, len(buffer), None, 0)
                if buffer.value:
                    targets[str(path)] = buffer.value
            except OSError:
                continue
            finally:
                if persist: method(persist, 2, [])
                if link: method(link, 2, [])
    finally:
        if initialized >= 0: ole.CoUninitialize()
    return targets


def launcher_target(engine, source, portable_root=None):
    if portable_root:
        target = Path(portable_root) / 'FreeVideo.exe'
        if not target.is_file():
            raise FileNotFoundError('Bundle launcher is missing: ' + str(target))
        return target, [], target
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable).resolve()
        from .launcher_update import current_build
        folder = (current_build() or {}).get('packaging') == 'onedir'
        fingerprint = digest(executable)
        parent = Path(engine) / 'launcher/application'
        root = parent / fingerprint
        target = root / 'FreeVideo.exe'
        if not target.is_file() or digest(target) != fingerprint or (folder and not (root / '_internal').is_dir()):
            parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix='launcher-', dir=parent))
            shutil.copy2(executable, stage / 'FreeVideo.exe')
            if folder:
                shutil.copytree(executable.parent / '_internal', stage / '_internal')
            if digest(stage / 'FreeVideo.exe') != fingerprint:
                raise OSError('Launcher changed while preparing the desktop shortcut; retry installation')
            if root.exists():
                root = root.with_name(fingerprint + '-' + uuid.uuid4().hex[:8])
            stage.rename(root)
            target = root / 'FreeVideo.exe'
        return target, [], target
    python = Path(sys.executable).resolve()
    if python.with_name('pythonw.exe').is_file():
        python = python.with_name('pythonw.exe')
    code = "import runpy,sys;sys.path.insert(0,sys.argv[1]);runpy.run_module('freevideo_engine.comfy_launcher',run_name='__main__')"
    return python, ['-c', code, str(Path(source).resolve())], Path(source) / 'freevideo_engine/assets/icon.ico'


def create(engine, source, portable_root=None):
    if not windows():
        if sys.platform == 'darwin':
            from .macos_shortcut import create as create_mac
            return create_mac(engine)
        return dict(status='not-applicable')
    engine = Path(engine)
    receipt = engine / 'launcher/desktop-shortcut.json'
    target, arguments, icon = launcher_target(engine, source, portable_root)
    desktop = desktop_directory()
    path = desktop / 'FreeVideo.lnk'
    try:
        previous = json.loads(receipt.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    old = Path(previous.get('path', str(path)))
    if old.parent == desktop and old.is_file() and digest(old) == previous.get('sha256'):
        path = old
        if previous.get('target') == str(target) and previous.get('arguments') == arguments:
            return dict(previous, status='present')
    else:
        index = 2
        while path.exists():
            path = desktop / ('FreeVideo (%d).lnk' % index); index += 1
    stage = desktop / ('FreeVideo-' + uuid.uuid4().hex + '.lnk')
    try:
        write_link(stage, target, arguments, target.parent, icon)
        os.replace(stage, path)
    finally:
        if stage.exists(): stage.unlink()
    result = dict(status='created', path=str(path), target=str(target), arguments=arguments, sha256=digest(path))
    save(receipt, result)
    return result


def create_after_install(engine, source, portable_root=None):
    # An unwritable desktop must not undo a successful model installation.
    try:
        return create(engine, source, portable_root)
    except (OSError, ValueError) as error:
        return dict(status='failed', error='Installed successfully, but the desktop shortcut could not be created: ' + str(error))
