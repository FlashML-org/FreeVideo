"""Isolated, hash-checked backport for FA4 b26's SM120 invalid-tile read."""
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from .paths import data_root

ORIGINAL = 'e9d890e10611ce48dc57c40570a7b65d1ab802c508f785e5536fb10044db65f3'
PATCHED = '0b6c45fbacf31dfbc6bca600e4efa21892f95a4ad3e21a163c04a74ffa076a12'


def location():
    return data_root() / 'vendor' / 'fa4-b26-valid-tile'


def prepare():
    distribution = importlib.metadata.distribution('flash-attn-4')
    source = Path(distribution.locate_file('flash_attn/cute/flash_fwd.py'))
    content = source.read_bytes()
    if distribution.version != '4.0.0b26' or hashlib.sha256(content).hexdigest() != ORIGINAL:
        raise RuntimeError('This guard requires the verified unmodified FA4 4.0.0b26; no package was changed.')
    target = location()
    if target.exists():
        validate(target)
        return target
    text = content.decode()
    marker = '        m_block, num_head, batch_size, _ = work_tile.tile_idx\n'
    if text.count(marker) != 1:
        raise RuntimeError('Unexpected FA4 source layout')
    start = text.index(marker)
    end = text.index('    @cute.jit\n    def compute_one_n_block', start)
    text = text[:start] + '        if work_tile.is_valid_tile:\n' + ''.join(
        '    ' + line if line.strip() else line for line in text[start:end].splitlines(keepends=True)) + text[end:]
    if hashlib.sha256(text.encode()).hexdigest() != PATCHED:
        raise RuntimeError('FA4 guard output differs from the validated patch')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='fa4-guard-') as temporary:
        temp = Path(temporary) / 'overlay'
        shutil.copytree(source.parent, temp / 'flash_attn/cute', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (temp / 'flash_attn/cute/flash_fwd.py').write_text(text, encoding='utf-8')
        (temp / 'manifest.json').write_text(json.dumps({'distribution': distribution.version,
            'source_sha256': ORIGINAL, 'patched_sha256': PATCHED, 'installed_package_modified': False,
            'upstream_reference': 'https://github.com/Dao-AILab/flash-attention/blob/main/flash_attn/cute/flash_fwd.py'}, indent=2) + '\n', encoding='utf-8')
        try:
            os.rename(temp, target)
        except OSError:
            if not target.exists():
                raise
            validate(target)  # Another setup process may have completed first.
    return target


def validate(root):
    target = Path(root) / 'flash_attn/cute/flash_fwd.py'
    if hashlib.sha256(target.read_bytes()).hexdigest() != PATCHED:
        raise RuntimeError('FA4 overlay failed its source check')


def activate():
    root = location()
    if not root.exists():
        return None
    validate(root)
    if 'flash_attn.cute' in sys.modules:
        current = Path(sys.modules['flash_attn.cute'].__file__).resolve()
        if root.resolve() not in current.parents:
            raise RuntimeError('FA4 was imported before the guard; start a fresh Engine process')
    # Extend the package path instead of replacing FA2's __init__.py. This works
    # with both the FA4 namespace package and a separately installed FA2 package.
    package = importlib.import_module('flash_attn')
    paths = [str(root / 'flash_attn'), *list(package.__path__)]
    package.__path__ = list(dict.fromkeys(paths))
    return root


def check_selected():
    """Refuse the known faulty SM120 kernel before any attention execution."""
    import torch
    if torch.cuda.get_device_capability()[0] != 12:
        return
    module = importlib.import_module('flash_attn.cute.flash_fwd')
    digest = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    if digest == ORIGINAL:
        raise RuntimeError('The attention speed-up component on this computer needs an update. Run freevideo setup --fa4-guard in a terminal, then restart FreeVideo.')
