"""UI-only duration ranges from this machine's complete local generations.

Keep each resolution's measured step cost separate. Only the incremental steps
scale; encoding, cold preparation, first-step overhead and decoding remain in
the measured request total. This forecast never admits a workload or selects
resource policy, and never uses prompt text or sends reports elsewhere.
"""
import json
import math
import platform
from pathlib import Path
from statistics import median
import threading
import time


def positive(value):
    return type(value) in (float, int) and math.isfinite(value) and value > 0


def same_device(hardware, device):
    """NVIDIA GPUs match by UUID; Macs by chip and unified memory (no UUID)."""
    if isinstance(device, dict):
        return (device.get('backend') == 'mps' and hardware.get('device_backend') == 'mps'
                and bool(device.get('name')) and hardware.get('gpu_name') == device['name']
                and hardware.get('ram_total') == device.get('unified_ram_bytes'))
    return bool(device) and hardware.get('gpu_uuid') == device


def timing_record(report, gpu_uuid, system, *, stage_details=True):
    if not isinstance(report, dict) or report.get('success') is not True:
        return None
    video = report.get('video') or {}
    if (report.get('result_cache_hit') or video.get('sampling_reused')
            or video.get('first_pass_reused') or video.get('phase') != 'complete'):
        return None
    hardware = report.get('profile', {}).get('policy', {}).get('hardware', {})
    if not same_device(hardware, gpu_uuid) or hardware.get('system') != system:
        return None
    shape = report.get('geometry', {})
    # The pooled ETA/capability schema has no per-step reference schedule.
    # An early-only success cannot calibrate a sustained-reference request.
    if shape.get('early_reference') is not None:
        return None
    plan = report.get('sampling_plan') or shape.get('sampling_plan') or {}
    if not all(type(shape.get(k)) is int and shape[k] > 0 for k in ('width', 'height', 'frames')):
        return None
    total = report.get('request_seconds')
    if not positive(total):
        return None
    # Requests that needed recovery do not describe the cost of one normal run.
    attempts = report.get('resource_attempts') or []
    if len(attempts) > 1:
        return None
    passes = video.get('sampling_passes') if plan.get('enabled') else [video]
    preview = bool(report.get('preview') or plan.get('preview'))
    if not isinstance(passes, list) or len(passes) != (2 if plan.get('enabled') and not preview else 1):
        return None
    measured = []
    for part in passes:
        times = part.get('step_seconds')
        if not isinstance(times, list) or len(times) < 2 or not all(positive(v) for v in times):
            return None
        measured.append(dict(steps=len(times), seconds=sum(times), incremental=median(times[1:])))
        if stage_details:
            measured[-1]['first'] = times[0]
    if sum(p['seconds'] for p in measured) > total * 1.01:
        return None
    base = plan.get('base_steps', video.get('config', {}).get('steps', 8))
    refine = plan.get('refine_steps', 0) if plan.get('enabled') and not preview else 0
    if measured[0]['steps'] != base or (refine and measured[1]['steps'] != refine):
        return None
    cache = effective_step_cache(passes[0])
    if cache == 'easycache-av' and not passes[0]['step_cache'].get('guard'):
        # Before the guard this cache reused steps unchecked and ran too fast to describe any request.
        return None
    encoding = report.get('encoding') or {}
    cached = bool(encoding.get('cache_hit') or report.get('input_cache', {}).get('conditioning_hit')
                  or report.get('tuning', {}).get('conditioning_cache_hit'))
    record = dict(shape={k: shape[k] for k in ('width', 'height', 'frames')},
        two_pass=bool(plan.get('enabled')), base_steps=base, refine_steps=refine,
        passes=measured, request_seconds=total,
        conditioning_cache_hit=cached,
        text_measured=positive(encoding.get('work_seconds')) and not cached,
        sampling_seconds=video.get('sample_seconds'),
        task=video.get('config', {}).get('task', 't2va'),
        adapters=bool(report.get('profile', {}).get('engine', {}).get('loras') or
                      report.get('lora') or video.get('config', {}).get('online_lora')))
    if cache is not None:
        # Rows without the key are plain runs, as every run before the caches was.
        record['step_cache'] = cache
    if plan.get('enabled') and plan.get('scale', 2) != 2:
        # Rows without the key upscaled 2x, as every two-pass run before the factor was.
        record['upscale_factor'] = plan['scale']
    if not stage_details:
        # The slider history carries no flow marker, and a preview (one pass)
        # is never a whole-request sample: estimate() would read its missing
        # second pass. Leave it out here; the detailed records mark it.
        return None if preview else record
    upscale = video.get('latent_upscale') or {}
    upscale_seconds = (sum(upscale[k] for k in ('load_seconds', 'compute_seconds'))
                       if all(type(upscale.get(k)) in (int, float) and math.isfinite(upscale[k])
                              and upscale[k] >= 0 for k in ('load_seconds', 'compute_seconds')) else None)
    stages = {'encode': encoding.get('work_seconds') if not cached else 0.,
              'load': video.get('load_seconds'),
              'pass1': measured[0]['seconds'],
              'upscale': upscale_seconds if refine else 0.,
              'pass2': measured[1]['seconds'] if refine else 0.,
              'decode_save': video.get('decode_save_seconds')}
    if all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in stages.values()):
        # Time outside the reported stages (request preparation, cache checks,
        # the model handover and final checks) falls about evenly before the
        # video model starts and around saving, where the bridge counts it.
        # A cached encode has no first-stage work to attach it to.
        extra = max(0., total - sum(stages.values()))
        first = 0. if cached else extra / 2
        stages['encode'] += first
        stages['decode_save'] += extra - first
    # Optional report chronology only; legacy readers already supply newest
    # first by file mtime. Never retain a path, prompt or request identifier.
    recorded_epoch = report.get('completed_epoch', report.get('started_epoch'))
    version = (report.get('runtime_code') or {}).get('version') if isinstance(report.get('runtime_code'), dict) else None
    # Saved planner input, after encoding/idle-cache credit, is in whole bytes.
    memory = [hardware.get(k) for k in ('vram_total', 'vram_free', 'ram_total', 'ram_available')]
    in_use = None
    if all((type(v) is int and v > 0) or
           (type(v) is float and positive(v) and v.is_integer()) for v in memory):
        vram, free, ram, available = map(int, memory)
        if free <= vram and available <= ram:
            in_use = dict(vram=vram - free, ram=ram - available)
    # Only the report's own final geometry describes retained conditioning.
    # This field is evidence for capability calibration, never a live ETA input.
    counts = [shape.get(k) for k in ('reference_video_tokens', 'reference_audio_tokens')]
    reference_rows = (dict(zip(('video', 'audio'), counts))
                      if all(type(n) is int and n >= 0 for n in counts) else None)
    record.update(recorded_epoch=recorded_epoch if positive(recorded_epoch) else None,
        version=version if isinstance(version, str) and len(version) <= 64 else None,
        in_use=in_use,
        reference_rows=reference_rows,
        flow='preview' if preview else 'full', stages=stages,
        sampling_geometry={k: plan[k] for k in ('first', 'second') if plan.get(k)},
        references=task_has_references(video.get('config', {}).get('task', 't2va')),
        model_resident=video.get('resident_engine_cache_hit'))
    return record


