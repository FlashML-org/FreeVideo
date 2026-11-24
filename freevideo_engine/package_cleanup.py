"""Remove FreeVideo's own copies of model files the installation already holds, verified.

Two kinds of leftovers duplicate an installed model file:

* an offline package's extraction under ``.fv`` (or the older ``.freevideo-packages``): every imported
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

from .installation_cleanup import local_path, root_path
from .long_paths import io_path, resolved, plain
from .runtime_alias import is_junction, checked_real_path as _checked_real_path, AliasUnavailable
from .system import windows
from .package_layout import (CONTAINERS, PACKAGES as SHORT_PACKAGES, LEGACY_PACKAGES,
                             LEGACY_INNER, inner_root, package_id, redirected, VERIFIED, layout_name,
                             check_boundaries)

# Compatibility names for callers constructing a legacy package path.
PACKAGES = LEGACY_PACKAGES
STAGES = '.freevideo-downloads'
INNER = LEGACY_INNER
MODELS_MARKER = 'offline-package.json'
# A model package's own inventory: read to assemble the installation, never installed itself.
PACK_INVENTORY = 'models/model-pack.json'
RUNTIME_MARKER = 'runtime.json'
# network.retain_partial and local_models.import_file: <name>[.partial].<reason>-<time_ns>
RETAINED = re.compile(r'(?P<name>.+?)(?:\.partial)?\.(?:hash-rejected|oversized-response|oversized|resume-unavailable|'
                      r'restart-approved|rejected|switch-retained|local-import)-\d{6,}')
STAGE = re.compile(r'(?P<sha>[0-9a-f]{16})-[A-Za-z0-9_-]+(?:\.(?:restart-approved|switch-retained|rejected)-\d{6,})?')


def checked_real_path(path):
    # Reject package redirects before callers resolve away that boundary.
    try:
        check_boundaries(path)
    except ValueError as error:
        raise AliasUnavailable('invalid', error) from error
    return _checked_real_path(path)


def unique_bytes(path):
    """Bytes that deleting path frees: regular files no other name links to."""
    try:
        path = io_path(checked_real_path(path))
        if path.is_dir():
            path = resolved(path)
    except AliasUnavailable:
        return 0
    total = 0
    entries = [(path.parent, [], [path.name])] if path.is_file() else os.walk(path, followlinks=False)
    for directory, folders, files in entries:
        # os.walk(followlinks=False) still follows Windows directory junctions.
        folders[:] = [name for name in folders if not is_junction(Path(directory) / name)]
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
    try:
        root = resolved(checked_real_path(engine)).parent
    except AliasUnavailable:
        return None
    return Path(plain(root)) if (root / 'portable.json').is_file() else None


def verified_files(engine):
    """sha256 -> installed files recorded with that hash and unchanged since."""
    found = {}
    try:
        engine = resolved(checked_real_path(engine))
    except AliasUnavailable:
        return found

    def add(path, record):
        try:
            path = checked_real_path(path)
        except AliasUnavailable:
            return
        if not isinstance(record, dict) or not isinstance(record.get('sha256'), str):
            return
        try:
            info = os.stat(io_path(path))
        except OSError:
            return
        if not stat.S_ISREG(info.st_mode) or info.st_size != record.get('bytes'):
            return
        if record.get('mtime_ns') is not None and info.st_mtime_ns != record['mtime_ns']:
            return
        if record.get('inode') and info.st_ino != record['inode']:
            return
        found.setdefault(record['sha256'], []).append(Path(plain(io_path(path).absolute())))

    ledger = engine / 'verified-models.json'
    rows = load(ledger)
    for path, record in (rows.items() if isinstance(rows, dict) else ()):
        add(path, record)
    root = runtime_root(engine)
    if root is not None:
        rows = load(engine / 'portable-verified.json')
        for path, record in (rows.items() if isinstance(rows, dict) else ()):
            if not Path(path).is_absolute() and '..' not in Path(path).parts:
                add(io_path(root) / path, record)
    return found


def load(path):
    try:
        path = checked_real_path(path)
        return json.loads(io_path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def installed_at(engine):
    """When the current installation was last set up; packages imported later may be waiting for it."""
    times = []
    try:
        engine = resolved(checked_real_path(engine))
    except AliasUnavailable:
        return None
    for path in (engine / 'machine.json', engine.parent / 'portable.json'):
        try:
            times.append(path.stat().st_mtime)
        except OSError:
            pass
    return max(times) if times else None


def package_roots(engine, comfy_root=None):
    """Folders an import may have unpacked into: the destination beside the engine, the ComfyUI folder,
    and, for an offline installation, the destination holding its own package folder."""
    try:
        engine = checked_real_path(engine)
        comfy_root = checked_real_path(comfy_root) if comfy_root else None
    except AliasUnavailable:
        return []
    engine = resolved(engine)
    comfy_root = resolved(comfy_root) if comfy_root else None
    roots = [engine.parent]
    if comfy_root:
        roots += [io_path(comfy_root).absolute(), io_path(comfy_root).absolute().parent]
    roots += [parent.parent for parent in engine.parents if layout_name(parent.name) in CONTAINERS]
    found = []
    for root in roots:
        for name in CONTAINERS:
            folder = root / name
            ordinary = Path(plain(folder))
            if (ordinary not in found and folder.is_dir()
                    and not redirected(folder)
                    and (not windows() or local_path(root, folder))):
                found.append(ordinary)
    return found


def inside(path, folder):
    try:
        io_path(checked_real_path(path)).absolute().relative_to(io_path(checked_real_path(folder)).absolute())
        return True
    except (OSError, ValueError):
        return False


def discard(path, root, running=None, dry_run=False):
    """Rename, then delete; returns the bytes freed, or None when the path was kept.

    A dry run applies every check and reports what would be freed, deleting nothing.
    """
    from .version_cleanup import started_from, plain_tree
    try:
        path, root = checked_real_path(path), checked_real_path(root)
        # Refuse container redirects before resolving erases the package boundary.
        if (redirected(path) or (layout_name(io_path(root).name) in CONTAINERS and redirected(root))):
            return None
        root, path = root_path(root, path)
    except AliasUnavailable:
        return None
    if ('.replaced-' in path.name or not local_path(root, path) or started_from(path, running)
            or (path.is_dir() and not plain_tree(path))):
        return None
    if only_linked_files(path):
        # Every file here is another name of a file kept elsewhere (an import linked it into the installation):
        # removing it frees nothing, and on Windows it changes that file's NTFS ChangeTime, which a generation
        # reading the installed copy treats as the file having changed.
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
        # Only the files no other name links to: a linked one stays under the retired name (packages() skips it).
        for directory, file in list(unlinked_files(retired)):
            try:
                os.unlink(os.path.join(directory, file))
            except OSError:
                pass
        for directory, _, _ in sorted(os.walk(retired, followlinks=False), key=lambda row: len(row[0]), reverse=True):
            try:
                os.rmdir(directory)
            except OSError:
                pass
    else:
        try:
            retired.unlink()
        except OSError:
            pass
    return freed if not retired.exists() else max(0, freed - unique_bytes(retired))


def finish_remnants(folder, running=None, dry_run=False):
    """What an earlier discard left under a retired name: files that then had another name (an installed model).
    Each that has no other name now goes, since removing a sole name changes no other file; emptied folders go
    with them. Returns [(remnant, bytes freed)] for the remnants that freed something or are gone."""
    from .version_cleanup import started_from, plain_tree
    try:
        folder = io_path(checked_real_path(folder))
    except AliasUnavailable:
        return []
    if not folder.is_dir() or (layout_name(folder.name) in CONTAINERS and redirected(folder)):
        return []
    done = []
    for remnant in sorted(folder.iterdir()):
        if ('.retired-' not in remnant.name or not remnant.is_dir() or redirected(remnant)
                or not local_path(folder, remnant) or started_from(remnant, running) or not plain_tree(remnant)):
            continue
        freed = 0
        for directory, name in list(unlinked_files(remnant)):
            path = os.path.join(directory, name)
            try:
                size = os.lstat(path).st_size
                if not dry_run:
                    os.unlink(path)
                freed += size
            except OSError:
                continue
        if not dry_run:
            for directory, _, _ in sorted(os.walk(remnant, followlinks=False), key=lambda row: len(row[0]), reverse=True):
                try:
                    os.rmdir(directory)
                except OSError:
                    pass
        if freed or not remnant.exists():
            done.append((remnant, freed))
    return done


def only_linked_files(path):
    """True when path holds files and every one of them has another name."""
    path = io_path(path)
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return info.st_nlink > 1
    seen = False
    for directory, folders, files in os.walk(path, followlinks=False):
        folders[:] = [name for name in folders if not is_junction(Path(directory) / name)]
        for name in files:
            try:
                if os.lstat(os.path.join(directory, name)).st_nlink == 1:
                    return False
            except OSError:
                return False
            seen = True
    return seen


def unlinked_files(path):
    """(directory, name) of each file below path (or path itself) that no other name links to."""
    path = io_path(path)
    try:
        info = os.lstat(path)
    except OSError:
        return
    if not stat.S_ISDIR(info.st_mode):
        if info.st_nlink == 1:
            yield str(path.parent), path.name
        return
    for directory, folders, files in os.walk(path, followlinks=False):
        # os.walk(followlinks=False) still follows Windows directory junctions.
        folders[:] = [name for name in folders if not is_junction(Path(directory) / name)]
        for name in files:
            try:
                if os.lstat(os.path.join(directory, name)).st_nlink == 1:
                    yield directory, name
            except OSError:
                continue


def packages(folder):
    try:
        folder = checked_real_path(folder)
    except AliasUnavailable:
        return
    if layout_name(io_path(folder).name) in CONTAINERS and redirected(folder):
        return
    for pack in sorted(io_path(folder).iterdir()) if io_path(folder).is_dir() else ():
        if (not pack.is_dir() or redirected(pack) or '.retired-' in pack.name
                or (windows() and not local_path(io_path(folder), pack))):
            continue
        inner = inner_root(pack)
        if redirected(inner) or (windows() and not local_path(pack, inner)):
            continue
        # An occupied prefix may be someone else's directory. Only our
        # manifest/identity receipt establishes ownership of a new extraction.
        if layout_name(io_path(folder).name) == SHORT_PACKAGES and package_id(inner) is None:
            continue
        if (inner / RUNTIME_MARKER).is_file() or (inner / 'portable.json').is_file():
            yield Path(plain(pack)), 'runtime', None
        else:
            value = load(inner / MODELS_MARKER)
            if layout_name(io_path(folder).name) == SHORT_PACKAGES and value is None and not own_contents(pack):
                continue  # A bare identity cannot vouch for somebody else's files.
            yield Path(plain(pack)), 'models', value if isinstance(value, dict) else None


def own_contents(pack, value=None):
    """Keep the whole package when any file/directory is outside its inventory."""
    pack, inner = io_path(pack), inner_root(pack)
    if any(redirected(path) for path in (pack.parent, pack, inner)):
        return False
    names = {MODELS_MARKER, VERIFIED, '模型包使用说明.txt'}
    references = {}
    try:
        if value is not None:
            from .offline_packages import inventory
            if (not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('kind') != 'models'
                    or not isinstance(value.get('files'), list) or not value['files']):
                return False
            inventory(value['files'])
            if any(not row['path'].startswith('models/') for row in value['files']):
                return False
            paths = [row['path'] for row in value['files']]
        else:
            receipt = load(inner / VERIFIED)
            references = receipt.get('installed_files', {})
            if not isinstance(references, dict):
                return False
            from .portable import bundle_name
            for name, runtime in references.items():
                bundle_name(name)
                record = receipt.get(name)
                if (not name.startswith('models/') or not isinstance(runtime, str)
                        or not isinstance(record, list) or len(record) not in (2, 3)
                        or any(type(part) is not int or part < 0 for part in record[:2])
                        or (len(record) == 3 and not re.fullmatch(r'[0-9a-f]{64}', str(record[2])))):
                    return False
            paths = [name for name in receipt if name not in ('package_id', 'installed_files')
                     and name not in references]
            for name in paths:
                record = receipt[name]
                from .portable import bundle_name
                bundle_name(name)
                info = (inner / name).stat()
                if (not isinstance(record, list) or len(record) not in (2, 3)
                        or record[:2] != [info.st_size, info.st_mtime_ns]
                        or (len(record) == 3 and not re.fullmatch(r'[0-9a-f]{64}', str(record[2])))):
                    return False
        from .portable import bundle_name
        for name in paths:
            bundle_name(name)
            names.update((name, name + '.importing'))
        directories = {inner.name}
        files = {inner.name + '/' + name for name in names}
        for name in files | {inner.name + '/' + name for name in references}:
            directories.update(str(parent).replace(os.sep, '/') for parent in Path(name).parents if str(parent) != '.')
        def unreadable(error):
            raise error
        for directory, folders, children in os.walk(pack, followlinks=False, onerror=unreadable):
            for name in [*folders, *children]:
                entry = io_path(directory) / name
                relative = entry.relative_to(pack).as_posix()
                if redirected(entry) or relative not in (directories if entry.is_dir() else files):
                    return False
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def prune_packages(engine, comfy_root=None, index=None, running=None, dry_run=False):
    """Model package extractions the installation no longer needs."""
    from .locking import runtime_lock
    result = dict(released_bytes=0, removed=[], kept=[])
    try:
        engine = checked_real_path(engine)
        if comfy_root:
            comfy_root = checked_real_path(comfy_root)
    except AliasUnavailable:
        result['kept'].append(dict(kind='installation', reason='invalid runtime alias'))
        return result
    engine = resolved(engine)
    index = verified_files(engine) if index is None else index
    installed = installed_at(engine)
    holders = [io_path(checked_real_path(path)) for paths in index.values() for path in paths]
    for folder in package_roots(engine, comfy_root):
        try:
            with runtime_lock(io_path(folder) / 'import.lock', inherit=False):
                for pack, kind, value in packages(folder):
                    pack = io_path(pack)
                    inner = inner_root(pack)
                    if kind == 'runtime' or inside(engine, pack):
                        result['kept'].append(dict(kind='offline-package', path=plain(pack), reason='environment package'))
                        continue
                    if any(inside(path, pack) for path in holders):
                        result['kept'].append(dict(kind='offline-package', path=plain(pack), reason='installed files live here'))
                        continue
                    receipt = load(inner / VERIFIED)
                    references = receipt.get('installed_files', {}) if isinstance(receipt, dict) else {}
                    references = references if isinstance(references, dict) else {}
                    rows = [row for row in (value or {}).get('files', [])
                            if isinstance(row, dict) and row.get('path') != PACK_INVENTORY
                            and ((inner / str(row.get('path', ''))).is_file() or row.get('path') in references)]
                    covered = bool(rows) and all(row.get('sha256') in index for row in rows)
                    try:
                        stale = installed is not None and max(inner.stat().st_mtime, pack.stat().st_mtime) < installed
                    except OSError:
                        stale = installed is not None
                    if not covered and not stale:
                        result['kept'].append(dict(kind='offline-package', path=plain(pack), reason='imported after this installation'))
                        continue
                    # Like retained originals, unlisted user content keeps the entire layer.
                    if not own_contents(pack, value):
                        result['kept'].append(dict(kind='offline-package', path=plain(pack), reason='unlisted files'))
                        continue
                    freed = discard(pack, folder, running, dry_run)
                    if freed is None:
                        result['kept'].append(dict(kind='offline-package', path=plain(pack), reason='in use'))
                        continue
                    result['released_bytes'] += freed
                    result['removed'].append(dict(kind='offline-package', path=plain(pack), bytes=freed,
                                                  reason='installed and verified' if covered else 'not used by this installation'))
                # Files an earlier pass left in place because an installed model shared them, once that name is gone.
                for remnant, freed in finish_remnants(folder, running, dry_run):
                    result['released_bytes'] += freed
                    result['removed'].append(dict(kind='offline-package', path=plain(remnant), bytes=freed,
                                                  reason='no longer linked to the installation'))
        except BlockingIOError:
            result['kept'].append(dict(kind='offline-package', path=plain(folder), reason='in use'))
    return result


def prune_downloads(engine, index=None, running=None, dry_run=False):
    """Staging folders and kept partial copies of model files that are installed and verified."""
    result = dict(released_bytes=0, removed=[], kept=[])
    try:
        engine = resolved(checked_real_path(engine))
    except AliasUnavailable:
        result['kept'].append(dict(kind='installation', reason='invalid runtime alias'))
        return result
    index = verified_files(engine) if index is None else index
    engine = io_path(checked_real_path(engine)).absolute()
    top = runtime_root(engine) or engine
    prefixes = {sha[:16] for sha in index}
    stages, siblings = set(), {}
    for sha, paths in index.items():
        for path in paths:
            path = io_path(checked_real_path(path))
            siblings.setdefault(path.parent, set()).add(path.name)
            for parent in path.parents:
                if not inside(parent, top):
                    break
                if (parent / STAGES).is_dir() and (not windows() or local_path(top, parent / STAGES)):
                    stages.add(parent / STAGES)
    for folder in sorted(stages):
        for remnant, freed in finish_remnants(folder, running, dry_run):
            result['released_bytes'] += freed
            result['removed'].append(dict(kind='download-leftover', path=plain(remnant), bytes=freed))
        for stage in sorted(folder.iterdir()):
            match = STAGE.fullmatch(stage.name)
            if not match or match['sha'] not in prefixes:
                continue
            freed = discard(stage, folder, running, dry_run)
            if freed is None:
                result['kept'].append(dict(kind='download-leftover', path=plain(stage), reason='in use'))
            else:
                result['released_bytes'] += freed
                result['removed'].append(dict(kind='download-leftover', path=plain(stage), bytes=freed))
    for directory, names in siblings.items():
        if windows() and not local_path(top, directory):
            continue
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
                result['kept'].append(dict(kind='download-leftover', path=plain(entry), reason='in use'))
            else:
                result['released_bytes'] += freed
                result['removed'].append(dict(kind='download-leftover', path=plain(entry), bytes=freed))
    return result


def copied_models(engine):
    """Model files imported from a library the installation could not link to, as (bytes, files)."""
    try:
        engine = checked_real_path(engine)
    except AliasUnavailable:
        return 0, 0
    rows = load(io_path(engine) / 'verified-models.json')
    copied = [record for record in (rows.values() if isinstance(rows, dict) else ())
              if isinstance(record, dict) and record.get('method') == 'verified_local_copy']
    return sum(int(record.get('bytes') or 0) for record in copied), len(copied)


def copy_reason(engine):
    """Why imports were copied: 'exfat' when the installation's own disk cannot hold links, else 'other-disk'."""
    engine = checked_real_path(engine)
    from .local_models import volume_filesystem
    filesystem = volume_filesystem(engine)
    return 'exfat' if filesystem and filesystem != 'NTFS' else 'other-disk'


