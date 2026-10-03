"""Disk estimates and live guards for the automatic Windows space saver."""
from pathlib import Path
import shutil
import hashlib
import json

GiB = 1 << 30
SPACE_FLOOR = 2 * GiB


def existing(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def add(disks, path, amount):
    parent = existing(path)
    disk = disks.setdefault(parent.stat().st_dev,
        dict(paths=[], needed_bytes=0, free_bytes=shutil.disk_usage(parent).free))
    if str(parent) not in disk['paths']:
        disk['paths'].append(str(parent))
    disk['needed_bytes'] += amount


def errors(disks):
    return [('Insufficient disk space: need %.2f GiB, available %.2f GiB, short %.2f GiB · %s' %
             (d['needed_bytes'] / GiB, d['free_bytes'] / GiB,
              (d['needed_bytes'] - d['free_bytes']) / GiB, ', '.join(d['paths'])))
            for d in disks if d['needed_bytes'] > d['free_bytes']]


def frontend_ready(root, frontend):
    """An interrupted engine install can already have a completed frontend."""
    try:
        root, comfy = Path(root), Path(frontend['root'])
        record = json.loads((root / 'launcher' / 'comfy-host.json').read_text(encoding='utf-8'))
        identity = record['identity']
        env_root = Path(record['environment'])
        return (env_root.resolve().parent == (root / 'envs').resolve()
                and Path(record['python']).is_file()
                and Path(record['python']).resolve().is_relative_to(env_root.resolve())
                and Path(identity['engine_python']).resolve() == (root / 'envs/unified/Scripts/python.exe').resolve()
                and Path(identity['comfy']).resolve() == comfy.resolve()
                and identity['requirements'] == hashlib.sha256((comfy / 'requirements.txt').read_bytes()).hexdigest()
                and json.loads((env_root / 'freevideo-host.json').read_text(encoding='utf-8')) == identity)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def budget(groups, root, normal_extra, *, eligible, environments_ready, frontend=None, keep_extreme=False):
    """Estimate each volume for the selected installation strategy."""
    frontend = frontend or {}
    def estimate(extreme):
        disks = {}
        for group in groups.values():
            add(disks, group['path'], group['needed_bytes'] - (group.get('resume_bytes', 0) if extreme else 0))
        extra = (1 if environments_ready else 6) if extreme else normal_extra
        add(disks, root, extra * GiB)
        if frontend.get('separate') and not (extreme and frontend_ready(root, frontend)):
            add(disks, root, (3 if extreme else 12) * GiB)
        if frontend.get('download') and not Path(frontend['root']).exists():
            add(disks, frontend['root'], GiB)
        if extreme:
            for disk in disks.values():
                disk['needed_bytes'] += SPACE_FLOOR
        return list(disks.values()), extra + (2 if extreme else 0)
    normal, extra = estimate(False)
    extreme = eligible and (keep_extreme or bool(errors(normal)))
    disks, extra = estimate(True) if extreme else (normal, extra)
    return dict(mode='extreme' if extreme else 'normal', disks=disks,
                environment_cache_safety_gib=extra, errors=errors(disks),
                ordinary_needed_bytes=sum(d['needed_bytes'] for d in normal),
                safety_bytes_per_volume=SPACE_FLOOR if extreme else 0)


def environment(root, env):
    """Bound package expansion and keep its temporary files on the chosen disk."""
    temporary = Path(root) / 'downloads' / 'install-temp'
    temporary.mkdir(parents=True, exist_ok=True)
    return dict(env, UV_CONCURRENT_DOWNLOADS='1', UV_CONCURRENT_INSTALLS='1',
                UV_CONCURRENT_BUILDS='1', UV_LINK_MODE='hardlink',
                TMP=str(temporary), TEMP=str(temporary), TMPDIR=str(temporary))


def check_floor(paths):
    seen = set()
    for path in paths:
        parent = existing(path)
        device = parent.stat().st_dev
        if device in seen:
            continue
        seen.add(device)
        free = shutil.disk_usage(parent).free
        if free < SPACE_FLOOR:
            raise RuntimeError('Space-saving installation paused: less than 2 GiB free on %s '
                               '(%.2f GiB available). Free space and resume; downloaded files are retained.' %
                               (parent, free / GiB))


def check_models(entries):
    """Recheck actual free disk AFTER package cleanup, before writing any models."""
    disks = {}
    for row, path, _, local in entries:
        remaining = row['bytes']
        if path.is_file() and path.stat().st_size == remaining:
            remaining = 0  # provision still verifies its pinned content.
        elif local and local.get('method') == 'hardlink':
            remaining = 0
        else:
            partial = path.with_suffix(path.suffix + '.partial')
            if partial.is_file() and partial.stat().st_size <= remaining:
                remaining -= partial.stat().st_size
        add(disks, path.parent, remaining)
    for disk in disks.values():
        disk['needed_bytes'] += SPACE_FLOOR
    problems = errors(disks.values())
    if problems:
        raise RuntimeError('\n'.join(problems) + '\nFree space and resume; installed components and downloaded files are retained.')
