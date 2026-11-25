// One hover tip for every FreeVideo control: a name, one line on what it does and what you get, and its shortcut.
// - About 500 ms on a control, then at once on its neighbours while one has just shown; gone ~100 ms after leaving.
// - Keyboard focus shows it too; a long press shows it on touch (and that press does not also tap the control).
// - It never covers the control, the menu the control sits in, another open layer, or the picture when there is room
//   elsewhere: below, above, then beside, measured at its real size and always kept inside the window.
// - It floats in the top layer, so it never changes the layout; it fades only, and not at all under reduced motion.
// - A disabled control (disabled or aria-disabled) says why and how to undo it instead of what it does.
// Where the words come from, first match wins:
//   1. data-fv-tip-name / -use / -key / -why on the control (data-fv-tip-off also works), or on a plain wrapper around it;
//   2. data-fv-tip="<id>" naming an entry of tooltip_copy.js;
//   3. tooltip_copy.js MATCHES (selector) and LABELS (label) for controls that carry nothing;
//   4. the control's own title (as the line) and its label (as the name).
// Entries found by 3 are also written onto the control (data-fv-tip-src="copy"), so screen readers and page checks see
// the words a hover shows; the moment a view writes its own attributes, the copy steps aside.
import { TIPS, MATCHES, LABELS } from './tooltip_copy.js';
import { installA11y } from './a11y.js';
import { isChinese } from './i18n.js';

if (globalThis.document?.head && !globalThis.document.head.querySelector?.('link[data-fv-sheet=tooltip]')) {
    const sheet = document.createElement('link');
    sheet.rel = 'stylesheet'; sheet.href = new URL('./tooltip.css', import.meta.url).href; sheet.dataset.fvSheet = 'tooltip';
    document.head.append(sheet);
}

const query = new URLSearchParams(globalThis.location?.search || '');
const cn = isChinese();
const pick = value => Array.isArray(value) ? value[cn ? 1 : 0] : value;

export const SHOW_DELAY = 500, HIDE_DELAY = 100, WARM_FOR = 700, GAP = 8, MARGIN = 8, LONG_PRESS = 500, MAX_WIDTH = 320;
const timing = {show: SHOW_DELAY, hide: HIDE_DELAY, warm: WARM_FOR};
// Kept for views that still import it; scope is decided by inScope() below.
export const SCOPE = '[data-fv-tip-scope]';
export const CONTROL = 'button,a[href],summary,select,label:has(>input[type=checkbox]),input:not([type=hidden]),textarea,[role=button],[role=tab],[role=switch],[role=checkbox],[role=radio],[role=menuitem],[role=menuitemradio],[role=menuitemcheckbox],[role=option],[role=slider],[role=link],[tabindex]:not([tabindex="-1"]),[data-fv-tip],[data-fv-tip-name],[data-fv-tip-use]';
const TIPSEL = '[data-fv-tip-name],[data-fv-tip-use],[data-fv-tip]';
const PLAIN_CONTROL = CONTROL.replace(/,\[data-fv-tip[^\]]*\]/g, '');
const TYPING = 'textarea,[contenteditable=""],[contenteditable=true],input:not([type]),input[type=text],input[type=search],input[type=number],input[type=url],input[type=email],input[type=password]';
// Big regions that take focus for the keyboard (a timeline, a list): their tip shows on keyboard focus, not on hover,
// so working inside them is not interrupted by a card every half second.
const REGION = '[role=region],[role=group],[role=tabpanel],[role=list],[role=grid],[role=listbox],[role=tree],[role=application]';

const disabledOf = element => element.matches(':disabled') || element.getAttribute('aria-disabled') === 'true';
const clean = text => String(text || '').replace(/\s+/g, ' ').trim();
const safeMatches = (element, selector) => { try { return element.matches(selector); } catch { return false; } };

// A FreeVideo surface: the control or a holder has an fv- / sgf- class, or carries data-fv-tip-scope. The walk stops
// below <body>, so html.fv-app does not pull ComfyUI's own controls in.
export function inScope(element) {
    for (let node = element, depth = 0; node && node.nodeType === 1 && depth < 48; node = node.parentElement, depth++) {
        if (node === globalThis.document?.body || node === globalThis.document?.documentElement) return false;
        if (node.hasAttribute?.('data-fv-tip-scope')) return true;
        const names = node.classList;
        if (names) for (const name of names) if (name.startsWith('fv-') || name.startsWith('sgf-')) return true;
    }
    return false;
}

function labelOf(element) {
    return clean(element.getAttribute?.('aria-label')) || clean(element.textContent).slice(0, 60) || clean(element.getAttribute?.('title')) || clean(element.value);
}

