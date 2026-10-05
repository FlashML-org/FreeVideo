"""Optional normalized H3 latent upscaler; no Comfy imports or automatic downloads.

Network adapted from LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler,
commit 40316cf008b2fd8663263270669eb4da23f89d2c (MIT).
Copyright (c) 2026 LBH-123-AI. Full notice: licenses/latent-upscaler-MIT.txt.
FreeVideo changes: independent loader, meta initialization, pinned checkpoint
validation, normalized-space API and explicit model cleanup.
"""
import gc
import hashlib
from pathlib import Path
import time

import torch
import torch.nn as nn
import torch.nn.functional as F
from einops import rearrange

from .two_pass import UPSCALER
CHECKPOINT_SHA256 = UPSCALER['sha256']
CHECKPOINT_BYTES = UPSCALER['bytes']

# Checkpoint-specific transform ON TOP OF H3 sampling normalization. The
# published weights need the same transform as the original node wrapper.
# Omitting it produces severe color/grid artifacts (upstream issue #60).
LATENTS_MEAN = (0.858090341091156, -0.9606591463088989, 1.0661640167236328, -0.5090325474739075, -0.2727581858634949, -1.3675414323806763, -0.2553254961967468, -0.26907554268836975, -0.5376840829849243, -0.0464097298681736, 0.6657370328903198, 0.19690127670764923, -0.5460608005523682, -0.4035342037677765, -0.23683024942874908, 0.25928452610969543, -0.30133944749832153, 0.211341992020607, -1.1206848621368408, 0.3581933379173279, -0.04225143790245056, 0.2604829967021942, 0.22864092886447906, 0.7056031823158264)
LATENTS_STD = (1.2223774194717407, 1.2767263650894165, 1.6831774711608887, 1.7549455165863037, 1.5636216402053833, 2.194143533706665, 0.9653137922286987, 1.0569885969161987, 0.841948926448822, 0.7729952931404114, 1.8955937623977661, 0.946841835975647, 0.7996809482574463, 0.44988900423049927, 0.7197399735450745, 0.6936293244361877, 2.961095094680786, 2.7694199085235596, 3.0496184825897217, 2.1088054180145264, 3.276226282119751, 3.1627357006073, 2.2816812992095947, 2.6127843856811523)


@torch.no_grad()
def upscale(video, checkpoint, width, height, *, memory_saving=False):
    """Preserve normalized latent/time axes and return a float32 spatial lift."""
    if (not isinstance(video, torch.Tensor) or video.ndim != 5 or tuple(video.shape[:2]) != (1,24)
            or not video.is_floating_point() or not bool(torch.isfinite(video).all())):
        raise ValueError('Latent upscaling requires finite batch-one, 24-channel video latents')
    if any(type(value) is not int or value % 32 for value in (width,height)):
        raise ValueError('Upscale target dimensions must be multiples of 32')
    source_h, source_w = video.shape[-2:]
    target_h, target_w = height//16, width//16
    if target_h < source_h or target_w < source_w or target_h*source_w != target_w*source_h:
        raise ValueError('Latent upscaling must preserve the aspect ratio without shrinking')
    scale = target_w/source_w
    if not 1. <= scale <= 4.:
        raise ValueError('Latent upscaler supports factors from 1 to 4')
    if scale == 1.:
        return video, dict(load_seconds=0.,compute_seconds=0.,scale=1.,noop=True)
    from .streamed_weights import _open_safetensors
    checkpoint = Path(checkpoint)
    tick = time.perf_counter()
    if checkpoint.stat().st_size != CHECKPOINT_BYTES:
        raise ValueError('Incomplete latent upscaler checkpoint')
    with checkpoint.open('rb') as stream:
        digest = hashlib.file_digest(stream,'sha256').hexdigest()
    if digest != CHECKPOINT_SHA256:
        raise ValueError('The selected file is not the supported H3 3D v1 upscaler')
    model = None
    try:
        with torch.device('meta'):
            model = LatentResizer3D(reuse_buffers=memory_saving)
        # Keep the same bounded Windows reader as the main engine; do not
        # create a new copy-on-write mapping/Commit charge for optional weights.
        with _open_safetensors(checkpoint, framework='pt', device='cpu') as handle:
            state = {name: handle.get_tensor(name) for name in handle.keys()}
        model.load_state_dict({key.removeprefix('upscaler.'):value for key,value in state.items()},
                              strict=True,assign=True)
        del state
        model = model.eval().requires_grad_(False).to(device=video.device,dtype=torch.float16)
        folded = install_folded_convolutions(model) if video.device.type == 'mps' else 0
        if video.is_cuda:torch.cuda.synchronize(video.device)
        elif video.device.type == 'mps':torch.mps.synchronize()
        loaded = time.perf_counter()-tick
        tick = time.perf_counter()
        mean = video.new_tensor(LATENTS_MEAN, dtype=torch.float16).view(1,-1,1,1,1)
        std = video.new_tensor(LATENTS_STD, dtype=torch.float16).view(1,-1,1,1,1)
        normalized = (video.to(torch.float16) - mean) / std
        result = model(normalized,scale=scale,
            target_size=(video.shape[2],target_h,target_w),enable_chunking=False)
        result = (result * std + mean).float()
        if video.is_cuda:torch.cuda.synchronize(video.device)
        elif video.device.type == 'mps':torch.mps.synchronize()
        computed = time.perf_counter()-tick
        if not bool(torch.isfinite(result).all()):
            raise RuntimeError('Latent upscaler produced nonfinite values')
        return result, dict(load_seconds=loaded,compute_seconds=computed,scale=scale,
            checkpoint_sha256=digest,temporal_chunking=False,normalized_input=True,
            buffer_reuse=memory_saving,checkpoint_transform='extra_per_channel_v1',
            folded_convolutions=folded)
    finally:
        del model
        gc.collect()
        if video.is_cuda:torch.cuda.empty_cache()
        elif video.device.type == 'mps':torch.mps.empty_cache()

