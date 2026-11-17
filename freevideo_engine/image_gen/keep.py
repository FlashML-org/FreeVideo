"""How long an idle image worker keeps the model on the card.

A worker that made a picture keeps the model for the next one, and lets go at once when another program starts
taking card memory or the host runs short of memory; the next job that needs the room stops it as well. Where
``server_mode.keeps_idle`` (Linux and Windows, unless keeping models is off) there is no time limit. Elsewhere
KEEP_SECONDS is a long safety limit for a model whole on the card, and other models wait IDLE_SECONDS.
"""

import time

from .. import usage_habit

IDLE_SECONDS = 45.
KEEP_SECONDS = 1800.
# The long wait of earlier versions, still given to a model that had it when no reserve stays free.
LEGACY_SECONDS = 600.
# Operations looked at (two thirds of them must be pictures), and how few are enough to judge at all.
WINDOW = 6
MINIMUM = 3
STORED = usage_habit.OPS_STORED
# Card memory other programs may take while the worker waits before it lets go.
YIELD_BYTES = 1 << 30
# With no reserve free on the card (keeps_idle), a smaller take by another program already ends the wait: on a full
# card the other program would otherwise have to make do with what is left.
TIGHT_YIELD_BYTES = 256 << 20
# The wait of a worker that keeps its model until something needs the room (no time limit).
STAY_SECONDS = 365 * 24 * 3600.
# The last words of a worker that left because another program needed the card or host memory.
CROWDED_LINE = 'image worker leaving: another program needs the memory'
# Host memory other programs may take while the worker waits, and the least host memory left, before it lets go.
HOST_YIELD_BYTES = 1 << 30
# Card memory the model must leave free to be kept at all, so another program can still take that much at once.
RESERVE_BYTES = 8 << 30
RESERVE_FRACTION = .15
HOST_LEAST_BYTES = 3 << 30
POLL_SECONDS = .5
# How long a worker the page warmed waits after the page's last warm-up call (the page calls again while it is used).
WARM_SECONDS = 120.


def path(root=None):
    """Where the habit is kept: the usage habit file under the installation directory."""
    return usage_habit.path(root)


def load(file=None):
    """The last operations ('image' or 'video'), oldest first; empty when the file cannot be trusted."""
    return usage_habit.ops(file)


def note(kind, file=None):
    """Remember one operation. Only the kind is stored, nothing from the request."""
    usage_habit.note_op(kind, file)


def mostly_images(file=None):
    """True when most of the recent operations were pictures and the last one was too."""
    recent = load(file)[-WINDOW:]
    return len(recent) >= MINIMUM and recent[-1] == 'image' and recent.count('image') * 3 >= len(recent) * 2


def keep_seconds(file=None):
    """The idle limit the worker is asked to use for the request being sent: always the long one, which the worker
    applies only to a model that is whole on the card (the others keep IDLE_SECONDS) and gives up on demand."""
    return KEEP_SECONDS


def card_free_bytes(device):
    """Free memory of the card the worker's model is on, or None when it cannot be read."""
    torch = __import__('sys').modules.get('torch')
    if torch is None or getattr(device, 'type', str(device).split(':')[0]) != 'cuda':
        return None
    try:
        return int(torch.cuda.mem_get_info(device)[0])
    except Exception:
        return None


def card_memory(device):
    """(free, total) bytes of the card the model is on, or None when it cannot be read."""
    torch = __import__('sys').modules.get('torch')
    if torch is None or getattr(device, 'type', str(device).split(':')[0]) != 'cuda':
        return None
    try:
        free, total = torch.cuda.mem_get_info(device)
        return int(free), int(total)
    except Exception:
        return None


def card_has_room(device):
    """True while the model leaves at least RESERVE_BYTES, or RESERVE_FRACTION of the card, free for other programs.

    The allocator's cached memory is handed back first, so only what the model really holds counts."""
    torch = __import__('sys').modules.get('torch')
    try:
        torch.cuda.empty_cache()
    except Exception:
        pass
    memory = card_memory(device)
    return memory is not None and memory[0] >= max(RESERVE_BYTES, memory[1] * RESERVE_FRACTION)


def own_process_ids():
    """This process and everything it started."""
    import os
    ids = {os.getpid()}
    try:
        import psutil
        ids.update(child.pid for child in psutil.Process().children(recursive=True))
    except Exception:
        pass
    return ids


_NVML = {}


def _nvml_card(device):
    """(library, handle) of the card the worker's model is on, opened once; None when NVML is missing or the card
    cannot be told apart from others (it is selected by UUID, never by index)."""
    key = str(device)
    if key not in _NVML:
        try:
            from ..monitoring import nvml_device
            torch = __import__('sys').modules['torch']
            lib, handle, _ = nvml_device('GPU-%s' % torch.cuda.get_device_properties(device).uuid)
            _NVML[key] = (lib, handle)
        except Exception:
            _NVML[key] = None
    return _NVML[key]


