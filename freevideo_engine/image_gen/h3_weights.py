"""Loader for the Comfy-format H3 Image DiT files (int8 ConvRot or plain BF16 rows)."""
import json

import torch

from ..streamed_weights import _open_safetensors
from .h3_dit import H3Dit
from .int8_linear import ops
from .qwen_weights import CONVROT_METADATA, COMFY_ATTENTION_HINT, _assign


def _marker(checkpoint, key):
    return json.loads(bytes(checkpoint.get_tensor(key).tolist()).decode('utf-8'))


def load_h3_dit(path, device='cpu', *, mapped=False):
    """Build the DiT with the file's tensors on ``device``; int8 projections stay int8 with FP32 row scales."""
    with _open_safetensors(path, framework='pt', device='cpu') as checkpoint:
        if mapped:
            from .mapped import MappedCheckpoint
            checkpoint = MappedCheckpoint(checkpoint, path)
        with torch.device('meta'):
            model = H3Dit()
        keys = {key: key for key in checkpoint.keys()}
        expected = model.state_dict()
        quantized = sorted(key[:-len('.comfy_quant')] for key in keys if key.endswith('.comfy_quant'))
        specs, consumed = {}, set()
        for name in quantized:
            if _marker(checkpoint, name + '.comfy_quant') != CONVROT_METADATA:
                raise ValueError('Unsupported quantization marker for ' + name)
            shape = list(expected[name + '.weight'].shape)
            specs[name] = dict(storage='int8', convrot_group=256, weight_shape=shape,
                               scale_shape=[shape[0], 1], input_dtype='bfloat16')
            consumed.update({name + '.comfy_quant', name + '.weight_scale'})
        for key in keys:
            if key.endswith('.attn.comfy_attention.config'):
                if _marker(checkpoint, key) not in (COMFY_ATTENTION_HINT, [COMFY_ATTENTION_HINT]):
                    raise ValueError('Unsupported attention hint: ' + key)
                consumed.add(key)
        ordinary = set(expected) - {name + '.weight' for name in specs}
        missing, extra = ordinary - set(keys), set(keys) - consumed - ordinary - {n + '.weight' for n in specs}
        if missing or extra:
            raise ValueError(f'H3 DiT keys differ: missing={sorted(missing)[:5]}, unexpected={sorted(extra)[:5]}')
        if specs:
            ops().install_cached_linears(model, specs, dict(kind='convrot', group=256))
        for name in specs:
            scales = checkpoint.get_tensor(name + '.weight_scale')
            if not torch.isfinite(scales).all() or not (scales > 0).all():
                raise ValueError('Weight scales must be finite and positive: ' + name)
            _assign(model, name + '.weight_int8', checkpoint.get_tensor(name + '.weight'), device, torch.int8)
            _assign(model, name + '.weight_scale', scales, device, torch.float32)
        for key in sorted(ordinary):
            parent, _, leaf = key.rpartition('.')
            target = parent + '.original.bias' if parent in specs and leaf == 'bias' else key
            tensor = checkpoint.get_tensor(key)
            keep = tensor.dtype if tensor.dtype in (torch.float32, torch.float16) else torch.bfloat16
            _assign(model, target, tensor, device, keep)
        model.to(device=device)
        return model.eval().requires_grad_(False)
