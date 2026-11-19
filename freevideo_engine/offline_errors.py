"""Bilingual copy for offline import failures; the full OS details stay in the log and the report."""
import errno
import os
import re

from .long_paths import plain, path_chars
from .system import windows as is_windows

COPY = {
    'title': ('Couldn’t complete this step', '暂时未能完成'),
    'runtime-alias': ("The installation folder's path is too long. "
                      'Install FreeVideo in a folder with a shorter path, such as D:\\FreeVideo.',
                      '安装文件夹的路径太长。请把 FreeVideo 安装到路径较短的文件夹，例如 D:\\FreeVideo。'),
    'permission': ('The offline package could not be imported: this account cannot write to the chosen folder. '
                   'Choose another folder and import again.',
                   '离线包未能导入：当前账户没有所选文件夹的写入权限。请换一个文件夹再导入。'),
    'disk': ('The offline package could not be imported: the drive is full. '
             'Free up space or choose a folder on another drive, then import again.',
             '离线包未能导入：磁盘空间不足。请腾出空间或换一个磁盘上的文件夹，再导入。'),
    'path': ('The offline package could not be imported: the path of the chosen folder is too long to save some of '
             'the files. Choose a folder with a shorter path%s and import again.',
             '离线包未能导入：所选文件夹的路径太长，部分文件无法保存。请换一个路径更短的文件夹再导入%s。'),
    'in-use': ('The offline package could not be imported: a file is in use by another program. Close programs that '
               'may be using the folder (for example an antivirus scan), then import again.',
               '离线包未能导入：有文件被其他程序占用。请关闭可能正在使用该文件夹的程序（例如杀毒软件扫描），再导入。'),
    'missing': ('The offline package could not be imported: a file disappeared during the import, or the drive was '
                'disconnected. Check that the package and the folder are still there, then import again.',
                '离线包未能导入：导入过程中有文件不见了，或磁盘已断开。请确认离线包和文件夹都还在，再导入。'),
    'read': ('The offline package could not be imported: reading it failed, so the file or the drive may be damaged. '
             'Download the package again or copy it to another drive, then import again.',
             '离线包未能导入：读取时出错，文件或磁盘可能已损坏。请重新下载离线包，或复制到其他磁盘后再导入。'),
    'other': ('The offline package could not be imported. Choose another folder and import again.',
              '离线包未能导入。请换一个文件夹再导入。'),
    'import-title': ('The offline package was not imported', '离线包未能导入'),
}
# The example folder is a Windows path; other systems leave it out.
PATH_EXAMPLE = (', such as D:\\FreeVideo,', '，例如 D:\\FreeVideo')


def alias_failure(value):
    """Recognize the exception after Controller/Runner have retained it as text."""
    from .runtime_alias import AliasUnavailable
    return isinstance(value, AliasUnavailable) or bool(re.search(
        r'Runtime alias unavailable \((?:location|filesystem|invalid|other)\):', str(value)))


def write_reason(error, *, windows=None):
    win = is_windows() if windows is None else windows
    code = getattr(error, 'winerror', None)
    # Prefer the Windows code over its lossy errno translation. The 2/3 (or
    # errno-only ENOENT) heuristic can misclassify genuinely missing files or
    # parents, dangling links and disconnected shares as "path too long",
    # even when the failed access already used the extended namespace.
    if code is not None:
        if code in (5, 19):
            return 'permission'
        if code in (32, 33):  # sharing and lock violations
            return 'in-use'
        if code in (23, 1392, 1117):  # data error (CRC), corrupt file, device I/O error
            return 'read'
        if code in (39, 112):
            return 'disk'
        if code == 206:
            return 'path'
        missing = code in (2, 3)
    else:
        if error.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            return 'permission'
        if error.errno in (errno.ENOSPC, errno.EDQUOT):
            return 'disk'
        if error.errno == errno.ENAMETOOLONG:
            return 'path'
        if error.errno in (errno.EBUSY, errno.ETXTBSY):
            return 'in-use'
        if error.errno == errno.EIO:
            return 'read'
        missing = error.errno == errno.ENOENT
    if win and missing:
        try:
            paths = [plain(os.fsdecode(p), windows=win) for p in
                     (getattr(error, 'filename', None), getattr(error, 'filename2', None)) if p]
            if any(path_chars(p) >= 248 for p in paths):
                return 'path'
        except Exception:
            pass
    # A missing file that is not explained by a long path: moved, deleted or a disconnected drive.
    return 'missing' if missing else 'other'


def guidance(reason, zh=False, *, windows=None):
    text = COPY[reason][bool(zh)]
    if reason == 'path':
        text %= PATH_EXAMPLE[bool(zh)] if (is_windows() if windows is None else windows) else ''
    return text
