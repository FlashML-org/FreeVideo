"""Sampling telemetry without tensor reads, CUDA synchronization or Torch imports.

Layers report where the host is dispatching work. Only the sampler's existing
synchronized step timer can mark a step complete; layer events are not GPU
completion measurements.
"""
from contextlib import contextmanager
import json
import time


def emit_event(event):
    print(json.dumps(event), flush=True)


def _bounded(value, low=0., high=1.):
    """Return a finite progress fraction without ever claiming completion."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return low
    if value != value:  # NaN, without importing math in the hot path.
        return low
    return min(high, max(low, value))


class SamplingProgress:
    def __init__(self, steps, blocks, emit=emit_event, clock=time.perf_counter, *, offset=0, total=None):
        self.steps, self.blocks = steps, blocks
        self.offset, self.total = offset, steps if total is None else total
        if not 0 <= offset < self.total or offset + steps > self.total:
            raise ValueError('Invalid sampling progress range')
        self.emit, self.clock = emit, clock
        self.started = clock()
        self.step_started = self.started
        self.completed = []
        self.last_layer_event = float('-inf')

    def _step_estimate(self):
        # The first step includes input preparation and kernel compilation. A
        # single completed warm step is enough for visual interpolation inside
        # the next step; remaining-time forecasts still require two warm
        # measurements below to avoid presenting a noisy ETA.
        warm = self.completed[1:]
        return sum(warm) / len(warm) if warm else None

    def _visual_step_estimate(self):
        """Return the earliest useful step estimate for UI interpolation.

        The first NFE includes setup and compilation, so it is excluded from
        the authoritative ETA.  It is still a useful temporary ruler for the
        progress animation until the first warm NFE is available; later real
        measurements replace it automatically.
        """
        estimate = self._step_estimate()
        if estimate is not None:
            return estimate
        return self.completed[-1] if self.completed else None

    def event(self, name, **fields):
        now = self.clock()
        if 'step' in fields:
            fields['step'] += self.offset
        self.emit(dict(event=name, total=self.total, completed_steps=self.offset + len(self.completed),
                       uniform_remaining_steps=self.offset + self.steps == self.total,
                       elapsed_seconds=now-self.started,
                       step_elapsed_seconds=now-self.step_started, **fields))

    def start(self):
        self.event('sampling_start', stage='inputs', step=1)

    def layer(self, index):
        if len(self.completed) >= self.steps:
            return
        now = self.clock()
        if index == 1 or now-self.last_layer_event >= 1.:
            self.last_layer_event = now
            estimate = self._visual_step_estimate()
            # A layer event is emitted before that layer runs. Once the next
            # layer is entered, the elapsed time covers the preceding layers;
            # use that fraction as an early, continuously corrected ruler.
            if estimate is None and index > 1 and self.blocks > 0:
                elapsed = now - self.step_started
                fraction = (index - 1) / self.blocks
                if elapsed > 0 and fraction > 0:
                    estimate = elapsed / fraction
            self.event('sampling_progress', stage='layers', step=len(self.completed)+1,
                       block=index, blocks=self.blocks,
                       estimated_step_seconds=estimate)

    def audio(self, part, index, count):
        """Prism's audio pass after a step's video passes: ``part`` 'teacher' (base-weight
        layers) or 'substeps' (audio-only sub-steps), ``index`` of ``count``."""
        if len(self.completed) >= self.steps:
            return
        now = self.clock()
        if index == 1 or now - self.last_layer_event >= 1.:
            self.last_layer_event = now
            self.event('sampling_progress', stage='audio', step=len(self.completed)+1, part=part, index=index,
                       count=count, estimated_step_seconds=self._visual_step_estimate())

    def complete(self, seconds):
        self.completed.append(seconds)
        # Exclude the first step's initialization/compilation. Use at least two
        # subsequent measurements, and label this as sampling-only in the UI.
        warm = self.completed[1:]
        remaining = ((self.steps-len(self.completed))*sum(warm)/len(warm)
                     if len(warm) >= 2 and self.offset + self.steps == self.total else None)
        self.event('step', step=len(self.completed), seconds=seconds,
                   remaining_seconds=remaining,
                   estimated_step_seconds=self._visual_step_estimate(),
                   # The next step starts after this event is emitted.  The
                   # browser uses this zero to interpolate smoothly from the
                   # completed NFE instead of carrying the previous step's
                   # elapsed time into the next one.
                   next_step_elapsed_seconds=0.)
        self.step_started = self.clock()

    @contextmanager
    def layers(self, blocks):
        handles = []
        try:
            for index, block in enumerate(blocks, 1):
                def entered(module, inputs, index=index):
                    self.layer(index)
                handles.append(block.register_forward_pre_hook(entered))
            yield
        finally:
            for handle in handles:
                handle.remove()


def progress_message(event):
    """One sampling event contract for ComfyUI and the terminal log reader."""
    name = event.get('event')
    if name == 'sample_finalize':
        labels = {'latent_validation': 'Checking completed sampling',
                  'latent_save': 'Saving completed sampling',
                  'offload_release': 'Releasing sampling buffers',
                  'transformer_release': 'Preparing video decoding'}
        phase = event.get('phase')
        if phase in labels:
            return dict(label=labels[phase], phase='sample_finalize', stage=phase,
                        done=0, total=0)
        return None
    if name not in ('sampling_start', 'sampling_progress', 'step', 'loaded'):
        return None
    total = event.get('total', 8)
    done = event.get('step', 0) if name == 'step' else event.get('completed_steps', 0)
    if type(total) is not int or total < 1 or type(done) is not int or not 0 <= done <= total:
        return None
    result = dict(label='Sampling %d / %d' % (done, total), phase='sampling',
                  done=done, total=total, unit='steps', stage=event.get('stage', 'starting'),
                  uniform_remaining_steps=event.get('uniform_remaining_steps', True))
    # `done` remains the count of steps whose synchronized work completed. A
    # layer event is only dispatch progress, so expose it separately as an
    # explicitly estimated fraction. The cap leaves a visible gap before the
    # real step event arrives and prevents a UI from claiming completion early.
    display_fraction = (done / total) if total else 0.
    estimated = False
    if name == 'sampling_start':
        result['detail'] = 'Preparing sampling inputs'
        result['reset'] = done == 0
    elif name == 'sampling_progress':
        block, blocks = event.get('block'), event.get('blocks')
        if type(block) is int and type(blocks) is int and 1 <= block <= blocks:
            local = _bounded((block - 1) / max(1, blocks), high=.88)
            display_fraction = _bounded((done + local) / total, high=1.)
            estimated = True
            result.update(block=block, blocks=blocks,
                          detail='Step %d · processing layer %d / %d' % (done+1, block, blocks))
        part, index, count = event.get('part'), event.get('index'), event.get('count')
        if event.get('stage') == 'audio' and type(index) is int and type(count) is int and 1 <= index <= count:
            # Prism's audio pass ends each step: the fraction near the step's end.
            display_fraction = _bounded((done + .88 + .1 * index / count) / total, high=1.)
            estimated = True
            result.update(audio_part=part, audio_index=index, audio_count=count,
                          detail=('Step %d · audio teacher layer %d / %d' if part == 'teacher' else
                                  'Step %d · audio sub-step %d / %d') % (done + 1, index, count))
    elif name == 'step':
        result.update(stage='complete' if done == total else 'between_steps',
                      detail='Sampling complete · preparing output' if done == total else
                             'Step %d complete' % done)
        result['last_step_seconds'] = event.get('seconds')
    for key in ('elapsed_seconds', 'step_elapsed_seconds', 'remaining_seconds'):
        if key in event:
            result[key] = event[key]
    if name == 'step':
        result['step_elapsed_seconds'] = event.get('next_step_elapsed_seconds', 0.)
    if event.get('estimated_step_seconds') is not None:
        result['estimated_step_seconds'] = event['estimated_step_seconds']
    result['display_fraction'] = display_fraction
    result['estimated'] = estimated
    result['display_percent'] = round(100 * display_fraction, 1)
    if name in ('sampling_progress', 'step'):
        prefix = '~' if estimated else ''
        result['label'] = 'Sampling %d / %d · %s%.0f%%' % (done, total, prefix, display_fraction * 100)
    # This is a capability/explanation, not a claim that a compilation is active
    # or that every new shape can reuse an existing compiled artifact.
    if done == 0:
        result['kernel_cache_note'] = True
    return result
