"""One local FP8 request, reusing the native ComfyUI encoder in a separate child.

Use a saved profile or select placement from current available resources.
The parent imports no tensor runtime. Standalone requests use fresh workers;
ComfyUI can reuse an isolated worker and reclaim its idle models. Both paths
share the exclusive GPU-work lock and monitor the actual compute process.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import shutil
import time
from contextlib import nullcontext

from .locking import runtime_lock, LOCK_ENV
from .paths import vdn_root, comfy_root
from .monitoring import save
from . import tuning
from . import processes
from .system import (inference_headroom, inference_memory_sample, inference_environment, memory_sample,
                     windows, inference_pressure, inference_emergency_floor, ram_budget_is_estimate)
from .adaptive import (WorkerExit, classify_failure, local_identity, history_path,
                       attempt_geometry, execute_attempts, complete_observation, benchmark_allocator_limit)
from .resource_history import ResourceHistory


def child(command, env, descriptor, log, *, ram_budget_bytes=None, gpu=None, on_start=None,
          minimum_available_bytes=None, ram_budget_is_estimate=False):
    from .ram import ProcessMemory
    memory = ProcessMemory() if ram_budget_bytes is not None else None
    telemetry = {}
    monitor = None
    with log.open('w', encoding='utf-8') as stream:
        from .resident_process import ENV, RemoteProcess, SessionRestarted
        module = command[command.index('-m')+1] if '-m' in command else None
        process = None
        if env.get(ENV) and module in ('freevideo_engine.worker', 'freevideo_engine.encode_worker'):
            try:
                process = RemoteProcess(env[ENV], command, env, descriptor, log)
            except SessionRestarted as error:
                env = dict(env)
                env.pop(ENV, None)
                os.environ.pop(ENV, None)
                print(json.dumps({'event': 'resident_session_restart', 'reason': str(error)}), flush=True)
        if process is None:
            process = processes.popen(command, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                       pass_fds=(descriptor,), start_new_session=True)
        pending_error = None
        try:
            if on_start:
                on_start(process.pid)
            if gpu:
                from .monitoring import Monitor
                monitor = Monitor(log.with_suffix('.gpu.csv'), device=gpu).start()
            while process.poll() is None:
                if memory:
                    sample = memory.sample(process.pid)
                    telemetry.setdefault('ram_start', sample)
                    telemetry['ram_last'] = sample
                    pressure = inference_pressure(sample, ram_budget_bytes, minimum_available_bytes,
                                                  budget_is_estimate=ram_budget_is_estimate)
                    telemetry['ram_guard'] = pressure
                    if pressure['reasons'] and not pressure['enforced_reasons']:
                        # Keep the first crossing even when later samples return
                        # below the estimate. Let the OS reclaim/page memory; do
                        # not trim another application's working set or alter its
                        # pagefile settings. One event per worker, not per sample.
                        if 'ram_budget_warning' not in telemetry:
                            telemetry['ram_budget_warning'] = dict(pressure)
                            print(json.dumps(dict(event='ram_budget_warning', **pressure)), flush=True)
                    if pressure['enforced_reasons']:
                        detail = ''
                        if pressure.get('commit_available_bytes') is not None:
                            if pressure.get('physical_available_bytes') is not None:
                                detail = ' Physical RAM available %.2f GiB;' % (pressure['physical_available_bytes']/2**30)
                            detail += ' Windows commit headroom %.3f GiB.' % (pressure['commit_available_bytes']/2**30)
                            if pressure['commit_available_bytes'] <= pressure['emergency_floor_bytes']:
                                detail += ' Windows commit headroom exhausted; this is separate from physical RAM usage.'
                        raise MemoryError(('RAM guard: %s. Working RAM %.2f GiB / budget %.2f GiB; '
                            'available physical/commit headroom %.2f GiB / emergency floor %.3f GiB.' %
                            (', '.join(pressure['enforced_reasons']), pressure['working_bytes']/2**30,
                             ram_budget_bytes/2**30, pressure['available_bytes']/2**30,
                             pressure['emergency_floor_bytes']/2**30)) + detail)
                try:
                    process.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    pass
            code = process.returncode
            if code:
                raise WorkerExit(code, log)
        except BaseException as error:
            pending_error = error
            try:
                processes.stop(process)
            except BaseException as stop_error:
                # A stop timeout is not a controlled timeout. The worker may
                # still own CUDA; never convert this into an ordinary retry.
                error.worker_stop_confirmed = False
                error.worker_stop_error = repr(stop_error)
            error.telemetry = telemetry
            raise
        finally:
            cleanup_errors = []
            for name, observed, suffix in (('ram', memory, '.memory.json'), ('gpu', monitor, '.gpu.json')):
                if observed is not None:
                    try:
                        telemetry[name] = observed.result() if name == 'ram' else observed.stop()
                        save(log.with_suffix(suffix), telemetry[name])
                    except BaseException as error:
                        cleanup_errors.append(error)
            if cleanup_errors:
                if pending_error is None:
                    raise cleanup_errors[0]
                pending_error.cleanup_errors = [repr(error) for error in cleanup_errors]
    return telemetry


def main():
    from .cli import main as cli
    sys.argv.insert(1, 'generate')
    cli()


def automatic_profile(args, canvas, *, stage, evidence, descriptor=None, environment=None):
    """Plan with idle-cache accounting; reclaim once if it prevents admission."""
    from .hardware import detect
    from .policy import choose, ResourceBudgetError
    from .kernel_capabilities import available_backends
    from .resident_process import credit, release_idle_cache, ENV, SessionRestarted
    environment = os.environ if environment is None else environment
    for attempt in range(2):
        raw = detect()
        encoding_stage = stage == 'before-encoding' and bool(getattr(args, 'prompt_file', None))
        hardware, state = credit(raw, environment, reuse_encoder=encoding_stage)
        row = dict(stage=stage, raw=raw.to_dict(), adjusted=hardware.to_dict(), idle_cache=state)
        from .system import system_memory
        try:
            row['system_memory'] = system_memory()
        except (OSError, RuntimeError):
            pass
        evidence.append(row)
        try:
            # What this machine has demonstrably held, for the weight cache
            # only. None on a first run, which leaves the plan unchanged.
            from .adaptive import demonstrated_local_ram
            options = {key: getattr(args, key) for key in
                ('vram_gib', 'ram_gib', 'attention', 'gpu_reserve_gib', 'ram_reserve_gib')}
            options.update(available_backends=available_backends(hardware, probe_missing=False), canvas=canvas,
                demonstrated_ram_bytes=demonstrated_local_ram(hardware, args, canvas))
            if getattr(args, '_lora_max_block_bytes', 0):
                options['lora_max_block_bytes'] = args._lora_max_block_bytes
            if getattr(args, '_lora_root_bytes', 0):
                options['lora_root_bytes'] = args._lora_root_bytes
            if encoding_stage:
                options['stage'] = 'encoding'
            try:
                selected = choose(hardware, **options)
            except ResourceBudgetError as estimate:
                details = estimate.details
                cache_cannot_resolve = (attempt or not state or not environment.get(ENV) or
                    details['gpu_capacity_bytes'] - details['gpu_reserve_bytes'] < details['gpu_minimum_bytes'])
                if (details['insufficient'] != ['gpu'] or details.get('gpu_requirement') != 'estimated_working_set'
                        or not cache_cannot_resolve):
                    raise
                selected = choose(hardware, **options, allow_capacity_trial=True)
                row.update(capacity_trial=True, resource_error=details)
            row['status'] = 'admitted'
            profile = selected.legacy_profile()
            row['budget_bytes'] = dict(gpu=profile['gpu_budget_gb']*1e9, ram=profile['inference_ram_budget_gb']*1e9)
            row['reserve_bytes'] = dict(gpu=profile.get('policy', {}).get('gpu_system_reserve_bytes'),
                                        ram=profile.get('policy', {}).get('ram_system_reserve_bytes'))
            # Encoding is finished. Do not keep its process if its retained
            # working set already leaves less than the model loader's existing
            # 2 GiB increment inside the new whole-process RAM budget. Release
            # under the lease, then actually measure recovered commit/physical
            # capacity; never invent commit credit for GPU or mapped weights.
            memory = (state.get('memory') or {}) if state else {}
            used = memory.get('guard_bytes')
            mapped = memory.get('reclaimable_mapped_bytes')
            commit = memory.get('system_commit_available_bytes')
            # A read-only text-encoder checkpoint is cheap in the working-set
            # guard, but Windows still charges its section view to Commit until
            # the resident worker exits.  The old test only looked at
            # ``guard_bytes`` and therefore retained a 14--16 GiB mapping on a
            # 30 GiB machine: the next stage then had a 5.5 GiB budget and
            # reopened roughly 20 GiB of transformer weights on every step.
            # Releasing the idle worker is safe after conditioning is saved and
            # is preferable to letting Commit fall through the emergency floor.
            # Require both measurements so a missing/incomplete Windows sample
            # never causes a speculative restart.  On machines with genuine
            # commit headroom the resident encoder remains reusable.
            # Only Windows section views consume Commit while looking small in
            # the working-set guard.  Linux mappings are already represented by
            # MemAvailable/cgroup accounting and must not trigger a Windows
            # style encoder restart.
            mapped_commit_pressure = (
                hardware.system == 'Windows' and
                type(mapped) is int and mapped >= 4 * 2**30 and
                type(commit) is int and commit >= 0 and
                commit < mapped + 2 * 2**30)
            encoder_idle = state and any(m.get('role') == 'encoder' for m in state.get('models', []))
            reclaim = (stage == 'after-encoding' and not attempt and encoder_idle and environment.get(ENV)
                       and ((type(used) is int and used >= 0
                             and used + 2*2**30 > row['budget_bytes']['ram'])
                            or mapped_commit_pressure))
            if not reclaim:
                return hardware, state, profile
            reason = ('Idle encoder working set leaves insufficient model-loading room'
                      if type(used) is int and used >= 0
                      and used + 2*2**30 > row['budget_bytes']['ram']
                      else 'Idle encoder mapping leaves insufficient Windows Commit headroom')
            row.update(status='reclaim-before-load', reason=reason,
                       mapped_bytes=mapped if type(mapped) is int else None,
                       commit_available_bytes=commit if type(commit) is int else None)
        except ResourceBudgetError as error:
            row.update(status='rejected', resource_error=error.details)
            error.resource_planning = evidence
            impossible_capacity = any(error.details[name+'_capacity_bytes'] - error.details[name+'_reserve_bytes']
                                      < error.details[name+'_minimum_bytes'] for name in error.details['insufficient'])
            if attempt or impossible_capacity or not state or not environment.get(ENV):
                raise
        print(json.dumps(dict(event='release_idle_cache', stage=stage,
            reason='Cached models prevent next-stage loading; releasing them and measuring again',
            resources=row.get('resource_error', row.get('budget_bytes')))), flush=True)
        lease_scope = runtime_lock() if descriptor is None else nullcontext(descriptor)
        with lease_scope as lease:
            try:
                row['recovery'] = release_idle_cache(environment, lease)
            except SessionRestarted:
                row['recovery'] = dict(released=True, reason='Old worker already confirmed stopped')
        environment.pop(ENV, None)
        os.environ.pop(ENV, None)


def run(args):
    """Also retain admission/import failures before the main request starts."""
    output = Path(args.out).expanduser().resolve()
    preexisting = any(path.exists() for path in (output, output.with_suffix('.request.json'),
                                               output.with_suffix('.debug.json'), output.with_suffix('.artifacts')))
    started = time.perf_counter()
    try:
        return _run(args)
    except BaseException as error:
        if not preexisting and not getattr(error, 'diagnostic_recorded', False):
            from .diagnostic_resources import exception_details
            from .support_report import write as write_debug, code_identity
            planning = getattr(error, 'resource_planning', [])
            record = dict(success=False, request_seconds=time.perf_counter()-started,
                phase=planning[-1]['stage'] if planning else 'preflight', resource_planning=planning,
                error_type=type(error).__name__, exception=exception_details(error),
                error_message=str(error), resource_error=getattr(error, 'details', None), runtime_code=code_identity())
            try:
                save(output.with_suffix('.request.json'), record)
                write_debug(output, record)
            except (OSError, ValueError):
                pass
        raise


def _run(args):
    from .hardware import detect
    from .geometry import geometry
    canvas = geometry(getattr(args, 'width', 1344), getattr(args, 'height', 768),
                      frames=getattr(args, 'frames', None), seconds=getattr(args, 'seconds', None))
    from .media_request import read as read_media, task_for, cache_key
    media = read_media(getattr(args, 'media', None))
    active_loras = [row for row in media.get('loras', []) if row['strength'] != 0]
    if media:
        inferred_task = task_for(media)
        structural = any(media.get(k) for k in ('first', 'last', 'references', 'conditioning_info'))
        if structural and getattr(args, 'task', None) and args.task != inferred_task:
            raise ValueError('Explicit task disagrees with the media input')
        args.task = inferred_task if structural or not getattr(args, 'task', None) else args.task
        if args.conditioning and any(media.get(k) for k in ('first', 'last', 'references')):
            raise ValueError('Preencoded conditioning already includes media; use conditioning_info, not another set of files')
    base_cache = args.cache
    from .resident_process import credit
    planning = []
    if args.profile:
        hardware, resident_credit = credit(detect())
        profile = json.loads(args.profile.read_text(encoding='utf-8'))
    else:
        hardware, resident_credit, profile = automatic_profile(args, canvas, stage='before-encoding', evidence=planning)
    print(json.dumps(dict(event='compute_device', backend='CUDA', name=getattr(hardware, 'gpu_name', None),
                          uuid=getattr(hardware, 'gpu_uuid', None), vram_total_bytes=getattr(hardware, 'vram_total', None),
                          vram_free_bytes=getattr(hardware, 'vram_free', None))), flush=True)
    allocator_limit_bytes = benchmark_allocator_limit(profile, getattr(args, 'allocator_limit_gib', None), hardware)
    if 'gpu_budget_bytes' in profile:
        profile = dict(gpu_budget_gb=profile['gpu_budget_bytes'] / 1e9,
                       inference_ram_budget_gb=profile['ram_budget_bytes'] / 1e9,
                       engine=profile['engine'], decoder=profile['decoder'],
                       allocator_config=profile['allocator_config'], policy=profile)
    if args.base:
        profile['engine']['base'] = str(args.base.resolve())
    if args.checkpoint:
        profile['engine']['checkpoint'] = str(args.checkpoint.resolve())
    if getattr(args, 'task', None):
        profile['engine']['task'] = args.task
    if allocator_limit_bytes is not None:
        profile['benchmark_allocator_limit_bytes'] = allocator_limit_bytes
    if args.out.suffix.lower() != '.mp4':
        raise ValueError('--out must name an MP4 file')
    manifest = json.loads((args.cache / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('precision') != 'fp8':
        raise ValueError('Select the prepared official FP8 cache')
    destination = args.out.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    artifacts = destination.with_suffix('.artifacts')
    artifacts.mkdir(exist_ok=False)
    if args.prompt_file:
        shutil.copyfile(args.prompt_file, artifacts / 'prompt.txt')
    repo = Path(__file__).resolve().parents[1]
    report = {'profile': profile, 'seed': args.seed, 'success': False, 'geometry': canvas,
              'resource_planning': planning,
              'artifacts': str(artifacts),
              'idle_resources': dict(automatic=not bool(args.profile), system=hardware.system,
                  vram_gib=getattr(args, 'vram_gib', None),
                  gpu_reserve_gib=getattr(args, 'gpu_reserve_gib', None),
                  allocator_limit_bytes=allocator_limit_bytes),
              'reclaimable_resident_models': resident_credit,
              'encoder_mode': 'native H3 encoder library child' if args.prompt_file else 'preencoded shared conditioning'}
    from .support_report import code_identity, write as write_debug
    report['runtime_hardware'] = hardware.to_dict()
    report['runtime_code'] = code_identity()
    started = time.perf_counter()
    applied = None
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'Request interrupted by signal {signum}')
    previous = processes.termination_handler(interrupted)
    try:
        with runtime_lock() as descriptor:
            if active_loras:
                lora_request = artifacts / 'lora-request.json'
                lora_result = artifacts / 'lora-result.json'
                save(lora_request, {'cache': str(args.cache.resolve()), 'adapters': active_loras, 'result': str(lora_result),
                    'mode': 'online' if profile['engine'].get('head_chunk') and profile['engine'].get('inference_kernels') else 'fused'})
                lora_env = dict(os.environ, PYTHONPATH=str(repo), CUDA_VISIBLE_DEVICES='', PYTHONUNBUFFERED='1')
                lora_env[LOCK_ENV] = str(descriptor)
                print(json.dumps({'event': 'lora_prepare_start'}), flush=True)
                report['lora_resources'] = child([sys.executable, '-m', 'freevideo_engine.lora_cache', '--request', str(lora_request)],
                    lora_env, descriptor, destination.with_suffix('.lora.log'),
                    ram_budget_bytes=profile['inference_ram_budget_gb'] * 1e9)
                prepared = json.loads(lora_result.read_text(encoding='utf-8'))
                args.cache = Path(prepared['cache'])
                report['lora'] = prepared['report']
                args._lora_max_block_bytes = prepared['report'].get('max_block_bytes', 0)
                args._lora_root_bytes = prepared['report'].get('root_bytes', 0)
                if args._lora_max_block_bytes or args._lora_root_bytes:
                    profile.setdefault('policy', {})['lora_max_block_bytes'] = args._lora_max_block_bytes
                    profile['policy']['lora_root_bytes'] = args._lora_root_bytes
            resource_identity = local_identity(hardware, args.cache)
            history = ResourceHistory(history_path())
            history.recover_pending()
            report['resource_history'] = str(history.path)
            state = None
            input_cache = None
            report['tuning'] = {'profile_id': None, 'conditioning_cache_hit': False}
            report['input_cache'] = {'enabled': False, 'conditioning_hit': False}
            if not args.profile and not getattr(args, 'no_tuning', False):
                machine_path = tuning.state_path().parents[1] / 'machine.json'
                if machine_path.is_file():
                    try:
                        machine = json.loads(machine_path.read_text(encoding='utf-8'))
                        same_inputs = (base_cache.resolve() == Path(machine['cache']).resolve()
                            and args.encoder == machine['encoder']
                            and Path(args.comfy_python).absolute() == Path(machine['comfy_python']).absolute()
                            and (args.comfy_root or comfy_root()).resolve() == Path(machine['comfy_root']).resolve()
                            and args.model_paths and args.model_paths.resolve() == Path(machine['model_paths']).resolve())
                        if same_inputs:
                            identity = tuning.identity(machine, hardware)
                            state, reason = tuning.load_state(identity)
                            if active_loras:
                                state, reason = None, 'LoRA uses its own model history; encoder cache remains reusable'
                            from .input_cache import InputCache
                            input_cache = InputCache(identity)
                            report['input_cache'].update(enabled=True, directory=str(input_cache.root))
                            report['tuning']['note'] = reason
                        else:
                            report['tuning']['note'] = 'Explicit model/encoder paths differ from the optimized installation'
                    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                        report['tuning']['note'] = 'Could not validate saved tuning: ' + str(error)
            else:
                report['tuning']['note'] = 'Saved optimization bypassed by --no-tuning or --profile'
            if state:
                report['tuning']['baseline_report'] = state.get('baseline_report')
                report['tuning']['optimization_report'] = state.get('optimization_report')
            save(destination.with_suffix('.request.json'), report)
            temporary = artifacts
            allocator = profile.get('allocator_config', os.environ.get('PYTORCH_ALLOC_CONF',
                os.environ.get('PYTORCH_CUDA_ALLOC_CONF', 'backend:native,pinned_max_round_threshold_mb:128')))
            profile['allocator_config'] = allocator
            env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONUNBUFFERED='1',
                       PYTORCH_ALLOC_CONF=allocator, PYTORCH_CUDA_ALLOC_CONF=allocator)
            env[LOCK_ENV] = str(descriptor)
            condition = temporary / 'conditioning.pt'
            if args.conditioning:
                shutil.copyfile(args.conditioning, condition)
            if args.prompt_file:
                encoding = {'prompt': args.prompt_file.read_text(encoding='utf-8'), 'encoder': args.encoder,
                            'comfy_root': str(args.comfy_root or comfy_root()),
                            'model_paths': str(args.model_paths.resolve()) if args.model_paths else None,
                            'output': str(condition), 'gpu_budget_gb': profile['gpu_budget_gb'],
                            'ram_budget_bytes': round(profile['inference_ram_budget_gb'] * 1e9),
                            'metrics': str(destination.with_suffix('.encoding.json')),
                            'diagnostic_output': str(destination)}
                if media:
                    encoding.update(media=media, geometry=canvas, base=profile['engine'].get('base'))
                conditioning_key = cache_key(encoding['prompt'], media, canvas)
                encode_request = temporary / 'encode.json'
                encode_request.write_text(json.dumps(encoding) + '\n', encoding='utf-8')
                cached = input_cache.get(conditioning_key, condition) if input_cache else None
                if cached and (cached.get('success') is not True or cached.get('conditioning_shape', [])[1:] != [5120]):
                    condition.rename(condition.with_name('conditioning.cache-rejected-' + str(time.time_ns()) + '.pt'))
                    cached = None
                if not cached and state and task_for(media) == 't2va':
                    cached = tuning.cached_conditioning(state, encoding['prompt'], condition)
                if cached:
                    save(destination.with_suffix('.encoding.json'), cached)
                    destination.with_suffix('.encoding.log').write_text('Reused validated local text conditioning; encoder model was not loaded.\n', encoding='utf-8')
                    report['tuning']['conditioning_cache_hit'] = True
                    report['input_cache']['conditioning_hit'] = True
                    print(json.dumps({'event': 'conditioning_cache_hit'}), flush=True)
                else:
                    encoder_env = dict(env, PYTHONPATH=str(repo))
                    print(json.dumps({'event': 'encoding_start'}), flush=True)
                    encoder_attempt = history.begin(resource_identity, {'encoder': args.encoder},
                        attempt_geometry(canvas, profile['engine']), purpose='encoding',
                        evidence={'reason': 'Text conditioning cache miss',
                                  'request_artifacts': str(artifacts)})
                    report['encoding_attempt'] = encoder_attempt
                    save(destination.with_suffix('.request.json'), report)
                    try:
                        report['encoding_resources'] = child(
                            [args.comfy_python, '-m', 'freevideo_engine.encode_worker', '--request', str(encode_request)],
                            encoder_env, descriptor, destination.with_suffix('.encoding.log'),
                            ram_budget_bytes=profile['inference_ram_budget_gb'] * 1e9,
                            ram_budget_is_estimate=ram_budget_is_estimate(args),
                            minimum_available_bytes=inference_emergency_floor(profile['inference_ram_budget_gb'] * 1e9,
                                getattr(args, 'ram_reserve_gib', None)),
                            on_start=lambda pid: history.attach_worker(encoder_attempt, pid, phase='encoding'))
                        encoded = json.loads(destination.with_suffix('.encoding.json').read_text(encoding='utf-8'))
                        if encoded.get('success') is not True or not condition.is_file():
                            raise ValueError('Encoder exited without verified conditioning')
                    except BaseException as error:
                        try:
                            failed_encoding = json.loads(destination.with_suffix('.encoding.json').read_text(encoding='utf-8'))
                        except (OSError, ValueError):
                            failed_encoding = {}
                        failure = classify_failure(error, failed_encoding)
                        report['encoding_resources'] = getattr(error, 'telemetry', {})
                        report['encoding_failure'] = dict(phase=failed_encoding.get('phase', 'encoding'), failure=failure)
                        history.finish(encoder_attempt, failure['outcome'], details=failure)
                        raise
                    history.finish(encoder_attempt, 'success', details={'reason': 'Encoder exited with conditioning',
                        'encoding': encoded, 'resources': report.get('encoding_resources'),
                        'prompt_characters': len(encoding['prompt'])})
                report['encoding'] = json.loads(destination.with_suffix('.encoding.json').read_text(encoding='utf-8'))
                report['encoder_process_exited_before_video'] = not bool(os.environ.get('FREEVIDEO_RESIDENT_SESSION'))
                if input_cache and not cached:
                    try:
                        report['input_cache']['conditioning_saved'] = input_cache.put(conditioning_key, condition, report['encoding'])
                        if input_cache.note:
                            report['input_cache']['note'] = input_cache.note
                    except (OSError, ValueError) as error:
                        report['input_cache']['note'] = str(error)
                if state and not cached and task_for(media) == 't2va':
                    try:
                        tuning.remember_conditioning(state, encoding['prompt'], condition, report['encoding'])
                        tuning.save_state(state)
                    except (OSError, ValueError) as error:
                        report['tuning']['cache_note'] = str(error)
            info = report.get('encoding', {}).get('conditioning_info') or media.get('conditioning_info')
            if info:
                if any(info.get(k) != canvas[k] for k in ('width', 'height', 'frames')):
                    raise ValueError('Encoded media does not match the requested geometry')
                for name in ('reference_video_tokens', 'reference_audio_tokens'):
                    count = info.get(name, 0)
                    if type(count) is not int or count < 0:
                        raise ValueError('Invalid reference token count')
                    canvas[name] = count
                profile['engine']['task'] = info['task']
            if not condition.is_file():
                raise FileNotFoundError(condition)
            from .two_pass import plan as plan_sampling, ensure_checkpoint
            sampling_plan = plan_sampling(canvas, getattr(args, 'two_pass', True),
                                          profile['engine'].get('task', 't2va'))
            report['sampling_plan'] = sampling_plan
            if sampling_plan['enabled']:
                if profile['engine'].get('steps', 8) != 8:
                    raise ValueError('Automatic two-pass generation requires the original DMD8 schedule')
                canvas['sampling_plan'] = sampling_plan
            print(json.dumps(dict(event='sampling_plan', **sampling_plan)), flush=True)
            save(destination.with_suffix('.request.json'), report)
            upscaler_checkpoint = None
            if sampling_plan.get('upscaler_sha256'):
                print(json.dumps(dict(event='latent_upscaler_prepare')), flush=True)
                upscaler_checkpoint = str(ensure_checkpoint())
            if not args.profile:
                # Encoding can take minutes; always recheck current free memory,
                # even before any optimization has been saved.
                hardware, resident_credit, live = automatic_profile(args, canvas, stage='after-encoding',
                    evidence=planning, descriptor=descriptor, environment=env)
                report['reclaimable_resident_models'] = resident_credit
                live['engine'].update({k: profile['engine'][k] for k in ('base', 'checkpoint', 'task') if k in profile['engine']})
                profile = live
                report['profile'] = profile
            from .prediction import predict
            from .prediction_ui import show as show_prediction, errors as prediction_errors
            condition_hash = tuning.digest(condition)
            observations = history.observations(identity=resource_identity, limit=512, include_details=False)
            tokens = info.get('text_tokens') if info else report.get('encoding', {}).get('conditioning_shape', [None])[0]
            if tokens is None:
                tokens = next((row['observation'].get('text_tokens', row.get('geometry', {}).get('text_tokens'))
                    for row in reversed(observations)
                    if row['observation'].get('conditioning_sha256') == condition_hash), None)
            if state and args.attention == 'auto' and tokens is not None:
                profile, applied = tuning.apply_profile(state, profile, canvas, tokens)
                report['profile'] = profile
                report['tuning']['profile_id'] = applied
            if allocator_limit_bytes is not None:
                profile['benchmark_allocator_limit_bytes'] = allocator_limit_bytes
            if not args.profile and not getattr(args, 'no_history_placement', False):
                from .prediction_policy import select
                profile, decision = select(profile, canvas, hardware, observations, resource_identity,
                                           text_tokens=tokens)
                report['history_placement'] = decision
                report['profile'] = profile
            from .compatibility import Store as CompatibilityStore, apply as apply_compatibility
            compatibility_state = CompatibilityStore(history.path.parent).status(resource_identity, persist=True)
            profile, report['compatibility'] = apply_compatibility(profile, compatibility_state)
            report['profile'] = profile
            if compatibility_state['level']:
                print(json.dumps(dict(event='compatibility', **report['compatibility'])), flush=True)
            def forecast(selected):
                if not args.profile and report.get('resource_attempts'):
                    # A failed resident worker may have exited and returned tens
                    # of GiB. Refresh limits in both directions; keep the chosen
                    # recovery options so a replan cannot undo offload/recompute.
                    _, _, live = automatic_profile(args, canvas,
                        stage='retry-%d' % (len(report['resource_attempts']) + 1), evidence=planning,
                        descriptor=descriptor, environment=engine_env)
                    for key in ('gpu_budget_gb', 'inference_ram_budget_gb'):
                        selected[key] = live[key]
                    if 'policy' in selected:
                        selected['policy'].update({k: v for k, v in live.get('policy', {}).items()
                                                  if k not in ('engine', 'decoder')})
                report['resource_prediction'] = predict(observations, resource_identity, selected, canvas, text_tokens=tokens)
                show_prediction(report['resource_prediction'])
            def validate(metrics, telemetry, selected):
                observation = complete_observation(metrics, canvas, destination, artifacts, telemetry)
                if observation is not None:
                    observation['conditioning_sha256'] = condition_hash
                    report['resource_prediction_error'] = prediction_errors(report['resource_prediction'], observation)
                return observation
            # A live policy may have replaced the initial profile after text
            # encoding. Record and execute that exact allocator consistently.
            allocator = profile.get('allocator_config', allocator)
            profile['allocator_config'] = allocator
            env.update(PYTORCH_ALLOC_CONF=allocator, PYTORCH_CUDA_ALLOC_CONF=allocator)
            save(destination.with_suffix('.request.json'), report)
            request = {'conditioning': str(condition), 'seed': args.seed, 'cache': str(args.cache.resolve()),
                       'input_cache_dir': str(input_cache.root.parent.parent) if input_cache else None,
                       'gpu_budget_bytes': round(profile['gpu_budget_gb'] * 1e9), 'engine_options': profile['engine'],
                       'decoder_options': profile['decoder'], 'output': str(destination),
                       'geometry': canvas, 'artifacts': str(artifacts),
                       'sampling_plan': sampling_plan, 'upscaler_checkpoint': upscaler_checkpoint,
                       'automatic_pass_cache': not bool(args.profile),
                       'metrics': str(destination.with_suffix('.engine.json'))}
            if allocator_limit_bytes is not None:
                request['allocator_limit_bytes'] = allocator_limit_bytes
                report['benchmark_limit'] = {'pytorch_allocator_bytes': request['allocator_limit_bytes'],
                    'scope': 'PyTorch CUDA allocator only; other CUDA allocations and whole-device usage are measured separately.'}
            request_path = temporary / 'video.json'
            resume_decode = None
            # Keep explicit dependency overlays while keeping the snapshotted engine first.
            engine_env = dict(env, PYTHONPATH=os.pathsep.join((str(repo), str(vdn_root()),
                                                              env.get('PYTHONPATH', ''))))
            def persist():
                save(destination.with_suffix('.request.json'), report)
            def launch(selected, attempt):
                selected_env = inference_environment(engine_env, selected['inference_ram_budget_gb'] * 1e9)
                request.update(engine_options=selected['engine'], decoder_options=selected['decoder'],
                               ram_budget_bytes=round(selected['inference_ram_budget_gb'] * 1e9),
                               gpu_budget_bytes=round(selected['gpu_budget_gb'] * 1e9),
                               gpu_reserve_bytes=selected.get('policy', {}).get('gpu_system_reserve_bytes', 0),
                               capacity_trial=bool(selected.get('policy', {}).get('capacity_trial')),
                               resource_attempt=attempt)
                request.pop('first_pass_policy', None)
                request['automatic_pass_cache'] = not bool(args.profile) and len(report.get('resource_attempts', [])) <= 1
                # Explicit profiles and recovery placements retain their cap.
                # Automatic Windows requests can use a later driver-budget rise
                # without exceeding a user/benchmark capacity constraint.
                request['dynamic_gpu_budget'] = (hardware.system == 'Windows' and
                    request['automatic_pass_cache'] and getattr(args, 'gpu_reserve_gib', None) is None)
                request['gpu_capacity_bytes'] = min(hardware.vram_total,
                    int(args.vram_gib * 2**30) if args.vram_gib else hardware.vram_total)
                if request['automatic_pass_cache']:
                    from .two_pass import first_pass_policy
                    first_policy = first_pass_policy(selected, canvas, sampling_plan)
                    if first_policy is not None:
                        first_policy['profile'], _ = apply_compatibility(first_policy['profile'], compatibility_state)
                        request['first_pass_policy'] = first_policy
                        report['first_pass_policy'] = first_policy
                if resume_decode is not None:
                    request['resume_decode'] = dict(resume_decode)
                else:
                    request.pop('resume_decode', None)
                save(request_path, request)
                print(json.dumps({'event': 'decode_resume' if resume_decode else 'video_start', 'resource_attempt': attempt,
                                  'engine': selected['engine'], 'decoder': selected['decoder']}), flush=True)
                try:
                    telemetry = child([sys.executable, '-m', 'freevideo_engine.worker', '--request', str(request_path)],
                        selected_env, descriptor, destination.with_suffix('.engine.log'),
                        ram_budget_bytes=selected['inference_ram_budget_gb'] * 1e9,
                        ram_budget_is_estimate=ram_budget_is_estimate(args),
                        minimum_available_bytes=inference_emergency_floor(selected['inference_ram_budget_gb'] * 1e9,
                            getattr(args, 'ram_reserve_gib', None)),
                        gpu=resource_identity['gpu']['uuid'],
                        on_start=lambda pid: history.attach_worker(attempt, pid, phase='video'))
                except BaseException as error:
                    try:
                        error.metrics = json.loads(destination.with_suffix('.engine.json').read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        pass
                    if isinstance(getattr(error, 'metrics', None), dict) and not error.metrics.get('sampling_memory'):
                        try:
                            from .diagnostics import read_bounded
                            raw, truncated = read_bounded(destination.with_suffix('.sampling-memory.json'), 2 * 2**20)
                            observed = json.loads(raw) if not truncated else None
                            if isinstance(observed, dict):
                                error.metrics['sampling_memory'] = observed
                        except (OSError, ValueError):
                            pass
                    raise
                return json.loads(destination.with_suffix('.engine.json').read_text(encoding='utf-8')), telemetry
            def archive(index, row):
                nonlocal resume_decode
                retained = artifacts / 'attempts' / ('%02d-%s' % (index + 1, row['id']))
                retained.mkdir(parents=True, exist_ok=False)
                paths = [destination] + [destination.with_suffix('.engine.' + ext)
                         for ext in ('json','log','memory.json','gpu.json','gpu.csv')]
                paths.append(destination.with_suffix('.sampling-memory.json'))
                paths += [artifacts/name for name in ('latents.pt','rgb.npy','audio.npy','audio.wav','video.json')]
                for path in paths:
                    if path.exists():
                        path.rename(retained / path.name)
                save(retained / 'attempt.json', row)
                # Reuse only completed sampling from this request, never a
                # partial sample. The worker validates provenance and tensor
                # metadata again before loading the saved video/audio latents.
                # The controller's durable row can still say ``sample`` when
                # the worker has already written a complete sampling receipt
                # and is failing while releasing streamed weights.  The
                # worker receipt is authoritative: inspect it regardless of
                # the parent monitor phase, then require completed_sampling()
                # to validate all planned finite steps, provenance, and the
                # retained latent checkpoint before switching to decode-only.
                if resume_decode is None:
                    try:
                        sample_metrics = retained / destination.with_suffix('.engine.json').name
                        sampled = json.loads(sample_metrics.read_text(encoding='utf-8'))
                        from .decode_resume import completed_sampling
                        if (completed_sampling(sampled)
                                and (retained / 'latents.pt').is_file() and (retained / 'latents.pt').stat().st_size > 0):
                            resume_decode = dict(latents=str(retained / 'latents.pt'),
                                metrics=str(sample_metrics), request=str(retained / 'video.json'))
                    except (OSError, ValueError, TypeError):
                        pass  # Older/incomplete artifacts retain full-request recovery.
                return str(retained)
            def retry(index, row, selected, decision):
                retry_state = dict(attempt=index + 2, max_attempts=getattr(args, 'resource_retries', 2) + 1,
                                   failed_phase=row.get('phase'), kind=row['failure']['kind'],
                                   reuse_sampling=resume_decode is not None)
                row['recovery'] = dict(decision, next_profile=selected, **retry_state)
                persist()
                print(json.dumps(dict(event='resource_retry', retained=row['retained'], retry=retry_state)), flush=True)
                from .support_report import write_retry
                write_retry(destination, report, index=index + 1, retained=row['retained'],
                            next_profile=selected, decision=row['recovery'])
            report['video'], report['resources'], profile = execute_attempts(
                history, resource_identity, profile, canvas, launch,
                validate=validate, prepare=forecast,
                archive=archive, report=report, persist=persist, on_retry=retry,
                automatic=not args.profile or getattr(args, 'recover_placement', False),
                max_retries=getattr(args, 'resource_retries', 2))
            report['success'] = True
    except BaseException as error:
        from .diagnostic_resources import exception_details
        report['exception'] = exception_details(error)
        error.diagnostic_recorded = True
        report['error'] = repr(error)
        report['error_message'] = str(error)
        report['error_type'] = type(error).__name__
        if hasattr(error, 'details'):
            report['resource_error'] = error.details
        if applied:
            report['tuning']['disabled_after_failure'] = tuning.disable_profile(applied, error)
        raise
    finally:
        processes.restore_handlers(previous)
        report['request_seconds'] = time.perf_counter() - started
        diagnostic = write_debug(destination, report)
        if diagnostic:
            report['diagnostic_file'] = diagnostic.name
        save(destination.with_suffix('.request.json'), report)


if __name__ == '__main__':
    main()
