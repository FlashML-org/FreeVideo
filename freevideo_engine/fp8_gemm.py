"""Bounded FP8 GEMM for wheels without PyTorch's rowwise CUTLASS build.

PyTorch 2.13 excludes RowwiseScaledMM.cu on Windows, including SM89.
cuBLAS scalar-scale FP8 GEMM is still available. Keep the official quantized
operands and apply their row/column scales to its FP32 accumulator in tiles.
This is FP8 compute, with no BF16 weight copy or scale recalibration.
"""
from functools import lru_cache
import types

import torch
import triton
import triton.language as tl

from .triton_compat import activate
activate()

from .kernel_capabilities import fp8_implementation

ACCUMULATOR_BYTES = 32 * 1024 * 1024
_CALLS = dict(torch_scaled_mm=0, cublas_fp8=0, rescale=0, triton_ff_up=0)


def execution_counts():
    """Submitted kernel calls, without synchronizing or retaining tensors."""
    return dict(_CALLS)


@lru_cache(maxsize=16)
def _fused_ff_device(device):
    return torch.cuda.get_device_capability(device) == (8, 9)


@triton.jit
def _fused_rowwise_ff_up(X, W, XS, WS, OUT, M: tl.constexpr, N: tl.constexpr,
                        K: tl.constexpr, BM: tl.constexpr, BN: tl.constexpr,
                        BK: tl.constexpr):
    rows = tl.program_id(0) * BM + tl.arange(0, BM)
    cols = tl.program_id(1) * BN + tl.arange(0, BN)
    kk = tl.arange(0, BK)
    xp = X + rows[:, None] * K + kk[None, :]
    wp = W + cols[None, :] * K + kk[:, None]
    accum = tl.full((BM, BN), 0, tl.float32)
    for k in range(tl.cdiv(K, BK)):
        x = tl.load(xp, (rows[:, None] < M) & (kk[None, :] + k * BK < K), other=0.)
        w = tl.load(wp, (cols[None, :] < N) & (kk[:, None] + k * BK < K), other=0.)
        accum = tl.dot(x, w, accum)
        xp += BK
        wp += BK
    xs = tl.load(XS + rows, rows < M, other=1.)
    ws = tl.load(WS + cols, cols < N, other=1.)
    result = (accum * xs[:, None]) * ws[None, :]
    tl.store(OUT + rows[:, None] * N + cols[None, :], result.to(OUT.dtype.element_ty),
             (rows[:, None] < M) & (cols[None, :] < N))


