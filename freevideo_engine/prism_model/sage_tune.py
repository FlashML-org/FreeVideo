# Prism (MIT, Tencent): vendored from the Prism single-GPU research branch
# (Prism-fast 0befcb7, hymm/fast/sage_tune.py) for FreeVideo; see NOTICE.
"""Self-tuning of the Sage BSA attention kernel per GPU (and Triton version).

On a new GPU, run once (a few minutes; results are cached and picked up
automatically by hymm.fast.sage_bsa.launch_plan for every later run):

    python -m hymm.fast.sage_tune                       # 720p + 480p, auto PV mode
    python -m hymm.fast.sage_tune --classes 720p --budget 120 --pv fp8

What is measured: the real IVPQ attention call structure - synthetic but
spatially smooth q/k/v at the class's latent grid (205 frames), the real IVPQ
block selection (sparsity 0.75 + cdf 0.2), the fused path's gather-quantized
inputs and per-block lists - for a couple of heads (time per head is what
matters; the full model runs the same kernel per head chunk). Only the attention
kernel launches are timed.

Search space (coordinate descent within a time budget):
  kernel opts   KPAIR (2 k tiles per step), USE_TMA (SM90), FAST_CVT (magic int->
                float + integer row max), QK_PREFETCH (next tile's QK issued early)
  cfg64/cfg128  (num_warps, num_stages) of the BLOCK_M=64 and merged BLOCK_M=128
                programs (stages beyond the shared-memory limit fail to launch and
                are skipped)
  merge         run the two q tiles of 128-token blocks as one BLOCK_M=128 program
Each candidate's output is checked against the default config's (cos > 0.9999,
finite) so a miscompiled variant can never be selected.

Cache: PRISM_SAGE_TUNE_CACHE (default ~/.cache/prism_fast/sage_bsa_tune.json),
key "sage|<gpu name>|sm<cc>|t<triton>|<pv>|<shape class>". Set PRISM_SAGE_TUNE=1 to
tune automatically the first time a (gpu, pv, shape class) is used in a run.
"""
from __future__ import annotations

import argparse
import itertools
import math
import os
import statistics
import time
from typing import Dict, List, Optional

import torch
import torch.nn.functional as F

from . import sage_bsa as sb

# valid latent token grids (T, H, W) for 205 frames
CLASS_GRIDS = {"480p": (52, 30, 53), "720p": (52, 45, 80), "1080p": (52, 67, 120)}


