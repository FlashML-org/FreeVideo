"""Windows Unicode compatibility for compiler configuration and kernel loading."""
from functools import wraps
import os
from pathlib import Path


TEMP_KEYS = ('TMPDIR', 'TEMP', 'TMP')
TEMP_ROOT_ENV = 'FREEVIDEO_COMPILER_TEMP_ROOT'


def temp_root(environ=None):
    """The managed compiler temporary folder, or None for caller/system folders."""
    from . import runtime_alias
    if not runtime_alias._backend.windows():
        return None
    env = os.environ if environ is None else environ
    root = env.get(TEMP_ROOT_ENV)
    return root if root and all(env.get(key) == root for key in TEMP_KEYS) else None


def _writable_temporary(path):
    import tempfile
    from .long_paths import io_path
    # dir= avoids consulting or caching the parent's default temporary folder.
    with tempfile.TemporaryFile(prefix='freevideo-temp-check-', dir=io_path(path)) as stream:
        stream.write(b'ok')
        stream.flush()


def _usable_folder(path):
    # Nothing is created or written to a folder that is only being looked at.
    return os.path.isdir(path) and os.access(path, os.W_OK)


def _unsafe_temporary(environ):
    from . import runtime_alias
    explicit = [os.path.abspath(environ[key]) for key in TEMP_KEYS if environ.get(key)]
    # Native compilers can prefer TMP over TEMP, unlike Python's TMPDIR first.
    if any(not runtime_alias._backend.ansi_path(path) for path in explicit):
        return True
    for path in explicit:
        if _usable_folder(path):
            return False  # a folder that is there and writable stays exactly as it is
    # Python's Windows fallbacks, after unusable or missing explicit choices.
    profile = environ.get('USERPROFILE') or os.path.expanduser('~')
    candidates = [os.path.join(profile, 'AppData', 'Local', 'Temp'),
                  os.path.join(environ.get('SYSTEMROOT', r'C:\Windows'), 'Temp'),
                  r'c:\temp', r'c:\tmp', r'\temp', r'\tmp', os.curdir]
    for path in candidates:
        path = os.path.abspath(path)
        if not runtime_alias._backend.ansi_path(path):
            return True
        if _usable_folder(path):
            return False
    return False


def _clean_temporary(path):
    """Remove day-old children of this dedicated folder, without following links."""
    import shutil
    import time
    cutoff = time.time() - 86400
    for child in path.iterdir():
        try:
            if child.lstat().st_mtime >= cutoff:
                continue
            if child.is_symlink():
                child.unlink()
            elif child.is_junction():
                child.rmdir()
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except OSError:
            pass  # A compiler may still hold it; try again on the next start.


def temporary_environment(root, environ):
    """Keep spellable temporary choices; otherwise use this installation's tmp."""
    import logging
    from . import launch_view, runtime_alias
    from .long_paths import io_path, path_chars, plain
    if not runtime_alias._backend.windows():
        return environ
    try:
        # A spellable folder that is there stays as it is, whoever chose it (also another installation's);
        # a managed one that has been removed is made again, for compilers that read TMP directly.
        managed = temp_root(environ)
        if not _unsafe_temporary(environ) and not (managed and not os.path.isdir(managed)):
            return environ
        # Keep the lexical short spelling: resolve() would undo the junction.
        root = Path(os.path.abspath(plain(root)))
        view = launch_view.current(environ) or launch_view.current() or launch_view.for_engine(root)
        temporary = Path(launch_view.value(root, view)) / 'tmp'
        if not runtime_alias._backend.ansi_path(temporary):
            raise OSError('installation temporary path cannot be represented by the system code page')
        if path_chars(temporary) > 200:
            raise OSError('installation temporary path exceeds 200 characters')
        folder = io_path(temporary)
        folder.mkdir(parents=True, exist_ok=True)
        if folder.is_symlink() or folder.is_junction():
            raise OSError('installation temporary folder is a link')
        _writable_temporary(temporary)
        _clean_temporary(folder)
        return dict(environ, **{key: str(temporary) for key in (*TEMP_KEYS, TEMP_ROOT_ENV)})
    except Exception as error:  # A temporary folder choice never blocks the engine from starting.
        logging.warning('[FreeVideo] Compiler temporary folder unchanged: %s',
                        str(error).replace('\r', ' ').replace('\n', ' '))
        return environ


def own_temporary_environment(root=None):
    """Apply before third-party imports and invalidate an already cached tempdir."""
    from . import runtime_alias
    if not runtime_alias._backend.windows():
        return
    import logging
    import tempfile
    try:
        from .system import install_root
        env = temporary_environment(root if root is not None else install_root(), os.environ)
        if env is not os.environ:
            os.environ.update(env)
            tempfile.tempdir = None
        elif tempfile.tempdir and not runtime_alias._backend.ansi_path(tempfile.tempdir):
            tempfile.tempdir = None  # a cached folder the system code page cannot spell is looked up again
    except Exception as error:  # A temporary folder choice never blocks the engine from starting.
        logging.warning('[FreeVideo] Compiler temporary folder unchanged: %s',
                        str(error).replace('\r', ' ').replace('\n', ' '))


