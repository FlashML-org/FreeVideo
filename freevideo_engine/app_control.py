"""Windows application control (Smart App Control) refusing unsigned extension files.

Windows words its refusal in the system language, so text matching alone misses
it on a non-English system.  ``describe`` asks Windows for the error code of the
file that failed to load and adds the code to the message.  ``tolerate_blocked_scipy``
keeps the optional SciPy use inside Transformers and Diffusers from stopping a
run when one of SciPy's files is refused.
"""
import importlib
import importlib.abc
import importlib.machinery
import sys
import threading

# ERROR_ACCESS_DISABLED_BY_POLICY, the code behind "An Application Control policy has blocked this file."
BLOCKED_BY_POLICY = 4551

# The SciPy modules each library imports when it finds SciPy.
SCIPY_USERS = {
    'transformers.utils.import_utils': ('scipy.optimize',),
    'diffusers.utils.import_utils': ('scipy.stats', 'scipy.integrate'),
}

_lock = threading.Lock()
_installed = False
_unusable = {}


def blocked_file(error):
    """The path Windows refused to load for this ImportError, or None."""
    if sys.platform != 'win32' or not isinstance(error, ImportError) or not getattr(error, 'path', None):
        return None
    if 'DLL load failed' not in str(error):
        return None
    try:
        import ctypes
        ctypes.WinDLL(str(error.path))
    except OSError as refusal:
        return str(error.path) if getattr(refusal, 'winerror', None) == BLOCKED_BY_POLICY else None
    return None


def describe(error):
    """The error text, with the Windows code appended when application control refused the file."""
    text = str(error)
    path = blocked_file(error)
    if path:
        text += ' [WinError %d: Windows application control blocked %s]' % (BLOCKED_BY_POLICY, path)
    return text


def scipy_module(name):
    """Import a SciPy module when a feature needs it; a refused file is reported with its Windows code."""
    try:
        return importlib.import_module(name)
    except ImportError as error:
        text = describe(error)
        if text == str(error):
            raise
        raise ImportError(text, name=error.name, path=error.path) from error


def scipy_problem():
    """Why SciPy was set aside in this process, or None."""
    return _unusable.get('scipy')


def _checked(name, original, modules):
    def available():
        if not original():
            return False
        if 'scipy' not in _unusable:
            try:
                for module in modules:
                    importlib.import_module(module)
            except ImportError as error:
                _unusable['scipy'] = describe(error)
                print('FreeVideo: SciPy cannot be loaded (%s); continuing without it.' % _unusable['scipy'],
                      file=sys.stderr, flush=True)
        return 'scipy' not in _unusable
    available.__name__ = original.__name__
    available.freevideo_checked = True
    return available


class _Patch(importlib.abc.Loader):
    def __init__(self, loader):
        self.loader = loader

    def create_module(self, spec):
        return self.loader.create_module(spec)

    def exec_module(self, module):
        self.loader.exec_module(module)
        original = getattr(module, 'is_scipy_available', None)
        if callable(original) and not hasattr(original, 'freevideo_checked'):
            module.is_scipy_available = _checked(module.__name__, original, SCIPY_USERS[module.__name__])


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name not in SCIPY_USERS:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and spec.loader is not None:
            spec.loader = _Patch(spec.loader)
        return spec


def tolerate_blocked_scipy():
    """Treat SciPy as not installed in Transformers and Diffusers once one of its files cannot be loaded.

    Both libraries use SciPy only for optional parts (detection losses, extra
    noise schedules) and gate it on ``is_scipy_available``; a file that Windows
    refuses is an ImportError at that gate otherwise.  The check imports the
    modules they would import next, so nothing is loaded that was not needed.
    """
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True
        for name in SCIPY_USERS:
            module = sys.modules.get(name)
            original = getattr(module, 'is_scipy_available', None)
            if callable(original) and not hasattr(original, 'freevideo_checked'):
                checked = module.is_scipy_available = _checked(name, original, SCIPY_USERS[name])
                package = sys.modules.get(name.rpartition('.')[0])
                if getattr(package, 'is_scipy_available', None) is original:
                    package.is_scipy_available = checked  # imported already by the package's own namespace
        sys.meta_path.insert(0, _Finder())
