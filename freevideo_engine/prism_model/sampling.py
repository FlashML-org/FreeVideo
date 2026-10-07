# Prism denoising forward and sampler for FreeVideo. Derived from the Prism
# single-GPU fast path (Prism-fast 3910631, hymm/fast/pipeline.py:
# bridge_forward, _forward_audio_cfg, fast_generate; MIT, Tencent) and the MOVA
# pipeline (Apache-2.0, OpenMOSS). FreeVideo changes:
#   * the transformer is split into per-layer "units" (video block of the active
#     expert + audio block + a2v/v2a conditioners) that a caller may keep on the
#     GPU or stream (``residency`` callback) -- one expert is materialized at a
#     time;
#   * an optional memory-lean execution of the same arithmetic for small GPUs
#     (``lean=True``): projections, FFN and the cross-attentions run in token
#     chunks, RoPE and residual updates are in place, the self-attention output
#     overwrites q, and the v2a key/value projections are shared between the
#     conditional and unconditional audio streams.
# With lean=False the per-layer code path is the research path.
from contextlib import contextmanager, nullcontext
from pathlib import Path
import math
import os
import time

import torch
import torch.nn as nn

from . import qblock, qlinear
from .interactionv2 import ConditionalCrossAttentionBlock, DualTowerConditionalBridge
from .mova import assemble_audio_freqs, assemble_visual_freqs
from .wan_audio_dit import WanAudioModel
from .wan_video_dit import (DiTBlock, WanModel, advance_dynamic_block_pass_id, flash_attention, norm_rope,
                            rope_apply_head_dim, sinusoidal_embedding_1d)


# ---------------------------------------------------------------------------
# Module tree
# ---------------------------------------------------------------------------
class PrismUnit(nn.Module):
    """One streamed layer of one expert: ``video`` (+ ``audio``, ``a2v``, ``v2a``
    for the fused layers). Parameter names match the prepared files
    (``NN.<role>.<module path>``) after the ``NN.`` prefix."""

    def __init__(self, video, audio=None, a2v=None, v2a=None):
        super().__init__()
        self.video = video
        self.audio = audio
        self.a2v = a2v
        self.v2a = v2a

    @property
    def fused(self):
        return self.audio is not None


def _cfg(configs, name):
    return {k: v for k, v in configs[name].items() if not k.startswith('_')}


def interaction(configs):
    bridge = _cfg(configs, 'dual_tower_bridge')
    from .interactionv2 import CrossModalInteractionController
    video_layers = configs['video_dit']['num_layers']
    audio_layers = configs['audio_dit']['num_layers']
    mapping = CrossModalInteractionController(video_layers, audio_layers).get_interaction_layers(
        bridge.get('interaction_strategy', 'shallow_focus'))
    return {i for i, _ in mapping['a2v']}, {i for i, _ in mapping['v2a']}, min(video_layers, audio_layers)


def build_unit(configs, index, bsa_params, *, ivpq=True):
    """Meta-device unit ``index`` of one expert (weights bound by the loader)."""
    video = _cfg(configs, 'video_dit')
    audio = _cfg(configs, 'audio_dit')
    bridge = _cfg(configs, 'dual_tower_bridge')
    a2v_layers, v2a_layers, fused = interaction(configs)
    with torch.device('meta'):
        vblock = DiTBlock(False, video['dim'], video['num_heads'], video['ffn_dim'], video['eps'],
                          enable_bsa=bsa_params is not None, bsa_params=dict(bsa_params or {}))
        if ivpq and bsa_params is not None:
            vblock.self_attn.enable_ivpq_dynamic_block = True
        ablock = a2v = v2a = None
        if index < fused:
            ablock = DiTBlock(False, audio['dim'], audio['num_heads'], audio['ffn_dim'], audio['eps'])
            head_dim = bridge.get('head_dim', 128)
            vd, ad = bridge['visual_hidden_dim'], bridge['audio_hidden_dim']
            if index in a2v_layers:
                a2v = ConditionalCrossAttentionBlock(dim=vd, kv_dim=ad, num_heads=vd // head_dim)
            if index in v2a_layers:
                v2a = ConditionalCrossAttentionBlock(dim=ad, kv_dim=vd, num_heads=ad // head_dim)
    return PrismUnit(vblock, ablock, a2v, v2a).eval().requires_grad_(False)


def build_roots(configs):
    """Meta-device roots: both video experts (no blocks), the audio DiT (no
    blocks) and the bridge (RoPE alignment only)."""
    with torch.device('meta'):
        high = WanModel(**dict(_cfg(configs, 'video_dit'), num_layers=0))
        low = WanModel(**dict(_cfg(configs, 'video_dit_2'), num_layers=0))
        audio = WanAudioModel(**dict(_cfg(configs, 'audio_dit'), num_layers=0))
        bridge = DualTowerConditionalBridge(**dict(_cfg(configs, 'dual_tower_bridge'), visual_layers=0,
                                                   audio_layers=0))
    return {'high': high.eval().requires_grad_(False), 'low': low.eval().requires_grad_(False),
            'audio': audio.eval().requires_grad_(False), 'bridge': bridge.eval().requires_grad_(False)}


def swap_qlinears(module, metadata, prefix=''):
    """Replace the nn.Linear modules named in ``metadata`` (qlinear save format,
    names relative to ``module`` after ``prefix``) by meta QLinear modules."""
    count = 0
    for full, md in metadata.items():
        if not full.startswith(prefix):
            continue
        name = full[len(prefix):]
        parent_name, _, leaf = name.rpartition('.')
        parent = module.get_submodule(parent_name) if parent_name else module
        q = qlinear.QLinear(md['in_features'], md['out_features'], md['mode'], bias=md['bias'],
                            rot_block=md['rot_block'], has_mult=md['has_mult'], act_asym=md['act_asym'],
                            bias_dtype=getattr(torch, md['bias_dtype']) if md['bias_dtype'] else torch.bfloat16,
                            device='meta')
        setattr(parent, leaf, q)
        count += 1
    return count


def fuse_norms(module):
    return qblock.fuse_norms(module, enable=True)


# FP8 PV with FP16 accumulation for the Sage attention (prism_model.sage_bsa_f16acc):
# PRISM_SAGE_F16ACC defaults to 'auto' (RTX 40 / 50 with Triton >= 3.7: same speed as FP8
# PV there, H200-level accuracy); '1' forces it on any supported GPU, '0' turns it off.
if os.environ.get('PRISM_SAGE_F16ACC', 'auto').strip().lower() not in ('0', 'off', 'false', 'no'):
    from . import sage_bsa_f16acc
    sage_bsa_f16acc.install()


# ---------------------------------------------------------------------------
# Lean (memory-bounded) per-layer execution
# ---------------------------------------------------------------------------
# Small GPUs (policy park_residual): the residual stream is not read during
# self-attention, so its 1.9 GB (720p) wait in page-locked host memory while q, k, v
# and the attention buffers use the GPU: the block's peak drops from four hidden
# states to three. The copies are exact; ~80 ms each way over PCIe 4.0 x16.
PARK_RESIDUAL = False
_PARK_BUFFERS = {}


def _park(x):
    if not PARK_RESIDUAL or x.device.type != 'cuda' or not x.is_contiguous() or x.storage_offset():
        return None
    nbytes = x.numel() * x.element_size()
    if x.untyped_storage().nbytes() != nbytes:
        return None  # x is a view of a larger storage
    buffer = _PARK_BUFFERS.get(nbytes)
    if buffer is None:
        buffer = _PARK_BUFFERS[nbytes] = host_buffer(nbytes)[0]
    host = buffer.view(x.dtype).view(x.shape)
    host.copy_(x, non_blocking=True)
    x.untyped_storage().resize_(0)  # stream-ordered free: reused only after the copy
    return host


def _unpark(x, host):
    if host is None:
        return
    x.untyped_storage().resize_(x.numel() * x.element_size())
    x.copy_(host, non_blocking=True)


def release_park_buffers():
    for buffer in _PARK_BUFFERS.values():
        if buffer.is_pinned():
            unregister(buffer)
    _PARK_BUFFERS.clear()


def _passed():
    """After a model pass: collect reference cycles now. On an RTX 4070 a cycle
    (an exception's frames, seen after Triton resource retries) kept the previous
    pass's 1.8 GB hidden state alive into the next pass until the cyclic
    collector happened to run; gc.collect freed 2.2 GiB there."""
    import gc
    gc.collect()


def _release(*tensors):
    """Give the device memory of a lean block's attention buffers back now. A
    reference left elsewhere (seen on an RTX 4070: q, k and v of one block stayed
    allocated after their last use, and the next block ran out of memory) must
    not keep 1.8 GB each alive. Stream-ordered, as a free: work already queued on
    this stream still reads them."""
    for t in tensors:
        if t is not None and t.is_cuda:
            t.untyped_storage().resize_(0)


def _rows(n, chunk):
    for r0 in range(0, n, chunk):
        yield r0, min(n, r0 + chunk)


# cuBLAS picks bf16 GEMM kernels (split-K, tiles) by M, so a row's result can
# depend on how many rows share the call. The lean paths run their bf16 GEMMs
# (LoRA branch, plain bf16 blocks, head) on fixed pieces of GEMM_ROWS rows from
# row 0; a policy chunk that is a multiple of it (16384, 4096) then gives the
# same latents. INT8/FP8 GEMMs are row-exact at any M.
GEMM_ROWS = 4096


def _gemm(lin, xq, sx, ox, r0, r1, dtype, act=None):
    return lin.forward_quantized(xq[r0:r1], sx[r0:r1], None if ox is None else ox[r0:r1], dtype, act=act)


def _gated_update(lin, inp, x2, r0, r1, gate2, dev):
    """x2[r0:r1] = x2[r0:r1] + gate * lin(inp) as the research block does it:
    fused into the Triton GEMM epilogue when qblock would fuse it, otherwise the
    GateModule arithmetic (bf16 multiply, then add)."""
    res = x2[r0:r1]
    if qblock._epi(lin, dev) and (gate2 is None or gate2.dtype == x2.dtype):
        lin(inp, residual=res, gate=gate2, out=res)
    else:
        y = lin(inp)
        res.add_(y if gate2 is None else gate2 * y)


def _lean_ok(block, x):
    sa, ca, ffn = block.self_attn, block.cross_attn, block.ffn
    dev = x.device
    mods = (sa.q, sa.k, sa.v)
    return (qblock.applicable(block, x) and qblock._shared_spec(block, mods, dev)
            and all(qblock._w8a8(m, dev) for m in (sa.o, ca.q, ca.o, ffn[0], ffn[-1]))
            and isinstance(ffn[1], nn.GELU) and ffn[1].approximate == 'tanh')


@torch.no_grad()
def lean_dit_block(block, x, context, t_mod, freqs, grid_size, audio_token_norms=None, chunk=16384):
    """In-place DiTBlock forward (QLinear W8A8 blocks); returns x."""
    if not _lean_ok(block, x):
        return block(x, context, t_mod, freqs, grid_size=grid_size, audio_token_norms=audio_token_norms)
    sa, ca, ffn = block.self_attn, block.cross_attn, block.ffn
    dev, dt = x.device, x.dtype
    B, S, D = x.shape
    shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
        block.modulation.to(dtype=t_mod.dtype, device=t_mod.device) + t_mod).chunk(6, dim=1)
    if not all(t.dtype == dt for t in (shift_msa, scale_msa, shift_mlp, scale_mlp)):
        return block(x, context, t_mod, freqs, grid_size=grid_size, audio_token_norms=audio_token_norms)
    x2 = x.view(-1, D)
    rows = B * S
    # 1. self-attention
    xq, sx, ox, _ = qblock._quant_ln(sa.q, x2, block.norm1, shift_msa, scale_msa)
    parked = _park(x)  # QKV and attention read xq, not x
    q = torch.empty_like(x)
    k = torch.empty_like(x)
    v = torch.empty_like(x)
    q2, k2, v2 = q.view(-1, D), k.view(-1, D), v.view(-1, D)
    for r0, r1 in _rows(rows, chunk):
        # research path: norm_rope(norm, GEMM) -- per-token RMSNorm + RoPE, chunk-exact
        f = freqs[r0:r1]
        q2[r0:r1] = norm_rope(sa.norm_q, _gemm(sa.q, xq, sx, ox, r0, r1, dt).view(1, r1 - r0, D), f,
                              sa.head_dim).view(r1 - r0, D)
        k2[r0:r1] = norm_rope(sa.norm_k, _gemm(sa.k, xq, sx, ox, r0, r1, dt).view(1, r1 - r0, D), f,
                              sa.head_dim).view(r1 - r0, D)
        v2[r0:r1] = _gemm(sa.v, xq, sx, ox, r0, r1, dt)
    del xq, sx, ox
    use_bsa, _ = sa._check_bsa(grid_size)
    out = sa._fused_dynamic_bsa(q, k, v, grid_size, audio_token_norms, out=q) if use_bsa else None
    if out is None:
        if use_bsa:
            from einops import rearrange
            out = rearrange(sa._run_bsa(*(rearrange(t, "b s (n d) -> b n s d", n=sa.num_heads).contiguous()
                                          for t in (q, k, v)), grid_size, audio_token_norms=audio_token_norms),
                            "b n s d -> b s (n d)")
        else:
            out = sa.attn(q, k, v)
    _release(k, v)
    del q2, k2, v2, k, v
    _unpark(x, parked)
    out2 = out.view(-1, D)
    g = qlinear.row_table(gate_msa, 1, D)[0] if gate_msa is not None else None
    for r0, r1 in _rows(rows, chunk):
        _gated_update(sa.o, out2[r0:r1], x2, r0, r1, g, dev)
    _release(out, q)
    del out, out2, q
    # 2. cross-attention (text): one token chunk at a time
    xq, sx, ox, _ = qblock._quant_ln(ca.q, x2, block.norm3)
    ck = ca.norm_k(ca.k(context))
    cv = ca.v(context)
    for r0, r1 in _rows(rows, chunk):
        cq = ca.norm_q(_gemm(ca.q, xq, sx, ox, r0, r1, dt)).view(1, r1 - r0, D)
        att = ca.attn(cq, ck, cv).view(r1 - r0, D)
        _gated_update(ca.o, att, x2, r0, r1, None, dev)
        del cq, att
    del xq, sx, ox, ck, cv
    # 3. FFN: LN2 + modulate + quant, then GELU GEMM and gated residual per chunk
    f0, f2 = ffn[0], ffn[-1]
    xq, sx, ox, _ = qblock._quant_ln(f0, x2, block.norm2, shift_mlp, scale_mlp)
    g = qlinear.row_table(gate_mlp, 1, D)[0]
    for r0, r1 in _rows(rows, chunk):
        h = _gemm(f0, xq, sx, ox, r0, r1, dt, act='gelu_tanh')
        _gated_update(f2, h, x2, r0, r1, g, dev)
        del h
    del xq, sx, ox
    return x


def _plain_ok(block):
    sa, ca, ffn = block.self_attn, block.cross_attn, block.ffn
    return (all(type(m) is nn.Linear for m in (sa.q, sa.k, sa.v, sa.o, ca.q, ca.k, ca.v, ca.o, ffn[0], ffn[-1]))
            and isinstance(ffn[1], nn.GELU) and ffn[1].approximate == 'tanh')


@torch.no_grad()
def lean_dit_block_plain(block, x, context, t_mod, freqs, grid_size, audio_token_norms=None, chunk=16384):
    """In-place DiTBlock forward for plain (bf16) Linear blocks, in token chunks:
    the reference arithmetic (LayerNorm + compiled modulate, norm_rope, GateModule
    residuals); GEMMs run per chunk of at most GEMM_ROWS rows (cuBLAS may pick
    other kernels than for the full M, the only difference to the reference)."""
    from .wan_video_dit import modulate
    sa, ca, ffn = block.self_attn, block.cross_attn, block.ffn
    B, S, D = x.shape
    chunk = min(chunk, GEMM_ROWS)
    shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
        block.modulation.to(dtype=t_mod.dtype, device=t_mod.device) + t_mod).chunk(6, dim=1)
    q = torch.empty_like(x)
    k = torch.empty_like(x)
    v = torch.empty_like(x)
    for r0, r1 in _rows(S, chunk):
        y = modulate(block.norm1(x[:, r0:r1]), shift_msa, scale_msa)
        f = freqs[r0:r1]
        q[:, r0:r1] = norm_rope(sa.norm_q, sa.q(y), f, sa.head_dim)
        k[:, r0:r1] = norm_rope(sa.norm_k, sa.k(y), f, sa.head_dim)
        v[:, r0:r1] = sa.v(y)
        del y
    parked = _park(x)
    use_bsa, _ = sa._check_bsa(grid_size)
    out = sa._fused_dynamic_bsa(q, k, v, grid_size, audio_token_norms, out=q) if use_bsa else None
    if out is None:
        out = sa.attn(q, k, v)
    _release(k, v)
    del k, v
    _unpark(x, parked)
    for r0, r1 in _rows(S, chunk):
        x[:, r0:r1] = x[:, r0:r1] + gate_msa * sa.o(out[:, r0:r1])
    _release(out, q)
    del out, q
    ck = ca.norm_k(ca.k(context))
    cv = ca.v(context)
    for r0, r1 in _rows(S, chunk):
        cq = ca.norm_q(ca.q(block.norm3(x[:, r0:r1])))
        x[:, r0:r1] = x[:, r0:r1] + ca.o(ca.attn(cq, ck, cv))
        del cq
    del ck, cv
    for r0, r1 in _rows(S, chunk):
        y = modulate(block.norm2(x[:, r0:r1]), shift_mlp, scale_mlp)
        x[:, r0:r1] = x[:, r0:r1] + gate_mlp * ffn(y)
        del y
    return x


