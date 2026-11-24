"""Start a FreeVideo module from this installation, whichever folder the command is run in.

The command scripts run this file with the module's name and arguments. The module then sees the same ``sys.argv``,
``__main__`` and exit code as under ``python -m``. It has no version requirement: the first step of a setup can run on
the system's Python 3.9 or later, and only the imports this file makes before the module starts matter there.
"""
import os
import sys

SAFE_PATH = 'PYTHONSAFEPATH'
INSTALLED_ONLY = 'import sys;sys.path[:]=[p for p in sys.path if p];'  # first in an inline ``python -c`` program


def _key(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def absolute_entries(value):
    return [entry for entry in (value or '').split(os.pathsep) if os.path.isabs(entry)]


def search_path(path, source, value, entry_folder):
    """``(sys.path, PYTHONPATH)``: this installation first, then what was named by a full path."""
    entries = value.split(os.pathsep) if value is not None else []
    named = absolute_entries(value)
    kept = {_key(entry) for entry in named} | {_key(source)}
    dropped = ({_key(entry) for entry in entries if not os.path.isabs(entry)} - kept) | {_key(entry_folder)}
    first = _key(source)
    return ([source] + [entry for entry in path if entry and _key(entry) not in dropped and _key(entry) != first],
            os.pathsep.join([source] + [entry for entry in named if _key(entry) != first]))


def protect(environ=None):
    """For a program that was not started through this file: the Python programs it starts use the installed code."""
    environ = os.environ if environ is None else environ
    if 'PYTHONPATH' in environ:
        named = absolute_entries(environ['PYTHONPATH'])
        if named:
            environ['PYTHONPATH'] = os.pathsep.join(named)
        else:
            del environ['PYTHONPATH']
    environ[SAFE_PATH] = '1'
    return environ


def without_safe_path(environ):
    """For a program of another project (ComfyUI, a package build), which imports from its own folder."""
    return {key: value for key, value in environ.items() if key != SAFE_PATH}


def main():
    folder = os.path.dirname(os.path.abspath(__file__))
    sys.path[:], os.environ['PYTHONPATH'] = search_path(sys.path, os.path.dirname(folder),
                                                        os.environ.get('PYTHONPATH'), folder)
    os.environ[SAFE_PATH] = '1'
    module = sys.argv[1]
    del sys.argv[0]
    import runpy
    runpy._run_module_as_main(module)  # what ``python -m`` itself calls


if __name__ == '__main__':
    main()
