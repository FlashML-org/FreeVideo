"""What a person sees when FreeVideo fails before its window opens.

Standard library only: the entry points call this when the launcher itself could not load, so nothing it
uses may be the part that failed. The traceback goes to startup-error.txt, never into the message box.
"""
import os
from pathlib import Path
import sys
import tempfile
import traceback

FILE = 'startup-error.txt'
ISSUES = 'https://github.com/FlashML-org/FreeVideo/issues/new'
TITLE = 'FreeVideo'


def launcher_folder():
    """The packaged launcher's own folder (desktop_runtime.launcher_root), without importing the engine."""
    if os.environ.get('FREEVIDEO_LAUNCHER_HOME'):
        return Path(os.environ['FREEVIDEO_LAUNCHER_HOME'])
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/FreeVideo/launcher'
    return Path(os.environ.get('LOCALAPPDATA') or str(Path.home())) / 'FreeVideo-launcher'


def save(detail, folder):
    """Write the traceback to the first folder that takes it; None when none does."""
    for place in (Path(folder), Path(tempfile.gettempdir())):
        try:
            place.mkdir(parents=True, exist_ok=True)
            path = place / FILE
            path.write_text(detail, encoding='utf-8')
            return path
        except OSError:
            continue
    return None


def message(path):
    """The short text for the person, in Chinese and English: what happened, where the details are, where to report."""
    if path:
        where = '错误详情已保存到：\nThe details were saved to:\n' + str(path)
        report = '请在下面的地址提交问题，并附上这个文件：\nPlease report the problem here and attach the file:\n'
    else:
        # Nothing to attach: only where to report.
        where = '错误详情未能保存。\nThe details could not be saved.'
        report = '请在下面的地址提交问题：\nPlease report the problem here:\n'
    return 'FreeVideo 未能启动。\nFreeVideo could not start.\n\n' + where + '\n\n' + report + ISSUES


def report(folder, detail=None):
    """Save the current exception's traceback and tell the person; returns the file it was saved to."""
    detail = detail if detail is not None else traceback.format_exc()
    path = save(detail, folder)
    text = message(path)
    if sys.stderr is not None:
        # A console shows the whole traceback; the short text comes last, where the person looks.
        sys.stderr.write(detail + '\n' + text + '\n')
    elif sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10)
    return path
