"""The pinned download table for the native Windows on Arm (win_arm64) install.

Everything the install fetches for that platform is in windows_arm64.json, kept apart from the x64 tables
(bootstrap_versions.json, uv_downloads.json, torch_downloads.json, distribution/windows-runtime-wheels.json): those files
are not touched, so a Windows x64 install, its package fingerprints and its downloads stay exactly as they were.
Each row carries a sha256 and a byte count; a file is used only when both match.
"""
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

TABLE = Path(__file__).with_name('windows_arm64.json')
HASH = re.compile(r'[0-9a-f]{64}')
# Where a pinned file may come from; the installer's mirrors keep these paths.
HOSTS = ('github.com', 'files.pythonhosted.org', 'pypi.tuna.tsinghua.edu.cn', 'download.pytorch.org', 'pypi.nvidia.com')


def _file(row, name, *, executable=None):
    if not isinstance(row.get('bytes'), int) or row['bytes'] <= 0:
        raise ValueError(name + ': missing byte count')
    if not HASH.fullmatch(str(row.get('sha256', ''))):
        raise ValueError(name + ': sha256 must be 64 lowercase hex digits')
    return row


def _address(url, filename, name):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in HOSTS or parts.username is not None
            or parts.query or parts.fragment):
        raise ValueError(name + ': address is not an allowed https source')
    if filename is not None and unquote(parts.path.rsplit('/', 1)[-1]) != filename:
        raise ValueError(name + ': address does not name the file')
    return parts


def validate(table):
    """Raise ValueError unless every row is complete, ARM64-named and from an allowed source."""
    if table.get('schema_version') != 1 or table.get('platform') != 'win_arm64' or table.get('python_tag') != 'cp312':
        raise ValueError('windows_arm64.json: wrong schema, platform or Python tag')
    python = _file(table['python'], 'python')
    if 'aarch64-pc-windows-msvc' not in python['filename'] or not python['filename'].endswith('-install_only.tar.gz'):
        raise ValueError('python: not the aarch64 Windows install_only build')
    if '+' + python['release'] + '-' not in python['filename'] or '-' + python['version'] + '+' not in python['filename']:
        raise ValueError('python: version or release does not match the file')
    _address(python['url'], python['filename'], 'python')
    uv = _file(table['uv'], 'uv')
    if not uv['filename'].endswith('-win_arm64.whl') or '-' + uv['version'] + '-' not in uv['filename']:
        raise ValueError('uv: not the win_arm64 wheel of the pinned version')
    if not uv.get('sources') or not isinstance(uv.get('executable_bytes'), int):
        raise ValueError('uv: sources and the executable size are required')
    for source, url in uv['sources'].items():
        _address(url, uv['filename'], 'uv ' + source)
    _file(dict(bytes=uv['legacy_bytes'], sha256=uv['legacy_sha256']), 'uv archive')
    if 'aarch64-pc-windows-msvc' not in uv['legacy_url']:
        raise ValueError('uv archive: not the aarch64 build')
    _address(uv['legacy_url'], None, 'uv archive')
    git = _file(table['git'], 'git')
    if not git['url'].endswith('-arm64.zip') or git['version'] not in git['url']:
        raise ValueError('git: not the arm64 MinGit of the pinned version')
    _address(git['url'], None, 'git')
    for row in table.get('torch', []):
        _file(row, row.get('filename', 'torch row'))
        # torch and torchvision are cp312 builds; torchaudio 2.11.1 is one py3-none win_arm64 file for every Python.
        if not row['filename'].endswith(('-cp312-cp312-win_arm64.whl', '-py3-none-win_arm64.whl')):
            raise ValueError(row['filename'] + ': not a cp312 or py3-none win_arm64 wheel')
        for source, url in row['sources'].items():
            _address(url, row['filename'], row['filename'] + ' ' + source)
    return table


def load():
    return validate(json.loads(TABLE.read_text(encoding='utf-8')))


def first_install_notice():
    """Shown while the x64 environment of an earlier version is replaced (wording: Lane)."""
    from .launcher_copy import size_text
    text = 'This is the first installation of the native runtime for this computer, so the runtime components are downloaded again'
    try:
        size = load().get('runtime_wheel_bytes')
    except (OSError, ValueError, KeyError):
        size = None
    return text + (' (about %s).' % size_text(size) if size else '.')
