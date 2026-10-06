"""Validated local tuning and conditioning reuse; no tensor imports in callers."""
import copy
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit

from .hardware import GiB
from .policy import BLOCK_BYTES
from .monitoring import save
from .paths import data_root


SCHEMA = 1
COMPUTE_FILES = ('activation_staging.py', 'residual.py', 'adaln.py', 'adaln_assets.py', 'attention.py', 'blocks.py',
                 'keyframes.py', 'media_request.py', 'media_conditioning.py', 'media_encoding.py', 'reference_sampler.py', 'lora_cache.py',
                 'conditioning.py', 'decode.py', 'decode_prefetch.py', 'resident_models.py', 'decode_stream.py', 'streamed_weights.py', 'encode_worker.py', 'idle_encoder.py', 'media_encoding.py', 'fp8.py', 'fp8_ops.py', 'fp8_gemm.py',
                 'encoder_precision.py', 'encoder_lowmem.py',
                 'kernel_capabilities.py', 'gpu_budget.py', 'worker.py', 'resident_worker.py', 'failure_cleanup.py',
                 'geometry.py', 'head_chunk.py', 'offload.py', 'packing.py', 'runtime.py',
                 'two_pass.py', 'two_pass_metrics.py', 'refine.py', 'latent_upscale.py',
                 'vae_tiles.py', 'weight_only.py', 'weights.py')
PACKAGES = ('torch', 'triton', 'triton-windows', 'transformers', 'tokenizers', 'sageattention', 'numpy',
            'safetensors', 'comfy-kitchen', 'comfy-aimdo', 'torchaudio', 'torchvision')
PLACEMENT_KEYS = {'resident_blocks', 'pin_host_gb'}
PATCH_KEYS = {'window_batch', 'ff_chunk', 'projection_chunk', 'head_chunk', 'head_parallelism', 'prefetch',
              'attention_cpu_outputs', 'grouped_attention_outputs', 'fp8_ff_recompute'} | PLACEMENT_KEYS
CACHE_ENTRIES = 128
CACHE_BYTES = 128 * 2**20


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def state_path():
    return data_root() / 'tuning' / 'state.json'


def package_versions(python=None):
    if python and os.path.abspath(python) != os.path.abspath(sys.executable):
        code = 'import importlib.metadata as m,json; print(json.dumps({d.metadata["Name"].lower().replace("_","-"):d.version for d in m.distributions()}))'
        result = subprocess.run([str(python), '-B', '-c', code], capture_output=True, text=True, timeout=15, check=True)
        installed = json.loads(result.stdout)
    else:
        installed = {d.metadata['Name'].lower().replace('_', '-'): d.version for d in importlib.metadata.distributions()}
    return {name: version for name, version in installed.items() if name in PACKAGES or name.startswith(('nvidia-', 'cuda-'))}


def frozen_versions(text):
    installed = {}
    for line in text.splitlines():
        if '==' in line:
            name, version = line.split('==', 1)
        elif ' @ ' in line and '.whl' in line:
            name, reference = line.split(' @ ', 1)
            filename = unquote(urlsplit(reference).path).rsplit('/', 1)[-1]
            match = re.match(r'[^-]+-([0-9][^-]*)-[^-]+-[^-]+-[^-]+\.whl$', filename)
            if not match:
                continue
            version = match[1]
        else:
            continue
        name = name.lower().replace('_', '-')
        if name in PACKAGES or name.startswith(('nvidia-', 'cuda-')):
            installed[name] = version
    return installed


def identity(machine, hardware):
    from .bootstrap import inventory, model_target
    from .install_tuning import required_models
    observed = inventory(machine['gpu_uuid'])
    package = Path(__file__).parent
    encoder = Path(machine['encoder_model_root']) / 'text_encoders' / machine['encoder']
    manifest = Path(machine['cache']) / 'manifest.json'
    weights = json.loads(manifest.read_text(encoding='utf-8'))
    stamps = {}
    required = required_models(json.loads((package / 'model_files.json').read_text(encoding='utf-8')), machine['cache'])
    extra = [model_target(row, Path(machine['model_root']), Path(machine['encoder_model_root'])) for row in required]
    for path in [encoder, *(Path(machine['cache']) / g['file'] for g in weights['groups']), *extra]:
        status = path.stat()
        stamps[str(path.resolve())] = [status.st_size, status.st_mtime_ns, status.st_ctime_ns]
    specs = json.loads((package / 'dependencies.json').read_text(encoding='utf-8'))
    revisions = {k: specs[k] for k in ('vdn', 'diffusers', 'encoder', 'sageattention')}
    for checkout, ref, expected in ((machine['vdn_root'], 'HEAD', specs['vdn']['commit']),
            (machine['comfy_root'], 'HEAD', specs['encoder']['comfy_commit']),
            (str(Path(machine['vdn_root']) / 'diffusers'), 'HEAD^{tree}', specs['diffusers']['patched_tree'])):
        observed_ref = subprocess.check_output(['git', '-C', checkout, 'rev-parse', ref], text=True, timeout=10).strip()
        dirty = subprocess.check_output(['git', '-C', checkout, 'status', '--porcelain', '--untracked-files=no'], text=True, timeout=10)
        if observed_ref != expected or dirty:
            raise ValueError('Pinned model code changed; saved tuning cannot be reused: ' + checkout)
    return {'schema': SCHEMA, 'system': hardware.system,
            'gpu': {k: observed['selected_gpu'][k] for k in ('uuid', 'name', 'driver_version')},
            'capability': list(hardware.capability), 'manifest_sha256': digest(manifest),
            'engine_packages': package_versions(), 'encoder_packages': package_versions(machine['comfy_python']),
            'source_sha256': {name: digest(package / name) for name in COMPUTE_FILES},
            'weight_stamps': stamps, 'dependency_revisions': revisions,
            'encoder': machine['encoder'], 'model_revision': machine['model_revision']}


