"""Compile recent request shapes while an interactive session is idle.

The first request after an update, a repair, a new installation or a GPU
driver update compiles its kernels; on a 12 GB Windows laptop that cost 50 s of
a 564 s request. Nobody waits for this compile here. Once ComfyUI has had no
work for START_DELAY seconds (OWED_DELAY when no compile is on record for this
installation), precompile runs at the lowest CPU and disk
priority. A queued prompt stops it within POLL seconds, and a generation, a
prompt rewrite or a calibration stops it before taking the GPU. Kernels
compiled by then stay in the caches, and the request compiles the rest as it
always did.

It runs only before the session's first generation. After that, the resident
encoder holds GPU memory that a request would release, so the plan would no
longer be the request's. An interrupted run leaves the cache receipt intact
and records diagnostic status separately. When the run
was stopped or found no room (background_room), the session waits twice as
long before the next try, up to MAX_DELAY; the next start tries again too.
"""
import logging
from pathlib import Path
import subprocess
import threading
import time

from . import processes
from .long_paths import io_path
from .precompile_state import read, record

START_DELAY = 30.
# A compile that is owed (nothing compiled yet for this code, driver or GPU)
# starts this soon after ComfyUI loads, before the user's first request.
OWED_DELAY = 5.
MAX_DELAY = 600.
POLL = .25
# Longer than the Linux supervisor takes to end every descendant (4.5 s):
# killing it first would orphan a worker in its own session. A tree that
# exits sooner returns as soon as it does.
STOP_GRACE = 6.
# Windows ends a stopped tree asynchronously: CUDA memory and the lease can
# outlive the top process by seconds.
RELEASE_SECONDS = 15.
ATTEMPTS = 8


def compile_owed(root):
    """True when no finished compile on record matches this installation's code.

    Reads only the receipt and the compute sources, without tensors or a GPU
    query; the compile itself decides again with the full identity, so a wrong
    "owed" costs one early start that ends at once with the receipt's skip.
    """
    import json
    try:
        previous = json.loads((Path(root) / 'precompile.json').read_text(encoding='utf-8'))
        identity = previous['identity']
        if not previous.get('success') or not identity:
            return True
        from .tuning import COMPUTE_FILES, digest
        package = Path(__file__).parent
        return (identity.get('source_sha256') != {name: digest(package / name) for name in COMPUTE_FILES}
                or identity.get('dependency_manifest_sha256') != digest(package / 'dependencies.json'))
    except Exception:
        return True


