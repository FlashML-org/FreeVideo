"""Trusted source-overlay bootstrap; intentionally outside freevideo_engine.

Entry points call activate() BEFORE importing that package. This module and its
trust anchors remain frozen, and are part of the runtime compatibility key.
No network, widgets or third-party imports are needed to recover a broken update.
"""
import hashlib
import importlib.machinery
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid
import zipfile

# Freeze the trust anchors before any source directory enters sys.path.
import freevideo_ed25519
import freevideo_update_keys

MAX_MANIFEST = 2 * 2**20
MAX_FILES = 4096
MAX_FILE = 32 * 2**20
MAX_SOURCE = 128 * 2**20
MAX_ZIP = 64 * 2**20
HEX = re.compile(r'[0-9a-f]{64}')
VERSION = re.compile(r'\d+\.\d+\.\d+')
TOP_FILES = {'pyproject.toml', 'README.md', 'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md',
             'MANIFEST.in', '__init__.py', 'freevideo_source_bootstrap.py', 'freevideo_ed25519.py',
             'freevideo_update_keys.py', 'freevideo', 'freevideo.ps1', 'freevideo.cmd',
             'setup.sh', 'setup.ps1', 'setup.cmd', 'test.sh', 'test.ps1', 'test.cmd'}
EXTENSIONS = {'.py', '.json', '.txt', '.ps1', '.sh', '.js', '.css', '.svg', '.png', '.ico',
              '.qml', '.woff2', '.otf', '.webp'}
ACTIVE = None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(256*1024), b''):
            result.update(chunk)
    return result.hexdigest()


def io_path(path):
    path = Path(os.path.abspath(path))
    if os.name == 'nt' and not str(path).startswith('\\\\?\\'):
        return Path('\\\\?\\UNC\\' + str(path)[2:] if str(path).startswith('\\\\') else '\\\\?\\' + str(path))
    return path


def root():
    if os.environ.get('FREEVIDEO_LAUNCHER_HOME'):
        return io_path(os.environ['FREEVIDEO_LAUNCHER_HOME'])
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/FreeVideo/launcher'
    return io_path(Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'FreeVideo-launcher')


TOUR_FILES = frozenset((
    'web/assets/tour/anime-first.webp',
    'web/assets/tour/anime-last.webp',
    'web/assets/tour/c1-s2.mp4',
    'web/assets/tour/c1-s2.webp',
    'web/assets/tour/c2-product.webp',
    'web/assets/tour/c2-whitemodel.webp',
    'web/assets/tour/c2g-s3.mp4',
    'web/assets/tour/c2g-s3.webp',
    'web/assets/tour/c3-s1.mp4',
    'web/assets/tour/c3-s1.webp',
    'web/assets/tour/c4-high.mp4',
    'web/assets/tour/c4-high.webp',
    'web/assets/tour/c4-light.mp4',
    'web/assets/tour/c4-light.webp',
    'web/assets/tour/c4-max.mp4',
    'web/assets/tour/c4-max.webp',
    'web/assets/tour/c4-medium.mp4',
    'web/assets/tour/c4-medium.webp',
    'web/assets/tour/c5-forest-s1.mp4',
    'web/assets/tour/c5-forest-s1.webp',
    'web/assets/tour/c5-snow-s1.mp4',
    'web/assets/tour/c5-snow-s1.webp',
    'web/assets/tour/c6-s1.mp4',
    'web/assets/tour/c6-s1.webp',
    'web/assets/tour/c7-s1.mp4',
    'web/assets/tour/c7-s1.webp',
    'web/assets/tour/c7-src-s1.mp4',
    'web/assets/tour/c7-src-s1.webp',
    'web/assets/tour/c8-original.webp',
    'web/assets/tour/c8-s1.mp4',
    'web/assets/tour/c8-s1.webp',
    'web/assets/tour/c9-s1.mp4',
    'web/assets/tour/c9-s1.webp',
    'web/assets/tour/fox-sheet.webp',
))


