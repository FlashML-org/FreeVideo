"""On-demand download of the dubbing model (Fun-CosyVoice3-0.5B-2512) and its inference runtime.

Nothing here ships in the installer. The catalog (`cosyvoice3_pack.json`) lists every file with its
size and sha256; a file is used only after it matched. Layout under `<model_root>/dubbing/`:
`cosyvoice3/` (the weights), `wheels/` (the runtime wheels as downloaded), `runtime/<id>/` (the wheels
unpacked; put on `sys.path` before `import onnxruntime`) and `licenses/` (the runtime licence texts).
ModelScope has no commit that matches a Hugging Face one, so every source is accepted only when the
size and SHA-256 in the catalog match, and the file names are the same on all of them. The runtime
wheels are the PyPI files, fetched from PyPI or its domestic mirror by the same rule. onnxruntime also
requires packaging and sympy; both come with torch in the unified environment, so they are not listed.
The wheels are chosen for the Python of the service process, which is the pinned 3.12 of the product's
own environment. A user's own ComfyUI Python of another version would fail the import in the worker,
which reports it as a general failure.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

from .. import disk_space

FAMILY = 'image-models'
RESERVE = 256 * 2**20
LEDGER = 'download-verified.json'
NAME = 'cosyvoice3'
COMPLETE = 'complete.json'
MAX_UNPACKED = 256 * 2**20
LICENSES = 'RUNTIME-LICENSES.txt'
# The two PyPI hosts of the `pypi` download family; a wheel's `path` is the same on both.
PYPI_FILES = {'official': 'https://files.pythonhosted.org/packages/', 'tuna': 'https://pypi.tuna.tsinghua.edu.cn/packages/'}
MODULES = (('onnxruntime', '__init__.py'), ('flatbuffers', '__init__.py'), ('google', 'protobuf', '__init__.py'))


class PackError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def catalog():
    """The file list shipped beside this file; a frozen launcher's bundled copy answers when it is gone."""
    name = 'cosyvoice3_pack.json'
    paths = [Path(__file__).with_name(name)]
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        paths.append(Path(bundle) / 'engine-source' / 'freevideo_engine' / 'dubbing' / name)
    for path in paths:
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            missing = error
    raise missing


def rows(spec=None):
    return list((spec or catalog())['files'])


def base(root=None):
    if root is None:
        from ..paths import model_root
        root = model_root()
    return Path(root) / 'dubbing'


def directory(root=None):
    return base(root) / NAME


