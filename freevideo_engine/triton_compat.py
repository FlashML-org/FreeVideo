"""Windows Unicode environment compatibility at FreeVideo's Triton entry points."""
from functools import wraps
import os


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
