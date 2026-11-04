"""The H3 step grid, computed the same way on every CPU.

The scheduler builds linspace(1, 0, steps + 1) in float32. PyTorch computes the
first half of a linspace as start + step * i, and whether that multiply and add
are fused depends on the CPU kernel it dispatches to and on the compiler that
built it. The two results differ by one float32 unit at some points: at 20
steps point 9 is 0.55000001 fused and 0.54999995 unfused, and the audio
timestep of step 10 is then 0.21428561 fused and 0.21428573 unfused. Published
sampling tables are selected by their exact timesteps, so on a machine that
does not fuse, the tables for 9, 13, 15, 17, 18, 20, 21, 23, 24 and 26 to 30
steps never matched: the request downloaded the 26 GB of original projections
and recomputed them.

Every schedule the engine builds therefore takes its grid from here. The
values are the fused ones, which the published tables were made with.
"""
from functools import lru_cache


def uniform(steps):
    """linspace(1, 0, steps + 1) in float32, each point rounded once from its exact value."""
    import torch
    if type(steps) is not int or steps < 1:
        raise ValueError('A step grid needs at least one step')
    points = steps + 1
    step = torch.tensor(-1. / steps, dtype=torch.float32).item()
    halfway = points // 2
    # A float32 step times a small integer, and one plus that, are exact in a
    # Python float (below 2**28 steps), so the conversion to float32 is the only
    # rounding. That is what a fused multiply-add returns.
    return torch.tensor([1. + step * index if index < halfway else 0. - step * (points - 1 - index)
                         for index in range(points)], dtype=torch.float32)


@lru_cache(maxsize=1)
def _scheduler_class():
    from diffusers import MiniMaxH3Scheduler

    class Scheduler(MiniMaxH3Scheduler):
        # A request resuming after step `start` iterates only the steps still to
        # run, on the unchanged sigma grid; `observer` sees every update.
        start = 0
        observer = None

        def set_timesteps(self, num_inference_steps=None, device=None, sigmas=None):
            if sigmas is None and type(num_inference_steps) is int and num_inference_steps >= 1:
                base = uniform(num_inference_steps)
                # The scheduler's own shift, applied to a grid it did not build.
                sigmas = self._shift * base / (1 + (self._shift - 1) * base)
            super().set_timesteps(num_inference_steps, device=device, sigmas=sigmas)
            if self.start:
                if not 0 < self.start < len(self.timesteps):
                    raise ValueError('A resumed schedule must start inside its steps')
                # The step index then comes from this begin index; the sigmas keep
                # their original positions.
                self.timesteps = self.timesteps[self.start:]
                self.set_begin_index(self.start)

        def step(self, model_output, timestep, sample, return_dict=True):
            # Read-only: the frame preview looks at the prediction, then the
            # upstream update runs exactly as before.
            from .frame_preview import observe
            observe(model_output, timestep, sample)
            result = super().step(model_output, timestep, sample, return_dict=return_dict)
            if self.observer is not None:
                self.observer(self, sample, result[0] if isinstance(result, tuple) else result.prev_sample)
            return result

    return Scheduler


def scheduler(shift=12., *, start=0, observer=None):
    """A MiniMaxH3Scheduler whose step grid is `uniform`; explicit sigmas pass through.

    `start` resumes a schedule after that many completed steps; `observer(scheduler,
    sample, updated)` is called after each update."""
    made = _scheduler_class()(shift=shift)
    if start:
        made.start = start
    if observer is not None:
        made.observer = observer
    return made
