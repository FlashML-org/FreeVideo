"""Native download controls, usable before ComfyUI or the engine is installed."""
from pathlib import Path

from .download_settings import CHOICES, MODES, SPEED_TEST_FAILED, Probe, read, speed_text


class DownloadSettings:
    def __init__(self, parent, root, zh=False, probe=None, token=None, busy=None):
        import tkinter as tk
        from tkinter import ttk
        self.root, self.zh = Path(root), zh
        self.probe = probe or Probe()
        self.token = token if token is not None else tk.StringVar(parent)
        self.busy = busy or (lambda: False)
        self.window = window = tk.Toplevel(parent)
        window.title(self.t('FreeVideo · Download sources', 'FreeVideo · 下载源'))
        window.transient(parent)
        window.geometry('640x770'); window.minsize(550, 710)
        self.timer = None
        body = ttk.Frame(window, padding=24); body.pack(fill='both', expand=True)
        ttk.Label(body, text=self.t('Connection', '连接模式')).pack(anchor='w')
        self.proxy_mode = ttk.Combobox(body, state='readonly', values=(
            self.t('Auto (recommended)', '自动（推荐）'), self.t('Proxy only', '仅代理'), self.t('Direct only', '仅直连')))
        self.proxy_mode.pack(fill='x', pady=(8, 4))
        self.proxy_mode.bind('<<ComboboxSelected>>', self.select_mode)
        self.mode_help = tk.StringVar()
        ttk.Label(body, textvariable=self.mode_help, wraplength=560, style='Muted.TLabel').pack(fill='x', pady=(0, 12))
        ttk.Label(body, text=self.t('Model download source', '模型下载源')).pack(anchor='w')
        self.names = (self.t('Automatic', '自动选择'), 'Hugging Face', 'HF Mirror', 'ModelScope')
        self.source = ttk.Combobox(body, values=self.names, state='readonly')
        self.source.pack(fill='x', pady=(8, 12))
        self.source.bind('<<ComboboxSelected>>', self.select)
        from .hf_auth import HELP_EN, HELP_ZH, TOKEN_URL
        import webbrowser
        auth = ttk.LabelFrame(body, text=self.t('Hugging Face token · optional', 'Hugging Face Token · 可选'), padding=12)
        auth.pack(fill='x', pady=(0, 12))
        ttk.Label(auth, text=self.t(HELP_EN, HELP_ZH), wraplength=520, style='Muted.TLabel').pack(fill='x')
        self.token_input = tk.StringVar(value=self.token.get())
        self.token_entry = ttk.Entry(auth, textvariable=self.token_input, show='•')
        self.token_entry.pack(fill='x', pady=8)
        actions = ttk.Frame(auth); actions.pack(fill='x')
        self.token_apply = ttk.Button(actions, text=self.t('Apply token', '应用 Token'), command=self.apply_token)
        self.token_apply.pack(side='left')
        ttk.Button(actions, text=self.t('Get a read token ↗', '获取读取 Token ↗'),
                   command=lambda: webbrowser.open(TOKEN_URL)).pack(side='left', padx=8)
        self.token_status = tk.StringVar()
        ttk.Label(auth, textvariable=self.token_status, wraplength=520, style='Muted.TLabel').pack(fill='x', pady=(6, 0))
        self.status = tk.StringVar()
        self.probe_button = ttk.Button(body, text=self.t('Test source speeds', '重新测速'), command=self.start_probe)
        self.probe_button.pack(anchor='w')
        ttk.Label(body, textvariable=self.status, wraplength=560, style='Muted.TLabel').pack(fill='x', pady=8)
        self.speeds = ttk.Treeview(body, columns=('model', 'source', 'speed'), show='headings', height=4)
        for key, en, cn, width in (('model', 'Files', '文件', 120), ('source', 'Source / route', '来源／连接', 235),
                                   ('speed', 'HTTP transfer', 'HTTP 传输速度', 150)):
            self.speeds.heading(key, text=self.t(en, cn)); self.speeds.column(key, width=width, minwidth=80)
        self.speeds.pack(fill='both', expand=True)
        ttk.Label(body, text=self.t(
            'Switching takes effect at once. Downloads resume where possible; otherwise the file restarts. Testing speeds does not switch.',
            '切换后马上生效，能续传的接着下载，不能的重新下载这个文件。只测速不会切换。'),
            wraplength=560, style='Muted.TLabel').pack(fill='x', pady=(12, 4))
        ttk.Label(body, text=str(self.root), wraplength=560, style='Muted.TLabel').pack(fill='x')
        ttk.Button(body, text=self.t('Done', '完成'), command=window.destroy).pack(anchor='e', pady=(8, 0))
        window.bind('<Destroy>', self.destroyed, add='+')
        self.rendered = None
        self.poll()

    def t(self, en, zh):
        return zh if self.zh else en

    def select(self, _=None):
        try:
            self.probe.select(self.root, CHOICES[self.source.current()])
            self.status.set(self.t('Source saved; switching any active model download…', '已保存，正在切换当前模型下载…'))
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def start_probe(self):
        self.probe.start(self.root, self.token.get())
        self.refresh()

    def select_mode(self, _=None):
        try:
            self.probe.select(self.root, proxy_mode=MODES[self.proxy_mode.current()])
            self.refresh()
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def apply_token(self):
        from .hf_auth import validate
        if self.busy():
            self.token_status.set(self.t('Stop setup before changing the token, then resume.', '先停止安装，再修改 Token 并继续安装。'))
            return
        try:
            self.token.set(validate(self.token_input.get()))
            self.token_status.set(self.t('Applied for this session. Review the installation again to continue.',
                '已应用到本次会话，重新检测安装即可继续。') if self.token.get() else
                self.t('Session token cleared. Existing environment credentials still apply.', '已清除本次填写的 Token；仍可使用环境中已有的凭据。'))
        except ValueError:
            self.token_status.set(self.t('Enter a token beginning with hf_, or leave it empty.', '请输入以 hf_ 开头的 Token，或留空。'))

    def refresh(self):
        value = read(self.root / 'download-settings.json')
        self.source.current(CHOICES.index(value['source']))
        mode = value['proxy_mode']
        self.proxy_mode.current(MODES.index(mode))
        self.mode_help.set(self.t(*{
            'auto': ('Compare proxy and direct connections automatically.', '自动测速，择优使用代理或直连。'),
            'proxy': ('Use the current proxy. No direct fallback.', '沿用当前代理，不尝试直连。'),
            'direct': ('Download directly, ignoring proxies.', '忽略代理，直接下载。')}[mode]))
        state = self.probe.snapshot()
        busy = state['status'] == 'running'
        installing = self.busy()
        self.token_apply.state(['disabled'] if installing else ['!disabled'])
        self.token_entry.state(['disabled'] if installing else ['!disabled'])
        if installing:
            self.token_status.set(self.t('Stop setup to change credentials; downloaded files will be kept.', '如需修改 Token，请先停止安装；已下载文件会保留。'))
        self.probe_button.state(['disabled'] if busy else ['!disabled'])
        if busy:
            progress = state.get('progress', {})
            self.status.set(self.t('Testing sources… ', '正在测速… ') +
                            ('%s/%s · %s' % (progress.get('done', 0), progress.get('total', '?'), progress.get('source', ''))))
        elif state.get('error'):
            self.status.set(self.t(SPEED_TEST_FAILED, '测速没有完成，请点“重新测速”。')
                            if state['error'] == SPEED_TEST_FAILED else state['error'])
        elif state.get('network_unavailable'):
            self.status.set(self.t('No sources reachable. Check your network or connection mode.', '下载源均无法连接，请检查网络或切换连接模式。'))
        measured = value.get('probe')
        if measured != self.rendered:
            self.rendered = measured
            self.speeds.delete(*self.speeds.get_children())
            families = {'edge-models': self.t('Video model', '视频模型'), 'vdn-models': self.t('Decoder', '解码器'),
                        'encoder-models': self.t('Text encoder', '文本编码器'),
                        'models': self.t('Prompt model', '提示词模型'), 'pypi': self.t('Python packages', 'Python 依赖'),
                        'github': self.t('Tools', '安装工具'), 'git': 'Git', 'cuda': 'CUDA'}
            for family, rows in (measured or {}).get('sources', {}).items():
                if family.startswith('torch-'):
                    families[family] = self.t('GPU packages', 'GPU 依赖')
                for row in rows:
                    speed = speed_text(row, self.zh)
                    route = self.t('Direct', '直连') if row.get('route') == 'direct' else self.t('Current connection', '当前连接')
                    self.speeds.insert('', 'end', values=(families.get(family, family), row['id'] + ' · ' + route, speed))
            if measured and not busy and not state.get('network_unavailable'):
                self.status.set(self.t('Speeds are a guide. Choose a source above to switch.',
                    '测速只作参考。在上方选择下载源来切换。'))

    def poll(self):
        try:
            self.refresh()
        except (OSError, ValueError) as error:
            self.status.set(str(error))
        self.timer = self.window.after(400, self.poll)

    def destroyed(self, event):
        if event.widget == self.window and self.timer is not None:
            self.window.after_cancel(self.timer)
            self.timer = None
