"""Headless image edits for agents, on the browser editor's own document.

web/image_editor_engine.js and this module read the same version 2 document:
an agent's recipe compiles to it, renders here without a browser, and a person
can reopen the result in the editor with every layer and step in place.
Pixels follow the browser's canvas maths (premultiplied colour, bilinear
sampling, the same adjustment formulas and noise sequence), so a document looks
the same saved from either side; text uses the editor's bundled fonts.
"""
import functools
import itertools
import math
from pathlib import Path
import time
import unicodedata

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps

BLENDS = ('source-over', 'multiply', 'screen', 'overlay', 'soft-light', 'darken', 'lighten',
          'color-dodge', 'color-burn', 'difference')
ADJUSTMENTS = ('exposure', 'contrast', 'highlights', 'shadows', 'temperature', 'tint', 'hue', 'saturation',
               'vibrance', 'sharpen', 'blur', 'noise', 'vignette')
RANGES = dict({k: (-100, 100) for k in ADJUSTMENTS}, hue=(-180, 180), sharpen=(0, 100), blur=(0, 100), noise=(0, 100))
# The families of web/fonts.js, bundled with FreeVideo: the browser editor draws with the same files.
FONT_DIR = Path(__file__).resolve().parents[2] / 'web' / 'fonts'
FONTS = {
    'sans': {400: 'FreeVideoSansSC-Regular.woff2', 700: 'FreeVideoSansSC-Bold.woff2'},
    'serif': {400: 'FreeVideoSerifSC-Regular.woff2', 700: 'FreeVideoSerifSC-Bold.woff2'},
    'kai': {400: 'FreeVideoKaiSC-Regular.woff2', 700: 'FreeVideoKaiSC-Medium.woff2'},
    'mono': {400: 'FreeVideoMonoSC-Regular.woff2', 700: 'FreeVideoMonoSC-Bold.woff2'},
}
# Regular faces for the scripts the Chinese faces lack, between the sans and the box (web/fonts.js EXTRA).
FONT_EXTRA = ('FreeVideoSansTC-Regular.woff2', 'FreeVideoSansKR-Regular.woff2', 'FreeVideoSansVietnamese-Regular.woff2',
              'FreeVideoSansArabic-Regular.woff2', 'FreeVideoSansThai-Regular.woff2', 'FreeVideoSansDevanagari-Regular.woff2')
FONT_BOX = 'FreeVideoBox.woff2'
# Canvas textBaseline 'top' puts the em box's top on y: the typographic ascender
# over ascender + descender. Every bundled face has 880 / -120 units in its OS/2
# table; tests/test_agent_edit.py reads the files again when fontTools is present.
EM_ASCENT = .88
RATIOS = {'16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1., '4:3': 4 / 3, '3:4': 3 / 4}
RATIO_IDS = ('video', 'original', 'free', *RATIOS)
ANCHORS = ('center', 'top-left', 'top', 'top-right', 'left', 'right', 'bottom-left', 'bottom', 'bottom-right')
MAX_PIXELS = 64 * 2**20
MAX_OPERATIONS = 64
TOOLS = ('paint', 'erase', 'clone', 'heal', 'blur', 'hide', 'reveal')
OPS = ('stroke', 'fill', 'clear', 'gradient', 'fromSelection')


class EditError(ValueError):
    """A document or recipe problem at a JSON path such as /layers/2/ops/0."""
    def __init__(self, path, message):
        super().__init__('%s: %s' % (path or '/', message))
        self.path, self.message = path or '/', message


def _round(value):
    return math.floor(value + .5)   # JavaScript's Math.round


def _size(value):
    return max(1, _round(value))


# ---------------------------------------------------------------------------
# Geometry, as DOMMatrix: each step multiplies on the right.

def _t(x, y):
    return np.array([[1., 0, x], [0, 1, y], [0, 0, 1]])


def _s(x, y):
    return np.array([[x, 0., 0], [0, y, 0], [0, 0, 1]])


def _r(degrees):
    turns = degrees / 90
    if turns == int(turns):
        c, s = ((1, 0), (0, 1), (-1, 0), (0, -1))[int(turns) % 4]
    else:
        c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return np.array([[c, -s, 0.], [s, c, 0], [0, 0, 1]])


def _chain(*matrices):
    out = np.eye(3)
    for m in matrices:
        out = out @ m
    return out


def oriented_size(doc):
    return (doc['height'], doc['width']) if doc['rotate'] % 180 else (doc['width'], doc['height'])


def orient_matrix(doc):
    ow, oh = oriented_size(doc)
    return _chain(_t(ow / 2, oh / 2), _s(-1 if doc['flipX'] else 1, -1 if doc['flipY'] else 1),
                  _r(doc['rotate']), _t(-doc['width'] / 2, -doc['height'] / 2))


def canvas_rect(doc):
    (ow, oh), e = oriented_size(doc), doc['extend']
    return dict(x=-e['l'], y=-e['t'], w=ow + e['l'] + e['r'], h=oh + e['t'] + e['b'])


def layer_matrix(layer):
    """Layer native pixels to document pixels."""
    return _chain(_t(layer['cx'], layer['cy']), _r(layer['rotation']),
                  _s(-1 if layer['flipX'] else 1, -1 if layer['flipY'] else 1),
                  _t(-layer['w'] / 2, -layer['h'] / 2), _s(layer['w'] / layer['nw'], layer['h'] / layer['nh']))


def _apply(m, x, y):
    return m[0, 0] * x + m[0, 1] * y + m[0, 2], m[1, 0] * x + m[1, 1] * y + m[1, 2]


# ---------------------------------------------------------------------------
# Colour and rasters: float32 premultiplied RGBA in 0..1, like a canvas.

def _color(value, path='/'):
    if value is None or isinstance(value, str) and value.strip().lower() in ('transparent', 'none'):
        return (0., 0., 0., 0.)
    try:
        r, g, b, a = ImageColor.getcolor(str(value), 'RGBA')
    except ValueError:
        raise EditError(path, 'not a colour: %r' % (value,)) from None
    return (r / 255, g / 255, b / 255, a / 255)


def _solid(h, w, color):
    r, g, b, a = color
    out = np.empty((h, w, 4), np.float32)
    out[...] = (r * a, g * a, b * a, a)
    return out


def _q(a):
    """Canvas storage: 8-bit premultiplied."""
    return np.rint(np.clip(a, 0, 1) * 255).astype(np.float32) / 255


def _from_image(image):
    d = np.asarray(image.convert('RGBA'), np.float32) / 255
    d[..., :3] *= d[..., 3:]
    return _q(d)


def _unpremul8(a):
    """getImageData: unpremultiplied 8-bit values."""
    p = np.rint(np.clip(a, 0, 1) * 255)
    alpha = p[..., 3:]
    with np.errstate(divide='ignore', invalid='ignore'):
        rgb = np.where(alpha > 0, np.rint(p[..., :3] * 255 / np.maximum(alpha, 1)), 0)
    return np.concatenate([np.minimum(rgb, 255), alpha], -1)


def _premul8(d):
    """putImageData: premultiply unpremultiplied 8-bit values."""
    d = np.asarray(d, np.float64)
    out = np.empty(d.shape, np.float32)
    out[..., :3] = np.rint(d[..., :3] * d[..., 3:] / 255) / 255
    out[..., 3] = d[..., 3] / 255
    return out


def _to_image(a):
    return Image.fromarray(_unpremul8(a).astype(np.uint8), 'RGBA')


def _blend(name, cb, cs):
    if name == 'multiply':
        return cb * cs
    if name == 'screen':
        return cb + cs - cb * cs
    if name == 'overlay':
        return np.where(cb <= .5, cs * 2 * cb, 1 - (1 - cs) * (1 - (2 * cb - 1)))
    if name == 'darken':
        return np.minimum(cb, cs)
    if name == 'lighten':
        return np.maximum(cb, cs)
    if name == 'difference':
        return np.abs(cb - cs)
    with np.errstate(divide='ignore', invalid='ignore'):
        if name == 'color-dodge':
            return np.where(cb <= 0, 0, np.where(cs >= 1, 1, np.minimum(1, cb / (1 - cs))))
        if name == 'color-burn':
            return np.where(cb >= 1, 1, np.where(cs <= 0, 0, 1 - np.minimum(1, (1 - cb) / cs)))
    if name == 'soft-light':
        d = np.where(cb <= .25, ((16 * cb - 12) * cb + 4) * cb, np.sqrt(cb))
        return np.where(cs <= .5, cb - (1 - 2 * cs) * cb * (1 - cb), cb + (2 * cs - 1) * (d - cb))
    raise EditError('/', 'unknown blend %r' % name)


def _composite(dst, src, op='source-over'):
    """Porter-Duff and separable blends of premultiplied src onto dst, in place."""
    sa, da = src[..., 3:], dst[..., 3:]
    if op == 'destination-out':
        dst *= 1 - sa
    elif op == 'destination-in':
        dst *= sa
    elif op == 'xor':
        dst[...] = src * (1 - da) + dst * (1 - sa)
    elif op == 'source-over':
        dst[...] = src + dst * (1 - sa)
    else:
        with np.errstate(divide='ignore', invalid='ignore'):
            cs = np.where(sa > 0, src[..., :3] / np.maximum(sa, 1e-12), 0)
            cb = np.where(da > 0, dst[..., :3] / np.maximum(da, 1e-12), 0)
        mixed = (1 - da) * cs + da * np.clip(_blend(op, cb, cs), 0, 1)
        rgb = sa * mixed + (1 - sa) * dst[..., :3]
        dst[..., 3:] = sa + da * (1 - sa)
        dst[..., :3] = rgb


def _draw(dst, src, m, alpha=1., op='source-over'):
    """drawImage(src) under matrix m (src pixels to dst pixels): bilinear, edge
    clamped colour and anti-aliased quad coverage, as Skia draws it."""
    H, W = dst.shape[:2]
    h, w = src.shape[:2]
    xs, ys = _apply(m, np.array([0., w, w, 0]), np.array([0., 0, h, h]))
    whole = op == 'destination-in'
    if np.allclose(m[:2, :2], np.eye(2)) and float(m[0, 2]).is_integer() and float(m[1, 2]).is_integer():
        ox, oy = int(m[0, 2]), int(m[1, 2])
        x0, y0, x1, y1 = max(0, ox), max(0, oy), min(W, ox + w), min(H, oy + h)
        piece = np.zeros((max(0, y1 - y0), max(0, x1 - x0), 4), np.float32)
        if x1 > x0 and y1 > y0:
            piece[...] = src[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
    else:
        x0, x1 = max(0, math.floor(xs.min())), min(W, math.ceil(xs.max()))
        y0, y1 = max(0, math.floor(ys.min())), min(H, math.ceil(ys.max()))
        piece = np.zeros((max(0, y1 - y0), max(0, x1 - x0), 4), np.float32)
        if x1 > x0 and y1 > y0:
            gx, gy = np.meshgrid(np.arange(x0, x1) + .5, np.arange(y0, y1) + .5)
            u, v = _apply(np.linalg.inv(m), gx, gy)
            # Snap sampling that lands on pixel centres (turns, flips) to exact copies.
            u, v = u - .5, v - .5
            u = np.where(np.abs(u - np.rint(u)) < 1e-6, np.rint(u), u)
            v = np.where(np.abs(v - np.rint(v)) < 1e-6, np.rint(v), v)
            ix, iy = np.floor(u), np.floor(v)
            fx, fy = (u - ix)[..., None].astype(np.float32), (v - iy)[..., None].astype(np.float32)
            ix0, iy0 = np.clip(ix, 0, w - 1).astype(np.intp), np.clip(iy, 0, h - 1).astype(np.intp)
            ix1, iy1 = np.clip(ix + 1, 0, w - 1).astype(np.intp), np.clip(iy + 1, 0, h - 1).astype(np.intp)
            piece = ((src[iy0, ix0] * (1 - fx) + src[iy0, ix1] * fx) * (1 - fy)
                     + (src[iy1, ix0] * (1 - fx) + src[iy1, ix1] * fx) * fy)
            area = np.sum(xs * np.roll(ys, -1) - np.roll(xs, -1) * ys)
            cover = np.ones(gx.shape, np.float32)
            for i in range(4):
                ax, ay, bx, by = xs[i], ys[i], xs[(i + 1) % 4], ys[(i + 1) % 4]
                length = math.hypot(bx - ax, by - ay) or 1
                d = ((bx - ax) * (gy - ay) - (by - ay) * (gx - ax)) / length * (1 if area > 0 else -1)
                cover *= np.clip(d + .5, 0, 1).astype(np.float32)
            piece = piece * cover[..., None]
    if alpha != 1:
        piece = piece * np.float32(alpha)
    if whole:
        full = np.zeros_like(dst)
        full[y0:y1, x0:x1] = piece
        _composite(dst, full, op)
    elif piece.size:
        region = dst[y0:y1, x0:x1]
        _composite(region, piece, op)
    return dst


def _mask_with(target, mask):
    """destination-in with a same-size mask."""
    target *= mask[..., 3:]
    return target


def _blurred(a, radius):
    """CSS blur(radius px): a Gaussian with that standard deviation, edges
    extended (the editor pads with edge pixels before filtering)."""
    if radius <= 0:
        return a.copy()
    image = Image.fromarray(np.rint(np.clip(a, 0, 1) * 255).astype(np.uint8), 'RGBA')
    image = Image.frombytes('RGBa', image.size, image.tobytes()).filter(ImageFilter.GaussianBlur(radius))
    return np.frombuffer(image.tobytes(), np.uint8).reshape(a.shape).astype(np.float32) / 255


def _blur_alpha(alpha, sigma):
    if sigma <= 0:
        return alpha
    image = Image.fromarray(np.rint(np.clip(alpha, 0, 1) * 255).astype(np.uint8), 'L')
    return np.asarray(image.filter(ImageFilter.GaussianBlur(sigma)), np.float32) / 255


def _alpha_raster(alpha):
    out = np.zeros(alpha.shape + (4,), np.float32)
    out[...] = alpha[..., None]
    return _q(out)


# ---------------------------------------------------------------------------
# Adjustments: the editor's formulas, step for step.

def _imul(a, b):
    return ((a.astype(np.uint64) * (b.astype(np.uint64) & 0xFFFFFFFF)) & 0xFFFFFFFF).astype(np.uint32)


def _mulberry(seed, count):
    """Values of the editor's mulberry32 generator, calls 1..count."""
    k = np.arange(1, count + 1, dtype=np.uint64)
    s = ((np.uint64(seed) + k * np.uint64(0x6D2B79F5)) & np.uint64(0xFFFFFFFF)).astype(np.uint32)
    t = _imul(s ^ (s >> np.uint32(15)), s | np.uint32(1))
    t = ((t.astype(np.uint64) + _imul(t ^ (t >> np.uint32(7)), t | np.uint32(61))) & 0xFFFFFFFF).astype(np.uint32) ^ t
    return (t ^ (t >> np.uint32(14))).astype(np.float64) / 4294967296


def hash_string(text):
    h = 2166136261
    for ch in str(text):
        code = ord(ch)
        if code > 0xFFFF:
            code = 0xD800 + ((code - 0x10000) >> 10)
        h = ((h ^ code) * 16777619) & 0xFFFFFFFF
    return h


def adjusted(raster, adjust, seed):
    a = {k: v for k, v in (adjust or {}).items() if v}
    if not any(a.get(k) for k in ADJUSTMENTS):
        return raster
    work = raster
    h, w = work.shape[:2]
    if any(a.get(k) for k in ADJUSTMENTS[:9]):
        d = _unpremul8(work).astype(np.float64)
        gain, contrast = math.pow(2, a.get('exposure', 0) / 50), 1 + a.get('contrast', 0) / 100
        temp, tint = a.get('temperature', 0) / 100, a.get('tint', 0) / 100
        hi, sh = a.get('highlights', 0) / 100, a.get('shadows', 0) / 100
        sat, vib = 1 + a.get('saturation', 0) / 100, a.get('vibrance', 0) / 100
        angle = a.get('hue', 0) * math.pi / 180
        cos, sin = math.cos(angle), math.sin(angle)
        hr = [.213 + cos * .787 - sin * .213, .715 - cos * .715 - sin * .715, .072 - cos * .072 + sin * .928,
              .213 - cos * .213 + sin * .143, .715 + cos * .285 + sin * .140, .072 - cos * .072 - sin * .283,
              .213 - cos * .213 - sin * .787, .715 - cos * .715 + sin * .715, .072 + cos * .928 + sin * .072]
        r, g, b = d[..., 0] / 255 * gain, d[..., 1] / 255 * gain, d[..., 2] / 255 * gain
        if temp:
            r, b = r + temp * .12, b - temp * .12
        if tint:
            g, r, b = g - tint * .1, r + tint * .04, b + tint * .04
        if hi or sh:
            lum = .2126 * r + .7152 * g + .0722 * b
            lift = hi * np.maximum(0, lum - .5) * .8 + sh * np.maximum(0, .5 - lum) * .8
            r, g, b = r + lift, g + lift, b + lift
        if contrast != 1:
            r, g, b = (r - .5) * contrast + .5, (g - .5) * contrast + .5, (b - .5) * contrast + .5
        if angle:
            r, g, b = (hr[0] * r + hr[1] * g + hr[2] * b, hr[3] * r + hr[4] * g + hr[5] * b,
                       hr[6] * r + hr[7] * g + hr[8] * b)
        s = sat
        if vib:
            spread = np.clip(np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b), 0, 1)
            s = s * (1 + vib * (1 - spread))
        if not (np.isscalar(s) and s == 1):
            r, g, b = ((.213 + .787 * s) * r + (.715 - .715 * s) * g + (.072 - .072 * s) * b,
                       (.213 - .213 * s) * r + (.715 + .285 * s) * g + (.072 - .072 * s) * b,
                       (.213 - .213 * s) * r + (.715 - .715 * s) * g + (.072 + .928 * s) * b)
        for i, c in enumerate((r, g, b)):
            d[..., i] = np.rint(np.clip(c, 0, 1) * 255)
        work = _premul8(d)
    unit = min(w, h) / 100
    if a.get('blur', 0) > 0:
        work = _blurred(work, a['blur'] / 100 * unit * 4)
    if a.get('sharpen', 0) > 0:
        sd = _unpremul8(_blurred(work, max(.6, unit * .35)))
        d = _unpremul8(work)
        amount = a['sharpen'] / 100 * 1.6
        d[..., :3] = np.rint(np.clip(d[..., :3] + (d[..., :3] - sd[..., :3]) * amount, 0, 255))
        work = _premul8(d)
    if a.get('noise', 0) > 0 or a.get('vignette'):
        d = _unpremul8(work)
        amount, v = a.get('noise', 0) / 100 * 60, a.get('vignette', 0) / 100
        f = np.ones((h, w))
        if v:
            dx = (np.arange(w) / w - .5) * 2
            dy = (np.arange(h) / h - .5) * 2
            rr = np.minimum(1, np.sqrt(dx[None, :] * dx[None, :] + dy[:, None] * dy[:, None]) / math.sqrt(2))
            f = 1 - v * rr * rr * 1.1 if v > 0 else 1 - v * rr * rr * .8
        n = 0
        if amount:
            values = _mulberry(seed, 2 * h * w).reshape(h, w, 2)
            n = (values[..., 0] + values[..., 1] - 1) * amount
        for c in range(3):
            d[..., c] = np.rint(np.clip(d[..., c] * f + n, 0, 255))
        work = _premul8(d)
    return work


# ---------------------------------------------------------------------------
# Strokes, selections and the editor's pixel tools

def _segments_distance(h, w, pts, radius):
    """Distance from pixel centres to a polyline, within radius + 1."""
    dist = np.full((h, w), np.inf, np.float32)
    pairs = list(zip(pts, pts[1:])) or [(pts[0], pts[0])]
    for (ax, ay), (bx, by) in pairs:
        x0, x1 = max(0, math.floor(min(ax, bx) - radius - 1)), min(w, math.ceil(max(ax, bx) + radius + 1))
        y0, y1 = max(0, math.floor(min(ay, by) - radius - 1)), min(h, math.ceil(max(ay, by) + radius + 1))
        if x1 <= x0 or y1 <= y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1) + .5, np.arange(y0, y1) + .5)
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = np.clip(((gx - ax) * dx + (gy - ay) * dy) / length, 0, 1) if length else 0
        d = np.hypot(gx - (ax + t * dx), gy - (ay + t * dy)).astype(np.float32)
        np.minimum(dist[y0:y1, x0:x1], d, out=dist[y0:y1, x0:x1])
    return dist