def report_identity_errors(inventory, expected):
    errors = []
    if expected.get('system') and inventory.get('hardware', {}).get('system') != expected['system']:
        errors.append('Operating system differs')
    gpu = inventory.get('selected_gpu', {})
    for name, value in expected['gpu'].items():
        if gpu.get(name) != value:
            errors.append('GPU/driver changed: ' + name)
    if inventory.get('fp8_cache_manifest_sha256') != expected['manifest_sha256']:
        errors.append('Prepared model cache differs')
    for role in ('engine', 'encoder'):
        if frozen_versions(inventory.get(role + '_packages', {}).get('stdout', '')) != expected[role + '_packages']:
            errors.append(role + ' dependency versions differ or are missing')
    for name, value in expected['source_sha256'].items():
        if inventory.get('source_files', {}).get('freevideo_engine/' + name) != value:
            errors.append('Compute source changed: ' + name)
    configuration = inventory.get('machine_configuration', {})
    if any(inventory.get('dependency_revisions', {}).get(k) != v for k, v in expected.get('dependency_revisions', {}).items()):
        errors.append('Pinned source revisions differ')
    if configuration.get('model_revision') != expected['model_revision'] or configuration.get('encoder') != expected['encoder']:
        errors.append('Pinned model or encoder differs')
    return errors


def load_state(expected=None):
    path = state_path()
    if not path.is_file():
        return None, 'No local optimization saved'
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(state, dict) or state.get('schema') != SCHEMA or not state.get('enabled'):
            return None, 'Local optimization is disabled or has an unknown format'
        if not isinstance(state.get('conditioning'), dict) or not isinstance(state.get('profiles'), list):
            return None, 'Invalid local optimization entries'
        for entry in state['conditioning'].values():
            if (not isinstance(entry, dict) or not isinstance(entry.get('encoding'), dict)
                    or type(entry.get('bytes')) is not int or not 0 < entry['bytes'] <= CACHE_BYTES
                    or not isinstance(entry.get('path'), str) or not isinstance(entry.get('sha256'), str)):
                return None, 'Invalid local conditioning cache entry'
        if expected is not None and state.get('identity') != expected:
            return None, 'Hardware, driver, dependencies, model or compute code changed; run optimize again'
        retired=False
        for entry in state['profiles']:
            if isinstance(entry,dict) and retired_failure(entry.get('last_failure','')):
                entry.pop('last_failure',None)
                entry.pop('disabled_epoch',None)
                entry['enabled']=True
                retired=True
        if retired:
            save_state(state)
        return state, None
    except (OSError, ValueError, TypeError) as error:
        return None, 'Unreadable local optimization: ' + str(error)


def save_state(state):
    save(state_path(), state)


def prompt_key(prompt):
    return hashlib.sha256(prompt.encode('utf-8')).hexdigest()


def remember_conditioning(state, prompt, condition, encoding):
    """Copy small verified conditioning; model/video artifacts are never removed."""
    if encoding.get('success') is not True or encoding.get('conditioning_shape', [0, 0])[1:] != [5120]:
        raise ValueError('Cannot cache unsuccessful or incompatible H3 conditioning')
    content = digest(condition)
    entries = state.setdefault('conditioning', {})
    size = Path(condition).stat().st_size
    if prompt_key(prompt) not in entries and (len(entries) >= CACHE_ENTRIES or
            sum(e.get('bytes', 0) for e in entries.values()) + size > CACHE_BYTES):
        return False
    target = state_path().parent / 'conditioning' / (content + '.pt')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if digest(target) != content:
            raise ValueError('Existing conditioning cache is damaged')
    else:
        temporary = target.with_suffix('.partial')
        shutil.copyfile(condition, temporary)
        if digest(temporary) != content:
            raise ValueError('Conditioning changed while copying')
        temporary.replace(target)
    state.setdefault('conditioning', {})[prompt_key(prompt)] = {
        'path': str(target), 'sha256': content, 'bytes': target.stat().st_size,
        'encoding': copy.deepcopy(encoding), 'created_epoch': time.time()}
    return True


