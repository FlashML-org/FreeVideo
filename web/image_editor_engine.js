// Document model and rendering for the image editor. Nothing here touches the
// page: the editor UI owns events and drawing on screen.
//
// Spaces:
//   document (D): pixels of the original image, origin at its top left;
//   oriented (O): the document after the canvas rotation and flips;
//   canvas: O plus any extension on each side; the crop is a rectangle of it.
// Layers live in D (centre, size, rotation, flips); a layer's own edits live in
// its local pixels as fractions, so they survive moves and preview scales.
// Every edit is an entry in a list, so a saved document reopens from the
// original image with each step still in place.

import {fontCSS, fontLoaded, loadFonts} from './fonts.js';

export const BLENDS = ['source-over', 'multiply', 'screen', 'overlay', 'soft-light', 'darken', 'lighten',
    'color-dodge', 'color-burn', 'difference'];
export const ADJUSTMENTS = ['exposure', 'contrast', 'highlights', 'shadows', 'temperature', 'tint', 'hue', 'saturation',
    'vibrance', 'sharpen', 'blur', 'noise', 'vignette'];

let counter = 0;
export const uid = prefix => `${prefix}${Date.now().toString(36)}${(counter++).toString(36)}`;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

function canvas(width, height) {
    const c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(width)); c.height = Math.max(1, Math.round(height));
    return c;
}
// Pixel work stays in system memory: Chrome keeps willReadFrequently canvases off the GPU.
const context = c => c.getContext('2d', {willReadFrequently: true});

export function emptyAdjust() {
    return Object.fromEntries(ADJUSTMENTS.map(k => [k, 0]));
}
function baseLayer(file, image) {
    const w = image.naturalWidth, h = image.naturalHeight;
    return {id: 'base', kind: 'image', name: '', src: file, visible: true, locked: true, opacity: 1, blend: 'source-over',
        cx: w / 2, cy: h / 2, w, h, rotation: 0, flipX: false, flipY: false, nw: w, nh: h,
        ops: [], mask: null, adjust: emptyAdjust()};
}
export function newDocument(file, image) {
    const w = image.naturalWidth, h = image.naturalHeight;
    return {version: 2, width: w, height: h, rotate: 0, flipX: false, flipY: false,
        extend: {l: 0, t: 0, r: 0, b: 0, color: '#000000'}, crop: {x: 0, y: 0, w, h}, ratio: 'original',
        layers: [baseLayer(file, image)], active: 'base'};
}
// Edits saved before layers: crop and strokes in fractions of the oriented picture.
export function migrate(edit, file, image) {
    if (!edit) return newDocument(file, image);
    if (edit.version === 2) return edit;
    const doc = newDocument(file, image);
    doc.rotate = edit.rotate || 0; doc.flipX = !!edit.flipX; doc.flipY = !!edit.flipY; doc.ratio = edit.ratio || 'original';
    const [ow, oh] = orientedSize(doc);
    const c = edit.crop || {x: 0, y: 0, w: 1, h: 1};
    doc.crop = {x: c.x * ow, y: c.y * oh, w: c.w * ow, h: c.h * oh};
    const a = edit.adjust || {};
    doc.layers[0].adjust = {...emptyAdjust(), exposure: a.brightness || 0, contrast: a.contrast || 0, saturation: a.saturation || 0};
    if (edit.strokes?.length) {
        // Old strokes were drawn in oriented fractions; a paint layer covering O holds them.
        // Its pixels must land on O unrotated: the layer transform is the inverse orientation.
        const paint = paintLayer(doc, 'Drawing');
        Object.assign(paint, {cx: doc.width / 2, cy: doc.height / 2, w: ow, h: oh, nw: ow, nh: oh,
            rotation: -doc.rotate, flipX: doc.flipX, flipY: doc.flipY});
        paint.ops = edit.strokes.map(s => ({id: uid('o'), type: 'stroke', tool: s.erase ? 'erase' : 'paint', pts: s.points,
            size: s.size, hardness: 1, opacity: 1, color: s.color}));
        doc.layers.push(paint);
    }
    return doc;
}

export function orientedSize(doc) {
    return doc.rotate % 180 ? [doc.height, doc.width] : [doc.width, doc.height];
}
// D → O: rotate about the document, then mirror in O.
export function orientMatrix(doc) {
    const [ow, oh] = orientedSize(doc);
    const m = new DOMMatrix();
    m.translateSelf(ow / 2, oh / 2);
    m.scaleSelf(doc.flipX ? -1 : 1, doc.flipY ? -1 : 1);
    m.rotateSelf(doc.rotate);
    m.translateSelf(-doc.width / 2, -doc.height / 2);
    return m;
}
export function canvasRect(doc) {
    const [ow, oh] = orientedSize(doc), e = doc.extend;
    return {x: -e.l, y: -e.t, w: ow + e.l + e.r, h: oh + e.t + e.b};
}
export function layerMatrix(layer) {
    const m = new DOMMatrix();
    m.translateSelf(layer.cx, layer.cy);
    m.rotateSelf(layer.rotation);
    m.scaleSelf(layer.flipX ? -1 : 1, layer.flipY ? -1 : 1);
    m.translateSelf(-layer.w / 2, -layer.h / 2);
    m.scaleSelf(layer.w / layer.nw, layer.h / layer.nh);
    return m;   // layer native pixels → D
}
// The document area the canvas covers, for sizing new paint layers.
export function documentBounds(doc) {
    const c = canvasRect(doc), inv = orientMatrix(doc).inverse();
    const pts = [[c.x, c.y], [c.x + c.w, c.y], [c.x, c.y + c.h], [c.x + c.w, c.y + c.h]].map(([x, y]) => inv.transformPoint(new DOMPoint(x, y)));
    const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
    return {x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys)};
}
export function paintLayer(doc, name) {
    const b = documentBounds(doc);
    return {id: uid('l'), kind: 'paint', name, visible: true, locked: false, opacity: 1, blend: 'source-over',
        cx: b.x + b.w / 2, cy: b.y + b.h / 2, w: b.w, h: b.h, rotation: 0, flipX: false, flipY: false,
        nw: Math.round(b.w), nh: Math.round(b.h), ops: [], mask: null, adjust: emptyAdjust()};
}
export const rasterKinds = new Set(['image', 'paint']);
// The bundled fonts a document's text layers draw with, as [family, weight] pairs.
export const textFonts = doc => doc.layers.filter(l => l.kind === 'text').map(l => [l.text.font, l.text.weight]);
export const textFontsLoaded = doc => textFonts(doc).every(([name, weight]) => fontLoaded(name, weight));

// Native size of a text block, from the same font settings it is drawn with. The font must
// be loaded (textFonts): measuring before that would use another font's metrics.
export function measureText(text) {
    const ctx = document.createElement('canvas').getContext('2d');
    ctx.font = fontCSS(text.font, text.weight, text.size);
    const lines = String(text.content || ' ').split('\n');
    const width = Math.max(...lines.map(line => ctx.measureText(line || ' ').width));
    return {nw: Math.ceil(width + text.size * .4), nh: Math.ceil(text.size * (.4 + (lines.length - 1) * (text.lineHeight || 1.25) + 1.1))};
}

// ---------------------------------------------------------------------------
// Image size

// A selection's shapes are in document pixels, so they scale with it (sx, sy along D's axes).
export function scaleSelection(selection, sx, sy) {
    if (!selection) return selection;
    const k = Math.sqrt(sx * sy);
    const part = p => {
        const q = {...p};
        if (p.feather) q.feather = p.feather * k;
        if (p.shape === 'rect' || p.shape === 'ellipse') Object.assign(q, {x: p.x * sx, y: p.y * sy, w: p.w * sx, h: p.h * sy});
        if (p.pts) q.pts = p.pts.map(([x, y]) => [x * sx, y * sy]);
        return q;
    };
    return {...selection, feather: (selection.feather || 0) * k, parts: selection.parts.map(part)};
}

// The document at another size: its picture (the crop) becomes width × height pixels. Every
// place and length in document pixels scales with it: the canvas, the crop, each layer's place
// and size, the selections kept with edits. Image layers keep their own pixels and are drawn at
// the new size; paint layers and shapes are remade at it and text at its new font size, so they
// stay sharp and editable. A layer's own edits are fractions of it and follow by themselves.
// keep (proportions locked): the same scale both ways, just enough to fill width × height, and
// the fraction of a pixel left over is cut from the right and bottom. Otherwise each way scales
// on its own: pictures and shapes stretch, text keeps its shape. The text fonts must be loaded.
export function resizeDocument(doc, width, height, keep = true) {
    const c = doc.crop;
    let kx = width / c.w, ky = height / c.h;
    if (keep) kx = ky = Math.max(kx, ky);
    const [sx, sy] = doc.rotate % 180 ? [ky, kx] : [kx, ky];
    const k = Math.sqrt(sx * sy);
    // How much a layer turned by rotation grows along its own width and height.
    const stretch = rotation => {
        const a = rotation * Math.PI / 180, cos = Math.cos(a), sin = Math.sin(a);
        return [Math.hypot(sx * cos, sy * sin), Math.hypot(sx * sin, sy * cos)];
    };
    const scaleOps = ops => ops.map(op => op.selection ? {...op, selection: scaleSelection(op.selection, sx, sy)} : op);
    // Floating-point dust (1344.0000000000002) is rounded away; real fractions stay.
    const tidy = v => Math.abs(v - Math.round(v)) < 1e-6 ? Math.round(v) : v;
    const layers = doc.layers.map(l => {
        const [fx, fy] = stretch(l.rotation || 0);
        const next = {...l, cx: tidy(l.cx * sx), cy: tidy(l.cy * sy), w: tidy(l.w * fx), h: tidy(l.h * fy)};
        if (l.ops) next.ops = scaleOps(l.ops);
        if (l.mask) next.mask = {...l.mask, ops: scaleOps(l.mask.ops)};
        if (l.copyOf?.selection) next.copyOf = {...l.copyOf, selection: scaleSelection(l.copyOf.selection, sx, sy)};
        if (l.kind === 'text') {
            const text = {...l.text, size: Math.max(1, Math.round(l.text.size * k * 10) / 10)}, size = measureText(text);
            return {...next, text, ...size, w: size.nw * l.w / l.nw, h: size.nh * l.h / l.nh};
        }
        if (l.kind === 'shape') {
            const shape = {...l.shape, width: (l.shape.width || 0) * k};
            if (l.shape.radius) shape.radius = l.shape.radius * k;
            return {...next, shape, nw: tidy(l.nw * fx), nh: tidy(l.nh * fy)};
        }
        if (l.kind === 'paint' && !l.copyOf) return {...next, nw: tidy(l.nw * fx), nh: tidy(l.nh * fy)};
        return next;
    });
    const e = doc.extend;
    return {...doc, width: tidy(doc.width * sx), height: tidy(doc.height * sy),
        extend: {...e, l: tidy(e.l * kx), r: tidy(e.r * kx), t: tidy(e.t * ky), b: tidy(e.b * ky)},
        crop: {x: tidy(c.x * kx), y: tidy(c.y * ky), w: width, h: height}, layers};
}

