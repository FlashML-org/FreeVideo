"""Int8 projections on Apple M5 TensorOps for the large H3 linear layers.

Activations take one scale per row (absmax / 127) and weights one per output
channel; exact int32 products are scaled back to BF16. On an M5 with a 10-core
GPU the products ran at 20.7-21.2 TOPS, against 13.7-14.6 TFLOPS for torch MPS
BF16 matmuls, at the 4,096- and 18,144-row block shapes; the 14,336-wide FC2
input reached 16.9-19.1 TOPS. The tiled TensorOps matmul is adapted from
antirez/h3.c (MIT). Other Apple GPUs keep the BF16 path.

ConvRot weights (rows rotated by a group-256 regular Hadamard transform before
quantization, the convention of ComfyUI's int8 H3 checkpoints) get the same
rotation on each activation row inside the quantizer. On real block-10 inputs
it cut the output error of FC2 from 5.9% to 1.2% and of every large projection
to 0.75-1.3%, against 1.8-2.6% for the FP8 weights of the BF16 path.
"""
from functools import lru_cache

TILE = 128

ROTATE = r'''
constant constexpr uint MAXG2 = 7;

inline void rotate_group2(thread float (&v)[8], uint lane) {
    // Digit 0: registers 0-3 and 4-7. Row d of H4 has its -1 at column 3 - d.
    for (uint b = 0; b < 8; b += 4) {
        float a0 = v[b], a1 = v[b + 1], a2 = v[b + 2], a3 = v[b + 3];
        float s = a0 + a1 + a2 + a3;
        v[b] = s - 2.0f * a3; v[b + 1] = s - 2.0f * a2; v[b + 2] = s - 2.0f * a1; v[b + 3] = s - 2.0f * a0;
    }
    // Digit 1 = register bit 2 + 2 * lane bit 0.
    uint low = lane & 1u;
    for (uint j = 0; j < 4; j++) {
        float own0 = v[j], own1 = v[j + 4];
        float other0 = simd_shuffle_xor(own0, 1), other1 = simd_shuffle_xor(own1, 1);
        float a0 = low ? other0 : own0, a1 = low ? other1 : own1;
        float a2 = low ? own0 : other0, a3 = low ? own1 : other1;
        float s = a0 + a1 + a2 + a3;
        // This lane keeps digit values 2*low (register j) and 1 + 2*low (register j + 4).
        v[j] = s - 2.0f * (low ? a1 : a3);
        v[j + 4] = s - 2.0f * (low ? a0 : a2);
    }
    // Digits 2 and 3: the partner with digit 3 - d is lane ^ (3 << shift).
    for (uint j = 0; j < 8; j++) {
        float a = v[j];
        float s = a + simd_shuffle_xor(a, 2);
        s += simd_shuffle_xor(s, 4);
        a = s - 2.0f * simd_shuffle_xor(a, 6);
        s = a + simd_shuffle_xor(a, 8);
        s += simd_shuffle_xor(s, 16);
        v[j] = (s - 2.0f * simd_shuffle_xor(a, 24)) * 0.0625f;
    }
}
'''

