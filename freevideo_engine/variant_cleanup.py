"""Remove a retired prepared model variant once its replacement is in use.

After an installation moves to another prepared variant (FP8 to int8), the old
variant's files are no longer read. Removal is limited to what this installer
placed there, one file at a time:

* each pinned catalog file of a variant that is not in use, at its catalogued
  size, regular and unshared. LoRA variants hardlink their unchanged groups, so
  a shared file stays with the LoRA that still uses it;
* that file's download sidecar, which must name the same SHA-256, and its
  interrupted partial downloads;
* that file's native staging folder below .freevideo-downloads;
* AdaLN tables the engine generated in the variant's cache folder, only when
  the folder's name is the digest of its identity marker and it holds nothing
  but table files.

* the online LoRA folders beside its cache that this installer built (see
  online_lora_folders): they hardlink the model's groups, so they are removed
  first and the model files then count only their own link;

A variant with fused LoRA variants beside its cache is kept whole: int8 cannot
merge those adapters yet, so the old model is still the only way to use them.

Every path must lie inside the installation and be reached without symbolic
links or junctions. Everything else in the folder (LoRA variants, user files)
is reported and kept, and a directory is removed only once it is empty. The
scan records a fingerprint of every file; removal requires it unchanged, under
the setup, engine and host-setup leases, with the replacement complete and in
use. Uncertain ownership is never permission to delete.
"""
import json
import os
from pathlib import Path
import re
import stat
import time

from .long_paths import io_path, plain, plain_errors
from . import online_lora_folders
from .locking import runtime_lock
from .storage import fingerprint
from .runtime_alias import checked_real_path, AliasUnavailable

SIDECAR = '.partial.source.json'
SIDECAR_LIMIT = 4096
STAGES = '.freevideo-downloads'
SOURCE = re.compile(r'[a-z0-9][a-z0-9-]{0,31}')
FUSED_LORA = 'vdn-fp8-lora-'
PARTIAL = re.compile(r'\.partial(-\d{1,20})?')
ADALN_DIRECTORY = re.compile(r'adaln-tables-v2-[0-9a-f]{24}')
ADALN_FILE = re.compile(r'identity\.json|producer\.json|\d{2}\.(safetensors|json)')
ADALN_IDENTITY_LIMIT = 65536
REPARSE_POINT = 0x400


def redirected(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & REPARSE_POINT)


@plain_errors
def inside(base, path):
    """True when path is base or below it and nothing between them is a link or junction."""
    base, path = io_path(base), io_path(path)
    try:
        path.relative_to(base)
        current = path
        while True:
            if redirected(current.lstat()):
                return False
            if current == base:
                return True
            current = current.parent
    except (OSError, ValueError):
        return False