// ---------------------------------------------------------------------------
// Pixels

function hexRGB(hex) {
    const v = parseInt(String(hex || '#000').slice(1).padEnd(6, '0'), 16);
    return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}
function mulberry(seed) {
    return () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
        t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
}
function hashString(s) { let h = 2166136261; for (const ch of String(s)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619); return h >>> 0; }

let blurFilter;
function canvasBlur() {
    if (blurFilter === undefined) {
        const ctx = document.createElement('canvas').getContext('2d');
        blurFilter = 'filter' in ctx && (ctx.filter = 'blur(2px)', ctx.filter === 'blur(2px)');
    }
    return blurFilter;
}
// Gaussian-like blur; ctx.filter where it works, three box passes elsewhere.
export function blurred(source, radius) {
    const out = canvas(source.width, source.height), ctx = context(out);
    if (radius <= 0) { ctx.drawImage(source, 0, 0); return out; }
    if (canvasBlur()) {
        // Pad by mirroring the edge so the border does not fade to transparent.
        const pad = Math.ceil(radius * 3);
        const big = canvas(source.width + pad * 2, source.height + pad * 2), b = context(big);
        b.drawImage(source, pad, pad);
        b.drawImage(source, 0, 0, 1, source.height, 0, pad, pad, source.height);
        b.drawImage(source, source.width - 1, 0, 1, source.height, pad + source.width, pad, pad, source.height);
        b.drawImage(big, 0, pad, big.width, 1, 0, 0, big.width, pad);
        b.drawImage(big, 0, pad + source.height - 1, big.width, 1, 0, pad + source.height, big.width, pad);
        ctx.filter = `blur(${radius}px)`;
        ctx.drawImage(big, -pad, -pad);
        ctx.filter = 'none';
        return out;
    }
    ctx.drawImage(source, 0, 0);
    const image = ctx.getImageData(0, 0, out.width, out.height);
    boxBlur(image.data, out.width, out.height, Math.max(1, Math.round(radius * .6)));
    ctx.putImageData(image, 0, 0);
    return out;
}
function boxBlur(d, w, h, r) {
    const tmp = new Float32Array(d.length);
    for (let pass = 0; pass < 3; pass++) {
        for (let y = 0; y < h; y++) for (let c = 0; c < 4; c++) {
            let acc = 0;
            for (let x = -r; x <= r; x++) acc += d[(y * w + clamp(x, 0, w - 1)) * 4 + c];
            for (let x = 0; x < w; x++) {
                tmp[(y * w + x) * 4 + c] = acc / (2 * r + 1);
                acc += d[(y * w + clamp(x + r + 1, 0, w - 1)) * 4 + c] - d[(y * w + clamp(x - r, 0, w - 1)) * 4 + c];
            }
        }
        for (let x = 0; x < w; x++) for (let c = 0; c < 4; c++) {
            let acc = 0;
            for (let y = -r; y <= r; y++) acc += tmp[(clamp(y, 0, h - 1) * w + x) * 4 + c];
            for (let y = 0; y < h; y++) {
                d[(y * w + x) * 4 + c] = acc / (2 * r + 1);
                acc += tmp[(clamp(y + r + 1, 0, h - 1) * w + x) * 4 + c] - tmp[(clamp(y - r, 0, h - 1) * w + x) * 4 + c];
            }
        }
    }
}

// Light and colour first, per pixel; then sharpening, blur, noise and vignette.
export function adjusted(source, adjust, seed, scale) {
    const a = adjust || {};
    const active = ADJUSTMENTS.some(k => a[k]);
    if (!active) return source;
    let work = canvas(source.width, source.height);
    let ctx = context(work);
    ctx.drawImage(source, 0, 0);
    const tonal = ['exposure', 'contrast', 'highlights', 'shadows', 'temperature', 'tint', 'hue', 'saturation', 'vibrance'].some(k => a[k]);
    if (tonal) {
        const image = ctx.getImageData(0, 0, work.width, work.height), d = image.data;
        const gain = Math.pow(2, (a.exposure || 0) / 50), contrast = 1 + (a.contrast || 0) / 100;
        const temp = (a.temperature || 0) / 100, tint = (a.tint || 0) / 100;
        const hi = (a.highlights || 0) / 100, sh = (a.shadows || 0) / 100;
        const sat = 1 + (a.saturation || 0) / 100, vib = (a.vibrance || 0) / 100;
        const angle = (a.hue || 0) * Math.PI / 180, cos = Math.cos(angle), sin = Math.sin(angle);
        // CSS hue-rotate matrix.
        const hr = [.213 + cos * .787 - sin * .213, .715 - cos * .715 - sin * .715, .072 - cos * .072 + sin * .928,
            .213 - cos * .213 + sin * .143, .715 + cos * .285 + sin * .140, .072 - cos * .072 - sin * .283,
            .213 - cos * .213 - sin * .787, .715 - cos * .715 + sin * .715, .072 + cos * .928 + sin * .072];
        for (let i = 0; i < d.length; i += 4) {
            let r = d[i] / 255 * gain, g = d[i + 1] / 255 * gain, b = d[i + 2] / 255 * gain;
            if (temp) { r += temp * .12; b -= temp * .12; }
            if (tint) { g -= tint * .1; r += tint * .04; b += tint * .04; }
            if (hi || sh) {
                const lum = .2126 * r + .7152 * g + .0722 * b;
                const lift = hi * Math.max(0, lum - .5) * .8 + sh * Math.max(0, .5 - lum) * .8;
                r += lift; g += lift; b += lift;
            }
            if (contrast !== 1) { r = (r - .5) * contrast + .5; g = (g - .5) * contrast + .5; b = (b - .5) * contrast + .5; }
            if (angle) { const nr = hr[0] * r + hr[1] * g + hr[2] * b, ng = hr[3] * r + hr[4] * g + hr[5] * b; b = hr[6] * r + hr[7] * g + hr[8] * b; r = nr; g = ng; }
            let s = sat;
            if (vib) { const mx = Math.max(r, g, b), mn = Math.min(r, g, b); s *= 1 + vib * (1 - clamp(mx - mn, 0, 1)); }
            if (s !== 1) {
                const nr = (.213 + .787 * s) * r + (.715 - .715 * s) * g + (.072 - .072 * s) * b;
                const ng = (.213 - .213 * s) * r + (.715 + .285 * s) * g + (.072 - .072 * s) * b;
                b = (.213 - .213 * s) * r + (.715 - .715 * s) * g + (.072 + .928 * s) * b; r = nr; g = ng;
            }
            d[i] = clamp(r, 0, 1) * 255; d[i + 1] = clamp(g, 0, 1) * 255; d[i + 2] = clamp(b, 0, 1) * 255;
        }
        ctx.putImageData(image, 0, 0);
    }
    // Radii follow the raster's size, so a saved image matches what was shown.
    const unit = Math.min(work.width, work.height) / 100;
    if (a.blur > 0) work = blurred(work, a.blur / 100 * unit * 4);
    if (a.sharpen > 0) {
        const soft = blurred(work, Math.max(.6, unit * .35));
        const sd = context(soft).getImageData(0, 0, soft.width, soft.height).data;
        ctx = context(work);
        const image = ctx.getImageData(0, 0, work.width, work.height), d = image.data, amount = a.sharpen / 100 * 1.6;
        for (let i = 0; i < d.length; i += 4) for (let c = 0; c < 3; c++) d[i + c] = clamp(d[i + c] + (d[i + c] - sd[i + c]) * amount, 0, 255);
        ctx.putImageData(image, 0, 0);
    }
    if (a.noise > 0 || a.vignette) {
        ctx = context(work);
        const image = ctx.getImageData(0, 0, work.width, work.height), d = image.data, w = work.width, h = work.height;
        const random = mulberry(seed), amount = (a.noise || 0) / 100 * 60, v = (a.vignette || 0) / 100;
        for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
            const i = (y * w + x) * 4;
            let f = 1;
            if (v) { const dx = (x / w - .5) * 2, dy = (y / h - .5) * 2, r = Math.min(1, Math.sqrt(dx * dx + dy * dy) / Math.SQRT2);
                f = v > 0 ? 1 - v * r * r * 1.1 : 1 - v * r * r * .8; }
            const n = amount ? (random() + random() - 1) * amount : 0;
            d[i] = clamp(d[i] * f + n, 0, 255); d[i + 1] = clamp(d[i + 1] * f + n, 0, 255); d[i + 2] = clamp(d[i + 2] * f + n, 0, 255);
        }
        ctx.putImageData(image, 0, 0);
    }
    return work;
}

