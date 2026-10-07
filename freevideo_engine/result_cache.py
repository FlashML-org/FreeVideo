"""Local references to completed videos; no copied media or GPU initialization."""
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import threading
import time
from collections import OrderedDict

from .monitoring import save
from .storage import fingerprint

CHECK_SECONDS = 2.
_inspection_lock = threading.Lock()
_digest_lock = threading.Lock()
_digests = OrderedDict()


class Inspection:
    """Cooperative I/O budget; a slow OS read must not block generation."""
    def __init__(self, timeout=CHECK_SECONDS):
        self.started = time.monotonic()
        self.deadline = self.started + timeout
        self.cancelled = threading.Event()
        self.phase = 'starting'
        self.files = 0
        self.bytes_read = 0
        self.reused_hashes = 0
        self.phases = {}

    def check(self):
        if self.cancelled.is_set() or time.monotonic() >= self.deadline:
            raise TimeoutError('Saved-result inspection exceeded its time budget')

    def measure(self, name, operation):
        self.check()
        self.phase = name
        started = time.monotonic()
        try:
            return operation()
        finally:
            self.phases[name] = time.monotonic() - started

    def report(self, status):
        return dict(status=status, phase=self.phase, seconds=time.monotonic()-self.started,
                    files=self.files, bytes_read=self.bytes_read, reused_hashes=self.reused_hashes,
                    phase_seconds=dict(self.phases))


def inspect_bounded(operation, *, interrupted=None, timeout=CHECK_SECONDS):
    """At most one bounded, daemon I/O task; no growing queue of stuck reads.

    The caller continues generation on timeout. Cancellation is checked on the
    calling thread, and the inspector stops between files/read chunks. A single
    stuck network/filesystem call cannot hold the Comfy executor or its shutdown.
    """
    check = Inspection(timeout)
    if not _inspection_lock.acquire(blocking=False):
        return None, check.report('busy')
    done, result = threading.Event(), {}
    def run():
        try:
            result['value'] = operation(check)
            check.check()
            result['status'] = 'complete'
        except TimeoutError:
            result['status'] = 'timeout'
        except Exception:
            result['status'] = 'unavailable'
        finally:
            _inspection_lock.release()
            done.set()
    try:
        thread = threading.Thread(target=run, name='FreeVideo saved-result check', daemon=True)
        thread.start()
    except Exception:
        _inspection_lock.release()
        return None, check.report('unavailable')
    try:
        while not done.is_set():
            if interrupted:
                interrupted()
            remaining = check.deadline - time.monotonic()
            if remaining <= 0:
                return None, check.report('timeout')
            done.wait(min(.05, remaining))
        if interrupted:
            interrupted()
        status = result.get('status', 'unavailable')
        return result.get('value') if status == 'complete' else None, check.report(status)
    finally:
        check.cancelled.set()


def _key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _file(path, *, content=False, inspection=None):
    if inspection:
        inspection.check()
    path = Path(path).absolute()
    before = fingerprint(path)
    if not path.is_file() or before.get('change_time_ns', 0) is None:
        raise ValueError('File identity is unavailable')
    if inspection:
        inspection.files += 1
    if not content:
        return before
    # Content is needed for input media/LoRAs and completed output, not every
    # Python source/configuration file. Unchanged files can reuse their SHA.
    identity = (str(path), tuple(sorted(before.items())))
    with _digest_lock:
        if identity in _digests:
            value = _digests[identity]
            _digests.move_to_end(identity)
            if inspection:
                inspection.reused_hashes += 1
            return value
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            if inspection:
                inspection.check()
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            if inspection:
                inspection.bytes_read += len(block)
    if before != fingerprint(path):
        raise ValueError('File changed during cache inspection')
    value = digest.hexdigest()
    with _digest_lock:
        _digests[identity] = value
        while len(_digests) > 128:
            _digests.popitem(last=False)
    return value


