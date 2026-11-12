"""MiniMax H3 Image transformer: the H3 video DiT without audio, run on a packed [text | references | image] sequence.

Parameter names follow the Comfy-format files (``blocks.N.attn.qkv_proj`` ...), so the int8 ConvRot file loads by name.
Every block modulates its rows through ``adaln_proj`` from a small time table (one 8-wide row per time on a 1025-point
curve), with one set of six vectors per modality tag (0 image, 1 text, 2 audio, unused here). The six PDD output heads
are stored as one full head followed by offsets from it.
"""
import math

import torch
from torch import nn
from torch.nn import functional as F

HIDDEN, HEADS, HEAD_DIM, FFN, LAYERS, REFINER_LAYERS = 5376, 56, 128, 14336, 50, 2
TEXT_DIM, PATCH_DIM, TIME_DIM, ROT_DIM, EPS = 5120, 96, 8, 96, 1e-5
TAGS = 3
SIGMAS = (1.0, 0.9675236940383911, 0.8875485062599182, 0.75, 0.5327739119529724, 0.23200887441635132, 0.0)
REFERENCE_TIME = 0.999


def rms(x, weight, eps=EPS):
    value = x.float()
    value = value * torch.rsqrt(value.pow(2).mean(-1, keepdim=True) + eps)
    return (value * weight.float()).to(x.dtype)


class Attention(nn.Module):
    def __init__(self):
        super().__init__()
        inner = HEADS * HEAD_DIM
        self.qkv_proj = nn.Linear(HIDDEN, inner * 3, bias=False)
        self.out_proj = nn.Linear(inner, HIDDEN, bias=False)
        self.q_norm = nn.Module()
        self.k_norm = nn.Module()
        self.q_norm.register_parameter('weight', nn.Parameter(torch.empty(HEAD_DIM), requires_grad=False))
        self.k_norm.register_parameter('weight', nn.Parameter(torch.empty(HEAD_DIM), requires_grad=False))

    def forward(self, x, rotation=None):
        rows = x.shape[0]
        q, k, v = self.qkv_proj(x).split(HEADS * HEAD_DIM, dim=-1)
        q = rms(q.view(rows, HEADS, HEAD_DIM), self.q_norm.weight)
        k = rms(k.view(rows, HEADS, HEAD_DIM), self.k_norm.weight)
        if rotation is not None:
            cos, sin = rotation
            q, k = rotate(q, cos, sin), rotate(k, cos, sin)
        v = v.view(rows, HEADS, HEAD_DIM)
        out = F.scaled_dot_product_attention(q.transpose(0, 1)[None], k.transpose(0, 1)[None],
                                             v.transpose(0, 1)[None])
        return self.out_proj(out[0].transpose(0, 1).reshape(rows, HEADS * HEAD_DIM))


def rotate(x, cos, sin):
    """Split-half rotation of the first ROT_DIM channels of every head; the rest pass through."""
    part, rest = x[..., :ROT_DIM], x[..., ROT_DIM:]
    first, second = part.chunk(2, dim=-1)
    turned = torch.cat([-second, first], dim=-1)
    return torch.cat([part * cos + turned * sin, rest], dim=-1)


