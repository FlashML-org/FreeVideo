"""Find pinned models in an existing library without changing that library."""
import json
import os
from pathlib import Path
import stat
import time

from .download_cache import discard_read_cache
from .storage import fingerprint

BLOCK = 4 * 2**20
MAX_ENTRIES = 200_000
MAX_CANDIDATES = 2048
MAX_ROOTS = 64
MAX_LISTED = 4096  # Paths accepted before duplicates and nested folders are merged.


def library_roots(values, *, keep_first=False):
    """Normalize selected libraries without walking entire drives.

    The 64-library limit counts distinct folders: a folder named twice, or one
    inside another listed folder, counts once. With keep_first, a list found
    automatically keeps its first 64 folders instead of failing.
    """
    if not isinstance(values, list) or len(values) > MAX_LISTED:
        raise ValueError('Invalid model library list')
    roots = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            raise ValueError('Invalid model library path')
        path = Path(value).expanduser().resolve()
        if not path.is_dir() or path == Path(path.anchor):
            raise ValueError('Choose an existing model folder, not an entire drive: ' + str(path))
        if path not in roots:
            roots.append(path)
    # Explicit symlink/junction roots are resolved above. Internal directory
    # links are still skipped by files(); never follow an arbitrary graph.
    roots = [path for path in roots if not any(parent in roots for parent in path.parents)]
    if len(roots) > MAX_ROOTS:
        if not keep_first:
            raise ValueError('Select at most 64 model libraries')
        roots = roots[:MAX_ROOTS]
    return [str(path) for path in roots]


def scan_many(directories, rows, *, prior=None, callback=None):
    roots = library_roots(directories)
    result = dict(root=roots[0] if roots else None, roots=roots, matches={},
                  candidate_files=0, rejected_count=0, rejected=[], reused_bytes=0)
    for directory in roots:
        remaining = [row for row in rows if key(row) not in result['matches']]
        if not remaining:
            break
        previous = dict(root=directory, matches=(prior or {}).get('matches', {}))
        found = scan(directory, remaining, prior=previous, callback=callback)
        result['matches'].update(found['matches'])
        result['candidate_files'] += found['candidate_files']
        result['rejected_count'] += found['rejected_count']
        result['rejected'] = (result['rejected'] + found['rejected'])[:20]
        result['reused_bytes'] += found['reused_bytes']
    return result


def key(row):
    return row['repo'] + '/' + row['file']


def expected(row):
    return row.get('sha256') or row['git_blob']


def can_link(source, destination_parent):
    """A read-only plan: never create a probe file before consent."""
    source_device = Path(source).stat().st_dev
    if not source_device or source_device != Path(destination_parent).stat().st_dev:
        return False
    if os.name != 'nt':
        return True
    return volume_filesystem(destination_parent) == 'NTFS'