// Soft round brush mask for a stroke, in the raster's pixels.
function strokeMask(width, height, op) {
    const m = canvas(width, height), ctx = context(m);
    const size = Math.max(1, op.size * Math.min(width, height));
    const hard = clamp(op.hardness ?? 1, 0, 1);
    const core = size * (.4 + .6 * hard), soft = size * (1 - hard) * .55;
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.strokeStyle = ctx.fillStyle = '#fff';
    // A shadow-only stroke gives a soft edge without overlapping dabs stacking up.
    const shift = soft ? width + size * 4 : 0;
    if (soft) { ctx.shadowColor = '#fff'; ctx.shadowBlur = soft; ctx.shadowOffsetX = shift; }
    ctx.lineWidth = core;
    const pts = op.pts.map(([u, v]) => [u * width - shift, v * height]);
    ctx.beginPath();
    if (pts.length === 1) { ctx.arc(pts[0][0], pts[0][1], core / 2, 0, Math.PI * 2); ctx.fill(); }
    else { ctx.moveTo(...pts[0]); for (const p of pts.slice(1)) ctx.lineTo(...p); ctx.stroke(); }
    return m;
}
function maskWith(target, mask) {
    const ctx = target.getContext('2d');
    ctx.globalCompositeOperation = 'destination-in'; ctx.drawImage(mask, 0, 0); ctx.globalCompositeOperation = 'source-over';
    return target;
}

// Area average: each new pixel is the mean of the source area it covers, edge pixels counted
// by the part inside. Colours are averaged weighted by alpha, so clear pixels add no colour.
function spans(n, m) {
    const k = n / m, start = new Int32Array(m), count = new Int32Array(m), weights = [];
    for (let o = 0; o < m; o++) {
        const a = o * k, b = a + k, i0 = Math.floor(a), i1 = Math.min(n, Math.ceil(b - 1e-9));
        start[o] = i0; count[o] = i1 - i0;
        for (let i = i0; i < i1; i++) weights.push((Math.min(b, i + 1) - Math.max(a, i)) / k);
    }
    return {start, count, weight: Float32Array.from(weights)};
}
export function areaResize(src, sw, sh, w, h) {
    let opaque = true;
    for (let i = 3; i < src.length; i += 4) if (src[i] !== 255) { opaque = false; break; }
    const X = spans(sw, w), Y = spans(sh, h), n = w * 4, xs = X.start, xc = X.count, xw = X.weight;
    // One source row at a time, averaged across into line; a row on the border of two new
    // rows is used by both, so the last one is kept.
    const line = new Float32Array(n), acc = new Float32Array(n), out = new Uint8ClampedArray(w * h * 4);
    let last = -1;
    const across = y => {
        const row = y * sw * 4;
        for (let o = 0, p = 0, t = 0; o < w; o++, t += 4) {
            let r = 0, g = 0, b = 0, a = 0, i = row + xs[o] * 4;
            if (opaque) for (let j = xc[o]; j > 0; j--, p++, i += 4) { const q = xw[p]; r += src[i] * q; g += src[i + 1] * q; b += src[i + 2] * q; }
            else for (let j = xc[o]; j > 0; j--, p++, i += 4) { const q = xw[p] * src[i + 3]; r += src[i] * q; g += src[i + 1] * q; b += src[i + 2] * q; a += q; }
            line[t] = r; line[t + 1] = g; line[t + 2] = b; line[t + 3] = a;
        }
    };
    for (let o = 0, p = 0; o < h; o++) {
        acc.fill(0);
        for (let j = Y.count[o], y = Y.start[o]; j > 0; j--, p++, y++) {
            if (y !== last) { across(y); last = y; }
            const q = Y.weight[p];
            for (let i = 0; i < n; i++) acc[i] += line[i] * q;
        }
        const base = o * n;
        for (let i = 0; i < n; i += 4) {
            const a = opaque ? 1 : acc[i + 3];
            if (a > 0) { out[base + i] = acc[i] / a + .5; out[base + i + 1] = acc[i + 1] / a + .5; out[base + i + 2] = acc[i + 2] / a + .5; }
            out[base + i + 3] = opaque ? 255 : a + .5;
        }
    }
    return out;
}
// A picture drawn into w × h pixels. On screen the browser's own scaling is enough; for a saved
// picture made smaller the pixels are averaged by area, so fine detail does not shimmer or alias.
function drawSource(ctx, image, w, h, exact) {
    const sw = image.naturalWidth || image.width, sh = image.naturalHeight || image.height;
    if (!exact || w >= sw || h >= sh) { ctx.drawImage(image, 0, 0, w, h); return; }
    const full = canvas(sw, sh), fctx = context(full);
    fctx.drawImage(image, 0, 0);
    const pixels = fctx.getImageData(0, 0, sw, sh).data;
    full.width = full.height = 0;
    ctx.putImageData(new ImageData(areaResize(pixels, sw, sh, w, h), w, h), 0, 0);
}

// Pull-push interpolation: known values (col, already multiplied by their
// weight wt) are averaged down a pyramid and pushed back up into the gaps.
// It works at the precision of the arrays it is given.
function pullPush(col, wt, bw, bh) {
    const levels = [{w: bw, h: bh, col, wt}], Values = col.constructor, Weights = wt.constructor;
    let cw = bw, ch = bh;
    while (cw > 1 || ch > 1) {
        const nw = Math.max(1, cw >> 1), nh = Math.max(1, ch >> 1), ncol = new Values(nw * nh * 4), nwt = new Weights(nw * nh);
        for (let y = 0; y < nh; y++) for (let x = 0; x < nw; x++) {
            let sw = 0; const acc = [0, 0, 0, 0];
            for (let dy = 0; dy < 2; dy++) for (let dx = 0; dx < 2; dx++) {
                const sx = Math.min(cw - 1, x * 2 + dx), sy = Math.min(ch - 1, y * 2 + dy), j = sy * cw + sx;
                sw += wt[j]; for (let c = 0; c < 4; c++) acc[c] += col[j * 4 + c];
            }
            const i = y * nw + x; nwt[i] = Math.min(1, sw);
            for (let c = 0; c < 4; c++) ncol[i * 4 + c] = sw > 0 ? acc[c] / sw * Math.min(1, sw) : 0;
        }
        cw = nw; ch = nh; col = ncol; wt = nwt; levels.push({w: cw, h: ch, col, wt});
    }
    for (let l = levels.length - 2; l >= 0; l--) {
        const cur = levels[l], up = levels[l + 1];
        for (let y = 0; y < cur.h; y++) for (let x = 0; x < cur.w; x++) {
            const i = y * cur.w + x, k = cur.wt[i];
            if (k >= 1) continue;
            // Bilinear sample of the coarser level.
            const fx = clamp((x + .5) / 2 - .5, 0, up.w - 1), fy = clamp((y + .5) / 2 - .5, 0, up.h - 1);
            const ix = Math.floor(fx), iy = Math.floor(fy), tx = fx - ix, ty = fy - iy;
            const jx = Math.min(up.w - 1, ix + 1), jy = Math.min(up.h - 1, iy + 1);
            for (let c = 0; c < 4; c++) {
                const s = (up.col[(iy * up.w + ix) * 4 + c] * (1 - tx) + up.col[(iy * up.w + jx) * 4 + c] * tx) * (1 - ty)
                    + (up.col[(jy * up.w + ix) * 4 + c] * (1 - tx) + up.col[(jy * up.w + jx) * 4 + c] * tx) * ty;
                const sw = (up.wt[iy * up.w + ix] * (1 - tx) + up.wt[iy * up.w + jx] * tx) * (1 - ty) + (up.wt[jy * up.w + ix] * (1 - tx) + up.wt[jy * up.w + jx] * tx) * ty;
                const norm = sw > 0 ? s / sw : 0;
                cur.col[i * 4 + c] = cur.col[i * 4 + c] + norm * (1 - k);
            }
            cur.wt[i] = 1;
        }
    }
    return levels[0].col;
}

