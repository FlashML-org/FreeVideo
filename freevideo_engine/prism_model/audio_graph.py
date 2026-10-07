# Prism (MIT, Tencent): vendored from the Prism single-GPU research branch
# (Prism-fast 0befcb7, hymm/fast/audio_graph.py) for FreeVideo; see NOTICE. FreeVideo
# changes: runs over (v2a conditioner, audio block) units instead of MOVABridge; the
# split-KV launch choice is kept on disk (PRISM_AUDIO_TUNE_CACHE).
"""Fast audio-only sub-steps (hymm.fast.audio_regen.audio_forward).

Two pieces:

1. ``v2a_attention``: cross-attention of the audio queries against the cached FP8
   v2a K/V of the video stream (187k keys at 720p/205f), reading the FP8 cache
   directly (no dequantized bf16 copy: the eager path spent ~90 ms per sub-step
   converting it). Split-KV flash attention in Triton: bf16 Q, K/V decoded to
   bf16 in registers, bf16 tensor-core dots with fp32 accumulation, the per-tensor
   K scale folded into the logits scale and the V scale into the output.

2. ``AudioGraph``: the whole audio_forward (30 audio blocks + v2a, CFG batch)
   captured as one CUDA graph with static input buffers. The K/V of a new cache
   are reached through a device-side pointer table, so switching caches costs a
   60-entry pointer update, not a recapture or a 17 GB copy.
"""
from __future__ import annotations

import math
import os
from typing import Optional

import torch

import triton
import triton.language as tl

ENABLED = os.environ.get('PRISM_AUDIO_FAST', '1') != '0'
GRAPH = os.environ.get('PRISM_AUDIO_GRAPH', '1') != '0'


@triton.jit
def _e4m3_to_bf16(bits, NATIVE: tl.constexpr):
    if NATIVE:
        return bits.to(tl.float8e4nv, bitcast=True).to(tl.bfloat16)
    b = bits.to(tl.int32) & 255  # integer decode for GPUs without FP8 conversions (SM80/86)
    exponent = (b >> 3) & 15
    mantissa = b & 7
    normal = ((exponent + 120) << 23) | (mantissa << 20)
    value = tl.where(exponent == 0, mantissa.to(tl.float32) * 0.001953125, normal.to(tl.float32, bitcast=True))
    return tl.where((b & 128) != 0, -value, value).to(tl.bfloat16)


