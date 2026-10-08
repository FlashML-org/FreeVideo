"""Coalesce FP8 FFN rows while retaining the reference 2048-row tail."""
import types
import torch
from src.models.ops import fp8_linear as official
from .fp8_ops import quantize_fixed_scale, row_scale
from .lora_online import apply as apply_lora, quantized as lora_quantized


def install_tail_preserving_ff(module, chunk=8192, recompute=False):
    """Retain global activation scales and the reference GEMM shape for the tail.

    Full 2048-row tiles are coalesced into larger tiles. The final partial tile
    keeps its original row count because changing it can select a different
    GEMM implementation and alter rounding on the validated gfx1201 stack.
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
                h = lora_quantized(self.net[0].proj, rows[section], x_fp8[section], row_scale(x_scale, section))
                quantized = official.swiglu_quantize(h)
                result = self.net[2].forward_quantized(*quantized, out_dtype=rows.dtype)
                if hasattr(self.net[2], '_freevideo_lora'):
                    tile = torch.empty((len(h), width), device=rows.device, dtype=rows.dtype)
                    maxima = torch.empty(len(h), device=rows.device, dtype=torch.float32)
                    official._swiglu_rowmax_kernel[(len(h),)](h, tile, maxima, width, BLOCK_K=2048, num_warps=16)
                    apply_lora(self.net[2], tile, result)
                    del tile, maxima
                output[section] = result
                del h, quantized, result
            return output.reshape(shape)
        maxima = torch.empty(len(rows), device=rows.device, dtype=torch.float32)
        # Preserve the original partial tile, especially below 1024 rows.
        prefix_end = len(rows) - len(rows) % 2048
        sections = [slice(start, min(start + chunk, prefix_end)) for start in range(0, prefix_end, chunk)]
        if prefix_end < len(rows):
            sections.append(slice(prefix_end, len(rows)))
        stash = []
        try:
            for section in sections:
                h = lora_quantized(self.net[0].proj, rows[section], x_fp8[section], x_scale)
                tile = torch.empty((len(h), width), device=rows.device, dtype=rows.dtype)
                official._swiglu_rowmax_kernel[(len(h),)](h, tile, maxima[section], width, BLOCK_K=2048, num_warps=16)
                if not recompute:
                    stash.append(tile)
                del h, tile
        except torch.cuda.OutOfMemoryError as error:
            # Recompute allocates the same per-tile buffers but retains none,
            # so only a failure with stashed tiles is one it can avoid. Label
            # exactly those for automatic recovery; leave other OOMs alone.
            if stash:
                error.freevideo_allocation = dict(fp8_ff_activation_stash_bytes=len(rows)*width*rows.element_size())
            raise
        scale = (maxima.amax() / official._FP8_MAX).clamp_min(1e-12).reshape(1, 1)
        del maxima
        if not recompute:
            del x_fp8, x_scale
        for index, section in enumerate(sections):
            if recompute:
                h = lora_quantized(self.net[0].proj, rows[section], x_fp8[section], x_scale)
                tile = torch.empty((len(h), width), device=rows.device, dtype=rows.dtype)
                unused_maxima = torch.empty(len(h), device=rows.device, dtype=torch.float32)
                official._swiglu_rowmax_kernel[(len(h),)](h, tile, unused_maxima, width, BLOCK_K=2048, num_warps=16)
                del h, unused_maxima
            else:
                tile, stash[index] = stash[index], None
            quantized, tile_scale = quantize_fixed_scale(tile, scale)
            down = self.net[2]
            if down.bias is None and not hasattr(down, '_freevideo_lora') and getattr(down, 'freevideo_fp8_gemm', 'torch') == 'torch':
                torch.ops.aten._scaled_mm.out(quantized, down.weight_fp8.t(), tile_scale, down.weight_scale,
                    out_dtype=rows.dtype, use_fast_accum=True, out=output[section])
            else:
                output[section] = lora_quantized(down, tile, quantized, tile_scale)
            del quantized, tile_scale
            del tile
        return output.reshape(shape)

    forward._freevideo_rocm_chunk = chunk
    module.forward = types.MethodType(forward, module)
