"""Music for a cut: one ACE-Step piece made in a one-shot worker after the video model hands back the GPU.

The weights are the image pack's music group, downloaded on first use with the engine's sources
(including ModelScope). The worker takes engine.lock the way a picture does: ComfyUI's idle models and
the resident video cache are released first, and the video model loads again with the next video. A
video that starts meanwhile stops the worker (a video is never kept waiting); the caller waits for it
and composes again.
"""
import json
import logging
from pathlib import Path
import subprocess
import tempfile
import threading
import time

MIN_SECONDS, MAX_SECONDS = 10, 600
WORKER_SECONDS = 1800
POLL_SECONDS = .2


class MusicError(Exception):
    def __init__(self, code, reason=None):
        super().__init__(code)
        self.code, self.reason = code, reason


class Preempted(Exception):
    """A video started; nothing was kept."""


class _Active:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.preempted = False
        self.idle = threading.Event()
        self.idle.set()


ACTIVE = _Active()


def stop_for_generation(server=None):
    """Called as a video starts: music that is starting or running stops, and engine.lock is free on return."""
    from .. import processes
    with ACTIVE.lock:
        if ACTIVE.idle.is_set():
            return
        ACTIVE.preempted = True
        process = ACTIVE.process
    if process is not None:
        processes.stop(process, grace=5)
    ACTIVE.idle.wait(30)


def ready(model_root):
    from ..image_gen import image_pack
    return image_pack.ready(model_root, group='music')


def install(model_root, engine_root, progress, cancelled):
    """Download what is missing; progress gets a fraction. PackError reasons become music_download_failed."""
    from ..image_gen import image_pack
    def check():
        if cancelled():
            raise InterruptedError('cancelled')
    def moved(value):
        if value.get('phase') == 'download' and value.get('total'):
            progress(min(1., value['done'] / value['total']))
    try:
        image_pack.install(model_root, moved, check, engine_root=engine_root, group='music')
    except image_pack.PackError as error:
        raise MusicError('music_download_failed', error.reason) from None


def environment(root):
    from ..comfy_bridge import source_root
    from ..comfy_environment import isolated_environment
    from .. import processes, resident_process
    env = isolated_environment(root, source_root())
    for name in ('FREEVIDEO_RUNTIME_LOCK_FD', processes.HANDLE_ENV, resident_process.ENV,
                 'FREEVIDEO_PROMPT_PRESTART_WORKER'):
        env.pop(name, None)
    return env


def _yield_gpu(server):
    """What a picture does before it starts: stop idle work and unload ComfyUI's own models."""
    from ..background_compile import COMPILE
    from ..encoder_prewarm import IDLE
    from ..image_gen.service import release_idle_worker
    from .. import resident_process
    COMPILE.stop()
    if server is not None:
        release_idle_worker(server)
    if resident_process.OWNER.active:
        raise Preempted()
    resident_process.OWNER.stop_prewarm()
    IDLE.stop()
    if server is not None:
        from ..comfy_residency import release_native
        try:
            released = release_native(server)
            if released:
                logging.info('[FreeVideo] Unloaded ComfyUI models before the music: %s', released)
        except Exception:  # only frees memory early; the worker places what still fits
            logging.warning('[FreeVideo] Could not unload ComfyUI models before the music', exc_info=True)


def _result(stream):
    stream.seek(0)
    lines = [line for line in stream.read().decode('utf-8', 'replace').splitlines() if line.startswith('{')]
    try:
        value = json.loads(lines[-1])
        return value if isinstance(value, dict) else {}
    except (IndexError, ValueError):
        return {}


def compose(out, caption, frames, *, seed, quiet=(), fps=24, server=None, cancelled=lambda: False,
            phase=lambda name, fraction=None: None):
    """Write the finished piece (exact length, levelled, faded; MP4 or WAV by out's suffix) to out; returns the worker's numbers.

    Raises Preempted when a video started, InterruptedError when cancelled, MusicError otherwise."""
    from ..comfy_bridge import installation
    root, machine = installation()
    seconds = frames / fps
    if seconds > MAX_SECONDS:
        raise MusicError('invalid_request', 'too_long')
    if not ready(machine['model_root']):
        phase('downloading', 0.)
        install(machine['model_root'], root, lambda fraction: phase('downloading', fraction), cancelled)
    if cancelled():
        raise InterruptedError('cancelled')
    phase('composing')
    with ACTIVE.lock:
        ACTIVE.preempted = False
        ACTIVE.idle.clear()
    try:
        return _run(out, caption, frames, seconds, seed, quiet, fps, server, cancelled, root, machine)
    except BlockingIOError:
        raise Preempted() from None  # other GPU work holds engine.lock; the caller waits and tries again
    finally:
        with ACTIVE.lock:
            ACTIVE.process = None
            ACTIVE.idle.set()


def _run(out, caption, frames, seconds, seed, quiet, fps, server, cancelled, root, machine):
    from ..image_gen import image_pack
    from ..locking import runtime_lock
    from .. import processes, resident_process
    _yield_gpu(server)
    with runtime_lock(Path(root) / 'engine.lock', inherit=False, wait_seconds=10) as descriptor:
        env = environment(root)
        owner = resident_process.OWNER
        if owner.process is not None and owner.process.poll() is None and owner.endpoint:
            env[resident_process.ENV] = str(owner.endpoint)
            try:
                resident_process.release_idle_cache(env, descriptor)
                owner.record(action='released', reason='music_model')
            except resident_process.SessionRestarted:
                pass  # That worker already exited, which is the release we need.
            env.pop(resident_process.ENV, None)
        command = processes.module_command(
            'freevideo_engine.music.worker', '--root', image_pack.base(machine['model_root']) / 'music',
            '--out', out, '--caption', caption, '--seconds', max(MIN_SECONDS, seconds), '--seed', seed,
            '--frames', frames, '--fps', fps, '--quiet', json.dumps([list(span) for span in quiet]),
            '--device', machine.get('device_backend', 'cuda'))
        command[0] = machine['comfy_python']
        env['FREEVIDEO_RUNTIME_LOCK_FD'] = str(descriptor)
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            with ACTIVE.lock:
                # A video that started while the GPU was being freed gets it first.
                if ACTIVE.preempted:
                    raise Preempted()
                process = processes.popen(command, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                          pass_fds=(descriptor,), supervise=True, start_new_session=True)
                ACTIVE.process = process
            began, timed_out = time.monotonic(), False
            try:
                while process.poll() is None:
                    timed_out = time.monotonic() - began > WORKER_SECONDS
                    if cancelled() or timed_out:
                        break
                    time.sleep(POLL_SECONDS)
            finally:
                # Keep the lease until the whole worker tree has exited.
                if process.poll() is None:
                    processes.stop(process, grace=5)
                process.wait()
                with ACTIVE.lock:
                    ACTIVE.process = None
                    preempted = ACTIVE.preempted
            if preempted:
                raise Preempted()
            if cancelled():
                raise InterruptedError('cancelled')
            value = _result(output)
            if not value.get('ok'):
                errors.seek(0, 2)
                errors.seek(max(0, errors.tell() - 4000))
                logging.warning('[FreeVideo] Music worker failed (exit %s, %s): %s', process.returncode,
                                value.get('error') or 'no result', errors.read().decode('utf-8', 'replace'))
                code = value.get('error')
                raise MusicError('out_of_memory' if code == 'out_of_memory' else 'music_failed',
                                 code if isinstance(code, str) else 'timeout' if timed_out else 'worker_exit')
            return value
