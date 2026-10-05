# Copyright 2026 The MiniMax and HuggingFace Teams. All rights reserved.
# Copyright 2026 FreeVideo contributors.
# Licensed under the Apache License, Version 2.0.
# Block ordering follows the pinned Diffusers MiniMaxH3TransformerBlock.
"""Metal indexed AdaLN arithmetic with the eager rounding boundaries.

Keep RMSNorm and checked row indices in PyTorch. Select the small modulation
table inside the fused kernel, avoiding full activation-sized gathered tables
and arithmetic temporaries. The checked index selection has one scalar column.
This module is used only by the explicit MPS block adapter.
"""
from functools import lru_cache
import types

IMPLEMENTATION = 'mps-indexed-rounded-modulation-v2'

SOURCE = r'''
#include <metal_stdlib>
using namespace metal;
#pragma clang fp contract(off)

template <typename T>
void modulate(device T* out, device const T* x, device const T* scale,
              device const T* shift, device const long* indices,
              constant uint* dims, uint i) {
    uint width = dims[0], rows = dims[1], channel = i % width;
    ulong row = ulong(indices[(i / width) % rows]);
    ulong sj = row * dims[2] + channel * dims[3];
    ulong bj = row * dims[4] + channel * dims[5];
    T factor = T(1.f + float(scale[sj]));
    T product = T(float(x[i]) * float(factor));
    out[i] = T(float(product) + float(shift[bj]));
}
template <typename R, typename G, typename B, typename P, typename O>
void post(device O* out, device const R* residual, device const G* gate,
          device const B* branch, device const long* indices,
          constant uint* dims, uint i) {
    ulong row = ulong(indices[(i / dims[0]) % dims[1]]);
    ulong j = row * dims[2] + (i % dims[0]) * dims[3];
    P product = P(float(gate[j]) * float(branch[i]));
    out[i] = O(float(residual[i]) + float(product));
}
#define MOD(NAME, T) \
kernel void NAME(device T* o [[buffer(0)]], device const T* x [[buffer(1)]], \
    device const T* s [[buffer(2)]], device const T* b [[buffer(3)]], \
    device const long* ix [[buffer(4)]], constant uint* d [[buffer(5)]], \
    uint i [[thread_position_in_grid]]) { modulate(o,x,s,b,ix,d,i); }
#define POST(NAME, R, G, B, P, O) \
kernel void NAME(device O* o [[buffer(0)]], device const R* x [[buffer(1)]], \
    device const G* g [[buffer(2)]], device const B* b [[buffer(3)]], \
    device const long* ix [[buffer(4)]], constant uint* d [[buffer(5)]], \
    uint i [[thread_position_in_grid]]) { post<R,G,B,P,O>(o,x,g,b,ix,d,i); }
MOD(modulate_bf16, bfloat)
MOD(modulate_f32, float)
POST(post_bbb, bfloat, bfloat, bfloat, bfloat, bfloat)
POST(post_bbf, bfloat, bfloat, float, float, float)
POST(post_bfb, bfloat, float, bfloat, float, float)
POST(post_bff, bfloat, float, float, float, float)
POST(post_fbb, float, bfloat, bfloat, bfloat, float)
POST(post_fbf, float, bfloat, float, float, float)
POST(post_ffb, float, float, bfloat, float, float)
POST(post_fff, float, float, float, float, float)
'''


@lru_cache(maxsize=1)
def _library():
    import torch
    try:
        return torch.mps.compile_shader(SOURCE)
    except RuntimeError:
        return None


def _eligible(value, *others, mixed=False):
    import torch
    return (value.device.type == 'mps' and value.dtype in (torch.bfloat16, torch.float32)
            and value.ndim == 3 and value.numel() > 0 and value.numel() < 2**32
            and value.is_contiguous() and not torch.is_grad_enabled()
            and all(t.device == value.device and (t.dtype in (torch.bfloat16, torch.float32)
                    if mixed else t.dtype == value.dtype)
                    for t in others) and _library() is not None)


