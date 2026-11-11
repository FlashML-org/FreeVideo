"""Smoke run of the H3 Image DiT: conditioning + noise -> six-step latents.

    python -m freevideo_engine.image_gen.h3_smoke --dit FILE --cond conditioning.pt --width 2048 --height 2048 \\
        --seed 1 --out latents.pt [--vram-gib 8] [--resident N|-1 plans it from the budget]

``--cond`` is the file written by ``python -m freevideo_engine encode`` (``prompt_embeds`` [L, 5120] and
``text_token_tags``). ``--vram-gib`` caps this process's CUDA memory; the blocks then stream through
``StreamedStack`` with ``--resident`` of them kept on the card.
"""
import argparse
import json
import time

import torch

from . import h3_dit
from .h3_weights import load_h3_dit
from .image_stream import StreamedStack


def main(argv=None):
    parser = argparse.ArgumentParser(prog='h3_smoke')
    parser.add_argument('--dit', required=True)
    parser.add_argument('--cond', required=True)
    parser.add_argument('--width', type=int, default=1024)
    parser.add_argument('--height', type=int, default=1024)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--out', required=True)
    parser.add_argument('--vram-gib', type=float)
    parser.add_argument('--vae', help='Comfy fp16 H3 Image VAE; decodes the latents to --png')
    parser.add_argument('--png')
    parser.add_argument('--cpu-noise', action='store_true', help='draw the noise on the CPU, as ComfyUI does')
    parser.add_argument('--resident', type=int, default=0)
    parser.add_argument('--no-mapped', action='store_true', help='read the checkpoint into private memory instead of mapping it')
    parser.add_argument('--host-prefetch', choices=('auto', 'off'), default='auto')
    args = parser.parse_args(argv)
    torch.set_grad_enabled(False)
    device = torch.device('cuda')
    if args.vram_gib:
        total = torch.cuda.get_device_properties(0).total_memory
        torch.cuda.set_per_process_memory_fraction(min(1.0, args.vram_gib * 2**30 / total))
    started = time.perf_counter()
    model = load_h3_dit(args.dit, 'cpu', mapped=not args.no_mapped)
    loaded = time.perf_counter()
    for name, child in model.named_children():
        if name != 'blocks':
            child.to(device)
    model.adaln_t_table = model.adaln_t_table.to(device)
    stack = StreamedStack(model.blocks, name='h3', device=device,
                          host_prefetch=False if args.host_prefetch == 'off' else None)
    plan = None
    if args.resident < 0:
        from .image_policy import ImageShape, plan_image
        height, width = h3_dit.canvas_size(args.height, args.width)
        shape = ImageShape.measure(list(model.blocks), None)
        shape = ImageShape(**{**shape.__dict__, 'steps': 6})
        plan = plan_image(gpu_budget_bytes=int(args.vram_gib * 2**30), width=width, height=height, shape=shape)
        args.resident = plan.dit_resident_layers
    stack.set_resident(args.resident)
    cond = torch.load(args.cond, map_location='cpu', weights_only=False)
    height, width = h3_dit.canvas_size(args.height, args.width)
    generator = torch.Generator('cpu' if args.cpu_noise else device).manual_seed(args.seed)
    prepared = time.perf_counter()
    latents = h3_dit.sample(model, cond['prompt_embeds'], cond['text_token_tags'], height // 16, width // 16, generator,
                             noise_device='cpu' if args.cpu_noise else None)
    torch.cuda.synchronize()
    done = time.perf_counter()
    denoise_peak = torch.cuda.max_memory_allocated()
    offload = {key: value for key, value in stack.stats().items() if 'prefetch' in key or 'direct' in key or 'read' in key}
    torch.cuda.reset_peak_memory_stats()
    torch.save(dict(latents=latents.cpu(), canvas=(height, width), seed=args.seed), args.out)
    if args.vae and args.png:
        from PIL import Image
        from .h3_vae import H3ImageDecoder, to_unit
        model = stack = None
        torch.cuda.empty_cache()
        decoder = H3ImageDecoder.load(args.vae, device)
        pixels = to_unit(decoder.decode(latents.to(device)[None], normalized=True))
        Image.fromarray((pixels[0].clamp(0, 1) * 255).round().byte().permute(1, 2, 0).cpu().numpy()).save(args.png)
        torch.cuda.synchronize()
    status = dict(line.split(':', 1) for line in open('/proc/self/status').read().splitlines() if ':' in line)
    memory = {key: status.get(key, '').strip() for key in ('VmHWM', 'VmSwap')}
    print(json.dumps(dict(memory=memory, stack=offload)), flush=True)
    print(json.dumps(dict(load_s=round(loaded - started, 1), prepare_s=round(prepared - loaded, 1),
                          sample_s=round(done - prepared, 1), step_s=round((done - prepared) / 6, 2), resident=args.resident, mapped=not args.no_mapped,
                          planned_peak_gib=None if plan is None else round(plan.peak_allocated_bytes / 2**30, 2), canvas=[height, width],
                          denoise_peak_gib=round(denoise_peak / 2**30, 2), decode_peak_gib=round(torch.cuda.max_memory_allocated() / 2**30, 2),
                          reserved_gib=round(torch.cuda.max_memory_reserved() / 2**30, 2),
                          latent_mean=float(latents.mean()), latent_std=float(latents.std()))), flush=True)


if __name__ == '__main__':
    main()
