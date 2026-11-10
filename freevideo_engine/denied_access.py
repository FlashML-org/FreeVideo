"""Where an access-denied failure happened, worded without a user name or a private folder.

Windows reports "[WinError 5] Access is denied" for many different operations, and when the call
names no file the message says nothing else. The launcher keeps this description next to the
message: the folder as the drive letter plus the part inside FreeVideo's own folders, and the last
FreeVideo functions that were running.
"""
import errno
import os
import re
import tempfile

from .system import windows

DENIED_ERRNO = (errno.EACCES, errno.EPERM)
# Folders that stay in the description when the denied path is inside one of them.
OWN_NAME = re.compile(r'^(?:freevideo|fv$)', re.I)
# The installation folder the launcher creates by default; it is kept even where no installation root is known.
ENGINE_NAME = re.compile(r'^freevideo-engine$', re.I)
# Folders named below FreeVideo's own folder: user-named files (models, inputs, outputs) sit deeper.
INSIDE = 2
STEPS = 3
SOCKET_DENIED = 10013


def is_denied(error):
    if getattr(error, 'winerror', None) == SOCKET_DENIED:
        # A port that Windows reserved is not a refused file or folder.
        return False
    return isinstance(error, PermissionError) or (isinstance(error, OSError) and (
        getattr(error, 'winerror', None) == 5 or error.errno in DENIED_ERRNO))


def _segments(path):
    path = str(path)
    if path.startswith('\\\\?\\') and not path.startswith('\\\\?\\UNC\\'):
        path = path[4:]
    drive = ''
    match = re.match(r'([A-Za-z]):(?:[\\/]|$)', path)
    if match:
        drive, path = match.group(1).upper() + ':', path[match.end():]
    return drive, [part for part in re.split(r'[\\/]+', path) if part]


def _under(parts, root):
    return len(parts) >= len(root) and all(a.casefold() == b.casefold() for a, b in zip(parts, root))


def _inside(parts):
    """The first folders below one of FreeVideo's own, then an ellipsis where more follow."""
    return parts[:INSIDE] + (['…'] if len(parts) > INSIDE else [])


def default_roots():
    """The user's own folders, which are named by a placeholder and never spelled out."""
    roots = []
    for label, value in (('HOME', os.path.expanduser('~')), ('TEMP', tempfile.gettempdir()),
                         ('PROGRAMDATA', os.environ.get('ProgramData', ''))):
        if value and value != '~':
            roots.append((label, value))
    return roots


def location(path, roots=()):
    """A path as "[D:] INSTALL\\launcher": the drive letter and a placeholder in place of every folder that
    is not FreeVideo's own. The installation root is the engine folder itself, so no folder above it is named,
    and nothing below it deeper than INSIDE folders. The text does not look like a path to the launcher's redaction."""
    path = str(path or '')
    if not path:
        return ''
    if path.startswith(('\\\\', '//')) and (not path.startswith('\\\\?\\') or path.upper().startswith('\\\\?\\UNC\\')):
        return '[network share]'
    drive, parts = _segments(path)
    best = None
    for label, root in list(roots) + default_roots():
        root_drive, root_parts = _segments(root)
        # A root with no folder (a whole drive) would make every path on it FreeVideo's own.
        if root_parts and root_drive == drive and _under(parts, root_parts) and (
                best is None or len(root_parts) > len(best[1])):
            best = (label, root_parts)
    prefix = '[%s] ' % drive if drive else ''
    if best is None:
        # A name outside FreeVideo's folders may be a person, a project or a video, so none is kept,
        # except the engine folder the launcher creates by default.
        own = next((index for index, part in enumerate(parts) if ENGINE_NAME.match(part)), None)
        if own is not None:
            return prefix + '\\'.join(['…', parts[own]] + _inside(parts[own + 1:]))
        return prefix + '…' if parts else prefix.strip()
    label, root_parts = best
    rest = parts[len(root_parts):]
    if label in ('HOME', 'TEMP'):
        # Only what FreeVideo created below the user's folder is named.
        own = next((index for index, part in enumerate(rest) if OWN_NAME.match(part)), None)
        rest = ['…'] + ([rest[own]] + _inside(rest[own + 1:]) if own is not None else [])
    elif label == 'INSTALL':
        rest = _inside(rest)
    return prefix + '\\'.join([label] + rest)


