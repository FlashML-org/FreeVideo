"""Pinned installation layouts; model lifetimes are independent of venv count."""
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
