"""MPS projection groups with bounded Q/K/V lifetimes.

Project independent output channels per head group. Reuse original norms,
RoPE, window attention, delta scans and full-width output projections. Grouped
GEMM/text-state shapes can change rounding; native/full-output checks are required.
This adapter is not enabled by the engine default policy yet.
"""
import types


def install(model, attention, *, head_chunk=4, row_chunk=1024, qk_prepare=None):
    import torch
    import torch.nn.functional as F
    from src.models.hybrid_attention import HybridAttention
    from src.models.linear_attention.scan import _run_scans, gather_linear_state
    from .mps_attention import prepare_qk, project_rows
    from .mps_linear import frame_mean, frame_statistics
    from ..lora_online import apply as apply_lora
    qk_prepare = prepare_qk if qk_prepare is None else qk_prepare
    for value in (head_chunk, row_chunk):
        if type(value) is not int or value < 1:
            raise ValueError('Positive integer group sizes required')
    modules = tuple(module for module in model.modules() if isinstance(module, HybridAttention))
    if not modules:
        raise ValueError('Expected at least one pinned HybridAttention module')
    for module in modules:
        projections = (module.orig.to_q, module.orig.to_k, module.orig.to_v)
        if any(not isinstance(projection, torch.nn.Linear) for projection in projections):
            raise TypeError('Grouped MPS attention requires eager Linear projections')
        if any(projection.out_features != module.orig.heads * module.head_dim for projection in projections):
            raise ValueError('Projection width must match the original attention heads')
    policy = dict(implementation='mps-grouped-qkv-v1', branches=0,
                  head_chunk=head_chunk, row_chunk=row_chunk,
                  largest_projected_elements=0, full_projection_elements=0)

    def project(module, value, channels, count, dim):
        if not isinstance(module, torch.nn.Linear):
            raise TypeError('Grouped MPS attention requires eager Linear projections')
        weight = module.weight[channels]
        bias = None if module.bias is None else module.bias[channels]
        output = F.linear(value, weight, bias)
        output = apply_lora(module, value, output, channels=channels)
        policy['largest_projected_elements'] = max(policy['largest_projected_elements'], output.numel())
        return output.unflatten(-1, (count, dim))

    def forward(owner, x, rotary_emb):
        if torch.is_grad_enabled() or owner.training or owner.inference_mode or owner.hybrid_inference_mode:
            raise RuntimeError('Grouped MPS QKV requires eager no-grad inference')
        orig, layout = owner.orig, owner.layout
        bounds = owner._bounds(layout) if layout is not None else None
        full = layout is None or all(lo <= 0 and hi >= layout.num_frames - 1 for lo, hi in bounds)
        dim, heads = owner.head_dim, orig.heads
        policy['full_projection_elements'] = max(policy['full_projection_elements'], x.shape[0] * heads * dim)
        softmax = None
        linear_active = not full and owner.linear_attention_enabled
        linear = beta = alpha = text_beta = xv = None
        if linear_active:
            branch = owner.linear_attention
            start, end = layout.video_start, layout.video_end
            skip = owner.anchor_frames == 'both'
            tokens = layout.tokens_per_frame
            frames = layout.num_frames - 2 if skip else layout.num_frames
            inner = slice(tokens, (layout.num_frames - 1) * tokens) if skip else slice(None)
            linear = x.new_zeros(layout.num_frames * tokens, heads, dim)
            xv = x[start:end][inner]
            inner_bounds = [(lo - 1, hi - 1) for lo, hi in bounds[1:-1]] if skip else bounds
            if frames > 0:
                beta = torch.sigmoid(branch.beta_proj(xv)).view(frames, tokens, heads).permute(0, 2, 1)
                alpha = branch.alpha(frame_mean(xv, frames, tokens))
                backend = branch._delta_backend('backend', tokens)
                if owner.enable_text_state:
                    ta, tb = layout.text_range
                    text_beta = torch.sigmoid(branch.beta_proj(x[ta:tb]))
        for begin in range(0, heads, head_chunk):
            group = slice(begin, min(begin + head_chunk, heads))
            count = group.stop - group.start
            channels = slice(group.start * dim, group.stop * dim)
            raw = tuple(project(module, x, channels, count, dim)
                        for module in (orig.to_q, orig.to_k, orig.to_v))
            if softmax is None:
                # Preserve the reference projection dtype under autocast, too.
                softmax = raw[0].new_empty(x.shape[0], heads, dim)
                policy['softmax_buffer_dtype'] = str(softmax.dtype)
                policy['softmax_buffer_bytes'] = softmax.numel() * softmax.element_size()
            if linear_active and raw[0].dtype != x.dtype:
                raise ValueError('MPS linear readout output dtype differs')
            query = qk_prepare(raw[0], orig.norm_q, rotary_emb, row_chunk)
            key = qk_prepare(raw[1], orig.norm_k, rotary_emb, row_chunk)
            if full:
                from diffusers.models.attention_dispatch import dispatch_attention_fn
                part = dispatch_attention_fn(query.unsqueeze(0), key.unsqueeze(0), raw[2].unsqueeze(0),
                    attn_mask=None, dropout_p=0., is_causal=False,
                    backend=getattr(type(orig.processor), '_attention_backend', None)).squeeze(0)
            else:
                part = attention(query, key, raw[2], layout, bounds, dim ** -.5, owner.anchor_frames)
            softmax[:, group] = part
            del query, key, part
            if linear_active and frames > 0:
                initial = None
                if text_beta is not None:
                    initial = branch._text_state(None, tuple(value[ta:tb] for value in raw),
                        heads=group, text_beta=text_beta[:, group])
                shape = (frames, tokens, count, dim)
                query, key, value = (branch._feature_one(value[start:end][inner].contiguous(), name,
                    frames, layout.frame_size if branch.short_conv is not None else None, heads=group)
                    for name, value in zip(('q', 'k', 'v'), raw))
                A, B = frame_statistics(key.view(shape).permute(0, 2, 1, 3),
                    value.view(shape).permute(0, 2, 1, 3), beta[:, group], a_fp32=branch.a_fp32)
                del key, value
                prefix, suffix = _run_scans(backend, alpha[:, group], A, B, text_state=initial)
                del A, B
                state = gather_linear_state(prefix, suffix, alpha[:, group], inner_bounds,
                    bridge=branch.bridge, text_state=initial).to(x.dtype)
                del prefix, suffix
                part = torch.einsum('fhvk,fshk->fshv', state, query.view(shape))
                linear[inner, group] = branch.norm(part.reshape(frames * tokens, count, dim))
                del state, query, part, initial
            del raw
        if owner.enable_softmax_gate:
            gate = owner.softmax_gate(x).to(softmax.dtype)
            softmax.mul_(gate)
            del gate
        out = orig.to_out[1](project_rows(softmax.reshape(x.shape[0], -1), orig.to_out[0],
                                        chunk=row_chunk, dtype=x.dtype, reuse=True))
        del softmax
        if linear_active:
            if frames > 0:
                for begin in range(0, len(xv), row_chunk):
                    stop = min(begin + row_chunk, len(xv))
                    linear[inner][begin:stop].mul_(branch.output_gate(xv[begin:stop]))
            readout = linear.reshape(end - start, -1)
            for begin in range(0, end - start, row_chunk):
                stop = min(begin + row_chunk, end - start)
                out[start + begin:start + stop] += owner.to_out_linear(readout[begin:stop].type_as(x))
        return out

    for module in modules:
        module._hybrid_forward = types.MethodType(forward, module)
        policy['branches'] += 1
    return policy
