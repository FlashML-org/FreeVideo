"""Keeping the video and picture models loaded together, and server mode.

On Linux the video models and the picture worker stay on the card together by default (co-residency): a picture
leaves the video models in place when the card has room for both, a video no longer stops an idle picture worker,
the picture worker takes the engine lock per job instead of for its whole life and does not leave after an idle
time, and neither counts the other as "another program". Each still lets go when a program outside this
installation needs the card, or host memory runs short. FREEVIDEO_KEEP_MODELS=off turns it off. Other platforms
keep their earlier behaviour until it is measured there, except that on Windows an idle picture worker also keeps
its model with no time limit (``keeps_idle``).

Server mode (FREEVIDEO_SERVER_RESIDENT=1, Linux only; the desktop application never sets it) adds loading the video
model's text encoder and the configured picture model as the server starts, and bringing the picture model back
after a video that needed its room.

Every release or trim is recorded with its reason (``releases``), for the running status. A model that only made
room for another program, or the picture model for a video, comes back once the room is free again (``rewarm``).
"""
import logging
import os
import sys
import threading
import time

ENV = 'FREEVIDEO_SERVER_RESIDENT'
# The picture model loaded at start; empty: H3 Image when it is installed, else Qwen-Image.
IMAGE_MODEL_ENV = 'FREEVIDEO_SERVER_IMAGE_MODEL'
ON = ('1', 'on', 'true', 'yes')
OFF = ('0', 'off', 'false', 'no')
# The idle wait of a picture worker in server mode: it stays until the server stops or something needs the card.
WAIT_SECONDS = 365 * 24 * 3600.


# Set by enable() while the server runs; None follows the environment.
_forced = None


def enabled(environ=None):
    """True when server mode is asked for (by enable() or the environment), on Linux, and keeping models has not
    been turned off."""
    environ = os.environ if environ is None else environ
    if not sys.platform.startswith('linux'):
        return False
    if str(environ.get('FREEVIDEO_KEEP_MODELS', 'auto')).strip().lower() in OFF:
        return False
    if _forced is not None and environ is os.environ:
        return _forced
    return str(environ.get(ENV, '')).strip().lower() in ON


def coresident(environ=None):
    """True when the video models and the picture worker stay loaded together (see the module text): on Linux,
    unless keeping models has been turned off."""
    environ = os.environ if environ is None else environ
    if not sys.platform.startswith('linux'):
        return False
    return str(environ.get('FREEVIDEO_KEEP_MODELS', 'auto')).strip().lower() not in OFF


def keeps_idle(environ=None):
    """True when an idle picture worker keeps its model with no time limit, until another program or the next job
    needs the room: on Linux and Windows, unless keeping models has been turned off. Macs keep the earlier idle
    limits, as the model there shares the Mac's memory with everything else."""
    environ = os.environ if environ is None else environ
    if sys.platform == 'darwin':
        return False
    return str(environ.get('FREEVIDEO_KEEP_MODELS', 'auto')).strip().lower() not in OFF


def enable(on=True):
    """Turn server mode on or off in this process (None: back to the environment's setting). It applies to the next
    picture worker that starts and the next keep decision; a picture worker already running keeps its wait."""
    global _forced
    _forced = None if on is None else bool(on)


def image_model(installed, environ=None):
    """The picture model to load at start: the configured one when it is installed, else H3 Image, else Qwen-Image;
    None when no picture model is installed. ``installed`` is the list of installed model names."""
    environ = os.environ if environ is None else environ
    wanted = str(environ.get(IMAGE_MODEL_ENV, '')).strip()
    if wanted:
        return wanted if wanted in installed else None
    for name in ('minimax-h3-image', 'qwen-image-2.1-turbo'):
        if name in installed:
            return name
    return None


# ── Release events ────────────────────────────────────────────────────────────────────────────────────────────
_mutex = threading.Lock()
_listeners = []
_last_release = None
_keep_notes = {}
# The last releases and trims, newest last, for the running status.
_releases = []
RELEASES_KEPT = 10


