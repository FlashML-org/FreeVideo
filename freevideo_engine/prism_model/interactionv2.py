# MOVA dual-tower conditional bridge (Apache-2.0, OpenMOSS), as used by Prism
# (MIT, Tencent). Vendored from Prism-fast 0befcb7
# (hymm/models/modules/interactionv2.py). FreeVideo keeps inference only: the
# sequence-parallel branches, the unused pooled-AdaLN variant and the
# cross-modal BSA option (disabled in the Prism recipe) are removed.
import os
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
from einops import rearrange

from .wan_video_dit import RMSNorm, AttentionModule


class RotaryEmbedding(nn.Module):
    inv_freq: torch.Tensor

    def __init__(self, base: float, dim: int, device=None):
        super().__init__()
        self.base = base
        self.dim = dim
        self.attention_scaling = 1.0
        inv_freq = 1.0 / (base ** (torch.arange(0, dim, 2, dtype=torch.int64, device='cpu').to(
            device=device or 'cpu', dtype=torch.float) / dim))
        self.register_buffer("inv_freq", inv_freq, persistent=False)
        self.original_inv_freq = self.inv_freq

    @torch.no_grad()
    def forward(self, x, position_ids):
        inv_freq_expanded = self.inv_freq[None, :, None].float().expand(position_ids.shape[0], -1, 1).to(x.device)
        position_ids_expanded = position_ids[:, None, :].float()
        device_type = x.device.type if isinstance(x.device.type, str) and x.device.type != "mps" else "cpu"
        with torch.autocast(device_type=device_type, enabled=False):  # Force float32
            freqs = (inv_freq_expanded.float() @ position_ids_expanded.float()).transpose(1, 2)
            emb = torch.cat((freqs, freqs), dim=-1)
            cos = emb.cos() * self.attention_scaling
            sin = emb.sin() * self.attention_scaling
        return cos.to(dtype=x.dtype), sin.to(dtype=x.dtype)


