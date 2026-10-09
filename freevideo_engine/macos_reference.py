"""Deliver supplemental Mac modulation tables without importing a GPU runtime.

The caller supplies a bundled catalog pinned to a published model revision.
Weights and exact timestep rows select the asset; a backend or task name alone
never does. Existing computed tables retain their original bytes and receipts.
"""
import json
import os
from pathlib import Path
import re
import shutil

from . import adaln_assets as assets
from . import network
from .monitoring import save
from . import disk_space


CATALOG_FILE = Path(__file__).with_name('macos_reference_catalog.json')


def prepare_missing(cache, identity, count, *, networking=None, env=None):
    """Deliver a missing schedule only when the app includes its pinned index.

    A build without that optional index keeps the original projection path.
    An invalid index or failed supported transfer is an error, so a failed
    small download cannot silently trigger a much larger projection download.
    """
    try:
        catalog = _read(CATALOG_FILE)
    except FileNotFoundError:
        return False
    return ensure(cache, identity, count, catalog=catalog, networking=networking, env=env)


def select(catalog, identity, count):
    """Validate the complete pinned index before selecting an exact schedule."""
    if (not isinstance(catalog, dict) or catalog.get('schema_version') != 1
            or not isinstance(catalog.get('repo'), str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', catalog['repo'])
            or not isinstance(catalog.get('revision'), str)
            or not re.fullmatch('[0-9a-f]{40}', catalog['revision'])
            or type(count) is not int or not 1 <= count <= 100
            or not isinstance(catalog.get('tables'), list)
            or not isinstance(catalog.get('prefix', 'reference-tables-v1'), str)
            or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,63}', catalog.get('prefix', 'reference-tables-v1'))):
        raise ValueError('Invalid pinned Mac reference catalog')
    # The folder that holds the tables at the pinned revision; catalogs before
    # 0.3.5 name none and use the original reference-tables-v1 folder.
    prefix = catalog.get('prefix', 'reference-tables-v1')
    selected, seen = None, set()
    for table in catalog['tables']:
        try:
            value = table['identity']
            expected = assets.identity(value['weights'], value['timesteps'], value['channels'], value['dtype'])
            directory = assets.directory(expected)
            if (value != expected or not isinstance(value['weights'], str)
                    or not re.fullmatch('[0-9a-f]{64}', value['weights'])
                    or table['directory'] != directory or directory in seen
                    or not isinstance(table['files'], list) or len(table['files']) != count):
                raise ValueError('Invalid Mac reference identity or layer count')
            indices = set()
            for row in table['files']:
                index = row['index']
                if (type(index) is not int or not 0 <= index < count or index in indices
                        or row['file'] != f'{prefix}/{directory}/{index:02d}.safetensors'
                        or type(row['bytes']) is not int or row['bytes'] <= 0
                        or not isinstance(row['sha256'], str)
                        or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
                    raise ValueError('Invalid Mac reference file record')
                indices.add(index)
            seen.add(directory)
        except (KeyError, TypeError, AttributeError, OverflowError) as error:
            raise ValueError('Invalid Mac reference catalog structure') from error
        if value == identity:
            selected = table
    return selected


def _read(path):
    with Path(path).open(encoding='utf-8') as stream:
        return json.load(stream)


def _receipt(row):
    return {key: row[key] for key in ('bytes', 'sha256')}


def _network_plan(env):
    from .paths import data_root
    value = network.installed_plan(env)
    value.setdefault('download_settings_path', str(data_root() / 'download-settings.json'))
    # An installation made without a saved probe still has the two supported
    # HF sources. ModelScope requires a separately verified revision mapping.
    value.setdefault('sources', {}).setdefault('models', [dict(id=name)
        for name in ('official', 'hf-mirror')])
    return value


def ensure(cache, identity, count, *, catalog, networking=None, env=None):
    """Reuse or download one schedule; unsupported identities return False.

    A supported transfer failure propagates. The caller must not turn it into
    a request for the much larger original projection weights. Publication of
    the catalog's immutable revision is a prerequisite, never inferred here.
    """
    table = select(catalog, identity, count)
    if table is None:
        return False
    root = assets.asset_path(cache, table['directory'])
    root.mkdir(parents=True, exist_ok=True)
    marker = assets.asset_path(root, 'identity.json')
    if marker.exists():
        if _read(marker) != identity:
            raise ValueError('Mac reference cache identity changed; files retained')
    else:
        save(marker, identity)
    missing, recovered, reused = [], [], 0
    for row in sorted(table['files'], key=lambda row: row['index']):
        path = assets.asset_path(root, f"{row['index']:02d}.safetensors")
        sidecar = assets.asset_path(root, f"{row['index']:02d}.json")
        partial = assets.asset_path(root, path.name + '.partial')
        assets.asset_path(root, partial.name + '.source.json')
        if sidecar.exists():
            # Local per-step GEMMs may have a different producer. Validate by
            # their original receipt instead of replacing valid cached output.
            if path.exists():
                assets.check_table(path, _read(sidecar), identity)
                reused += 1
                continue
            if _receipt(_read(sidecar)) != _receipt(row):
                raise ValueError('Missing locally computed reference table; receipt retained')
        if path.exists():
            # Recover an interrupted publication only after checking against
            # the bundled checksum, not a new self-generated checksum.
            assets.check_table(path, row, identity)
            recovered.append((sidecar, row))
        else:
            missing.append((path, sidecar, row))
    # Rejected partial bytes are retained by the shared downloader. Reserve a
    # full replacement even when an existing prefix looks complete.
    needed = sum(row['bytes'] for _, _, row in missing)
    if missing and disk_space.free_bytes(root) < needed + 256 * 2**20:
        raise ValueError('Insufficient disk space for Mac reference constants; existing files retained')
    for sidecar, row in recovered:
        if not sidecar.exists():
            save(sidecar, _receipt(row))
    if not missing:
        _retire_superseded(cache, catalog)
        return True
    env = os.environ if env is None else env
    networking = _network_plan(env) if networking is None else networking
    from .prepared_model import token
    secret = token(env)
    headers = ['Authorization: Bearer ' + secret] if secret else []
    network.event(networking, category='models', action='reference-tables-required',
                  files=len(missing), bytes=needed, reused_files=reused,
                  recovered_files=len(recovered), revision=catalog['revision'])
    for path, sidecar, row in missing:
        remote = dict(row, repo=catalog['repo'], revision=catalog['revision'])
        def progress(done, total, speed):
            network.event(networking, category='models', action='reference-table-progress',
                          file=path.name, done=done, total=total, bytes_per_second=speed)
        network.download(network.model_urls(networking, remote, env), path, row['sha256'],
                         size=row['bytes'], network=networking, env=env,
                         progress=progress,
                         headers_for=lambda source: headers if source == 'official' else [],
                         category=network.model_family(remote), stall_seconds=30,
                         slow_seconds=15, low_speed_limit=64 * 1024)
        assets.check_table(path, row, identity)
        if not sidecar.exists():
            save(sidecar, _receipt(row))
    _retire_superseded(cache, catalog)
    return True


def _retire_superseded(cache, catalog):
    """Remove the reference tables 0.3.5 replaced once their replacements are here; never fail."""
    from .superseded_tables import retire
    try:
        return retire([Path(cache)], catalog['tables'])
    except Exception:  # Best effort; the request continues with the new tables.
        return None
