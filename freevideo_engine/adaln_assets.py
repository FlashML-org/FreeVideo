"""Portable AdaLN model assets. Validation and installation stay Torch-free.

Producer hardware/software describe where constants were evaluated; they are
not dependencies of a stored tensor. The contract, weights, exact timestep rows
and tensor layout are dependencies. Never substitute a different schedule.
"""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import struct

CONTRACT = 'minimax-h3-adaln-silu-linear-3x6-v1'
FORMAT = 'freevideo-adaln-v2'
SLIM_FORMAT = 'freevideo-fp8-slim-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def asset_path(root, name):
    if not isinstance(name, str):
        raise ValueError('Invalid AdaLN asset path')
    parts = PurePosixPath(name)
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or parts.is_absolute() or any(p in ('..', '.') for p in name.split('/'))):
        raise ValueError('Invalid AdaLN asset path')
    root = Path(root).resolve()
    path = root.joinpath(*parts.parts)
    try:
        path.resolve().relative_to(root)
    except ValueError as error:
        raise ValueError('AdaLN asset escapes the cache') from error
    return path


def tensor_header(path):
    """Bounded structural check, including payload length, before any mapping."""
    with Path(path).open('rb') as stream:
        prefix = stream.read(8)
        if len(prefix) != 8:
            raise ValueError('Truncated AdaLN tensor header')
        size = struct.unpack('<Q', prefix)[0]
        if not 2 <= size <= 16 * 1024 * 1024:
            raise ValueError('Invalid AdaLN tensor header size')
        header = json.loads(stream.read(size))
        payload = Path(path).stat().st_size - 8 - size
    end = 0
    entries = {k: v for k, v in header.items() if k != '__metadata__'}
    for row in sorted(entries.values(), key=lambda v: v['data_offsets'][0]):
        begin, stop = row['data_offsets']
        shape = row['shape']
        width = {'BF16': 2, 'F32': 4, 'F16': 2}.get(row['dtype'])
        if (width is None or any(type(v) is not int or v < 1 for v in shape)
                or begin != end or stop - begin != math.prod(shape) * width):
            raise ValueError('Invalid AdaLN tensor layout')
        end = stop
    if not entries or end != payload:
        raise ValueError('Truncated or oversized AdaLN tensor payload')
    return entries


def projection_groups(manifest):
    sources = manifest.get('adaln_sources', {})
    rows = sources.get('groups', [r for r in manifest['groups'] if r['group'].startswith('adaln/')])
    return sorted(rows, key=lambda r: r['group'])


def weight_identity(manifest):
    roots = [r for r in manifest['groups'] if r['group'] == 'root']
    if len(roots) != 1:
        raise ValueError('AdaLN needs one root weight group')
    # Export records the hash of just the time embedding tensors. Legacy caches
    # conservatively bind their whole root group until an export supplies it.
    embedding = manifest.get('adaln_sources', {}).get('embedding_sha256', roots[0]['sha256'])
    return digest({'embedding': embedding, 'projections':
                   [(r['group'], r['sha256']) for r in projection_groups(manifest)]})


def identity(weights, timesteps, channels, dtype='BF16'):
    if (not timesteps or type(channels) is not int or channels <= 0 or dtype not in ('BF16', 'F32')
            or any(not row or row != sorted(set(row)) or any(not math.isfinite(v) for v in row)
                   for row in timesteps)):
        raise ValueError('Invalid AdaLN schedule or layout')
    return dict(format=FORMAT, contract=CONTRACT, weights=weights,
                timesteps=timesteps, channels=channels, dtype=dtype,
                modality_rows=3, components=6)


def directory(value):
    return 'adaln-tables-v2-' + digest(value)[:24]


