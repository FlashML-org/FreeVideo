# MOVA audio DiT (Apache-2.0, OpenMOSS; Wan2.1 architecture, Apache-2.0), as
# used by Prism (MIT, Tencent). Vendored from Prism-fast 3910631
# (hymm/models/modules/wan_audio_dit.py); FreeVideo keeps the inference parts
# (no sequence parallelism, gradient checkpointing or training forward).
import math
from typing import Literal, Optional, Tuple

import torch
import torch.nn as nn
from einops import rearrange

from .wan_video_dit import DiTBlock


def precompute_freqs_cis(dim: int, end: int = 16384, theta: float = 10000.0, s: float = 1.0):
    freqs = 1.0 / (theta ** (torch.arange(0, dim, 2, device='cpu')[: (dim // 2)].double() / dim))
    pos = torch.arange(end, dtype=torch.float64, device=freqs.device) * s
    freqs = torch.outer(pos, freqs)
    return torch.polar(torch.ones_like(freqs), freqs)  # complex64


def legacy_precompute_freqs_cis_1d(dim: int, end: int = 16384, theta: float = 10000.0, base_tps=4.0,
                                   target_tps=44100/2048):
    s = float(base_tps) / float(target_tps)
    f_freqs_cis = precompute_freqs_cis(dim - 2 * (dim // 3), end, theta, s)
    no_freqs_cis = torch.ones_like(precompute_freqs_cis(dim // 3, end, theta, s))
    return f_freqs_cis, no_freqs_cis, no_freqs_cis


def precompute_freqs_cis_1d(dim: int, end: int = 16384, theta: float = 10000.0):
    return precompute_freqs_cis(dim, end, theta).chunk(3, dim=-1)


class Head(nn.Module):
    def __init__(self, dim: int, out_dim: int, patch_size: Tuple[int, ...], eps: float):
        super().__init__()
        self.dim = dim
        self.patch_size = patch_size
        self.norm = nn.LayerNorm(dim, eps=eps, elementwise_affine=False)
        self.head = nn.Linear(dim, out_dim * math.prod(patch_size))
        self.modulation = nn.Parameter(torch.randn(1, 2, dim) / dim**0.5)

    def forward(self, x, t_mod):
        if len(t_mod.shape) == 3:
            shift, scale = (self.modulation.unsqueeze(0).to(dtype=t_mod.dtype, device=t_mod.device)
                            + t_mod.unsqueeze(2)).chunk(2, dim=2)
            return self.head(self.norm(x) * (1 + scale.squeeze(2)) + shift.squeeze(2))
        # t_mod [B, C] is unsqueezed at dim 1 so B > 1 also broadcasts.
        shift, scale = (self.modulation.to(dtype=t_mod.dtype, device=t_mod.device) + t_mod.unsqueeze(1)).chunk(2, dim=1)
        return self.head(self.norm(x) * (1 + scale) + shift)


class WanAudioModel(nn.Module):
    def __init__(self, dim: int, in_dim: int, ffn_dim: int, out_dim: int, text_dim: int, freq_dim: int,
                 eps: float, patch_size: Tuple[int, ...], num_heads: int, num_layers: int,
                 has_image_input: bool = False, vae_type: Literal["oobleck", "dac"] = "oobleck", **_):
        super().__init__()
        self.dim = dim
        self.freq_dim = freq_dim
        self.has_image_input = has_image_input
        self.patch_size = patch_size
        self.vae_type = vae_type
        self.patch_embedding = nn.Conv1d(in_dim, dim, kernel_size=patch_size, stride=patch_size)
        self.text_embedding = nn.Sequential(nn.Linear(text_dim, dim), nn.GELU(approximate='tanh'), nn.Linear(dim, dim))
        self.time_embedding = nn.Sequential(nn.Linear(freq_dim, dim), nn.SiLU(), nn.Linear(dim, dim))
        self.time_projection = nn.Sequential(nn.SiLU(), nn.Linear(dim, dim * 6))
        self.blocks = nn.ModuleList([DiTBlock(has_image_input, dim, num_heads, ffn_dim, eps)
                                     for _ in range(num_layers)])
        self.head = Head(dim, out_dim, patch_size, eps)
        head_dim = dim // num_heads
        if vae_type == "oobleck":
            self.freqs = legacy_precompute_freqs_cis_1d(head_dim, base_tps=4.0, target_tps=44100/2048)
        elif vae_type == "dac":
            self.freqs = precompute_freqs_cis_1d(head_dim)
        else:
            raise ValueError(f"Invalid VAE type: {vae_type}")

    @property
    def dtype(self):
        return self.patch_embedding.weight.dtype

    def patchify(self, x: torch.Tensor, control_camera_latents_input: Optional[torch.Tensor] = None):
        x = self.patch_embedding(x)
        grid_size = x.shape[2:]
        x = rearrange(x, 'b c f -> b f c').contiguous()
        return x, grid_size

    def unpatchify(self, x: torch.Tensor, grid_size):
        return rearrange(x, 'b f (p c) -> b c (f p)', f=grid_size[0], p=self.patch_size[0])
