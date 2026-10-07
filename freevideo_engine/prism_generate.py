"""One local Prism (preview) request: image + prompt -> 720p video with audio.

Mirrors generate.py without importing a tensor runtime: the parent plans
placement from live VRAM / RAM (prism_policy), holds the GPU-work lock, and
runs two isolated workers through generate.child (RAM guard, NVML monitor,
resource history): the UMT5 prompt + first-frame encoder, then the video
worker (sampling, decoding, MP4). Reports follow the H3 layout next to the
output: ``.request.json`` (this supervisor), ``.encoding.json`` /
``.encoding.log`` and ``.engine.json`` / ``.engine.log`` (workers, with the
same JSON progress events comfy_bridge tails) and the support report.

Sampling recipe: the steps come from ``--base-steps`` (the quality tier in
``prism_tiers.json`` with that many steps supplies cfg_steps, audio_cfg and
optional shifts); without a matching tier the validated recipe applies:
6 steps, full CFG 2.0 on step 1, audio-only CFG 5.0 afterwards, shifts 5 / 7.
"""
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time

from .locking import runtime_lock, LOCK_ENV
from .monitoring import save
from . import processes
from .system import inference_emergency_floor, ram_budget_is_estimate

PACKAGE = Path(__file__).resolve().parent
FPS = 24
OFFICIAL = dict(width=1280, height=720, frames=205)
NEGATIVE_PROMPT = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，"
                   "整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指")


def prism_canvas(width=None, height=None, frames=None, seconds=None):
    width = OFFICIAL['width'] if width is None else width
    height = OFFICIAL['height'] if height is None else height
    if any(type(n) is not int or n < 256 or n > 4096 or n % 16 for n in (width, height)):
        raise ValueError('Prism (preview): width and height must be multiples of 16 from 256 to 4096 pixels.')
    if frames is not None and seconds is not None:
        raise ValueError('Specify frames or seconds, not both.')
    requested = math.ceil(seconds * FPS) if seconds is not None else (OFFICIAL['frames'] if frames is None else frames)
    if type(requested) is not int or requested < 1:
        raise ValueError('Frame count must be a positive integer.')
    aligned = requested + (1 - requested) % 4  # the Wan VAE needs 4n + 1 frames
    if aligned < 9:
        raise ValueError('Prism (preview) needs at least 9 frames.')
    return dict(width=width, height=height, frames=aligned, fps=FPS, seconds=aligned / FPS,
                requested_frames=requested, latent_frames=(aligned - 1) // 4 + 1,
                experimental=(width, height, aligned) != tuple(OFFICIAL[k] for k in ('width', 'height', 'frames')))


TIER_KEYS = ('steps', 'cfg_steps', 'audio_cfg', 'audio_substeps', 'shift', 'audio_shift', 'cfg_scale', 'distilled',
             'audio_teacher', 'sparsity', 'cdf', 'weights', 'attention', 'vae_encode', 'vae_decode', 't2v', 'fallback',
             'fbcache', 'fbcache_max_skip', 'sol', 'sol_beta', 'audio_calibrated')


def recipe(base_steps=None, tiers_path=None, tier_id=None):
    """Sampling settings from prism_tiers.json: the tier ``tier_id``, else the tier
    sampling ``base_steps``, else the file's default (validated recipe without a file).
    Tier fields: steps, cfg_steps (int or "all"), cfg_scale, audio_cfg (null = joint
    CFG), shift, audio_shift, distilled, audio_teacher {substeps, cfg, max_sigma,
    skip_below, every, sparsity, min_sigma, rescale, power}, sparsity, cdf, weights
    (int8 | fp8 | bf16), attention (sage | exact), vae_encode, vae_decode, t2v (a request
    without a first frame is accepted; undistilled tiers only), fallback (settings used
    when the level's weights are not installed, e.g. {"attention": "sage"}), fbcache /
    fbcache_max_skip (first-block step cache: threshold, reuses in a row; null = off), sol /
    sol_beta (Sol correction of the skipped attention tiles, with ``sparsity`` raised to
    0.85-0.90; Sage attention only), audio_calibrated (student-side v2a K/V maps instead
    of the teacher base pass; needs the bundle's audio_calibration files)."""
    from .prism_policy import RECIPE
    selected = dict(RECIPE)
    path = Path(tiers_path or os.environ.get('FREEVIDEO_PRISM_TIERS') or PACKAGE / 'prism_tiers.json')
    tier = None
    try:
        tiers = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        tiers = None
    rows = tiers.get('tiers', []) if isinstance(tiers, dict) else []
    if tier_id is not None:
        tier = next((row for row in rows if row.get('id') == tier_id), None)
        if tier is None:
            raise ValueError('Unknown Prism quality level: %s' % tier_id)
    elif base_steps is None and isinstance(tiers, dict) and tiers.get('default'):
        tier = next((row for row in rows if row.get('id') == tiers['default']), None)
    elif base_steps is not None:
        # A request that names only its steps (saved before the levels had ids): the
        # level those steps selected then (legacy_steps), else the default level when
        # it samples that many steps, else the first that does.
        legacy = (tiers.get('legacy_steps') or {}).get(str(base_steps)) if isinstance(tiers, dict) else None
        matching = [row for row in rows if row.get('steps') == base_steps]
        tier = (next((row for row in rows if row.get('id') == legacy), None)
                or next((row for row in matching if row.get('id') == (tiers or {}).get('default')), None)
                or (matching[0] if matching else None))
    if tier is not None:
        for key in TIER_KEYS:
            if key in tier and (tier[key] is not None or key == 'audio_cfg'):
                selected[key] = tier[key]
        selected['tier'] = tier.get('id')
    else:
        selected['tier'] = None
    if base_steps is not None:
        selected['steps'] = base_steps
    selected['source'] = str(path) if tier is not None else 'validated recipe'
    if type(selected['steps']) is not int or not 1 <= selected['steps'] <= 100:
        raise ValueError('Prism (preview) steps must be an integer between 1 and 100')
    if selected.get('cfg_steps') not in ('all', None) and type(selected.get('cfg_steps')) is not int:
        raise ValueError('cfg_steps must be an integer or "all"')
    if selected.get('audio_substeps', 1) not in (None, 1):
        raise ValueError('Prism audio sub-steps are not supported by this engine yet')
    return selected


