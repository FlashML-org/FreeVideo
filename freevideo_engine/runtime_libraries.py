"""Whether the cuDNN files a process uses are the ones FreeVideo installed.

cuDNN 9 is one dispatcher and seven sublibraries, and it refuses to run when
they come from different builds (CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH).
The Windows PyTorch wheel ships all eight in torch/lib and Linux gets them from
nvidia-cudnn-cu13, so a mismatch means a file there was replaced or another
copy was loaded first. Environment files are hard links into the uv cache, so
replacing one in place changes the cached copy too, and an ordinary reinstall
would link the changed file back. Each owning distribution's RECORD holds the
hashes it was installed with; that is what this compares against.
"""
import base64
import csv
import hashlib
import importlib.metadata
import io
import os
from pathlib import Path
import re

# ComfyUI messages choose between this and the page's own setup (repair_hint).
from .comfy_launcher_api import LAUNCHER_REPAIR as REINSTALL

LIBRARY = re.compile(r'^(?:lib)?cudnn[\w.-]*\.(?:dll|so(?:\.\d+)*)$', re.I)
MISMATCH = re.compile(r'SUBLIBRARY_VERSION_MISMATCH|SUBLIBRARY_LOADING_FAILED|CUDNN_STATUS_VERSION_MISMATCH'
                      r'|cuDNN version incompatibility'
                      # The system loader's refusal, before cuDNN can check anything: Windows names
                      # the cuDNN file whose import failed (torch loads them all when imported),
                      # Linux the missing, damaged or mismatched file.
                      r'|(?:WinError (?:126|127|193)|DLL load failed|cannot open shared object file|undefined symbol)[^\n]*cudnn'
                      r'|cudnn[^\n]*(?:cannot open shared object file|undefined symbol|file too short|invalid ELF header)', re.I)


