"""Review and retain local files that differ from the tested model."""
import ctypes
import errno
import json
import os
from pathlib import Path
import stat
import sys
import time
import uuid

from .disk_space import size_text
from .monitoring import save
from .long_paths import io_path, plain


# Shared copy for setup, the launcher and the browser plan.
REVIEW = (
    '{file} ({size}) differs from the tested version: it will be renamed to {keep_as} and kept, '
    'and the tested version ({expected_size}) will be downloaded.',
    '{file}（{size}）与 FreeVideo 测试过的版本不同：将改名为 {keep_as} 保留，'
    '再下载测试过的版本（{expected_size}）。',
)
PROGRESS = ('Keeping the original file: ', '正在保留原文件：')
FAILURE_TITLE = ('An existing model differs from the tested version', '已有模型与 FreeVideo 测试过的版本不同')
FAILURE = (
    '{file} differs from the tested version and was left unchanged. Click {entry} again: '
    'the confirmation page will list this file, and after you confirm, it will be renamed and kept, '
    'and the tested version will be downloaded.',
    '{file} 与 FreeVideo 测试过的版本不同，这次没有改动它。请再次点击“{entry}”：'
    '确认页会列出这个文件，确认后将它改名保留，再下载测试过的版本。',
)
CLI_FAILURE = FAILURE[0].replace('Click {entry} again', 'Run setup again (./setup.sh, or setup.cmd on Windows)')
MISMATCH = 'MODEL_MISMATCH_REPAIR'
MISMATCH_PATH = 'MODEL_MISMATCH_PATH'
# Where the launcher offers no Repair: the first sentence of FAILURE, then one way out
# for each reason (failure_details.WITHOUT_REPAIR).
UNCHANGED = ('{file} differs from the tested version and was left unchanged.',
             '{file} 与 FreeVideo 测试过的版本不同，这次没有改动它。')
BUSY = ('When the current task ends, a Repair button appears here; after you click it and confirm, '
        'FreeVideo renames and keeps this file, then downloads the tested version.',
        '当前任务结束后，这里会出现“修复”按钮；点击并确认后，FreeVideo 会把这个文件改名保留，再下载测试过的版本。')
OFFLINE = ('This offline-package installation cannot switch back in one step yet. Move or rename the file below, '
           'then import the Prompt model package (FreeVideo-文本编码器.zip) again on the Installation page.',
           '离线包安装暂时不能一键换回。请把下面这个文件移走或改名，再在“安装”页重新导入“提示词模型（FreeVideo-文本编码器.zip）”包。')
FIRST_INSTALL = (
    '{file} differs from the tested version and was left unchanged. Click Retry installation below: '
    'the confirmation page will list this file, and after you confirm, it is renamed and kept, '
    'and the tested version is downloaded.',
    '{file} 与 FreeVideo 测试过的版本不同，这次没有改动它。请点击下方的“重试安装”：'
    '确认页会列出这个文件，确认后会将它改名保留，再下载测试过的版本。',
)
CHANGED = 'The installation plan changed. Detect configuration again and review the new plan before installing.'
# A file the review listed changed before setup reached it (the confirmed plan
# can also differ in other ways: bootstrap.confirmed raises plain CHANGED).
FILE_CHANGED = 'A file in the installation folder changed after the review. ' + CHANGED
CHANGED_TITLE = ('Installation stopped', '安装已停止')
CHANGED_WHY = dict(
    files=('Files in the installation folder changed after you confirmed.', '确认之后安装位置里的文件有变化。'),
    other=('The settings, GPU or free disk space changed after you confirmed.',
           '确认之后设置、显卡或磁盘空间有变化。'),
)
CHANGED_REPAIR = ('Click Repair again.', '请再点一次“修复”。')
CHANGED_WAYS = dict(
    busy=('A Repair button appears here when the current task ends. Click it.',
          '当前任务结束后，这里会出现“修复”按钮，请点它。'),
    none=('Click Retry installation below.', '请点下方的“重试安装”。'),
)
IDENTITY = ('bytes', 'mtime_ns', 'st_dev', 'st_ino', 'parent_realpath')


def review_lines(rows, zh=False):
    return [dict(text=REVIEW[zh].format(file=row['file'], size=size_text(row['bytes']), keep_as=row['keep_as'],
                                       expected_size=size_text(row['expected_bytes'])), path=row['path']) for row in rows]


