"""Bounded diagnostic evidence without tensor imports, paths or exception text."""
import math
import re
import sys


RAM_PEAKS = ('process_tree_peak_guard_bytes', 'process_tree_peak_rss_bytes', 'process_tree_peak_pss_bytes',
    'process_tree_peak_private_commit_bytes', 'process_tree_peak_private_working_set_bytes',
    'system_min_available_bytes', 'system_min_physical_available_bytes', 'system_min_commit_available_bytes',
    'effective_min_available_bytes', 'inference_min_available_bytes', 'inference_peak_working_pss_bytes', 'ram_observation_samples', 'process_tree_disk_read_bytes',
    'process_tree_disk_write_bytes')
RAM_SAMPLE = ('rss_bytes', 'pss_bytes', 'nonfile_pss_bytes', 'inference_guard_bytes', 'inference_available_bytes', 'private_commit_bytes', 'private_working_set_bytes', 'guard_bytes',
    'reclaimable_mapped_bytes', 'effective_available_bytes', 'system_available_bytes',
    'system_physical_available_bytes', 'system_commit_available_bytes', 'processes')
GUARD = ('working_bytes', 'budget_bytes', 'available_bytes', 'emergency_floor_bytes',
         'physical_available_bytes', 'commit_available_bytes')
HARDWARE_MEMORY = ('ram_total', 'ram_available', 'vram_total', 'vram_free', 'cgroup_ram_limit')
ENGINE_KNOBS = ('resident_blocks', 'pin_host_gb', 'head_chunk', 'window_batch', 'head_parallelism',
    'ff_chunk', 'projection_chunk', 'steps', 'prefetch', 'stream_weights', 'attention_cpu_outputs',
    'grouped_attention_outputs', 'residual_offload', 'fp8_ff_recompute', 'query_chunk', 'fp8_linears',
    'resident_weight_bytes', 'pinned_model_bytes', 'pinned_host_allocated_bytes', 'inference_kernels',
    'window_varlen', 'varlen_smooth_k', 'reuse_block_outputs', 'preload_host', 'pin_host_weights',
    'cache_refined_text')
ATTENTION_BACKENDS = frozenset(
    'global_cudnn global_torch-flash global_sage2 global_fa2 global_fa4 '
    'window_cudnn window_torch-flash window_sage2 window_fa2 window_fa4 '
    'window_fa2_varlen window_fa4_varlen window_sage2_varlen'.split())
DECODER_KNOBS = ('resident_blocks', 'offload', 'prefetch', 'stream_weights', 'stream_output',
                 'pin_weights', 'preload', 'tile_group', 'linear_compute_cache')
DECODE_PHASES = ('decoder_admission', 'vae_load', 'video_decode', 'video_postprocess', 'rgb_save',
                 'audio_load_decode', 'audio_save', 'mp4_save', 'latent_load', 'latent_transfer')
SAMPLE_FINALIZE_PHASES = ('latent_validation', 'latent_save', 'offload_release', 'transformer_release')
HOST_SAMPLE = ('pinned_allocated_bytes', 'pinned_active_bytes', 'pinned_cached_bytes',
    'physical_available_bytes', 'commit_available_bytes', 'torch_threads', 'torch_interop_threads')
PROCESS_DELTA = ('cpu_user_seconds', 'cpu_system_seconds', 'read_bytes', 'write_bytes')
WEIGHT_PLACEMENT = ('transfers', 'h2d_bytes', 'host_stage_seconds', 'h2d_seconds',
    'host_buffer_wait_seconds', 'prefetch_wait_seconds', 'pinned_model_bytes', 'pinned_host_allocated_bytes',
    'pinned_layer_count', 'streamed_layer_count', 'pinned_buffer_bytes', 'pageable_buffer_bytes', 'cuda_buffer_bytes',
    'direct_read_layers', 'direct_read_bytes',
    'host_prefetch_reads', 'host_prefetch_wait_seconds', 'host_prefetch_buffer_bytes',
    'pass_cache_budget_bytes', 'pass_cache_bytes', 'pass_cache_hits', 'pass_cache_layer_count')
ERROR_CATEGORIES = ('cuda_out_of_memory', 'torch_allocator_out_of_memory',
                    'host_memory_allocation', 'cuda_error', 'checkpoint_changed', 'other')
ENCODER_DTYPES = ('uint8', 'int8', 'int16', 'int32', 'int64', 'float16', 'bfloat16',
                  'float32', 'float64', 'float8_e4m3fn', 'float8_e5m2')
