"""Two host facts for the diagnostic report: booleans and integers only, no names or paths."""
import json
import os
from pathlib import Path
import time

from .comfy_source import RECEIPT

COUNTER = 'comfy-host-stats.json'
DAYS = 30
OURS = ('FreeVideo', 'FreeVideoExecutor')
# Files ComfyUI itself ships in custom_nodes.
UPSTREAM = ('websocket_image_save.py',)


def counter_path(root=None):
    if root is None:
        from .comfy_bridge import installation_root
        root = installation_root()
    return Path(root) / COUNTER


def _day(now):
    return time.strftime('%Y-%m-%d', time.gmtime(now))


def record_node_view(root=None, now=None):
    """Count one opening of the node canvas on today's date; days older than the window are dropped."""
    now = time.time() if now is None else now
    path = counter_path(root)
    try:
        days = json.loads(path.read_text(encoding='utf-8')).get('days', {})
    except (OSError, ValueError, AttributeError):
        days = {}
    if not isinstance(days, dict):
        days = {}
    cutoff = _day(now - DAYS * 86400)
    days = {k: v for k, v in days.items() if isinstance(v, int) and not isinstance(v, bool) and k > cutoff}
    today = _day(now)
    days[today] = days.get(today, 0) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.%d.tmp' % os.getpid())
    temporary.write_text(json.dumps(dict(days=days)), encoding='utf-8')
    os.replace(temporary, path)


def node_view_opens(root=None, now=None):
    now = time.time() if now is None else now
    try:
        days = json.loads(counter_path(root).read_text(encoding='utf-8')).get('days', {})
        cutoff = _day(now - DAYS * 86400)
        return sum(v for k, v in days.items() if k > cutoff and isinstance(v, int) and not isinstance(v, bool) and v > 0)
    except (OSError, ValueError, AttributeError, TypeError):
        return 0


def third_party_packs(folders):
    names = set()
    for folder in folders:
        try:
            entries = list(Path(folder).iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name in OURS or name in UPSTREAM or name.lower().startswith('freevideo-') or name.startswith(('.', '__')) or name.endswith('.disabled'):
                continue
            if entry.is_dir() or name.endswith('.py'):
                names.add(str(entry.resolve()))
    return len(names)


def _managed(comfy_root, install_root):
    comfy_root = Path(comfy_root).resolve()
    if (comfy_root / RECEIPT).is_file():
        return True
    try:
        # The portable package and the launcher keep their ComfyUI inside the FreeVideo folder.
        comfy_root.relative_to(Path(install_root).resolve())
        return True
    except ValueError:
        return False


def host_fields(comfy_root=None, node_folders=None, root=None, now=None):
    """own_comfyui: ComfyUI is not the copy FreeVideo installed, or it has third-party node packs."""
    try:
        if comfy_root is None:
            import folder_paths
            comfy_root = folder_paths.base_path
            node_folders = folder_paths.get_folder_paths('custom_nodes')
    except (ImportError, AttributeError, OSError):
        return None
    if root is None:
        from .comfy_bridge import installation_root
        root = installation_root()
    packs = third_party_packs(node_folders if node_folders is not None else [Path(comfy_root) / 'custom_nodes'])
    return dict(own_comfyui=bool(packs) or not _managed(comfy_root, root),
                third_party_node_packs=packs,
                node_view_opens_30d=node_view_opens(root, now))