def _ledger(folder):
    try:
        value = json.loads((folder / LEDGER).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _intact(folder, row, ledger):
    """Size and the stamp recorded after the last hash match; no hashing here."""
    from .. import provision
    path = folder / row['file']
    try:
        if not path.is_file() or path.stat().st_size != row['bytes']:
            return False
        prior = ledger.get(str(path))
        return isinstance(prior, dict) and all(
            prior.get(key) == value for key, value in provision.file_identity(path, row).items())
    except OSError:
        return False


def wheel_rows(spec=None):
    """This machine's runtime wheels, or None (other platform or Python, or none listed)."""
    from ..subtitle_pack import platform_key
    runtime = (spec or catalog()).get('runtime') or {}
    if runtime.get('python') != 'cp%d%d' % sys.version_info[:2]:
        return None
    return list((runtime.get('wheels') or {}).get(platform_key()) or []) or None


def runtime_id(wheels):
    return hashlib.sha256('\n'.join(row['sha256'] for row in wheels).encode('ascii')).hexdigest()[:12]


def runtime_folder(root=None, spec=None):
    wheels = wheel_rows(spec)
    return base(root) / 'runtime' / runtime_id(wheels) if wheels else None


def _unpacked(root=None, spec=None):
    folder = runtime_folder(root, spec)
    if folder is None:
        return False
    try:
        receipt = json.loads((folder / COMPLETE).read_text(encoding='utf-8'))
        return receipt.get('id') == folder.name and all(folder.joinpath(*parts).is_file() for parts in MODULES)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def runtime_path(root=None):
    """The folder to put on sys.path for onnxruntime, or None while it is not unpacked."""
    try:
        return str(runtime_folder(root)) if _unpacked(root) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def activate(path):
    """Put an unpacked runtime on sys.path (once); Windows also needs its DLL folders."""
    if not path:
        return False
    folder = Path(path)
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
    if sys.platform == 'win32' and hasattr(os, 'add_dll_directory'):
        for item in {folder} | {found.parent for found in folder.rglob('*.dll')}:
            os.add_dll_directory(str(item))
    return True


def ready(root=None, spec=None):
    """Cheap and safe on a UI thread: sizes and the identity stamps of the last content check."""
    try:
        spec = spec or catalog()
        folder = directory(root)
        ledger = _ledger(folder)
        return _unpacked(root, spec) and all(_intact(folder, row, ledger) for row in rows(spec))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def settle(root=None, spec=None):
    """Same answer, but a file that moved without a stamp (a copied folder) is hashed here once.
    For request handlers, never for a UI thread or under a lock."""
    if ready(root, spec):
        return True
    try:
        from .. import provision
        from ..monitoring import save
        spec = spec or catalog()
        folder = directory(root)
        if not _unpacked(root, spec) or not folder.is_dir():
            return False
        ledger = _ledger(folder)
        found = all(provision.verified(folder / row['file'], row, ledger) for row in rows(spec))
        if found:
            save(folder / LEDGER, ledger)
        return found
    except (OSError, ValueError, KeyError, TypeError):
        return False


def missing_bytes(root=None, spec=None):
    spec = spec or catalog()
    folder = directory(root)
    ledger = _ledger(folder)
    weights = sum(row['bytes'] for row in rows(spec) if not _intact(folder, row, ledger))
    return weights + (0 if _unpacked(root, spec) else sum(row['bytes'] for row in wheel_rows(spec) or ()))


def status(root=None):
    """What a page may show: the size of the download and whether the model is in place."""
    spec = catalog()
    wheels = wheel_rows(spec)
    total = sum(row['bytes'] for row in rows(spec)) + sum(row['bytes'] for row in wheels or ())
    return dict(name=NAME, license=spec.get('license'), bytes=missing_bytes(root, spec), total=total,
                ready=ready(root, spec), supported=wheels is not None,
                published=bool(spec.get('modelscope') or spec.get('huggingface')))


def remote(spec, row):
    """One catalog file with every source FreeVideo may fetch it from (ModelScope, Hugging Face, mirror)."""
    from ..network import source_url
    sources = []
    mirror = spec.get('modelscope')
    if mirror:
        sources.append(dict(id='modelscope', url=source_url(FAMILY, 'modelscope') + '/models/%s/resolve/%s/%s' % (
            mirror['repo'], mirror.get('revision', 'master'), row['file'])))
    hub = spec.get('huggingface')
    if hub:
        path = '/%s/resolve/%s/%s' % (hub['repo'], hub.get('revision', 'main'), row['file'])
        sources.append(dict(id='official', url=source_url('models', 'official') + path))
        sources.append(dict(id='hf-mirror', url=source_url('models', 'hf-mirror') + path))
    return dict(repo=(mirror or hub)['repo'], revision=(mirror or hub).get('revision', 'main'), file=row['file'],
                family='image', sources=sources)


def runtime_plan(plan):
    """The plan for the runtime wheels: the `image-models` list holds the PyPI hosts in the order the
    plan prefers them (PyPI, or its domestic mirror)."""
    from ..network import ordered
    names = [name for name in ordered(plan, 'pypi') if name in PYPI_FILES] or ['official']
    return dict(plan, sources=dict(plan.get('sources', {}), **{FAMILY: [{'id': name} for name in names]}))


def runtime_remote(row):
    return dict(repo='pypi/' + row['name'], revision=row['version'], file=row['file'], family='image',
                sources=[dict(id=name, url=PYPI_FILES[name] + row['path']) for name in PYPI_FILES])


def _unpack(root, wheels, check):
    """Extract the verified wheels into runtime/<id>/ and drop older runtimes. A file that two wheels
    both carry, an unsafe name, or too much data fails the whole unpack."""
    folder = runtime_folder(root)
    parent = folder.parent
    parent.mkdir(parents=True, exist_ok=True)
    work = parent / ('.unpack-' + folder.name)
    shutil.rmtree(work, ignore_errors=True)
    total = 0
    try:
        for row in wheels:
            with zipfile.ZipFile(wheels_folder(root) / row['file']) as archive:
                for item in archive.infolist():
                    check()
                    name = item.filename
                    parts = Path(name).parts
                    if (name.startswith('/') or '\\' in name or ':' in name or not parts or '..' in parts
                            or (item.external_attr >> 16) & 0o170000 == 0o120000):
                        raise PackError('failed')
                    total += item.file_size
                    if total > MAX_UNPACKED:
                        raise PackError('failed')
                    target = work.joinpath(*parts)
                    if item.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    if target.exists():
                        raise PackError('failed')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as source, target.open('wb') as stream:
                        shutil.copyfileobj(source, stream, 1024 * 1024)
        if not all(work.joinpath(*parts).is_file() for parts in MODULES):
            raise PackError('failed')
        write_licenses(root, work, wheels)
        (work / COMPLETE).write_text(json.dumps(dict(id=folder.name, bytes=total)), encoding='utf-8')
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(work, folder)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for other in parent.iterdir():
        if other.is_dir() and other != folder and not other.name.startswith('.unpack-'):
            shutil.rmtree(other, ignore_errors=True)


def write_licenses(root, unpacked, wheels):
    """The runtime's licence texts, with the pack and not with the installer: the name, version and
    licence of every wheel, then the licence files its metadata carries."""
    parts = ['Runtime libraries of the dubbing model pack. They are downloaded when the pack is installed.\n']
    for row in wheels:
        parts.append('\n%s\n%s %s, %s\n%s\n' % ('=' * 72, row['name'], row['version'], row['license'], '=' * 72))
        for info in sorted(unpacked.glob('%s-%s.dist-info' % (row['name'], row['version']))):
            for path in sorted(info.rglob('*')):
                if path.is_file() and path.suffix in ('', '.txt', '.md') and any(
                        word in path.name.upper() for word in ('LICENSE', 'NOTICE', 'COPYING')):
                    parts.append('\n--- %s ---\n%s\n' % (path.name, path.read_text(encoding='utf-8', errors='replace')))
    folder = base(root) / 'licenses'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / LICENSES).write_text(''.join(parts), encoding='utf-8')


