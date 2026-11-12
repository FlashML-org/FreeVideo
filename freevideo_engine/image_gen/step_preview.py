"""A small picture of an H3 Image request after each sampling step.

After each of the six steps the sampler holds the model's prediction of the
clean latents (``rows + sigma * velocity``). A fixed per-pixel affine map from
the 24 latent channels to RGB draws it at an eighth of the canvas without the
VAE. The observer only reads the sampler's tensors and never writes to them,
so the picture is identical with previews on or off. Any failure turns
previews off for the rest of the request; the picture itself is unaffected.
"""
import io
from pathlib import Path

WEIGHTS = Path(__file__).with_name('step_preview_weights.json')
CHANNELS = 24
ROW_WIDTH = CHANNELS * 4  # 24 channels x a 2 x 2 patch
MAX_SIDE = 512
QUALITY = 80
FIRST_STEP = 1
# Base64 characters of one picture on the worker's pipe; the service drops longer lines (128 KiB).
# 16 MP pictures measured 15-30 KB as JPEG, so this is never reached in practice.
LIMIT = 96 * 1024


def load_weights(path=None):
    """(k, weight[24][3k^2], bias[3k^2]); ValueError when malformed. Same file format as the video preview's."""
    from ..frame_preview import load_weights as load
    return load(WEIGHTS if path is None else path)


def render(rows, velocity, sigma, latent_h, latent_w, weights, tensors=None, max_side=MAX_SIDE):
    """uint8 (height, width, 3) numpy picture of ``rows + sigma * velocity``; ``tensors`` caches the map on the device."""
    import torch
    k, weight, bias = weights
    lh, lw = latent_h // 2, latent_w // 2
    with torch.no_grad():
        x0 = rows.detach().float() + float(sigma) * velocity.detach().float()
        # rows are raster-ordered 2 x 2 patches with features (C, ph, pw) -> (lh*2, lw*2, C)
        x0 = x0.reshape(lh, lw, CHANNELS, 2, 2).permute(0, 3, 1, 4, 2).reshape(lh * 2 * lw * 2, CHANNELS)
        if tensors is None or tensors[0].device != x0.device:
            tensors = (torch.tensor(weight, dtype=torch.float32, device=x0.device),
                       torch.tensor(bias, dtype=torch.float32, device=x0.device))
        out = torch.addmm(tensors[1], x0, tensors[0])
        out = out.reshape(lh * 2, lw * 2, 3, k, k).permute(2, 0, 3, 1, 4).reshape(1, 3, lh * 2 * k, lw * 2 * k)
        height, width = out.shape[-2:]
        scale = max_side / max(height, width)
        if scale < 1:
            size = (max(1, round(height * scale)), max(1, round(width * scale)))
            out = torch.nn.functional.interpolate(out, size=size, mode='area')
        picture = out[0].clamp(0., 1.).mul(255.).round().to(torch.uint8).permute(1, 2, 0)
        return picture.cpu().numpy(), tensors


def encode(picture, quality=QUALITY):
    """JPEG bytes of a uint8 (height, width, 3) picture."""
    from PIL import Image
    buffer = io.BytesIO()
    Image.fromarray(picture).save(buffer, 'JPEG', quality=quality)
    return buffer.getvalue()


class Previewer:
    """The sampler's ``preview_callback``: ``send(step=, total=, width=, height=, jpeg=bytes)`` per step from FIRST_STEP.

    Never raises: the first failure (weights missing, a full card, a closed pipe) turns it off for the request.
    """
    def __init__(self, send, weights=None):
        self.send = send
        self.tensors = None
        try:
            self.weights = load_weights() if weights is None else weights
        except Exception:
            self.weights = None

    @property
    def active(self):
        return self.weights is not None

    def __call__(self, step, total, rows, velocity, sigma, latent_h, latent_w):
        if self.weights is None or step < FIRST_STEP:
            return
        try:
            if rows.ndim != 2 or rows.shape[1] != ROW_WIDTH or rows.shape[0] != (latent_h // 2) * (latent_w // 2):
                raise ValueError('unexpected rows')
            picture, self.tensors = render(rows, velocity, sigma, latent_h, latent_w, self.weights, self.tensors)
            self.send(step=step, total=total, width=picture.shape[1], height=picture.shape[0], jpeg=encode(picture))
        except Exception:
            self.weights = self.tensors = None
