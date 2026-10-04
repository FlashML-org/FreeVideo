"""Torch-free attempt admission, recovery and complete-request observations.

Placement can be retried without changing arithmetic. Chunk changes are search
candidates and need local full-request equivalence, not an OOM exception waiver.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import time

from .paths import data_root


class WorkerExit(RuntimeError):
    def __init__(self, code, log):
        self.returncode, self.log = code, Path(log)
        super().__init__('Worker exited %s; see %s\n%s' %
                         (code, log, self.log.read_text(encoding='utf-8', errors='replace')[-4000:]))


def cuda_runtime_error(error):
    """A CUDA runtime error other than an allocation failure anywhere in the
    exception chain, whether or not it is a known sticky error."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        text = str(error).lower()
        if 'cuda error' in text and 'out of memory' not in text:
            return True
        error = error.__cause__ or error.__context__
    return False


def classify_failure(error, metrics=None):
    """Only positively identified resource failures are retriable.

    A GPU error may be the first synchronization point, not the fault origin.
    An unexplained native exit is not reclassified using post-reboot health.
    """
    metrics = metrics if isinstance(metrics, dict) else {}
    details = metrics.get('failure', {})
    details = details if isinstance(details, dict) else {}
    cleanup = metrics.get('cleanup_errors', [])
    cleanup = list(cleanup) if isinstance(cleanup, list) else [cleanup]
    attached = getattr(error, 'cleanup_errors', [])
    cleanup += attached if isinstance(attached, list) else [attached]
    text = '\n'.join([str(error), str(metrics.get('error', ''))] + [str(item) for item in cleanup]).lower()
    if any(part in text for part in ('cuda error: unknown error', 'device is lost', 'device lost',
            'gpu is lost', 'illegal memory access', 'device-side assert', 'unspecified launch failure',
            'driver shutting down', 'device has been removed', 'cuda error: launch timeout',
            'the launch timed out', 'cudaerrorillegaladdress', 'cudaerrorunknown')) or details.get('kind') == 'cuda_error':
        kind, outcome, retry = 'cuda_error', 'discarded', False
    elif getattr(error, 'worker_stop_confirmed', None) is False:
        kind, outcome, retry = 'worker_stop_unconfirmed', 'discarded', False
    elif isinstance(error, KeyboardInterrupt):
        kind, outcome, retry = 'cancelled', 'cancelled', False
    elif isinstance(error, subprocess.TimeoutExpired):
        kind, outcome, retry = 'timeout', 'cancelled', False
    elif details.get('kind') == 'gpu_oom' or 'cuda out of memory' in text or 'cuda error: out of memory' in text:
        kind, outcome, retry = 'gpu_oom', 'resource_failure', True
    elif isinstance(error, MemoryError) or details.get('kind') == 'ram_pressure':
        kind, outcome, retry = 'ram_pressure', 'resource_failure', True
    elif isinstance(error, WorkerExit) and not (metrics or {}).get('error'):
        kind, outcome, retry = 'unknown_worker_exit', 'discarded', False
    else:
        kind, outcome, retry = 'code_error', 'code_failure', False
    result = dict(kind=kind, outcome=outcome, retryable=retry, reason=str(error))
    allocation = getattr(error, 'freevideo_allocation', details.get('allocation'))
    if isinstance(allocation, dict) and type(allocation.get('fp8_ff_activation_stash_bytes')) is int and allocation['fp8_ff_activation_stash_bytes'] > 0:
        result['allocation'] = {'fp8_ff_activation_stash_bytes': allocation['fp8_ff_activation_stash_bytes']}
    from .diagnostic_resources import exception_details
    captured = exception_details(error)
    result['exception'] = details.get('exception') or captured
    if details.get('exception') and captured:
        result['controller_exception'] = captured
    # Preserve the worker's failure-time evidence when the controller wraps its
    # exit; the controller's CUDA context cannot reconstruct these readings.
    for key in ('gpu', 'memory', 'memory_collection_error'):
        if key in details:
            result[key] = details[key]
    device_memory = metrics.get('device_memory', details.get('device_memory'))
    if kind == 'gpu_oom' and isinstance(device_memory, dict):
        result['device_memory'] = device_memory
    telemetry = getattr(error, 'telemetry', None)
    if kind == 'ram_pressure' and isinstance(telemetry, dict) and isinstance(telemetry.get('ram_guard'), dict):
        # Preserve the actual monitor decision; recovery must not parse rounded
        # GiB values out of an exception or mistake commit/RSS for working RAM.
        result['ram_guard'] = {key: telemetry['ram_guard'].get(key) for key in
            ('reasons', 'working_bytes', 'budget_bytes', 'available_bytes', 'emergency_floor_bytes',
             'physical_available_bytes', 'commit_available_bytes') if key in telemetry['ram_guard']}
    if cleanup:
        result['cleanup_evidence'] = [str(item) for item in cleanup]
    if getattr(error, 'worker_stop_confirmed', None) is False:
        result['worker_stop_error'] = getattr(error, 'worker_stop_error', 'Worker exit could not be confirmed')
    return result