def cached_conditioning(state, prompt, destination):
    entry = state.get('conditioning', {}).get(prompt_key(prompt))
    if not entry:
        return None
    started = time.perf_counter()
    # A stale or modified cache entry is a miss, never an unchecked tensor load.
    try:
        source = Path(entry['path'])
        if (not source.is_file() or source.stat().st_size != entry['bytes'] or digest(source) != entry['sha256']
                or entry['encoding'].get('success') is not True
                or entry['encoding'].get('conditioning_shape', [])[1:] != [5120]):
            return None
    except (KeyError, TypeError, OSError, AttributeError):
        return None
    shutil.copyfile(source, destination)
    from .encoder_diagnostics import cached_metrics
    result = dict(cached_metrics(copy.deepcopy(entry['encoding'])), output=str(destination), load_seconds=0.,
                  work_seconds=time.perf_counter() - started, cache_hit=True,
                  cache_sha256=entry['sha256'], scope='Reused validated local native H3 conditioning; no encoder model loaded')
    for name in ('torch_peak_allocated_bytes', 'torch_peak_reserved_bytes'):
        result[name] = 0
    return result


def compatible_profile(entry, base, canvas, tokens):
    if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
        return False
    reference_context = {key: canvas.get(key, 0) for key in ('reference_video_tokens', 'reference_audio_tokens')}
    if any(reference_context.values()) and entry.get('reference_context') != reference_context:
        return False
    if not entry.get('enabled') or entry.get('geometry') != {k: canvas[k] for k in ('width', 'height', 'frames')}:
        return False
    if tokens != entry.get('text_tokens') or base['decoder'] != entry.get('decoder'):
        return False
    patch = entry.get('patch', {})
    if not isinstance(patch, dict) or not patch or not set(patch).issubset(PATCH_KEYS):
        return False
    baseline = entry.get('base_engine')
    if not isinstance(baseline, dict):
        return False
    if PLACEMENT_KEYS.issubset(patch):
        # Live RAM changes the default pin budget almost every request. Reuse
        # measured placement only when arithmetic matches and today's budget
        # still covers its measured peak. A validated faster computation can
        # deliberately give GPU weights or host pins back to its workspace.
        if ({k: v for k, v in baseline.items() if k not in PLACEMENT_KEYS}
                != {k: v for k, v in base['engine'].items() if k not in PLACEMENT_KEYS}):
            return False
        if type(patch['resident_blocks']) is not int or not 0 <= patch['resident_blocks'] <= 50:
            return False
        if patch['resident_blocks'] < base['engine']['resident_blocks'] and not measured_compute_gain(entry):
            return False
    elif baseline != base['engine']:
        return False
    from .compute_budget import CHOICES, valid_parallelism
    choices = CHOICES
    for k, v in patch.items():
        if k in ('prefetch', 'attention_cpu_outputs', 'grouped_attention_outputs', 'fp8_ff_recompute'):
            valid = type(v) is bool
        elif k == 'resident_blocks':
            valid = type(v) is int and 0 <= v <= 50
        elif k == 'pin_host_gb':
            valid = type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 22
        else:
            valid = type(v) is int and v in choices[k]
        if not valid:
            return False
    applied = dict(base['engine'], **patch)
    if not valid_parallelism(applied):
        return False
    if applied.get('grouped_attention_outputs') and not applied.get('attention_cpu_outputs'):
        return False
    if PLACEMENT_KEYS.issubset(patch):
        # More available RAM should not be held to an older, smaller pin budget.
        # Moving weights onto the GPU legitimately reduces how much needs pinning.
        useful_pin = min(base['engine']['pin_host_gb'], (50 - patch["resident_blocks"]) * BLOCK_BYTES / 1e9)
        if patch['pin_host_gb'] + .001 < useful_pin and not measured_compute_gain(entry):
            return False
    for name in ('required_gpu_bytes', 'required_ram_bytes'):
        value = entry.get(name)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            return False
    validation = entry.get('validation', {})
    if not isinstance(validation, dict):
        return False
    return (base['gpu_budget_gb'] * 1e9 >= entry.get('required_gpu_bytes', math.inf)
            and base['inference_ram_budget_gb'] * 1e9 >= entry.get('required_ram_bytes', math.inf)
            and validation.get('bitwise_latents') is True
            and validation.get('decoded_output_equal') == {'rgb.npy': True, 'audio.npy': True}
            and validation.get('full_request') is True
            and type(validation.get('completed_steps')) is int and validation['completed_steps'] == 8
            and validation.get('memory_checked') is True)


