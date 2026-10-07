# Prism (MIT, Tencent): vendored from the Prism single-GPU research branch
# (Prism-fast 0befcb7, hymm/fast/qblock.py) for FreeVideo; see NOTICE. FreeVideo
# changes: no sequence parallelism; precomputed audio token norms pass through.
"""Fused inference forward of the Wan/MOVA ``DiTBlock`` when its Linears are QLinear.

``DiTBlock.forward`` (hymm/models/modules/wan_video_dit.py) calls
``dit_block_forward`` when ``applicable(block, x)``; otherwise (plain bf16
nn.Linear everywhere, training, autograd, sequence parallel, torch.compile,
PRISM_FUSED_BLOCK=0, ``block._fused_block = False``) the original code runs
unchanged. Per block it removes these full-tensor passes:

  norm1 + modulate + 3x act-quant (q/k/v)  -> one LN+modulate+quant pass that also
                                              writes the bf16 input (shared by q/k/v)
  x + gate_msa * self_attn.o(...)          -> o GEMM epilogue
  norm3 + act-quant (cross_attn.q)         -> one LN(affine)+quant pass (+ bf16 copy)
  x + cross_attn.o(...)                    -> o GEMM epilogue
  norm2 + modulate + act-quant (ffn.0)     -> one LN+modulate+quant pass (no bf16 copy)
  GELU(tanh) over [M, ffn_dim]             -> ffn.0 GEMM epilogue
  x + gate_mlp * ffn.2(...)                -> ffn.2 GEMM epilogue

SelfAttention / CrossAttention are not modified: their q/k/v/o are reached
through two QLinear side channels (``x._qlinear_prequant`` on the tensor passed
in, ``o._pending_epilogue`` consumed by the next call of o). Both rely on the
attention modules calling ``self.q/k/v(x)`` on the tensor they receive and
returning ``self.o(...)`` unmodified; if an epilogue is not consumed the gate is
applied here, and a lost prequant only costs a re-quantization.
"""
from __future__ import annotations

import os

import torch
import torch.nn as nn

from . import qlinear as ql

ENABLED = os.environ.get('PRISM_FUSED_BLOCK', '1') != '0'
# Residual/gate GEMM epilogues (o, cross o, ffn.2). On H200 at 720p they save ~2 ms on
# N=13824 and break even on N=5120 (epilogue not overlapped with the main loop); the
# bandwidth they save is worth more on ~1 TB/s consumer cards. 0 = apply gates unfused.
RESIDUAL_EPILOGUE = os.environ.get('PRISM_FUSED_RESIDUAL', '1') != '0'
_STATS = dict(calls=0, o_epilogue_missed=0)


def _is_q(m) -> bool:
    return getattr(type(m), '_is_prism_qlinear', False)


def _w8a8(m, device) -> bool:
    """W8A8 QLinear with a native backend. Prologue fusion works with any of them;
    residual/gate epilogues are fused only by 'triton' (others apply them unfused
    inside w8a8_gemm, still correct)."""
    return _is_q(m) and m.mode.startswith('w8a8') and m.resolved_backend(device) != 'ref'


def _sp_enabled() -> bool:
    return False  # single GPU: no sequence parallelism in the vendored model


def _compiling() -> bool:
    try:
        return torch.compiler.is_compiling()
    except Exception:
        return False


def _linears(block):
    sa, ca, ffn = block.self_attn, block.cross_attn, block.ffn
    return (sa.q, sa.k, sa.v, sa.o, ca.q, ca.o, ffn[0], ffn[-1])


def applicable(block, x) -> bool:
    if not ENABLED or getattr(block, '_fused_block', True) is False or block.training:
        return False
    if x.device.type != 'cuda' or x.dtype not in (torch.bfloat16, torch.float16) or x.dim() != 3:
        return False
    if torch.is_grad_enabled() and x.requires_grad:
        return False
    if _compiling() or _sp_enabled():
        return False
    return any(_is_q(m) for m in _linears(block))


