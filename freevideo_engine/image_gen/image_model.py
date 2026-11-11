"""Which picture model a request uses, and the sizes each model is asked for.

The picture page keeps its choice in the browser; the Agent runs on the server, so the page's last request
also leaves the choice in a small file next to the models (`remember`). With one model installed that model
is used; with both, the remembered one (H3 Image before any choice, as the page does).
"""
import json
import math
from pathlib import Path

from ..monitoring import save

QWEN = 'qwen-image-2.1-turbo'
H3 = 'minimax-h3-image'
MODELS = (H3, QWEN)
FILE = 'image-model.json'

# Side of the square whose area each size tier asks for. H3 Image starts at 4 MP (2048 x 2048) and goes to 16 MP;
# the same table as the picture page. Qwen keeps its own sizes.
H3_AREAS = {'standard': 2048, 'high': 4096}
RATIOS = {'1:1': (1, 1), '16:9': (16, 9), '9:16': (9, 16), '4:3': (4, 3), '3:4': (3, 4)}


def remembered(root):
    try:
        value = json.loads((Path(root) / FILE).read_text(encoding='utf-8')).get('model')
    except (OSError, ValueError, AttributeError):
        return None
    return value if value in MODELS else None


def remember(root, model):
    """Keep the model the page last generated with; a missing or unknown id changes nothing."""
    if model in MODELS and model != remembered(root):
        try:
            save(Path(root) / FILE, dict(model=model))
        except OSError:
            pass  # The choice only goes unremembered.


def choose(info, remembered_model=None):
    """The model id for a request that names none, from the service's `info()`; Qwen when it lists no models."""
    ready = [row['id'] for row in (info or {}).get('models') or () if row.get('ready') and row.get('id') in MODELS]
    if not ready:
        return QWEN
    if len(ready) == 1:
        return ready[0]
    return remembered_model if remembered_model in ready else (H3 if H3 in ready else ready[0])


def size(model, aspect, tier='standard'):
    """Width and height for an aspect at a tier: H3 Image at 4 or 16 MP in steps of 32, else None (Qwen's table)."""
    if model != H3:
        return None
    ratio_w, ratio_h = RATIOS[aspect]
    side = H3_AREAS.get(tier, H3_AREAS['standard'])
    scale = math.sqrt(side * side / (ratio_w * ratio_h))
    return tuple(max(32, round(value * scale / 32) * 32) for value in (ratio_w, ratio_h))


def at_least_four_megapixels(model, width, height):
    """H3 Image is asked for the same shape at 4 MP when the request is smaller; the picture is not scaled back."""
    if model != H3 or width * height >= H3_AREAS['standard'] ** 2:
        return width, height
    scale = math.sqrt(H3_AREAS['standard'] ** 2 / (width * height))
    return tuple(max(32, round(value * scale / 32) * 32) for value in (width, height))