def safe_name(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 240 or '\\' in name or ':' in name:
        raise ValueError('Invalid source path')
    if name.startswith('web/assets/tour/') and name not in TOUR_FILES:
        raise ValueError('Unregistered tour asset')
    parts = name.split('/')
    for part in parts:
        if (part in ('', '.', '..') or part.endswith((' ', '.')) or
                any(ord(c) < 32 or c in '<>"|?*' for c in part) or
                re.fullmatch(r'(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?', part, re.I)):
            raise ValueError('Invalid source path')
    if any(part == '__pycache__' for part in parts):
        raise ValueError('Bytecode caches are not source')
    if name not in TOP_FILES and name not in TOUR_FILES and name != 'web/vendor/three/LICENSE' and not (
            parts[0] == 'web' and len(parts) > 2 and parts[-2] == 'tutorial' and Path(name).suffix == '.mp4') and not (len(parts) > 1 and parts[0] in
            ('freevideo_engine', 'web', 'constraints', 'example_workflows', 'scripts') and
            Path(name).suffix in EXTENSIONS):
        raise ValueError('Source path is outside the payload')
    return name


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def decode(data):
    if len(data) > MAX_MANIFEST:
        raise ValueError('Source manifest too large')
    return json.loads(data, object_pairs_hook=_pairs)


def read_json(path):
    with path.open('rb') as stream:
        return decode(stream.read(MAX_MANIFEST+1))


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def source_identity(value):
    return sha(canonical({k: value[k] for k in ('base', 'build', 'files', 'gui')}))


def manifest(value):
    if not isinstance(value, dict) or value.get('schema') != 1 or not HEX.fullmatch(str(value.get('base', ''))):
        raise ValueError('Invalid source manifest')
    build, files = value.get('build'), value.get('files')
    if (not isinstance(build, dict) or not VERSION.fullmatch(str(build.get('product_version', ''))) or
            not isinstance(build.get('version'), str) or not 1 <= len(build['version']) <= 64 or
            not re.fullmatch(r'[0-9a-f]{40,64}', str(build.get('revision', ''))) or
            type(build.get('built_at')) is not int or build['built_at'] <= 0 or
            build.get('repository') != 'FlashML-org/FreeVideo' or build.get('schema') != 1 or
            build.get('channel') not in ('windows-preview', 'macos-preview', 'linux-preview') or
            build.get('track', 'stable') != 'stable' or type(value.get('gui')) is not bool or
            not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES or
            # A source tree without its root package would let the older one load.
            'freevideo_engine/__init__.py' not in files):
        raise ValueError('Invalid source build')
    total, seen = 0, set()
    for name, row in files.items():
        safe_name(name)
        if name.casefold() in seen or not isinstance(row, dict):
            raise ValueError('Duplicate source path')
        seen.add(name.casefold())
        if (type(row.get('bytes')) is not int or not 0 <= row['bytes'] <= MAX_FILE or
                not HEX.fullmatch(str(row.get('sha256', '')))):
            raise ValueError('Invalid source file')
        total += row['bytes']
    if total > MAX_SOURCE or value.get('identity') != source_identity(value):
        raise ValueError('Invalid source identity or size')
    # No file may be an ancestor of another, on case-insensitive systems either.
    if any('/'.join(name.casefold().split('/')[:i]) in seen
           for name in files for i in range(1, len(name.split('/')))):
        raise ValueError('Conflicting source paths')
    delta = value.get('delta')
    if delta is not None:
        if (not isinstance(delta, dict) or not HEX.fullmatch(str(delta.get('from', ''))) or
                not isinstance(delta.get('files'), list) or not isinstance(delta.get('deleted'), list) or
                len(delta['files']) + len(delta['deleted']) > 2*MAX_FILES or
                type(delta.get('bytes')) is not int or not 1 <= delta['bytes'] <= MAX_ZIP or
                not HEX.fullmatch(str(delta.get('sha256', ''))) or
                not re.fullmatch(r'delta-[0-9a-f]{16}-to-[0-9a-f]{16}\.zip', str(delta.get('name', '')))):
            raise ValueError('Invalid source delta')
        for field in ('files', 'deleted'):
            names = delta[field]
            if len(set(names)) != len(names):
                raise ValueError('Duplicate delta path')
            for name in names:
                safe_name(name)
                if (name in files) != (field == 'files'):
                    raise ValueError('Inconsistent delta path')
    return value


def read_manifest(path):
    return manifest(read_json(path))


def regular(root_path, relative):
    current = root_path
    if current.is_symlink():
        raise ValueError('Symlink source directory')
    for part in relative.split('/'):
        current = current / part
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError('Symlink source path')
    if not stat.S_ISREG(current.stat().st_mode):
        raise ValueError('Nonregular source file')
    return current


def verify_file(path, row):
    if path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
        raise ValueError('Source file changed')


def verify_tree(folder, files):
    for name, row in files.items():
        verify_file(regular(folder, name), row)
    found = set()
    for path in folder.rglob('*'):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Symlink in source tree')
        if path.is_file():
            found.add(path.relative_to(folder).as_posix())
    if found != set(files):
        raise ValueError('Unexpected source files')


def compatible(value, base):
    a, b = value['build'], base['build']
    return (value['base'] == base['base'] and value['gui'] == base['gui'] and
            all(a.get(k, default) == b.get(k, default) for k, default in
                (('target', 'windows-x86_64'), ('channel', None), ('packaging', None), ('track', 'stable'))))


def overlay_name(value):
    return value['build']['product_version'] + '-' + value['identity'][:16]


def validate_overlay(folder, base):
    if {p.name for p in folder.iterdir()} != {'receipt.json', 'files'}:
        raise ValueError('Unexpected overlay files')
    receipt = read_json(regular(folder, 'receipt.json'))
    value = manifest(receipt['manifest'])
    if (receipt.get('sha256') != sha(canonical(value)) or not compatible(value, base) or
            folder.name != overlay_name(value)):
        raise ValueError('Overlay receipt changed')
    changed = {n: r for n, r in value['files'].items() if base['files'].get(n) != r}
    verify_tree(folder / 'files', changed)
    return value


def compose(bundle, folder, value, destination):
    """Publish a complete verified source tree; sparse packages never enter sys.path."""
    target = destination / overlay_name(value)
    if target.exists():
        verify_tree(target, value['files'])
        return target
    stage = destination / ('.source-' + uuid.uuid4().hex)
    stage.mkdir(parents=True)
    try:
        for name, row in value['files'].items():
            origin = folder / 'files' / name
            origin = regular(folder / 'files', name) if origin.exists() else regular(bundle, name)
            verify_file(origin, row)
            output = stage / name
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, output)
        verify_tree(stage, value['files'])
        try:
            stage.rename(target)
        except OSError:
            # Another launcher published the same version first; it may already
            # be running from it, so verify and reuse it, never replace or delete.
            if not target.exists():
                raise
            verify_tree(target, value['files'])
        return target
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def storage(home):
    for name in ('overlay', 'source-overlay', 'updates'):
        path = home / name
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Symlink update directory')