def _smooth(heads, T, H, W, D, seed, device, scale=1.0, bias=0.0):
    g = torch.Generator(device=device)
    g.manual_seed(seed)
    x = torch.empty(1, T * H * W, heads * D, device=device, dtype=torch.bfloat16)
    x4 = x.view(1, -1, heads, D)
    b = torch.randn(D, device=device, generator=g) * bias
    for h in range(heads):
        c = torch.randn(1, D, max(2, T // 8), max(2, H // 8), max(2, W // 8), device=device, generator=g)
        f = F.interpolate(c, size=(T, H, W), mode="trilinear", align_corners=False).view(D, -1).T
        x4[0, :, h] = ((f + 0.5 * torch.randn(f.shape, device=device, generator=g)) * scale + b).to(torch.bfloat16)
    return x


def _timeit(fn, reps=3):
    fn()
    torch.cuda.synchronize()
    ts = []
    for _ in range(reps):
        a, b = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        a.record()
        fn()
        b.record()
        torch.cuda.synchronize()
        ts.append(a.elapsed_time(b))
    return statistics.median(ts)


def build_problem(cls: str, heads: int, pv: str, device):
    """Synthetic q/k/v + real IVPQ selection + fused-path quantized inputs for one head chunk."""
    from . import ivpq_fast as fx
    from .block_sparse_attention.dynamic_block_shape import ZONE_SIZE, select_zone_shapes
    from .block_sparse_attention import bsa_interface as bsi
    T, H, W = CLASS_GRIDS[cls]
    D = 128
    q = _smooth(heads, T, H, W, D, 1, device, scale=2.0)
    k = _smooth(heads, T, H, W, D, 2, device, scale=2.0, bias=1.0)
    v = _smooth(heads, T, H, W, D, 3, device)
    Tp, Hp, Wp = (-(-x // ZONE_SIZE) * ZONE_SIZE for x in (T, H, W))
    it, ih, iw = (torch.arange(x, device=device) for x in (Tp, Hp, Wp))
    valid = ((it[:, None, None] < T) & (ih[None, :, None] < H) & (iw[None, None, :] < W)).reshape(-1).contiguous()
    hm = fx._head_mean_padded(v.view(1, -1, heads, D)[0].mean(dim=1), T, H, W, (Tp, Hp, Wp), D, v.dtype, device)
    shape_ids = select_zone_shapes(hm, None, (Tp, Hp, Wp), "ivpq", lambda_a=0.5, tau_128=0.25, lambda_128=1.0,
                                   valid_mask_thw=valid, sp_reduce=True)
    src_u, valid_re = fx._src_maps(shape_ids, (T, H, W), (Tp, Hp, Wp), device, padded_input=False)
    geo = fx._Geometry(shape_ids, (Tp, Hp, Wp), valid_re, src_u, device)
    q4, k4, v4 = (x.view(1, -1, heads, D).permute(0, 2, 1, 3) for x in (q, k, v))
    R = src_u.shape[0]
    tiles = [bsi.masked_mean_pooling_compression(fx._gather(x4, src_u, R), fx.TILE, valid_re) for x4 in (q4, k4)]
    sm = D ** -0.5
    sel = fx._select_chunk(tiles[0], tiles[1], geo, 0.75, 0.2, sm, True)
    lists, lens = fx._compact(sel, geo)
    k_mean = (tiles[1] * geo.tile_counts.view(1, 1, -1, 1)).sum(dim=2) / geo.tile_counts.sum().clamp(min=1)
    out = torch.empty_like(q)
    out4 = out.view(1, -1, heads, D).permute(0, 2, 1, 3)
    st = fx._prep_sage(q4, k4, v4, out4, lists, lens, geo, sm, pv, k_mean=k_mean)
    # selected (q tile, k tile) pairs actually computed: list row of each valid q tile
    per_tile = lens.index_select(2, geo.block_id_of_tile) * geo.tile_valid.view(1, 1, -1)
    work = float(per_tile.sum().item()) * 4.0 * 64 * 64 * D
    return dict(st=st, out=out, R=R, work=work, cls=cls, heads=heads, pv=pv, device=device,
                keep=(q, k, v, lists, lens, geo))


def candidate_opts(cap, pv) -> List[Dict[str, int]]:
    if cap == (9, 0):
        base = [{}, {"KPAIR": 1}, {"KPAIR": 1, "USE_TMA": 1}, {"FAST_CVT": 1}]
    else:
        base = [{}, {"FAST_CVT": 1}, {"KPAIR": 1}, {"QK_PREFETCH": 1}]
    if pv == "fp16acc":
        base = [o for o in base if "KPAIR" not in o and "USE_TMA" not in o]
    return base


def tune_class(cls: str, pv: str = "auto", device=None, heads: int = 2, budget_s: float = 180.0,
               reps: int = 3, verbose: bool = True, save: bool = True) -> dict:
    device = torch.device(device or "cuda")
    pv = sb.resolve_pv(pv, device)
    cap = sb.device_arch(device)
    from . import ivpq_fast as fx
    t_start = time.time()
    with torch.no_grad():
        prob = build_problem(cls, heads, pv, device)
    st = prob["st"]
    work = prob["work"]
    log = (lambda *a: print("[sage_tune]", *a, flush=True)) if verbose else (lambda *a: None)
    log(f"{torch.cuda.get_device_name(device)} SM{cap[0]}{cap[1]} triton {sb.triton.__version__} class={cls} "
        f"pv={pv} heads={heads} R={prob['R']} work={work / 1e12:.2f} TFLOP (setup {time.time() - t_start:.1f}s)")
    results = []

    def run(plan):
        sb._PLAN_OVERRIDE = plan
        try:
            fx._launch_sage(st, plan=sb.launch_plan(device, pv, prob["R"], st["k_per_token"]))
        finally:
            sb._PLAN_OVERRIDE = None

    def measure(plan, ref=None):
        if time.time() - t_start > budget_s:
            return None
        try:
            t = _timeit(lambda: run(plan), reps)
            o = prob["out"].float()
            if not torch.isfinite(o).all():
                raise RuntimeError("non-finite output")
            if ref is not None:
                cos = F.cosine_similarity(o.flatten(), ref.flatten(), dim=0).item()
                if cos < 0.9999:
                    raise RuntimeError(f"output mismatch cos={cos:.6f}")
        except Exception as e:  # noqa: BLE001 - compile / resource / mismatch -> skip
            log(f"  skip {plan_str(plan)}: {type(e).__name__}: {str(e).splitlines()[0][:120]}")
            return float("inf")
        results.append((t, plan))
        log(f"  {t:8.2f} ms {work / t / 1e9:6.0f} TFLOP/s  {plan_str(plan)}")
        return t

    default = sb.default_plan(device, pv)
    t_def = measure(default)
    ref = prob["out"].float().clone()
    best_t, best = t_def, default
    # 1) opts x cfg64 (merge on, cfg128 default)
    for opts in candidate_opts(cap, pv):
        for w64, s64 in itertools.product((4, 8), (2, 3, 4)):
            plan = {"opts": dict(opts), "cfg64": [w64, s64], "cfg128": list(default["cfg128"]), "merge": True}
            if plan_str(plan) == plan_str(default):
                continue
            t = measure(plan, ref)
            if t is None:
                break
            if t < best_t:
                best_t, best = t, plan
    # 2) cfg128 for the best opts/cfg64
    for w128, s128 in itertools.product((4, 8), (2, 3, 4)):
        plan = dict(best, cfg128=[w128, s128])
        if plan_str(plan) == plan_str(best):
            continue
        t = measure(plan, ref)
        if t is None:
            break
        if t < best_t:
            best_t, best = t, plan
    # 3) merge off
    t = measure(dict(best, merge=False), ref)
    if t is not None and t < best_t:
        best_t, best = t, dict(best, merge=False)
    entry = dict(opts=best["opts"], cfg64=best["cfg64"], cfg128=best["cfg128"], merge=best["merge"],
                 ms=round(best_t, 3), default_ms=round(t_def, 3), tflops=round(work / best_t / 1e9, 1),
                 default_tflops=round(work / t_def / 1e9, 1), heads=heads, evaluated=len(results),
                 seconds=round(time.time() - t_start, 1))
    key = sb.tune_key(device, pv, cls)
    log(f"best {plan_str(best)}: {best_t:.2f} ms ({entry['tflops']} TFLOP/s) vs default {t_def:.2f} ms "
        f"({entry['default_tflops']}) after {entry['evaluated']} configs, {entry['seconds']} s -> {key}")
    if save:
        sb.save_tuned(key, entry)
    del prob
    torch.cuda.empty_cache()
    return entry


def plan_str(p):
    o = ",".join(f"{k}={v}" for k, v in sorted(p["opts"].items())) or "-"
    return f"opts[{o}] cfg64={tuple(p['cfg64'])} cfg128={tuple(p['cfg128'])} merge={int(p['merge'])}"


def ensure_tuned(device, pv: str, n_rows: int, budget_s: float = 180.0) -> None:
    """PRISM_SAGE_TUNE=1: tune the class of n_rows once per process if not cached."""
    cls = sb.shape_class(n_rows)
    key = sb.tune_key(device, pv, cls)
    if key in sb._tune_disk() or key in _TRIED or cls not in CLASS_GRIDS:
        return
    _TRIED.add(key)
    tune_class(cls, pv, device, budget_s=budget_s)


_TRIED = set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classes", default="720p,480p")
    ap.add_argument("--pv", default="auto", help="auto | fp8 | fp16 | fp16acc (comma list allowed)")
    ap.add_argument("--heads", type=int, default=2)
    ap.add_argument("--budget", type=float, default=180.0, help="seconds per (class, pv)")
    ap.add_argument("--reps", type=int, default=3)
    args = ap.parse_args()
    for pv in args.pv.split(","):
        for cls in args.classes.split(","):
            tune_class(cls, pv, heads=args.heads, budget_s=args.budget, reps=args.reps)
    print(f"[sage_tune] cache: {sb.TUNE_PATH}")


if __name__ == "__main__":
    main()
