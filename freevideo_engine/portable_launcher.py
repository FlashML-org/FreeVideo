"""Offline bundle adapter for the shared launcher, settings and progress UI."""
import json
import os
from pathlib import Path
import subprocess
import time

from . import portable, processes
from .comfy_launcher import Launcher
from .comfy_launcher_runtime import Controller, local_url, matches_server, server_info
from .monitoring import save

UNREADABLE = 'FreeVideo cannot read its program files'


class PortableController(Controller):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.bundle = portable.manifest(self.root)
        super().__init__(portable.inside(self.root, self.bundle['source']))
        self.child = None
        self.initialization_log = None
        self.comfy_controller = None
        self.fixed_environment = True

    def restore(self, values):
        # Restoring the launcher is read-only. A bundled deployment is not yet
        # a GPU-ready machine; only initialize() may certify local kernels.
        url = (values.get('installation') or {}).get('url') or values.get('url')
        self.selection = dict(root=str(portable.inside(self.root, self.bundle['comfy'])),
            engine=str(self.root/'engine'), source=str(self.source),
            python=str(portable.inside(self.root, self.bundle['python'])),
            url=local_url(url or 'http://127.0.0.1:8188'), ready=False, separate=False)
        self.state = dict(status='bundled', selection=dict(self.selection))
        self.restore_terminal(self.selection['engine'])
        return True

    def terminal_sources(self):
        sources = super().terminal_sources()
        if self.initialization_log:
            sources.insert(0, ('initialize', str(self.initialization_log)))
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
            self.selection['ready'] = True
            self.ensure_shortcut()
            self.attach_console(info)
            self.state = dict(self.state, status='open', url=url+'/?freevideo=launch', selection=dict(self.selection))
            return
        # Windows keeps a folder made by a run as administrator, or under another
        # account, from this account; the bundled Python would only report a
        # missing module.
        for folder in (self.source, Path(self.selection['python']).parent):
            try:
                os.listdir(folder)
            except PermissionError:
                raise RuntimeError('%s: Windows denies this account access to %s' % (UNREADABLE, folder)) from None
            except OSError:
                pass  # Missing files are for the bundle check to name.
        log_path = self.root/'engine/portable-runs'/('%s-%s.log' % (time.time_ns(), os.getpid()))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialization_log = log_path
        command = [self.selection['python'], '-I', '-u', str(self.root/'start_portable.py'), '--initialize']
        if not (self.root/'start_portable.py').is_file():
            # Independent EXE + environment ZIP: use this launcher's materialized
            # source, without requiring an older launcher inside the archive.
            command = [self.selection['python'], '-I', '-u', '-c',
                'import sys;sys.path.insert(0,sys.argv[1]);'
                'from freevideo_engine.portable import main;'
                'sys.exit(main(sys.argv[2], ["--initialize"]))', str(self.source), str(self.root)]
        tail = ''
        self.progress(dict(stage='verify', done=0, total=None, detail='Check local files'))
        try:
            if self.cancelled.is_set():
                raise RuntimeError('Startup cancelled; all files retained')
            with log_path.open('w', encoding='utf-8') as log:
                with self._server_lock:
                    if self._closed or self.cancelled.is_set():
                        raise RuntimeError('Startup cancelled; all files retained')
                    self.child = processes.popen(command, env=portable.environment(self.root, self.bundle),
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                        start_new_session=True, supervise=True)
                for line in self.child.stdout:
                    log.write(line); log.flush()
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
                raise RuntimeError('Bundle startup failed (exit %s)\n%s\nLog: %s' % (code, tail, log_path))
        finally:
            if self.child is not None:
                if self.child.poll() is None:
                    processes.stop(self.child)
                if self.child.stdout:
                    self.child.stdout.close()
                self.child = None
        machine = json.loads((self.root/'engine/machine.json').read_text(encoding='utf-8'))
        self.progress(dict(stage='comfy', detail='Open FreeVideo workflow'))
        opened = portable.connect(self.root, self.bundle, machine, self.progress_keywords,
                                  self.cancelled, url=url, on_controller=self.retain_comfy_controller,
                                  previous=self.comfy_controller)
        self.selection['ready'] = True
        self.ensure_shortcut()
        self.state = dict(self.state, status='open', url=opened, selection=dict(self.selection),
                          overall=dict(done=3, total=3, label='Ready'))

    def ensure_shortcut(self):
        from .desktop_shortcut import create_after_install
        self.state = dict(self.state, shortcut=create_after_install(
            self.selection['engine'], self.source,
            portable_root=self.root if (self.root/'FreeVideo.exe').is_file() else None))

    def retain_comfy_controller(self, controller):
        with self._server_lock:
            if self._closed or self.cancelled.is_set():
                controller.close()
                raise RuntimeError('Startup cancelled; all files retained')
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
        self.bundle_root = Path(root).resolve()
        self.consent_path = self.bundle_root/'engine/portable-consent.json'
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
        if not self.controller.busy:
            self.controller.restore({'url': self.url.get()})
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
    root = Path(root).resolve()
    os.environ.update(portable.environment(root, portable.manifest(root)))
    os.environ['FREEVIDEO_LAUNCHER_HOME'] = str(root/'engine/launcher')
    class PortableSession(Session):
        def __init__(self):
            controller = PortableController(root)
            self.consent_path = root/'engine/portable-consent.json'
            super().__init__(controller.source, controller=controller, smoke=True)
            self.smoke = False
            self.page = 'launcher'

        def edit(self, key, value):
            if key == 'url' and not self.controller.busy:
                value = local_url(value)
                self.controller.restore({'url': value})
                self.remember(self.controller.selection)
                self.browser_attempted = False
            elif key in ('language', 'token'):
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
