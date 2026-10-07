# Prism (MIT, Tencent): vendored from the Prism single-GPU research branch
# (Prism-fast 0befcb7, hymm/fast/qlinear.py) for FreeVideo; see NOTICE.
"""Quantized drop-in Linear layers (INT8 / FP8) for Prism on consumer GPUs.

Modes (bias stays in its original dtype, output is the activation dtype, bf16):

  w8a8_int8   per-output-channel symmetric INT8 weights, per-token dynamic INT8
              activations (symmetric, or asymmetric with a per-token offset),
              Triton INT8 tensor-core GEMM with a fused dequant epilogue
              ``out = acc_i32 * s_x[m] * s_w[n] (+ o_x[m] * s_w[n] * wsum[n]) + bias``.
              Optional online block-Hadamard rotation (64/128/256 along K, random
              signs) and SmoothQuant vector, both fused into the activation-quant
              kernel. RTX 30/40/50 (SM86/89/120) and SM90 run the same kernel.
  w8a8_fp8    per-channel E4M3 weights, per-token E4M3 activations,
              ``torch._scaled_mm`` with rowwise scales when the build supports it
              (probed once per device), otherwise a Triton FP8 GEMM. SM89+.
  w8a16_int8  weight-only INT8 (memory path). Small M: Triton GEMM that decodes
  w8a16_fp8   the weight tile in registers (FP8 by bit manipulation below SM89,
              so RTX 30 works). Large M: dequantize into a transient BF16 buffer
              and use cuBLAS (bf16 tensor-core rate is the same either way).

Every mode has a pure-torch ``ref`` backend (exact emulation of the quantized
arithmetic) used on CPU, pre-SM80 GPUs, and as the oracle in tests.

Math of the rotation: with D = diag(random signs), S = diag(smoothing), H the
orthonormal block-diagonal Sylvester Hadamard (H = H^T, H H^T = I):
    x W^T = (x S^-1 D H) (W S D H)^T
The weight side is applied offline; the activation side ``x * act_mult`` (act_mult
= d / s) followed by the unnormalized +-1 Hadamard is fused into the quant kernel,
and 1/sqrt(B) is folded into the per-token scale.
"""
from __future__ import annotations

import functools
import json
import math
import os
import warnings
from typing import Callable, Dict, Optional, Tuple, Union

import torch
import torch.nn as nn
import torch.nn.functional as F

try:  # Triton is optional: the ``ref`` backend covers machines without it.
    import triton
    import triton.language as tl
    from triton.language.extra import libdevice
    _HAS_TRITON = True
except Exception:  # pragma: no cover
    triton = None
    _HAS_TRITON = False

MODES = ('w8a8_int8', 'w8a8_fp8', 'w8a16_int8', 'w8a16_fp8')
FP8_MAX = 448.0
INT8_MAX = 127.0
# Above this many tokens the w8a16 path dequantizes to a transient bf16 weight
# and uses cuBLAS: the GEMM is compute bound there and bf16 MMA is the same
# instruction either way. Override with PRISM_QLINEAR_W8A16_DEQUANT_M.
W8A16_DEQUANT_M = int(os.environ.get('PRISM_QLINEAR_W8A16_DEQUANT_M', '1024'))
_INT_MM_CHUNK_BYTES = 256 * 1024 * 1024

# ----------------------------------------------------------------------------
# Device capability
# ----------------------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def _device_props(index: int):
    p = torch.cuda.get_device_properties(index)
    smem = None
    if _HAS_TRITON:
        try:
            smem = triton.runtime.driver.active.utils.get_device_properties(index)['max_shared_mem']
        except Exception:
            smem = None
    if smem is None:
        cc = (p.major, p.minor)
        smem = 232448 if cc in ((9, 0), (10, 0)) else (166912 if cc == (8, 0) else 101376)
    return dict(name=p.name, cc=(p.major, p.minor), smem=int(smem), sms=p.multi_processor_count)


def _cc(device) -> Tuple[int, int]:
    device = torch.device(device)
    if device.type != 'cuda':
        return (0, 0)
    return _device_props(device.index if device.index is not None else torch.cuda.current_device())['cc']


def _triton_ok(device) -> bool:
    return _HAS_TRITON and torch.device(device).type == 'cuda' and _cc(device) >= (8, 0)


def _fp8_tc(device) -> bool:
    return torch.device(device).type == 'cuda' and _cc(device) >= (8, 9)


@functools.lru_cache(maxsize=None)
def _scaled_mm_rowwise_ok(index: int) -> bool:
    """Probe torch._scaled_mm with rowwise scales, bias and an odd M once per device."""
    dev = torch.device('cuda', index)
    if _cc(dev) < (8, 9):
        return False
    try:
        g = torch.Generator(device=dev).manual_seed(0)
        a = torch.randn(17, 64, device=dev, generator=g)
        b = torch.randn(48, 64, device=dev, generator=g)
        a8, b8 = a.to(torch.float8_e4m3fn), b.to(torch.float8_e4m3fn)
        sa = torch.rand(17, 1, device=dev, generator=g) + 0.5
        sb = torch.rand(1, 48, device=dev, generator=g) + 0.5
        bias = torch.randn(48, device=dev, generator=g).to(torch.bfloat16)
        out = torch._scaled_mm(a8, b8.t(), scale_a=sa, scale_b=sb, bias=bias,
                               out_dtype=torch.bfloat16, use_fast_accum=True)
        ref = (a8.float() * sa) @ (b8.float() * sb.t()).t() + bias.float()
        err = (out.float() - ref).norm() / ref.norm()
        return bool(err < 2e-2)
    except Exception:
        return False


def available_modes(device=None) -> Dict[str, Optional[str]]:
    """Map each mode to the backend ``auto`` resolves to on ``device``.

    'triton' / 'scaled_mm' / 'triton+cublas' are native; 'ref' is a correct but
    slow torch emulation (no tensor-core path on this device); None = unsupported.
    """
    if device is None:
        device = torch.device('cuda', torch.cuda.current_device()) if torch.cuda.is_available() else torch.device('cpu')
    device = torch.device(device)
    out = {}
    for mode in MODES:
        out[mode] = _resolve_backend(mode, device, 'auto')
    if device.type == 'cuda':
        out['_device'] = '%s sm%d%d' % ((_device_props(device.index or 0)['name'],) + _cc(device))
    return out


def _resolve_backend(mode: str, device, backend: str) -> str:
    device = torch.device(device)
    if backend not in ('auto', None):
        return backend
    if mode == 'w8a8_int8':
        return 'triton' if _triton_ok(device) else 'ref'
    if mode == 'w8a8_fp8':
        if not _fp8_tc(device):
            return 'ref'
        if _scaled_mm_rowwise_ok(device.index if device.index is not None else torch.cuda.current_device()):
            return 'scaled_mm'
        return 'triton' if _triton_ok(device) else 'ref'
    if mode in ('w8a16_int8', 'w8a16_fp8'):
        return 'triton' if _triton_ok(device) else 'ref'
    raise ValueError('unknown mode %r' % mode)


# ----------------------------------------------------------------------------
# GEMM tile configs: arch heuristics, optional tuning with a JSON cache
# ----------------------------------------------------------------------------
# (BM, BN, BK, GROUP_M, num_warps, num_stages)
_W8A8_CANDIDATES = [
    # large M (consumer smem fits up to 96 KB of pipeline buffers = (stages-1) * tile bytes)
    (128, 256, 128, 8, 8, 3), (256, 128, 128, 8, 8, 3), (128, 256, 128, 8, 8, 4),
    (128, 256, 64, 8, 8, 3), (128, 256, 64, 8, 8, 4), (128, 256, 64, 8, 8, 5),
    (256, 128, 64, 8, 8, 4), (128, 128, 128, 8, 8, 4), (128, 128, 128, 8, 4, 4),
    (128, 128, 64, 8, 4, 5), (64, 256, 128, 8, 8, 3),
    # small / medium M
    (64, 128, 128, 8, 4, 4), (64, 64, 128, 8, 4, 4), (32, 64, 256, 8, 4, 3),
    (16, 64, 256, 8, 4, 3), (16, 128, 128, 8, 4, 4), (32, 128, 128, 8, 4, 4),
]
_W8A16_CANDIDATES = [
    (128, 256, 64, 8, 8, 3), (128, 256, 64, 8, 8, 4), (128, 128, 64, 8, 8, 4),
    (128, 128, 64, 8, 4, 4), (256, 128, 64, 8, 8, 3), (64, 128, 64, 8, 4, 4),
    (64, 128, 128, 8, 4, 3), (64, 64, 128, 8, 4, 4), (32, 64, 128, 8, 4, 4),
    (32, 128, 128, 8, 4, 3), (16, 64, 128, 8, 4, 4), (16, 128, 128, 8, 4, 3),
]


def _smem_bytes(cfg, a_bytes, b_bytes, cc=(8, 9)):
    # Triton's mma.sync pipeline keeps (stages - 1) cp.async buffers; the Hopper
    # wgmma pipeline keeps `stages` (measured from AOT cubins, see compile check).
    bm, bn, bk, _, _, st = cfg
    bufs = st if _arch_class(cc) == 'dc' else max(1, st - 1)
    return bufs * (bm * bk * a_bytes + bk * bn * b_bytes)


def _arch_class(cc):
    if cc[0] in (9, 10):
        return 'dc'  # Hopper / datacenter Blackwell: large smem, wgmma/tcgen05
    return 'consumer'  # SM80/86/89/120: ~99 KB smem per block, mma.sync


def _default_config(kind: str, m: int, cc) -> tuple:
    big = _arch_class(cc) == 'dc'
    if kind == 'w8a8':
        if m >= 1024:
            return (128, 256, 128, 8, 8, 3) if big else (128, 256, 64, 8, 8, 4)
        if m > 64:
            return (64, 128, 128, 8, 4, 4)
        return (16 if m <= 16 else 32 if m <= 32 else 64, 64, 256, 8, 4, 3)
    if kind == 'w8a16':
        if m >= 1024:
            return (128, 256, 64, 8, 8, 3) if big else (128, 128, 64, 8, 8, 4)
        if m > 64:
            return (64, 128, 64, 8, 4, 4)
        return (16 if m <= 16 else 32 if m <= 32 else 64, 64, 128, 8, 4, 4)
    raise ValueError(kind)


def _m_bucket(m: int) -> int:
    return min(1 << max(4, (m - 1).bit_length()), 8192)


_TUNE_ENABLED = os.environ.get('PRISM_QLINEAR_TUNE', '0') == '1'
_TUNE_PATH = os.environ.get('PRISM_QLINEAR_TUNE_CACHE',
                            os.path.join(os.path.expanduser('~'), '.cache', 'prism_fast', 'qlinear_tune.json'))
_CFG_CACHE: Dict[str, tuple] = {}
_DISK_CACHE: Optional[dict] = None


def set_tuning(enabled: bool, path: Optional[str] = None):
    """Enable on-line tuning (benchmarks every candidate tile the first time a
    (kernel, M-bucket, N, K) is seen on this GPU) and persist results to JSON."""
    global _TUNE_ENABLED, _TUNE_PATH, _DISK_CACHE
    _TUNE_ENABLED = bool(enabled)
    if path:
        _TUNE_PATH, _DISK_CACHE = path, None


def _disk_cache() -> dict:
    global _DISK_CACHE
    if _DISK_CACHE is None:
        try:
            with open(_TUNE_PATH) as f:
                _DISK_CACHE = json.load(f)
        except Exception:
            _DISK_CACHE = {}
    return _DISK_CACHE


def _save_disk_cache():
    try:
        os.makedirs(os.path.dirname(_TUNE_PATH), exist_ok=True)
        tmp = _TUNE_PATH + '.tmp%d' % os.getpid()
        with open(tmp, 'w') as f:
            json.dump(_disk_cache(), f, indent=1, sort_keys=True)
        os.replace(tmp, _TUNE_PATH)
    except Exception as e:  # pragma: no cover
        warnings.warn('qlinear: cannot write tune cache %s: %s' % (_TUNE_PATH, e))


def _pick_config(kind: str, tag: str, m: int, n: int, k: int, device, a_bytes: int, b_bytes: int,
                 run: Callable[[tuple], None]) -> tuple:
    props = _device_props(device.index)
    key = '%s|%s|%s|sm%d%d|t%s|M%d|N%d|K%d' % (kind, tag, props['name'], props['cc'][0], props['cc'][1],
                                                triton.__version__, _m_bucket(m), n, k)
    cfg = _CFG_CACHE.get(key)
    if cfg is not None:
        return cfg
    disk = _disk_cache().get(key)
    if disk is not None:
        cfg = tuple(disk['cfg'])
    elif _TUNE_ENABLED:
        cfg = _tune(kind, m, props, a_bytes, b_bytes, run)
        _disk_cache()[key] = dict(cfg=list(cfg))
        _save_disk_cache()
    else:
        cfg = _default_config(kind, m, props['cc'])
        if _smem_bytes(cfg, a_bytes, b_bytes, props['cc']) > props['smem']:
            bm, bn, bk, g, w, st = cfg
            cfg = (bm, bn, bk, g, w, max(2, st - 1))
    _CFG_CACHE[key] = cfg
    return cfg