@triton.jit
def _v2a_attn_split(Q, ANCHOR, KOFF, VOFF, KS, VS, OP, MP, LP, Lq, Lk, stride_q, stride_kv, layer, chunk, qk_scale,
                    H: tl.constexpr, D: tl.constexpr, BM: tl.constexpr, BN: tl.constexpr, NATIVE_FP8: tl.constexpr):
    pid_m = tl.program_id(0)
    h = tl.program_id(1)
    sp = tl.program_id(2)
    rm = pid_m * BM + tl.arange(0, BM)
    rd = tl.arange(0, D)
    rn = tl.arange(0, BN)
    q = tl.load(Q + rm[:, None] * stride_q + h * D + rd[None, :], mask=(rm < Lq)[:, None], other=0.)
    # K/V of layer ``layer`` = ANCHOR + byte offset from a device table: a captured CUDA
    # graph is pointed at a new cache by rewriting the table (no recapture / copy). An
    # aligned base pointer + offsets hinted as 16-byte multiples keep the FP8 loads
    # vectorized (a raw int64 -> pointer cast loses the alignment: 1.5x slower).
    kbase = ANCHOR + tl.multiple_of(tl.load(KOFF + layer), 16)
    vbase = ANCHOR + tl.multiple_of(tl.load(VOFF + layer), 16)
    # Dequantize exactly like the eager path (fp8 -> bf16, times the bf16-rounded scale,
    # rounded to bf16): same K/V values as KVCache.get, without materializing them.
    ksc = tl.load(KS + layer).to(tl.bfloat16).to(tl.float32)
    vsc = tl.load(VS + layer).to(tl.bfloat16).to(tl.float32)
    start = sp * chunk
    end = tl.minimum(start + chunk, Lk)
    m_i = tl.full((BM,), -float('inf'), tl.float32)
    l_i = tl.zeros((BM,), tl.float32)
    acc = tl.zeros((BM, D), tl.float32)
    for n0 in range(start, end, BN):
        cols = n0 + rn
        kmask = cols < end
        off = cols.to(tl.int64)[:, None] * stride_kv + h * D + rd[None, :]
        kb = tl.load(kbase + off, mask=kmask[:, None], other=0)
        k = (_e4m3_to_bf16(kb, NATIVE_FP8).to(tl.float32) * ksc).to(tl.bfloat16)
        qk = tl.dot(q, tl.trans(k)) * qk_scale
        qk = tl.where(kmask[None, :], qk, -float('inf'))
        m_new = tl.maximum(m_i, tl.max(qk, 1))
        alpha = tl.exp2(m_i - m_new)
        p = tl.exp2(qk - m_new[:, None])
        l_i = l_i * alpha + tl.sum(p, 1)
        vb = tl.load(vbase + off, mask=kmask[:, None], other=0)
        v = (_e4m3_to_bf16(vb, NATIVE_FP8).to(tl.float32) * vsc).to(tl.bfloat16)
        acc = acc * alpha[:, None] + tl.dot(p.to(tl.bfloat16), v)
        m_i = m_new
    base = (sp * H + h) * Lq
    rmask = rm < Lq
    tl.store(OP + (base + rm)[:, None].to(tl.int64) * D + rd[None, :], acc, mask=rmask[:, None])
    tl.store(MP + base + rm, m_i, mask=rmask)
    tl.store(LP + base + rm, l_i, mask=rmask)


@triton.jit
def _v2a_attn_combine(OP, MP, LP, OUT, Lq, stride_o, SPLITS: tl.constexpr, H: tl.constexpr,
                      D: tl.constexpr, BM: tl.constexpr):
    pid_m = tl.program_id(0)
    h = tl.program_id(1)
    rm = pid_m * BM + tl.arange(0, BM)
    rd = tl.arange(0, D)
    rmask = rm < Lq
    m = tl.full((BM,), -float('inf'), tl.float32)
    for s in range(SPLITS):
        m = tl.maximum(m, tl.load(MP + (s * H + h) * Lq + rm, mask=rmask, other=-float('inf')))
    l_tot = tl.zeros((BM,), tl.float32)
    acc = tl.zeros((BM, D), tl.float32)
    for s in range(SPLITS):
        ms = tl.load(MP + (s * H + h) * Lq + rm, mask=rmask, other=-float('inf'))
        w = tl.where(ms > -float('inf'), tl.exp2(ms - m), 0.)
        l_tot += w * tl.load(LP + (s * H + h) * Lq + rm, mask=rmask, other=0.)
        o = tl.load(OP + ((s * H + h) * Lq + rm)[:, None].to(tl.int64) * D + rd[None, :], mask=rmask[:, None],
                    other=0.)
        acc += w[:, None] * o
    out = acc / l_tot[:, None]
    tl.store(OUT + rm[:, None] * stride_o + h * D + rd[None, :], out.to(OUT.dtype.element_ty), mask=rmask[:, None])


