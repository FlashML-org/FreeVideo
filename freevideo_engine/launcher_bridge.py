"""File handoff between the launcher and the ComfyUI server it started.

The server only records an explicit browser request; the launcher itself
checks, downloads the verified release and restarts. Standard library only.
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import time

from .monitoring import save

ENV = 'FREEVIDEO_LAUNCHER_BRIDGE'
# The launcher rewrites its status at least this often while it runs.
HEARTBEAT_SECONDS = 5
FRESH_SECONDS = 20


def create(root):
    parent = Path(root) / 'bridge'
    parent.mkdir(parents=True, exist_ok=True)
    # Folders of launchers that ended without cleanup are only advisory.
    for old in parent.iterdir():
        try:
            if old.is_dir() and time.time() - old.stat().st_mtime > 86400:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
    path = parent / ('%d-%s' % (os.getpid(), secrets.token_hex(4)))
    path.mkdir()
    return path


def remove(path):
    if path:
        shutil.rmtree(path, ignore_errors=True)


def write_status(path, value):
    save(Path(path) / 'status.json', dict(value, updated_at=time.time()))


def read_status(path, *, max_age=FRESH_SECONDS):
    """A recent launcher status, or None when no launcher is listening."""
    try:
        value = json.loads((Path(path) / 'status.json').read_text(encoding='utf-8'))
        if isinstance(value, dict) and abs(time.time() - float(value.get('updated_at', 0))) <= max_age:
            return value
    except (OSError, ValueError, TypeError):
        pass
    return None


def request(path, action='update'):
    if action not in ('update', 'cancel'):
        raise ValueError('Unknown launcher request')
    save(Path(path) / 'request.json', dict(action=action, at=time.time()))


def take_request(path):
    file = Path(path) / 'request.json'
    claimed = file.with_name('.request-' + secrets.token_hex(8) + '.json')
    try:
        # Claim one receipt before reading it. A browser action written while
        # it is consumed stays at request.json for the next launcher tick.
        file.rename(claimed)
    except OSError:
        return None
    try:
        value = json.loads(claimed.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        value = None
    try:
        claimed.unlink()
    except OSError:
        pass
    return value if isinstance(value, dict) and value.get('action') in ('update', 'cancel') else None
