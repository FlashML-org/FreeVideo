// Frame & size picker shared by the creation panel and the image view.
//
//   const picker = createFramePicker({t, shapes, sizes, sizeFor, custom, onChange, onPreview});
//   host.append(picker.element); picker.set({shape, ratio, pixels, width, height, locked, disabled, limit, unavailable});
//
// Everything is in sight, nothing folds. Frame: every shape as a tile drawn at its own ratio, with its ratio under
// it. Size: the main sizes as blocks that say their final pixels at the current frame, then width × height and the
// ratio lock. Typing a width or height is the custom size: no size block is lit then, the two fields are.
// The tiles and blocks lay themselves out by the room they are given (one row when wide, four tiles a row when narrow).
//
// shapes   [{id, label, ratio, thumb?}]: ratio a number, or null for a shape that follows something else (an image;
//          give it the ratio through set({ratio}) while selected). A shape with the id 'custom' gets no tile: the
//          typed width × height stands for it. set({unavailable: {id: 'why'}}) greys a tile and says why on hover.
// sizes    [{pixels, label?}]: megapixels; a size is named by them (1M, 2M) unless a label is given. Entries marked more are not listed:
//          any other size is typed as width × height.
// sizeFor  (ratio, pixels) => {width, height}: the caller's rounding (default: 32-pixel steps, 256–4096 a side).
// custom   optional {minSide, maxSide, snap = 32, lock = true, keepEmpty = false}: the width × height fields and the ratio lock.
//          keepEmpty: a cleared side stays empty (onChange source 'empty'); without it the side is filled again.
//          set({limit: {pixels, note?}}): what this computer can make. A typed size above it becomes the largest size
//          at that shape, and a line under the fields says so.
// onChange ({source, side?, shape, ratio, pixels, width, height, locked}) after the user picks ('shape', 'size',
//          'dimensions' with side 'width' | 'height', 'lock'); the caller applies it and calls set() with what it accepted.
// onPreview({ratio, width, height} | null) while an option is pointed at or focused, and when it is left. The views show
//          the next generation's empty frame at that shape (nextFrame below gives its box), not an outline over a work.

const SVG = 'http://www.w3.org/2000/svg';
let styled = false;
function loadStyle() {
    if (styled || typeof document === 'undefined') return; styled = true;
    const href = new URL('frame_picker.css', import.meta.url).href;
    if ([...document.querySelectorAll('link')].some(link => link.href === href)) return;
    const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = href; document.head.append(link);
}
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
function svgBox(size) { const s = document.createElementNS(SVG, 'svg'); s.setAttribute('viewBox', `0 0 ${size} ${size}`); s.setAttribute('aria-hidden', 'true'); return s; }
function rect(svg, cls) { const r = document.createElementNS(SVG, 'rect'); r.setAttribute('class', cls); r.setAttribute('rx', '2'); svg.append(r); return r; }
// A ratio drawn inside a square box: the long side fills it.
function place(r, ratio, box, scale = 1, inset = 1) {
    const room = box - 2 * inset, w = (ratio >= 1 ? room : room * ratio) * scale, h = (ratio >= 1 ? room / ratio : room) * scale;
    r.setAttribute('x', String((box - w) / 2)); r.setAttribute('y', String((box - h) / 2));
    r.setAttribute('width', String(Math.max(2, w))); r.setAttribute('height', String(Math.max(2, h)));
    // The same box as style, so a change of ratio eases (frame_picker.css) where the browser animates SVG geometry.
    Object.assign(r.style, {x: `${(box - w) / 2}px`, y: `${(box - h) / 2}px`, width: `${Math.max(2, w)}px`, height: `${Math.max(2, h)}px`});
}
const ratioName = ratio => {
    for (const [a, b] of [[16, 9], [9, 16], [1, 1], [4, 3], [3, 4], [21, 9], [9, 21], [3, 2], [2, 3]]) if (Math.abs(ratio - a / b) < .01) return `${a}:${b}`;
    return ratio >= 1 ? `${ratio.toFixed(2)}:1` : `1:${(1 / ratio).toFixed(2)}`;
};
// A size is named by its megapixels: 1M, 2M, 4M; under one, to one decimal (0.5M). A label given with it is used instead.
export const sizeName = size => size.label || `${size.pixels >= .95 ? Math.round(size.pixels) : Math.round(size.pixels * 10) / 10}M`;
export const pixelsText = (width, height) => `${width}\u2009×\u2009${height}`;