def effective_step_cache(part):
    """The step cache a first pass actually used, from its report; None for a plain pass."""
    from .tiers import STEP_CACHES
    stats = part.get('step_cache') if isinstance(part, dict) else None
    if not isinstance(stats, dict) or stats.get('disabled_reason') or stats.get('kind') not in STEP_CACHES:
        return None
    return stats['kind']


def report_step_cache(report):
    """The first-pass step cache a finished request used, from its report."""
    video = report.get('video') or {} if isinstance(report, dict) else {}
    passes = video.get('sampling_passes')
    return effective_step_cache(passes[0] if isinstance(passes, list) and passes else video)


def estimate(records, canvas, *, base_steps=8, refine_steps=3, two_pass=True, task='t2va', adapters=False,
             step_cache=None, cache_share=None, upscale_factor=2):
    """Whole-request seconds from local runs of this shape.

    Runs with the requested step cache come first. Without one, plain runs stand
    in with their first pass scaled by `cache_share` (tiers.sampling_share by
    default) and a wider range; a cached run never stands in for a plain request.
    A two-pass run with another upscale factor stands in the same way, its
    sampling scaled by tiers.upscale_share.
    """
    from .two_pass import validate_steps
    from .tiers import sampling_share, upscale_share
    validate_steps(base_steps, refine_steps, two_pass)
    share = sampling_share(step_cache) if cache_share is None else cache_share
    if two_pass and upscale_factor != 2:
        # The factor this canvas really runs: a small canvas samples both passes at its own size.
        from .two_pass import plan
        upscale_factor = plan(dict(width=canvas['width'], height=canvas['height'], frames=canvas['frames']), True,
                              base_steps=base_steps, refine_steps=refine_steps, scale=upscale_factor).get('scale', 2)
    upscale_factor = upscale_factor if two_pass else 2
    candidates = []
    for row in records:
        if (row.get('flow', 'full') != 'full' or row['task'] != task or row['adapters'] != adapters
                or row['shape'] != {k: canvas[k] for k in ('width', 'height', 'frames')}):
            continue
        cache = row.get('step_cache')
        if cache != step_cache and (cache is not None or row['two_pass'] != two_pass):
            continue
        # Both passes retain their own resolution costs. Never multiply the
        # entire generation by a step ratio or confuse eight low-res steps
        # with eight full-res steps. Existing first-step warmup stays intact.
        switched = row['two_pass'] != two_pass
        if switched:
            # An observed refinement supplies full-resolution step costs. We
            # cannot infer the reverse (low-resolution cost) from one pass.
            if two_pass or not positive(row.get('sampling_seconds')):
                continue
            last = row['passes'][1]
            seconds = row['request_seconds'] - row['sampling_seconds'] + last['seconds'] + (base_steps - last['steps']) * last['incremental']
        else:
            seconds = row['request_seconds'] + (base_steps - row['base_steps']) * row['passes'][0]['incremental']
            if cache != step_cache:
                # A plain run of the same plan: only its first pass changes with a cache.
                first = row['passes'][0]['seconds'] + (base_steps - row['base_steps']) * row['passes'][0]['incremental']
                seconds -= (1 - share) * first
                switched = True
        if two_pass and row['two_pass'] == two_pass:
            seconds += (refine_steps - row['refine_steps']) * row['passes'][1]['incremental']
            factor = row.get('upscale_factor', 2)
            if factor != upscale_factor:
                sampled = sum(part['seconds'] for part in row['passes'])
                seconds += (upscale_share(upscale_factor) / upscale_share(factor) - 1) * sampled
                switched = True
        if positive(seconds):
            candidates.append((seconds, row, switched))
    if not candidates:
        return dict(status='unavailable', reason='no_matching_local_history')
    # Prefer actual runs of the requested mode over cross-mode extrapolation.
    matched = [r for r in candidates if not r[2]]
    candidates = (matched or candidates)[:6]
    values = [v for v, _, _ in candidates]
    # Cover the observed cold/warm range as well as modest scheduling variation;
    # these are a presentation range, not a statistically calibrated interval.
    low, high = min(values) * .85, max(values) * 1.2
    if len(values) == 1:
        low, high = values[0] * .8, values[0] * 1.35
    if any(switched for _, _, switched in candidates):
        low, high = min(values) * .65, max(values) * 1.5
    encoding_known = any(r['text_measured'] for _, r, _ in candidates)
    return dict(status='estimated', seconds=median(values), low_seconds=low, high_seconds=high,
        samples=len(values), includes_text_encoding=encoding_known,
        scope='whole_request' if encoding_known else 'cached_prompt_request',
        source='local_completed_generations', capacity_guarantee=False)


