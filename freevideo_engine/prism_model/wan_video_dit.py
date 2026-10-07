# Wan2.1/2.2 video DiT (Apache-2.0, Wan team), as modified for MOVA (Apache-2.0,
# OpenMOSS) and Prism (MIT, Tencent). Vendored for FreeVideo from the Prism
# single-GPU research branch (Prism-fast 0befcb7, hymm/models/modules/
# wan_video_dit.py). FreeVideo changes: inference only -- the sequence-parallel,
# FSDP, gradient-checkpoint, USP/yunchang, FA3/kernel-hub and training-only
# sparse-attention variants (audio/variance guidance, bias rectification,
# layer-adaptive blocks) are removed; the dense attention fallback is FA2 when
# installed, otherwise PyTorch SDPA. The arithmetic of the kept paths is
# unchanged. See NOTICE in this directory.
import math
import os
from typing import Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from einops import rearrange
from torch.nn import RMSNorm

from .block_sparse_attention import flash_attn_bsa_3d

try:  # fused inference path for blocks whose Linears are QLinear
    from . import qblock as _qblock
except Exception:  # pragma: no cover - Triton missing: original path only
    _qblock = None

_DYN_BLOCK_FWD_ID = 0


def advance_dynamic_block_pass_id():
    """Call once at the start of each real outer forward."""
    global _DYN_BLOCK_FWD_ID
    _DYN_BLOCK_FWD_ID += 1
    return _DYN_BLOCK_FWD_ID


def current_dynamic_block_pass_id():
    return _DYN_BLOCK_FWD_ID


try:
    import flash_attn
    FLASH_ATTN_2_AVAILABLE = os.environ.get('FREEVIDEO_PRISM_FA2', '1') != '0'
except Exception:  # ModuleNotFoundError or a broken binary wheel
    FLASH_ATTN_2_AVAILABLE = False


def flash_attention(q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, num_heads: int, compatibility_mode=False):
    """Dense attention on [B, S, H*D] tensors (FA2 when available, else SDPA)."""
    if FLASH_ATTN_2_AVAILABLE and not compatibility_mode and q.is_cuda:
        q = rearrange(q, "b s (n d) -> b s n d", n=num_heads)
        k = rearrange(k, "b s (n d) -> b s n d", n=num_heads)
        v = rearrange(v, "b s (n d) -> b s n d", n=num_heads)
        x = flash_attn.flash_attn_func(q, k, v)
        return rearrange(x, "b s n d -> b s (n d)", n=num_heads)
    q = rearrange(q, "b s (n d) -> b n s d", n=num_heads)
    k = rearrange(k, "b s (n d) -> b n s d", n=num_heads)
    v = rearrange(v, "b s (n d) -> b n s d", n=num_heads)
    x = F.scaled_dot_product_attention(q, k, v)
    return rearrange(x, "b n s d -> b s (n d)", n=num_heads)


def _modulate(x: torch.Tensor, shift: torch.Tensor, scale: torch.Tensor):
    return (x * (1 + scale) + shift)


_COMPILED_MODULATE = None


def modulate(x: torch.Tensor, shift: torch.Tensor, scale: torch.Tensor):
    """The research code wraps this in torch.compile(fullgraph=True). Compiled
    lazily so importing never needs a compiler; FREEVIDEO_PRISM_COMPILE=0 runs
    it eagerly (same formula; elementwise rounding may differ in the last bit)."""
    global _COMPILED_MODULATE
    if os.environ.get('FREEVIDEO_PRISM_COMPILE', '1') == '0':
        return _modulate(x, shift, scale)
    if _COMPILED_MODULATE is None:
        _COMPILED_MODULATE = torch.compile(_modulate, fullgraph=True)
    return _COMPILED_MODULATE(x, shift, scale)


