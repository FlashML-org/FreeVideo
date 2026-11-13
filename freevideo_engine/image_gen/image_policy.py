"""Memory tiers for the image model (Qwen-Image 2.1 Turbo, ConvRot int8 files).

``policy.choose`` is written for the video model's fixed 50 layers and block
size, so the image model gets its own small planner. It decides, for one
request, how many DiT blocks and text-encoder layers stay on the GPU between
uses, whether the VAE decodes in tiles, and whether the host can hold the
weights pinned. Everything else (slots, prefetch, pinned planes) is the
engine's ``LayerOffloader``.

The numbers are measurements on a B200 under per-process memory caps, 1024x1024
text-to-image unless noted. A layer that is not resident is copied to the card
once per step (DiT) or once per request (text encoder), so what the plan
cannot keep resident turns into link traffic; ``link_floor_seconds`` reports
that bound. Nothing here was measured on a consumer card yet.
"""
from dataclasses import dataclass, field

GiB = 2**30


@dataclass(frozen=True)
class ImageShape:
    """Weight sizes of the planned model; measure() replaces these once loaded."""
    dit_blocks: int = 32
    dit_block_bytes: int = round(0.2035 * GiB)
    te_layers: int = 36
    te_layer_bytes: int = round(0.18 * GiB)
    # DiT + text encoder + embedding table as the engine pins them for full streaming.
    host_weights_bytes: int = round(14.14 * GiB)
    steps: int = 8

    @classmethod
    def measure(cls, dit_blocks, te_layers, *, total_weight_bytes=None):
        """Shape from the loaded modules' real layer lists (anything None keeps the default)."""
        def size(layer):
            return sum(t.numel() * t.element_size() for t in list(layer.parameters()) + list(layer.buffers()))
        fields = {}
        if dit_blocks:
            fields.update(dit_blocks=len(dit_blocks), dit_block_bytes=max(size(b) for b in dit_blocks))
        if te_layers:
            fields.update(te_layers=len(te_layers), te_layer_bytes=max(size(b) for b in te_layers))
        if total_weight_bytes:
            fields.update(host_weights_bytes=int(total_weight_bytes))
        return cls(**fields)


# Peak allocated bytes of each stage with nothing but the fixed parts and the two
# streaming slots resident (blocks all streamed, VAE tiled), from a 36-request
# B200 sweep over budgets 5-24 GiB, 1024^2 to 2048^2, 0-3 references. The slots
# stay allocated when every block is resident, so there is no credit for that.
# Peaks are linear in megapixels; the streamed-stage figures are within 0.1 GiB.
_ENCODE_BASE = 2.7 * GiB          # prompt length, not image size, drives this stage
_DENOISE_BASE, _DENOISE_PER_MP = 1.06 * GiB, 0.554 * GiB
# The small-image fit holds to about 4.2 MP (2048 x 2048). Beyond it the denoise
# activations grow much more slowly (int8 H3 Image, nothing resident, B200 real cap:
# 4.72 GiB at 16.8 MP where the line gives 10.4), 0.147 GiB per megapixel; each
# resident block adds its own size. Continued from the line's value at 4.2 MP.
_DENOISE_FIT_MP, _DENOISE_LARGE_PER_MP = 4.2, 0.147 * GiB
# Rows beside the image's own (H3 Image references: latent patches and vision tokens). A short sequence pays what
# the large-canvas line does per 32x32 patch; past the transformer's row slice (h3_dit.ROW_SLICE) the row-wise work
# runs in slices and only attention and the residual stream grow with the sequence. Streamed-only peaks, int8 H3
# Image at 4 MP, RTX PRO 6000 under a card cap: 3.75 GiB at 5.8k reference rows, 7.46 at 43.6k, 7.64 at 46.2k,
# 11.35 at 103k; both lines sit about 0.5 GiB above every one of them, as the image's own line does.
_SEQUENCE_ROW = _DENOISE_LARGE_PER_MP * 32 * 32 / 1e6
_SLICED_BASE, _SLICED_ROW = 1.77 * GiB, 0.065 * GiB / 1000
_DECODE_BASE, _DECODE_PER_MP = 2.10 * GiB, 0.15 * GiB
# Decoding the whole image at once instead of in tiles. A single frame keeps no
# causal cache: the whole-image decoder workspace is 3.49 GiB per megapixel
# (RTX PRO 6000, 0.26 to 4.2 MP, linear) where the cached one took 6.25.
_UNTILED_DECODE_PER_MP = 3.7 * GiB
# Decoding in 512-pixel tiles (256 overlap) keeps the decoder's padded tile edges
# out of tall pictures, where 256-pixel tiles left coloured vertical lines and a
# grid texture. Without the cache it takes 0.68-0.72 GiB more than 256-pixel tiles
# (1.22-1.28 with it, where a B200 measured 1.43); reserve 1.0 GiB. A card that
# cannot afford it decodes in 256-pixel tiles as before.
SMALL_VAE_TILE, LARGE_VAE_TILE = 256, 512
_LARGE_TILE_DECODE_EXTRA = 1.0 * GiB
# Reference images, about 1 MP each after resizing. Their tokens are recomputed at
# every step (the pipeline keeps no prefix key/value cache: it would hold about
# 2 GiB per reference on the card, and the extra compute is about 1 s per image on
# a B200). The first reference pays for the vision tower and one image's
# activations; more references add to the encode stage faster than to the others.
_REF_ENCODE_FLOOR = 0.25 * GiB
_REF_ENCODE_PER_MP, _REF_ENCODE_OFFSET = 0.7 * GiB, 0.5 * GiB
_REF_DENOISE_PER_MP = 0.6 * GiB
_REF_DECODE_PER_MP = 0.15 * GiB
# Encoding a reference into latents (VAE on the card, one image at a time, in 256-pixel
# tiles like the reference pipeline): 1.86-2.07 GiB with nothing resident, then the
# resident weights on top; flat in the reference's size. Real-cap runs, budgets 6-12 GiB.
# Untiled it peaked 4 GiB higher, which is why this stage once set the residency.
_REF_VAE_ENCODE_BASE = 2.25 * GiB

