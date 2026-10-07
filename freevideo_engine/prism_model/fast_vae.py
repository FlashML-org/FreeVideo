# Fast paths for the official Wan2.1 video VAE (diffusers AutoencoderKLWan, Apache-2.0)
# from the Prism single-GPU research branch (MIT, Tencent): Prism-fast 0befcb7,
# hymm/fast/vae.py, "Official-VAE speedups" section (WanFastDecoder,
# fast_decode_frames, frames_from_video, WanFastEncoder, encode_i2v_condition,
# fast_i2v_encode). The light/tiny decoders of that file are not vendored. See NOTICE.
"""Faster decode (2.9-3.6x, ~half the memory; ``low_vram`` time slicing at ~5.5 GiB
for 720p x 205) and the I2V condition encode shortcut, on the official weights."""
import torch
import torch.nn as nn
import torch.nn.functional as F

# =====================================================================================================
# Official Wan2.1 VAE (diffusers AutoencoderKLWan): faster decode, low-VRAM decode, I2V condition encode.
#
# These run the *official* weights of ``pipe.video_vae`` (no extra checkpoint); they re-implement the
# diffusers decoder loop (same ops, same causal-cache semantics) with:
#   * channels_last_3d (NDHWC) activations and conv weights, so cuDNN runs its native NHWC kernels;
#   * one fused Triton kernel for RMS_norm + SiLU (fp32 math, bf16 out, exactly the autocast path's math)
#     that writes straight into the causal-conv input buffer behind the 2 cached frames, so the
#     torch.cat(cache, x) + F.pad copies and the fp32 intermediates of the autocast path disappear;
#     spatial zero padding is done by the conv itself;
#   * nearest upsampling in bf16 (bit-identical to diffusers' fp32 round trip);
#   * the output written into one preallocated [B, 3, F, H, W] tensor (on the GPU or in host memory);
#   * optional depth-first time splitting (``max_frames``): after the temporal upsamplers every stage
#     processes at most ``max_frames`` frames at a time, carrying the causal caches -- the same math in a
#     different order, which caps activation memory (the low-VRAM mode).
# =====================================================================================================

_CL3 = torch.channels_last_3d

try:
    import triton
    import triton.language as tl

    @triton.jit
    def _rms_silu_kernel(X, Y, G, Bias, n_rows, scale, eps, C: tl.constexpr, BLOCK_C: tl.constexpr,
                         ROWS: tl.constexpr, SILU: tl.constexpr, HAS_BIAS: tl.constexpr):
        pid = tl.program_id(0)
        rows = pid * ROWS + tl.arange(0, ROWS)
        cols = tl.arange(0, BLOCK_C)
        cmask = cols < C
        mask = (rows[:, None] < n_rows) & cmask[None, :]
        offs = rows[:, None].to(tl.int64) * C + cols[None, :]
        x = tl.load(X + offs, mask=mask, other=0.0).to(tl.float32)
        if HAS_BIAS:  # the producing conv's bias, folded in (conv ran with bias=None)
            x = x + tl.load(Bias + cols, mask=cmask, other=0.0).to(tl.float32)[None, :]
            x = tl.where(mask, x, 0.0)
        d = tl.maximum(tl.sqrt(tl.sum(x * x, axis=1)), eps)
        g = tl.load(G + cols, mask=cmask, other=0.0).to(tl.float32)
        y = x / d[:, None] * scale * g[None, :]
        if SILU:
            y = y / (1.0 + tl.exp(-y))
        tl.store(Y + offs, y.to(Y.dtype.element_ty), mask=mask)

    @triton.jit
    def _add_res_kernel(Y, H, B1, B2, n_rows, C: tl.constexpr, BLOCK_C: tl.constexpr, ROWS: tl.constexpr,
                        HAS_B2: tl.constexpr):
        """Y = Y + B1 + H (+ B2), fp32 math: conv2 output + its bias + the (shortcut) residual."""
        pid = tl.program_id(0)
        rows = pid * ROWS + tl.arange(0, ROWS)
        cols = tl.arange(0, BLOCK_C)
        cmask = cols < C
        mask = (rows[:, None] < n_rows) & cmask[None, :]
        offs = rows[:, None].to(tl.int64) * C + cols[None, :]
        y = tl.load(Y + offs, mask=mask, other=0.0).to(tl.float32)
        y += tl.load(B1 + cols, mask=cmask, other=0.0).to(tl.float32)[None, :]
        y += tl.load(H + offs, mask=mask, other=0.0).to(tl.float32)
        if HAS_B2:
            y += tl.load(B2 + cols, mask=cmask, other=0.0).to(tl.float32)[None, :]
        tl.store(Y + offs, y.to(Y.dtype.element_ty), mask=mask)

    _HAVE_TRITON = True
except Exception:  # noqa: BLE001
    _HAVE_TRITON = False


