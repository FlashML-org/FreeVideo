"""Factorise only matrices that really are I + A with A positive semidefinite.

At inference, upstream computes each frame's A = (k * beta)^T k with a TF32 GEMM
(`scan._tf32_matmul`). The rounding error of that product grows with |A|. A
1920x1088 request of 583 frames produced keys of norm up to 80 over 2040 tokens
per frame, and |A| reached 1.9e6. The batched TF32 product then pushed the
smallest eigenvalue of I + A from 1.005 down to -3.5 on 4 of the 9520
frame-head matrices of one block. torch.linalg.cholesky then stopped the request
on step 1. The same batch computed in FP32 factorised all 9520.

Every matrix that factorises keeps upstream's factor, bit for bit, and so does
every request that never hits this case. A matrix that fails gets A recomputed
from its own keys and gates in FP64. Its factor is computed there and handed
back in FP32, and the scan continues as before. Upstream's cholesky already
reads its error flags on the host, so the check adds no synchronisation.
"""
import logging

import torch

_log = logging.getLogger(__name__)
REPAIRED = [0]
_SOURCE = '_freevideo_delta_source'
_TAG = '_freevideo_delta_guard'


def exact_statistics(kf, beta):
    """A = (k * beta)^T k in FP64 for [N, S, d] keys and [N, S] gates."""
    k64 = kf.to(torch.float64)
    A = torch.matmul((k64 * beta.to(torch.float64).unsqueeze(-1)).transpose(-1, -2), k64)
    return 0.5 * (A + A.transpose(-1, -2))


def factor(A32, source=None):
    """Lower Cholesky factor of I + A32 over a [..., d, d] batch.

    `source` is (kf [F, H, S, d], beta [F, H, S]) for an [F, H, d, d] A32. When it
    is given, a matrix that does not factorise is rebuilt from it; without it the
    error is upstream's, unchanged.
    """
    eye = torch.eye(A32.shape[-1], device=A32.device, dtype=torch.float32).expand_as(A32)
    chol, info = torch.linalg.cholesky_ex(A32 + eye)
    if not bool((info != 0).any()):
        return chol
    if source is None or A32.dim() != 4:
        return torch.linalg.cholesky(A32 + eye)
    kf, beta = source
    flat_info = info.reshape(-1)
    bad = torch.nonzero(flat_info).reshape(-1)
    heads = A32.shape[1]
    frames, rows = bad // heads, bad % heads
    A64 = exact_statistics(kf[frames, rows], beta[frames, rows])
    eye64 = torch.eye(A64.shape[-1], device=A64.device, dtype=torch.float64)
    repaired = torch.linalg.cholesky(A64 + eye64).to(torch.float32)
    flat = chol.reshape(-1, *chol.shape[-2:])
    flat[bad] = repaired
    REPAIRED[0] += int(bad.numel())
    _log.info('delta factor rebuilt %d of %d matrices in FP64', int(bad.numel()), flat.shape[0])
    return chol


def factor_apply(backend, alpha, A_raw, B_raw):
    """Only CUDA computes A with TF32; every other device keeps upstream's call."""
    if A_raw.device.type != 'cuda':
        return _upstream()(backend, alpha, A_raw, B_raw)
    return guarded_apply(backend, alpha, A_raw, B_raw)


def guarded_apply(backend, alpha, A_raw, B_raw):
    """upstream VdnDelta.factor_apply, with `factor` in place of torch.linalg.cholesky."""
    A32 = A_raw.float()
    eye = torch.eye(A32.shape[-1], device=A32.device, dtype=torch.float32).expand_as(A32)
    chol = factor(A32, getattr(A_raw, _SOURCE, None))
    linv = torch.linalg.solve_triangular(chol, eye, upper=False, left=True)
    inv = linv.transpose(-1, -2) @ linv
    transition = alpha.unsqueeze(-1) * inv
    injection = B_raw.float() @ inv
    return transition.to(A_raw.dtype), injection.to(B_raw.dtype)


def _upstream():
    from src.models.linear_attention.delta_rule import VdnDelta
    return getattr(VdnDelta, '_freevideo_upstream_factor_apply', VdnDelta.factor_apply)


def _statistics(original):
    def call(kf, vf, beta, a_fp32, inference=False):
        A, B = original(kf, vf, beta, a_fp32, inference=inference)
        if inference:
            # Views of tensors the caller holds until the scan has run: no extra memory.
            setattr(A, _SOURCE, (kf, beta))
        return A, B
    setattr(call, _TAG, True)
    return call


def install():
    """Wrap upstream's statistics and unscaled delta factor once. Idempotent."""
    from src.models.linear_attention import scan
    from src.models.linear_attention.delta_rule import VdnDelta
    if not getattr(scan._frame_statistics, _TAG, False):
        scan._frame_statistics = _statistics(scan._frame_statistics)
    if not getattr(VdnDelta.factor_apply, _TAG, False):
        setattr(factor_apply, _TAG, True)
        VdnDelta._freevideo_upstream_factor_apply = VdnDelta.factor_apply
        VdnDelta.factor_apply = factor_apply