class KVTable:
    """Device-side per-layer K/V address (offset from an anchor) and scale tables."""

    def __init__(self, n_layers, device):
        self.anchor = torch.empty(512, dtype=torch.uint8, device=device)  # 512-byte aligned (caching allocator)
        self.koff = torch.zeros(n_layers, dtype=torch.int64, device=device)
        self.voff = torch.zeros(n_layers, dtype=torch.int64, device=device)
        self.ks = torch.ones(n_layers, dtype=torch.float32, device=device)
        self.vs = torch.ones(n_layers, dtype=torch.float32, device=device)
        self.n = n_layers
        self.lk = None
        self.dim = None
        self._bound = None
        self._keep = None

    def bind_one(self, slot, k, v, ks, vs):
        """FreeVideo: point entry ``slot`` at one layer's FP8 K/V (host-streamed caches
        upload layer by layer into two device buffers); eager use only."""
        base = self.anchor.data_ptr()
        assert k.dtype == v.dtype == torch.float8_e4m3fn and k.is_contiguous() and v.is_contiguous()
        assert k.data_ptr() % 16 == 0 and v.data_ptr() % 16 == 0 and k.device == self.anchor.device
        self.koff[slot] = k.data_ptr() - base
        self.voff[slot] = v.data_ptr() - base
        self.ks[slot] = ks.reshape(()).float()
        self.vs[slot] = vs.reshape(()).float()
        self.lk, self.dim = k.shape[-2], k.shape[-1]
        self._bound = None

    def bind(self, cache):
        """Point the tables at ``cache`` (an audio_regen.KVCache with fp8=True). The
        cache must stay alive while kernels that use the table run."""
        ks, vs = cache.k, cache.v
        sig = tuple(t.data_ptr() for t in ks) + tuple(t.data_ptr() for t in vs) + \
            tuple(id(s) for s in cache.ks) + tuple(id(s) for s in cache.vs)
        if self._bound == sig:
            return
        assert len(ks) == self.n and cache.fp8, 'need an FP8 KVCache with one entry per fused block'
        lk, dim = ks[0].shape[-2], ks[0].shape[-1]
        base = self.anchor.data_ptr()
        offs = []
        for k, v in zip(ks, vs):
            assert k.shape[-2:] == (lk, dim) and v.shape[-2:] == (lk, dim) and k.shape[0] == 1 == v.shape[0]
            assert k.is_contiguous() and v.is_contiguous() and k.dtype == v.dtype == torch.float8_e4m3fn
            assert k.device == self.anchor.device and k.data_ptr() % 16 == 0 and v.data_ptr() % 16 == 0
            offs.append((k.data_ptr() - base, v.data_ptr() - base))
        o = torch.tensor(offs, dtype=torch.int64)
        self.koff.copy_(o[:, 0])
        self.voff.copy_(o[:, 1])
        self.ks.copy_(torch.stack([s.reshape(()).float() for s in cache.ks]))
        self.vs.copy_(torch.stack([s.reshape(()).float() for s in cache.vs]))
        self.lk, self.dim = lk, dim
        self._bound = sig


