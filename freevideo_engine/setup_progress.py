"""Retain overall stages and concurrent transfer progress independently of logs."""
import math
import time

from .launcher_copy import size_text
from .model_status import NAMES


def number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


MODEL_ACTIVITIES = ('download', 'verify', 'assemble', 'copy', 'copy_bytes', 'check', 'resume')


VERB_ACTIVITIES = ('verify', 'assemble', 'copy_bytes', 'check', 'copy', 'download')
COMPONENT_NAMES = ('Video ', 'Text ', 'Sampling ', 'Image ', 'Model', 'Runtime components')


RUNTIME_NAME = 'Runtime components'


class ProgressState:
    def __init__(self):
        self.phase = {}
        self.tasks = {}
        self.last = {}
        self.model_groups = []
        self.download_notices = {}
        self.runtime_parts = {}
        self.runtime_total = self.runtime_done = 0

    def update(self, event, now=None):
        now = time.monotonic() if now is None else now
        event = dict(event)
        if event.get('kind') == 'models':
            self.model_groups = event.get('groups', [])
            return
        if event.get('kind') == 'phase':
            # A parallel compiler re-sends the running phase with a larger count
            # every moment; only a different phase ends a retry notice.
            if event.get('label') != self.phase.get('label'):
                self.download_notices.clear()
            self.phase = event
            return
        key = str(event.get('key', 'current'))
        if event.get('kind') == 'progress' and key.startswith('download-patient-'):
            if event.get('state', 'running') == 'running' and event.get('detail'):
                self.download_notices[key] = event
            else:
                self.download_notices.pop(key, None)
            return
        if event.get('kind') == 'task':
            self.tasks.pop(key, None)
        previous = self.tasks.get(key, {})
        state = event.get('state', 'running')
        self.tasks[key] = dict(previous, **event, observed_at=now)
        self.tasks[key]['state'] = state
        row = self.tasks[key]
        if row.get('name') == RUNTIME_NAME and row.get('unit') == 'bytes' and number(row.get('total')) and row['total'] > 0:
            self.runtime_parts[row.get('scope') or key] = (min(row.get('done') or 0, row['total']), row['total'])
            # Wheels download side by side, each with its own size; the group
            # keeps one total that only grows, as a model group does.
            self.runtime_done = max(self.runtime_done, sum(d for d, _ in self.runtime_parts.values()))
            self.runtime_total = max(self.runtime_total, sum(t for _, t in self.runtime_parts.values()))
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
        # Keep model work visible while a parallel compiler reports a heartbeat,
        # also in the moments a file is only being connected or verified (no
        # byte total yet); otherwise the line would alternate between the two.
        def models(row):
            return (row.get('unit') == 'bytes' or row.get('activity') in MODEL_ACTIVITIES) and row['age_seconds'] <= 30
        active.sort(key=lambda row: (models(row), number(row.get('total')) and row['total'] > 0,
                                     row.get('unit') == 'bytes'), reverse=True)
        current = active[0] if active else self._view(self.last, now)
        if (current.get('name') == RUNTIME_NAME and current.get('activity') == 'download'
                and self.runtime_total > 0):
            current = dict(current, unit='bytes', total=self.runtime_total, done=min(self.runtime_done, self.runtime_total),
                           fraction=min(self.runtime_done, self.runtime_total) / self.runtime_total)
        if self.download_notices:
            notice = next(reversed(self.download_notices.values()))
            # A temporary activity line, with the underlying stage and counters
            # retained so clearing it restores the current operation immediately.
            current = dict(current, label=current.get('label') or notice['label'],
                           detail=notice['detail'], activity='', name='', notice=True)
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
    """One activity line for the launcher, including known transfer counts. zh is the interface language: a tag ('en',
    'zh-Hans', a language file's) or, as before, True for Simplified Chinese."""
    from .i18n import code_language, tr
    language = 'zh-Hans' if zh is True else 'en' if not zh else code_language(zh)
    t = lambda en, cn, values=None: tr(en, cn, language, values)
    zh = language == 'zh-Hans'
    activity, name = event.get('activity'), event.get('name') or ''
    # A Chinese name follows its verb directly; a Latin one (Python) keeps a space.
    gap = ' ' if name[:1].isascii() and name[:1].isalnum() else ''
    if language == 'en' and activity in VERB_ACTIVITIES and (
            name.startswith(COMPONENT_NAMES) or any(name == en for en, _ in NAMES.values())):
        name = name[:1].lower() + name[1:]  # In the middle of a sentence.
    if activity == 'count':
        done, total = event.get('done'), event.get('total')
        if number(done) and number(total) and total > 0 and done <= total:
            return t('{name} · Completed {done} / {total}', '{name} · 已完成 {done} / {total}', dict(name=name, done='%d' % done, total='%d' % total))
        return name
    if activity in ('verify', 'assemble', 'copy_bytes'):
        values = dict(name=name, gap=gap)
        # MiB rather than tenths of GiB: even slow disks change the visible text.
        parts = [t('Verifying {name}', '正在校验{gap}{name}', values) if activity == 'verify'
                 else t('Assembling {name}', '正在合并{gap}{name}', values) if activity == 'assemble'
                 else t('Copying {name}', '正在复制{gap}{name}', values)]
        done, total = event.get('done'), event.get('total')
        # Sampling caches are checked file by file; the changing count would only flicker.
        if (event.get('unit') == 'bytes' and number(done) and number(total) and total >= 2**20 and done <= total
                and name.lower() not in ('sampling caches', '采样缓存')):
            parts.append('%d / %d %s' % (done // 2**20, total // 2**20, 'MiB'))
        return ' · '.join(parts)
    if activity == 'resume':
        return t('The connection dropped; resuming the download and keeping what was already downloaded',
                 '连接中断，正在继续下载，已下载的部分会保留')
    if activity in ('check', 'copy'):
        parts = [t('Checking {name}', '正在校验{gap}{name}', dict(name=name, gap=gap)) if activity == 'check'
                 else t('Copying {name}', '正在复制{gap}{name}', dict(name=name, gap=gap))]
        done, total = event.get('done'), event.get('total')
        # Below 1 KiB the size would read "0 / 0 KiB".
        if event.get('unit') == 'bytes' and number(done) and number(total) and total >= 1024 and done <= total:
            parts.extend(transfer_parts(done, total, event.get('rate'), decimal_sizes))
        return ' · '.join(parts)
    if activity == 'note':
        if name in ('Finding existing models', '正在查找已有模型'):
            return t('Finding files', '正在查找文件')
        parts = [name]
        scanned = event.get('scanned')
        if number(scanned) and scanned > 0:
            parts.append(t('{:,} checked', '已查看 {} 项').format(scanned))
        return ' · '.join(parts + transfer_parts(0, 0, event.get('rate'), decimal_sizes))
    if activity == 'download':
        total = event.get('total') if event.get('unit') == 'bytes' else 0
        return ' · '.join([t('Downloading {name}', '正在下载{gap}{name}', dict(name=name, gap=gap))] +
                          transfer_parts(event.get('done'), total or 0, event.get('rate'), decimal_sizes))
    return ''


class LineHold:
    """A changed line stays three seconds before the latest one replaces it; the lines in between are skipped.
    Retry, resume, stall and error lines are shown at once and do not start a hold, a language
    switch drops the held line, and a line that ends stays until a new one replaces it or three
    seconds pass without one."""
    HOLD_SECONDS = 3.

    def __init__(self):
        self.shown = self.shown_at = self.zh = self.blank_since = None

    def text(self, text, now=None, immediate=False, zh=False):
        now = time.monotonic() if now is None else now
        if zh != self.zh:
            self.shown = self.shown_at = None
        self.zh = zh
        if immediate:
            self.shown = self.shown_at = self.blank_since = None
            return text
        if not text:
            # A blank is a change too: the line stays, a new one replaces it at once,
            # and the blank shows only when no new line came within three seconds.
            if self.shown is None:
                return text
            self.blank_since = now if self.blank_since is None else self.blank_since
            if now - self.blank_since < self.HOLD_SECONDS or now - self.shown_at < self.HOLD_SECONDS:
                return self.shown
            self.shown = self.shown_at = self.blank_since = None
            return text
        self.blank_since = None
        if text == self.shown:
            return text
        if self.shown is not None and now - self.shown_at < self.HOLD_SECONDS:
            return self.shown
        self.shown, self.shown_at = text, now
        return text


class ModelLine:
    """One steady line while a model group downloads: its shards, connections and
    checks must not take turns on the screen."""
    STALL_SECONDS, HOLD_SECONDS = 10., 3.

    def __init__(self):
        self.group = None
        self.done = self.rate = self.bytes_at = None
        self.shown = self.shown_at = None
        self.style = None
        self.finished = set()
        self.adopted_at = None

    def line(self, current, groups, connecting, zh=False, decimal_sizes=False, now=None):
        """The stabilised text, or None when this is not a model download."""
        now = time.monotonic() if now is None else now
        name = current.get('name')
        if (zh, decimal_sizes) != self.style:
            self.style, self.shown, self.shown_at = (zh, decimal_sizes), None, None
        if self.group in groups and self.done is not None and self.done >= groups[self.group]['download_bytes']:
            self.finished.add(self.group)
        # Groups that download side by side must not take turns: the line stays on one
        # group until it is complete or has gone quiet, and a finished group is not shown again.
        since = self.bytes_at if self.bytes_at is not None else self.adopted_at
        stalled = since is not None and now - since > self.STALL_SECONDS
        if (current.get('activity') == 'download' and name in groups and name != self.group
                and name not in self.finished
                and (self.group is None or self.group in self.finished or stalled)):
            self.group, self.done, self.rate, self.bytes_at = name, None, None, None
            self.adopted_at = now
            self.shown = self.shown_at = None
        modelish = current.get('activity') in MODEL_ACTIVITIES or current.get('unit') == 'bytes'
        # A build running beside the download does not take the line while the group is unfinished.
        if self.group not in groups or not (modelish or self.group not in self.finished):
            self.group = self.shown = self.shown_at = None
            return None
        group = groups[self.group]
        total = group['download_bytes']
        done = min(total, group.get('downloaded_bytes') or 0)
        if self.done is None or done > self.done:
            self.bytes_at = now if done else self.bytes_at
        self.done = done
        if group.get('bytes_per_second'):
            self.rate = group['bytes_per_second']
        shown = dict(name=self.group, total=total, done=done, rate=self.rate)
        if done >= total:
            text = progress_text(dict(shown, activity='verify'), zh)
        elif self.bytes_at is None or now - self.bytes_at > self.STALL_SECONDS:
            text = connecting
        else:
            text = progress_text(dict(shown, activity='download', unit='bytes'), zh, decimal_sizes=decimal_sizes)
        if self.shown is not None and text != self.shown and now - self.shown_at < self.HOLD_SECONDS:
            return self.shown
        if text != self.shown:
            self.shown, self.shown_at = text, now
        return text