def card_other_use(device):
    """{process id: card bytes} of the programs on the card that are not ours (compute and graphics), or None when
    the driver does not report memory per process (some Windows drivers) or NVML is missing."""
    if getattr(device, 'type', str(device).split(':')[0]) != 'cuda':
        return None
    card = _nvml_card(device)
    if card is None:
        return None
    try:
        import ctypes
        lib, handle = card

        class Info(ctypes.Structure):
            _fields_ = [('pid', ctypes.c_uint), ('used', ctypes.c_ulonglong), ('gpu', ctypes.c_uint), ('compute', ctypes.c_uint)]
        use = {}
        own = own_process_ids()
        for kind in ('Compute', 'Graphics'):
            for version in ('_v3', '_v2'):
                function = getattr(lib, 'nvmlDeviceGet%sRunningProcesses%s' % (kind, version), None)
                if function is None:
                    continue
                # A fresh count and a buffer twice as large as the count says each time: a call that finds the buffer
                # too small rewrites the count, and a larger structure than ours must still stay inside the buffer.
                count = ctypes.c_uint(64)
                rows = (Info * 128)()
                if function(handle, ctypes.byref(count), rows) == 0 and count.value <= 64:
                    break
            else:
                return None
            for row in rows[:count.value]:
                if row.used >= 1 << 62:
                    return None
                if row.pid not in own:
                    use[row.pid] = max(use.get(row.pid, 0), int(row.used))
        return use
    except Exception:
        return None


def host_available_bytes():
    """Physical host memory available now, within this process's cgroup limit when there is one (clean file cache
    in the group counts as available, as the kernel reclaims it first), or None when it cannot be read."""
    try:
        from ..system import system_memory
        memory = system_memory()
        available = int(memory.get('physical_available_bytes') or memory['available_bytes'])
    except Exception:
        return None
    try:
        from ..hardware import cgroup_memory
        group = cgroup_memory()
        group = group.get('reclaimable_available_bytes', group.get('available_bytes'))
        if type(group) is int and group >= 0:
            available = min(available, group)
    except Exception:
        pass
    return available


class Crowding:
    """True once other programs have taken YIELD_BYTES of card memory, or HOST_YIELD_BYTES of host memory, since
    the wait began, or the host has less than HOST_LEAST_BYTES left.

    The worker's own memory does not change while it waits, so a drop in free memory is theirs; and where the driver
    reports memory per program, the growth of each other program is added up as well, so one program giving memory
    back does not hide another taking it."""

    def __init__(self, read, limit=YIELD_BYTES, host=None, host_limit=HOST_YIELD_BYTES, host_least=HOST_LEAST_BYTES,
                 host_after=0., clock=time.monotonic, others=None):
        self.read = read
        self.others = others
        self.others_start = others() if others is not None else None
        self.limit = limit
        self.start = read()
        self.host = host
        self.host_limit = host_limit
        self.host_least = host_least
        self.host_start = host() if host is not None else None
        # Host memory only ends the wait once the ordinary idle time is over, so it never ends it earlier than before.
        self.host_from = clock() + host_after
        self.clock = clock

    def __bool__(self):
        if self.others_start is not None:
            # Per program: what each other program has added counts, what another one gave back does not hide it.
            now = self.others()
            if now is not None and sum(max(0, use - self.others_start.get(pid, 0)) for pid, use in now.items()) >= self.limit:
                return True
        # The whole card as well, always: it also sees what the per-program list does not (some graphics programs,
        # a reading that failed for a while).
        now = self.read()
        if self.start is not None and now is not None and self.start - now >= self.limit:
            return True
        if self.host is None or self.clock() < self.host_from:
            return False
        host = self.host()
        return host is not None and (host < self.host_least
                                     or (self.host_start is not None and self.host_start - host >= self.host_limit))


def stays(environ=None):
    """Whether an idle worker keeps its model with no time limit (see ``server_mode.keeps_idle``)."""
    from .. import server_mode
    return server_mode.keeps_idle(environ)


def limit(device):
    """Card memory another program may take before the waiting worker lets go: YIELD_BYTES, or TIGHT_YIELD_BYTES
    where the worker stays with no time limit and the card has no reserve free."""
    if stays() and not card_has_room(device):
        return TIGHT_YIELD_BYTES
    return YIELD_BYTES


def wait_seconds(requested, runner, idle=IDLE_SECONDS):
    """Idle limit for the worker after a picture: the longer one only for a model whole on a readable card.

    The longest wait also needs the reserve to stay free. Without it a model that already waited LEGACY_SECONDS
    before still does, and one whose long wait is new (H3) waits only the idle time.

    Where ``stays()``, any model on a readable card waits with no time limit; the watch (``limit``) ends the wait."""
    if (type(requested) in (int, float) and requested > idle and stays()
            and card_free_bytes(getattr(runner, 'device', None)) is not None):
        return STAY_SECONDS
    if (type(requested) in (int, float) and requested > idle and getattr(runner, 'full_card', False) is True
            and card_free_bytes(getattr(runner, 'device', None)) is not None):
        if card_has_room(getattr(runner, 'device', None)):
            return min(float(requested), KEEP_SECONDS)
        if getattr(runner, 'long_wait_needs_room', False):
            return idle
        return min(float(requested), LEGACY_SECONDS, KEEP_SECONDS)
    return idle
