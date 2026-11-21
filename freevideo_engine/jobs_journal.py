"""The short record of finished generations that a restart or a closed page cannot lose.

One file in the output root, written by the server process only: a row is added when a job ends
(done, failed, cancelled or interrupted) and rows older than a week, or beyond the newest 200, are dropped.
A write that fails is logged and never reaches the job; a damaged file reads as empty."""
import json
import logging
import math
import os
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


_SWEPT = set()


def _sweep(root):
    """Once per process and output folder: a row naming a saved video that is no longer in the library goes. This
    clears rows left by deletions made before the record forgot them. Nothing is dropped while the library folder
    itself is missing (an unplugged drive) or any check fails other than "not found", and a row without a video is kept. Caller holds _LOCK. Never raises."""
    key = str(root)
    if key in _SWEPT:
        return
    _SWEPT.add(key)
    try:
        library = io_path(root / 'FreeVideo')
        if not library.is_dir() or not io_path(root / NAME).is_file():
            return
        os.listdir(library)  # a library that cannot be listed is not known to be empty
        rows = _rows(root)
        kept = []
        for row in rows:
            if 'result' in row:
                try:
                    (library / row['result'] / 'video.mp4').stat()
                except FileNotFoundError:
                    continue
                except OSError:
                    return  # no permission, a drive going away: the answer is open, so nothing is dropped
            kept.append(row)
        if len(kept) != len(rows):
            monitoring.save(root / NAME, dict(jobs=kept))
    except Exception:
        _LOG.warning('The finished-job record could not be updated.', exc_info=True)


def read(*, root):
    """The recorded jobs, oldest first; an unreadable file gives an empty list."""
    with _LOCK:
        _sweep(root)
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


def forget(results, *, root):
    """A saved video the user deleted for good: the rows naming it go, so no line of the record points at it. Never raises."""
    try:
        results = {value for value in results if isinstance(value, str)}
        if not results or not io_path(root / NAME).is_file():
            return
        with _LOCK:
            rows = _rows(root)
            kept = [row for row in rows if row.get('result') not in results]
            if len(kept) != len(rows):
                monitoring.save(root / NAME, dict(jobs=kept))
    except Exception:
        _LOG.warning('The finished-job record could not be updated.', exc_info=True)


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