def history_path():
    return data_root() / 'resource-history.sqlite3'


def benchmark_allocator_limit(profile, explicit_gib, hardware):
    """Resolve the actually enforced optional cap before any model is loaded.

    An explicit CLI value overrides a saved profile. Profile byte values stay
    integers; silently rounding malformed metadata could fingerprint a cap that
    the worker does not enforce. This helper performs no device calls.
    """
    if explicit_gib is not None:
        if (type(explicit_gib) not in (int, float) or not math.isfinite(explicit_gib)
                or explicit_gib <= 0):
            raise ValueError('--allocator-limit-gib must be positive and finite')
        requested = explicit_gib * 2**30
        if not math.isfinite(requested):
            raise ValueError('--allocator-limit-gib exceeds representable memory capacity')
        value = round(requested)
    else:
        value = profile.get('benchmark_allocator_limit_bytes')
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError('Benchmark allocator limit must be positive integer bytes')
    observed = hardware.to_dict() if hasattr(hardware, 'to_dict') else hardware
    total = observed.get('vram_total')
    if type(total) not in (int, float) or not math.isfinite(total) or total <= 0:
        raise ValueError('Detected physical VRAM is required for an explicit allocator cap')
    if value > total:
        raise ValueError('Benchmark allocator limit exceeds detected physical VRAM')
    return value


def demonstrated_ram_bytes(history, identity, canvas):
    """Largest RAM peak a complete verified request reached on this machine.

    Only the weight cache may use this, and only because it is a measurement
    of this machine rather than an assumption about it. Every fence is already
    enforced elsewhere and relied on here:

    * ``observations`` filters ``state='success'`` and exact-matches the whole
      software identity -- GPU UUID and driver, system, capability, package
      versions, kernel overlay, model manifest, and the digest of all 34
      compute source files. A different build or device cannot contribute.
    * ``complete_observation`` is the only writer, and it refuses anything
      that is not a finished sampling plan with matching geometry and
      verified retained frames and audio: "A probe or incomplete sample
      cannot become a resource observation."
    * The geometry is matched here as well, because a RAM peak scales with
      the request.
    * The recorded RAM metric must match the one this platform reports now.
      Windows measures a private working set and Linux a guard PSS; a peak
      read under one metric says nothing about capacity under the other, and
      'unknown' says nothing at all.

    Returns None when this machine has no such evidence, which leaves the
    plan exactly as it would have been.
    """
    if history is None or identity is None or canvas is None:
        return None
    keys = ('width', 'height', 'frames', 'fps')
    try:
        from .ram import ProcessMemory
        metric = ProcessMemory().sample(os.getpid()).get('ram_guard_metric')
        rows = history.observations(identity=identity, limit=512, include_details=False)
    except Exception:
        # Planning must not fail because the local history or a live sample is
        # unreadable; it falls back to the reported availability.
        return None
    if not metric or metric == 'unknown':
        return None
    peaks = []
    for row in rows:
        geometry = row.get('geometry') or {}
        if any(geometry.get(key) != canvas.get(key) for key in keys):
            continue
        observation = row.get('observation') or {}
        if observation.get('ram_metric') != metric:
            continue
        peak = observation.get('ram_peak_bytes')
        if type(peak) is int and peak > 0:
            peaks.append(peak)
    return max(peaks) if peaks else None


def demonstrated_local_ram(hardware, args, canvas):
    """Best-effort wrapper: never let this reduce a plan to an exception.

    Planning has to work on a machine with no history, no readable history, no
    prepared cache and no stable GPU UUID. Every one of those simply means
    there is no evidence, which is the same as a first run.
    """
    cache = getattr(args, 'cache', None)
    if cache is None:
        return None
    try:
        from .resource_history import ResourceHistory
        return demonstrated_ram_bytes(ResourceHistory(history_path()),
                                      local_identity(hardware, cache), canvas)
    except Exception:
        return None


