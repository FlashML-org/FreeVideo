"""The launcher's own download of the prompt model: one background thread, pausable, resumable."""
from collections import deque
from pathlib import Path
import threading
import time

from ..locking import runtime_lock
from . import assets

SPEED_WINDOW_SECONDS = 20


class Installer:
    """state: idle, running, cancelled, failed (reason), done; done/total in bytes."""

    def __init__(self, root, *, busy=None):
        self.root = Path(root)
        self.generation_busy = busy
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.samples = deque()
        self.value = dict(state='idle', done=0, total=0, reason=None)

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def state(self):
        with self.lock:
            value = dict(self.value)
            now = time.monotonic()
            while len(self.samples) > 1 and self.samples[1][0] <= now - SPEED_WINDOW_SECONDS:
                self.samples.popleft()
            rate = 0
            if value['state'] == 'running' and self.samples:
                start, amount = self.samples[0]
                if now > start:
                    rate = max(0, value['done'] - amount) / (now - start)
            value['bytes_per_second'] = round(rate)
            return value

    def start(self):
        with self.lock:
            if self.busy:
                return
            self.stop = threading.Event()
            self.samples.clear()
            self.value = dict(state='running', done=0, total=0, reason=None)
            self.thread = threading.Thread(target=self._run, args=(self.stop,), daemon=True)
            self.thread.start()

    def cancel(self):
        self.stop.set()

    def _progress(self, row):
        with self.lock:
            if row.get('phase') == 'download':
                self.value.update(done=row['done'], total=row['total'])
                self.samples.append((time.monotonic(), row['done']))

    def _run(self, stop):
        def check():
            if stop.is_set():
                raise InterruptedError('cancelled')
        try:
            # The engine's own install and this one never write the same partial files.
            with runtime_lock(self.root / 'prompt-vlm-install.lock', inherit=False):
                assets.install(self.root, self._progress, check)
            with self.lock:
                self.value.update(state='done', reason=None)
        except InterruptedError:
            with self.lock:
                self.value.update(state='cancelled', reason=None)
        except BlockingIOError:
            with self.lock:
                self.value.update(state='failed', reason='busy')
        except Exception as error:
            known = {'disk_space', 'ram_space'}
            with self.lock:
                self.value.update(state='failed', reason=str(error) if str(error) in known else failure_reason(error))


def failure_reason(error):
    """What stopped the download, in words the card can act on; anything else stays 'download_failed'."""
    import errno
    from ..network import DownloadError
    text = str(error).lower()
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return 'disk_space'
    if isinstance(error, PermissionError) or (isinstance(error, OSError) and error.errno in (errno.EROFS, errno.EACCES, errno.EIO)):
        return 'write_failed'
    if any(word in text for word in ('sha256', 'checksum', 'hash mismatch', 'digest')):
        return 'verify_failed'
    if isinstance(error, (DownloadError, ConnectionError, TimeoutError)) or any(word in text for word in ('timed out', 'connection', 'resolve')):
        return 'network_failed'
    return 'download_failed'
