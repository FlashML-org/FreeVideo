"""The online LoRA folders this installer builds beside a prepared model, and when they can go.

prepare() in lora_online_cache names a folder after the digest of its identity
(model manifest, the code that builds it, every adapter and strength), so each
new combination, strength or code update leaves another folder. Each holds the
adapter factors it built and hard links to the model's unchanged groups.

A folder is ours only when its name is the digest it was built under, its
manifest names it, and every file is regular and listed by that manifest.
Removal takes exactly those files: a link only drops its count, so the model
it points at is never touched, and unknown files stay.

A folder is stale once nothing can select it again - its model manifest or
the code that built it has changed - or nobody has used it for a long time.
Use is recorded as the manifest's modification time.
"""
import json
import os
from pathlib import Path
import re
import stat
import time

from .long_paths import io_path, plain, plain_errors

NAME = re.compile(r'vdn-online-lora-[0-9a-f]{24}')
LORA_FILE = re.compile(r'lora/(root|\d{2})\.(safetensors|partial)')
ADALN_FILE = re.compile(r'adaln-tables-v2-[0-9a-f]{24}/(identity|producer)\.json|adaln-tables-v2-[0-9a-f]{24}/\d{2}\.(safetensors|json)')
MARKER = 'manifest.json'
MARKER_LIMIT = 64 << 20
IDLE_DAYS = 30
EXPIRED_IDLE_DAYS = 1
REPARSE_POINT = 0x400


def redirected(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & REPARSE_POINT)


def safe_relative(value):
    return (isinstance(value, str) and value and '\\' not in value and ':' not in value
            and not value.startswith('/') and all(part not in ('', '.', '..') for part in value.split('/')))


@plain_errors
def listing(beside):
    """Real folders of this naming below beside, never through a link."""
    beside = io_path(beside)
    try:
        found = sorted(path for path in beside.iterdir() if NAME.fullmatch(path.name))
    except OSError:
        return []
    return [Path(plain(path)) for path in found if not redirected(path.lstat()) and path.is_dir()]


@plain_errors
def manifest(folder):
    """The folder's manifest when it names this folder as its own, else None."""
    marker = io_path(folder) / MARKER
    try:
        info = marker.lstat()
        if redirected(info) or not stat.S_ISREG(info.st_mode) or info.st_size > MARKER_LIMIT:
            return None
        value = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if (not isinstance(value, dict) or not isinstance(value.get('lora_identity'), dict)
            or not isinstance(value.get('online_lora'), dict)
            or str(value.get('source_id', ''))[:24] != Path(folder).name[len('vdn-online-lora-'):]):
        return None
    return value


def listed_names(value):
    """Relative names the manifest of a folder lists: groups, shared AdaLN tables and itself."""
    names = {MARKER}
    for group in value.get('groups', []):
        if isinstance(group, dict) and safe_relative(group.get('file')):
            names.add(group['file'])
    for table in value.get('adaln_tables', []):
        for row in table.get('files', []) if isinstance(table, dict) else []:
            if isinstance(row, dict) and safe_relative(row.get('file')):
                names.add(row['file'])
    return names


@plain_errors
def owned_files(folder):
    """Regular files of one folder that are ours, or [] when the folder is not ours."""
    value = manifest(folder)
    if value is None:
        return []
    names = listed_names(value)
    folder = io_path(folder)
    found = []
    for current, directories, files in os.walk(folder, followlinks=False):
        for name in list(directories):
            if redirected((Path(current) / name).lstat()):
                directories.remove(name)
        for name in files:
            path = Path(current) / name
            relative = path.relative_to(folder).as_posix()
            try:
                info = path.lstat()
            except OSError:
                continue
            if redirected(info) or not stat.S_ISREG(info.st_mode):
                continue
            if relative in names or LORA_FILE.fullmatch(relative):
                found.append(Path(plain(path)))
    return found


def permitted(relative, group_names):
    """A planned name below an online folder: its manifest, an adapter file, a model group or an AdaLN table."""
    return (relative == MARKER or LORA_FILE.fullmatch(relative) is not None
            or ADALN_FILE.fullmatch(relative) is not None or relative in group_names)


def home(path):
    """The online folder a path lies in, or None."""
    return next((parent for parent in Path(path).parents if NAME.fullmatch(parent.name)), None)


def last_used(folder):
    try:
        return (io_path(folder) / MARKER).stat().st_mtime
    except OSError:
        return None


def touch(folder):
    """Record a use; never an error."""
    try:
        os.utime(io_path(folder) / MARKER)
    except OSError:
        pass


def stale(folder, current, now=None):
    """Why this folder can go, or None. current holds the digests a folder built today would carry."""
    value = manifest(folder)
    used = last_used(folder)
    if value is None or used is None:
        return None
    idle = ((now if now is not None else time.time()) - used) / 86400
    identity = value['lora_identity']
    expired = any(identity.get(key) != digest for key, digest in current.items())
    if expired and idle >= EXPIRED_IDLE_DAYS:
        return 'code or model changed'
    if idle >= IDLE_DAYS:
        return 'unused for %d days' % IDLE_DAYS
    return None


@plain_errors
def remove(folder):
    """Remove one owned folder's files, then its empty directories. Bytes released by files that had no other link."""
    folder = io_path(folder)
    freed, left = 0, False
    # The manifest is what makes the folder ours, so it goes last, and only when every other file went.
    for path in sorted(owned_files(folder), key=lambda p: p.relative_to(Path(plain(folder))).as_posix() == MARKER):
        if path.name == MARKER and path.parent == Path(plain(folder)) and left:
            continue
        try:
            info = io_path(path).lstat()
            io_path(path).unlink()
        except OSError:
            left = True
            continue
        freed += info.st_size if info.st_nlink == 1 else 0
    for current, directories, files in os.walk(folder, topdown=False, followlinks=False):
        try:
            Path(current).rmdir()
        except OSError:
            pass
    return freed


@plain_errors
def prune(beside, current, keep=None, now=None):
    """Remove stale folders beside a prepared model, except keep. Returns (folder, reason, bytes) rows."""
    removed = []
    for folder in listing(beside):
        if keep is not None and Path(plain(folder)) == Path(plain(keep)):
            continue
        reason = stale(folder, current, now)
        if reason:
            removed.append((plain(folder), reason, remove(folder)))
    return removed
