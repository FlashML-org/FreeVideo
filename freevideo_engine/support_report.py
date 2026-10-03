"""One small, torch-free report per request, including failed requests."""
import hashlib
import json
import math
from pathlib import Path
import statistics
import time

from . import __version__
from .diagnostics import Redactor, is_link, read_bounded
from .diagnostic_resources import kernel_receipt
from .monitoring import save


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def mapping(value):
    return value if isinstance(value, dict) else {}


def code_identity():
    package = Path(__file__).parent
    names = ('generate.py', 'policy.py', 'runtime.py', 'attention.py', 'decode.py', 'worker.py',
             'encode_worker.py', 'encoder_checkpoint.py', 'system.py')
    values = {}
    for name in names:
        try:
            values[name] = hashlib.sha256((package / name).read_bytes()).hexdigest()
        except OSError:
            values[name] = None
    return dict(version=__version__, files_sha256=values)


def summarize(report, engine, encoding):
    profile = mapping(report.get('profile'))
    policy = mapping(profile.get('policy'))
    cache_hit = any(mapping(report.get(k)).get(flag) is True for k, flag in
                    (('input_cache', 'conditioning_hit'), ('tuning', 'conditioning_cache_hit'), ('encoding', 'cache_hit')))
    preencoded = str(report.get('encoder_mode', '')).startswith('preencoded')
    sampling_reused = engine.get('sampling_reused') is True
    stages = []
    # Substage timers overlap their parent. Only these top-level stages may
    # contribute to request accounting. Cached receipts describe a past run.
    for name, zh, value in (
        ('text_encoding', '文本编码（含加载）', 0 if cache_hit or preencoded else encoding.get('work_seconds')),
        ('model_load', '视频模型准备与加载', engine.get('load_seconds')),
        ('sampling', '采样', engine.get('sample_seconds')),
        ('latent_save', '保存潜变量', engine.get('latent_save_seconds')),
        ('decode_save', '解码与视频保存', engine.get('decode_save_seconds'))):
        stages.append(dict(stage=name, label_zh=zh, seconds=number(value)))
        if name == 'sampling' and sampling_reused:
            stages[-1].update(reused_from_attempt=True, label_zh='采样（复用本次请求已完成的采样）')
    total = number(report.get('request_seconds'))
    measured = sum(row['seconds'] for row in stages if row['seconds'] is not None)
    accounting = 'partial: missing stage timers' if any(r['seconds'] is None for r in stages) else 'complete stage timers'
    if total is not None and measured <= total + .01:
        stages.append(dict(stage='other_or_unmeasured', label_zh='其他／未单独计时', seconds=max(0., total-measured)))
    elif total is not None:
        accounting = 'inconsistent: stage sum exceeds request time; no percentages assigned'
    for row in stages:
        row['request_percent'] = (100 * row['seconds'] / total if total and row['seconds'] is not None
                                  and measured <= total + .01 else None)
    ranked = sorted((r for r in stages if r['seconds'] is not None), key=lambda r: r['seconds'], reverse=True)
    config = mapping(engine.get('config')) or mapping(profile.get('engine'))
    offload = mapping(engine.get('offload'))
    sampling_memory = mapping(engine.get('sampling_memory'))
    decoder_read_ahead = mapping(engine.get('decoder_read_ahead'))
    hardware = mapping(report.get('runtime_hardware')) or mapping(policy.get('hardware'))
    steps = engine.get('step_seconds', [])
    steps = steps if isinstance(steps, list) and all(number(x) is not None for x in steps) else []
    kernels = kernel_receipt(engine)
    hints = []
    if (mapping(engine.get('config')).get('resident_blocks') == 50
            and number(offload.get('transfers')) == 0 and number(offload.get('h2d_bytes')) == 0):
        hints.append(dict(kind='measured', message='All 50 transformer blocks are configured on GPU and recorded weight transfers are zero. '
            'A larger host weight cache cannot remove transfers from this placement. This does not prove full GPU residency is the fastest placement.',
            message_zh='全部 50 个模型块配置为 GPU 常驻，记录到的权重传输为零；增大 RAM 权重缓存不能再减少这条路径的传输，但这不证明全驻留是最快的放置方式。'))
    if ranked:
        hints.append(dict(kind='measured', message='Largest measured wall-time category: ' + ranked[0]['stage'],
                          message_zh='耗时最大的已记录类别：' + ranked[0]['label_zh']))
    if len(steps) > 1 and statistics.median(steps[1:]) > 0 and steps[0] > 2 * statistics.median(steps[1:]):
        hints.append(dict(kind='needs_investigation', message='First sampling step exceeds twice the later-step median. '
            'Check compilation, cache preparation and transfers; these timers do not identify the cause.',
            message_zh='第一步耗时超过后续步骤中位数的两倍；需检查编译、缓存准备及传输，不能仅据此认定是编译。'))
    if config.get('attention_cpu_outputs') or config.get('residual_offload'):
        hints.append(dict(kind='configuration', message='CPU attention/residual staging is active. Check live VRAM and '
            'policy decisions before attributing slow sampling to GPU compute.',
            message_zh='已启用 attention 输出或残差的 CPU 暂存；需结合当时剩余显存及策略理由判断采样变慢原因。'))
    reserved = number(engine.get('torch_peak_reserved_bytes'))
    capacity = number(hardware.get('vram_total'))
    if hardware.get('system') == 'Windows' and reserved and capacity and reserved > capacity:
        hints.append(dict(kind='needs_investigation', message='CUDA allocator peak reservation exceeds physical VRAM. '
            'Check Windows shared GPU memory/system-memory fallback and allocator fragmentation. '
            'Reservation alone does not prove paging caused the slowdown.',
            message_zh='CUDA 分配器保留峰值超过物理显存；需检查 Windows 共享 GPU 内存／系统内存回退和分配碎片。仅凭 reserved 不能确认变慢由换页造成。'))
    memory_steps = sampling_memory.get('steps') or []
    windows_observations = [mapping(row).get('windows') for row in memory_steps] if isinstance(memory_steps, list) else []
    windows_observations.append(sampling_memory.get('incomplete_step_windows'))
    if any((number(mapping(mapping(row).get('local')).get('over_budget_samples')) or 0) > 0 for row in windows_observations):
        hints.append(dict(kind='measured', message='This process exceeded its Windows local video-memory budget during sampling. '
            'Compare the per-step nonlocal memory and latency; this is pressure evidence, not a measured PCIe paging rate.',
            message_zh='采样时进程超过了 Windows 分配的本地显存预算。请对照每步共享内存与耗时；这是压力证据，不是 PCIe 换页速率测量。'))
    attempts = report.get('resource_attempts', [])
    failures = [dict(state=r.get('state'), failure=r.get('failure')) for r in attempts
                if isinstance(r, dict) and r.get('failure')] if isinstance(attempts, list) else []
    if failures:
        if sampling_reused:
            hints.append(dict(kind='measured', message='Request includes %d failed attempt(s). Sampling timings describe the '
                'completed sampling reused by the decoder retry; the final attempt did not run sampling again.' % len(failures),
                message_zh='本次请求包含 %d 次失败尝试。采样计时来自本次请求已完成的采样；最终尝试复用其结果，只重试解码。' % len(failures)))
        else:
            hints.append(dict(kind='measured', message='Request includes %d failed attempt(s); their cost is outside the final successful sampling timer.' % len(failures),
                message_zh='本次请求包含 %d 次失败尝试，其开销未计入最终成功的采样计时。' % len(failures)))
    def budget(key, fallback):
        measured = number(policy.get(key))
        planned = number(profile.get(fallback))
        return measured if measured is not None else planned*1e9 if planned is not None else None
    return dict(status='complete' if report.get('success') is True else 'incomplete',
        request_seconds=total, accounting=accounting, stages=stages, findings=hints,
        kernels=kernels or None,
        largest_category=ranked[0]['stage'] if ranked else None,
        step_seconds=steps, completed_nfe=len(steps),
        sampling_plan=engine.get('sampling_plan') or report.get('sampling_plan'),
        sampling_passes=engine.get('sampling_passes'), latent_upscale=engine.get('latent_upscale'),
        sampling_reused=sampling_reused,
        sampling_memory=sampling_memory or None,
        decoder_read_ahead={key: decoder_read_ahead[key] for key in (
            'state', 'read_bytes', 'total_bytes', 'allowance_bytes',
            'elapsed_seconds', 'gpu_allocation_bytes', 'private_buffer_bytes',
            'sampling') if key in decoder_read_ahead} or None,
        device_memory=mapping(engine.get('device_memory')) or None,
        seconds_per_completed_nfe=(sum(steps)/len(steps) if steps else None),
        conditioning_cache_hit=cache_hit, preencoded=preencoded,
        geometry=report.get('geometry') or engine.get('geometry'),
        task=config.get('task'), effective_engine_config=config,
        config_scope='executed' if engine.get('config') else 'planned; executed config unavailable',
        weight_placement=dict(resident_blocks=config.get('resident_blocks'), pin_host_gb=config.get('pin_host_gb'),
            pinned_model_bytes=config.get('pinned_model_bytes'),
            pinned_host_allocated_bytes=config.get('pinned_host_allocated_bytes'),
            pinned_layer_count=offload.get('pinned_layer_count'), streamed_layer_count=offload.get('streamed_layer_count'),
            pinned_buffer_bytes=offload.get('pinned_buffer_bytes'), pageable_buffer_bytes=offload.get('pageable_buffer_bytes'),
            cuda_buffer_bytes=offload.get('cuda_buffer_bytes'),
            host_buffer_wait_seconds=offload.get('host_buffer_wait_seconds'),
            prefetch_wait_seconds=offload.get('prefetch_wait_seconds'),
            stream_weights=config.get('stream_weights'), transfers=offload.get('transfers'),
            h2d_bytes=offload.get('h2d_bytes'), host_stage_seconds=offload.get('host_stage_seconds'),
            direct_read_layers=offload.get('direct_read_layers'), direct_read_bytes=offload.get('direct_read_bytes'),
            host_prefetch=offload.get('host_prefetch'), host_prefetch_reads=offload.get('host_prefetch_reads'),
            host_prefetch_wait_seconds=offload.get('host_prefetch_wait_seconds'),
            host_prefetch_buffer_bytes=offload.get('host_prefetch_buffer_bytes'),
            host_prefetch_disabled_reason=offload.get('host_prefetch_disabled_reason'),
            h2d_seconds=offload.get('h2d_seconds'), timing_scope='Transfer/wait timers may overlap; do not add to wall time.',
            pinning_scope='pin_host_gb is requested decimal GB. pinned_model_bytes is actual weight payload; '
                          'pinned_host_allocated_bytes includes rounded active and cached host allocations at load. '
                          'Per-step host counters describe the process during sampling.'),
        decoder_options=profile.get('decoder'),
        hardware=hardware, failed_attempts=failures,
        budget_bytes=dict(gpu=budget('gpu_budget_bytes', 'gpu_budget_gb'),
                          ram=budget('ram_budget_bytes', 'inference_ram_budget_gb')),
        observed_memory=dict(
            sampling_allocated_bytes=engine.get('torch_peak_allocated_bytes'), sampling_reserved_bytes=reserved,
            final_stage_allocated_bytes=engine.get('final_stage_peak_allocated_bytes'),
            final_stage_reserved_bytes=engine.get('final_stage_peak_reserved_bytes'),
            whole_gpu_peak_bytes=mapping(mapping(report.get('resources')).get('gpu')).get('gpu_peak_bytes'),
            video_ram=mapping(mapping(report.get('resources')).get('ram')),
            encoding_ram=None if cache_hit or preencoded else mapping(mapping(report.get('encoding_resources')).get('ram'))),
        encoder_metrics_scope='cached receipt from a previous request; excluded from current timing' if cache_hit else 'this request',
        compile_seconds=None,
        compile_scope=(sampling_memory.get('compiler_scope') or
                       'Compilation is not timed separately; never infer it from a slow first step.'))


