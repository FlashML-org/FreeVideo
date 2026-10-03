"""Independent low-rank branches whose buffers follow the layer offloader."""
import types

import torch
import torch.nn.functional as F


def attach_block(block, index, cache, manifest, load, device='cpu'):
    spec = manifest.get('online_lora', {}).get('blocks', {}).get(str(index))
    if not spec:
        return
    values = load(cache / spec['file'])
    prefix = f'transformer_blocks.{index}.' if index != 'root' else ''
    for name, patches in spec['modules'].items():
        module = block.get_submodule(name.removeprefix(prefix))
        branches = torch.nn.ModuleList()
        for number, patch in enumerate(patches):
            factor = torch.nn.Module()
            key = name + '._freevideo_lora.' + str(number)
            factor.register_buffer('a', values.pop(key + '.a').to(device=device))
            factor.register_buffer('b', values.pop(key + '.b').to(device=device))
            factor.scale = patch['scale']
            branches.append(factor)
        module.add_module('_freevideo_lora', branches)
        original = module.forward
        def forward(self, value, old=original):
            return apply(self, value, old(value))
        module.forward = types.MethodType(forward, module)
    if values:
        raise ValueError('Unbound LoRA tensors')


def apply(module, raw, output, channels=None):
    branches = getattr(module, '_freevideo_lora', None)
    if branches is None:
        return output
    rows = raw.reshape(-1, raw.shape[-1])
    target = output.reshape(-1, output.shape[-1])
    if not target.is_contiguous():
        raise ValueError('LoRA projection output must be contiguous')
    for branch in branches:
        b = branch.b if channels is None else branch.b[channels].contiguous()
        # Bound workspace independently of the video/reference token count.
        for start in range(0, len(rows), 512):
            z = F.linear(rows[start:start + 512], branch.a)
            result = target[start:start + 512]
            if z.is_cuda and z.dtype in (torch.bfloat16, torch.float16) and z.shape[1] <= 256:
                from .lora_kernels import up_add
                up_add(z, b, result, branch.scale)
            else:
                result.add_(F.linear(z, b), alpha=branch.scale)
    return output


def quantized(module, raw, xq, xs):
    return apply(module, raw, module.forward_quantized(xq, xs, out_dtype=raw.dtype))