TENSOR_VARIABLES = ('input', 'output', 'x', 'q', 'scale', 'indices', 'qdata', 'weight', 'mat1', 'mat2')


def error_details(error):
    """Extract fixed classifications and numbers, never the exception message.

    CUDA runtime allocation errors and PyTorch allocator OOMs are distinct:
    only the latter normally include the failed allocation's size.
    """
    result = numbers({'error_code': getattr(error, 'error_code', None)}, ('error_code',))
    message = str(error)
    if 'CUDA error: out of memory' in message or 'cudaErrorMemoryAllocation' in message:
        result.update(category='cuda_out_of_memory', error_code=2)
    elif 'CUDA out of memory' in message:
        result['category'] = 'torch_allocator_out_of_memory'
    elif isinstance(error, MemoryError) or getattr(error, 'winerror', None) == 1455:
        result['category'] = 'host_memory_allocation'
    elif 'CUDA error:' in message:
        result['category'] = 'cuda_error'
    elif message.startswith('Checkpoint changed during streamed inference:'):
        result['category'] = 'checkpoint_changed'
        result['checkpoint_change'] = checkpoint_change(getattr(error, 'checkpoint_change', None))
    else:
        result['category'] = 'other'
    size = re.search(r'\bTried to allocate (\d+(?:\.\d+)?) (bytes|KiB|MiB|GiB)\b', message)
    if size:
        amount = float(size.group(1)) * {'bytes': 1, 'KiB': 2**10, 'MiB': 2**20, 'GiB': 2**30}[size.group(2)]
        if math.isfinite(amount):
            result['requested_allocation_bytes'] = int(amount)
    return result


def checkpoint_change(value):
    """Fixed file-change evidence only; no paths, names or timestamp values."""
    value = mapping(value)
    result = {}
    if value.get('source') in ('path', 'handle'):
        result['source'] = value['source']
    fields = value.get('fields', ())
    result['fields'] = [field for field in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
                        if field in sequence(fields)]
    return result


def token_summary(value):
    return {key: count for key, count in numbers(value, ('sequences', 'max_sequence_tokens',
        'total_tokens', 'image_count', 'video_count')).items() if type(count) is int}


def encoder_timing(value):
    from .encoder_diagnostics import STAGES
    value = mapping(value)
    result = numbers(value, ('version', 'elapsed_seconds', 'diagnostic_seconds'))
    if type(value.get('complete')) is bool:
        result['complete'] = value['complete']
    stages = numbers(value.get('stage_seconds'), STAGES)
    if stages:
        result['stage_seconds'] = stages
    return result


def encoder_runtime(value):
    value = mapping(value)
    return {key: value[key] for key in ('resident_worker', 'torch_imported_at_start',
        'cuda_initialized_at_start', 'native_imported_at_start') if type(value.get(key)) is bool}


