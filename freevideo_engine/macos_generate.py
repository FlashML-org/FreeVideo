"""Native request entry point, using shared leases, supervision and reports.

Encoding and generation use separate children so their weights never overlap.
No tensor runtime is imported by this supervisor. CUDA orchestration is unchanged.
"""
import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import sys
import time
import traceback

from . import processes
from .geometry import geometry
from .locking import LOCK_ENV, runtime_lock
from .monitoring import save
from .paths import data_root

GiB = 2**30


def policy(args, memory=None):
    from .system import system_memory
    memory = memory or system_memory()
    if (any(type(memory.get(k)) is not int or memory[k] < 0 for k in ('total_bytes', 'available_bytes'))
            or not 0 < memory['total_bytes'] or memory['available_bytes'] > memory['total_bytes']):
        raise ValueError('Invalid unified-memory observation')
    for name in ('vram_gib', 'gpu_reserve_gib', 'profile'):
        if getattr(args, name, None) is not None:
            raise ValueError('Mac uses a unified memory budget; --' + name.replace('_', '-') + ' is not supported')
    if getattr(args, 'attention', 'auto') not in ('auto', 'mps'):
        raise ValueError('Native Mac attention must be auto or mps')
    def amount(name, default):
        value = getattr(args, name, None)
        if value is None:
            return default
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(name + ' must be positive and finite')
        return int(value * GiB)
    reserve = max(GiB, amount('ram_reserve_gib', 2 * GiB))
    capacity = min(memory['total_bytes'], amount('ram_gib', memory['total_bytes']))
    working = min(capacity, memory['available_bytes']) - reserve
    if working < 2 * GiB:
        raise MemoryError('Native generation needs at least 2 GiB working unified memory after the system reserve')
    # The original 4 GiB cap was a small-workload trial, not a device capacity.
    # Use the live unified allowance. The native backend additionally clamps it
    # to Metal's recommendation and rechecks availability when the worker starts.
    allocator = min(working, amount('allocator_limit_gib', working))
    allocator_capacity = min(capacity - reserve, amount('allocator_limit_gib', capacity - reserve))
    return dict(device_backend='mps', memory_model='unified', observed=memory,
        reserve_bytes=reserve, allocator_bytes=allocator, allocator_capacity_bytes=allocator_capacity,
        encoder_bytes=min(3 * GiB, allocator),
        ram_budget_bytes=capacity if getattr(args, 'ram_gib', None) is not None else working,
        ram_budget_is_estimate=getattr(args, 'ram_gib', None) is None,
        emergency_floor_bytes=GiB, query_chunk=128, ff_chunk=256, projection_chunk=256,
        weight_decoder='metal', encoder_weight_decoder='metal', cpu_operator_fallback=False,
        sampling_lifetime='auto',
        scope='Live unified-memory bounds and eligible cross-pass weight retention; not proof of workload capacity.')


def encoder_checkpoint(args):
    filename = getattr(args, 'encoder', 'qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors')
    if Path(filename).name != filename:
        raise ValueError('Encoder must be an installed checkpoint filename')
    folders = [data_root() / 'models/encoder', data_root() / 'models/encoder/text_encoders']
    paths = getattr(args, 'model_paths', None)
    if paths:
        import yaml
        config = yaml.safe_load(Path(paths).read_text(encoding='utf-8'))
        for row in config.values():
            if not isinstance(row, dict):
                continue
            base = Path(row.get('base_path', Path(paths).parent)).expanduser()
            if not base.is_absolute():
                base = Path(paths).parent / base
            for folder in str(row.get('text_encoders', '')).splitlines():
                if folder.strip():
                    folders.append(base / folder.strip())
    matches = {(folder / filename).resolve() for folder in folders if (folder / filename).is_file()}
    if len(matches) != 1:
        raise ValueError('Expected one installed native H3 encoder checkpoint. In the FreeVideo launcher, open Settings and click Repair.')
    return matches.pop()


def conditioning_cache(checkpoint, library):
    from .input_cache import InputCache, digest
    from .storage import fingerprint
    # Include the actual encoder implementation, not only a nominal version.
    sources = {}
    for label, root in (('engine', Path(__file__).parent), ('comfy', Path(library) / 'comfy')):
        for path in sorted(root.rglob('*.py')):
            sources[label + '/' + path.relative_to(root).as_posix()] = digest(path)
    identity = dict(backend='mps', macos=platform.mac_ver()[0], checkpoint=fingerprint(checkpoint),
        torch=importlib.metadata.version('torch'), sources=sources, precision='native-h3-fp32-v1')
    return InputCache(identity)


