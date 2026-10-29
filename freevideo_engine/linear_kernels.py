"""Compile the linear branch's fused kernels once for every canvas, clip length and head split.

Besides the row-wise kernels of row_kernels, upstream fuses three steps of the
linear branch with dynamic=False: the output norm and gate, the SiLU + L2Norm of
the query features, and the operands of the per-frame statistics. Their shapes
are [frames, heads, tokens per frame, head_dim], so dynamo keeps one graph per
(frame count, canvas, heads of a head chunk). Every clip length or canvas the
install has not compiled yet is a full compile of each in the first step of both
passes, and so is each head chunk the planner picks for a pass.

On an RTX PRO 6000, a 1344x768 5.2 s two-pass request on an empty kernel cache, twice each way:
the graphs missing in the second pass fell from 5 to 2, and the first steps'
excess over steady steps from 27-34 s and 9-11 s to 23-24 s and 6 s. With the
default 10 s request compiled ahead, the excess fell from 22 s and 8 s to
10 s and 3 s. A cold compile of the three symbolic graphs costs about 1.5 s
each, against 0.7 s for the per-canvas ones, so a single-pass request on an
empty cache starts about 4 s later; the next canvas or length then costs nothing.

Only the frame, head and token dimensions are marked dynamic, on an alias of each
argument, so the caller's tensors carry no mark into any other compiled
function. The head dimension's size, the dtypes and the head_dim stay specialised
as upstream compiles them, and each body does the same arithmetic on the same
values. Complete requests produced bitwise the same latents as before, in
two passes with int8 and in one pass with native FP8, and the same steady step
times. The gather of the scan states stays as upstream compiles it: its
cumulative sum is scheduled differently once the frame count is dynamic, which
moves values by a bf16 step.
"""
import torch

_DYNAMIC = '_freevideo_dynamic_frames'


def _alias(tensor, *dims):
    """`tensor` seen through a new view with the given dimensions dynamic."""
    alias = tensor.view(tensor.shape)
    for dim in dims:
        torch._dynamo.maybe_mark_dynamic(alias, dim)
    return alias


def _tagged(call):
    setattr(call, _DYNAMIC, True)
    return call


def _epilogue_body(readout, weight, gate, eps):
    """upstream `_linear_epilogue_fhsd_body` with the gate as [frames, tokens, heads, head_dim]."""
    ms = torch.linalg.vector_norm(
        readout, dim=-1, keepdim=True, dtype=torch.float32).pow(2) / readout.shape[-1]
    normed = readout * torch.rsqrt(ms + eps).to(readout.dtype) * weight.to(readout.dtype)
    return normed.permute(0, 2, 1, 3) * gate


def _epilogue():
    compiled = torch.compile(_epilogue_body, dynamic=False)

    def call(readout, weight, gate, eps):
        frames, heads, per_frame, dim = readout.shape
        gate = gate.reshape(frames, per_frame, heads, dim)
        out = compiled(_alias(readout, 0, 1, 2), weight, _alias(gate, 0, 1, 2), eps)
        return out.reshape(frames * per_frame, heads * dim)
    return _tagged(call)


def _activate_body(tokens, l2norm):
    """upstream `_activate_fhsd_body` on tokens already viewed as [frames, tokens, heads, head_dim]."""
    x = torch.nn.functional.silu(tokens)
    if l2norm:
        x = torch.nn.functional.normalize(x, dim=-1, eps=1e-6).to(x.dtype)
    return x.permute(0, 2, 1, 3).contiguous()


def _activate():
    compiled = torch.compile(_activate_body, dynamic=False)

    def call(tokens, l2norm, num_frames, per_frame):
        tokens = tokens.reshape(num_frames, per_frame, tokens.shape[-2], tokens.shape[-1])
        return compiled(_alias(tokens, 0, 1, 2), l2norm)
    return _tagged(call)


def _statistics(body):
    compiled = torch.compile(body, dynamic=False)

    def call(kf, vf, beta):
        return compiled(_alias(kf, 0, 1, 2), _alias(vf, 0, 1, 2), _alias(beta, 0, 1, 2))
    return _tagged(call)


def install():
    """Put dynamic-frame compiles of upstream's linear-branch kernels in upstream's caches.

    Upstream looks each kernel up by name and compiles it on first use, so an
    entry placed here before the first call is the one every block uses.
    Idempotent, like row_kernels.install.
    """
    from src.models.linear_attention import features, kernels, scan
    if not getattr(kernels._LINEAR_EPILOGUE_CACHE.get('fhsd'), _DYNAMIC, False):
        kernels._LINEAR_EPILOGUE_CACHE['fhsd'] = _epilogue()
    for l2norm in (True, False):
        key = f'act_fhsd_{l2norm}'
        if not getattr(features._FEATURES_CACHE.get(key), _DYNAMIC, False):
            features._FEATURES_CACHE[key] = _activate()
    if not getattr(scan._STATS_PREP_CACHE.get('fn'), _DYNAMIC, False):
        scan._STATS_PREP_CACHE['fn'] = _statistics(scan._frame_stats_prep_body)