_CACHE = {}


def task_has_references(task):
    return task not in (None, 't2va')


# No measured request, stage or step takes this long (about twelve days). A
# larger number in a record is damage, and would overflow the medians and sums
# of a forecast. Times extrapolated to more steps are not held to it.
_LONGEST_SECONDS = 1e6


def _believable(row):
    values = [row.get('request_seconds'), *(row.get('stages') or {}).values()]
    for part in row.get('passes') or ():
        values += [part.get(key) for key in ('seconds', 'first', 'incremental')]
    return all(value <= _LONGEST_SECONDS for value in values if type(value) in (int, float))


def _near(left, right):
    return left > 0 and abs(right / left - 1.) <= .1000001


def stage_forecast(records, request, cold_flags, prior, version=None):
    """Prefer runs of this FreeVideo version; older runs stay in use until then.

    An update can change stage times, so when any run of the current version
    matches the whole request, only runs of that version are used.
    """
    if version:
        same = [row for row in records if row.get('version') == version]
        if same:
            result = _stage_forecast(same, request, cold_flags, prior)
            if result.get('stages'):
                return result
    return _stage_forecast(records, request, cold_flags, prior)


def _stage_forecast(records, request, cold_flags, prior):
    """One whole match can seed ETA; each typical still needs two similar runs.

    Only timing_record's allowlisted successful, same-device/OS observations
    enter here. Typical step costs keep first/warm and pass identity separate.
    A partial record can teach a typical without claiming a whole local ETA.
    Use the latest three matches for each stage, not three arbitrary reports.
    Callers supply newest-first records when report chronology is unavailable.
    """
    from .stage_estimate import STAGES, canvases, token_count, step_counts
    first, second, decode = canvases(request)
    counts = step_counts(request)
    samples = {key: [] for key in (*STAGES, 'pass1_first', 'pass1_warm', 'pass2_first', 'pass2_warm')}
    whole = []
    recent = records[:48]
    if recent and all(positive(row.get('recorded_epoch')) for row in recent):
        recent = sorted(recent, key=lambda row: row['recorded_epoch'], reverse=True)
    cached = cold_flags.get('encode_cached') is True
    for row in recent:
        # A damaged record must not stop the estimate, nor leave part of itself
        # behind: its samples are kept only when the whole record computes.
        found, total = [], None
        try:
            if not _believable(row):
                continue
            same_task = row.get('task') == request.get('task', 't2va')
            same_lora = row.get('adapters', False) == bool(request.get('lora_count'))
            refs = row.get('references', task_has_references(row.get('task')))
            same_refs = refs == bool(request.get('reference_count') or task_has_references(request.get('task', 't2va')))
            r1, r2, rd = canvases(dict(row['shape'], two_pass=row['two_pass'],
                                     sampling_geometry=row.get('sampling_geometry'), flow=row.get('flow', 'full')))
            stages = row.get('stages') or {}
            if same_task and same_refs and row.get('text_measured') and positive(stages.get('encode')):
                found.append(('encode', stages['encode']))
            if same_lora and row.get('model_resident') is False and positive(stages.get('load')):
                found.append(('load', stages['load']))
            if _near(decode['width'] * decode['height'] * decode['frames'], rd['width'] * rd['height'] * rd['frames']):
                if positive(stages.get('decode_save')):
                    found.append(('decode_save', stages['decode_save']))
            similar_passes = row['two_pass'] == request.get('two_pass', True)
            # A step cache changes the first pass only; its steps and totals stay with their own cache.
            same_cache = row.get('step_cache') == request.get('step_cache')
            for i, (target, observed, count) in enumerate(zip((first, second), (r1, r2), counts), 1):
                near = _near(token_count(target), token_count(observed))
                similar_passes = similar_passes and (near or not count)
                if not (count and same_task and same_lora and near and i <= len(row['passes'])
                        and (same_cache or i == 2)):
                    continue
                part = row['passes'][i-1]
                seconds = part['seconds'] + (count-part['steps']) * part['incremental']
                if positive(seconds):
                    found.append((f'pass{i}', seconds))
                if positive(part.get('first')):
                    found.append((f'pass{i}_first', part['first']))
                if positive(part['incremental']):
                    found.append((f'pass{i}_warm', part['incremental']))
            if similar_passes and same_task and same_lora and positive(stages.get('upscale')):
                found.append(('upscale', stages['upscale']))
            if (request.get('flow', 'full') == row.get('flow', 'full') and similar_passes and same_task and same_lora
                    and same_refs and same_cache):
                total = row['request_seconds'] + (counts[0] - row['base_steps']) * row['passes'][0]['incremental']
                if counts[1]:
                    total += (counts[1] - row['refine_steps']) * row['passes'][1]['incremental']
                # Reusing the prompt cache removes the first stage and little
                # else, so a run in the other state can still describe this
                # request: without its first stage, or with one added below.
                hit = bool(row.get('conditioning_cache_hit', False))
                if cached and not hit:
                    total = total - stages['encode'] if positive(stages.get('encode')) else None
                total = (total, hit and not cached, hit == cached) if total is not None and positive(total) else None
        except (LookupError, TypeError, ValueError, ArithmeticError, AttributeError):
            continue
        for key, value in found:
            samples[key].append(value)
        if total is not None:
            whole.append(total)
    samples = {key: values[:3] for key, values in samples.items()}
    # Runs in the request's own prompt-cache state come first; converted runs
    # are used only when there is none.
    whole = ([item for item in whole if item[2]] or whole)[:3]
    typical = {}
    for key, values in samples.items():
        if len(values) < 2:
            continue
        seconds = median(values)
        unit = 5 if seconds < 60 else 10 if seconds < 600 else 60
        rounded = math.floor(seconds / unit + .5) * unit
        if rounded >= 10:
            typical[key] = rounded
    result = dict(samples=len(whole), typical=typical)
    if whole:
        weights = {key: (median(samples[key]) if samples[key] else prior[key]) for key in STAGES}
        if cached:
            weights['encode'] = 0.
        # A run that reused the prompt cache lacks the encode this request will do.
        seconds = median([total + (weights['encode'] if encodes else 0.) for total, encodes, _ in whole])
        # Preserve whole-request observed overhead, including sparse old reports.
        # It is mostly request preparation before the video model starts (the
        # first stage in the bridge), so it is not spread over later stages.
        # A cached encode has no first-stage work, so its share goes to saving.
        extra = seconds - sum(weights.values())
        if extra >= 0:
            stage = 'decode_save' if cached else 'encode'
            stages = dict(weights, **{stage: weights[stage] + extra})
        else:
            stages = {k: v * seconds / max(sum(weights.values()), .001) for k, v in weights.items()}
        result.update(seconds=seconds, stages=stages)
        # Unrounded warm step costs, so live steps can scale this machine's history.
        result['warm'] = {key: median(samples[key + '_warm']) for key in ('pass1', 'pass2')
                          if samples[key + '_warm']}
    return result


