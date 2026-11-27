// Pure counterpart of freevideo_engine/editor_picture.py; no DOM or project state.
// Coordinates are pixel edges; matrix uses CSS [a,b,c,d,e,f] order.
const cache = new Map();
const number = (v, fallback, low, high) => typeof v !== 'number' || Number.isNaN(v)
    ? fallback : Math.min(high, Math.max(low, v));
const dimension = v => {
    if (!Number.isSafeInteger(v) || v < 1) throw new RangeError('Picture dimensions must be positive integers');
    return v;
};
// Math.round differs from Python round at exact half-integers (including -0.5).
function round(v) {
    const low = Math.floor(v), part = v - low;
    return part === .5 ? (low % 2 === 0 ? low : low + 1) : Math.floor(v + .5);
}
function fields(clip) {
    clip = clip && typeof clip === 'object' && !Array.isArray(clip) ? clip : {};
    const edges = ['left', 'top', 'right', 'bottom'].map(edge => number(clip[`crop_${edge}`], 0, 0, .9));
    for (const [a, b] of [[0, 2], [1, 3]]) {
        const total = edges[a] + edges[b];
        if (total > .9) [edges[a], edges[b]] = [edges[a] * .9 / total, edges[b] * .9 / total];
    }
    return [clip.fit === 'contain' ? 'contain' : 'cover', ...edges,
        clip.flip_h === true, clip.flip_v === true, number(clip.rotate, 0, -180, 180),
        number(clip.scale, 1, .1, 10), number(clip.offset_x, 0, -2, 2), number(clip.offset_y, 0, -2, 2)];
}
function apply(m, [x, y]) {
    const [a, b, c, d, e, f] = m;
    return [a * x + c * y + e, b * x + d * y + f];
}
function inverse(m) {
    const [a, b, c, d, e, f] = m, det = a * d - b * c;
    return [d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det];
}
function clipPolygon(polygon, width, height) {
    for (const [axis, bound, sign] of [[0, 0, 1], [0, width, -1], [1, 0, 1], [1, height, -1]]) {
        const result = [];
        if (!polygon.length) break;
        let previous = polygon.at(-1), before = sign * (previous[axis] - bound);
        for (const point of polygon) {
            const after = sign * (point[axis] - bound);
            if ((before >= 0) !== (after >= 0)) {
                const t = before / (before - after);
                result.push([previous[0] + t * (point[0] - previous[0]), previous[1] + t * (point[1] - previous[1])]);
            }
            if (after >= 0) result.push(point);
            [previous, before] = [point, after];
        }
        polygon = result;
    }
    const unique = [];
    for (const p of polygon) {
        if (!unique.length || Math.max(...p.map((v, i) => Math.abs(v - unique.at(-1)[i]))) > 1e-9) unique.push(p);
    }
    if (unique.length > 1 && Math.max(...unique[0].map((v, i) => Math.abs(v - unique.at(-1)[i]))) <= 1e-9) unique.pop();
    return unique;
}
function area(polygon) {
    return Math.abs(polygon.reduce((sum, p, i) => {
        const prev = polygon[(i + polygon.length - 1) % polygon.length];
        return sum + prev[0] * p[1] - prev[1] * p[0];
    }, 0)) / 2;
}
function build(f, sw, sh, width, height) {
    const [fit, left, top, right, bottom, flip_h, flip_v, angle, scale, ox, oy] = f;
    const x = Math.min(sw - 1, round(left * sw)), y = Math.min(sh - 1, round(top * sh));
    const cw = Math.max(1, sw - round(right * sw) - x), ch = Math.max(1, sh - round(bottom * sh) - y);
    const [cosine, sine] = angle % 90 === 0
        ? [[1, 0], [0, 1], [-1, 0], [0, -1]][((angle / 90) % 4 + 4) % 4]
        : [Math.cos(angle * Math.PI / 180), Math.sin(angle * Math.PI / 180)];
    const rw = Math.abs(cosine) * cw + Math.abs(sine) * ch;
    const rh = Math.abs(sine) * cw + Math.abs(cosine) * ch;
    const ratio = (fit === 'cover' ? Math.max : Math.min)(width / rw, height / rh);
    const legacy = JSON.stringify(f) === JSON.stringify(fields({}));
    const dw = legacy ? Math.max(1, round(rw * ratio)) : rw * ratio * scale;
    const dh = legacy ? Math.max(1, round(rh * ratio)) : rh * ratio * scale;
    const dx = legacy ? -Math.floor((dw - width) / 2) : (width - dw) / 2 + ox * width;
    const dy = legacy ? -Math.floor((dh - height) / 2) : (height - dh) / 2 + oy * height;
    const cx = dx + dw / 2, cy = dy + dh / 2, sx = dw / rw, sy = dh / rh;
    const fx = flip_h ? -1 : 1, fy = flip_v ? -1 : 1;
    const a = sx * cosine * fx, b = sy * sine * fx, c = -sx * sine * fy, d = sy * cosine * fy;
    const matrix = [a, b, c, d, cx - a * (x + cw / 2) - c * (y + ch / 2), cy - b * (x + cw / 2) - d * (y + ch / 2)];
    const ra = sx * cosine, rb = sy * sine, rc = -sx * sine, rd = sy * cosine;
    const raster_matrix = angle % 90 === 0 ? [sx, 0, 0, sy, dx, dy]
        : [ra, rb, rc, rd, cx - ra * cw / 2 - rc * ch / 2, cy - rb * cw / 2 - rd * ch / 2];
    const polygon = [[x, y], [x + cw, y], [x + cw, y + ch], [x, y + ch]].map(p => apply(matrix, p));
    const inv = inverse(matrix);
    const visible = clipPolygon(polygon, width, height).map(p => apply(inv, p))
        .map(p => [Math.min(x + cw, Math.max(x, p[0])), Math.min(y + ch, Math.max(y, p[1]))]);
    const covers = [[0, 0], [width, 0], [width, height], [0, height]].map(p => apply(inv, p))
        .every(p => p[0] >= x - 1e-7 && p[0] <= x + cw + 1e-7 && p[1] >= y - 1e-7 && p[1] <= y + ch + 1e-7);
    const cropped_fraction = Math.min(1, Math.max(0, 1 - area(visible) / (sw * sh)));
    const loss = Math.max(x, y, sw - x - cw, sh - y - ch,
        -Math.min(...polygon.map(p => p[0])), Math.max(...polygon.map(p => p[0])) - width,
        -Math.min(...polygon.map(p => p[1])), Math.max(...polygon.map(p => p[1])) - height);
    const vx = visible.length ? Math.min(...visible.map(p => p[0])) : x;
    const vy = visible.length ? Math.min(...visible.map(p => p[1])) : y;
    const vw = visible.length ? Math.max(...visible.map(p => p[0])) - vx : 0;
    const vh = visible.length ? Math.max(...visible.map(p => p[1])) - vy : 0;
    return {version: 1, source: {width: sw, height: sh}, output: {width, height}, fit,
        crop: {x, y, width: cw, height: ch}, flip_h, flip_v, rotate: angle,
        rotated: {width: rw, height: rh}, center: {x: cx, y: cy}, size: {width: dw, height: dh}, matrix, raster_matrix, legacy,
        covers, cropped: loss > 1 + 1e-7, cropped_fraction,
        visible: {x: vx, y: vy, width: vw, height: vh, polygon: visible}};
}

