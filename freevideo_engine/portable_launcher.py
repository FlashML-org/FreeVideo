"""Offline bundle adapter for the shared launcher, settings and progress UI."""
import json
import os
from pathlib import Path
import subprocess
import time

from . import component_licenses, launch_view, portable, processes
from .comfy_launcher import Launcher
from .comfy_launcher_runtime import Controller, local_url, loopback_url, matches_listener, matches_server, server_info
from .monitoring import save
from .long_paths import io_path, resolved, plain, plain_text

UNREADABLE = 'FreeVideo cannot read its program files'


class PortableController(Controller):
    def __init__(self, root, bundled=None):
        self.root = Path(plain(resolved(root)))
        self.bundle = portable.manifest(self.root)
        super().__init__(Path(plain(portable.inside(self.root, self.bundle['source']))))
        # The launcher's own engine code, which an installation it assembled can move to.
        self.bundled = Path(bundled) if bundled else None
        self.update_values = None
        self.child = None
        self.initialization_log = None
        self.comfy_controller = None
        self.fixed_environment = True
        # What the first start asks the user to accept, read once from the file list.
        self.licenses = component_licenses.bundle(self.root, self.bundle)

    def restore(self, values):
        # Restoring the launcher is read-only. A bundled deployment is not yet
        # a GPU-ready machine; only initialize() may certify local kernels.
        url = (values.get('installation') or {}).get('url') or values.get('url')
        self.selection = dict(root=plain(portable.inside(self.root, self.bundle['comfy'])),
            engine=str(self.root/'engine'), source=str(self.source),
            python=plain(portable.inside(self.root, self.bundle['python'])),
            url=local_url(url or 'http://127.0.0.1:8188'), ready=False, separate=False,
            lan_access=(values.get('installation') or {}).get('lan_access', values.get('lan_access', False)) is True,
            lan_port=(values.get('installation') or {}).get('lan_port', (self.selection or {}).get('lan_port')))
        self.state = dict(status='bundled', selection=dict(self.selection),
                          engine_update_available=self.engine_outdated())
        self.restore_terminal(self.selection['engine'])
        return True

    def engine_outdated(self):
        """This launcher carries other engine code than the installation it assembled runs.

        A full bundle starts the code inside it, and an environment that no
        longer fits this launcher's engine keeps the code it has. Whether the
        launcher's code is the newer one is the session's comparison, as for
        any installation."""
        if self.bundled is None or (io_path(self.root)/'start_portable.py').is_file():
            return False
        from .desktop_runtime import matching_source
        from .offline_packages import runtime_fits
        try:
            fits = runtime_fits(json.loads((io_path(self.root)/'runtime.json').read_text(encoding='utf-8')))
        except (OSError, ValueError, AttributeError):
            return False
        return fits and not matching_source(self.bundled, self.source)

    def owns_server(self):
        # The ComfyUI a launch started belongs to that connection's controller.
        controller = self.comfy_controller
        return controller is not None and controller.owns_server()

    def _inspect(self, values):
        """The session's engine update asks first; nothing is downloaded or installed."""
        self.restore(values)
        if not self.state['engine_update_available']:
            raise ValueError('This offline installation already runs the engine this launcher carries')
        self.update_values = dict(values)
        self.state = dict(self.state, status='review', action='inspect', host=dict(ready=True),
                          selection=dict(self.selection, ready=True))

    def _install(self, accepted):
        """Start the launcher's engine code with this installation's environment and models.

        A start that fails puts the previous code back, so the next start works as before."""
        if not accepted or self.update_values is None:
            raise ValueError('Review the engine update first')
        values, self.update_values = self.update_values, None
        if self.comfy_controller is not None:
            # Our idle ComfyUI runs the previous code; a running job is never stopped.
            self.comfy_controller._stop_server_for_setup(self.selection['url'])
        previous = self.bundle
        from .offline_packages import rebind
        rebind(self.root, self.bundled)
        try:
            self.bundle = portable.manifest(self.root)
            self.source = portable.inside(self.root, self.bundle['source'])
            self._launch(values)
        except BaseException:
            try:
                # Only this update's binding goes back: another launcher may have changed it since.
                if portable.manifest(self.root).get('source') == self.bundle['source']:
                    save(self.root/'portable.json', previous)
                self.bundle = portable.manifest(self.root)
                self.source = portable.inside(self.root, self.bundle['source'])
                # The selection and the offered update follow the code now bound, so Update now stays.
                self.restore(values)
            except (OSError, ValueError):
                pass  # The start's own failure is the one to report.
            raise

    def terminal_sources(self):
        sources = super().terminal_sources()
        if self.initialization_log:
            sources.insert(0, ('initialize', plain(self.initialization_log)))
        if self.comfy_controller:
            sources = [row for row in sources if row[0] != 'comfy'] + self.comfy_controller.terminal_sources()
        return sources

    def terminal_running(self, path):
        if self.comfy_controller and path and Path(path) == self.comfy_controller.server_log:
            return self.comfy_controller.terminal_running(path)
        return super().terminal_running(path)

    def progress(self, row):
        stage = row.get('stage')
        label = {'verify': 'Verify bundled files', 'gpu': 'Test GPU acceleration',
                 'comfy': 'Start ComfyUI'}.get(stage, 'Prepare FreeVideo')
        progress = dict(row, label=label)
        done, total = row.get('done', 0), row.get('total')
        fraction = min(1., max(0., done/total)) if total else 0.
        offset = {'verify': 0, 'gpu': 1, 'comfy': 2}.get(stage, 0)
        self.state = dict(self.state, task=dict(progress=progress),
                          overall=dict(done=offset+fraction, total=3, label=label))

    def _launch(self, values):
        self.restore(values)
        self.state = dict(self.state, status='running', action='launch')
        url = self.selection['url']
        info = server_info(url)
        if matches_server(info, self.selection['root'], self.selection['engine'], self.source):
            if self.comfy_controller is not None and self.comfy_controller.owns_server():
                # Our own server on this port: its controller moves the listening
                # address and the LAN proxy. A server left on the previous source
                # does not match and restarts below, as before.
                self.selection['ready'] = True
                self.ensure_shortcut()
                self.attach_console(info)
                try:
                    return self.comfy_controller._configure_access(
                        dict(url=url, lan_access=self.selection['lan_access']), connect=True)
                finally:
                    self._sync_access()
            if not matches_listener(info, url):
                self.state = dict(self.state, status='restart-required', url=loopback_url(url),
                    error='The ComfyUI already running does not use this address. After its jobs finish, restart it with this address and connect again.')
                return
            self.selection['ready'] = True
            self.ensure_shortcut()
            self.attach_console(info)
            self.state = dict(self.state, status='open', url=loopback_url(url)+'/?freevideo=launch', selection=dict(self.selection))
            if self.selection['lan_access']:
                self._configure_access(dict(lan_access=True))
            return
        # Windows keeps a folder made by a run as administrator, or under another
        # account, from this account; the bundled Python would only report a
        # missing module.
        for folder in (self.source, Path(self.selection['python']).parent):
            try:
                os.listdir(io_path(folder))
            except PermissionError:
                raise RuntimeError('%s: Windows denies this account access to %s' % (UNREADABLE, folder)) from None
            except OSError:
                pass  # Missing files are for the bundle check to name.
        log_path = io_path(self.root)/'engine/portable-runs'/('%s-%s.log' % (time.time_ns(), os.getpid()))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialization_log = Path(plain(log_path))
        command = [self.selection['python'], '-I', '-u', str(self.root/'start_portable.py'), '--initialize']
        if not (io_path(self.root)/'start_portable.py').is_file():
            # Independent EXE + environment ZIP: use this launcher's materialized
            # source, without requiring an older launcher inside the archive.
            command = [self.selection['python'], '-I', '-u', '-c',
                'import sys;sys.path.insert(0,sys.argv[1]);'
                'from freevideo_engine.portable import main;'
                'sys.exit(main(sys.argv[2], ["--initialize"]))', str(self.source), str(self.root)]
        # -I ignores PYTHONUTF8, so name the mode on the command line: the start
        # process then reads and writes UTF-8 like every other FreeVideo process,
        # whatever the system code page is.
        command[1:1] = ['-X', 'utf8']
        tail = ''
        view = launch_view.view_for(self.root)
        self.progress(dict(stage='verify', done=0, total=None, detail='Check local files'))
        try:
            if self.cancelled.is_set():
                raise RuntimeError('Startup cancelled; all files retained')
            with log_path.open('w', encoding='utf-8') as log:
                with self._server_lock:
                    if self._closed or self.cancelled.is_set():
                        raise RuntimeError('Startup cancelled; all files retained')
                    self.child = processes.popen(command, env=launch_view.environment_view(portable.environment(self.root, self.bundle), view),
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                        start_new_session=True, supervise=True)
                for line in self.child.stdout:
                    line = launch_view.line_back(line, view)
                    log.write(plain_text(line)); log.flush()
                    tail = (tail + line)[-12000:]
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict) and row.get('stage'):
                            self.progress(row)
                    except ValueError:
                        pass
                    if self.cancelled.is_set():
                        processes.stop(self.child)
                        break
                code = self.child.wait()
            if self.cancelled.is_set():
                raise RuntimeError('Startup cancelled; all files retained')
            if code:
                raise RuntimeError('Bundle startup failed (exit %s)\n%s\nLog: %s' % (code, plain_text(tail), plain(log_path)))
        finally:
            if self.child is not None:
                if self.child.poll() is None:
                    processes.stop(self.child)
                if self.child.stdout:
                    self.child.stdout.close()
                self.child = None
        machine = json.loads((io_path(self.root)/'engine/machine.json').read_text(encoding='utf-8'))
        self.progress(dict(stage='comfy', detail='Open FreeVideo workflow'))
        opened = portable.connect(self.root, self.bundle, machine, self.progress_keywords,
                                  self.cancelled, url=url, on_controller=self.retain_comfy_controller,
                                  previous=self.comfy_controller, lan_access=self.selection['lan_access'],
                                  lan_port=self.selection.get('lan_port'))
        if self.comfy_controller is not None:
            self._sync_access()
        self.selection['ready'] = True
        self.ensure_shortcut()
        self.state = dict(self.state, status='open', url=opened, selection=dict(self.selection),
                          lan_status=self.comfy_controller.state.get('lan_status', '') if self.comfy_controller else '',
                          overall=dict(done=3, total=3, label='Ready'))

    def _sync_access(self, status=True):
        child = self.comfy_controller
        self.selection.update({key: child.selection[key] for key in ('url', 'lan_access', 'lan_port')
                               if key in child.selection})
        self.access_links = child.access_links
        self.state = dict(self.state, selection=dict(self.selection), lan_status=child.state.get('lan_status', ''))
        if status:
            self.state = dict(self.state, status=child.state['status'], url=child.state.get('url', ''))
            if 'engine_exit' in child.state:
                self.state = dict(self.state, engine_exit=child.state['engine_exit'])

    def _configure_access(self, values, *, connect=False):
        if connect:
            return self._launch(dict(installation=dict(self.selection, **values)))
        if self.comfy_controller is None:
            pending = self.state.get('engine_update_available', False)
            try:
                return super()._configure_access(values)
            finally:
                self.state = dict(self.state, engine_update_available=pending)
        try:
            self.comfy_controller._configure_access(values)
        finally:
            self._sync_access()

    def stop_owned_server(self, *, grace=5):
        if self.comfy_controller is not None:
            self.comfy_controller.stop_owned_server(grace=grace)
        super().stop_owned_server(grace=grace)

    def check_server_exit(self):
        """The ComfyUI of a package runs under the inner controller; its record and notice are copied here."""
        child = self.comfy_controller
        if child is None:
            return super().check_server_exit()
        value = child.check_server_exit()
        if value is not None:
            self._sync_access()
        return value

    def check_access_proxy(self):
        if self.comfy_controller is None:
            return super().check_access_proxy()
        if self.comfy_controller.access_proxy is None:
            return
        status = self.comfy_controller.state.get('status')
        self.comfy_controller.check_access_proxy()
        # The package's own state is copied only when this check saw ComfyUI stop.
        self._sync_access(self.comfy_controller.state.get('status') != status)

    def ensure_shortcut(self):
        from .desktop_shortcut import create_after_install
        self.state = dict(self.state, shortcut=create_after_install(
            self.selection['engine'], self.source,
            portable_root=self.root if (io_path(self.root)/'FreeVideo.exe').is_file() else None))

    def retain_comfy_controller(self, controller):
        with self._server_lock:
            if self._closed or self.cancelled.is_set():
                controller.close()
                raise RuntimeError('Startup cancelled; all files retained')
            # An external connection can own a proxy directly on the package
            # controller. Retire it before handing startup to the inner controller;
            # later switch-off and polling operate through that inner controller.
            self.stop_access_proxy()
            self.comfy_controller = controller

    def close(self):
        try:
            super().close()
        finally:
            with self._server_lock:
                if self.comfy_controller:
                    self.comfy_controller.close()
                    self.comfy_controller = None

    def progress_keywords(self, **row):
        self.progress(row)

    def cancel(self):
        super().cancel()
        if self.child is not None:
            processes.stop(self.child)


