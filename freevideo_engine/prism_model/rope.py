# Prism (MIT, Tencent): vendored from the Prism single-GPU research branch
# (Prism-fast 0befcb7, hymm/fast/rope.py) for FreeVideo; see NOTICE.
"""One-pass RoPE (optionally fused with the q/k RMSNorm) for inference.

Reference (wan_video_dit.rope_apply_head_dim): x -> float64 complex, times the
complex128 `freqs` [S, 1, D/2], -> bf16. Here every element is computed in fp32
"double-float" arithmetic: cos/sin are split into hi+lo fp32 pairs, products are
made exact with FMA (TwoProd) and sums compensated (TwoSum), giving ~2^-45
relative error before the single rounding to the output dtype. That is far below
bf16 resolution, so the output matches the fp64 reference except in vanishingly
rare tie cases (the test reports the mismatch count; it was 0 at 720p). No fp64
arithmetic per element (consumer GPUs run fp64 at 1/64 rate); the fp64 table is
read per token and split into hi/lo once per (token, frequency).

fused_norm_rope(x, weight, eps, freqs, head_dim): RMSNorm over the full feature
row (x_f32 * rstd * w_f32, rounded to x.dtype, like torch's nn.RMSNorm and
hymm.fast.qblock.FusedRMSNorm), then RoPE on that rounded value - one read and
one write of x instead of two of each. In place when out=x.

Switch: PRISM_FAST_ROPE=0 restores the fp64 torch path.
"""
from __future__ import annotations

import os
from typing import Optional

import torch
import triton
import triton.language as tl
from triton.language.extra import libdevice


def enabled() -> bool:
    return os.environ.get("PRISM_FAST_ROPE", "1") != "0"


@triton.jit
def _two_prod(a, b):
    p = a * b
    return p, tl.fma(a, b, -p)


@triton.jit
def _two_sum(a, b):
    s = a + b
    bb = s - a
    return s, (a - (s - bb)) + (b - bb)


@triton.jit
def _rot(xe, xo, c_hi, c_lo, s_hi, s_lo):
    """(xe + i xo) * (c + i s) with c = c_hi + c_lo, s = s_hi + s_lo; xe/xo exact fp32."""
    p1, e1 = _two_prod(xe, c_hi)
    p2, e2 = _two_prod(xo, s_hi)
    r, er = _two_sum(p1, -p2)
    re = r + (er + (e1 - e2) + (xe * c_lo - xo * s_lo))
    p3, e3 = _two_prod(xe, s_hi)
    p4, e4 = _two_prod(xo, c_hi)
    i, ei = _two_sum(p3, p4)
    im = i + (ei + (e3 + e4) + (xe * s_lo + xo * c_lo))
    return re, im


@triton.jit
def _rstd_kernel(X, RSTD, n_rows, stride_x, K, eps, ROWS: tl.constexpr, CW: tl.constexpr):
    rows = tl.program_id(0) * ROWS + tl.arange(0, ROWS)
    rm = rows < n_rows
    c = tl.arange(0, CW)
    acc = tl.zeros([ROWS, CW], dtype=tl.float32)
    for c0 in range(0, K, CW):
        m = rm[:, None] & ((c0 + c) < K)[None, :]
        x = tl.load(X + rows.to(tl.int64)[:, None] * stride_x + (c0 + c)[None, :], mask=m, other=0.0).to(tl.float32)
        acc += x * x
    ss = tl.sum(acc, axis=1)
    tl.store(RSTD + rows, libdevice.rsqrt(ss / K + eps), mask=rm)


@triton.jit
def _norm_rope_kernel(X, W, RSTD, F, Y, n_rows, S_TOK, NH, stride_x, stride_y, stride_f,
                      HALF: tl.constexpr, ROWS: tl.constexpr, HB: tl.constexpr,
                      HAS_NORM: tl.constexpr, HAS_W: tl.constexpr):
    """grid (head groups, row blocks): ROWS tokens x HB heads per program. Rows,
    cos/sin and output are moved as contiguous [ROWS, 2*HALF] tiles and split into
    even/odd lanes in registers; the cos/sin hi/lo split is reused for HB heads."""
    hg = tl.program_id(0)
    rows = tl.program_id(1) * ROWS + tl.arange(0, ROWS)
    rm = (rows < n_rows)[:, None]
    c2 = tl.arange(0, 2 * HALF)
    fr = F + (rows % S_TOK).to(tl.int64)[:, None] * stride_f + c2[None, :]
    cs = tl.load(fr, mask=rm, other=0.0)                                   # fp64 [ROWS, 2*HALF]
    c, s = tl.split(tl.reshape(cs, [ROWS, HALF, 2]))
    c_hi = c.to(tl.float32)
    s_hi = s.to(tl.float32)
    c_lo = (c - c_hi.to(tl.float64)).to(tl.float32)
    s_lo = (s - s_hi.to(tl.float64)).to(tl.float32)
    if HAS_NORM:
        rstd = tl.load(RSTD + rows, mask=rows < n_rows, other=0.0)[:, None]
    for hh in tl.static_range(HB):
        h = hg * HB + hh
        hm = rm & (h < NH)
        col = h * (2 * HALF) + c2
        x = tl.load(X + rows.to(tl.int64)[:, None] * stride_x + col[None, :], mask=hm, other=0.0).to(tl.float32)
        if HAS_NORM:
            x = x * rstd
            if HAS_W:
                x = x * tl.load(W + col, mask=col < NH * 2 * HALF, other=0.0).to(tl.float32)[None, :]
            # the reference rounds the norm output to the activation dtype before RoPE
            x = x.to(Y.dtype.element_ty).to(tl.float32)
        xe, xo = tl.split(tl.reshape(x, [ROWS, HALF, 2]))
        re, im = _rot(xe, xo, c_hi, c_lo, s_hi, s_lo)
        y = tl.reshape(tl.join(re, im), [ROWS, 2 * HALF])
        tl.store(Y + rows.to(tl.int64)[:, None] * stride_y + col[None, :], y.to(Y.dtype.element_ty), mask=hm)


