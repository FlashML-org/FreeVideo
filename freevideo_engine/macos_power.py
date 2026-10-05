"""Request-scoped idle sleep prevention through macOS power assertions."""
from contextlib import contextmanager
import ctypes
import sys


def _create():
    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    io = ctypes.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFRelease.argtypes, cf.CFRelease.restype = [ctypes.c_void_p], None
    io.IOPMAssertionCreateWithName.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                              ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    io.IOPMAssertionCreateWithName.restype = ctypes.c_int32
    io.IOPMAssertionRelease.argtypes, io.IOPMAssertionRelease.restype = [ctypes.c_uint32], ctypes.c_int32
    strings = []
    try:
        for value in (b'PreventUserIdleSystemSleep', b'FreeVideo generation'):
            string = cf.CFStringCreateWithCString(None, value, 0x08000100)
            if not string:
                return None
            strings.append(string)
        assertion = ctypes.c_uint32()
        if io.IOPMAssertionCreateWithName(strings[0], 255, strings[1], ctypes.byref(assertion)) != 0:
            return None
        return io.IOPMAssertionRelease, assertion.value
    finally:
        for string in reversed(strings):
            cf.CFRelease(string)


@contextmanager
def awake():
    """Hold one assertion during work, without changing persistent preferences.

    Display sleep and explicit user sleep/lid actions retain their normal meaning.
    Failure to create the optional assertion does not prevent generation.
    """
    token = None
    if sys.platform == 'darwin':
        try:
            token = _create()
        except (OSError, AttributeError):
            pass
    try:
        yield token is not None
    finally:
        if token is not None:
            release, assertion = token
            release(assertion)
