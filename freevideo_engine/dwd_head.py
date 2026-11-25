"""The fastest mode's pixel head: frames straight from the last step's features, without the video VAE.

It follows the structure of the no-decode head published with pi-H3 (the last eight
window-attention layers of a video decoder, LakonLab, Apache-2.0): features expand to a
2x2 grid of 2048-wide tokens, eight blocks of window attention (16x16 tokens, five latent
frames, every other block shifted) with eight register tokens per five-frame block, then
a projection to 4x16x16 pixels per token (one frame of 16x16 for each block's first frame).

The memory-lean order: Q/K/V are built only for the windows being processed and the
feed-forward runs in row tiles, so the peak stays near the features and one block's
activations. Weights are read from the adapter file when the head runs, after the
transformer has been released.
"""
import math

import torch
import torch.nn.functional as F

DIM, HEADS, HEAD_DIM, WINDOW, BLOCK_FRAMES, TIME_SHIFT, LAYERS = 2048, 32, 64, 16, 5, 2, 24
PATCH, TIME_PATCH, CHANNELS = 16, 4, 3
# The head's own output normalization (its pixels are in [-1, 1] before this).
PIXEL_MEAN, PIXEL_STD = (-.03, -.088, -.188), (.458, .448, .45)
PREFIX = 'dwd_pixel_refiner.'


def _rope(frames, height, width, shift, time_shift, device):
    lt = ((torch.arange(frames, device=device) + time_shift) % BLOCK_FRAMES).float()
    ly = ((torch.arange(height, device=device) + shift) % WINDOW).float()
    lx = ((torch.arange(width, device=device) + shift) % WINDOW).float()
    grid = torch.stack(torch.meshgrid((lt + .5) / 5 * 2 - 1, (ly + .5) / WINDOW * 2 - 1, (lx + .5) / WINDOW * 2 - 1,
                                      indexing='ij'), -1).reshape(-1, 3)
    inverse = 1. / 100. ** torch.arange(0, 1, 6 / 48, dtype=torch.float32, device=device)
    angles = (2 * math.pi * grid[:, :, None] * inverse).flatten(1)
    return angles.cos(), angles.sin()


def _rotate(x, cos_sin):
    cos, sin = cos_sin[0][:, None].to(x.dtype), cos_sin[1][:, None].to(x.dtype)
    half = cos.size(-1)
    a, b = x[..., :half], x[..., half:2 * half]
    return torch.cat((a * cos - b * sin, b * cos + a * sin, x[..., 2 * half:]), -1)