// Spot healing is a patch-based fill: coarse to fine, every 7×7 patch that
// touches the stroke takes the most similar patch outside it, and the stroke's
// pixels become the weighted vote of those patches, so the fill takes real
// texture from around the mark. Each level searches once and votes once:
// - the coarsest level starts by peeling the hole from its edge inwards, then
//   compares every patch with every source patch;
// - each finer level starts from the coarser fill, upsampled, and compares the
//   patches in a fixed window around twice the offset found one level coarser,
//   and nothing else: 3 pixels each way, 1 on a level with very many patches;
// - at full size, the difference between the fill and the picture just around
//   the hole is spread smoothly across it, so the fill meets it without a seam.
// Distances count estimated pixels less than real ones and prefer nearby
// sources. There is no randomness: the same stroke always heals the same way.
const HEAL_PATCH = 3, HEAL_COARSE = 24, HEAL_WINDOW = 3, HEAL_MARGIN = 384, HEAL_PEEL = 2048;
// Above this many patches to place, a level searches the window of radius HEAL_WINDOW_FINE.
const HEAL_SEARCH = 60000, HEAL_WINDOW_FINE = 1;
// A whole stroke leans on nearby patches; a caption's thin strokes have picture all around them.
const HEAL_NEAR = 4, HEAL_NEAR_TEXT = .25, HEAL_GUESS = .1, HEAL_TEXT_OUTLINE = .15;
// A caption's letters share one stroke width: parts whose half-widths are within this ratio (and a
// pixel) agree, and a joint or a curve may be this many times as thick as the stroke.
const HEAL_TEXT_AGREE = 1.35, HEAL_TEXT_JOINT = 1.5;
// Pixels within r of a set pixel (a square neighbourhood).
function dilate(map, w, h, r) {
    const rows = new Uint8Array(w * h), out = new Uint8Array(w * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        let v = 0; for (let k = Math.max(0, x - r); k <= Math.min(w - 1, x + r) && !v; k++) v = map[y * w + k];
        rows[y * w + x] = v;
    }
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        let v = 0; for (let k = Math.max(0, y - r); k <= Math.min(h - 1, y + r) && !v; k++) v = rows[k * w + x];
        out[y * w + x] = v;
    }
    return out;
}
// Chessboard distance of each set pixel to the nearest unset one in the picture (0 when unset).
// When some pixel is unset, a pixel survives thinning by r (no unset pixel within r) exactly when
// its distance is above r.
function distances(map, w, h) {
    const far = w + h, out = new Int32Array(w * h);
    for (let i = 0; i < w * h; i++) out[i] = map[i] ? far : 0;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        const i = y * w + x;
        if (!out[i]) continue;
        let v = out[i];
        if (x > 0) v = Math.min(v, out[i - 1] + 1);
        if (y > 0) {
            v = Math.min(v, out[i - w] + 1);
            if (x > 0) v = Math.min(v, out[i - w - 1] + 1);
            if (x < w - 1) v = Math.min(v, out[i - w + 1] + 1);
        }
        out[i] = v;
    }
    for (let y = h - 1; y >= 0; y--) for (let x = w - 1; x >= 0; x--) {
        const i = y * w + x;
        if (!out[i]) continue;
        let v = out[i];
        if (x < w - 1) v = Math.min(v, out[i + 1] + 1);
        if (y < h - 1) {
            v = Math.min(v, out[i + w] + 1);
            if (x > 0) v = Math.min(v, out[i + w - 1] + 1);
            if (x < w - 1) v = Math.min(v, out[i + w + 1] + 1);
        }
        out[i] = v;
    }
    return out;
}
// The set pixels of a distance map farther than r from an unset one.
function beyond(dist, r) {
    const out = new Uint8Array(dist.length);
    for (let i = 0; i < dist.length; i++) out[i] = dist[i] > r ? 1 : 0;
    return out;
}
// Each connected part (8 neighbours) of a map, handed to each as a list of its pixels.
function parts(map, w, h, each) {
    const seen = new Uint8Array(w * h);
    for (let start = 0; start < w * h; start++) {
        if (!map[start] || seen[start]) continue;
        seen[start] = 1;
        const part = [], stack = [start];
        while (stack.length) {
            const i = stack.pop(), x = i % w, y = (i - x) / w;
            part.push(i);
            for (let ny = y - 1; ny <= y + 1; ny++) for (let nx = x - 1; nx <= x + 1; nx++) {
                if (ny < 0 || nx < 0 || ny >= h || nx >= w) continue;
                const j = ny * w + nx;
                if (map[j] && !seen[j]) { seen[j] = 1; stack.push(j); }
            }
        }
        each(part);
    }
}
// The coarsest level's start: ring by ring from the edge inwards, each hole
// pixel takes the centre of the source patch that best matches its filled neighbours
// (not the avoided ones, unless nothing else is near).
function peel(E, hole, sources, pr, lw, lh, avoid = null) {
    const filled = new Uint8Array(lw * lh);
    for (let i = 0; i < lw * lh; i++) filled[i] = hole[i] ? 0 : 1;
    const stride = Math.max(1, Math.ceil(sources.length / HEAL_PEEL)), S = sources.filter((_, k) => k % stride === 0);
    const qs = new Int32Array((2 * pr + 1) ** 2), ks = new Uint8Array(qs.length);
    for (;;) {
        const grown = dilate(filled, lw, lh, 1), front = [];
        for (let i = 0; i < lw * lh; i++) if (hole[i] && !filled[i] && grown[i]) front.push(i);
        if (!front.length) break;
        const chosen = front.map(f => {
            const fx = f % lw, fy = (f - fx) / lw;
            let n = 0, count = 0;
            for (let dy = -pr; dy <= pr; dy++) for (let dx = -pr; dx <= pr; dx++, n++) {
                qs[n] = Math.min(lh - 1, Math.max(0, fy + dy)) * lw + Math.min(lw - 1, Math.max(0, fx + dx));
                ks[n] = filled[qs[n]] && !(avoid && avoid[qs[n]] && !hole[qs[n]]) ? 1 : 0; count += ks[n];
            }
            if (!count) for (n = 0; n < qs.length; n++) { ks[n] = filled[qs[n]]; count += ks[n]; }
            let best = S[0], bestV = Infinity;
            for (const s of S) {
                const sx = s % lw, sy = (s - sx) / lw;
                let num = 0, k = 0;
                for (let dy = -pr; dy <= pr && num / count < bestV; dy++) for (let dx = -pr; dx <= pr; dx++, k++) {
                    if (!ks[k]) continue;
                    const a = qs[k] * 4, b = ((sy + dy) * lw + sx + dx) * 4;
                    const e0 = E[a] - E[b], e1 = E[a + 1] - E[b + 1], e2 = E[a + 2] - E[b + 2], e3 = E[a + 3] - E[b + 3];
                    num += e0 * e0 + e1 * e1 + e2 * e2 + e3 * e3;
                }
                if (num / count < bestV) { bestV = num / count; best = s; }
            }
            return best;
        });
        front.forEach((f, k) => { for (let c = 0; c < 4; c++) E[f * 4 + c] = E[chosen[k] * 4 + c]; filled[f] = 1; });
    }
}
// The fill of the hole pixels (Float32Array RGBA of the region), or null when no patch fits.
// avoid marks pixels that are neither a source nor a known neighbour (the rest of a caption).
function patchFill(region, hole, w, h, holeSize, near, avoid = null) {
    const pr = HEAL_PATCH, area = (2 * pr + 1) * (2 * pr + 1);
    let count = 1 + Math.max(0, Math.ceil(Math.log2(holeSize / HEAL_COARSE)));
    while (count > 1 && Math.min(w >> (count - 1), h >> (count - 1)) < 4 * (2 * pr + 1)) count--;
    const levels = [{w, h, img: region, hole, avoid}];
    for (let l = 1; l < count; l++) {
        const p = levels[l - 1], nw = Math.max(1, p.w >> 1), nh = Math.max(1, p.h >> 1);
        const img = new Float32Array(nw * nh * 4), nhole = new Uint8Array(nw * nh), navoid = p.avoid && new Uint8Array(nw * nh);
        for (let y = 0; y < nh; y++) for (let x = 0; x < nw; x++) {
            const i = y * nw + x;
            for (let dy = 0; dy < 2; dy++) for (let dx = 0; dx < 2; dx++) {
                const j = Math.min(p.h - 1, y * 2 + dy) * p.w + Math.min(p.w - 1, x * 2 + dx);
                if (p.hole[j]) nhole[i] = 1;
                if (navoid && p.avoid[j]) navoid[i] = 1;
                for (let c = 0; c < 4; c++) img[i * 4 + c] += p.img[j * 4 + c] / 4;
            }
        }
        levels.push({w: nw, h: nh, img, hole: nhole, avoid: navoid});
    }
    let nnf = null, E = null;
    for (let l = count - 1; l >= 0; l--) {
        const {w: lw, h: lh, img, hole: lhole, avoid: lavoid} = levels[l];
        const nearHole = dilate(lhole, lw, lh, pr), nearAvoid = lavoid && dilate(lavoid, lw, lh, pr);
        const targets = [], valid = new Uint8Array(lw * lh), sources = [];
        for (let y = 0; y < lh; y++) for (let x = 0; x < lw; x++) {
            const i = y * lw + x;
            if (nearHole[i]) targets.push(i);
            else if (x >= pr && y >= pr && x < lw - pr && y < lh - pr && !(nearAvoid && nearAvoid[i])) { valid[i] = 1; sources.push(i); }
        }
        if (!sources.length) return null;
        E = new Float32Array(img);
        const coarse = l === count - 1 ? null : levels[l + 1];
        if (!coarse) peel(E, lhole, sources, pr, lw, lh, lavoid);
        else for (let i = 0; i < lw * lh; i++) {
            // This level starts from the coarser fill, upsampled bilinearly.
            if (!lhole[i]) continue;
            const x = i % lw, y = (i - x) / lw, uw = coarse.w, uh = coarse.h, ce = nnf.E;
            const fx = clamp((x + .5) / 2 - .5, 0, uw - 1), fy = clamp((y + .5) / 2 - .5, 0, uh - 1);
            const ix = Math.floor(fx), iy = Math.floor(fy), ax = fx - ix, ay = fy - iy;
            const jx = Math.min(uw - 1, ix + 1), jy = Math.min(uh - 1, iy + 1);
            if (coarse.avoid) {
                // An avoided coarse pixel outside the coarser hole is the caption itself: the others share its weight.
                const at = [iy * uw + ix, iy * uw + jx, jy * uw + ix, jy * uw + jx];
                const f = [(1 - ax) * (1 - ay), ax * (1 - ay), (1 - ax) * ay, ax * ay];
                let barred = 0, sum = 0;
                for (let n = 0; n < 4; n++) {
                    if (coarse.avoid[at[n]] && !coarse.hole[at[n]]) { f[n] = 0; barred |= 1 << n; } else sum += f[n];
                }
                if (barred && barred !== 15) {
                    // The open corners carry no weight of their own here: they share it evenly.
                    if (!sum) for (let n = 0; n < 4; n++) if (!(barred >> n & 1)) { f[n] = 1; sum++; }
                    for (let k = 0; k < 4; k++) {
                        let v = 0;
                        for (let n = 0; n < 4; n++) v += ce[at[n] * 4 + k] * f[n];
                        E[i * 4 + k] = v / sum;
                    }
                    continue;
                }
            }
            for (let k = 0; k < 4; k++) {
                E[i * 4 + k] = (ce[(iy * uw + ix) * 4 + k] * (1 - ax) + ce[(iy * uw + jx) * 4 + k] * ax) * (1 - ay)
                    + (ce[(jy * uw + ix) * 4 + k] * (1 - ax) + ce[(jy * uw + jx) * 4 + k] * ax) * ay;
            }
        }
        const span2 = Math.max(1, holeSize / (1 << l)) ** 2;
        // How much each pixel's difference counts: estimated (hole) pixels less than real ones.
        const weight = new Float64Array(lw * lh);
        for (let i = 0; i < lw * lh; i++) weight[i] = lhole[i] ? HEAL_GUESS : lavoid && lavoid[i] ? 0 : 1;
        // Patch distance times a preference for nearby sources. limit: stop early once the
        // distance is clearly above it (the margin keeps the choice exact).
        const cost = (t, s, limit) => {
            const tx = t % lw, ty = (t - tx) / lw, sx = s % lw, sy = (s - sx) / lw;
            const ddx = sx - tx, ddy = sy - ty, factor = 1 + near * (ddx * ddx + ddy * ddy) / span2, stop = limit * 1.000001;
            let d = 0;
            if (tx >= pr && ty >= pr && tx < lw - pr && ty < lh - pr) {
                // Inside the region: no edge to clamp at.
                for (let dy = -pr; dy <= pr; dy++) {
                    let q = t + dy * lw - pr, a = q * 4, b = (s + dy * lw - pr) * 4;
                    for (let k = -pr; k <= pr; k++, q++, a += 4, b += 4) {
                        const e0 = E[a] - E[b], e1 = E[a + 1] - E[b + 1], e2 = E[a + 2] - E[b + 2], e3 = E[a + 3] - E[b + 3];
                        d += weight[q] * (e0 * e0 + e1 * e1 + e2 * e2 + e3 * e3);
                    }
                    if (d * factor > stop) return Infinity;
                }
                return d * factor;
            }
            for (let dy = -pr; dy <= pr; dy++) {
                const row = Math.min(lh - 1, Math.max(0, ty + dy)) * lw, srow = (sy + dy) * lw;
                for (let dx = -pr; dx <= pr; dx++) {
                    const q = row + Math.min(lw - 1, Math.max(0, tx + dx)), a = q * 4, b = (srow + sx + dx) * 4;
                    const e0 = E[a] - E[b], e1 = E[a + 1] - E[b + 1], e2 = E[a + 2] - E[b + 2], e3 = E[a + 3] - E[b + 3];
                    d += weight[q] * (e0 * e0 + e1 * e1 + e2 * e2 + e3 * e3);
                }
                if (d * factor > stop) return Infinity;
            }
            return d * factor;
        };
        // Every source, in order; the first of equal ones wins.
        const everywhere = t => {
            let best = -1, bestD = Infinity;
            for (const s of sources) { const d = cost(t, s, bestD); if (d < bestD) { best = s; bestD = d; } }
            return [best, bestD];
        };
        // A level with many patches to place (a large stroke on a large picture, when it is saved)
        // looks only next to twice the coarser offset: the coarser levels have already searched
        // wider, and the full window there costs seconds for no visible gain.
        const win = targets.length > HEAL_SEARCH ? HEAL_WINDOW_FINE : HEAL_WINDOW, side = 2 * win + 1;
        const next = new Int32Array(lw * lh).fill(-1), D = new Float64Array(lw * lh);
        for (const t of targets) {
            let best = -1, bestD = Infinity, bestK = Infinity;
            if (coarse) {
                // The fixed window around twice the coarser offset. Its centre is compared first, so
                // most others stop early; of equal ones the first in reading order wins.
                const tx = t % lw, ty = (t - tx) / lw;
                const cx = Math.min(coarse.w - 1, tx >> 1), cy = Math.min(coarse.h - 1, ty >> 1), s0 = nnf.map[cy * coarse.w + cx];
                if (s0 >= 0) {
                    const ox = tx + 2 * (s0 % coarse.w - cx), oy = ty + 2 * (Math.floor(s0 / coarse.w) - cy);
                    const centre = win * side + win;
                    for (let n = -1; n < side * side; n++) {
                        if (n === centre) continue;
                        const k = n < 0 ? centre : n, x = ox - win + k % side, y = oy - win + Math.floor(k / side);
                        if (x < 0 || y < 0 || x >= lw || y >= lh || !valid[y * lw + x]) continue;
                        const d = cost(t, y * lw + x, bestD);
                        if (d < bestD || (d === bestD && k < bestK)) { best = y * lw + x; bestD = d; bestK = k; }
                    }
                }
            }
            // The coarsest level, or a window with no source in it: search everywhere.
            if (best < 0) [best, bestD] = everywhere(t);
            next[t] = best; D[t] = bestD;
        }
        // Each hole pixel: the weighted vote of the patches that cover it.
        const acc = new Float64Array(lw * lh * 4), sum = new Float64Array(lw * lh);
        for (const t of targets) {
            const tx = t % lw, ty = (t - tx) / lw, s = next[t], sx = s % lw, sy = (s - sx) / lw;
            const weight = 1 / (1 + D[t] / (area * 4) / 100);
            for (let dy = -pr; dy <= pr; dy++) for (let dx = -pr; dx <= pr; dx++) {
                const x = tx + dx, y = ty + dy;
                if (x < 0 || y < 0 || x >= lw || y >= lh || !lhole[y * lw + x]) continue;
                const i = y * lw + x, j = ((sy + dy) * lw + sx + dx) * 4;
                for (let c = 0; c < 4; c++) acc[i * 4 + c] += weight * E[j + c];
                sum[i] += weight;
            }
        }
        for (let i = 0; i < lw * lh; i++) if (sum[i] > 0) for (let c = 0; c < 4; c++) E[i * 4 + c] = acc[i * 4 + c] / sum[i];
        if (l === 0) {
            // What the chosen patches say about the known pixels just around the hole, against what is there:
            // spread that difference smoothly across the hole, so the fill meets its surroundings without a seam.
            const ring = dilate(lhole, lw, lh, 2);
            for (let i = 0; i < lw * lh; i++) if (lhole[i]) ring[i] = 0;
            const pa = new Float64Array(lw * lh * 4), pt = new Float64Array(lw * lh);
            for (const t of targets) {
                const tx = t % lw, ty = (t - tx) / lw, s = next[t], sx = s % lw, sy = (s - sx) / lw;
                const weight = 1 / (1 + D[t] / (area * 4) / 100);
                for (let dy = -pr; dy <= pr; dy++) for (let dx = -pr; dx <= pr; dx++) {
                    const x = tx + dx, y = ty + dy;
                    if (x < 0 || y < 0 || x >= lw || y >= lh || !ring[y * lw + x]) continue;
                    const i = y * lw + x, j = ((sy + dy) * lw + sx + dx) * 4;
                    for (let c = 0; c < 4; c++) pa[i * 4 + c] += weight * E[j + c];
                    pt[i] += weight;
                }
            }
            const diff = new Float64Array(lw * lh * 4), wt = new Float64Array(lw * lh);
            for (let i = 0; i < lw * lh; i++) {
                if (!ring[i] || !(pt[i] > 0) || (lavoid && lavoid[i])) continue;
                wt[i] = 1;
                for (let c = 0; c < 4; c++) diff[i * 4 + c] = img[i * 4 + c] - pa[i * 4 + c] / pt[i];
            }
            const smooth = pullPush(diff, wt, lw, lh);
            for (let i = 0; i < lw * lh; i++) if (lhole[i]) for (let c = 0; c < 4; c++) E[i * 4 + c] = clamp(E[i * 4 + c] + smooth[i * 4 + c], 0, 255);
        }
        nnf = {map: next, E};
    }
    return E;
}