def _stroke_mask(h, w, op):
    size = max(1, op['size'] * min(w, h))
    hard = min(1, max(0, op.get('hardness', 1) if op.get('hardness') is not None else 1))
    core, soft = size * (.4 + .6 * hard), size * (1 - hard) * .55
    pts = [(u * w, v * h) for u, v in op['pts']]
    alpha = np.clip(core / 2 - _segments_distance(h, w, pts, core / 2) + .5, 0, 1)
    # A shadow-only stroke: canvas shadows blur with sigma = shadowBlur / 2.
    alpha = _blur_alpha(alpha, soft / 2)
    return _alpha_raster(alpha)


def _polygon_alpha(h, w, pts, factor=4):
    """Anti-aliased fill of a closed polygon given in pixel coordinates."""
    out = np.zeros((h, w), np.float32)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1 = max(0, math.floor(min(xs))), min(w, math.ceil(max(xs)))
    y0, y1 = max(0, math.floor(min(ys))), min(h, math.ceil(max(ys)))
    if x1 <= x0 or y1 <= y0:
        return out
    big = Image.new('L', ((x1 - x0) * factor, (y1 - y0) * factor))
    ImageDraw.Draw(big).polygon([((x - x0) * factor - .5, (y - y0) * factor - .5) for x, y in pts], fill=255)
    small = big.reduce(factor)
    out[y0:y1, x0:x1] = np.asarray(small, np.float32) / 255
    return out


def _runs(row):
    edges = np.diff(np.concatenate([[0], row.view(np.int8), [0]]))
    return np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)


def _flood(candidates, sx, sy):
    """4-connected region of True pixels around (sx, sy), walked by row runs."""
    h, w = candidates.shape
    rows = [_runs(candidates[y]) for y in range(h)]
    seen = [np.zeros(len(r[0]), bool) for r in rows]
    selected = np.zeros((h, w), bool)
    starts, ends = rows[sy]
    first = np.flatnonzero((starts <= sx) & (ends > sx))
    stack = [(sy, int(first[0]))] if len(first) else []
    while stack:
        y, i = stack.pop()
        if seen[y][i]:
            continue
        seen[y][i] = True
        a, b = rows[y][0][i], rows[y][1][i]
        selected[y, a:b] = True
        for ny in (y - 1, y + 1):
            if 0 <= ny < h:
                ns, ne = rows[ny]
                stack.extend((ny, int(j)) for j in np.flatnonzero((ns < b) & (ne > a)) if not seen[ny][j])
    return selected


def wand(raster, u, v, tolerance, contiguous):
    h, w = raster.shape[:2]
    sx, sy = min(w - 1, max(0, math.floor(u * w))), min(h - 1, max(0, math.floor(v * h)))
    d = _unpremul8(raster)
    near = np.all(np.abs(d - d[sy, sx]) <= tolerance * 2.55, -1)
    selected = _flood(near, sx, sy) if contiguous else near
    return _alpha_raster(selected.astype(np.float32))


