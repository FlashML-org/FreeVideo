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
from scripts.prune_qt_bundle import prune_qt_bundle, write_prune_report
from scripts.deduplicate_bundle_data import deduplicate_bundle_data, write_data_report


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
    # Importing an offline runtime ZIP fingerprints its pinned dependencies,
    # including this file outside the package.
    arguments.extend(['--add-data', str(root / 'constraints/windows-portable.txt') + os.pathsep + 'constraints'])
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


def data_hook(datas):
    """Add files PyInstaller would have taken from --add-data, after Analysis.

    Analysis reuses its cache only when every collected file is older than the
    previous build. These files are written by every build, so they are merged
    here with PyInstaller's own formatting and normalization instead: first in
    the list, deduplicated together with binaries, as Analysis does.
    """
    return f'''
from PyInstaller.building.datastruct import normalize_toc as _normalize_toc
from PyInstaller.building.utils import format_binaries_and_datas as _format_datas
_user = sorted(_normalize_toc([(d, s, 'DATA') for d, s in _format_datas({datas!r}, workingdir=SPECPATH)]))
_combined = _normalize_toc(_user + a.datas + a.binaries)
a.datas = [e for e in _combined if e[2] not in ('BINARY', 'EXTENSION')]
a.binaries = [e for e in _combined if e[2] in ('BINARY', 'EXTENSION')]
'''


def build_pyinstaller(command, out, *, onefolder, workpath=None, datas=()):
    # Windows onefile has no tree to prune after EXE: use exactly the same
    # globs on Analysis TOCs before PyInstaller writes its compressed archive.
    # Generate the usual spec so all bootloader/version/manifest defaults stay
    # those of the pinned PyInstaller, then insert only our build-time filter.
    options, i = [], 3
    while i < len(command):
        option = command[i]
        if option in ('--distpath', '--workpath'):
            i += 2
            continue
        if option != '--noconfirm':
            options.append(option)
        i += 1
    subprocess.run([sys.executable, '-m', 'PyInstaller.utils.cliutils.makespec', *options], cwd=ROOT, check=True)
    spec = out / 'FreeVideo.spec'
    from scripts.source_build import spec_hook
    injection = spec_hook(ROOT, out / 'engine-source', out / 'launcher-build.json')
    # Data files join before pruning, as they did inside Analysis.
    user = data_hook(list(datas)) if datas else ''
    if not onefolder:
        # The onefile runtime hook goes in after Analysis, where PyInstaller puts a
        # --runtime-hook (first script; its imports are all already collected), so both
        # layouts run the same Analysis and the second build reuses the first's.
        pruning = f'''
a.scripts = [('pyi_rth_bundle_data', {str(ROOT / 'scripts/pyi_rth_bundle_data.py')!r}, 'PYSOURCE')] + a.scripts
import sys
sys.path.insert(0, {str(ROOT)!r})
from scripts.prune_qt_bundle import prune_qt_toc, write_prune_report
from scripts.deduplicate_bundle_data import deduplicate_toc, write_data_report
a.binaries, removed_binaries = prune_qt_toc(a.binaries)
a.datas, removed_data = prune_qt_toc(a.datas)
write_prune_report({str(out)!r}, dict(files=removed_binaries['files'] + removed_data['files'],
    bytes=removed_binaries['bytes'] + removed_data['bytes']))
'''
        injection = pruning + injection + f'''
a.datas, deduplicated = deduplicate_toc(a.datas, {str(out)!r})
write_data_report({str(out)!r}, deduplicated)
'''
    injection = user + injection
    text = spec.read_text(encoding='utf-8')
    if text.count('pyz = PYZ(') != 1:
        raise ValueError('Unexpected PyInstaller spec layout')
    spec.write_text(text.replace('pyz = PYZ(', injection + '\npyz = PYZ('), encoding='utf-8')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
                    '--distpath', str(out / 'dist'), '--workpath', str(workpath or out / 'build'), str(spec)],
                   cwd=ROOT, check=True)
    if onefolder:
        dist = out / 'dist/FreeVideo'
        write_prune_report(out, prune_qt_bundle(dist))
        write_data_report(out, deduplicate_bundle_data(dist))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--onefolder', action='store_true', help='Build a folder ZIP that runs without temporary self-extraction')
    parser.add_argument('--workpath', type=Path,
                        help='PyInstaller work directory to reuse between builds of the same checkout path')
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
    # Same files and order as --add-data, but merged after Analysis (see data_hook)
    # so that a new build stamp does not invalidate a reused --workpath.
    data = [str(staged) + os.pathsep + 'engine-source',
            str(build_file) + os.pathsep + '.',
            str(stamp) + os.pathsep + 'freevideo_engine',
            str(stamp.parent / 'build-identity.json') + os.pathsep + 'freevideo_engine',
            str(notices) + os.pathsep + 'freevideo_engine/launcher/licenses/bundled',
            *bundle_data_args(ROOT)[1::2]]
    datas = [tuple(item.split(os.pathsep, 1)) for item in data]
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--noupx',
        '--onedir' if args.onefolder else '--onefile', '--name', 'FreeVideo',
        '--icon', str(ROOT / 'freevideo_engine/assets/icon.ico'),
        '--version-file', str(version_info),
        '--distpath', str(out / 'dist'), '--workpath', str(out / 'build'), '--specpath', str(out),
        '--paths', str(ROOT),
        '--hidden-import', 'psutil',
        '--exclude-module', 'torch', '--exclude-module', 'triton', '--exclude-module', 'numpy',
        '--exclude-module', 'transformers', '--exclude-module', 'tkinter',
        # PyInstaller appends __main__ to the stored list itself; naming it here keeps the
        # stored and requested lists equal, so a reused --workpath can skip Analysis.
        '--exclude-module', '__main__',
        str(ROOT / 'scripts/windows_app.py')]
    workpath = args.workpath.resolve() if args.workpath else None
    build_pyinstaller(command, out, onefolder=args.onefolder, workpath=workpath, datas=datas)
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
