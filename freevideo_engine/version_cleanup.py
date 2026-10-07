"""Remove FreeVideo's own superseded copies after an update; never models or user files.

Every launcher update leaves the previous executable twice (its download and the
copy behind the desktop shortcut) and the previous source twice (the launcher's
copy and the installation's). An update of the user's ComfyUI used to leave a
whole separate ComfyUI environment, PyTorch included. Once the new version runs
ComfyUI, none of these is read again. Removal is limited to copies FreeVideo
itself wrote:

* updates/<sha256>/ in the launcher folder: the executable that digest names,
  checked, and interrupted downloads; never the running or approved one, a
  newer one downloaded and waiting to start, nor one a shortcut, a pinned
  taskbar item or a Dock item starts;
* source/<version>-<hash>/ in the launcher and installation folders: unmodified
  copies (each file as recorded in launcher-source.json, plus the installation
  binding, compiled bytecode, that version's setup logs and its editable-install
  metadata) older than the launcher's own or the one ComfyUI loads; never
  those, the one a running ComfyUI reports or one an environment installed in
  editable mode;
* launcher/application/<sha256>/ in the installation: the launcher an earlier
  desktop shortcut started, checked by its digest; never the current target or
  one any shortcut, pinned item or Dock item starts;
* envs/comfyui-*: a separate environment for this same ComfyUI folder that the
  current one replaced;
* the downloaded PyTorch wheel archives, once every environment has them.

Every path lies inside its folder and is reached without links or junctions. A
copy still in use is kept for the next run: no running process may name it as
its program, working folder, argument or PYTHONPATH, and Windows must allow
renaming the folder (it refuses while files inside are open). Package caches,
environments in use, models and outputs are never touched.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import uuid

from .installation_cleanup import local_path
from .monitoring import save
from .system import windows

VERSION = re.compile(r'\d{4}\.\d{1,2}\.\d{1,2}\.\d{1,6}-[0-9a-f]{16}(?:-[0-9a-f]{8})?')
DIGEST = re.compile(r'[0-9a-f]{64}(?:-[0-9a-f]{8})?')
ENVIRONMENT = re.compile(r'comfyui-[0-9a-f]{16}')
RETIRED = re.compile(r'(?P<name>.+)\.retired-[0-9a-f]{8}')
EXECUTABLES = ('FreeVideo.exe', 'FreeVideo-Linux-x86_64.AppImage')


def tree_bytes(path):
    total = 0
    for directory, folders, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                info = os.lstat(os.path.join(directory, name))
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def plain_tree(path):
    """No links or junctions anywhere below path; anything unreadable counts as one."""
    def unreadable(error):
        raise error
    try:
        for directory, folders, files in os.walk(path, followlinks=False, onerror=unreadable):
            for name in [*folders, *files]:
                info = os.lstat(os.path.join(directory, name))
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    return False
    except OSError:
        return False
    return True


def references():
    """Every path a running process names: its program, working folder, arguments
    and PYTHONPATH, which is how FreeVideo starts workers from a source folder."""
    try:
        import psutil
    except ImportError:
        return None  # Unknown: everything counts as in use.
    found = []
    for process in psutil.process_iter(['exe', 'cwd', 'cmdline']):
        info = process.info
        values = [info.get('exe'), info.get('cwd'), *(info.get('cmdline') or ())]
        try:
            values += (process.environ().get('PYTHONPATH') or '').split(os.pathsep)
        except (psutil.Error, OSError):
            pass  # Another user's process cannot run our copies.
        found += [os.path.normcase(str(value)) for value in values if value]
    return found


def started_from(path, running):
    """Whether a running process uses path, by the references captured for this run."""
    if running is None:
        return True
    prefix = os.path.normcase(os.path.abspath(path))
    # The folder itself, a path inside it, or an option ending with it (--root=...).
    return any(value.endswith(prefix) or prefix + os.sep in value for value in running)


def remove(path, root, running=None):
    """Rename, then delete; a folder Windows cannot rename is still in use.

    A deletion that stops part-way leaves the renamed folder, which a later run
    finishes; nothing else ever has that name.
    """
    path, root = Path(path), Path(root)
    if not local_path(root, path) or not plain_tree(path) or started_from(path, running):
        return 0
    size = tree_bytes(path)
    retired = path.with_name(path.name + '.retired-' + uuid.uuid4().hex[:8])
    try:
        os.rename(path, retired)
    except OSError:
        return 0
    shutil.rmtree(retired, ignore_errors=True)
    return size if not retired.exists() else size - tree_bytes(retired)


def finish_retired(parent, root, pattern):
    """Folders an earlier run renamed but could not finish deleting."""
    released = 0
    for path in sorted(Path(parent).glob('*.retired-*')):
        match = RETIRED.fullmatch(path.name)
        if match and pattern.fullmatch(match['name']) and path.is_dir() and local_path(Path(root), path):
            size = tree_bytes(path)
            shutil.rmtree(path, ignore_errors=True)
            released += size if not path.exists() else 0
    return released


def unchanged_source(path):
    """A materialized source exactly as deployed: launcher-source.json still describes every file."""
    try:
        rows = json.loads((path / 'launcher-source.json').read_text(encoding='utf-8'))['sha256']
        if not isinstance(rows, dict) or not rows:
            return False
        seen = set()
        for directory, folders, files in os.walk(path, followlinks=False):
            for name in files:
                item = Path(directory) / name
                relative = item.relative_to(path).as_posix()
                if relative in rows:
                    if digest(item) != rows[relative]:
                        return False
                    seen.add(relative)
                elif relative not in ('launcher-source.json', 'comfyui.json') and not (
                        item.parent.name == '__pycache__' and item.suffix == '.pyc') and not (
                        # That version's setup logs and editable-install metadata.
                        relative.startswith(('.freevideo/comfy-setup/', 'freevideo_engine.egg-info/'))):
                    return False  # Something the user added.
        return seen == set(rows)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def editable_sources(engine):
    """Sources an environment installed in editable mode still imports from."""
    from urllib.parse import urlsplit
    from urllib.request import url2pathname
    found = set()
    for pattern in ('envs/*/Lib/site-packages/freevideo_engine-*.dist-info/direct_url.json',
                    'envs/*/lib/python3*/site-packages/freevideo_engine-*.dist-info/direct_url.json'):
        for path in Path(engine).glob(pattern):
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                url = urlsplit(value['url'])
                if url.scheme == 'file' and (value.get('dir_info') or {}).get('editable'):
                    found.add(url2pathname(url.netloc and '//' + url.netloc + url.path or url.path))
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                found.add(None)  # An unreadable record could name any source.
    return found


def resolved(value):
    try:
        return Path(value).resolve() if value else None
    except (OSError, ValueError, TypeError):
        return None


def version(path):
    """The build version a source folder is named after, as numbers."""
    try:
        return tuple(int(part) for part in Path(path).name.split('-', 1)[0].split('.'))
    except (ValueError, TypeError):
        return None


def sources(parent, root, keep, current):
    """Source copies under parent older than the current one.

    A newer copy may belong to a newer launcher that is running; it is never
    an earlier version of this one.
    """
    rows = []
    parent = Path(parent)
    newest = version(current) if current else None
    if newest is None or not parent.is_dir() or not local_path(Path(root), parent):
        return rows
    keep = {path for path in map(resolved, keep) if path}
    for path in sorted(parent.iterdir()):
        if not VERSION.fullmatch(path.name) or path.is_symlink() or not path.is_dir() or path.resolve() in keep:
            continue
        if (version(path) or newest) < newest and unchanged_source(path):
            rows.append(path)
    return rows


def downloads(parent, root, keep):
    """Launcher updates other than the running and the approved one."""
    rows = []
    parent = Path(parent)
    if not parent.is_dir() or not local_path(Path(root), parent):
        return rows
    for path in sorted(parent.iterdir()):
        if not DIGEST.fullmatch(path.name) or path.is_symlink() or not path.is_dir() or path.name[:64] in keep:
            continue
        try:
            names = [item.name for item in path.iterdir()]
            executables = [name for name in names if name in EXECUTABLES]
            extras = [name for name in names if name not in EXECUTABLES and not (name.startswith('download-') and name.endswith('.partial'))]
            if extras or len(executables) > 1 or (executables and digest(path / executables[0]) != path.name[:64]):
                continue
        except OSError:
            continue  # Unreadable: kept.
        rows.append(path)
    return rows


def applications(parent, root, keep):
    """Launchers behind earlier desktop shortcuts, checked against the digest that names them."""
    rows = []
    parent = Path(parent)
    if not parent.is_dir() or not local_path(Path(root), parent):
        return rows
    keep = {path for path in map(resolved, keep) if path}
    for path in sorted(parent.iterdir()):
        if not DIGEST.fullmatch(path.name) or path.is_symlink() or not path.is_dir() or path.resolve() in keep:
            continue
        try:
            names = sorted(item.name for item in path.iterdir())
            if names == ['FreeVideo.app']:
                # The Mac copy is named by its executable and Info.plist digests.
                contents = path / 'FreeVideo.app' / 'Contents'
                actual = hashlib.sha256((digest(contents / 'MacOS' / 'FreeVideo') +
                                         digest(contents / 'Info.plist')).encode()).hexdigest()
            elif names in (['FreeVideo.exe'], ['FreeVideo.exe', '_internal'],
                           ['FreeVideo-Linux-x86_64.AppImage'], ['FreeVideo-Linux-x86_64.AppImage', 'freevideo.png']):
                actual = digest(path / names[0])
            else:
                continue
        except OSError:
            continue  # Unreadable: kept.
        if actual == path.name[:64]:
            rows.append(path)
    return rows


def environments(engine, current):
    """Separate ComfyUI environments for the current ComfyUI folder that the current one replaced."""
    rows = []
    try:
        current_identity = json.loads((current / 'freevideo-host.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return rows
    for path in sorted((engine / 'envs').glob('comfyui-*')):
        if (not ENVIRONMENT.fullmatch(path.name) or path.is_symlink() or not path.is_dir()
                or path.resolve() == current.resolve()):
            continue
        try:
            identity = json.loads((path / 'freevideo-host.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue  # Without a receipt the owner is uncertain.
        if isinstance(identity, dict) and identity.get('comfy') == current_identity.get('comfy'):
            rows.append(path)
    return rows


def torch_wheels(engine, pythons):
    """Downloaded PyTorch wheels, once every environment has exactly those versions installed."""
    if not windows():
        return []  # Other systems install PyTorch from the package cache.
    from .torch_download import catalog, installed_versions
    directory = engine / 'downloads' / 'torch-wheels'
    if not pythons or not directory.is_dir() or not local_path(engine, directory):
        return []
    rows = catalog()
    for python in pythons:
        installed = installed_versions(python)
        if any(installed.get(row['package']) != row['version'] for row in rows):
            return []
    found = []
    for row in rows:
        for path in (directory / row['filename'], *directory.glob(row['filename'] + '.partial*')):
            try:
                info = path.lstat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                found.append(path)
    return found


def shortcut_targets():
    """Programs the user's own shortcuts, pinned taskbar items or Dock items start.

    Pinning a running launcher records the path of that exact copy, so a copy
    a shortcut names stays even when it is old. None when they cannot be read.
    """
    found = set()
    try:
        if windows():
            from .desktop_shortcut import desktop_directory, read_link_targets
            folders = [desktop_directory()]
            for variable, relative in (('APPDATA', 'Microsoft/Windows/Start Menu'), ('PROGRAMDATA', 'Microsoft/Windows/Start Menu'),
                                       ('APPDATA', 'Microsoft/Internet Explorer/Quick Launch'), ('PUBLIC', 'Desktop')):
                if os.environ.get(variable):
                    folders.append(Path(os.environ[variable]) / relative)
            links = []
            for folder in folders:
                for path in (folder.rglob('*.lnk') if folder.is_dir() else ()):
                    try:
                        data = path.read_bytes()[:1 << 16]
                    except OSError:
                        continue
                    if b'FreeVideo' in data or 'FreeVideo'.encode('utf-16-le') in data:
                        links.append(path)
            found |= {Path(target) for target in read_link_targets(links).values()}
        elif sys.platform == 'linux':
            import shlex
            data = os.environ.get('XDG_DATA_HOME', '')
            folders = [Path(data) if os.path.isabs(data) else Path.home() / '.local/share', Path.home()]
            for path in [*(folders[0] / 'applications').glob('*.desktop'), *(folders[1] / 'Desktop').glob('*.desktop')]:
                try:
                    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
                except OSError:
                    continue
                for line in lines:
                    if line.startswith('Exec=') and 'FreeVideo' in line:
                        try:
                            found.add(Path(shlex.split(line[5:].replace('%%', '%'))[0]))
                        except (ValueError, IndexError):
                            continue
        elif sys.platform == 'darwin':
            import plistlib
            from urllib.parse import unquote, urlsplit
            dock = plistlib.loads((Path.home() / 'Library/Preferences/com.apple.dock.plist').read_bytes())
            for key in ('persistent-apps', 'persistent-others', 'recent-apps'):
                for item in dock.get(key, []):
                    url = ((item.get('tile-data') or {}).get('file-data') or {}).get('_CFURLString', '')
                    if url.startswith('file://'):
                        found.add(Path(unquote(urlsplit(url).path).rstrip('/')))
    except FileNotFoundError:
        pass  # No Dock preferences yet.
    except Exception:
        return None
    return {path for path in map(resolved, found) if path}


def superseded(candidate, current):
    """Whether the running launcher is this build or a newer one of the same kind."""
    if current is None:
        return False
    from .launcher_update import manifest, newer
    try:
        candidate = manifest(candidate)
        return candidate['revision'] == current['revision'] or newer(current, candidate)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def approved_digests(launcher_root, current=None):
    """The approved update, and a found one the running launcher would still offer."""
    keep = set()
    updates = Path(launcher_root) / 'updates'
    for name, read in (('active.json', lambda value: value['candidate']), ('available.json', lambda value: value)):
        try:
            found = read(json.loads((updates / name).read_text(encoding='utf-8')))
            digest = str(found['asset']['sha256'])
        except (OSError, ValueError, TypeError, KeyError):
            continue
        # A reminder of an update older than the running launcher is ignored by
        # the launcher too; only an unknown or newer one keeps its download.
        if name == 'available.json' and superseded(found, current):
            continue
        keep.add(digest)
    return keep


def run(*, launcher_root=None, launcher_source=None, launcher_build=None, running=(), engine=None,
        comfy_root=None, engine_source=None, server_source=None, receipt=True):
    """Remove what the current launcher and installation no longer use; report what was freed."""
    from .locking import runtime_lock
    result = dict(released_bytes=0, removed=[], kept=[])
    snapshot = references()

    def drop(path, root, kind):
        released = remove(path, root, snapshot)
        if not Path(path).exists():
            result['released_bytes'] += released
            result['removed'].append(dict(kind=kind, path=str(path), bytes=released))
        else:
            result['kept'].append(dict(kind=kind, path=str(path), reason='in use'))

    running = [path for path in map(resolved, running) if path]
    linked = shortcut_targets()
    if linked is None:
        result['kept'].append(dict(kind='launcher-copies', reason='shortcuts unreadable'))
    if launcher_root:
        launcher_root = Path(launcher_root).absolute()
        keep = approved_digests(launcher_root, launcher_build) | {path.parent.name[:64] for path in [*running, *(linked or ())]}
        for path in downloads(launcher_root / 'updates', launcher_root, keep) if linked is not None else ():
            drop(path, launcher_root, 'launcher-update')
        result['released_bytes'] += finish_retired(launcher_root / 'updates', launcher_root, DIGEST)
        editable = editable_sources(engine) if engine else set()
        if None not in editable:
            for path in sources(launcher_root / 'source', launcher_root, {launcher_source, *editable}, launcher_source):
                drop(path, launcher_root, 'launcher-source')
        result['released_bytes'] += finish_retired(launcher_root / 'source', launcher_root, VERSION)
    if engine:
        engine = Path(engine).absolute()
        receipt_source = None
        if comfy_root:
            try:
                from .comfy_launcher_runtime import node_target
                receipt_source = json.loads((node_target(comfy_root) / 'freevideo-launcher.json')
                                            .read_text(encoding='utf-8')).get('source')
            except (OSError, ValueError, TypeError):
                pass
        editable = editable_sources(engine)
        # Unknown which copy ComfyUI or an environment loads: keep them all.
        if receipt_source and None not in editable:
            for path in sources(engine / 'launcher' / 'source', engine,
                                {receipt_source, engine_source, server_source, *editable}, receipt_source):
                drop(path, engine, 'engine-source')
            result['released_bytes'] += finish_retired(engine / 'launcher' / 'source', engine, VERSION)
        try:
            target = json.loads((engine / 'launcher' / 'desktop-shortcut.json').read_text(encoding='utf-8')).get('target')
        except (OSError, ValueError, TypeError):
            target = None
        if target and linked is not None:
            keep = {Path(target).parent, *(path.parent for path in [*running, *linked])}
            for folder in ('application', 'application.noindex'):
                for path in applications(engine / 'launcher' / folder, engine, keep):
                    drop(path, engine, 'launcher-copy')
                result['released_bytes'] += finish_retired(engine / 'launcher' / folder, engine, DIGEST)
        # Environments and downloads belong to installation: never race it.
        try:
            with runtime_lock(engine / 'setup.lock', inherit=False), \
                    runtime_lock(engine / 'launcher' / 'host-setup.lock', inherit=False):
                pythons, current = [], None
                try:
                    machine = json.loads((engine / 'machine.json').read_text(encoding='utf-8'))
                    pythons.append(Path(machine['python']))
                except (OSError, ValueError, KeyError, TypeError):
                    machine = None
                try:
                    host = json.loads((engine / 'launcher' / 'comfy-host.json').read_text(encoding='utf-8'))
                    current = Path(host['environment'])
                    pythons.append(Path(host['python']))
                except (OSError, ValueError, KeyError, TypeError):
                    pass
                if current is not None and current.is_dir() and local_path(engine, current):
                    for path in environments(engine, current):
                        drop(path, engine, 'comfyui-environment')
                    result['released_bytes'] += finish_retired(engine / 'envs', engine, ENVIRONMENT)
                if machine and machine.get('ready') is True and all(python.is_file() for python in pythons):
                    for path in torch_wheels(engine, pythons):
                        try:
                            size = path.stat().st_size
                            path.unlink()
                            result['released_bytes'] += size
                            result['removed'].append(dict(kind='torch-wheel', path=str(path), bytes=size))
                        except OSError:
                            result['kept'].append(dict(kind='torch-wheel', path=str(path), reason='in use'))
        except OSError as error:  # Includes BlockingIOError: an installation holds the lease.
            result['kept'].append(dict(kind='installation', reason=type(error).__name__))
        if receipt and result['removed']:
            save(engine / 'logs' / ('cleanup-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '.json'), result)
    return result
