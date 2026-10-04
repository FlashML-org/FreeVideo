"""Bounded encoder OOM recovery without changing tokens or model arithmetic."""
import gc
import os
import sys

GiB = 2**30
GPU_FIELDS = ('free_bytes', 'total_bytes', 'allocated_bytes', 'reserved_bytes',
              'peak_allocated_bytes', 'peak_reserved_bytes', 'loaded_weight_bytes',
              'model_weight_bytes', 'extra_reserved_bytes', 'allocator_ooms',
              'allocator_retries', 'inactive_split_bytes', 'active_bytes',
              'requested_bytes', 'memory_fraction', 'pinned_bytes', 'pinned_limit_bytes',
              'readonly_pin_attempts', 'readonly_pin_successes', 'readonly_pin_failures',
              'readonly_pin_skips', 'readonly_pin_last_error_code', 'readonly_pin_flags',
              'readonly_pin_capacity_bytes', 'readonly_pin_memory_skips', 'readonly_pin_memory_query_failures',
              'host_pinned_allocated_bytes', 'host_pinned_active_bytes', 'host_pinned_peak_bytes',
              'local_budget_bytes', 'local_usage_bytes', 'nonlocal_budget_bytes', 'nonlocal_usage_bytes')


def snapshot(torch, clip=None, manager=None, *, windows_memory=False):
    """Independent observations: one failed CUDA query must not hide the rest.

    Native registered model pages and PyTorch's host allocator are separate
    counters. Neither is inferred from free VRAM, RSS, or a pinning budget.
    This reads counters only; it never allocates tensors or synchronizes CUDA.
    """
    from .diagnostic_resources import exception_details
    result = {}
    def failed(section, error):
        details = exception_details(error)[:1]
        for row in details:
            row['frames'] = []  # The named counter identifies this optional query.
        result.setdefault('collection_errors', {}).setdefault(section, details)
    try:
        free, total = torch.cuda.mem_get_info()
        result.update(free_bytes=int(free), total_bytes=int(total))
    except Exception as error:
        failed('cuda_memory', error)
    try:
        stats = torch.cuda.memory_stats()
        for target, source in (
                ('allocated_bytes', 'allocated_bytes.all.current'), ('reserved_bytes', 'reserved_bytes.all.current'),
                ('peak_allocated_bytes', 'allocated_bytes.all.peak'), ('peak_reserved_bytes', 'reserved_bytes.all.peak'),
                ('allocator_ooms', 'num_ooms'), ('allocator_retries', 'num_alloc_retries'),
                ('inactive_split_bytes', 'inactive_split_bytes.all.current'), ('active_bytes', 'active_bytes.all.current'),
                ('requested_bytes', 'requested_bytes.all.current')):
            if type(stats.get(source)) is int and stats[source] >= 0:
                result[target] = stats[source]
    except Exception as error:
        failed('allocator_stats', error)
    try:
        result['memory_fraction'] = float(torch.cuda.get_per_process_memory_fraction())
    except Exception as error:
        failed('memory_fraction', error)
    try:
        stats = torch.cuda.memory.host_memory_stats()
        for target, source in (('host_pinned_allocated_bytes', 'allocated_bytes.current'),
                               ('host_pinned_active_bytes', 'active_bytes.current'),
                               ('host_pinned_peak_bytes', 'allocated_bytes.peak')):
            if type(stats.get(source)) is int and stats[source] >= 0:
                result[target] = stats[source]
    except Exception as error:
        failed('host_pinning', error)
    try:
        if clip is not None:
            result.update(loaded_weight_bytes=int(clip.patcher.loaded_size()),
                          model_weight_bytes=int(clip.patcher.model_size()))
        manager = manager or sys.modules.get('comfy.model_management')
        if manager is not None:
            result['extra_reserved_bytes'] = int(manager.EXTRA_RESERVED_VRAM)
            for target, source in (('pinned_bytes', 'TOTAL_PINNED_MEMORY'), ('pinned_limit_bytes', 'MAX_PINNED_MEMORY'),
                    ('readonly_pin_attempts', 'FREEVIDEO_READONLY_PIN_ATTEMPTS'),
                    ('readonly_pin_successes', 'FREEVIDEO_READONLY_PIN_SUCCESSES'),
                    ('readonly_pin_failures', 'FREEVIDEO_READONLY_PIN_FAILURES'),
                    ('readonly_pin_skips', 'FREEVIDEO_READONLY_PIN_SKIPS'),
                    ('readonly_pin_last_error_code', 'FREEVIDEO_READONLY_PIN_LAST_ERROR_CODE'),
                    ('readonly_pin_flags', 'FREEVIDEO_READONLY_PIN_FLAGS'),
                    ('readonly_pin_capacity_bytes', 'FREEVIDEO_READONLY_PIN_CAPACITY_BYTES'),
                    ('readonly_pin_memory_skips', 'FREEVIDEO_READONLY_PIN_MEMORY_SKIPS'),
                    ('readonly_pin_memory_query_failures', 'FREEVIDEO_READONLY_PIN_MEMORY_QUERY_FAILURES')):
                value = getattr(manager, source, None)
                if type(value) in (int, float):
                    result[target] = max(0, int(value))
    except Exception as error:
        failed('native_placement', error)
    if windows_memory and sys.platform == 'win32':
        reader = None
        try:
            from .windows_gpu_memory import AdapterMemory
            reader = AdapterMemory()
            observed = reader.sample()
            for segment in ('local', 'nonlocal'):
                for field in ('budget_bytes', 'usage_bytes'):
                    value = observed.get(segment, {}).get(field)
                    if type(value) is int and value >= 0:
                        result[segment + '_' + field] = value
        except Exception as error:
            failed('windows_memory', error)
        finally:
            if reader is not None:
                try:
                    reader.close()
                except Exception as error:
                    failed('windows_memory', error)
    return result


