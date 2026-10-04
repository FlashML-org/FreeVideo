# Copyright 2026 The MiniMax and HuggingFace Teams. All rights reserved.
# Copyright 2026 FreeVideo contributors.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.
"""Transpose the tile/layer loops to transfer each decoder layer once per clip.

Prologue/epilogue adapted from diffusers' MiniMaxH3VideoViTDecoder3d, pinned at
VDN 30b6b380c2482f3519469350810c2955d8847fd9. Spatial tile geometry, per-tile
GEMM shapes, temporal chunking and stitching remain those of the original VAE.
"""
import os
import types
from contextlib import nullcontext
import torch

from .offload import LayerOffloader


def compile_blocks(decoder):
    """Compile the decoder block once; all blocks share the graph, their weights are inputs.

    Decoding 1344x768x243 on Windows with an RTX 5060 Ti took 114.9 s eager. Compiled, it
    took 100.6 s in a fresh process that loaded the graph from the disk cache, and 105.4 s
    in one that compiled it first. On an RTX PRO 6000 it took 18.1 s and 14.6 s. The
    rounding of fused RMSNorm, rotary and residual work moves 0.7% of output values by
    one or two 8-bit levels. FREEVIDEO_VAE_COMPILE=0 keeps the eager blocks.
    """
    if os.environ.get('FREEVIDEO_VAE_COMPILE', '1').lower() in ('0', 'off', 'false'):
        return False
    for block in decoder.transformer_blocks:
        if not getattr(block, 'freevideo_compiled', False):
            block.forward = torch.compile(block.forward, dynamic=False)
            block.freevideo_compiled = True
    return True


class TileDecoder:
    def __init__(self, decoder, prefetch=True, weight_source=None, resident_blocks=0):
        self.decoder = decoder
        self.resident_blocks = resident_blocks
        self.offloader = LayerOffloader(list(decoder.transformer_blocks)[resident_blocks:], prefetch=prefetch, manage_hooks=False,
                                       weight_source=weight_source)

    def decode(self, tiles):
        model = self.decoder
        states = []
        shapes = []
        ropes = []
        for tile in tiles:
            batch, channels, frames, height, width = tile.shape
            hidden = tile.permute(0, 2, 3, 4, 1).reshape(batch, frames * height * width, channels)
            hidden = model.proj_in(hidden)
            hidden = torch.cat([hidden, model.register_tokens.expand(batch, -1, -1),
                                torch.zeros_like(hidden[:, :1, :])], dim=1)
            grids = [2.0 * (torch.arange(0.5, size, dtype=torch.float32, device=hidden.device) / size) - 1.0
                     for size in (frames, height, width)]
            position = torch.stack(torch.meshgrid(*grids, indexing='ij'), dim=-1).flatten(0, 2)
            position = position.unsqueeze(0).expand(batch, -1, -1)
            position = torch.cat([position, position.new_zeros((batch, model.num_register_tokens + 1, 3))], dim=1)
            states.append(hidden)
            shapes.append((batch, frames, height, width))
            ropes.append(model.rope(position))
        for index in range(len(model.transformer_blocks)):
            context = (nullcontext(model.transformer_blocks[index]) if index < self.resident_blocks
                       else self.offloader.layer(index - self.resident_blocks))
            with context as block:
                for tile_index, hidden in enumerate(states):
                    states[tile_index] = block(hidden, ropes[tile_index])
        outputs = []
        for hidden, shape in zip(states, shapes):
            batch, frames, height, width = shape
            hidden = model.proj_out(model.norm_out(hidden))[:, :frames * height * width]
            hidden = hidden.view(batch, frames, height, width, model.out_channels,
                                 model.patch_size_t, model.patch_size, model.patch_size)
            hidden = hidden.permute(0, 4, 1, 5, 2, 6, 3, 7).contiguous()
            outputs.append(hidden.reshape(batch, model.out_channels, frames * model.patch_size_t,
                                          height * model.patch_size, width * model.patch_size))
        return outputs

    def install(self, vae):
        self.original = vae._decode_clip
        self.vae = vae

        def decode_clip(module, z):
            if not module.use_tiling:
                return self.decode([module.post_quant_conv(z)])[0]
            ratio = module.spatial_compression_ratio
            yi, yl, yo = module._split_tiles(z.shape[-2] * ratio, module.tile_sample_min_height,
                                            module.tile_sample_min_overlap_height)
            xi, xl, xo = module._split_tiles(z.shape[-1] * ratio, module.tile_sample_min_width,
                                            module.tile_sample_min_overlap_width)
            tiles = [module.post_quant_conv(z[..., y // ratio:(y + h) // ratio, x // ratio:(x + w) // ratio])
                     for y, h in zip(yi, yl) for x, w in zip(xi, xl)]
            values = self.decode(tiles)
            rows = [values[start:start + len(xi)] for start in range(0, len(values), len(xi))]
            return module._stitch_tiles(rows, yo, xo)

        vae._decode_clip = types.MethodType(decode_clip, vae)

    def close(self):
        self.offloader.close()
        if hasattr(self, 'vae'):
            self.vae._decode_clip = self.original
