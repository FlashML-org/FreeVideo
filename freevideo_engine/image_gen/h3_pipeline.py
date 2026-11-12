"""In-process H3 Image pipeline: text encoding, residency planning, denoising, tiled decoding.

Text goes through the video model's text encoder in its short-lived worker
(``freevideo_engine encode``), so that encoder is gone from the card and the
host before the DiT is loaded. The DiT streams its int8 blocks through the
engine's ``StreamedStack``; how many stay resident comes from ``plan_image``
against the card's budget. Requests below the 4 MP floor are generated at 4 MP
with the same proportions and are not scaled back.
"""
import argparse
import tempfile
import time
from pathlib import Path

import torch

from . import h3_dit
from .h3_weights import load_h3_dit

MAX_REFERENCES = 16
# The released edit pipeline's reference sizes: the VAE copy keeps its size while the short edge is within
# [1024, 2048] and is scaled to the nearer bound otherwise; the vision copy has a short edge of exactly 1024. A
# resized side rounds to 32. References are never resized to the output size.
REFERENCE_SHORT_EDGE = (1024, 2048)
VISION_SHORT_EDGE = 1024
SIDE_MULTIPLE = 32
REFERENCE_CACHE_RECIPE = 'h3-image-reference-moments-v1'


def free_host_memory():
    """Return the request's weights to the host: the hooks make a stack a reference cycle, and torch keeps freed locked pages cached, so the next request in the same process would find them still charged."""
    import gc
    from ..torch_compat import empty_host_cache
    gc.collect()
    torch.cuda.empty_cache()
    empty_host_cache(torch)


class Cancelled(Exception):
    pass


def bound_short_edge(width, height, low, high, multiple=SIDE_MULTIPLE):
    """(width, height) a reference is resized to for one consumer (the released pipeline's rule)."""
    short = min(width, height)
    if low <= short <= high:
        return width, height
    scale = (low if short < low else high) / short
    return (max(multiple, round(width * scale / multiple) * multiple),
            max(multiple, round(height * scale / multiple) * multiple))


def reference_copies(references):
    """[(vae copy, vision copy)] of PIL references: read upright, RGB, each copy sized for its consumer."""
    from PIL import Image, ImageOps
    copies = []
    for image in references:
        image = ImageOps.exif_transpose(image).convert('RGB')
        if min(image.size) < SIDE_MULTIPLE:
            raise ValueError(f'A reference image needs at least {SIDE_MULTIPLE} pixels on each side, got {image.size}.')

        def fit(low, high):
            size = bound_short_edge(*image.size, low, high)
            return image if image.size == size else image.resize(size, Image.LANCZOS)
        copies.append((fit(*REFERENCE_SHORT_EDGE), fit(VISION_SHORT_EDGE, VISION_SHORT_EDGE)))
    return copies