class MLP(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(HIDDEN, FFN * 2, bias=False)
        self.fc2 = nn.Linear(FFN, HIDDEN, bias=False)

    def forward(self, x):
        gate, up = self.fc1(x).chunk(2, dim=-1)
        return self.fc2(F.silu(gate) * up)


def norm_weight():
    return nn.Parameter(torch.empty(HIDDEN), requires_grad=False)


class Scale(nn.Module):
    def __init__(self):
        super().__init__()
        self.weight = norm_weight()


class RefinerBlock(nn.Module):
    def __init__(self):
        super().__init__()
        self.norm1, self.norm2 = Scale(), Scale()
        self.attn, self.mlp = Attention(), MLP()

    def forward(self, x):
        x = x + self.attn(rms(x, self.norm1.weight))
        return x + self.mlp(rms(x, self.norm2.weight))


class Refiner(nn.Module):
    def __init__(self):
        super().__init__()
        self.blocks = nn.ModuleList(RefinerBlock() for _ in range(REFINER_LAYERS))
        self.final_norm = Scale()

    def forward(self, x):
        for block in self.blocks:
            x = block(x)
        return rms(x, self.final_norm.weight)


class Adaln(nn.Module):
    def __init__(self, chunks, tags):
        super().__init__()
        self.chunks, self.tags = chunks, tags
        self.linear = nn.Linear(TIME_DIM, chunks * HIDDEN * tags)

    def forward(self, curve):
        """[U, 8] curve rows -> ``chunks`` tensors of [U * tags, HIDDEN] (row ``u * tags + tag``)."""
        out = F.linear(curve.float(), self.linear.weight.float(), self.linear.bias.float())
        return out.view(curve.shape[0] * self.tags, self.chunks * HIDDEN).chunk(self.chunks, dim=-1)


class Block(nn.Module):
    def __init__(self):
        super().__init__()
        self.norm1, self.norm2 = Scale(), Scale()
        self.attn, self.mlp = Attention(), MLP()
        self.adaln_proj = Adaln(6, TAGS)

    def forward(self, x, curve, segments, rotation):
        shift_a, scale_a, gate_a, shift_m, scale_m, gate_m = (m.to(x.dtype) for m in self.adaln_proj(curve))
        h = rms(x, self.norm1.weight)
        for a, b, row in segments:
            h[a:b] = h[a:b] * (1 + scale_a[row]) + shift_a[row]
        out = self.attn(h, rotation)
        for a, b, row in segments:
            x[a:b] += out[a:b] * gate_a[row]
        h = rms(x, self.norm2.weight)
        for a, b, row in segments:
            h[a:b] = h[a:b] * (1 + scale_m[row]) + shift_m[row]
        out = self.mlp(h)
        for a, b, row in segments:
            x[a:b] += out[a:b] * gate_m[row]
        return x


class Final(nn.Module):
    def __init__(self):
        super().__init__()
        self.norm = Scale()
        self.adaln_proj = Adaln(2, 1)
        self.video_out = nn.Linear(HIDDEN, PATCH_DIM * 6)


class H3Dit(nn.Module):
    def __init__(self):
        super().__init__()
        self.video_patch_proj = nn.Linear(PATCH_DIM, HIDDEN)
        self.condition_proj = nn.Linear(TEXT_DIM, HIDDEN)
        self.register_buffer('adaln_t_table', torch.empty(1025, TIME_DIM))
        self.rope = nn.Module()
        self.rope.register_buffer('inv_freq', torch.empty(16))
        self.token_refiner = Refiner()
        self.blocks = nn.ModuleList(Block() for _ in range(LAYERS))
        self.final_layer = Final()

    def curve(self, times):
        table = self.adaln_t_table.float()
        position = times.float().clamp(0, 1) * (table.shape[0] - 1)
        low = position.floor().long().clamp(max=table.shape[0] - 2)
        return torch.lerp(table[low], table[low + 1], (position - low).unsqueeze(1))

    def embed_text(self, features):
        """[L, 5120] hidden states of the text encoder -> [L, 5376] refined text rows."""
        return self.token_refiner(self.condition_proj(features.to(self.condition_proj.weight.dtype)))

    def rotation(self, positions, dtype):
        angles = positions.float()[:, :, None] * self.rope.inv_freq.float().view(1, 1, -1)
        half = angles.flatten(1)
        angles = torch.cat([half, half], dim=-1)
        return angles.cos().to(dtype)[:, None, :], angles.sin().to(dtype)[:, None, :]

    def head(self, step):
        """The (weight, bias) of the output head of PDD step ``step``: the full head plus the step's offset."""
        weight = self.final_layer.video_out.weight.view(6, PATCH_DIM, HIDDEN).float()
        bias = self.final_layer.video_out.bias.view(6, PATCH_DIM).float()
        if step == 0:
            return weight[0], bias[0]
        return weight[0] + weight[step], bias[0] + bias[step]

    @torch.no_grad()
    def forward(self, text, rows, positions, segments, times, step, *, blocks=None):
        """One velocity prediction.

        ``text`` [T, 5376] refined text rows, ``rows`` [N, 96] float32 image rows (references first),
        ``positions`` [T+N, 3], ``segments`` [(start, stop, row)] covering the sequence where ``row`` indexes
        ``u * 3 + tag`` for the unique times ``times`` [U]; returns [N, 96] float32 velocity for every image row.
        ``blocks`` replaces the block iteration (layer streaming wraps it).
        """
        dtype = text.dtype
        x = torch.cat([text, F.linear(rows, self.video_patch_proj.weight.float(),
                                      self.video_patch_proj.bias.float()).to(dtype)], dim=0)
        curve = self.curve(times)
        rotation = self.rotation(positions, dtype)
        for block in (self.blocks if blocks is None else blocks):
            x = block(x, curve, segments, rotation)
        shift, scale = (m.to(dtype) for m in self.final_layer.adaln_proj(curve[:1]))
        out_rows = x[text.shape[0]:]
        out_rows = rms(out_rows, self.final_layer.norm.weight) * (1 + scale[0]) + shift[0]
        weight, bias = self.head(step)
        return F.linear(out_rows.float(), weight, bias)


# Layout ----------------------------------------------------------------------------------------------

def spatial_axis(dim, patch, sqrt_area):
    import numpy as np
    ratio = dim / sqrt_area
    left = (1.0 - ratio) / 2.0
    return torch.from_numpy(np.linspace(left, left + ratio, dim // patch, endpoint=False) * 32.0).to(torch.float64)


def frame_grid(height, width, patch=2):
    import numpy as np
    sqrt_area = np.sqrt(height * width)
    grids = torch.meshgrid(spatial_axis(height, patch, sqrt_area), spatial_axis(width, patch, sqrt_area), indexing='ij')
    return torch.stack([g.reshape(-1) for g in grids], dim=-1)


def canvas_size(height, width, min_area=2048 * 2048, multiple=32):
    scale = max(1.0, math.sqrt(min_area / (width * height)))
    return (max(multiple, round(height * scale / multiple) * multiple),
            max(multiple, round(width * scale / multiple) * multiple))


def positions_for(text_tokens, references, latent_h, latent_w):
    """Rotary (t, h, w) of ``[text | references | image]``; ``references`` are (latent_h, latent_w) pairs."""
    count = sum((h // 2) * (w // 2) for h, w in references) + (latent_h // 2) * (latent_w // 2)
    out = torch.zeros(text_tokens + count, 3, dtype=torch.float64)
    out[:text_tokens, 0] = torch.arange(text_tokens, dtype=torch.float64)
    cursor, clock = text_tokens, float(text_tokens)
    for h, w in list(references) + [(latent_h, latent_w)]:
        stop = cursor + (h // 2) * (w // 2)
        out[cursor:stop, 0] = clock
        out[cursor:stop, 1:] = frame_grid(h, w)
        cursor, clock = stop, clock + 1.0
    return out


def segments_for(tags, reference_rows, image_rows):
    """Contiguous (start, stop, row) runs for a sequence of ``text tags | reference rows | image rows``.

    With references the unique times are [step time, 0.999] sorted, so the reference rows use unique index 1 (0.999 is
    above every step time) except when a step time exceeds it, which the six steps never do.
    """
    text = len(tags)
    reference_unique = 1 if reference_rows else 0
    runs, start = [], 0
    for i in range(1, text + 1):
        if i == text or tags[i] != tags[start]:
            runs.append((start, i, 0 * 3 + int(tags[start])))
            start = i
    if reference_rows:
        runs.append((text, text + reference_rows, reference_unique * 3 + 0))
    runs.append((text + reference_rows, text + reference_rows + image_rows, 0))
    return runs


def patchify(latent):
    """[C, H, W] -> [H/2 * W/2, C * 4] (rows in raster order, features ordered (C, ph, pw))."""
    c, h, w = latent.shape
    return latent.reshape(c, h // 2, 2, w // 2, 2).permute(1, 3, 0, 2, 4).reshape(-1, c * 4).contiguous()


def unpatchify(rows, height, width, channels=24):
    return rows.reshape(height // 2, width // 2, channels, 2, 2).permute(2, 0, 3, 1, 4).reshape(channels, height, width)


@torch.no_grad()
def sample(dit, features, tags, latent_h, latent_w, generator, device='cuda', dtype=torch.bfloat16, blocks=None,
           noise_device=None, step_callback=None, preview_callback=None):
    """Text-to-image: six velocity-Euler steps from noise; returns [24, H, W] float32 latents.

    ``preview_callback(step, total, rows, velocity, sigma, latent_h, latent_w)`` runs after each step's velocity, before
    the update; it only reads the two tensors (the clean prediction is ``rows + sigma * velocity``)."""
    text = dit.embed_text(features.to(device))
    noise = torch.randn((1, 24, 1, latent_h, latent_w), generator=generator,
                        device=noise_device or device, dtype=torch.float32).to(device)
    rows = patchify(noise[0, :, 0])
    positions = positions_for(features.shape[0], [], latent_h, latent_w).to(device)
    segments = segments_for([int(t) for t in tags], 0, rows.shape[0])
    steps = torch.tensor([1.0 - s for s in SIGMAS[:-1]], dtype=torch.float32)
    sigmas = torch.cat([1.0 - steps, torch.zeros(1)])
    for i in range(len(steps)):
        if step_callback is not None:
            step_callback(i, len(steps))
        times = steps[i:i + 1].to(device)
        velocity = dit(text, rows, positions, segments, times, i, blocks=blocks)
        if preview_callback is not None:
            preview_callback(i + 1, len(steps), rows, velocity, float(sigmas[i]), latent_h, latent_w)
        rows = rows + (sigmas[i] - sigmas[i + 1]).to(device) * velocity
    return unpatchify(rows, latent_h, latent_w)