// Bright, colourless pixels (a caption's letters) among those set in within.
function brightCore(d, within, w, h) {
    const core = new Uint8Array(w * h);
    for (let i = 0; i < w * h; i++) {
        if (!within[i]) continue;
        const r = d[i * 4], g = d[i * 4 + 1], b = d[i * 4 + 2];
        if ((77 * r + 150 * g + 29 * b) >> 8 >= 190 && Math.max(r, g, b) - Math.min(r, g, b) <= 60) core[i] = 1;
    }
    return core;
}

// The half-width up to which a bright part counts as lettering. Past a tenth of the brush it is
// picture (a white shirt, a lamp), unless the stroke holds a caption whose letters are that thick
// themselves (a small brush on a large or bold caption): when three or more parts agree on a stroke
// width, joints and curves of that caption count as letters up to HEAL_TEXT_JOINT times its widest.
// The agreeing parts that cover the most pixels set it, so a few slivers the stroke's edge cut off
// do not. Parts below least (specks) take no part.
function strokeLimit(core, dist, w, h, brush, least) {
    const base = Math.max(1, Math.round(.1 * brush)), widths = [];
    parts(core, w, h, part => {
        if (part.length < least) return;
        // A part's half-width: the median distance along its middle (where no neighbour lies farther from the edge).
        const ridge = [];
        for (const i of part) {
            const x = i % w, y = (i - x) / w, v = dist[i];
            let top = true;
            for (let ny = y - 1; ny <= y + 1 && top; ny++) for (let nx = x - 1; nx <= x + 1; nx++) {
                if (ny >= 0 && nx >= 0 && ny < h && nx < w && dist[ny * w + nx] > v) { top = false; break; }
            }
            if (top) ridge.push(v);
        }
        ridge.sort((a, b) => a - b);
        widths.push([ridge[ridge.length >> 1], part.length]);
    });
    widths.sort((a, b) => a[0] - b[0]);
    let most = 0, widest = 0;
    for (let i = 0, j = 0, count = 0, pixels = 0; i < widths.length; i++) {
        count++; pixels += widths[i][1];
        while (widths[j][0] * HEAL_TEXT_AGREE + 1 < widths[i][0]) { count--; pixels -= widths[j][1]; j++; }
        if (count >= 3 && pixels > most) { most = pixels; widest = widths[i][0]; }
    }
    // A part inside a stroke is no wider than the brush, except where the picture's edge stands in
    // for the background a stroke would have around it (a selection one pixel wide): there its
    // half-width can run the selection's whole length.
    return most && widest >= base ? Math.ceil(HEAL_TEXT_JOINT * Math.min(widest, Math.max(base, brush))) : base;
}

