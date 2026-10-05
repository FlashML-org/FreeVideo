"""Public CLI. Planning and help do not import the model runtime."""
import argparse
import json
import os
from pathlib import Path
import sys
from .hardware import Hardware, detect
from .kernel_capabilities import available_backends
from .policy import choose


def write_json(value, path=None):
    text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding='utf-8')
    print(text, end='')


def resource_arguments(parser):
    parser.add_argument('--vram-gib', type=float, help='Nominal capacity, bounded by detected VRAM')
    parser.add_argument('--ram-gib', type=float, help='Nominal system RAM, bounded by detected RAM')
    parser.add_argument('--gpu-reserve-gib', type=float,
                        help='Explicit foreground VRAM growth reserve (minimum 0.2 GiB); automatic planning may shrink its default reserve for a tight request')
    parser.add_argument('--ram-reserve-gib', type=float, help='Override headroom kept beyond current system RAM usage')
    parser.add_argument('--attention', default='auto', help='auto, sage2, cudnn, torch-flash, fa2, fa4, or global/window')


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'predict':
        from .prediction_cli import main as predict
        raise SystemExit(predict(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == 'resource-history':
        from .resource_cli import main as resource_history
        raise SystemExit(resource_history(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == 'optimize':
        from .optimize import main as optimize
        raise SystemExit(optimize(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == 'diagnose':
        from .diagnostics import main as diagnose
        raise SystemExit(diagnose(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == 'bench':
        from .benchmark import main as benchmark
        return benchmark(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == 'calibrate':
        from .calibration import main as calibrate
        return calibrate(sys.argv[2:])
    parser = argparse.ArgumentParser(prog='freevideo', description='VDN Engine: video and audio with bounded memory')
    sub = parser.add_subparsers(dest='command', required=True)
    plan = sub.add_parser('plan', help='Show a resource policy without loading models')
    resource_arguments(plan)
    plan.add_argument('--hardware-json', type=Path, help='Inspect a fixture, not a hardware benchmark')
    plan.add_argument('--out', type=Path)
    plan.add_argument('--width', type=int, default=1344)
    plan.add_argument('--height', type=int, default=768)
    plan_length = plan.add_mutually_exclusive_group()
    plan_length.add_argument('--frames', type=int)
    plan_length.add_argument('--seconds', type=float)
    doctor = sub.add_parser('doctor', help='Check paths, versions and installed attention kernels')
    doctor.add_argument('--probe', action='store_true', help='Execute small kernels in isolated processes')
    doctor.add_argument('--require-paths', action='store_true', help='Fail if model or source directories are missing')
    doctor.add_argument('--out', type=Path, help='Save the complete kernel report, including optional failures')
    sub.add_parser('diagnose', help='Collect bounded local diagnostics, even after a failed setup')
    sub.add_parser('optimize', help='Reuse a test report and validate bounded local tuning')
    sub.add_parser('resource-history', help='Inspect local performance and resource measurements (CPU only)')
    sub.add_parser('predict', help='Preview phase time and memory from local complete request history')
    setup = sub.add_parser('setup', help='Install the pinned upstream model-code dependency')
    setup.add_argument('--vdn-root', type=Path)
    setup.add_argument('--install-packages', action='store_true')
    setup.add_argument('--fa4-guard', action='store_true', help='Prepare the validated isolated FA4 b26 SM120 fix')
    prepare = sub.add_parser('prepare', help='Prepare official merged weights and an FP8 cache')
    prepare.add_argument('--base', type=Path, required=True)
    prepare.add_argument('--checkpoint', type=Path, required=True)
    prepare.add_argument('--cache-root', type=Path, required=True)
    prepare.add_argument('--bf16-source', type=Path)
    encode = sub.add_parser('encode', help='Save reusable text conditioning for generation and matched benchmarks')
    resource_arguments(encode)
    encode.add_argument('--prompt-file', type=Path, required=True)
    encode.add_argument('--out', type=Path, required=True)
    generate = sub.add_parser('generate', help='Generate an MP4 from text or cached conditioning')
    resource_arguments(generate)
    source = generate.add_mutually_exclusive_group(required=True)
    source.add_argument('--prompt-file', type=Path)
    source.add_argument('--conditioning', type=Path)
    generate.add_argument('--cache', type=Path, required=True)
    generate.add_argument('--base', type=Path)
    generate.add_argument('--checkpoint', type=Path)
    generate.add_argument('--profile', type=Path, help='Explicit policy/profile override')
    from .media_request import TASKS
    generate.add_argument('--task', choices=TASKS, help='Input task; inferred when --media is provided')
    generate.add_argument('--media', type=Path, help='Versioned keyframe/reference/LoRA request JSON; local files only')
    generate.add_argument('--allocator-limit-gib', type=float,
                          help='Explicit benchmark cap on PyTorch CUDA allocations; not a whole-device memory limit')
    generate.add_argument('--no-history-placement', action='store_true', help='Keep the starting placement for a matched baseline; still record measurements')
    generate.add_argument('--recover-placement', action='store_true', help='Allow numerical-neutral resource recovery with an explicit profile; automatic profiles already recover')
    generate.add_argument('--resource-retries', type=int, choices=(0, 1, 2), default=2,
                          help='Fresh-worker placement retries after confirmed resource exhaustion (default: 2)')
    generate.add_argument('--no-tuning', action='store_true', help='Bypass saved tuning and conditioning reuse for this request')
    generate.add_argument('--seed', type=int, default=2026090901)
    generate.add_argument('--two-pass', action=argparse.BooleanOptionalAction, default=True,
                          help='Sample a smaller canvas, upscale latents and refine (default: on; 8 + 3 steps)')
    generate.add_argument('--base-steps', type=int, choices=range(1, 33), metavar='1..32',
                          help='First-pass steps (default: 8, or explicit profile). Changes may reduce quality.')
    generate.add_argument('--refine-steps', type=int, choices=range(1, 32), default=3, metavar='1..31',
                          help='Second-pass steps (default: 3, independent schedule). Other counts use the original tail.')
    generate.add_argument('--out', type=Path, required=True)
    generate.add_argument('--width', type=int, default=1344)
    generate.add_argument('--height', type=int, default=768)
    length = generate.add_mutually_exclusive_group()
    length.add_argument('--frames', type=int)
    length.add_argument('--seconds', type=float)
    for command in (generate, encode):
        command.add_argument('--encoder-python', '--comfy-python', dest='comfy_python',
                             default=os.environ.get('FREEVIDEO_COMFY_PYTHON', sys.executable))
        command.add_argument('--encoder-root', '--comfy-root', dest='comfy_root', type=Path)
        command.add_argument('--model-paths', type=Path, help='Optional ComfyUI extra_model_paths.yaml')
        command.add_argument('--encoder', default='qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors')
    bench = sub.add_parser('bench', help='Compare attention backends under one explicit policy')
    bench.add_argument('arguments', nargs=argparse.REMAINDER)
    calibrate = sub.add_parser('calibrate',
                               help='Measure the placement questions this machine can settle')
    calibrate.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if sys.platform == 'darwin' and args.command in ('plan', 'generate', 'encode', 'doctor'):
        from .macos_generate import dispatch
        return dispatch(args)
    if args.command == 'plan':
        from .geometry import geometry
        canvas = geometry(args.width, args.height, frames=args.frames, seconds=args.seconds)
        hardware = Hardware.from_dict(json.loads(args.hardware_json.read_text(encoding='utf-8'))) if args.hardware_json else detect()
        available = {'sage2', 'cudnn', 'torch-flash', 'fa2', 'fa4'} if args.hardware_json else available_backends(hardware, probe_missing=False)
        policy = choose(hardware, **{key: getattr(args, key) for key in
                        ['vram_gib', 'ram_gib', 'attention', 'gpu_reserve_gib', 'ram_reserve_gib']},
                        available_backends=available, canvas=canvas)
        write_json(policy.to_dict(), args.out)
    elif args.command == 'doctor':
        from .doctor import doctor
        result = doctor(probe=args.probe)
        if args.out:
            from .monitoring import save
            save(args.out, result)
        write_json(result)
        if args.probe and not result['ready']:
            raise SystemExit(1)
        if args.require_paths and not all(row['exists'] for row in result['paths'].values()):
            raise SystemExit(1)
    elif args.command == 'setup':
        from .install import install
        install(args.vdn_root, packages=args.install_packages)
        if args.fa4_guard:
            from .fa4_guard import prepare
            write_json({'fa4_overlay': str(prepare())})
    elif args.command == 'prepare':
        from .paths import add_vdn
        add_vdn()
        from .weights import prepare as prepare_bf16
        from .fp8 import prepare as prepare_fp8
        source = args.bf16_source or prepare_bf16(args.base, args.checkpoint, args.cache_root)
        write_json({'cache': str(prepare_fp8(source, args.cache_root, base=args.base, checkpoint=args.checkpoint))})
    elif args.command == 'generate':
        from .generate import run
        run(args)
    elif args.command == 'encode':
        from .encode import run
        run(args)
    else:
        from .benchmark import main as benchmark
        benchmark(args.arguments)
