"""A durable, per-user Windows shortcut; no shell scripts or registry writes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from .monitoring import save
from .system import windows


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def _shell_folder(csidl, what):
    import ctypes
    from ctypes import wintypes
    buffer = ctypes.create_unicode_buffer(32768)
    call = ctypes.WinDLL('shell32').SHGetFolderPathW
    call.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR]
    call.restype = ctypes.c_long
    if call(None, csidl | 0x8000, None, 0, buffer) != 0 or not buffer.value:  # CSIDL_FLAG_CREATE
        raise OSError('Windows could not locate your %s folder' % what)
    return Path(buffer.value)


def desktop_directory():
    return _shell_folder(0x10, 'desktop')  # CSIDL_DESKTOPDIRECTORY, redirected desktops included


def start_menu_directory():
    """Start menu Programs, where Windows search finds FreeVideo when the desktop has no shortcut."""
    return _shell_folder(0x02, 'Start menu')  # CSIDL_PROGRAMS


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


# IShellLinkW::SetPath fails (E_FAIL) for a target of MAX_PATH, 260 characters, or more.
LINK_PATH_LIMIT = 259


def copies_folder(engine, fingerprint):
    """Where the launcher copy behind the shortcut goes: in the installation, unless the path
    would be too long for a Windows shortcut, as in an offline installation in a deep folder."""
    parent = Path(os.path.abspath(engine)) / 'launcher/application'
    if len(str(parent / (fingerprint + '-' + 'f' * 8) / 'FreeVideo.exe')) <= LINK_PATH_LIMIT:
        return parent
    from .desktop_runtime import launcher_root
    return Path(os.path.abspath(launcher_root())) / 'application'


def _complete(root, fingerprint, folder):
    """A launcher copy the shortcut can start; one this account cannot read is not."""
    try:
        return (digest(root / 'FreeVideo.exe') == fingerprint
                and (not folder or (root / '_internal').is_dir()))
    except OSError:
        return False


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
        parent = copies_folder(engine, fingerprint)
        for root in [parent / fingerprint, *sorted(parent.glob(fingerprint + '-*'))]:
            if _complete(root, fingerprint, folder):
                return root / 'FreeVideo.exe', [], root / 'FreeVideo.exe'
        parent.mkdir(parents=True, exist_ok=True)
        # Not tempfile.mkdtemp: since Python 3.12.4 that folder admits only its owner, so a copy
        # made while running as administrator could not be opened from the shortcut afterwards.
        stage = parent / ('launcher-' + uuid.uuid4().hex)
        stage.mkdir()
        try:
            shutil.copy2(executable, stage / 'FreeVideo.exe')
            if folder:
                shutil.copytree(executable.parent / '_internal', stage / '_internal')
            if digest(stage / 'FreeVideo.exe') != fingerprint:
                raise OSError('Launcher changed while preparing the desktop shortcut; retry installation')
            root = parent / fingerprint
            if os.path.lexists(root):
                root = root.with_name(fingerprint + '-' + uuid.uuid4().hex[:8])
            stage.rename(root)
        finally:
            if stage.exists(): shutil.rmtree(stage, ignore_errors=True)  # Every start retries: no partial copies.
        return root / 'FreeVideo.exe', [], root / 'FreeVideo.exe'
    python = Path(sys.executable).resolve()
    if python.with_name('pythonw.exe').is_file():
        python = python.with_name('pythonw.exe')
    code = "import runpy,sys;sys.path.insert(0,sys.argv[1]);runpy.run_module('freevideo_engine.comfy_launcher',run_name='__main__')"
    return python, ['-c', code, str(Path(source).resolve())], Path(source) / 'freevideo_engine/assets/icon.ico'


def _same(first, second):
    return bool(first) and bool(second) and os.path.normcase(str(first)) == os.path.normcase(str(second))


def _ours(path, entry, target):
    """A shortcut this or an earlier FreeVideo installation wrote, not one of the user's own.

    The recorded digest stops matching once Windows rewrites a link's tracking
    data in place, and a reinstall into another folder starts with no record.
    Either way the old link was kept as the user's own and a second shortcut
    added next to it, one of them starting a launcher that no longer exists.
    A link that starts a FreeVideo launcher copy, or names a FreeVideo.exe that
    is gone, is still ours; a user's own link to anything else is left alone.
    """
    if _same(entry.get('path'), path) and entry.get('sha256'):
        try:
            if digest(path) == entry['sha256']:
                return True
        except OSError:
            pass
    try:
        named = read_link_targets([path]).get(str(path))
    except (OSError, AttributeError, ValueError):
        return False  # Unreadable: treat it as the user's.
    if not named:
        return False
    named = Path(named)
    if _same(named, target) or _same(named, entry.get('target')):
        return True
    return named.name.lower() == 'freevideo.exe' and (
        not named.exists() or named.parent.parent.name.lower() in ('application', 'application.noindex'))


def _starts(path, target):
    try:
        return _same(read_link_targets([path]).get(str(path)), target)
    except (OSError, AttributeError, ValueError):
        return False


def _renamed(folder, entry, target):
    """This installation's link under a name the user chose; refresh it rather than add another."""
    links = [path for path in folder.glob('*.lnk') if not path.name.startswith('FreeVideo-')]
    try:
        named = read_link_targets(links)
    except (OSError, AttributeError, ValueError):
        return None
    copies = Path(target).parent.parent  # <engine>/launcher/application
    for path in links:
        found = named.get(str(path))
        if found and (_same(found, target) or _same(found, entry.get('target'))
                      or copies.name == 'application' and _same(Path(found).parent.parent, copies)
                      and Path(found).name.lower() == 'freevideo.exe'):
            return path
    return None