def encoder_prewarm(value):
    value = mapping(value)
    result = numbers(value, ('elapsed_seconds', 'seconds', 'bytes', 'done_bytes', 'total_bytes',
        'gpu_bytes_before', 'gpu_bytes_after', 'gpu_budget_bytes', 'age_seconds'))
    if value.get('state') in ('waiting', 'loading', 'ready', 'partial', 'skipped', 'cancelled', 'released', 'failed', 'complete'):
        result['state'] = value['state']
    for key in ('gpu_preload', 'worker_released', 'receipt_available'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    if value.get('purpose') in ('encoder', 'upscale'):
        result['purpose'] = value['purpose']
    return result


def embedding(value):
    """Tensor metadata only: no indices, tensor values, prompt or media content."""
    value = mapping(value)
    result = numbers(value, ('group_size',))
    for key in ('indices_shape', 'weight_shape', 'scale_shape', 'output_shape'):
        shape = value.get(key)
        if isinstance(shape, (tuple, list)) and len(shape) <= 8 and all(type(n) is int and n >= 0 for n in shape):
            result[key] = list(shape)
    if value.get('dtype') in ENCODER_DTYPES:
        result['dtype'] = value['dtype']
    if value.get('device') in ('cpu', 'cuda', 'meta'):
        result['device'] = value['device']
    return result


def tensor_metadata(value):
    """Only fixed tensor names and metadata, sanitized again before upload."""
    result = {}
    for key in TENSOR_VARIABLES:
        source = mapping(mapping(value).get(key))
        item = embedding(dict(indices_shape=source.get('shape'), dtype=source.get('dtype'),
                              device=source.get('device')))
        if 'indices_shape' not in item:
            continue
        item['shape'] = item.pop('indices_shape')
        result[key] = item
        if len(result) == 4:
            break
    return result


def _frame_tensors(frame):
    # Never import torch on the control plane. Exception collection can inspect
    # metadata if a worker already imported it, without copying tensor contents.
    torch = sys.modules.get('torch')
    tensor_type = getattr(torch, 'Tensor', None)
    if not isinstance(tensor_type, type):
        return {}
    result = {}
    for key in TENSOR_VARIABLES:
        tensor = frame.f_locals.get(key)
        if not issubclass(type(tensor), tensor_type):
            continue
        try:
            result[key] = dict(shape=list(tensor.shape), dtype=str(tensor.dtype).removeprefix('torch.'),
                               device=tensor.device.type)
        except (AttributeError, RuntimeError, TypeError):
            continue
        if len(result) == 4:
            break
    return tensor_metadata(result)


def mapping(value):
    return value if isinstance(value, dict) else {}


def sequence(value):
    return value if isinstance(value, (list, tuple)) else []


def attempt_metrics(value):
    """Retain completed work before retry artifacts move or a new worker starts."""
    value = mapping(value)
    result = {key: value[key] for key in ('config', 'device_memory', 'work_seconds', 'decode_phase',
        'sampling_reused', 'sampling_metrics_scope', 'resident_admission',
        'sample_finalize_phase', 'sampling_checkpoint_complete', 'sample_metrics_scope',
        'load_seconds', 'sample_seconds', 'latent_save_seconds', 'decode_save_seconds',
        'load_peak_allocated_bytes', 'load_peak_reserved_bytes', 'torch_peak_allocated_bytes',
        'torch_peak_reserved_bytes', 'final_stage_peak_allocated_bytes', 'final_stage_peak_reserved_bytes',
        'step_seconds', 'sampling_memory', 'sampling_passes', 'latent_upscale') if key in value}
    if 'failure_cleanup' in value:
        result['failure_cleanup'] = failure_cleanup(value['failure_cleanup'])
    if 'decoder_read_ahead' in value:
        result['decoder_read_ahead'] = decoder_read_ahead(value.get('decoder_read_ahead'))
    decoded = decoder_measurements(value)
    if decoded:
        result['decoder_measurements'] = decoded
    kernels = kernel_receipt(value)
    if kernels:
        result['kernels'] = kernels
    result['completed_steps'] = len(sequence(mapping(value.get('sampling_memory')).get('steps')))
    return result


def decoder_measurements(value):
    """Observed VAE work/placement, never checkpoint paths or tensor contents."""
    value = mapping(value)
    result = numbers(value, ('vae_load_seconds', 'video_decode_seconds',
        'audio_load_decode_seconds', 'video_postprocess_seconds', 'encode_seconds',
        'decoded_artifact_save_seconds', 'decode_save_seconds', 'vae_resident_blocks',
        'vae_pinned_weight_bytes'))
    for key in ('vae_linear_compute_cache', 'resident_vae_cache_hit', 'resident_audio_vae_cache_hit',
                'streamed_video_output', 'streamed_vae_weights'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    prepared = numbers(value.get('vae_compute_cache_preparation'),
                       ('linear_parameter_count', 'prepared_parameter_bytes',
                        'streamed_parameter_count', 'streamed_parameter_bytes'))
    if prepared:
        result['vae_compute_cache_preparation'] = prepared
    transfers = weight_placement(value.get('vae_offload'))
    transfers.update(numbers(value.get('vae_offload'), ('slots',)))
    if transfers:
        result['vae_offload'] = transfers
    return result


def failure_cleanup(value):
    value = mapping(value)
    result = numbers(value, ('frames_cleared',))
    if type(value.get('complete')) is bool:
        result['complete'] = value['complete']
    for key in ('before', 'after'):
        if isinstance(value.get(key), dict):
            result[key] = gpu_snapshot(value[key])
    if value.get('errors'):
        result['errors'] = [trace(row) for row in sequence(value['errors'])[:2]]
    return result


def decoder_read_ahead(value):
    """Keep bounded decoder overlap evidence without paths or free-form text."""
    value = mapping(value)
    result = numbers(value, ('read_bytes', 'total_bytes', 'allowance_bytes', 'target_bytes',
                             'elapsed_seconds', 'private_buffer_bytes',
                             'gpu_allocation_bytes'))
    state = value.get('state')
    if state in ('not-started', 'reading', 'ready', 'partial', 'skipped', 'stopped', 'failed'):
        result['state'] = state
    sampling = mapping(value.get('sampling'))
    if sampling:
        result['sampling'] = numbers(sampling, ('trigger_step', 'steps_remaining',
                                                'estimated_remaining_seconds',
                                                'trigger_elapsed_seconds'))
    return result


def kernel_receipt(value):
    """Keep backend call counts and parallel execution counters only."""
    value = mapping(value)
    result = {}
    calls = mapping(value.get('attention_backend_calls'))
    if calls:
        result['attention_backend_calls'] = {
            key: value for key, value in calls.items()
            if key in ATTENTION_BACKENDS and type(value) is int and value >= 0
        }
    fp8 = mapping(value.get('fp8_kernel_calls'))
    if fp8:
        result['fp8_kernel_calls'] = {
            key: count for key, count in fp8.items()
            if key in ('torch_scaled_mm', 'cublas_fp8', 'rescale', 'triton_ff_up')
            and type(count) is int and count >= 0
        }
    execution = mapping(value.get('head_execution'))
    if execution:
        item = numbers(execution, ('requested_parallelism', 'parallel_head_calls', 'parallel_head_warmups'))
        if item:
            result['head_execution'] = item
    return result


def numbers(value, keys):
    value = mapping(value)
    return {k: value[k] for k in keys if type(value.get(k)) in (int, float)
            and math.isfinite(value[k]) and value[k] >= 0}


def weight_placement(value):
    value = mapping(value)
    result = numbers(value, WEIGHT_PLACEMENT)
    if type(value.get('host_prefetch')) is bool:
        result['host_prefetch'] = value['host_prefetch']
    for key in ('host_prefetch_disabled_reason', 'host_prefetch_shared_reason'):
        if value.get(key) in ('host_headroom', 'host_allocation_refused', 'live_host_pressure'):
            result[key] = value[key]
    return result


def encoder_discard(value):
    """Report only the observed discard mode, never native decision text."""
    modes = [row['encoder_discarded_without_cpu_copy']
             for raw in sequence(mapping(value).get('resident_admission'))
             for row in (mapping(raw),) if row.get('action') == 'evict' and row.get('role') == 'encoder'
             and type(row.get('encoder_discarded_without_cpu_copy')) is bool]
    return {'encoder_discarded_without_cpu_copy': all(modes)} if modes else {}


def sampling_checkpoint(value):
    """Keep completion and cleanup evidence without paths or free-form text."""
    value = mapping(value)
    result = {}
    if value.get('sample_finalize_phase') in SAMPLE_FINALIZE_PHASES:
        result['phase'] = value['sample_finalize_phase']
    if type(value.get('sampling_checkpoint_complete')) is bool:
        result['complete'] = value['sampling_checkpoint_complete']
    if value.get('sample_metrics_scope') == 'Completed sampling before offloader cleanup; finalization is excluded.':
        result['finalization_excluded'] = True
    return {'sampling_checkpoint': result} if result else {}


def name(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,96}', value) else None


def exception_details(error):
    """Capture locations and bounded tensor metadata, never source or values."""
    chain, seen = [], set()
    while isinstance(error, BaseException) and id(error) not in seen and len(chain) < 4:
        seen.add(id(error))
        frames, tb = [], error.__traceback__
        while tb is not None:
            code = tb.tb_frame.f_code
            frames.append(dict(file=code.co_filename.replace('\\', '/').rsplit('/', 1)[-1],
                module=tb.tb_frame.f_globals.get('__name__'), function=code.co_name, line=tb.tb_lineno,
                tensor_metadata=_frame_tensors(tb.tb_frame)))
            tb = tb.tb_next
        tensor_frames = [frame for frame in frames if frame.get('tensor_metadata')]
        for frame in tensor_frames[:-3]:
            frame.pop('tensor_metadata', None)
        chain.append(dict(exception_type=type(error).__name__, frames=frames[-32:], **error_details(error),
                          **numbers({'errno': getattr(error, 'errno', None),
                                     'winerror': getattr(error, 'winerror', None)}, ('errno', 'winerror'))))
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return [trace(row) for row in chain]


def trace(value):
    value = mapping(value)
    result = numbers(value, ('errno', 'winerror', 'error_code', 'requested_allocation_bytes'))
    if value.get('category') in ERROR_CATEGORIES:
        result['category'] = value['category']
    if value.get('category') == 'checkpoint_changed':
        result['checkpoint_change'] = checkpoint_change(value.get('checkpoint_change'))
    if name(value.get('exception_type')):
        result['exception_type'] = value['exception_type']
    result['frames'] = []
    for frame in sequence(value.get('frames'))[-24:]:
        frame = mapping(frame)
        item = numbers(frame, ('line',))
        for key in ('file', 'module', 'function'):
            if name(frame.get(key)):
                item[key] = frame[key]
        tensors = tensor_metadata(frame.get('tensor_metadata'))
        if tensors:
            item['tensor_metadata'] = tensors
        if 'line' in item and ('file' in item or 'module' in item):
            result['frames'].append(item)
    tensor_frames = [frame for frame in result['frames'] if frame.get('tensor_metadata')]
    for frame in tensor_frames[:-3]:
        frame.pop('tensor_metadata', None)
    return result


def gpu_snapshot(value):
    from .encoder_memory import GPU_FIELDS
    result = numbers(value, GPU_FIELDS)
    errors = mapping(mapping(value).get('collection_errors'))
    clean = {}
    for section in ('cuda_memory', 'allocator_stats', 'memory_fraction', 'host_pinning',
                    'native_placement', 'windows_memory'):
        rows = sequence(errors.get(section))
        if not rows:
            continue
        item = trace(rows[0])
        # Optional counter failures need their error code and location, not
        # repeated activation metadata from the primary model exception.
        item['frames'] = [{key: val for key, val in frame.items() if key != 'tensor_metadata'}
                          for frame in item['frames'][-3:]]
        clean[section] = [item]
    if clean:
        result['collection_errors'] = clean
    return result


def guard(value):
    value = mapping(value)
    result = numbers(value, GUARD)
    result['reasons'] = [r for r in sequence(value.get('reasons'))[:4]
                         if r in ('working_memory_budget', 'system_or_commit_pressure')]
    if type(value.get('budget_is_estimate')) is bool:
        result['budget_is_estimate'] = value['budget_is_estimate']
    return result


def resources(value):
    value = mapping(value)
    result = dict(ram=numbers(value.get('ram'), RAM_PEAKS), ram_guard=guard(value.get('ram_guard')))
    ram = mapping(value.get('ram'))
    for key in ('process_tree_guard_complete', 'process_tree_pss_complete', 'process_tree_io_complete'):
        if type(ram.get(key)) is bool:
            result['ram'][key] = ram[key]
    if isinstance(value.get('ram_budget_warning'), dict):
        result['ram_budget_warning'] = guard(value['ram_budget_warning'])
    for key in ('ram_start', 'ram_last'):
        result[key] = numbers(value.get(key), RAM_SAMPLE)
    gpu = mapping(value.get('gpu'))
    result['gpu'] = numbers(gpu, ('gpu_peak_bytes', 'gpu_baseline_bytes', 'gpu_last_bytes', 'samples',
        'max_temperature_c', 'max_power_mw', 'busy_util_threshold_percent', 'sampling_interval_seconds'))
    # Error strings can contain paths; preserve a count, not their text.
    if isinstance(gpu.get('sampling_errors'), list):
        result['gpu']['sampling_error_count'] = len(gpu['sampling_errors'])
    from .monitoring import Activity
    for key in ('activity', 'busy_activity'):
        if not isinstance(gpu.get(key), dict):
            continue
        observed = gpu[key]
        clean = numbers(observed, ('samples', 'clock_reason_samples', 'clock_reason_mask'))
        for field in Activity.fields:
            if isinstance(observed.get(field), dict):
                clean[field] = numbers(observed[field], ('samples', 'minimum', 'maximum', 'mean'))
        if isinstance(observed.get('clock_reason_counts'), dict):
            clean['clock_reason_counts'] = numbers(observed['clock_reason_counts'], tuple(Activity.clock_reasons))
        result['gpu'][key] = clean
    return result


WINDOWS_BUDGET = ('peak_usage_bytes', 'minimum_budget_bytes', 'maximum_budget_bytes',
                 'first_budget_bytes', 'last_budget_bytes', 'budget_changes', 'over_budget_samples', 'samples')
# Whole-device NVML samples inside one sampling step (monitoring.step_activity).
STEP_ACTIVITY = ('samples', 'busy_samples', 'gpu_util_mean_percent', 'sm_clock_mean_mhz', 'power_mean_mw',
                 'device_used_peak_bytes', 'device_used_minimum_bytes', 'device_total_bytes')


def dynamic_budget(value):
    value = mapping(value)
    result = numbers(value, ('base_reserve_bytes', 'maximum_allocator_bytes', 'minimum_limit_bytes',
                             'maximum_limit_bytes', 'observations'))
    if type(value.get('enabled')) is bool:
        result['enabled'] = value['enabled']
    def clean(row):
        row = mapping(row)
        item = numbers(row, ('elapsed_seconds', 'previous_limit_bytes', 'candidate_limit_bytes',
            'allocator_limit_bytes', 'live_free_bytes', 'reserved_bytes', 'non_torch_local_bytes', 'driver_budget_bytes',
            'device_used_bytes', 'workspace_bytes', 'settle_seconds', 'settle_samples', 'settled_candidate_bytes'))
        for key, allowed in (('stage', ('load', 'sample', 'upscale', 'decode')),
                             ('action', ('hold', 'pending', 'shrink', 'settled', 'grow'))):
            if row.get(key) in allowed:
                item[key] = row[key]
        if type(row.get('driver_available')) is bool:
            item['driver_available'] = row['driver_available']
        return item
    result['last'] = clean(value.get('last'))
    result['transitions'] = [clean(row) for row in sequence(value.get('transitions'))[:64]]
    return result


def allocator(value):
    value = mapping(value)
    result = numbers(value, ('budget_bytes', 'device_total_bytes', 'effective_allocator_limit_bytes'))
    for key in ('enforced', 'windows_allocator_limit_enforced', 'benchmark_allocator_limit_enforced',
                'capacity_trial_limit_enforced'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    decision = mapping(value.get('admission'))
    result['admission'] = numbers(decision, ('allocator_limit_bytes', 'observed_non_torch_local_bytes',
        'owned_allocator_reserved_bytes', 'live_free_bytes', 'growth_reserve_bytes'))
    result['admission']['bounds'] = numbers(decision.get('bounds'), ('planning_budget_bytes',
        'physical_capacity_bytes', 'live_pool_capacity_bytes', 'wddm_pool_capacity_bytes'))
    observed = mapping(value.get('windows_memory'))
    result['windows_memory'] = {segment: numbers(observed.get(segment), ('budget_bytes', 'usage_bytes'))
                                for segment in ('local', 'nonlocal')}
    if value.get('dynamic_budget'):
        result['dynamic_budget'] = dynamic_budget(value['dynamic_budget'])
    return result


def sampling(value):
    from .compilation_diagnostics import sanitize
    value = mapping(value)
    result = dict(steps=[])
    for row in sequence(value.get('steps'))[:63]:
        row = mapping(row)
        item = numbers(row, ('step', 'seconds', 'allocated_bytes', 'reserved_bytes',
            'cumulative_peak_allocated_bytes', 'cumulative_peak_reserved_bytes',
            'inactive_split_bytes', 'allocation_retries', 'allocator_ooms'))
        item['host'] = numbers(row.get('host'), HOST_SAMPLE)
        item['process_delta'] = numbers(row.get('process_delta'), PROCESS_DELTA)
        item['compiler'] = sanitize(row.get('compiler'))
        observed = mapping(row.get('windows'))
        for segment in ('local', 'nonlocal'):
            item[segment] = numbers(observed.get(segment),
                WINDOWS_BUDGET)
        device = numbers(row.get('device_activity'), STEP_ACTIVITY)
        if device:
            item['device'] = device
        result['steps'].append(item)
    observed = mapping(value.get('incomplete_step_windows'))
    result['incomplete_step'] = {segment: numbers(observed.get(segment),
        WINDOWS_BUDGET)
        for segment in ('local', 'nonlocal')}
    result['incomplete_step_compiler'] = sanitize(value.get('incomplete_step_compiler'))
    return result


def sampling_passes(value):
    """Per-canvas execution evidence with no paths, prompts or tensor values."""
    result = []
    for index, row in enumerate(sequence(value)[:2]):
        row = mapping(row)
        config = mapping(row.get('compute_configuration'))
        item = dict(index=index + 1,
            geometry=numbers(row.get('geometry'), ('width', 'height', 'frames', 'video_tokens',
                                                  'reference_video_tokens', 'reference_audio_tokens')),
            timing=numbers(row, ('sample_seconds', 'torch_peak_allocated_bytes', 'torch_peak_reserved_bytes')),
            config=numbers(config, ENGINE_KNOBS), offload=weight_placement(row.get('offload')),
            cache_admission=numbers(row.get('pass_cache_admission'), ('gpu_budget_bytes', 'admitted_bytes',
                'measured_peak_reserved_bytes', 'live_free_bytes', 'commit_available_bytes',
                'requested_resident_blocks', 'workspace_peak_bytes', 'peak_cache_bytes', 'device_headroom_bytes')),
            kernels=kernel_receipt(row))
        item['config'].update({key: config[key] for key in ENGINE_KNOBS if type(config.get(key)) is bool})
        refinement = mapping(row.get('refinement'))
        if refinement.get('schedule') in ('community-sigma3-v1', 'original-tail'):
            item['refinement'] = dict(numbers(refinement, ('base_steps', 'steps', 'start_index')),
                                      schedule=refinement['schedule'])
        preparation = mapping(row.get('refinement_schedule_preparation'))
        if preparation.get('schedule') == 'community-sigma3-v1':
            item['schedule_cache'] = numbers(preparation, ('seconds', 'table_cache_hits'))
        item['step_seconds'] = [duration for duration in sequence(row.get('step_seconds'))[:32]
                                if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0]
        result.append(item)
    return result


def latent_upscale(value):
    value = mapping(value)
    result = numbers(value, ('load_seconds', 'compute_seconds', 'stage_seconds', 'scale',
        'estimated_workspace_bytes', 'available_workspace_bytes', 'allocation_retries',
        'torch_peak_allocated_bytes', 'torch_peak_reserved_bytes'))
    for key in ('transformer_reloaded', 'temporal_chunking', 'attempted_below_estimate', 'buffer_reuse'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    failures = []
    for row in sequence(value.get('allocation_failures'))[:2]:
        row = mapping(row)
        if row.get('category') in ERROR_CATEGORIES:
            failures.append(dict(category=row['category'],
                **numbers(row, ('error_code', 'requested_allocation_bytes'))))
    if failures:
        result['allocation_failures'] = failures
    return result


def failure(value):
    value = mapping(value)
    result = dict(ram_guard=guard(value.get('ram_guard')))
    if isinstance(value.get('gpu'), dict):
        result['gpu'] = gpu_snapshot(value['gpu'])
    if isinstance(value.get('memory'), dict):
        result['memory'] = numbers(value['memory'], RAM_SAMPLE)
    if isinstance(value.get('memory_collection_error'), list):
        result['memory_collection_error'] = [trace(row) for row in value['memory_collection_error'][:4]]
    result['allocation'] = numbers(value.get('allocation'), ('fp8_ff_activation_stash_bytes',))
    if value.get('kind') in ('gpu_oom', 'ram_pressure', 'cuda_error', 'timeout', 'cancelled',
                            'code_error', 'unknown_worker_exit', 'worker_stop_unconfirmed'):
        result['kind'] = value['kind']
    for key in ('exception', 'controller_exception'):
        result[key] = [trace(row) for row in sequence(value.get(key))[:4]]
    return result


def planning(rows):
    result = []
    for row in sequence(rows)[:8]:
        row = mapping(row)
        item = {}
        for key in ('stage', 'status'):
            if name(row.get(key)):
                item[key] = row[key]
        if type(row.get('capacity_trial')) is bool:
            item['capacity_trial'] = row['capacity_trial']
        for key in ('raw', 'adjusted'):
            item[key] = numbers(row.get(key), HARDWARE_MEMORY)
        item['system_memory'] = numbers(row.get('system_memory'),
            ('total_bytes', 'available_bytes', 'physical_available_bytes', 'commit_available_bytes'))
        for key in ('budget_bytes', 'reserve_bytes'):
            item[key] = numbers(row.get(key), ('gpu', 'ram'))
        item['resource_error'] = numbers(row.get('resource_error'), tuple(resource + '_' + field
            for resource in ('gpu', 'ram') for field in
            ('capacity_bytes', 'available_bytes', 'reserve_bytes', 'budget_bytes', 'minimum_bytes')))
        idle = mapping(row.get('idle_cache'))
        item['idle_cache'] = dict(present=bool(idle), **numbers(idle, ('reclaimable_gpu_bytes',
            'reclaimable_ram_bytes', 'retained_pinned_ram_bytes')))
        item['idle_cache']['memory'] = numbers(idle.get('memory'), RAM_SAMPLE)
        item['idle_cache']['gpu_capacity'] = numbers(idle.get('gpu_capacity'),
            ('allocator_limit_bytes', 'owned_allocator_reserved_bytes', 'live_free_bytes',
             'observed_non_torch_local_bytes', 'growth_reserve_bytes'))
        item['idle_cache']['accounting'] = numbers(idle.get('ram_accounting'),
            ('raw_available_bytes', 'credited_available_bytes', 'pinned_model_bytes',
             'reused_encoder_working_bytes'))
        result.append(item)
    return result


def attempts(rows):
    result = []
    rows = sequence(rows)
    indices = list(range(len(rows))) if len(rows) <= 3 else [0, 1, len(rows)-1]
    for index in indices:
        row = rows[index]
        row = mapping(row)
        profile, metrics = mapping(row.get('profile')), mapping(row.get('metrics'))
        item = dict(index=index + 1, failure=failure(row.get('failure')), resources=resources(row.get('resources')))
        item['resources'].update(encoder_discard(metrics))
        item['resources'].update(sampling_checkpoint(metrics))
        if 'failure_cleanup' in metrics:
            item['failure_cleanup'] = failure_cleanup(metrics['failure_cleanup'])
        for key in ('state', 'phase'):
            if name(row.get(key)):
                item[key] = row[key]
        item['budgets'] = {key: val*1e9 for key, field in (('gpu', 'gpu_budget_gb'), ('ram', 'inference_ram_budget_gb'))
                          for val in numbers(profile, (field,)).values() if math.isfinite(val*1e9)}
        config = mapping(metrics.get('config')) or mapping(profile.get('engine'))
        item['config'] = numbers(config, ENGINE_KNOBS)
        item['config'].update({k: config[k] for k in ENGINE_KNOBS if type(config.get(k)) is bool})
        item['timing'] = numbers(metrics, ('work_seconds', 'load_seconds', 'sample_seconds',
            'latent_save_seconds', 'decode_save_seconds'))
        if 'seconds' in numbers(row, ('seconds',)):
            item['timing']['controller_seconds'] = row['seconds']
        item['memory'] = numbers(metrics, ('load_peak_allocated_bytes', 'load_peak_reserved_bytes',
            'torch_peak_allocated_bytes', 'torch_peak_reserved_bytes',
            'final_stage_peak_allocated_bytes', 'final_stage_peak_reserved_bytes'))
        item['allocator'] = allocator(metrics.get('device_memory'))
        item['sampling'] = sampling(metrics.get('sampling_memory'))
        item['sampling_passes'] = sampling_passes(metrics.get('sampling_passes'))
        item['latent_upscale'] = latent_upscale(metrics.get('latent_upscale'))
        kernels = kernel_receipt(metrics)
        if kernels:
            item['kernels'] = kernels
        item['completed_nfe'] = max(len(sequence(metrics.get('step_seconds'))), numbers(metrics, ('completed_steps',)).get('completed_steps', 0))
        if type(metrics.get('sampling_reused')) is bool:
            item['sampling_reused'] = metrics['sampling_reused']
        decoder = mapping(profile.get('decoder'))
        item['decoder'] = numbers(decoder, DECODER_KNOBS)
        item['decoder'].update({key: decoder[key] for key in DECODER_KNOBS if type(decoder.get(key)) is bool})
        if metrics.get('decode_phase') in DECODE_PHASES:
            item['decoder']['phase'] = metrics['decode_phase']
        decoded = decoder_measurements(metrics.get('decoder_measurements') or metrics)
        if decoded:
            item['decoder']['measurements'] = decoded
        recovery = mapping(row.get('recovery'))
        if recovery:
            following = mapping(recovery.get('next_profile'))
            item['recovery'] = numbers(recovery, ('attempt', 'max_attempts'))
            if type(recovery.get('reuse_sampling')) is bool:
                item['recovery']['reuse_sampling'] = recovery['reuse_sampling']
            if recovery.get('failed_phase') in ('load', 'sample', 'sampling', 'sample_finalize', 'decode', 'save', 'complete'):
                item['recovery']['phase'] = recovery['failed_phase']
            for section, keys in (('engine', ENGINE_KNOBS), ('decoder', DECODER_KNOBS)):
                before, after = mapping(profile.get(section)), mapping(following.get(section))
                planned = numbers(after, keys)
                planned.update({key: after[key] for key in keys if type(after.get(key)) is bool})
                changes = {}
                for key, value in planned.items():
                    old = before.get(key)
                    if value != old and (type(old) is bool or numbers({key: old}, (key,))):
                        changes[key] = dict(before=old, after=value)
                item['recovery']['next_' + section] = planned
                item['recovery'][section + '_changes'] = changes
            item['recovery']['next_budgets'] = {key: val * 1e9
                for key, field in (('gpu', 'gpu_budget_gb'), ('ram', 'inference_ram_budget_gb'))
                for val in numbers(following, (field,)).values() if math.isfinite(val * 1e9)}
        result.append(item)
    return result