def check_table(path, row, expected, *, verify_hash=True):
    if not Path(path).is_file() or Path(path).stat().st_size != row['bytes']:
        raise ValueError('AdaLN table integrity failure (missing or wrong size): ' + str(path))
    if verify_hash and file_hash(path) != row['sha256']:
        raise ValueError('AdaLN table integrity failure: ' + str(path))
    header = tensor_header(path)
    steps = expected['timesteps']
    if set(header) != {'step_%d' % i for i in range(len(steps))}:
        raise ValueError('AdaLN table schedule length mismatch')
    for i, times in enumerate(steps):
        item = header['step_%d' % i]
        if (item['dtype'] != expected['dtype'] or
                item['shape'] != [len(times) * expected['modality_rows'], expected['channels'] * 6]):
            raise ValueError('AdaLN table shape or dtype mismatch')


def validate_catalog(manifest, count):
    """Return every required asset row; no file I/O, Torch or device probing."""
    tables = manifest.get('adaln_tables', [])
    slim = manifest.get('format') == SLIM_FORMAT
    sources = projection_groups(manifest)
    for row in sources:
        if (row['file'] != row['group'] + '.safetensors' or
                not re.fullmatch(r'adaln/[0-9]{2}', row['group']) or
                not isinstance(row.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', row['sha256']) or
                type(row.get('bytes')) is not int or row['bytes'] <= 0):
            raise ValueError('Invalid original AdaLN source identity')
    if slim and ({r['group'] for r in sources} != {'adaln/%02d' % i for i in range(count)}
                 or len(sources) != count or not tables):
        raise ValueError('Slim model is missing AdaLN source identities or complete tables')
    result, seen = [], set()
    for table in tables:
        value = table['identity']
        expected = identity(weight_identity(manifest), value['timesteps'], value['channels'], value['dtype'])
        if value != expected or table['directory'] != directory(value) or table['directory'] in seen:
            raise ValueError('AdaLN model asset identity mismatch')
        seen.add(table['directory'])
        files = table['files']
        if len(files) != count or {r['index'] for r in files} != set(range(count)):
            raise ValueError('Incomplete AdaLN model asset set')
        for row in files:
            if (row['file'] != table['directory'] + '/%02d.safetensors' % row['index']
                    or not re.fullmatch('[0-9a-f]{64}', row['sha256']) or row['bytes'] <= 0):
                raise ValueError('Invalid AdaLN model asset receipt')
            result.append((row, value))
    return result


def optional_table(expected, manifest):
    """Select only exact, published constants; LoRA/clock changes cannot match."""
    catalog = json.loads(Path(__file__).with_name('prepared_models.json').read_text(encoding='utf-8'))
    optional = catalog.get('optional_adaln', {})
    table = next((t for t in optional.get('tables', []) if t['identity'] == expected), None)
    if table is None:
        return None
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', optional.get('repo', ''))
            or not re.fullmatch('[0-9a-f]{40}', optional.get('revision', ''))):
        raise ValueError('Optional sampling preset has no pinned source')
    validate_catalog(dict(manifest, adaln_tables=[table]), len(projection_groups(manifest)))
    prefix = optional['prefix']
    asset_path(Path('.'), prefix)
    return dict(table, download=dict(repo=optional['repo'], revision=optional['revision'], prefix=prefix))


def _download_plan():
    from . import network
    from .paths import data_root
    plan = network.installed_plan()
    machine = data_root() / 'machine.json'
    if not plan and machine.is_file():
        installed = json.loads(machine.read_text(encoding='utf-8'))
        plan = installed.get('network', {}) or {}
        if not plan and installed.get('setup_run'):
            setup = Path(installed['setup_run']) / 'plan.json'
            if setup.is_file():
                plan = json.loads(setup.read_text(encoding='utf-8')).get('network', {}) or {}
    plan.setdefault('download_settings_path', str(data_root() / 'download-settings.json'))
    plan.setdefault('sources', {}).setdefault('models', [{'id': 'official'}, {'id': 'hf-mirror'}])
    return plan


