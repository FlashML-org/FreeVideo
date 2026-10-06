"""ConvRot int8 projections for CUDA (W8A8, int32 accumulation).

Weights are the prepared int8 rows of a ConvRot cache: each row was rotated by
the group-256 regular Hadamard transform H256 = H16 (x) H16 before per-row
quantization, the convention of ComfyUI's int8 H3 checkpoints. Activations get
the same rotation and one scale per row here; the int8 product is exact in
int32 and the epilogue applies both scales. Because H256 is symmetric and
orthogonal, x W^T = (x H)(W H)^T.
"""
import torch
import triton
import triton.language as tl

from .triton_compat import activate
activate()

GROUP = 256
_H16 = {}


def hadamard16(device):
    """H4 (x) H4 / 4 with H4 = [[1,1,1,-1],[1,1,-1,1],[1,-1,1,1],[-1,1,1,1]] (FP32)."""
    key = str(device)
    if key not in _H16:
        h4 = torch.tensor([[1, 1, 1, -1], [1, 1, -1, 1], [1, -1, 1, 1], [-1, 1, 1, 1]], dtype=torch.float32)
        _H16[key] = (torch.kron(h4, h4) / 4).to(device).contiguous()
    return _H16[key]


@triton.jit
def _rotate(x, h16, BM: tl.constexpr):
    # Channel c = 16 a + b of a 256-wide group: y = H16 X H16 over (a, b).
    z = tl.dot(tl.reshape(x, (BM * 16, 16)), h16, input_precision='ieee')
    z = tl.permute(tl.reshape(z, (BM, 16, 16)), (0, 2, 1))
    z = tl.dot(tl.reshape(z, (BM * 16, 16)), h16, input_precision='ieee')
    return tl.reshape(tl.permute(tl.reshape(z, (BM, 16, 16)), (0, 2, 1)), (BM, 256))


