"""Bringing a model back once the room it gave up is free again.

Where idle models stay loaded (``server_mode.keeps_idle``: Linux and Windows), a model lets go when another program
needs the card or host memory runs short, the picture model when a video needs its room, and the video model when a
picture needs its room (the encoder stays). Where both do not fit, the one used last keeps the card: the other waits
for room beside it. Without this, it stayed
unloaded until the next job asked for it and paid the whole load then. Now, once that room is free again and has
stayed free for QUIET_SECONDS, the same model is loaded in the background as a warm-up would load it:

- only into room that is free: the card holds the model's files and the picture reserve beside everything on it now,
  and host memory holds what the model's worker took the last time it held the model, plus the least host memory
  that worker keeps free (below it the worker lets go again at once); the other model is never trimmed to make room
  for it;
- never while a video, a picture or a warm-up runs, or once the model is loaded again by other means;
- after a quiet time that doubles with each return of the same model in the last hour (QUIET_SECONDS, then twice
  that, up to QUIET_MOST_SECONDS), so a program that comes and goes does not make it load over and over, yet the
  model still comes back; and only for LIMIT_SECONDS after the release.

Each return is recorded beside the releases (``server_mode.releases``) with action 'reloaded'. Macs and
FREEVIDEO_KEEP_MODELS=off keep the earlier behaviour. In server mode the picture model's return after a video stays
with server_mode, which loads the configured model.
"""
import logging
import os
import threading
import time

POLL_SECONDS = 2.
QUIET_SECONDS = 30.
LIMIT_SECONDS = 6 * 3600.
# The longest quiet time, reached after several returns within the hour.
QUIET_MOST_SECONDS = 15 * 60.
HOST_SPARE_BYTES = 1 << 30
# The releases that only made room: the model would have stayed. Others (closed, worker_exit, finished, a job's own
# trim, an unreadable card) are not undone.
REASONS = dict(image=('crowded', 'video_start'), video=('other_program_gpu', 'host_memory_low', 'image_model'))
# A warm-up that did not start because a job got there first: try again once it is done.
BUSY = ('busy', 'video_busy')

_mutex = threading.Lock()
_generation = {}
_reloads = {}


def after_release(model, reason, held=None, host_bytes=None):
    """``model`` ('video' or 'image') was released for ``reason``; ``held`` is the picture model it held and
    ``host_bytes`` the host memory its worker took while it held it (None: not known). Starts the wait to bring it
    back and returns the thread, or None when this release is not one to undo."""
    from . import server_mode
    if reason not in REASONS.get(model, ()) or not server_mode.keeps_idle():
        return None
    if model == 'image' and (not held or (reason == 'video_start' and server_mode.enabled())):
        return None
    probe = Probe(model, held, reason, host_bytes)
    if not probe.ready():
        return None
    with _mutex:
        generation = _generation[model] = _generation.get(model, 0) + 1
    thread = threading.Thread(target=watch, args=(probe, generation), name='freevideo-rewarm-' + model, daemon=True)
    thread.start()
    return thread


def quiet_seconds(model, now):
    """How long the room must stay free and still before ``model`` comes back: QUIET_SECONDS, doubled for each
    return of it in the last hour, at most QUIET_MOST_SECONDS."""
    with _mutex:
        recent = [at for at in _reloads.get(model, ()) if now - at < 3600.]
        _reloads[model] = recent
    return min(QUIET_SECONDS * 2 ** len(recent), QUIET_MOST_SECONDS)


def _count(model, now):
    with _mutex:
        _reloads.setdefault(model, []).append(now)


def watch(probe, generation, *, clock=time.monotonic, sleep=time.sleep):
    """Wait for quiet room, then load; returns why it ended: 'reloaded', 'replaced' (a newer release of the same
    model waits instead), 'off', 'loaded', 'skipped', 'expired'."""
    from .resident_process import OTHER_GPU_GROWTH
    model = probe.model
    deadline = clock() + LIMIT_SECONDS
    since = used = None
    while clock() < deadline:
        sleep(POLL_SECONDS)
        with _mutex:
            if _generation.get(model) != generation:
                return 'replaced'
        try:
            if not probe.keeping():
                return 'off'
            if probe.loaded():
                return 'loaded'
            now_used = None if probe.busy() else probe.room()
        except Exception:
            logging.warning('[FreeVideo] Reading the room for the %s model failed', model, exc_info=True)
            now_used = None
        now = clock()
        if now_used is None:
            since = used = None
            continue
        # The card's use must hold still: another program still starting or leaving is not room yet.
        if since is None or abs(now_used - used) >= OTHER_GPU_GROWTH:
            since, used = now, now_used
            continue
        if now - since < quiet_seconds(model, now):
            continue
        try:
            answer = probe.load() or {}
        except Exception:
            logging.warning('[FreeVideo] Bringing back the %s model failed', model, exc_info=True)
            return 'skipped'
        if answer.get('warm') == 'skipped':
            if answer.get('reason') in BUSY:
                since = used = None
                continue
            return 'skipped'
        _count(model, now)
        from . import server_mode
        server_mode.reloaded(model, probe.reason)
        return 'reloaded'
    return 'expired'