# ---------------------------------------------------------------------------
# Unmerged distill LoRA (student pass): base QLinear + x @ down^T @ up^T
# ---------------------------------------------------------------------------
def has_lora(block):
    return getattr(block.self_attn.q, 'lora_down', None) is not None


def _lora(lin, y):
    """Low-rank update of ``lin`` for bf16 rows ``y`` (+ its bias diff), or None."""
    down = getattr(lin, 'lora_down', None)
    out = None
    if down is not None:
        if y.shape[0] <= GEMM_ROWS:
            out = (y @ down.t()) @ lin.lora_up.t()
        else:
            out = y.new_empty((y.shape[0], lin.lora_up.shape[0]))
            for r0, r1 in _rows(y.shape[0], GEMM_ROWS):
                out[r0:r1] = (y[r0:r1] @ down.t()) @ lin.lora_up.t()
    diff_b = getattr(lin, 'lora_diff_b', None)
    if diff_b is not None:
        out = diff_b.expand(y.shape[0], -1) if out is None else out.add_(diff_b)
    return out


def _with_lora(lin, base, y):
    """base + LoRA(y) in place: (y @ down^T) @ up^T (+ bias diff) per GEMM_ROWS
    piece, so no full-size product is allocated beside ``base``."""
    down = getattr(lin, 'lora_down', None)
    diff_b = getattr(lin, 'lora_diff_b', None)
    if down is None:
        return base if diff_b is None else base.add_(diff_b.expand(y.shape[0], -1))
    flat = base.view(-1, base.shape[-1])
    for r0, r1 in _rows(y.shape[0], GEMM_ROWS):
        extra = (y[r0:r1] @ down.t()) @ lin.lora_up.t()
        if diff_b is not None:
            extra.add_(diff_b)
        flat[r0:r1].add_(extra)
        del extra
    return base


class _Norm:
    """A norm's student parameters (weight + lora_diff, bias + lora_diff_b)."""

    def __init__(self, norm):
        self.eps = norm.eps
        weight = getattr(norm, 'weight', None)
        bias = getattr(norm, 'bias', None)
        diff = getattr(norm, 'lora_diff', None)
        diff_b = getattr(norm, 'lora_diff_b', None)
        self.weight = weight + diff if (weight is not None and diff is not None) else weight
        self.bias = bias + diff_b if (bias is not None and diff_b is not None) else bias


def _rms(norm, x):
    from .qblock import rms_norm
    return rms_norm(x, norm.weight, norm.eps if norm.eps is not None else torch.finfo(x.dtype).eps)


def _rms_rope(norm, x2, freqs, head_dim):
    """Fused RMSNorm (student weight) + RoPE on rows x2 [r, H*D], in place."""
    from . import rope
    x = x2.view(1, x2.shape[0], x2.shape[1])
    if rope.supported(x, freqs, head_dim):
        eps = norm.eps if norm.eps is not None else torch.finfo(x.dtype).eps
        return rope.fused_norm_rope(x, norm.weight, eps, freqs, head_dim, out=x).view(x2.shape)
    return rope_apply_head_dim(_rms(norm, x), freqs, head_dim).view(x2.shape)


def _ln_rows(x2, r0, r1, norm, shift=None, scale=None):
    """bf16 LayerNorm (+ modulate) of rows r0:r1: the LoRA branch input."""
    return qlinear.quantize_activation_ln(x2[r0:r1], norm.eps, getattr(norm, 'weight', None),
                                          getattr(norm, 'bias', None), shift, scale, want_y=True, want_q=False)[3]


@torch.no_grad()
def lean_dit_block_lora(block, x, context, t_mod, freqs, grid_size, audio_token_norms=None, chunk=16384):
    """Student DiTBlock: the lean path of the base QLinears plus the unmerged
    distill LoRA (bf16 x @ down @ up), bias diffs and norm diffs. Residual
    updates are applied unfused (x += gate * (W8A8(h) + LoRA(h)))."""
    sa, ca, ffn = block.self_attn, block.cross_attn, block.ffn
    dev, dt = x.device, x.dtype
    B, S, D = x.shape
    mod = block.modulation
    if getattr(block, 'lora_diff_m', None) is not None:
        mod = mod + block.lora_diff_m
    shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
        mod.to(dtype=t_mod.dtype, device=t_mod.device) + t_mod).chunk(6, dim=1)
    x2 = x.view(-1, D)
    rows = B * S
    nq, nk = _Norm(sa.norm_q), _Norm(sa.norm_k)
    # 1. self-attention
    xq, sx, ox, _ = qblock._quant_ln(sa.q, x2, block.norm1, shift_msa, scale_msa)
    q = torch.empty_like(x)
    k = torch.empty_like(x)
    v = torch.empty_like(x)
    q2, k2, v2 = q.view(-1, D), k.view(-1, D), v.view(-1, D)
    for r0, r1 in _rows(rows, chunk):
        y = _ln_rows(x2, r0, r1, block.norm1, shift_msa, scale_msa)
        f = freqs[r0:r1]
        q2[r0:r1] = _rms_rope(nq, _with_lora(sa.q, _gemm(sa.q, xq, sx, ox, r0, r1, dt), y), f, sa.head_dim)
        k2[r0:r1] = _rms_rope(nk, _with_lora(sa.k, _gemm(sa.k, xq, sx, ox, r0, r1, dt), y), f, sa.head_dim)
        v2[r0:r1] = _with_lora(sa.v, _gemm(sa.v, xq, sx, ox, r0, r1, dt), y)
        del y
    del xq, sx, ox
    parked = _park(x)
    use_bsa, _ = sa._check_bsa(grid_size)
    out = sa._fused_dynamic_bsa(q, k, v, grid_size, audio_token_norms, out=q) if use_bsa else None
    if out is None:
        out = sa.attn(q, k, v)
    _release(k, v)
    del q2, k2, v2, k, v
    _unpark(x, parked)
    out2 = out.view(-1, D)
    g = qlinear.row_table(gate_msa, 1, D)[0]
    for r0, r1 in _rows(rows, chunk):
        a = out2[r0:r1]
        x2[r0:r1].add_(g * _with_lora(sa.o, sa.o(a), a))
    _release(out, q)
    del out, out2, q
    # 2. cross-attention
    n3 = _Norm(block.norm3)
    xq, sx, ox, _ = qblock._quant_ln(ca.q, x2, n3)
    cq_norm, ck_norm = _Norm(ca.norm_q), _Norm(ca.norm_k)
    ck = _rms(ck_norm, _with_lora(ca.k, ca.k(context), context.view(-1, D)).view(context.shape))
    cv = _with_lora(ca.v, ca.v(context), context.view(-1, D)).view(context.shape)
    for r0, r1 in _rows(rows, chunk):
        y = _ln_rows(x2, r0, r1, n3)
        cq = _rms(cq_norm, _with_lora(ca.q, _gemm(ca.q, xq, sx, ox, r0, r1, dt), y)).view(1, r1 - r0, D)
        att = ca.attn(cq, ck, cv).view(r1 - r0, D)
        x2[r0:r1].add_(_with_lora(ca.o, ca.o(att), att))
        del cq, att, y
    del xq, sx, ox, ck, cv
    # 3. FFN
    f0, f2 = ffn[0], ffn[-1]
    xq, sx, ox, _ = qblock._quant_ln(f0, x2, block.norm2, shift_mlp, scale_mlp)
    g = qlinear.row_table(gate_mlp, 1, D)[0]
    for r0, r1 in _rows(rows, chunk):
        y = _ln_rows(x2, r0, r1, block.norm2, shift_mlp, scale_mlp)
        h = _with_lora(f0, _gemm(f0, xq, sx, ox, r0, r1, dt), y)
        del y
        h = torch.nn.functional.gelu(h, approximate='tanh')
        x2[r0:r1].add_(g * _with_lora(f2, f2(h), h))
        del h
    del xq, sx, ox
    return x


def _lora_int8_ok(block):
    sa, ca, ffn = block.self_attn, block.cross_attn, block.ffn
    from . import qlora
    return (qlora.enabled() and all(qlora.supported(m) for m in (sa.q, sa.k, sa.v, sa.o, ca.q, ca.o, ffn[0], ffn[-1]))
            and isinstance(ffn[1], nn.GELU) and ffn[1].approximate == 'tanh')


def _gated_lora(lin, lq, inp, x2, r0, r1, gate):
    """x2[r0:r1] += [gate *] (lin(inp) + LoRA(inp)) with the INT8 LoRA ``lq``: the base
    GEMM and the LoRA up both add into the residual rows in their epilogues (bf16 x2
    rounded after each) when the gate dtype matches x2; otherwise one unfused add."""
    from . import qlora
    res = x2[r0:r1]
    xq, sx, ox = lin.quantize_input(inp)
    if gate is None or gate.dtype == x2.dtype:
        lin.forward_quantized(xq, sx, ox, residual=res, gate=gate, out=res)
        qlora.lora_into(lq, xq, sx, ox, [res], gates=None if gate is None else [gate])
    else:
        y = lin.forward_quantized(xq, sx, ox, x2.dtype)
        qlora.lora_into(lq, xq, sx, ox, [y])
        res.add_(gate * y)
        del y
    del xq, sx, ox


