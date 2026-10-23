"""Keep ComfyUI process configuration out of managed engine children."""
import os
import shutil
from pathlib import Path
import sys


def with_private_compiler(root, env):
    """Without a system compiler, tools that build at run time (Triton) use the one FreeVideo installed."""
    private = Path(root) / 'tools' / 'gcc-bin' / 'gcc'
    if os.name != 'nt' and 'CC' not in env and private.exists() and not shutil.which('gcc', path=env.get('PATH')):
        env['CC'] = str(private)
    return env


def isolated_environment(root, source, environ=None):
    """Remove inherited Python/package targets only in the engine's child."""
    from .proxy import inherited
    from .download_settings import read
    env = inherited(environ)
    proxy_mode = read(Path(root) / 'download-settings.json')['proxy_mode']
    prefixes = [env.get(name) for name in ('VIRTUAL_ENV', 'CONDA_PREFIX')]
    prefixes.append(sys.prefix)
    # Do not remove /usr/bin when Comfy itself uses a system interpreter.
    prefixes = [Path(p).resolve() for p in prefixes if p and Path(p).resolve() not in
                (Path('/usr'), Path('/usr/local'), Path('/'))]
    for name in ('PATH', 'LD_LIBRARY_PATH'):
        if name not in env:
            continue
        kept = []
        for entry in env[name].split(os.pathsep):
            if not entry:
                continue
            path = Path(entry).resolve()
            if not any(path == prefix or prefix in path.parents for prefix in prefixes):
                kept.append(entry)
        env[name] = os.pathsep.join(kept)
    for name in list(env):
        if (name.startswith(('PYTHON', 'CONDA_', 'UV_', 'PIP_')) and name != 'PIP_PROXY' or name == 'VIRTUAL_ENV'
                or name.startswith('FREEVIDEO_BOOTSTRAP_') or name in
                ('FREEVIDEO_PYTHON', 'FREEVIDEO_FORCE_BOOTSTRAP', 'FREEVIDEO_RUNTIME_LOCK_FD',
                 'FREEVIDEO_RUNTIME_LOCK_HANDLE')):
            env.pop(name, None)
    env.update(FREEVIDEO_HOME=str(root), FREEVIDEO_PROXY_MODE=proxy_mode, PYTHONPATH=str(source), PYTHONNOUSERSITE='1',
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8', NO_COLOR='1', FREEVIDEO_UI_EVENTS='1')
    with_private_compiler(root, env)
    from .triton_compat import temporary_environment
    # Pruning sys.prefix above also prunes the frozen launcher's static curl.
    # Restore its fallback for managed Python after isolating Python packages.
    from .linux_bundle import child_environment
    return temporary_environment(root, child_environment(env))