def reference_sequence_rows(copies):
    """Rows the references add to the transformer's sequence: their latent patches and their vision tokens."""
    rows = 0
    for index, (vae_copy, vision_copy) in enumerate(copies):
        width, height = vae_copy.size
        rows += (height // 16 // 2) * (width // 16 // 2)
        width, height = vision_copy.size
        # The vision block with its two markers, and the label ("Picture 1: " is five tokens, "Picture 10: " six).
        rows += round(height / 32) * round(width / 32) + 2 + 4 + len(str(index + 1))
    return rows


# What the streamed-only floor must leave free for the request to be tried at all. Under an 8 GiB cap 16 photos
# (floor 7.99 GiB) ran out in the first step with 0.84 GiB held in pieces, while 4 large references (floor 6.72)
# finished; half the planner's smallest margin separates the two.
REFUSE_ALLOWANCE = 0.75 * 2**30


class ReferencesTooLarge(MemoryError):
    """The references cannot fit on this card even with every transformer block streamed."""


def card_budget(vram_gib):
    """Bytes of the card this request may use: the caller's limit, else the whole card."""
    total = torch.cuda.get_device_properties(0).total_memory
    return int(min(vram_gib * 2**30, total)) if vram_gib else total


def denoise_floor_bytes(copies, width, height):
    """The denoising stage's peak with nothing resident: the least any plan for these references needs."""
    from .image_policy import ImageShape, stage_peaks
    canvas_h, canvas_w = h3_dit.canvas_size(height, width)
    return stage_peaks(ImageShape(), 0, 0, width=canvas_w, height=canvas_h,
                       sequence_rows=reference_sequence_rows(copies))['denoise']


def _encode(prompt, *, encoder, comfy_root, comfy_python, model_paths, vram_gib, ram_gib, vision_images=()):
    """Run the shared text encoder in its own process and return the saved conditioning."""
    from .. import encode as encode_command
    import sys
    with tempfile.TemporaryDirectory(prefix='freevideo-h3img-') as directory:
        prompt_file = Path(directory) / 'prompt.txt'
        prompt_file.write_text(prompt, encoding='utf-8')
        out = Path(directory) / 'cond.pt'
        pictures = []
        for index, image in enumerate(vision_images):
            pictures.append(Path(directory) / f'picture-{index + 1}.png')
            image.save(pictures[-1], format='PNG', compress_level=1)
        encode_command.run(argparse.Namespace(
            prompt_file=prompt_file, out=out, encoder=encoder,
            comfy_root=Path(comfy_root) if comfy_root else None, model_paths=Path(model_paths) if model_paths else None,
            comfy_python=comfy_python or sys.executable, vram_gib=vram_gib, ram_gib=ram_gib, attention='auto',
            gpu_reserve_gib=None, ram_reserve_gib=None, h3_image_references=pictures))
        return torch.load(out, map_location='cpu', weights_only=False)


def reference_moments(copies, vae_path, device, *, cache=True, cache_root=None, stats=None):
    """Posterior moments ([1, 48, h, w] float32 on the CPU) of each reference's VAE copy, read from or kept in the
    input cache the video references use; the encoder loads only when one is missing."""
    import hashlib
    import numpy as np
    from .h3_vae import H3ImageEncoder, PIXEL_MEAN, PIXEL_STD
    store = None
    if cache:
        try:
            from ..media_encoding import _latent_cache
            store = _latent_cache(cache_root, Path(vae_path).parent.parent, device, recipe=REFERENCE_CACHE_RECIPE)
        except Exception:
            store = None
    out, keys, encoder, hits = [], [], None, 0
    work = Path(tempfile.mkdtemp(prefix='freevideo-h3ref-'))
    try:
        for vae_copy, _ in copies:
            pixels = np.asarray(vae_copy, dtype=np.uint8)
            pixels = pixels[:pixels.shape[0] // 16 * 16, :pixels.shape[1] // 16 * 16]
            key = 'h3-image-reference:%s:%dx%d' % (hashlib.sha256(np.ascontiguousarray(pixels).data).hexdigest(),
                                                   pixels.shape[1], pixels.shape[0])
            moments = None
            if store is not None:
                from ..media_encoding import _cached_latent
                moments = _cached_latent(store, key, work)
            if moments is None:
                if encoder is None:
                    encoder = H3ImageEncoder.load(str(vae_path), device)
                tensor = torch.from_numpy(np.ascontiguousarray(pixels)).to(device).permute(2, 0, 1)[None].float()
                mean = torch.tensor(PIXEL_MEAN, device=device).view(1, 3, 1, 1)
                std = torch.tensor(PIXEL_STD, device=device).view(1, 3, 1, 1)
                moments = encoder.moments((tensor / 255.0 - mean) / std).cpu()
                if store is not None:
                    from ..media_encoding import _remember_latent
                    _remember_latent(store, key, moments, work)
            else:
                hits += 1
            out.append(moments)
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
        del encoder
    if stats is not None:
        stats['reference_cache_hits'] = hits
    return out


def generate(prompt, width, height, seed, references=None, progress_cb=None, cancel=None, *, dit_path, vae_path,
             encoder='qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors', comfy_root=None, comfy_python=None,
             model_paths=None, vram_gib=None, ram_gib=None, resident=None, cpu_noise=False, stats=None, mapped=True,
             preview=None, enter=None, reference_cache=True, reference_cache_root=None):
    """Return a PIL image for the prompt.

    ``progress_cb(stage, done, total)`` is called with stage in encode, denoise, decode;
    ``cancel()`` returning true stops at the next step. ``mapped`` leaves the checkpoint's tensors as views of the mapped file, so a host
    with less memory than the 21 GB of weights loads them and pins what live memory allows.
    ``resident`` overrides the planned
    layer count. ``stats`` (a dict) receives timings, the plan and the peak memory.
    ``preview`` is the sampler's ``preview_callback`` (see ``step_preview``); it never changes the picture.
    ``enter(stage)`` names the stage a failure belongs to without moving the progress: the DiT loads after the
    text, so a load error would otherwise be filed under the text stage.
    """
    references = list(references or [])
    if len(references) > MAX_REFERENCES:
        raise ValueError('too_many_images')
    stats = stats if stats is not None else {}
    report = progress_cb or (lambda *a: None)

    def check():
        if cancel is not None and cancel():
            raise Cancelled()

    check()
    report('encode', 0, 1)
    copies = reference_copies(references)
    if copies and denoise_floor_bytes(copies, width, height) + REFUSE_ALLOWANCE > card_budget(vram_gib):
        # Refused before the text encoder starts: otherwise the request spends a minute or more there and runs out
        # in the first step. Filed under the reference stage, where the page asks for fewer references.
        if enter is not None:
            enter('encode')
        raise ReferencesTooLarge('the references need more memory than this card has, even with every block streamed')
    tick = time.perf_counter()
    cond = _encode(prompt, encoder=encoder, comfy_root=comfy_root, model_paths=model_paths, comfy_python=comfy_python,
                   vram_gib=vram_gib, ram_gib=ram_gib, vision_images=[vision for _, vision in copies])
    stats['encode_s'] = round(time.perf_counter() - tick, 2)
    check()
    moments = []
    if copies:
        # Encoded before the DiT loads, so the card holds only the encoder and one batch of tiles.
        if enter is not None:
            enter('encode')
        tick = time.perf_counter()
        if vram_gib:
            total = torch.cuda.get_device_properties(0).total_memory
            torch.cuda.set_per_process_memory_fraction(min(1.0, vram_gib * 2**30 / total))
        torch.cuda.reset_peak_memory_stats()
        moments = reference_moments(copies, vae_path, torch.device('cuda'), cache=reference_cache,
                                    cache_root=reference_cache_root, stats=stats)
        free_host_memory()
        stats.update(reference_encode_s=round(time.perf_counter() - tick, 2),
                     reference_encode_peak_gib=round(torch.cuda.max_memory_allocated() / 2**30, 2),
                     reference_sizes=[list(vae.size) for vae, _ in copies],
                     text_tokens=int(cond['prompt_embeds'].shape[-2]))
    report('encode', 1, 1)
    check()

    from .h3_vae import H3ImageDecoder, to_unit
    from .image_policy import ImageShape, plan_image
    from .image_stream import StreamedStack
    from PIL import Image
    torch.set_grad_enabled(False)
    device = torch.device('cuda')
    total = torch.cuda.get_device_properties(0).total_memory
    budget = card_budget(vram_gib)
    if vram_gib:
        torch.cuda.set_per_process_memory_fraction(min(1.0, budget / total))
    canvas_h, canvas_w = h3_dit.canvas_size(height, width)
    if enter is not None:
        enter('load')
    tick = time.perf_counter()
    model = load_h3_dit(str(dit_path), 'cpu', mapped=mapped)
    for name, child in model.named_children():
        if name != 'blocks':
            child.to(device)
    model.adaln_t_table = model.adaln_t_table.to(device)
    stack = StreamedStack(model.blocks, name='h3', device=device)
    shape = ImageShape.measure(list(model.blocks), None)
    shape = ImageShape(**{**shape.__dict__, 'steps': 6})
    plan = plan_image(gpu_budget_bytes=budget, width=canvas_w, height=canvas_h, shape=shape,
                      sequence_rows=reference_sequence_rows(copies))
    stack.set_resident(plan.dit_resident_layers if resident is None else resident)
    stats.update(load_s=round(time.perf_counter() - tick, 2), canvas=[canvas_h, canvas_w],
                 resident=plan.dit_resident_layers if resident is None else resident,
                 planned_peak_gib=round(plan.peak_allocated_bytes / 2**30, 2), host_mode=plan.host_mode)
    generator = torch.Generator('cpu' if cpu_noise else device).manual_seed(seed)
    # The released order: each reference's posterior sample, then the image's noise, then each reference's noise.
    from .h3_vae import reference_latents
    held = [reference_latents(m.to(device), generator, 'cpu' if cpu_noise else None) for m in moments]

    def step(i, n):
        check()
        report('denoise', i, n)

    tick = time.perf_counter()
    try:
        latents = h3_dit.sample(model, cond['prompt_embeds'], cond['text_token_tags'], canvas_h // 16, canvas_w // 16,
                                generator, noise_device='cpu' if cpu_noise else None, step_callback=step,
                                preview_callback=preview, references=held)
        torch.cuda.synchronize()
        stats['sample_s'] = round(time.perf_counter() - tick, 2)
        stats['denoise_peak_gib'] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    finally:
        stack.close()
        del model, stack
        free_host_memory()
    report('denoise', 6, 6)
    check()
    report('decode', 0, 1)
    torch.cuda.reset_peak_memory_stats()
    decoder = H3ImageDecoder.load(str(vae_path), device)
    pixels = to_unit(decoder.decode(latents.to(device)[None], normalized=True))
    image = Image.fromarray((pixels[0].clamp(0, 1) * 255).round().byte().permute(1, 2, 0).cpu().numpy())
    stats['decode_peak_gib'] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    del decoder
    free_host_memory()
    report('decode', 1, 1)
    return image
