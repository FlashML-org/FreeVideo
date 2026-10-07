# Prism (MIT, Tencent): vendored from the Prism single-GPU research branch
# (Prism-fast 0befcb7 + the opt-in Sol correction of 98b7cc8, hymm/fast/sage_bsa.py) for FreeVideo; see NOTICE. FreeVideo
# change: the bias-rectification hooks are not vendored.
"""SageAttention-style quantized forward for Prism's block-sparse attention (BSA).

Inference only (forward, no backward). Block selection is untouched: the kernel
iterates exactly the k-tiles listed in ``block_indices[b, h, q_tile, :lens]``
like ``_attn_fwd_bsa_varlen_align``; only the arithmetic inside each tile pair
is quantized.

Pipeline (all Triton, no host sync), run head_chunk heads at a time:
  1. ``_kv_stats_kernel``: per-(b, h, channel) K sum and V |max| over valid
     tokens (chunked partials, reduced in torch).
  2. ``_quant_kernel``: Q -> INT8 per token (free: the kernel multiplies a per-row
     scale vector either way), K -> INT8 per (b, h, 64-tile) (or per token) after
     subtracting the per-(b, h) channel mean of K over valid tokens (exact for
     softmax: q.k_mean is constant along a query row; it is added back to the
     returned lse). sm_scale * log2(e) is folded into the Q scale. V -> fp16 with
     a power-of-two per-channel scale (lossless; keeps fp16 accumulation far from
     overflow) or -> FP8 e4m3 transposed [B, H, D, S] (per-channel amax/448;
     K-major operand layout required by FP8 MMA).
  3. ``_pair_same_kernel``: flags adjacent 64-row q tiles whose k-tile lists are
     identical (in the IVPQ path the two tiles of a 128-token logical block always
     are); those run as one BLOCK_M=128 program (half the K/V traffic per row).
  4. ``_sage_bsa_attn_kernel``: INT8 QK^T (int32 acc) dequantized with
     q_scale * k_scale, online softmax in fp32 (exp2), PV as
       'fp16'    fp16 x fp16 -> fp32 accumulator               (SM80+)
       'fp16acc' fp16 x fp16 -> fp16 per k-tile, fp32 across     (Sage1; full-rate
                 HMMA on GeForce RTX 30/40/50, where fp32-accumulate is half rate)
       'fp8'     e4m3 x e4m3 -> fp32, P scaled by 448 (Sage2), two-level
                 accumulation, V per-channel scale in the epilogue  (SM89+)
     kv_valid_mask and the all-padding / lens==0 guards match the original: such
     rows produce exact zeros (lse = -inf), never NaN.

SM90 default (measured on H200, 720p IVPQ lists): KPAIR=1 — two listed k tiles per
loop step with a shared running max, one fresh PV accumulator for both and one merge
(half the accumulator zeroing / rescale / merge work per tile), int32->fp32 by a
magic-number add instead of I2F and the key mask folded into that subtraction:
fp8 445 -> 585 TFLOP/s (original bf16 kernel: 318). fp16 on SM90 additionally loads
K/V through device-side TMA descriptors (360 -> 495). Consumer archs keep the
per-tile loop (the paired variant spills on mma.sync targets). Knobs:
PRISM_SAGE_BSA_OPTS="KPAIR=0,USE_TMA=1,..." and PRISM_SAGE_BSA_CFG="w64,s64,w128,s128".

The quant and attention kernels also take a row map (GATHER / ROWMAP / SCATTER)
so hymm.fast.ivpq_fast can read q/k/v straight from the THW projections and store
the output in place, without rearranged copies.

Entry points:
  sage_bsa_forward(q_re, k_re, v_re, sm_scale, block_indices, block_indices_lens,
                   kv_valid_mask=None, pv='auto')  -> o (same layout / dtype)
  patch_prism(pv='auto') / unpatch_prism(): swap
      bsa_interface.attn_fwd_bsa_varlen_triton (used by the IVPQ dynamic path via
      _dyn_bsa_kernel, by the uniform BSA paths, and by bias_rectification) for a
      dispatcher that runs the Sage kernel for supported inference calls and the
      original otherwise (grad-requiring inputs, chunk sizes other than 64/128,
      head_dim other than 64/128, < SM80). ``ORIGINAL_attn_fwd_bsa_varlen_triton``
      / ``original_bsa_forward`` stay available for A/B.

Sol correction (opt-in: sol=True / PRISM_SOL=1, default off; Li et al., Sol-Attn,
arXiv 2607.24027): the k tiles a row does NOT list are not dropped but approximated by
their masked token means (zeroth-order term): the quant pre-pass also stores per k tile
the INT8 mean of the smoothed K, the mean of the quantized-unit V and the valid count
(STORE_KC), the lists become a per-row bitmask (_sel_bitmask_kernel), and before the
exact loop each program streams all tile means SOL_G at a time (_sol_proxy): INT8
q . kbar scores, unlisted + non-empty tiles only, n_j * exp(.) folded into the same
online-softmax state (m, l, acc) as the exact tiles. Cost ~ N_k_tiles/SOL_G extra
tile steps per program (~1/64 of dense); memory O(B*H*N_k_tiles*D). With SOL off the
kernels compile to the previous code (bit-identical outputs).

'auto' PV per arch: SM80 fp16, SM86/87 fp16acc, SM89 and SM90 fp8, other >= SM89
(SM100/SM12x) fp8 only if this Triton emits a native FP8 MMA for it (Triton 3.3.1
emulates FP8 dot on SM120 via fp16 MMA, so RTX 50 gets fp16acc). Compile or
resource failures fall back fp8 -> fp16 -> original kernel, warning once.
"""
from __future__ import annotations

import os
import warnings
from typing import Optional, Tuple

import torch
import triton
import triton.language as tl

# Triton >= 3.4 exposes tl.make_tensor_descriptor; 3.3 only the experimental name.
_make_tensor_descriptor = getattr(tl, "make_tensor_descriptor", None) or tl._experimental_make_tensor_descriptor

_TRITON_GE_34 = tuple(int(x) for x in triton.__version__.split(".")[:2]) >= (3, 4)

_LOG2E = 1.4426950408889634
_LN2 = 0.6931471805599453
_FP8_MAX = 448.0
_FP16_V_TARGET_EXP = 9  # fp16 V is scaled (by a power of two) so |v| <= 2^9

PV_MODES = ("fp16", "fp16acc", "fp8")
_PV_CODE = {"fp16": 0, "fp16acc": 1, "fp8": 2}


# =====================================================================
# Kernels
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
    HAS_KV_MASK: tl.constexpr, SMOOTH_K: tl.constexpr, V_FP8: tl.constexpr,
    STORE_QKM: tl.constexpr, GATHER: tl.constexpr = False,
    STORE_KC: tl.constexpr = False, VC_FP8: tl.constexpr = False,
    KC8=None, KCS=None, VC=None, KCNT=None, N_KT_PAD=0,     # STORE_KC only (keyword args)
):
    # STORE_KC (Sol correction): per k tile also store the masked mean of the
    # smoothed K (INT8 + per-tile scale, the count packed into the scale's low 8
    # mantissa bits) -> KC8/KCS, the masked mean of the scaled V
    # (same units as the quantized V) -> VC fp16, and the valid-token count -> KCNT,
    # all at [B*H, N_KT_PAD(, D)]; VC_FP8: VC as e4m3 transposed [B*H, D, N_KT_PAD]
    # (K-major operand of the proxy's FP8 P.V, like the FP8 V copy).
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
        if STORE_KC:
            if HAS_KV_MASK:
                cnt = tl.sum(valid.to(tl.float32), axis=0)
            else:
                cnt = tl.full([], TK, tl.float32)
            inv_cnt = 1.0 / tl.maximum(cnt, 1.0)
            kcm = tl.sum(k, axis=0) * inv_cnt
            kca = tl.maximum(tl.max(tl.abs(kcm), axis=0), 1e-12)
            kci = kcm * (127.0 / kca)
            kci = kci + tl.where(kci >= 0, 0.5, -0.5)
            kc_row = off_hz.to(tl.int64) * N_KT_PAD + pid
            tl.store(KC8 + kc_row * HEAD_DIM + offs_d, kci.to(tl.int8))
            # scale with the valid count in its low 8 mantissa bits (one load per tile in
            # the attention kernel; relative change of the scale <= 2^-15)
            kcs_bits = (kca / 127.0).to(tl.int32, bitcast=True)
            tl.store(KCS + kc_row, ((kcs_bits & -256) | cnt.to(tl.int32)).to(tl.float32, bitcast=True))
            tl.store(KCNT + kc_row, cnt)
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
        if STORE_KC:
            vcm = tl.sum(v, axis=0) * inv_cnt
            if VC_FP8:
                tl.store(VC + (off_hz.to(tl.int64) * HEAD_DIM + offs_d) * N_KT_PAD + pid, vcm.to(tl.float8e4nv))
            else:
                tl.store(VC + kc_row * HEAD_DIM + offs_d, vcm.to(tl.float16))
        vo_base = VO + off_z.to(tl.int64) * stride_voz + off_h.to(tl.int64) * stride_voh
        if V_FP8:
            # transposed [D, TK] store: tokens contiguous (K-major PV operand)
            vt = tl.trans(v).to(tl.float8e4nv)
            tl.store(vo_base + offs_d[:, None] * stride_vod + offs_n[None, :] * stride_von, vt)
        else:
            tl.store(vo_base + offs_n[:, None] * stride_von + offs_d[None, :] * stride_vod,
                     v.to(tl.float16))


