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

from .locking import runtime_lock
from .storage import fingerprint

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


def inside(base, path):
    """True when path is base or below it and nothing between them is a link or junction."""
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


def unshared_file(path):
    try:
        info = path.lstat()
    except OSError:
        return None
    if redirected(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return None
    return info


def variant_folder(root, catalog, name):
    variant = catalog['variants'][name]
    return root / 'prepared' / ('edge-' + variant.get('revision', catalog['revision'])[:16])


def active_variant(root, catalog):
    """The catalog variant whose cache machine.json names, or None when that is unknown."""
    try:
        record = json.loads((root / 'machine.json').read_text(encoding='utf-8'))
        cache = Path(record['cache'])
    except (OSError, ValueError, KeyError, TypeError):
        return None, None
    for name, variant in catalog['variants'].items():
        expected = variant_folder(root, catalog, name) / variant['cache_prefix']
        try:
            if cache.absolute() == expected or cache.resolve() == expected.resolve():
                return name, expected
        except OSError:
            continue
    return None, cache


def complete(root, catalog, name):
    """Every catalog file of the variant is present, regular, local and of its catalogued size."""
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


def _sidecar_matches(path, row):
    info = unshared_file(path)
    if info is None or info.st_size > SIDECAR_LIMIT:
        return False
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(value, dict) and value.get('expected') == row['sha256']


def _stage(folder, prepared, stage):
    """Files and directories of one native staging folder, or None when anything is redirected or shared."""
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
    return files, directories


def _generated_tables(prepared, table):
    """Files of one engine-generated AdaLN table folder, or None unless it is bound to its identity."""
    from .adaln_assets import FORMAT, directory
    if not ADALN_DIRECTORY.fullmatch(table.name) or not inside(prepared, table) or not table.is_dir():
        return None
    entries = list(table.iterdir())
    if any(not ADALN_FILE.fullmatch(entry.name) or unshared_file(entry) is None for entry in entries):
        return None
    marker = table / 'identity.json'
    info = unshared_file(marker)
    if info is None or info.st_size > ADALN_IDENTITY_LIMIT:
        return None
    try:
        identity = json.loads(marker.read_text(encoding='utf-8'))
        if not isinstance(identity, dict) or identity.get('format') != FORMAT or directory(identity) != table.name:
            return None
    except (OSError, ValueError, TypeError):
        return None
    return entries


def _plan_file(path, kind, entries):
    entries.append(dict(path=str(path), kind=kind, bytes=path.lstat().st_size, stamp=fingerprint(path)))


def retire(root, catalog, name):
    """Owned files of one retired variant, and what stays in its folder."""
    folder = variant_folder(root, catalog, name)
    prepared = root / 'prepared'
    entries, kept, planned, stage_directories = [], [], set(), []
    if not folder.is_dir() or not inside(prepared, folder):
        return dict(variant=name, folder=str(folder), files=entries, kept=kept, bytes=0, kept_bytes=0,
                    stage_directories=stage_directories, present=folder.exists() or folder.is_symlink())
    stages = folder / STAGES
    for row in catalog['variants'][name]['files']:
        path = folder / row['file']
        if path.exists() or path.is_symlink():
            info = unshared_file(path)
            if info is not None and info.st_size == row['bytes'] and inside(prepared, path):
                _plan_file(path, 'catalog', entries)
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
                    if path_in_stage not in planned:
                        _plan_file(path_in_stage, 'stage', entries)
                        planned.add(path_in_stage)
                for directory in found[1]:
                    if str(directory) not in stage_directories:
                        stage_directories.append(str(directory))
    cache = folder / catalog['variants'][name]['cache_prefix']
    if cache.is_dir() and inside(prepared, cache):
        for table in sorted(cache.iterdir()):
            found = _generated_tables(prepared, table)
            for path in found or ():
                if path not in planned:
                    _plan_file(path, 'adaln', entries)
                    planned.add(path)
    kept_bytes = 0
    for current, names, files in os.walk(folder, followlinks=False):
        for name_ in list(names):
            path = Path(current) / name_
            try:
                if redirected(path.lstat()):
                    names.remove(name_)
                    kept.append(dict(path=str(path), bytes=0, reason='link or junction'))
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
            kept.append(dict(path=str(path), bytes=size, reason='not placed by this installer for this variant'))
    return dict(variant=name, folder=str(folder), files=entries, kept=kept, present=True,
                stage_directories=stage_directories,
                bytes=sum(row['bytes'] for row in entries), kept_bytes=kept_bytes)


def fused_lora_variants(root, catalog, name):
    """Fused LoRA variants built from this variant (they live beside its cache)."""
    beside = (variant_folder(root, catalog, name) / catalog['variants'][name]['cache_prefix']).parent
    try:
        return sorted(str(path) for path in beside.iterdir() if path.name.startswith(FUSED_LORA))
    except OSError:
        return []


def scan(root, catalog=None, expect_active=None):
    """What removing the variants that are not in use would release; nothing is changed.

    expect_active names the variant the caller has just switched to; a scan
    that finds any other variant in use plans nothing.
    """
    from .prepared_model import catalog as load_catalog
    root = Path(root).absolute()
    catalog = catalog or load_catalog()
    active, cache = active_variant(root, catalog)
    plan = dict(root=str(root), active=active, cache=str(cache) if cache else None, expect_active=expect_active,
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
            plan['kept_variants'].append(dict(variant=name, folder=str(folder), reason='fused-lora-variants',
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


def clean(plan, catalog=None, receipt=None):
    """Remove exactly the planned files that are still unchanged; report everything else."""
    from .prepared_model import catalog as load_catalog
    catalog = catalog or load_catalog()
    root = Path(plan['root'])
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
            folder, rows = allowed(root, catalog, name)
            if str(folder) != retired['folder'] or folder == variant_folder(root, catalog, active):
                raise ValueError('Retired model folder changed; check storage again.')
            directories = set()
            for entry in retired['files']:
                path = Path(entry['path'])
                reason = None
                if not permitted(path, entry['kind'], folder, rows, catalog['variants'][name]['cache_prefix']):
                    reason = 'not a planned name'
                elif not inside(prepared, path) or unshared_file(path) is None:
                    reason = 'link, junction, shared or missing'
                else:
                    try:
                        if fingerprint(path) != entry['stamp']:
                            reason = 'changed since the scan'
                    except OSError:
                        reason = 'missing'
                if reason:
                    skipped.append(dict(path=str(path), reason=reason))
                    continue
                try:
                    path.unlink()
                except OSError as error:
                    skipped.append(dict(path=str(path), reason=type(error).__name__))
                    continue
                removed.append(dict(path=str(path), bytes=entry['bytes'], kind=entry['kind']))
                released += entry['bytes']
                current = path.parent
                while current != folder and folder in current.parents:
                    directories.add(current)
                    current = current.parent
            # Staging folders hold the SDK's own empty work directories; remove
            # them only when empty, deepest first, each named under its stage.
            for value in sorted(retired.get('stage_directories', []), key=lambda p: len(Path(p).parts), reverse=True):
                directory = Path(value)
                if not stage_directory_permitted(directory, folder, rows):
                    continue
                try:
                    if inside(prepared, directory) and directory.is_dir() and not any(directory.iterdir()):
                        directory.rmdir()
                except OSError:
                    pass
            # Only directories that held removed files, deepest first, and only when empty.
            for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True) + [folder / STAGES, folder]:
                try:
                    if inside(prepared, directory) and directory.is_dir() and not any(directory.iterdir()):
                        directory.rmdir()
                except OSError:
                    pass
    result = dict(released_bytes=released, removed_files=len(removed), skipped=skipped,
                  kept_bytes=plan.get('kept_bytes', 0), active=plan['active'])
    if receipt is not None:
        receipt = Path(receipt)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(dict(result, removed=removed, finished=time.time()), indent=1), encoding='utf-8')
    return result
