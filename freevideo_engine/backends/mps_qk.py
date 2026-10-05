"""Q/K RMSNorm and H3 rotary embedding in one Metal pass per head row.

The eager adapter normalizes and rotates in row chunks: about a dozen kernels
and full reads/writes per chunk, 0.15 s of a first-pass block and 0.7 s of a
full-resolution block on an M5. Here each threadgroup owns one token's head:
FP32 sum of squares, the RMSNorm weight, rounding to BF16 where the reference
stores the normalized row, then rotate-half over the leading rotary channels
with BF16 cos/sin, as `_apply_rotary_emb` casts them.
"""
from functools import lru_cache

SOURCE = r'''
#include <metal_stdlib>
using namespace metal;

kernel void norm_rope(device bfloat* out [[buffer(0)]],
                      device const bfloat* x [[buffer(1)]],
                      device const bfloat* weight [[buffer(2)]],
                      device const bfloat* cos_table [[buffer(3)]],
                      device const bfloat* sin_table [[buffer(4)]],
                      constant int* dims [[buffer(5)]],
                      constant float* eps [[buffer(6)]],
                      uint group [[threadgroup_position_in_grid]],
                      uint lane [[thread_index_in_threadgroup]],
                      uint simd_lane [[thread_index_in_simdgroup]],
                      uint simd_id [[simdgroup_index_in_threadgroup]]) {
    threadgroup float partial[32];
    threadgroup float row[1024];
    // dims: tokens, heads, head_dim, rotary_dim, row stride (elements) of x, head stride of x
    const int heads = dims[1], head_dim = dims[2], rotary = dims[3];
    const long token_stride = dims[4], head_stride = dims[5];
    const int head = int(group) % heads;
    const int token = int(group) / heads;
    const int channel = int(lane);
    const float value = float(x[long(token) * token_stride + long(head) * head_stride + channel]);
    float square = simd_sum(value * value);
    if (simd_lane == 0) partial[simd_id] = square;
    threadgroup_barrier(mem_flags::mem_threadgroup);
    float total = 0.f;
    const int groups = (head_dim + 31) / 32;
    for (int index = 0; index < groups; ++index) total += partial[index];
    const float scale = rsqrt(total / float(head_dim) + eps[0]);
    const float normed = float(bfloat(value * scale * float(weight[channel])));
    row[channel] = normed;
    threadgroup_barrier(mem_flags::mem_threadgroup);
    float result = normed;
    if (channel < rotary) {
        const int half_width = rotary / 2;
        const float rotated = channel < half_width ? -row[channel + half_width] : row[channel - half_width];
        const float c = float(cos_table[long(token) * rotary + channel]);
        const float s = float(sin_table[long(token) * rotary + channel]);
        result = normed * c + rotated * s;
    }
    out[(long(token) * heads + head) * head_dim + channel] = bfloat(result);
}
'''


@lru_cache(maxsize=1)
def _library():
    import torch
    try:
        return torch.mps.compile_shader(SOURCE)
    except Exception:          # Older Metal compilers lack bfloat; keep eager.
        return None


def available():
    return _library() is not None


def norm_rope(value, norm, rotary_emb):
    """value [tokens, heads, head_dim] BF16 MPS (any token/head stride) -> contiguous result."""
    import torch
    tokens, heads, head_dim = value.shape
    if value.stride(-1) != 1:
        value = value.contiguous()
    if rotary_emb is None:
        cos = sin = value  # The kernel does not read rotary tables when rotary=0.
        rotary = 0
    else:
        cos, sin = (t.to(device=value.device, dtype=torch.bfloat16).contiguous() for t in rotary_emb)
        rotary = cos.shape[-1]
        if cos.shape != (tokens, rotary) or rotary > head_dim or rotary % 2:
            raise ValueError('Rotary tables differ from the query/key rows')
    weight = norm.weight.to(device=value.device, dtype=torch.bfloat16).contiguous()
    out = torch.empty((tokens, heads, head_dim), dtype=torch.bfloat16, device=value.device)
    dims = [tokens, heads, head_dim, rotary, value.stride(0), value.stride(1)]
    eps = float(norm.eps if norm.eps is not None else torch.finfo(torch.float32).eps)
    _library().norm_rope(out, value, weight, cos, sin, dims, eps,
                         threads=tokens * heads * head_dim, group_size=head_dim, arg_casts={5: 'int32'})
    return out


def prepare_qk(value, norm, rotary_emb, chunk, *, stats=None):
    """Drop-in for mps_attention.prepare_qk on BF16 MPS rows; other inputs stay eager."""
    import torch
    from . import mps_attention
    usable = (value.device.type == 'mps' and value.dtype == torch.bfloat16 and value.shape[-1] <= 1024
              and isinstance(norm, torch.nn.RMSNorm) and norm.weight is not None
              and not torch.is_grad_enabled() and available())
    if not usable:
        if stats is not None:
            stats['reference_calls'] = stats.get('reference_calls', 0) + 1
        return mps_attention.prepare_qk(value, norm, rotary_emb, chunk)
    if stats is not None:
        stats['metal_calls'] = stats.get('metal_calls', 0) + 1
    return norm_rope(value, norm, rotary_emb)