@triton.jit
def _pair_same_kernel(
    BI, BL, OUT,
    stride_bz, stride_bh, stride_bm, stride_bs,
    stride_lz, stride_lh, stride_lm,
    H, N_PAIRS,
    CH: tl.constexpr,
):
    pid = tl.program_id(0)
    off_hz = tl.program_id(1)
    off_z = off_hz // H
    off_h = off_hz % H
    l_base = BL + off_z.to(tl.int64) * stride_lz + off_h.to(tl.int64) * stride_lh
    la = tl.load(l_base + (2 * pid) * stride_lm)
    lb = tl.load(l_base + (2 * pid + 1) * stride_lm)
    same = (la == lb).to(tl.int32)
    if la == lb:
        b_base = BI + off_z.to(tl.int64) * stride_bz + off_h.to(tl.int64) * stride_bh
        row_a = b_base + (2 * pid).to(tl.int64) * stride_bm
        row_b = row_a + stride_bm
        ndiff = tl.full([], 0, tl.int32)
        for s in range(0, la, CH):
            offs = s + tl.arange(0, CH)
            msk = offs < la
            a = tl.load(row_a + offs * stride_bs, mask=msk, other=0)
            b = tl.load(row_b + offs * stride_bs, mask=msk, other=0)
            ndiff += tl.sum((a != b).to(tl.int32), axis=0)
        same = (ndiff == 0).to(tl.int32)
    tl.store(OUT + off_hz.to(tl.int64) * N_PAIRS + pid, same.to(tl.int8))


# ---------------------------------------------------------------------
# Sol-Attn style correction for the k tiles a row does NOT list
# (Li et al., "Sol-Attn", arXiv 2607.24027, Sec. 3.2 "proxy-score reuse"; zeroth-order
# term only: a skipped tile j contributes n_j * exp(q . kbar_j) to the softmax
# denominator and exp(q . kbar_j) * n_j * vbar_j to the numerator, with kbar_j / vbar_j
# the masked token means of the tile and n_j its valid-token count). Reimplemented
# here for the INT8/FP8 Sage kernel; the reference code (NVlabs/Sana, sol-engine
# branch, techniques/sparse_backends/sol_attn) is Apache-2.0.
# ---------------------------------------------------------------------
@triton.jit
def _sel_bitmask_kernel(BI, BL, OUT,
                        stride_bz, stride_bh, stride_bm, stride_bs,
                        stride_lz, stride_lh, stride_lm,
                        H, N_ROWS, N_WORDS, N_KT,
                        CH: tl.constexpr):
    """OUT[bh, row, w] |= 1 << (t % 32) for every listed tile t (w = t // 32) of
    block_indices[b, h, row, :lens]. OUT is pre-initialised (zeros, or the words of
    the empty tiles, see sel_bitmask)."""
    row = tl.program_id(0)
    bh = tl.program_id(1)
    z = bh // H
    h = bh % H
    n = tl.load(BL + z.to(tl.int64) * stride_lz + h.to(tl.int64) * stride_lh + row * stride_lm)
    b_row = BI + z.to(tl.int64) * stride_bz + h.to(tl.int64) * stride_bh + row.to(tl.int64) * stride_bm
    o_row = OUT + (bh.to(tl.int64) * N_ROWS + row) * N_WORDS
    for s in range(0, n, CH):
        j = s + tl.arange(0, CH)
        jm = j < n
        e = tl.load(b_row + j * stride_bs, mask=jm, other=0).to(tl.int32)
        m = jm & (e >= 0) & (e < N_KT)
        e = tl.where(m, e, 0)
        tl.atomic_or(o_row + (e >> 5), tl.full([CH], 1, tl.int32) << (e & 31), mask=m, sem="relaxed")


