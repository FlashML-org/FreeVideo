"""Where an image request stands as one percentage, and how long it still takes.

The share of each stage comes from how long it really took on this machine for a request of this
size (the last runs are kept in a small file); before the first run, from rough per-size priors that
are corrected by the speed of the stages already finished. The remaining time is that prediction, never
the current percentage extrapolated. Between worker reports the percentage keeps moving at the pace
of the last step, but never past the next step, so a long load or a tiled decode does not stand still.
"""
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time

ORDER = ('load', 'text', 'encode', 'denoise', 'decode')
# Per request, seconds for 1 megapixel (the part that grows with the size) and fixed seconds.
PRIOR = dict(load=(0., 12.), text=(0., 40.), encode=(2., 1.), denoise=(10., 3.), decode=(4., 1.))
KEEP_RUNS, KEEP_KEYS = 6, 48
MIN_RATE = .06  # points per second kept up while a stage reports nothing
MAX_RATE = 1.6  # percentage points per second the shown number may rise
CEILING = .99   # only the finished state shows 100


def key_for(model, width, height, images):
    return '%s|%.1f|%s' % (model, width * height / 1e6, 'edit' if images else 'new')


def _megapixels(key):
    try:
        return float(key.split('|')[1])
    except (IndexError, ValueError):
        return 1.


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value < 1e6


