// Image editor for media cards: layers, selections, retouching, text, shapes,
// colour, crop and canvas size, in the spirit of a desktop photo editor and in
// FreeVideo's own look. The original file is never changed: saving renders a
// new image and keeps the document, so the next visit starts from the original
// with every layer and step in place. Closing frees every canvas it made.
import {BLENDS, uid, emptyAdjust, migrate, orientedSize, orientMatrix, canvasRect, layerMatrix, resizeDocument, scaleSelection,
    paintLayer, rasterKinds, measureText, Renderer} from './image_editor_engine.js';
import {FONT_KEYS, fontStack, fontLoaded, loadFont, loadFonts} from './fonts.js';

const css = document.createElement('link');
css.rel = 'stylesheet'; css.href = new URL('./image_editor.css', import.meta.url).href;
document.head.append(css);

const RATIOS = [['video', null], ['original', null], ['free', null], ['16:9', 16 / 9], ['9:16', 9 / 16], ['1:1', 1], ['4:3', 4 / 3], ['3:4', 3 / 4]];
const HISTORY = 80;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const SVG = (body, size = 18) => `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
// Line icons on a 24-unit grid; eyedropper, hand, crop, link and unlink follow Lucide (ISC, see THIRD_PARTY_NOTICES.md).
export const ICONS = {
    move: '<path d="M12 3v18M3 12h18"/><path d="m9 6 3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/>',
    crop: '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M2 6h14a2 2 0 0 1 2 2v14"/>',
    marquee: '<rect x="4" y="5" width="16" height="14" rx="1" stroke-dasharray="2.6 2.4"/>',
    ellipse: '<ellipse cx="12" cy="12" rx="9" ry="7" stroke-dasharray="2.6 2.4"/>',
    lasso: '<path d="M7 17c-3-1.5-4-4-3-6.5C5.5 6.5 10 4.5 14.5 5S21 8.5 20 12s-6 5-10 4.5"/><path d="M7 17c0 2 1.5 3.5 3.5 3"/><circle cx="7" cy="17" r="1.6"/>',
    polygon: '<path d="M5 18 4 8l8-4 8 6-4 9z" stroke-dasharray="2.6 2.4"/>',
    wand: '<path d="m4 20 11-11"/><path d="M15 4v2M18.5 5.5l-1.4 1.4M20 9h-2M18.5 12.5l-1.4-1.4M12 5.5l1.4 1.4"/>',
    brush: '<path d="M18.4 3.6a2 2 0 0 1 2.8 2.8L11 16.6 7.4 13z"/><path d="M7.4 13c-2.6 0-4 1.8-4 4.2 0 1.6-.6 2.6-1.4 3.2 4.6.6 8.6-.6 9-4.2"/>',
    eraser: '<path d="m7 21-4-4a2 2 0 0 1 0-2.8L13.2 4a2 2 0 0 1 2.8 0l4 4a2 2 0 0 1 0 2.8L10 21z"/><path d="M21 21H7M8.5 9.5l6 6"/>',
    heal: '<rect x="2.8" y="8.2" width="18.4" height="7.6" rx="3.8" transform="rotate(-45 12 12)"/><path d="M10.5 10.5h.01M13.5 13.5h.01M10.5 13.5h.01M13.5 10.5h.01"/>',
    clone: '<path d="M9 3h6v4a3 3 0 0 1-1 2.2L13 10v3h-2v-3l-1-.8A3 3 0 0 1 9 7z"/><path d="M5 13h14v4H5zM7 21h10"/>',
    blur: '<path d="M12 3s6 6.6 6 11a6 6 0 0 1-12 0c0-4.4 6-11 6-11z"/>',
    gradient: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M11 4v16" opacity=".55"/><path d="M15 4v16" opacity=".3"/>',
    text: '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
    shape: '<rect x="3" y="11" width="9" height="9" rx="1"/><circle cx="15.5" cy="8.5" r="5.5"/>',
    eyedropper: '<path d="m2 22 1-1h3l9-9"/><path d="M3 21v-3l9-9"/><path d="m15 6 3.4-3.4a2.1 2.1 0 1 1 3 3L18 9l.4.4a2.1 2.1 0 1 1-3 3l-3.8-3.8a2.1 2.1 0 1 1 3-3z"/>',
    hand: '<path d="M18 11V6a2 2 0 0 0-4 0v5M14 10V4a2 2 0 0 0-4 0v6M10 10.5V6a2 2 0 0 0-4 0v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.9-6-2.4l-3.6-3.6a2 2 0 0 1 2.8-2.8L7 15"/>',
    undo: '<path d="M8 4 3 9l5 5"/><path d="M3 9h11.5a5.5 5.5 0 0 1 0 11H10"/>',
    redo: '<path d="m16 4 5 5-5 5"/><path d="M21 9H9.5a5.5 5.5 0 0 0 0 11H14"/>',
    ccw: '<path d="M4 12a8 8 0 1 0 2.34-5.66"/><path d="M4 4v5h5"/>',
    cw: '<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/>',
    flipX: '<path d="M12 3v18" stroke-dasharray="2 2.5"/><path d="M9.5 7v10L3.5 12z"/><path d="M14.5 7v10l6-5z"/>',
    flipY: '<path d="M3 12h18" stroke-dasharray="2 2.5"/><path d="M7 9.5h10l-5-6z"/><path d="M7 14.5h10l-5 6z"/>',
    eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="M3 3l18 18M10.6 5.1A10.4 10.4 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.1M6.6 6.6C3.9 8.4 2 12 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    unlink: '<path d="m18.84 12.25 1.72-1.71h-.02a5 5 0 0 0-.12-7.07 5 5 0 0 0-6.95 0l-1.72 1.71"/><path d="m5.17 11.75-1.71 1.71a5 5 0 0 0 .12 7.07 5 5 0 0 0 6.95 0l1.71-1.71"/><path d="M8 2v3M2 8h3M16 19v3M19 16h3"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5L5 20"/>',
    copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
    trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>',
    up: '<path d="m6 14 6-6 6 6"/>',
    down: '<path d="m6 10 6 6 6-6"/>',
    mask: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="12" cy="12" r="4.5" fill="currentColor" stroke="none" opacity=".75"/>',
    swap: '<path d="M7 4 4 7l3 3"/><path d="M4 7h11a5 5 0 0 1 5 5v1"/><path d="m17 20 3-3-3-3"/>',
    minus: '<path d="M5 12h14"/>',
    edit: '<path d="M4 20h4L18.5 9.5a2.1 2.1 0 0 0-3-3L5 17z"/><path d="m14 8 2 2"/>',
};
export const icon = (name, size = 18) => SVG(ICONS[name], size);

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
}
function button(label, title, action, className = 'fv-ie-button') {
    const b = el('button', label, className); b.type = 'button';
    // A visible label is the accessible name; the title only adds the hint.
    if (title) { b.title = title; if (!label) b.setAttribute('aria-label', title); }
    if (action) b.onclick = action;
    return b;
}
function iconButton(name, title, action, className = 'fv-ie-icon') {
    const b = button('', title, action, className); b.innerHTML = SVG(ICONS[name]); return b;
}
function paint(input) {
    const min = Number(input.min), max = Number(input.max), value = Number(input.value);
    const p = (value - min) / (max - min), z = min < 0 && max > 0 ? -min / (max - min) : 0;
    input.style.setProperty('--fv-lo', Math.min(p, z));
    input.style.setProperty('--fv-hi', Math.max(p, z));
    // A one-way slider fills from its left end, not from under the thumb's centre.
    input.style.setProperty('--fv-two', z > 0 ? 1 : 0);
}
function slider(id, label, min, max, step, value, onInput, onChange, unit = '') {
    const row = el('div', null, 'fv-ie-slider');
    const input = el('input'); Object.assign(input, {type: 'range', min, max, step, value, id});
    input.classList.add('fv-ie-range'); paint(input);
    const lab = el('label', label); lab.htmlFor = id;
    const out = el('output', String(value) + unit); out.htmlFor = id;
    input.oninput = () => { paint(input); out.textContent = input.value + unit; onInput(Number(input.value)); };
    input.onchange = () => onChange?.(Number(input.value));
    // Double-click a slider to put it back to zero.
    input.ondblclick = () => { input.value = String(clamp(0, min, max)); input.oninput(); input.onchange(); };
    row.append(lab, out, input);
    return {row, input, out};
}
function segmented(options, value, onPick, label) {
    const group = el('div', null, 'fv-ie-seg'); group.setAttribute('role', 'group');
    if (label) group.setAttribute('aria-label', label);
    const buttons = options.map(([id, text, icon]) => {
        const b = icon ? iconButton(icon, text, null, 'fv-ie-seg-item fv-ie-seg-icon') : button(text, null, null, 'fv-ie-seg-item');
        b.dataset.value = id; group.append(b);
        b.onclick = () => { set(id); onPick(id); };
        return b;
    });
    const set = v => buttons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.value === v)));
    set(value);
    return {group, set};
}
function loadImage(src) {
    return new Promise((resolve, reject) => {
        const image = new Image(); image.onload = () => resolve(image); image.onerror = () => reject(new Error('load')); image.src = src;
    });
}
const strip = d => JSON.stringify({...d, active: null, layers: d.layers.map(l => ({...l, name: ''}))});

/**
 * Open the editor over everything else.
 * Resolves to {blob, name, edit, width, height} on Save, {restore: true} when
 * every change was undone, or null when the person leaves without saving.
 *   source: original image file; resolve(file) → URL; upload(file) → {file};
 *   target: {width, height} of the video, if known; library: [{file, label}] other media images.
 */
export async function openImageEditor({source, name, target = null, edit = null, role = 'reference', t, resolve,
                                       upload = null, library = [], tool: startTool = null}) {
    const baseImage = await loadImage(resolve(source));
    const renderer = new Renderer(resolve);
    const fresh = !edit;
    const original = () => { const d = migrate(null, source, baseImage); d.layers[0].name = t('Original', '原图'); return d; };
    let doc = edit ? migrate(edit, source, baseImage) : original();
    doc = {...doc, layers: doc.layers.map(l => l.id === 'base' ? {...l, name: t('Original', '原图')} : l)};
    if (fresh && target && role !== 'reference') {
        // A keyframe becomes the first or last frame of the video: start from its shape.
        doc = {...doc, ratio: 'video', crop: fitRect(target.width / target.height, canvasRect(doc))};
    }
    // Text draws with the fonts that ship with FreeVideo; the text tool starts in bold sans.
    await Promise.all([renderer.preload(doc), loadFont('sans', 700)]);
    // A text box measured with other fonts (or by another renderer) fits its text again,
    // keeping its place and scale. It is part of opening, not a change to undo or save.
    doc = {...doc, layers: doc.layers.map(l => {
        if (l.kind !== 'text') return l;
        const size = measureText(l.text);
        if (Math.abs(size.nw - l.nw) <= 1 && Math.abs(size.nh - l.nh) <= 1) return l;
        const sx = l.w / l.nw, sy = l.h / l.nh;
        return {...l, ...size, w: size.nw * sx, h: size.nh * sy};
    })};
    const initial = doc;
    let history = [doc], future = [];
    let selection = null;            // {parts, feather, invert}; not part of the saved document
    let maskTarget = false;          // painting goes to the active layer's mask
    let fg = '#ffffff', bg = '#111111';
    let healing = null;              // a heal stroke about to be applied; Save and leaving wait for it
    let tool = 'move';
    const opts = {
        marquee: {shape: 'rect', mode: 'replace', feather: 0}, lasso: {type: 'free', mode: 'replace', feather: 0},
        wand: {tolerance: 24, contiguous: true, mode: 'replace'},
        brush: {size: .02, hardness: .85, opacity: 1}, eraser: {size: .035, hardness: .7, opacity: 1},
        heal: {size: .025, hardness: .6, text: false}, clone: {size: .035, hardness: .6, opacity: 1, source: null, offset: null, picking: false},
        blur: {size: .05, hardness: .4, strength: .6}, gradient: {shape: 'linear', end: 'bg'},
        text: {font: 'sans', weight: 700, align: 'left', outline: false, shadow: true},
        shape: {type: 'rect', width: 6},
        move: {auto: true},
    };
    const extension = /\.jpe?g$/i.test(name) ? 'jpg' : 'png';
    const stem = name.split('/').pop().replace(/\.[^.]+$/, '').replace(/-edit$/, '');

    // ----- frame --------------------------------------------------------------
    const dialog = el('dialog', null, 'fv-image-editor');
    dialog.setAttribute('aria-label', t('Edit image', '编辑素材图'));
    const header = el('header', null, 'fv-ie-header');
    const title = el('div', null, 'fv-ie-title');
    // The picture's size; clicking it opens Image size.
    const sizeLabel = el('button', '', 'fv-ie-size'); sizeLabel.type = 'button';
    sizeLabel.title = t('Image size…', '图像大小…'); sizeLabel.onclick = () => imageSizeDialog();
    title.append(el('strong', t('Edit image', '编辑素材图')), el('span', name.split('/').pop(), 'fv-ie-file'), sizeLabel);
    const zoomLabel = button('100%', t('Fit to window (Ctrl+0)', '适合窗口（Ctrl+0）'), () => fit(), 'fv-ie-zoom-label');
    const zoom = el('div', null, 'fv-ie-zoom');
    zoom.append(iconButton('minus', t('Zoom out (Ctrl −)', '缩小（Ctrl −）'), () => zoomBy(1 / 1.25)), zoomLabel,
        iconButton('plus', t('Zoom in (Ctrl +)', '放大（Ctrl +）'), () => zoomBy(1.25)));
    const undo = iconButton('undo', t('Undo (Ctrl+Z)', '撤销（Ctrl+Z）'), () => step(-1));
    const redo = iconButton('redo', t('Redo (Ctrl+Shift+Z)', '重做（Ctrl+Shift+Z）'), () => step(1));
    const reset = button(t('Reset', '全部重置'), t('Back to the original image', '回到原图，清除全部修改'), () => { deselectQuiet(); const next = original(); followSize(doc, next); commit(next); }, 'fv-ie-button fv-ie-reset');
    const cancel = button(t('Cancel', '取消'), null, () => leave());
    const save = button(t('Save', '保存'), t('Save (Ctrl+Enter)', '保存（Ctrl+Enter）'), () => finish(), 'fv-ie-button fv-ie-primary');
    const actions = el('div', null, 'fv-ie-actions'); actions.append(undo, redo, reset, cancel, save);
    header.append(title, zoom, actions);

    const main = el('div', null, 'fv-ie-main');
    const toolbar = el('nav', null, 'fv-ie-toolbar'); toolbar.setAttribute('aria-label', t('Tools', '工具'));
    const workspace = el('div', null, 'fv-ie-workspace');
    const optionsBar = el('div', null, 'fv-ie-options');
    const stage = el('div', null, 'fv-ie-stage'); stage.tabIndex = -1;
    const view = el('canvas', null, 'fv-ie-view');
    const ants = el('canvas', null, 'fv-ie-ants');
    const overlay = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); overlay.classList.add('fv-ie-overlay');
    const hint = el('div', '', 'fv-ie-hint'); hint.hidden = true;
    stage.append(view, ants, overlay, hint);
    workspace.append(optionsBar, stage);
    const panel = el('aside', null, 'fv-ie-panel');
    const props = el('section', null, 'fv-ie-props');
    const layersBox = el('section', null, 'fv-ie-layers');
    panel.append(props, layersBox);
    main.append(toolbar, workspace, panel);
    const confirmBar = el('div', null, 'fv-ie-confirm'); confirmBar.hidden = true;
    confirmBar.append(el('span', t('Leave without saving? Your changes to this image will be lost.', '不保存就离开？对这张图的修改会丢失。')),
        button(t('Discard', '放弃修改'), null, () => close(null), 'fv-ie-button fv-ie-danger'),
        button(t('Keep editing', '继续编辑'), null, () => { confirmBar.hidden = true; save.focus(); }));
    const status = el('div', '', 'fv-ie-status'); status.setAttribute('role', 'status');
    const filePicker = el('input'); filePicker.type = 'file'; filePicker.accept = 'image/*'; filePicker.hidden = true;
    dialog.append(header, main, confirmBar, status, filePicker);
    document.body.append(dialog);

    // ----- tools ----------------------------------------------------------------
    const TOOLS = [
        ['move', t('Move', '移动'), 'V'], ['crop', t('Crop and canvas', '裁剪与画布'), 'C'], null,
        ['marquee', t('Marquee', '选框'), 'M'], ['lasso', t('Lasso', '套索'), 'L'], ['wand', t('Magic wand', '魔棒'), 'W'], null,
        ['brush', t('Brush', '画笔'), 'B'], ['eraser', t('Eraser', '橡皮擦'), 'E'], ['heal', t('Spot healing', '污点修复'), 'J'],
        ['clone', t('Clone stamp', '仿制图章'), 'S'], ['blur', t('Blur', '模糊'), 'R'], ['gradient', t('Gradient', '渐变'), 'G'], null,
        ['text', t('Text', '文字'), 'T'], ['shape', t('Shape', '形状'), 'U'], ['eyedropper', t('Eyedropper', '吸管'), 'I'], ['hand', t('Hand', '抓手'), 'H'],
    ];
    const toolButtons = {};
    for (const entry of TOOLS) {
        if (!entry) { toolbar.append(el('span', null, 'fv-ie-tool-gap')); continue; }
        const [id, label, key] = entry;
        const b = iconButton(id === 'marquee' ? 'marquee' : id, `${label} (${key})`, () => select(id), 'fv-ie-tool');
        b.dataset.tool = id; toolButtons[id] = b; toolbar.append(b);
    }
    const colors = el('div', null, 'fv-ie-colors');
    const fgInput = el('input'); fgInput.type = 'color'; fgInput.className = 'fv-ie-fg'; fgInput.value = fg;
    fgInput.title = t('Foreground color', '前景色'); fgInput.setAttribute('aria-label', fgInput.title);
    const bgInput = el('input'); bgInput.type = 'color'; bgInput.className = 'fv-ie-bg'; bgInput.value = bg;
    bgInput.title = t('Background color', '背景色'); bgInput.setAttribute('aria-label', bgInput.title);
    fgInput.oninput = () => { fg = fgInput.value; };
    bgInput.oninput = () => { bg = bgInput.value; };
    colors.append(bgInput, fgInput, iconButton('swap', t('Swap colors (X)', '交换前景色和背景色（X）'), () => swapColors(), 'fv-ie-swap'));
    toolbar.append(el('span', null, 'fv-ie-tool-fill'), colors);
    function swapColors() { [fg, bg] = [bg, fg]; fgInput.value = fg; bgInput.value = bg; }

    // ----- geometry ---------------------------------------------------------------
    let viewState = {zoom: 1, panX: 0, panY: 0};
    let layout = {w: 1, h: 1, k: 1, fitK: 1, ox: 0, oy: 0, dpr: 1};
    const rect = () => canvasRect(doc);
    function computeLayout() {
        // Layout size, not the on-screen box: the dialog's opening animation scales it slightly.
        const box = {width: stage.clientWidth, height: stage.clientHeight}, r = rect(), pad = 56;
        const fitK = Math.max(.001, Math.min((box.width - pad) / r.w, (box.height - pad) / r.h));
        const k = fitK * viewState.zoom;
        layout = {w: Math.max(1, box.width), h: Math.max(1, box.height), k, fitK, dpr: Math.min(window.devicePixelRatio || 1, 2),
            ox: (box.width - r.w * k) / 2 + viewState.panX - r.x * k, oy: (box.height - r.h * k) / 2 + viewState.panY - r.y * k};
    }
    // Screen ↔ oriented ↔ document ↔ layer fractions.
    const toO = (sx, sy) => [(sx - layout.ox) / layout.k, (sy - layout.oy) / layout.k];
    const fromO = (x, y) => [layout.ox + x * layout.k, layout.oy + y * layout.k];
    const toD = ([x, y]) => { const p = orientMatrix(doc).inverse().transformPoint(new DOMPoint(x, y)); return [p.x, p.y]; };
    const fromD = ([x, y]) => { const p = orientMatrix(doc).transformPoint(new DOMPoint(x, y)); return [p.x, p.y]; };
    const toLocal = (layer, [x, y]) => { const p = layerMatrix(layer).inverse().transformPoint(new DOMPoint(x, y)); return [p.x / layer.nw, p.y / layer.nh]; };
    const pointer = event => {
        const box = stage.getBoundingClientRect(), sx = stage.clientWidth / (box.width || 1), sy = stage.clientHeight / (box.height || 1);
        return [(event.clientX - box.left) * sx, (event.clientY - box.top) * sy];
    };
    function fitRect(aspect, r) {
        if (!aspect) return {...r};
        let w = r.w, h = r.w / aspect;
        if (h > r.h) { h = r.h; w = h * aspect; }
        return {x: r.x + (r.w - w) / 2, y: r.y + (r.h - h) / 2, w, h};
    }
    const active = () => doc.layers.find(l => l.id === doc.active) || doc.layers[0];
    const replaceLayer = (id, patch) => ({...doc, layers: doc.layers.map(l => l.id === id ? {...l, ...(typeof patch === 'function' ? patch(l) : patch)} : l)});
    // A new layer that reads upright on screen whatever the canvas orientation;
    // angle is its tilt as seen on screen.
    function upright(w, h, angle = 0) {
        const mirrored = doc.flipX !== doc.flipY;
        return {w, h, rotation: (mirrored ? -angle : angle) - doc.rotate, flipX: doc.flipX, flipY: doc.flipY};
    }
    // Brush sizes are fractions of the picture's shorter side, in document pixels.
    const brushPixels = size => size * Math.min(doc.width, doc.height);

    // ----- history ------------------------------------------------------------------
    // panels: true redraws the side panels and options; 'layers' only the layer list.
    function commit(next, panels = true) {
        // The same state twice (a slider that reports its change again) is one step.
        const last = history[history.length - 1];
        if (next !== last && JSON.stringify(next) === JSON.stringify(last)) { doc = last; refresh(panels); return; }
        doc = next;
        if (next === last) { refresh(panels); return; }
        history.push(doc); if (history.length > HISTORY) history.shift();
        future = [];
        refresh(panels);
    }
    // A step being dragged: drawn, not yet in the history.
    function preview(next) { doc = next; refresh(false); }
    function step(direction) {
        const before = doc;
        if (direction < 0 && history.length > 1) { future.push(history.pop()); doc = history[history.length - 1]; }
        else if (direction > 0 && future.length) { history.push(future.pop()); doc = history[history.length - 1]; }
        else return;
        followSize(before, doc);
        if (!doc.layers.some(l => l.id === doc.active)) doc = {...doc, active: doc.layers[doc.layers.length - 1].id};
        if (maskTarget && !active().mask) maskTarget = false;
        refresh(true);
    }
    const changed = () => history.length > 1 && strip(doc) !== strip(initial);
    async function refresh(panels) {
        if (doc.layers.some(l => l.src && !renderer.loaded(l.src))) await renderer.preload(doc);
        computeLayout(); draw(); controls();
        if (panels === 'layers') renderLayers();
        else if (panels) { renderPanels(); renderOptions(); keepFocus(); }
    }
    // Re-rendered controls can take the focused button with them: keep keys in the editor.
    function keepFocus() {
        if (dialog.open && !dialog.contains(document.activeElement)) stage.focus({preventScroll: true});
    }

    // ----- drawing ------------------------------------------------------------------
    let composited = null;       // {scale, canvas, doc}
    let liveStroke = null;       // stroke in progress, drawn over the picture
    function previewScale() {
        const want = Math.min(1, layout.k * layout.dpr);
        let s = 1; while (s / 2 >= want && s > 1 / 64) s /= 2;
        return s;
    }
    function picture() {
        const s = previewScale();
        if (!composited || composited.scale !== s || composited.doc !== doc) {
            if (composited && composited.scale !== s) renderer.prune(renderer.scales(doc, s));
            composited = {scale: s, doc, canvas: renderer.composite(doc, s)};
        }
        return composited;
    }
    function draw() {
        const ctx = view.getContext('2d'), dpr = layout.dpr;
        const w = Math.round(layout.w * dpr), h = Math.round(layout.h * dpr);
        if (view.width !== w || view.height !== h) { view.width = w; view.height = h; ants.width = w; ants.height = h; }
        ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, w, h);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        const r = rect(), [x0, y0] = fromO(r.x, r.y);
        // Transparency shows as a checkerboard.
        ctx.fillStyle = checker(ctx); ctx.fillRect(x0, y0, r.w * layout.k, r.h * layout.k);
        const {canvas: pic} = picture();
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(pic, x0, y0, r.w * layout.k, r.h * layout.k);
        if (liveStroke) drawLiveStroke(ctx);
        drawOverlay();
    }
    let checkerPattern = null;
    function checker(ctx) {
        if (!checkerPattern) {
            const c = document.createElement('canvas'); c.width = c.height = 16; const x = c.getContext('2d');
            x.fillStyle = '#262d39'; x.fillRect(0, 0, 16, 16); x.fillStyle = '#313948'; x.fillRect(0, 0, 8, 8); x.fillRect(8, 8, 8, 8);
            checkerPattern = ctx.createPattern(c, 'repeat');
        }
        return checkerPattern;
    }
    function drawLiveStroke(ctx) {
        const {points, size, color, kind, opacity} = liveStroke;
        ctx.save(); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
        ctx.strokeStyle = ctx.fillStyle = kind === 'paint' ? color : 'rgba(255,255,255,.4)';
        ctx.globalAlpha = kind === 'paint' ? opacity : 1;
        ctx.lineWidth = size;
        const pts = points.map(p => fromO(...fromD(p)));
        ctx.beginPath();
        if (pts.length === 1) { ctx.arc(pts[0][0], pts[0][1], size / 2, 0, Math.PI * 2); ctx.fill(); }
        else { ctx.moveTo(...pts[0]); for (const p of pts.slice(1)) ctx.lineTo(...p); ctx.stroke(); }
        ctx.restore();
    }

    // Overlay: crop frame, transform box, brush cursor, drags in progress.
    const svg = (tag, attrs = {}) => { const n = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; };
    let cursorPos = null, gesture = null, antsPhase = 0;
    function drawOverlay() {
        overlay.replaceChildren();
        overlay.setAttribute('width', layout.w); overlay.setAttribute('height', layout.h);
        const r = rect(), [cx0, cy0] = fromO(r.x, r.y);
        const c = doc.crop, [x, y] = fromO(c.x, c.y), w = c.w * layout.k, h = c.h * layout.k;
        const full = Math.abs(r.x - c.x) < .5 && Math.abs(r.y - c.y) < .5 && Math.abs(r.w - c.w) < .5 && Math.abs(r.h - c.h) < .5;
        if (!full || tool === 'crop') {
            overlay.append(svg('path', {d: `M${cx0},${cy0}h${r.w * layout.k}v${r.h * layout.k}h${-r.w * layout.k}z M${x},${y}v${h}h${w}v${-h}z`,
                class: tool === 'crop' ? 'fv-ie-dim' : 'fv-ie-dim fv-ie-dim-soft', 'fill-rule': 'evenodd'}));
            overlay.append(svg('rect', {x, y, width: w, height: h, class: tool === 'crop' ? 'fv-ie-crop-frame' : 'fv-ie-crop-frame fv-ie-dashed'}));
        }
        if (tool === 'crop') {
            for (const i of [1, 2]) {
                overlay.append(svg('line', {x1: x + w * i / 3, y1: y, x2: x + w * i / 3, y2: y + h, class: 'fv-ie-third'}));
                overlay.append(svg('line', {x1: x, y1: y + h * i / 3, x2: x + w, y2: y + h * i / 3, class: 'fv-ie-third'}));
            }
            for (const [id, hx, hy] of [['nw', x, y], ['n', x + w / 2, y], ['ne', x + w, y], ['e', x + w, y + h / 2], ['se', x + w, y + h], ['s', x + w / 2, y + h], ['sw', x, y + h], ['w', x, y + h / 2]])
                overlay.append(svg('rect', {x: hx - 5, y: hy - 5, width: 10, height: 10, rx: 2, class: 'fv-ie-handle', 'data-handle': id}));
        }
        if (gesture?.preview) overlay.append(gesture.preview());
        if (tool === 'move' && !maskTarget) drawTransformBox();
        if (['brush', 'eraser', 'heal', 'clone', 'blur'].includes(tool) && cursorPos) {
            const radius = brushPixels(opts[tool].size) * layout.k / 2;
            overlay.append(svg('circle', {cx: cursorPos[0], cy: cursorPos[1], r: Math.max(2, radius), class: 'fv-ie-brush'}));
            if (tool === 'clone' && opts.clone.source) {
                const [sx, sy] = fromO(...fromD(opts.clone.source));
                overlay.append(svg('path', {d: `M${sx - 8},${sy}h16M${sx},${sy - 8}v16`, class: 'fv-ie-source'}));
            }
        }
        drawAnts();
    }
    function layerCorners(layer) {
        const m = new DOMMatrix(orientMatrix(doc)).multiplySelf(layerMatrix(layer));
        return [[0, 0], [layer.nw, 0], [layer.nw, layer.nh], [0, layer.nh]].map(([px, py]) => { const p = m.transformPoint(new DOMPoint(px, py)); return fromO(p.x, p.y); });
    }
    function drawTransformBox() {
        const layer = active();
        if (!layer || layer.locked || !layer.visible) return;
        const pts = layerCorners(layer);
        overlay.append(svg('polygon', {points: pts.map(p => p.join(',')).join(' '), class: 'fv-ie-box'}));
        const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
        const list = [['c0', pts[0]], ['e01', mid(pts[0], pts[1])], ['c1', pts[1]], ['e12', mid(pts[1], pts[2])], ['c2', pts[2]], ['e23', mid(pts[2], pts[3])], ['c3', pts[3]], ['e30', mid(pts[3], pts[0])]];
        for (const [id, [hx, hy]] of list) overlay.append(svg('rect', {x: hx - 5, y: hy - 5, width: 10, height: 10, rx: 2, class: 'fv-ie-handle', 'data-handle': id}));
        const top = mid(pts[0], pts[1]), centre = mid(pts[0], pts[2]);
        const len = Math.hypot(top[0] - centre[0], top[1] - centre[1]) || 1;
        const knob = [top[0] + (top[0] - centre[0]) / len * 26, top[1] + (top[1] - centre[1]) / len * 26];
        overlay.append(svg('line', {x1: top[0], y1: top[1], x2: knob[0], y2: knob[1], class: 'fv-ie-box'}));
        overlay.append(svg('circle', {cx: knob[0], cy: knob[1], r: 6, class: 'fv-ie-handle fv-ie-rotate', 'data-handle': 'rotate'}));
    }
    // Marching ants: the selection's edge, striped, moving.
    let antsEdge = null;
    function selectionEdge() {
        const s = previewScale(), key = JSON.stringify([selection, s]) + (selection?.parts.some(p => p.shape === 'wand') ? composited?.doc === doc : '');
        if (antsEdge?.key === key && antsEdge.doc === doc) return antsEdge.canvas;
        const mask = renderer.selectionMask(doc, selection, {layer: canvasLayer(), scale: s});
        const shifted = (dx, dy) => { const c = document.createElement('canvas'); c.width = mask.width; c.height = mask.height; const x = c.getContext('2d');
            x.drawImage(mask, 0, 0); x.globalCompositeOperation = 'xor'; x.drawImage(mask, dx, dy); return c; };
        const edge = shifted(1, 0); edge.getContext('2d').drawImage(shifted(0, 1), 0, 0);
        antsEdge = {key, doc, canvas: edge};
        return edge;
    }
    function drawAnts() {
        const ctx = ants.getContext('2d');
        ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, ants.width, ants.height);
        if (!selection) return;
        const edge = selectionEdge(), r = rect(), [x0, y0] = fromO(r.x, r.y), dpr = layout.dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(edge, x0, y0, r.w * layout.k, r.h * layout.k);
        ctx.globalCompositeOperation = 'source-in';
        const stripe = document.createElement('canvas'); stripe.width = stripe.height = 8; const st = stripe.getContext('2d');
        st.fillStyle = '#fff'; st.fillRect(0, 0, 8, 8); st.fillStyle = '#0b0f15'; st.fillRect(0, 0, 4, 8);
        const pattern = ctx.createPattern(stripe, 'repeat');
        pattern.setTransform?.(new DOMMatrix().translateSelf(antsPhase, 0).rotateSelf(45));
        ctx.fillStyle = pattern; ctx.fillRect(0, 0, layout.w, layout.h);
        ctx.globalCompositeOperation = 'source-over';
    }
    function canvasLayer() {
        const r = rect(), centre = toD([r.x + r.w / 2, r.y + r.h / 2]);
        return {id: '#canvas', nw: r.w, nh: r.h, w: r.w, h: r.h, cx: centre[0], cy: centre[1], rotation: -doc.rotate, flipX: doc.flipX, flipY: doc.flipY};
    }
    const antsTimer = setInterval(() => { if (selection && dialog.open) { antsPhase = (antsPhase + 1) % 8; drawAnts(); } }, 120);

    // ----- tool options ---------------------------------------------------------------
    function controls() {
        undo.disabled = history.length < 2; redo.disabled = !future.length;
        const c = doc.crop; sizeLabel.textContent = `${Math.round(c.w)} × ${Math.round(c.h)}`;
        zoomLabel.textContent = Math.round(layout.k * 100) + '%';
        for (const [id, b] of Object.entries(toolButtons)) b.setAttribute('aria-pressed', String(id === tool));
        stage.dataset.tool = tool;
    }
    function select(id) {
        commitText();
        gesture?.cancel?.(); gesture = null;
        tool = id; renderOptions(); controls(); draw(); showHint(); keepFocus();
    }
    function showHint() {
        const layer = active();
        let text = '';
        if (['eraser', 'heal', 'clone', 'blur'].includes(tool) && !rasterKinds.has(layer.kind)) text = t('Pick an image or paint layer to work on its pixels.', '请选择图片图层或绘画图层再处理像素。');
        else if (tool === 'clone' && !opts.clone.source) text = t('Alt-click to set the source, then paint.', '先按住 Alt 点击设置取样点，再涂抹。');
        else if (tool === 'move' && layer.locked && doc.layers.length > 1) text = t('The original image is locked. Click another layer to move it.', '原图图层已锁定；点击其他图层即可移动。');
        else if (tool === 'lasso' && opts.lasso.type === 'polygon') text = t('Click to add corners; double-click or press Enter to close.', '单击添加顶点；双击或按 Enter 闭合。');
        hint.textContent = text; hint.hidden = !text;
    }
    const ratioLabel = id => ({
        video: target ? t(`Video ${target.width}×${target.height}`, `视频画面 ${target.width}×${target.height}`) : t('Video', '视频画面'),
        original: t('Original', '原图比例'), free: t('Free', '自由'),
    })[id] || id;
    function renderOptions() {
        clearPanel(optionsBar);
        const add = (...nodes) => optionsBar.append(...nodes);
        const label = text => el('span', text, 'fv-ie-opt-label');
        const sep = () => el('span', null, 'fv-ie-sep');
        const note = text => el('span', text, 'fv-ie-opt-note');
        const sizeSlider = o => {
            const s = slider(`fv-ie-${tool}-size`, t('Size', '大小'), .002, .25, .001, o.size, v => { o.size = v; s.out.textContent = Math.round(brushPixels(v)) + ' px'; drawOverlay(); });
            s.out.textContent = Math.round(brushPixels(o.size)) + ' px';
            return s.row;
        };
        const pct = (o, key, text) => slider(`fv-ie-${tool}-${key}`, text, 0, 100, 1, Math.round(o[key] * 100), v => { o[key] = v / 100; }, null, '%').row;
        add(el('span', TOOLS.find(x => x?.[0] === tool)?.[1] || '', 'fv-ie-opt-title'));
        if (tool === 'move') {
            const auto = el('label', null, 'fv-ie-check'); const box = el('input'); box.type = 'checkbox'; box.checked = opts.move.auto;
            box.onchange = () => { opts.move.auto = box.checked; }; auto.append(box, el('span', t('Pick layer by clicking', '点击自动选择图层')));
            add(auto, sep(),
                iconButton('flipX', t('Flip layer horizontally', '水平翻转图层'), () => transformLayer(l => ({flipX: !l.flipX}))),
                iconButton('flipY', t('Flip layer vertically', '垂直翻转图层'), () => transformLayer(l => ({flipY: !l.flipY}))),
                button(t('Reset size', '原始大小'), null, () => transformLayer(l => ({w: l.nw, h: l.nh, ...upright(l.nw, l.nh), cx: l.cx, cy: l.cy}))),
                button(t('Cover canvas', '铺满画布'), null, () => transformLayer(cover)));
        } else if (tool === 'crop') {
            const ratio = el('select', null, 'fv-ie-select'); ratio.id = 'fv-ie-ratio'; ratio.setAttribute('aria-label', t('Shape', '比例'));
            for (const [id] of RATIOS) { if (id === 'video' && !target) continue; const o = el('option', ratioLabel(id)); o.value = id; ratio.append(o); }
            ratio.value = doc.ratio; ratio.onchange = () => setRatio(ratio.value);
            add(label(t('Shape', '比例')), ratio, sep(),
                iconButton('ccw', t('Rotate left 90°', '向左旋转 90°'), () => turn('ccw')), iconButton('cw', t('Rotate right 90°', '向右旋转 90°'), () => turn('cw')),
                iconButton('flipX', t('Flip horizontally', '水平翻转'), () => turn('flipX')), iconButton('flipY', t('Flip vertically', '垂直翻转'), () => turn('flipY')), sep());
            if (target) add(button(t('Pad to video shape', '补边到视频比例'), t('Extend the canvas with the background color instead of cropping', '用背景色补边到视频比例，不裁掉画面'), () => padToVideo()));
            add(button(t('Image size…', '图像大小…'), null, () => imageSizeDialog()), button(t('Canvas size…', '画布大小…'), null, () => canvasDialog()));
        } else if (tool === 'marquee' || tool === 'lasso' || tool === 'wand') {
            const o = opts[tool];
            if (tool === 'marquee') add(segmented([['rect', t('Rectangle', '矩形'), 'marquee'], ['ellipse', t('Ellipse', '椭圆'), 'ellipse']], o.shape, v => { o.shape = v; }, t('Shape', '形状')).group);
            if (tool === 'lasso') add(segmented([['free', t('Freehand', '自由套索'), 'lasso'], ['polygon', t('Polygonal', '多边形套索'), 'polygon']], o.type, v => { o.type = v; showHint(); }, t('Type', '类型')).group);
            if (tool === 'wand') {
                add(slider('fv-ie-wand-tolerance', t('Tolerance', '容差'), 0, 100, 1, o.tolerance, v => { o.tolerance = v; }).row);
                const c = el('label', null, 'fv-ie-check'); const box = el('input'); box.type = 'checkbox'; box.checked = o.contiguous;
                box.onchange = () => { o.contiguous = box.checked; }; c.append(box, el('span', t('Contiguous', '连续'))); add(c);
            }
            add(sep(), segmented([['replace', t('New', '新选区')], ['add', t('Add · Shift', '添加 · Shift')], ['subtract', t('Subtract · Alt', '减去 · Alt')]], o.mode, v => { o.mode = v; }, t('Mode', '方式')).group);
            if (tool !== 'wand') add(slider(`fv-ie-${tool}-feather`, t('Feather', '羽化'), 0, 80, 1, o.feather, v => { o.feather = v; }, null, ' px').row);
            add(sep(), ...selectionActions());
        } else if (tool === 'brush' || tool === 'eraser') {
            const o = opts[tool];
            add(sizeSlider(o), pct(o, 'hardness', t('Hardness', '硬度')), pct(o, 'opacity', t('Opacity', '不透明度')));
            if (active().mask) add(sep(), maskToggle());
            else if (tool === 'eraser') add(note(t('Erases pixels of the selected layer.', '擦除当前图层的像素。')));
        } else if (tool === 'heal') {
            const o = opts.heal;
            add(sizeSlider(o), segmented([['all', t('Whole stroke', '整个涂抹区域')], ['text', t('Captions only', '只去字幕')]], o.text ? 'text' : 'all',
                v => { o.text = v === 'text'; renderOptions(); }, t('Heal', '修复范围')).group,
                note(o.text ? t('Paint over a caption: only its white letters and outline are replaced, the picture between them stays.', '涂过字幕即可：只替换白色文字和描边，字与字之间的画面保留。')
                    : t('Paint over the whole mark in one stroke; it is filled with texture from around it.', '一笔涂满要去掉的部分，松开后用周围的纹理补上。')));
        } else if (tool === 'clone') {
            const o = opts.clone;
            const pick = button(o.picking ? t('Click the source…', '点击取样位置…') : t('Set source', '设置取样点'), t('Or Alt-click the picture', '也可以按住 Alt 点击画面'),
                () => { o.picking = !o.picking; renderOptions(); }, 'fv-ie-button' + (o.picking ? ' fv-ie-active' : ''));
            add(sizeSlider(o), pct(o, 'hardness', t('Hardness', '硬度')), pct(o, 'opacity', t('Opacity', '不透明度')), pick);
        } else if (tool === 'blur') {
            add(sizeSlider(opts.blur), pct(opts.blur, 'strength', t('Strength', '强度')));
        } else if (tool === 'gradient') {
            const o = opts.gradient;
            add(segmented([['linear', t('Linear', '线性')], ['radial', t('Radial', '径向')]], o.shape, v => { o.shape = v; }, t('Type', '类型')).group,
                segmented([['bg', t('To background color', '到背景色')], ['clear', t('To transparent', '到透明')]], o.end, v => { o.end = v; }, t('End', '终点')).group,
                note(t('Drag across the picture.', '在画面上拖动。')));
        } else if (tool === 'text') {
            add(note(t('Click the picture to add text; click text to edit it.', '点击画面添加文字；点击已有文字进行编辑。')));
        } else if (tool === 'shape') {
            const o = opts.shape;
            add(segmented([['rect', t('Rectangle', '矩形')], ['ellipse', t('Ellipse', '椭圆')], ['line', t('Line', '直线')]], o.type, v => { o.type = v; }, t('Shape', '形状')).group,
                note(t('Drag to draw in the foreground color.', '拖动绘制，使用前景色。')));
        } else if (tool === 'eyedropper') {
            add(note(t('Click to pick the foreground color; Alt-click for the background color.', '点击吸取前景色；按住 Alt 点击吸取背景色。')));
        } else if (tool === 'hand') {
            add(note(t('Drag to pan. With any tool, hold Space to pan; Ctrl + wheel zooms.', '拖动平移画面。任何工具下按住空格都能平移，Ctrl + 滚轮缩放。')));
        }
    }
    function maskToggle() {
        return segmented([['layer', t('On layer', '画在图层')], ['mask', t('On mask', '画在蒙版')]], maskTarget ? 'mask' : 'layer',
            v => { maskTarget = v === 'mask'; renderLayers(); }, t('Paint on', '绘制位置')).group;
    }
    function selectionActions() {
        const off = !selection;
        const list = [
            button(t('All', '全选'), t('Select all (Ctrl+A)', '全选（Ctrl+A）'), () => selectAll()),
            button(t('Invert', '反选'), t('Invert (Ctrl+Shift+I)', '反选（Ctrl+Shift+I）'), () => invertSelection()),
            button(t('Deselect', '取消选区'), t('Deselect (Ctrl+D)', '取消选择（Ctrl+D）'), () => deselect()),
            button(t('Fill', '填充'), t('Fill with the foreground color (Alt+Delete)', '用前景色填充（Alt+Delete）'), () => fillSelection()),
            button(t('Delete', '删除'), t('Clear the selected pixels (Delete)', '清除选中的像素（Delete）'), () => clearSelection()),
            button(t('To layer', '复制为图层'), t('Copy to a new layer (Ctrl+J)', '复制到新图层（Ctrl+J）'), () => copyToLayer()),
            button(t('Mask', '建立蒙版'), t('Show the layer only inside the selection', '只显示选区内的图层内容'), () => maskFromSelection()),
        ];
        list.slice(2).forEach(b => { b.disabled = off; });
        return list;
    }

    // ----- properties and layers -------------------------------------------------------
    function renderPanels() { renderProps(); renderLayers(); }
    // Empty a panel for redrawing. A focused field there leaves first, so its own change and
    // blur handlers run (and may redraw) before its panel is taken apart, not halfway through.
    function clearPanel(box) {
        if (box.contains(document.activeElement)) document.activeElement.blur();
        box.replaceChildren();
    }
    const blendNames = () => ({'source-over': t('Normal', '正常'), multiply: t('Multiply', '正片叠底'), screen: t('Screen', '滤色'), overlay: t('Overlay', '叠加'),
        'soft-light': t('Soft light', '柔光'), darken: t('Darken', '变暗'), lighten: t('Lighten', '变亮'), 'color-dodge': t('Color dodge', '颜色减淡'),
        'color-burn': t('Color burn', '颜色加深'), difference: t('Difference', '差值')});
    const field = (text, input) => { const f = el('div', null, 'fv-ie-field'); const l = el('label', text); l.htmlFor = input.id; f.append(l, input); return f; };
    function renderProps() {
        clearPanel(props);
        const layer = active();
        const head = el('div', null, 'fv-ie-section-head');
        head.append(el('h3', layer.kind === 'text' ? t('Text', '文字') : layer.kind === 'shape' ? t('Shape', '形状') : t('Adjust', '调整')),
            el('span', layer.name || '', 'fv-ie-props-name'));
        props.append(head);
        const opacity = slider('fv-ie-opacity', t('Opacity', '不透明度'), 0, 100, 1, Math.round(layer.opacity * 100),
            v => preview(replaceLayer(layer.id, {opacity: v / 100})), v => commit(replaceLayer(layer.id, {opacity: v / 100})), '%');
        const blend = el('select', null, 'fv-ie-select'); blend.id = 'fv-ie-blend';
        for (const [value, text] of Object.entries(blendNames())) { const o = el('option', text); o.value = value; blend.append(o); }
        blend.value = layer.blend || 'source-over'; blend.onchange = () => commit(replaceLayer(layer.id, {blend: blend.value}));
        const basics = el('div', null, 'fv-ie-basics'); basics.append(opacity.row, field(t('Blend', '混合'), blend));
        props.append(basics);
        if (layer.kind === 'text') props.append(textProps(layer));
        else if (layer.kind === 'shape') props.append(shapeProps(layer));
        else props.append(adjustProps(layer));
    }
    function adjustProps(layer) {
        const box = el('div', null, 'fv-ie-adjust');
        const groups = [
            ['light', t('Light', '光线'), [['exposure', t('Exposure', '曝光')], ['contrast', t('Contrast', '对比度')], ['highlights', t('Highlights', '高光')], ['shadows', t('Shadows', '阴影')]]],
            ['colour', t('Color', '颜色'), [['temperature', t('Temperature', '色温')], ['tint', t('Tint', '色调')], ['hue', t('Hue', '色相')], ['saturation', t('Saturation', '饱和度')], ['vibrance', t('Vibrance', '自然饱和度')]]],
            ['effects', t('Effects', '效果'), [['sharpen', t('Sharpen', '锐化')], ['blur', t('Blur', '模糊')], ['noise', t('Grain', '颗粒')], ['vignette', t('Vignette', '暗角')]]],
        ];
        for (const [id, name, items] of groups) {
            const g = el('details', null, 'fv-ie-group'); g.dataset.group = id;
            g.open = openGroups.has(id) || items.some(([k]) => layer.adjust?.[k]);
            g.ontoggle = () => { if (g.open) openGroups.add(id); else openGroups.delete(id); };
            const summary = el('summary', name);
            const touched = items.filter(([k]) => layer.adjust?.[k]).length;
            if (touched) summary.append(el('span', String(touched), 'fv-ie-count'));
            g.append(summary);
            for (const [key, text] of items) {
                const positive = ['sharpen', 'blur', 'noise'].includes(key), range = key === 'hue' ? 180 : 100;
                const set = v => l => ({adjust: {...(l.adjust || emptyAdjust()), [key]: v}});
                g.append(slider(`fv-ie-${key}`, text, positive ? 0 : -range, range, 1, layer.adjust?.[key] || 0,
                    v => preview(replaceLayer(layer.id, set(v))), v => commit(replaceLayer(layer.id, set(v)))).row);
            }
            box.append(g);
        }
        box.append(button(t('Reset adjustments', '重置调整'), null, () => commit(replaceLayer(layer.id, {adjust: emptyAdjust()})), 'fv-ie-button fv-ie-quiet'));
        return box;
    }
    const openGroups = new Set(['light']);
    let textArea = null, textEditing = false;
    function textProps(layer) {
        const box = el('div', null, 'fv-ie-fields');
        textArea = el('textarea', null, 'fv-ie-textarea'); textArea.id = 'fv-ie-text'; textArea.value = layer.text.content; textArea.rows = 3;
        textArea.setAttribute('aria-label', t('Text', '文字内容'));
        textArea.oninput = () => { textEditing = true; preview(updateText(layer.id, {content: textArea.value})); };
        // Leaving the text box keeps the other controls in place: the one being clicked must stay.
        textArea.onblur = () => commitText('layers');
        // The bundled fonts only, each name set in its own face.
        const names = {sans: t('Sans', '黑体'), serif: t('Serif', '宋体'), kai: t('Kai', '楷体'), mono: t('Mono', '等宽')};
        const font = segmented(FONT_KEYS.map(k => [k, names[k]]), FONT_KEYS.includes(layer.text.font) ? layer.text.font : 'sans',
            v => setTextFont(layer.id, {font: v}), t('Font', '字体'));
        font.group.id = 'fv-ie-font'; font.group.classList.add('fv-ie-fonts');
        for (const b of font.group.children) b.style.fontFamily = fontStack(b.dataset.value);
        loadFonts(FONT_KEYS.map(k => [k, 400])).catch(() => {});
        const size = el('input'); size.type = 'number'; size.min = '6'; size.max = '2000'; size.value = String(Math.round(layer.text.size)); size.id = 'fv-ie-font-size';
        size.onchange = () => commit(updateText(layer.id, {size: clamp(Number(size.value) || 12, 6, 2000)}));
        const color = el('input'); color.type = 'color'; color.value = layer.text.color; color.id = 'fv-ie-text-color';
        color.onchange = () => commit(updateText(layer.id, {color: color.value}));
        const fontRow = el('div', null, 'fv-ie-font-row'); fontRow.append(el('span', t('Font', '字体'), 'fv-ie-opt-label'), font.group);
        const row = el('div', null, 'fv-ie-pair'); row.append(field(t('Size', '字号'), size), field(t('Color', '颜色'), color));
        // Only the weights that ship: LXGW WenKai's heavier face is a medium.
        const heavy = layer.text.font === 'kai' ? t('Medium', '中粗') : t('Bold', '加粗');
        const style = segmented([['400', t('Regular', '常规')], ['700', heavy]], String(Number(layer.text.weight) >= 550 ? 700 : 400),
            v => setTextFont(layer.id, {weight: Number(v)}), t('Weight', '字重'));
        const align = segmented([['left', t('Left', '左')], ['center', t('Center', '中')], ['right', t('Right', '右')]], layer.text.align, v => commit(updateText(layer.id, {align: v})), t('Align', '对齐'));
        const effects = el('div', null, 'fv-ie-row');
        for (const [key, text] of [['shadow', t('Shadow', '阴影')], ['outline', t('Outline', '描边')]]) {
            const c = el('label', null, 'fv-ie-check'); const cb = el('input'); cb.type = 'checkbox'; cb.checked = !!layer.text[key];
            cb.onchange = () => commit(updateText(layer.id, {[key]: key === 'outline' ? (cb.checked ? '#000000' : false) : cb.checked}));
            c.append(cb, el('span', text)); effects.append(c);
        }
        const styles = el('div', null, 'fv-ie-row'); styles.append(style.group, align.group);
        box.append(textArea, fontRow, row, styles, effects);
        return box;
    }
    // A font or weight change waits for that font, so the text box is measured with it.
    async function setTextFont(id, patch) {
        const layer = doc.layers.find(l => l.id === id);
        if (!layer) return;
        const next = {...layer.text, ...patch};
        if (!fontLoaded(next.font, next.weight)) {
            message(t('Preparing the font…', '正在准备字体…'));
            try { await loadFont(next.font, next.weight); }
            catch { message(t('The font could not be loaded. Reload the page and try again.', '字体加载失败，请刷新页面后重试。')); renderProps(); return; }
            message('');
        }
        if (dialog.open && doc.layers.some(l => l.id === id)) commit(updateText(id, patch));
    }
    function updateText(id, patch) {
        return replaceLayer(id, l => { const text = {...l.text, ...patch}; const size = measureText(text);
            const sx = l.w / l.nw, sy = l.h / l.nh; return {text, ...size, w: size.nw * sx, h: size.nh * sy}; });
    }
    function commitText(panels = true) {
        if (!textEditing) return;
        textEditing = false;
        if (doc !== history[history.length - 1]) commit(doc, panels);
    }
    function shapeProps(layer) {
        const box = el('div', null, 'fv-ie-fields'), s = layer.shape;
        const update = patch => commit(replaceLayer(layer.id, l => ({shape: {...l.shape, ...patch}})));
        const colourRow = (key, text, fallback) => {
            const input = el('input'); input.type = 'color'; input.id = `fv-ie-shape-${key}`; input.value = s[key] || fallback;
            const on = el('input'); on.type = 'checkbox'; on.checked = !!s[key]; on.setAttribute('aria-label', text);
            input.onchange = () => update({[key]: input.value}); on.onchange = () => update({[key]: on.checked ? input.value : null});
            const f = field(text, input); f.prepend(on); return f;
        };
        const width = el('input'); width.type = 'number'; width.min = '1'; width.max = '400'; width.value = String(s.width); width.id = 'fv-ie-shape-width';
        width.onchange = () => update({width: clamp(Number(width.value) || 1, 1, 400)});
        if (s.type !== 'line') box.append(colourRow('fill', t('Fill', '填充'), fg));
        box.append(colourRow('stroke', t('Stroke', '描边'), bg), field(t('Stroke width', '描边粗细'), width));
        if (s.type === 'rect') {
            const radius = el('input'); radius.type = 'number'; radius.min = '0'; radius.max = '2000'; radius.value = String(s.radius || 0); radius.id = 'fv-ie-shape-radius';
            radius.onchange = () => update({radius: clamp(Number(radius.value) || 0, 0, 2000)});
            box.append(field(t('Corner radius', '圆角'), radius));
        }
        return box;
    }
    function renderLayers() {
        clearPanel(layersBox);
        const layer = active(), index = doc.layers.findIndex(l => l.id === layer.id);
        const head = el('div', null, 'fv-ie-section-head');
        head.append(el('h3', t('Layers', '图层')));
        const tools = el('div', null, 'fv-ie-layer-tools');
        const addMenu = el('details', null, 'fv-ie-menu');
        const summary = el('summary'); summary.innerHTML = SVG(ICONS.image, 16); summary.title = t('Add image layer', '添加图片图层'); summary.setAttribute('aria-label', summary.title);
        const menu = el('div', null, 'fv-ie-menu-list');
        if (upload) menu.append(button(t('From this computer…', '从电脑选择…'), null, () => { addMenu.open = false; filePicker.click(); }, 'fv-ie-menu-item'));
        if (library.length) menu.append(el('div', t('From Media', '来自素材'), 'fv-ie-menu-label'));
        for (const item of library) {
            const b = button('', null, () => { addMenu.open = false; addImageLayer(item.file, item.label); }, 'fv-ie-menu-item');
            const img = el('img'); img.src = resolve(item.file); img.alt = ''; img.loading = 'lazy';
            b.append(img, el('span', item.label)); menu.append(b);
        }
        addMenu.append(summary, menu);
        const del = iconButton('trash', t('Delete layer', '删除图层'), () => deleteLayer());
        const up = iconButton('up', t('Move up', '上移'), () => moveLayer(1)), down = iconButton('down', t('Move down', '下移'), () => moveLayer(-1));
        const maskButton = iconButton('mask', layer.mask ? t('Delete mask', '删除蒙版') : t('Add mask', '添加蒙版'), () => toggleMask(), 'fv-ie-icon' + (layer.mask ? ' fv-ie-active' : ''));
        del.disabled = !!layer.locked; up.disabled = index < 1 || index >= doc.layers.length - 1; down.disabled = index <= 1;
        maskButton.disabled = !rasterKinds.has(layer.kind);
        tools.append(iconButton('plus', t('New paint layer', '新建绘画图层'), () => addPaintLayer()), addMenu, maskButton,
            iconButton('copy', t('Duplicate layer', '复制图层'), () => duplicateLayer()), up, down, del);
        head.append(tools);
        const list = el('ol', null, 'fv-ie-layer-list'); list.setAttribute('aria-label', t('Layers', '图层'));
        for (const l of [...doc.layers].reverse()) {
            const row = el('li', null, 'fv-ie-layer'); row.dataset.id = l.id;
            row.setAttribute('aria-selected', String(l.id === doc.active));
            const eye = iconButton(l.visible ? 'eye' : 'eyeOff', l.visible ? t('Hide layer', '隐藏图层') : t('Show layer', '显示图层'),
                e => { e.stopPropagation(); commit(replaceLayer(l.id, {visible: !l.visible})); }, 'fv-ie-eye');
            const thumb = el('canvas', null, 'fv-ie-thumb'); thumb.width = 48; thumb.height = 34; drawThumb(thumb, l);
            const name = el('span', l.name || t('Layer', '图层'), 'fv-ie-layer-name');
            name.title = t('Double-click to rename', '双击重命名');
            name.ondblclick = e => { e.stopPropagation(); renameLayer(l, name); };
            row.append(eye, thumb);
            if (l.mask) {
                const m = el('canvas', null, 'fv-ie-thumb fv-ie-mask-thumb'); m.width = 48; m.height = 34; drawMaskThumb(m, l);
                m.title = t('Layer mask: click to paint on it', '图层蒙版：点击后画笔画在蒙版上');
                m.onclick = e => { e.stopPropagation(); setActive(l.id); maskTarget = true; renderLayers(); renderOptions(); };
                row.append(m);
                if (l.id === doc.active) (maskTarget ? m : thumb).classList.add('fv-ie-target');
            }
            row.append(name);
            if (l.locked) { const lk = el('span', null, 'fv-ie-lock'); lk.innerHTML = SVG(ICONS.lock, 14); lk.title = t('Locked', '已锁定'); row.append(lk); }
            else if (l.opacity < 1) row.append(el('span', Math.round(l.opacity * 100) + '%', 'fv-ie-layer-opacity'));
            row.onclick = () => { setActive(l.id); maskTarget = false; renderPanels(); renderOptions(); };
            list.append(row);
        }
        layersBox.append(head, list);
    }
    function drawThumb(c, layer) {
        try {
            const s = Math.min(1, 128 / Math.max(layer.nw, layer.nh));
            const img = renderer.layerImage(doc, layer, s);
            const ctx = c.getContext('2d'), k = Math.min(c.width / img.width, c.height / img.height);
            ctx.fillStyle = checker(ctx); ctx.fillRect(0, 0, c.width, c.height);
            ctx.drawImage(img, (c.width - img.width * k) / 2, (c.height - img.height * k) / 2, img.width * k, img.height * k);
        } catch { /* drawn on the next update once its image has loaded */ }
    }
    function drawMaskThumb(c, layer) {
        const m = renderer.maskRaster(doc, layer, Math.min(1, 128 / Math.max(layer.nw, layer.nh)));
        const ctx = c.getContext('2d'); ctx.fillStyle = '#000'; ctx.fillRect(0, 0, c.width, c.height);
        const k = Math.min(c.width / m.width, c.height / m.height);
        ctx.drawImage(m, (c.width - m.width * k) / 2, (c.height - m.height * k) / 2, m.width * k, m.height * k);
    }
    // Choosing a layer is not a step of its own: it rides on the current one.
    function setActive(id) {
        if (doc.active === id) return;
        doc = {...doc, active: id};
        if (history[history.length - 1].active !== id) history[history.length - 1] = {...history[history.length - 1], active: id};
        draw(); controls(); showHint();
    }
    function renameLayer(layer, node) {
        const input = el('input', null, 'fv-ie-rename'); input.value = layer.name; input.setAttribute('aria-label', t('Layer name', '图层名称'));
        node.replaceWith(input); input.focus(); input.select();
        let done = false;
        const finishRename = keep => { if (done) return; done = true; if (keep && input.value.trim() && input.value.trim() !== layer.name) commit(replaceLayer(layer.id, {name: input.value.trim()})); else renderLayers(); };
        input.onkeydown = e => { e.stopPropagation(); if (e.key === 'Enter') finishRename(true); if (e.key === 'Escape') { e.preventDefault(); finishRename(false); } };
        input.onblur = () => finishRename(true);
        input.onclick = e => e.stopPropagation();
    }
    function insertLayer(layer, base = doc) {
        const index = base.layers.findIndex(l => l.id === base.active);
        const layers = [...base.layers]; layers.splice(index + 1, 0, layer);
        maskTarget = false;
        commit({...base, layers, active: layer.id});
    }
    function addPaintLayer() {
        const n = doc.layers.filter(l => l.kind === 'paint').length + 1;
        insertLayer(paintLayer(doc, t(`Paint ${n}`, `绘画 ${n}`)));
    }
    async function addImageLayer(file, label) {
        try {
            const img = await renderer.image(file); renderer.images.get(file).value = img;
            const r = rect(), centre = toD([r.x + r.w / 2, r.y + r.h / 2]);
            // Fit inside two thirds of the canvas, upright on screen.
            const s = Math.min(1, r.w * .66 / img.naturalWidth, r.h * .66 / img.naturalHeight);
            insertLayer({id: uid('l'), kind: 'image', name: label || file.split('/').pop(), src: file, visible: true, locked: false, opacity: 1, blend: 'source-over',
                cx: centre[0], cy: centre[1], ...upright(img.naturalWidth * s, img.naturalHeight * s), nw: img.naturalWidth, nh: img.naturalHeight,
                ops: [], mask: null, adjust: emptyAdjust()});
            select('move');
        } catch { message(t('The image could not be added.', '图片无法添加。')); }
    }
    filePicker.onchange = async () => {
        const file = filePicker.files?.[0]; filePicker.value = '';
        if (!file || !upload) return;
        message(t('Uploading the image…', '正在上传图片…'));
        try { const result = await upload(file); message(''); await addImageLayer(result.file, file.name); }
        catch (error) { message(error.message || t('Upload failed.', '上传失败。')); }
    };
    function duplicateLayer() {
        const layer = active();
        insertLayer({...layer, id: uid('l'), locked: false, name: t(`${layer.name} copy`, `${layer.name} 副本`)});
    }
    function deleteLayer() {
        const layer = active(); if (layer.locked) return;
        const index = doc.layers.findIndex(l => l.id === layer.id);
        const layers = doc.layers.filter(l => l.id !== layer.id);
        maskTarget = false;
        commit({...doc, layers, active: layers[Math.max(0, index - 1)].id});
    }
    function moveLayer(delta) {
        const index = doc.layers.findIndex(l => l.id === doc.active), to = index + delta;
        if (index < 1 || to < 1 || to >= doc.layers.length) return;
        const layers = [...doc.layers]; [layers[index], layers[to]] = [layers[to], layers[index]];
        commit({...doc, layers});
    }
    function toggleMask() {
        const layer = active();
        if (layer.mask) { maskTarget = false; commit(replaceLayer(layer.id, {mask: null})); return; }
        if (!rasterKinds.has(layer.kind)) return;
        maskTarget = true;
        commit(replaceLayer(layer.id, {mask: {ops: selection ? [{id: uid('o'), type: 'fromSelection', selection}] : []}}));
        if (selection) deselect();
    }

    // ----- canvas: shape, orientation, size ----------------------------------------------
    function aspectOf(id, r = rect()) {
        if (id === 'video') return target ? target.width / target.height : null;
        if (id === 'original') return r.w / r.h;
        return RATIOS.find(x => x[0] === id)?.[1] || null;
    }
    function setRatio(id) {
        const r = rect(), aspect = aspectOf(id, r);
        let crop = {...doc.crop};
        if (aspect) {
            const fitted = fitRect(aspect, r), cx = crop.x + crop.w / 2, cy = crop.y + crop.h / 2;
            crop = {...fitted, x: clamp(cx - fitted.w / 2, r.x, r.x + r.w - fitted.w), y: clamp(cy - fitted.h / 2, r.y, r.y + r.h - fitted.h)};
        }
        commit({...doc, ratio: id, crop});
    }
    function turn(op) {
        const before = canvasRect(doc), next = {...doc}, e = doc.extend;
        if (op === 'cw' || op === 'ccw') {
            const mirrored = doc.flipX !== doc.flipY;
            next.rotate = (doc.rotate + ((op === 'cw') !== mirrored ? 90 : 270)) % 360;
            next.extend = op === 'cw' ? {...e, l: e.b, t: e.l, r: e.t, b: e.r} : {...e, l: e.t, t: e.r, r: e.b, b: e.l};
        } else {
            next[op] = !doc[op];
            next.extend = op === 'flipX' ? {...e, l: e.r, r: e.l} : {...e, t: e.b, b: e.t};
        }
        // The crop follows the picture.
        const map = ([x, y]) => {
            const u = (x - before.x) / before.w, v = (y - before.y) / before.h;
            return op === 'cw' ? [1 - v, u] : op === 'ccw' ? [v, 1 - u] : op === 'flipX' ? [1 - u, v] : [u, 1 - v];
        };
        const after = canvasRect(next), c = doc.crop, a = map([c.x, c.y]), b = map([c.x + c.w, c.y + c.h]);
        let crop = {x: after.x + Math.min(a[0], b[0]) * after.w, y: after.y + Math.min(a[1], b[1]) * after.h,
            w: Math.abs(a[0] - b[0]) * after.w, h: Math.abs(a[1] - b[1]) * after.h};
        if ((op === 'cw' || op === 'ccw') && doc.ratio !== 'free') {
            const aspect = aspectOf(doc.ratio, after);
            if (aspect) crop = fitRect(aspect, after);
        }
        commit({...next, crop});
    }
    // Pad (never crop) to the video's shape with the background colour.
    function padToVideo() {
        const [ow, oh] = orientedSize(doc), aspect = target.width / target.height;
        // Whole pixels in total, split between the two sides.
        let l = 0, r = 0, tp = 0, b = 0;
        if (ow / oh < aspect) { const extra = Math.round(oh * aspect - ow); l = Math.floor(extra / 2); r = extra - l; }
        else { const extra = Math.round(ow / aspect - oh); tp = Math.floor(extra / 2); b = extra - tp; }
        const next = {...doc, extend: {l, t: tp, r, b, color: bg}, ratio: 'video'};
        commit({...next, crop: canvasRect(next)});
    }
    function canvasDialog() {
        stage.querySelector('.fv-ie-popover')?.remove();
        const box = el('div', null, 'fv-ie-popover');
        const [ow, oh] = orientedSize(doc), e = doc.extend, inputs = {};
        const grid = el('div', null, 'fv-ie-pad-grid');
        for (const [key, text] of [['t', t('Top', '上')], ['b', t('Bottom', '下')], ['l', t('Left', '左')], ['r', t('Right', '右')]]) {
            const input = el('input'); input.type = 'number'; input.min = '0'; input.max = '20000'; input.value = String(Math.round(e[key])); input.id = `fv-ie-pad-${key}`;
            grid.append(field(text + ' (px)', input)); inputs[key] = input;
        }
        const color = el('input'); color.type = 'color'; color.value = e.color || bg; color.id = 'fv-ie-pad-color';
        const buttons = el('div', null, 'fv-ie-row fv-ie-end');
        buttons.append(button(t('Cancel', '取消'), null, () => box.remove()), button(t('Apply', '应用'), null, () => {
            const extend = {l: +inputs.l.value || 0, t: +inputs.t.value || 0, r: +inputs.r.value || 0, b: +inputs.b.value || 0, color: color.value};
            const next = {...doc, extend}; box.remove();
            commit({...next, crop: canvasRect(next), ratio: 'free'});
        }, 'fv-ie-button fv-ie-primary'));
        box.append(el('h3', t('Canvas size', '画布大小')), el('p', t(`The picture is ${ow} × ${oh} px. Add space on each side:`, `画面 ${ow} × ${oh} 像素，在四周补充空间：`), 'fv-ie-opt-note'),
            grid, field(t('Fill color', '补边颜色'), color), buttons);
        stage.append(box); inputs.t.focus();
    }

    // A selection and the clone source are kept in document pixels: when a step changes the
    // image size they follow it.
    function followSize(before, after) {
        if (before.width === after.width && before.height === after.height) return;
        const sx = after.width / before.width, sy = after.height / before.height;
        selection = scaleSelection(selection, sx, sy); antsEdge = null;
        if (opts.clone.source) opts.clone.source = [opts.clone.source[0] * sx, opts.clone.source[1] * sy];
    }
    // Image size: the whole picture at another size, every layer with it, in one step.
    function imageSizeDialog() {
        commitText();
        stage.querySelector('.fv-ie-popover')?.remove();
        const box = el('div', null, 'fv-ie-popover fv-ie-image-size');
        const cw = doc.crop.w, ch = doc.crop.h, now = [Math.round(cw), Math.round(ch)];
        const sized = k => [Math.max(1, Math.round(cw * k)), Math.max(1, Math.round(ch * k))];
        const long = Math.max(cw, ch), video = target ? Math.max(target.width / cw, target.height / ch) : null;
        const vsize = target ? `${target.width} × ${target.height}` : '';
        // [id, label, size or null when it would not make the picture smaller, why not]
        const presets = [
            ...(target ? [['video', t('Video size', '视频尺寸'), video < 1 ? sized(video) : null,
                t('The picture is no larger than the video', '图片已经不比视频大')]] : []),
            ['half', '50%', sized(.5), ''],
            ['long', t('Long side 2048', '长边 2048'), long > 2048 ? sized(2048 / long) : null,
                t('The long side is already 2048 px or less', '长边已经不超过 2048 像素')],
        ];
        let keep = true, preset = null;
        const choice = segmented(presets.map(([id, text]) => [id, text]), null, id => {
            const size = presets.find(p => p[0] === id)[2];
            width.value = String(size[0]); height.value = String(size[1]); preset = id; update();
        }, t('Presets', '预设'));
        choice.group.classList.add('fv-ie-presets');
        choice.group.querySelectorAll('.fv-ie-seg-item').forEach((b, i) => { if (!presets[i][2]) { b.disabled = true; b.title = presets[i][3]; } });
        const width = el('input'), height = el('input');
        for (const [input, id, v] of [[width, 'fv-ie-size-w', now[0]], [height, 'fv-ie-size-h', now[1]]]) {
            Object.assign(input, {type: 'number', min: '1', max: '20000', step: '1', value: String(v), id}); input.inputMode = 'numeric';
        }
        const number = input => Number(input.value);
        const valid = v => Number.isInteger(v) && v >= 1 && v <= 20000;
        const chain = iconButton('link', t('Keep proportions', '锁定比例'), () => {
            keep = !keep; chain.innerHTML = icon(keep ? 'link' : 'unlink'); chain.setAttribute('aria-pressed', String(keep));
            if (keep && valid(number(width))) height.value = String(Math.max(1, Math.round(number(width) * ch / cw)));
            update();
        }, 'fv-ie-icon fv-ie-chain');
        chain.setAttribute('aria-pressed', 'true');
        width.oninput = () => { preset = null; if (keep && valid(number(width))) height.value = String(Math.max(1, Math.round(number(width) * ch / cw))); update(); };
        height.oninput = () => { preset = null; if (keep && valid(number(height))) width.value = String(Math.max(1, Math.round(number(height) * cw / ch))); update(); };
        const dims = el('div', null, 'fv-ie-dims');
        dims.append(field(t('Width (px)', '宽 (px)'), width), chain, field(t('Height (px)', '高 (px)'), height));
        const notes = el('div', null, 'fv-ie-notes'), result = el('p', '', 'fv-ie-opt-note');
        const stretch = el('p', t('With the proportions changed, pictures and shapes stretch; text keeps its shape.', '比例改变后，图片和形状会拉伸，文字保持原样。'), 'fv-ie-opt-note');
        const vector = doc.layers.some(l => l.kind === 'text' || l.kind === 'shape');
        const texts = doc.layers.some(l => l.kind === 'text');
        const scales = el('p', t('Text and shape layers scale too and stay editable.', '文字和形状图层会一起缩放，仍然可以编辑。'), 'fv-ie-opt-note');
        notes.append(result, stretch, scales);
        const cancel = button(t('Cancel', '取消'), null, () => { box.remove(); stage.focus(); });
        const ok = button(t('Apply', '应用'), null, () => apply(), 'fv-ie-button fv-ie-primary');
        const buttons = el('div', null, 'fv-ie-row fv-ie-end'); buttons.append(cancel, ok);
        let same = true;
        function update() {
            choice.set(preset);
            const w = number(width), h = number(height), kx = w / cw, ky = h / ch;
            // Proportions unchanged when the other side is the rounded one (either way round).
            same = Math.abs(h - Math.round(w * ch / cw)) <= 1 || Math.abs(w - Math.round(h * cw / ch)) <= 1;
            const k = Math.max(kx, ky), pct = v => `${Math.round(v * 100)}%`;
            // How big against the original; verb is the Chinese 缩到 or 放大到.
            const share = verb => same ? t(`${pct(k)} of the original`, `${verb}原来的 ${pct(k)}`)
                : t(`Width ${pct(kx)} and height ${pct(ky)} of the original`, `宽为原来的 ${pct(kx)}、高为原来的 ${pct(ky)}`);
            let text, warn = false, allowed = true;
            if (!valid(w) || !valid(h)) { text = t('Enter whole numbers from 1 to 20000.', '请输入 1 到 20000 的整数。'); warn = true; allowed = false; }
            else if (k > 2 + 1e-9) { text = t('You can enlarge up to 200% of the original.', '最多放大到原来的 200%。'); warn = true; allowed = false; }
            else if (w * h > 64e6) { text = t('Up to 64 megapixels; reduce the width or height.', '最多 6400 万像素，请减小宽或高。'); warn = true; allowed = false; }
            else if (w === now[0] && h === now[1]) { text = t('The same size as now.', '和现在一样大。'); allowed = false; }
            else if (kx > 1 + 1e-9 || ky > 1 + 1e-9) {
                text = t(`${share()}; enlarging makes the picture softer.`, `${share('放大到')}，画面会变模糊。`); warn = true;
            } else if (target && presets[0][2] && w === presets[0][2][0] && h === presets[0][2][1]) {
                text = t(`${share()}; fills a ${vsize} video.`, `${share('缩到')}，刚好铺满 ${vsize} 的视频。`);
            } else if (target && Math.max(target.width / w, target.height / h) > 1 + 1e-9) {
                text = t(`Smaller than the ${vsize} video; the result will look softer.`, `比 ${vsize} 的视频小，生成的画面会变模糊。`); warn = true;
            } else text = t(`${share()}.`, `${share('缩到')}。`);
            result.textContent = text; result.classList.toggle('fv-ie-note-warn', warn);
            // With the proportions changed, the stretch note says what happens to each kind of layer.
            stretch.hidden = same || !texts || !valid(w) || !valid(h);
            scales.hidden = !vector || !stretch.hidden;
            ok.disabled = !allowed;
        }
        function apply() {
            if (ok.disabled) return;
            const next = resizeDocument(doc, number(width), number(height), keep || same);
            followSize(doc, next);
            box.remove(); stage.focus();
            commit(next);
        }
        box.addEventListener('keydown', event => {
            if (event.key === 'Enter' && !event.ctrlKey && !event.metaKey && event.target.tagName === 'INPUT') { event.preventDefault(); event.stopPropagation(); apply(); }
        });
        box.append(el('h3', t('Image size', '图像大小')),
            el('p', t(`Now ${now[0]} × ${now[1]} px`, `现在 ${now[0]} × ${now[1]} 像素`), 'fv-ie-opt-note'),
            choice.group, dims, notes, buttons);
        update();
        stage.append(box); width.focus(); width.select();
    }

    // ----- selections ---------------------------------------------------------------------
    function addPart(part, mode) {
        if (mode === 'replace' || !selection) selection = {parts: [{...part, mode: 'replace'}], feather: part.feather || 0, invert: false};
        else selection = {...selection, parts: [...selection.parts, {...part, mode}]};
        antsEdge = null; renderOptions(); draw();
    }
    function selectAll() { selection = {parts: [{shape: 'all', mode: 'replace'}], feather: 0, invert: false}; antsEdge = null; renderOptions(); draw(); }
    function deselectQuiet() { selection = null; antsEdge = null; }
    function deselect() { deselectQuiet(); renderOptions(); draw(); }
    function invertSelection() {
        selection = selection ? {...selection, invert: !selection.invert} : {parts: [{shape: 'all', mode: 'replace'}], feather: 0, invert: true};
        antsEdge = null; renderOptions(); draw();
    }
    function pixelLayer() {
        const layer = active();
        if (!rasterKinds.has(layer.kind)) { message(t('Pick an image or paint layer first.', '请先选择图片图层或绘画图层。')); return null; }
        return layer;
    }
    function pushOp(layer, op, base = doc) {
        const entry = {id: uid('o'), ...op};
        doc = base;
        // Only the brush and eraser paint a mask (hide / reveal); every other tool works on pixels.
        if (layer.mask && (op.tool === 'hide' || op.tool === 'reveal')) commit(replaceLayer(layer.id, l => ({mask: {ops: [...l.mask.ops, entry]}})));
        else commit(replaceLayer(layer.id, l => ({ops: [...l.ops, entry]})));
    }
    function fillSelection(color = fg) { const layer = pixelLayer(); if (layer) pushOp(layer, {type: 'fill', color, selection}); }
    function clearSelection() { const layer = pixelLayer(); if (layer && selection) pushOp(layer, {type: 'clear', selection}); }
    function copyToLayer() {
        const layer = pixelLayer(); if (!layer) return;
        const copy = {...layer, id: uid('l'), locked: false, src: null, name: t(`${layer.name} part`, `${layer.name} 局部`), ops: [], mask: null,
            copyOf: {layer: layer.id, upto: layer.ops.length, selection}, adjust: {...layer.adjust}};
        deselectQuiet(); insertLayer(copy); select('move');
    }
    function maskFromSelection() {
        const layer = pixelLayer(); if (!layer || !selection) return;
        maskTarget = true;
        commit(replaceLayer(layer.id, {mask: {ops: [{id: uid('o'), type: 'fromSelection', selection}]}}));
        deselect();
    }

    // ----- pointer ----------------------------------------------------------------------------
    let spaceHeld = false;
    function panGesture(start) {
        const from = {...viewState};
        return {move: p => { viewState = {...from, panX: from.panX + p[0] - start[0], panY: from.panY + p[1] - start[1]}; computeLayout(); draw(); controls(); }};
    }
    stage.addEventListener('pointerdown', event => {
        if (event.target.closest('.fv-ie-popover, .fv-ie-hint')) return;
        stage.focus({preventScroll: true});
        const p = pointer(event);
        if (event.button === 1 || spaceHeld || tool === 'hand') gesture = panGesture(p);
        else if (event.button !== 0) return;
        else if (gesture?.keep) { /* a polygon in progress takes the click on release */ }
        else gesture = startGesture(event, p);
        if (gesture) { stage.setPointerCapture(event.pointerId); event.preventDefault(); }
    });
    stage.addEventListener('pointermove', event => {
        const p = pointer(event); cursorPos = p;
        if (gesture?.move) gesture.move(p, event); else drawOverlay();
    });
    const release = event => {
        if (!gesture) return;
        const g = gesture; if (!g.keep) gesture = null;
        g.up?.(pointer(event), event);
        if (!g.keep) draw();
    };
    stage.addEventListener('pointerup', release);
    stage.addEventListener('pointercancel', event => { if (gesture && !gesture.keep) { gesture.cancel?.(); gesture = null; draw(); } void event; });
    stage.addEventListener('pointerleave', () => { cursorPos = null; drawOverlay(); });
    stage.addEventListener('dblclick', event => {
        if (gesture?.close) { gesture.close(); return; }
        if (tool === 'move' || tool === 'text') {
            const hit = hitLayer(pointer(event));
            if (hit?.kind === 'text') { setActive(hit.id); renderPanels(); textArea?.focus(); textArea?.select(); }
        }
    });
    stage.addEventListener('wheel', event => {
        event.preventDefault();
        if (event.ctrlKey || event.metaKey) zoomAt(pointer(event), Math.exp(-event.deltaY * .0025));
        else { viewState = {...viewState, panX: viewState.panX - event.deltaX, panY: viewState.panY - event.deltaY}; computeLayout(); draw(); controls(); }
    }, {passive: false});

    function hitLayer(p) {
        const d = toD(toO(...p));
        for (const layer of [...doc.layers].reverse()) {
            if (!layer.visible || layer.locked) continue;
            const [u, v] = toLocal(layer, d);
            if (u >= 0 && u <= 1 && v >= 0 && v <= 1) return layer;
        }
        return null;
    }
    function startGesture(event, p) {
        const o = toO(...p), d = toD(o), layer = active();
        const mode = event.shiftKey ? 'add' : event.altKey ? 'subtract' : null;
        if (tool === 'crop') return cropGesture(event, o);
        if (tool === 'move') return moveGesture(event, p, d);
        if (tool === 'marquee') {
            const om = opts.marquee;
            return dragBox(o, (a, b) => {
                const [x1, y1] = toD(a), [x2, y2] = toD(b);
                const part = {shape: om.shape, x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1), feather: om.feather};
                if (part.w * layout.k < 3 && part.h * layout.k < 3) { if ((mode || om.mode) === 'replace') deselect(); return; }
                addPart(part, mode || om.mode);
            }, om.shape === 'ellipse');
        }
        if (tool === 'lasso') return lassoGesture(o, mode || opts.lasso.mode);
        if (tool === 'wand') {
            const src = rasterKinds.has(layer.kind) ? layer : doc.layers[0];
            const [u, v] = toLocal(src, d);
            if (u < 0 || v < 0 || u > 1 || v > 1) return null;
            addPart({shape: 'wand', layer: src.id, u, v, tolerance: opts.wand.tolerance, contiguous: opts.wand.contiguous, upto: src.ops.length}, mode || opts.wand.mode);
            return null;
        }
        if (tool === 'eyedropper') {
            const {canvas: pic, scale} = picture(), r = rect();
            const x = Math.floor((o[0] - r.x) * scale), y = Math.floor((o[1] - r.y) * scale);
            if (x < 0 || y < 0 || x >= pic.width || y >= pic.height) return null;
            const px = pic.getContext('2d', {willReadFrequently: true}).getImageData(x, y, 1, 1).data;
            if (px[3] < 16) { message(t('Nothing to pick here: this part is transparent.', '这里是透明的，没有可吸取的颜色。')); return null; }
            const hex = '#' + [px[0], px[1], px[2]].map(v => v.toString(16).padStart(2, '0')).join('');
            if (event.altKey) { bg = hex; bgInput.value = hex; } else { fg = hex; fgInput.value = hex; }
            message(t(`Picked ${hex}.`, `已吸取 ${hex}。`));
            return null;
        }
        if (tool === 'text') {
            const hit = hitLayer(p);
            if (hit?.kind === 'text') { setActive(hit.id); renderPanels(); textArea?.focus(); return null; }
            addText(d); return null;
        }
        if (tool === 'shape') return shapeGesture(o);
        // Pixel tools.
        let target = layer, base = doc;
        if ((tool === 'brush' || tool === 'gradient') && (!rasterKinds.has(layer.kind) || (layer.locked && !maskTarget))) {
            // Paint goes on a layer of its own above the picture, so it can be erased or hidden alone.
            const reuse = tool === 'brush' && [...doc.layers].reverse().find(l => l.kind === 'paint' && l.visible);
            if (reuse) { target = reuse; base = {...doc, active: reuse.id}; }
            else {
                const n = doc.layers.filter(l => l.kind === 'paint').length + 1;
                target = paintLayer(doc, tool === 'gradient' ? t('Gradient', '渐变') : t(`Paint ${n}`, `绘画 ${n}`));
                const index = doc.layers.findIndex(l => l.id === doc.active);
                const layers = [...doc.layers]; layers.splice(index + 1, 0, target);
                base = {...doc, layers, active: target.id};
            }
        } else if (!rasterKinds.has(layer.kind)) { message(t('Pick an image or paint layer to work on its pixels.', '请选择图片图层或绘画图层再处理像素。')); return null; }
        if (tool === 'gradient') {
            return dragLine(o, (a, b) => {
                const from = toLocal(target, toD(a)), to = toLocal(target, toD(b));
                pushOp(target, {type: 'gradient', shape: opts.gradient.shape, from, to, c1: fg, c2: opts.gradient.end === 'bg' ? bg : 'transparent', selection}, base);
            });
        }
        if (tool === 'clone' && (event.altKey || opts.clone.picking)) {
            opts.clone.source = d; opts.clone.picking = false; opts.clone.offset = null;
            renderOptions(); showHint(); drawOverlay(); return null;
        }
        if (tool === 'clone' && !opts.clone.source) { message(t('Alt-click to set the source first.', '请先按住 Alt 点击设置取样点。')); return null; }
        return strokeGesture(target, d, base);
    }
    function strokeGesture(layer, d, base) {
        const o = opts[tool], kind = tool === 'brush' ? 'paint' : tool === 'eraser' ? 'erase' : tool, started = doc;
        const sizePx = brushPixels(o.size), points = [d];
        liveStroke = {points, size: sizePx * layout.k, color: fg, kind, opacity: o.opacity ?? 1};
        draw();
        return {
            move(p) {
                const next = toD(toO(...p)), last = points[points.length - 1];
                if (Math.hypot(next[0] - last[0], next[1] - last[1]) * layout.k < 1.5) return;
                points.push(next); draw();
            },
            up() {
                const held = liveStroke;
                liveStroke = null;
                // Kept in this layer's own pixels: fractions of its size, a size relative to its shorter side.
                const pts = points.map(q => toLocal(layer, q).map(v => Math.round(v * 1e5) / 1e5));
                const op = {type: 'stroke', tool: kind, pts, size: sizePx * (layer.nw / layer.w) / Math.min(layer.nw, layer.nh),
                    hardness: o.hardness ?? 1, opacity: o.opacity ?? 1, color: fg, selection};
                if (kind === 'clone') {
                    const src = toLocal(layer, opts.clone.source), start = toLocal(layer, points[0]);
                    // Aligned: the first stroke after picking a source fixes the offset.
                    if (!opts.clone.offset) opts.clone.offset = [start[0] - src[0], start[1] - src[1]];
                    op.offset = opts.clone.offset;
                }
                if (kind === 'blur') op.strength = o.strength;
                if (kind === 'heal' && o.text) op.text = true;
                if (maskTarget && layer.mask) op.tool = kind === 'paint' ? 'hide' : kind === 'erase' ? 'reveal' : kind;
                if (kind === 'heal') {
                    // Healing a large stroke takes a moment: say so, and keep the stroke on screen until it is done.
                    liveStroke = held; message(t('Healing…', '正在修复…')); draw();
                    healing = new Promise(resolve => setTimeout(() => {
                        if (liveStroke === held) liveStroke = null;
                        const current = dialog.open && doc.layers.find(l => l.id === layer.id);
                        if (current) pushOp(current, op, doc);
                        healing = null; message(''); resolve();
                    }, 40));
                    return;
                }
                // Onto the latest document, unless this stroke made its own layer.
                pushOp(layer, op, base === started ? doc : base);
            },
            cancel() { liveStroke = null; },
        };
    }
    function dragBox(start, done, ellipse = false) {
        let end = start;
        return {
            move(p, event) {
                end = toO(...p);
                if (event?.shiftKey) { const s = Math.max(Math.abs(end[0] - start[0]), Math.abs(end[1] - start[1])); end = [start[0] + Math.sign(end[0] - start[0] || 1) * s, start[1] + Math.sign(end[1] - start[1] || 1) * s]; }
                drawOverlay();
            },
            up() { done(start, end); },
            preview() {
                const [x1, y1] = fromO(...start), [x2, y2] = fromO(...end);
                if (ellipse) return svg('ellipse', {cx: (x1 + x2) / 2, cy: (y1 + y2) / 2, rx: Math.abs(x2 - x1) / 2, ry: Math.abs(y2 - y1) / 2, class: 'fv-ie-drag'});
                return svg('rect', {x: Math.min(x1, x2), y: Math.min(y1, y2), width: Math.abs(x2 - x1), height: Math.abs(y2 - y1), class: 'fv-ie-drag'});
            },
        };
    }
    function dragLine(start, done) {
        let end = start;
        return {
            move(p) { end = toO(...p); drawOverlay(); },
            up() { if (Math.hypot(end[0] - start[0], end[1] - start[1]) * layout.k > 3) done(start, end); },
            preview() { const [x1, y1] = fromO(...start), [x2, y2] = fromO(...end); return svg('line', {x1, y1, x2, y2, class: 'fv-ie-drag'}); },
        };
    }
    function lassoGesture(start, mode) {
        const pts = [start];
        const finish = () => {
            gesture = null;
            if (pts.length < 3) { if (mode === 'replace') deselect(); else draw(); return; }
            addPart({shape: 'poly', pts: pts.map(toD), feather: opts.lasso.feather}, mode);
        };
        const line = extra => svg('polyline', {points: [...pts, ...(extra ? [extra] : [])].map(q => fromO(...q).join(',')).join(' '), class: 'fv-ie-drag'});
        if (opts.lasso.type === 'free') return {move(p) { pts.push(toO(...p)); drawOverlay(); }, up: finish, preview: () => line()};
        // Polygonal: each click adds a corner; double-click, Enter or the first corner closes it.
        let hover = null, first = true;
        return {
            keep: true,
            move(p) { hover = toO(...p); drawOverlay(); },
            up(p) {
                if (first) { first = false; return; }
                const q = toO(...p), [fx, fy] = fromO(...pts[0]), last = fromO(...pts[pts.length - 1]);
                if (pts.length > 2 && Math.hypot(fx - p[0], fy - p[1]) < 9) { finish(); return; }
                if (Math.hypot(last[0] - p[0], last[1] - p[1]) > 2) pts.push(q);
                drawOverlay();
            },
            close: finish,
            cancel() { gesture = null; drawOverlay(); },
            preview: () => line(hover),
        };
    }
    function shapeGesture(o) {
        const kind = opts.shape.type;
        return {...dragBox(o, (a, b) => {
            const width = opts.shape.width, dx = b[0] - a[0], dy = b[1] - a[1];
            if (Math.hypot(dx, dy) * layout.k < 4) return;
            const centre = toD([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]);
            let w, h, angle = 0;
            if (kind === 'line') { w = Math.hypot(dx, dy); h = Math.max(width * 2, 8); angle = Math.atan2(dy, dx) * 180 / Math.PI; }
            else { w = Math.max(4, Math.abs(dx)); h = Math.max(4, Math.abs(dy)); }
            const shape = {type: kind, fill: kind === 'line' ? null : fg, stroke: kind === 'line' ? fg : null, width, radius: 0};
            insertLayer({id: uid('l'), kind: 'shape', name: {rect: t('Rectangle', '矩形'), ellipse: t('Ellipse', '椭圆'), line: t('Line', '直线')}[kind],
                visible: true, locked: false, opacity: 1, blend: 'source-over', cx: centre[0], cy: centre[1], ...upright(w, h, angle),
                nw: Math.max(1, Math.round(w)), nh: Math.max(1, Math.round(h)), shape, ops: [], mask: null, adjust: emptyAdjust()});
        }, kind === 'ellipse'), ...(kind === 'line' ? {preview: null} : {})};
    }
    function cropGesture(event, o) {
        const handle = event.target.dataset?.handle;
        const r = rect(), c0 = {...doc.crop};
        const inside = o[0] >= c0.x && o[0] <= c0.x + c0.w && o[1] >= c0.y && o[1] <= c0.y + c0.h;
        const kind = handle ? 'resize' : inside ? 'move' : 'new';
        const ratio = kind === 'new' && doc.ratio === 'original' ? 'free' : doc.ratio;
        const aspect = () => aspectOf(ratio, r);
        const limit = c => {
            let {x, y, w, h} = c; const a = aspect();
            w = clamp(w, 8, r.w); h = clamp(h, 8, r.h);
            if (a) { if (w / h > a) w = h * a; else h = w / a; if (w > r.w) { w = r.w; h = w / a; } if (h > r.h) { h = r.h; w = h * a; } }
            return {x: clamp(x, r.x, r.x + r.w - w), y: clamp(y, r.y, r.y + r.h - h), w, h};
        };
        return {
            move(p) {
                const q = toO(...p);
                let c;
                if (kind === 'move') c = {...c0, x: clamp(c0.x + q[0] - o[0], r.x, r.x + r.w - c0.w), y: clamp(c0.y + q[1] - o[1], r.y, r.y + r.h - c0.h)};
                else if (kind === 'new') {
                    let w = Math.abs(q[0] - o[0]), h = Math.abs(q[1] - o[1]); const a = aspect();
                    if (a) { if (w / Math.max(h, 1e-6) > a) w = h * a; else h = w / a; }
                    c = limit({x: q[0] < o[0] ? o[0] - w : o[0], y: q[1] < o[1] ? o[1] - h : o[1], w, h});
                } else {
                    let left = c0.x, top = c0.y, right = c0.x + c0.w, bottom = c0.y + c0.h;
                    if (handle.includes('w')) left = clamp(q[0], r.x, right - 8);
                    if (handle.includes('e')) right = clamp(q[0], left + 8, r.x + r.w);
                    if (handle.includes('n')) top = clamp(q[1], r.y, bottom - 8);
                    if (handle.includes('s')) bottom = clamp(q[1], top + 8, r.y + r.h);
                    let w = right - left, h = bottom - top; const a = aspect();
                    if (a) {
                        if (handle === 'n' || handle === 's') w = h * a; else if (handle === 'e' || handle === 'w') h = w / a; else if (w / h > a) w = h * a; else h = w / a;
                        const ax = handle.includes('w') ? right : handle.includes('e') ? left : c0.x + c0.w / 2;
                        const ay = handle.includes('n') ? bottom : handle.includes('s') ? top : c0.y + c0.h / 2;
                        left = handle.includes('w') ? ax - w : handle.includes('e') ? ax : ax - w / 2;
                        top = handle.includes('n') ? ay - h : handle.includes('s') ? ay : ay - h / 2;
                    }
                    c = limit({x: left, y: top, w, h});
                }
                preview({...doc, crop: c, ratio});
            },
            up() {
                if (kind === 'new' && (doc.crop.w * layout.k < 6 || doc.crop.h * layout.k < 6)) { doc = history[history.length - 1]; refresh(false); return; }
                if (doc !== history[history.length - 1]) commit(doc);
            },
            cancel() { doc = history[history.length - 1]; refresh(false); },
        };
    }
    function moveGesture(event, p, d) {
        let layer = active();
        const handle = event.target.dataset?.handle;
        if (!handle && opts.move.auto) {
            const hit = hitLayer(p);
            if (hit && hit.id !== layer.id) { setActive(hit.id); maskTarget = false; renderPanels(); layer = hit; }
        }
        if (layer.locked) { showHint(); return null; }
        const l0 = {...layer};
        const frame = new DOMMatrix().translateSelf(l0.cx, l0.cy).rotateSelf(l0.rotation);
        const local = q => { const r = frame.inverse().transformPoint(new DOMPoint(q[0], q[1])); return [r.x, r.y]; };
        // A handle names a corner or edge of the box as drawn; find its side in the layer's own axes.
        let sx = 0, sy = 0;
        if (handle && handle !== 'rotate') {
            const corners = layerCorners(l0);
            const at = handle.startsWith('c') ? corners[+handle[1]] : [(corners[+handle[1]][0] + corners[+handle[2]][0]) / 2, (corners[+handle[1]][1] + corners[+handle[2]][1]) / 2];
            const lp = local(toD(toO(...at)));
            sx = Math.abs(lp[0]) > l0.w * .25 ? Math.sign(lp[0]) : 0; sy = Math.abs(lp[1]) > l0.h * .25 ? Math.sign(lp[1]) : 0;
        }
        return {
            move(pp, ev) {
                const q = toD(toO(...pp));
                let patch;
                if (!handle) patch = {cx: l0.cx + q[0] - d[0], cy: l0.cy + q[1] - d[1]};
                else if (handle === 'rotate') {
                    const mirrored = (doc.flipX !== doc.flipY);
                    let delta = (Math.atan2(q[1] - l0.cy, q[0] - l0.cx) - Math.atan2(d[1] - l0.cy, d[0] - l0.cx)) * 180 / Math.PI;
                    let angle = l0.rotation + delta;
                    if (ev?.shiftKey) angle = Math.round(angle / 15) * 15;
                    void mirrored; patch = {rotation: angle};
                } else {
                    const lq = local(q);
                    let w = l0.w, h = l0.h;
                    if (sx) w = Math.max(4, sx > 0 ? lq[0] + l0.w / 2 : l0.w / 2 - lq[0]);
                    if (sy) h = Math.max(4, sy > 0 ? lq[1] + l0.h / 2 : l0.h / 2 - lq[1]);
                    // Corners keep the proportions (Shift frees them); edges stretch one side.
                    if (sx && sy && !ev?.shiftKey) { const s = Math.max(w / l0.w, h / l0.h); w = l0.w * s; h = l0.h * s; }
                    const c = frame.transformPoint(new DOMPoint(sx ? sx * (w - l0.w) / 2 : 0, sy ? sy * (h - l0.h) / 2 : 0));
                    patch = {w, h, cx: c.x, cy: c.y};
                }
                preview(replaceLayer(layer.id, patch));
            },
            up() { if (doc !== history[history.length - 1]) commit(doc); },
            cancel() { doc = history[history.length - 1]; refresh(false); },
        };
    }
    function transformLayer(fn) {
        const layer = active(); if (layer.locked) { showHint(); return; }
        commit(replaceLayer(layer.id, fn(layer)));
    }
    function cover(layer) {
        const r = rect(), centre = toD([r.x + r.w / 2, r.y + r.h / 2]);
        const s = Math.max(r.w / layer.nw, r.h / layer.nh);
        return {cx: centre[0], cy: centre[1], ...upright(layer.nw * s, layer.nh * s)};
    }
    function addText(d) {
        const size = Math.max(16, Math.round(Math.min(doc.width, doc.height) / 12));
        const text = {content: t('Text', '文字'), font: opts.text.font, size, color: fg, weight: opts.text.weight, align: opts.text.align,
            shadow: opts.text.shadow, outline: opts.text.outline, lineHeight: 1.25};
        const {nw, nh} = measureText(text);
        insertLayer({id: uid('l'), kind: 'text', name: t('Text', '文字'), visible: true, locked: false, opacity: 1, blend: 'source-over',
            cx: d[0], cy: d[1], ...upright(nw, nh), nw, nh, text, ops: [], mask: null, adjust: emptyAdjust()});
        requestAnimationFrame(() => { textArea?.focus(); textArea?.select(); });
    }

    // ----- zoom --------------------------------------------------------------------------------
    function zoomAt(p, factor) {
        const before = toO(...p);
        viewState = {...viewState, zoom: clamp(viewState.zoom * factor, .05, 64)};
        computeLayout();
        const after = fromO(...before);
        viewState = {...viewState, panX: viewState.panX + p[0] - after[0], panY: viewState.panY + p[1] - after[1]};
        computeLayout(); draw(); controls();
    }
    function zoomBy(f) { zoomAt([layout.w / 2, layout.h / 2], f); }
    function fit() { viewState = {zoom: 1, panX: 0, panY: 0}; computeLayout(); draw(); controls(); }
    function actualSize() { viewState = {zoom: 1 / layout.fitK, panX: 0, panY: 0}; computeLayout(); draw(); controls(); }

    // ----- keys ----------------------------------------------------------------------------------
    const KEYS = {v: 'move', c: 'crop', m: 'marquee', l: 'lasso', w: 'wand', b: 'brush', e: 'eraser', j: 'heal', s: 'clone', r: 'blur', g: 'gradient', t: 'text', u: 'shape', i: 'eyedropper', h: 'hand'};
    function keys(event) {
        const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) && !['range', 'checkbox', 'color', 'radio'].includes(event.target.type);
        const mod = event.ctrlKey || event.metaKey, key = event.key.toLowerCase();
        // Nothing typed here reaches ComfyUI's canvas shortcuts behind the dialog.
        if (event.key !== 'Escape' && event.key !== 'Tab') event.stopPropagation();
        if (typing) { if (mod && key === 'enter') { event.preventDefault(); finish(); } return; }
        if (mod && key === 'z') { event.preventDefault(); step(event.shiftKey ? 1 : -1); return; }
        if (mod && key === 'y') { event.preventDefault(); step(1); return; }
        if (mod && key === 'enter') { event.preventDefault(); finish(); return; }
        if (mod && key === 'a') { event.preventDefault(); selectAll(); return; }
        if (mod && key === 'd') { event.preventDefault(); deselect(); return; }
        if (mod && event.shiftKey && key === 'i') { event.preventDefault(); invertSelection(); return; }
        if (mod && key === 'j') { event.preventDefault(); copyToLayer(); return; }
        if (mod && (key === '=' || key === '+')) { event.preventDefault(); zoomBy(1.25); return; }
        if (mod && key === '-') { event.preventDefault(); zoomBy(1 / 1.25); return; }
        if (mod && key === '0') { event.preventDefault(); fit(); return; }
        if (mod && key === '1') { event.preventDefault(); actualSize(); return; }
        if (mod || event.altKey && key !== 'backspace' && key !== 'delete') return;
        if (event.key === ' ') { event.preventDefault(); if (!spaceHeld) { spaceHeld = true; stage.dataset.pan = 'true'; } return; }
        if ((event.key === 'Delete' || event.key === 'Backspace') && event.altKey) { event.preventDefault(); fillSelection(); return; }
        if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); clearSelection(); return; }
        if (event.key === 'Enter' && gesture?.close) { event.preventDefault(); gesture.close(); return; }
        if (key === 'x') { swapColors(); return; }
        if (key === 'd') { fg = '#ffffff'; bg = '#111111'; fgInput.value = fg; bgInput.value = bg; return; }
        if (key === '[' || key === ']') {
            const o = opts[tool]; if (!o || o.size === undefined) return;
            if (event.shiftKey && o.hardness !== undefined) o.hardness = clamp(o.hardness + (key === ']' ? .1 : -.1), 0, 1);
            else o.size = clamp(o.size * (key === ']' ? 1.2 : 1 / 1.2), .002, .25);
            renderOptions(); drawOverlay(); return;
        }
        if (KEYS[key]) {
            if (key === 'm' && tool === 'marquee' && event.shiftKey) opts.marquee.shape = opts.marquee.shape === 'rect' ? 'ellipse' : 'rect';
            if (key === 'l' && tool === 'lasso' && event.shiftKey) opts.lasso.type = opts.lasso.type === 'free' ? 'polygon' : 'free';
            select(KEYS[key]);
        }
    }
    function keyup(event) { if (event.key === ' ') { spaceHeld = false; delete stage.dataset.pan; } }
    // Keys pressed while focus has slipped out of the dialog still belong to the editor.
    function stray(event) {
        if (!dialog.open || dialog.contains(event.target) || event.key === 'Escape') return;
        (event.type === 'keydown' ? keys : keyup)(event);
        event.stopPropagation();
    }
    dialog.addEventListener('keydown', keys);
    dialog.addEventListener('keyup', keyup);
    window.addEventListener('keydown', stray, true);
    window.addEventListener('keyup', stray, true);
    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        if (gesture?.cancel) { gesture.cancel(); gesture = null; draw(); return; }
        // Escape in a text field ends typing there; the next one leaves. In a popover it closes it.
        const field = document.activeElement;
        const open = field?.closest?.('.fv-ie-popover');
        if (open && stage.contains(open)) { open.remove(); stage.focus(); return; }
        if (dialog.contains(field) && /^(INPUT|TEXTAREA)$/.test(field.tagName)
            && !['range', 'checkbox', 'color', 'radio'].includes(field.type)) { commitText(); stage.focus(); return; }
        const pop = stage.querySelector('.fv-ie-popover'); if (pop) { pop.remove(); return; }
        if (selection) { deselect(); return; }
        if (!confirmBar.hidden) close(null); else leave();
    });

    // ----- leave / save ----------------------------------------------------------------------------
    let resolveResult;
    const result = new Promise(r => { resolveResult = r; });
    let messageTimer = null;
    function message(text) { status.textContent = text; clearTimeout(messageTimer); if (text) messageTimer = setTimeout(() => { status.textContent = ''; }, 5000); }
    async function leave() {
        if (healing) await healing;
        commitText();
        if (changed()) { confirmBar.hidden = false; confirmBar.querySelector('.fv-ie-danger').focus(); return; }
        close(null);
    }
    async function finish() {
        if (healing) await healing;
        commitText();
        if (strip(doc) === strip(original())) { close(fresh ? null : {restore: true}); return; }
        if (!changed() && !fresh) { close(null); return; }
        save.disabled = cancel.disabled = true; message(t('Saving the edited image…', '正在保存编辑后的图片…'));
        try {
            const {blob, width, height} = await renderer.export(doc, extension === 'jpg' ? 'image/jpeg' : 'image/png');
            close({blob, name: `${stem}-edit.${extension}`, edit: doc, width, height});
        } catch {
            message(t('The image could not be rendered. Try a smaller canvas or reload the page.', '图片生成失败，请缩小画布或刷新页面后重试。'));
            save.disabled = cancel.disabled = false;
        }
    }
    function close(value) {
        clearInterval(antsTimer); clearTimeout(messageTimer);
        observer.disconnect();
        dialog.removeEventListener('keydown', keys); dialog.removeEventListener('keyup', keyup);
        window.removeEventListener('keydown', stray, true); window.removeEventListener('keyup', stray, true);
        if (dialog.open) dialog.close();
        // Give every canvas back: this editor holds nothing once it is closed.
        renderer.release();
        for (const c of [view, ants]) { c.width = c.height = 0; }
        composited = null; antsEdge = null;
        dialog.remove();
        resolveResult(value);
    }

    // A read-only view of the editor for automation and tests.
    dialog.freevideoEditor = {
        state: () => ({doc, selection, tool, fg, bg, zoom: layout.k, maskTarget, history: history.length, future: future.length, healing: !!healing}),
        pictureBox: () => { const b = stage.getBoundingClientRect(), r = rect(), [x, y] = fromO(r.x, r.y); return {x: b.left + x, y: b.top + y, width: r.w * layout.k, height: r.h * layout.k}; },
    };

    // ----- start -------------------------------------------------------------------------------------
    const observer = new ResizeObserver(() => { computeLayout(); draw(); controls(); });
    observer.observe(stage);
    dialog.showModal();
    computeLayout();
    renderPanels();
    select(startTool || (fresh && target && role !== 'reference' ? 'crop' : 'move'));
    save.focus();
    // The other fonts load in the background, so switching to one is immediate.
    loadFonts(FONT_KEYS.flatMap(k => [[k, 400], [k, 700]])).catch(() => {});
    return result;
}
