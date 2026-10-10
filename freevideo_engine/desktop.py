"""Native desktop views. Rendering is separate from installer/test orchestration."""
import json
import locale
import os
from pathlib import Path
import queue
import sys
import time
import traceback
import uuid
import webbrowser

from . import __version__, branding
from .desktop_runtime import Runner, launcher_root, materialize_source, preflight_json
from .monitoring import save
from .system import install_root, windows
from .terminal_ui import duration, clipped
from .widgets import Card, Progress, Stat


class Desktop:
    def __init__(self, window, source=None):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.window = tk, ttk, window
        self.source = Path(source or materialize_source())
        self.events = queue.Queue()
        self.runner = Runner(self.source, self.events)
        self.plan = None
        self.last_log = None
        self.closing = False
        self.running = False
        self.progress_rows = {}
        self.zh = (locale.getlocale()[0] or '').lower().startswith('zh')
        try:
            settings = json.loads((launcher_root() / 'settings.json').read_text(encoding='utf-8'))
            if not isinstance(settings, dict):
                settings = {}
        except (OSError, ValueError):
            settings = {}
        if settings.get('language') in ('en', 'zh'):
            self.zh = settings['language'] == 'zh'
        saved_root = settings.get('root') if isinstance(settings.get('root'), str) else None
        self.install_path = tk.StringVar(value=str(os.environ.get('FREEVIDEO_HOME') or saved_root or install_root()))
        self.reuse_path = tk.StringVar(value=settings.get('reuse_models') if isinstance(settings.get('reuse_models'), str) else '')
        self.copy_existing = tk.BooleanVar(value=settings.get('copy_existing_models') is True)
        self.allow_restart = tk.BooleanVar(value=False)
        self.setup_retry = False
        self.accept = tk.BooleanVar(value=False)
        self.resolution = tk.StringVar(value='1344 × 768')
        self.duration = tk.StringVar(value='10.125')
        self.status = tk.StringVar(value=self.t('Ready', '准备就绪'))
        self.note = tk.StringVar()
        self.phase_status = tk.StringVar(value=self.t('Installation progress', '安装进度'))
        self.resource = tk.StringVar(value=self.t('Detect your hardware to see the installation plan.', '先检测配置，查看安装计划。'))
        self.buttons = []
        self.translated = []
        window.title('FreeVideo')
        from .windows_ux import window_size
        width, height = window_size(window)
        window.geometry('%dx%d' % (width, height))
        window.minsize(min(760, width), min(560, height))
        style = branding.theme(window)
        body = ttk.Frame(window, padding=28)
        body.pack(fill='both', expand=True)
        header = ttk.Frame(body)
        header.pack(fill='x', pady=(0, 20))
        self.logo = branding.wordmark(header)
        self.logo.pack(side='left')
        ttk.Label(header, text='  ENGINE  /  ' + __version__, style='Muted.TLabel').pack(side='left', pady=(12, 0))
        self.button(header, '中文', 'EN', self.language, managed=False).configure(style='Quiet.TButton')
        self.translated[-1][0].pack(side='right')
        self.tabs = ttk.Notebook(body)
        self.tabs.pack(fill='both', expand=True)
        self.pages = []
        for en, zh in [('Setup', '安装'), ('Generate', '生成'), ('Test & tune', '测试与优化'), ('Reports', '报告')]:
            page = ttk.Frame(self.tabs, padding=22)
            self.tabs.add(page, text=self.t(en, zh))
            self.pages.append((page, en, zh))
        self.setup_page(self.pages[0][0])
        self.generate_page(self.pages[1][0])
        self.test_page(self.pages[2][0])
        self.reports_page(self.pages[3][0])
        footer = ttk.Frame(body, padding=(0, 20, 0, 0))
        # Reserve task controls before the expanding notebook, so a second
        # parallel task cannot be clipped below the window's bottom edge.
        footer.pack(side='bottom', fill='x', before=self.tabs)
        status_line = ttk.Frame(footer)
        status_line.pack(fill='x')
        ttk.Label(status_line, textvariable=self.status, wraplength=650).pack(side='left', fill='x', expand=True)
        self.cancel_button = self.button(status_line, 'Stop', '停止', self.cancel, managed=False)
        self.cancel_button.pack(side='right')
        self.cancel_button.state(['disabled'])
        ttk.Label(footer, textvariable=self.phase_status, style='Muted.TLabel').pack(anchor='w', pady=(8, 0))
        self.progress = Progress(footer, background=branding.BACKGROUND, height=branding.space(2))
        self.progress.configure(mode='indeterminate')
        self.progress.pack(fill='x', pady=(branding.space(2), branding.space(2)))
        self.task_area = ttk.Frame(footer)
        self.task_area.pack(fill='x')
        self.button(footer, 'Show / hide details', '展开／收起详情', self.toggle_log, managed=False).pack(anchor='w')
        # Details must not take height away from the setup viewport. On small
        # Windows desktops, two task cards plus inline logs could unmap the
        # canvas completely; scrolling an unmapped canvas cannot reveal actions.
        self.log_window = tk.Toplevel(window)
        self.log_window.withdraw()
        self.log_window.title(self.t('FreeVideo · Details', 'FreeVideo · 详情'))
        self.log_window.geometry('%dx%d' % (min(860, width), min(440, height)))
        self.log_window.transient(window)
        self.log_window.protocol('WM_DELETE_WINDOW', self.log_window.withdraw)
        self.log_frame = ttk.Frame(self.log_window, padding=12)
        self.log_frame.pack(fill='both', expand=True)
        self.log = tk.Text(self.log_frame, height=6, font=(branding.MONO, branding.MICRO),
                           bg=branding.SURFACE, fg=branding.TEXT, relief='flat', wrap='word',
                           padx=branding.space(3), pady=branding.space(2), state='disabled')
        scrollbar = ttk.Scrollbar(self.log_frame, command=self.log.yview,
                                  style='Wizard.Vertical.TScrollbar')
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        self.compact_layout = None
        def resize_layout(event):
            if event.widget != window:
                return
            compact = event.height < 760
            if compact == self.compact_layout:
                return
            self.compact_layout = compact
            # Reduce whitespace, not text size or the space needed by controls.
            # Real Windows/Linux fonts can otherwise leave a viewport shorter
            # than the installation button, even with details in another window.
            body.configure(padding=12 if compact else 28)
            header.pack_configure(pady=(0, 8 if compact else 20))
            footer.configure(padding=(0, 8 if compact else 20, 0, 0))
            for page, _, _ in self.pages:
                page.configure(padding=12 if compact else 22)
        window.bind('<Configure>', resize_layout, add='+')
        window.protocol('WM_DELETE_WINDOW', self.close)
        window.bind('<Destroy>', self.cancel_poll, add='+')
        window.report_callback_exception = self.callback_error
        self.install_path.trace_add('write', self.root_changed)
        self.reuse_path.trace_add('write', self.root_changed)
        self.copy_existing.trace_add('write', self.root_changed)
        self.allow_restart.trace_add('write', self.root_changed)
        self.update_buttons()
        self.poll_timer = window.after(100, self.poll)

    def t(self, english, chinese):
        return chinese if self.zh else english

    def label(self, parent, english, chinese, **kwargs):
        widget = self.ttk.Label(parent, text=self.t(english, chinese), **kwargs)
        self.translated.append((widget, english, chinese))
        return widget

    def button(self, parent, english, chinese, command, primary=False, managed=True):
        widget = self.ttk.Button(parent, text=self.t(english, chinese), command=command,
                                 style='Primary.TButton' if primary else 'TButton')
        self.translated.append((widget, english, chinese))
        if managed:
            self.buttons.append(widget)
        return widget

    def scrolled(self, page, index):
        """Every tab scrolls. A second live task grows the footer, which used to
        squeeze the notebook and clip whichever tab was open; only Setup could
        scroll, so the Generate tab lost its own action buttons.

        The scrollbar shows itself only while the content does not fit.
        """
        canvas = self.tk.Canvas(page, highlightthickness=0, background=branding.BACKGROUND)
        scroll = self.ttk.Scrollbar(page, orient='vertical', command=canvas.yview,
                                    style='Wizard.Vertical.TScrollbar')
        canvas.pack(side='left', fill='both', expand=True)
        def reach(first, last):
            scroll.set(first, last)
            if float(first) == 0 and float(last) == 1:
                scroll.pack_forget()
            elif not scroll.winfo_manager():
                scroll.pack(side='right', fill='y', before=canvas)
        canvas.configure(yscrollcommand=reach)
        content = self.ttk.Frame(canvas, padding=(0, 0, branding.space(3), branding.space(3)))
        item = canvas.create_window(0, 0, anchor='nw', window=content)
        content.bind('<Configure>', lambda _: canvas.configure(scrollregion=canvas.bbox('all')))
        def resize(event):
            canvas.itemconfigure(item, width=event.width)
            if index == 0 and hasattr(self, 'plan_text'):
                self.plan_text.configure(wraplength=max(280, event.width-branding.space(6)))
        canvas.bind('<Configure>', resize)
        def wheel(event):
            if (self.tabs.select() == str(self.pages[index][0])
                    and canvas.winfo_rooty() <= event.y_root < canvas.winfo_rooty()+canvas.winfo_height()):
                canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        for name in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            self.window.bind(name, wheel, add='+')
        return canvas, content

    def setup_page(self, page):
        # Keep consent/actions reachable on smaller Windows laptop screens.
        self.setup_canvas, page = self.scrolled(page, 0)
        self.label(page, 'Installation folder', '安装目录').pack(anchor='w')
        row = self.ttk.Frame(page)
        row.pack(fill='x', pady=(8, 16))
        self.root_entry = self.ttk.Entry(row, textvariable=self.install_path)
        self.root_entry.pack(side='left', fill='x', expand=True)
        self.button(row, 'Browse', '浏览', self.browse).pack(side='right', padx=(8, 0))
        self.label(page, 'Existing model folder (optional)', '已有模型文件夹（可选）').pack(anchor='w')
        row = self.ttk.Frame(page)
        row.pack(fill='x', pady=(8, 6))
        self.reuse_entry = self.ttk.Entry(row, textvariable=self.reuse_path)
        self.reuse_entry.pack(side='left', fill='x', expand=True)
        self.button(row, 'Browse', '浏览', self.browse_models).pack(side='right', padx=(8, 0))
        self.button(row, 'Clear', '清除', lambda: self.reuse_path.set('')).pack(side='right', padx=(8, 0))
        self.label(page, 'Subfolders are scanned. Missing files download during setup.',
                   '自动扫描子目录，缺少的文件在安装时下载。',
                   style='Meta.TLabel', wraplength=700).pack(anchor='w')
        self.copy_check = self.ttk.Checkbutton(page, variable=self.copy_existing)
        self.copy_check.configure(text=self.t('Create independent copies', '创建独立副本'))
        self.copy_check.pack(anchor='w', pady=(8, 14))
        self.restart_check = self.ttk.Checkbutton(page, variable=self.allow_restart)
        self.restart_check.configure(text=self.t('Allow restarting incomplete downloads',
                                               '允许重新下载无法续传的文件'))
        self.restart_check.pack(anchor='w', pady=(0, 14))
        self.button(page, 'Detect configuration', '检测配置', self.inspect, primary=True).pack(anchor='w')
        self.plan_text = self.ttk.Label(page, textvariable=self.resource, wraplength=790,
                                        justify='left', padding=(0, branding.space(4)))
        self.plan_text.pack(fill='x')
        self.plan_grid = self.ttk.Frame(page)
        self.plan_grid.columnconfigure((0, 1, 2), weight=1, uniform='plan')
        self.plan_tiles = []
        self.plan_note = self.ttk.Label(page, textvariable=self.note, style='Muted.TLabel',
                                        wraplength=790, justify='left')
        self.consent = self.ttk.Checkbutton(page, variable=self.accept, command=self.update_buttons)
        self.consent.configure(text=self.t('I accept the displayed installation plan and model licenses.', '我接受所展示的安装计划和模型许可证。'))
        self.consent.pack(anchor='w', pady=(8, 12))
        row = self.ttk.Frame(page)
        row.pack(fill='x')
        self.install_button = self.button(row, 'Install / repair', '安装／修复', self.install, primary=True)
        self.install_button.pack(side='left')
        self.button(row, 'Model licenses', '查看模型许可证', self.licenses, managed=False).pack(side='left', padx=10)

    def generate_page(self, page):
        _, page = self.scrolled(page, 1)
        self.label(page, 'Describe your video', '描述你想生成的视频').pack(anchor='w', pady=(0, 10))
        prompt_card = Card(page, padding=branding.space(3), radius=branding.RADIUS_MD)
        prompt_card.pack(fill='x')
        self.prompt = self.tk.Text(prompt_card.body, height=6, font=(branding.FAMILY, branding.STRONG),
                                   relief='flat', padx=branding.space(3), pady=branding.space(3),
                                   highlightthickness=0, borderwidth=0, bg=branding.BACKGROUND,
                                   fg=branding.TEXT, insertbackground=branding.TEXT, wrap='word')
        self.prompt.pack(fill='x')
        row = self.ttk.Frame(page)
        row.pack(fill='x', pady=16)
        self.label(row, 'Resolution', '分辨率').pack(side='left')
        self.ttk.Combobox(row, textvariable=self.resolution, state='readonly', width=18,
                         values=('1344 × 768', '1024 × 576', '768 × 448')).pack(side='left', padx=(10, 24))
        self.label(row, 'Seconds', '时长（秒）').pack(side='left')
        self.ttk.Combobox(row, textvariable=self.duration, state='readonly', width=10,
                         values=('10.125', '5')).pack(side='left', padx=10)
        self.label(page, 'Strategy follows your available VRAM and RAM.', '策略随当前可用显存与内存自动调整。',
                   style='Meta.TLabel').pack(anchor='w')
        self.prediction_status = self.tk.StringVar(value=self.t('Forecasts improve as you generate.',
                                                              '生成越多，耗时与内存预测越准。'))
        self.ttk.Label(page, textvariable=self.prediction_status, style='Muted.TLabel', wraplength=780).pack(anchor='w', pady=(10, 0))
        actions = self.ttk.Frame(page)
        actions.pack(anchor='w', pady=20)
        self.button(actions, 'Generate video', '生成视频', self.generate, primary=True).pack(side='left')
        self.button(actions, 'Estimate time / memory', '预估耗时／内存', self.predict).pack(side='left', padx=12)

    def test_page(self, page):
        _, page = self.scrolled(page, 2)
        self.label(page, 'Find out how your machine performs.', '看看这台机器的实际表现。',
                   style='Section.TLabel').pack(anchor='w')
        self.label(page, 'Five requests: first, repeat, new prompt, resolution, duration.',
                   '连续五轮：首次、重复、更换提示词、分辨率、时长。',
                   style='Muted.TLabel', wraplength=730).pack(anchor='w', pady=branding.space(4))
        row = self.ttk.Frame(page)
        row.pack(fill='x')
        self.button(row, 'Standard test', '标准测试', lambda: self.test('standard'), primary=True).pack(side='left')
        self.button(row, 'Quick test', '快速测试', lambda: self.test('quick')).pack(side='left', padx=10)
        self.label(page, 'Tune once, then test again. Only validated settings are kept.',
                   '优化一次，再测一次；仅保存通过验证的设置。',
                   style='Muted.TLabel', wraplength=730).pack(anchor='w', pady=(branding.space(7), branding.space(4)))
        self.button(page, 'Optimize once', '优化一次', lambda: self.ready_action('optimize', ['optimize', '--plain'])).pack(anchor='w')

    def reports_page(self, page):
        _, page = self.scrolled(page, 3)
        self.label(page, 'Your results stay on this computer.', '结果始终保留在本机。',
                   style='Section.TLabel').pack(anchor='w')
        self.label(page, 'Open a report, or export diagnostics when something fails.',
                   '打开报告；遇到问题时导出诊断包。',
                   style='Muted.TLabel', wraplength=730).pack(anchor='w', pady=branding.space(4))
        for en, zh, command in [('Open latest video report', '打开最近视频报告', self.open_report),
                                ('Play latest generated video', '播放最近生成的视频', self.open_video),
                                ('Open output folder', '打开产物目录', self.open_outputs),
                                ('Export diagnostics', '导出诊断包', self.diagnose),
                                ('Open launcher logs', '打开启动器日志', self.open_logs)]:
            self.button(page, en, zh, command, managed=en == 'Export diagnostics').pack(anchor='w', pady=6)

    def root(self):
        if not self.install_path.get().strip():
            raise ValueError(self.t('Choose an installation folder.', '请选择安装目录。'))
        return Path(self.install_path.get()).expanduser().resolve()

    def root_changed(self, *_):
        self.plan = None
        self.setup_retry = False
        self.accept.set(False)
        self.clear_plan_view()
        self.resource.set(self.t('Detect configuration for this installation folder.', '请检测此目录对应的安装配置。'))
        self.update_buttons()

    def clear_plan_view(self):
        """The controller tests build a view-less Desktop, so skip if unbuilt."""
        if not hasattr(self, 'plan_grid'):
            return
        self.note.set('')
        for widget in (self.plan_grid, self.plan_note):
            widget.pack_forget()
        self.plan_text.pack_configure(pady=(0, branding.space(4)))

    def update_buttons(self):
        self.install_button.configure(text=self.t('Continue installation / retry', '继续安装／重试') if self.setup_retry
                                      else self.t('Install / repair', '安装／修复'))
        for button in self.buttons:
            button.state(['disabled'] if self.running else ['!disabled'])
        self.root_entry.state(['disabled'] if self.running else ['!disabled'])
        self.reuse_entry.state(['disabled'] if self.running else ['!disabled'])
        self.copy_check.state(['disabled'] if self.running else ['!disabled'])
        self.restart_check.state(['disabled'] if self.running else ['!disabled'])
        self.consent.state(['disabled'] if self.running else ['!disabled'])
        if self.running or not self.plan or self.plan.get('errors') or not self.accept.get():
            self.install_button.state(['disabled'])

    def language(self):
        self.zh = not self.zh
        self.log_window.title(self.t('FreeVideo · Details', 'FreeVideo · 详情'))
        for widget, en, zh in self.translated:
            widget.configure(text=self.t(en, zh))
        for page, en, zh in self.pages:
            self.tabs.tab(page, text=self.t(en, zh))
        self.consent.configure(text=self.t('I accept the displayed installation plan and model licenses.', '我接受所展示的安装计划和模型许可证。'))
        self.copy_check.configure(text=self.t('Create independent copies', '创建独立副本'))
        self.restart_check.configure(text=self.t('Allow restarting incomplete downloads',
                                               '允许重新下载无法续传的文件'))
        if self.plan:
            self.show_plan()
        self.update_buttons()
        self.preferences()

    def preferences(self):
        if self.install_path.get().strip():
            save(launcher_root() / 'settings.json', {'root': self.install_path.get(), 'language': 'zh' if self.zh else 'en',
                'reuse_models': self.reuse_path.get(), 'copy_existing_models': self.copy_existing.get()})

    def browse(self):
        from tkinter import filedialog
        value = filedialog.askdirectory(parent=self.window, title=self.t('Installation folder', '安装目录'))
        if value:
            self.install_path.set(value)

    def browse_models(self):
        from tkinter import filedialog
        value = filedialog.askdirectory(parent=self.window, title=self.t('Existing model folder', '已有模型文件夹'))
        if value:
            self.reuse_path.set(value)

    def model_arguments(self):
        folder = self.reuse_path.get().strip()
        arguments = (['--reuse-models', folder] + (['--copy-existing-models'] if self.copy_existing.get() else [])) if folder else []
        if self.allow_restart.get():
            arguments.append('--allow-model-restart')
        return arguments

    def begin(self, action, arguments):
        if self.running:
            return
        self.running = True
        for row in self.progress_rows.values():
            row['bar'].stop()
            row['frame'].destroy()
        self.progress_rows.clear()
        self.phase_status.set(self.t('Preparing…', '准备中…'))
        self.status.set(self.t('Working…', '正在处理…'))
        self.progress.configure(mode='indeterminate', value=0)
        self.progress.start(15)
        self.cancel_button.state(['!disabled'])
        self.update_buttons()
        try:
            self.preferences()
            self.runner.start(action, self.root(), arguments)
        except BaseException:
            self.running = False
            self.progress.stop()
            self.cancel_button.state(['disabled'])
            self.update_buttons()
            raise

    def inspect(self):
        self.plan = None
        self.accept.set(False)
        self.begin('plan', ['setup', '--plan', '--json', *self.model_arguments()])

    def install(self):
        if self.plan and not self.plan['errors'] and self.accept.get():
            receipt = launcher_root() / 'plans' / ('approved-' + uuid.uuid4().hex + '.json')
            save(receipt, self.plan)
            self.begin('setup', ['setup', '--plain', '--yes', '--accept-model-license', '--approved-plan', str(receipt), *self.model_arguments()])

    def ready_action(self, action, arguments):
        from tkinter import messagebox
        try:
            machine = json.loads((self.root() / 'machine.json').read_text(encoding='utf-8'))
            ready = machine.get('ready') is True and machine.get('source') == str(self.source)
        except (OSError, ValueError):
            ready = False
        if not ready:
            messagebox.showinfo('FreeVideo', self.t('Run setup/repair with this launcher first.', '请先使用此版本启动器完成安装／修复。'), parent=self.window)
            self.tabs.select(0)
            return False
        self.begin(action, arguments)
        return True

    def generate(self):
        from tkinter import messagebox
        prompt = self.prompt.get('1.0', 'end-1c').strip()
        if not prompt:
            messagebox.showinfo('FreeVideo', self.t('Describe the video first.', '请先填写视频描述。'), parent=self.window)
            return
        run = self.root() / 'outputs' / ('gui-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
        # Prompt text stays local and is excluded from the launcher command/log.
        run.mkdir(parents=True)
        (run / 'prompt.txt').write_text(prompt, encoding='utf-8')
        width, height = self.resolution.get().split(' × ')
        self.ready_action('generate', ['generate', '--prompt-file', str(run / 'prompt.txt'),
            '--width', width, '--height', height, '--seconds', self.duration.get(), '--out', str(run / 'video.mp4')])

    def predict(self):
        width, height = self.resolution.get().split(' × ')
        self.ready_action('predict', ['predict', '--width', width, '--height', height,
                                      '--seconds', self.duration.get()])

    def test(self, suite):
        self.ready_action('test', ['test', '--suite', suite, '--plain'])

    def diagnose(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(parent=self.window, defaultextension='.zip',
            initialfile='freevideo-diagnostics-' + time.strftime('%Y%m%d-%H%M%S') + '.zip', filetypes=[('ZIP', '*.zip')])
        if path:
            self.begin('diagnose', ['diagnose', '--full', '--out', path])

    def licenses(self):
        from tkinter import messagebox
        if not self.plan:
            messagebox.showinfo('FreeVideo', self.t('Detect configuration to load the pinned model licenses.', '先检测配置，获取固定模型的许可证。'), parent=self.window)
            return
        popup = self.tk.Toplevel(self.window)
        popup.title(self.t('Model licenses', '模型许可证'))
        popup.transient(self.window)
        self.label(popup, 'Read the licenses before confirming installation.', '确认安装前请阅读对应许可证。', padding=20).pack()
        for url in self.plan['licenses']:
            self.ttk.Button(popup, text=url, command=lambda value=url: webbrowser.open(value)).pack(fill='x', padx=20, pady=6)

    def show_plan(self):
        """Figures go in tiles; only what a figure cannot say stays as text."""
        from .bootstrap import download_bytes
        gib = lambda value: '%.1f GiB' % (value/2**30)
        h = self.plan['inventory']['hardware']
        estimate = self.plan.get('installation_resources') or self.plan.get('policy_estimate')
        local = self.plan.get('local_models')
        tiles = [(self.t('VRAM free', '空闲显存'), '%s / %s' % (gib(h['vram_free']), gib(h['vram_total']))),
                 (self.t('RAM available', '可用内存'), '%s / %s' % (gib(h['ram_available']), gib(h['ram_total']))),
                 (self.t('To download', '需下载'), gib(download_bytes(self.plan)))]
        if estimate:
            tiles.append((self.t('Installation RAM budget', '安装内存预算'), gib(estimate['ram_budget_bytes'])))
        for disk in self.plan['disks'][:1]:
            tiles.append((self.t('Disk needed', '磁盘需求'),
                          '%s / %s' % (gib(disk['needed_bytes']), gib(disk['free_bytes']))))
        if local:
            tiles.append((self.t('Reused locally', '本地复用'),
                          '%d · %s' % (len(local['matches']), gib(local['reused_bytes']))))
        while len(self.plan_tiles) < len(tiles):
            self.plan_tiles.append(Stat(self.plan_grid, '', ''))
        for index, tile in enumerate(self.plan_tiles):
            if index < len(tiles):
                tile.show(*tiles[index])
                tile.grid(row=index//3, column=index % 3, sticky='nsew',
                          padx=(0 if index % 3 == 0 else branding.space(2), 0),
                          pady=(0, branding.space(2)))
            else:
                tile.grid_forget()
        notes = [h['gpu_name']]
        if self.plan.get('prepared_model'):
            notes.append('FP8 · ' + self.plan['prepared_model']['repo'])
            if self.plan['prepared_model'].get('private'):
                notes.append(self.t('Hugging Face access required', '需要 Hugging Face 访问权限'))
        if self.plan.get('allow_model_restart'):
            notes.append(self.t('incomplete files may restart', '未完成文件可能重下'))
        if local:
            notes.append(self.t('originals stay in place', '原文件保留在原目录'))
        if self.plan.get('storage') == 'retain':
            notes.append(self.t('original weights retained', '保留原始权重'))
        self.note.set(' · '.join(notes))
        self.resource.set('\n'.join(self.plan['errors']))
        for widget in (self.plan_grid, self.plan_note):
            if not widget.winfo_manager():
                widget.pack(fill='x', pady=(0, branding.space(3)), before=self.consent)
        self.plan_text.pack_configure(pady=(0, branding.space(2)) if self.plan['errors'] else 0)

    def open_path(self, path):
        from tkinter import messagebox
        if path and Path(path).exists():
            os.startfile(str(path))
        else:
            messagebox.showinfo('FreeVideo', self.t('No result yet.', '目前还没有结果。'), parent=self.window)

    def open_report(self):
        reports = sorted((self.root() / 'test-runs').glob('*/report.html'))
        self.open_path(reports[-1] if reports else None)

    def open_outputs(self):
        self.open_path(self.root())

    def open_video(self):
        videos = sorted((self.root() / 'outputs').glob('gui-*/video.mp4'))
        self.open_path(videos[-1] if videos else None)

    def open_logs(self):
        self.open_path(self.last_log or launcher_root() / 'runs')

    def toggle_log(self):
        if self.log_window.state() != 'withdrawn':
            self.log_window.withdraw()
        else:
            self.log_window.deiconify()
            self.log_window.lift()

    def append_log(self, line):
        self.log.configure(state='normal')
        self.log.insert('end', line)
        if int(self.log.index('end-1c').split('.')[0]) > 600:
            self.log.delete('1.0', '100.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def progress_event(self, value):
        """Separate installation stages from parallel task/file progress."""
        if value.get('kind') == 'prediction':
            from .prediction_ui import summary
            self.prediction_status.set(summary(value.get('forecast', {}), zh=self.zh))
            return
        if value.get('kind') == 'phase':
            total, done = value.get('total') or 0, value.get('done') or 0
            self.progress.stop()
            self.progress.configure(mode='determinate', maximum=max(1, total), value=done)
            self.phase_status.set(self.t('Stages', '阶段') + ' %d / %d · %s' % (done, total, value.get('label', '')))
            return
        key = value.get('key') or value.get('label', 'task')
        if key not in self.progress_rows:
            for prior, row in list(self.progress_rows.items()):
                if row['value'].get('state') in ('complete', 'failed', 'cancelled'):
                    row['bar'].stop()
                    row['frame'].destroy()
                    del self.progress_rows[prior]
            card = Card(self.task_area, padding=branding.space(3), radius=branding.RADIUS_MD)
            card.pack(fill='x', pady=(0, branding.space(2)))
            frame = card.body
            title, meta, detail = (self.tk.StringVar() for _ in range(3))
            header = self.ttk.Frame(frame, style='Card.TFrame')
            header.pack(fill='x')
            self.ttk.Label(header, textvariable=title, style='Progress.TLabel').pack(side='left')
            self.ttk.Label(header, textvariable=meta, style='ProgressMeta.TLabel').pack(side='right')
            progress = Progress(frame, height=branding.space(1) + 2)
            progress.pack(fill='x', pady=(branding.space(1), branding.space(1)))
            self.ttk.Label(frame, textvariable=detail, style='ProgressMeta.TLabel', wraplength=790).pack(anchor='w')
            self.progress_rows[key] = dict(frame=card, title=title, meta=meta, detail=detail, bar=progress)
        row = self.progress_rows[key]
        row.update(value=dict(value), received=time.monotonic())
        row['title'].set(clipped(value.get('label', ''), 40))
        detail = value.get('detail', '')
        if self.zh:
            for en, zh in [('Retrying same source', '正在重连原下载源'), ('Resume unavailable; checking another source', '此源无法续传，检查其他源'),
                           ('Paused; existing data kept', '下载已停止，进度已保留'), ('Restart explicitly allowed', '已允许重新下载未完成文件'),
                           ('Cannot resume this format; enable restart in Setup', '此格式无法续传；如需重下，请勾选允许重新下载并重新检测'),
                           ('MiB retained', 'MiB 已保留'), ('Continue installation / retry', '继续安装／重试')]:
                detail = detail.replace(en, zh)
        row['detail'].set(detail[:180])
        total, done = value.get('total') or 0, value.get('done') or 0
        bar = row['bar']
        finished = value.get('state') in ('complete', 'failed', 'cancelled')
        mode = 'determinate' if total or finished else 'indeterminate'
        if str(bar['mode']) != mode or finished:
            bar.stop()
            bar.configure(mode=mode)
            if mode == 'indeterminate':
                bar.start(20)
        if mode == 'determinate':
            bar.configure(maximum=max(1, total), value=(total or 1) if value.get('state') == 'complete' else done)
        if value.get('resource'):
            self.status.set(value['resource'])
        self.refresh_progress_times()

    def refresh_progress_times(self):
        for row in self.progress_rows.values():
            value, age = row['value'], time.monotonic() - row['received']
            finished = value.get('state') in ('complete', 'failed', 'cancelled')
            elapsed = (value.get('elapsed_seconds') or 0) + (0 if finished else age)
            pieces = [self.t('Elapsed ', '已用 ') + duration(elapsed)]
            if value.get('total'):
                pieces.insert(0, '%.0f%%' % min(100, max(0, 100*(value.get('done') or 0)/value['total'])))
            remaining = value.get('remaining_seconds') if age <= 10 else None
            if finished:
                pieces.append(self.t('Complete', '完成') if value['state'] == 'complete' else self.t('Stopped', '已停止'))
            elif remaining == 0:
                pieces.append(self.t('Finalizing', '正在收尾'))
            elif remaining is not None:
                pieces.append(self.t('ETA ~', '预计剩余约 ') + duration(remaining))
            else:
                pieces.append(self.t('Estimating…', '估算中…'))
            row['meta'].set(' · '.join(pieces))

    def poll(self):
        self.cancel_poll()
        try:
            for _ in range(150):
                kind, value = self.events.get_nowait()
                if kind == 'started':
                    self.last_log = value
                elif kind == 'log':
                    self.append_log(value)
                elif kind == 'progress':
                    self.progress_event(value)
                elif kind == 'done':
                    self.running = False
                    if value['action'] == 'setup':
                        self.setup_retry = value['status'] != 'complete'
                    self.progress.stop()
                    if value['status'] == 'complete':
                        self.progress.configure(mode='determinate', maximum=1, value=1)
                    for key, row in list(self.progress_rows.items()):
                        if row['value'].get('state') not in ('complete', 'failed', 'cancelled'):
                            self.progress_event(dict(row['value'], key=key, state=value['status']))
                    self.cancel_button.state(['disabled'])
                    if value['action'] == 'plan':
                        try:
                            self.plan = preflight_json(value['output'])
                            self.show_plan()
                        except ValueError as error:
                            reason = value.get('error') or '\n'.join(value['output'].strip().splitlines()[-6:])
                            self.resource.set(self.t('Configuration check failed:', '配置检测未完成：') + '\n' + (reason or str(error))[-1200:])
                    labels = {'complete': self.t('Complete', '已完成'), 'failed': self.t('Failed — open details or export diagnostics', '未完成，可查看详情或导出诊断'),
                              'cancelled': self.t('Stopped. Files retained.', '已停止，文件已保留。')}
                    self.status.set(labels[value['status']] + '  ·  %.1f s' % value['wall_seconds'])
                    if value['action'] == 'setup' and self.setup_retry:
                        self.status.set(self.t('Installation stopped. Use Continue installation / retry; completed files are reused.',
                                               '安装已停止。点击「继续安装／重试」，复用已完成文件。'))
                    self.update_buttons()
                    if self.closing:
                        self.stop_progress()
                        self.window.destroy()
                        return
        except queue.Empty:
            pass
        self.refresh_progress_times()
        self.poll_timer = self.window.after(100, self.poll)

    def cancel_poll(self, event=None):
        if event is not None and event.widget != self.window:
            return
        timer = getattr(self, 'poll_timer', None)
        if timer is not None:
            self.window.after_cancel(timer)
            self.poll_timer = None

    def cancel(self):
        self.status.set(self.t('Stopping workers; retaining files…', '正在停止工作进程并保留文件…'))
        self.cancel_button.state(['disabled'])
        self.runner.cancel()

    def close(self):
        from tkinter import messagebox
        if self.running:
            if not messagebox.askyesno('FreeVideo', self.t('Stop the current task and close? All files are retained.', '停止当前任务并退出？所有文件会保留。'), parent=self.window):
                return
            self.closing = True
            self.cancel()
        else:
            self.stop_progress()
            self.window.destroy()

    def stop_progress(self):
        self.progress.stop()
        for row in self.progress_rows.values():
            row['bar'].stop()

    def callback_error(self, kind, value, trace):
        from tkinter import messagebox
        detail = ''.join(traceback.format_exception(kind, value, trace))
        self.append_log(detail)
        path = launcher_root() / 'errors' / ('error-' + uuid.uuid4().hex + '.log')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(detail, encoding='utf-8')
        messagebox.showerror('FreeVideo', str(value) + '\n' + str(path), parent=self.window)


def main():
    import tkinter as tk
    if windows():
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass
        # A hidden console gives Ctrl+Break a real console group while the GUI
        # remains windowed. Workers can finalize reports before forced cleanup.
        lib = ctypes.windll.kernel32
        lib.GetConsoleWindow.restype = ctypes.c_void_p
        if not lib.GetConsoleWindow():
            lib.AllocConsole()
            ctypes.windll.user32.ShowWindow(ctypes.c_void_p(lib.GetConsoleWindow()), 0)
    window = tk.Tk()
    app = Desktop(window)
    if '--smoke-test' in sys.argv:
        destination = Path(sys.argv[sys.argv.index('--smoke-test') + 1])
        destination.mkdir(parents=True, exist_ok=True)
        app.begin('help', ['--help'])
        deadline = time.monotonic() + 240
        from .ram import ProcessMemory
        observation = ProcessMemory()
        def finished():
            observation.sample(os.getpid())
            if app.running and time.monotonic() < deadline:
                window.after(100, finished)
                return
            if app.running:
                app.runner.cancel()
                save(destination / 'smoke.json', {'success': False, 'error': 'Launcher smoke timeout'})
            else:
                status = json.loads((Path(app.last_log) / 'status.json').read_text(encoding='utf-8'))
                from .system import system_memory
                save(destination / 'smoke.json', {'success': status['status'] == 'complete',
                    'window_geometry': window.geometry(), 'task': status, 'source': str(app.source),
                    'model_modules_imported': [p for p in ('torch', 'triton', 'transformers') if p in sys.modules],
                    'launcher_process_tree': observation.result(),
                    'memory': system_memory()})
            window.destroy()
        window.after(100, finished)
    window.mainloop()


if __name__ == '__main__':
    main()
