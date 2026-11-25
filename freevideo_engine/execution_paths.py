"""Short installation paths only at native-program and child-process boundaries."""
import os
from pathlib import PurePath
import subprocess

from . import launch_view
from .installed_entry import INSTALLED_ONLY


def execution_path(path):
    """Keep the original object without a runtime view; never touch the disk."""
    view = launch_view.current()
    if view is None:
        return path
    moved = launch_view.value(path, view)
    return type(path)(moved) if moved is not path and isinstance(path, PurePath) else moved


def execution_environment(env):
    """Environment for this process's compilers and Python children, not receipts."""
    view = launch_view.current(env) or launch_view.current()
    return env if view is None else launch_view.execution((), env, None, view)[1]


def _execution(command, kwargs):
    view = launch_view.current(kwargs.get('env')) or launch_view.current()
    if view is None:
        return command, kwargs, None
    # CreateProcess cannot use a long cwd even when its caller supports long paths.
    command, env, cwd = launch_view.execution(
        command, kwargs.get('env'), kwargs.get('cwd') or os.getcwd(), view)
    return command, dict(kwargs, env=env, cwd=cwd), view


def _text_back(value, view):
    return launch_view.text_back(value, view) if view is not None and isinstance(value, str) else value


def _call(function, command, kwargs):
    original = command
    command, kwargs, view = _execution(command, kwargs)
    try:
        result = function(command, **kwargs)
    except subprocess.CalledProcessError as error:
        if view is not None:
            error.cmd = original
            error.output = _text_back(error.output, view)
            error.stderr = _text_back(error.stderr, view)
        raise
    if view is not None and isinstance(result, subprocess.CompletedProcess):
        # Tool output and receipts keep the user's real paths.
        result.args = original
        result.stdout = _text_back(result.stdout, view)
        result.stderr = _text_back(result.stderr, view)
    return _text_back(result, view)


def run(command, **kwargs):
    # Preserve startupinfo, creationflags and detached lifetimes of native helpers.
    return _call(subprocess.run, command, kwargs)


def popen(command, **kwargs):
    command, kwargs, _ = _execution(command, kwargs)
    return subprocess.Popen(command, **kwargs)


def check_output(command, **kwargs):
    return _call(subprocess.check_output, command, kwargs)


def call(function, command, **kwargs):
    """Run a caller's own spawn function with the same paths in and real paths back."""
    return _call(function, command, kwargs)


# Run as "python -c": import the engine, leave no trace of that import, then run the script as
# python would have. Arguments: the engine's source folder, the script, the script's own arguments.
_ENGINE_FIRST = (
    INSTALLED_ONLY + "import os,runpy;source=sys.argv.pop(1);sys.path.insert(0,source);import freevideo_engine;"
    "sys.path.remove(source);"
    "[sys.modules.pop(name) for name in list(sys.modules) if name.partition('.')[0]=='freevideo_engine'];"
    "del sys.argv[0];folder=os.path.dirname(sys.argv[0]);"
    "sys.path.insert(0,folder);"
    "runpy.run_path(sys.argv[0],run_name='__main__')")


def engine_first(command, source):
    """A script's command that imports the engine before the script runs.

    Where the system's long-path support is on, a program is long-path aware from its first
    line, and a library it loads by a resolved path fails until the engine's import has put
    library loading through the short folder. A script that imports such a library before
    the engine, as ComfyUI's main does, is started this way.
    """
    entry = next(index for index, item in enumerate(command) if str(item).endswith('.py'))
    return [*command[:entry], '-c', _ENGINE_FIRST, str(source), *command[entry:]]