@torch.no_grad()
def lean_dit_block_lora_int8(block, x, context, t_mod, freqs, grid_size, audio_token_norms=None, chunk=16384):
    """lean_dit_block_lora with the distill LoRA on the INT8 GEMM (prism_model.qlora,
    FREEVIDEO_PRISM_LORA_INT8=1): each LoRA down runs on its base linear's quantized
    input (one GEMM for q/k/v), the up adds into the base output or the residual in
    the GEMM epilogue, so there is no bf16 LayerNorm pass and no bf16 GEMM. The
    cross-attention k/v of the context (a few hundred rows) keep the bf16 branch."""
    from . import qlora
    sa, ca, ffn = block.self_attn, block.cross_attn, block.ffn
    f0, f2 = ffn[0], ffn[-1]
    dev, dt = x.device, x.dtype
    B, S, D = x.shape
    mod = block.modulation
    if getattr(block, 'lora_diff_m', None) is not None:
        mod = mod + block.lora_diff_m
    shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
        mod.to(dtype=t_mod.dtype, device=t_mod.device) + t_mod).chunk(6, dim=1)
    x2 = x.view(-1, D)
    rows = B * S
    nq, nk = _Norm(sa.norm_q), _Norm(sa.norm_k)
    # INT8 LoRA weights of this call's block (see qlora: never cached across calls)
    lq_qkv, lq_o, lq_cq, lq_co, lq_f0, lq_f2 = (qlora.prepare(g) for g in (
        (sa.q, sa.k, sa.v), (sa.o,), (ca.q,), (ca.o,), (f0,), (f2,)))
    # 1. self-attention
    xq, sx, ox, _ = qblock._quant_ln(sa.q, x2, block.norm1, shift_msa, scale_msa)
    parked = _park(x)  # QKV and attention read xq, not x
    q = torch.empty_like(x)
    k = torch.empty_like(x)
    v = torch.empty_like(x)
    q2, k2, v2 = q.view(-1, D), k.view(-1, D), v.view(-1, D)
    for r0, r1 in _rows(rows, chunk):
        f = freqs[r0:r1]
        t = qlora.down(lq_qkv, xq[r0:r1], sx[r0:r1], None if ox is None else ox[r0:r1], dtype=dt)
        hq = _gemm(sa.q, xq, sx, ox, r0, r1, dt)
        q2[r0:r1] = _rms_rope(nq, qlora.up_add(lq_qkv, t, 0, hq), f, sa.head_dim)
        hk = _gemm(sa.k, xq, sx, ox, r0, r1, dt)
        k2[r0:r1] = _rms_rope(nk, qlora.up_add(lq_qkv, t, 1, hk), f, sa.head_dim)
        v2[r0:r1] = qlora.up_add(lq_qkv, t, 2, _gemm(sa.v, xq, sx, ox, r0, r1, dt))
        del t, hq, hk
    del xq, sx, ox
    use_bsa, _ = sa._check_bsa(grid_size)
    out = sa._fused_dynamic_bsa(q, k, v, grid_size, audio_token_norms, out=q) if use_bsa else None
    if out is None:
        out = sa.attn(q, k, v)
    _release(k, v)
    del q2, k2, v2, k, v
    _unpark(x, parked)
    out2 = out.view(-1, D)
    g = qlinear.row_table(gate_msa, 1, D)[0]
    for r0, r1 in _rows(rows, chunk):
        _gated_lora(sa.o, lq_o, out2[r0:r1], x2, r0, r1, g)
    _release(out, q)
    del out, out2, q
    # 2. cross-attention
    n3 = _Norm(block.norm3)
    xq, sx, ox, _ = qblock._quant_ln(ca.q, x2, n3)
    cq_norm, ck_norm = _Norm(ca.norm_q), _Norm(ca.norm_k)
    ck = _rms(ck_norm, _with_lora(ca.k, ca.k(context), context.view(-1, D)).view(context.shape))
    cv = _with_lora(ca.v, ca.v(context), context.view(-1, D)).view(context.shape)
    for r0, r1 in _rows(rows, chunk):
        hq = _gemm(ca.q, xq, sx, ox, r0, r1, dt)
        qlora.lora_into(lq_cq, xq, sx, ox, [hq], rows=slice(r0, r1))
        cq = _rms(cq_norm, hq).view(1, r1 - r0, D)
        att = ca.attn(cq, ck, cv).view(r1 - r0, D)
        _gated_lora(ca.o, lq_co, att, x2, r0, r1, None)
        del cq, att, hq
    del xq, sx, ox, ck, cv
    # 3. FFN
    xq, sx, ox, _ = qblock._quant_ln(f0, x2, block.norm2, shift_mlp, scale_mlp)
    g = qlinear.row_table(gate_mlp, 1, D)[0]
    for r0, r1 in _rows(rows, chunk):
        h = _gemm(f0, xq, sx, ox, r0, r1, dt)
        qlora.lora_into(lq_f0, xq, sx, ox, [h], rows=slice(r0, r1), act='gelu_tanh_post')  # GELU(base + LoRA)
        _gated_lora(f2, lq_f2, h, x2, r0, r1, g)
        del h
    del xq, sx, ox, lq_qkv, lq_o, lq_cq, lq_co, lq_f0, lq_f2
    return x


def video_block(block, x, context, t_mod, freqs, grid_size, audio_token_norms=None, chunk=16384, lean=True,
                variant='base', timestep_ratio=None):
    """One video DiTBlock in the requested variant: the student adds the
    distill LoRA when the block carries one; 'base' ignores it."""
    if variant == 'student' and has_lora(block):
        if _plain_ok(block):
            raise NotImplementedError('The distill LoRA student needs a quantized (int8/fp8) bundle')
        if _lora_int8_ok(block):
            return lean_dit_block_lora_int8(block, x, context, t_mod, freqs, grid_size, audio_token_norms, chunk)
        return lean_dit_block_lora(block, x, context, t_mod, freqs, grid_size, audio_token_norms, chunk)
    if lean and _plain_ok(block):
        return lean_dit_block_plain(block, x, context, t_mod, freqs, grid_size, audio_token_norms=audio_token_norms,
                                    chunk=chunk)
    if lean:
        return lean_dit_block(block, x, context, t_mod, freqs, grid_size, audio_token_norms=audio_token_norms,
                              chunk=chunk)
    return block(x, context, t_mod, freqs, grid_size=grid_size, timestep_ratio=timestep_ratio,
                 audio_token_norms=audio_token_norms)


def _chunked_rope(rope, t, freqs, chunk):
    """Apply ConditionalCrossAttention.rope_q / rope_k over token chunks of t
    ([1, S, H*D]) in place; freqs (cos, sin) are [1, S, head_dim]."""
    cos, sin = freqs
    for r0, r1 in _rows(t.shape[1], chunk):
        t[:, r0:r1] = rope(t[:, r0:r1], (cos[:, r0:r1], sin[:, r0:r1]))
    return t


def _slice_rope(freqs, r0, r1):
    return None if freqs is None else (freqs[0][:, r0:r1], freqs[1][:, r0:r1])


def _fused_cond(block, x):
    return qblock.cond_applicable(block, x, x)


def _cond_q(block, x, x_freqs):
    """qblock.cond_forward's query: fused RMSNorm + rotate-half RoPE."""
    inner = block.inner
    return qblock.rmsnorm_rope(inner.q(x), inner.norm_q.weight, inner.norm_q.eps or torch.finfo(x.dtype).eps,
                               inner.head_dim, None if x_freqs is None else tuple(t.to(x.dtype) for t in x_freqs))


def _cond_kv(block, y, y_freqs):
    """qblock.cond_forward's keys/values (shared y_norm + quant pass, fused k norm + RoPE)."""
    inner = block.inner
    b, l, kd = y.shape
    if qblock._shared_spec(block, (inner.k, inner.v), y.device):
        yq, ys, yo, _ = qblock._quant_ln(inner.k, y.reshape(-1, kd), block.y_norm)
        kk = inner.k.forward_quantized(yq, ys, yo, y.dtype).view(b, l, -1)
        vv = inner.v.forward_quantized(yq, ys, yo, y.dtype).view(b, l, -1)
        del yq, ys, yo
    else:
        yn = block.y_norm(y)
        kk, vv = inner.k(yn), inner.v(yn)
        del yn
    kk = qblock.rmsnorm_rope(kk, inner.norm_k.weight, inner.norm_k.eps or torch.finfo(kk.dtype).eps,
                             inner.head_dim, None if y_freqs is None else tuple(t.to(y.dtype) for t in y_freqs))
    return kk, vv


@torch.no_grad()
def lean_v2a_kv(block, video_x, vrope, chunk, fused=True):
    """Keys/values of a v2a conditioner over the video tokens, in token chunks
    (shared by the conditional and unconditional audio streams). ``fused``: the
    joint pass's qblock.cond_forward math; False: the teacher capture's
    (audio_regen.capture_v2a_kv: y_norm, k, norm_k, compiled RoPE)."""
    inner = block.inner
    B, S, _ = video_x.shape
    k = video_x.new_empty((B, S, inner.k.out_features))
    v = video_x.new_empty((B, S, inner.v.out_features))
    fused = fused and _fused_cond(block, video_x)
    for r0, r1 in _rows(S, chunk):
        if fused:
            k[:, r0:r1], v[:, r0:r1] = _cond_kv(block, video_x[:, r0:r1], _slice_rope(vrope, r0, r1))
            continue
        y = block.y_norm(video_x[:, r0:r1])
        k[:, r0:r1] = inner.norm_k(inner.k(y))
        v[:, r0:r1] = inner.v(y)
        del y
    if vrope is not None and not fused:
        _chunked_rope(inner.rope_k, k, vrope, chunk)
    return k, v


def v2a_with_kv(block, audio_x, kv, arope):
    """v2a output for audio queries against precomputed K/V (joint pass: fused query path)."""
    inner = block.inner
    if _fused_cond(block, audio_x):
        q = _cond_q(block, audio_x, arope)
    else:
        q = inner.norm_q(inner.q(audio_x))
        if arope is not None:
            q = inner.rope_q(q, arope)
    return inner.o(inner.attn(q, *kv))


@torch.no_grad()
def lean_a2v_update(block, video_x, audio_x, vrope, arope, chunk):
    """video_x += a2v(video_x, audio_x) in place (qblock.cond_forward math, query
    chunks over the video tokens); returns per-token ||a2v||_2."""
    inner = block.inner
    fused = _fused_cond(block, video_x)
    if fused:
        k, v = _cond_kv(block, audio_x, arope)
    else:
        y = block.y_norm(audio_x)
        k = inner.norm_k(inner.k(y))
        v = inner.v(y)
        if arope is not None:
            k = inner.rope_k(k, arope)
    B, S, D = video_x.shape
    norms = video_x.new_empty((S,))
    for r0, r1 in _rows(S, chunk):
        xr = video_x[:, r0:r1]
        if fused:
            q = _cond_q(block, xr, _slice_rope(vrope, r0, r1))
        else:
            q = inner.norm_q(inner.q(xr))
            if vrope is not None:
                q = inner.rope_q(q, _slice_rope(vrope, r0, r1))
        cond = inner.o(inner.attn(q, k, v))
        norms[r0:r1] = cond.norm(dim=-1)[0]
        xr.add_(cond)
        del q, cond
    return norms


# ---------------------------------------------------------------------------
# One transformer evaluation
# ---------------------------------------------------------------------------
class Context:
    """Per-forward shared inputs."""
    __slots__ = ('vctx', 'actx', 'actx_u', 'visual_t', 'visual_t_mod', 'audio_t', 'audio_t_mod', 'vfreqs',
                 'afreqs', 'vrope', 'arope', 'grid', 'ratio', 'f')


def _prologue(roots, expert, visual_latents, audio_latents, context, timestep, audio_context, audio_timestep,
              video_fps, num_train_timesteps, neg_audio_context):
    vdit, adit, br = roots[expert], roots['audio'], roots['bridge']
    if audio_context is None:
        audio_context = context
    if audio_timestep is None:
        audio_timestep = timestep
    c = Context()
    with torch.autocast("cuda", dtype=torch.float32):
        c.visual_t = vdit.time_embedding(sinusoidal_embedding_1d(vdit.freq_dim, timestep))
        c.visual_t_mod = vdit.time_projection(c.visual_t).unflatten(1, (6, vdit.dim))
        c.audio_t = adit.time_embedding(sinusoidal_embedding_1d(adit.freq_dim, audio_timestep))
        c.audio_t_mod = adit.time_projection(c.audio_t).unflatten(1, (6, adit.dim))
    dt = vdit.dtype
    c.visual_t, c.visual_t_mod = c.visual_t.to(dt), c.visual_t_mod.to(dt)
    c.audio_t, c.audio_t_mod = c.audio_t.to(dt), c.audio_t_mod.to(dt)
    c.vctx = vdit.text_embedding(context)
    c.actx = adit.text_embedding(audio_context)
    c.actx_u = adit.text_embedding(neg_audio_context) if neg_audio_context is not None else None
    vx, (t, h, w) = vdit.patchify(visual_latents.to(dt))
    c.grid = (t, h, w)
    c.vfreqs = assemble_visual_freqs(vdit.freqs, t, h, w, vx.device)
    ax, (f,) = adit.patchify(audio_latents.to(dt), None)
    c.f = f
    c.afreqs = assemble_audio_freqs(adit.freqs, f, ax.device)
    t_val = timestep.float().mean()
    c.ratio = t_val if t_val <= 1.0 else t_val / float(num_train_timesteps)
    if br.apply_cross_rope:
        c.vrope, c.arope = br.build_aligned_freqs(video_fps=video_fps, grid_size=c.grid, audio_steps=ax.shape[1],
                                                  device=vx.device, dtype=vx.dtype)
    else:
        c.vrope = c.arope = None
    return vx, ax, c


def _head(vdit, x, t, grid, lean, chunk):
    if not lean:
        return vdit.unpatchify(vdit.head(x, t), grid)
    out = None
    for r0, r1 in _rows(x.shape[1], min(chunk, GEMM_ROWS)):
        y = vdit.head(x[:, r0:r1], t)
        if out is None:
            out = y.new_empty((x.shape[0], x.shape[1], y.shape[-1]))
        out[:, r0:r1] = y
    return vdit.unpatchify(out, grid)


def root_key(roots, expert, variant):
    key = expert + '/student'
    return key if variant == 'student' and key in roots else expert