def volume_filesystem(path):
    """The file system of path's Windows volume (NTFS, exFAT, FAT32...), or None elsewhere or when unknown."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR, wintypes.DWORD]
    volume, filesystem = ctypes.create_unicode_buffer(32768), ctypes.create_unicode_buffer(64)
    if not kernel.GetVolumePathNameW(str(path), volume, len(volume)):
        return None
    if not kernel.GetVolumeInformationW(volume.value, None, 0, None, None, None, filesystem, len(filesystem)):
        return None
    return filesystem.value.upper()


def progress_output(value):
    print(json.dumps(dict(event='local_model_progress', **value)), flush=True)


class Progress:
    def __init__(self, callback=None):
        self.callback = callback
        self.started = self.tick = time.monotonic()

    def send(self, phase, path='', done=0, total=0, *, force=False):
        now = time.monotonic()
        if self.callback and (force or now-self.tick >= .5):
            elapsed = now-self.started
            self.callback(dict(phase=phase, file=Path(path).name if path else '', done_bytes=done,
                total_bytes=total, elapsed_seconds=elapsed,
                bytes_per_second=done/elapsed if elapsed and total else None))
            self.tick = now


def hash_source(path, row, progress=None, output=None):
    """Bounded RAM hashing, optionally copying in the same sequential read."""
    import hashlib
    before = fingerprint(path)
    if before['bytes'] != row['bytes'] or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Local model size/type changed: ' + str(path))
    digest = hashlib.new('sha256' if row.get('sha256') else 'sha1')
    if not row.get('sha256'):
        digest.update(('blob %d\0' % row['bytes']).encode())
    done = 0
    phase = 'copy' if output is not None else 'verify'
    progress = progress or Progress()
    progress.started = time.monotonic()
    progress.send(phase, path, total=row['bytes'], force=True)
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(BLOCK), b''):
            if done+len(block) > row['bytes']:
                raise ValueError('Local model grew while being read: ' + str(path))
            digest.update(block)
            if output is not None:
                output.write(block)
            discard_read_cache(stream.fileno(), done, len(block))
            done += len(block)
            progress.send(phase, path, done, row['bytes'])
    if fingerprint(path) != before or done != row['bytes']:
        raise ValueError('Local model changed while being read: ' + str(path))
    progress.send(phase, path, done, row['bytes'], force=True)
    return digest.hexdigest(), before


def files(directory, progress):
    seen = 0
    def failed(error):
        raise OSError('Cannot read part of the selected model folder; choose an accessible folder') from error
    for root, directories, names in os.walk(directory, followlinks=False, onerror=failed):
        kept = []
        for name in sorted(directories):
            path = Path(root) / name
            info = path.lstat()
            if (name not in ('.git', '.venv', 'envs', 'node_modules', '__pycache__')
                    and not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400):
                kept.append(name)
        directories[:] = kept
        seen += len(names) + len(directories)
        if seen > MAX_ENTRIES:
            raise ValueError('Too many files to scan. Select a narrower model folder.')
        progress.send('scan', done=seen)
        for name in sorted(names):
            path = Path(root) / name
            try:
                # HF snapshot file symlinks are valid; directory links/junctions
                # are not traversed. Later reads use the resolved regular file.
                resolved = path.resolve(strict=True)
                info = resolved.stat()
                if stat.S_ISREG(info.st_mode):
                    yield resolved, info
            except (OSError, RuntimeError):
                continue


def scan(directory, rows, *, prior=None, callback=None):
    directory = Path(directory).expanduser().resolve()
    if not rows:
        return dict(root=str(directory), matches={}, candidate_files=0, rejected_count=0, rejected=[], reused_bytes=0)
    if not directory.is_dir():
        raise ValueError('Existing model folder is missing or unreadable: ' + str(directory))
    progress = Progress(callback)
    progress.send('scan', force=True)
    by_size = {}
    for row in rows:
        by_size.setdefault(row['bytes'], []).append(row)
    candidates, seen = [], set()
    for path, info in files(directory, progress):
        identity = (info.st_dev, info.st_ino) if info.st_ino else str(path)
        if info.st_size in by_size and identity not in seen:
            candidates.append(path)
            seen.add(identity)
            if len(candidates) > MAX_CANDIDATES:
                raise ValueError('Too many possible models. Select a narrower model folder.')
    # Prefer expected names/known HF blobs; still verify the actual content.
    names = {Path(row['file']).name for row in rows} | {expected(row) for row in rows}
    candidates.sort(key=lambda path: (path.name not in names, str(path)))
    previous = (prior or {}).get('matches', {}) if (prior or {}).get('root') == str(directory) else {}
    old_paths = {value.get('source'): value for value in previous.values() if isinstance(value, dict)}
    matches, rejected = {}, []
    for path in candidates:
        current = fingerprint(path)
        options = [row for row in by_size.get(current['bytes'], []) if key(row) not in matches]
        if not options:
            continue
        # One file can match multiple identical configs. Hash once per algorithm.
        computed = {}
        old = old_paths.get(str(path), {})
        for row in options:
            algorithm = 'sha256' if row.get('sha256') else 'git_blob'
            if algorithm not in computed:
                reliable = ('change_time_ns' not in current or current['change_time_ns'] is not None)
                settled = max(current['mtime_ns'], current.get('change_time_ns') or current['ctime_ns']) < time.time_ns()-10**9
                if (reliable and settled and old.get('fingerprint') == current
                        and old.get('digest') == expected(row) and old.get('algorithm') == algorithm):
                    computed[algorithm] = (old['digest'], current)
                else:
                    computed[algorithm] = hash_source(path, row, progress)
            digest, stamp = computed[algorithm]
            if digest == expected(row):
                matches[key(row)] = dict(source=str(path), fingerprint=stamp, digest=digest,
                                         algorithm=algorithm, bytes=row['bytes'])
        if not any(record['source'] == str(path) for record in matches.values()):
            rejected.append(dict(file=path.name, reason='Content does not match this engine revision'))
    progress.send('ready', done=len(rows), total=len(rows), force=True)
    return dict(root=str(directory), matches=matches, candidate_files=len(candidates),
                rejected_count=len(rejected), rejected=rejected[:20],
                reused_bytes=sum(record['bytes'] for record in matches.values()))


def import_file(row, target, record, *, callback=None):
    """Create an installation-owned reference/copy; never modify the source."""
    source, target = Path(record['source']), Path(target)
    if record.get('digest') != expected(row) or record.get('bytes') != row['bytes']:
        raise ValueError('Local model selection no longer matches the pinned manifest')
    progress = Progress(callback)
    current = fingerprint(source)
    if current != record.get('fingerprint'):
        digest, current = hash_source(source, row, progress)
        if digest != expected(row):
            raise ValueError('Selected local model changed; inspect the folder again: ' + str(source))
    if target.exists() or target.is_symlink():
        raise ValueError('Model destination already exists; both files retained: ' + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.local-import-' + str(time.time_ns()))
    method = record['method']
    if method == 'hardlink':
        try:
            os.link(source, temporary)
        except OSError as error:
            raise OSError('Cannot link the selected model on this filesystem. Enable "Copy existing models" and inspect the plan again; source retained.') from error
        # Link creation changes ctime/link count, but must not change bytes or
        # mtime. A later setup verification still checks the imported file.
        linked = fingerprint(temporary)
        if any(linked[field] != current[field] for field in ('device', 'inode', 'bytes', 'mtime_ns')):
            raise ValueError('Local model changed while linking; both files retained')
        digest, _ = hash_source(temporary, row, progress)
        if digest != expected(row):
            raise ValueError('Local linked model failed verification; source and reference retained')
        progress.send('link', source, row['bytes'], row['bytes'], force=True)
    elif method == 'copy':
        from .download_cache import DownloadCache
        with DownloadCache(lambda: [temporary]):
            with temporary.open('xb') as output:
                digest, _ = hash_source(source, row, progress, output)
                output.flush()
                os.fsync(output.fileno())
        if digest != expected(row):
            raise ValueError('Local copy failed integrity verification; source and partial copy retained')
    else:
        raise ValueError('Unknown local model import method')
    # On Windows rename refuses an existing destination. On Linux the setup
    # lease excludes another engine installer; never intentionally overwrite.
    if target.exists() or target.is_symlink():
        raise ValueError('Model destination appeared during import; files retained')
    from .file_ops import publish
    publish(temporary, target)
    return method