// The table's selectors, indexed by what their last step needs (an id, a class, an attribute, else a tag), so a control
// is tested only against the few selectors that could match it, in table order (same answer as trying them all).
function splitTop(text, isSep) {
    const out = []; let depth = 0, quote = '', start = 0;
    for (let i = 0; i < text.length; i++) {
        const c = text[i];
        if (quote) { if (c === quote) quote = ''; continue; }
        if (c === '"' || c === "'") quote = c;
        else if (c === '(' || c === '[') depth++;
        else if (c === ')' || c === ']') depth--;
        else if (!depth && isSep(c)) { out.push(text.slice(start, i)); start = i + 1; }
    }
    out.push(text.slice(start));
    return out.map(part => part.trim()).filter(Boolean);
}
const INDEX = (() => {
    const byKey = new Map(), loose = [];
    MATCHES.forEach(([selector, id], order) => {
        for (const part of splitTop(selector, c => c === ',')) {
            const last = (splitTop(part, c => c === ' ' || c === '>' || c === '+' || c === '~').at(-1) || '').replace(/:[\w-]+\((?:[^()]|\([^()]*\))*\)/g, '');
            const hash = last.match(/#([\w-]+)/), cls = last.match(/\.([\w-]+)/), attr = last.match(/\[([\w-]+)/), tag = last.match(/^([a-z][\w-]*)/i);
            const key = hash ? '#' + hash[1] : cls ? '.' + cls[1] : attr ? '[' + attr[1] : tag ? tag[1].toLowerCase() : null;
            const row = {order, selector: part, id};
            if (key) { if (!byKey.has(key)) byKey.set(key, []); byKey.get(key).push(row); } else loose.push(row);
        }
    });
    return {byKey, loose};
})();
// The table entry a selector gives this control: {id, entry}, null (matched, no entry), or undefined (no selector matches).
// Candidate rows depend only on the control's shape (tag, id, classes, attribute names); cards in a grid share one.
const shapes = new Map();
function candidatesOf(element) {
    const names = element.getAttributeNames?.() || [];
    const shape = `${element.tagName}#${element.id || ''}.${[...(element.classList || [])].join('.')}[${names.join(',')}`;
    let rows = shapes.get(shape);
    if (rows) return rows;
    rows = [];
    const add = list => { if (list) rows.push(...list); };
    if (element.id) add(INDEX.byKey.get('#' + element.id));
    for (const name of element.classList || []) add(INDEX.byKey.get('.' + name));
    for (const name of names) add(INDEX.byKey.get('[' + name));
    add(INDEX.byKey.get(String(element.tagName).toLowerCase()));
    add(INDEX.loose);
    rows.sort((a, b) => a.order - b.order);
    if (shapes.size > 2000) shapes.clear();
    shapes.set(shape, rows);
    return rows;
}
function matchFor(element) {
    for (const row of candidatesOf(element)) if (safeMatches(element, row.selector)) return TIPS[row.id] ? {id: row.id, entry: TIPS[row.id]} : null;
    return undefined;
}

// The copy-table entry for a control that carries no words of its own, or null.
export function copyFor(element) {
    const matched = matchFor(element);
    if (matched !== undefined) return matched;
    const label = labelOf(element);
    if (!label) return null;
    for (const [pattern, id, within] of LABELS) {
        if (!pattern.test(label)) continue;
        if (within && !element.closest?.(within)) continue;
        return TIPS[id] ? {id, entry: TIPS[id]} : null;
    }
    return null;
}

// The element whose data-fv-tip-* belong to this control: the control itself, the field a label wraps, or a plain
// (non-interactive) wrapper such as a select host. Another control's words (a card around a button) are not borrowed.
function holderOf(element) {
    if (safeMatches(element, TIPSEL)) return element;
    const field = element.tagName === 'LABEL' ? element.control || element.querySelector?.('input,select,textarea') : null;
    if (field && safeMatches(field, TIPSEL)) return field;
    const wrapper = element.parentElement?.closest?.(TIPSEL);
    if (wrapper && !safeMatches(wrapper, PLAIN_CONTROL)) return wrapper;
    return null;
}

// The words for one control, or null when there is nothing worth saying.
export function tipContent(element) {
    if (!element) return null;
    const holder = holderOf(element);
    const data = holder?.dataset || {};
    let name = data.fvTipName || '', use = data.fvTipUse || '', key = data.fvTipKey || '', off = data.fvTipWhy || data.fvTipOff || '';
    let entry = data.fvTip ? TIPS[data.fvTip] : null;
    if (!entry && !name && !use) {
        const field = element.tagName === 'LABEL' ? element.control : null;
        entry = (copyFor(element) || (field && copyFor(field)))?.entry || null;
    }
    const title = clean(element.dataset?.fvTitle ?? element.getAttribute('title'));
    const label = clean(element.getAttribute('aria-label')) || clean(element.textContent).slice(0, 40);
    if (entry) { name ||= (entry.byLabel && label) || pick(entry.name) || ''; use ||= pick(entry.use) || ''; key ||= entry.key || ''; off ||= pick(entry.off) || ''; }
    if (!name && !use) { if (!title) return null; name = label && label !== title ? label : ''; use = title; }
    if (!name) name = label;
    const disabled = disabledOf(element);
    if (disabled && !off && !entry) off = pick(copyFor(element)?.entry?.off) || '';
    if (disabled && !off && title && title !== use && title !== name) off = title;
    if (!name && !use && !(disabled && off)) return null;
    return {name: clean(name), use: clean(use) === clean(name) ? '' : clean(use), key: clean(key), off: disabled ? clean(off) : ''};
}

// ── Placement (pure, tested in tests/tips_tooltip.mjs) ──────────────────────────────────────────
const overlapArea = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));