def _check_admission(admission, sampling):
    if admission['state'] == 'refuse':
        # error_panel.js shows the lower-resolution advice for this text.
        raise MemoryError('Generation needs at least %.1f GiB of unified memory for %s at %d × %d; '
                          'this Mac can allocate at most %.1f GiB.' % (
                              math.ceil(admission['needed_bytes'] / GiB * 10) / 10,
                              'the second pass' if admission['phase'] == 'refinement' else 'this video',
                              admission['width'], admission['height'],
                              math.floor(admission['capacity_bytes'] / GiB * 10) / 10))
    if admission['state'] == 'tight':
        print(json.dumps(dict(event='unified_memory_tight', phase=admission['phase'],
                              needed_bytes=admission['needed_bytes'],
                              allowance_bytes=admission['allowance_bytes'],
                              first_pass_steps=sampling['base_steps'])), flush=True)


def run(args):
    from .macos_bootstrap import require_native, environment, inventory
    from .macos_power import awake
    from .generate import child
    from .support_report import write as write_debug, code_identity
    from .two_pass import plan
    require_native()
    from .preview import validate_flags, source as preview_source
    preview = getattr(args, 'preview', False) is True
    validate_flags(preview, getattr(args, 'refine_from', None), args.two_pass)
    refine_source = preview_source(args.refine_from, native=True) if getattr(args, 'refine_from', None) else None
    output = Path(args.out).expanduser().resolve()
    artifacts = output.with_suffix('.artifacts')
    if any(p.exists() for p in (output, artifacts, output.with_suffix('.request.json'))):
        raise FileExistsError('Choose a new output path; previous artifacts are retained')
    artifacts.mkdir(parents=True)
    started = time.monotonic()
    report = dict(success=False, device_backend='mps', phase='preflight', seed=args.seed)
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Request interrupted by signal ' + str(signum))
    previous = processes.termination_handler(interrupted)
    try:
        resource = policy(args)
        hardware = inventory()['hardware']
        resource['hardware'] = hardware
        report.update(runtime_hardware=hardware, runtime_code=code_identity())
        print(json.dumps(dict(event='compute_device', backend='MPS', name=hardware['gpu_name'],
                              memory_model='unified', ram_total_bytes=hardware['ram_total'])), flush=True)
        canvas = geometry(args.width, args.height, frames=args.frames, seconds=args.seconds)
        from .media_request import read as read_media, task_for, cache_key, TASKS
        media = read_media(args.media)
        structural = any(media.get(k) for k in ('first', 'last', 'references', 'conditioning_info'))
        task = args.task or task_for(media)
        if (args.task and structural and args.task != task_for(media)
                and not (media.get('references') and args.task in ('ref2va_audio', 'ref2va_av'))):
            raise ValueError('Native request task differs from its media inputs')
        if task not in TASKS:
            raise ValueError('Unsupported native conditioning task')
        raw_media = any(media.get(k) for k in ('first', 'last', 'references'))
        if media.get('conditioning_info') and (not args.conditioning or raw_media):
            raise ValueError('Use preencoded conditioning information with its file and no additional media')
        if args.conditioning and raw_media:
            raise ValueError('Preencoded conditioning already includes media; use its conditioning_info')
        if args.prompt_file and task != 't2va' and not raw_media:
            raise ValueError('Native media encoding requires its input files')
        sampling = plan(canvas, args.two_pass, task, base_steps=args.base_steps or 8, refine_steps=args.refine_steps)
        from .paths import base_path, checkpoint_path
        base = str(Path(args.base or base_path()).resolve())
        adapters = [row for row in media.get('loras', []) if row['strength'] != 0]
        prepared_cache = Path(args.cache).resolve()
        report.update(geometry=canvas, sampling_plan=sampling, profile=dict(policy=resource,
            engine=dict(device_backend='mps', task=task, attention='mps', steps=sampling['base_steps'])),
            encoder_mode='native H3 encoder library child' if args.prompt_file else 'preencoded shared conditioning')
        # Known before the text encoder and a 20-minute first pass: whether the
        # largest sampling pass can fit, and whether other applications leave it room.
        from .macos_compute import generated_audio_tokens, request_admission
        from .backends.mps import recommended_working_set_bytes
        working_set = recommended_working_set_bytes()
        conditioning_info = media.get('conditioning_info', {})
        reference_tokens = (conditioning_info.get('reference_video_tokens', 0)
                            + conditioning_info.get('reference_audio_tokens', 0))
        admission = report['memory_admission'] = request_admission(resource, sampling, preview=preview,
                                                                  working_set_bytes=working_set,
                                                                  reference_tokens=reference_tokens)
        _check_admission(admission, sampling)
        if preview:
            report['preview'] = dict(geometry=dict(sampling['first'], fps=canvas['fps']))
        if refine_source:
            report['upscaled_preview'] = dict(output=refine_source['output'])
        env = environment(data_root(), dict(os.environ, PYTHONUNBUFFERED='1', OMP_NUM_THREADS='2', MKL_NUM_THREADS='2'))
        with runtime_lock() as descriptor, awake() as idle_sleep_prevented:
            report['idle_sleep_prevented'] = idle_sleep_prevented
            env[LOCK_ENV] = str(descriptor)
            def execute(value, name, module='freevideo_engine.macos_generate'):
                request = artifacts / (name + '-request.json')
                save(request, value)
                return child([sys.executable, '-m', module, '--request', str(request)],
                    env, descriptor, output.with_suffix('.' + name + '.log'),
                    ram_budget_bytes=resource['ram_budget_bytes'],
                    minimum_available_bytes=resource['emergency_floor_bytes'],
                    ram_budget_is_estimate=resource['ram_budget_is_estimate'])
            if adapters:
                report['phase'] = 'lora'
                save(output.with_suffix('.request.json'), report)
                result_path = artifacts / 'lora-result.json'
                print(json.dumps(dict(event='lora_prepare_start')), flush=True)
                report['lora_resources'] = execute(dict(cache=str(prepared_cache), adapters=adapters,
                    result=str(result_path), mode='online'), 'lora', module='freevideo_engine.lora_cache')
                prepared = json.loads(result_path.read_text(encoding='utf-8'))
                prepared_cache = Path(prepared['cache']).resolve()
                report['lora'] = prepared['report']
            conditioning = Path(args.conditioning).resolve() if args.conditioning else artifacts / 'conditioning.pt'
            if preview and args.conditioning:
                # Keep the preview self-contained for its later upscale.
                shutil.copyfile(conditioning, artifacts / 'conditioning.pt')
                conditioning = artifacts / 'conditioning.pt'
            if refine_source:
                # The preview's own conditioning: the exact file its first pass used.
                conditioning = Path(refine_source['conditioning'])
                prior = refine_source['report']
                if prior.get('encoding'):
                    report['encoding'] = dict(prior['encoding'], reused_from_preview=True)
                conditioning_info = prior.get('encoding', {}).get('conditioning_info', {})
                task = prior.get('profile', {}).get('engine', {}).get('task', task)
                report['profile']['engine']['task'] = task
                print(json.dumps(dict(event='conditioning_reused', source='preview')), flush=True)
            if args.prompt_file and not refine_source:
                report['phase'] = 'encoding'
                save(output.with_suffix('.request.json'), report)
                prompt = Path(args.prompt_file).read_text(encoding='utf-8')
                if not prompt.strip():
                    raise ValueError('A nonempty prompt is required')
                checkpoint = encoder_checkpoint(args)
                library = Path(args.comfy_root or data_root() / 'vendor/h3-text-encoder').resolve()
                cache = None if args.no_tuning else conditioning_cache(checkpoint, library)
                key = cache_key(prompt, media, canvas)
                metrics = cache.get(key, conditioning) if cache else None
                if metrics is None:
                    report['encoding_resources'] = execute(dict(phase='encoding', root=str(data_root()),
                        prompt_file=str(Path(args.prompt_file).resolve()), output=str(conditioning),
                        report=str(output.with_suffix('.encoding.json')), checkpoint=str(checkpoint),
                        library=str(library), resources=resource, media=media, canvas=canvas, base=base), 'encoding')
                    metrics = json.loads(output.with_suffix('.encoding.json').read_text())
                    if cache:
                        try:
                            cache.put(key, conditioning, metrics)
                        except (OSError, ValueError) as error:
                            report['input_cache_note'] = type(error).__name__ + ': input cache unavailable'
                else:
                    save(output.with_suffix('.encoding.json'), metrics)
                    print(json.dumps(dict(event='conditioning_cache_hit')), flush=True)
                report['input_cache'] = dict(conditioning_hit=bool(metrics.get('cache_hit')))
                report['encoding'] = metrics
                conditioning_info = metrics.get('conditioning_info', {})
                encoded_task = conditioning_info.get('task')
                if task.startswith('ref2va'):
                    if (encoded_task not in ('ref2va', 'ref2va_audio', 'ref2va_av') or
                            args.task in ('ref2va_audio', 'ref2va_av') and args.task != encoded_task):
                        raise ValueError('Native encoder did not return the requested reference task')
                    task = encoded_task
                    report['profile']['engine']['task'] = task
                elif task != 't2va' and encoded_task != task:
                    raise ValueError('Native encoder did not return the requested keyframe task')
            if not conditioning.is_file():
                raise FileNotFoundError('Conditioning is missing: ' + str(conditioning))
            reference_tokens = (conditioning_info.get('reference_video_tokens', 0)
                                + conditioning_info.get('reference_audio_tokens', 0))
            text_tokens = conditioning_info.get('text_tokens')
            if text_tokens is None:
                shape = report.get('encoding', {}).get('shape')
                text_tokens = shape[0] if shape else None
            if refine_source and not conditioning_info:
                prior_admission = prior.get('memory_admission', {})
                reference_tokens = prior_admission.get('reference_tokens', reference_tokens)
                if text_tokens is None:
                    text_tokens = prior_admission.get('text_tokens')
            conditioning_tokens = 1024 if text_tokens is None else text_tokens + generated_audio_tokens(admission['frames'])
            if conditioning_tokens + reference_tokens > 1024 + admission['reference_tokens']:
                report['memory_admission_before_encoding'] = admission
                admission = report['memory_admission'] = request_admission(resource, sampling, preview=preview,
                    working_set_bytes=working_set, reference_tokens=reference_tokens, text_tokens=text_tokens)
                _check_admission(admission, sampling)
            report['phase'] = 'engine'
            save(output.with_suffix('.request.json'), report)
            print(json.dumps(dict(event='sampling_plan', **sampling, **({'preview': True} if preview else {}))), flush=True)
            engine = dict(phase='engine', output=str(output), artifacts=str(artifacts),
                conditioning=str(conditioning), cache=str(prepared_cache),
                base=base, task=task,
                checkpoint=str(Path(args.checkpoint or checkpoint_path()).resolve()),
                seed=args.seed, canvas=canvas, sampling=sampling, resources=resource)
            if preview:
                engine['preview'] = True
            if refine_source:
                engine['refine_from'] = {key: refine_source[key] for key in ('input', 'metrics', 'request', 'output')}
            report['resources'] = execute(engine, 'engine')
            metrics = json.loads(output.with_suffix('.engine.json').read_text())
            if (metrics.get('success') is not True or metrics.get('sampling_plan') != sampling
                    or len(metrics.get('step_seconds', [])) != (sampling['base_steps'] if preview else sampling['total_steps'])
                    or not output.is_file() or output.stat().st_size == 0):
                raise RuntimeError('Native worker did not complete the requested video')
            if refine_source:
                report['upscaled_preview'].update(metrics.get('upscaled_preview') or {})
            report.update(success=True, phase='complete', video=metrics)
            return output
    except BaseException as error:
        report.update(error_type=type(error).__name__, error_message=str(error), exception=traceback.format_exc(),
                      cancelled=isinstance(error, KeyboardInterrupt))
        raise
    finally:
        processes.restore_handlers(previous)
        report['request_seconds'] = time.monotonic() - started
        save(output.with_suffix('.request.json'), report)
        write_debug(output)