SOURCE = r'''
#include <metal_stdlib>
#include <metal_tensor>
#include <MetalPerformancePrimitives/MetalPerformancePrimitives.h>
using namespace metal;
using namespace mpp::tensor_ops;
''' + ROTATE + r'''

// One threadgroup per row: the absmax reduction, then rounding to nearest
// even. Rows past the last real row are written as zeros for the matmul tile.
template <typename T>
[[kernel]] void quantize_rows_impl(device const T *input [[buffer(0)]],
                          device int8_t *output [[buffer(1)]],
                          device float *scales [[buffer(2)]],
                          constant uint &rows [[buffer(3)]],
                          constant uint &depth [[buffer(4)]],
                          constant uint &stride [[buffer(5)]],
                          uint row [[threadgroup_position_in_grid]],
                          uint tid [[thread_index_in_threadgroup]],
                          uint threads [[threads_per_threadgroup]],
                          uint lane [[thread_index_in_simdgroup]],
                          uint simd [[simdgroup_index_in_threadgroup]]) {
    threadgroup float partial[32];
    device int8_t *destination = output + (ulong)row * depth;
    if (row >= rows) {
        for (uint k = tid; k < depth; k += threads) destination[k] = 0;
        if (tid == 0) scales[row] = 0.0f;
        return;
    }
    device const T *source = input + (ulong)row * stride;
    float largest = 0.0f;
    for (uint k = tid; k < depth; k += threads) largest = max(largest, fabs((float)source[k]));
    largest = simd_max(largest);
    if (lane == 0) partial[simd] = largest;
    threadgroup_barrier(mem_flags::mem_threadgroup);
    if (simd == 0) {
        uint groups = (threads + 31) / 32;
        largest = lane < groups ? partial[lane] : 0.0f;
        largest = simd_max(largest);
        if (lane == 0) partial[0] = largest;
    }
    threadgroup_barrier(mem_flags::mem_threadgroup);
    float scale = max(partial[0], 1e-12f) / 127.0f;
    for (uint k = tid; k < depth; k += threads)
        destination[k] = (int8_t)clamp(rint(precise::divide((float)source[k], scale)), -127.0f, 127.0f);
    if (tid == 0) scales[row] = scale;
}

template <typename T>
[[kernel]] void int8_matmul_impl(device int8_t *input [[buffer(0)]],
                        device int8_t *weight [[buffer(1)]],
                        device const float *input_scales [[buffer(2)]],
                        device const float *weight_scales [[buffer(3)]],
                        device T *output [[buffer(4)]],
                        constant uint &rows [[buffer(5)]],
                        constant uint &depth [[buffer(6)]],
                        constant uint &columns [[buffer(7)]],
                        uint2 group [[threadgroup_position_in_grid]]) {
    constexpr uint TILE = 128;
    uint padded_rows = (rows + TILE - 1) & ~(TILE - 1);
    uint row_start = group.y * TILE;
    uint column_start = group.x * TILE;
    auto x = tensor<device int8_t, dextents<int32_t, 2>, tensor_inline>(
        input, dextents<int32_t, 2>((int)depth, (int)padded_rows));
    auto w = tensor<device int8_t, dextents<int32_t, 2>, tensor_inline>(
        weight, dextents<int32_t, 2>((int)depth, (int)columns));
    constexpr auto descriptor = matmul2d_descriptor(
        TILE, TILE, TILE, false, true, true, matmul2d_descriptor::mode::multiply_accumulate);
    matmul2d<descriptor, execution_simdgroups<8>> mm;
    auto first_a = x.slice<TILE, TILE>(0, (int)row_start);
    auto first_b = w.slice<TILE, TILE>(0, (int)column_start);
    auto accum = mm.template get_destination_cooperative_tensor<decltype(first_a), decltype(first_b), int32_t>();
    #pragma clang loop unroll(full)
    for (ushort e = 0; e < accum.get_capacity(); e++)
        if (accum.is_valid_element(e)) accum[e] = 0;
    for (uint k = 0; k < depth; k += TILE) {
        auto a = x.slice<TILE, TILE>((int)k, (int)row_start);
        auto b = w.slice<TILE, TILE>((int)k, (int)column_start);
        mm.run(a, b, accum);
    }
    #pragma clang loop unroll(full)
    for (ushort e = 0; e < accum.get_capacity(); e++) {
        if (!accum.is_valid_element(e)) continue;
        auto index = accum.get_multidimensional_index(e);
        uint row = row_start + (uint)index[1];
        uint column = column_start + (uint)index[0];
        if (row < rows)
            output[(ulong)row * columns + column] =
                (T)((float)accum[e] * input_scales[row] * weight_scales[column]);
    }
}

// ConvRot: a group-256 regular Hadamard rotation of each row, then per-row int8.
// H256 = H4 (x) H4 (x) H4 (x) H4 / 16 with H4 = [[1,1,1,-1],[1,1,-1,1],[1,-1,1,1],[-1,1,1,1]],
// symmetric and orthogonal, so x W^T = (x H)(W H)^T. Each simdgroup rotates whole groups:
// lane l holds channels 8l..8l+7, so digit 0 lives in registers, digit 1 mixes register
// bit 2 with lane bit 0, and digits 2 and 3 are lane bits 1-2 and 3-4 (xor shuffles).
// The rotated row stays in registers (at most MAXG2 groups per simdgroup) until the row's
// absmax is known; rounding is to nearest even like quantize_rows. At 18,144 rows this
// took 2.5-6.5 ms, against 3.0-8.1 ms for the unrotated quantizer.
template <typename T, typename T4>
[[kernel]] void quantize_rows_convrot2_impl(device const T *input [[buffer(0)]],
                                            device int8_t *output [[buffer(1)]],
                                            device float *scales [[buffer(2)]],
                                            constant uint &rows [[buffer(3)]],
                                            constant uint &depth [[buffer(4)]],
                                            constant uint &stride [[buffer(5)]],
                                            uint row [[threadgroup_position_in_grid]],
                                            uint tid [[thread_index_in_threadgroup]],
                                            uint lane [[thread_index_in_simdgroup]],
                                            uint simd [[simdgroup_index_in_threadgroup]]) {
    threadgroup float partial[8];
    device int8_t *destination = output + (ulong)row * depth;
    if (row >= rows) {
        for (uint k = tid; k < depth; k += 256) destination[k] = 0;
        if (tid == 0) scales[row] = 0.0f;
        return;
    }
    device const T *source = input + (ulong)row * stride;
    uint groups = depth / 256;
    float values[MAXG2][8];
    float largest = 0.0f;
    for (uint slot = 0; slot < MAXG2; slot++) {
        uint g = simd + slot * 8;
        if (g >= groups) break;
        thread float (&v)[8] = values[slot];
        device const T4 *pair = (device const T4 *)(source + g * 256 + lane * 8);
        float4 first = float4(pair[0]), second = float4(pair[1]);
        v[0] = first.x; v[1] = first.y; v[2] = first.z; v[3] = first.w;
        v[4] = second.x; v[5] = second.y; v[6] = second.z; v[7] = second.w;
        rotate_group2(v, lane);
        for (uint j = 0; j < 8; j++) largest = max(largest, fabs(v[j]));
    }
    largest = simd_max(largest);
    if (lane == 0) partial[simd] = largest;
    threadgroup_barrier(mem_flags::mem_threadgroup);
    largest = max(max(max(partial[0], partial[1]), max(partial[2], partial[3])),
                  max(max(partial[4], partial[5]), max(partial[6], partial[7])));
    float scale = max(largest, 1e-12f) / 127.0f;
    for (uint slot = 0; slot < MAXG2; slot++) {
        uint g = simd + slot * 8;
        if (g >= groups) break;
        char4 q0, q1;
        for (uint j = 0; j < 4; j++) {
            q0[j] = (char)clamp(rint(precise::divide(values[slot][j], scale)), -127.0f, 127.0f);
            q1[j] = (char)clamp(rint(precise::divide(values[slot][j + 4], scale)), -127.0f, 127.0f);
        }
        device char4 *out = (device char4 *)(destination + g * 256 + lane * 8);
        out[0] = q0; out[1] = q1;
    }
    if (tid == 0) scales[row] = scale;
}

#define INSTANTIATE(NAME, FUNCTION, T) \
    template [[host_name(NAME)]] [[kernel]] decltype(FUNCTION<T>) FUNCTION<T>;
template [[host_name("quantize_rows_convrot")]] [[kernel]]
decltype(quantize_rows_convrot2_impl<bfloat, bfloat4>) quantize_rows_convrot2_impl<bfloat, bfloat4>;
template [[host_name("quantize_rows_convrot_half")]] [[kernel]]
decltype(quantize_rows_convrot2_impl<half, half4>) quantize_rows_convrot2_impl<half, half4>;
INSTANTIATE("quantize_rows", quantize_rows_impl, bfloat)
INSTANTIATE("quantize_rows_half", quantize_rows_impl, half)
INSTANTIATE("int8_matmul", int8_matmul_impl, bfloat)
INSTANTIATE("int8_matmul_half", int8_matmul_impl, half)
'''


