"""Decode native H3 NVFP4 weight storage into an MPS FP32 matrix.

The encoder still runs its original FP32 projections and AWQ input smoothing.
Only packed weight decoding moves to Metal; the CPU reader is the reference.
"""
from functools import lru_cache
import os


@lru_cache(maxsize=1)
def _kernel():
    import torch
    return torch.mps.compile_shader('''
        #include <metal_stdlib>
        using namespace metal;
        constant float e2m1[16] = {
            0.0f, 0.5f, 1.0f, 1.5f, 2.0f, 3.0f, 4.0f, 6.0f,
            -0.0f, -0.5f, -1.0f, -1.5f, -2.0f, -3.0f, -4.0f, -6.0f
        };
        kernel void decode_nvfp4(device float* output,
                                 device const uchar* packed,
                                 device const float* block_scales,
                                 device const float* tensor_scale,
                                 uint index [[thread_position_in_grid]]) {
            uchar byte = packed[index / 2];
            uint code = (index & 1) ? (byte & 15) : (byte >> 4);
            float block = e2m1[code] * block_scales[index / 16];
            output[index] = block * tensor_scale[0];
        }
    ''')


def decode_weight(weight, dtype, device):
    """Match nvfp4_weight's nibble order, scale order and padded output view."""
    import torch
    from comfy_kitchen.float_utils import from_blocked
    if torch.device(device).type != 'mps' or dtype != torch.float32:
        raise ValueError('Metal NVFP4 decoding requires FP32 output on MPS')
    # Torch 2.13 compiles shaders with safe math when this is unset or "0".
    # Empty or other values enable fast math in its native implementation.
    if os.environ.get('PYTORCH_MPS_FAST_MATH', '0') != '0':
        raise ValueError('Metal NVFP4 decoding requires PYTORCH_MPS_FAST_MATH=0')
    packed, params = weight._qdata, weight._params
    if (packed.device.type != 'cpu' or packed.dtype != torch.uint8
            or packed.ndim != 2 or not packed.is_contiguous()
            or getattr(params, 'transposed', False)):
        raise ValueError('Expected contiguous untransposed CPU NVFP4 storage')
    rows, columns = packed.shape[0], packed.shape[1] * 2
    original = tuple(params.orig_shape)
    if (not rows or not columns or columns % 16 or params.scale.numel() != 1
            or params.scale.device.type != 'cpu' or params.block_scale.device.type != 'cpu'
            or len(original) != 2 or any(type(n) is not int or n < 1 for n in original)
            or original[0] > rows or original[1] > columns):
        raise ValueError('Invalid NVFP4 scale or original shape geometry')
    # Unblocking the small scale plane is CPU work; the expanded weight never
    # occupies a full CPU FP32 matrix. Preserve the reference's two multiplies.
    scales = from_blocked(params.block_scale, num_rows=rows, num_cols=columns // 16).float()
    result = torch.empty((rows, columns), dtype=dtype, device=device)
    packed_device = packed.to(device)
    scales_device = scales.contiguous().to(device)
    tensor_scale = params.scale.float().reshape(-1).to(device)
    _kernel().decode_nvfp4(result, packed_device, scales_device, tensor_scale)
    return result[:original[0], :original[1]]
