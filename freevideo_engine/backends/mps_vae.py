"""H3 video decoder attention through MLX's fused Metal kernel on MPS.

A decoder tile is 448 latent voxels plus five appended tokens, attended over
with full self-attention by 32 heads of 64 channels. PyTorch's MPS kernel
materializes each [32, 453, 453] score matrix; in a fixed default clip, MLX's
fused kernel decoded 28 tiles in 32 s instead of 37 s. Projections, norms,
RoPE and the residual path are the upstream processor's.
"""


def mlx_attention(query, key, value):
    """[B, S, H, D] Torch FP16/BF16 MPS -> [B, S, H, D] on the same device."""
    import mlx.core as mx
    from .mps_mlx_attention import to_mlx_many, to_torch
    # Reuse the strict shared-buffer import used by DiT attention. Prepare all
    # strided inputs before one producer fence; DLPack retains their storage.
    # Attention shapes, strides after swapaxes, scale and arithmetic stay fixed.
    q, k, v = (mx.swapaxes(t, 1, 2) for t in to_mlx_many((query, key, value)))
    out = mx.fast.scaled_dot_product_attention(q, k, v, scale=query.shape[-1] ** -.5)
    return to_torch(mx.swapaxes(out, 1, 2), query.device)


class MLXAttentionProcessor:
    """MiniMaxH3VideoAttnProcessor at OpenVDN 30b6b380 with MLX's fused attention."""

    def __call__(self, attn, hidden_states, rotary_emb=None):
        import torch
        query = attn.to_q(hidden_states).unflatten(2, (attn.heads, -1))
        key = attn.to_k(hidden_states).unflatten(2, (attn.heads, -1))
        value = attn.to_v(hidden_states).unflatten(2, (attn.heads, -1))

        # The reference normalizes Q/K in float32 regardless of the compute dtype.
        query = attn.norm_q(query.float()).to(query.dtype)
        key = attn.norm_k(key.float()).to(key.dtype)

        if rotary_emb is not None:
            cos, sin = rotary_emb
            cos = cos.to(query.dtype)
            sin = sin.to(query.dtype)
            rotary_dim = cos.shape[-1]
            query_rotary, query_pass = query[..., :rotary_dim], query[..., rotary_dim:]
            key_rotary, key_pass = key[..., :rotary_dim], key[..., rotary_dim:]
            query_first, query_second = query_rotary.chunk(2, dim=-1)
            key_first, key_second = key_rotary.chunk(2, dim=-1)
            query_rotated = torch.cat([-query_second, query_first], dim=-1)
            key_rotated = torch.cat([-key_second, key_first], dim=-1)
            query = torch.cat([query_rotary * cos + query_rotated * sin, query_pass], dim=-1)
            key = torch.cat([key_rotary * cos + key_rotated * sin, key_pass], dim=-1)

        hidden_states = mlx_attention(query, key, value.to(query.dtype)).flatten(2, 3)
        return attn.to_out[0](hidden_states)


def install_mlx_attention(vae):
    """Select optional MLX before execution; installed-backend failures stay visible."""
    try:
        import mlx.core as mx
    except ModuleNotFoundError as error:
        if error.name not in ('mlx', 'mlx.core'):
            raise
        return 0
    # A broken native library, Metal initialization error or allocation failure
    # must retain its cause rather than silently selecting different arithmetic.
    mx.array([1.0])
    from diffusers.models.autoencoders.autoencoder_kl_minimax_h3 import (
        MiniMaxH3VideoAttention, MiniMaxH3VideoAttnProcessor)
    modules = [module for module in vae.decoder.modules() if isinstance(module, MiniMaxH3VideoAttention)]
    if any(type(module.processor) is not MiniMaxH3VideoAttnProcessor for module in modules):
        raise ValueError('Unexpected H3 decoder attention processor')
    for module in modules:
        module.processor = MLXAttentionProcessor()
    return len(modules)