// Where the tip goes. target: the control's rect; size: the tip's measured size; view: the window.
// prefer: the order of sides to try (default below, above, right, left; above first in the lower part of the window);
// avoid: rects the tip should not cover (other open layers, the picture).
// around: the floating layer the control sits in (a menu, a popover, a tray); the tip goes outside it, lined up with
// the control, so it never covers the layer's own words. Without room outside it, the control alone counts.
// The first side that fits and covers nothing wins; otherwise the side that fits and covers least; if no side fits,
// the side with the most room, pushed back inside the window. The tip never leaves the window.
export function placeTip(target, size, view, {prefer = null, avoid = [], around = null} = {}) {
    if (around) {
        const outside = placeAt(target, size, view, {prefer, avoid, edge: around});
        if (outside.fits) return outside;
    }
    return placeAt(target, size, view, {prefer, avoid, edge: target});
}
function placeAt(target, size, view, {prefer, avoid, edge}) {
    const width = Math.min(size.width, view.width - 2 * MARGIN), height = Math.min(size.height, view.height - 2 * MARGIN);
    const clampX = x => Math.max(MARGIN, Math.min(x, view.width - MARGIN - width));
    const clampY = y => Math.max(MARGIN, Math.min(y, view.height - MARGIN - height));
    const midX = target.left + target.width / 2 - width / 2, midY = target.top + target.height / 2 - height / 2;
    const spots = {
        below: {left: clampX(midX), top: edge.bottom + GAP, fits: edge.bottom + GAP + height <= view.height - MARGIN},
        above: {left: clampX(midX), top: edge.top - GAP - height, fits: edge.top - GAP - height >= MARGIN},
        right: {left: edge.right + GAP, top: clampY(midY), fits: edge.right + GAP + width <= view.width - MARGIN},
        left: {left: edge.left - GAP - width, top: clampY(midY), fits: edge.left - GAP - width >= MARGIN},
    };
    const lower = target.top + target.height / 2 > view.height * 0.6;
    const order = prefer || (lower ? ['above', 'below', 'right', 'left'] : ['below', 'above', 'right', 'left']);
    const covered = spot => avoid.reduce((sum, rect) => sum + overlapArea({left: spot.left, top: spot.top, right: spot.left + width, bottom: spot.top + height}, rect), 0);
    let best = null;
    for (const side of order) {
        const spot = spots[side];
        if (!spot.fits) continue;
        const cover = covered(spot);
        if (!best || cover < best.cover) best = {side, ...spot, cover};
        if (cover === 0) break;
    }
    const fits = !!best;
    if (!best) {
        // Nothing fits whole: the side with the most room, kept inside the window (it may touch the control).
        const room = {below: view.height - target.bottom, above: target.top, right: view.width - target.right, left: target.left};
        const side = order.slice().sort((a, b) => room[b] - room[a])[0];
        const spot = spots[side];
        best = {side, left: clampX(spot.left), top: clampY(spot.top)};
    }
    // Whole pixels, and the rounding never pushes the card past the edge.
    const left = Math.max(MARGIN, Math.min(Math.round(best.left), Math.floor(view.width - MARGIN - width)));
    const top = Math.max(MARGIN, Math.min(Math.round(best.top), Math.floor(view.height - MARGIN - height)));
    return {left, top, side: best.side, width: Math.round(width), fits};
}

// ── The tip itself ─────────────────────────────────────────────────────────────────────────────
let tip = null, shown = null, pending = null, showTimer = 0, hideTimer = 0, warmUntil = 0, pressTimer = 0, pressPoint = null, swallowClick = false, watch = 0, watcher = null;
const reduced = () => globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;

function ensureTip() {
    if (tip?.isConnected) return tip;
    tip = document.createElement('div'); tip.className = 'fv-tip'; tip.id = 'fv-tip'; tip.setAttribute('role', 'tooltip');
    if (tip.showPopover) tip.popover = 'manual';
    document.body.append(tip);
    return tip;
}

