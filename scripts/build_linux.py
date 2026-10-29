"""Build the shared launcher as a Linux x86_64 AppImage with Python 3.12.

The release workflow runs this in a manylinux_2_34 container: PySide6 6.11 needs
glibc 2.34, so the AppImage starts on Ubuntu 22.04, Debian 12, Fedora 36, RHEL 9
and newer (older systems use ./setup.sh). The Python must have a shared libpython (a
python-build-standalone build, as `uv python install 3.12` provides) with
constraints/linux-launcher.txt installed. appimagetool and the AppImage runtime
are pinned and verified before they run.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from freevideo_engine.desktop_runtime import check_launcher_dependencies, source_files
from freevideo_engine.launcher_update import LINUX_CHANNEL, RELEASE_ASSETS, build_identity
from freevideo_engine.release_notes import markdown
from scripts.build_launcher_notices import collect as collect_notices
from scripts.build_windows import build_info, bundle_data_args, stamp_source
from scripts.prune_qt_bundle import prune_qt_bundle, write_prune_report
from scripts.deduplicate_bundle_data import deduplicate_bundle_data, write_data_report

APP_ID = 'org.flashml.FreeVideo'
NAME = RELEASE_ASSETS[LINUX_CHANNEL][0]
TOOLS = {
    # stunnel/static-curl 8.22.0, x86_64 musl: fully static, HTTPS + HTTP/SOCKS
    # proxies, with embedded Mozilla roots. The dev archive supplies the original
    # licenses for curl AND its linked components; headers/libraries are not shipped.
    'curl': dict(version='8.22.0',
                 url='https://github.com/stunnel/static-curl/releases/download/8.22.0/curl-linux-x86_64-dev-8.22.0.tar.xz',
                 sha256='93a928c9bab36604ff646e7de31c9d4b789145acf8c7cda75e7da2e1221c5cc7',
                 binary_sha256='b7443db66d622a27ef2152bb2032d6ada9fb29a8917f22b9521a993f8e61cd7f',
                 ca_sha256='f66dff1bdf8f96060b8177976f8b7d9254bc89bc4db933d769f7384d28480bc9'),
    'appimagetool': dict(url='https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage',
                         sha256='ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0'),
    'runtime': dict(url='https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64',
                    sha256='2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d'),
    # Noto Sans CJK SC 2.004 (SIL Open Font License 1.1), cut down to the launcher's own characters.
    'font-regular': dict(url='https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf',
                         sha256='2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b'),
    'font-bold': dict(url='https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf',
                      sha256='b5f0d1a190a7f9b43c310a8850630af12553df32c4c050543f9059732d9b4c0a'),
    'font-license': dict(url='https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/LICENSE',
                         sha256='6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2'),
}
FONT_FAMILY = 'FreeVideo Sans SC'
# Characters the launcher can show that desktop Latin fonts may lack: Chinese text
# (pages, messages, release notes) and the arrows and check marks next to it
# (✓ for a finished step, ↗ for a link); Noto Sans, for one, has neither.
CJK = re.compile(r'[\u2190-\u21ff\u2700-\u27bf\u2e80-\u9fff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef\u3000-\u303f]')
# Qt's X11 platform plugin needs these, and common desktop installations lack
# some of them (libxcb-cursor0 above all). Everything else, glibc, the GPU
# driver's libGL/libEGL, X11, Wayland, fontconfig and freetype, comes from the
# system the AppImage runs on.
SYSTEM_LIBRARIES = ('libxcb-cursor.so.0', 'libxcb-icccm.so.4', 'libxcb-image.so.0', 'libxcb-keysyms.so.1',
                    'libxcb-render-util.so.0', 'libxcb-util.so.1', 'libxkbcommon-x11.so.0', 'libxkbcommon.so.0')
DESKTOP = '''[Desktop Entry]
Type=Application
Name=FreeVideo
Comment=Install and open FreeVideo in ComfyUI
Comment[zh_CN]=安装并在 ComfyUI 中打开 FreeVideo
Exec=FreeVideo
Icon={app}
Terminal=false
Categories=AudioVideo;Video;Graphics;
StartupWMClass=FreeVideo
'''.format(app=APP_ID)
APP_RUN = '''#!/bin/sh
# The AppImage runtime sets APPDIR; resolve it here for extracted runs too.
HERE="$(dirname "$(readlink -f "$0")")"
# A launcher that cannot start leaves its message on a terminal nobody sees: show it in a dialog too.
fv_stop() {
    printf '%s\\n%s\\n' "$1" "$2" >&2
    # xmessage draws no CJK and does not wrap, so it gets the English text wrapped.
    # Exit 127 means the tool itself could not load (its own libraries may be the missing ones): try the next.
    if command -v zenity >/dev/null 2>&1; then zenity --error --title=FreeVideo --text="$1\\n$2" 2>/dev/null; [ $? -ne 127 ] && exit 1; fi
    if command -v kdialog >/dev/null 2>&1; then kdialog --title FreeVideo --error "$1
$2" 2>/dev/null; [ $? -ne 127 ] && exit 1; fi
    if command -v notify-send >/dev/null 2>&1; then notify-send FreeVideo "$1 $2" 2>/dev/null; [ $? -ne 127 ] && exit 1; fi
    if command -v xmessage >/dev/null 2>&1; then xmessage -center "$(printf '%s' "$2" | fold -s -w 72)" 2>/dev/null; [ $? -ne 127 ] && exit 1; fi
    exit 1
}
# The launcher needs glibc 2.34; on an older system it fails with a message on a terminal nobody sees.
glibc="$(getconf GNU_LIBC_VERSION 2>/dev/null | sed 's/^[^0-9]*//')"
major="${glibc%%.*}"
minor="${glibc#*.}"
minor="${minor%%.*}"
case "$major$minor" in
    ''|*[!0-9]*) ;;
    *)
        if [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -lt 34 ]; }; then
            fv_stop "此系统版本过旧，无法运行 FreeVideo 桌面版。请使用 2022 年及以后发布的 Linux 发行版，或按 Linux 安装说明在终端中运行 setup.sh 安装。" \\
                "This system is too old to run the FreeVideo desktop app. Use a Linux distribution released in 2022 or later, or follow the Linux installation guide to install with setup.sh in a terminal."
        fi
        ;;
esac
# The window needs the system's graphics libraries (OpenGL, EGL, ...); a minimal system lacks them and the launcher
# would end with a Python traceback instead. Headless runs (offscreen, minimal) never load the xcb plugin.
if command -v ldd >/dev/null 2>&1 && [ "${QT_QPA_PLATFORM:-}" != offscreen ] && [ "${QT_QPA_PLATFORM:-}" != minimal ] \\
        && [ "${1:-}" != --smoke-test ]; then
    libs="$HERE/usr/lib/FreeVideo/_internal"
    missing="$(LD_LIBRARY_PATH="$libs:$libs/PySide6/Qt/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ldd "$libs/PySide6/Qt/plugins/platforms/libqxcb.so" 2>/dev/null \\
        | sed -n 's/^[[:space:]]*\\(lib[^ ]*\\) => not found.*/\\1/p' | sort -u | tr '\\n' ' ' | sed 's/ $//; s/ /, /g')"
    if [ -n "$missing" ]; then
        fv_stop "缺少系统图形库：$missing。请联系系统管理员安装提供这些库的软件包，完成后重新打开 FreeVideo。" \\
            "Missing system graphics libraries: $missing. Ask your administrator to install the packages that provide them, then open FreeVideo again."
    fi
fi
exec "$HERE/usr/lib/FreeVideo/FreeVideo" "$@"
'''


def identity(root):
    return build_identity(dict(build_info(root), channel=LINUX_CHANNEL, target='linux-x86_64', packaging='appimage'))


def tool(cache, name):
    """A pinned tool, downloaded once and checked on every use."""
    spec = TOOLS[name]
    path = cache / Path(spec['url']).name
    if not path.is_file():
        cache.mkdir(parents=True, exist_ok=True)
        stage = path.with_suffix('.partial')
        with urllib.request.urlopen(spec['url'], timeout=60) as reply, stage.open('wb') as stream:
            shutil.copyfileobj(reply, stream)
        stage.replace(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != spec['sha256']:
        raise SystemExit('Pinned %s does not match its checksum: %s' % (name, path))
    if name in ('appimagetool', 'runtime'):
        path.chmod(0o755)
    return path


def bundle_curl(cache, destination, notices):
    """Stage only the verified static executable and its original license texts."""
    spec = TOOLS['curl']
    destination.mkdir(parents=True, exist_ok=True)
    licenses = notices / 'curl'
    licenses.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tool(cache, 'curl'), 'r:xz') as archive:
        # Read exact members, never extract archive paths or follow archive links.
        member = archive.getmember('curl-x86_64/bin/curl')
        if not member.isfile():
            raise ValueError('Bundled curl must be a regular file')
        data = archive.extractfile(member).read()
        if hashlib.sha256(data).hexdigest() != spec['binary_sha256']:
            raise ValueError('Bundled curl binary does not match its checksum')
        executable = destination / 'curl'
        executable.write_bytes(data)
        executable.chmod(0o755)
        copied = []
        for member in archive.getmembers():
            path = Path(member.name)
            if path.parent == Path('curl-x86_64/licenses') and member.isfile():
                (licenses / (path.name + '.txt')).write_bytes(archive.extractfile(member).read())
                copied.append(path.name)
        if not {'curl', 'ca-bundle', 'openssl'} <= set(copied):
            raise ValueError('Bundled curl licenses are missing')
    (licenses / 'SOURCE.txt').write_text(
        'Static curl %s (Linux x86_64 musl)\n%s\nArchive SHA256: %s\nBinary SHA256: %s\n'
        'Build sources: https://github.com/stunnel/static-curl/tree/%s\n' %
        (spec['version'], spec['url'], spec['sha256'], spec['binary_sha256'], spec['version']), encoding='utf-8')
    return executable


def launcher_characters(root):
    text = ''
    for path in [*(root / 'freevideo_engine').rglob('*.py'), *(root / 'freevideo_engine').rglob('*.qml'),
                 *(root / 'freevideo_engine').glob('*.json')]:
        text += path.read_text(encoding='utf-8', errors='ignore')
    # Latin text uses the system's font; the subset covers Chinese and the symbols.
    return ''.join(sorted(set(CJK.findall(text))))


def make_fonts(root, tools, destination):
    """Desktops without Chinese fonts would show the launcher's Chinese as boxes."""
    from fontTools import subset
    from fontTools.ttLib import TTFont
    characters = launcher_characters(root)
    destination.mkdir(parents=True)
    notices = set()
    for weight in ('Regular', 'Bold'):
        options = subset.Options()
        options.layout_features = ['*']
        # OFL 2: every copy keeps the copyright notice (0) and the licence (13, 14).
        options.name_IDs = [0, 13, 14]
        font = TTFont(tool(tools, 'font-' + weight.lower()))
        notices.add(font['name'].getDebugName(0))
        subsetter = subset.Subsetter(options)
        subsetter.populate(text=characters)
        subsetter.subset(font)
        # A renamed subset never stands in for a complete Noto Sans CJK the system has.
        for record, value in ((1, FONT_FAMILY), (2, weight), (4, FONT_FAMILY + ' ' + weight),
                              (6, FONT_FAMILY.replace(' ', '') + '-' + weight), (16, FONT_FAMILY), (17, weight)):
            font['name'].setName(value, record, 3, 1, 0x409)
        font.save(destination / ('FreeVideoSansSC-%s.otf' % weight))
    # noto-cjk's LICENSE is the licence text alone; the copyright line comes from the font.
    if len(notices) != 1 or not all(notices):
        raise SystemExit('The bundled fonts do not carry one copyright notice: %r' % sorted(map(str, notices)))
    licence = tool(tools, 'font-license').read_text(encoding='utf-8')
    (destination / 'OFL.txt').write_text(notices.pop() + '\n\n' + licence, encoding='utf-8')
    return len(characters)


