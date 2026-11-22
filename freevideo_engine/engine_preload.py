"""The video model on the card before the next video asks for it.

Every warm-up (the start of the app, a return after a release, the creation panel) put only the text encoder back on
the card: the first video after any of them still loaded the video model itself. Now the settings that decide whether
a loaded video model can serve a request (``ModelBank.engine_parts``: the model pack and LoRAs, the size and length,
the execution options the planner chose) are kept from the last successful video, and the warm-up loads the video
model with them into the resident worker, once the encoder is in place:

- only into room that is free: the card holds the model beside everything on it now, and host memory holds the
  weights it keeps there beside the next encoder's start, as after a video (``ModelBank.settle``); nothing else is
  released for it;
- a next video with the same settings uses it (a hit); one with other settings loads its own, as before (a miss);
- a video or a picture that starts stops a preload under way, as it stops the encoder's placement.

Each preload, hit and miss is logged and kept in the worker's status (``preloads``) with what differed.

Nothing derived from a prompt, picture or seed is stored: only the model pack and input-cache folders, the size and
length, the planner's options and the card bytes the model took.
"""
import json
import os
from pathlib import Path
import time

VERSION = 1
NAME = 'last-video-model.json'
# The request fields a video model is built from (see worker.generate), none of them from a prompt, picture or seed.
KEPT = ('cache', 'input_cache_dir', 'engine_options', 'geometry', 'gpu_budget_bytes', 'gpu_reserve_bytes',
        'ram_budget_bytes', 'ram_budget_is_estimate', 'sampling_gpu_limit_bytes')
# Card room left beside a preloaded model: the allocator's own slack and another program's small growth.
CARD_SPARE_BYTES = 1 << 30
CAPABILITY = 'engine-preload'


def path(root=None):
    override = os.environ.get('FREEVIDEO_LAST_VIDEO_MODEL')
    if override:
        return Path(override)
    if root is None:
        from .system import install_root
        root = install_root()
    return Path(root) / '.freevideo' / NAME


def remember(request, card_bytes, allocator, root=None):
    """Keep what the video model of a successful ``request`` was built from; ``card_bytes`` is what it took on the
    card. A preview, an upscale, a continuation or a sound redo is kept like any other video: it is the last one."""
    if type(card_bytes) is not int or card_bytes <= 0 or not isinstance(request.get('engine_options'), dict):
        return None
    value = {name: request[name] for name in KEPT if name in request}
    value.update(version=VERSION, card_bytes=card_bytes, allocator=allocator or '', saved_at=time.time())
    target = path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    os.replace(temporary, target)
    return value


def recall(root=None):
    """The kept settings, or None when there are none or they cannot be read."""
    try:
        value = json.loads(path(root).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if (not isinstance(value, dict) or value.get('version') != VERSION or not isinstance(value.get('cache'), str)
            or not isinstance(value.get('engine_options'), dict) or not isinstance(value.get('geometry'), dict)
            or type(value.get('gpu_budget_bytes')) is not int or type(value.get('card_bytes')) is not int
            or value['card_bytes'] <= 0):
        return None
    return value


def differences(before, after):
    """What differs between two ``ModelBank.engine_parts``: 'cache', 'inputs', 'options.<name>', 'geometry.<name>'."""
    found = []
    for part in ('cache', 'inputs', 'options', 'geometry'):
        old, new = before.get(part), after.get(part)
        if isinstance(old, dict) and isinstance(new, dict) and part in ('options', 'geometry'):
            found += ['%s.%s' % (part, name) for name in sorted(set(old) | set(new)) if old.get(name) != new.get(name)]
        elif old != new:
            found.append(part)
    return found


def place(request, bank, factory, *, free_bytes, host_fits):
    """In the resident worker: load the video model ``request`` describes into ``bank`` when it fits beside
    everything held now. ``free_bytes`` is the card's free room (with the worker's own unused reserve) and
    ``host_fits(bytes)`` whether host memory takes that much more beside the worker. Returns what happened."""
    from .resident_models import GiB, gpu_bytes
    parts = bank.engine_parts(request)
    identity = bank.engine_key(request)
    entry = bank.entries.get('engine')
    if entry is not None and entry['identity'] == identity:
        bank.preloaded = dict(parts=parts, at=time.time())
        return dict(state='kept', reason='The video model with these settings is already loaded')
    need = int(request['card_bytes']) + CARD_SPARE_BYTES
    if entry is not None:
        # The model loaded now was built for other settings: its room counts once it goes.
        free_bytes += gpu_bytes(entry['model'])
    if free_bytes < need:
        return dict(state='skipped', reason='no_card_room', need_bytes=need, free_bytes=int(free_bytes))
    pinned = int(float(request['engine_options'].get('pin_host_gb') or 0) * 1e9)
    # As settle keeps a model after a video: its host weights beside the next encoder's start.
    if not host_fits(8 * GiB + pinned):
        return dict(state='skipped', reason='no_host_room', pinned_bytes=pinned)
    if entry is not None:
        bank.drop('engine', 'Replaced by the video model of the last video settings')
    started = time.perf_counter()
    engine = factory()
    bank.put('engine', identity, engine)
    bank.preloaded = dict(parts=parts, at=time.time())
    return dict(state='loaded', seconds=round(time.perf_counter() - started, 2), gpu_bytes=gpu_bytes(engine))


def worker_request(value, *, metrics, cancel):
    """The resident worker's request for a preload of the kept ``value`` (from recall)."""
    request = {name: value[name] for name in KEPT if name in value}
    request.update(engine_preload=True, card_bytes=value['card_bytes'], metrics=str(metrics), cancel=str(cancel))
    return request
