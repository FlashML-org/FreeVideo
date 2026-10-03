"""Optional browser update notices; no installation or generation side effects."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
import time

from . import launcher_update


def installed_build(package):
    """Use the loaded engine's release stamp, never the source fallback version."""
    package = Path(package)
    try:
        return launcher_update.build_identity(json.loads(
            (package / 'build-identity.json').read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        pass
    # EXEs built before browser notices only stamped this timestamp file.
    try:
        version = (package / 'build-version.txt').read_text(encoding='utf-8').strip()
        if not re.fullmatch(r'\d{4}\.\d{1,2}\.\d{1,2}\.\d{1,6}', version):
            return None
        year, month, day, clock = map(int, version.split('.'))
        stamp = datetime(year, month, day, clock // 10000, clock // 100 % 100,
                         clock % 100, tzinfo=timezone.utc)
        return dict(version=version, built_at=int(stamp.timestamp()), revision=None)
    except (OSError, ValueError):
        return None


class UpdateStatus:
    """One bounded background check per server, shared by all browser tabs."""
    def __init__(self, current):
        self.current = current
        self.state = dict(status='idle' if current else 'source',
                          current_version=current['version'] if current else None,
                          available=None)
        self.next_check = 0
        self.lock = threading.Lock()
        self.thread = None

    def snapshot(self):
        with self.lock:
            if (self.current and time.monotonic() >= self.next_check
                    and not (self.thread and self.thread.is_alive())):
                self.state['status'] = 'checking'
                self.thread = threading.Thread(target=self._check, daemon=True,
                                               name='FreeVideo-update-check')
                self.thread.start()
            return dict(self.state)

    def _check(self):
        try:
            candidate = launcher_update.latest_release()
            newer = (candidate['built_at'] > self.current['built_at']
                     and candidate['revision'] != self.current['revision'])
            available = {key: candidate[key] for key in ('version', 'revision', 'built_at')} if newer else None
            with self.lock:
                self.state.update(status='available' if available else 'current', available=available)
                self.next_check = time.monotonic() + 15 * 60
        except Exception:
            # Offline/rate-limited checks must not interrupt a running server.
            # Keep a previously verified notice and retry without a user action.
            with self.lock:
                self.state['status'] = 'unavailable'
                self.next_check = time.monotonic() + 60


def register():
    from aiohttp import web
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_updates', None):
        return
    # Capture once: replacing files on disk cannot update a running engine.
    status = UpdateStatus(installed_build(Path(__file__).parent))
    server._freevideo_updates = status

    @server.routes.get('/freevideo/updates')
    async def updates(request):
        return web.json_response(status.snapshot(), headers={'Cache-Control': 'no-store'})