def local_identity(hardware, cache):
    """Exact software identity for comparable performance measurements."""
    from .tuning import COMPUTE_FILES, digest, package_versions
    from .kernel_capabilities import identity as kernel_identity
    observed = hardware.to_dict()
    uuid = observed.get('gpu_uuid')
    if not uuid:
        visible = os.environ.get('CUDA_VISIBLE_DEVICES', '').split(',')[0]
        if visible.startswith('GPU-'):
            uuid = visible
    if not uuid:
        raise ValueError('A stable GPU UUID is required to retain local failure history.')
    package = Path(__file__).parent
    kernels = kernel_identity(hardware)
    return dict(gpu={'uuid': uuid, 'name': observed['gpu_name'],
                     'driver_version': observed.get('driver_version', '')},
                system=observed.get('system', platform.system()), capability=list(observed['capability']),
                identity_schema=1, dependency_manifest_sha256=digest(package/'dependencies.json'),
                manifest_sha256=digest(Path(cache)/'manifest.json'),
                engine_packages=package_versions(),
                kernel_packages=kernels['packages'], fa4_overlay_sha256=kernels['fa4_overlay_sha256'],
                source_sha256={name: digest(package/name) for name in COMPUTE_FILES})


def attempt_geometry(canvas, engine, text_tokens=None):
    value = dict(canvas, steps=engine.get('steps', 8), task=engine.get('task', 't2va'))
    if text_tokens is not None:
        value['text_tokens'] = text_tokens
    return value


def reduced_pinning(profile, failure, phase, previous_ram_retries):
    """One measured partial reduction before the minimal-memory fallback."""
    guard = failure.get('ram_guard')
    if previous_ram_retries or phase not in ('load', 'sample') or not isinstance(guard, dict):
        return None
    if guard.get('reasons') != ['working_memory_budget']:
        return None
    keys = ('working_bytes', 'budget_bytes', 'available_bytes', 'emergency_floor_bytes')
    if any(type(guard.get(k)) not in (int, float) or not math.isfinite(guard[k]) or guard[k] < 0 for k in keys):
        return None
    engine = profile['engine']
    pin = engine.get('pin_host_gb', 0.)
    if type(pin) not in (int, float) or not math.isfinite(pin) or pin <= 0:
        return None
    pin *= 1e9
    deficit = guard['working_bytes'] - guard['budget_bytes']
    if (deficit <= 0 or deficit > .25 * guard['budget_bytes']
            or guard['available_bytes'] < guard['emergency_floor_bytes'] + 2 * 2**30):
        return None
    # Leave useful weights resident in host memory; do not jump from a 0.11
    # GiB overflow to zero pinned weights on a 56 GiB Windows host. A complete
    # layer is the unit of pinning. This is a retry candidate, not a fit proof.
    from .policy import BLOCK_BYTES
    release = math.ceil(max(2 * 2**30, deficit + 2**30, .15 * pin) / BLOCK_BYTES) * BLOCK_BYTES
    remaining = max(0, pin - release)
    if remaining < 2 * BLOCK_BYTES:
        return None
    return remaining / 1e9, dict(mode='partial-pinning-reduction', observed_overflow_bytes=deficit,
        planned_release_bytes=release, retained_pin_budget_bytes=remaining,
        reason='Only the working-memory budget was exceeded and system headroom remains. '
               'Retain host caches with a smaller pin budget; a second RAM failure uses the bounded streaming fallback.')


def nonlocal_exhausted(failure):
    """A CUDA out-of-memory raised while the WDDM non-local budget, not VRAM, was full.

    Page-locked host memory counts against that budget. A 20 s request failed
    with non-local usage at 13.14 of 13.24 GiB, 10.68 GiB of VRAM free and no
    caching-allocator OOM; the failed allocation itself is not in the usage.
    """
    gpu = failure.get('gpu') if isinstance(failure.get('gpu'), dict) else {}
    values = [gpu.get(key) for key in ('nonlocal_budget_bytes', 'nonlocal_usage_bytes',
                                       'local_budget_bytes', 'local_usage_bytes')]
    if not all(type(value) is int and value >= 0 for value in values) or not values[0]:
        return False
    budget, usage, local_budget, local_usage = values
    return (usage >= budget - 2**30 and local_usage <= local_budget - 2 * 2**30
            and not gpu.get('allocator_ooms'))


