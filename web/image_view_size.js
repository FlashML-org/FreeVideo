// Picture size: any area inside the model's range at the chosen shape, in steps of 32 pixels. "Standard" and
// "High" are two quick points on the scale, not the only choices. The engine checks width and height, not tiers:
// H3 Image takes 4 to 16 MP (each side 256 to 8192), Qwen-Image 2.1 and MOSS-Image take each side 256 to 2048.
import { createFrameChip } from './glass/frame_picker.js';
import { el, t, tip } from './image_view_ui.js';

export const SNAP = 32;
export const MP = 1e6;
export const H3 = 'minimax-h3-image', QWEN = 'qwen-image-2.1-turbo', MOSS = 'moss-image-sft';
export const RANGES = Object.freeze({
    [H3]: Object.freeze({minArea: 2048 * 2048, maxArea: 4096 * 4096, minSide: 256, maxSide: 8192, step: .5 * MP,
        presets: Object.freeze({standard: 2048 * 2048, high: 4096 * 4096})}),
    [QWEN]: Object.freeze({minArea: 0, maxArea: Infinity, minSide: 256, maxSide: 2048, step: .1 * MP,
        presets: Object.freeze({standard: 1024 * 1024, high: 1280 * 1280})}),
    [MOSS]: Object.freeze({minArea: 0, maxArea: 2048 * 2048, minSide: 256, maxSide: 2048, step: .1 * MP,
        presets: Object.freeze({standard: 1024 * 1024, high: 2048 * 2048})}),
});
export const RATIOS = Object.freeze(['1:1', '4:3', '3:4', '16:9', '9:16', '3:2', '2:3']);
const rangeOf = model => RANGES[model] || RANGES[QWEN];

// A shape as width over height: '16:9', {width, height}, or a number.
export function ratioValue(ratio) {
    if (typeof ratio === 'number' && ratio > 0) return ratio;
    if (ratio && typeof ratio === 'object' && ratio.width > 0 && ratio.height > 0) return ratio.width / ratio.height;
    const [w, h] = String(ratio || '1:1').split(':').map(Number);
    return w > 0 && h > 0 ? w / h : 1;
}

// The areas this model accepts at this shape: its own area limits, narrowed by the side limits.
export function areaRange(model, ratio) {
    const range = rangeOf(model), r = ratioValue(ratio), long = Math.max(r, 1 / r);
    return {min: Math.max(range.minArea, range.minSide ** 2 * long), max: Math.min(range.maxArea, range.maxSide ** 2 / long)};
}

const snap = (value, mode = 'round') => Math.max(SNAP, Math[mode](value / SNAP) * SNAP);
const clampSide = (value, range) => Math.min(Math.floor(range.maxSide / SNAP) * SNAP, Math.max(Math.ceil(range.minSide / SNAP) * SNAP, value));

// Width and height at about this area and shape, each a multiple of 32, inside the model's limits.
export function sizeFor(model, ratio, area) {
    const range = rangeOf(model), r = ratioValue(ratio), limits = areaRange(model, r);
    const target = Math.min(limits.max, Math.max(limits.min, Number(area) || range.presets.standard));
    const w0 = Math.sqrt(target * r), h0 = w0 / r;
    let width = clampSide(snap(w0), range), height = clampSide(snap(h0), range);
    // Rounding may step outside the area limits; go the other way on the side that moved most.
    if (width * height < limits.min) { width = clampSide(snap(w0, 'ceil'), range); height = clampSide(snap(h0, 'ceil'), range); }
    if (width * height > limits.max) { width = clampSide(snap(w0, 'floor'), range); height = clampSide(snap(h0, 'floor'), range); }
    return {width, height};
}

// A typed width or height: the other side follows the shape when it is locked, otherwise the shape follows.
export function sizeFromSide(model, {width, height}, side, value, locked, ratio) {
    const range = rangeOf(model); const typed = clampSide(snap(Number(value) || 0), range);
    if (!locked) {
        let next = side === 'width' ? {width: typed, height} : {width, height: typed};
        const limits = areaRange(model, next.width / next.height), area = next.width * next.height;
        if (area > limits.max || area < limits.min) next = sizeFor(model, next.width / next.height, area);
        return next;
    }
    const r = ratioValue(ratio);
    const area = side === 'width' ? typed * typed / r : typed * typed * r;
    const exact = side === 'width' ? {width: typed, height: clampSide(snap(typed / r), range)} : {width: clampSide(snap(typed * r), range), height: typed};
    const limits = areaRange(model, r);
    return area >= limits.min && area <= limits.max && exact.width * exact.height >= limits.min * .97 && exact.width * exact.height <= limits.max ? exact : sizeFor(model, r, area);
}