COMPILER_ENVIRONMENT_KEYS = (
    'TRITON_CACHE_DIR', 'TORCHINDUCTOR_CACHE_DIR', 'CUDA_CACHE_PATH',
    'CUDA_CACHE_MAXSIZE', 'TORCHINDUCTOR_USE_STATIC_CUDA_LAUNCHER',
)
# The driver's JIT cache limit; 4 GiB is the most it accepts. RTX 50 kernels
# ship as capsule-Mercury cubins, and a Windows driver (616.92) finalized
# them again on first load and kept the result in CUDA_CACHE_PATH, entries up
# to 58 MiB (10 MiB in all on Linux). At the default 1 GiB the cache filled,
# the driver evicted entries, and finalizing one again stalled encoding, the
# first step, the latent upscale or decoding for about 10 s, at random.
CUDA_CACHE_MAXSIZE = str(4 << 30)


def torch_default_inductor_cache():
    """torch.compile's own default, as torch._inductor.runtime.cache_dir_utils builds it.

    The first time torch needs this folder it writes it back into os.environ.
    A ComfyUI process that has touched torch.compile then hands the temporary
    folder to every child as if a caller had chosen it, and system cleanup of
    temporary files deletes the compiled graphs.
    """
    import getpass
    import re
    import tempfile
    try:
        username = getpass.getuser()
    except (KeyError, ModuleNotFoundError, OSError):
        getuid = getattr(os, 'getuid', None)
        username = f'uid_{getuid()}' if callable(getuid) else 'unknown_user'
    return os.path.join(tempfile.gettempdir(), 'torchinductor_' + re.sub(r'[\\/:*?"<>|]', '_', username))


def _same_path(first, second):
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def environment(root, environ):
    """Configure child compilers before imports, including encoder prewarming."""
    env = dict(temporary_environment(root, environ))
    cache = Path(root) / 'kernel-cache'
    for key, folder in (('TRITON_CACHE_DIR', 'triton'),
                        ('TORCHINDUCTOR_CACHE_DIR', 'inductor'),
                        ('CUDA_CACHE_PATH', 'cuda')):
        if not env.get(key):
            env[key] = str(cache / folder)
    # Generation and background precompiling must share one folder, and it
    # belongs to the installation. Only torch's inherited default is replaced;
    # a folder someone set on purpose stays.
    if _same_path(env['TORCHINDUCTOR_CACHE_DIR'], torch_default_inductor_cache()):
        env['TORCHINDUCTOR_CACHE_DIR'] = str(cache / 'inductor')
    if not env.get('CUDA_CACHE_MAXSIZE'):
        env['CUDA_CACHE_MAXSIZE'] = CUDA_CACHE_MAXSIZE
    if os.name == 'nt' and any(not os.path.abspath(env[key]).isascii()
                              for key in ('TRITON_CACHE_DIR', 'TORCHINDUCTOR_CACHE_DIR')):
        # The static launcher passes a UTF-8 narrow filename to cuModuleLoad.
        # Triton's normal launcher loads the same cubin bytes with
        # cuModuleLoadData, avoiding Windows filename encoding at that boundary.
        # Keep explicit caller choices and leave torch.compile enabled.
        env.setdefault('TORCHINDUCTOR_USE_STATIC_CUDA_LAUNCHER', '0')
    return env


def activate():
    """Recover Unicode knob values when the native getenv binding cannot decode.

    triton-windows 3.7.1.post27 exposes narrow CRT getenv strings through a
    UTF-8 pybind return value. A Chinese TRITON_CACHE_DIR on a GBK Windows
    installation fails before a kernel can compile. Python's Windows
    environment mapping already preserves the original Unicode value.
    """
    if os.name != 'nt':
        return
    try:
        from triton import knobs
    except ImportError:
        return
    native_getenv = getattr(knobs, 'getenv', None)
    if native_getenv is None:
        # Pascal stub / incomplete Triton: no CRT getenv to wrap.
        return
    if getattr(native_getenv, '_freevideo_unicode_env', False):
        return

    @wraps(native_getenv)
    def unicode_getenv(key, *args, **kwargs):
        try:
            return native_getenv(key, *args, **kwargs)
        except UnicodeDecodeError:
            value = os.environ.get(key)
            if value is None or value.isascii():
                raise
            return value

    unicode_getenv._freevideo_unicode_env = True
    knobs.getenv = unicode_getenv
