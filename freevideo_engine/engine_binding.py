"""Move managed editable installs only when an installation switches source."""
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import url2pathname

from . import subprocess_output
from .monitoring import save
from .long_paths import io_path, resolved, plain, plain_text, plain_errors
from .runtime_alias import checked_real_path
from .system import windows


@plain_errors
def editable_source(record):
    """Read one direct_url record; unreadable records raise for cleanup's safety."""
    value = json.loads(io_path(record).read_text(encoding='utf-8'))
    if not isinstance(value.get('url'), str):
        raise ValueError('Editable install record without a URL: ' + plain(record))  # Unknown: cleanup keeps every source.
    url = urlsplit(value['url'])
    if url.scheme != 'file' or not (value.get('dir_info') or {}).get('editable'):
        return None
    path = url2pathname('//' + url.netloc + url.path if url.netloc else url.path)
    # On Windows url2pathname already handles drive letters. Also allow reading
    # a Windows record on a POSIX host without treating /C:/ as a POSIX root.
    if re.match(r'^/[A-Za-z]:/', path):
        path = path[1:]
    return Path(plain(path))


@plain_errors
def bound_source(python):
    """The environment's editable source, or None if missing or ambiguous.

    Do not resolve the Python executable: venv/bin/python commonly links to the
    system Python, but its metadata belongs to the venv beside bin.
    """
    python = io_path(python).absolute()
    environment = python.parent.parent if python.parent.name in ('bin', 'Scripts') else python.parent
    found = set()
    for pattern in ('Lib/site-packages/freevideo_engine-*.dist-info/direct_url.json',
                    'lib/python3*/site-packages/freevideo_engine-*.dist-info/direct_url.json'):
        for record in environment.glob(pattern):
            try:
                source = editable_source(record)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                return None
            if source is not None:
                found.add(source)
    return next(iter(found)) if len(found) == 1 else None


@plain_errors
def source_version(source):
    """Read the selected source's stamp without importing it into this process."""
    package = io_path(source) / 'freevideo_engine'
    try:
        from .launcher_update import build_identity
        version = build_identity(json.loads((package / 'build-identity.json').read_text(encoding='utf-8')))['version']
        if version:
            return version
    except (OSError, ValueError, TypeError):
        pass
    try:
        version = (package / 'build-version.txt').read_text(encoding='utf-8').strip()
        if version:
            return version
    except OSError:
        pass
    try:
        for node in ast.parse((package / '__init__.py').read_text(encoding='utf-8')).body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == '__version__'
                                                    for target in node.targets):
                version = ast.literal_eval(node.value)
                if isinstance(version, str) and version:
                    return version
    except (OSError, ValueError, SyntaxError):
        pass
    raise RuntimeError('Cannot read the engine version from source: ' + str(source))


@plain_errors
def _uv(engine_root, machine, restore=None):
    system = machine.get('system') or ('Windows' if os.name == 'nt' else
                                      'Darwin' if sys.platform == 'darwin' else 'Linux')
    relative = {'Windows': 'uv.exe', 'Darwin': 'uv', 'Linux': 'uv-x86_64-unknown-linux-gnu/uv'}.get(system)
    tool = io_path(engine_root) / 'tools' / relative if relative else None
    if tool is None or not tool.is_file():
        where = 'The engine\'s uv is missing from %s/tools (%s)' % (engine_root, system)
        if restore is None or tool is None:
            raise RuntimeError(where + '; run Install / repair. Source rebinding does not download tools.')
        # Security software can remove it after installation; download the pinned one again. A tool in place stays.
        try:
            restore(engine_root, system)
            if not tool.is_file():
                raise RuntimeError('it was not found after the download')
        except Exception as error:
            reason = (str(error).strip().splitlines() or [type(error).__name__])[0]
            raise RuntimeError('%s and could not be downloaded again (%s); run Install / repair.' % (where, reason)) from error
    return Path(plain(tool))


def _same_source(left, right):
    return left is not None and resolved(left) == resolved(right)


def _tail(result):
    parts = [getattr(result, name, '') or '' for name in ('stdout', 'stderr')]
    return plain_text('\n'.join('\n'.join(part.decode(errors='replace') if isinstance(part, bytes) else part
                             for part in parts).strip().splitlines()[-6:]) or '(no output)')


