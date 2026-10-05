# Copyright 2026 The MiniMax and HuggingFace Teams. All rights reserved.
# Copyright 2026 FreeVideo contributors.
# Licensed under the Apache License, Version 2.0.
"""Mac decoder scheduling: reuse one layer across original, unbatched tiles.

The decoder preparation/output equations are adapted from the pinned Diffusers
AutoencoderKLMiniMaxH3 at OpenVDN 30b6b380c2482f3519469350810c2955d8847fd9.
Tile geometry, per-tile operator shapes and the original stitching are retained.
Temporal chunks and spatial stitching continue to use the shared decoder plan.
"""
import torch
import time

from .backends.mps_weights import load_group, release_group


def prepare_tile(decoder, hidden):
    batch, channels, frames, height, width = hidden.shape
    hidden = hidden.permute(0, 2, 3, 4, 1).reshape(batch, frames * height * width, channels)
    hidden = decoder.proj_in(hidden)
    patches = hidden.shape[1]
    registers = decoder.register_tokens.expand(batch, -1, -1)
    cls = torch.zeros_like(hidden[:, :1, :])
    hidden = torch.cat([hidden, registers, cls], dim=1)
    grids = [2.0 * (torch.arange(0.5, size, dtype=torch.float32, device=hidden.device) / size) - 1.0
             for size in (frames, height, width)]
    positions = torch.stack(torch.meshgrid(*grids, indexing='ij'), dim=-1).flatten(0, 2)
    positions = positions.unsqueeze(0).expand(batch, -1, -1)
    suffix = positions.new_zeros((batch, decoder.num_register_tokens + 1, 3))
    positions = torch.cat([positions, suffix], dim=1)
    return dict(hidden=hidden, rotary=decoder.rope(positions), patches=patches,
                shape=(batch, frames, height, width))


def finish_tile(decoder, state):
    hidden = decoder.norm_out(state['hidden'])
    hidden = decoder.proj_out(hidden)
    hidden = hidden[:, :state['patches'], :]
    batch, frames, height, width = state['shape']
    patch, temporal = decoder.patch_size, decoder.patch_size_t
    hidden = hidden.view(batch, frames, height, width, decoder.out_channels, temporal, patch, patch)
    hidden = hidden.permute(0, 4, 1, 5, 2, 6, 3, 7).contiguous()
    output = hidden.reshape(batch, decoder.out_channels, frames * temporal, height * patch, width * patch)
    if not bool(output.isfinite().all()):
        raise ValueError('Tile-layer decoder produced nonfinite pixels')
    return output


def decode_clip(vae, z, apply_layer, *, group_tiles):
    if torch.is_grad_enabled() or vae.decoder.gradient_checkpointing:
        raise ValueError('Tile-layer scheduling requires inference without gradient checkpointing')
    if type(group_tiles) is not int or group_tiles < 1:
        raise ValueError('Tile group must be a positive integer')
    if not vae.use_tiling:
        return vae.decoder(vae.post_quant_conv(z))
    ratio = vae.spatial_compression_ratio
    ys, heights, overlaps_y = vae._split_tiles(z.shape[-2] * ratio,
        vae.tile_sample_min_height, vae.tile_sample_min_overlap_height)
    xs, widths, overlaps_x = vae._split_tiles(z.shape[-1] * ratio,
        vae.tile_sample_min_width, vae.tile_sample_min_overlap_width)
    positions = [(row, col, y, h, x, w) for row, (y, h) in enumerate(zip(ys, heights))
                 for col, (x, w) in enumerate(zip(xs, widths))]
    outputs = [[None] * len(xs) for _ in ys]
    for begin in range(0, len(positions), group_tiles):
        locations = positions[begin:begin + group_tiles]
        states = [prepare_tile(vae.decoder, vae.post_quant_conv(
            z[..., y // ratio:(y + h) // ratio, x // ratio:(x + w) // ratio]))
            for _, _, y, h, x, w in locations]
        for index, block in enumerate(vae.decoder.transformer_blocks):
            # Each invocation has the exact original per-tile shape. Only the
            # order of independent tiles changes; no concatenated matmul batch.
            hidden = apply_layer(index, block, states)
            if len(hidden) != len(states):
                raise ValueError('Layer scheduler lost a tile')
            for state, value in zip(states, hidden):
                state['hidden'] = value
            del hidden, value
        for (row, col, *_), state in zip(locations, states):
            outputs[row][col] = finish_tile(vae.decoder, state)
        del states, state
    return vae._stitch_tiles(outputs, overlaps_y, overlaps_x)


class TileDecoder:
    """A request-local VAE view; never replace model or module-level methods.

    One active layer belongs to a whole group, then leaves before the next
    layer is read. Each original forward still executes its pressure guard.
    This is active work, not an optional cache between future layer visits.
    """
    def __init__(self, vae, backend, specs, *, group_tiles=4):
        if type(group_tiles) is not int or not 1 <= group_tiles <= 4:
            raise ValueError('Native decode groups require one to four original tiles')
        if not vae.use_tiling:
            raise ValueError('Tile scheduling requires the original tiled decoder')
        self.vae, self.backend, self.specs = vae, backend, list(specs)
        self.group_tiles = group_tiles
        self.records = []
        self.entered = False

    def __getattr__(self, name):
        return getattr(self.vae, name)

    def __enter__(self):
        if self.entered or self.vae is None:
            raise RuntimeError('A tile decoder belongs to one request')
        self.entered = True
        return self

    def __exit__(self, *args):
        self.entered = False
        self.vae = None
        self.specs.clear()
        return False

    def _decode_clip(self, z):
        if not self.entered:
            raise RuntimeError('Tile decoder requires an active request scope')
        return decode_clip(self.vae, z, self.apply, group_tiles=self.group_tiles)

    def apply(self, index, block, states):
        if not self.entered or self.specs[index][0] is not block:
            raise ValueError('Decoder layer order or request scope changed')
        _, paths, prefix, exclude = self.specs[index]
        if any(not p.is_meta for name, p in block.named_parameters()
               if not any(name.startswith(skip) for skip in exclude)):
            raise ValueError('Decoder layer must start unloaded')
        tick = time.monotonic()
        record = dict(layer_loads=0, hydrated_weight_bytes=0, largest_layer_bytes=0,
                      load_seconds=0., tile_forwards=0)
        try:
            size = load_group(block, paths, {}, prefix=prefix, exclude=exclude,
                              device=self.backend.device, linear_dtype=torch.float16)
            record.update(layer_loads=1, hydrated_weight_bytes=size, largest_layer_bytes=size,
                          load_seconds=time.monotonic() - tick)
            result = []
            for state in states:
                result.append(block(state['hidden'], state['rotary']))
                record['tile_forwards'] += 1
            return result
        finally:
            try:
                self.backend.synchronize()
            finally:
                release_group(block, exclude=exclude)
                self.backend.empty_cache()
                self.records.append(record)

    def stats(self):
        summed = ('layer_loads', 'hydrated_weight_bytes', 'load_seconds', 'tile_forwards')
        value = {key: sum(row.get(key, 0) for row in self.records) for key in summed}
        value.update(policy='One layer across original unbatched spatial tiles',
            spatial_tile_group=self.group_tiles, layer_groups=len(self.records),
            largest_layer_bytes=max((r['largest_layer_bytes'] for r in self.records), default=0),
            retains_between_layer_groups=False,
            load_seconds_scope='Host read and submission; completion is inside the group timer',
            layer_synchronization='Group completion and failure before weight release',
            precision='Original FP32 checkpoint; FP16 Linear compute cache')
        return value
