"""Create portable standalone zip packages of FreeVideo.

Packages source code, web studio interface, workflows, and launcher scripts
into a portable standalone archive.
"""
import argparse
import json
from pathlib import Path
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from freevideo_engine.desktop_runtime import source_files
from scripts.build_windows import build_info


def package_zip(destination_zip: Path, target_system: str = 'all') -> Path:
    """Create a standalone portable zip file from source files."""
    destination_zip.parent.mkdir(parents=True, exist_ok=True)
    identity = build_info(ROOT)
    identity['target_package'] = target_system
    identity['packaged_at'] = int(time.time())

    with zipfile.ZipFile(destination_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        # Include all clean engine, web, and config files
        for file_path in source_files(ROOT):
            rel = file_path.relative_to(ROOT)
            zf.write(file_path, arcname=str(rel))

        # Include root execution scripts if present
        extra_root_files = [
            'MakeInstaller.bat', 'Full_Install.bat', 'README.md',
            'README.zh-CN.md', 'LICENSE', 'NOTICE'
        ]
        for extra in extra_root_files:
            extra_path = ROOT / extra
            if extra_path.is_file() and extra not in [p.name for p in source_files(ROOT)]:
                zf.write(extra_path, arcname=extra)

        # Write build identity descriptor
        zf.writestr('launcher-build.json', json.dumps(identity, indent=2))

    return destination_zip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='Output directory for the zip file')
    parser.add_argument('--target', choices=['all', 'windows', 'macos', 'linux'], default='all',
                        help='Target distribution flavor')
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())
    zip_name = f'FreeVideo-Portable-{args.target.capitalize()}-{timestamp}.zip'
    output_path = args.out / zip_name

    print(f'[FreeVideo] Creating portable zip archive: {output_path} ...')
    package_zip(output_path, target_system=args.target)
    print(f'[FreeVideo] Archive created successfully ({output_path.stat().st_size / (1024*1024):.2f} MB).')
    print(json.dumps({'archive': str(output_path), 'bytes': output_path.stat().st_size}))


if __name__ == '__main__':
    main()
