"""RDNA4 depthwise spatial convolution with FP32 accumulation and BF16 output."""
import torch
import triton
import triton.language as tl


@triton.jit
def _depthwise_5x5(X, W, Y, T: tl.constexpr, H: tl.constexpr,
                   WIDTH: tl.constexpr, C: tl.constexpr, BLOCK: tl.constexpr):
    offsets = tl.program_id(0).to(tl.int64) * BLOCK + tl.arange(0, BLOCK)
    valid = offsets < T * H * WIDTH * C
    channel = offsets % C
    pixel = offsets // C
    x = pixel % WIDTH
    y = (pixel // WIDTH) % H
    frame = pixel // (WIDTH * H)
    accum = tl.full((BLOCK,), 0, tl.float32)
    for kh in tl.static_range(5):
        for kw in tl.static_range(5):
            yy, xx = y + kh - 2, x + kw - 2
            source = ((frame * H + yy) * WIDTH + xx) * C + channel
            value = tl.load(X + source, valid & (yy >= 0) & (yy < H)
                            & (xx >= 0) & (xx < WIDTH), other=0.).to(tl.float32)
            weight = tl.load(W + channel * 25 + kh * 5 + kw,
                             valid, other=0.).to(tl.float32)
            accum = tl.fma(value, weight, accum)
    tl.store(Y + offsets, accum.to(Y.dtype.element_ty), valid)


def spatial(tokens, weight, frames, frame_size):
    """Same zero-padded 5x5 cross-correlation as H3's bias-free Conv2d.

    Padding is applied separately within each frame. Neither temporal taps nor
    activation/normalization math changes. Summation can differ from MIOpen.
    """
    height, width = frame_size
    channels = tokens.shape[-2] * tokens.shape[-1]
    if (tokens.ndim != 3 or tokens.shape[0] != frames * height * width
            or weight.shape != (channels, 1, 5, 5)
            or tokens.dtype != torch.bfloat16 or weight.dtype != tokens.dtype
            or not tokens.is_cuda or weight.device != tokens.device):
        raise ValueError('ROCm spatial convolution requires BF16 H3 tokens and 5x5 depthwise weights')
    value = tokens.reshape(frames, height * width, channels).contiguous()
    output = torch.empty_like(value)
    _depthwise_5x5[(triton.cdiv(value.numel(), 256),)](
        value, weight.contiguous(), output, frames, height, width, channels,
        BLOCK=256, num_warps=8)
    return output
