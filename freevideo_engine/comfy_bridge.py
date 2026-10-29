"""ComfyUI orchestration without importing Torch or a model into its process.

The installed engine owns resource policy, input caches, retries and history.
This bridge only retains a request, supervises that CLI and forwards progress.
"""
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from collections import deque

from .long_paths import io_path, resolved, plain, plain_data, plain_errors
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


class _SafeProgress:
    """Any failure of the estimate falls back to plain progress messages."""
    def __init__(self, inner):
        self.inner = inner

    def _call(self, name, *args, **kwargs):
        try:
            return getattr(self.inner, name)(*args, **kwargs)
        except Exception:
            self.inner = _PlainProgress()  # A progress estimate cannot prevent generation.
            return getattr(self.inner, name)(*args, **kwargs)

    def annotate(self, message, *, event=None):
        return self._call('annotate', message, event=event)

    def observe(self, event):
        return self._call('observe', event)

    def heartbeat(self):
        return self._call('heartbeat')

    def configure(self, **facts):
        return self._call('configure', **facts)


class _PlainProgress:
    """Pass engine messages through unchanged when no estimate can be built."""
    def annotate(self, message, *, event=None):
        return dict(message)

    def observe(self, event):
        pass

    def heartbeat(self):
        return None

    def configure(self, **facts):
        pass


def _engine_version():
    from . import __version__
    return __version__


# Names emitted by both decoders; forecast keys use the same suffixes.
_DECODE_PARTS = {'Loading video VAE': 'vae_load', 'Decoding the video': 'video_decode',
                 'Loading and decoding audio': 'audio_load_decode', 'Saving MP4 and audio': 'encode'}


