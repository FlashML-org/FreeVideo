"""Keep another CUDA/cuDNN install on the user's PATH from replacing FreeVideo's own libraries.

Windows searches PATH for the sublibraries cuDNN loads by name, so a cuDNN
folder the user installed earlier can be picked over the one in the PyTorch
environment and generation then fails. A process removes such folders from its
own PATH before torch loads and names its own library folders explicitly.
"""
import os
from pathlib import Path
import re
import sys

# Files that make a PATH folder a CUDA runtime or cuDNN folder.
_MARK = re.compile(r'^(?:cudnn[\w.-]*|cublas[\w.-]*|cudart[\w.-]*|cufft[\w.-]*|curand[\w.-]*|cusparse[\w.-]*'
                   r'|cusolver[\w.-]*|nvrtc[\w.-]*|nvjitlink[\w.-]*)\.dll$', re.I)


def _inside(path, roots):
    path = os.path.normcase(os.path.normpath(path))
    return any(path == r or path.startswith(r + os.sep) for r in (os.path.normcase(os.path.normpath(x)) for x in roots))


def foreign_directory(directory, listing=os.listdir):
    try:
        return any(_MARK.match(name) for name in listing(directory))
    except OSError:
        return False


def bundled_directories(prefix=None):
    """Library folders of this Python environment (torch/lib, nvidia/*/bin)."""
    import sysconfig
    site = Path(sysconfig.get_paths()['purelib']) if prefix is None else Path(prefix)
    found = [site / 'torch' / 'lib']
    nvidia = site / 'nvidia'
    try:
        found += sorted(p / 'bin' for p in nvidia.iterdir() if (p / 'bin').is_dir())
    except OSError:
        pass
    return [str(p) for p in found if p.is_dir()]


def clean(path, bundled, listing=os.listdir):
    """PATH without the folders that hold CUDA/cuDNN files outside `bundled`; (new PATH, removed)."""
    kept, removed = [], []
    for entry in path.split(os.pathsep):
        if entry and not _inside(entry, bundled) and foreign_directory(entry, listing):
            removed.append(entry)
        else:
            kept.append(entry)
    return os.pathsep.join(kept), removed


def isolate(bundled=None, listing=os.listdir):
    """This process: clean PATH, then add the bundled folders to the DLL search. Returns removed folders."""
    if os.name != 'nt' or 'torch' in sys.modules:
        return []
    bundled = bundled_directories() if bundled is None else bundled
    value, removed = clean(os.environ.get('PATH', ''), bundled, listing)
    if removed:
        os.environ['PATH'] = value
        if hasattr(os, 'add_dll_directory'):
            for directory in bundled:
                try:
                    os.add_dll_directory(directory)
                except OSError:
                    pass
    return removed