# Compute partitions a sampling OOM may shrink once placement is exhausted.
# Smaller slices change floating-point reduction order, never the requested
# video: geometry, frames, steps, seed, precision and attention backend stay.
# A 6 GiB RTX 3060 Laptop (2026.10.4.15209) completed the 288x480 first pass of
# a 544x960 two-pass request, then the 544x960 second pass ran out of memory
# in the VDN linear branch with zero resident blocks; with no smaller
# placement left the whole 13-minute request failed. Those branch states scale
# with frames x heads x head_dim^2, not with tokens, so a smaller canvas does
# not shrink them, while a smaller head group does.
COMPUTE_RECOVERY = (
    # Keep attention outputs on the GPU first: host outputs cost 19-36% of
    # sampling time in the small band. FF and projection slices take the
    # small-card values.
    dict(head_chunk=2, ff_chunk=512, projection_chunk=512, window_batch=1, head_parallelism=1),
    # The smallest bounded path: one head at a time, attention outputs in
    # grouped host buffers and the residual stream staged in host memory.
    dict(head_chunk=1, ff_chunk=512, projection_chunk=512, window_batch=1, head_parallelism=1,
         attention_cpu_outputs=True, grouped_attention_outputs=True, residual_offload=True),
)
COMPUTE_RECOVERY_KEYS = ('head_chunk', 'ff_chunk', 'projection_chunk', 'window_batch', 'head_parallelism',
                         'attention_cpu_outputs', 'grouped_attention_outputs', 'residual_offload')
# Absent options run with these values; a chunk of 0 is unchunked, the widest.
COMPUTE_DEFAULTS = dict(window_batch=1, head_parallelism=1)


def compute_recovery(engine):
    """The next smaller compute partition for the bounded inference path, or None.

    Integers only shrink and flags only switch on, so recovery never widens a
    partition that already failed.
    """
    if engine.get('inference_kernels') is not True or type(engine.get('head_chunk')) is not int or engine['head_chunk'] <= 0:
        return None
    for rung in COMPUTE_RECOVERY:
        updates = {}
        for name, target in rung.items():
            current = engine.get(name, COMPUTE_DEFAULTS.get(name))
            if type(target) is bool:
                if current is not True:
                    updates[name] = True
            elif type(current) is not int or current <= 0 or current > target:
                updates[name] = target
        if updates:
            return updates
    return None


def recovered_compute(profile, history, identity, canvas):
    """Start with the compute partitions this machine needed for the same request.

    Only a verified complete request counts whose attempt was a
    compute-partition recovery, with the identical software identity and
    geometry, so a request that failed once does not fail first every time.
    The partitions only shrink, and only while the budget is not more than
    0.5 GB above the recovered one; every other plan stays exactly as chosen.
    """
    decision = dict(applied=False)
    engine = profile.get('engine') or {}
    if history is None or identity is None or canvas is None or compute_recovery(engine) is None:
        return profile, decision
    try:
        rows = history.observations(identity=identity, geometry=attempt_geometry(canvas, engine),
                                    limit=16, include_details=True)
    except Exception:
        return profile, decision  # Unreadable history is the same as none.
    for row in reversed(rows):
        evidence = ((row.get('details') or {}).get('decision_evidence') or {})
        config = row.get('config') or {}
        observed = config.get('engine') or {}
        budget = config.get('gpu_budget_gb')
        if (evidence.get('numerical_class') != 'compute-partition' or type(budget) not in (int, float)
                or profile.get('gpu_budget_gb', 0) > budget + .5):
            continue
        updates = {}
        for name in COMPUTE_RECOVERY_KEYS:
            value, current = observed.get(name), engine.get(name, COMPUTE_DEFAULTS.get(name))
            if type(value) is bool:
                if value and current is not True:
                    updates[name] = True
            elif type(value) is int and value > 0 and (type(current) is not int or current <= 0 or value < current):
                updates[name] = value
        if not updates:
            return profile, decision
        value = copy.deepcopy(profile)
        value['engine'].update(updates)
        if isinstance(value.get('policy'), dict):
            value['policy']['engine'] = dict(value['engine'])
        return value, dict(applied=True, source_attempt=row.get('id'), changes=updates,
                           numerical_class='compute-partition',
                           reason='A complete request with this geometry needed these partitions on this machine.')
    return profile, decision