def on_release(callback):
    """Call ``callback(event)`` whenever the video or the picture models are released or trimmed; returns a function
    that ends the subscription. ``event``: {'model': 'video' | 'image', 'action': 'released' | 'trimmed' | 'reloaded',
    'reason': str, 'at': unix seconds}. Called on a background thread; it must not block, and what it raises is
    logged and dropped."""
    with _mutex:
        _listeners.append(callback)

    def unsubscribe():
        with _mutex:
            if callback in _listeners:
                _listeners.remove(callback)
    return unsubscribe


def released(model, row):
    """The video worker or the picture worker was released or trimmed (called by them)."""
    global _last_release
    event = dict(model=model, action=row.get('action', 'released'), reason=str(row.get('reason') or 'unknown'),
                 at=float(row.get('at') or time.time()))
    with _mutex:
        _last_release = event
        _releases[:] = (_releases + [event])[-RELEASES_KEPT:]
        listeners = list(_listeners)
    if model == 'image' and event['reason'] == 'video_start' and enabled():
        # Until the picture worker can stay through a video, it comes back once the video is done.
        threading.Thread(target=_after_video, name='freevideo-server-rewarm', daemon=True).start()
    if event['action'] == 'released':
        try:
            from . import rewarm
            rewarm.after_release(model, event['reason'], row.get('held'))
        except Exception:
            logging.warning('[FreeVideo] Could not wait to bring the %s model back', model, exc_info=True)
    for callback in listeners:
        try:
            callback(dict(event))
        except Exception:
            logging.warning('[FreeVideo] A server release listener failed', exc_info=True)


def reloaded(model, reason):
    """``model`` was loaded again after a release for ``reason`` (by rewarm), recorded beside the releases."""
    event = dict(model=model, action='reloaded', reason=str(reason or 'unknown'), at=time.time())
    with _mutex:
        _releases[:] = (_releases + [event])[-RELEASES_KEPT:]
        listeners = list(_listeners)
    for callback in listeners:
        try:
            callback(dict(event))
        except Exception:
            logging.warning('[FreeVideo] A server release listener failed', exc_info=True)


# How often the picture model's return waits for the video to finish, and for how long at most.
REWARM_POLL_SECONDS = 2.
REWARM_LIMIT_SECONDS = 6 * 3600.


def _after_video(clock=time.monotonic, sleep=time.sleep):
    from .resident_process import OWNER
    deadline = clock() + REWARM_LIMIT_SECONDS
    sleep(REWARM_POLL_SECONDS)
    while clock() < deadline and enabled():
        if not OWNER.active and not state()['busy']:
            answer = warm(models=('image',))
            if answer['state'] != 'skipped' or answer['reason'] != 'busy':
                return answer
        sleep(REWARM_POLL_SECONDS)
    return None


def releases():
    """The last releases and trims of the video and picture models, newest last: [{'model', 'action', 'reason',
    'at'}]."""
    with _mutex:
        return [dict(event) for event in _releases]


def note_keep(model, kept, **numbers):
    """Remember the last keep-or-trim decision of a side, for state()."""
    with _mutex:
        _keep_notes[model] = dict(numbers, kept=bool(kept), at=time.time())


# ── Loading at start ──────────────────────────────────────────────────────────────────────────────────────────
_warm = dict(thread=None, started=None, ready_seconds=None, models=())


def _services():
    """(video warmer, picture service) of this server; either may be None before registration."""
    from . import video_warm
    try:
        from .image_gen.service import get_service
        images = get_service()
    except Exception:
        images = None
    return video_warm.warmer(), images


def warm(models=('video', 'image'), policy='resident'):
    """Start loading the models in the background and return at once: {'state': 'loading' | 'ready' | 'skipped' |
    'off', 'reason': str | None}. The video side places the text encoder on the card in the resident worker and reads
    the video model's files ahead (the first video moves them onto the card); the picture side loads the picture
    model. Calling again while it loads or once it is ready changes nothing."""
    if policy != 'resident' or not set(models) <= {'video', 'image'}:
        raise ValueError('invalid_request')
    if not enabled():
        return dict(state='off', reason=None)
    with _mutex:
        thread = _warm['thread']
        if thread is not None and thread.is_alive():
            return dict(state='loading', reason=None)
    now = state()
    if now['state'] == 'ready' and set(models) <= set(_warm['models'] or models):
        return dict(state='ready', reason=None)
    if now['busy']:
        return dict(state='skipped', reason='busy')
    try:
        from .resident_process import host_margin
        from .background_room import available_memory
        if available_memory() < host_margin():
            return dict(state='skipped', reason='host_memory_low')
    except (OSError, RuntimeError, ValueError, ImportError):
        pass
    videos, images = _services()
    if videos is None:
        return dict(state='skipped', reason='not_started')
    with _mutex:
        _warm.update(started=time.monotonic(), ready_seconds=None, models=tuple(models))
        _warm['thread'] = threading.Thread(target=_load, args=(tuple(models), videos, images),
                                           name='freevideo-server-warm', daemon=True)
        _warm['thread'].start()
    return dict(state='loading', reason=None)


