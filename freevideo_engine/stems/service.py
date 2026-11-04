"""Audio separation for a clip: dialogue, music and ambience written by a one-shot worker under engine.lock.

The weights are the image pack's stems group, downloaded on first use. The worker takes the GPU the way
music does and stops when a video starts (a video is never kept waiting); the caller waits and asks again.
The feature is offered only with an NVIDIA GPU of at least MIN_VRAM_GIB and never on macOS.
"""
import logging
from pathlib import Path
import platform
import subprocess
import tempfile
import time

from ..music.service import Preempted, _Active, _result, _yield_gpu, environment

# An 8 GB card reports about 7.996 GiB, so the bar sits just under 8.
MIN_VRAM_GIB = 7.5
WORKER_SECONDS = 1800
POLL_SECONDS = .2
WEIGHTS = 'stems/bandit_plus_cocktail.pt'


class StemsError(Exception):
    def __init__(self, code, reason=None):
        super().__init__(code)
        self.code, self.reason = code, reason


ACTIVE = _Active()


def stop_for_generation(server=None):
    """Called as a video starts: a separation that is starting or running stops, and engine.lock is free on return."""
    from .. import processes
    with ACTIVE.lock:
        if ACTIVE.idle.is_set():
            return
        ACTIVE.preempted = True
        process = ACTIVE.process
    if process is not None:
        processes.stop(process, grace=5)
    ACTIVE.idle.wait(30)


def gpu_total_gib(uuid=None):
    """Physical memory in GiB of the GPU FreeVideo uses (the first one when its UUID is unknown), or None when unreadable.

    A reading is kept for the session; a failed one is tried again after RETRY_SECONDS, and waits at most 3 s."""
    key = str(uuid or 0)
    kept = _TOTAL.get(key)
    if kept is not None and (kept[0] is not None or time.monotonic() < kept[1]):
        return kept[0]
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=memory.total', '--format=csv,noheader,nounits', '-i', key],
                             capture_output=True, text=True, timeout=3, check=True).stdout
        value = float(out.split()[0]) / 1024
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        value = None
    _TOTAL[key] = (value, time.monotonic() + RETRY_SECONDS)
    return value


RETRY_SECONDS = 30
_TOTAL = {}


def supported(vram_gib, system=None, total=None, uuid=None):
    """Whether the entry is offered: CUDA only, so not on macOS, and enough video memory.

    vram_gib is the limit a user may have set (normally None); the card's own size applies as well."""
    sizes = [v for v in (vram_gib, total if total is not None else gpu_total_gib(uuid)) if v is not None]
    return (system or platform.system()) != 'Darwin' and bool(sizes) and min(sizes) >= MIN_VRAM_GIB


def ready(model_root):
    from ..image_gen import image_pack
    return image_pack.ready(model_root, group='stems')


def install(model_root, engine_root, progress, cancelled):
    from ..image_gen import image_pack

    def check():
        if cancelled():
            raise InterruptedError('cancelled')

    def moved(value):
        if value.get('phase') == 'download' and value.get('total'):
            progress(min(1., value['done'] / value['total']))
    try:
        image_pack.install(model_root, moved, check, engine_root=engine_root, group='stems')
    except image_pack.PackError as error:
        raise StemsError('stems_download_failed', error.reason) from None


def separate(source, out_dir, *, server=None, cancelled=lambda: False, phase=lambda name, fraction=None: None):
    """Write dialogue.wav, music.wav and ambience.wav into out_dir; returns the worker's result.

    Raises Preempted when a video started, InterruptedError when cancelled, StemsError otherwise."""
    from ..comfy_bridge import installation
    root, machine = installation()
    if not ready(machine['model_root']):
        phase('downloading', 0.)
        install(machine['model_root'], root, lambda fraction: phase('downloading', fraction), cancelled)
    if cancelled():
        raise InterruptedError('cancelled')
    phase('separating')
    with ACTIVE.lock:
        ACTIVE.preempted = False
        ACTIVE.idle.clear()
    try:
        return _run(source, out_dir, server, cancelled, root, machine)
    except BlockingIOError:
        raise Preempted() from None
    finally:
        with ACTIVE.lock:
            ACTIVE.process = None
            ACTIVE.idle.set()


def _run(source, out_dir, server, cancelled, root, machine):
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
                owner.record(action='released', reason='stems_model')
            except resident_process.SessionRestarted:
                pass
            env.pop(resident_process.ENV, None)
        command = processes.module_command(
            'freevideo_engine.stems.worker', '--weights', image_pack.base(machine['model_root']) / WEIGHTS,
            '--source', source, '--out', out_dir, '--device', machine.get('device_backend', 'cuda'))
        command[0] = machine['comfy_python']
        env['FREEVIDEO_RUNTIME_LOCK_FD'] = str(descriptor)
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            with ACTIVE.lock:
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
                logging.warning('[FreeVideo] Separation worker failed (exit %s, %s): %s', process.returncode,
                                value.get('error') or 'no result', errors.read().decode('utf-8', 'replace'))
                code = value.get('error')
                raise StemsError('out_of_memory' if code == 'out_of_memory' else code if code == 'no_audio'
                                 else 'stems_failed', code if isinstance(code, str) else 'timeout' if timed_out else 'worker_exit')
            return value
