"""Persistent ComfyUI resource preferences, read once for each new request."""
import json
import math
from pathlib import Path

from .monitoring import save


def setting(backend):
    if backend == 'mps':
        return 'ram_reserve_gib', 1., 'Reserved unified RAM'
    if backend != 'cuda':
        raise ValueError('Unknown resource backend')
    return 'gpu_reserve_gib', .2, 'Reserved VRAM'


def validate(value, *, backend='cuda'):
    _, minimum, label = setting(backend)
    if value is None:
        return None  # Leave the automatic policy in charge, including tight fits.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(label + ' must be automatic or a finite number of at least %g GiB' % minimum)
    return float(value)


def read(root, *, backend='cuda'):
    field, _, _ = setting(backend)
    path = Path(root) / 'resource-settings.json'
    if not path.exists():
        return {field: None}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Invalid resource settings')
    return {field: validate(value.get(field), backend=backend)}


def write(root, value, *, backend='cuda'):
    field, _, _ = setting(backend)
    result = {field: validate(value, backend=backend)}
    save(Path(root) / 'resource-settings.json', result)
    return result