class BackgroundCompile:
    def __init__(self):
        self.guard = threading.Lock()
        self.closed = threading.Event()
        self.process = None
        self.thread = None
        self.lock = None
        self.attempts = []
        self.root = None

    def start(self, command, *, environment, cwd, log, lock, busy, delay=START_DELAY):
        """Run `command` once the session is quiet; False when this session already did.

        `lock` is the engine lease the compile holds while it runs.
        """
        with self.guard:
            if self.thread is not None or self.closed.is_set():
                return False
            self.lock = Path(lock)
            self.root = Path(log).parent
            self.thread = threading.Thread(target=self._work, args=(command, environment, cwd, Path(log), busy, delay),
                                           name='FreeVideo background compile', daemon=True)
            self.thread.start()
            return True

    def stop(self, *, final=False):
        """Stop a running compile and return once its processes, GPU memory and lease are gone.

        `final` when a generation starts: the session compiles nothing more.
        """
        if final:
            self.closed.set()
        tick = time.monotonic()  # One deadline from here, a concurrent stop included.
        with self.guard:
            process, self.process = self.process, None
            if process is None:
                if final and self.thread is not None and self.thread.is_alive():
                    record(self.root, 'not_started', reason='foreground_work', final=True)
                return None
            row = self.attempts[-1]
            if process.poll() is None:
                # Marked before the signal: the worker thread then retries
                # instead of reading the exit as a finished run.
                row['stopped'] = True
                record(self.root, 'not_started', reason='foreground_work', final=final)
                processes.stop(process, grace=STOP_GRACE)
                row['stop_seconds'] = time.monotonic() - tick
            # Every process of the tree holds the lease; once it is free the
            # tree is gone, even when the top process had already exited. On a
            # timeout the request reports the busy lease. One deadline for all.
            row['released'] = self._released(tick + RELEASE_SECONDS)
            row['release_seconds'] = time.monotonic() - tick
            if not row.get('stopped'):
                return None
            logging.info('[FreeVideo] Background compile stopped in %.1f s; the GPU lease is %s.',
                         row['release_seconds'], 'free' if row['released'] else 'still held')
            return dict(row)

    def _released(self, deadline):
        from .locking import runtime_lock
        while True:
            try:
                with runtime_lock(io_path(self.lock), inherit=False):
                    return True
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(.1)

    def _quiet(self, busy, delay):
        """True after `delay` seconds without work; False once closed."""
        since = time.monotonic()
        postponed = False
        while time.monotonic() - since < delay:
            if self.closed.wait(1.):
                return False
            if busy():
                since = time.monotonic()
                if not postponed:
                    record(self.root, 'not_started', reason='session_busy')
                postponed = True
            else:
                postponed = False
        return True

    def _work(self, command, environment, cwd, log, busy, delay):
        # The first wait may be shorter than the retry backoff, which always
        # starts from START_DELAY: a stopped or crowded-out run is not hammered.
        pause = START_DELAY
        self.root = log.parent
        for attempt in range(ATTEMPTS):
            if attempt:
                pause = delay = min(2 * pause, MAX_DELAY)
            # A retry retains the actual pressure/stop reason from its last
            # attempt instead of replacing it with the backoff mechanism.
            record(self.root, 'not_started', reason=None if attempt else 'idle_delay')
            if not self._quiet(busy, delay):
                return
            with self.guard:
                if self.closed.is_set():
                    return
                logging.info('[FreeVideo] Compiling GPU kernels for recent video sizes in the background; '
                             'a generation request stops it.')
                row = dict(started=time.time())
                self.attempts.append(row)
                try:
                    with io_path(log).open('ab') as stream:
                        from .triton_compat import temporary_environment
                        environment = temporary_environment(environment.get('FREEVIDEO_HOME') or self.lock.parent,
                                                            environment)
                        process = self.process = processes.popen(
                            command, env=environment, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stream,
                            stderr=subprocess.STDOUT, start_new_session=True, supervise=True)
                except BaseException:
                    record(self.root, 'unknown')
                    raise
                # Only a started process is running, with its own identity.
                record(self.root, 'running', started_at=row['started'], shapes=[], pid=process.pid)
            while process.poll() is None:
                if busy():
                    self.stop()
                    break
                try:
                    process.wait(timeout=POLL)
                except subprocess.TimeoutExpired:
                    pass
            with self.guard:
                # A stop in progress holds the guard and has marked the row.
                stopped = row.get('stopped')
                if not stopped and self.process is process:
                    # Ended by itself: confirm its tree let go before forgetting
                    # it, so a later stop never waits on someone else's lease.
                    row['released'] = self._released(time.monotonic() + RELEASE_SECONDS)
                    if row['released']:
                        self.process = None
            row.update(returncode=process.poll(), seconds=time.time() - row['started'])
            from .precompile import BUSY_EXIT
            if not stopped and row['returncode'] != BUSY_EXIT:
                receipt, _ = read(self.root / 'precompile.json', 2 * 2**20)
                record(self.root, 'complete' if row['returncode'] == 0 and receipt.get('success') is True
                       else 'unknown')
                return  # Compiled, or failed with a receipt that says why.
            if not stopped:
                # The child normally wrote the resource reason. Keep it even
                # if the child exited before publishing diagnostic status.
                record(self.root, 'not_started')


COMPILE = BackgroundCompile()
