"""Small, dependency-free terminal UI, including before Python is installed.

Only completed work drives progress bars. Unknown-duration work stays animated.
No alternate screen, input interception, network access or extra UI packages.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import unicodedata
from .system import enable_terminal

ESCAPES = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]')
SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'


def clean(value):
    value = ESCAPES.sub('', str(value))
    return ' '.join(''.join(c for c in value if c.isprintable() or c.isspace()).split())


def clipped(value, width):
    result, used = '', 0
    for char in clean(value):
        size = 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
        if used + size > width:
            return result[:-1] + '…' if result else ''
        result += char
        used += size
    return result


def duration(seconds):
    seconds = max(0, int(seconds))
    return '%d:%02d:%02d' % (seconds // 3600, seconds // 60 % 60, seconds % 60) if seconds >= 3600 else '%02d:%02d' % (seconds // 60, seconds % 60)


def bar(done, total, width=22):
    fraction = min(1., max(0., done / total)) if total else 0.
    filled = int(width * fraction)
    return '━' * filled + '─' * (width - filled)


class ProgressEstimate:
    """Bounded estimate for the current file/counter, never the whole install."""
    def __init__(self):
        self.scope = None
        self.total = self.done = 0
        self.rate = None
        self.last = self.started = None
        self.samples = 0
        self.direct_rate = False

    def update(self, done, total, *, scope=None, rate=None, now=None):
        now = time.monotonic() if now is None else now
        done, total = max(0, done or 0), max(0, total or 0)
        if self.last is None or scope != self.scope or total != self.total or done < self.done:
            self.scope, self.total, self.done = scope, total, done
            self.last = self.started = now
            self.rate, self.samples = None, 0
            self.direct_rate = False
        advanced = done > self.done
        if rate is not None and math.isfinite(rate) and rate > 0:
            self.rate = rate if self.rate is None else .3 * rate + .7 * self.rate
            self.direct_rate = True
        elif advanced and now > self.last:
            observed = (done - self.done) / (now - self.last)
            self.rate = observed if self.rate is None else .3 * observed + .7 * self.rate
            self.samples += 1
        if advanced:
            self.last = now
        self.done = done

    def remaining(self, now=None):
        now = time.monotonic() if now is None else now
        if not self.total or self.done > self.total or not self.rate or self.last is None or now - self.last > 10:
            return None
        if not self.direct_rate and (self.samples < 3 or now - self.started < 2):
            return None
        if self.done >= self.total:
            return 0.
        return max(0., (self.total - self.done) / self.rate)


class TerminalUI:
    def __init__(self, title, *, plain=False, no_color=False, stream=None, verbose=False, show_location=True):
        self.stream = stream or sys.stdout
        self.live = not plain and self.stream.isatty() and os.environ.get('TERM') != 'dumb'
        if self.live:
            self.live = enable_terminal(self.stream)
        self.color = self.live and not no_color and 'NO_COLOR' not in os.environ
        self.unicode = 'utf' in (getattr(self.stream, 'encoding', None) or 'utf-8').lower()
        self.title = clean(title)
        self.verbose = verbose
        self.show_location = show_location
        self.tasks = {}
        self.stage = 'Preparing'
        self.done = self.total = 0
        self.resource = ''
        self.location = ''
        self.started = time.monotonic()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._lines = 0

    def event(self, kind, **value):
        if os.environ.get('FREEVIDEO_UI_EVENTS') == '1':
            self.write(json.dumps(dict(event='freevideo_ui', kind=kind, **value)) + '\n')

    def paint(self, value, code):
        return '\x1b[' + code + 'm' + value + '\x1b[0m' if self.color else value

    def write(self, value):
        if not self.unicode:
            value = value.translate(str.maketrans({'━': '=', '─': '-', '│': '|', '╭': '+', '╮': '+',
                '╰': '+', '╯': '+', '✓': '+', '✕': 'x', '…': '.', '·': '.'}))
            value = value.encode('ascii', errors='replace').decode('ascii')
        self.stream.write(value)
        self.stream.flush()

    def panel(self, title, rows):
        """Static reviewable preflight; never starts live rendering."""
        width = max(28, min(108, shutil.get_terminal_size((100, 28)).columns - 2))
        self.write('\n' + self.paint('╭─ ' + clean(title) + ' ', '1;36') + '\n')
        for label, value in rows:
            # Keep full paths/commands in scrollback, including narrow terminals.
            self.write(self.paint('│ ', '36') + self.paint(clean(label) + '  ', '1') + clean(value) + '\n')
        self.write(self.paint('╰' + '─' * min(width - 1, 70), '36') + '\n')

    def start(self, location=''):
        self.location = str(location)
        if self.live:
            self.write('\x1b[?25l')
            self._thread = threading.Thread(target=self._animate, name='freevideo-terminal', daemon=True)
            self._thread.start()
        else:
            self.write('\n' + self.title + '\n' + ('Logs: ' + clean(location) + '\n' if location and self.show_location else ''))
        return self

    def phase(self, label, done, total):
        with self._lock:
            self.stage, self.done, self.total = clean(label), done, total
            self.event('phase', label=self.stage, done=done, total=total)
            if not self.live:
                self.write('[%d/%d] %s\n' % (done, total, self.stage))

    def begin(self, key, label=None, *, detail='', total=None):
        with self._lock:
            self.tasks[key] = dict(label=clean(label or key), detail=clean(detail), total=total, done=0,
                                   state='running', started=time.monotonic(), heartbeat=time.monotonic(),
                                   estimate=ProgressEstimate(), scope=None, unit=None, activity='', name='', event_tick=0.,
                                   display_observed_at=time.monotonic())
            self.event('task', key=str(key), label=self.tasks[key]['label'], detail=clean(detail), done=0, total=total,
                       elapsed_seconds=0, remaining_seconds=None)
            if not self.live:
                self.write('  > %s%s\n' % (self.tasks[key]['label'], ' · ' + clean(detail) if detail else ''))

    def update(self, key, *, detail=None, done=None, total=None, resource=None, scope=None, rate=None,
               unit=None, activity=None, name=None, display_fraction=None, estimated=None, estimated_step_seconds=None, step_elapsed_seconds=None):
        with self._lock:
            row = self.tasks.get(key)
            if row is None:
                return
            # A new scope (for example decoding after sampling) must not
            # inherit the previous step's estimated fraction.
            if scope is not None and scope != row.get('scope'):
                row['display_fraction'] = None
                row['estimated'] = False
                row['estimated_step_seconds'] = None
                row['step_elapsed_seconds'] = None
                row['activity'] = ''
                row['name'] = ''
                row['rate'] = None
            for field, value in [('detail', clean(detail) if detail is not None else None), ('done', done), ('total', total),
                                ('activity', activity), ('name', clean(name) if name is not None else None), ('rate', rate),
                                ('scope', scope), ('unit', unit), ('display_fraction', display_fraction),
                                ('estimated', estimated), ('estimated_step_seconds', estimated_step_seconds),
                                ('step_elapsed_seconds', step_elapsed_seconds)]:
                if value is not None:
                    row[field] = value
            now = time.monotonic()
            row['display_observed_at'] = now
            if done is not None or total is not None:
                row['estimate'].update(row['done'], row['total'], scope=row['scope'], rate=rate, now=now)
            if resource is not None:
                self.resource = clean(resource)
            if now - row['event_tick'] >= .5:
                self.event('progress', key=str(key), label=row['label'], detail=row['detail'], done=row['done'], total=row['total'],
                           resource=self.resource, elapsed_seconds=now-row['started'], remaining_seconds=row['estimate'].remaining(now),
                           unit=row['unit'], activity=row['activity'], name=row['name'], rate=row.get('rate'),
                           display_fraction=row.get('display_fraction'), estimated=row.get('estimated', False),
                           estimated_step_seconds=row.get('estimated_step_seconds'),
                           step_elapsed_seconds=row.get('step_elapsed_seconds'))
                row['event_tick'] = now
            if not self.live and time.monotonic() - row['heartbeat'] >= 30:
                self.write('  … %s · %s · %s%s\n' % (row['label'], duration(time.monotonic() - row['started']),
                    row['detail'] + self.eta_text(row), ' · ' + self.resource if self.resource else ''))
                row['heartbeat'] = time.monotonic()

    def end(self, key, *, success=True, detail=None):
        with self._lock:
            row = self.tasks.get(key)
            if row is None:
                return
            row.update(state='complete' if success else 'failed', seconds=time.monotonic() - row['started'])
            if success and row['total']:
                row['done'] = row['total']
            if detail is not None:
                row['detail'] = clean(detail)
            self.event('task_end', key=str(key), label=row['label'], detail=row['detail'], done=row['done'], total=row['total'],
                       state=row['state'], activity='', name='',
                       elapsed_seconds=row['seconds'], remaining_seconds=0 if success else None)
            if not self.live:
                self.write('  %s %s · %s · %s\n' % ('✓' if success else '✕', row['label'], duration(row['seconds']), row['detail']))

    @staticmethod
    def eta_text(row):
        remaining = row['estimate'].remaining() if row['state'] == 'running' else None
        if remaining == 0:
            return ' · Finalizing'
        return ' · ETA ~' + duration(math.ceil(remaining)) if remaining is not None else ''

    def render(self, width=100, height=28):
        with self._lock:
            width = max(26, width - 2)
            line = lambda s: clipped(s, width)
            progress = '%s  %d/%d steps complete' % (bar(self.done, self.total, min(28, width // 3)), self.done, self.total) if self.total else ''
            lines = [self.paint(line('  FREEVIDEO  /  ' + self.title), '1;36'),
                     line('  ' + self.stage + '   ·   ' + duration(time.monotonic() - self.started)),
                     self.paint(line('  ' + progress), '35'), '']
            tasks = list(self.tasks.values())
            slots = max(1, (height - 10) // 2)
            # Always keep parallel active work on screen, followed by recent results.
            active = [r for r in tasks if r['state'] == 'running']
            history = max(0, slots - len(active))
            if not self.verbose and active:
                history = min(2, history)
            recent = [r for r in tasks if r['state'] != 'running'][-history:] if history else []
            visible = (recent + active)[-slots:]
            for row in visible:
                running = row['state'] == 'running'
                icon = SPINNER[int(time.monotonic() * 8) % len(SPINNER)] if running else ('✓' if row['state'] == 'complete' else '✕')
                elapsed = time.monotonic() - row['started'] if running else row['seconds']
                timing = ('Elapsed ' if running else '') + duration(elapsed) + self.eta_text(row)
                title = clipped(row['label'], max(8, width-len(timing)-9))
                lines.append(self.paint(line('  %s  %s   %s' % (icon, title, timing)), '36' if running else ('32' if row['state'] == 'complete' else '31')))
                if row['total']:
                    display = row.get('display_fraction')
                    if not isinstance(display, (int, float)) or not math.isfinite(display):
                        display = row['done'] / row['total']
                    # Layer events are deliberately rate-limited in the
                    # worker. Once one warm step provides a duration, carry
                    # the visual estimate forward between events while the
                    # completed N/total counter remains unchanged.
                    if row.get('estimated') and isinstance(row.get('estimated_step_seconds'), (int, float)) \
                            and math.isfinite(row['estimated_step_seconds']) and row['estimated_step_seconds'] > 0 \
                            and isinstance(row.get('step_elapsed_seconds'), (int, float)) \
                            and math.isfinite(row['step_elapsed_seconds']):
                        age = max(0., time.monotonic() - row.get('display_observed_at', time.monotonic()))
                        local = min(.88, .88 * (row['step_elapsed_seconds'] + age) / row['estimated_step_seconds'])
                        display = max(display, (row['done'] + local) / row['total'])
                    display = min(1., max(0., display))
                    if row['unit'] == 'bytes':
                        counter = 'File %.0f%%' % min(100, 100 * display)
                    else:
                        prefix = '~' if row.get('estimated') else ''
                        counter = '%d/%d · %s%.0f%%' % (row['done'], row['total'], prefix, 100 * display)
                    detail = '%s %s  ' % (bar(display, 1, min(18, width // 4)), counter)
                else:
                    detail = ''
                if running or row['state'] == 'failed' or self.verbose:
                    lines.append(line('     ' + detail + row['detail']))
            footer = 'Files and diagnostics saved' if self._stop.is_set() else 'Ctrl+C to stop · run setup again to resume' if not self.show_location else 'Ctrl+C safely stops workers · all files retained'
            lines += ['', line('  ' + self.resource)]
            if self.show_location and self.location:
                lines.append(self.paint(line('  Logs / outputs  ' + self.location), '2'))
            lines.append(self.paint(line('  ' + footer), '2'))
            return lines

    def _draw(self):
        size = shutil.get_terminal_size((100, 28))
        lines = self.render(min(112, size.columns), size.lines)
        previous = '\x1b[%dA' % self._lines if self._lines else ''
        self.write(previous + '\r\x1b[J' + '\n'.join(lines) + '\n')
        self._lines = len(lines)

    def _animate(self):
        while not self._stop.wait(.15):
            with self._lock:
                self._draw()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self.live:
            with self._lock:
                self._draw()
                self.write('\x1b[?25h')


def model_download_name(filename):
    name = str(filename).replace('\\', '/').rsplit('/', 1)[-1]
    shard = re.fullmatch(r'diffusion_pytorch_model-(\d+)-of-(\d+)\.safetensors', name)
    if shard:
        index, total = map(int, shard.groups())
        component = 'Video model' if total == 14 else 'Video decoder' if total == 3 else 'Model'
        return '%s · shard %d / %d' % (component, index, total)
    return name


class LogProgress:
    """Incrementally consume structured worker events and Ninja counters."""
    def __init__(self, path):
        self.path, self.offset, self.pending = Path(path), 0, ''
        self.packages = {}
        self.installing_packages = False
        self.model_source = ''
        self.model_backend = ''
        self.parallel_tasks = {}
        self.model_groups = None

    def take_tasks(self):
        tasks, self.parallel_tasks = self.parallel_tasks, {}
        return tasks

    @staticmethod
    def merge(result, values):
        # Several scopes can arrive in a single read, before TerminalUI sees
        # them. Do not carry activity/name from an earlier file into the last.
        if values.get('scope') is not None and values['scope'] != result.get('scope'):
            result.update(activity='', name='')
        result.update(values)

    def read(self, *, final=False):
        result = {}
        try:
            with self.path.open(errors='replace', encoding='utf-8') as stream:
                stream.seek(self.offset)
                # Catch up after bursts without reading an entire log into RAM.
                # After worker exit, consume the whole tail: its last ready or
                # paused event must reach the UI before the next stage starts.
                chunks = 0
                while final or chunks < 8:
                    chunk = stream.read(65536)
                    self.offset = stream.tell()
                    if not chunk:
                        break
                    chunks += 1
                    self.merge(result, self._consume(chunk))
        except OSError:
            pass
        return result

    def _consume(self, chunk):
        lines = (self.pending + chunk).replace('\r', '\n').split('\n')
        self.pending = lines.pop()
        result = {}
        def update(**values):
            self.merge(result, values)
        for line in lines:
            # uv does not emit byte progress to redirected logs. Show the real
            # package phase and announced sizes, without inventing a percentage.
            text = clean(line)
            git = re.search(r'(Receiving objects|Resolving deltas|Updating files):\s*\d+%\s*\((\d+)/(\d+)\)', text)
            if git:
                phase, done, total = git.groups()
                # The card says what Git is doing; its percentages and counts stay in the terminal.
                update(done=int(done), total=int(total), scope='git-' + phase, unit='items', detail=text, activity='note',
                       name='Downloading components' if phase == 'Receiving objects' else 'Preparing components')
            package = re.fullmatch(r'Downloading ([\w.-]+) \(([\d.]+)(KiB|MiB|GiB)\)', text)
            if package:
                name, size, unit = package.groups()
                self.packages[name] = float(size) * {'KiB': 2**10, 'MiB': 2**20, 'GiB': 2**30}[unit]
                amount = sum(self.packages.values()) / 2**30
                size_text = '%.1f GiB' % amount if amount >= 1 else '%.0f MiB' % (amount * 1024)
                update(done=0, total=0, scope='package-download', activity='note', name='Downloading runtime packages',
                              detail='Downloading packages · about %s across %d files' % (size_text, len(self.packages)))
            elif re.match(r'Resolved \d+ packages? in ', text):
                self.packages.clear()
                self.installing_packages = False
                update(done=0, total=0, scope='package-resolve', detail='Dependencies resolved · preparing downloads')
            elif re.match(r'Prepared \d+ packages? in ', text):
                self.installing_packages = True
                update(done=0, total=0, scope='package-install', detail='Downloads ready · installing packages')
            elif re.match(r'Installed \d+ packages? in ', text):
                update(done=0, total=0, scope='package-finish', detail='Packages installed · finishing checks')
            match = re.search(r'\[(\d+)/(\d+)\]', line)
            if match:
                self.installing_packages |= 'Installing wheels' in text
                update(done=int(match[1]), total=int(match[2]),
                              detail='Installing packages' if self.installing_packages else 'Compiling CUDA kernels',
                              scope='package-install' if self.installing_packages else 'compile', unit='items')
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get('event')
            if kind == 'model_groups':
                self.model_groups = event['groups']
                continue
            if kind in ('loaded', 'sampling_start', 'sampling_progress', 'step', 'sample_finalize'):
                from .sampling_progress import progress_message
                message = progress_message(event)
                if message is not None:
                    detail = message['label']
                    if message.get('detail'):
                        detail += ' · ' + message['detail']
                    if kind == 'step':
                        detail += ' · %.2f s last step' % event['seconds']
                    update(done=message['done'], total=message['total'], detail=detail,
                                  scope=message['phase'], unit='items',
                                  display_fraction=message.get('display_fraction'),
                                  estimated=message.get('estimated', False),
                                  estimated_step_seconds=message.get('estimated_step_seconds'),
                                  step_elapsed_seconds=message.get('step_elapsed_seconds'))
            elif kind == 'prepared_blocks':
                update(done=event['blocks'], total=50, detail='Load cached blocks and upload resident weights')
                if event['blocks'] == 50:
                    update(done=0, total=0, detail='Finish model placement')
            elif kind == 'model_ready':
                if event.get('operation') == 'verify':
                    continue  # Local verification cannot reset download ETA.
                self.model_source = ''
                self.model_backend = ''
                update(done=0, total=0, detail='Verified ' + Path(event['file']).name, scope='verified', unit='items')
            elif kind == 'prepare_fp8_phase':
                work = 'Verify cached weights' if event['phase'] == 'verify_cache' else 'Read and convert source weights'
                update(done=max(0, event['index'] - 1), total=event['total'],
                              detail=work + ' · ' + event['group'], scope='prepare-fp8-' + event['phase'], unit='items')
            elif kind == 'prepared_fp8_group':
                update(done=event.get('index', 0), total=event.get('total', 0),
                              detail=('Reuse verified FP8 ' if event.get('reused') else 'Prepare FP8 ') + event['group'], scope='prepare-fp8', unit='items')
            elif kind == 'storage_compacted':
                update(done=0, total=0, activity='', name='',
                       detail='Storage ready · %.2f GiB released' % (event['released_bytes'] / 2**30))
            elif kind in ('verify_model', 'download_model', 'assemble_model'):
                self.model_source = ''
                self.model_backend = ''
                update(done=0, total=0, scope=event['file'], unit='bytes',
                              activity={'verify_model': 'check', 'download_model': 'download'}.get(kind, ''),
                              name=event.get('name') or model_download_name(event['file']).split(' · ')[0],
                              detail={'verify_model': 'Verify ', 'download_model': 'Download ',
                                      'assemble_model': 'Assemble downloaded parts · '}[kind] + model_download_name(event['file']))
            elif kind == 'network' and event.get('category') in ('models', 'vdn-models', 'edge-models') and event.get('action') == 'attempt':
                self.model_backend = ''
                self.model_source = {'modelscope': 'ModelScope', 'official': 'Hugging Face',
                                     'hf-mirror': 'HF Mirror', 'user': 'Custom source'}.get(event['source'], 'Model source')
                update(done=0, total=0, scope='connecting-' + self.model_source, unit='bytes',
                              detail='Connecting to ' + self.model_source + ' · ' + event['file'])
            elif kind == 'network' and event.get('action') == 'backend':
                self.model_backend = {'hf-xet': 'HF Xet', 'modelscope-ranges': 'ModelScope parallel',
                                      'http-ranges': 'Parallel HTTP', 'hf-http': 'HF HTTP'}.get(event.get('method'), '')
            elif kind == 'network' and event.get('action') == 'manual-switch':
                self.model_source=event.get('source','')
                self.model_backend=''
                detail='Switched current download to '+self.model_source
                if event.get('retained_unmapped_bytes'):
                    detail+=' · Restarting this file; incompatible fragments retained on disk'
                else:
                    detail+=' · Resuming %.1f MiB retained' % (event.get('resume_bytes',0)/2**20)
                self.last_notice=detail
                update(done=0,total=0,rate=None,scope='manual-switch',unit='bytes',detail=detail)
            elif kind == 'network' and event.get('action') in ('retry', 'route-retry', 'resume-refused', 'paused', 'restart-approved'):
                action = event['action']
                label = {'retry': 'Retrying same source', 'resume-refused': 'Resume unavailable; checking another source',
                         'route-retry': 'Retrying same source with ' + ('direct connection' if event.get('route') == 'direct' else 'configured proxy'),
                         'paused': 'Paused; existing data kept', 'restart-approved': 'Restart explicitly allowed'}[action]
                detail = '%s · %s · %.1f MiB retained · %s' % (label, event.get('source', ''),
                    event.get('resume_bytes', event.get('retained_bytes', 0))/2**20, event.get('file', ''))
                if action == 'retry':
                    detail += ' · retry %d in %ds' % (event['retry_number'], event['wait_seconds'])
                if action == 'paused' and event.get('reason') == 'xet-or-legacy-resume-unavailable':
                    # Xet can preallocate the entire file: its logical size is
                    # not a reliable count of bytes actually downloaded.
                    detail = label + ' · Cannot resume this format; enable restart in Setup · ' + event.get('file', '')
                self.last_notice = detail if action == 'paused' else None
                update(done=0, total=0, rate=None, scope=action, unit='bytes', detail=detail)
                if action in ('retry', 'route-retry'):
                    update(activity='resume', name='')
            elif kind == 'network' and event.get('category') in ('models', 'vdn-models', 'edge-models') and event.get('action') in ('fallback', 'verifying'):
                action = event['action']
                self.model_backend = ''
                detail = ('Verify %s · ' % event.get('algorithm', 'integrity').upper() if action == 'verifying'
                          else 'Download response too large · switching source · ' if event.get('reason') == 'response-size-exceeded'
                          else 'Download memory budget reached · switching to streaming · ' if event.get('reason') in ('memory-guard', 'low-live-memory-use-streaming')
                          else 'Download source failed · trying another source · ')
                update(done=0, total=0, rate=None, scope=action + '-' + event['file'],
                              unit='bytes', detail=detail + event['file'])
            elif kind == 'decode_phase':
                update(done=0, total=0, detail=event['phase'])
            elif kind == 'local_model_progress':
                from .desktop_runtime import local_model_ui
                local = local_model_ui(event)
                update(done=local['done'], total=local['total'], rate=event.get('bytes_per_second'),
                              activity=local['activity'], name=local['name'],
                              scope='local-' + event.get('file', ''), unit='bytes', detail=local['label'] + ' · ' + local['detail'])
            elif kind == 'model_verification_progress':
                state = event.get('state', 'running')
                active = state == 'running'
                self.parallel_tasks['verify-models'] = dict(label='Verify / reuse local models', state=state,
                    activity='check' if active else '',
                    name=(event.get('name') or model_download_name(event.get('file', '')).split(' · ')[0]) if active else '',
                    done=event.get('done_bytes', 0) if active else event.get('files_done', 0),
                    total=event.get('total_bytes', 0) if active else event.get('files_total', 0),
                    rate=event.get('bytes_per_second') if active else None,
                    unit='bytes' if active else 'items', scope='local-' + event.get('file', ''),
                    detail=('%d / %d files · %.1f MiB/s · %s' %
                            (event.get('files_done', 0), event.get('files_total', 0),
                             event.get('bytes_per_second', 0)/2**20, event.get('file', '')))
                           if active else 'Local models verified' if state == 'complete' else 'Local verification stopped; files retained')
            elif kind == 'download_progress':
                done, total = event['done_bytes'], event.get('total_bytes') or 0
                description = event.get('description', 'download')
                if total and done > total:
                    # Older workers and invalid backend counters must not look
                    # like successful completion. Retain the reported numbers
                    # as an explicit error, with no percentage or estimate.
                    update(done=0, total=0, rate=None, scope='invalid-' + description, unit='bytes',
                        detail='Download counter mismatch · %.1f MiB reported / %.1f MiB expected · %s' %
                               (done/2**20, total/2**20, description))
                    continue
                self.model_backend = {'hf-xet': 'HF Xet', 'modelscope-ranges': 'ModelScope parallel',
                                      'hf-http': 'HF HTTP'}.get(event.get('method'), self.model_backend)
                source = self.model_backend or self.model_source
                speed = event.get('network_bytes_per_second')
                shown_speed = speed if speed is not None else event['bytes_per_second']
                update(done=done, total=total, scope=description + self.model_source, rate=event['bytes_per_second'], unit='bytes',
                    activity='download', name=event.get('name') or ('PyTorch' if description == 'torch'
                        else model_download_name(description).split(' · ')[0]),
                    detail='%s%s%.1f MiB/s · %.1f / %.1f MiB · %s' %
                    (source + ' · ' if source else '',
                     '~' if event.get('counter_source') == 'uv-terminal' else '', shown_speed/2**20,
                     done/2**20, total/2**20, model_download_name(event.get('description', 'Download'))))
        return result
