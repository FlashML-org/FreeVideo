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


def timing_record(report, gpu_uuid, system):
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
    plan = report.get('sampling_plan') or shape.get('sampling_plan') or {}
    if 'prism' in (plan.get('model'), report.get('model')):
        return None  # These estimates describe MiniMax H3 runs.
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
    if not isinstance(passes, list) or len(passes) != (2 if plan.get('enabled') else 1):
        return None
    measured = []
    for part in passes:
        times = part.get('step_seconds')
        if not isinstance(times, list) or len(times) < 2 or not all(positive(v) for v in times):
            return None
        measured.append(dict(steps=len(times), seconds=sum(times), incremental=median(times[1:])))
    if sum(p['seconds'] for p in measured) > total * 1.01:
        return None
    base = plan.get('base_steps', video.get('config', {}).get('steps', 8))
    refine = plan.get('refine_steps', 0) if plan.get('enabled') else 0
    if measured[0]['steps'] != base or (refine and measured[1]['steps'] != refine):
        return None
    encoding = report.get('encoding') or {}
    cached = bool(encoding.get('cache_hit') or report.get('input_cache', {}).get('conditioning_hit')
                  or report.get('tuning', {}).get('conditioning_cache_hit'))
    return dict(shape={k: shape[k] for k in ('width', 'height', 'frames')},
        two_pass=bool(plan.get('enabled')), base_steps=base, refine_steps=refine,
        passes=measured, request_seconds=total,
        conditioning_cache_hit=cached,
        text_measured=positive(encoding.get('work_seconds')) and not cached,
        sampling_seconds=video.get('sample_seconds'),
        task=video.get('config', {}).get('task', 't2va'),
        adapters=bool(report.get('profile', {}).get('engine', {}).get('loras') or
                      report.get('lora') or video.get('config', {}).get('online_lora')))


def estimate(records, canvas, *, base_steps=8, refine_steps=3, two_pass=True, task='t2va', adapters=False):
    from .two_pass import validate_steps
    validate_steps(base_steps, refine_steps, two_pass)
    candidates = []
    for row in records:
        if (row['task'] != task or row['adapters'] != adapters
                or row['shape'] != {k: canvas[k] for k in ('width', 'height', 'frames')}):
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
        if two_pass and not switched:
            seconds += (refine_steps - row['refine_steps']) * row['passes'][1]['incremental']
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


def local_records(output_directory, gpu_uuid, *, system=None):
    """Bounded recent JSON reads, cached between slider changes; never open MP4s."""
    system = system or platform.system()
    root = Path(output_directory).resolve() / 'FreeVideo'
    key = (str(root), json.dumps(gpu_uuid, sort_keys=True), system)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < 15:
        return cached[1]
    paths = []
    for path in root.glob('*/*/video.request.json'):
        try:
            if path.resolve().is_relative_to(root) and path.stat().st_size <= 4 * 1024 * 1024:
                paths.append((path.stat().st_mtime_ns, path))
        except OSError:
            continue
    records = []
    for _, path in sorted(paths, reverse=True)[:48]:
        try:
            with path.open('rb') as stream:
                data = stream.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                continue
            row = timing_record(json.loads(data), gpu_uuid, system)
            if row:
                records.append(row)
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            continue
    _CACHE.clear()
    _CACHE[key] = (now, records)
    return records