_START_CONTEXT = {}
# Guards the entries above: only one background read per key, and readers
# always see a complete hardware/records pair. File IO stays outside it.
_START_LOCK = threading.Lock()


def _context_key(output_directory, machine):
    device = machine.get('device_identity') if machine.get('device_backend') == 'mps' else machine.get('gpu_uuid')
    system = machine.get('system') or platform.system()
    return (str(output_directory), json.dumps(device, sort_keys=True), system, machine.get('setup_run')), device, system


_INVENTORY = {}


def inventory_hardware(machine):
    """The installer's hardware inventory for this machine's device (gpu_name, capability, ram/vram totals, system), from
    its setup run's plan.json; {} when there is none or it describes another device. Read again only when the file
    changes, so callers that ask often (the prompt model's info) stay cheap."""
    _, device, _ = _context_key('', machine)
    try:
        path = Path(machine['setup_run']) / 'plan.json'
        stamp = path.stat().st_mtime_ns
    except (KeyError, TypeError, OSError):
        return {}
    key = (str(path), json.dumps(device, sort_keys=True))
    cached = _INVENTORY.get(key)
    if cached and cached[0] == stamp:
        return dict(cached[1])
    hardware = {}
    try:
        with path.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) <= 1024 * 1024:
            found = json.loads(raw).get('inventory', {}).get('hardware', {})
            if same_device(found, device):
                hardware = {k: found[k] for k in ('gpu_name', 'capability', 'ram_total', 'vram_total', 'system') if k in found}
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    _INVENTORY[key] = (stamp, hardware)
    return dict(hardware)


