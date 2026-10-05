"""Linear-branch features in one Metal pass: temporal 5-tap conv, SiLU, L2 norm.

Eager MPS walks each projection's [frames, tokens, channels] tensor about ten
times (pad, five shifted products and sums, SiLU, norm, division); on an M5
the features cost 0.31 s of a 1.1 s first-pass linear branch. This kernel reads
each element's temporal taps once and writes the normalized feature once,
accumulating in FP32 with one rounding at the store, like the upstream
inference kernel. The spatial 5x5 depthwise conv stays on PyTorch.
"""
from functools import lru_cache

SOURCE = r'''
#include <metal_stdlib>
using namespace metal;

kernel void conv_silu_norm(device bfloat* out [[buffer(0)]],
                           device const bfloat* x [[buffer(1)]],
                           device const bfloat* weight [[buffer(2)]],
                           constant int* dims [[buffer(3)]],
                           uint group [[threadgroup_position_in_grid]],
                           uint lane [[thread_index_in_threadgroup]],
                           uint simd_lane [[thread_index_in_simdgroup]],
                           uint simd_id [[simdgroup_index_in_threadgroup]]) {
    threadgroup float partial[32];
    // dims: frames, tokens, channels, taps (0 = no conv), l2norm, head_dim
    const int frames = dims[0], tokens = dims[1], channels = dims[2];
    const int taps = dims[3], l2norm = dims[4], head_dim = dims[5];
    const int heads = channels / head_dim;
    const int head = int(group) % heads;
    const int token = (int(group) / heads) % tokens;
    const int frame = int(group) / (heads * tokens);
    const int channel = head * head_dim + int(lane);
    float value;
    if (taps > 0) {
        const int pad = taps / 2;
        value = 0.f;
        for (int tap = 0; tap < taps; ++tap) {
            const int source = frame + tap - pad;
            if (source >= 0 && source < frames) {
                value += float(x[(ulong(source) * tokens + token) * channels + channel])
                       * float(weight[channel * taps + tap]);
            }
        }
    } else {
        value = float(x[(ulong(frame) * tokens + token) * channels + channel]);
    }
    float y = value / (1.f + exp(-value));
    if (l2norm) {
        float square = simd_sum(y * y);
        if (simd_lane == 0) partial[simd_id] = square;
        threadgroup_barrier(mem_flags::mem_threadgroup);
        float total = 0.f;
        const int groups = (head_dim + 31) / 32;
        for (int index = 0; index < groups; ++index) total += partial[index];
        y = y / max(sqrt(total), 1e-6f);
    }
    out[(ulong(frame) * tokens + token) * channels + channel] = bfloat(y);
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


def conv_silu_norm(x, weight, *, head_dim, l2norm):
    """x [frames, tokens, channels] BF16 MPS, weight [channels, taps] or None -> same shape."""
    import torch
    if x.device.type != 'mps' or x.dtype != torch.bfloat16 or x.ndim != 3:
        raise ValueError('Fused features need a [frames, tokens, channels] BF16 MPS tensor')
    frames, tokens, channels = x.shape
    if channels % head_dim or head_dim > 1024:
        raise ValueError('Channels must be whole heads of at most 1024 features')
    x = x.contiguous()
    if weight is None:
        taps, weight = 0, x  # No weight access when the convolution is disabled.
    else:
        taps = weight.shape[1]
        weight = weight.to(device=x.device, dtype=torch.bfloat16).contiguous()
        if weight.shape[0] != channels:
            raise ValueError('Temporal weight channels differ from the features')
    out = torch.empty_like(x)
    dims = [frames, tokens, channels, taps, int(l2norm), head_dim]
    _library().conv_silu_norm(out, x, weight, dims, threads=frames * tokens * channels,
                             group_size=head_dim, arg_casts={3: 'int32'})
    return out


def feature_one(branch, tokens, proj, num_frames, frame_size, use_conv=True, inference=False,
                fhsd=None, heads=None, *, stats=None):
    """BidirectionalLinearBranch._feature_one for MPS BF16 inference."""
    import torch
    from src.models.linear_attention.branch import _HeadSliceSepConv
    conv = branch.short_conv if use_conv else None
    if conv is not None and heads is not None:
        conv = _HeadSliceSepConv(conv, heads, branch.head_dim)
    l2norm = proj != 'v'
    if (tokens.device.type != 'mps' or tokens.dtype != torch.bfloat16 or fhsd is not None
            or torch.is_grad_enabled() or not available()):
        if stats is not None:
            stats['reference_calls'] = stats.get('reference_calls', 0) + 1
        return branch._freevideo_eager_feature_one(tokens, proj, num_frames, frame_size,
            use_conv=use_conv, inference=inference, fhsd=fhsd, heads=heads)
    if stats is not None:
        stats['metal_calls'] = stats.get('metal_calls', 0) + 1
    count, dim = tokens.shape[-2], tokens.shape[-1]
    if conv is not None and proj in conv.projs:
        x, weight = conv.spatial(proj, tokens, num_frames, frame_size)
        out = conv_silu_norm(x, weight, head_dim=dim, l2norm=l2norm)
    else:
        out = conv_silu_norm(tokens.reshape(1, -1, count * dim), None, head_dim=dim, l2norm=l2norm)
    return out.reshape(-1, count, dim)


def install(model, *, stats=None):
    """Bind the fused features to this model only; leave VDN classes unchanged."""
    from functools import partial
    import types
    from src.models.linear_attention.branch import BidirectionalLinearBranch
    for branch in model.modules():
        if isinstance(branch, BidirectionalLinearBranch) and not hasattr(branch, '_freevideo_eager_feature_one'):
            branch._freevideo_eager_feature_one = branch._feature_one
            branch._feature_one = types.MethodType(partial(feature_one, stats=stats), branch)
    return 'mps-fused-conv-silu-norm-v1'