class WholeVideoProgress:
    """One elapsed/remaining clock, bounded by evidence of completed work.

    This display estimator never changes the inference plan. Stage predictions
    set the initial ruler; only real events release its count ceiling. Replay
    options control smoothing, silent countdown and the ceiling's time shares.
    """
    DECODE_PARTS = tuple(_DECODE_PARTS.values())

    def __init__(self, clock=time.perf_counter, *, hardware=None, request=None,
                 cold_flags=None, records=(), rate=.005, smoothing='track',
                 silence='stage', ceiling='stretch', tau=10., max_rise=.019, final=.98, slow_start=True, started=None):
        from . import stage_estimate
        from .effort_forecast import stage_forecast
        if not math.isfinite(rate) or rate < 0:
            raise ValueError('Progress smoothing rate must be finite and nonnegative')
        for name, value, choices in (('smoothing', smoothing, ('countdown', 'hold', 'track')),
                                     ('silence', silence, ('freeze', 'stage')),
                                     ('ceiling', ceiling, ('prior', 'current', 'stretch'))):
            if value not in choices:
                raise ValueError(f'Invalid progress {name}: {value!r}')
        self.clock, self.rate = clock, rate
        self.smoothing, self.silence, self.ceiling = smoothing, silence, ceiling
        # tau: time constant of a downward correction ('track'); max_rise: the
        # most the shown fraction may rise in any one second (None: no cap).
        self.tau, self.max_rise = tau, max_rise
        # final: once every frame is decoded, the bar approaches this fraction
        # over the predicted audio and save time (None: no approach).
        self.final = final
        # slow_start: on a 16 GB machine, a load much slower than predicted
        # scales the later stages until history or measured speed takes over.
        self.slow_start = slow_start
        self.started = self.phase_started = self.last_tick = self.last_event = clock() if started is None else started
        self.last_publish = self.started
        self.rise_window = deque()
        self.hardware = dict(hardware or {})
        self.request = dict(width=1344, height=768, frames=243, base_steps=8,
                            refine_steps=3, two_pass=True, flow='full', task='t2va')
        self.request.update({k: v for k, v in (request or {}).items() if v is not None})
        self.cold_flags = dict(cold_flags or {})
        self.records = records
        self.params = stage_estimate._params(None)
        self.prior = stage_estimate.predict_prior(self.hardware, self.request, self.cold_flags, self.params)
        self.weights = {k: self.prior['stages'][k]['seconds'] for k in stage_estimate.STAGES}
        local = stage_forecast(records, self.request, self.cold_flags, self.weights, version=_engine_version())
        self.remaining_source = 'local' if local.get('stages') else 'similar'
        self.typical = local['typical']
        self.local = local
        self.weights.update(local.get('stages', {}))
        self.completed = {k for k, v in self.weights.items() if v == 0}
        self.measured = {}
        self.spent = {}
        self.forecasts = dict(self.weights)
        self.phase_counts = {}
        self.reused = set()
        self.phase = 'encode'
        self.completed.discard(self.phase)
        self.sampling_plan = {}
        self.steps = {'pass1': {}, 'pass2': {}}
        self.live_calibration = True
        self.count = None
        self.fraction = 0.
        self.ceiling_base = 0.
        self.last_ceiling = 0.
        self.ceiling_weights = dict(self.weights)
        self.remaining_target = self.remaining_shown = sum(self.weights.values())
        self.total = self.remaining_target
        self.last_message = {}
        self.finished = False
        self.decode_remaining = None
        self.decode_parts = {}
        self.decode_part = None
        self.decode_rest = None
        self.decode_tail = max(0., self.weights['decode_save'] - self.prior['stages']['video_decode']['seconds'])
        self.retry_decode_only = False
        self.retry_reuse_first = False
        self.history = None  # (output directory, machine, opened): read late by _late_history.
        self.shown_source = self.remaining_source

    @classmethod
    def for_request(cls, machine, canvas, output_directory, *, base_steps=8,
                    refine_steps=3, two_pass=True, preview=False, upscale=False,
                    encoder_prewarm=None, options=None, step_cache=None, upscale_factor=2):
        import platform
        from .effort_forecast import start_context
        started, opened = time.perf_counter(), time.monotonic()
        observed, records = start_context(output_directory, machine)
        hardware = cls._hardware(observed, machine)
        # machine.vram_gib/ram_gib can be user policy caps, not physical totals.
        # A worker process being alive is not proof that a model is resident.
        warm = encoder_prewarm or {}
        flags = {'encode_cached': True if upscale else None}
        if warm.get('gpu_preload') is True and warm.get('state') in ('ready', 'complete') and not warm.get('worker_released'):
            flags['encoder_cold'] = False
        tracker = cls(hardware=hardware, records=records, cold_flags=flags, started=started,
                      request=dict(canvas, base_steps=base_steps, refine_steps=refine_steps,
                                   two_pass=two_pass, flow='upscale' if upscale else 'preview' if preview else 'full',
                                   step_cache=step_cache, upscale_factor=upscale_factor),
                      **(options or {}))
        tracker.history = (output_directory, machine, opened)
        return tracker

    @staticmethod
    def _hardware(observed, machine):
        import platform
        observed = dict(observed)
        if isinstance(machine.get('hardware'), dict):
            observed.update(machine['hardware'])
        device = machine.get('device_identity')
        device = device if isinstance(device, dict) else {}
        hardware = dict(gpu_name=observed.get('gpu_name') or machine.get('gpu_name') or device.get('name'),
                        compute_capability=observed.get('capability'),
                        os=observed.get('system') or machine.get('system') or platform.system())
        for target, source in (('ram_total_gib', 'ram_total'), ('vram_total_gib', 'vram_total')):
            value = observed.get(source) or device.get('unified_ram_bytes')
            if _timing_seconds(value):
                hardware[target] = value / 2**30
        return hardware

    def _late_history(self):
        """Use history the background read finished after this request began."""
        from .effort_forecast import start_context, history_read_at
        if self.history is None or self.remaining_source == 'measured':
            return
        output_directory, machine, opened = self.history
        if (history_read_at(output_directory, machine) or 0.) < opened:
            return
        self.history = None  # One late update per request.
        # Work on the live state, but restore all of it if anything fails.
        saved = {key: dict(value) if isinstance(value, dict) else value for key, value in vars(self).items()}
        try:
            observed, rows = start_context(output_directory, machine, timeout=0.)
            for key, value in self._hardware(observed, machine).items():
                if value is not None and self.hardware.get(key) is None:
                    self.hardware[key] = value
            self.records = rows
            self._reforecast()
            self.remaining_target = self._target(self.clock())
        except Exception:
            # Damaged history keeps the estimate the request already has.
            vars(self).clear()
            vars(self).update(saved)

    def configure(self, *, plan=None, media=None, conditioning=False, placement=None, sampled=None):
        """Use facts already obtained by the bridge; never inspect media/models."""
        self._late_history()
        from .media_request import task_for
        if sampled:
            # The engine samples this canvas, which can differ from the requested size.
            self.request.update({k: sampled[k] for k in ('width', 'height', 'frames') if sampled.get(k) is not None})
        if plan:
            self.sampling_plan = plan
            self.request.update(two_pass=bool(plan.get('enabled')), base_steps=plan.get('base_steps', 8),
                                refine_steps=plan.get('refine_steps', 0),
                                sampling_geometry={k: plan[k] for k in ('first', 'second') if plan.get(k)})
            for key in ('pass1_resident_blocks', 'pass2_resident_blocks'):
                value = plan.get(key)
                if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 50:
                    self.request[key] = value
        if placement:
            # generate.py's video_start/decode_resume carry the selected full
            # canvas engine/decoder plan. Pass 1 can admit extra temporary
            # cache, so its residency stays unknown for a two-pass request.
            engine, decoder = placement.get('engine'), placement.get('decoder')
            engine = engine if isinstance(engine, dict) else {}
            decoder = decoder if isinstance(decoder, dict) else {}
            blocks = engine.get('resident_blocks')
            if type(blocks) in (int, float) and math.isfinite(blocks) and 0 <= blocks <= 50:
                key = 'pass2_resident_blocks' if self.request['two_pass'] else 'pass1_resident_blocks'
                self.request[key] = blocks
            blocks = decoder.get('resident_blocks')
            if type(blocks) in (int, float) and math.isfinite(blocks) and 0 <= blocks <= 36:
                self.request['decoder_resident_blocks'] = blocks
            if type(decoder.get('offload')) is bool:
                self.request['decoder_offload'] = decoder['offload']
            if self.request.get('step_cache') is not None:
                # The planner keeps the first-block cache only where it fits; the
                # estimate follows the cache this placement actually uses.
                if engine.get('step_cache') is None:
                    self.request.pop('step_cache')
                else:
                    self.request['step_cache'] = engine['step_cache']
        if media is not None:
            self.request.update(task=task_for(media), lora_count=len(media.get('loras') or []),
                                reference_count=len(media.get('references') or []) +
                                sum(bool(media.get(k)) for k in ('first', 'last')))
        if conditioning:
            self.cold_flags['encode_cached'] = True
        self._reforecast()

    def _reforecast(self, *, reset_source=False):
        from .stage_estimate import predict_prior, STAGES
        from .effort_forecast import stage_forecast
        self.prior = predict_prior(self.hardware, self.request, self.cold_flags, self.params)
        weights = {k: self.prior['stages'][k]['seconds'] for k in STAGES}
        local = stage_forecast(self.records, self.request, self.cold_flags, weights, version=_engine_version())
        weights.update(local.get('stages', {}))
        self.typical = local['typical']
        self.local = local
        if reset_source or self.remaining_source != 'measured':
            self.remaining_source = 'local' if local.get('stages') else 'similar'
        for key, value in weights.items():
            if key not in self.completed:
                self.weights[key] = value
                self.forecasts[key] = value
        self.decode_tail = max(0., self.weights['decode_save'] - self.prior['stages']['video_decode']['seconds'])

    def forecast(self, value):
        """Consume older worker forecasts without closing or crediting a phase."""
        value = value.get('forecast', value) if isinstance(value, dict) else {}
        if not isinstance(value, dict):
            return
        stages = value.get('stages') or {}
        if not isinstance(stages, dict):
            return
        # Decoder substeps remain useful after sampling speed is measured.
        for part in self.DECODE_PARTS:
            seconds = _timing_seconds(stages.get(part + '_seconds'))
            if seconds is not None:
                self.decode_parts[part] = seconds
        # Worker predictions lack the bridge's two-record similarity evidence;
        # they must never create a local typical or replace live measurements.
        if self.remaining_source == 'measured':
            return
        mapping = {'encode': 'text_encode_seconds', 'load': 'load_seconds', 'decode_save': 'decode_save_seconds'}
        for key, source in mapping.items():
            seconds = _timing_seconds(stages.get(source))
            if seconds is not None and key not in self.completed:
                self.weights[key] = seconds
        sample = _timing_seconds(stages.get('sample_seconds'))
        if sample:
            keys = [k for k in ('pass1', 'upscale', 'pass2') if k not in self.completed]
            total = sum(self.weights[k] for k in keys)
            if total:
                for key in keys:
                    self.weights[key] *= sample / total
        work = _timing_seconds(stages.get('work_seconds'))
        parts = [_timing_seconds(stages.get(k)) for k in ('load_seconds', 'sample_seconds', 'decode_save_seconds')]
        if work and all(v is not None for v in parts) and 'decode_save' not in self.completed:
            self.weights['decode_save'] += max(0., work - sum(parts))
        self.forecasts.update({k: v for k, v in self.weights.items() if k not in self.completed})

    def _enter(self, phase, now):
        from .stage_estimate import STAGES
        if phase == self.phase:
            return
        self._late_history()
        if phase == 'load' and self.phase == 'upscale':
            # The model handed over between the passes belongs to the gap
            # between them, which is a stage of its own; a retry reloads
            # through _retry instead.
            return
        if self.phase is not None:
            self.spent[self.phase] = max(0., now - self.phase_started)
            self.phase_counts[self.phase] = self.count
            self.forecasts[self.phase] = self.weights[self.phase]
        # A brief model check can interrupt encoding, and loading can return
        # between passes. Only predecessors of the active phase are finished.
        self.completed = (set(STAGES[:STAGES.index(phase)]) | self.reused) - {phase}
        for key in STAGES:
            if key in self.completed and key in self.spent:
                self.measured[key] = self.weights[key] = max(.001, self.spent[key])
            elif key in self.measured:
                self.measured.pop(key)
                self.weights[key] = max(self.forecasts.get(key, 0.), self.spent.get(key, 0.))
        self.phase, self.phase_started = phase, now - self.spent.get(phase, 0.)
        self.count = self.phase_counts.get(phase, self._initial_count())
        self.decode_remaining = None

    def _initial_count(self):
        from .stage_estimate import canvases, step_counts
        if self.phase == 'load':
            return (0, 50, 1)
        if self.phase in ('pass1', 'pass2'):
            count = step_counts(self.request)[0 if self.phase == 'pass1' else 1]
            return (0, count * 50, 1)
        if self.phase == 'decode_save':
            return (0, canvases(self.request)[2]['frames'], 1)
        return None

    def _set_count(self, done, total, margin=1):
        # Duplicate/delayed logs cannot undo already witnessed counts. Using
        # the smallest known unit avoids lowering the ceiling on the first
        # counted event after an uncounted stage-start notification.
        candidate = (max(0, done), total, margin)
        old = self.count
        if old is None or (candidate[0] + margin) / max(total, 1) >= (old[0] + old[2]) / max(old[1], 1):
            self.count = candidate

    def _phase_for(self, message, event):
        name = event.get('event')
        if self.retry_decode_only:
            return 'decode_save'
        if name == 'decode_resume' or message.get('phase') == 'sample_finalize':
            return 'decode_save'
        if message.get('stage') in ('latent_upscale', 'first_pass_reused'):
            return 'upscale'
        phase = message.get('timing_phase') or message.get('phase')
        if phase == 'encoding' and any(self.steps.values()):
            # After synchronized sampling steps, encoder-file lines are late
            # notifications from the finished prompt, not a new encoding run.
            return None
        if phase == 'sampling':
            # `loaded` falsely looks like sampling 0/N in the legacy converter.
            if name == 'loaded':
                return 'load'
            done = message.get('done', 0)
            base = self.request['base_steps']
            if self.request['flow'] == 'upscale' or self.retry_reuse_first:
                return 'pass2'
            if self.request['flow'] != 'preview' and self.request['two_pass']:
                if done > base or (done == base and (name == 'sampling_start' or message.get('stage') in ('layers', 'inputs', 'kernel_prepare'))):
                    return 'pass2'
            return 'pass1'
        return {'encoding': 'encode', 'load': 'load', 'decode': 'decode_save'}.get(phase) if isinstance(phase, str) else None

    def _retry(self, retry, now):
        # A retry changes the plan; the earlier load no longer says how fast
        # this machine is, and measured steps arrive again soon.
        self.slow_start = False
        self.retry_decode_only = retry.get('reuse_sampling') is True
        self.retry_reuse_first = retry.get('reuse_first_pass') is True
        start = 'decode_save' if self.retry_decode_only else 'load'
        if not self.retry_decode_only:
            self.steps['pass2'].clear()
            if not retry.get('reuse_first_pass'):
                self.steps['pass1'].clear()
        from .stage_estimate import STAGES
        self.spent = {k: v for k, v in self.spent.items() if STAGES.index(k) < STAGES.index(start)}
        self.phase_counts = {}
        self.decode_part, self.decode_rest = None, None
        self.reused = set(STAGES[:STAGES.index(start)])
        self.completed = set(self.reused)
        if self.retry_reuse_first and not self.retry_decode_only:
            self.completed.add('pass1')
            self.reused.add('pass1')
        self.phase, self.phase_started = start, now
        self.count, self.decode_remaining = self._initial_count(), None
        self.live_calibration = False
        # Recovery can change placement. Wait for the new attempt's plan;
        # retained step durations are not new measurements of pending work.
        for key in ('pass1_resident_blocks', 'pass2_resident_blocks',
                    'decoder_resident_blocks', 'decoder_offload'):
            self.request.pop(key, None)
        self._reforecast(reset_source=True)
        self.ceiling_base = self.fraction
        self.ceiling_weights = {k: v for k, v in self.weights.items() if k not in self.completed}

    def _counts_and_live(self, message, event, now):
        from .stage_estimate import predict_live, step_counts
        phase = self.phase
        done, total = message.get('done'), message.get('total')
        if phase in ('pass1', 'pass2'):
            index = 0 if phase == 'pass1' else 1
            count = step_counts(self.request)[index]
            offset = self.request['base_steps'] if index else 0
            if type(done) is int:
                local_done = max(0, min(count, done - offset))
                blocks, block = message.get('blocks'), message.get('block')
                if type(blocks) is int and blocks > 0 and type(block) is int:
                    # One step of margin: layer events are dispatch estimates,
                    # steps are the synchronized counts (a first step may compile).
                    self._set_count(local_done * blocks + max(0, block - 1), count * blocks,
                                    blocks if self.ceiling == 'stretch' else 1)
                else:
                    # Keep the same one-layer margin across step boundaries;
                    # changing from one step to one layer could lower a ceiling.
                    self._set_count(local_done * 50, count * 50, 50 if self.ceiling == 'stretch' else 1)
                seconds = _timing_seconds(message.get('last_step_seconds'))
                if seconds and local_done > 0:
                    self.steps[phase][local_done] = seconds
                    if 1 in self.steps[phase] and 2 in self.steps[phase]:
                        self.live_calibration = True
        elif phase == 'load':
            if event.get('event') == 'loaded':
                total = self.count[1] if self.count else 50
                done = total
            elif event.get('event') == 'prepared_blocks':
                done, total = message.get('done'), message.get('total')
            elif event.get('event') == 'model_load_phase':
                done, total = event.get('done'), event.get('total')
            # LoRA groups describe a substage, not completion of the whole load.
            if type(done) is int and type(total) is int and total > 0 and message.get('unit') != 'bytes':
                self._set_count(done, total)
        elif phase == 'decode_save' and message.get('stage') == 'video':
            elapsed = _timing_seconds(message.get('elapsed_seconds'))
            if type(done) is int and type(total) is int and 0 <= done <= total and total > 0:
                self._set_count(done, total)
                if elapsed and done > 0:
                    self.decode_remaining = elapsed * (total - done) / done + self._after_frames(total)
                    self.weights[phase] = now - self.phase_started + self.decode_remaining
                    self.remaining_source = 'measured'
        elif phase == 'decode_save' and (event.get('event') == 'decode_phase' or message.get('decode_part')):
            # These real transitions finish video frames even for a decoder
            # that does not stream progress callbacks.
            part = message.get('decode_part') or _DECODE_PARTS.get(event.get('phase') if isinstance(event.get('phase'), str) else '')
            if part in self.DECODE_PARTS and part != self.decode_part:
                rest = self.DECODE_PARTS[self.DECODE_PARTS.index(part):]
                self.decode_part = part
                if all(k in self.decode_parts for k in rest):
                    self.decode_rest = sum(self.decode_parts[k] for k in rest[1:])
                    self.decode_remaining = self.decode_parts[part] + self.decode_rest
                    self.weights[phase] = now - self.phase_started + self.decode_remaining
            if part in ('audio_load_decode', 'encode'):
                total = self.count[1] if self.count else self.request['frames']
                self._set_count(total, total)
                if self.decode_remaining is None:
                    # Without frame callbacks this is the first sign that the
                    # video is decoded; only the measured tail remains.
                    self.decode_remaining = self._after_frames(total)
                    self.weights[phase] = now - self.phase_started + self.decode_remaining
                    self.remaining_source = 'measured'
        # Only contiguous, actually completed steps calibrate the model.
        steps = {}
        for key, values in self.steps.items():
            steps[key] = []
            for i in range(1, len(values) + 1):
                if i not in values:
                    break
                steps[key].append(values[i])
        if self.live_calibration and any(len(v) >= 2 for v in steps.values()):
            done_stages = [('decode' if k == 'decode_save' else k) for k in self.completed]
            live = predict_live(dict(hardware=self.hardware, request=self.request, cold_flags=self.cold_flags,
                pass1_steps=steps['pass1'], pass2_steps=steps['pass2'], completed_stages=done_stages,
                current_stage=phase, step_elapsed_seconds=message.get('step_elapsed_seconds', 0.),
                pass_wall_overhead_done=[k for k, v in steps.items() if v]), self.params)
            # With matching history on this machine, scale its own stage times
            # by the measured warm step instead of the general model.
            from statistics import median
            history, warm, ratio = self.local.get('stages') or {}, self.local.get('warm') or {}, None
            for key in ('pass2', 'pass1'):
                if len(steps[key]) >= 2 and warm.get(key):
                    ratio = min(2., max(.5, median(steps[key][1:]) / warm[key]))
                    break
            for key in ('pass1', 'upscale', 'pass2', 'decode_save'):
                if key in self.completed or (key == 'decode_save' and self.decode_remaining is not None):
                    continue
                if ratio is not None and key in history:
                    self.weights[key] = history[key] * (1. if key == 'decode_save' else ratio)
                    continue
                remaining = live['stages'][key]['seconds']
                if key == phase:
                    if key == 'decode_save':
                        # decode/save prediction is a full duration until frames arrive.
                        self.weights[key] = remaining
                    else:
                        self.weights[key] = now - self.phase_started + remaining
                else:
                    self.weights[key] = remaining
            self.decode_tail = max(0., live['stages']['decode_save']['seconds'] - live['stages']['video_decode']['seconds'])
            self.remaining_source = 'measured'

    def _after_frames(self, frames):
        # Audio, MP4 and saving after the last decoded frame: about 2 s plus
        # 0.03 s per frame (measured, slightly below the median, so the bar
        # rarely jumps at saving), never more than the model's tail.
        if self.decode_rest is not None and self.decode_part == 'video_decode':
            return self.decode_rest
        return min(self.decode_tail, 2. + .03 * frames)

    def _slow_factor(self, now):
        # A disk-bound 16 GB machine that loads much slower than predicted is
        # slower in every later stage too (reports: about the square root of
        # the load ratio). Larger memory or a slow encode does not predict it.
        if not self.slow_start or self.remaining_source != 'similar':
            return 1.
        ram = self.hardware.get('ram_total_gib')
        if ram is None or ram > 16.5:
            return 1.
        predicted = self.prior['stages']['load']['seconds']
        actual = self.measured.get('load') or (now - self.phase_started if self.phase == 'load' else None)
        if not actual or predicted <= 1 or actual / predicted <= 2:
            return 1.
        return min(4., math.sqrt(actual / predicted))

    def _target(self, now):
        factor = self._slow_factor(now)
        later = ('pass1', 'upscale', 'pass2', 'decode_save')
        remaining = sum(v * (factor if k in later else 1.)
                        for k, v in self.weights.items() if k not in self.completed and k != self.phase)
        elapsed = max(0., now - self.phase_started)
        # The stage under way is scaled too until its speed is measured.
        current = self.weights.get(self.phase, 0.) * (factor if self.phase in later else 1.) - elapsed
        if current < 0:
            current = max(3., .1 * elapsed)
        return max(0., remaining + current)

    def _ceiling(self, now):
        weights = (self.ceiling_weights if self.ceiling == 'prior' else
                   {k: self.weights[k] for k in self.ceiling_weights})
        stretch = self.ceiling == 'stretch' and (self.count is None or self.count[0] <= 0)
        if stretch and self.phase in weights:
            # An overrunning stage without counted work keeps its end ahead of
            # it, by the same remainder the remaining-time target assumes.
            elapsed = max(0., now - self.phase_started)
            weights[self.phase] = max(weights[self.phase], elapsed + max(3., .1 * elapsed))
        total = sum(weights.values())
        if total <= 0:
            if self.ceiling in ('current', 'stretch'):
                self.last_ceiling = .99
            return .99
        credit = sum(v for k, v in weights.items() if k in self.completed)
        # An uncounted stage earns its allowance over time, not at its entry.
        # This also avoids releasing all of upscale's share at a boundary.
        duration = weights.get(self.phase, 0.)
        fraction = min(1., max(0., now - self.phase_started) / max(duration, .001))
        if self.count is not None and not stretch:
            done, count, margin = self.count
            fraction = min(1., max(0., (done + margin) / max(count, 1)))
        credit += duration * fraction
        value = min(.99, self.ceiling_base + (1. - self.ceiling_base) * credit / total)
        if self.ceiling in ('current', 'stretch'):
            self.last_ceiling = max(self.last_ceiling, value)
            value = self.last_ceiling
        return value

    def _smooth(self, dt):
        if self.smoothing == 'track':
            shown, target = self.remaining_shown, self.remaining_target
            if target > shown:
                # Slower than predicted: stop counting down, then rise gently.
                # A time that ran out starts again from the remainder an
                # overrunning stage keeps; from zero it could never rise.
                shown = max(shown, min(target, 3.))
                self.remaining_shown = min(target, shown + self.rate * shown * dt)
            else:
                shown = max(target, shown - dt)
                self.remaining_shown = max(0., shown - (shown - target) * (1. - math.exp(-dt / max(self.tau, .001))))
            return
        # Hold suppresses countdown only for an upward target correction.
        # Both policies approach the target without crossing it by correction;
        # countdown itself can pass below it. Keep a positive running remainder.
        if self.smoothing == 'hold' and self.remaining_target > self.remaining_shown:
            shown = self.remaining_shown
        else:
            floor = 3. if self.smoothing == 'countdown' else min(3., self.remaining_shown)
            shown = max(floor, self.remaining_shown - dt)
        limit = self.rate * shown * dt
        self.remaining_shown = max(0., shown + max(-limit, min(limit, self.remaining_target - shown)))

    def _render(self, message, now, *, heartbeat=False):
        if self.remaining_source == 'local' and self.shown_source == 'similar':
            # History read after the start replaces the estimate from similar
            # settings: this computer's record corrects it once, so do not smooth.
            # (A first measurement keeps smoothing; two steps are still noisy.)
            self.remaining_shown = self.remaining_target
        self.shown_source = self.remaining_source
        elapsed = max(0., now - self.started)
        candidate = elapsed / max(elapsed + self.remaining_shown, .001)
        if (self.final is not None and self.phase == 'decode_save' and self.count
                and self.count[0] >= self.count[1] > 0):
            # Every frame is decoded; only audio, MP4 and saving remain, often
            # for a few seconds only. Move there as fast as the rise limit allows.
            candidate = max(candidate, self.final)
        ceiling = self._ceiling(now)
        limit = .99
        # Retain the value in force one second ago and all newer changes.
        while len(self.rise_window) > 1 and self.rise_window[1][0] <= now - 1.:
            self.rise_window.popleft()
        if self.max_rise is not None:
            base = self.rise_window[0][1] if self.rise_window else self.fraction
            limit = min(limit, base + self.max_rise)
        self.fraction = max(self.fraction, min(limit, candidate, ceiling))
        if not self.rise_window or self.rise_window[-1][1] != self.fraction:
            if len(self.rise_window) > 1 and self.rise_window[-1][0] == now:
                self.rise_window.pop()
            self.rise_window.append((now, self.fraction))
        overall = dict(status='complete' if self.finished else 'running', estimated=True,
                       fraction=1. if self.finished else self.fraction,
                       remaining_seconds=0. if self.finished else self.remaining_shown,
                       elapsed_seconds=elapsed, remaining_source=self.remaining_source,
                       phase_start_fraction=self.fraction, phase_weight=0., remaining_floor_seconds=0.,
                       at_ceiling=not self.finished and candidate >= ceiling,
                       sampling_time_weighted=True)
        key = self.phase
        if key in self.steps:
            key += '_first' if not self.steps[key] else '_warm'
        if key in self.typical:
            overall.update(typical_seconds=self.typical[key], typical_source='local')
        if heartbeat:
            overall['heartbeat'] = True
        self.last_publish = now
        return dict(message, overall=overall)

    def annotate(self, message, *, event=None):
        message, event = dict(message), event or {}
        now = self.clock()
        if self.finished:
            return self._render(message, now)
        dt = max(0., now - self.last_tick)
        old_phase, old_part = self.phase, self.decode_part
        waiting_for_pass = (self.phase in self.steps and self.count is not None
                            and self.count[0] >= self.count[1] > 0)
        plan = event if event.get('event') == 'sampling_plan' else message.get('sampling_plan')
        if isinstance(plan, dict):
            self.configure(plan=plan)
        if isinstance(message.get('prediction'), dict):
            self.forecast(message['prediction'])
        retried = isinstance(message.get('retry'), dict) or message.get('stage') == 'preview_mismatch'
        if isinstance(message.get('retry'), dict):
            self._retry(message['retry'], now)
        if message.get('stage') == 'preview_mismatch':
            self.request['flow'] = 'full'
            self._retry({}, now)
        if event.get('event') in ('video_start', 'decode_resume'):
            self.configure(placement=event)
        phase = self._phase_for(message, event)
        if phase:
            self._enter(phase, now)
            if phase in self.steps and phase != old_phase and phase not in self.spent:
                # A first layer reports time inside its step, not the model
                # handover before it. Backdate only that witnessed work.
                inside = _timing_seconds(message.get('step_elapsed_seconds')) or 0.
                self.phase_started = now - inside
                if waiting_for_pass:
                    dt = min(dt, inside)
        if event.get('event') == 'conditioning_cache_hit':
            self.cold_flags['encode_cached'] = True
        if event.get('event') == 'conditioning_reused':
            self.cold_flags['encode_cached'] = True
        if event.get('event') == 'encoder_device_reuse':
            self.cold_flags['encoder_cold'] = False
        if event.get('event') == 'compute_device':
            self.hardware.update(gpu_name=event.get('name') or self.hardware.get('gpu_name'))
            if _timing_seconds(event.get('vram_total_bytes')):
                self.hardware['vram_total_gib'] = event['vram_total_bytes'] / 2**30
            self._reforecast()
        if not retried:
            self._counts_and_live(message, event, now)
        self.remaining_target = self._target(now)
        self._smooth(dt)
        if retried:
            # A new attempt is estimated afresh: the time left of the attempt
            # that failed says nothing about it, and may already have run out.
            self.remaining_shown = self.remaining_target
        if self.phase == 'decode_save' and self.decode_rest is not None and (
                self.decode_part != old_part or message.get('stage') == 'video'):
            # A decoder boundary gives the pending substeps directly; the
            # fraction still obeys its monotonic, per-second rise limit.
            self.remaining_shown = self.remaining_target
        self.last_tick = self.last_event = now
        self.total = sum(self.weights.values())
        self.finished = message.get('phase') == 'complete' or message.get('overall', {}).get('status') == 'complete'
        if self.finished:
            self.measured[self.phase] = max(.001, now - self.phase_started)
            self.weights[self.phase] = self.measured[self.phase]
            self.total = sum(self.weights.values())
            self.completed.update(self.weights)
            self.remaining_target = self.remaining_shown = 0.
            self.fraction = 1.
        if message.keys() - {'report_id'}:
            self.last_message = {k: v for k, v in message.items() if k not in ('overall', 'reset', 'new_request', 'prediction', 'sampling_plan')}
        else:
            message = dict(self.last_message, **message)
        return self._render(message, now)

    def observe(self, event):
        """Use an engine event that has no progress message, without publishing."""
        published, window = self.last_publish, self.rise_window.copy()
        self.annotate({}, event=event)
        self.last_publish, self.rise_window = published, window

    def heartbeat(self):
        now = self.clock()
        if self.finished or now - self.last_publish < 1.:
            return None
        if (self.phase in self.steps and self.count is not None
                and self.count[0] >= self.count[1] > 0):
            # Between passes no pending step time is being spent.
            self.last_tick = now
        elif self.silence == 'stage':
            # Admit only the part of this interval inside the predicted stage.
            # Crossing its end freezes at the boundary, even with sparse polls.
            end = self.phase_started + self.weights.get(self.phase, 0.)
            dt = max(0., min(now, end) - max(self.last_tick, self.phase_started))
            if dt:
                self.remaining_target = max(0., self.remaining_target - dt)
                self._smooth(dt)
            self.last_tick = max(self.last_tick, min(now, end))
        # Silent time is not counted down until the next engine event shows
        # the engine is still working; that event then counts all of it.
        return self._render(self.last_message, now, heartbeat=True)