@triton.jit
def _rotate_quantize(X, Q, S, H, M, K: tl.constexpr, SX, BM: tl.constexpr):
    rows = tl.program_id(0).to(tl.int64) * BM + tl.arange(0, BM)
    live = rows < M
    lane = tl.arange(0, 256)
    pair = tl.arange(0, 16)
    h16 = tl.load(H + pair[:, None] * 16 + pair[None, :])
    largest = tl.zeros((BM,), tl.float32)
    for group in range(K // 256):
        x = tl.load(X + rows[:, None] * SX + group * 256 + lane[None, :], live[:, None], other=0.).to(tl.float32)
        largest = tl.maximum(largest, tl.max(tl.abs(_rotate(x, h16, BM)), 1))
    scale = tl.maximum(largest, 1e-12) / 127.
    tl.store(S + rows, scale, live)
    for group in range(K // 256):
        x = tl.load(X + rows[:, None] * SX + group * 256 + lane[None, :], live[:, None], other=0.).to(tl.float32)
        value = tl.extra.cuda.libdevice.rint(tl.fdiv(_rotate(x, h16, BM), scale[:, None], ieee_rounding=True))
        value = tl.minimum(tl.maximum(value, -127.), 127.)
        tl.store(Q + rows[:, None] * K + group * 256 + lane[None, :], value.to(tl.int8), live[:, None])


def rotate_quantize(value):
    """[..., K] BF16/FP16 -> (int8 [M, K], FP32 [M] row scales) after the ConvRot rotation."""
    rows = value.reshape(-1, value.shape[-1])
    if rows.stride(-1) != 1:
        rows = rows.contiguous()
    m, k = rows.shape
    if k % GROUP:
        raise ValueError('ConvRot activations need a multiple of 256 channels')
    quantized = torch.empty((m, k), device=rows.device, dtype=torch.int8)
    scales = torch.empty(m, device=rows.device, dtype=torch.float32)
    block = 16
    _rotate_quantize[(triton.cdiv(m, block),)](rows, quantized, scales, hadamard16(rows.device), m, k, rows.stride(0),
                                               BM=block, num_warps=4)
    return quantized, scales


@triton.autotune(configs=[
    triton.Config({'BM': 128, 'BN': 128, 'BK': 128, 'GROUP_M': 8}, num_warps=8, num_stages=3),
    triton.Config({'BM': 128, 'BN': 256, 'BK': 128, 'GROUP_M': 8}, num_warps=8, num_stages=3),
    triton.Config({'BM': 256, 'BN': 128, 'BK': 128, 'GROUP_M': 8}, num_warps=8, num_stages=3),
    triton.Config({'BM': 128, 'BN': 128, 'BK': 64, 'GROUP_M': 8}, num_warps=4, num_stages=4),
    triton.Config({'BM': 64, 'BN': 128, 'BK': 128, 'GROUP_M': 8}, num_warps=4, num_stages=4),
], key=['ROWS', 'N', 'K'], cache_results=True)  # tuned once per machine, kept in the Triton cache
@triton.jit
def _matmul(A, W, SA, SW, B, O, M, N, K: tl.constexpr, SAM, SWN, SOM, ROWS,
            HAS_BIAS: tl.constexpr, BM: tl.constexpr, BN: tl.constexpr, BK: tl.constexpr, GROUP_M: tl.constexpr):
    pid = tl.program_id(0)
    blocks_m, blocks_n = tl.cdiv(M, BM), tl.cdiv(N, BN)
    per_group = GROUP_M * blocks_n
    first_m = (pid // per_group) * GROUP_M
    size_m = tl.minimum(blocks_m - first_m, GROUP_M)
    pid_m = first_m + (pid % per_group) % size_m
    pid_n = (pid % per_group) // size_m
    rows = pid_m.to(tl.int64) * BM + tl.arange(0, BM)
    cols = pid_n.to(tl.int64) * BN + tl.arange(0, BN)
    inner = tl.arange(0, BK)
    a_ptrs = A + rows[:, None] * SAM + inner[None, :]
    w_ptrs = W + cols[None, :] * SWN + inner[:, None]
    acc = tl.zeros((BM, BN), dtype=tl.int32)
    for _ in range(K // BK):
        a = tl.load(a_ptrs, rows[:, None] < M, other=0)
        w = tl.load(w_ptrs, cols[None, :] < N, other=0)
        acc = tl.dot(a, w, acc, out_dtype=tl.int32)
        a_ptrs += BK
        w_ptrs += BK
    sa = tl.load(SA + rows, rows < M, other=0.)
    sw = tl.load(SW + cols, cols < N, other=0.)
    out = acc.to(tl.float32) * sa[:, None] * sw[None, :]
    if HAS_BIAS:
        out += tl.load(B + cols, cols < N, other=0.).to(tl.float32)[None, :]
    tl.store(O + rows[:, None] * SOM + cols[None, :], out.to(O.dtype.element_ty),
             (rows[:, None] < M) & (cols[None, :] < N))


def matmul(quantized, weight, weight_scale, bias=None, out_dtype=torch.bfloat16):
    """(int8 [M, K], FP32 [M]) x int8 [N, K] rows with FP32 [N] scales -> out_dtype [M, N]."""
    values, scales = quantized
    m, k = values.shape
    n, weight_k = weight.shape
    if k != weight_k or k % 128:
        raise ValueError('Int8 projection widths differ or are not multiples of 128')
    if weight.stride(1) != 1 or values.stride(1) != 1:
        raise ValueError('Int8 operands need unit column stride')
    weight_scale = weight_scale.reshape(-1)
    output = torch.empty((m, n), device=values.device, dtype=out_dtype)
    # Tune once per power-of-two row band, not for every chunk and remainder row count.
    rows = max(256, 1 << (m - 1).bit_length())
    _matmul[lambda meta: (triton.cdiv(m, meta['BM']) * triton.cdiv(n, meta['BN']),)](
        values, weight, scales, weight_scale, bias if bias is not None else weight_scale, output,
        m, n, k, values.stride(0), weight.stride(0), output.stride(0), rows, bias is not None)
    return output


class Int8Linear(torch.nn.Module):
    """A prepared ConvRot int8 Linear: per-row int8 activations times int8 rows.

    The most recent quantized input is shared by every Int8Linear, so Q/K/V and
    their head-chunk slices quantize the same activation once. The entry keeps
    its source alive: a freed input's address could otherwise be reused by a
    later activation of the same shape.
    """
    _last = {}

    @property
    def bias(self):
        return self.original.bias

    def quantized(self, value):
        rows = value.reshape(-1, value.shape[-1])
        key = (rows.data_ptr(), rows._version, tuple(rows.shape), tuple(rows.stride()), str(rows.device))
        last = Int8Linear._last
        if last.get('key') != key:
            last.clear()
            last.update(key=key, source=rows, value=rotate_quantize(rows))
        return last['value']

    def project(self, value, channels):
        scales = self.weight_scale.reshape(-1)[channels]
        output = matmul(self.quantized(value), self.weight_int8[channels], scales,
                        None if self.bias is None else self.bias[channels], out_dtype=value.dtype)
        return output.reshape(*value.shape[:-1], output.shape[-1])

    def forward(self, value):
        output = matmul(self.quantized(value), self.weight_int8, self.weight_scale.reshape(-1), self.bias,
                        out_dtype=value.dtype)
        return output.reshape(*value.shape[:-1], self.out_features)


def install_cached_linears(model, linears, rotation):
    """Replace prepared int8 Linears with meta int8 placeholders for layer loading."""
    if rotation is None or rotation.get('kind') != 'convrot' or rotation.get('group') != GROUP:
        raise ValueError('CUDA int8 projections need ConvRot group-256 weights')
    for name, spec in linears.items():
        if spec.get('storage') != 'int8' or spec.get('convrot_group') != GROUP:
            raise ValueError('Int8 cache entry is not a ConvRot int8 matrix: ' + name)
        original = model.get_submodule(name)
        if not isinstance(original, torch.nn.Linear):
            raise ValueError('Int8 cache target is not a Linear: ' + name)
        if list(original.weight.shape) != spec['weight_shape'] or original.in_features % GROUP:
            raise ValueError('Int8 cache shape differs from the official model: ' + name)
        replacement = Int8Linear.__new__(Int8Linear)
        torch.nn.Module.__init__(replacement)
        replacement.register_buffer('weight_int8', torch.empty(spec['weight_shape'], dtype=torch.int8, device='meta'))
        replacement.register_buffer('weight_scale', torch.empty(spec['scale_shape'], dtype=torch.float32, device='meta'))
        replacement.original = torch.nn.Module()
        replacement.original.register_parameter('bias', original.bias)
        replacement.in_features = original.in_features
        replacement.out_features = original.out_features
        replacement.input_dtype = getattr(torch, spec.get('input_dtype', 'bfloat16'))
        prefix, _, child = name.rpartition('.')
        setattr(model.get_submodule(prefix) if prefix else model, child, replacement)
