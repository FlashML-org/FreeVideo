"""Validate retained sampling results before a decoder-only retry; Torch-free IO."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile


# These change where immutable weights live, not the requested sampling math.
ENGINE_PLACEMENT = frozenset(('resident_blocks', 'pin_host_gb', 'pin_host_weights',
    'preload_host', 'stream_weights', 'prefetch', 'offload_refiner', 'adaln_disk_cache'))
DECODER_PLACEMENT = frozenset(('offload', 'prefetch', 'preload', 'pin_weights',
    'stream_output', 'stream_weights', 'resident_blocks'))
# Compute partitions change floating-point reduction order, not what a pass
# computes. An OOM retry shrinks them for the second pass, so a retained first
# pass stays valid across them.
COMPUTE_PARTITION = frozenset(('head_chunk', 'window_batch', 'ff_chunk', 'projection_chunk', 'head_parallelism',
    'attention_cpu_outputs', 'grouped_attention_outputs', 'fp8_ff_recompute', 'residual_offload', 'query_chunk'))
SAMPLE_FIELDS = ('config', 'sample_seconds', 'step_seconds', 'finite_latents', 'geometry',
    'conditioning_info', 'conditioning_shape', 'offload', 'residual_offload_steps',
    'attention_backend_calls', 'head_execution', 'text_refinement', 'sampling_memory',
    'load_seconds', 'load_breakdown', 'load_peak_allocated_bytes', 'load_peak_reserved_bytes',
    'torch_peak_allocated_bytes', 'torch_peak_reserved_bytes', 'latent_save_seconds', 'sample_metrics_scope',
    'sampling_plan', 'sampling_passes', 'latent_upscale', 'refinement', 'fp8_kernel_calls')


def completed_sampling(metrics):
    """A retained completion receipt, including interrupted sampler cleanup."""
    if not isinstance(metrics, dict) or metrics.get('success') is not False:
        return False
    phase = metrics.get('phase')
    if phase not in ('decode', 'sample_finalize'):
        return False
    if phase == 'sample_finalize' and metrics.get('sampling_checkpoint_complete') is not True:
        return False
    config, steps = metrics.get('config'), metrics.get('step_seconds')
    from .two_pass import steps as expected_steps
    try:
        total = expected_steps(metrics.get('sampling_plan'))
    except (ValueError, AttributeError, TypeError):
        return False
    return (metrics.get('finite_latents') is True and isinstance(config, dict)
            and type(config.get('steps')) is int
            and config['steps'] == (metrics.get('sampling_plan') or {}).get('base_steps', 8)
            and isinstance(steps, list) and len(steps) == total
            and all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in steps)
            and isinstance(metrics.get('sampling_provenance'), dict) and bool(metrics['sampling_provenance']))


def save_sampling(value, artifacts, torch):
    """Expose a complete archive atomically; a failed save cannot replace it."""
    artifacts = Path(artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='latents.', suffix='.tmp', dir=artifacts, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        torch.save(value, temporary)
        temporary.replace(artifacts / 'latents.pt')
    finally:
        temporary.unlink(missing_ok=True)


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def sampling_provenance(request):
    """Bind the result to the exact conditioning, model manifest and math."""
    from .paths import base_path
    options = request['engine_options']
    if (type(request.get('seed')) is not int or not isinstance(request.get('geometry'), dict)
            or not isinstance(options, dict) or not isinstance(request.get('decoder_options'), dict)):
        raise ValueError('Invalid sampling request identity')
    result = dict(version=1, seed=request['seed'], geometry=request['geometry'],
        cache=str(Path(request['cache']).resolve()),
        cache_manifest_sha256=_digest(Path(request['cache']) / 'manifest.json'),
        conditioning=str(Path(request['conditioning']).resolve()),
        conditioning_sha256=_digest(request['conditioning']),
        base=str(Path(options.get('base') or base_path()).resolve()),
        engine_math={key: value for key, value in options.items() if key not in ENGINE_PLACEMENT},
        decoder_math={key: value for key, value in request['decoder_options'].items() if key not in DECODER_PLACEMENT})
    if 'sampling_plan' in request:
        from .two_pass import steps
        steps(request['sampling_plan'])
        result.update(version=2, sampling_plan=request['sampling_plan'])
        if request['sampling_plan'].get('upscaler_sha256'):
            result['upscaler_sha256'] = _digest(request['upscaler_checkpoint'])
            if result['upscaler_sha256'] != request['sampling_plan']['upscaler_sha256']:
                raise ValueError('Latent upscaler differs from the planned checkpoint')
    return result


def load_saved_sampling(request, torch):
    """Load verified CPU tensors; caller controls CUDA admission and transfer."""
    resume = request['resume_decode']
    if not isinstance(resume, dict) or any(not isinstance(resume.get(key), str) or not resume[key]
                                           for key in ('latents', 'metrics', 'request')):
        raise ValueError('Invalid decoder retry source')
    paths = {key: Path(resume[key]).resolve() for key in ('latents', 'metrics', 'request')}
    if len({path.parent for path in paths.values()}) != 1 or len(set(paths.values())) != 3:
        raise ValueError('Decoder retry artifacts must belong to one retained attempt')
    prior = json.loads(paths['request'].read_text(encoding='utf-8'))
    previous = json.loads(paths['metrics'].read_text(encoding='utf-8'))
    if not isinstance(prior, dict) or not isinstance(previous, dict):
        raise ValueError('Invalid retained sampling records')
    if not completed_sampling(previous):
        raise ValueError('Decoder retry requires all planned finite sampling steps')
    provenance = sampling_provenance(request)
    if provenance != sampling_provenance(prior) or provenance != previous.get('sampling_provenance'):
        raise ValueError('Retained sampling does not match this request and its current inputs')
    canvas = request['geometry']
    if any(previous.get('geometry', {}).get(key) != canvas[key] for key in ('width', 'height', 'frames', 'fps')):
        raise ValueError('Retained sampling geometry differs from the request')
    value = torch.load(str(paths['latents']), map_location='cpu', weights_only=True)
    if (not isinstance(value, dict) or type(value.get('seed')) is not int or value['seed'] != request['seed']
            or value.get('geometry') != canvas or value.get('sampling_provenance') != provenance):
        raise ValueError('Retained latent provenance differs from the sampling receipt')
    width, height, frames, fps = (canvas[key] for key in ('width', 'height', 'frames', 'fps'))
    if (any(type(n) is not int or n <= 0 for n in (width, height, frames, fps))
            or width % 32 or height % 32 or frames % 17 != 5 or fps != 24):
        raise ValueError('Invalid retained H3 sampling geometry')
    # Pinned H3 contract: video C=24, spatial stride=16; stereo audio C=32
    # with 40 latent frames/s. These are also the official sampler's layouts.
    shapes = {'video': (1, 24, (frames - 5) // 17 * 5 + 2, height // 16, width // 16),
              'audio': (2, 32, round(frames / fps * 40))}
    for key, shape in shapes.items():
        tensor = value.get(key)
        if (not isinstance(tensor, torch.Tensor) or tensor.device.type != 'cpu'
                or tuple(tensor.shape) != shape or not tensor.is_floating_point()
                or not bool(torch.isfinite(tensor).all())):
            raise ValueError('Retained %s latents are malformed or non-finite' % key)
    source = previous.get('sampling_source_metrics')
    if not isinstance(source, dict):
        source = {key: previous[key] for key in SAMPLE_FIELDS if key in previous}
    metrics = {key: copy.deepcopy(previous[key]) for key in SAMPLE_FIELDS if key in previous
               and not key.startswith(('load_', 'torch_peak_')) and key != 'latent_save_seconds'}
    metrics.update(sampling_reused=True, sampling_source_attempt=previous.get('sampling_source_attempt',
        prior.get('resource_attempt')), sampling_provenance=provenance,
        sampling_source_metrics=copy.deepcopy(source), load_seconds=0.,
        sampling_metrics_scope='Reused completed sampling; sampling times and peaks belong to the source attempt.',
        work_seconds_scope='Current decoder retry, including retained latent validation and transfer only.')
    return value['video'], value['audio'], metrics, Path(provenance['base'])


def retain_latents(source, artifacts):
    """Keep the source attempt intact and satisfy final artifact validation."""
    source, artifacts = Path(source), Path(artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    destination = artifacts / 'latents.pt'
    try:
        os.link(source, destination)
    except FileExistsError:
        if not os.path.samefile(source, destination):
            raise
    except OSError:
        with source.open('rb') as incoming, destination.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)


def refine_provenance(request):
    """Identity of a completed first pass: the sampling identity without compute partitions or decoding."""
    result = sampling_provenance(request)
    if result.get('version') != 2:
        raise ValueError('A retained first pass requires a two-pass sampling plan')
    result['engine_math'] = {key: value for key, value in result['engine_math'].items() if key not in COMPUTE_PARTITION}
    result.pop('decoder_math', None)
    return dict(result, scope='first-pass')


def save_refine_input(value, artifacts, torch):
    """Retain the upscaled, cropped first pass atomically before the second pass."""
    artifacts = Path(artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='refine-input.', suffix='.tmp', dir=artifacts, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        torch.save(value, temporary)
        temporary.replace(artifacts / 'refine-input.pt')
    finally:
        temporary.unlink(missing_ok=True)


def save_first_pass(value, artifacts, torch):
    """Retain a preview's first pass atomically: the input of a later upscale."""
    artifacts = Path(artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='first-pass.', suffix='.tmp', dir=artifacts, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        torch.save(value, temporary)
        temporary.replace(artifacts / 'first-pass.pt')
    finally:
        temporary.unlink(missing_ok=True)


def first_pass_complete(metrics):
    """A finished preview retained its whole first pass, before the latent upscale."""
    if (not isinstance(metrics, dict) or metrics.get('success') is not True or metrics.get('preview') is not True
            or metrics.get('first_pass_checkpoint_complete') is not True
            or not isinstance(metrics.get('refine_provenance'), dict)):
        return False
    plan, passes = metrics.get('sampling_plan'), metrics.get('sampling_passes')
    if not isinstance(plan, dict) or not plan.get('enabled') or not isinstance(passes, list) or len(passes) != 1:
        return False
    first = passes[0]
    if not isinstance(first, dict):
        return False
    steps, seconds = first.get('step_seconds'), first.get('sample_seconds')
    return (isinstance(steps, list) and len(steps) == plan.get('base_steps')
            and all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in steps)
            and type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0)


