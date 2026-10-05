"""Build the shared launcher as an arm64 Mac app using Python 3.12.

Build outputs are private until native installation and generation pass. Local
builds are ad-hoc signed; this does not establish Developer ID notarization.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from freevideo_engine.desktop_runtime import check_launcher_dependencies, package_data_files, source_files
from freevideo_engine.launcher_update import MAC_CHANNEL, build_identity
from scripts.build_windows import build_info, stamp_source
from freevideo_engine.release_notes import markdown
from scripts.build_launcher_notices import collect as collect_notices


OPEN_GUIDE_NAME = 'Open FreeVideo.txt'
OPEN_GUIDE = '''FreeVideo for Mac — 首次打开 / First open

安装
1. 将 FreeVideo.app 拖入 Applications（应用程序）。
2. 从“应用程序”打开 FreeVideo，按安装器引导配置运行环境和模型。

如果 macOS 提示无法验证开发者或无法检查恶意软件
这是因为预览版暂未进行 Apple 公证。放行前请先确认安装包来自官方：
1. 只从 https://github.com/FlashML-org/FreeVideo/releases/tag/macos-preview 下载。
2. 在“终端”运行 shasum -a 256 ~/Downloads/FreeVideo-Mac-arm64.dmg，结果应与该页面 SHA256SUMS.txt 中的值一致。
然后：
3. 先尝试打开 FreeVideo，再关闭系统提示。
4. 打开“系统设置” → “隐私与安全”，向下找到 FreeVideo，点击“仍要打开”。
5. 在确认窗口点击“打开”，按系统要求验证身份。
这只放行 FreeVideo，不会关闭 Gatekeeper 或改动其他安全设置。系统会记住此次允许；更新应用后可能需要重新确认。
如果没有“仍要打开”，请向 FreeVideo 反馈完整提示；设备管理策略可能限制打开。
如果提示应用已损坏或包含恶意软件，请停止打开并向 FreeVideo 反馈。

Install
1. Drag FreeVideo.app into Applications.
2. Open FreeVideo from Applications and follow the installer to set up the runtime and models.

If macOS cannot verify the developer or check the app for malicious software
This happens because the preview isn't notarized by Apple yet. Before approving it, make sure the file is the official one:
1. Download it only from https://github.com/FlashML-org/FreeVideo/releases/tag/macos-preview.
2. In Terminal, run shasum -a 256 ~/Downloads/FreeVideo-Mac-arm64.dmg. The result should match the value in SHA256SUMS.txt on that page.
Then:
3. Try opening FreeVideo, then dismiss the system alert.
4. Open System Settings → Privacy & Security, scroll to FreeVideo and click Open Anyway.
5. Click Open in the confirmation and authenticate if asked.
This approves FreeVideo only. It doesn't turn off Gatekeeper or change other security settings. macOS remembers this approval; an app update may ask again.
If Open Anyway is unavailable, report the full alert to FreeVideo; device management may restrict opening.
If the alert says the app is damaged or contains malware, stop and report it to FreeVideo.

Apple 官方说明 / Apple instructions:
https://support.apple.com/102445
'''


def write_open_guide(directory):
    path = directory / OPEN_GUIDE_NAME
    payload = OPEN_GUIDE.encode('utf-8')
    path.write_bytes(payload)
    return dict(name=path.name, bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())


def identity(root):
    value = build_info(root)
    rows = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source_files(root)}
    # Include uncommitted development changes; do not label them as HEAD.
    value.update(revision=hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
                 channel=MAC_CHANNEL, target='macos-arm64', packaging='app')
    return build_identity(value)


def render_icon(source, pixels):
    """Give the Mac icon a transparent margin without changing shared artwork."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    # The shared rounded tile fills its canvas. Inset it to 824/1024 of the
    # Mac icon so Finder and the Dock don't display an oversized tile.
    extent = round(pixels * 824 / 1024)
    artwork = source.scaled(extent, extent, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    canvas = QImage(pixels, pixels, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    try:
        painter.drawImage((pixels - artwork.width()) // 2,
                          (pixels - artwork.height()) // 2, artwork)
    finally:
        painter.end()
    return canvas


def make_icon(destination):
    from PySide6.QtGui import QImage
    source = QImage(str(ROOT / 'freevideo_engine/assets/icon.png'))
    if source.isNull():
        raise ValueError('Launcher icon is missing')
    folder = destination / 'FreeVideo.iconset'
    folder.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = '@2x' if scale == 2 else ''
            output = folder / ('icon_%dx%d%s.png' % (size, size, suffix))
            if not render_icon(source, size * scale).save(str(output)):
                raise OSError('Could not write Mac icon')
    result = destination / 'FreeVideo.icns'
    subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(folder), '-o', str(result)], check=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64' or sys.version_info[:2] != (3, 12):
        parser.error('Build on native Apple Silicon with Python 3.12')
    check_launcher_dependencies()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    build = identity(ROOT)
    staged = out / 'engine-source'
    for path in source_files(ROOT):
        if path.is_relative_to(ROOT / 'freevideo_engine/launcher/licenses/bundled'):
            continue
        target = staged / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    stamp = stamp_source(staged, build)
    notices = staged / 'freevideo_engine/launcher/licenses/bundled'
    collect_notices(ROOT, notices)
    build_file = out / 'launcher-build.json'
    build_file.write_text(json.dumps(build, indent=2) + '\n', encoding='utf-8')
    icon = make_icon(out)
    data = [(str(staged), 'engine-source'), (str(build_file), '.'),
            (str(stamp), 'freevideo_engine'),
            (str(stamp.parent / 'build-identity.json'), 'freevideo_engine'),
            (str(notices), 'freevideo_engine/launcher/licenses/bundled')]
    data += [(str(path), str(path.parent.relative_to(ROOT))) for path in package_data_files(ROOT)
             if path.name not in ('build-version.txt', 'build-identity.json')
             and not path.is_relative_to(ROOT / 'freevideo_engine/launcher/licenses/bundled')]
    spec = out / 'FreeVideo.spec'
    # Shared QML, controller, diagnostics and network services. Never bundle
    # Torch/model weights or pick up packages from a generation environment.
    short_version = build['product_version']
    plist = dict(CFBundleDisplayName='FreeVideo', CFBundleShortVersionString=short_version,
                 CFBundleVersion='.'.join(build['version'].split('.')[:3]), FreeVideoVersion=build['version'],
                 CFBundleDevelopmentRegion='en', CFBundleLocalizations=['en', 'zh-Hans'],
                 CFBundleAllowMixedLocalizations=True,
                 LSMinimumSystemVersion='14.0', NSHighResolutionCapable=True,
                 NSPrincipalClass='NSApplication')
    text = f'''# Generated by scripts/build_macos.py
a = Analysis([{str(ROOT / 'scripts/macos_app.py')!r}], pathex=[{str(ROOT)!r}],
    binaries=[], datas={data!r},
    hiddenimports=['psutil', 'PySide6.QtQml', 'PySide6.QtQuick',
                   'PySide6.QtQuickControls2', 'PySide6.QtWidgets', 'freevideo_engine.managed'],
    excludes=['torch', 'triton', 'numpy', 'transformers', 'tkinter'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='FreeVideo',
          debug=False, strip=False, upx=False, console=False, target_arch='arm64')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='FreeVideo')
app = BUNDLE(coll, name='FreeVideo.app', icon={str(icon)!r},
             bundle_identifier='org.flashml.FreeVideo', info_plist={plist!r})
'''
    spec.write_text(text, encoding='utf-8')
    # Historical app bundles must not fill the user's application search with
    # duplicate FreeVideo entries. The exported disk image stays in dist.
    application_dist = out / 'applications.noindex'
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
                    '--distpath', str(application_dist), '--workpath', str(out / 'build'), str(spec)],
                   cwd=ROOT, check=True)
    app = application_dist / 'FreeVideo.app'
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    (out / 'dist').mkdir(exist_ok=True)
    (out / 'dist/RELEASE_NOTES.md').write_text(markdown(build), encoding='utf-8')
    guide = write_open_guide(out / 'dist')
    # The disk image is the only download. Keep the guide beside the app: it
    # must be readable before Gatekeeper allows the app to run.
    image_root = out / 'dmg-root.noindex'
    image_root.mkdir()
    subprocess.run(['/usr/bin/ditto', str(app), str(image_root / app.name)], check=True)
    (image_root / 'Applications').symlink_to('/Applications', target_is_directory=True)
    shutil.copyfile(out / 'dist' / guide['name'], image_root / guide['name'])
    dmg = out / 'dist/FreeVideo-Mac-arm64.dmg'
    subprocess.run(['/usr/bin/hdiutil', 'create', '-volname', 'FreeVideo', '-srcfolder',
                    str(image_root), '-format', 'UDZO', str(dmg)], check=True)
    sha = hashlib.sha256(dmg.read_bytes()).hexdigest()
    (out / 'dist/SHA256SUMS.txt').write_text(sha + '  ' + dmg.name + '\n', encoding='utf-8')
    result = dict(app=str(app), dmg=str(dmg), dmg_bytes=dmg.stat().st_size, dmg_sha256=sha,
                  build=build, signing='ad-hoc', notarized=False, first_open_guide=guide,
                  native_installation_validation='pending', native_generation_validation='pending')
    (out / 'build-result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
