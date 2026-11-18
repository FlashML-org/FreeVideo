"""Release this download's disk cache without deleting retained data.

Linux file cache is outside process PSS. Sync in a separate thread so slow disks
do not stop the RAM/cancellation monitor. This is advisory, not a kernel cap.
"""
import os
from pathlib import Path
import re
import stat
import threading

MiB = 1 << 20


def mount_type(path, mountinfo=Path('/proc/self/mountinfo')):
    if os.name != 'posix' or not mountinfo.is_file():
        return None
    target = Path(path).resolve()
    matches = []
    try:
        lines = mountinfo.read_text(encoding='utf-8').splitlines()
        for line in lines:
            before, after = line.split(' - ', 1)
            mount = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), before.split()[4]))
            if target == mount or mount in target.parents:
                matches.append((len(mount.parts), after.split()[0]))
    except (OSError, ValueError, IndexError):
        return None  # Sandboxed or restricted /proc: the mount type is advisory, so the download proceeds.
    return max(matches)[1] if matches else None


def discard_read_cache(fd, offset, length):
    """Drop only already-read clean pages; never flush or alter file contents."""
    if hasattr(os, 'posix_fadvise'):
        try:
            os.posix_fadvise(fd, offset, length, os.POSIX_FADV_DONTNEED)
        except OSError:
            pass  # Some filesystems do not implement this advisory operation.


class DownloadCache:
    def __init__(self, paths, window_bytes=256 * MiB, interval=.5):
        self.paths = paths
        self.window_bytes = window_bytes
        self.interval = interval
        self.enabled = hasattr(os, 'posix_fadvise') and hasattr(os, 'fdatasync')
        self.stop = threading.Event()
        self.thread = None
        self.error = None
        self.synced = {}
        self.syncs = self.advised_bytes = 0

    def sweep(self, force=False):
        pending, changed = 0, []
        for path in self.paths():
            try:
                current = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or current.st_size < MiB:
                continue
            key = (current.st_dev, current.st_ino)
            stamp = (current.st_size, current.st_mtime_ns)
            previous = self.synced.get(key, (0, 0))
            if force or stamp != previous:
                # Xet can preallocate then write out of order without growth.
                pending += max(0, stamp[0] - previous[0]) or self.window_bytes
                changed.append((path, key, stamp))
        if not force and pending < self.window_bytes:
            return
        for path, key, stamp in changed:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                continue  # SDK parts may have been merged and removed.
            try:
                current = os.fstat(fd)
                if ((current.st_dev, current.st_ino) != key or current.st_nlink != 1
                        or not stat.S_ISREG(current.st_mode)):
                    continue
                os.fdatasync(fd)  # DONTNEED alone does not release dirty pages.
                discard_read_cache(fd, 0, current.st_size)
                self.synced[key] = stamp
                self.syncs += 1
                self.advised_bytes += current.st_size
            finally:
                os.close(fd)

    def check(self):
        if self.error is not None:
            raise OSError('Cannot flush model download to disk; retained files are unchanged') from self.error

    def __enter__(self):
        if self.enabled:
            def run():
                try:
                    while not self.stop.is_set():
                        self.sweep()
                        self.stop.wait(self.interval)
                    self.sweep(force=True)
                except Exception as error:
                    self.error = error
            self.thread = threading.Thread(target=run, name='model-disk-cache', daemon=True)
            self.thread.start()
        return self

    def __exit__(self, kind, value, traceback):
        self.stop.set()
        if self.thread is not None:
            # The outer setup UI/monitor keeps running while disk writes finish.
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                raise OSError('Model disk writeback did not finish within 30 seconds; files retained')
        if kind is None:
            self.check()

    def result(self):
        return dict(enabled=self.enabled, window_bytes=self.window_bytes, syncs=self.syncs,
                    advised_bytes=self.advised_bytes,
                    error_type=type(self.error).__name__ if self.error is not None else None,
                    scope='Owned download files only; writeback and cache advice, not a hard cache limit')


def transfer_paths(path, row):
    """Only this file's native stages and streaming attempts; never symlinks."""
    path = Path(path)
    base = path
    for _ in Path(row['file']).parts:
        base = base.parent
    identity = (row.get('sha256') or row['git_blob'])[:16]
    stages = base / '.freevideo-downloads'
    if stages.is_dir() and not stages.is_symlink():
        for stage in stages.glob(identity + '-*'):
            if not stage.is_dir() or stage.is_symlink():
                continue
            for root, directories, files in os.walk(stage):
                directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
                for name in files:
                    yield Path(root) / name
    if path.parent.is_dir():
        yield path
        yield from path.parent.glob(path.name + '.partial*')
