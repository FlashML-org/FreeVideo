"""Recent usage habit kept across restarts: operation types and counts only.

Nothing derived from a prompt, image or seed is stored. A transition between
two successive successful videos is one of three kinds of change, and the file
keeps the last three transitions (the four-request window) plus running counts.
A file that is missing, unreadable or malformed counts as no habit at all.
"""
import json
import os
from pathlib import Path

WINDOW = 4
KINDS = ('images', 'prompt', 'rerun')


def path():
    override = os.environ.get('FREEVIDEO_USAGE_HABIT')
    if override:
        return Path(override)
    from .system import install_root
    return install_root() / '.freevideo' / 'usage-habit.json'


def transition(previous, current):
    """Kinds of change between two requests' digests (never stored)."""
    kinds = []
    if previous['images'] != current['images']:
        kinds.append('images')
    if previous['prompt'] != current['prompt']:
        kinds.append('prompt')
    elif not kinds:
        kinds.append('rerun')
    return kinds


def transitions(history):
    recent = history[-WINDOW:]
    return [transition(a, b) for a, b in zip(recent, recent[1:])]


def dominant(recent):
    """The kind that two thirds of the recent transitions share, or None.

    Image changes win ties with prompt changes, then same-prompt reruns.
    """
    if not recent:
        return None
    for kind in KINDS:
        if sum(kind in kinds for kinds in recent) * 3 >= len(recent) * 2:
            return kind
    return None


def load(file=None):
    """The stored habit, or None when there is nothing trustworthy to read."""
    try:
        value = json.loads(Path(file or path()).read_text(encoding='utf-8'))
        if value['version'] != 1 or value['window'] != WINDOW:
            return None
        recent = value['recent']
        counts = value['counts']
        if (not isinstance(recent, list) or len(recent) > WINDOW - 1
                or any(not isinstance(kinds, list) or any(kind not in KINDS for kind in kinds) for kinds in recent)
                or not isinstance(counts, dict)
                or any(type(counts.get(name)) is not int or counts[name] < 0 for name in (*KINDS, 'requests'))):
            return None
        return dict(version=1, window=WINDOW, recent=[list(kinds) for kinds in recent],
                    counts={name: counts[name] for name in (*KINDS, 'requests')})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def record(kinds, file=None):
    """Append one transition (None for the first request after a restart) and save."""
    from .monitoring import save
    value = load(file) or dict(version=1, window=WINDOW, recent=[], counts=dict.fromkeys((*KINDS, 'requests'), 0))
    value['counts']['requests'] += 1
    if kinds is not None:
        value['recent'] = (value['recent'] + [list(kinds)])[-(WINDOW - 1):]
        for kind in kinds:
            value['counts'][kind] += 1
    save(Path(file or path()), value)
    return value
