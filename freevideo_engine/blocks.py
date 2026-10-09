"""Release consumed activations and reuse branch outputs in fused H3 blocks."""
import types
import torch


def _post_into_branch(residual, gate, indices, branch):
    branch.copy_(residual + gate.index_select(0, indices) * branch)
    return branch


def chunked_ff(original, chunk):
    """Run a row-wise FF over tiles of `chunk` rows.

    Each tile's output may replace that tile's input once it is computed. The
    bounded blocks own their normalized FF input and drop it right after the
    call, so for them the output goes back into it instead of into a full-size
    buffer allocated for every block. On Windows, without expandable segments,
    that allocation is where staging ran out of memory: an RTX 5060 at
    1344x768x362 asked for 1.12 GiB with 1.06 GiB free in pieces, and an RTX
    3090 capped at a 6 GiB laptop's 4.76 GiB asked for 0.57 with 0.60 free.
    """
    def forward(ff, hidden, *args, **kwargs):
        output = hidden if getattr(ff, '_freevideo_owns_input', False) else torch.empty_like(hidden)
        for start in range(0, hidden.shape[-2], chunk):
            output[..., start:start + chunk, :] = original(hidden[..., start:start + chunk, :], *args, **kwargs)
        return output
    return forward


def install_bounded_blocks(model, residual_offload=False):
    from src.models.ops.fused_block import _compiled, _pre_ref
    from .row_kernels import POST_ROWS, dynamic_rows
    pre = _compiled('pre', _pre_ref)
    # Autotuning a mutating kernel clones its full branch input. That extra
    # ~758 MiB copy defeats reuse under small memory limits. This elementwise
    # kernel needs no shape search or reduction; use the fixed launch policy.
    # One graph for every row count, like the kernels in row_kernels.
    post = dynamic_rows(torch.compile(_post_into_branch, dynamic=False,
                                      options={'triton.autotune_pointwise': False}), POST_ROWS)

    def forward(self, hidden_states, temb, adaln_indices, rotary_emb, attention_mask=None):
        if torch.is_grad_enabled():
            raise RuntimeError('Buffer reuse requires inference without autograd')
        hidden, indices = hidden_states, adaln_indices
        shift_a, scale_a, gate_a, shift_f, scale_f, gate_f = self.adaln_proj(temb)
        normalized = pre(hidden, self.norm1.weight, self.norm1.eps, scale_a, shift_a, indices)
        branch = self.attn(normalized, rotary_emb, attention_mask)
        del normalized
        hidden = post(hidden, gate_a, indices, branch)
        del branch
        normalized = pre(hidden, self.norm2.weight, self.norm2.eps, scale_f, shift_f, indices)
        branch = self.ff(normalized)
        del normalized
        return post(hidden, gate_f, indices, branch)

    if residual_offload:
        from .residual import residual_forward
        forward = residual_forward(pre, post)
    for block in model.transformer_blocks:
        block.forward = types.MethodType(forward, block)
        # Both forwards drop the normalized attention and FF inputs right after the call.
        block.ff._freevideo_owns_input = True
        block.attn._freevideo_owns_input = True