def start_context(output_directory, machine, *, timeout=.035):
    """Bound foreground IO to ~35 ms, even on slow/unavailable output disks.

    One daemon per key reads in the background. Its last result stays in use
    while a newer read runs (at most one every 15 s), so a request that starts
    minutes after the previous one still sees that run. A request that started
    before a read finished can pick it up later (history_read_at).
    No GPU calls, resident RPC, model scans, hashing or directory-wide cache
    inspection. Installation inventory describes identity, not live free RAM.
    """
    from threading import Event, Thread
    key, device, system = _context_key(output_directory, machine)
    start = False
    with _START_LOCK:
        entry = _START_CONTEXT.get(key)
        if entry is None:
            entry = dict(ready=Event(), started=None, read_at=None, hardware={}, records=[], reading=False)
            _START_CONTEXT[key] = entry
        now = time.monotonic()
        if not entry['reading'] and (entry['started'] is None or now - entry['started'] > 15):
            entry['reading'], entry['started'], start = True, now, True
    if start:
        def read():
            hardware, records = dict(entry['hardware']), list(entry['records'])
            if machine.get('setup_run'):
                hardware = inventory_hardware(machine) or hardware
            try:
                records = local_records(output_directory, device, system=system, stage_details=True)
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            finally:
                with _START_LOCK:
                    entry['hardware'], entry['records'] = hardware, records
                    entry['read_at'], entry['reading'] = time.monotonic(), False
                entry['ready'].set()
        Thread(target=read, name='freevideo-progress-history', daemon=True).start()
    if timeout and not entry['ready'].is_set():
        entry['ready'].wait(timeout)
    with _START_LOCK:
        return dict(entry['hardware']), list(entry['records'])


