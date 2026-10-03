"""Small native launcher: choose ComfyUI, prepare FreeVideo, open its workflow."""
import json
import locale
import os
from pathlib import Path
import sys
import time
import traceback
import webbrowser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import __version__
from . import branding
from .comfy_launcher_runtime import Controller
from .desktop_runtime import launcher_root, materialize_source
from .monitoring import save
from .terminal_ui import clean, duration
from .setup_progress import number, progress_text


class Launcher:
    def __init__(self, window, source=None, controller=None, update_client=None):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.window = tk, ttk, window
        self.source = Path(source or materialize_source())
        self.controller = controller or Controller(self.source)
        from .launcher_settings import Store
        self.settings_store = Store(launcher_root(), documents=launcher_root() / 'smoke-documents' if '--smoke-test' in sys.argv else None)
        self.settings_path = self.settings_store.primary
        settings = self.settings_store.read()
        self.zh = settings.get('language', (locale.getlocale()[0] or '').lower()).startswith('zh')
        self.comfy = tk.StringVar(value=settings.get('comfy', ''))
        self.mode = tk.StringVar(value='new' if settings.get('new_comfy', not bool(settings.get('comfy'))) else 'existing')
        default_folder = Path(os.environ.get('USERPROFILE') or os.environ.get('HOME') or launcher_root().parent) / 'FreeVideo'
        self.destination = tk.StringVar(value=settings.get('destination', str(default_folder)))
        self.engine = tk.StringVar(value=os.environ.get('FREEVIDEO_HOME') or settings.get('engine', ''))
        self.model_dirs = settings.get('model_dirs', [])
        if not isinstance(self.model_dirs, list) or any(not isinstance(v, str) for v in self.model_dirs):
            self.model_dirs = []
        if isinstance(settings.get('models'), str) and settings['models'].strip():
            self.model_dirs = list(dict.fromkeys(self.model_dirs + [settings['models']]))
        self.models = tk.StringVar()  # Legacy callers can still provide one folder.
        self.model_method = tk.StringVar(value=settings.get('model_method', 'reuse' if self.model_dirs else 'auto'))
        self.wizard_step = 0
        self.launch_selection = settings.get('installation')
        self.save_timer = None
        self.loading_settings = False
        self.saved_setup = settings.get('setup', {})
        self.last_model_groups = []
        self.error_text = ''
        self.python = tk.StringVar(value=settings.get('python', ''))
        self.url = tk.StringVar(value=settings.get('url', 'http://127.0.0.1:8188'))
        self.token = tk.StringVar()  # Never stored in settings, commands or receipts.
        self.separate = tk.BooleanVar(value=settings.get('separate', False))
        self.repair, self.accept = tk.BooleanVar(), tk.BooleanVar()
        self.status = tk.StringVar()
        self.details = tk.StringVar()
        self.meta = tk.StringVar()
        self.phase = tk.StringVar()
        self.overall_text = tk.StringVar()
        self.current_text = tk.StringVar()
        self.folder_label, self.folder_hint = tk.StringVar(), tk.StringVar()
        self.last_state = None
        self.opened = False
        self.browser_attempted = False
        self.browser_error = ''
        self.browser_address = tk.StringVar()
        self.closing = False
        self.started = None
        self.labels = []
        self.terminal_operation = None
        self.options = None
        self.options_inputs = []
        self.download_dialog = None
        self.download_probes = {}
        if self.controller.restore(dict(settings, engine=self.engine.get())):
            self.launch_selection = dict(self.controller.selection)
            self.wizard_step = 3
        elif self.saved_setup.get('action') == 'install' and self.saved_setup.get('status') in ('running', 'failed', 'cancelled'):
            self.wizard_step = 2
            self.controller.state = dict(status='cancelled')
        from .launcher_view import build
        build(self)
        self.updates = None
        if update_client is None and '--smoke-test' not in sys.argv:
            from .launcher_update import current_build, UpdateClient
            identity = current_build()
            if identity:
                update_client = UpdateClient(identity, launcher_root())
        if update_client is not None:
            from .launcher_update_ui import UpdateUI
            self.updates = UpdateUI(self, update_client, self.update_row)
        for variable in (self.mode, self.destination, self.comfy, self.engine, self.models, self.model_method, self.python, self.url, self.token, self.separate, self.repair):
            variable.trace_add('write', self.changed)
        window.protocol('WM_DELETE_WINDOW', self.close)
        window.bind('<Destroy>', self.cancel_timers, add='+')
        window.report_callback_exception = self.callback_error
        window.bind('<Return>', lambda _: self.act() if 'disabled' not in self.primary.state() else None)
        self.refresh()
        self.poll_timer = window.after(120, self.poll)
        self.update_timer = None
        if self.updates:
            self.update_timer = window.after(300, self.updates.start)
        self.compatibility_timer = window.after(500, self.compatibility_notice)
        self.reported_setup = None

    def t(self, en, zh):
        return zh if self.zh else en

    def label(self, parent, en, zh, **kwargs):
        widget = self.ttk.Label(parent, text=self.t(en, zh), **kwargs)
        self.labels.append((widget, en, zh))
        return widget

    def button(self, parent, en, zh, command):
        widget = self.ttk.Button(parent, text=self.t(en, zh), command=command)
        self.labels.append((widget, en, zh))
        return widget

    def language(self):
        self.zh = not self.zh
        for widget, en, zh in self.labels:
            if widget.winfo_exists():
                widget.configure(text=self.t(en, zh))
        self.refresh()
        self.queue_save()

    def values(self):
        return dict({name: getattr(self, name).get() for name in ('comfy', 'destination', 'engine', 'models', 'python', 'url', 'token', 'separate', 'repair')},
                    new_comfy=self.mode.get() == 'new', model_dirs=list(self.model_dirs), model_method=self.model_method.get())

    def model_download_link(self, component):
        from .model_guidance import show_component
        show_component(self, component)

    def select_model_source(self, _=None):
        from .download_settings import CHOICES, preference
        try:
            root = self.compatibility_root()
            if root is None:
                return
            preference(root, CHOICES[self.model_source.current()])
            self.model_source_status.configure(text=self.t('Saved. You can change sources during download too.', '已保存，下载过程中也可切换。'))
        except (OSError, ValueError) as error:
            self.model_source_status.configure(text=str(error))

    def refresh_model_choices(self):
        from .download_settings import CHOICES, read
        method = self.model_method.get()
        instructions = {
            'auto': ('FreeVideo selects compatible models, reuses matches in ComfyUI and downloads only missing files.',
                     '自动选择适配模型、复用 ComfyUI 内已有模型，只下载缺少的部分。'),
            'manual': ('Choose a component below to open its download links. After downloading, extract ZIP files and select their folder.',
                       '点击下方组件获取下载链接。下载完成后先解压 ZIP，再选择模型文件夹。'),
            'reuse': ('Choose your model folders. Subfolders are scanned automatically; the next page shows what is reusable and what is still missing.',
                      '选择已有模型的总目录，会自动扫描子文件夹。下一页会列出可复用和仍缺少的组件。'),
        }
        self.model_help.configure(text=self.t(*instructions[method]))
        if method == 'auto':
            self.model_source_row.pack(fill='x', pady=(8, 0), before=self.model_components)
        else:
            self.model_source_row.pack_forget()
        if method == 'reuse':
            self.model_library.pack(fill='x', pady=(12, 0))
            self.model_reuse_button.pack_forget()
        else:
            self.model_library.pack_forget()
            self.model_reuse_button.configure(text=self.t('Downloaded / already have models · choose folders…', '已下载或已有模型，选择文件夹…'))
            self.model_reuse_button.pack(fill='x', pady=(12, 0))
        self.model_source.configure(values=(self.t('Automatic', '自动选择'), 'Hugging Face', 'HF Mirror', 'ModelScope'))
        try:
            root = self.compatibility_root()
            self.model_source.current(CHOICES.index(read(root / 'download-settings.json')['source']) if root else 0)
        except (OSError, ValueError):
            self.model_source.current(0)
        for widget in self.model_method_buttons:
            widget.state(['disabled'] if self.controller.busy else ['!disabled'])

    def selected_folder(self):
        return self.destination if self.mode.get() == 'new' else self.comfy

    def changed(self, *_):
        if self.loading_settings:
            return
        self.queue_save()
        if self.controller.busy:
            return
        self.controller.selection = None
        self.controller.state = dict(status='idle')
        self.accept.set(False)
        self.reset_browser()
        self.last_model_groups = []
        if self.wizard_step == 2:
            self.wizard_step = 1
        elif self.wizard_step == 3:
            self.wizard_step = 0
        self.refresh()

    def browse(self, variable, file=False):
        from tkinter import filedialog
        parent = self.options if self.options and self.options.winfo_exists() else self.window
        path = (filedialog.askopenfilename if file else filedialog.askdirectory)(parent=parent)
        if path:
            variable.set(path)

    def show_options(self):
        if self.options and self.options.winfo_exists():
            self.options.lift(); return
        dialog = self.tk.Toplevel(self.window); self.options = dialog
        dialog.title(self.t('FreeVideo · Options', 'FreeVideo · 选项'))
        dialog.transient(self.window)
        dialog.geometry('640x620'); dialog.minsize(580, 560)
        body = self.ttk.Frame(dialog, padding=24); body.pack(fill='both', expand=True)
        self.options_inputs = []
        fixed_environment = getattr(self.controller, 'fixed_environment', False)
        for name, en, zh in (
            ('engine', 'Engine folder (blank = automatic)', '引擎目录（留空自动选择）'),
            ('python', 'ComfyUI Python (auto-detected when blank)', 'ComfyUI Python（留空自动检测）'),
            ('url', 'ComfyUI address', 'ComfyUI 地址')):
            if fixed_environment and name != 'url':
                continue
            self.label(body, en, zh).pack(anchor='w', pady=(0, 4))
            row = self.ttk.Frame(body); row.pack(fill='x', pady=(0, 10))
            entry = self.ttk.Entry(row, textvariable=getattr(self, name), show='•' if name == 'token' else '')
            entry.pack(side='left', fill='x', expand=True)
            self.options_inputs.append(entry)
            if name in ('engine', 'models', 'python'):
                browse = self.button(row, '…', '…', lambda n=name: self.browse(getattr(self, n), n == 'python'))
                browse.pack(side='right', padx=(6, 0)); self.options_inputs.append(browse)
        for variable, en, zh in (
            (self.separate, 'Use a separate ComfyUI environment', '使用独立 ComfyUI 环境'),
            (self.repair, 'Repair the engine environment', '修复引擎环境')):
            if fixed_environment:
                continue
            check = self.ttk.Checkbutton(body, variable=variable, text=self.t(en, zh))
            self.labels.append((check, en, zh)); check.pack(anchor='w', pady=(3, 3))
            self.options_inputs.append(check)
        self.button(body, 'Done', '完成', dialog.destroy).pack(side='bottom', anchor='e', pady=(10, 0))
        compatibility = self.button(body, 'Compatibility settings', '兼容性设置', self.compatibility_settings)
        compatibility.pack(side='bottom', anchor='w'); self.options_inputs.append(compatibility)
        self.button(body, 'Download sources', '下载源', self.download_settings).pack(side='bottom', anchor='w')
        self.label(body, 'Saved settings: ', '设置保存位置：', style='Muted.TLabel').pack(anchor='w', pady=(8, 0))
        self.ttk.Label(body, text=str(self.settings_path), style='Muted.TLabel', wraplength=580).pack(fill='x')
        for widget in self.options_inputs:
            widget.state(['disabled'] if self.controller.busy else ['!disabled'])

    def download_settings(self):
        from .download_settings import Probe
        from .download_settings_ui import DownloadSettings
        from tkinter import messagebox
        try:
            selected = self.controller.selection
            root = Path(selected['engine']) if selected else self.compatibility_root()
            if root is None:
                raise ValueError(self.t('Choose the installation folder first.', '请先选择安装位置。'))
            root = root.resolve()
            old = self.download_dialog
            if old and old.window.winfo_exists():
                if old.root == root:
                    old.window.lift(); return
                old.window.destroy()
            probe = self.download_probes.setdefault(root, Probe())
            self.download_dialog = DownloadSettings(self.window, root, self.zh, probe,
                token=self.token, busy=lambda: self.controller.busy)
        except (OSError, ValueError) as error:
            messagebox.showerror('FreeVideo', str(error), parent=self.window)

    def compatibility_root(self):
        if self.engine.get().strip():
            return Path(self.engine.get()).expanduser()
        if self.mode.get() == 'new':
            return Path(self.destination.get())/'FreeVideo-engine' if self.destination.get().strip() else None
        from .comfy_launcher_runtime import layout
        if self.comfy.get().strip():
            return Path(layout(self.comfy.get())['root'])/'FreeVideo-engine'
        return None

    def compatibility_notice(self):
        from .compatibility_ui import notify
        try:
            root = self.compatibility_root()
        except (OSError, ValueError):
            return
        if root is not None:
            notify(self.window, root, self.zh)

    def compatibility_settings(self):
        from .compatibility_ui import open_settings
        from tkinter import messagebox
        try:
            root = self.compatibility_root()
            if root is None or not (root/'machine.json').is_file():
                raise ValueError(self.t('Choose an installed FreeVideo engine first.', '请先选择已安装的 FreeVideo 引擎。'))
            open_settings(self.window, root, self.zh)
        except (OSError, ValueError) as error:
            messagebox.showerror('FreeVideo', str(error), parent=self.window)

    def save_settings(self):
        if self.save_timer is not None:
            self.window.after_cancel(self.save_timer)
            self.save_timer = None
        row = self.controller.state
        if self.launch_selection and self.mode.get() == 'existing':
            selected = self.launch_selection
            if (self.comfy.get() == selected.get('root') and self.engine.get() == selected.get('engine')):
                from .comfy_launcher_runtime import local_url
                try:
                    selected['url'] = local_url(self.url.get())
                    if self.python.get() and Path(self.python.get()).is_file():
                        selected['python'] = self.python.get()
                except ValueError:
                    pass  # Keep an editable draft without breaking the saved launcher.
        if row.get('action') == 'install':
            self.saved_setup = dict(status=row.get('status', ''), action='install',
                phase=row.get('overall', {}).get('label', ''))
        written, failures = self.settings_store.write(self.values(), 'zh' if self.zh else 'en',
                                                     self.launch_selection, self.saved_setup)
        self.settings_path = written[0]

    def queue_save(self):
        if self.save_timer is not None:
            self.window.after_cancel(self.save_timer)
        self.save_timer = self.window.after(200, self.persist_settings)

    def persist_settings(self):
        try:
            self.save_settings()
        except (OSError, ValueError) as error:
            self.callback_error(type(error), error, error.__traceback__)

    def remember_installation(self, selection):
        if not selection or not selection.get('ready'):
            return
        self.launch_selection = dict(selection)
        self.loading_settings = True
        try:
            for name, key in (('comfy', 'root'), ('engine', 'engine'), ('python', 'python'), ('url', 'url')):
                getattr(self, name).set(selection.get(key) or '')
            self.mode.set('existing')
            self.separate.set(selection.get('separate', False))
            self.repair.set(False)
        finally:
            self.loading_settings = False
        self.persist_settings()

    def show_launcher(self):
        if self.controller.busy or not self.launch_selection:
            return
        if not self.controller.restore({'installation': self.launch_selection}):
            self.controller.state = dict(status='failed', error=self.t(
                'The saved installation is unavailable. Return to Installation settings to locate or repair it.',
                '保存的安装暂时不可用，请返回安装设置重新定位或修复。'))
        self.wizard_step = 3
        self.refresh()

    def update_engine(self):
        if self.controller.busy or not self.launch_selection:
            return
        self.remember_installation(self.launch_selection)
        self.wizard_step = 2
        self.controller.state = dict(status='idle')
        self.act()

    def add_model_folder(self, path=None):
        if self.controller.busy:
            return
        if path is None:
            from tkinter import filedialog
            path = filedialog.askdirectory(parent=self.window, title=self.t('Choose a model folder', '选择模型总目录'))
        if path:
            from .local_models import library_roots
            try:
                self.model_dirs = library_roots(self.model_dirs + [str(path)])
                self.model_method.set('reuse')
                self.changed()
            except (OSError, ValueError) as error:
                self.status.set(str(error))

    def remove_model_folders(self):
        if self.controller.busy:
            return
        selected = set(self.model_list.curselection())
        self.model_dirs = [p for i, p in enumerate(self.model_dirs) if i not in selected]
        self.changed()

    def back(self):
        if not self.controller.busy and self.wizard_step:
            if self.wizard_step == 3:
                self.wizard_step = 0
                self.controller.state = dict(status='idle')
            else:
                self.wizard_step -= 1
            self.accept.set(False)
            self.refresh()

    def act(self):
        if self.controller.busy:
            return
        state = self.controller.state.get('status')
        try:
            if self.wizard_step == 3:
                self.terminal.show()
                self.reset_browser()
                self.save_settings()
                self.controller.run('launch', {'installation': self.launch_selection})
                self.refresh()
                return
            if self.wizard_step == 0:
                from .comfy_launcher_runtime import layout, new_layout
                if self.mode.get() == 'new':
                    new_layout(self.destination.get())
                else:
                    layout(self.comfy.get())
                self.wizard_step = 1
                self.refresh()
                return
            self.terminal.show()
            if self.wizard_step == 2 and state == 'review':
                if not self.accept.get() or self.controller.state.get('errors'):
                    return
                self.reset_browser()
                self.controller.run('install', True)
            elif self.wizard_step == 2 and state in ('restart-required', 'open'):
                self.reset_browser()
                self.controller.run('connect')
            else:
                if not self.selected_folder().get().strip():
                    self.browse(self.selected_folder())
                    if not self.selected_folder().get().strip():
                        return
                values = self.values()
                self.save_settings()
                self.last_model_groups = []
                self.controller.run('inspect', values)
                self.wizard_step = 2
            self.started = time.monotonic()
            self.bar.stop(); self.bar.configure(mode='determinate', maximum=1, value=0)
            self.overall_bar.stop(); self.overall_bar.configure(mode='determinate', maximum=1, value=0)
            self.refresh()
        except (ValueError, OSError) as error:
            self.status.set(str(error))

    def reset_browser(self):
        self.opened = self.browser_attempted = False
        self.browser_error = ''

    def open_browser(self):
        if self.controller.state.get('status') != 'open':
            return
        from .windows_ux import open_browser
        self.browser_attempted = True
        self.browser_error = ''
        address = self.controller.state['url']
        if self.zh:
            parsed = urlsplit(address)
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query['freevideo_lang'] = 'zh'
            address = urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                                  urlencode(query), parsed.fragment))
        try:
            self.opened = bool(open_browser(address))
            if not self.opened:
                raise OSError(self.t('The system did not accept the browser request.', '系统未接受打开浏览器的请求。'))
        except Exception as error:
            # Browser failures must not terminate the GUI poll loop or claim
            # the server failed. Keep the URL and a one-click manual retry.
            self.opened = False
            self.browser_error = self.t('ComfyUI is ready, but the browser did not open. Use Open browser or copy the address.\n',
                'ComfyUI 已就绪，但浏览器未能打开。请点击“打开浏览器”，或复制地址手动打开。\n') + str(error)
        self.refresh()

    def copy_browser_address(self):
        self.window.clipboard_clear()
        self.window.clipboard_append(self.browser_address.get())

    def create_shortcut(self):
        if self.controller.busy or not self.controller.selection:
            return
        self.controller.run('shortcut', self.controller.state.get('status'))
        self.refresh()

    def refresh(self):
        from .launcher_view import show_page
        row = self.controller.state
        state, busy = row.get('status', 'idle'), self.controller.busy
        fresh = self.mode.get() == 'new'
        self.entry.configure(textvariable=self.selected_folder())
        self.folder_label.set(self.t('Install location', '安装位置') if fresh else self.t('Your ComfyUI folder', 'ComfyUI 安装目录'))
        self.folder_hint.set(self.t('ComfyUI, Python and FreeVideo install here.', 'ComfyUI、Python 和 FreeVideo 都安装在这里。') if fresh else
                             self.t('Your ComfyUI folder, or its portable package folder.', '选择 ComfyUI 或整合包所在文件夹。'))
        headings = [('Set up FreeVideo', '开始使用 FreeVideo'), ('Get your models', '准备模型'),
                    ('Ready to install', '准备安装'), ('FreeVideo', 'FreeVideo')]
        # One line each. What a control does is stated once, next to the control.
        descriptions = [('Choose how you want to use ComfyUI.', '选择 ComfyUI 的使用方式。'),
                        ('Download automatically, download yourself, or reuse existing models.', '自动下载、手动下载，或复用已有模型。'),
                        ('Review, then install.', '确认后开始安装。'),
                        ('Start ComfyUI and open the FreeVideo workflow.', '启动 ComfyUI 并打开 FreeVideo 工作流。')]
        if self.wizard_step == 3:
            descriptions[3] = {
                'running': ('Starting ComfyUI…', '正在启动 ComfyUI…'),
                'open': ('ComfyUI is ready. Open FreeVideo in your browser.', 'ComfyUI 已就绪，可在浏览器中打开 FreeVideo。'),
                'failed': ('Could not start. Details below.', '启动未完成，原因见下方。'),
                'restart-required': ('Restart your running ComfyUI, then connect again.', '请重启正在运行的 ComfyUI，然后重新连接。'),
                'cancelled': ('Startup stopped. Your installation is retained.', '已停止启动，安装文件已保留。'),
            }.get(state, descriptions[3])
            if busy and row.get('action') == 'shortcut':
                descriptions[3] = ('Creating desktop shortcut…', '正在创建桌面快捷方式…')
        if self.wizard_step == 2:
            headings[2], descriptions[2] = {
                'running': (('Finding your models', '正在查找模型'), ('Scanning folders…', '正在扫描模型目录…')) if row.get('action') == 'inspect' else
                           (('Installing FreeVideo', '正在安装 FreeVideo'), ('Preparing models and dependencies.', '正在准备模型和运行环境。')),
                'failed': (('Installation paused', '安装已暂停'), ('Files retained. Details below.', '文件已保留，原因见下方。')),
                'cancelled': (('Installation paused', '安装已暂停'), ('Downloaded files are reused.', '已下载文件会复用。')),
                'open': (('You’re ready', '准备完成'), ('ComfyUI is ready. Open FreeVideo in your browser.', 'ComfyUI 已就绪，可在浏览器中打开 FreeVideo。')),
                'restart-required': (('One last step', '最后一步'), ('Restart ComfyUI, then connect below.', '请重启 ComfyUI，然后点击下方连接。')),
            }.get(state, (headings[2], descriptions[2]))
        self.heading.configure(text=self.t(*headings[self.wizard_step]))
        self.status.set(self.t(*descriptions[self.wizard_step]))
        self.model_list.delete(0, 'end')
        for folder in self.model_dirs:
            self.model_list.insert('end', folder)
        # As tall as the folders it holds, so an empty or short list is not
        # five rows of blank well.
        self.model_list.configure(height=max(2, min(6, len(self.model_dirs))))
        self.error_text = '\n'.join(str(e) for e in row.get('errors', []))
        if row.get('error'):
            self.error_text += ('\n' if self.error_text else '') + str(row['error'])
        if self.browser_error:
            self.error_text += ('\n' if self.error_text else '') + self.browser_error
        if row.get('shortcut', {}).get('status') == 'failed':
            self.error_text += ('\n' if self.error_text else '') + self.t(
                row['shortcut']['error'], '安装已完成，但无法创建桌面快捷方式。可继续使用启动器。\n' + row['shortcut']['error'])
        if self.error_text:
            self.copy_error_button.pack(side='left', padx=6)
            self.review_card.pack(fill='x', pady=12)
        else:
            self.copy_error_button.pack_forget(); self.review_card.pack_forget()
        self.review.configure(state='normal'); self.review.delete('1.0', 'end')
        self.review.insert('1.0', self.error_text)
        # Only as tall as the retained text, so a one-line failure is one line.
        self.review.configure(height=min(4, max(1, len(self.error_text.splitlines()))), state='disabled')
        self.launch_error.configure(text=self.error_text)
        if row.get('engine_update_available'):
            self.engine_update_row.pack(fill='x', pady=(12, 0))
        else:
            self.engine_update_row.pack_forget()
        self.engine_update_button.state(['disabled'] if busy else ['!disabled'])
        selected = self.launch_selection or {}
        self.launch_paths.configure(text='ComfyUI · ' + selected.get('root', '') + '\n' +
            self.t('Engine', '引擎') + ' · ' + selected.get('engine', ''))
        self.browser_address.set(row.get('url', '') if state == 'open' else '')
        if state == 'open':
            self.browser_controls.pack(fill='x', pady=(16, 0))
        else:
            self.browser_controls.pack_forget()
        self.browser_button.state(['disabled'] if busy else ['!disabled'])
        self.shortcut_button.state(['!disabled'] if not busy and self.controller.selection and
            self.controller.selection.get('ready') else ['disabled'])
        shortcut = row.get('shortcut', {})
        self.shortcut_status.configure(text=self.t('Desktop shortcut ready', '桌面快捷方式已就绪')
            if shortcut.get('status') in ('created', 'present') else '')
        if self.wizard_step == 3:
            action = self.t('Start & open FreeVideo', '启动并打开 FreeVideo')
        elif self.wizard_step < 2:
            action = self.t('Next →', '下一步 →') if self.wizard_step == 0 else self.t('Scan & continue →', '扫描并继续 →')
        else:
            action = self.t('Install & open ComfyUI', '安装并打开 ComfyUI') if state == 'review' else self.t('Connect', '连接') if state == 'restart-required' else self.t('Open FreeVideo', '打开 FreeVideo') if state == 'open' else self.t('Check & resume', '检查并继续')
        self.primary.configure(text=action)
        if busy:
            self.primary.configure(text=self.t('Starting…', '启动中…') if self.wizard_step == 3 else
                self.t('Checking…', '检查中…') if row.get('action') == 'inspect' else self.t('Installing…', '安装中…'))
        allowed = not busy and (self.wizard_step != 2 or state != 'review' or (self.accept.get() and not row.get('errors')))
        self.primary.state(['!disabled'] if allowed else ['disabled'])
        self.back_button.state(['disabled'] if busy or self.wizard_step == 0 else ['!disabled'])
        self.back_button.configure(text=self.t('Installation settings', '安装设置') if self.wizard_step == 3 else self.t('Back', '上一步'))
        if self.launch_selection and self.wizard_step != 3:
            self.launcher_button.pack(side='left', padx=12)
            self.launcher_button.state(['disabled'] if busy else ['!disabled'])
        else:
            self.launcher_button.pack_forget()
        self.stop_button.configure(text=self.t('Stop startup', '停止启动') if self.wizard_step == 3 else self.t('Pause installation', '暂停安装'))
        for widget in (self.entry, self.browse_button, self.add_model_button, self.remove_model_button, *self.mode_buttons, *self.options_inputs):
            if widget.winfo_exists():
                widget.state(['disabled'] if busy else ['!disabled'])
        if busy:
            self.stop_button.pack(side='right', padx=(0, 10))
        else:
            self.stop_button.pack_forget()
        self.consent.configure(text=self.t('I accept the installation plan and model / toolkit licenses.', '我同意本安装计划及模型／工具许可证。'))
        if state == 'review' and not busy and self.wizard_step == 2:
            self.consent.pack(anchor='w', pady=(0, 12), before=self.update_row)
        else:
            self.consent.pack_forget()
        plan = row.get('plan', {})
        hardware = plan.get('inventory', {}).get('hardware', {})
        summary = []
        if plan.get('disk_mode') == 'extreme':
            summary.append(self.t('Space saver (automatic)', '极限省空间（自动启用）'))
        if hardware.get('gpu_name'):
            summary.append(hardware['gpu_name'])
        if 'model_download_bytes' in plan:
            summary.append(self.t('To download ', '需下载 ') + '%.1f GiB' % (plan['model_download_bytes']/2**30))
        if state == 'review' and row.get('disks'):
            needed = sum(d.get('needed_bytes', 0) for d in row['disks'])
            summary.append(self.t('Peak disk space ~', '预计磁盘峰值 ~') + '%.1f GiB' % (needed/2**30))
        self.summary.configure(text=' · '.join(summary))
        if state == 'review':
            self.progress_card.pack_forget()
        elif not self.progress_card.winfo_manager():
            self.progress_card.pack(fill='x', before=self.review_card if self.review_card.winfo_manager() else None)
        self.render_models(row)
        self.refresh_model_choices()
        show_page(self)

    def render_models(self, row):
        groups = row.get('task', {}).get('model_groups') or row.get('model_groups')
        if groups:
            self.last_model_groups = groups
        groups = self.last_model_groups or row.get('plan', {}).get('model_groups', [])
        by_name = {item['id']: item for item in groups}
        ready = row.get('selection', {}).get('ready') or row.get('status') == 'open'
        for name, card in self.model_cards.items():
            card.update(by_name.get(name), ready=ready)

    def copy_error(self):
        if self.error_text:
            self.window.clipboard_clear()
            self.window.clipboard_append(self.error_text)

    def show_details(self):
        dialog = self.tk.Toplevel(self.window); dialog.title(self.t('FreeVideo · Details', 'FreeVideo · 详情'))
        dialog.configure(bg=branding.BACKGROUND)
        dialog.geometry('720x440')
        text = self.tk.Text(dialog, wrap='word', padx=16, pady=16, font=('Consolas', 10),
                            bg=branding.SURFACE, fg=branding.TEXT, relief='flat',
                            selectbackground=branding.RAISED, selectforeground=branding.TEXT)
        text.pack(fill='both', expand=True)
        row = self.controller.state
        safe = {k: v for k, v in row.items() if k != 'host'}
        text.insert('1.0', json.dumps(safe, ensure_ascii=False, indent=2)); text.configure(state='disabled')
        self.button(dialog, 'Model licenses', '模型许可证', lambda: webbrowser.open('https://huggingface.co/OpenVDN/vdn-minimax-h3-edge/blob/main/LICENSE')).pack(pady=8)

    def poll(self):
        if self.poll_timer is not None:
            self.window.after_cancel(self.poll_timer)
            self.poll_timer = None
        row = self.controller.state
        if self.controller.busy and row.get('action') != 'shortcut' and id(self.controller.thread) != self.terminal_operation:
            self.terminal_operation = id(self.controller.thread)
            self.terminal.show()
        self.terminal.poll()
        task = row.get('task', {})
        progress = task.get('progress', {})
        self.render_progress(row, task, progress)
        self.render_models(row)
        if not self.controller.busy:
            self.bar.stop()
            if self.closing:
                self.controller.close()
                self.window.destroy(); return
            selected = row.get('selection')
            if (selected and selected.get('ready') and
                    (row.get('deployed') or row.get('status') in ('open', 'restart-required')) and
                    row.get('status') != 'running'):
                if selected != self.launch_selection:
                    self.remember_installation(selected)
                if row.get('action') == 'install' or row.get('status') in ('open', 'restart-required'):
                    self.wizard_step = 3
            if row.get('status') == 'open':
                self.bar.configure(mode='determinate', maximum=1, value=1)
                if not self.browser_attempted:
                    self.open_browser()
        # Include busy separately: the task's final state arrives just before
        # the thread exits, and the action must become enabled at that boundary.
        key = (row.get('status'), self.controller.busy, row.get('error'), id(row.get('plan')), row.get('overall', {}).get('label'))
        if key != self.last_state:
            self.last_state = key
            if row.get('action') == 'install':
                self.queue_save()
            self.refresh()
        if row.get('action') == 'install' and not self.controller.busy and self.started and row.get('status') in ('open','failed','cancelled','restart-required'):
            event_key = 'setup-%s-%s' % (self.started, row['status'])
            if event_key != self.reported_setup:
                self.reported_setup = event_key
                from .installation_diagnostics import write
                write(self.compatibility_root(), time.time()-(time.monotonic()-self.started),
                    {'summary': {'status': 'complete' if row['status'] in ('open','restart-required') else 'incomplete',
                    'hardware': row.get('plan', {}).get('inventory', {}).get('hardware', {}),
                    'stages': [{'stage':'installation', 'seconds':time.monotonic()-self.started}]},
                    'request': {'exception': row.get('exception', []), 'phase': self.controller.section or 'installation'},
                    'log_tails': {'installation': row.get('error', '')}})
        self.poll_timer = self.window.after(120, self.poll)

    def render_progress(self, row, task, progress):
        from .launcher_copy import display
        overall = row.get('overall') or task.get('phase_progress') or dict(done=0, total=1)
        total, done = overall.get('total'), overall.get('done')
        valid = number(total) and total > 0 and number(done) and done <= total
        if valid:
            self.overall_bar.stop()
            self.overall_bar.configure(mode='determinate', maximum=total, value=done)
            self.overall_text.set('%d / %d ' % (done, total) + self.t('stages', '阶段') +
                                  ' · %.0f%%' % (100 * done / total))
        else:
            # An unmeasured plan sweeps rather than showing an empty track.
            self.overall_bar.start()
            self.overall_text.set('')
        label = clean(progress.get('label') or overall.get('label') or self.t('Checking configuration', '正在检查配置'))
        self.phase.set(display(label, self.zh))
        self.current_text.set(progress_text(progress, self.zh))
        total, done = progress.get('total'), progress.get('done')
        valid = number(total) and total > 0 and number(done) and done <= total
        self.bar.stop()
        self.bar.configure(mode='determinate', maximum=total if valid else 1, value=done if valid else 0)
        details = [display(progress.get('detail', ''), self.zh)]
        others = [display(p.get('label', ''), self.zh) for p in task.get('active_tasks', [])[1:]]
        if others:
            details.append('+ ' + ' / '.join(others[:2]))
        self.details.set(' · '.join(p for p in details if p))
        if self.started:
            self.meta.set(self.t('Elapsed ', '已用 ') + duration(time.monotonic() - self.started))

    def close(self):
        try:
            self.persist_settings()
        finally:
            self.controller.close()
        for timer in (self.update_timer, self.compatibility_timer):
            if timer is not None:
                self.window.after_cancel(timer)
        if self.updates:
            self.updates.stop()
        if self.controller.busy:
            self.closing = True
            self.controller.cancel()
            self.status.set(self.t('Stopping installation; retaining files…', '正在停止安装，保留文件…'))
        else:
            if self.poll_timer is not None:
                self.window.after_cancel(self.poll_timer)
                self.poll_timer = None
            self.bar.stop()
            self.window.destroy()

    def cancel_timers(self, event):
        if event.widget != self.window:
            return
        self.controller.close()
        for name in ('poll_timer', 'save_timer', 'compatibility_timer', 'update_timer', 'layout_timer'):
            timer = getattr(self, name, None)
            if timer is not None:
                try:
                    self.window.after_cancel(timer)
                except self.tk.TclError:
                    pass
                setattr(self, name, None)

    def callback_error(self, kind, value, trace):
        path = launcher_root() / 'errors' / ('launcher-' + str(time.time_ns()) + '.log')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(traceback.format_exception(kind, value, trace)), encoding='utf-8')
        self.status.set(str(value))


