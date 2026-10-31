# Inference subset of ACE-Step 1.5 XL turbo (MIT; see licenses/ACE-Step-MIT.txt).
# Source: ACE-Step-1.5 checkpoints/acestep-v15-xl-turbo/modeling_acestep_v15_xl_turbo.py
# and acestep/models/common/configuration_acestep_v15.py, snapshot 2026-10-29.
# Upstream Apache/Qwen notices below are retained in addition to the MIT notice.
# Configuration copyright 2024 The Qwen team, Alibaba Group and HuggingFace Inc.
# Copyright 2025 The ACESTEO Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import copy
import math
from typing import Callable, Optional

import torch
import torch.nn.functional as F
from torch import nn
from .layers import linear, MusicMLP

# Transformers imports (sorted by submodule, then alphabetically)
from transformers.cache_utils import Cache, DynamicCache, EncoderDecoderCache
from transformers.modeling_flash_attention_utils import FlashAttentionKwargs
from transformers.modeling_outputs import BaseModelOutput
from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS, PreTrainedModel
from transformers.processing_utils import Unpack
from transformers.utils import can_return_tuple
from transformers.models.qwen3.modeling_qwen3 import (
    Qwen3MLP,
    Qwen3RMSNorm,
    Qwen3RotaryEmbedding,
    apply_rotary_pos_emb,
    eager_attention_forward,
)

from transformers.configuration_utils import PretrainedConfig, layer_type_validation
from transformers.modeling_rope_utils import rope_config_validation

class AceStepConfig(PretrainedConfig):
    model_type = 'acestep'
    keys_to_ignore_at_inference = ['past_key_values']
    def __init__(
        self,
        vocab_size=64003,
        fsq_dim=2048,
        fsq_input_levels=[8, 8, 8, 5, 5, 5],
        fsq_input_num_quantizers=1,
        hidden_size=2048,
        intermediate_size=6144,
        num_hidden_layers=24,
        num_attention_heads=16,
        num_key_value_heads=8,
        head_dim=128,
        hidden_act='silu',
        max_position_embeddings=32768,
        initializer_range=0.02,
        rms_norm_eps=1e-06,
        use_cache=True,
        tie_word_embeddings=True,
        rope_theta=1000000,
        rope_scaling=None,
        attention_bias=False,
        use_sliding_window=True,
        sliding_window=128,
        layer_types=None,
        attention_dropout=0.0,
        num_lyric_encoder_hidden_layers=8,
        audio_acoustic_hidden_dim=64,
        pool_window_size=5,
        text_hidden_dim=1024,
        in_channels=192,
        data_proportion=0.5,
        timestep_mu=-0.4,
        timestep_sigma=1.0,
        timbre_hidden_dim=64,
        num_timbre_encoder_hidden_layers=4,
        timbre_fix_frame=750,
        patch_size=2,
        num_attention_pooler_hidden_layers=2,
        num_audio_decoder_hidden_layers=24,
        lyric_alignment_layers_config=None,
        model_version='turbo',
        **kwargs,
    ):
        self.max_position_embeddings = max_position_embeddings
        self.hidden_size = hidden_size
        self.intermediate_size = intermediate_size
        self.num_hidden_layers = num_hidden_layers
        self.num_attention_heads = num_attention_heads
        self.use_sliding_window = use_sliding_window
        self.sliding_window = sliding_window if self.use_sliding_window else None
        self.text_hidden_dim = text_hidden_dim
        self.num_lyric_encoder_hidden_layers = num_lyric_encoder_hidden_layers
        self.patch_size = patch_size
        self.audio_acoustic_hidden_dim = audio_acoustic_hidden_dim
        self.pool_window_size = pool_window_size
        self.in_channels = in_channels
        self.data_proportion = data_proportion
        self.timestep_mu = timestep_mu
        self.timestep_sigma = timestep_sigma
        self.fsq_dim = fsq_dim
        self.fsq_input_levels = fsq_input_levels
        self.fsq_input_num_quantizers = fsq_input_num_quantizers
        self.timbre_hidden_dim = timbre_hidden_dim
        self.num_timbre_encoder_hidden_layers = num_timbre_encoder_hidden_layers
        self.timbre_fix_frame = timbre_fix_frame
        self.num_attention_pooler_hidden_layers = num_attention_pooler_hidden_layers
        self.num_audio_decoder_hidden_layers = num_audio_decoder_hidden_layers
        self.lyric_alignment_layers_config = lyric_alignment_layers_config
        self.vocab_size = vocab_size
        if num_key_value_heads is None:
            num_key_value_heads = num_attention_heads
        self.num_key_value_heads = num_key_value_heads
        self.head_dim = head_dim
        self.hidden_act = hidden_act
        self.initializer_range = initializer_range
        self.rms_norm_eps = rms_norm_eps
        self.use_cache = use_cache
        self.rope_theta = rope_theta
        self.rope_scaling = rope_scaling
        self.attention_bias = attention_bias
        self.attention_dropout = attention_dropout
        self.model_version = model_version
        if self.rope_scaling is not None and 'type' in self.rope_scaling:
            self.rope_scaling['rope_type'] = self.rope_scaling['type']
        rope_config_validation(self)
        self.layer_types = layer_types
        if self.layer_types is None:
            self.layer_types = ['sliding_attention' if bool((i + 1) % 2) else 'full_attention' for i in range(self.num_hidden_layers)]
        layer_type_validation(self.layer_types)
        super().__init__(tie_word_embeddings=tie_word_embeddings, **kwargs)

def create_4d_mask(
    seq_len: int,
    dtype: torch.dtype,
    device: torch.device,
    attention_mask: Optional[torch.Tensor] = None, # [Batch, Seq_Len]
    sliding_window: Optional[int] = None,
    is_sliding_window: bool = False,
    is_causal: bool = True,
) -> torch.Tensor:
    """
    General 4D Attention Mask generator compatible with CPU/Mac/SDPA and Eager mode.
    Supports use cases:
    1. Causal Full: is_causal=True, is_sliding_window=False (standard GPT)
    2. Causal Sliding: is_causal=True, is_sliding_window=True (Mistral/Qwen local window)
    3. Bidirectional Full: is_causal=False, is_sliding_window=False (BERT/Encoder)
    4. Bidirectional Sliding: is_causal=False, is_sliding_window=True (Longformer local)

    Returns:
        [Batch, 1, Seq_Len, Seq_Len] additive mask (0.0 for keep, -inf for mask)
    """
    # ------------------------------------------------------
    # 1. Construct basic geometry mask [Seq_Len, Seq_Len]
    # ------------------------------------------------------

    # Build index matrices
    # i (Query): [0, 1, ..., L-1]
    # j (Key):   [0, 1, ..., L-1]
    indices = torch.arange(seq_len, device=device)
    # diff = i - j
    diff = indices.unsqueeze(1) - indices.unsqueeze(0)

    # Initialize all True (all positions visible)
    valid_mask = torch.ones((seq_len, seq_len), device=device, dtype=torch.bool)

    # (A) Handle causality (Causal)
    if is_causal:
        # i >= j  =>  diff >= 0
        valid_mask = valid_mask & (diff >= 0)

    # (B) Handle sliding window
    if is_sliding_window and sliding_window is not None:
        if is_causal:
            # Causal sliding: only attend to past window steps
            # i - j <= window  =>  diff <= window
            # (diff >= 0 already handled above)
            valid_mask = valid_mask & (diff <= sliding_window)
        else:
            # Bidirectional sliding: attend past and future window steps
            # |i - j| <= window  =>  abs(diff) <= sliding_window
            valid_mask = valid_mask & (torch.abs(diff) <= sliding_window)

    # Expand dimensions to [1, 1, Seq_Len, Seq_Len] for broadcasting
    valid_mask = valid_mask.unsqueeze(0).unsqueeze(0)

    # ------------------------------------------------------
    # 2. Apply padding mask (Key Masking)
    # ------------------------------------------------------
    if attention_mask is not None:
        # attention_mask shape: [Batch, Seq_Len] (1=valid, 0=padding)
        # We want to mask out invalid keys (columns)
        # Expand shape: [Batch, 1, 1, Seq_Len]
        padding_mask_4d = attention_mask.view(attention_mask.shape[0], 1, 1, seq_len).to(torch.bool)

        # Broadcasting: Geometry Mask [1, 1, L, L] & Padding Mask [B, 1, 1, L]
        # Result shape: [B, 1, L, L]
        valid_mask = valid_mask & padding_mask_4d

    # ------------------------------------------------------
    # 3. Convert to additive mask
    # ------------------------------------------------------
    # Get the minimal value for current dtype
    min_dtype = torch.finfo(dtype).min

    # Create result tensor filled with -inf by default
    mask_tensor = torch.full(valid_mask.shape, min_dtype, dtype=dtype, device=device)

    # Set valid positions to 0.0
    mask_tensor.masked_fill_(valid_mask, 0.0)

    return mask_tensor