// The same drawing for a frame anywhere else (a chip): a rectangle at the ratio in a square box; dashed when it
// follows something (an image).
export function createFrameIcon(size = 16) {
    const svg = svgBox(size); svg.setAttribute('class', 'fvp-icon'); svg.setAttribute('width', String(size)); svg.setAttribute('height', String(size));
    const frame = rect(svg, 'fvp-frame');
    return {element: svg, set(ratio, follow = false) { place(frame, ratio > 0 ? ratio : 1, size); frame.classList.toggle('fvp-follow', follow); }};
}

export function frameSize(ratio, pixels, {snap = 32, minSide = 256, maxSide = 4096} = {}) {
    const total = pixels * 1024 ** 2, clamp = v => Math.min(maxSide, Math.max(minSide, Math.round(v / snap) * snap));
    return {width: clamp(Math.sqrt(total * ratio)), height: clamp(Math.sqrt(total / ratio))};
}

// The box of the next generation's empty frame: the ratio fitted inside the room (pad on every side), centred.
export function nextFrame(room, ratio, pad = 0) {
    const w = Math.max(0, room.width - 2 * pad), h = Math.max(0, room.height - 2 * pad);
    const width = Math.min(w, h * ratio), height = width / ratio;
    return {width, height, left: (room.width - width) / 2, top: (room.height - height) / 2};
}