// The nearest step on the scale, with the two ends catching within half a step.
export function snapArea(model, ratio, area) {
    const range = rangeOf(model), limits = areaRange(model, ratio), half = range.step / 2;
    if (area <= limits.min + half) return limits.min;
    if (area >= limits.max - half) return limits.max;
    return Math.min(limits.max, Math.max(limits.min, Math.round(area / range.step) * range.step));
}

export const megapixels = area => (area / MP).toFixed(area >= MP ? 1 : 2);
export const sizeText = ({width, height}) => `${width} × ${height}`;

// Which quick point an area is at, if any: within 2% counts.
export function presetAt(model, area) {
    const presets = rangeOf(model).presets;
    return Object.keys(presets).find(name => Math.abs(presets[name] - area) / presets[name] < .02) || null;
}

// What this machine has shown it can make: the engine's own tier list says whether the high size is offered,
// and a run the engine had to shrink says the area it fell back to. The smaller of the two is the line drawn.
export function machineLimit(model, info, fittedArea = null) {
    const presets = rangeOf(model).presets; let limit = Infinity, why = '';
    if (Array.isArray(info?.sizes) && !info.sizes.includes('high')) { limit = presets.standard; why = 'card'; }
    if (Number.isFinite(fittedArea) && fittedArea > 0 && fittedArea < limit) { limit = fittedArea; why = 'fitted'; }
    return {area: limit, why};
}

// A rough time from this machine's own recent runs: seconds per megapixel, averaged, times the area.
export function estimateSeconds(samples, model, area) {
    const mine = (samples || []).filter(s => s && s.model === model && s.area > 0 && s.seconds > 0 && !s.references).slice(-6);
    if (!mine.length) return null;
    const rate = mine.reduce((sum, s) => sum + s.seconds / (s.area / MP), 0) / mine.length;
    return Math.max(1, Math.round(rate * area / MP));
}


// The control: the shared frame and size picker (glass/frame_picker.js), everything in sight. Frame: every shape and
// Image 1 as tiles. Size: Standard and High with their final pixels at the shape, then width × height and the shape lock.
// The page owns the state: the picker reports a choice, the page applies it and paints it back. Pointing at an
// option asks the page to show it (onPreview: the next picture's empty frame); nothing is drawn over a work.
// The picker counts pixels in units of 1024²; the page counts areas in pixels.
const PX = 1024 ** 2;
const SHAPE_ORDER = ['1:1', '16:9', '9:16', '4:3', '3:4', '3:2', '2:3'];
const SHAPE_WORDS = {'1:1': ['Square', '方形'], '16:9': ['Wide', '横屏'], '9:16': ['Tall', '竖屏'], '4:3': ['Landscape', '横向'], '3:4': ['Portrait', '纵向'],
    '3:2': ['Photo, wide', '照片横'], '2:3': ['Photo, tall', '照片竖']};