def pack_sequences(hidden1: torch.Tensor, hidden2: torch.Tensor, mask1: torch.Tensor, mask2: torch.Tensor):
    """
    Pack two sequences by concatenating and sorting them based on mask values.

    Args:
        hidden1: First hidden states tensor of shape [B, L1, D]
        hidden2: Second hidden states tensor of shape [B, L2, D]
        mask1: First mask tensor of shape [B, L1]
        mask2: Second mask tensor of shape [B, L2]

    Returns:
        Tuple of (packed_hidden_states, new_mask) where:
        - packed_hidden_states: Packed hidden states with valid tokens (mask=1) first, shape [B, L1+L2, D]
        - new_mask: New mask tensor indicating valid positions, shape [B, L1+L2]
    """
    # Step 1: Concatenate hidden states and masks along sequence dimension
    hidden_cat = torch.cat([hidden1, hidden2], dim=1)  # [B, L, D]
    mask_cat = torch.cat([mask1, mask2], dim=1)  # [B, L]

    B, L, D = hidden_cat.shape

    # Step 2: Sort indices so that mask values of 1 come before 0
    sort_idx = mask_cat.argsort(dim=1, descending=True, stable=True)  # [B, L]

    # Step 3: Reorder hidden states using sorted indices
    hidden_left = torch.gather(hidden_cat, 1, sort_idx.unsqueeze(-1).expand(B, L, D))

    # Step 4: Create new mask based on valid sequence lengths
    lengths = mask_cat.sum(dim=1)  # [B]
    new_mask = (torch.arange(L, dtype=torch.long, device=hidden_cat.device).unsqueeze(0) < lengths.unsqueeze(1))

    return hidden_left, new_mask


class TimestepEmbedding(nn.Module):
    """
    Timestep embedding module for diffusion models.

    Converts timestep values into high-dimensional embeddings using sinusoidal
    positional encoding, followed by MLP layers. Used for conditioning diffusion
    models on timestep information.
    """
    def __init__(
        self,
        in_channels: int,
        time_embed_dim: int,
        scale: float = 1000,
    ):
        super().__init__()

        self.linear_1 = linear(in_channels, time_embed_dim, bias=True)
        self.act1 = nn.SiLU()
        self.linear_2 = linear(time_embed_dim, time_embed_dim, bias=True)
        self.in_channels = in_channels

        self.act2 = nn.SiLU()
        self.time_proj = linear(time_embed_dim, time_embed_dim * 6)
        self.scale = scale

    def timestep_embedding(self, t, dim, max_period=10000):
        """
        Create sinusoidal timestep embeddings.

        Args:
            t: A 1-D tensor of N indices, one per batch element. These may be fractional.
            dim: The dimension of the output embeddings.
            max_period: Controls the minimum frequency of the embeddings.

        Returns:
            An (N, D) tensor of positional embeddings.
        """
        t = t * self.scale
        half = dim // 2
        freqs = torch.exp(
            -math.log(max_period) * torch.arange(start=0, end=half, dtype=torch.float32) / half
        ).to(device=t.device)
        args = t[:, None].float() * freqs[None]
        embedding = torch.cat([torch.cos(args), torch.sin(args)], dim=-1)
        if dim % 2:
            embedding = torch.cat([embedding, torch.zeros_like(embedding[:, :1])], dim=-1)
        return embedding

    def forward(self, t):
        t_freq = self.timestep_embedding(t, self.in_channels)
        temb = self.linear_1(t_freq.to(t.dtype))
        temb = self.act1(temb)
        temb = self.linear_2(temb)
        timestep_proj = self.time_proj(self.act2(temb)).unflatten(1, (6, -1))
        return temb, timestep_proj


class AceStepAttention(nn.Module):
    """
    Multi-headed attention module for AceStep model.

    Implements the attention mechanism from 'Attention Is All You Need' paper,
    with support for both self-attention and cross-attention modes. Uses RMSNorm
    for query and key normalization, and supports sliding window attention for
    efficient long-sequence processing.
    """

    def __init__(self, config: AceStepConfig, layer_idx: int, is_cross_attention: bool = False, is_causal: bool = False):
        super().__init__()
        self.config = config
        self.layer_idx = layer_idx
        self.head_dim = getattr(config, "head_dim", config.hidden_size // config.num_attention_heads)
        self.num_key_value_groups = config.num_attention_heads // config.num_key_value_heads
        self.scaling = self.head_dim**-0.5
        self.attention_dropout = config.attention_dropout
        if is_cross_attention:
            is_causal = False
        self.is_causal = is_causal
        self.is_cross_attention = is_cross_attention

        self.q_proj = linear(config.hidden_size, config.num_attention_heads * self.head_dim, bias=config.attention_bias)
        self.k_proj = linear(config.hidden_size, config.num_key_value_heads * self.head_dim, bias=config.attention_bias)
        self.v_proj = linear(config.hidden_size, config.num_key_value_heads * self.head_dim, bias=config.attention_bias)
        self.o_proj = linear(config.num_attention_heads * self.head_dim, config.hidden_size, bias=config.attention_bias)
        # Apply RMS normalization only on the head dimension (unlike OLMo)
        self.q_norm = Qwen3RMSNorm(self.head_dim, eps=config.rms_norm_eps)
        self.k_norm = Qwen3RMSNorm(self.head_dim, eps=config.rms_norm_eps)
        self.attention_type = config.layer_types[layer_idx]
        self.sliding_window = config.sliding_window if config.layer_types[layer_idx] == "sliding_attention" else None

    def forward(
        self,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor],
        past_key_value: Optional[Cache] = None,
        cache_position: Optional[torch.LongTensor] = None,
        encoder_hidden_states: Optional[torch.Tensor] = None,
        position_embeddings: tuple[torch.Tensor, torch.Tensor] = None,
        output_attentions: Optional[bool] = False,
        **kwargs: Unpack[FlashAttentionKwargs],
    ) -> tuple[torch.Tensor, Optional[torch.Tensor], Optional[tuple[torch.Tensor]]]:
        input_shape = hidden_states.shape[:-1]
        hidden_shape = (*input_shape, -1, self.head_dim)

        # Project and normalize query states
        query_states = self.q_norm(self.q_proj(hidden_states).view(hidden_shape)).transpose(1, 2)

        # Determine if this is cross-attention (requires encoder_hidden_states)
        is_cross_attention = self.is_cross_attention and encoder_hidden_states is not None

        # Cross-attention path: attend to encoder hidden states
        if is_cross_attention:
            encoder_hidden_shape = (*encoder_hidden_states.shape[:-1], -1, self.head_dim)
            if past_key_value is not None:
                is_updated = past_key_value.is_updated.get(self.layer_idx)
                # After the first generated token, we can reuse all key/value states from cache
                curr_past_key_value = past_key_value.cross_attention_cache

                # Conditions for calculating key and value states
                if not is_updated:
                    # Compute and cache K/V for the first time
                    key_states = self.k_norm(self.k_proj(encoder_hidden_states).view(encoder_hidden_shape)).transpose(1, 2)
                    value_states = self.v_proj(encoder_hidden_states).view(encoder_hidden_shape).transpose(1, 2)
                    # Update cache: save all key/value states to cache for fast auto-regressive generation
                    key_states, value_states = curr_past_key_value.update(key_states, value_states, self.layer_idx)
                    # Set flag that this layer's cross-attention cache is updated
                    past_key_value.is_updated[self.layer_idx] = True
                else:
                    # Reuse cached key/value states for subsequent tokens
                    key_states = curr_past_key_value.layers[self.layer_idx].keys
                    value_states = curr_past_key_value.layers[self.layer_idx].values
            else:
                # No cache used, compute K/V directly
                key_states = self.k_norm(self.k_proj(encoder_hidden_states).view(encoder_hidden_shape)).transpose(1, 2)
                value_states = self.v_proj(encoder_hidden_states).view(encoder_hidden_shape).transpose(1, 2)

        # Self-attention path: attend to the same sequence
        else:
            # Project and normalize key/value states for self-attention
            key_states = self.k_norm(self.k_proj(hidden_states).view(hidden_shape)).transpose(1, 2)
            value_states = self.v_proj(hidden_states).view(hidden_shape).transpose(1, 2)
            # Apply rotary position embeddings (RoPE) if provided
            if position_embeddings is not None:
                cos, sin = position_embeddings
                query_states, key_states = apply_rotary_pos_emb(query_states, key_states, cos, sin)

            # Update cache for auto-regressive generation
            if past_key_value is not None:
                # Sin and cos are specific to RoPE models; cache_position needed for the static cache
                cache_kwargs = {"sin": sin, "cos": cos, "cache_position": cache_position}
                key_states, value_states = past_key_value.update(key_states, value_states, self.layer_idx, cache_kwargs)

        attention_interface: Callable = eager_attention_forward
        if is_cross_attention and output_attentions:
            attention_interface: Callable = eager_attention_forward
        elif self.config._attn_implementation != "eager":
            attention_interface = ALL_ATTENTION_FUNCTIONS[self.config._attn_implementation]

        attn_output, attn_weights = attention_interface(
            self,
            query_states,
            key_states,
            value_states,
            attention_mask,
            dropout=0.0,
            scaling=self.scaling,
            sliding_window=self.sliding_window if not self.is_cross_attention else None,
            **kwargs,
        )

        attn_output = attn_output.reshape(*input_shape, -1).contiguous()
        attn_output = self.o_proj(attn_output)
        return attn_output, attn_weights


