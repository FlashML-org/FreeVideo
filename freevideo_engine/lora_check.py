"""Tell from a LoRA file's header alone whether it can be used with the FreeVideo model.

Only the key names and tensor shapes are read; no weights are loaded. The fixed
layout supports precision-independent checks without a prepared model. The UI
also uses the active int8 manifest's linear index to count skipped patches.
The engine still makes the exact check when it prepares the adapter.
"""
import json
import os
import re
import struct
import threading

from .lora_names import INT8_UNMERGEABLE, MIXED_RANK_ALPHA, _targets, adapter_keys, int8_skip_reason, resolve_target, supported

MAX_HEADER_BYTES = 64 * 1024 * 1024
BLOCKS, REFINER_BLOCKS = 50, 2
UNCHECKED = None  # known module, shape not asserted here


def _layout():
    shapes = {}
    for scope, count in (('transformer_blocks', BLOCKS), ('token_refiner.refiner_blocks', REFINER_BLOCKS)):
        for i in range(count):
            p = f'{scope}.{i}.'
            attn = p + ('attn.orig.' if scope == 'transformer_blocks' else 'attn.')
            for name in ('to_q', 'to_k', 'to_v'):
                shapes[attn + name] = (7168, 5376)
            shapes[attn + 'to_out.0'] = (5376, 7168)
            shapes[p + 'ff.net.0.proj'] = (28672, 5376)
            shapes[p + 'ff.net.2'] = (5376, 14336)
    for i in range(BLOCKS):
        p = f'transformer_blocks.{i}.'
        shapes[p + 'attn.to_out_linear'] = (5376, 7168)
        shapes[p + 'adaln_proj.linear'] = UNCHECKED
        shapes[p + 'attn.linear_attention.alpha.down'] = (128, 5376)
        shapes[p + 'attn.linear_attention.alpha.up'] = (7168, 128)
        shapes[p + 'attn.linear_attention.beta_proj'] = (56, 5376)
        shapes[p + 'attn.linear_attention.output_gate.down'] = (128, 5376)
        shapes[p + 'attn.linear_attention.output_gate.up'] = (7168, 128)
        shapes[p + 'attn.softmax_gate.up'] = (56, 5376)
        shapes[p + 'attn.linear_attention.norm'] = (128,)
        for name, shape in (('k_sp', (7168, 1, 5, 5)), ('v_sp', (7168, 1, 5, 5)), ('k_tm', (7168, 1, 5)), ('v_tm', (7168, 1, 5))):
            shapes[p + 'attn.linear_attention.short_conv.' + name] = shape
        shapes[p + 'attn.orig.norm_k'] = shapes[p + 'attn.orig.norm_q'] = (128,)
        shapes[p + 'norm1'] = shapes[p + 'norm2'] = (5376,)
    for i in range(REFINER_BLOCKS):
        p = f'token_refiner.refiner_blocks.{i}.'
        shapes[p + 'attn.norm_k'] = shapes[p + 'attn.norm_q'] = (128,)
        shapes[p + 'norm1'] = shapes[p + 'norm2'] = (5376,)
    shapes.update({
        'context_embedder': (5376, 5120), 'audio_proj_in': (5376, 32), 'audio_proj_out': (32, 5376),
        'proj_in': (5376, 96), 'proj_out': (96, 5376), 'time_embedder.linear_1': (5376, 256),
        'time_embedder.linear_2': (2688, 5376), 'norm_out.linear': (10752, 2688),
        'norm_out.norm': (5376,), 'token_refiner.final_norm': (5376,)})
    return shapes


LAYOUT = _layout()
UNUSABLE = 'This LoRA was not trained for the FreeVideo model and cannot be used.'
ACCELERATION = ('This acceleration LoRA was made for the original model and cannot be used. '
                'The FreeVideo model is already accelerated and does not need one.')
# Step-distillation adapters (for example parallel-decoding ones) ship a bank of
# whole replacement output heads, one per stretch of the sampling schedule.
HEADS = ('proj_out', 'audio_proj_out')
_CACHE, _LOCK = {}, threading.Lock()


