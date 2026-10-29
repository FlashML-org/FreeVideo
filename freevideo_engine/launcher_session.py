"""Widget-independent state for the Qt launcher; uses the existing installer."""
from copy import deepcopy
from functools import lru_cache
import importlib
import json
import os
from pathlib import Path
from .long_paths import io_path, resolved, plain, plain_text, plain_data
import sys
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import __version__
from . import disk_space
from .comfy_launcher_runtime import Controller, layout, local_url, loopback_url, managed_frontend, new_layout, queue_busy
from .desktop_runtime import launcher_root, materialize_source
from .download_settings import Probe, read as download_preferences, speed_text
from .launcher_settings import Store, default_language
from .launcher_copy import ACTION_COPY, size_text
from .launcher_terminal import Tail
from .model_status import FAMILIES, NAMES
from .model_guidance import (package_instructions, video_instructions, runtime_packages_supported, download_view,
                             export_download_list, DownloadListError, DOWNLOAD_COPY,
                             image_download_view, export_image_download_list, IMAGE_COPY)
from .setup_progress import LineHold, ModelLine, progress_text
from .terminal_ui import clean, duration
from .model_upgrade import TARGET
from .runtime_alias import checked_real_path

# Background launcher release checks while the window stays open.
CHECK_SECONDS = 30 * 60
SETTLE_SECONDS = 60
# "Later" hides one reminder for this long; a newer release reminds at once.
SNOOZE_SECONDS = 4 * 3600
QUEUE_POLL_SECONDS = 3
RECEIPT_POLL_SECONDS = 5


# package_cleanup.failure_reason codes; the raw error stays in the details.
ARCHIVE_FAILURES = {
    'changed': ('The file changed after the check, so it was kept.', '文件在检查后有改动，已保留。'),
    'busy': ('An installation or a video is in progress. Try again when it finishes.', '有安装或生成正在进行，完成后可以再删除。'),
    'in-use': ('Another program is using it, or this account does not have permission to delete it.', '正被其他程序使用，或当前账户没有删除权限。'),
    'failed': ('It could not be deleted.', '未能删除。'),
}



def _lan_password():
    # Resolved per call: the access module is optional and may be absent.
    return importlib.import_module('.lan_password', __package__)


@lru_cache(maxsize=4)
def _qr_rows(text):
    from .qr_code import encode
    try:
        return [''.join('1' if cell else '0' for cell in row) for row in encode(text)]
    except ValueError:
        return []