class AceStepEncoderLayer(nn.Module):
    """
    Encoder layer for AceStep model.

    Consists of self-attention and MLP (feed-forward) sub-layers with residual connections.
    """

    def __init__(self, config, layer_idx: int):
        super().__init__()
        self.hidden_size = config.hidden_size
        self.config = config
        self.layer_idx = layer_idx

        # Self-attention sub-layer
        self.self_attn = AceStepAttention(
            config=config,
            layer_idx=layer_idx,
            is_cross_attention=False,
            is_causal=False,
        )
        self.input_layernorm = Qwen3RMSNorm(config.hidden_size, eps=config.rms_norm_eps)
        self.post_attention_layernorm = Qwen3RMSNorm(config.hidden_size, eps=config.rms_norm_eps)

        # MLP (feed-forward) sub-layer
        self.mlp = MusicMLP(config)
        self.attention_type = config.layer_types[layer_idx]

    def forward(
        self,
        hidden_states: torch.Tensor,
        position_embeddings: tuple[torch.Tensor, torch.Tensor],
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.LongTensor] = None,
        output_attentions: Optional[bool] = False,
        **kwargs,
    ) -> tuple[
        torch.FloatTensor,
        Optional[tuple[torch.FloatTensor, torch.FloatTensor]],
    ]:
        # Self-attention with residual connection
        residual = hidden_states
        hidden_states = self.input_layernorm(hidden_states)
        hidden_states, self_attn_weights = self.self_attn(
            hidden_states=hidden_states,
            position_embeddings=position_embeddings,
            attention_mask=attention_mask,
            position_ids=position_ids,
            output_attentions=output_attentions,
            # Encoders don't use cache
            use_cache=False,
            past_key_value=None,
            **kwargs,
        )
        hidden_states = residual + hidden_states

        # MLP with residual connection
        residual = hidden_states
        hidden_states = self.post_attention_layernorm(hidden_states)
        hidden_states = self.mlp(hidden_states)
        hidden_states = residual + hidden_states

        outputs = (hidden_states,)

        if output_attentions:
            outputs += (self_attn_weights,)

        return outputs


class AceStepDiTLayer(nn.Module):
    """
    DiT (Diffusion Transformer) layer for AceStep model.

    Implements a transformer layer with three main components:
    1. Self-attention with adaptive layer norm (AdaLN)
    2. Cross-attention (optional) for conditioning on encoder outputs
    3. Feed-forward MLP with adaptive layer norm

    Uses scale-shift modulation from timestep embeddings for adaptive normalization.
    """
    def __init__(self, config: AceStepConfig, layer_idx: int, use_cross_attention: bool = True):
        super().__init__()

        # 1. Self-attention sub-layer with adaptive normalization
        self.self_attn_norm = Qwen3RMSNorm(config.hidden_size, eps=config.rms_norm_eps)
        self.self_attn = AceStepAttention(config=config, layer_idx=layer_idx)

        # 2. Cross-attention sub-layer (optional, for encoder conditioning)
        self.use_cross_attention = use_cross_attention
        if self.use_cross_attention:
            self.cross_attn_norm = Qwen3RMSNorm(config.hidden_size, eps=config.rms_norm_eps)
            self.cross_attn = AceStepAttention(config=config, layer_idx=layer_idx, is_cross_attention=True)

        # 3. Feed-forward MLP sub-layer with adaptive normalization
        self.mlp_norm = Qwen3RMSNorm(config.hidden_size, eps=config.rms_norm_eps)
        self.mlp = MusicMLP(config)

        # Scale-shift table for adaptive layer norm modulation (6 values: 3 for self-attn, 3 for MLP)
        self.scale_shift_table = nn.Parameter(torch.randn(1, 6, config.hidden_size) / config.hidden_size**0.5)
        self.attention_type = config.layer_types[layer_idx]

    def forward(
        self,
        hidden_states: torch.Tensor,
        position_embeddings: tuple[torch.Tensor, torch.Tensor],
        temb: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.LongTensor] = None,
        past_key_value: Optional[EncoderDecoderCache] = None,
        output_attentions: Optional[bool] = False,
        use_cache: Optional[bool] = False,
        cache_position: Optional[torch.LongTensor] = None,
        encoder_hidden_states: Optional[torch.Tensor] = None,
        encoder_attention_mask: Optional[torch.Tensor] = None,
        **kwargs,
    ) -> torch.Tensor:

        # Extract scale-shift parameters for adaptive layer norm from timestep embeddings
        # 6 values: (shift_msa, scale_msa, gate_msa, c_shift_msa, c_scale_msa, c_gate_msa)
        shift_msa, scale_msa, gate_msa, c_shift_msa, c_scale_msa, c_gate_msa = (
            self.scale_shift_table + temb
        ).chunk(6, dim=1)

        # Step 1: Self-attention with adaptive layer norm (AdaLN)
        # Apply adaptive normalization: norm(x) * (1 + scale) + shift
        norm_hidden_states = (self.self_attn_norm(hidden_states) * (1 + scale_msa) + shift_msa).type_as(hidden_states)
        attn_output, self_attn_weights = self.self_attn(
            hidden_states=norm_hidden_states,
            position_embeddings=position_embeddings,
            attention_mask=attention_mask,
            position_ids=position_ids,
            output_attentions=output_attentions,
            use_cache=False,
            past_key_value=None,
            **kwargs,
        )
        # Apply gated residual connection: x = x + attn_output * gate
        hidden_states = (hidden_states + attn_output * gate_msa).type_as(hidden_states)

        # Step 2: Cross-attention (if enabled) for conditioning on encoder outputs
        if self.use_cross_attention:
            norm_hidden_states = self.cross_attn_norm(hidden_states).type_as(hidden_states)
            attn_output, cross_attn_weights = self.cross_attn(
                hidden_states=norm_hidden_states,
                encoder_hidden_states=encoder_hidden_states,
                attention_mask=encoder_attention_mask,
                past_key_value=past_key_value,
                output_attentions=output_attentions,
                use_cache=use_cache,
                **kwargs,
            )
            # Standard residual connection for cross-attention
            hidden_states = hidden_states + attn_output

        # Step 3: Feed-forward (MLP) with adaptive layer norm
        # Apply adaptive normalization for MLP: norm(x) * (1 + scale) + shift
        norm_hidden_states = (self.mlp_norm(hidden_states) * (1 + c_scale_msa) + c_shift_msa).type_as(hidden_states)
        ff_output = self.mlp(norm_hidden_states)
        # Apply gated residual connection: x = x + mlp_output * gate
        hidden_states = (hidden_states + ff_output * c_gate_msa).type_as(hidden_states)

        outputs = (hidden_states,)
        if output_attentions:
            outputs += (self_attn_weights, cross_attn_weights)

        return outputs