def measured_compute_gain(entry):
    """Allow resource tradeoffs only with retained repeat and full-run evidence.

    Merely filling memory is not a benefit. Older profiles without this timing
    receipt keep their existing placement rules; quality gates remain separate.
    """
    baseline_engine = entry.get('base_engine', {})
    if not any(name not in PLACEMENT_KEYS and value != baseline_engine.get(name)
               for name, value in entry.get('patch', {}).items()):
        return False
    measured = entry.get('performance')
    if not isinstance(measured, dict):
        return False
    baseline = measured.get('baseline_step_seconds')
    candidate = measured.get('candidate_step_seconds')
    if not isinstance(baseline, list) or len(baseline) < 2 or not isinstance(candidate, list) or len(candidate) < 2:
        return False
    if not all(_positive(value) for value in baseline + candidate):
        return False
    before, after = measured.get('baseline_full_sample_seconds'), measured.get('candidate_full_sample_seconds')
    return (_positive(before) and _positive(after) and after < before
            and max(candidate) < min(baseline) * .95)


def apply_profile(state, base, canvas, tokens):
    for entry in reversed(state.get('profiles', [])):
        if compatible_profile(entry, base, canvas, tokens):
            result = copy.deepcopy(base)
            result['engine'].update(entry['patch'])
            if 'policy' in result:
                result['policy']['engine'] = dict(result['engine'])
            return result, entry['id']
    return base, None


def retired_failure(error):
    """Legacy device/exit incidents are not evidence against a tuned profile."""
    text=str(error).lower()
    from .adaptive import classify_failure
    return ('durable local attempt history' in text or 'attemptblocked' in text
            or classify_failure(error)['outcome']=='discarded')


def disable_profile(identifier, error):
    if retired_failure(error):
        return False
    state, _ = load_state()
    changed=False
    if state:
        for entry in state.get('profiles', []):
            if isinstance(entry, dict) and entry.get('id') == identifier:
                entry.update(enabled=False, last_failure=str(error), disabled_epoch=time.time())
                changed=True
        save_state(state)
    return changed


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _complete_peak(row, base):
    """A probe or another configuration cannot establish full-request headroom."""
    measured = row.get('engine', {})
    if row.get('status') != 'complete' or measured.get('success') is not True:
        return None, 'No successful complete request; probe peaks are not capacity evidence'
    steps = measured.get('step_seconds', [])
    if not isinstance(steps, list) or len(steps) != 8 or not all(_positive(v) for v in steps):
        return None, 'Eight measured steps are required; no linear peak extrapolation is used'
    reported = row.get('request', {}).get('profile', {}).get('engine', measured.get('config', {}))
    if any(reported.get(k) != v for k, v in base['engine'].items()):
        return None, 'Completed request used a different engine configuration'
    canvas = row.get('geometry', {})
    if (not all(k in canvas for k in ('width', 'height', 'frames')) or
            any(measured.get('geometry', {}).get(k) != canvas[k] for k in ('width', 'height', 'frames'))):
        return None, 'Completed request geometry is missing or inconsistent'
    gpu = row.get('gpu', {})
    if gpu.get('sampling_errors') or not gpu.get('samples') or not _positive(gpu.get('gpu_peak_bytes')):
        return None, 'Whole-request GPU observations are incomplete'
    peak = gpu['gpu_peak_bytes']
    reserved = measured.get('torch_peak_reserved_bytes')
    if _positive(reserved):
        peak = max(peak, reserved)
    return peak, 'Matched complete eight-step request; maximum of reserved and whole-device peak'


def _owned_working_memory(sample, hardware, bound):
    """Current worker memory already excluded from available RAM, counted once.

    Clean mapped pages may already be credited in availability. Never add RSS,
    private commit or raw Linux PSS; the current owned working memory is also
    bounded by the complete matching request's peak.
    """
    sample = sample or {}
    system = hardware.get('system')
    if system == 'Windows' and sample.get('guard_metric') == 'tree private working set':
        used = sample.get('guard_bytes')
    elif system == 'Linux' and sample.get('guard_metric') == 'PSS':
        # inference_guard_bytes is an upper bound and may contain clean file
        # pages when their protection counters are incomplete. Only measured
        # non-file pages establish bytes not already credited in availability.
        used = sample.get('nonfile_pss_bytes')
    else:
        return 0
    return min(used, bound) if _positive(used) and _positive(bound) else 0