def _place(folder, entry, target, arguments, icon):
    """Write or refresh FreeVideo.lnk in one folder, keeping a user's own link of that name."""
    path = folder / 'FreeVideo.lnk'
    old = Path(entry['path']) if entry.get('path') else None
    if old is not None and old.parent == folder and old.is_file() and _ours(old, entry, target):
        path = old
    elif not (path.is_file() and _ours(path, entry, target)):
        path = _renamed(folder, entry, target) or path
        index = 2
        while path.name.startswith('FreeVideo') and path.exists() and not _ours(path, entry, target):
            path = folder / ('FreeVideo (%d).lnk' % index); index += 1
    if path.is_file():
        if (_same(entry.get('path'), path) and _same(entry.get('target'), target)
                and entry.get('arguments') == arguments and entry.get('sha256') == digest(path)):
            return dict(entry, status='present')
        if not arguments and _starts(path, target):
            # Renamed, given another icon or hotkey, or its tracking data rewritten by
            # Windows: it still starts this launcher, so keep it as it is and record it.
            return dict(status='present', path=str(path), target=str(target), arguments=arguments, sha256=digest(path))
    stage = folder / ('FreeVideo-' + uuid.uuid4().hex + '.lnk')
    try:
        write_link(stage, target, arguments, target.parent, icon)
        os.replace(stage, path)
    finally:
        if stage.exists(): stage.unlink()
    return dict(status='created', path=str(path), target=str(target), arguments=arguments, sha256=digest(path))


def _failure(error, menu_ready):
    """What the launcher says when the desktop refused the shortcut, in both languages."""
    text = str(error)
    denied = isinstance(error, PermissionError) or '0x80070005' in text.lower() or 'access is denied' in text.lower()
    en = ['The desktop shortcut could not be created:']
    zh = ['桌面快捷方式没有创建成功：']
    if denied:
        # Controlled folder access (Windows Security) refuses unknown programs on the desktop.
        en.append('Windows did not allow FreeVideo to write to the desktop. If Controlled folder access is on in '
                  'Windows Security, allow FreeVideo there, then click Create desktop shortcut.')
        zh.append('Windows 不允许 FreeVideo 写入桌面。如果在“Windows 安全中心”里开启了“受控文件夹访问”，'
                  '请允许 FreeVideo，然后点击“创建桌面快捷方式”。')
    else:
        en.append(text); zh.append(text)
    if menu_ready:
        en.append('FreeVideo is in the Start menu.'); zh.append('开始菜单里可以找到 FreeVideo。')
    return ' '.join(en), ''.join(zh)


def create(engine, source, portable_root=None):
    if not windows():
        if sys.platform == 'darwin':
            from .macos_shortcut import create as create_mac
            return create_mac(engine)
        return dict(status='not-applicable')
    engine = Path(engine)
    receipt = engine / 'launcher/desktop-shortcut.json'
    target, arguments, icon = launcher_target(engine, source, portable_root)
    try:
        previous = json.loads(receipt.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    recorded = {key: value for key, value in previous.items() if key != 'start_menu'}
    # Each place on its own: a refused desktop must not cost the Start menu entry.
    try:
        menu = _place(start_menu_directory(), previous.get('start_menu') or {}, target, arguments, icon)
    except OSError as error:
        menu = dict(status='failed', error=str(error))
    try:
        desktop = _place(desktop_directory(), recorded, target, arguments, icon)
    except OSError as error:
        en, zh = _failure(error, menu['status'] in ('created', 'present'))
        if menu['status'] != 'failed':
            save(receipt, dict(recorded, start_menu=menu))
        return dict(status='failed', error=en, error_zh=zh, start_menu=menu)
    result = dict(desktop, start_menu=menu)
    if result != previous:
        save(receipt, result)
    return result


def create_after_install(engine, source, portable_root=None):
    # An unwritable desktop must not undo a successful model installation.
    try:
        return create(engine, source, portable_root)
    except (OSError, ValueError) as error:
        return dict(status='failed', error='Installed successfully, but the desktop shortcut could not be created: ' + str(error),
                    error_zh='安装已完成，但桌面快捷方式没有创建成功：' + str(error))
