# Prism (MIT, Tencent): FreeVideo addition to the vendored Sage BSA kernel, kept identical
# to the Prism single-GPU research branch file (Prism-fast hymm/fast/sage_bsa_f16acc.py); see NOTICE.
"""FP8 x FP8 -> FP16-accumulate PV ("fp8f16", SageAttention2++) for the Sage BSA kernel.

GeForce Ada / Blackwell (SM89, SM12x) run FP8 MMA with an FP16 accumulator at twice
the rate of FP32 accumulation (RTX 4070: ~233 vs ~117 dense TFLOPS), the rate of the
INT8 Q.K^T MMA. Triton 3.7 lowers ``tl.dot(e4m3, e4m3, out_dtype=tl.float16)`` to
``mma.sync.aligned.m16n8k32.row.col.f16.e4m3.e4m3.f16`` on SM89/SM12x (checked by an
AOT compile, ``f16acc_mma_native``; SM90 gets the equivalent wgmma; Triton 3.3.1
rejects it, and the probe then keeps fp8).

Measured (RTX 4070, triton-windows 3.7.1, real 720p IVPQ lists): the same speed as
fp8 (tuned 129.8 vs 126.7 ms per 2 heads; the Triton kernel runs ~92 TFLOP/s there,
issue/latency bound, not tensor bound), and more accurate: vs the exact bf16 BSA
kernel cos 0.99987 / relL2 0.0164 (= H200 fp8) where fp8 gives 0.99940 / 0.0415 on
that card (Ada's FP8 MMA FP32 accumulator). Switch: PRISM_SAGE_F16ACC (auto_enabled).

Numerics (Sage2++ style): P is produced pre-scaled by 224 (log2 folded into the exp2
argument) and V is quantized per (b, h, channel) to |v| <= 3.5, so one 64-key tile's
partial sum is bounded by 64 * 224 * 3.5 = 50176 < 65504 (fp16 max). Each k tile gets a
fresh fp16 accumulator that is flushed (converted and added) into the fp32 running
accumulator after the tile, so fp16 rounding is applied once per tile (~2^-11 of the
tile's partial) and never across tiles. Sage2++ uses 224 / 4.5; 3.5 = 448 / 2^7 and
224 = 448 / 2 instead keep both e4m3 rounding grids exactly those of the fp8 mode
(power-of-two rescale), so fp8f16 differs from fp8 only by the fp16 accumulation and
the e4m3 subnormal range (|v| < amax / 224, P < 7e-5 of the row max). Q/K quantization, smoothing, masking, row maps
and outputs are identical to sage_bsa's fp8 mode; the attention kernel takes the same
arguments as ``sage_bsa._sage_bsa_attn_kernel`` (unused constexprs are accepted).

Loop variants (tuner candidates, same knobs as sage_bsa): per-tile loop (default),
FAST_CVT (magic int->float, integer row max), KPAIR=1 (two tiles per step with a shared
running max: two fp16 dots, one merge), QK_PREFETCH (next tile's QK issued early).
K_PER_TOKEN, KPAIR=2, USE_TMA and WARP_SPEC are not implemented here.

Entry points: ``sage_bsa_forward(..)`` (generic, like sage_bsa.sage_bsa_forward with
pv='fp8f16'), ``prep_sage`` / ``launch_sage`` (the fused IVPQ path, like
ivpq_fast._prep_sage / _launch_sage), and ``install()``, which routes pv='fp8f16'
(and 'auto' when enabled, see ``auto_enabled``) through this module without editing
sage_bsa.py / ivpq_fast.py.
"""
from __future__ import annotations

import os
from typing import Optional

import torch
import triton
import triton.language as tl

from . import sage_bsa as sb

PV = "fp8f16"
P_SCALE = 224.0
LOG2_P_SCALE = 7.807354922057604      # log2(224)
V_MAX = 3.5                           # 448 / 2^7: same e4m3 grid as sage_bsa's fp8 V
_LOG2E = 1.4426950408889634