class FBCache:
    """First-block cache (research hymm.fast.pipeline FBCache in bridge_forward):
    after fused block 0, the relative L1 change of its video residual against the
    last full forward of the same (branch, expert) decides whether to reuse that
    forward's residuals of the remaining blocks (video and audio) instead of
    running them; at most ``max_skip`` reuses in a row. The video residuals live
    on the GPU or in registered host memory (``placement``), handled in row chunks
    of ``chunk``, the same arithmetic for both, so every plan takes the same
    decisions. A skipped forward rewinds the residency map: the remaining units
    are not streamed."""

    BUFFERS = 6  # two states (r1, rv) per branch, a snapshot and a spare r1

    def __init__(self, threshold, max_skip=3, placement='gpu', chunk=16384):
        self.threshold = None if threshold is None else float(threshold)
        self.max_skip = int(max_skip)
        self.placement = placement
        self.chunk = int(chunk)
        self.state = {}
        self.free = []
        self.owned = []
        self.skipped = self.total = 0
        self.events = []

    # buffers (uint8, the bytes of one hidden state) -------------------------
    def _get(self, like):
        nbytes = like.numel() * like.element_size()
        while self.free:
            buf = self.free.pop()
            if buf.numel() == nbytes:
                return buf
        if self.placement == 'gpu':
            buf = torch.empty(nbytes, dtype=torch.uint8, device=like.device)
        else:
            buf, kind = host_buffer(nbytes)
            if kind == 'registered':
                self.owned.append(buf)
        return buf

    def _put(self, buf):
        if buf is not None:
            self.free.append(buf)

    @staticmethod
    def _rows(buf, like):
        return buf.view(like.dtype).view(-1, like.shape[-1])

    def _load(self, rows, r0, r1, device):
        part = rows[r0:r1]
        if part.device.type == 'cuda':
            return part
        out = torch.empty(part.shape, dtype=part.dtype, device=device)
        out.copy_(part, non_blocking=True)
        return out

    def snapshot(self, x, buf=None):
        buf = buf if buf is not None else self._get(x)
        self._rows(buf, x).copy_(x.view(-1, x.shape[-1]), non_blocking=True)
        return buf

    def nbytes(self):
        return sum(b.numel() for b in self.owned) if self.placement != 'gpu' else \
            sum(b.numel() for st in self.state.values() for b in (st['r1'], st['rv']))

    # the two decision points -------------------------------------------------
    def after_first(self, key, vx, ax, snap):
        """After block 0 (``snap`` holds the video state before it). Returns
        (skip, ax, r1 buffer, snapshot buffer of the state after block 0)."""
        self.total += 1
        st = self.state.get(key)
        compare = st is not None and st['skips'] < self.max_skip
        x2 = vx.view(-1, vx.shape[-1])
        r1buf = self._get(vx)
        v0, r1v = self._rows(snap, vx), self._rows(r1buf, vx)
        prev = self._rows(st['r1'], vx) if compare else None
        num = torch.zeros((), dtype=torch.float32, device=vx.device)
        den = torch.zeros((), dtype=torch.float32, device=vx.device)
        for r0, r1 in _rows(x2.shape[0], self.chunk):
            rc = x2[r0:r1] - self._load(v0, r0, r1, vx.device)
            r1v[r0:r1].copy_(rc, non_blocking=True)
            if compare:
                pc = self._load(prev, r0, r1, vx.device)
                num += (rc - pc).abs().sum(dtype=torch.float32)
                den += pc.abs().sum(dtype=torch.float32)
            del rc
        skip, rel = False, None
        if compare:
            n = x2.numel()
            # research: (r1 - prev).abs().mean() / prev.abs().mean() of bf16 tensors
            rel = float(((num / n).to(vx.dtype) / (den / n).to(vx.dtype).clamp_min(1e-8)).item())
            skip = rel < self.threshold
        self.events.append(dict(key='%s/%s' % key, rel=rel, skip=skip))
        if skip:
            rv = self._rows(st['rv'], vx)
            for r0, r1 in _rows(x2.shape[0], self.chunk):
                x2[r0:r1].add_(self._load(rv, r0, r1, vx.device))
            st['skips'] += 1
            self.skipped += 1
            self._put(r1buf)
            self._put(snap)
            return True, ax + st['ra'], None, None
        return False, ax, r1buf, self.snapshot(vx, snap)

    def finish(self, key, vx, ax, a1, r1buf, snap):
        """After a full forward: store r1, rv = vx - (state after block 0), ra."""
        x2 = vx.view(-1, vx.shape[-1])
        v1 = self._rows(snap, vx)
        for r0, r1 in _rows(x2.shape[0], self.chunk):
            rc = x2[r0:r1] - self._load(v1, r0, r1, vx.device)
            v1[r0:r1].copy_(rc, non_blocking=True)  # rv into the snapshot buffer, chunk by chunk
            del rc
        old = self.state.get(key)
        if old is not None:
            self._put(old['r1'])
            self._put(old['rv'])
        self.state[key] = dict(r1=r1buf, rv=snap, ra=ax - a1, skips=0)

    def drop(self):
        """Expert switch: the states of the previous expert are not used again."""
        for st in self.state.values():
            self._put(st['r1'])
            self._put(st['rv'])
        self.state = {}

    def close(self):
        self.drop()
        torch.cuda.synchronize()
        for buf in self.owned:
            unregister(buf)
        self.owned, self.free = [], []


@torch.no_grad()
def transformer(units, roots, expert, *, visual_latents, audio_latents, context, timestep, audio_context=None,
                audio_timestep=None, video_fps=24.0, num_train_timesteps=1000, neg_audio_context=None,
                residency=None, lean=False, chunk=16384, progress=None, variant='base', fbcache=None,
                branch='cond', tap=None):
    """One evaluation of the active expert. With ``neg_audio_context`` an
    unconditional audio stream (negative prompt) that attends to this pass's
    video is also run: returns (video, audio_cond, audio_uncond); otherwise
    (video, audio). ``residency(index)`` returns a context manager that makes
    unit ``index`` resident while it runs (default: already resident).
    ``tap`` (StudentTap, conditional student pass of an audio-teacher step, lean
    path): receives each fused layer's v2a keys/values after this pass's audio
    stream used them, and the hidden states entering fused layer ``tap.start``."""
    advance_dynamic_block_pass_id()
    expert = root_key(roots, expert, variant)
    vdit, adit = roots[expert], roots['audio']
    vx, ax, c = _prologue(roots, expert, visual_latents, audio_latents, context, timestep, audio_context,
                          audio_timestep, video_fps, num_train_timesteps, neg_audio_context)
    audio_cfg = c.actx_u is not None
    axu = ax
    scale = 1.0  # MOVABridge default condition_scale (the pipeline never overrides it)
    # first-block cache (research passes no cache to the audio-CFG forward)
    fb = fbcache if (fbcache is not None and fbcache.threshold is not None and not audio_cfg) else None
    fb_key = (branch, expert)
    snap = fb.snapshot(vx) if fb is not None else None
    r1buf = a1 = None
    if tap is not None and not lean:
        raise ValueError('The audio teacher modes read the student pass on the lean path')
    for index, unit in enumerate(units):
        if tap is not None and index == tap.start and unit.fused:
            tap.states(vx, ax)
        if index == 1 and fb is not None:
            skip, ax, r1buf, snap = fb.after_first(fb_key, vx, ax, snap)
            if skip:
                rewind = getattr(residency, 'rewind', None)
                if rewind is not None:
                    rewind()  # the remaining units are not run, so not streamed
                if progress is not None:
                    progress(len(units))
                fb = None
                break
            a1 = ax
        if progress is not None:
            progress(index + 1)
        with (residency(index) if residency is not None else nullcontext()):
            if not unit.fused:
                vx = video_block(unit.video, vx, c.vctx, c.visual_t_mod, c.vfreqs, c.grid, chunk=chunk, lean=lean,
                                 variant=variant, timestep_ratio=c.ratio)
                continue
            if lean:
                # Both bridge directions read the original states: v2a first
                # (video keys/values), then a2v updates the video stream in place.
                ax_orig = ax
                norms = None
                if unit.v2a is not None:
                    kv = lean_v2a_kv(unit.v2a, vx, c.vrope, chunk)
                    ax = ax + v2a_with_kv(unit.v2a, ax_orig, kv, c.arope) * scale
                    if audio_cfg:
                        axu = axu + v2a_with_kv(unit.v2a, axu, kv, c.arope) * scale
                    if tap is not None:
                        tap.kv(index, kv, c)  # may change kv in place: this pass is done with it
                    del kv
                if unit.a2v is not None:
                    norms = lean_a2v_update(unit.a2v, vx, ax_orig, c.vrope, c.arope, chunk)
                    norms = norms if unit.video.self_attn._check_bsa(c.grid)[0] \
                        and unit.video.self_attn._use_dynamic() else None
                vx = video_block(unit.video, vx, c.vctx, c.visual_t_mod, c.vfreqs, c.grid, audio_token_norms=norms,
                                 chunk=chunk, variant=variant)
                del ax_orig
            elif audio_cfg:
                # research _forward_audio_cfg, one layer
                v_orig = vx
                a2v_res = None
                if unit.a2v is not None:
                    cnd = unit.a2v(vx, ax, c.vrope, c.arope, c.grid, "3d", "1d", c.grid, None)
                    a2v_res = cnd
                    vx = vx + cnd * scale
                if unit.v2a is not None:
                    ax = ax + unit.v2a(ax, v_orig, c.arope, c.vrope, c.grid, "1d", "3d", None, c.grid) * scale
                    axu = axu + unit.v2a(axu, v_orig, c.arope, c.vrope, c.grid, "1d", "3d", None, c.grid) * scale
                del v_orig
                if variant == 'student' and has_lora(unit.video):
                    vx = video_block(unit.video, vx, c.vctx, c.visual_t_mod, c.vfreqs, c.grid, chunk=chunk,
                                     variant=variant, audio_token_norms=unit.video.self_attn.audio_norms(c.grid, a2v_res))
                else:
                    vx = unit.video(vx, c.vctx, c.visual_t_mod, c.vfreqs, grid_size=c.grid,
                                    a2v_bridge_residual=a2v_res, timestep_ratio=c.ratio)
                del a2v_res
            else:
                # research FusedMOVABlock.forward
                v_orig = vx
                a2v_res = None
                if unit.a2v is not None:
                    cnd = unit.a2v(vx, ax, c.vrope, c.arope, c.grid, "3d", "1d", c.grid, None)
                    a2v_res = cnd
                    vx = vx + cnd * scale
                if unit.v2a is not None:
                    ax = ax + unit.v2a(ax, v_orig, c.arope, c.vrope, c.grid, "1d", "3d", None, c.grid) * scale
                del v_orig
                if variant == 'student' and has_lora(unit.video):
                    vx = video_block(unit.video, vx, c.vctx, c.visual_t_mod, c.vfreqs, c.grid, chunk=chunk,
                                     variant=variant, audio_token_norms=unit.video.self_attn.audio_norms(c.grid, a2v_res))
                else:
                    vx = unit.video(vx, c.vctx, c.visual_t_mod, c.vfreqs, grid_size=c.grid,
                                    a2v_bridge_residual=a2v_res, timestep_ratio=c.ratio)
                del a2v_res
            ax = unit.audio(ax, c.actx, c.audio_t_mod, c.afreqs)
            if audio_cfg:
                axu = unit.audio(axu, c.actx_u, c.audio_t_mod, c.afreqs)
    if fb is not None and r1buf is not None:
        fb.finish(fb_key, vx, ax, a1, r1buf, snap)
    vout = _head(vdit, vx, c.visual_t, c.grid, lean, chunk)
    _release(vx)  # see _passed: a reference cycle may still hold this tensor object
    del vx
    aout = adit.unpatchify(adit.head(ax, c.audio_t), (c.f,))
    if audio_cfg:
        return vout, aout, adit.unpatchify(adit.head(axu, c.audio_t), (c.f,))
    return vout, aout


# ---------------------------------------------------------------------------
# Audio teacher: base-weight v2a K/V of this step's video + audio-only sub-steps
# (Prism-fast audio_regen.capture_v2a_kv / audio_forward / audio_substeps)
# ---------------------------------------------------------------------------
def registered_empty(nbytes):
    """Page-locked host bytes of exactly ``nbytes`` (cudaHostRegister on a pageable
    allocation), or None. torch's pinned allocator rounds every block up to a power
    of two: the 287.5 MB K/V tensors of a 720p cache took 512 MB each (30 GiB of
    locked RAM for a 16.1 GiB cache)."""
    try:
        buffer = torch.empty(int(nbytes), dtype=torch.uint8)
        code = int(torch.cuda.cudart().cudaHostRegister(buffer.data_ptr(), buffer.nbytes, 0))
    except (RuntimeError, MemoryError):
        return None
    if code != 0:
        return None
    with _REGISTERED_LOCK:
        _REGISTERED[buffer.data_ptr()] = buffer.nbytes
    return buffer


_REGISTERED = {}  # data_ptr -> bytes this process registered (cudaHostRegister)
_REGISTERED_LOCK = __import__('threading').Lock()


def registered_bytes():
    """Host bytes this process holds registered (page-locked) through registered_empty:
    the part of the plan's locked memory already in Windows' non-local usage."""
    with _REGISTERED_LOCK:
        return sum(_REGISTERED.values())


def host_buffer(nbytes):
    """Host bytes for H2D/D2H staging: registered (exact size), else torch pinned,
    else pageable. On Windows page-locking draws on the WDDM non-local budget and
    fails with a CUDA out-of-memory error past it; pageable memory still works
    (its copies are synchronous). Returns (tensor, kind)."""
    buffer = registered_empty(nbytes)
    if buffer is not None:
        return buffer, 'registered'
    try:
        return torch.empty(int(nbytes), dtype=torch.uint8, pin_memory=True), 'pinned'
    except (RuntimeError, MemoryError):  # torch.AcceleratorError is a RuntimeError
        return torch.empty(int(nbytes), dtype=torch.uint8), 'pageable'


def pinned_or_pageable(nbytes):
    """Short-lived staging: torch pinned memory, else pageable (no registration to undo)."""
    try:
        return torch.empty(int(nbytes), dtype=torch.uint8, pin_memory=True)
    except (RuntimeError, MemoryError):
        return torch.empty(int(nbytes), dtype=torch.uint8)


def unregister(buffer):
    try:
        torch.cuda.cudart().cudaHostUnregister(buffer.data_ptr())
    except RuntimeError:
        pass
    with _REGISTERED_LOCK:
        _REGISTERED.pop(buffer.data_ptr(), None)


