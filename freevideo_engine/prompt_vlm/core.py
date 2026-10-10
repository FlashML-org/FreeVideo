"""Portable single-request subset adapted from FreeToken (Apache-2.0).

Source: FlashML-org/FreeToken, 555efd89447232a555e05d187256b76a51c1aaa0,
models/blocks.py GatedMLP, models/qwen3/attention.py merged QKV,
models/qwen3_vl/vision.py VisionAttention/VisionMLP.
Changes: nn.Module weights, single-device linears, Torch SiLU/SDPA, and the
Transformers vision-call signature. No server, scheduler or global context.
Tokenizer, checkpoint loading, language attention/KV cache and decoding remain
Transformers implementations; this is not the complete FreeToken engine.
"""
import types

import torch
from torch import nn
from torch.nn import functional as F


class GatedMLP(nn.Module):
    def __init__(self, source):
        super().__init__()
        self.gate_up_proj = nn.Linear(source.gate_proj.in_features,
                                      2 * source.gate_proj.out_features, bias=False,
                                      device='meta', dtype=source.gate_proj.weight.dtype)
        self.gate_up_proj.weight = nn.Parameter(torch.cat(
            (source.gate_proj.weight, source.up_proj.weight)), requires_grad=False)
        self.down_proj = source.down_proj

    def forward(self, x):
        gate_up = self.gate_up_proj(x)
        del x
        gate, up = gate_up.chunk(2, dim=-1)
        y = F.silu(gate) * up
        del gate, up, gate_up
        return self.down_proj(y)


def text_attention(self, hidden_states, position_embeddings, attention_mask,
                   past_key_values=None, **kwargs):
    # FreeToken's merged QKV, retaining Transformers' rotary, cache and SDPA
    # contract. No new kernels, JIT compiler, persistent cache or CUDA graphs.
    from transformers.models.qwen3_vl.modeling_qwen3_vl import (
        apply_rotary_pos_emb, ALL_ATTENTION_FUNCTIONS, eager_attention_forward)
    shape = hidden_states.shape[:-1]
    head_shape = (*shape, -1, self.head_dim)
    q, k, v = self.qkv_proj(hidden_states).split(self.qkv_sizes, dim=-1)
    q = self.q_norm(q.reshape(head_shape)).transpose(1, 2)
    k = self.k_norm(k.reshape(head_shape)).transpose(1, 2)
    v = v.reshape(head_shape).transpose(1, 2)
    q, k = apply_rotary_pos_emb(q, k, *position_embeddings)
    if past_key_values is not None:
        k, v = past_key_values.update(k, v, self.layer_idx)
    attention = ALL_ATTENTION_FUNCTIONS.get_interface(self.config._attn_implementation,
                                                     eager_attention_forward)
    out, weights = attention(self, q, k, v, attention_mask,
                            dropout=self.attention_dropout if self.training else 0.,
                            scaling=self.scaling, **kwargs)
    return self.o_proj(out.reshape(*shape, -1).contiguous()), weights


def merge_qkv(attention):
    projections = [attention.q_proj, attention.k_proj, attention.v_proj]
    attention.qkv_sizes = [p.out_features for p in projections]
    merged = nn.Linear(projections[0].in_features, sum(attention.qkv_sizes),
                       bias=projections[0].bias is not None, device='meta',
                       dtype=projections[0].weight.dtype)
    merged.weight = nn.Parameter(torch.cat([p.weight for p in projections]), requires_grad=False)
    if merged.bias is not None:
        merged.bias = nn.Parameter(torch.cat([p.bias for p in projections]), requires_grad=False)
    attention.qkv_proj = merged
    del attention.q_proj, attention.k_proj, attention.v_proj
    attention.forward = types.MethodType(text_attention, attention)


def vision_attention(self, hidden_states, cu_seqlens, position_embeddings=None, **kwargs):
    # Per-image bidirectional attention: unrelated references cannot attend
    # across image boundaries. Match FreeToken's fp32 rotary arithmetic.
    size = hidden_states.shape[0]
    q, k, v = self.qkv(hidden_states).view(size, 3, self.num_heads, self.head_dim).unbind(1)
    cos, sin = (x.unsqueeze(-2).float() for x in position_embeddings)
    def rotate(x):
        x = x.float()
        a, b = x.chunk(2, dim=-1)
        return x * cos + torch.cat((-b, a), dim=-1) * sin
    q, k = rotate(q).to(q.dtype), rotate(k).to(k.dtype)
    q, k, v = (x.transpose(0, 1).unsqueeze(0) for x in (q, k, v))
    lengths = torch.diff(cu_seqlens).tolist()
    outs = [F.scaled_dot_product_attention(qs, ks, vs)
            for qs, ks, vs in zip(torch.split(q, lengths, dim=2),
                                 torch.split(k, lengths, dim=2), torch.split(v, lengths, dim=2))]
    output = outs[0] if len(outs) == 1 else torch.cat(outs, dim=2)
    return self.proj(output[0].transpose(0, 1).reshape(size, -1))


def vision_mlp(self, x):
    if x.shape[0] <= 4096:
        return self.linear_fc2(F.gelu(self.linear_fc1(x), approximate='tanh'))
    out = torch.empty_like(x)
    for lo in range(0, x.shape[0], 4096):
        rows = x[lo:lo + 4096]
        out[lo:lo + 4096] = self.linear_fc2(F.gelu(self.linear_fc1(rows), approximate='tanh'))
    return out


def prepare(model, each_layer=None):
    """Transform before accelerate installs placement/offload hooks.

    each_layer(layer, merge) replaces the plain merge, so a caller can move the
    layer to the GPU first: merging there never holds a second copy in RAM.
    """
    if model.config.model_type != 'qwen3_vl' or model.config.text_config.hidden_act != 'silu':
        raise ValueError('Unsupported prompt model architecture')
    for layer in model.model.language_model.layers:
        (each_layer or (lambda layer, merge: merge(layer)))(layer, merge_layer)
    for block in model.model.visual.blocks:
        block.attn.forward = types.MethodType(vision_attention, block.attn)
        block.mlp.forward = types.MethodType(vision_mlp, block.mlp)
    return model


def merge_layer(layer):
    layer.mlp = GatedMLP(layer.mlp)
    merge_qkv(layer.self_attn)