// The caption inside a stroke: thin, bright, colourless strokes and their outline, with the
// half-width past which a bright part is picture. Null when the stroke holds no such text, or so
// much (over 70%) that it is not a caption.
function textHole(d, hole, w, h, brush) {
    const core = brightCore(d, hole, w, h);
    let total = 0;
    for (let i = 0; i < w * h; i++) total += hole[i];
    // Letters are thin: what survives thinning by the limit (a white shirt, a lamp) is picture,
    // and so is anything right beside it. Specks (a highlight on a buckle) are not letters either.
    const least = Math.max(4, Math.round((.12 * brush) ** 2)), dist = distances(core, w, h);
    const t = strokeLimit(core, dist, w, h, brush, least), thick = dilate(beyond(dist, t), w, h, t + 2);
    for (let i = 0; i < w * h; i++) if (thick[i]) core[i] = 0;
    let found = 0;
    parts(core, w, h, part => {
        if (part.length < least) for (const i of part) core[i] = 0;
        else found += part.length;
    });
    if (!found || found * 10 > total * 7) return null;
    const grown = dilate(core, w, h, Math.max(2, Math.round(HEAL_TEXT_OUTLINE * brush)));
    for (let i = 0; i < w * h; i++) grown[i] &= hole[i];
    return {mask: grown, core, limit: t};
}

// The rest of the letters a caption stroke cut through: thin bright parts of the region (as thin
// as limit allows) that hold letters found in the stroke and lie a fifth or more outside the hole,
// with their outline, outside the hole. Null unless the stroke cut through a third or more of the
// letters it holds: a stroke over a whole caption leaves alone what merely touches it.
function letterRest(d, hole, letters, w, h, brush, limit) {
    const core = brightCore(d, new Uint8Array(w * h).fill(1), w, h), rest = new Uint8Array(w * h);
    const thick = dilate(beyond(distances(core, w, h), limit), w, h, limit + 2);
    for (let i = 0; i < w * h; i++) if (thick[i]) core[i] = 0;
    let held = 0, cut = 0;
    parts(core, w, h, part => {
        if (!part.some(i => letters[i])) return;
        held++;
        let inside = 0;
        for (const i of part) inside += hole[i];
        if ((part.length - inside) * 5 < part.length) return;
        for (const i of part) rest[i] = 1;
        cut++;
    });
    if (!cut || cut * 3 < held) return null;
    const out = dilate(rest, w, h, Math.max(2, Math.round(HEAL_TEXT_OUTLINE * brush)));
    for (let i = 0; i < w * h; i++) if (hole[i]) out[i] = 0;
    return out;
}

// Spot healing: fill the stroke (only its caption when text is set, and
// nothing when it holds none) from patches of the picture around it; the
// stroke's soft outer edge blends into the picture. Without room for patches
// (a stroke over nearly everything) it fills smoothly from the surroundings.
function heal(raster, mask, text = false, brush = 0) {
    const w = raster.width, h = raster.height;
    const mctx = context(mask), md = mctx.getImageData(0, 0, w, h).data;
    const box = () => {
        let x0 = w, y0 = h, x1 = -1, y1 = -1;
        for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (md[(y * w + x) * 4 + 3] > 8) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
        return [x0, y0, x1, y1];
    };
    let [x0, y0, x1, y1] = box();
    if (x1 < 0) return;
    const rctx = context(raster);
    let limit = 0, letters = null;
    if (text) {
        // Only the caption is replaced, and the work centres on it rather than on the stroke.
        const bw = x1 - x0 + 1, bh = y1 - y0 + 1, hole = new Uint8Array(bw * bh);
        for (let y = 0; y < bh; y++) for (let x = 0; x < bw; x++) hole[y * bw + x] = md[((y + y0) * w + x + x0) * 4 + 3] > 8 ? 1 : 0;
        const words = textHole(rctx.getImageData(x0, y0, bw, bh).data, hole, bw, bh, brush);
        if (!words) return;     // no caption under the stroke: nothing to remove
        limit = words.limit;
        letters = {core: words.core, x0, y0, w: bw};
        for (let i = 3; i < md.length; i += 4) md[i] = 0;
        for (let y = 0; y < bh; y++) for (let x = 0; x < bw; x++) md[((y + y0) * w + x + x0) * 4 + 3] = words.mask[y * bw + x] ? 255 : 0;
        [x0, y0, x1, y1] = box();
    }
    const size = Math.max(x1 - x0 + 1, y1 - y0 + 1);
    const margin = Math.max(24, Math.min(HEAL_MARGIN, Math.round(size * .8)));
    const rx0 = Math.max(0, x0 - margin), ry0 = Math.max(0, y0 - margin), rx1 = Math.min(w - 1, x1 + margin), ry1 = Math.min(h - 1, y1 + margin);
    const rw = rx1 - rx0 + 1, rh = ry1 - ry0 + 1;
    const image = rctx.getImageData(rx0, ry0, rw, rh), d = image.data;
    const alpha = new Uint8Array(rw * rh), hole = new Uint8Array(rw * rh);
    for (let y = 0; y < rh; y++) for (let x = 0; x < rw; x++) {
        const a = md[((y + ry0) * w + x + rx0) * 4 + 3]; alpha[y * rw + x] = a; hole[y * rw + x] = a > 8 ? 1 : 0;
    }
    // Letters the caption stroke cut through go on outside it: no patch comes from them, and next
    // to the hole they are filled too (only the hole is kept), so the fill does not draw them back in.
    let work = hole, avoid = null;
    if (text) {
        const found = new Uint8Array(rw * rh), {core, x0: bx, y0: by, w: bw} = letters;
        for (let i = 0; i < core.length; i++) if (core[i]) found[(Math.floor(i / bw) + by - ry0) * rw + i % bw + bx - rx0] = 1;
        avoid = letterRest(d, hole, found, rw, rh, brush, limit);
    }
    if (avoid) {
        const close = dilate(hole, rw, rh, Math.max(4, Math.round(brush / 2)));
        work = hole.slice();
        for (let i = 0; i < rw * rh; i++) if (avoid[i] && close[i]) work[i] = 1;
    }
    const fill = patchFill(new Float32Array(d), work, rw, rh, size, text ? HEAL_NEAR_TEXT : HEAL_NEAR, avoid);
    if (fill) {
        for (let i = 0; i < rw * rh; i++) {
            if (!hole[i]) continue;
            const k = Math.min(1, alpha[i] / 128);
            for (let c = 0; c < 4; c++) d[i * 4 + c] = d[i * 4 + c] * (1 - k) + fill[i * 4 + c] * k;
        }
    } else {
        const col = new Float32Array(rw * rh * 4), wt = new Float32Array(rw * rh);
        // The rest of the caption is no more known here than for patches.
        // Unless that leaves nothing known: then the stroke fills as it would without a caption.
        let rest = avoid;
        if (rest) { rest = null; for (let i = 0; i < rw * rh && !rest; i++) if (!avoid[i] && !work[i] && alpha[i] < 255) rest = avoid; }
        for (let i = 0; i < rw * rh; i++) { const known = rest && (rest[i] || work[i]) ? 0 : 1 - alpha[i] / 255; wt[i] = known; for (let c = 0; c < 4; c++) col[i * 4 + c] = d[i * 4 + c] * known; }
        const smooth = pullPush(col, wt, rw, rh);
        for (let i = 0; i < rw * rh; i++) { const a = alpha[i] / 255; if (a) for (let c = 0; c < 4; c++) d[i * 4 + c] = d[i * 4 + c] * (1 - a) + smooth[i * 4 + c] * a; }
    }
    rctx.putImageData(image, rx0, ry0);
}

