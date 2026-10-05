"""Bounded MPS inference buffers, preserving full normalization reductions.

The consumed FF input is overwritten only after each original row partition has
completed. Normalization is evaluated over the original full input. Only independent row
selection, multiplication and addition are chunked into owned output buffers.
"""
import types


def post_into_branch(residual, gate, indices, branch, chunk, *, stats=None):
    import torch
    if torch.is_grad_enabled():
        raise RuntimeError('Owned branch reuse requires inference without autograd')
    if type(chunk) is not int or chunk < 1:
        raise ValueError('Positive integer row chunk required')
    if residual.shape != branch.shape or residual.ndim != 3:
        raise ValueError('Expected matching batch, row, channel residual and branch')
    if gate.ndim != 2 or indices.ndim != 1 or indices.numel() != branch.shape[-2]:
        raise ValueError('Invalid packed modulation rows')
    if gate.shape[-1] != branch.shape[-1]:
        raise ValueError('Gate width differs')
    if not residual.dtype == branch.dtype == gate.dtype:
        raise ValueError('Buffer reuse requires the original common output dtype')
    if any(value.device != branch.device for value in (residual, gate, indices)):
        raise ValueError('Branch and modulation must share a device')
    if any(value.untyped_storage().data_ptr() == branch.untyped_storage().data_ptr()
           for value in (residual, gate, indices)):
        raise ValueError('The consumed branch must not alias live residual or modulation')
    for begin in range(0, branch.shape[-2], chunk):
        end = min(begin + chunk, branch.shape[-2])
        rows = slice(begin, end)
        # Preserve eager multiply and add as separate operations/dtype roundings.
        selected = gate.index_select(0, indices[rows])
        branch[..., rows, :].copy_(residual[..., rows, :] + selected * branch[..., rows, :])
        if stats is not None:
            stats['post_chunks'] = stats.get('post_chunks', 0) + 1
            stats['largest_selected_gate_elements'] = max(
                stats.get('largest_selected_gate_elements', 0), selected.numel())
    return branch


def modulate_owned(normalized, scale, shift, indices, chunk, *, live=(), stats=None):
    import torch
    if torch.is_grad_enabled():
        raise RuntimeError('Owned modulation requires inference without autograd')
    if type(chunk) is not int or chunk < 1:
        raise ValueError('Positive integer row chunk required')
    if normalized.ndim != 3 or not normalized.is_contiguous():
        raise ValueError('Require an owned contiguous batch/row/channel normalization output')
    if scale.ndim != 2 or scale.shape != shift.shape or scale.shape[-1] != normalized.shape[-1]:
        raise ValueError('Invalid scale/shift geometry')
    if indices.ndim != 1 or indices.numel() != normalized.shape[-2]:
        raise ValueError('Invalid packed modulation rows')
    if not normalized.dtype == scale.dtype == shift.dtype:
        raise ValueError('Owned modulation requires the original common output dtype')
    if any(value.device != normalized.device for value in (scale, shift, indices)):
        raise ValueError('Modulation inputs must share a device')
    if any(value.untyped_storage().data_ptr() == normalized.untyped_storage().data_ptr()
           for value in (scale, shift, indices, *live)):
        raise ValueError('Owned normalized output must not alias live inputs')
    for begin in range(0, normalized.shape[-2], chunk):
        end = min(begin + chunk, normalized.shape[-2])
        rows = slice(begin, end)
        selected_scale = scale.index_select(0, indices[rows])
        selected_shift = shift.index_select(0, indices[rows])
        # Do not fuse multiply/add or change their separate dtype rounding.
        normalized[..., rows, :].copy_(
            normalized[..., rows, :] * (1.0 + selected_scale) + selected_shift)
        if stats is not None:
            stats['modulation_chunks'] = stats.get('modulation_chunks', 0) + 1
            stats['largest_selected_modulation_elements'] = max(
                stats.get('largest_selected_modulation_elements', 0), selected_scale.numel())
    return normalized