class History:
    """Seconds per stage of the finished runs, by model, size and kind of request."""

    def __init__(self, file):
        self.file = Path(file) if file else None
        self.lock = threading.Lock()

    def _load(self):
        try:
            data = json.loads(self.file.read_text('utf-8'))
            runs = data['runs']
            return {key: [r for r in rows if isinstance(r, dict) and all(_number(r.get(s, 0)) for s in ORDER)][-KEEP_RUNS:]
                    for key, rows in runs.items() if isinstance(rows, list)}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}

    def record(self, key, seconds):
        """Keep one finished run; only runs that went through every stage of the key's kind count."""
        row = {stage: float(seconds[stage]) for stage in ORDER if _number(seconds.get(stage))}
        if self.file is None or 'denoise' not in row or 'decode' not in row:
            return
        with self.lock:
            runs = self._load()
            runs.setdefault(key, []).append(row)
            runs[key] = runs[key][-KEEP_RUNS:]
            for old in list(runs)[:-KEEP_KEYS]:
                del runs[old]
            try:
                self.file.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temp = tempfile.mkstemp(dir=self.file.parent, prefix='.image-stage-times')
                with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                    json.dump(dict(version=1, runs=runs), handle, allow_nan=False)
                os.replace(temp, self.file)
            except (OSError, ValueError):
                pass

    def predict(self, key, with_edit):
        """(seconds per stage, kind): 'history' from this size, 'scaled' from another size, 'prior'."""
        with self.lock:
            runs = self._load() if self.file else {}
        rows = runs.get(key)
        kind = 'history'
        if not rows:
            same = [(abs(_megapixels(k) - _megapixels(key)), k) for k in runs
                    if k.split('|')[0] == key.split('|')[0] and k.split('|')[2:] == key.split('|')[2:] and runs[k]]
            if same:
                rows, kind = runs[min(same)[1]], 'scaled'
                scale = _megapixels(key) / max(.05, _megapixels(min(same)[1]))
        if not rows:
            return self.prior(_megapixels(key), with_edit), 'prior'
        out = {}
        for stage in ORDER:
            values = sorted(r[stage] for r in rows if stage in r)
            if values:
                out[stage] = values[len(values) // 2]
                if kind == 'scaled' and stage in ('encode', 'denoise', 'decode'):
                    out[stage] *= scale
        if not with_edit:
            out.pop('encode', None)
        for stage, value in self.prior(_megapixels(key), with_edit).items():
            out.setdefault(stage, value)
        return out, kind

    @staticmethod
    def prior(megapixels, with_edit):
        return {stage: per * megapixels + fixed for stage, (per, fixed) in PRIOR.items()
                if stage != 'encode' or with_edit}


class Forecast:
    """One request's stage list, fed with the worker's rows, read as often as the page polls."""

    def __init__(self, predicted, kind, clock=time.monotonic):
        self.clock = clock
        self.predicted, self.kind = dict(predicted), kind
        self.stages = [s for s in ORDER if s in self.predicted]
        self.started = clock()
        self.stage, self.entered = None, self.started
        self.reported, self.reported_at = 0., self.started
        self.unit, self.step_seconds = .1, None
        self.spent = {}   # finished stage -> seconds it really took
        self.shown, self.shown_at = 0., self.started
        self.left, self.left_at = None, self.started

    def update(self, stage, fraction):
        now = self.clock()
        if stage not in self.stages:
            return
        if stage != self.stage:
            if self.stage is not None:
                self.spent[self.stage] = now - self.entered
            self.stage, self.entered, self.reported, self.reported_at = stage, now, 0., now
            self.unit, self.step_seconds = .1, None
            # A stage that was skipped over counts as done in no time.
            for earlier in self.stages[:self.stages.index(stage)]:
                self.spent.setdefault(earlier, 0.)
        if fraction > self.reported:
            gap = now - self.reported_at
            if self.reported > 0 or fraction < 1:
                self.unit = max(.01, fraction - self.reported) if self.reported > 0 or fraction < .5 else self.unit
                self.step_seconds = gap if gap > 0 else self.step_seconds
            self.reported, self.reported_at = fraction, now

    def _ratio(self):
        """How much slower or faster than predicted the finished stages ran (damped, 0.5 .. 3)."""
        spent = dict(self.spent)
        running = self.stage in self.predicted and self.clock() - self.entered
        if running and running > 1.2 * self.predicted[self.stage]:
            spent[self.stage] = running   # already over its prediction: at least this slow
        done = [s for s in spent if self.predicted.get(s, 0) > 1.]
        want = sum(self.predicted[s] for s in done)
        if not done or want <= 0:
            return 1.
        ratio = sum(spent[s] for s in done) / want
        weight = .5 if self.kind == 'prior' else .3
        return min(3., max(.5, 1 + (ratio - 1) * weight))

    def _inside(self, now):
        """Fraction of the current stage, the reported one plus the pace of the last step up to the next step."""
        if self.stage is None:
            return 0.
        fraction = self.reported
        if fraction >= 1:
            return 1.
        over = now - self.entered - self.predicted.get(self.stage, 10.)
        if over > 0 and not self.step_seconds:
            # Past its prediction without any step report: keep creeping towards the end, never stand still.
            return .8 + .17 * (1 - math.exp(-over / max(10., self.predicted.get(self.stage, 10.))))
        pace = self.step_seconds or max(1., self.predicted.get(self.stage, 10.) * self.unit)
        since = now - self.reported_at
        return min(fraction + self.unit * .95, fraction + self.unit * since / max(.5, pace)) if self.reported > 0 or self.step_seconds else \
            min(.8, since / max(1., self.predicted.get(self.stage, 10.) * 1.3))

    def view(self):
        now = self.clock()
        ratio = self._ratio()
        index = self.stages.index(self.stage) if self.stage in self.stages else -1
        # Finished stages count with the time they really took, the others with the corrected prediction.
        before = total = sum(self.spent.get(s, 0.) for s in self.stages[:max(index, 0)])
        current = self.predicted.get(self.stage, 0.) if index >= 0 else 0.
        after = sum(self.predicted[s] for s in self.stages[index + 1:]) * ratio
        inside = self._inside(now)
        current_left = max(0., current * ratio * (1 - inside)) if index >= 0 else 0.
        wanted = total + current * ratio + after
        done = before + current * ratio * inside
        target = min(CEILING, done / wanted) if wanted > 0 else 0.
        # Monotone, and never faster than the number can be followed.
        dt = max(0., now - self.shown_at)
        if now - self.reported_at > 5. and self.stage is not None:
            target = max(target, min(CEILING, self.shown + MIN_RATE / 100 * dt))  # still running: never stand still
        self.shown = max(self.shown, min(target, self.shown + MAX_RATE / 100 * dt))
        self.shown_at = now
        left = current_left + after
        if self.left is None:
            self.left = left
        else:
            # Counts down in real time and follows the new prediction slowly: no jump up and down.
            counted = max(0., self.left - (now - self.left_at))
            dt = now - self.left_at
            step = (left - counted) * min(1., dt / 20.)
            # A longer prediction raises the time left gradually, never by more than about a tenth per 30 s.
            self.left = counted + min(step, max(.1 * counted, 10.) * dt / 30.)
        self.left_at = now
        return dict(overall=round(self.shown, 4), remaining_seconds=round(self.left, 1),
                    typical_seconds=round(sum(self.predicted.values()), 1), basis=self.kind)
