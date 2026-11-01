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
import shlex

from .long_paths import io_path, plain, resolved

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
    environment = io_path(environment)
    if (environment / 'Lib' / 'site-packages').is_dir():
        return Path(plain(environment / 'Lib' / 'site-packages'))
    found = sorted(environment.glob('lib/python*/site-packages'))
    return Path(plain(found[-1])) if found else None


def _digest(path, algorithm):
    digest = hashlib.new(algorithm)
    with open(io_path(path), 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return algorithm + '=' + base64.urlsafe_b64encode(digest.digest()).rstrip(b'=').decode('ascii')


def _canonical(path):
    """One spelling per file for comparisons: links resolved, case folded on Windows."""
    text = os.path.realpath(io_path(path))
    return os.path.normcase(plain(text))


def check(site, progress=None):
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
    for distribution in importlib.metadata.distributions(path=[str(io_path(site))]):
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
    if progress:
        progress(0, len(expected))
    for path, records in expected.values():
        owners = sorted({name for name, _, _ in records})
        entry = dict(name=os.path.basename(path), owner=owners[0], owners=owners)
        directories.add(os.path.dirname(path))
        result['checked'] += 1
        try:
            if not os.path.isfile(io_path(path)):
                result['changed'].append(dict(entry, state='missing'))
                continue
            size = os.path.getsize(io_path(path))
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
        finally:
            if progress:
                progress(result['checked'], len(expected))
    recorded = {_canonical(path) for path, _ in expected.values()}
    for directory in sorted(directories):
        try:
            names = sorted(os.listdir(io_path(directory)))
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
        result['missing_files'] = {name: files for name, files in list(package_files(site).items())[:6]}
    except Exception as error:
        result['missing_files_error'] = '%s: %s' % (type(error).__name__, error)
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
    missing = result.get('missing_files')
    if isinstance(missing, dict) and missing:
        names = ['%s (%s)' % (name, ', '.join(str(file).rsplit('/', 1)[-1] for file in files[:2]))
                 for name, files in list(missing.items())[:4] if isinstance(files, list) and files]
        if names:
            lines.append('Files of these Python packages are missing or have changed: %s%s. %s'
                         % (', '.join(names), ', …' if len(missing) > 4 else '', repair))
    outside = sorted({str(Path(p).parent) for p in result.get('outside', [])})
    if outside:
        lines.append('cuDNN was also loaded from outside FreeVideo: %s.' % '; '.join(outside[:3]))
    return '\n'.join(lines)


def site_for(python):
    """site-packages of the environment a Python executable runs: a virtual
    environment (Scripts/ or bin/ beside it) or an embedded Windows Python."""
    python = io_path(python)
    for candidate in (python.parent / 'Lib' / 'site-packages', python.parent.parent / 'Lib' / 'site-packages'):
        if candidate.is_dir():
            return Path(plain(candidate))
    return site_packages(python.parent.parent)


def normalize_name(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def _requirement_rows(requirements):
    pending, seen, rows = [Path(requirements)], set(), []
    while pending:
        try:
            path = resolved(pending.pop())
            if path in seen:
                continue
            seen.add(path)
            lines = path.read_text(encoding='utf-8-sig').splitlines()
        except (OSError, UnicodeError, RuntimeError):
            return None
        continued = ''
        for line in lines:
            if line.lstrip().startswith('#'):
                continue
            continued += line
            if continued.endswith('\\'):
                continued = continued[:-1]
                continue
            row = re.split(r'\s+#', continued, maxsplit=1)[0].strip()
            continued = ''
            if not row:
                continue
            if row.startswith('-e') or re.match(r'^--editable(?:\s|=|$)', row):
                return None
            if row.startswith('-r') or re.match(r'^--requirement(?:\s|=|$)', row):
                value = re.sub(r'^(?:-r\s*|--requirement(?:\s+|=)?)', '', row)
                try:
                    paths = shlex.split(value, posix=False)
                except ValueError:
                    return None
                if len(paths) != 1 or '://' in paths[0]:
                    return None
                pending.append(path.parent / paths[0].strip('\'"'))
            elif not row.startswith('-'):
                if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', row):
                    try:
                        if (path.parent / row).exists():
                            return None
                    except OSError:
                        return None
                rows.append(row)
        if continued:
            return None
    return rows


def dependency_closure(python, requirements, additional=()):
    """Required names from a requirements file and installed metadata, or None if uncertain."""
    rows = _requirement_rows(requirements)
    if rows is None:
        return None
    try:
        from packaging.requirements import Requirement
    except ImportError:
        Requirement = None
    dependencies = {}
    site = site_for(python)
    if site is not None:
        for distribution in importlib.metadata.distributions(path=[str(io_path(site))]):
            try:
                name = distribution.metadata['Name']
                if name:
                    dependencies.setdefault(normalize_name(name), []).extend(distribution.requires or [])
            except (OSError, ValueError, UnicodeDecodeError, KeyError):
                continue
    pending = [(row, {''}) for row in [*rows, *additional]]
    seen = {}
    while pending:
        row, parent_extras = pending.pop()
        row = re.split(r'\s+#', row, maxsplit=1)[0].strip()
        if re.fullmatch(r'[A-Za-z0-9._-]+\.(?:whl|zip|tar(?:\.(?:gz|bz2|xz))?)(?:\[[^]]+\])?(?:\s*;.*)?', row, re.I):
            return None
        extras = {''}
        if Requirement is not None:
            try:
                requirement = Requirement(row)
                if requirement.marker and not any(requirement.marker.evaluate({'extra': extra})
                                                  for extra in parent_extras):
                    continue
                name = normalize_name(requirement.name)
                extras.update(requirement.extras)
            except ValueError:
                return None
        else:
            match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9._-]*)(?:\s*\[([A-Za-z0-9._,\s-]+)\])?'
                                 r'\s*(?:[<>=!~].*|@\s*\S+)?\s*(?:;.*)?', row)
            if not match:
                return None
            name = normalize_name(match[1])
            extras.update(value.strip() for value in (match[2] or '').split(','))
        previous = seen.setdefault(name, set())
        if extras <= previous:
            continue
        previous.update(extras)
        pending.extend((row, set(previous)) for row in dependencies.get(name, []))
    return set(seen)