def _checked_indices(table, indices):
    """Preserve index_select bounds/dtype checks without gathering wide rows."""
    import torch
    return torch.arange(table.shape[0], device=table.device, dtype=torch.int64).index_select(0, indices)


def _table_matches(value, table, indices):
    return (table.ndim == 2 and table.shape[0] > 0 and table.shape[1] == value.shape[-1]
            and indices.ndim == 1 and indices.numel() == value.shape[-2]
            and indices.device == value.device and all(0 <= s < 2**31 for s in table.stride()))


def modulate(value, scale, shift, indices, *, stats=None):
    if (not _eligible(value, scale, shift) or scale.shape != shift.shape
            or not _table_matches(value, scale, indices) or not _table_matches(value, shift, indices)):
        return value * (1.0 + scale.index_select(0, indices)) + shift.index_select(0, indices)
    import torch
    checked = _checked_indices(scale, indices)
    # Encode small constants directly; making an MPS tensor here introduces
    # a host-to-device copy in every block's hot path.
    dims = [value.shape[-1], value.shape[-2], *scale.stride(), *shift.stride()]
    output = torch.empty_like(value)
    kernel = _library().modulate_bf16 if value.dtype == torch.bfloat16 else _library().modulate_f32
    kernel(output, value, scale, shift, checked, dims, threads=value.numel(), arg_casts={5: 'int32'})
    if stats is not None:
        stats['modulation_calls'] = stats.get('modulation_calls', 0) + 1
    return output


def residual_add(residual, gate, indices, branch, *, stats=None):
    if (not _eligible(branch, residual, gate, mixed=True) or branch.shape != residual.shape
            or not residual.is_contiguous() or not _table_matches(branch, gate, indices)):
        return residual + gate.index_select(0, indices) * branch
    import torch
    checked = _checked_indices(gate, indices)
    dims = [branch.shape[-1], branch.shape[-2], *gate.stride()]
    dtype = torch.promote_types(residual.dtype, torch.promote_types(gate.dtype, branch.dtype))
    output = torch.empty_like(branch, dtype=dtype)
    suffix = ''.join('b' if t.dtype == torch.bfloat16 else 'f' for t in (residual, gate, branch))
    kernel = getattr(_library(), 'post_' + suffix)
    kernel(output, residual, gate, branch, checked, dims, threads=branch.numel(), arg_casts={5: 'int32'})
    if stats is not None:
        stats['residual_calls'] = stats.get('residual_calls', 0) + 1
    return output


def install(model):
    """Install on this model's blocks only; preserve normalization, FF and attention."""
    from diffusers.models.transformers.transformer_minimax_h3 import MiniMaxH3TransformerBlock
    blocks = tuple(model.transformer_blocks)
    if not blocks or any(not isinstance(block, MiniMaxH3TransformerBlock) for block in blocks):
        raise TypeError('Expected pinned MiniMax H3 transformer blocks')
    stats = dict(modulation_calls=0, residual_calls=0)

    def forward(owner, hidden_states, temb, adaln_indices, rotary_emb, attention_mask=None):
        shift_a, scale_a, gate_a, shift_f, scale_f, gate_f = owner.adaln_proj(temb)
        value = modulate(owner.norm1(hidden_states), scale_a, shift_a, adaln_indices, stats=stats)
        branch = owner.attn(value, rotary_emb, attention_mask)
        del value
        hidden = residual_add(hidden_states, gate_a, adaln_indices, branch, stats=stats)
        del branch
        value = modulate(owner.norm2(hidden), scale_f, shift_f, adaln_indices, stats=stats)
        return residual_add(hidden, gate_f, adaln_indices, owner.ff(value), stats=stats)

    for block in blocks:
        block.forward = types.MethodType(forward, block)
    return stats
