// One anchored layer for everything that explains a control on screen: the feature cards, "Take me there", the
// first-run steps, how-to steps and one-line hints. It points at the control it talks about and never blocks the view:
//   level 0  hint       no dim, no ring
//   level 1  point      a ring on the control (the person is about to act on it)
//   level 2  spotlight  a light dim over the whole window with the control cut out (the person reads)
// The layer is a manual popover inside the view it belongs to, so it sits in the top layer above that view (the layout
// never moves) and is not made inert by the view's modal dialog. It takes no pointer input except on its card: a press
// anywhere else reaches what was pressed and puts the layer away on the same pointerdown. Leaving or closing the view,
// another dialog opening, or the control going away puts it away too. Only one layer exists at a time.

const SIDES = ['above', 'below', 'right', 'left'];
const EDGE = 16, PHONE_EDGE = 12, GAP = 12, ARROW_INSET = 20, NOTCH = 16;
// Work bars a card should not sit on: the editor's generate bar, the creation console, anything marked for it.
const BARS = '.fv-ed-genbar,.fvs-console,[data-fv-tour-avoid]';

const area = (x, y, w, h, b) => Math.max(0, Math.min(x + w, b.right) - Math.max(x, b.left)) * Math.max(0, Math.min(y + h, b.bottom) - Math.max(y, b.top));

/**
 * Where the card goes. Pure: everything it needs is passed in.
 * target: the control's rectangle (null: centre in `center`, or the window below the top bar);
 * sizes: {shape: {width, height}} in order of preference; view: {width, height};
 * avoid: rectangles of the user's picture (covering them costs); bars: work bars (generate bar, console) covered only
 * when no other spot fits; keep: rectangles the card must never cover
 * (what the explanation talks about besides the target); prefer: the order of sides to try.
 * Returns {shape, side, left, top, arrow} where arrow is the offset of the arrow along the card's edge (null when centred).
 */
export function placeAnchor({target, sizes, view, avoid = [], keep = [], bars = [], prefer = SIDES, center = null}) {
    const shapes = Object.keys(sizes);
    const edge = view.width < 720 ? PHONE_EDGE : EDGE;
    const centred = shape => {
        const {width: w, height: h} = sizes[shape];
        const box = center || {left: 0, top: 56, width: view.width, height: view.height - 56};
        return {shape, side: 'center', arrow: null,
            left: Math.round(Math.max(edge, Math.min(box.left + (box.width - w) / 2, view.width - w - edge))),
            top: Math.round(Math.max(edge, Math.min(box.top + (box.height - h) / 2, view.height - h - edge)))};
    };
    if (!target) return centred(shapes[0]);
    const r = target, cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const guard = {left: r.left - 8, top: r.top - 8, right: r.right + 8, bottom: r.bottom + 8};
    let best = null;
    shapes.forEach((shape, si) => {
        const {width: w, height: h} = sizes[shape];
        const clampX = x => Math.max(edge, Math.min(x, view.width - w - edge));
        const clampY = y => Math.max(edge, Math.min(y, view.height - h - edge));
        // Each side has three alignments: centred on the target, or flush with its start or end edge.
        const spots = prefer.flatMap(side => side === 'above' || side === 'below'
            ? [clampX(cx - w / 2), clampX(r.left), clampX(r.right - w)].map(x => [side, x, side === 'above' ? r.top - GAP - h : r.bottom + GAP])
            : [clampY(cy - h / 2), clampY(r.bottom - h), clampY(r.top)].map(y => [side, side === 'right' ? r.right + GAP : r.left - GAP - w, y]));
        spots.forEach(([side, x, y], pi) => {
            if (x < edge - .5 || y < edge - .5 || x + w > view.width - edge + .5 || y + h > view.height - edge + .5) return;
            if (area(x, y, w, h, guard)) return;
            if (keep.some(b => area(x, y, w, h, b))) return;
            // The arrow must still reach the target's middle.
            if ((side === 'left' || side === 'right') && (cy < y + ARROW_INSET || cy > y + h - ARROW_INSET)) return;
            if ((side === 'above' || side === 'below') && (cx < x + ARROW_INSET || cx > x + w - ARROW_INSET)) return;
            const cover = avoid.reduce((sum, b) => sum + area(x, y, w, h, b), 0);
            // A bar the person works in (the generate bar, the console) is covered only when nothing else fits.
            const barCover = bars.reduce((sum, b) => sum + area(x, y, w, h, b), 0);
            const score = cover + (barCover ? 100000 + barCover * 8 : 0) + si * 400 + Math.floor(pi / 3) * 50 + (pi % 3) * 10;
            if (!best || score < best.score) best = {score, shape, side, x, y, w, h};
        });
    });
    if (!best) return centred(shapes.at(-1));
    const across = best.side === 'above' || best.side === 'below';
    const arrow = across ? Math.max(ARROW_INSET, Math.min(cx - best.x - NOTCH / 2, best.w - ARROW_INSET - NOTCH))
        : Math.max(ARROW_INSET, Math.min(cy - best.y - NOTCH / 2, best.h - ARROW_INSET - NOTCH));
    return {shape: best.shape, side: best.side, left: Math.round(best.x), top: Math.round(best.y), arrow: Math.round(arrow)};
}