// Key glyphs drawn as lines (some systems have no font glyph for them); the words stay for screen readers.
const KEY_GLYPHS = {
    '⌘': ['Command', 'M6 6V4.5A1.5 1.5 0 1 0 4.5 6H6Zm0 0h4M6 6v4m4-4V4.5A1.5 1.5 0 1 1 11.5 6H10Zm0 0v4m0 0h1.5a1.5 1.5 0 1 1-1.5 1.5V10Zm0 0H6m0 0v1.5A1.5 1.5 0 1 1 4.5 10H6Z'],
    '⌥': ['Option', 'M2 4.5h3.5l5 7H14M10 4.5h4'], '⇧': ['Shift', 'M8 2.5 2.5 8.5h3V13h5V8.5h3L8 2.5Z'],
    '↵': ['Return', 'M13 3v5a2 2 0 0 1-2 2H3m0 0 3-3m-3 3 3 3'], '⌫': ['Delete', 'M5.5 3.5H14v9H5.5L1.5 8l4-4.5ZM8 6l4 4m0-4-4 4'],
    '←': ['Left', 'M13 8H3m0 0 4-4M3 8l4 4'], '→': ['Right', 'M3 8h10m0 0-4-4m4 4-4 4'], '↑': ['Up', 'M8 13V3m0 0L4 7m4-4 4 4'], '↓': ['Down', 'M8 3v10m0 0-4-4m4 4 4-4'],
};
function keyCap(text) {
    const key = document.createElement('kbd'); key.className = 'fv-tip-key';
    for (const part of String(text).split(/([⌘⌥⇧↵⌫←→↑↓])/u)) {
        if (!part) continue;
        const glyph = KEY_GLYPHS[part];
        if (!glyph || !document.createElementNS) { key.append(part); continue; }
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', glyph[0]); svg.classList.add('fv-tip-glyph');
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', glyph[1]);
        svg.append(path); key.append(svg);
    }
    return key;
}

function render(content) {
    const box = ensureTip();
    box.replaceChildren();
    const head = document.createElement('div'); head.className = 'fv-tip-head';
    const name = document.createElement('span'); name.className = 'fv-tip-name'; name.textContent = content.name; head.append(name);
    if (content.key) head.append(keyCap(content.key));
    box.append(head);
    if (content.use && !content.off) { const use = document.createElement('div'); use.className = 'fv-tip-use'; use.textContent = content.use; box.append(use); }
    if (content.off) { const off = document.createElement('div'); off.className = 'fv-tip-off'; off.textContent = content.off; box.append(off); }
    box.dataset.state = content.off ? 'off' : 'on';
}

const visibleRect = element => { const rect = element.getBoundingClientRect(); return rect.width > 0 && rect.height > 0 ? rect : null; };

// What the tip should keep clear of, and which sides to try first.
function surroundings(element) {
    const avoid = [];
    let prefer = null;
    let around = null;
    const menu = holderLayer(element);
    if (menu) {
        const rect = visibleRect(menu);
        if (rect && rect.height <= innerHeight * 0.6 && rect.width * rect.height < innerWidth * innerHeight * 0.45) {
            around = rect;
            if (menu.matches('[role=menu],[role=listbox],.fv-select-menu')) prefer = ['right', 'left', 'below', 'above'];
        }
    }
    for (const layer of document.querySelectorAll('[popover]:popover-open,[role=menu],[role=listbox],.fv-select-menu')) {
        if (layer === tip || layer === menu || layer.contains(element) || layer.matches('.sgf,.fv-tip')) continue;
        const rect = visibleRect(layer);
        if (rect && rect.width * rect.height < innerWidth * innerHeight * 0.5) avoid.push(rect);
    }
    // The user's picture: the main block's video, image or canvas, unless the control sits on it.
    for (const picture of document.querySelectorAll('[data-fv-main] :is(video,img,canvas),.fv-library-viewer :is(video,img)')) {
        if (picture.contains(element)) continue;
        const rect = visibleRect(picture);
        if (rect && !(element.getBoundingClientRect().top >= rect.top && element.getBoundingClientRect().bottom <= rect.bottom &&
            element.getBoundingClientRect().left >= rect.left && element.getBoundingClientRect().right <= rect.right)) avoid.push(rect);
    }
    return {avoid, prefer, around};
}

// The floating layer a control sits in: a menu, a popover, a panel that says it is a dialog, or an open tray that
// floats (an open <details> taken out of the flow). Not the view itself, the top bar or the floating task window.
function holderLayer(element) {
    for (let node = element.parentElement, depth = 0; node && depth < 24; node = node.parentElement, depth++) {
        if (node === document.body || node.matches('dialog.fv-studio,[data-fv-surface=view],.fv-header,.sgf')) return null;
        if (node.matches('[role=menu],[role=listbox],[role=menubar],.fv-select-menu,[popover],[role=dialog]:not(dialog)')) return node;
        if (node.matches('details[open]')) { const position = getComputedStyle(node).position; if (position === 'absolute' || position === 'fixed') return node; }
    }
    return null;
}