def download_table(root, table, index):
    """Fetch a missing small table, preserving partials and user network choices.

    A network failure must propagate, not fall back to downloading 26 GB of
    projection weights. Valid locally computed tables are handled by TableCache
    before reaching this function.
    """
    from . import network
    from .monitoring import save
    from .provision import model_headers
    row = next(r for r in table['files'] if r['index'] == index)
    path = asset_path(root, '%02d.safetensors' % index)
    marker = path.with_suffix('.json')
    if marker.is_file():
        previous = json.loads(marker.read_text(encoding='utf-8'))
        if any(previous.get(key) != row[key] for key in ('bytes', 'sha256')):
            raise ValueError('Missing locally computed AdaLN table; original receipt retained: ' + str(path))
    remaining = sum(r['bytes'] for r in table['files'] if
                    not asset_path(root, '%02d.safetensors' % r['index']).is_file())
    if shutil.disk_usage(root).free < remaining + 64 * 1024**2:
        raise ValueError('Not enough disk space to prepare this sampling preset. Existing files retained.')
    plan = _download_plan()
    remote = table['download']
    source = dict(repo=remote['repo'], revision=remote['revision'], file=remote['prefix']+'/'+row['file'])
    total = sum(r['bytes'] for r in table['files'])
    before = sum(r['bytes'] for r in table['files'] if r['index'] < index)
    event = ('reference_assets_download' if table.get('task') in ('ref2va_audio', 'ref2va_av')
             else 'sampling_preset_download')
    def progress(done, size, speed, **kwargs):
        print(json.dumps(dict(event=event, steps=len(table['identity']['timesteps']),
            done_bytes=before+done, total_bytes=total, bytes_per_second=speed)), flush=True)
    progress(0, row['bytes'], 0.)
    network.download(network.model_urls(plan, source), path, row['sha256'], progress,
        network=plan, size=row['bytes'], category='models', headers_for=model_headers, keep_partial=True,
        stall_seconds=30, slow_seconds=15, low_speed_limit=64 * 1024)
    check_table(path, row, table['identity'])
    save(marker, dict(bytes=row['bytes'], sha256=row['sha256']))
    progress(row['bytes'], row['bytes'], 0.)
    return row


def restore_projections(cache, manifest):
    """Opt-in by requesting an unsupported schedule or an AdaLN-changing LoRA.

    The normal eight-step model never calls this. Restore only the optional
    projection files from an immutable published revision, with bounded writes.
    Existing bytes and original package metadata are never overwritten.
    """
    from . import network
    rows = projection_groups(manifest)
    if not rows:
        raise ValueError('This model has no original AdaLN projection source')
    missing = []
    for row in rows:
        path = asset_path(cache, row['file'])
        if path.exists():
            if path.stat().st_size != row['bytes'] or file_hash(path) != row['sha256']:
                raise ValueError('Optional AdaLN source changed; retained: ' + str(path))
        else:
            missing.append(row)
    if not missing:
        return rows
    remote = manifest.get('adaln_sources', {}).get('download', {})
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', remote.get('repo', ''))
            or not re.fullmatch('[0-9a-f]{40}', remote.get('revision', ''))):
        raise ValueError('This schedule/LoRA needs original AdaLN weights. Reuse a full prepared cache; '
                         'the slim package has no pinned recovery source.')
    needed = sum(r['bytes'] for r in missing)
    if shutil.disk_usage(cache).free < needed + 1024**3:
        raise ValueError('This schedule/LoRA needs %.2f GiB of optional AdaLN weights; '
                         'insufficient disk space. Existing model and request retained.' % (needed / 1024**3))
    print(json.dumps({'event': 'adaln_sources_required', 'bytes': needed,
                      'reason': 'Requested schedule or LoRA changes precomputed modulation',
                      'files': len(missing), 'revision': remote['revision']}), flush=True)
    from .provision import model_headers
    plan = _download_plan()
    prefix = remote.get('prefix', 'cache')
    asset_path(cache, prefix)  # Validate before constructing a remote URL.
    for row in missing:
        source = dict(repo=remote['repo'], revision=remote['revision'], file=prefix+'/'+row['file'])
        network.download(network.model_urls(plan, source), asset_path(cache, row['file']), row['sha256'],
                         size=row['bytes'], headers_for=model_headers, network=plan, category='models',
                         stall_seconds=30, slow_seconds=15, low_speed_limit=64 * 1024)
    return rows