@plain_errors
def unshared_file(path, links=1):
    """The file's stat when it is a regular file with exactly this many links (its own, by default)."""
    path = io_path(path)
    if '.replaced-' in path.name:
        return None
    try:
        info = path.lstat()
    except OSError:
        return None
    if redirected(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != links:
        return None
    return info


def variant_folder(root, catalog, name):
    variant = catalog['variants'][name]
    return root / 'prepared' / ('edge-' + variant.get('revision', catalog['revision'])[:16])


@plain_errors
def active_variant(root, catalog):
    """The catalog variant whose cache machine.json names, or None when that is unknown."""
    root = io_path(root)
    try:
        record = json.loads((root / 'machine.json').read_text(encoding='utf-8'))
        cache = io_path(record['cache'])
    except (OSError, ValueError, KeyError, TypeError):
        return None, None
    for name, variant in catalog['variants'].items():
        expected = variant_folder(root, catalog, name) / variant['cache_prefix']
        try:
            if cache.absolute() == expected or cache.resolve() == expected.resolve():
                return name, Path(plain(expected))
        except OSError:
            continue
    return None, Path(plain(cache))


@plain_errors
def complete(root, catalog, name):
    """Every catalog file of the variant is present, regular, local and of its catalogued size."""
    root = io_path(root)
    folder = variant_folder(root, catalog, name)
    prepared = root / 'prepared'
    for row in catalog['variants'][name]['files']:
        path = folder / row['file']
        try:
            info = path.lstat()
        except OSError:
            return False
        if redirected(info) or not stat.S_ISREG(info.st_mode) or info.st_size != row['bytes'] or not inside(prepared, path):
            return False
    return True


@plain_errors
def _sidecar_matches(path, row):
    path = io_path(path)
    info = unshared_file(path)
    if info is None or info.st_size > SIDECAR_LIMIT:
        return False
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(value, dict) and value.get('expected') == row['sha256']


@plain_errors
def _stage(folder, prepared, stage):
    """Files and directories of one native staging folder, or None when anything is redirected or shared."""
    prepared, stage = io_path(prepared), io_path(stage)
    if not inside(prepared, stage) or not stage.is_dir():
        return None
    files, directories = [], [stage]
    for current, names, entries in os.walk(stage, followlinks=False):
        for name in names:
            path = Path(current) / name
            try:
                if redirected(path.lstat()):
                    return None
            except OSError:
                return None
            directories.append(path)
        for name in entries:
            path = Path(current) / name
            if unshared_file(path) is None:
                return None
            files.append(path)
    return [Path(plain(path)) for path in files], [Path(plain(path)) for path in directories]


@plain_errors
def _generated_tables(prepared, table, links=None):
    """Files of one engine-generated AdaLN table folder, or None unless it is bound to its identity."""
    from .adaln_assets import FORMAT, directory
    prepared, table = io_path(prepared), io_path(table)
    if not ADALN_DIRECTORY.fullmatch(table.name) or not inside(prepared, table) or not table.is_dir():
        return None
    entries = list(table.iterdir())
    links = links or (lambda path: 1)
    if any(not ADALN_FILE.fullmatch(entry.name) or unshared_file(entry, links(entry)) is None for entry in entries):
        return None
    marker = table / 'identity.json'
    info = unshared_file(marker, links(marker))
    if info is None or info.st_size > ADALN_IDENTITY_LIMIT:
        return None
    try:
        identity = json.loads(marker.read_text(encoding='utf-8'))
        if not isinstance(identity, dict) or identity.get('format') != FORMAT or directory(identity) != table.name:
            return None
    except (OSError, ValueError, TypeError):
        return None
    return [Path(plain(path)) for path in entries]


@plain_errors
def _regular(path):
    try:
        info = io_path(path).lstat()
    except OSError:
        return False
    return not redirected(info) and stat.S_ISREG(info.st_mode)


@plain_errors
def _plan_file(path, kind, entries, linked=False):
    path = io_path(path)
    entries.append(dict(path=plain(path), kind=kind, bytes=path.lstat().st_size, stamp=fingerprint(path)))
    if linked:
        entries[-1]['linked'] = True


def _same(entry, now):
    """The file is as planned. Removing another link to the same data changes its change time, which a linked file may do."""
    if not entry.get('linked'):
        return now == entry['stamp']
    ignored = ('ctime_ns', 'change_time_ns')
    return ({k: v for k, v in now.items() if k not in ignored}
            == {k: v for k, v in entry['stamp'].items() if k not in ignored})


@plain_errors
def retire(root, catalog, name):
    """Owned files of one retired variant, and what stays in its folder."""
    root = io_path(root)
    folder = variant_folder(root, catalog, name)
    prepared = root / 'prepared'
    entries, kept, planned, stage_directories = [], [], set(), []
    if not folder.is_dir() or not inside(prepared, folder):
        return dict(variant=name, folder=plain(folder), files=entries, kept=kept, bytes=0, kept_bytes=0,
                    stage_directories=stage_directories, present=folder.exists() or folder.is_symlink())
    stages = folder / STAGES
    # Online LoRA folders go first; each hard link they hold on a model file is
    # one more link that file may carry and still count as unshared.
    peers = {}
    beside = (folder / catalog['variants'][name]['cache_prefix']).parent
    online_files = []
    if inside(prepared, beside):
        for online in online_lora_folders.listing(beside):
            for path in online_lora_folders.owned_files(online):
                try:
                    info = io_path(path).lstat()
                except OSError:
                    continue
                if info.st_ino:
                    peers[(info.st_dev, info.st_ino)] = peers.get((info.st_dev, info.st_ino), 0) + 1
                online_files.append((path, info))
    # Each folder's manifest last: it is what makes the folder ours, so it must outlive any file that stays.
    online_files.sort(key=lambda row: (str(online_lora_folders.home(row[0])), row[0] == online_lora_folders.home(row[0]) / online_lora_folders.MARKER))
    for path, info in online_files:
        _plan_file(path, 'online-lora', entries, linked=True)
        # A link to a model file frees nothing of its own: the model file is counted once, under its own name.
        if info.st_ino and info.st_nlink > peers[(info.st_dev, info.st_ino)]:
            entries[-1]['bytes'] = 0
        planned.add(io_path(path))

    def links(path):
        """Links a file may carry: its own, plus the planned online LoRA links to the same data."""
        try:
            info = io_path(path).lstat()
        except OSError:
            return 1
        return 1 + peers.get((info.st_dev, info.st_ino), 0) if info.st_ino else 1
    for row in catalog['variants'][name]['files']:
        path = folder / row['file']
        if path.exists() or path.is_symlink():
            info = unshared_file(path, links(path))
            if info is not None and info.st_size == row['bytes'] and inside(prepared, path):
                _plan_file(path, 'catalog', entries, linked=links(path) > 1)
                planned.add(path)
        sidecar = path.with_name(path.name + SIDECAR)
        if sidecar.exists() and inside(prepared, sidecar) and _sidecar_matches(sidecar, row):
            _plan_file(sidecar, 'sidecar', entries)
            planned.add(sidecar)
        if path.parent.is_dir() and inside(prepared, path.parent):
            for candidate in path.parent.iterdir():
                suffix = candidate.name[len(path.name):]
                if (candidate.name.startswith(path.name) and PARTIAL.fullmatch(suffix)
                        and unshared_file(candidate) is not None and inside(prepared, candidate)):
                    _plan_file(candidate, 'partial', entries)
                    planned.add(candidate)
        if stages.is_dir() and inside(prepared, stages):
            for stage in stages.iterdir():
                identity, _, source = stage.name.partition('-')
                if identity != row['sha256'][:16] or not SOURCE.fullmatch(source):
                    continue
                found = _stage(folder, prepared, stage)
                if found is None:
                    continue
                for path_in_stage in found[0]:
                    path_in_stage = io_path(path_in_stage)
                    if path_in_stage not in planned:
                        _plan_file(path_in_stage, 'stage', entries)
                        planned.add(path_in_stage)
                for directory in found[1]:
                    if plain(directory) not in stage_directories:
                        stage_directories.append(plain(directory))
    cache = folder / catalog['variants'][name]['cache_prefix']
    if cache.is_dir() and inside(prepared, cache):
        for table in sorted(cache.iterdir()):
            found = _generated_tables(prepared, table, links)
            for path in found or ():
                path = io_path(path)
                if path not in planned:
                    _plan_file(path, 'adaln', entries, linked=links(path) > 1)
                    planned.add(path)
    kept_bytes = 0
    for current, names, files in os.walk(folder, followlinks=False):
        for name_ in list(names):
            path = Path(current) / name_
            try:
                if redirected(path.lstat()):
                    names.remove(name_)
                    kept.append(dict(path=plain(path), bytes=0, reason='link or junction'))
            except OSError:
                names.remove(name_)
        for name_ in files:
            path = Path(current) / name_
            if path in planned:
                continue
            try:
                size = path.lstat().st_size
            except OSError:
                size = 0
            kept_bytes += size
            kept.append(dict(path=plain(path), bytes=size, reason='not placed by this installer for this variant'))
    return dict(variant=name, folder=plain(folder), files=entries, kept=kept, present=True,
                stage_directories=stage_directories,
                bytes=sum(row['bytes'] for row in entries), kept_bytes=kept_bytes)


@plain_errors
def fused_lora_variants(root, catalog, name):
    """Fused LoRA variants built from this variant (they live beside its cache)."""
    beside = (variant_folder(io_path(root), catalog, name) / catalog['variants'][name]['cache_prefix']).parent
    try:
        return sorted(plain(path) for path in beside.iterdir() if path.name.startswith(FUSED_LORA))
    except OSError:
        return []


@plain_errors
def scan(root, catalog=None, expect_active=None):
    """What removing the variants that are not in use would release; nothing is changed.

    expect_active names the variant the caller has just switched to; a scan
    that finds any other variant in use plans nothing.
    """
    try:
        root = checked_real_path(root)
    except AliasUnavailable:
        return dict(root=plain(root), active=None, cache=None, expect_active=expect_active,
                    retired=[], kept_variants=[], bytes=0, kept_bytes=0,
                    blockers=['invalid runtime alias'])
    from .prepared_model import catalog as load_catalog
    root = io_path(Path(root).absolute())
    catalog = catalog or load_catalog()
    active, cache = active_variant(root, catalog)
    plan = dict(root=plain(root), active=active, cache=plain(cache) if cache else None, expect_active=expect_active,
                retired=[], kept_variants=[], bytes=0, kept_bytes=0, blockers=[])
    try:
        if redirected((root / 'prepared').lstat()):
            # A models folder moved elsewhere behind a link is the user's own
            # arrangement; deleting through it is not ours to decide.
            plan['blockers'].append('prepared-folder-redirected')
            return plan
    except OSError:
        pass
    if active is None:
        plan['blockers'].append('active-variant-unknown')
        return plan
    if expect_active is not None and active != expect_active:
        plan['blockers'].append('unexpected-active-variant')
        return plan
    if not complete(root, catalog, active):
        plan['blockers'].append('active-variant-incomplete')
        return plan
    active_folder = variant_folder(root, catalog, active)
    for name in catalog['variants']:
        if name == active:
            continue
        folder = variant_folder(root, catalog, name)
        if folder == active_folder:
            continue  # A shared revision folder also holds the files in use.
        if not folder.exists() and not folder.is_symlink():
            continue
        fused = fused_lora_variants(root, catalog, name)
        if fused:
            plan['kept_variants'].append(dict(variant=name, folder=plain(folder), reason='fused-lora-variants',
                                              lora_variants=fused))
            continue
        retired = retire(root, catalog, name)
        plan['retired'].append(retired)
        plan['bytes'] += retired['bytes']
        plan['kept_bytes'] += retired['kept_bytes']
    return plan


def allowed(root, catalog, name):
    """The paths removal may touch for one retired variant: catalog files and their own metadata names."""
    folder = variant_folder(root, catalog, name)
    rows = {}
    for row in catalog['variants'][name]['files']:
        rows[str(folder / row['file'])] = ('catalog', row)
        rows[str(folder / (row['file'] + SIDECAR))] = ('sidecar', row)
    return folder, rows


def permitted(path, kind, folder, rows, cache_prefix=None):
    """Recheck at removal time that a planned path still has exactly the name it was planned under."""
    if not path.is_absolute() or '..' in path.parts or Path(os.path.normpath(path)) != path:
        return False
    key = str(path)
    if kind in ('catalog', 'sidecar'):
        return key in rows and rows[key][0] == kind
    if kind == 'partial':
        for name, (row_kind, row) in rows.items():
            if row_kind == 'catalog' and key.startswith(name) and PARTIAL.fullmatch(key[len(name):]):
                return Path(name).parent == path.parent
        return False
    if kind == 'online-lora':
        if cache_prefix is None:
            return False
        beside = folder / Path(cache_prefix).parent
        try:
            relative = path.relative_to(beside)
        except ValueError:
            return False
        parts = relative.parts
        groups = {row_file[len(cache_prefix) + 1:] for row_file in (r['file'] for _, r in rows.values())
                  if row_file.startswith(cache_prefix + '/')}
        return (len(parts) >= 2 and online_lora_folders.NAME.fullmatch(parts[0]) is not None
                and online_lora_folders.permitted('/'.join(parts[1:]), groups))
    if kind == 'adaln':
        if cache_prefix is None:
            return False
        try:
            relative = path.relative_to(folder / cache_prefix)
        except ValueError:
            return False
        return (len(relative.parts) == 2 and ADALN_DIRECTORY.fullmatch(relative.parts[0]) is not None
                and ADALN_FILE.fullmatch(relative.parts[1]) is not None)
    if kind == 'stage':
        try:
            relative = path.relative_to(folder / STAGES)
        except ValueError:
            return False
        identity, _, source = relative.parts[0].partition('-') if relative.parts else ('', '', '')
        return (len(relative.parts) >= 2 and SOURCE.fullmatch(source) is not None
                and any(row_kind == 'catalog' and row['sha256'][:16] == identity for row_kind, row in rows.values()))
    return False


def stage_directory_permitted(directory, folder, rows):
    """A staging folder of a catalog file of this variant, or a directory inside one."""
    if not directory.is_absolute() or '..' in directory.parts or Path(os.path.normpath(directory)) != directory:
        return False
    try:
        relative = directory.relative_to(folder / STAGES)
    except ValueError:
        return False
    if not relative.parts:
        return False
    identity, _, source = relative.parts[0].partition('-')
    return (SOURCE.fullmatch(source) is not None
            and any(kind == 'catalog' and row['sha256'][:16] == identity for kind, row in rows.values()))


@plain_errors
def clean(plan, catalog=None, receipt=None):
    """Remove exactly the planned files that are still unchanged; report everything else."""
    # Authenticate every saved path before resolving, enumerating, or taking leases.
    # Copy the plan so refusal leaves the caller's plan unchanged as well.
    def checked(value):
        return plain(checked_real_path(value))
    plan = dict(plan, root=checked(plan['root']))
    if plan.get('cache') is not None:
        plan['cache'] = checked(plan['cache'])
    retired = []
    for row in plan['retired']:
        retired.append(dict(row, folder=checked(row['folder']),
                            files=[dict(entry, path=checked(entry['path'])) for entry in row['files']],
                            kept=[dict(entry, path=checked(entry['path'])) for entry in row.get('kept', [])],
                            stage_directories=[checked(value) for value in row.get('stage_directories', [])]))
    plan['retired'] = retired
    plan['kept_variants'] = [dict(row, folder=checked(row['folder']),
                                lora_variants=[checked(value) for value in row.get('lora_variants', [])])
                             for row in plan.get('kept_variants', [])]
    receipt = checked_real_path(receipt) if receipt is not None else None
    from .prepared_model import catalog as load_catalog
    catalog = catalog or load_catalog()
    root = io_path(plan['root'])
    removed, skipped, released = [], [], 0
    if plan.get('blockers'):
        raise ValueError('Retired model cleanup is blocked: ' + ', '.join(plan['blockers']))
    with runtime_lock(root / 'setup.lock', inherit=False), \
            runtime_lock(root / 'engine.lock', inherit=False), \
            runtime_lock(root / 'launcher' / 'host-setup.lock', inherit=False):
        active, _ = active_variant(root, catalog)
        if (active is None or active != plan['active'] or not complete(root, catalog, active)
                or (plan.get('expect_active') is not None and active != plan['expect_active'])):
            raise ValueError('The model in use changed; check storage again.')
        prepared = root / 'prepared'
        for retired in plan['retired']:
            name = retired['variant']
            if name == active or name not in catalog['variants']:
                raise ValueError('Retired model plan names the model in use; nothing was removed.')
            folder, rows = allowed(Path(plain(root)), catalog, name)
            folder = io_path(folder)
            if plain(folder) != plain(retired['folder']) or folder == variant_folder(root, catalog, active):
                raise ValueError('Retired model folder changed; check storage again.')
            directories = set()
            held = set()
            for entry in retired['files']:
                path = Path(entry['path'])
                reason = None
                if entry['kind'] == 'online-lora' and online_lora_folders.home(path) in held and path == online_lora_folders.home(path) / online_lora_folders.MARKER:
                    reason = 'folder still holds files'
                elif not permitted(path, entry['kind'], Path(plain(folder)), rows, catalog['variants'][name]['cache_prefix']):
                    reason = 'not a planned name'
                elif entry['kind'] == 'online-lora' and not inside(prepared, path):
                    reason = 'link, junction, shared or missing'
                elif entry['kind'] == 'online-lora' and not _regular(path):
                    reason = 'link, junction, shared or missing'
                elif entry['kind'] != 'online-lora' and (not inside(prepared, path) or unshared_file(path) is None):
                    reason = 'link, junction, shared or missing'
                else:
                    try:
                        if not _same(entry, fingerprint(path)):
                            reason = 'changed since the scan'
                    except OSError:
                        reason = 'missing'
                if reason:
                    skipped.append(dict(path=plain(path), reason=reason))
                    if entry['kind'] == 'online-lora':
                        held.add(online_lora_folders.home(path))
                    continue
                try:
                    io_path(path).unlink()
                except OSError as error:
                    skipped.append(dict(path=plain(path), reason=type(error).__name__))
                    if entry['kind'] == 'online-lora':
                        held.add(online_lora_folders.home(path))
                    continue
                removed.append(dict(path=plain(path), bytes=entry['bytes'], kind=entry['kind']))
                released += entry['bytes']
                current = io_path(path).parent
                while current != folder and folder in current.parents:
                    directories.add(current)
                    current = current.parent
            # Staging folders hold the SDK's own empty work directories; remove
            # them only when empty, deepest first, each named under its stage.
            for value in sorted(retired.get('stage_directories', []), key=lambda p: len(Path(p).parts), reverse=True):
                directory = Path(value)
                if not stage_directory_permitted(directory, Path(plain(folder)), rows):
                    continue
                try:
                    directory = io_path(directory)
                    if inside(prepared, directory) and directory.is_dir() and not any(directory.iterdir()):
                        directory.rmdir()
                except OSError:
                    pass
            # Only directories that held removed files, deepest first, and only when empty.
            for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True) + [folder / STAGES, folder]:
                try:
                    directory = io_path(directory)
                    if inside(prepared, directory) and directory.is_dir() and not any(directory.iterdir()):
                        directory.rmdir()
                except OSError:
                    pass
    result = dict(released_bytes=released, removed_files=len(removed), skipped=skipped,
                  kept_bytes=plan.get('kept_bytes', 0), active=plan['active'])
    if receipt is not None:
        receipt = io_path(receipt)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(dict(result, removed=removed, finished=time.time()), indent=1), encoding='utf-8')
    return result