def prepared_directory(cache, capability, variant=None):
    """``cache`` is a prepared variant (manifest.json) or a folder of variants."""
    cache = Path(cache).expanduser().resolve()
    if (cache / 'manifest.json').is_file():
        return cache
    wanted = variant or os.environ.get('FREEVIDEO_PRISM_VARIANT')
    options = [wanted] if wanted else ['int8', 'fp8']
    for name in options:
        if name == 'fp8' and tuple(capability) < (8, 9):
            continue
        if (cache / name / 'manifest.json').is_file():
            return cache / name
    if wanted:
        raise FileNotFoundError('This Prism quality level needs the %s weights (%s); install them or choose '
                                'another level.' % (wanted, cache / wanted))
    raise FileNotFoundError('No prepared Prism weights for this GPU under %s' % cache)


def main():
    from .cli import main as cli
    sys.argv[1:1] = ['generate', '--model', 'prism']
    cli()


def run(args):
    output = Path(args.out).expanduser().resolve()
    preexisting = any(path.exists() for path in (output, output.with_suffix('.request.json'),
                                               output.with_suffix('.artifacts')))
    started = time.perf_counter()
    try:
        return _run(args)
    except BaseException as error:
        if not preexisting and not getattr(error, 'diagnostic_recorded', False):
            from .diagnostic_resources import exception_details
            from .support_report import write as write_debug, code_identity
            record = dict(success=False, model='prism', request_seconds=time.perf_counter() - started,
                          phase='preflight', error_type=type(error).__name__, exception=exception_details(error),
                          error_message=str(error), runtime_code=code_identity())
            try:
                save(output.with_suffix('.request.json'), record)
                write_debug(output, record)
            except (OSError, ValueError):
                pass
        raise