function position(element) {
    const box = ensureTip();
    const rect = element.getBoundingClientRect();
    if (!rect.width && !rect.height) return false;
    // Measure the real card: open but invisible, at the corner, at its final width limit.
    box.style.visibility = 'hidden'; box.style.left = '0px'; box.style.top = '0px';
    box.style.maxWidth = `${Math.min(MAX_WIDTH, innerWidth - 2 * MARGIN)}px`;
    box.dataset.open = 'true';
    const size = box.getBoundingClientRect();
    const place = placeTip(rect, {width: size.width, height: size.height}, {width: innerWidth, height: innerHeight}, surroundings(element));
    box.style.left = `${place.left}px`; box.style.top = `${place.top}px`; box.dataset.side = place.side;
    box.style.visibility = '';
    return true;
}

function show(element) {
    const content = tipContent(element);
    if (!content || !element.isConnected) return;
    clearTimeout(hideTimer);
    if (shown && shown !== element) release(shown);
    shown = element;
    // The browser's own tooltip would show the same words a second time.
    // It is left empty rather than removed, so a page that removes it while the tip shows is seen doing so.
    if (element.hasAttribute('title')) { element.dataset.fvTitle = element.getAttribute('title'); element.setAttribute('title', ''); }
    render(content);
    const box = ensureTip();
    box.dataset.motion = reduced() ? 'none' : 'fade';
    // Above whatever opened last: re-entering the top layer puts it on top of a newer dialog.
    if (box.showPopover) { try { if (box.matches(':popover-open')) box.hidePopover(); box.showPopover(); } catch { /* Shown as a fixed element. */ } }
    else { const host = [...document.querySelectorAll('dialog[open]')].at(-1) || document.body; if (box.parentNode !== host) host.append(box); }
    if (!position(element)) { hideTip({now: true}); return; }
    const described = element.getAttribute('aria-describedby');
    if (!String(described || '').split(/\s+/).includes('fv-tip')) { element.dataset.fvDescribedby = described ?? ''; element.setAttribute('aria-describedby', [described, 'fv-tip'].filter(Boolean).join(' ')); }
    follow(element);
}

// While a tip shows: new words when the control changes (it turns disabled, Generate becomes Stop), gone when it goes.
function follow(element) {
    watcher?.disconnect();
    if (globalThis.MutationObserver) {
        watcher = new MutationObserver(records => {
            if (shown !== element) return;
            // A title the page sets while the tip shows (Generate's reason, cleared once a prompt is typed) is the tip's
            // new words; one with words goes aside like the first, so the browser's own tooltip does not show them too.
            // A title the page removes takes its words away, and nothing is put back when the tip goes.
            if (records.some(record => record.attributeName === 'title')) {
                if (!element.hasAttribute('title')) delete element.dataset.fvTitle;
                else {
                    element.dataset.fvTitle = element.getAttribute('title');
                    // Emptying it is the tip's own change, not the page's: its record is dropped.
                    if (element.dataset.fvTitle) { element.setAttribute('title', ''); watcher.takeRecords(); }
                }
            }
            const content = tipContent(element);
            if (!content) { hideTip({now: true}); return; }
            render(content); position(element);
        });
        watcher.observe(element, {attributes: true, attributeFilter: ['disabled', 'aria-disabled', 'data-fv-tip-name', 'data-fv-tip-use', 'data-fv-tip-key', 'data-fv-tip-why', 'data-fv-tip-off', 'data-fv-tip', 'aria-label', 'title']});
    }
    clearInterval(watch);
    watch = setInterval(() => {
        if (shown !== element) { clearInterval(watch); return; }
        const rect = element.isConnected ? element.getBoundingClientRect() : null;
        if (!rect || (!rect.width && !rect.height) || element.closest('[inert],[hidden]')) hideTip({now: true});
    }, 250);
}

function release(element) {
    if (!element) return;
    // The words go back only where the tip left the title empty; a title the page set or removed since stays as it is.
    if (element.dataset.fvTitle !== undefined) { if (element.getAttribute('title') === '') element.setAttribute('title', element.dataset.fvTitle); delete element.dataset.fvTitle; }
    if (element.dataset.fvDescribedby !== undefined) {
        const before = element.dataset.fvDescribedby; delete element.dataset.fvDescribedby;
        if (before) element.setAttribute('aria-describedby', before); else element.removeAttribute('aria-describedby');
    }
}

// Show a control's tip now, as a long press does, and take it away a moment later: for a click on a control that
// cannot change, which answers with the same words its hover gives instead of doing nothing.
export function showTipFor(element) {
    if (!element?.isConnected) return;
    show(element);
    setTimeout(() => { if (shown === element) hideTip(); }, 1800);
}

export function hideTip({now = false} = {}) {
    clearTimeout(showTimer); clearTimeout(hideTimer); showTimer = 0; pending = null;
    const finish = () => {
        if (!shown) return;
        warmUntil = Date.now() + timing.warm;
        release(shown); shown = null; watcher?.disconnect(); clearInterval(watch);
        if (tip) { delete tip.dataset.open; try { if (tip.matches(':popover-open')) tip.hidePopover(); } catch { /* Already hidden. */ } }
    };
    if (now) finish(); else hideTimer = setTimeout(finish, timing.hide);
}

