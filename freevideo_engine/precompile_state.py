"""Best-effort precompile observations, separate from cache-validity receipts.

Nothing in the scheduler reads this file to decide whether or what to compile.
Only fixed states, reason codes, numeric shape keys and process identity live
here. Process identity stays local and prevents a crashed run looking active.
"""
import collections
import json
import os
from pathlib import Path
import threading
import time
from time import monotonic

from . import diagnostic_resources as diagnostic
from .diagnostics import read_bounded
from .locking import runtime_lock
from .long_paths import io_path
from .monitoring import save

FILENAME = 'precompile_state.json'


def read(path, limit):
    try:
        raw, truncated = read_bounded(path, limit)
        value = json.loads(raw) if not truncated else None
        return (value, 'available') if isinstance(value, dict) else ({}, 'invalid')
    except FileNotFoundError:
        return {}, 'missing'
    except Exception:
        return {}, 'invalid'


def reason_code(error):
    """Classify existing GiveWay messages; never persist their free text."""
    text = str(error)
    if text == 'stopped by FreeVideo':
        return 'foreground_work'
    for prefix, code in (('memory:', 'ram_pressure'), ('gpu busy:', 'gpu_busy'),
                         ('gpu memory:', 'gpu_memory_pressure'),
                         ('gpu: its processes cannot be listed', 'gpu_processes_unavailable'),
                         ('gpu: another program started', 'gpu_in_use'),
                         ('gpu: another program keeps it busy', 'gpu_busy')):
        if text.startswith(prefix):
            return code
    return 'unknown'


# Callers hold the compile guard or the runtime lease, so they only queue a
# change; one daemon thread does the file I/O in the order changes were made.
_queue = collections.deque()
_wake = threading.Condition()
_writer = None
_queued = _written = 0
_final = set()  # Roots whose last change for this process has been queued.


def record(root, state, *, reason=None, started_at=None, shapes=None, pid=None, final=False):
    """Diagnostic writes must never change compilation, cancellation or retries.

    `final` marks the session's last word: later non-final changes from this
    process are dropped, so a racing idle wait cannot overwrite a stop.
    """
    global _queued, _writer
    if root is None:
        return
    try:
        change = dict(root=Path(root), state=state, reason=reason, started_at=started_at, at=time.time(),
                      shapes=None if shapes is None else covered_shapes(shapes),
                      pid=(pid or os.getpid()) if state == 'running' else None)
        with _wake:
            if change['root'] in _final and not final:
                return
            if final:
                _final.add(change['root'])
            _queued += 1
            change['sequence'] = _queued
            _queue.append(change)
            if _writer is None:
                _writer = threading.Thread(target=_write, name='FreeVideo precompile status', daemon=True)
                _writer.start()
            _wake.notify_all()
    except Exception:
        pass


def flush(timeout=1., *, cancelled=None):
    """Wait up to `timeout` seconds for queued changes, yielding to cancellation."""
    deadline = monotonic() + timeout
    with _wake:
        target = _queued
        while _written < target:
            if cancelled is not None and cancelled.is_set():
                return False
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            _wake.wait(min(remaining, .02) if cancelled is not None else remaining)
        return True


def _write():
    global _written
    while True:
        with _wake:
            while not _queue:
                _wake.wait()
            change = _queue.popleft()
        try:
            _apply(change)
        except Exception:
            pass
        with _wake:
            _written = change['sequence']
            _wake.notify_all()


def _apply(change):
    path = change['root'] / FILENAME
    # Lock a stable sibling: save() replaces the JSON inode. Only this daemon
    # waits for other writers; never inherit or acquire the compilation lease.
    with runtime_lock(io_path(path.with_suffix('.json.lock')), inherit=False, wait_seconds=60.):
        previous, _ = read(path, 64 * 1024)
        value = diagnostic.precompile(previous)
        # Compare and replace under the same cross-process lock, so an older
        # observation cannot overwrite a newer stop or move its timestamp back.
        if value.get('updated_at', 0) > change['at']:
            return
        value.update(schema_version=1, state=change['state'], updated_at=change['at'])
        if change['started_at'] is not None:
            value['started_at'] = change['started_at']
        if change['shapes'] is not None:
            value['covered_shapes'] = change['shapes']
        if change['reason'] is not None:
            value['last_postpone'] = dict(reason=change['reason'] if change['reason'] in diagnostic.PRECOMPILE_REASONS
                                          else 'unknown', at=change['at'])
        if change['pid'] is not None:
            try:
                import psutil
                value.update(pid=change['pid'], created=psutil.Process(change['pid']).create_time())
            except Exception:
                pass  # Exited already: without an identity the state reads as unknown.
        save(path, value)


def covered_shapes(rows):
    return [shape for row in diagnostic.sequence(rows)[:16]
            if type(diagnostic.mapping(row).get('returncode')) is int and row['returncode'] == 0
            if (shape := diagnostic.precompile_shape(row.get('shape')))]


def collect(root=None):
    """Read bounded files only; no GPU queries, cache probes or tensor imports."""
    if root is None:
        from .system import install_root
        root = install_root()
        # The ComfyUI parent can select a separate engine through comfyui.json
        # without setting FREEVIDEO_HOME in its own process. Match the bridge's
        # selection, including paths relative to the source checkout.
        if not os.environ.get('FREEVIDEO_HOME'):
            source = Path(__file__).resolve().parents[1]
            config, _ = read(source / 'comfyui.json', 64 * 1024)
            selected = config.get('installation')
            if isinstance(selected, str) and selected.strip():
                root = Path(selected).expanduser()
                if not root.is_absolute():
                    root = source / root
    receipt, receipt_status = read(Path(root) / 'precompile.json', 2 * 2**20)
    activity, activity_status = read(Path(root) / FILENAME, 64 * 1024)
    result = dict(state='not_started' if receipt_status == activity_status == 'missing' else 'unknown',
                  covered_shapes=covered_shapes(receipt.get('shapes')), receipt=receipt_status,
                  scope='installation_snapshot')
    rows = receipt.get('shapes')
    complete = (receipt.get('success') is True and isinstance(rows, list)
                and 0 < len(rows) == len(result['covered_shapes']))
    if complete:
        result['state'] = 'complete'
    if activity.get('schema_version') == 1:
        current = diagnostic.precompile(activity)
        # A receipt published after the last activity write wins (for example
        # if the process exited between saving its receipt and saving status).
        finished = diagnostic.numbers(receipt, ('started', 'seconds'))
        completed_at = finished.get('started', 0) + finished.get('seconds', 0)
        newer_receipt = complete and completed_at > current.get('updated_at', 0)
        shapes = result['covered_shapes']
        if not newer_receipt:
            result.update(current)
            if result['state'] == 'running' and not active(activity):
                result['state'] = 'unknown'
            for shape in current['covered_shapes']:
                if shape not in shapes:
                    shapes.append(shape)
        result['covered_shapes'] = shapes
        if current.get('last_postpone'):
            result['last_postpone'] = current['last_postpone']
    if result['state'] == 'complete' and not complete:
        result['state'] = 'unknown'
    return diagnostic.precompile(result)


def active(value):
    try:
        import psutil
        if type(value.get('pid')) is not int or value['pid'] <= 0:
            return False
        process = psutil.Process(value['pid'])
        return (process.create_time() == value.get('created') and process.is_running()
                and process.status() != psutil.STATUS_ZOMBIE)
    except Exception:
        return False
