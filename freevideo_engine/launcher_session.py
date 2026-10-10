"""Widget-independent state for the Qt launcher; uses the existing installer."""
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import __version__
from . import disk_space
from .comfy_launcher_runtime import Controller, layout, local_url, managed_frontend, new_layout
from .desktop_runtime import launcher_root, materialize_source
from .download_settings import Probe, read as download_preferences, speed_text
from .launcher_settings import Store, default_language
from .launcher_copy import size_text
from .launcher_terminal import Tail
from .model_status import FAMILIES, NAMES
from .model_guidance import package_instructions, video_instructions, runtime_packages_supported
from .setup_progress import progress_text
from .terminal_ui import clean, duration
from .model_upgrade import TARGET

# Background launcher release checks while the window stays open.
CHECK_SECONDS = 30 * 60
# "Later" hides one reminder for this long; a newer release reminds at once.
SNOOZE_SECONDS = 4 * 3600
QUEUE_POLL_SECONDS = 3
RECEIPT_POLL_SECONDS = 5


class Session:
    # Update flow defaults; __init__ starts each launcher session from these.
    update_snoozed = {}
    update_intent = None
    update_source = 'launcher'
    update_waiting = update_restarting = False
    engine_updating = engine_autoinstall = reload_expected = resume_pages = False
    browser_wait = queue_state = queue_thread = bridge = None
    foreign_state = foreign_thread = None
    clients_polled = queue_polled = foreign_polled = receipt_polled = resume_until = resume_polled = bridge_polled = 0.
    bridge_pending = False
    bridge_written = (0., None)
    update_checked = 0.
    page = 'comfy'
    selected = installed_versions = None
    release_details = None
    # The source whose earlier copies were removed once ComfyUI ran it.
    old_versions_retired = None

    def __init__(self, source=None, *, controller=None, store=None, updater=None, smoke=False):
        from .offline_packages import Importer
        from .installation_cleanup import Cleaner
        from .model_upgrade import ModelUpgrade
        self.cleaner = Cleaner()
        self.importer = Importer()
        self.imported_batch = None
        self.import_retry = None
        self.source = Path(source or materialize_source())
        self.controller = controller or Controller(self.source)
        self.upgrade = ModelUpgrade(self.source)
        self.model_switch = None
        self.store = store or Store(launcher_root())
        saved = self.store.read()
        home = Path(os.environ.get('USERPROFILE') or os.environ.get('HOME') or launcher_root().parent)
        self.form = dict(comfy='', destination=str(home / 'FreeVideo'), engine='', python='',
                         url='http://127.0.0.1:8188', models='', model_dirs=[], model_method='auto', environment_method='auto',
                         separate=False, repair=False, new_comfy=True, offline_runtime='', offline_models=[],
                         imported_archives=[], sampling_caches=not saved.get('installation'))
        self.form.update({k: v for k, v in saved.items() if k in self.form})
        if 'environment_method' not in saved and self.form['offline_runtime']:
            self.form['environment_method'] = 'manual'
        if self.form['model_method'] == 'reuse':
            self.form['model_method'] = 'manual' if self.form['offline_runtime'] else 'auto'
        if not runtime_packages_supported():
            self.form.update(environment_method='auto', offline_runtime='')
        self.form['engine'] = os.environ.get('FREEVIDEO_HOME') or self.form['engine']
        if saved.get('comfy') and 'new_comfy' not in saved:
            self.form['new_comfy'] = False
        self.language = saved['language'] if 'language' in saved else default_language()
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
        self.browser_url = None
        self.port_notice_url = None
        self.browser_error = ''
        # What the opened page reported about loading FreeVideo (web/health.js).
        from .page_check import PageCheck
        self.page_check = PageCheck()
        self.browser_opened = None
        self.page_error = ''
        self.started = None
        self.closing = False
        self.smoke = smoke
        self.reported_setup = None
        # An explicit update continues from tick() through update_intent:
        # 'check', 'launcher' or 'engine'. Other defaults are class attributes.
        self.update_snoozed = {}
        self.installed_versions = {}
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
        self.update_checked = time.monotonic()
        if self.updater and not smoke:
            # A restart from an explicit update finishes by updating the engine.
            # Check now so the engine reminder does not flash first, and keep
            # checking briefly in case the previous launcher writes late.
            self.resume_until = time.monotonic() + 20
            self._check_resumed(time.monotonic())
        if controller is None and not smoke:
            from .launcher_bridge import create
            try:
                self.bridge = create(launcher_root())
                self.controller.update_bridge = self.bridge
            except OSError:
                self.bridge = None
        if self.form['environment_method'] == 'manual' and self.form['offline_runtime'] and not controller:
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
        if key not in self.form or self.controller.busy or self.importer.busy or self.cleaner.busy:
            return
        if key in ('separate', 'repair', 'new_comfy', 'sampling_caches'):
            value = bool(value)
        if key == 'environment_method' and value not in ('auto', 'manual'):
            raise ValueError('Unknown environment installation method')
        if key == 'environment_method' and value == 'manual' and not runtime_packages_supported():
            system = 'Mac' if sys.platform == 'darwin' else 'Linux'
            raise ValueError(self.t('The %s environment is prepared automatically. Import model packages in the next step.' % system,
                                   '%s 运行环境由安装器自动准备，请在下一步导入模型包。' % system))
        if key == 'model_method' and value not in ('auto', 'manual', 'reuse'):
            raise ValueError('Unknown model download method')
        if self.form[key] == value:
            return
        self.form[key] = value
        if key in ('new_comfy', 'destination', 'comfy'):
            # A remembered installation's engine belongs to that installation;
            # another location installs into its own FreeVideo-engine.
            self.form['engine'] = os.environ.get('FREEVIDEO_HOME') or ''
        self.import_retry = None
        self.cleaner.plan = None
        self.cleaner.state = dict(status='idle', bytes=0, error='')
        self.controller.selection = None
        self.controller.state = dict(status='idle')
        self.browser_attempted = False
        self.port_notice_url = None
        self.browser_error = self.error = ''
        self.persist()

    def use_disk(self, folder):
        """Move a new installation to another disk, then show the location page to confirm it."""
        if not self.form['new_comfy'] or self.controller.busy or self.importer.busy or self.cleaner.busy:
            return
        self.edit('destination', str(folder))
        self.page = 'comfy'

    def disk_view(self, row, failure):
        """The disk under the chosen location, with other disks to move to (macOS only)."""
        if self.page != 'comfy' and not str(failure.get('kind', '')).startswith('disk'):
            return None
        need = sum(d.get('needed_bytes', 0) for d in row.get('disks', [])) if row.get('status') == 'review' else 0
        folder = self.form['destination'] if self.form['new_comfy'] else self.form['comfy']
        return disk_space.location(folder, need_bytes=need, suggest=self.form['new_comfy'])

    def add_folder(self, folder):
        from .local_models import library_roots
        # Saved folders that were deleted since are dropped instead of blocking the new one.
        kept = [p for p in self.form['model_dirs'] if Path(p).expanduser().is_dir()]
        self.edit('model_dirs', library_roots(kept + [folder]))

    def remove_folder(self, index):
        removed = self.form['model_dirs'][index]
        self.edit('model_dirs', [p for i, p in enumerate(self.form['model_dirs']) if i != index])
        self.edit('offline_models', [p for p in self.form['offline_models']
                                    if Path(p) / 'models' != Path(removed)])

    def clear_runtime(self):
        # Detach it from this plan; imported files remain available for reuse.
        self.edit('offline_runtime', '')
        self.edit('environment_method', 'auto')
        self.edit('model_method', 'auto')


    def import_packages(self, paths):
        if self.controller.busy or self.importer.busy or self.cleaner.busy:
            return
        if not paths:
            return
        destination = (Path(self.form['destination']).expanduser().resolve() if self.form['new_comfy']
                       else self.engine_root().parent)
        self.importer.start(paths, destination)
        self.import_retry = ('import', list(paths), destination)
        self.imported_batch = None
        self.error = ''

    def prepare_offline(self):
        """Models from imported packages, or downloaded into the imported environment.

        Imported model packages are always used; the download is for an
        environment imported without any.
        """
        if self.form['model_method'] == 'auto' and not self.form['offline_models']:
            self.importer.download(self.form['offline_runtime'], self.source, self.form['sampling_caches'])
        else:
            self.importer.prepare(self.form['offline_runtime'], self.form['offline_models'], self.source)

    def activate_offline(self, root):
        from .offline_packages import check_runtime_platform
        check_runtime_platform()
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
        if self.cleaner.busy:
            return
        # The model download holds the setup lease; launching and opening
        # FreeVideo continue, anything that would start setup waits for it.
        # Retrying a failed start only launches again.
        if (self.upgrade.active or self.model_switch) and (
                name == 'setup' or (name == 'retry' and self.retry_kind() != 'launch')
                or (name == 'primary' and self.page != 'launcher')):
            return
        if self.importer.busy:
            if name == 'stop':
                self.importer.cancelled.set()
            return
        if self.controller.busy:
            if name == 'stop':
                self.controller.cancel()
            return
        self.error = ''
        if name == 'repair':
            # One click instead of Settings › repair switch › Installation › Check:
            # plan a repair of this installation. The review installs it, and
            # setup first stops this launcher's own idle ComfyUI.
            if not self.can_repair():
                return
            self.persist()
            self.controller.run('inspect', dict(self.form, token=self.token, repair=True))
            self.page = 'progress'
            self.model_groups = []
            self.started = time.monotonic()
            return
        if name == 'retry':
            kind = self.retry_kind()
            if kind == 'import':
                _, paths, destination = self.import_retry
                self.importer.start(paths, destination)
                self.imported_batch = None
            elif kind == 'prepare':
                self.prepare_offline()
                self.imported_batch = None
            elif kind == 'launch':
                self.action('launch', accepted)
            elif kind in ('check', 'install'):
                # Recompute the plan and live disk space; never install a stale
                # failed review or discard completed/resumable downloads.
                self.persist()
                self.controller.run('inspect', dict(self.form, token=self.token))
                self.page = 'progress'
                self.started = time.monotonic()
            return
        if name == 'setup':
            if getattr(self.controller, 'fixed_environment', False) is True:
                self.controller.close()
                self.controller = Controller(self.source)
                self.controller.update_bridge = self.bridge
            self.page = 'comfy'; return
        if name == 'launcher' and self.selected:
            if not self.controller.restore({'installation': self.selected}):
                raise ValueError(self.t('Locate or repair this installation in Setup.', '请在安装设置中重新定位或修复这份安装。'))
            self.page = 'launcher'
            return
        if name == 'update-engine' and self.selected:
            self.update_intent, self.update_source = 'engine', 'launcher'
            self.queue_state = None
            self._update_engine_when_idle()
            return
        if name == 'back':
            self.page = 'comfy' if self.page == 'models' else 'models'
            return
        if name == 'browser':
            self.open_browser(); return
        if name == 'delete_archives':
            self.delete_archives(); return
        if name == 'shortcut':
            self.controller.run('shortcut', self.controller.state.get('status')); return
        if name not in ('primary', 'launch'):
            return
        state = self.controller.state.get('status')
        if (self.page == 'models' and self.form['new_comfy']
                and self.form['environment_method'] == 'manual' and self.form['offline_runtime']):
            self.import_retry = ('prepare',)
            self.prepare_offline()
            return
        if self.page == 'comfy':
            if self.form['new_comfy'] and self.form['environment_method'] == 'manual' and not self.form['offline_runtime']:
                raise ValueError(self.t('Import the Environment ZIP, or choose Automatic installation.',
                                       '请导入运行环境包，或选择「自动安装」。'))
            new_layout(self.form['destination']) if self.form['new_comfy'] else layout(self.form['comfy'])
            self.persist(); self.page = 'models'; return
        self.browser_attempted = False
        self.port_notice_url = None
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

    def can_repair(self):
        """An installed engine this launcher can check and reinstall again."""
        return (bool(self.selected) and getattr(self.controller, 'fixed_environment', False) is not True
                and not (self.controller.busy or self.importer.busy or self.cleaner.busy or self.closing
                         or self.engine_updating or self.upgrade.active or self.model_switch))

    def repairs_comfy(self):
        """ComfyUI runs in the environment this launcher made, so Repair reinstalls its packages."""
        selected = self.controller.selection or self.selected
        if not isinstance(selected, dict) or not selected.get('python') or not selected.get('engine'):
            return False
        try:
            return managed_frontend(selected)
        except (OSError, TypeError, ValueError):
            return False

    def retry_kind(self):
        if self.importer.state.get('status') == 'error' and self.import_retry:
            return self.import_retry[0]
        row = self.controller.state
        if self.page == 'progress' and row.get('status') == 'review' and row.get('errors'):
            return 'check'
        if row.get('status') in ('failed', 'cancelled'):
            if self.page == 'launcher':
                return 'launch'
            if self.page == 'progress':
                return 'install'
        return ''

    def cleanup_downloads(self, remove=False):
        if (self.controller.busy or self.importer.busy or self.closing or self.upgrade.active or self.model_switch
                or self.update_intent or self.engine_updating or self.updater and self.updater.busy):
            return
        self.cleaner.start(self.engine_root(), remove=remove)

    def upgrade_model(self):
        """The user's one consent: download int8 beside the current model, switch, then release the old one.

        After a failure only the step that failed runs again: a stopped download,
        or a switch that failed before setup started, is offered afresh (files
        already fetched count); a failed release retries the release. A switch
        that failed during setup is retried from the setup page, which restores
        the previous model. An old model left after the switch is released here
        too, when the user asks.
        """
        state = self.upgrade.state
        if (state.get('status') not in ('available', 'failed', 'releasable') or self.upgrade.busy or self.model_switch
                or self.closing or self.controller.busy or self.importer.busy or self.cleaner.busy or self.engine_updating):
            return
        root = self.engine_root()
        # Started from Settings after "Later": show the card again.
        self.upgrade.state = state = dict(state, dismissed=False)
        if state.get('status') == 'releasable' or state.get('status') == 'failed' and state.get('step') == 'release':
            # After the switch the int8 model must be in use; files offered on
            # their own name the variant they keep.
            self.upgrade.release(root, state.get('in_use') or TARGET)
            return
        if state.get('status') == 'failed':
            # A switch that failed after setup started leaves the installation
            # unfinished; Retry on the setup page restores it first.
            if state.get('step') not in ('download', 'switch') or state.get('step') == 'switch' and not state.get('kept'):
                return
            from .model_upgrade import offer
            state = offer(root)
            self.upgrade.state = state
            if state.get('status') != 'available':
                return
        from .desktop_runtime import materialize_source
        self.upgrade.source = materialize_source(self.source, root / 'launcher' / 'source')
        self.upgrade.download(root, state)

    def cancel_model_upgrade(self):
        self.upgrade.cancel()

    def dismiss_model_upgrade(self):
        # Hides the card for this session; Settings → Storage keeps the entry.
        if not self.upgrade.busy and not self.model_switch:
            self.upgrade.state = dict(self.upgrade.state, dismissed=True)

    def _tick_model_upgrade(self, row, busy):
        upgrade = self.upgrade
        state = upgrade.state
        if self.closing or upgrade.busy:
            return
        if (state.get('status') == 'idle' and self.page == 'launcher' and not busy
                and row.get('status') in ('open', 'ready')):
            try:
                upgrade.inspect(self.engine_root())
            except ValueError:
                pass
            return
        if (state.get('status') == 'unavailable' and state.get('reason') == 'int8-kernels'
                and self.page == 'launcher' and not busy):
            # A failed int8 probe is final until the GPU checks run again (a
            # setup or repair, for example after a driver update).
            now = time.monotonic()
            if now - self.receipt_polled >= RECEIPT_POLL_SECONDS:
                self.receipt_polled = now
                from .model_upgrade import kernel_receipt
                try:
                    changed = kernel_receipt(self.engine_root()) != state.get('receipt')
                except ValueError:
                    changed = False
                if changed:
                    upgrade.inspect(self.engine_root())
            return
        if state.get('status') == 'downloaded' and self.model_switch is None:
            # Switch only between videos, as an engine update does.
            if busy or self.engine_updating or self._queue_busy() is not False:
                return
            # The switch restarts our ComfyUI so its resident worker lets go of
            # the old model and the GPU packages setup reinstalls. A ComfyUI
            # this launcher did not start cannot be restarted: wait until the
            # user closes it.
            foreign = self._foreign_server()
            if foreign is not False:
                if foreign:
                    upgrade.waiting_for('comfy')
                return
            self.model_switch = 'inspect'
            upgrade.switching()
            self.controller.run('inspect', dict(self.form, token=self.token, repair=True, prepared_format='int8'))
            self.page = 'progress'
            self.model_groups = []
            self.started = time.monotonic()
            return
        if self.model_switch == 'inspect' and not busy and row.get('action') == 'inspect' and row.get('status') != 'running':
            plan = row.get('plan') or {}
            if (row.get('status') == 'review' and not row.get('errors') and plan.get('prepared_format') == 'int8_convrot'
                    and plan.get('model_download_bytes', 1 << 62) <= 64 * (1 << 20)):
                # Everything was downloaded and verified beside the old model;
                # the user agreed to this switch when starting the upgrade.
                self.model_switch = 'install'
                self.controller.run('install', True)
            else:
                # Only a plan was made; machine.json is unchanged.
                self.model_switch = None
                upgrade.switch_failed('\n'.join(row.get('errors') or []) or row.get('error') or
                                      self.t('The switch needs more files than were downloaded; check again.',
                                             '切换还需要未下载的文件，请重新检查。'), kept=True)
            return
        if self.model_switch == 'install' and not busy and row.get('action') == 'install' and row.get('status') != 'running':
            self.model_switch = None
            if row.get('status') in ('open', 'restart-required') or row.get('deployed'):
                upgrade.release(self.engine_root())
            else:
                # Setup marks the installation unfinished when it starts; then
                # Retry on the setup page restores the previous model.
                upgrade.switch_failed(row.get('error') or self.t('The switch stopped.', '切换未完成。'),
                                      kept=self._installation_ready())

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
        self.browser_url = self.controller.state['url']
        address = self._browser_url(self.browser_url)
        moved_from = self.controller.state.get('moved_from')
        if moved_from and self.port_notice_url != self.browser_url:
            parsed = urlsplit(address)
            query = parse_qsl(parsed.query, keep_blank_values=True)
            query.append(('freevideo_port_from', moved_from))
            address = urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                                 urlencode(query), parsed.fragment))
        # ComfyUI itself may follow the browser's language, but the FreeVideo
        # panels need an explicit choice when the launcher language differs
        # from the operating system. Keep the controller's canonical URL
        # unchanged so saved installations and existing integrations remain
        # compatible; only the browser hint is added at launch time.
        try:
            if not open_browser(address):
                raise OSError('The system did not accept the browser request')
            if moved_from:
                self.port_notice_url = self.browser_url
            self.browser_error = ''
            self.browser_opened = time.monotonic()
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

    def engine_update_pending(self):
        """This launcher carries a newer engine than the installed one.

        An older launcher opened later would otherwise offer a downgrade."""
        if not (self.selected and self.controller.state.get('engine_update_available')):
            return False
        bundled = self.source_stamp(self.source)[1]
        installed = self.source_stamp(self.selected.get('source'))[1]
        return not (bundled and installed and installed > bundled)

    def update_key(self):
        """The current reminder: a newer launcher first, then the bundled engine."""
        row = self.updater.state if self.updater else {}
        if row.get('candidate') and row.get('status') in ('available', 'downloading', 'ready', 'error', 'cancelled'):
            return 'launcher:' + str(row['candidate'].get('revision', ''))
        if self.engine_update_pending():
            return 'engine:' + __version__
        return ''

    def dismiss_update(self):
        key = self.update_key()
        if key:
            self.update_snoozed = dict(self.update_snoozed, **{key: time.monotonic() + SNOOZE_SECONDS})
        self.update_intent = None
        self.update_waiting = False
        if self.updater and self.updater.state['status'] == 'downloading':
            self.updater.cancelled.set()

    def _unsnooze(self):
        key = self.update_key()
        self.update_snoozed = {k: v for k, v in self.update_snoozed.items() if k != key}

    def check_update(self, token=''):
        if self.updater and not self.updater.busy:
            if token.strip():
                self.updater.token = token.strip()
            self.update_checked = time.monotonic()
            self.updater.run('check')

    def update(self, token='', source='launcher'):
        """One explicit update: the newest launcher if one is known, otherwise
        the engine bundled with this launcher. Downloading, waiting for running
        ComfyUI jobs and restarting continue from tick()."""
        if self.controller.busy or self.importer.busy or self.cleaner.busy or self.closing:
            if source == 'browser':
                self.bridge_pending = True
            return
        if self.updater and token.strip():
            self.updater.token = token.strip()
        self.update_source = source
        self.queue_state = None
        row = self.updater.state if self.updater else {}
        if self.updater and row.get('candidate') and row.get('status') != 'checking':
            if self.updater.current.get('packaging') in ('onedir', 'app'):
                from .launcher_update import release_page
                from .windows_ux import open_browser
                self.update_intent = None
                open_browser(release_page(self.updater.current))
                return
            self._unsnooze()
            self.update_intent = 'launcher'
            if row['status'] == 'ready':
                self._restart_when_idle()
            elif not self.updater.busy:
                self.updater.run('download', row['candidate'])
        elif self.engine_update_pending():
            self._unsnooze()
            self.update_intent = 'engine'
            self._update_engine_when_idle()
        elif self.updater and not self.updater.busy:
            self.update_intent = 'check'
            self.update_checked = time.monotonic()
            self.updater.run('check')

    def _queue_busy(self):
        """True or False once known for a server this launcher owns, None before."""
        owns = getattr(self.controller, 'owns_server', None)
        if not callable(owns) or owns() is not True:
            return False
        now = time.monotonic()
        if (self.queue_thread is None or not self.queue_thread.is_alive()) and (
                self.queue_state is None or now - self.queue_polled >= QUEUE_POLL_SECONDS):
            from .comfy_launcher_runtime import queue_busy
            url = (self.controller.selection or self.selected or {}).get('url') or self.form['url']
            self.queue_polled = now
            def poll():
                # Off the UI thread: a stalled server must not freeze the window.
                self.queue_state = queue_busy(url)
            self.queue_thread = threading.Thread(target=poll, name='freevideo-queue', daemon=True)
            self.queue_thread.start()
        return self.queue_state

    def _foreign_server(self):
        """Whether a ComfyUI this launcher did not start answers at the address; None before known."""
        owns = getattr(self.controller, 'owns_server', None)
        if callable(owns) and owns() is True:
            return False
        now = time.monotonic()
        if (self.foreign_thread is None or not self.foreign_thread.is_alive()) and (
                self.foreign_state is None or now - self.foreign_polled >= QUEUE_POLL_SECONDS):
            from .comfy_launcher_runtime import launcher_comfy, server_info
            selected = dict(self.controller.selection or self.selected or {})
            url = selected.get('url') or self.form['url']
            self.foreign_polled = now
            def poll():
                # Off the UI thread, like the queue poll. Another program's
                # server at the address of a ComfyUI the launcher downloaded is
                # not this installation: that ComfyUI moves to a free port.
                info = server_info(url)
                self.foreign_state = info.get('status') != 'offline' and not (
                    selected.get('root') and selected.get('engine') and launcher_comfy(selected, info))
            self.foreign_thread = threading.Thread(target=poll, name='freevideo-server', daemon=True)
            self.foreign_thread.start()
        return self.foreign_state

    def _installation_ready(self):
        try:
            return json.loads((self.engine_root() / 'machine.json').read_text(encoding='utf-8')).get('ready') is True
        except (OSError, ValueError, AttributeError):
            return False

    def _restart_when_idle(self):
        row = self.updater.state
        if row.get('status') != 'ready' or self.update_restarting:
            return
        busy = self._queue_busy()
        self.update_waiting = bool(busy)
        if busy is not False:
            return
        from .launcher_update import DownloadedLauncherUnavailable, handoff, launch_download
        self.persist()
        try:
            handoff(row['candidate'], self.updater.root, self.update_source,
                    pages=self.controller.state.get('status') == 'open')
            launch_download(row['candidate'], self.updater.root, token=self.updater.token)
        except DownloadedLauncherUnavailable:
            self.error = ''
            self.updater.run('download', row['candidate'])
            return
        except Exception:
            self.update_intent = None
            raise
        self.update_restarting = True
        self._write_bridge_status(force=True)
        self.closing = True

    def _update_engine_when_idle(self):
        if self.controller.busy or self.importer.busy or self.upgrade.active or self.model_switch:
            return
        busy = self._queue_busy()
        self.update_waiting = bool(busy)
        if busy is not False:
            return
        self.update_intent = None
        self.engine_updating = self.engine_autoinstall = True
        # Open pages reload themselves after the restart; without any, open
        # the browser as soon as ComfyUI is ready.
        self.reload_expected = (self.update_source == 'browser' or self.resume_pages
                                or self.controller.state.get('status') == 'open')
        self.browser_attempted = False
        self.port_notice_url = None
        self.browser_wait = None
        self.persist()
        self.controller.run('inspect', dict(self.form, token=self.token))
        self.page = 'progress'
        self.model_groups = []
        self.started = time.monotonic()

    def _tick_updates(self):
        now = time.monotonic()
        updater = self.updater
        if (updater and self.update_intent is None and not updater.busy and now - self.update_checked >= CHECK_SECONDS
                and updater.state.get('status') in ('idle', 'current', 'available', 'error', 'cancelled')):
            self.update_checked = now
            updater.run('check', background=True)
        if self.resume_until:
            self._check_resumed(now)
        if self.bridge and now - self.bridge_polled >= 1:
            self.bridge_polled = now
            from .launcher_bridge import take_request
            request = take_request(self.bridge)
            if request and request['action'] == 'cancel':
                self.bridge_pending = False
                if self.update_intent:
                    self.dismiss_update()
            elif request or (self.bridge_pending and not self.controller.busy):
                self.bridge_pending = False
                self.update(source='browser')
        row = updater.state if updater else {}
        if self.update_intent == 'check' and updater and not updater.busy:
            self.update_intent = None
            if row.get('candidate') or self.engine_update_pending():
                self.update(source=self.update_source)
        elif self.update_intent == 'launcher' and updater and not updater.busy:
            if row.get('status') == 'ready':
                self._restart_when_idle()
            elif row.get('status') != 'downloading':
                self.update_intent, self.update_waiting = None, False
        elif self.update_intent == 'engine':
            self._update_engine_when_idle()
        self._write_bridge_status()

    def _check_resumed(self, now):
        if now >= self.resume_until or self.update_intent:
            self.resume_until = 0.
        elif self.engine_update_pending() and now - self.resume_polled >= 1:
            self.resume_polled = now
            from .launcher_update import resumed_update
            resumed = resumed_update(self.updater.current, self.updater.root)
            if resumed:
                self.resume_until = 0.
                self.update_intent, self.update_source = 'engine', resumed['source']
                self.resume_pages = resumed.get('pages', False)

    def _clients_connected(self):
        """A FreeVideo page reconnected to the restarted server."""
        now = time.monotonic()
        if now - self.clients_polled < 1:
            return False
        self.clients_polled = now
        from .comfy_launcher_runtime import get_json
        try:
            return get_json(self.controller.state['url'].split('/?')[0] + '/freevideo/updates', timeout=1).get('clients', 0) > 0
        except (OSError, ValueError, KeyError, AttributeError):
            return False

    def update_phase(self):
        row = self.updater.state if self.updater else {}
        if self.update_restarting:
            return 'restarting'
        if self.engine_updating:
            return 'engine'
        if self.update_waiting:
            return 'waiting'
        if row.get('status') == 'downloading':
            return 'downloading'
        if self.update_intent and row.get('status') == 'checking':
            return 'checking'
        if self.selected and self.page == 'progress' and self.controller.state.get('status') == 'review':
            return 'review'
        return ''

    def model_phase(self):
        """For open pages: switching to the int8 model restarts our ComfyUI once."""
        if self.model_switch:
            return 'switching'
        state = self.upgrade.state
        if state.get('status') == 'downloaded' and not state.get('waiting'):
            return 'waiting'
        return ''

    def source_stamp(self, source):
        """(version, built_at) of an engine source; a source checkout has neither."""
        if not source:
            return '', 0
        if self.installed_versions is None:
            self.installed_versions = {}
        if str(source) not in self.installed_versions:
            package = Path(source) / 'freevideo_engine'
            try:
                identity = json.loads((package / 'build-identity.json').read_text(encoding='utf-8'))
                stamp = str(identity['version']), int(identity['built_at'])
            except (OSError, ValueError, KeyError, TypeError):
                try:
                    stamp = (package / 'build-version.txt').read_text(encoding='utf-8').strip(), 0
                except OSError:
                    stamp = Path(source).name.split('-')[0], 0
            self.installed_versions[str(source)] = stamp
        return self.installed_versions[str(source)]

    def installed_version(self):
        return self.source_stamp((self.selected or {}).get('source'))[0]

    def installed_product_version(self):
        """The product version of the engine the selected installation runs, if it says."""
        source = (self.selected or {}).get('source')
        try:
            value = json.loads((Path(source) / 'freevideo_engine/release_notes.json').read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return None
        return value.get('product_version') if isinstance(value, dict) else None

    def update_view(self):
        from .release_notes import installed_details, public_details
        if self.release_details is None:
            self.release_details = installed_details(Path(__file__).parent, __version__)
        row = dict(self.updater.state) if self.updater else dict(status='development')
        # Only display fields from the update manifest, never access tokens.
        view = {k: row[k] for k in ('status', 'error', 'progress', 'candidate') if k in row}
        key = self.update_key()
        view.update(engine=self.engine_update_pending(), current=__version__,
                    current_release=public_details(self.updater.current) if self.updater else self.release_details,
                    installed=self.installed_version(), phase=self.update_phase(), key=key,
                    channel=self.updater.current.get('channel') if self.updater else None,
                    track=self.updater.current.get('track', 'stable') if self.updater else 'stable',
                    manual=bool(self.updater and self.updater.current.get('packaging') in ('onedir', 'app')))
        # Versions between the one in use and the update, so skipped notes are not lost.
        from .release_notes import since
        current = view['current_release'] or {}
        view['candidate_earlier'] = since(view.get('candidate'), current.get('product_version')) if view.get('candidate') else []
        view['engine_earlier'] = since(current, self.installed_product_version()) if view['engine'] else []
        due = ((view.get('candidate') and row['status'] in ('available', 'ready', 'error'))
               or (view['engine'] and self.page == 'launcher'))
        view['remind'] = bool(key and due and self.update_snoozed.get(key, 0) <= time.monotonic()
                              and not self.update_intent and not view['phase'])
        return view

    def _write_bridge_status(self, force=False):
        if not self.bridge:
            return
        view = self.update_view()
        from .release_notes import public_details
        candidate = view.get('candidate') or {}
        value = dict(version=__version__, phase=view['phase'], model=self.model_phase(), status=view.get('status'),
                     progress=view.get('progress'), manual=view['manual'], channel=view['channel'], track=view['track'],
                     candidate=public_details(candidate) if candidate.get('version') else None,
                     engine=dict(view['current_release'], pending=view['engine'], installed=view['installed']),
                     error=str(view.get('error') or '')[:500])
        now = time.monotonic()
        last, previous = self.bridge_written
        if not force and value == previous and now - last < 5:
            return
        from .launcher_bridge import write_status
        try:
            write_status(self.bridge, value)
            self.bridge_written = (now, value)
        except OSError:
            pass

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
            self.upgrade.close()
            self.controller.close()
            from .launcher_bridge import remove
            remove(self.bridge)
            self._closed = True

    def tick(self):
        imported = self.importer.state
        if not self.importer.busy and imported is not self.imported_batch:
            self.imported_batch = imported
            retry = self.import_retry
            for package in imported.get('packages', []):
                if package.get('archive'):
                    # The user's own ZIP: offered for deletion once the installation holds its contents.
                    record = dict(path=package['archive'], bytes=package['archive_bytes'],
                                  mtime_ns=package['archive_mtime_ns'], kind=package['kind'], root=package['root'])
                    self.form['imported_archives'] = [r for r in self.form['imported_archives']
                                                      if r.get('path') != record['path']] + [record]
                    self.archive_view = None
                if package['kind'] == 'runtime':
                    self.edit('offline_runtime', package['root'])
                    self.edit('environment_method', 'manual')
                else:
                    roots = self.form['offline_models']
                    if package['root'] not in roots:
                        self.form['offline_models'] = roots + [package['root']]
                        self.add_folder(str(Path(package['root']) / 'models'))
            if imported.get('packages'):
                self.edit('model_method', 'manual')
                self.persist()
            if imported.get('runtime_skipped') and not any(p['kind'] == 'runtime' for p in imported.get('packages', [])):
                # The runtime installs automatically; imported model packages still feed it.
                self.edit('offline_runtime', '')
                self.edit('environment_method', 'auto')
            if imported['status'] == 'error':
                self.import_retry = retry
                self.error = imported['error']
            elif imported['status'] == 'prepared':
                self.activate_offline(imported['ready_root'])

        row = self.controller.state
        busy = self.controller.busy
        selection = getattr(self.controller, 'selection', None)
        if (row.get('status') == 'open' and not busy and not self.smoke and selection
                and selection.get('source') != self.old_versions_retired):
            # ComfyUI runs this version now: earlier FreeVideo copies are no longer read.
            self.old_versions_retired = selection.get('source')
            retire = getattr(self.controller, 'retire_old_versions', None)
            if retire:
                retire(dict(selection))
        if self.engine_autoinstall and not busy and row.get('action') == 'inspect' and row.get('status') != 'running':
            self.engine_autoinstall = False
            selection = row.get('selection') or {}
            if (row.get('status') == 'review' and selection.get('ready') and not row.get('errors')
                    and (row.get('host') or {}).get('ready')):
                # Nothing new to download or approve: deploy, then restart ComfyUI.
                self.controller.run('install', True)
                row, busy = self.controller.state, self.controller.busy
        if self.engine_updating and not busy and not self.engine_autoinstall:
            self.engine_updating = False
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
        if (not busy and row.get('status') == 'open' and row.get('moved_from')
                and row.get('url') != self.browser_url):
            self.browser_attempted = False
            self.reload_expected = False
        if not busy and not self.closing and row.get('status') == 'open' and not self.browser_attempted:
            # After an update, open pages reload themselves; open a new tab
            # only if none of them returns.
            if self.reload_expected and self.browser_wait is None:
                self.browser_wait = time.monotonic() + 10
            if self.reload_expected and self._clients_connected():
                self.browser_attempted = True
                self.reload_expected = False
            elif not self.reload_expected or time.monotonic() >= self.browser_wait:
                self.reload_expected = False
                self.open_browser()
        self._tick_page_check(row)
        self._tick_model_upgrade(row, busy)
        sources = self.controller.terminal_sources()
        if sources:
            selected = next((p for _, p in sources if p == self.log_source), sources[-1][1])
            self.tail.select(selected)
            self.tail.read(final=not self.controller.terminal_running(selected))
        self._tick_updates()

    def _tick_page_check(self, row):
        """Show a page that failed to load FreeVideo, or never reported, in the failure card."""
        if row.get('status') != 'open' or self.closing or self.smoke:
            self.page_check.reset()
            self.browser_opened, self.page_error = None, ''
            return
        address = row['url'].split('/?')[0]
        self.page_check.poll(address)
        self.page_error = self.page_check.text(self.t, self.browser_opened, address)

    def old_versions_text(self):
        """What the background removal of earlier FreeVideo copies freed this session."""
        value = getattr(self.controller, 'old_versions', None)
        released = value.get('released_bytes') if isinstance(value, dict) else None
        if isinstance(released, int):
            # PyTorch wheels a fresh installation downloaded are not an earlier version.
            downloads = value.get('download_bytes')
            released -= downloads if isinstance(downloads, int) else 0
        if not isinstance(released, int) or released < 2**20:
            return ''
        kinds = {row.get('kind') for row in value.get('removed', []) if isinstance(row, dict)}
        if kinds and kinds <= {'offline-package', 'download-leftover', 'torch-wheel'}:
            # Only this installation's own extractions and download leftovers: not an earlier version.
            return self.t('Temporary setup files removed · %s freed', '已清理安装时的临时文件 · 释放 %s') % size_text(released)
        return self.t('Old versions removed · %s freed', '已清理旧版本 · 释放 %s') % size_text(released)

    def copied_models_text(self):
        """Models copied from a library the installation could not link to, with what would save the space."""
        value = getattr(self.controller, 'old_versions', None)
        copied = value.get('copied_model_bytes') if isinstance(value, dict) else None
        if not isinstance(copied, int) or copied < 2**30:
            return ''
        if value.get('copied_model_reason') == 'exfat':
            return self.t('This disk uses exFAT, so %s of models had to be copied instead of shared with your model library.',
                          '这块硬盘是 exFAT 格式，有 %s 的模型只能复制，不能和模型库共用。') % size_text(copied)
        return self.t('%s of models were copied from another disk. Keep your model library on the same disk as FreeVideo '
                      'to avoid this.',
                      '有 %s 的模型是从另一块硬盘复制来的。把模型库和 FreeVideo 放在同一块硬盘上，就不会多占这部分空间。') % size_text(copied)

    def archive_offer(self):
        """The imported ZIPs whose contents the running installation holds, verified."""
        if self.controller.state.get('status') != 'open' or not self.form.get('imported_archives'):
            return None
        engine = (self.controller.selection or {}).get('engine')
        if not engine:
            return None
        def identity(path):
            try:
                info = os.stat(path)
                return path, info.st_size, info.st_mtime_ns
            except OSError:
                return path, None, None
        # A ZIP moved, changed or deleted while the launcher is open is noticed on the next view.
        key = (engine, tuple(identity(r['path']) for r in self.form['imported_archives']))
        if getattr(self, 'archive_view', None) is None or self.archive_view[0] != key:
            from .package_cleanup import archives
            self.archive_view = (key, archives(self.form['imported_archives'], engine))
        return self.archive_view[1]

    def delete_archives(self):
        from .package_cleanup import delete_archives
        engine = (self.controller.selection or {}).get('engine')
        if self.controller.state.get('status') != 'open' or not engine:
            return
        offered = len((self.archive_offer() or {}).get('paths', []))
        freed, removed = delete_archives(self.form['imported_archives'], engine)
        self.archives_failed = max(0, offered - len(removed))
        self.form['imported_archives'] = [r for r in self.form['imported_archives'] if r['path'] not in removed]
        self.archive_view = None
        self.archives_freed = (getattr(self, 'archives_freed', 0) or 0) + freed
        self.persist()

    def archives_text(self):
        offer = self.archive_offer()
        if offer and offer['bytes'] >= 2**20:
            return self.t('The offline package ZIP files you imported still take %s. FreeVideo no longer needs them.',
                          '导入用过的离线包压缩包还占着 %s，FreeVideo 已经用不到它们。') % size_text(offer['bytes'])
        return ''

    def archives_confirm_text(self):
        offer = self.archive_offer()
        if not offer or offer['bytes'] < 2**20:
            return ''
        count = len(offer['paths'])
        english = 'Delete 1 ZIP file (%s)? This cannot be undone.' if count == 1 else 'Delete %d ZIP files (%%s)? This cannot be undone.' % count
        return self.t(english % size_text(offer['bytes']), '删除 %d 个压缩包（%s）？删除后不能恢复。' % (count, size_text(offer['bytes'])))

    def archives_done_text(self):
        freed = getattr(self, 'archives_freed', 0) or 0
        return self.t('ZIP files deleted · %s freed', '已删除压缩包 · 释放 %s') % size_text(freed) if freed >= 2**20 else ''

    def archives_failed_text(self):
        failed = getattr(self, 'archives_failed', 0) or 0
        if not failed:
            return ''
        return self.t('1 ZIP file could not be deleted.' if failed == 1 else '%d ZIP files could not be deleted.' % failed,
                      '%d 个压缩包没能删除。' % failed)

    def snapshot(self):
        from .failure_details import REPAIR_KINDS, launcher_failure, redacted_launcher_error
        from .launcher_copy import display, progress_view, source_name
        zh = self.language.startswith('zh')
        row = self.controller.state
        task = row.get('task', {})
        port_from = int(row['moved_from']) if row.get('status') == 'open' and 'moved_from' in row else 0
        port_to = (urlsplit(row['url']).port or 80) if port_from else 0
        progress = progress_view(task.get('progress') or {}, zh)
        overall = progress_view(row.get('overall') or task.get('phase_progress') or {}, zh)
        if self.importer.busy:
            # The sidebar's Working card follows the offline import, not the idle installer.
            state = self.importer.state
            if state.get('status') == 'downloading':
                label = self.t('Downloading models', '下载模型')
            elif state.get('status') == 'preparing':
                label = self.t('Checking packages', '检查配套包')
            else:
                label = self.t('Importing offline packages', '导入离线包')
                if (state.get('count') or 0) > 1:
                    label += ' %d/%d' % (state.get('index') or 1, state['count'])
                if state.get('detail'):
                    label += ' · ' + display(state['detail'], zh)
            overall = dict(label=label, done=state.get('done'), total=state.get('total'))
        errors = [self.error, self.browser_error, self.page_error, row.get('error', ''), *row.get('errors', [])]
        shortcut = row.get('shortcut') or {}
        if shortcut.get('status') == 'failed':
            errors.append(shortcut.get('error_zh' if zh else 'error') or shortcut.get('error', ''))
        error = redacted_launcher_error('\n'.join(str(e) for e in errors if e),
                                        [(self.token, '<REDACTED>')] if self.token else [])
        by_id = {r['id']: r for r in self.model_groups}
        ready = bool(row.get('selection', {}).get('ready') or row.get('status') == 'open')
        models = []
        states = {'ready': ('Ready', '已就绪'), 'done': ('Done', '已完成'), 'waiting': ('Waiting for scan', '等待检查'),
                  'pending': ('Download needed', '需要下载'), 'replace': ('Needs replacing', '需要替换'),
                  'queued': ('Waiting to download', '等待下载'), 'downloading': ('Downloading', '正在下载'),
                  'verifying': ('Downloaded; verifying', '已下载，正在校验'),
                  'paused': ('Paused', '已暂停')}
        review = row.get('status') == 'review'
        for name in FAMILIES:
            if name == 'sampling' and name not in by_id and not self.form['sampling_caches']:
                continue
            item = dict(by_id.get(name, {}))
            state = 'ready' if ready else item.get('state', 'waiting')
            if state == 'pending':
                # Part of the group is ready; the rest (often small config files) waits for a download slot.
                state = 'queued'
            elif state == 'waiting' and item.get('download_bytes'):
                state = 'pending' if review else 'queued'
            elif state == 'waiting' and 0 < item.get('total_bytes', 0) <= item.get('verified_bytes', 0):
                # Every file of this group is here and already verified: nothing is left to check.
                # Files that only match in size still wait for their integrity check.
                state = 'ready'
            if state == 'ready' and item.get('download_bytes') and not review:
                state = 'done'  # Downloaded during this installation.
            total = item.get('total_bytes', 0)
            done = total if state == 'ready' else min(total,
                    item.get('verified_bytes', 0)+item.get('downloaded_bytes', 0))
            models.append(dict(id=name, title=self.t(*NAMES[name]), state=state,
                detail=self.t(*states.get(state, states['waiting'])), done=done, total=total,
                found=item.get('existing_bytes', 0), download=item.get('download_bytes', 0),
                rate='%.1f MiB/s' % (item['bytes_per_second']/2**20) if item.get('bytes_per_second') else ''))
        # A model download names its group, so it shows the group's download, as in the plan and the rows below.
        groups = {self.t(*NAMES[group['id']]): group for group in self.model_groups
                  if group.get('id') in NAMES and group.get('download_bytes')}
        current = progress
        if progress.get('activity') == 'download' and progress.get('name') in groups:
            group = groups[progress['name']]
            current = dict(progress, unit='bytes', total=group['download_bytes'],
                           done=min(group['download_bytes'], group.get('downloaded_bytes') or 0),
                           rate=group.get('bytes_per_second') or progress.get('rate'))
        file_text = progress_text(current, zh, decimal_sizes=sys.platform == 'darwin')
        # The time row's estimate: only while downloading, from what is left at the current speed.
        from .setup_progress import number
        left, rate = (current.get('total') or 0) - (current.get('done') or 0), current.get('rate')
        remaining = (left / rate if current.get('activity') == 'download' and current.get('unit') == 'bytes'
                     and number(rate) and rate > 0 and number(left) and left > 0 else None)
        try:
            preferences = download_preferences(self.engine_root() / 'download-settings.json')
        except (OSError, ValueError):
            preferences = dict(source='auto', proxy_mode='auto')
        source = preferences['source']
        plan = row.get('plan', {})
        estimate = []
        if plan.get('disk_mode') == 'extreme':
            estimate.append(self.t('Space saver (automatic)', '极限省空间（自动启用）'))
        gpu = plan.get('inventory', {}).get('hardware', {}).get('gpu_name')
        if gpu:
            estimate.append(gpu)
        if 'model_download_bytes' in plan:
            from .bootstrap import download_bytes
            estimate.append(self.t('Download ', '需下载 ')+disk_space.size_text(download_bytes(plan)))
        if row.get('disks'):
            estimate.append(self.t('Peak disk ~', '磁盘峰值约 ')+disk_space.size_text(sum(d.get('needed_bytes', 0) for d in row['disks'])))
        update = self.update_view()
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
        from .sampling_assets import total_bytes
        failure = launcher_failure(error, zh=self.language.startswith('zh'), can_repair=self.can_repair(),
                                   repairs_comfy=self.repairs_comfy())
        disk = self.disk_view(row, failure)
        if disk and str(failure.get('kind', '')).startswith('disk'):
            failure = launcher_failure(error, zh=self.language.startswith('zh'),
                other_disk=self.form['new_comfy'] and any(d['enough'] for d in disk['others']),
                disk_name=disk['name'] if disk['problem'] == 'disconnected' else '')
        # A problem card comes first: no update window opens over it (one already open closes
        # without counting as "Later", and reminds again once the card is gone). Repair installs
        # the launcher's own engine as it sets up again, so with Repair on the card the engine
        # update is part of it rather than a second offer.
        repair_offered = bool(error) and self.can_repair() and failure.get('kind') in REPAIR_KINDS
        update = dict(update, remind=update['remind'] and not error,
                      with_repair=repair_offered and bool(update.get('engine')))
        return dict(sampling_cache_bytes=total_bytes(), version=__version__,
            disk=disk, decimal_sizes=sys.platform == 'darwin', zh=self.language.startswith('zh'), form=dict(self.form),
            page=self.page, status=row.get('status', 'idle'), busy=self.controller.busy or self.importer.busy or self.cleaner.busy,
            cleanup=dict(self.cleaner.state, busy=self.cleaner.busy),
            can_cleanup=not (self.controller.busy or self.importer.busy or self.cleaner.busy or self.closing
                or self.update_intent or self.engine_updating or self.updater and self.updater.busy
                or self.upgrade.active or self.model_switch),
            model_upgrade=dict(self.upgrade.state, busy=self.upgrade.busy, switching=bool(self.model_switch)),
            offline=dict(progress_view(self.importer.state, zh), runtime=bool(self.form['offline_runtime']),
                runtime_supported=runtime_packages_supported(),
                models=len(self.form['offline_models']), guide=package_instructions(self.form['new_comfy'] and self.form['environment_method'] == 'manual'
                                           and not self.form['offline_runtime'], zh)),
            video_model_guide=video_instructions(zh),
            selected=bool(self.selected), can_repair=self.can_repair(), repair_offered=repair_offered, error=error, retry_kind=self.retry_kind(), notice=self.notice,
            # Qt reads a list as an array; LEVELS is a tuple.
            compatibility=dict(self.compatibility, levels=list(self.compatibility.get('levels') or ())),
            report=dict(self.report),
            models=models, overall=overall, progress=progress, detail='' if file_text else clean(progress.get('detail', '')),
            progress_text=file_text, remaining_seconds=remaining,
            elapsed=duration(time.monotonic()-self.started) if self.started else '', summary=' · '.join(estimate),
            failure=failure,
            source=source, source_name=source_name(source, zh), proxy_mode=preferences['proxy_mode'],
            probe=probe, speeds=speeds, token_set=bool(self.token),
            log=self.tail.text, logs=[dict(label=display(n, zh), path=str(p)) for n, p in self.controller.terminal_sources()],
            url=(self._browser_url(row.get('url', '')) if row.get('status') == 'open' else ''), shortcut=shortcut,
            port_from=port_from, port_to=port_to,
            old_versions=self.old_versions_text(), copied_models=self.copied_models_text(),
            archives=self.archives_text(), archives_confirm=self.archives_confirm_text(),
            archives_done=self.archives_done_text(), archives_failed=self.archives_failed_text(),
            can_shortcut=bool(self.controller.selection and self.controller.selection.get('ready')),
            update=update, engine_update_available=bool(row.get('engine_update_available')),
            settings_path=str(self.store.primary),
            review_id=row.get('plan', {}).get('plan_id', ''),
            consent=self.t('I accept the installation plan and model / toolkit licenses.', '我同意安装计划及模型／工具包许可证。'),
            portable=False, needs_consent=bool(getattr(self.controller, 'fixed_environment', False) is True
                and not (self.controller.root / 'engine/portable-consent.json').exists()),
            review=dict(engine=str(row.get('selection', {}).get('engine', self.form['engine'])),
                        comfy=str(row.get('selection', {}).get('root', self.form['comfy']))))
