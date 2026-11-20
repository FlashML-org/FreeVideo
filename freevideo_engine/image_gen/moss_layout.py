"""MOSS-Image layout prompts: boxes drawn on the picture, each saying what goes there, written as the JSON MOSS reads.

The model-facing form is MOSS-Image's own (USERGUIDE "Layout Prompts", Apache-2.0):

    {"summary": {"caption": <the whole picture>}, "items": [{"label", "caption", "text"?, "palette"?, "bbox"}],
     "bbox_coord_space": "abs_centralized", "bbox_format": "yxyx"}

``bbox`` is [top, left, bottom, right] in pixels of the picture made, measured from its centre (x to the right, y
down). Boxes come in as fractions of the picture (0..1, from the top-left), so they are written only once the final
width and height are known: a box drawn on the page lands on the same part of the picture whatever size is made.
Fractional edges enclose the box (top/left down, bottom/right up) before the centre is taken off.

The text is passed to the model as it is: a prompt rewrite would rewrite the JSON as prose and drop the boxes.
"""
import json
import math

LABELS = ('object', 'character', 'text')
MAX_ITEMS = 16
MAX_CAPTION = 400
MAX_TEXT = 200
MAX_PALETTE = 5
# The text encoder reads at most 4096 tokens and cuts the rest silently, which would cut the JSON. MOSS's tokenizer
# gives about 3.5 characters a token in English and 1.7 at worst in Chinese layouts, so this stays near 2100 tokens
# at most; ten boxes with a sentence each are 1100-1800 characters.
MAX_CHARS = 3600


class LayoutError(ValueError):
    """A layout that cannot be written; ``code`` is the reason the page shows."""

    def __init__(self, code, index=None):
        super().__init__(code if index is None else '%s (box %d)' % (code, index + 1))
        self.code = code
        self.index = index


def _words(value, limit, code, index, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise LayoutError(code, index)
    return value.strip()


def _edges(box, width, height, index):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise LayoutError('invalid_layout', index)
    if not all(type(v) in (int, float) and math.isfinite(v) for v in box):
        raise LayoutError('invalid_layout', index)
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise LayoutError('invalid_layout', index)
    # Rounded first so that a fraction written from whole pixels comes back as those pixels.
    top, left = math.floor(round(y0 * height, 6)), math.floor(round(x0 * width, 6))
    bottom, right = math.ceil(round(y1 * height, 6)), math.ceil(round(x1 * width, 6))
    # A box thinner than the rounding keeps no width or height in pixels; MOSS's own compiler refuses it too.
    if not (top < bottom and left < right):
        raise LayoutError('invalid_layout', index)
    return [top - height // 2, left - width // 2, bottom - height // 2, right - width // 2]


def compile_layout(caption, items, width, height):
    """The MOSS prompt for ``caption`` (the whole picture) and ``items`` on a ``width`` x ``height`` picture.

    Each item: ``label`` (object, character or text), ``caption`` (what it looks like), ``text`` (the exact words, for
    a text box), optional ``palette`` (up to five colours) and ``box`` [left, top, right, bottom] as fractions."""
    caption = _words(caption, 2000, 'invalid_prompt', None)
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise LayoutError('invalid_dimensions')
    if not isinstance(items, list) or not items:
        raise LayoutError('invalid_layout')
    if len(items) > MAX_ITEMS:
        raise LayoutError('too_many_boxes')
    out = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not set(item) <= {'label', 'caption', 'text', 'palette', 'box'}:
            raise LayoutError('invalid_layout', index)
        label = item.get('label', 'object')
        if label not in LABELS:
            raise LayoutError('invalid_layout', index)
        row = {'label': label}
        text = _words(item.get('text'), MAX_TEXT, 'invalid_layout', index, required=label == 'text')
        # A text box may say only its words; the model still wants a caption, so the words stand in for it.
        row['caption'] = _words(item.get('caption') or (text and 'The words "%s".' % text), MAX_CAPTION,
                                'invalid_layout', index)
        if label == 'text':
            row['text'] = text
        palette = item.get('palette')
        if palette:
            if (not isinstance(palette, list) or len(palette) > MAX_PALETTE
                    or not all(isinstance(c, str) and 0 < len(c) <= 32 for c in palette)):
                raise LayoutError('invalid_layout', index)
            row['palette'] = palette
        row['bbox'] = _edges(item.get('box'), width, height, index)
        out.append(row)
    prompt = json.dumps({'summary': {'caption': caption}, 'items': out, 'bbox_coord_space': 'abs_centralized',
                         'bbox_format': 'yxyx'}, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if len(prompt) > MAX_CHARS:
        raise LayoutError('layout_too_long')
    return prompt