PORTABLE = r'''
#include <metal_stdlib>
using namespace metal;
''' + ROTATE + r'''
// One threadgroup (8 simdgroups) per row: int8 x row scale, then the ConvRot
// rotation, which is its own inverse, back to the original BF16 basis.
kernel void dequantize_convrot(device const char *weight [[buffer(0)]],
                               device const float *scales [[buffer(1)]],
                               device bfloat *output [[buffer(2)]],
                               constant uint &depth [[buffer(3)]],
                               uint row [[threadgroup_position_in_grid]],
                               uint lane [[thread_index_in_simdgroup]],
                               uint simd [[simdgroup_index_in_threadgroup]]) {
    float scale = scales[row];
    for (uint g = simd; g < depth / 256; g += 8) {
        float v[8];
        device const char *source = weight + (ulong)row * depth + g * 256 + lane * 8;
        for (uint j = 0; j < 8; j++) v[j] = (float)source[j] * scale;
        rotate_group2(v, lane);
        device bfloat *target = output + (ulong)row * depth + g * 256 + lane * 8;
        for (uint j = 0; j < 8; j++) target[j] = (bfloat)v[j];
    }
}
'''


@lru_cache(maxsize=1)
def _portable_library():
    import torch
    return torch.mps.compile_shader(PORTABLE)


