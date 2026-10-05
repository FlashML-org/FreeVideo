"""VDN delta-rule factors with GPU matrix products instead of batched Cholesky.

The released checkpoints use (I + A)^-1 for every frame and head. MPS runs the
batched Cholesky and triangular solve far below its matrix-product rate: on an
M5 those two calls cost 0.39 s of a 1.1 s linear branch. I + A is symmetric
positive definite with eigenvalues in [1, 1 + trace(A)], so a Newton-Schulz
iteration started at 2 / (2 + trace(A)) * I converges to the same inverse using
only batched FP32 products. The iteration count covers the worst condition
number the trace allows, and the residual is checked rather than assumed.
"""
import math


def iterations_for(trace_bound):
    """Quadratic steps that shrink the initial error below FP32 resolution."""
    condition = 1. + max(0., float(trace_bound))
    error = (condition - 1.) / (condition + 1.)
    if error <= 0.:
        return 1
    # error ** (2 ** k) <= 2 ** -26  ->  2 ** k >= 26 ln 2 / -ln(error)
    return max(1, math.ceil(math.log2(26. * math.log(2.) / -math.log(error))) + 1)


def iterations_to(trace_bound, error):
    """Quadratic steps that shrink the initial error below `error`."""
    condition = 1. + max(0., float(trace_bound))
    start = (condition - 1.) / (condition + 1.)
    if start <= error:
        return 0
    return max(1, math.ceil(math.log2(math.log(error) / math.log(start))))


BATCH_CHUNK = 1024


def inverse_spd(A32, *, max_residual=1e-3, low_precision=True, chunk=BATCH_CHUNK):
    """Chunked over the batch: the FP32/FP16 iterates of all F x H matrices at
    once cost about 1.5 GiB on a full first pass and ran an M5 out of memory.

    The host reads two scalars per call, the spectral bound and the residual, not
    two per chunk: each read waits for every queued kernel."""
    import torch
    flat = A32.reshape(-1, *A32.shape[-2:])
    trace = torch.diagonal(flat, dim1=-2, dim2=-1).sum(-1).clamp_min(0.)
    # min(trace, Frobenius norm) bounds the largest eigenvalue of a PSD A; the
    # tighter bound saved one of nine first-pass iterations.
    scale = torch.minimum(trace, flat.square().sum((-2, -1)).sqrt()) if low_precision else trace
    bound = float(scale.max())
    out, residuals = torch.empty_like(flat), []
    for begin in range(0, flat.shape[0], chunk):
        end = begin + chunk
        out[begin:end], part = _inverse_spd(flat[begin:end], bound, scale=scale[begin:end],
                                            low_precision=low_precision)
        residuals.append(part)
    residual = float(residuals[0] if len(residuals) == 1 else torch.stack(residuals).amax())
    if not math.isfinite(residual) or residual > max_residual:
        raise FloatingPointError(f'VDN inverse did not converge (residual {residual:.3g})')
    return out.reshape(A32.shape)


def _inverse_spd(A32, bound, *, scale=None, low_precision=True):
    """(I + A)^-1 for a batch of symmetric positive semidefinite FP32 A, and the
    largest residual entry as a device scalar. `bound` bounds every `scale`,
    which bounds each matrix's largest eigenvalue (its trace by default).

    MPS runs FP32 batched products at about 1.4 TFLOPS on an M5 and FP16 ones
    several times faster, so the long contraction phase runs in FP16 until the
    error is about 1e-3; each FP32 step then squares the error, and two reach
    FP32 resolution. Entries stay within FP16 range: ||X|| <= 1, ||M|| <= 1 + trace.
    """
    import torch
    size = A32.shape[-1]
    eye = torch.eye(size, device=A32.device, dtype=torch.float32)
    M = A32 + eye
    if scale is None:
        scale = torch.diagonal(A32, dim1=-2, dim2=-1).sum(-1).clamp_min(0.)
    two = 2. * eye
    if low_precision and A32.device.type == 'mps':
        X = (2. / (2. + scale))[..., None, None] * eye
        M16, X16, two16 = M.half(), X.half(), two.half()
        for _ in range(iterations_to(bound, 2e-3)):
            X16 = X16 @ (two16 - M16 @ X16)
        X = X16.float()
        del M16, X16
        refinements = 3
    else:
        X = (2. / (2. + scale))[..., None, None] * eye
        # One step beyond the bound absorbs FP32 rounding in the products.
        refinements = iterations_for(bound) + 1
    for _ in range(refinements):
        X = X @ (two - M @ X)
    X = 0.5 * (X + X.transpose(-1, -2))
    # A NaN anywhere must fail the check: amax propagates it.
    return X, (M @ X - eye).abs().amax()


SMALL_BATCH = 512


def factor_apply(backend, alpha, A_raw, B_raw, *, stats=None):
    """Drop-in for VdnDelta.factor_apply: (transition, injection) in the input dtypes."""
    from src.models.linear_attention.delta_rule import VdnDelta
    exact = getattr(backend, '_freevideo_cholesky', None)
    if exact is None:
        from functools import partial
        exact = partial(VdnDelta.factor_apply, backend)
    if A_raw.shape[:-2].numel() < SMALL_BATCH:
        # The prompt's single chunk is one matrix per head: a dozen launch-bound
        # products cost more than the exact factor there.
        if stats is not None:
            stats['cholesky_small_batch_calls'] = stats.get('cholesky_small_batch_calls', 0) + 1
        return exact(alpha, A_raw, B_raw)
    try:
        inverse = inverse_spd(A_raw.float())
    except FloatingPointError:
        # Keep the request on the exact Cholesky path rather than failing it.
        if stats is not None:
            stats['cholesky_residual_calls'] = stats.get('cholesky_residual_calls', 0) + 1
        return exact(alpha, A_raw, B_raw)
    if stats is not None:
        stats['newton_calls'] = stats.get('newton_calls', 0) + 1
    transition = alpha.unsqueeze(-1) * inverse
    injection = B_raw.float() @ inverse
    return transition.to(A_raw.dtype), injection.to(B_raw.dtype)


def install(model, *, stats=None):
    """Wrap only this model's lazy unscaled delta backends, including text state."""
    import types
    import torch
    from src.models.linear_attention.branch import BidirectionalLinearBranch
    from src.models.linear_attention.delta_rule import VdnDelta

    def apply(backend, alpha, A_raw, B_raw):
        if A_raw.device.type != 'mps' or torch.is_grad_enabled():
            if stats is not None:
                stats['reference_calls'] = stats.get('reference_calls', 0) + 1
            return backend._freevideo_cholesky(alpha, A_raw, B_raw)
        return factor_apply(backend, alpha, A_raw, B_raw, stats=stats)

    def delta_backend(branch, attr, length):
        backend = branch._freevideo_eager_delta_backend(attr, length)
        # Subclasses may implement a different scaling rule: leave them intact.
        if type(backend) is VdnDelta and not hasattr(backend, '_freevideo_cholesky'):
            backend._freevideo_cholesky = backend.factor_apply
            backend.factor_apply = types.MethodType(apply, backend)
        return backend

    for branch in model.modules():
        if isinstance(branch, BidirectionalLinearBranch) and not hasattr(branch, '_freevideo_eager_delta_backend'):
            branch._freevideo_eager_delta_backend = branch._delta_backend
            branch._delta_backend = types.MethodType(delta_backend, branch)
    return 'mps-newton-schulz-v1'
