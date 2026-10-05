"""Bound the native reference linear branch by independent attention heads.

Keep the upstream eager operations, dtypes and temporal/spatial neighborhoods.
Only independent heads are grouped; no token reduction is split. Smaller batched
GEMM/convolution shapes can change rounding and need native numerical comparison.
The CUDA inference body and its fused kernels are never selected here.
"""
from functools import partial
import types


def frame_mean(xv, num_frames, tokens_per_frame):
    """Keep each full spatial reduction; bound independent frames' FP32 casts."""
    import torch
    frames = xv.view(num_frames, tokens_per_frame, -1)
    # MPS mean(dtype=FP32) can materialize an FP32 copy of its input. A whole
    # 10-second sequence needs hundreds of MiB before any reduction happens.
    # Batch only independent frames, never split the spatial reduction axis.
    batch = max(1, (32 * 2**20) // (tokens_per_frame * frames.shape[-1] * 4))
    result = torch.empty((num_frames, frames.shape[-1]), dtype=torch.float32, device=xv.device)
    for start in range(0, num_frames, batch):
        result[start:start + batch] = torch.mean(frames[start:start + batch], dim=1, dtype=torch.float32)
    return result


def frame_statistics(key, value, beta, *, a_fp32):
    """Call the original statistics on bounded batches of independent frames."""
    import torch
    from src.models.linear_attention.scan import frame_statistics as reference
    frames, heads, tokens, dim = key.shape
    # Preserve the complete token/channel contractions and original FP32 A /
    # BF16 B policy. Limit simultaneous cast/scaling planes, not their precision.
    per_frame = heads * tokens * dim * (8 + 2 * key.element_size())
    batch = max(1, (16 * 2**20) // per_frame)
    if frames <= batch:
        return reference(key, value, beta, a_fp32=a_fp32)
    A = torch.empty((frames, heads, dim, dim), device=key.device, dtype=torch.float32)
    B = torch.empty((frames, heads, value.shape[-1], dim), device=key.device, dtype=torch.float32)
    for start in range(0, frames, batch):
        end = min(start + batch, frames)
        a, b = reference(key[start:end], value[start:end], beta[start:end], a_fp32=a_fp32)
        A[start:end], B[start:end] = a, b
        del a, b
    return A, B


def readout(branch, xv, num_frames, tokens_per_frame, bounds, qkv_raw,
            frame_size=None, text_x=None, text_qkv_raw=None, *, head_chunk=4, gate_chunk=256,
            output=None):
    import torch
    from src.models.linear_attention.scan import _run_scans, gather_linear_state
    if torch.is_grad_enabled():
        raise RuntimeError('Bounded MPS linear attention requires inference without autograd')
    heads, dim = branch.num_heads, branch.head_dim
    rows = num_frames * tokens_per_frame
    backend = branch._delta_backend('backend', tokens_per_frame)
    # Evaluate the original small projections with their full shapes, preserving
    # the FP32 frame mean and alpha island, and the shared text initial state.
    beta = torch.sigmoid(branch.beta_proj(xv)).view(num_frames, tokens_per_frame, heads)
    beta = beta.permute(0, 2, 1)
    alpha = branch.alpha(frame_mean(xv, num_frames, tokens_per_frame))
    text_state = branch._text_state(text_x, text_qkv_raw)
    out = xv.new_empty(rows, heads, dim) if output is None else output
    if out.shape != (rows, heads, dim) or out.dtype != xv.dtype or out.device != xv.device:
        raise ValueError('MPS linear readout output shape, dtype or device differs')
    for start in range(0, heads, head_chunk):
        group = slice(start, min(start + head_chunk, heads))
        count = group.stop - group.start
        shape = (num_frames, tokens_per_frame, count, dim)
        query, key, value = (
            branch._feature_one(tokens[:, group].contiguous(), proj, num_frames,
                                frame_size, heads=group)
            for proj, tokens in zip(('q', 'k', 'v'), qkv_raw))
        A, B = frame_statistics(key.view(shape).permute(0, 2, 1, 3),
                                value.view(shape).permute(0, 2, 1, 3),
                                beta[:, group], a_fp32=branch.a_fp32)
        del key, value
        initial = None if text_state is None else text_state[group]
        prefix, suffix = _run_scans(backend, alpha[:, group], A, B, text_state=initial)
        del A, B
        state = gather_linear_state(prefix, suffix, alpha[:, group], bounds,
                                    bridge=branch.bridge, text_state=initial).to(xv.dtype)
        del prefix, suffix
        part = torch.einsum('fhvk,fshk->fshv', state, query.view(shape))
        del state, query
        out[:, group] = branch.norm(part.reshape(rows, count, dim))
        del part
    # The original gate retains all channels and reduction axes. Bound only its
    # independent token rows, avoiding another full video-sized activation.
    for start in range(0, rows, gate_chunk):
        end = min(start + gate_chunk, rows)
        out[start:end].mul_(branch.output_gate(xv[start:end]))
    return out.reshape(rows, heads * dim)


def consume_qkv(branch, xv, num_frames, tokens_per_frame, bounds, qkv_raw, *,
                frame_size=None, skip_ends=False, text_x=None, text_qkv_raw=None,
                head_chunk=4, gate_chunk=256):
    """Consume dead query storage after softmax, preserving upstream anchors.

    Only the native hybrid adapter owns these QKV buffers and may use this path.
    Each head's query is read before its output overwrites that head's storage;
    K/V and text state remain unchanged. No whole-sequence readout/scatter copy.
    """
    import torch
    if torch.is_grad_enabled():
        raise RuntimeError('MPS QKV consumption requires inference without autograd')
    target = qkv_raw[0]
    if target.shape != (num_frames * tokens_per_frame, branch.num_heads, branch.head_dim):
        raise ValueError('MPS QKV output geometry differs')
    inner = slice(tokens_per_frame, (num_frames - 1) * tokens_per_frame) if skip_ends else slice(None)
    if not skip_ends or num_frames > 2:
        readout(branch, xv[inner], num_frames - 2 if skip_ends else num_frames,
            tokens_per_frame, [(lo - 1, hi - 1) for lo, hi in bounds[1:-1]] if skip_ends else bounds,
            tuple(t[inner] for t in qkv_raw), frame_size, text_x, text_qkv_raw,
            head_chunk=head_chunk, gate_chunk=gate_chunk, output=target[inner])
    if skip_ends:
        target[:tokens_per_frame].zero_()
        target[(num_frames - 1) * tokens_per_frame:].zero_()
    return target.reshape(num_frames * tokens_per_frame, -1)


def install(model, *, head_chunk=4, gate_chunk=256):
    """Bind only this native model's eager readout; upstream anchor handling stays."""
    from src.models.linear_attention.branch import BidirectionalLinearBranch
    for name, value in (('head_chunk', head_chunk), ('gate_chunk', gate_chunk)):
        if type(value) is not int or value < 1:
            raise ValueError(name + ' must be a positive integer')
    count = 0
    for module in model.modules():
        if isinstance(module, BidirectionalLinearBranch):
            module._readout = types.MethodType(partial(readout, head_chunk=head_chunk,
                                                     gate_chunk=gate_chunk), module)
            count += 1
    return dict(implementation='mps-eager-head-groups-v3', branches=count,
                head_chunk=head_chunk, gate_chunk=gate_chunk)