// Pixels similar to the seed, as an alpha mask of the raster.
export function wand(raster, u, v, tolerance, contiguous) {
    const w = raster.width, h = raster.height;
    const sx = clamp(Math.floor(u * w), 0, w - 1), sy = clamp(Math.floor(v * h), 0, h - 1);
    const d = context(raster).getImageData(0, 0, w, h).data;
    const s = (sy * w + sx) * 4, ref = [d[s], d[s + 1], d[s + 2], d[s + 3]], tol = tolerance * 2.55;
    const near = i => Math.abs(d[i] - ref[0]) <= tol && Math.abs(d[i + 1] - ref[1]) <= tol && Math.abs(d[i + 2] - ref[2]) <= tol && Math.abs(d[i + 3] - ref[3]) <= tol;
    const sel = new Uint8Array(w * h);
    if (contiguous) {
        const stack = [sy * w + sx]; sel[sy * w + sx] = 1;
        while (stack.length) {
            const p = stack.pop(), x = p % w, y = (p - x) / w;
            for (const [nx, ny] of [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]]) {
                if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
                const q = ny * w + nx;
                if (!sel[q] && near(q * 4)) { sel[q] = 1; stack.push(q); }
            }
        }
    } else for (let p = 0; p < w * h; p++) sel[p] = near(p * 4) ? 1 : 0;
    const m = canvas(w, h), mctx = context(m), image = mctx.createImageData(w, h);
    for (let p = 0; p < w * h; p++) if (sel[p]) { image.data[p * 4] = image.data[p * 4 + 1] = image.data[p * 4 + 2] = image.data[p * 4 + 3] = 255; }
    mctx.putImageData(image, 0, 0);
    return m;
}

// ---------------------------------------------------------------------------
// Rendering with caches. A layer's raster is rebuilt only when its edits change.

