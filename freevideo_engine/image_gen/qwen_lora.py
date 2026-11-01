"""Community LoRAs for the Qwen-Image 2.1 DiT, applied as unmerged low-rank branches.

The DiT's Linears are int8 ConvRot matrices, which cannot absorb a weight delta,
so every LoRA stays a pair of small matrices and adds ``scale * B(A x)`` to the
projection's output (the same shape as the video model's online LoRA). Nothing
is written into the model's weights: attaching adds a forward wrapper per
target, detaching restores the original forward, and a request without LoRAs
runs the untouched model.

Accepted files are the layouts the community trains for this model: kohya
(``lora_down``/``lora_up`` and ``alpha``) and PEFT / ai-toolkit (``lora_A`` /
``lora_B``), under a ``diffusion_model.``, ``transformer.`` or
``base_model.model.`` prefix, or kohya's flat ``lora_unet_...`` names. The fused
``img_mlp.gate_up`` target is split into the model's ``gate_layer`` (first half
of the rows) and ``proj`` (second half), the order the checkpoint loader uses.
"""
import types
from functools import lru_cache
from pathlib import Path

import torch
import torch.nn.functional as F

UNUSABLE = 'This LoRA was not trained for the Qwen image model and cannot be used.'

_PREFIXES = ('model.diffusion_model.', 'diffusion_model.', 'base_model.model.', 'transformer.')
_DOWN = ('.lora_down.weight', '.lora_A.weight', '.lora_A.default.weight', '.lora_down.default.weight')
_UP = ('.lora_up.weight', '.lora_B.weight', '.lora_B.default.weight', '.lora_up.default.weight')
_ALPHA = ('.alpha', '.lora_alpha')
_CHUNK = 512


@lru_cache(maxsize=1)
def layout():
    """name -> (in_features, out_features) of every LoRA target; ``gate_up`` is the fused pair, added beside its two halves."""
    from .qwen_dit import QwenImageDiT
    from .qwen_weights import _config
    with torch.device('meta'):
        model = QwenImageDiT(**_config({}, QwenImageDiT, None))
    shapes = {}
    for name, module in model.named_modules():
        if isinstance(module, torch.nn.Linear):
            shapes[name] = (module.in_features, module.out_features)
    for name in [n for n in shapes if n.endswith('.img_mlp.gate_layer')]:
        prefix = name[:-len('gate_layer')]
        inputs, rows = shapes[name]
        shapes[prefix + 'gate_up'] = (inputs, rows * 2)
    return shapes


@lru_cache(maxsize=1)
def _flat_names():
    return {'lora_unet_' + name.replace('.', '_'): name for name in layout()}


def _split(key):
    """-> (target name, 'down' | 'up' | 'alpha') or None for a key that is not a LoRA tensor."""
    for kind, suffixes in (('down', _DOWN), ('up', _UP), ('alpha', _ALPHA)):
        for suffix in suffixes:
            if key.endswith(suffix):
                base = key[:-len(suffix)]
                for prefix in _PREFIXES:
                    if base.startswith(prefix):
                        base = base[len(prefix):]
                        break
                return _flat_names().get(base, base), kind
    return None


def _entries(keys):
    """Group tensor keys by target: name -> {'down': key, 'up': key, 'alpha': key}."""
    groups = {}
    for key in keys:
        parsed = _split(key)
        if parsed is not None:
            groups.setdefault(parsed[0], {})[parsed[1]] = key
    return groups


def inspect_file(path):
    """Header-only check: ``dict(ok, reason)``; reason is 'other-model', 'shape', 'empty' or 'unreadable'."""
    from safetensors import safe_open
    try:
        with safe_open(str(path), framework='pt', device='cpu') as handle:
            keys = list(handle.keys())
            groups = _entries(keys)
            if not keys:
                return dict(ok=False, reason='empty')
            shapes = layout()
            mapped = {name: group for name, group in groups.items() if name in shapes}
            if not mapped or not all('down' in g and 'up' in g for g in mapped.values()):
                return dict(ok=False, reason='other-model')
            for name, group in mapped.items():
                inputs, outputs = shapes[name]
                if 'alpha' in group:
                    alpha = handle.get_tensor(group['alpha']).float()
                    if alpha.numel() != 1 or not torch.isfinite(alpha).all():
                        return dict(ok=False, reason='shape')
                down = tuple(handle.get_slice(group['down']).get_shape())
                up = tuple(handle.get_slice(group['up']).get_shape())
                if len(down) != 2 or len(up) != 2 or down[1] != inputs or up[0] != outputs or down[0] != up[1] or down[0] < 1:
                    return dict(ok=False, reason='shape')
            return dict(ok=True, reason=None)
    except Exception:
        return dict(ok=False, reason='unreadable')