MIN_MARGIN = 1.5 * GiB            # allocator fragmentation, stage hand-over, driver overhead
MARGIN_FRACTION = 0.06
MIN_HOST_WORKING = 4 * GiB        # beyond the weights: images, tokenizer, the process itself

# Apple GPUs draw on the host's memory, where the weights are mapped file pages the
# system can drop. Planning against half of it kept a 24 GB M5 with a browser open
# out of heavy swapping; planning against what was free at the time let the second
# request keep 16 encoder layers resident and swap 4 GB. A Mac below 16 GB cannot
# plan even with every block streamed (4 GiB), and closing programs does not change it.
UNIFIED_SHARE = 0.5
UNIFIED_MIN_TOTAL = 15 * GiB      # 16 GB Macs report exactly 16 GiB


def unified_budget(total_bytes):
    """What a request on an Apple GPU plans against, from the machine's total memory."""
    return int(total_bytes * UNIFIED_SHARE)


@dataclass(frozen=True)
class ImagePlan:
    dit_resident_layers: int      # on the card while denoising
    te_resident_layers: int
    vae_tiling: bool
    host_mode: str                # 'pinned' | 'pageable' | 'insufficient'
    feasible: bool
    peak_allocated_bytes: int     # largest stage
    stage_peaks: dict
    margin_bytes: int
    dit_streamed_bytes_per_step: int
    te_streamed_bytes: int
    link_floor_seconds: object
    reasons: list = field(default_factory=list)
    vae_tile_size: int = SMALL_VAE_TILE
    # Of those, kept on the card through the other stages and between requests;
    # the rest are released after denoising and filled again by the next one.
    dit_kept_layers: int = None

    def to_dict(self):
        return dict(self.__dict__)


def stage_peaks(shape, dit_resident, te_resident, *, width, height, reference_pixels=0, vae_tiling=True,
                vae_tile_size=SMALL_VAE_TILE, dit_kept=None, sequence_rows=0):
    """Largest allocation of each stage for a given residency (bytes).

    ``dit_kept`` blocks stay on the card outside denoising (default: all of them).
    ``sequence_rows`` are rows the transformer runs beside the image's own (H3 Image references: their latent
    patches and vision tokens); what they cost the denoising stage is described at ``_SEQUENCE_ROW``.
    """
    mp = width * height / 1e6
    ref_mp = reference_pixels / 1e6
    kept = dit_resident if dit_kept is None else min(dit_kept, dit_resident)
    resident = kept * shape.dit_block_bytes + te_resident * shape.te_layer_bytes
    encode = _ENCODE_BASE + resident
    denoise = (_DENOISE_BASE + _DENOISE_PER_MP * min(mp, _DENOISE_FIT_MP)
               + _DENOISE_LARGE_PER_MP * max(0.0, mp - _DENOISE_FIT_MP)
               + min(_SEQUENCE_ROW * sequence_rows, _SLICED_BASE + _SLICED_ROW * sequence_rows)
               + resident + (dit_resident - kept) * shape.dit_block_bytes)
    decode = _DECODE_BASE + _DECODE_PER_MP * mp + resident
    if not vae_tiling:
        decode += _UNTILED_DECODE_PER_MP * mp
    elif vae_tile_size > SMALL_VAE_TILE:
        decode += _LARGE_TILE_DECODE_EXTRA
    if ref_mp:
        encode += max(_REF_ENCODE_FLOOR, _REF_ENCODE_PER_MP * ref_mp - _REF_ENCODE_OFFSET)
        denoise += _REF_DENOISE_PER_MP * ref_mp
        decode += _REF_DECODE_PER_MP * ref_mp
    stages = dict(encode=int(encode), denoise=int(denoise), decode=int(decode))
    if ref_mp:
        stages['reference_encode'] = int(_REF_VAE_ENCODE_BASE + resident)
    return stages