def _shared_spec(block, mods, device) -> bool:
    """q/k/v quantized with one activation spec (quantize_model(share=wan_share_key))?
    Checked once per (module identities) with torch.equal, then cached."""
    key = tuple(id(m) for m in mods) + tuple(id(getattr(m, 'act_mult', None)) for m in mods)
    cache = block.__dict__.setdefault('_qblock_spec_cache', {})
    hit = cache.get(key)
    if hit is not None:
        return hit
    ok = all(_w8a8(m, device) for m in mods)
    if ok:
        f = mods[0]
        for m in mods[1:]:
            if (m.mode, m.rot_block, m.act_asym, m.in_features) != (f.mode, f.rot_block, f.act_asym, f.in_features):
                ok = False
            elif (m.act_mult is None) != (f.act_mult is None):
                ok = False
            elif m.act_mult is not None and m.act_mult is not f.act_mult and not torch.equal(m.act_mult, f.act_mult):
                ok = False
    cache.clear()
    cache[key] = ok
    return ok


def _quant_ln(qmod, x2, norm, shift=None, scale=None, want_y=False):
    had = qmod._hadamard_for(x2.device, torch.float16 if x2.dtype == torch.float16 else torch.bfloat16) \
        if qmod.rot_block else None
    w = getattr(norm, 'weight', None)
    b = getattr(norm, 'bias', None)
    return ql.quantize_activation_ln(x2, norm.eps, w, b, shift, scale, qmod.mode == 'w8a8_fp8', qmod.act_mult,
                                     qmod.rot_block, qmod.act_asym, had, 'triton', want_y=want_y)


def _gate_ok(gate, x):
    return gate.dtype == x.dtype and gate.shape[-1] == x.shape[-1]


def _epi(m, device) -> bool:
    """Residual/gate epilogue is only fused by the Triton W8A8 GEMM."""
    return RESIDUAL_EPILOGUE and _is_q(m) and m.mode.startswith('w8a8') and m.resolved_backend(device) == 'triton'


def _call_with_epilogue(omod, fn, x, gate):
    """Run ``fn()`` (an attention module whose last op is ``omod(...)``) with
    residual/gate fused into omod's epilogue. Returns (out, fused)."""
    omod._pending_epilogue = (x, gate)
    try:
        out = fn()
    finally:
        pending = omod._pending_epilogue
        omod._pending_epilogue = None
    if pending is None:
        return out, True
    _STATS['o_epilogue_missed'] += 1
    return out, False