def dequantize_convrot(weight, scales):
    """ConvRot int8 rows [N, K] and FP32 row scales -> BF16 [N, K] in the original basis (MPS)."""
    import torch
    if (weight.dtype != torch.int8 or weight.ndim != 2 or weight.device.type != 'mps'
            or weight.shape[1] % CONVROT_GROUP or not weight.is_contiguous()):
        raise ValueError('ConvRot dequantization expects contiguous int8 MPS rows of 256-channel groups')
    scales = scales.reshape(-1).float().contiguous()
    if scales.numel() != weight.shape[0] or scales.device != weight.device:
        raise ValueError('ConvRot dequantization needs one MPS scale per row')
    output = torch.empty(weight.shape, dtype=torch.bfloat16, device=weight.device)
    _portable_library().dequantize_convrot(weight, scales, output, weight.shape[1],
                                           threads=[weight.shape[0] * 256, 1, 1], group_size=[256, 1, 1])
    return output

@lru_cache(maxsize=1)
def _library():
    import torch
    return torch.mps.compile_shader(SOURCE)


@lru_cache(maxsize=1)
def available():
    """True on an Apple M5 or newer whose Metal compiler accepts the TensorOps kernels."""
    import platform
    import subprocess
    if platform.system() != 'Darwin':
        return False
    try:
        import os
        import re
        if os.environ.get('FREEVIDEO_MPS_INT8_COMPUTE') == '0':
            return False
        chip = subprocess.run(['sysctl', '-n', 'machdep.cpu.brand_string'], capture_output=True,
                              text=True, timeout=10).stdout
        generation = re.search(r'Apple M(\d+)', chip)
        if generation is None or int(generation.group(1)) < 5:
            return False
        _library()
        return True
    except Exception:
        return False


CONVROT_GROUP = 256
CONVROT_DEPTH = 14336  # MAXG2 groups per simdgroup in the rotation kernel


def convrot_rotate(matrix, group=CONVROT_GROUP):
    """Rows of a float matrix times the block-diagonal ConvRot Hadamard transform.

    The transform is symmetric and orthogonal, so this both rotates rows into
    the ConvRot basis and rotates dequantized ConvRot rows back.
    """
    import torch
    if group != CONVROT_GROUP or matrix.ndim != 2 or matrix.shape[1] % group:
        raise ValueError('ConvRot rows need 256-channel groups')
    h4 = torch.tensor([[1, 1, 1, -1], [1, 1, -1, 1], [1, -1, 1, 1], [-1, 1, 1, 1]], dtype=torch.float64)
    h = torch.kron(torch.kron(h4, h4), torch.kron(h4, h4)) / 16
    rows = matrix.double().reshape(matrix.shape[0], -1, group) @ h.to(matrix.device)
    return rows.reshape(matrix.shape).to(matrix.dtype)