def wheels_folder(root=None):
    return base(root) / 'wheels'


def install(root=None, progress=None, check=None):
    """Download and verify the missing runtime wheels and model files, then unpack the runtime.
    Resumable; a file is kept only after its hash matched."""
    from .. import network, provision
    from ..adaln_assets import _download_plan
    from ..monitoring import save
    spec = catalog()
    progress = progress or (lambda value: None)
    check = check or (lambda: None)
    if not (spec.get('modelscope') or spec.get('huggingface')):
        raise PackError('not_published')
    wheels = wheel_rows(spec)
    if wheels is None:
        raise PackError('unsupported')
    folder = directory(root)
    folder.mkdir(parents=True, exist_ok=True)
    cache = wheels_folder(root)
    cache.mkdir(parents=True, exist_ok=True)
    ledger = _ledger(folder)
    cached = _ledger(cache)
    # A file without a stamp (a copied folder) is hashed once here and stamped.
    todo = [row for row in rows(spec) if not provision.verified(folder / row['file'], row, ledger)]
    save(folder / LEDGER, ledger)
    fetch = [] if _unpacked(root, spec) else [row for row in wheels if not provision.verified(cache / row['file'], row, cached)]
    save(cache / LEDGER, cached)
    total = sum(row['bytes'] for row in todo) + sum(row['bytes'] for row in fetch)
    retained = 0
    for row, where in [(row, folder) for row in todo] + [(row, cache) for row in fetch]:
        partial = (where / row['file']).with_suffix((Path(row['file']).suffix) + '.partial')
        if partial.is_file() and partial.stat().st_size < row['bytes']:
            retained += partial.stat().st_size
    if total and disk_space.free_bytes(folder) < total - retained + RESERVE:
        raise PackError('disk_space')
    plan = _download_plan(root)
    # The first dubbing download follows the same hosts as the image model; installs whose plan has
    # no entry for the family try ModelScope first, then the two Hugging Face hosts.
    sources = plan['sources'] = dict(plan.get('sources', {}))
    if not sources.get(FAMILY):
        sources[FAMILY] = [{'id': 'modelscope'}] + [
            dict(item) for item in sources.get('models', []) if item['id'] in ('official', 'hf-mirror')]
        if len(sources[FAMILY]) == 1:
            sources[FAMILY] += [{'id': 'official'}, {'id': 'hf-mirror'}]
    plan.update(quiet=True, resource_check=check)
    wheel_plan = runtime_plan(plan)
    done = 0

    def transfer(row, path, remote_row, ledger_file, stamps, plan=plan):
        nonlocal done
        check()
        path.parent.mkdir(parents=True, exist_ok=True)

        def moved(current, size, speed, **kwargs):
            progress(dict(phase='download', done=done + current, total=total))
        network.download(network.model_urls(plan, remote_row), path, row['sha256'], moved,
                         network=plan, size=row['bytes'], category=FAMILY, keep_partial=True,
                         stall_seconds=30, slow_seconds=15, low_speed_limit=64 * 1024)
        stamps[str(path)] = provision.file_identity(path, row)
        save(ledger_file, stamps)
        done += row['bytes']
    try:
        for row in fetch:
            transfer(row, cache / row['file'], runtime_remote(row), cache / LEDGER, cached, plan=wheel_plan)
        if fetch or not _unpacked(root, spec):
            progress(dict(phase='unpack', done=done, total=total))
            _unpack(root, wheels, check)
        for row in todo:
            transfer(row, folder / row['file'], remote(spec, row), folder / LEDGER, ledger)
    except PackError:
        raise
    except InterruptedError:
        raise
    except ValueError as error:
        raise PackError('disk_space' if str(error) == 'disk_space' else 'download_failed') from error
    except OSError as error:
        raise PackError('disk_space' if getattr(error, 'errno', None) == 28 else 'download_failed') from error
    except Exception as error:
        raise PackError('download_failed') from error