export function createFramePicker({t = en => en, shapes = [], sizes = [], sizeFor = (ratio, pixels) => frameSize(ratio, pixels), custom = null,
    onChange = () => {}, onPreview = () => {}, onPaint = () => {}} = {}) {
    loadStyle();
    shapes = shapes.filter(shape => shape.id !== 'custom'); sizes = sizes.filter(size => !size.more);
    const state = {shape: shapes[0]?.id ?? null, ratio: shapes[0]?.ratio || 16 / 9, pixels: sizes[0]?.pixels ?? 1, width: 0, height: 0, locked: true, disabled: false, limit: null, custom: false, unavailable: {}, capped: '', empty: '', ask: false};
    const element = el('div', 'fvp');
    const emit = (source, side) => onChange({source, side, shape: state.shape, ratio: state.ratio, pixels: state.pixels, width: state.width, height: state.height, locked: state.locked});
    const ratioOf = shape => shape.ratio || (shape.id === state.shape ? state.ratio : null) || 1;
    const leave = () => onPreview(null);
    const hover = (node, frame) => {
        const show = () => { if (!node.disabled) onPreview(frame()); };
        node.addEventListener('pointerenter', show); node.addEventListener('focus', show);
        node.addEventListener('pointerleave', leave); node.addEventListener('blur', leave);
    };
    function group(label, cls) {
        const box = el('div', 'fvp-group'), title = el('span', 'fvp-title', label), items = el('div', cls);
        title.id = `fvp-${Math.random().toString(36).slice(2, 8)}`; items.setAttribute('role', 'radiogroup'); items.setAttribute('aria-labelledby', title.id);
        box.append(title, items); element.append(box);
        return items;
    }

    // Frame: one tile a shape.
    const frameRow = group(t('Frame', '画幅'), 'fvp-shapes');
    const shapeItems = shapes.map(shape => {
        const b = el('button', 'fvp-shape'); b.type = 'button'; b.dataset.shape = shape.id; b.setAttribute('role', 'radio');
        const art = el('span', 'fvp-art'), icon = createFrameIcon(20); art.append(icon.element);
        if (shape.thumb) { const img = el('img', 'fvp-thumb'); img.alt = ''; img.draggable = false; img.src = shape.thumb; art.append(img); }
        const name = el('span', 'fvp-name', shape.ratio ? ratioName(shape.ratio) : shape.label);
        b.append(art, name); b.setAttribute('aria-label', shape.ratio ? `${shape.label} ${ratioName(shape.ratio)}` : shape.label);
        b.onclick = () => {
            if (state.disabled) return;
            state.capped = ''; state.empty = ''; state.shape = shape.id; if (shape.ratio) state.ratio = shape.ratio; Object.assign(state, sizeFor(state.ratio, state.pixels));
            paint(); emit('shape');
        };
        hover(b, () => { const ratio = ratioOf(shape); return {ratio, ...sizeFor(ratio, state.pixels)}; });
        frameRow.append(b);
        return {b, shape, icon};
    });

    // Size: the main sizes, then width × height and the lock.
    const sizeRow = group(t('Size', '尺寸'), 'fvp-sizes');
    const sizeItems = sizes.map(size => {
        const b = el('button', 'fvp-size'); b.type = 'button'; b.dataset.pixels = String(size.pixels); b.setAttribute('role', 'radio');
        const px = el('span', 'fvp-px'), name = el('span', 'fvp-sub', sizeName(size));
        b.append(px, name);
        b.onclick = () => {
            if (state.disabled) return;
            state.capped = ''; state.empty = ''; state.pixels = size.pixels; state.custom = false; Object.assign(state, sizeFor(state.ratio, size.pixels));
            paint(); emit('size');
        };
        hover(b, () => ({ratio: state.ratio, ...sizeFor(state.ratio, size.pixels)}));
        sizeRow.append(b);
        return {b, size, px};
    });
    let more = null;
    if (custom) {
        const {minSide = 256, maxSide = 4096, snap = 32, lock = true, keepEmpty = false} = custom;
        more = {panel: el('div', 'fvp-custom'), width: el('input', 'fvp-dim'), height: el('input', 'fvp-dim'), lock: el('button', 'fvp-lock'), status: el('p', 'fvp-status')};
        const {panel, width, height} = more;
        const field = (input, label, short) => {
            input.type = 'number'; input.min = String(minSide); input.max = String(maxSide); input.step = String(snap); input.setAttribute('aria-label', label); input.inputMode = 'numeric';
            const box = el('label', 'fvp-field'); box.append(el('span', 'fvp-field-name', short), input); return box;
        };
        more.lock.type = 'button'; more.lock.hidden = !lock;
        const closed = svgBox(16), shackle = document.createElementNS(SVG, 'path'), bodyRect = document.createElementNS(SVG, 'rect');
        bodyRect.setAttribute('x', '3.5'); bodyRect.setAttribute('y', '7.5'); bodyRect.setAttribute('width', '9'); bodyRect.setAttribute('height', '6'); bodyRect.setAttribute('rx', '1.5');
        closed.append(bodyRect, shackle); more.shackle = shackle; more.lock.append(closed);
        panel.append(field(width, t('Width', '宽'), t('W', '宽')), el('span', 'fvp-times', '×'), field(height, t('Height', '高'), t('H', '高')), more.lock);
        more.status.hidden = true; more.status.setAttribute('role', 'status');
        sizeRow.append(panel); element.append(more.status);
        const fit = v => Math.min(maxSide, Math.max(minSide, Math.round(Number(v) / snap) * snap));
        const typed = which => {
            if (state.disabled) return;
            // A side left empty stays empty (Generate then asks for it); it is not filled with the smallest size.
            // With the ratio locked, a side typed after the other was cleared fills both again.
            const other = which === 'width' ? 'height' : 'width', blank = input => !String(input.value).trim();
            if (keepEmpty && blank(more[which])) { state.empty = which; paint(); emit('empty', which); return; }
            if (keepEmpty && !state.locked && blank(more[other])) { state[which] = fit(more[which].value); state.empty = other; paint(); emit('empty', other); return; }
            const w = fit(keepEmpty ? width.value || state.width : width.value), h = fit(keepEmpty ? height.value || state.height : height.value);
            if (!(w > 0 && h > 0)) { paint(); return; }
            state.empty = '';
            if (state.locked) { if (which === 'width') { state.width = w; state.height = fit(w / state.ratio); } else { state.height = h; state.width = fit(h * state.ratio); } }
            else { state.width = w; state.height = h; state.ratio = w / h; state.shape = 'custom'; }
            // More than this computer can make: the largest size at this shape is used, and the line under the fields says so.
            const most = state.limit?.pixels;
            state.capped = '';
            if (most > 0 && state.width * state.height / 1024 ** 2 > most * 1.001) { const d = sizeFor(state.ratio, most); state.width = d.width; state.height = d.height; state.capped = pixelsText(d.width, d.height); }
            state.custom = true; state.pixels = state.width * state.height / 1024 ** 2; paint(true); emit('dimensions', which);
        };
        width.onchange = () => typed('width'); height.onchange = () => typed('height');
        for (const input of [width, height]) hover(input, () => ({ratio: state.width / state.height || state.ratio, width: state.width, height: state.height}));
        more.lock.onclick = () => { if (state.disabled) return; state.locked = !state.locked; paint(); emit('lock'); };
    }

    function paint(typed = false) {
        element.toggleAttribute('data-disabled', state.disabled);
        for (const {b, shape, icon} of shapeItems) {
            const why = state.unavailable?.[shape.id] || '';
            b.setAttribute('aria-checked', String(shape.id === state.shape)); b.disabled = state.disabled || Boolean(why);
            if (why) b.dataset.fvTipWhy = why; else delete b.dataset.fvTipWhy;
            icon.set(ratioOf(shape), shape.ratio == null);
        }
        // A size block is lit when the size in force is exactly its size at this frame; otherwise the typed fields are.
        let preset = false;
        for (const {b, size, px} of sizeItems) {
            const dims = sizeFor(state.ratio, size.pixels), on = !state.empty && dims.width === state.width && dims.height === state.height;
            // A size this computer cannot make is listed, greyed, and says so where its pixels would be.
            const above = state.limit?.pixels > 0 && size.pixels > state.limit.pixels * 1.001;
            preset ||= on;
            b.setAttribute('aria-checked', String(on)); b.disabled = state.disabled || above; b.toggleAttribute('data-above', above);
            px.textContent = above ? t("Above this computer's limit", '超出本机上限') : pixelsText(dims.width, dims.height);
        }
        state.custom = !preset;
        if (more) {
            const {width, height, lock, status, panel, shackle} = more;
            panel.dataset.on = String(!preset && state.width > 0);
            width.disabled = height.disabled = lock.disabled = state.disabled;
            if (typed || document.activeElement !== width) width.value = state.empty === 'width' ? '' : String(state.width || '');
            if (typed || document.activeElement !== height) height.value = state.empty === 'height' ? '' : String(state.height || '');
            lock.setAttribute('aria-pressed', String(state.locked));
            lock.setAttribute('aria-label', state.locked ? t('Ratio locked', '比例锁定') : t('Ratio free', '比例不锁'));
            shackle.setAttribute('d', state.locked ? 'M5.5 7.5V5.5a2.5 2.5 0 0 1 5 0v2' : 'M5.5 7.5V5.5a2.5 2.5 0 0 1 4.8-1');
            const limit = state.limit;
            // Generate stopped on an empty side (the caller sets ask): the panel covers the studio's status line, so it asks here.
            if (!state.empty) state.ask = false;
            const ask = state.ask && (state.empty === 'height' ? t('Fill in the height.', '请填写高度。') : t('Fill in the width.', '请填写宽度。'));
            const said = state.capped ? t(`The largest on this computer is ${state.capped}; that size is used.`, `本机最大 ${state.capped}，已按最大生成。`) : ask || limit?.note || '';
            status.textContent = said; status.hidden = !said; status.toggleAttribute('data-capped', Boolean(state.capped || ask));
        }
        onPaint();
    }
    paint();
    return {element, state, shapes, sizes, parts: {frame: frameRow, size: sizeRow, status: more?.status || null},
        // Whatever the caller accepted (or its own state): the picker shows it; nothing is emitted.
        set(next = {}) {
            Object.assign(state, Object.fromEntries(Object.entries(next).filter(([k]) => k !== 'range')));
            if (!(state.width > 0 && state.height > 0)) Object.assign(state, sizeFor(state.ratio, state.pixels));
            paint();
        },
        dispose() { element.remove(); }};
}