def _windows(frames, height, width, shift, time_shift, device):
    """Row ids (windows, tokens) of each window (-1 where none) and the window count per time block."""
    tp = -(-(frames + time_shift) // BLOCK_FRAMES) * BLOCK_FRAMES
    hp, wp = -(-(height + shift) // WINDOW) * WINDOW, -(-(width + shift) // WINDOW) * WINDOW
    ids = torch.full((tp, hp, wp), -1, device=device, dtype=torch.long)
    ids[time_shift:time_shift + frames, shift:shift + height, shift:shift + width] = torch.arange(
        frames * height * width, device=device).view(frames, height, width)
    nt, nh, nw = tp // BLOCK_FRAMES, hp // WINDOW, wp // WINDOW
    ids = ids.view(nt, BLOCK_FRAMES, nh, WINDOW, nw, WINDOW).permute(0, 2, 4, 1, 3, 5).reshape(nt * nh * nw, -1)
    return ids, nt, nh * nw


def _norm(x, weight=None, eps=1e-5):
    return F.rms_norm(x.float(), (x.shape[-1],), weight, eps).to(x.dtype)


def _attention(q, k, v, mask=None):  # (batch, tokens, heads, dim)
    return F.scaled_dot_product_attention(q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2),
                                          attn_mask=mask).transpose(1, 2)


class _Layer:
    def __init__(self, weights, index, device):
        prefix = f'{PREFIX}blocks.{index}.'
        get = lambda name: weights.pop(prefix + name).to(device, torch.bfloat16)
        self.qkv, self.qkv_bias = get('attn.to_qkv.weight'), get('attn.to_qkv.bias')
        self.out, self.out_bias = get('attn.to_out.weight'), get('attn.to_out.bias')
        self.w1, self.w1_bias, self.w2, self.w2_bias = get('ff.w1.weight'), get('ff.w1.bias'), get('ff.w2.weight'), get('ff.w2.bias')
        self.norm1, self.norm2 = (weights.pop(prefix + name + '.weight').to(device, torch.float32) for name in ('norm1', 'norm2'))
        self.scale1, self.scale2 = get('scale1'), get('scale2')


class Head:
    def __init__(self, weights, device, *, window_batch=8, tile=4096):
        """weights: the adapter's tensors whose names start with dwd_pixel_refiner. (consumed)."""
        self.layers = [_Layer(weights, index, device) for index in range(8)]
        get = lambda name: weights.pop(PREFIX + name).to(device, torch.bfloat16)
        self.registers = get('register_tokens')
        self.expand, self.expand_bias = get('feature_expand.weight'), get('feature_expand.bias')
        self.norm_weight, self.norm_bias = (weights.pop(PREFIX + 'norm_out.' + name).to(device, torch.float32)
                                            for name in ('weight', 'bias'))
        self.project, self.project_bias = get('proj_out.weight'), get('proj_out.bias')
        self.first, self.first_bias = get('first_frame_proj_out.weight'), get('first_frame_proj_out.bias')
        self.device, self.window_batch, self.tile = device, window_batch, tile

    def _layer(self, layer, depth, x, registers, frames, height, width, cache):
        count = frames * height * width
        blocks, per_block = registers.shape[:2]
        shift, time_shift = (WINDOW // 2, TIME_SHIFT) if depth % 2 else (0, 0)
        flat_registers = registers.flatten(0, 1)

        def qkv(rows):
            z = F.linear(_norm(rows, layer.norm1), layer.qkv, layer.qkv_bias).view(-1, HEADS, 3, HEAD_DIM)
            return _norm(z[:, :, 0]), _norm(z[:, :, 1]), z[:, :, 2]
        rope = cache.setdefault((shift, time_shift), _rope(frames, height, width, shift, time_shift, self.device))

        def take(ids):  # rotary on the tokens only
            q, k, v = qkv(x[ids])
            cos_sin = (rope[0][ids], rope[1][ids])
            return _rotate(q, cos_sin), _rotate(k, cos_sin), v
        gq, gk, gv = qkv(flat_registers)
        # Register tokens attend to their own five-frame block and its registers.
        out_registers = []
        for block in range(blocks):
            a, z = block * BLOCK_FRAMES * height * width, min((block + 1) * BLOCK_FRAMES * height * width, count)
            keys, values = [], []
            for start in range(a, z, 8192):
                _, k, v = take(torch.arange(start, min(start + 8192, z), device=self.device))
                keys.append(k)
                values.append(v)
            keys.append(gk[block * per_block:(block + 1) * per_block])
            values.append(gv[block * per_block:(block + 1) * per_block])
            out_registers.append(_attention(gq[block * per_block:(block + 1) * per_block][None],
                                            torch.cat(keys)[None], torch.cat(values)[None])[0])
            del keys, values
        out_registers = torch.cat(out_registers).flatten(1)
        # Window attention; a window's keys end with the registers of the time blocks it overlaps.
        key = (shift, time_shift, 'windows')
        if key not in cache:
            ids, nt, per = _windows(frames, height, width, shift, time_shift, self.device)
            spans = [(max(t * BLOCK_FRAMES - time_shift, 0) // BLOCK_FRAMES,
                      (min((t + 1) * BLOCK_FRAMES - time_shift, frames) - 1) // BLOCK_FRAMES) for t in range(nt)]
            register_ids = torch.tensor([[b * per_block + m for b in pair for m in range(per_block)] for pair in spans],
                                        device=self.device)
            register_valid = torch.tensor([[True] * per_block + [p1 != p0] * per_block for p0, p1 in spans],
                                          device=self.device)
            cache[key] = (ids, register_ids.repeat_interleave(per, 0), register_valid.repeat_interleave(per, 0))
        ids, register_ids, register_valid = cache[key]
        out = torch.zeros(count, HEADS * HEAD_DIM, dtype=x.dtype, device=self.device)
        for start in range(0, len(ids), self.window_batch):
            rows = ids[start:start + self.window_batch]
            present = rows >= 0
            flat = rows.clamp(min=0)
            q, k, v = take(flat.reshape(-1))
            tokens = flat.shape[1]
            q, k, v = (t.view(len(rows), tokens, HEADS, HEAD_DIM) for t in (q, k, v))
            picked = register_ids[start:start + self.window_batch]
            k = torch.cat((k, gk[picked]), 1)
            v = torch.cat((v, gv[picked]), 1)
            mask = torch.cat((present, register_valid[start:start + self.window_batch]), 1)
            o = _attention(q, k, v, mask[:, None, None, :]).flatten(2)
            out[rows[present]] = o[present]
            del q, k, v, o
        x = x + F.linear(out, layer.out, layer.out_bias) * layer.scale1
        del out
        registers = flat_registers + F.linear(out_registers, layer.out, layer.out_bias) * layer.scale1

        def feed_forward(h):
            gate, up = F.linear(_norm(h, layer.norm2), layer.w1, layer.w1_bias).chunk(2, -1)
            return F.linear(F.silu(gate) * up, layer.w2, layer.w2_bias)
        for start in range(0, len(x), self.tile):
            x[start:start + self.tile] += feed_forward(x[start:start + self.tile]) * layer.scale2
        registers = registers + feed_forward(registers) * layer.scale2
        return x, registers.view(blocks, per_block, DIM)

    @torch.no_grad()
    def __call__(self, features, grid):
        """features (frames * h * w, 5376) on the head's device; grid (latent frames, h, w). Float pixels per block."""
        frames, h, w = grid
        height, width = h * 2, w * 2
        x = torch.cat([F.linear(features[a:a + 8192].to(torch.bfloat16), self.expand, self.expand_bias)
                       for a in range(0, len(features), 8192)])
        x = x.view(frames, h, w, 2, 2, DIM).permute(0, 1, 3, 2, 4, 5).reshape(frames * height * width, DIM)
        blocks = -(-frames // BLOCK_FRAMES)
        registers = torch.cat((self.registers[0], self.registers.new_zeros(1, DIM))).unsqueeze(0).expand(blocks, -1, -1).contiguous()
        cache = {}
        for depth, layer in enumerate(self.layers, LAYERS - 8):
            x, registers = self._layer(layer, depth, x, registers, frames, height, width, cache)
        for a in range(0, frames, BLOCK_FRAMES):
            c = F.layer_norm(x[a * height * width:(a + BLOCK_FRAMES) * height * width].float(), (DIM,),
                             self.norm_weight, self.norm_bias, 1e-5).to(torch.bfloat16).view(-1, height, width, DIM)
            n = len(c)
            parts = [F.linear(c[:1], self.first, self.first_bias).view(height, width, CHANNELS, 1, PATCH, PATCH).permute(
                3, 2, 0, 4, 1, 5).reshape(1, CHANNELS, height * PATCH, width * PATCH)]
            if n > 1:
                parts.append(F.linear(c[1:], self.project, self.project_bias).view(
                    n - 1, height, width, CHANNELS, TIME_PATCH, PATCH, PATCH).permute(0, 4, 3, 1, 5, 2, 6).reshape(
                    (n - 1) * TIME_PATCH, CHANNELS, height * PATCH, width * PATCH))
            yield torch.cat(parts).float()


def render(features, grid, adapter, device):
    """uint8 frames (count, height, width, 3) on the CPU and the head's timings and peak."""
    import time
    from .fast_tier_runtime import adapter_tensors
    started = time.perf_counter()
    weights = adapter_tensors(adapter, lambda name: name.startswith(PREFIX))
    torch.cuda.reset_peak_memory_stats(device)
    head = Head(weights, device)
    loaded = time.perf_counter()
    mean = torch.tensor(PIXEL_MEAN, device=device).view(3, 1, 1)
    std = torch.tensor(PIXEL_STD, device=device).view(3, 1, 1)
    frames = []
    for pixels in head(features.to(device), grid):
        frames.append((((pixels * std + mean).clamp(-1, 1) * .5 + .5) * 255).round().byte().permute(0, 2, 3, 1).cpu())
    torch.cuda.synchronize(device)
    result = torch.cat(frames)
    report = dict(head_load_seconds=round(loaded - started, 3), head_seconds=round(time.perf_counter() - loaded, 3),
                  head_peak_reserved_bytes=torch.cuda.max_memory_reserved(device),
                  head_peak_allocated_bytes=torch.cuda.max_memory_allocated(device), frames=len(result))
    del head, weights
    return result, report