def plan_image(*, gpu_budget_bytes, host_available_bytes=None, width=1024, height=1024,
               reference_pixels=0, shape=None, link_bytes_per_second=None, sequence_rows=0):
    """Choose the decoding and the residency for one request.

    The decoding comes first: whole-image decoding is several times faster than
    512-pixel tiles, and those than 256-pixel ones, far more than a resident
    block saves. Then DiT blocks for denoising, where each is read every step
    (8 times per image); of those, the ones the other stages leave room for stay
    on the card between requests, the rest are filled again on the next first
    step. Text-encoder layers, read once per request, come last and only when
    every block stays. ``host_available_bytes`` is what the host can lock or
    hold now (``offload.live_pin_capacity``); None means it was not read and
    the plan assumes it fits.
    """
    shape = shape or ImageShape()
    margin = int(max(MIN_MARGIN, MARGIN_FRACTION * gpu_budget_bytes))
    limit = int(gpu_budget_bytes) - margin
    reasons = []
    kwargs = dict(width=width, height=height, reference_pixels=reference_pixels, sequence_rows=sequence_rows)

    def peaks(kd, kt, tiling, tile_size, kept=None):
        return stage_peaks(shape, kd, kt, vae_tiling=tiling, vae_tile_size=tile_size, dit_kept=kept, **kwargs)

    def fits(kd, kt, tiling, tile_size, kept=None):
        return max(peaks(kd, kt, tiling, tile_size, kept).values()) <= limit

    kd = kt = kept = 0
    tiling, tile_size = True, SMALL_VAE_TILE
    for option in ((False, LARGE_VAE_TILE), (True, LARGE_VAE_TILE), (True, SMALL_VAE_TILE)):
        if fits(0, 0, *option):
            tiling, tile_size = option
            break
    feasible = fits(0, 0, tiling, tile_size)
    if not feasible:
        reasons.append('even full streaming with a tiled VAE needs %.1f GiB beyond the %.1f GiB budget' % (
            (max(peaks(0, 0, tiling, tile_size).values()) + margin) / GiB, gpu_budget_bytes / GiB))
    else:
        kd = max(k for k in range(shape.dit_blocks + 1) if fits(k, 0, tiling, tile_size, kept=0))
        kept = max(k for k in range(kd + 1) if fits(kd, 0, tiling, tile_size, kept=k))
        # Encoder layers are read once per request, blocks eight times: only
        # when every block stays does leftover room go to the encoder.
        if kept == shape.dit_blocks:
            kt = max(k for k in range(shape.te_layers + 1) if fits(kd, k, tiling, tile_size))
    peaks_now = peaks(kd, kt, tiling, tile_size, kept)

    streamed_dit = (shape.dit_blocks - kd) * shape.dit_block_bytes
    streamed_te = (shape.te_layers - kt) * shape.te_layer_bytes
    refilled = (kd - kept) * shape.dit_block_bytes
    floor = None
    if link_bytes_per_second:
        floor = (streamed_dit * shape.steps + refilled + streamed_te) / link_bytes_per_second

    host_mode = 'pinned'
    if host_available_bytes is not None:
        need = shape.host_weights_bytes + MIN_HOST_WORKING
        if host_available_bytes >= need:
            host_mode = 'pinned'
        elif host_available_bytes >= MIN_HOST_WORKING + shape.host_weights_bytes // 2:
            host_mode = 'pageable'
            reasons.append('not enough lockable host memory for all weights; the rest is read unpinned')
        else:
            host_mode = 'insufficient'
            reasons.append('host memory cannot hold the weights (%.1f GiB needed)' % (need / GiB))
    return ImagePlan(dit_resident_layers=kd, te_resident_layers=kt, vae_tiling=tiling,
                     host_mode=host_mode, feasible=feasible and host_mode != 'insufficient',
                     peak_allocated_bytes=max(peaks_now.values()), stage_peaks=peaks_now, margin_bytes=margin,
                     dit_streamed_bytes_per_step=streamed_dit, te_streamed_bytes=streamed_te,
                     link_floor_seconds=floor, reasons=reasons, vae_tile_size=tile_size, dit_kept_layers=kept)