def _run(args):
    from .hardware import detect
    from . import prism_policy
    from .generate import child
    from .adaptive import MAX_RESOURCE_RETRIES, attempt_geometry, execute_attempts, local_identity, history_path
    from .resource_history import ResourceHistory
    if getattr(args, 'conditioning', None):
        raise ValueError('Prism (preview) encodes its own prompt; pass --prompt-file, not --conditioning.')
    if args.out.suffix.lower() != '.mp4':
        raise ValueError('--out must name an MP4 file')
    canvas = prism_canvas(getattr(args, 'prism_width', None), getattr(args, 'prism_height', None),
                          frames=getattr(args, 'frames', None), seconds=getattr(args, 'seconds', None))
    settings = recipe(getattr(args, 'prism_steps', None) or getattr(args, 'base_steps', None),
                      tier_id=getattr(args, 'prism_tier', None))
    from .media_request import read as read_media
    media = read_media(getattr(args, 'media', None))
    if media.get('references') or media.get('last') or media.get('loras'):
        raise ValueError('Prism (preview) animates one first frame; references, last frames and LoRAs are not used.')
    image = media.get('first', {}).get('path')
    if not image and not (settings.get('t2v') and not settings.get('distilled', True)):
        raise ValueError('This Prism quality level animates a first frame; add one, or choose a level that '
                         'generates from text alone.')
    hardware, wddm, pinned_limit, resident_credit = planning_inputs(detect)
    if tuple(hardware.capability) < (8, 0):
        raise ValueError('Prism (preview) needs an NVIDIA RTX 30 series or newer GPU (SM 8.0+).')
    explicit = getattr(args, 'prism_variant', None)
    wanted = explicit or settings.get('weights')
    try:
        prepared = prepared_directory(args.cache, hardware.capability, wanted)
    except FileNotFoundError:
        if explicit or not wanted:
            raise
        # A level's optional weights (bf16 for original) are a separate download:
        # fall back to the installed weights with the level's fallback settings.
        prepared = prepared_directory(args.cache, hardware.capability, None)
        for key, value in (settings.get('fallback') or {}).items():
            if key in ('attention', 'vae_encode', 'vae_decode', 'sparsity', 'cdf'):
                settings[key] = value
        settings['weights_fallback'] = dict(wanted=wanted, used=prepared.name, note=(
            'The %s weights for this quality level are not installed; using the %s weights.'
            % (wanted.upper(), prepared.name.upper())))
        print(json.dumps(dict(event='prism_weights_fallback', **settings['weights_fallback'])), flush=True)
    from .prism_layout import find_kv_maps, kv_maps_bytes, with_shared  # no torch in the planning process
    manifest = with_shared(prepared, json.loads((prepared / 'manifest.json').read_text(encoding='utf-8')))
    teacher = settings.get('audio_teacher') or {}
    audio_maps = None
    if teacher.get('calibrated') and teacher.get('calibrated') != 'identity' or (
            teacher.get('calibrated') and os.environ.get('FREEVIDEO_PRISM_KV_MAPS')):
        # Fitted K/V maps (a prism-kv-calib/1 file); "identity" needs none.
        maps = find_kv_maps(prepared)
        if maps is None:
            raise FileNotFoundError('This Prism level needs its K/V calibration file (prism-kv-calib/1) in %s'
                                    % prepared)
        settings['audio_maps'] = str(maps)
        audio_maps = kv_maps_bytes(maps)
    print(json.dumps(dict(event='compute_device', backend='CUDA', name=hardware.gpu_name, uuid=hardware.gpu_uuid,
                          vram_total_bytes=hardware.vram_total, vram_free_bytes=hardware.vram_free)), flush=True)
    vram_budget = int(args.vram_gib * 2**30) if getattr(args, 'vram_gib', None) else None
    ram_budget = int(args.ram_gib * 2**30) if getattr(args, 'ram_gib', None) else None
    overrides = json.loads(args.profile.read_text(encoding='utf-8')) if getattr(args, 'profile', None) else {}
    planner = {k: overrides.pop(k) for k in ('force_lean', 'head_chunk', 'chunk', 'vae_tiling') if k in overrides}
    if 'kv_placement' in overrides:
        planner['kv_placement'] = overrides.pop('kv_placement')
    def plan(inputs, recovery):
        """The policy for one attempt from fresh planning inputs and the recovery
        state (``speed_first``; ``shrink``: bytes taken off the usable VRAM;
        ``pin_scale``: share of the page-locking allowance; ``chunk``: rows)."""
        hw, _, limit, _ = inputs
        shrink, pin_scale = recovery.get('shrink', 0), recovery.get('pin_scale', 1.0)
        free = max(0, hw.vram_free - shrink)
        budget = (vram_budget - shrink) if vram_budget else None
        options = dict(planner)
        host = ram_budget
        if recovery.get('ram_shrink'):
            # after the RAM guard stopped a worker: plan with the observed overflow less
            host = max(4 * 2**30, (ram_budget or hw.ram_available) - recovery['ram_shrink'])
        if recovery.get('chunk') and 'chunk' not in options:
            options['chunk'] = recovery['chunk']
        chosen = prism_policy.choose(manifest, vram_total=hw.vram_total, vram_free=free,
                                     ram_available=hw.ram_available, vram_budget=budget, ram_budget=host,
                                     audio_teacher=bool(settings.get('audio_teacher')), attention=settings.get('attention'),
                                     fbcache=bool(settings.get('fbcache')), speed_first=bool(recovery.get('speed_first')),
                                     pinned_limit=None if limit is None else int(limit * pin_scale),
                                     desktop=hw.system == 'Windows', audio_maps=audio_maps,
                                     teacher_start=int((settings.get('audio_teacher') or {}).get('start_layer') or 0),
                                     **canvas_args(canvas), **options)
        if limit is None and pin_scale < 1.0:
            chosen['pin_bytes'] = int(chosen['pin_bytes'] * pin_scale)  # no Windows allowance: fewer pins directly
        chosen.update(sparsity=settings['sparsity'], cdf=settings['cdf'])
        for key in ('attention', 'vae_encode', 'vae_decode', 'sol', 'sol_beta'):
            if settings.get(key) is not None:
                chosen[key] = settings[key]
        chosen.update(overrides)  # an explicit --profile wins over the tier
        # Allocator ceiling the worker enforces on Windows (gpu_budget.configure): the
        # dedicated VRAM free at planning, bounded live by the WDDM local budget minus
        # the process's non-Torch usage. Not the (shrunk) planning budget: a retry
        # plans with less, but the ceiling only keeps the allocator out of shared
        # memory. Explicit caps use allocator_limit_bytes.
        chosen['gpu_budget_bytes'] = int(hw.vram_free) if hw.system == 'Windows' else None
        return chosen
    explicit_plan = bool(overrides or planner)
    policy = plan((hardware, wddm, pinned_limit, resident_credit), dict(speed_first=False))
    room = int(policy['estimate']['resident_room_bytes'])
    if not explicit_plan and room < -INFEASIBLE_MARGIN:
        # Even the smallest plan does not fit: refuse now instead of after minutes of retries.
        usable = int(policy['estimate']['usable_vram_bytes'])
        raise MemoryError('This Prism (preview) video needs about %.1f GiB of GPU memory, but %.1f GiB is free. '
                          'Close other programs that use the GPU, or choose a smaller size or a shorter length. · '
                          '这条 Prism（预览）视频约需 %.1f GiB 显存，当前只有 %.1f GiB 可用。请关闭其他占用显卡的程序，'
                          '或选择更小的尺寸、更短的时长。'
                          % ((usable - room) / 2**30, usable / 2**30, (usable - room) / 2**30, usable / 2**30))
    if not settings.get('distilled', True) and (manifest.get('distill') or {}).get('kind') == 'merged' \
            and (manifest.get('distill') or {}).get('high'):
        raise ValueError('This quality level samples the base (undistilled) Prism weights; the installed '
                         'bundle has the distillation merged in.')
    ram_limit = ram_budget or max(8 * 2**30, hardware.ram_available - 4 * 2**30)
    destination = args.out.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    artifacts = destination.with_suffix('.artifacts')
    artifacts.mkdir(exist_ok=False)
    prompt = args.prompt_file.read_text(encoding='utf-8').strip()
    shutil.copyfile(args.prompt_file, artifacts / 'prompt.txt')
    sampling_plan = dict(version=1, model='prism', requested=False, enabled=False, mode='prism', tier=settings['tier'],
                         base_steps=settings['steps'], refine_steps=0, total_steps=settings['steps'],
                         first={k: canvas[k] for k in ('width', 'height', 'frames')}, second=None,
                         reason='Prism (preview): %d steps at the target size.' % settings['steps'])
    print(json.dumps(dict(event='sampling_plan', **sampling_plan)), flush=True)
    from .support_report import code_identity, write as write_debug
    report = dict(model='prism', success=False, geometry=canvas, seed=args.seed, recipe=settings, policy=policy,
                  wddm_at_planning=wddm,
                  resident_credit=(dict((k, resident_credit.get(k)) for k in ('reclaimable_gpu_bytes', 'reclaimable_ram_bytes',
                                                                              'prism_models', 'models'))
                                   if resident_credit else None),
                  sampling_plan=sampling_plan, prepared=str(prepared), variant=manifest.get('variant'),
                  image=image, artifacts=str(artifacts), runtime_hardware=hardware.to_dict(),
                  runtime_code=code_identity(),
                  idle_resources=dict(vram_gib=getattr(args, 'vram_gib', None), ram_gib=getattr(args, 'ram_gib', None)))
    started = time.perf_counter()

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'Request interrupted by signal {signum}')
    previous = processes.termination_handler(interrupted)
    repo = PACKAGE.parent
    try:
        from .windows_ux import awake
        # A Prism video takes 30-50 minutes on a 12-24 GB card: keep Windows from idle sleep.
        with runtime_lock() as descriptor, awake():
            history = ResourceHistory(history_path())
            history.recover_pending()
            identity = local_identity(hardware, prepared)
            report['resource_history'] = str(history.path)
            save(destination.with_suffix('.request.json'), report)
            env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONUNBUFFERED='1',
                       PYTHONPATH=os.pathsep.join(p for p in (str(repo), os.environ.get('PYTHONPATH', '')) if p))
            env[LOCK_ENV] = str(descriptor)
            # MiniMax H3's allocator string (pinned blocks not rounded up to a power of two)
            from .policy import ALLOCATOR_CONFIG
            env.setdefault('PYTORCH_CUDA_ALLOC_CONF', ALLOCATOR_CONFIG)
            # Keep Triton autotune results (the exact BSA kernel's configs) with the kernel
            # cache: re-benchmarking per process could pick another config, and with it
            # another summation order, from one request to the next.
            env.setdefault('TRITON_CACHE_AUTOTUNING', '1')
            env.setdefault('PYTORCH_ALLOC_CONF', env['PYTORCH_CUDA_ALLOC_CONF'])
            floor = inference_emergency_floor(ram_limit, getattr(args, 'ram_reserve_gib', None))
            estimate = ram_budget_is_estimate(args)
            # beside the installation's compiler caches (TRITON_CACHE_DIR=<root>/kernel-cache/triton)
            if env.get('TRITON_CACHE_DIR'):
                kernel_cache = Path(env['TRITON_CACHE_DIR']).expanduser().parent
            else:
                from .paths import data_root
                kernel_cache = data_root() / 'kernel-cache'
            common = dict(prepared=str(prepared), geometry=canvas,
                          sage_tune_cache=str(kernel_cache / 'prism-sage-tune.json'))
            condition = artifacts / 'conditioning.pt'
            task = 'i2va' if image else 't2va'
            geometry = attempt_geometry(canvas, dict(steps=settings['steps'], task=task))
            report['resource_attempts'] = []
            # The first video attempt is speed-first (head chunk 2 with prefetch where
            # the conservative estimate overruns by a little) unless that plan failed
            # on this device and request shape: then the conservative plan directly.
            speed_first = not explicit_plan and not speed_first_failed(history, identity, geometry, settings['tier'])

            def attempt_files(stage):
                suffix = '.encoding' if stage == 'encode' else '.engine'
                return [destination.with_suffix(suffix + ext) for ext in
                        ('.json', '.log', '.memory.json', '.gpu.json', '.gpu.csv')] + (
                    [destination.with_suffix('.sampling-memory.json')] if stage == 'video' else [])

            def archive(stage):
                def keep(index, row):
                    retained = artifacts / 'attempts' / ('%02d-%s-%s' % (len(report['resource_attempts']), stage, row['id']))
                    retained.mkdir(parents=True, exist_ok=False)
                    for path in attempt_files(stage):
                        if path.exists():
                            path.rename(retained / path.name)
                    save(retained / 'attempt.json', row)
                    return str(retained)
                return keep

            def on_retry(stage, retries):
                def note(index, row, selected, decision):
                    row['recovery'] = dict(decision, next_profile=selected)
                    save(destination.with_suffix('.request.json'), report)
                    retry = dict(attempt=index + 2, max_attempts=retries + 1, failed_phase=row.get('phase'),
                                 kind=row['failure'].get('kind'), stage=stage)
                    if stage == 'video':
                        retry['reuse_sampling'] = bool(selected.get('resume_latents'))
                    print(json.dumps(dict(event='resource_retry', stage=stage, reason=row['failure'].get('kind'),
                                          retry=retry, retained=row.get('retained'), mode=decision.get('mode'),
                                          next={k: selected['policy'].get(k) for k in RETRY_FIELDS})), flush=True)
                    from .support_report import write_retry
                    write_retry(destination, report, index=len(report['resource_attempts']), retained=row.get('retained'),
                                next_profile=selected, decision=row['recovery'])
                return note

            def worker(stage, selected, attempt, request, metrics_path, log_path, phase):
                save(artifacts / ('%s.json' % ('encode' if stage == 'encode' else 'video')), request)
                try:
                    telemetry = child(
                        [sys.executable, '-m', 'freevideo_engine.prism_worker', '--stage', stage,
                         '--request', str(artifacts / ('%s.json' % ('encode' if stage == 'encode' else 'video')))],
                        env, descriptor, log_path, ram_budget_bytes=ram_limit, ram_budget_is_estimate=estimate,
                        minimum_available_bytes=floor, gpu=hardware.gpu_uuid or None,
                        on_start=lambda pid: history.attach_worker(attempt, pid, phase=phase))
                except BaseException as error:
                    # the worker's own failure record (kind, device memory, phase) for
                    # classify_failure and the recovery
                    metrics = _read(metrics_path)
                    if metrics:
                        error.metrics = metrics
                        if stage == 'video' and not metrics.get('sampling_memory'):
                            observed = _read(destination.with_suffix('.sampling-memory.json'))
                            if observed:
                                metrics['sampling_memory'] = observed
                    raise
                return _read(metrics_path), telemetry

            # Encode: on an out-of-memory error or a shared-memory spill, stream the
            # text encoder (then run it on the CPU) and tile the VAE encode.
            def launch_encode(selected, attempt):
                encode_request = dict(common, policy=selected['policy'], prompt=prompt, negative_prompt=NEGATIVE_PROMPT,
                                      image=image, output=str(condition),
                                      gpu_budget_bytes=selected['policy'].get('gpu_budget_bytes'),
                                      allocator_limit_bytes=selected['policy'].get('allocator_limit_bytes'),
                                      metrics=str(destination.with_suffix('.encoding.json')))
                print(json.dumps({'event': 'encoding_start', 'resource_attempt': attempt}), flush=True)
                metrics, telemetry = worker('encode', selected, attempt, encode_request,
                                            destination.with_suffix('.encoding.json'),
                                            destination.with_suffix('.encoding.log'), 'encoding')
                report['encoding'], report['encoding_resources'] = metrics, telemetry
                return metrics, telemetry

            def validate_encode(metrics, telemetry, selected):
                if metrics.get('success') is not True or not condition.is_file():
                    raise ValueError('Encoder exited without verified conditioning')
                return None  # no resource observation (H3's calibration rows are video requests)

            def recover_encode(current, failure, phase, canvas_, **_):
                placement = current['policy'].get('text_encoder_device', 'cuda')
                later = TEXT_ENCODER_LADDER[TEXT_ENCODER_LADDER.index(placement) + 1:] \
                    if placement in TEXT_ENCODER_LADDER else []
                if failure.get('kind') not in RETRYABLE or not later:
                    return None, dict(reason='No smaller placement for the Prism text encoder and VAE encode.')
                updated = dict(current, policy=dict(current['policy'], text_encoder_device=later[0],
                                                    vae_encode_tiling=True))
                return updated, dict(mode='prism-encode', reason='Text encoder on %s, tiled VAE encode' % later[0])

            encode_profile = dict(model='prism', stage='encode', tier=settings['tier'],
                                  engine=dict(steps=settings['steps'], task=task),
                                  policy=dict(policy, speed_first=False))
            execute_attempts(history, identity, encode_profile, canvas, launch_encode, validate=validate_encode,
                             archive=archive('encode'), report=report,
                             persist=lambda: save(destination.with_suffix('.request.json'), report),
                             on_retry=on_retry('encode', len(TEXT_ENCODER_LADDER) - 1), recover=recover_encode,
                             automatic=not explicit_plan, max_retries=len(TEXT_ENCODER_LADDER) - 1)
            save(destination.with_suffix('.request.json'), report)

            # Video: OOM, a Windows shared-memory spill or the RAM guard end the worker;
            # adaptive.execute_attempts retries with the next plan (recover_video),
            # planned again from live VRAM / RAM / WDDM budgets.
            def launch_video(selected, attempt):
                chosen = selected['policy']
                video_request = dict(common, policy=chosen, allocator_limit_bytes=chosen.get('allocator_limit_bytes'),
                                     gpu_budget_bytes=chosen.get('gpu_budget_bytes'), conditioning=str(condition),
                                     seed=args.seed, recipe=settings, output=str(destination),
                                     metrics=str(destination.with_suffix('.engine.json')), artifacts=str(artifacts),
                                     resource_attempt=attempt)
                if selected.get('resume_latents'):
                    video_request['resume_latents'] = selected['resume_latents']
                report['policy'] = chosen
                print(json.dumps({'event': 'decode_resume' if selected.get('resume_latents') else 'video_start',
                                  'resource_attempt': attempt, 'engine': chosen,
                                  'decoder': {'tiling': chosen['vae_tiling']}}), flush=True)
                metrics, telemetry = worker('video', selected, attempt, video_request,
                                            destination.with_suffix('.engine.json'),
                                            destination.with_suffix('.engine.log'), 'video')
                report['video'], report['resources'] = metrics, telemetry
                return metrics, telemetry

            def validate_video(metrics, telemetry, selected):
                if metrics.get('success') is not True or not destination.is_file():
                    raise ValueError('Video worker exited without a verified MP4')
                return None

            def recover_video(current, failure, phase, canvas_, **_):
                kind = failure.get('kind')
                if kind not in RETRYABLE:
                    return None, dict(reason='Not a resource failure: %s' % kind)
                latents = artifacts / 'latents.pt'
                if phase == 'decode' and latents.is_file() and not current.get('resume_latents'):
                    # Sampling finished and its latents were saved: decode again with the
                    # low-memory VAE decode instead of sampling again.
                    policy = dict(current['policy'], vae_tiling=True, vae_decode='fast_low_vram')
                    return (dict(current, policy=policy, resume_latents=str(latents)),
                            dict(mode='prism-decode-resume', reason='Decode ran out of memory; the saved '
                                 'latents are decoded again with the low-memory VAE decode.'))
                state = dict(current['recovery'])
                before = current['policy']
                # The failed worker has exited (and a resident worker may have released
                # its models): plan from fresh readings.
                inputs = planning_inputs(detect)
                nonlocal_full = nonlocal_exhausted_at_failure(failure)
                steps = []
                if kind == 'ram_pressure':
                    # fewer pins and more teacher K/V layers in the spill file: plan with the
                    # measured overflow (working set over the guard's budget) plus 1 GiB less RAM
                    guard = failure.get('ram_guard') or {}
                    over = (guard.get('working_bytes') or 0) - (guard.get('budget_bytes') or 0)
                    steps.append(dict(ram_shrink=state.get('ram_shrink', 0) + max(0, int(over)) + 2**30))
                    steps.append(dict(ram_shrink=state.get('ram_shrink', 0) + max(0, int(over)) + 3 * 2**30))
                if kind == 'ram_pressure' or nonlocal_full:
                    steps.append(dict(pin_scale=state.get('pin_scale', 1.0) / 2))
                if kind in ('gpu_oom', 'shared_memory_spill'):
                    # Smaller work first (head chunk and prefetch with the conservative
                    # plan, then half the row chunks), residency only after that.
                    chunk = int(before.get('chunk') or 16384)
                    if state.get('speed_first'):
                        steps.append(dict(speed_first=False))
                    steps += [dict(speed_first=False, chunk=max(1024, chunk // 2)),
                              dict(speed_first=False, chunk=max(1024, chunk // 4), shrink=state.get('shrink', 0) + 2**30),
                              dict(speed_first=False, chunk=1024, shrink=state.get('shrink', 0) + 2 * 2**30)]
                else:
                    steps += [dict(pin_scale=state.get('pin_scale', 1.0) / 4)]
                for change in steps:
                    candidate = dict(state, **change)
                    if candidate.get('shrink', 0) > MAX_SHRINK:
                        continue
                    chosen = plan(inputs, candidate)
                    if plan_key(chosen) != plan_key(before) or kind == 'shared_memory_spill' and change.get('shrink'):
                        report['wddm_at_retry'] = inputs[1]
                        return (dict(current, recovery=candidate, policy=chosen),
                                dict(mode='prism-' + ('pinning' if {'pin_scale', 'ram_shrink'} & set(change)
                                                      else 'placement'),
                                     reason=RECOVERY_REASON[kind], recovery=candidate,
                                     changed={k: (before.get(k), chosen.get(k)) for k in RETRY_FIELDS
                                              if before.get(k) != chosen.get(k)}))
                return None, dict(reason='No smaller Prism placement is left for this request.')

            video_profile = dict(model='prism', stage='video', tier=settings['tier'],
                                 engine=dict(steps=settings['steps'], task=task),
                                 recovery=dict(speed_first=speed_first), policy=None)
            video_profile['policy'] = plan((hardware, wddm, pinned_limit, resident_credit), video_profile['recovery'])
            execute_attempts(history, identity, video_profile, canvas, launch_video, validate=validate_video,
                             archive=archive('video'), report=report,
                             persist=lambda: save(destination.with_suffix('.request.json'), report),
                             on_retry=on_retry('video', MAX_RESOURCE_RETRIES), recover=recover_video, automatic=not explicit_plan,
                             max_retries=MAX_RESOURCE_RETRIES)
            report['sage_tune'] = report['video'].get('sage_tune')
            report['summary'] = summary(report)
            report['success'] = True
    except BaseException as error:
        from .diagnostic_resources import exception_details
        report['exception'] = exception_details(error)
        error.diagnostic_recorded = True
        report.update(error=repr(error), error_message=str(error), error_type=type(error).__name__)
        raise
    finally:
        processes.restore_handlers(previous)
        report['request_seconds'] = time.perf_counter() - started
        try:
            diagnostic = write_debug(destination, report)
            if diagnostic:
                report['diagnostic_file'] = diagnostic.name
        except (OSError, ValueError):
            pass
        save(destination.with_suffix('.request.json'), report)
    return report


def windows_wddm():
    """The WDDM budgets a new CUDA process gets ({'local': {budget_bytes, usage_bytes},
    'nonlocal': {...}}), read in a probe process; None when unreadable. Local is
    dedicated VRAM (the probe's own context is accounted by CONTEXT_BYTES);
    non-local bounds page-locked host memory."""
    from .windows_gpu_memory import probe
    return probe(sys.executable, cwd=str(PACKAGE.parent))


RETRYABLE = ('gpu_oom', 'shared_memory_spill', 'ram_pressure')
# How far the smallest plan's estimate may exceed the free VRAM before a request is
# refused up front (the estimate is conservative: an RTX 4070 Light run planned 10.2 GiB
# and its allocator reserved at most 10.5 GiB of the 10.98 GiB budget).
INFEASIBLE_MARGIN = int(0.5 * 2**30)
TEXT_ENCODER_LADDER = ('cuda', 'stream', 'cpu')
RETRY_FIELDS = ('head_chunk', 'prefetch', 'chunk', 'park_residual', 'resident_units', 'pin_bytes', 'kv_disk_layers',
                'kv_placement', 'gpu_budget_bytes', 'speed_first')
MAX_SHRINK = 3 * 2**30  # VRAM taken off the plan at most, over all retries
RECOVERY_REASON = dict(
    gpu_oom='Out of GPU memory: a smaller plan (no speed-first, less usable VRAM, smaller row chunks).',
    shared_memory_spill='The allocator grew into shared system memory: a smaller plan.',
    ram_pressure='The RAM guard stopped the worker: fewer pinned units and teacher K/V layers in the spill file.')


def planning_inputs(detect):
    """(hardware, WDDM budgets, page-locking allowance, resident credit), read now.
    Read again before every retry: the failed worker has exited, and an idle
    resident worker's models are credited (released before, or reused by, the request)."""
    hardware = detect()
    from .resident_process import ENV as RESIDENT_ENV, credit
    resident_credit = None
    if os.environ.get(RESIDENT_ENV):
        hardware, resident_credit = credit(hardware)
    if hardware.system != 'Windows':
        return hardware, None, None, resident_credit
    from .windows_gpu_memory import NONLOCAL_PIN_RESERVE
    wddm = windows_wddm()
    limit = None
    if wddm is not None:
        local, nonlocal_ = wddm.get('local') or {}, wddm.get('nonlocal') or {}
        if resident_credit is None and type(local.get('budget_bytes')) is int and local['budget_bytes'] > 0:
            # Plan from the dedicated (local) WDDM budget, never from memory Windows
            # would back with shared system RAM.
            from dataclasses import replace
            hardware = replace(hardware, vram_free=min(hardware.vram_free, local['budget_bytes']))
        if type(nonlocal_.get('budget_bytes')) is int and nonlocal_['budget_bytes'] > 0:
            # Page-locked host memory (pinned units, registered K/V, staging) is charged
            # to the non-local ("shared GPU memory") budget, about half of RAM.
            limit = max(0, nonlocal_['budget_bytes'] - nonlocal_.get('usage_bytes', 0) - NONLOCAL_PIN_RESERVE)
    if limit is None:
        # DXGI unreadable: Windows sets the non-local budget near half of RAM (offload.py)
        limit = max(0, hardware.ram_total // 2 - NONLOCAL_PIN_RESERVE)
    return hardware, wddm, limit, resident_credit


def speed_first_failed(history, identity, geometry, tier):
    """Whether the newest speed-first video attempt of this level on this device and
    request shape ran out of memory (resource history): then start conservative."""
    try:
        rows = history.recent(identity, geometry, purpose='generation', limit=20)
    except Exception:  # noqa: BLE001 - an unreadable history only costs one speed-first attempt
        return False
    for row in rows:
        config = row.get('config') or {}
        if config.get('model') == 'prism' and config.get('stage') == 'video' and config.get('tier') == tier \
                and (config.get('recovery') or {}).get('speed_first'):
            return row.get('outcome') == 'resource_failure'
    return False


def nonlocal_exhausted_at_failure(failure):
    """Windows: the failed worker's non-local (page-locked) usage was at its budget."""
    end = ((failure.get('device_memory') or {}).get('windows_memory_at_end') or {}).get('nonlocal') or {}
    budget, usage = end.get('budget_bytes'), end.get('usage_bytes')
    return type(budget) is int and type(usage) is int and budget > 0 and usage >= budget - 2**30


def plan_key(policy):
    return json.dumps({k: v for k, v in policy.items() if k not in ('notes', 'estimate')}, sort_keys=True,
                      default=str)


def canvas_args(canvas):
    return {k: canvas[k] for k in ('width', 'height', 'frames')}


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def summary(report):
    """Peaks and timings across both workers."""
    video = report.get('video', {})
    gpu = [row.get('gpu', {}).get('gpu_peak_bytes') for row in (report.get('encoding_resources', {}),
                                                                 report.get('resources', {}))]
    ram = [row.get('ram', {}).get('process_tree_peak_rss_bytes') for row in (report.get('encoding_resources', {}),
                                                                           report.get('resources', {}))]
    peaks = video.get('stage_peaks', {})
    steps = video.get('step_seconds') or []
    return dict(whole_gpu_peak_bytes=max([v for v in gpu if v] or [0]),
                torch_peak_reserved_bytes=max([v.get('reserved_bytes', 0) for v in peaks.values()] or [0]),
                torch_peak_allocated_bytes=max([v.get('allocated_bytes', 0) for v in peaks.values()] or [0]),
                ram_peak_rss_bytes=max([v for v in ram if v] or [0]),
                step_seconds=steps, mean_warm_step_seconds=(sum(steps[1:]) / len(steps[1:])) if len(steps) > 1 else None,
                sampling_seconds=sum(steps), load_seconds=video.get('load_seconds'),
                decode_seconds=(video.get('video_decode_seconds') or 0) + (video.get('audio_decode_seconds') or 0),
                lean=report.get('policy', {}).get('lean'))


if __name__ == '__main__':
    main()
