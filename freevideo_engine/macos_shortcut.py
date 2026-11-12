"""A Finder shortcut to a retained native app; no scripts or global installation."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import uuid

from .desktop_shortcut import digest, read_build
from .monitoring import save

# Written when the retained copy cannot be verified; the diagnostic report includes it.
FAILURE = 'launcher/desktop-shortcut-failure.json'


def desktop_directory():
    from PySide6.QtCore import QStandardPaths
    value = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
    if not value:
        raise OSError('macOS could not locate your desktop folder')
    return Path(value)


def application():
    executable = Path(sys.executable).resolve()
    if not getattr(sys, 'frozen', False) or len(executable.parents) < 3:
        raise OSError('Open FreeVideo.app to create its desktop shortcut')
    app = executable.parents[2]
    try:
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    except (OSError, ValueError) as error:
        raise OSError('The FreeVideo application bundle is incomplete') from error
    if (app.suffix != '.app' or info.get('CFBundleIdentifier') != 'org.flashml.FreeVideo'
            or (app / 'Contents/MacOS' / info.get('CFBundleExecutable', '')).resolve() != executable):
        raise OSError('The running executable does not belong to FreeVideo.app')
    return app, executable


def _mount_type(path):
    """File system type of the volume holding `path`, e.g. 'apfs' or 'exfat'; '' when unknown."""
    try:
        out = getattr(subprocess.run(['/sbin/mount'], capture_output=True, timeout=10), 'stdout', b'') or b''
        text = out.decode('utf-8', 'replace') if isinstance(out, bytes) else str(out)
    except (OSError, subprocess.SubprocessError):
        return ''
    best = ('', '')
    for line in text.splitlines():
        before, _, rest = line.partition(' on ')
        # The options sit at the end of the line; a volume name may itself contain ' ('.
        point, _, tail = rest.rpartition(' (')
        if point and (path == point or path.startswith(point.rstrip('/') + '/')) and len(point) >= len(best[0]):
            best = (point, tail.split(',')[0].strip())
    return best[1]


def _codesign(path):
    """(returncode, stderr text) of a strict deep verification, or (None, reason) when it did not run."""
    try:
        result = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(path)],
                                capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return None, 'codesign timed out'
    except (OSError, subprocess.SubprocessError) as error:
        return None, 'codesign could not run: ' + str(error)[:200]
    stderr = getattr(result, 'stderr', b'') or b''
    if isinstance(stderr, bytes):
        stderr = stderr.decode('utf-8', 'replace')
    return result.returncode, str(stderr).strip()[:1000]


def check_application(path, executable):
    """None when the bundle at `path` is a verified copy of `executable`, else what failed."""
    if not path.is_dir() or path.is_symlink():
        return dict(reason='not a bundle folder')
    code, message = _codesign(path)
    if code is None:
        return dict(reason=message)
    if code != 0:
        return dict(reason='codesign verification failed', codesign_exit=code, codesign_message=message)
    try:
        if digest(path / 'Contents/MacOS' / executable.name) != digest(executable):
            return dict(reason='executable differs from the running one')
    except OSError as error:
        return dict(reason='executable could not be read: ' + str(error)[:200])
    return None


def _failure_record(engine, source, copy, executable, problem):
    """Where verification failed and what the copy sits on, saved for the diagnostic report."""
    record = dict(problem, source_app=str(source), copy_parent=str(copy.parent),
                  copy_volume=_mount_type(str(copy.parent)))
    try:
        record['detritus'] = sorted(str(p.relative_to(copy)) for p in copy.rglob('._*'))[:10]
    except OSError:
        pass
    # A running app that does not verify itself points at the app, not at copying it.
    code, message = _codesign(source)
    record['source_codesign_exit'] = code
    record['source_codesign_message'] = message
    try:
        save(Path(engine) / FAILURE, record)
    except OSError:
        pass


def retained_application(engine):
    app, executable = application()
    parent = Path(engine).resolve() / 'launcher/application.noindex'
    # Already running from a retained app; do not copy it again.
    if app.is_relative_to(parent):
        return app
    # The executable contains the frozen Python code. Include the bundle metadata
    # to distinguish packaging changes with the same executable.
    import hashlib
    identity = hashlib.sha256((digest(executable) + digest(app / 'Contents/Info.plist')).encode()).hexdigest()
    target = parent / identity / 'FreeVideo.app'
    def clear():
        try:
            (Path(engine) / FAILURE).unlink()
        except OSError:
            pass
    if check_application(target, executable) is None:
        clear()
        return target
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='launcher-', dir=parent))
    try:
        prepared = stage / 'FreeVideo.app'
        # Preserve framework links, permissions and signing resources.
        subprocess.run(['/usr/bin/ditto', str(app), str(prepared)], check=True, capture_output=True, timeout=120)
        problem = check_application(prepared, executable)
        if problem:
            _failure_record(engine, app, prepared, executable, problem)
            raise OSError('The copied FreeVideo application failed verification')
        if target.parent.exists():
            target = parent / (identity + '-' + uuid.uuid4().hex[:8]) / 'FreeVideo.app'
        stage.rename(target.parent)
        clear()
        return target
    except subprocess.SubprocessError as error:
        raise OSError('Could not prepare the FreeVideo desktop shortcut') from error
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def create(engine):
    receipt = Path(engine) / 'launcher/desktop-shortcut.json'
    desktop = desktop_directory().resolve()
    path = desktop / 'FreeVideo.app'
    try:
        previous = json.loads(receipt.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    old = Path(previous.get('path', str(path)))
    owned = (old.parent == desktop and old.is_symlink()
             and os.readlink(old) == previous.get('target'))
    if owned:
        from .launcher_update import current_build
        from .release_notes import older
        # build_macos.py adds the stamp as a root data file. PyInstaller puts it
        # in Resources, cross-linked from Frameworks (the bootloader's _MEIPASS).
        identity = read_build(old / 'Contents/Resources/launcher-build.json')
        if not identity:
            identity = read_build(old / 'Contents/Frameworks/launcher-build.json')
        if older(current_build() or {}, identity):
            return dict(previous, status='present')
    target = retained_application(engine)
    if owned:
        path = old
        if previous['target'] == str(target):
            return dict(previous, status='present')
    else:
        index = 2
        while os.path.lexists(path):
            path = desktop / ('FreeVideo (%d).app' % index)
            index += 1
    if owned:
        stage = desktop / ('FreeVideo-' + uuid.uuid4().hex + '.app')
        try:
            stage.symlink_to(target, target_is_directory=True)
            if not path.is_symlink() or os.readlink(path) != previous.get('target'):
                raise OSError('The desktop shortcut changed while updating; retry to keep the new item')
            os.replace(stage, path)
        finally:
            if stage.is_symlink():
                stage.unlink()
    else:
        # An unrelated file appearing during creation must never be overwritten.
        path.symlink_to(target, target_is_directory=True)
    result = dict(status='created', kind='macos-app-symlink', path=str(path), target=str(target))
    save(receipt, result)
    return result
