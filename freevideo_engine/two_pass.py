"""Generation planning with an 8 + 2 default, without importing Torch."""
from math import gcd
from pathlib import Path

UPSCALER = dict(
    repo='LBH-123-AI/Minimax_h3_latent_Upscaler',
    revision='3f941d5d182014dd5c0a5e16330420ee2d4aa0c6',
    file='minimax_h3_latent_upscaler_3d_conv_v1/minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors',
    bytes=690592672,
    sha256='043e5a48e161610ef6c3ea974645220354d06fa618abca15f76d084812eb55c2',
    role='latent_upscaler')


def validate_steps(base_steps=8, refine_steps=2, enabled=True):
    if type(enabled) is not bool:
        raise ValueError('Two-pass generation must be a boolean')
    if type(base_steps) is not int or not 1 <= base_steps <= 32:
        raise ValueError('First-pass steps must be an integer between 1 and 32')
    if type(refine_steps) is not int or not 1 <= refine_steps <= 31:
        raise ValueError('Second-pass steps must be an integer between 1 and 31')
    if enabled and refine_steps >= base_steps:
        raise ValueError('Second-pass steps must be fewer than first-pass steps')


def plan(canvas, enabled=True, task='t2va', *, base_steps=8, refine_steps=2):
    """Align the smaller canvas, lift, center-crop, then refine at the target.

    Odd multiples of 32 need up to 32 extra pixels before the latent crop.
    Small canvases use a smaller factor or same-size refinement. Size never
    disables a requested second pass; memory pressure cannot change its math.
    """
    from .geometry import geometry
    from .media_request import TASKS
    validate_steps(base_steps, refine_steps, enabled)
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
    crop = dict(left=(lift['width'] - target['width']) // 2,
                top=(lift['height'] - target['height']) // 2,
                width=target['width'], height=target['height'])
    upscaling = first != lift
    result.update(enabled=True, mode='vdn%d-lbh-tail%d' % (base_steps, refine_steps),
                  refine_steps=refine_steps, total_steps=base_steps + refine_steps,
                  first=first, second=shape, upscale_target=lift, crop=crop,
                  restart_seed_offset=1,
                  upscaler_sha256=UPSCALER['sha256'] if upscaling else None,
                  audio_policy='first_pass_preserved_with_audio_clock_conditioning',
                  reason='%d steps on a smaller canvas, learned latent upscale, then the DMD schedule tail (%d steps).' % (base_steps, refine_steps))
    if default:
        result['reason'] = '8 steps on a smaller canvas, learned latent upscale, then the original DMD8 schedule tail (2 steps).'
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
    if (type(sampling_plan.get('version')) is not int or sampling_plan['version'] not in (1, 2)
            or (sampling_plan['version'] == 1 and (base != 8 or refine != (2 if enabled else 0)))
            or (not enabled and (type(refine) is not int or refine != 0))
            or type(sampling_plan.get('total_steps')) is not int or sampling_plan['total_steps'] != expected):
        raise ValueError('Invalid sampling step plan')
    return expected


def same_strategy(left, right):
    """Do not mix mixed-resolution timings with ordinary full-resolution steps."""
    a, b = left.get('sampling_plan'), right.get('sampling_plan')
    try:
        if steps(a) != steps(b):
            return False
    except (ValueError, AttributeError, TypeError):
        return False
    # Until a two-resolution cost model is calibrated, use exact-plan evidence.
    return a == b if (a or {}).get('enabled') or (b or {}).get('enabled') else True


def checkpoint_path():
    from .paths import model_root
    return model_root() / 'latent_upscaler' / Path(UPSCALER['file']).name


def ensure_checkpoint():
    """Also upgrade existing installations lazily; retain invalid local files."""
    from .network import download, model_urls
    path = checkpoint_path()
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


def upscale_workspace(canvas):
    """Conservative admission estimate, not a measured capacity guarantee."""
    latent_frames = (canvas['frames'] - 5) // 17 * 5 + 2
    feature = latent_frames * (canvas['width'] // 16) * (canvas['height'] // 16) * 512 * 2
    return 2 * 2**30 + max(4 * 2**30, 6 * feature)


def pass_cache_allowance(budget, peak_reserved, free, reserve, commit_available=None):
    """Spare capacity after one real step, bounded independently by live space.

    Keep another 512 MiB beyond the existing OS reserve for pass-local growth.
    Windows GPU allocations can also consume commit; retain host workspace.
    """
    bounds = [budget - peak_reserved, free - reserve]
    if commit_available is not None:
        # Sampling workspace has already been exercised. This cache creates no
        # new host tensors, so do not reserve the full model-loading allowance
        # again. Still leave 2 GiB of system commit before optional GPU growth.
        bounds.append(commit_available - 2 * 2**30)
    return max(0, int(min(bounds)) - 512 * 2**20)


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
    backend = profile['engine']['attention']
    selected = choose(hardware, attention=backend, available_backends=set(backend.split('/')),
                      gpu_reserve_gib=gpu_reserve / GiB, ram_reserve_gib=ram_reserve / GiB,
                      lora_max_block_bytes=policy.get('lora_max_block_bytes', 0),
                      lora_root_bytes=policy.get('lora_root_bytes', 0),
                      canvas=first, allow_capacity_trial=policy.get('capacity_trial', False)).legacy_profile()
    selected['engine']['steps'] = sampling_plan['base_steps']
    selected['policy']['engine']['steps'] = sampling_plan['base_steps']
    return dict(geometry=first, profile=selected)
