# Copyright 2026 The MiniMax and HuggingFace Teams. All rights reserved.
# Copyright 2026 FreeVideo contributors.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.
"""Tiled decoding of MiniMax H3 Image latents with the 1.17 GB fp16 VAE, in a few hundred MB.

The decoder is a 2D ViT: every latent pixel is a token, four register tokens and one zero
token are appended, 24 transformer blocks run over the whole sequence and each token expands
to a 16x16 pixel patch. Attention is quadratic, so the official release decodes in 256 px
tiles (16x16 tokens) that overlap by at least 64 px and are blended linearly; that is also
what its decoder was trained against. The tile geometry, rotary positions (normalised per
tile) and blending here are those of diffusers' AutoencoderKLMiniMaxH3Image.

What differs is memory: tiles are decoded in batches one row at a time, and each finished
row is blended with the raw tiles of the row above and written straight into the output, so
a 16 MP image (256x256 latents, 441 tiles) needs the weights, one row of tiles and the
output, not every tile at once.

The weights are the Comfy-format file (decoder.x_embedder, attn.to_qkv, ff.w1 ...). Linear
layers run in fp16; RMSNorm, LayerNorm, residual scales, register tokens and the 1x1
post-quantisation convolution stay fp32, as the reference pins them.
"""
import math

import torch
import torch.nn.functional as F

SPATIAL_RATIO = 16
TILE = 256
OVERLAP = 64
EPS = 1e-5
HEADS = 16
HEAD_DIM = 64
ROPE_DIM = 32
THETA = 100.0
REGISTER_TOKENS = 4

LATENTS_MEAN = (0.858090341091156, -0.9606591463088989, 1.0661640167236328, -0.5090325474739075,
                -0.2727581858634949, -1.3675414323806763, -0.2553254961967468, -0.26907554268836975,
                -0.5376840829849243, -0.0464097298681736, 0.6657370328903198, 0.19690127670764923,
                -0.5460608005523682, -0.4035342037677765, -0.23683024942874908, 0.25928452610969543,
                -0.30133944749832153, 0.211341992020607, -1.1206848621368408, 0.3581933379173279,
                -0.04225143790245056, 0.2604829967021942, 0.22864092886447906, 0.7056031823158264)
LATENTS_STD = (1.2223774194717407, 1.2767263650894165, 1.6831774711608887, 1.7549455165863037,
               1.5636216402053833, 2.194143533706665, 0.9653137922286987, 1.0569885969161987,
               0.841948926448822, 0.7729952931404114, 1.8955937623977661, 0.946841835975647,
               0.7996809482574463, 0.44988900423049927, 0.7197399735450745, 0.6936293244361877,
               2.961095094680786, 2.7694199085235596, 3.0496184825897217, 2.1088054180145264,
               3.276226282119751, 3.1627357006073, 2.2816812992095947, 2.6127843856811523)
PIXEL_MEAN = (0.485, 0.456, 0.406)
PIXEL_STD = (0.229, 0.224, 0.225)

# Tiles per forward pass. A tile costs about 10 MB of activations in fp16.
MAX_BATCH = 32


