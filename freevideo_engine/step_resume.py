"""Resume a native first pass from its last completed sampling step.

A sampling pass that Metal or the unified-memory allocator stops is attempted
again in a fresh process. Without a saved state that attempt repeats every
completed step, which on a slow Mac is most of the lost time. This module saves
the generated video and audio rows after every step of the plain first pass and
lets the retry continue from them.

The vendored sampler is not changed. It is bound to the engine's scheduler, so
a resumed scheduler iterates only the remaining steps on the original sigma grid
(the step index comes from the begin index), the scheduler's observer keeps the
rows each update returns, and a one-shot forward hook writes the saved rows into
the sampler's input tensors before the first resumed forward. That first update
then verifies that the saved rows are what the sampler stepped from; if the
sampler no longer shares those tensors the pass stops with `ResumeRejected` and
the controller starts the pass over.

Only the plain text-to-video pass without a step cache or continuation is
resumable. Every other pass behaves as before.
"""
import os
from pathlib import Path

VERSION = 1
VIDEO_SHIFT, AUDIO_SHIFT = 12., 3.        # the vendored sampler's defaults


class ResumeRejected(RuntimeError):
    """The saved step could not be applied; the pass must start from step one."""


class StepState:
    """Saves the rows after each step, and applies a matching saved step once."""

    def __init__(self, path, identity, steps):
        self.path, self.identity, self.steps = Path(path), identity, steps
        self.saved = self.resume = None
        self.start = 0
        self.previous_seconds = []
        self.finished = 0
        self._rows = {}
        self._hook = None
        self._verified = set()
        self.failed = None

    def load(self):
        """The saved state when it belongs to this pass, else None."""
        import torch
        try:
            saved = torch.load(self.path, map_location='cpu', weights_only=True)
        except Exception:
            # A missing, truncated or foreign file is just no saved step.
            return None
        if not isinstance(saved, dict):
            return None
        done = saved.get('completed')
        if (saved.get('version') != VERSION or saved.get('identity') != self.identity
                or saved.get('steps') != self.steps or type(done) is not int
                or not 0 < done < self.steps or len(saved.get('step_seconds', ())) != done
                or not all(isinstance(saved.get(key), torch.Tensor) and bool(saved[key].isfinite().all())
                           for key in ('video', 'audio'))):
            return None
        self.resume, self.start = saved, done
        self.previous_seconds = [float(seconds) for seconds in saved['step_seconds']]
        self.finished = done
        return saved

    def scheduler(self, shift=12., **options):
        """The engine scheduler for the sampler: resumed, and observed."""
        from .step_grid import scheduler
        return scheduler(shift, start=self.start, observer=self._observe, **options)

    def _observe(self, scheduler, sample, updated):
        kind = {VIDEO_SHIFT: 'video', AUDIO_SHIFT: 'audio'}.get(scheduler.shift)
        if kind is None:
            self.failed = 'unknown schedule'
            return
        if self.resume is not None and kind not in self._verified:
            import torch
            # `sample` is the sampler's own tensor, overwritten right after this
            # update; compare it now, before the sampler stores the result.
            if not torch.equal(sample.detach().cpu(), self.resume[kind]):
                raise ResumeRejected('The saved ' + kind + ' rows were not the sampler input')
            self._verified.add(kind)
        self._rows[kind] = updated

    def attach(self, model, device):
        """Write the saved rows into the first forward's inputs; no-op without a saved step."""
        if self.resume is None:
            return
        def inject(module, args, kwargs):
            self._hook.remove()
            for name, key in (('hidden_states', 'video'), ('audio_hidden_states', 'audio')):
                target, rows = kwargs[name][0], self.resume[key]
                if tuple(target.shape) != tuple(rows.shape):
                    raise ResumeRejected('The saved rows do not fit the sampler input')
                target.copy_(rows.to(device=target.device, dtype=target.dtype))
        self._hook = model.register_forward_pre_hook(inject, with_kwargs=True)

    def detach(self):
        if self._hook is not None:
            self._hook.remove()
            self._hook = None

    def completed(self, seconds):
        """A step finished: keep its rows so a retry can continue after it."""
        import torch
        self.finished += 1
        self.previous_seconds.append(float(seconds))
        if self.failed or self.finished >= self.steps or set(self._rows) != {'video', 'audio'}:
            return
        temporary = self.path.with_name(self.path.name + '.partial')
        try:
            torch.save(dict(version=VERSION, identity=self.identity, steps=self.steps, completed=self.finished,
                            step_seconds=list(self.previous_seconds),
                            video=self._rows['video'].detach().cpu(), audio=self._rows['audio'].detach().cpu()),
                       temporary)
            os.replace(temporary, self.path)
        except Exception as error:
            # Saving is an optimization: without it a retry starts over. A failed
            # write is an OSError from the rename but a RuntimeError from torch.save.
            self.failed = str(error) or type(error).__name__
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        self._rows = {}

    def clear(self):
        for path in (self.path, self.path.with_name(self.path.name + '.partial')):
            path.unlink(missing_ok=True)


def step_path(artifacts):
    return Path(artifacts) / 'first-pass-step.pt'


def discard(artifacts):
    """Remove a pass's saved step; a new pass must never meet an older one's."""
    path = step_path(artifacts)
    for stale in (path, path.with_name(path.name + '.partial')):
        stale.unlink(missing_ok=True)