def _chain(error):
    seen = set()
    while isinstance(error, BaseException) and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)


def steps(error):
    """The innermost FreeVideo functions of the traceback, newest first: "processes.popen:211"."""
    rows, tb = [], error.__traceback__
    while tb is not None:
        module = str(tb.tb_frame.f_globals.get('__name__') or '')
        if module == 'freevideo_engine' or module.startswith('freevideo_engine.'):
            rows.append('%s.%s:%d' % (module.partition('.')[2] or module, tb.tb_frame.f_code.co_name, tb.tb_lineno))
        tb = tb.tb_next
    return rows[-STEPS:][::-1]


def describe(error, roots=()):
    """The access-denied error in this exception chain as plain data, or None when there is none."""
    for item in _chain(error):
        if is_denied(item):
            winerror = getattr(item, 'winerror', None)
            result = dict(code=winerror or item.errno, windows=bool(winerror), steps=steps(item))
            paths = [location(name, roots) for name in (item.filename, item.filename2) if name]
            if paths:
                result['path'] = ' → '.join(paths)
            return result
    return None


def text(info, zh=False):
    """The line kept in the report: what was denied, where, and the FreeVideo steps that were running."""
    if not info:
        return ''
    label = (('Windows 错误 %s' if zh else 'Windows error %s') if info.get('windows') else ('错误 %s' if zh else 'error %s'))
    code = ('（%s）' if zh else ' (%s)') % (label % info['code']) if info.get('code') else ''
    if info.get('path'):
        head = ('访问被拒绝%s：%s' if zh else 'Access denied%s: %s') % (code, info['path'])
    else:
        head = ('访问被拒绝%s，系统没有说明是哪个文件或文件夹' if zh else
                'Access denied%s; the system did not name a file or folder') % code
    if info.get('steps'):
        head += ('；步骤：' if zh else '; steps: ') + ' < '.join(info['steps'])
    return head


PLACEHOLDERS = (('INSTALL', '[安装目录]', '[installation folder]'), ('HOME', '[用户文件夹]', '[user folder]'),
                ('TEMP', '[临时文件夹]', '[temp folder]'), ('[network share]', '[网络共享]', '[network share]'))


def card_text(info, zh=False):
    """The line on the failure card: only where access was denied, with readable names for the placeholders."""
    value = (info or {}).get('path')
    if not value:
        return ''
    for token, chinese, english in PLACEHOLDERS:
        value = re.sub(r'(?<![\w\[])%s(?![\w\]])' % re.escape(token) if token[0] != '[' else re.escape(token),
                       chinese if zh else english, value)
    return ('被拒绝的位置：' if zh else 'Denied location: ') + value


# The lead of the error when the engine folder's place refuses new folders; the failure card matches it.
ENGINE_FOLDER_DENIED = 'Windows does not let this account create folders in'
# Folders a standard account cannot write to, named as they are because they hold no personal name.
PROTECTED = ('program files', 'program files (x86)', 'windows')


def engine_folder_error(error, engine):
    """The error to raise when preparing the engine folder was denied on Windows, else None.

    Only a refusal to create something in the engine folder, or in a folder above it, says the place
    is closed to this account. A file in use (Windows error 32 is also a PermissionError), a rename
    or copy that names two files, and a file elsewhere (the launcher's own files) keep their cards.
    The message names a protected system folder (Program Files) and otherwise only the drive,
    never a user's folder."""
    if not windows() or not is_denied(error):
        return None
    if getattr(error, 'winerror', None) == 32 or getattr(error, 'filename2', None):
        return None
    drive, parts = _segments(engine)
    if getattr(error, 'filename', None):
        denied_drive, denied = _segments(error.filename)
        # mkdir(parents=True) can be refused at a folder above the engine folder.
        if denied_drive != drive or not (_under(denied, parts) or _under(parts, denied)):
            return None
    if drive and parts and parts[0].casefold() in PROTECTED:
        where = (drive + '\\' if drive else '') + parts[0]
    elif drive:
        where = 'the chosen location on drive ' + drive[:-1]
    else:
        where = 'the chosen location'
    return ValueError('%s %s. Choose a folder in the user folder or on another drive.'
                      % (ENGINE_FOLDER_DENIED, where))
