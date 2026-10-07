"""Pinned installation layouts; model lifetimes are independent of venv count."""
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


def bootstrap_versions(value, system=None):
    result = dict(value, target_system=system or platform.system())
    if result['target_system'] == 'Darwin':
        from .macos_bootstrap import versions
        return versions(value)
    if result['target_system'] == 'Windows':
        result['uv'] = result['windows']['uv']
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
