"""Copies of a file that did not pass: at most the newest one stays, none once the right file is in place.

``network.retain_partial`` (and a few renames of the same kind) keep a whole file or a prefix that failed its
check, as ``<file>[.partial].<reason>-<tag>``. Each failure used to leave one more, and nothing removed them, so
a file that failed again and again filled the disk with whole copies of itself.

One rule, applied only at the two moments the code knows the state:

* a new copy has just been kept: the older ones of the same file go, the newest stays for diagnosis;
* the right file has just passed its sha256: every copy of it goes.

Only the exact name of the file the caller is working on is ever matched (never a glob over a folder), a link is
never followed, and a file that has another hard link is left alone: removing it would change the installed file's
change time (the link count is on the file, not on the name).
"""
import os
from pathlib import Path
import re
import stat

# What network.retain_partial is called with; ``rejected`` is also what a few callers rename by hand.
# Not here, so never removed by this module: ``set-aside``, a file that was already on the install path and is not the
# pinned one (it may be the user's own), moved aside before the right one is fetched.
REASONS = ('hash-rejected', 'rejected', 'oversized-response', 'oversized', 'restart-approved', 'switch-retained')
# The tag is the time in nanoseconds (retain_partial), ticks (Windows scripts) or a uuid (cache folders).
_TAG = r'(?P<tag>[0-9a-f]{6,})'
_SUFFIX = re.compile(r'(?:\.partial)?\.(?:%s)-%s' % ('|'.join(REASONS), _TAG))


def _junction(path):
    """A Windows directory junction (os.walk and Path.is_dir follow it like a folder, and it is not a symlink)."""
    try:
        from .runtime_alias import is_junction
        return bool(is_junction(Path(path)))
    except Exception:
        return False


def base_name(path):
    """The file a retained name belongs to: the name without a trailing ``.partial``."""
    name = Path(path).name
    return name[:-len('.partial')] if name.endswith('.partial') else name


def copies(path):
    """Copies of the file at path that did not pass, newest first. Never follows a link."""
    path = Path(path)
    name = base_name(path)
    found = []
    try:
        entries = list(path.parent.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.startswith(name):
            continue
        match = _SUFFIX.fullmatch(entry.name[len(name):])
        if not match:
            continue
        try:
            info = entry.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            continue
        tag = match['tag']
        # Newest first by the time the copy was last written (names carry nanoseconds, ticks or a uuid, which do not compare).
        found.append((info.st_mtime_ns, int(tag) if tag.isdigit() else 0, entry.name, entry))
    return [row[3] for row in sorted(found, reverse=True)]


def size_of(entry):
    """Bytes a copy holds (a folder: its regular files)."""
    entry = Path(entry)
    try:
        if entry.is_file():
            return entry.lstat().st_size
        total = 0
        for directory, folders, files in os.walk(entry, followlinks=False):
            folders[:] = [name for name in folders if not _junction(os.path.join(directory, name))]
            for name in files:
                try:
                    info = os.lstat(os.path.join(directory, name))
                except OSError:
                    continue
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
        return total
    except OSError:
        return 0


def _clear(folder):
    """Remove what a copy's folder holds that may go: regular files with one link and the folders emptied by that.
    Links and junctions are never followed or removed; what cannot go stays, and so does the folder around it."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        member = os.path.join(folder, name)
        try:
            info = os.lstat(member)
            if stat.S_ISREG(info.st_mode):
                if info.st_nlink == 1:
                    os.unlink(member)
            elif stat.S_ISDIR(info.st_mode) and not _junction(member):
                _clear(member)
                os.rmdir(member)
        except OSError:
            pass


def _remove(entry):
    """Delete one copy; True when it is gone. A file with another hard link, or a link, stays."""
    entry = Path(entry)
    try:
        info = entry.lstat()
        if stat.S_ISREG(info.st_mode):
            if info.st_nlink > 1:
                return False
            entry.unlink()
            return True
        if not stat.S_ISDIR(info.st_mode) or _junction(entry):
            return False
        _clear(entry)
        entry.rmdir()
        return True
    except OSError:
        return False


def prune(path, keep=1):
    """Remove the copies of the file at path beyond the newest `keep`. Returns what went, as event rows.

    Only called when a new copy was kept (keep=1) or the right file has passed its sha256 (keep=0)."""
    removed = []
    for entry in copies(path)[keep:]:
        size = size_of(entry)
        if _remove(entry):
            removed.append(dict(copy=entry.name, bytes=size))
    return removed


def describe(entry, observed=None, algorithm='sha256'):
    """What a diagnostic report may say about a kept copy: its name, size, time and the digest it actually had.

    The name only, never a folder: a path would carry the user's home directory."""
    entry = Path(entry)
    row = dict(copy=entry.name, bytes=size_of(entry))
    match = _SUFFIX.search(entry.name)
    if match and match['tag'].isdigit() and len(match['tag']) >= 16:
        row['kept_ns'] = int(match['tag'])
    if observed:
        row.update(observed_digest=observed, algorithm=algorithm)
    return row