export class Renderer {
    constructor(resolve) {
        this.resolve = resolve;
        this.images = new Map();
        this.layers = new Map();
    }
    async load(doc) {
        const files = new Set(doc.layers.filter(l => l.src).map(l => l.src));
        await Promise.all([...files].map(f => this.image(f)));
    }
    image(file) {
        if (!this.images.has(file)) {
            this.images.set(file, new Promise((resolve, reject) => {
                const image = new Image();
                image.onload = () => resolve(image); image.onerror = () => reject(new Error('load'));
                image.src = this.resolve(file);
            }));
        }
        return this.images.get(file);
    }
    loaded(file) { return this.images.get(file)?.value; }
    async preload(doc) {
        for (const l of doc.layers) if (l.src) { const img = await this.image(l.src); this.images.get(l.src).value = img; }
        await loadFonts(textFonts(doc));
    }
    // Raster of a layer at scale × its native pixels, edits applied (before
    // adjustments and mask), reusing the cached prefix of its edits. exact: for a saved
    // picture, its source image is made smaller by area average.
    raster(doc, layer, scale, upto = layer.ops.length, exact = false) {
        // An earlier state (a wand or copy made before later edits) gets its own entry.
        // At full size the saved picture's raster is the same as the one on screen.
        const at = exact && scale < 1 ? `${scale}!` : scale;
        const key = upto === layer.ops.length ? `${layer.id}@${at}` : `${layer.id}@${at}^${upto}`;
        const w = Math.max(1, Math.round(layer.nw * scale)), h = Math.max(1, Math.round(layer.nh * scale));
        let entry = this.layers.get(key);
        const ops = layer.ops.slice(0, upto);
        const prefix = entry && entry.source === this.sourceKey(layer) && entry.ops.length <= ops.length && entry.ops.every((id, i) => id === ops[i].id);
        if (!prefix) {
            const c = canvas(w, h), ctx = context(c);
            if (layer.kind === 'image' && layer.src) drawSource(ctx, this.images.get(layer.src).value, w, h, exact);
            if (layer.copyOf) {
                const src = doc.layers.find(l => l.id === layer.copyOf.layer);
                if (src) {
                    ctx.drawImage(this.raster(doc, src, scale, layer.copyOf.upto, exact), 0, 0, w, h);
                    if (layer.copyOf.selection) maskWith(c, this.selectionMask(doc, layer.copyOf.selection, {layer: src, scale}));
                }
            }
            entry = {source: this.sourceKey(layer), ops: [], canvas: c};
        }
        for (const op of ops.slice(entry.ops.length)) { this.apply(doc, layer, entry.canvas, op, scale); entry.ops.push(op.id); }
        this.layers.set(key, entry);
        return entry.canvas;
    }
    sourceKey(layer) { return JSON.stringify([layer.src, layer.copyOf, layer.nw, layer.nh]); }
    // A layer as composited: edits, adjustments, mask; text and shapes drawn.
    layerImage(doc, layer, scale, exact = false) {
        if (layer.kind === 'text') return this.text(layer, scale);
        if (layer.kind === 'shape') return this.shape(layer, scale);
        const raster = this.raster(doc, layer, scale, layer.ops.length, exact);
        const key = `${layer.id}@${exact && scale < 1 ? `${scale}!` : scale}#look`;
        const look = JSON.stringify([layer.ops.map(o => o.id), layer.adjust, layer.mask?.ops.map(o => o.id), layer.src, layer.copyOf]);
        const cached = this.layers.get(key);
        if (cached?.look === look) return cached.canvas;
        let out = adjusted(raster, layer.adjust, hashString(layer.id), scale);
        if (layer.mask) {
            if (out === raster) { out = canvas(raster.width, raster.height); out.getContext('2d').drawImage(raster, 0, 0); }
            maskWith(out, this.maskRaster(doc, layer, scale));
        }
        this.layers.set(key, {look, canvas: out});
        return out;
    }
    maskRaster(doc, layer, scale) {
        const w = Math.max(1, Math.round(layer.nw * scale)), h = Math.max(1, Math.round(layer.nh * scale));
        const key = `${layer.id}@${scale}#mask`, ids = layer.mask.ops.map(o => o.id);
        let entry = this.layers.get(key);
        if (!(entry && entry.ops.length <= ids.length && entry.ops.every((id, i) => id === ids[i]))) {
            const c = canvas(w, h), ctx = context(c); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h);
            entry = {ops: [], canvas: c};
        }
        for (const op of layer.mask.ops.slice(entry.ops.length)) { this.apply(doc, layer, entry.canvas, op, scale, true); entry.ops.push(op.id); }
        this.layers.set(key, entry);
        return entry.canvas;
    }
    apply(doc, layer, target, op, scale, isMask = false) {
        const w = target.width, h = target.height, ctx = target.getContext('2d');
        const selection = op.selection ? this.selectionMask(doc, op.selection, {layer, scale}) : null;
        const within = img => selection ? maskWith(img, selection) : img;
        if (op.type === 'fromSelection') {
            ctx.clearRect(0, 0, w, h);
            if (op.selection) ctx.drawImage(selection, 0, 0); else { ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h); }
            if (op.invert) { ctx.globalCompositeOperation = 'xor'; ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h); ctx.globalCompositeOperation = 'source-over'; }
            return;
        }
        if (op.type === 'fill' || op.type === 'clear') {
            const layerFill = canvas(w, h), lctx = layerFill.getContext('2d');
            lctx.fillStyle = isMask ? '#fff' : op.color || '#000'; lctx.fillRect(0, 0, w, h);
            within(layerFill);
            ctx.globalCompositeOperation = op.type === 'clear' ? 'destination-out' : 'source-over';
            ctx.globalAlpha = op.opacity ?? 1; ctx.drawImage(layerFill, 0, 0);
            ctx.globalAlpha = 1; ctx.globalCompositeOperation = 'source-over';
            return;
        }
        if (op.type === 'gradient') {
            const g = canvas(w, h), gctx = g.getContext('2d');
            const [x1, y1, x2, y2] = [op.from[0] * w, op.from[1] * h, op.to[0] * w, op.to[1] * h];
            const grad = op.shape === 'radial' ? gctx.createRadialGradient(x1, y1, 0, x1, y1, Math.hypot(x2 - x1, y2 - y1) || 1)
                : gctx.createLinearGradient(x1, y1, x2, y2);
            grad.addColorStop(0, op.c1); grad.addColorStop(1, op.c2 === 'transparent' ? op.c1 + '00' : op.c2);
            gctx.fillStyle = grad; gctx.fillRect(0, 0, w, h);
            within(g); ctx.globalAlpha = op.opacity ?? 1; ctx.drawImage(g, 0, 0); ctx.globalAlpha = 1;
            return;
        }
        if (op.type !== 'stroke') return;
        const mask = within(strokeMask(w, h, op));
        const opacity = op.opacity ?? 1;
        if (op.tool === 'paint' || op.tool === 'reveal') {
            const paint = canvas(w, h), pctx = paint.getContext('2d');
            pctx.fillStyle = op.tool === 'reveal' ? '#fff' : op.color; pctx.fillRect(0, 0, w, h); maskWith(paint, mask);
            ctx.globalAlpha = opacity; ctx.drawImage(paint, 0, 0); ctx.globalAlpha = 1;
        } else if (op.tool === 'erase' || op.tool === 'hide') {
            ctx.globalCompositeOperation = 'destination-out'; ctx.globalAlpha = opacity; ctx.drawImage(mask, 0, 0);
            ctx.globalAlpha = 1; ctx.globalCompositeOperation = 'source-over';
        } else if (op.tool === 'clone') {
            const copy = canvas(w, h), cctx = copy.getContext('2d');
            cctx.drawImage(target, op.offset[0] * w, op.offset[1] * h); maskWith(copy, mask);
            ctx.globalAlpha = opacity; ctx.drawImage(copy, 0, 0); ctx.globalAlpha = 1;
        } else if (op.tool === 'blur') {
            const soft = blurred(target, Math.max(1, op.size * Math.min(w, h) * .25 * (op.strength ?? .6)));
            maskWith(soft, mask);
            ctx.globalAlpha = opacity; ctx.drawImage(soft, 0, 0); ctx.globalAlpha = 1;
        } else if (op.tool === 'heal') {
            heal(target, mask, !!op.text, op.size * Math.min(w, h));
        }
    }
    // A selection drawn into a layer's pixels (or into D when target.layer is absent).
    selectionMask(doc, selection, {layer, scale}) {
        const w = Math.max(1, Math.round(layer.nw * scale)), h = Math.max(1, Math.round(layer.nh * scale));
        const out = canvas(w, h), ctx = context(out);
        // D → layer pixels at this scale.
        const toLocal = new DOMMatrix().scaleSelf(scale, scale).multiplySelf(layerMatrix(layer).inverse());
        for (const part of selection.parts) {
            ctx.globalCompositeOperation = part.mode === 'subtract' ? 'destination-out' : part.mode === 'intersect' ? 'destination-in' : 'source-over';
            if (part.mode === 'replace') ctx.clearRect(0, 0, w, h);
            ctx.save();
            if (part.shape === 'wand') {
                const src = doc.layers.find(l => l.id === part.layer);
                if (src) {
                    const r = this.raster(doc, src, scale, part.upto ?? src.ops.length);
                    const m = wand(r, part.u, part.v, part.tolerance, part.contiguous);
                    const toSrc = new DOMMatrix().scaleSelf(scale, scale).multiplySelf(layerMatrix(layer).inverse()).multiplySelf(layerMatrix(src)).scaleSelf(1 / scale, 1 / scale);
                    ctx.setTransform(toSrc); ctx.drawImage(m, 0, 0);
                }
            } else {
                ctx.setTransform(toLocal);
                ctx.fillStyle = '#fff';
                ctx.beginPath();
                if (part.shape === 'rect') ctx.rect(part.x, part.y, part.w, part.h);
                else if (part.shape === 'ellipse') ctx.ellipse(part.x + part.w / 2, part.y + part.h / 2, Math.abs(part.w / 2), Math.abs(part.h / 2), 0, 0, Math.PI * 2);
                else if (part.shape === 'all') ctx.rect(-1e6, -1e6, 2e6, 2e6);
                else if (part.pts?.length > 2) { ctx.moveTo(...part.pts[0]); for (const p of part.pts.slice(1)) ctx.lineTo(...p); ctx.closePath(); }
                ctx.fill();
            }
            ctx.restore();
        }
        ctx.globalCompositeOperation = 'source-over';
        if (selection.invert) { ctx.globalCompositeOperation = 'xor'; ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h); ctx.globalCompositeOperation = 'source-over'; }
        if (selection.feather > 0) {
            const px = selection.feather * scale * Math.max(layer.nw / layer.w, layer.nh / layer.h);
            return blurred(out, px);
        }
        return out;
    }
    text(layer, scale) {
        const key = `${layer.id}@${scale}#text`, look = JSON.stringify([layer.text, layer.nw, layer.nh]);
        const cached = this.layers.get(key);
        if (cached?.look === look) return cached.canvas;
        const t = layer.text, c = canvas(layer.nw * scale, layer.nh * scale), ctx = c.getContext('2d');
        ctx.scale(scale, scale);
        ctx.font = fontCSS(t.font, t.weight, t.size);
        ctx.fillStyle = t.color; ctx.textBaseline = 'top';
        ctx.textAlign = t.align || 'left';
        const x = t.align === 'center' ? layer.nw / 2 : t.align === 'right' ? layer.nw - t.size * .2 : t.size * .2;
        if (t.shadow) { ctx.shadowColor = 'rgba(0,0,0,.55)'; ctx.shadowBlur = t.size * .18; ctx.shadowOffsetY = t.size * .05; }
        if (t.outline) { ctx.lineWidth = Math.max(1, t.size * .08); ctx.strokeStyle = t.outline; ctx.lineJoin = 'round'; }
        String(t.content).split('\n').forEach((line, i) => {
            const y = t.size * .2 + i * t.size * (t.lineHeight || 1.25);
            if (t.outline) ctx.strokeText(line, x, y);
            ctx.fillText(line, x, y);
        });
        this.layers.set(key, {look, canvas: c});
        return c;
    }
    shape(layer, scale) {
        const key = `${layer.id}@${scale}#shape`, look = JSON.stringify([layer.shape, layer.nw, layer.nh]);
        const cached = this.layers.get(key);
        if (cached?.look === look) return cached.canvas;
        const s = layer.shape, c = canvas(layer.nw * scale, layer.nh * scale), ctx = c.getContext('2d');
        ctx.scale(scale, scale);
        const lw = s.stroke ? s.width : 0, inset = lw / 2;
        ctx.lineWidth = lw; ctx.strokeStyle = s.stroke || 'transparent'; ctx.fillStyle = s.fill || 'transparent';
        ctx.lineCap = 'round';
        ctx.beginPath();
        if (s.type === 'ellipse') ctx.ellipse(layer.nw / 2, layer.nh / 2, Math.max(1, layer.nw / 2 - inset), Math.max(1, layer.nh / 2 - inset), 0, 0, Math.PI * 2);
        else if (s.type === 'line') { ctx.moveTo(inset, layer.nh / 2); ctx.lineTo(layer.nw - inset, layer.nh / 2); }
        else ctx.roundRect ? ctx.roundRect(inset, inset, layer.nw - lw, layer.nh - lw, s.radius || 0) : ctx.rect(inset, inset, layer.nw - lw, layer.nh - lw);
        if (s.fill && s.type !== 'line') ctx.fill();
        if (s.stroke) ctx.stroke();
        this.layers.set(key, {look, canvas: c});
        return c;
    }
    // The scale a layer's pixels are made at: as fine as it is drawn, never finer than its own
    // pixels. On screen it is a power of two, so moving or resizing a layer keeps reusing them;
    // a saved picture (exact) makes them at the size drawn.
    layerScale(layer, scale, exact = false) {
        const need = scale * Math.max(Math.abs(layer.w / layer.nw), Math.abs(layer.h / layer.nh));
        if (need >= .999) return 1;
        if (exact) return need;
        let s = 1; while (s / 2 >= need * .999 && s > 1 / 64) s /= 2;
        return s;
    }
    // The scales the layers are made at for a picture at this scale (the ones to keep cached).
    scales(doc, scale) { return [scale, ...doc.layers.map(l => this.layerScale(l, scale))]; }
    // The whole canvas at scale × oriented pixels.
    composite(doc, scale, {skip = null, background = true, exact = false} = {}) {
        const rect = canvasRect(doc);
        const out = canvas(rect.w * scale, rect.h * scale), ctx = out.getContext('2d');
        if (background && (doc.extend.l || doc.extend.t || doc.extend.r || doc.extend.b)) {
            ctx.fillStyle = doc.extend.color; ctx.fillRect(0, 0, out.width, out.height);
        }
        const view = new DOMMatrix().scaleSelf(scale, scale).translateSelf(-rect.x, -rect.y).multiplySelf(orientMatrix(doc));
        for (const layer of doc.layers) {
            if (!layer.visible || layer.id === skip) continue;
            const s = this.layerScale(layer, scale, exact);
            const img = this.layerImage(doc, layer, s, exact);
            ctx.save();
            ctx.setTransform(new DOMMatrix(view).multiplySelf(layerMatrix(layer)).scaleSelf(1 / s, 1 / s));
            // A picture enlarged past its own pixels is smoothed with the finer filter when saved;
            // everything else is drawn at about its own size, where the plain one is as good and far quicker.
            if (exact && scale * Math.max(Math.abs(layer.w / layer.nw), Math.abs(layer.h / layer.nh)) > 1.001) ctx.imageSmoothingQuality = 'high';
            ctx.globalAlpha = layer.opacity; ctx.globalCompositeOperation = layer.blend || 'source-over';
            ctx.drawImage(img, 0, 0);
            ctx.restore();
        }
        return out;
    }
    async export(doc, type) {
        await this.preload(doc);
        const full = this.composite(doc, 1, {exact: true});
        const rect = canvasRect(doc), c = doc.crop;
        const out = canvas(c.w, c.h), ctx = out.getContext('2d');
        if (type === 'image/jpeg') { ctx.fillStyle = doc.extend.color || '#000'; ctx.fillRect(0, 0, out.width, out.height); }
        ctx.drawImage(full, Math.round(c.x - rect.x), Math.round(c.y - rect.y), out.width, out.height, 0, 0, out.width, out.height);
        const blob = await new Promise((resolve, reject) => out.toBlob(b => b ? resolve(b) : reject(new Error('render')), type, .95));
        return {blob, width: out.width, height: out.height};
    }
    // Drop cached rasters made at other preview scales (zooming leaves them behind).
    prune(scales) {
        const keep = new Set(scales.map(String));
        for (const [key, entry] of this.layers) {
            const scale = key.split('@')[1]?.split(/[#^]/)[0];
            if (scale !== undefined && !keep.has(scale) && parseFloat(scale) > .05) { if (entry.canvas) entry.canvas.width = entry.canvas.height = 0; this.layers.delete(key); }
        }
    }
    // Free every cached canvas; the browser reclaims their memory.
    release() {
        for (const entry of this.layers.values()) { if (entry.canvas) { entry.canvas.width = entry.canvas.height = 0; } }
        this.layers.clear(); this.images.clear();
    }
}
