"""Remove FreeVideo's own copies of model files the installation already holds, verified.

Two kinds of leftovers duplicate an installed model file:

* an offline package's extraction under ``.freevideo-packages``: every imported
  ZIP is unpacked there in full, and the installation then links or copies the
  files it needs;
* download leftovers: a file's staging folder under ``.freevideo-downloads`` and
  the partial copies that a failed download or local import kept beside it.

They go only once the installation holds and has verified what they could
provide: each file recorded in the installation's ledger with its hash, and
unchanged since. A model package qualifies when every file it holds is
installed that way, or when it was imported before the current installation
was set up and is therefore not waiting to be used. An environment package is
never removed: it is the offline installation itself and holds its ComfyUI,
outputs and workflows. The user's own ZIP files are not touched here
(``archives`` reports them, and ``delete_archives`` removes them on request).
Released bytes count only data no other name still links to.
"""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

from .installation_cleanup import local_path

PACKAGES = '.freevideo-packages'
STAGES = '.freevideo-downloads'
INNER = 'FreeVideo-Windows'
MODELS_MARKER = 'offline-package.json'
# A model package's own inventory: read to assemble the installation, never installed itself.
PACK_INVENTORY = 'models/model-pack.json'
RUNTIME_MARKER = 'runtime.json'
# network.retain_partial and local_models.import_file: <name>[.partial].<reason>-<time_ns>
RETAINED = re.compile(r'(?P<name>.+?)(?:\.partial)?\.(?:hash-rejected|oversized-response|oversized|resume-unavailable|'
                      r'restart-approved|rejected|switch-retained|local-import)-\d{6,}')
STAGE = re.compile(r'(?P<sha>[0-9a-f]{16})-[A-Za-z0-9_-]+(?:\.(?:restart-approved|switch-retained|rejected)-\d{6,})?')


def unique_bytes(path):
    """Bytes that deleting path frees: regular files no other name links to."""
    path, total = Path(path), 0
    entries = [(path.parent, [], [path.name])] if path.is_file() else os.walk(path, followlinks=False)
    for directory, _, files in entries:
        for name in files:
            try:
                info = os.lstat(os.path.join(directory, name))
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                total += info.st_size
    return total


def runtime_root(engine):
    """The offline installation's root, when this engine belongs to one."""
    root = Path(engine).parent
    return root if (root / 'portable.json').is_file() else None


def verified_files(engine):
    """sha256 -> installed files recorded with that hash and unchanged since."""
    engine, found = Path(engine), {}

    def add(path, record):
        if not isinstance(record, dict) or not isinstance(record.get('sha256'), str):
            return
        try:
            info = os.stat(path)
        except OSError:
            return
        if not stat.S_ISREG(info.st_mode) or info.st_size != record.get('bytes'):
            return
        if record.get('mtime_ns') is not None and info.st_mtime_ns != record['mtime_ns']:
            return
        if record.get('inode') and info.st_ino != record['inode']:
            return
        found.setdefault(record['sha256'], []).append(Path(path).absolute())

    ledger = engine / 'verified-models.json'
    rows = load(ledger)
    for path, record in (rows.items() if isinstance(rows, dict) else ()):
        add(path, record)
    root = runtime_root(engine)
    if root is not None:
        rows = load(engine / 'portable-verified.json')
        for path, record in (rows.items() if isinstance(rows, dict) else ()):
            if not Path(path).is_absolute() and '..' not in Path(path).parts:
                add(root / path, record)
    return found


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def installed_at(engine):
    """When the current installation was last set up; packages imported later may be waiting for it."""
    engine, times = Path(engine), []
    for path in (engine / 'machine.json', engine.parent / 'portable.json'):
        try:
            times.append(path.stat().st_mtime)
        except OSError:
            pass
    return max(times) if times else None


def package_roots(engine, comfy_root=None):
    """Folders an import may have unpacked into: the destination beside the engine, the ComfyUI folder,
    and, for an offline installation, the destination holding its own package folder."""
    engine = Path(engine).absolute()
    roots = [engine.parent]
    if comfy_root:
        roots += [Path(comfy_root).absolute(), Path(comfy_root).absolute().parent]
    roots += [parent.parent for parent in engine.parents if parent.name == PACKAGES]
    found = []
    for root in roots:
        folder = root / PACKAGES
        if folder.is_dir() and folder not in found:
            found.append(folder)
    return found


