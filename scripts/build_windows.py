"""Build on Windows with Python 3.12 after installing constraints/windows-launcher.txt."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from freevideo_engine.desktop_runtime import check_launcher_dependencies, package_data_files, source_files
from freevideo_engine.launcher_update import REPOSITORY, CHANNEL, TRACKS
from freevideo_engine.release_notes import catalog, markdown
from scripts.build_launcher_notices import collect as collect_notices


def release_track():
    """Nightly builds are made with FREEVIDEO_RELEASE_TRACK=nightly; stable identities stay unchanged."""
    track = os.environ.get('FREEVIDEO_RELEASE_TRACK') or 'stable'
    if track not in TRACKS:
        raise SystemExit('FREEVIDEO_RELEASE_TRACK must be one of ' + ', '.join(TRACKS))
    return {} if track == 'stable' else {'track': track}


def build_info(root):
    try:
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True,
                                            stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        rows = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files(root)}
        revision = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    built_at = int(time.time())
    # Numeric UTC date + HHMMSS is also a valid Python package version.
    version = '.'.join(str(int(part)) for part in time.strftime('%Y.%m.%d.%H%M%S', time.gmtime(built_at)).split('.'))
    return dict(schema=1, repository=REPOSITORY, channel=CHANNEL, revision=revision,
                built_at=built_at, version=version, **release_track(), **catalog(root / 'freevideo_engine'))


def stamp_source(staged, identity):
    version = identity['version']
    stamp = staged / 'freevideo_engine/build-version.txt'
    stamp.write_text(version, encoding='utf-8')
    (stamp.parent / 'build-identity.json').write_text(json.dumps(identity), encoding='utf-8')
    project = staged / 'pyproject.toml'
    project.write_text(re.sub(r'^version = "[^"]+"', 'version = "' + version + '"',
                              project.read_text(encoding='utf-8'), count=1, flags=re.M), encoding='utf-8')
    return stamp


def bundle_data_args(root):
    # engine-source backs external Python; frozen modules have their own __file__
    # under _MEIPASS/freevideo_engine and need the same resources beside them.
    arguments = []
    for path in package_data_files(root):
        if path.is_relative_to(root / 'freevideo_engine/launcher/licenses/bundled'):
            continue  # A rebuild collects notices from its current environment.
        arguments.extend(['--add-data', str(path) + os.pathsep + path.parent.relative_to(root).as_posix()])
    return arguments


def version_file(out, identity):
    """Write standard PE metadata so Windows can identify the publisher/product."""
    parts = tuple(int(part) for part in identity['version'].split('.'))
    # VERSIONINFO stores four unsigned 16-bit words. The human-readable
    # timestamp remains complete in StringFileInfo; the resource build word
    # is deliberately folded into the valid 16-bit range.
    version = parts[:3] + (identity['built_at'] & 0xffff,)
    text = '''# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers=%r, prodvers=%r, mask=0x3f, flags=0x0,
                   OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([
    StringTable('040904B0', [
      StringStruct('CompanyName', 'FlashML'),
      StringStruct('FileDescription', 'FreeVideo launcher'),
      StringStruct('FileVersion', '%s'),
      StringStruct('InternalName', 'FreeVideo'),
      StringStruct('OriginalFilename', 'FreeVideo.exe'),
      StringStruct('ProductName', 'FreeVideo'),
      StringStruct('ProductVersion', '%s'),
      StringStruct('LegalCopyright', 'Copyright (c) FlashML'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
''' % (version, version, identity['version'], identity.get('product_version', identity['version']))
    path = out / 'version-info.txt'
    path.write_text(text, encoding='utf-8')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--onefolder', action='store_true', help='Build a folder ZIP that runs without temporary self-extraction')
    args = parser.parse_args()
    if os.name != 'nt' or sys.version_info[:2] != (3, 12):
        parser.error('Build this artifact on Windows x64 with Python 3.12')
    try:
        check_launcher_dependencies()
        from PySide6 import QtQml, QtQuick, QtQuickControls2, QtWidgets
    except ImportError as error:
        parser.error('Launcher build dependencies are missing or broken: %s. Run this Python with '
                     '-m pip install -r constraints/windows-launcher.txt before building.' % error)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    identity = build_info(ROOT)
    if args.onefolder:
        identity['packaging'] = 'onedir'
    build_file = out / 'launcher-build.json'
    build_file.write_text(json.dumps(identity, indent=2), encoding='utf-8')
    version_info = version_file(out, identity)
    staged = out / 'engine-source'
    for path in source_files(ROOT):
        if path.is_relative_to(ROOT / 'freevideo_engine/launcher/licenses/bundled'):
            continue
        target = staged / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    stamp = stamp_source(staged, identity)
    notices = staged / 'freevideo_engine/launcher/licenses/bundled'
    collect_notices(ROOT, notices)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--noupx',
        '--onedir' if args.onefolder else '--onefile', '--name', 'FreeVideo',
        '--icon', str(ROOT / 'freevideo_engine/assets/icon.ico'),
        '--version-file', str(version_info),
        '--distpath', str(out / 'dist'), '--workpath', str(out / 'build'), '--specpath', str(out),
        '--paths', str(ROOT), '--add-data', str(staged) + os.pathsep + 'engine-source',
        '--add-data', str(build_file) + os.pathsep + '.',
        '--add-data', str(stamp) + os.pathsep + 'freevideo_engine',
        '--add-data', str(stamp.parent / 'build-identity.json') + os.pathsep + 'freevideo_engine',
        '--add-data', str(notices) + os.pathsep + 'freevideo_engine/launcher/licenses/bundled',
        *bundle_data_args(ROOT),
        '--hidden-import', 'psutil',
        '--exclude-module', 'torch', '--exclude-module', 'triton', '--exclude-module', 'numpy',
        '--exclude-module', 'transformers', '--exclude-module', 'tkinter',
        str(ROOT / 'scripts/windows_app.py')]
    subprocess.run(command, cwd=ROOT, check=True)
    executable = out / 'dist' / ('FreeVideo/FreeVideo.exe' if args.onefolder else 'FreeVideo.exe')
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    (executable.parent / 'SHA256SUMS.txt').write_text(digest + '  FreeVideo.exe\n', encoding='utf-8')
    (executable.parent / 'launcher-build.json').write_text(json.dumps(identity, indent=2), encoding='utf-8')
    (executable.parent / 'RELEASE_NOTES.md').write_text(markdown(identity), encoding='utf-8')
    shutil.copytree(notices, executable.parent / 'licenses')
    if args.onefolder:
        shutil.make_archive(str(out / 'dist/FreeVideo-Windows-folder'), 'zip',
                            root_dir=out / 'dist', base_dir='FreeVideo')
    print(json.dumps({'exe': str(executable), 'bytes': executable.stat().st_size, 'sha256': digest}))


if __name__ == '__main__':
    main()