def mismatch(error):
    """True for the errors raised when cuDNN files come from different builds:
    cuDNN's own status, or the loader failing on a cuDNN file."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if MISMATCH.search(str(error)):
            return True
        error = getattr(error, '__cause__', None) or getattr(error, '__context__', None)
    return False


def site_packages(environment):
    """The site-packages folder of a virtual environment, Windows or POSIX."""
    environment = Path(environment)
    if (environment / 'Lib' / 'site-packages').is_dir():
        return environment / 'Lib' / 'site-packages'
    found = sorted(environment.glob('lib/python*/site-packages'))
    return found[-1] if found else None


def _digest(path, algorithm):
    digest = hashlib.new(algorithm)
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return algorithm + '=' + base64.urlsafe_b64encode(digest.digest()).rstrip(b'=').decode('ascii')


def _canonical(path):
    """One spelling per file for comparisons: links resolved, case folded on Windows."""
    text = os.path.realpath(str(path))
    return os.path.normcase(text[4:] if text.startswith('\\\\?\\') else text)


def check(site):
    """Compare every recorded cuDNN file under ``site`` with its RECORD hash.

    A file is changed only when it matches none of the hashes recorded for its
    path: leftover metadata of another build (nvidia-cudnn-cu12 beside cu13)
    lists the same paths with other hashes and must not count as damage, or
    every setup would reinstall. Paths are kept as RECORD spells them, so a
    package folder that is a junction to another drive is still checked. A
    file that cannot be read is reported as unreadable, not as changed.
    """
    site = Path(site)
    root = os.path.normcase(os.path.normpath(str(site)))
    result = dict(owners=[], checked=0, changed=[], unrecorded=[], unreadable=[], directories=[])
    expected = {}
    for distribution in importlib.metadata.distributions(path=[str(site)]):
        try:
            rows = list(csv.reader(io.StringIO(distribution.read_text('RECORD') or '')))
            name = distribution.metadata['Name']
        except (OSError, csv.Error, UnicodeDecodeError, KeyError):
            continue
        for row in rows:
            if len(row) < 2 or not LIBRARY.match(row[0].replace('\\', '/').rsplit('/', 1)[-1]):
                continue
            path = os.path.normpath(os.path.join(str(site), row[0]))
            if not os.path.normcase(path).startswith(root + os.sep):
                continue
            size = int(row[2]) if len(row) > 2 and row[2].isdigit() else None
            expected.setdefault(os.path.normcase(path), (path, []))[1].append((name, row[1], size))
            if name not in result['owners']:
                result['owners'].append(name)
    directories = set()
    for path, records in expected.values():
        owners = sorted({name for name, _, _ in records})
        entry = dict(name=os.path.basename(path), owner=owners[0], owners=owners)
        directories.add(os.path.dirname(path))
        result['checked'] += 1
        try:
            if not os.path.isfile(path):
                result['changed'].append(dict(entry, state='missing'))
                continue
            size = os.path.getsize(path)
            matching = [(value, length) for _, value, length in records if length is None or length == size]
            hashed = {}
            def matches(value):
                algorithm = value.partition('=')[0]
                if not value or algorithm not in hashlib.algorithms_guaranteed:
                    return True
                if algorithm not in hashed:
                    hashed[algorithm] = _digest(path, algorithm)
                return hashed[algorithm] == value
            if not any(matches(value) for value, _ in matching):
                sizes = sorted({length for _, _, length in records if length is not None})
                result['changed'].append(dict(entry, state='changed', size=size,
                                              expected_size=sizes[0] if len(sizes) == 1 else sizes or None))
        except OSError as error:
            result['unreadable'].append(dict(entry, error=type(error).__name__))
    recorded = {_canonical(path) for path, _ in expected.values()}
    for directory in sorted(directories):
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            if LIBRARY.match(name) and _canonical(os.path.join(directory, name)) not in recorded:
                result['unrecorded'].append(name)
    result['directories'] = sorted(directories)
    return result


def loaded():
    """Paths of the cuDNN files mapped into this process."""
    import psutil
    paths = set()
    for mapping in psutil.Process().memory_maps(grouped=True):
        path = getattr(mapping, 'path', '') or ''
        if LIBRARY.match(path.replace('\\', '/').rsplit('/', 1)[-1]):
            paths.add(path)
    return sorted(paths)


def diagnose(site=None):
    """What this process loaded and what differs on disk, for a failed request."""
    if site is None:
        import sysconfig
        site = Path(sysconfig.get_paths()['purelib'])
    result = dict(loaded=[], outside=[], changed=[], unrecorded=[], unreadable=[], owners=[], checked=0)
    directories = set()
    try:
        found = check(site)
        directories = {_canonical(d) for d in found.pop('directories')}
        result.update(found)
    except Exception as error:
        result['check_error'] = '%s: %s' % (type(error).__name__, error)
    try:
        for path in loaded():
            inside = _canonical(os.path.dirname(path)) in directories
            result['loaded'].append(dict(name=os.path.basename(path.replace('\\', '/')), inside=inside))
            if not inside:
                result['outside'].append(path)
    except Exception as error:
        result['loaded_error'] = '%s: %s' % (type(error).__name__, error)
    return result


def summary(result, repair=REINSTALL):
    """One or two plain sentences for the failure message, or ''. `repair` says where to repair."""
    if not isinstance(result, dict):
        return ''
    lines = []
    changed = [row.get('name') for row in result.get('changed', []) if isinstance(row, dict)]
    if changed:
        lines.append("Some of FreeVideo's PyTorch library files do not match the installed version (%s). %s"
                     % (', '.join(changed[:4]) + (', …' if len(changed) > 4 else ''), repair))
    outside = sorted({str(Path(p).parent) for p in result.get('outside', [])})
    if outside:
        lines.append('cuDNN was also loaded from outside FreeVideo: %s.' % '; '.join(outside[:3]))
    return '\n'.join(lines)


def site_for(python):
    """site-packages of the environment a Python executable runs: a virtual
    environment (Scripts/ or bin/ beside it) or an embedded Windows Python."""
    python = Path(python)
    for candidate in (python.parent / 'Lib' / 'site-packages', python.parent.parent / 'Lib' / 'site-packages'):
        if candidate.is_dir():
            return candidate
    return site_packages(python.parent.parent)


def damaged(python):
    """Distributions whose recorded cuDNN files differ in this Python's environment.

    Sizes are compared first and only files of the recorded size are hashed,
    so this reads the libraries once, at setup or repair, never per launch.
    A check that cannot run finds nothing: it must never stop a setup.
    """
    try:
        site = site_for(python)
        found = check(site) if site is not None else {'changed': []}
    except Exception:
        return []
    return sorted({owner for row in found['changed'] for owner in row['owners']})


def installed_bytes(python, name):
    """Installed size of one distribution in this Python's environment, from its RECORD."""
    try:
        site = site_for(python)
        for distribution in importlib.metadata.distributions(path=[str(site)] if site else []):
            if (distribution.metadata['Name'] or '').lower() == name.lower():
                rows = csv.reader(io.StringIO(distribution.read_text('RECORD') or ''))
                return sum(int(row[2]) for row in rows if len(row) > 2 and row[2].isdigit())
    except Exception:
        pass
    return 0


def reinstall_arguments(names):
    """uv options that put back fresh files for these distributions.

    --reinstall-package alone is not enough, and neither is --refresh-package:
    an environment file is a hard link into the uv cache, so a file replaced in
    place changed the cached copy too, and both link those same bytes back
    (measured with uv 0.9.0). --no-cache unpacks the wheel again without
    touching the existing cache; copying avoids hard links out of that
    temporary cache, which may sit on another drive.
    """
    if not names:
        return []
    return [*(flag for name in names for flag in ('--reinstall-package', name)), '--no-cache', '--link-mode', 'copy']
