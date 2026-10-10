"""Int8 weights for the language model when its bf16 weights do not fit the GPU.

Each output row keeps one scale (symmetric, per channel); activations stay
bf16. Decoding multiplies one token at a time, which reads every weight once:
a Triton kernel reads the int8 rows directly. Longer inputs (the prompt and
images) widen the int8 weight to bf16 one linear at a time for cuBLAS.
"""
import torch
from torch import nn
from torch.nn import functional as F

ROWS = 16  # Up to this many input rows use the int8 kernel.
PREFILL_ROWS = 512


def quantize(weight, rows=2048):
    """bf16 [out, in] -> int8 [out, in], fp32 [out]; runs on the weight's device.

    A few rows at a time, so the fp32 temporaries stay small on 8 GB cards.
    """
    quantized = torch.empty(weight.shape, dtype=torch.int8, device=weight.device)
    scale = torch.empty(weight.shape[0], dtype=torch.float32, device=weight.device)
    for start in range(0, weight.shape[0], rows):
        value = weight[start:start + rows].float()
        step = value.abs().amax(dim=1).clamp_(min=1e-8) / 127
        quantized[start:start + rows] = torch.round(value / step[:, None]).clamp_(-127, 127)
        scale[start:start + rows] = step
    return quantized, scale


class Int8Linear(nn.Module):
    def __init__(self, source, device):
        super().__init__()
        self.in_features, self.out_features = source.in_features, source.out_features
        quantized, scale = quantize(source.weight.detach().to(device))
        self.register_buffer('weight_int8', quantized)
        self.register_buffer('scale', scale)
        bias = source.bias
        self.register_buffer('bias', None if bias is None else bias.detach().to(device))

    def forward(self, x):
        shape = x.shape
        rows = x.reshape(-1, self.in_features)
        if rows.shape[0] <= ROWS and rows.is_cuda:
            out = _small(rows, self.weight_int8, self.scale)
        else:
            # int8 values are exact in bf16; scale the output (in fp32) instead
            # of the weight. A few hundred rows at a time bounds the fp32 copy.
            weight = self.weight_int8.to(rows.dtype)
            out = torch.empty((rows.shape[0], self.out_features), device=rows.device, dtype=rows.dtype)
            for start in range(0, rows.shape[0], PREFILL_ROWS):
                part = F.linear(rows[start:start + PREFILL_ROWS], weight)
                out[start:start + PREFILL_ROWS] = part.float().mul_(self.scale)
            del weight
        if self.bias is not None:
            out = out + self.bias
        return out.reshape(*shape[:-1], self.out_features)


_kernel = None


def _small(rows, quantized, scale):
    global _kernel
    if _kernel is None:
        _kernel = _build()
    m, k = rows.shape
    n = quantized.shape[0]
    rows = rows.contiguous()
    out = torch.empty((m, n), device=rows.device, dtype=rows.dtype)
    block_n = 64
    _kernel[(triton_cdiv(n, block_n),)](rows, quantized, scale, out, m, n, k,
                                        rows.stride(0), quantized.stride(0), out.stride(0),
                                        BM=16, BN=block_n, BK=128, num_warps=4)
    return out


def triton_cdiv(a, b):
    return (a + b - 1) // b


def _build():
    import triton
    import triton.language as tl
    from ..triton_compat import activate
    activate()

    @triton.jit
    def kernel(X, Q, S, O, M, N, K, SXM, SQN, SOM, BM: tl.constexpr, BN: tl.constexpr, BK: tl.constexpr):
        n = tl.program_id(0) * BN + tl.arange(0, BN)
        m = tl.arange(0, BM)
        acc = tl.zeros((BM, BN), dtype=tl.float32)
        for start in range(0, K, BK):
            k = start + tl.arange(0, BK)
            x = tl.load(X + m[:, None] * SXM + k[None, :], mask=(m[:, None] < M) & (k[None, :] < K), other=0.)
            q = tl.load(Q + n[:, None] * SQN + k[None, :], mask=(n[:, None] < N) & (k[None, :] < K), other=0)
            acc += tl.dot(x, tl.trans(q.to(tl.bfloat16)))
        s = tl.load(S + n, mask=n < N, other=0.)
        tl.store(O + m[:, None] * SOM + n[None, :], (acc * s[None, :]).to(O.dtype.element_ty),
                 mask=(m[:, None] < M) & (n[None, :] < N))
    return kernel


def projections(layer):
    """(parent, name) of a language-model layer's projections after core.merge_layer()."""
    return [(layer.self_attn, 'qkv_proj'), (layer.self_attn, 'o_proj'),
            (layer.mlp, 'gate_up_proj'), (layer.mlp, 'down_proj')]


def saved_bytes(stored):
    """GPU bytes int8 saves over bf16 for the stored weights core.merge_layer() merges:
    half of each language-model projection, less its fp32 scales."""
    import re
    projection = re.compile(r'model\.language_model\.layers\.\d+\.(self_attn\.[qkvo]_proj|mlp\.(gate|up|down)_proj)\.weight$')
    return sum(row['bytes'] // 2 - 4 * row['shape'][0] for name, row in stored.items() if projection.match(name))


def install(layer, device):
    """Quantize a merged layer's projections on the GPU, dropping each bf16 copy."""
    for parent, name in projections(layer):
        source = getattr(parent, name)
        setattr(parent, name, Int8Linear(source, device))
        del source