@triton.jit
def _sol_proxy(q, qs, acc, l_i, m_i, KC8, KCS, VC, SELM, off_hz, row, n_sel, N_ROWS, N_WORDS, N_KT_PAD,
               offs_d, HEAD_DIM: tl.constexpr, SOL_G: tl.constexpr, PV_MODE: tl.constexpr,
               BLOCK_N: tl.constexpr, PV8: tl.constexpr):
    """Approximate contribution of every unlisted valid k tile, SOL_G tiles per step,
    folded into the online-softmax state (acc, l_i, m_i) before the exact loop.

    Scores q . kbar are in the exact path's units: INT8 q (per-token scale qs, which
    holds sm_scale*log2 e) against INT8 kbar of the smoothed K (per-tile scale), so the
    shared running max and the exact tiles' scores stay consistent. The tile count n_j
    multiplies P after the max (never enters it), so the exact tiles' FP8 P keep their
    range. Listed tiles (bit set in SELM) and empty tiles (n_j = 0, incl. the padding
    up to N_KT_PAD) get -inf. Rows with an empty list (lens == 0: padding / no
    attention) are left untouched, so they still produce exact zeros."""
    offs_g = tl.arange(0, SOL_G)
    base = off_hz.to(tl.int64) * N_KT_PAD
    kc_base = KC8 + base * HEAD_DIM
    vc_base = VC + base * HEAD_DIM          # fp16 [NP, D], or e4m3 [D, NP] if PV8
    sm_base = SELM + (off_hz.to(tl.int64) * N_ROWS + row) * N_WORDS
    n_end = tl.where(n_sel > 0, N_KT_PAD, 0)
    # acc is still zero here, so the proxy accumulates in its own units (no per-step
    # rescale constant) and is converted to the exact path's units once at the end.
    # Per step: the row's list bits as SOL_G/32 scalar word loads (SELM also has the
    # empty / padding tiles set), one vector load of the packed per-tile scale + count
    # (prefetched one step ahead), and P.V into a fresh accumulator merged once (two-
    # level, so the MMA does not wait on the rescale of acc).
    kp_n = tl.load(KCS + base + offs_g, mask=n_end > 0, other=0.0)
    for g in range(0, n_end, SOL_G):
        t = g + offs_g
        kc = tl.load(kc_base + t[None, :] * HEAD_DIM + offs_d[:, None])           # [D, G] int8
        kp = kp_n
        tn = t + SOL_G
        kp_n = tl.load(KCS + base + tn, mask=tn < n_end, other=0.0)
        wsel = tl.zeros([SOL_G], dtype=tl.int32)
        for wi in tl.static_range(SOL_G // 32):
            wv = tl.load(sm_base + g // 32 + wi)
            wsel = tl.where((offs_g >> 5) == wi, wv, wsel)
        ok = ((wsel >> (offs_g & 31)) & 1) == 0
        kbits = kp.to(tl.int32, bitcast=True)
        cnt = (kbits & 255).to(tl.float32)
        kcs = (kbits & -256).to(tl.float32, bitcast=True)
        # int32 -> fp32 by the magic add (no I2F, see _sage_pair): bits(dot + 0x4B400000)
        # = 1.5*2^23 + dot (|dot| <= 128*127^2 < 2^22); one FFMA then removes the offset,
        # applies the per-tile scale and masks listed / empty tiles (-inf).
        x = (tl.dot(q, kc, out_dtype=tl.int32) + 0x4B400000).to(tl.float32, bitcast=True)
        nb = tl.where(ok, -12582912.0 * kcs, float("-inf"))
        sx = tl.fma(x, tl.broadcast_to(kcs[None, :], x.shape), tl.broadcast_to(nb[None, :], x.shape))
        m_ij = tl.maximum(m_i, tl.max(sx, 1) * qs)                                # qs > 0
        m_ninf = m_ij == float("-inf")
        alpha = tl.where(m_ninf, 1.0, tl.math.exp2(m_i - m_ij))
        m_sub = tl.where(m_ninf, 0.0, m_ij)
        pn = tl.math.exp2(sx * qs[:, None] - m_sub[:, None]) * cnt[None, :]
        l_i = l_i * alpha + tl.sum(pn, 1)
        if PV8:
            # FP8 P.V: pn <= n_j <= BLOCK_N, so pn * 448/BLOCK_N fits e4m3
            vc = tl.load(vc_base + offs_d[None, :] * N_KT_PAD + t[:, None])       # [G, D] e4m3, K-major
            d = tl.dot((pn * (448.0 / BLOCK_N)).to(tl.float8e4nv), vc)
        else:
            vc = tl.load(vc_base + t[:, None] * HEAD_DIM + offs_d[None, :])       # [G, D] fp16
            d = tl.dot(pn.to(tl.float16), vc)
        acc = acc * alpha[:, None] + d
        m_i = m_ij
    if PV8:
        acc = acc * BLOCK_N                   # -> the FP8 path's 448 * P.V units
    elif PV_MODE == 2:
        acc = acc * 448.0
    return acc, l_i, m_i


@triton.jit
def _sage_pair2(q, kid0, kid1, TWO: tl.constexpr, acc, l_i, m_i, qs, k_base, ks_base, CINIT, v_base,
                stride_vn, stride_vd, offs_n, offs_d, BLOCK_M: tl.constexpr, BLOCK_N: tl.constexpr,
                HEAD_DIM: tl.constexpr, PV_MODE: tl.constexpr, HAS_KV_MASK: tl.constexpr,
                ONES_SUM: tl.constexpr):
    """_sage_pair with the int->float conversion and the key mask done by the tensor
    core: the int32 QK accumulator starts at CINIT[key] = bits(1.5*2^23) (valid) or
    bits(-1.5*2^120) (padded), so the MMA output reinterpreted as fp32 is
    1.5*2^23 + s (exact, |s| < 2^22) or a huge negative number. The 1.5*2^23 offset
    is folded into the exp2 FMA's row constant (its rounding is < 1 LSB of s)."""
    s0 = tl.multiple_of(kid0 * BLOCK_N, BLOCK_N)
    k0 = tl.load(k_base + (s0 + offs_n)[None, :] * HEAD_DIM + offs_d[:, None])
    if HAS_KV_MASK:
        ci0 = tl.broadcast_to(tl.load(CINIT + s0 + offs_n)[None, :], [BLOCK_M, BLOCK_N])
    else:
        ci0 = tl.full([BLOCK_M, BLOCK_N], 0x4B400000, tl.int32)
    x0 = tl.dot(q, k0, ci0, out_dtype=tl.int32).to(tl.float32, bitcast=True)
    sc0 = qs * tl.load(ks_base + kid0)
    xm = tl.max(x0, 1)
    m_t = (xm - 12582912.0) * sc0
    if HAS_KV_MASK:
        m_t = tl.where(xm < 0.0, float("-inf"), m_t)
    if TWO:
        s1 = tl.multiple_of(kid1 * BLOCK_N, BLOCK_N)
        k1 = tl.load(k_base + (s1 + offs_n)[None, :] * HEAD_DIM + offs_d[:, None])
        if HAS_KV_MASK:
            ci1 = tl.broadcast_to(tl.load(CINIT + s1 + offs_n)[None, :], [BLOCK_M, BLOCK_N])
        else:
            ci1 = tl.full([BLOCK_M, BLOCK_N], 0x4B400000, tl.int32)
        x1 = tl.dot(q, k1, ci1, out_dtype=tl.int32).to(tl.float32, bitcast=True)
        sc1 = qs * tl.load(ks_base + kid1)
        xm1 = tl.max(x1, 1)
        m_t1 = (xm1 - 12582912.0) * sc1
        if HAS_KV_MASK:
            m_t1 = tl.where(xm1 < 0.0, float("-inf"), m_t1)
        m_t = tl.maximum(m_t, m_t1)
    m_ij = tl.maximum(m_i, m_t)
    if HAS_KV_MASK:
        m_ninf = m_ij == float("-inf")
        alpha = tl.where(m_ninf, 1.0, tl.math.exp2(m_i - m_ij))
        m_sub = tl.where(m_ninf, 0.0, m_ij)
    else:
        alpha = tl.math.exp2(m_i - m_ij)
        m_sub = m_ij
    if PV_MODE == 2:
        m_sub = m_sub - 8.807354922057604      # P pre-scaled by 448 = 2^8.807
    c0 = 12582912.0 * sc0 + m_sub
    p0 = tl.math.exp2(x0 * sc0[:, None] - c0[:, None])
    v0 = tl.load(v_base + (s0 + offs_n)[:, None] * stride_vn + offs_d[None, :] * stride_vd)
    if PV_MODE == 2:
        p0q = p0.to(tl.float8e4nv)
    else:
        p0q = p0.to(tl.float16)
    d = tl.dot(p0q, v0)
    if ONES_SUM:
        ones = tl.full([BLOCK_N, 16], 1.0, tl.float32).to(p0q.dtype)
        dl = tl.dot(p0q, ones)
    else:
        rs = tl.sum(p0, 1)
    if TWO:
        c1 = 12582912.0 * sc1 + m_sub
        p1 = tl.math.exp2(x1 * sc1[:, None] - c1[:, None])
        v1 = tl.load(v_base + (s1 + offs_n)[:, None] * stride_vn + offs_d[None, :] * stride_vd)
        if PV_MODE == 2:
            p1q = p1.to(tl.float8e4nv)
        else:
            p1q = p1.to(tl.float16)
        d = tl.dot(p1q, v1, d)
        if ONES_SUM:
            dl = tl.dot(p1q, ones, dl)
        else:
            rs += tl.sum(p1, 1)
    if ONES_SUM:
        rs = tl.max(dl, 1)          # 16 identical columns (sum of the quantized P)
    if PV_MODE == 2:
        rs = rs * (1.0 / 448.0)
    l_i = l_i * alpha + rs
    acc = acc * alpha[:, None] + d
    return acc, l_i, m_ij


@triton.jit
def _sage_pair(q, kid0, kid1, TWO: tl.constexpr, acc, l_i, m_i, qs, k_base, ks_base, COLB, v_base,
               stride_vn, stride_vd, offs_n, offs_d, BLOCK_N: tl.constexpr, HEAD_DIM: tl.constexpr,
               PV_MODE: tl.constexpr, HAS_KV_MASK: tl.constexpr, desc_k=None, desc_v=None,
               USE_TMA: tl.constexpr = False):
    """One or two k tiles with a shared running max: P0.V0 (+ P1.V1) accumulate in one
    fresh accumulator that is merged into acc once (two-level accumulation, half the
    zeroing/merge work per tile). Padded keys are masked by subtracting COLB
    (= 1.5*2^23 for valid keys, +inf for padded) during the magic int->float
    conversion, so masking costs no extra instruction."""
    s0 = tl.multiple_of(kid0 * BLOCK_N, BLOCK_N)
    if USE_TMA:
        k0 = tl.trans(desc_k.load([s0, 0]))
    else:
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
        if USE_TMA:
            k1 = tl.trans(desc_k.load([s1, 0]))
        else:
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
    if PV_MODE == 2:
        m_sub = m_sub - 8.807354922057604      # P pre-scaled by 448 = 2^8.807
    p0 = tl.math.exp2(x0 * sc0[:, None] - m_sub[:, None])
    if USE_TMA:
        if PV_MODE == 2:
            v0 = tl.trans(desc_v.load([0, s0]))
        else:
            v0 = desc_v.load([s0, 0])
    else:
        v0 = tl.load(v_base + (s0 + offs_n)[:, None] * stride_vn + offs_d[None, :] * stride_vd)
    if PV_MODE == 2:
        d = tl.dot(p0.to(tl.float8e4nv), v0)
    else:
        d = tl.dot(p0.to(tl.float16), v0)
    rs = tl.sum(p0, 1)
    if TWO:
        p1 = tl.math.exp2(x1 * sc1[:, None] - m_sub[:, None])
        if USE_TMA:
            if PV_MODE == 2:
                v1 = tl.trans(desc_v.load([0, s1]))
            else:
                v1 = desc_v.load([s1, 0])
        else:
            v1 = tl.load(v_base + (s1 + offs_n)[:, None] * stride_vn + offs_d[None, :] * stride_vd)
        if PV_MODE == 2:
            d = tl.dot(p1.to(tl.float8e4nv), v1, d)
        else:
            d = tl.dot(p1.to(tl.float16), v1, d)
        rs += tl.sum(p1, 1)
    if PV_MODE == 2:
        rs = rs * (1.0 / 448.0)
    l_i = l_i * alpha + rs
    acc = acc * alpha[:, None] + d
    return acc, l_i, m_ij


@triton.jit
def _sage_step(s_i32, kid, acc, l_i, m_i, qs, ks_base, KVM, v_base, stride_vn, stride_vd, offs_n, offs_d,
               BLOCK_N: tl.constexpr, PV_MODE: tl.constexpr, K_PER_TOKEN: tl.constexpr,
               HAS_KV_MASK: tl.constexpr, ACC_DIRECT: tl.constexpr, FAST_CVT: tl.constexpr):
    """One k tile: dequant + mask + online softmax + PV. s_i32: raw int32 Q.K scores."""
    start_n = tl.multiple_of(kid * BLOCK_N, BLOCK_N)
    if FAST_CVT and not K_PER_TOKEN:
        # int32 -> fp32 without I2F (a quarter-rate conversion that competes with
        # exp2 for the SFU pipe): |s| < 2^22, so bits(s + 0x4B400000) == 1.5*2^23 + s
        # exactly and one FADD recovers s. The row max is taken in the integer domain.
        sc = qs * tl.load(ks_base + kid)
        if HAS_KV_MASK:
            k_valid = tl.load(KVM + start_n + offs_n) != 0
            s_i32 = tl.where(k_valid[None, :], s_i32, -(1 << 30))
        rmax = tl.max(s_i32, 1)
        m_t = rmax.to(tl.float32) * sc
        if HAS_KV_MASK:
            m_t = tl.where(rmax == -(1 << 30), float("-inf"), m_t)
        m_ij = tl.maximum(m_i, m_t)
        if HAS_KV_MASK:
            m_ninf = m_ij == float("-inf")
            alpha = tl.where(m_ninf, 1.0, tl.math.exp2(m_i - m_ij))
            m_sub = tl.where(m_ninf, 0.0, m_ij)
        else:
            alpha = tl.math.exp2(m_i - m_ij)
            m_sub = m_ij
        s = (s_i32 + 0x4B400000).to(tl.float32, bitcast=True) - 12582912.0
        if HAS_KV_MASK:
            s = tl.where(k_valid[None, :], s, float("-inf"))
        v = tl.load(v_base + (start_n + offs_n)[:, None] * stride_vn + offs_d[None, :] * stride_vd)
        if PV_MODE == 2:
            # P is produced pre-scaled by 448 (log2(448) folded into the exp2 argument)
            p = tl.math.exp2(s * sc[:, None] - (m_sub - 8.807354922057604)[:, None])
            l_i = l_i * alpha + tl.sum(p, 1) * (1.0 / 448.0)
            if ACC_DIRECT:
                acc = acc * alpha[:, None]
                acc = tl.dot(p.to(tl.float8e4nv), v, acc)
            else:
                acc = acc * alpha[:, None] + tl.dot(p.to(tl.float8e4nv), v)
        else:
            p = tl.math.exp2(s * sc[:, None] - m_sub[:, None])
            l_i = l_i * alpha + tl.sum(p, 1)
            if PV_MODE == 0:
                acc = acc * alpha[:, None]
                acc = tl.dot(p.to(tl.float16), v, acc)
            else:
                acc = acc * alpha[:, None] + tl.dot(p.to(tl.float16), v, out_dtype=tl.float16).to(tl.float32)
        return acc, l_i, m_ij
    s = s_i32.to(tl.float32)
    if HAS_KV_MASK:
        k_valid = tl.load(KVM + start_n + offs_n) != 0
        s = tl.where(k_valid[None, :], s, float("-inf"))
    if K_PER_TOKEN:
        ks = tl.load(ks_base + start_n + offs_n)
        s = s * qs[:, None] * ks[None, :]
        m_ij = tl.maximum(m_i, tl.max(s, 1))
    else:
        # row scale > 0, so rowmax(s * sc) = rowmax(s) * sc and the dequant
        # folds into the exp2 argument as a single FMA per score.
        sc = qs * tl.load(ks_base + kid)
        m_ij = tl.maximum(m_i, tl.max(s, 1) * sc)
    if HAS_KV_MASK:
        # all-(-inf) so far: keep acc / l_i untouched (alpha = 1) and p = 0,
        # identical to the original's guard (no NaN for all-padding rows).
        m_ninf = m_ij == float("-inf")
        alpha = tl.where(m_ninf, 1.0, tl.math.exp2(m_i - m_ij))
        m_sub = tl.where(m_ninf, 0.0, m_ij)
    else:
        alpha = tl.math.exp2(m_i - m_ij)
        m_sub = m_ij
    if K_PER_TOKEN:
        p = tl.math.exp2(s - m_sub[:, None])
    else:
        p = tl.math.exp2(s * sc[:, None] - m_sub[:, None])
    l_i = l_i * alpha + tl.sum(p, 1)
    v = tl.load(v_base + (start_n + offs_n)[:, None] * stride_vn + offs_d[None, :] * stride_vd)
    if PV_MODE == 0:
        acc = acc * alpha[:, None]
        acc = tl.dot(p.to(tl.float16), v, acc)
    elif PV_MODE == 1:
        acc = acc * alpha[:, None] + tl.dot(p.to(tl.float16), v, out_dtype=tl.float16).to(tl.float32)
    else:
        if ACC_DIRECT:
            acc = acc * alpha[:, None]
            acc = tl.dot((p * 448.0).to(tl.float8e4nv), v, acc)
        else:
            acc = acc * alpha[:, None] + tl.dot((p * 448.0).to(tl.float8e4nv), v)
    return acc, l_i, m_ij


@triton.jit
def _sage_bsa_attn_kernel(
    Q8, K8, V, QS, KS, VS, Out, LSE, QKM,
    BI, BL, PAIR, KVM, RMAP, TSKIP, SRCU, COLB, CINIT,
    stride_oz, stride_oh, stride_om,
    stride_vz, stride_vh, stride_vn, stride_vd,
    stride_bz, stride_bh, stride_bm, stride_bs,
    stride_lz, stride_lh, stride_lm,
    H, N_Q, N_K, N_PAIRS,
    HEAD_DIM: tl.constexpr,
    TQ: tl.constexpr,        # q tile (= chunk_size_q) that owns a block_indices row
    BLOCK_M: tl.constexpr,   # rows per program (TQ, or 2*TQ for merged pairs)
    BLOCK_N: tl.constexpr,   # k tile (= chunk_size_k)
    MODE: tl.constexpr,      # 0: every q tile, 1: merged pairs only, 2: tiles not in a merged pair
    PV_MODE: tl.constexpr,   # 0 fp16/fp32acc, 1 fp16/fp16acc, 2 fp8
    Q_PER_TOKEN: tl.constexpr, K_PER_TOKEN: tl.constexpr,
    HAS_KV_MASK: tl.constexpr, STORE_LSE: tl.constexpr, LSE_SHIFT: tl.constexpr,
    ROWMAP: tl.constexpr = False,        # list row of q tile t = RMAP[t] (per-logical-block lists)
    SHARED_FLAGS: tl.constexpr = False,  # PAIR [N_PAIRS] / TSKIP [N_tiles] are head-independent
    SCATTER: tl.constexpr = False,       # output row r goes to token SRCU[r] (-1: dropped)
    QK_PREFETCH: tl.constexpr = False,   # issue the next tile's QK before this tile's softmax
    ACC_DIRECT: tl.constexpr = False,    # fp8: accumulate PV straight into acc (no 2-level temp)
    FAST_CVT: tl.constexpr = False,      # int->float via magic add, integer row max, 448 folded
    KPAIR: tl.constexpr = False,         # 2 k tiles per step: shared row max, one PV accumulator,
                                         # one merge; key mask as additive column bias (COLB)
                                         # 2: + magic-int accumulator init / mask in the MMA (CINIT)
    ONES_SUM: tl.constexpr = False,      # KPAIR=2: P row sums by an MMA against ones
    USE_TMA: tl.constexpr = False,       # KPAIR=1: K/V tiles via device-side TMA descriptors (SM90)
    WARP_SPEC: tl.constexpr = False,     # KPAIR=1: tl.range(warp_specialize=True) (Triton >= 3.6)
    UNROLL: tl.constexpr = 1,            # KPAIR=1: loop_unroll_factor of the paired loop
    SOL: tl.constexpr = False,           # approximate the unlisted k tiles (_sol_proxy)
    SOL_G: tl.constexpr = 64,            # SOL: k tiles per proxy step
    SOL_PV8: tl.constexpr = False,       # SOL, PV_MODE 2: proxy P.V in FP8 (VC stored e4m3 [D, NP])
    # SOL only, passed as keyword args (_sol_args): tile summaries, list bitmask
    KC8=None, KCS=None, VC=None, KCNT=None, SELM=None, N_ROWS=1, N_WORDS=1, N_KT_PAD=1,
):
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
    if K_PER_TOKEN:
        ks_base = KS + off_hz.to(tl.int64) * N_K
    else:
        ks_base = KS + off_hz.to(tl.int64) * (N_K // BLOCK_N)

    m_i = tl.zeros([BLOCK_M], dtype=tl.float32) - float("inf")
    l_i = tl.zeros([BLOCK_M], dtype=tl.float32) + 1.0
    acc = tl.zeros([BLOCK_M, HEAD_DIM], dtype=tl.float32)
    if SOL:
        acc, l_i, m_i = _sol_proxy(q, qs, acc, l_i, m_i, KC8, KCS, VC, SELM, off_hz, row, n_sel, N_ROWS,
                                   N_WORDS, N_KT_PAD, offs_d, HEAD_DIM, SOL_G, PV_MODE, BLOCK_N, SOL_PV8)

    if KPAIR == 2:
        n_pair = n_sel // 2
        for ip in range(0, n_pair):
            kid0 = tl.load(bi_ptr + (2 * ip) * stride_bs).to(tl.int32)
            kid1 = tl.load(bi_ptr + (2 * ip + 1) * stride_bs).to(tl.int32)
            acc, l_i, m_i = _sage_pair2(q, kid0, kid1, True, acc, l_i, m_i, qs, k_base, ks_base, CINIT, v_base,
                                        stride_vn, stride_vd, offs_n, offs_d, BLOCK_M, BLOCK_N, HEAD_DIM,
                                        PV_MODE, HAS_KV_MASK, ONES_SUM)
        if n_sel % 2 == 1:
            kid0 = tl.load(bi_ptr + (n_sel - 1) * stride_bs).to(tl.int32)
            acc, l_i, m_i = _sage_pair2(q, kid0, kid0, False, acc, l_i, m_i, qs, k_base, ks_base, CINIT, v_base,
                                        stride_vn, stride_vd, offs_n, offs_d, BLOCK_M, BLOCK_N, HEAD_DIM,
                                        PV_MODE, HAS_KV_MASK, ONES_SUM)
    elif KPAIR:
        if USE_TMA:
            desc_k = _make_tensor_descriptor(
                k_base, shape=[N_K, HEAD_DIM], strides=[HEAD_DIM, 1], block_shape=[BLOCK_N, HEAD_DIM])
            if PV_MODE == 2:
                desc_v = _make_tensor_descriptor(
                    v_base, shape=[HEAD_DIM, N_K], strides=[stride_vd, 1], block_shape=[HEAD_DIM, BLOCK_N])
            else:
                desc_v = _make_tensor_descriptor(
                    v_base, shape=[N_K, HEAD_DIM], strides=[stride_vn, 1], block_shape=[BLOCK_N, HEAD_DIM])
        n_pair = n_sel // 2
        if USE_TMA:
            # same math as _sage_pair(TWO=True), inlined: Triton 3.3 cannot pass
            # tensor descriptors to jit helpers.
            for ip in range(0, n_pair):
                kid0 = tl.load(bi_ptr + (2 * ip) * stride_bs).to(tl.int32)
                kid1 = tl.load(bi_ptr + (2 * ip + 1) * stride_bs).to(tl.int32)
                s0 = tl.multiple_of(kid0 * BLOCK_N, BLOCK_N)
                s1 = tl.multiple_of(kid1 * BLOCK_N, BLOCK_N)
                x0 = (tl.dot(q, tl.trans(desc_k.load([s0, 0])), out_dtype=tl.int32) + 0x4B400000).to(tl.float32, bitcast=True)
                x1 = (tl.dot(q, tl.trans(desc_k.load([s1, 0])), out_dtype=tl.int32) + 0x4B400000).to(tl.float32, bitcast=True)
                if HAS_KV_MASK:
                    x0 = x0 - tl.load(COLB + s0 + offs_n)[None, :]
                    x1 = x1 - tl.load(COLB + s1 + offs_n)[None, :]
                else:
                    x0 = x0 - 12582912.0
                    x1 = x1 - 12582912.0
                sc0 = qs * tl.load(ks_base + kid0)
                sc1 = qs * tl.load(ks_base + kid1)
                m_ij = tl.maximum(m_i, tl.maximum(tl.max(x0, 1) * sc0, tl.max(x1, 1) * sc1))
                if HAS_KV_MASK:
                    m_ninf = m_ij == float("-inf")
                    alpha = tl.where(m_ninf, 1.0, tl.math.exp2(m_i - m_ij))
                    m_sub = tl.where(m_ninf, 0.0, m_ij)
                else:
                    alpha = tl.math.exp2(m_i - m_ij)
                    m_sub = m_ij
                if PV_MODE == 2:
                    m_sub = m_sub - 8.807354922057604
                p0 = tl.math.exp2(x0 * sc0[:, None] - m_sub[:, None])
                p1 = tl.math.exp2(x1 * sc1[:, None] - m_sub[:, None])
                if PV_MODE == 2:
                    d = tl.dot(p0.to(tl.float8e4nv), tl.trans(desc_v.load([0, s0])))
                    d = tl.dot(p1.to(tl.float8e4nv), tl.trans(desc_v.load([0, s1])), d)
                    rs = (tl.sum(p0, 1) + tl.sum(p1, 1)) * (1.0 / 448.0)
                else:
                    d = tl.dot(p0.to(tl.float16), desc_v.load([s0, 0]))
                    d = tl.dot(p1.to(tl.float16), desc_v.load([s1, 0]), d)
                    rs = tl.sum(p0, 1) + tl.sum(p1, 1)
                l_i = l_i * alpha + rs
                acc = acc * alpha[:, None] + d
                m_i = m_ij
        elif WARP_SPEC:
            # Triton >= 3.6 only (automatic warp specialization of the loop)
            for ip in tl.range(0, n_pair, warp_specialize=True):
                kid0 = tl.load(bi_ptr + (2 * ip) * stride_bs).to(tl.int32)
                kid1 = tl.load(bi_ptr + (2 * ip + 1) * stride_bs).to(tl.int32)
                acc, l_i, m_i = _sage_pair(q, kid0, kid1, True, acc, l_i, m_i, qs, k_base, ks_base, COLB, v_base,
                                           stride_vn, stride_vd, offs_n, offs_d, BLOCK_N, HEAD_DIM, PV_MODE,
                                           HAS_KV_MASK)
        else:
            for ip in tl.range(0, n_pair, loop_unroll_factor=UNROLL):
                kid0 = tl.load(bi_ptr + (2 * ip) * stride_bs).to(tl.int32)
                kid1 = tl.load(bi_ptr + (2 * ip + 1) * stride_bs).to(tl.int32)
                acc, l_i, m_i = _sage_pair(q, kid0, kid1, True, acc, l_i, m_i, qs, k_base, ks_base, COLB, v_base,
                                           stride_vn, stride_vd, offs_n, offs_d, BLOCK_N, HEAD_DIM, PV_MODE,
                                           HAS_KV_MASK)
        if n_sel % 2 == 1:
            kid0 = tl.load(bi_ptr + (n_sel - 1) * stride_bs).to(tl.int32)
            acc, l_i, m_i = _sage_pair(q, kid0, kid0, False, acc, l_i, m_i, qs, k_base, ks_base, COLB, v_base,
                                       stride_vn, stride_vd, offs_n, offs_d, BLOCK_N, HEAD_DIM, PV_MODE,
                                       HAS_KV_MASK)
    elif QK_PREFETCH:
        # Software-pipelined QK: S_{i+1} = Q K_{i+1}^T is issued before the softmax
        # and PV of step i, so the (async) tensor-core work overlaps the exp/max/sum.
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
            acc, l_i, m_i = _sage_step(s_cur, kid, acc, l_i, m_i, qs, ks_base, KVM, v_base,
                                       stride_vn, stride_vd, offs_n, offs_d, BLOCK_N, PV_MODE, K_PER_TOKEN,
                                       HAS_KV_MASK, ACC_DIRECT, FAST_CVT)
            s_cur = s_next
            kid = kid_n
    else:
        for i in range(0, n_sel):
            kid = tl.load(bi_ptr + i * stride_bs).to(tl.int32)
            start_n = tl.multiple_of(kid * BLOCK_N, BLOCK_N)
            kT = tl.load(k_base + (start_n + offs_n)[None, :] * HEAD_DIM + offs_d[:, None])
            s = tl.dot(q, kT, out_dtype=tl.int32)
            acc, l_i, m_i = _sage_step(s, kid, acc, l_i, m_i, qs, ks_base, KVM, v_base,
                                       stride_vn, stride_vd, offs_n, offs_d, BLOCK_N, PV_MODE, K_PER_TOKEN,
                                       HAS_KV_MASK, ACC_DIRECT, FAST_CVT)

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
# Arch dispatch / launch configs
# =====================================================================
def device_arch(device=None) -> Tuple[int, int]:
    if device is None:
        device = torch.cuda.current_device()
    return torch.cuda.get_device_capability(device)


@triton.jit
def _fp8_probe_kernel(A, B, C):
    offs = tl.arange(0, 64)
    a = tl.load(A + offs[:, None] * 64 + offs[None, :])
    b = tl.load(B + offs[:, None] + offs[None, :] * 64)
    tl.store(C + offs[:, None] * 64 + offs[None, :], tl.dot(a, b))


_FP8_NATIVE = {}


def fp8_mma_native(device=None) -> bool:
    """True if this Triton lowers an e4m3 tl.dot to a real FP8 MMA for the device.

    Triton 3.3 does so for SM89 (mma.sync e4m3) and SM90 (wgmma e4m3) but emulates
    it on SM120 (upcast to fp16 + fp32-accumulate MMA, half rate on GeForce), in
    which case fp16acc is the faster PV mode. Checked by an AOT compile (no launch).
    """
    cap = device_arch(device)
    if cap < (8, 9):
        return False
    if cap not in _FP8_NATIVE:
        try:
            from triton.backends.compiler import GPUTarget
            sig = {"A": "*fp8e4nv", "B": "*fp8e4nv", "C": "*fp32"}
            attrs = {(i,): [["tt.divisibility", 16]] for i in range(3)}
            src = triton.compiler.ASTSource(fn=_fp8_probe_kernel, signature=sig, constexprs={}, attrs=attrs)
            ccinfo = triton.compile(src, target=GPUTarget("cuda", cap[0] * 10 + cap[1], 32),
                                    options={"num_warps": 4})
            _FP8_NATIVE[cap] = "e4m3.e4m3" in ccinfo.asm["ptx"]
        except Exception as e:  # noqa: BLE001
            _warn_once(f"fp8 probe failed ({type(e).__name__}: {str(e)[:120]}); assuming no native fp8 MMA")
            _FP8_NATIVE[cap] = False
    return _FP8_NATIVE[cap]


def auto_pv(device=None) -> Optional[str]:
    """Default PV mode for a device, or None if the Sage path is unsupported.

    SM80: fp16 (full-rate fp32 accumulate). SM86/87 (RTX 30): fp16acc (no FP8;
    GeForce fp32-accumulate HMMA is half rate). SM89 (RTX 40) / SM90: fp8.
    SM12x (RTX 50) and others >= SM89: fp8 only if Triton emits a native FP8 MMA
    for them (not the case for SM120 in Triton 3.3.1), else fp16acc.
    """
    cap = device_arch(device)
    if cap < (8, 0):
        return None                 # keep the original kernel
    if cap in ((8, 6), (8, 7)):
        return "fp16acc"
    if cap < (8, 9):
        return "fp16"
    if cap in ((8, 9), (9, 0)):
        return "fp8"
    return "fp8" if fp8_mma_native(device) else "fp16acc"


def resolve_pv(pv: str, device=None) -> Optional[str]:
    if pv in (None, "auto"):
        return auto_pv(device)
    if pv not in PV_MODES:
        raise ValueError(f"pv must be one of {PV_MODES} or 'auto', got {pv!r}")
    if pv == "fp8" and device_arch(device) < (8, 9):
        _warn_once(f"fp8 PV needs SM89+, device is SM{''.join(map(str, device_arch(device)))}; using fp16")
        return "fp16"
    return pv


# (num_warps, num_stages) for the BLOCK_M=64 and BLOCK_M=128 attention programs.
# Consumer parts (SM86/89/12x) have ~99 KB of shared memory per block, so keep
# 3 stages; overridable for tuning via PRISM_SAGE_BSA_CFG="w64,s64,w128,s128".
def _launch_cfg(device, block_m: int, opts=None):
    env = os.environ.get("PRISM_SAGE_BSA_CFG")
    if env:
        w64, s64, w128, s128 = (int(x) for x in env.split(","))
        return (w64, s64) if block_m <= 64 else (w128, s128)
    stages = 2 if (opts or {}).get("USE_TMA") else 3
    if block_m <= 64:
        return 4, stages
    return 8, stages


# ---------------------------------------------------------------------------
# Launch plans: kernel constexpr opts + (num_warps, num_stages) for the BLOCK_M=64
# and BLOCK_M=128 programs + whether to merge 128-token q blocks. Source order:
# tuner override > tune cache (hymm.fast.sage_tune) > per-arch defaults; then
# PRISM_SAGE_BSA_OPTS / PRISM_SAGE_BSA_CFG env overrides on top.
# ---------------------------------------------------------------------------
import json as _json

TUNE_PATH = os.environ.get("PRISM_SAGE_TUNE_CACHE",
                           os.path.join(os.path.expanduser("~"), ".cache", "prism_fast", "sage_bsa_tune.json"))
_PLAN_OVERRIDE = None          # set by hymm.fast.sage_tune while measuring candidates
_TUNE_DISK = None
_TUNE_MEM = {}
# padded token counts of the attention problem per resolution (205 frames, IVPQ padding)
SHAPE_CLASSES = {"480p": 56 * 32 * 56, "720p": 56 * 48 * 80, "1080p": 56 * 72 * 120}


def shape_class(n_rows: int) -> str:
    import math as _m
    best = min(SHAPE_CLASSES.items(), key=lambda kv: abs(_m.log(n_rows / kv[1])))
    if abs(_m.log(n_rows / best[1])) < 0.35:
        return best[0]
    return "S%dk" % (1 << max(0, round(_m.log2(max(1, n_rows) / 1024))))


def _device_tag(device):
    p = torch.cuda.get_device_properties(device)
    return "%s|sm%d%d|t%s" % (p.name, p.major, p.minor, triton.__version__)


def tune_key(device, pv: str, cls: str) -> str:
    return "sage|%s|%s|%s" % (_device_tag(device), pv, cls)


def _tune_disk():
    global _TUNE_DISK
    if _TUNE_DISK is None:
        try:
            with open(TUNE_PATH) as f:
                _TUNE_DISK = _json.load(f)
        except Exception:  # noqa: BLE001
            _TUNE_DISK = {}
    return _TUNE_DISK


def save_tuned(key: str, entry: dict):
    _tune_disk()[key] = entry
    _TUNE_MEM.pop(key, None)
    try:
        os.makedirs(os.path.dirname(TUNE_PATH), exist_ok=True)
        tmp = TUNE_PATH + ".tmp%d" % os.getpid()
        with open(tmp, "w") as f:
            _json.dump(_TUNE_DISK, f, indent=1, sort_keys=True)
        os.replace(tmp, TUNE_PATH)
    except Exception as e:  # noqa: BLE001
        _warn_once("cannot write tune cache %s: %s" % (TUNE_PATH, e))


def default_plan(device, pv):
    opts = default_kernel_opts(device, pv)
    st = 2 if (opts.get("USE_TMA") and not _TRITON_GE_34) else 3
    return {"opts": opts, "cfg64": [4, st], "cfg128": [8, st], "merge": True}


def launch_plan(device, pv: str, n_rows: int, k_per_token: bool = False) -> dict:
    if _PLAN_OVERRIDE is not None:
        plan = dict(_PLAN_OVERRIDE)
        plan["opts"] = dict(plan.get("opts", {}))
    else:
        key = tune_key(device, pv, shape_class(n_rows))
        plan = _TUNE_MEM.get(key)
        if plan is None and os.environ.get("PRISM_SAGE_TUNE", "0") == "1":
            from . import sage_tune      # first use on this GPU: measure, then cache
            sage_tune.ensure_tuned(device, pv, n_rows, float(os.environ.get("PRISM_SAGE_TUNE_BUDGET", "180")))
        if plan is None:
            ent = _tune_disk().get(key)
            plan = ({"opts": dict(ent["opts"]), "cfg64": list(ent["cfg64"]), "cfg128": list(ent["cfg128"]),
                     "merge": bool(ent.get("merge", True))} if ent else default_plan(device, pv))
            _TUNE_MEM[key] = plan
        plan = dict(plan)
        plan["opts"] = dict(plan["opts"])
    env = os.environ.get("PRISM_SAGE_BSA_OPTS", "")
    for item in filter(None, env.split(",")):
        k_, v_ = item.split("=")
        plan["opts"][k_.strip()] = int(v_)
    env = os.environ.get("PRISM_SAGE_BSA_CFG")
    if env:
        w64, s64, w128, s128 = (int(x) for x in env.split(","))
        plan["cfg64"], plan["cfg128"] = [w64, s64], [w128, s128]
    if pv == "fp16acc" or k_per_token:
        plan["opts"].pop("KPAIR", None)       # the paired path accumulates in fp32
        plan["opts"].pop("USE_TMA", None)
    if plan["opts"].get("USE_TMA"):
        _ensure_tma_allocator()
    return plan


def _colbias(kvm, has_mask, n, dev):
    """Per-key additive bias for KPAIR masking: 1.5*2^23 for valid keys, +inf for padding."""
    if not has_mask:
        return torch.empty(1, dtype=torch.float32, device=dev)
    return torch.where(kvm[:n] != 0, 12582912.0, float("inf")).to(torch.float32).contiguous()


def _cinit(kvm, has_mask, n, dev):
    """Per-key int32 QK accumulator init for KPAIR=2: bits(1.5*2^23) valid, bits(-1.5*2^120) padded."""
    if not has_mask:
        return torch.empty(1, dtype=torch.int32, device=dev)
    return torch.where(kvm[:n] != 0, 0x4B400000, -0x04400000).to(torch.int32).contiguous()


def default_kernel_opts(device=None, pv: Optional[str] = None):
    """Measured on H200 (720p IVPQ lists, H=8): fp8 445 -> 585 TFLOP/s with KPAIR,
    fp16 360 -> 495 with KPAIR+TMA (2 stages). On SM8x/SM12x (mma.sync) the KPAIR
    variant spills (~600 B/thread at 255 regs) and would drop fp16 accumulation, so
    those keep the per-tile loop."""
    cap = device_arch(device)
    if cap == (9, 0) and pv == "fp8":
        # Triton >= 3.4: TMA descriptor loads win (545 -> 625 TFLOP/s on H200, 3.7.1)
        return {"KPAIR": 1, "USE_TMA": 1} if _TRITON_GE_34 else {"KPAIR": 1}
    if cap == (9, 0) and pv == "fp16":
        return {"KPAIR": 1, "USE_TMA": 1}
    return {}


def kernel_opts(device=None, pv: Optional[str] = None):
    """Attention-kernel constexprs: per-arch defaults, overridable (tuning) with
    PRISM_SAGE_BSA_OPTS="KPAIR=0,USE_TMA=0,..."."""
    env = os.environ.get("PRISM_SAGE_BSA_OPTS", "")
    out = dict(default_kernel_opts(device, pv))
    for item in filter(None, env.split(",")):
        key, val = item.split("=")
        out[key.strip()] = int(val)
    if out.get("USE_TMA"):
        _ensure_tma_allocator()
    return out


_TMA_ALLOC = {"set": False}


def _ensure_tma_allocator():
    """Device-side TMA descriptors need a per-launch global scratch buffer."""
    if not _TMA_ALLOC["set"]:
        def _alloc(size, alignment, stream):
            return torch.empty(size, dtype=torch.int8, device="cuda")
        triton.set_allocator(_alloc)
        _TMA_ALLOC["set"] = True


_DEFAULT_KERNEL_OPTS = {}
_WARNED = set()


def _warn_once(msg):
    if msg not in _WARNED:
        _WARNED.add(msg)
        warnings.warn("[sage_bsa] " + msg, stacklevel=3)


# =====================================================================
# Quantization pre-pass
# =====================================================================
def quantize_qkv(
    q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, sm_scale: float,
    kv_valid_mask: Optional[torch.Tensor], pv: str,
    tile_q: int = 64, tile_k: int = 64,
    q_per_token: bool = True, k_per_token: bool = False, smooth_k: bool = True,
    want_qkm: bool = False, src_map: Optional[torch.Tensor] = None,
    k_mean: Optional[torch.Tensor] = None, want_kc: bool = False,
):
    """want_kc: also return the Sol tile summaries as a 10th element
    (kc8 int8 [B*H, NP, D]; kc_scale fp32 [B*H, NP] with the valid count in its low 8
    mantissa bits; vc = tile-mean V in V's quantized units, fp16 [B*H, NP, D], or e4m3
    [B*H, D, NP] for pv='fp8' unless PRISM_SOL_PV8=0; count fp32 [B*H, NP]);
    NP = n_k_tiles padded to a multiple of 128; padding tiles have count 0.

    k_mean: optional precomputed smoothing vector [B*H, D] fp32 (any per-head constant
    is exact for softmax; e.g. derived from the selection's tile means), which skips the
    K pass of the stats kernel.

    Returns (q8, q_scale, k8, k_scale, v_q, v_scale_eff, kv_mask_u8, has_mask, qkm).

    q8/k8: int8 [B,H,S,D] contiguous. q_scale includes sm_scale*log2(e).
    v_q: fp16 [B,H,Sk,D] or float8_e4m3fn [B,H,D,Sk] (transposed).
    v_scale_eff: fp32 [B*H, D] to multiply into the PV accumulator.
    qkm: fp32 [B*H, Sq] = sm_scale * q.k_mean (lse correction) if want_qkm and smooth_k.
    src_map: optional int32 [R] row map for self-attention: logical (tile-layout)
    row r reads token src_map[r] of q/k/v (-1 = padding, reads zeros); q/k/v may
    then be any [B,H,S_in,D] strided views (e.g. of the THW [B,S,H*D] projections)
    and Sq = Sk = R.
    """
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
    v_fp8 = pv == "fp8"

    has_mask = kv_valid_mask is not None
    if has_mask:
        kvm = kv_valid_mask.contiguous()
        if kvm.dtype == torch.bool:
            kvm = kvm.view(torch.uint8)
    else:
        kvm = torch.empty(1, dtype=torch.uint8, device=dev)

    need_ksum = smooth_k and k_mean is None
    CHUNK = 4096 if need_ksum else 1024        # V-only pass: more programs, same max
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
        if has_mask:
            cnt = (kvm != 0).sum().clamp(min=1).to(torch.float32)
        else:
            cnt = torch.tensor(float(Sk), device=dev)
        kmean = (ksum.sum(dim=1) / cnt).contiguous()
    else:
        kmean = torch.empty(1, dtype=torch.float32, device=dev)
    vamax = vmax.amax(dim=1)                                   # [BH, D]
    if v_fp8:
        v_scale = torch.where(vamax > 0, vamax / _FP8_MAX, torch.ones_like(vamax))
        v_scale_eff = (v_scale / _FP8_MAX).contiguous()        # P is scaled by 448 too
    else:
        e = torch.ceil(torch.log2(vamax.clamp(min=1e-30))) - _FP16_V_TARGET_EXP
        v_scale = torch.where(vamax > 0, torch.exp2(e), torch.ones_like(vamax))
        v_scale_eff = v_scale.contiguous()
    v_inv = (1.0 / v_scale).contiguous()

    q8 = torch.empty((B, H, Sq, D), dtype=torch.int8, device=dev)
    k8 = torch.empty((B, H, Sk, D), dtype=torch.int8, device=dev)
    qs = torch.empty((BH, Sq if q_per_token else n_qt), dtype=torch.float32, device=dev)
    ks = torch.empty((BH, Sk if k_per_token else n_kt), dtype=torch.float32, device=dev)
    if v_fp8:
        vq = torch.empty((B, H, D, Sk), dtype=torch.float8_e4m3fn, device=dev)
        s_von, s_vod = 1, vq.stride(2)
    else:
        vq = torch.empty((B, H, Sk, D), dtype=torch.float16, device=dev)
        s_von, s_vod = vq.stride(2), 1
    store_qkm = bool(want_qkm and smooth_k)
    qkm = torch.empty((BH, Sq) if store_qkm else (1,), dtype=torch.float32, device=dev)
    sol_kw = {}
    if want_kc:
        n_kt_pad = triton.cdiv(n_kt, _SOL_PAD) * _SOL_PAD
        kc8 = torch.zeros((BH, n_kt_pad, D), dtype=torch.int8, device=dev)
        kcs = torch.zeros((BH, n_kt_pad), dtype=torch.float32, device=dev)
        if v_fp8 and sol_pv8():
            vc = torch.zeros((BH, D, n_kt_pad), dtype=torch.uint8, device=dev).view(torch.float8_e4m3fn)
        else:
            vc = torch.zeros((BH, n_kt_pad, D), dtype=torch.float16, device=dev)
        kcnt = torch.zeros((BH, n_kt_pad), dtype=torch.float32, device=dev)
        sol_kw = dict(KC8=kc8, KCS=kcs, VC=vc, KCNT=kcnt, N_KT_PAD=n_kt_pad, STORE_KC=True,
                      VC_FP8=vc.dtype == torch.float8_e4m3fn)

    _quant_kernel[(max(n_qt, n_kt), BH)](
        q, k, v, kvm, srcu, kmean, v_inv,
        q8, qs, k8, ks, vq, qkm,
        q.stride(0), q.stride(1), q.stride(2),
        k.stride(0), k.stride(1), k.stride(2),
        v.stride(0), v.stride(1), v.stride(2),
        vq.stride(0), vq.stride(1), s_von, s_vod,
        H, Sq, Sk,
        float(sm_scale) * _LOG2E, float(sm_scale),
        TQ=tile_q, TK=tile_k, HEAD_DIM=D,
        Q_PER_TOKEN=q_per_token, K_PER_TOKEN=k_per_token,
        HAS_KV_MASK=has_mask, SMOOTH_K=smooth_k, V_FP8=v_fp8, STORE_QKM=store_qkm, GATHER=gather,
        num_warps=4, **sol_kw,
    )
    if want_kc:
        return q8, qs, k8, ks, vq, v_scale_eff, kvm, has_mask, qkm, (kc8, kcs, vc, kcnt)
    return q8, qs, k8, ks, vq, v_scale_eff, kvm, has_mask, qkm


# =====================================================================
# Sol correction switches / helpers
# =====================================================================
def sol_enabled(explicit: Optional[bool] = None) -> bool:
    """explicit (e.g. bsa_params['sol']) wins; otherwise PRISM_SOL=1 enables the
    approximate correction of unlisted k tiles. Default off."""
    if explicit is not None:
        return bool(explicit)
    return os.environ.get("PRISM_SOL", "0").lower() in ("1", "true", "on", "yes")


_SOL_PAD = 128     # tile summaries are padded to a multiple of this (every SOL_G in 32..128 divides it)


def sol_group(device=None) -> int:
    """k tiles per proxy step: PRISM_SOL_G, else 64 on SM90 (wgmma; H200, 720p, 0.85:
    +7.2 ms per 8 heads with 64, +9.5 with 32) and 32 on the mma.sync archs (SM8x/SM12x:
    64 spills at BLOCK_M=128 there)."""
    env = os.environ.get("PRISM_SOL_G")
    if env:
        g = int(env)
        assert g in (32, 64, 128), "PRISM_SOL_G must be 32, 64 or 128"
        return g
    return 64 if device_arch(device) == (9, 0) else 32


def sol_pv8() -> bool:
    """FP8 proxy P.V when the PV mode is fp8 (PRISM_SOL_PV8, default on)."""
    return os.environ.get("PRISM_SOL_PV8", "1") != "0"


def _bits_to_words(m):
    """bool [N] (N % 32 == 0) -> int32 [N // 32], bit t % 32 of word t // 32."""
    w = (m.view(-1, 32).to(torch.int64) << torch.arange(32, device=m.device)).sum(-1)
    return torch.where(w >= 2 ** 31, w - 2 ** 32, w).to(torch.int32)


def sel_bitmask(block_indices, block_indices_lens, H, n_kt, n_kt_pad, empty=None):
    """int32 [B*H, n_rows, n_kt_pad // 32]: bit t set where tile t is listed in a row
    (or is flagged in `empty`, bool [n_kt_pad], for every row)."""
    B, _, n_rows, _ = block_indices.shape
    nw = n_kt_pad // 32
    if empty is None:
        out = torch.zeros((B * H, n_rows, nw), dtype=torch.int32, device=block_indices.device)
    else:
        out = _bits_to_words(empty).view(1, 1, nw).expand(B * H, n_rows, nw).contiguous()
    if n_rows:
        _sel_bitmask_kernel[(n_rows, B * H)](
            block_indices, block_indices_lens, out,
            block_indices.stride(0), block_indices.stride(1), block_indices.stride(2), block_indices.stride(3),
            block_indices_lens.stride(0), block_indices_lens.stride(1), block_indices_lens.stride(2),
            H, n_rows, nw, n_kt, CH=256, num_warps=4)
    return out


def _sol_args(sol_t, block_indices, block_indices_lens, H, n_kt, dev):
    """Attention-kernel keyword args for SOL ({} when sol_t is None: the launch is then
    exactly the pre-Sol one)."""
    if sol_t is None:
        return {}
    kc8, kcs, vc, kcnt = sol_t
    n_kt_pad = kc8.shape[1]
    # empty tiles (no valid key, incl. the padding up to n_kt_pad) are flagged like listed
    # ones, so the kernel's mask is the bitmask alone; counts are head-independent
    selm = sel_bitmask(block_indices, block_indices_lens, H, n_kt, n_kt_pad, empty=kcnt[0] == 0)
    return dict(SOL=True, SOL_G=sol_group(dev), SOL_PV8=vc.dtype == torch.float8_e4m3fn,
                KC8=kc8, KCS=kcs, VC=vc, KCNT=kcnt, SELM=selm, N_ROWS=selm.shape[1], N_WORDS=selm.shape[2],
                N_KT_PAD=n_kt_pad)


def pair_same_flags(block_indices, block_indices_lens, H):
    """int8 [B*H, N_q_tiles // 2]: 1 where q tiles 2p and 2p+1 have identical k lists."""
    B = block_indices.shape[0]
    n_pairs = block_indices.shape[2] // 2
    out = torch.empty((B * H, max(n_pairs, 1)), dtype=torch.int8, device=block_indices.device)
    if n_pairs == 0:
        out.zero_()
        return out, 0
    _pair_same_kernel[(n_pairs, B * H)](
        block_indices, block_indices_lens, out,
        block_indices.stride(0), block_indices.stride(1), block_indices.stride(2), block_indices.stride(3),
        block_indices_lens.stride(0), block_indices_lens.stride(1), block_indices_lens.stride(2),
        H, n_pairs, CH=512, num_warps=4,
    )
    return out, n_pairs


# =====================================================================
# Public forward
# =====================================================================
def sage_bsa_forward(
    q_re: torch.Tensor, k_re: torch.Tensor, v_re: torch.Tensor, sm_scale: float,
    block_indices: torch.Tensor, block_indices_lens: torch.Tensor,
    kv_valid_mask: Optional[torch.Tensor] = None,
    pv: str = "auto",
    *,
    chunk_size_q: int = 64, chunk_size_k: int = 64,
    merge: bool = True,
    pair_flags: Optional[torch.Tensor] = None,
    q_per_token: bool = True, k_per_token: bool = False,
    smooth_k: bool = True,
    head_chunk: Optional[int] = 8,
    return_lse: bool = False,
    sol: Optional[bool] = None,
):
    """Quantized drop-in for ``attn_fwd_bsa_varlen_triton(...)[0]``.

    sol: add the Sol-style approximate contribution of the k tiles a row does not
    list (None: PRISM_SOL env, default off; see _sol_proxy).

    q_re/k_re/v_re: [B,H,S,D] (bf16/fp16, last dim contiguous), tile-contiguous
    layout; block_indices [B,H,Sq//chunk_size_q, L] (any int dtype),
    block_indices_lens [B,H,Sq//chunk_size_q]; kv_valid_mask [Sk] bool or None.
    Returns o [B,H,Sq,D] in q's dtype (and natural-log lse [B,H,Sq] fp32 if
    return_lse). merge=True runs adjacent 64-row q tiles with identical lists
    as one BLOCK_M=128 program (pair_flags [B,H,Nq//2] or [Nq//2] may be
    precomputed by the caller). Heads are processed head_chunk at a time so the
    INT8/FP8 copies cost ~(2+|V|) bytes * head_chunk * S * D instead of all heads.
    """
    B, H, Sq, D = q_re.shape
    Sk = k_re.shape[2]
    assert D in (64, 128), "head_dim must be 64 or 128"
    assert chunk_size_q in (64, 128) and chunk_size_k in (64, 128)
    assert Sq % chunk_size_q == 0 and Sk % chunk_size_k == 0
    assert block_indices.shape[2] == Sq // chunk_size_q
    pv = resolve_pv(pv, q_re.device)
    if pv is None:
        raise RuntimeError("sage_bsa: unsupported device (needs SM80+)")
    if q_re.stride(-1) != 1:
        q_re = q_re.contiguous()
    if k_re.stride(-1) != 1:
        k_re = k_re.contiguous()
    if v_re.stride(-1) != 1:
        v_re = v_re.contiguous()
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
        assert pair_flags.shape[-1] == n_qt // 2

    sol = sol_enabled(sol)
    o = torch.empty_like(q_re)
    lse = torch.empty((B, H, Sq), dtype=torch.float32, device=q_re.device) if return_lse else None
    hc = H if not head_chunk else max(1, min(H, int(head_chunk)))
    for h0 in range(0, H, hc):
        h1 = min(H, h0 + hc)
        sl = slice(h0, h1)
        lse_c = _forward_heads(
            q_re[:, sl], k_re[:, sl], v_re[:, sl], sm_scale, block_indices[:, sl], block_indices_lens[:, sl],
            kv_valid_mask, pv, o[:, sl], chunk_size_q, chunk_size_k, do_merge,
            None if pair_flags is None or not do_merge else pair_flags[:, sl],
            q_per_token, k_per_token, smooth_k, return_lse, sol,
        )
        if return_lse:
            lse[:, sl] = lse_c
    if return_lse:
        return o, lse
    return o


def _forward_heads(q_re, k_re, v_re, sm_scale, block_indices, block_indices_lens, kv_valid_mask, pv, o,
                   chunk_size_q, chunk_size_k, do_merge, pair_flags, q_per_token, k_per_token, smooth_k,
                   return_lse, sol=False):
    """One head chunk: quantize, (pair flags), attention into the o view."""
    B, H, Sq, D = q_re.shape
    Sk = k_re.shape[2]
    dev = q_re.device
    qz = quantize_qkv(
        q_re, k_re, v_re, sm_scale, kv_valid_mask, pv, chunk_size_q, chunk_size_k,
        q_per_token, k_per_token, smooth_k, want_qkm=return_lse, want_kc=sol,
    )
    return _attend_prequant(qz, block_indices, block_indices_lens, o, pv, chunk_size_q, chunk_size_k,
                            do_merge, pair_flags, q_per_token, k_per_token, smooth_k, return_lse)


def _attend_prequant(qz, block_indices, block_indices_lens, o, pv, chunk_size_q=64, chunk_size_k=64,
                     do_merge=True, pair_flags=None, q_per_token=True, k_per_token=False, smooth_k=True,
                     return_lse=False):
    """Attention kernel launch(es) on already-quantized inputs (qz = quantize_qkv(...));
    a 10-element qz (want_kc=True) enables the Sol correction."""
    q8, qs, k8, ks, vq, vs, kvm, has_mask, qkm = qz[:9]
    sol_t = qz[9] if len(qz) > 9 else None
    B, H, Sq, D = q8.shape
    Sk = k8.shape[2]
    dev = q8.device
    lse = torch.empty((B, H, Sq), dtype=torch.float32, device=dev) if return_lse else \
        torch.empty(1, dtype=torch.float32, device=dev)
    n_qt = Sq // chunk_size_q
    do_merge = do_merge and chunk_size_q == 64 and n_qt >= 2
    if do_merge:
        if pair_flags is None:
            pair, n_pairs = pair_same_flags(block_indices, block_indices_lens, H)
        else:
            pair = pair_flags.reshape(B * H, -1).contiguous()
            n_pairs = pair.shape[1]
    else:
        pair, n_pairs = torch.zeros(1, dtype=torch.int8, device=dev), 0

    if pv == "fp8":
        s_vn, s_vd = 1, vq.stride(2)
    else:
        s_vn, s_vd = vq.stride(2), 1
    colb = _colbias(kvm, has_mask, Sk, dev)
    cinit = _cinit(kvm, has_mask, Sk, dev)
    plan = launch_plan(dev, pv, Sq, k_per_token)
    kopts = plan["opts"]
    common = (
        q8, k8, vq, qs, ks, vs, o, lse, qkm,
        block_indices, block_indices_lens, pair, kvm, pair, pair, pair, colb, cinit,
        o.stride(0), o.stride(1), o.stride(2),
        vq.stride(0), vq.stride(1), s_vn, s_vd,
        block_indices.stride(0), block_indices.stride(1), block_indices.stride(2), block_indices.stride(3),
        block_indices_lens.stride(0), block_indices_lens.stride(1), block_indices_lens.stride(2),
        H, Sq, Sk, n_pairs,
    )
    kw = dict(
        HEAD_DIM=D, TQ=chunk_size_q, BLOCK_N=chunk_size_k, PV_MODE=_PV_CODE[pv],
        Q_PER_TOKEN=q_per_token, K_PER_TOKEN=k_per_token,
        HAS_KV_MASK=has_mask, STORE_LSE=return_lse, LSE_SHIFT=bool(return_lse and smooth_k),
        **kopts,
    )
    kw.update(_sol_args(sol_t, block_indices, block_indices_lens, H, Sk // chunk_size_k, dev))
    if do_merge:
        w, s = plan["cfg128"]
        _sage_bsa_attn_kernel[(n_pairs, B * H)](*common, BLOCK_M=128, MODE=1, num_warps=w, num_stages=s, **kw)
        w, s = plan["cfg64"]
        _sage_bsa_attn_kernel[(n_qt, B * H)](*common, BLOCK_M=64, MODE=2, num_warps=w, num_stages=s, **kw)
    else:
        w, s = plan["cfg64"] if chunk_size_q <= 64 else plan["cfg128"]
        _sage_bsa_attn_kernel[(n_qt, B * H)](*common, BLOCK_M=chunk_size_q, MODE=0, num_warps=w, num_stages=s, **kw)
    return lse if return_lse else None


# =====================================================================
# Monkeypatch for the Prism inference path
# =====================================================================
ORIGINAL_attn_fwd_bsa_varlen_triton = None
_PATCH_STATE = {"pv": "auto", "merge": True, "q_per_token": True, "k_per_token": False, "head_chunk": 8,
                "active": False, "disabled": set(), "calls": 0, "fallbacks": 0}


def _sage_supported(q, k, v, chunk_size_q, chunk_size_k):
    if not (q.is_cuda and q.dtype in (torch.bfloat16, torch.float16)):
        return False
    if q.dtype != k.dtype or k.dtype != v.dtype:
        return False
    if q.shape[-1] not in (64, 128) or k.shape[-1] != q.shape[-1] or v.shape[-1] != q.shape[-1]:
        return False
    if chunk_size_q not in (64, 128) or chunk_size_k not in (64, 128):
        return False
    if q.shape[2] % chunk_size_q or k.shape[2] % chunk_size_k:
        return False
    if torch.is_grad_enabled() and (q.requires_grad or k.requires_grad or v.requires_grad):
        return False  # training: keep the exact kernel so backward stays consistent
    return auto_pv(q.device) is not None


def _patched_attn_fwd_bsa_varlen_triton(q, k, v, sm_scale, block_indices, block_indices_lens,
                                        chunk_size_q, chunk_size_k, sparsity, kv_valid_mask=None):
    orig = ORIGINAL_attn_fwd_bsa_varlen_triton
    if _sage_supported(q, k, v, chunk_size_q, chunk_size_k):
        pv = resolve_pv(_PATCH_STATE["pv"], q.device)
        chain = {"fp8": ["fp8", "fp16"], "fp16acc": ["fp16acc", "fp16"], "fp16": ["fp16"]}[pv]
        for mode in chain:
            key = (q.device.index, mode)
            if key in _PATCH_STATE["disabled"]:
                continue
            try:
                out = sage_bsa_forward(
                    q, k, v, sm_scale, block_indices, block_indices_lens, kv_valid_mask, pv=mode,
                    chunk_size_q=chunk_size_q, chunk_size_k=chunk_size_k,
                    merge=_PATCH_STATE["merge"], q_per_token=_PATCH_STATE["q_per_token"],
                    k_per_token=_PATCH_STATE["k_per_token"], head_chunk=_PATCH_STATE["head_chunk"],
                    return_lse=True,
                )
                _PATCH_STATE["calls"] += 1
                return out
            except torch.cuda.OutOfMemoryError:
                raise
            except Exception as e:  # compile / resource failure on this arch -> next mode
                _PATCH_STATE["disabled"].add(key)
                _warn_once(f"pv={mode} failed on {torch.cuda.get_device_name(q.device)} "
                           f"({type(e).__name__}: {str(e)[:200]}); falling back")
    _PATCH_STATE["fallbacks"] += 1
    return orig(q, k, v, sm_scale, block_indices, block_indices_lens,
                chunk_size_q, chunk_size_k, sparsity, kv_valid_mask=kv_valid_mask)


def patch_prism(pv: str = "auto", merge: bool = True, q_per_token: bool = True, k_per_token: bool = False,
                head_chunk: Optional[int] = 8):
    """Route Prism's BSA forward kernel through the Sage kernel (inference calls).

    Covers the IVPQ dynamic path (_dyn_bsa_kernel imports the function lazily),
    the uniform-shape BSA paths in bsa_interface, and bias_rectification.
    Idempotent; call unpatch_prism() to restore. Returns the original function.
    """
    global ORIGINAL_attn_fwd_bsa_varlen_triton
    from .block_sparse_attention import bsa_interface
    if ORIGINAL_attn_fwd_bsa_varlen_triton is None:
        cur = bsa_interface.attn_fwd_bsa_varlen_triton
        ORIGINAL_attn_fwd_bsa_varlen_triton = getattr(cur, "_sage_original", cur)
    if pv not in (None, "auto") and pv not in PV_MODES:
        raise ValueError(pv)
    _PATCH_STATE.update(pv=pv or "auto", merge=merge, q_per_token=q_per_token,
                        k_per_token=k_per_token, head_chunk=head_chunk, active=True)
    _patched_attn_fwd_bsa_varlen_triton._sage_original = ORIGINAL_attn_fwd_bsa_varlen_triton
    bsa_interface.attn_fwd_bsa_varlen_triton = _patched_attn_fwd_bsa_varlen_triton
    return ORIGINAL_attn_fwd_bsa_varlen_triton


def unpatch_prism():
    from .block_sparse_attention import bsa_interface
    if ORIGINAL_attn_fwd_bsa_varlen_triton is not None:
        bsa_interface.attn_fwd_bsa_varlen_triton = ORIGINAL_attn_fwd_bsa_varlen_triton
    _PATCH_STATE["active"] = False


def original_bsa_forward(q_re, k_re, v_re, sm_scale, block_indices, block_indices_lens,
                         kv_valid_mask=None, chunk_size_q=64, chunk_size_k=64):
    """The unpatched kernel's output (for A/B), regardless of patch state."""
    from .block_sparse_attention import bsa_interface
    fn = ORIGINAL_attn_fwd_bsa_varlen_triton or bsa_interface.attn_fwd_bsa_varlen_triton
    o, _ = fn(q_re, k_re, v_re, sm_scale, block_indices, block_indices_lens,
              chunk_size_q, chunk_size_k, None, kv_valid_mask=kv_valid_mask)
    return o


__all__ = [
    "sage_bsa_forward", "quantize_qkv", "pair_same_flags", "patch_prism", "unpatch_prism",
    "original_bsa_forward", "auto_pv", "resolve_pv", "PV_MODES",
]