class PortableLauncher(Launcher):
    def __init__(self, window, root):
        self.bundle_root = Path(plain(resolved(root)))
        self.consent_path = io_path(self.bundle_root)/'engine/portable-consent.json'
        controller = PortableController(self.bundle_root)
        super().__init__(window, controller.source, controller=controller)
        self.loading_settings = True
        for name, key in (('comfy', 'root'), ('engine', 'engine'), ('python', 'python'), ('url', 'url')):
            getattr(self, name).set(controller.selection[key])
        self.loading_settings = False
        from .launcher_view import Card, Progress
        from . import branding as b
        self.bundle_progress = Card(self.pages[3], padding=b.space(4))
        body = self.bundle_progress.body
        self.ttk.Label(body, textvariable=self.phase, style='ProgressTitle.Card.TLabel', anchor='center').pack(fill='x')
        self.overall_bar = Progress(body); self.overall_bar.pack(fill='x', pady=b.space(2))
        self.ttk.Label(body, textvariable=self.overall_text, style='Meta.Card.TLabel', anchor='center').pack(fill='x')
        self.bar = Progress(body, subdued=True); self.bar.pack(fill='x', pady=b.space(2))
        self.ttk.Label(body, textvariable=self.details, style='Meta.Card.TLabel', wraplength=530).pack(fill='x')
        self.ttk.Label(body, textvariable=self.current_text, style='Meta.Card.TLabel').pack(fill='x')
        self.bundle_note = self.ttk.Label(self.pages[3], style='Meta.TLabel', wraplength=600, anchor='center')
        self.bundle_note.pack(fill='x')
        self.refresh()

    def changed(self, *_):
        if self.loading_settings:
            return
        self.queue_save()
        if self.launch_selection and _ and _[0] in (str(self.url), str(self.lan_access)):
            return  # The shared settings saver applies the connection change.
        if not self.controller.busy:
            self.controller.restore({'url': self.url.get(), 'lan_access': self.lan_access.get()})
            self.launch_selection = dict(self.controller.selection)
            self.reset_browser()
            self.refresh()

    def refresh(self):
        self.wizard_step = 3
        super().refresh()
        self.back_button.configure(text=self.t('Settings', '设置'))
        if not self.consent_path.exists() and not self.controller.busy:
            self.primary.configure(text=self.t('Accept licenses & start', '同意许可证并启动'))
        if hasattr(self, 'bundle_progress'):
            if self.controller.busy:
                self.bundle_progress.pack(fill='x', before=self.bundle_note)
            else:
                self.bundle_progress.pack_forget()
            note = self.t('Python, ComfyUI and models are included.', '已包含 Python、ComfyUI 和模型。')
            if not self.consent_path.exists():
                note += '\n' + self.t('Licenses: models/edge/LICENSE', '许可证：models/edge/LICENSE')
            self.bundle_note.configure(text=note)

    def act(self):
        if self.controller.busy:
            return
        if not self.consent_path.exists():
            save(self.consent_path, dict(source_commit=self.controller.bundle['source_commit'], accepted_at=time.time()))
        self.started = time.monotonic()
        super().act()

    def back(self):
        self.show_options()



