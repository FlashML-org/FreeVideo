"""Read native UI language preferences without depending on a shell locale or Qt."""
import ctypes
import sys


def preferred_language():
    if sys.platform != 'darwin':
        raise RuntimeError('Native language preferences require macOS')
    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    signatures = {
        'CFLocaleCopyPreferredLanguages': ([], ctypes.c_void_p),
        'CFArrayGetCount': ([ctypes.c_void_p], ctypes.c_long),
        'CFArrayGetValueAtIndex': ([ctypes.c_void_p, ctypes.c_long], ctypes.c_void_p),
        'CFStringGetCString': ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_uint32], ctypes.c_bool),
        'CFRelease': ([ctypes.c_void_p], None),
    }
    for name, (args, result) in signatures.items():
        function = getattr(cf, name)
        function.argtypes, function.restype = args, result
    languages = cf.CFLocaleCopyPreferredLanguages()
    if not languages:
        return None
    try:
        count = cf.CFArrayGetCount(languages)
        if not count:
            return None
        for index in range(min(count, 32)):
            value = cf.CFArrayGetValueAtIndex(languages, index)
            buffer = ctypes.create_string_buffer(128)
            if value and cf.CFStringGetCString(value, buffer, len(buffer), 0x08000100):
                language = buffer.value.decode('utf-8').lower().replace('_', '-').split('-')[0]
                if language in ('en', 'zh'):
                    return language
        return 'en'
    finally:
        # Copy follows Core Foundation's Create Rule; array elements are borrowed.
        cf.CFRelease(languages)