def source_root():
    return Path(__file__).resolve().parents[1]


@plain_errors
def installation_root(source=None, environ=None):
    source = io_path(source or source_root())
    environ = os.environ if environ is None else environ
    selected = environ.get('FREEVIDEO_HOME')
    config = source / 'comfyui.json'
    if not selected and config.is_file():
        selected = json.loads(config.read_text(encoding='utf-8')).get('installation')
        if not isinstance(selected, str) or not selected.strip():
            raise ValueError('comfyui.json must contain an installation directory.')
    root = io_path(Path(selected).expanduser()) if selected else source
    if not root.is_absolute():
        root = source / root
    return Path(plain(resolved(root)))


@plain_errors
def installation(source=None, environ=None):
    from .comfy_launcher_api import repair_hint
    root = installation_root(source, environ)
    try:
        machine = json.loads((io_path(root) / 'machine.json').read_text(encoding='utf-8'))
    except FileNotFoundError as error:
        raise ValueError('FreeVideo setup is missing. ' + repair_hint() + ' Or run setup.cmd (Windows) or setup.sh (Linux) '
                         'in the FreeVideo folder, or set FREEVIDEO_HOME to your existing installation. '
                         'A prepared engine installation is required.') from error
    required = ('root', 'python', 'cache', 'base', 'checkpoint', 'comfy_python',
                'comfy_root', 'vdn_root', 'model_paths', 'model_root', 'encoder')
    if not isinstance(machine, dict) or not machine.get('ready') or any(not machine.get(k) for k in required):
        raise ValueError('FreeVideo setup is incomplete in %s. %s Existing files are reused.' % (root, repair_hint()))
    identity = 'device_identity' if machine.get('device_backend') == 'mps' else 'gpu_uuid'
    if not machine.get(identity):
        raise ValueError('FreeVideo device configuration is missing. ' + repair_hint())
    if resolved(Path(machine['root']).expanduser()) != resolved(root):
        raise ValueError('The FreeVideo installation was moved. Rerun setup in the selected folder.')
    if not io_path(machine['python']).is_file():
        raise ValueError('The FreeVideo Python environment is missing. Rerun setup in %s.' % root)
    return root, plain_data(machine)