def read(path, strength=1.0):
    """-> list of (target, a [r, in], b [out, r], scale) in bf16 on the CPU. ValueError when unusable."""
    from safetensors import safe_open
    verdict = inspect_file(path)
    if not verdict['ok']:
        raise ValueError(UNUSABLE)
    shapes = layout()
    result = []
    with safe_open(str(path), framework='pt', device='cpu') as handle:
        for name, group in sorted(_entries(handle.keys()).items()):
            if name not in shapes:
                continue   # norm deltas and the like: not part of the supported targets
            a = handle.get_tensor(group['down']).to(torch.bfloat16).contiguous()
            b = handle.get_tensor(group['up']).to(torch.bfloat16).contiguous()
            rank = a.shape[0]
            alpha = float(handle.get_tensor(group['alpha']).float()) if 'alpha' in group else float(rank)
            result.append((name, a, b, float(strength) * alpha / rank))
    return result


def file_bytes(loras):
    """Bytes the branches of these ``{'path', 'strength'}`` entries occupy once read (bf16)."""
    total = 0
    for item in loras:
        total += Path(item['path']).stat().st_size
    return total


class Branch:
    """One LoRA's factors for one target; ``a``/``b`` live on ``home`` and move to the activation on use."""

    __slots__ = ('a', 'b', 'scale')

    def __init__(self, a, b, scale):
        self.a, self.b, self.scale = a, b, scale


def _apply(module, raw, output):
    rows = raw.reshape(-1, raw.shape[-1])
    target = output.reshape(-1, output.shape[-1])
    if not target.is_contiguous():
        raise ValueError('LoRA projection output must be contiguous')
    for branch in module._qwen_lora:
        a, b = branch.a, branch.b
        if a.device != rows.device:
            a, b = a.to(rows.device, non_blocking=True), b.to(rows.device, non_blocking=True)
        for start in range(0, len(rows), _CHUNK):
            z = F.linear(rows[start:start + _CHUNK].to(a.dtype), a)
            target[start:start + _CHUNK].add_(F.linear(z, b).to(target.dtype), alpha=branch.scale)
    return output


class LoraSet:
    """The branches of one request's LoRAs, attached to a DiT for the length of a with-block."""

    def __init__(self, loras, *, device='cpu', pin=True):
        self.entries = {}     # module name -> [Branch]
        self.bytes = 0
        self._wrapped = []
        for item in loras:
            for name, a, b, scale in read(item['path'], item['strength']):
                if device != 'cpu':
                    a, b = a.to(device), b.to(device)
                elif pin and torch.cuda.is_available():
                    try:
                        a, b = a.pin_memory(), b.pin_memory()
                    except RuntimeError:
                        pass
                self.bytes += a.numel() * a.element_size() + b.numel() * b.element_size()
                if name.endswith('.img_mlp.gate_up'):
                    prefix = name[:-len('gate_up')]
                    half = b.shape[0] // 2
                    self.entries.setdefault(prefix + 'gate_layer', []).append(Branch(a, b[:half], scale))
                    self.entries.setdefault(prefix + 'proj', []).append(Branch(a, b[half:], scale))
                else:
                    self.entries.setdefault(name, []).append(Branch(a, b, scale))

    def attach(self, model):
        for name, branches in self.entries.items():
            module = model.get_submodule(name)
            module._qwen_lora = branches
            original = module.forward

            def forward(self, value, old=original):
                return _apply(self, value, old(value))
            module.forward = types.MethodType(forward, module)
            self._wrapped.append(module)
        return self

    def detach(self):
        for module in self._wrapped:
            module.__dict__.pop('forward', None)
            module.__dict__.pop('_qwen_lora', None)
        self._wrapped = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.detach()
        return False