FOLD_BYTES = 512 * 2**20


def _folded_conv3d(module, x):
    """A 3x3x3, padding-1 Conv3d as one conv2d over the temporal taps in channels.

    Each output frame is the same single reduction over 3 x C x 3 x 3 inputs:
    its previous, current and next frame (zeros past either end) side by side.
    MPS ran the 3D form at about 0.9 TFLOPS on an M5; the 2D form uses its
    matrix units. Frames are folded a bounded group at a time.
    """
    batch, channels, frames, height, width = x.shape
    weight = module.weight.permute(0, 2, 1, 3, 4).reshape(module.out_channels, 3 * channels, 3, 3)
    planes = x.transpose(1, 2)                                            # [B, T, C, H, W]
    out = x.new_empty((batch, module.out_channels, frames, height, width))
    group = max(1, FOLD_BYTES // max(1, 3 * channels * height * width * x.element_size()))
    for start in range(0, frames, group):
        end = min(frames, start + group)
        parts = []
        for shift in (-1, 0, 1):
            lo, hi = start + shift, end + shift
            part = planes[:, max(lo, 0):min(hi, frames)]
            pad_before, pad_after = max(0, -lo), max(0, hi - frames)
            if pad_before or pad_after:
                zero = x.new_zeros((batch, 1, channels, height, width))
                part = torch.cat([zero] * pad_before + [part] + [zero] * pad_after, dim=1)
            parts.append(part)
        stacked = torch.cat(parts, dim=2).reshape(batch * (end - start), 3 * channels, height, width)
        result = F.conv2d(stacked, weight, module.bias, padding=1)
        out[:, :, start:end] = result.view(batch, end - start, module.out_channels, height, width).transpose(1, 2)
        del parts, stacked, result
    return out


def install_folded_convolutions(model):
    """Route this model's 3x3x3 zero-padded convolutions through `_folded_conv3d`."""
    import types
    count = 0
    for module in model.modules():
        if (isinstance(module, nn.Conv3d) and module.kernel_size == (3, 3, 3) and module.stride == (1, 1, 1)
                and module.padding == (1, 1, 1) and module.dilation == (1, 1, 1) and module.groups == 1
                and module.padding_mode == 'zeros'):
            module.forward = types.MethodType(_folded_conv3d, module)
            count += 1
    return count


def normalization(channels):
    return nn.GroupNorm(32, channels)

def zero_module(module):
    for p in module.parameters():
        p.detach().zero_()
    return module

class AttnBlock3D(nn.Module):

    def __init__(self, in_channels):
        super().__init__()
        self.norm = normalization(in_channels)
        self.q = nn.Conv3d(in_channels, in_channels, 1)
        self.k = nn.Conv3d(in_channels, in_channels, 1)
        self.v = nn.Conv3d(in_channels, in_channels, 1)
        self.proj_out = nn.Conv3d(in_channels, in_channels, 1)

    def forward(self, x):
        h = self.norm(x)
        q = rearrange(self.q(h), 'b c t h w -> b 1 (t h w) c')
        k = rearrange(self.k(h), 'b c t h w -> b 1 (t h w) c')
        v = rearrange(self.v(h), 'b c t h w -> b 1 (t h w) c')
        h = F.scaled_dot_product_attention(q, k, v)
        h = rearrange(h, 'b 1 (t h w) c -> b c t h w', t=x.shape[2], h=x.shape[3], w=x.shape[4])
        return x + self.proj_out(h)

class ResBlockEmb3D(nn.Module):

    def __init__(self, channels, emb_channels, dropout=0, out_channels=None, reuse_buffers=False):
        super().__init__()
        self.out_channels = out_channels or channels
        self.reuse_buffers = reuse_buffers
        self.in_layers = nn.Sequential(normalization(channels), nn.SiLU(inplace=reuse_buffers), nn.Conv3d(channels, self.out_channels, 3, padding=1))
        self.emb_layers = nn.Sequential(nn.SiLU(), nn.Linear(emb_channels, 2 * self.out_channels))
        self.out_norm = normalization(self.out_channels)
        self.out_layers = nn.Sequential(nn.SiLU(inplace=reuse_buffers), nn.Dropout(p=dropout), zero_module(nn.Conv3d(self.out_channels, self.out_channels, 3, padding=1)))
        self.skip = nn.Conv3d(channels, self.out_channels, 1) if self.out_channels != channels else nn.Identity()

    def forward(self, x, emb):
        h = self.in_layers(x)
        emb_out = self.emb_layers(emb).type(h.dtype)
        while len(emb_out.shape) < len(h.shape):
            emb_out = emb_out[..., None]
        (scale, shift) = torch.chunk(emb_out, 2, dim=1)
        if self.reuse_buffers:
            # Preserve the original FP16 rounding after each operation. Only
            # overwrite the consumed normalization output, never the residual.
            h = self.out_norm(h)
            h.mul_(1 + scale).add_(shift)
        else:
            h = self.out_norm(h) * (1 + scale) + shift
        h = self.out_layers(h)
        return h.add_(self.skip(x)) if self.reuse_buffers else self.skip(x) + h

class TemporalConv(nn.Module):

    def __init__(self, channels, kernel_size=5, reuse_buffers=False):
        super().__init__()
        padding = kernel_size // 2
        self.norm = normalization(channels)
        self.reuse_buffers = reuse_buffers
        self.dwconv = nn.Conv3d(channels, channels, kernel_size=(kernel_size, 1, 1), padding=(padding, 0, 0), groups=channels)
        self.pwconv = nn.Conv3d(channels, channels, kernel_size=1)
        nn.init.zeros_(self.pwconv.weight)
        nn.init.zeros_(self.pwconv.bias)

    def forward(self, x):
        identity = x
        h = self.norm(x)
        h = F.silu(h, inplace=self.reuse_buffers)
        h = self.dwconv(h)
        h = self.pwconv(h)
        return h.add_(identity) if self.reuse_buffers else identity + h

class LatentResizer3D(nn.Module):

    def __init__(self, in_channels=24, in_blocks=12, out_blocks=12, channels=512, dropout=0.1, attn=False, temporal_every=2, temporal_kernel=5, reuse_buffers=False):
        super().__init__()
        self.conv_in = nn.Conv3d(in_channels, channels, 3, padding=1)
        embed_dim = 64
        self.embed = nn.Sequential(nn.Linear(1, embed_dim), nn.SiLU(), nn.Linear(embed_dim, embed_dim))
        self.in_blocks = nn.ModuleList()
        for b in range(in_blocks):
            if (b == 1 or b == in_blocks - 1) and attn:
                self.in_blocks.append(AttnBlock3D(channels))
            self.in_blocks.append(ResBlockEmb3D(channels, embed_dim, dropout, reuse_buffers=reuse_buffers))
            if temporal_every > 0 and b % temporal_every == 0:
                self.in_blocks.append(TemporalConv(channels, temporal_kernel, reuse_buffers=reuse_buffers))
        self.out_blocks = nn.ModuleList()
        for b in range(out_blocks):
            if (b == 1 or b == out_blocks - 1) and attn:
                self.out_blocks.append(AttnBlock3D(channels))
            self.out_blocks.append(ResBlockEmb3D(channels, embed_dim, dropout, reuse_buffers=reuse_buffers))
            if temporal_every > 0 and b % temporal_every == 0:
                self.out_blocks.append(TemporalConv(channels, temporal_kernel, reuse_buffers=reuse_buffers))
        self.norm_out = normalization(channels)
        self.conv_out = nn.Conv3d(channels, in_channels, 3, padding=1)

    def forward(self, x, scale=None, target_size=None, enable_chunking=True):
        if target_size is not None:
            size = target_size
        elif scale is not None:
            size = tuple((int(round(s * scale)) for s in x.shape[-3:]))
        else:
            return x
        if size == x.shape[-3:]:
            return x
        (B, C, T, H, W) = x.shape
        tk = 0
        for b in self.in_blocks:
            if isinstance(b, TemporalConv):
                tk = b.dwconv.weight.shape[2]
                break
        overlap = tk
        chunk = 32
        if not enable_chunking or T <= chunk:
            return self._forward_seg(x, scale, size)
        print(f'[MinimaxH3-3D] temporal chunking: T={T} chunks={(T + chunk - 1) // chunk} overlap={overlap}')
        x_padded = F.pad(x, (0, 0, 0, 0, overlap, overlap), mode='replicate')
        out_full = torch.zeros(B, C, T, size[-2], size[-1], device=x.device, dtype=x.dtype)
        weight_full = torch.zeros(1, 1, T, 1, 1, device=x.device, dtype=x.dtype)
        start = 0
        while start < T:
            seg_start = start
            seg_end = min(T, start + chunk)
            out_start = max(0, seg_start - overlap)
            out_end = min(T, seg_end + overlap)
            lo = max(0, out_start - overlap)
            hi = min(T + 2 * overlap, out_end + overlap)
            seg = x_padded[:, :, lo:hi].contiguous()
            seg_size = (hi - lo, size[-2], size[-1])
            seg_out = self._forward_seg(seg, scale, seg_size)
            s0 = out_start + overlap - lo
            s1 = s0 + (out_end - out_start)
            valid_out = seg_out[:, :, s0:s1]
            n_valid = out_end - out_start
            weight = torch.ones(n_valid, device=x.device, dtype=x.dtype)
            if seg_start > out_start:
                blend_len = seg_start - out_start
                weight[:blend_len] = torch.arange(1, blend_len + 1, device=x.device, dtype=x.dtype) / (blend_len + 1)
            if out_end > seg_end:
                blend_len = out_end - seg_end
                weight[-blend_len:] = torch.arange(blend_len, 0, -1, device=x.device, dtype=x.dtype) / (blend_len + 1)
            out_full[:, :, out_start:out_end] += valid_out * weight.view(1, 1, n_valid, 1, 1)
            weight_full[:, :, out_start:out_end] += weight.view(1, 1, n_valid, 1, 1)
            start += chunk
            del seg, seg_out, valid_out
            if start % (chunk * 4) == 0:
                gc.collect()
        out_full = out_full / weight_full.clamp(min=1e-08)
        return out_full

    def _forward_seg(self, x, scale, size):
        scale_emb = torch.tensor([scale - 1 if scale is not None else 0.0], dtype=x.dtype, device=x.device).unsqueeze(0)
        emb = self.embed(scale_emb)
        x = self.conv_in(x)
        for b in self.in_blocks:
            if isinstance(b, ResBlockEmb3D):
                emb_t = emb.expand(x.shape[0], -1)
                x = b(x, emb_t)
            else:
                x = b(x)
        x = F.interpolate(x, size=size, mode='trilinear', align_corners=False)
        for b in self.out_blocks:
            if isinstance(b, ResBlockEmb3D):
                emb_t = emb.expand(x.shape[0], -1)
                x = b(x, emb_t)
            else:
                x = b(x)
        x = self.norm_out(x)
        x = F.silu(x)
        x = self.conv_out(x)
        return x