/** Independently mutable JSON plan. Normalized fields and sizes key a 256-entry LRU. */
export function plan(clip, sourceWidth, sourceHeight, width, height) {
    const sizes = [sourceWidth, sourceHeight, width, height].map(dimension), f = fields(clip);
    const key = JSON.stringify([f, ...sizes]);
    let result = cache.get(key);
    if (result) cache.delete(key);
    else result = build(f, ...sizes);
    cache.set(key, result);
    if (cache.size > 256) cache.delete(cache.keys().next().value);
    return JSON.parse(JSON.stringify(result));
}

/** More than one source pixel of explicit crop, or output pixel of overflow. */
export function isCropped(clip, sourceWidth, sourceHeight, width, height) {
    return plan(clip, sourceWidth, sourceHeight, width, height).cropped;
}

/** Styles for a video inside a positioned, overflow:hidden output viewport.
 * Uses standard clip-path:inset + 2D matrix (Chromium and Safari); no
 * object-view-box. Apply all returned styles. The element keeps its natural
 * source dimensions, so inset distances are source pixels before transform.
 * This helper doesn't draw a background: the contain preview has none.
 */
export function cssTransform(p, boxWidth, boxHeight) {
    if (![boxWidth, boxHeight].every(v => Number.isFinite(v) && v > 0)) throw new RangeError('Invalid preview size');
    const sx = boxWidth / p.output.width, sy = boxHeight / p.output.height;
    const [a, b, c, d, e, f] = p.matrix, crop = p.crop;
    const right = p.source.width - crop.x - crop.width, bottom = p.source.height - crop.y - crop.height;
    return {position: 'absolute', left: '0px', top: '0px', width: `${p.source.width}px`, height: `${p.source.height}px`,
        maxWidth: 'none', maxHeight: 'none', objectFit: 'fill', transformOrigin: '0 0',
        transform: `matrix(${[a * sx, b * sy, c * sx, d * sy, e * sx, f * sy].join(',')})`,
        clipPath: `inset(${crop.y}px ${right}px ${bottom}px ${crop.x}px)`};
}

/** Original-source pixel rectangle enclosing all retained pixels, with exact
 * polygon for rotated cuts. Use polygon to dim the outside in the full-source
 * preview; a rectangle alone cannot represent arbitrary-angle export crops.
 * An empty polygon means the foreground is entirely outside the export.
 */
export function visibleRect(p) {
    return JSON.parse(JSON.stringify(p.visible));
}