// Where a panel goes: right at the button that opened it. Its lower edge 8 px over the button (its upper edge 8 px under
// it when there is more room there, or when asked 'below'); its left edge on the button's (moved left where the window
// ends), or, for the keys at the right end of the console's row, its right edge on the button's; kept 16 px inside the
// window (12 on a phone). It may cover the description, never the button itself or a Generate key (a panel that would
// reach over one ends 8 px above it), and never the top bar: where the room is too short, the panel takes the room there
// is and scrolls inside. (Exported for every panel of the console's row, the trays shown as panels and the image view,
// so they all open the same way.)
export function placePop(element, pop, placement = 'auto') {
    const a = element.getBoundingClientRect(), edge = innerWidth <= 520 ? 12 : 16, gap = 8;
    const scope = element.closest('dialog') || document;
    const bar = scope.querySelector('.fv-topbar')?.getBoundingClientRect().bottom || 0, roof = Math.max(edge, bar + gap);
    pop.style.maxHeight = '';
    const w = pop.offsetWidth, h = pop.offsetHeight;
    // The keys after the row's spacer (LoRA and Advanced, at the right of the console's row) line up their right edges.
    const spacer = element.closest('[role=toolbar]')?.querySelector(':scope > .fvs-chip-spacer');
    const end = Boolean(spacer && spacer.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING);
    const x = Math.round(Math.max(edge, Math.min(end ? a.right - w : a.left, innerWidth - edge - w)));
    // The Generate keys the panel must leave in sight (the console's, the image view's), other than the button itself.
    const keys = [...scope.querySelectorAll('.fv-runrow .fv-primary, .fv-iv-go, [data-fv-pop-keep]')].filter(key => key !== element && !key.contains(element))
        .map(key => key.getBoundingClientRect()).filter(k => k.width && k.left < x + w && k.right > x);
    let bottom = a.top - gap;
    for (const k of keys) if (k.top < bottom && k.bottom > bottom - h) bottom = Math.min(bottom, k.top - gap);
    const above = bottom - roof, below = innerHeight - edge - (a.bottom + gap);
    const up = placement === 'below' ? below < Math.min(h, 160) && above > below : above >= h || above >= below || placement === 'above' && above >= 160;
    const height = Math.max(0, Math.min(h, up ? above : below));
    if (height < h) pop.style.maxHeight = `${Math.floor(height)}px`;
    pop.dataset.side = up ? 'above' : 'below';
    pop.style.left = `${x}px`;
    pop.style.top = `${Math.round(up ? bottom - height : a.bottom + gap)}px`;
}
// A bar that asks before going on (a download, a licence: prompt_enhance.js's consent) sends this event from where it
// shows; every open panel of the toolbars closes for it (these, and the trays shown as panels: tray_pop.js), so none
// covers the question.
export const ASKING = 'freevideo-asking';
// Text fields in a panel show their focus by their frame, for the keyboard only: a focus that follows a press marks the
// panel as used by pointer; a focus that comes without one (Tab) clears the mark. (In a view, keys reach only the
// handlers it routes to, so the mark follows focus, not keys.)
function pointerMarks(pop) {
    let pressed = 0;
    pop.addEventListener('pointerdown', () => { pressed = performance.now(); pop.dataset.pointer = 'true'; }, true);
    pop.addEventListener('focusin', () => { if (performance.now() - pressed > 150) delete pop.dataset.pointer; });
}