def run(engine, comfy_root=None, running=None, dry_run=False):
    result = dict(released_bytes=0, removed=[], kept=[], dry_run=dry_run)
    try:
        engine = checked_real_path(engine)
        comfy_root = checked_real_path(comfy_root) if comfy_root else None
    except AliasUnavailable:
        result['kept'].append(dict(kind='installation', reason='invalid runtime alias'))
        result.update(copied_model_bytes=0, copied_model_files=0, copied_model_reason=None)
        return result
    engine = resolved(engine)
    index = verified_files(engine)
    for part in (prune_packages(engine, comfy_root, index, running, dry_run),
                 prune_downloads(engine, index, running, dry_run)):
        result['released_bytes'] += part['released_bytes']
        result['removed'] += part['removed']
        result['kept'] += part['kept']
    result['copied_model_bytes'], result['copied_model_files'] = copied_models(engine)
    result['copied_model_reason'] = copy_reason(engine) if result['copied_model_bytes'] else None
    return result


def archive_unchanged(record):
    try:
        path = checked_real_path(record['path'])
        if not path.is_absolute() or path.suffix.lower() != '.zip' or not local_path(Path(path.anchor), path):
            return False
        info = os.lstat(io_path(path))
    except (OSError, KeyError, TypeError, ValueError):
        return False
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_size == record.get('bytes') and info.st_mtime_ns == record.get('mtime_ns'))