def validate_request(prompt, width, height, seconds, seed, *, continue_from=None, frames=None,
                     two_pass=True, preview=False, refine_from=None, media=None, original_audio=False):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('Enter a prompt describing the video and its audio.')
    if type(seed) is not int or not 0 <= seed <= 2**53 - 1:
        raise ValueError('Seed must be an integer between 0 and 9007199254740991.')
    if frames is not None:
        from .continuation import validate_frames
        validate_frames(frames)
    elif isinstance(seconds, bool) or not isinstance(seconds, (float, int)) or not math.isfinite(seconds):
        raise ValueError('Duration must be a positive number of seconds.')
    canvas = geometry(width, height, frames=frames, seconds=None if frames is not None else seconds)
    if continue_from is not None:
        from .continuation import validate_path, validate_route
        validate_route(two_pass=two_pass, preview=preview, refine_from=refine_from, media=media,
                       original_audio=original_audio)
        validate_path(continue_from)
    return canvas


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


def preview_state(event):
    """The few facts the page needs to show the newest frame preview, or None."""
    values = {key: event.get(key) for key in ('serial', 'step', 'total', 'frames', 'frame_width', 'frame_height')}
    if any(type(value) is not int or value < 1 or value > 100000 for value in values.values()) or values['frames'] > 8:
        return None
    return values