def read_header(path):
    """The safetensors header as {name: shape} plus its metadata; ValueError when it is not one."""
    with open(path, 'rb') as stream:
        raw = stream.read(8)
        if len(raw) != 8:
            raise ValueError('Not a safetensors file')
        size = struct.unpack('<Q', raw)[0]
        if not 2 <= size <= MAX_HEADER_BYTES:
            raise ValueError('Not a safetensors file')
        text = stream.read(size)
    if len(text) != size:
        raise ValueError('Truncated safetensors header')
    try:
        header = json.loads(text.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError('Not a safetensors file') from error
    if not isinstance(header, dict):
        raise ValueError('Not a safetensors file')
    metadata = header.pop('__metadata__', None)
    shapes = {}
    for key, value in header.items():
        shape = value.get('shape') if isinstance(value, dict) else None
        if not isinstance(shape, list) or not all(type(n) is int and n >= 0 for n in shape):
            raise ValueError('Not a safetensors file')
        shapes[key] = tuple(shape)
    return shapes, metadata if isinstance(metadata, dict) else {}


def _find(target):
    """The model module a target name refers to (without any #bias mark), or None."""
    target = target.removesuffix('#bias')
    if target not in LAYOUT and '.attn.' in target:
        target = target.replace('.attn.', '.attn.orig.', 1)
    return target if target in LAYOUT else None


FAMILIES = (('wan', re.compile(r'\.(?:self_attn|cross_attn)\.|\.ffn\.\d|^head\.head')),
            ('hunyuan', re.compile(r'double_blocks|single_blocks')),
            ('ltx', re.compile(r'\.attn[12]\.')),
            ('sd', re.compile(r'down_blocks|up_blocks|input_blocks|lora_te|text_model')))


def family(keys):
    """Which other model a file's layer names belong to, or None when they match none we know."""
    for name, pattern in FAMILIES:
        if any(pattern.search(key) for key in keys):
            return name
    return None


def _bad(reason, keys, **detail):
    return dict(reason=reason, family=family(keys) if reason == 'other-model' else None, **detail)


def _head_bank(shapes):
    """The first tensor that is a stack of whole output-head weights, or None."""
    for key, shape in shapes.items():
        if not key.endswith('.weight') or len(shape) != 3:
            continue
        name = _targets(key[:-7])[0][0]
        if name in HEADS and list(shape[1:]) == list(LAYOUT[name]):
            return key
    return None


def _block(target):
    found = re.search(r'blocks\.(\d+)\.', target)
    return int(found[1]) + 1 if found else None


def assess(shapes, *, int8_targets=None, skipped=None, metadata=None):
    """None when the adapter fits, else why not: reason 'other-model' (names of another model), 'shape' (our names,
    a different size), 'acceleration' (a step-distillation adapter that replaces the output heads and needs its own
    sampler) or 'format' (not a LoRA we can read; 'layer' names the first tensor that is not a LoRA factor).
    'layer' and the sizes say where. A layer name the table does not list, in a file that matches no other known
    model, is let through.
    With int8_targets, use the installed linear shapes and collect the runtime's
    skipped patch categories; 'int8-unmergeable' means every patch was skipped.
    With the file's metadata, 'alpha' means one alpha value is stored for factors of different ranks, which the
    engine refuses because the strength cannot be determined; 'ranks' lists them."""
    keys = list(shapes)
    heads = _head_bank(shapes)
    if heads:
        return _bad('acceleration', keys, layer=heads)
    try:
        pairs, full = adapter_keys(keys)
    except ValueError:
        if family(keys):
            return _bad('other-model', keys, layer=None)
        return _bad('format', keys, layer=next((key for key in keys if not supported(key)), None))
    if not any('A' in pair for pair in pairs.values()) and not full:
        return _bad('format', keys, layer=None)
    wrong, applied = [], set()
    skipped = [] if skipped is None else skipped
    for name, pair in pairs.items():
        if not {'A', 'B'} <= pair.keys():
            return _bad('format', keys, layer=name)
        a, b = shapes[pair['A']], shapes[pair['B']]
        if len(a) != 2 or len(b) != 2 or a[0] != b[1] or a[0] <= 0:
            return _bad('format', keys, layer=name)
        for target, split, swap in _targets(name):
            if int8_targets is not None:
                target = resolve_target(target, int8_targets)
                reason = int8_skip_reason(target, int8_targets)
                if reason == 'other_layer' and family(keys):
                    return _bad('other-model', keys, layer=name)
                if reason:
                    skipped.append((reason, target))
                    continue
                if target in applied:
                    return _bad('format', keys, layer=target)
                applied.add(target)
            found = target if int8_targets is not None else _find(target)
            if found is None:
                if family(keys):
                    return _bad('other-model', keys, layer=name)
                continue  # a name this table does not list: the engine makes the exact check
            expected = int8_targets[found] if int8_targets is not None else LAYOUT[found]
            rows = b[0] if split is None else b[0] // 3
            if expected is UNCHECKED:
                continue
            if (split is not None and b[0] % 3) or (swap and rows % 2) or list(expected) != [rows, a[1]]:
                wrong.append((found, list(expected), [rows, a[1]]))
    for name, key, bias in full:
        shape = shapes[key]
        for target, split, swap in _targets(name):
            if int8_targets is not None:
                target = resolve_target(target + ('#bias' if bias else ''), int8_targets)
                if int8_skip_reason(target, int8_targets, full=True) == 'other_layer' and family(keys):
                    return _bad('other-model', keys, layer=name)
                skipped.append((int8_skip_reason(target, int8_targets, full=True), target))
                continue
            found = _find(target)
            if found is None:
                if family(keys):
                    return _bad('other-model', keys, layer=name)
                continue
            expected = LAYOUT[found]
            if bias or expected is UNCHECKED:
                continue
            actual = list(shape)
            if split is not None:
                if not actual or actual[0] % 3:
                    wrong.append((found, list(expected), actual))
                    continue
                actual[0] //= 3
            if actual != list(expected) or (swap and (not actual or actual[0] % 2)):
                wrong.append((found, list(expected), actual))
    if wrong:
        layer, expected, actual = wrong[0]
        return dict(reason='shape', family=None, layer=layer, block=_block(layer), expected=expected, actual=actual, count=len(wrong))
    if metadata and 'alpha' in metadata:
        ranks = sorted({shapes[pair['A']][0] for pair in pairs.values() if 'A' in pair and 'alpha' not in pair})
        if len(ranks) > 1:
            return _bad('alpha', keys, ranks=ranks)
    if int8_targets is not None and not applied:
        return _bad('int8-unmergeable', keys)
    return None


BASE_FIELDS = ('ss_base_model_version', 'modelspec.architecture', 'base_model', 'modelspec.implementation', 'ss_network_module')


def summarize(shapes, metadata):
    """Counts and a few names from the header; no path, no weights."""
    first = [[key, list(shapes[key])] for key in list(shapes)[:5]]
    base = {name: str(metadata[name])[:80] for name in BASE_FIELDS if name in metadata}
    return dict(keys=len(shapes), first=first, base=base)


def check_file(path, *, int8_targets=None):
    """{'usable', 'reason', ...} for a LoRA file, remembered until the file changes."""
    try:
        stat = os.stat(path)
        # The same file can be checked against a different installed model while
        # the page stays open. Never reuse its int8 verdict for another index.
        target_stamp = None if int8_targets is None else tuple(sorted((name, tuple(shape)) for name, shape in int8_targets.items()))
        stamp = (str(path), stat.st_size, stat.st_mtime_ns, target_stamp)
    except OSError:
        return {'usable': False, 'reason': 'unreadable'}
    with _LOCK:
        if stamp in _CACHE:
            return dict(_CACHE[stamp])
    try:
        shapes, metadata = read_header(path)
        skipped = []
        problem = assess(shapes, int8_targets=int8_targets, skipped=skipped, metadata=metadata)
        result = {'usable': problem is None, 'reason': None, **(problem or {}), 'summary': summarize(shapes, metadata)}
        if int8_targets is not None:
            result['partial'] = len(skipped)
    except (OSError, ValueError):
        result = {'usable': False, 'reason': 'unreadable'}
    with _LOCK:
        if len(_CACHE) > 256:
            _CACHE.clear()
        _CACHE[stamp] = dict(result)
    return dict(result)


def check_installed_file(path):
    """Header check for the same installed cache generation uses; no Torch import.

    Unknown installation/manifest means the precision-independent check, never an
    assumed int8 warning. Only the precision leaves the server, not model paths.
    """
    from .comfy_bridge import installation
    from .long_paths import io_path
    precision, targets = None, None
    try:
        _, machine = installation()
        manifest = json.loads((io_path(machine['cache']) / 'manifest.json').read_text(encoding='utf-8'))
        precision = manifest.get('precision')
        if precision == 'int8':
            targets = {name: spec['weight_shape'] for name, spec in manifest['linears'].items()}
            if any(not isinstance(shape, list) or len(shape) != 2 or
                   any(type(n) is not int or n <= 0 for n in shape) for shape in targets.values()):
                raise ValueError('Invalid linear index')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        precision, targets = None, None
    return dict(check_file(path, int8_targets=targets), precision=precision)


def require_usable(path):
    """Stop a request before generation starts when a LoRA cannot work with the model."""
    result = check_installed_file(path)
    if not result['usable']:
        raise ValueError({'int8-unmergeable': INT8_UNMERGEABLE, 'acceleration': ACCELERATION, 'alpha': MIXED_RANK_ALPHA}.get(result['reason'], UNUSABLE))