/** The clip path of the dim: the whole window minus a rounded rectangle (even-odd), in px. Pure. */
export function holePath(w, h, x, y, rw, rh, r) {
    r = Math.max(0, Math.min(r, rw / 2, rh / 2));
    const n = v => Math.round(v * 10) / 10;
    return `path(evenodd, "M0 0H${n(w)}V${n(h)}H0Z M${n(x + r)} ${n(y)}H${n(x + rw - r)}A${n(r)} ${n(r)} 0 0 1 ${n(x + rw)} ${n(y + r)}V${n(y + rh - r)}A${n(r)} ${n(r)} 0 0 1 ${n(x + rw - r)} ${n(y + rh)}H${n(x + r)}A${n(r)} ${n(r)} 0 0 1 ${n(x)} ${n(y + rh - r)}V${n(y + r)}A${n(r)} ${n(r)} 0 0 1 ${n(x + r)} ${n(y)}Z")`;
}

// ── The live layer (browser only below this line) ───────────────────────────────────────────────────────────────

let current = null;
// Keys go to the layer before anything else: this listener is registered when the module loads (at startup), so it runs
// ahead of the views' own window-level key routing (dialog_keys.js), which would otherwise swallow Escape.
if (typeof addEventListener === 'function') addEventListener('keydown', event => current?.key(event), true);
const visible = node => {
    if (!node?.isConnected || node.closest('[hidden],[inert]')) return false;
    const r = node.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
};
const rectOf = r => ({left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.right - r.left, height: r.bottom - r.top});
// A target is an element, a selector, a list of either (all of them are lit together), or a function that returns
// {node, rect} (a range on the timeline that is not an element of its own) or null.
function resolve(target, scope) {
    if (!target) return null;
    if (typeof target === 'function') { const found = target(scope); return found?.rect && found.rect.width > 0 && found.rect.height > 0 ? found : null; }
    const list = (Array.isArray(target) ? target : [target])
        .map(item => typeof item === 'string' ? scope.querySelector(item) || document.querySelector(item) : item).filter(visible);
    if (!list.length) return null;
    const rects = list.map(node => node.getBoundingClientRect());
    return {node: list[0], rect: rectOf({left: Math.min(...rects.map(r => r.left)), top: Math.min(...rects.map(r => r.top)),
        right: Math.max(...rects.map(r => r.right)), bottom: Math.max(...rects.map(r => r.bottom))})};
}
export const resolveTarget = resolve;

function styles() {
    if (typeof document === 'undefined') return;
    for (const name of ['theme', 'feature_tour']) if (!document.querySelector(`link[data-fv-tour-${name}]`)) {
        const css = document.createElement('link'); css.rel = 'stylesheet'; css.setAttribute(`data-fv-tour-${name}`, '');
        css.href = new URL(`./${name}.css`, import.meta.url).href; document.head.append(css);
    }
}

export const anchorOpen = () => current;
/** Put the layer away. how: 'now' removes it at once (leaving a view), otherwise it fades out in 160 ms. */
export function closeAnchor(how = 'fade') { return current?.close(how) || Promise.resolve(); }

/**
 * Open the layer. options:
 *  owner     the view dialog it belongs to (default: the top open dialog); it closes with that view
 *  target    what it points at (see resolve); without one the card is centred
 *  keep      selectors or elements the card must not cover (the sound under a range, say)
 *  level     0 hint · 1 point · 2 spotlight
 *  shapes    the card's shapes in order of preference: 'stand', 'wide', 'text', 'hint'
 *  content   the card's inner element(s); the layer adds the arrow
 *  ghost     sweep a soft bar across the target once (a drag gesture)
 *  focus     move focus into the card (only when the person asked for it); automatic cards never take focus
 *  label     the id of the card's title, for aria-labelledby
 *  closeAfter close by itself after this many ms (the "Take me there" ring)
 *  outside   a press outside the card puts the layer away (default); a how-to step keeps it while the person acts
 *  onClose(reason) called once: 'outside' | 'escape' | 'leave' | 'dialog' | 'gone' | 'replace' | 'api' | 'timeout'
 */
