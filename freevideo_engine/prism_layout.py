"""Prepared-bundle layout helpers that need no tensor runtime (the request
planner imports them; prism_runtime re-exports them)."""
import json
import os
from pathlib import Path
import struct


SHARED_FOLDERS = ('text_encoder', 'tokenizer', 'vae', 'audio_vae')
VARIANT_NAMES = ('int8', 'fp8', 'bf16')


def sibling(root, folder):
    """The variant folder that holds ``folder``: ``root`` itself, else a sibling
    variant. An add-on variant (bf16, for the original level) ships only its own
    weights and reads the text encoder, tokenizer and VAEs of the installed one."""
    root = Path(root)
    if (root / folder).is_dir():
        return root
    for name in VARIANT_NAMES:
        other = root.parent / name
        if other != root and (other / folder).is_dir() and (other / 'manifest.json').is_file():
            return other
    return root


def shared(root, folder):
    return sibling(root, folder) / folder


def with_shared(root, manifest):
    """``manifest`` plus the file rows of shared folders it borrows from a sibling."""
    files = dict(manifest.get('files') or {})
    for folder in SHARED_FOLDERS:
        if any(name.startswith(folder + '/') for name in files):
            continue
        source = sibling(root, folder)
        if source == Path(root):
            continue
        other = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
        files.update((name, row) for name, row in other.get('files', {}).items() if name.startswith(folder + '/'))
    return dict(manifest, files=files)


# Light's v2a K/V calibration maps (prism_model.kv_calib), found by their format.
KV_MAPS_FORMAT = 'prism-kv-calib/1'


def safetensors_header(path):
    """The safetensors header of ``path`` ({tensor: row, '__metadata__': {...}}), or None."""
    try:
        with open(path, 'rb') as stream:
            size = struct.unpack('<Q', stream.read(8))[0]
            if not 0 < size < 64 * 2**20:
                return None
            value = json.loads(stream.read(size))
            return value if isinstance(value, dict) else None
    except (OSError, ValueError, struct.error):
        return None


def find_kv_maps(root):
    """The newest prism-kv-calib/1 file for the prepared variant ``root``:
    FREEVIDEO_PRISM_KV_MAPS, else its audio_calibration folder or the variant folder
    (a sibling variant's when this one has none), else None."""
    explicit = os.environ.get('FREEVIDEO_PRISM_KV_MAPS')
    if explicit:
        return Path(explicit) if Path(explicit).is_file() else None
    roots = [Path(root)] + [Path(root).parent / name for name in VARIANT_NAMES if Path(root).parent / name != Path(root)]
    for variant in roots:
        found = []
        for folder in (variant / 'audio_calibration', variant):
            if folder.is_dir():
                for path in folder.glob('*.safetensors'):
                    if ((safetensors_header(path) or {}).get('__metadata__') or {}).get('format') == KV_MAPS_FORMAT:
                        found.append(path)
        if found:
            return max(found, key=lambda p: p.stat().st_mtime)
    return None


def kv_maps_bytes(path):
    """(all buckets, the largest bucket) of a maps file in bytes, from its header."""
    rows = dict(safetensors_header(path) or {})
    rows.pop('__metadata__', None)
    buckets = {}
    for name, row in rows.items():
        start, end = row['data_offsets']
        bucket = name.split('.', 1)[0]
        buckets[bucket] = buckets.get(bucket, 0) + (end - start)
    return sum(buckets.values()), max(buckets.values(), default=0)