class AceStepPreTrainedModel(PreTrainedModel):
    config_class = AceStepConfig
    base_model_prefix = "model"
    _no_split_modules = ["AceStepEncoderLayer", "AceStepDiTLayer"]
    _skip_keys_device_placement = ["past_key_values"]
    _supports_flash_attn_3 = True
    _supports_flash_attn_2 = True
    _supports_sdpa = True
    _supports_flex_attn = True
    _supports_cache_class = True
    _supports_quantized_cache = True
    _supports_static_cache = True
    _supports_attention_backend = True

    def _init_weights(self, module):
        """
        Initialize weights for different module types.

        TODO: Support separate initialization for encoders and decoders.
        """
        std = self.config.initializer_range
        if isinstance(module, nn.Linear):
            module.weight.data.normal_(mean=0.0, std=std)
            if module.bias is not None:
                module.bias.data.zero_()
        elif isinstance(module, nn.Embedding):
            module.weight.data.normal_(mean=0.0, std=std)
            if module.padding_idx is not None:
                module.weight.data[module.padding_idx].zero_()
        elif isinstance(module, Qwen3RMSNorm):
            module.weight.data.fill_(1.0)


class AceStepLyricEncoder(AceStepPreTrainedModel):
    """
    Encoder for processing lyric text embeddings.

    Encodes lyric text hidden states using a transformer encoder architecture
    with bidirectional attention. Projects text embeddings to model hidden size
    and processes them through multiple encoder layers.
    """
    def __init__(self, config):
        super().__init__(config)

        # Project text embeddings to model hidden size
        self.embed_tokens = linear(config.text_hidden_dim, config.hidden_size)
        self.norm = Qwen3RMSNorm(config.hidden_size, eps=config.rms_norm_eps)
        self.rotary_emb = Qwen3RotaryEmbedding(config=config)

        # Stack of encoder layers
        self.layers = nn.ModuleList(
            [AceStepEncoderLayer(config, layer_idx) for layer_idx in range(config.num_lyric_encoder_hidden_layers)]
        )

        # Initialize weights and apply final processing
        self.post_init()

    @can_return_tuple
    def forward(
        self,
        input_ids: Optional[torch.LongTensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.LongTensor] = None,
        inputs_embeds: Optional[torch.FloatTensor] = None,
        output_attentions: Optional[bool] = None,
        output_hidden_states: Optional[bool] = None,
        **flash_attn_kwargs: Unpack[FlashAttentionKwargs],
    ) -> BaseModelOutput:
        output_attentions = output_attentions if output_attentions is not None else self.config.output_attentions
        output_hidden_states = (
            output_hidden_states if output_hidden_states is not None else self.config.output_hidden_states
        )

        assert input_ids is None, "Only `input_ids` is supported for the lyric encoder."
        assert attention_mask is not None, "Attention mask must be provided for the lyric encoder."
        assert inputs_embeds is not None, "Inputs embeddings must be provided for the lyric encoder."

        # Project input embeddings: N x T x text_hidden_dim -> N x T x hidden_size
        inputs_embeds = self.embed_tokens(inputs_embeds)
        # Cache position: only used for mask construction (not for actual caching)
        cache_position = torch.arange(0, inputs_embeds.shape[1], device=inputs_embeds.device)

        # Positional IDs
        if position_ids is None:
            position_ids = cache_position.unsqueeze(0)

        # Attention masks
        seq_len = inputs_embeds.shape[1]
        dtype = inputs_embeds.dtype
        device = inputs_embeds.device

        # 判断是否使用 Flash Attention 2
        is_flash_attn = (self.config._attn_implementation == "flash_attention_2")

        # 初始化 Mask 变量
        full_attn_mask = None
        sliding_attn_mask = None

        if is_flash_attn:
            # -------------------------------------------------------
            # 场景 A: Flash Attention 模式
            # -------------------------------------------------------
            # FA 不需要 4D Mask。
            # 如果有 padding mask (attention_mask [B, L])，直接传给它即可。
            # 如果没有 padding mask，传 None。
            # 滑动窗口逻辑由 Layer 内部传给 FA kernel 的 sliding_window 参数控制。
            full_attn_mask = attention_mask

            # 这里的逻辑是：如果配置启用了滑动窗口，FA 模式下我们也只需要传基础的 padding mask
            # Layer 会自己决定是否调用带 sliding window 的 kernel
            sliding_attn_mask = attention_mask if self.config.use_sliding_window else None

        else:
            # -------------------------------------------------------
            # 场景 B: CPU / Mac / SDPA (Eager 模式)
            # -------------------------------------------------------
            # 必须手动生成 4D Mask [B, 1, L, L]

            # 1. Full Attention (Bidirectional, Global)
            # 对应原来的 create_causal_mask + bidirectional
            full_attn_mask = create_4d_mask(
                seq_len=seq_len,
                dtype=dtype,
                device=device,
                attention_mask=attention_mask,     # [B, L]
                sliding_window=None,
                is_sliding_window=False,
                is_causal=False                    # <--- 关键：双向注意力
            )

            # 2. Sliding Attention (Bidirectional, Local)
            # 对应原来的 create_sliding_window... + bidirectional
            if self.config.use_sliding_window:
                sliding_attn_mask = create_4d_mask(
                    seq_len=seq_len,
                    dtype=dtype,
                    device=device,
                    attention_mask=attention_mask, # [B, L]
                    sliding_window=self.config.sliding_window,
                    is_sliding_window=True,        # <--- 开启滑动窗口
                    is_causal=False                # <--- 关键：双向注意力
                )

        # 构建 Mapping
        self_attn_mask_mapping = {
            "full_attention": full_attn_mask,
            "sliding_attention": sliding_attn_mask,
        }

        # Initialize hidden states with input embeddings
        hidden_states = inputs_embeds

        # Create position embeddings to be shared across all layers
        position_embeddings = self.rotary_emb(hidden_states, position_ids)

        # Pass through transformer layers
        all_hidden_states = () if output_hidden_states else None
        all_self_attns = () if output_attentions else None

        for layer_module in self.layers[: self.config.num_hidden_layers]:
            if output_hidden_states:
                all_hidden_states += (hidden_states,)

            layer_outputs = layer_module(
                hidden_states,
                position_embeddings,
                self_attn_mask_mapping[layer_module.attention_type],
                position_ids,
                output_attentions,
                **flash_attn_kwargs,
            )

            hidden_states = layer_outputs[0]

            if output_attentions:
                all_self_attns += (layer_outputs[1],)

        hidden_states = self.norm(hidden_states)

        if output_hidden_states:
            all_hidden_states += (hidden_states,)

        return BaseModelOutput(
            last_hidden_state=hidden_states,
            hidden_states=all_hidden_states,
            attentions=all_self_attns,
        )


