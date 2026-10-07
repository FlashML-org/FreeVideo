"""Student -> teacher calibration of the v2a K/V (Prism-fast hymm/fast/kv_calib.py,
2026-10-06: the loader, the map application and the time-only RoPE inverse; the
statistics collection and the fitting stay in the research repository).

The audio teacher re-ran the 30 fused video blocks with the base (undistilled)
weights every step only to get the video keys/values the audio tower attends to
through each fused block's v2a cross-attention. Here an affine map per fused layer
and denoising step, fitted by ridge regression, moves the distilled student's own
K/V toward the base model's, so the base pass is dropped (Light level):

  per step i (video sigma s_i, expert e: 1 high-noise, 2 low-noise):
  1. layers = maps_for(maps, s_i, e): the maps of the fitted step nearest to s_i.
  2. In the student's conditional joint pass (not the unconditional CFG pass), fused
     block j hands its post-RoPE (K, V) over (sampling.StudentTap) after the
     student's own audio stream has used them; they are stored as
       K' = RoPE_v(f_kj(RoPE_v^-1(K))),  V' = f_vj(V)
       f(x) = x + x @ D + b (full) | x + (x @ U) @ V + b (lowrank) | x * (1 + s) + b (diag)
     in the FP8 V2AKVCache, as the teacher's were.
  3. The audio latents take the audio-only sub-steps with audio CFG against that
     cache instead of the joint audio update (sampling.audio_substeps).

File format prism-kv-calib/1 (safetensors): tensors "b{bucket}.l{layer}.{k|v}.{name}"
with name D [d, d] fp16, U [d, r] / V [r, d] fp16, s [d] fp32, A [d, d] fp16 and b [d]
fp32; metadata "format" = "prism-kv-calib/1", "steps" = JSON list of {step, sigma,
expert, bucket}, "meta" = JSON. The file is found by this format, not by its name
(prism_layout.find_kv_maps). The maps stay in host memory (pageable: one step's bucket, ~283 MB for
the full form, is uploaded before that step); the planner charges them to RAM.
"""
import json

import torch

from ..prism_layout import KV_MAPS_FORMAT as FORMAT, find_kv_maps, kv_maps_bytes  # noqa: F401


def load_maps(path):
    """maps file -> {"steps": [{step, sigma, expert, layers}], "meta"}: host tensors
    (matrices fp16, vectors fp32), one layer list per bucket shared by its steps."""
    from safetensors import safe_open
    with safe_open(str(path), 'pt') as handle:
        metadata = handle.metadata() or {}
        if metadata.get('format') != FORMAT:
            raise ValueError('Not a %s file: %s' % (FORMAT, path))
        buckets = {}
        for name in handle.keys():
            bucket, layer, kv, field = name.split('.')
            tensor = handle.get_tensor(name)
            tensor = tensor.to(torch.float16 if tensor.dim() == 2 else torch.float32).contiguous()
            buckets.setdefault(int(bucket[1:]), {}).setdefault(int(layer[1:]), {}).setdefault(kv, {})[field] = tensor
    layers = {b: [rows[i] for i in range(len(rows))] for b, rows in buckets.items()}
    steps = [dict(step=int(row['step']), sigma=float(row['sigma']), expert=int(row['expert']),
                  bucket=int(row['bucket']), layers=layers[int(row['bucket'])])
             for row in json.loads(metadata['steps'])]
    meta = json.loads(metadata.get('meta', '{}'))
    if meta.get('kind', 'kv') != 'kv':
        raise ValueError('Only K/V maps are supported (kind %r)' % meta.get('kind'))
    return dict(steps=steps, meta=meta, path=str(path))


def maps_for(maps, sigma, expert):
    """The fitted step nearest to ``sigma`` (same expert preferred): (bucket, layers)."""
    candidates = [s for s in maps['steps'] if s['expert'] == expert] or maps['steps']
    step = min(candidates, key=lambda s: abs(s['sigma'] - float(sigma)))
    return step['bucket'], step['layers']


def layers_to(layers, device):
    """One bucket's per-layer maps -> ``device``."""
    return [{kv: {n: t.to(device, non_blocking=False) for n, t in d.items()} for kv, d in layer.items()}
            for layer in layers]


def _rot(t, cos, sin, head_dim, inverse=False):
    """t [..., L, H*D]; cos/sin [..., L, D] (duplicated halves). ``inverse`` undoes the
    rotation exactly, also for bf16 tables (cos^2 + sin^2 is not exactly 1). fp32."""
    shape = t.shape
    x = t.float().reshape(*shape[:-1], shape[-1] // head_dim, head_dim)
    c = cos.float().unsqueeze(-2)
    s = sin.float().unsqueeze(-2)
    h = head_dim // 2
    rh = torch.cat((-x[..., h:], x[..., :h]), -1)
    out = (x * c - rh * s) / (c * c + s * s) if inverse else x * c + rh * s
    return out.reshape(shape)


def _apply_affine(x, m):
    """x [L, d] fp32 -> the map's estimate, fp32."""
    D, U, s, A, b = m.get('D'), m.get('U'), m.get('s'), m.get('A'), m['b']
    if D is not None:
        return x + (x.to(D.dtype) @ D).float() + b
    if U is not None:
        return x + ((x.to(U.dtype) @ U) @ m['V']).float() + b
    if s is not None:
        return x * (1.0 + s) + b
    return (x.to(A.dtype) @ A).float() + b


@torch.no_grad()
def apply_(k, v, layer, vrope, head_dim=128, chunk=32768):
    """Map one fused layer's post-RoPE K and V [1, L, d] in place (research
    CalibKVCache.put: K unrotated, mapped and rotated again; V mapped). The research
    row chunk (32768) keeps the GEMM shapes, and with them the results, the same."""
    cos, sin = vrope
    rows = k.shape[1]
    for r0 in range(0, rows, chunk):
        r1 = min(rows, r0 + chunk)
        if layer.get('k') is not None:
            c, s = cos[0, r0:r1], sin[0, r0:r1]
            kp = _apply_affine(_rot(k[0, r0:r1], c, s, head_dim, inverse=True), layer['k'])
            k[0, r0:r1] = _rot(kp, c, s, head_dim).to(k.dtype)
            del kp
        if layer.get('v') is not None:
            v[0, r0:r1] = _apply_affine(v[0, r0:r1].float(), layer['v']).to(v.dtype)
