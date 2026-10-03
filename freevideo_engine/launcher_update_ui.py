"""Tk update prompt; networking is delegated to a background client."""
import time
import webbrowser

from . import branding
from .launcher_update import RELEASE_PAGE, launch_download, DownloadedLauncherUnavailable


class UpdateUI:
    def __init__(self, app, client, footer):
        self.app, self.client = app, client
        self.dialog = None
        self.notified = None
        self.last_state = None
        self.closed = False
        self.timer = None
        self.snoozed = False
        self.button = app.ttk.Button(footer, text=app.t('Check updates', '检查更新'), command=self.open)
        self.button.pack(side='left')
        app.window.bind('<Destroy>', self.destroyed, add='+')

    def start(self):
        self.client.run('check')
        self.poll()

    def open(self):
        app = self.app
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.lift(); return
        d = self.dialog = app.tk.Toplevel(app.window)
        d.title(app.t('FreeVideo · Update', 'FreeVideo · 更新')); d.transient(app.window)
        d.geometry('560x380'); d.minsize(440, 340)
        d.configure(bg=branding.BACKGROUND)
        body = app.ttk.Frame(d, padding=24); body.pack(fill='both', expand=True)
        self.heading = app.ttk.Label(body, text='', wraplength=480)
        self.heading.pack(anchor='w', pady=(0, 10))
        self.detail = app.tk.Text(body, height=5, wrap='word', relief='flat',
            bg=branding.BACKGROUND, fg=branding.TEXT, state='disabled')
        self.detail.pack(fill='both', expand=True)
        self.progress = app.ttk.Progressbar(body, maximum=1); self.progress.pack(fill='x', pady=8)
        self.rate = app.ttk.Label(body, text=''); self.rate.pack(anchor='w')
        self.access = app.ttk.Frame(body)
        app.ttk.Label(self.access, text=app.t('GitHub token · private updates · this session only', 'GitHub Token · 私有更新 · 仅本次会话使用')).pack(anchor='w')
        self.token = app.tk.StringVar(value=self.client.token)
        app.ttk.Entry(self.access, textvariable=self.token, show='•').pack(fill='x')
        actions = app.ttk.Frame(body); actions.pack(fill='x', pady=(14, 0))
        self.primary = app.ttk.Button(actions, command=self.accept); self.primary.pack(side='left')
        self.dismiss_button = app.ttk.Button(actions, command=self.dismiss)
        self.dismiss_button.pack(side='left', padx=8)
        app.ttk.Button(actions, text=app.t('Release page', '下载页'), command=lambda: webbrowser.open(RELEASE_PAGE)).pack(side='right')
        d.protocol('WM_DELETE_WINDOW', self.dismiss)
        self.render()
        if self.client.state['status'] in ('idle', 'current', 'cancelled'):
            self.client.run('check')

    def dismiss(self):
        self.snoozed = True
        if self.client.state['status'] == 'downloading':
            self.client.cancelled.set()
        if self.dialog:
            self.dialog.destroy(); self.dialog = None

    def accept(self):
        app, row = self.app, self.client.state
        if self.client.busy or app.controller.busy:
            return
        try:
            if row['status'] == 'ready':
                app.save_settings()
                launch_download(row['candidate'], self.client.root, token=self.client.token)
                self.closed = True
                app.close()
            elif row['status'] == 'available' or (row['status'] in ('error', 'cancelled') and row.get('candidate')):
                if getattr(self.client, 'current', {}).get('packaging') == 'onedir':
                    webbrowser.open(RELEASE_PAGE)
                else:
                    self.client.run('download', row['candidate'])
            else:
                self.client.token = self.token.get().strip()
                self.client.run('check')
        except DownloadedLauncherUnavailable:
            self.client.run('download', row['candidate'])
        except Exception as error:
            from .diagnostics import Redactor
            self.client.state = dict(status='error', error=Redactor([(self.client.token, '<REDACTED>')]).text(str(error)),
                                     candidate=row.get('candidate'))

    def render(self):
        if not self.dialog or not self.dialog.winfo_exists():
            return
        app, row = self.app, self.client.state
        state = row['status']
        headings = {'checking': ('Checking for updates…', '正在检查更新…'),
                    'available': ('A FreeVideo update is available', '发现 FreeVideo 新版本'),
                    'downloading': ('Downloading update…', '正在下载更新…'),
                    'ready': ('Update ready', '更新已就绪'),
                    'current': ('You have the current release', '当前已是最新发布版本'),
                    'error': ('Update check / download failed', '更新检查／下载失败'),
                    'cancelled': ('Update paused', '更新已停止')}
        self.heading.configure(text=app.t(*headings.get(state, headings['checking'])))
        candidate = row.get('candidate')
        description = row.get('error', '')
        if candidate:
            description += ('\n\n' if description else '') + '%s · %s · %s\n%.1f MiB\n\n%s' % (candidate['version'],
                time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(candidate['built_at'])), candidate['revision'][:8],
                candidate['asset']['bytes']/2**20,
                app.t('Your models, environments and settings are kept. Restart the launcher after downloading.',
                      '保留已有模型、环境和设置。下载后重启启动器即可。'))
            self.notified = candidate['revision']
            if getattr(self.client, 'current', {}).get('packaging') == 'onedir':
                description += '\n' + app.t('Download and extract the updated folder ZIP; keep all files together.',
                                             '请下载并解压新版文件夹包，保留包内完整文件。')
        if app.controller.busy:
            description += '\n' + app.t('Finish the current installation before updating.', '请等待当前安装完成后再更新。')
        self.detail.configure(state='normal'); self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', description); self.detail.configure(state='disabled')
        progress = row.get('progress', {})
        self.progress.configure(maximum=progress.get('total', 1), value=progress.get('done', 0))
        self.rate.configure(text=('%.0f%% · %.1f MiB/s · ~%ds' %
            (100*progress['done']/progress['total'], progress['bytes_per_second']/2**20,
             progress['remaining_seconds'])) if progress else '')
        if state == 'error':
            self.access.pack(fill='x', pady=8, before=self.primary.master)
        else:
            self.access.pack_forget()
        downloadable = state == 'available' or (state in ('error', 'cancelled') and candidate)
        self.primary.configure(text=app.t('Restart & update', '重启并更新') if state == 'ready' else
                               app.t('Download folder ZIP', '下载文件夹版') if downloadable and
                               getattr(self.client, 'current', {}).get('packaging') == 'onedir' else
                               app.t('Download update', '下载更新') if downloadable else app.t('Check again', '重新检查'))
        self.primary.state(['disabled'] if self.client.busy or app.controller.busy else ['!disabled'])
        self.dismiss_button.configure(text=app.t('Later', '稍后更新') if candidate else app.t('Close', '关闭'))

    def poll(self):
        if self.timer is not None:
            self.app.window.after_cancel(self.timer)
            self.timer = None
        if self.closed:
            return
        row = self.client.state
        state = row['status']
        self.button.configure(text=self.app.t('Update available', '有可用更新') if row.get('candidate') else
                              self.app.t('Update check failed', '更新检查失败') if state == 'error' else self.app.t('Check updates', '检查更新'))
        if (row.get('candidate') and state in ('available', 'ready', 'error') and not self.snoozed and not self.client.busy and not self.app.controller.busy
                and row['candidate']['revision'] != self.notified):
            self.open()
        key = (id(row), self.client.busy, self.app.controller.busy, self.app.zh)
        if key != self.last_state:
            self.last_state = key
            self.app.refresh()
            self.render()
        self.timer = self.app.window.after(150, self.poll)

    def stop(self):
        self.closed = True
        self.client.cancelled.set()
        if self.timer is not None:
            self.app.window.after_cancel(self.timer)
            self.timer = None

    def destroyed(self, event):
        if event.widget == self.app.window:
            self.stop()
