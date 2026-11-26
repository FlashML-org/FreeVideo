"""Folder names that Windows refuses, found before anything is created.

Windows answers a path with a character it forbids in a folder name with
``[WinError 123] The filename, directory name, or volume label syntax is
incorrect``. The failure text hides the path, so neither the user nor we could see
which folder name caused it, and it appeared only part way through installation.
This module names the problem by position and kind and never returns the name itself.
"""

import re

from pathlib import Path

from .long_paths import io_path, is_windows, plain

# The characters Windows forbids in a name; the separators / and \ and the drive colon are not names.
FORBIDDEN = '<>:"|?*'
RESERVED = re.compile(r'(?i)^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9¹²³])$')
ENDING = ' .'

# The folder-name problem in the installation folder path; the failure card matches it.
FOLDER_NAME_INVALID = 'Windows does not accept a folder name in the installation folder path'


def _names(path):
    """The folder names after the drive (or the network share), as typed; the drive itself is not a name."""
    value = plain(str(path), windows=True)
    match = re.match(r'[A-Za-z]:[\\/]*', value)
    if match:
        value = value[match.end():]
    elif re.match(r'[\\/]{2}[^\\/]+[\\/]+[^\\/]+(?:[\\/]|$)', value):
        value = re.sub(r'^[\\/]{2}[^\\/]+[\\/]+[^\\/]+', '', value)
    return [part for part in re.split(r'[\\/]+', value) if part and part not in ('.', '..')]


def problem(path, *, windows=None):
    """``(kind, number, character)`` for the first folder name Windows refuses, else None.

    ``number`` counts the names after the drive from 1. ``kind`` is 'character' (``character``
    is the forbidden character, or '' for a control character), 'reserved' or 'ending'.
    Other systems accept all of these, so only Windows is checked."""
    if not (is_windows() if windows is None else windows) or not isinstance(path, str):
        return None
    for number, name in enumerate(_names(path), 1):
        for character in name:
            if character in FORBIDDEN or ord(character) < 32:
                return 'character', number, character if character in FORBIDDEN else ''
        if RESERVED.match(name.split('.')[0].rstrip(ENDING)):
            return 'reserved', number, ''
        if name[-1] in ENDING:
            return 'ending', number, ''
    return None


def message(found, zh=False):
    """The line shown under the field that tells the user which name to change, without the name."""
    kind, number, character = found
    if kind == 'character':
        what = ('contains %s, which Windows does not allow' % ('"%s"' % character if character else 'a control character'),
                '含有 Windows 不允许出现在文件夹名中的%s' % ('“%s”' % character if character else '控制字符'))
    elif kind == 'reserved':
        what = ('is a name Windows reserves for devices (such as CON, NUL or COM1)',
                '是 Windows 为设备保留的名称（例如 CON、NUL、COM1）')
    else:
        what = ('ends with a space or a dot, which Windows does not allow',
                '以空格或句点结尾，Windows 不允许这样的名称')
    if zh:
        return '路径中的第 %d 个文件夹名%s。请修改该名称，或另选一个文件夹。' % (number, what[1])
    return 'Folder name %d in the path %s. Rename it or choose another folder.' % (number, what[0])


def error(found, zh=False):
    """The error text of a failed run: the English text starts with the sentence the failure card matches."""
    text = message(found, zh)
    return text if zh else '%s. %s' % (FOLDER_NAME_INVALID, text)


def refused(path, *, windows=None):
    """The problem of a path that is to be created; a folder that already exists works, so it is never refused."""
    found = problem(path, windows=windows)
    if not found:
        return None
    try:
        if io_path(Path(path).expanduser()).exists():
            return None
    except (OSError, ValueError):
        pass
    return found


def check(path, zh=False, *, windows=None):
    """Raise ValueError with the explained problem when a folder to be created has a name Windows refuses."""
    found = refused(path, windows=windows)
    if found:
        raise ValueError(error(found, zh))