// The control the views show: one button that says the frame and the size ([▭ 16:9 · 1M]) and opens a small panel
// beside it with both: Frame (every shape in one segmented strip), Size (the sizes in another), and under them a
// quiet row for a typed width × height with the ratio lock.
//
//   const chip = createFrameChip({...the picker's options, placement: 'above' | 'below' | 'auto', host, extra, onClose});
//   row.append(chip.element); chip.set({...as the picker});
//
// host:  where the panel lives from the start (else it is added to the view when first opened).
// extra: an element shown under the size row (what else the view keeps with the frame; its empty parts take no room).
//
// A press outside or Escape closes the panel and gives the button the focus back; the arrow keys move between the
// options of a strip. In each strip one highlight slides to the chosen option.
export function createFrameChip({placement = 'auto', host = null, extra = null, onOpen = () => {}, onClose = () => {}, onChange = () => {}, onPreview = () => {}, ...options} = {}) {
    const t = options.t || (en => en);
    let open = false, timer = 0;
    const picker = createFramePicker({...options, onPreview, onChange, onPaint: () => queueMicrotask(sync)});
    const element = el('button', 'fvp-chip'); element.type = 'button'; element.setAttribute('aria-haspopup', 'dialog'); element.setAttribute('aria-expanded', 'false');
    const icon = createFrameIcon(14), frameName = el('span', 'fvp-chip-label fvs-chip-label'), dot = el('span', 'fvp-chip-dot', '·'), sizeLabel = el('span', 'fvp-chip-label fvs-chip-label');
    dot.setAttribute('aria-hidden', 'true'); element.append(icon.element, frameName, dot, sizeLabel);
    const pop = el('div', 'fvp fvp-pop'); pop.dataset.open = 'false'; pop.hidden = true; pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', t('Frame & size', '画幅与尺寸'));
    const strips = [picker.parts.frame, picker.parts.size].map(list => { const glide = el('span', 'fvp-glide'); glide.setAttribute('aria-hidden', 'true'); list.prepend(glide); return {list, glide}; });
    const custom = picker.parts.size.querySelector(':scope > .fvp-custom');
    const sizeRow = el('div', 'fvp-size-row'); sizeRow.append(picker.parts.size); if (custom) sizeRow.append(custom);
    pop.append(el('span', 'fvp-title', t('Frame', '画幅')), picker.parts.frame, el('span', 'fvp-title', t('Size', '尺寸')), sizeRow);
    if (picker.parts.status) pop.append(picker.parts.status);
    if (extra) { const more = el('div', 'fvp-extra'); more.append(extra); pop.append(more); }
    pointerMarks(pop); if (host) host.append(pop);
    picker.element.remove();
    // The highlight sits behind the chosen option of each strip, and slides there.
    function glide() {
        for (const strip of strips) {
            const on = strip.list.querySelector(':scope > [aria-checked=true]');
            strip.glide.dataset.on = String(Boolean(on));
            if (on) Object.assign(strip.glide.style, {width: `${on.offsetWidth}px`, height: `${on.offsetHeight}px`, transform: `translate(${on.offsetLeft}px, ${on.offsetTop}px)`});
        }
    }
    const place = () => placePop(element, pop, placement);
    function close(refocus = false) {
        if (!open) return; open = false;
        element.setAttribute('aria-expanded', 'false'); pop.dataset.open = 'false';
        clearTimeout(timer); timer = setTimeout(() => { pop.hidden = true; }, 130);
        document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', escape, true); document.removeEventListener('cancel', escape, true);
        document.removeEventListener(ASKING, asked);
        onPreview(null); onClose();
        if (refocus) element.focus({preventScroll: true});
    }
    // Escape closes the panel only, and the button has the focus again. In a view (a modal dialog) the browser may
    // send the dialog's cancel instead of the key, so both are caught before the view's own handlers.
    const escape = event => { if (open && (event.key === 'Escape' || event.type === 'cancel')) { event.preventDefault(); event.stopImmediatePropagation(); close(true); } };
    const outside = event => { if (open && !pop.contains(event.target) && !element.contains(event.target)) close(); };
    const asked = () => close();
    function show() {
        if (open) { close(true); return; }
        if (!host) (element.closest('dialog') || document.body).append(pop);
        clearTimeout(timer); pop.hidden = false; delete pop.dataset.pointer; place(); glide();
        void pop.offsetWidth; pop.dataset.open = 'true'; element.setAttribute('aria-expanded', 'true'); open = true;
        document.addEventListener('pointerdown', outside, true); document.addEventListener('keydown', escape, true); document.addEventListener('cancel', escape, true);
        document.addEventListener(ASKING, asked);
        (picker.parts.frame.querySelector('[aria-checked=true]:not(:disabled)') || pop.querySelector('button:not(:disabled)'))?.focus({preventScroll: true});
        onOpen();
    }
    element.onclick = () => { if (!element.disabled) show(); };
    // (A property, so the view's key routing reaches it.)
    pop.onkeydown = event => {
        const step = {ArrowLeft: -1, ArrowRight: 1}[event.key];
        if (!step || !event.target.matches?.('[role=radio]')) return;
        const all = [...event.target.parentElement.querySelectorAll('[role=radio]')];
        let next = all.indexOf(event.target) + step; while (all[next] && all[next].disabled) next += step;
        if (all[next]) { event.preventDefault(); all[next].focus(); }
    };
    globalThis.addEventListener?.('resize', () => { if (open) { place(); glide(); } });
    function sync() {
        const state = picker.state, shape = picker.shapes.find(s => s.id === state.shape);
        icon.set(state.ratio > 0 ? state.ratio : 1, Boolean(shape && shape.ratio == null));
        // The frame's name: its own, or the listed frame its ratio is, or Custom.
        const like = shape || picker.shapes.find(s => s.ratio && Math.abs(s.ratio - state.ratio) < .01);
        frameName.textContent = like ? (like.ratio ? ratioName(like.ratio) : like.label) : t('Custom', '自定义');
        const preset = picker.sizes.find(s => { const d = (options.sizeFor || ((r, p) => frameSize(r, p)))(state.ratio, s.pixels); return d.width === state.width && d.height === state.height; });
        sizeLabel.textContent = state.empty ? '' : preset ? sizeName(preset) : state.width > 0 ? pixelsText(state.width, state.height) : '';
        element.disabled = Boolean(state.disabled);
        glide(); if (open) place();
    }
    sync();
    return {element, pop, state: picker.state, picker, close, place: () => { if (open) place(); },
        set(next) { picker.set(next); },
        dispose() { close(); clearTimeout(timer); pop.remove(); element.remove(); picker.dispose(); }};
}

