"""Compact local report formatting; no network access or background work."""
import hashlib
import json
import math
from pathlib import Path
import re

from . import __version__


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _measurements(value, names):
    value = value if isinstance(value, dict) else {}
    return {k:v for k in names if (v := _number(value.get(k))) is not None}


def _label(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9 _().+-]{1,96}', value) else None


def summary_report(report):
    """Fresh structure, not a redacted copy of arbitrary diagnostic text."""
    from . import diagnostic_resources as diagnostic
    from .compilation_diagnostics import sanitize as compiler_summary
    summary = report.get('summary') or {}
    engine = report.get('engine') or {}
    hardware = summary.get('hardware') or {}
    config = summary.get('effective_engine_config') or {}
    result = dict(kind='generation', status=summary.get('status') if summary.get('status') in
                  ('complete', 'reused', 'retrying', 'running') else 'incomplete',
                  version=__version__, hardware={}, config={}, geometry={}, memory={}, stages=[], steps=[], errors=[])
    for k in ('gpu_name', 'system', 'architecture', 'driver_version', 'torch_version', 'cuda_version'):
        if _label(hardware.get(k)):
            result['hardware'][k] = hardware[k]
    capability = hardware.get('capability')
    if isinstance(capability, (tuple,list)) and len(capability)==2 and all(type(x) is int and x>=0 for x in capability):
        result['hardware']['capability'] = list(capability)
    result['hardware'].update(_measurements(hardware, ('vram_total','vram_free','ram_total','ram_available')))
    result['offload'] = diagnostic.weight_placement(summary.get('weight_placement'))
    result['sampling_passes'] = diagnostic.sampling_passes(summary.get('sampling_passes') or engine.get('sampling_passes'))
    result['latent_upscale'] = diagnostic.latent_upscale(summary.get('latent_upscale') or engine.get('latent_upscale'))
    decoder = summary.get('decoder_options') or {}
    result['decoder'] = _measurements(decoder, ('resident_blocks',))
    for key in ('offload','prefetch','stream_weights','stream_output','pin_weights','preload','tile_group',
                'linear_compute_cache'):
        if type(decoder.get(key)) is bool:
            result['decoder'][key] = decoder[key]
    if engine.get('decode_phase') in diagnostic.DECODE_PHASES:
        result['decoder']['phase'] = engine['decode_phase']
    decoded = diagnostic.decoder_measurements(engine)
    if decoded:
        result['decoder']['measurements'] = decoded
    result['decoder_read_ahead'] = diagnostic.decoder_read_ahead(summary.get('decoder_read_ahead'))
    kernels = diagnostic.kernel_receipt(summary.get('kernels') or engine)
    if kernels:
        result['kernels'] = kernels
    for key in ('conditioning_cache_hit','preencoded'):
        if type(summary.get(key)) is bool:
            result[key] = summary[key]
    result['geometry'] = _measurements(summary.get('geometry'), ('width','height','frames','fps','video_tokens','reference_video_tokens','reference_audio_tokens'))
    shape = engine.get('conditioning_shape')
    if isinstance(shape, (list, tuple)) and len(shape) == 2 and all(type(x) is int and x > 0 for x in shape):
        result['geometry']['text_tokens'] = shape[0]
    for k in ('resident_blocks','pin_host_gb','head_chunk','window_batch','head_parallelism','ff_chunk','projection_chunk',
              'query_chunk','fp8_linears','resident_weight_bytes','pinned_model_bytes','pinned_host_allocated_bytes','steps',
              'adaln_optional_blocks','adaln_downloaded_files','adaln_downloaded_bytes'):
        if _number(config.get(k)) is not None:
            result['config'][k] = config[k]
    for k in ('prefetch','stream_weights','attention_cpu_outputs','grouped_attention_outputs','residual_offload','fp8_ff_recompute',
              'inference_kernels','window_varlen','varlen_smooth_k','reuse_block_outputs','preload_host','pin_host_weights','cache_refined_text'):
        if type(config.get(k)) is bool:
            result['config'][k] = config[k]
    for k, allowed in (('task', ('t2va','fl2va','ref2va')), ('linear_compute',('native-fp8','bf16-weight-only')),
                       ('adaln_mode', ('portable-model-asset','optional-model-asset','local-precompute','original-projections'))):
        if config.get(k) in allowed:
            result['config'][k] = config[k]
    for k, allowed in (('fp8_gemm', ('torch','scaled-mm-epilogue','triton')),
                       ('fp8_scale_granularity', ('per_tensor','rowwise')), ('precision', ('fp8','bf16','fp16'))):
        if config.get(k) in allowed:
            result['config'][k] = config[k]
    attention = config.get('attention', '')
    if isinstance(attention, str) and all(x in ('sage2','cudnn','torch-flash','fa2','fa4','dense') for x in attention.split('/')):
        result['config']['attention'] = attention
    if isinstance(report.get('runtime_code'), dict):
        result['build_id'] = hashlib.sha256(json.dumps(report['runtime_code'],sort_keys=True).encode()).hexdigest()
    result.update(_measurements(summary, ('request_seconds','seconds_per_completed_nfe')))
    for row in (summary.get('stages') or [])[:16]:
        if row.get('stage') in ('text_encoding','model_load','sampling','latent_save','decode_save','other_or_unmeasured','installation'):
            result['stages'].append(dict(stage=row['stage'], **_measurements(row, ('seconds',))))
            if type(row.get('reused_from_attempt')) is bool:
                result['stages'][-1]['reused_from_attempt'] = row['reused_from_attempt']
    result['step_seconds'] = [x for x in (summary.get('step_seconds') or [])[:128] if _number(x) is not None]
    result['memory'] = _measurements(summary.get('observed_memory'), (
        'sampling_allocated_bytes','sampling_reserved_bytes','final_stage_allocated_bytes','final_stage_reserved_bytes',
        'whole_gpu_peak_bytes','working_ram_peak_bytes','rss_peak_bytes','pss_peak_bytes','private_commit_peak_bytes'))
    for phase in ('video_ram', 'encoding_ram'):
        result['memory'][phase] = _measurements((summary.get('observed_memory') or {}).get(phase), (
            'process_tree_peak_guard_bytes','process_tree_peak_rss_bytes','process_tree_peak_pss_bytes',
            'process_tree_peak_private_commit_bytes','system_min_available_bytes',
            'system_min_physical_available_bytes','system_min_commit_available_bytes'))
    result['budgets'] = _measurements(summary.get('budget_bytes'), ('gpu','ram'))
    device = summary.get('device_memory') or engine.get('device_memory') or {}
    result['allocator'] = _measurements(device, ('budget_bytes','device_total_bytes','effective_allocator_limit_bytes'))
    if type(device.get('windows_allocator_limit_enforced')) is bool:
        result['allocator']['windows_allocator_limit_enforced'] = device['windows_allocator_limit_enforced']
    if device.get('dynamic_budget'):
        result['allocator']['dynamic_budget'] = diagnostic.dynamic_budget(device['dynamic_budget'])
    for row in ((summary.get('sampling_memory') or {}).get('steps') or [])[:128]:
        item = _measurements(row, ('step','seconds','elapsed_seconds','allocated_bytes','reserved_bytes','cumulative_peak_allocated_bytes',
                                     'cumulative_peak_reserved_bytes','inactive_split_bytes','allocation_retries','allocator_ooms'))
        item['host'] = diagnostic.numbers(row.get('host'), diagnostic.HOST_SAMPLE)
        item['process_delta'] = diagnostic.numbers(row.get('process_delta'), diagnostic.PROCESS_DELTA)
        item['compiler'] = compiler_summary(row.get('compiler'))
        for segment in ('local','nonlocal'):
            item[segment] = _measurements((row.get('windows') or {}).get(segment),
                diagnostic.WINDOWS_BUDGET)
        result['steps'].append(item)
    sampling = summary.get('sampling_memory') or {}
    if sampling.get('windows_status') in ('observing','unavailable','not-applicable','reader-still-stopping'):
        result['windows_status'] = sampling['windows_status']
    result['incomplete_step_memory'] = dict(compiler=compiler_summary(sampling.get('incomplete_step_compiler')))
    for segment in ('local','nonlocal'):
        result['incomplete_step_memory'][segment] = _measurements((sampling.get('incomplete_step_windows') or {}).get(segment),
            diagnostic.WINDOWS_BUDGET)
    failures = [engine.get('failure') or {}] + [r.get('failure') or {} for r in (summary.get('failed_attempts') or [])[:3]]
    for row in failures:
        kind = row.get('kind')
        if kind in ('gpu_oom','ram_pressure','cuda_error','timeout','cancelled','code_error','unknown_worker_exit'):
            result['errors'].append(dict(kind=kind, ram_guard=_measurements(row.get('ram_guard'),
                ('working_bytes','budget_bytes','available_bytes','emergency_floor_bytes'))))
    # Frame names from our own source help locate failures without retaining
    # arbitrary exception messages (which may contain a prompt or access key).
    known = {p.name for p in Path(__file__).parent.glob('*.py')}
    frames = []
    for text in (report.get('log_tails') or {}).values():
        if not isinstance(text, str):
            continue
        for filename, line, function in re.findall(r'freevideo_engine[\\/]([a-z_]+\.py)"?, line (\d+), in ([A-Za-z_0-9]+)', text[-8192:]):
            if filename in known:
                frames.append(dict(file=filename, line=int(line), function=function[:64]))
    result['stack_frames'] = frames[-24:]
    request = diagnostic.mapping(report.get('request'))
    result['resource_planning'] = diagnostic.planning(request.get('resource_planning'))
    result['attempts'] = diagnostic.attempts(request.get('resource_attempts'))
    result['exception'] = [diagnostic.trace(row) for row in diagnostic.sequence(request.get('exception'))[:4]]
    result['encoder_failure'] = diagnostic.failure(diagnostic.mapping(request.get('encoding_failure')).get('failure'))
    result['encoder_resources'] = (diagnostic.resources(diagnostic.mapping(report.get('memory')).get('encoding'))
                                   if not summary.get('conditioning_cache_hit') and not summary.get('preencoded') else {})
    previous_prewarm = diagnostic.encoder_prewarm(diagnostic.mapping(report.get('bridge')).get('encoder_prewarm'))
    if previous_prewarm:
        result['encoder_resources']['previous_prewarm'] = previous_prewarm
    if not summary.get('conditioning_cache_hit') and not summary.get('preencoded'):
        encoding = diagnostic.mapping(report.get('encoding'))
        from .encoder_diagnostics import STAGES
        result['encoder_resources']['timing'] = diagnostic.encoder_timing(encoding.get('timing'))
        result['encoder_resources']['runtime'] = diagnostic.encoder_runtime(encoding.get('runtime'))
        result['encoder_resources'].update({key: encoding[key] for key in
            ('resident_encoder_cache_hit', 'resident_encoder_gpu_ready_at_start') if type(encoding.get(key)) is bool})
        if encoding.get('phase') in STAGES:
            result['encoder_resources']['phase'] = encoding['phase']
        checkpoint = diagnostic.mapping(encoding.get('checkpoint'))
        if checkpoint.get('state') in ('read_only_mmap', 'native_mmap', 'resident_reuse'):
            result['encoder_resources']['checkpoint'] = dict(
                diagnostic.numbers(checkpoint, ('bytes',)), state=checkpoint['state'])
        result['encoder_resources']['load_stages'] = []
        result['encoder_resources']['gpu'] = dict(result['encoder_resources'].get('gpu', {}),
            **diagnostic.gpu_snapshot(encoding.get('gpu')))
        result['encoder_resources']['token_summary'] = diagnostic.token_summary(encoding.get('token_summary'))
        result['encoder_resources']['embedding'] = diagnostic.embedding(encoding.get('embedding'))
        retry = diagnostic.mapping(encoding.get('diagnostic_retry'))
        if type(retry.get('index')) is int and 1 <= retry['index'] <= 3:
            result['encoder_resources']['retry'] = dict(index=retry['index'], final_attempt=retry.get('final_attempt') is True)
        result['encoder_resources']['oom_attempts'] = []
        for row in diagnostic.sequence(encoding.get('encoder_attempts'))[:3]:
            row = diagnostic.mapping(row)
            result['encoder_resources']['oom_attempts'].append(dict(
                diagnostic.gpu_snapshot(row), **diagnostic.numbers(row, ('attempt',)),
                exception=[diagnostic.trace(trace) for trace in diagnostic.sequence(row.get('exception'))[:4]],
                embedding=diagnostic.embedding(row.get('embedding'))))
        for row in diagnostic.sequence(encoding.get('load_stages'))[-48:]:
            row = diagnostic.mapping(row)
            if row.get('stage') in STAGES:
                result['encoder_resources']['load_stages'].append(dict(
                    diagnostic.numbers(row, ('seconds', 'duration_seconds')), stage=row['stage'],
                    process_delta=diagnostic.numbers(row.get('process_delta'), diagnostic.PROCESS_DELTA),
                    memory=diagnostic.numbers(row.get('memory'), diagnostic.RAM_SAMPLE),
                    gpu=diagnostic.gpu_snapshot(row.get('gpu'))))
    result['worker_resources'] = diagnostic.resources(diagnostic.mapping(report.get('memory')).get('video'))
    result['worker_resources'].update(diagnostic.encoder_discard(engine))
    result['worker_resources'].update(diagnostic.sampling_checkpoint(engine))
    if 'failure_cleanup' in engine:
        result['worker_resources']['failure_cleanup'] = diagnostic.failure_cleanup(engine['failure_cleanup'])
    # Keep the driver activity next to the kernel/backend receipt so a
    # performance review can correlate the actual backend and chunking with
    # utilization, clocks, power and allocator evidence.  This is whole-worker
    # activity (sampling plus decode), never a claim that it is sampling-only.
    if result.get('kernels') and result['worker_resources'].get('gpu'):
        result['kernels']['gpu_activity'] = result['worker_resources']['gpu']
    result['hardware_stage'] = 'request_start'
    result['diagnostic_revision'] = 9
    phase = (engine.get('phase') or diagnostic.mapping(request.get('encoding_failure')).get('phase')
             or request.get('phase'))
    if result['status'] == 'running':
        current = phase or diagnostic.mapping(report.get('encoding')).get('phase')
        if diagnostic.name(current):
            result['current_stage'] = current
    elif result['status'] not in ('complete', 'reused') and diagnostic.name(phase):
        result['failure_stage'] = phase
    if result['attempts']:
        result['errors'] = [dict(attempt=row['index'], phase=row.get('phase', 'unknown'),
                                kind=row['failure']['kind'], ram_guard=row['failure']['ram_guard'])
                            for row in result['attempts'] if row['failure'].get('kind')]
    peaks = [diagnostic.mapping(value.get('ram')) for value in
             [result['encoder_resources'], result['worker_resources']] + [r['resources'] for r in result['attempts']]]
    result['request_memory_peaks'] = {key: max(row[key] for row in peaks if key in row)
        for key in diagnostic.RAM_PEAKS if key.startswith('process_tree_peak_') and any(key in row for row in peaks)}
    result['coverage'] = dict(planning=bool(result['resource_planning']), attempts=bool(result['attempts']),
        encoder_timing=bool(result['encoder_resources'].get('timing', {}).get('stage_seconds')),
        encoder_memory=bool(result['encoder_resources'].get('ram')), worker_memory=bool(result['worker_resources'].get('ram')),
        structured_exception=bool(result['exception']) or bool(result['encoder_failure'].get('exception'))
            or any(row['failure'].get('exception') for row in result['attempts']))
    result['coverage'].update(attempts_observed=len(diagnostic.sequence(request.get('resource_attempts'))),
        attempts_retained=len(result['attempts']), planning_observed=len(diagnostic.sequence(request.get('resource_planning'))),
        planning_retained=len(result['resource_planning']))
    if type(engine.get('sampling_reused')) is bool:
        result['coverage']['sampling_reused'] = engine['sampling_reused']
    return result