def preview_update(current, event):
    """The page's frame picture after a frame_preview / frame_preview_clear event."""
    if event.get('event') == 'frame_preview_clear':
        return None
    return preview_state(event) or current


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
            'encoder_tokenize': 'Preparing the prompt and images',
            'encoder_device_load': 'Loading text encoder onto GPU',
            'encoder_page_release': 'Preparing text encoding',
            'encoder_compute': 'Encoding text and images',
            'encoder_oom': 'Releasing encoder weights after insufficient GPU memory',
            'encoder_retry': 'Retrying text encoding with more GPU workspace',
            'encoder_spill': 'Leaving more GPU memory for text encoding',
            'encoder_low_memory': 'Encoding long references',
            'encoder_conditioning_pack': 'Preparing prompt data',
            'keyframe_vae': 'Encoding reference media',
            'media_vae': 'Encoding reference media',
            'encoder_save': 'Saving prompt cache',
        }
        label = labels.get(event.get('stage'))
        return dict(label=label, timing_phase='encoding') if label else None
    if name == 'sampling_plan':
        first, second = event.get('first', {}), event.get('second')
        if event.get('enabled') and event.get('preview'):
            return dict(label='First-pass preview · %d × %d · %d steps' % (first['width'], first['height'], event.get('base_steps', 8)),
                        detail='Run the second pass from the result', sampling_plan=event)
        if event.get('enabled') and second:
            return dict(label='Two-pass · %d × %d → %d × %d · %d + %d steps' %
                        (first['width'], first['height'], second['width'], second['height'],
                         event.get('base_steps', 8), event.get('refine_steps', 3)),
                        detail='Audio is preserved from the first pass', sampling_plan=event)
        return dict(label='Single-pass · %d step%s' % (event.get('base_steps', 8), '' if event.get('base_steps', 8) == 1 else 's'), detail=event.get('reason'), sampling_plan=event)
    if name == 'latent_upscaler_prepare':
        return dict(label='Preparing two-pass upscaler', timing_phase='load')
    if name == 'first_pass_reused':
        done, total = event.get('completed_steps', 8), event.get('total', 11)
        return dict(label='Continuing from the preview' if event.get('source') == 'preview'
                    else 'Reusing the completed first pass', phase='sampling', stage='first_pass_reused',
                    done=done, total=total, display_fraction=done / total, timing_phase='sampling',
                    estimated_step_seconds=None, step_elapsed_seconds=0., remaining_seconds=None,
                    uniform_remaining_steps=False)
    if name == 'preview_mismatch':
        return dict(label='The preview no longer matches; sampling its first pass again', stage='preview_mismatch')
    if name == 'low_memory_mode':
        return dict(label='Low-memory mode for this GPU', timing_phase='load',
                    low_memory=dict(staging=bool(event.get('staging')), host_outputs=bool(event.get('host_outputs'))))
    if name == 'unified_memory_tight':
        # Mac, before encoding: other applications leave less unified memory
        # than the largest sampling pass needs. Closing them during the first
        # pass can still let it fit, so the page says so for the whole request.
        return dict(label='Preparing video', timing_phase='load',
                    memory_tight=dict(phase=event.get('phase'), needed_bytes=event.get('needed_bytes'),
                                      allowance_bytes=event.get('allowance_bytes'),
                                      first_pass_steps=event.get('first_pass_steps')))
    if name == 'latent_upscale':
        done, total = event.get('completed_steps', 8), event.get('total', 10)
        return dict(label='Upscaling before the second pass', phase='sampling', stage='latent_upscale',
                    done=done, total=total, display_fraction=done / total, timing_phase='sampling',
                    estimated_step_seconds=None, step_elapsed_seconds=event.get('step_elapsed_seconds', 0.),
                    remaining_seconds=None, uniform_remaining_steps=False)
    if name == 'ram_budget_warning':
        # Kept in resource logs and diagnostics, not in generation progress:
        # crossing a soft estimate while memory is available needs no action.
        return None
    if name == 'compatibility':
        from .compatibility import LEVELS, RECOVERY_SUCCESSES
        level = event.get('level')
        if type(level) is not int or not 1 <= level <= 3:
            return None
        return {'compatibility': dict(level=level,
                                      name_en=LEVELS[level]['en'].split(' · ', 1)[0],
                                      name_zh=LEVELS[level]['zh'].split(' · ', 1)[0],
                                      origin=event.get('origin'),
                                      consecutive_successes=event.get('consecutive_successes', 0),
                                      recovery_successes_required=event.get('recovery_successes_required', RECOVERY_SUCCESSES),
                                      recovery_disabled=event.get('recovery_disabled', False))}
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
              'encoder_tokenize_start': 'Preparing the prompt and images',
              'encoder_device_load_start': 'Loading text encoder onto GPU',
              'encoder_device_reuse': 'Using text encoder already on GPU',
              'encoder_compute_start': 'Encoding text and images',
              'encoder_oom': 'Releasing encoder weights after insufficient GPU memory',
              'encoder_retry': 'Retrying text encoding with more GPU workspace',
              'conditioning_cache_hit': 'Reusing prompt cache', 'encoder_complete': 'Prompt ready',
              'video_start': 'Loading the video model',
              'decode_resume': 'Reusing completed sampling · retrying video and audio decoding',
              'lora_prepare_start': 'Loading LoRAs',
              'resource_retry': 'Adjusting memory placement and retrying'}
    if name == 'decode_phase':
        return {'label': str(event.get('phase', 'Decoding video and audio')), 'timing_phase': 'decode'}
    if name == 'decode_progress':
        return dict(label='Decoding the video', timing_phase='decode', phase='decode', stage='video',
                    done=event.get('done'), total=event.get('total'), unit='frames',
                    elapsed_seconds=event.get('elapsed_seconds'))
    if name == 'media_encode_phase':
        return {'label': str(event.get('phase', 'Encoding input media')), 'timing_phase': 'encoding'}
    if name == 'reference_trimmed':
        return {'label': 'Preparing reference media', 'timing_phase': 'encoding',
                'reference_trimmed': {key: event.get(key) for key in ('kind', 'number', 'seconds', 'used_seconds')}}
    if name == 'prepared_blocks':
        # Old engine logs omitted the total for the pinned 50-block model.
        # Current workers report the actual model length, including the last
        # block when that length is not a multiple of five.
        result = {'label': 'Loading the video model', 'timing_phase': 'load'}
        done, total = event.get('blocks'), event.get('total', 50)
        if type(done) is int and type(total) is int and total > 0 and 0 <= done <= total:
            result.update(done=done, total=total)
        return result
    if name in ('sampling_preset_download', 'reference_assets_download'):
        reference = name == 'reference_assets_download'
        return dict(label='Preparing reference media resources' if reference else 'Preparing sampling preset',
                    timing_phase='load', phase='load', stage='reference_download' if reference else 'preset_download',
                    done=event.get('done_bytes'), total=event.get('total_bytes'),
                    bytes_per_second=event.get('bytes_per_second'), unit='bytes')
    if name == 'adaln_sources_download':
        # A step count without published tables, or an FP8 LoRA that changes
        # modulation: the original modulation weights, downloaded once.
        return dict(label='Downloading extra model weights (one-time download)',
                    timing_phase='load', phase='load', stage='sources_download',
                    done=event.get('done_bytes'), total=event.get('total_bytes'),
                    bytes_per_second=event.get('bytes_per_second'), unit='bytes')
    if name == 'loaded':
        return {'label': 'Video model ready', 'timing_phase': 'load'}
    if name == 'decode_resume':
        return {'label': 'Reusing completed sampling · retrying video and audio decoding',
                'timing_phase': 'decode'}
    if name == 'model_load_phase':
        return {'label': str(event.get('phase', 'Loading the video model')), 'timing_phase': 'load',
                **{key: event[key] for key in ('done', 'total') if key in event}}
    if name == 'lora_prepare':
        return {'label': 'Loading LoRAs', 'timing_phase': 'load',
                **{key: event[key] for key in ('done', 'total') if key in event}}
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