def legacy_main():
    if '--smoke-test' not in sys.argv:
        from .launcher_update import current_build, forward_approved
        identity = current_build()
        if identity and forward_approved(identity, launcher_root()):
            return
    import tkinter as tk
    if os.name == 'nt':
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass
        lib = ctypes.windll.kernel32
        lib.GetConsoleWindow.restype = ctypes.c_void_p
        if not lib.GetConsoleWindow():
            lib.AllocConsole(); ctypes.windll.user32.ShowWindow(ctypes.c_void_p(lib.GetConsoleWindow()), 0)
    window = tk.Tk()
    app = Launcher(window)
    if '--smoke-test' in sys.argv:
        import queue
        from .desktop_runtime import Runner, check_launcher_payload, check_launcher_reopen
        destination = Path(sys.argv[sys.argv.index('--smoke-test') + 1])
        destination.mkdir(parents=True, exist_ok=True)
        try:
            payload = check_launcher_payload(app.source, destination)
            payload['reopen'] = check_launcher_reopen(destination)
            for step in range(len(app.pages)):
                app.wizard_step = step
                app.refresh(); window.update_idletasks()
                if not app.pages[step].winfo_ismapped():
                    raise ValueError('Packaged setup wizard page did not open')
            payload['wizard_pages'] = 3
            payload['launcher_page'] = True
            app.wizard_step = 0
            app.refresh()
            # Exercise frozen-only imports before any real installation exists.
            app.engine.set(str(destination / 'engine'))
            app.download_settings()
            if app.download_dialog is None or not app.download_dialog.window.winfo_exists():
                raise ValueError('Packaged download controls could not open')
            payload['download_controls'] = True
            app.download_dialog.window.destroy()
            from .launcher_update import current_build
            build = current_build()
            if getattr(sys, 'frozen', False) and build is None:
                raise ValueError('Packaged launcher update identity is missing')
        except Exception:
            save(destination / 'smoke.json', dict(success=False, error=traceback.format_exc(),
                 interface='ComfyUI launcher', model_modules_imported=[]))
            window.destroy()
            return
        events = queue.Queue(); runner = Runner(app.source, events, destination / 'help')
        runner.start('help', destination / 'engine', ['--help'])
        deadline = time.monotonic() + 240
        def finish():
            window.update_idletasks()
            if runner.busy and time.monotonic() < deadline:
                window.after(100, finish); return
            result = None
            while not events.empty():
                kind, value = events.get_nowait()
                if kind == 'done': result = value
            imported = [p for p in ('torch', 'triton', 'transformers') if p in sys.modules]
            save(destination / 'smoke.json', dict(success=bool(result and result['status'] == 'complete' and not imported),
                 task=result, interface='ComfyUI launcher', window_geometry=window.geometry(),
                 build=build,
                 source=str(app.source), payload=payload, model_modules_imported=imported))
            if runner.busy: runner.cancel()
            window.destroy()
        window.after(100, finish)
    window.mainloop()


def main():
    from .modern_launcher import main as launch
    launch()


if __name__ == '__main__':
    main()