def inside(path, folder):
    try:
        Path(path).absolute().relative_to(Path(folder).absolute())
        return True
    except ValueError:
        return False


def discard(path, root, running=None, dry_run=False):
    """Rename, then delete; returns the bytes freed, or None when the path was kept.

    A dry run applies every check and reports what would be freed, deleting nothing.
    """
    from .version_cleanup import started_from, plain_tree
    path, root = Path(path), Path(root)
    if not local_path(root, path) or started_from(path, running) or (path.is_dir() and not plain_tree(path)):
        return None
    freed = unique_bytes(path)
    if dry_run:
        return freed
    retired = path.with_name(path.name + '.retired-' + uuid.uuid4().hex[:8])
    try:
        os.rename(path, retired)
    except OSError:
        return None
    if retired.is_dir():
        shutil.rmtree(retired, ignore_errors=True)
    else:
        try:
            retired.unlink()
        except OSError:
            pass
    return freed if not retired.exists() else max(0, freed - unique_bytes(retired))


def packages(folder):
    for pack in sorted(Path(folder).iterdir()) if Path(folder).is_dir() else ():
        if not pack.is_dir() or '.retired-' in pack.name:
            continue
        inner = pack / INNER
        if (inner / RUNTIME_MARKER).is_file() or (inner / 'portable.json').is_file():
            yield pack, 'runtime', None
        else:
            value = load(inner / MODELS_MARKER)
            yield pack, 'models', value if isinstance(value, dict) else None


def prune_packages(engine, comfy_root=None, index=None, running=None, dry_run=False):
    """Model package extractions the installation no longer needs."""
    result = dict(released_bytes=0, removed=[], kept=[])
    index = verified_files(engine) if index is None else index
    installed = installed_at(engine)
    holders = [path for paths in index.values() for path in paths]
    for folder in package_roots(engine, comfy_root):
        for pack, kind, value in packages(folder):
            if kind == 'runtime' or inside(engine, pack):
                result['kept'].append(dict(kind='offline-package', path=str(pack), reason='environment package'))
                continue
            if any(inside(path, pack) for path in holders):
                result['kept'].append(dict(kind='offline-package', path=str(pack), reason='installed files live here'))
                continue
            rows = [row for row in (value or {}).get('files', [])
                    if isinstance(row, dict) and row.get('path') != PACK_INVENTORY
                    and (pack / INNER / str(row.get('path', ''))).is_file()]
            covered = bool(rows) and all(row.get('sha256') in index for row in rows)
            try:
                stale = installed is not None and max((pack / INNER).stat().st_mtime, pack.stat().st_mtime) < installed
            except OSError:
                stale = installed is not None
            if not covered and not stale:
                result['kept'].append(dict(kind='offline-package', path=str(pack), reason='imported after this installation'))
                continue
            freed = discard(pack, folder, running, dry_run)
            if freed is None:
                result['kept'].append(dict(kind='offline-package', path=str(pack), reason='in use'))
                continue
            result['released_bytes'] += freed
            result['removed'].append(dict(kind='offline-package', path=str(pack), bytes=freed,
                                          reason='installed and verified' if covered else 'not used by this installation'))
    return result


def prune_downloads(engine, index=None, running=None, dry_run=False):
    """Staging folders and kept partial copies of model files that are installed and verified."""
    result = dict(released_bytes=0, removed=[], kept=[])
    index = verified_files(engine) if index is None else index
    engine = Path(engine).absolute()
    top = runtime_root(engine) or engine
    prefixes = {sha[:16] for sha in index}
    stages, siblings = set(), {}
    for sha, paths in index.items():
        for path in paths:
            siblings.setdefault(path.parent, set()).add(path.name)
            for parent in path.parents:
                if not inside(parent, top):
                    break
                if (parent / STAGES).is_dir():
                    stages.add(parent / STAGES)
    for folder in sorted(stages):
        for stage in sorted(folder.iterdir()):
            match = STAGE.fullmatch(stage.name)
            if not match or match['sha'] not in prefixes:
                continue
            freed = discard(stage, folder, running, dry_run)
            if freed is None:
                result['kept'].append(dict(kind='download-leftover', path=str(stage), reason='in use'))
            else:
                result['released_bytes'] += freed
                result['removed'].append(dict(kind='download-leftover', path=str(stage), bytes=freed))
    for directory, names in siblings.items():
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            match = RETAINED.fullmatch(entry.name)
            if not match or match['name'] not in names:
                continue
            freed = discard(entry, directory, running, dry_run)
            if freed is None:
                result['kept'].append(dict(kind='download-leftover', path=str(entry), reason='in use'))
            else:
                result['released_bytes'] += freed
                result['removed'].append(dict(kind='download-leftover', path=str(entry), bytes=freed))
    return result