def _tree(path, *, code=False, inspection=None):
    if inspection:
        inspection.check()
    path = Path(path).resolve(strict=True)
    if path.is_file():
        return _file(path, inspection=inspection)
    ignored = {'__pycache__', 'node_modules', 'envs', 'venv', 'input', 'output', 'user', 'custom_nodes'}
    suffixes = ('.py', '.json', '.yaml', '.yml') if code else ('.py', '.json', '.yaml', '.yml', '.safetensors', '.bin', '.pt')
    rows = {}
    visited = set()
    def unreadable(error):
        raise error
    for directory, folders, files in os.walk(path, followlinks=True, onerror=unreadable):
        if inspection:
            inspection.check()
        resolved = Path(directory).resolve()
        if resolved in visited:
            raise ValueError('Recursive model/code directory')
        visited.add(resolved)
        folders[:] = sorted(p for p in folders if not p.startswith('.') and p not in ignored)
        for name in sorted(files):
            file = Path(directory) / name
            if file.suffix in suffixes:
                # Detect edits/replacements through file ID, size, mtime and
                # ChangeTime on Windows. Reading thousands of source/metadata
                # payloads on every request made this optional cache expensive.
                rows[file.relative_to(path).as_posix()] = _file(file, inspection=inspection)
    if not rows:
        raise ValueError('No model/code identity available')
    return rows


def request_key(prompt, seed, canvas, sampling_plan, extra, machine, resources, source, environment, *, inspection=None,
                model='h3'):
    """Fail closed to ordinary generation if an installation cannot be identified.

    Automatic placement is a policy, not a fixed free-memory reading. Preserve
    its code, user settings and GPU identity; changing live free RAM alone must
    not prevent reusing an already finished video. Force regeneration bypasses
    lookup when measuring a new placement or fresh performance.
    """
    def phase(name, operation):
        return inspection.measure(name, operation) if inspection else operation()
    def file(path, content=False):
        return _file(path, content=content, inspection=inspection)
    def tree(path, code=False):
        return _tree(path, code=code, inspection=inspection)
    try:
        root = Path(machine['root'])
        code = phase('code', lambda: {'engine': tree(Path(source) / 'freevideo_engine', code=True),
                'vdn': tree(machine['vdn_root'], code=True),
                'encoder': tree(Path(machine['comfy_root']) / 'comfy', code=True)})
        if model == 'prism':
            # Prism's own weights; the H3 files play no part in its result.
            record = machine['models']['prism']
            models = phase('models', lambda: {'prism': tree(record['root']), 'variant': record['variant'],
                                              'revision': record.get('revision')})
        else:
            models = phase('models', lambda: {key: tree(machine[key]) for key in ('cache', 'base', 'checkpoint')})
            models['encoder'] = file(Path(machine['encoder_model_root']) / 'text_encoders' / machine['encoder'])
            models['paths'] = file(machine['model_paths'])
        if sampling_plan.get('upscaler_sha256'):
            from .two_pass import UPSCALER
            models['upscaler'] = file(Path(machine['model_root']) / 'latent_upscaler' / Path(UPSCALER['file']).name)
        native = machine.get('device_backend') == 'mps'
        if native:
            identity = machine.get('device_identity')
            if not isinstance(identity, dict) or identity.get('backend') != 'mps' or not identity.get('name'):
                raise ValueError('Native device identity is unavailable')
            # Mac placement has its own unified-memory policy. NVIDIA's
            # compatibility levels do not apply; machine below retains the
            # actual Apple device identity, without a fabricated GPU UUID.
            settings = dict(resources=resources, macos=platform.mac_ver()[0])
        else:
            from .compatibility import Store
            compatibility = phase('settings', lambda: Store(root).status({'gpu_uuid': machine['gpu_uuid'], 'system': platform.system()}))
            settings = dict(resources=resources, compatibility=compatibility['level'])
        tuning = root / 'tuning' / 'state.json'
        if tuning.exists():
            saved = json.loads(tuning.read_text(encoding='utf-8'))
            settings['tuning'] = {k: saved.get(k) for k in ('schema', 'enabled', 'identity', 'profiles')}
        def dependencies():
            packages, runtimes = {}, {}
            for name in ('python', 'comfy_python'):
                executable = Path(machine[name]).absolute()
                if executable not in runtimes:
                    runtime = file(executable)  # Keep the virtualenv prefix below.
                    prefix = executable.parent.parent if executable.parent.name in ('bin', 'Scripts') else executable.parent
                    sites = [prefix / 'Lib/site-packages', *prefix.glob('lib/python*/site-packages')]
                    metadata = [path for site in sites for path in site.glob('*.dist-info/METADATA')]
                    if not metadata:
                        raise ValueError('Runtime dependency identity is unavailable')
                    runtimes[executable] = dict(executable=runtime,
                        metadata={str(path): file(path) for path in sorted(metadata)})
                packages[name] = runtimes[executable]
            return packages
        packages = phase('packages', dependencies)
        if inspection:
            inspection.check()
            inspection.phase = 'inputs'
        input_started = time.monotonic()
        media = dict(extra.get('media') or {})
        for name in ('first', 'last'):
            if media.get(name):
                media[name] = file(media[name], content=True)
        for name in ('references', 'loras'):
            media[name] = [{**{k: v for k, v in row.items() if k != 'path'},
                            'sha256': file(row['path'], content=True)} for row in media.get(name, [])]
        conditioning = file(extra['conditioning'], content=True) if extra.get('conditioning') else None
        if inspection:
            inspection.phases['inputs'] = time.monotonic() - input_started
            inspection.check()
        ignored = {'FREEVIDEO_RESIDENT_SESSION', 'FREEVIDEO_RUNTIME_LOCK_FD', 'FREEVIDEO_RUNTIME_LOCK_HANDLE'}
        prefixes = ('FREEVIDEO_', 'CUDA_', 'NVIDIA_', 'PYTORCH_', 'TORCH_', 'TRITON_')
        if native:
            prefixes += ('MLX_', 'MTL_', 'METAL_')
        compute_env = {k: v for k, v in environment.items() if k not in ignored and k.startswith(prefixes)}
        value = dict(schema=2, prompt=prompt, seed=seed, canvas=canvas, sampling_plan=sampling_plan,
                     media=media, conditioning=conditioning, machine=machine, settings=settings,
                     models=models, code=code, packages=packages, environment=compute_env)
        if model != 'h3':
            value['model'] = model  # H3 keys stay as before, so saved H3 results still match.
        return _key(value)
    except TimeoutError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
        return None