def next_placement(profile, failure, phase, canvas=None, *, previous_ram_retries=0, previous_gpu_retries=0,
                   sampling_complete=False):
    """Bounded recovery after a resource failure; never change the request.

    Free activation space in one useful step instead of spending many full
    requests lowering residency two blocks at a time. Placement comes first.
    When a sampling OOM leaves no smaller placement, shrink compute partitions
    (``COMPUTE_RECOVERY``): a slightly different rounding is better than no
    video. This is recovery only; success does not declare the fallback faster
    than the initial candidate.
    """
    value = copy.deepcopy(profile)
    engine, decoder = value['engine'], value['decoder']
    changes = {}
    resume_finalization = phase == 'sample_finalize' and sampling_complete
    ram_recovery = None
    allocation = failure.get('allocation')
    stash_bytes = allocation.get('fp8_ff_activation_stash_bytes') if isinstance(allocation, dict) else None
    recompute_recovery = (failure['kind'] == 'gpu_oom' and phase == 'sample'
        and type(stash_bytes) is int and stash_bytes > 0
        and engine.get('inference_kernels') and engine.get('ff_chunk', 0) > 0
        and engine.get('linear_compute', 'native-fp8') == 'native-fp8' and not engine.get('fp8_ff_recompute'))
    def change(section, name, new):
        options = engine if section == 'engine' else decoder
        if options.get(name) != new:
            changes[section+'.'+name] = {'from': options.get(name), 'to': new}
            options[name] = new
    host_pins = failure['kind'] == 'gpu_oom' and nonlocal_exhausted(failure)
    compute = None
    if failure['kind'] == 'gpu_oom':
        if host_pins and phase in ('load', 'sample') and (engine.get('pin_host_gb', 0.) > 0 or engine.get('pin_host_weights')):
            # Page-locked memory ran out, not VRAM: return pinned weights and
            # keep every GPU setting. Halve once, then pin nothing.
            change('engine', 'pin_host_gb', 0. if previous_gpu_retries else round(engine.get('pin_host_gb', 0.) / 2, 3))
            for name in ('pin_host_weights', 'preload_host'):
                if engine.get(name):
                    change('engine', name, False)
        elif recompute_recovery:
            # Keep the up/down GEMM row shapes, FP8 global scale and requested
            # output. Only regenerate identical FF tiles instead of stashing
            # the full intermediate matrix which actually failed to allocate.
            change('engine', 'fp8_ff_recompute', True)
        elif phase == 'decode' or resume_finalization:
            if host_pins:
                change('decoder', 'pin_weights', False)
            change('decoder', 'offload', True)
            change('decoder', 'prefetch', False)
            change('decoder', 'stream_output', True)
            if decoder.get('resident_blocks', 0):
                change('decoder', 'resident_blocks', 0)
            if value['inference_ram_budget_gb'] * 1e9 < 14 * 2**30:
                change('decoder', 'stream_weights', True)
                change('decoder', 'pin_weights', False)
                change('decoder', 'preload', False)
        else:
            if engine.get('head_parallelism', 1) > 1:
                # First remove extra simultaneous workspaces. Keep all GPU
                # weights and the original arithmetic partitions on this retry.
                change('engine', 'head_parallelism', 1)
            else:
                memory = failure.get('device_memory', {})
                limit = memory.get('effective_allocator_limit_bytes')
                resident = engine.get('resident_blocks', 0)
                from .policy import BLOCK_BYTES
                partial = (not previous_gpu_retries and memory.get('windows_allocator_limit_enforced') is True
                           and type(limit) is int and limit > 0 and type(resident) is int and resident > 0)
                if partial:
                    # The first allocator OOM frees a useful workspace, not all
                    # 50 weights. Full residency gains transfer slots when it is
                    # reduced: budget those too. This is a measured-failure
                    # recovery candidate, not a claim about the optimal count.
                    blocks = math.ceil(max(2 * 2**30, .1 * limit) / BLOCK_BYTES)
                    if resident == 50:
                        blocks += 2
                    change('engine', 'resident_blocks', max(0, resident - blocks))
                else:
                    change('engine', 'resident_blocks', 0)
                    change('engine', 'prefetch', False)
                # This route retains the exact arithmetic partitions used by
                # the bounded small-card profile; only residual storage moves.
                # Unknown/other chunk configurations remain independent trials.
                if (engine.get('inference_kernels') and engine.get('head_chunk') == 2
                        and engine.get('ff_chunk') == 512 and engine.get('projection_chunk') == 512
                        and engine.get('attention_cpu_outputs') and engine.get('grouped_attention_outputs')
                        and engine.get('window_batch', 1) == 1):
                    change('engine', 'residual_offload', True)
                if not changes and phase == 'sample':
                    compute = compute_recovery(engine)
                    for name, new in (compute or {}).items():
                        change('engine', name, new)
                # Offloading more GPU weights must not turn the next attempt
                # into a whole-model RAM allocation on a 16 GiB machine.
                from .system import weight_cache_headroom, residual_host_headroom
                ram = value['inference_ram_budget_gb'] * 1e9
                weights = (50 - engine['resident_blocks']) * BLOCK_BYTES
                working = (2 if engine.get('attention_cpu_outputs') else .5) * 2**30
                residual_host = residual_host_headroom(canvas) if engine.get('residual_offload') else 0
                working += residual_host
                if weights + working + 2 * 2**30 > ram:
                    change('engine', 'stream_weights', True)
                    host = weight_cache_headroom(system=value.get('policy', {}).get('hardware', {}).get('system', 'unknown'),
                        streamed=True, cpu_outputs=engine.get('attention_cpu_outputs', False), canvas=canvas)
                    host += residual_host
                    limit = max(0, min(weights, ram - host)) / 1e9
                    change('engine', 'pin_host_gb', min(engine.get('pin_host_gb', 0.), limit))
                    for name in ('pin_host_weights', 'preload_host'):
                        if engine.get(name):
                            change('engine', name, False)
                elif partial and not engine.get('preload_host') and not engine.get('pin_host_weights'):
                    from .system import HOST_WEIGHT_HEADROOM
                    added = (resident - engine['resident_blocks']) * BLOCK_BYTES
                    pins = engine.get('pin_host_gb', 0.) * 1e9 + added
                    change('engine', 'pin_host_gb', round(max(0, min(weights, ram - HOST_WEIGHT_HEADROOM, pins))/1e9, 3))
    elif failure['kind'] == 'ram_pressure':
        partial = reduced_pinning(value, failure, phase, previous_ram_retries)
        if partial:
            amount, ram_recovery = partial
            change('engine', 'pin_host_gb', amount)
            # Legacy all-host modes would bypass a bounded partial budget.
            for name in ('preload_host', 'pin_host_weights'):
                if engine.get(name):
                    change('engine', name, False)
        else:
            for name in ('pin_host_gb', 'preload_host', 'pin_host_weights'):
                if name in engine:
                    change('engine', name, 0. if name == 'pin_host_gb' else False)
            change('engine', 'prefetch', False)
            change('engine', 'stream_weights', True)
            for name in ('pin_weights', 'preload', 'prefetch'):
                if name in decoder:
                    change('decoder', name, False)
            change('decoder', 'stream_output', True)
            if decoder.get('offload'):
                change('decoder', 'stream_weights', True)
    if not changes and resume_finalization and failure['kind'] in ('gpu_oom', 'ram_pressure'):
        # A fresh decode worker never loads the transformer whose finalization
        # failed. This single transition remains within the normal retry bound.
        return value, dict(reason='Reuse completed sampling in a fresh decoder worker after sampler finalization failed.',
                           changes={}, numerical_class='placement-only', measurement=failure,
                           ram_recovery=ram_recovery)
    if not changes:
        return None, {'reason': 'No further placement or compute-partition recovery is available.', 'changes': {}}
    if 'policy' in value:
        value['policy'].update(engine=dict(engine), decoder=dict(decoder))
    cause = 'page-locked memory exhausted the WDDM non-local budget' if host_pins else failure['kind']
    if compute:
        return value, dict(reason='Fresh worker after %s with no smaller placement left; use smaller compute '
                                  'partitions. Geometry, steps, seed, precision and attention are preserved; '
                                  'floating-point reduction order may differ.' % cause, changes=changes,
                           numerical_class='compute-partition', measurement=failure, ram_recovery=ram_recovery)
    return value, dict(reason='Fresh worker after %s; preserve geometry, steps, seed, precision, '
                             'attention and chunk arithmetic.' % cause, changes=changes,
                       numerical_class='same-shape-ff-recompute' if recompute_recovery else 'placement-only',
                       measurement=failure, ram_recovery=ram_recovery)


