"""Reusable native H3 conditioning with a short-lived text-encoder process."""
import json
import os
from pathlib import Path
import signal
import tempfile
from .generate import child
from .hardware import detect
from .kernel_capabilities import available_backends
from .locking import runtime_lock, LOCK_ENV
from .paths import comfy_root
from .policy import choose
from . import processes
from .system import ram_budget_is_estimate, inference_emergency_floor


def run(args):
    destination = args.out.resolve()
    if destination.suffix != '.pt':
        raise ValueError('--out must name a .pt conditioning file')
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    hardware = detect()
    policy = choose(hardware, **{key: getattr(args, key) for key in
                    ['vram_gib', 'ram_gib', 'attention', 'gpu_reserve_gib', 'ram_reserve_gib']},
                    available_backends=available_backends(hardware), allow_capacity_trial=True)
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'Encoding interrupted by signal {signum}')
    previous = processes.termination_handler(interrupted)
    try:
        with runtime_lock() as descriptor, tempfile.TemporaryDirectory(prefix='freevideo-encode-', dir=destination.parent) as directory:
            request = Path(directory) / 'request.json'
            request.write_text(json.dumps({'prompt': args.prompt_file.read_text(encoding='utf-8'), 'encoder': args.encoder,
                'comfy_root': str(args.comfy_root or comfy_root()),
                'model_paths': str(args.model_paths.resolve()) if args.model_paths else None,
                'output': str(destination), 'gpu_budget_gb': policy.gpu_budget_bytes / 1e9,
                'metrics': str(destination.with_suffix('.encoding.json'))}) + '\n', encoding='utf-8')
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                       PYTORCH_ALLOC_CONF=policy.allocator_config,
                       PYTORCH_CUDA_ALLOC_CONF=policy.allocator_config,
                       HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONUNBUFFERED='1')
            env[LOCK_ENV] = str(descriptor)
            child([args.comfy_python, '-m', 'freevideo_engine.encode_worker', '--request', str(request)],
                  env, descriptor, destination.with_suffix('.encoding.log'), ram_budget_bytes=policy.ram_budget_bytes,
                  ram_budget_is_estimate=ram_budget_is_estimate(args),
                  gpu=hardware.gpu_uuid if hardware.hip_version else None,
                  minimum_available_bytes=inference_emergency_floor(policy.ram_budget_bytes,
                      getattr(args, 'ram_reserve_gib', None)))
    finally:
        processes.restore_handlers(previous)
    print(json.dumps({'conditioning': str(destination), 'policy': policy.to_dict()}), flush=True)