function controlAt(target) {
    if (!(target instanceof Element) || target.closest('.fv-tip')) return null;
    const control = target.closest(CONTROL);
    if (!control || control.closest('[inert]') || !inScope(control)) return null;
    // Typing fields keep the caret clear; they show a tip only when one was written for them.
    if (control.matches(TYPING) && !holderOf(control)) return null;
    return control;
}

const keyboardOnly = control => safeMatches(control, REGION) && !safeMatches(control, 'button,[role=button],[role=tab],[role=option],[role=menuitem],[role=switch],[role=checkbox],[role=radio],[role=slider],a[href],summary');

function schedule(control, delay, {keyboard = false} = {}) {
    clearTimeout(hideTimer);
    if (shown === control || (pending === control && showTimer)) return;
    clearTimeout(showTimer); showTimer = 0; pending = null;
    if (!keyboard && keyboardOnly(control)) { if (shown) hideTip(); return; }
    const warm = shown || Date.now() < warmUntil;
    if (warm || delay === 0) { show(control); return; }
    pending = control;
    showTimer = setTimeout(() => {
        showTimer = 0; pending = null;
        if (buttonsDown && !control.matches(':focus-visible')) return;
        if (control.isConnected && (control.matches(':hover,:focus-visible') || hovered === control)) show(control);
    }, delay);
}

let hovered = null, buttonsDown = 0;
function onPointerOver(event) {
    if (event.pointerType === 'touch') return;
    // A drag (a clip, a handle, a slider) is under way: no tip appears, and one that shows goes at once.
    if (event.buttons) { if (shown || showTimer) hideTip({now: true}); return; }
    const control = controlAt(event.target);
    hovered = control;
    if (control) schedule(control, timing.show);
    else if (shown || showTimer) hideTip();
}
function onFocusIn(event) {
    const control = controlAt(event.target);
    if (control && control.matches(':focus-visible')) schedule(control, timing.show, {keyboard: true});
}
function onPointerDown(event) {
    buttonsDown = event.buttons || 1;
    hideTip({now: true}); warmUntil = 0;
    if (event.pointerType !== 'touch') return;
    const control = controlAt(event.target);
    if (!control) return;
    pressPoint = {x: event.clientX, y: event.clientY};
    clearTimeout(pressTimer);
    pressTimer = setTimeout(() => { pressPoint = null; show(control); swallowClick = !!shown; setTimeout(() => hideTip(), 1800); }, LONG_PRESS);
}
function onPointerMove(event) {
    if (event.buttons && (shown || showTimer) && !pressPoint) hideTip({now: true});
    if (!pressPoint) return;
    if (Math.hypot(event.clientX - pressPoint.x, event.clientY - pressPoint.y) > 8) { clearTimeout(pressTimer); pressPoint = null; }
}
function onPointerEnd(event) { buttonsDown = event?.buttons || 0; clearTimeout(pressTimer); pressPoint = null; }
function onClick(event) {
    // The long press that showed the tip is not also a tap on the control.
    if (swallowClick) { swallowClick = false; event.preventDefault(); event.stopPropagation(); }
}

// ── Copying table words onto controls ──────────────────────────────────────────────────────────
const stamped = new WeakMap();
const FIELDS = [['name', 'fvTipName'], ['use', 'fvTipUse'], ['key', 'fvTipKey'], ['why', 'fvTipWhy']];

function stampOne(element, found) {
    const before = stamped.get(element);
    if (before) {
        // A view wrote over the copy: it is theirs from now on.
        if (FIELDS.some(([field, prop]) => (element.dataset[prop] || '') !== (before[field] || ''))) { stamped.delete(element); delete element.dataset.fvTipSrc; return; }
    } else if (safeMatches(element, TIPSEL)) {
        // The view's own words stay; only a missing reason for a greyed control comes from the table.
        const why = pick(found.entry.off);
        if (why && !element.dataset.fvTipWhy && !element.dataset.fvTipOff) { element.dataset.fvTipWhy = why; element.dataset.fvTipWhySrc = 'copy'; }
        return;
    }
    if (element.matches(TYPING)) return;
    // A name that is the control's own label is read at hover time (labels change: Next becomes Done), not copied.
    const words = {name: found.entry.byLabel ? '' : pick(found.entry.name) || '', use: pick(found.entry.use) || '', key: found.entry.key || '', why: pick(found.entry.off) || ''};
    const own = before ? {} : {key: element.dataset.fvTipKey, why: element.dataset.fvTipWhy};
    const write = {name: words.name, use: words.use, key: own.key || words.key, why: own.why || words.why};
    for (const [field, prop] of FIELDS) { if (write[field]) element.dataset[prop] = write[field]; else delete element.dataset[prop]; }
    element.dataset.fvTipSrc = 'copy';
    stamped.set(element, write);
}

