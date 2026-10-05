"""Verify a built Mac DMG payload, signatures and image fidelity on macOS."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import subprocess
import time
import traceback



def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def snapshot(root):
    rows = {}
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(folder) / name
            record = dict(mode=stat.S_IMODE(path.lstat().st_mode))
            if path.is_symlink():
                record.update(kind='symlink', target=os.readlink(path))
            elif path.is_file():
                record.update(kind='file', bytes=path.stat().st_size, sha256=digest(path))
            elif path.is_dir():
                record['kind'] = 'directory'
            else:
                raise ValueError('Unsupported app bundle entry: ' + str(path))
            rows[path.relative_to(root).as_posix()] = record
    return rows


def verify(build_directory, report_directory):
    build_directory = Path(build_directory).resolve()
    report_directory = Path(report_directory).absolute()
    report_directory.mkdir(parents=True, exist_ok=False)
    build = json.loads((build_directory / 'build-result.json').read_text())
    result = dict(success=False, scope=__doc__, started_at=time.time(), build=build)
    (report_directory / 'run.json').write_text(json.dumps(result, indent=2) + '\n')
    commands = []
    mounted = False
    mount = report_directory / 'mounted.noindex'
    mount.mkdir()
    started = time.monotonic()

    def run(command, timeout=120):
        output = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        commands.append(dict(command=command, returncode=output.returncode,
                             stdout=output.stdout, stderr=output.stderr))
        output.check_returncode()
        return output

    try:
        if shutil.disk_usage(report_directory).free < 2 * 2**30:
            raise OSError('Insufficient scratch disk for artifact verification')
        image = Path(build['dmg'])
        if image.stat().st_size != build['dmg_bytes'] or digest(image) != build['dmg_sha256']:
            raise ValueError('Artifact differs from build receipt: ' + image.name)
        result['image_hash_passed'] = True
        guide = build.get('first_open_guide')
        guide_payload = None
        if guide is not None:
            if guide.get('name') != 'Open FreeVideo.txt':
                raise ValueError('Unexpected first-open guide name')
            guide_path = image.parent / guide['name']
            if guide_path.is_symlink():
                raise ValueError('First-open guide must be a regular file')
            guide_payload = guide_path.read_bytes()
            if (len(guide_payload) != guide['bytes'] or
                    hashlib.sha256(guide_payload).hexdigest() != guide['sha256']):
                raise ValueError('First-open guide differs from build receipt')
        original = snapshot(Path(build['app']))
        run(['/usr/bin/hdiutil', 'verify', build['dmg']])
        run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-noautoopen',
             '-mountpoint', str(mount), build['dmg']])
        mounted = True
        if not (mount / 'Applications').is_symlink() or os.readlink(mount / 'Applications') != '/Applications':
            raise ValueError('DMG Applications shortcut changed')
        if guide is not None:
            image_guide = mount / guide['name']
            if image_guide.is_symlink() or image_guide.read_bytes() != guide_payload:
                raise ValueError('DMG first-open guide differs from build receipt')
            result['first_open_guide_exact'] = True
        imaged = snapshot(mount / 'FreeVideo.app')
        if imaged != original:
            result['dmg_differences'] = [k for k in sorted(set(imaged) | set(original))
                                         if imaged.get(k) != original.get(k)]
            raise ValueError('DMG app differs from built app')
        run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(mount / 'FreeVideo.app')])
        result.update(dmg_app_exact=True, bundle_entries=len(original),
                      gatekeeper_accepted=False, notarized=False)
        (report_directory / 'bundle-manifest.json').write_text(json.dumps(original, indent=2) + '\n')
        result['success'] = True
    except BaseException:
        result['error'] = traceback.format_exc()
    finally:
        if mounted:
            try:
                run(['/usr/bin/hdiutil', 'detach', str(mount)], timeout=60)
                result['mounted_image_detached'] = True
            except BaseException:
                result.update(success=False, detach_error=traceback.format_exc())
        result.update(elapsed_seconds=time.monotonic()-started, commands=commands)
        (report_directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('commands','build')}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('Verify the disk image on native macOS')
    result = verify(args.build, args.out)
    raise SystemExit(not result['success'])


if __name__ == '__main__':
    main()