def dit_block_forward(block, x, context, t_mod, freqs, grid_size=None, a2v_bridge_residual=None,
                      timestep_ratio=None, audio_token_norms=None):
    from .wan_video_dit import modulate
    _STATS['calls'] += 1
    has_seq = len(t_mod.shape) == 4
    chunk_dim = 2 if has_seq else 1
    shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
        block.modulation.to(dtype=t_mod.dtype, device=t_mod.device) + t_mod).chunk(6, dim=chunk_dim)
    if has_seq:
        shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
            shift_msa.squeeze(2), scale_msa.squeeze(2), gate_msa.squeeze(2),
            shift_mlp.squeeze(2), scale_mlp.squeeze(2), gate_mlp.squeeze(2),
        )
    sa, ca, ffn = block.self_attn, block.cross_attn, block.ffn
    dev = x.device
    B, S, D = x.shape
    same_dtype = all(t.dtype == x.dtype for t in (shift_msa, scale_msa, shift_mlp, scale_mlp))

    # 1. self-attention: LN1 + modulate + quant once for q/k/v; o epilogue = x + gate_msa * o(.)
    if same_dtype and _shared_spec(block, (sa.q, sa.k, sa.v), dev):
        xq, sx, ox, y = _quant_ln(sa.q, x.reshape(-1, D), block.norm1, shift_msa, scale_msa, want_y=True)
        input_x = y.view(B, S, D)
        input_x._qlinear_prequant = (frozenset((id(sa.q), id(sa.k), id(sa.v))), xq, sx, ox)
        del xq, sx, ox, y
    else:
        input_x = modulate(block.norm1(x), shift_msa, scale_msa)

    def run_sa():
        return sa(input_x, freqs, grid_size=grid_size, a2v_bridge_residual=a2v_bridge_residual,
                  timestep_ratio=timestep_ratio, audio_token_norms=audio_token_norms)
    if _epi(sa.o, dev) and _gate_ok(gate_msa, x):
        out, fused = _call_with_epilogue(sa.o, run_sa, x, gate_msa)
        x = out if fused else block.gate(x, gate_msa, out)
    else:
        x = block.gate(x, gate_msa, run_sa())
    if hasattr(input_x, '_qlinear_prequant'):
        del input_x._qlinear_prequant
    del input_x

    # 2. cross-attention: LN3(affine) + quant for q; o epilogue = x + o(.)
    if _w8a8(ca.q, dev):
        xq, sx, ox, y = _quant_ln(ca.q, x.reshape(-1, D), block.norm3, want_y=True)
        nx = y.view(B, S, D)
        nx._qlinear_prequant = (frozenset((id(ca.q),)), xq, sx, ox)
        del xq, sx, ox, y
    else:
        nx = block.norm3(x)

    def run_ca():
        return ca(nx, context)
    if _epi(ca.o, dev):
        out, fused = _call_with_epilogue(ca.o, run_ca, x, None)
        x = out if fused else x + out
    else:
        x = x + run_ca()
    if hasattr(nx, '_qlinear_prequant'):
        del nx._qlinear_prequant
    del nx

    # 3. FFN: LN2 + modulate + quant -> ffn.0 (+GELU epilogue) -> ffn.2 (+ x + gate_mlp * . epilogue)
    f0, f2 = ffn[0], ffn[-1]
    mid = list(ffn)[1:-1]
    gelu_mid = len(mid) == 1 and isinstance(mid[0], nn.GELU) and mid[0].approximate == 'tanh'
    ident_mid = len(mid) == 1 and isinstance(mid[0], nn.Identity)
    if same_dtype and _w8a8(f0, dev) and (gelu_mid or ident_mid):
        xq, sx, ox, _ = _quant_ln(f0, x.reshape(-1, D), block.norm2, shift_mlp, scale_mlp)
        act = 'gelu_tanh' if gelu_mid else f0.act
        h = f0.forward_quantized(xq, sx, ox, x.dtype, act=act).view(B, S, -1)
        del xq, sx, ox
    else:
        h = modulate(block.norm2(x), shift_mlp, scale_mlp)
        for m in list(ffn)[:-1]:
            h = m(h)
    if _epi(f2, dev) and _gate_ok(gate_mlp, x):
        x = f2(h, residual=x, gate=gate_mlp)
    else:
        x = block.gate(x, gate_mlp, f2(h))
    return x


def stats():
    return dict(_STATS)


