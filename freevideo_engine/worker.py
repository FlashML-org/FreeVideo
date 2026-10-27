"""Isolated VDN sampling and decode worker for one Engine request."""
import argparse
import json
import os
from pathlib import Path
import threading
import time

if __name__ == '__main__':
    # Before torch and the CUDA libraries load. With the stage event before the
    # model and the first prepared_blocks, a request held at "Loading video
    # model" shows in its log whether the process started, got as far as the
    # model, or stopped while reading blocks.
    print(json.dumps({'event': 'worker_start', 'pid': os.getpid(), 'epoch': time.time()}), flush=True)

import torch

from .device_index import observed_card
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
    from .backends import get_backend
    return get_backend(torch_module=torch).configure_budget(
        budget_bytes, allocator_limit_bytes, reserve_bytes=reserve_bytes, capacity_trial=capacity_trial)


def generate(request, resident=None):
    def released(engine):
        # A parked engine (ModelBank.park) holds no GPU weights either.
        return engine.closed or type(getattr(engine, 'parked', None)) is list
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
        from .continuation import validate_hide_tail
        hide_tail_frames = validate_hide_tail(request.get('hide_tail_frames', 0))
        if hide_tail_frames:
            metrics['hide_tail_frames'] = hide_tail_frames
        if 'sampling_plan' in request:
            from .two_pass import plan
            expected = plan(request['geometry'], request['sampling_plan']['requested'],
                            request['engine_options'].get('task', 't2va'),
                            base_steps=request['engine_options'].get('steps', 8),
                            refine_steps=request['sampling_plan']['refine_steps'] if request['sampling_plan']['enabled'] else 3,
                            staged=request['sampling_plan'].get('staged', False),
                            scale=request['sampling_plan'].get('scale', 2))
            if request['sampling_plan'] != expected:
                raise ValueError('Sampling plan does not match the requested canvas and task')
            metrics['sampling_plan'] = expected
        original_audio = request.get('original_audio')
        if original_audio is not None:
            from .original_audio import verify, AudioUseError
            verify(original_audio)
            if request.get('continuation') is not None and 'segment' not in original_audio:
                raise AudioUseError('audio_original_route')
            if request['engine_options'].get('task') not in ('ref2va_audio', 'ref2va_av'):
                raise ValueError('Original audio requires a clean audio reference layout')
            if hide_tail_frames:
                # The clip's samples set the part's length; no frames are hidden after it.
                raise AudioUseError('audio_original_route')
            metrics['original_audio'] = original_audio
        continuation = request.get('continuation')
        if continuation is not None:
            from .continuation import (validate_route, summary, ADALN_TASK, ANCHOR_ADALN_TASK, UNSUPPORTED_DEVICE,
                                       validate_audio_context, AUDIO_CONTEXT_DEFAULT)
            # A plan that fell back to one pass on a small canvas still has
            # the two-pass flag set and keeps its continuation; an explicit
            # single-pass request does not.
            validate_route(task=request['engine_options'].get('task', 't2va'),
                           anchor=bool(continuation.get('anchor')),
                           **(dict(sound=True, media=dict(references=[continuation['sound']]))
                              if continuation.get('sound') else {}),
                           two_pass=bool(request.get('sampling_plan', {}).get('requested')),
                           preview=request.get('preview', False), refine_from=request.get('resume_first_pass'),
                           original_audio=original_audio is not None and 'segment' in original_audio)
            if isinstance(getattr(torch.version, 'hip', None), str):
                raise ValueError(UNSUPPORTED_DEVICE)
            continuation = dict(continuation,
                audio_context=validate_audio_context(continuation.get('audio_context', AUDIO_CONTEXT_DEFAULT)))
            if original_audio is not None:
                from .original_audio_chain import validate_continuation
                validate_continuation(original_audio, continuation)
                continuation.update(video_only=True, audio_context='clean')
            request['continuation'] = continuation
            request['engine_options'] = dict(request['engine_options'],
                adaln_task=(request['engine_options']['task'] if original_audio is not None
                            else ANCHOR_ADALN_TASK if continuation.get('anchor') else ADALN_TASK))
            metrics['continuation'] = summary(continuation, hide_tail_frames=hide_tail_frames)
            metrics['audio_context'] = continuation['audio_context']
        metrics['gpu_card'] = observed_card(torch)
        metrics['device_memory'] = device_memory_report(request['gpu_budget_bytes'], request.get('allocator_limit_bytes'),
            reserve_bytes=request.get('gpu_reserve_bytes', 0), capacity_trial=request.get('capacity_trial', False))
        if request.get('dynamic_gpu_budget') and metrics['device_memory'].get('windows_allocator_limit_enforced'):
            from .gpu_budget import LiveGPUBudget
            reserve = request.get('gpu_reserve_bytes', 0)
            live_budget = LiveGPUBudget(torch, metrics['device_memory'],
                request.get('gpu_capacity_bytes', metrics['device_memory']['device_total_bytes']) - reserve, reserve)

        def refresh_budget(stage, reclaim=None, workspace=None, confirm=False):
            def release(target):
                if reclaim is not None:
                    reclaim(target)
                if resident is not None:
                    for role in tuple(resident.entries):
                        if torch.cuda.memory_allocated() <= target:
                            break
                        if role != 'engine' or stage == 'decode':
                            resident.drop(role, 'Live GPU budget decreased')
            limit = live_budget.refresh(stage, release, workspace=workspace, confirm=confirm)
            if resident is not None:
                resident.gpu_budget = limit
            return limit
        if live_budget is not None:
            # The pass cache grows only into memory no other process holds.
            refresh_budget.device_headroom = live_budget.device_headroom

            def release_idle():
                freed = 0
                if resident is not None:
                    for role in tuple(resident.entries):
                        if role != 'engine':
                            held = torch.cuda.memory_allocated()
                            resident.drop(role, 'Video memory budget was cut during sampling')
                            freed += max(0, held - torch.cuda.memory_allocated())
                return freed
            # Read between layers: a cut inside a step is answered there, not at the next step.
            from types import SimpleNamespace
            refresh_budget.pressure = SimpleNamespace(local=live_budget.local_memory, release=release_idle,
                                                      limit=lambda: live_budget.limit)
        if resident is not None:
            # Idle models stay beside sampling only where the device holds both,
            # with this request's growth reserve left over.
            resident.device_free = live_budget.device_free if live_budget is not None else None
            resident.device_reserve = request.get('gpu_reserve_bytes', 0) if live_budget is not None else 0
            limit = metrics['device_memory'].get('effective_allocator_limit_bytes')
            if limit is not None:
                resident.gpu_budget = min(resident.gpu_budget, limit)
        save(request['metrics'], metrics)
        manifest = json.loads((Path(request['cache']) / 'manifest.json').read_text(encoding='utf-8'))
        if continuation is not None and 'tail' in continuation:
            # Nothing was stored for the previous video: its ending is encoded here, before any model of
            # this run is loaded, and every later attempt reads the same record.
            from .continuation_tail import complete
            continuation, metrics['continuation_encode'] = complete(
                continuation, request['geometry'], manifest, request.get('sampling_plan'), resident=resident)
            request['continuation'] = continuation
        if manifest.get('precision') not in ('fp8', 'int8', 'bf16'):
            raise ValueError('The Engine requires an official FP8 or int8 cache')
        options, decoder = request['engine_options'], request['decoder_options']

        def fit_decoder():
            # The plan sized the decoder's resident blocks to the request's
            # budget. When another program takes GPU memory during sampling,
            # the Windows limit shrinks below them: 29 of 36 blocks against a
            # 9.08 GB limit failed on the first clip, and the retry decoded
            # with none. Keep what the current limit holds, by the planner's
            # rule; resident and streamed blocks decode the same values.
            nonlocal decoder
            limit = metrics['device_memory'].get('effective_allocator_limit_bytes')
            planned = decoder.get('resident_blocks', 0)
            if decoder.get('offload') and planned and limit:
                from .policy import decoder_resident_blocks
                fit = decoder_resident_blocks(limit, canvas,
                                              linear_compute_cache=bool(decoder.get('linear_compute_cache')))
                if fit < planned:
                    decoder = dict(decoder, resident_blocks=fit)
                    metrics.setdefault('decoder_residency', dict(planned_resident_blocks=planned))
                    metrics['decoder_residency'].update(resident_blocks=fit, allocator_limit_bytes=int(limit))
        resume = request.get('resume_decode') is not None
        if not resume and (options.get('task', 't2va') != 't2va' or request.get('geometry', {}).get('early_reference')):
            from .media_conditioning import describe
            canvas = request.get('geometry', {})
            value = torch.load(request['conditioning'], map_location='cpu', weights_only=True)
            # Older official keyframe caches have no explicit task field.
            value.setdefault('task', options['task'])
            actual = describe(value, canvas['width'], canvas['height'], canvas['frames'])
            from .original_audio import identity
            originals = [row for row in value.get('references', []) if row.get('use') == 'original']
            if originals or original_audio is not None:
                if (len(originals) != 1 or original_audio is None
                        or identity(originals[0].get('original_audio', {})) != identity(original_audio)):
                    raise ValueError('Encoded original audio does not match the retained source')
            if actual['task'] != options['task'] or any(canvas.get(name, actual[name]) != actual[name]
                    for name in ('reference_video_tokens', 'reference_audio_tokens')):
                raise ValueError('Encoded media disagrees with the admitted request layout')
            del value
            metrics['conditioning_info'] = actual
        canvas = request.get('geometry', {})
        artifacts = Path(request['artifacts']) if request.get('artifacts') else None
        # A two-pass request that runs as one pass (small canvases) continues
        # too: its first and final canvases are the same one, so one tail
        # serves both stages. An explicit single-pass request keeps nothing.
        # Whatever media conditioned the request, the sampler returns only the
        # generated picture and sound rows, so the tail is the same. A clip used
        # as is continues only as a part of its own chain.
        plan_flags = request.get('sampling_plan') or {}
        continuable = (bool(plan_flags.get('enabled') or plan_flags.get('requested'))
                       and (original_audio is None or 'segment' in original_audio)
                       and not request.get('preview'))
        first_tail = continuation_data = None
        def tail_failure(error):
            from .adaptive import classify_failure
            if (isinstance(error, torch.cuda.OutOfMemoryError)
                    or type(error).__name__ in ('InterruptProcessingException', 'CancelledError')
                    or classify_failure(error)['kind'] in ('gpu_oom', 'cancelled')):
                raise error
            metrics['continuation_store'] = dict(saved=False, error=type(error).__name__)
        def restore_first(value):
            nonlocal first_tail
            first_tail = value

        def restore_tail(value):
            nonlocal continuation_data
            continuation_data = value

        def capture_first(video):
            nonlocal first_tail
            if not continuable:
                return
            try:
                from .continuation import tail
                first_tail = tail(video, **({'hide_tail_frames': hide_tail_frames} if hide_tail_frames else {}))
            except Exception as error:
                tail_failure(error)
                first_tail = None
        if resume:
            from .decode_resume import load_saved_sampling
            metrics.update(phase='decode', decode_phase='latent_load')
            save(request['metrics'], metrics)
            tick = time.perf_counter()
            latents, audio, sampled, base = load_saved_sampling(request, torch,
                **({'continuation_tail_callback': restore_tail} if continuable else {}))
            metrics.update(sampled, phase='decode', decode_phase='latent_transfer')
            if continuation is not None:
                metrics['continuation'] = summary(continuation, hide_tail_frames=hide_tail_frames)
            save(request['metrics'], metrics)
            print(json.dumps(dict(event='decode_resume', sampling_source_attempt=metrics['sampling_source_attempt'])), flush=True)
            fit_decoder()
            if resident is not None:
                resident.decoder_room(decoder, canvas)
            latents, audio = latents.to('cuda'), audio.to('cuda')
            metrics['latent_reload_seconds'] = time.perf_counter() - tick
            metrics['transformer_released_before_decode'] = resident is None or not resident.engine_on_gpu()
        else:
            from .sampling_memory import SamplingMemory as _Counters
            load_read_start = _Counters.process_counters().get('read_bytes')
            tick = time.perf_counter()
            # Windows charges the allocator room still to be reserved to commit;
            # the engine keeps that room out of its locked weights.
            allocation = metrics['device_memory'].get('effective_allocator_limit_bytes') or request['gpu_budget_bytes']
            factory = lambda: Engine(request['cache'], input_cache_dir=request.get('input_cache_dir'),
                                     gpu_allocation_bytes=int(allocation),
                                     **dict(options, canvas=request.get('geometry')))
            # CUDA and the GPU budget are ready; the model itself comes next.
            print(json.dumps({'event': 'model_load_phase', 'phase': 'Loading video model', 'stage': 'engine',
                              'epoch': time.time(), 'seconds': time.perf_counter() - started}), flush=True)
            engine, reused = resident.engine(request, factory) if resident is not None else (factory(), False)
            loaded_seconds = time.perf_counter() - tick
            load_read_end = _Counters.process_counters().get('read_bytes')
            load_read_bytes = (load_read_end - load_read_start) if all(isinstance(v, (int, float)) for v in (load_read_start, load_read_end)) else None
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
            decode_read_ahead = DecoderReadAhead(engine.base, ram_budget_bytes=request.get('ram_budget_bytes'),
                                                 budget_is_estimate=request.get('ram_budget_is_estimate') is True)
            decoder_cached = resident is not None and 'video_vae' in resident.entries
            if decoder_cached:
                decode_read_ahead.result.update(state='skipped', reason='Video VAE is already resident on the GPU')
            def read_decoder_ahead():
                # Every offloaded layer in memory and none read in the last step.
                decode_read_ahead.start(drive_idle=getattr(engine, 'drive_idle', False) is True)
            if hasattr(decode_read_ahead, 'due'):
                read_decoder_ahead.due = decode_read_ahead.due
                if not reused and isinstance(load_read_bytes, (int, float)) and loaded_seconds > 0:
                    decode_read_ahead.load_read_rate = load_read_bytes / loaded_seconds
                    decode_read_ahead.result['load_read_bytes_per_second'] = round(decode_read_ahead.load_read_rate)
            from .sampling_memory import SamplingMemory
            memory_diagnostics = SamplingMemory(torch, Path(request['output']).with_suffix('.sampling-memory.json')).start()
            from . import frame_preview
            if not request.get('capacity_trial', False):
                frame_preview.configure(Path(request['output']).parent)
            def sample_finalize_phase(name):
                metrics.update(phase='sample_finalize', sample_finalize_phase=name)
                save(request['metrics'], metrics)
                print(json.dumps(dict(event='sample_finalize', phase=name)), flush=True)

            def sampling_complete(latents, audio, sampled):
                nonlocal continuation_data
                metrics.update(sampled, sampling_checkpoint_complete=False,
                    sample_metrics_scope='Completed sampling before offloader cleanup; finalization is excluded.')
                metrics['sampling_memory'] = memory_diagnostics.result()
                sample_finalize_phase('latent_validation')
                if not bool(torch.isfinite(latents).all() and torch.isfinite(audio).all()):
                    raise RuntimeError('Non-finite video/audio latents; refusing to save a corrupted result')
                metrics['finite_latents'] = True
                if continuable and not request['sampling_plan'].get('enabled'):
                    capture_first(latents)
                if continuable and first_tail is not None:
                    try:
                        from .continuation import tail_data
                        continuation_data = tail_data(first_tail, latents, audio, canvas,
                            request['sampling_plan'], manifest, continuation,
                            **({'hide_tail_frames': hide_tail_frames} if hide_tail_frames else {}))
                    except Exception as error:
                        tail_failure(error)
                        continuation_data = None
                if artifacts:
                    from .decode_resume import sampling_provenance, save_sampling
                    tick = time.perf_counter()
                    sample_finalize_phase('latent_save')
                    provenance = sampling_provenance(request)
                    save_sampling({'video': latents.cpu(), 'audio': audio.cpu(), 'seed': request['seed'],
                                   'geometry': canvas, 'sampling_provenance': provenance,
                                   **({'continuation_tail': continuation_data} if continuation_data is not None else {})}, artifacts, torch)
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
                    preview = request.get('preview') is True
                    if preview and (request.get('resume_refine') or request.get('resume_first_pass')):
                        raise ValueError('A preview samples its own first pass')
                    if request.get('resume_refine'):
                        # A retry after a second-pass failure: this request's
                        # retained first pass, latent upscale and crop are
                        # reused instead of sampling them again.
                        from .decode_resume import load_refine_input
                        metrics.update(sample_stage='first_pass_load')
                        save(request['metrics'], metrics)
                        tick = time.perf_counter()
                        latents, audio, first, lifted, reused = load_refine_input(request, torch,
                            **({'continuation_tail_callback': restore_first} if continuable else {}))
                        latents, audio = latents.to('cuda'), audio.to('cuda')
                        metrics.update(reused, sampling_passes=[first], latent_upscale=lifted,
                                       first_pass_load_seconds=time.perf_counter() - tick)
                        print(json.dumps(dict(event='first_pass_reused', source_attempt=reused['first_pass_source_attempt'],
                                              completed_steps=sampling_plan['base_steps'],
                                              total=sampling_plan['total_steps'])), flush=True)
                    else:
                        if request.get('resume_first_pass'):
                            # Upscale a finished preview: its retained first pass
                            # replaces sampling it again; upscale and refine follow.
                            from .decode_resume import load_first_pass
                            metrics.update(sample_stage='first_pass_load')
                            save(request['metrics'], metrics)
                            tick = time.perf_counter()
                            latents, audio, first, reused = load_first_pass(request, torch)
                            latents, audio = latents.to('cuda'), audio.to('cuda')
                            metrics.update(reused, first_pass_load_seconds=time.perf_counter() - tick)
                            print(json.dumps(dict(event='first_pass_reused', source='preview',
                                                  completed_steps=sampling_plan['base_steps'],
                                                  total=sampling_plan['total_steps'])), flush=True)
                        else:
                            pass_budget = (min(request['gpu_budget_bytes'],
                                metrics['device_memory'].get('effective_allocator_limit_bytes') or request['gpu_budget_bytes'])
                                if request.get('automatic_pass_cache', False) else None)
                            first_options = request.get('first_pass_policy', {}).get('profile', {}).get('engine')
                            metrics['first_pass_policy'] = request.get('first_pass_policy')
                            first_context = None
                            if continuation is not None:
                                from .continuation import load
                                first_context = load(continuation, 'first')
                            latents, audio, first = engine.sample(request['conditioning'], request['seed'],
                                **({'continuation': first_context} if first_context is not None else {}),
                                **sampling_plan['first'], allow_smaller_canvas=True,
                                pass_cache_budget_bytes=pass_budget, gpu_reserve_bytes=request.get('gpu_reserve_bytes', 0),
                                compute_options=first_options,
                                pass_resident_blocks=first_options.get('resident_blocks') if first_options else None,
                                step_callback=memory_diagnostics.complete,
                                progress_total=sampling_plan['base_steps'] if preview else sampling_plan['total_steps'],
                                budget_refresh=refresh_budget if live_budget is not None else None)
                        metrics['sampling_passes'] = [first]
                        capture_first(latents)
                        if preview:
                            # Keep the first pass before the latent upscale; an
                            # upscale of this preview starts from these latents.
                            if not artifacts:
                                raise ValueError('A preview needs an artifacts folder for its first pass')
                            if not bool(torch.isfinite(latents).all() and torch.isfinite(audio).all()):
                                raise RuntimeError('Non-finite first-pass latents; refusing to save a corrupted preview')
                            from .decode_resume import refine_provenance, save_first_pass
                            tick = time.perf_counter()
                            provenance = refine_provenance(request)
                            save_first_pass({'video': latents.cpu(), 'audio': audio.cpu(), 'seed': request['seed'],
                                             'geometry': canvas, 'refine_provenance': provenance}, artifacts, torch)
                            metrics.update(preview=True, finite_latents=True, first_pass_checkpoint_complete=True,
                                           refine_provenance=provenance, latent_save_seconds=time.perf_counter() - tick)
                        else:
                            metrics['sample_stage'] = 'latent_upscale'
                            save(request['metrics'], metrics)
                            print(json.dumps(dict(event='latent_upscale', completed_steps=sampling_plan['base_steps'],
                                                  total=sampling_plan['total_steps'])), flush=True)
                            from .sampling_progress import elapsed_reports
                            with elapsed_reports(lambda event: print(json.dumps(event), flush=True),
                                                 dict(event='latent_upscale', completed_steps=sampling_plan['base_steps'],
                                                      total=sampling_plan['total_steps'])):
                                tick = time.perf_counter()
                                if live_budget is not None:
                                    refresh_budget('upscale', confirm=True)
                                need = upscale_workspace(sampling_plan['upscale_target']) if sampling_plan['upscaler_sha256'] else 0
                                # Its weights go straight to the GPU. Charging 2 GiB of
                                # host RAM evicted the second pass's own engine on
                                # tight-RAM machines, which then reloaded every block.
                                from .two_pass import UPSCALER_HOST_BYTES
                                if need and resident is not None and not resident.ram_fits(UPSCALER_HOST_BYTES):
                                    resident.make_room('latent_upscaler', need, ram_need=UPSCALER_HOST_BYTES)
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
                                    if classify_failure(error)['kind'] != 'gpu_oom' or (released(engine) and memory_saving):
                                        raise
                                    from .diagnostic_resources import error_details
                                    upscale_failures.append(error_details(error))
                                    retry_upscale = True
                                if retry_upscale:
                                    # Leave the failed call's traceback before retrying so
                                    # its tensors no longer occupy the allocator. Do not
                                    # sample again or change the temporal normalization.
                                    if not released(engine):
                                        if resident is not None:
                                            resident.drop('engine', 'Latent upscale allocation needs additional workspace')
                                        else:
                                            engine.close()
                                    if resident is not None:
                                        resident.make_room('latent_upscaler', need, ram_need=UPSCALER_HOST_BYTES)
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
                                transformer_reloaded=released(engine))
                            metrics['latent_upscale'] = lifted
                            # A parked engine is reacquired too: its GPU weights come back.
                            if released(engine):
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
                                                       'geometry': canvas, 'refine_provenance': provenance,
                                                       **({'continuation_first': first_tail} if first_tail is not None else {})}, artifacts, torch)
                                    metrics.update(refine_checkpoint_complete=True, refine_provenance=provenance,
                                                   refine_source_attempt=request.get('resource_attempt'))
                                except (OSError, RuntimeError, ValueError) as error:
                                    metrics['refine_checkpoint_error'] = str(error)
                    if preview:
                        sampled = dict(first, sampling_plan=sampling_plan, sampling_passes=[dict(first)],
                                       step_seconds=list(first['step_seconds']), sample_seconds=first['sample_seconds'])
                        if not decoder_cached:
                            read_decoder_ahead()
                    else:
                        metrics['sample_stage'] = 'refine'
                        save(request['metrics'], metrics)
                        def refined(receipt):
                            result = combine(first, receipt, lifted, sampling_plan)
                            if request.get('resume_first_pass'):
                                # This request sampled only the refinement; the
                                # reused first pass's seconds belong to its preview.
                                result.update(reused_first_pass_seconds=first['sample_seconds'],
                                              sample_seconds=result['sample_seconds'] - first['sample_seconds'])
                            return result
                        def refined_complete(video, sound, receipt):
                            sampling_complete(video, sound, refined(receipt))
                        restart_seed = (request['seed'] + sampling_plan['restart_seed_offset']) % (1 << 64)
                        final_context = None
                        if continuation is not None:
                            from .continuation import load
                            final_context = load(continuation, 'final')
                        # The first pass/upscaler can leave a lower WDDM cap
                        # than the one that selected the refinement's heads.
                        # Recheck before its first allocation, not after OOM.
                        from .policy import windows_int8_refinement_heads, windows_int8_refinement_head_need
                        guard = dict(windows=metrics['device_memory'].get('windows_allocator_limit_enforced', False)
                                     and not request.get('capacity_trial', False),
                                     lora=bool(manifest.get('online_lora')))
                        head_need = windows_int8_refinement_head_need(engine.config, canvas, **guard)
                        second_compute = None
                        if live_budget is not None:
                            refresh_budget('refine', confirm=True)
                        if head_need is not None:
                            limit = metrics['device_memory'].get('effective_allocator_limit_bytes')
                            second_compute = windows_int8_refinement_heads(engine.config, canvas, limit, **guard)
                            if second_compute:
                                metrics['refinement_head_planning'] = dict(planned_head_chunk=4, head_chunk=2,
                                    allocator_limit_bytes=limit, head4_need_bytes=head_need)
                                save(request['metrics'], metrics)
                        latents, audio, second = engine.sample(request['conditioning'], restart_seed,
                            **({'continuation': final_context} if final_context is not None else {}),
                            **sampling_plan['second'], initial_latents=(latents, audio), refine_steps=sampling_plan['refine_steps'],
                            refine_schedule=sampling_plan.get('refine_schedule'),
                            compute_options=second_compute,
                            final_step_callback=None if decoder_cached else read_decoder_ahead,
                            step_callback=memory_diagnostics.complete, sampling_complete_callback=refined_complete,
                            progress_offset=sampling_plan['base_steps'], progress_total=sampling_plan['total_steps'],
                            gpu_reserve_bytes=request.get('gpu_reserve_bytes', 0),
                            budget_refresh=refresh_budget if live_budget is not None else None)
                        sampled = refined(second)
                        metrics.pop('sample_stage', None)
                else:
                    single_context = None
                    if continuation is not None:
                        from .continuation import load
                        single_context = load(continuation, 'final')
                    latents, audio, sampled = engine.sample(request['conditioning'], request['seed'],
                        **({'continuation': single_context} if single_context is not None else {}),
                        frames=canvas.get('frames', 243), width=canvas.get('width', 1344), height=canvas.get('height', 768),
                        final_step_callback=None if decoder_cached else read_decoder_ahead,
                        step_callback=memory_diagnostics.complete, sampling_complete_callback=sampling_complete,
                        gpu_reserve_bytes=request.get('gpu_reserve_bytes', 0),
                        budget_refresh=refresh_budget if live_budget is not None else None)
            finally:
                frame_preview.clear()
                metrics['sampling_memory'] = memory_diagnostics.close()
            metrics.update(sampled)
            metrics.pop('sample_metrics_scope', None)
            base = engine.base
            sample_finalize_phase('transformer_release')
            # A one-shot worker releases the transformer. An interactive worker
            # keeps it if live memory also covers decoding, otherwise evicts it.
            fit_decoder()
            if resident is not None:
                resident.decoder_room(decoder, canvas)
            else:
                engine.close()
            metrics['transformer_released_before_decode'] = released(engine)
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
            refresh_budget('decode', confirm=True)
            if engine is not None:
                metrics['transformer_released_before_decode'] = released(engine)
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
        fit_decoder()
        output_size = request.get('output_size')
        if output_size is not None:
            output_size = (output_size['width'], output_size['height'])
            metrics['output_size'] = dict(width=output_size[0], height=output_size[1])
        def keep_audio_lead_in(value):
            if value is not None:
                continuation_data['audio_lead_in'] = value
                # Older retained sampling tails predate the predecessor binding.
                continuation_data['metadata']['source_id'] = continuation['source_id']
        metrics.update(decode_to_file(latents, audio, request['output'], base=base, artifacts_dir=artifacts,
                                     **({'hide_tail_frames': hide_tail_frames} if hide_tail_frames else {}),
                                     **({'original_audio': original_audio} if original_audio is not None else {}),
                                     **({'continuation': continuation} if continuation is not None else {}),
                                     **({'audio_lead_in_callback': keep_audio_lead_in}
                                        if continuation is not None and continuation_data is not None and original_audio is None else {}),
                                     model_cache=resident, phase_callback=decode_phase, output_size=output_size,
                                     ram_budget_bytes=request.get('ram_budget_bytes'), **decoder))
        if continuable and continuation_data is not None:
            if continuation is not None:
                # Every continuation of a film carries its first frame on, used or not: a later
                # part may ask for it. Without one this part is saved as before.
                try:
                    from .continuation import chain_first_frame
                    frame = chain_first_frame(continuation, continuation.get('source_video'))
                    if frame is not None:
                        continuation_data['metadata']['first_frame'] = frame
                except Exception as error:
                    if isinstance(error, MemoryError) or type(error).__name__ in (
                            'OutOfMemoryError', 'CancelledError', 'InterruptProcessingException'):
                        raise
                    metrics['first_frame_kept'] = dict(saved=False, error=type(error).__name__)
                try:
                    from .continuation import chain_first_sound
                    sound = chain_first_sound(continuation, continuation.get('source_video'))
                    if sound is not None:
                        continuation_data['metadata']['first_sound'] = sound
                except Exception as error:
                    if isinstance(error, MemoryError) or type(error).__name__ in (
                            'OutOfMemoryError', 'CancelledError', 'InterruptProcessingException'):
                        raise
                    metrics['first_sound_kept'] = dict(saved=False, error=type(error).__name__)
            try:
                from .continuation_store import save as save_continuation
                metrics['continuation_store'] = dict(save_continuation(request['output'], continuation_data), saved=True)
                if continuation is not None and 'audio_lead_in' in continuation_data:
                    from .continuation import LEAD_IN_SAMPLES
                    metrics['continuation']['audio_lead_in_samples'] = LEAD_IN_SAMPLES
            except Exception as error:
                tail_failure(error)
        elif continuable:
            metrics.setdefault('continuation_store', dict(saved=False, reason='checkpoint_missing'))
        if resume and artifacts:
            from .decode_resume import retain_latents
            retain_latents(request['resume_decode']['latents'], artifacts)
        metrics.update(success=True, phase='complete')
        if resident is not None:
            from .resident_models import key
            if not resume:
                from .two_pass_metrics import sampling_workspace_peak
                # A preview's first pass is kept apart from the complete request's peak.
                resident.observe_peak('engine_preview' if request.get('preview') is True else 'engine',
                    resident.engine_key(request), sampling_workspace_peak(sampled) - foreign_sample_bytes)
            resident.observe_peak('decode', key(dict(options=decoder, canvas=canvas)), torch.cuda.max_memory_allocated() - foreign_decode_bytes)
            metrics['resident_models'] = resident.snapshot()
    except BaseException as error:
        original_error = error
        metrics['error'] = repr(error)
        from .adaptive import classify_failure
        metrics['failure'] = classify_failure(error)
        from .runtime_libraries import mismatch, diagnose
        if mismatch(error):
            metrics['failure']['runtime_libraries'] = diagnose()
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
        if resident is not None:
            resident.device_free, resident.device_reserve = None, 0
        if live_budget is not None:
            live_budget.close()
        if resident is not None and metrics['success'] and engine is not None:
            try:
                resident.settle(float(request['engine_options'].get('pin_host_gb') or 0)*1e9)
            except Exception:
                # Trimming is best effort; it must not turn a finished request into a failure.
                pass
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
