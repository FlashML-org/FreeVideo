// Immutable document commands shared by the editor and DOM-free hosts.
// Names and text metrics belong to the caller; rendering stays in the engine.
import {BLENDS, uid, emptyAdjust, orientedSize, orientMatrix, canvasRect, paintLayer, rasterKinds,
    resizeDocument, scaleSelection, documentBounds} from './image_editor_engine.js';

export class ImageEditError extends Error {
    constructor(code, message) { super(message); this.name = 'ImageEditError'; this.code = code; }
}
const fail = (code, message) => { throw new ImageEditError(code, message); };
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const finite = v => typeof v === 'number' && Number.isFinite(v);
const positive = v => finite(v) && v > 0;
const dimensions = (w, h) => { if (!positive(w) || !positive(h)) fail('range', 'Invalid dimensions'); };
function layerOf(doc, id) {
    const layer = doc.layers.find(l => l.id === id);
    if (!layer) fail('not_found', 'Layer not found');
    return layer;
}
function pixelLayer(doc, id) {
    const layer = layerOf(doc, id);
    if (!rasterKinds.has(layer.kind)) fail('not_pixel_layer', 'An image or paint layer is required');
    return layer;
}
const replace = (doc, id, patch) => ({...doc, layers: doc.layers.map(l => l.id === id ? {...l, ...patch} : l)});
const requireSelection = selection => { if (!selection) fail('no_selection', 'A selection is required'); };
function fields(patch, allowed) {
    if (!patch || typeof patch !== 'object' || Array.isArray(patch)) fail('unsupported', 'Expected a property object');
    if (Object.keys(patch).some(key => !allowed.includes(key))) fail('unsupported', 'Unsupported property');
}

export function commitStep({history, future}, next, limit = 80) {
    if (!Number.isInteger(limit) || limit < 1) fail('range', 'Invalid history limit');
    const last = history[history.length - 1];
    if (next === last || (last && JSON.stringify(next) === JSON.stringify(last))) return {history, future, doc: last};
    return {history: [...history, next].slice(-limit), future: [], doc: next};
}
function travel({history, future, doc = history[history.length - 1], selection = null}, direction) {
    if (direction < 0 ? history.length <= 1 : !future.length) fail('range', 'No history step available');
    const nextHistory = direction < 0 ? history.slice(0, -1) : [...history, future[future.length - 1]];
    const nextFuture = direction < 0 ? [...future, history[history.length - 1]] : future.slice(0, -1);
    let next = nextHistory[nextHistory.length - 1];
    if (!next.layers.some(l => l.id === next.active)) next = {...next, active: next.layers.at(-1)?.id || null};
    const sx = next.width / doc.width, sy = next.height / doc.height;
    return {history: nextHistory, future: nextFuture, doc: next,
        selection: sx === 1 && sy === 1 ? selection : scaleSelection(selection, sx, sy), scale: {sx, sy}};
}
export function undoStep(state) { return travel(state, -1); }
export function redoStep(state) { return travel(state, 1); }

