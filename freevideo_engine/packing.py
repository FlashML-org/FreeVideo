# Copyright 2026 The MiniMax and HuggingFace Teams. All rights reserved.
# Copyright 2026 FreeVideo contributors.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.
"""Bound packed-input and output-head intermediates without dropping any rows.

Adapted from the pinned Diffusers MiniMaxH3Transformer3DModel.forward. Scattering
is in-place under inference, modality projections are released immediately, and
the FP32 projections/output normalization run over bounded row chunks. GEMM
shape changes can alter rounding; this is an explicit optimization profile.
"""
import gc
import types
import torch
from .fp8 import input_dtype

from diffusers.models.transformers.transformer_minimax_h3 import MiniMaxH3TransformerOutput


def install_streamed_forward(model, chunk=1024, residual_offload=False):
    if chunk < 1:
        raise ValueError('Projection chunk must be positive')
    # Compiling for a new sequence length captures Python frames in reference
    # cycles that also hold the first block's output. The second block stages
    # that output to host memory and drops it, but the cycle keeps it alive
    # through the second block's attention until the collector happens to run:
    # on an RTX 3090 under Windows, 1344x768x362 second passes allocated
    # 6.82 GiB instead of 5.74. A staged block collects once right after it
    # drops its input, in the first forward of each new length.
    collected = set()

    def blocks(self, value, length, temb, adaln_indices, rotary):
        first = length not in collected
        collected.add(length)
        for index, block in enumerate(self.transformer_blocks):
            if first and index in (1, 2) and hasattr(value, 'collect_after_store'):
                value.collect_after_store = True
            value = block(value, temb, adaln_indices, rotary)
            if first and index == 0 and not hasattr(value, 'collect_after_store'):
                gc.collect()
        return value

    def forward(self, hidden_states, audio_hidden_states, encoder_hidden_states,
                timestep, timestep_indices, token_tags, position_ids,
                video_indices, audio_indices, text_indices, attention_kwargs=None, return_dict=True):
        if torch.is_grad_enabled():
            raise RuntimeError('Streamed packing requires inference without autograd')
        if attention_kwargs:
            raise ValueError('This profile expects already merged adapters without attention kwargs')
        length = position_ids.shape[0]
        if position_ids.shape != (length, 3) or token_tags.shape != (length,) or timestep_indices.shape != (length,):
            raise ValueError('Invalid packed sequence geometry')
        rotary = self.rope(position_ids)
        text = getattr(self, '_freevideo_refined_text', None)
        if text is None:
            text = self.context_embedder(encoder_hidden_states.to(input_dtype(self.context_embedder)))
            text = self.token_refiner(text)
        packed = text.new_zeros((text.shape[0], length, text.shape[-1]))
        packed.index_copy_(1, text_indices, text)
        del text
        for source, indices, projection in ((hidden_states, video_indices, self.proj_in),
                                            (audio_hidden_states, audio_indices, self.audio_proj_in)):
            for start in range(0, source.shape[1], chunk):
                projected = projection(source[:, start:start + chunk].to(projection.weight.dtype))
                packed.index_copy_(1, indices[start:start + chunk], projected.to(packed.dtype))
                del projected
        del source
        temb = self.time_embedder(self.time_proj(timestep).to(self.time_embedder.linear_1.weight.dtype))
        adaln_indices = timestep_indices * 3 + token_tags
        if residual_offload:
            from .residual import ResidualState
            state = ResidualState(packed)
            del packed
            try:
                state = blocks(self, state, length, temb, adaln_indices, rotary)
                packed = state.take()
                self._freevideo_residual_stats = state.stats()
            finally:
                state.close()
        else:
            packed = blocks(self, packed, length, temb, adaln_indices, rotary)
        video_all = torch.empty((packed.shape[0], length, self.proj_out.out_features),
                                dtype=self.proj_out.weight.dtype, device=packed.device)
        audio_all = torch.empty((packed.shape[0], length, self.audio_proj_out.out_features),
                                dtype=self.audio_proj_out.weight.dtype, device=packed.device)
        for start in range(0, length, chunk):
            normalized = self.norm_out(packed[:, start:start + chunk], temb, timestep_indices[start:start + chunk])
            normalized = normalized.to(self.proj_out.weight.dtype)
            video_all[:, start:start + chunk] = self.proj_out(normalized)
            audio_all[:, start:start + chunk] = self.audio_proj_out(normalized)
        video = video_all.index_select(1, video_indices)
        audio = audio_all.index_select(1, audio_indices)
        return MiniMaxH3TransformerOutput(sample=video, audio_sample=audio) if return_dict else (video, audio)

    model.forward = types.MethodType(forward, model)