def quantize_rows(value, convrot=False):
    """BF16 [rows, K] -> int8 [rows padded to 128, K] and FP32 row scales.

    With convrot, each 256-channel group of a row is first rotated by the
    regular Hadamard transform the ConvRot weights were rotated with.
    """
    import torch
    if (value.ndim != 2 or value.dtype not in (torch.bfloat16, torch.float16)
            or value.device.type != 'mps' or value.stride(1) != 1):
        raise ValueError('Row quantization expects a BF16 or FP16 MPS matrix with unit column stride')
    rows, depth = value.shape
    if depth % TILE:
        raise ValueError('Int8 projection depth must be a multiple of 128')
    if convrot and (depth % CONVROT_GROUP or depth > CONVROT_DEPTH):
        raise ValueError('ConvRot rows need a multiple of 256 channels, at most 14,336')
    if convrot and (value.stride(0) % 4 or value.data_ptr() % 8):
        value = value.contiguous()  # The rotation kernel reads four channels per load.
    padded = (rows + TILE - 1) // TILE * TILE
    quantized = torch.empty((padded, depth), dtype=torch.int8, device=value.device)
    scales = torch.empty(padded, dtype=torch.float32, device=value.device)
    library = _library()
    if convrot:
        kernel = library.quantize_rows_convrot if value.dtype == torch.bfloat16 else library.quantize_rows_convrot_half
    else:
        kernel = library.quantize_rows if value.dtype == torch.bfloat16 else library.quantize_rows_half
    kernel(value, quantized, scales, rows, depth, value.stride(0),
           threads=[padded * 256, 1, 1], group_size=[256, 1, 1])
    return quantized, scales, rows