def archive_rows(record):
    """The files a recorded ZIP provides: from its extraction, or else from the ZIP's own record."""
    try:
        value = load(io_path(checked_real_path(record['root']))
                     / (RUNTIME_MARKER if record.get('kind') == 'runtime' else MODELS_MARKER))
    except AliasUnavailable:
        return None
    if not isinstance(value, dict):
        try:
            from .offline_packages import inspect_archive
            value = inspect_archive(checked_real_path(record['path']))
        except Exception:  # A ZIP that cannot be read is never offered.
            return None
    return [row for row in value.get('files', []) if isinstance(row, dict) and row.get('path') != PACK_INVENTORY]


def archives(records, engine, index=None):
    """The recorded ZIPs whose contents this installation holds, verified: dict(bytes, paths)."""
    try:
        engine = checked_real_path(engine)
    except AliasUnavailable:
        return dict(bytes=0, paths=[])
    index = verified_files(engine) if index is None else index
    runtime = runtime_root(engine)
    runtime = io_path(runtime) if runtime is not None else None
    paths, files, total = [], [], 0
    for record in records:
        try:
            checked_real_path(record['root'])
        except AliasUnavailable:
            continue
        try:
            named = plain(checked_real_path(record['path']))
        except (OSError, KeyError, TypeError, ValueError):
            continue
        # One ZIP is one offer, whichever spelling its records carry.
        if record.get('kind') not in ('runtime', 'models') or named in paths or not archive_unchanged(record):
            continue
        rows = archive_rows(record)
        held = bool(rows) and all(row.get('sha256') in index for row in rows)
        if record.get('kind') == 'runtime':
            # Root identity alone is insufficient: the current environment must
            # still hold every imported file with its verified hash and stamp.
            try:
                held = held and runtime is not None and resolved(checked_real_path(record['root'])) == resolved(runtime)
            except AliasUnavailable:
                continue
        if held:
            try:
                real = checked_real_path(record['path'])
                info = os.lstat(io_path(real))
            except OSError:
                continue
            if not archive_unchanged(record):
                continue
            paths.append(plain(real))
            total += record['bytes']
            files.append(dict(path=plain(real), bytes=record['bytes'], mtime_ns=record['mtime_ns'],
                              inode=info.st_ino, device=info.st_dev))
    return dict(bytes=total, paths=paths, files=files)