def execute_attempts(history, identity, profile, canvas, launch, *,
                     validate, archive, report, persist, max_retries=2, automatic=True,
                     prepare=None, on_retry=None):
    """Run one complete request in fresh children, retaining every failed attempt.

    Callbacks make process death, OOM and promotion testable without a tensor
    runtime. ``begin`` commits before ``launch`` is invoked. No exception handler
    uses failed measurements to certify a profile.
    """
    current = copy.deepcopy(profile)
    from .diagnostic_resources import attempt_metrics
    if type(max_retries) is not int or not 0 <= max_retries <= 2:
        raise ValueError('Resource retries must be an integer between zero and two.')
    report.setdefault('resource_attempts', [])
    evidence = {'reason': 'Initial automatic placement' if automatic else 'Explicit reproducible profile'}
    for index in range(max_retries + 1):
        if prepare is not None:
            prepare(current)
        evidence['request_artifacts'] = report.get('artifacts')
        if report.get('resource_prediction') is not None:
            evidence['prediction'] = copy.deepcopy(report['resource_prediction'])
        geometry = attempt_geometry(canvas, current['engine'])
        config = current
        attempt = history.begin(identity, config, geometry, purpose='generation',
                                evidence=evidence)
        row = dict(id=attempt, profile=copy.deepcopy(current), state='started', decision=evidence)
        report['resource_attempts'].append(row)
        report['profile'] = current
        persist()
        started = time.perf_counter()
        try:
            metrics, telemetry = launch(current, attempt)
            observation = validate(metrics, telemetry, current)
        except BaseException as error:
            metrics = getattr(error, 'metrics', None)
            failure = classify_failure(error, metrics)
            details = dict(failure)
            if isinstance(metrics, dict):
                details['failed_phase'] = metrics.get('phase')
            telemetry = getattr(error, 'telemetry', None)
            if isinstance(telemetry, dict):
                details['incomplete_resource_observations'] = telemetry
                row['resources'] = telemetry
            row['phase'] = metrics.get('phase', 'unknown') if isinstance(metrics, dict) else 'unknown'
            row['seconds'] = time.perf_counter() - started
            if isinstance(metrics, dict):
                row['metrics'] = attempt_metrics(metrics)
            history.finish(attempt, failure['outcome'], details=details)
            row.update(state=failure['outcome'], failure=failure)
            persist()
            if not automatic or not failure['retryable'] or index == max_retries:
                raise
            previous_ram_retries = sum(r.get('failure', {}).get('kind') == 'ram_pressure'
                                       for r in report['resource_attempts'][:-1])
            previous_gpu_retries = sum(r.get('failure', {}).get('kind') == 'gpu_oom'
                                       for r in report['resource_attempts'][:-1])
            from .decode_resume import completed_sampling
            updated, decision = next_placement(current, failure, (metrics or {}).get('phase', 'sample'), canvas,
                                               previous_ram_retries=previous_ram_retries,
                                               previous_gpu_retries=previous_gpu_retries,
                                               sampling_complete=completed_sampling(metrics))
            if updated is None:
                row['recovery_note'] = decision['reason']
                persist()
                raise
            row['retained'] = archive(index, row)
            current, evidence = updated, decision
            persist()
            if on_retry is not None:
                on_retry(index, row, current, decision)
            continue
        # Failure to commit success must not trigger another GPU attempt.
        history.finish(attempt, 'success', observation=observation,
                       details={'reason': 'Complete request, retained output and metrics verified.',
                                'prediction_error': report.get('resource_prediction_error')})
        row.update(state='success', phase='complete', observation=observation, resources=telemetry,
                   seconds=time.perf_counter() - started, metrics=attempt_metrics(metrics))
        report['profile'] = current
        persist()
        return metrics, telemetry, current