def copied_models(engine):
    """Model files imported from a library the installation could not link to, as (bytes, files)."""
    rows = load(Path(engine) / 'verified-models.json')
    copied = [record for record in (rows.values() if isinstance(rows, dict) else ())
              if isinstance(record, dict) and record.get('method') == 'verified_local_copy']
    return sum(int(record.get('bytes') or 0) for record in copied), len(copied)


def copy_reason(engine):
    """Why imports were copied: 'exfat' when the installation's own disk cannot hold links, else 'other-disk'."""
    from .local_models import volume_filesystem
    filesystem = volume_filesystem(engine)
    return 'exfat' if filesystem and filesystem != 'NTFS' else 'other-disk'


def run(engine, comfy_root=None, running=None, dry_run=False):
    index = verified_files(engine)
    result = dict(released_bytes=0, removed=[], kept=[], dry_run=dry_run)
    for part in (prune_packages(engine, comfy_root, index, running, dry_run),
                 prune_downloads(engine, index, running, dry_run)):
        result['released_bytes'] += part['released_bytes']
        result['removed'] += part['removed']
        result['kept'] += part['kept']
    result['copied_model_bytes'], result['copied_model_files'] = copied_models(engine)
    result['copied_model_reason'] = copy_reason(engine) if result['copied_model_bytes'] else None
    return result


OPTIONAL_PREFIX = 'models/base/sampling-cache/'


def archive_unchanged(record):
    try:
        info = os.stat(record['path'])
    except (OSError, KeyError, TypeError):
        return False
    return stat.S_ISREG(info.st_mode) and info.st_size == record.get('bytes') and info.st_mtime_ns == record.get('mtime_ns')


def archive_rows(record):
    """The files a recorded ZIP provides: from its extraction, or else from the ZIP's own record."""
    value = load(Path(record['root']) / MODELS_MARKER)
    if not isinstance(value, dict):
        try:
            from .offline_packages import inspect_archive
            value = inspect_archive(record['path'])
        except Exception:  # A ZIP that cannot be read is never offered.
            return None
    return [row for row in value.get('files', []) if isinstance(row, dict) and row.get('path') != PACK_INVENTORY]


def archives(records, engine, index=None):
    """The recorded ZIPs whose contents this installation holds, verified: dict(bytes, paths)."""
    index = verified_files(engine) if index is None else index
    runtime = runtime_root(engine)
    paths, total = [], 0
    for record in records:
        if not archive_unchanged(record):
            continue
        if record.get('kind') == 'runtime':
            # The environment package was unpacked into this very installation.
            held = runtime is not None and Path(record['root']).resolve() == runtime.resolve()
        else:
            rows = archive_rows(record)

            def installed(row):
                if row.get('sha256') in index:
                    return True
                # Optional sampling tables an offline installation placed itself: present at full size.
                placed = runtime / str(row.get('path', '')) if runtime is not None else None
                return (placed is not None and str(row.get('path', '')).startswith(OPTIONAL_PREFIX)
                        and placed.is_file() and placed.stat().st_size == row.get('bytes'))
            held = bool(rows) and all(installed(row) for row in rows)
        if held:
            paths.append(record['path'])
            total += record['bytes']
    return dict(bytes=total, paths=paths)


def delete_archives(records, engine):
    """Delete the user's imported ZIPs this installation holds; returns (bytes freed, paths removed)."""
    offer = archives(records, engine)
    freed, removed = 0, []
    for path in offer['paths']:
        try:
            info = os.lstat(path)
            os.unlink(path)
        except OSError:
            continue  # In use or read-only: kept, offered again next time.
        removed.append(path)
        freed += info.st_size if info.st_nlink == 1 else 0
    return freed, removed
