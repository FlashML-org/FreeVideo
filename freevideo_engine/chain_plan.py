"""How a requested length becomes one generation or a chain of blocks.

One rule for the creation panel, the editor and the server: under 30 s is one
generation when this device can do it; 30 s and longer, and lengths this device
cannot do at once, are cut into blocks that continue one another. A block
length given explicitly also cuts a video under 30 s.
"""
import math

from .geometry import geometry

FPS, UNIT, OVERLAP = 24, 17, 22
SINGLE_SECONDS, BLOCK_SECONDS, SHORTEST_BLOCK = 30, 15, 2
MAX_SECONDS = 7200
MAX_BLOCKS = MAX_SECONDS // SHORTEST_BLOCK


class PlanError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _number(value, name, whole=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0 or whole and type(value) is not int:
        raise PlanError('invalid', '%s must be a positive %s' % (name, 'integer' if whole else 'number'))
    return value


def _first_frames(seconds):
    return geometry(seconds=seconds)['frames']


def plan(seconds, single=False, max_frames=None, single_limit_seconds=None, block_seconds=None, continued=False):
    """`max_frames`: the most frames this device generates at once for the current settings (unknown: 15 s).

    `continued`: the video goes on from a saved one, so its first block too spends OVERLAP frames on what
    came before and adds only the rest.
    """
    _number(seconds, 'Length')
    if type(single) is not bool:
        raise PlanError('invalid', 'Single must be true or false')
    try:
        if max_frames is not None:
            limit = _number(max_frames, 'The frame limit', whole=True)
        else:
            limit = _first_frames(BLOCK_SECONDS if single_limit_seconds is None
                                  else _number(single_limit_seconds, 'The single generation limit'))
        if seconds >= SINGLE_SECONDS:
            seconds = math.floor(seconds + .5)  # 30 s and longer are planned in whole seconds
        frames = _first_frames(seconds)
    except PlanError:
        raise
    except ValueError as error:
        raise PlanError('too_short', str(error)) from None
    if seconds > MAX_SECONDS:
        raise PlanError('too_long', 'A video is at most 2 hours long')
    # The longest whole-second block this device can start a video with, and continue one with.
    first = (limit - (limit - 5) % UNIT) // FPS if limit >= 39 else 0
    following = (limit - OVERLAP) // UNIT * UNIT // FPS if limit >= OVERLAP + UNIT else 0
    new = frames
    if continued:
        new = max(1, math.ceil(round(FPS * seconds, 6) / UNIT)) * UNIT
        frames = new + OVERLAP
    block = BLOCK_SECONDS
    if block_seconds is not None:
        block = math.floor(_number(block_seconds, 'The block length') + .5)
        if block < SHORTEST_BLOCK:
            raise PlanError('block_too_short', 'A block is at least %d s long' % SHORTEST_BLOCK)
        if block > first:
            raise PlanError('block_too_long', 'This device cannot generate a block of this length')
    fits = frames <= limit
    alone = dict(seconds=seconds, frames=new, single=True, hide_frames=0,
                 blocks=[dict(seconds=seconds, frames=frames, new_frames=new)])
    # Under 30 s stays one generation unless a block length was asked for; 30 s is two blocks of 15 s.
    if fits and (single or block_seconds is None and seconds < SINGLE_SECONDS
                 or seconds <= min(block, following if continued else first)):
        return alone
    first, following = min(block, following if continued else first), min(block, following)
    if first < SHORTEST_BLOCK or following < SHORTEST_BLOCK:
        raise PlanError('device_limit', 'This device cannot generate this length at this size')
    total = seconds = math.floor(seconds + .5)
    lengths = [min(first, total)]
    while sum(lengths) < total:
        if len(lengths) >= MAX_BLOCKS:
            raise PlanError('too_long', 'This length needs too many blocks')
        lengths.append(min(following, total - sum(lengths)))
    if len(lengths) > 1 and lengths[-1] < SHORTEST_BLOCK:
        # No short tail: the last two blocks share their length, the earlier one gets the odd second.
        pair = lengths[-2] + lengths[-1]
        lengths[-2:] = [pair - pair // 2, pair // 2]
    if fits and block_seconds is not None and total < SINGLE_SECONDS and min(lengths) < SHORTEST_BLOCK:
        return alone  # a requested block length never makes a part under SHORTEST_BLOCK
    blocks, shown = [], 0
    for index, length in enumerate(lengths):
        target = FPS * sum(lengths[:index + 1])
        if index == 0 and not continued:
            new = requested = _first_frames(length)
        else:
            # Each block takes the shortest step that reaches its whole second; nothing is cut in between.
            new = max(1, math.ceil((target - shown) / UNIT)) * UNIT
            requested = new + OVERLAP
        if requested > limit or index and length > following or not index and length > first:
            raise PlanError('device_limit', 'This device cannot generate this length at this size')
        shown += new
        blocks.append(dict(seconds=length, frames=requested, new_frames=new))
    return dict(seconds=total, frames=FPS * total, single=False, hide_frames=shown - FPS * total, blocks=blocks)