def damaged(python, progress=None):
    """Distributions whose recorded cuDNN files differ in this Python's environment.

    Sizes are compared first and only files of the recorded size are hashed,
    so this reads the libraries once, at setup or repair, never per launch.
    A check that cannot run finds nothing: it must never stop a setup.
    """
    try:
        site = site_for(python)
        found = check(site, **({'progress': progress} if progress else {})) if site is not None else {'changed': []}
    except Exception:
        return []
    return sorted({owner for row in found['changed'] for owner in row['owners']})


def incomplete(python):
    """Distributions with missing or wrong-sized RECORD files, without hashing.

    Only files inside this environment with a recorded size are checked.
    Unreadable records and checks that cannot run must never stop a setup.
    """
    try:
        site = site_for(python)
        if site is None:
            return []
        site = io_path(site)
        parent = Path(python).parent
        root = _canonical(parent.parent if parent.name.lower() in ('bin', 'scripts') else parent)
        found = set()
        for distribution in importlib.metadata.distributions(path=[str(site)]):
            try:
                rows = list(csv.reader(io.StringIO(distribution.read_text('RECORD') or '')))
                name = distribution.metadata['Name']
            except (OSError, csv.Error, UnicodeDecodeError, KeyError):
                continue
            if not name:
                continue
            for row in rows:
                if len(row) < 3 or not row[2].isdigit():
                    continue
                path = _canonical(os.path.join(str(site), row[0]))
                if not path.startswith(root + os.sep):
                    continue
                try:
                    size = os.path.getsize(io_path(path))
                except (FileNotFoundError, NotADirectoryError):
                    size = None
                except OSError:
                    continue
                if size != int(row[2]):
                    found.add(name)
                    break
        return sorted(found)
    except Exception:
        return []


def package_files(site, limit=3):
    """Files RECORD lists inside site-packages that are missing or have another size.

    Returns {distribution: [paths relative to site-packages, at most `limit`]}. Only
    files below site-packages count: console scripts and data files sit beside it, and
    their size depends on where the environment was created. A numpy extension that
    cannot load is usually one of these files gone (quarantined, cleaned up, cut short),
    which an ordinary install does not notice. No hashing, so it is cheap to run.
    Unreadable records and checks that cannot run find nothing.
    """
    found = {}
    try:
        site = Path(site)
        # Paths stay as RECORD spells them: a package folder that is a junction
        # to another drive is still checked.
        base = os.path.normcase(os.path.normpath(str(site))) + os.sep
        for distribution in importlib.metadata.distributions(path=[str(io_path(site))]):
            try:
                rows = list(csv.reader(io.StringIO(distribution.read_text('RECORD') or '')))
                name = distribution.metadata['Name']
            except (OSError, csv.Error, UnicodeDecodeError, KeyError):
                continue
            if not name:
                continue
            for row in rows:
                if len(row) < 3 or not row[2].isdigit():
                    continue
                path = os.path.normpath(os.path.join(str(site), row[0]))
                if not os.path.normcase(path).startswith(base):
                    continue
                try:
                    size = os.path.getsize(io_path(path))
                except (FileNotFoundError, NotADirectoryError):
                    size = None
                except OSError:
                    continue
                if size != int(row[2]):
                    files = found.setdefault(name, [])
                    if len(files) >= limit:
                        break
                    files.append(row[0].replace('\\', '/'))
    except Exception:
        return {}
    return found


def gpu_family(name):
    """The packages installed from the GPU wheels: PyTorch, its CUDA libraries and its compiler."""
    name = normalize_name(name)
    return name in ('torch', 'torchvision', 'torchaudio', 'triton', 'triton-windows') or name.startswith('nvidia-')


def pinned_names(constraints):
    """Normalized names a constraints file pins, or None when it cannot be read."""
    rows = _requirement_rows(constraints)
    if rows is None:
        return None
    return {normalize_name(match[0]) for match in (re.match(r'[A-Za-z0-9][A-Za-z0-9._-]*', row) for row in rows) if match}


def missing_packages(python, constraints):
    """Pinned distributions of this environment whose RECORD files are missing or cut short.

    Setup and repair reinstall these: uv treats a package at the pinned version
    as installed whatever its files look like. Packages the constraints do not
    pin (editable installs, kernels built on this machine) are left alone.
    """
    try:
        site = site_for(python)
        pinned = pinned_names(constraints)
        if site is None or pinned is None:
            return []
        return sorted(name for name in package_files(site, limit=1) if normalize_name(name) in pinned)
    except Exception:
        return []


def installed_bytes(python, name):
    """Installed size of one distribution in this Python's environment, from its RECORD."""
    try:
        site = site_for(python)
        for distribution in importlib.metadata.distributions(path=[str(io_path(site))] if site else []):
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
