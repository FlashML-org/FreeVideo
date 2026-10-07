"""What the browser page reported about loading FreeVideo (web/health.js).

The launcher reads the report from its ComfyUI server off the UI thread and
shows a failed or silent page in its failure card.
"""
import threading
import time

# A page that loaded FreeVideo always reports; none this long after opening
# the browser means FreeVideo never ran there.
SILENT_AFTER = 90
POLL_EVERY = 3


class PageCheck:
    def __init__(self):
        self.url = None
        self.report = {}
        self.polled = float('-inf')
        self.thread = None

    def reset(self):
        self.url, self.report = None, {}

    def poll(self, url, now=None):
        """Read the server's latest page report in the background, every few seconds."""
        now = time.monotonic() if now is None else now
        if url != self.url:
            self.url, self.report = url, {}
        if now - self.polled < POLL_EVERY or (self.thread and self.thread.is_alive()):
            return
        self.polled = now

        def read():
            from .comfy_launcher_runtime import get_json
            try:
                value = get_json(url + '/freevideo/launcher/ui', timeout=2)
            except (OSError, ValueError):
                return
            if self.url == url:
                self.report = value if isinstance(value, dict) else {}

        self.thread = threading.Thread(target=read, daemon=True, name='FreeVideo-page-check')
        self.thread.start()

    def text(self, t, opened_at, address, now=None):
        """The failure text for a page that failed or never reported; '' otherwise."""
        report = self.report
        if report.get('state') == 'error':
            problems = [p for p in report.get('problems') or [] if isinstance(p, str) and p]
            lines = [t('The browser page did not load FreeVideo completely.', '浏览器页面没有完整加载 FreeVideo。')]
            lines += ['- ' + p for p in problems] or ['- ' + t('No details were sent.', '页面没有发送详情。')]
            if report.get('agent'):
                lines.append(t('Browser: ', '浏览器：') + str(report['agent']))
            if report.get('frontend'):
                lines.append(t('ComfyUI frontend: ', 'ComfyUI 前端：') + str(report['frontend']))
            return '\n'.join(lines)
        now = time.monotonic() if now is None else now
        if not report.get('state') and opened_at is not None and now - opened_at >= SILENT_AFTER:
            return t('The browser has not opened FreeVideo yet. If no browser window appeared, click “Open FreeVideo”. '
                     'If the page stays blank or keeps loading, open %s in a current Chrome or Edge.',
                     '浏览器还没有打开 FreeVideo。如果没有弹出浏览器窗口，请点击“打开 FreeVideo”；'
                     '如果页面空白或一直在加载，请用最新版 Chrome 或 Edge 打开 %s。') % address
        return ''