def identity(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISREG(info.st_mode):
        return dict(bytes=info.st_size, mtime_ns=info.st_mtime_ns, st_dev=info.st_dev, st_ino=info.st_ino,
                    parent_realpath=plain(os.path.realpath(io_path(path.parent))))
    return None


def same_identity(current, approved):
    if not isinstance(current, dict) or not isinstance(approved, dict):
        return False
    keys = IDENTITY
    if os.name == 'nt' and (current.get('st_ino') == 0 or approved.get('st_ino') == 0):
        keys = ('bytes', 'mtime_ns', 'parent_realpath')
    return all(key in current and key in approved and current[key] == approved[key] for key in keys)


def expected(row):
    for algorithm in ('sha256', 'git_blob'):
        if row.get(algorithm):
            return dict(algorithm=algorithm, digest=row[algorithm])
    return None


def read(root):
    try:
        value = json.loads((Path(root) / 'logs/model-mismatches.json').read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def replacement(path, row, mismatches, reserved):
    current = identity(path)
    if current is None:
        return None
    prior = mismatches.get(str(path))
    recorded = (same_identity(current, prior) and expected(row) is not None
                and prior.get('expected') == expected(row))
    if current['bytes'] != row['bytes'] or recorded:
        name = path.name + '.replaced-' + time.strftime('%Y%m%d')
        target = path.with_name(name)
        index = 2
        while target.exists() or target.is_symlink() or target in reserved:
            target = path.with_name(name + '-' + str(index))
            index += 1
        reserved.add(target)
        return dict(path=str(path), **current, expected_bytes=row['bytes'], file=path.name, keep_as=target.name)
    return None


def record(root, path, row, before):
    if before is not None and same_identity(identity(path), before):
        values = read(root)
        values[str(path)] = dict(before, expected=expected(row),
                                 recorded_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        save(Path(root) / 'logs/model-mismatches.json', values)


def check(path, approved):
    if not same_identity(identity(path), approved):
        raise ValueError(FILE_CHANGED + ' ' + str(path))


def reuse_names(current, reviewed):
    approved = {row['path']: row for row in reviewed}
    for row in current:
        prior = approved.get(row['path'])
        if prior is None or row.get('keep_as') == prior.get('keep_as'):
            continue
        if {key: value for key, value in row.items() if key != 'keep_as'} != \
                {key: value for key, value in prior.items() if key != 'keep_as'}:
            continue
        name = prior.get('keep_as')
        if valid_name(name) and not os.path.lexists(Path(row['path']).with_name(name)):
            row['keep_as'] = name


def valid_name(name):
    return isinstance(name, str) and name not in ('', '.', '..') and '/' not in name and '\\' not in name


def exclusive_rename(source, target):
    """Rename in one call that refuses an existing target, where the system has
    one (Linux renameat2, macOS renamex_np): True when moved, False when the
    system or filesystem offers no such call."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == 'linux':
            call = libc.renameat2
            call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            args = (-100, os.fsencode(source), -100, os.fsencode(target), 1)  # AT_FDCWD, RENAME_NOREPLACE
        elif sys.platform == 'darwin':
            call = libc.renamex_np
            call.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            args = (os.fsencode(source), os.fsencode(target), 4)  # RENAME_EXCL
        else:
            return False
    except (AttributeError, OSError):
        return False
    call.restype = ctypes.c_int
    if call(*args) == 0:
        return True
    code = ctypes.get_errno()
    if code in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
        return False
    raise OSError(code, os.strerror(code), str(target))


def rename_noreplace(source, target):
    """Move source to target without replacing target, and without removing a
    file someone else puts at source meanwhile."""
    source, target = Path(source), Path(target)
    if os.name == 'nt':
        os.rename(source, target)  # refuses an existing target
        return
    if exclusive_rename(source, target):
        return
    # No one-call rename here: link the file, move the old name aside under a
    # private one, and remove that only while it is still the linked file.
    os.link(source, target)
    kept = os.lstat(target)
    spare = source.with_name('.%s.%s.retaining' % (source.name, uuid.uuid4().hex[:12]))
    os.rename(source, spare)
    moved = os.lstat(spare)
    if (moved.st_dev, moved.st_ino) != (kept.st_dev, kept.st_ino):
        # Another file arrived at source in between: it goes back, and both stay.
        os.link(spare, source)
        os.unlink(spare)
        raise ValueError(FILE_CHANGED + ' ' + str(source))
    try:
        os.unlink(spare)
    except OSError:
        # Leave the original name in place rather than only the private one.
        try:
            os.link(spare, source)
        except OSError:
            pass
        raise


def retain(root, path, approved):
    name = approved.get('keep_as')
    if not valid_name(name):
        raise ValueError(CHANGED)
    target = path.with_name(approved['keep_as'])
    if target.exists() or target.is_symlink():
        raise ValueError(FILE_CHANGED + ' ' + str(target))
    check(path, approved)
    try:
        rename_noreplace(path, target)
    except FileExistsError as error:
        raise ValueError(FILE_CHANGED + ' ' + str(target)) from error
    if not same_identity(identity(target), approved):
        # Another file had replaced the approved one just before the move: put it back.
        try:
            rename_noreplace(target, path)
        except (OSError, ValueError):
            pass
        raise ValueError(FILE_CHANGED + ' ' + str(path))
    values = read(root)
    if str(path) in values:
        del values[str(path)]
        save(Path(root) / 'logs/model-mismatches.json', values)
    return target


def restore(root, path, retained, approved, prior=None):
    """Undo retention after a failed download, preserving any newly present target."""
    if os.path.lexists(path):
        return
    check(retained, approved)
    try:
        rename_noreplace(retained, path)
    except FileExistsError:
        return
    if prior is not None and same_identity(identity(path), prior):
        values = read(root)
        values[str(path)] = prior
        save(Path(root) / 'logs/model-mismatches.json', values)