def system_library(name, extra=None):
    """The build system's copy of a shared library: --libraries first, then the linker cache."""
    if extra is not None and (extra / name).exists():
        return extra / name
    listing = subprocess.run(['/sbin/ldconfig', '-p'], capture_output=True, text=True).stdout
    for line in listing.splitlines():
        parts = line.strip().split(' => ')
        if len(parts) == 2 and parts[0].split(' ')[0] == name and 'x86-64' in parts[0]:
            return Path(parts[1])
    return None


def bundle_libraries(internal, extra=None):
    found = []
    for name in SYSTEM_LIBRARIES:
        if (internal / name).exists():
            continue
        path = system_library(name, extra)
        if path is None:
            raise SystemExit('Install %s on the build system; Qt needs it on desktops that lack it.' % name)
        shutil.copy2(path.resolve(), internal / name)
        found.append(name)
    return found


def appdir(out, dist, icon):
    folder = out / 'AppDir'
    shutil.copytree(dist, folder / 'usr/lib/FreeVideo', symlinks=True)
    (folder / 'AppRun').write_text(APP_RUN, encoding='utf-8')
    (folder / 'AppRun').chmod(0o755)
    (folder / (APP_ID + '.desktop')).write_text(DESKTOP, encoding='utf-8')
    shutil.copyfile(icon, folder / (APP_ID + '.png'))
    themed = folder / 'usr/share/icons/hicolor/512x512/apps'
    themed.mkdir(parents=True)
    shutil.copyfile(icon, themed / (APP_ID + '.png'))
    (folder / '.DirIcon').symlink_to(APP_ID + '.png')
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--tools', type=Path, help='Folder for the pinned appimagetool and runtime downloads')
    parser.add_argument('--libraries', type=Path, help='Folder searched first for the system libraries bundled for Qt')
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or sys.version_info[:2] != (3, 12):
        parser.error('Build this artifact on Linux x86_64 with Python 3.12')
    if not sysconfig.get_config_var('Py_ENABLE_SHARED'):
        parser.error('This Python has no shared libpython; use a python-build-standalone 3.12 (uv python install 3.12)')
    try:
        check_launcher_dependencies()
        from PySide6 import QtQml, QtQuick, QtQuickControls2, QtWidgets  # noqa: F401
    except ImportError as error:
        parser.error('Launcher build dependencies are missing or broken: %s. Run this Python with '
                     '-m pip install -r constraints/linux-launcher.txt before building.' % error)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    tools = (args.tools or out / 'tools').resolve()
    appimagetool, runtime = tool(tools, 'appimagetool'), tool(tools, 'runtime')
    build = identity(ROOT)
    build_file = out / 'launcher-build.json'
    build_file.write_text(json.dumps(build, indent=2), encoding='utf-8')
    staged = out / 'engine-source'
    for path in source_files(ROOT):
        if path.is_relative_to(ROOT / 'freevideo_engine/launcher/licenses/bundled'):
            continue
        target = staged / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    stamp = stamp_source(staged, build)
    notices = staged / 'freevideo_engine/launcher/licenses/bundled'
    collect_notices(ROOT, notices)
    curl = bundle_curl(tools, out / 'curl', notices)
    fonts = out / 'fonts'
    glyphs = make_fonts(ROOT, tools, fonts)
    shutil.copyfile(fonts / 'OFL.txt', notices / 'Noto-Sans-CJK-SC-OFL.txt')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--noupx', '--onedir',
        '--name', 'FreeVideo', '--distpath', str(out / 'dist'), '--workpath', str(out / 'build'), '--specpath', str(out),
        '--paths', str(ROOT), '--add-data', str(staged) + os.pathsep + 'engine-source',
        '--add-data', str(build_file) + os.pathsep + '.',
        '--add-data', str(stamp) + os.pathsep + 'freevideo_engine',
        '--add-data', str(stamp.parent / 'build-identity.json') + os.pathsep + 'freevideo_engine',
        '--add-data', str(notices) + os.pathsep + 'freevideo_engine/launcher/licenses/bundled',
        '--add-data', str(fonts) + os.pathsep + 'freevideo_engine/assets/fonts',
        *bundle_data_args(ROOT),
        '--hidden-import', 'psutil',
        # Run by name when the launcher supervises setup and ComfyUI (processes.module_command).
        '--hidden-import', 'freevideo_engine.linux_process_host',
        '--exclude-module', 'torch', '--exclude-module', 'triton', '--exclude-module', 'numpy',
        '--exclude-module', 'transformers', '--exclude-module', 'tkinter',
        str(ROOT / 'scripts/linux_app.py')]
    from scripts.source_build import run_pyinstaller
    # These files are added after PyInstaller; they belong to the same base key.
    extra_runtime = {'appimage-runtime': str(runtime), 'appimagetool': str(appimagetool)}
    extra_runtime['curl'] = str(curl)
    for name in SYSTEM_LIBRARIES:
        path = system_library(name, args.libraries.resolve() if args.libraries else None)
        if path is not None:
            extra_runtime[name] = str(path.resolve())
    run_pyinstaller(command, ROOT, staged, build_file, extra_runtime)
    dist = out / 'dist' / 'FreeVideo'
    write_prune_report(out, prune_qt_bundle(dist))
    bundled = bundle_libraries(dist / '_internal', args.libraries.resolve() if args.libraries else None)
    # Copy after PyInstaller: a static ELF must not be rewritten or analyzed as
    # a shared library. The base manifest above still binds its exact bytes.
    shutil.copytree(curl.parent, dist / '_internal/curl')
    folder = appdir(out, dist, ROOT / 'freevideo_engine/assets/icon.png')
    # copytree expands hardlinks: deduplicate the final AppDir, after copying.
    write_data_report(out, deduplicate_bundle_data(folder))
    release = out / 'release'
    release.mkdir()
    executable = release / NAME
    env = dict(os.environ, ARCH='x86_64', APPIMAGE_EXTRACT_AND_RUN='1')
    subprocess.run([str(appimagetool), '--no-appstream', '--runtime-file', str(runtime), '--comp', 'zstd',
                    '--mksquashfs-opt=-b', '--mksquashfs-opt=1M',
                    '--mksquashfs-opt=-Xcompression-level', '--mksquashfs-opt=19',
                    str(folder), str(executable)],
                   cwd=out, env=env, check=True)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    (release / 'SHA256SUMS.txt').write_text(digest + '  ' + NAME + '\n', encoding='utf-8')
    (release / 'launcher-build.json').write_text(json.dumps(build, indent=2), encoding='utf-8')
    (release / 'RELEASE_NOTES.md').write_text(markdown(build), encoding='utf-8')
    shutil.copytree(notices, release / 'licenses')
    print(json.dumps({'appimage': str(executable), 'bytes': executable.stat().st_size, 'sha256': digest,
                      'bundled_system_libraries': bundled, 'font_characters': glyphs}))


if __name__ == '__main__':
    main()