def _rows_view_ok(t):
    """True if the [B, C, T, H, W] tensor is one dense NDHWC block (rows of C contiguous elements)."""
    B, C, T, H, W = t.shape
    return t.stride() == (T * H * W * C, 1, H * W * C, W * C, C) or (B == 1 and t.stride()[1:] == (1, H * W * C, W * C, C))


def rms_silu(x, gamma, scale, out=None, silu=True, eps=1e-12, bias=None):
    """WanRMS_norm over channels (+ SiLU) of (x + bias) [B, C, T, H, W] in fp32 math -> bf16, like the
    autocast path (F.normalize in fp32, * scale * gamma, SiLU in fp32, cast at the next conv).  ``out`` may
    be a view (e.g. the tail of a causal-conv input buffer); both must be dense NDHWC."""
    if out is None:
        out = torch.empty_like(x, memory_format=_CL3)
    if _HAVE_TRITON and x.is_cuda and _rows_view_ok(x) and _rows_view_ok(out):
        C = x.shape[1]
        n_rows = x.numel() // C
        BLOCK_C = triton.next_power_of_2(C)
        ROWS = max(1, 8192 // BLOCK_C)
        _rms_silu_kernel[(triton.cdiv(n_rows, ROWS),)](x, out, gamma, gamma if bias is None else bias, n_rows,
                                                        float(scale), eps, C=C, BLOCK_C=BLOCK_C, ROWS=ROWS,
                                                        SILU=silu, HAS_BIAS=bias is not None, num_warps=4)
        return out
    xf = x.float() if bias is None else x.float() + bias.float().view(1, -1, 1, 1, 1)
    y = F.normalize(xf, dim=1) * scale * gamma.float().view(1, -1, 1, 1, 1)
    if silu:
        y = F.silu(y)
    out.copy_(y)
    return out


def add_res_(y, h, b1, b2=None):
    """In place y = y + b1 + h (+ b2) (fp32 math)."""
    if _HAVE_TRITON and y.is_cuda and _rows_view_ok(y) and _rows_view_ok(h):
        C = y.shape[1]
        n_rows = y.numel() // C
        BLOCK_C = triton.next_power_of_2(C)
        ROWS = max(1, 8192 // BLOCK_C)
        _add_res_kernel[(triton.cdiv(n_rows, ROWS),)](y, h, b1, b1 if b2 is None else b2, n_rows, C=C,
                                                       BLOCK_C=BLOCK_C, ROWS=ROWS, HAS_B2=b2 is not None, num_warps=4)
        return y
    v = y.float() + b1.float().view(1, -1, 1, 1, 1) + h.float()
    if b2 is not None:
        v = v + b2.float().view(1, -1, 1, 1, 1)
    return y.copy_(v)


class _CConv:
    """A WanCausalConv3d: kernel_t 3 keeps the last 2 input frames (zeros before the first frame)."""

    def __init__(self, conv, cl):
        w = conv.weight.detach()
        self.w = w.contiguous(memory_format=_CL3) if cl else w.contiguous()
        self.b = None if conv.bias is None else conv.bias.detach()
        p = conv._padding  # (w, w, h, h, 2 * t, 0)
        self.pad = (0, p[2], p[0])
        self.kt = w.shape[2]
        self.stride = conv.stride
        self.cl = cl
        self.hist = None

    def buffer(self, x_like, C=None):
        """Input buffer [B, C, 2 + T, H, W]: history in [:, :, :2], caller fills [:, :, 2:]."""
        B, C0, T, H, W = x_like.shape
        C = C or C0
        buf = torch.empty((B, C, T + 2, H, W), dtype=x_like.dtype, device=x_like.device,
                          memory_format=_CL3 if self.cl else torch.contiguous_format)
        if self.hist is None:
            buf[:, :, :2].zero_()
        else:
            buf[:, :, :2].copy_(self.hist)
        return buf

    def run(self, buf, bias=True):
        y = F.conv3d(buf, self.w, self.b if bias else None, self.stride, self.pad)
        self.hist = buf[:, :, -2:].clone()
        return y

    def plain(self, x, bias=True):  # kernel_t == 1
        return F.conv3d(x, self.w, self.b if bias else None, self.stride, self.pad)


class _Res:
    def __init__(self, rb, cl):
        self.g1, self.s1 = rb.norm1.gamma.detach().flatten().float(), rb.norm1.scale
        self.g2, self.s2 = rb.norm2.gamma.detach().flatten().float(), rb.norm2.scale
        self.c1, self.c2 = _CConv(rb.conv1, cl), _CConv(rb.conv2, cl)
        self.sc = None if isinstance(rb.conv_shortcut, nn.Identity) else _CConv(rb.conv_shortcut, cl)

    def reset(self):
        self.c1.hist = self.c2.hist = None

    def __call__(self, x):
        fold = _HAVE_TRITON and x.is_cuda and self.c1.cl
        h = x if self.sc is None else self.sc.plain(x, bias=not fold)
        buf = self.c1.buffer(x)
        rms_silu(x, self.g1, self.s1, out=buf[:, :, 2:])
        y = self.c1.run(buf, bias=not fold)
        del buf
        buf = self.c2.buffer(y)
        rms_silu(y, self.g2, self.s2, out=buf[:, :, 2:], bias=self.c1.b if fold else None)
        del y
        y = self.c2.run(buf, bias=not fold)
        del buf
        if fold:
            return add_res_(y, h, self.c2.b, None if self.sc is None else self.sc.b)
        return y.add_(h)


class _Attn:
    def __init__(self, attn, cl):
        self.m = attn
        self.cl = cl

    def reset(self):
        pass

    def __call__(self, x):
        with torch.autocast("cuda", dtype=x.dtype):
            y = self.m(x.contiguous())
        return y.contiguous(memory_format=_CL3) if self.cl else y.contiguous()


def _subpixel_weight(w):
    """Nearest-2x upsample followed by a 3x3 conv (pad 1) == a 3x3 conv on the low-res input with 4x the output
    channels (one per output phase) + pixel_shuffle.  Phase a of an output row reads low-res rows {i-1: W0,
    i: W1+W2} (a=0) or {i: W0+W1, i+1: W2} (a=1); the same for columns.  Weights summed in fp32."""
    Co, Ci = w.shape[:2]
    wf = w.float()
    R = torch.tensor([[[1, 0, 0], [0, 1, 1], [0, 0, 0]], [[0, 0, 0], [1, 1, 0], [0, 0, 1]]], dtype=torch.float32,
                     device=w.device)  # R[a][low-res tap, original tap]
    k = torch.einsum("akr,blc,oirc->oabikl", R, R, wf)  # [Co, 2, 2, Ci, 3, 3]
    return k.reshape(Co * 4, Ci, 3, 3).to(w.dtype)


class _Up:
    def __init__(self, rs, cl, subpixel=False):
        self.mode = rs.mode
        self.tc = _CConv(rs.time_conv, cl) if rs.mode == "upsample3d" else None
        conv = rs.resample[1]
        w = _subpixel_weight(conv.weight.detach()) if subpixel else conv.weight.detach()
        self.w = w.contiguous(memory_format=torch.channels_last) if cl else w
        self.b = conv.bias.detach().repeat_interleave(4) if subpixel else conv.bias.detach()
        self.subpixel = subpixel
        self.cl = cl
        self.started = False

    def reset(self):
        self.started = False
        if self.tc is not None:
            self.tc.hist = None

    def __call__(self, x):
        B, C, T, H, W = x.shape
        if self.tc is not None:
            if not self.started:
                self.started = True  # diffusers' "Rep": the first (single-frame) chunk is not upsampled in time
            else:
                buf = self.tc.buffer(x)
                buf[:, :, 2:].copy_(x)
                y = self.tc.run(buf)  # [B, 2C, T, H, W]
                del buf
                if self.cl:
                    y = y.permute(0, 2, 3, 4, 1).reshape(B, T, H, W, 2, C).permute(0, 1, 4, 2, 3, 5)
                    x = y.reshape(B, 2 * T, H, W, C).permute(0, 4, 1, 2, 3)
                else:
                    y = y.reshape(B, 2, C, T, H, W)
                    x = torch.stack((y[:, 0], y[:, 1]), 3).reshape(B, C, 2 * T, H, W)
                del y
                T = 2 * T
        if self.cl:
            x4 = x.permute(0, 2, 3, 4, 1).reshape(B * T, H, W, C).permute(0, 3, 1, 2)
        else:
            x4 = x.permute(0, 2, 1, 3, 4).reshape(B * T, C, H, W)
        if self.subpixel:
            y = F.pixel_shuffle(F.conv2d(x4, self.w, self.b, padding=1), 2)
            if self.cl:
                y = y.contiguous(memory_format=torch.channels_last)
        else:
            u = F.interpolate(x4, scale_factor=2.0, mode="nearest-exact")
            del x4
            y = F.conv2d(u, self.w, self.b, padding=1)
            del u
        C2 = y.shape[1]
        if self.cl:
            return y.permute(0, 2, 3, 1).reshape(B, T, 2 * H, 2 * W, C2).permute(0, 4, 1, 2, 3)
        return y.view(B, T, C2, 2 * H, 2 * W).permute(0, 2, 1, 3, 4).contiguous()


class _Head:
    def __init__(self, dec, cl):
        self.g, self.s = dec.norm_out.gamma.detach().flatten().float(), dec.norm_out.scale
        self.c = _CConv(dec.conv_out, cl)

    def reset(self):
        self.c.hist = None

    def __call__(self, x):
        buf = self.c.buffer(x)
        rms_silu(x, self.g, self.s, out=buf[:, :, 2:])
        y = self.c.run(buf)
        return y


class _ConvIn:
    def __init__(self, conv, cl):
        self.c = _CConv(conv, cl)

    def reset(self):
        self.c.hist = None

    def __call__(self, x):
        buf = self.c.buffer(x)
        buf[:, :, 2:].copy_(x)
        return self.c.run(buf)


class WanFastDecoder:
    """Fast / low-VRAM replacement for ``AutoencoderKLWan.decode(z).sample`` (bf16 autocast) on the same
    official weights.  Build once per VAE (holds channels-last copies of the decoder conv weights, ~150 MB):

        fd = WanFastDecoder(pipe.video_vae)
        video = fd.decode(pipe.denormalize_video_latents(latents))              # fast, GPU output
        frames = fd.decode_frames(z)              # == postprocess_video(decode(z), "pil"), no video tensor
        video = fd.decode(z, chunk=1, max_frames=1, out_device="cpu")           # low-VRAM mode

    Measured on an H200, 720p x 205 (fast_tests/vae_official_speed.py): official 11.8 s / +17.7 GiB;
    this 3.9 s / +9.8 GiB (chunk 2), PSNR 63.1 dB vs the official decode (the official bf16 decode is
    61.5 dB from an fp32 decode, this 61.2 dB); low-VRAM 4.8 s / +5.5 GiB; fp16 3.7 s (60.8 dB vs the
    official, 68.8 dB vs fp32).

    ``chunk``: latent frames per step after the first (2 is the sweet spot: 3-4 cost memory, no speed).
    ``max_frames``: cap on frames per stage step (depth-first); 1 = least memory.
    ``out_device``: where the [B, 3, F, H, W] output lives ("cpu" uses pinned host memory).
    ``dtype``: torch.float16 runs a converted copy (faster cuDNN kernels, closer to fp32, further from bf16).
    ``subpixel``: the nearest-2x upsample + 3x3 conv runs as a 4-phase 3x3 conv + pixel_shuffle on the
    low-res input (same math, weights pre-summed in fp32; -2% time, 63.1 vs 63.2 dB).
    Batch > 1 works but falls back to unfused norms for the time-sliced buffers.
    """

    def __init__(self, vae, channels_last=True, dtype=None, benchmark=True, subpixel=True):
        self.vae = vae
        dec = vae.decoder
        cl = channels_last
        self.cl = cl
        self.benchmark = benchmark
        self.dtype = dtype or dec.conv_in.weight.dtype
        if self.dtype != dec.conv_in.weight.dtype:
            # run on a converted copy of the decoder (e.g. fp16: faster cuDNN kernels, more mantissa than bf16)
            import copy
            dec = copy.deepcopy(dec).to(self.dtype)
            self._dec_copy = dec
        pq = vae.post_quant_conv
        self.pq_w, self.pq_b = pq.weight.detach().to(self.dtype), pq.bias.detach().to(self.dtype)
        ops = [_ConvIn(dec.conv_in, cl), _Res(dec.mid_block.resnets[0], cl), _Attn(dec.mid_block.attentions[0], cl),
               _Res(dec.mid_block.resnets[1], cl)]
        for ub in dec.up_blocks:
            ops += [_Res(r, cl) for r in ub.resnets]
            if ub.upsamplers is not None:
                ops.append(_Up(ub.upsamplers[0], cl, subpixel))
        ops.append(_Head(dec, cl))
        self.ops = ops

    def _run(self, i, x, emit, max_frames):
        ops = self.ops
        while i < len(ops):
            if max_frames and x.shape[2] > max_frames:
                for sub in x.split(max_frames, dim=2):
                    self._run(i, sub, emit, max_frames)
                return
            x = ops[i](x)
            i += 1
        emit(x)

    @torch.no_grad()
    def decode_frames(self, z, chunk=2, max_frames=None, pil=True):
        """Decode straight to the frames ``video_processor.postprocess_video(decode(z), "pil")`` produces
        (bit-identical uint8: (x * 0.5 + 0.5).clamp(0, 1) in the VAE dtype, * 255 in fp32, round half to
        even), converted on the GPU chunk by chunk into pinned host memory -- no [B, 3, F, H, W] video
        tensor on the GPU and no fp32 numpy pass on the CPU.  Returns a list (per batch item) of lists of
        PIL images, or of uint8 arrays [F, H, W, 3] with ``pil=False``."""
        B, _, T, h, w = z.shape
        F_out, H, W = 1 + 4 * (T - 1), 8 * h, 8 * w
        out = torch.empty((B, F_out, H, W, 3), dtype=torch.uint8, pin_memory=z.is_cuda)
        self.decode(z, chunk=chunk, max_frames=max_frames, out=out, _u8=True)
        arr = out.numpy()
        if not pil:
            return [arr[b] for b in range(B)]
        from PIL import Image
        return [[Image.fromarray(arr[b, f]) for f in range(F_out)] for b in range(B)]

    @torch.no_grad()
    def decode(self, z, chunk=2, max_frames=None, out_device=None, out=None, _u8=False):
        # cuDNN's NDHWC heuristics pick a slow sm80 kernel for the 96-channel full-res conv (6.4 ms vs 3.4 ms
        # benchmarked).  PyTorch caches the first plan per shape whatever the flag says later, so benchmark
        # is forced on for our own (NDHWC) shapes from their first call.
        prev = torch.backends.cudnn.benchmark
        torch.backends.cudnn.benchmark = self.benchmark or prev
        try:
            with torch.autocast("cuda", enabled=False):  # dtypes are explicit here (Phase 4 wraps decode in autocast)
                return self._decode(z, chunk, max_frames, out_device, out, _u8)
        finally:
            torch.backends.cudnn.benchmark = prev

    def _decode(self, z, chunk, max_frames, out_device, out, u8=False):
        B, _, T, h, w = z.shape
        for op in self.ops:
            op.reset()
        z = z.to(self.dtype)
        x = F.conv3d(z, self.pq_w, self.pq_b)
        if self.cl:
            x = x.contiguous(memory_format=_CL3)
        F_out, H, W = 1 + 4 * (T - 1), 8 * h, 8 * w
        if out is None:
            assert not u8
            dev = torch.device(out_device) if out_device is not None else z.device
            pin = dev.type == "cpu" and z.is_cuda
            out = torch.empty((B, 3, F_out, H, W), dtype=self.vae.dtype, device=dev, pin_memory=pin)
        pos = [0]

        vdt = self.vae.dtype

        def emit(y):
            n = y.shape[2]
            y = y.to(vdt).clamp_(-1, 1)
            if u8:  # out: [B, F, H, W, 3] uint8 (host); same rounding as VaeImageProcessor.postprocess
                q = ((y * 0.5 + 0.5).clamp_(0, 1).float() * 255).round_().to(torch.uint8)
                out[:, pos[0]:pos[0] + n].copy_(q.permute(0, 2, 3, 4, 1))
            else:
                out[:, :, pos[0]:pos[0] + n].copy_(y)
            pos[0] += n

        for t0 in [0] + list(range(1, T, chunk)):
            t1 = 1 if t0 == 0 else min(T, t0 + chunk)
            self._run(0, x[:, :, t0:t1], emit, max_frames)
        assert pos[0] == F_out, (pos[0], F_out)
        for op in self.ops:
            op.reset()
        return out


_FAST_DECODERS = {}


def get_fast_decoder(vae, **kw):
    key = (id(vae), tuple(sorted(kw.items())))
    fd = _FAST_DECODERS.get(key)
    if fd is None or fd.vae is not vae:
        fd = _FAST_DECODERS[key] = WanFastDecoder(vae, **kw)
    return fd


def fast_decode(vae, z, low_vram=False, **kw):
    """``vae.decode(z).sample`` (bf16 autocast) via a cached WanFastDecoder (see WanFastDecoder.decode).
    ``low_vram=True``: one frame per stage step, chunk 1, output in pinned host memory (~5.5 GiB at 720p)."""
    if low_vram:
        kw = {"chunk": 1, "max_frames": 1, "out_device": "cpu", **kw}
    return get_fast_decoder(vae).decode(z, **kw)


def fast_decode_frames(vae, z, low_vram=False, pil=True, **kw):
    """``video_processor.postprocess_video(vae.decode(z).sample, output_type="pil")`` in one go (bit-identical
    rounding), see WanFastDecoder.decode_frames."""
    if low_vram:
        kw = {"chunk": 1, "max_frames": 1, **kw}
    return get_fast_decoder(vae).decode_frames(z, pil=pil, **kw)


def frames_from_video(video, pil=True):
    """GPU version of ``VideoProcessor.postprocess_video(video, output_type="pil")`` (do_normalize=True):
    bit-identical frames, without the fp32 numpy round trip on the CPU."""
    B, _, F_, H, W = video.shape
    out = torch.empty((B, F_, H, W, 3), dtype=torch.uint8, pin_memory=video.is_cuda)
    for f0 in range(0, F_, 16):
        y = video[:, :, f0:f0 + 16]
        q = ((y * 0.5 + 0.5).clamp(0, 1).float() * 255).round_().to(torch.uint8)
        out[:, f0:f0 + y.shape[2]].copy_(q.permute(0, 2, 3, 4, 1))
    arr = out.numpy()
    if not pil:
        return [arr[b] for b in range(B)]
    from PIL import Image
    return [[Image.fromarray(arr[b, f]) for f in range(F_)] for b in range(B)]



class _Down:
    """WanResample downsample2d / downsample3d (spatial: pad right/bottom 1, 3x3 stride 2; temporal: kernel 3
    stride 2 over [last frame of the previous chunk, chunk], nothing for the first chunk)."""

    def __init__(self, rs, cl):
        self.mode = rs.mode
        conv = rs.resample[1]
        self.w = conv.weight.detach().contiguous(memory_format=torch.channels_last) if cl else conv.weight.detach()
        self.b = conv.bias.detach()
        self.cl = cl
        if rs.mode == "downsample3d":
            tw = rs.time_conv.weight.detach()
            self.tw = tw.contiguous(memory_format=_CL3) if cl else tw.contiguous()
            self.tb = rs.time_conv.bias.detach()
        self.cache = None

    def reset(self):
        self.cache = None

    def __call__(self, x):
        B, C, T, H, W = x.shape
        if self.cl:
            x4 = x.permute(0, 2, 3, 4, 1).reshape(B * T, H, W, C).permute(0, 3, 1, 2)
        else:
            x4 = x.permute(0, 2, 1, 3, 4).reshape(B * T, C, H, W)
        x4 = F.pad(x4, (0, 1, 0, 1))
        if self.cl:
            x4 = x4.contiguous(memory_format=torch.channels_last)
        y = F.conv2d(x4, self.w, self.b, stride=2)
        del x4
        H2, W2 = y.shape[-2:]
        if self.cl:
            x = y.permute(0, 2, 3, 1).reshape(B, T, H2, W2, C).permute(0, 4, 1, 2, 3)
        else:
            x = y.view(B, T, C, H2, W2).permute(0, 2, 1, 3, 4).contiguous()
        if self.mode == "downsample3d":
            if self.cache is None:
                self.cache = x[:, :, -1:].clone()
            else:
                xin = torch.cat([self.cache, x], 2)
                if self.cl:
                    xin = xin.contiguous(memory_format=_CL3)
                self.cache = x[:, :, -1:].clone()
                x = F.conv3d(xin, self.tw, self.tb, stride=(2, 1, 1))
        return x


class WanFastEncoder:
    """The official encoder (``vae.encoder`` + ``quant_conv``) with the decoder's fast ops (NDHWC, fused
    fp32 RMS_norm + SiLU, conv biases folded).  Note the official encode runs plain bf16 (no autocast, so
    its norms are bf16 math); this one does the norms in fp32 like the decoder path -- not bit-identical,
    see fast_tests/vae_i2v_encode_test.py for the difference.

        fe = WanFastEncoder(pipe.video_vae)
        params = fe.encode(video)          # [B, 2z, T, h, w] = vae._encode(video) (mean, logvar)
    """

    def __init__(self, vae, channels_last=True, benchmark=True):
        self.vae = vae
        enc = vae.encoder
        cl = channels_last
        self.cl, self.benchmark = cl, benchmark
        ops = [_ConvIn(enc.conv_in, cl)]
        for layer in enc.down_blocks:
            ops.append(_Res(layer, cl) if hasattr(layer, "conv1") else _Down(layer, cl))
        ops += [_Res(enc.mid_block.resnets[0], cl), _Attn(enc.mid_block.attentions[0], cl),
                _Res(enc.mid_block.resnets[1], cl), _Head(enc, cl)]
        self.ops = ops
        qc = vae.quant_conv
        self.q_w, self.q_b = qc.weight.detach(), qc.bias.detach()

    def reset(self):
        for op in self.ops:
            op.reset()

    @torch.no_grad()
    def step(self, x):
        """One causal chunk (frame 0 alone, then multiples of 4 frames) -> quant_conv output chunk."""
        prev = torch.backends.cudnn.benchmark
        torch.backends.cudnn.benchmark = self.benchmark or prev
        try:
            with torch.autocast("cuda", enabled=False):
                return self._step(x)
        finally:
            torch.backends.cudnn.benchmark = prev

    def _step(self, x):
        x = x.to(self.q_w.dtype)
        x = x.contiguous(memory_format=_CL3) if self.cl else x.contiguous()
        for op in self.ops:
            x = op(x)
        return F.conv3d(x.contiguous(), self.q_w, self.q_b)

    @torch.no_grad()
    def encode(self, x, chunk=4):
        assert chunk % 4 == 0 and (x.shape[2] - 1) % 4 == 0
        self.reset()
        outs = [self.step(x[:, :, :1])] + [self.step(x[:, :, t0:t0 + chunk]) for t0 in range(1, x.shape[2], chunk)]
        self.reset()
        return torch.cat(outs, 2)


_FAST_ENCODERS = {}


def get_fast_encoder(vae):
    fe = _FAST_ENCODERS.get(id(vae))
    if fe is None or fe.vae is not vae:
        fe = _FAST_ENCODERS[id(vae)] = WanFastEncoder(vae)
    return fe


# ----------------------------------------------------------------------------------------------------
# I2V condition encode shortcut.
#
# prepare_latents encodes [reference image, num_frames - 1 zero frames] with the causal encoder (frame 0
# alone, then 4-frame chunks).  Each causal conv sees 2 cached frames, so latent k depends on the input
# frames inside a finite window (~110 frames, ~28 latents, for the Wan2.1 encoder); beyond it every
# zero-chunk latent is the same constant, and the image's influence decays geometrically on the way
# (fast_tests/vae_i2v_encode_test.py).  Two shortcuts:
#   mode="exact":    encode chunk by chunk and stop as soon as a chunk's output equals the previous one
#                    bit for bit (the encoder state reached its fixed point); the rest is that latent.
#   mode="template": encode only the first ``prefix`` latents with the image and take the remaining ones
#                    from a cached encode of a mid-gray (zero) reference at the same size -- the image's
#                    influence there is below bf16 rounding (see the test for the measured error).
# ----------------------------------------------------------------------------------------------------

_I2V_TEMPLATES = {}


def _encoder_chunks(vae, first, n_zero_chunks, stop_when_constant=False, fast=False):
    """Run the encoder (with its causal cache) on ``first`` [B, 3, 1, H, W] then ``n_zero_chunks`` zero
    4-frame chunks; returns the list of quant_conv outputs (B, 2z, 1, h, w) per chunk.  ``fast`` uses
    WanFastEncoder instead of the diffusers modules."""
    zero = first.new_zeros(first.shape[0], first.shape[1], 4, first.shape[3], first.shape[4])
    outs = []
    fe = get_fast_encoder(vae) if fast else None
    if fe is not None:
        fe.reset()
    else:
        vae.clear_cache()
    try:
        for i in range(1 + n_zero_chunks):
            x = first if i == 0 else zero
            if fe is not None:
                outs.append(fe.step(x))
            else:
                vae._enc_conv_idx = [0]
                h = vae.encoder(x, feat_cache=vae._enc_feat_map, feat_idx=vae._enc_conv_idx)
                outs.append(vae.quant_conv(h))
            if stop_when_constant and i >= 2 and torch.equal(outs[-1], outs[-2]):
                break
    finally:
        if fe is not None:
            fe.reset()
        else:
            vae.clear_cache()
    return outs


@torch.no_grad()
def encode_i2v_condition(vae, image, num_frames, mode="exact", prefix=8, return_params=False, fast=False):
    """``_retrieve_latents(vae.encode(cat([image, zeros(num_frames - 1)], 2)), "argmax")`` without
    encoding the whole zero tail.  image: [B, 3, H, W] or [B, 3, 1, H, W] in [-1, 1].  Returns the
    denormalized mean [B, z, 1 + (num_frames - 1) // 4, H/8, W/8] (or the full [mean, logvar] parameters
    with ``return_params``)."""
    if image.dim() == 4:
        image = image.unsqueeze(2)
    image = image.to(vae.dtype)
    assert (num_frames - 1) % 4 == 0, num_frames
    T = 1 + (num_frames - 1) // 4
    if mode == "exact":
        outs = _encoder_chunks(vae, image, T - 1, stop_when_constant=True, fast=fast)
        params = torch.cat(outs + [outs[-1]] * (T - len(outs)), 2)
    elif mode == "template":
        prefix = max(1, min(prefix, T))
        B, _, _, H, W = image.shape
        key = (id(vae), B, H, W, T, image.dtype, image.device, fast)
        tmpl = _I2V_TEMPLATES.get(key)
        if tmpl is None:
            outs = _encoder_chunks(vae, torch.zeros_like(image), T - 1, stop_when_constant=True, fast=fast)
            tmpl = _I2V_TEMPLATES[key] = torch.cat(outs + [outs[-1]] * (T - len(outs)), 2)
        outs = _encoder_chunks(vae, image, prefix - 1, fast=fast)
        params = torch.cat(outs + [tmpl[:, :, prefix:]], 2)
    elif mode == "full":
        params = torch.cat(_encoder_chunks(vae, image, T - 1, fast=fast), 2)
    else:
        raise ValueError(mode)
    return params if return_params else params[:, :vae.config.z_dim]


_TILE_TEMPLATES = {}


@torch.no_grad()
def tiled_i2v_condition(vae, image, num_frames, prefix=8, templates=None):
    """``template`` mode with the VAE's spatial tiling, for GPUs whose untiled first-frame
    encode does not fit (12 GB: ~8.7 GiB at 720p). Same tiles, causal caches and
    blending as diffusers AutoencoderKLWan.tiled_encode of [image, zeros]; in each tile
    the image and the next ``prefix`` - 1 zero chunks are encoded and the rest of the
    zero tail is that tile shape's encode of an all-zero video (computed once per tile
    shape; ``templates``: dict to keep them). At 720p x 205: ~8 instead of 52 chunks per
    tile. image: [B, 3, H, W] or [B, 3, 1, H, W] in [-1, 1]. Returns the latent mean."""
    if image.dim() == 4:
        image = image.unsqueeze(2)
    image = image.to(vae.dtype)
    assert (num_frames - 1) % 4 == 0, num_frames
    if vae.config.patch_size is not None:
        raise NotImplementedError('patchified Wan VAEs')
    templates = _TILE_TEMPLATES if templates is None else templates
    chunks = 1 + (num_frames - 1) // 4
    prefix = max(1, min(prefix, chunks))
    B, _, _, height, width = image.shape
    ratio = vae.spatial_compression_ratio
    min_h, min_w = vae.tile_sample_min_height, vae.tile_sample_min_width
    stride_h, stride_w = vae.tile_sample_stride_height, vae.tile_sample_stride_width
    blend_h, blend_w = (min_h - stride_h) // ratio, (min_w - stride_w) // ratio
    # The first frame and one zero chunk laid out as frames 0-4 of the [B, 3, num_frames, H, W]
    # video tiled_encode slices (the convolutions may pick their algorithm by layout).
    plane = height * width
    video = torch.empty_strided((B, 3, 5, height, width), (3 * num_frames * plane, num_frames * plane, plane, width, 1),
                                dtype=image.dtype, device=image.device)
    video[:, :, :1].copy_(image)
    video[:, :, 1:].zero_()

    def encode(first, zero, count):
        vae.clear_cache()
        outs = []
        for k in range(count):
            vae._enc_conv_idx = [0]
            outs.append(vae.quant_conv(vae.encoder(first if k == 0 else zero, feat_cache=vae._enc_feat_map,
                                                   feat_idx=vae._enc_conv_idx)))
        return outs

    rows = []
    for i in range(0, height, stride_h):
        row = []
        for j in range(0, width, stride_w):
            zero = video[:, :, 1:5, i:i + min_h, j:j + min_w]
            key = (B, tuple(zero.shape[-2:]), chunks, image.dtype, image.device)
            tail = templates.get(key)
            if tail is None and prefix < chunks:
                tail = templates[key] = torch.cat(encode(zero[:, :, :1], zero, chunks), 2)
            outs = encode(video[:, :, :1, i:i + min_h, j:j + min_w], zero, prefix)
            if prefix < chunks:
                outs.append(tail[:, :, prefix:])
            row.append(torch.cat(outs, 2))
        rows.append(row)
    vae.clear_cache()
    del video
    result = []
    for i, row in enumerate(rows):
        parts = []
        for j, tile in enumerate(row):
            if i > 0:
                tile = vae.blend_v(rows[i - 1][j], tile, blend_h)
            if j > 0:
                tile = vae.blend_h(row[j - 1], tile, blend_w)
            parts.append(tile[:, :, :, :stride_h // ratio, :stride_w // ratio])
        result.append(torch.cat(parts, dim=-1))
    enc = torch.cat(result, dim=3)[:, :, :, :height // ratio, :width // ratio]
    return enc[:, :vae.config.z_dim]


class fast_i2v_encode:
    """Context manager: inside it ``vae.encode(x)`` takes the shortcut when x is [image, zeros...] (what
    prepare_latents builds for I2V without a last image) and falls back to the normal encode otherwise.

        with fast_i2v_encode(pipe.video_vae, mode="exact"):
            latents, condition = pipe.prepare_latents(img, 1, z_dim, height, width, num_frames, ...)
    """

    def __init__(self, vae, mode="exact", prefix=8, fast=False):
        self.vae, self.mode, self.prefix, self.fast = vae, mode, prefix, fast
        self.used = 0

    def __enter__(self):
        from diffusers.models.autoencoders.autoencoder_kl import AutoencoderKLOutput
        from diffusers.models.autoencoders.vae import DiagonalGaussianDistribution
        vae, orig = self.vae, self.vae.encode

        def encode(x, return_dict=True):
            if x.dim() == 5 and x.shape[2] > 1 and (x.shape[2] - 1) % 4 == 0 and not x[:, :, 1:].any():
                self.used += 1
                params = encode_i2v_condition(vae, x[:, :, :1], x.shape[2], self.mode, self.prefix, return_params=True,
                                              fast=self.fast)
                post = DiagonalGaussianDistribution(params)
                return AutoencoderKLOutput(latent_dist=post) if return_dict else (post,)
            return orig(x, return_dict=return_dict)

        vae.encode = encode
        return self

    def __exit__(self, *exc):
        del self.vae.encode
        return False



def clear_caches():
    """Drop the cached fast decoders / encoders (weight copies) and I2V templates."""
    _FAST_DECODERS.clear()
    _FAST_ENCODERS.clear()
    _I2V_TEMPLATES.clear()
