"""Retain overall stages and concurrent transfer progress independently of logs."""
import math
import time

from .launcher_copy import size_text


def number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


class ProgressState:
    def __init__(self):
        self.phase = {}
        self.tasks = {}
        self.last = {}
        self.model_groups = []

    def update(self, event, now=None):
        now = time.monotonic() if now is None else now
        event = dict(event)
        if event.get('kind') == 'models':
            self.model_groups = event.get('groups', [])
            return
        if event.get('kind') == 'phase':
            self.phase = event
            return
        key = str(event.get('key', 'current'))
        if event.get('kind') == 'task':
            self.tasks.pop(key, None)
        previous = self.tasks.get(key, {})
        state = event.get('state', 'running')
        self.tasks[key] = dict(previous, **event, observed_at=now)
        self.tasks[key]['state'] = state
        self.last = self.tasks[key]
        # Completed events need not grow with the number of downloaded files.
        for name in list(self.tasks):
            if len(self.tasks) <= 32:
                break
            if self.tasks[name].get('state') != 'running':
                self.tasks.pop(name)

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        active = [self._view(row, now) for row in self.tasks.values() if row.get('state') == 'running']
        # Keep a download visible while a parallel compiler reports a heartbeat.
        active.sort(key=lambda row: (number(row.get('total')) and row['total'] > 0,
                                     row.get('unit') == 'bytes'), reverse=True)
        current = active[0] if active else self._view(self.last, now)
        return dict(phase_progress=dict(self.phase), active_tasks=active, progress=current, model_groups=self.model_groups)

    @staticmethod
    def _view(row, now):
        value = dict(row)
        age = max(0., now - value.pop('observed_at', now))
        total, done = value.get('total'), value.get('done')
        valid = number(total) and total > 0 and number(done) and done <= total
        value['fraction'] = done / total if valid else None
        remaining = value.get('remaining_seconds')
        if not valid or age > 10 or not number(remaining):
            value['remaining_seconds'] = None
        value['age_seconds'] = age
        return value


def transfer_parts(done, total, rate, decimal_sizes=False):
    """"5.2 / 14.6 GiB" and "31.0 MiB/s" when known; decimal units on macOS, as Finder shows."""
    base = 1000 if decimal_sizes else 1024
    units = ('KB', 'MB', 'GB', 'TB') if decimal_sizes else ('KiB', 'MiB', 'GiB', 'TiB')
    parts = []
    if number(total) and total > 0:
        done = min(done, total) if number(done) else 0
        power = 1
        while power < 4 and total >= base ** (power + 1):
            power += 1
        digits = 1 if power >= 3 else 0
        parts.append('%.*f / %.*f %s' % (digits, done / base ** power, digits, total / base ** power, units[power - 1]))
    if number(rate) and rate >= base:
        power = 2 if rate >= base ** 2 else 1
        parts.append('%.*f %s/s' % (1 if power == 2 else 0, rate / base ** power, units[power - 1]))
    return parts


def progress_text(event, zh=False, *, decimal_sizes=False):
    """One activity line for the launcher; step counts and ETAs remain in worker events/logs."""
    t = lambda en, cn: cn if zh else en
    activity, name = event.get('activity'), event.get('name') or ''
    if activity == 'resume':
        return t('The connection dropped; resuming the download and keeping what was already downloaded',
                 '连接中断，正在继续下载，已下载的部分会保留')
    if activity == 'check':
        return t('Checking ', '正在校验 ') + name
    if activity == 'copy':
        return t('Copying ', '正在复制 ') + name
    if activity == 'note':
        # A fixed description of the step (already translated with the event), and its speed if any.
        return ' · '.join([name] + transfer_parts(0, 0, event.get('rate'), decimal_sizes))
    if activity == 'download':
        total = event.get('total') if event.get('unit') == 'bytes' else 0
        return ' · '.join([t('Downloading ', '正在下载 ') + name] +
                          transfer_parts(event.get('done'), total or 0, event.get('rate'), decimal_sizes))
    return ''