// What one scan looks at: anything that can be pressed or focused (one light query per added subtree, no :has()).
const SCAN = 'button,a[href],summary,select,input:not([type=hidden]),textarea,[role=button],[role=tab],[role=switch],[role=checkbox],[role=radio],[role=menuitem],[role=menuitemradio],[role=menuitemcheckbox],[role=option],[role=slider],[role=link],[tabindex],[data-fv-tip]';
function collect(root) {
    if (!root?.querySelectorAll) return [];
    const list = [...root.querySelectorAll(SCAN)];
    if (root.matches?.(SCAN)) list.unshift(root);
    return list;
}
const labelHit = element => {
    const label = labelOf(element);
    const hit = label && LABELS.find(([pattern, , within]) => pattern.test(label) && (!within || element.closest(within)));
    return hit && TIPS[hit[1]] ? {id: hit[1], entry: TIPS[hit[1]]} : null;
};

// One control: the table's words when it has none, a reason when it has words but none for being greyed.
function stampElement(element) {
    if (!inScope(element)) return false;
    if (safeMatches(element, TIPSEL) && !stamped.has(element)) {
        if (element.dataset.fvTipWhy || element.dataset.fvTipOff) return false;
        const found = matchFor(element) || labelHit(element);
        if (!found?.entry.off) return false;
        stampOne(element, found); return true;
    }
    if (element.matches(TYPING)) return false;
    let found = matchFor(element);
    if (found === undefined) {
        const title = clean(element.getAttribute('title'));
        if (title && title !== labelOf(element)) return false;   // its title already says what it does
        found = labelHit(element);
    }
    if (!found) return false;
    stampOne(element, found); return true;
}

// Write the table's words onto the controls under `root` that have none (root itself included); synchronous.
export function stampTips(root = globalThis.document) {
    let count = 0;
    for (const element of collect(root)) if (stampElement(element)) count++;
    return count;
}

// An icon-only control (no words you can read, no aria-label) takes its tip's name as its accessible name, and keeps
// it in step when the view renames the control (Generate becomes Stop).
const NAMED = 'button,a[href],summary,[role=button],[role=tab],[role=switch],[role=menuitem],[role=menuitemradio],[role=option],[role=link]';
// readLayout: false defers a control whose words sit in a child (only innerText can tell whether they are drawn, and
// reading it right after a re-render forces a layout); it returns 'later' for those.
function nameOne(element, {readLayout = true} = {}) {
    if (element.dataset.fvLabelSrc !== 'tip' && (element.hasAttribute('aria-label') || element.hasAttribute('aria-labelledby') || element.hasAttribute('title'))) return;
    if (element.dataset.fvLabelSrc !== 'tip') {
        if (element.querySelector('img[alt]:not([alt=""])')) return;
        const text = clean(element.textContent);
        // Words straight inside are drawn with the control; words in a child may be hidden at this width (innerText knows).
        if (text && !element.children?.length) return;
        if (text && !readLayout) return 'later';
        if (text && clean(element.innerText ?? text)) return;
    }
    const name = element.dataset.fvTipName || (() => { const found = copyFor(element); return found && !found.entry.byLabel ? pick(found.entry.name) : ''; })();
    if (!name) return;
    if (element.getAttribute('aria-label') !== name) element.setAttribute('aria-label', name);
    element.dataset.fvLabelSrc = 'tip';
}
export function nameControls(root = globalThis.document) {
    for (const element of collect(root)) if (element.matches(NAMED) && inScope(element)) nameOne(element);
}

// New and moved nodes are handled in idle time, a few milliseconds at a time: each added subtree is queried once, then
// its controls are stamped and named in slices of at most ~6 ms, so a big re-render never turns into one long task.
const stampLists = [], stampRoots = new Set(), stampQueue = new Set(), layoutQueue = new Set();
let working = false;
// patient: wait for a real idle period however long it takes (only layout reads are left), instead of a 600 ms deadline.
const idle = (work, patient = false) => globalThis.requestIdleCallback ? requestIdleCallback(work, patient ? {} : {timeout: 600})
    : setTimeout(() => work({didTimeout: !patient, timeRemaining: () => (patient ? 14 : 6)}), patient ? 400 : 60);
