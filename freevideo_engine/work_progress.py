"""Real completed work counts for long silent spans; reporting only, no tensor access."""
import json
import time

UNITS = ('bytes', 'layers', 'files', 'frames', 'chunks')
GROUPS = ('encoding', 'load', 'decode')


class WorkProgress:
    """Print `work_progress` rows: the first, the last, and at most one per `interval` seconds."""

    def __init__(self, group, stage, total, unit, interval=5.):
        if group not in GROUPS or unit not in UNITS:
            raise ValueError('Unknown work progress group or unit')
        if type(total) is not int or total < 1:
            raise ValueError('Work total must be a positive integer')
        self.group, self.stage, self.total, self.unit, self.interval = group, stage, total, unit, interval
        self.started = time.perf_counter()
        self.shown_at = None
        self.done = 0

    def update(self, done, **fields):
        done = max(0, min(int(done), self.total))
        self.done = max(self.done, done)
        now = time.perf_counter()
        if self.shown_at is not None and self.done < self.total and now - self.shown_at < self.interval:
            return
        self.shown_at = now
        print(json.dumps(dict(fields, event='work_progress', group=self.group, stage=self.stage,
                              done=self.done, total=self.total, unit=self.unit,
                              elapsed_seconds=now - self.started)), flush=True)
