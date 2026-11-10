"""A small convolutional decoder for the sampling preview.

It reads the predicted clean latents of a few frames and draws each at a
quarter of the final size; the per-pixel map in frame_preview.py is the
fallback. Weights are a ~1M-parameter network distilled from the video VAE
decoder (a MiniMax H3 derivative, see licenses/MiniMax-H3-Community-License-
Agreement.txt). It only reads tensors the sampler produced.
"""
from pathlib import Path

WEIGHTS = Path(__file__).with_name('frame_preview_decoder.safetensors')
CHANNELS = 24
BATCH = 4  # frames per forward pass; the reserved memory below is for this many
FIXED_BYTES = 8 * 2**20  # weights, convolution workspace and allocator slack
BYTES_PER_PIXEL = 128    # of the final picture, for BATCH frames (measured 126: 130 MB at 768x1344, 264 MB at 1088x1920)


def reserve_bytes(width, height):
    """GPU bytes one preview of the final request size can take beside the sampler."""
    return FIXED_BYTES + BYTES_PER_PIXEL * int(width) * int(height)


def build():
    import torch.nn as nn
    import torch.nn.functional as F

    class Res(nn.Module):
        def __init__(self, c):
            super().__init__()
            self.a, self.b = nn.Conv2d(c, c, 3, padding=1), nn.Conv2d(c, c, 3, padding=1)

        def forward(self, x):
            return x + self.b(F.gelu(self.a(F.gelu(x))))

    class Decoder(nn.Module):
        """(n, 24, h, w) latent frames -> (n, 3, 4h, 4w) RGB in [0, 1] once clamped."""
        def __init__(self, c0=96, c1=48):
            super().__init__()
            self.inp = nn.Conv2d(CHANNELS, c0, 3, padding=1)
            self.b0 = nn.Sequential(*[Res(c0) for _ in range(4)])
            self.up1 = nn.Conv2d(c0, c1 * 4, 3, padding=1)
            self.b1 = nn.Sequential(Res(c1), Res(c1))
            self.up2 = nn.Conv2d(c1, c1 // 2 * 4, 3, padding=1)
            self.b2 = Res(c1 // 2)
            self.out = nn.Conv2d(c1 // 2, 3, 3, padding=1)

        def forward(self, z):
            x = self.b0(self.inp(z))
            x = self.b1(F.pixel_shuffle(self.up1(x), 2))
            x = self.b2(F.pixel_shuffle(self.up2(x), 2))
            return self.out(F.gelu(x)) + 0.5

    return Decoder()


def load(device, path=WEIGHTS):
    """The decoder on `device` in fp32; raises when the file is missing or does not fit."""
    from safetensors.torch import load_file
    model = build()
    model.load_state_dict(load_file(str(path)), strict=True)
    return model.to(device).eval().requires_grad_(False)


def decode(model, latents, finish=lambda picture: picture):
    """latents (n, 24, h, w) float32 -> finish((m, 3, 4h, 4w)) per BATCH frames, joined along the frame axis."""
    import torch
    with torch.no_grad():
        return torch.cat([finish(model(latents[i:i + BATCH])) for i in range(0, latents.shape[0], BATCH)])
