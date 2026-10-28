"""Stop sampling after the current step and decode a watchable video + audio."""
from pathlib import Path
import threading

LOCK = threading.Lock()
RUNS = {}


def flag_path(output):
    return Path(output).expanduser().resolve().parent / 'keep-preview'


def register(node_id, run_dir):
    if node_id is None or run_dir is None:
        return
    with LOCK:
        RUNS[str(node_id)] = Path(run_dir) / 'keep-preview'


def unregister(node_id):
    if node_id is None:
        return
    with LOCK:
        RUNS.pop(str(node_id), None)


def request(node_id):
    with LOCK:
        path = RUNS.get(str(node_id))
    if path is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('keep', encoding='utf-8')
    return True


def requested(output):
    path = flag_path(output)
    try:
        return path.is_file()
    except OSError:
        return False