def write_retry(output, report, *, index, retained, next_profile=None, decision=None):
    """Save each failed attempt before its replacement starts.

    A separate file keeps the failure evidence alongside the final request's
    complete timings and the cost of every failed attempt.
    """
    if type(index) is not int or not 1 <= index <= 3 or not isinstance(report, dict):
        return None
    rows = report.get('resource_attempts')
    if not isinstance(rows, list) or index > len(rows) or not isinstance(rows[index - 1], dict):
        return None
    request = dict(report, success=False)
    rows = [dict(row) if isinstance(row, dict) else row for row in rows[:index]]
    failed = rows[-1]
    previous = mapping(failed.get('recovery'))
    failed['recovery'] = dict(previous,
        next_profile=mapping(next_profile) or mapping(previous.get('next_profile')),
        decision=mapping(decision))
    request['resource_attempts'] = rows
    request['profile'] = mapping(failed.get('profile')) or mapping(report.get('profile'))
    request['resources'] = mapping(failed.get('resources'))
    request.pop('video', None)  # Read the failed worker's retained sidecars.
    return write(output, request, _retry=dict(stage='video', index=index, retained=retained))


def write_encoder_retry(output, encoding, *, index):
    """Save encoder OOM evidence while the failed attempt is still resident."""
    if type(index) is not int or not 1 <= index <= 3 or not isinstance(encoding, dict):
        return None
    return write(output, _retry=dict(stage='encoding', index=index, encoding=encoding))


