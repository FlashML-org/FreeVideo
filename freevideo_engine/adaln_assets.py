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
# The int8 export keeps the slim layout and its AdaLN tables; only matrices differ.
SLIM_FORMATS = (SLIM_FORMAT, 'freevideo-int8-slim-v1')


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
    slim = manifest.get('format') in SLIM_FORMATS
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
    from huggingface_hub import get_token
    token = get_token()
    headers = ['Authorization: Bearer ' + token] if token else []
    prefix = remote.get('prefix', 'cache')
    asset_path(cache, prefix)  # Validate before constructing a remote URL.
    for row in missing:
        url = 'https://huggingface.co/%s/resolve/%s/%s/%s' % (
            remote['repo'], remote['revision'], prefix, row['file'])
        network.download([('official', url)], asset_path(cache, row['file']), row['sha256'],
                         size=row['bytes'], headers_for=lambda source: headers if source == 'official' else [],
                         category='models')
    return rows
