"""Run the H3 text encoder's language model in token blocks for very long inputs.

The native forward keeps FP32 activations, builds one dense T x T causal mask
and runs every projection and MLP over the whole sequence at once. Measured on
the 32B encoder, a 15 s reference video (15.7k tokens) needed 9.5 GiB beyond
the weights and two videos plus three images (34k tokens) 22 GiB, more than a
16 GB card has. The same math fits in a fraction of that:

- attention runs per block of queries, each against all earlier keys with the
  causal limit as a lower-right bias, so no mask is ever materialized; the
  memory-efficient kernel takes FP32 but not grouped K/V heads, so each K/V
  head is broadcast (a view, no copy) to its group of query heads;
- the position-wise MLP runs per block of tokens;
- DeepStack features are dropped after the layers that use them.

Only the language-model path the H3 encoder uses is replaced. Calls with KV
caches, attention masks or intermediate layer lists go to the native code.
"""
from contextlib import contextmanager
import types

import torch
import torch.nn.functional as F


def grouped_causal_attention(q, k, v, start):
    """Queries at positions start..start+len(q) over keys 0..start+len(q), causal, grouped K/V heads."""
    from torch.nn.attention.bias import causal_lower_right
    batch, heads, rows, width = q.shape
    groups = k.shape[1]
    per_group = heads // groups
    end = start + rows
    bias = causal_lower_right(rows, end)
    output = torch.empty((batch, heads, rows, width), device=q.device, dtype=q.dtype)
    for group in range(groups):
        first = group * per_group
        keys = k[:, group:group + 1, :end].expand(batch, per_group, end, width)
        values = v[:, group:group + 1, :end].expand(batch, per_group, end, width)
        output[:, first:first + per_group] = F.scaled_dot_product_attention(
            q[:, first:first + per_group], keys, values, attn_mask=bias)
    return output


def rope(x, freqs_cis):
    """The native apply_rope, for one tensor (queries or keys) at a time."""
    cos, sin, nsin = freqs_cis[0], freqs_cis[1], freqs_cis[2]
    embedded = x * cos
    half = embedded.shape[-1] // 2
    embedded[..., :half].addcmul_(x[..., half:], nsin)
    embedded[..., half:].addcmul_(x[..., :half], sin)
    return embedded.to(x.dtype)


def chunked_attention(attention, chunk):
    original = attention.forward

    def forward(self, hidden_states, attention_mask=None, freqs_cis=None, optimized_attention=None,
                past_key_value=None, sliding_window=None):
        batch, length, _ = hidden_states.shape
        if optimized_attention is not None:  # a native caller: its mask and caches apply
            return original(hidden_states, attention_mask=attention_mask, freqs_cis=freqs_cis,
                            optimized_attention=optimized_attention, past_key_value=past_key_value,
                            sliding_window=sliding_window)
        if (attention_mask is not None or past_key_value is not None or sliding_window is not None
                or freqs_cis is None or any(t.shape[-2] != length for t in freqs_cis[:3])):
            raise ValueError('Block-wise encoder attention supports causal attention without caches only')
        if self.merged_qkv:
            fused = self.qkv_proj(hidden_states)
            projected = dict(zip(('q', 'k', 'v'), fused.split((self.inner_size, self.kv_size, self.kv_size), dim=-1)))
        else:
            projected = {}
        keys = (projected.get('k') if projected else self.k_proj(hidden_states))
        values = (projected.get('v') if projected else self.v_proj(hidden_states))
        keys = keys.view(batch, length, self.num_kv_heads, self.head_dim).transpose(1, 2)
        values = values.view(batch, length, self.num_kv_heads, self.head_dim).transpose(1, 2)
        if self.k_norm is not None:
            keys = self.k_norm(keys)
        keys = rope(keys, freqs_cis)
        output = None
        for start in range(0, length, chunk):
            end = min(length, start + chunk)
            queries = (projected['q'][:, start:end] if projected else self.q_proj(hidden_states[:, start:end]))
            queries = queries.reshape(batch, end - start, self.num_heads, self.head_dim)
            queries = queries.transpose(1, 2)
            if self.q_norm is not None:
                queries = self.q_norm(queries)
            queries = rope(queries, tuple(t[..., start:end, :] for t in freqs_cis[:3]))
            value = grouped_causal_attention(queries, keys, values, start)
            del queries
            value = self.o_proj(value.transpose(1, 2).reshape(batch, end - start, self.num_heads * self.head_dim))
            if output is None:
                output = torch.empty((batch, length, value.shape[-1]), device=value.device, dtype=value.dtype)
            output[:, start:end] = value
            del value
        return output, None
    return types.MethodType(forward, attention)