def completed_placement_baseline(row, base):
    """Keep usable full-request evidence across live placement-budget rounding.

    The caller has already validated the local software/model identity and
    artifacts. Only placement may differ; fresh budgets still bound the measured
    whole request. This selects a search baseline, not a new user profile.
    """
    previous = row.get('request', {}).get('profile', {})
    engine = previous.get('engine', {})
    if not isinstance(engine, dict):
        return base, None
    changed = {k for k in set(engine) | set(base['engine']) if engine.get(k) != base['engine'].get(k)}
    if not changed or not changed <= PLACEMENT_KEYS or previous.get('decoder') != base['decoder']:
        return base, None
    matched = copy.deepcopy(base)
    matched['engine'] = copy.deepcopy(engine)
    peak, _ = _complete_peak(row, matched)
    ram = row.get('ram', {})
    system = base.get('policy', {}).get('hardware', {}).get('system')
    if system == 'Windows' and ram.get('ram_guard_metric') == 'tree private working set':
        working = ram.get('process_tree_peak_guard_bytes')
    elif system == 'Linux' and ram.get('ram_guard_metric') == 'PSS':
        working = ram.get('inference_peak_working_pss_bytes')
    else:
        return base, None
    if (peak is None or peak > base['gpu_budget_gb'] * 1e9 or not _positive(working)
            or working > base['inference_ram_budget_gb'] * 1e9
            or ram.get('process_tree_guard_complete') is not True
            or not _positive(ram.get('ram_observation_samples'))):
        return base, None
    if 'policy' in matched:
        matched['policy']['engine'] = dict(engine)
    return matched, dict(reason='Start from the complete local placement so live budget rounding does not discard its measurements',
                         placement_fields=sorted(changed), complete_gpu_peak_bytes=peak,
                         complete_working_ram_bytes=working,
                         scope='Search baseline only; current budgets and full candidate validation still apply')


def _host_cache_trial(row, base, live_ram_available, pinning_limit_bytes, live_ram_sample):
    """Use measured host staging to nominate storage, never certify it.

    Pinning already-cached weights barely helps. Retaining weights which would
    otherwise be read from disk each step is a different operation. Use the
    complete working-memory peak (which includes pinned pages), not reclaimable
    file cache or the requested, partly unused pin budget. Windows must supply
    its private working set; RSS and private commit are not interchangeable.
    """
    engine = base['engine']
    measured, ram = row.get('engine', {}), row.get('ram', {})
    offload = measured.get('offload', {})
    reader = offload.get('disk_read_ahead', {})
    hardware = base.get('policy', {}).get('hardware', {})
    if (not engine.get('stream_weights')
            or ram.get('process_tree_guard_complete') is not True
            or not _positive(ram.get('ram_observation_samples'))):
        return None
    if hardware.get('system') == 'Linux':
        if (ram.get('ram_guard_metric') != 'PSS' or reader.get('enabled') is not True
                or reader.get('failure') or not _positive(reader.get('read_bytes'))):
            return None
        working = ram.get('inference_peak_working_pss_bytes')
        waited = reader.get('wait_seconds')
        evidence = {'inference_peak_working_pss_bytes': working,
                    'disk_wait_seconds': waited, 'bottleneck': 'disk reads'}
    elif hardware.get('system') == 'Windows':
        if ram.get('ram_guard_metric') != 'tree private working set':
            return None
        working = ram.get('process_tree_peak_guard_bytes')
        waited = offload.get('host_stage_seconds')
        evidence = {'private_working_set_peak_bytes': working,
                    'host_stage_seconds': waited, 'bottleneck': 'host weight staging',
                    'scope': 'Host staging includes RAM copies and file reads; it is not physical disk I/O'}
    else:
        return None
    sample = measured.get('sample_seconds')
    if not _positive(sample) or not _positive(waited) or not .35 * sample <= waited <= sample:
        return None
    pinned = measured.get('config', {}).get('pinned_model_bytes')
    pinned_layers, streamed = offload.get('pinned_layer_count'), offload.get('streamed_layer_count')
    if (not _positive(working) or type(pinned) not in (int, float)
            or not math.isfinite(pinned) or pinned < 0 or pinned > working
            or type(pinned_layers) is not int or pinned_layers < 0
            or (pinned_layers == 0) != (pinned == 0)
            or type(streamed) is not int or streamed <= 0
            or pinned_layers + streamed != 50 - engine['resident_blocks']):
        return None
    # A zero-cache baseline is useful evidence too. With no retained layer to
    # measure, use the same block-size estimate as placement, then require a
    # complete trial to measure the actual allocation and speed.
    layer_bytes = pinned / pinned_layers if pinned_layers else BLOCK_BYTES
    limit = base['inference_ram_budget_gb'] * 1e9
    owned = 0
    if live_ram_available is not None:
        if not _positive(live_ram_available):
            return None
        reserve = max(GiB, base.get('policy', {}).get('ram_system_reserve_bytes', GiB))
        owned = _owned_working_memory(live_ram_sample, hardware, working)
        limit = min(limit, live_ram_available + owned - reserve)
    # The policy already removed OS growth headroom. Keep another 256 MiB
    # within its process budget for run-to-run variation. Use the measured
    # remainder directly: a 1 GiB step cap makes a disk-bound user reload the
    # whole model repeatedly to explore capacity already established locally.
    # Count growth from actual retained bytes, not a rounded plan which
    # can hide almost a whole unused block's worth of apparent spare memory.
    growth = max(0., limit - working - .25 * GiB)
    target = min(22e9, (50 - engine['resident_blocks']) * BLOCK_BYTES, pinned + growth)
    if pinning_limit_bytes is not None:
        target = min(target, pinning_limit_bytes)
    target = math.floor(target / 1e6) * 1e6
    if target < pinned + layer_bytes + 2**20 or target / 1e9 <= engine['pin_host_gb']:
        return None
    evidence.update(sample_seconds=sample, staging_wait_fraction=waited / sample,
                    actual_pinned_bytes=pinned, candidate_pin_bytes=int(target),
                    minimum_extra_layer_bytes=layer_bytes,
                    layer_size_source='retained layers' if pinned_layers else 'placement estimate',
                    live_owned_working_bytes=owned,
                    ram_budget_bytes=int(limit), additional_working_margin_bytes=GiB // 4,
                    local_pinning_limit_bytes=pinning_limit_bytes,
                    warning='Overlap is not additive wall time; a full repeated request must establish the gain')
    if hardware.get('system') == 'Linux':
        evidence['disk_wait_fraction'] = waited / sample
    return ({'pin_host_gb': target / 1e9, 'resident_blocks': engine['resident_blocks']}, evidence)