PLACEMENT_FIELDS = ('head_chunk', 'window_batch', 'resident_blocks', 'pin_host_gb',
                    'ff_chunk', 'projection_chunk', 'prefetch', 'stream_weights',
                    'attention_cpu_outputs', 'residual_offload', 'fp8_ff_recompute')
CALIBRATION_MEASUREMENTS = ('sample_seconds', 'steady_seconds_per_step', 'load_seconds',
                            'elapsed_seconds', 'torch_peak_reserved_bytes',
                            'torch_peak_allocated_bytes', 'whole_gpu_peak_bytes',
                            'host_peak_bytes', 'residual_staged_steps', 'attempt')


def _placement(value):
    from . import diagnostic_resources as diagnostic
    value = diagnostic.mapping(value)
    result = diagnostic.numbers(value, PLACEMENT_FIELDS)
    result.update({key: value[key] for key in PLACEMENT_FIELDS if type(value.get(key)) is bool})
    return result


def _failure_kind(message):
    # A message can quote the prompt or a credential, even when it contains
    # only ordinary ASCII. Classify it without transmitting the original text.
    from .adaptive import classify_failure
    return classify_failure(str(message or ''))['kind']


_CALIBRATION_QUESTIONS = ('baseline', 'attention_outputs', 'ff_recompute',
    'pinned_host_cache', 'window_batch', 'resident_blocks', 'residual_staging')