def _time_ms(fn, reps=3):
    fn()
    torch.cuda.synchronize()
    ts = []
    for _ in range(reps):
        a, b = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        a.record()
        fn()
        b.record()
        b.synchronize()
        ts.append(a.elapsed_time(b))
    return sorted(ts)[len(ts) // 2]


def _tune(kind, m, props, a_bytes, b_bytes, run):
    cands = _W8A8_CANDIDATES if kind == 'w8a8' else _W8A16_CANDIDATES
    mb = _m_bucket(m)
    cands = [c for c in cands if _smem_bytes(c, a_bytes, b_bytes, props['cc']) <= props['smem'] and
             (c[0] <= max(16, mb) and (mb < 1024 or c[0] >= 64))]
    best, best_t = None, float('inf')
    for c in cands:
        try:
            t = _time_ms(lambda: run(c))  # first call compiles
        except Exception:  # OutOfResources etc.: skip the candidate
            continue
        if t < best_t:
            best, best_t = c, t
    return best or _default_config(kind, m, props['cc'])


# ----------------------------------------------------------------------------
# Triton kernels
# ----------------------------------------------------------------------------
if _HAS_TRITON:

    @triton.jit
    def _gelu_tanh(x):
        # MUFU tanh.approx.f32 (SM75+, max rel err ~2^-11, below bf16 output rounding):
        # libdevice.tanh made the fused-GELU epilogue slower than a separate pass.
        inner = 0.7978845608028654 * (x + 0.044715 * x * x * x)
        t = tl.inline_asm_elementwise("tanh.approx.f32 $0, $1;", "=f,f", [inner], dtype=tl.float32,
                                      is_pure=True, pack=1)
        return 0.5 * x * (1.0 + t)

    @triton.jit
    def _tile_ids(M, N, BM: tl.constexpr, BN: tl.constexpr, GROUP_M: tl.constexpr):
        # Grouped ordering: GROUP_M row-tiles sweep all column tiles together so
        # their activation rows stay in L2 while the weight streams.
        pid = tl.program_id(0)
        num_m = tl.cdiv(M, BM)
        num_n = tl.cdiv(N, BN)
        width = GROUP_M * num_n
        group = pid // width
        first_m = group * GROUP_M
        gsize = tl.minimum(num_m - first_m, GROUP_M)
        pid_m = first_m + (pid % width) % gsize
        pid_n = (pid % width) // gsize
        return pid_m, pid_n

    @triton.jit
    def _w8a8_gemm_kernel(A, B, C, SA, OA, SB, WSUM, BIAS, RES, GATE, M, N, K,
                          stride_am, stride_bn, stride_cm, stride_rm, stride_gm, gate_div,
                          IS_INT: tl.constexpr, HAS_OFFSET: tl.constexpr, HAS_BIAS: tl.constexpr,
                          ACT: tl.constexpr, HAS_RES: tl.constexpr, HAS_GATE: tl.constexpr,
                          GATE_VEC: tl.constexpr, EVEN_K: tl.constexpr,
                          BM: tl.constexpr, BN: tl.constexpr, BK: tl.constexpr, GROUP_M: tl.constexpr):
        pid_m, pid_n = _tile_ids(M, N, BM, BN, GROUP_M)
        row0 = pid_m * BM
        col0 = pid_n * BN
        rm = tl.arange(0, BM)
        rn = tl.arange(0, BN)
        rk = tl.arange(0, BK)
        # Clamp tail rows/cols onto valid memory so the main loop needs no masks;
        # 64-bit tile base (M*K and M*N exceed 2^31 at 720p), 32-bit in-tile offsets.
        am = tl.minimum(row0 + rm, M - 1) - row0
        bn = tl.minimum(col0 + rn, N - 1) - col0
        a_ptrs = A + row0.to(tl.int64) * stride_am + (am[:, None] * stride_am + rk[None, :])
        b_ptrs = B + col0.to(tl.int64) * stride_bn + (bn[None, :] * stride_bn + rk[:, None])
        if IS_INT:
            acc = tl.zeros((BM, BN), dtype=tl.int32)
        else:
            acc = tl.zeros((BM, BN), dtype=tl.float32)
        for k in range(0, tl.cdiv(K, BK)):
            if EVEN_K:
                a = tl.load(a_ptrs)
                b = tl.load(b_ptrs)
            else:
                kk = k * BK + rk
                if IS_INT:
                    a = tl.load(a_ptrs, mask=kk[None, :] < K, other=0)
                    b = tl.load(b_ptrs, mask=kk[:, None] < K, other=0)
                else:
                    a = tl.load(a_ptrs, mask=kk[None, :] < K, other=0.0)
                    b = tl.load(b_ptrs, mask=kk[:, None] < K, other=0.0)
            if IS_INT:  # Triton >= 3.4 asserts the int32 accumulator dtype (no-op on 3.3)
                acc = tl.dot(a, b, acc, out_dtype=tl.int32)
            else:
                acc = tl.dot(a, b, acc)
            a_ptrs += BK
            b_ptrs += BK
        offs_m = row0 + rm
        offs_n = col0 + rn
        mmask = offs_m < M
        nmask = offs_n < N
        sa = tl.load(SA + offs_m, mask=mmask, other=0.)
        sb = tl.load(SB + offs_n, mask=nmask, other=0.)
        out = acc.to(tl.float32) * sa[:, None]
        if HAS_OFFSET:
            oa = tl.load(OA + offs_m, mask=mmask, other=0.)
            ws = tl.load(WSUM + offs_n, mask=nmask, other=0.)
            out = out + oa[:, None] * ws[None, :]
        out = out * sb[None, :]
        if HAS_BIAS:
            out = out + tl.load(BIAS + offs_n, mask=nmask, other=0.).to(tl.float32)[None, :]
        if ACT == 1:
            out = _gelu_tanh(out)
        omask = mmask[:, None] & nmask[None, :]
        if HAS_RES:
            # out = residual + gate * y (Wan's GateModule); may run in place (C == RES).
            if HAS_GATE:
                if GATE_VEC:  # one gate row for the whole tensor (per-sample gate, B == 1)
                    g = tl.load(GATE + offs_n, mask=nmask, other=0.)
                    out = out * g.to(tl.float32)[None, :]
                else:
                    grow = (offs_m // gate_div).to(tl.int64)
                    g = tl.load(GATE + grow[:, None] * stride_gm + offs_n[None, :], mask=omask, other=0.)
                    out = out * g.to(tl.float32)
            r_ptrs = RES + row0.to(tl.int64) * stride_rm + (rm[:, None] * stride_rm + offs_n[None, :])
            out = out + tl.load(r_ptrs, mask=omask, other=0., eviction_policy='evict_first').to(tl.float32)
        if ACT == 2:  # 'gelu_tanh_post': GELU of the residual sum (FreeVideo: INT8 LoRA up into ffn.0)
            out = _gelu_tanh(out)
        c_ptrs = C + row0.to(tl.int64) * stride_cm + (rm[:, None] * stride_cm + offs_n[None, :])
        tl.store(c_ptrs, out.to(C.dtype.element_ty), mask=omask)

    @triton.jit
    def _e4m3_bits_to_f32(bits):
        # Exact E4M3FN decode with integer ops only (no FP8 hardware needed: SM80/86).
        b = bits.to(tl.int32) & 255
        exponent = (b >> 3) & 15
        mantissa = b & 7
        normal = ((exponent + 120) << 23) | (mantissa << 20)
        value = tl.where(exponent == 0, mantissa.to(tl.float32) * 0.001953125,
                         normal.to(tl.float32, bitcast=True))
        return tl.where((b & 128) != 0, -value, value)

    @triton.jit
    def _w8a16_gemm_kernel(A, B, C, SB, BIAS, M, N, K, stride_am, stride_bn, stride_cm,
                           W_FMT: tl.constexpr, HAS_BIAS: tl.constexpr, ACT: tl.constexpr,
                           EVEN_K: tl.constexpr, BM: tl.constexpr, BN: tl.constexpr,
                           BK: tl.constexpr, GROUP_M: tl.constexpr):
        # W_FMT: 0 int8, 1 fp8 bits via native cvt (SM89+), 2 fp8 bits via integer decode.
        pid_m, pid_n = _tile_ids(M, N, BM, BN, GROUP_M)
        row0 = pid_m * BM
        col0 = pid_n * BN
        rm = tl.arange(0, BM)
        rn = tl.arange(0, BN)
        rk = tl.arange(0, BK)
        am = tl.minimum(row0 + rm, M - 1) - row0
        bn = tl.minimum(col0 + rn, N - 1) - col0
        a_ptrs = A + row0.to(tl.int64) * stride_am + (am[:, None] * stride_am + rk[None, :])
        b_ptrs = B + col0.to(tl.int64) * stride_bn + (bn[None, :] * stride_bn + rk[:, None])
        acc = tl.zeros((BM, BN), dtype=tl.float32)
        for k in range(0, tl.cdiv(K, BK)):
            if EVEN_K:
                a = tl.load(a_ptrs)
                b = tl.load(b_ptrs)
            else:
                kk = k * BK + rk
                a = tl.load(a_ptrs, mask=kk[None, :] < K, other=0.)
                b = tl.load(b_ptrs, mask=kk[:, None] < K, other=0)
            if W_FMT == 0:
                w = b.to(a.dtype)
            elif W_FMT == 1:
                w = b.to(tl.float8e4nv, bitcast=True).to(a.dtype)
            else:
                w = _e4m3_bits_to_f32(b).to(a.dtype)
            acc = tl.dot(a, w, acc)
            a_ptrs += BK
            b_ptrs += BK
        offs_m = row0 + rm
        offs_n = col0 + rn
        mmask = offs_m < M
        nmask = offs_n < N
        out = acc * tl.load(SB + offs_n, mask=nmask, other=0.)[None, :]
        if HAS_BIAS:
            out = out + tl.load(BIAS + offs_n, mask=nmask, other=0.).to(tl.float32)[None, :]
        if ACT == 1:
            out = _gelu_tanh(out)
        c_ptrs = C + row0.to(tl.int64) * stride_cm + (rm[:, None] * stride_cm + offs_n[None, :])
        tl.store(c_ptrs, out.to(C.dtype.element_ty), mask=mmask[:, None] & nmask[None, :])

    @triton.jit
    def _dequant_kernel(Q, S, OUT, NK, K, W_FMT: tl.constexpr, BLOCK: tl.constexpr):
        offs = tl.program_id(0).to(tl.int64) * BLOCK + tl.arange(0, BLOCK)
        mask = offs < NK
        q = tl.load(Q + offs, mask=mask, other=0)
        if W_FMT == 0:
            v = q.to(tl.float32)
        elif W_FMT == 1:
            v = q.to(tl.float8e4nv, bitcast=True).to(tl.float32)
        else:
            v = _e4m3_bits_to_f32(q)
        s = tl.load(S + offs // K, mask=mask, other=0.)
        tl.store(OUT + offs, (v * s).to(OUT.dtype.element_ty), mask=mask)

    @triton.jit
    def _aq_load(xb, MULT, loff, cols, mask, kmask, HAS_MULT: tl.constexpr):
        x = tl.load(xb + loff, mask=mask, other=0.)
        if HAS_MULT:
            x = x.to(tl.float32) * tl.load(MULT + cols, mask=kmask, other=0.)
        return x

    @triton.jit
    def _aq_transform(xb, MULT, had, loff, cols, mask, kmask,
                      HAS_MULT: tl.constexpr, G: tl.constexpr, CB: tl.constexpr):
        """Load the G sub-tiles [R, CB] of one chunk and apply x -> x @ (H_G (x) H_CB).

        G == 0: no rotation (single tile, fp32). Otherwise every sub-tile goes
        through one tensor-core dot with the +-1 H_CB, then an H_G butterfly."""
        if G == 0:
            y1 = _aq_load(xb, MULT, loff, cols, mask, kmask, HAS_MULT).to(tl.float32)
            y2 = y1
            y3 = y1
            y4 = y1
        else:
            z1 = tl.dot(_aq_load(xb, MULT, loff, cols, mask, kmask, HAS_MULT).to(had.dtype), had)
            if G == 1:
                y1 = z1
                y2 = z1
                y3 = z1
                y4 = z1
            else:
                z2 = tl.dot(_aq_load(xb, MULT, loff + CB, cols + CB, mask, kmask, HAS_MULT).to(had.dtype), had)
                if G == 2:
                    y1 = z1 + z2
                    y2 = z1 - z2
                    y3 = y1
                    y4 = y1
                else:
                    z3 = tl.dot(_aq_load(xb, MULT, loff + 2 * CB, cols + 2 * CB, mask, kmask,
                                         HAS_MULT).to(had.dtype), had)
                    z4 = tl.dot(_aq_load(xb, MULT, loff + 3 * CB, cols + 3 * CB, mask, kmask,
                                         HAS_MULT).to(had.dtype), had)
                    a = z1 + z2
                    b = z1 - z2
                    c = z3 + z4
                    d = z3 - z4
                    y1 = a + c
                    y2 = b + d
                    y3 = a - c
                    y4 = b - d
        return y1, y2, y3, y4

    @triton.jit
    def _aq_rowstat(y, mask, ASYM: tl.constexpr):
        if ASYM:
            hi = tl.max(tl.where(mask, y, -float('inf')), axis=1)
            lo = tl.min(tl.where(mask, y, float('inf')), axis=1)
        else:
            hi = tl.max(tl.abs(y), axis=1)
            lo = hi
        return hi, lo

    @triton.jit
    def _aq_stats(y1, y2, y3, y4, mask, G: tl.constexpr, ROWS: tl.constexpr, NB: tl.constexpr,
                  ASYM: tl.constexpr):
        hi, lo = _aq_rowstat(y1, mask, ASYM)
        if G >= 2:
            h, l2 = _aq_rowstat(y2, mask, ASYM)
            hi = tl.maximum(hi, h)
            lo = tl.minimum(lo, l2)
        if G == 4:
            h, l2 = _aq_rowstat(y3, mask, ASYM)
            hi = tl.maximum(hi, h)
            lo = tl.minimum(lo, l2)
            h, l2 = _aq_rowstat(y4, mask, ASYM)
            hi = tl.maximum(hi, h)
            lo = tl.minimum(lo, l2)
        hi = tl.max(tl.reshape(hi, (ROWS, NB)), axis=1)
        lo = tl.min(tl.reshape(lo, (ROWS, NB)), axis=1)
        return hi, lo

    @triton.jit
    def _aq_q(y, inv_j, lo_j, ASYM: tl.constexpr, OUT_FP8: tl.constexpr, QMAX: tl.constexpr):
        if ASYM:
            q = tl.clamp(libdevice.rint((y - lo_j) * inv_j) - 128.0, -128.0, 127.0).to(tl.int8)
        elif OUT_FP8:
            q = tl.clamp(y * inv_j, -QMAX, QMAX).to(tl.float8e4nv)
        else:
            q = tl.clamp(libdevice.rint(y * inv_j), -QMAX, QMAX).to(tl.int8)
        return q

    @triton.jit
    def _aq_store(qb, qoff, y1, y2, y3, y4, mask, inv, lo, G: tl.constexpr, CB: tl.constexpr,
                  ROWS: tl.constexpr, NB: tl.constexpr, ASYM: tl.constexpr, OUT_FP8: tl.constexpr,
                  QMAX: tl.constexpr):
        R: tl.constexpr = ROWS * NB
        inv_j = tl.reshape(tl.broadcast_to(inv[:, None], (ROWS, NB)), (R,))[:, None]
        lo_j = tl.reshape(tl.broadcast_to(lo[:, None], (ROWS, NB)), (R,))[:, None]
        tl.store(qb + qoff, _aq_q(y1, inv_j, lo_j, ASYM, OUT_FP8, QMAX), mask=mask)
        if G >= 2:
            tl.store(qb + qoff + CB, _aq_q(y2, inv_j, lo_j, ASYM, OUT_FP8, QMAX), mask=mask)
        if G == 4:
            tl.store(qb + qoff + 2 * CB, _aq_q(y3, inv_j, lo_j, ASYM, OUT_FP8, QMAX), mask=mask)
            tl.store(qb + qoff + 3 * CB, _aq_q(y4, inv_j, lo_j, ASYM, OUT_FP8, QMAX), mask=mask)

    @triton.jit
    def _act_quant_kernel(X, MULT, HAD, Q, S, O, M, K, stride_xm, stride_qm, n_blocks,
                          NB: tl.constexpr, CB: tl.constexpr, G: tl.constexpr, ROWS: tl.constexpr,
                          NCHUNK: tl.constexpr, HAS_MULT: tl.constexpr, ASYM: tl.constexpr,
                          OUT_FP8: tl.constexpr, QMAX: tl.constexpr, ROT_NORM: tl.constexpr):
        """Per-token dynamic quantization (optionally smoothed + block-Hadamard rotated).

        Persistent programs; each step owns ROWS tokens viewed as [ROWS*NB, CB]
        sub-tiles: tile row j = (token j // NB, K-block j % NB), a K-block spans
        SPAN = max(G, 1) * CB columns (the rotation block). NCHUNK == 1: the whole
        row stays in registers (one read of x). NCHUNK > 1 (rows too long for
        registers): pass 1 computes the row statistics, pass 2 re-reads (L2) and
        quantizes, with bounded registers / shared memory on 99 KB parts.
        """
        pid = tl.program_id(0)
        nprog = tl.num_programs(0)
        R: tl.constexpr = ROWS * NB
        SPAN: tl.constexpr = CB * G if G > 0 else CB
        CHUNK: tl.constexpr = NB * SPAN
        j = tl.arange(0, R)
        c = tl.arange(0, CB)
        r_loc = j // NB
        cols0 = ((j % NB) * SPAN)[:, None] + c[None, :]
        loff0 = r_loc[:, None] * stride_xm + cols0
        qoff0 = r_loc[:, None] * stride_qm + cols0
        if G > 0:
            had = tl.load(HAD + c[:, None] * CB + c[None, :])
        else:
            had = 0.0
        srow = tl.arange(0, ROWS)
        for blk in range(pid, n_blocks, nprog):
            row0 = blk * ROWS
            xb = X + row0.to(tl.int64) * stride_xm
            qb = Q + row0.to(tl.int64) * stride_qm
            rmask = (row0 + r_loc < M)[:, None]
            if NCHUNK == 1:
                kmask = cols0 < K
                mask = rmask & kmask
                y1, y2, y3, y4 = _aq_transform(xb, MULT, had, loff0, cols0, mask, kmask, HAS_MULT, G, CB)
                hi, lo = _aq_stats(y1, y2, y3, y4, mask, G, ROWS, NB, ASYM)
            else:
                if ASYM:
                    hi = tl.full((ROWS,), -float('inf'), tl.float32)
                    lo = tl.full((ROWS,), float('inf'), tl.float32)
                else:
                    hi = tl.zeros((ROWS,), tl.float32)
                    lo = hi
                for ch in range(NCHUNK):
                    cols = cols0 + ch * CHUNK
                    kmask = cols < K
                    mask = rmask & kmask
                    y1, y2, y3, y4 = _aq_transform(xb, MULT, had, loff0 + ch * CHUNK, cols, mask, kmask,
                                                   HAS_MULT, G, CB)
                    h, l2 = _aq_stats(y1, y2, y3, y4, mask, G, ROWS, NB, ASYM)
                    hi = tl.maximum(hi, h)
                    lo = tl.minimum(lo, l2)
            if ASYM:
                step = (hi - lo) / 255.0
                step = tl.where(step > 0, step, 1.0)
                inv = 1.0 / step
            else:
                inv = tl.where(hi > 0, QMAX / hi, 0.)
                step = hi / QMAX
            if NCHUNK == 1:
                _aq_store(qb, qoff0, y1, y2, y3, y4, mask, inv, lo, G, CB, ROWS, NB, ASYM, OUT_FP8, QMAX)
            else:
                for ch in range(NCHUNK):
                    cols = cols0 + ch * CHUNK
                    kmask = cols < K
                    mask = rmask & kmask
                    y1, y2, y3, y4 = _aq_transform(xb, MULT, had, loff0 + ch * CHUNK, cols, mask, kmask,
                                                   HAS_MULT, G, CB)
                    _aq_store(qb, qoff0 + ch * CHUNK, y1, y2, y3, y4, mask, inv, lo, G, CB, ROWS, NB,
                              ASYM, OUT_FP8, QMAX)
            smask = row0 + srow < M
            tl.store(S + row0 + srow, step * ROT_NORM, mask=smask)
            if ASYM:
                tl.store(O + row0 + srow, (lo + 128.0 * step) * ROT_NORM, mask=smask)

    @triton.jit
    def _lnq_prep(x, cols, mask, kmask, mean_j, rstd_j, LNW, LNB, SHIFT, SCALE, moff, MULT, yb, yoff,
                  LN_AFFINE: tl.constexpr, HAS_MOD: tl.constexpr, HAS_MULT: tl.constexpr,
                  WRITE_Y: tl.constexpr):
        y = (x - mean_j[:, None]) * rstd_j[:, None]
        if LN_AFFINE:
            y = y * tl.load(LNW + cols, mask=kmask, other=0.).to(tl.float32) + \
                tl.load(LNB + cols, mask=kmask, other=0.).to(tl.float32)
        if HAS_MOD:
            sc = tl.load(SCALE + moff + cols, mask=mask, other=0.).to(tl.float32)
            sh = tl.load(SHIFT + moff + cols, mask=mask, other=0.).to(tl.float32)
            y = y * (1.0 + sc) + sh
        if WRITE_Y:  # also emit modulate(LN(x)) in the activation dtype for non-quantized consumers
            tl.store(yb + yoff, y.to(yb.dtype.element_ty), mask=mask)
        if HAS_MULT:
            y = y * tl.load(MULT + cols, mask=kmask, other=0.)
        return tl.where(mask, y, 0.)

    @triton.jit
    def _ln_quant_kernel(X, LNW, LNB, SHIFT, SCALE, MULT, HAD, Q, S, O, Y, M, K, stride_xm, stride_qm,
                         stride_ym, stride_mod, mod_div, eps, n_blocks,
                         NB: tl.constexpr, CB: tl.constexpr, G: tl.constexpr, ROWS: tl.constexpr,
                         LN_AFFINE: tl.constexpr, HAS_MOD: tl.constexpr, HAS_MULT: tl.constexpr,
                         WRITE_Y: tl.constexpr, WRITE_Q: tl.constexpr,
                         ASYM: tl.constexpr, OUT_FP8: tl.constexpr, QMAX: tl.constexpr, ROT_NORM: tl.constexpr):
        """LayerNorm(x) [* w + b] [* (1 + scale) + shift] [* act_mult] -> rotate -> quantize,
        one read of x (single pass; the row stays in registers). Modulation row for
        token m is m // mod_div (mod_div = tokens per sample; >= M for one shared row)."""
        pid = tl.program_id(0)
        nprog = tl.num_programs(0)
        R: tl.constexpr = ROWS * NB
        SPAN: tl.constexpr = CB * G if G > 0 else CB
        j = tl.arange(0, R)
        c = tl.arange(0, CB)
        r_loc = j // NB
        cols1 = ((j % NB) * SPAN)[:, None] + c[None, :]
        loff = r_loc[:, None] * stride_xm + cols1
        qoff = r_loc[:, None] * stride_qm + cols1
        yoff = r_loc[:, None] * stride_ym + cols1
        if G > 0:
            had = tl.load(HAD + c[:, None] * CB + c[None, :])
        srow = tl.arange(0, ROWS)
        for blk in range(pid, n_blocks, nprog):
            row0 = blk * ROWS
            xb = X + row0.to(tl.int64) * stride_xm
            qb = Q + row0.to(tl.int64) * stride_qm
            yb = Y + row0.to(tl.int64) * stride_ym
            rows = row0 + r_loc
            rmask = (rows < M)[:, None]
            moff = ((rows // mod_div).to(tl.int64) * stride_mod)[:, None]
            k1 = cols1 < K
            m1 = rmask & k1
            x1 = tl.load(xb + loff, mask=m1, other=0.).to(tl.float32)
            s1 = tl.sum(x1, axis=1)
            if G >= 2:
                k2 = cols1 + CB < K
                m2 = rmask & k2
                x2 = tl.load(xb + loff + CB, mask=m2, other=0.).to(tl.float32)
                s1 += tl.sum(x2, axis=1)
            if G == 4:
                k3 = cols1 + 2 * CB < K
                m3 = rmask & k3
                x3 = tl.load(xb + loff + 2 * CB, mask=m3, other=0.).to(tl.float32)
                k4 = cols1 + 3 * CB < K
                m4 = rmask & k4
                x4 = tl.load(xb + loff + 3 * CB, mask=m4, other=0.).to(tl.float32)
                s1 += tl.sum(x3, axis=1) + tl.sum(x4, axis=1)
            mean = tl.sum(tl.reshape(s1, (ROWS, NB)), axis=1) / K
            mean_j = tl.reshape(tl.broadcast_to(mean[:, None], (ROWS, NB)), (R,))
            d = tl.where(m1, x1 - mean_j[:, None], 0.)
            v1 = tl.sum(d * d, axis=1)
            if G >= 2:
                d = tl.where(m2, x2 - mean_j[:, None], 0.)
                v1 += tl.sum(d * d, axis=1)
            if G == 4:
                d = tl.where(m3, x3 - mean_j[:, None], 0.)
                v1 += tl.sum(d * d, axis=1)
                d = tl.where(m4, x4 - mean_j[:, None], 0.)
                v1 += tl.sum(d * d, axis=1)
            var = tl.sum(tl.reshape(v1, (ROWS, NB)), axis=1) / K
            rstd = 1.0 / tl.sqrt(var + eps)
            rstd_j = tl.reshape(tl.broadcast_to(rstd[:, None], (ROWS, NB)), (R,))
            y1 = _lnq_prep(x1, cols1, m1, k1, mean_j, rstd_j, LNW, LNB, SHIFT, SCALE, moff, MULT, yb, yoff,
                           LN_AFFINE, HAS_MOD, HAS_MULT, WRITE_Y)
            if G == 0:
                y2 = y1
                y3 = y1
                y4 = y1
            else:
                z1 = tl.dot(y1.to(had.dtype), had)
                if G == 1:
                    y1 = z1
                    y2 = z1
                    y3 = z1
                    y4 = z1
                else:
                    y2 = _lnq_prep(x2, cols1 + CB, m2, k2, mean_j, rstd_j, LNW, LNB, SHIFT, SCALE, moff, MULT,
                                   yb, yoff + CB, LN_AFFINE, HAS_MOD, HAS_MULT, WRITE_Y)
                    z2 = tl.dot(y2.to(had.dtype), had)
                    if G == 2:
                        y1 = z1 + z2
                        y2 = z1 - z2
                        y3 = y1
                        y4 = y1
                    else:
                        y3 = _lnq_prep(x3, cols1 + 2 * CB, m3, k3, mean_j, rstd_j, LNW, LNB, SHIFT, SCALE, moff,
                                       MULT, yb, yoff + 2 * CB, LN_AFFINE, HAS_MOD, HAS_MULT, WRITE_Y)
                        y4 = _lnq_prep(x4, cols1 + 3 * CB, m4, k4, mean_j, rstd_j, LNW, LNB, SHIFT, SCALE, moff,
                                       MULT, yb, yoff + 3 * CB, LN_AFFINE, HAS_MOD, HAS_MULT, WRITE_Y)
                        z3 = tl.dot(y3.to(had.dtype), had)
                        z4 = tl.dot(y4.to(had.dtype), had)
                        a = z1 + z2
                        b = z1 - z2
                        cc = z3 + z4
                        dd = z3 - z4
                        y1 = a + cc
                        y2 = b + dd
                        y3 = a - cc
                        y4 = b - dd
            if WRITE_Q:
                hi, lo = _aq_stats(y1, y2, y3, y4, m1, G, ROWS, NB, ASYM)
                if ASYM:
                    step = (hi - lo) / 255.0
                    step = tl.where(step > 0, step, 1.0)
                    inv = 1.0 / step
                else:
                    inv = tl.where(hi > 0, QMAX / hi, 0.)
                    step = hi / QMAX
                _aq_store(qb, qoff, y1, y2, y3, y4, m1, inv, lo, G, CB, ROWS, NB, ASYM, OUT_FP8, QMAX)
                smask = row0 + srow < M
                tl.store(S + row0 + srow, step * ROT_NORM, mask=smask)
                if ASYM:
                    tl.store(O + row0 + srow, (lo + 128.0 * step) * ROT_NORM, mask=smask)


# num_stages of the persistent activation-quant loops. Triton's default (3)
# multi-buffers the next row in shared memory: 240 KB for LN+rotation at K=5120,
# which does not even fit an H200. Explicit and AOT-checked (compile check).
ACTQ_STAGES = int(os.environ.get('PRISM_QLINEAR_ACTQ_STAGES', '2'))
_STAGE_FALLBACK: Dict[tuple, int] = {}


def _launch_stages(kernel, grid, key, stages, *args, **kw):
    """Launch with ``stages``; on OutOfResources (smem: 99 KB consumer parts) retry
    with fewer stages and remember the working value for this specialization."""
    st = _STAGE_FALLBACK.get(key, stages)
    while True:
        try:
            kernel[grid](*args, num_stages=st, **kw)
            _STAGE_FALLBACK[key] = st
            return
        except Exception as e:  # triton.runtime.errors.OutOfResources
            if 'out of resource' not in str(e).lower() or st <= 1:
                raise
            st -= 1


def _act_quant_plan(k: int, rot_block: int):
    """Host-side tiling for _act_quant_kernel: (NB, CB, G, ROWS, NCHUNK, num_warps).

    Single pass (row in registers, x read once) whenever the padded row has
    <= 16384 elements, which covers every Prism width (1536, 5120, 8960, 13824);
    the chunked two-pass path is for wider rows. Rough H200 measurements: the
    single pass is ~2x faster than chunking with rotation, and 4 warps beat 8 / 16
    for <= 8192-element tiles."""
    if rot_block:
        cb, g = 64, rot_block // 64
    else:
        cb, g = 128, 0
    span = cb * max(g, 1)
    nb_full = triton.next_power_of_2(-(-k // span))
    if nb_full * span <= 16384:
        nb, nchunk = nb_full, 1
        rows = max(1, (4096 if g else 8192) // (nb * span))
        if g:
            rows = max(rows, -(-16 // nb))  # tl.dot needs >= 16 tile rows
        rows = min(triton.next_power_of_2(rows), 64)
    else:
        nb = max(4096 // span, 16 if g else 1)
        nchunk, rows = -(-k // (nb * span)), 1
    elems = rows * nb * span
    # 16 warps keep the 16384-element no-rotation row spill-free (128 regs); with
    # rotation 16 warps spill heavily, 8 warps spill <= 216 B (AOT cubin check).
    warps = 4 if elems <= 8192 else (16 if g == 0 else 8)
    return nb, cb, g, rows, nchunk, warps, ACTQ_STAGES


# ----------------------------------------------------------------------------
# Hadamard / weight quantization helpers (torch, any device)
# ----------------------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def _hadamard_cpu(n: int) -> torch.Tensor:
    assert n & (n - 1) == 0 and n > 0, 'Hadamard size must be a power of two'
    h = torch.ones(1, 1)
    while h.shape[0] < n:
        h = torch.cat([torch.cat([h, h], 1), torch.cat([h, -h], 1)], 0)
    return h  # +-1, symmetric, H @ H = n I


def hadamard(n: int, device=None, dtype=torch.float32) -> torch.Tensor:
    return _hadamard_cpu(n).to(device=device, dtype=dtype)


def rotate_k(t: torch.Tensor, block: int) -> torch.Tensor:
    """t @ blockdiag(H_block) / sqrt(block) along the last dim (fp32 math)."""
    k = t.shape[-1]
    h = hadamard(block, t.device, torch.float32) / math.sqrt(block)
    return (t.float().reshape(-1, k // block, block) @ h).reshape(t.shape)


def random_signs(k: int, seed: int = 0) -> torch.Tensor:
    g = torch.Generator().manual_seed(1000003 * seed + k)
    return torch.randint(0, 2, (k,), generator=g).float() * 2 - 1


def _parse_rot(rot) -> int:
    if rot in (None, 0, '', 'none', False):
        return 0
    if isinstance(rot, int):
        b = rot
    else:
        s = str(rot).lower().replace('hadamard', '').replace('had', '').strip('_-')
        b = int(s) if s else 128
    if b not in (64, 128, 256):
        raise ValueError('rotation block must be 64, 128 or 256, got %r' % rot)
    return b


def quantize_weight_int8(w: torch.Tensor, clip: str = 'mse', chunk: int = 2048):
    """Per-output-channel symmetric INT8. clip='mse' searches a per-channel clip
    ratio in [0.80, 1.00] minimising ||W - Q(W)||^2 (RTN otherwise)."""
    n = w.shape[0]
    q = torch.empty(w.shape, dtype=torch.int8, device=w.device)
    s = torch.empty(n, dtype=torch.float32, device=w.device)
    ratios = [1.0] if clip in (None, 'none', 'rtn') else [1.0 - 0.025 * i for i in range(9)]
    for i in range(0, n, chunk):
        wc = w[i:i + chunk].float()
        amax = wc.abs().amax(1).clamp_min(1e-12)
        best_err = None
        for r in ratios:
            sc = amax * (r / INT8_MAX)
            qc = torch.clamp(torch.round(wc / sc[:, None]), -INT8_MAX, INT8_MAX)
            if len(ratios) == 1:
                best_q, best_s = qc, sc
                break
            err = (qc * sc[:, None] - wc).pow_(2).sum(1)
            if best_err is None:
                best_err, best_q, best_s = err, qc, sc
            else:
                better = err < best_err
                best_err = torch.where(better, err, best_err)
                best_q = torch.where(better[:, None], qc, best_q)
                best_s = torch.where(better, sc, best_s)
        q[i:i + chunk] = best_q.to(torch.int8)
        s[i:i + chunk] = best_s
    return q, s


def quantize_weight_fp8(w: torch.Tensor, chunk: int = 2048):
    """Per-output-channel E4M3 (scale = amax / 448). Returns raw bits as uint8."""
    n = w.shape[0]
    q = torch.empty(w.shape, dtype=torch.uint8, device=w.device)
    s = torch.empty(n, dtype=torch.float32, device=w.device)
    for i in range(0, n, chunk):
        wc = w[i:i + chunk].float()
        sc = wc.abs().amax(1).clamp_min(1e-12) / FP8_MAX
        q[i:i + chunk] = torch.clamp(wc / sc[:, None], -FP8_MAX, FP8_MAX).to(torch.float8_e4m3fn).view(torch.uint8)
        s[i:i + chunk] = sc
    return q, s


def smoothing_vector(act_absmax: torch.Tensor, weight: torch.Tensor, alpha: float = 0.5) -> torch.Tensor:
    """SmoothQuant s_j = max|X_j|^a / max|W_j|^(1-a); x / s and W * s."""
    w_absmax = weight.float().abs().amax(0).clamp_min(1e-5)
    a = act_absmax.float().to(w_absmax.device).clamp_min(1e-5)
    s = a.pow(alpha) / w_absmax.pow(1 - alpha)
    return s.clamp(1e-3, 1e3)


# ----------------------------------------------------------------------------
# Activation quantization + GEMM launchers
# ----------------------------------------------------------------------------


def _act_quant_ref(x2, out_fp8, mult, rot_block, asym):
    y = x2.float()
    if mult is not None:
        y = y * mult.to(y.device)
        if rot_block:  # the kernel feeds the tensor-core Hadamard in bf16 (fp16 for fp16 inputs)
            y = y.to(torch.float16 if x2.dtype == torch.float16 else torch.bfloat16).float()
    if rot_block:
        y = (y.reshape(-1, y.shape[1] // rot_block, rot_block) @ hadamard(rot_block, y.device)).reshape(y.shape)
    norm = 1.0 / math.sqrt(rot_block) if rot_block else 1.0
    if asym:
        hi, lo = y.amax(1), y.amin(1)
        step = (hi - lo) / 255.0
        step = torch.where(step > 0, step, torch.ones_like(step))
        q = torch.clamp(torch.round((y - lo[:, None]) / step[:, None]) - 128, -128, 127).to(torch.int8)
        return q, step * norm, (lo + 128 * step) * norm
    qmax = FP8_MAX if out_fp8 else INT8_MAX
    amax = y.abs().amax(1)
    inv = torch.where(amax > 0, qmax / amax, torch.zeros_like(amax))
    if out_fp8:
        q = torch.clamp(y * inv[:, None], -qmax, qmax).to(torch.float8_e4m3fn)
    else:
        q = torch.clamp(torch.round(y * inv[:, None]), -qmax, qmax).to(torch.int8)
    return q, amax * (norm / qmax), None


def quantize_activation(x2: torch.Tensor, out_fp8: bool = False, mult: Optional[torch.Tensor] = None,
                        rot_block: int = 0, asym: bool = False, had: Optional[torch.Tensor] = None,
                        backend: str = 'triton', plan: Optional[tuple] = None):
    """x2 [M, K] -> (q [M, K] int8|e4m3, scale [M] fp32, offset [M] fp32 | None).

    Dequantized value (in the rotated, smoothed space) is q * scale (+ offset)."""
    m, k = x2.shape
    if backend == 'ref' or not _triton_ok(x2.device):
        return _act_quant_ref(x2, out_fp8, mult, rot_block, asym)
    if x2.stride(1) != 1:
        x2 = x2.contiguous()
    if rot_block and k % rot_block:
        raise ValueError('K=%d is not a multiple of the rotation block %d' % (k, rot_block))
    q = torch.empty((m, k), device=x2.device, dtype=torch.float8_e4m3fn if out_fp8 else torch.int8)
    s = torch.empty(m, device=x2.device, dtype=torch.float32)
    o = torch.empty(m, device=x2.device, dtype=torch.float32) if asym else s
    if m == 0:
        return q, s, (o if asym else None)
    dot_dtype = torch.float16 if x2.dtype == torch.float16 else torch.bfloat16
    nb, cb, g, rows, nchunk, warps, stages = (tuple(plan) + (ACTQ_STAGES,))[:7] if plan else _act_quant_plan(k, rot_block)
    if g:
        if had is None or had.dtype != dot_dtype or had.shape[0] != cb:
            had = hadamard(cb, x2.device, dot_dtype)
    else:
        had = s  # unused placeholder pointer
    n_blocks = triton.cdiv(m, rows)
    props = _device_props(x2.device.index)
    grid = (max(1, min(n_blocks, props['sms'] * max(1, 32 // warps))),)
    key = ('aq', x2.device.index, x2.dtype, nb, cb, g, rows, nchunk, warps, mult is not None, asym, out_fp8)
    with torch.cuda.device(x2.device):
        _launch_stages(
            _act_quant_kernel, grid, key, stages,
            x2, mult if mult is not None else s, had, q, s, o, m, k, x2.stride(0), q.stride(0), n_blocks,
            NB=nb, CB=cb, G=g, ROWS=rows, NCHUNK=nchunk, HAS_MULT=mult is not None, ASYM=asym,
            OUT_FP8=out_fp8, QMAX=FP8_MAX if out_fp8 else INT8_MAX,
            ROT_NORM=(1.0 / math.sqrt(rot_block)) if rot_block else 1.0, num_warps=warps)
    return q, s, (o if asym else None)


def row_table(t: torch.Tensor, m: int, n: int):
    """Map a table broadcastable over tokens -- [n], [1, n], [B, 1, n] (per sample),
    [B, F, n] (per frame / per token, tokens sample-major then frame-major) or
    [M, n] -- onto the rows of a flattened [M, n] activation.
    Returns (table [R, n] with unit last stride, rows_per_table_row)."""
    if t.shape[-1] != n:
        raise ValueError('table last dim %d != %d' % (t.shape[-1], n))
    t2 = t.reshape(-1, n)
    if t2.stride(-1) != 1:
        t2 = t2.contiguous()
    r = t2.shape[0]
    if r == 1:
        return t2, max(m, 1)
    if m % r:
        raise ValueError('%d rows cannot be split evenly over %d table rows' % (m, r))
    return t2, m // r


def _ln_mod_ref(x2, eps, ln_weight, ln_bias, shift, scale):
    m, k = x2.shape
    y = F.layer_norm(x2.float(), (k,), ln_weight.float() if ln_weight is not None else None,
                     ln_bias.float() if ln_bias is not None else None, eps)
    if scale is not None or shift is not None:
        sc, div = row_table(scale, m, k)
        sh, _ = row_table(shift, m, k)
        idx = torch.arange(m, device=x2.device) // div
        y = y * (1 + sc.float().index_select(0, idx)) + sh.float().index_select(0, idx)
    return y


def quantize_activation_ln(x2: torch.Tensor, eps: float = 1e-6, ln_weight=None, ln_bias=None,
                           shift=None, scale=None, out_fp8: bool = False, mult=None, rot_block: int = 0,
                           asym: bool = False, had=None, backend: str = 'triton', want_y: bool = False,
                           want_q: bool = True):
    """quantize_activation(modulate(LayerNorm(x2), shift, scale)) in one pass over x2.

    LayerNorm in fp32 over the last dim (optional affine weight/bias), then
    y * (1 + scale) + shift with tables in any form ``row_table`` accepts (Wan's
    modulate), then act_mult / rotation / per-token quantization.
    want_y: also return y = modulate(LN(x2)) in x2's dtype (same pass), for
    consumers that need the unquantized tensor. want_q=False: only y (a fused
    LayerNorm + modulate). Returns (q, scale, offset|None, y|None)."""
    m, k = x2.shape
    if (shift is None) != (scale is None):
        raise ValueError('pass both shift and scale, or neither')
    plan = _act_quant_plan(k, rot_block if want_q else 0) if _HAS_TRITON else None
    if backend == 'ref' or not _triton_ok(x2.device) or plan[4] != 1:
        y = _ln_mod_ref(x2, eps, ln_weight, ln_bias, shift, scale)
        yo = y.to(x2.dtype) if want_y else None
        if not want_q:
            return None, None, None, yo
        if rot_block:
            y = y.to(torch.float16 if x2.dtype == torch.float16 else torch.bfloat16)
        if backend == 'ref' or not _triton_ok(x2.device):
            return _act_quant_ref(y, out_fp8, mult, rot_block, asym) + (yo,)
        return quantize_activation(y.to(x2.dtype), out_fp8, mult, rot_block, asym, had) + (yo,)
    if x2.stride(1) != 1:
        x2 = x2.contiguous()
    nb, cb, g, rows, _, warps, stages = plan
    warps = max(warps, 8)  # raw + normalized tiles are live together: 4 warps spill (AOT check)
    s = torch.empty(m, device=x2.device, dtype=torch.float32)
    if want_q:
        q = torch.empty((m, k), device=x2.device, dtype=torch.float8_e4m3fn if out_fp8 else torch.int8)
        o = torch.empty(m, device=x2.device, dtype=torch.float32) if asym else s
    else:
        q, o, g, asym, mult = s, s, 0, False, None
    y = torch.empty((m, k), device=x2.device, dtype=x2.dtype) if want_y else s
    if m == 0:
        return (q if want_q else None), (s if want_q else None), (o if asym else None), (y if want_y else None)
    dot_dtype = torch.float16 if x2.dtype == torch.float16 else torch.bfloat16
    if g and (had is None or had.dtype != dot_dtype or had.shape[0] != cb):
        had = hadamard(cb, x2.device, dot_dtype)
    if scale is not None:
        sc, div = row_table(scale, m, k)
        sh, div2 = row_table(shift, m, k)
        if sh.shape != sc.shape or sh.stride() != sc.stride():
            sh = sh.expand_as(sc).contiguous()
            sc = sc.contiguous()
        stride_mod = sc.stride(0)
    else:
        sc = sh = s
        div, stride_mod = 1, 0
    n_blocks = triton.cdiv(m, rows)
    props = _device_props(x2.device.index)
    grid = (max(1, min(n_blocks, props['sms'] * max(1, 32 // warps))),)
    key = ('ln', x2.device.index, x2.dtype, nb, cb, g, rows, warps, ln_weight is not None, scale is not None,
           mult is not None, asym, out_fp8, want_y, want_q)
    with torch.cuda.device(x2.device):
        _launch_stages(
            _ln_quant_kernel, grid, key, stages,
            x2, ln_weight if ln_weight is not None else s, ln_bias if ln_bias is not None else s, sh, sc,
            mult if mult is not None else s, had if g else s, q, s, o, y, m, k, x2.stride(0),
            q.stride(0) if want_q else 0, y.stride(0) if want_y else 0, stride_mod, div, float(eps), n_blocks,
            NB=nb, CB=cb, G=g, ROWS=rows, LN_AFFINE=ln_weight is not None, HAS_MOD=scale is not None,
            HAS_MULT=mult is not None, WRITE_Y=want_y, WRITE_Q=want_q, ASYM=asym, OUT_FP8=out_fp8,
            QMAX=FP8_MAX if out_fp8 else INT8_MAX,
            ROT_NORM=(1.0 / math.sqrt(rot_block)) if rot_block else 1.0, num_warps=warps)
    if not want_q:
        return None, None, None, y
    return q, s, (o if asym else None), (y if want_y else None)


def _launch_w8a8(xq, sx, ox, wq, sw, wsum, bias, out, act, cfg, res=None, gate=None, gate_div=1):
    m, k = xq.shape
    n = wq.shape[0]
    bm, bn, bk, gm, warps, stages = cfg
    grid = (triton.cdiv(m, bm) * triton.cdiv(n, bn),)
    _w8a8_gemm_kernel[grid](
        xq, wq, out, sx, ox if ox is not None else sx, sw, wsum if wsum is not None else sw,
        bias if bias is not None else sw, res if res is not None else out, gate if gate is not None else sw,
        m, n, k, xq.stride(0), wq.stride(0), out.stride(0),
        res.stride(0) if res is not None else 0, gate.stride(0) if gate is not None else 0, gate_div,
        IS_INT=xq.dtype == torch.int8, HAS_OFFSET=ox is not None, HAS_BIAS=bias is not None,
        ACT=1 if act == 'gelu_tanh' else (2 if act == 'gelu_tanh_post' else 0), HAS_RES=res is not None,
        HAS_GATE=gate is not None,
        GATE_VEC=gate is not None and gate.shape[0] == 1,
        EVEN_K=k % bk == 0, BM=bm, BN=bn, BK=bk, GROUP_M=gm, num_warps=warps, num_stages=stages)


def _gated_residual(y, res, gate, gate_div):
    """Unfused out = res + gate[m // gate_div] * y, one broadcast addcmul in the
    residual dtype (the original GateModule's arithmetic; no [M, N] temporaries)."""
    m, n = y.shape
    if y.dtype != res.dtype:
        y = y.to(res.dtype)
    if gate is None:
        return res + y
    r = gate.shape[0]
    g = gate.to(res.dtype).view(r, 1, n)
    return torch.addcmul(res.view(r, m // r, n), g, y.view(r, m // r, n)).view(m, n)


def w8a8_gemm(xq, sx, wq, sw, bias=None, *, ox=None, wsum=None, act='none',
              out_dtype=torch.bfloat16, backend='triton', fast_accum=True, cfg=None,
              residual=None, gate=None, gate_div=1, out=None):
    """out[M, N] = (xq * sx (+ ox)) @ (wq * sw)^T + bias, int8 or e4m3 operands.

    wq is [N, K] (K contiguous). For FP8 pass wq as float8_e4m3fn.
    residual [M, N] (+ gate [G, N], gate row = m // gate_div): returns
    residual + gate * act(linear); ``out`` may be ``residual`` (in place).
    act='gelu_tanh_post' (with residual): GELU(residual + gate * linear) instead."""
    m, k = xq.shape
    n = wq.shape[0]
    dev = xq.device
    if act == 'gelu_tanh_post':
        if residual is None:
            act = 'gelu_tanh'
        elif backend != 'triton' or not _triton_ok(dev):
            r = w8a8_gemm(xq, sx, wq, sw, bias, ox=ox, wsum=wsum, out_dtype=out_dtype, backend=backend,
                          fast_accum=fast_accum, cfg=cfg, residual=residual, gate=gate, gate_div=gate_div, out=out)
            return r.copy_(F.gelu(r.float(), approximate='tanh').to(r.dtype))
    if residual is not None and backend != 'triton' or (residual is not None and not _triton_ok(dev)):
        y = w8a8_gemm(xq, sx, wq, sw, bias, ox=ox, wsum=wsum, act=act, out_dtype=out_dtype,
                      backend=backend, fast_accum=fast_accum, cfg=cfg)
        r = _gated_residual(y, residual, gate, gate_div)
        if out is not None:
            out.copy_(r)
            return out
        return r
    if backend == 'ref' or not _triton_ok(dev) and backend == 'triton':
        acc = xq.float() @ wq.float().t()
        out = acc * sx[:, None]
        if ox is not None:
            out = out + ox[:, None] * wsum[None, :]
        out = out * sw[None, :]
        if bias is not None:
            out = out + bias.float()
        if act == 'gelu_tanh':
            out = F.gelu(out, approximate='tanh')
        return out.to(out_dtype)
    if backend == 'scaled_mm' and (k % 16 or n % 16):  # cuBLASLt/CUTLASS FP8 alignment
        backend = 'triton' if _triton_ok(dev) else 'ref'
    if backend == 'scaled_mm':
        assert ox is None
        out = torch._scaled_mm(xq, wq.t(), scale_a=sx.reshape(m, 1), scale_b=sw.reshape(1, n),
                               bias=bias.to(out_dtype) if bias is not None else None,
                               out_dtype=out_dtype, use_fast_accum=fast_accum)
        return F.gelu(out, approximate='tanh') if act == 'gelu_tanh' else out
    if backend == 'int_mm':  # cuBLASLt INT8 baseline; int32 tile + torch epilogue
        assert xq.dtype == torch.int8
        out = torch.empty((m, n), device=dev, dtype=out_dtype)
        rows = max(32, (_INT_MM_CHUNK_BYTES // (4 * n)) // 32 * 32)
        for i in range(0, m, rows):
            j = min(m, i + rows)
            xi = xq[i:j]
            if j - i <= 16:  # _int_mm needs M > 16
                xi = torch.cat([xi, xi.new_zeros(17 - (j - i), k)])
            acc = torch._int_mm(xi, wq.t())[:j - i].float()
            acc.mul_(sx[i:j, None])
            if ox is not None:
                acc.add_(ox[i:j, None] * wsum[None, :])
            acc.mul_(sw[None, :])
            if bias is not None:
                acc.add_(bias.float())
            if act == 'gelu_tanh':
                acc = F.gelu(acc, approximate='tanh')
            out[i:j] = acc
        return out
    if backend != 'triton':
        raise ValueError('unknown w8a8 backend %r' % backend)
    if out is None:
        out = torch.empty((m, n), device=dev, dtype=residual.dtype if residual is not None else out_dtype)
    if m == 0:
        return out
    if bias is not None and not bias.is_contiguous():
        bias = bias.contiguous()
    if residual is not None:
        assert residual.shape == (m, n) and residual.stride(1) == 1 and out.stride(1) == 1
        if gate is not None:
            assert gate.dim() == 2 and gate.shape[1] == n and gate.stride(1) == 1
    with torch.cuda.device(dev):
        if cfg is None:
            tag = '%s%s%s' % ('i8' if xq.dtype == torch.int8 else 'f8', 'o' if ox is not None else '',
                              'g' if act == 'gelu_tanh' else '')
            scratch = torch.empty_like(out) if residual is not None and out.data_ptr() == residual.data_ptr() else out
            cfg = _pick_config('w8a8', tag, m, n, k, dev, 1, 1,
                               lambda c: _launch_w8a8(xq, sx, ox, wq, sw, wsum, bias, scratch, act, c,
                                                      residual, gate, gate_div))
            del scratch
        _launch_w8a8(xq, sx, ox, wq, sw, wsum, bias, out, act, cfg, residual, gate, gate_div)
    return out


# Force the integer E4M3 decode used below SM89 (lets SM89+/SM90 test the RTX 30 path).
FORCE_SOFT_FP8 = os.environ.get('PRISM_QLINEAR_SOFT_FP8', '0') == '1'


def _w_fmt(wq: torch.Tensor, device) -> int:
    if wq.dtype == torch.int8:
        return 0
    return 1 if _cc(device) >= (8, 9) and not FORCE_SOFT_FP8 else 2


def dequantize_weight(wq: torch.Tensor, sw: torch.Tensor, dtype=torch.bfloat16) -> torch.Tensor:
    """[N, K] int8 or uint8(E4M3 bits) -> dtype, value = q * s[n]."""
    if _triton_ok(wq.device):
        out = torch.empty(wq.shape, device=wq.device, dtype=dtype)
        nk = wq.numel()
        with torch.cuda.device(wq.device):
            _dequant_kernel[(triton.cdiv(nk, 4096),)](wq, sw, out, nk, wq.shape[1],
                                                       W_FMT=_w_fmt(wq, wq.device), BLOCK=4096, num_warps=8)
        return out
    w = wq.float() if wq.dtype == torch.int8 else wq.view(torch.float8_e4m3fn).float()
    return (w * sw[:, None].float()).to(dtype)


def _launch_w8a16(x2, wq, sw, bias, out, act, cfg):
    m, k = x2.shape
    n = wq.shape[0]
    bm, bn, bk, gm, warps, stages = cfg
    grid = (triton.cdiv(m, bm) * triton.cdiv(n, bn),)
    _w8a16_gemm_kernel[grid](
        x2, wq, out, sw, bias if bias is not None else sw, m, n, k, x2.stride(0), wq.stride(0), out.stride(0),
        W_FMT=_w_fmt(wq, x2.device), HAS_BIAS=bias is not None, ACT=1 if act == 'gelu_tanh' else 0,
        EVEN_K=k % bk == 0, BM=bm, BN=bn, BK=bk, GROUP_M=gm, num_warps=warps, num_stages=stages)


def w8a16_gemm(x2, wq, sw, bias=None, *, act='none', backend='triton', cfg=None):
    """x2 [M, K] bf16/fp16; wq [N, K] int8 or uint8 E4M3 bits; per-channel sw."""
    m, k = x2.shape
    n = wq.shape[0]
    dev = x2.device
    if backend == 'triton' and not _triton_ok(dev):
        backend = 'ref'
    if backend == 'triton' and m >= W8A16_DEQUANT_M:
        backend = 'dequant'
    if backend in ('ref', 'dequant'):
        w = dequantize_weight(wq, sw, x2.dtype if x2.dtype in (torch.float16, torch.bfloat16) else torch.float32)
        out = F.linear(x2.to(w.dtype), w, bias.to(w.dtype) if bias is not None else None)
        return F.gelu(out, approximate='tanh') if act == 'gelu_tanh' else out
    if backend != 'triton':
        raise ValueError('unknown w8a16 backend %r' % backend)
    if x2.dtype not in (torch.bfloat16, torch.float16):
        x2 = x2.to(torch.bfloat16)
    if x2.stride(1) != 1:
        x2 = x2.contiguous()
    out = torch.empty((m, n), device=dev, dtype=x2.dtype)
    if m == 0:
        return out
    with torch.cuda.device(dev):
        if cfg is None:
            tag = '%s%s' % ('i8' if wq.dtype == torch.int8 else 'f8', 'g' if act == 'gelu_tanh' else '')
            cfg = _pick_config('w8a16', tag, m, n, k, dev, 2, 1,
                               lambda c: _launch_w8a16(x2, wq, sw, bias, out, act, c))
        _launch_w8a16(x2, wq, sw, bias, out, act, cfg)
    return out


# ----------------------------------------------------------------------------
# Module
# ----------------------------------------------------------------------------


class QLinear(nn.Module):
    """Drop-in replacement for nn.Linear. forward(x[..., K]) -> [..., N].

    Buffers (all persistent, safetensors friendly):
      qweight  [N, K] int8 (INT8 modes) or uint8 E4M3 bits (FP8 modes; kept as
               uint8 so model.to(dtype) cannot cast it)
      w_scale  [N] fp32 per output channel
      bias     [N] original dtype or absent
      act_mult [K] fp32 (smoothing^-1 * random signs) or absent
      w_sum    [N] fp32 sum_k qweight[n, k] (asymmetric activations only)
    """

    def __init__(self, in_features: int, out_features: int, mode: str, *, bias: bool = True,
                 rot_block: int = 0, has_mult: bool = False, act_asym: bool = False,
                 bias_dtype=torch.bfloat16, device=None, backend: str = 'auto', fast_accum: bool = True):
        super().__init__()
        if mode not in MODES:
            raise ValueError('mode must be one of %s, got %r' % (MODES, mode))
        if mode.startswith('w8a16') and (rot_block or has_mult or act_asym):
            raise ValueError('rotation / smoothing / asym apply to w8a8 modes only')
        if act_asym and mode != 'w8a8_int8':
            raise ValueError('asymmetric activations are implemented for w8a8_int8 only')
        if rot_block and in_features % rot_block:
            raise ValueError('in_features=%d not divisible by rotation block %d' % (in_features, rot_block))
        self.in_features, self.out_features, self.mode = in_features, out_features, mode
        self.rot_block, self.act_asym, self.backend, self.fast_accum = rot_block, act_asym, backend, fast_accum
        self.act = 'none'  # 'gelu_tanh' when fused by fuse_gelu_epilogue
        self._pending_epilogue = None
        wdt = torch.int8 if mode.endswith('int8') else torch.uint8
        self.register_buffer('qweight', torch.empty(out_features, in_features, dtype=wdt, device=device))
        self.register_buffer('w_scale', torch.empty(out_features, dtype=torch.float32, device=device))
        self.register_buffer('bias', torch.empty(out_features, dtype=bias_dtype, device=device) if bias else None)
        self.register_buffer('act_mult', torch.empty(in_features, dtype=torch.float32, device=device)
                             if has_mult else None)
        self.register_buffer('w_sum', torch.empty(out_features, dtype=torch.float32, device=device)
                             if act_asym else None)
        self._had = {}

    # Keep the quantization metadata fp32 under model.to(dtype)/half()/bfloat16().
    def _apply(self, fn, recurse=True):
        keep = {n: self._buffers[n] for n in ('w_scale', 'act_mult', 'w_sum') if self._buffers.get(n) is not None}
        super()._apply(fn, recurse)
        for n, old in keep.items():
            new = self._buffers[n]
            if new.dtype != torch.float32:
                self._buffers[n] = old.to(device=new.device)
        self._had = {}
        return self

    @property
    def weight_dtype(self):
        return torch.int8 if self.mode.endswith('int8') else torch.float8_e4m3fn

    @property
    def weight(self):
        """The quantized codes (int8, or float8_e4m3fn view), NOT a usable weight.
        Exists for code that only inspects ``linear.weight.dtype/device/shape``
        (e.g. HF T5 skips its fp32 cast of ``wo`` inputs when the dtype is int8)."""
        return self.qweight if self.mode.endswith('int8') else self.qweight.view(torch.float8_e4m3fn)

    # -- side channels used by hymm.fast.qblock (fused DiT block) -------------
    # A caller that already quantized this layer's input attaches
    # ``x._qlinear_prequant = (consumer_ids, xq, sx, ox)`` to the tensor it passes
    # in; a caller that wants ``residual + gate * self(x)`` from the next call sets
    # ``self._pending_epilogue = (residual, gate)`` (consumed and cleared by it).
    _pending_epilogue = None
    _is_prism_qlinear = True  # duck-type marker (survives importlib.reload of this module)

    def extra_repr(self):
        return 'in=%d, out=%d, mode=%s, rot=%s, smooth/signs=%s, asym=%s, act=%s, backend=%s' % (
            self.in_features, self.out_features, self.mode, self.rot_block or None,
            self.act_mult is not None, self.act_asym, self.act, self.backend)

    @classmethod
    @torch.no_grad()
    def from_linear(cls, linear: nn.Linear, mode: str, rot=None, *, rot_signs: bool = True, rot_seed: int = 0,
                    smooth: Optional[torch.Tensor] = None, smooth_alpha: float = 0.5,
                    act_absmax: Optional[torch.Tensor] = None, act_asym: bool = False,
                    w_clip: str = 'mse', backend: str = 'auto', fast_accum: bool = True,
                    quant_device=None, device=None) -> 'QLinear':
        """Quantize ``linear``. smooth: explicit per-in-channel vector s (x/s, W*s);
        or act_absmax: calibration max|x_j| from which s is derived (SmoothQuant)."""
        w = linear.weight
        out_dev = torch.device(device) if device is not None else w.device
        qdev = torch.device(quant_device) if quant_device is not None else (
            w.device if w.device.type == 'cuda' else (torch.device('cuda') if torch.cuda.is_available() else w.device))
        rb = _parse_rot(rot)
        n, k = w.shape
        wf = w.detach().to(qdev, torch.float32)
        mult = None
        if mode.startswith('w8a8'):
            if smooth is None and act_absmax is not None:
                smooth = smoothing_vector(act_absmax, wf, smooth_alpha)
            if smooth is not None:
                smooth = smooth.to(qdev, torch.float32)
                wf = wf * smooth[None, :]
                mult = 1.0 / smooth
            if rb:
                if rot_signs:
                    d = random_signs(k, rot_seed).to(qdev)
                    wf = wf * d[None, :]
                    mult = d if mult is None else mult * d
                wf = rotate_k(wf, rb)
        elif rot or smooth is not None or act_absmax is not None or act_asym:
            raise ValueError('rotation / smoothing / asym apply to w8a8 modes only')
        if mode.endswith('int8'):
            q, s = quantize_weight_int8(wf, w_clip)
        else:
            q, s = quantize_weight_fp8(wf)
        del wf
        m = cls(k, n, mode, bias=linear.bias is not None, rot_block=rb, has_mult=mult is not None,
                act_asym=act_asym, bias_dtype=linear.bias.dtype if linear.bias is not None else torch.bfloat16,
                device=out_dev, backend=backend, fast_accum=fast_accum)
        m.qweight.copy_(q)
        m.w_scale.copy_(s)
        if linear.bias is not None:
            m.bias.copy_(linear.bias.detach())
        if mult is not None:
            m.act_mult.copy_(mult)
        if act_asym:
            m.w_sum.copy_(q.float().sum(1))
        return m

    def _hadamard_for(self, device, dtype):
        cb = 64  # kernel applies H_B as H_(B/64) (x) H_64
        key = (device, dtype)
        h = self._had.get(key)
        if h is None:
            h = self._had[key] = hadamard(cb, device, dtype)
        return h

    def resolved_backend(self, device) -> str:
        return _resolve_backend(self.mode, device, self.backend)

    def quantize_input(self, x2: torch.Tensor, ln: Optional[dict] = None, mod: Optional[dict] = None):
        """[M, K] -> (q, scale, offset|None). Layers that share an input and were
        quantized with the same act spec (quantize_model share groups: q/k/v)
        can quantize it once. ln=dict(eps=, weight=, bias=) and mod=dict(shift=,
        scale=) fuse LayerNorm + Wan modulate into the same single pass."""
        be = self.resolved_backend(x2.device)
        dot_dtype = torch.float16 if x2.dtype == torch.float16 else torch.bfloat16
        had = self._hadamard_for(x2.device, dot_dtype) if self.rot_block else None
        bk = 'ref' if be == 'ref' else 'triton'
        if ln is not None or mod is not None:
            ln = ln or {}
            mod = mod or {}
            return quantize_activation_ln(x2, ln.get('eps', 1e-6), ln.get('weight'), ln.get('bias'),
                                          mod.get('shift'), mod.get('scale'), self.mode == 'w8a8_fp8',
                                          self.act_mult, self.rot_block, self.act_asym, had, bk)[:3]
        return quantize_activation(x2, self.mode == 'w8a8_fp8', self.act_mult, self.rot_block, self.act_asym,
                                   had, bk)

    def forward_quantized(self, xq, sx, ox=None, out_dtype=torch.bfloat16, residual=None, gate=None, out=None,
                          act: Optional[str] = None):
        """GEMM on a pre-quantized input; residual/gate as in forward (2-D [M, N]).
        act overrides self.act for this call ('none' | 'gelu_tanh')."""
        be = self.resolved_backend(xq.device)
        wq = self.qweight if self.mode == 'w8a8_int8' else self.qweight.view(torch.float8_e4m3fn)
        gdiv = 1
        if gate is not None:
            gate, gdiv = row_table(gate, xq.shape[0], self.out_features)
        return w8a8_gemm(xq, sx, wq, self.w_scale, self.bias, ox=ox, wsum=self.w_sum, act=act or self.act,
                         out_dtype=out_dtype, backend=be, fast_accum=self.fast_accum,
                         residual=residual, gate=gate, gate_div=gdiv, out=out)

    def forward(self, x: torch.Tensor, residual: Optional[torch.Tensor] = None,
                gate: Optional[torch.Tensor] = None, out: Optional[torch.Tensor] = None) -> torch.Tensor:
        """Drop-in ``linear(x)``. With ``residual`` (shape [..., N]) returns
        residual + gate * linear(x) fused in the GEMM epilogue (Wan GateModule);
        gate may be [N], [B, 1, N], [B, F, N] or [..., N]. ``out=residual`` updates
        the residual stream in place."""
        if _OPS_OK and out is None and _compiling():
            return _forward_compiled(self, x, residual, gate)
        lead = x.shape[:-1]
        x2 = x.reshape(-1, self.in_features)
        out_dtype = x.dtype if x.dtype in (torch.bfloat16, torch.float16) else torch.bfloat16
        r2 = o2 = None
        if residual is not None:
            r2 = residual.reshape(-1, self.out_features)
            if r2.stride(-1) != 1:
                r2 = r2.contiguous()
            if out is not None:
                o2 = out.view(-1, self.out_features)
        if self.mode.startswith('w8a16'):
            be = self.resolved_backend(x.device)
            y = w8a16_gemm(x2 if be == 'ref' else x2.to(out_dtype), self.qweight, self.w_scale, self.bias,
                           act=self.act, backend=be).to(out_dtype)
            if r2 is not None:
                g2, gdiv = row_table(gate, x2.shape[0], self.out_features) if gate is not None else (None, 1)
                y = _gated_residual(y, r2, g2, gdiv)
                if o2 is not None:
                    o2.copy_(y)
                    return out
            return y.reshape(*lead, self.out_features)
        pre = getattr(x, '_qlinear_prequant', None)
        if pre is not None and id(self) in pre[0] and pre[1].shape[0] == x2.shape[0]:
            xq, sx, ox = pre[1], pre[2], pre[3]
        else:
            xq, sx, ox = self.quantize_input(x2)
        y = self.forward_quantized(xq, sx, ox, out_dtype, residual=r2, gate=gate, out=o2)
        if o2 is not None:
            return out
        return y.reshape(*lead, self.out_features)

    def __call__(self, *args, **kwargs):
        pending = self._pending_epilogue
        if pending is not None and len(args) == 1 and not kwargs and not _compiling():
            self._pending_epilogue = None
            res, gate = pending
            if res.shape[:-1] == args[0].shape[:-1]:
                return super().__call__(args[0], residual=res, gate=gate)
            self._pending_epilogue = ('unused',)  # shape mismatch: tell the caller it was not applied
        return super().__call__(*args, **kwargs)

    @torch.no_grad()
    def effective_weight(self) -> torch.Tensor:
        """W_hat in the original input space (fp32), for analysis."""
        w = dequantize_weight(self.qweight, self.w_scale, torch.float32)
        if self.rot_block:
            w = rotate_k(w, self.rot_block)  # H is symmetric orthonormal: H^-1 = H
        if self.act_mult is not None:
            w = w * self.act_mult[None, :]
        return w


# ----------------------------------------------------------------------------
# Model-level helpers
# ----------------------------------------------------------------------------


def default_include(name: str, linear: nn.Linear) -> bool:
    """Large 2-D projections only; tiny/odd layers stay bf16."""
    return (linear.in_features % 16 == 0 and linear.out_features % 16 == 0 and
            linear.in_features >= 512 and linear.out_features >= 512)


def _set_submodule(model: nn.Module, name: str, module: nn.Module):
    parent_name, _, child = name.rpartition('.')
    parent = model.get_submodule(parent_name) if parent_name else model
    setattr(parent, child, module)


# Per-layer candidates tried by select_config / quantize_model(calib=...). '_smooth'
# means: derive the SmoothQuant vector from the calibration activations.
DEFAULT_SEARCH = {
    'w8a8_int8': [dict(), dict(rot='had128'), dict(rot='had256'),
                  dict(rot='had128', smooth_alpha=0.5, _smooth=True),
                  dict(rot='had256', smooth_alpha=0.3, _smooth=True),
                  dict(smooth_alpha=0.5, _smooth=True)],
    'w8a8_fp8': [dict(), dict(smooth_alpha=0.5, _smooth=True), dict(rot='had128', smooth_alpha=0.5, _smooth=True)],
}


@torch.no_grad()
def select_config(linear: nn.Linear, x_calib: torch.Tensor, mode: str = 'w8a8_int8', candidates=None,
                  prefer_cheaper: float = 0.03, device=None, **fixed):
    """Pick the rotation / smoothing variant with the lowest output error on
    calibration activations x_calib [T, K]. Smoothing statistics come from the
    even rows and the error is measured on the odd rows, so a vector that only
    fits its own samples is not rewarded. A cheaper variant (fewer online ops)
    wins when within ``prefer_cheaper`` (relative) of the best.
    Returns (best_kwargs_for_from_linear, [(candidate, rel_err), ...])."""
    dev = torch.device(device) if device is not None else (
        linear.weight.device if linear.weight.device.type == 'cuda' else torch.device('cuda'))
    x = x_calib.reshape(-1, linear.in_features).to(dev, linear.weight.dtype)
    xs, xe = x[0::2], x[1::2]
    if xe.shape[0] == 0:
        xs = xe = x
    w = linear.weight.to(dev)
    ref = xe.float() @ w.float().t()
    if linear.bias is not None:
        ref += linear.bias.to(dev).float()
    lin = nn.Linear(linear.in_features, linear.out_features, bias=linear.bias is not None, device='meta')
    lin.weight = nn.Parameter(w, requires_grad=False)
    if linear.bias is not None:
        lin.bias = nn.Parameter(linear.bias.to(dev), requires_grad=False)
    cands = candidates if candidates is not None else DEFAULT_SEARCH.get(mode, [dict()])
    fixed = {k: v for k, v in fixed.items() if k not in ('rot', 'act_absmax', 'smooth', 'smooth_alpha')}
    trial = {k: v for k, v in fixed.items() if k not in ('device', 'quant_device')}
    table = []
    for cand in cands:
        kw = dict(trial, **cand)
        if kw.pop('_smooth', False):
            kw['act_absmax'] = xs.float().abs().amax(0)
        rb = _parse_rot(kw.get('rot'))
        if rb and linear.in_features % rb:
            continue
        q = QLinear.from_linear(lin, mode, quant_device=dev, device=dev, **kw)
        y = q(xe).float()
        table.append((cand, ((y - ref).norm() / ref.norm().clamp_min(1e-30)).item()))
        del q, y
    best_err = min(e for _, e in table)
    cost = lambda c: (_parse_rot(c.get('rot')) > 0) * 2 + bool(c.get('_smooth')) + _parse_rot(c.get('rot')) / 1024
    ok = [(cost(c), e, c) for c, e in table if e <= best_err * (1 + prefer_cheaper)]
    best = dict(min(ok, key=lambda t: (t[0], t[1]))[2])
    if best.pop('_smooth', False):
        best['act_absmax'] = x.float().abs().amax(0)
    return dict(fixed, **best), table


def wan_share_key(name: str) -> Optional[str]:
    """Group Linears that read the same tensor in Wan/MOVA blocks: self-attn
    q/k/v (one input) and cross-attn / bridge k/v (both read the context)."""
    head, _, leaf = name.rpartition('.')
    if head.endswith('self_attn') and leaf in ('q', 'k', 'v'):
        return head + '.(qkv)'
    if leaf in ('k', 'v') and (head.endswith('cross_attn') or head.endswith('inner')):
        return head + '.(kv)'
    if leaf in ('k_img', 'v_img'):
        return head + '.(kv_img)'
    return None


def _concat_linear(linears, device):
    w = torch.cat([l.weight.detach().to(device) for l in linears], 0)
    has_b = any(l.bias is not None for l in linears)
    big = nn.Linear(w.shape[1], w.shape[0], bias=has_b, device='meta')
    big.weight = nn.Parameter(w, requires_grad=False)
    if has_b:
        big.bias = nn.Parameter(torch.cat([
            (l.bias.detach() if l.bias is not None else torch.zeros(l.out_features, dtype=w.dtype)).to(device, w.dtype)
            for l in linears]), requires_grad=False)
    return big


@torch.no_grad()
def quantize_group(linears, mode: str, calib: Optional[torch.Tensor] = None, search=None, **opts):
    """Quantize Linears that share one input with a common activation spec
    (smoothing vector from the concatenated weight, same rotation, same signs),
    so ``quantize_input`` of any of them feeds all. Returns (list[QLinear], info)."""
    k = linears[0].in_features
    assert all(l.in_features == k for l in linears)
    dev = torch.device(opts.get('quant_device') or ('cuda' if torch.cuda.is_available() else 'cpu'))
    out_dev = opts.pop('device', None) or linears[0].weight.device
    big = _concat_linear(linears, dev)
    info = ''
    if calib is not None and mode.startswith('w8a8'):
        opts, table = select_config(big, calib, mode, search, device=dev, **opts)
        info = ' auto[%s]' % ', '.join('%s:%.4f' % (_cand_name(c), e) for c, e in table)
    q = QLinear.from_linear(big, mode, device=dev, **opts)
    del big
    outs, start = [], 0
    for l in linears:
        n = l.out_features
        m = QLinear(k, n, mode, bias=l.bias is not None, rot_block=q.rot_block, has_mult=q.act_mult is not None,
                    act_asym=q.act_asym, bias_dtype=l.bias.dtype if l.bias is not None else torch.bfloat16,
                    device=out_dev, backend=q.backend, fast_accum=q.fast_accum)
        m.qweight.copy_(q.qweight[start:start + n])
        m.w_scale.copy_(q.w_scale[start:start + n])
        if l.bias is not None:
            m.bias.copy_(q.bias[start:start + n])
        if q.act_mult is not None:
            m.act_mult.copy_(q.act_mult)
        if q.w_sum is not None:
            m.w_sum.copy_(q.w_sum[start:start + n])
        outs.append(m)
        start += n
    return outs, info


@torch.no_grad()
def quantize_model(model: nn.Module, mode: str,
                   include: Optional[Callable[[str, nn.Linear], Union[bool, dict, None]]] = None,
                   *, act_stats: Optional[Dict[str, torch.Tensor]] = None,
                   calib: Optional[Dict[str, torch.Tensor]] = None, search=None,
                   share: Optional[Callable[[str], Optional[str]]] = None,
                   verbose: bool = False, **kw) -> Dict[str, str]:
    """Swap nn.Linear layers in place. ``include(name, linear)`` returns False/None
    (keep bf16), True (use ``mode`` and ``kw``) or a dict of per-layer overrides,
    e.g. {'mode': 'w8a16_int8'} or {'rot': 'had128'}.

    act_stats[name]: per in-channel max|x| (collect_act_absmax) -> SmoothQuant.
    calib[name]: sampled input rows (collect_act_samples) -> per-layer automatic
    choice among ``search`` (default DEFAULT_SEARCH[mode]) by measured error;
    explicit 'rot'/'act_absmax'/'smooth' in the include dict skip the search.
    share(name) -> group key (e.g. wan_share_key): layers with the same key and
    mode are quantized jointly (common act spec, one quantize_input for all).
    Returns {name: description} of replaced layers."""
    include = include or default_include
    done = {}
    plans = []
    for name, lin in [(n, m) for n, m in model.named_modules() if type(m) is nn.Linear]:
        dec = include(name, lin)
        if not dec:
            continue
        opts = dict(kw, mode=mode)
        if isinstance(dec, dict):
            opts.update(dec)
        if opts['mode'] in (None, 'bf16', 'none'):
            continue
        explicit = isinstance(dec, dict) and any(k in dec for k in ('rot', 'act_absmax', 'smooth'))
        plans.append((name, lin, opts, explicit))
    groups: Dict[str, list] = {}
    if share is not None:
        for p in plans:
            key = share(p[0])
            if key is not None and p[2]['mode'].startswith('w8a8'):
                groups.setdefault('%s|%s' % (key, p[2]['mode']), []).append(p)
    grouped = set()
    for key, members in groups.items():
        if len(members) < 2:
            continue
        names = [p[0] for p in members]
        opts = dict(members[0][2])
        md = opts.pop('mode')
        cal = next((calib[n] for n in names if calib is not None and n in calib), None)
        if cal is None and act_stats is not None:
            st = [act_stats[n] for n in names if n in act_stats]
            if st:
                opts.setdefault('act_absmax', st[0])
        rb = _parse_rot(opts.get('rot'))
        if rb and members[0][1].in_features % rb:
            opts['rot'] = None
        cands = search.get(md) if isinstance(search, dict) else search
        qs, info = quantize_group([p[1] for p in members], md, None if members[0][3] else cal, cands, **opts)
        for (name, _, _, _), q in zip(members, qs):
            _set_submodule(model, name, q)
            done[name] = q.extra_repr() + ' shared[%s]' % key.split('|')[0] + info
            grouped.add(name)
            if verbose:
                print('[qlinear] %s: %s' % (name, done[name]))
    for name, lin, opts, explicit in plans:
        if name in grouped:
            continue
        opts = dict(opts)
        md = opts.pop('mode')
        info = ''
        if calib is not None and name in calib and md.startswith('w8a8') and not explicit:
            cands = search.get(md) if isinstance(search, dict) else search
            opts, table = select_config(lin, calib[name], md, cands, **opts)
            info = ' auto[%s]' % ', '.join('%s:%.4f' % (_cand_name(c), e) for c, e in table)
        elif act_stats is not None and name in act_stats and md.startswith('w8a8'):
            opts.setdefault('act_absmax', act_stats[name])
        rb = _parse_rot(opts.get('rot'))
        if rb and lin.in_features % rb:
            opts['rot'] = None
        q = QLinear.from_linear(lin, md, **opts)
        _set_submodule(model, name, q)
        done[name] = q.extra_repr() + info
        if verbose:
            print('[qlinear] %s: %s' % (name, done[name]))
        del lin
    return done


def _cand_name(c: dict) -> str:
    parts = [str(c['rot'])] if c.get('rot') else []
    if c.get('_smooth') or c.get('act_absmax') is not None:
        parts.append('smooth%.2g' % c.get('smooth_alpha', 0.5))
    if c.get('act_asym'):
        parts.append('asym')
    return '+'.join(parts) or 'plain'


@torch.no_grad()
def collect_act_samples(model: nn.Module, run: Callable[[], None],
                        include: Optional[Callable[[str, nn.Linear], bool]] = None,
                        max_tokens: int = 512, seed: int = 0) -> Dict[str, torch.Tensor]:
    """Run ``run()`` (e.g. a few denoising steps) and keep a uniform random subset
    of <= max_tokens input rows per Linear (bf16, CPU) for quantize_model(calib=)."""
    include = include or default_include
    store, hooks = {}, []
    gen = torch.Generator().manual_seed(seed)

    def hook(name):
        def fn(mod, inputs):
            x = inputs[0].detach().reshape(-1, inputs[0].shape[-1])
            take = min(x.shape[0], max_tokens)
            idx = torch.randperm(x.shape[0], generator=gen)[:take].to(x.device)
            rows = x.index_select(0, idx).to('cpu', torch.bfloat16)
            prev = store.get(name)
            rows = rows if prev is None else torch.cat([prev, rows])
            if rows.shape[0] > max_tokens:
                rows = rows[torch.randperm(rows.shape[0], generator=gen)[:max_tokens]]
            store[name] = rows
        return fn
    for name, mod in model.named_modules():
        if type(mod) is nn.Linear and include(name, mod):
            hooks.append(mod.register_forward_pre_hook(hook(name)))
    try:
        run()
    finally:
        for h in hooks:
            h.remove()
    return store


@torch.no_grad()
def collect_act_absmax(model: nn.Module, run: Callable[[], None],
                       include: Optional[Callable[[str, nn.Linear], bool]] = None) -> Dict[str, torch.Tensor]:
    """Run ``run()`` with hooks recording per-input-channel max|x| of each Linear."""
    include = include or default_include
    stats, hooks = {}, []

    def hook(name):
        def fn(mod, inputs):
            x = inputs[0].detach()
            a = x.reshape(-1, x.shape[-1]).abs().amax(0).float()
            stats[name] = a if name not in stats else torch.maximum(stats[name], a)
        return fn
    for name, mod in model.named_modules():
        if type(mod) is nn.Linear and include(name, mod):
            hooks.append(mod.register_forward_pre_hook(hook(name)))
    try:
        run()
    finally:
        for h in hooks:
            h.remove()
    return {k: v.cpu() for k, v in stats.items()}


def fuse_gelu_epilogue(model: nn.Module) -> int:
    """For nn.Sequential(QLinear, GELU(tanh), ...) apply GELU inside the first
    GEMM's epilogue and replace the GELU with Identity (saves a full read+write
    of the [M, ffn_dim] tensor). Returns the number of fused pairs."""
    count = 0
    for mod in model.modules():
        if isinstance(mod, nn.Sequential):
            items = list(mod._modules.items())
            for (_, a), (kb, b) in zip(items, items[1:]):
                if isinstance(a, QLinear) and isinstance(b, nn.GELU) and b.approximate == 'tanh' and a.act == 'none':
                    a.act = 'gelu_tanh'
                    mod._modules[kb] = nn.Identity()
                    count += 1
    return count


_META_KEY = 'prism_qlinear'


def quantized_state(model: nn.Module) -> Tuple[Dict[str, torch.Tensor], Dict[str, dict]]:
    tensors, meta = {}, {}
    for name, mod in model.named_modules():
        if isinstance(mod, QLinear):
            meta[name] = dict(mode=mod.mode, in_features=mod.in_features, out_features=mod.out_features,
                              rot_block=mod.rot_block, has_mult=mod.act_mult is not None, act_asym=mod.act_asym,
                              bias=mod.bias is not None,
                              bias_dtype=str(mod.bias.dtype).replace('torch.', '') if mod.bias is not None else None)
            for b in ('qweight', 'w_scale', 'bias', 'act_mult', 'w_sum'):
                t = getattr(mod, b)
                if t is not None:
                    tensors['%s.%s' % (name, b)] = t.detach().contiguous().cpu()
    return tensors, meta


def save_quantized(model: nn.Module, path: str):
    """Write every QLinear's buffers + layout metadata to one safetensors file."""
    from safetensors.torch import save_file
    tensors, meta = quantized_state(model)
    save_file(tensors, path, metadata={_META_KEY: json.dumps(meta)})
    return len(meta)


@torch.no_grad()
def load_quantized(model: nn.Module, path: str, device=None, strict: bool = True) -> int:
    """Replace the Linear (or meta/empty) modules named in ``path`` by QLinear and
    load their buffers straight to ``device`` (default: the replaced module's device)."""
    from safetensors import safe_open
    with safe_open(path, framework='pt') as f:
        meta = json.loads(f.metadata()[_META_KEY])
    handles = {}

    def handle(dev):
        key = str(dev) if dev.type == 'cuda' else 'cpu'
        if key not in handles:
            handles[key] = safe_open(path, framework='pt', device=key)
        return handles[key]
    count = 0
    for name, md in meta.items():
        try:
            old = model.get_submodule(name)
        except AttributeError:
            if strict:
                raise
            continue
        dev = device
        if dev is None:
            p = next(iter(old.parameters()), None) if not isinstance(old, QLinear) else old.qweight
            if p is None:
                p = next(iter(old.buffers()), None)
            dev = p.device if p is not None and p.device.type != 'meta' else 'cpu'
        dev = torch.device(dev)
        q = QLinear(md['in_features'], md['out_features'], md['mode'], bias=md['bias'],
                    rot_block=md['rot_block'], has_mult=md['has_mult'], act_asym=md['act_asym'],
                    bias_dtype=getattr(torch, md['bias_dtype']) if md['bias_dtype'] else torch.bfloat16,
                    device='meta')
        q.backend = getattr(old, 'backend', 'auto') if isinstance(old, QLinear) else 'auto'
        f = handle(dev)
        for b in ('qweight', 'w_scale', 'bias', 'act_mult', 'w_sum'):
            if getattr(q, b) is not None:
                q._buffers[b] = f.get_tensor('%s.%s' % (name, b)).to(dev)
        _set_submodule(model, name, q)
        count += 1
    handles.clear()
    return count


# ----------------------------------------------------------------------------
# Text encoder (UMT5-XXL, 5.7B): weight-only INT8
# ----------------------------------------------------------------------------


class QEmbedding(nn.Module):
    """nn.Embedding with per-row symmetric INT8 rows (fp32 scale per token id)."""

    def __init__(self, num_embeddings, embedding_dim, dtype=torch.bfloat16, padding_idx=None, device=None):
        super().__init__()
        self.num_embeddings, self.embedding_dim, self.padding_idx = num_embeddings, embedding_dim, padding_idx
        self.out_dtype = dtype
        self.register_buffer('qweight', torch.empty(num_embeddings, embedding_dim, dtype=torch.int8, device=device))
        self.register_buffer('scale', torch.empty(num_embeddings, dtype=torch.float32, device=device))

    def _apply(self, fn, recurse=True):
        old = self._buffers['scale']
        super()._apply(fn, recurse)
        if self._buffers['scale'].dtype != torch.float32:
            self._buffers['scale'] = old.to(device=self._buffers['scale'].device)
        return self

    @property
    def weight(self):  # codes only (shape / device / dtype inspection)
        return self.qweight

    @classmethod
    @torch.no_grad()
    def from_embedding(cls, emb: nn.Embedding, chunk: int = 16384, quant_device=None) -> 'QEmbedding':
        w = emb.weight
        dev = torch.device(quant_device) if quant_device is not None else (
            w.device if w.device.type == 'cuda' else (torch.device('cuda') if torch.cuda.is_available() else w.device))
        out = cls(emb.num_embeddings, emb.embedding_dim, w.dtype if w.dtype != torch.float32 else torch.bfloat16,
                  emb.padding_idx, device=w.device)
        for i in range(0, w.shape[0], chunk):
            q, s = quantize_weight_int8(w[i:i + chunk].to(dev, torch.float32), 'none')
            out.qweight[i:i + chunk].copy_(q)
            out.scale[i:i + chunk].copy_(s)
        return out

    def forward(self, ids):
        flat = ids.reshape(-1)
        rows = self.qweight.index_select(0, flat).to(torch.float32) * self.scale.index_select(0, flat)[:, None]
        return rows.to(self.out_dtype).view(*ids.shape, self.embedding_dim)


@torch.no_grad()
def quantize_t5(text_encoder: nn.Module, mode: str = 'w8a16_int8', embeddings: bool = True,
                verbose: bool = False) -> Dict[str, str]:
    """Weight-only INT8 for a HF (U)MT5 encoder in place: every attention / FFN
    Linear (q, k, v, o, wi_0, wi_1, wo) -> QLinear(mode), the token embedding ->
    QEmbedding. UMT5-XXL: 11.4 GB -> ~5.8 GB. ``wo`` (kept fp32 by HF) is
    quantized from fp32; its forward then stays in the activation dtype (HF skips
    the fp32 cast when wo.weight is int8). Returns {name: description}."""
    done = quantize_model(text_encoder, mode, lambda n, l: l.in_features % 16 == 0 and l.out_features % 16 == 0,
                          verbose=verbose)
    if embeddings:
        # transformers 4.x: encoder.embed_tokens is text_encoder.shared; 5.x: a separate
        # module (tied weights or not). Quantize each distinct weight once.
        made = {}
        enc = getattr(text_encoder, 'encoder', None)
        for owner, attr, name in ((text_encoder, 'shared', 'shared'), (enc, 'embed_tokens', 'encoder.embed_tokens')):
            emb = getattr(owner, attr, None) if owner is not None else None
            if not isinstance(emb, nn.Embedding):
                continue
            key = emb.weight.data_ptr()
            if key not in made:
                made[key] = QEmbedding.from_embedding(emb)
            setattr(owner, attr, made[key])
            q = made[key]
            done[name] = 'QEmbedding int8 rows %dx%d' % (q.num_embeddings, q.embedding_dim)
    return done


# ----------------------------------------------------------------------------
# torch.library custom ops: QLinear traces under torch.compile (fullgraph) as
# three opaque ops instead of graph-breaking on the Triton launches.
# ----------------------------------------------------------------------------
_OPS_OK = False
try:
    from torch import Tensor
    from typing import List

    @torch.library.custom_op('prism_qlinear::act_quant', mutates_args=())
    def _op_act_quant(x: Tensor, mult: Optional[Tensor], had: Optional[Tensor], out_fp8: bool, rot_block: int,
                      asym: bool) -> List[Tensor]:
        q, s, o = quantize_activation(x, out_fp8, mult, rot_block, asym, had)
        return [q, s, o if o is not None else s.new_empty(0)]

    @_op_act_quant.register_fake
    def _(x, mult, had, out_fp8, rot_block, asym):
        m, k = x.shape
        return [x.new_empty((m, k), dtype=torch.float8_e4m3fn if out_fp8 else torch.int8),
                x.new_empty((m,), dtype=torch.float32), x.new_empty((m if asym else 0,), dtype=torch.float32)]

    @torch.library.custom_op('prism_qlinear::w8a8_gemm', mutates_args=())
    def _op_w8a8_gemm(xq: Tensor, sx: Tensor, ox: Optional[Tensor], wq: Tensor, sw: Tensor, wsum: Optional[Tensor],
                      bias: Optional[Tensor], residual: Optional[Tensor], gate: Optional[Tensor], gate_div: int,
                      act: str, out_dtype: torch.dtype, backend: str, fast_accum: bool) -> Tensor:
        backend = _resolve_backend('w8a8_int8' if xq.dtype == torch.int8 else 'w8a8_fp8', xq.device, backend)
        return w8a8_gemm(xq, sx, wq, sw, bias, ox=ox, wsum=wsum, act=act, out_dtype=out_dtype, backend=backend,
                         fast_accum=fast_accum, residual=residual, gate=gate, gate_div=gate_div)

    @_op_w8a8_gemm.register_fake
    def _(xq, sx, ox, wq, sw, wsum, bias, residual, gate, gate_div, act, out_dtype, backend, fast_accum):
        dt = residual.dtype if residual is not None else out_dtype
        return xq.new_empty((xq.shape[0], wq.shape[0]), dtype=dt)

    @torch.library.custom_op('prism_qlinear::w8a16_gemm', mutates_args=())
    def _op_w8a16_gemm(x: Tensor, wq: Tensor, sw: Tensor, bias: Optional[Tensor], act: str, backend: str) -> Tensor:
        backend = _resolve_backend('w8a16_int8', x.device, backend)
        return w8a16_gemm(x, wq, sw, bias, act=act, backend=backend)

    @_op_w8a16_gemm.register_fake
    def _(x, wq, sw, bias, act, backend):
        return x.new_empty((x.shape[0], wq.shape[0]))

    _OPS_OK = True
except Exception as _e:  # pragma: no cover - old torch without torch.library.custom_op
    warnings.warn('qlinear: custom ops unavailable (%s); torch.compile will graph-break on QLinear' % _e)


def _compiling() -> bool:
    try:
        return torch.compiler.is_compiling()
    except Exception:
        return False


def _forward_compiled(self, x, residual=None, gate=None):
    """QLinear.forward body used while torch.compile traces: pure tensor ops +
    the custom ops above (no side channels, no in-place out=, no Python caches;
    backend resolution and the Hadamard tile happen inside the opaque ops)."""
    lead = x.shape[:-1]
    x2 = x.reshape(-1, self.in_features)
    out_dtype = x.dtype if x.dtype in (torch.bfloat16, torch.float16) else torch.bfloat16
    r2 = residual.reshape(-1, self.out_features) if residual is not None else None
    if self.mode.startswith('w8a16'):
        y = torch.ops.prism_qlinear.w8a16_gemm(x2, self.qweight, self.w_scale, self.bias, self.act,
                                               self.backend or 'auto').to(out_dtype)
        if r2 is not None:
            g2, gdiv = row_table(gate, x2.shape[0], self.out_features) if gate is not None else (None, 1)
            y = _gated_residual(y, r2, g2, gdiv)
        return y.reshape(*lead, self.out_features)
    q, s, o = torch.ops.prism_qlinear.act_quant(x2, self.act_mult, None, self.mode == 'w8a8_fp8', self.rot_block,
                                                self.act_asym)
    wq = self.qweight if self.mode == 'w8a8_int8' else self.qweight.view(torch.float8_e4m3fn)
    g2, gdiv = (row_table(gate, x2.shape[0], self.out_features) if gate is not None else (None, 1))
    y = torch.ops.prism_qlinear.w8a8_gemm(q, s, o if self.act_asym else None, wq, self.w_scale, self.w_sum,
                                          self.bias, r2, g2, gdiv, self.act, out_dtype, self.backend or 'auto',
                                          self.fast_accum)
    return y.reshape(*lead, self.out_features)
