"""Bounded projections without changing the official FP8 activation scale scope."""
import types

import torch
import triton
import triton.language as tl

from .triton_compat import activate
activate()

from src.models.ops import fp8_linear as official


@triton.jit
def _cast_with_scale(X, Y, S, N, K: tl.constexpr, ROWWISE: tl.constexpr, BLOCK: tl.constexpr):
    offsets = tl.program_id(0).to(tl.int64) * BLOCK + tl.arange(0, BLOCK)
    mask = offsets < N
    scale = tl.load(S + offsets // K, mask=mask, other=1.) if ROWWISE else tl.load(S)
    value = tl.load(X + offsets, mask=mask, other=0.).to(tl.float32) / scale
    value = tl.minimum(tl.maximum(value, -448.), 448.)
    tl.store(Y + offsets, value.to(Y.dtype.element_ty), mask=mask)


def quantize_fixed_scale(value, scale):
    rows = value.reshape(-1, value.shape[-1]).contiguous()
    if scale.numel() not in (1, len(rows)):
        raise ValueError('Expected a global scale or one scale for every row')
    result = torch.empty_like(rows, dtype=official.FP8_DTYPE)
    _cast_with_scale[(triton.cdiv(rows.numel(), 8192),)](
        rows, result, scale.contiguous(), rows.numel(), rows.shape[1], scale.numel() != 1,
        BLOCK=8192, num_warps=8)
    return result, scale


class ColumnScale:
    """Accumulate the scale of a matrix generated as disjoint head columns."""
    def __init__(self, rows, device):
        self.per_tensor = official.per_tensor_gemm()
        self.amax = torch.zeros(1 if self.per_tensor else rows, device=device, dtype=torch.float32)

    def update(self, columns):
        columns = columns.contiguous()
        if self.per_tensor:
            official._absmax_kernel[(triton.cdiv(columns.numel(), 8192),)](
                columns.view(-1), self.amax, columns.numel(), BLOCK=8192, num_warps=8)
        else:
            self.amax.copy_(torch.maximum(self.amax, columns.abs().amax(dim=1).float()))

    def finish(self):
        return (self.amax / official._FP8_MAX).clamp_min(1e-12).reshape(-1, 1)


def row_scale(scale, rows):
    return scale if scale.numel() == 1 else scale[rows].contiguous()


def project_with_scale(module, value, scale=None):
    if scale is None or not isinstance(module, official.Fp8Linear):
        return module(value)
    return module.forward_quantized(*quantize_fixed_scale(value, scale), out_dtype=value.dtype)


def sliced_projection(module, value, channels, quantized=None):
    from .weight_only import WeightOnlyLinear
    if isinstance(module, WeightOnlyLinear):
        return module.project(value, channels)
    if not isinstance(module, official.Fp8Linear):
        return torch.nn.functional.linear(value, module.weight[channels],
                                          None if module.bias is None else module.bias[channels])
    quantized = official.quantize_activation(value) if quantized is None else quantized
    x_fp8, scale = quantized
    weight_scale = module.weight_scale if module.weight_scale.numel() == 1 else module.weight_scale[:, channels].contiguous()
    from .fp8_gemm import scaled_mm
    result = scaled_mm(x_fp8, module.weight_fp8[channels].t(), scale, weight_scale,
                       out_dtype=value.dtype, implementation=getattr(module, 'freevideo_fp8_gemm', 'torch'))
    return result if module.bias is None else result + module.bias[channels]


def install_chunked_ff(module, chunk, recompute=False):
    """Keep global scales on SM100+; optionally recompute tiles to avoid a 2 GB stash.

    Rowwise-scale devices can finish each row tile immediately. A change in GEMM
    row shape may still alter rounding, and is validated as a numerical ablation.
    """
    if chunk < 1:
        raise ValueError('FP8 FF chunk must be positive')
    up, _, down = module.net
    if not isinstance(up.proj, official.Fp8Linear) or not isinstance(down, official.Fp8Linear):
        raise ValueError('Chunked FP8 FF requires both official FP8 projections')

    def forward(self, hidden):
        shape = hidden.shape
        rows = hidden.reshape(-1, shape[-1])
        x_fp8, x_scale = official.quantize_activation(rows)
        width = self.net[2].weight_fp8.shape[1]
        output = torch.empty_like(rows)
        if not official.per_tensor_gemm():
            for start in range(0, len(rows), chunk):
                section = slice(start, start + chunk)
                h = self.net[0].proj.forward_quantized(x_fp8[section], row_scale(x_scale, section), out_dtype=rows.dtype)
                output[section] = self.net[2].forward_quantized(*official.swiglu_quantize(h), out_dtype=rows.dtype)
            return output.reshape(shape)
        try:
            activation = None if recompute else torch.empty((len(rows), width), device=rows.device, dtype=rows.dtype)
        except torch.cuda.OutOfMemoryError as error:
            # Identify this allocation precisely so automatic recovery can use
            # the existing same-shape, global-scale recompute path. Other CUDA
            # errors and unrelated OOMs are not relabelled as stash failures.
            error.freevideo_allocation = dict(fp8_ff_activation_stash_bytes=len(rows)*width*rows.element_size())
            raise
        maxima = torch.empty(len(rows), device=rows.device, dtype=torch.float32)
        for start in range(0, len(rows), chunk):
            section = slice(start, min(start + chunk, len(rows)))
            h = self.net[0].proj.forward_quantized(x_fp8[section], x_scale, out_dtype=rows.dtype)
            tile = torch.empty((len(h), width), device=rows.device, dtype=rows.dtype) if recompute else activation[section]
            official._swiglu_rowmax_kernel[(len(h),)](h, tile, maxima[section], width, BLOCK_K=2048, num_warps=16)
            del h, tile
        scale = (maxima.amax() / official._FP8_MAX).clamp_min(1e-12).reshape(1, 1)
        del maxima
        if not recompute:
            del x_fp8, x_scale
        for start in range(0, len(rows), chunk):
            section = slice(start, min(start + chunk, len(rows)))
            if recompute:
                h = self.net[0].proj.forward_quantized(x_fp8[section], x_scale, out_dtype=rows.dtype)
                tile = torch.empty((len(h), width), device=rows.device, dtype=rows.dtype)
                unused_maxima = torch.empty(len(h), device=rows.device, dtype=torch.float32)
                official._swiglu_rowmax_kernel[(len(h),)](h, tile, unused_maxima, width, BLOCK_K=2048, num_warps=16)
                del h, unused_maxima
            else:
                tile = activation[section]
            output[section] = self.net[2].forward_quantized(*quantize_fixed_scale(tile, scale), out_dtype=rows.dtype)
            del tile
        return output.reshape(shape)

    module.forward = types.MethodType(forward, module)
