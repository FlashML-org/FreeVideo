"""Expose ComfyUI's process output to the local embedded terminal."""
import atexit
import logging
import os
from pathlib import Path
import sys
import threading
import time


class Stream:
    def __init__(self, original, capture):
        self.original, self.capture = original, capture

    def write(self, value):
        try:
            if self.original is not None:
                self.original.write(value)
        except (OSError, ValueError):
            pass
        finally:
            self.capture.write(value)
        return len(value)

    def flush(self):
        if self.original is not None:
            try: self.original.flush()
            except (OSError, ValueError): pass

    def isatty(self):
        return False  # Use readable counters instead of terminal cursor controls.

    def __getattr__(self, name):
        return getattr(self.original, name)


class Capture:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.stream = self.path.open('a', encoding='utf-8', buffering=1)
        self.stdout, self.stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = Stream(self.stdout, self), Stream(self.stderr, self)
        self.handlers = []
        loggers = [logging.getLogger()] + [v for v in list(logging.Logger.manager.loggerDict.values()) if isinstance(v, logging.Logger)]
        for logger in loggers:
            for handler in logger.handlers:
                previous = getattr(handler, 'stream', None)
                if previous is not None and (previous is self.stdout or previous is self.stderr):
                    self.handlers.append((handler, previous))
                    handler.stream = sys.stdout if previous is self.stdout else sys.stderr
        self.handler = None
        root = logging.getLogger()
        if not any(handler in root.handlers for handler, _ in self.handlers):
            # A console handler on a stream that was replaced before this capture (for example by
            # ComfyUI-Manager's stdout/stderr wrappers) still prints; only the log file misses its records.
            printing = any(isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
                           and getattr(handler, 'stream', None) is not None for handler in root.handlers)
            self.handler = logging.StreamHandler(self if printing else sys.stderr)
            root.addHandler(self.handler)

    def write(self, value):
        with self.lock:
            try:
                self.stream.write(value)
                self.stream.flush()
            except (OSError, ValueError):
                pass  # A failed log disk must not crash another node's execution.

    def close(self):
        if isinstance(sys.stdout, Stream) and sys.stdout.capture is self: sys.stdout = self.stdout
        if isinstance(sys.stderr, Stream) and sys.stderr.capture is self: sys.stderr = self.stderr
        for handler, original in self.handlers:
            if isinstance(handler.stream, Stream) and handler.stream.capture is self:
                handler.stream = original
        if self.handler is not None: logging.getLogger().removeHandler(self.handler)
        self.stream.close()


def install(engine):
    root = Path(engine).resolve() / 'launcher/comfy-runs'
    managed = os.environ.get('FREEVIDEO_COMFY_LOG')
    if managed:
        # Only the launcher's own output file is reusable. A value left by something else (a user's own
        # start script, another tool) must not stop ComfyUI from starting: capture on our own instead.
        try:
            path = Path(managed).resolve()
            path.relative_to(root.resolve())
            return path
        except (OSError, ValueError):
            pass
    capture = getattr(sys, '_freevideo_console', None)
    if capture is None:
        path = root / ('connected-%s-%s' % (time.time_ns(), os.getpid())) / 'comfy.log'
        capture = Capture(path)
        sys._freevideo_console = capture
        atexit.register(capture.close)
    return capture.path