def _pull_push(col, wt):
    """Pull-push interpolation, as the editor's pullPush: col (h, w, 4) holds
    known values times their weight wt (h, w); gaps are filled from coarser averages."""
    levels = [(col, wt)]
    while col.shape[0] > 1 or col.shape[1] > 1:
        ch, cw = wt.shape
        nh, nw = max(1, ch >> 1), max(1, cw >> 1)
        sy = [np.minimum(ch - 1, np.arange(nh) * 2 + k) for k in (0, 1)]
        sx = [np.minimum(cw - 1, np.arange(nw) * 2 + k) for k in (0, 1)]
        sw = sum(wt[np.ix_(a, b)] for a in sy for b in sx)
        acc = sum(col[np.ix_(a, b)] for a in sy for b in sx)
        with np.errstate(divide='ignore', invalid='ignore'):
            col = np.where(sw[..., None] > 0, acc / sw[..., None] * np.minimum(1, sw)[..., None], 0)
        wt = np.minimum(1, sw)
        levels.append((col, wt))
    for level in range(len(levels) - 2, -1, -1):
        col, wt = levels[level]
        up_col, up_wt = levels[level + 1]
        uh, uw = up_wt.shape
        fy = np.clip((np.arange(wt.shape[0]) + .5) / 2 - .5, 0, uh - 1)
        fx = np.clip((np.arange(wt.shape[1]) + .5) / 2 - .5, 0, uw - 1)
        iy, ix = np.floor(fy).astype(int), np.floor(fx).astype(int)
        ty, tx = (fy - iy)[:, None], (fx - ix)[None, :]
        jy, jx = np.minimum(uh - 1, iy + 1), np.minimum(uw - 1, ix + 1)
        def sample(a):
            t3 = tx[..., None] if a.ndim == 3 else tx
            u3 = ty[..., None] if a.ndim == 3 else ty
            return ((a[np.ix_(iy, ix)] * (1 - t3) + a[np.ix_(iy, jx)] * t3) * (1 - u3)
                    + (a[np.ix_(jy, ix)] * (1 - t3) + a[np.ix_(jy, jx)] * t3) * u3)
        s, sw = sample(up_col), sample(up_wt)
        with np.errstate(divide='ignore', invalid='ignore'):
            norm = np.where(sw[..., None] > 0, s / sw[..., None], 0)
        hole = wt < 1
        col[hole] = col[hole] + norm[hole] * (1 - wt[hole])[..., None]
        wt[hole] = 1
    return levels[0][0]


# Spot healing: the editor's patch-based fill (image_editor_engine.js), step
# for step, so a stroke heals to the same pixels here as in the browser.
HEAL_PATCH, HEAL_COARSE, HEAL_ITERS, HEAL_SEARCH, HEAL_MARGIN, HEAL_PEEL = 3, 24, 5, 60000, 384, 2048
# A whole stroke leans on nearby patches; a caption's thin strokes have picture all around them.
HEAL_NEAR, HEAL_NEAR_TEXT, HEAL_GUESS, HEAL_TEXT_OUTLINE = 4, .25, .1, .15
_U32 = np.uint32


def _hash32(x):
    x = np.asarray(x, np.uint32)
    x = x ^ (x >> _U32(16)); x = x * _U32(0x7feb352d)
    x = x ^ (x >> _U32(15)); x = x * _U32(0x846ca68b)
    return x ^ (x >> _U32(16))


def _heal_seed(i, level, it, step):
    value = (np.asarray(i, np.int64) * 0x9e3779b1 + level * 0x85ebca6b + it * 0xc2b2ae35 + step * 0x27d4eb2f) & 0xffffffff
    return _hash32(value.astype(np.uint32))


def _dilate(m, r):
    """Pixels within r of a set pixel (a square neighbourhood)."""
    h, w = m.shape
    rows = np.zeros_like(m)
    for k in range(-r, r + 1):
        rows[:, max(0, -k):w - max(0, k)] |= m[:, max(0, k):w - max(0, -k)]
    out = np.zeros_like(m)
    for k in range(-r, r + 1):
        out[max(0, -k):h - max(0, k), :] |= rows[max(0, k):h - max(0, -k), :]
    return out


