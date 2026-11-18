"""Helpers for running a model in two stages (encoder, then denoiser) with weights read from disk.

``open_index`` reads a model folder's safetensors index; ``read_tensors`` loads the tensors outside the streamed
layers (embeddings, norms, input and output projections) straight to a device; ``save_encoding`` and
``load_encoding`` hand the encoder's result to the next stage through a file, so that the encoder's host memory
goes back to the system with its process.
"""
import json
from pathlib import Path

import torch
from safetensors import safe_open

from freevideo_engine.image_gen.layer_stream import SafetensorsIndex

INDEX_NAME = 'model.safetensors.index.json'


def open_index(folder, index_name=INDEX_NAME):
    """Index of the safetensors files in ``folder``; a folder with one file and no index file is accepted."""
    folder = Path(folder)
    index_file = folder / index_name
    if index_file.exists():
        weight_map = json.loads(index_file.read_text())['weight_map']
    else:
        files = sorted(folder.glob('*.safetensors'))
        if len(files) != 1:
            raise FileNotFoundError('no %s and %d safetensors files in %s' % (index_name, len(files), folder))
        with safe_open(str(files[0]), 'pt', device='cpu') as handle:
            weight_map = {key: files[0].name for key in handle.keys()}
    return SafetensorsIndex(folder, weight_map)


def read_tensors(index, skip_prefix, device):
    """Every tensor whose key does not start with ``skip_prefix``, on ``device``, one file open at a time."""
    by_file = {}
    for key, name in index.weight_map.items():
        if not key.startswith(skip_prefix):
            by_file.setdefault(name, []).append(key)
    tensors = {}
    for name, keys in by_file.items():
        with safe_open(str(index.folder / name), 'pt', device='cpu') as handle:
            for key in keys:
                tensors[key] = handle.get_tensor(key).to(device)
    return tensors


def layer_key(prefix):
    """``key(i, name)`` for ``DiskLayerStream`` when layer ``i`` is stored as ``<prefix>layers.<i>.<name>``."""
    return lambda i, name: '%slayers.%d.%s' % (prefix, i, name)


def save_encoding(path, encodings):
    """Write ``{name: (embeds, mask)}`` to ``path``; tensors only, so the file can be read back without code."""
    flat = {}
    for name, (embeds, mask) in encodings.items():
        flat['%s/embeds' % name] = embeds.detach().cpu().contiguous()
        flat['%s/mask' % name] = mask.detach().cpu().contiguous()
    from safetensors.torch import save_file
    save_file(flat, str(path))


def load_encoding(path, device='cpu'):
    """Read what ``save_encoding`` wrote; returns ``{name: (embeds, mask)}``."""
    out = {}
    with safe_open(str(path), 'pt', device='cpu') as handle:
        for key in handle.keys():
            name, kind = key.rsplit('/', 1)
            out.setdefault(name, {})[kind] = handle.get_tensor(key).to(device)
    return {name: (pair['embeds'], pair['mask']) for name, pair in out.items()}
