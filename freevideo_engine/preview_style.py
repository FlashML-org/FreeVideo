"""How the preview while generating is shown: a still sheet of frames (default), or a loop of the video in progress."""
import json
from pathlib import Path

from .monitoring import save

STYLES = ('sheet', 'loop')
FILE = 'preview-settings.json'


def read(root):
    """{'style': 'sheet' | 'loop', 'chosen': bool}; anything unreadable is the default sheet, not yet chosen."""
    try:
        value = json.loads((Path(root) / FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return dict(style='sheet', chosen=False)
    if not isinstance(value, dict) or value.get('style') not in STYLES:
        return dict(style='sheet', chosen=False)
    return dict(style=value['style'], chosen=value.get('chosen') is True)


def write(root, style):
    """Save the user's choice; a saved choice also means the first-time hint has no more to say."""
    if style not in STYLES:
        raise ValueError('The preview is shown as a sheet or a loop')
    result = dict(style=style, chosen=True)
    save(Path(root) / FILE, result)
    return result
