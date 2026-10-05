"""Native MPS attention with the same NHD contract as the CUDA adapter."""


class MPSAttentionKernels:
    def __init__(self, global_backend, window_backend, *, query_chunk=0, window_varlen=False):
        if (global_backend, window_backend) != ('mps', 'mps'):
            raise ValueError('The MPS device requires explicit mps attention for both legs')
        if window_varlen:
            raise ValueError('MPS packed varlen attention is not implemented; use bounded window batches')
        if type(query_chunk) is not int or query_chunk < 0:
            raise ValueError('Query chunk must be nonnegative')

    def batched(self, leg, q, k, v, scale):
        if leg != 'mps':
            raise ValueError('Unknown MPS attention operator: ' + str(leg))
        if any(t.device.type != 'mps' for t in (q, k, v)):
            raise ValueError('MPS attention requires native MPS Q/K/V tensors')
        import torch.nn.functional as F
        result = F.scaled_dot_product_attention(q.transpose(1, 2), k.transpose(1, 2),
            v.transpose(1, 2), dropout_p=0., is_causal=False, scale=scale)
        return result.transpose(1, 2)

    def varlen(self, *args, **kwargs):
        raise NotImplementedError('MPS packed varlen attention is not implemented')

    def decomposed(self, *args, **kwargs):
        raise NotImplementedError('CUDA FA4 decomposition is not an MPS kernel')


def prepare_qk(value, norm, rotary_emb, chunk):
    """Preserve the full per-head reduction and each token's rotary position."""
    import torch
    from diffusers.models.transformers.transformer_minimax_h3 import _apply_rotary_emb
    output = torch.empty_like(value)
    for start in range(0, value.shape[0], chunk):
        end = min(start + chunk, value.shape[0])
        part = norm(value[start:end])
        if rotary_emb is not None:
            part = _apply_rotary_emb(part.unsqueeze(0),
                *(position[start:end] for position in rotary_emb)).squeeze(0)
        output[start:end] = part
    return output


def project_rows(value, projection, *, chunk, dtype, reuse=False):
    """Project independent token rows, optionally consuming an owned buffer.

    Reuse is only valid for a dead, unaliased intermediate with sufficient row
    width and matching dtype. The complete channel reduction remains in each GEMM.
    """
    import torch
    if torch.is_grad_enabled():
        raise RuntimeError('MPS row projection requires inference without autograd')
    if type(chunk) is not int or chunk < 1:
        raise ValueError('Projection chunk must be a positive integer')
    if reuse and value.shape[-1] >= projection.out_features and value.dtype == dtype:
        output = value[:, :projection.out_features]
    else:
        output = torch.empty((value.shape[0], projection.out_features), dtype=dtype, device=value.device)
    for start in range(0, value.shape[0], chunk):
        end = min(start + chunk, value.shape[0])
        output[start:end] = projection(value[start:end].to(dtype))
    return output


def install_hybrid(model, attention, *, head_chunk=4, row_chunk=256, qk_prepare=None):
    """Native eager hybrid attention with bounded independent-head preparation.

    Keep full QKV projections and upstream linear attention. QK normalization,
    RoPE and softmax run on independent head groups; all sequence/mask reductions
    remain intact. This adapter is installed only by the native engine.
    """
    import types
    import torch
    from src.models.hybrid_attention import HybridAttention
    qk_prepare = prepare_qk if qk_prepare is None else qk_prepare
    for name, value in (('head_chunk', head_chunk), ('row_chunk', row_chunk)):
        if type(value) is not int or value < 1:
            raise ValueError(name + ' must be a positive integer')

    def forward(owner, x, rotary_emb):
        if torch.is_grad_enabled() or owner.inference_mode or owner.hybrid_inference_mode:
            raise RuntimeError('MPS hybrid attention requires the eager, no-grad branch')
        orig, layout = owner.orig, owner.layout
        bounds = owner._bounds(layout) if layout is not None else None
        full = layout is None or all(lo <= 0 and hi >= layout.num_frames - 1 for lo, hi in bounds)
        raw = tuple(p(x).unflatten(-1, (orig.heads, -1))
                    for p in (orig.to_q, orig.to_k, orig.to_v))
        softmax = torch.empty_like(raw[0])
        for start in range(0, orig.heads, head_chunk):
            group = slice(start, min(start + head_chunk, orig.heads))
            query = qk_prepare(raw[0][:, group], orig.norm_q, rotary_emb, row_chunk)
            key = qk_prepare(raw[1][:, group], orig.norm_k, rotary_emb, row_chunk)
            if full:
                # Match the original processor's full-cover dispatcher. Only
                # independent heads are grouped; no alternative kernel fallback.
                from diffusers.models.attention_dispatch import dispatch_attention_fn
                part = dispatch_attention_fn(query.unsqueeze(0), key.unsqueeze(0),
                    raw[2][:, group].unsqueeze(0), attn_mask=None, dropout_p=0., is_causal=False,
                    backend=getattr(type(orig.processor), '_attention_backend', None)).squeeze(0)
            else:
                part = attention(query, key, raw[2][:, group], layout, bounds,
                                 owner.head_dim ** -.5, owner.anchor_frames)
            softmax[:, group] = part
            del query, key, part
        if owner.enable_softmax_gate:
            gate = owner.softmax_gate(x).to(softmax.dtype)
            softmax.mul_(gate)
            del gate
        # This softmax buffer has no remaining readers. A narrowing projection can
        # consume it one row chunk at a time instead of allocating another full
        # packed-sequence activation while QKV are still needed by the linear leg.
        out = orig.to_out[1](project_rows(softmax.reshape(x.shape[0], -1), orig.to_out[0],
                                        chunk=row_chunk, dtype=x.dtype, reuse=True))
        del softmax
        if not full and owner.linear_attention_enabled:
            start, end = layout.video_start, layout.video_end
            video_qkv = tuple(t[start:end] for t in raw)
            text_x = text_qkv = None
            if owner.enable_text_state:
                a, b = layout.text_range
                text_x, text_qkv = x[a:b], tuple(t[a:b] for t in raw)
            from .mps_linear import consume_qkv
            readout = consume_qkv(owner.linear_attention, x[start:end], layout.num_frames, layout.tokens_per_frame,
                bounds, qkv_raw=video_qkv,
                frame_size=layout.frame_size if owner.linear_attention.short_conv is not None else None,
                skip_ends=owner.anchor_frames == 'both', text_x=text_x, text_qkv_raw=text_qkv,
                head_chunk=head_chunk, gate_chunk=row_chunk)
            # The readout now owns Q storage. Drop the remaining views so K/V
            # can be released before the output projection.
            del raw, video_qkv, text_qkv
            for begin in range(0, end - start, row_chunk):
                stop = min(begin + row_chunk, end - start)
                out[start + begin:start + stop] += owner.to_out_linear(readout[begin:stop].type_as(x))
        return out

    count = 0
    for module in model.modules():
        if isinstance(module, HybridAttention):
            module._hybrid_forward = types.MethodType(forward, module)
            count += 1
    return dict(implementation='mps-eager-hybrid-heads-v3', branches=count,
                head_chunk=head_chunk, row_chunk=row_chunk)
