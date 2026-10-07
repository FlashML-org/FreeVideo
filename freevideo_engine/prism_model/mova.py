# RoPE assembly of the MOVA dual-tower model (Apache-2.0, OpenMOSS) as used by
# Prism (MIT, Tencent). Vendored from Prism-fast 3910631
# (hymm/models/modules/mova.py). The MOVABridge / FusedMOVABlock wrappers and
# their FSDP / sequence-parallel / training code are not vendored: FreeVideo
# runs the same per-layer computation unit by unit (sampling.transformer).
import torch


def assemble_visual_freqs(freqs_tuple, t, h, w, device):
    """3D RoPE complex freqs for the video DiT: [t*h*w, 1, head_dim//2]."""
    freqs = tuple(f.to(device) for f in freqs_tuple)
    return torch.cat([
        freqs[0][:t].view(t, 1, 1, -1).expand(t, h, w, -1),
        freqs[1][:h].view(1, h, 1, -1).expand(t, h, w, -1),
        freqs[2][:w].view(1, 1, w, -1).expand(t, h, w, -1),
    ], dim=-1).reshape(t * h * w, 1, -1)


def assemble_audio_freqs(freqs_tuple, f, device):
    """1D RoPE complex freqs for the audio DiT: [f, 1, head_dim//2]."""
    return torch.cat([
        freqs_tuple[0][:f].view(f, -1).expand(f, -1),
        freqs_tuple[1][:f].view(f, -1).expand(f, -1),
        freqs_tuple[2][:f].view(f, -1).expand(f, -1),
    ], dim=-1).reshape(f, 1, -1).to(device)