class Probe:
    """What the wait reads and does for one model; the tests replace it."""
    def __init__(self, model, held=None, reason=None, host_bytes=None):
        self.model, self.held, self.reason = model, held, reason
        self.host_bytes = host_bytes if type(host_bytes) is int and host_bytes > 0 else 0

    def services(self):
        from . import server_mode
        return server_mode._services()

    def ready(self):
        videos, images = self.services()
        return (videos if self.model == 'video' else images) is not None

    def keeping(self):
        from . import server_mode
        return server_mode.keeps_idle()

    def busy(self):
        from .resident_process import OWNER
        videos, images = self.services()
        try:
            # A warm-up under way (either model) counts: two loads never measure the same free room.
            return bool(OWNER.active or (videos is not None and (videos.busy() or videos.alive()))
                        or (images is not None and (images.busy() or images.loading_full)))
        except Exception:
            return True

    def loaded(self):
        from .resident_process import OWNER
        if self.model == 'video':
            if OWNER.process is None or OWNER.process.poll() is not None:
                return False
            # With settings to preload, the video model itself, not only the worker with its encoder.
            return self.preload() is None or 'engine' in self.held_roles()
        _, images = self.services()
        return images.worker_alive() and images.loaded_model is not None

    def preload(self):
        if self.model != 'video':
            return None
        from . import engine_preload
        return engine_preload.recall()

    def held_roles(self):
        """The models the video worker holds; empty while it is busy or cannot be read."""
        from . import resident_process
        try:
            answer = resident_process.status({resident_process.ENV: str(resident_process.OWNER.endpoint)})
        except Exception:
            return set()
        return {row.get('role') for row in (answer or {}).get('models') or [] if isinstance(row, dict)}

    def machine(self):
        videos, images = self.services()
        return (videos if self.model == 'video' else images).installation()[1]

    def need(self, machine):
        """Card bytes the model needs free: its files plus the picture reserve."""
        from .image_gen import image_model, keep
        extra = 0
        if self.model == 'video':
            from .video_warm import encoder_file
            files = [] if 'encoder' in self.held_roles() else encoder_file(machine)
            value = self.preload()
            extra = value['card_bytes'] if value else 0
        else:
            _, images = self.services()
            files = images.model_files(machine, self.held)
        size = sum(os.path.getsize(item) for item in files) + extra
        return size + keep.RESERVE_BYTES if size > 0 else None

    def host_least(self):
        """Host memory the model's worker keeps free once it holds the model: below it, it lets go again."""
        from .resident_process import host_margin
        if self.model == 'video':
            value = self.preload()
            if value:
                from .resident_models import GiB
                # The video model's host weights beside the next encoder's start, as the worker settles after a video.
                pinned = int(float(value['engine_options'].get('pin_host_gb') or 0) * 1e9)
                return max(host_margin(), 8 * GiB + pinned)
            return host_margin()
        from .image_gen import image_model, keep
        if self.held == image_model.H3:
            from .image_gen.h3_pipeline import host_floor
            return max(host_margin(), host_floor())
        return max(host_margin(), keep.HOST_LEAST_BYTES)

    def room(self):
        """The card's used bytes when the model fits now beside everything on it, else None."""
        from .background_room import available_memory
        from .resident_process import OWNER, gpu_memory
        if available_memory() < self.host_bytes + self.host_least() + HOST_SPARE_BYTES:
            return None
        machine = self.machine()
        if self.model == 'image' and OWNER.process is not None and OWNER.process.poll() is None:
            # A picture session gives the kept video models back unless they fit beside it: never take them.
            _, images = self.services()
            if images.takes_video_room(machine, self.held):
                return None
        memory = gpu_memory(machine.get('gpu_uuid'))
        need = self.need(machine)
        if not memory or need is None or memory[1] - memory[0] < need:
            return None
        return memory[0]

    def load(self):
        videos, images = self.services()
        if self.model == 'video':
            return videos.warm(dict(level='full', mode='eager'))
        return images.warm(dict(model=self.held, level='full', mode='eager'))
