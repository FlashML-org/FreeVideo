# Prism (MIT, Tencent): vendored from the Prism single-GPU research branch
# (Prism-fast 0befcb7 + the opt-in Sol routing of 98b7cc8, hymm/fast/ivpq_fast.py) for FreeVideo; see NOTICE.
"""Fused IVPQ dynamic-block BSA forward for inference (no grad).

Same algorithm as ``dynamic_block_attention.flash_attn_bsa_3d_dynamic`` (same
shape_ids, same selected blocks, same per-tile k lists in the same order), with
the memory traffic and copies of the reference path removed:

* no permute/pad/index_select copies of q/k/v: a row map ``src_u`` (tile-layout
  row -> input token, -1 = padding) is applied inside the kernels that read them,
  and the output is scattered straight into the caller's THW [B, S, H*D] layout
  (padding rows are never stored), so the inverse index_select + crop + rearrange
  copies disappear too;
* heads are processed ``head_chunk`` at a time (default 8): block scoring and
  selection are batched over the chunk, and every per-head buffer is chunk sized;
* the per-q-tile expanded + sorted ``tile_block_indices`` is replaced by per-
  logical-block tile lists built by a compaction kernel (no sort; ascending order
  is preserved) plus ``block_id_of_tile`` as a row map; the two q tiles of a
  128-token block share a list row, so the Sage kernel runs them as one BLOCK_M=128
  program without any list comparison;
* no host syncs: the block count N_b is read through an async pinned copy that
  overlaps the first chunk's gathers, and invalid blocks are always masked (a
  no-op when there are none) instead of testing ``block_valid.all()`` on the host.

* selection without sorts (PRISM_BSA_FAST_SELECT=1, default): when the cdf term
  cannot bind (int(cdf*N_b)+1 <= the top-k count, e.g. sparsity 0.75 + cdf 0.2),
  the reference keeps exactly torch.topk(softmax(score), k)'s set; a Triton radix
  select reproduces that set (ties included) and the tile lists are compacted from
  the membership mask in ascending order - no topk sort, no index sort, no cumsum.
  Fully padded key blocks are set to -inf column-wise instead of a full masked_fill.
  Other configs use the reference compiled selection functions.
* K smoothing uses k_mean derived from the selection's tile means (exact for softmax),
  so the Sage pre-pass reads K once less.

Exactness: the tile means for scoring are computed by the same compiled
``masked_mean_pooling_compression`` on a gathered chunk (bit-identical to the
full-tensor call), scoring/selection call the same compiled functions batched over
heads (bit-identical to the per-head loop), and with ``pv=None`` the attention
itself is the original bf16 kernel on gathered chunks -> bit-identical output to
the reference path on valid tokens. With pv in {'fp16','fp16acc','fp8','auto'} the
Sage INT8 kernel (hymm.fast.sage_bsa) is used with gather-quantization and a
scatter epilogue (equal, bit for bit, to the Sage patch on the reference path).

Entry points:
  fused_dynamic_bsa(q, k, v, grid_size, num_heads, ...)  q/k/v [B,S,H*D] THW unpadded
      -> [B,S,H*D]   (used by SelfAttention.forward)
  flash_attn_bsa_3d_dynamic_fused(q, k, v, grid_padded, ...)  drop-in for the
      reference signature (q/k/v [B,H,S_pad,D]) -> [B,H,S_pad,D]
Switches (read per call): PRISM_BSA_FUSED=0 disables the fused path;
PRISM_BSA_HEAD_CHUNK=n; PRISM_SAGE_BSA=fp8|fp16|fp16acc|auto selects the Sage
kernel (as does an active hymm.fast.sage_bsa.patch_prism()).
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

import torch
import triton
import triton.language as tl

from .block_sparse_attention import dynamic_block_attention as dba
from .block_sparse_attention.dynamic_block_shape import ZONE_SIZE, select_zone_shapes

TILE = 64
STATS = {"calls": 0, "sage_calls": 0, "sol_calls": 0}


def fused_enabled() -> bool:
    return os.environ.get("PRISM_BSA_FUSED", "1") != "0"


def head_chunk_default() -> int:
    return int(os.environ.get("PRISM_BSA_HEAD_CHUNK", "8"))


def sage_mode() -> Optional[str]:
    """PV mode for the Sage kernel, or None for the exact bf16 kernel."""
    env = os.environ.get("PRISM_SAGE_BSA")
    if env:
        return None if env.lower() in ("0", "off", "none", "exact", "bf16") else env
    try:
        from . import sage_bsa as sb
    except Exception:  # noqa: BLE001
        return None
    if sb._PATCH_STATE.get("active"):
        return sb._PATCH_STATE.get("pv") or "auto"
    return None


# =====================================================================
# Kernels
# =====================================================================
@triton.jit
def _gather_rows_kernel(X, SRCU, OUT, stride_xz, stride_xh, stride_xs, H, R,
                        D: tl.constexpr, BLOCK: tl.constexpr):
    """OUT[bh, r, :] = X[b, h, SRCU[r], :] (0 where SRCU[r] < 0); OUT contiguous [B*H, R, D]."""
    pid = tl.program_id(0)
    bh = tl.program_id(1)
    z = bh // H
    h = bh % H
    rows = pid * BLOCK + tl.arange(0, BLOCK)
    m = rows < R
    u = tl.load(SRCU + rows, mask=m, other=-1)
    ok = u >= 0
    d = tl.arange(0, D)
    x = tl.load(X + z.to(tl.int64) * stride_xz + h.to(tl.int64) * stride_xh
                + tl.where(ok, u, 0).to(tl.int64)[:, None] * stride_xs + d[None, :],
                mask=ok[:, None], other=0.0)
    tl.store(OUT + bh.to(tl.int64) * R * D + rows.to(tl.int64)[:, None] * D + d[None, :], x, mask=m[:, None])


@triton.jit
def _scatter_rows_kernel(X, SRCU, OUT, stride_oz, stride_oh, stride_os, H, R,
                         D: tl.constexpr, BLOCK: tl.constexpr):
    """OUT[b, h, SRCU[r], :] = X[bh, r, :] for SRCU[r] >= 0; X contiguous [B*H, R, D]."""
    pid = tl.program_id(0)
    bh = tl.program_id(1)
    z = bh // H
    h = bh % H
    rows = pid * BLOCK + tl.arange(0, BLOCK)
    u = tl.load(SRCU + rows, mask=rows < R, other=-1)
    ok = u >= 0
    d = tl.arange(0, D)
    x = tl.load(X + bh.to(tl.int64) * R * D + rows.to(tl.int64)[:, None] * D + d[None, :], mask=ok[:, None])
    tl.store(OUT + z.to(tl.int64) * stride_oz + h.to(tl.int64) * stride_oh
             + tl.where(ok, u, 0).to(tl.int64)[:, None] * stride_os + d[None, :], x, mask=ok[:, None])


@triton.jit
def _compact_tiles_kernel(SEL, KBT0, KBT1, TVALID, OUT, LENS,
                          stride_sz, stride_sh, stride_sm,
                          H, NB, R_SEL, L_OUT, N_TILES,
                          CH: tl.constexpr):
    """Per (b, h, q-block): expand the selected logical blocks (sorted, sentinel NB)
    into their valid tiles, in ascending tile order, without a sort.

    Equivalent to the reference cat([t0, t1]) -> drop sentinel/fully-padded tiles
    -> sort: tile ids are monotone in block id and t0 < t1 within a block.
    """
    row = tl.program_id(0)
    bh = tl.program_id(1)
    z = bh // H
    h = bh % H
    sel_row = SEL + z.to(tl.int64) * stride_sz + h.to(tl.int64) * stride_sh + row.to(tl.int64) * stride_sm
    out_row = OUT + (bh.to(tl.int64) * NB + row) * L_OUT
    cnt = tl.full([], 0, tl.int32)
    for s in range(0, R_SEL, CH):
        j = s + tl.arange(0, CH)
        blk = tl.load(sel_row + j, mask=j < R_SEL, other=NB).to(tl.int32)
        blk = tl.minimum(tl.maximum(blk, 0), NB)
        t0 = tl.load(KBT0 + blk)
        t1 = tl.load(KBT1 + blk)
        v0 = (t0 < N_TILES) & (tl.load(TVALID + tl.minimum(t0, N_TILES - 1)) != 0)
        v1 = (t1 < N_TILES) & (tl.load(TVALID + tl.minimum(t1, N_TILES - 1)) != 0)
        c0 = v0.to(tl.int32)
        c = c0 + v1.to(tl.int32)
        pos = cnt + tl.cumsum(c, 0) - c
        tl.store(out_row + pos, t0, mask=v0)
        tl.store(out_row + pos + c0, t1, mask=v1)
        cnt += tl.sum(c, 0)
    tl.store(LENS + bh.to(tl.int64) * NB + row, cnt)


@triton.jit
def _topk_kernel(X, MASK, KBT0, KBT1, TVALID, OUT, LENS, n_rows, NCOL, K, stride_x, stride_m,
                 L_OUT, N_TILES, ROWS: tl.constexpr, BLOCK: tl.constexpr, EMIT_TILES: tl.constexpr):
    """Per row of X (ROWS rows per program): the set of torch.topk(X[row], K) indices
    (all values > the K-th value, then ties equal to it by ascending index), found
    by a 32-step bit search on order-preserving uint32 keys held in registers.
    EMIT_TILES=False: MASK[row, j] = membership (int8).
    EMIT_TILES=True : expand the selected logical blocks j (ascending) into their
    valid tiles (KBT0/KBT1, TVALID) -> OUT[row, :LENS[row]], i.e. the reference
    sort + expand + filter, fused."""
    r = tl.program_id(0) * ROWS + tl.arange(0, ROWS)
    rm = r < n_rows
    cols = tl.arange(0, BLOCK)
    cm = cols < NCOL
    m2 = rm[:, None] & cm[None, :]
    x = tl.load(X + r.to(tl.int64)[:, None] * stride_x + cols[None, :], mask=m2, other=0.0).to(tl.float32)
    bits = x.to(tl.uint32, bitcast=True)
    key = tl.where((bits >> 31) != 0, ~bits, bits | 0x80000000)
    key = tl.where(cm[None, :], key, 0)         # padding lanes rank below every value (incl. -inf)
    kth = tl.zeros([ROWS], dtype=tl.uint32)
    for b in tl.static_range(32):
        cand = kth | (1 << (31 - b))
        cnt = tl.sum((key >= cand[:, None]).to(tl.int32), axis=1)
        kth = tl.where(cnt >= K, cand, kth)
    gt = key > kth[:, None]
    need = K - tl.sum(gt.to(tl.int32), axis=1)
    eq = (key == kth[:, None]) & cm[None, :]
    sel = gt | (eq & (tl.cumsum(eq.to(tl.int32), axis=1) <= need[:, None]))
    if EMIT_TILES:
        cidx = tl.zeros([ROWS, BLOCK], dtype=tl.int32) + cols[None, :]
        t0 = tl.load(KBT0 + cidx, mask=sel, other=N_TILES)
        t1 = tl.load(KBT1 + cidx, mask=sel, other=N_TILES)
        v0 = sel & (t0 < N_TILES) & (tl.load(TVALID + tl.minimum(t0, N_TILES - 1), mask=sel, other=0) != 0)
        v1 = sel & (t1 < N_TILES) & (tl.load(TVALID + tl.minimum(t1, N_TILES - 1), mask=sel, other=0) != 0)
        c0 = v0.to(tl.int32)
        c = c0 + v1.to(tl.int32)
        pos = tl.cumsum(c, axis=1) - c
        orow = OUT + r.to(tl.int64)[:, None] * L_OUT
        tl.store(orow + pos, t0, mask=v0 & rm[:, None])
        tl.store(orow + pos + c0, t1, mask=v1 & rm[:, None])
        tl.store(LENS + r, tl.sum(c, axis=1), mask=rm)
    else:
        tl.store(MASK + r.to(tl.int64)[:, None] * stride_m + cols[None, :], sel.to(tl.int8), mask=m2)


@triton.jit
def _topk_hist_kernel(X, MASK, NCOL, K, stride_x, stride_m, BLOCK: tl.constexpr):
    """Same result as _topk_kernel(EMIT_TILES=False) with an 8-bit-digit radix select:
    4 histogram passes over the row (vs 32 bit-count passes). Rows of up to BLOCK."""
    row = tl.program_id(0)
    cols = tl.arange(0, BLOCK)
    cm = cols < NCOL
    x = tl.load(X + row.to(tl.int64) * stride_x + cols, mask=cm, other=0.0).to(tl.float32)
    bits = x.to(tl.uint32, bitcast=True)
    key = tl.where((bits >> 31) != 0, ~bits, bits | 0x80000000)
    key = tl.where(cm, key, 0)
    bins = tl.arange(0, 256)
    prefix = tl.full([], 0, tl.uint32)
    kk = tl.full([], 0, tl.int32) + K            # rank still to find inside the matching set
    for p in tl.static_range(4):
        shift = 24 - 8 * p
        digit = ((key >> shift) & 255).to(tl.int32)
        if p == 0:
            match = cm | (~cm)                    # all lanes (padding has key 0 -> bin 0)
            n_nonmatch = tl.full([], 0, tl.int32)
        else:
            match = (key >> (shift + 8)) == prefix
            n_nonmatch = tl.sum((~match).to(tl.int32), axis=0)
        digit = tl.where(match, digit, 0)
        hist = tl.histogram(digit, 256)
        hist = tl.where(bins == 0, hist - n_nonmatch, hist)
        if p == 0:
            hist = tl.where(bins == 0, hist - (BLOCK - NCOL), hist)   # padding lanes never rank
        csum = tl.cumsum(hist, axis=0)
        total = tl.sum(hist, axis=0)
        suffix = total - (csum - hist)           # count of matching digits >= bin
        bstar = tl.sum((suffix >= kk).to(tl.int32), axis=0) - 1
        above = tl.sum(tl.where(bins > bstar, hist, 0), axis=0)
        kk = kk - above
        prefix = (prefix << 8) | bstar.to(tl.uint32)
    kth = prefix
    gt = key > kth
    need = K - tl.sum(gt.to(tl.int32), axis=0)
    eq = (key == kth) & cm
    take = eq & (tl.cumsum(eq.to(tl.int32), axis=0) <= need)
    tl.store(MASK + row.to(tl.int64) * stride_m + cols, (gt | take).to(tl.int8), mask=cm)


def _topk_cfg(N):
    # measured on H200 for N=3312 (mask mode): rows=1/warps=8 0.87 ms per 26.5k rows
    blk = triton.next_power_of_2(N)
    return blk, 1, (4 if blk <= 1024 else 8 if blk <= 8192 else 16)


@triton.jit
def _compact_mask_kernel(MASK, KBT0, KBT1, TVALID, OUT, LENS, stride_m, H, NB, L_OUT, N_TILES,
                         CH: tl.constexpr):
    """Like _compact_tiles_kernel but from a per-row block mask (ascending by construction)."""
    row = tl.program_id(0)
    bh = tl.program_id(1)
    m_row = MASK + (bh.to(tl.int64) * NB + row) * stride_m
    out_row = OUT + (bh.to(tl.int64) * NB + row) * L_OUT
    cnt = tl.full([], 0, tl.int32)
    for s0 in range(0, NB, CH):
        j = s0 + tl.arange(0, CH)
        jm = j < NB
        sel = (tl.load(m_row + j, mask=jm, other=0) != 0) & jm
        t0 = tl.load(KBT0 + j, mask=sel, other=N_TILES)
        t1 = tl.load(KBT1 + j, mask=sel, other=N_TILES)
        v0 = sel & (t0 < N_TILES) & (tl.load(TVALID + tl.minimum(t0, N_TILES - 1)) != 0)
        v1 = sel & (t1 < N_TILES) & (tl.load(TVALID + tl.minimum(t1, N_TILES - 1)) != 0)
        c0 = v0.to(tl.int32)
        c = c0 + v1.to(tl.int32)
        pos = cnt + tl.cumsum(c, 0) - c
        tl.store(out_row + pos, t0, mask=v0)
        tl.store(out_row + pos + c0, t1, mask=v1)
        cnt += tl.sum(c, 0)
    tl.store(LENS + bh.to(tl.int64) * NB + row, cnt)


@triton.jit
def _fill_cols_kernel(S, IDX, NINV, n_rows, stride, ROWS: tl.constexpr):
    rows = tl.program_id(0) * ROWS + tl.arange(0, ROWS)
    rm = rows < n_rows
    n = tl.load(NINV)
    for i in range(0, n):
        c = tl.load(IDX + i)
        tl.store(S + rows.to(tl.int64) * stride + c, float("-inf"), mask=rm)


@torch.compile
def _softmax_weights(score, sm_scale):
    # same expression as get_select_indices_cdf_topk_from_score's first line
    return torch.softmax(score * sm_scale, dim=-1)


def topk_mask(x: torch.Tensor, k: int) -> torch.Tensor:
    """x [B,h,R,N] contiguous -> int8 mask of torch.topk(x, k, -1).indices (same set)."""
    B, h, R, N = x.shape
    x2 = x.reshape(-1, N)
    mask = torch.empty(x2.shape, dtype=torch.int8, device=x.device)
    if k <= 0:
        return mask.zero_().view(B, h, R, N)
    blk, rows, warps = _topk_cfg(N)
    if os.environ.get("PRISM_TOPK_HIST", "0") == "1":
        _topk_hist_kernel[(x2.shape[0],)](x2, mask, N, k, x2.stride(0), mask.stride(0), BLOCK=blk,
                                          num_warps=warps)
    else:
        _topk_kernel[(triton.cdiv(x2.shape[0], rows),)](
            x2, mask, mask, mask, mask, mask, mask, x2.shape[0], N, k, x2.stride(0), mask.stride(0), 0, 0,
            ROWS=rows, BLOCK=blk, EMIT_TILES=False, num_warps=warps)
    return mask.view(B, h, R, N)


def topk_tiles(x: torch.Tensor, k: int, geo) -> tuple:
    """x [B,h,N_b,N_b] scores/weights -> (tile lists [B,h,N_b,2k] int32, lens [B,h,N_b])
    equal to the reference topk -> sort -> expand -> drop invalid tiles."""
    B, h, R, N = x.shape
    x2 = x.reshape(-1, N)
    L = 2 * max(1, k)
    lists = torch.empty((B, h, R, L), dtype=torch.int32, device=x.device)
    lens = torch.empty((B, h, R), dtype=torch.int32, device=x.device)
    if k <= 0:
        lens.zero_()
        return lists, lens
    blk, rows, warps = _topk_cfg(N)
    _topk_kernel[(triton.cdiv(x2.shape[0], rows),)](
        x2, lists, geo.kbt0, geo.kbt1, geo.tile_valid_u8, lists, lens, x2.shape[0], N, k, x2.stride(0), 0,
        L, geo.N_tiles, ROWS=rows, BLOCK=blk, EMIT_TILES=True, num_warps=warps)
    return lists, lens


@triton.jit
def _tile_mean_kernel(X, VALID, OUT, R, N_TILES, D: tl.constexpr, TILE_: tl.constexpr, HAS_MASK: tl.constexpr):
    """OUT[bh, t, :] = mean of X[bh, t*TILE:(t+1)*TILE, :] over the valid rows (fp32).
    One program per (tile, head): the summation order is fixed by TILE and D alone."""
    t = tl.program_id(0)
    bh = tl.program_id(1)
    rows = t * TILE_ + tl.arange(0, TILE_)
    d = tl.arange(0, D)
    x = tl.load(X + bh.to(tl.int64) * R * D + rows.to(tl.int64)[:, None] * D + d[None, :]).to(tl.float32)
    if HAS_MASK:
        m = tl.load(VALID + rows).to(tl.float32)
        y = tl.sum(x * m[:, None], axis=0) / tl.maximum(tl.sum(m, axis=0), 1.0)
    else:
        y = tl.sum(x, axis=0) / TILE_
    tl.store(OUT + (bh.to(tl.int64) * N_TILES + t) * D + d, y)


def _tile_means(xc, valid_re):
    """FreeVideo: (masked_)mean_pooling_compression(xc, TILE, valid_re) with a fixed
    summation order. The torch.compile'd originals are specialised per shape, so a
    head chunk of 1 or 4 (small-GPU plans) or another autotuned config averaged the
    same tile differently (7.7e-5 in one block, ~3% after a pass through the top-k).
    xc: contiguous [B, h, R, D] with R a multiple of TILE. Masked: fp32 (as the
    original); unmasked: xc.dtype (the original's mean keeps the input dtype)."""
    B, h, R, D = xc.shape
    assert R % TILE == 0 and xc.is_contiguous()
    n = R // TILE
    out = torch.empty((B, h, n, D), dtype=torch.float32, device=xc.device)
    has_mask = valid_re is not None
    valid = valid_re.view(torch.uint8) if has_mask else xc
    _tile_mean_kernel[(n, B * h)](xc, valid, out, R, n, D=D, TILE_=TILE, HAS_MASK=has_mask, num_warps=4)
    return out if has_mask else out.to(xc.dtype)


def _gather(x4, src_u, R):
    """x4: [B, h, S_in, D] strided view (D contiguous) -> contiguous [B, h, R, D]."""
    B, h, _, D = x4.shape
    out = torch.empty((B, h, R, D), dtype=x4.dtype, device=x4.device)
    _gather_rows_kernel[(triton.cdiv(R, 64), B * h)](
        x4, src_u, out, x4.stride(0), x4.stride(1), x4.stride(2), h, R, D=D, BLOCK=64, num_warps=4)
    return out


def _scatter(x, src_u, out4):
    """x: contiguous [B, h, R, D] -> out4 (strided [B, h, S_out, D]) at rows src_u."""
    B, h, R, D = x.shape
    _scatter_rows_kernel[(triton.cdiv(R, 64), B * h)](
        x, src_u, out4, out4.stride(0), out4.stride(1), out4.stride(2), h, R, D=D, BLOCK=64, num_warps=4)


# =====================================================================
# Geometry (no host syncs)
# =====================================================================
class _Geometry:
    """Per-call, head-independent bookkeeping. N_b comes from an async pinned copy."""

    def __init__(self, shape_ids, grid_padded, valid_re, src_u, device):
        Tp, Hp, Wp = grid_padded
        ZT, ZH, ZW = Tp // ZONE_SIZE, Hp // ZONE_SIZE, Wp // ZONE_SIZE
        Nz = ZT * ZH * ZW
        _, products, tiles_per_block = dba._precompute_local_orders(device)
        tpb = tiles_per_block[shape_ids]
        nbz = 512 // products[shape_ids]
        self._nb_host = torch.empty((), dtype=torch.int64, pin_memory=True)
        self._nb_host.copy_(nbz.sum(), non_blocking=True)
        self._nb_event = torch.cuda.Event()
        self._nb_event.record()
        self._nb = None
        self.N_tiles = N_tiles = Nz * (512 // TILE)
        block_offset = torch.zeros(Nz, dtype=torch.long, device=device)
        if Nz > 1:
            block_offset[1:] = torch.cumsum(nbz, dim=0)[:-1]
        tile_g = torch.arange(N_tiles, device=device)
        z_of_tile = tile_g // 8
        tpb_t = tpb[z_of_tile]
        pos_in_block = (tile_g % 8) % tpb_t
        self.block_id_of_tile = block_offset[z_of_tile] + (tile_g % 8) // tpb_t       # int64 [N_tiles]
        # kblock_tiles sized by the upper bound N_tiles (+1): rows >= N_b stay sentinel,
        # so the sentinel row N_b is correct without knowing N_b on the host.
        kbt = torch.full((N_tiles + 1, 2), N_tiles, dtype=torch.int32, device=device)
        kbt[self.block_id_of_tile, pos_in_block] = tile_g.to(torch.int32)
        self.kbt0 = kbt[:, 0].contiguous()
        self.kbt1 = kbt[:, 1].contiguous()
        self.valid_re = valid_re                       # bool [S_p] or None
        if valid_re is not None:
            self.tile_counts = valid_re.view(N_tiles, TILE).sum(dim=-1).float()
        else:
            self.tile_counts = torch.full((N_tiles,), float(TILE), device=device)
        self.tile_valid = self.tile_counts > 0
        self.tile_valid_u8 = self.tile_valid.to(torch.uint8)
        self.src_u = src_u                             # int32 [S_p]
        bid = self.block_id_of_tile
        merged = (bid[0::2] == bid[1::2]) & self.tile_valid[0::2] & self.tile_valid[1::2]
        self.pair_merged = merged.to(torch.int8).contiguous()                      # [N_tiles/2]
        skip = torch.repeat_interleave(merged, 2) | ~self.tile_valid
        self.tile_skip_merged = skip.to(torch.int8).contiguous()                   # MODE 2 launch
        self.tile_skip_single = (~self.tile_valid).to(torch.int8).contiguous()    # MODE 0 launch
        self.bid_i32 = bid.to(torch.int32).contiguous()

    @property
    def N_b(self) -> int:
        if self._nb is None:
            self._nb_event.synchronize()
            self._nb = int(self._nb_host.item())
        return self._nb

    def invalid_blocks(self):
        """(int32 indices of fully padded logical blocks, padded to N_b; int32 count),
        both on device (no host sync)."""
        if not hasattr(self, "_inv"):
            bv = self.block_masks()
            order = torch.argsort(bv.to(torch.int8), stable=True).to(torch.int32)   # invalid first
            self._inv = (order.contiguous(), (~bv).sum().to(torch.int32).reshape(1))
        return self._inv

    def block_masks(self):
        if not hasattr(self, "_bv"):
            block_count = self.tile_counts.new_zeros(self.N_b)
            block_count.index_add_(0, self.block_id_of_tile, self.tile_counts)
            self._bv = block_count > 0
        return self._bv


# =====================================================================
# Selection for one head chunk (same compiled ops as the reference)
# =====================================================================
@torch.compile
def _sol_threshold_keep(score, beta: float):
    """Sol-Attn query-dependent threshold (arXiv 2607.24027 Eq. 4): keep block j of row i
    if score_ij > mu_i + beta * sigma_i, mean/std over the row's finite (non-padded)
    entries; the row maximum is always kept. (Prism-fast 98b7cc8)"""
    fin = torch.isfinite(score)
    s0 = torch.where(fin, score, 0.0)
    n = fin.sum(-1, keepdim=True).clamp(min=1).to(score.dtype)
    mu = s0.sum(-1, keepdim=True) / n
    var = (s0 * s0).sum(-1, keepdim=True) / n - mu * mu
    tau = mu + beta * var.clamp(min=0.0).sqrt()
    return (score > tau) | (score >= score.amax(-1, keepdim=True))


def _sol_mask(score, sparsity, sol_beta):
    """Sol threshold routing with the sparsity budget as a cap: the top-k_cap set
    intersected with the mu + beta*sigma threshold (research 98b7cc8)."""
    k_cap = max(1, int((1 - sparsity) * score.shape[-1]))
    keep = _sol_threshold_keep(score, float(sol_beta))
    m = topk_mask(score.contiguous(), k_cap)
    m.mul_(keep.to(torch.int8))
    return m, k_cap


def _score(q_cmp, k_cmp, geo: _Geometry, has_padding):
    from .block_sparse_attention.bsa_interface import cal_score
    score = cal_score(q_cmp, k_cmp)
    if has_padding:
        # reference applies this only when some block is fully padded; with none it
        # is a no-op, so applying it whenever padding exists avoids the host check.
        # only the fully padded key blocks' columns are written (same values as
        # the reference masked_fill, without a full read+write of the score matrix)
        idx, n_inv = geo.invalid_blocks()
        s2 = score.view(-1, score.shape[-1])
        _fill_cols_kernel[(triton.cdiv(s2.shape[0], 256),)](s2, idx, n_inv, s2.shape[0], s2.stride(0),
                                                             ROWS=256, num_warps=4)
    return score


def _select_chunk(q_tile, k_tile, geo: _Geometry, sparsity, cdf_threshold, sm_scale, has_padding, sol_beta=None):
    from .block_sparse_attention.bsa_interface import (
        get_select_indices_topk_from_score,
        get_select_indices_cdf_from_score,
        get_select_indices_cdf_topk_from_score,
    )
    N_b = geo.N_b
    heads = q_tile.shape[1]
    q_cmp, _ = dba._logical_block_means(q_tile, geo.tile_counts, geo.block_id_of_tile, N_b)
    k_cmp, _ = dba._logical_block_means(k_tile, geo.tile_counts, geo.block_id_of_tile, N_b)
    # FreeVideo: cuBLAS picks the batched score GEMM, and inductor the softmax
    # kernel, by the head count; scoring one head at a time keeps the selection
    # independent of the head chunk (and holds one head's score matrix at a time).
    per_head = deterministic_selection() and heads > 1
    sol_route = sol_beta is not None and sparsity is not None
    Sk = N_b if per_head else None
    if not per_head:
        score = _score(q_cmp, k_cmp, geo, has_padding)
        Sk = score.shape[-1]
        if sol_route:
            del q_cmp, k_cmp
            m, k_cap = _sol_mask(score, sparsity, sol_beta)
            return ("mask", m, k_cap)
    elif sol_route:
        mask, k_cap = None, None
        for h in range(heads):
            x = _score(q_cmp[:, h:h + 1].contiguous(), k_cmp[:, h:h + 1].contiguous(), geo, has_padding)
            m, k_cap = _sol_mask(x, sparsity, sol_beta)
            if mask is None:
                mask = m.new_empty((m.shape[0], heads) + tuple(m.shape[2:]))
            mask[:, h:h + 1] = m
            del x, m
        del q_cmp, k_cmp
        return ("mask", mask, k_cap)
    k_fast = None
    if fast_selection_enabled() and (per_head or score.is_contiguous()):
        if sparsity is not None and cdf_threshold is None:
            k_fast, use_w = int((1 - sparsity) * Sk), False
        elif sparsity is not None and cdf_threshold is not None:
            n_topk = max(1, int((1 - sparsity) * Sk))
            if n_topk >= int(cdf_threshold * Sk) + 1:
                # upper_bound == n_topk and num_selected is clamped to >= n_topk, so the
                # reference keeps exactly its top-n_topk weights: the cdf cannot bind.
                k_fast, use_w = min(Sk, n_topk), True
    if per_head:
        if k_fast is not None:
            mask = None
            for h in range(heads):
                x = _score(q_cmp[:, h:h + 1].contiguous(), k_cmp[:, h:h + 1].contiguous(), geo, has_padding)
                assert x.shape[-1] == Sk
                if use_w:
                    x = _softmax_weights(x, sm_scale)
                m = topk_mask(x, k_fast)
                if mask is None:
                    mask = m.new_empty((m.shape[0], heads) + tuple(m.shape[2:]))
                mask[:, h:h + 1] = m
                del x, m
            del q_cmp, k_cmp
            # separate mask + compaction kernels: the fused EMIT_TILES variant spills
            return ("mask", mask, k_fast)
        score = torch.cat([_score(q_cmp[:, h:h + 1].contiguous(), k_cmp[:, h:h + 1].contiguous(), geo, has_padding)
                           for h in range(heads)], dim=1)
    del q_cmp, k_cmp
    if k_fast is not None:
        x = _softmax_weights(score, sm_scale) if use_w else score
        del score
        # separate mask + compaction kernels: the fused EMIT_TILES variant spills
        return ("mask", topk_mask(x, k_fast), k_fast)
    if sparsity is not None and cdf_threshold is None:
        sel, _ = get_select_indices_topk_from_score(score, sparsity)
    elif sparsity is None and cdf_threshold is not None:
        sel, _ = get_select_indices_cdf_from_score(score, cdf_threshold, sm_scale)
    elif sparsity is not None and cdf_threshold is not None:
        sel, _ = get_select_indices_cdf_topk_from_score(score, sparsity, cdf_threshold, sm_scale)
    else:
        raise ValueError("Either sparsity or cdf_threshold must be provided")
    del score
    return ("list", sel.clamp(max=N_b), None)


SELECT_HEADS = 8  # the default head chunk


def deterministic_selection() -> bool:
    """FreeVideo default: block selection independent of the head chunk and of
    per-process kernel autotuning (PRISM_BSA_DETERMINISTIC=0: the research path)."""
    return os.environ.get("PRISM_BSA_DETERMINISTIC", "1") != "0"


def _pad_heads(x):
    h = x.shape[1]
    if h >= SELECT_HEADS:
        return x
    return torch.nn.functional.pad(x, (0, 0, 0, 0, 0, SELECT_HEADS - h))


def _sol_on(sol, pv) -> bool:
    """Sol correction for this call: Sage kernel only (the exact bf16 path is unchanged)."""
    if pv is None:
        return False
    from . import sage_bsa as sb
    return sb.sol_enabled(sol)


def _sol_beta(sol_beta):
    if sol_beta is not None:
        return float(sol_beta)
    env = os.environ.get("PRISM_SOL_BETA")
    return float(env) if env not in (None, "", "none", "off") else None


def fast_selection_enabled() -> bool:
    return os.environ.get("PRISM_BSA_FAST_SELECT", "1") != "0"


def _compact(sel, geo: _Geometry):
    """("list", sorted block ids [B,h,N_b,R]) or ("mask", int8 [B,h,N_b,N_b]) ->
    (tile lists [B,h,N_b,L] int32 ascending, lens [B,h,N_b] int32)."""
    if sel[0] == "tiles":
        return sel[1], sel[2]
    kind, sel, k_sel = sel
    if kind == "mask":
        B, h, NB, _ = sel.shape
        L = 2 * max(1, k_sel)
        lists = torch.empty((B, h, NB, L), dtype=torch.int32, device=sel.device)
        lens = torch.empty((B, h, NB), dtype=torch.int32, device=sel.device)
        m2 = sel.reshape(B * h * NB, NB)
        _compact_mask_kernel[(NB, B * h)](
            m2, geo.kbt0, geo.kbt1, geo.tile_valid_u8, lists, lens, m2.stride(0),
            h, NB, L, geo.N_tiles, CH=512, num_warps=4)
        return lists, lens
    B, h, NB, R = sel.shape
    L = 2 * R
    lists = torch.empty((B, h, NB, L), dtype=torch.int32, device=sel.device)
    lens = torch.empty((B, h, NB), dtype=torch.int32, device=sel.device)
    _compact_tiles_kernel[(NB, B * h)](
        sel, geo.kbt0, geo.kbt1, geo.tile_valid_u8, lists, lens,
        sel.stride(0), sel.stride(1), sel.stride(2),
        h, NB, R, L, geo.N_tiles, CH=256, num_warps=4)
    return lists, lens


# =====================================================================
# Core
# =====================================================================
def _core(q4, k4, v4, out4, geo: _Geometry, *, sparsity, cdf_threshold, sm_scale, pv, head_chunk,
          has_padding, sparsity_arg, sol=False, sol_beta=None):
    """q4/k4/v4/out4: [B, H, S_in, D] strided views (D contiguous); out4 written at rows src_u."""
    from .block_sparse_attention import bsa_interface as bsi
    B, H, _, D = q4.shape
    R = geo.src_u.shape[0]
    valid_re = geo.valid_re
    use_sage = pv is not None
    if use_sage:
        from . import sage_bsa as sb
        pv = sb.resolve_pv(pv, q4.device)
    for h0 in range(0, H, head_chunk):
        h1 = min(H, h0 + head_chunk)
        sl = slice(h0, h1)
        tiles = []
        keep = []
        for x4 in (q4, k4):
            xc = _gather(x4[:, sl], geo.src_u, R)
            if deterministic_selection():
                tiles.append(_tile_means(xc, valid_re))
            elif valid_re is not None:
                tiles.append(bsi.masked_mean_pooling_compression(xc, TILE, valid_re))
            else:
                tiles.append(bsi.mean_pooling_compression(xc, TILE))
            if not use_sage:
                keep.append(xc)
            del xc
        sel = _select_chunk(tiles[0], tiles[1], geo, sparsity, cdf_threshold, sm_scale, has_padding,
                            sol_beta=sol_beta)
        # K smoothing vector from the selection's tile means (mean over valid keys; any
        # per-head constant is exact for softmax) -> the quant pre-pass skips its K read.
        kt = _pad_heads(tiles[1]) if deterministic_selection() else tiles[1]
        k_mean = ((kt * geo.tile_counts.view(1, 1, -1, 1)).sum(dim=2) / geo.tile_counts.sum().clamp(min=1))[:, :h1 - h0]
        del kt
        del tiles
        lists, lens = _compact(sel, geo)
        del sel
        if use_sage:
            _attend_sage(q4[:, sl], k4[:, sl], v4[:, sl], out4[:, sl], lists, lens, geo, sm_scale, pv,
                         k_mean=k_mean, sol=sol)
            STATS["sage_calls"] += 1
            STATS["sol_calls"] += int(bool(sol))
        else:
            q_c, k_c = keep
            v_c = _gather(v4[:, sl], geo.src_u, R)
            rows = lists.index_select(2, geo.block_id_of_tile)
            lens_t = lens.index_select(2, geo.block_id_of_tile) * geo.tile_valid.view(1, 1, -1).to(torch.int32)
            orig = _original_kernel()
            o_c, _ = orig(q_c, k_c, v_c, sm_scale, rows, lens_t, TILE, TILE, sparsity_arg, kv_valid_mask=valid_re)
            del q_c, k_c, v_c, rows, lens_t, keep
            _scatter(o_c, geo.src_u, out4[:, sl])
            del o_c
        del lists, lens
    STATS["calls"] += 1


def _original_kernel():
    try:
        from . import sage_bsa as sb
        if sb.ORIGINAL_attn_fwd_bsa_varlen_triton is not None:
            return sb.ORIGINAL_attn_fwd_bsa_varlen_triton
    except Exception:  # noqa: BLE001
        pass
    from .block_sparse_attention import bsa_interface as bsi
    return bsi.attn_fwd_bsa_varlen_triton


def _attend_sage(q4, k4, v4, out4, lists, lens, geo: _Geometry, sm_scale, pv, merge=True, k_mean=None, sol=False):
    st = _prep_sage(q4, k4, v4, out4, lists, lens, geo, sm_scale, pv, k_mean=k_mean, sol=sol)
    _launch_sage(st)


def _prep_sage(q4, k4, v4, out4, lists, lens, geo: _Geometry, sm_scale, pv, k_mean=None, sol=False):
    """Quantize a head chunk (gather from the THW projections) and build the launch args.
    sol: also build the Sol tile summaries + list bitmask (approximate unselected tiles)."""
    from . import sage_bsa as sb
    B, H, _, D = q4.shape
    R = geo.src_u.shape[0]
    st = sb._PATCH_STATE
    qz = sb.quantize_qkv(
        q4, k4, v4, sm_scale, geo.valid_re, pv, TILE, TILE,
        st.get("q_per_token", True), st.get("k_per_token", False), True, want_qkm=False, src_map=geo.src_u,
        k_mean=k_mean, want_kc=bool(sol))
    q8, qs, k8, ks, vq, vs, kvm, has_mask, qkm = qz[:9]
    sol_t = qz[9] if sol else None
    if pv == "fp8":
        s_vn, s_vd = 1, vq.stride(2)
    else:
        s_vn, s_vd = vq.stride(2), 1
    dummy = torch.empty(1, dtype=torch.float32, device=q4.device)
    n_qt = R // TILE
    n_pairs = n_qt // 2
    tail = (
        geo.src_u,
        sb._colbias(kvm, has_mask, R, q4.device), sb._cinit(kvm, has_mask, R, q4.device),
        out4.stride(0), out4.stride(1), out4.stride(2),
        vq.stride(0), vq.stride(1), s_vn, s_vd,
        lists.stride(0), lists.stride(1), lists.stride(2), lists.stride(3),
        lens.stride(0), lens.stride(1), lens.stride(2),
        H, R, R, n_pairs,
    )
    head = (q8, k8, vq, qs, ks, vs, out4, dummy, dummy, lists, lens, geo.pair_merged, kvm, geo.bid_i32)
    kw = dict(HEAD_DIM=D, TQ=TILE, BLOCK_N=TILE, PV_MODE=sb._PV_CODE[pv],
              Q_PER_TOKEN=st.get("q_per_token", True), K_PER_TOKEN=st.get("k_per_token", False),
              HAS_KV_MASK=has_mask, STORE_LSE=False, LSE_SHIFT=False,
              ROWMAP=True, SHARED_FLAGS=True, SCATTER=True)
    kw.update(sb._sol_args(sol_t, lists, lens, H, n_qt, q4.device))      # {} without Sol
    return dict(head=head, tail=tail, kw=kw, geo=geo, pv=pv, B=B, H=H, R=R, n_qt=n_qt, n_pairs=n_pairs,
                device=q4.device, k_per_token=st.get("k_per_token", False), keep=(qkm, sol_t))


def _launch_sage(st, plan=None):
    """Attention kernel launch(es) for a prepared chunk; plan from sage_bsa.launch_plan."""
    from . import sage_bsa as sb
    if plan is None:
        plan = sb.launch_plan(st["device"], st["pv"], st["R"], st["k_per_token"])
    geo = st["geo"]
    merge = plan.get("merge", True) and st["n_pairs"] > 0
    kw = dict(st["kw"], **plan["opts"])
    BH = st["B"] * st["H"]
    if merge:
        common = st["head"] + (geo.tile_skip_merged,) + st["tail"]
        w, s = plan["cfg128"]
        sb._sage_bsa_attn_kernel[(st["n_pairs"], BH)](*common, BLOCK_M=128, MODE=1, num_warps=w, num_stages=s,
                                                      **kw)
        w, s = plan["cfg64"]
        sb._sage_bsa_attn_kernel[(st["n_qt"], BH)](*common, BLOCK_M=64, MODE=2, num_warps=w, num_stages=s, **kw)
    else:
        common = st["head"] + (geo.tile_skip_single,) + st["tail"]
        w, s = plan["cfg64"]
        sb._sage_bsa_attn_kernel[(st["n_qt"], BH)](*common, BLOCK_M=64, MODE=0, num_warps=w, num_stages=s, **kw)


def _head_mean_padded(v_hm_valid, T, H, W, grid_padded, D, dtype, device):
    Tp, Hp, Wp = grid_padded
    if (Tp, Hp, Wp) == (T, H, W):
        return v_hm_valid.view(T, H, W, D)
    hm = torch.zeros((Tp, Hp, Wp, D), dtype=dtype, device=device)
    hm[:T, :H, :W] = v_hm_valid.view(T, H, W, D)
    return hm


def _src_maps(shape_ids, grid_valid, grid_padded, device, padded_input: bool, valid_mask=None):
    """src_idx (tile-layout row -> padded THW index) and src_u (-> input token or -1)."""
    T, H, W = grid_valid
    Tp, Hp, Wp = grid_padded
    ZT, ZH, ZW = Tp // ZONE_SIZE, Hp // ZONE_SIZE, Wp // ZONE_SIZE
    src_idx, _ = dba._build_rearrange_index(shape_ids, ZT, ZH, ZW, Hp, Wp, device)
    if padded_input:
        if valid_mask is None:
            return src_idx.to(torch.int32).contiguous(), None
        valid_re = valid_mask[src_idx].contiguous()
        src_u = torch.where(valid_re, src_idx, -1).to(torch.int32).contiguous()
        return src_u, valid_re
    pt = src_idx // (Hp * Wp)
    ph = (src_idx // Wp) % Hp
    pw = src_idx % Wp
    ok = (pt < T) & (ph < H) & (pw < W)
    src_u = torch.where(ok, (pt * H + ph) * W + pw, -1).to(torch.int32).contiguous()
    valid_re = ok.contiguous() if (Tp, Hp, Wp) != (T, H, W) else None
    return src_u, valid_re


def fused_dynamic_bsa(
    q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, grid_size, num_heads: int, *,
    method: str, sparsity, cdf_threshold=None, audio_token_norms=None,
    lambda_a: float = 0.5, tau_128: float = 0.15, lambda_128: float = 1.0,
    pv: Optional[str] = "env", head_chunk: Optional[int] = None, shape_ids=None, return_shape_ids=False,
    out: Optional[torch.Tensor] = None, sol: Optional[bool] = None, sol_beta: Optional[float] = None,
):
    """IVPQ/penalty dynamic BSA from the THW projections.

    sol: Sol-style correction for the unselected k tiles (Sage kernel only);
    None reads PRISM_SOL (default off). sol_beta: Sol threshold routing (keep blocks
    above mu + beta*sigma of the row, at most the sparsity budget); None reads
    PRISM_SOL_BETA (default: plain top-k). (Prism-fast 98b7cc8)

    out: optional [B, S, num_heads*D] destination; may be q itself (each head chunk
    of q is fully consumed - gathered for scoring and gathered/quantized for the
    attention - before that chunk's output columns are stored), saving one
    projection-sized buffer (as in FreeVideo's vendored copy).

    q/k/v: [B, S, num_heads*D] (S = T*H*W, unpadded THW order, D contiguous).
    Returns [B, S, num_heads*D] (== rearrange(_run_bsa_dynamic(...), 'b n s d -> b s (n d)')).
    """
    B, S, HD = q.shape
    Hn = num_heads
    D = HD // Hn
    T, H, W = grid_size
    assert T * H * W == S
    device = q.device
    Tp, Hp, Wp = (-(-x // ZONE_SIZE) * ZONE_SIZE for x in (T, H, W))
    padded = (Tp, Hp, Wp) != (T, H, W)
    sm_scale = 1.0 / (D ** 0.5)
    if pv == "env":
        pv = sage_mode()
    head_chunk = head_chunk or head_chunk_default()
    q4 = q.view(B, S, Hn, D).permute(0, 2, 1, 3)
    k4 = k.view(B, S, Hn, D).permute(0, 2, 1, 3)
    v4 = v.view(B, S, Hn, D).permute(0, 2, 1, 3)
    with torch.no_grad():
        valid_mask = None
        if padded:
            it, ih, iw = (torch.arange(x, device=device) for x in (Tp, Hp, Wp))
            valid_mask = ((it[:, None, None] < T) & (ih[None, :, None] < H)
                          & (iw[None, None, :] < W)).reshape(-1).contiguous()
        if shape_ids is None:
            # == padded_v[0].mean(dim=0) of the reference (verified bit-identical)
            hm = _head_mean_padded(v.view(B, S, Hn, D)[0].mean(dim=1), T, H, W, (Tp, Hp, Wp), D, v.dtype, device)
            an = audio_token_norms
            if an is not None and padded:
                an = torch.nn.functional.pad(an.reshape(T, H, W), (0, Wp - W, 0, Hp - H, 0, Tp - T),
                                             value=0.0).reshape(-1).contiguous()
            shape_ids = select_zone_shapes(hm, an, (Tp, Hp, Wp), method, lambda_a=lambda_a, tau_128=tau_128,
                                           lambda_128=lambda_128, valid_mask_thw=valid_mask, sp_reduce=True)
            del hm
        src_u, valid_re = _src_maps(shape_ids, (T, H, W), (Tp, Hp, Wp), device, padded_input=False)
        geo = _Geometry(shape_ids, (Tp, Hp, Wp), valid_re, src_u, device)
        if out is None:
            out = torch.empty_like(q)
        assert out.shape == q.shape and out.is_contiguous()
        out4 = out.view(B, S, Hn, D).permute(0, 2, 1, 3)
        _core(q4, k4, v4, out4, geo, sparsity=sparsity, cdf_threshold=cdf_threshold, sm_scale=sm_scale,
              pv=pv, head_chunk=head_chunk, has_padding=padded, sparsity_arg=sparsity,
              sol=_sol_on(sol, pv), sol_beta=_sol_beta(sol_beta))
    if return_shape_ids:
        return out, shape_ids
    return out


def flash_attn_bsa_3d_dynamic_fused(
    q, k, v, grid_padded, *, method, sparsity=0.9375, cdf_threshold=None, audio_token_norms=None,
    valid_mask=None, lambda_a=0.5, tau_128=0.15, lambda_128=1.0, shape_ids=None, return_shape_ids=False,
    pv: Optional[str] = "env", head_chunk: Optional[int] = None,
):
    """Drop-in for flash_attn_bsa_3d_dynamic (q/k/v [B,H,S_pad,D], padded THW).

    Padded output positions are zero (the reference leaves computed values there;
    callers crop them)."""
    B, Hn, S, D = q.shape
    T, H, W = grid_padded
    assert T * H * W == S
    device = q.device
    sm_scale = 1.0 / (D ** 0.5)
    if pv == "env":
        pv = sage_mode()
    head_chunk = head_chunk or head_chunk_default()
    with torch.no_grad():
        if shape_ids is None:
            v_headavg = v[0].mean(dim=0).view(T, H, W, D)
            shape_ids = select_zone_shapes(v_headavg, audio_token_norms, grid_padded, method,
                                           lambda_a=lambda_a, tau_128=tau_128, lambda_128=lambda_128,
                                           valid_mask_thw=valid_mask, sp_reduce=True)
            del v_headavg
        src_u, valid_re = _src_maps(shape_ids, grid_padded, grid_padded, device, padded_input=True,
                                    valid_mask=valid_mask)
        geo = _Geometry(shape_ids, grid_padded, valid_re, src_u, device)
        out = torch.zeros_like(q) if valid_mask is not None else torch.empty_like(q)
        _core(q, k, v, out, geo, sparsity=sparsity, cdf_threshold=cdf_threshold, sm_scale=sm_scale,
              pv=pv, head_chunk=head_chunk, has_padding=valid_mask is not None, sparsity_arg=sparsity)
    if return_shape_ids:
        return out, shape_ids
    return out


__all__ = ["fused_dynamic_bsa", "flash_attn_bsa_3d_dynamic_fused", "fused_enabled", "sage_mode", "STATS"]