def _load(models, videos, images):
    try:
        if 'video' in models:
            answer = videos.warm(dict(level='full', mode='eager'))
            if answer.get('warm') == 'loading' and videos.thread is not None:
                videos.thread.join()
        if 'image' in models and images is not None:
            try:
                info = images.installed_models()
            except Exception:
                info = ()
            model = image_model(info)
            if model is not None:
                images.warm(dict(model=model, level='full', mode='eager'))
                images.wait_warm(600.)
    except Exception:
        logging.warning('[FreeVideo] Server warm-up failed', exc_info=True)
    finally:
        with _mutex:
            if _warm['started'] is not None:
                _warm['ready_seconds'] = round(time.monotonic() - _warm['started'], 1)


# ── What is loaded ────────────────────────────────────────────────────────────────────────────────────────────
# The last models the resident worker listed, with its process id.
_held = []
VIDEO_ROLES = dict(text_encoder=('encoder',), video=('engine',), vae=('video_vae', 'audio_vae'))


def _where(rows, file_cache):
    if not rows:
        return dict(where='file_cache' if file_cache else 'none', gpu_bytes=None)
    on_card = sum(int(row.get('gpu_bytes') or 0) for row in rows)
    return dict(where='gpu' if on_card > 0 else 'host', gpu_bytes=on_card)


def state():
    """Where each model is now, the last release and whether a job runs; quick enough to call every few seconds."""
    from . import resident_process
    owner = resident_process.OWNER
    held = []
    alive = owner.process is not None and owner.process.poll() is None and owner.endpoint
    if alive and not owner.active:
        answer = resident_process.status({resident_process.ENV: str(owner.endpoint)})
        with _mutex:
            if isinstance(answer, dict):
                _held[:] = [(owner.process.pid, row) for row in answer.get('models') or [] if isinstance(row, dict)]
            # A worker busy with a placement does not answer: its last answer stands.
            held = [row for pid, row in _held if pid == owner.process.pid]
    videos, images = _services()
    warmed = bool(alive) and videos is not None and isinstance(videos.last, dict) and videos.last.get('video_files') in ('reading', 'skipped')
    models = {name: _where([row for row in held if row.get('role') in roles], warmed and name != 'text_encoder')
              for name, roles in VIDEO_ROLES.items()}
    if owner.active:
        models = {name: dict(where='gpu', gpu_bytes=None) for name in VIDEO_ROLES}
    picture = dict(where='none', gpu_bytes=None, model=None)
    busy = bool(owner.active)
    try:
        # The video queue (a video waiting or running) and the picture service.
        busy = busy or bool(videos is not None and videos.busy())
    except Exception:
        busy = True
    if images is not None:
        try:
            loaded = images.loaded_model if images.worker_alive() else None
            busy = busy or images.busy()
        except Exception:
            loaded = None
        if loaded:
            picture = dict(where='gpu', gpu_bytes=None, model=loaded)
    models['image'] = picture
    wanted = _warm['models'] or ('video', 'image')
    names = [name for name in models if name != 'image' or 'image' in wanted]
    present = [models[name]['where'] in ('gpu', 'file_cache') for name in names]
    with _mutex:
        last, seconds, loading = _last_release, _warm['ready_seconds'], (
            _warm['thread'] is not None and _warm['thread'].is_alive())
    overall = ('off' if not enabled() else 'loading' if loading else 'ready' if all(present)
               else 'released' if not any(present) else 'partial')
    return dict(mode='resident' if enabled() else 'desktop', state=overall, models=models,
                last_release=dict(last) if last else None, ready_seconds=seconds, busy=busy)