def sinusoidal_embedding_1d(dim, position):
    sinusoid = torch.outer(position.type(torch.float64), torch.pow(
        10000, -torch.arange(dim//2, dtype=torch.float64, device=position.device).div(dim//2)))
    x = torch.cat([torch.cos(sinusoid), torch.sin(sinusoid)], dim=1)
    return x.to(position.dtype)


def precompute_freqs_cis_3d(dim: int, end: int = 1024, theta: float = 10000.0):
    f_freqs_cis = precompute_freqs_cis(dim - 2 * (dim // 3), end, theta)
    h_freqs_cis = precompute_freqs_cis(dim // 3, end, theta)
    w_freqs_cis = precompute_freqs_cis(dim // 3, end, theta)
    return f_freqs_cis, h_freqs_cis, w_freqs_cis


def precompute_freqs_cis(dim: int, end: int = 1024, theta: float = 10000.0):
    # Explicit CPU: the model tree may be built under torch.device('meta').
    freqs = 1.0 / (theta ** (torch.arange(0, dim, 2, device='cpu')
                   [: (dim // 2)].double() / dim))
    freqs = torch.outer(torch.arange(end, device=freqs.device), freqs)
    freqs_cis = torch.polar(torch.ones_like(freqs), freqs)  # complex64
    return freqs_cis


# Chunked (over heads) evaluation of the float64 RoPE: elementwise, hence
# bit-identical, but the fp64 temporaries shrink to ~PRISM_ROPE_CHUNK_ELEMS.
_ROPE_CHUNK_ELEMS = int(os.environ.get("PRISM_ROPE_CHUNK_ELEMS", str(1 << 26)))

try:  # one-pass double-float RoPE (+ fused q/k RMSNorm); PRISM_FAST_ROPE=0 disables
    from . import rope as _fast_rope
except Exception:  # pragma: no cover - no Triton
    _fast_rope = None


def norm_rope(norm, x, freqs, head_dim):
    """rope_apply_head_dim(norm(x), freqs, head_dim); on CUDA one fused Triton pass
    (rope.py: RMSNorm + double-float RoPE, in place on x), as the research path."""
    if _fast_rope is not None and _fast_rope.supported(x, freqs, head_dim):
        p = _fast_rope.norm_params(norm)
        if p is not None and (p[0] is None or p[0].dtype == x.dtype):
            return _fast_rope.fused_norm_rope(x, p[0], p[1], freqs, head_dim, out=x)
        return _fast_rope.rope(norm(x), freqs, head_dim)
    return rope_apply_head_dim(norm(x), freqs, head_dim)


def _rope_apply_head_dim_chunked(x, freqs, head_dim, out=None):
    B, S = x.shape[0], x.shape[1]
    x4 = x.reshape(B, S, -1, head_dim)
    n = x4.shape[2]
    if out is None:
        out = torch.empty(x4.shape, dtype=x.dtype, device=x.device)
    else:
        out = out.view(x4.shape)
    hc = max(1, min(n, _ROPE_CHUNK_ELEMS // max(1, B * S * head_dim)))
    for h0 in range(0, n, hc):
        h1 = min(n, h0 + hc)
        xc = torch.view_as_complex(x4[:, :, h0:h1].to(torch.float64).reshape(B, S, h1 - h0, -1, 2))
        out[:, :, h0:h1] = torch.view_as_real(xc * freqs).flatten(3).to(x.dtype)
        del xc
    return out.view(B, S, n * head_dim)


@torch.amp.autocast('cuda', enabled=False)
def rope_apply_head_dim(x, freqs, head_dim, inplace=False):
    """inplace=True overwrites x (each head chunk is read in fp64 before it is
    written back), which keeps one projection-sized tensor instead of two."""
    if _fast_rope is not None and _fast_rope.supported(x, freqs, head_dim):
        return _fast_rope.rope(x, freqs, head_dim, out=x if inplace else None)
    if inplace or (_ROPE_CHUNK_ELEMS > 0 and x.dim() == 3 and x.numel() > _ROPE_CHUNK_ELEMS
                   and not (torch.is_grad_enabled() and x.requires_grad)):
        return _rope_apply_head_dim_chunked(x, freqs, head_dim, out=x if inplace else None)
    x = rearrange(x, "b s (n d) -> b s n d", d=head_dim)
    x_out = torch.view_as_complex(x.to(torch.float64).reshape(x.shape[0], x.shape[1], x.shape[2], -1, 2))
    x_out = torch.view_as_real(x_out * freqs).flatten(2)
    return x_out.to(x.dtype)


class AttentionModule(nn.Module):
    def __init__(self, num_heads):
        super().__init__()
        self.num_heads = num_heads

    def forward(self, q, k, v):
        return flash_attention(q=q, k=k, v=v, num_heads=self.num_heads)


class SelfAttention(nn.Module):
    def __init__(self, dim: int, num_heads: int, eps: float = 1e-6,
                 enable_bsa: bool = False, bsa_params: dict = None):
        super().__init__()
        self.dim = dim
        self.num_heads = num_heads
        self.head_dim = dim // num_heads

        self.q = nn.Linear(dim, dim)
        self.k = nn.Linear(dim, dim)
        self.v = nn.Linear(dim, dim)
        self.o = nn.Linear(dim, dim)
        self.norm_q = RMSNorm(dim, eps=eps)
        self.norm_k = RMSNorm(dim, eps=eps)

        self.attn = AttentionModule(self.num_heads)
        self.enable_bsa = enable_bsa
        self.bsa_params = bsa_params or {}
        # Anisotropic dynamic block shape (IVPQ = Section 8.5, penalty = 8.6).
        self.enable_ivpq_dynamic_block = False
        self.enable_penalty_dynamic_block = False

    _bsa_fallback_logged = False

    def _check_bsa(self, grid_size):
        if not self.enable_bsa:
            return False, None
        if grid_size is None:
            return False, "grid_size is None"
        T, H, W = grid_size
        if T <= 1:
            return False, f"T={T} <= 1 (image mode)"
        return True, None

    def _use_dynamic(self):
        return self.enable_ivpq_dynamic_block or self.enable_penalty_dynamic_block

    def _run_bsa_dynamic(self, q_bhsd, k_bhsd, v_bhsd, grid_size, audio_token_norms=None):
        """Dynamic-block BSA with internal padding to the 8-token zone grid."""
        from .block_sparse_attention.dynamic_block_attention import flash_attn_bsa_3d_dynamic
        from .block_sparse_attention.dynamic_block_shape import ZONE_SIZE

        T, H, W = grid_size
        B, n_heads, S, D = q_bhsd.shape
        pad_t = (ZONE_SIZE - T % ZONE_SIZE) % ZONE_SIZE
        pad_h = (ZONE_SIZE - H % ZONE_SIZE) % ZONE_SIZE
        pad_w = (ZONE_SIZE - W % ZONE_SIZE) % ZONE_SIZE
        need_pad = pad_t > 0 or pad_h > 0 or pad_w > 0

        valid_mask = None
        audio_norms_p = audio_token_norms
        if need_pad:
            T_p, H_p, W_p = T + pad_t, H + pad_h, W + pad_w
            idx_t = torch.arange(T_p, device=q_bhsd.device)
            idx_h = torch.arange(H_p, device=q_bhsd.device)
            idx_w = torch.arange(W_p, device=q_bhsd.device)
            valid_mask = ((idx_t[:, None, None] < T) & (idx_h[None, :, None] < H)
                          & (idx_w[None, None, :] < W)).reshape(-1).contiguous()

            def _pad_3d(t):
                t_3d = t.view(B, n_heads, T, H, W, D)
                return F.pad(t_3d, (0, 0, 0, pad_w, 0, pad_h, 0, pad_t)).reshape(B, n_heads, -1, D).contiguous()

            q_bhsd, k_bhsd, v_bhsd = _pad_3d(q_bhsd), _pad_3d(k_bhsd), _pad_3d(v_bhsd)
            if audio_token_norms is not None:
                audio_norms_p = F.pad(audio_token_norms.reshape(T, H, W), (0, pad_w, 0, pad_h, 0, pad_t),
                                      value=0.0).reshape(-1).contiguous()
            grid_padded = (T_p, H_p, W_p)
        else:
            grid_padded = grid_size

        out = flash_attn_bsa_3d_dynamic(
            q_bhsd, k_bhsd, v_bhsd, grid_padded,
            method="ivpq" if self.enable_ivpq_dynamic_block else "penalty",
            sparsity=self.bsa_params.get('sparsity', 0.9375),
            cdf_threshold=self.bsa_params.get('cdf_threshold', None),
            audio_token_norms=audio_norms_p,
            valid_mask=valid_mask,
            lambda_a=self.bsa_params.get('dynamic_block_lambda_a', 0.5),
            tau_128=self.bsa_params.get('dynamic_block_tau_128', 0.15),
            lambda_128=self.bsa_params.get('dynamic_block_lambda_128', 1.0),
        )
        if need_pad:
            out = out.view(B, n_heads, T_p, H_p, W_p, D)[:, :, :T, :H, :W, :].contiguous().reshape(
                B, n_heads, T * H * W, D)
        return out

    def _fused_dynamic_bsa(self, q, k, v, grid_size, audio_token_norms=None, out=None):
        """Fused dynamic-block BSA on [B, S, H*D] projections (ivpq_fast);
        None when not applicable. ``out`` may alias q (see ivpq_fast)."""
        if self.training or (torch.is_grad_enabled() and (q.requires_grad or k.requires_grad or v.requires_grad)):
            return None
        if not self._use_dynamic():
            return None
        from . import ivpq_fast
        if not ivpq_fast.fused_enabled():
            return None
        return ivpq_fast.fused_dynamic_bsa(
            q, k, v, grid_size, self.num_heads,
            method="ivpq" if self.enable_ivpq_dynamic_block else "penalty",
            sparsity=self.bsa_params.get('sparsity', 0.9375),
            cdf_threshold=self.bsa_params.get('cdf_threshold', None),
            audio_token_norms=audio_token_norms,
            lambda_a=self.bsa_params.get('dynamic_block_lambda_a', 0.5),
            tau_128=self.bsa_params.get('dynamic_block_tau_128', 0.15),
            lambda_128=self.bsa_params.get('dynamic_block_lambda_128', 1.0),
            out=out,
            sol=self.bsa_params.get('sol'),             # None -> PRISM_SOL env (Sage kernel only)
            sol_beta=self.bsa_params.get('sol_beta'),   # None -> PRISM_SOL_BETA env (top-k if unset)
        )

    def _run_bsa(self, q_bhsd, k_bhsd, v_bhsd, grid_size, audio_token_norms=None):
        """Dynamic-block BSA when enabled, otherwise uniform 4x4x4 BSA with
        internal padding / masking / cropping."""
        if self._use_dynamic():
            return self._run_bsa_dynamic(q_bhsd, k_bhsd, v_bhsd, grid_size, audio_token_norms=audio_token_norms)
        T, H, W = grid_size
        cq = self.bsa_params.get('chunk_3d_shape_q', [4, 4, 4])
        ck = self.bsa_params.get('chunk_3d_shape_k', [4, 4, 4])
        chunk_t, chunk_h, chunk_w = max(cq[0], ck[0]), max(cq[1], ck[1]), max(cq[2], ck[2])
        pad_t = (chunk_t - T % chunk_t) % chunk_t
        pad_h = (chunk_h - H % chunk_h) % chunk_h
        pad_w = (chunk_w - W % chunk_w) % chunk_w
        B, n_heads, S, D = q_bhsd.shape
        need_pad = pad_t > 0 or pad_h > 0 or pad_w > 0
        valid_mask = None
        grid_padded = grid_size
        if need_pad:
            T_p, H_p, W_p = T + pad_t, H + pad_h, W + pad_w
            idx_t = torch.arange(T_p, device=q_bhsd.device)
            idx_h = torch.arange(H_p, device=q_bhsd.device)
            idx_w = torch.arange(W_p, device=q_bhsd.device)
            valid_mask = ((idx_t[:, None, None] < T) & (idx_h[None, :, None] < H)
                          & (idx_w[None, None, :] < W)).reshape(-1).contiguous()

            def _pad_3d(t):
                t_3d = t.view(B, n_heads, T, H, W, D)
                return F.pad(t_3d, (0, 0, 0, pad_w, 0, pad_h, 0, pad_t)).reshape(B, n_heads, -1, D).contiguous()

            q_bhsd, k_bhsd, v_bhsd = _pad_3d(q_bhsd), _pad_3d(k_bhsd), _pad_3d(v_bhsd)
            grid_padded = (T_p, H_p, W_p)
        keys = ('sparsity', 'cdf_threshold', 'chunk_3d_shape_q', 'chunk_3d_shape_k')
        bsa_kwargs = {k: v for k, v in self.bsa_params.items() if k in keys}
        out = flash_attn_bsa_3d(q_bhsd, k_bhsd, v_bhsd, grid_padded, grid_padded, valid_mask=valid_mask, **bsa_kwargs)
        if need_pad:
            out = out.view(B, n_heads, T_p, H_p, W_p, D)[:, :, :T, :H, :W, :].contiguous().reshape(
                B, n_heads, T * H * W, D)
        return out

    def audio_norms(self, grid_size, a2v_bridge_residual):
        """Per-token ||a2v residual||_2 for the dynamic block shape decision, or
        None when this layer does not use it. A caller may compute this early
        and drop the residual (pass ``audio_token_norms=`` to forward)."""
        use_bsa, _ = self._check_bsa(grid_size)
        if not (use_bsa and self._use_dynamic()) or a2v_bridge_residual is None:
            return None
        with torch.no_grad():
            return a2v_bridge_residual.norm(dim=-1)[0]

    def forward(self, x, freqs, grid_size=None, a2v_bridge_residual=None, timestep_ratio=None,
                audio_token_norms=None):
        q = norm_rope(self.norm_q, self.q(x), freqs, self.head_dim)
        k = norm_rope(self.norm_k, self.k(x), freqs, self.head_dim)
        v = self.v(x)

        use_bsa, fallback_reason = self._check_bsa(grid_size)
        if self.enable_bsa and not use_bsa and not SelfAttention._bsa_fallback_logged:
            print(f"[BSA] Falling back to full attention: {fallback_reason} (grid_size={grid_size})")
            SelfAttention._bsa_fallback_logged = True
        if audio_token_norms is None:
            audio_token_norms = self.audio_norms(grid_size, a2v_bridge_residual)

        out_q = q if (q.is_contiguous() and os.environ.get("PRISM_BSA_OUT_IN_Q", "1") != "0") else None
        x = self._fused_dynamic_bsa(q, k, v, grid_size, audio_token_norms, out=out_q) if use_bsa else None
        if x is None and use_bsa:
            q_bsa = rearrange(q, "b s (n d) -> b n s d", n=self.num_heads).contiguous()
            k_bsa = rearrange(k, "b s (n d) -> b n s d", n=self.num_heads).contiguous()
            v_bsa = rearrange(v, "b s (n d) -> b n s d", n=self.num_heads).contiguous()
            x = rearrange(self._run_bsa(q_bsa, k_bsa, v_bsa, grid_size, audio_token_norms=audio_token_norms),
                          "b n s d -> b s (n d)")
        elif x is None:
            x = self.attn(q, k, v)
        return self.o(x)


class CrossAttention(nn.Module):
    def __init__(self, dim: int, num_heads: int, eps: float = 1e-6, has_image_input: bool = False):
        super().__init__()
        self.dim = dim
        self.num_heads = num_heads
        self.head_dim = dim // num_heads

        self.q = nn.Linear(dim, dim)
        self.k = nn.Linear(dim, dim)
        self.v = nn.Linear(dim, dim)
        self.o = nn.Linear(dim, dim)
        self.norm_q = RMSNorm(dim, eps=eps)
        self.norm_k = RMSNorm(dim, eps=eps)
        self.has_image_input = has_image_input
        if has_image_input:
            self.k_img = nn.Linear(dim, dim)
            self.v_img = nn.Linear(dim, dim)
            self.norm_k_img = RMSNorm(dim, eps=eps)
        self.attn = AttentionModule(self.num_heads)

    def forward(self, x: torch.Tensor, y: torch.Tensor):
        if self.has_image_input:
            img = y[:, :257]
            ctx = y[:, 257:]
        else:
            ctx = y
        q = self.norm_q(self.q(x))
        k = self.norm_k(self.k(ctx))
        v = self.v(ctx)
        x = self.attn(q, k, v)
        if self.has_image_input:
            k_img = self.norm_k_img(self.k_img(img))
            v_img = self.v_img(img)
            x = x + flash_attention(q, k_img, v_img, num_heads=self.num_heads)
        return self.o(x)


class GateModule(nn.Module):
    def forward(self, x, gate, residual):
        return x + gate * residual


class DiTBlock(nn.Module):
    def __init__(self, has_image_input: bool, dim: int, num_heads: int, ffn_dim: int, eps: float = 1e-6,
                 enable_bsa: bool = False, bsa_params: dict = None):
        super().__init__()
        self.dim = dim
        self.num_heads = num_heads
        self.ffn_dim = ffn_dim
        self.self_attn = SelfAttention(dim, num_heads, eps, enable_bsa=enable_bsa, bsa_params=bsa_params)
        self.cross_attn = CrossAttention(dim, num_heads, eps, has_image_input=has_image_input)
        self.norm1 = nn.LayerNorm(dim, eps=eps, elementwise_affine=False)
        self.norm2 = nn.LayerNorm(dim, eps=eps, elementwise_affine=False)
        self.norm3 = nn.LayerNorm(dim, eps=eps)
        self.ffn = nn.Sequential(nn.Linear(dim, ffn_dim), nn.GELU(approximate='tanh'), nn.Linear(ffn_dim, dim))
        self.modulation = nn.Parameter(torch.randn(1, 6, dim) / dim**0.5)
        self.gate = GateModule()

    def forward(self, x, context, t_mod, freqs, grid_size=None, a2v_bridge_residual=None, timestep_ratio=None,
                audio_token_norms=None):
        # QLinear blocks: fused LN/modulate/quant prologues and residual/gate/GELU
        # GEMM epilogues (qblock). Plain nn.Linear blocks run the code below.
        if _qblock is not None and _qblock.applicable(self, x):
            return _qblock.dit_block_forward(self, x, context, t_mod, freqs, grid_size=grid_size,
                                             a2v_bridge_residual=a2v_bridge_residual,
                                             timestep_ratio=timestep_ratio, audio_token_norms=audio_token_norms)
        has_seq = len(t_mod.shape) == 4
        chunk_dim = 2 if has_seq else 1
        shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
            self.modulation.to(dtype=t_mod.dtype, device=t_mod.device) + t_mod).chunk(6, dim=chunk_dim)
        if has_seq:
            shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
                shift_msa.squeeze(2), scale_msa.squeeze(2), gate_msa.squeeze(2),
                shift_mlp.squeeze(2), scale_mlp.squeeze(2), gate_mlp.squeeze(2))
        input_x = modulate(self.norm1(x), shift_msa, scale_msa)
        x = self.gate(x, gate_msa, self.self_attn(input_x, freqs, grid_size=grid_size,
                                                  a2v_bridge_residual=a2v_bridge_residual,
                                                  timestep_ratio=timestep_ratio,
                                                  audio_token_norms=audio_token_norms))
        x = x + self.cross_attn(self.norm3(x), context)
        input_x = modulate(self.norm2(x), shift_mlp, scale_mlp)
        x = self.gate(x, gate_mlp, self.ffn(input_x))
        return x


class Head(nn.Module):
    def __init__(self, dim: int, out_dim: int, patch_size: Tuple[int, int, int], eps: float):
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
        shift, scale = (self.modulation.to(dtype=t_mod.dtype, device=t_mod.device) + t_mod).chunk(2, dim=1)
        return self.head(self.norm(x) * (1 + scale) + shift)


class WanModel(nn.Module):
    """Video expert. Holds the embeddings, head and (until MOVABridge takes
    them) the 40 DiT blocks. Built from the MOVA ``video_dit/config.json``."""

    def __init__(self, dim: int, in_dim: int, ffn_dim: int, out_dim: int, text_dim: int, freq_dim: int,
                 eps: float, patch_size: Tuple[int, int, int], num_heads: int, num_layers: int,
                 has_image_input: bool = False, enable_bsa: bool = False, bsa_params: dict = None, **_):
        super().__init__()
        self.dim = dim
        self.freq_dim = freq_dim
        self.has_image_input = has_image_input
        self.patch_size = patch_size
        self.patch_embedding = nn.Conv3d(in_dim, dim, kernel_size=patch_size, stride=patch_size)
        self.text_embedding = nn.Sequential(nn.Linear(text_dim, dim), nn.GELU(approximate='tanh'), nn.Linear(dim, dim))
        self.time_embedding = nn.Sequential(nn.Linear(freq_dim, dim), nn.SiLU(), nn.Linear(dim, dim))
        self.time_projection = nn.Sequential(nn.SiLU(), nn.Linear(dim, dim * 6))
        self.blocks = nn.ModuleList([
            DiTBlock(has_image_input, dim, num_heads, ffn_dim, eps, enable_bsa=enable_bsa, bsa_params=bsa_params)
            for _ in range(num_layers)])
        self.head = Head(dim, out_dim, patch_size, eps)
        self.freqs = precompute_freqs_cis_3d(dim // num_heads)

    @property
    def dtype(self):
        return self.patch_embedding.weight.dtype

    def patchify(self, x: torch.Tensor):
        x = x.contiguous(memory_format=torch.channels_last_3d)  # avoid slow_conv
        x = self.patch_embedding(x)
        grid_size = x.shape[2:]
        x = rearrange(x, 'b c f h w -> b (f h w) c').contiguous()
        return x, grid_size

    def unpatchify(self, x: torch.Tensor, grid_size):
        return rearrange(x, 'b (f h w) (x y z c) -> b c (f x) (h y) (w z)',
                         f=grid_size[0], h=grid_size[1], w=grid_size[2],
                         x=self.patch_size[0], y=self.patch_size[1], z=self.patch_size[2])