@triton.jit
def _rescale(A, X_SCALE, W_SCALE, OUT, COUNT: tl.constexpr, WIDTH: tl.constexpr,
             X_ROWWISE: tl.constexpr, W_COLUMNWISE: tl.constexpr, BLOCK: tl.constexpr):
    offsets = tl.program_id(0).to(tl.int64) * BLOCK + tl.arange(0, BLOCK)
    valid = offsets < COUNT
    value = tl.load(A + offsets, valid, other=0.)
    xs = tl.load(X_SCALE + offsets // WIDTH, valid, other=1.) if X_ROWWISE else tl.load(X_SCALE)
    ws = tl.load(W_SCALE + offsets % WIDTH, valid, other=1.) if W_COLUMNWISE else tl.load(W_SCALE)
    # Match the CUTLASS epilogue order; cast before the caller adds bias.
    value = (value * xs) * ws
    tl.store(OUT + offsets, value.to(OUT.dtype.element_ty), valid)


def scaled_mm(x, weight_t, scale_a, scale_b, *, out_dtype, implementation='torch',
              accumulator_bytes=ACCUMULATOR_BYTES):
    if implementation == 'torch':
        _CALLS['torch_scaled_mm'] += 1
        return torch._scaled_mm(x, weight_t, scale_a=scale_a, scale_b=scale_b,
                                out_dtype=out_dtype, use_fast_accum=True)
    if implementation != 'scaled-mm-epilogue':
        raise ValueError('Unknown FP8 GEMM implementation: ' + implementation)
    rows, width = x.shape[0], weight_t.shape[1]
    if (x.ndim != 2 or weight_t.ndim != 2 or x.shape[1] != weight_t.shape[0]
            or scale_a.numel() not in (1, rows) or scale_b.numel() not in (1, width)
            or scale_a.dtype != torch.float32 or scale_b.dtype != torch.float32):
        raise ValueError('Expected FP8 matrices with FP32 scalar or row/column scales')
    tile_rows = accumulator_bytes // (4 * width) if width else 0
    if tile_rows < 1:
        raise ValueError('FP8 accumulator budget cannot hold one output row')
    # The full H3 FF-up tile benefits on native Windows Ada; its small tail
    # does not. Preserve the other shapes and native Torch dispatch. Fuse the
    # same FP32 row/column scaling and BF16 rounding, before the caller's bias,
    # without allocating an FP32 accumulator in global memory.
    if (out_dtype == torch.bfloat16 and x.shape == (2048, 5376)
            and weight_t.shape == (5376, 28672) and x.is_cuda
            and x.dtype == weight_t.dtype == torch.float8_e4m3fn
            and x.is_contiguous() and weight_t.t().is_contiguous()
            and scale_a.numel() == rows and scale_b.numel() == width
            and _fused_ff_device(x.device)):
        output = torch.empty((rows, width), dtype=out_dtype, device=x.device)
        _fused_rowwise_ff_up[(16, 224)](
            x, weight_t.t(), scale_a.contiguous(), scale_b.contiguous(), output,
            rows, width, x.shape[1], BM=128, BN=128, BK=64,
            num_warps=8, num_stages=3, enable_fp_fusion=False)
        _CALLS['triton_ff_up'] += 1
        return output
    # Keep normal tiles aligned; PyTorch also accepts the final partial tile.
    tile_rows = tile_rows // 16 * 16 if tile_rows >= 16 else tile_rows
    output = torch.empty((rows, width), dtype=out_dtype, device=x.device)
    unit = torch.ones((1, 1), dtype=torch.float32, device=x.device)
    scale_a, scale_b = scale_a.contiguous(), scale_b.contiguous()
    for start in range(0, rows, tile_rows):
        stop = min(rows, start + tile_rows)
        raw = torch._scaled_mm(x[start:stop], weight_t, scale_a=unit, scale_b=unit,
                               out_dtype=torch.float32, use_fast_accum=True)
        _CALLS['cublas_fp8'] += 1
        xs = scale_a if scale_a.numel() == 1 else scale_a.reshape(-1)[start:stop]
        _rescale[(triton.cdiv(raw.numel(), 1024),)](
            raw, xs, scale_b, output[start:stop], raw.numel(), width,
            scale_a.numel() != 1, scale_b.numel() != 1, BLOCK=1024,
            enable_fp_fusion=False)
        _CALLS['rescale'] += 1
        del raw, xs
    return output


def _forward_quantized(module, x_fp8, x_scale, out_dtype=None):
    value = scaled_mm(x_fp8, module.weight_fp8.t(), x_scale, module.weight_scale,
                      out_dtype=out_dtype or torch.bfloat16,
                      implementation=module.freevideo_fp8_gemm)
    return value if module.bias is None else value + module.bias


def install(model, scale_granularity, *, requested='auto', system=None):
    """Bind every official projection, including fused FF and sliced heads."""
    import platform
    from src.models.ops.fp8_linear import Fp8Linear
    selected = fp8_implementation(system or platform.system(), scale_granularity, requested)
    for module in model.modules():
        if isinstance(module, Fp8Linear):
            module.freevideo_fp8_gemm = selected
            if selected != 'torch':
                module.forward_quantized = types.MethodType(_forward_quantized, module)
    return selected