export function insertLayer(doc, layer) {
    const index = doc.layers.findIndex(l => l.id === doc.active);
    if (index < 0 && doc.layers.length) fail('not_found', 'Active layer not found');
    if (doc.layers.some(l => l.id === layer.id)) fail('unsupported', 'Duplicate layer id');
    const layers = [...doc.layers]; layers.splice(index + 1, 0, layer);
    return {...doc, layers, active: layer.id};
}
export function addPaintLayer(doc, name) { return insertLayer(doc, paintLayer(doc, name)); }
// The sketch's mannequins (pose tool): one layer over the whole canvas, right above the paper or the picture, so every
// stroke and shape stays on top of it. `pose` is the scene as plain JSON (pose_stage.js); the layer has no pixels of its own.
export function addPoseLayer(doc, {name, pose}) {
    if (!pose || typeof pose !== 'object') fail('unsupported', 'A pose scene is required');
    const b = documentBounds(doc);
    const layer = {id: uid('l'), kind: 'pose', name, visible: true, locked: false, opacity: 1, blend: 'source-over',
        cx: b.x + b.w / 2, cy: b.y + b.h / 2, w: b.w, h: b.h, rotation: 0, flipX: false, flipY: false,
        nw: Math.round(b.w), nh: Math.round(b.h), ops: [], mask: null, adjust: emptyAdjust(), pose};
    const layers = [...doc.layers]; layers.splice(Math.min(1, layers.length), 0, layer);
    return {...doc, layers};
}
export function updatePose(doc, id, pose) {
    const layer = layerOf(doc, id);
    if (layer.kind !== 'pose' || !pose || typeof pose !== 'object') fail('unsupported', 'A pose layer and scene are required');
    return replace(doc, id, {pose});
}
function upright(doc, w, h, angle = 0) {
    return {w, h, rotation: (doc.flipX !== doc.flipY ? -angle : angle) - doc.rotate, flipX: doc.flipX, flipY: doc.flipY};
}
function centre(doc, point) {
    const p = orientMatrix(doc).inverse().transformPoint(new DOMPoint(...point));
    return [p.x, p.y];
}
function newLayer(doc, kind, name, point, w, h, angle = 0) {
    return {id: uid('l'), kind, name, visible: true, locked: false, opacity: 1, blend: 'source-over',
        cx: point[0], cy: point[1], ...upright(doc, w, h, angle), ops: [], mask: null, adjust: emptyAdjust()};
}
export function addImageLayer(doc, {src, nw, nh, name}) {
    dimensions(nw, nh);
    const r = canvasRect(doc), point = centre(doc, [r.x + r.w / 2, r.y + r.h / 2]);
    // Preserve the editor's .66 fit factor (rather than changing it to 2/3).
    const s = Math.min(1, r.w * .66 / nw, r.h * .66 / nh);
    return insertLayer(doc, {...newLayer(doc, 'image', name, point, nw * s, nh * s), src, nw, nh});
}
export function duplicateLayer(doc, id, name) {
    return insertLayer(doc, {...layerOf(doc, id), id: uid('l'), locked: false, name});
}
export function deleteLayer(doc, id) {
    const layer = layerOf(doc, id);
    if (layer.locked) fail('locked', 'Layer is locked');
    if (doc.layers.length === 1) fail('range', 'Cannot delete the last layer');
    const index = doc.layers.findIndex(l => l.id === id), layers = doc.layers.filter(l => l.id !== id);
    return {...doc, layers, active: layers[Math.max(0, index - 1)].id};
}
export function moveLayer(doc, id, delta) {
    layerOf(doc, id);
    const index = doc.layers.findIndex(l => l.id === id), to = index + delta;
    const first = doc.layers[0]?.id === 'base' ? 1 : 0;
    if (!Number.isInteger(delta) || index < first || to < first || to >= doc.layers.length) fail('range', 'Layer move out of range');
    const layers = [...doc.layers]; [layers[index], layers[to]] = [layers[to], layers[index]];
    return {...doc, layers};
}
export function setActive(doc, id) { layerOf(doc, id); return {...doc, active: id}; }
export function renameLayer(doc, id, name) { layerOf(doc, id); return replace(doc, id, {name}); }
export function setLayer(doc, id, patch) {
    layerOf(doc, id);
    fields(patch, ['visible', 'locked', 'opacity', 'blend', 'adjust']);
    for (const key of ['visible', 'locked']) if (key in patch && typeof patch[key] !== 'boolean') fail('unsupported', 'Expected a boolean');
    if ('opacity' in patch && (!finite(patch.opacity) || patch.opacity < 0 || patch.opacity > 1)) fail('range', 'Invalid opacity');
    if ('blend' in patch && !BLENDS.includes(patch.blend)) fail('unsupported', 'Unsupported blend');
    if ('adjust' in patch) {
        fields(patch.adjust, Object.keys(emptyAdjust()));
        for (const [key, v] of Object.entries(patch.adjust)) {
            const lo = ['sharpen', 'blur', 'noise'].includes(key) ? 0 : key === 'hue' ? -180 : -100;
            if (!finite(v) || v < lo || v > (key === 'hue' ? 180 : 100)) fail('range', 'Invalid adjustment');
        }
    }
    return replace(doc, id, patch);
}
export function toggleMask(doc, id, selection) {
    // Any layer can drop a mask it has; only image and paint layers get a new one.
    if (layerOf(doc, id).mask) return {doc: replace(doc, id, {mask: null}), maskTarget: false, selectionUsed: false};
    pixelLayer(doc, id);
    return {doc: replace(doc, id, {mask: {ops: selection ? [{id: uid('o'), type: 'fromSelection', selection}] : []}}),
        maskTarget: true, selectionUsed: !!selection};
}