def worker(value):
    from .macos_bootstrap import require_native
    require_native()
    if value['phase'] == 'engine':
        from .macos_stages import run
        return run(value)
    if value['phase'] != 'encoding':
        raise ValueError('Unknown native request worker phase')
    import torch
    torch.set_num_threads(2)
    resource, output = value['resources'], Path(value['output'])
    path = Path(value['report'])
    result = dict(success=False, device_backend='mps', phase=value['phase'])
    started = time.monotonic()
    try:
        from .macos_encoder import encode
        result.update(encode(Path(value['prompt_file']).read_text(encoding='utf-8'), output,
            root=value['root'], library=value['library'], checkpoint=value['checkpoint'],
            budget_bytes=resource['encoder_bytes'], reserve_bytes=resource['reserve_bytes'],
            media_budget_bytes=resource.get('allocator_capacity_bytes', resource['encoder_bytes']),
            weight_decoder=resource.get('encoder_weight_decoder', 'cpu'),
            media=value.get('media'), canvas=value.get('canvas'), base=value.get('base')))
        result['work_seconds'] = time.monotonic() - started
    except BaseException as error:
        result.update(error_type=type(error).__name__, error_message=str(error), exception=traceback.format_exc())
        raise
    finally:
        result['worker_seconds'] = time.monotonic() - started
        save(path, result)


def dispatch(args):
    from .cli import write_json
    from .macos_bootstrap import require_native
    require_native()
    if args.command == 'generate':
        return run(args)
    if args.command == 'doctor':
        from .macos_doctor import doctor
        result = doctor(probe=args.probe)
        write_json(result, args.out)
        if (args.probe and not result['ready']) or (args.require_paths and
                not all(row['exists'] for row in result['paths'].values())):
            raise SystemExit(1)
    elif args.command == 'plan':
        write_json(policy(args), args.out)
    elif args.command == 'encode':
        from .macos_encoder import encode
        resource = policy(args)
        with runtime_lock():
            result = encode(args.prompt_file.read_text(encoding='utf-8'), args.out,
                root=data_root(), library=args.comfy_root, checkpoint=encoder_checkpoint(args),
                budget_bytes=resource['encoder_bytes'], reserve_bytes=resource['reserve_bytes'],
                weight_decoder=resource['encoder_weight_decoder'])
        write_json(result, args.out.with_suffix('.encoding.json'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    worker(json.loads(parser.parse_args().request.read_text(encoding='utf-8')))