def load_first_pass(request, torch):
    """Load a finished preview's first pass on the CPU; the caller moves it to the GPU."""
    resume = request['resume_first_pass']
    if not isinstance(resume, dict) or any(not isinstance(resume.get(key), str) or not resume[key]
                                           for key in ('input', 'metrics', 'request')):
        raise ValueError('Invalid preview source')
    paths = {key: Path(resume[key]).resolve() for key in ('input', 'metrics', 'request')}
    if len({path.parent for path in paths.values()}) != 1 or len(set(paths.values())) != 3:
        raise ValueError('Preview artifacts must belong to one finished preview')
    prior = json.loads(paths['request'].read_text(encoding='utf-8'))
    previous = json.loads(paths['metrics'].read_text(encoding='utf-8'))
    if not isinstance(prior, dict) or not first_pass_complete(previous):
        raise ValueError('Upscaling requires a finished preview')
    provenance = refine_provenance(request)
    if provenance != refine_provenance(prior) or provenance != previous['refine_provenance']:
        raise ValueError('The preview does not match this request and its current inputs')
    canvas, first = request['geometry'], request['sampling_plan']['first']
    value = torch.load(str(paths['input']), map_location='cpu', weights_only=True)
    if (not isinstance(value, dict) or type(value.get('seed')) is not int or value['seed'] != request['seed']
            or value.get('geometry') != canvas or value.get('refine_provenance') != provenance):
        raise ValueError('Preview first-pass provenance differs from its receipt')
    width, height, frames, fps = first['width'], first['height'], first['frames'], canvas['fps']
    if (any(type(n) is not int or n <= 0 for n in (width, height, frames, fps))
            or width % 32 or height % 32 or frames % 17 != 5 or fps != 24 or frames != canvas['frames']):
        raise ValueError('Invalid preview first-pass geometry')
    # The first pass samples the smaller planned canvas: video C=24 with
    # stride 16; stereo audio C=32 at 40/s for the same duration.
    shapes = {'video': (1, 24, (frames - 5) // 17 * 5 + 2, height // 16, width // 16),
              'audio': (2, 32, round(frames / fps * 40))}
    for key, shape in shapes.items():
        tensor = value.get(key)
        if (not isinstance(tensor, torch.Tensor) or tensor.device.type != 'cpu'
                or tuple(tensor.shape) != shape or not tensor.is_floating_point()
                or not bool(torch.isfinite(tensor).all())):
            raise ValueError('Preview first-pass %s latents are malformed or non-finite' % key)
    receipt = dict(first_pass_reused=True, first_pass_source='preview', refine_provenance=provenance,
                   first_pass_source_output=prior.get('output'),
                   first_pass_scope='Reused the first pass of a finished preview; its times and peaks '
                                    'belong to that preview.')
    return value['video'], value['audio'], copy.deepcopy(previous['sampling_passes'][0]), receipt


def refine_checkpoint_complete(metrics):
    """A failed attempt retained its full first pass, latent upscale and crop."""
    if (not isinstance(metrics, dict) or metrics.get('success') is not False
            or metrics.get('refine_checkpoint_complete') is not True
            or not isinstance(metrics.get('refine_provenance'), dict)):
        return False
    plan, passes = metrics.get('sampling_plan'), metrics.get('sampling_passes')
    if not isinstance(plan, dict) or not plan.get('enabled') or not isinstance(passes, list) or not passes:
        return False
    first, lifted = passes[0], metrics.get('latent_upscale')
    if not isinstance(first, dict) or not isinstance(lifted, dict):
        return False
    steps = first.get('step_seconds')
    seconds = [first.get('sample_seconds'), lifted.get('stage_seconds')]
    return (isinstance(steps, list) and len(steps) == plan.get('base_steps')
            and all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in steps)
            and all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in seconds))