class Session:
    # A launcher started from an offline package (portable_launcher.PortableSession).
    bundled = False
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
    settle_at = None
    page = 'comfy'
    selected = installed_versions = engine_identity = bundled_identity = None
    release_details = None
    adaln_thread = adaln_result = adaln_found = None
    adaln_polled = 0.
    lan_synced = 0.
    adaln_hidden = adaln_retry = False
    # The source whose earlier copies were removed once ComfyUI ran it.
    old_versions_retired = None
    archives_busy = False
    action_notice = ''
    pending_update = None
    pending_check = None
    upgrade = image_installer = prompt_installer = None
    model_switch = None
    language = 'en'

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
                         imported_archives=[], kept_archives=[], sampling_caches=not saved.get('installation'),
                         image_model=False, prompt_model=False, prompt_hint_dismissed=False)
        self.form['lan_access'] = False
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
        self.repair_request = None
        self.saved_setup = saved.get('setup', {})
        self.page = 'comfy'
        self.token = ''
        self.error = ''
        self.notice = ''
        self.action_notice = ''
        self.pending_update = None
        self.pending_action = None
        self.pending_imports = []
        self.pending_check = None
        self.report = dict(status='idle', path='', error='')
        self.compatibility = dict(available=False, level=0, automatic=False)
        self.probe = Probe()
        self.tail = Tail()
        self.log_source = None
        self.model_groups = []
        self.image_installer = self.prompt_installer = None
        self.download_export = self.download_error = self.download_error_key = ''
        self._download_view = None
        # 'image' opens the image model's own list, which needs no file check; any other value is the video list.
        self.download_scope = 'video'
        self._image_download_view = None
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
        self.archives_busy = False
        self.archives_from_dialog = False
        self.archives_thread = self.archives_result = self.archives_deleting_offer = None
        self.archives_failures = []
        self.archives_retry_dialog = False
        self.archives_prompt_offer = None
        self.archives_freed = 0
        self.archives_toast_until = 0.
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
                self.updater = UpdateClient(identity, launcher_root(), language=self.language)
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
            root = io_path(self.form['offline_runtime'])
            if (root / 'portable.json').is_file():
                self.activate_offline(root)
        self.inspect_compatibility()

    def t(self, en, zh):
        return zh if self.language.startswith('zh') else en

    def task_busy(self):
        """Work that must finish before another installation operation starts."""
        return bool(self.controller.busy or self.importer.busy or self.cleaner.busy
                    or self.archives_busy or self.closing or self.upgrade and self.upgrade.active
                    or self.model_switch or self.engine_updating
                    or self.background_download_busy())

    def can_manage(self):
        return not (self.task_busy() or self.update_intent)

    def explain_busy(self):
        self.action_notice = 'busy'

    def engine_root(self):
        if self.form['engine'].strip():
            return Path(plain(resolved(Path(self.form['engine']).expanduser())))
        if self.form['new_comfy']:
            return Path(plain(resolved(Path(self.form['destination']).expanduser()))) / 'FreeVideo-engine'
        if self.form['comfy'].strip():
            return Path(layout(self.form['comfy'])['root']) / 'FreeVideo-engine'
        raise ValueError(self.t('Choose your installation folder first.', '请先选择安装位置。'))

    def installation_engine(self):
        """The engine folder of the installation in use; None before one is chosen."""
        selected = self.selected or self.controller.selection or {}
        engine = selected.get('engine')
        if engine is None and getattr(self.controller, 'fixed_environment', False) is True:
            engine = self.controller.root / 'engine'
        return engine

    def persist(self):
        if self.selected and self.form['comfy'] == self.selected.get('root') and self.form['engine'] == self.selected.get('engine'):
            try:
                self.selected['url'] = local_url(self.form['url'])
                if 'lan_access' in self.selected or self.form.get('lan_access') is True:
                    self.selected['lan_access'] = self.form.get('lan_access') is True
            except ValueError:
                pass  # Save the editable draft while retaining a valid installation.
        self.store.write(self.form, self.language, self.selected, self.saved_setup)

    def remember(self, selection, persist=True):
        self.selected = dict(selection)
        self.form.update(comfy=selection['root'], engine=selection['engine'],
                         python=selection.get('python') or '', url=selection['url'],
                         separate=selection.get('separate', False), new_comfy=False, repair=False)
        self.form['lan_access'] = selection.get('lan_access') is True
        if persist:
            self.persist()

    def edit(self, key, value):
        if key in ('engine', 'comfy', 'destination', 'new_comfy', 'offline_runtime') and not self.can_manage():
            self.explain_busy()
            return
        if key == 'language':
            self.language = str(value)
            if self.updater:
                self.updater.language = self.language
            self.persist(); return
        if key == 'token':
            if self.controller.busy:
                raise ValueError(self.t('Pause installation before changing the token.', '请先暂停安装，再修改 Token。'))
            from .hf_auth import validate
            self.token = validate(str(value)); return
        if key not in self.form:
            return
        if self.controller.busy or self.importer.busy or self.cleaner.busy or self.archives_busy:
            self.explain_busy()
            return
        if key in ('separate', 'repair', 'new_comfy', 'sampling_caches', 'lan_access', 'image_model', 'prompt_model', 'prompt_hint_dismissed'):
            value = bool(value)
        if key == 'url':
            value = local_url(str(value))
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
        self.repair_request = None
        self.form[key] = value
        if (key in ('url', 'lan_access') and self.selected and not self.form['new_comfy']
                and self.form['comfy'] == self.selected.get('root') and self.form['engine'] == self.selected.get('engine')):
            selected = dict(self.controller.selection or self.selected, url=local_url(self.form['url']),
                            lan_access=self.form.get('lan_access') is True)
            previous_status = self.controller.state.get('status')
            self.selected = selected
            if key == 'url':
                self.browser_attempted = False
            self.browser_error = self.error = ''
            self.persist()
            if previous_status == 'open':
                self.controller.run('configure_access', dict(url=selected['url'], lan_access=selected['lan_access']))
            else:
                self.controller.selection = dict(selected)
                self.controller.state = dict(status='ready', selection=dict(selected), engine_update_available=bool(
                    self.controller.state.get('engine_update_available')))
            return
        if key in ('new_comfy', 'destination', 'comfy'):
            # A remembered installation's engine belongs to that installation;
            # another location installs into its own FreeVideo-engine.
            self.form['engine'] = os.environ.get('FREEVIDEO_HOME') or ''
        self.import_retry = None
        self.cleaner.plan = None
        self.cleaner.state = dict(status='idle', bytes=0, error='')
        self.controller.selection = None
        self.controller.state = dict(status='idle')
        self.download_export = self.download_error = self.download_error_key = ''
        self.browser_attempted = False
        self.port_notice_url = None
        self.browser_error = self.error = ''
        self.persist()

    def use_disk(self, folder):
        """Move a new installation to another disk, then show the location page to confirm it."""
        if not self.can_manage():
            self.explain_busy()
            return
        if not self.form['new_comfy']:
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
        kept = [p for p in self.form['model_dirs'] if io_path(Path(p).expanduser()).is_dir()]
        self.edit('model_dirs', library_roots(kept + [folder]))

    def set_download_scope(self, scope):
        scope = 'image' if scope == 'image' else 'video'
        if scope != self.download_scope:
            self.download_scope = scope
            self.download_export = self.download_error = self.download_error_key = ''

    def prepare_download_list(self):
        if self.download_scope == 'image':
            return
        if self.controller.busy or self.importer.busy or self.cleaner.busy:
            return
        if (self.controller.state.get('plan', {}).get('download_files_verified')
                and self.controller.state.get('status') == 'review'
                and self.download_error_key not in ('unchecked', 'changed')):
            return
        self.error = ''
        self.download_export = self.download_error = self.download_error_key = ''
        self.persist()
        self.controller.run('inspect', self.setup_values(download_list=True))

    def export_downloads(self, folder):
        """The dialog shows a worded error; nothing raises into a traceback."""
        self.download_export = self.download_error = self.download_error_key = ''
        image = self.download_scope == 'image'
        if not image and (self.controller.busy or self.controller.state.get('status') != 'review'):
            self.download_error = self.t('Wait for the file check to finish.', '请等待文件检查完成。')
            return
        preferences = download_preferences(self.engine_root() / 'download-settings.json')
        try:
            self.download_export = (export_image_download_list(folder, preferences['source'], self._image_location()[0]) if image else
                                    export_download_list(self.controller.state.get('plan', {}), folder, preferences['source']))
        except DownloadListError as error:
            self.download_error_key = error.key
            reason = ''
            if error.key == 'write':
                reason = self.t(*DOWNLOAD_COPY.get('write_' + error.reason, DOWNLOAD_COPY['write_other']))
                # Session has no log writer; keep the OS detail on stderr, without the user's paths.
                from .failure_details import redacted_launcher_error
                print('Download list export failed (%s): %s' % (error.reason, redacted_launcher_error(error.original_error)),
                      file=sys.stderr)
            texts = dict(DOWNLOAD_COPY, error_changed=IMAGE_COPY['error_changed']) if image else DOWNLOAD_COPY
            self.download_error = self.t(*texts['error_' + error.key]).format(reason=reason)

    def use_downloaded_folder(self, folder):
        if not self.can_manage():
            self.explain_busy()
            return
        self.add_folder(folder)
        # Even a folder already in the list may have received new downloads.
        self.controller.state = dict(status='idle')
        self.prepare_download_list()

    def downloads_view(self):
        if self.download_scope == 'image':
            zh, decimal_sizes = self.language.startswith('zh'), sys.platform == 'darwin'
            key = (zh, decimal_sizes)
            if self._image_download_view is None or self._image_download_view[0] != key:
                self._image_download_view = (key, image_download_view(zh, decimal_sizes=decimal_sizes))
            return self._image_download_view[1]
        row = self.controller.state
        plan = row.get('plan', {}) if row.get('status') == 'review' else {}
        if not plan.get('download_files_verified', True):
            plan = {}
        zh = self.language.startswith('zh')
        decimal_sizes = sys.platform == 'darwin'
        # Only inputs that affect list content belong here. Disk/progress/notice
        # updates must not replace the QML model or its file delegates.
        key = ('download_files' in plan, plan.get('download_files', []),
               (plan.get('local_models') or {}).get('matches', {}), zh, decimal_sizes)
        if self._download_view is None or self._download_view[0] != key:
            view = download_view(plan, zh, decimal_sizes=decimal_sizes)
            if self._download_view is None or self._download_view[1] != view:
                self._download_view = (deepcopy(key), view)
            else:
                self._download_view = (deepcopy(key), self._download_view[1])
        return self._download_view[1]

    def remove_folder(self, index):
        removed = self.form['model_dirs'][index]
        self.edit('model_dirs', [p for i, p in enumerate(self.form['model_dirs']) if i != index])
        self.edit('offline_models', [p for p in self.form['offline_models']
                                    if io_path(p) / 'models' != io_path(removed)])

    def clear_runtime(self):
        # Detach it from this plan; imported files remain available for reuse.
        self.edit('offline_runtime', '')
        self.edit('environment_method', 'auto')
        self.edit('model_method', 'auto')


    def import_packages(self, paths):
        if not paths:
            return
        if self.task_busy() or self.update_intent or self.pending_update:
            self.pending_imports = list(dict.fromkeys(self.pending_imports + list(paths)))
            self.action_notice = 'import_queued'
            return
        self.repair_request = None
        destination = (Path(plain(resolved(Path(self.form['destination']).expanduser()))) if self.form['new_comfy']
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
        if self.archives_busy:
            return
        if self.form['model_method'] == 'auto' and not self.form['offline_models']:
            self.importer.download(self.form['offline_runtime'], self.source, False)
        else:
            self.importer.prepare(self.form['offline_runtime'], self.form['offline_models'], self.source)

    def setup_values(self, form=None, **extra):
        """Only the visible automatic-setup option can request every quality level."""
        form = self.form if form is None else form
        values = dict(form, token=self.token, **extra)
        if (form['model_method'] == 'manual' or
                (runtime_packages_supported() and form['new_comfy']
                 and form['environment_method'] == 'manual')):
            values['sampling_caches'] = False
        return values

    def activate_offline(self, root):
        from .offline_packages import check_runtime_platform
        check_runtime_platform()
        from .portable_launcher import PortableController
        root = Path(plain(root))
        controller = PortableController(root, bundled=self.source)
        values = {'url': self.form['url']}
        selected = self.selected or {}
        # Imported environments must not inherit another installation's LAN access.
        if (selected.get('engine') and Path(plain(resolved(selected['engine'])))
                == Path(plain(resolved(root / 'engine')))):
            values['installation'] = selected
        controller.restore(values)
        self.controller.close()
        self.repair_request = None
        self.controller = controller
        self.form['offline_runtime'] = plain(root)
        self.remember(controller.selection)
        self.page = 'launcher'
        self.persist()

    def action(self, name, accepted=False, *, retry=None):
        if self.action_notice == 'installation_unavailable':
            self.action_notice = ''
        if name == 'stop':
            self.pending_action = None
            if self.action_notice == 'setup_queued':
                self.action_notice = ''
        setup_action = (name == 'primary' and self.page != 'launcher'
                        or name == 'retry' and self.retry_kind() != 'launch')
        if setup_action and self.task_busy():
            self.pending_action = (name, accepted, self.page, self.retry_kind() if name == 'retry' else None)
            self.action_notice = 'setup_queued'
            return
        if self.cleaner.busy or self.archives_busy:
            return
        if self.background_download_busy() and (
                name == 'setup' or name == 'update-engine'
                or name == 'retry' and self.retry_kind() != 'launch'
                or name == 'primary' and self.page != 'launcher'):
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
        if name in ('setup', 'launcher', 'launch', 'update-engine'):
            self.repair_request = None
        if name == 'repair':
            # One click instead of Settings › repair switch › Installation › Check:
            # plan a repair of this installation. The review installs it, and
            # setup first stops this launcher's own idle ComfyUI.
            if not self.can_repair():
                return
            # Restore the selected target before freezing this session's repair;
            # the editable setup form may currently describe another installation.
            self.remember(self.selected, persist=False)
            self.repair_request = dict(self.form, repair=True)
            self.persist()
            self.controller.run('inspect', self.setup_values(self.repair_request))
            self.page = 'progress'
            self.model_groups = []
            self.started = time.monotonic()
            return
        if name == 'retry':
            kind = retry or self.retry_kind()
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
                self.controller.run('inspect', self.setup_values(self.repair_request or self.form))
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
                # A moved, renamed or unplugged folder is expected: go to Setup
                # with the saved paths filled in instead of a failure card.
                self.action_notice = 'installation_unavailable'
                self.page = 'comfy'
                return
            self.page = 'launcher'
            return
        if name == 'update-engine' and self.selected:
            self.update_intent, self.update_source = 'engine', 'launcher'
            self.queue_state = None
            self._update_engine_when_idle()
            return
        if name == 'keep_adaln':
            self.keep_adaln(); return
        if name == 'delete_adaln':
            self.delete_adaln(); return
        if name == 'back':
            self.page = 'comfy' if self.page == 'models' else 'models'
            return
        if name == 'browser':
            if self._host_repair_waiting():
                # Finish a repair interrupted earlier before opening the page.
                # Connect stops this launcher's idle ComfyUI first and leaves a
                # running job alone; the page opens when it is done.
                self.browser_attempted = False
                self.browser_error = ''
                self.controller.run('connect'); return
            self.open_browser(); return
        if name == 'delete_archives':
            self.delete_archives(); return
        if name == 'delete_archives_dialog':
            self.delete_archives(dialog=True); return
        if name == 'keep_archives':
            self.keep_archives(); return
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
        if self.page == 'launcher':
            self.repair_request = None
        if self.page == 'launcher' and getattr(self.controller, 'fixed_environment', False) is True:
            consent = io_path(self.controller.root) / 'engine/portable-consent.json'
            if not consent.exists():
                if not accepted:
                    raise ValueError(self.t('Accept the installation plan and licenses.', '请先同意安装计划及许可证。'))
                from .monitoring import save
                save(consent, dict(accepted_at=time.time()))
            self.controller.run('launch', {'url': self.form['url'], 'lan_access': self.form.get('lan_access') is True})
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
            self.controller.run('inspect', self.setup_values(self.repair_request or self.form))
            self.page = 'progress'
            self.model_groups = []
        self.started = time.monotonic()

    def can_repair(self):
        """An installed engine this launcher can check and reinstall again."""
        return (bool(self.selected) and getattr(self.controller, 'fixed_environment', False) is not True
                and not (self.controller.busy or self.importer.busy or self.cleaner.busy or self.archives_busy or self.closing
                         or self.engine_updating or self.upgrade.active or self.model_switch
                         or self.background_download_busy()))

    def repair_unavailable(self):
        """Why Repair is missing: it appears once a task ends, never in an offline
        environment, and needs an installed engine (WITHOUT_REPAIR's reasons)."""
        if getattr(self.controller, 'fixed_environment', False) is True:
            return 'bundled' if self.bundled else 'offline'
        return 'busy' if self.selected else 'none'

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
        if not self.can_manage() or self.updater and self.updater.busy:
            self.explain_busy()
            return
        self.cleaner.start(self.engine_root(), remove=remove)

    def _image_location(self):
        engine = Path(self.installation_engine() or self.engine_root())
        try:
            machine = json.loads(io_path(engine / 'machine.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            machine = {}
        if not isinstance(machine, dict):
            machine = {}
        root = Path(os.environ.get('FREEVIDEO_MODEL_ROOT') or machine.get('model_root') or engine / 'models')
        return root.expanduser(), engine

    def image_pack_action(self, action):
        from .image_gen import image_pack
        if action == 'cancel':
            if self.image_installer:
                self.image_installer.cancel()
            return
        if (self.controller.busy or self.importer.busy or self.cleaner.busy or self.archives_busy
                or self.closing or self.engine_updating or self.update_intent or self.upgrade.active or self.model_switch
                or self.updater and self.updater.busy):
            return
        root, engine = self._image_location()
        if self.image_installer and self.image_installer.busy:
            return
        # An install is the quiet kind: it stops while a video is generating and carries on afterwards.
        if not (self.image_installer and self.image_installer.root == root and self.image_installer.engine_root == engine):
            self.image_installer = image_pack.Installer(root, engine_root=engine, busy=self.generating)
        self.image_installer.start(action, background=action == 'install')

    def generating(self):
        """Whether the opened ComfyUI is running or holding a job (read from the download thread)."""
        url = (self.controller.selection or self.selected or {}).get('url') or self.form['url']
        return queue_busy(url)

    def image_disk_fits(self, total):
        """Whether the disk the image model downloads to has room for it, by the margin that
        download itself keeps and after the video models still waiting on the same disk."""
        from .image_gen import image_pack
        folder = Path(self.form['destination'] if self.form['new_comfy'] else self.form['comfy'])
        if self.installation_engine():
            # An existing installation downloads to its own model folder, which may be on another disk.
            try:
                folder = self._image_location()[0]
            except (OSError, ValueError):
                pass
        try:
            folder = folder.expanduser()
            free = disk_space.free_bytes(folder)
            row = getattr(self.controller, 'state', None) or {}
            if row.get('status') == 'review':
                key = disk_space.disk_key(folder)
                free -= sum(d.get('needed_bytes', 0) for d in row.get('disks', [])
                            if d.get('path') and disk_space.disk_key(d['path']) == key)
            return free >= total + image_pack.BACKGROUND_RESERVE
        except Exception:  # A page reading the disk must never fail to draw.
            return True

    def image_pack_view(self):
        from .image_gen import image_pack
        from .failure_details import image_pack_failure
        try:
            total = sum(r['bytes'] for r in image_pack.rows())
        except image_pack.PackError:
            total = 0
        if not self.installation_engine():
            return dict(installable=False, ready=False, state='missing', visible=False, busy=False,
                        downloaded_bytes=0, verified_bytes=0, total_bytes=total, bytes=total,
                        title=self.t(*NAMES['image']), detail=self.t('Not installed', '未安装'),
                        install={}, can_act=False, removable_bytes=0, repairable=False, failure={}, report_id=None)
        root, engine = self._image_location()
        try:
            value = image_pack.status(root)
            removable = image_pack.removal_plan(root)['bytes']
        except (OSError, ValueError, image_pack.PackError):
            value = dict(installable=False, ready=False, state='invalid', downloaded_bytes=0, verified_bytes=0,
                         total_bytes=total, bytes=0)
            removable = 0
        installer = self.image_installer
        active = installer.state() if installer and installer.root == root and installer.engine_root == engine else {}
        busy = bool(active and installer.busy)
        failure = {}
        labels = {'missing': ('Not installed', '未安装'), 'partial': ('Paused', '已暂停'),
                  'ready': ('Ready', '已就绪'), 'invalid': ('Needs repair', '需要修复')}
        detail = self.t(*labels[value['state']])
        if value.get('checking'):
            detail = self.t('Checking', '正在校验')
        # Repair only answers a pack that is ready or known to be damaged; half a download just continues.
        repairable = value['state'] in ('ready', 'invalid')
        if active.get('state') == 'running':
            labels = {'install': ('Downloading', '正在下载'), 'repair': ('Repairing', '正在修复'),
                      'remove': ('Removing', '正在删除'), 'verify': ('Checking', '正在检查')}
            detail = self.t(*labels[active['action']])
        elif active.get('state') == 'paused_for_generation':
            detail = self.t('Paused while generating', '生成时暂停')
        elif active.get('state') == 'cancelled':
            detail = self.t('Paused', '已暂停')
        elif active.get('state') == 'failed':
            failure = image_pack_failure(active.get('reason'), zh=self.language.startswith('zh'))
            detail = failure['detail']
            repairable = repairable or active.get('reason') in ('needs_repair', 'integrity_failed')
        blocked = (self.controller.busy or self.importer.busy or self.cleaner.busy or self.archives_busy
                   or self.closing or self.engine_updating or self.update_intent or self.upgrade.active or self.model_switch
                   or self.updater and self.updater.busy)
        return dict(value, visible=bool(self.installation_engine()), title=self.t(*NAMES['image']),
                    detail=detail, busy=busy, install=active, can_act=not (blocked or busy or value.get('checking')),
                    removable_bytes=removable,
                    repairable=repairable, failure=failure, report_id=active.get('report_id'))

    def image_pack_report(self):
        if self.image_installer:
            root, engine = self._image_location()
            if self.image_installer.root == root and self.image_installer.engine_root == engine:
                report = self.image_installer.failure_report()
                return report if isinstance(report, dict) else None
        return None

    def start_image_model_after_install(self):
        """The installer's "also install the image model": once ComfyUI opens, the download runs in the background."""
        if self.form['prompt_model'] and self.installation_engine() and self.controller.state.get('status') == 'open' \
                and not self.controller.busy and not (self.prompt_installer and self.prompt_installer.busy):
            from .prompt_vlm import assets
            # Without its model list the download cannot start; the tick stays for a later start.
            if assets.available() and not assets.ready(self._image_location()[1]):
                self.prompt_model_action('install')
            if self.prompt_installer and self.prompt_installer.busy or assets.ready(self._image_location()[1]):
                self.form['prompt_model'] = False
                self.persist()
        if (not self.form['image_model'] or sys.platform == 'darwin' or not self.installation_engine()
                or self.controller.state.get('status') != 'open' or self.controller.busy):
            return
        if self.image_installer and self.image_installer.busy:
            return
        self.image_pack_action('install')
        if self.image_installer and self.image_installer.busy:
            self.form['image_model'] = False
            self.persist()

    def background_download_busy(self):
        return bool(self.image_installer and self.image_installer.busy or self.prompt_installer and self.prompt_installer.busy)

    def prompt_model_action(self, action):
        from .prompt_vlm import installer as prompt_installer
        if action == 'cancel':
            if self.prompt_installer:
                self.prompt_installer.cancel()
            return
        if (self.controller.busy or self.importer.busy or self.cleaner.busy or self.archives_busy
                or self.closing or self.engine_updating or self.update_intent or self.upgrade.active or self.model_switch
                or self.updater and self.updater.busy or not self.installation_engine()):
            return
        engine = self._image_location()[1]
        if self.prompt_installer and self.prompt_installer.busy:
            return
        from .prompt_vlm import assets
        if assets.checking(engine) or not assets.available():
            return
        if not (self.prompt_installer and self.prompt_installer.root == engine):
            self.prompt_installer = prompt_installer.Installer(engine, busy=self.generating)
        self.prompt_installer.start()

    def prompt_model_view(self):
        """The prompt model as a row of the download card and as the suggestion on the home page."""
        from .prompt_vlm import assets
        try:
            spec = assets.catalog()
        except (OSError, ValueError):
            # Without its model list the row can neither be described nor offered, but
            # a missing resource must not take the whole launcher page down with it.
            return dict(ready=False, checking=False, unavailable=True, total_bytes=0, enough_disk=True, busy=False,
                        install={}, failure='', downloaded_bytes=0, title=self.t(*NAMES['text']), suggest=False)
        total = sum(row['bytes'] for row in spec['files'])
        engine = self._image_location()[1] if self.installation_engine() else None
        ready = bool(engine) and assets.ready(engine)
        checking = bool(engine) and not ready and assets.checking(engine)
        installer = self.prompt_installer
        active = installer.state() if installer and engine and installer.root == engine else {}
        busy = bool(active and installer.busy)
        try:
            folder = Path(self.form['destination'] if self.form['new_comfy'] else self.form['comfy'])
            if engine:
                folder = engine
            enough = disk_space.free_bytes(folder.expanduser()) >= total + 2 * 2**30
        except Exception:  # A page reading the disk must never fail to draw.
            enough = True
        failure = ''
        if active.get('state') == 'failed':
            failure = {'disk_space': self.t('Not enough disk space.', '磁盘空间不足。'),
                       'busy': self.t('Another download of this model is running.', '这个模型正在被另一处下载。')}.get(
                active.get('reason'), self.t('The download stopped. Resume to continue.', '下载中断了，点继续可接着下载。'))
        return dict(ready=ready, checking=checking, total_bytes=total, enough_disk=enough, busy=busy, install=active, failure=failure,
                    downloaded_bytes=active.get('done', 0), title=self.t(*NAMES['text']),
                    suggest=bool(engine) and not ready and not checking and not busy and enough and not self.form['prompt_hint_dismissed']
                    and active.get('state') not in ('failed', 'cancelled'))

    def dismiss_prompt_hint(self):
        self.form['prompt_hint_dismissed'] = True
        self.persist()

    def upgrade_model(self):
        """The user's one consent: download int8 beside the current model, switch, then release the old one.

        After a failure only the step that failed runs again: a stopped download,
        or a switch that failed before setup started, is offered afresh (files
        already fetched count); a failed release retries the release. A switch
        that failed during setup is retried from the setup page, which restores
        the previous model. An old model left after the switch is released here
        too, when the user asks.
        """
        if not self.can_manage():
            self.explain_busy()
            return
        state = self.upgrade.state
        if (state.get('status') not in ('available', 'failed', 'releasable') or self.upgrade.busy or self.model_switch
                or self.closing or self.controller.busy or self.importer.busy or self.cleaner.busy or self.archives_busy or self.engine_updating):
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
        if self.closing or upgrade.busy or self.archives_busy:
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
            self.repair_request = None
            upgrade.switching()
            self.controller.run('inspect', self.setup_values(repair=True, prepared_format='int8'))
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

    def _host_repair_waiting(self):
        """A repair of the ComfyUI environment this launcher made and runs, not finished yet."""
        from .comfy_launcher_runtime import host_repair_pending
        selected = self.controller.selection or {}
        return (not self.controller.busy and self.controller.owns_server() and bool(selected.get('python'))
                and managed_frontend(selected) and host_repair_pending(selected['python']))

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
                raise OSError(self.t("Couldn't open the browser. Click Copy address and open it in your browser.",
                                     '没能自动打开浏览器，请点“复制地址”，在浏览器里打开。'))
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
            if (io_path(root) / 'machine.json').is_file():
                self.compatibility = check_installation(root)
                if self.compatibility.get('notice'):
                    from .compatibility_copy import notice_text
                    self.notice = notice_text(self.compatibility, self.language.startswith('zh'))
        except (OSError, ValueError, RuntimeError):
            self.compatibility = dict(available=False, level=0, automatic=False)

    def set_compatibility(self, level, automatic):
        from .compatibility import Store, installed_identity
        root = self.engine_root()
        Store(root).set(installed_identity(root), level, automatic)
        self.inspect_compatibility()

    def set_compatibility_level(self, level):
        # Only moving the slider to Off clears automatic activation. The
        # independent switch can still enable it again while the level is 0.
        self.set_compatibility(level, False if level == 0 else self.compatibility['automatic'])

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
        raw, _ = read_complete(io_path(self.tail.path))
        return redacted_launcher_error(plain_text(raw.decode('utf-8-sig', errors='replace')),
                                       [(self.token, '<REDACTED>')] if self.token else [])

    def _raw_error(self):
        row = self.controller.state
        errors = [self.error, self.browser_error, self.page_error, row.get('error', ''), *row.get('errors', [])]
        shortcut = row.get('shortcut') or {}
        if shortcut.get('status') == 'failed':
            errors.append(shortcut.get('error_zh' if self.language.startswith('zh') else 'error') or shortcut.get('error', ''))
        return plain_text('\n'.join(str(e) for e in errors if e))

    def diagnostic_error(self):
        """Full retained causes, including alias failures shown in storage settings."""
        from .failure_details import redacted_launcher_error
        from .offline_errors import alias_failure
        raw = self._raw_error()
        for state in (self.cleaner.state, self.importer.state, self.upgrade.state):
            value = state.get('error') or ''
            if alias_failure(value) and value not in raw:
                raw += ('\n' if raw else '') + value
        pack_report = self.image_pack_report()
        if pack_report:
            # The existing ZIP exporter retains launcher/error.txt in full.
            # Capture the structured, redacted pack snapshot here before a
            # retry can replace it; collection needs no writable model folder.
            raw += ('\n\n' if raw else '') + 'Image model diagnostic report:\n' + json.dumps(pack_report, ensure_ascii=False, indent=2)
        return redacted_launcher_error(raw, [(self.token, '<REDACTED>')] if self.token else [])

    def export_report(self, output):
        if self.report['status'] == 'running':
            return
        from .diagnostics import collect, Redactor
        root = self.engine_root()
        # Capture the failure and selected logs before another action changes
        # the screen. Collection does not need a working Python/GPU runtime.
        notes = self.diagnostic_error()
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
        # The order the installation check applies: an offered update is never refused there.
        from .release_notes import older
        self.bundled_identity = self.stamped(self.source, self.bundled_identity)
        self.engine_identity = self.stamped((self.selected or {}).get('source'), self.engine_identity)
        return not older(self.bundled_identity[1], self.engine_identity[1])

    def update_key(self):
        """The current reminder: a newer launcher first, then the bundled engine."""
        row = self.updater.state if self.updater else {}
        if row.get('candidate') and row.get('status') in ('available', 'downloading', 'ready', 'error', 'cancelled'):
            return 'launcher:' + str(row['candidate'].get('revision', ''))
        if self.engine_update_pending():
            return 'engine:' + __version__
        return ''

    def dismiss_update(self):
        self.pending_update = None
        self.bridge_pending = False
        if self.action_notice == 'update_queued':
            self.action_notice = ''
        self._snooze_update()
        self.update_intent = None
        self.update_waiting = False
        if self.updater and (self.updater.state['status'] == 'downloading'
                             or self.updater.state.get('retry_operation') == 'download'):
            self.updater.cancelled.set()
            self.updater.stop()

    def _snooze_update(self):
        key = self.update_key()
        if key:
            self.update_snoozed = dict(self.update_snoozed, **{key: time.monotonic() + SNOOZE_SECONDS})

    def _unsnooze(self):
        key = self.update_key()
        self.update_snoozed = {k: v for k, v in self.update_snoozed.items() if k != key}

    def check_update(self, token=''):
        if not self.updater:
            return
        self.action_notice = 'checking_update'
        if self.updater.busy:
            self.pending_check = token
            return
        self.pending_check = None
        if token.strip():
            self.updater.token = token.strip()
        self.update_checked = time.monotonic()
        self.updater.run('check')

    def _open_release_page(self):
        """A package the user installs themselves (macOS app, Windows folder): the release
        page opens and the reminder closes. True when this update was handled that way."""
        row = self.updater.state if self.updater else {}
        from .launcher_update import manual_install, release_page
        if not (row.get('candidate') and row.get('status') != 'checking'
                and manual_install(self.updater.current, self.updater)):
            return False
        from .windows_ux import open_browser
        self.pending_update = None
        self.update_intent = None
        try:
            opened = open_browser(release_page(self.updater.current))
        except OSError:
            opened = False
        if opened:
            self.action_notice = ''
            self._snooze_update()
        else:
            self.action_notice = 'update_page_failed'
        return True

    def update(self, token='', source='launcher'):
        """One explicit update: the newest launcher if one is known, otherwise
        the engine bundled with this launcher. Downloading, waiting for running
        ComfyUI jobs and restarting continue from tick()."""
        if self.engine_updating or self.update_restarting:
            return
        if self._open_release_page():
            return
        if (self.background_download_busy() and not self.controller.busy
                and not self.importer.busy and not self.cleaner.busy and not self.archives_busy
                and not self.closing and not self.upgrade.active and not self.model_switch):
            # An explicit update goes first. The image model download is the long, quiet kind and
            # keeps what it has; it carries on from there when the user continues it.
            if self.image_installer:
                self.image_installer.cancel()
            if self.prompt_installer:
                self.prompt_installer.cancel()
        if self.task_busy() or self.updater and self.updater.busy:
            self.pending_update = (token, source)
            self.action_notice = 'update_queued'
            return
        self.pending_update = None
        if self.action_notice == 'update_queued':
            self.action_notice = ''
        self.repair_request = None
        if self.updater and token.strip():
            self.updater.token = token.strip()
        self.update_source = source
        self.queue_state = None
        row = self.updater.state if self.updater else {}
        if self.updater and row.get('candidate') and row.get('status') != 'checking':
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
            return json.loads((io_path(self.engine_root()) / 'machine.json').read_text(encoding='utf-8')).get('ready') is True
        except (OSError, ValueError, AttributeError):
            return False

    def _restart_when_idle(self):
        if self.task_busy():
            return
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
        if self.task_busy():
            return
        busy = self._queue_busy()
        self.update_waiting = bool(busy)
        if busy is not False:
            return
        self.repair_request = None
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
        self.controller.run('inspect', self.setup_values())
        self.page = 'progress'
        self.model_groups = []
        self.started = time.monotonic()

    def _tick_updates(self):
        now = time.monotonic()
        updater = self.updater
        if updater:
            updater.poll(allow_download=not (self.controller.busy or self.importer.busy or self.cleaner.busy))
        if (updater and self.pending_check is not None and not updater.busy and not self.closing
                and not self.update_intent and not self.pending_update):
            self.check_update(self.pending_check)
        if (self.action_notice == 'checking_update' and self.pending_check is None
                and updater and not updater.busy and updater.state.get('status') != 'checking'):
            self.action_notice = ''
        if (updater and self.update_intent is None and not self.pending_update and not updater.busy and now - self.update_checked >= CHECK_SECONDS
                and not updater.retry
                and updater.state.get('status') in ('idle', 'current', 'available', 'error', 'cancelled')):
            self.update_checked = now
            updater.run('check', background=True)
        if self.resume_until:
            self._check_resumed(now)
        if updater and updater.current.get('packaging') == 'app' and sys.platform == 'darwin':
            # A new app has to run for a while before the old one beside it is deleted.
            self.settle_at = self.settle_at or now + SETTLE_SECONDS
            if now >= self.settle_at:
                self.settle_at = float('inf')
                from . import mac_app_update
                app = mac_app_update.current_app()
                if app is not None:
                    mac_app_update.settle(app)
        if self.bridge and now - self.bridge_polled >= 1:
            self.bridge_polled = now
            from .launcher_bridge import take_request
            request = take_request(self.bridge)
            if request and request['action'] == 'cancel':
                self.bridge_pending = False
                if self.update_intent or self.pending_update:
                    self.dismiss_update()
            elif request or (self.bridge_pending and not self.controller.busy):
                self.bridge_pending = False
                self.update(source='browser')
        if self.pending_update and not self.task_busy() and not (updater and updater.busy):
            token, source = self.pending_update
            self.update(token, source)
        row = updater.state if updater else {}
        if self.update_intent == 'check' and updater and not updater.busy:
            self.update_intent = None
            if row.get('candidate') or self.engine_update_pending():
                self.update(source=self.update_source)
        elif self.update_intent == 'launcher' and updater and not updater.busy:
            if row.get('status') == 'ready':
                self._restart_when_idle()
            elif row.get('status') != 'downloading' and not row.get('retry_at'):
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
            package = io_path(source) / 'freevideo_engine'
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

    @staticmethod
    def stamped(source, cached):
        """(key, stamp) of an engine source, read again only when its stamp file changes."""
        if not source:
            return (None, {})
        try:
            changed = (io_path(source) / 'freevideo_engine' / 'build-identity.json').stat().st_mtime_ns
        except OSError:
            changed = None
        key = (plain(source), changed)
        if cached is not None and cached[0] == key:
            return cached
        from .desktop_runtime import source_release
        return (key, source_release(source))

    def engine_release(self):
        """The stamp of the engine the selected installation runs."""
        self.engine_identity = self.stamped((self.selected or {}).get('source'), self.engine_identity)
        return self.engine_identity[1]

    def installed_product_version(self):
        """The product version of the engine the selected installation runs, if it says."""
        source = (self.selected or {}).get('source')
        try:
            value = json.loads((io_path(source) / 'freevideo_engine/release_notes.json').read_text(encoding='utf-8'))
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
        from .launcher_update import error_text, manual_install, release_page
        if row.get('error_code'):
            view['error'] = error_text(row, self.language.startswith('zh'))
        view['release_page'] = release_page(self.updater.current) if self.updater else ''
        if self.updater and self.updater.install_failed and view.get('candidate'):
            # The new app did not go in and the old one is back: the reminder says so.
            view['status'], view['error'] = 'error', self.t(*ACTION_COPY['update_install_failed'])
        key = self.update_key()
        view.update(engine=self.engine_update_pending(), current=__version__,
                    current_release=public_details(self.updater.current) if self.updater else self.release_details,
                    engine_release=self.engine_release(),
                    installed=self.installed_version(), phase=self.update_phase(), key=key,
                    channel=self.updater.current.get('channel') if self.updater else None,
                    track=self.updater.current.get('track', 'stable') if self.updater else 'stable',
                    manual=bool(self.updater and manual_install(self.updater.current, self.updater)),
                    check_requested=self.pending_check is not None or row.get('status') == 'checking',
                    check_text=self.t(*ACTION_COPY['checking_update']))
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
        self.repair_request = None
        self.importer.cancelled.set()
        if getattr(self, '_closed', False):
            return
        try:
            # A finished deletion is recorded (and saved) inside the same guarantee:
            # a failed save never skips closing the engine.
            if self.archives_thread:
                self.archives_thread.join()
                self._finish_archives()
            self.persist()
        finally:
            self.closing = True
            if self.updater:
                self.updater.cancelled.set()
            self.upgrade.close()
            if self.image_installer:
                self.image_installer.cancel()
                if self.image_installer.thread:
                    self.image_installer.thread.join(5)
            if self.prompt_installer:
                self.prompt_installer.cancel()
                if self.prompt_installer.thread:
                    self.prompt_installer.thread.join(5)
            self.controller.close()
            from .launcher_bridge import remove
            remove(self.bridge)
            self._closed = True

    # -- AdaLN sources the engine no longer reads: the user decides ---------------------
    def adaln_offer(self):
        """The background check's standing offer for this installation, or None; read at most every 5 s."""
        now = time.monotonic()
        if now - self.adaln_polled < 5:
            return self.adaln_found
        self.adaln_polled, self.adaln_found = now, None
        try:
            from . import adaln_reclaim
            machine = json.loads((io_path(self.engine_root()) / 'machine.json').read_text(encoding='utf-8'))
            offer = adaln_reclaim.offer(machine['cache'])
            line = adaln_reclaim.release_line(self.installed_product_version() or adaln_reclaim.installed_release())
            size = sum(offer.get(key, 0) for key in ('bytes', 'tables_bytes'))
            if (all(type(offer.get(key, 0)) is int for key in ('bytes', 'tables_bytes')) and size >= 2**30
                    and offer.get('manifest') and (line is None or offer.get('declined') != line)):
                self.adaln_found = dict(bytes=size, machine=machine, line=line)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return self.adaln_found

    def adaln_idle(self):
        return (self.controller.state.get('status') == 'open'
                and not (self.controller.busy or self.importer.busy or self.cleaner.busy or self.closing
                         or self.update_intent or self.engine_updating or self.update_restarting
                         or self.updater and self.updater.busy or self.upgrade.busy or self.model_switch)
                and self._queue_busy() is False)

    def keep_adaln(self):
        """"Later": the question does not come back before the next release line."""
        offer = self.adaln_offer()
        if self.adaln_thread is not None or not offer:
            return
        if offer['line']:
            from . import adaln_reclaim
            try:
                adaln_reclaim.decline(offer['machine']['cache'], offer['line'])
            except OSError:
                pass
        self.adaln_hidden = True

    def delete_adaln(self):
        offer = self.adaln_offer()
        if self.adaln_thread is not None or not offer or not self.adaln_idle():
            return
        machine = offer['machine']
        def work():
            from . import adaln_reclaim
            self.adaln_result = adaln_reclaim.apply(machine)
        self.adaln_retry = False
        self.adaln_thread = threading.Thread(target=work, name='freevideo-adaln-cleanup', daemon=True)
        self.adaln_thread.start()

    def adaln_dialog_view(self, error, update):
        thread = self.adaln_thread
        if thread is not None and not thread.is_alive():
            result, self.adaln_thread, self.adaln_result = self.adaln_result or {}, None, None
            thread = None
            # Busy GPU: ask again; anything else (deleted, or the conditions stopped holding) ends the question.
            self.adaln_retry = result.get('reason') in ('busy', 'stopped')
            self.adaln_hidden = not self.adaln_retry
            self.adaln_polled, self.adaln_found = 0., None
        offer = None if self.adaln_hidden else self.adaln_offer()
        remind = bool(offer and self.page == 'launcher' and not error and not update['remind']
                      and (thread is not None or self.adaln_idle()))
        size = size_text((offer or {}).get('bytes', 0), decimal_sizes=sys.platform == 'darwin')
        return dict(remind=remind, busy=thread is not None,
                    title=self.t('Delete model files no longer in use?', '删除不再使用的模型文件？'),
                    body=self.t('This frees %s of disk space. If the files are needed later, they will be downloaded again automatically.',
                                '可释放 %s 磁盘空间。以后如需要，会自动重新下载。') % size,
                    delete_text=(self.t('Deleting…', '正在删除…') if thread is not None else
                                 self.t('Try again', '再试一次') if self.adaln_retry else self.t('Delete', '删除')),
                    later_text=self.t('Not now', '暂不删除'))

    def sync_lan_from_store(self):
        """Follow a LAN switch changed from the page's phone panel (it writes the shared settings)."""
        now = time.monotonic()
        if now - self.lan_synced < RECEIPT_POLL_SECONDS or not self.selected:
            return
        if self.controller.busy or self.importer.busy or self.cleaner.busy or self.archives_busy:
            return  # edit() would refuse and tell the user the app is busy; follow the store once it is idle
        self.lan_synced = now
        saved = self.store.read().get('installation') or {}
        if (saved.get('root') != self.selected.get('root') or saved.get('engine') != self.selected.get('engine')
                or type(saved.get('lan_access')) is not bool):
            return
        if saved['lan_access'] != (self.form.get('lan_access') is True):
            self.edit('lan_access', saved['lan_access'])

    def tick(self):
        if isinstance(self.controller, Controller) and not self.controller.busy:
            self.controller.poll_access_proxy()
            self.sync_lan_from_store()
        self._finish_archives()
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

        self.start_image_model_after_install()
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
        if not busy and selection and selection.get('ready') and (row.get('deployed') or row.get('lan_status') == 'stopped' or row.get('status') in ('open', 'restart-required')):
            self.repair_request = None
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
        if self.action_notice == 'busy' and self.can_manage() and not (self.updater and self.updater.busy):
            self.action_notice = ''
        if self.pending_imports and not self.task_busy() and not self.update_intent and not self.pending_update:
            paths, self.pending_imports = self.pending_imports, []
            if self.action_notice == 'import_queued':
                self.action_notice = ''
            self.import_packages(paths)
        if self.pending_action and not self.task_busy() and not self.update_intent and not self.pending_update:
            name, accepted, page, retry = self.pending_action
            self.pending_action = None
            if self.action_notice == 'setup_queued':
                self.action_notice = ''
            self.page = page
            if retry:
                Session.action(self, name, accepted, retry=retry)
            else:
                self.action(name, accepted)

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
                # An alias slot is authenticated before anything below it is read.
                info = os.lstat(io_path(checked_real_path(path)))
                return path, info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev, info.st_mode, info.st_nlink
            except OSError:
                return path, None, None
        # A ZIP moved, changed or deleted while the launcher is open is noticed on the next view.
        key = (engine, tuple((tuple(r.items()), identity(r['path'])) for r in self.form['imported_archives']),
               tuple(identity(str(Path(engine) / name)) for name in ('verified-models.json', 'portable-verified.json')))
        if getattr(self, 'archive_view', None) is None or self.archive_view[0] != key:
            from .package_cleanup import archives
            self.archive_view = (key, archives(self.form['imported_archives'], engine))
        return self.archive_view[1]

    def archives_idle(self):
        """Use the engine update's queue check, including its unknown/wait state."""
        return (not (self.controller.busy or self.importer.busy or self.cleaner.busy or self.archives_busy
                     or self.closing or self.update_intent or self.engine_updating or self.update_restarting
                     or self.updater and self.updater.busy or self.upgrade.busy or self.model_switch)
                and self._queue_busy() is False)

    @staticmethod
    def archive_path(path):
        """The authenticated real spelling that offers and removals report."""
        try:
            return plain(checked_real_path(path))
        except (OSError, KeyError, TypeError, ValueError):
            return path

    @classmethod
    def archive_identity(cls, row):
        return dict(path=cls.archive_path(row['path']), bytes=row['bytes'], mtime_ns=row['mtime_ns'])

    @staticmethod
    def archive_size(value):
        return size_text(value, decimal_sizes=sys.platform == 'darwin')

    def keep_archives(self):
        if self.archives_busy:
            return
        offer = self.archives_prompt_offer or self.archive_offer() or {}
        recorded = [self.archive_identity(row) for row in self.form['imported_archives']]
        kept = [row for row in self.form['kept_archives'] if row in recorded]
        shown = [self.archive_identity(row) for row in offer.get('files', [])]
        self.form['kept_archives'] = kept + [row for row in shown if row not in kept]
        self.archives_retry_dialog = False
        self.archives_prompt_offer = None
        self.persist()

    def delete_archives(self, dialog=False):
        from .package_cleanup import delete_archives
        engine = (self.controller.selection or {}).get('engine')
        if self.controller.state.get('status') != 'open' or not engine or not self.archives_idle():
            return
        offer = self.archives_prompt_offer if dialog and self.archives_prompt_offer else self.archive_offer()
        if not offer or not offer['paths']:
            return
        self.archives_busy = True
        self.archives_from_dialog = dialog
        self.archives_deleting_offer = offer
        self.archives_failures = []
        records = [dict(r) for r in self.form['imported_archives']]
        def work():
            try:
                self.archives_result = delete_archives(records, engine, offered=offer)
            except Exception as error:
                self.archives_result = (0, [], [dict(path=p, reason='failed', detail=str(error)) for p in offer['paths']])
        self.archives_thread = threading.Thread(target=work, name='freevideo-delete-archives')
        self.archives_thread.start()

    def _finish_archives(self):
        if self.archives_result is None:
            return
        freed, removed, failures = self.archives_result
        offered = {r['path']: r for r in (self.archives_deleting_offer or {}).get('files', [])}
        # Keep the checked size even if the file changed or is no longer eligible.
        self.archives_failures = [dict(row, bytes=offered.get(row['path'], {}).get('bytes', 0))
                                  for row in failures]
        self.archives_result = None
        self.archives_busy = False
        self.archives_retry_dialog = bool(self.archives_failures) and self.archives_from_dialog
        if self.archives_retry_dialog:
            failed_paths = {r['path'] for r in self.archives_failures}
            remaining = [r for r in offered.values() if r['path'] in failed_paths]
            self.archives_prompt_offer = dict(bytes=sum(r['bytes'] for r in remaining), files=remaining,
                                             paths=[r['path'] for r in remaining])
        else:
            self.archives_prompt_offer = None
        self.archives_deleting_offer = None
        gone = set(removed)
        self.form['imported_archives'] = [r for r in self.form['imported_archives']
                                          if r['path'] not in gone and self.archive_path(r['path']) not in gone]
        self.archive_view = None
        self.archives_freed = freed
        self.archives_toast_until = time.monotonic() + 3 if freed else 0.
        self.persist()

    def archives_dialog_view(self, offer, idle, error, update):
        files = (offer or {}).get('files', [])
        kept = self.form['kept_archives']
        new = any(self.archive_identity(row) not in kept for row in files)
        remind = (self.page == 'launcher' and not error and (not update['remind'] or self.archives_busy)
                  and ((idle and ((offer or {}).get('bytes', 0) >= 2**30 and new or self.archives_retry_dialog))
                       or self.archives_busy and self.archives_from_dialog))
        if remind and not self.archives_busy and not self.archives_retry_dialog:
            self.archives_prompt_offer = offer
        groups = {}
        for row in files:
            folder = str(Path(row['path']).parent)
            groups.setdefault(folder, []).append(dict(name=Path(row['path']).name, size=self.archive_size(row['bytes'])))
        size, count = self.archive_size((offer or {}).get('bytes', 0)), len(files)
        retry = self.archives_retry_dialog
        if retry:
            failed_title = self.t('%d file could not be deleted' if count == 1 else '%d files could not be deleted',
                                  '%d 个文件未能删除') % count
            title = (self.t('%s freed. ', '已释放 %s，') % self.archive_size(self.archives_freed)
                     if self.archives_freed else '') + failed_title
        else:
            title = self.t('Free up %s of disk space', '可以释放 %s 磁盘空间') % size
        return dict(remind=bool(remind), size=size, count=count, retry=retry, title=title,
                    body='' if retry else self.t(
                        'The imported offline package ZIPs are still on the drive, and FreeVideo no longer needs them. '
                        'Deleting them does not affect models, the runtime or your videos.',
                        '导入过的离线包压缩包仍保留在磁盘上，FreeVideo 已不再需要。删除后，模型、运行环境和作品都不受影响。'),
                    delete_text=(self.t('Deleting…', '正在删除…') if self.archives_busy else
                                 self.t('Try again', '再试一次') if retry else
                                 self.t('Delete and free %s', '删除并释放 %s') % size),
                    keep_text=self.t('Keep for now', '暂时保留'),
                    footnote=self.t('You can also delete them later in Settings › Preferences › Storage.',
                                    '以后也可以在“设置 → 偏好 → 存储”里删除。'),
                    groups=[dict(folder=folder, files=rows) for folder, rows in groups.items()])

    def archives_text(self):
        offer = self.archive_offer()
        if offer and offer['bytes'] > 0:
            return self.t('The ZIPs used for importing are no longer needed. Deleting them frees %s.',
                          '导入用的压缩包已不再需要，删除后可释放 %s。') % self.archive_size(offer['bytes'])
        return ''

    def archives_confirm_text(self):
        offer = self.archive_offer()
        if not offer or offer['bytes'] <= 0:
            return ''
        count = len(offer['paths'])
        english = 'Delete 1 ZIP file (%s)? This cannot be undone.' if count == 1 else 'Delete %d ZIP files (%%s)? This cannot be undone.' % count
        return self.t(english % self.archive_size(offer['bytes']), '删除 %d 个压缩包（%s）？删除后不能恢复。' % (count, self.archive_size(offer['bytes'])))

    def archives_done_text(self):
        freed = getattr(self, 'archives_freed', 0) or 0
        return self.t('ZIP files deleted · %s freed', '已删除压缩包 · 释放 %s') % self.archive_size(freed) if freed > 0 else ''

    def archives_failed_text(self):
        return '\n'.join(self.t('%s (%s): %s', '%s（%s）：%s') % (
                             Path(row['path']).name, self.archive_size(row['bytes']),
                             self.t(*ARCHIVE_FAILURES.get(row.get('reason'), ARCHIVE_FAILURES['failed'])))
                         for row in self.archives_failures)

    def licenses(self, row):
        """What the consent accepts, listed when the user reads the licences: an offline runtime's, or the installation plan's."""
        from .component_licenses import MODEL, is_file, named
        if getattr(self.controller, 'fixed_environment', False) is True:
            rows = self.controller.licenses
        else:
            rows = [named(url) for url in row.get('plan', {}).get('licenses') or ()]
        return [dict(key=value['key'], name=self.t(*value['name']), license=self.t(*value['license']),
                     terms=self.t(*value['terms']) if value['terms'] else '', url=value['link'], file=is_file(value['link']))
                for value in rows or [MODEL]]

    def lan_qr_rows(self):
        """The first access link as rows of '0'/'1' for the settings page to draw."""
        links = self.controller.access_links
        if self.controller.state.get('status') != 'open' or not isinstance(links, list) or not links:
            return []
        return _qr_rows(str(links[0]))

    def lan_password_state(self):
        """(supported, set) for the optional LAN password; unsupported without the access module."""
        engine = (self.controller.selection or {}).get('engine')
        try:
            lan_password = _lan_password()
            return True, bool(engine) and bool(lan_password.is_set(engine))
        except (ImportError, OSError):
            return False, False

    def lan_password_min(self):
        try:
            lan_password = _lan_password()
            return int(getattr(lan_password, 'MIN_LENGTH', 6))
        except (ImportError, OSError, TypeError, ValueError):
            return 6

    def change_lan_password(self, text=None):
        """Set or change the password (text), or remove it (None). Other devices must sign in again."""
        engine = (self.controller.selection or {}).get('engine')
        if not engine:
            return
        lan_password = _lan_password()
        if text is None:
            lan_password.remove(engine)
        else:
            text = str(text)
            least = self.lan_password_min()
            if len(text) < least:
                raise ValueError(self.t('Use at least %d characters.' % least, '至少 %d 个字符。' % least))
            lan_password.set_password(engine, text)
        # The links change with the password; do not wait for the next poll.
        self.controller.access_polled = 0

    def snapshot(self):
        from .failure_details import REPAIR_KINDS, launcher_failure, redacted_launcher_error, display_launcher_error
        from .launcher_copy import display, progress_view, source_name
        zh = self.language.startswith('zh')
        row = self.controller.state
        busy = self.controller.busy or self.importer.busy or self.cleaner.busy
        # The sidebar card shows work being done; a plan waiting for confirmation is not work, but an
        # import or a cleanup started from that page is.
        working = bool(self.importer.busy or self.cleaner.busy
                       or (self.controller.busy and row.get('status') != 'review'))
        task = row.get('task', {})
        port_from = int(row['moved_from']) if row.get('status') == 'open' and 'moved_from' in row else 0
        port_to = (urlsplit(row.get('selection', {}).get('url') or row['url']).port or 80) if port_from else 0
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
        shortcut = row.get('shortcut') or {}
        raw_error = self._raw_error()
        error = redacted_launcher_error(raw_error,
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
        # The model files are checked once the environment is installed; until their step
        # reports, say that instead of a bare "waiting".
        before_models = (row.get('action') == 'install' and row.get('status') == 'running'
                         and not (row.get('task', {}).get('model_groups') or row.get('model_groups')))
        for name in FAMILIES:
            if name == 'image' and name not in by_id:
                continue  # Optional weights have their own receipt; video ready cannot make them ready.
            if name == 'sampling' and name not in by_id and not self.form['sampling_caches']:
                continue
            item = dict(by_id.get(name, {}))
            state = 'ready' if ready and name != 'image' else item.get('state', 'waiting')
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
            if state == 'waiting' and before_models:
                shown = ('Checked after setup', '安装环境后检查')
            else:
                shown = states.get(state, states['waiting'])
            models.append(dict(id=name, title=self.t(*NAMES[name]), state=state,
                detail=self.t(*shown), done=done, total=total,
                found=item.get('existing_bytes', 0), download=item.get('download_bytes', 0),
                rate='%.1f MiB/s' % (item['bytes_per_second']/2**20) if item.get('bytes_per_second') else ''))
        image_pack = self.image_pack_view()
        image_pack['enough_disk'] = self.image_disk_fits(image_pack['total_bytes'])
        prompt_model = self.prompt_model_view()
        try:
            from .download_settings import observed_speed
            speed = observed_speed(self.engine_root()) or 0
        except Exception:
            speed = 0
        if image_pack['visible'] and 'image' not in by_id:
            models.append(dict(id='image', title=image_pack['title'], state=image_pack['state'],
                               detail=image_pack['detail'], done=image_pack['downloaded_bytes'],
                               total=image_pack['total_bytes'], found=image_pack['verified_bytes'],
                               download=image_pack['bytes'], rate=''))
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
        if not hasattr(self, 'model_line'):
            self.model_line = ModelLine()
        steady = self.model_line.line(progress, groups, display('Connecting to download server', zh), zh,
                                      sys.platform == 'darwin')
        file_text = steady if steady is not None else file_text
        if self.importer.busy and self.importer.state.get('activity'):
            imported = self.importer.state
            file_text = progress_text(dict(activity=imported['activity'], unit='bytes',
                name=display('Package files', zh), done=imported.get('activity_done'), total=imported.get('activity_total')), zh)
        if not hasattr(self, 'line_hold'):
            self.line_hold = LineHold()
        detail = clean(progress.get('detail', ''))
        urgent = (progress.get('notice') or progress.get('activity') == 'resume'
                  or progress.get('state') in ('failed', 'cancelled') or steady == display('Connecting to download server', zh))
        held = self.line_hold.text(file_text or detail, immediate=bool(urgent), zh=zh)
        if held != (file_text or detail):
            file_text, detail = held, ''
        else:
            detail = '' if file_text else detail
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
        from .model_repair import review_lines
        replace_models = review_lines(plan.get('replace_models', []), zh=zh)
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
                         'image-models': NAMES['image'],
                         'models': NAMES['text'], 'pypi': ('Python packages', 'Python 依赖'),
                         'github': ('Tools', '安装工具'), 'git': ('Git', 'Git'), 'cuda': ('CUDA', 'CUDA')}
                if group.startswith('torch-'):
                    names[group] = ('GPU packages', 'GPU 依赖')
                route = self.t('direct', '直连') if entry.get('route') == 'direct' else self.t('current connection', '当前连接')
                speeds.append(dict(source=source_name(entry['id'], zh)+' · '+route, group=self.t(*names.get(group, (group, group))),
                    ok=entry.get('ok', False), rate=speed_text(entry, self.language.startswith('zh'))))
        from .sampling_assets import total_bytes
        failure = launcher_failure(error, zh=self.language.startswith('zh'), can_repair=self.can_repair(),
                                   repairs_comfy=self.repairs_comfy(), unavailable=self.repair_unavailable())
        if failure.get('kind') == 'gpu-driver-missing':
            from .display_adapters import adapters, line, only_other_vendors
            zh = self.language.startswith('zh')
            names = adapters()
            if line(zh, names):
                failure['note'] = line(zh, names)
            if only_other_vendors(names):
                from .failure_details import UNSUPPORTED_VENDOR
                failure['title'], failure['action'] = UNSUPPORTED_VENDOR[1 if zh else 0]
        if failure.get('path'):
            # The local repair instruction needs the actual file location; copied details stay redacted.
            from .model_repair import MISMATCH_PATH
            for line in reversed(raw_error.splitlines()):
                if line.startswith(MISMATCH_PATH + ': ') and line[len(MISMATCH_PATH) + 2:]:
                    failure['path'] = line[len(MISMATCH_PATH) + 2:]
                    break
        reasons = self.importer.state.get('write_reasons') or []
        if (self.importer.state.get('status') == 'error' and reasons
                and self.error == self.importer.state.get('error') and failure.get('kind') != 'runtime-alias'):
            from .offline_errors import guidance
            failure = dict(title=guidance('title', zh),
                           detail=guidance(reasons[0], zh), action='', kind='offline-write')
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
        cleanup = dict(self.cleaner.state, busy=self.cleaner.busy)
        from .offline_errors import alias_failure
        if alias_failure(cleanup.get('error', '')):
            cleanup['error'] = display_launcher_error(cleanup['error'], zh=zh)
        model_upgrade = dict(self.upgrade.state, busy=self.upgrade.busy)
        if alias_failure(model_upgrade.get('error', '')):
            model_upgrade['error'] = display_launcher_error(model_upgrade['error'], zh=zh)
        archive_offer = (self.archives_deleting_offer if self.archives_busy else
                         self.archives_prompt_offer if self.archives_retry_dialog else self.archive_offer())
        archives_idle = bool(archive_offer and archive_offer['paths']) and self.archives_idle()
        archive_dialog = self.archives_dialog_view(archive_offer, archives_idle, error, update)
        downloads = self.downloads_view()
        inspection_read_error = (row.get('status') == 'failed' and row.get('action') == 'inspect'
                                 and 'Download list error: read' in row.get('error', '').splitlines())
        download_state = dict(checking=bool(self.controller.busy), error=self.download_error,
            needs_check=self.download_error_key in ('unchecked', 'changed'), folder=self.download_export,
            inspection_error=inspection_read_error,
            notice=downloads['copy']['exported'].format(folder=self.download_export) if self.download_export else '')
        if inspection_read_error:
            download_state.update(error=self.t(*DOWNLOAD_COPY['error_read']), needs_check=True)
        return plain_data(dict(download_list=download_state, sampling_cache_bytes=total_bytes(), version=__version__, image_pack=image_pack,
            prompt_model=prompt_model, bytes_per_second=speed,
            disk=disk, decimal_sizes=sys.platform == 'darwin', zh=self.language.startswith('zh'), form=dict(self.form),
            page=self.page, status=row.get('status', 'idle'), busy=busy, working=working,
            action_notice=self.t(*ACTION_COPY[self.action_notice]) if self.action_notice else '',
            setup_pending=bool(self.pending_action), setup_wait_text=self.t(*ACTION_COPY['setup_queued']),
            can_manage=self.can_manage(), busy_reason=self.t(*ACTION_COPY['busy']),
            cleanup=cleanup,
            can_cleanup=self.can_manage() and not (self.updater and self.updater.busy),
            model_upgrade=dict(model_upgrade, switching=bool(self.model_switch)),
            offline=dict(progress_view(self.importer.state, zh), runtime=bool(self.form['offline_runtime']),
                runtime_supported=runtime_packages_supported(),
                models=len(self.form['offline_models']), guide=package_instructions(self.form['new_comfy'] and self.form['environment_method'] == 'manual'
                                           and not self.form['offline_runtime'], zh)),
            video_model_guide=video_instructions(zh),
            adaln_dialog=self.adaln_dialog_view(error, update),
            selected=bool(self.selected), can_repair=self.can_repair(), repair_offered=repair_offered, error=error, retry_kind=self.retry_kind(), notice=self.notice,
            # Qt reads a list as an array; LEVELS is a tuple.
            compatibility=dict(self.compatibility, levels=list(self.compatibility.get('levels') or ())),
            report=dict(self.report),
            models=models, overall=overall, progress=progress, detail=detail,
            progress_text=file_text, remaining_seconds=remaining,
            # The elapsed row is only shown while working. An idle clock would
            # keep publishing changed snapshots after a launch has failed.
            elapsed=duration(time.monotonic()-self.started) if self.started and busy else '', summary=' · '.join(estimate),
            failure=failure,
            source=source, source_name=source_name(source, zh), proxy_mode=preferences['proxy_mode'],
            probe=probe, speeds=speeds, token_set=bool(self.token),
            log=self.tail.text, logs=[dict(label=display(n, zh), path=str(p)) for n, p in self.controller.terminal_sources()],
            url=(self._browser_url(row.get('url', '')) if row.get('status') == 'open' else ''), shortcut=shortcut,
            access_links=(list(self.controller.access_links) if row.get('status') == 'open'
                          and isinstance(self.controller.access_links, list) else []),
            lan_qr=self.lan_qr_rows(), lan_password_set=self.lan_password_state()[1],
            lan_password_available=self.lan_password_state()[0], lan_password_min=self.lan_password_min(),
            lan_message=(self.t('LAN access has stopped. Turn it on again.', '局域网访问已停止，请重新开启。')
                         if row.get('lan_status') == 'stopped' else
                         self.t('No LAN address found. Connect to Wi-Fi or a wired network first.', '未找到局域网地址，请先连接 Wi-Fi 或有线网络。')
                         if row.get('lan_status') == 'no-address' else ''),
            port_from=port_from, port_to=port_to,
            plugin_scoped=row.get('status') == 'open' and row.get('plugin_scoped') is True,
            old_versions=self.old_versions_text(), copied_models=self.copied_models_text(),
            archives=self.archives_text(), archives_confirm=self.archives_confirm_text(),
            archives_done=self.archives_done_text(), archives_failed=self.archives_failed_text(),
            archives_dialog=archive_dialog, archives_busy=self.archives_busy, can_delete_archives=archives_idle,
            archives_toast=(self.t('Freed %s', '已释放 %s') % self.archive_size(self.archives_freed)
                            if self.archives_freed and time.monotonic() < self.archives_toast_until else ''),
            can_shortcut=bool(self.controller.selection and self.controller.selection.get('ready')),
            update=update, engine_update_available=bool(row.get('engine_update_available')),
            settings_path=str(self.store.primary),
            review_id=row.get('plan', {}).get('plan_id', ''),
            consent=self.t('I accept the installation plan and model / toolkit licenses.', '我同意安装计划及模型／工具包许可证。'),
            licenses=self.licenses(row), portable=False, needs_consent=bool(getattr(self.controller, 'fixed_environment', False) is True
                and not (io_path(self.controller.root) / 'engine/portable-consent.json').exists()),
            review=dict(engine=str(row.get('selection', {}).get('engine', self.form['engine'])),
                        replace_models=replace_models,
                        comfy=str(row.get('selection', {}).get('root', self.form['comfy'])))))
