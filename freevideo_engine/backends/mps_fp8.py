"""Decode E4M3 storage directly into BF16 MPS buffers with explicit rounding.

Only weight storage is FP8. Model projections still execute in BF16. The CPU
decoder remains the numerical reference and neither path enters CUDA imports.
"""
from functools import lru_cache


@lru_cache(maxsize=1)
def _kernel():
    import torch
    library = torch.mps.compile_shader('''
        #include <metal_stdlib>
        using namespace metal;
        kernel void decode_e4m3(device ushort* output,
                               device const uchar* packed,
                               device const float* scales,
                               device const float* values,
                               device const int* config,
                               uint index [[thread_position_in_grid]]) {
            uint row = config[1] ? index / config[0] : 0;
            float decoded = values[packed[index]] * scales[row];
            uint bits = as_type<uint>(decoded);
            output[index] = (bits & 0x7fffffffu) > 0x7f800000u
                ? ushort((bits >> 16) | 0x0040u)
                : ushort((bits + 0x7fffu + ((bits >> 16) & 1u)) >> 16);
        }
    ''')
    values = torch.arange(256, dtype=torch.uint8).view(torch.float8_e4m3fn).float().to('mps')
    return library, values


def decode_weight(weight, scale):
    import torch
    if (weight.device.type != 'cpu' or scale.device.type != 'cpu' or weight.ndim != 2
            or weight.dtype != torch.float8_e4m3fn or not weight.is_contiguous()
            or scale.dtype != torch.float32 or scale.numel() not in (1, weight.shape[0])):
        raise ValueError('Expected contiguous CPU E4M3 matrix and FP32 scalar/row scales')
    library, values = _kernel()
    result = torch.empty(weight.shape, dtype=torch.bfloat16, device='mps')
    packed = weight.view(torch.uint8).to('mps')
    scales = scale.reshape(-1).to('mps')
    config = torch.tensor([weight.shape[1], int(scale.numel() > 1)], dtype=torch.int32, device='mps')
    library.decode_e4m3(result, packed, scales, values, config)
    return result
