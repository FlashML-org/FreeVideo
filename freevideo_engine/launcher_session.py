"""Widget-independent state for the Qt launcher; uses the existing installer."""
import locale
import os
from pathlib import Path
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import __version__
from .comfy_launcher_runtime import Controller, layout, local_url, new_layout
from .desktop_runtime import launcher_root, materialize_source
from .download_settings import Probe, read as download_preferences, speed_text
from .launcher_settings import Store
from .launcher_terminal import Tail
from .model_status import FAMILIES, NAMES
from .setup_progress import progress_text
from .terminal_ui import clean, duration


class Session:
    def __init__(self, source=None, *, controller=None, store=None, updater=None, smoke=False):
        from .offline_packages import Importer
        self.importer = Importer()
        self.imported_batch = None
        self.source = Path(source or materialize_source())
        self.controller = controller or Controller(self.source)
        self.store = store or Store(launcher_root())
        saved = self.store.read()
        home = Path(os.environ.get('USERPROFILE') or os.environ.get('HOME') or launcher_root().parent)
        self.form = dict(comfy='', destination=str(home / 'FreeVideo'), engine='', python='',
                         url='http://127.0.0.1:8188', models='', model_dirs=[], model_method='auto',
                         separate=False, repair=False, new_comfy=True, offline_runtime='', offline_models=[])
        self.form.update({k: v for k, v in saved.items() if k in self.form})
        if self.form['model_method'] == 'reuse':
            self.form['model_method'] = 'manual' if self.form['offline_runtime'] else 'auto'
        self.form['engine'] = os.environ.get('FREEVIDEO_HOME') or self.form['engine']
        if saved.get('comfy') and 'new_comfy' not in saved:
            self.form['new_comfy'] = False
        self.language = saved.get('language', (locale.getlocale()[0] or '').lower())
        self.selected = saved.get('installation')
        self.saved_setup = saved.get('setup', {})
        self.page = 'comfy'
        self.token = ''
        self.error = ''
        self.notice = ''
        self.report = dict(status='idle', path='', error='')
        self.compatibility = dict(available=False, level=0, automatic=False)
        self.probe = Probe()
        self.tail = Tail()
        self.log_source = None
        self.model_groups = []
        self.browser_attempted = False
        self.browser_error = ''
        self.started = None
        self.closing = False
        self.smoke = smoke
        self.reported_setup = None
        self.update_snoozed = False
        if self.controller.restore(dict(saved, engine=self.form['engine'])):
            self.remember(self.controller.selection, persist=False)
            self.page = 'launcher'
        elif self.saved_setup.get('status') in ('running', 'failed', 'cancelled'):
            self.page = 'progress'
        self.updater = updater
        if updater is None and not smoke:
            from .launcher_update import current_build, UpdateClient
            identity = current_build()
            if identity:
                self.updater = UpdateClient(identity, launcher_root())
        if self.updater and not self.updater.busy:
            self.updater.run('check')
        if self.form['offline_runtime'] and not controller:
            root = Path(self.form['offline_runtime'])
            if (root / 'portable.json').is_file():
                self.activate_offline(root)
        self.inspect_compatibility()

    def t(self, en, zh):
        return zh if self.language.startswith('zh') else en

    def engine_root(self):
        if self.form['engine'].strip():
            return Path(self.form['engine']).expanduser().resolve()
        if self.form['new_comfy']:
            return Path(self.form['destination']).expanduser().resolve() / 'FreeVideo-engine'
        if self.form['comfy'].strip():
            return Path(layout(self.form['comfy'])['root']) / 'FreeVideo-engine'
        raise ValueError(self.t('Choose your installation folder first.', '请先选择安装位置。'))

    def persist(self):
        self.store.write(self.form, self.language, self.selected, self.saved_setup)

    def remember(self, selection, persist=True):
        self.selected = dict(selection)
        self.form.update(comfy=selection['root'], engine=selection['engine'],
                         python=selection.get('python') or '', url=selection['url'],
                         separate=selection.get('separate', False), new_comfy=False, repair=False)
        if persist:
            self.persist()

    def edit(self, key, value):
        if key == 'language':
            self.language = str(value); self.persist(); return
        if key == 'token':
            if self.controller.busy:
                raise ValueError(self.t('Pause installation before changing the token.', '请先暂停安装，再修改 Token。'))
            from .hf_auth import validate
            self.token = validate(str(value)); return
        if key not in self.form or self.controller.busy or self.importer.busy:
            return
        if key in ('separate', 'repair', 'new_comfy'):
            value = bool(value)
        if key == 'model_method' and value not in ('auto', 'manual', 'reuse'):
            raise ValueError('Unknown model download method')
        if self.form[key] == value:
            return
        self.form[key] = value
        self.controller.selection = None
        self.controller.state = dict(status='idle')
        self.browser_attempted = False
        self.browser_error = self.error = ''
        self.persist()

    def add_folder(self, folder):
        from .local_models import library_roots
        self.edit('model_dirs', library_roots(self.form['model_dirs'] + [folder]))

    def remove_folder(self, index):
        removed = self.form['model_dirs'][index]
        self.edit('model_dirs', [p for i, p in enumerate(self.form['model_dirs']) if i != index])
        self.edit('offline_models', [p for p in self.form['offline_models']
                                    if Path(p) / 'models' != Path(removed)])

    def clear_runtime(self):
        # Detach it from this plan; imported files remain available for reuse.
        self.edit('offline_runtime', '')


    def import_packages(self, paths):
        if self.controller.busy or self.importer.busy:
            return
        if not paths:
            return
        self.edit('model_method', 'manual')
        destination = (Path(self.form['destination']).expanduser().resolve() if self.form['new_comfy']
                       else self.engine_root().parent)
        self.importer.start(paths, destination)
        self.imported_batch = None
        self.error = ''

    def activate_offline(self, root):
        from .portable_launcher import PortableController
        controller = PortableController(root)
        controller.restore({'url': self.form['url']})
        self.controller.close()
        self.controller = controller
        self.form['offline_runtime'] = str(root)
        self.remember(controller.selection)
        self.page = 'launcher'
        self.persist()

    def action(self, name, accepted=False):
        if self.importer.busy:
            if name == 'stop':
                self.importer.cancelled.set()
            return
        if self.controller.busy:
            if name == 'stop':
                self.controller.cancel()
            return
        self.error = ''
        if name == 'setup':
            if getattr(self.controller, 'fixed_environment', False) is True:
                self.controller.close()
                self.controller = Controller(self.source)
            self.page = 'comfy'; return
        if name == 'launcher' and self.selected:
            if not self.controller.restore({'installation': self.selected}):
                raise ValueError(self.t('Locate or repair this installation in Setup.', '请在安装设置中重新定位或修复这份安装。'))
            self.page = 'launcher'
            return
        if name == 'update-engine' and self.selected:
            self.persist()
            self.controller.run('inspect', dict(self.form, token=self.token))
            self.page = 'progress'
            self.model_groups = []
            self.started = time.monotonic()
            return
        if name == 'back':
            self.page = 'comfy' if self.page == 'models' else 'models'
            return
        if name == 'browser':
            self.open_browser(); return
        if name == 'shortcut':
            self.controller.run('shortcut', self.controller.state.get('status')); return
        if name not in ('primary', 'launch'):
            return
        state = self.controller.state.get('status')
        if (self.page == 'models' and self.form['new_comfy'] and self.form['offline_runtime']
                and self.form['model_method'] == 'manual'):
            self.importer.prepare(self.form['offline_runtime'], self.form['offline_models'], self.source)
            return
        if self.page == 'comfy':
            new_layout(self.form['destination']) if self.form['new_comfy'] else layout(self.form['comfy'])
            self.persist(); self.page = 'models'; return
        self.browser_attempted = False
        self.browser_error = ''
        if self.page == 'launcher' and getattr(self.controller, 'fixed_environment', False) is True:
            consent = self.controller.root / 'engine/portable-consent.json'
            if not consent.exists():
                if not accepted:
                    raise ValueError(self.t('Accept the installation plan and licenses.', '请先同意安装计划及许可证。'))
                from .monitoring import save
                save(consent, dict(accepted_at=time.time()))
            self.controller.run('launch', {'url': self.form['url']})
        elif self.page == 'launcher':
            self.controller.run('launch', {'installation': self.selected})
        elif self.page == 'progress' and state == 'review':
            if not accepted or self.controller.state.get('errors'):
                raise ValueError(self.t('Accept the reviewed installation plan to continue.', '请先同意本次安装计划及许可证。'))
            self.controller.run('install', True)
        elif state in ('open', 'restart-required'):
            self.controller.run('connect')
        else:
            self.persist()
            self.controller.run('inspect', dict(self.form, token=self.token))
            self.page = 'progress'
            self.model_groups = []
        self.started = time.monotonic()

    def _browser_url(self, address):
        """Return the URL used by the browser, with the launcher locale hint."""
        if not address or not self.language.startswith('zh'):
            return address
        parsed = urlsplit(address)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query['freevideo_lang'] = 'zh'
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                           urlencode(query), parsed.fragment))

    def open_browser(self):
        if self.controller.state.get('status') != 'open':
            return
        from .windows_ux import open_browser
        self.browser_attempted = True
        address = self._browser_url(self.controller.state['url'])
        # ComfyUI itself may follow the browser's language, but the FreeVideo
        # panels need an explicit choice when the launcher language differs
        # from the operating system. Keep the controller's canonical URL
        # unchanged so saved installations and existing integrations remain
        # compatible; only the browser hint is added at launch time.
        try:
            if not open_browser(address):
                raise OSError('The system did not accept the browser request')
            self.browser_error = ''
        except Exception as error:
            self.browser_error = self.t('ComfyUI is ready. Open the browser again or copy its address.\n',
                                        'ComfyUI 已就绪，请重试打开浏览器，或复制地址手动打开。\n') + str(error)

    def select_source(self, name):
        self.probe.select(self.engine_root(), name)

    def select_proxy_mode(self, mode):
        self.probe.select(self.engine_root(), proxy_mode=mode)

    def speed_test(self):
        self.probe.start(self.engine_root(), self.token)

    def inspect_compatibility(self):
        from .compatibility import check_installation
        try:
            root = self.engine_root()
            if (root / 'machine.json').is_file():
                self.compatibility = check_installation(root)
                if self.compatibility.get('notice'):
                    self.notice = self.t('Compatibility settings were enabled after an interrupted generation. You can change them in Settings.',
                                        '上次生成中断后已开启兼容性设置，可在设置中调整或关闭。')
        except (OSError, ValueError, RuntimeError):
            self.compatibility = dict(available=False, level=0, automatic=False)

    def set_compatibility(self, level, automatic):
        from .compatibility import Store, installed_identity
        root = self.engine_root()
        Store(root).set(installed_identity(root), level, automatic)
        self.inspect_compatibility()

    def dismiss_notice(self):
        from .compatibility import Store, installed_identity
        if self.compatibility.get('notice'):
            root = self.engine_root()
            Store(root).acknowledge(installed_identity(root), self.compatibility['notice']['id'])
        self.notice = ''

    def full_log(self):
        from .diagnostics import read_complete
        from .failure_details import redacted_launcher_error
        if self.tail.path is None:
            return self.tail.text
        raw, _ = read_complete(self.tail.path)
        return redacted_launcher_error(raw.decode('utf-8-sig', errors='replace'),
                                       [(self.token, '<REDACTED>')] if self.token else [])

    def export_report(self, output):
        if self.report['status'] == 'running':
            return
        from .diagnostics import collect, Redactor
        root = self.engine_root()
        # Capture the failure and selected logs before another action changes
        # the screen. Collection does not need a working Python/GPU runtime.
        notes = self.snapshot()['error']
        logs = tuple(path for _, path in self.controller.terminal_sources())
        replacements = [(self.token, '<REDACTED>')] if self.token else []
        self.report = dict(status='running', path=str(output), error='')

        def work():
            try:
                result = collect(root, root / 'machine.json', None, Path(output),
                                 complete=True, extra_files=logs, notes=notes)
                self.report = dict(status='complete', path=str(output), error='',
                                   collection_errors=len(result['errors']))
            except Exception as error:
                self.report = dict(status='error', path=str(output),
                                   error=Redactor(replacements).text(str(error)))

        threading.Thread(target=work, name='freevideo-export-report', daemon=True).start()

    def dismiss_update(self):
        self.update_snoozed = True
        if self.updater and self.updater.state['status'] == 'downloading':
            self.updater.cancelled.set()

    def check_update(self, token=''):
        if self.updater and not self.updater.busy:
            if token.strip():
                self.updater.token = token.strip()
            self.updater.run('check')

    def update(self, token=''):
        if not self.updater or self.updater.busy or self.controller.busy or self.importer.busy:
            return
        from .launcher_update import launch_download, RELEASE_PAGE, DownloadedLauncherUnavailable
        from .windows_ux import open_browser
        row = self.updater.state
        if row['status'] == 'ready':
            self.persist()
            try:
                launch_download(row['candidate'], self.updater.root, token=self.updater.token)
            except DownloadedLauncherUnavailable:
                self.error = ''
                self.updater.run('download', row['candidate'])
                return
            self.closing = True
        elif row.get('candidate'):
            if self.updater.current.get('packaging') == 'onedir':
                open_browser(RELEASE_PAGE)
            else:
                self.updater.run('download', row['candidate'])
        else:
            self.updater.token = token.strip()
            self.updater.run('check')

    def close(self):
        self.importer.cancelled.set()
        if getattr(self, '_closed', False):
            return
        try:
            self.persist()
        finally:
            self.closing = True
            if self.updater:
                self.updater.cancelled.set()
            self.controller.close()
            self._closed = True

    def tick(self):
        imported = self.importer.state
        if not self.importer.busy and imported is not self.imported_batch:
            self.imported_batch = imported
            for package in imported.get('packages', []):
                if package['kind'] == 'runtime':
                    self.form['offline_runtime'] = package['root']
                else:
                    roots = self.form['offline_models']
                    if package['root'] not in roots:
                        self.form['offline_models'] = roots + [package['root']]
                        self.add_folder(str(Path(package['root']) / 'models'))
            if imported.get('packages'):
                self.persist()
            if imported['status'] == 'error':
                self.error = imported['error']
            elif imported['status'] == 'prepared':
                self.activate_offline(imported['ready_root'])

        row = self.controller.state
        busy = self.controller.busy
        groups = row.get('task', {}).get('model_groups') or row.get('model_groups') or row.get('plan', {}).get('model_groups')
        if groups:
            self.model_groups = groups
        selection = row.get('selection')
        if not busy and selection and selection.get('ready') and (row.get('deployed') or row.get('status') in ('open', 'restart-required')):
            if selection != self.selected:
                self.remember(selection)
            if row.get('action') == 'install' or row.get('status') in ('open', 'restart-required'):
                self.page = 'launcher'
        if row.get('action') == 'install':
            saved = dict(status=row.get('status', ''), action='install', phase=row.get('overall', {}).get('label', ''))
            if self.saved_setup != saved:
                self.saved_setup = saved; self.persist()
            if not busy and self.started and row.get('status') in ('open', 'failed', 'cancelled', 'restart-required'):
                key = 'setup-%s-%s' % (self.started, row['status'])
                if self.reported_setup != key and not self.smoke:
                    self.reported_setup = key
                    from .installation_diagnostics import write
                    write(self.engine_root(), time.time()-(time.monotonic()-self.started),
                        {'summary': {'status': 'complete' if row['status'] in ('open', 'restart-required') else 'incomplete',
                        'hardware': row.get('plan', {}).get('inventory', {}).get('hardware', {}),
                        'stages': [{'stage': 'installation', 'seconds': time.monotonic()-self.started}]},
                        'request': {'exception': row.get('exception', []), 'phase': self.controller.section or 'installation'},
                        'log_tails': {'installation': row.get('error', '')}})
        if not busy and not self.closing and row.get('status') == 'open' and not self.browser_attempted:
            self.open_browser()
        sources = self.controller.terminal_sources()
        if sources:
            selected = next((p for _, p in sources if p == self.log_source), sources[-1][1])
            self.tail.select(selected)
            self.tail.read(final=not self.controller.terminal_running(selected))

    def snapshot(self):
        from .failure_details import launcher_failure, redacted_launcher_error
        from .launcher_copy import display, progress_view, source_name
        zh = self.language.startswith('zh')
        row = self.controller.state
        task = row.get('task', {})
        progress = progress_view(task.get('progress') or {}, zh)
        overall = progress_view(row.get('overall') or task.get('phase_progress') or {}, zh)
        errors = [self.error, self.browser_error, row.get('error', ''), *row.get('errors', [])]
        shortcut = row.get('shortcut') or {}
        if shortcut.get('status') == 'failed':
            errors.append(shortcut.get('error', ''))
        error = redacted_launcher_error('\n'.join(str(e) for e in errors if e),
                                        [(self.token, '<REDACTED>')] if self.token else [])
        by_id = {r['id']: r for r in self.model_groups}
        ready = bool(row.get('selection', {}).get('ready') or row.get('status') == 'open')
        models = []
        states = {'ready': ('Ready locally', '本地已就绪'), 'waiting': ('Waiting for scan', '等待检查'),
                  'pending': ('Download needed', '需要下载'), 'downloading': ('Downloading', '正在下载'),
                  'verifying': ('Transfer complete · verifying (no re-download)', '传输完成 · 正在校验（不会重复下载）'),
                  'paused': ('Paused', '已暂停')}
        for name in FAMILIES:
            item = dict(by_id.get(name, {}))
            state = 'ready' if ready else item.get('state', 'waiting')
            if state == 'waiting' and item.get('download_bytes'):
                state = 'pending'
            total = item.get('total_bytes', 0)
            done = total if state == 'ready' else min(total,
                    item.get('verified_bytes', 0)+item.get('downloaded_bytes', 0))
            models.append(dict(id=name, title=self.t(*NAMES[name]), state=state,
                detail=self.t(*states.get(state, states['waiting'])), done=done, total=total,
                found=item.get('existing_bytes', 0), download=item.get('download_bytes', 0),
                rate='%.1f MiB/s' % (item['bytes_per_second']/2**20) if item.get('bytes_per_second') else ''))
        try:
            preferences = download_preferences(self.engine_root() / 'download-settings.json')
        except (OSError, ValueError):
            preferences = dict(source='auto', proxy_mode='auto')
        source = preferences['source']
        plan = row.get('plan', {})
        estimate = []
        gpu = plan.get('inventory', {}).get('hardware', {}).get('gpu_name')
        if gpu:
            estimate.append(gpu)
        if 'model_download_bytes' in plan:
            estimate.append(self.t('Download ', '需下载 ')+'%.1f GiB' % (plan['model_download_bytes']/2**30))
        if row.get('disks'):
            estimate.append(self.t('Peak disk ~', '磁盘峰值约 ')+'%.1f GiB' % (sum(d.get('needed_bytes', 0) for d in row['disks'])/2**30))
        update = dict(self.updater.state) if self.updater else dict(status='development')
        probe = self.probe.snapshot()
        speeds = []
        for group, entries in probe.get('sources', {}).items():
            for entry in entries:
                names = {'edge-models': ('Video model', '视频模型'), 'vdn-models': ('Decoder', '解码器'),
                         'models': ('Text encoder', '文本编码器'), 'pypi': ('Python packages', 'Python 依赖'),
                         'github': ('Tools', '安装工具'), 'git': ('Git', 'Git'), 'cuda': ('CUDA', 'CUDA')}
                if group.startswith('torch-'):
                    names[group] = ('GPU packages', 'GPU 依赖')
                route = self.t('direct', '直连') if entry.get('route') == 'direct' else self.t('current connection', '当前连接')
                speeds.append(dict(source=source_name(entry['id'], zh)+' · '+route, group=self.t(*names.get(group, (group, group))),
                    ok=entry.get('ok', False), rate=speed_text(entry, self.language.startswith('zh'))))
        # Only display fields from the update manifest, never access tokens.
        update = {k: update[k] for k in ('status', 'error', 'progress', 'candidate') if k in update}
        update['remind'] = bool(update.get('candidate') and not self.update_snoozed
                                and update['status'] in ('available', 'ready', 'error'))
        return dict(version=__version__, zh=self.language.startswith('zh'), form=dict(self.form),
            page=self.page, status=row.get('status', 'idle'), busy=self.controller.busy or self.importer.busy,
            offline=dict(progress_view(self.importer.state, zh), runtime=bool(self.form['offline_runtime']), models=len(self.form['offline_models'])),
            selected=bool(self.selected), error=error, notice=self.notice, compatibility=self.compatibility,
            report=dict(self.report),
            models=models, overall=overall, progress=progress, detail=clean(progress.get('detail', '')),
            progress_text=progress_text(progress, self.language.startswith('zh')),
            elapsed=duration(time.monotonic()-self.started) if self.started else '', summary=' · '.join(estimate),
            failure=launcher_failure(error, zh=self.language.startswith('zh')),
            source=source, source_name=source_name(source, zh), proxy_mode=preferences['proxy_mode'],
            probe=probe, speeds=speeds, token_set=bool(self.token),
            log=self.tail.text, logs=[dict(label=display(n, zh), path=str(p)) for n, p in self.controller.terminal_sources()],
            url=(self._browser_url(row.get('url', '')) if row.get('status') == 'open' else ''), shortcut=shortcut,
            can_shortcut=bool(self.controller.selection and self.controller.selection.get('ready')),
            update=update, engine_update_available=bool(row.get('engine_update_available')),
            settings_path=str(self.store.primary),
            review_id=row.get('plan', {}).get('plan_id', ''),
            consent=self.t('I accept the installation plan and model / toolkit licenses.', '我同意安装计划及模型／工具包许可证。'),
            portable=False, needs_consent=bool(getattr(self.controller, 'fixed_environment', False) is True
                and not (self.controller.root / 'engine/portable-consent.json').exists()),
            review=dict(engine=str(row.get('selection', {}).get('engine', self.form['engine'])),
                        comfy=str(row.get('selection', {}).get('root', self.form['comfy']))))
