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

from .desktop_shortcut import digest
from .monitoring import save


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
    def valid(path):
        if not path.is_dir() or path.is_symlink():
            return False
        try:
            result = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(path)],
                                    capture_output=True, timeout=30)
            return result.returncode == 0 and digest(path / 'Contents/MacOS' / executable.name) == digest(executable)
        except (OSError, subprocess.SubprocessError):
            return False
    if valid(target):
        return target
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='launcher-', dir=parent))
    try:
        prepared = stage / 'FreeVideo.app'
        # Preserve framework links, permissions and signing resources.
        subprocess.run(['/usr/bin/ditto', str(app), str(prepared)], check=True, capture_output=True, timeout=120)
        if not valid(prepared):
            raise OSError('The copied FreeVideo application failed verification')
        if target.parent.exists():
            target = parent / (identity + '-' + uuid.uuid4().hex[:8]) / 'FreeVideo.app'
        stage.rename(target.parent)
        return target
    except subprocess.SubprocessError as error:
        raise OSError('Could not prepare the FreeVideo desktop shortcut') from error
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def create(engine):
    receipt = Path(engine) / 'launcher/desktop-shortcut.json'
    target = retained_application(engine)
    desktop = desktop_directory().resolve()
    path = desktop / 'FreeVideo.app'
    try:
        previous = json.loads(receipt.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    old = Path(previous.get('path', str(path)))
    owned = (old.parent == desktop and old.is_symlink()
             and os.readlink(old) == previous.get('target'))
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