def preview_request(output_directory, relative):
    """The saved request of a finished preview in this output folder."""
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError('Choose a preview to upscale')
    output_root = Path(output_directory).resolve()
    video = (output_root / relative).resolve()
    try:
        video.relative_to(output_root / 'FreeVideo')
    except ValueError:
        raise ValueError('Choose a FreeVideo preview from this output folder') from None
    path = video.parent / 'comfy-request.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    if (not isinstance(state, dict) or state.get('preview') is not True or state.get('status') != 'complete'
            or Path(state.get('output', '')).resolve() != video or not isinstance(state.get('command'), list)
            or 'generate' not in state['command']):
        raise ValueError('This video is not a finished FreeVideo preview')
    if not video.is_file():
        raise ValueError('The preview video is no longer there; generate it again')
    return video, state


def upscale_command(state, output, video, resources):
    """The preview's own generate arguments, refining it into a new output."""
    tail = state['command'][state['command'].index('generate') + 1:]
    replaced = {'--' + field.replace('_', '-') for field in list(state.get('resources') or {}) + list(resources)}
    arguments, index = [], 0
    while index < len(tail):
        flag = tail[index]
        if flag == '--preview':
            index += 1
            continue
        if flag in replaced or flag == '--out':
            if flag == '--out':
                arguments += ['--out', str(output)]
            index += 2
            continue
        arguments.append(flag)
        index += 1
    for field, reserve in resources.items():
        if reserve is not None:
            arguments += ['--' + field.replace('_', '-'), str(reserve)]
    return arguments + ['--refine-from', str(video)]


def without_upscale(graph, node_id, refine_from):
    import copy
    graph = copy.deepcopy(graph)
    inputs = ((graph.get('prompt') or {}).get(str(node_id)) or {}).get('inputs')
    if isinstance(inputs, dict):
        inputs.update(refine_from='', preview=False)
    for node in (graph.get('workflow') or {}).get('nodes') or []:
        values = node.get('widgets_values')
        if str(node.get('id')) == str(node_id) and isinstance(values, list) and values and values[-1] == refine_from:
            # Preview first and Upscale preview are the node's last two widgets.
            values[-1] = ''
            if len(values) > 1 and type(values[-2]) is bool:
                values[-2] = False
    return graph


def argument(arguments, flag, default=None):
    return arguments[arguments.index(flag) + 1] if flag in arguments else default


def engine_environment(root, source, environ=None):
    if sys.platform == 'darwin':
        from .macos_bootstrap import environment
    else:
        from .triton_compat import environment
    return environment(root, isolated_environment(root, source, environ))


