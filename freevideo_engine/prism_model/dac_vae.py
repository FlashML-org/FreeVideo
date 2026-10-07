# DAC audio VAE decoder (Descript Audio Codec, MIT; continuous-latent variant
# from MOVA, Apache-2.0), as used by Prism (MIT, Tencent). Vendored from
# Prism-fast 3910631 (hymm/models/modules/dac_vae.py). FreeVideo keeps the
# decoder and post-quant convolution of the continuous model with weight norm
# already removed (``use_weight_norm: false`` in the MOVA config); the encoder,
# codebooks, audiotools codec helpers and training losses are not needed.
import json
import math
from pathlib import Path
from typing import List

import torch
from torch import nn


def _snake(x, alpha):
    shape = x.shape
    x = x.reshape(shape[0], shape[1], -1)
    x = x + (alpha + 1e-9).reciprocal() * torch.sin(alpha * x).pow(2)
    return x.reshape(shape)


try:  # scripting speeds this up 1.4x in the original code
    snake = torch.jit.script(_snake)
except Exception:  # pragma: no cover - TorchScript unavailable
    snake = _snake


class Snake1d(nn.Module):
    def __init__(self, channels):
        super().__init__()
        self.alpha = nn.Parameter(torch.ones(1, channels, 1))

    def forward(self, x):
        return snake(x, self.alpha)


class ResidualUnit(nn.Module):
    def __init__(self, dim: int = 16, dilation: int = 1):
        super().__init__()
        pad = ((7 - 1) * dilation) // 2
        self.block = nn.Sequential(
            Snake1d(dim), nn.Conv1d(dim, dim, kernel_size=7, dilation=dilation, padding=pad),
            Snake1d(dim), nn.Conv1d(dim, dim, kernel_size=1))

    def forward(self, x):
        y = self.block(x)
        pad = (x.shape[-1] - y.shape[-1]) // 2
        if pad > 0:
            x = x[..., pad:-pad]
        return x + y


class DecoderBlock(nn.Module):
    def __init__(self, input_dim: int = 16, output_dim: int = 8, stride: int = 1):
        super().__init__()
        self.block = nn.Sequential(
            Snake1d(input_dim),
            nn.ConvTranspose1d(input_dim, output_dim, kernel_size=2 * stride, stride=stride,
                               padding=math.ceil(stride / 2), output_padding=stride % 2),
            ResidualUnit(output_dim, dilation=1), ResidualUnit(output_dim, dilation=3),
            ResidualUnit(output_dim, dilation=9))

    def forward(self, x):
        return self.block(x)


class Decoder(nn.Module):
    def __init__(self, input_channel, channels, rates, d_out: int = 1):
        super().__init__()
        layers = [nn.Conv1d(input_channel, channels, kernel_size=7, padding=3)]
        for i, stride in enumerate(rates):
            layers += [DecoderBlock(channels // 2**i, channels // 2 ** (i + 1), stride)]
        output_dim = channels // 2 ** len(rates)
        layers += [Snake1d(output_dim), nn.Conv1d(output_dim, d_out, kernel_size=7, padding=3), nn.Tanh()]
        self.model = nn.Sequential(*layers)

    def forward(self, x):
        return self.model(x)


class DACDecoder(nn.Module):
    """``DAC.decode`` of the continuous MOVA audio VAE (post_quant_conv + decoder)."""

    def __init__(self, encoder_dim: int = 64, encoder_rates: List[int] = (2, 4, 8, 8), latent_dim: int = None,
                 decoder_dim: int = 1536, decoder_rates: List[int] = (8, 8, 4, 2), sample_rate: int = 44100,
                 continuous: bool = False, use_weight_norm: bool = True, **_):
        super().__init__()
        if not continuous or use_weight_norm:
            raise ValueError('Prism uses the continuous DAC variant with weight norm removed')
        if latent_dim is None:
            latent_dim = encoder_dim * (2 ** len(encoder_rates))
        self.latent_dim = latent_dim
        self.sample_rate = sample_rate
        self.hop_length = int(math.prod(encoder_rates))
        self.post_quant_conv = nn.Conv1d(latent_dim, latent_dim, 1)
        self.decoder = Decoder(latent_dim, decoder_dim, list(decoder_rates))

    @property
    def dtype(self):
        return self.post_quant_conv.weight.dtype

    def decode(self, z: torch.Tensor):
        return self.decoder(self.post_quant_conv(z))

    @classmethod
    def load(cls, directory, device='cpu', dtype=torch.bfloat16):
        from safetensors.torch import load_file
        directory = Path(directory)
        config = {k: v for k, v in json.loads((directory / 'config.json').read_text(encoding='utf-8')).items()
                  if not k.startswith('_')}
        with torch.device('meta'):
            model = cls(**config)
        state = {k: v for k, v in load_file(str(directory / 'diffusion_pytorch_model.safetensors'), device='cpu').items()
                 if k.startswith(('decoder.', 'post_quant_conv.'))}
        model.load_state_dict(state, strict=True, assign=True)
        return model.to(device=device, dtype=dtype).eval().requires_grad_(False)
