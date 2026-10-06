"""User-requested cleanup of this installation's disposable package downloads.

Never walks model folders, environments, outputs or imported offline packages.
uv owns its cache format and locking; removal of that cache goes through uv.
"""
import os
from pathlib import Path
import stat
import sys
import threading

from .locking import runtime_lock
from .storage import fingerprint


def local_path(root, path):
    """Refuse redirected cache roots, including Windows junctions."""
    try:
        path.relative_to(root)
        current = path
        while current != root:
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                return False
            current = current.parent
        return True
    except (OSError, ValueError):
        return False


def cache_bytes(path):
    """Estimate reclaimable storage, counting cache-only hardlinks just once."""
    inodes = {}
    def unreadable(error):
        raise error
    for directory, folders, files in os.walk(path, followlinks=False, onerror=unreadable):
        for name in [*folders, *files]:
            item = Path(directory) / name
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode):
                # uv uses internal symlinks. External redirects are not ours.
                if not item.resolve().is_relative_to(path):
                    raise ValueError('Cache contains an external link; retained.')
                if name in folders:
                    folders.remove(name)
            elif getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Cache contains a redirected folder; retained.')
            elif stat.S_ISREG(info.st_mode):
                key = info.st_dev, info.st_ino
                row = inodes.setdefault(key, [info.st_size, info.st_nlink, 0])
                row[2] += 1
    return sum(size for size, links, inside in inodes.values() if links <= inside)


def uv_path(root):
    relative = ('uv.exe' if os.name == 'nt' else 'uv' if sys.platform == 'darwin'
                else 'uv-x86_64-unknown-linux-gnu/uv')
    path = root / 'tools' / relative
    return path if local_path(root, path) and path.is_file() else None


def scan(root):
    from .torch_download import catalog
    root = Path(root).absolute().resolve()
    plan = dict(root=str(root), cache=None, wheels=[], bytes=0)
    cache = root / 'downloads' / 'uv-cache'
    if uv_path(root) and local_path(root, cache) and cache.is_dir():
        try:
            size = cache_bytes(cache)
        except (OSError, ValueError):
            pass  # Uncertain ownership is not permission to delete it.
        else:
            if size:
                plan['cache'] = dict(bytes=size, path=str(cache))
                plan['bytes'] += size
    for row in catalog():
        path = root / 'downloads' / 'torch-wheels' / row['filename']
        if not local_path(root, path):
            continue
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != row['bytes']:
            continue
        stamp = fingerprint(path)
        if 'change_time_ns' in stamp and stamp['change_time_ns'] is None:
            continue
        plan['wheels'].append(dict(path=str(path), stamp=stamp, bytes=info.st_size))
        plan['bytes'] += info.st_size
    return plan


def clean(plan):
    from . import processes
    from .torch_download import catalog
    root = Path(plan['root'])
    released = skipped = 0
    # Other launchers and CLI installers also hold these leases. Do not race
    # package installation, generation or a resumed download from another UI.
    with runtime_lock(root / 'setup.lock', inherit=False), \
            runtime_lock(root / 'engine.lock', inherit=False), \
            runtime_lock(root / 'launcher' / 'host-setup.lock', inherit=False):
        cache = plan.get('cache')
        if cache:
            path, uv = root / 'downloads' / 'uv-cache', uv_path(root)
            if str(path) != cache['path'] or not uv or not local_path(root, path):
                raise ValueError('Package cache changed; check storage again.')
            before = cache_bytes(path)  # Recheck redirects before invoking uv.
            result = processes.run([str(uv), 'cache', 'clean', '--cache-dir', str(path)],
                                   capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
            if result.returncode:
                raise RuntimeError(result.stderr or 'Package cache cleanup did not complete.')
            after = cache_bytes(path) if path.exists() else 0
            released += max(0, before - after)
        allowed = {str(root / 'downloads' / 'torch-wheels' / row['filename'])
                   for row in catalog()}
        for row in plan['wheels']:
            path = Path(row['path'])
            try:
                if (str(path) not in allowed or not local_path(root, path) or path.stat().st_nlink != 1
                        or fingerprint(path) != row['stamp']):
                    skipped += 1
                    continue
                path.unlink()
                released += row['bytes']
            except OSError:
                skipped += 1
    return dict(released_bytes=released, skipped=skipped)


class Cleaner:
    def __init__(self):
        self.state = dict(status='idle', bytes=0, error='')
        self.thread = None
        self.plan = None

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, root, *, remove=False):
        if self.busy:
            return
        root = str(Path(root).absolute().resolve())
        if remove and (not self.plan or self.plan['root'] != root):
            raise ValueError('Check storage in the selected installation first.')
        plan = self.plan
        self.state = dict(status='cleaning' if remove else 'scanning', bytes=0, error='')
        def work():
            from .failure_details import redacted_launcher_error
            try:
                if remove:
                    result = clean(plan)
                    self.plan = None
                    self.state = dict(status='complete', bytes=0, error='', **result)
                else:
                    self.plan = scan(root)
                    self.state = dict(status='ready', bytes=self.plan['bytes'], error='')
            except BlockingIOError:
                self.state = dict(status='busy', bytes=0, error='')
            except Exception as error:
                self.state = dict(status='error', bytes=0, error=redacted_launcher_error(str(error)))
        # If the window closes, finish the current tool operation and release
        # its leases before Python exits; do not abandon a half-cleaned cache.
        self.thread = threading.Thread(target=work, name='freevideo-storage')
        self.thread.start()
