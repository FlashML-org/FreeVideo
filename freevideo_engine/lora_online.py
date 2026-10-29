"""Independent low-rank branches whose buffers follow the layer offloader."""
import contextlib
import types

import torch
import torch.nn.functional as F

# Row tile of the down projection and the cap on the projections one layer call
# keeps (a rank-r projection of n rows is n * r values per branch).
TILE_ROWS = 512
SHARED_LIMIT_BYTES = 256 * 2**20
_shared = None


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


@contextlib.contextmanager
def shared_down():
    """Compute each branch's down projection of one input once for a layer call.

    The head groups of an attention block all project the same input rows, and
    each applied the branches to its own channel slice, repeating the
    rows-by-A product once per group. Inside this context the product is
    computed on its first use, in the same row tiles as before, and the other
    groups read it. Only for groups that run in order on one stream.
    """
    global _shared
    previous, _shared = _shared, {'bytes': 0}
    try:
        yield
    finally:
        _shared = previous


def _down(branch, rows):
    """rows @ A in bounded row tiles, kept for the other head groups when it fits."""
    cache = _shared
    key = id(branch)
    if cache is not None:
        entry = cache.get(key)
        if entry is not None and entry[0] == rows.data_ptr() and entry[1] == rows.shape:
            return entry[2]
    z = None
    if cache is not None and len(rows) and rows.is_cuda and rows.dtype in (torch.bfloat16, torch.float16):
        size = len(rows) * branch.a.shape[0] * rows.element_size()
        if cache['bytes'] + size <= SHARED_LIMIT_BYTES:
            first = F.linear(rows[:TILE_ROWS], branch.a)
            z = first.new_empty((len(rows), first.shape[1]))
            z[:TILE_ROWS].copy_(first)
            for start in range(TILE_ROWS, len(rows), TILE_ROWS):
                z[start:start + TILE_ROWS].copy_(F.linear(rows[start:start + TILE_ROWS], branch.a))
            cache['bytes'] += size
            cache[key] = (rows.data_ptr(), rows.shape, z)
    return z


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
        whole = _down(branch, rows) if channels is not None else None
        if whole is not None and whole.shape[1] <= 256:
            from .lora_kernels import up_add
            up_add(whole, b, target, branch.scale)
            continue
        # Bound workspace independently of the video/reference token count.
        for start in range(0, len(rows), TILE_ROWS):
            z = whole[start:start + TILE_ROWS] if whole is not None else F.linear(rows[start:start + TILE_ROWS], branch.a)
            result = target[start:start + TILE_ROWS]
            if z.is_cuda and z.dtype in (torch.bfloat16, torch.float16) and z.shape[1] <= 256:
                from .lora_kernels import up_add
                up_add(z, b, result, branch.scale)
            else:
                result.add_(F.linear(z, b), alpha=branch.scale)
    return output


def quantized(module, raw, xq, xs):
    return apply(module, raw, module.forward_quantized(xq, xs, out_dtype=raw.dtype))
