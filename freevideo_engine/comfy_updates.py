"""Optional browser update notices. Applying an update is delegated to the
launcher that started this server; the server itself never installs anything."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import time

from . import launcher_update
from .release_notes import installed_details, public_details


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


def launcher_view(status):
    """The parts of a launcher status a page may show; None without a launcher."""
    if not isinstance(status, dict):
        return None
    view = {k: status.get(k) for k in ('version', 'phase', 'status', 'progress', 'candidate', 'engine', 'error', 'manual', 'channel', 'track')}
    view['phase'] = str(view['phase'] or '')
    return view


class UpdateStatus:
    """One bounded background check per server, shared by all browser tabs.

    When the launcher that started this server is running, its own release
    check and engine comparison are used instead of a second GitHub request.
    """
    def __init__(self, current, bridge=None):
        self.current = current
        self.bridge = bridge
        self.state = dict(status='idle' if current else 'source',
                          current_version=current['version'] if current else None,
                          current_release=public_details(current) if current else installed_details(Path(__file__).parent),
                          channel=(current or {}).get('channel', launcher_update.CHANNEL),
                          track=launcher_update.build_track(current or {}),
                          available=None)
        self.next_check = 0
        self.client_seen = -1e9
        self.lock = threading.Lock()
        self.thread = None

    def launcher(self):
        if not self.bridge:
            return None
        from .launcher_bridge import read_status
        return launcher_view(read_status(self.bridge))

    def snapshot(self, *, client=False):
        launcher = self.launcher()
        with self.lock:
            now = time.monotonic()
            if client:
                self.client_seen = now
            if (launcher is None and self.current and now >= self.next_check
                    and not (self.thread and self.thread.is_alive())):
                self.state['status'] = 'checking'
                self.thread = threading.Thread(target=self._check, daemon=True,
                                               name='FreeVideo-update-check')
                self.thread.start()
            state = dict(self.state)
            # Pages that reload themselves after an update polled recently.
            state['clients'] = int(now - self.client_seen < 20)
        if launcher is not None:
            if launcher.get('channel') in launcher_update.CHANNELS:
                state['channel'] = launcher['channel']
            if launcher.get('track') in launcher_update.TRACKS:
                state['track'] = launcher['track']
            engine = launcher.get('engine') or {}
            candidate = launcher.get('candidate') if isinstance(launcher.get('candidate'), dict) else None
            available = (public_details(candidate) if candidate and candidate.get('version') else
                         public_details(engine) if engine.get('pending') and engine.get('version') else None)
            if self.current or available:
                state.update(status='available' if available else 'current', available=available)
            state['launcher'] = launcher
        return state

    def _check(self):
        try:
            channel = self.current.get('channel', launcher_update.CHANNEL)
            track = launcher_update.build_track(self.current)
            candidate = launcher_update.latest_release(channel=channel, track=track)
            if (candidate.get('channel', launcher_update.CHANNEL) != channel
                    or launcher_update.build_target(candidate) != launcher_update.build_target(self.current)
                    or launcher_update.build_track(candidate) != track):
                raise ValueError('Update belongs to a different platform')
            newer = (candidate['built_at'] > self.current['built_at']
                     and candidate['revision'] != self.current['revision'])
            available = public_details(candidate) if newer else None
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
    from .launcher_bridge import ENV, request as ask_launcher
    bridge = os.environ.get(ENV) or None
    status = UpdateStatus(installed_build(Path(__file__).parent), bridge=bridge)
    server._freevideo_updates = status

    @server.routes.get('/freevideo/updates')
    async def updates(request):
        value = status.snapshot(client=request.query.get('client') == '1')
        return web.json_response(value, headers={'Cache-Control': 'no-store'})

    @server.routes.post('/freevideo/updates/apply')
    async def apply(request):
        # An explicit click in the page; the launcher verifies, downloads,
        # waits for running jobs and restarts. Without one, show the release.
        if status.launcher() is None:
            return web.json_response(dict(status='unavailable'), status=409)
        ask_launcher(bridge)
        return web.json_response(dict(status='requested'), headers={'Cache-Control': 'no-store'})

    @server.routes.post('/freevideo/updates/cancel')
    async def cancel(request):
        # Withdraws an update that still waits for running jobs.
        if status.launcher() is None:
            return web.json_response(dict(status='unavailable'), status=409)
        ask_launcher(bridge, 'cancel')
        return web.json_response(dict(status='cancelled'), headers={'Cache-Control': 'no-store'})
