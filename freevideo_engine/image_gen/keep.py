"""How long an idle image worker keeps the model on the card.

A worker that made a picture waits IDLE_SECONDS for the next one. For someone who mostly makes
pictures it waits KEEP_SECONDS, but only while the whole model sits on the card, and it lets go at
once when another program starts taking card memory. Video always takes the card first.
"""

from .. import usage_habit

IDLE_SECONDS = 45.
KEEP_SECONDS = 600.
# Operations looked at (two thirds of them must be pictures), and how few are enough to judge at all.
WINDOW = 6
MINIMUM = 3
STORED = usage_habit.OPS_STORED
# Card memory other programs may take while the worker waits before it lets go.
YIELD_BYTES = 1 << 30
POLL_SECONDS = .5


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
    """The idle limit the worker is asked to use for the request being sent."""
    return KEEP_SECONDS if mostly_images(file) else IDLE_SECONDS


def card_free_bytes(device):
    """Free memory of the card the worker's model is on, or None when it cannot be read."""
    torch = __import__('sys').modules.get('torch')
    if torch is None or getattr(device, 'type', str(device).split(':')[0]) != 'cuda':
        return None
    try:
        return int(torch.cuda.mem_get_info(device)[0])
    except Exception:
        return None


class Crowding:
    """True once other programs have taken YIELD_BYTES of card memory since the wait began.

    The worker's own memory does not change while it waits, so a drop in free memory is theirs."""

    def __init__(self, read, limit=YIELD_BYTES):
        self.read = read
        self.limit = limit
        self.start = read()

    def __bool__(self):
        now = self.read()
        return self.start is not None and now is not None and self.start - now >= self.limit


def wait_seconds(requested, runner, idle=IDLE_SECONDS):
    """Idle limit for the worker after a picture: the longer one only for a model whole on a readable card."""
    if (type(requested) in (int, float) and requested > idle and getattr(runner, 'full_card', False) is True
            and card_free_bytes(getattr(runner, 'device', None)) is not None):
        return min(float(requested), KEEP_SECONDS)
    return idle