def matmul(quantized, weight, weight_scales, dtype=None):
    """(int8 rows, scales, row count) x int8 [N, K] weight -> BF16 (or FP16) [rows, N]."""
    import torch
    dtype = torch.bfloat16 if dtype is None else dtype
    values, scales, rows = quantized
    columns, depth = weight.shape
    if (weight.dtype != torch.int8 or not weight.is_contiguous() or values.shape[1] != depth
            or columns % TILE or weight_scales.shape != (columns,) or weight_scales.dtype != torch.float32):
        raise ValueError('Int8 projection expects a contiguous int8 [N, K] weight and FP32 [N] scales')
    if dtype not in (torch.bfloat16, torch.float16):
        raise ValueError('Int8 products return BF16 or FP16')
    output = torch.empty((rows, columns), dtype=dtype, device=values.device)
    kernel = _library().int8_matmul if dtype == torch.bfloat16 else _library().int8_matmul_half
    kernel(values, weight, scales, weight_scales, output, rows, depth, columns,
           threads=[columns // TILE * 256, values.shape[0] // TILE, 1], group_size=[256, 1, 1])
    return output


def quantize_weight(weight):
    """Per-output-channel int8 weights and FP32 scales from a floating [N, K] matrix."""
    import torch
    values = weight.float()
    scales = values.abs().amax(dim=1).clamp_min(1e-12) / 127
    quantized = torch.round(values / scales[:, None]).clamp_(-127, 127).to(torch.int8)
    return quantized.contiguous(), scales.contiguous()


def eligible(module):
    """The large projections: Q/K/V, both attention outputs, FF up and down."""
    import torch
    return (isinstance(module, torch.nn.Linear) and module.in_features >= 5376
            and module.out_features >= 1024 and module.in_features % TILE == 0
            and module.out_features % TILE == 0)


def install(model, *, stats=None, storage=None, int8_linears=None):
    """Route eligible Linear forwards through int8 products.

    With storage='int8' the prepared model already holds per-row int8 weights:
    each eligible weight placeholder becomes int8 and gains a `weight_scale`
    buffer, so layer loading binds both and residency accounting sees one byte
    per weight. `int8_linears` maps those Linear names to their manifest
    entries; an entry with convrot_group 256 holds ConvRot-rotated rows, and its
    inputs are rotated the same way while they are quantized. Otherwise BF16
    weights are quantized on first use after each load, and the int8 copy lives
    as long as the bound BF16 weight. The most recent quantized input is reused,
    so Q/K/V share one quantization.
    """
    import types
    import weakref
    import torch
    if storage not in (None, 'int8'):
        raise ValueError('Unknown int8 weight storage')
    if storage == 'int8' and not isinstance(int8_linears, dict):
        raise ValueError('Int8 storage needs the manifest entries of the Linears stored as int8')
    for entry in (int8_linears or {}).values():
        if entry.get('convrot_group') not in (None, CONVROT_GROUP):
            raise ValueError('Unknown ConvRot group in the int8 manifest')
    # Engine kernel reports subtract counters between phases: numbers only.
    stats = {} if stats is None else stats
    stats.update(modules=0, convrot_modules=0, weight_quantizations=0, input_quantizations=0, int8_forwards=0)
    weights = {}
    last = {}

    def quantized_weight(weight):
        key = (weight.data_ptr(), weight._version, tuple(weight.shape))
        found = weights.get(key)
        if found is None:
            found = weights[key] = quantize_weight(weight)
            weakref.finalize(weight, weights.pop, key, None)
            stats['weight_quantizations'] += 1
        return found

    def quantized_input(rows, convrot):
        # The entry keeps its source alive: a freed input's address could
        # otherwise be reused by the next block with the same shape.
        key = (rows.data_ptr(), rows._version, tuple(rows.shape), tuple(rows.stride()), convrot)
        if last.get('key') != key or last.get('source') is None:
            last.clear()
            last.update(key=key, source=rows, value=quantize_rows(rows, convrot=convrot))
            stats['input_quantizations'] += 1
        return last['value']

    def forward(module, value):
        weight = module.weight
        if weight.dtype == torch.int8:
            if value.dtype != torch.bfloat16 or value.device.type != 'mps' or weight.is_meta:
                raise RuntimeError('Int8 weights need a loaded layer and BF16 MPS inputs')
            pair = (weight, module.weight_scale)
        elif value.dtype != torch.bfloat16 or value.device.type != 'mps' or weight.is_meta:
            return torch.nn.functional.linear(value, weight, module.bias)
        else:
            pair = quantized_weight(weight)
        rows = value.reshape(-1, value.shape[-1])
        if rows.stride(1) != 1:
            rows = rows.contiguous()
        output = matmul(quantized_input(rows, getattr(module, '_freevideo_convrot', False)), *pair)
        if module.bias is not None:
            output += module.bias
        stats['int8_forwards'] += 1
        return output.reshape(*value.shape[:-1], module.out_features)

    for name, module in model.named_modules():
        # Only matrices the prepared model stores as int8 get int8 placeholders;
        # every other Linear keeps its BF16 storage and ordinary forward.
        if eligible(module) and (storage != 'int8' or name in int8_linears):
            if storage == 'int8':
                module.weight = torch.nn.Parameter(torch.empty(module.weight.shape, dtype=torch.int8,
                                                               device='meta'), requires_grad=False)
                module.register_buffer('weight_scale', torch.empty(module.out_features, dtype=torch.float32,
                                                                    device='meta'))
                if int8_linears[name].get('convrot_group') == CONVROT_GROUP:
                    if module.in_features % CONVROT_GROUP or module.in_features > CONVROT_DEPTH:
                        raise ValueError('ConvRot Linear width is outside the rotation kernel: ' + name)
                    module._freevideo_convrot = True
                    stats['convrot_modules'] += 1
            module.forward = types.MethodType(forward, module)
            stats['modules'] += 1
    return dict(implementation='mps-int8-tensorops-v1', storage=storage or 'quantize-on-load',
                modules=stats['modules'])