def chunked_mlp(mlp, chunk):
    original = mlp.forward

    def forward(self, x):
        if x.shape[1] <= chunk:
            return original(x)
        output = torch.empty_like(x)
        for start in range(0, x.shape[1], chunk):
            output[:, start:start + chunk] = original(x[:, start:start + chunk])
        return output
    return types.MethodType(forward, mlp)


def language_model(model):
    """The Llama2_ module inside the native H3 text encoder."""
    for _, module in model.named_modules():
        if type(module).__name__ == 'Llama2_' and hasattr(module, 'layers'):
            return module
    raise ValueError('The native H3 text encoder has no Llama language model')


def chunked_forward(llama, original):
    def forward(self, x, attention_mask=None, embeds=None, num_tokens=None, intermediate_output=None,
                final_layer_norm_intermediate=True, dtype=None, position_ids=None, embeds_info=[],
                past_key_values=None, input_ids=None, deepstack_embeds=None, visual_pos_masks=None):
        if (attention_mask is not None or past_key_values is not None
                or isinstance(intermediate_output, (list, str))):
            return original(x, attention_mask=attention_mask, embeds=embeds, num_tokens=num_tokens,
                            intermediate_output=intermediate_output,
                            final_layer_norm_intermediate=final_layer_norm_intermediate, dtype=dtype,
                            position_ids=position_ids, embeds_info=embeds_info, past_key_values=past_key_values,
                            input_ids=input_ids, deepstack_embeds=deepstack_embeds, visual_pos_masks=visual_pos_masks)
        x = embeds if embeds is not None else self.embed_tokens(x, out_dtype=dtype)
        length = x.shape[1]
        if position_ids is None:
            position_ids = torch.arange(length, device=x.device).unsqueeze(0)
        freqs_cis = self.compute_freqs_cis(position_ids, x.device)
        if intermediate_output is not None and intermediate_output < 0:
            intermediate_output = len(self.layers) + intermediate_output
        deepstack = list(deepstack_embeds) if deepstack_embeds is not None else None
        deepstack_embeds = None
        intermediate = None
        for index, layer in enumerate(self.layers):
            # The block-wise attention installed on each layer needs no mask.
            x, _ = layer(x=x, attention_mask=None, freqs_cis=freqs_cis, optimized_attention=None,
                         past_key_value=None)
            # DeepStack: per-layer visual features at image positions (Qwen3-VL), as the native forward.
            if deepstack is not None and index < len(deepstack):
                x[visual_pos_masks] = x[visual_pos_masks] + deepstack[index].to(x)
                deepstack[index] = None  # used by this layer only
            if index == intermediate_output:
                intermediate = x.clone()
        if self.norm is not None:
            x = self.norm(x)
        if intermediate is not None and final_layer_norm_intermediate and self.norm is not None:
            intermediate = self.norm(intermediate)
        return x, intermediate
    return types.MethodType(forward, llama)


@contextmanager
def low_memory(model, chunk):
    """Within the block, the encoder's language model runs attention and MLPs in `chunk`-token blocks."""
    llama = language_model(model)
    patched = []

    def patch(module, replacement):
        patched.append((module, module.__dict__.get('forward')))
        module.forward = replacement

    try:
        patch(llama, chunked_forward(llama, llama.forward))
        for layer in llama.layers:
            patch(layer.self_attn, chunked_attention(layer.self_attn, chunk))
            patch(layer.mlp, chunked_mlp(layer.mlp, chunk))
        yield
    finally:
        for module, previous in reversed(patched):
            if previous is None:
                del module.forward  # back to the class's own forward
            else:
                module.forward = previous