def feed_forward_owned(value, forward, chunk, *, live=(), stats=None):
    import torch
    if torch.is_grad_enabled():
        raise RuntimeError('Owned feed-forward requires inference without autograd')
    if type(chunk) is not int or chunk < 1:
        raise ValueError('Positive integer row chunk required')
    if value.ndim != 3 or not value.is_contiguous():
        raise ValueError('Require owned contiguous batch/row/channel input')
    if any(other.untyped_storage().data_ptr() == value.untyped_storage().data_ptr() for other in live):
        raise ValueError('Consumed feed-forward input must not alias live inputs')
    for begin in range(0, value.shape[-2], chunk):
        rows = slice(begin, min(begin + chunk, value.shape[-2]))
        part = value[..., rows, :]
        output = forward(part)
        if output.shape != part.shape or output.dtype != part.dtype or output.device != part.device:
            raise ValueError('Feed-forward changed the buffer geometry, dtype or device')
        part.copy_(output)
        if stats is not None:
            stats['ff_chunks'] = stats.get('ff_chunks', 0) + 1
            stats['largest_ff_input_elements'] = max(stats.get('largest_ff_input_elements', 0), part.numel())
        del part, output
    return value


def validate(model, *, row_chunk=256, ff_chunk=256):
    """Validate the complete block/partition contract without changing methods."""
    from diffusers.models.transformers.transformer_minimax_h3 import MiniMaxH3TransformerBlock
    if type(row_chunk) is not int or row_chunk < 1:
        raise ValueError('Positive integer row chunk required')
    if type(ff_chunk) is not int or ff_chunk < 1:
        raise ValueError('Positive integer feed-forward chunk required')
    blocks = tuple(model.transformer_blocks)
    if not blocks:
        raise ValueError('Expected at least one MiniMax H3 transformer block')
    for block in blocks:
        if not isinstance(block, MiniMaxH3TransformerBlock):
            raise TypeError('Expected the pinned MiniMax H3 transformer block')
        if getattr(block.ff, '_freevideo_mps_ff_chunk', None) != ff_chunk:
            raise ValueError('Owned feed-forward must preserve the installed row chunk')
        if block.training:
            raise ValueError('Owned block buffers require evaluation mode')
    return blocks


def install(model, *, row_chunk=256, ff_chunk=256):
    """Bind owned intermediates only after validating every block.

    The engine must install the ordinary MPS feed-forward partition first. Its
    chunk size is the arithmetic contract: changing a GEMM batch can change
    rounding. Reuse that partition, including its final partial chunk.

    This adapter is not enabled by the engine's default policy yet.
    """
    import torch
    blocks = validate(model, row_chunk=row_chunk, ff_chunk=ff_chunk)
    stats = dict(implementation='mps-owned-block-buffers-v1', blocks=0,
                 row_chunk=row_chunk, block_calls=0, post_chunks=0,
                 largest_selected_gate_elements=0, modulation_chunks=0,
                 largest_selected_modulation_elements=0, ff_chunk=ff_chunk, ff_chunks=0, largest_ff_input_elements=0)

    def forward(owner, hidden_states, temb, adaln_indices, rotary_emb, attention_mask=None):
        if torch.is_grad_enabled() or owner.training:
            raise RuntimeError('Owned branch reuse requires evaluation without autograd')
        shift_a, scale_a, gate_a, shift_f, scale_f, gate_f = owner.adaln_proj(temb)
        # Keep normalization reductions unchanged; chunk only independent modulation rows.
        normalized = owner.norm1(hidden_states)
        modulate_owned(normalized, scale_a, shift_a, adaln_indices, row_chunk,
                       live=(hidden_states,), stats=stats)
        branch = owner.attn(normalized, rotary_emb, attention_mask)
        del normalized
        hidden = post_into_branch(hidden_states, gate_a, adaln_indices, branch, row_chunk, stats=stats)
        del branch
        normalized = owner.norm2(hidden)
        modulate_owned(normalized, scale_f, shift_f, adaln_indices, row_chunk,
                       live=(hidden,), stats=stats)
        branch = feed_forward_owned(normalized, owner.ff, ff_chunk, live=(hidden,), stats=stats)
        del normalized
        output = post_into_branch(hidden, gate_f, adaln_indices, branch, row_chunk, stats=stats)
        stats['block_calls'] += 1
        return output

    for block in blocks:
        block.forward = types.MethodType(forward, block)
        stats['blocks'] += 1
    return stats