// The same kind of button and panel for another setting (quality): the button says the current value after
// a small drawing; the panel beside it holds whatever the caller puts in `body`. It lives in `host` from the start (so
// what is in it is always part of the page) and is shown or hidden; a press outside or Escape closes it.
//
//   const chip = createPopChip({host, name, icon: '<svg path d>', placement, width, onOpen, onClose});
//   chip.body.append(...); chip.setLabel('5 s'); row.append(chip.element);
export function createPopChip({host = null, name = '', icon = '', placement = 'auto', kind = '', onOpen = () => {}, onClose = () => {}} = {}) {
    loadStyle();
    let open = false, timer = 0;
    const element = el('button', 'fvp-chip'); element.type = 'button'; element.setAttribute('aria-haspopup', 'dialog'); element.setAttribute('aria-expanded', 'false'); element.setAttribute('aria-label', name);
    if (icon) { const svg = svgBox(16); svg.setAttribute('class', 'fvp-chip-glyph'); svg.setAttribute('width', '16'); svg.setAttribute('height', '16'); const path = document.createElementNS(SVG, 'path'); path.setAttribute('d', icon); svg.append(path); element.append(svg); }
    const label = el('span', 'fvp-chip-label fvs-chip-label'); element.append(label);
    const pop = el('div', 'fvp fvp-pop fvp-pop-plain'); pop.dataset.open = 'false'; pop.dataset.kind = kind; pop.hidden = true; pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', name);
    const body = el('div', 'fvp-pop-body'); pop.append(el('span', 'fvp-title', name), body);
    (host || document.body).append(pop); pointerMarks(pop);
    const place = () => placePop(element, pop, placement);
    function close(refocus = false) {
        if (!open) return; open = false;
        element.setAttribute('aria-expanded', 'false'); pop.dataset.open = 'false';
        clearTimeout(timer); timer = setTimeout(() => { pop.hidden = true; }, 130);
        document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', escape, true); document.removeEventListener('cancel', escape, true);
        document.removeEventListener(ASKING, asked);
        onClose();
        if (refocus) element.focus({preventScroll: true});
    }
    const escape = event => { if (open && (event.key === 'Escape' || event.type === 'cancel')) { event.preventDefault(); event.stopImmediatePropagation(); close(true); } };
    const outside = event => { if (open && !pop.contains(event.target) && !element.contains(event.target)) close(); };
    const asked = () => close();
    function show() {
        if (open) { close(true); return; }
        clearTimeout(timer); pop.hidden = false; delete pop.dataset.pointer; place();
        void pop.offsetWidth; pop.dataset.open = 'true'; element.setAttribute('aria-expanded', 'true'); open = true;
        onOpen(); place();
        document.addEventListener('pointerdown', outside, true); document.addEventListener('keydown', escape, true); document.addEventListener('cancel', escape, true);
        document.addEventListener(ASKING, asked);
        // The control that holds the value takes the focus (a slider, a chosen option), else the first that can.
        (pop.querySelector('[role=slider][tabindex="0"],[aria-checked=true]:not(:disabled)') || pop.querySelector('input:not(:disabled):not([hidden]),button:not(:disabled)'))?.focus({preventScroll: true, focusVisible: false});
    }
    element.onclick = () => { if (!element.disabled) show(); };
    globalThis.addEventListener?.('resize', () => { if (open) place(); });
    return {element, pop, body, close, show, isOpen: () => open, place: () => { if (open) place(); },
        setLabel(text) { if (label.textContent !== text) label.textContent = text; },
        dispose() { close(); clearTimeout(timer); pop.remove(); element.remove(); }};
}