_CALIBRATION_VARIANTS = ('as_planned', 'as_planned_again', 'changed', 'no_staging', 'with_staging')


def calibration_report(report):
    """Compact local placement comparison without prompts, paths or media."""
    from . import diagnostic_resources as diagnostic
    hardware = _measurements(report.get('hardware'), ('vram_total', 'vram_free', 'ram_total', 'ram_available'))
    source = diagnostic.mapping(report.get('hardware'))
    for name in ('gpu_name', 'system', 'architecture', 'torch_version', 'cuda_version', 'driver_version'):
        # Keep absent or invalid labels unknown.
        if _label(source.get(name)):
            hardware[name] = source[name][:64]
    if isinstance(source.get('capability'), (list, tuple)) and len(source['capability']) == 2:
        hardware['capability'] = [_number(value) for value in source['capability']]
    result = dict(hardware=hardware,
                  calibration_id=report.get('calibration_id') if re.fullmatch(r'[0-9a-f]{32}', str(report.get('calibration_id', ''))) else None,
                  questions=[row['name'] for row in (report.get('questions') or [])
                             if isinstance(row, dict) and row.get('name') in _CALIBRATION_QUESTIONS][:8],
                  geometry=_measurements(report.get('geometry'),
                                         ('width', 'height', 'frames', 'video_tokens')),
                  plan=_placement(report.get('plan')),
                  budget_bytes=_number(report.get('budget_bytes')),
                  gpu_reserve_bytes=_number(report.get('gpu_reserve_bytes')),
                  status=report.get('status') if report.get('status') in ('complete', 'error') else 'incomplete',
                  diagnostic_revision=5, results=[], summary={})
    if report.get('status') == 'error':
        result['error'] = _failure_kind(report.get('error'))
        result['failure_stage'] = 'encode' if str(report.get('error', '')).startswith('encode:') else 'calibration'
        frames = []
        for line in str(report.get('traceback') or '').splitlines():
            found = re.search(r'freevideo_engine[\\/]([a-z_]+\.py)", line (\d+), in ([A-Za-z_0-9]+)', line)
            if found:
                frames.append(dict(file=found.group(1), line=int(found.group(2)),
                                   function=found.group(3)[:64]))
        result['traceback'] = frames[-8:]
    for row in (report.get('results') or [])[:16]:
        row = diagnostic.mapping(row)
        entry = dict(_measurements(row, CALIBRATION_MEASUREMENTS),
                     question=row.get('question') if row.get('question') in _CALIBRATION_QUESTIONS else None,
                     variant=row.get('variant') if row.get('variant') in _CALIBRATION_VARIANTS else None,
                     status=row.get('status') if row.get('status') in ('complete', 'error') else 'incomplete')
        if row.get('status') != 'complete':
            entry['error'] = _failure_kind(row.get('error'))
        if type(row.get('finite_latents')) is bool:
            entry['finite_latents'] = row['finite_latents']
        entry['overrides'] = _placement(row.get('overrides'))
        entry['step_seconds'] = [x for x in (row.get('step_seconds') or [])[:32]
                                 if _number(x) is not None]
        result['results'].append(entry)
    # summarise() keeps the baseline beside the questions, not inside them.
    source = diagnostic.mapping(report.get('summary'))
    variants = dict(diagnostic.mapping(source.get('questions')))
    variants['baseline'] = diagnostic.mapping(source.get('baseline'))
    for question, value in variants.items():
        name = question
        if name not in _CALIBRATION_QUESTIONS:
            continue
        value = diagnostic.mapping(value)
        row = _measurements(value, ('runs', 'steady_seconds_per_step', 'drift',
                                    'torch_peak_reserved_bytes', 'whole_gpu_peak_bytes',
                                    'host_peak_bytes', 'faster_than_baseline'))
        # Express signed memory changes as explicitly named savings or costs.
        for field in ('host', 'reserved', 'whole_gpu'):
            delta = value.get(field + '_delta_bytes')
            if type(delta) in (int, float) and math.isfinite(delta):
                row[field + ('_added_bytes' if delta > 0 else '_saved_bytes')] = abs(delta)
        row['over_budget'] = value.get('over_budget') is True
        result['summary'][name] = row
    if isinstance(source.get('failed'), list):
        result['summary']['failed_variants'] = dict(count=len(source['failed']))
    return result
