"""FP8 storage with BF16 tensor-core compute for Ampere (W8A16).

This is a separately reported arithmetic policy, not native FP8 GEMM. Decode
E4M3 bytes inside each GEMM tile instead of materializing a BF16 weight copy.
No FP8 tensor-core instruction or native float8 conversion is required.
"""
import torch
import triton
import triton.language as tl

from .triton_compat import activate
activate()


@triton.jit
def _decode_e4m3(bits):
    bits = bits.to(tl.uint32)
    exponent = (bits >> 3) & 15
    mantissa = bits & 7
    # Reconstruct normal IEEE float bits; this avoids an exp2 per weight in
    # every K tile while remaining usable before native FP8 hardware.
    normal = ((exponent + 120) << 23) | (mantissa << 20)
    value = tl.where(exponent == 0, mantissa.to(tl.float32) * (1. / 512.),
                     normal.to(tl.float32, bitcast=True))
    value = tl.where((bits & 128) != 0, -value, value)
    return tl.where((bits & 127) == 127, float('nan'), value)


@triton.autotune(configs=[
    triton.Config({'BM': 32, 'BN': 64, 'BK': 32}, num_warps=4, num_stages=3),
    triton.Config({'BM': 64, 'BN': 64, 'BK': 32}, num_warps=4, num_stages=3),
    triton.Config({'BM': 32, 'BN': 128, 'BK': 32}, num_warps=4, num_stages=3),
], key=['M', 'N', 'K'])
@triton.jit
def _matmul(A, W, S, O, M: tl.constexpr, N: tl.constexpr, K: tl.constexpr,
            SA: tl.constexpr, SW: tl.constexpr, COLUMNWISE: tl.constexpr,
            BM: tl.constexpr, BN: tl.constexpr, BK: tl.constexpr):
    # Reuse an eight-tile band of activations across output columns in L2.
    # A full M sweep per column tile rereads the large packed input repeatedly.
    blocks_m, blocks_n = triton.cdiv(M, BM), triton.cdiv(N, BN)
    group = tl.program_id(0) // (8 * blocks_n)
    first_m = group * 8
    group_m = tl.minimum(blocks_m - first_m, 8)
    within = tl.program_id(0) % (8 * blocks_n)
    rows = (first_m + within % group_m) * BM + tl.arange(0, BM)
    cols = (within // group_m) * BN + tl.arange(0, BN)
    inner = tl.arange(0, BK)
    scale = tl.load(S + cols, cols < N, other=1.) if COLUMNWISE else tl.load(S)
    acc = tl.zeros((BM, BN), tl.float32)
    for begin in range(triton.cdiv(K, BK)):
        kk = begin * BK + inner
        a = tl.load(A + rows[:, None] * SA + kk[None, :],
                    (rows[:, None] < M) & (kk[None, :] < K), other=0.)
        bits = tl.load(W + cols[None, :] * SW + kk[:, None],
                       (cols[None, :] < N) & (kk[:, None] < K), other=0)
        weight = _decode_e4m3(bits) * scale
        acc += tl.dot(a, weight.to(a.dtype))
    tl.store(O + rows[:, None] * N + cols[None, :], acc,
             (rows[:, None] < M) & (cols[None, :] < N))


def linear(value, weight_fp8, weight_scale, bias=None):
    rows = value.reshape(-1, value.shape[-1]).contiguous()
    if rows.dtype not in (torch.bfloat16, torch.float16):
        raise ValueError('W8A16 expects BF16 or FP16 activations')
    if weight_fp8.dtype != torch.float8_e4m3fn or not weight_fp8.is_contiguous():
        raise ValueError('Expected contiguous E4M3 weights')
    if weight_scale.numel() not in (1, weight_fp8.shape[0]):
        raise ValueError('Expected scalar or per-output-channel weight scales')
    m, k = rows.shape
    n, weight_k = weight_fp8.shape
    if k != weight_k:
        raise ValueError('Projection input width differs from the weight')
    output = torch.empty((m, n), device=rows.device, dtype=rows.dtype)
    _matmul[lambda meta: (triton.cdiv(m, meta['BM']) * triton.cdiv(n, meta['BN']),)](
        rows, weight_fp8.view(torch.uint8), weight_scale.contiguous(), output,
        m, n, k, rows.stride(0), weight_fp8.stride(0), weight_scale.numel() != 1)
    if bias is not None:
        output.add_(bias)
    return output.reshape(*value.shape[:-1], n)


class WeightOnlyLinear(torch.nn.Module):
    @property
    def bias(self):
        return self.original.bias

    def project(self, value, channels):
        scales = self.weight_scale if self.weight_scale.numel() == 1 else self.weight_scale[:, channels].contiguous()
        return linear(value, self.weight_fp8[channels], scales,
                      None if self.bias is None else self.bias[channels])

    def forward(self, value):
        return linear(value, self.weight_fp8, self.weight_scale, self.bias)