class _KVSpill:
    """FP8 K/V of some teacher layers in a file (the RAM allowance cannot hold the
    whole cache). One I/O thread writes each layer as the teacher pass produces it
    and reads layers back for the sub-steps, ahead of their upload; two registered
    slabs serve both directions (capture and sub-steps never overlap)."""

    def __init__(self, path, layers, layer_bytes):
        from concurrent.futures import ThreadPoolExecutor
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.layers = sorted(layers)
        self.offset = {layer: j * layer_bytes for j, layer in enumerate(self.layers)}
        self.layer_bytes = layer_bytes
        size = len(self.layers) * layer_bytes
        import shutil
        free = shutil.disk_usage(self.path.parent).free
        if free < size + 2**30:
            raise OSError('Prism (preview) needs %.1f GB of free disk space next to the output for part of its audio '
                          'cache, but %s has %.1f GB free. Free some space or choose another output folder. · '
                          'Prism（预览）需要在输出位置旁边临时占用 %.1f GB 磁盘空间存放部分音频缓存，但 %s 只剩 %.1f GB。'
                          '请清理空间或换一个输出文件夹。' % (size / 1e9, self.path.parent, free / 1e9, size / 1e9,
                                                         self.path.parent, free / 1e9))
        # Gone with the process however it ends: delete-on-close on Windows; elsewhere
        # the name is removed at once and the open file keeps its data.
        if os.name == 'nt':
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_BINARY | os.O_TEMPORARY)
            self.file = os.fdopen(fd, 'w+b')
        else:
            self.file = open(self.path, 'w+b')
            try:
                self.path.unlink()
            except OSError:
                pass
        self.file.truncate(size)
        self.io = ThreadPoolExecutor(max_workers=1, thread_name_prefix='prism-kv-spill')
        self.slabs = []
        self.registered = []
        for _ in range(2):
            slab, kind = host_buffer(layer_bytes)
            self.slabs.append(slab)
            self.registered.append(kind == 'registered')
        self.busy = [[], []]          # futures / CUDA events that still use each slab
        self.turn = 0
        self.writes = {}              # layer -> future of its last write
        self.reads = {}               # layer -> (slab index, future)
        self.read_bytes = self.write_bytes = 0
        self.read_seconds = self.write_seconds = 0.0

    def _claim(self):
        index = self.turn
        self.turn ^= 1
        for item in self.busy[index]:
            item.synchronize() if isinstance(item, torch.cuda.Event) else item.result()
        self.busy[index] = []
        return index

    def _views(self, index, shape, dtype):
        import math
        n = math.prod(shape)
        slab = self.slabs[index]
        return slab[:n].view(dtype).view(shape), slab[n:2 * n].view(dtype).view(shape)

    def write(self, layer, kq, vq):
        index = self._claim()
        hk, hv = self._views(index, kq.shape, kq.dtype)
        hk.copy_(kq, non_blocking=True)
        hv.copy_(vq, non_blocking=True)
        copied = torch.cuda.Event()
        copied.record()
        nbytes = 2 * kq.numel()

        def task():
            copied.synchronize()
            tick = time.perf_counter()
            self.file.seek(self.offset[layer])
            self.file.write(memoryview(self.slabs[index][:nbytes].numpy()))
            self.file.flush()
            self.write_seconds += time.perf_counter() - tick
            self.write_bytes += nbytes
        future = self.io.submit(task)
        self.busy[index].append(future)
        self.writes[layer] = future
        self.reads.pop(layer, None)

    def request(self, layer, shape, dtype):
        """Start reading ``layer`` into a free slab (no-op when already requested)."""
        if layer in self.reads or layer not in self.offset:
            return
        index = self._claim()
        nbytes = 2 * int(torch.Size(shape).numel())
        written = self.writes.get(layer)

        def task():
            if written is not None:
                written.result()
            tick = time.perf_counter()
            self.file.seek(self.offset[layer])
            view = memoryview(self.slabs[index][:nbytes].numpy())
            got = 0
            while got < nbytes:
                n = self.file.readinto(view[got:])
                if not n:
                    raise ValueError('Incomplete K/V spill read: %s' % self.path)
                got += n
            self.read_seconds += time.perf_counter() - tick
            self.read_bytes += nbytes
        future = self.io.submit(task)
        self.busy[index].append(future)
        self.reads[layer] = (index, future)

    def fetch(self, layer, shape, dtype):
        """Pinned (k, v) views of ``layer``; call ``uploaded`` with the H2D event."""
        self.request(layer, shape, dtype)
        index, future = self.reads.pop(layer)
        future.result()
        return self._views(index, shape, dtype) + (index,)

    def uploaded(self, index, event):
        self.busy[index].append(event)

    def close(self):
        try:
            self.io.shutdown(wait=True)
            for items in self.busy:
                for item in items:
                    item.synchronize() if isinstance(item, torch.cuda.Event) else item.result()
        finally:
            self.file.close()
            for slab, registered in zip(self.slabs, self.registered):
                if registered:
                    unregister(slab)
            try:
                self.path.unlink()
            except OSError:
                pass