# ---------------------------------------------------------------------------
# One-pass RMSNorm for the q/k norms (torch 2.7 runs nn.RMSNorm as ~7 fp32
# elementwise/reduce kernels over [M, 5120]: ~13 ms per 720p q or k tensor).
# ---------------------------------------------------------------------------
try:
    import triton
    import triton.language as tl
    from triton.language.extra import libdevice

    @triton.jit
    def _rmsnorm_kernel(X, W, Y, M, K, stride_x, stride_y, eps, n_blocks,
                        BK: tl.constexpr, ROWS: tl.constexpr, HAS_W: tl.constexpr):
        pid = tl.program_id(0)
        nprog = tl.num_programs(0)
        r = tl.arange(0, ROWS)
        c = tl.arange(0, BK)
        kmask = c < K
        if HAS_W:
            w = tl.load(W + c, mask=kmask, other=0.).to(tl.float32)
        for blk in range(pid, n_blocks, nprog):
            rows = blk * ROWS + r
            mask = (rows < M)[:, None] & kmask[None, :]
            x = tl.load(X + rows.to(tl.int64)[:, None] * stride_x + c[None, :], mask=mask, other=0.).to(tl.float32)
            rstd = libdevice.rsqrt(tl.sum(x * x, axis=1) / K + eps)
            # torch 2.7 nn.RMSNorm (bf16): x_f32 * rstd * w_f32, rounded once (checked empirically)
            y = x * rstd[:, None]
            if HAS_W:
                y = y * w[None, :]
            tl.store(Y + rows.to(tl.int64)[:, None] * stride_y + c[None, :], y.to(Y.dtype.element_ty), mask=mask)
    _HAS_TRITON = True
except Exception:  # pragma: no cover
    _HAS_TRITON = False