@plain_errors
def _bind(uv, python, source, run):
    command = [str(uv), 'pip', 'install', '--python', str(python), '--no-deps', '--no-build-isolation',
               '--reinstall-package', 'freevideo-engine', '-e', str(source)]
    try:
        result = subprocess_output.run_external(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=180, runner=run)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError('Editable install failed for %s: %s\n%s' % (python, error, _tail(error))) from error
    if result.returncode:
        raise RuntimeError('Editable install failed for %s (uv exit %s):\n%s' %
                           (python, result.returncode, _tail(result)))
    uv_tail = _tail(result)
    try:
        result = subprocess_output.run_python([str(python), '-I', '-B', '-c',
                      'import freevideo_engine,sys;print(freevideo_engine.__file__)'],
                     stdin=subprocess.DEVNULL, capture_output=True, timeout=30, runner=run)
        lines = (result.stdout or '').strip().splitlines()
        imported = Path(lines[-1]) if lines else None
        if (result.returncode or imported is None or not imported.is_absolute()
                or not resolved(imported).is_relative_to(resolved(source))
                or not _same_source(bound_source(python), source)):
            raise RuntimeError('isolated import or editable metadata did not match %s:\n%s' % (source, _tail(result)))
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as error:
        raise RuntimeError('Editable verification failed for %s: %s\nuv output:\n%s' %
                           (python, error, uv_tail)) from error


@plain_errors
def pending(machine, source):
    """The environments (E, then T if separate) whose editable install points elsewhere."""
    source = Path(plain(resolved(source)))
    result, seen = [], set()
    for key in ('python', 'comfy_python'):
        value = machine.get(key)
        if not value:
            continue
        python = Path(plain(io_path(value).absolute()))
        identity = os.path.normcase(os.path.abspath(python))
        if identity in seen or not io_path(python).is_file():
            continue
        seen.add(identity)
        old = bound_source(python)
        if not _same_source(old, source):
            result.append((python, old))
    return result


@plain_errors
def rebind(engine_root, machine, source, *, uv=None, run=subprocess.run, restore=None):
    """Rebind E/T, verify fresh imports, then atomically record the new source.

    A failure restores every attempted environment whose previous source still
    exists, including a failed uv install that may have partially replaced it.
    A missing uv is restored through `restore(engine_root, system)` when the caller passes one; a failed
    restore stops the update before any environment is touched.
    """
    engine_root = Path(plain(io_path(checked_real_path(engine_root))))
    source = Path(plain(resolved(checked_real_path(source))))
    version = source_version(source)
    environments = pending(machine, source)
    if environments:
        try:
            uv = Path(plain(uv)) if uv is not None else _uv(engine_root, machine, restore)
            if not io_path(uv).is_file():
                raise RuntimeError('The engine\'s uv is missing: ' + str(uv))
        except RuntimeError as error:
            raise RuntimeError('%s\nEnvironments: %s' % (error, ', '.join(str(py) for py, _ in environments))) from error
    shown = str
    if windows():
        from . import launch_view, processes
        view = launch_view.for_engine(engine_root)
        if view.used:
            runner = processes.run if run is subprocess.run else run
            def viewed_run(command, **kwargs):
                command, environment, _ = launch_view.execution(command, os.environ, None, view)
                return runner(command, env=environment, **kwargs)
            run = viewed_run
            # A failure quotes what the tools printed: the same files, in the folder the user knows.
            shown = lambda text: launch_view.text_back(text, view)
    updated = dict(machine, source=str(source), engine_version=version)
    attempted = []
    try:
        for python, old in environments:
            attempted.append((python, old))
            _bind(uv, python, source, run)
        if updated != machine:
            save(engine_root / 'machine.json', updated)
    except Exception as error:
        rollback = []
        for python, old in reversed(attempted):
            # Even unchanged direct_url metadata cannot prove an interrupted uv
            # install left the finder and console scripts intact.
            if old is None or not io_path(old).is_dir():
                rollback.append('Cannot restore %s: previous source is missing or unknown (%s).' % (python, old))
                continue
            try:
                _bind(uv, python, Path(plain(resolved(old))), run)
            except Exception as restore_error:
                rollback.append('Cannot restore %s: %s' % (python, restore_error))
        raise RuntimeError(shown(str(error) + ('\n' + '\n'.join(rollback) if rollback else ''))) from error
    return updated