def prepare(value, previous, bundle, current_source, archive, home, base):
    """Validate a single hop, then save independent cumulative sparse overlay."""
    value, previous = manifest(value), manifest(previous)
    delta = value.get('delta') or {}
    if (not compatible(value, base) or delta.get('from') != previous['identity'] or
            (not value['gui'] and value.get('launcher_files_changed', True))):
        raise ValueError('Incompatible source update')
    expected = {n for n, row in value['files'].items() if previous['files'].get(n) != row}
    deleted = set(previous['files']) - set(value['files'])
    if set(delta['files']) != expected or set(delta['deleted']) != deleted:
        raise ValueError('Delta inventory mismatch')
    verify_file(archive, delta)
    home = io_path(home)
    storage(home)
    parent = home / 'overlay'
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / overlay_name(value)
    if target.exists():
        validate_overlay(target, base)
        compose(bundle, target, value, home / 'source-overlay')
        return target
    stage = parent / ('.stage-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        changed = {n: r for n, r in value['files'].items() if base['files'].get(n) != r}
        (stage / 'files').mkdir()
        with zipfile.ZipFile(archive) as zipped:
            rows = zipped.infolist()
            if len(rows) != len(expected) or len(rows) > MAX_FILES:
                raise ValueError('Invalid ZIP entry count')
            seen, expanded = set(), 0
            for item in rows:
                name = safe_name(item.filename)
                mode = item.external_attr >> 16
                if (name in seen or name not in expected or item.is_dir() or item.flag_bits & 1 or
                        stat.S_IFMT(mode) not in (0, stat.S_IFREG) or item.file_size != value['files'][name]['bytes'] or
                        item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise ValueError('Invalid ZIP entry')
                seen.add(name)
                expanded += item.file_size
                if expanded > MAX_SOURCE or item.file_size > MAX_FILE:
                    raise ValueError('ZIP expansion limit')
                # Read no more than the authenticated size, even with misleading headers.
                with zipped.open(item) as source:
                    data = source.read(item.file_size+1)
                if len(data) != item.file_size or sha(data) != value['files'][name]['sha256']:
                    raise ValueError('ZIP file checksum mismatch')
                if name.endswith('.py'):
                    compile(data, name, 'exec')
                if name in changed:
                    output = stage / 'files' / name
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(data)
            if seen != expected:
                raise ValueError('Missing ZIP files')
        for name, row in changed.items():
            if name not in expected:
                origin = regular(current_source, name)
                verify_file(origin, row)
                output = stage / 'files' / name
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, output)
        # Verify all reused base bytes as well; corrupt bases cannot be activated.
        for name, row in value['files'].items():
            if name not in changed:
                verify_file(regular(bundle, name), row)
        verify_tree(stage / 'files', changed)
        atomic_json(stage / 'receipt.json', {'manifest': value, 'sha256': sha(canonical(value))})
        try:
            stage.rename(target)
        except OSError:
            # A concurrent launcher published the same version; reuse its copy.
            if not target.exists():
                raise
            validate_overlay(target, base)
        compose(bundle, target, value, home / 'source-overlay')
        return target
    finally:
        # Only this attempt's stage is ours. A published (verified) overlay may
        # already be shared with another launcher, so it is never removed here.
        shutil.rmtree(stage, ignore_errors=True)


LEASES = {}


def _lock(handle):
    """Non-blocking exclusive lock; raises OSError while another process holds it."""
    if os.name == 'nt':
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def lease(home, name):
    """Mark a source version as running for the life of this process."""
    key = (str(home), name)
    if key not in LEASES:
        folder = home / 'leases'
        folder.mkdir(parents=True, exist_ok=True)
        handle = open(folder / ('%s.%d.lock' % (name, os.getpid())), 'a+b')
        try:
            _lock(handle)
        except BaseException:
            handle.close()
            raise
        LEASES[key] = handle
    return LEASES[key]


def release_leases():
    while LEASES:
        LEASES.popitem()[1].close()


def in_use(home, name):
    """Whether any live launcher holds a lease on this source version."""
    for path in (home / 'leases').glob(name + '.*.lock'):
        try:
            handle = open(path, 'a+b')
        except OSError:
            return True
        try:
            _lock(handle)
        except OSError:
            handle.close()
            return True
        handle.close()
        try:
            path.unlink()  # its owner has exited
        except OSError:
            pass
    return False


def bundle_context():
    if not getattr(sys, 'frozen', False):
        return None
    bundle = io_path(sys._MEIPASS)
    try:
        base = read_manifest(bundle / 'source-base.json')
        return base, bundle / 'engine-source'
    except Exception:
        return None


def activate(home=None, context=None):
    """Fail closed to the last good source or bundled GUI, before package import."""
    global ACTIVE
    ACTIVE = None
    context = context or bundle_context()
    if context is None:
        return None
    base, bundle = context
    home = io_path(home or root())
    try:
        storage(home)
        entries = read_json(home / 'overlay' / 'active.json')['entries']
        if not isinstance(entries, list) or len(entries) > 2:
            return None
    except Exception:
        return None
    for name in entries:
        try:
            if not isinstance(name, str) or not re.fullmatch(r'\d+\.\d+\.\d+-[0-9a-f]{16}', name):
                continue
            folder = home / 'overlay' / name
            value = validate_overlay(folder, base)
            if value['build']['built_at'] <= base['build']['built_at']:
                continue
            # Held before the tree is used so no cleanup can remove it from under us.
            lease(home, name)
            source = compose(bundle, folder, value, home / 'source-overlay')
            if value['gui']:
                if 'freevideo_engine' in sys.modules:
                    return None  # Never create a half-old, half-new GUI.
                # Only the regular source package is redirected; third-party
                # modules, stdlib and bootstrap remain in the frozen runtime.
                sys.meta_path.insert(0, SourceFinder(source))
                sys.path.insert(0, str(source))
                sys.dont_write_bytecode = True
                os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
            ACTIVE = {'manifest': value, 'source': source, 'folder': folder}
            return ACTIVE
        except Exception:
            continue
    return None


class SourceFinder:
    """Explicit package-only precedence also handles older meta-path freezers."""
    def __init__(self, source):
        self.source = source

    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'freevideo_engine':
            return importlib.machinery.PathFinder.find_spec(fullname, [str(self.source)])
        if fullname.startswith('freevideo_engine.'):
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec is None:
                # Deleted modules must not resurrect from the older PYZ.
                raise ModuleNotFoundError(fullname)
            return spec
        return None