def generate(prompt, width, height, seconds, seed, output_directory, *,
             source=None, environ=None, metadata=None, progress=None, interrupted=None,
             release_models=None, export_inputs=None, two_pass=True, encoder_prewarm=None,
             force_regenerate=False, base_steps=8, refine_steps=3, comfy_metadata=None,
             prompt_rewrite_report=None, preview=False, refine_from=None, report_extra=None,
             continue_from=None, frames=None, first_frame_reference=False, voice_reference=False, hide_tail=False,
             step_cache=None, upscale_factor=2):
    from .tiers import STEP_CACHES
    from .two_pass import SCALES
    if upscale_factor not in SCALES:
        raise ValueError('Upscale factor must be one of ' + ', '.join('%g' % value for value in SCALES))
    if step_cache == 'none':
        step_cache = None
    if step_cache not in STEP_CACHES:
        raise ValueError('Step cache must be none, ' + ', '.join(c for c in STEP_CACHES if c))
    if type(first_frame_reference) is not bool:
        raise ValueError('First-frame reference must be a boolean')
    if type(voice_reference) is not bool:
        raise ValueError('Voice reference must be a boolean')
    if type(hide_tail) is not bool:
        raise ValueError('Hide tail must be a boolean')
    if continue_from is not None:
        from .continuation import validate_route
        validate_route(two_pass=two_pass, preview=preview, refine_from=refine_from)
    if type(two_pass) is not bool:
        raise ValueError('Two-pass generation must be a boolean')
    if type(force_regenerate) is not bool:
        raise ValueError('Force regeneration must be a boolean')
    if type(preview) is not bool:
        raise ValueError('Preview must be a boolean')
    from .comfy_continuation import optional_text
    refine_from = optional_text(refine_from)
    refine_from = refine_from if isinstance(refine_from, str) and refine_from.strip() else None
    upscale = None
    if refine_from:
        # An upscale continues the preview's own request: its prompt, seed,
        # canvas, steps and inputs, not whatever the editor shows now.
        upscale = preview_request(output_directory, refine_from)
        _, previous = upscale
        arguments = previous['command'][previous['command'].index('generate') + 1:]
        prompt = (Path(previous['output']).parent / 'prompt.txt').read_text(encoding='utf-8')
        width, height = previous['geometry']['width'], previous['geometry']['height']
        seconds, seed = previous['geometry']['seconds'], previous['seed']
        two_pass, preview = True, False
        base_steps = int(argument(arguments, '--base-steps', 8))
        refine_steps = int(argument(arguments, '--refine-steps', 3))
        # The second pass never uses a step cache; the preview's own command keeps its flag.
        step_cache = argument(arguments, '--step-cache')
    if preview or upscale:
        # A preview stays the quick half-size first pass, and its upscale continues that same pass.
        upscale_factor = 2
    from .preview import validate_flags
    validate_flags(preview, upscale, two_pass, base_steps)
    from .two_pass import validate_steps
    validate_steps(base_steps, refine_steps, two_pass)
    canvas = validate_request(prompt, width, height, seconds, seed, continue_from=continue_from, frames=frames)
    source = Path(source or source_root()).resolve()
    root, machine = installation(source, environ)
    continuation = None

    def check_ending():
        from .continuation_tail import check
        from .two_pass import sampling_canvas
        check(continue_from, sampling_canvas(canvas)[0])

    if step_cache == 'fbcache' and machine.get('device_backend') == 'mps':
        # Only the audio-video EasyCache is ported to Mac: the request samples every step,
        # and its identity is that of the same request without a cache.
        logging.warning('[FreeVideo] The first-block cache is not available on Mac yet; sampling every step.')
        step_cache = None
    if continue_from is not None:
        validate_route(backend=machine.get('device_backend', 'cuda'))
        from .continuation import MISSING, prepare, summary
        from .two_pass import sampling_canvas
        # A continuation takes no media or preview, so its sampled size is known here; the snapshot
        # of the previous segment was written for that canvas, not for the requested size.
        try:
            continuation = prepare(continue_from, sampling_canvas(canvas)[0], directory=root / 'continuations')
        except ValueError as error:
            if str(error) != MISSING:
                raise
            # A video with no stored context (single-pass, an earlier version, a replaced file) is
            # continued from its own ending, which the engine reads and encodes. Only a video whose
            # ending cannot be used stops here, before anything is loaded.
            check_ending()
    # The engine decides the same way from the same record: whether the film's first frame is there to use.
    # An ending encoded from the file has no first frame of the film to give.
    anchored = False
    if continue_from is not None and continuation is not None and first_frame_reference:
        from .continuation import chain_first_frame_picture
        try:
            anchored = chain_first_frame_picture(continuation, continue_from) is not None
        except ValueError:
            pass
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
    if prompt_rewrite_report:
        from .prompt_vlm.receipt import attach
        attach(root, prompt_rewrite_report, run)
    (run / 'prompt.txt').write_text(prompt, encoding='utf-8')
    if metadata is not None:
        save(run / 'workflow.json', metadata)
    command = [machine['python'], '-B', '-m', 'freevideo_engine.managed', '--root', str(root),
               'generate', '--prompt-file', str(run / 'prompt.txt'), '--out', str(output),
               '--width', str(width), '--height', str(height), '--seconds', str(seconds), '--seed', str(seed),
               '--two-pass' if two_pass else '--no-two-pass',
               '--base-steps', str(base_steps), '--refine-steps', str(refine_steps)]
    if step_cache is not None:
        command += ['--step-cache', step_cache]
    if two_pass and upscale_factor != 2:
        command += ['--upscale-factor', '%g' % upscale_factor]
    if frames is not None:
        position = command.index('--seconds')
        command[position:position + 2] = ['--frames', str(frames)]
    if continue_from is not None:
        command += ['--continue-from', str(continue_from)]
        if first_frame_reference:
            command.append('--first-frame-reference')
        if voice_reference:
            command.append('--voice-reference')
        if continuation is not None:
            from .continuation import persist_snapshot
            continuation = persist_snapshot(continuation, run / 'continuation-source.safetensors')
            command += ['--continuation-snapshot', continuation['snapshot'],
                        '--continuation-snapshot-sha256', continuation['snapshot_sha256']]
    if upscale:
        command = command[:command.index('generate') + 1] + upscale_command(upscale[1], output, upscale[0], resources)
    else:
        for field, reserve in resources.items():
            if reserve is not None:
                command += ['--' + field.replace('_', '-'), str(reserve)]
        if preview:
            command.append('--preview')
    if hide_tail:
        command.append('--hide-tail')
    state = {'status': 'starting', 'geometry': canvas, 'seed': seed, 'installation': str(root),
             'command': command, 'output': str(output), 'source': str(source), 'resources': resources}
    if preview:
        state['preview'] = True
    if upscale:
        state['upscaled_preview'] = str(upscale[0])
    from .diagnostic_resources import encoder_prewarm as prewarm_summary
    state['encoder_prewarm'] = prewarm_summary(encoder_prewarm)
    save(run / 'comfy-request.json', state)
    started = time.monotonic()
    try:
        whole_progress = WholeVideoProgress.for_request(machine, canvas, output_directory,
            base_steps=base_steps, refine_steps=refine_steps, two_pass=two_pass,
            preview=preview, upscale=bool(upscale), encoder_prewarm=state['encoder_prewarm'],
            step_cache=None if upscale else step_cache, upscale_factor=upscale_factor)
    except Exception:
        # A progress estimate cannot prevent generation, even without its parameters.
        try:
            whole_progress = WholeVideoProgress()
        except Exception:
            whole_progress = _PlainProgress()
    whole_progress = _SafeProgress(whole_progress)
    from .comfy_progress import REPORTS
    report_id = REPORTS.register(output)
    frame_state = [None]
    def send_progress(message, event=None):
        if progress:
            value = whole_progress.annotate(dict(message, report_id=report_id), event=event)
            if message.get('phase') in ('sample_finalize', 'decode') or message.get('timing_phase') in ('decode', 'encoding'):
                frame_state[0] = None
            if frame_state[0] is not None:
                value = dict(value, frame_preview=frame_state[0])
            progress(value)
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
                if event.get('event') in ('frame_preview', 'frame_preview_clear'):
                    frame_state[0] = preview_update(frame_state[0], event)
                    continue
                message = progress_message(event)
                if message:
                    send_progress(message, event)
                else:
                    whole_progress.observe(event)
        heartbeat = whole_progress.heartbeat()
        if progress and heartbeat:
            progress(heartbeat)
    try:
        send_progress({'label': 'Preparing video'})
        if interrupted:
            interrupted()
        extra = {}
        media_base = run
        if upscale:
            # The preview's retained inputs, exactly as its request used them.
            arguments = command[command.index('generate') + 1:]
            media_base = Path(upscale[1]['output']).parent
            if '--media' in arguments:
                extra['media'] = json.loads(Path(argument(arguments, '--media')).read_text(encoding='utf-8'))
            if '--conditioning' in arguments:
                extra['conditioning'] = argument(arguments, '--conditioning')
        elif export_inputs:
            send_progress({'label': 'Retaining and checking input media', 'timing_phase': 'encoding'})
            extra = export_inputs(run, canvas) or {}
            if any(row.get('depth') is True for row in (extra.get('media') or {}).get('references', [])):
                from .depth_reference import prepare_media
                def release_depth_models():
                    if release_models:
                        release_models()
                    from .encoder_prewarm import IDLE
                    from .resident_process import OWNER, ENV
                    IDLE.stop()
                    OWNER.release('depth_reference')
                    environment.pop(ENV, None)
                extra = dict(extra, media=prepare_media(extra['media'], root=root, machine=machine,
                    environ=environment, progress=send_progress, interrupted=interrupted,
                    release_models=release_depth_models))
            if extra:
                if extra.get('conditioning'):
                    position = command.index('--prompt-file')
                    command[position:position + 2] = ['--conditioning', str(extra['conditioning'])]
                if extra.get('media'):
                    media_path = run / 'media.json'
                    save(media_path, extra['media'])
                    command += ['--media', str(media_path)]
            state['command'] = command
            # The progress area names reference media only when the request has them.
            media = extra.get('media') or {}
            send_progress({'label': 'Retaining and checking input media', 'timing_phase': 'encoding',
                           'references': any(media.get(key) for key in ('first', 'last', 'references'))})
        # A continued part keeps the cast's pictures; the engine then leaves out the film's first frame.
        pictured = continue_from is not None and any(
            row.get('kind') == 'image' for row in (extra.get('media') or {}).get('references', []))
        if pictured:
            anchored = False
        from .media_request import task_for
        from .two_pass import plan, sampling_canvas
        from .generate import hide_tail_geometry
        from .original_audio import prepare_request
        from copy import deepcopy
        original_audio, audio_canvas = prepare_request(deepcopy(extra.get('media') or {}), width, height,
            continuation=continue_from is not None, backend=machine.get('device_backend', 'cuda'))
        if original_audio is not None:
            canvas = audio_canvas
            state['geometry'] = canvas
            extra['original_audio'] = original_audio
            flag = '--frames' if '--frames' in command else '--seconds'
            position = command.index(flag)
            command[position:position + 2] = (['--frames', str(canvas['frames'])] if 'segment' in original_audio else
                ['--seconds', str((original_audio['end_sample'] - original_audio['start_sample']) / original_audio['sample_rate'])])
            # The sound used as is sets the length and is the part's one sound: the engine adds no hidden
            # tail, no voice reference and no first frame of the film beside it (cast pictures stay).
            first_frame_reference = voice_reference = hide_tail = anchored = False
            for flag in ('--first-frame-reference', '--voice-reference', '--hide-tail'):
                if flag in command:
                    command.remove(flag)
        generated_canvas, hidden = hide_tail_geometry(canvas, None if extra.get('conditioning') else prompt,
                                                      hide_tail, preview=bool(preview or upscale))
        hide_tail_frames = hidden['frames'] if hidden and hidden['used'] else 0
        # The engine samples this canvas; results, cache keys and labels keep the requested size.
        sampled, output_size = sampling_canvas(generated_canvas, staged=bool(preview or upscale), media=extra.get('media'),
                                               conditioned=bool(extra.get('conditioning')))
        planned = plan(sampled, two_pass, 'ref2va' if anchored or pictured else task_for(extra.get('media', {})),
                       base_steps=base_steps, refine_steps=refine_steps, staged=bool(preview or upscale),
                       scale=upscale_factor)
        if continue_from is not None:
            from .continuation import ADALN_TASK, ANCHOR_ADALN_TASK, MISSING, validate_snapshot, summary
            validate_route(task='ref2va' if anchored or pictured else task_for(extra.get('media', {})),
                           anchor=anchored or pictured,
                           media=extra.get('media'), original_audio=original_audio is not None)
            if original_audio is not None and continuation is not None:
                # An encoded ending has no stored origin; the engine checks the part against its own.
                from .original_audio_chain import validate_continuation
                validate_continuation(original_audio, continuation)
            if continuation is not None:
                try:
                    continuation = validate_snapshot(continuation, sampled, sampling_plan=planned)
                    state['continuation'] = summary(continuation, hide_tail_frames=hide_tail_frames)
                except ValueError as error:
                    if str(error) != MISSING:
                        raise
                    # Stored for another first-pass size: the engine sets it aside and encodes the ending.
                    check_ending()
                    continuation = None
                    if anchored:
                        # An encoded ending carries no first frame of the film, so nothing anchors this run.
                        anchored = False
                        planned = plan(sampled, two_pass, task_for(extra.get('media', {})),
                                       base_steps=base_steps, refine_steps=refine_steps, staged=bool(preview or upscale),
                                       scale=upscale_factor)
            if continuation is None:
                from .continuation import OVERLAP_FRAMES
                state['continuation'] = dict(overlap_frames=OVERLAP_FRAMES,
                                             new_frames=sampled['frames'] - hide_tail_frames - OVERLAP_FRAMES, encoded=True)
        # A preview is its own result; an upscale is always run from its preview.
        keyed = dict(planned, preview=True) if preview else planned
        if step_cache is not None:
            # A cache changes the arithmetic; without one the identity stays that of earlier versions.
            keyed = dict(keyed, step_cache=step_cache)
        if hide_tail_frames:
            keyed = dict(keyed, hide_tail_frames=hide_tail_frames)
        identify = lambda check: request_key(prompt, seed, canvas, keyed, extra, machine,
                                             resources, source, environment, inspection=check)
        state['result_cache'] = dict(enabled=False, hit=False, forced=force_regenerate)
        reused = None
        if not force_regenerate and not upscale and continue_from is None:
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
        from .sampling_assets import engine_task, prepare as prepare_sampling_assets
        def asset_progress(message):
            if progress:
                progress(dict(message, report_id=report_id))
        preparation_started = time.monotonic()
        try:
            # Reference audio selects its own tables; match the encoder's choice.
            asset_task = ((ANCHOR_ADALN_TASK if anchored or pictured else ADALN_TASK)
                          if continue_from is not None and original_audio is None
                          else engine_task(extra.get('media', {}), media_base))
            # Tables follow the step count; the cache is carried for exact level recognition.
            preparation = prepare_sampling_assets(root, machine, dict(planned, step_cache=step_cache) if step_cache else planned,
                                                  asset_task,
                progress=asset_progress, interrupted=interrupted, environ=environment)
        except BaseException:
            elapsed = time.monotonic() - preparation_started
            state['sampling_cache_install'] = dict(status='failed', seconds=elapsed)
            started += elapsed
            raise
        state['sampling_cache_install'] = preparation
        if preparation['seconds']:
            started += preparation['seconds']
            send_progress({'reset': True, 'label': 'Preparing video'})
        whole_progress.configure(plan=planned, media=extra.get('media', {}),
                                 conditioning=bool(extra.get('conditioning')), sampled=sampled)
        send_progress({'label': 'Preparing %.3f s video + audio · %d × %d' %
                       (canvas['seconds'], width, height)})
        if release_models:
            release_models()
        state['encoder_prewarm'] = prewarm_summary(encoder_prewarm)
        if machine.get('device_backend') != 'mps' and (source/'freevideo_engine'/'resident_worker.py').is_file():
            from .resident_process import OWNER, ENV
            # Whether the models were kept for this request or released, and why.
            state['resident_since_last'] = OWNER.take_decisions()
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
                from .comfy_launcher_api import repair_hint, request_repair
                from .failure_details import generation_failure, runtime_explanation
                hint = repair_hint()
                changed = runtime_explanation(run, hint)
                if changed:
                    # The launcher shows its Repair card when it next reads the page check.
                    request_repair(changed)
                raise RuntimeError(generation_failure(run, process.returncode, repair=hint))
        report = json.loads(output.with_suffix('.request.json').read_text(encoding='utf-8'))
        engine = json.loads(output.with_suffix('.engine.json').read_text(encoding='utf-8'))
        if hide_tail_frames and report.get('hide_tail') == dict(requested=True, used=False, reason='too_long'):
            # Admission before encoding can decline the extra frames after the bridge's
            # estimate. Validate the ordinary plan and don't cache it as a hidden-tail result.
            # After encoding the conditioning is tied to the longer canvas, so a later refusal
            # fails like any other request.
            sampled, output_size = sampling_canvas(canvas, staged=bool(preview or upscale),
                media=extra.get('media'), conditioned=bool(extra.get('conditioning')))
            hidden, hide_tail_frames = report['hide_tail'], 0
            cache_key = None
        from .two_pass import steps, plan
        completed_steps = steps(report.get('sampling_plan'))
        expected_plan = plan(sampled, two_pass, report.get('profile', {}).get('engine', {}).get('task', 't2va'),
                             base_steps=base_steps, refine_steps=refine_steps, staged=bool(preview or upscale),
                             scale=upscale_factor)
        shown = sampled
        if preview:
            # A preview decodes its first pass at the planned smaller canvas.
            completed_steps = expected_plan['base_steps']
            shown = dict(canvas, width=expected_plan['first']['width'], height=expected_plan['first']['height'])
        if (report.get('success') is not True or engine.get('success') is not True
                or not output.is_file() or not output.stat().st_size
                or len(engine.get('step_seconds', [])) != completed_steps
                or engine.get('sampling_plan') != report.get('sampling_plan')
                or report.get('sampling_plan') != expected_plan
                or bool(report.get('preview')) != preview or bool(report.get('upscaled_preview')) != bool(upscale)
                or (hide_tail and report.get('hide_tail') != hidden)
                or any(engine.get('geometry', {}).get(k) != shown[k] for k in ('width', 'height', 'frames'))
                or (report.get('output_size') or None) != output_size):
            from .comfy_launcher_api import repair_hint
            from .failure_details import generation_failure
            raise RuntimeError('FreeVideo did not complete the requested video.\n' + generation_failure(run, repair=repair_hint()))
        if report_extra:
            # Only the newly generated, verified result is annotated. Cache hits
            # return earlier and retain their historical report byte for byte.
            report.update(report_extra)
            save(output.with_suffix('.request.json'), report)
        if continue_from is not None:
            receipt = engine.get('continuation') or {}
            if continuation is None and receipt:
                # The ending was encoded by the engine, which is where the real counters come from.
                state['continuation'] = dict(receipt)
            state['continuation']['audio_lead_in_samples'] = receipt.get('audio_lead_in_samples', 0)
        if comfy_metadata:
            # Dropping the video on the canvas restores this graph, as with ComfyUI's own video nodes.
            # Written before the result cache records the file's size and hash.
            from .comfy_metadata import embed_comfy_metadata
            timing = {}
            if original_audio is not None:
                from math import lcm
                timing['exact_timescale'] = lcm(original_audio['sample_rate'], 24)
            state['workflow_in_video'] = embed_comfy_metadata(output, **comfy_metadata, **timing)
        # The MP4 is saved and checked; its uncompressed frames and audio are no longer read.
        from .comfy_library import release_decoded, release_later
        state['decoded_release'] = release_decoded(run)
        if state['decoded_release']['left']:
            try:
                release_later(output_directory)
            except OSError:
                pass  # The video is complete; a later release only frees space.
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
        send_progress({'label': 'Preview saved' if preview else 'Video + audio saved', 'phase': 'complete',
                       'done': completed_steps, 'total': completed_steps})
        try:
            # The validated request report is on disk. This advisory refresh
            # must never change a successful generation's result.
            from .comfy_capabilities import changed
            changed()
        except Exception:
            pass
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
                    OWNER.release('cancelled')
        raise
    finally:
        from .resident_process import OWNER
        OWNER.active = False
        state['bridge_seconds'] = time.monotonic() - started
        state['bridge_wall_seconds'] = state['bridge_seconds'] + state.get('sampling_cache_install', {}).get('seconds', 0.)
        save(run / 'comfy-request.json', state)
        from .support_report import write as write_debug
        write_debug(output, bridge=state)
