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

MAX_REFERENCES = 0  # reference images are not supported by this engine yet


def free_host_memory():
    """Return the request's weights to the host: the hooks make a stack a reference cycle, and torch keeps freed locked pages cached, so the next request in the same process would find them still charged."""
    import gc
    from ..torch_compat import empty_host_cache
    gc.collect()
    torch.cuda.empty_cache()
    empty_host_cache(torch)


class Cancelled(Exception):
    pass


def _encode(prompt, *, encoder, comfy_root, comfy_python, model_paths, vram_gib, ram_gib):
    """Run the shared text encoder in its own process and return the saved conditioning."""
    from .. import encode as encode_command
    import sys
    with tempfile.TemporaryDirectory(prefix='freevideo-h3img-') as directory:
        prompt_file = Path(directory) / 'prompt.txt'
        prompt_file.write_text(prompt, encoding='utf-8')
        out = Path(directory) / 'cond.pt'
        encode_command.run(argparse.Namespace(
            prompt_file=prompt_file, out=out, encoder=encoder,
            comfy_root=Path(comfy_root) if comfy_root else None, model_paths=Path(model_paths) if model_paths else None,
            comfy_python=comfy_python or sys.executable, vram_gib=vram_gib, ram_gib=ram_gib, attention='auto',
            gpu_reserve_gib=None, ram_reserve_gib=None))
        return torch.load(out, map_location='cpu', weights_only=False)


def generate(prompt, width, height, seed, references=None, progress_cb=None, cancel=None, *, dit_path, vae_path,
             encoder='qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors', comfy_root=None, comfy_python=None,
             model_paths=None, vram_gib=None, ram_gib=None, resident=None, cpu_noise=False, stats=None, mapped=True):
    """Return a PIL image for the prompt.

    ``progress_cb(stage, done, total)`` is called with stage in encode, denoise, decode;
    ``cancel()`` returning true stops at the next step. ``mapped`` leaves the checkpoint's tensors as views of the mapped file, so a host
    with less memory than the 21 GB of weights loads them and pins what live memory allows.
    ``resident`` overrides the planned
    layer count. ``stats`` (a dict) receives timings, the plan and the peak memory.
    """
    if references:
        raise ValueError('H3 Image does not take reference images yet')
    stats = stats if stats is not None else {}
    report = progress_cb or (lambda *a: None)

    def check():
        if cancel is not None and cancel():
            raise Cancelled()

    check()
    report('encode', 0, 1)
    tick = time.perf_counter()
    cond = _encode(prompt, encoder=encoder, comfy_root=comfy_root, model_paths=model_paths, comfy_python=comfy_python,
                   vram_gib=vram_gib, ram_gib=ram_gib)
    stats['encode_s'] = round(time.perf_counter() - tick, 2)
    report('encode', 1, 1)
    check()

    from .h3_vae import H3ImageDecoder, to_unit
    from .image_policy import ImageShape, plan_image
    from .image_stream import StreamedStack
    from PIL import Image
    torch.set_grad_enabled(False)
    device = torch.device('cuda')
    total = torch.cuda.get_device_properties(0).total_memory
    budget = int(min(vram_gib * 2**30, total)) if vram_gib else total
    if vram_gib:
        torch.cuda.set_per_process_memory_fraction(min(1.0, budget / total))
    canvas_h, canvas_w = h3_dit.canvas_size(height, width)
    tick = time.perf_counter()
    model = load_h3_dit(str(dit_path), 'cpu', mapped=mapped)
    for name, child in model.named_children():
        if name != 'blocks':
            child.to(device)
    model.adaln_t_table = model.adaln_t_table.to(device)
    stack = StreamedStack(model.blocks, name='h3', device=device)
    shape = ImageShape.measure(list(model.blocks), None)
    shape = ImageShape(**{**shape.__dict__, 'steps': 6})
    plan = plan_image(gpu_budget_bytes=budget, width=canvas_w, height=canvas_h, shape=shape)
    stack.set_resident(plan.dit_resident_layers if resident is None else resident)
    stats.update(load_s=round(time.perf_counter() - tick, 2), canvas=[canvas_h, canvas_w],
                 resident=plan.dit_resident_layers if resident is None else resident,
                 planned_peak_gib=round(plan.peak_allocated_bytes / 2**30, 2), host_mode=plan.host_mode)
    generator = torch.Generator('cpu' if cpu_noise else device).manual_seed(seed)

    def step(i, n):
        check()
        report('denoise', i, n)

    tick = time.perf_counter()
    try:
        latents = h3_dit.sample(model, cond['prompt_embeds'], cond['text_token_tags'], canvas_h // 16, canvas_w // 16,
                                generator, noise_device='cpu' if cpu_noise else None, step_callback=step)
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