export function openAnchor({owner, target = null, keep = [], level = 2, shapes = ['stand'], prefer = SIDES, content = null, ghost = false,
    focus = false, label = '', closeAfter = 0, onClose = null, center = null, outside: outsideCloses = true} = {}) {
    current?.close('now', 'replace');
    styles();
    const top = [...document.querySelectorAll('dialog[open]')].at(-1);
    owner = owner === undefined ? top || null : owner;
    const host = owner?.isConnected && owner.open ? owner : document.body;
    const scope = host === document.body ? document : host;
    const layer = document.createElement('div');
    layer.className = 'fv-tour-layer'; layer.dataset.level = String(level); layer.dataset.fvTipScope = '';
    layer.setAttribute('aria-hidden', content ? 'false' : 'true');
    // The dim (level 2) is one window-sized layer with the target cut out by a clip path: the raster area stays the
    // window, not a 200vmax shadow, and the cut-out keeps the target's rounded corners.
    const dim = document.createElement('div'); dim.className = 'fv-tour-dim'; dim.setAttribute('aria-hidden', 'true'); layer.append(dim);
    const spot = document.createElement('div'); spot.className = 'fv-tour-spot'; spot.setAttribute('aria-hidden', 'true');
    if (ghost) { const bar = document.createElement('span'); bar.className = 'fv-tour-ghost'; spot.append(bar); }
    layer.append(spot);
    let pop = null, arrow = null;
    if (content) {
        pop = document.createElement('section'); pop.className = 'fv-tour-pop fv-glass fv-g3 sgf-avoid';
        pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-modal', 'false');
        if (label) pop.setAttribute('aria-labelledby', label);
        pop.append(...(Array.isArray(content) ? content : [content]));
        arrow = document.createElement('span'); arrow.className = 'fv-tour-notch'; arrow.setAttribute('aria-hidden', 'true');
        pop.append(arrow); layer.append(pop);
    }
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!reduced) layer.dataset.state = 'enter';
    if ('popover' in HTMLElement.prototype) layer.popover = 'manual';
    host.append(layer);
    try { layer.showPopover?.(); } catch { /* Shown in place. */ }
    const restore = document.activeElement;
    let closed = false, timer = 0, frame = 0, lastRect = '';
    const keepNodes = () => (Array.isArray(keep) ? keep : [keep]).map(item => typeof item === 'string' ? scope.querySelector(item) : item).filter(visible);
    function place() {
        frame = 0;
        if (closed) return;
        const found = resolve(target, scope);
        if (target && !found) { close('fade', 'gone'); return; }
        const r = found?.rect || null;
        if (r) {
            const pad = level === 0 ? 0 : 4;
            const radius = found.node instanceof Element ? parseFloat(getComputedStyle(found.node).borderTopLeftRadius) || 8 : 8;
            Object.assign(spot.style, {left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + 2 * pad}px`, height: `${r.height + 2 * pad}px`});
            spot.style.setProperty('--fv-spot-r', `${Math.min(radius, 20) + pad}px`);
            dim.style.clipPath = holePath(innerWidth, innerHeight, r.left - pad, r.top - pad, r.width + 2 * pad, r.height + 2 * pad, Math.min(radius, 20) + pad);
            delete layer.dataset.nospot;
        } else {
            layer.dataset.nospot = '';
            Object.assign(spot.style, {left: '-8px', top: '-8px', width: '0px', height: '0px'});
            dim.style.clipPath = '';
        }
        if (!pop) return;
        const sizes = {};
        for (const shape of shapes) { pop.dataset.shape = shape; sizes[shape] = {width: pop.offsetWidth, height: pop.offsetHeight}; }
        const avoid = [...scope.querySelectorAll('[data-fv-pic],[data-fv-main]')].filter(node => !layer.contains(node) && visible(node)).map(node => node.getBoundingClientRect());
        const bars = [...scope.querySelectorAll(BARS)].filter(node => !layer.contains(node) && visible(node) && !(found?.node instanceof Node && (node.contains(found.node) || found.node.contains(node)))).map(node => node.getBoundingClientRect());
        const spotAt = placeAnchor({target: r, sizes, view: {width: innerWidth, height: innerHeight}, avoid, bars, keep: keepNodes().map(node => node.getBoundingClientRect()),
            prefer, center: center ? rectOf(center.getBoundingClientRect ? center.getBoundingClientRect() : center) : null});
        pop.dataset.shape = spotAt.shape; pop.dataset.side = spotAt.side;
        pop.style.left = `${spotAt.left}px`; pop.style.top = `${spotAt.top}px`;
        arrow.style.left = arrow.style.top = '';
        if (spotAt.arrow != null) arrow.style[spotAt.side === 'above' || spotAt.side === 'below' ? 'left' : 'top'] = `${spotAt.arrow}px`;
    }
    const follow = () => { if (!closed && !frame) frame = requestAnimationFrame(place); };
    // Smooth scrolling, a timeline that redraws, or a panel that slides: follow the target while it moves.
    const watch = () => {
        if (closed) return;
        const found = resolve(target, scope), key = found ? `${Math.round(found.rect.left)},${Math.round(found.rect.top)},${Math.round(found.rect.width)},${Math.round(found.rect.height)}` : '';
        if (key !== lastRect) { lastRect = key; place(); }
        timer = setTimeout(watch, 250);
    };
    const outside = event => { if (outsideCloses && (!pop || !pop.contains(event.target))) close('fade', 'outside'); };
    const key = event => {
        if (event.isComposing) return;
        if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); close('fade', 'escape'); return; }
        if (event.key === 'F6' && pop && !pop.contains(document.activeElement)) { event.preventDefault(); event.stopImmediatePropagation(); first()?.focus(); return; }
        if (!pop?.contains(event.target)) return;
        if (event.key === 'Tab') {
            const items = focusables(); if (!items.length || event.defaultPrevented) return;
            const at = items.indexOf(document.activeElement);
            event.preventDefault(); event.stopImmediatePropagation();
            items[(at + (event.shiftKey ? -1 : 1) + items.length) % items.length].focus();
        } else event.stopImmediatePropagation(); // the view's own shortcuts do not run while a key is pressed on the card
    };
    const focusables = () => pop ? [...pop.querySelectorAll('button:not(:disabled),a[href],video[tabindex="0"]')].filter(node => !node.closest('[hidden]')) : [];
    const first = () => pop?.querySelector('[data-fv-tour-primary]') || focusables()[0];
    const leave = event => {
        const element = event.detail?.element;
        if (event.type === 'freevideo-view-leave') { if (!owner || element === owner || !element) close('now', 'leave'); return; }
        const front = globalThis.FreeVideoShell?.viewDialog?.();
        if (owner && front !== undefined && front !== owner) close('now', 'leave');
    };
    const ownerClosed = () => close('now', 'leave');
    // Another dialog (settings, the command panel, a confirmation) is a higher layer; this one has nothing to say there.
    const dialogs = new MutationObserver(rows => {
        for (const row of rows) {
            const node = row.target;
            if (node instanceof HTMLDialogElement && node.open && node !== owner && !layer.contains(node) && !node.matches('.fv-tip')) { close('fade', 'dialog'); return; }
        }
    });
    dialogs.observe(document.documentElement, {subtree: true, attributes: true, attributeFilter: ['open']});
    const sizeWatch = typeof ResizeObserver === 'function' ? new ResizeObserver(follow) : null;
    if (pop) sizeWatch?.observe(pop);
    document.addEventListener('pointerdown', outside, true);
    addEventListener('resize', follow); addEventListener('scroll', follow, true);
    addEventListener('freevideo-view-leave', leave); addEventListener('freevideo-view', leave);
    owner?.addEventListener('close', ownerClosed);
    function close(how = 'fade', reason = 'api') {
        if (closed) return Promise.resolve();
        closed = true; clearTimeout(timer); clearTimeout(auto); cancelAnimationFrame(frame);
        dialogs.disconnect(); sizeWatch?.disconnect();
        document.removeEventListener('pointerdown', outside, true);
        removeEventListener('resize', follow); removeEventListener('scroll', follow, true);
        removeEventListener('freevideo-view-leave', leave); removeEventListener('freevideo-view', leave);
        owner?.removeEventListener('close', ownerClosed);
        const hadFocus = layer.contains(document.activeElement);
        if (current?.layer === layer) current = null;
        try { onClose?.(reason); } catch (error) { console.warn('FreeVideo tour layer:', error); }
        if (hadFocus && restore?.isConnected && reason !== 'outside') restore.focus({preventScroll: true});
        const remove = () => { try { layer.hidePopover?.(); } catch { /* Not a popover. */ } layer.remove(); };
        if (how === 'now' || reduced) { remove(); return Promise.resolve(); }
        layer.dataset.state = 'leave';
        return new Promise(done => setTimeout(() => { remove(); done(); }, 170));
    }
    const auto = closeAfter ? setTimeout(() => close('fade', 'timeout'), closeAfter) : 0;
    place();
    if (layer.dataset.state === 'enter') requestAnimationFrame(() => requestAnimationFrame(() => { if (!closed) delete layer.dataset.state; }));
    lastRect = ''; timer = setTimeout(watch, 250);
    if (focus) first()?.focus({preventScroll: true});
    current = {layer, pop, owner, close, place, key, closed: () => closed};
    return current;
}