class V2AKVCache:
    """Post-RoPE v2a keys and values of one video stream per fused layer, FP8
    with one scale per tensor (research audio_regen.KVCache: ``k``/``v``/``ks``/
    ``vs`` lists, so audio_graph's KVTable can bind it). ``placement``: 'gpu'
    keeps every layer on the device (the CUDA-graph sub-steps read them in
    place); 'host' keeps them in pinned host memory (written once per step from
    the teacher pass) and uploads one layer at a time into two device buffers,
    prefetching the next on a side stream, while the audio tower runs."""

    def __init__(self, device, placement='gpu', fp8=True, chunk=16384, disk_layers=0, spill_path=None):
        if not fp8:
            raise ValueError('the v2a K/V cache is FP8')
        # host placement: the first ``disk_layers`` layers live in a file instead of
        # pinned RAM; the sub-steps keep the first layers on the GPU, so these are
        # read back once per step where VRAM allows.
        self.disk_layers = int(disk_layers) if placement == 'host' and spill_path else 0
        self.spill_path = spill_path
        self.disk = None
        self.layer_shape = None
        self.device = torch.device(device)
        self.placement = placement
        self.fp8 = True
        self.chunk = chunk
        self.k, self.v, self.ks, self.vs = [], [], [], []
        self.host = []
        self.stream = torch.cuda.Stream(self.device) if placement == 'host' else None
        self.uploads = {}
        self.buffers = [None, None]
        self.consumed = [None, None]
        self.arope = None
        self.h2d_bytes = 0
        self.table = None
        self.kept = {}  # layer -> (k, v, event): held on the device through one step's sub-steps
        self.slot_of = None
        self.slabs = []          # registered host slabs (k and v of one layer each)
        self.reserved = []       # futures of slabs being registered in the background
        self.reserver = None
        self.host_bytes = 0

    def reserve(self, shape, count):
        """Register the host slabs of ``count`` layers of K and V ``shape`` (FP8) in a
        background thread, so the first teacher pass does not wait for 16 GB of page
        locking (cudaHostRegister runs at ~1.7 GB/s here)."""
        if self.placement != 'host' or self.reserved or self.host:
            return
        if self.disk_layers and self.disk is None:
            import math
            self.disk = _KVSpill(self.spill_path, range(min(self.disk_layers, count)), 2 * math.prod(shape))
        count = max(0, count - self.disk_layers)
        if not count:
            self.reserved = [None]  # nothing to register; marks the reservation as done
            return
        from concurrent.futures import ThreadPoolExecutor
        import math
        nbytes = 2 * math.prod(shape)
        self.reserver = ThreadPoolExecutor(max_workers=1, thread_name_prefix='prism-kv-pin')
        self.reserved = [self.reserver.submit(registered_empty, nbytes) for _ in range(count)]
        self.reserve_shape = tuple(shape)

    def _host_pair(self, i, shape, dtype):
        import math
        n = math.prod(shape)
        slab = None
        j = i - self.disk_layers  # reserved slabs cover the layers kept in RAM
        if 0 <= j < len(self.reserved) and self.reserved[j] is not None \
                and tuple(shape) == getattr(self, 'reserve_shape', None):
            slab = self.reserved[j].result()
        if slab is None:
            slab = registered_empty(2 * n)
        if slab is None:
            # Registration refused (Windows: the shared-GPU-memory budget is used up):
            # pageable memory, uploaded with synchronous copies. Never the rounded
            # torch pinned allocator, which would fail the same way and twice as big.
            self.pageable_layers = getattr(self, 'pageable_layers', 0) + 1
            buffer = torch.empty(2 * n, dtype=torch.uint8)
            return buffer[:n].view(dtype).view(shape), buffer[n:].view(dtype).view(shape)
        self.slabs.append(slab)
        self.host_bytes += slab.nbytes
        return slab[:n].view(dtype).view(shape), slab[n:].view(dtype).view(shape)

    def _quantize(self, t):
        scale = t.abs().amax().float().clamp_min(1e-6) / 448.0
        out = torch.empty(t.shape, dtype=torch.float8_e4m3fn, device=t.device)
        flat, dst = t.view(-1, t.shape[-1]), out.view(-1, t.shape[-1])
        for r0, r1 in _rows(flat.shape[0], self.chunk):  # bound the fp32 temporary
            dst[r0:r1] = (flat[r0:r1].float() / scale).to(torch.float8_e4m3fn)
        return out, scale

    def put(self, i, k, v, dequantized=True):
        """Store layer i (layers arrive in order 0, 1, ...); returns the dequantized
        pair (what KVCache.get returns: bf16(fp8 * bf16(scale))), or None when
        ``dequantized`` is False (the caller does not read it back)."""
        kq, ks = self._quantize(k)
        vq, vs = self._quantize(v)
        out = (kq.to(k.dtype) * ks.to(k.dtype), vq.to(v.dtype) * vs.to(v.dtype)) if dequantized else None
        if i < len(self.ks):
            self.ks[i], self.vs[i] = ks, vs
        else:
            assert i == len(self.ks), 'v2a K/V layers must be stored in order'
            self.ks.append(ks)
            self.vs.append(vs)
        if self.placement == 'gpu':
            if i < len(self.k):
                self.k[i], self.v[i] = kq, vq
            else:
                self.k.append(kq)
                self.v.append(vq)
            return out
        self.layer_shape, self.layer_dtype = tuple(kq.shape), kq.dtype
        if i < self.disk_layers:
            if self.disk is None:
                import math
                self.disk = _KVSpill(self.spill_path, range(self.disk_layers), 2 * math.prod(kq.shape))
            self.disk.write(i, kq, vq)
            if i >= len(self.host):
                self.host.append(None)
            return out
        if i >= len(self.host) or self.host[i][0].shape != kq.shape:
            pinned = self._host_pair(i, kq.shape, kq.dtype)
            if i < len(self.host):
                self.host[i] = pinned
            else:
                self.host.append(pinned)
        self.host[i][0].copy_(kq)
        self.host[i][1].copy_(vq)
        return out

    def _source(self, i):
        """Pinned (k, v) of layer i for an upload, and the spill slab it came from."""
        if self.host[i] is not None:
            return self.host[i][0], self.host[i][1], None
        return self.disk.fetch(i, self.layer_shape, self.layer_dtype)

    def _read_ahead(self, i):
        """Start reading the next spilled layer that will stream after layer i."""
        if self.disk is None:
            return
        for j in range(i + 1, len(self.host)):
            if j not in self.kept and self.host[j] is None:
                self.disk.request(j, self.layer_shape, self.layer_dtype)
                return
            if self.host[j] is not None and j not in self.kept:
                return

    def keep(self, budget):
        """Upload the first layers that fit ``budget`` bytes once and hold them on
        the device until ``release_device``: the audio sub-steps of a step read
        every layer once per sub-step, so these skip their repeated uploads."""
        if self.placement != 'host' or not self.host or self.layer_shape is None:
            return 0
        import math
        size = 2 * math.prod(self.layer_shape)
        count = min(len(self.host), max(0, int(budget) // size))
        if count:
            self.stream.wait_stream(torch.cuda.current_stream(self.device))
            for i in range(count):
                hk, hv, slab = self._source(i)
                with torch.cuda.stream(self.stream):
                    k = torch.empty(hk.shape, dtype=hk.dtype, device=self.device)
                    v = torch.empty(hv.shape, dtype=hv.dtype, device=self.device)
                    k.copy_(hk, non_blocking=True)
                    v.copy_(hv, non_blocking=True)
                    event = torch.cuda.Event()
                    event.record(self.stream)
                if slab is not None:
                    self.disk.uploaded(slab, event)
                if i + 1 < count and self.host[i + 1] is None:
                    self.disk.request(i + 1, self.layer_shape, self.layer_dtype)  # one read ahead
                self.kept[i] = (k, v, event)
                self.h2d_bytes += k.numel() + v.numel()
        # the two upload buffers alternate over the layers that still stream
        self.slot_of = {j: n % 2 for n, j in enumerate(j for j in range(len(self.host)) if j not in self.kept)}
        return count * size

    def _slot(self, i):
        return self.slot_of[i] if self.slot_of is not None else i % 2

    def _next_streamed(self, i):
        while i in self.kept:
            i += 1
        return i

    def prefetch(self, i):
        if self.placement != 'host' or i in self.uploads or i >= len(self.host) or i in self.kept:
            return
        slot = self._slot(i)
        hk, hv, spill_slab = self._source(i)
        fresh = self.buffers[slot] is None or self.buffers[slot][0].shape != hk.shape
        if fresh:
            # Allocate from the copy stream's pool and order after all queued
            # work: a block just freed on the compute stream may still be written.
            self.stream.wait_stream(torch.cuda.current_stream(self.device))
            with torch.cuda.stream(self.stream):
                self.buffers[slot] = (torch.empty(hk.shape, dtype=hk.dtype, device=self.device),
                                      torch.empty(hv.shape, dtype=hv.dtype, device=self.device))
        k, v = self.buffers[slot]
        with torch.cuda.stream(self.stream):
            if self.consumed[slot] is not None:
                self.stream.wait_event(self.consumed[slot])
            k.copy_(hk, non_blocking=True)
            v.copy_(hv, non_blocking=True)
            event = torch.cuda.Event()
            event.record(self.stream)
        if spill_slab is not None:
            self.disk.uploaded(spill_slab, event)
        self._read_ahead(i)
        self.uploads[i] = (k, v, event)
        self.h2d_bytes += k.numel() + v.numel()

    def device_layer(self, i):
        """FP8 (k, v) of layer i on the device (host placement: the uploaded slot,
        valid until ``done(i)``; the next layer is prefetched)."""
        if self.placement != 'host':
            return self.k[i], self.v[i]
        if i in self.kept:
            k, v, event = self.kept[i]
            torch.cuda.current_stream(self.device).wait_event(event)
            self.prefetch(self._next_streamed(i + 1))
            return k, v
        self.prefetch(i)
        k, v, event = self.uploads.pop(i)
        torch.cuda.current_stream(self.device).wait_event(event)
        self.prefetch(self._next_streamed(i + 1))
        return k, v

    def done(self, i):
        if self.placement == 'host' and i not in self.kept:
            event = torch.cuda.Event()
            event.record(torch.cuda.current_stream(self.device))
            self.consumed[self._slot(i)] = event

    def get(self, i, dtype):
        """Dequantized bf16 (k, v) of layer i (research KVCache.get)."""
        k, v = self.device_layer(i)
        ks, vs = self.ks[i], self.vs[i]
        out = (k.to(dtype) * ks.to(dtype), v.to(dtype) * vs.to(dtype))
        self.done(i)
        return out

    def nbytes(self):
        tensors = self.k + self.v + [t for pair in self.host if pair is not None for t in pair]
        return sum(t.numel() * t.element_size() for t in tensors)

    def spill_stats(self):
        if self.disk is None:
            return None
        d = self.disk
        return dict(layers=len(d.layers), file_bytes=len(d.layers) * d.layer_bytes, write_bytes=d.write_bytes,
                    write_seconds=d.write_seconds, read_bytes=d.read_bytes, read_seconds=d.read_seconds)

    def locked_bytes(self):
        """Page-locked host bytes held (registered slabs plus any rounded fallback)."""
        return self.host_bytes + (sum(s.nbytes for s in self.disk.slabs) if self.disk is not None else 0)

    def release_device(self):
        """Free the two upload buffers between steps (the pinned copies stay)."""
        if self.stream is not None:
            self.stream.synchronize()
        torch.cuda.current_stream(self.device).synchronize()
        self.uploads.clear()
        self.kept.clear()
        self.slot_of = None
        self.buffers = [None, None]
        self.consumed = [None, None]

    def close(self):
        if self.stream is not None:
            self.stream.synchronize()
        self.k.clear(); self.v.clear(); self.uploads.clear(); self.host.clear(); self.kept.clear()
        if self.disk is not None:
            self.disk.close()
            self.disk = None
        if self.reserver is not None:
            self.reserver.shutdown(wait=True)
            for future in self.reserved:
                slab = future.result() if future is not None else None
                if slab is not None and all(slab is not other for other in self.slabs):
                    unregister(slab)
            self.reserver, self.reserved = None, []
        for slab in self.slabs:
            unregister(slab)
        self.slabs = []
        self.buffers = [None, None]
        self.consumed = [None, None]


def device_room(device, limit=None):
    """Bytes the allocator can still hand out: what the device has free (plus the
    allocator's idle cache), at most the planned cap minus what is allocated."""
    allocated = torch.cuda.memory_allocated(device)
    reserved = torch.cuda.memory_reserved(device)
    free, _ = torch.cuda.mem_get_info(device)
    room = free + reserved - allocated
    if not limit:
        return room
    # Under a cap, cached blocks the allocator holds count as used: without
    # expandable segments (Windows) they are fragments that a new layer-sized
    # buffer often cannot reuse (an RTX 4070 sub-step ran out of memory with
    # 1.45 GiB of them under its 10.6 GiB ceiling).
    return min(room, int(limit) - max(allocated + 2**29, reserved))


class SubstepWeights:
    """GPU copies of the audio blocks and v2a conditioners of the streamed fused
    units, held through one step's audio sub-steps. Without them every sub-step
    streams each fused unit whole (~550 MB at 720p, ~89 MB of it audio + v2a),
    four times per step. Copied from the pinned host copies, or read from the
    prepared files (just those tensors' bytes) for units that stream from disk;
    an ordered pass through the fused offloader otherwise. Calling it
    with a unit index binds that unit's copies (a residency map for _eager_audio);
    ``.streamed`` keeps the original set, so the cached CUDA graph is not used."""

    PARTS = ('audio.', 'v2a.')

    def __init__(self, units, residency, count, device):
        self.bound = {}
        self.nbytes = 0
        self.streamed = set(getattr(residency, 'streamed', None) or ())
        phase = getattr(residency, 'phase', None)
        wanted = sorted(i for i in self.streamed if i < count)
        if phase is None or not wanted:
            return
        groups = {}
        source_of = {id(offloader): source for offloader, source in getattr(phase, 'offloaders', [])}
        for i in wanted:
            offloader, k = phase.slot[i]
            groups.setdefault(id(offloader), (offloader, []))[1].append((i, k))
        for offloader, rows in groups.values():
            if all(offloader.pinned_layers[k] for _, k in rows):
                for i, k in rows:
                    self.bound[i] = [(parameter, offloader.cpu[k][name].to(device, non_blocking=True))
                                     for parameter, name, *_ in offloader.layout[k] if name.startswith(self.PARTS)]
            elif getattr(source_of.get(id(offloader)), 'byte_ranges', None):
                # Read just these tensors' bytes from the prepared files (~89 MB per unit
                # instead of streaming the whole unit through the offloader again).
                self._read_parts(offloader, source_of[id(offloader)], rows, device)
            else:
                ks = {k: i for i, k in rows}
                order = list(range(offloader.expected, len(offloader.layers))) + list(range(offloader.expected))
                for k in order:  # the offloader serves its layers in cyclic order only
                    with offloader.layer(k):
                        if k in ks:
                            self.bound[ks[k]] = [(parameter, parameter.data.clone())
                                                 for parameter, name, *_ in offloader.layout[k]
                                                 if name.startswith(self.PARTS)]
        self.nbytes = sum(t.numel() * t.element_size() for pairs in self.bound.values() for _, t in pairs)

    def _read_parts(self, offloader, source, rows, device):
        """Pinned units: from their host copies; the others: readinto a pinned
        staging buffer (two, alternating) from the file byte ranges, then H2D."""
        stage = [None, None]
        done = [None, None]
        turn = 0
        for i, k in rows:
            entries = [(parameter, name) for parameter, name, *_ in offloader.layout[k] if name.startswith(self.PARTS)]
            if offloader.pinned_layers[k]:
                self.bound[i] = [(p, offloader.cpu[k][name].to(device, non_blocking=True)) for p, name in entries]
                continue
            prefix = source.prefixes[k]
            spans = [(name, source.keys[prefix + name]) + source.byte_ranges[prefix + name] for _, name in entries]
            total = sum(row[3] for row in spans)
            if stage[turn] is None or stage[turn].numel() < total:
                if done[turn] is not None:
                    done[turn].synchronize()
                stage[turn] = pinned_or_pageable(total)
            elif done[turn] is not None:
                done[turn].synchronize()
            buffer = stage[turn]
            view = memoryview(buffer.numpy())
            offset, placed = 0, {}
            for path in sorted({row[1] for row in spans}):
                with open(path, 'rb', buffering=0) as stream:
                    for name, _, start, size, _, _ in sorted((r for r in spans if r[1] == path), key=lambda r: r[2]):
                        stream.seek(start)
                        got = 0
                        while got < size:
                            n = stream.readinto(view[offset + got:offset + size])
                            if not n:
                                raise ValueError('Incomplete checkpoint read: %s' % path)
                            got += n
                        placed[name] = (offset, size)
                        offset += size
            pairs = []
            for parameter, name in entries:
                start, size = placed[name]
                shape = source.byte_ranges[prefix + name][3]
                gpu = torch.empty(shape, dtype=parameter.dtype, device=device)
                gpu.view(-1).view(torch.uint8).copy_(buffer[start:start + size], non_blocking=True)
                pairs.append((parameter, gpu))
            event = torch.cuda.Event()
            event.record()
            done[turn] = event
            self.bound[i] = pairs
            turn ^= 1
        for event in done:
            if event is not None:
                event.synchronize()

    @staticmethod
    def estimate(units, residency, count):
        phase = getattr(residency, 'phase', None)
        total = 0
        for i in sorted(set(getattr(residency, 'streamed', None) or ()) & set(range(count))):
            offloader, k = phase.slot[i]
            total += sum(c * p.element_size() for p, name, _, _, c, _ in offloader.layout[k]
                         if name.startswith(SubstepWeights.PARTS))
        return total

    @contextmanager
    def bind(self, index):
        pairs = self.bound.get(index)
        if not pairs:
            yield
            return
        saved = [parameter.data for parameter, _ in pairs]
        for parameter, tensor in pairs:
            parameter.data = tensor
        try:
            yield
        finally:
            for (parameter, _), data in zip(pairs, saved):
                parameter.data = data

    def __call__(self, index):
        return self.bind(index)

    def close(self):
        self.bound.clear()


def v2a_cached(block, audio_x, kv, arope):
    """v2a output for (possibly several stacked) audio streams against one video's K/V."""
    inner = block.inner
    q = inner.norm_q(inner.q(audio_x))
    if arope is not None:
        q = inner.rope_q(q, arope)
    b = q.shape[0]
    if b != kv[0].shape[0]:
        out = inner.attn(q.reshape(1, -1, q.shape[-1]), *kv).reshape(b, -1, q.shape[-1])
    else:
        out = inner.attn(q, *kv)
    return inner.o(out)


@torch.no_grad()
def teacher_capture(units, roots, expert, cache, *, visual_latents, audio_latents, context, audio_context, timestep,
                    audio_timestep, video_fps=24.0, num_train_timesteps=1000, residency=None, chunk=16384,
                    fused=None, start_layer=0, init_states=None, progress=None):
    """Base-weight pass over the fused layers: store each v2a K/V in ``cache``
    and return the audio prediction (research capture_v2a_kv, return_audio).
    ``start_layer`` / ``init_states`` (partial teacher, research audio_teacher
    start_layer): begin at that fused layer from the given (video, audio) hidden
    states; ``cache`` already holds the K/V of the layers before it."""
    advance_dynamic_block_pass_id()
    vdit, adit = roots[expert], roots['audio']
    vx, ax, c = _prologue(roots, expert, visual_latents, audio_latents, context, timestep, audio_context,
                          audio_timestep, video_fps, num_train_timesteps, None)
    cache.arope = c.arope
    if start_layer:
        assert len(cache.ks) >= start_layer, (len(cache.ks), start_layer)
        del vx, ax
        vx, ax = (t.to(visual_latents.device, non_blocking=False) for t in init_states)
        seek = getattr(residency, 'seek', None)
        if seek is not None:
            seek(start_layer)  # streamed units: the offloaders expect their units in order
    count = fused if fused is not None else sum(1 for u in units if u.fused)
    for index in range(start_layer, count):
        if progress is not None:
            progress('teacher', index + 1, count)
        unit = units[index]
        with (residency(index) if residency is not None else nullcontext()):
            ax_orig = ax
            norms = None
            kv = lean_v2a_kv(unit.v2a, vx, c.vrope, chunk, fused=False)
            # the capture's own audio stream reads the stored (FP8) K/V, as in the research code
            kv = cache.put(index, *kv)
            ax = ax + v2a_cached(unit.v2a, ax_orig, kv, c.arope)
            del kv
            if unit.a2v is not None:
                norms = lean_a2v_update(unit.a2v, vx, ax_orig, c.vrope, c.arope, chunk)
                if not (unit.video.self_attn._check_bsa(c.grid)[0] and unit.video.self_attn._use_dynamic()):
                    norms = None
            vx = video_block(unit.video, vx, c.vctx, c.visual_t_mod, c.vfreqs, c.grid, audio_token_norms=norms,
                             chunk=chunk, variant='base')
            ax = unit.audio(ax, c.actx, c.audio_t_mod, c.afreqs)
            del ax_orig
    _release(vx)
    del vx
    return adit.unpatchify(adit.head(ax, c.audio_t), (c.f,))


class StudentTap:
    """What the conditional student pass of an audio-teacher step hands the teacher
    (sampling.transformer ``tap``): the v2a K/V of fused layers before ``start``
    (partial teacher: the student's own, research audio_teacher start_layer) or of
    every layer through the calibration maps (``maps``: one bucket's per-layer maps
    on the device, kv_calib), stored in ``cache``; and the hidden states entering
    fused layer ``start`` (``host_states``: kept in host memory until the teacher
    pass, pageable, for small GPUs)."""

    def __init__(self, cache, *, start=None, maps=None, host_states=False):
        self.cache, self.start, self.maps, self.host = cache, start, maps, host_states
        self.saved = None

    def kv(self, index, kv, c):
        if self.start is not None and index >= self.start:
            return
        self.cache.arope = c.arope
        k, v = kv
        if self.maps is not None:
            from . import kv_calib
            kv_calib.apply_(k, v, self.maps[index], c.vrope)
        self.cache.put(index, k, v, dequantized=False)

    def states(self, vx, ax):
        if self.host:
            self.saved = (vx.to('cpu'), ax.to('cpu'))
        else:
            self.saved = (vx.clone(), ax.clone())

    def take_states(self):
        saved, self.saved = self.saved, None
        if saved is None:
            raise RuntimeError('The student pass did not reach the partial teacher\'s start layer')
        return saved


class _AudioUnits:
    """audio_graph's view of the fused layers: ``audio_dit`` + ``fusion_blocks``
    items with ``v2a_conditioner`` / ``audio_block`` (MOVABridge attribute names)."""

    def __init__(self, units, roots, count):
        from types import SimpleNamespace
        self.audio_dit = roots['audio']
        self.fusion_blocks = [SimpleNamespace(v2a_conditioner=u.v2a, audio_block=u.audio) for u in units[:count]]


_AUDIO_GRAPHS = {}


def _eager_audio(units, roots, cache, audio_latents, audio_t, audio_ctx_emb, residency, count):
    """audio_regen.audio_forward with audio_graph's FP8 split-KV v2a attention
    (no CUDA graph: units may stream, a host cache uploads layer by layer)."""
    from . import audio_graph as ag
    adit = roots['audio']
    dev = audio_latents.device
    if cache.table is None:
        cache.table = ag.KVTable(2, dev)
    table = cache.table
    b = audio_latents.shape[0]
    ta = audio_t.reshape(1).to(dev, torch.float32).expand(b)
    with torch.autocast("cuda", dtype=torch.float32):
        at = adit.time_embedding(sinusoidal_embedding_1d(adit.freq_dim, ta))
        at_mod = adit.time_projection(at).unflatten(1, (6, adit.dim))
    dt = adit.dtype
    at, at_mod = at.to(dt), at_mod.to(dt)
    ax, (f,) = adit.patchify(audio_latents.to(dt), None)
    afreqs = assemble_audio_freqs(adit.freqs, f, ax.device)
    for index in range(count):
        unit = units[index]
        with (residency(index) if residency is not None else nullcontext()):
            k8, v8 = cache.device_layer(index)
            table.bind_one(index % 2, k8, v8, cache.ks[index], cache.vs[index])
            ax = ax + ag._v2a(unit.v2a, ax, table, index % 2, cache.arope)
            cache.done(index)
            ax = unit.audio(ax, audio_ctx_emb, at_mod, afreqs)
    return adit.unpatchify(adit.head(ax, at), (f,))


@torch.no_grad()
def audio_forward(units, roots, cache, audio_latents, audio_t, audio_ctx_emb, residency=None, fused=None):
    """Audio tower only: v2a from ``cache`` + the fused layers' audio blocks.
    ``audio_latents`` may stack several streams (CFG) along the batch. As in the
    research path (audio_graph): FP8 split-KV attention from the cache, captured as
    one CUDA graph when the cache and every fused unit stay on the GPU."""
    from . import audio_graph as ag
    count = fused if fused is not None else sum(1 for u in units if u.fused)
    streamed = getattr(residency, 'streamed', None) if residency is not None else set()
    resident = streamed is not None and not any(i in streamed for i in range(count))
    if ag.ENABLED and cache.placement == 'gpu' and resident:
        key = (id(units[0]), count)
        entry = _AUDIO_GRAPHS.get(key)
        if entry is None or entry[0] is not units[0]:
            _AUDIO_GRAPHS.clear()
            adapter = _AudioUnits(units, roots, count)
            entry = _AUDIO_GRAPHS[key] = (units[0], adapter)
        out = ag.audio_forward(entry[1], cache, audio_latents, audio_t, audio_ctx_emb)
        if out is not None:
            return out
    if ag.ENABLED:
        return _eager_audio(units, roots, cache, audio_latents, audio_t, audio_ctx_emb, residency, count)
    adit = roots['audio']
    b = audio_latents.shape[0]
    ta = audio_t.reshape(1).to(audio_latents.device, torch.float32).expand(b)
    with torch.autocast("cuda", dtype=torch.float32):
        at = adit.time_embedding(sinusoidal_embedding_1d(adit.freq_dim, ta))
        at_mod = adit.time_projection(at).unflatten(1, (6, adit.dim))
    dt = adit.dtype
    at, at_mod = at.to(dt), at_mod.to(dt)
    ax, (f,) = adit.patchify(audio_latents.to(dt), None)
    afreqs = assemble_audio_freqs(adit.freqs, f, ax.device)
    for index in range(count):
        unit = units[index]
        with (residency(index) if residency is not None else nullcontext()):
            ax = ax + v2a_cached(unit.v2a, ax, cache.get(index, ax.dtype), cache.arope)
            ax = unit.audio(ax, audio_ctx_emb, at_mod, afreqs)
    return adit.unpatchify(adit.head(ax, at), (f,))


SUBSTEP_RESERVE_BYTES = int(2.0 * 2**30)  # two K/V upload slots (~1.15 GB at 720p) + the audio streams


def release_audio_graphs():
    _AUDIO_GRAPHS.clear()


def guided(cond, uncond, cfg, sigma, min_sigma=None, rescale=0.0):
    """CFG with an optional guidance interval and std rescaling (research audio_regen.guided)."""
    if cfg == 1.0 or uncond is None or (min_sigma is not None and float(sigma) < min_sigma):
        return cond
    x = uncond + cfg * (cond - uncond)
    if rescale:
        dims = tuple(range(1, x.ndim))
        r = x * (cond.std(dim=dims, keepdim=True) / x.std(dim=dims, keepdim=True).clamp_min(1e-6))
        x = rescale * r + (1 - rescale) * x
    return x


def substep_times(t0, t1, m, power=1.0):
    out = []
    for k in range(m + 1):
        u = k / m
        out.append(t0 + (t1 - t0) * (1 - (1 - u) ** power) if power != 1.0 else t0 + (t1 - t0) * u)
    return out


@torch.no_grad()
def audio_substeps(units, roots, cache, scheduler, lat, t0, t1, m, first_pred, ctx_emb, neg_emb, cfg,
                   residency=None, min_sigma=None, rescale=0.0, power=1.0, progress=None):
    """Integrate audio from t0 to t1 in m Euler sub-steps against the cached
    video K/V (the first uses ``first_pred`` when given)."""
    adit = roots['audio']
    ts = substep_times(t0, t1, m, power)
    actx = adit.text_embedding(ctx_emb)
    both = torch.cat([actx, adit.text_embedding(neg_emb)], 0) if cfg != 1.0 else actx
    for k in range(m):
        if progress is not None:
            progress('substeps', k + 1, m)
        if k == 0 and first_pred is not None:
            pred = first_pred
        else:
            inp = lat.repeat(2, 1, 1) if cfg != 1.0 else lat
            out = audio_forward(units, roots, cache, inp, ts[k], both, residency).float()
            pred = guided(out[:1], out[1:] if cfg != 1.0 else None, cfg, scheduler.timestep_to_sigma(ts[k]),
                          min_sigma, rescale)
        lat = scheduler.step_from_to(pred, ts[k], ts[k + 1], lat)
    return lat


# ---------------------------------------------------------------------------
# Inputs (MOVA pipeline helpers)
# ---------------------------------------------------------------------------
def prompt_clean(text):
    """MOVA/Wan prompt cleaning: ftfy (when installed), HTML unescape, whitespace."""
    import html
    import re
    try:
        import ftfy
        text = ftfy.fix_text(text)
    except ImportError:  # ftfy only repairs mojibake; clean UTF-8 is unchanged
        pass
    text = html.unescape(html.unescape(text)).strip()
    return re.sub(r"\s+", " ", text).strip()


@torch.no_grad()
def t5_prompt_embeds(tokenizer, text_encoder, prompt, device, max_sequence_length=512, dtype=torch.bfloat16):
    """MOVAPipeline._get_t5_prompt_embeds for one prompt: [1, 512, 4096], zero
    padded beyond the prompt's tokens."""
    inputs = tokenizer([prompt_clean(prompt)], padding="max_length", max_length=max_sequence_length,
                       truncation=True, add_special_tokens=True, return_attention_mask=True, return_tensors="pt")
    ids, mask = inputs.input_ids, inputs.attention_mask
    seq_lens = mask.gt(0).sum(dim=1).long()
    embeds = text_encoder(ids.to(device), mask.to(device)).last_hidden_state.to(dtype=dtype)
    embeds = [u[:v] for u, v in zip(embeds, seq_lens)]
    return torch.stack([torch.cat([u, u.new_zeros(max_sequence_length - u.size(0), u.size(1))]) for u in embeds],
                       dim=0), int(seq_lens[0])


def crop_and_resize(img, height, width):
    """Center-crop to the target aspect ratio, then LANCZOS resize (PIL)."""
    from PIL import Image
    w, h = img.size
    target, ratio = width / height, w / h
    if ratio > target:
        new_w = int(h * target)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    elif ratio < target:
        new_h = int(w / target)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    return img.resize((width, height), Image.LANCZOS)


def image_tensor(img):
    """VaeImageProcessor.preprocess of an image already at the target size: [1, 3, H, W] in [-1, 1]."""
    import numpy as np
    array = np.array(img.convert('RGB')).astype(np.float32) / 255.0
    return torch.from_numpy(array[None].transpose(0, 3, 1, 2)) * 2.0 - 1.0


def latent_stats(vae_config, z, device, dtype):
    mean = torch.tensor(vae_config['latents_mean'], device=device, dtype=dtype).view(1, z, 1, 1, 1)
    std = torch.tensor(vae_config['latents_std'], device=device, dtype=dtype).view(1, z, 1, 1, 1)
    return mean, std


@torch.no_grad()
def image_condition(vae, vae_config, image, frames, height, width, device, first_frame_encoder=None):
    """MOVAPipeline.prepare_latents (first-frame I2V condition, no last frame):
    returns [1, 4 + z, F_lat, H/8, W/8] (mask channels + normalized VAE latents).
    ``image`` None: text-to-video (zero video, first-frame mask cleared).
    ``first_frame_encoder(frame [1, 3, H, W])``: the latent mean of [frame, zeros]
    without building the whole video (fast_vae.encode_i2v_condition)."""
    temporal = vae_config.get('scale_factor_temporal', 4)
    spatial = vae_config.get('scale_factor_spatial', 8)
    z = vae_config['z_dim']
    latent_frames = (frames - 1) // temporal + 1
    lh, lw = height // spatial, width // spatial
    img = (torch.zeros(1, 3, height, width) if image is None else image).to(device, dtype=torch.float32)
    if first_frame_encoder is not None:
        # The frame laid out as the first frame of a [1, 3, frames, H, W] video (the
        # view the research path hands the encoder): cuDNN picks its convolution by
        # layout, and a contiguous frame encoded 8e-4 apart. Only 2/3 of that
        # video's storage is allocated, never filled.
        stride = (3 * frames * height * width, frames * height * width, height * width, width, 1)
        frame = torch.empty_strided((1, 3, 1, height, width), stride, dtype=vae.dtype, device=device)
        frame.copy_(img.unsqueeze(2))
        latent = first_frame_encoder(frame).to(torch.float32)
        del frame
    else:
        # [frame, zeros] built in the VAE dtype (the same values as building it in
        # fp32 and casting; a 720p x 205 fp32 copy alone is 2.1 GiB)
        video = torch.zeros((1, 3, frames, height, width), dtype=vae.dtype, device=device)
        video[:, :, 0] = img.to(vae.dtype)
        latent = vae.encode(video).latent_dist.mode().to(torch.float32)
        del video
    del img
    mean, std = latent_stats(vae_config, z, latent.device, latent.dtype)
    latent = (latent - mean) * (1.0 / std)
    mask = torch.ones(1, 1, frames, lh, lw)
    mask[:, :, list(range(1, frames))] = 0
    first = torch.repeat_interleave(mask[:, :, 0:1], dim=2, repeats=temporal)
    mask = torch.concat([first, mask[:, :, 1:, :]], dim=2)
    mask = mask.view(1, -1, temporal, lh, lw).transpose(1, 2).to(latent.device)
    condition = torch.concat([mask, latent], dim=1)
    if image is None:
        condition[:, :temporal] = 0
    return condition, latent_frames


# ---------------------------------------------------------------------------
# Sampler (fast_generate's denoising loop)
# ---------------------------------------------------------------------------
@torch.no_grad()
def sample(*, scheduler, phase, condition, prompt_embeds, negative_embeds, seed, frames, height, width,
           audio_latent_dim, audio_samples, audio_hop, boundary_ratio, steps, video_shift, audio_shift, cfg_scale,
           cfg_steps, audio_cfg, device, lean=False, chunk=16384, step_callback=None, layer_callback=None,
           log=None, distilled=True, audio_teacher=None, kv_placement='gpu', vram_limit=None, substep_cache=True,
           kv_disk_layers=0, kv_spill_path=None, fbcache=None, fbcache_max_skip=3, fbcache_placement='gpu',
           kv_width=None, kv_layers=None, audio_maps=None, audio_callback=None):
    """Prism fast sampler: full CFG on the first ``cfg_steps`` steps, audio-only
    CFG (``audio_cfg``) on the others. ``phase(expert)`` makes an expert active
    and returns (units, roots, residency).

    ``distilled``: the main passes use the student (base + distill LoRA when the
    prepared weights carry one); False samples with the base weights (the
    undistilled "max" tier). ``audio_teacher`` (dict: substeps, cfg, max_sigma,
    min_sigma, rescale, power): per step, a base-weight pass over the fused layers
    stores the v2a K/V of this step's video (``kv_placement`` 'gpu' or 'host')
    and the audio is integrated to the next timestep in audio-only sub-steps with
    audio CFG against it, replacing the joint audio update. ``audio_teacher``
    ``start_layer`` (partial teacher): the fused layers before it keep the
    student's own K/V and the base pass starts there from the student's hidden
    states; ``calibrated`` (with ``audio_maps``, kv_calib.load_maps): no base
    pass, the student's K/V of every fused layer go through the step's fitted maps."""
    temporal_spatial = condition.shape
    gen = torch.Generator(device=device)
    gen.manual_seed(seed)
    latent_frames, lh, lw = temporal_spatial[2], temporal_spatial[3], temporal_spatial[4]
    # Same draws, same order and same generator as MOVAPipeline.prepare_latents
    # (video noise) and prepare_audio_latents (audio noise); the VAE encode in
    # between consumes no random numbers (argmax latents).
    latents = torch.randn((1, 16, latent_frames, lh, lw), generator=gen, device=device, dtype=torch.float32)
    audio_t = (audio_samples - 1) // audio_hop + 1
    audio_latents = torch.randn((1, audio_latent_dim, audio_t), generator=gen, device=device, dtype=torch.float32)
    scheduler.set_timesteps(steps, shift=video_shift, device=device)
    scheduler.set_pair_postprocess_by_name("dual_sigma_shift", visual_shift=float(video_shift),
                                           audio_shift=float(audio_shift))
    pairs = scheduler.get_pairs()
    boundary = boundary_ratio * scheduler.num_train_timesteps
    total = pairs.shape[0]
    expert = None
    pe, ne = prompt_embeds, negative_embeds
    ape = prompt_embeds  # audio prompt = prompt (research runner)
    log = [] if log is None else log
    variant = 'student' if distilled else 'base'
    # One cache for the whole request: its pinned host / device buffers are reused every step.
    cache = V2AKVCache(device, kv_placement, chunk=chunk, disk_layers=kv_disk_layers,
                       spill_path=kv_spill_path) if audio_teacher else None
    if cache is not None and cache.placement == 'host' and kv_width and kv_layers:
        # Register the teacher K/V before any expert pins its units: the cache is
        # required, the pinned units are an optimisation (Windows non-local budget).
        cache.reserve((1, latent_frames * (lh // 2) * (lw // 2), int(kv_width)), int(kv_layers))
    # first-block step cache (research fbcache / fbcache_max_skip); off for the teacher passes
    fb = FBCache(fbcache, fbcache_max_skip, placement=fbcache_placement, chunk=chunk) if fbcache else None
    # The K/V cache's spill file and registered buffers are released however sampling ends.
    try:
        for i in range(total):
            tv, ta = pairs[i]
            wanted = 'low' if (expert == 'low' or tv.item() < boundary) else 'high'
            if wanted != expert:
                # Drop this loop's references first: the previous expert's units
                # must be released before the next expert is loaded.
                units = roots = residency = None
                release_audio_graphs()
                if fb is not None:
                    fb.drop()
                if cache is not None:
                    cache.ks.clear(); cache.vs.clear(); cache.k.clear(); cache.v.clear()
                units, roots, residency = phase(wanted)
                expert = wanted
                if cache is not None and cache.placement == 'host' and not cache.reserved:
                    fused_units = [u for u in units if u.fused]
                    if fused_units and fused_units[0].v2a is not None:
                        width_kv = fused_units[0].v2a.inner.k.out_features
                        cache.reserve((1, latent_frames * (lh // 2) * (lw // 2), width_kv), len(fused_units))
            tick = time.perf_counter()
            torch.cuda.reset_peak_memory_stats(device)
            tvt = tv.unsqueeze(0).to(device=device, dtype=torch.float32)
            tat = ta.unsqueeze(0).to(device=device, dtype=torch.float32)
            x_in = torch.cat([latents, condition], dim=1)
            do_cfg = cfg_scale != 1.0 and (cfg_steps in ('all', None) or i < cfg_steps)
            sigma = float(scheduler.timestep_to_sigma(tv))
            teach = bool(audio_teacher) and float(audio_teacher.get('skip_below', -1.0)) < sigma <= float(
                audio_teacher.get('max_sigma', 1.01))
            # The teacher replaces this step's joint audio update: skip the student's audio CFG stream.
            a_cfg = audio_cfg is not None and audio_cfg != 1.0 and not do_cfg and not teach
            mode = tap = None
            if teach:
                # what the conditional student pass hands the audio teacher (StudentTap)
                if audio_teacher.get('calibrated'):
                    # Light: the student's own K/V of every fused layer, through the step's
                    # fitted maps when a maps file is given, else unchanged ("identity").
                    mode = 'calibrated'
                    gpu_maps = None
                    if audio_maps is not None:
                        from . import kv_calib
                        bucket, layers = kv_calib.maps_for(audio_maps, sigma, 1 if expert == 'high' else 2)
                        gpu_maps = kv_calib.layers_to(layers, device)
                    tap = StudentTap(cache, maps=gpu_maps)
                elif int(audio_teacher.get('start_layer') or 0):
                    mode = 'partial'
                    tap = StudentTap(cache, start=int(audio_teacher['start_layer']), host_states=cache.placement == 'host')
                else:
                    mode = 'full'
            common = dict(visual_latents=x_in, audio_latents=audio_latents, timestep=tvt, audio_timestep=tat,
                          video_fps=24.0, num_train_timesteps=scheduler.num_train_timesteps, residency=residency,
                          lean=lean, chunk=chunk, variant=variant)
            progress = (lambda block, step=i, branch='cond': layer_callback(step, branch, block, len(units))) \
                if layer_callback else None
            events_before = len(fb.events) if fb is not None else 0
            outs = transformer(units, roots, expert, context=pe, audio_context=ape,
                               neg_audio_context=ne if a_cfg else None, progress=progress, fbcache=fb, branch='cond',
                               tap=tap, **common)
            if tap is not None:
                tap.maps = None  # this step's maps leave the device
            _passed()
            vp, ap = outs[0].float(), outs[1].float()
            if a_cfg:
                apu = outs[2].float()
                ap = apu + audio_cfg * (ap - apu)
            del outs
            if do_cfg:
                progress = (lambda block, step=i: layer_callback(step, 'uncond', block, len(units))) \
                    if layer_callback else None
                vn, an = transformer(units, roots, expert, context=ne, audio_context=None, progress=progress, fbcache=fb,
                                     branch='uncond', **common)
                _passed()
                vp = vn.float() + cfg_scale * (vp - vn.float())
                a_scale = audio_cfg if audio_cfg is not None else cfg_scale
                ap = an.float() + a_scale * (ap - an.float())
                del vn, an
            nv = pairs[i + 1, 0] if i + 1 < total else None
            na = pairs[i + 1, 1] if i + 1 < total else None
            latents_next = scheduler.step_from_to(vp, tv, nv, latents)
            del vp
            teacher = None
            torch.cuda.synchronize(device)
            student_seconds = time.perf_counter() - tick
            if teach:
                tick_teacher = time.perf_counter()
                h2d_before = cache.h2d_bytes
                t_cfg = float(audio_teacher.get('cfg', 5.0))
                every = int(audio_teacher.get('every', 1))
                if mode == 'calibrated':
                    a_pred = None  # the student's mapped K/V are in the cache already
                elif every > 1 and i % every != 0 and cache.ks and mode == 'full':
                    a_pred = None  # research: reuse the last capture's K/V for this step
                else:
                    saved = None
                    if audio_teacher.get('sparsity') is not None:  # optional sparser base pass
                        sas = [u.video.self_attn for u in units]
                        saved = [sa.bsa_params.get('sparsity') for sa in sas]
                        for sa in sas:
                            sa.bsa_params['sparsity'] = float(audio_teacher['sparsity'])
                    try:
                        start = tap.start if mode == 'partial' else 0
                        a_pred = teacher_capture(units, roots, expert, cache, visual_latents=x_in,
                                                 audio_latents=audio_latents, context=pe, audio_context=ape, timestep=tvt,
                                                 audio_timestep=tat, num_train_timesteps=scheduler.num_train_timesteps,
                                                 residency=residency, chunk=chunk, start_layer=start,
                                                 init_states=tap.take_states() if start else None,
                                                 progress=audio_callback)
                    finally:
                        if saved is not None:
                            for sa, value in zip(sas, saved):
                                sa.bsa_params['sparsity'] = value
                _passed()
                torch.cuda.synchronize(device)
                capture_seconds = time.perf_counter() - tick_teacher
                del ap
                t1 = na if na is not None else torch.zeros_like(ta)
                # Sub-steps run beside little else: hold the streamed units' audio parts
                # and as many K/V layers as fit on the device for the four sub-steps.
                sub_res, held, kept = residency, None, 0
                count = sum(1 for u in units if u.fused)
                if substep_cache and (cache.placement == 'host' or getattr(residency, 'streamed', None)):
                    room = device_room(device, vram_limit) - SUBSTEP_RESERVE_BYTES
                    need = SubstepWeights.estimate(units, residency, count) if residency is not None else 0
                    if need and need <= room:
                        held = SubstepWeights(units, residency, count, device)
                        sub_res = held
                        room -= held.nbytes
                    kept = cache.keep(room)
                audio_latents = audio_substeps(units, roots, cache, scheduler, audio_latents, ta, t1,
                                               int(audio_teacher.get('substeps', 4)),
                                               a_pred.float() if (t_cfg == 1.0 and a_pred is not None) else None,
                                               ape, ne, t_cfg, sub_res,
                                               audio_teacher.get('min_sigma'), audio_teacher.get('rescale', 0.0),
                                               audio_teacher.get('power', 1.0), progress=audio_callback)
                torch.cuda.synchronize(device)
                cache.release_device()
                held_bytes = held.nbytes if held is not None else 0
                if held is not None:
                    held.close()
                    del held
                teacher = dict(mode=mode, capture_seconds=capture_seconds,
                               substep_seconds=time.perf_counter() - tick_teacher - capture_seconds,
                               kv_cache_bytes=cache.nbytes(), kv_placement=kv_placement,
                               kv_h2d_bytes=cache.h2d_bytes - h2d_before, substep_weights_bytes=held_bytes,
                               kv_kept_bytes=kept, kv_locked_bytes=cache.locked_bytes(), kv_spill=cache.spill_stats(),
                               peak_allocated_bytes=torch.cuda.max_memory_allocated(device))
                del a_pred
            else:
                audio_latents = scheduler.step_from_to(ap, ta, na, audio_latents)
                del ap
            latents = latents_next
            del x_in, latents_next
            import os
            if os.environ.get('FREEVIDEO_PRISM_DUMP'):  # diagnostics: per-step states
                torch.save(dict(video=latents.cpu(), audio=audio_latents.cpu()),
                           os.path.join(os.environ['FREEVIDEO_PRISM_DUMP'], 'step%02d.pt' % i))
            torch.cuda.synchronize(device)
            seconds = time.perf_counter() - tick
            log.append(dict(step=i, expert=expert, cfg=do_cfg, audio_cfg=a_cfg, seconds=seconds, variant=variant,
                            student_seconds=student_seconds,
                            fbcache=(fb.events[events_before:] if fb is not None else None),
                            audio_teacher=teacher, timestep=float(tv), audio_timestep=float(ta),
                            peak_allocated_bytes=torch.cuda.max_memory_allocated(device),
                            peak_reserved_bytes=torch.cuda.max_memory_reserved(device),
                            allocated_bytes=torch.cuda.memory_allocated(device)))
            if step_callback is not None:
                step_callback(i, seconds)
    finally:
        if cache is not None:
            cache.close()
        if fb is not None:
            fb.close()
    return latents, audio_latents, log


def _blend_v(a, b, extent):
    extent = min(a.shape[-2], b.shape[-2], extent)
    for y in range(extent):
        b[:, :, :, y, :] = a[:, :, :, -extent + y, :] * (1 - y / extent) + b[:, :, :, y, :] * (y / extent)
    return b


def _blend_h(a, b, extent):
    extent = min(a.shape[-1], b.shape[-1], extent)
    for x in range(extent):
        b[:, :, :, :, x] = a[:, :, :, :, -extent + x] * (1 - x / extent) + b[:, :, :, :, x] * (x / extent)
    return b


@torch.no_grad()
def tiled_vae_decode(vae, z, tile=256, stride=192, spatial=8):
    """Spatially tiled Wan VAE decode with diffusers' AutoencoderKLWan.tiled_decode
    tiles (256 px, stride 192) and linear blends, for any diffusers version (0.33
    has no Wan tiling). Each tile is a full causal ``vae.decode``; only the
    previous tile row is kept, so the peak is one tile's decoder state."""
    _, _, frames, height, width = z.shape
    lt, ls = tile // spatial, stride // spatial
    blend = tile - stride
    out = None
    previous = None
    for i in range(0, height, ls):
        row = []
        for j in range(0, width, ls):
            row.append(vae.decode(z[:, :, :, i:i + lt, j:j + lt]).sample)
        for j, piece in enumerate(row):
            if previous is not None:
                piece = _blend_v(previous[j], piece, blend)
            if j > 0:
                piece = _blend_h(row[j - 1], piece, blend)
            row[j] = piece
            if out is None:
                out = piece.new_empty((piece.shape[0], piece.shape[1], piece.shape[2], height * spatial, width * spatial))
            y0, x0 = i * spatial, j * stride
            h = min(stride, height * spatial - y0)
            w = min(stride, width * spatial - x0)
            out[:, :, :, y0:y0 + h, x0:x0 + w] = piece[:, :, :, :h, :w]
        previous = row
    return out.clamp_(-1.0, 1.0)


@torch.no_grad()
def decode_video(vae, vae_config, latents, tiling=False):
    """Research decode: denormalize, Wan VAE decode under bf16 autocast;
    ``tiling`` decodes in 256-pixel tiles (lower VRAM, seams blended)."""
    if getattr(vae, 'use_tiling', False):
        vae.use_tiling = False
    mean, std = latent_stats(vae_config, vae_config['z_dim'], latents.device, latents.dtype)
    with torch.autocast("cuda", dtype=torch.bfloat16):
        z = latents * std + mean
        if tiling:
            return tiled_vae_decode(vae, z, spatial=vae_config.get('scale_factor_spatial', 8))
        return vae.decode(z).sample


def frames_uint8(video, chunk=8):
    """VideoProcessor.postprocess_video(output_type='pil'): denormalize in the
    decoder's dtype (x/2 + 0.5, clamp), then FP32 x255 and round -> [F, H, W, 3]
    uint8 on the CPU, a few frames at a time."""
    frames = torch.empty((video.shape[2], video.shape[3], video.shape[4], 3), dtype=torch.uint8)
    for start in range(0, video.shape[2], chunk):
        part = (video[0, :, start:start + chunk] / 2 + 0.5).clamp(0, 1).float()
        frames[start:start + part.shape[1]] = (part.permute(1, 2, 3, 0) * 255).round().to(torch.uint8).cpu()
    return frames


@torch.no_grad()
def decode_audio(audio_vae, audio_latents):
    with torch.autocast("cuda", dtype=torch.float32):
        return audio_vae.decode(audio_latents)