def gui(root):
    from .modern_launcher import main
    main(portable_session(root))


def portable_session(root):
    from .launcher_session import Session
    root = Path(plain(resolved(root)))
    from .triton_compat import own_temporary_environment
    own_temporary_environment(root / 'engine')
    os.environ.update(portable.environment(root, portable.manifest(root)))
    os.environ['FREEVIDEO_LAUNCHER_HOME'] = str(root/'engine/launcher')
    class PortableSession(Session):
        bundled = True

        def __init__(self):
            controller = PortableController(root)
            self.consent_path = io_path(root)/'engine/portable-consent.json'
            super().__init__(controller.source, controller=controller, smoke=True)
            self.smoke = False
            self.page = 'launcher'

        def edit(self, key, value):
            if key in ('url', 'lan_access', 'language', 'token'):
                super().edit(key, value)

        def action(self, name, accepted=False):
            if name in ('primary', 'launch') and not self.controller.busy:
                if not self.consent_path.exists():
                    if not accepted:
                        raise ValueError(self.t('Accept the included licenses to continue.', '请先同意随包附带的许可证。'))
                    save(self.consent_path, dict(source_commit=self.controller.bundle['source_commit'], accepted_at=time.time()))
            if name != 'setup':
                super().action(name, accepted)

        def snapshot(self):
            return dict(super().snapshot(), portable=True, needs_consent=not self.consent_path.exists())
    return PortableSession()