def split_tiles(length, tile_size, min_overlap, ratio=SPATIAL_RATIO):
    """Lay tile_size tiles over length pixels with every overlap at least min_overlap.

    The slack is spread over the overlaps in whole latent steps so each boundary stays on
    the latent grid. Returns (starts, lengths, overlaps).
    """
    if tile_size >= length:
        return [0], [length], []
    count = math.ceil(length / tile_size)
    while tile_size * count - min_overlap * (count - 1) - length < 0:
        count += 1
    overlaps = [min_overlap] * (count - 1)
    remaining = tile_size * count - sum(overlaps) - length
    for index in range(remaining // ratio):
        overlaps[index % (count - 1)] += ratio
    starts = [0]
    for index in range(count - 1):
        starts.append(starts[-1] + tile_size - overlaps[index])
    return starts, [tile_size] * count, overlaps


def blend(a, b, extent, dim):
    """Linear ramp from the end of a to the start of b over extent pixels along dim."""
    extent = min(a.shape[dim], b.shape[dim], extent)
    if extent <= 0:
        return b
    positions = torch.arange(extent, device=b.device, dtype=b.dtype)
    shape = [1] * a.ndim
    shape[dim] = extent
    weight_a = (1 - positions / extent).view(shape)
    weight_b = (positions / extent).view(shape)
    tail = [slice(None)] * a.ndim
    tail[dim] = slice(-extent, None)
    head = [slice(None)] * b.ndim
    head[dim] = slice(0, extent)
    blended = a[tuple(tail)] * weight_a + b[tuple(head)] * weight_b
    if extent == b.shape[dim]:
        return blended
    rest = [slice(None)] * b.ndim
    rest[dim] = slice(extent, None)
    return torch.cat([blended, b[tuple(rest)]], dim=dim)


def stitch_row(row, above, y_overlap_above, y_overlap_below, x_overlaps):
    """Blend one row of raw tiles with the raw row above and crop the overlaps below and right.

    Each tile blends with the raw tile above it, then with the raw tile to its left,
    as the reference does, so the corners come out the same.
    """
    pieces = []
    for j, tile in enumerate(row):
        if above is not None:
            tile = blend(above[j], tile, y_overlap_above, dim=-2)
        if j > 0:
            tile = blend(row[j - 1], tile, x_overlaps[j - 1], dim=-1)
        if y_overlap_below:
            tile = tile[..., :-y_overlap_below, :]
        if j < len(row) - 1 and x_overlaps[j]:
            tile = tile[..., :, :-x_overlaps[j]]
        pieces.append(tile)
    return torch.cat(pieces, dim=-1)


def _rms(x, weight, eps=EPS):
    x = x.float()
    x = x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + eps)
    return x if weight is None else x * weight


def _rope(height, width, device):
    """cos/sin for a height x width token grid plus the register and zero tokens, which sit at 0."""
    grids = [2.0 * (torch.arange(0.5, size, dtype=torch.float32, device=device) / size) - 1.0
             for size in (height, width)]
    position = torch.stack(torch.meshgrid(*grids, indexing='ij'), dim=-1).flatten(0, 1)
    position = torch.cat([position, position.new_zeros((REGISTER_TOKENS + 1, 2))], dim=0)
    inv_freq = 1.0 / THETA ** torch.arange(0, 1, 2 * 2 / ROPE_DIM, dtype=torch.float32, device=device)
    angles = 2.0 * math.pi * position[:, :, None] * inv_freq[None, None, :]
    angles = angles.flatten(1, 2).tile(2)
    return angles.cos()[None, None], angles.sin()[None, None]


def _rotate(x, cos, sin):
    rotary, rest = x[..., :ROPE_DIM], x[..., ROPE_DIM:]
    first, second = rotary.chunk(2, dim=-1)
    rotated = torch.cat([-second, first], dim=-1)
    return torch.cat([rotary * cos + rotated * sin, rest], dim=-1)