// area: () => {left, top, width, height} in the stage's own box, the part not covered (by an open tray); the frame
// is fitted and centred there, whole. Outside the frame the stage is dimmed; inside it stays as it is.
export function createFrameGhost(stage, {t = en => en, area = null} = {}) {
    loadStyle();
    const ghost = el('div', 'fvp-ghost'), label = el('span', 'fvp-ghost-label');
    ghost.append(label); ghost.setAttribute('aria-hidden', 'true'); stage.append(ghost);
    let shown = null;
    const fit = () => {
        if (!shown) return;
        const w = stage.clientWidth, h = stage.clientHeight; if (!w || !h) return;
        const free = area?.() || {left: 0, top: 0, width: w, height: h};
        const room = {w: Math.max(32, free.width - 24), h: Math.max(32, free.height - 24)};
        const r = shown.ratio, fw = Math.min(room.w, room.h * r), fh = fw / r;
        ghost.style.width = `${Math.round(fw)}px`; ghost.style.height = `${Math.round(fh)}px`;
        ghost.style.left = `${Math.round(free.left + (free.width - fw) / 2)}px`; ghost.style.top = `${Math.round(free.top + (free.height - fh) / 2)}px`;
    };
    const watch = typeof ResizeObserver === 'function' ? new ResizeObserver(fit) : null; watch?.observe(stage);
    return {element: ghost, fit,
        show(frame) { if (!frame?.ratio) return; shown = frame; label.textContent = pixelsText(frame.width, frame.height); fit(); ghost.dataset.shown = 'true'; },
        hide() { shown = null; ghost.dataset.shown = 'false'; },
        dispose() { watch?.disconnect(); ghost.remove(); }};
}