def failure_resources(torch, *, query_cuda=True):
    """Capture the failure endpoint before model cleanup releases its memory."""
    result = {'gpu': snapshot(torch, windows_memory=True) if query_cuda else {}}
    try:
        from .ram import ProcessMemory
        result['memory'] = ProcessMemory().sample(os.getpid())
    except Exception as error:
        from .diagnostic_resources import exception_details
        result['memory_collection_error'] = exception_details(error)
    return result


def token_counts(tokens):
    """The native H3 token container's lengths, never token IDs or content."""
    if not isinstance(tokens, dict):
        return {}
    sequences = tokens.get('qwen3vl_32b')
    if not isinstance(sequences, (list, tuple)) or not all(isinstance(row, (list, tuple)) for row in sequences):
        return {}
    lengths = [len(row) for row in sequences]
    return dict(sequences=len(lengths), max_sequence_tokens=max(lengths, default=0), total_tokens=sum(lengths))


def release_cast_buffers(manager, torch):
    """Free the streaming buffers the native library keeps after an encode.

    Without dynamic VRAM, which this worker disables, ComfyUI copies every
    weight it did not load through one cached buffer per offload stream, sized
    to the largest weight so far. Unloading models never frees them, and in
    this mode ComfyUI itself never does: its executor resets them only for
    dynamic VRAM. The resident worker runs the transformer next in the same
    process. On a 6 GiB RTX 3060 Laptop, with no encoder weights loaded, they
    held 0.80 GiB allocated and 1.68 GiB reserved, and evicting the encoder
    released nothing. The next encode allocates them again.
    """
    reset = getattr(manager, 'reset_cast_buffers', None)
    if not callable(reset):
        return None
    allocated, reserved = torch.cuda.memory_allocated(), torch.cuda.memory_reserved()
    reset()  # The native reset also empties the CUDA cache.
    return dict(released_allocated_bytes=max(0, allocated - torch.cuda.memory_allocated()),
                released_reserved_bytes=max(0, reserved - torch.cuda.memory_reserved()))


def encode_with_recovery(clip, tokens, manager, torch, phase, *, max_attempts=3):
    from .adaptive import classify_failure
    original_reserve = manager.EXTRA_RESERVED_VRAM
    attempts = []
    try:
        for index in range(max_attempts):
            try:
                return clip.encode_from_tokens_scheduled(tokens), attempts
            except Exception as error:
                failure = classify_failure(error)
                native_is_oom = getattr(manager, 'is_oom', None)
                runtime_code = getattr(error, 'error_code', None)
                runtime_oom = type(runtime_code) is int and runtime_code == 2
                if failure['kind'] == 'cuda_error' or (type(runtime_code) is int and runtime_code not in (0, 2)):
                    raise
                if failure['kind'] != 'gpu_oom' and not (runtime_oom and callable(native_is_oom)):
                    raise
                # Capture before native is_oom: AcceleratorError handling can
                # allocate/synchronize to clear the CUDA runtime error state.
                failed = snapshot(torch, clip, manager, windows_memory=True)
                failed.update(attempt=index + 1, kind='gpu_oom', exception=failure['exception'])
                attempts.append(failed)
                phase('encoder_oom', gpu=failed, encoder_attempts=list(attempts))
                if index + 1 == max_attempts:
                    raise
                retry_error = error
            # Retain the original exception for cleanup failures, but release
            # completed forward frames after their metadata has been recorded.
            tb = retry_error.__traceback__
            while tb is not None:
                try:
                    tb.tb_frame.clear()
                except RuntimeError:
                    pass  # The recovery function itself is still executing.
                else:
                    # Diagnostics materialized f_locals. On Python 3.9/3.12,
                    # frame.clear() alone leaves that cached dict holding tensors.
                    tb.tb_frame.f_locals.clear()
                tb = tb.tb_next
            try:
                gc.collect()
                native_oom = native_is_oom(retry_error) if callable(native_is_oom) else False
            except Exception as cleanup:
                retry_error.cleanup_errors = [repr(cleanup)]
                raise retry_error from cleanup
            if failure['kind'] != 'gpu_oom' and not native_oom:
                raise retry_error
            try:
                manager.unload_all_models()
                manager.soft_empty_cache()
            except Exception as cleanup:
                retry_error.cleanup_errors = [repr(cleanup)]
                raise retry_error from cleanup
            del retry_error
            manager.EXTRA_RESERVED_VRAM = original_reserve + (index + 1) * GiB
            phase('encoder_retry', gpu=snapshot(torch, clip, manager), encoder_attempts=list(attempts))
    finally:
        manager.EXTRA_RESERVED_VRAM = original_reserve