const ratios = {'16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1, '4:3': 4 / 3, '3:4': 3 / 4};
function aspectOf(id, r, target) {
    if (id === 'video') { if (target) dimensions(target.width, target.height); return target ? target.width / target.height : null; }
    if (id === 'original') return r.w / r.h;
    if (id === 'free') return null;
    if (!Object.keys(ratios).includes(id)) fail('unsupported', 'Unsupported ratio');
    return ratios[id];
}
function fitRect(aspect, r) {
    let w = r.w, h = r.w / aspect;
    if (h > r.h) { h = r.h; w = h * aspect; }
    return {x: r.x + (r.w - w) / 2, y: r.y + (r.h - h) / 2, w, h};
}
export function setRatio(doc, ratioId, target = null) {
    const r = canvasRect(doc), aspect = aspectOf(ratioId, r, target);
    let crop = {...doc.crop};
    if (aspect) {
        const fitted = fitRect(aspect, r), cx = crop.x + crop.w / 2, cy = crop.y + crop.h / 2;
        crop = {...fitted, x: clamp(cx - fitted.w / 2, r.x, r.x + r.w - fitted.w), y: clamp(cy - fitted.h / 2, r.y, r.y + r.h - fitted.h)};
    }
    return {...doc, ratio: ratioId, crop};
}
export function turn(doc, op, target = null) {
    if (!['cw', 'ccw', 'flipX', 'flipY'].includes(op)) fail('unsupported', 'Unsupported turn');
    const before = canvasRect(doc), next = {...doc}, e = doc.extend;
    if (op === 'cw' || op === 'ccw') {
        const mirrored = doc.flipX !== doc.flipY;
        next.rotate = (doc.rotate + ((op === 'cw') !== mirrored ? 90 : 270)) % 360;
        next.extend = op === 'cw' ? {...e, l: e.b, t: e.l, r: e.t, b: e.r} : {...e, l: e.t, t: e.r, r: e.b, b: e.l};
    } else {
        next[op] = !doc[op];
        next.extend = op === 'flipX' ? {...e, l: e.r, r: e.l} : {...e, t: e.b, b: e.t};
    }
    const map = ([x, y]) => {
        const u = (x - before.x) / before.w, v = (y - before.y) / before.h;
        return op === 'cw' ? [1 - v, u] : op === 'ccw' ? [v, 1 - u] : op === 'flipX' ? [1 - u, v] : [u, 1 - v];
    };
    const after = canvasRect(next), c = doc.crop, a = map([c.x, c.y]), b = map([c.x + c.w, c.y + c.h]);
    let crop = {x: after.x + Math.min(a[0], b[0]) * after.w, y: after.y + Math.min(a[1], b[1]) * after.h,
        w: Math.abs(a[0] - b[0]) * after.w, h: Math.abs(a[1] - b[1]) * after.h};
    if ((op === 'cw' || op === 'ccw') && doc.ratio !== 'free') {
        const aspect = aspectOf(doc.ratio, after, target);
        if (aspect) crop = fitRect(aspect, after);
    }
    return {...next, crop};
}
export function padToVideo(doc, target, color) {
    if (!target) fail('unsupported', 'A video target is required');
    dimensions(target.width, target.height);
    const [ow, oh] = orientedSize(doc), aspect = target.width / target.height;
    let l = 0, r = 0, t = 0, b = 0;
    if (ow / oh < aspect) { const extra = Math.round(oh * aspect - ow); l = Math.floor(extra / 2); r = extra - l; }
    else { const extra = Math.round(ow / aspect - oh); t = Math.floor(extra / 2); b = extra - t; }
    const next = {...doc, extend: {l, t, r, b, color}, ratio: 'video'};
    return {...next, crop: canvasRect(next)};
}
export function setCanvas(doc, extend) {
    // The old dialog applies numeric values directly; its HTML min/max are not
    // application guards. Preserve that policy when the resulting canvas fits.
    if (['l', 't', 'r', 'b'].some(k => !finite(extend[k]))) fail('range', 'Invalid canvas extension');
    const next = {...doc, extend: {...extend}};
    const crop = canvasRect(next); dimensions(crop.w, crop.h);
    return {...next, crop, ratio: 'free'};
}
export function imageSizeCheck(doc, w, h, target = null) {
    // target affects UI advice, never whether a size is allowed.
    if (![w, h].every(v => Number.isInteger(v) && v >= 1 && v <= 20000)) return {allowed: false, reason: 'dimensions'};
    if (Math.max(w / doc.crop.w, h / doc.crop.h) > 2 + 1e-9) return {allowed: false, reason: 'enlargement'};
    if (w * h > 64e6) return {allowed: false, reason: 'pixels'};
    if (w === Math.round(doc.crop.w) && h === Math.round(doc.crop.h)) return {allowed: false, reason: 'unchanged'};
    return {allowed: true, reason: null};
}
export function resizeImage(doc, w, h, keep = true, measure) {
    const check = imageSizeCheck(doc, w, h);
    if (!check.allowed) fail('range', check.reason);
    if (doc.layers.some(l => l.kind === 'text') && typeof measure !== 'function') fail('unsupported', 'Text measurement is required');
    return resizeDocument(doc, w, h, keep, measure);
}
// Crop drag constraints are pure too; the UI keeps pointer anchoring and preview.
export function normalizeCrop(doc, crop, ratioId = doc.ratio, target = null) {
    if (![crop.x, crop.y, crop.w, crop.h].every(finite)) fail('range', 'Invalid crop');
    const r = canvasRect(doc), a = aspectOf(ratioId, r, target);
    let {x, y, w, h} = crop;
    w = clamp(w, 8, r.w); h = clamp(h, 8, r.h);
    if (a) { if (w / h > a) w = h * a; else h = w / a; if (w > r.w) { w = r.w; h = w / a; } if (h > r.h) { h = r.h; w = h * a; } }
    return {x: clamp(x, r.x, r.x + r.w - w), y: clamp(y, r.y, r.y + r.h - h), w, h};
}

export function addPart(selection, part, mode) {
    if (!['replace', 'add', 'subtract', 'intersect'].includes(mode)) fail('unsupported', 'Unsupported selection mode');
    if (mode === 'replace' || !selection) return {parts: [{...part, mode: 'replace'}], feather: part.feather || 0, invert: false};
    return {...selection, parts: [...selection.parts, {...part, mode}]};
}
export function selectAll() { return {parts: [{shape: 'all', mode: 'replace'}], feather: 0, invert: false}; }
export function invertSelection(selection) { return selection ? {...selection, invert: !selection.invert} : {...selectAll(), invert: true}; }
export function pushOp(doc, layerId, op) {
    const layer = pixelLayer(doc, layerId), entry = {id: uid('o'), ...op};
    const mask = layer.mask && (op.tool === 'hide' || op.tool === 'reveal');
    if (layer.locked && !mask) fail('locked', 'Layer is locked');
    return replace(doc, layerId, mask ? {mask: {ops: [...layer.mask.ops, entry]}} : {ops: [...layer.ops, entry]});
}
export function fillSelection(doc, id, color, selection) { return pushOp(doc, id, {type: 'fill', color, selection}); }
// The eraser over a vector shape: the stroke hides that part through the shape's own mask (made on first use), so the
// shape stays a shape (it can still be moved, resized, recoloured) and the erased part goes with it.
export function maskStroke(doc, id, op) {
    const layer = layerOf(doc, id);
    if (layer.locked) fail('locked', 'Layer is locked');
    if (op?.type !== 'stroke') fail('unsupported', 'A stroke is required');
    const mask = layer.mask || {ops: []};
    return replace(doc, id, {mask: {ops: [...mask.ops, {id: uid('o'), ...op, tool: 'hide'}]}});
}
export function clearSelection(doc, id, selection) {
    pixelLayer(doc, id); requireSelection(selection);
    return pushOp(doc, id, {type: 'clear', selection});
}
export function copyToLayer(doc, id, selection, name) {
    const layer = pixelLayer(doc, id);
    const copy = {...layer, id: uid('l'), locked: false, src: null, name, ops: [], mask: null,
        copyOf: {layer: layer.id, upto: layer.ops.length, selection}, adjust: {...layer.adjust}};
    return insertLayer(doc, copy);
}
export function maskFromSelection(doc, id, selection) {
    pixelLayer(doc, id); requireSelection(selection);
    return replace(doc, id, {mask: {ops: [{id: uid('o'), type: 'fromSelection', selection}]}});
}

function measured(text, measure) {
    if (typeof measure !== 'function') fail('unsupported', 'Text measurement is required');
    const size = measure(text); dimensions(size.nw, size.nh); return size;
}
export function addTextLayer(doc, {position, name, text}, measure) {
    if (!position?.every(finite) || position.length !== 2) fail('range', 'Invalid text position');
    if (!positive(text.size)) fail('range', 'Invalid text size');
    const {nw, nh} = measured(text, measure);
    return insertLayer(doc, {...newLayer(doc, 'text', name, position, nw, nh), nw, nh, text: {...text}});
}
export function updateText(doc, id, patch, measure) {
    const l = layerOf(doc, id);
    if (l.kind !== 'text') fail('unsupported', 'A text layer is required');
    fields(patch, ['content', 'font', 'weight', 'size', 'color', 'align', 'shadow', 'outline', 'lineHeight']);
    if ('size' in patch && (!positive(patch.size) || patch.size < 6 || patch.size > 2000)) fail('range', 'Invalid text size');
    if ('lineHeight' in patch && !positive(patch.lineHeight)) fail('range', 'Invalid line height');
    const text = {...l.text, ...patch}, size = measured(text, measure);
    const sx = l.w / l.nw, sy = l.h / l.nh;
    return replace(doc, id, {text, ...size, w: size.nw * sx, h: size.nh * sy});
}
export const SHAPES = Object.freeze(['rect', 'ellipse', 'line', 'arrow', 'triangle']), LINE_SHAPES = Object.freeze(['line', 'arrow']);
export function addShapeLayer(doc, {from, to, shape, name}) {
    if (from?.length !== 2 || to?.length !== 2 || ![...from, ...to].every(finite)) fail('range', 'Invalid shape points');
    // A shape is a vector object (type, fill, stroke, width) that stays editable; lines and arrows run along their box.
    if (!SHAPES.includes(shape.type)) fail('unsupported', 'Unsupported shape');
    if (!positive(shape.width)) fail('range', 'Invalid stroke width');
    const dx = to[0] - from[0], dy = to[1] - from[1], point = centre(doc, [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2]);
    const line = LINE_SHAPES.includes(shape.type), w = line ? Math.hypot(dx, dy) : Math.max(4, Math.abs(dx));
    const h = line ? Math.max(shape.width * (shape.type === 'arrow' ? 4 : 2), shape.type === 'arrow' ? 16 : 8) : Math.max(4, Math.abs(dy));
    dimensions(w, h);
    const angle = line ? Math.atan2(dy, dx) * 180 / Math.PI : 0;
    return insertLayer(doc, {...newLayer(doc, 'shape', name, point, w, h, angle),
        nw: Math.max(1, Math.round(w)), nh: Math.max(1, Math.round(h)), shape: {...shape}});
}
export function updateShape(doc, id, patch) {
    const l = layerOf(doc, id);
    if (l.kind !== 'shape') fail('unsupported', 'A shape layer is required');
    fields(patch, ['fill', 'stroke', 'width', 'radius']);
    if ('width' in patch && (!finite(patch.width) || patch.width < 1 || patch.width > 400)) fail('range', 'Invalid stroke width');
    if ('radius' in patch && (!finite(patch.radius) || patch.radius < 0 || patch.radius > 2000)) fail('range', 'Invalid corner radius');
    return replace(doc, id, {shape: {...l.shape, ...patch}});
}