class H3ImageDecoder:
    """The H3 Image ViT decoder with tiled, batched, streamed decoding."""

    def __init__(self, tensors, device, dtype=torch.float16):
        self.device = torch.device(device)
        self.dtype = dtype
        self.layers = int(1 + max(int(key.split('.')[2]) for key in tensors
                                  if key.startswith('decoder.transformer_blocks.')))

        def fp32(key):
            return tensors[key].to(self.device, torch.float32)

        def linear(key):
            return tensors[key + '.weight'].to(self.device, dtype), tensors[key + '.bias'].to(self.device, dtype)

        self.post_quant = (fp32('post_quant_conv.weight').flatten(1), fp32('post_quant_conv.bias'))
        self.embed = (fp32('decoder.x_embedder.weight'), fp32('decoder.x_embedder.bias'))
        self.register = fp32('decoder.register_tokens')
        self.norm_out = (fp32('decoder.norm_out.weight'), fp32('decoder.norm_out.bias'))
        self.proj_out = linear('decoder.proj_out')
        self.blocks = []
        for index in range(self.layers):
            prefix = f'decoder.transformer_blocks.{index}.'
            self.blocks.append({
                'norm1': fp32(prefix + 'norm1.weight'), 'norm2': fp32(prefix + 'norm2.weight'),
                'scale1': fp32(prefix + 'scale1'), 'scale2': fp32(prefix + 'scale2'),
                'qkv': linear(prefix + 'attn.to_qkv'), 'out': linear(prefix + 'attn.to_out'),
                'w1': linear(prefix + 'ff.w1'), 'w2': linear(prefix + 'ff.w2'),
            })
        self.mean = torch.tensor(LATENTS_MEAN, device=self.device).view(1, -1, 1, 1)
        self.std = torch.tensor(LATENTS_STD, device=self.device).view(1, -1, 1, 1)

    @classmethod
    def load(cls, path, device='cuda', dtype=torch.float16):
        """Read the Comfy fp16 VAE file, keeping only the decoder and post-quantisation tensors."""
        from safetensors import safe_open
        tensors = {}
        with safe_open(str(path), framework='pt', device='cpu') as handle:
            for key in handle.keys():
                if key.startswith('decoder.') or key.startswith('post_quant_conv.'):
                    tensors[key] = handle.get_tensor(key)
        return cls(tensors, device, dtype)

    def _block(self, x, block, cos, sin):
        batch, count, dim = x.shape
        norm = _rms(x, block['norm1']).to(self.dtype)
        # to_qkv rows are grouped per head: (head, q|k|v, head_dim).
        qkv = F.linear(norm, *block['qkv']).view(batch, count, HEADS, 3, HEAD_DIM)
        query, key, value = (qkv[:, :, :, index].transpose(1, 2) for index in range(3))
        query = _rotate(_rms(query, None).to(self.dtype), cos.to(self.dtype), sin.to(self.dtype))
        key = _rotate(_rms(key, None).to(self.dtype), cos.to(self.dtype), sin.to(self.dtype))
        attended = F.scaled_dot_product_attention(query, key, value.contiguous())
        attended = attended.transpose(1, 2).reshape(batch, count, dim)
        x = x + F.linear(attended, *block['out']) * block['scale1']
        norm = _rms(x, block['norm2']).to(self.dtype)
        gate, value = F.linear(norm, *block['w1']).chunk(2, dim=-1)  # the reverse of diffusers' ff.net.0.proj
        return x + F.linear(value * F.silu(gate), *block['w2']) * block['scale2']

    def decode_tiles(self, latents):
        """Decode (n, 24, h, w) denormalised latents, all of one shape, to (n, 3, 16h, 16w) fp32."""
        count, _, height, width = latents.shape
        weight, bias = self.post_quant
        tokens = F.linear(latents.float().permute(0, 2, 3, 1).reshape(count, height * width, -1), weight, bias)
        weight, bias = self.embed
        tokens = F.linear(tokens, weight, bias)
        x = torch.cat([tokens, self.register.expand(count, -1, -1),
                       torch.zeros_like(tokens[:, :1])], dim=1)
        cos, sin = _rope(height, width, self.device)
        for block in self.blocks:
            x = self._block(x, block, cos, sin)
        x = F.layer_norm(x, (x.shape[-1],), *self.norm_out, eps=EPS).to(self.dtype)
        x = F.linear(x, *self.proj_out)[:, :height * width]
        x = x.view(count, height, width, 3, SPATIAL_RATIO, SPATIAL_RATIO)
        return x.permute(0, 3, 1, 4, 2, 5).reshape(count, 3, height * SPATIAL_RATIO,
                                                    width * SPATIAL_RATIO).float()

    @torch.inference_mode()
    def decode(self, latents, tile=TILE, overlap=OVERLAP, normalized=True, max_batch=MAX_BATCH):
        """Decode (n, 24, h, w) latents to (n, 3, 16h, 16w) fp32 in the model's ImageNet-normalised space.

        tile is the tile edge in pixels (default 256, the size the model was trained to
        decode at; overlap 64 px between neighbours). tile=None decodes the whole image in
        one sequence of 16 times fewer tokens than pixels per side squared, far longer than
        the 256 tokens the decoder was trained on.
        normalized=True undoes the latent mean and standard deviation first, as the
        official pipeline does for sampled latents. Use to_unit for [0, 1] pixels.
        """
        if latents.ndim != 4:
            raise ValueError('latents must have shape (batch, 24, height, width)')
        latents = latents.to(self.device)
        if normalized:
            latents = latents.float() * self.std + self.mean
        return torch.cat([self._decode_one(latents[index:index + 1], tile, overlap, max_batch)
                          for index in range(latents.shape[0])])

    def _decode_one(self, z, tile, overlap, max_batch):
        height, width = z.shape[-2] * SPATIAL_RATIO, z.shape[-1] * SPATIAL_RATIO
        if tile is None:
            return self.decode_tiles(z)
        if tile % SPATIAL_RATIO or overlap % SPATIAL_RATIO or not 0 <= overlap < tile:
            raise ValueError('tile and overlap must be multiples of 16 px with overlap < tile')
        yi, yl, yo = split_tiles(height, tile, overlap)
        xi, xl, xo = split_tiles(width, tile, overlap)
        output = torch.empty((1, 3, height, width), dtype=torch.float32, device=self.device)
        above = None
        top = 0
        ratio = SPATIAL_RATIO
        for i, (y, h) in enumerate(zip(yi, yl)):
            batch = [z[..., y // ratio:(y + h) // ratio, x // ratio:(x + w) // ratio] for x, w in zip(xi, xl)]
            row = []
            for start in range(0, len(batch), max_batch):
                chunk = torch.cat(batch[start:start + max_batch])
                row.extend(self.decode_tiles(chunk).split(1))
            below = yo[i] if i < len(yo) else 0
            piece = stitch_row(row, above, yo[i - 1] if i else 0, below, xo)
            output[:, :, top:top + piece.shape[-2]] = piece
            top += piece.shape[-2]
            above = row
        return output


class H3ImageEncoder:
    """The H3 Image VAE encoder, for reference images.

    The released encoder is a causal 3D CNN run on one frame. Every causal convolution
    pads two zero frames in front, so on a single frame only the last temporal tap of
    each kernel meets data, and the temporal strides leave one frame: the encoder is
    exactly a 2D CNN with those taps. The reference pipeline keeps it in float32 and
    encodes in 256 px tiles overlapping by at least 64 px, blending the moments linearly
    in latent space; so does this one. Its weights are the encoder half of the same
    Comfy file the decoder reads (identical to the video VAE's encoder).
    """

    def __init__(self, tensors, device, dtype=torch.float32):
        self.device = torch.device(device)
        self.dtype = dtype

        def conv(key):
            weight = tensors[key + '.weight']
            weight = weight[:, :, -1] if weight.ndim == 5 else weight
            return weight.to(self.device, dtype).contiguous(), tensors[key + '.bias'].to(self.device, dtype)

        def norm(key):
            return tensors[key + '.weight'].to(self.device, dtype), tensors[key + '.bias'].to(self.device, dtype)

        levels = 1 + max(int(key.split('.')[2]) for key in tensors if key.startswith('encoder.down.'))
        self.conv_in = conv('encoder.conv_in')
        self.levels = []
        for level in range(levels):
            blocks, index = [], 0
            while f'encoder.down.{level}.block.{index}.conv1.weight' in tensors:
                prefix = f'encoder.down.{level}.block.{index}.'
                blocks.append(dict(norm1=norm(prefix + 'norm1'), conv1=conv(prefix + 'conv1'),
                                   norm2=norm(prefix + 'norm2'), conv2=conv(prefix + 'conv2'),
                                   shortcut=conv(prefix + 'nin_shortcut')
                                   if prefix + 'nin_shortcut.weight' in tensors else None))
                index += 1
            key = f'encoder.down.{level}.downsample.conv'
            # Every level that downsamples halves both sides (the released 2, 2, 2, 2, 1, 1).
            self.levels.append((blocks, conv(key) if key + '.weight' in tensors else None))
        self.norm_out = norm('encoder.norm_out')
        self.conv_out = conv('encoder.conv_out')
        self.quant = conv('quant_conv')

    @classmethod
    def load(cls, path, device='cuda', dtype=torch.float32):
        """Read the encoder and quantisation tensors of the Comfy fp16 VAE file."""
        from safetensors import safe_open
        tensors = {}
        with safe_open(str(path), framework='pt', device='cpu') as handle:
            for key in handle.keys():
                if key.startswith('encoder.') or key.startswith('quant_conv.'):
                    tensors[key] = handle.get_tensor(key)
        return cls(tensors, device, dtype)

    @staticmethod
    def _conv(x, weight_bias, stride=1, pad=1):
        if pad:
            x = F.pad(x, (pad,) * 4, mode='reflect')
        return F.conv2d(x, *weight_bias, stride=stride)

    @staticmethod
    def _norm(x, weight_bias):
        return F.silu(F.group_norm(x, 32, *weight_bias, eps=1e-6))

    def encode_tiles(self, pixels):
        """(n, 3, H, W) normalised pixels, H and W multiples of 16 -> (n, 48, H/16, W/16) moments."""
        x = self._conv(pixels.to(self.device, self.dtype), self.conv_in)
        for blocks, down in self.levels:
            for block in blocks:
                h = self._conv(self._norm(x, block['norm1']), block['conv1'])
                h = self._conv(self._norm(h, block['norm2']), block['conv2'])
                x = (self._conv(x, block['shortcut'], pad=0) if block['shortcut'] is not None else x) + h
            if down is not None:
                x = self._conv(F.pad(x, (0, 1, 0, 1), mode='reflect'), down, stride=2, pad=0)
        x = self._conv(self._norm(x, self.norm_out), self.conv_out)
        return self._conv(x, self.quant, pad=0)

    @torch.inference_mode()
    def moments(self, pixels, tile=TILE, overlap=OVERLAP, max_batch=4):
        """(1, 3, H, W) ImageNet-normalised pixels -> (1, 48, H/16, W/16) float32 posterior moments.

        Convolutions run in full float32: with TF32 the 2D form drifts up to 0.7 from the exact moments (the
        released 3D form, about 0.1); without it both agree to 5e-4. The setting is restored afterwards."""
        height, width = pixels.shape[-2:]
        if height % SPATIAL_RATIO or width % SPATIAL_RATIO:
            raise ValueError('reference pixels must be cropped to multiples of 16')
        previous = torch.backends.cudnn.allow_tf32
        torch.backends.cudnn.allow_tf32 = False
        try:
            return self._moments(pixels, tile, overlap, max_batch)
        finally:
            torch.backends.cudnn.allow_tf32 = previous

    def _moments(self, pixels, tile, overlap, max_batch):
        height, width = pixels.shape[-2:]
        yi, yl, yo = split_tiles(height, tile, overlap)
        xi, xl, xo = split_tiles(width, tile, overlap)
        y_overlaps = [o // SPATIAL_RATIO for o in yo]
        x_overlaps = [o // SPATIAL_RATIO for o in xo]
        pieces, above = [], None
        for i, (y, h) in enumerate(zip(yi, yl)):
            tiles = [pixels[..., y:y + h, x:x + w] for x, w in zip(xi, xl)]
            row = []
            for start in range(0, len(tiles), max_batch):
                row.extend(self.encode_tiles(torch.cat(tiles[start:start + max_batch])).float().split(1))
            below = y_overlaps[i] if i < len(y_overlaps) else 0
            pieces.append(stitch_row(row, above, y_overlaps[i - 1] if i else 0, below, x_overlaps))
            above = row
        return torch.cat(pieces, dim=-2)


def reference_latents(moments, generator, noise_device=None):
    """Posterior sample of a reference as the released recipe takes it: drawn from the request's generator,
    rounded to float16, cropped to whole 2x2 patches and normalised. (1, 48, h, w) -> (24, h', w')."""
    mean, logvar = moments.float().chunk(2, dim=1)
    std = torch.exp(0.5 * logvar.clamp(-30.0, 20.0))
    noise = torch.randn(mean.shape, generator=generator, device=noise_device or mean.device, dtype=torch.float32)
    latents = (mean + std * noise.to(mean.device)).to(torch.float16).float()
    latents = latents[..., :latents.shape[-2] // 2 * 2, :latents.shape[-1] // 2 * 2]
    shift = torch.tensor(LATENTS_MEAN, device=latents.device).view(1, -1, 1, 1)
    scale = torch.tensor(LATENTS_STD, device=latents.device).view(1, -1, 1, 1)
    return ((latents - shift) / scale)[0]


def to_unit(pixels):
    """Model output to [0, 1] pixels: undo the ImageNet normalisation and clamp."""
    mean = torch.tensor(PIXEL_MEAN, device=pixels.device).view(1, 3, 1, 1)
    std = torch.tensor(PIXEL_STD, device=pixels.device).view(1, 3, 1, 1)
    return (pixels * std + mean).clamp(0, 1)