# =====================================================================
# Quantization pre-pass kernels: copies of sage_bsa's (Prism-fast 0befcb7) so this
# module does not depend on their signatures; V is always stored e4m3 transposed.
# =====================================================================
@triton.jit
def _kv_stats_kernel(
    K, V, KVM, SRCU, KSUM, VMAX,
    stride_kz, stride_kh, stride_kn,
    stride_vz, stride_vh, stride_vn,
    H, N_K, N_CHUNKS,
    CHUNK: tl.constexpr, BLOCK: tl.constexpr, HEAD_DIM: tl.constexpr,
    HAS_KV_MASK: tl.constexpr, NEED_KSUM: tl.constexpr, NEED_VMAX: tl.constexpr,
    GATHER: tl.constexpr = False,
):
    # GATHER: logical row r reads input token SRCU[r] (-1 = padding -> zeros), so
    # K/V can be read straight from the THW-order [B,S,H,D] projections.
    pid = tl.program_id(0)
    off_hz = tl.program_id(1)
    off_z = off_hz // H
    off_h = off_hz % H
    offs_d = tl.arange(0, HEAD_DIM)
    k_base = K + off_z.to(tl.int64) * stride_kz + off_h.to(tl.int64) * stride_kh
    v_base = V + off_z.to(tl.int64) * stride_vz + off_h.to(tl.int64) * stride_vh
    ksum = tl.zeros([HEAD_DIM], dtype=tl.float32)
    vmax = tl.zeros([HEAD_DIM], dtype=tl.float32)
    for it in range(0, CHUNK // BLOCK):
        offs_n = pid * CHUNK + it * BLOCK + tl.arange(0, BLOCK)
        valid = offs_n < N_K
        if HAS_KV_MASK:
            valid = valid & (tl.load(KVM + offs_n, mask=offs_n < N_K, other=0) != 0)
        if GATHER:
            rows = tl.load(SRCU + offs_n, mask=offs_n < N_K, other=-1)
            valid = valid & (rows >= 0)
            rows = tl.where(valid, rows, 0).to(tl.int64)
        else:
            rows = offs_n
        if NEED_KSUM:
            k = tl.load(k_base + rows[:, None] * stride_kn + offs_d[None, :],
                        mask=valid[:, None], other=0.0).to(tl.float32)
            ksum += tl.sum(k, axis=0)
        if NEED_VMAX:
            v = tl.load(v_base + rows[:, None] * stride_vn + offs_d[None, :],
                        mask=valid[:, None], other=0.0).to(tl.float32)
            vmax = tl.maximum(vmax, tl.max(tl.abs(v), axis=0))
    out = (off_hz * N_CHUNKS + pid).to(tl.int64) * HEAD_DIM
    if NEED_KSUM:
        tl.store(KSUM + out + offs_d, ksum)
    if NEED_VMAX:
        tl.store(VMAX + out + offs_d, vmax)


@triton.jit
def _quant_kernel(
    Q, K, V, KVM, SRCU, KMEAN, VINV,
    Q8, QS, K8, KS, VO, QKM,
    stride_qz, stride_qh, stride_qm,
    stride_kz, stride_kh, stride_kn,
    stride_vz, stride_vh, stride_vn,
    stride_voz, stride_voh, stride_von, stride_vod,
    H, N_Q, N_K,
    qk_mult, sm_scale,
    TQ: tl.constexpr, TK: tl.constexpr, HEAD_DIM: tl.constexpr,
    Q_PER_TOKEN: tl.constexpr, K_PER_TOKEN: tl.constexpr,
    HAS_KV_MASK: tl.constexpr, SMOOTH_K: tl.constexpr,
    STORE_QKM: tl.constexpr, GATHER: tl.constexpr = False,
):
    pid = tl.program_id(0)
    off_hz = tl.program_id(1)
    off_z = off_hz // H
    off_h = off_hz % H
    offs_d = tl.arange(0, HEAD_DIM)
    n_qt = N_Q // TQ
    n_kt = N_K // TK

    if pid < n_qt:
        offs_m = pid * TQ + tl.arange(0, TQ)
        q_base = Q + off_z.to(tl.int64) * stride_qz + off_h.to(tl.int64) * stride_qh
        if GATHER:
            uq = tl.load(SRCU + offs_m)
            q = tl.load(q_base + tl.where(uq >= 0, uq, 0).to(tl.int64)[:, None] * stride_qm + offs_d[None, :],
                        mask=(uq >= 0)[:, None], other=0.0).to(tl.float32)
        else:
            q = tl.load(q_base + offs_m[:, None] * stride_qm + offs_d[None, :]).to(tl.float32)
        if Q_PER_TOKEN:
            amax = tl.maximum(tl.max(tl.abs(q), axis=1), 1e-12)
            qi = q * (127.0 / amax)[:, None]
            tl.store(QS + off_hz.to(tl.int64) * N_Q + offs_m, amax * (qk_mult / 127.0))
        else:
            amax = tl.maximum(tl.max(tl.abs(q)), 1e-12)
            qi = q * (127.0 / amax)
            tl.store(QS + off_hz.to(tl.int64) * n_qt + pid, amax * (qk_mult / 127.0))
        if STORE_QKM:
            # smoothing shifts every score of a row by -q.k_mean; the softmax output
            # is unchanged but the lse needs it added back.
            km_q = tl.load(KMEAN + off_hz * HEAD_DIM + offs_d)
            tl.store(QKM + off_hz.to(tl.int64) * N_Q + offs_m, tl.sum(q * km_q[None, :], axis=1) * sm_scale)
        qi = qi + tl.where(qi >= 0, 0.5, -0.5)
        tl.store(Q8 + off_hz.to(tl.int64) * N_Q * HEAD_DIM + offs_m[:, None] * HEAD_DIM + offs_d[None, :],
                 qi.to(tl.int8))

    if pid < n_kt:
        offs_n = pid * TK + tl.arange(0, TK)
        k_base = K + off_z.to(tl.int64) * stride_kz + off_h.to(tl.int64) * stride_kh
        v_base = V + off_z.to(tl.int64) * stride_vz + off_h.to(tl.int64) * stride_vh
        if GATHER:
            uk = tl.load(SRCU + offs_n)
            ok = (uk >= 0)[:, None]
            rk = tl.where(uk >= 0, uk, 0).to(tl.int64)
            k = tl.load(k_base + rk[:, None] * stride_kn + offs_d[None, :], mask=ok, other=0.0).to(tl.float32)
            v = tl.load(v_base + rk[:, None] * stride_vn + offs_d[None, :], mask=ok, other=0.0).to(tl.float32)
        else:
            k = tl.load(k_base + offs_n[:, None] * stride_kn + offs_d[None, :]).to(tl.float32)
            v = tl.load(v_base + offs_n[:, None] * stride_vn + offs_d[None, :]).to(tl.float32)
        if SMOOTH_K:
            km = tl.load(KMEAN + off_hz * HEAD_DIM + offs_d)
            k = k - km[None, :]
        if HAS_KV_MASK:
            valid = tl.load(KVM + offs_n) != 0
            # padded keys are masked to -inf in the attention kernel; zero them so
            # they do not inflate the tile scale (and cannot inject inf/nan via V).
            k = tl.where(valid[:, None], k, 0.0)
            v = tl.where(valid[:, None], v, 0.0)
        if K_PER_TOKEN:
            amax = tl.maximum(tl.max(tl.abs(k), axis=1), 1e-12)
            ki = k * (127.0 / amax)[:, None]
            tl.store(KS + off_hz.to(tl.int64) * N_K + offs_n, amax / 127.0)
        else:
            amax = tl.maximum(tl.max(tl.abs(k)), 1e-12)
            ki = k * (127.0 / amax)
            tl.store(KS + off_hz.to(tl.int64) * n_kt + pid, amax / 127.0)
        ki = ki + tl.where(ki >= 0, 0.5, -0.5)
        tl.store(K8 + off_hz.to(tl.int64) * N_K * HEAD_DIM + offs_n[:, None] * HEAD_DIM + offs_d[None, :],
                 ki.to(tl.int8))

        vinv = tl.load(VINV + off_hz * HEAD_DIM + offs_d)
        v = v * vinv[None, :]
        vo_base = VO + off_z.to(tl.int64) * stride_voz + off_h.to(tl.int64) * stride_voh
        # transposed [D, TK] e4m3 store: tokens contiguous (K-major PV operand)
        vt = tl.trans(v).to(tl.float8e4nv)
        tl.store(vo_base + offs_d[:, None] * stride_vod + offs_n[None, :] * stride_von, vt)



# =====================================================================
# Kernel
# =====================================================================
@triton.jit
def _f16_step(s_i32, kid, acc, l_i, m_i, qs, ks_base, KVM, v_base, stride_vn, stride_vd, offs_n, offs_d,
              BLOCK_N: tl.constexpr, HAS_KV_MASK: tl.constexpr, FAST_CVT: tl.constexpr):
    """One k tile: dequant + mask + online softmax + fp8 PV with a fresh fp16 accumulator."""
    start_n = tl.multiple_of(kid * BLOCK_N, BLOCK_N)
    sc = qs * tl.load(ks_base + kid)
    if HAS_KV_MASK:
        k_valid = tl.load(KVM + start_n + offs_n) != 0
    if FAST_CVT:
        if HAS_KV_MASK:
            s_i32 = tl.where(k_valid[None, :], s_i32, -(1 << 30))
        rmax = tl.max(s_i32, 1)
        m_t = rmax.to(tl.float32) * sc
        if HAS_KV_MASK:
            m_t = tl.where(rmax == -(1 << 30), float("-inf"), m_t)
        s = (s_i32 + 0x4B400000).to(tl.float32, bitcast=True) - 12582912.0
    else:
        s = s_i32.to(tl.float32)
        m_t = tl.max(s, 1) * sc
    if HAS_KV_MASK:
        s = tl.where(k_valid[None, :], s, float("-inf"))
        if not FAST_CVT:
            m_t = tl.max(s, 1) * sc
    m_ij = tl.maximum(m_i, m_t)
    if HAS_KV_MASK:
        m_ninf = m_ij == float("-inf")
        alpha = tl.where(m_ninf, 1.0, tl.math.exp2(m_i - m_ij))
        m_sub = tl.where(m_ninf, 0.0, m_ij)
    else:
        alpha = tl.math.exp2(m_i - m_ij)
        m_sub = m_ij
    # P pre-scaled by 224 = 2^7.807 (folded into the exp2 argument)
    p = tl.math.exp2(s * sc[:, None] - (m_sub - 7.807354922057604)[:, None])
    l_i = l_i * alpha + tl.sum(p, 1) * (1.0 / 224.0)
    v = tl.load(v_base + (start_n + offs_n)[:, None] * stride_vn + offs_d[None, :] * stride_vd)
    d = tl.dot(p.to(tl.float8e4nv), v, out_dtype=tl.float16)
    acc = acc * alpha[:, None] + d.to(tl.float32)
    return acc, l_i, m_ij


@triton.jit
def _f16_pair(q, kid0, kid1, TWO: tl.constexpr, acc, l_i, m_i, qs, k_base, ks_base, COLB, v_base,
              stride_vn, stride_vd, offs_n, offs_d, BLOCK_N: tl.constexpr, HEAD_DIM: tl.constexpr,
              HAS_KV_MASK: tl.constexpr):
    """One or two k tiles with a shared running max (sage_bsa._sage_pair): each tile's
    P.V runs in its own fp16 accumulator (64-key overflow bound), both are flushed to
    fp32 and merged into acc once. Padded keys masked by the COLB column bias."""
    s0 = tl.multiple_of(kid0 * BLOCK_N, BLOCK_N)
    k0 = tl.load(k_base + (s0 + offs_n)[None, :] * HEAD_DIM + offs_d[:, None])
    x0 = (tl.dot(q, k0, out_dtype=tl.int32) + 0x4B400000).to(tl.float32, bitcast=True)
    if HAS_KV_MASK:
        x0 = x0 - tl.load(COLB + s0 + offs_n)[None, :]
    else:
        x0 = x0 - 12582912.0
    sc0 = qs * tl.load(ks_base + kid0)
    m_t = tl.max(x0, 1) * sc0
    if TWO:
        s1 = tl.multiple_of(kid1 * BLOCK_N, BLOCK_N)
        k1 = tl.load(k_base + (s1 + offs_n)[None, :] * HEAD_DIM + offs_d[:, None])
        x1 = (tl.dot(q, k1, out_dtype=tl.int32) + 0x4B400000).to(tl.float32, bitcast=True)
        if HAS_KV_MASK:
            x1 = x1 - tl.load(COLB + s1 + offs_n)[None, :]
        else:
            x1 = x1 - 12582912.0
        sc1 = qs * tl.load(ks_base + kid1)
        m_t = tl.maximum(m_t, tl.max(x1, 1) * sc1)
    m_ij = tl.maximum(m_i, m_t)
    if HAS_KV_MASK:
        m_ninf = m_ij == float("-inf")
        alpha = tl.where(m_ninf, 1.0, tl.math.exp2(m_i - m_ij))
        m_sub = tl.where(m_ninf, 0.0, m_ij)
    else:
        alpha = tl.math.exp2(m_i - m_ij)
        m_sub = m_ij
    m_sub = m_sub - 7.807354922057604
    p0 = tl.math.exp2(x0 * sc0[:, None] - m_sub[:, None])
    v0 = tl.load(v_base + (s0 + offs_n)[:, None] * stride_vn + offs_d[None, :] * stride_vd)
    d = tl.dot(p0.to(tl.float8e4nv), v0, out_dtype=tl.float16).to(tl.float32)
    rs = tl.sum(p0, 1)
    if TWO:
        p1 = tl.math.exp2(x1 * sc1[:, None] - m_sub[:, None])
        v1 = tl.load(v_base + (s1 + offs_n)[:, None] * stride_vn + offs_d[None, :] * stride_vd)
        d = d + tl.dot(p1.to(tl.float8e4nv), v1, out_dtype=tl.float16).to(tl.float32)
        rs += tl.sum(p1, 1)
    l_i = l_i * alpha + rs * (1.0 / 224.0)
    acc = acc * alpha[:, None] + d
    return acc, l_i, m_ij


@triton.jit
def _attn_kernel(
    Q8, K8, V, QS, KS, VS, Out, LSE, QKM,
    BI, BL, PAIR, KVM, RMAP, TSKIP, SRCU, COLB, CINIT,
    stride_oz, stride_oh, stride_om,
    stride_vz, stride_vh, stride_vn, stride_vd,
    stride_bz, stride_bh, stride_bm, stride_bs,
    stride_lz, stride_lh, stride_lm,
    H, N_Q, N_K, N_PAIRS,
    HEAD_DIM: tl.constexpr,
    TQ: tl.constexpr,
    BLOCK_M: tl.constexpr,
    BLOCK_N: tl.constexpr,
    MODE: tl.constexpr,
    PV_MODE: tl.constexpr,
    Q_PER_TOKEN: tl.constexpr, K_PER_TOKEN: tl.constexpr,
    HAS_KV_MASK: tl.constexpr, STORE_LSE: tl.constexpr, LSE_SHIFT: tl.constexpr,
    ROWMAP: tl.constexpr = False,
    SHARED_FLAGS: tl.constexpr = False,
    SCATTER: tl.constexpr = False,
    QK_PREFETCH: tl.constexpr = False,
    ACC_DIRECT: tl.constexpr = False,     # accepted, unused (fp16 accumulators are always flushed)
    FAST_CVT: tl.constexpr = False,
    KPAIR: tl.constexpr = False,          # 1 only (2 is treated as 1)
    ONES_SUM: tl.constexpr = False,       # accepted, unused
    USE_TMA: tl.constexpr = False,        # accepted, unused
    WARP_SPEC: tl.constexpr = False,      # accepted, unused
    UNROLL: tl.constexpr = 1,
):
    """sage_bsa._sage_bsa_attn_kernel with fp8 x fp8 -> fp16-accumulate PV (per-tile
    flush to fp32). Same arguments and modes; V must come from quantize_qkv() below
    (transposed [B,H,D,S] e4m3, |v| <= 3.5) and VS = v_scale / 224."""
    tl.static_assert(not K_PER_TOKEN, "fp8f16: per-token K scales are not implemented")
    pid = tl.program_id(0)
    off_hz = tl.program_id(1)
    off_z = off_hz // H
    off_h = off_hz % H

    if MODE == 1:
        if SHARED_FLAGS:
            flag = tl.load(PAIR + pid)
        else:
            flag = tl.load(PAIR + off_hz.to(tl.int64) * N_PAIRS + pid)
        if flag == 0:
            return
        tile = 2 * pid
    elif MODE == 2:
        if SHARED_FLAGS:
            flag = tl.load(TSKIP + pid)
        else:
            pair = pid // 2
            flag = tl.load(PAIR + off_hz.to(tl.int64) * N_PAIRS + pair, mask=pair < N_PAIRS, other=0)
        if flag != 0:
            return
        tile = pid
    else:
        if SHARED_FLAGS:
            if tl.load(TSKIP + pid) != 0:
                return
        tile = pid
    if ROWMAP:
        row = tl.load(RMAP + tile)
    else:
        row = tile

    offs_m = pid * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_n = tl.arange(0, BLOCK_N)
    offs_d = tl.arange(0, HEAD_DIM)

    q = tl.load(Q8 + off_hz.to(tl.int64) * N_Q * HEAD_DIM + offs_m[:, None] * HEAD_DIM + offs_d[None, :])
    if Q_PER_TOKEN:
        qs = tl.load(QS + off_hz.to(tl.int64) * N_Q + offs_m)
    else:
        qs = tl.load(QS + off_hz.to(tl.int64) * (N_Q // TQ) + offs_m // TQ)

    bi_ptr = BI + off_z.to(tl.int64) * stride_bz + off_h.to(tl.int64) * stride_bh + row.to(tl.int64) * stride_bm
    n_sel = tl.load(BL + off_z.to(tl.int64) * stride_lz + off_h.to(tl.int64) * stride_lh + row * stride_lm)

    k_base = K8 + off_hz.to(tl.int64) * N_K * HEAD_DIM
    v_base = V + off_z.to(tl.int64) * stride_vz + off_h.to(tl.int64) * stride_vh
    ks_base = KS + off_hz.to(tl.int64) * (N_K // BLOCK_N)

    m_i = tl.zeros([BLOCK_M], dtype=tl.float32) - float("inf")
    l_i = tl.zeros([BLOCK_M], dtype=tl.float32) + 1.0
    acc = tl.zeros([BLOCK_M, HEAD_DIM], dtype=tl.float32)

    if KPAIR:
        n_pair = n_sel // 2
        for ip in tl.range(0, n_pair, loop_unroll_factor=UNROLL):
            kid0 = tl.load(bi_ptr + (2 * ip) * stride_bs).to(tl.int32)
            kid1 = tl.load(bi_ptr + (2 * ip + 1) * stride_bs).to(tl.int32)
            acc, l_i, m_i = _f16_pair(q, kid0, kid1, True, acc, l_i, m_i, qs, k_base, ks_base, COLB, v_base,
                                      stride_vn, stride_vd, offs_n, offs_d, BLOCK_N, HEAD_DIM, HAS_KV_MASK)
        if n_sel % 2 == 1:
            kid0 = tl.load(bi_ptr + (n_sel - 1) * stride_bs).to(tl.int32)
            acc, l_i, m_i = _f16_pair(q, kid0, kid0, False, acc, l_i, m_i, qs, k_base, ks_base, COLB, v_base,
                                      stride_vn, stride_vd, offs_n, offs_d, BLOCK_N, HEAD_DIM, HAS_KV_MASK)
    elif QK_PREFETCH:
        kid = tl.load(bi_ptr, mask=n_sel > 0, other=0).to(tl.int32)
        start_n = tl.multiple_of(kid * BLOCK_N, BLOCK_N)
        kT = tl.load(k_base + (start_n + offs_n)[None, :] * HEAD_DIM + offs_d[:, None], mask=n_sel > 0, other=0)
        s_cur = tl.dot(q, kT, out_dtype=tl.int32)
        for i in range(0, n_sel):
            has_next = i + 1 < n_sel
            kid_n = tl.load(bi_ptr + (i + 1) * stride_bs, mask=has_next, other=0).to(tl.int32)
            start_nn = tl.multiple_of(kid_n * BLOCK_N, BLOCK_N)
            kT_n = tl.load(k_base + (start_nn + offs_n)[None, :] * HEAD_DIM + offs_d[:, None], mask=has_next,
                           other=0)
            s_next = tl.dot(q, kT_n, out_dtype=tl.int32)
            acc, l_i, m_i = _f16_step(s_cur, kid, acc, l_i, m_i, qs, ks_base, KVM, v_base, stride_vn, stride_vd,
                                      offs_n, offs_d, BLOCK_N, HAS_KV_MASK, FAST_CVT)
            s_cur = s_next
            kid = kid_n
    else:
        for i in range(0, n_sel):
            kid = tl.load(bi_ptr + i * stride_bs).to(tl.int32)
            start_n = tl.multiple_of(kid * BLOCK_N, BLOCK_N)
            kT = tl.load(k_base + (start_n + offs_n)[None, :] * HEAD_DIM + offs_d[:, None])
            s = tl.dot(q, kT, out_dtype=tl.int32)
            acc, l_i, m_i = _f16_step(s, kid, acc, l_i, m_i, qs, ks_base, KVM, v_base, stride_vn, stride_vd,
                                      offs_n, offs_d, BLOCK_N, HAS_KV_MASK, FAST_CVT)

    vs = tl.load(VS + off_hz * HEAD_DIM + offs_d)
    o = acc * vs[None, :] * (1.0 / l_i)[:, None]
    o_base = Out + off_z.to(tl.int64) * stride_oz + off_h.to(tl.int64) * stride_oh
    if SCATTER:
        dst = tl.load(SRCU + offs_m)
        tl.store(o_base + tl.where(dst >= 0, dst, 0).to(tl.int64)[:, None] * stride_om + offs_d[None, :],
                 o.to(Out.type.element_ty), mask=(dst >= 0)[:, None])
    else:
        tl.store(o_base + offs_m[:, None] * stride_om + offs_d[None, :], o.to(Out.type.element_ty))
    if STORE_LSE:
        lse = (m_i + tl.math.log2(l_i)) * 0.6931471805599453
        if LSE_SHIFT:
            lse += tl.load(QKM + off_hz.to(tl.int64) * N_Q + offs_m)
        tl.store(LSE + off_hz.to(tl.int64) * N_Q + offs_m, lse)


# =====================================================================
# Capability / selection
# =====================================================================
@triton.jit
def _probe_kernel(A, B, C):
    offs = tl.arange(0, 64)
    a = tl.load(A + offs[:, None] * 64 + offs[None, :])
    b = tl.load(B + offs[:, None] + offs[None, :] * 64)
    tl.store(C + offs[:, None] * 64 + offs[None, :], tl.dot(a, b, out_dtype=tl.float16))


_NATIVE = {}


def f16acc_mma_native(device=None) -> bool:
    """True if this Triton lowers an e4m3 dot with an fp16 accumulator to a native FP8
    MMA with fp16 accumulation for the device (AOT compile, no launch)."""
    cap = sb.device_arch(device)
    if cap < (8, 9):
        return False
    if cap not in _NATIVE:
        try:
            from triton.backends.compiler import GPUTarget
            sig = {"A": "*fp8e4nv", "B": "*fp8e4nv", "C": "*fp16"}
            attrs = {(i,): [["tt.divisibility", 16]] for i in range(3)}
            src = triton.compiler.ASTSource(fn=_probe_kernel, signature=sig, constexprs={}, attrs=attrs)
            cc = triton.compile(src, target=GPUTarget("cuda", cap[0] * 10 + cap[1], 32), options={"num_warps": 4})
            ptx = cc.asm["ptx"]
            _NATIVE[cap] = ("f16.e4m3.e4m3.f16" in ptx) or ("f16.e4m3.e4m3" in ptx and "wgmma" in ptx)
        except Exception as e:  # noqa: BLE001
            sb._warn_once(f"fp8f16 probe failed ({type(e).__name__}: {str(e)[:120]}); not using fp8f16")
            _NATIVE[cap] = False
    return _NATIVE[cap]


def supported(device=None) -> bool:
    return sb.device_arch(device) >= (8, 9) and f16acc_mma_native(device)


_TRITON_VERSION = tuple(int(x) for x in triton.__version__.split(".")[:2])


def auto_enabled(device=None) -> bool:
    """Whether pv='auto' resolves to 'fp8f16' (only consulted once install() ran).

    PRISM_SAGE_F16ACC 0: off. Unset means auto (the default).
    1: on for every supported GPU (SM89+, incl. SM90 via wgmma: for A/B on H200).
    auto: on for GeForce-class SM89 / SM12x with Triton >= 3.7 (validated: RTX 4070,
    triton-windows 3.7.1) -- the intended default. There fp8f16 runs at the speed of
    fp8 (the kernel is not tensor-bound) but is more accurate: Ada's FP8 MMA with an
    FP32 accumulator lost accuracy on real 720p q/k/v (relL2 vs the exact kernel 0.042
    on an RTX 4070 against 0.016 on H200); fp8f16 gives 0.016 there. Not SM90/SM100 in
    'auto' (same MMA rate; SM90 fp8 has its own KPAIR+TMA kernel)."""
    env = os.environ.get("PRISM_SAGE_F16ACC", "auto").strip().lower() or "auto"
    if env in ("0", "off", "false", "no") or not supported(device):
        return False
    if env == "auto":
        cap = sb.device_arch(device)
        return (cap == (8, 9) or cap[0] == 12) and _TRITON_VERSION >= (3, 7)
    return True


# =====================================================================
# Quantization (sage_bsa's kernels, V scaled for fp16 accumulation)
# =====================================================================
def quantize_qkv(q, k, v, sm_scale, kv_valid_mask, tile_q=64, tile_k=64, q_per_token=True,
                 k_per_token=False, smooth_k=True, want_qkm=False, src_map=None, k_mean=None):
    """sage_bsa.quantize_qkv(pv='fp8') with V quantized to |v| <= 3.5 (per channel) and
    v_scale_eff = v_scale / 224 (the P pre-scale). Same return tuple."""
    assert not k_per_token, "fp8f16: per-token K scales are not implemented"
    B, H, Sq, D = q.shape
    Sk = k.shape[2]
    gather = src_map is not None
    if gather:
        Sq = Sk = src_map.shape[0]
        srcu = src_map
    else:
        srcu = torch.empty(1, dtype=torch.int32, device=q.device)
    dev = q.device
    BH = B * H
    assert q.stride(-1) == 1 and k.stride(-1) == 1 and v.stride(-1) == 1
    assert Sq % tile_q == 0 and Sk % tile_k == 0
    n_qt, n_kt = Sq // tile_q, Sk // tile_k
    has_mask = kv_valid_mask is not None
    if has_mask:
        kvm = kv_valid_mask.contiguous()
        if kvm.dtype == torch.bool:
            kvm = kvm.view(torch.uint8)
    else:
        kvm = torch.empty(1, dtype=torch.uint8, device=dev)

    need_ksum = smooth_k and k_mean is None
    CHUNK = 4096 if need_ksum else 1024
    n_chunks = triton.cdiv(Sk, CHUNK)
    ksum = torch.empty((BH, n_chunks, D), dtype=torch.float32, device=dev) if need_ksum else \
        torch.empty(1, dtype=torch.float32, device=dev)
    vmax = torch.empty((BH, n_chunks, D), dtype=torch.float32, device=dev)
    _kv_stats_kernel[(n_chunks, BH)](
        k, v, kvm, srcu, ksum, vmax,
        k.stride(0), k.stride(1), k.stride(2),
        v.stride(0), v.stride(1), v.stride(2),
        H, Sk, n_chunks,
        CHUNK=CHUNK, BLOCK=64, HEAD_DIM=D,
        HAS_KV_MASK=has_mask, NEED_KSUM=need_ksum, NEED_VMAX=True, GATHER=gather,
        num_warps=4,
    )
    if smooth_k and k_mean is not None:
        kmean = k_mean.reshape(BH, D).to(torch.float32).contiguous()
    elif smooth_k:
        cnt = (kvm != 0).sum().clamp(min=1).to(torch.float32) if has_mask else torch.tensor(float(Sk), device=dev)
        kmean = (ksum.sum(dim=1) / cnt).contiguous()
    else:
        kmean = torch.empty(1, dtype=torch.float32, device=dev)
    vamax = vmax.amax(dim=1)
    v_scale = torch.where(vamax > 0, vamax / V_MAX, torch.ones_like(vamax))
    v_scale_eff = (v_scale / P_SCALE).contiguous()
    v_inv = (1.0 / v_scale).contiguous()

    q8 = torch.empty((B, H, Sq, D), dtype=torch.int8, device=dev)
    k8 = torch.empty((B, H, Sk, D), dtype=torch.int8, device=dev)
    qs = torch.empty((BH, Sq if q_per_token else n_qt), dtype=torch.float32, device=dev)
    ks = torch.empty((BH, n_kt), dtype=torch.float32, device=dev)
    vq = torch.empty((B, H, D, Sk), dtype=torch.float8_e4m3fn, device=dev)
    store_qkm = bool(want_qkm and smooth_k)
    qkm = torch.empty((BH, Sq) if store_qkm else (1,), dtype=torch.float32, device=dev)
    _quant_kernel[(max(n_qt, n_kt), BH)](
        q, k, v, kvm, srcu, kmean, v_inv,
        q8, qs, k8, ks, vq, qkm,
        q.stride(0), q.stride(1), q.stride(2),
        k.stride(0), k.stride(1), k.stride(2),
        v.stride(0), v.stride(1), v.stride(2),
        vq.stride(0), vq.stride(1), 1, vq.stride(2),
        H, Sq, Sk,
        float(sm_scale) * _LOG2E, float(sm_scale),
        TQ=tile_q, TK=tile_k, HEAD_DIM=D,
        Q_PER_TOKEN=q_per_token, K_PER_TOKEN=False,
        HAS_KV_MASK=has_mask, SMOOTH_K=smooth_k, STORE_QKM=store_qkm, GATHER=gather,
        num_warps=4,
    )
    return q8, qs, k8, ks, vq, v_scale_eff, kvm, has_mask, qkm


# =====================================================================
# Generic forward (sage_bsa.sage_bsa_forward with pv='fp8f16')
# =====================================================================
def _attend_prequant(qz, block_indices, block_indices_lens, o, chunk_size_q=64, chunk_size_k=64,
                     do_merge=True, pair_flags=None, q_per_token=True, smooth_k=True, return_lse=False):
    q8, qs, k8, ks, vq, vs, kvm, has_mask, qkm = qz
    B, H, Sq, D = q8.shape
    Sk = k8.shape[2]
    dev = q8.device
    lse = torch.empty((B, H, Sq), dtype=torch.float32, device=dev) if return_lse else \
        torch.empty(1, dtype=torch.float32, device=dev)
    n_qt = Sq // chunk_size_q
    do_merge = do_merge and chunk_size_q == 64 and n_qt >= 2
    if do_merge:
        if pair_flags is None:
            pair, n_pairs = sb.pair_same_flags(block_indices, block_indices_lens, H)
        else:
            pair = pair_flags.reshape(B * H, -1).contiguous()
            n_pairs = pair.shape[1]
    else:
        pair, n_pairs = torch.zeros(1, dtype=torch.int8, device=dev), 0
    colb = sb._colbias(kvm, has_mask, Sk, dev)
    plan = sb.launch_plan(dev, PV, Sq, False)
    common = (
        q8, k8, vq, qs, ks, vs, o, lse, qkm,
        block_indices, block_indices_lens, pair, kvm, pair, pair, pair, colb, colb,
        o.stride(0), o.stride(1), o.stride(2),
        vq.stride(0), vq.stride(1), 1, vq.stride(2),
        block_indices.stride(0), block_indices.stride(1), block_indices.stride(2), block_indices.stride(3),
        block_indices_lens.stride(0), block_indices_lens.stride(1), block_indices_lens.stride(2),
        H, Sq, Sk, n_pairs,
    )
    kw = dict(HEAD_DIM=D, TQ=chunk_size_q, BLOCK_N=chunk_size_k, PV_MODE=2,
              Q_PER_TOKEN=q_per_token, K_PER_TOKEN=False,
              HAS_KV_MASK=has_mask, STORE_LSE=return_lse, LSE_SHIFT=bool(return_lse and smooth_k),
              **_kernel_opts(plan["opts"]))
    if do_merge:
        w, s = plan["cfg128"]
        _attn_kernel[(n_pairs, B * H)](*common, BLOCK_M=128, MODE=1, num_warps=w, num_stages=s, **kw)
        w, s = plan["cfg64"]
        _attn_kernel[(n_qt, B * H)](*common, BLOCK_M=64, MODE=2, num_warps=w, num_stages=s, **kw)
    else:
        w, s = plan["cfg64"] if chunk_size_q <= 64 else plan["cfg128"]
        _attn_kernel[(n_qt, B * H)](*common, BLOCK_M=chunk_size_q, MODE=0, num_warps=w, num_stages=s, **kw)
    return lse if return_lse else None


def _kernel_opts(opts):
    """Plan opts this kernel implements (KPAIR 2 -> 1; TMA / warp specialization dropped)."""
    out = {k: v for k, v in opts.items() if k in ("KPAIR", "FAST_CVT", "QK_PREFETCH", "UNROLL")}
    if out.get("KPAIR"):
        out["KPAIR"] = 1
    return out


def sage_bsa_forward(q_re, k_re, v_re, sm_scale, block_indices, block_indices_lens, kv_valid_mask=None,
                     pv: str = PV, *, chunk_size_q=64, chunk_size_k=64, merge=True, pair_flags=None,
                     q_per_token=True, k_per_token=False, smooth_k=True, head_chunk: Optional[int] = 8,
                     return_lse=False):
    """sage_bsa.sage_bsa_forward(..., pv='fp8f16')."""
    B, H, Sq, D = q_re.shape
    Sk = k_re.shape[2]
    assert D in (64, 128) and chunk_size_q in (64, 128) and chunk_size_k in (64, 128)
    assert Sq % chunk_size_q == 0 and Sk % chunk_size_k == 0 and not k_per_token
    q_re, k_re, v_re = (x if x.stride(-1) == 1 else x.contiguous() for x in (q_re, k_re, v_re))
    if block_indices.stride(-1) != 1:
        block_indices = block_indices.contiguous()
    if block_indices_lens.stride(-1) != 1:
        block_indices_lens = block_indices_lens.contiguous()
    n_qt = Sq // chunk_size_q
    do_merge = merge and chunk_size_q == 64 and n_qt >= 2
    if do_merge and pair_flags is not None:
        pair_flags = pair_flags.to(torch.int8)
        if pair_flags.dim() == 1:
            pair_flags = pair_flags.view(1, 1, -1).expand(B, H, -1)
    o = torch.empty_like(q_re)
    lse = torch.empty((B, H, Sq), dtype=torch.float32, device=q_re.device) if return_lse else None
    hc = H if not head_chunk else max(1, min(H, int(head_chunk)))
    for h0 in range(0, H, hc):
        sl = slice(h0, min(H, h0 + hc))
        qz = quantize_qkv(q_re[:, sl], k_re[:, sl], v_re[:, sl], sm_scale, kv_valid_mask, chunk_size_q, chunk_size_k,
                          q_per_token, False, smooth_k, want_qkm=return_lse)
        lse_c = _attend_prequant(qz, block_indices[:, sl], block_indices_lens[:, sl], o[:, sl], chunk_size_q,
                                 chunk_size_k, do_merge, None if pair_flags is None or not do_merge else pair_flags[:, sl],
                                 q_per_token, smooth_k, return_lse)
        if return_lse:
            lse[:, sl] = lse_c
    return (o, lse) if return_lse else o


# =====================================================================
# Fused IVPQ path (ivpq_fast._prep_sage / _launch_sage with pv='fp8f16')
# =====================================================================
def prep_sage(q4, k4, v4, out4, lists, lens, geo, sm_scale, k_mean=None, tile=64):
    st = sb._PATCH_STATE
    B, H, _, D = q4.shape
    R = geo.src_u.shape[0]
    q8, qs, k8, ks, vq, vs, kvm, has_mask, qkm = quantize_qkv(
        q4, k4, v4, sm_scale, geo.valid_re, tile, tile, st.get("q_per_token", True), False, True,
        want_qkm=False, src_map=geo.src_u, k_mean=k_mean)
    dummy = torch.empty(1, dtype=torch.float32, device=q4.device)
    n_qt = R // tile
    n_pairs = n_qt // 2
    colb = sb._colbias(kvm, has_mask, R, q4.device)
    tail = (
        geo.src_u, colb, colb,
        out4.stride(0), out4.stride(1), out4.stride(2),
        vq.stride(0), vq.stride(1), 1, vq.stride(2),
        lists.stride(0), lists.stride(1), lists.stride(2), lists.stride(3),
        lens.stride(0), lens.stride(1), lens.stride(2),
        H, R, R, n_pairs,
    )
    head = (q8, k8, vq, qs, ks, vs, out4, dummy, dummy, lists, lens, geo.pair_merged, kvm, geo.bid_i32)
    kw = dict(HEAD_DIM=D, TQ=tile, BLOCK_N=tile, PV_MODE=2,
              Q_PER_TOKEN=st.get("q_per_token", True), K_PER_TOKEN=False,
              HAS_KV_MASK=has_mask, STORE_LSE=False, LSE_SHIFT=False,
              ROWMAP=True, SHARED_FLAGS=True, SCATTER=True)
    return dict(head=head, tail=tail, kw=kw, geo=geo, pv=PV, B=B, H=H, R=R, n_qt=n_qt, n_pairs=n_pairs,
                device=q4.device, k_per_token=False, keep=(qkm,))


def launch_sage(st, plan=None):
    if plan is None:
        plan = sb.launch_plan(st["device"], PV, st["R"], False)
    geo = st["geo"]
    merge = plan.get("merge", True) and st["n_pairs"] > 0
    kw = dict(st["kw"], **_kernel_opts(plan["opts"]))
    BH = st["B"] * st["H"]
    if merge:
        common = st["head"] + (geo.tile_skip_merged,) + st["tail"]
        w, s = plan["cfg128"]
        _attn_kernel[(st["n_pairs"], BH)](*common, BLOCK_M=128, MODE=1, num_warps=w, num_stages=s, **kw)
        w, s = plan["cfg64"]
        _attn_kernel[(st["n_qt"], BH)](*common, BLOCK_M=64, MODE=2, num_warps=w, num_stages=s, **kw)
    else:
        common = st["head"] + (geo.tile_skip_single,) + st["tail"]
        w, s = plan["cfg64"]
        _attn_kernel[(st["n_qt"], BH)](*common, BLOCK_M=64, MODE=0, num_warps=w, num_stages=s, **kw)


# =====================================================================
# Runtime hook (no edits to sage_bsa.py / ivpq_fast.py)
# =====================================================================
_INSTALLED = {}


def install():
    """Route pv='fp8f16' (and pv='auto' when auto_enabled()) through this module:
    sage_bsa.resolve_pv accepts/returns 'fp8f16'; ivpq_fast._prep_sage/_launch_sage and
    sage_bsa.sage_bsa_forward dispatch on it (so sage_tune tunes it like any PV mode);
    the patched generic BSA entry falls back fp8f16 -> fp8 -> fp16 -> original.
    Idempotent. Call before (or after) sage_bsa.patch_prism()."""
    if _INSTALLED:
        return
    from . import ivpq_fast as fx
    _INSTALLED.update(resolve_pv=sb.resolve_pv, sage_bsa_forward=sb.sage_bsa_forward,
                      prep=fx._prep_sage, launch=fx._launch_sage, patched=sb._patched_attn_fwd_bsa_varlen_triton)
    orig_resolve = _INSTALLED["resolve_pv"]
    orig_fwd = _INSTALLED["sage_bsa_forward"]
    orig_prep, orig_launch = _INSTALLED["prep"], _INSTALLED["launch"]
    orig_patched = _INSTALLED["patched"]

    def resolve_pv(pv, device=None):
        if pv == PV:
            if supported(device):
                return PV
            sb._warn_once("fp8f16 PV needs a native FP8/FP16-accumulate MMA (SM89+, Triton >= 3.4); using fp8")
            return orig_resolve("fp8", device)
        if pv in (None, "auto") and auto_enabled(device):
            return PV
        return orig_resolve(pv, device)

    def sage_bsa_forward_hook(q_re, k_re, v_re, sm_scale, block_indices, block_indices_lens,
                              kv_valid_mask=None, pv="auto", **kw):
        if resolve_pv(pv, q_re.device) == PV:
            if not _sol_requested(kw.get("sol")):
                kw.pop("sol", None)
                return sage_bsa_forward(q_re, k_re, v_re, sm_scale, block_indices, block_indices_lens,
                                        kv_valid_mask, PV, **kw)
            pv = "fp8"
        return orig_fwd(q_re, k_re, v_re, sm_scale, block_indices, block_indices_lens, kv_valid_mask, pv=pv, **kw)

    def prep_hook(q4, k4, v4, out4, lists, lens, geo, sm_scale, pv, *a, **kw):
        if pv == PV:
            if not a and not _sol_requested(kw.get("sol")):
                return prep_sage(q4, k4, v4, out4, lists, lens, geo, sm_scale, k_mean=kw.get("k_mean"))
            pv = "fp8"
        return orig_prep(q4, k4, v4, out4, lists, lens, geo, sm_scale, pv, *a, **kw)

    def launch_hook(st, plan=None, *a, **kw):
        if st.get("pv") == PV:
            return launch_sage(st, plan)
        return orig_launch(st, plan, *a, **kw)

    def patched_hook(q, k, v, sm_scale, block_indices, block_indices_lens, chunk_size_q, chunk_size_k, sparsity,
                     kv_valid_mask=None):
        ps = sb._PATCH_STATE
        if (sb._sage_supported(q, k, v, chunk_size_q, chunk_size_k) and resolve_pv(ps["pv"], q.device) == PV
                and (q.device.index, PV) not in ps["disabled"] and not _sol_requested(ps.get("sol"))):
            try:
                out = sage_bsa_forward(q, k, v, sm_scale, block_indices, block_indices_lens, kv_valid_mask,
                                       chunk_size_q=chunk_size_q, chunk_size_k=chunk_size_k, merge=ps["merge"],
                                       q_per_token=ps["q_per_token"], head_chunk=ps["head_chunk"], return_lse=True)
                ps["calls"] += 1
                return out
            except torch.cuda.OutOfMemoryError:
                raise
            except Exception as e:  # noqa: BLE001 - compile / resource failure -> sage_bsa's fp8 chain
                ps["disabled"].add((q.device.index, PV))
                sb._warn_once(f"pv=fp8f16 failed ({type(e).__name__}: {str(e)[:200]}); falling back to fp8")
            saved = ps["pv"]
            ps["pv"] = "fp8"
            try:
                return orig_patched(q, k, v, sm_scale, block_indices, block_indices_lens, chunk_size_q,
                                    chunk_size_k, sparsity, kv_valid_mask=kv_valid_mask)
            finally:
                ps["pv"] = saved
        if resolve_pv(ps["pv"], q.device) == PV:     # Sol requested / disabled: sage_bsa's fp8 chain
            saved = ps["pv"]
            ps["pv"] = "fp8"
            try:
                return orig_patched(q, k, v, sm_scale, block_indices, block_indices_lens, chunk_size_q,
                                    chunk_size_k, sparsity, kv_valid_mask=kv_valid_mask)
            finally:
                ps["pv"] = saved
        return orig_patched(q, k, v, sm_scale, block_indices, block_indices_lens, chunk_size_q, chunk_size_k,
                            sparsity, kv_valid_mask=kv_valid_mask)

    sb.resolve_pv = resolve_pv
    sb.sage_bsa_forward = sage_bsa_forward_hook
    fx._prep_sage = prep_hook
    fx._launch_sage = launch_hook
    sb._patched_attn_fwd_bsa_varlen_triton = patched_hook   # what a later patch_prism() installs
    _repoint(orig_patched, patched_hook)                    # patch_prism() already ran


def _sol_requested(explicit=None) -> bool:
    """The Sol skipped-block correction (sage_bsa.sol_enabled, if this sage_bsa has it)
    is not implemented in this kernel: such calls run sage_bsa's fp8 path instead."""
    if explicit:
        return True
    fn = getattr(sb, "sol_enabled", None)
    if fn is None:
        return False
    try:
        return bool(fn(explicit))
    except Exception:  # noqa: BLE001
        return False


def _bsa_modules():
    """The modules whose attn_fwd_bsa_varlen_triton sage_bsa.patch_prism() replaces."""
    import importlib
    mods = []
    for rel in (".block_sparse_attention.bsa_interface", ".block_sparse_attention.bias_rectification"):
        try:
            mods.append(importlib.import_module(rel, __package__))     # vendored layout
        except ImportError:
            pass
    if not mods:
        for name in ("hymm.models.modules.block_sparse_attention.bsa_interface",
                     "hymm.models.modules.block_sparse_attention.bias_rectification"):
            try:
                mods.append(importlib.import_module(name))
            except ImportError:
                pass
    return mods


def _repoint(old, new):
    new._sage_original = sb.ORIGINAL_attn_fwd_bsa_varlen_triton
    for mod in _bsa_modules():
        if getattr(mod, "attn_fwd_bsa_varlen_triton", None) is old:
            mod.attn_fwd_bsa_varlen_triton = new


def uninstall():
    if not _INSTALLED:
        return
    from . import ivpq_fast as fx
    sb.resolve_pv = _INSTALLED["resolve_pv"]
    sb.sage_bsa_forward = _INSTALLED["sage_bsa_forward"]
    fx._prep_sage = _INSTALLED["prep"]
    fx._launch_sage = _INSTALLED["launch"]
    cur = sb._patched_attn_fwd_bsa_varlen_triton
    sb._patched_attn_fwd_bsa_varlen_triton = _INSTALLED["patched"]
    _repoint(cur, _INSTALLED["patched"])
    _INSTALLED.clear()


__all__ = ["PV", "sage_bsa_forward", "quantize_qkv", "prep_sage", "launch_sage", "install", "uninstall",
           "f16acc_mma_native", "supported", "auto_enabled"]
