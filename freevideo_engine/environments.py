"""Pinned installation layouts; model lifetimes are independent of venv count."""
import logging
import os
from pathlib import Path
import platform

ENVIRONMENTS = {
    'unified': {'cuda': 'cu130', 'torch': ['torch==2.13.0', 'torchvision==0.28.0', 'torchaudio==2.11.0']},
    'engine': {'cuda': 'cu129', 'torch': ['torch==2.13.0', 'torchvision==0.28.0']},
    'encoder': {'cuda': 'cu130', 'torch': ['torch==2.14.0', 'torchvision==0.29.0', 'torchaudio==2.11.0']},
}


def constraints_file(name, system=None):
    if (system or platform.system()) == 'Darwin':
        if name != 'unified':
            raise ValueError('Mac requires the native unified environment')
        from .macos_bootstrap import constraints
        return constraints()
    prefix = 'windows-' if (system or platform.system()) == 'Windows' else ''
    return Path(__file__).resolve().parent.parent / 'constraints' / (prefix + name + '.txt')


# uv reads each value of these options as a space-separated list of files, even
# when it arrives as one quoted argument: `-c "D:\Comfy UI\pins.txt"` names
# `D:\Comfy` and `UI\pins.txt` and stops with "File not found" (exit code 2).
# Requirement files, editables, wheels and --python keep their spaces.
UV_FILE_LIST_OPTIONS = frozenset(('-c', '--constraint', '--constraints', '-b', '--build-constraint',
                                  '--build-constraints', '--override', '--overrides'))


# uv 0.9.0 adds sdists-v9/<index>/<name>/<version>/<revision>/src and
# setuptools adds build/bdist.<platform>/wheel/... beneath the cache. Reserve
# 200 characters below the engine root, including downloads/uv-cache. This
# allows headroom above measured staging paths, not arbitrary sdists.
UV_BUILD_PATH_CHARS = 200


PYTHON_NAMES = ('python.exe', 'python', 'pythonw.exe')


def uv_build_environment(command, root, environ):
    """Windows uv pip builds use a short view of the existing cache and scratch.

    TEMP alone does not move uv's sdist source tree or isolated build venv.
    Mark only this child's view: processes.popen translates its paths, while
    installation settings, cache ownership and the parent's environment stay put.
    """
    # `venv` and `python` also write into the cache (interpreter records, downloads) below the same root.
    # A Python child also needs it: it runs `uv` itself (VDN and ComfyUI packages) and the loader
    # refuses a DLL such as torch\\lib\\c10.dll whose full path exceeds the limit (WinError 206).
    runs_uv = list(command[1:2]) in (['pip'], ['venv'], ['python']) and Path(command[0]).name.lower() in ('uv', 'uv.exe')
    if not command or not (runs_uv or Path(str(command[0])).name.lower() in PYTHON_NAMES):
        return environ
    from . import launch_view, runtime_alias
    from .long_paths import io_path, path_chars, plain
    from .package_paths import WINDOWS_PATH_LIMIT
    from .triton_compat import TEMP_KEYS, TEMP_ROOT_ENV, _writable_temporary
    if not runtime_alias._backend.windows():
        return environ
    root = Path(os.path.abspath(plain(root)))
    view = launch_view.current(environ)
    if (view is None or not root.is_relative_to(view.real)
            or path_chars(launch_view.value(root, view)) + 1 + UV_BUILD_PATH_CHARS > WINDOWS_PATH_LIMIT):
        try:
            view = launch_view.View(root, runtime_alias.runtime_view(root, longest=UV_BUILD_PATH_CHARS))
        except OSError as error:
            if (path_chars(root) + 1 + UV_BUILD_PATH_CHARS <= WINDOWS_PATH_LIMIT
                    and runtime_alias._backend.ansi_path(root)):
                view = launch_view.View(root, root)
            else:
                return environ  # No short entry: keep the old behaviour; packages without source builds still install.
    if path_chars(launch_view.value(root, view)) + 1 + UV_BUILD_PATH_CHARS > WINDOWS_PATH_LIMIT:
        return environ
    temporary = Path(launch_view.value(root / 'tmp' / 'uv', view))
    try:
        for path in (temporary.parent, temporary):
            folder = io_path(path)
            folder.mkdir(parents=True, exist_ok=True)
            if folder.is_symlink() or runtime_alias.is_junction(path):
                raise OSError('The package build temporary folder is a link')
        _writable_temporary(temporary)
    except OSError as error:
        # Like a missing short entry, an unusable scratch folder must not stop installs that worked before.
        logging.getLogger(__name__).warning('Package builds keep the previous environment: %s', error)
        return environ
    return dict(launch_view.environment_view(environ, view),
                **{key: str(temporary) for key in (*TEMP_KEYS, TEMP_ROOT_ENV)})


