"""Generation planning with an 8 + 3 default, without importing Torch."""
from math import gcd
from pathlib import Path

UPSCALER = dict(
    repo='LBH-123-AI/Minimax_h3_latent_Upscaler',
    revision='3f941d5d182014dd5c0a5e16330420ee2d4aa0c6',
    file='minimax_h3_latent_upscaler_3d_conv_v1/minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors',
    bytes=690592672,
    sha256='043e5a48e161610ef6c3ea974645220354d06fa618abca15f76d084812eb55c2',
    role='latent_upscaler')


def validate_steps(base_steps=8, refine_steps=3, enabled=True):
    if type(enabled) is not bool:
        raise ValueError('Two-pass generation must be a boolean')
    if type(base_steps) is not int or not 1 <= base_steps <= 32:
        raise ValueError('First-pass steps must be an integer between 1 and 32')
    if type(refine_steps) is not int or not 1 <= refine_steps <= 31:
        raise ValueError('Second-pass steps must be an integer between 1 and 31')
    if enabled and refine_steps != 3 and refine_steps >= base_steps:
        raise ValueError('Second-pass steps must be fewer than first-pass steps')


# The learned latent lift and the three refinement steps were built and checked
# for the 8-step first pass only. Any other first-pass count runs in one pass.
TWO_PASS_BASE_STEPS = 8


def one_pass_note(base_steps):
    """The sentence a request gets when its step count rules out two-pass, else None."""
    if base_steps == TWO_PASS_BASE_STEPS:
        return None
    return ('Two-pass generation runs only with %d first-pass steps; this request runs in one pass of %d step%s.'
            % (TWO_PASS_BASE_STEPS, base_steps, '' if base_steps == 1 else 's'))


# A first pass below this many pixels is too small for the learned lift: about
# a third of the clips at 768x512 and every clip at 512x512 had coloured
# bursts, and none did from 480x288 (960x544) upward. Those sizes sample both
# passes at the target size, without the lift.
SMALL_FIRST_PASS_PIXELS = 138240


# A requested size below this many pixels shows the same bursts in any pass
# layout. Such a request is rendered at a larger size of nearly the same shape
# and scaled down to the size asked for.
SMALL_TARGET_PIXELS = 131072
# How far the rendered shape may differ from the requested one; the rendered
# frames are centre-cropped to the requested shape before scaling.
RENDER_SHAPE_TOLERANCE = 0.03


def render_size(width, height):
    """Return (width, height) to render for a small requested size, else None.

    The smallest multiple-of-32 size of at least SMALL_FIRST_PASS_PIXELS whose
    shape is within RENDER_SHAPE_TOLERANCE of the requested shape.
    """
    from math import log
    from .geometry import geometry
    geometry(width, height)
    if width * height >= SMALL_TARGET_PIXELS:
        return None
    wanted = log(width / height)
    best = None
    for rw in range(256, 4097, 32):
        for rh in range(256, 4097, 32):
            pixels = rw * rh
            if pixels < SMALL_FIRST_PASS_PIXELS:
                continue
            if best is not None and pixels > best[0]:
                break
            error = abs(log(rw / rh) - wanted)
            if error <= RENDER_SHAPE_TOLERANCE and (best is None or (pixels, error) < best[:2]):
                best = (pixels, error, rw, rh)
    return best[2:] if best else None


def render_canvas(canvas):
    """Return (canvas to render, requested size or None) for a geometry() dict."""
    render = render_size(canvas['width'], canvas['height'])
    if render is None:
        return canvas, None
    from .geometry import geometry
    larger = geometry(*render, frames=canvas['frames'])
    return (dict(canvas, width=larger['width'], height=larger['height'], video_tokens=larger['video_tokens']),
            dict(width=canvas['width'], height=canvas['height']))


def sampling_canvas(canvas, *, staged=False, media=None, conditioned=False):
    """The one place that decides what canvas a request samples.

    A preview, the upscale of a preview and pre-encoded conditioning (tied to
    the requested canvas) keep the requested size.
    """
    if staged or conditioned or (media or {}).get('conditioning_info'):
        return canvas, None
    return render_canvas(canvas)


# Learned-upscale factors a level may ask for. 2 is the original half-size
# first pass; 1.5 and 1.2 sample a larger first pass (44% and 69% of the final
# pixels: 896x512 and 1120x640 for 1344x768) and lift it by that factor.
SCALES = (2, 1.5, 1.2)


