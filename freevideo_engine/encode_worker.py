"""Run the native H3 encoder library in the isolated compute environment.

Standalone encoding exits before video; an interactive worker may retain a
compatible encoder while live resources permit. Qwen weights and conditioning are
shared with the pinned ComfyUI model library, without deploying its application.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from .monitoring import save
from .processes import worker_signals


def encoder_on_gpu(clip):
    patcher = clip.patcher
    return (getattr(patcher.current_loaded_device(), 'type', None) == 'cuda' and
            patcher.loaded_size() >= patcher.model_size() > 0)


def preload(clip, request, resident):
    """Only weight placement; independent of the next prompt or input media."""
    import torch
    import comfy.model_management as memory
    from .idle_encoder import PreloadCancelled, interruptible_load
    from .resident_models import gpu_bytes, GiB
    started = time.perf_counter()
    cancel = Path(request['cancel'])
    before = gpu_bytes(clip)
    result = dict(success=True, state='loading', gpu_bytes_before=before,
                  scope='GPU weights only; no prompt encoding or inference',
                  gpu_budget_bytes=resident.gpu_budget,
                  resource_decision=request.get('idle_resource_decision'))
    updated = [started]
    def check():
        if cancel.exists():
            raise PreloadCancelled('New foreground work takes priority')
        if resident.available() < 256*1024**2 or not resident.ram_fits(256*1024**2):
            raise MemoryError('Live memory pressure; stop speculative placement')
        now = time.perf_counter()
        if now - updated[0] >= .5:
            result.update(gpu_bytes_after=gpu_bytes(clip), elapsed_seconds=now-started)
            save(request['metrics'], result)
            updated[0] = now
    try:
        if encoder_on_gpu(clip):
            result.update(state='ready', reason='Encoder already fully resident')
        elif resident.available() < 2*GiB:
            result.update(state='skipped', reason='Keep useful resident models; insufficient spare GPU workspace')
        else:
            check()
            save(request['metrics'], result)
            _, total = torch.cuda.mem_get_info()
            # The native planner already honors the live budget; the allocator
            # additionally bounds an unexpected speculative allocation.
            torch.cuda.set_per_process_memory_fraction(min(1., resident.gpu_budget/total))
            try:
                with interruptible_load(clip.patcher, check):
                    memory.load_models_gpu([clip.patcher], memory_required=0)
                    torch.cuda.synchronize()
            finally:
                torch.cuda.set_per_process_memory_fraction(1.)
            result.update(state='ready' if encoder_on_gpu(clip) else 'partial',
                          reason='Prepared weights in spare VRAM without encoding a prompt')
    except (PreloadCancelled, MemoryError, torch.OutOfMemoryError) as error:
        resident.drop('encoder', 'Speculative preload interrupted; foreground work wins')
        result.update(state='cancelled' if isinstance(error, PreloadCancelled) else 'released', reason=str(error))
    result.update(gpu_bytes_after=gpu_bytes(clip), elapsed_seconds=time.perf_counter()-started,
                  torch_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                  torch_peak_reserved_bytes=torch.cuda.max_memory_reserved())
    save(request['metrics'], result)
    print(json.dumps(dict(event='encoder_prewarm_complete', **result)), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path)
    parser.add_argument('--comfy-root', type=Path)
    parser.add_argument('--check-library', action='store_true', help='Import the native encoder without UI or model weights')
    args = parser.parse_args()
    if not args.check_library and not args.request:
        parser.error('--request is required for encoding')
    request = json.loads(args.request.read_text(encoding='utf-8')) if args.request else {}
    started = time.perf_counter()
    if request.get('metrics'):
        save(request['metrics'], {'success': False, 'phase': 'encoder_import'})
    try:
        with worker_signals():
            encode(args, request)
    except BaseException as error:
        if request.get('metrics'):
            record = json.loads(Path(request['metrics']).read_text(encoding='utf-8'))
            record.update(success=False, error=repr(error), work_seconds=time.perf_counter() - started)
            from .adaptive import classify_failure
            record['failure'] = classify_failure(error)
            save(request['metrics'], record)
            if 'torch' in sys.modules and sys.modules['torch'].cuda.is_initialized():
                from .encoder_memory import failure_resources
                record['failure'].update(failure_resources(sys.modules['torch'],
                    query_cuda=record['failure']['kind'] != 'cuda_error'))
                record['gpu'] = record['failure']['gpu']
                for target, source in (('torch_peak_allocated_bytes', 'peak_allocated_bytes'),
                                       ('torch_peak_reserved_bytes', 'peak_reserved_bytes')):
                    if source in record['gpu']:
                        record[target] = record['gpu'][source]
            save(request['metrics'], record)
        raise


def encode(args, request, resident=None, diagnostics=None):
    from .encoder_diagnostics import EncoderTrace
    trace = diagnostics or EncoderTrace(request, resident=resident is not None)
    try:
        metrics = _encode(args, request, resident, trace)
    except BaseException:
        trace.finish(False)
        raise
    result = trace.finish(True, metrics)
    if not args.check_library and not request.get('idle_preload'):
        print(json.dumps({'event': 'encoder_complete', **result}), flush=True)
    return result


def _encode(args, request, resident, trace):
    loading = trace.data
    def phase(name, **metrics):
        trace.stage(name, **metrics)
        if name in ('encoder_oom', 'encoder_retry'):
            print(json.dumps(dict(event=name, attempt=len(loading.get('encoder_attempts', [])))), flush=True)
        if name == 'encoder_oom' and request.get('metrics') and request.get('diagnostic_output'):
            from .support_report import write_encoder_retry
            write_encoder_retry(request['diagnostic_output'], loading,
                                index=len(loading.get('encoder_attempts', [])))

    phase('encoder_torch_import')
    from .paths import comfy_root
    args.comfy_root = args.comfy_root or Path(request.get('comfy_root') or comfy_root())
    idle = request.get('idle_preload') is True
    if idle and resident is None:
        raise ValueError('GPU preloading requires the managed resident worker')
    prompt = request.get('prompt', 'library check')
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('A nonempty prompt is required')
    started = time.perf_counter()
    import torch
    torch.set_num_threads(8)
    torch.set_grad_enabled(False)
    phase('encoder_cuda_setup')
    if idle:
        from .idle_encoder import gpu_preload_budget
        free, total = torch.cuda.mem_get_info()
        decision = gpu_preload_budget(resident.gpu_budget, request.get('idle_resources'),
            free=free, total=total, reserved=torch.cuda.memory_reserved())
        request['idle_resource_decision'] = decision
        resident.configure(decision['budget_bytes'], resident.ram_budget,
                           gpu_reserve=decision['system_reserve_bytes'])
        request['gpu_budget_gb'] = decision['budget_bytes'] / 1e9
        if decision['budget_bytes'] < 4e9 or Path(request['cancel']).exists():
            return dict(state='skipped', resource_decision=decision,
                        reason='Foreground work or insufficient live memory for idle preloading')
    phase('encoder_options')
    sys.path.insert(0, str(args.comfy_root.resolve()))
    import comfy.options
    comfy.options.enable_args_parsing()
    # The child has no UI cache, custom-node initialization or HTTP server.
    sys.argv = [str(args.comfy_root / 'main.py'), '--cache-none', '--disable-all-custom-nodes',
                '--disable-dynamic-vram', '--disable-comfy-compiler', '--disable-cuda-graphs']
    budget = request.get('gpu_budget_gb')
    reserve_gib = None
    phase('encoder_cuda_setup')
    if budget is not None:
        budget = float(budget)
        if budget < 4:
            raise ValueError('GPU budget must be at least 4 decimal GB')
        available, total = torch.cuda.mem_get_info()
        # A budget above the device's capacity is the automatic policy on a
        # large card, not a bad request: an H200's own plan is about 145 GB and
        # a fixed 128 GB ceiling refused the request before encoding started.
        # Clamp to the capacity the reserve is computed against.
        budget = min(budget, total / 1e9)
        # Let the native model manager leave space for CUDA and live activations.
        # This is placement guidance, not an enforced capacity result.
        reserve_gib = max(0.5, (available - budget * 1e9 + 1e9) / 2**30)
        sys.argv += ['--reserve-vram', str(reserve_gib)]
    phase('encoder_path_config')
    import folder_paths
    from utils.extra_config import load_extra_path_config
    if request.get('model_paths'):
        load_extra_path_config(request['model_paths'])
    phase('encoder_native_import')
    import comfy.sd
    if args.check_library:
        forbidden = {'nodes', 'server', 'execution'} & set(sys.modules)
        if forbidden:
            raise RuntimeError('Unexpected application modules: ' + repr(forbidden))
        print(json.dumps({'native_encoder_library_import': True, 'application_modules_loaded': [],
                          'torch': str(torch.__version__), 'source': str(args.comfy_root)}), flush=True)
        return
    phase('encoder_media_prepare')
    keyframes = request.get('keyframes')
    images = None
    normalized = None
    media_kwargs = {}
    task = 't2va'
    if request.get('media'):
        from .media_request import task_for
        from .media_encoding import prepare
        task = task_for(request['media'])
        if task != 't2va':
            normalized, media_kwargs = prepare(request['media'], request['geometry'], Path(request['output']).parent / 'media')
            # Saved with the conditioning, so a reused input cache still reports them.
            loading['reference_trims'] = [row['trimmed'] for row in normalized if row.get('trimmed')]
    if keyframes is not None:
        if not isinstance(keyframes, dict) or set(keyframes) != {'first', 'last'}:
            raise ValueError('FL2VA encoding requires keyframes.first and keyframes.last')
        if not request.get('base'):
            raise ValueError('FL2VA encoding requires the H3 base for VAE conditioning')
        from PIL import Image
        from .paths import vdn_root
        sys.path.insert(0, str(vdn_root()))
        from src.inference.encode_keyframes import put_on_canvas
        with Image.open(keyframes['first']) as first, Image.open(keyframes['last']) as last:
            images, height, width = put_on_canvas([first, last])
    from freevideo_engine.conditioning import to_cache
    phase('encoder_lookup')
    name = request.get('encoder', 'qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors')
    path = folder_paths.get_full_path_or_raise('text_encoders', name)
    loading['checkpoint'] = dict(state='read_only_mmap' if sys.platform == 'win32' else 'native_mmap',
                                 bytes=Path(path).stat().st_size)
    print(json.dumps({'event': 'encoder_cache_lookup', 'encoder': name}), flush=True)
    phase('encoder_load')
    # Same native loader and model type as CLIPLoader; CPU initialization prevents
    # eager full-GPU loading before the native memory planner runs.
    clip = None
    if resident is not None:
        from .resident_models import file_identity, key
        encoder_key = key(dict(file=file_identity(path), library=str(args.comfy_root.resolve()), type='MINIMAX'))
        clip = resident.take('encoder', encoder_key)
        estimated = Path(path).stat().st_size * 1.4 + 2*2**30
        if not idle and clip is None:
            resident.make_room('encoder', min(resident.gpu_budget, estimated), model=clip, ram_need=2*2**30)
        elif idle and (Path(request['cancel']).exists() or not resident.ram_fits() or resident.available() < 2*2**30):
            return dict(state='skipped', reason='No spare live memory, or a foreground request arrived')
        import comfy.model_management
        real_free = torch.cuda.mem_get_info()[0] + torch.cuda.memory_reserved() - torch.cuda.memory_allocated()
        reserve_gib = max(.5, (real_free - resident.available() + 1e9) / 2**30)
        comfy.model_management.EXTRA_RESERVED_VRAM = reserve_gib * 2**30
    encoder_cache_hit = clip is not None
    loading['resident_encoder_cache_hit'] = encoder_cache_hit
    if clip is None:
        from .encoder_checkpoint import load_clip
        print(json.dumps({'event': 'encoder_load_start', 'encoder': name}), flush=True)
        clip = load_clip(ckpt_paths=[path], embedding_directory=folder_paths.get_folder_paths('embeddings'),
                                 clip_type=comfy.sd.CLIPType.MINIMAX,
                                 model_options={'initial_device': torch.device('cpu')}, progress=phase)
    else:
        loading['checkpoint']['state'] = 'resident_reuse'
        print(json.dumps({'event': 'encoder_cache_hit', 'encoder': name, 'gpu_ready': encoder_on_gpu(clip)}), flush=True)
    if resident is not None:
        resident.put('encoder', encoder_key, clip)
    if idle:
        from .encoder_pinning import readonly_pinning
        import comfy.model_management as memory
        phase('encoder_idle_preload')
        with readonly_pinning(torch, memory):
            return preload(clip, request, resident)
    gpu_ready_at_start = encoder_on_gpu(clip)
    loading['resident_encoder_gpu_ready_at_start'] = gpu_ready_at_start
    load_seconds = time.perf_counter() - started
    phase('encoder_tokenize', load_seconds=load_seconds)
    # Call the native encoder library directly. Importing nodes would load the
    # ComfyUI application/API graph, which independent Engine requests do not use.
    encode_started = time.perf_counter()
    vision_kwargs = dict(media_kwargs)
    if images is not None:
        import numpy as np
        vision_kwargs['images'] = [torch.from_numpy(np.array(image)).float().div_(255)[None] for image in images]
    print(json.dumps({'event': 'encoder_tokenize_start'}), flush=True)
    tokens = clip.tokenize(prompt, **vision_kwargs)
    from .encoder_memory import token_counts
    loading['token_summary'] = token_counts(tokens)
    tokenize_seconds = time.perf_counter() - encode_started
    from contextlib import contextmanager, nullcontext
    from .encoder_workspace import (LOW_MEMORY_CHUNK, WHOLE_BLOCK_TOKENS, SharedMemorySpill, SpillGuard, Workspace, adapter_reader,
                                    dedicated_room, input_size, make_room, plan)
    size = loading['encoder_input'] = input_size(tokens)
    workspace = Workspace.for_encoder(torch, path)
    reader = adapter_reader()
    guard = SpillGuard(torch, reader)
    current = {}
    # What this process may hold under FreeVideo's plan, beside the physical room.
    budget_bytes = int(float(request['gpu_budget_gb']) * 1e9) if request.get('gpu_budget_gb') else None

    if resident is not None and encoder_cache_hit:
        resident.encoder_room(clip, tokens, estimated)
        loading['resident_admission'] = list(resident.decisions)
        real_free = torch.cuda.mem_get_info()[0] + torch.cuda.memory_reserved() - torch.cuda.memory_allocated()
        comfy.model_management.EXTRA_RESERVED_VRAM = max(.5*2**30, real_free - resident.available() + 1e9)
    # Time the loader the native encode path itself invokes. Wrapping it keeps
    # call order, token-dependent memory planning and arithmetic unchanged.
    native_load = clip.load_model
    transfer_seconds = [0.]
    checkpoint_paths = [path]
    def timed_load(*values, **kwargs):
        phase('encoder_device_load', load_seconds=load_seconds)
        print(json.dumps({'event': 'encoder_device_reuse' if encoder_on_gpu(clip) else 'encoder_device_load_start'}), flush=True)
        tick = time.perf_counter()
        import comfy.model_management as memory
        if current.get('need') is not None:
            # The native planner keeps 0.8 GiB beside the reserve; leave this attempt's room.
            memory.EXTRA_RESERVED_VRAM = max(memory.EXTRA_RESERVED_VRAM, current['need'] - int(.8 * 2**30))
        result = native_load(*values, **kwargs)
        torch.cuda.synchronize()
        transfer_seconds[0] += time.perf_counter() - tick
        phase('encoder_page_release', device_load_seconds=transfer_seconds[0])
        # The checkpoint's pages are dead weight once its weights are on the
        # device, and they are the encoder's whole host footprint. Measured on
        # an H200 at three GPU budgets, the split of the encoder's own resident
        # memory after this load:
        #
        #   budget  6.45 GiB   7.82 GiB total = 5.88 mapped + 1.94 anonymous
        #   budget  9.50 GiB  11.06 GiB total = 8.51 mapped + 2.54 anonymous
        #   budget   120 GiB  17.85 GiB total = 15.00 mapped + 2.84 anonymous
        #
        # So the model's real hold is under 3 GiB and the rest is clean file
        # cache the transfer walked through. A guard that credits clean pages
        # sees past it, but a hard cgroup or commit limit does not wait for
        # reclaim: a 10 GiB limit killed the encoder at 9.84 GiB with zero
        # disk reads recorded. Advise those pages away, as the transformer's
        # streamed reader already does for its own weights. Anything still
        # mapped and needed refaults from an immutable read-only file; that
        # costs a read, where keeping it costs the request.
        #
        # It has to be madvise on the mapping, not fadvise on the file:
        # fadvise only drops pages no process maps, and a tensor maps these
        # for as long as it exists. Advising the file moved the resident total
        # by 0.00 GiB at all three budgets. Advising the mapping moved it from
        # 7.82 to 2.33 GiB at the 6.45 GiB budget, 6.10 to 3.46 at 9.50, and
        # 9.44 to 2.91 at 120 -- in every case down to the model's real hold
        # plus 0.39 GiB of mapping.
        from .encoder_checkpoint import release_mapped_pages
        released = release_mapped_pages(checkpoint_paths)
        if released:
            print(json.dumps(dict(event='encoder_pages_released', **released)), flush=True)
        if current.get('need') is not None:
            # cudaMemGetInfo can promise room the WDDM budget does not have; check what is
            # really free and move weights back to host memory until the forward fits.
            room = make_room(clip.patcher, torch, current['need'], reader, budget_bytes)
            loading.setdefault('encoder_workspace', []).append(dict(current['plan'], mode=current['mode'], **room))
        guard.arm()
        from .encoder_memory import snapshot
        phase('encoder_compute', load_seconds=load_seconds, device_load_seconds=transfer_seconds[0],
              gpu=snapshot(torch, clip, memory))
        print(json.dumps({'event': 'encoder_compute_start'}), flush=True)
        return result
    @contextmanager
    def attempt(index, failure):
        # Room is planned against what this device can give the encoder with none of its weights loaded.
        capacity = dedicated_room(torch, reader, budget_bytes) + int(clip.patcher.loaded_size())
        mode, need, details = plan(workspace, size, capacity, index, current.get('mode') if failure else None)
        current.update(mode=mode, need=need, plan=details)
        from .encoder_lowmem import low_memory
        if mode == 'blocks':
            phase('encoder_low_memory', **details)
            context = low_memory(clip.cond_stage_model, LOW_MEMORY_CHUNK)
        elif size['sequence'] >= WHOLE_BLOCK_TOKENS:
            # One block: the whole sequence at once, without the native dense T x T mask.
            context = low_memory(clip.cond_stage_model, 1 << 30)
        else:
            context = nullcontext()
        from .encoder_precision import bf16_language_model
        try:
            # BF16, as the official pipeline runs this encoder (ComfyUI's base runs it in FP32).
            with context, bf16_language_model(clip.cond_stage_model):
                yield
            workspace.learn(mode, size, guard.used(), spilled=guard.spill is not None)
        except SharedMemorySpill as error:
            workspace.learn(mode, size, error.used_bytes, spilled=True)
            raise
        finally:
            guard.disarm()

    clip.load_model = timed_load
    try:
        from .encoder_memory import encode_with_recovery
        from .encoder_pinning import readonly_pinning
        import comfy.model_management as memory
        with readonly_pinning(torch, memory):
            encoded, attempts = encode_with_recovery(clip, tokens, memory, torch, phase, max_attempts=4,
                                                     attempt=attempt)
        loading['encoder_attempts'] = attempts
        # Finish async forward work at the existing conditioning boundary, so
        # it is not attributed to packing/saving on the host.
        torch.cuda.synchronize()
        from .encoder_memory import release_cast_buffers, snapshot
        loading['cast_buffers'] = release_cast_buffers(memory, torch)
        phase('encoder_conditioning_pack', gpu=snapshot(torch, clip, memory))
    finally:
        del clip.load_model
        guard.disarm()
        if reader is not None:
            reader.close()
    del timed_load, native_load, attempt
    del tokens
    task = 'fl2va' if images is not None else task
    value = to_cache(encoded, prompt, task=task)
    value['task'] = task
    torch.cuda.synchronize()
    prompt_encode_seconds = time.perf_counter() - encode_started
    keyframe_metrics = {}
    if images is not None:
        phase('keyframe_vae')
        import gc
        import comfy.model_management
        del clip, encoded, vision_kwargs
        if resident is None:
            comfy.model_management.unload_all_models()
        gc.collect()
        comfy.model_management.soft_empty_cache()
        from diffusers.modular_pipelines.minimax_h3.encoders import encode_vae_condition
        from .vae_weights import load_video_encoder
        from src.inference.render import PIXEL_MEAN, PIXEL_STD
        vae_started = time.perf_counter()
        if request.get('metrics'):
            save(request['metrics'], {**loading, 'success': False, 'phase': 'keyframe_vae',
                                     'load_seconds': load_seconds, 'prompt_encode_seconds': prompt_encode_seconds})
        # Only the encoder is read; the decoder in the shared shards is skipped.
        vae, _ = load_video_encoder(request['base'], before_upload=None if resident is None else
                                    lambda model: resident.input_vae_room(model, dict(width=width, height=height)))
        torch.cuda.synchronize()
        keyframe_metrics['keyframe_vae_load_seconds'] = time.perf_counter() - vae_started
        vae_started = time.perf_counter()
        with torch.no_grad():
            conditions = [encode_vae_condition(vae,
                torch.from_numpy(np.array(image)).to('cuda').permute(2, 0, 1)[None, :, None],
                PIXEL_MEAN, PIXEL_STD, 42).cpu() for image in images]
        torch.cuda.synchronize()
        keyframe_metrics['keyframe_vae_encode_seconds'] = time.perf_counter() - vae_started
        value.update(keyframe_anchors=['first', 'last'], condition_latents=conditions,
                     keyframe_files=[keyframes['first'], keyframes['last']], width=width, height=height)
        from .keyframes import validate_conditioning
        validate_conditioning(value['prompt_embeds'], value['text_token_tags'],
                              (value['keyframe_anchors'], conditions), 'fl2va', width, height)
    if normalized is not None:
        phase('media_vae')
        import gc
        import comfy.model_management
        del clip, encoded, vision_kwargs, media_kwargs
        if resident is None:
            comfy.model_management.unload_all_models()
        gc.collect()
        comfy.model_management.soft_empty_cache()
        from .paths import add_vdn
        add_vdn()
        from .media_encoding import encode_latents
        tick = time.perf_counter()
        info = encode_latents(normalized, value, request['base'], request['geometry'], resident=resident)
        keyframe_metrics.update(media_vae_encode_seconds=time.perf_counter() - tick, conditioning_info=info)
        task = info['task']
    phase('encoder_save')
    output = Path(request['output'])
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.partial')
    torch.save(value, temporary)
    temporary.replace(output)
    metrics = {'scope': 'Native H3 encoder library in an isolated worker; no UI/server',
               **loading,
               'resident_encoder_cache_hit': encoder_cache_hit,
               'resident_encoder_gpu_ready_at_start': gpu_ready_at_start,
               'encoder': str(Path(path).resolve()), 'precision': 'native shared NVFP4 checkpoint',
               'initial_device': 'cpu', 'reserve_vram_gib': reserve_gib,
               'load_seconds': load_seconds, 'work_seconds': time.perf_counter() - started,
               'prompt_encode_seconds': prompt_encode_seconds, **keyframe_metrics,
               'tokenize_seconds': tokenize_seconds,
               'device_load_seconds': transfer_seconds[0],
               'encoder_compute_seconds': max(0., prompt_encode_seconds - tokenize_seconds - transfer_seconds[0]),
               'task': task,
               'conditioning_shape': list(value['prompt_embeds'].shape), 'output': str(output),
               'torch_peak_allocated_bytes': torch.cuda.max_memory_allocated(),
               'torch_peak_reserved_bytes': torch.cuda.max_memory_reserved(), 'success': True}
    return metrics


if __name__ == '__main__':
    main()