def uv_file_arguments(command, cwd=None):
    """Return (command, cwd) for which uv reads constraint files in folders with spaces.

    Such a file is named relative to its folder, which becomes uv's working
    directory. FreeVideo passes every other path to uv as an absolute path.
    """
    command = [str(item) for item in command]
    if not command or Path(command[0]).name.lower() not in ('uv', 'uv.exe') or command[1:2] != ['pip']:
        return command, cwd
    values = {}
    for index, item in enumerate(command):
        option, equals, value = item.partition('=')
        if item in UV_FILE_LIST_OPTIONS and index + 1 < len(command):
            values[index + 1] = ('', command[index + 1])
        elif equals and option.startswith('--') and option in UV_FILE_LIST_OPTIONS:
            values[index] = (option + '=', value)
    spaced = {index: row for index, row in values.items() if ' ' in row[1]}
    if not spaced:
        return command, cwd
    base = Path(cwd or os.getcwd())
    folder = (base / next(iter(spaced.values()))[1]).parent
    result = list(command)
    for index, (prefix, value) in spaced.items():
        try:
            relative = os.path.relpath(base / value, folder)
        except ValueError:  # Another Windows drive; uv reports the original path.
            return command, cwd
        if ' ' in relative:
            return command, cwd
        result[index] = prefix + relative
    return result, str(folder)


# Linux processors setup installs on, by the names platform.machine() and uname -m give them.
LINUX_MACHINES = {'x86_64': 'x86_64', 'amd64': 'x86_64', 'aarch64': 'aarch64', 'arm64': 'aarch64'}


def linux_machine(machine=None):
    """'x86_64' or 'aarch64' for a Linux processor setup installs on; None for any other."""
    return LINUX_MACHINES.get(str(platform.machine() if machine is None else machine).lower())


def setup_supported(system, machine):
    """Setup installs on Linux x86_64 or ARM64 and on native Windows x64."""
    machine = str(machine or '').lower()
    return (system == 'Linux' and linux_machine(machine) is not None
            or system == 'Windows' and machine in ('x86_64', 'amd64'))


def uv_folder(machine=None):
    """The folder the Linux uv archive unpacks to under <root>/tools for this processor."""
    return 'uv-%s-unknown-linux-gnu' % (linux_machine(machine) or 'x86_64')


def conda_lock(value, machine=None):
    """A conda-forge lock (micromamba and packages) for this Linux processor: its linux_aarch64 section on ARM64."""
    if linux_machine(machine) == 'aarch64' and 'linux_aarch64' in value:
        return dict(value, **value['linux_aarch64'])
    return value


def conda_triple(machine=None):
    """The prefix conda-forge's GCC gives its compilers on this Linux processor."""
    return '%s-conda-linux-gnu' % (linux_machine(machine) or 'x86_64')


def bootstrap_versions(value, system=None, machine=None):
    result = dict(value, target_system=system or platform.system())
    if result['target_system'] == 'Darwin':
        from .macos_bootstrap import versions
        return versions(value)
    if result['target_system'] == 'Windows':
        result['uv'] = result['windows']['uv']
    elif result['target_system'] == 'Linux':
        result['target_machine'] = linux_machine(machine) or 'x86_64'
        if result['target_machine'] == 'aarch64':
            # ARM64 servers (and GB10) take NVIDIA's linux-sbsa toolkit archives of the same versions.
            result['uv'] = result['linux_aarch64']['uv']
            result['cuda_toolkits'] = result['linux_aarch64']['cuda_toolkits']
    return result


def environment_names(layout):
    if layout == 'unified':
        return ('unified',)
    if layout == 'dual':
        return ('engine', 'encoder')
    raise ValueError('Unknown environment layout: ' + str(layout))


def select_layout(requested=None, saved=None):
    saved = saved or {}
    layout = requested or saved.get('pending_environment_layout') or saved.get('environment_layout')
    if layout is None and saved.get('python'):
        # v0.2.0/0.2.1 receipts did not record a layout. Preserve them on update.
        layout = 'unified' if saved.get('python') == saved.get('comfy_python') else 'dual'
    layout = layout or 'unified'
    environment_names(layout)
    return layout


def role_pythons(root, layout, system=None):
    from .system import venv_python
    names = environment_names(layout)
    return {'engine': venv_python(root / 'envs' / names[0], system),
            'encoder': venv_python(root / 'envs' / names[-1], system)}