class AceStepTimbreEncoder(AceStepPreTrainedModel):
    """
    Encoder for extracting timbre embeddings from reference audio.

    Processes packed reference audio acoustic features to extract timbre
    representations. Uses a special token (CLS-like) to aggregate information
    from the entire reference audio sequence. Outputs are unpacked back to
    batch format for use in conditioning.
    """
    def __init__(self, config):
        super().__init__(config)

        # Project acoustic features to model hidden size
        self.embed_tokens = linear(config.timbre_hidden_dim, config.hidden_size)
        self.norm = Qwen3RMSNorm(config.hidden_size, eps=config.rms_norm_eps)
        self.rotary_emb = Qwen3RotaryEmbedding(config=config)
        # Special token for aggregating timbre information (prepended to sequence)
        self.special_token = nn.Parameter(torch.randn(1, 1, config.hidden_size))
        self.layers = nn.ModuleList(
            [AceStepEncoderLayer(config, layer_idx) for layer_idx in range(config.num_timbre_encoder_hidden_layers)]
        )

        # Initialize weights and apply final processing
        self.post_init()

    def unpack_timbre_embeddings(self, timbre_embs_packed, refer_audio_order_mask):
        """
        Unpack packed timbre embeddings into batch format.

        Args:
            timbre_embs_packed: Packed timbre embeddings of shape [N, d]
            refer_audio_order_mask: Order mask indicating batch assignment for each packed embedding

        Returns:
            Tuple of (unpacked_embeddings, mask):
            - unpacked_embeddings: Unpacked embeddings of shape [B, max_count, d]
            - new_mask: Mask indicating valid positions, shape [B, max_count]
        """
        N, d = timbre_embs_packed.shape
        device = timbre_embs_packed.device
        dtype = timbre_embs_packed.dtype

        # Get batch size
        B = int(refer_audio_order_mask.max().item() + 1)

        # Calculate element count and positions for each batch
        counts = torch.bincount(refer_audio_order_mask, minlength=B)
        max_count = counts.max().item()

        # Calculate positions within batch
        sorted_indices = torch.argsort(refer_audio_order_mask * N + torch.arange(N, device=device), stable=True)
        sorted_batch_ids = refer_audio_order_mask[sorted_indices]

        positions = torch.arange(N, device=device)
        batch_starts = torch.cat([torch.tensor([0], device=device),
                                torch.cumsum(counts, dim=0)[:-1]])
        positions_in_sorted = positions - batch_starts[sorted_batch_ids]

        inverse_indices = torch.empty_like(sorted_indices)
        inverse_indices[sorted_indices] = torch.arange(N, device=device)
        positions_in_batch = positions_in_sorted[inverse_indices]

        # Use one-hot encoding and matrix multiplication (gradient-friendly approach)
        # Create one-hot encoding
        indices_2d = refer_audio_order_mask * max_count + positions_in_batch  # (N,)
        one_hot = F.one_hot(indices_2d, num_classes=B * max_count).to(dtype)  # (N, B*max_count)

        # Rearrange using matrix multiplication
        timbre_embs_flat = one_hot.t() @ timbre_embs_packed  # (B*max_count, d)
        timbre_embs_unpack = timbre_embs_flat.reshape(B, max_count, d)

        # Create mask indicating valid positions
        mask_flat = (one_hot.sum(dim=0) > 0).long()  # (B*max_count,)
        new_mask = mask_flat.reshape(B, max_count)

        return timbre_embs_unpack, new_mask

    @can_return_tuple
    def forward(
        self,
        refer_audio_acoustic_hidden_states_packed: Optional[torch.FloatTensor] = None,
        refer_audio_order_mask: Optional[torch.LongTensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        **flash_attn_kwargs: Unpack[FlashAttentionKwargs],
    ) -> BaseModelOutput:
        inputs_embeds = refer_audio_acoustic_hidden_states_packed
        # Project embeddings: N x T x timbre_hidden_dim -> N x T x hidden_size
        inputs_embeds = self.embed_tokens(inputs_embeds)
        # XL prepends the learned reference CLS token before its encoder.
        inputs_embeds = torch.cat([self.special_token.expand(inputs_embeds.shape[0], 1, -1), inputs_embeds], dim=1)
        if attention_mask is not None:
            attention_mask = torch.cat([torch.ones(attention_mask.shape[0], 1, device=attention_mask.device, dtype=attention_mask.dtype), attention_mask], dim=1)
        # Prepend special token for timbre aggregation (CLS-like token)
        # inputs_embeds = torch.cat([self.special_token.expand(inputs_embeds.shape[0], 1, -1), inputs_embeds], dim=1)
        # Cache position: only used for mask construction (not for actual caching)
        cache_position = torch.arange(0, inputs_embeds.shape[1], device=inputs_embeds.device)
        # Positional IDs
        position_ids = cache_position.unsqueeze(0)

        seq_len = inputs_embeds.shape[1]
        dtype = inputs_embeds.dtype
        device = inputs_embeds.device

        # 判断是否使用 Flash Attention 2
        is_flash_attn = (self.config._attn_implementation == "flash_attention_2")

        # 初始化 Mask 变量
        full_attn_mask = None
        sliding_attn_mask = None

        if is_flash_attn:
            # -------------------------------------------------------
            # 场景 A: Flash Attention 模式
            # -------------------------------------------------------
            # FA 不需要 4D Mask。
            # 如果有 padding mask (attention_mask [B, L])，直接传给它即可。
            # 如果没有 padding mask，传 None。
            # 滑动窗口逻辑由 Layer 内部传给 FA kernel 的 sliding_window 参数控制。
            full_attn_mask = attention_mask

            # 这里的逻辑是：如果配置启用了滑动窗口，FA 模式下我们也只需要传基础的 padding mask
            # Layer 会自己决定是否调用带 sliding window 的 kernel
            sliding_attn_mask = attention_mask if self.config.use_sliding_window else None

        else:
            # -------------------------------------------------------
            # 场景 B: CPU / Mac / SDPA (Eager 模式)
            # -------------------------------------------------------
            # 必须手动生成 4D Mask [B, 1, L, L]

            # 1. Full Attention (Bidirectional, Global)
            # 对应原来的 create_causal_mask + bidirectional
            full_attn_mask = create_4d_mask(
                seq_len=seq_len,
                dtype=dtype,
                device=device,
                attention_mask=attention_mask,     # [B, L]
                sliding_window=None,
                is_sliding_window=False,
                is_causal=False                    # <--- 关键：双向注意力
            )

            # 2. Sliding Attention (Bidirectional, Local)
            # 对应原来的 create_sliding_window... + bidirectional
            if self.config.use_sliding_window:
                sliding_attn_mask = create_4d_mask(
                    seq_len=seq_len,
                    dtype=dtype,
                    device=device,
                    attention_mask=attention_mask, # [B, L]
                    sliding_window=self.config.sliding_window,
                    is_sliding_window=True,        # <--- 开启滑动窗口
                    is_causal=False                # <--- 关键：双向注意力
                )

        # 构建 Mapping
        self_attn_mask_mapping = {
            "full_attention": full_attn_mask,
            "sliding_attention": sliding_attn_mask,
        }

        # Initialize hidden states
        hidden_states = inputs_embeds

        # Create position embeddings to be shared across all layers
        position_embeddings = self.rotary_emb(hidden_states, position_ids)

        # Pass through transformer layers
        for layer_module in self.layers[: self.config.num_hidden_layers]:
            layer_outputs = layer_module(
                hidden_states,
                position_embeddings,
                self_attn_mask_mapping[layer_module.attention_type],
                position_ids,
                **flash_attn_kwargs,
            )

            hidden_states = layer_outputs[0]

        hidden_states = self.norm(hidden_states)
        # Extract special token output (first position) as timbre embedding: N x T x D -> N x D
        hidden_states = hidden_states[:, 0, :]
        # Unpack packed embeddings back to batch format
        timbre_embs_unpack, timbre_embs_mask = self.unpack_timbre_embeddings(hidden_states, refer_audio_order_mask)
        return timbre_embs_unpack, timbre_embs_mask


class Lambda(nn.Module):
    """
    Wrapper module for arbitrary lambda functions.

    Allows using lambda functions in nn.Sequential by wrapping them in a Module.
    Useful for simple transformations like transpose operations.
    """
    def __init__(self, func):
        super().__init__()
        self.func = func

    def forward(self, x):
        return self.func(x)


class AceStepDiTModel(AceStepPreTrainedModel):
    """
    DiT (Diffusion Transformer) model for AceStep.

    Main diffusion model that generates audio latents conditioned on text, lyrics,
    and timbre. Uses patch-based processing with transformer layers, timestep
    conditioning, and cross-attention to encoder outputs.
    """
    def __init__(self, config: AceStepConfig):
        super().__init__(config)
        # Rotary position embeddings for transformer layers
        self.rotary_emb = Qwen3RotaryEmbedding(config)
        # Stack of DiT transformer layers
        self.layers = nn.ModuleList(
            [AceStepDiTLayer(config, layer_idx) for layer_idx in range(config.num_hidden_layers)]
        )

        in_channels = config.in_channels
        inner_dim = config.hidden_size
        patch_size = config.patch_size
        self.patch_size = patch_size

        # Input projection: patch embedding using 1D convolution
        # Converts sequence into patches for efficient processing
        self.proj_in = nn.Sequential(
            Lambda(lambda x: x.transpose(1, 2)),  # [B, T, C] -> [B, C, T]
            nn.Conv1d(
                in_channels=in_channels,
                out_channels=inner_dim,
                kernel_size=patch_size,
                stride=patch_size,
                padding=0,
            ),
            Lambda(lambda x: x.transpose(1, 2)),  # [B, C, T//patch_size] -> [B, T//patch_size, C]
        )

        # Timestep embeddings for diffusion conditioning
        # Two embeddings: one for timestep t, one for timestep difference (t - r)
        self.time_embed = TimestepEmbedding(in_channels=256, time_embed_dim=inner_dim)
        self.time_embed_r = TimestepEmbedding(in_channels=256, time_embed_dim=inner_dim)

        # Project encoder hidden states to model dimension
        condition_dim = getattr(config, "encoder_hidden_size", None) or config.hidden_size
        self.condition_embedder = linear(condition_dim, inner_dim, bias=True)

        # Output normalization and projection
        # Adaptive layer norm with scale-shift modulation, then de-patchify
        self.norm_out = Qwen3RMSNorm(inner_dim, eps=config.rms_norm_eps)
        self.proj_out = nn.Sequential(
            Lambda(lambda x: x.transpose(1, 2)),  # [B, T//patch_size, inner_dim] -> [B, inner_dim, T//patch_size]
            nn.ConvTranspose1d(
                in_channels=inner_dim,
                out_channels=config.audio_acoustic_hidden_dim,
                kernel_size=patch_size,
                stride=patch_size,
                padding=0,
            ),
            Lambda(lambda x: x.transpose(1, 2)),  # [B, out_channels, T] -> [B, T, out_channels]
        )
        # Scale-shift table for adaptive output normalization (2 values: shift, scale)
        self.scale_shift_table = nn.Parameter(torch.randn(1, 2, inner_dim) / inner_dim**0.5)


    def forward(
        self,
        hidden_states: torch.Tensor,
        timestep: torch.Tensor,
        timestep_r: torch.Tensor,
        attention_mask: torch.Tensor,
        encoder_hidden_states: torch.Tensor,
        encoder_attention_mask: torch.Tensor,
        context_latents: torch.Tensor,
        use_cache: Optional[bool] = None,
        past_key_values: Optional[EncoderDecoderCache] = None,
        cache_position: Optional[torch.LongTensor] = None,
        position_ids: Optional[torch.LongTensor] = None,
        output_attentions: Optional[bool] = False,
        return_hidden_states: int = None,
        custom_layers_config: Optional[dict] = None,
        enable_early_exit: bool = False,
        **flash_attn_kwargs: Unpack[FlashAttentionKwargs],
    ):

        use_cache = use_cache if use_cache is not None else self.config.use_cache

        if use_cache and past_key_values is None:
            past_key_values = EncoderDecoderCache(DynamicCache(), DynamicCache())

        # Compute timestep embeddings for diffusion conditioning
        # Two embeddings: one for timestep t, one for timestep difference (t - r)
        temb_t, timestep_proj_t = self.time_embed(timestep)
        temb_r, timestep_proj_r = self.time_embed_r(timestep - timestep_r)
        # Combine embeddings
        temb = temb_t + temb_r
        timestep_proj = timestep_proj_t + timestep_proj_r

        # Concatenate context latents (source latents + chunk masks) with hidden states
        hidden_states = torch.cat([context_latents, hidden_states], dim=-1)
        # Record original sequence length for later restoration after padding
        original_seq_len = hidden_states.shape[1]
        # Apply padding if sequence length is not divisible by patch_size
        # This ensures proper patch extraction
        pad_length = 0
        if hidden_states.shape[1] % self.patch_size != 0:
            pad_length = self.patch_size - (hidden_states.shape[1] % self.patch_size)
            hidden_states = F.pad(hidden_states, (0, 0, 0, pad_length), mode='constant', value=0)

        # Project input to patches and project encoder states
        hidden_states = self.proj_in(hidden_states)
        encoder_hidden_states = self.condition_embedder(encoder_hidden_states)

        # Cache positions
        if cache_position is None:
            past_seen_tokens = past_key_values.get_seq_length() if past_key_values is not None else 0
            cache_position = torch.arange(
                past_seen_tokens, past_seen_tokens + hidden_states.shape[1], device=hidden_states.device
            )

        # Position IDs
        if position_ids is None:
            position_ids = cache_position.unsqueeze(0)


        seq_len = hidden_states.shape[1]
        encoder_seq_len = encoder_hidden_states.shape[1]
        dtype = hidden_states.dtype
        device = hidden_states.device

        # 判断是否使用 Flash Attention 2
        is_flash_attn = (self.config._attn_implementation == "flash_attention_2")

        # 初始化 Mask 变量
        full_attn_mask = None
        sliding_attn_mask = None
        encoder_attention_mask = None
        attention_mask = None
        if is_flash_attn:
            # -------------------------------------------------------
            # 场景 A: Flash Attention 模式
            # -------------------------------------------------------
            # FA 不需要 4D Mask。
            # 如果有 padding mask (attention_mask [B, L])，直接传给它即可。
            # 如果没有 padding mask，传 None。
            # 滑动窗口逻辑由 Layer 内部传给 FA kernel 的 sliding_window 参数控制。
            full_attn_mask = attention_mask

            # 这里的逻辑是：如果配置启用了滑动窗口，FA 模式下我们也只需要传基础的 padding mask
            # Layer 会自己决定是否调用带 sliding window 的 kernel
            sliding_attn_mask = attention_mask if self.config.use_sliding_window else None

        else:
            # -------------------------------------------------------
            # 场景 B: CPU / Mac / SDPA (Eager 模式)
            # -------------------------------------------------------
            # 必须手动生成 4D Mask [B, 1, L, L]

            # 1. Full Attention (Bidirectional, Global)
            # 对应原来的 create_causal_mask + bidirectional
            full_attn_mask = create_4d_mask(
                seq_len=seq_len,
                dtype=dtype,
                device=device,
                attention_mask=attention_mask,     # [B, L]
                sliding_window=None,
                is_sliding_window=False,
                is_causal=False                    # <--- 关键：双向注意力
            )
            max_len = max(seq_len, encoder_seq_len)

            encoder_attention_mask = create_4d_mask(
                seq_len=max_len,
                dtype=dtype,
                device=device,
                attention_mask=attention_mask,     # [B, L]
                sliding_window=None,
                is_sliding_window=False,
                is_causal=False                    # <--- 关键：双向注意力
            )
            encoder_attention_mask = encoder_attention_mask[:, :, :seq_len, :encoder_seq_len]
            # 2. Sliding Attention (Bidirectional, Local)
            # 对应原来的 create_sliding_window... + bidirectional
            if self.config.use_sliding_window:
                sliding_attn_mask = create_4d_mask(
                    seq_len=seq_len,
                    dtype=dtype,
                    device=device,
                    attention_mask=attention_mask, # [B, L]
                    sliding_window=self.config.sliding_window,
                    is_sliding_window=True,        # <--- 开启滑动窗口
                    is_causal=False                # <--- 关键：双向注意力
                )

        # 构建 Mapping
        self_attn_mask_mapping = {
            "full_attention": full_attn_mask,
            "sliding_attention": sliding_attn_mask,
            "encoder_attention_mask": encoder_attention_mask,
        }

        # Create position embeddings to be shared across all decoder layers
        position_embeddings = self.rotary_emb(hidden_states, position_ids)
        all_cross_attentions = () if output_attentions else None

        # Handle early exit for custom layer configurations
        max_needed_layer = float('inf')
        if custom_layers_config is not None and enable_early_exit:
            max_needed_layer = max(custom_layers_config.keys())
            # Force output_attentions to True when early exit is enabled for attention extraction
            output_attentions = True
            if all_cross_attentions is None:
                all_cross_attentions = ()

        # Process through transformer layers
        for index_block, layer_module in enumerate(self.layers):

            layer_outputs = layer_module(
                hidden_states,
                position_embeddings,
                timestep_proj,
                self_attn_mask_mapping[layer_module.attention_type],
                position_ids,
                past_key_values,
                output_attentions,
                use_cache,
                cache_position,
                encoder_hidden_states,
                self_attn_mask_mapping["encoder_attention_mask"],
                **flash_attn_kwargs,
            )
            hidden_states = layer_outputs[0]

            if output_attentions and self.layers[index_block].use_cross_attention:
                # layer_outputs structure: (hidden_states, self_attn_weights, cross_attn_weights)
                # Extract the last element which is cross_attn_weights
                if len(layer_outputs) >= 3:
                    all_cross_attentions += (layer_outputs[2],)

        if return_hidden_states:
            return hidden_states

        # Extract scale-shift parameters for adaptive output normalization
        shift, scale = (self.scale_shift_table + temb.unsqueeze(1)).chunk(2, dim=1)
        shift = shift.to(hidden_states.device)
        scale = scale.to(hidden_states.device)

        # Apply adaptive layer norm: norm(x) * (1 + scale) + shift
        hidden_states = (self.norm_out(hidden_states) * (1 + scale) + shift).type_as(hidden_states)
        # Project output: de-patchify back to original sequence format
        hidden_states = self.proj_out(hidden_states)

        # Crop back to original sequence length to ensure exact length match (remove padding)
        hidden_states = hidden_states[:, :original_seq_len, :]

        outputs = (hidden_states, past_key_values)

        if output_attentions:
            outputs += (all_cross_attentions,)
        return outputs


class AceStepConditionEncoder(AceStepPreTrainedModel):
    """
    Condition encoder for AceStep model.

    Encodes multiple conditioning inputs (text, lyrics, timbre) and packs them
    into a single sequence for cross-attention in the diffusion model. Handles
    projection, encoding, and sequence packing.
    """
    def __init__(self, config: AceStepConfig):
        super().__init__(config)
        self.config = config
        # Project text embeddings to model hidden size
        self.text_projector = linear(config.text_hidden_dim, config.hidden_size, bias=False)
        # Encoder for lyric text
        self.lyric_encoder = AceStepLyricEncoder(config)
        # Encoder for timbre from reference audio
        self.timbre_encoder = AceStepTimbreEncoder(config)

    def forward(
        self,
        # Text inputs
        text_hidden_states: Optional[torch.FloatTensor] = None,
        text_attention_mask: Optional[torch.Tensor] = None,
        # Lyric inputs
        lyric_hidden_states: Optional[torch.LongTensor] = None,
        lyric_attention_mask: Optional[torch.Tensor] = None,
        # Reference audio for timbre
        refer_audio_acoustic_hidden_states_packed: Optional[torch.Tensor] = None,
        refer_audio_order_mask: Optional[torch.LongTensor] = None,
    ):
        # Project and encode text
        text_hidden_states = self.text_projector(text_hidden_states)
        # Encode lyrics
        lyric_encoder_outputs = self.lyric_encoder(
            inputs_embeds=lyric_hidden_states,
            attention_mask=lyric_attention_mask,
        )
        lyric_hidden_states = lyric_encoder_outputs.last_hidden_state
        # Encode timbre from reference audio
        timbre_embs_unpack, timbre_embs_mask = self.timbre_encoder(refer_audio_acoustic_hidden_states_packed, refer_audio_order_mask)

        # Pack sequences: combine lyrics and timbre, then add text
        # This creates a single sequence with all conditioning information
        encoder_hidden_states, encoder_attention_mask = pack_sequences(lyric_hidden_states, timbre_embs_unpack, lyric_attention_mask, timbre_embs_mask)
        encoder_hidden_states, encoder_attention_mask = pack_sequences(encoder_hidden_states, text_hidden_states, encoder_attention_mask, text_attention_mask)
        return encoder_hidden_states, encoder_attention_mask



# Inverse FSQ arithmetic: vector-quantize-pytorch (MIT, Copyright Phil Wang).
# See licenses/Vector-Quantize-PyTorch-MIT.txt. No package import is required.
class AudioCodeQuantizer(nn.Module):
    """ResidualFSQ inverse for one quantizer, without encoder-only packages.

    Match vector_quantize_pytorch ResidualFSQ.get_output_from_indices:
    build the symmetry-preserving codebook in CPU float32, then cast the
    buffer along with weights, index, multiply scales, sum and project.
    """
    def __init__(self, config):
        super().__init__()
        if config.fsq_input_num_quantizers != 1:
            raise ValueError("only the published single-quantizer checkpoint is supported")
        levels = config.fsq_input_levels
        with torch.device("cpu"):
            level = torch.tensor(levels, dtype=torch.int32)
            basis = torch.cumprod(torch.tensor([1] + levels[:-1]), 0, dtype=torch.int32)
            ids = torch.arange(math.prod(levels)).unsqueeze(-1)
            codebook = ((ids // basis) % level) * (2. / (level - 1).float()) - 1.
            scales = torch.tensor(levels).float() ** 0
        self.register_buffer("codebook", codebook, persistent=False)
        self.register_buffer("scales", scales, persistent=False)
        self.project_out = linear(len(levels), config.fsq_dim)

    def get_output_from_indices(self, indices):
        codes = self.codebook[indices[..., 0]].unsqueeze(0)
        codes = codes * self.scales.reshape(1, 1, 1, -1)
        return self.project_out(codes.sum(dim=0))


class AudioTokenDetokenizer(AceStepPreTrainedModel):
    """
    Audio token detokenizer module.

    Converts quantized audio tokens back to continuous acoustic representations.
    Expands each token into multiple patches using special tokens, processes them
    through encoder layers, and projects to acoustic hidden dimension.
    """
    def __init__(self, config):
        super().__init__(config)
        self.config = config
        self.embed_tokens = linear(config.hidden_size, config.hidden_size)
        self.norm = Qwen3RMSNorm(config.hidden_size, eps=config.rms_norm_eps)
        self.rotary_emb = Qwen3RotaryEmbedding(config=config)
        self.gradient_checkpointing = False
        # Special tokens for expanding each quantized token into patches
        self.special_tokens = nn.Parameter(torch.randn(1, config.pool_window_size, config.hidden_size) * 0.02)
        self.layers = nn.ModuleList(
            [AceStepEncoderLayer(config, layer_idx) for layer_idx in range(config.num_attention_pooler_hidden_layers)]
        )
        # Project back to acoustic hidden dimension
        self.proj_out = linear(config.hidden_size, config.audio_acoustic_hidden_dim)

        # Initialize weights and apply final processing
        self.post_init()

    @can_return_tuple
    def forward(self,
        x,
        attention_mask: Optional[torch.Tensor] = None,
        **flash_attn_kwargs: Unpack[FlashAttentionKwargs],
    ) -> BaseModelOutput:
        B, T, D = x.shape
        x = self.embed_tokens(x)
        # Expand and add special tokens: N x T x D -> N x T x P x D
        # Each token is expanded into pool_window_size patches
        x = x.unsqueeze(2)  # N x T x 1 x D
        x = x.repeat(1, 1, self.config.pool_window_size, 1)  # N x T x P x D
        # Add learnable special tokens to each patch
        special_tokens = self.special_tokens.expand(B, T, -1, -1)
        x = x + special_tokens
        # Reshape for processing: (batch * time) x patches x hidden
        x = x.reshape(B * T, self.config.pool_window_size, -1)

        # Cache position: only used for mask construction
        cache_position = torch.arange(0, x.shape[1], device=x.device)
        # Positional IDs
        position_ids = cache_position.unsqueeze(0)

        # Initialize hidden states
        hidden_states = x

        # Create position embeddings to be shared across all layers
        position_embeddings = self.rotary_emb(hidden_states, position_ids)

        seq_len = x.shape[1]
        dtype = x.dtype
        device = x.device

        # 判断是否使用 Flash Attention 2
        is_flash_attn = (self.config._attn_implementation == "flash_attention_2")

        # 初始化 Mask 变量
        full_attn_mask = None
        sliding_attn_mask = None

        if is_flash_attn:
            # -------------------------------------------------------
            # 场景 A: Flash Attention 模式
            # -------------------------------------------------------
            # FA 不需要 4D Mask。
            # 如果有 padding mask (attention_mask [B, L])，直接传给它即可。
            # 如果没有 padding mask，传 None。
            # 滑动窗口逻辑由 Layer 内部传给 FA kernel 的 sliding_window 参数控制。
            full_attn_mask = attention_mask

            # 这里的逻辑是：如果配置启用了滑动窗口，FA 模式下我们也只需要传基础的 padding mask
            # Layer 会自己决定是否调用带 sliding window 的 kernel
            sliding_attn_mask = attention_mask if self.config.use_sliding_window else None

        else:
            # -------------------------------------------------------
            # 场景 B: CPU / Mac / SDPA (Eager 模式)
            # -------------------------------------------------------
            # 必须手动生成 4D Mask [B, 1, L, L]

            # 1. Full Attention (Bidirectional, Global)
            # 对应原来的 create_causal_mask + bidirectional
            full_attn_mask = create_4d_mask(
                seq_len=seq_len,
                dtype=dtype,
                device=device,
                attention_mask=attention_mask,     # [B, L]
                sliding_window=None,
                is_sliding_window=False,
                is_causal=False                    # <--- 关键：双向注意力
            )

            # 2. Sliding Attention (Bidirectional, Local)
            # 对应原来的 create_sliding_window... + bidirectional
            if self.config.use_sliding_window:
                sliding_attn_mask = create_4d_mask(
                    seq_len=seq_len,
                    dtype=dtype,
                    device=device,
                    attention_mask=attention_mask, # [B, L]
                    sliding_window=self.config.sliding_window,
                    is_sliding_window=True,        # <--- 开启滑动窗口
                    is_causal=False                # <--- 关键：双向注意力
                )

        # 构建 Mapping
        self_attn_mask_mapping = {
            "full_attention": full_attn_mask,
            "sliding_attention": sliding_attn_mask,
        }

        for layer_module in self.layers:
            layer_outputs = layer_module(
                hidden_states,
                position_embeddings,
                attention_mask=self_attn_mask_mapping[layer_module.attention_type],
                **flash_attn_kwargs,
            )

            hidden_states = layer_outputs[0]

        hidden_states = self.norm(hidden_states)

        hidden_states = self.proj_out(hidden_states)

        hidden_states = hidden_states.reshape(B, T * self.config.pool_window_size, -1)
        return hidden_states


class AceStepConditionGenerationModel(AceStepPreTrainedModel):
    """Instrumental text2music with LM hints and the default eight-step XL sampler."""

    def __init__(self, config):
        super().__init__(config)
        self.decoder = AceStepDiTModel(config)
        encoder_config = copy.deepcopy(config)
        for field in ("hidden_size", "intermediate_size", "num_attention_heads", "num_key_value_heads"):
            setattr(encoder_config, field, getattr(config, "encoder_" + field, getattr(config, field)))
        self.encoder = AceStepConditionEncoder(encoder_config)
        self.tokenizer = nn.Module()  # Only the inverse FSQ projection is needed.
        self.tokenizer.quantizer = AudioCodeQuantizer(encoder_config)
        self.detokenizer = AudioTokenDetokenizer(encoder_config)
        self.post_init()

    @torch.no_grad()
    def generate_audio(self, text_hidden_states, text_attention_mask,
                       lyric_hidden_states, lyric_attention_mask,
                       refer_audio_acoustic_hidden_states_packed,
                       refer_audio_order_mask, src_latents, chunk_masks, seed,
                       progress=None):
        encoder_hidden_states, encoder_attention_mask = self.encoder(
            text_hidden_states=text_hidden_states,
            text_attention_mask=text_attention_mask,
            lyric_hidden_states=lyric_hidden_states,
            lyric_attention_mask=lyric_attention_mask,
            refer_audio_acoustic_hidden_states_packed=refer_audio_acoustic_hidden_states_packed,
            refer_audio_order_mask=refer_audio_order_mask,
        )
        context = torch.cat([src_latents, chunk_masks.to(src_latents.dtype)], dim=-1)
        bsz, device, dtype = context.shape[0], context.device, context.dtype
        generator = torch.Generator(device=device).manual_seed(seed)
        xt = torch.randn(src_latents.shape, generator=generator, device=device, dtype=dtype)
        cache = EncoderDecoderCache(DynamicCache(), DynamicCache())
        schedule = torch.tensor([1.0 - i / 8 for i in range(8)], device=device, dtype=dtype)
        attention_mask = torch.ones(src_latents.shape[:2], device=device, dtype=torch.long)
        # Official defaults: ODE/Euler, shift=1, no CFG/EMA/clamping/retake.
        # DCW defaults to enabled/double/0.05/0.02/haar, but its optional
        # pytorch_wavelets loader returns None in the supported dependency set.
        # Port the resulting no-op, rather than introducing a wavelet dependency.
        for i in range(8):
            t = schedule[i].item()
            ts = t * torch.ones((bsz,), device=device, dtype=dtype)
            vt, cache = self.decoder(
                hidden_states=xt, timestep=ts, timestep_r=ts,
                attention_mask=attention_mask, encoder_hidden_states=encoder_hidden_states,
                encoder_attention_mask=encoder_attention_mask, context_latents=context,
                use_cache=True, past_key_values=cache,
            )[:2]
            if i == 7:
                xt = xt - vt * ts.unsqueeze(-1).unsqueeze(-1)
            else:
                dt = t - schedule[i + 1].item()
                dt_tensor = dt * torch.ones((bsz,), device=device, dtype=dtype).unsqueeze(-1).unsqueeze(-1)
                xt = xt - vt * dt_tensor
            if progress:
                progress(0.15 + (i + 1) * 0.075, desc=f"Generating music ({i + 1}/8)")
        return xt
