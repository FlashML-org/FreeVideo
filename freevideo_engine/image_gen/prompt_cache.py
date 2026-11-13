"""The same prompt is not encoded twice: image conditioning goes through the input cache the video path uses.

The key is the prompt plus the pixels of every reference, under the identity of the encoder (file content, its code
and libraries, GPU architecture). A hit hands back exactly the tensors the encoder saved, so a cached image equals an
uncached one. Anything that cannot be verified (no installation record, other explicit paths, an unreadable or
damaged entry) encodes as before."""
import hashlib
import json
from pathlib import Path
import tempfile

KIND = 'h3-image-conditioning'


def reference_key(prompt, images, encoder):
    """Prompt, encoder name and the pixels of each reference, in order."""
    rows = []
    for image in images:
        picture = image.convert('RGB')
        rows.append({'size': list(picture.size), 'sha256': hashlib.sha256(picture.tobytes()).hexdigest()})
    return json.dumps({'version': 1, 'prompt': prompt, 'encoder': encoder, 'references': rows},
                      sort_keys=True, ensure_ascii=False)


_OPENED = {}


def open_cache(*, encoder, comfy_root, comfy_python, model_paths):
    """The input cache for this installation, or None when its identity cannot be established.

    Worked out once per process and set of paths: reading the GPU and hashing the encoder's code costs seconds."""
    key = (encoder, str(comfy_root), str(comfy_python), str(model_paths))
    if key not in _OPENED:
        _OPENED[key] = _open_cache(encoder=encoder, comfy_root=comfy_root, comfy_python=comfy_python,
                                   model_paths=model_paths)
    return _OPENED[key]


def _open_cache(*, encoder, comfy_root, comfy_python, model_paths):
    try:
        from .. import tuning
        from ..hardware import detect
        from ..input_cache import InputCache
        from ..paths import comfy_root as default_root
        machine_path = tuning.state_path().parents[1] / 'machine.json'
        if not machine_path.is_file():
            return None
        machine = json.loads(machine_path.read_text(encoding='utf-8'))
        same = (encoder == machine['encoder']
                and Path(comfy_python).absolute() == Path(machine['comfy_python']).absolute()
                and Path(comfy_root or default_root()).resolve() == Path(machine['comfy_root']).resolve()
                and model_paths and Path(model_paths).resolve() == Path(machine['model_paths']).resolve())
        if not same:
            return None
        return InputCache(tuning.text_identity(machine, detect()), kind=KIND)
    except Exception:
        return None


def encode(prompt, images, compute, *, cache, encoder, stats=None):
    """``compute()`` returns the conditioning dict; it runs only when the cache has no entry for this input."""
    import time
    import torch
    started = time.perf_counter()
    stats = stats if stats is not None else {}
    stats['encode_cache'] = 'off' if cache is None else 'miss'
    if cache is None:
        return compute()
    key = reference_key(prompt, images, encoder)
    with tempfile.TemporaryDirectory(prefix='freevideo-h3cond-') as directory:
        target = Path(directory) / 'cond.pt'
        try:
            found = cache.get(key, target)
            if found is not None:
                value = torch.load(target, map_location='cpu', weights_only=False)
                if isinstance(value, dict) and 'prompt_embeds' in value:
                    stats['encode_cache'] = 'hit'
                    stats['encode_cache_s'] = round(time.perf_counter() - started, 2)
                    return value
        except Exception:
            pass
        value = compute()
        try:
            source = Path(directory) / 'save.pt'
            torch.save(value, source)
            cache.put(key, source, {'success': True, 'kind': KIND})
        except Exception:
            pass
        return value