def load_refine_input(request, torch):
    """Load a verified retained first pass on the CPU; the caller moves it to the GPU."""
    resume = request['resume_refine']
    if not isinstance(resume, dict) or any(not isinstance(resume.get(key), str) or not resume[key]
                                           for key in ('input', 'metrics', 'request')):
        raise ValueError('Invalid first-pass retry source')
    paths = {key: Path(resume[key]).resolve() for key in ('input', 'metrics', 'request')}
    if len({path.parent for path in paths.values()}) != 1 or len(set(paths.values())) != 3:
        raise ValueError('First-pass retry artifacts must belong to one retained attempt')
    prior = json.loads(paths['request'].read_text(encoding='utf-8'))
    previous = json.loads(paths['metrics'].read_text(encoding='utf-8'))
    if not isinstance(prior, dict) or not refine_checkpoint_complete(previous):
        raise ValueError('First-pass retry requires a complete retained first pass')
    provenance = refine_provenance(request)
    if provenance != refine_provenance(prior) or provenance != previous['refine_provenance']:
        raise ValueError('Retained first pass does not match this request and its current inputs')
    canvas = request['geometry']
    value = torch.load(str(paths['input']), map_location='cpu', weights_only=True)
    if (not isinstance(value, dict) or type(value.get('seed')) is not int or value['seed'] != request['seed']
            or value.get('geometry') != canvas or value.get('refine_provenance') != provenance):
        raise ValueError('Retained first-pass provenance differs from its receipt')
    width, height, frames, fps = (canvas[key] for key in ('width', 'height', 'frames', 'fps'))
    if (any(type(n) is not int or n <= 0 for n in (width, height, frames, fps))
            or width % 32 or height % 32 or frames % 17 != 5 or fps != 24):
        raise ValueError('Invalid retained H3 first-pass geometry')
    # The second pass refines at the requested canvas: same layouts as the
    # final latents (video C=24, stride 16; stereo audio C=32 at 40/s).
    shapes = {'video': (1, 24, (frames - 5) // 17 * 5 + 2, height // 16, width // 16),
              'audio': (2, 32, round(frames / fps * 40))}
    for key, shape in shapes.items():
        tensor = value.get(key)
        if (not isinstance(tensor, torch.Tensor) or tensor.device.type != 'cpu'
                or tuple(tensor.shape) != shape or not tensor.is_floating_point()
                or not bool(torch.isfinite(tensor).all())):
            raise ValueError('Retained first-pass %s latents are malformed or non-finite' % key)
    first = copy.deepcopy(previous['sampling_passes'][0])
    lifted = copy.deepcopy(previous['latent_upscale'])
    receipt = dict(first_pass_reused=True, refine_provenance=provenance,
                   first_pass_source_attempt=previous.get('refine_source_attempt', prior.get('resource_attempt')),
                   first_pass_scope='Reused the retained first pass, latent upscale and crop; their times and '
                                    'peaks belong to the source attempt.')
    return value['video'], value['audio'], first, lifted, receipt