def rotate_half(x):
    x1 = x[..., : x.shape[-1] // 2]
    x2 = x[..., x.shape[-1] // 2:]
    return torch.cat((-x2, x1), dim=-1)


def _apply_rotary_pos_emb(q, k, cos, sin, position_ids=None, unsqueeze_dim=1):
    cos = cos.unsqueeze(unsqueeze_dim)
    sin = sin.unsqueeze(unsqueeze_dim)
    q_embed = (q * cos) + (rotate_half(q) * sin)
    k_embed = (k * cos) + (rotate_half(k) * sin)
    return q_embed, k_embed


_COMPILED_ROPE = None


def apply_rotary_pos_emb(q, k, cos, sin, position_ids=None, unsqueeze_dim=1):
    """torch.compile(fullgraph=True) as in the research code, compiled lazily;
    FREEVIDEO_PRISM_COMPILE=0 runs the same formula eagerly."""
    global _COMPILED_ROPE
    if os.environ.get('FREEVIDEO_PRISM_COMPILE', '1') == '0':
        return _apply_rotary_pos_emb(q, k, cos, sin, position_ids, unsqueeze_dim)
    if _COMPILED_ROPE is None:
        _COMPILED_ROPE = torch.compile(_apply_rotary_pos_emb, fullgraph=True)
    return _COMPILED_ROPE(q, k, cos, sin, position_ids, unsqueeze_dim)


class CrossModalInteractionController:
    """Interaction mapping between the visual and audio towers."""

    def __init__(self, visual_layers: int = 30, audio_layers: int = 30):
        self.visual_layers = visual_layers
        self.audio_layers = audio_layers
        self.min_layers = min(visual_layers, audio_layers)

    def get_interaction_layers(self, strategy: str = "shallow_focus") -> Dict[str, List[Tuple[int, int]]]:
        if strategy == "shallow_focus":
            interact_layers = list(range(0, min(10, self.min_layers // 3)))
        elif strategy == "distributed":
            interact_layers = list(range(0, self.min_layers, 3))
        elif strategy == "progressive":
            shallow = list(range(0, min(8, self.min_layers)))
            interact_layers = shallow + (list(range(8, self.min_layers, 3)) if self.min_layers > 8 else [])
        elif strategy == "custom":
            interact_layers = [i for i in [0, 2, 4, 6, 8, 12, 16, 20] if i < self.min_layers]
        elif strategy == "full":
            interact_layers = list(range(0, self.min_layers))
        else:
            raise ValueError(f"Unknown interaction strategy: {strategy}")
        return {'v2a': [(i, i) for i in interact_layers], 'a2v': [(i, i) for i in interact_layers]}


class ConditionalCrossAttention(nn.Module):
    def __init__(self, dim: int, kv_dim: int, num_heads: int, eps: float = 1e-6):
        super().__init__()
        self.q_dim = dim
        self.kv_dim = kv_dim
        self.num_heads = num_heads
        self.head_dim = self.q_dim // num_heads
        self.q = nn.Linear(dim, dim)
        self.k = nn.Linear(kv_dim, dim)
        self.v = nn.Linear(kv_dim, dim)
        self.o = nn.Linear(dim, dim)
        self.norm_q = RMSNorm(dim, eps=eps)
        self.norm_k = RMSNorm(dim, eps=eps)
        self.attn = AttentionModule(self.num_heads)

    def rope_q(self, q, x_freqs):
        x_cos, x_sin = x_freqs
        q_view = rearrange(q, 'b l (h d) -> b l h d', d=self.head_dim)
        q_view, _ = apply_rotary_pos_emb(q_view, q_view, x_cos.to(q_view.dtype).to(q_view.device),
                                         x_sin.to(q_view.dtype).to(q_view.device), unsqueeze_dim=2)
        return rearrange(q_view, 'b l h d -> b l (h d)')

    def rope_k(self, k, y_freqs):
        y_cos, y_sin = y_freqs
        k_view = rearrange(k, 'b l (h d) -> b l h d', d=self.head_dim)
        _, k_view = apply_rotary_pos_emb(k_view, k_view, y_cos.to(k_view.dtype).to(k_view.device),
                                         y_sin.to(k_view.dtype).to(k_view.device), unsqueeze_dim=2)
        return rearrange(k_view, 'b l h d -> b l (h d)')

    def forward(self, x: torch.Tensor, y: torch.Tensor, x_freqs: Optional[Tuple[torch.Tensor, torch.Tensor]] = None,
                y_freqs: Optional[Tuple[torch.Tensor, torch.Tensor]] = None,
                q_structure: str = "1d", k_structure: str = "1d", q_grid_size=None, k_grid_size=None):
        q = self.norm_q(self.q(x))
        k = self.norm_k(self.k(y))
        v = self.v(y)
        if x_freqs is not None:
            q = self.rope_q(q, x_freqs)
        if y_freqs is not None:
            k = self.rope_k(k, y_freqs)
        return self.o(self.attn(q, k, v))


try:  # fused inference path when the conditioner Linears are QLinear (research 0befcb7)
    from . import qblock as _qblock
except Exception:  # pragma: no cover
    _qblock = None


class ConditionalCrossAttentionBlock(nn.Module):
    """LayerNorm on the conditioning input ``y`` followed by cross-attention."""

    def __init__(self, dim: int, kv_dim: int, num_heads: int, eps: float = 1e-6, pooled_adaln: bool = False):
        super().__init__()
        if pooled_adaln:
            raise NotImplementedError('pooled_adaln bridges are not used by Prism')
        self.y_norm = nn.LayerNorm(kv_dim, eps=eps)
        self.inner = ConditionalCrossAttention(dim=dim, kv_dim=kv_dim, num_heads=num_heads, eps=eps)
        self.pooled_adaln = False

    def forward(self, x, y, x_freqs=None, y_freqs=None, video_grid_size=None, q_structure="1d", k_structure="1d",
                q_grid_size=None, k_grid_size=None):
        # QLinear conditioners: fused y_norm+quant (k/v share it) and RMSNorm+RoPE
        # passes (qblock.cond_forward); plain Linears run the code below.
        if _qblock is not None and _qblock.cond_applicable(self, x, y):
            out = _qblock.cond_forward(self, x, y, x_freqs, y_freqs, q_structure, k_structure,
                                       q_grid_size, k_grid_size)
            if out is not None:
                return out
        y = self.y_norm(y)
        return self.inner(x=x, y=y, x_freqs=x_freqs, y_freqs=y_freqs, q_structure=q_structure,
                          k_structure=k_structure, q_grid_size=q_grid_size, k_grid_size=k_grid_size)


class DualTowerConditionalBridge(nn.Module):
    def __init__(self, visual_layers: int = 30, audio_layers: int = 30, visual_hidden_dim: int = 3072,
                 audio_hidden_dim: int = 1536, audio_fps: float = 44100.0 / 2048.0, head_dim: int = 128,
                 interaction_strategy: str = "shallow_focus", apply_cross_rope: bool = False,
                 apply_first_frame_bias_in_rope: bool = False, trainable_condition_scale: bool = False,
                 pooled_adaln: bool = False, **_):
        super().__init__()
        self.visual_hidden_dim = visual_hidden_dim
        self.audio_hidden_dim = audio_hidden_dim
        self.audio_fps = audio_fps
        self.head_dim = head_dim
        self.apply_cross_rope = apply_cross_rope
        self.apply_first_frame_bias_in_rope = apply_first_frame_bias_in_rope
        self.trainable_condition_scale = trainable_condition_scale
        if trainable_condition_scale:
            self.condition_scale = nn.Parameter(torch.tensor([1.0], dtype=torch.float32))
        else:
            self.condition_scale = 1.0
        self.controller = CrossModalInteractionController(visual_layers, audio_layers)
        self.interaction_mapping = self.controller.get_interaction_layers(interaction_strategy)
        self.audio_to_video_conditioners = nn.ModuleDict()
        self.video_to_audio_conditioners = nn.ModuleDict()
        self.rotary = RotaryEmbedding(base=10000.0, dim=head_dim)
        for v_layer, _ in self.interaction_mapping['a2v']:
            self.audio_to_video_conditioners[str(v_layer)] = ConditionalCrossAttentionBlock(
                dim=visual_hidden_dim, kv_dim=audio_hidden_dim, num_heads=visual_hidden_dim // head_dim)
        for a_layer, _ in self.interaction_mapping['v2a']:
            self.video_to_audio_conditioners[str(a_layer)] = ConditionalCrossAttentionBlock(
                dim=audio_hidden_dim, kv_dim=visual_hidden_dim, num_heads=audio_hidden_dim // head_dim,
                pooled_adaln=pooled_adaln)

    @torch.no_grad()
    def build_aligned_freqs(self, video_fps: float, grid_size: Tuple[int, int, int], audio_steps: int,
                            device: Optional[torch.device] = None, dtype: Optional[torch.dtype] = None):
        """Aligned RoPE (cos, sin) for video tokens [1, f*h*w, head_dim] and audio
        tokens [1, audio_steps, head_dim], positions in audio-step units."""
        f_v, h, w = grid_size
        L_v = f_v * h * w
        L_a = int(audio_steps)
        device = device or self.rotary.inv_freq.device
        dtype = dtype or torch.float32
        audio_pos = torch.arange(L_a, device=device, dtype=torch.float32).unsqueeze(0)
        # FIXME(dhyu): hard-coded VAE temporal stride = 4
        if self.apply_first_frame_bias_in_rope:
            video_effective_fps = float(video_fps) / 4.0
            if f_v > 0:
                t_starts = torch.zeros((f_v,), device=device, dtype=torch.float32)
                if f_v > 1:
                    t_starts[1:] = (1.0 / float(video_fps)) + torch.arange(
                        f_v - 1, device=device, dtype=torch.float32) * (1.0 / video_effective_fps)
            else:
                t_starts = torch.zeros((0,), device=device, dtype=torch.float32)
            video_pos_per_frame = t_starts * float(self.audio_fps)
        else:
            scale = float(self.audio_fps) / float(video_fps / 4.0)
            video_pos_per_frame = torch.arange(f_v, device=device, dtype=torch.float32) * scale
        video_pos = video_pos_per_frame.repeat_interleave(h * w).unsqueeze(0)
        dummy_v = torch.zeros((1, L_v, self.head_dim), device=device, dtype=dtype)
        dummy_a = torch.zeros((1, L_a, self.head_dim), device=device, dtype=dtype)
        cos_v, sin_v = self.rotary(dummy_v, position_ids=video_pos)
        cos_a, sin_a = self.rotary(dummy_a, position_ids=audio_pos)
        return (cos_v, sin_v), (cos_a, sin_a)