def candidate_plan(row, base, maximum=2, *, live_gpu_free=None, live_ram_available=None,
                   reject=None, pinning_limit_bytes=None, live_ram_sample=None):
    """Predict worthwhile trials, with reasons; this never certifies a profile.

    ``reject(patch)`` optionally applies caller-specific resource constraints. Shared
    measurements only rank hypotheses: both numerical changes and isolated
    kernel successes still require a complete local request and bitwise checks.
    ``pinning_limit_bytes`` is a local observed ceiling, not free host memory.
    ``live_ram_sample`` is a fresh observation of the current tuning worker,
    taken alongside ``live_ram_available``; historical peaks cannot replace it.
    """
    if type(maximum) is not int or maximum < 0:
        raise ValueError('maximum must be a non-negative integer')
    if pinning_limit_bytes is not None and (type(pinning_limit_bytes) not in (int, float)
            or not math.isfinite(pinning_limit_bytes) or pinning_limit_bytes < 0):
        raise ValueError('pinning_limit_bytes must be a finite non-negative local observation')
    engine = base['engine']
    peak, peak_reason = _complete_peak(row, base)
    free = max(0, base['gpu_budget_gb'] * 1e9 - peak) if peak is not None else None
    from .system import memory_peak, windows
    if free is not None and live_gpu_free is not None:
        free = max(0, min(free, live_gpu_free - (1 if windows() else .5) * GiB))
    options, rejected = [], []
    evidence_source = 'benchmarks/2026-09-12-windows-ada-sampling-speed.json'

    def offer(patch, reason, observation, *, numerical='requires-bitwise-validation', risk='recoverable-memory', source=evidence_source):
        if any(item['patch'] == patch for item in options + rejected):
            return
        record = {'patch': patch, 'reason': reason, 'observation': observation,
                  'source': source, 'numerical_class': numerical, 'risk': risk,
                  'validation_required': 'complete-local-request-and-bitwise-output',
                  'evidence_scope': 'Candidate prediction only; not certification for this device'}
        blocked = reject(dict(patch)) if reject is not None else None
        if blocked:
            record['rejected_reason'] = str(blocked)
            rejected.append(record)
        else:
            options.append(record)

    storage = _host_cache_trial(row, base, live_ram_available, pinning_limit_bytes, live_ram_sample) if peak is not None else None
    if storage is not None:
        patch, observation = storage
        offer(patch, 'Prioritize retained host weights: this complete local request spends at least 35% of sampling on ' + observation['bottleneck'],
              observation, numerical='placement-neutral', source='complete-local-request')

    # Cached H2D traffic and repeated host staging are different bottlenecks.
    # The offloader measures CPU staging on both platforms, even without the
    # RAM evidence needed to grow the host cache. When that dominates a complete
    # request, spend measured GPU room on weights before changing arithmetic.
    # This only nominates a full local trial: staging can include
    # RAM copies and cached file reads, so do not label it physical disk I/O.
    offload = row.get('engine', {}).get('offload', {})
    sample = row.get('engine', {}).get('sample_seconds')
    staged = offload.get('host_stage_seconds')
    streamed = offload.get('streamed_layer_count')
    if (free is not None and engine.get('stream_weights')
            and type(streamed) is int and 0 < streamed <= 50 - engine['resident_blocks']
            and _positive(sample) and _positive(staged) and .35 * sample <= staged <= sample):
        extra = min(streamed, max(0, int((free - .25 * GiB) / BLOCK_BYTES)))
        if extra:
            resident = engine['resident_blocks'] + extra
            offer({'resident_blocks': resident,
                   'pin_host_gb': math.floor(min(engine['pin_host_gb'], (50 - resident) * BLOCK_BYTES / 1e9) * 1000) / 1000},
                  'Prioritize GPU weight residency: this complete local request spends over 35% of sampling staging streamed host weights',
                  {'complete_peak_bytes': peak, 'live_headroom_bytes': free,
                   'sample_seconds': sample, 'host_stage_seconds': staged,
                   'host_stage_fraction': staged / sample, 'extra_blocks': extra,
                   'additional_gpu_margin_bytes': GiB // 4,
                   'scope': 'Host staging includes RAM copies and file reads; it is not a physical disk measurement'},
                  numerical='placement-neutral', source='complete-local-request')

    from .compute_budget import spare_compute_trials
    for patch, observation in spare_compute_trials(row, engine, peak, free):
        observation.update(complete_peak_bytes=peak, headroom_bytes=free,
                           memory_estimate_scope='Trial bound only; complete local validation required')
        offer(patch, observation['reason'], observation, source='complete-local-request',
              risk=('unvalidated-concurrency' if 'head_parallelism' in patch else
                    'numerical-validation-required' if 'window_batch' in patch else 'recoverable-memory'))

    # A hard sub-10 GiB tier missed this complete 12/32 result by only 34 MiB.
    # Nominate the measured high-value knobs independently; a shared result
    # predicts a candidate, never certifies another GPU or numerical equality.
    if (engine.get('linear_compute') == 'native-fp8' and engine.get('attention_cpu_outputs')
            and engine.get('grouped_attention_outputs') and 0 < engine['head_chunk'] < 8
            and engine['attention'].split('/')[-1] not in ('fa2', 'fa4')):
        offer({'head_chunk': 8}, 'Test wider head groups before growing weight residency; keep the current window grouping',
              {'device': 'Linux Blackwell, 12 GiB whole-GPU / 28 GiB RAM cgroup',
               'geometry': {'width': 1344, 'height': 768, 'frames': 243},
               'baseline_seconds_per_nfe': 39.404771428571884, 'candidate_seconds_per_nfe': 24.80211799457902,
               'sampling_speedup': 1.589, 'baseline_whole_gpu_peak_bytes': 10592059392,
               'candidate_whole_gpu_peak_bytes': 10552213504,
               'bitwise_equal': False, 'rgb_psnr_db': 17.737965055277115,
               'scope': 'One complete matched request; equal memory use is not proof for other geometries. '
                        'Head 8/window 1 was slightly faster than 8/4; this is not a repeatability claim.'},
              source='benchmarks/2026-09-14-head-window-capacity.json', risk='numerical-validation-required')
        if engine['window_batch'] < 4:
            offer({'window_batch': 4}, 'Test window batching independently when a wider head group changes the prediction',
                  {'baseline_seconds_per_nfe': 39.404771428571884, 'candidate_seconds_per_nfe': 33.56010567014372,
                   'sampling_speedup': 1.174, 'bitwise_equal': False, 'rgb_psnr_db': 17.377521730600915,
                   'scope': 'Same complete 12/32 request, head 2 unchanged. First-step rejection saves time; full validation is still required.'},
                  source='benchmarks/2026-09-14-head-window-capacity.json', risk='numerical-validation-required')

    # Compute comes before unmeasured occupancy, after a locally measured disk
    # bottleneck if present. In the matching Linux
    # complete run head 4 and head 2 reserved exactly the same bytes, so the old
    # universal 3 GiB admission threshold had no evidence.
    if engine['head_chunk'] < 16:
        offer({'head_chunk': min(16, max(4, engine['head_chunk'] * 2))},
              'Test head parallelism when no complete local storage bottleneck takes priority',
              'Head 2 to 4: complete Linux Blackwell sample 270.5 to 196.0 s (1.38x), identical peak',
              risk='numerical-validation-required')
    if engine.get('attention_cpu_outputs'):
        offer({'attention_cpu_outputs': False, 'grouped_attention_outputs': False},
              'Remove attention host round-trips; recoverable OOM must descend without changing the request',
              '1.26x in three-step Ada probes only; complete-request peak unmeasured, no slope extrapolation')
    if engine.get('fp8_ff_recompute') and engine['linear_compute'] == 'native-fp8':
        offer({'fp8_ff_recompute': False}, 'Avoid recomputing FF activations before increasing weight storage',
              '1.046x in Ada probes; reserved bytes unchanged there; complete combined change also succeeded')
    if engine['attention'].split('/')[-1] not in ('fa2', 'fa4') and engine['window_batch'] < 8:
        offer({'window_batch': 8}, 'Try the fastest measured looped window batch; never enable packed varlen implicitly',
              'Isolated looped attention 1.20x, about 4% sampling; full local equivalence remains to be measured',
              risk='numerical-validation-required')
    if engine['linear_compute'] == 'native-fp8' and engine['ff_chunk'] < 4096:
        offer({'ff_chunk': 4096}, 'Test fewer FF chunks after the measured high-value compute changes',
              'No universal full-request speedup established for this chunk size')
    if engine['projection_chunk'] < 4096:
        offer({'projection_chunk': 4096}, 'Test larger projection chunks after head and attention-output changes',
              'No universal full-request speedup established for this chunk size')
    if not engine['prefetch'] and engine['resident_blocks'] < 50:
        offer({'prefetch': True}, 'Try overlapping remaining weight transfers after compute candidates',
              'Transformer weight H2D was only 6.78 s of 878.4 s sampling on the Ada laptop')

    # Placement remains eligible, but a full-run peak is the minimum evidence
    # for turning unused bytes into more residency. Do not update this estimate
    # from a one-step probe, a truncated request or a changed chunk configuration.
    if free is not None:
        room = min(50 - engine['resident_blocks'], max(0, int((free - .25 * GiB) / BLOCK_BYTES)))
        for extra in ([room, 2] if room > 2 else [room]):
            if extra:
                resident = engine['resident_blocks'] + extra
                offer({'resident_blocks': resident,
                       'pin_host_gb': math.floor(min(engine['pin_host_gb'], (50 - resident) * BLOCK_BYTES / 1e9) * 1000) / 1000},
                      'Spend measured whole-request headroom only after higher-value compute trials',
                      {'complete_peak_bytes': peak, 'live_headroom_bytes': free, 'extra_blocks': extra,
                       'speed_evidence': 'Cached Ada weights showed approximately zero residency gain; this does not establish the value when weights are read from disk'},
                      numerical='placement-neutral')
        ram_peak = memory_peak(row.get('ram', {}))
        if _positive(ram_peak):
            ram_free = base['inference_ram_budget_gb'] * 1e9 - ram_peak
            if live_ram_available is not None:
                owned = _owned_working_memory(live_ram_sample, base.get('policy', {}).get('hardware', {}), ram_peak)
                ram_free = min(ram_free, live_ram_available + owned - ram_peak - (2 if windows() else 1) * GiB)
            pin = min(22., (50 - engine['resident_blocks']) * BLOCK_BYTES / 1e9,
                      engine['pin_host_gb'] + min(GiB, max(0, ram_free - GiB)) / 1e9)
            if pinning_limit_bytes is not None:
                pin = min(pin, pinning_limit_bytes / 1e9)
            if pin > engine['pin_host_gb'] + .2:
                offer({'pin_host_gb': math.floor(pin * 1000) / 1000, 'resident_blocks': engine['resident_blocks']},
                      'Pinning is last: only test bounded host storage after higher-value candidates',
                      {'sampling_gain_measured': .0057, 'local_pinning_limit_bytes': pinning_limit_bytes,
                       'warning': 'Free RAM does not establish the platform locked-memory ceiling'},
                      numerical='placement-neutral')
    return {'candidates': options[:maximum], 'rejected': rejected,
            'memory_evidence': {'complete_peak_bytes': peak, 'headroom_bytes': free, 'reason': peak_reason}}


def candidates(row, base, maximum=2, *, live_gpu_free=None, live_ram_available=None,
               reject=None, pinning_limit_bytes=None, live_ram_sample=None):
    """Backward-compatible patch list; use candidate_plan for auditable reasons."""
    plan = candidate_plan(row, base, maximum, live_gpu_free=live_gpu_free,
                          live_ram_available=live_ram_available, reject=reject,
                          pinning_limit_bytes=pinning_limit_bytes, live_ram_sample=live_ram_sample)
    return [item['patch'] for item in plan['candidates']]
