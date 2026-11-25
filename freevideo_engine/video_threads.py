"""A fixed libx264 thread count, so a video's bytes do not depend on how many CPUs the process may use.

libx264 splits its work into frame threads, and what it writes depends on how many: with the count left to the
encoder it takes the CPUs the process may use (at most 12 for a 1344x768 picture, more for a taller one), so the same frames gave a different mp4 on a computer
with 8 CPUs than on one with 48, or when a process was limited to fewer CPUs. The pictures were identical; only
the encoding differed (mean difference about 1.5 of 255).  Every encode that left the count open goes through here
and gets 12. For a landscape picture of 1344x768 or smaller that is what a computer with 12 or more CPUs always
chose, so those files do not change. A taller or larger picture (768x1344, 2560x1440) lets the encoder use more than
12 threads when the computer has more than 12 CPUs (13 or more), so on such computers its file changes once, to the
fixed one.
"""
import importlib
import threading

X264_THREADS = 12

_lock = threading.Lock()


def pin(stream, count=X264_THREADS):
    """Give a libx264 stream the fixed thread count (before its first frame is encoded)."""
    stream.codec_context.thread_count = count
    return stream


class _Container:
    """An output container whose libx264 streams get the fixed thread count."""

    def __init__(self, container, count):
        self._container = container
        self._count = count

    def add_stream(self, codec, *args, **kwargs):
        stream = self._container.add_stream(codec, *args, **kwargs)
        if codec == 'libx264':
            pin(stream, self._count)
        return stream

    def __getattr__(self, name):
        return getattr(self._container, name)


class _Av:
    """The av module, except that av.open hands out containers as above."""

    def __init__(self, av, count):
        self._av = av
        self._count = count

    def open(self, *args, **kwargs):
        container = self._av.open(*args, **kwargs)
        return _Container(container, self._count) if kwargs.get('mode', args[1] if len(args) > 1 else 'r') == 'w' else container

    def __getattr__(self, name):
        return getattr(self._av, name)


def encode_video(*args, count=X264_THREADS, **kwargs):
    """diffusers' encode_video with the libx264 thread count fixed; its arguments and result are unchanged."""
    export = importlib.import_module('diffusers.utils.export_utils')
    original = getattr(export, '_import_av', None)
    if original is None:  # a library version (or a test double) that does not open the file through av
        return export.encode_video(*args, **kwargs)
    with _lock:
        export._import_av = lambda: _Av(original(), count)
        try:
            return export.encode_video(*args, **kwargs)
        finally:
            export._import_av = original