class ResultCache:
    def __init__(self, output_directory):
        self.root = Path(output_directory).resolve()
        self.index = self.root / 'FreeVideo' / '.result-cache'

    def lookup(self, key, *, inspection=None):
        if key is None:
            return None
        try:
            from .comfy_library import _video, _report
            row = json.loads((self.index / (key + '.json')).read_text(encoding='utf-8'))
            if row['schema'] != 1 or row['key'] != key:
                return None
            output = _video(self.root, row['id'])
            for suffix in ('.mp4', '.request.json', '.engine.json'):
                file = output.with_suffix(suffix)
                if file.stat().st_size != row['files'][suffix]['bytes'] or _file(file, content=True, inspection=inspection) != row['files'][suffix]['sha256']:
                    return None
            _report(output)
            if json.loads(output.with_suffix('.engine.json').read_text(encoding='utf-8')).get('success') is not True:
                return None
            return output
        except TimeoutError:
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None

    def remember(self, key, output, *, inspection=None):
        if key is None:
            return False
        try:
            from .comfy_library import _video, _report
            identity = output.parent.relative_to(self.root / 'FreeVideo').as_posix()
            if _video(self.root, identity) != output.resolve():
                return False
            _report(output)
            engine = json.loads(output.with_suffix('.engine.json').read_text(encoding='utf-8'))
            if engine.get('success') is not True:
                return False
            files = {}
            for suffix in ('.mp4', '.request.json', '.engine.json'):
                file = output.with_suffix(suffix)
                if not file.stat().st_size:
                    return False
                files[suffix] = dict(bytes=file.stat().st_size, sha256=_file(file, content=True, inspection=inspection))
            if inspection:
                inspection.check()
            save(self.index / (key + '.json'), dict(schema=1, key=key, id=identity, files=files))
            return True
        except TimeoutError:
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return False  # A full disk/unavailable index cannot lose a completed video.
