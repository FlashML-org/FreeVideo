"""Persistent ComfyUI resource preferences, read once for each new request."""
import json
import math
import os
import time
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
    path = Path(root) / NAME
    if not path.exists():
        return {field: None}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Invalid resource settings')
        return {field: validate(value.get(field), backend=backend)}
    except FileNotFoundError:
        return {field: None}
    except ValueError:
        # A damaged file must not stop the settings panel or every generation:
        # it is renamed and kept, and the automatic policy takes over.
        keep(path)
        return {field: None}


NAME = 'resource-settings.json'
DAMAGED = 'resource-settings.damaged-'


def keep(path):
    stamp, count = DAMAGED + time.strftime('%Y%m%d-%H%M%S'), 1
    target = path.with_name(stamp + '.json')
    while target.exists():  # A second damaged file within the same second keeps the first copy.
        count += 1
        target = path.with_name('%s-%d.json' % (stamp, count))
    try:
        os.replace(path, target)
    except FileNotFoundError:
        pass  # Another reader already set it aside.
    except OSError:
        pass  # Left in place; reading still falls back to automatic.


def damaged(root):
    """The newest damaged copy's name while no settings were saved after it."""
    root = Path(root)
    if (root / NAME).exists():
        return None
    try:
        names = sorted(item.name for item in root.glob(DAMAGED + '*.json'))
    except OSError:
        return None
    return names[-1] if names else None


def write(root, value, *, backend='cuda'):
    field, _, _ = setting(backend)
    result = {field: validate(value, backend=backend)}
    try:
        save(Path(root) / NAME, result)
    except OSError as error:
        raise ValueError('Could not save resource settings: %s' % error) from error
    return result