def complete_observation(metrics, canvas, output, artifacts, telemetry):
    """Validate existing results without rerunning a model or decoding video again."""
    from .system import memory_complete, memory_peak
    from .validation import _retained_array_header, finite_number
    expected = canvas['frames']
    if (metrics.get('success') is not True or metrics.get('phase') != 'complete'
            or metrics.get('finite_latents') is not True or metrics.get('frames') != expected
            or any(metrics.get('geometry', {}).get(k) != canvas[k] for k in ('width','height','frames','fps'))):
        raise ValueError('A successful complete request with matching geometry is required.')
    steps = metrics.get('config', {}).get('steps')
    times = metrics.get('step_seconds', [])
    from .two_pass import steps as sampling_steps, same_strategy
    total_steps = sampling_steps(metrics.get('sampling_plan'))
    if (type(steps) is not int or steps != canvas.get('steps', 8) or len(times) != total_steps
            or not same_strategy(metrics, canvas) or not all(finite_number(t) and t > 0 for t in times)):
        raise ValueError('A probe or incomplete sample cannot become a resource observation.')
    rgb_shape, dtype = _retained_array_header(Path(artifacts)/'rgb.npy')
    if str(dtype) != 'uint8' or rgb_shape != (expected, canvas['height'], canvas['width'], 3):
        raise ValueError('Retained video frames do not match the complete request.')
    audio_shape, audio_dtype = _retained_array_header(Path(artifacts)/'audio.npy')
    if (str(audio_dtype) != 'float32' or len(audio_shape) != 2 or audio_shape[0] != 2
            or abs(audio_shape[1]/32000. - expected/canvas['fps']) > 1/canvas['fps']):
        raise ValueError('Retained stereo audio does not match the request duration.')
    import av
    with av.open(str(output)) as container:
        video, audio = container.streams.video, container.streams.audio
        if len(video) != 1 or len(audio) != 1:
            raise ValueError('The completed MP4 must contain video and audio.')
        stream = video[0]
        if ((stream.width, stream.height, stream.frames) != (canvas['width'],canvas['height'],expected)
                or float(stream.average_rate) != canvas['fps'] or audio[0].codec_context.channels != 2):
            raise ValueError('MP4 metadata does not match the requested output.')
    if metrics.get('sampling_reused') is True:
        # The output is validated, but these observations cover only a decoder
        # retry. Do not teach placement/ETA that a full request cost this little
        # time or occupied only the decoder's memory.
        return None
    ram, gpu = telemetry.get('ram', {}), telemetry.get('gpu', {})
    counts = (ram.get('ram_observation_samples'), gpu.get('samples'))
    if (not memory_complete(ram) or any(type(n) is not int or n <= 0 for n in counts)
            or gpu.get('sampling_errors')):
        raise ValueError('Complete request finished but resource observations are incomplete.')
    if any(type(value) is not int or value <= 0 for value in (memory_peak(ram), gpu.get('gpu_peak_bytes'))):
        raise ValueError('Resource observations require positive measured RAM and GPU peaks.')
    peaks = [metrics.get(name) for name in ('load_peak_reserved_bytes','torch_peak_reserved_bytes',
                                           'final_stage_peak_reserved_bytes')]
    if any(type(x) is not int or x <= 0 for x in peaks):
        raise ValueError('Missing full-phase GPU allocator peaks.')
    stages = {name: metrics.get(name) for name in ('load_seconds','sample_seconds','vae_load_seconds',
        'video_decode_seconds','audio_load_decode_seconds','encode_seconds','decode_save_seconds','work_seconds')}
    if not all(finite_number(t) for t in stages.values()):
        raise ValueError('Incomplete per-stage timings.')
    result = dict(full_request=True, validated=True, completed_steps=total_steps,
                  completed_frames=expected, media_verified=True, metrics_complete=True,
                  peak_reserved_bytes=max(peaks), whole_gpu_peak_bytes=gpu['gpu_peak_bytes'],
                  ram_peak_bytes=memory_peak(ram), ram_metric=ram.get('ram_guard_metric', 'unknown'),
                  step_seconds=times, stage_seconds=stages,
                  execution_context={
                      'adaln_table_cache_hits': metrics.get('config', {}).get('adaln_table_cache_hits'),
                      'host_threads': metrics.get('execution_context', {}).get('host_threads'),
                      'allocator_limit_bytes': metrics.get('device_memory', {}).get('benchmark_allocator_limit_bytes')},
                  load_breakdown=metrics.get('load_breakdown', {}),
                  evidence='Completed original request; existing array and MP4 metadata verified. '
                           'This is a resource observation, not proof of numerical equivalence to another configuration.')
    shape = metrics.get('conditioning_shape')
    if shape and len(shape) == 2 and shape[1] == 5120:
        result['text_tokens'] = shape[0]
    return result