export function sizePicker({onChange, onPreview = () => {}}) {
    const root = el('div', 'fv-iv-size');
    let picker = null, key = '', view = null;
    const presetIn = (model, pixels) => Object.entries(rangeOf(model).presets).find(([, area]) => Math.abs(area / PX - pixels) < .005)?.[1];
    function handle(change) {
        if (!view) return;
        if (change.source === 'shape') onChange({ratio: change.shape === 'custom' ? view.custom : change.shape});
        else if (change.source === 'size') { const area = presetIn(view.model, change.pixels); if (area) onChange({area, from: 'preset'}); }
        else if (change.source === 'slider') onChange({area: snapArea(view.model, view.ratioValue, change.pixels * PX), from: 'slider'});
        else if (change.source === 'dimensions') onChange({dims: {width: change.width, height: change.height}});
        else if (change.source === 'lock') onChange({locked: change.locked});
    }
    // Image 1 and the custom shape follow something (the picture, what was typed): the frame shown for them is theirs.
    function preview(frame) {
        if (frame && view) {
            const id = picker?.picker.parts.frame.querySelector('button[data-shape]:is(:hover,:focus-visible)')?.dataset.shape;
            const r = id === 'same' ? view.ref?.ratio : id === 'custom' && view.custom ? ratioValue(view.custom) : null;
            if (r) frame = {ratio: r, ...sizeFor(view.model, r, view.area)};
        }
        onPreview(frame);
    }
    // Shapes and sizes are fixed when a picker is made: another model, another first reference or another custom
    // shape makes a new one in the same place. An open "Custom size" and the focus inside it carry over.
    function build(next) {
        const range = rangeOf(next.model), limits = areaRange(next.model, next.ratioValue);
        const shapes = SHAPE_ORDER.map(r => ({id: r, label: t(...SHAPE_WORDS[r]), ratio: ratioValue(r)}));
        // Match image (the first reference picture) is always listed (greyed, with the reason, while there is no reference picture); a typed width × height
        // is the custom shape, so it has no tile of its own.
        shapes.push({id: 'same', label: t('Match image', '跟随图片'), ratio: null, thumb: next.ref?.url});
        // The two quick sizes, named by their megapixels (4M, 16M) as in the creation panel.
        const sizes = ['standard', 'high'].map(name => ({pixels: range.presets[name] / PX}));
        // The panel opens upwards, so it never covers Generate under the column.
        const made = createFrameChip({t, placement: 'above', shapes, sizes, sizeFor: (ratio, pixels) => sizeFor(next.model, ratio, pixels * PX),
            custom: {min: limits.min / PX, max: limits.max / PX, step: range.step / PX, minSide: range.minSide, maxSide: range.maxSide, snap: SNAP, lock: true},
            onChange: handle, onPreview: preview});
        const all = sel => [...made.pop.querySelectorAll(sel)];
        for (const b of all('button[data-shape]')) {
            const id = b.dataset.shape;
            if (id === 'same') tip(b, t('Match image', '跟随图片'), t('Match the first reference picture', '画幅跟第一张参考图一致'));
            else if (id === 'custom') tip(b, t('Custom shape', '自定义画幅'), t('The width and height typed last', '上次输入的宽和高'));
            else tip(b, `${t(...SHAPE_WORDS[id])} ${id}`, t('Width to height of the picture', '画面的宽高比例'));
        }
        const [frameMore, sizeMore] = all('.fvp-more-button');
        const q = sel => all(sel)[0];
        if (frameMore) tip(frameMore, t('More shapes', '更多画幅'), next.ref ? t('Other shapes and Image 1', '其他比例和“同图 1”') : t('Other shapes', '其他比例'));
        if (sizeMore) tip(sizeMore, t('More sizes', '更多尺寸'), t('Any size between the two ends', '两端之间的任意大小'));
        if (q('.fvp-custom-item')) tip(q('.fvp-custom-item'), t('Custom size', '自定义尺寸'), t('Drag, or type width × height', '拖动，或输入宽 × 高'));
        if (q('.fvp-slider')) tip(q('.fvp-slider'), t('Picture size', '画面大小'), t('Drag to set the pixel count', '拖动选择像素数，宽高随之变化'), '← →');
        const [width, height] = all('.fvp-dim');
        if (width) tip(width, t('Width', '宽度'), t('Type a width; snaps to 32', '输入宽度，自动对齐到 32 的倍数'));
        if (height) tip(height, t('Height', '高度'), t('Type a height; snaps to 32', '输入高度，自动对齐到 32 的倍数'));
        if (q('.fvp-lock')) tip(q('.fvp-lock'), t('Shape lock', '比例锁定'), t('Locked: typing one side changes the other', '锁定时改一边，另一边按比例跟着变'));
        if (picker) picker.dispose();
        picker = made; root.replaceChildren(made.element);
        return null;
    }
    // view: {model, ratio, ref: {url, ratio} | null, custom: 'w:h' | null, area, size, locked, limit, ratioValue}
    function paint(next) {
        view = next;
        const nextKey = [next.model, next.ref ? `${next.ref.url}@${next.ref.ratio.toFixed(4)}` : ''].join('|');
        let refocus = null;
        if (nextKey !== key || !picker) { key = nextKey; refocus = build(next); }
        const range = rangeOf(next.model), limits = areaRange(next.model, next.ratioValue);
        const shape = next.ratio === 'same' ? 'same' : SHAPE_ORDER.includes(next.ratio) ? next.ratio : 'custom';
        const limitArea = next.limit?.area, capped = Number.isFinite(limitArea) && limitArea < limits.max;
        // The line marks what this computer has shown it can make (none when that is the smallest size); the words
        // for a size above it are in the status line over Generate, not repeated here.
        picker.set({shape, ratio: next.ratioValue, pixels: next.area / PX, width: next.size.width, height: next.size.height, locked: next.locked,
            range: {min: limits.min / PX, max: limits.max / PX, step: range.step / PX},
            unavailable: {same: next.ref ? '' : t('Add a reference picture first', '先添加参考图')},
            limit: capped && limitArea > limits.min * 1.01 ? {pixels: limitArea / PX, note: ''} : null});
        // Each quick size says what it makes at this shape.
        for (const b of picker.picker.parts.size.querySelectorAll('button[data-pixels]')) {
            const area = presetIn(next.model, Number(b.dataset.pixels)), made = area ? sizeText(sizeFor(next.model, next.ratioValue, area)) : '';
            tip(b, b.querySelector('.fvp-sub')?.textContent || '', t(`Quick size: ${made} at this shape`, `快捷尺寸：当前画幅下 ${made}`));
        }
        refocus?.();
    }
    return {element: root, paint, dispose() { picker?.dispose(); }};
}
