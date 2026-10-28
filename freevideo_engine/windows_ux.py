"""Small Windows desktop helpers; no admin rights or persistent settings."""
from contextlib import contextmanager
import sys
import threading
from .system import windows


def taskbar_identity():
    """Identify the launcher before creating windows, including source launches."""
    if windows():
        import ctypes
        function = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        function.argtypes = [ctypes.c_wchar_p]
        function.restype = ctypes.c_long
        function('SrCodeGorilla.FreeVideoPascal.Launcher')


def open_browser(url):
    """Use Windows' URL association directly and retain its actionable errors."""
    with external_python():
        if windows():
            import os
            os.startfile(url)
            return True
        from .linux_bundle import child_environment, frozen_linux
        if frozen_linux():
            # webbrowser would hand the launcher's own environment to the browser.
            import shutil
            import subprocess
            opener = shutil.which('xdg-open')
            if opener:
                command = [opener, url]
            else:
                # Without xdg-open, start the browser webbrowser would choose (gio open,
                # x-www-browser, firefox...) the same way.
                import webbrowser
                try:
                    browser = webbrowser.get()
                except webbrowser.Error:
                    return False
                command = [browser.name] + [arg.replace('%s', url) for arg in browser.args]
            try:
                subprocess.Popen(command, env=child_environment(), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                return False
            return True
        import webbrowser
        return webbrowser.open(url, new=2)



def _viewer_path(path, engine):
    """A text in an installation deeper than the viewer can open, named through that installation's short folder."""
    from . import launch_view
    from .long_paths import plain
    path = plain(path)
    try:
        # The file's own runtime first: it can be another one than the selected.
        view = launch_view.for_file(path) or (launch_view.for_engine(engine) if engine else launch_view.current())
    except OSError:
        return path  # No short folder can be made: the viewer gets the path as it is.
    return path if view is None else str(launch_view.value(path, view))


def open_text(path, *, engine=None):
    """Show a text file on this computer (a licence, the notices) in the system's text viewer."""
    path = str(path)
    with external_python():
        if windows():
            path = _viewer_path(path, engine)
            if path.lower().endswith('.txt'):
                import os
                os.startfile(path)
            else:
                # LICENSE has no program of its own; Notepad reads it.
                import subprocess
                subprocess.Popen(['notepad.exe', path])
            return True
        if sys.platform == 'darwin':
            import subprocess
            subprocess.Popen(['/usr/bin/open', '-t', path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return True
    from pathlib import Path
    return open_browser(Path(path).as_uri())

def hidden_console():
    """Hide a console and keep it inheritable by native helper processes."""
    if not windows():
        return {}
    import subprocess
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    return dict(startupinfo=info)


_DLL_LOCK = threading.Lock()
_DLL_USERS = 0
_DLL_PREVIOUS = None


@contextmanager
def external_python():
    """Keep PyInstaller's private DLL search directory out of external Python.

    The directory is process-wide and parallel source probes start several curl
    processes at once: clear it for the first user and restore it after the last,
    so overlapping callers cannot leave the launcher without its DLL directory.
    """
    global _DLL_USERS, _DLL_PREVIOUS
    if not (windows() and getattr(sys, 'frozen', False)):
        yield
        return
    import ctypes
    with _DLL_LOCK:
        if not _DLL_USERS:
            buffer = ctypes.create_unicode_buffer(32768)
            ctypes.windll.kernel32.GetDllDirectoryW(len(buffer), buffer)
            _DLL_PREVIOUS = buffer.value
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        _DLL_USERS += 1
    try:
        yield
    finally:
        with _DLL_LOCK:
            _DLL_USERS -= 1
            if not _DLL_USERS:
                ctypes.windll.kernel32.SetDllDirectoryW(_DLL_PREVIOUS or None)


@contextmanager
def awake():
    """Prevent idle sleep on this worker thread, restore its state on exit.

    Display power saving and an explicit user sleep/lid action still apply.
    """
    previous, function = 0, None
    if windows():
        try:
            import ctypes
            function = ctypes.WinDLL('kernel32', use_last_error=True).SetThreadExecutionState
            function.argtypes, function.restype = [ctypes.c_uint32], ctypes.c_uint32
            previous = function(0x80000001)
        except OSError:
            pass
    try:
        yield bool(previous)
    finally:
        if previous:
            function(0x80000000 | previous)


def work_area(window):
    left, top = 0, 0
    width, height = window.winfo_screenwidth(), window.winfo_screenheight()
    if windows():
        import ctypes
        from ctypes import wintypes
        rectangle = wintypes.RECT()
        function = ctypes.WinDLL('user32', use_last_error=True).SystemParametersInfoW
        function.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        function.restype = wintypes.BOOL
        if function(0x30, 0, ctypes.byref(rectangle), 0):
            left, top = rectangle.left, rectangle.top
            width, height = rectangle.right-rectangle.left, rectangle.bottom-rectangle.top
    return left, top, width, height


def window_size(window):
    _, _, width, height = work_area(window)
    return min(width-40, max(680, int(width*.82))), min(height-60, max(600, int(height*.86)))
