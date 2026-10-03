"""Windows Unicode compatibility for compiler configuration and kernel loading."""
from functools import wraps
import os
from pathlib import Path


COMPILER_ENVIRONMENT_KEYS = (
    'TRITON_CACHE_DIR', 'TORCHINDUCTOR_CACHE_DIR', 'CUDA_CACHE_PATH',
    'TORCHINDUCTOR_USE_STATIC_CUDA_LAUNCHER',
)


def environment(root, environ):
    """Configure child compilers before imports, including encoder prewarming."""
    env = dict(environ)
    cache = Path(root) / 'kernel-cache'
    for key, folder in (('TRITON_CACHE_DIR', 'triton'),
                        ('TORCHINDUCTOR_CACHE_DIR', 'inductor'),
                        ('CUDA_CACHE_PATH', 'cuda')):
        if not env.get(key):
            env[key] = str(cache / folder)
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
    from triton import knobs
    native_getenv = knobs.getenv
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
