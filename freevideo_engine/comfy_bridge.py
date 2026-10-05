"""ComfyUI orchestration without importing Torch or a model into its process.

The installed engine owns resource policy, input caches, retries and history.
This bridge only retains a request, supervises that CLI and forwards progress.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from . import processes
from .geometry import geometry
from .comfy_environment import isolated_environment
from .monitoring import save


def _timing_seconds(value):
    """Read a finite positive forecast value without inventing a duration."""
    if isinstance(value, dict):
        value = value.get('estimate')
    return value if type(value) in (int, float) and math.isfinite(value) and value > 0 else None


def progress_history_forecast(root, machine, canvas):
    """Approximate UI timing from recent complete videos on this device.

    Placement can change with free memory, so these durations only seed the
    waiting animation. They never feed the resource policy or capacity checks;
    the current request's layer/step timings replace the sampling estimate.
    Read the existing history without creating or migrating a database.
    """
    import platform
    import sqlite3
    from contextlib import closing
    from statistics import median
    path = Path(root) / 'resource-history.sqlite3'
    if not machine.get('gpu_uuid') or not path.is_file():
        return {}
    try:
        # SQLite's transaction context commits/rolls back but does not close.
        # Close this read-only handle immediately, including on query failure.
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.1)) as db:
            rows = db.execute("SELECT identity_json,geometry_json,observation_json FROM attempts "
                              "WHERE state='success' AND observation_json IS NOT NULL "
                              "ORDER BY started DESC LIMIT 64").fetchall()
        samples = []
        from .two_pass import same_strategy
        for raw_identity, raw_canvas, raw_observation in rows:
            identity, shape, observation = map(json.loads, (raw_identity, raw_canvas, raw_observation))
            if not all(isinstance(value, dict) for value in (identity, shape, observation)):
                continue
            gpu = identity.get('gpu', {})
            gpu_uuid = gpu.get('uuid') if isinstance(gpu, dict) else None
            if ((gpu_uuid or identity.get('gpu_uuid')) != machine['gpu_uuid']
                    or identity.get('system') != platform.system()
                    or any(shape.get(key) != canvas.get(key) for key in ('width', 'height', 'frames'))
                    or not same_strategy(shape, canvas)
                    or observation.get('validated') is not True or observation.get('full_request') is not True):
                continue
            stages = observation.get('stage_seconds')
            if (isinstance(stages, dict)
                    and all(_timing_seconds(stages.get(key)) is not None
                            for key in ('load_seconds', 'sample_seconds', 'decode_save_seconds'))):
                samples.append(stages)
            if len(samples) == 3:
                break
        return {'stages': {key: median(row[key] for row in samples)
                          for key in ('load_seconds', 'sample_seconds', 'decode_save_seconds')}} if samples else {}
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return {}  # A progress estimate cannot prevent generation.


class WholeVideoProgress:
    """Annotate phase events with one report-weighted whole-video progress.

    Local reports take precedence. Without history, live sampling supplies a
    rough decoder estimate which real decoded-frame timings then replace.
    These presentation estimates never change resource admission or placement.
    """
    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.started = clock()
        self.phase_started = self.started
        self.phase = None
        self.completed = set()
        self.weights = {}
        self.measured = {}
        self.total = 0.
        self.forecast_ready = False
        self.failed = False
        self.sampling_plan = {}
        self.decode_from_sampling = False
        self.sampling_time_weighted = False
        self.decode_tail = 0.

    def _sampling_estimate(self, message, now):
        estimate = _timing_seconds(message.get('estimated_step_seconds'))
        done, total = message.get('done'), message.get('total')
        if estimate is None or type(done) is not int or type(total) is not int or not 0 <= done <= total:
            return
        full_step = estimate
        remaining = (total - done) * estimate
        if message.get('uniform_remaining_steps') is False:
            plan = self.sampling_plan
            first, second = plan.get('first', {}), plan.get('second') or {}
            area = first.get('width', 0) * first.get('height', 0)
            if not area or not second.get('width') or not second.get('height'):
                return  # Do not treat the two larger refinement NFEs as small NFEs.
            full_step *= second['width'] * second['height'] / area
            remaining = max(0, plan['base_steps'] - done) * estimate + plan['refine_steps'] * full_step
        remaining = max(0., remaining - (_timing_seconds(message.get('step_elapsed_seconds')) or 0.))
        self.weights['sampling'] = max(.001, now - self.phase_started + remaining)
        self.sampling_time_weighted = True
        if 'decode' not in self.weights or self.decode_from_sampling:
            # Windows 1344x768x243 measurements: decode/save was 1.71, 1.90
            # and 2.01 final-resolution warm NFEs on 4060 Ti / 5060 Ti / 5090.
            # This is an approximate first-run ruler, not a GPU speed claim.
            # It scales with this request's work, then yields to frame timing.
            self.weights['decode'] = 1.9 * full_step
            self.decode_from_sampling = True
        self.forecast_ready = True

    @staticmethod
    def _forecast_weights(forecast):
        stages = forecast.get('stages') if isinstance(forecast, dict) else None
        if not isinstance(stages, dict):
            return {}
        values = {
            'encoding': _timing_seconds(stages.get('text_encode_seconds')),
            'load': _timing_seconds(stages.get('load_seconds')),
            'sampling': _timing_seconds(stages.get('sample_seconds')),
            'decode': _timing_seconds(stages.get('decode_save_seconds')),
        }
        values = {key: value for key, value in values.items() if value is not None}
        # Sparse phase forecasts expose work_seconds plus its independently
        # predicted components.  Keep the measured residual as the final
        # save/cleanup tail instead of silently dropping it or double-counting
        # the worker total.
        work = _timing_seconds(stages.get('work_seconds'))
        residual = work - sum(values.get(key, 0.) for key in ('load', 'sampling', 'decode')) if work is not None else 0.
        if residual > 0:
            values['decode'] = values.get('decode', 0.) + residual
        return values

    def forecast(self, value):
        value = value.get('forecast') if isinstance(value, dict) and 'forecast' in value else value
        weights = self._forecast_weights(value)
        if weights:
            # The prediction is emitted after prompt encoding has completed.
            # Close that phase before the first video-model event so its
            # measured weight is credited instead of starting a second timer.
            if self.phase == 'encoding':
                self.measured['encoding'] = max(.001, self.clock() - self.phase_started)
                self.completed.add('encoding')
                self.phase = None
            self.weights.update(weights)
            if 'decode' in weights:
                self.decode_from_sampling = False
            self.weights.update(self.measured)
            self.total = sum(self.weights.values())
            self.forecast_ready = all(name in self.weights for name in ('sampling', 'decode'))

    def _base(self):
        return sum(self.weights.get(name, 0.) for name in self.completed)

    def _message_phase(self, message):
        if message.get('timing_phase') in ('encoding', 'load', 'sampling', 'decode'):
            return message['timing_phase']
        phase = message.get('phase')
        return {'sampling': 'sampling', 'sample_finalize': 'decode',
                'complete': 'decode'}.get(phase)

    def annotate(self, message):
        message = dict(message)
        if isinstance(message.get('sampling_plan'), dict):
            self.sampling_plan = message['sampling_plan']
        if isinstance(message.get('prediction'), dict):
            self.forecast(message['prediction'])
        retry = message.get('retry') if isinstance(message.get('retry'), dict) else {}
        if retry.get('reuse_sampling') is True:
            self.completed.add('sampling')
        phase = self._message_phase(message)
        now = self.clock()
        if phase and phase != self.phase:
            if self.phase is not None:
                self.completed.add(self.phase)
                self.measured[self.phase] = max(.001, now - self.phase_started)
                self.weights[self.phase] = self.measured[self.phase]
            self.phase, self.phase_started = phase, now
        if phase == 'sampling':
            self._sampling_estimate(message, now)
        if phase == 'decode' and message.get('stage') == 'video':
            done, total = message.get('done'), message.get('total')
            elapsed = _timing_seconds(message.get('elapsed_seconds'))
            if elapsed and type(done) is int and type(total) is int and 0 < done <= total:
                video_seconds = elapsed * total / done
                # Reports put audio/MP4/post-decode cleanup at about 6-8% of
                # video decode time. Retain this tail until audio/save starts.
                self.decode_tail = video_seconds * .08
                self.weights['decode'] = now - self.phase_started + video_seconds - elapsed + self.decode_tail
                self.forecast_ready = True
        elif phase == 'decode':
            self.decode_tail = 0.
        self.total = sum(self.weights.values())
        if message.get('phase') == 'complete' or message.get('overall', {}).get('status') == 'complete':
            message['overall'] = {'status': 'complete', 'fraction': 1., 'estimated': False,
                                  'elapsed_seconds': now - self.started, 'remaining_seconds': 0.}
            return message
        overall = {'status': 'running', 'estimated': self.forecast_ready,
                   'elapsed_seconds': now - self.started}
        if self.forecast_ready and self.total > 0:
            start = self._base() / self.total
            weight = self.weights.get(self.phase, 0.)
            elapsed = max(0., now - self.phase_started)
            seconds = self.weights.get(self.phase)
            fraction = start
            if weight:
                fraction += weight / self.total * min(.95, elapsed / max(seconds, 1e-9))
            remaining = max(0., self.total - self._base() - min(elapsed, seconds or 0.))
            future = sum(value for name, value in self.weights.items()
                         if name not in self.completed and name != self.phase)
            overall.update(fraction=fraction, phase_start_fraction=start,
                           phase_weight=weight / self.total if self.total else 0.,
                           phase_elapsed_seconds=elapsed, phase_seconds=seconds,
                           remaining_seconds=remaining,
                           remaining_floor_seconds=max(future, self.decode_tail),
                           sampling_time_weighted=self.sampling_time_weighted)
        message['overall'] = overall
        return message


def source_root():
    return Path(__file__).resolve().parents[1]


def installation_root(source=None, environ=None):
    source = Path(source or source_root())
    environ = os.environ if environ is None else environ
    selected = environ.get('FREEVIDEO_HOME')
    config = source / 'comfyui.json'
    if not selected and config.is_file():
        selected = json.loads(config.read_text(encoding='utf-8')).get('installation')
        if not isinstance(selected, str) or not selected.strip():
            raise ValueError('comfyui.json must contain an installation directory.')
    root = Path(selected).expanduser() if selected else source
    if not root.is_absolute():
        root = source / root
    return root.resolve()


def installation(source=None, environ=None):
    root = installation_root(source, environ)
    try:
        machine = json.loads((root / 'machine.json').read_text(encoding='utf-8'))
    except FileNotFoundError as error:
        raise ValueError('FreeVideo setup is missing. Open FreeVideo Settings and click "Install / repair", '
                         'or run setup.cmd (Windows) or setup.sh (Linux) '
                         'in the FreeVideo folder, or set FREEVIDEO_HOME to your existing installation. '
                         'A prepared engine installation is required.') from error
    required = ('root', 'python', 'cache', 'base', 'checkpoint', 'comfy_python',
                'comfy_root', 'vdn_root', 'model_paths', 'model_root', 'encoder')
    if not isinstance(machine, dict) or not machine.get('ready') or any(not machine.get(k) for k in required):
        raise ValueError('FreeVideo setup is incomplete. Open FreeVideo Settings and click "Install / repair" to repair %s; existing files are reused.' % root)
    identity = 'device_identity' if machine.get('device_backend') == 'mps' else 'gpu_uuid'
    if not machine.get(identity):
        raise ValueError('FreeVideo device configuration is missing. Open Settings and click "Install / repair".')
    if Path(machine['root']).expanduser().resolve() != root:
        raise ValueError('The FreeVideo installation was moved. Rerun setup in the selected folder.')
    if not Path(machine['python']).is_file():
        raise ValueError('The FreeVideo Python environment is missing. Rerun setup in %s.' % root)
    return root, machine


def validate_request(prompt, width, height, seconds, seed):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('Enter a prompt describing the video and its audio.')
    if type(seed) is not int or not 0 <= seed <= 2**53 - 1:
        raise ValueError('Seed must be an integer between 0 and 9007199254740991.')
    if isinstance(seconds, bool) or not isinstance(seconds, (float, int)) or not math.isfinite(seconds):
        raise ValueError('Duration must be a positive number of seconds.')
    return geometry(width, height, seconds=seconds)


class EventTail:
    """Read bounded complete JSON lines; do not hold worker log handles open."""
    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.identity = None
        self.pending = b''

    def reset(self):
        self.offset = 0
        self.identity = None
        self.pending = b''

    def events(self):
        try:
            with self.path.open('rb') as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if identity != self.identity or stat.st_size < self.offset:
                    self.reset()
                self.identity = identity
                stream.seek(self.offset)
                data = stream.read(64 * 1024)
                self.offset += len(data)
        except FileNotFoundError:
            return []
        lines = (self.pending + data).split(b'\n')
        self.pending = lines.pop()
        if len(self.pending) > 64 * 1024:
            self.pending = b''
        result = []
        for line in lines:
            if not line.startswith(b'{'):
                continue
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    result.append(event)
            except (ValueError, UnicodeError):
                pass
        return result


def progress_message(event):
    from .sampling_progress import progress_message as sampling_message
    sampling = sampling_message(event)
    if sampling is not None:
        sampling['timing_phase'] = 'sampling'
        return sampling
    name = event.get('event')
    if name == 'encoder_phase':
        labels = {
            'worker_import': 'Starting text encoder',
            'worker_cuda_setup': 'Preparing text encoder GPU',
            'encoder_torch_import': 'Starting text encoder',
            'encoder_options': 'Starting text encoder',
            'encoder_cuda_setup': 'Preparing text encoder GPU',
            'encoder_path_config': 'Starting text encoder',
            'encoder_native_import': 'Starting text encoder',
            'encoder_media_prepare': 'Preparing reference media',
            'encoder_lookup': 'Checking text encoder cache',
            'encoder_load': 'Loading text encoder',
            'encoder_checkpoint_map': 'Reading text encoder weights',
            'encoder_construct': 'Preparing text encoder model',
            'encoder_tokenize': 'Preparing text and image tokens',
            'encoder_device_load': 'Loading text encoder onto GPU',
            'encoder_page_release': 'Preparing text encoding',
            'encoder_compute': 'Encoding text and images',
            'encoder_oom': 'Releasing encoder weights after insufficient GPU memory',
            'encoder_retry': 'Retrying text encoding with more GPU workspace',
            'encoder_conditioning_pack': 'Preparing prompt data',
            'keyframe_vae': 'Encoding reference media',
            'media_vae': 'Encoding reference media',
            'encoder_save': 'Saving prompt cache',
        }
        label = labels.get(event.get('stage'))
        return dict(label=label, timing_phase='encoding') if label else None
    if name == 'sampling_plan':
        first, second = event.get('first', {}), event.get('second')
        if event.get('enabled') and second:
            return dict(label='Two-pass · %d × %d → %d × %d · %d + %d steps' %
                        (first['width'], first['height'], second['width'], second['height'],
                         event.get('base_steps', 8), event.get('refine_steps', 3)),
                        detail='Audio is preserved from the first pass', sampling_plan=event)
        return dict(label='Single-pass · %d steps' % event.get('base_steps', 8), detail=event.get('reason'), sampling_plan=event)
    if name == 'latent_upscaler_prepare':
        return dict(label='Preparing two-pass upscaler', timing_phase='load')
    if name == 'latent_upscale':
        done, total = event.get('completed_steps', 8), event.get('total', 10)
        return dict(label='Upscaling before the second pass', phase='sampling', stage='latent_upscale',
                    done=done, total=total, display_fraction=done / total, timing_phase='sampling',
                    estimated_step_seconds=None, step_elapsed_seconds=0., remaining_seconds=None,
                    uniform_remaining_steps=False)
    if name == 'ram_budget_warning':
        # Kept in resource logs and diagnostics, not in generation progress:
        # crossing a soft estimate while memory is available needs no action.
        return None
    if name == 'compatibility':
        return {'label': 'Compatibility level %s · smaller work groups' % event.get('level'),
                'compatibility': event}
    if name == 'compute_device':
        return {'label': 'Compute device · %s · %s' % (event.get('backend', ''), event.get('name', '')),
                'device': event}
    if name == 'resource_retry':
        retry = event.get('retry') if isinstance(event.get('retry'), dict) else {}
        return {'phase': 'recovery', 'label': 'Adjusting memory placement and retrying', 'retry': retry}
    labels = {'encoding_start': 'Encoding prompt', 'encoder_load_start': 'Loading text encoder',
              'encoder_cache_lookup': 'Checking text encoder cache',
              'encoder_cache_hit': 'Reusing text encoder',
              'release_idle_cache': 'Releasing idle models and checking available memory again',
              'encoder_tokenize_start': 'Preparing text and image tokens',
              'encoder_device_load_start': 'Loading text encoder onto GPU',
              'encoder_device_reuse': 'Using text encoder already on GPU',
              'encoder_compute_start': 'Encoding text and images',
              'encoder_oom': 'Releasing encoder weights after insufficient GPU memory',
              'encoder_retry': 'Retrying text encoding with more GPU workspace',
              'conditioning_cache_hit': 'Reusing prompt cache', 'encoder_complete': 'Prompt ready',
              'video_start': 'Loading video model',
              'decode_resume': 'Reusing completed sampling · retrying video and audio decoding',
              'lora_prepare_start': 'Loading LoRAs',
              'resource_retry': 'Adjusting memory placement and retrying'}
    if name == 'decode_phase':
        return {'label': str(event.get('phase', 'Decoding video and audio')), 'timing_phase': 'decode'}
    if name == 'decode_progress':
        return dict(label='Decoding video tiles', timing_phase='decode', phase='decode', stage='video',
                    done=event.get('done'), total=event.get('total'), unit='frames',
                    elapsed_seconds=event.get('elapsed_seconds'))
    if name == 'media_encode_phase':
        return {'label': str(event.get('phase', 'Encoding input media')), 'timing_phase': 'encoding'}
    if name == 'prepared_blocks':
        return {'label': 'Loading cached video model · %s / 50 blocks' % event.get('blocks', '?'),
                'timing_phase': 'load'}
    if name in ('sampling_preset_download', 'reference_assets_download'):
        reference = name == 'reference_assets_download'
        return dict(label='Preparing reference media resources' if reference else 'Preparing sampling preset',
                    timing_phase='load', phase='load', stage='reference_download' if reference else 'preset_download',
                    done=event.get('done_bytes'), total=event.get('total_bytes'),
                    bytes_per_second=event.get('bytes_per_second'), unit='bytes')
    if name == 'loaded':
        return {'label': 'Video model ready', 'timing_phase': 'load'}
    if name == 'decode_resume':
        return {'label': 'Reusing completed sampling · retrying video and audio decoding',
                'timing_phase': 'decode'}
    if name == 'model_load_phase':
        label = str(event.get('phase', 'Loading video model'))
        if event.get('total'):
            label += ' · %s / %s blocks' % (event.get('done', 0), event['total'])
        return {'label': label, 'timing_phase': 'load'}
    if name == 'lora_prepare':
        return {'label': 'Loading LoRAs · %s / %s' % (event.get('done', '?'), event.get('total', '?')),
                'timing_phase': 'load'}
    if name in labels:
        phase = ('encoding' if name.startswith('encoding') or name.startswith('encoder_')
                 or name in ('conditioning_cache_hit', 'release_idle_cache') else
                 'load' if name in ('video_start', 'prepared_blocks', 'lora_prepare_start') else None)
        result = {'label': labels[name]}
        if phase:
            result['timing_phase'] = phase
        return result
    if name == 'freevideo_ui' and event.get('kind') == 'prediction':
        return {'label': event.get('detail') or event.get('label') or 'Resource forecast ready',
                'prediction': event}
    return None


def engine_environment(root, source, environ=None):
    if sys.platform == 'darwin':
        from .macos_bootstrap import environment
    else:
        from .triton_compat import environment
    return environment(root, isolated_environment(root, source, environ))


def generate(prompt, width, height, seconds, seed, output_directory, *,
             source=None, environ=None, metadata=None, progress=None, interrupted=None,
             release_models=None, export_inputs=None, two_pass=True, encoder_prewarm=None,
             force_regenerate=False, base_steps=8, refine_steps=3):
    if type(two_pass) is not bool:
        raise ValueError('Two-pass generation must be a boolean')
    if type(force_regenerate) is not bool:
        raise ValueError('Force regeneration must be a boolean')
    from .two_pass import validate_steps
    validate_steps(base_steps, refine_steps, two_pass)
    canvas = validate_request(prompt, width, height, seconds, seed)
    source = Path(source or source_root()).resolve()
    root, machine = installation(source, environ)
    environment = engine_environment(root, source, environ)
    environment.update(FREEVIDEO_HOME=str(root), PYTHONPATH=str(source), PYTHONUNBUFFERED='1',
                       PYTHONUTF8='1', PYTHONIOENCODING='utf-8', NO_COLOR='1', FREEVIDEO_UI_EVENTS='1')
    for key in ('FREEVIDEO_RUNTIME_LOCK_FD', 'FREEVIDEO_RUNTIME_LOCK_HANDLE'):
        environment.pop(key, None)
    # No attention, placement or capacity override: managed CLI uses the
    # installation's explicit caps, otherwise its live automatic policy.
    from .resource_settings import read as read_resources
    resources = read_resources(root, backend=machine.get('device_backend', 'cuda'))
    run = Path(output_directory).resolve() / 'FreeVideo' / time.strftime('%Y-%m-%d', time.gmtime()) / uuid.uuid4().hex
    run.mkdir(parents=True, exist_ok=False)
    output = run / 'video.mp4'
    (run / 'prompt.txt').write_text(prompt, encoding='utf-8')
    if metadata is not None:
        save(run / 'workflow.json', metadata)
    command = [machine['python'], '-B', '-m', 'freevideo_engine.managed', '--root', str(root),
               'generate', '--prompt-file', str(run / 'prompt.txt'), '--out', str(output),
               '--width', str(width), '--height', str(height), '--seconds', str(seconds), '--seed', str(seed),
               '--two-pass' if two_pass else '--no-two-pass',
               '--base-steps', str(base_steps), '--refine-steps', str(refine_steps)]
    for field, reserve in resources.items():
        if reserve is not None:
            command += ['--' + field.replace('_', '-'), str(reserve)]
    state = {'status': 'starting', 'geometry': canvas, 'seed': seed, 'installation': str(root),
             'command': command, 'output': str(output), 'source': str(source), 'resources': resources}
    from .diagnostic_resources import encoder_prewarm as prewarm_summary
    state['encoder_prewarm'] = prewarm_summary(encoder_prewarm)
    save(run / 'comfy-request.json', state)
    started = time.monotonic()
    whole_progress = WholeVideoProgress()
    from .comfy_progress import REPORTS
    report_id = REPORTS.register(output)
    def send_progress(message):
        if progress:
            progress(whole_progress.annotate(dict(message, report_id=report_id)))
    process = None
    from .result_cache import ResultCache, request_key, inspect_bounded
    cache = ResultCache(output_directory)
    cache_key = None
    tails = [EventTail(run / name) for name in ('generate.log', 'video.encoding.log', 'video.engine.log', 'video.lora.log')]
    def poll():
        if interrupted:
            interrupted()
        for index, tail in enumerate(tails):
            for event in tail.events():
                if index == 0 and event.get('event') == 'resource_retry':
                    tails[2].reset()
                message = progress_message(event)
                if message:
                    send_progress(message)
    try:
        if interrupted:
            interrupted()
        extra = {}
        if export_inputs:
            send_progress({'label': 'Retaining and checking input media', 'timing_phase': 'encoding'})
            extra = export_inputs(run, canvas) or {}
            if extra:
                if extra.get('conditioning'):
                    position = command.index('--prompt-file')
                    command[position:position + 2] = ['--conditioning', str(extra['conditioning'])]
                if extra.get('media'):
                    media_path = run / 'media.json'
                    save(media_path, extra['media'])
                    command += ['--media', str(media_path)]
            state['command'] = command
        from .media_request import task_for
        from .two_pass import plan
        planned = plan(canvas, two_pass, task_for(extra.get('media', {})), base_steps=base_steps, refine_steps=refine_steps)
        identify = lambda check: request_key(prompt, seed, canvas, planned, extra, machine,
                                             resources, source, environment, inspection=check)
        state['result_cache'] = dict(enabled=False, hit=False, forced=force_regenerate)
        reused = None
        if not force_regenerate:
            send_progress({'label': 'Checking saved video', 'timing_phase': 'encoding'})
            def inspect_saved(check):
                key = identify(check)
                return key, check.measure('output', lambda: cache.lookup(key, inspection=check))
            inspected, timing = inspect_bounded(inspect_saved, interrupted=interrupted)
            state['result_cache']['inspection'] = timing
            if inspected:
                cache_key, reused = inspected
                state['result_cache']['enabled'] = cache_key is not None
        save(run / 'comfy-request.json', state)
        if interrupted:
            interrupted()
        if reused is not None:
            state.update(status='reused', reused_output=str(reused))
            state['result_cache']['hit'] = True
            send_progress({'label': 'Reused previous result', 'phase': 'complete',
                           'result_cache_hit': True})
            return reused
        from .sampling_assets import prepare as prepare_sampling_assets
        def asset_progress(message):
            if progress:
                progress(dict(message, report_id=report_id))
        preparation_started = time.monotonic()
        try:
            preparation = prepare_sampling_assets(root, machine, planned, task_for(extra.get('media', {})),
                progress=asset_progress, interrupted=interrupted, environ=environment)
        except BaseException:
            elapsed = time.monotonic() - preparation_started
            state['sampling_cache_install'] = dict(status='failed', seconds=elapsed)
            started += elapsed
            raise
        state['sampling_cache_install'] = preparation
        if preparation['seconds']:
            started += preparation['seconds']
            whole_progress = WholeVideoProgress()
            send_progress({'reset': True})
        whole_progress.sampling_plan = planned
        whole_progress.forecast(progress_history_forecast(root, machine, dict(canvas, sampling_plan=planned)))
        send_progress({'label': 'Preparing %.3f s video + audio · %d × %d' %
                       (canvas['seconds'], width, height)})
        if release_models:
            release_models()
        state['encoder_prewarm'] = prewarm_summary(encoder_prewarm)
        if machine.get('device_backend') != 'mps' and (source/'freevideo_engine'/'resident_worker.py').is_file():
            from .resident_process import OWNER, ENV
            if environment.get('FREEVIDEO_KEEP_MODELS', 'auto').lower() in ('0', 'off', 'false'):
                OWNER.close()
                environment.pop(ENV, None)
            else:
                environment[ENV] = OWNER.start(root, source, machine['python'], environment)
                OWNER.active = True
        with (run / 'generate.log').open('wb') as log:
            process = processes.popen(command, env=environment, cwd=source, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True, supervise=True)
            state.update(status='running', pid=process.pid)
            save(run / 'comfy-request.json', state)
            while process.poll() is None:
                poll()
                try:
                    process.wait(timeout=.25)
                except subprocess.TimeoutExpired:
                    pass
            poll()
            if process.returncode:
                from .failure_details import generation_failure
                raise RuntimeError(generation_failure(run, process.returncode))
        report = json.loads(output.with_suffix('.request.json').read_text(encoding='utf-8'))
        engine = json.loads(output.with_suffix('.engine.json').read_text(encoding='utf-8'))
        from .two_pass import steps, plan
        completed_steps = steps(report.get('sampling_plan'))
        expected_plan = plan(canvas, two_pass, report.get('profile', {}).get('engine', {}).get('task', 't2va'),
                             base_steps=base_steps, refine_steps=refine_steps)
        if (report.get('success') is not True or engine.get('success') is not True
                or not output.is_file() or not output.stat().st_size
                or len(engine.get('step_seconds', [])) != completed_steps
                or engine.get('sampling_plan') != report.get('sampling_plan')
                or report.get('sampling_plan') != expected_plan
                or any(engine.get('geometry', {}).get(k) != canvas[k] for k in ('width', 'height', 'frames'))):
            from .failure_details import generation_failure
            raise RuntimeError('FreeVideo did not complete the requested video.\n' + generation_failure(run))
        state.update(status='complete', request_seconds=report.get('request_seconds'),
                     engine_report=str(output.with_suffix('.engine.json')))
        # Do not index a result against inputs/models that changed while it ran.
        # The resident session address is transport state, not compute config.
        if cache_key is not None:
            def store_result(check):
                if identify(check) != cache_key:
                    return False
                return check.measure('output', lambda: cache.remember(cache_key, output, inspection=check))
            stored, timing = inspect_bounded(store_result, interrupted=interrupted)
            state['result_cache'].update(stored=stored is True, store_inspection=timing)
        send_progress({'label': 'Video + audio saved', 'phase': 'complete', 'done': completed_steps, 'total': completed_steps})
        return output
    except BaseException as error:
        cancelled = isinstance(error, KeyboardInterrupt) or type(error).__name__ in ('InterruptProcessingException', 'CancelledError')
        state.update(status='cancelled' if cancelled else 'failed', error=repr(error))
        try:
            if cancelled:
                # Windows may terminate the CLI before its finally block runs.
                # Persist intent before stopping it so history cannot mistake
                # this deliberate cancellation for an unexplained device exit.
                save(run / 'comfy-request.json', state)
        finally:
            try:
                if process is not None:
                    processes.stop(process)
            finally:
                if cancelled:
                    # The resident worker belongs to ComfyUI, not to the managed
                    # request's process tree. Confirm it exits too before offering
                    # another generation; cancellation should release its memory.
                    from .resident_process import OWNER
                    OWNER.close()
        raise
    finally:
        from .resident_process import OWNER
        OWNER.active = False
        state['bridge_seconds'] = time.monotonic() - started
        state['bridge_wall_seconds'] = state['bridge_seconds'] + state.get('sampling_cache_install', {}).get('seconds', 0.)
        save(run / 'comfy-request.json', state)
        from .support_report import write as write_debug
        write_debug(output, bridge=state)
