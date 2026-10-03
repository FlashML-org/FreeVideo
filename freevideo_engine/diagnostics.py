"""Collect bounded local support files, even when setup or CUDA cannot start.

Python 3.9 stdlib only. Nothing is uploaded; original reports and media are kept.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile
import platform

from . import __version__
from .bootstrap import DEFAULT_ROOT
from .system import windows, venv_python, system_memory, nvidia_smi, bootstrap_root


FILE_LIMIT = 512 * 1024
TOTAL_LIMIT = 12 * 1024 * 1024
FILE_COUNT = 256
NAMES = {'report.json', 'report.csv', 'inventory.json', 'case.json', 'status.json',
         'plan.json', 'machine.before.json', 'comparison.json', 'profile.json',
         'result.json', 'gpu.csv', 'ram.jsonl', 'network.jsonl', 'optimization.json', 'storage.json', 'kernel-capabilities.json'}
SENSITIVE = re.compile(r'(?:^|[_-])(?:token|password|secret|credential|api[_-]?key)(?:$|[_-])', re.I)
# Path-bearing fields are retained only as installation-relative names (when a
# known report root is available) or as a basename under ``<PATH>``.  Reports
# are often collected from an installation whose model/config paths live
# elsewhere, so replacing only the current installation root is insufficient.
PATH_KEYS = frozenset(('base', 'checkpoint', 'encoder', 'output', 'directory',
                       'retained', 'sample_csv', 'path', 'root', 'command'))
ABSOLUTE_PATH = re.compile(
    # The preceding character guard prevents URL slashes from being treated
    # as local paths while still matching a drive path after prose such as
    # ``at C:\\Users\\...`` or a quoted traceback location.
    r'''(?<![A-Za-z0-9_:/])(?:(?:[A-Za-z]:[\\/])|(?:\\\\[^\\/\s"'<>]+[\\/])|/(?!/))[^"'<>;\r\n,\)\]}]*''')


class Redactor:
    def __init__(self, replacements=()):
        self.replacements = sorted(((str(a), b) for a, b in replacements if len(str(a)) > 1),
                                   key=lambda pair: len(pair[0]), reverse=True)
        self.prompts = set()
        self.secrets = set()

    def structured(self, value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key.lower() in ('prompt', 'negative_prompt', 'prompt_text'):
                    if isinstance(item, str) and item:
                        self.prompts.add(item)
                    result[key] = '<PROMPT REMOVED>'
                elif SENSITIVE.search(key) or key.lower() in (
                        'gpu_uuid', 'machine_id', 'device_uuid', 'hostname', 'username', 'computer_name'):
                    if isinstance(item, str) and len(item) >= 8:
                        self.secrets.add(item)
                    result[key] = '<REDACTED>'
                elif self._path_key(key) and isinstance(item, str):
                    result[key] = self.path(item)
                else:
                    result[key] = self.structured(item)
            return result
        if isinstance(value, list):
            return [self.structured(item) for item in value]
        return value

    def text(self, value):
        value = str(value)
        for prompt in sorted(self.prompts, key=len, reverse=True):
            value = value.replace(prompt, '<PROMPT REMOVED>')
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, '<REDACTED>')
        for source, target in self.replacements:
            value = value.replace(source, target)
        value = re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@', r'\1<REDACTED>@', value)
        value = re.sub(r'\bhf_[A-Za-z0-9_-]{8,}\b', '<REDACTED>', value)
        value = re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+', r'\1<REDACTED>', value)
        value = re.sub(r'(?i)(\b[\w-]{0,64}(?:token|password|secret|api[_-]?key)\s*[=:]\s*)[^\s,;&]+',
                       r'\1<REDACTED>', value)
        # Keep diagnostics useful without exposing arbitrary user directories.
        # The callback returns only a stable placeholder and the final path
        # component, which is enough to identify a binary/config/log while
        # removing usernames, drive letters, mounts and parent directories.
        value = ABSOLUTE_PATH.sub(lambda match: self._path_placeholder(match.group(0)), value)
        return value

    @staticmethod
    def _path_key(key):
        lowered = str(key).lower()
        return lowered in PATH_KEYS or lowered.endswith(('_path', '_paths', '_dir', '_root', '_report'))

    @staticmethod
    def _path_placeholder(value):
        normalized = str(value).rstrip('\\/')
        name = re.split(r'[\\/]', normalized)[-1]
        return '<PATH>/' + name if name else '<PATH>'

    def path(self, value):
        """Return a slash-normalized relative path safe for a shared report."""
        cleaned = self.text(value)
        # ``text`` already handles arbitrary absolute paths.  Keep known
        # placeholders such as <RUN>/video.mp4 and ordinary relative paths.
        return cleaned.replace('\\', '/')

    def data(self, raw, suffix):
        # Windows PowerShell 5.1 redirects native output as UTF-16 by default.
        encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
        value = raw.decode(encoding, errors='replace')
        def clean(item):
            if isinstance(item, dict):
                return {self.text(key): (self.path(v) if self._path_key(key) and isinstance(v, str)
                                         else clean(v)) for key, v in item.items()}
            if isinstance(item, list):
                return [clean(v) for v in item]
            return self.text(item) if isinstance(item, str) else item
        if suffix in ('.json', '.jsonl'):
            try:
                if suffix == '.json':
                    value = json.dumps(clean(self.structured(json.loads(value))), ensure_ascii=False, indent=2) + '\n'
                else:
                    rows = [self.structured(json.loads(line)) for line in value.splitlines()]
                    value = '\n'.join(json.dumps(clean(row)) for row in rows) + '\n'
                return value.encode('utf-8')
            except (ValueError, TypeError):
                # Keep corrupt failure reports readable; do not discard the evidence.
                value = re.sub(r'(?i)("(?:negative_)?prompt"\s*:\s*)"(?:[^"\\]|\\.)*"',
                               r'\1"<PROMPT REMOVED>"', value)
        return self.text(value).encode('utf-8')


def read_bounded(path, limit=FILE_LIMIT):
    if windows():
        from .win32 import open_regular
        descriptor = open_regular(path)
    else:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Not a regular file')
        if info.st_size <= limit:
            return stream.read(limit), False
        # Logs/telemetry retain both startup and the final failure, with a visible gap.
        head = stream.read(limit // 2)
        stream.seek(max(limit // 2, info.st_size - limit // 2))
        return head + b'\n<TRUNCATED: middle omitted>\n' + stream.read(limit // 2), True


def read_complete(path):
    """Read one regular diagnostic file in full for an explicit user export."""
    if windows():
        from .win32 import open_regular
        descriptor = open_regular(path)
    else:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Not a regular file')
        return stream.read(), False


def is_link(path):
    try:
        info = path.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)
    except OSError:
        return True


def report_files(directory):
    for current, directories, files in os.walk(directory, followlinks=False):
        directories[:] = sorted(name for name in directories
                                if not name.startswith('.') and not name.endswith('.artifacts')
                                and not is_link(Path(current) / name)
                                and name not in ('models', 'envs', 'vendor', 'downloads', 'compiler', 'kernel-cache'))
        for name in sorted(files, key=lambda n: (not n.endswith('.json'), n)):
            # Include every small diagnostic sidecar written by a generation
            # worker.  Media, tensors, model weights and prompt.txt stay out
            # of the bundle through the directory and suffix filters above.
            if name in NAMES or name.endswith(('.log', '.jsonl', '.request.json', '.debug.json',
                                               '.encoding.json', '.engine.json', '.memory.json',
                                               '.gpu.json', '.sampling-memory.json', '.comfy-request.json',
                                               'comfy-request.json')):
                yield Path(current) / name


def latest(root, category):
    parent = root / category
    if not parent.is_dir() or is_link(parent):
        return None
    directories = [p for p in parent.iterdir() if p.is_dir() and not is_link(p)]
    return max(directories, key=lambda p: p.name) if directories else None


def collect(root, config, run, output, *, complete=False, extra_files=(), notes=''):
    root, config = root.expanduser().resolve(), config.expanduser().absolute()
    if run is not None and (not run.is_dir() or is_link(run)):
        raise ValueError('--run must be a real report directory')
    if output.suffix.lower() != '.zip':
        raise ValueError('--out must name a new .zip file')
    # Refuse overwriting evidence before invoking any discovery commands.
    output.parent.mkdir(parents=True, exist_ok=True)
    setup = latest(root, 'setup-runs')
    selected = run.resolve() if run else latest(root, 'test-runs')
    optimized = latest(root, 'optimization-runs')
    redactor = Redactor([(Path.home(), '<HOME>'), (root, '<INSTALL>'),
                         *((p, '<' + label.upper() + '>') for label, p in [('setup', setup), ('run', selected), ('optimization', optimized)] if p)])
    manifest = {'schema_version': 1, 'engine_version': __version__, 'created_epoch': time.time(),
                'files': [], 'omitted': [], 'errors': [], 'payload_bytes': 0,
                'scope': ('Complete eligible local diagnostics. ' if complete else 'Local bounded diagnostics. ') +
                         'No media, tensors, model weights, environment dump or automatic upload. '
                         'Archive entries are relative labels. Absolute paths, prompts and credentials are redacted; review before sharing. ' +
                         ('No eligible report files are truncated; originals are unchanged.' if complete else
                          'Truncated files contain a marker; originals are unchanged.')}
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        def add(name, raw, truncated=False):
            data = redactor.data(raw, Path(name).suffix)
            if (not complete and (len(manifest['files']) >= FILE_COUNT or
                                  manifest['payload_bytes'] + len(data) > TOTAL_LIMIT)):
                manifest['omitted'].append({'file': name, 'reason': 'Diagnostic size/file limit reached'})
                return
            archive.writestr(name, data)
            manifest['files'].append({'file': name, 'bytes': len(data), 'truncated': truncated,
                                      'sha256': hashlib.sha256(data).hexdigest()})
            manifest['payload_bytes'] += len(data)

        def add_path(name, path):
            try:
                data, truncated = read_complete(path) if complete else read_bounded(path)
                add(name, data, truncated)
            except (OSError, ValueError) as error:
                manifest['errors'].append({'file': name, 'error': redactor.text(str(error))})

        machine = {}
        try:
            raw, truncated = read_complete(config) if complete else read_bounded(config)
            if not truncated:
                machine = json.loads(raw)
                if not isinstance(machine, dict):
                    machine = {}
            add('installation/machine.json', raw, truncated)
        except (OSError, ValueError) as error:
            manifest['errors'].append({'file': 'machine.json', 'error': redactor.text(str(error))})
            if config.is_file() and not is_link(config):
                add_path('installation/machine.json', config)
        tuning_state = root / 'tuning/state.json'
        kernels = root / 'kernel-capabilities.json'
        if kernels.is_file():
            add_path('installation/kernel-capabilities.json', kernels)
        if tuning_state.is_file():
            add_path('installation/tuning.json', tuning_state)
        download_ownership = root / '.freevideo/downloaded-models.json'
        if download_ownership.is_file():
            add_path('installation/downloaded-models.json', download_ownership)
        for label, directory in [('setup', setup), ('run', selected), ('optimization', optimized)]:
            if directory:
                for path in report_files(directory):
                    if not complete and (len(manifest['files']) >= FILE_COUNT or manifest['payload_bytes'] >= TOTAL_LIMIT):
                        manifest['omitted'].append({'file': label + '/*', 'reason': 'Collection limit reached'})
                        break
                    add_path(label + '/' + path.relative_to(directory).as_posix(), path)
        # No CUDA initialization or ready runtime is needed. Failures become evidence.
        try:
            smi = nvidia_smi()
        except RuntimeError:
            smi = 'nvidia-smi'
        git = machine.get('git') or 'git'
        commands = [('nvidia-smi', [smi, '-q']),
                    ('git-head', [git, '-C', str(Path(__file__).resolve().parent.parent), 'rev-parse', 'HEAD']),
                    ('git-status', [git, '-C', str(Path(__file__).resolve().parent.parent), 'status', '--porcelain'])]
        pythons = {str(p) for key in ('python', 'comfy_python') if (p := machine.get(key))}
        pythons.update(str(p) for name in ('unified', 'engine', 'encoder')
                       if (p := venv_python(root / 'envs' / name)).is_file())
        code = 'import importlib.metadata as m,json,sys; print(json.dumps({"python":sys.version,"packages":{d.metadata["Name"]:d.version for d in m.distributions()}}))'
        for index, python in enumerate(sorted(pythons)):
            commands.append(('packages-' + str(index), [python, '-B', '-c', code]))
        for name, command in commands:
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace')
                value = {'returncode': result.returncode,
                         'stdout': result.stdout if complete else result.stdout[-FILE_LIMIT:],
                         'stderr': result.stderr if complete else result.stderr[-FILE_LIMIT:]}
                if result.returncode:
                    manifest['errors'].append({'command': name, 'returncode': result.returncode})
            except (OSError, subprocess.TimeoutExpired) as error:
                value = {'error': str(error)}
                manifest['errors'].append({'command': name, 'error': redactor.text(str(error))})
            add('current/' + name + '.json', json.dumps(value).encode())
        bootstrap = bootstrap_root(root)
        if not is_link(bootstrap) and (bootstrap / 'bootstrap.log').is_file():
            add_path('bootstrap/bootstrap.log', bootstrap / 'bootstrap.log')
        if windows():
            from .desktop_runtime import launcher_root
            gui_runs = launcher_root() / 'runs'
            if gui_runs.is_dir() and not is_link(gui_runs):
                recent = sorted((p for p in gui_runs.iterdir() if p.is_dir() and not is_link(p)), key=lambda p: p.name)[-3:]
                for gui_run in recent:
                    for path in report_files(gui_run):
                        add_path('launcher/' + gui_run.name + '/' + path.relative_to(gui_run).as_posix(), path)
            try:
                add('current/windows.json', json.dumps({'platform': platform.platform(),
                    'windows_version': platform.win32_ver(), 'machine': platform.machine(),
                    'memory': system_memory(), 'cpu': platform.processor(),
                    'executables': {name: shutil.which(name) for name in ('git', 'curl', 'cl', 'nvcc')},
                    'cuda_paths': {k: v for k, v in os.environ.items() if k == 'CUDA_PATH' or k.startswith('CUDA_PATH_V')},
                    'bootstrap_log': str(bootstrap / 'bootstrap.log')}).encode())
            except OSError as error:
                manifest['errors'].append({'command': 'windows-memory', 'error': redactor.text(str(error))})
        else:
            for name, path in [('meminfo.txt', '/proc/meminfo'), ('vmstat.txt', '/proc/vmstat'),
                               ('os-release.txt', '/etc/os-release')]:
                add_path('current/' + name, Path(path).resolve())
        # ComfyUI host setup has its own retained run directory.  It is not
        # part of ``setup-runs`` and used to be omitted entirely, leaving only
        # the generic "all package sources failed" line in a support report.
        # Include the most recent bounded runs on every platform; package
        # manager output is already redacted and capped by ``report_files``.
        host_runs = root / 'launcher' / 'host-runs'
        if host_runs.is_dir() and not is_link(host_runs):
            recent = sorted((p for p in host_runs.iterdir() if p.is_dir() and not is_link(p)),
                            key=lambda p: p.name)[-3:]
            for host_run in recent:
                for path in report_files(host_run):
                    add_path('launcher/host-runs/' + host_run.name + '/' +
                             path.relative_to(host_run).as_posix(), path)
        add_path('source/dependencies.json', Path(__file__).with_name('dependencies.json'))
        source = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))}
        add('source/hashes.json', json.dumps(source).encode())
        # Read structured reports first so their secrets are also redacted
        # when they occur in a launcher's plain-text error or log.
        if notes:
            add('launcher/error.txt', str(notes).encode('utf-8'))
        # These are retained log paths selected by the local launcher, never
        # archive names or directories supplied by a web request.
        for index, path in enumerate(extra_files):
            add_path('launcher/selected-%d.log' % index, Path(path))
        disk = shutil.disk_usage(output.parent)
        add('current/collector.json', json.dumps({'python': sys.version, 'disk_free_bytes': disk.free,
                                                'selected_run': str(selected) if selected else None}).encode())
        archive.writestr('manifest.json', json.dumps(manifest, indent=2))
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(prog='./freevideo diagnose', description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(os.environ.get('FREEVIDEO_HOME', DEFAULT_ROOT)))
    parser.add_argument('--config', type=Path)
    parser.add_argument('--run', type=Path, help='Test/benchmark/generation report directory; default latest test run')
    parser.add_argument('--out', type=Path, help='New ZIP; default <installation>/.freevideo/diagnostics/')
    parser.add_argument('--full', action='store_true',
                        help='Include every eligible report/log sidecar in full; media, tensors, weights, prompts and credentials remain excluded')
    args = parser.parse_args(argv)
    if args.out is None:
        args.out = args.root / '.freevideo' / 'diagnostics' / ('freevideo-diagnostics-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '.zip')
    try:
        result = collect(args.root, args.config or args.root / 'machine.json', args.run, args.out, complete=args.full)
    except (OSError, ValueError) as error:
        print('Diagnostics could not be written: ' + str(error), file=sys.stderr)
        return 1
    print('Diagnostics: %s\nFiles: %d · payload %.2f MiB · collection errors: %d\nLocal only; review before sharing. Original artifacts retained.' %
          (args.out.resolve(), len(result['files']), result['payload_bytes'] / 2**20, len(result['errors'])))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