def rms_norm(x: torch.Tensor, weight, eps: float) -> torch.Tensor:
    k = x.shape[-1]
    x2 = x.reshape(-1, k)
    if x2.stride(-1) != 1:
        x2 = x2.contiguous()
    m = x2.shape[0]
    y = torch.empty((m, k), device=x.device, dtype=x.dtype)
    if m == 0:
        return y.view(x.shape)
    bk = triton.next_power_of_2(k)
    rows = max(1, min(16, 4096 // bk))
    warps = 4 if bk * rows <= 4096 else 8
    n_blocks = triton.cdiv(m, rows)
    sms = torch.cuda.get_device_properties(x.device).multi_processor_count
    grid = (max(1, min(n_blocks, sms * 8)),)
    with torch.cuda.device(x.device):
        _rmsnorm_kernel[grid](x2, weight if weight is not None else x2, y, m, k, x2.stride(0), y.stride(0),
                              float(eps), n_blocks, BK=bk, ROWS=rows, HAS_W=weight is not None, num_warps=warps)
    return y.view(x.shape)


class FusedRMSNorm(nn.RMSNorm):
    """nn.RMSNorm whose CUDA inference forward is one Triton pass (bf16/fp16 in,
    same dtype out, same rounding as torch 2.7's nn.RMSNorm up to the fp32 sum order). Training / autograd / CPU /
    fp32 use nn.RMSNorm.forward. Swapped in by fuse_norms(); state_dict unchanged."""

    def forward(self, x):
        if (_HAS_TRITON and x.is_cuda and x.dtype in (torch.bfloat16, torch.float16) and
                not (torch.is_grad_enabled() and (x.requires_grad or (self.weight is not None and
                                                                        self.weight.requires_grad and self.training)))
                and len(self.normalized_shape) == 1 and not _compiling() and
                (self.weight is None or self.weight.dtype == x.dtype)):
            eps = self.eps if self.eps is not None else torch.finfo(x.dtype).eps
            return rms_norm(x, self.weight, eps)
        return super().forward(x)


def fuse_norms(model: nn.Module, enable: bool = True) -> int:
    """Swap every nn.RMSNorm (q/k norms of self/cross attention and the bridge) to
    FusedRMSNorm in place (enable=False swaps back). Returns the number changed."""
    n = 0
    for mod in model.modules():
        if enable and type(mod) is nn.RMSNorm:
            mod.__class__ = FusedRMSNorm
            n += 1
        elif not enable and type(mod) is FusedRMSNorm:
            mod.__class__ = nn.RMSNorm
            n += 1
    return n


# ---------------------------------------------------------------------------
# Bridge conditioners (interactionv2.ConditionalCrossAttentionBlock), inference.
#   v2a (video -> audio): y_norm(LayerNorm, 187k video tokens) + k/v projections share
#                         one LN+quant pass; norm_k + rotate-half RoPE in one pass.
#   a2v (audio -> video): q projection on the video tokens, norm_q + RoPE in one pass.
# ---------------------------------------------------------------------------
COND_ENABLED = os.environ.get('PRISM_FUSED_COND', '1') != '0'
_STATS.update(cond_calls=0)

if _HAS_TRITON:
    @triton.jit
    def _rmsnorm_rope_kernel(X, W, COS, SIN, Y, M, L, H, stride_x, stride_y, stride_c, eps, n_blocks,
                             HB: tl.constexpr, HALF: tl.constexpr, HAS_ROPE: tl.constexpr):
        """Row = H heads x 2*HALF dims. y = rmsnorm(x) * w over the whole row, then per
        head: out[:HALF] = y1*cos[:HALF] - y2*sin[:HALF], out[HALF:] = y2*cos[HALF:] + y1*sin[HALF:]
        (HF rotate_half convention, cos/sin [L, 2*HALF] per token, shared by the heads)."""
        pid = tl.program_id(0)
        nprog = tl.num_programs(0)
        hh = tl.arange(0, HB)
        dd = tl.arange(0, HALF)
        hmask = hh < H
        D: tl.constexpr = 2 * HALF
        off1 = hh[:, None] * D + dd[None, :]
        mask = hmask[:, None]
        w1 = tl.load(W + off1, mask=mask, other=0.).to(tl.float32)
        w2 = tl.load(W + off1 + HALF, mask=mask, other=0.).to(tl.float32)
        K = H * D
        for row in range(pid, n_blocks, nprog):
            xb = X + row.to(tl.int64) * stride_x
            x1 = tl.load(xb + off1, mask=mask, other=0.).to(tl.float32)
            x2 = tl.load(xb + off1 + HALF, mask=mask, other=0.).to(tl.float32)
            ss = tl.sum(tl.sum(x1 * x1, axis=1), axis=0) + tl.sum(tl.sum(x2 * x2, axis=1), axis=0)
            rstd = libdevice.rsqrt(ss / K + eps)
            y1 = x1 * rstd * w1
            y2 = x2 * rstd * w2
            if HAS_ROPE:
                tok = row % L
                cb = COS + tok.to(tl.int64) * stride_c
                sb = SIN + tok.to(tl.int64) * stride_c
                c1 = tl.load(cb + dd).to(tl.float32)[None, :]
                c2 = tl.load(cb + HALF + dd).to(tl.float32)[None, :]
                s1 = tl.load(sb + dd).to(tl.float32)[None, :]
                s2 = tl.load(sb + HALF + dd).to(tl.float32)[None, :]
                o1 = y1 * c1 - y2 * s1
                o2 = y2 * c2 + y1 * s2
            else:
                o1 = y1
                o2 = y2
            yb = Y + row.to(tl.int64) * stride_y
            tl.store(yb + off1, o1.to(Y.dtype.element_ty), mask=mask)
            tl.store(yb + off1 + HALF, o2.to(Y.dtype.element_ty), mask=mask)


def rmsnorm_rope(x, weight, eps, head_dim, freqs=None):
    """rope(rms_norm(x) * weight) for x [B, L, H*head_dim]; freqs = (cos, sin) [1|B, L, head_dim]."""
    b, l, k = x.shape
    x2 = x.reshape(-1, k)
    if x2.stride(-1) != 1:
        x2 = x2.contiguous()
    y = torch.empty((b * l, k), device=x.device, dtype=x.dtype)
    h = k // head_dim
    if freqs is not None:
        cos, sin = freqs
        cos = cos.reshape(-1, head_dim)
        sin = sin.reshape(-1, head_dim)
        assert cos.shape[0] == l and cos.stride(-1) == 1 and sin.stride(0) == cos.stride(0), 'per-token cos/sin'
    else:
        cos = sin = x2
    m = b * l
    if m == 0:
        return y.view(b, l, k)
    sms = torch.cuda.get_device_properties(x.device).multi_processor_count
    grid = (max(1, min(m, sms * 16)),)
    with torch.cuda.device(x.device):
        _rmsnorm_rope_kernel[grid](x2, weight, cos, sin, y, m, l, h, x2.stride(0), y.stride(0),
                                   cos.stride(0) if freqs is not None else 0, float(eps), m,
                                   HB=triton.next_power_of_2(h), HALF=head_dim // 2, HAS_ROPE=freqs is not None,
                                   num_warps=4)
    return y.view(b, l, k)


def cond_applicable(cond, x, y) -> bool:
    if not (ENABLED and COND_ENABLED and _HAS_TRITON) or cond.training or getattr(cond, 'pooled_adaln', False):
        return False
    inner = cond.inner
    if getattr(inner, 'enable_bsa', False) and inner.bsa_params.get('sparsity', 0) > 0:
        return False
    if x.device.type != 'cuda' or x.dtype not in (torch.bfloat16, torch.float16) or x.dim() != 3 or y.dim() != 3:
        return False
    if torch.is_grad_enabled() and (x.requires_grad or y.requires_grad):
        return False
    if _compiling() or _sp_enabled():
        return False
    if not all(isinstance(n, nn.RMSNorm) and n.weight is not None and n.weight.dtype == x.dtype
               for n in (inner.norm_q, inner.norm_k)):
        return False
    return any(_is_q(m) for m in (inner.q, inner.k, inner.v, inner.o))


def _rope_ok(freqs, l, head_dim):
    if freqs is None:
        return True
    cos, sin = freqs
    return (cos.dim() == 3 and cos.shape[0] == 1 and cos.shape[1] == l and cos.shape[2] == head_dim and
            sin.shape == cos.shape and cos.is_cuda)


def cond_forward(cond, x, y, x_freqs=None, y_freqs=None, q_structure='1d', k_structure='1d',
                 q_grid_size=None, k_grid_size=None):
    """= ConditionalCrossAttentionBlock.forward (non-pooled, dense attention, no SP).
    Returns None (caller runs the original) when the RoPE tables are not per-token [1, L, D]."""
    inner = cond.inner
    hd = inner.head_dim
    dev = x.device
    bq, lq, _ = x.shape
    bk, lk, kd = y.shape
    if not (_rope_ok(x_freqs, lq, hd) and _rope_ok(y_freqs, lk, hd)):
        return None
    _STATS['cond_calls'] += 1
    x_freqs = None if x_freqs is None else tuple(t.to(x.dtype) for t in x_freqs)
    y_freqs = None if y_freqs is None else tuple(t.to(x.dtype) for t in y_freqs)
    # keys / values: y_norm + k/v projections (one LN+quant pass when k and v share a spec)
    if _shared_spec(cond, (inner.k, inner.v), dev) and y.dtype == x.dtype:
        yq, ys, yo, _ = _quant_ln(inner.k, y.reshape(-1, kd), cond.y_norm)
        kk = inner.k.forward_quantized(yq, ys, yo, y.dtype).view(bk, lk, -1)
        vv = inner.v.forward_quantized(yq, ys, yo, y.dtype).view(bk, lk, -1)
        del yq, ys, yo
    else:
        yn = cond.y_norm(y)
        kk, vv = inner.k(yn), inner.v(yn)
        del yn
    kk = rmsnorm_rope(kk, inner.norm_k.weight, inner.norm_k.eps or torch.finfo(kk.dtype).eps, hd, y_freqs)
    cap = getattr(inner, '_capture', None)  # hymm.fast.audio_regen capture hook
    if cap is not None:
        cap.put(kk, vv, None)
        if not hasattr(cap, 'arope'):
            cap.arope = x_freqs
    q = rmsnorm_rope(inner.q(x), inner.norm_q.weight, inner.norm_q.eps or torch.finfo(x.dtype).eps, hd, x_freqs)
    return inner.o(inner.attn(q, kk, vv))