def write(output, report=None, bridge=None, *, _retry=None, live=False):
    """Bounded JSON/log reads only. Failure to write must not fail generation."""
    output = Path(output)
    notes = []
    def read_json(suffix, path=None):
        path = path if path is not None else output.with_suffix(suffix)
        try:
            if is_link(path):
                if path.exists():
                    notes.append(suffix + ': linked file omitted')
                return {}
            raw, truncated = read_bounded(path, 2 * 2**20)
            if truncated:
                notes.append(suffix + ': exceeds report limit')
                return {}
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('Report must be an object')
            return value
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            notes.append(suffix + ': unreadable or invalid')
            return {}
    def sampling_sidecar(metrics, artifact_output):
        # A terminated worker may never return Engine.sample() or finalize its
        # metrics, although SamplingMemory persisted every completed step.
        observed = read_json('.sampling-memory.json', artifact_output.with_suffix('.sampling-memory.json'))
        current = mapping(metrics.get('sampling_memory'))
        def count(value):
            rows = value.get('steps')
            return len(rows) if isinstance(rows, list) else 0
        if observed and (not current or count(observed) > count(current)):
            return dict(metrics, sampling_memory=observed)
        return metrics
    try:
        request = report if isinstance(report, dict) else read_json('.request.json')
        video_retry = _retry and _retry['stage'] == 'video'
        encoder_retry = _retry and _retry['stage'] == 'encoding'
        engine_output = Path(_retry['retained']) / output.name if video_retry else output
        engine = mapping(request.get('video')) or read_json('.engine.json', engine_output.with_suffix('.engine.json'))
        engine = sampling_sidecar(engine, engine_output)
        encoding = mapping(request.get('encoding')) or read_json('.encoding.json')
        if encoder_retry:
            request = dict(request, success=False, phase='encoder_oom')
            engine = {}  # A reused CLI output path may hold an older video's sidecars.
            encoding = dict(_retry['encoding'])
            encoding['diagnostic_retry'] = dict(index=_retry['index'], final_attempt=_retry['index'] >= 3)
            attempts = encoding.get('encoder_attempts')
            latest = mapping(attempts[-1]) if isinstance(attempts, list) and attempts else {}
            stages = encoding.get('load_stages')
            last_stage = mapping(stages[-1]) if isinstance(stages, list) and stages else {}
            request['encoding_failure'] = dict(phase='encoder_oom', failure=dict(kind='gpu_oom',
                exception=latest.get('exception', []), gpu=latest or mapping(encoding.get('gpu')),
                memory=mapping(last_stage.get('memory'))))
        bridge = mapping(bridge) or read_json('comfy-request', output.parent / 'comfy-request.json')
        redactor = Redactor([(str(output.parent), '<RUN>')])
        try:
            redactor.replacements.append((str(Path.home()), '<HOME>'))
        except (OSError, RuntimeError):
            pass
        include_logs = True
        # The bridge prompt exists before the worker creates its artifacts.
        # Live exports during startup must redact it too.
        for prompt in (output.parent / 'prompt.txt', output.with_suffix('.artifacts') / 'prompt.txt'):
            if not prompt.exists():
                continue
            try:
                if is_link(prompt) or is_link(prompt.parent):
                    raise ValueError('Linked prompt omitted')
                raw, truncated = read_bounded(prompt, 64*1024)
                if truncated:
                    raise ValueError('Prompt exceeds redaction limit')
                text = raw.decode('utf-8', errors='replace').strip()
                if text:
                    redactor.prompts.add(text)
            except (OSError, ValueError):
                include_logs = False
                notes.append('Log text omitted: prompt could not be read safely for redaction')
        redactor.structured(request)  # Learn prompts before redacting repeated error text.
        redactor.structured(bridge)
        # Failed children never return their telemetry into request['resources'].
        # Their retained memory files must feed the summary, not just an unused
        # raw section of the local report.
        request = dict(request)
        attempts = request.get('resource_attempts')
        if isinstance(attempts, list) and not encoder_retry:
            from .diagnostic_resources import attempt_metrics
            restored = []
            for index, row in enumerate(attempts, 1):
                if not isinstance(row, dict):
                    restored.append(row)
                    continue
                row = dict(row)
                retained = row.get('retained')
                if video_retry and index == _retry['index']:
                    artifact_output = engine_output
                elif isinstance(retained, str) and retained:
                    artifact_output = Path(retained) / output.name
                elif index == len(attempts) and row.get('failure'):
                    artifact_output = output
                else:
                    restored.append(row)
                    continue
                metrics = mapping(row.get('metrics'))
                observed = sampling_sidecar(metrics, artifact_output)
                if observed is not metrics:
                    row['metrics'] = dict(metrics, **attempt_metrics(observed))
                restored.append(row)
            request['resource_attempts'] = restored
        if encoder_retry:
            request['resources'] = {}
        elif not request.get('resources'):
            request['resources'] = dict(ram=read_json('.engine.memory.json', engine_output.with_suffix('.engine.memory.json')),
                                       gpu=read_json('.engine.gpu.json', engine_output.with_suffix('.engine.gpu.json')))
        if not request.get('encoding_resources'):
            request['encoding_resources'] = dict(ram=read_json('.encoding.memory.json'))
        summary = summarize(request, engine, encoding)
        if live and bridge.get('status') in ('starting', 'running'):
            summary['status'] = 'running'
        if video_retry or encoder_retry and _retry['index'] < 3:
            summary['status'] = 'retrying'
        payload = dict(schema_version=1, summary=summary,
            runtime_code=request.get('runtime_code'), collector_version=__version__,
            request={k:request[k] for k in ('seed', 'encoder_mode', 'profile', 'resource_planning', 'compatibility',
                'reclaimable_resident_models', 'idle_resources', 'tuning', 'input_cache', 'resource_attempts',
                'resource_prediction', 'resource_prediction_error', 'error', 'error_message', 'error_type',
                'resource_error', 'exception', 'encoding_failure', 'phase') if k in request},
            engine=engine, encoding=encoding,
            memory=dict(video=request.get('resources'),
                        encoding=request.get('encoding_resources') or dict(ram=read_json('.encoding.memory.json')),
                        scope='Keep working RAM, PSS/RSS, commit and whole-GPU/allocator observations separate. '
                              'Cached encoder receipts do not describe this request’s peak.'),
            bridge={k:bridge[k] for k in ('status', 'error', 'bridge_seconds', 'encoder_prewarm') if k in bridge},
            collection_notes=notes, log_tails={})
        # Scrub structured prompts before processing log tails that may repeat them.
        payload = redactor.structured(payload)
        if live:
            payload['snapshot'] = dict(collected_at=time.time(), running=summary['status'] == 'running')
        # The generic secret redactor matches "token_summary" as a token key.
        # Restore only its fixed numeric counts, never token IDs or text.
        from .diagnostic_resources import token_summary
        payload['encoding']['token_summary'] = token_summary(encoding.get('token_summary'))
        for suffix in ('.engine.log', '.encoding.log', '.lora.log'):
            path = (engine_output if suffix == '.engine.log' else output).with_suffix(suffix)
            if include_logs and path.is_file() and not is_link(path):
                raw, truncated = read_bounded(path, 8192)
                payload['log_tails'][suffix] = redactor.text(raw.decode('utf-8', errors='replace'))
        if bridge and include_logs:
            path = output.parent / 'generate.log'
            if path.is_file() and not is_link(path):
                raw, _ = read_bounded(path, 8192)
                payload['log_tails']['generate.log'] = redactor.text(raw.decode('utf-8', errors='replace'))
        def clean(value, key=''):
            if isinstance(value, dict):
                return {k:clean(v, k) for k,v in value.items()}
            if isinstance(value, list):
                return [clean(v, key) for v in value]
            if isinstance(value, str):
                # Keep a useful relative name (for example ``<RUN>/video.mp4``
                # or ``models/cache.safetensors``) while removing drive
                # letters, usernames, mounts and unrelated parent folders.
                if redactor._path_key(key):
                    return redactor.path(value)
                return redactor.text(value)
            return value
        suffix = '.live.debug.json' if live else '.debug.json'
        if _retry:
            suffix = ('.encoder-retry-' if encoder_retry else '.retry-') + str(_retry['index']) + '.debug.json'
        target = output.with_suffix(suffix)
        cleaned = clean(payload)
        from .diagnostic_summary import summary_report
        cleaned['analysis'] = summary_report(cleaned)
        save(target, cleaned)
        return target
    except Exception:
        # Diagnostics must not turn a saved video into a failed request, nor
        # replace an original OOM/error with a secondary reporting exception.
        return None