def failure_reason(error):
    """A failure code the launcher words for the user; the raw error stays in detail."""
    if isinstance(error, ValueError):
        return 'changed'
    if isinstance(error, BlockingIOError):
        return 'busy'  # Setup or a video holds the installation's lease.
    if isinstance(error, PermissionError):
        return 'in-use'  # Windows reports a file open elsewhere as access denied.
    return 'failed'


def delete_archives(records, engine, *, offered=None):
    """Revalidate imported ZIPs, then delete; return (freed bytes, removed paths, failures).

    Each failure is dict(path, reason, detail): reason is 'changed', 'busy',
    'in-use' or 'failed' (failure_reason). A ZIP already gone counts as removed.
    """
    from .locking import runtime_lock
    engine = io_path(checked_real_path(engine))
    offer = archives(records, engine) if offered is None else offered
    freed, removed, failed = 0, [], []
    by_path = {}
    for item in records:
        try:
            by_path[plain(checked_real_path(item['path']))] = item
        except (AliasUnavailable, KeyError, TypeError):
            continue
    try:
        # Exclude setup/generation from other launchers as well as this UI.
        with runtime_lock(engine / 'setup.lock', inherit=False), \
                runtime_lock(engine / 'engine.lock', inherit=False), \
                runtime_lock(engine / 'launcher' / 'host-setup.lock', inherit=False):
            current = set(archives(records, engine)['paths'])
            for row in offer['files']:
                path = row['path']
                try:
                    if not os.path.lexists(io_path(path)):
                        removed.append(path)  # Deleted elsewhere meanwhile: nothing left to free.
                        continue
                    record = by_path.get(path)
                    if path not in current or not record or not archive_unchanged(record):
                        # A ZIP another program holds open cannot be read to verify it:
                        # that is "in use", not "changed".
                        with open(io_path(path), 'rb'):
                            pass
                        raise ValueError('File or installed contents changed; kept for safety.')
                    info = os.lstat(io_path(path))
                    if (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev) != (
                            row['bytes'], row['mtime_ns'], row['inode'], row['device']):
                        raise ValueError('File changed since the confirmation; kept for safety.')
                    os.unlink(io_path(path))
                except (OSError, ValueError) as error:
                    failed.append(dict(path=path, reason=failure_reason(error), detail=str(error)))
                    continue
                removed.append(path)
                # A name that was linked after the check frees nothing.
                freed += info.st_size if info.st_nlink == 1 else 0
    except (OSError, ValueError) as error:
        failed.extend(dict(path=row['path'], reason=failure_reason(error), detail=str(error)) for row in offer['files']
                      if row['path'] not in removed and row['path'] not in {r['path'] for r in failed})
    return freed, removed, failed