def first_canvas(target, scale):
    """The first-pass canvas and its lift for a factor below 2: multiples of 32 whose
    lift by `scale` is also a multiple of 32 and covers the target (center-cropped)."""
    from fractions import Fraction
    factor = Fraction(scale).limit_denominator(10)  # exactly 3/2 or 6/5, not the binary float

    def side(length):
        units = -(-Fraction(length, 32) // factor)  # ceil(length / 32 / factor)
        units = -(-units // factor.denominator) * factor.denominator  # the lift stays a multiple of 32
        return 32 * int(units), int(32 * units * factor)
    (w, lift_w), (h, lift_h) = side(target['width']), side(target['height'])
    return dict(width=w, height=h, frames=target['frames']), dict(width=lift_w, height=lift_h, frames=target['frames'])


def plan(canvas, enabled=True, task='t2va', *, base_steps=8, refine_steps=3, staged=False, scale=2,
         schedule=None):
    """Align the smaller canvas, lift, center-crop, then refine at the target.

    Odd multiples of 32 need up to 32 extra pixels before the latent crop.
    Small canvases use a smaller factor or same-size refinement. A first pass
    below SMALL_FIRST_PASS_PIXELS is sampled at the target size instead (same-size
    refinement), unless the first pass itself is wanted (a preview or its upscale: staged).
    Memory pressure never changes this math. `scale` below 2 samples a larger
    first pass (SCALES) on canvases of at least 512 on the short side; 2 is the
    original plan, byte for byte. `schedule` names another three-step refinement
    clock (refine_schedule.SIGMAS); None is the community one.
    """
    from .geometry import geometry
    from .media_request import TASKS
    validate_steps(base_steps, refine_steps, enabled)
    if scale not in SCALES:
        raise ValueError('Two-pass upscale factor must be one of ' + ', '.join('%g' % value for value in SCALES))
    if task not in TASKS:
        raise ValueError('Unsupported two-pass conditioning task: ' + str(task))
    target = geometry(canvas['width'], canvas['height'], frames=canvas['frames'])
    shape = {key: target[key] for key in ('width', 'height', 'frames')}
    default = base_steps == 8 and (not enabled or refine_steps == 2)
    result = dict(version=1 if default else 2, requested=enabled, enabled=False, mode='vdn%d' % base_steps,
                  base_steps=base_steps, refine_steps=0, total_steps=base_steps, first=shape, second=None)
    if not enabled:
        result['reason'] = 'Two-pass generation is disabled.'
        return result
    if base_steps != TWO_PASS_BASE_STEPS:
        if not staged:
            single = plan(canvas, False, task, base_steps=base_steps)
            return dict(single, requested=True, reason=one_pass_note(base_steps))
        # A preview and its upscale keep the first pass they were made with; the
        # receipt says so, because a replay without the flag would take the one-pass rule.
        result['staged'] = True
    if min(target['width'], target['height']) < 512:
        scale = 2  # a small canvas keeps its own factor below
    if min(target['width'], target['height']) >= 512:
        first = dict(width=((target['width'] + 63) // 64) * 32,
                     height=((target['height'] + 63) // 64) * 32, frames=target['frames'])
        lift = dict(first, width=2 * first['width'], height=2 * first['height'])
    else:
        # Avoid a large field-of-view crop just to force 2x on a small input.
        w, h = target['width'] // 32, target['height'] // 32
        divisor = gcd(w, h)
        unit_w, unit_h = w // divisor, h // divisor
        multiple = max((divisor + 1) // 2, (8 + unit_w - 1) // unit_w, (8 + unit_h - 1) // unit_h)
        first = dict(width=32 * unit_w * multiple, height=32 * unit_h * multiple, frames=target['frames'])
        lift = dict(shape)
    upscaling = first != lift
    if upscaling and first['width'] * first['height'] < SMALL_FIRST_PASS_PIXELS:
        # Judged on the 2x first pass, so a smaller factor never samples less than 2x does.
        if staged:
            result['staged'] = True
        else:
            first = lift = dict(shape)
            upscaling, scale = False, 2
    if upscaling and scale != 2:
        first, lift = first_canvas(target, scale)
    if not upscaling:
        crop = dict(left=0, top=0, width=target['width'], height=target['height'])
    else:
        crop = dict(left=(lift['width'] - target['width']) // 2,
                    top=(lift['height'] - target['height']) // 2,
                    width=target['width'], height=target['height'])
    result.update(enabled=True, mode='vdn%d-lbh-tail%d' % (base_steps, refine_steps),
                  refine_steps=refine_steps, total_steps=base_steps + refine_steps,
                  first=first, second=shape, upscale_target=lift, crop=crop,
                  restart_seed_offset=1,
                  upscaler_sha256=UPSCALER['sha256'] if upscaling else None,
                  audio_policy='first_pass_preserved_with_audio_clock_conditioning',
                  reason='%d steps on a smaller canvas, learned latent upscale, then the DMD schedule tail (%d steps).' % (base_steps, refine_steps))
    if default:
        result['reason'] = '8 steps on a smaller canvas, learned latent upscale, then the original DMD8 schedule tail (2 steps).'
    if refine_steps == 3:
        from .refine_schedule import COMMUNITY, UPSCALE, sigmas
        result.update(version=3, mode='vdn%d-lbh-community3' % base_steps,
            refine_schedule=COMMUNITY, video_sigmas=list(sigmas(COMMUNITY)),
            reason='%d steps on a smaller canvas, learned latent upscale, then three independent refinement steps.' % base_steps)
        if schedule not in (None, COMMUNITY):
            # Only a finished video is upscaled this way; its first pass is the video itself.
            if schedule != UPSCALE or not staged:
                raise ValueError('Unsupported refinement schedule')
            # Only the three refinement steps are sampled.
            result.update(staged=True, mode='vdn%d-lbh-upscale3' % base_steps, refine_schedule=UPSCALE,
                video_sigmas=list(sigmas(UPSCALE)), total_steps=refine_steps,
                reason='A finished video, learned latent upscale, then three light refinement steps.')
    elif schedule is not None:
        raise ValueError('Unsupported refinement schedule')
    if scale != 2 and upscaling:
        # Recorded only off the original factor, so 2x plans (and their cache keys) are unchanged.
        result.update(scale=scale, mode=result['mode'] + '-x%g' % scale,
                      reason=result['reason'].replace('learned latent upscale', 'learned %gx latent upscale' % scale))
    if not upscaling:
        result['reason'] = 'Small canvas: %d steps and %d refinement steps at the same target size, without upscaling.' % (base_steps, refine_steps)
    return result


def crop_latents(video, sampling_plan):
    """Remove alignment padding in normalized latent space before tail sampling."""
    crop = sampling_plan['crop']
    lift = sampling_plan['upscale_target']
    if tuple(video.shape[-2:]) != (lift['height'] // 16, lift['width'] // 16):
        raise ValueError('Upscaled latents do not match the planned canvas')
    top, left, height, width = (crop[key] // 16 for key in ('top', 'left', 'height', 'width'))
    if top == left == 0 and (height, width) == tuple(video.shape[-2:]):
        return video
    return video[..., top:top + height, left:left + width].contiguous()


def steps(sampling_plan=None):
    """Validate the supported receipt rather than trusting a supplied NFE count."""
    if sampling_plan is None:
        return 8
    enabled = sampling_plan.get('enabled')
    base, refine = sampling_plan.get('base_steps'), sampling_plan.get('refine_steps')
    validate_steps(base, refine if enabled else 2, enabled)
    expected = base + refine if enabled else base
    if enabled and sampling_plan.get('refine_schedule') == 'upscale-sigma3-v1':
        expected = refine   # a finished video is the first pass: only the refinement is sampled
    if (type(sampling_plan.get('version')) is not int or sampling_plan['version'] not in (1, 2, 3)
            or (sampling_plan['version'] == 1 and (base != 8 or refine != (2 if enabled else 0)))
            or (sampling_plan['version'] == 3 and (not enabled or refine != 3
                or (sampling_plan.get('refine_schedule'), sampling_plan.get('video_sigmas')) not in (
                    ('community-sigma3-v1', [0.9035, 0.6316, 0.3158, 0.]),
                    ('upscale-sigma3-v1', [0.30, 0.2097, 0.1049, 0.]))))
            or (not enabled and (type(refine) is not int or refine != 0))
            or type(sampling_plan.get('total_steps')) is not int or sampling_plan['total_steps'] != expected):
        raise ValueError('Invalid sampling step plan')
    return expected


def same_strategy(left, right):
    """Do not mix mixed-resolution timings with ordinary full-resolution steps."""
    def reference_schedule(value):
        # Worker receipts add step traces and describe subsets at the smaller
        # canvas. Neither changes the reference schedule's identity.
        source = value.get('geometry') or value
        schedule = source.get('early_reference', value.get('early_reference'))
        if schedule is None:
            return None
        return dict(early_steps=schedule.get('early_steps'), conditions=[
            {key: row.get(key) for key in ('start_step', 'task', 'text_tokens',
                                         'reference_video_tokens', 'reference_audio_tokens')}
            for row in schedule.get('conditions', [])])

    if reference_schedule(left) != reference_schedule(right):
        return False
    a, b = left.get('sampling_plan'), right.get('sampling_plan')
    try:
        if steps(a) != steps(b):
            return False
    except (ValueError, AttributeError, TypeError):
        return False
    # Until a two-resolution cost model is calibrated, use exact-plan evidence.
    return a == b if (a or {}).get('enabled') or (b or {}).get('enabled') else True


def stage_tokens(canvas):
    """Video/reference activation rows for an encoded early-reference schedule.

    Counts are the scalar metadata from media_conditioning.describe, never
    conditioning tensors. Ordinary requests retain their historical envelope.
    The loaded engine must cover both passes; pass-local caching cannot shrink
    its resident prefix during the first steps.
    """
    fields = ('reference_video_tokens', 'reference_audio_tokens')

    def references(row):
        counts = [row.get(name, 0) for name in fields]
        if any(type(n) is not int or n < 0 for n in counts):
            raise ValueError('Reference token counts must be nonnegative integers')
        return sum(counts)

    maximum = references(canvas)
    full = canvas['video_tokens'] + maximum
    schedule = canvas.get('early_reference')
    if schedule is None:
        return dict(first=full, refinement=full, engine=full)
    conditions = schedule.get('conditions') if isinstance(schedule, dict) else None
    if not isinstance(conditions, list) or not conditions or any(not isinstance(row, dict) for row in conditions):
        raise ValueError('Early reference planning requires described condition counts')
    starts = [row.get('start_step') for row in conditions]
    if (any(type(n) is not int or n < 0 for n in starts) or starts[0] != 0
            or starts != sorted(set(starts))):
        raise ValueError('Invalid early reference planning boundaries')
    counts = [references(row) for row in conditions]
    if any(max(row.get(name, 0) for row in conditions) > canvas.get(name, 0) for name in fields):
        raise ValueError('Early reference counts exceed the request allowance')
    sampling = canvas.get('sampling_plan') or {}
    first = canvas['video_tokens']
    refinement = None
    if sampling.get('enabled'):
        steps(sampling)
        # The receipt records a non-default upscale factor and a staged first pass; the check replays both.
        expected = plan(canvas, task=canvas.get('task', 't2va'), base_steps=sampling['base_steps'],
                        refine_steps=sampling['refine_steps'], staged=sampling.get('staged', False),
                        scale=sampling.get('scale', 2))
        if any(sampling.get(key) != expected[key] for key in ('first', 'second')):
            raise ValueError('Early reference sampling geometry disagrees with the request')
        from .geometry import geometry
        first = geometry(**sampling['first'])['video_tokens']
        # Permanent image/video/audio references still participate in refinement.
        refinement = canvas['video_tokens'] + counts[-1]
    first += maximum
    return dict(first=first, refinement=refinement, engine=max(first, refinement or 0))


def placement_tokens(canvas):
    """Shared activation envelope for fixed GPU and host placement."""
    return stage_tokens(canvas)['engine']


def checkpoint_path():
    """Setup's copy; an older lazily downloaded copy under models/ stays in use."""
    from .paths import installed_model_root, model_root
    installed = installed_model_root() / 'latent_upscaler' / Path(UPSCALER['file']).name
    earlier = model_root() / 'latent_upscaler' / installed.name
    return earlier if earlier.is_file() and not installed.is_file() else installed


def verified_checkpoint(path):
    """Whether setup's ledger of verified models covers this copy, unchanged since.

    Every two-pass request used to hash the whole checkpoint before sampling:
    one more full read of the file, which the upscaler then read again. The
    ledger keeps the identity of each file whose content was hashed; a copy
    it does not know yet is hashed once here and recorded. An unreadable
    ledger is left alone and the caller hashes as before.
    """
    from .paths import data_root
    import json
    ledger = data_root() / 'verified-models.json'
    try:
        stamps = json.loads(ledger.read_text(encoding='utf-8')) if ledger.is_file() else {}
    except (OSError, ValueError):
        return False
    if not isinstance(stamps, dict):
        return False
    from . import provision
    # Setup records a file under its resolved folder and its own name.
    try:
        key = path.parent.resolve() / path.name
        recorded = stamps.get(str(key))
        if not provision.verified(key, dict(bytes=UPSCALER['bytes'], sha256=UPSCALER['sha256']), stamps):
            return False
        if stamps.get(str(key)) != recorded:
            from .monitoring import save
            save(ledger, stamps)
    except OSError:
        return False
    return True


def ensure_checkpoint():
    """Also upgrade existing installations lazily; retain invalid local files."""
    from .network import download, model_urls
    path = checkpoint_path()
    if verified_checkpoint(path):
        return path
    network = {}
    from .paths import data_root
    import json
    machine = data_root() / 'machine.json'
    if machine.is_file():
        installed = json.loads(machine.read_text(encoding='utf-8'))
        network = installed.get('network', {}) or {}
        if not network and installed.get('setup_run'):
            setup = Path(installed['setup_run']) / 'plan.json'
            if setup.is_file():
                network = json.loads(setup.read_text(encoding='utf-8')).get('network', {}) or {}
    network.setdefault('download_settings_path', str(data_root() / 'download-settings.json'))
    network.setdefault('sources', {}).setdefault('models', [{'id': 'official'}, {'id': 'hf-mirror'}])
    download(model_urls(network, UPSCALER), path, UPSCALER['sha256'],
             network=network, size=UPSCALER['bytes'], category='models')
    return path


# Host RAM the upscaler needs beside a resident engine. latent_upscale moves
# each checkpoint tensor (at most 13.5 MiB) to the device as it is read; this
# covers one in flight plus the reader's buffers, with ample margin.
UPSCALER_HOST_BYTES = 256 * 2**20


def upscale_workspace(canvas):
    """Conservative admission estimate, not a measured capacity guarantee."""
    latent_frames = (canvas['frames'] - 5) // 17 * 5 + 2
    feature = latent_frames * (canvas['width'] // 16) * (canvas['height'] // 16) * 512 * 2
    return 2 * 2**30 + max(4 * 2**30, 6 * feature)


# Windows moves a process's memory to shared system memory once it exceeds the
# local budget, which other applications change while a request runs. A cache
# filled to the budget measured at admission can cross it when the budget dips,
# and with the weights already in locked host pages the cached layers save only
# a fraction of a second per step.
WINDOWS_PASS_CACHE_MARGIN = 512 * 2**20


def pass_cache_allowance(budget, peak_reserved, free, reserve, commit_available=None, windows=None,
                         device_free=None):
    """Spare capacity after one real step, bounded independently by live space.

    Keep another 512 MiB beyond the existing OS reserve for pass-local growth,
    and on Windows another WINDOWS_PASS_CACHE_MARGIN below the local budget.
    Windows GPU allocations can also consume commit; retain host workspace.
    `device_free` is what the whole device can still take with every other
    process counted (Windows' budget and CUDA's free memory leave them out).
    """
    if windows is None:
        import os
        windows = os.name == 'nt'
    if windows:
        budget -= WINDOWS_PASS_CACHE_MARGIN
    bounds = [budget - peak_reserved, free - reserve]
    if device_free is not None:
        bounds.append(device_free - reserve)
    if commit_available is not None:
        # Sampling workspace has already been exercised. This cache creates no
        # new host tensors, so do not reserve the full model-loading allowance
        # again. Still leave 2 GiB of system commit before optional GPU growth.
        bounds.append(commit_available - 2 * 2**30)
    return max(0, int(min(bounds)) - 512 * 2**20)


def reusable_cache_bytes(cached, reserved, workspace_peak):
    """Device bytes a pass cache holds or can take back without a new allocation.

    Planes dropped from the cache stay in the allocator's pool, where
    mem_get_info no longer counts them as free. Counting only the planes still
    cached shrank the cache again at every step: an 8 GiB Windows card filled
    eight first-pass planes after its first step and ended the pass with one.
    What the allocator holds beyond the step's own peak is the cache's to reuse.
    """
    if reserved is None:
        return cached
    return max(cached, reserved - workspace_peak)


def first_pass_policy(profile, canvas, sampling_plan):
    """Run the existing token-aware policy within the admitted request budgets.

    The final pass keeps its already selected profile, including local evidence.
    Host weight storage is shared across passes; this result selects computation
    and a temporary GPU-residency target without rebuilding the transformer.
    """
    from dataclasses import replace
    from .geometry import geometry
    from .hardware import Hardware, GiB
    from .policy import choose
    if not sampling_plan.get('enabled'):
        return None
    policy = profile.get('policy', {})
    if not policy.get('hardware'):
        return None
    hardware = Hardware.from_dict(policy['hardware'])
    gpu_reserve = policy['gpu_system_reserve_bytes']
    ram_reserve = policy['ram_system_reserve_bytes']
    gpu = round(profile['gpu_budget_gb'] * 1e9)
    ram = round(profile['inference_ram_budget_gb'] * 1e9)
    hardware = replace(hardware, vram_free=min(hardware.vram_total, gpu + gpu_reserve),
                       ram_available=min(hardware.ram_total, ram + ram_reserve))
    first = geometry(**sampling_plan['first'])
    # Reference audio does not shrink with the image. Retain the full reference
    # video allowance too; keyframes may be resized but ref2va inputs need not be.
    for name in ('reference_video_tokens', 'reference_audio_tokens'):
        if name in canvas:
            first[name] = canvas[name]
    first['steps'] = sampling_plan['base_steps']
    first['task'] = profile['engine'].get('task', 't2va')
    backend = profile['engine']['attention']
    selected = choose(hardware, attention=backend, available_backends=set(backend.split('/')),
                      gpu_reserve_gib=gpu_reserve / GiB, ram_reserve_gib=ram_reserve / GiB,
                      lora_max_block_bytes=policy.get('lora_max_block_bytes', 0),
                      lora_root_bytes=policy.get('lora_root_bytes', 0),
                      precision='int8' if profile['engine'].get('linear_compute') == 'int8' else 'fp8',
                      canvas=first, allow_capacity_trial=policy.get('capacity_trial', False)).legacy_profile()
    selected['engine']['steps'] = sampling_plan['base_steps']
    selected['policy']['engine']['steps'] = sampling_plan['base_steps']
    return dict(geometry=first, profile=selected)


# Placement options a recovery attempt may have loosened or tightened. A first
# pass that is kept across a retry never uses more GPU residency, parallel
# workspaces or pinned host memory than the retry itself allows; its compute
# partitions (chunk sizes) are not in this list and stay as planned.
RETRY_MIN_KEYS = ('resident_blocks', 'head_parallelism', 'pin_host_gb')
RETRY_AND_KEYS = ('prefetch', 'pin_host_weights', 'preload_host')
RETRY_OR_KEYS = ('residual_offload', 'fp8_ff_recompute', 'stream_weights')


def clamp_first_pass_for_retry(first_policy, retry_profile):
    """The first-pass policy limited to the placement a resource retry selected.

    Returns (policy, clamped) where `clamped` maps each option the retry
    tightened to its first-pass and retry values. The input is not modified.
    """
    import copy
    policy = copy.deepcopy(first_policy)
    engine, retry = policy['profile']['engine'], retry_profile['engine']
    clamped = {}
    def set_option(name, value):
        if engine.get(name) != value:
            clamped[name] = {'first_pass': engine.get(name), 'retry': retry.get(name)}
            engine[name] = value
    for name in RETRY_MIN_KEYS:
        if name in retry and name in engine:
            set_option(name, min(engine[name], retry[name]))
    for name in RETRY_AND_KEYS:
        if name in retry and name in engine:
            set_option(name, bool(engine[name] and retry[name]) if isinstance(engine[name], bool) else engine[name])
    for name in RETRY_OR_KEYS:
        if retry.get(name) and not engine.get(name):
            set_option(name, retry[name])
    inner = policy['profile'].get('policy')
    if isinstance(inner, dict) and isinstance(inner.get('engine'), dict):
        inner['engine'].update({name: engine[name] for name in clamped})
    return policy, clamped


def keeps_first_pass_on_retry(attempts, max_retries):
    """Whether the retry now starting may keep the first pass's planned placement.

    Only the first recovery attempt does, and only while every recovery so far
    kept the arithmetic partitions. The last attempt falls back to the
    conservative profile exactly as before, so a first pass whose own chunk
    sizes were the cause cannot fail the request twice.
    """
    rows = list(attempts or [])
    if len(rows) != 2 or len(rows) - 1 >= max_retries:
        return False
    decision = rows[-1].get('decision') or {}
    return decision.get('numerical_class') != 'compute-partition'