def _freq_table(freqs: torch.Tensor, half: int):
    """complex128 [S, 1, half] (or [S, half]) -> float64 [S, half, 2] view + row stride."""
    f = freqs
    if f.dim() == 3:
        assert f.shape[1] == 1
        f = f[:, 0]
    assert f.shape[-1] == half, (f.shape, half)
    if f.dtype != torch.complex128:
        f = f.to(torch.complex128)
    fr = torch.view_as_real(f)
    if fr.stride(-1) != 1 or fr.stride(-2) != 2:
        fr = fr.contiguous()
    return fr, fr.stride(0)


def supported(x: torch.Tensor, freqs, head_dim: int) -> bool:
    return (enabled() and x.is_cuda and x.dim() == 3 and x.dtype in (torch.bfloat16, torch.float16)
            and torch.is_tensor(freqs) and freqs.is_complex() and head_dim % 2 == 0
            and x.shape[-1] % head_dim == 0 and freqs.shape[0] == x.shape[1]
            and not (torch.is_grad_enabled() and x.requires_grad))


def fused_norm_rope(x: torch.Tensor, weight: Optional[torch.Tensor], eps: Optional[float], freqs: torch.Tensor,
                    head_dim: int, *, norm: bool = True, out: Optional[torch.Tensor] = None) -> torch.Tensor:
    """x [B, S, H*head_dim] -> RoPE(RMSNorm(x)) (norm=False: RoPE only), same dtype."""
    B, S, HD = x.shape
    nh = HD // head_dim
    half = head_dim // 2
    if x.stride(-1) != 1:
        x = x.contiguous()
    x2 = x.reshape(B * S, HD)
    if out is None:
        out = torch.empty_like(x)
    y2 = out.view(B * S, HD)
    fr, stride_f = _freq_table(freqs, half)
    if fr.device != x.device:
        fr = fr.to(x.device)
    n_rows = B * S
    if eps is None:
        eps = torch.finfo(x.dtype).eps
    has_norm = bool(norm)
    rows_per = 16
    with torch.cuda.device(x.device):
        if has_norm:
            rstd = torch.empty(n_rows, dtype=torch.float32, device=x.device)
            _rstd_kernel[(triton.cdiv(n_rows, 8),)](x2, rstd, n_rows, x2.stride(0), HD, float(eps),
                                                    ROWS=8, CW=512, num_warps=4)
        else:
            rstd = x2
        hb = 8
        _norm_rope_kernel[(triton.cdiv(nh, hb), triton.cdiv(n_rows, rows_per))](
            x2, weight if (has_norm and weight is not None) else x2, rstd, fr, y2, n_rows, S, nh,
            x2.stride(0), y2.stride(0), stride_f,
            HALF=half, ROWS=rows_per, HB=hb, HAS_NORM=has_norm, HAS_W=has_norm and weight is not None,
            num_warps=4, enable_fp_fusion=False)
    return out


def rope(x: torch.Tensor, freqs: torch.Tensor, head_dim: int, out: Optional[torch.Tensor] = None) -> torch.Tensor:
    return fused_norm_rope(x, None, None, freqs, head_dim, norm=False, out=out)


def norm_params(norm_mod):
    """(weight, eps) if norm_mod is a plain nn.RMSNorm over the last dim, else None."""
    if not isinstance(norm_mod, torch.nn.RMSNorm):
        return None
    if len(norm_mod.normalized_shape) != 1:
        return None
    w = norm_mod.weight
    if w is not None and (w.requires_grad and torch.is_grad_enabled() and norm_mod.training):
        return None
    return w, norm_mod.eps


__all__ = ["fused_norm_rope", "rope", "supported", "norm_params", "enabled"]
