"""Isolated VDN sampling and decode worker for one Engine request."""
import argparse
import json
import os
from pathlib import Path
import threading
import time

import torch

from .policy import memory_fraction
from .runtime import Engine
from .decode import decode_to_file
from .locking import runtime_lock
from .monitoring import save
from .processes import worker_signals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding='utf-8'))
    with worker_signals(), runtime_lock():
        generate(request)


def prefetch_compiler_keys():
    """Hash the installed torch and Triton sources while models load.

    The first torch.compile in a process hashes every installed torch source
    file into its cache key; on Windows with real-time scanning that took
    7-8 s of the first sampling step. torch caches the value per process and
    can compute it ahead; the keys themselves are unchanged."""
    def work():
        try:
            from torch._inductor import codecache
        except Exception:
            return
        for key in (getattr(codecache, 'torch_key', None), getattr(codecache, 'triton_key', None)):
            try:
                prefetch = getattr(key, 'prefetch', None)
                if callable(prefetch):
                    prefetch()
                elif callable(key):
                    key()
            except Exception:
                pass  # The first compile computes it as before.
    thread = threading.Thread(target=work, name='freevideo-compiler-keys', daemon=True)
    thread.start()
    return thread


def device_memory_report(budget_bytes, allocator_limit_bytes=None, *, reserve_bytes=0, capacity_trial=False):
    from .gpu_budget import configure
    return configure(torch, budget_bytes, allocator_limit_bytes, reserve_bytes=reserve_bytes,
                     capacity_trial=capacity_trial)