def _splits(n_tiles, sms, lk, bn):
    # ~20 programs per SM (H200 sweep: 32 splits best for 7 q-tiles x 12 heads)
    s = max(1, round(20 * sms / max(1, n_tiles)))
    return int(min(s, 128, max(1, lk // (8 * bn))))


_TUNED = {}
_CANDIDATES = [(128, 64, 4, 3), (128, 64, 8, 3), (128, 128, 8, 3), (128, 64, 8, 4),
               (64, 64, 4, 3), (64, 64, 4, 2), (64, 32, 4, 3)]  # BM=64: far fewer spills on mma.sync (SM8x/12x)


def _disk_key(q, table, num_heads):
    p = torch.cuda.get_device_properties(q.device)
    return 'v2a|%s|sm%d%d|t%s|%d|%d|%d|%d' % (p.name, p.major, p.minor, triton.__version__,
                                             q.shape[0] * q.shape[1], q.shape[2], table.lk, num_heads)


def _disk_tuned(name):
    """FreeVideo: the launch picked by timing is kept on disk, so every process on
    this GPU uses the same one (the split count and tiles set the summation order:
    re-timing per process gave other audio latents from one request to the next)."""
    path = os.environ.get('PRISM_AUDIO_TUNE_CACHE')
    if not path:
        return None
    try:
        import json
        with open(path) as f:
            cfg = json.load(f).get(name)
        return tuple(int(x) for x in cfg) if cfg else None
    except (OSError, ValueError, TypeError):
        return None


def _save_tuned(name, cfg):
    path = os.environ.get('PRISM_AUDIO_TUNE_CACHE')
    if not path:
        return
    import json
    try:
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        data[name] = list(cfg)
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        tmp = '%s.tmp%d' % (path, os.getpid())
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass


def _tune(q, table, layer, num_heads):
    """Pick (bm, bn, warps, stages, splits) for this shape once (first eager call;
    AudioGraph runs two eager warmups before capturing, so never during capture)."""
    key = (q.shape[0] * q.shape[1], q.shape[2], table.lk, num_heads, q.device.index)
    if key in _TUNED:
        return _TUNED[key]
    name = _disk_key(q, table, num_heads)
    saved = _disk_tuned(name)
    if saved is not None:
        _TUNED[key] = saved
        return saved
    best, best_t = None, float('inf')
    sms = _sms(q.device)
    for bm, bn, w, st in _CANDIDATES:
        n_m = triton.cdiv(q.shape[0] * q.shape[1], bm)
        base = _splits(n_m * num_heads, sms, table.lk, bn)
        for sp in sorted({max(1, base * 3 // 4), base, base * 3 // 2}):
            cfg = (bm, bn, w, st, sp)
            try:
                fn = lambda: v2a_attention(q, table, layer, num_heads, cfg=cfg)
                fn()
                torch.cuda.synchronize()
                ts = []
                for _ in range(3):
                    a, b = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
                    a.record()
                    fn()
                    b.record()
                    b.synchronize()
                    ts.append(a.elapsed_time(b))
                t = sorted(ts)[1]
            except Exception:
                continue
            if t < best_t:
                best, best_t = cfg, t
    if best is None:
        raise RuntimeError('v2a_attention: no kernel configuration runs on this GPU')
    _TUNED[key] = best
    _save_tuned(name, best)
    return best


def v2a_attention(q: torch.Tensor, table: KVTable, layer: int, num_heads: int, out: Optional[torch.Tensor] = None,
                  cfg: Optional[tuple] = None):
    """softmax(q k^T / sqrt(d)) v for q [B, Lq, H*D] (all B streams attend independently
    to the same keys) against layer ``layer`` of the bound FP8 cache. Returns [B, Lq, H*D].
    K/V are dequantized in registers exactly like KVCache.get (bf16(kq * bf16(scale)))."""
    b, lq_b, hd = q.shape
    d = hd // num_heads
    q2 = q.reshape(b * lq_b, hd)
    if q2.stride(-1) != 1:
        q2 = q2.contiguous()
    lq = q2.shape[0]
    lk = table.lk
    dev = q.device
    if cfg is None:
        cfg = _TUNED.get((lq, hd, lk, num_heads, dev.index))
        if cfg is None:
            if torch.cuda.is_current_stream_capturing():
                n_m = triton.cdiv(lq, 128)
                cfg = (128, 64, 4, 3, _splits(n_m * num_heads, _sms(dev), lk, 64))
            else:
                cfg = _tune(q, table, layer, num_heads)
    bm, bn, warps, stages, splits = cfg
    n_m = triton.cdiv(lq, bm)
    chunk = triton.cdiv(triton.cdiv(lk, splits), bn) * bn
    splits = triton.cdiv(lk, chunk)
    op = torch.empty((splits, num_heads, lq, d), device=dev, dtype=torch.float32)
    mp = torch.empty((splits, num_heads, lq), device=dev, dtype=torch.float32)
    lp = torch.empty_like(mp)
    if out is None:
        out = torch.empty((lq, hd), device=dev, dtype=q.dtype)
    qk_scale = (1.0 / math.sqrt(d)) * 1.4426950408889634
    _v2a_attn_split[(n_m, num_heads, splits)](
        q2, table.anchor, table.koff, table.voff, table.ks, table.vs, op, mp, lp, lq, lk, q2.stride(0), table.dim,
        layer, chunk, qk_scale, H=num_heads, D=d, BM=bm, BN=bn, NATIVE_FP8=_native_fp8(dev),
        num_warps=warps, num_stages=stages)
    _v2a_attn_combine[(triton.cdiv(lq, 64), num_heads)](
        op, mp, lp, out, lq, out.stride(0), SPLITS=splits, H=num_heads, D=d, BM=64, num_warps=4)
    return out.view(b, lq_b, hd)


_PROPS = {}


def _sms(dev):
    i = dev.index if dev.index is not None else torch.cuda.current_device()
    if i not in _PROPS:
        p = torch.cuda.get_device_properties(i)
        _PROPS[i] = (p.multi_processor_count, (p.major, p.minor) >= (8, 9))
    return _PROPS[i][0]


def _native_fp8(dev):
    _sms(dev)
    return _PROPS[dev.index if dev.index is not None else torch.cuda.current_device()][1]


# ---------------------------------------------------------------------------
# audio_forward with the fused v2a attention, optionally as one CUDA graph
# ---------------------------------------------------------------------------


def _v2a(cond, ax, table, i, arope):
    """= audio_regen._v2a_from_cache (non-pooled conditioner) on the fast attention."""
    inner = cond.inner
    q = inner.norm_q(inner.q(ax))
    if arope is not None:
        q = inner.rope_q(q, arope)  # audio_regen._rope_q: the compiled rotate-half RoPE
    return inner.o(v2a_attention(q, table, i, inner.num_heads))


def _body(bridge, table, lat, ta_in, ctx, arope, afreqs):
    """audio_regen.audio_forward with the fused v2a attention and precomputed RoPE
    tables (no host->device copies, so it is CUDA-graph capturable)."""
    from .wan_video_dit import sinusoidal_embedding_1d
    adit = bridge.audio_dit
    b = lat.shape[0]
    ta = ta_in.reshape(1).to(torch.float32).expand(b)
    with torch.autocast('cuda', dtype=torch.float32):
        at = adit.time_embedding(sinusoidal_embedding_1d(adit.freq_dim, ta))
        at_mod = adit.time_projection(at).unflatten(1, (6, adit.dim))
    dt = adit.dtype if hasattr(adit, 'dtype') else torch.bfloat16
    at, at_mod = at.to(dt), at_mod.to(dt)
    ax, (f,) = adit.patchify(lat.to(dt), None)
    for i, fb in enumerate(bridge.fusion_blocks):
        ax = ax + _v2a(fb.v2a_conditioner, ax, table, i, arope)
        ax = fb.audio_block(ax, ctx, at_mod, afreqs)
    return adit.unpatchify(adit.head(ax, at), (f,))


def _fingerprint(bridge):
    """Identity of every module/weight the captured graph points at."""
    from . import qblock
    out = [qblock.ENABLED, qblock.RESIDUAL_EPILOGUE]
    for fb in bridge.fusion_blocks:
        for owner in (fb.v2a_conditioner, fb.audio_block):
            for m in owner.modules():
                t = next(iter(m._parameters.values()), None)
                if t is None:
                    t = next((b for b in m._buffers.values() if b is not None), None)
                out.append((id(m), type(m), t.data_ptr() if t is not None else 0))
    return tuple(out)


class _Entry:
    pass


class AudioGraph:
    """Callable like audio_regen.audio_forward(bridge, cache, latents, t, ctx)."""

    def __init__(self, bridge, graph: bool = True):
        self.bridge = bridge
        self.graph = graph
        self.table = None
        self.entries = {}
        self.fp = None
        self.captures = 0

    def _afreqs(self, f, device):
        from .mova import assemble_audio_freqs
        return assemble_audio_freqs(self.bridge.audio_dit.freqs, f, device)

    def __call__(self, cache, audio_latents, audio_t, audio_ctx_emb):
        dev = audio_latents.device
        n = len(self.bridge.fusion_blocks)
        if self.table is None or self.table.n != n or self.table.anchor.device != dev:
            self.table = KVTable(n, dev)
            self.entries = {}
        self.table.bind(cache)
        ta = audio_t if torch.is_tensor(audio_t) else torch.tensor(float(audio_t))
        ta = ta.reshape(1).to(dev, torch.float32)
        cos, sin = cache.arope
        f = audio_latents.shape[-1]
        if not self.graph:
            return _body(self.bridge, self.table, audio_latents, ta, audio_ctx_emb, (cos, sin), self._afreqs(f, dev))
        fp = _fingerprint(self.bridge)
        if fp != self.fp:
            self.entries = {}
            self.fp = fp
        key = (tuple(audio_latents.shape), audio_latents.dtype, tuple(audio_ctx_emb.shape), audio_ctx_emb.dtype,
               tuple(cos.shape), cos.dtype, self.table.lk, self.table.dim)
        e = self.entries.get(key)
        if e is None:
            e = self._capture(key, audio_latents, ta, audio_ctx_emb, cos, sin)
            self.entries[key] = e
        e.lat.copy_(audio_latents)
        e.ta.copy_(ta)
        e.ctx.copy_(audio_ctx_emb)
        e.cos.copy_(cos)
        e.sin.copy_(sin)
        e.g.replay()
        return e.out.clone()

    def _capture(self, key, lat, ta, ctx, cos, sin):
        e = _Entry()
        e.lat, e.ta, e.ctx = lat.clone(), ta.clone(), ctx.clone()
        e.cos, e.sin = cos.clone(), sin.clone()
        e.afreqs = self._afreqs(lat.shape[-1], lat.device)
        run = lambda: _body(self.bridge, self.table, e.lat, e.ta, e.ctx, (e.cos, e.sin), e.afreqs)
        s = torch.cuda.Stream(device=lat.device)
        s.wait_stream(torch.cuda.current_stream(lat.device))
        with torch.cuda.stream(s):
            for _ in range(2):  # JIT compiles, autotune / config caches, cuBLAS workspaces
                run()
        torch.cuda.current_stream(lat.device).wait_stream(s)
        e.g = torch.cuda.CUDAGraph()
        with torch.cuda.graph(e.g):
            e.out = run()
        self.captures += 1
        return e


def audio_forward(bridge, cache, audio_latents, audio_t, audio_ctx_emb):
    """Drop-in for audio_regen.audio_forward: fused v2a attention + CUDA graph
    (PRISM_AUDIO_GRAPH=0: fused attention only). Returns None when not applicable."""
    if not ENABLED or not getattr(cache, 'fp8', False) or not audio_latents.is_cuda:
        return None
    if any(getattr(fb.v2a_conditioner, 'pooled_adaln', False) for fb in bridge.fusion_blocks):
        return None
    if torch.is_grad_enabled() and audio_latents.requires_grad:
        return None
    state = bridge.__dict__.setdefault('_prism_audio_state', {'graph': GRAPH, 'fused': True})
    if not state['fused']:
        return None
    r = bridge.__dict__.get('_prism_audio_graph')
    if r is None or r.graph != state['graph']:
        r = AudioGraph(bridge, graph=state['graph'])
        bridge.__dict__['_prism_audio_graph'] = r
    try:
        return r(cache, audio_latents, audio_t, audio_ctx_emb)
    except Exception as e:  # degrade: graph -> fused attention only -> original eager path
        import warnings
        if state['graph']:
            state['graph'] = False
            warnings.warn('audio_graph: CUDA graph failed (%s: %s); using the fused attention without a graph'
                          % (type(e).__name__, str(e).splitlines()[0][:200] if str(e) else ''))
            bridge.__dict__.pop('_prism_audio_graph', None)
            torch.cuda.synchronize()
            return audio_forward(bridge, cache, audio_latents, audio_t, audio_ctx_emb)
        state['fused'] = False
        warnings.warn('audio_graph: fused v2a attention failed (%s: %s); using the original audio_forward'
                      % (type(e).__name__, str(e).splitlines()[0][:200] if str(e) else ''))
        return None
