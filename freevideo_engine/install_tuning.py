"""Bound installation work by live CPU/RAM and reuse immutable artifacts."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .storage import conversion_source, fingerprint

GiB = 1 << 30


def build_parallelism(ram_budget_bytes, cpu_threads):
    # Measured serial Sage2 build: 3.70 GiB peak PSS. Allow 4 GiB per
    # compiler, 2 GiB coordination space and another 20% budget margin.
    jobs = max(1, min(8, max(1, cpu_threads // 2), int((ram_budget_bytes * .8 - 2 * GiB) // (4 * GiB))))
    return {'jobs': jobs, 'nvcc_threads': 2, 'parallel_extensions': 1,
            'estimated_peak_bytes': (jobs * 4 + 2) * GiB,
            'ram_budget_bytes': ram_budget_bytes, 'cpu_threads': cpu_threads}


def required_models(rows, reuse_cache):
    if not reuse_cache:
        return rows
    # Runtime consumes the prepared transformer, its configs, and original
    # decoders. Original BF16/LoRA tensors are only needed for conversion.
    return [r for r in rows if not conversion_source(r)]


def cache_compatible(path, capability=None, *, scale_granularity=None):
    try:
        value = json.loads((Path(path) / 'manifest.json').read_text(encoding='utf-8'))
        if value.get('format') in ('freevideo-fp8-slim-v1', 'freevideo-int8-slim-v1'):
            from .adaln_assets import validate_catalog
            validate_catalog(value, sum(r['group'].startswith('blocks/') for r in value['groups']))
        if scale_granularity == 'int8_convrot':
            rotation = value.get('rotation') or {}
            return (value.get('precision') == 'int8' and value.get('scale_granularity') == 'rowwise'
                    and rotation.get('kind') == 'convrot' and rotation.get('group') == 256 and bool(value.get('groups')))
        if scale_granularity is not None:
            return (scale_granularity in ('rowwise', 'per_tensor') and value.get('precision') == 'fp8'
                    and bool(value.get('groups')) and value.get('scale_granularity') == scale_granularity)
        native_format = 'per_tensor' if capability[0] >= 10 else 'rowwise'
        return (value.get('precision') == 'fp8' and bool(value.get('groups')) and
                (tuple(capability) < (8, 9) or value.get('scale_granularity') == native_format))
    except (OSError, ValueError, TypeError):
        return False


def discover_prepared(folder, capability=None, *, scale_granularity=None):
    """Recognize a downloaded engine bundle in the user's selected model folder."""
    if not folder:
        return None
    root = Path(folder).expanduser().resolve()
    candidates = [root, root / 'cache', root / 'vdn-h3-edge/cache', root / 'vdn-minimax-h3-edge/cache']
    for candidate in candidates:
        if cache_compatible(candidate, capability, scale_granularity=scale_granularity):
            return candidate
    return None


def unchanged_before(path, receipt):
    """Receipts only apply to files unchanged since that receipt was written."""
    identity = fingerprint(path)
    changed = identity.get('change_time_ns', identity['ctime_ns'])
    # Equal timestamps cannot establish ordering (notably in fast Windows
    # writes). Unknown ChangeTime must not fall back to creation time.
    return changed is not None and max(identity['mtime_ns'], changed) < receipt.stat().st_mtime_ns


def hf_receipt(path, row, directory):
    receipt = directory / '.cache/huggingface/download' / (row['file'] + '.metadata')
    try:
        revision, etag, completed = receipt.read_text(encoding='utf-8').splitlines()[:3]
        return (revision == row['revision'] and etag == (row.get('sha256') or row.get('git_blob')) and
                float(completed) > 0 and unchanged_before(path, receipt) and
                max(path.stat().st_mtime, path.stat().st_ctime) <= float(completed))
    except (OSError, ValueError):
        return False


def wheel_key(identity):
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