def _peel(E, hole, sources, pr):
    """The coarsest level's start: ring by ring from the edge inwards, each hole pixel takes
    the centre of the source patch that best matches its filled neighbours."""
    lh, lw = hole.shape
    filled = ~hole
    S = sources[::max(1, -(-len(sources) // HEAL_PEEL))]
    sx, sy = S % lw, S // lw
    E64 = E.astype(np.float64)
    while True:
        F = np.flatnonzero((hole & ~filled & _dilate(filled, 1)).ravel())
        if not len(F):
            break
        fx, fy = F % lw, F // lw
        num = np.zeros((len(F), len(S)))
        count = np.zeros(len(F))
        for dy in range(-pr, pr + 1):
            for dx in range(-pr, pr + 1):
                q = np.clip(fy + dy, 0, lh - 1) * lw + np.clip(fx + dx, 0, lw - 1)
                k = filled.ravel()[q]
                e = E64[q][:, None, :] - E64[(sy + dy) * lw + sx + dx][None, :, :]
                num += k[:, None] * (e[..., 0] * e[..., 0] + e[..., 1] * e[..., 1] + e[..., 2] * e[..., 2] + e[..., 3] * e[..., 3])
                count += k
        E64[F] = E64[S[np.argmin(num / count[:, None], axis=1)]]
        filled.ravel()[F] = True
    E[:] = E64.astype(np.float32)


def _patch_fill(region, hole, hole_size, near):
    """Fill of the hole pixels of region ((h, w, 4) float32 of 0-255 values), or None when no patch fits."""
    pr = HEAL_PATCH
    area = (2 * pr + 1) ** 2
    h, w = hole.shape
    count = 1 + max(0, math.ceil(math.log2(hole_size / HEAL_COARSE)))
    while count > 1 and min(w >> (count - 1), h >> (count - 1)) < 4 * (2 * pr + 1):
        count -= 1
    levels = [(region.astype(np.float32), hole.astype(bool))]
    for _ in range(1, count):
        img, m = levels[-1]
        ph, pw = m.shape
        nh, nw = max(1, ph >> 1), max(1, pw >> 1)
        acc = np.zeros((nh, nw, 4), np.float32)
        nm = np.zeros((nh, nw), bool)
        for dy in (0, 1):
            for dx in (0, 1):
                ys, xs = np.minimum(ph - 1, np.arange(nh) * 2 + dy), np.minimum(pw - 1, np.arange(nw) * 2 + dx)
                acc = (acc.astype(np.float64) + img[np.ix_(ys, xs)].astype(np.float64) / 4).astype(np.float32)
                nm |= m[np.ix_(ys, xs)]
        levels.append((acc, nm))
    nnf = coarse_e = None
    for level in range(count - 1, -1, -1):
        img, m = levels[level]
        lh, lw = m.shape
        touching = _dilate(m, pr)
        yy, xx = np.mgrid[0:lh, 0:lw]
        valid = (~touching & (xx >= pr) & (yy >= pr) & (xx < lw - pr) & (yy < lh - pr)).ravel()
        targets, sources = np.flatnonzero(touching.ravel()), np.flatnonzero(valid)
        if not len(sources):
            return None
        holef = m.ravel()
        tx, ty = targets % lw, targets // lw
        pick = lambda step: sources[(_heal_seed(targets, level, 255, step) % _U32(len(sources))).astype(np.int64)]
        E = img.reshape(-1, 4).copy()
        if level == count - 1:
            _peel(E, m, sources, pr)
            nxt = pick(0)
        else:
            uh, uw = levels[level + 1][1].shape
            cx, cy = np.minimum(uw - 1, xx.ravel() >> 1), np.minimum(uh - 1, yy.ravel() >> 1)
            c = cy * uw + cx
            E[holef] = coarse_e[c[holef]]
            s = nnf[c[targets]]
            sx, sy = tx + 2 * (s % uw - cx[targets]), ty + 2 * (s // uw - cy[targets])
            inside = (s >= 0) & (sx >= 0) & (sy >= 0) & (sx < lw) & (sy < lh)
            cand = np.where(inside, sy * lw + sx, 0)
            nxt = np.where(inside & valid[cand], cand, pick(0))
        span2 = max(1.0, hole_size / (1 << level)) ** 2
        wide = [E.astype(np.float64)]      # E in float64, as the browser reads it; refreshed after each vote

        def cost(t, src):
            """Patch distance, estimated pixels counting less, times a preference for nearby sources."""
            E64 = wide[0]
            px, py, sx, sy = t % lw, t // lw, src % lw, src // lw
            d = np.zeros(len(t))
            for dy in range(-pr, pr + 1):
                row, srow = np.clip(py + dy, 0, lh - 1) * lw, (sy + dy) * lw
                for dx in range(-pr, pr + 1):
                    q = row + np.clip(px + dx, 0, lw - 1)
                    e = E64[q] - E64[srow + sx + dx]
                    d += np.where(holef[q], HEAL_GUESS, 1.0) * (e[:, 0] * e[:, 0] + e[:, 1] * e[:, 1] + e[:, 2] * e[:, 2] + e[:, 3] * e[:, 3])
            ddx, ddy = (sx - px).astype(np.float64), (sy - py).astype(np.float64)
            return d * (1 + near * (ddx * ddx + ddy * ddy) / span2)

        def vote(src, D):
            weight = 1 / (1 + D / (area * 4) / 100)
            acc, total = np.zeros((lh * lw, 4)), np.zeros(lh * lw)
            sx, sy = src % lw, src // lw
            E64 = wide[0]
            for dy in range(-pr, pr + 1):
                for dx in range(-pr, pr + 1):
                    x, y = tx + dx, ty + dy
                    ok = (x >= 0) & (y >= 0) & (x < lw) & (y < lh)
                    ok[ok] &= holef[(y * lw + x)[ok]]
                    idx = (y * lw + x)[ok]      # distinct for one offset, so plain indexing adds once each
                    acc[idx] += weight[ok, None] * E64[((sy + dy) * lw + sx + dx)[ok]]
                    total[idx] += weight[ok]
            filled = total > 0
            E[filled] = (acc[filled] / total[filled, None]).astype(np.float32)
            wide[0] = E.astype(np.float64)

        D = cost(targets, nxt)
        if level < count - 1:
            vote(nxt, D)
        reach = max(lw, lh)
        for it in range(HEAL_ITERS if len(targets) <= HEAL_SEARCH else 0):
            prev = np.full(lh * lw, -1, np.int64)
            prev[targets] = nxt
            best = nxt.copy()
            best_d = cost(targets, best)

            def consider(ok, s):
                nonlocal best, best_d
                if not ok.any():
                    return
                d = np.full(len(targets), np.inf)
                d[ok] = cost(targets[ok], s[ok])
                better = d < best_d
                best, best_d = np.where(better, s, best), np.where(better, d, best_d)

            # Neighbours' sources, shifted by one: good matches spread along the patch grid.
            for step, ok in ((-1, tx > 0), (1, tx < lw - 1), (-lw, targets >= lw), (lw, targets + lw < lw * lh)):
                pq = np.where(ok, prev[np.where(ok, targets + step, 0)], -1)
                s = pq - step
                ok = ok & (pq >= 0) & (s >= 0) & (s < lw * lh)
                ok[ok] &= np.abs(s[ok] % lw - pq[ok] % lw) <= 1
                ok[ok] &= valid[s[ok]]
                consider(ok, s)
            # Random search in shrinking windows around the best so far.
            r, step = reach, 1
            while r >= 1:
                h1 = _heal_seed(targets, level, it, step)
                h2 = _hash32(h1)
                bx, by = best % lw, best // lw
                x = np.clip(bx + (h1 % _U32(2 * r + 1)).astype(np.int64) - r, pr, lw - 1 - pr)
                y = np.clip(by + (h2 % _U32(2 * r + 1)).astype(np.int64) - r, pr, lh - 1 - pr)
                s = y * lw + x
                consider(valid[s], s)
                r >>= 1
                step += 1
            nxt, D = best, best_d
            vote(nxt, D)
        nnf = np.full(lh * lw, -1, np.int64)
        nnf[targets] = nxt
        coarse_e = E
    return E.reshape(h, w, 4)


def _without_specks(m, least):
    """m without its 8-connected parts of fewer than least pixels."""
    h, w = m.shape
    flat = m.ravel()
    seen = np.zeros(h * w, bool)
    keep = np.zeros(h * w, bool)
    for start in np.flatnonzero(flat):
        if seen[start]:
            continue
        seen[start] = True
        part, stack = [], [int(start)]
        while stack:
            i = stack.pop()
            part.append(i)
            y, x = divmod(i, w)
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if 0 <= ny < h and 0 <= nx < w:
                        j = ny * w + nx
                        if flat[j] and not seen[j]:
                            seen[j] = True
                            stack.append(j)
        if len(part) >= least:
            keep[part] = True
    return keep.reshape(h, w)


def _text_hole(d, hole, brush):
    """The caption inside a stroke: thin, bright, colourless strokes and their outline. None
    when the stroke holds no such text, or so much (over 70%) that it is not a caption."""
    r8 = d.astype(np.int64)
    lum = (77 * r8[..., 0] + 150 * r8[..., 1] + 29 * r8[..., 2]) >> 8
    chroma = r8[..., :3].max(-1) - r8[..., :3].min(-1)
    core = hole & (lum >= 190) & (chroma <= 60)
    # Letters are thin: what survives thinning by a tenth of the brush (a white shirt, a
    # lamp) is picture, and so is anything right beside it.
    t = max(1, _round(.1 * brush))
    core &= ~_dilate(~_dilate(~core, t), t + 2)
    # Specks (a highlight on a buckle) are not letters either.
    core = _without_specks(core, max(4, _round((.12 * brush) ** 2)))
    found = int(core.sum())
    if not found or found * 10 > int(hole.sum()) * 7:
        return None
    return _dilate(core, max(2, _round(HEAL_TEXT_OUTLINE * brush))) & hole


def _heal(raster, mask, text=False, brush=0):
    """Spot healing of the masked pixels, as the editor does it: a patch-based fill from
    patches around the stroke (only its caption when text is set, and nothing when it holds
    none), or a smooth fill when no patch fits."""
    h, w = raster.shape[:2]
    md = np.rint(mask[..., 3] * 255)
    ys, xs = np.nonzero(md > 8)
    if not len(xs):
        return raster
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    if text:
        # Only the caption is replaced, and the work centres on it rather than on the stroke.
        words = _text_hole(_unpremul8(raster[y0:y1 + 1, x0:x1 + 1]), md[y0:y1 + 1, x0:x1 + 1] > 8, brush)
        if words is None:
            return raster     # no caption under the stroke: nothing to remove
        md = np.zeros_like(md)
        md[y0:y1 + 1, x0:x1 + 1] = np.where(words, 255, 0)
        ys, xs = np.nonzero(md > 8)
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    size = max(x1 - x0 + 1, y1 - y0 + 1)
    margin = max(24, min(HEAL_MARGIN, _round(size * .8)))
    rx0, ry0, rx1, ry1 = max(0, x0 - margin), max(0, y0 - margin), min(w - 1, x1 + margin), min(h - 1, y1 + margin)
    d = _unpremul8(raster[ry0:ry1 + 1, rx0:rx1 + 1])
    m = md[ry0:ry1 + 1, rx0:rx1 + 1]
    hole = m > 8
    fill = _patch_fill(d.astype(np.float32), hole, size, HEAL_NEAR_TEXT if text else HEAL_NEAR)
    if fill is not None:
        k = np.minimum(1, m / 128)[..., None]
        healed = np.where(hole[..., None], np.rint(np.clip(d * (1 - k) + fill * k, 0, 255)), d)
    else:
        known = 1 - m / 255
        smooth = _pull_push(d * known[..., None], known.copy())
        a = (m / 255)[..., None]
        healed = np.where(a > 0, np.rint(np.clip(d * (1 - a) + smooth * a, 0, 255)), d)
    out = raster.copy()
    out[ry0:ry1 + 1, rx0:rx1 + 1] = _premul8(healed)
    return out


def _gradient(h, w, op, path):
    gx, gy = np.meshgrid(np.arange(w) + .5, np.arange(h) + .5)
    x1, y1, x2, y2 = op['from'][0] * w, op['from'][1] * h, op['to'][0] * w, op['to'][1] * h
    if op.get('shape') == 'radial':
        t = np.hypot(gx - x1, gy - y1) / (math.hypot(x2 - x1, y2 - y1) or 1)
    else:
        length = (x2 - x1) ** 2 + (y2 - y1) ** 2
        if not length:
            return np.zeros((h, w, 4), np.float32)
        t = ((gx - x1) * (x2 - x1) + (gy - y1) * (y2 - y1)) / length
    t = np.clip(t, 0, 1)[..., None]
    c1 = np.array(_color(op['c1'], path + '/c1'))
    c2 = c1 * [1, 1, 1, 0] if op['c2'] == 'transparent' else np.array(_color(op['c2'], path + '/c2'))
    c = c1 * (1 - t) + c2 * t
    c[..., :3] *= c[..., 3:]
    return _q(c.astype(np.float32))


# ---------------------------------------------------------------------------
# Fonts: web/fonts.js picks the family, then the sans for what it lacks, then the box.

def _ink(font, ch):
    image = Image.new('L', (96, 96))
    ImageDraw.Draw(image).text((16, 16), ch, font=font, fill=255)
    return image.tobytes()


def _covers(font, text):
    """Whether the font has every character: a missing one draws .notdef."""
    missing = _ink(font, '\U0010FFFD')
    return all(ch.isspace() or _ink(font, ch) != missing for ch in set(text))


_CHOSEN = {}


# Arabic letters take their joined forms and Devanagari puts its vowel signs and joining marks in place only when the
# text is shaped (Pillow with raqm: libraqm and FriBiDi). Basic layout draws Arabic as separate letters left to right
# and Devanagari with its marks out of order, which reads wrong: without raqm these two faces are left out of the
# stack, and a line in these scripts keeps showing the box, as it did before the faces existed.
SHAPED_EXTRA = ('FreeVideoSansArabic-Regular.woff2', 'FreeVideoSansDevanagari-Regular.woff2')


@functools.lru_cache(maxsize=1)
def _raqm():
    from PIL import features
    return bool(features.check('raqm'))


def font_stack(key, weight):
    """The font files of fontStack(): the family, the sans fallback, the other scripts, the box."""
    key = key if key in FONTS else 'sans'
    weight = 700 if (weight or 400) >= 550 else 400   # fontWeight() of web/fonts.js
    extra = [name for name in FONT_EXTRA if name not in SHAPED_EXTRA or _raqm()]
    files = [FONTS[key][weight]] + ([FONTS['sans'][weight]] if key != 'sans' else []) + extra + [FONT_BOX]
    return [str(FONT_DIR / name) for name in files]


class _Runs:
    """A text no single file covers: browsers fall back per character, so each run of
    characters is drawn with the first file of the stack that has them."""

    def __init__(self, fonts):
        self.fonts = fonts   # character -> font

    def runs(self, line):
        out = []
        for ch in line:
            font = self.fonts.get(ch) or self.fonts[None]
            if out and out[-1][1] is font:
                out[-1][0].append(ch)
            else:
                out.append(([ch], font))
        return [(''.join(chars), font) for chars, font in out]

    def getlength(self, line):
        return sum(font.getlength(run) for run, font in self.runs(line))

    def draw(self, draw, xy, line, anchor, **options):
        x, y = xy
        width = self.getlength(line)
        x -= width / 2 if anchor[0] == 'm' else width if anchor[0] == 'r' else 0
        for run, font in self.runs(line):
            draw.text((x, y), run, font=font, anchor='l' + anchor[1], **options)
            x += font.getlength(run)


def _line_length(font, line):
    return font.getlength(line)


def _draw_line(draw, xy, line, font, anchor, **options):
    if isinstance(font, _Runs):
        font.draw(draw, xy, line, anchor, **options)
    else:
        draw.text(xy, line, font=font, anchor=anchor, **options)


def font_for(key, weight, size, text=''):
    """The bundled font that draws this text, and its file.

    Browsers fall back per character. The first file of the stack that has every character
    draws the whole text; when none has them all, each run of characters takes the first file
    that has it; when some character is in none of them, or the line has a letter written right to left and no
    single file has the whole line, the box font draws the whole text (runs would come out in the wrong order)."""
    stack = font_stack(key, weight)
    if (tuple(stack), text) not in _CHOSEN:
        whole = next((path for path in stack[:-1] if _covers(ImageFont.truetype(path, 32), text)), None)
        if whole is None:
            probes = [(path, ImageFont.truetype(path, 32)) for path in stack[:-1]]
            owners = {}
            for ch in set(text):
                if ch != '\n':
                    owners[ch] = next((path for path, probe in probes if _covers(probe, ch)), stack[-1])
            # A character no file has keeps the old result: the box draws the whole text. So does a line with a letter
            # written right to left: its runs would be put in order from the left.
            right_to_left = any(unicodedata.bidirectional(ch) in ('R', 'AL') for ch in text)
            whole = stack[-1] if right_to_left or stack[-1] in owners.values() else tuple(sorted(owners.items()))
        _CHOSEN[(tuple(stack), text)] = whole
    chosen = _CHOSEN[(tuple(stack), text)]
    pixels = max(1, round(size))
    if isinstance(chosen, tuple):
        faces = {}
        for path in set(owner for _, owner in chosen) | {stack[-1]}:
            faces[path] = ImageFont.truetype(path, pixels)
        fonts = {ch: faces[owner] for ch, owner in chosen}
        fonts[None] = faces[stack[-1]]
        return _Runs(fonts), stack[0]
    return ImageFont.truetype(chosen, pixels), chosen


def _top_ascent(size):
    return round(EM_ASCENT * size * 64) / 64


def measure_text(text):
    """measureText of the editor: the native size of a text block."""
    lines = str(text.get('content') or ' ').split('\n')
    font, _ = font_for(text.get('font'), text.get('weight'), text['size'], str(text.get('content') or ''))
    width = max(_line_length(font, line or ' ') for line in lines)
    size, height = text['size'], text.get('lineHeight') or 1.25
    return dict(nw=math.ceil(width + size * .4), nh=math.ceil(size * (.4 + (len(lines) - 1) * height + 1.1)))


def _text_raster(layer, path):
    t = layer['text']
    h, w = _size(layer['nh']), _size(layer['nw'])
    size = t['size']
    content = str(t.get('content', ''))
    font, _ = font_for(t.get('font'), t.get('weight'), size, content)
    ascent = _top_ascent(size)
    align = t.get('align') or 'left'
    x = layer['nw'] / 2 if align == 'center' else layer['nw'] - size * .2 if align == 'right' else size * .2
    anchor = {'center': 'ms', 'right': 'rs'}.get(align, 'ls')
    out = np.zeros((h, w, 4), np.float32)
    fill = _color(t.get('color', '#000'), path + '/text/color')
    outline = t.get('outline')
    stroke = max(1, size * .08) if outline else 0
    for i, line in enumerate(content.split('\n')):
        y = size * .2 + i * size * (t.get('lineHeight') or 1.25) + ascent
        shapes = []
        if outline:
            mask = Image.new('L', (w, h))
            _draw_line(ImageDraw.Draw(mask), (x, y), line, font, anchor, fill=255,
                       stroke_width=max(1, round(stroke / 2)), stroke_fill=255)
            shapes.append((np.asarray(mask, np.float32) / 255, _color(outline, path + '/text/outline')))
        mask = Image.new('L', (w, h))
        _draw_line(ImageDraw.Draw(mask), (x, y), line, font, anchor, fill=255)
        shapes.append((np.asarray(mask, np.float32) / 255, fill))
        for alpha, color in shapes:
            if t.get('shadow'):
                # Canvas shadows: sigma is half of shadowBlur, offset below.
                layer_shadow = np.zeros((h, w, 4), np.float32)
                _draw(layer_shadow, _alpha_raster(_blur_alpha(alpha, size * .18 / 2)), _t(0, size * .05))
                _composite(out, layer_shadow * np.array([0, 0, 0, .55], np.float32))
            paint = _solid(h, w, color) * alpha[..., None]
            _composite(out, paint)
    return _q(out)


def _shape_raster(layer, path, factor=4):
    s = layer['shape']
    nw, nh = layer['nw'], layer['nh']
    h, w = _size(nh), _size(nw)
    lw = s.get('width', 0) if s.get('stroke') else 0
    inset = lw / 2

    def mask(draw):
        big = Image.new('L', (w * factor, h * factor))
        draw(ImageDraw.Draw(big), factor)
        return np.asarray(big.reduce(factor), np.float32) / 255

    def rect(d, k, x0, y0, x1, y1, radius):
        if x1 > x0 and y1 > y0:
            box = [x0 * k, y0 * k, x1 * k - 1, y1 * k - 1]
            d.rounded_rectangle(box, radius=max(0, radius) * k, fill=255) if radius > 0 else d.rectangle(box, fill=255)

    def ellipse(d, k, rx, ry):
        if rx > 0 and ry > 0:
            d.ellipse([(nw / 2 - rx) * k, (nh / 2 - ry) * k, (nw / 2 + rx) * k - 1, (nh / 2 + ry) * k - 1], fill=255)

    out = np.zeros((h, w, 4), np.float32)
    kind = s.get('type', 'rect')
    radius = min(s.get('radius') or 0, (nw - lw) / 2, (nh - lw) / 2)
    if kind == 'line':
        if lw:
            dist = _segments_distance(h, w, [(inset, nh / 2), (nw - inset, nh / 2)], lw / 2)
            _composite(out, _solid(h, w, _color(s['stroke'], path + '/shape/stroke'))
                       * np.clip(lw / 2 - dist + .5, 0, 1)[..., None])
        return _q(out)
    rx, ry = max(1, nw / 2 - inset), max(1, nh / 2 - inset)
    if s.get('fill'):
        area = (mask(lambda d, k: ellipse(d, k, rx, ry)) if kind == 'ellipse'
                else mask(lambda d, k: rect(d, k, inset, inset, nw - inset, nh - inset, radius)))
        _composite(out, _solid(h, w, _color(s['fill'], path + '/shape/fill')) * area[..., None])
    if lw:
        if kind == 'ellipse':
            ring = (mask(lambda d, k: ellipse(d, k, rx + lw / 2, ry + lw / 2))
                    - mask(lambda d, k: ellipse(d, k, rx - lw / 2, ry - lw / 2)))
        else:
            ring = (mask(lambda d, k: rect(d, k, 0, 0, nw, nh, radius + lw / 2 if radius > 0 else 0))
                    - mask(lambda d, k: rect(d, k, lw, lw, nw - lw, nh - lw, max(0, radius - lw / 2))))
        _composite(out, _solid(h, w, _color(s['stroke'], path + '/shape/stroke')) * np.clip(ring, 0, 1)[..., None])
    return _q(out)


# ---------------------------------------------------------------------------
# Documents

def _number(value, path, low=-math.inf, high=math.inf, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EditError(path, 'must be a finite number')
    if value < low or value > high or positive and value <= 0:
        raise EditError(path, 'out of range')
    return value


def _check_selection(selection, path):
    if not isinstance(selection, dict) or not isinstance(selection.get('parts'), list):
        raise EditError(path, 'a selection needs parts')
    _number(selection.get('feather', 0) or 0, path + '/feather', 0)
    for i, part in enumerate(selection['parts']):
        where = '%s/parts/%d' % (path, i)
        if not isinstance(part, dict) or part.get('shape') not in ('rect', 'ellipse', 'all', 'poly', 'wand'):
            raise EditError(where, 'unsupported selection shape')
        if part.get('mode', 'replace') not in ('replace', 'add', 'subtract', 'intersect'):
            raise EditError(where + '/mode', 'unsupported mode')
        if part['shape'] in ('rect', 'ellipse'):
            for key in ('x', 'y', 'w', 'h'):
                _number(part.get(key), '%s/%s' % (where, key))
        if part['shape'] == 'poly' and not (isinstance(part.get('pts'), list)
                                           and all(isinstance(p, list) and len(p) == 2 for p in part['pts'])):
            raise EditError(where + '/pts', 'points must be [x, y] pairs')
        if part['shape'] == 'wand':
            for key in ('u', 'v', 'tolerance'):
                _number(part.get(key), '%s/%s' % (where, key))


def _check_op(op, path, mask=False):
    if not isinstance(op, dict) or op.get('type') not in OPS:
        raise EditError(path, 'unsupported operation %r' % (op.get('type') if isinstance(op, dict) else op,))
    if op.get('selection') is not None:
        _check_selection(op['selection'], path + '/selection')
    _number(op.get('opacity', 1) if op.get('opacity') is not None else 1, path + '/opacity', 0, 1)
    if op['type'] == 'stroke':
        if op.get('tool') not in TOOLS:
            raise EditError(path + '/tool', 'unsupported tool %r' % (op.get('tool'),))
        if not isinstance(op.get('pts'), list) or not op['pts'] or not all(
                isinstance(p, (list, tuple)) and len(p) == 2 for p in op['pts']):
            raise EditError(path + '/pts', 'a stroke needs [u, v] points')
        _number(op.get('size'), path + '/size', positive=True)
        if op.get('text') not in (None, True, False):
            raise EditError(path + '/text', 'text is true or false')
        if op['tool'] == 'clone' and not (isinstance(op.get('offset'), list) and len(op['offset']) == 2):
            raise EditError(path + '/offset', 'clone needs an offset')
        if op['tool'] in ('paint',) and not mask:
            _color(op.get('color'), path + '/color')
    elif op['type'] == 'gradient':
        for key in ('from', 'to'):
            if not (isinstance(op.get(key), list) and len(op[key]) == 2):
                raise EditError('%s/%s' % (path, key), 'a gradient needs from and to points')


def _check(doc):
    if not isinstance(doc, dict) or doc.get('version') != 2:
        raise EditError('/version', 'only version 2 editor documents are supported')
    for key in ('width', 'height'):
        _number(doc.get(key), '/' + key, positive=True)
    if doc.get('rotate') not in (0, 90, 180, 270):
        raise EditError('/rotate', 'must be 0, 90, 180 or 270')
    for key in ('flipX', 'flipY'):
        if not isinstance(doc.get(key), bool):
            raise EditError('/' + key, 'must be true or false')
    e = doc.get('extend')
    if not isinstance(e, dict):
        raise EditError('/extend', 'missing')
    for key in 'ltrb':
        _number(e.get(key), '/extend/' + key, 0)
    _color(e.get('color', '#000000'), '/extend/color')
    c = doc.get('crop')
    if not isinstance(c, dict):
        raise EditError('/crop', 'missing')
    for key in ('x', 'y', 'w', 'h'):
        _number(c.get(key), '/crop/' + key, positive=key in 'wh')
    r = canvas_rect(doc)
    if r['w'] * r['h'] > MAX_PIXELS or c['w'] * c['h'] > MAX_PIXELS:
        raise EditError('/extend', 'the canvas is larger than %d pixels' % MAX_PIXELS)
    layers = doc.get('layers')
    if not isinstance(layers, list) or not layers:
        raise EditError('/layers', 'a document needs layers')
    ids = {layer.get('id') for layer in layers if isinstance(layer, dict)}
    for i, layer in enumerate(layers):
        path = '/layers/%d' % i
        if not isinstance(layer, dict) or layer.get('kind') not in ('image', 'paint', 'text', 'shape'):
            raise EditError(path + '/kind', 'unsupported layer kind')
        for key in ('cx', 'cy', 'rotation'):
            _number(layer.get(key), '%s/%s' % (path, key))
        for key in ('w', 'h', 'nw', 'nh'):
            _number(layer.get(key), '%s/%s' % (path, key), positive=True)
        if layer['nw'] * layer['nh'] > MAX_PIXELS:
            raise EditError(path, 'the layer is larger than %d pixels' % MAX_PIXELS)
        _number(layer.get('opacity', 1), path + '/opacity', 0, 1)
        if layer.get('blend', 'source-over') not in BLENDS:
            raise EditError(path + '/blend', 'unsupported blend %r' % (layer.get('blend'),))
        for key, value in (layer.get('adjust') or {}).items():
            if key not in ADJUSTMENTS:
                raise EditError('%s/adjust/%s' % (path, key), 'unknown adjustment')
            _number(value or 0, '%s/adjust/%s' % (path, key))
        for j, op in enumerate(layer.get('ops') or []):
            _check_op(op, '%s/ops/%d' % (path, j))
        if layer.get('mask') is not None:
            if not isinstance(layer['mask'], dict) or not isinstance(layer['mask'].get('ops'), list):
                raise EditError(path + '/mask', 'a mask needs ops')
            for j, op in enumerate(layer['mask']['ops']):
                _check_op(op, '%s/mask/ops/%d' % (path, j), mask=True)
        if layer.get('copyOf') and layer['copyOf'].get('layer') not in ids:
            raise EditError(path + '/copyOf/layer', 'unknown layer')
        if layer['kind'] == 'text':
            t = layer.get('text')
            if not isinstance(t, dict) or not isinstance(t.get('content'), str):
                raise EditError(path + '/text', 'a text layer needs content')
            _number(t.get('size'), path + '/text/size', positive=True)
            _color(t.get('color', '#000'), path + '/text/color')
        if layer['kind'] == 'shape':
            s = layer.get('shape')
            if not isinstance(s, dict) or s.get('type') not in ('rect', 'ellipse', 'line'):
                raise EditError(path + '/shape/type', 'unsupported shape')
            if s.get('stroke'):
                _number(s.get('width'), path + '/shape/width', positive=True)


class _Renderer:
    def __init__(self, doc, resolve):
        self.doc, self.resolve, self.images, self.rasters = doc, resolve, {}, {}
        self.layers = {layer['id']: (i, layer) for i, layer in enumerate(doc['layers'])}

    def image(self, src, path):
        if src not in self.images:
            try:
                with Image.open(self.resolve(src)) as source:
                    if source.width * source.height > MAX_PIXELS:
                        raise EditError(path, 'the image is larger than %d pixels' % MAX_PIXELS)
                    self.images[src] = _from_image(ImageOps.exif_transpose(source))
            except (OSError, ValueError, KeyError) as error:
                if isinstance(error, EditError):
                    raise
                raise EditError(path, 'cannot read image %r' % (src,)) from None
        return self.images[src]

    def raster(self, layer, upto=None):
        index = self.layers[layer['id']][0]
        ops = layer.get('ops') or []
        upto = len(ops) if upto is None else max(0, min(len(ops), int(upto)))
        key = (layer['id'], upto)
        if key in self.rasters:
            return self.rasters[key]
        h, w = _size(layer['nh']), _size(layer['nw'])
        out = np.zeros((h, w, 4), np.float32)
        if layer['kind'] == 'image' and layer.get('src'):
            img = self.image(layer['src'], '/layers/%d/src' % index)
            _draw(out, img, _s(w / img.shape[1], h / img.shape[0]))
        copy = layer.get('copyOf')
        if copy:
            _, source = self.layers[copy['layer']]
            src = self.raster(source, copy.get('upto'))
            _draw(out, src, _s(w / src.shape[1], h / src.shape[0]))
            if copy.get('selection'):
                _mask_with(out, self.selection(copy['selection'], source))
        out = _q(out)
        for j, op in enumerate(ops[:upto]):
            out = self.apply(layer, out, op, '/layers/%d/ops/%d' % (index, j))
        self.rasters[key] = out
        return out

    def mask(self, layer):
        index = self.layers[layer['id']][0]
        out = np.ones((_size(layer['nh']), _size(layer['nw']), 4), np.float32)
        for j, op in enumerate(layer['mask']['ops']):
            out = self.apply(layer, out, op, '/layers/%d/mask/ops/%d' % (index, j), is_mask=True)
        return out

    def apply(self, layer, target, op, path, is_mask=False):
        h, w = target.shape[:2]
        selection = self.selection(op['selection'], layer) if op.get('selection') else None
        def within(img):
            return _mask_with(img, selection) if selection is not None else img
        opacity = op.get('opacity') if op.get('opacity') is not None else 1
        target = target.copy()
        kind = op['type']
        if kind == 'fromSelection':
            target = selection.copy() if selection is not None else np.ones((h, w, 4), np.float32)
            if op.get('invert'):
                _composite(target, np.ones((h, w, 4), np.float32), 'xor')
            return _q(target)
        if kind in ('fill', 'clear'):
            fill = within(_solid(h, w, (1, 1, 1, 1) if is_mask else _color(op.get('color') or '#000', path + '/color')))
            _composite(target, fill * np.float32(opacity), 'destination-out' if kind == 'clear' else 'source-over')
            return _q(target)
        if kind == 'gradient':
            g = within(_gradient(h, w, op, path))
            _composite(target, g * np.float32(opacity))
            return _q(target)
        mask = within(_stroke_mask(h, w, op))
        tool = op['tool']
        if tool in ('paint', 'reveal'):
            color = (1, 1, 1, 1) if tool == 'reveal' else _color(op.get('color'), path + '/color')
            _composite(target, _mask_with(_solid(h, w, color), mask) * np.float32(opacity))
        elif tool in ('erase', 'hide'):
            _composite(target, mask * np.float32(opacity), 'destination-out')
        elif tool == 'clone':
            copy = np.zeros_like(target)
            _draw(copy, target, _t(op['offset'][0] * w, op['offset'][1] * h))
            _composite(target, _mask_with(_q(copy), mask) * np.float32(opacity))
        elif tool == 'blur':
            strength = op.get('strength') if op.get('strength') is not None else .6
            radius = max(1, op['size'] * min(w, h) * .25 * strength)
            _composite(target, _mask_with(_blurred(target, radius), mask) * np.float32(opacity))
        elif tool == 'heal':
            target = _heal(target, mask, text=bool(op.get('text')), brush=op['size'] * min(w, h))
        return _q(target)

    def selection(self, selection, layer):
        h, w = _size(layer['nh']), _size(layer['nw'])
        out = np.zeros((h, w, 4), np.float32)
        to_local = np.linalg.inv(layer_matrix(layer))
        for part in selection['parts']:
            mode = part.get('mode', 'replace')
            op = {'subtract': 'destination-out', 'intersect': 'destination-in'}.get(mode, 'source-over')
            if mode == 'replace':
                out[...] = 0
            shape = part['shape']
            if shape == 'wand':
                if part.get('layer') not in self.layers:
                    continue
                _, src = self.layers[part['layer']]
                raster = self.raster(src, part.get('upto'))
                m = wand(raster, part['u'], part['v'], part['tolerance'], part.get('contiguous', True))
                _draw(out, m, to_local @ layer_matrix(src), op=op)
                continue
            if shape == 'all':
                alpha = np.ones((h, w), np.float32)
            else:
                if shape == 'rect':
                    x, y, pw, ph = part['x'], part['y'], part['w'], part['h']
                    pts = [(x, y), (x + pw, y), (x + pw, y + ph), (x, y + ph)]
                elif shape == 'ellipse':
                    cx, cy = part['x'] + part['w'] / 2, part['y'] + part['h'] / 2
                    rx, ry = abs(part['w'] / 2), abs(part['h'] / 2)
                    pts = [(cx + rx * math.cos(a), cy + ry * math.sin(a))
                           for a in np.linspace(0, 2 * math.pi, 256, endpoint=False)]
                else:
                    pts = [tuple(p) for p in part.get('pts') or []]
                    if len(pts) < 3:
                        continue
                pts = [_apply(to_local, x, y) for x, y in pts]
                alpha = _polygon_alpha(h, w, pts)
            _composite(out, _alpha_raster(alpha), op)
        if selection.get('invert'):
            _composite(out, np.ones((h, w, 4), np.float32), 'xor')
        if (selection.get('feather') or 0) > 0:
            px = selection['feather'] * max(layer['nw'] / layer['w'], layer['nh'] / layer['h'])
            return _alpha_raster(_blur_alpha(out[..., 3], px))
        return _q(out)

    def layer_image(self, layer, index):
        path = '/layers/%d' % index
        if layer['kind'] == 'text':
            return _text_raster(layer, path)
        if layer['kind'] == 'shape':
            return _shape_raster(layer, path)
        raster = self.raster(layer)
        out = adjusted(raster, layer.get('adjust'), hash_string(layer['id']))
        if layer.get('mask') is not None:
            out = _mask_with(out.copy(), self.mask(layer))
        return _q(out)

    def composite(self, background=True):
        doc = self.doc
        rect = canvas_rect(doc)
        h, w = _size(rect['h']), _size(rect['w'])
        e = doc['extend']
        out = (_solid(h, w, _color(e.get('color', '#000000'))) if background and (e['l'] or e['t'] or e['r'] or e['b'])
               else np.zeros((h, w, 4), np.float32))
        view = _chain(_t(-rect['x'], -rect['y']), orient_matrix(doc))
        for i, layer in enumerate(doc['layers']):
            if not layer.get('visible', True):
                continue
            image = self.layer_image(layer, i)
            _draw(out, image, view @ layer_matrix(layer), alpha=layer.get('opacity', 1),
                  op=layer.get('blend') or 'source-over')
            out = _q(out)
        return out


def render(doc, resolve, *, jpeg=False):
    """The document's saved picture, as the editor exports it: the crop of the
    composited canvas, RGBA (or RGB on the background colour for JPEG)."""
    _check(doc)
    full = _Renderer(doc, resolve).composite()
    rect, c = canvas_rect(doc), doc['crop']
    w, h = _size(c['w']), _size(c['h'])
    sx, sy = _round(c['x'] - rect['x']), _round(c['y'] - rect['y'])
    out = np.zeros((h, w, 4), np.float32)
    _draw(out, full, _t(-sx, -sy))
    if not jpeg:
        return _to_image(out)
    matte = _solid(h, w, _color(doc['extend'].get('color') or '#000'))
    _composite(matte, out)
    return _to_image(matte).convert('RGB')


def save(doc, resolve, path):
    """Render to a .png or .jpg file; returns its size."""
    path = Path(path)
    jpeg = path.suffix.lower() in ('.jpg', '.jpeg')
    image = render(doc, resolve, jpeg=jpeg)
    if jpeg:
        image.save(path, 'JPEG', quality=95)
    else:
        image.save(path, 'PNG')
    return dict(width=image.width, height=image.height, path=str(path))


# ---------------------------------------------------------------------------
# Recipes: what an agent asks for, compiled to the editor's document.

_counter = itertools.count()


def _base36(n):
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    out = ''
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if not n:
            return out


def uid(prefix):
    return prefix + _base36(int(time.time() * 1000)) + _base36(next(_counter))


def empty_adjust():
    return {k: 0 for k in ADJUSTMENTS}


def new_document(source, width, height):
    base = dict(id='base', kind='image', name='', src=source, visible=True, locked=True, opacity=1,
                blend='source-over', cx=width / 2, cy=height / 2, w=width, h=height, rotation=0, flipX=False,
                flipY=False, nw=width, nh=height, ops=[], mask=None, adjust=empty_adjust())
    return dict(version=2, width=width, height=height, rotate=0, flipX=False, flipY=False,
                extend=dict(l=0, t=0, r=0, b=0, color='#000000'), crop=dict(x=0, y=0, w=width, h=height),
                ratio='original', layers=[base], active='base')


def _fit_rect(aspect, r):
    if not aspect:
        return dict(r)
    w, h = r['w'], r['w'] / aspect
    if h > r['h']:
        h = r['h']
        w = h * aspect
    return dict(x=r['x'] + (r['w'] - w) / 2, y=r['y'] + (r['h'] - h) / 2, w=w, h=h)


def _aspect(ratio, r, target, path):
    if ratio == 'video':
        if not target:
            raise EditError(path, 'the video size is unknown here; give a ratio such as 16:9')
        return target[0] / target[1]
    if ratio == 'original':
        return r['w'] / r['h']
    if ratio == 'free':
        return None
    if ratio in RATIOS:
        return RATIOS[ratio]
    try:
        a, b = (float(x) for x in str(ratio).split(':'))
        if a > 0 and b > 0 and math.isfinite(a / b):
            return a / b
    except ValueError:
        pass
    raise EditError(path, "use 'video', 'original', 'free' or W:H such as 16:9")


def _turn(doc, op, target):
    """Rotate or mirror the canvas exactly as the editor's buttons do."""
    before, e, nxt = canvas_rect(doc), doc['extend'], dict(doc)
    if op in ('cw', 'ccw'):
        mirrored = doc['flipX'] != doc['flipY']
        nxt['rotate'] = (doc['rotate'] + (90 if (op == 'cw') != mirrored else 270)) % 360
        nxt['extend'] = (dict(e, l=e['b'], t=e['l'], r=e['t'], b=e['r']) if op == 'cw'
                         else dict(e, l=e['t'], t=e['r'], r=e['b'], b=e['l']))
    else:
        nxt[op] = not doc[op]
        nxt['extend'] = dict(e, l=e['r'], r=e['l']) if op == 'flipX' else dict(e, t=e['b'], b=e['t'])

    def where(x, y):
        u, v = (x - before['x']) / before['w'], (y - before['y']) / before['h']
        return {'cw': (1 - v, u), 'ccw': (v, 1 - u), 'flipX': (1 - u, v), 'flipY': (u, 1 - v)}[op]
    after, c = canvas_rect(nxt), doc['crop']
    a, b = where(c['x'], c['y']), where(c['x'] + c['w'], c['y'] + c['h'])
    crop = dict(x=after['x'] + min(a[0], b[0]) * after['w'], y=after['y'] + min(a[1], b[1]) * after['h'],
                w=abs(a[0] - b[0]) * after['w'], h=abs(a[1] - b[1]) * after['h'])
    if op in ('cw', 'ccw') and doc['ratio'] != 'free':
        aspect = _aspect(doc['ratio'], after, target, '/ratio') if doc['ratio'] != 'video' or target else None
        if aspect:
            crop = _fit_rect(aspect, after)
    nxt['crop'] = crop
    return nxt


def _check_keys(op, allowed, path):
    for key in op:
        if key not in allowed and key != 'op':
            raise EditError('%s/%s' % (path, key), 'unknown key for %s' % op['op'])


def _opt(op, key, path, default=None, low=-math.inf, high=math.inf, positive=False):
    if op.get(key) is None:
        return default
    return _number(op[key], '%s/%s' % (path, key), low, high, positive)


def _choice(op, key, path, options, default):
    value = op.get(key, default)
    if value not in options:
        raise EditError('%s/%s' % (path, key), 'use one of %s' % ', '.join(map(str, options)))
    return value


def _color_key(op, key, path, default):
    value = op.get(key, default)
    if value is not None and value is not False:
        _color(value, '%s/%s' % (path, key))
    return value


def _place(op, path, frame, w, h):
    """Top-left of a w x h box in the frame: x/y, or an anchor and margin."""
    margin = _opt(op, 'margin', path, 0, 0)
    anchor = _choice(op, 'anchor', path, ANCHORS, 'center')
    hx = 0 if 'left' in anchor else 2 if 'right' in anchor else 1
    vy = 0 if anchor.startswith('top') else 2 if anchor.startswith('bottom') else 1
    x = [margin, (frame['w'] - w) / 2, frame['w'] - w - margin][hx]
    y = [margin, (frame['h'] - h) / 2, frame['h'] - h - margin][vy]
    return _opt(op, 'x', path, x), _opt(op, 'y', path, y)


def _add_layer(doc, layer, x, y, angle):
    """Insert a layer whose box is at (x, y) in the frame, upright on screen."""
    c = doc['crop']
    centre = _apply(np.linalg.inv(orient_matrix(doc)), c['x'] + x + layer['w'] / 2, c['y'] + y + layer['h'] / 2)
    mirrored = doc['flipX'] != doc['flipY']
    layer.update(cx=float(centre[0]), cy=float(centre[1]), rotation=(-angle if mirrored else angle) - doc['rotate'],
                 flipX=doc['flipX'], flipY=doc['flipY'])
    index = next((i for i, l in enumerate(doc['layers']) if l['id'] == doc['active']), 0)
    doc['layers'].insert(index + 1, layer)
    doc['active'] = layer['id']


def _common(op, path, name):
    return dict(name=str(op.get('name') or name), visible=True, locked=False,
                opacity=_opt(op, 'opacity', path, 1, 0, 1), blend=_choice(op, 'blend', path, BLENDS, 'source-over'),
                ops=[], mask=None, adjust=empty_adjust())


SPEC = {
    'crop': dict(keys=('ratio', 'x', 'y', 'width', 'height', 'anchor', 'zoom'),
                 about='Keep part of the canvas: a ratio (video, original, free, W:H) placed by anchor and zoom, '
                       'or an explicit x/y/width/height box in canvas pixels.'),
    'rotate': dict(keys=('degrees',),
                   about='Turn the canvas clockwise by 90, 180 or 270 degrees (negative turns left).'),
    'flip': dict(keys=('axis',), about='Mirror the canvas horizontally or vertically.'),
    'pad': dict(keys=('to', 'left', 'top', 'right', 'bottom', 'color'),
                about='Grow the frame around the current crop to a ratio, or by pixels per side; '
                      'beyond the picture the background colour shows.'),
    'fit': dict(keys=('width', 'height', 'mode', 'color'),
                about='Give the frame the shape of width x height: cover crops, contain pads. '
                      'Pixel size stays the source resolution; the video scales keyframes itself.'),
    'adjust': dict(keys=('layer',) + ADJUSTMENTS, about='Set light, colour and effect adjustments of a layer.'),
    'overlay': dict(keys=('image', 'x', 'y', 'width', 'height', 'scale', 'anchor', 'margin', 'rotation',
                          'opacity', 'blend', 'name'),
                    about='Add an image layer (another media file) placed in the frame.'),
    'text': dict(keys=('text', 'x', 'y', 'anchor', 'margin', 'size', 'color', 'font', 'weight', 'align', 'shadow',
                       'outline', 'line_height', 'rotation', 'opacity', 'blend', 'name'),
                 about='Add a text layer.'),
    'shape': dict(keys=('type', 'x', 'y', 'width', 'height', 'from', 'to', 'anchor', 'margin', 'fill', 'stroke',
                        'stroke_width', 'radius', 'rotation', 'opacity', 'blend', 'name'),
                  about='Add a rectangle, ellipse or line layer.'),
}
SPEC['extend'] = SPEC['pad']
SPEC['remove_text'] = dict(keys=('x', 'y', 'width', 'height', 'text_height'),
                           about='Remove burned-in captions inside a box of the frame (default: its lower 40%): '
                                 'white letters and their outline are filled from the picture around them, and '
                                 'everything else in the box stays. A box without such text is left as it is.')


def _remove_text(doc, op, path, frame):
    """A captions-only heal stroke on the picture, swept row by row over a box of the frame."""
    fw, fh = frame['w'], frame['h']
    x = min(_opt(op, 'x', path, 0, 0), fw - 1)
    y = min(_opt(op, 'y', path, fh * .6, 0), fh - 1)
    width = min(_opt(op, 'width', path, fw - x, positive=True), fw - x)
    height = min(_opt(op, 'height', path, fh - y, positive=True), fh - y)
    th = _opt(op, 'text_height', path, max(12, min(fw, fh) / 16), 4, 2000)
    layer = doc['layers'][0]
    to_doc, to_layer = np.linalg.inv(orient_matrix(doc)), np.linalg.inv(layer_matrix(layer))

    def local(px, py):
        lx, ly = _apply(to_layer, *_apply(to_doc, frame['x'] + px, frame['y'] + py))
        return [round(float(lx) / layer['nw'], 5), round(float(ly) / layer['nh'], 5)]
    # A brush as tall as the letters, in rows 0.8 of it apart, so the box is covered.
    rows = max(1, math.ceil(max(0, height - th) / (th * .8)) + 1)
    pts = []
    for i in range(rows):
        ry = y + height / 2 if height <= th else y + th / 2 + (height - th) * (i / (rows - 1) if rows > 1 else 0)
        ends = [x + width / 2] * 2 if width <= th else [x + th / 2, x + width - th / 2]
        for px in (ends[::-1] if i % 2 else ends):
            pts.append(local(px, ry))
    size = th * (layer['nw'] / layer['w']) / min(layer['nw'], layer['nh'])
    layer['ops'].append({'id': uid('o'), 'type': 'stroke', 'tool': 'heal', 'text': True, 'pts': pts, 'size': size,
                         'hardness': 1, 'opacity': 1})
    return doc


def _layer_ref(doc, value, path):
    if value in (None, 'base'):
        return doc['layers'][0]
    if value == 'last':
        return doc['layers'][-1]
    for layer in doc['layers']:
        if layer['name'] == value or layer['id'] == value:
            return layer
    raise EditError(path, 'no layer named %r' % (value,))


def compile_recipe(recipe, *, source=None, source_size=None, target=None, resolve=None, base=None):
    """An editor document for {"operations": [...]}, applied in order.

    source is the original image file (the base layer), source_size its
    upright (width, height); target the video's (width, height) for ratio
    'video'; resolve(file) -> path is needed to size overlay images; base
    continues an existing document instead of starting from the original.
    Positions are pixels of the current frame (the crop), from its top left.
    Overlay files go through resolve, which must refuse paths outside the
    media folder.
    """
    if not isinstance(recipe, dict):
        raise EditError('/', 'a recipe is an object with operations')
    for key in recipe:
        if key not in ('operations',):
            raise EditError('/' + key, 'unknown key')
    operations = recipe.get('operations')
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS:
        raise EditError('/operations', 'give 1 to %d operations' % MAX_OPERATIONS)
    if target is not None:
        target = (_number(target[0], '/target/0', positive=True), _number(target[1], '/target/1', positive=True))
    if base is not None:
        _check(base)
        import copy
        doc = copy.deepcopy(base)
    else:
        if not isinstance(source, str) or not source:
            raise EditError('/source', 'name the original image file')
        if not isinstance(source_size, (list, tuple)) or len(source_size) != 2:
            raise EditError('/source_size', 'give the image width and height')
        w, h = source_size
        doc = new_document(source, _number(w, '/source_size/0', positive=True),
                           _number(h, '/source_size/1', positive=True))
    for i, op in enumerate(operations):
        path = '/operations/%d' % i
        if not isinstance(op, dict) or op.get('op') not in SPEC:
            raise EditError(path + '/op', 'use one of %s' % ', '.join(SPEC))
        _check_keys(op, SPEC[op['op']]['keys'], path)
        doc = _operation(doc, op, path, target, resolve)
    _check(doc)
    return doc


def _operation(doc, op, path, target, resolve):
    kind = op['op']
    frame = doc['crop']
    if kind == 'rotate':
        degrees = op.get('degrees')
        if degrees not in (90, 180, 270, -90, -180, -270):
            raise EditError(path + '/degrees', 'use 90, 180 or 270 (negative turns left)')
        for _ in range(abs(degrees) // 90):
            doc = _turn(doc, 'cw' if degrees > 0 else 'ccw', target)
        return doc
    if kind == 'flip':
        axis = _choice(op, 'axis', path, ('horizontal', 'vertical'), 'horizontal')
        return _turn(doc, 'flipX' if axis == 'horizontal' else 'flipY', target)
    if kind == 'crop':
        r = canvas_rect(doc)
        if any(op.get(k) is not None for k in ('x', 'y', 'width', 'height')):
            if op.get('anchor') is not None or op.get('zoom') is not None:
                raise EditError(path, 'give either a box (x, y, width, height) or a ratio with anchor and zoom')
            x, y = _opt(op, 'x', path, 0, 0), _opt(op, 'y', path, 0, 0)
            w = _opt(op, 'width', path, r['w'] - x, positive=True)
            h = _opt(op, 'height', path, r['h'] - y, positive=True)
            if x + w > r['w'] + 1e-6 or y + h > r['h'] + 1e-6:
                raise EditError(path, 'the box leaves the %g x %g canvas' % (r['w'], r['h']))
            ratio = op.get('ratio', 'free')
            if ratio != 'free':
                aspect = _aspect(ratio, r, target, path + '/ratio')
                if abs(w / h - aspect) > .01 * aspect:
                    raise EditError(path + '/ratio', 'the box is not %s' % ratio)
            doc = dict(doc, crop=dict(x=r['x'] + x, y=r['y'] + y, w=w, h=h),
                       ratio=ratio if ratio in RATIO_IDS else 'free')
            return doc
        ratio = op.get('ratio', 'original')
        aspect = _aspect(ratio, r, target, path + '/ratio')
        zoom = _opt(op, 'zoom', path, 1, 1, 100)
        fitted = _fit_rect(aspect, r)
        w, h = fitted['w'] / zoom, fitted['h'] / zoom
        x, y = _place(dict(anchor=op.get('anchor', 'center')), path, r, w, h)
        return dict(doc, crop=dict(x=r['x'] + x, y=r['y'] + y, w=w, h=h), ratio=ratio if ratio in RATIO_IDS else 'free')
    if kind in ('pad', 'extend', 'fit'):
        color = _color_key(op, 'color', path, doc['extend'].get('color') or '#000000')
        c = doc['crop']
        if kind == 'fit':
            width, height = _opt(op, 'width', path, positive=True), _opt(op, 'height', path, positive=True)
            if width is None or height is None:
                raise EditError(path, 'fit needs width and height')
            mode = _choice(op, 'mode', path, ('cover', 'contain'), 'cover')
            same = target and abs(width / height - target[0] / target[1]) < 1e-9
            ratio = 'video' if same else '%g:%g' % (width, height)
            if mode == 'cover':
                return _operation(doc, dict(op='crop', ratio=ratio), path, target or (width, height), resolve)
            return _operation(doc, dict(op='pad', to=ratio, color=color), path, target or (width, height), resolve)
        sides = [op.get(k) for k in ('left', 'top', 'right', 'bottom')]
        if op.get('to') is not None:
            if any(s is not None for s in sides):
                raise EditError(path, 'give either to or pixels per side')
            aspect = _aspect(op['to'], c, target, path + '/to')
            l = t = r = b = 0
            if aspect and c['w'] / c['h'] < aspect:
                extra = _round(c['h'] * aspect - c['w'])
                l = math.floor(extra / 2)
                r = extra - l
            elif aspect:
                extra = _round(c['w'] / aspect - c['h'])
                t = math.floor(extra / 2)
                b = extra - t
            ratio = op['to'] if op['to'] in RATIO_IDS and op['to'] != 'original' else 'free'
        else:
            l, t, r, b = (_opt(op, k, path, 0, 0, 20000) for k in ('left', 'top', 'right', 'bottom'))
            ratio = 'free'
        crop = dict(x=c['x'] - l, y=c['y'] - t, w=c['w'] + l + r, h=c['h'] + t + b)
        (ow, oh), e = oriented_size(doc), doc['extend']
        extend = dict(l=max(e['l'], -crop['x']), t=max(e['t'], -crop['y']), r=max(e['r'], crop['x'] + crop['w'] - ow),
                      b=max(e['b'], crop['y'] + crop['h'] - oh), color=color)
        return dict(doc, extend=extend, crop=crop, ratio=ratio)
    import copy
    doc = copy.deepcopy(doc)
    if kind == 'remove_text':
        return _remove_text(doc, op, path, frame)
    if kind == 'adjust':
        layer = _layer_ref(doc, op.get('layer'), path + '/layer')
        if layer['kind'] not in ('image', 'paint'):
            raise EditError(path + '/layer', 'only image and paint layers take adjustments')
        for key in ADJUSTMENTS:
            if op.get(key) is not None:
                layer['adjust'][key] = _number(op[key], '%s/%s' % (path, key), *RANGES[key])
        return doc
    rotation = _opt(op, 'rotation', path, 0, -360, 360)
    if kind == 'overlay':
        if not isinstance(op.get('image'), str) or not op['image']:
            raise EditError(path + '/image', 'name the media file to place')
        if resolve is None:
            raise EditError(path + '/image', 'overlay images cannot be read here')
        try:
            with Image.open(resolve(op['image'])) as image:
                nw, nh = ImageOps.exif_transpose(image).size
        except (OSError, ValueError, KeyError):
            raise EditError(path + '/image', 'cannot read image %r' % (op['image'],)) from None
        width, height = _opt(op, 'width', path, positive=True), _opt(op, 'height', path, positive=True)
        scale = _opt(op, 'scale', path, positive=True)
        if scale is not None and (width or height):
            raise EditError(path + '/scale', 'give scale or a width/height, not both')
        if width is None and height is None:
            s = scale if scale is not None else min(1, frame['w'] * .66 / nw, frame['h'] * .66 / nh)
            width, height = nw * s, nh * s
        elif width is None:
            width = height * nw / nh
        elif height is None:
            height = width * nh / nw
        layer = dict(id=uid('l'), kind='image', src=op['image'], w=width, h=height, nw=nw, nh=nh,
                     **_common(op, path, Path(op['image']).name))
        x, y = _place(op, path, frame, width, height)
        _add_layer(doc, layer, x, y, rotation)
        return doc
    if kind == 'text':
        content = op.get('text')
        if not isinstance(content, str) or not content.strip() or len(content) > 2000:
            raise EditError(path + '/text', 'give 1 to 2000 characters of text')
        size = _opt(op, 'size', path, max(16, _round(min(doc['width'], doc['height']) / 12)), 6, 2000)
        outline = op.get('outline', False)
        if outline is True:
            outline = '#000000'
        if outline is not False:
            _color(outline, path + '/outline')
        if op.get('shadow', True) not in (True, False):
            raise EditError(path + '/shadow', 'must be true or false')
        text = dict(content=content, font=_choice(op, 'font', path, tuple(FONTS), 'sans'), size=size,
                    color=_color_key(op, 'color', path, '#ffffff'), weight=_choice(op, 'weight', path, (400, 700), 700),
                    align=_choice(op, 'align', path, ('left', 'center', 'right'), 'left'),
                    shadow=op.get('shadow', True), outline=outline,
                    lineHeight=_opt(op, 'line_height', path, 1.25, .5, 4))
        measured = measure_text(text)
        layer = dict(id=uid('l'), kind='text', text=text, w=measured['nw'], h=measured['nh'], **measured,
                     **_common(op, path, 'Text'))
        x, y = _place(op, path, frame, layer['w'], layer['h'])
        _add_layer(doc, layer, x, y, rotation)
        return doc
    # shape
    kind = _choice(op, 'type', path, ('rect', 'ellipse', 'line'), 'rect')
    width = _opt(op, 'stroke_width', path, 6, 1, 400)
    fill = _color_key(op, 'fill', path, None if kind == 'line' else '#ffffff')
    stroke = _color_key(op, 'stroke', path, '#ffffff' if kind == 'line' else None)
    if kind == 'line':
        for key in ('x', 'y', 'width', 'height', 'anchor', 'margin', 'rotation', 'radius'):
            if op.get(key) is not None:
                raise EditError('%s/%s' % (path, key), 'a line is placed by from and to')
        ends = [op.get('from'), op.get('to')]
        if not all(isinstance(p, list) and len(p) == 2 for p in ends):
            raise EditError(path, 'a line needs from and to as [x, y] in the frame')
        (x1, y1), (x2, y2) = [(_number(p[0], '%s/%s/0' % (path, k)), _number(p[1], '%s/%s/1' % (path, k)))
                              for k, p in zip(('from', 'to'), ends)]
        if fill:
            raise EditError(path + '/fill', 'a line has no fill')
        w, h = math.hypot(x2 - x1, y2 - y1), max(width * 2, 8)
        if w < 1:
            raise EditError(path, 'the line is too short')
        rotation = math.degrees(math.atan2(y2 - y1, x2 - x1))
        x, y = (x1 + x2) / 2 - w / 2, (y1 + y2) / 2 - h / 2
    else:
        if op.get('from') is not None or op.get('to') is not None:
            raise EditError(path, 'from and to are for lines; give x, y, width and height')
        w, h = _opt(op, 'width', path, positive=True), _opt(op, 'height', path, positive=True)
        if w is None or h is None:
            raise EditError(path, 'a %s needs width and height' % kind)
        x, y = _place(op, path, frame, w, h)
    if not fill and not stroke:
        raise EditError(path, 'give a fill or a stroke colour')
    shape = dict(type=kind, fill=fill or None, stroke=stroke or None, width=width,
                 radius=_opt(op, 'radius', path, 0, 0, 2000) if kind == 'rect' else 0)
    layer = dict(id=uid('l'), kind='shape', shape=shape, w=w, h=h, nw=_size(w), nh=_size(h),
                 **_common(op, path, {'rect': 'Rectangle', 'ellipse': 'Ellipse', 'line': 'Line'}[kind]))
    _add_layer(doc, layer, x, y, rotation)
    return doc


def describe():
    """Supported recipe operations, keys and ranges, for agents and schemas."""
    number, frame = {'type': 'number'}, 'pixels in the current frame (the crop), from its top left'
    place = {'x': dict(number, about=frame), 'y': dict(number, about=frame),
             'anchor': {'enum': list(ANCHORS), 'default': 'center', 'about': 'used when x/y are absent'},
             'margin': dict(number, minimum=0, default=0)}
    look = {'rotation': dict(number, minimum=-360, maximum=360, default=0, about='degrees clockwise on screen'),
            'opacity': dict(number, minimum=0, maximum=1, default=1),
            'blend': {'enum': list(BLENDS), 'default': 'source-over'},
            'name': {'type': 'string', 'about': 'layer name; adjust can refer to it'}}
    ratio = {'type': 'string', 'about': "'video', 'original', 'free' or W:H such as 16:9 or 2.39:1"}
    keys = {
        'crop': {'ratio': dict(ratio, default='original'), 'x': number, 'y': number,
                 'width': dict(number, exclusiveMinimum=0), 'height': dict(number, exclusiveMinimum=0),
                 'anchor': place['anchor'], 'zoom': dict(number, minimum=1, maximum=100, default=1)},
        'rotate': {'degrees': {'enum': [90, 180, 270, -90, -180, -270]}},
        'flip': {'axis': {'enum': ['horizontal', 'vertical'], 'default': 'horizontal'}},
        'pad': {'to': ratio, **{k: dict(number, minimum=0, maximum=20000) for k in ('left', 'top', 'right', 'bottom')},
                'color': {'type': 'string', 'about': 'CSS colour of the added area', 'default': '#000000'}},
        'fit': {'width': dict(number, exclusiveMinimum=0), 'height': dict(number, exclusiveMinimum=0),
                'mode': {'enum': ['cover', 'contain'], 'default': 'cover'}, 'color': {'type': 'string'}},
        'adjust': {'layer': {'type': 'string', 'default': 'base', 'about': "'base', 'last' or a layer name"},
                   **{k: dict(number, minimum=RANGES[k][0], maximum=RANGES[k][1], default=0) for k in ADJUSTMENTS}},
        'overlay': {'image': {'type': 'string', 'about': 'media file (ComfyUI input path)'},
                    'width': dict(number, exclusiveMinimum=0), 'height': dict(number, exclusiveMinimum=0),
                    'scale': dict(number, exclusiveMinimum=0, about="multiplies the image's own pixel size"),
                    **place, **look},
        'text': {'text': {'type': 'string', 'maxLength': 2000}, **place,
                 'size': dict(number, minimum=6, maximum=2000, about='pixels; default 1/12 of the shorter side'),
                 'color': {'type': 'string', 'default': '#ffffff'}, 'font': {'enum': list(FONTS), 'default': 'sans'},
                 'weight': {'enum': [400, 700], 'default': 700},
                 'align': {'enum': ['left', 'center', 'right'], 'default': 'left'},
                 'shadow': {'type': 'boolean', 'default': True},
                 'outline': {'about': 'false, true (black) or a CSS colour', 'default': False},
                 'line_height': dict(number, minimum=.5, maximum=4, default=1.25), **look},
        'shape': {'type': {'enum': ['rect', 'ellipse', 'line'], 'default': 'rect'},
                  'width': dict(number, exclusiveMinimum=0), 'height': dict(number, exclusiveMinimum=0),
                  'from': {'type': 'array', 'about': '[x, y] start of a line in the frame'},
                  'to': {'type': 'array', 'about': '[x, y] end of a line in the frame'},
                  'fill': {'type': ['string', 'null'], 'default': '#ffffff (none for lines)'},
                  'stroke': {'type': ['string', 'null'], 'default': 'none (#ffffff for lines)'},
                  'stroke_width': dict(number, minimum=1, maximum=400, default=6),
                  'radius': dict(number, minimum=0, maximum=2000, default=0), **place, **look},
    }
    keys['extend'] = keys['pad']
    keys['remove_text'] = {'x': dict(number, minimum=0, default=0, about=frame),
                           'y': dict(number, minimum=0, about=frame + '; default 60% of the frame height'),
                           'width': dict(number, exclusiveMinimum=0, about='default: to the right edge'),
                           'height': dict(number, exclusiveMinimum=0, about='default: to the bottom edge'),
                           'text_height': dict(number, minimum=4, maximum=2000,
                                               about='pixels; about the caption letters\' height, default 1/16 of '
                                                     'the shorter side')}
    return {
        'document_version': 2, 'max_operations': MAX_OPERATIONS, 'max_pixels': MAX_PIXELS,
        'coordinates': 'Operations apply in order. Positions and sizes are ' + frame + '; crop boxes are pixels '
                       'of the whole canvas. The saved picture is the crop at source resolution.',
        'operations': {name: {'about': SPEC[name]['about'], 'keys': keys[name]} for name in SPEC},
        'adjustments': {k: list(RANGES[k]) for k in ADJUSTMENTS},
        'blends': list(BLENDS), 'fonts': list(FONTS),
        'renders': ['image, paint, text and shape layers', 'opacity, blend modes, adjustments, layer masks',
                    'brush, eraser, clone, heal (whole stroke or captions only) and blur strokes',
                    'rectangle, ellipse, lasso and magic wand selections'],
        'differences': ['Text uses the editor\'s bundled fonts without their kerning, so a line can be a few '
                        'pixels wider; the editor fits the text box again when it opens the picture. A browser '
                        'picks the font per character; here the first font of the family list that has every '
                        'character draws the whole text.'],
    }