def history_read_at(output_directory, machine):
    """Monotonic time the last background read finished, or None."""
    with _START_LOCK:
        entry = _START_CONTEXT.get(_context_key(output_directory, machine)[0])
        return entry and entry['read_at']


def local_records(output_directory, gpu_uuid, *, system=None, stage_details=False, refresh=False):
    """Bounded recent JSON reads, cached between slider changes; never open MP4s."""
    system = system or platform.system()
    root = Path(output_directory).resolve() / 'FreeVideo'
    key = (str(root), json.dumps(gpu_uuid, sort_keys=True), system)
    if stage_details:
        key += (True,)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and not refresh and now - cached[0] < 15:
        return cached[1]
    paths = []
    for path in root.glob('*/*/video.request.json'):
        try:
            if path.resolve().is_relative_to(root) and path.stat().st_size <= 4 * 1024 * 1024:
                paths.append((path.stat().st_mtime_ns, path))
        except OSError:
            continue
    records = []
    # Slider estimates keep their original window of the 48 newest files; the
    # progress history reads on until it has 48 usable rows.
    candidates = sorted(paths, reverse=True)
    for _, path in (candidates if stage_details else candidates[:48]):
        try:
            with path.open('rb') as stream:
                data = stream.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                continue
            row = timing_record(json.loads(data), gpu_uuid, system, stage_details=stage_details)
            if row:
                records.append(row)
                if stage_details and len(records) == 48:
                    break
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            continue
    if not stage_details:
        _CACHE.clear()
    _CACHE[key] = (now, records)
    return records