const now = () => globalThis.performance?.now?.() ?? Date.now();
// Called from the mutation observer inside the page's own work: it only keeps the lists; everything else waits for idle time.
function queueStamp(nodes) {
    stampLists.push(nodes);
    if (!working) { working = true; idle(drain); }
}
function drain(deadline) {
    const start = now();
    const more = () => now() - start < 6 && (deadline.didTimeout || deadline.timeRemaining() > 1);
    while (stampLists.length && more()) for (const node of stampLists.shift()) if (node.nodeType === 1) stampRoots.add(node);
    while (stampRoots.size && more()) {
        const root = stampRoots.values().next().value; stampRoots.delete(root);
        if (root.isConnected && !(tip && tip.contains(root))) for (const element of collect(root)) stampQueue.add(element);
    }
    while (stampQueue.size && more()) {
        const element = stampQueue.values().next().value; stampQueue.delete(element);
        if (!element.isConnected) continue;
        stampElement(element);
        if (element.matches(NAMED) && nameOne(element, {readLayout: false}) === 'later') layoutQueue.add(element);
    }
    // Reading layout only in a long idle period, a few controls at a time.
    while (layoutQueue.size && !stampQueue.size && !deadline.didTimeout && deadline.timeRemaining() > 12 && now() - start < 6) {
        const element = layoutQueue.values().next().value; layoutQueue.delete(element);
        if (element.isConnected) nameOne(element);
    }
    if (stampLists.length || stampRoots.size || stampQueue.size) idle(drain);
    else if (layoutQueue.size) idle(drain, true);
    else working = false;
}

export function installTooltips() {
    if (globalThis.__fvTooltips) return;
    globalThis.__fvTooltips = true;
    installA11y();
    // Timing from the shared tokens when the sheet defines them (glob: --fv-tip-delay / -hide / -warm).
    try {
        const style = getComputedStyle(document.documentElement);
        const ms = name => { const value = style.getPropertyValue(name).trim(); const n = parseFloat(value); return value && Number.isFinite(n) ? (value.endsWith('ms') ? n : value.endsWith('s') ? n * 1000 : n) : null; };
        timing.show = ms('--fv-tip-delay') ?? timing.show; timing.hide = ms('--fv-tip-hide') ?? timing.hide; timing.warm = ms('--fv-tip-warm') ?? timing.warm;
        if (reduced()) { timing.show = SHOW_DELAY; timing.hide = HIDE_DELAY; timing.warm = WARM_FOR; }   // reduced motion zeroes durations, not delays
    } catch { /* Defaults. */ }
    const listen = (type, handler, options = true) => document.addEventListener(type, handler, options);
    listen('pointerover', onPointerOver);
    listen('focusin', onFocusIn);
    // Focus moving into the control under the pointer (a menu that focuses the row being hovered) keeps its tip coming.
    // So does focus moving into the submenu that control has just opened by itself.
    listen('focusout', event => {
        if (hovered && event.relatedTarget instanceof Node && (hovered.contains(event.relatedTarget) || hovered.getAttribute('aria-expanded') === 'true')) return;
        hideTip();
    });
    listen('pointerdown', onPointerDown);
    listen('pointermove', onPointerMove, {capture: true, passive: true});
    listen('pointerup', onPointerEnd); listen('pointercancel', onPointerEnd);
    listen('click', onClick);
    listen('contextmenu', () => { clearTimeout(pressTimer); pressPoint = null; hideTip({now: true}); });
    listen('keydown', event => { if (shown && (event.key === 'Escape' || !['Shift', 'Control', 'Alt', 'Meta', 'Tab'].includes(event.key))) hideTip({now: true}); });
    listen('wheel', () => hideTip({now: true}), {capture: true, passive: true});
    listen('scroll', () => hideTip({now: true}), {capture: true, passive: true});
    // A menu, popover or dialog opening over the control takes the tip away (a long press that opens a menu, too).
    listen('toggle', event => {
        const layer = event.target;
        if (layer === tip || event.newState !== 'open' || !(shown || showTimer) || !(layer instanceof Element)) return;
        // A submenu the hovered row opened by itself: the tip stays and is placed clear of it.
        if (hovered?.getAttribute('aria-expanded') === 'true') return;
        // The floating task window and other standing layers re-show themselves; only menus, dialogs and light-dismiss popovers count.
        const kind = layer.getAttribute('popover');
        if (layer.matches('dialog,[role=menu],[role=listbox],[role=dialog],[role=alertdialog]') || kind === '' || kind === 'auto') hideTip({now: true});
    });
    globalThis.addEventListener('blur', () => hideTip({now: true}));
    globalThis.addEventListener('resize', () => hideTip({now: true}));
    globalThis.addEventListener('freevideo-view', () => hideTip({now: true}));
    document.addEventListener('pointerout', event => {
        if (event.target instanceof Element && hovered && hovered.contains(event.target) && !hovered.contains(event.relatedTarget)) hovered = null;
        if (shown && event.target instanceof Element && shown.contains(event.target) && !shown.contains(event.relatedTarget)) hideTip();
    }, true);
    queueStamp([document.body]);
    if (globalThis.MutationObserver) {
        new MutationObserver(records => { for (const record of records) queueStamp(record.addedNodes); }).observe(document.body, {childList: true, subtree: true});
        new MutationObserver(records => { for (const record of records) if (record.target.dataset?.fvLabelSrc === 'tip') nameOne(record.target); })
            .observe(document.body, {attributes: true, attributeFilter: ['data-fv-tip-name'], subtree: true});
    }
}
