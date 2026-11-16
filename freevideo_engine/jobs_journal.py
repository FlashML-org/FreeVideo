"""The short record of finished generations that a restart or a closed page cannot lose.

One file in the output root, written by the server process only: a row is added when a job ends
(done, failed, cancelled or interrupted) and rows older than a week, or beyond the newest 200, are dropped.
A write that fails is logged and never reaches the job; a damaged file reads as empty."""
import json
import logging
import math
import re
import threading
import time

from .long_paths import io_path
from . import monitoring

NAME = 'jobs-journal.json'
LIMIT = 200
KEEP_SECONDS = 7 * 24 * 3600
# Only these fields are kept. A prompt, a reference name or a path never reaches the file: it records that a job ran, not what it was about.
FIELDS = ('id', 'kind', 'origin', 'status', 'phase', 'created_at', 'started_at', 'finished_at', 'result', 'failure')
FINAL = ('done', 'failed', 'cancelled', 'interrupted')
_LOCK = threading.Lock()
_LOG = logging.getLogger(__name__)


TIMES = ('created_at', 'started_at', 'finished_at')
CODE = re.compile(r'[A-Za-z0-9_.:-]{1,64}')
LIBRARY = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}/[A-Za-z0-9_-]{1,64}')
CHOICES = dict(kind=('video', 'image', 'chain'), status=('queued', 'running') + FINAL, origin=('page', 'agent'))


def clean(row):
    """Keep a field only when its value is a plain number or a short code; anything else is dropped, so nothing
    free-form (a message, a path, a prompt) can reach the file. None when the row has no usable id."""
    if not isinstance(row, dict):
        return None
    out = {}
    for key in FIELDS:
        value = row.get(key)
        if key in TIMES:
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                out[key] = float(value)
        elif key in CHOICES:
            if value in CHOICES[key]:
                out[key] = value
        elif isinstance(value, str) and (CODE.fullmatch(value) or (key == 'result' and LIBRARY.fullmatch(value))):
            out[key] = value
    return out if 'id' in out else None


def _rows(root):
    path = io_path(root / NAME)
    try:
        value = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    except (OSError, ValueError):
        return []
    rows = value.get('jobs') if isinstance(value, dict) else None
    return [row for row in map(clean, rows) if row] if isinstance(rows, list) else []


def _recent(rows, now):
    kept = [row for row in rows if now - row.get('finished_at', 0.) <= KEEP_SECONDS]
    return kept[-LIMIT:]


def read(*, root):
    """The recorded jobs, oldest first; an unreadable file gives an empty list."""
    with _LOCK:
        return _recent(_rows(root), time.time())


def append(row, *, root):
    """Record a job that has ended. A row with the same id replaces the earlier one. Only the listed fields are kept. Never raises."""
    try:
        row = clean(row)
        if row is None or row.get('status') not in FINAL:
            return
        now = time.time()
        row.setdefault('finished_at', now)
        with _LOCK:
            rows = [old for old in _rows(root) if old['id'] != row['id']]
            rows.append(row)
            monitoring.save(root / NAME, dict(jobs=_recent(rows, now)))
    except Exception:
        _LOG.warning('The finished-job record could not be written.', exc_info=True)


def mark_interrupted(*, root):
    """At start-up: a row still queued or running belongs to a process that is gone."""
    try:
        with _LOCK:
            rows, now, changed = _rows(root), time.time(), False
            for row in rows:
                if row.get('status') in ('queued', 'running'):
                    row['status'], row['finished_at'], changed = 'interrupted', now, True
            if changed:
                monitoring.save(root / NAME, dict(jobs=_recent(rows, now)))
    except Exception:
        _LOG.warning('The finished-job record could not be updated.', exc_info=True)