def generate(request, resident=None):
    torch.set_num_threads(8)
    started = time.perf_counter()
    prefetch_compiler_keys()
    engine = None
    live_budget = None
    decode_read_ahead = None
    decoder_read_ahead_closed = False
    original_error = None
    metrics = {'success': False, 'phase': 'load', 'geometry': request.get('geometry'),
               'gpu_budget_bytes': request['gpu_budget_bytes'],
               'compiler_threads': os.environ.get('TORCHINDUCTOR_COMPILE_THREADS'),
               'execution_context': {'host_threads': {
                   'OMP_NUM_THREADS': os.environ.get('OMP_NUM_THREADS'),
                   'MKL_NUM_THREADS': os.environ.get('MKL_NUM_THREADS'),
                   'torch_num_threads': torch.get_num_threads()}}}
    save(request['metrics'], metrics)
    try:
        if 'sampling_plan' in request:
            from .two_pass import plan
            expected = plan(request['geometry'], request['sampling_plan']['requested'],
                            request['engine_options'].get('task', 't2va'),
                            base_steps=request['engine_options'].get('steps', 8),
                            refine_steps=request['sampling_plan']['refine_steps'] if request['sampling_plan']['enabled'] else 2)
            if request['sampling_plan'] != expected:
                raise ValueError('Sampling plan does not match the requested canvas and task')
            metrics['sampling_plan'] = expected
        metrics['device_memory'] = device_memory_report(request['gpu_budget_bytes'], request.get('allocator_limit_bytes'),
            reserve_bytes=request.get('gpu_reserve_bytes', 0), capacity_trial=request.get('capacity_trial', False))
        if request.get('dynamic_gpu_budget') and metrics['device_memory'].get('windows_allocator_limit_enforced'):
            from .gpu_budget import LiveGPUBudget
            reserve = request.get('gpu_reserve_bytes', 0)
            live_budget = LiveGPUBudget(torch, metrics['device_memory'],
                request.get('gpu_capacity_bytes', metrics['device_memory']['device_total_bytes']) - reserve, reserve)

        def refresh_budget(stage, reclaim=None):
            def release(target):
                if reclaim is not None:
                    reclaim(target)
                if resident is not None:
                    for role in tuple(resident.entries):
                        if torch.cuda.memory_allocated() <= target:
                            break
                        if role != 'engine' or stage == 'decode':
                            resident.drop(role, 'Live GPU budget decreased')
            limit = live_budget.refresh(stage, release)
            if resident is not None:
                resident.gpu_budget = limit
            return limit
        if resident is not None:
            limit = metrics['device_memory'].get('effective_allocator_limit_bytes')
            if limit is not None:
                resident.gpu_budget = min(resident.gpu_budget, limit)
        save(request['metrics'], metrics)
        manifest = json.loads((Path(request['cache']) / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('precision') != 'fp8':
            raise ValueError('The Engine requires an official FP8 cache')
        options, decoder = request['engine_options'], request['decoder_options']
        resume = request.get('resume_decode') is not None
        if not resume and options.get('task', 't2va') != 't2va':
            from .media_conditioning import describe
            canvas = request.get('geometry', {})
            value = torch.load(request['conditioning'], map_location='cpu', weights_only=True)
            # Older official keyframe caches have no explicit task field.
            value.setdefault('task', options['task'])
            actual = describe(value, canvas['width'], canvas['height'], canvas['frames'])
            if actual['task'] != options['task'] or any(canvas.get(name, actual[name]) != actual[name]
                    for name in ('reference_video_tokens', 'reference_audio_tokens')):
                raise ValueError('Encoded media disagrees with the admitted request layout')
            del value
            metrics['conditioning_info'] = actual
        canvas = request.get('geometry', {})
        artifacts = Path(request['artifacts']) if request.get('artifacts') else None
        if resume:
            from .decode_resume import load_saved_sampling
            metrics.update(phase='decode', decode_phase='latent_load')
            save(request['metrics'], metrics)
            tick = time.perf_counter()
            latents, audio, sampled, base = load_saved_sampling(request, torch)
            metrics.update(sampled, phase='decode', decode_phase='latent_transfer')
            save(request['metrics'], metrics)
            print(json.dumps(dict(event='decode_resume', sampling_source_attempt=metrics['sampling_source_attempt'])), flush=True)
            if resident is not None:
                resident.decoder_room(decoder, canvas)
            latents, audio = latents.to('cuda'), audio.to('cuda')
            metrics['latent_reload_seconds'] = time.perf_counter() - tick
            metrics['transformer_released_before_decode'] = resident is None or 'engine' not in resident.entries
        else:
            tick = time.perf_counter()
            factory = lambda: Engine(request['cache'], input_cache_dir=request.get('input_cache_dir'),
                                     **dict(options, canvas=request.get('geometry')))
            engine, reused = resident.engine(request, factory) if resident is not None else (factory(), False)
            loaded_seconds = time.perf_counter() - tick
            metrics.update(config=dict(engine.config), load_seconds=loaded_seconds,
                           resident_engine_cache_hit=reused,
                           load_breakdown=({'resident_lookup_seconds': loaded_seconds} if reused else engine.load_breakdown), phase='sample',
                           load_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                           load_peak_reserved_bytes=torch.cuda.max_memory_reserved())
            if live_budget is not None:
                refresh_budget('load')
            if resident is not None:
                metrics['resident_admission'] = list(resident.decisions)
            save(request['metrics'], metrics)
            print(json.dumps({'event': 'loaded', 'seconds': loaded_seconds, 'resident_cache_hit': reused}), flush=True)
            foreign_sample_bytes = 0
            if resident is not None:
                from .resident_models import gpu_bytes
                foreign_sample_bytes = max(0, torch.cuda.memory_allocated() - gpu_bytes(engine))
            from .decode_prefetch import DecoderReadAhead
            decode_read_ahead = DecoderReadAhead(engine.base, ram_budget_bytes=request.get('ram_budget_bytes'))
            decoder_cached = resident is not None and 'video_vae' in resident.entries
            if decoder_cached:
                decode_read_ahead.result.update(state='skipped', reason='Video VAE is already resident on the GPU')
            from .sampling_memory import SamplingMemory
            memory_diagnostics = SamplingMemory(torch, Path(request['output']).with_suffix('.sampling-memory.json')).start()
            def sample_finalize_phase(name):
                metrics.update(phase='sample_finalize', sample_finalize_phase=name)
                save(request['metrics'], metrics)
                print(json.dumps(dict(event='sample_finalize', phase=name)), flush=True)

            def sampling_complete(latents, audio, sampled):
                metrics.update(sampled, sampling_checkpoint_complete=False,
                    sample_metrics_scope='Completed sampling before offloader cleanup; finalization is excluded.')
                metrics['sampling_memory'] = memory_diagnostics.result()
                sample_finalize_phase('latent_validation')
                if not bool(torch.isfinite(latents).all() and torch.isfinite(audio).all()):
                    raise RuntimeError('Non-finite video/audio latents; refusing to save a corrupted result')
                metrics['finite_latents'] = True
                if artifacts:
                    from .decode_resume import sampling_provenance, save_sampling
                    tick = time.perf_counter()
                    sample_finalize_phase('latent_save')
                    provenance = sampling_provenance(request)
                    save_sampling({'video': latents.cpu(), 'audio': audio.cpu(), 'seed': request['seed'],
                                   'geometry': canvas, 'sampling_provenance': provenance}, artifacts, torch)
                    metrics.update(sampling_provenance=provenance, sampling_source_attempt=request.get('resource_attempt'),
                                   latent_save_seconds=time.perf_counter() - tick, sampling_checkpoint_complete=True)
                    # The final latents supersede this attempt's retained first pass.
                    (artifacts / 'refine-input.pt').unlink(missing_ok=True)
                sample_finalize_phase('offload_release')
            try:
                sampling_plan = request.get('sampling_plan', {})
                if sampling_plan.get('enabled'):
                    from .two_pass import upscale_workspace, crop_latents
                    from .two_pass_metrics import combine
                    from .latent_upscale import upscale
                    if engine.steps != sampling_plan['base_steps']:
                        raise ValueError('Engine steps disagree with the requested sampling plan')
                    if request.get('resume_refine'):
                        # A retry after a second-pass failure: this request's
                        # retained first pass, latent upscale and crop are
                        # reused instead of sampling them again.
                        from .decode_resume import load_refine_input
                        metrics.update(sample_stage='first_pass_load')
                        save(request['metrics'], metrics)
                        tick = time.perf_counter()
                        latents, audio, first, lifted, reused = load_refine_input(request, torch)
                        latents, audio = latents.to('cuda'), audio.to('cuda')
                        metrics.update(reused, sampling_passes=[first], latent_upscale=lifted,
                                       first_pass_load_seconds=time.perf_counter() - tick)
                        print(json.dumps(dict(event='first_pass_reused', source_attempt=reused['first_pass_source_attempt'],
                                              completed_steps=sampling_plan['base_steps'],
                                              total=sampling_plan['total_steps'])), flush=True)
                    else:
                        pass_budget = (min(request['gpu_budget_bytes'],
                            metrics['device_memory'].get('effective_allocator_limit_bytes') or request['gpu_budget_bytes'])
                            if request.get('automatic_pass_cache', False) else None)
                        first_options = request.get('first_pass_policy', {}).get('profile', {}).get('engine')
                        metrics['first_pass_policy'] = request.get('first_pass_policy')
                        latents, audio, first = engine.sample(request['conditioning'], request['seed'],
                            **sampling_plan['first'], allow_smaller_canvas=True,
                            pass_cache_budget_bytes=pass_budget, gpu_reserve_bytes=request.get('gpu_reserve_bytes', 0),
                            compute_options=first_options,
                            pass_resident_blocks=first_options.get('resident_blocks') if first_options else None,
                            step_callback=memory_diagnostics.complete, progress_total=sampling_plan['total_steps'],
                            budget_refresh=refresh_budget if live_budget is not None else None)
                        metrics.update(sampling_passes=[first], sample_stage='latent_upscale')
                        save(request['metrics'], metrics)
                        print(json.dumps(dict(event='latent_upscale', completed_steps=sampling_plan['base_steps'],
                                              total=sampling_plan['total_steps'])), flush=True)
                        tick = time.perf_counter()
                        if live_budget is not None:
                            refresh_budget('upscale')
                        need = upscale_workspace(sampling_plan['upscale_target']) if sampling_plan['upscaler_sha256'] else 0
                        if need and resident is not None and not resident.ram_fits(2 * 2**30):
                            resident.make_room('latent_upscaler', need, ram_need=2 * 2**30)
                        torch.cuda.empty_cache()
                        def upscale_available():
                            free, _ = torch.cuda.mem_get_info()
                            budget = metrics['device_memory'].get('effective_allocator_limit_bytes') or request['gpu_budget_bytes']
                            return min(free - request.get('gpu_reserve_bytes', 0), budget - torch.cuda.memory_allocated())
                        available = upscale_available()
                        # Prefer buffer reuse before discarding useful host weights.
                        # The estimate selects this path, not permission to run.
                        # Actual allocation remains bounded by the worker's CUDA
                        # allocator limit. Keep the completed first-pass latents.
                        memory_saving = available < need
                        torch.cuda.reset_peak_memory_stats()
                        lift_canvas = sampling_plan['upscale_target']
                        retry_upscale = False
                        upscale_failures = []
                        metrics['latent_upscale'] = dict(estimated_workspace_bytes=need,
                            available_workspace_bytes=max(0, available), attempted_below_estimate=memory_saving,
                            buffer_reuse=memory_saving, allocation_retries=0, allocation_failures=upscale_failures)
                        save(request['metrics'], metrics)
                        try:
                            kwargs = dict(memory_saving=True) if memory_saving else {}
                            lifted_video, lifted = upscale(latents, request.get('upscaler_checkpoint'),
                                lift_canvas['width'], lift_canvas['height'], **kwargs)
                        except Exception as error:
                            from .adaptive import classify_failure
                            if classify_failure(error)['kind'] != 'gpu_oom' or (engine.closed and memory_saving):
                                raise
                            from .diagnostic_resources import error_details
                            upscale_failures.append(error_details(error))
                            retry_upscale = True
                        if retry_upscale:
                            # Leave the failed call's traceback before retrying so
                            # its tensors no longer occupy the allocator. Do not
                            # sample again or change the temporal normalization.
                            if not engine.closed:
                                if resident is not None:
                                    resident.drop('engine', 'Latent upscale allocation needs additional workspace')
                                else:
                                    engine.close()
                            if resident is not None:
                                resident.make_room('latent_upscaler', need, ram_need=2 * 2**30)
                            import gc
                            gc.collect()
                            torch.cuda.empty_cache()
                            metrics['latent_upscale'].update(buffer_reuse=True, allocation_retries=1)
                            save(request['metrics'], metrics)
                            lifted_video, lifted = upscale(latents, request.get('upscaler_checkpoint'),
                                lift_canvas['width'], lift_canvas['height'], memory_saving=True)
                        latents = lifted_video
                        del lifted_video
                        latents = crop_latents(latents, sampling_plan)
                        lifted.update(stage_seconds=time.perf_counter() - tick,
                            crop=dict(sampling_plan['crop']),
                            estimated_workspace_bytes=need,
                            available_workspace_bytes=max(0, available),
                            attempted_below_estimate=memory_saving,
                            allocation_retries=len(upscale_failures),
                            allocation_failures=upscale_failures,
                            torch_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                            torch_peak_reserved_bytes=torch.cuda.max_memory_reserved(),
                            transformer_reloaded=engine.closed)
                        metrics['latent_upscale'] = lifted
                        if engine.closed:
                            tick = time.perf_counter()
                            engine, _ = resident.engine(request, factory) if resident is not None else (factory(), False)
                            reload_seconds = time.perf_counter() - tick
                            metrics['load_seconds'] += reload_seconds
                            metrics['load_breakdown']['two_pass_reload_seconds'] = reload_seconds
                        if artifacts:
                            # Retain the second pass's input so an out-of-memory
                            # retry starts there. A failed save only loses that.
                            try:
                                from .decode_resume import refine_provenance, save_refine_input
                                provenance = refine_provenance(request)
                                save_refine_input({'video': latents.cpu(), 'audio': audio.cpu(), 'seed': request['seed'],
                                                   'geometry': canvas, 'refine_provenance': provenance}, artifacts, torch)
                                metrics.update(refine_checkpoint_complete=True, refine_provenance=provenance,
                                               refine_source_attempt=request.get('resource_attempt'))
                            except (OSError, RuntimeError, ValueError) as error:
                                metrics['refine_checkpoint_error'] = str(error)
                    metrics['sample_stage'] = 'refine'
                    save(request['metrics'], metrics)
                    def refined_complete(video, sound, receipt):
                        sampling_complete(video, sound, combine(first, receipt, lifted, sampling_plan))
                    restart_seed = (request['seed'] + sampling_plan['restart_seed_offset']) % (1 << 64)
                    latents, audio, second = engine.sample(request['conditioning'], restart_seed,
                        **sampling_plan['second'], initial_latents=(latents, audio), refine_steps=sampling_plan['refine_steps'],
                        final_step_callback=None if decoder_cached else decode_read_ahead.start,
                        step_callback=memory_diagnostics.complete, sampling_complete_callback=refined_complete,
                        progress_offset=sampling_plan['base_steps'], progress_total=sampling_plan['total_steps'],
                        gpu_reserve_bytes=request.get('gpu_reserve_bytes', 0),
                        budget_refresh=refresh_budget if live_budget is not None else None)
                    sampled = combine(first, second, lifted, sampling_plan)
                    metrics.pop('sample_stage', None)
                else:
                    latents, audio, sampled = engine.sample(request['conditioning'], request['seed'],
                        frames=canvas.get('frames', 243), width=canvas.get('width', 1344), height=canvas.get('height', 768),
                        final_step_callback=None if decoder_cached else decode_read_ahead.start,
                        step_callback=memory_diagnostics.complete, sampling_complete_callback=sampling_complete,
                        gpu_reserve_bytes=request.get('gpu_reserve_bytes', 0),
                        budget_refresh=refresh_budget if live_budget is not None else None)
            finally:
                metrics['sampling_memory'] = memory_diagnostics.close()
            metrics.update(sampled)
            metrics.pop('sample_metrics_scope', None)
            base = engine.base
            sample_finalize_phase('transformer_release')
            # A one-shot worker releases the transformer. An interactive worker
            # keeps it if live memory also covers decoding, otherwise evicts it.
            if resident is not None:
                resident.decoder_room(decoder, canvas)
            else:
                engine.close()
            metrics['transformer_released_before_decode'] = engine.closed
            # Keep the bounded file reader alive through transformer teardown.
            # Releasing DiT mappings is independent of the decoder checkpoint,
            # so both operations can run concurrently and give slow storage
            # more time to populate the reclaimable OS cache.  close() still
            # cancels promptly when a memory gate or error stops the request.
            read_ahead = decode_read_ahead.close()
            if sampled.get('decoder_prefetch'):
                read_ahead['sampling'] = dict(sampled['decoder_prefetch'])
            metrics['decoder_read_ahead'] = read_ahead
            decoder_read_ahead_closed = True
            metrics.pop('sample_finalize_phase', None)
            metrics['decode_phase'] = 'decoder_admission'
        metrics['phase'] = 'decode'
        if live_budget is not None:
            refresh_budget('decode')
            if engine is not None:
                metrics['transformer_released_before_decode'] = engine.closed
        if resident is not None:
            metrics['resident_admission'] = list(resident.decisions)
        save(request['metrics'], metrics)
        foreign_decode_bytes = 0
        if resident is not None:
            from .resident_models import gpu_bytes
            foreign_decode_bytes = sum(gpu_bytes(entry['model']) for role, entry in resident.entries.items()
                                       if role not in ('video_vae', 'audio_vae'))
        torch.cuda.reset_peak_memory_stats()
        def decode_phase(name):
            metrics['decode_phase'] = name
            if resident is not None:
                metrics['resident_admission'] = list(resident.decisions)
            save(request['metrics'], metrics)
        metrics.update(decode_to_file(latents, audio, request['output'], base=base, artifacts_dir=artifacts,
                                     model_cache=resident, phase_callback=decode_phase, **decoder))
        if resume and artifacts:
            from .decode_resume import retain_latents
            retain_latents(request['resume_decode']['latents'], artifacts)
        metrics.update(success=True, phase='complete')
        if resident is not None:
            from .resident_models import key
            if not resume:
                from .two_pass_metrics import sampling_workspace_peak
                resident.observe_peak('engine', resident.engine_key(request),
                    sampling_workspace_peak(sampled) - foreign_sample_bytes)
            resident.observe_peak('decode', key(dict(options=decoder, canvas=canvas)), torch.cuda.max_memory_allocated() - foreign_decode_bytes)
            metrics['resident_models'] = resident.snapshot()
    except BaseException as error:
        original_error = error
        metrics['error'] = repr(error)
        from .adaptive import classify_failure
        metrics['failure'] = classify_failure(error)
        # A driver query can itself fail. Persist the original exception first.
        save(request['metrics'], metrics)
        from .encoder_memory import failure_resources
        metrics['failure'].update(failure_resources(torch, query_cuda=metrics['failure']['kind'] != 'cuda_error'))
        if isinstance(error, torch.cuda.OutOfMemoryError):
            metrics['failure'].update(kind='gpu_oom', outcome='resource_failure', retryable=True)
            metrics['failure']['device_memory'] = metrics.get('device_memory', {})
        # Write the first failure before any cleanup CUDA call: the device may
        # no longer be readable and that secondary error is not its cause.
        if resident is not None:
            metrics['resident_admission'] = list(resident.decisions)
        save(request['metrics'], metrics)
        raise
    finally:
        if live_budget is not None:
            live_budget.close()
        if resident is not None:
            metrics['resident_admission'] = list(resident.decisions)
        from .adaptive import classify_failure
        if decode_read_ahead is not None and not decoder_read_ahead_closed:
            metrics['decoder_read_ahead'] = decode_read_ahead.close()
        cleanup_error = None
        unsafe = classify_failure(original_error or '', metrics)['kind'] == 'cuda_error'
        if unsafe:
            metrics['cleanup_skipped'] = 'Unsafe CUDA evidence; retain first failure and let the isolated process exit.'
        if engine is not None and not unsafe and (resident is None or not metrics['success']):
            try:
                engine.close()
            except BaseException as error:
                cleanup_error = error
                metrics.setdefault('cleanup_errors', []).append(repr(error))
        metrics['work_seconds'] = time.perf_counter() - started
        unsafe = classify_failure(original_error or '', metrics)['kind'] == 'cuda_error'
        if not unsafe and torch.cuda.is_initialized():
            try:
                metrics['final_stage_peak_allocated_bytes'] = torch.cuda.max_memory_allocated()
                metrics['final_stage_peak_reserved_bytes'] = torch.cuda.max_memory_reserved()
            except BaseException as error:
                cleanup_error = cleanup_error or error
                metrics.setdefault('cleanup_errors', []).append(repr(error))
        if cleanup_error is not None and original_error is None:
            from .adaptive import classify_failure
            metrics.update(success=False, error=repr(cleanup_error), failure=classify_failure(cleanup_error))
        save(request['metrics'], metrics)
        if cleanup_error is not None and original_error is None:
            raise cleanup_error


if __name__ == '__main__':
    main()
