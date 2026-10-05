"""Window softmax attention through MLX's fused Metal SDPA.

On the profiled M5 window shapes the Torch path ran at about 3.4 TFLOPS and
MLX's fused kernel at 10.5-12 TFLOPS. Those measurements do not establish which
Torch kernel ran. Q/K/V share their prepared Metal buffers through DLPack.
The planner retains its conservative import allowance until complete workload
capacity measurements justify changing compute partitions.

The row sets are exactly the decomposed plan's: global and anchor-row queries
see every key; each run of frames with identical bounds sees the globals, its
window frames and any anchor columns. Only the reduction order changes.
"""
import time

import numpy as np


def _merge(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and merged[-1][1] >= start:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def plan_ranges(layout, bounds, anchor_frames):
    """Dense query ranges and (query range, key ranges) windows, covering every row once."""
    rows, frames, per_frame = layout.seq_len, layout.num_frames, layout.tokens_per_frame
    video_start, video_end = layout.video_start, layout.video_end
    anchors = {0, frames - 1} if anchor_frames in ('columns', 'rows', 'both') else set()
    dense_frames = anchors if anchor_frames in ('rows', 'both') else set()
    column_frames = anchors if anchor_frames in ('columns', 'both') else set()

    def frame_rows(frame):
        return (video_start + frame * per_frame, video_start + (frame + 1) * per_frame)

    globals_ = [r for r in ((0, video_start), (video_end, rows)) if r[0] < r[1]]
    dense = _merge(globals_ + [frame_rows(f) for f in sorted(dense_frames)])
    groups = []
    for frame in range(frames):
        if frame in dense_frames:
            continue
        if groups and bounds[groups[-1][-1]] == bounds[frame] and groups[-1][-1] == frame - 1:
            groups[-1].append(frame)
        else:
            groups.append([frame])
    windows = []
    for group in groups:
        low, high = bounds[group[0]]
        keys = sorted(set(range(max(low, 0), min(high + 1, frames))) | column_frames)
        query = _merge([frame_rows(f) for f in group])
        if len(query) != 1:
            raise ValueError('A window group must be one contiguous run of frames')
        windows.append((query[0], tuple(_merge(globals_ + [frame_rows(f) for f in keys]))))
    covered = sum(b - a for a, b in dense) + sum(q[1] - q[0] for q, _ in windows)
    if covered != rows:
        raise ValueError(f'Window plan covers {covered} of {rows} rows')
    return dense, windows


def to_mlx(tensor):
    """Share prepared MPS storage; retain the host conversion for CPU inputs."""
    return to_mlx_many((tensor,))[0]


def to_mlx_many(tensors):
    """Prepare all producer buffers before one Torch-to-MLX stream boundary.

    A fence before preparing the last strided input would allow MLX to read an
    unfinished copy. Validate the entire batch, prepare every copy, then fence.
    DLPack retains each producer until lazy MLX consumers release it.
    """
    import mlx.core as mx
    import torch
    tensors = tuple(tensors)
    for tensor in tensors:
        if tensor.device.type == 'mps' and (torch.is_grad_enabled() or tensor.requires_grad):
            raise ValueError('Shared MPS imports require no-grad inference')
    prepared = tuple(t.contiguous() if t.device.type == 'mps' and not t.is_contiguous()
                     else t for t in tensors)
    if any(t.device.type == 'mps' for t in tensors):
        torch.mps.synchronize()
    result = []
    for original, tensor in zip(tensors, prepared):
        if original.device.type == 'mps':
            # Sharing failures stay explicit; do not silently duplicate Q/K/V.
            result.append(mx.from_dlpack(tensor, copy=False))
        else:
            host = tensor.detach().to('cpu')
            if host.dtype == torch.bfloat16:
                result.append(mx.array(host.view(torch.int16).numpy()).view(mx.bfloat16))
            else:
                result.append(mx.array(host.numpy()))
    return tuple(result)


def to_torch(array, device):
    """MLX array -> torch tensor; on MPS the DLPack capsule shares the buffer."""
    import mlx.core as mx
    import torch
    mx.eval(array)
    if str(device).startswith('mps'):
        try:
            # The capsule keeps the MLX array alive for the tensor's lifetime.
            return torch.utils.dlpack.from_dlpack(array)
        except (RuntimeError, TypeError, BufferError):
            pass
    if array.dtype == mx.bfloat16:
        host = np.array(array.view(mx.int16), copy=False)
        return torch.from_numpy(host).view(torch.bfloat16).to(device)
    return torch.from_numpy(np.array(array, copy=False)).to(device)


def window_attention(query, key, value, layout, bounds, scale, anchor_frames, *,
                     batch_bytes=1 << 29, stats=None):
    """[S, H, d] torch tensors -> [S, H, d] on the query's device.

    batch_bytes bounds the gathered keys plus values of one batched call; the
    MLX kernel itself never materializes scores.
    """
    import mlx.core as mx
    tick = time.perf_counter()
    dense, windows = plan_ranges(layout, bounds, anchor_frames)
    q, k, v = to_mlx_many((query, key, value))                  # [S, H, d], imported
    heads, dim = q.shape[1], q.shape[2]
    qh, kh, vh = (mx.swapaxes(t, 0, 1) for t in (q, k, v))       # [H, S, d] views
    imported = time.perf_counter()
    pieces = []
    for start, end in dense:
        out = mx.fast.scaled_dot_product_attention(qh[None, :, start:end], kh[None], vh[None], scale=scale)
        pieces.append((start, mx.swapaxes(out[0], 0, 1)))
    shapes = {}
    for (qa, qb), keys in windows:
        shapes.setdefault((qb - qa, tuple(b - a for a, b in keys)), []).append(((qa, qb), keys))
    for (length, key_lengths), members in shapes.items():
        per_window = 2 * heads * sum(key_lengths) * dim * 2
        count = max(1, min(len(members), batch_bytes // max(1, per_window)))
        for begin in range(0, len(members), count):
            chosen = members[begin:begin + count]
            kb = mx.stack([mx.concatenate([kh[:, a:b] for a, b in keys], axis=1) for _, keys in chosen])
            vb = mx.stack([mx.concatenate([vh[:, a:b] for a, b in keys], axis=1) for _, keys in chosen])
            qb = mx.stack([qh[:, qa:qe] for (qa, qe), _ in chosen])
            out = mx.fast.scaled_dot_product_attention(qb, kb, vb, scale=scale)   # [B, H, L, d]
            for index, ((qa, _), _) in enumerate(chosen):
                pieces.append((qa, mx.swapaxes(out[index], 0, 1)))
            # Bound live gathers: evaluate this batch before building the next.
            mx.eval([p for _, p in pieces])
            del kb, vb, qb, out
    computed = time.perf_counter()
    pieces.sort(key=lambda item: item[0])
    result = mx.concatenate([p for _, p in pieces], axis=0)                     # [S, H, d]
    output = to_torch(result, query.device)
    if output.device != query.device or output.shape != query.shape or output.dtype != query.dtype:
        raise ValueError('MLX attention output differs from its query geometry')
    del q, k, v, qh, kh, vh, pieces, result
    # Torch and MLX draw on one unified pool; do not let MLX keep freed buffers.
    mx.clear_cache()
    if stats is not None:
        done = time.perf_counter()
        stats['import_seconds'] = stats.get('import_seconds', 0.) + imported - tick
        stats['attention_seconds'] = stats.get('attention_seconds', 0.) + computed - imported
        stats['export_seconds'] = stats.get('export_seconds', 0.) + done - computed
    return output


class MLXWindowAttention:
    """Drop-in for WindowAttention's call contract inside the native hybrid adapter."""

    def __init__(self, *, batch_bytes=1 << 29):
        self.batch_bytes = batch_bytes
        self.calls = self.window_calls = 0
        self.backend_calls = {'global_mlx': 0, 'window_mlx': 0}
        self.stats = {}

    def install(self, transformer):
        from src.models.hybrid_transform import iter_hybrids
        import types
        policy = self

        def window(attn, q, k, v, layout, bounds, scale, inference):
            return policy(q, k, v, layout, bounds, scale, attn.anchor_frames)

        count = 0
        for attn in iter_hybrids(transformer):
            attn._window_softmax = types.MethodType(window, attn)
            count += 1
        return count

    def __call__(self, query, key, value, layout, bounds, scale, anchor_frames='none'):
        self.calls += 1
        self.window_calls += 1
        self.backend_calls['window_mlx'] += 1
        return window_attention(query, key, value, layout, bounds, scale, anchor_frames,
                                batch_bytes=self.batch_bytes, stats=self.stats)
