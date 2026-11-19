// MOSS-Image layout: boxes drawn on the empty frame of the next picture, each with one line saying what goes there.
// Boxes are kept as fractions of the frame ([left, top, right, bottom], 0..1, from the top-left), so a change of shape
// moves them with the frame; the engine writes them in pixels of the size actually made (image_gen/moss_layout.py).
// Words in quotes (“…”, 「…」 or "…") are written into the picture: such a box shows a T, which can be switched off when
// the quotes are only emphasis. A box with no words is drawn dashed and is not sent.
import { el, t, tip } from './image_view_ui.js';
import { listenDialogKey } from './dialog_keys.js';

export const LAYOUT_LIMIT = 8;
export const LINE_LIMIT = 400;
export const TEXT_LIMIT = 200;
const SMALLEST = .04;
const QUOTED = /“([^”]*)”|「([^」]*)」|"([^"]*)"/g;

// The quoted parts of a line, in order (empty quotes say nothing and are left out).
export function quotedParts(line) {
    const parts = [];
    for (const found of String(line || '').matchAll(QUOTED)) {
        const words = (found[1] ?? found[2] ?? found[3] ?? '').trim();
        if (words) parts.push(words);
    }
    return parts;
}

const round = value => Math.round(value * 1e4) / 1e4;

// What the engine gets for one box, or null for a box with no words. With quotes (and the T on) it is a text box: the
// quoted words, several parts on lines of their own, and the rest of the line as its description.
export function boxRequest(item) {
    const line = String(item?.line || '').trim();
    if (!line) return null;
    const box = item.box.map(round);
    const parts = quotedParts(line);
    if (parts.length && !item.textOff) {
        const caption = line.replace(QUOTED, ' ').replace(/\s+/g, ' ').replace(/^[\s,，、:：;；]+|[\s,，、:：;；]+$/g, '').trim();
        return {label: 'text', text: parts.join('\n').slice(0, TEXT_LIMIT), ...(caption ? {caption} : {}), box};
    }
    return {caption: line.slice(0, LINE_LIMIT), box};
}

export function layoutRequest(items) { return (items || []).map(boxRequest).filter(Boolean); }

// The whole picture's words when the description is left empty: the boxes' lines in their order, within the 2000
// characters a description may have (eight full lines would pass it).
export const CAPTION_LIMIT = 2000;
export function layoutCaption(items) {
    return (items || []).map(item => String(item.line || '').trim()).filter(Boolean).join('; ').slice(0, CAPTION_LIMIT).trim();
}

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const copy = items => items.map(item => ({...item, box: [...item.box]}));

// The box editor over the frame. ``host`` is the frame (the overlay fills it); onChange runs after every change.
export function createLayout({host, layer = document.body, onChange = () => {}, limit = LAYOUT_LIMIT, narrow = () => matchMedia('(max-width: 719px)').matches,
    coarse = () => matchMedia('(pointer: coarse)').matches}) {
    let items = [], selected = -1, editing = -1, shown = false, nextId = 1;
    const undo = [], redo = [];
    // The frame takes the keys while a box is chosen (Delete, arrows, undo): focus stays on it, inside the view, so the
    // view's own keys (Delete deletes the shown picture) never see them.
    const overlay = el('div', 'fv-iv-layout'); overlay.hidden = true; overlay.tabIndex = -1;
    const ghost = el('div', 'fv-iv-lbox fv-iv-lbox-ghost'); ghost.hidden = true;
    const field = el('input', 'fv-iv-lbox-input'); field.type = 'text'; field.maxLength = LINE_LIMIT; field.placeholder = t('What goes here', '放什么');
    field.setAttribute('aria-label', t('What goes here', '放什么')); field.hidden = true;
    // On a phone the line is written in a bar at the foot, opaque, with the box's number at its start.
    const bar = el('div', 'fv-iv-lbar'), barNo = el('span', 'fv-iv-lbox-no'), done = el('button', 'fv-iv-lbar-done', t('Done', '完成'));
    done.type = 'button'; bar.append(barNo, done); bar.hidden = true;
    overlay.append(ghost, field);
    host.append(overlay);
    // In the view's own layer (a modal dialog shows nothing of the page body above it).
    layer.append(bar);
    // The bar rises with the on-screen keyboard.
    const viewport = window.visualViewport;
    const lift = () => { if (viewport) bar.style.setProperty('--fv-kb', `${Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop)}px`); keepInView(); };
    viewport?.addEventListener('resize', lift); viewport?.addEventListener('scroll', lift);
    window.addEventListener('resize', lift);
    // The box being written stays in sight above the bar (8 px clear, Iris 11-19): the page under it scrolls by what the
    // bar covers; a box already clear is left where it is.
    function keepInView() {
        if (editing < 0 || bar.hidden) return;
        requestAnimationFrame(() => {
            const node = overlay.querySelector(`.fv-iv-lbox[data-index="${editing}"]`);
            if (!node || bar.hidden) return;
            const over = node.getBoundingClientRect().bottom - (bar.getBoundingClientRect().top - 8);
            if (over <= 0) return;
            let scroller = overlay.parentElement;
            while (scroller && !(scroller.scrollHeight > scroller.clientHeight + 1 && /auto|scroll/.test(getComputedStyle(scroller).overflowY))) scroller = scroller.parentElement;
            (scroller || document.scrollingElement)?.scrollBy({top: over});
        });
    }
    let hint = null;
    // An Escape the boxes used (closing a line, letting go of a box): the dialog's own cancel still follows it, and the
    // view asks here so that it does not also step back a layer.
    let tookAt = -Infinity;
    const took = () => { tookAt = performance.now(); };

    const changed = () => { paint(); onChange(); };
    const remember = () => { undo.push(copy(items)); if (undo.length > 100) undo.shift(); redo.length = 0; };
    const frame = () => overlay.getBoundingClientRect();
    const at = event => { const r = frame(); return [clamp((event.clientX - r.left) / r.width, 0, 1), clamp((event.clientY - r.top) / r.height, 0, 1)]; };

    function say(event) {
        // Once, beside the pointer, gone by itself: never a dialog, never left on the page.
        hint?.remove();
        hint = el('div', 'fv-iv-lhint', t(`Up to ${limit} boxes`, `最多 ${limit} 个框`)); hint.setAttribute('role', 'status');
        hint.style.left = `${event.clientX + 12}px`; hint.style.top = `${event.clientY + 12}px`;
        layer.append(hint);
        const mine = hint; setTimeout(() => { mine.remove(); if (hint === mine) hint = null; }, 1600);
    }

    let before = null;
    function select(index, edit = false) {
        if (editing >= 0 && editing !== index) commit();
        selected = index; editing = edit && index >= 0 ? index : -1;
        // The line changes as it is typed; undo goes back to how it was when the box was opened.
        before = editing >= 0 ? {items: copy(items), line: items[editing].line} : null;
        paint();
        if (editing >= 0) { field.value = items[editing].line; queueMicrotask(() => { field.focus(); field.setSelectionRange(field.value.length, field.value.length); }); }
    }
    function commit() {
        if (editing < 0 || !items[editing]) { editing = -1; return; }
        const line = field.value.slice(0, LINE_LIMIT), item = items[editing];
        // A T switched off stays off while the line still has quotes; quotes taken out and put back bring it back on.
        item.textOff = item.textOff && quotedParts(line).length > 0;
        item.line = line;
        if (before && before.line !== line) { undo.push(before.items); redo.length = 0; }
        before = null; editing = -1;
    }
    function remove(index) {
        if (index < 0 || !items[index]) return;
        remember(); if (editing === index) editing = -1;
        items.splice(index, 1); selected = -1; editing = -1; changed(); overlay.focus({preventScroll: true});
    }
    function step(direction) {
        const list = direction < 0 ? undo : redo, other = direction < 0 ? redo : undo;
        if (!list.length) return;
        other.push(copy(items)); items = list.pop(); selected = editing = -1; changed();
    }

    function paint() {
        for (const node of [...overlay.querySelectorAll('.fv-iv-lbox:not(.fv-iv-lbox-ghost)')]) node.remove();
        const handles = coarse() ? ['nw', 'ne', 'sw', 'se'] : ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];
        items.forEach((item, index) => {
            const [x0, y0, x1, y1] = item.box, node = el('div', 'fv-iv-lbox');
            node.dataset.index = String(index); node.dataset.empty = String(!item.line.trim());
            node.dataset.selected = String(index === selected); node.dataset.editing = String(index === editing);
            // A chosen box is a layer of the view: Escape lets go of it before it steps the view back (shell.js FLOATING).
            if (index === selected) node.dataset.fvLayer = '';
            Object.assign(node.style, {left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%`});
            const head = el('div', 'fv-iv-lbox-head'), no = el('span', 'fv-iv-lbox-no', String(index + 1));
            head.append(no);
            if (quotedParts(item.line).length) {
                const T = el('button', 'fv-iv-lbox-t', 'T'); T.type = 'button';
                T.setAttribute('aria-pressed', String(!item.textOff)); T.setAttribute('aria-label', t('Text in the picture', '写进画面的字'));
                tip(T, t('Text in the picture', '写进画面的字'));
                T.addEventListener('pointerdown', event => event.stopPropagation());
                T.addEventListener('click', event => { event.stopPropagation(); remember(); item.textOff = !item.textOff; changed(); });
                head.append(T);
            }
            if (index !== editing && item.line.trim()) head.append(el('span', 'fv-iv-lbox-line', item.line.trim()));
            node.append(head);
            if (index === selected) {
                for (const side of handles) { const h = el('span', 'fv-iv-lbox-h'); h.dataset.side = side; node.append(h); }
                const x = el('button', 'fv-iv-lbox-x', '×'); x.type = 'button'; x.setAttribute('aria-label', t('Delete box', '删除框')); tip(x, t('Delete box', '删除框'));
                x.addEventListener('pointerdown', event => event.stopPropagation());
                x.addEventListener('click', event => { event.stopPropagation(); remove(index); });
                node.append(x);
            }
            // (The line's box sits after the boxes, above them; on a phone it is in the bar instead.)
            if (field.parentNode === overlay) overlay.insertBefore(node, field); else overlay.append(node);
        });
        placeField();
    }
    function placeField() {
        const phone = narrow();
        field.hidden = editing < 0;
        if (editing >= 0) field.dataset.fvLayer = ''; else delete field.dataset.fvLayer;
        bar.hidden = editing < 0 || !phone || !shown;
        if (editing < 0) { if (field.parentNode !== overlay) overlay.append(field); return; }
        if (phone) {
            if (field.parentNode !== bar) bar.insertBefore(field, done);
            barNo.textContent = String(editing + 1);
            keepInView();
            return;
        }
        if (field.parentNode !== overlay) overlay.append(field);
        const [x0, , , y1] = items[editing].box, below = y1 < .88;
        Object.assign(field.style, {left: `${x0 * 100}%`, top: below ? `${y1 * 100}%` : '', bottom: below ? '' : `${(1 - items[editing].box[1]) * 100}%`});
        field.dataset.side = below ? 'below' : 'above';
    }

    // Draw on empty frame, move by the inside, resize by the handles.
    let drag = null;
    overlay.addEventListener('pointerdown', event => {
        if (event.button !== 0 || event.target === field) return;
        const node = event.target.closest('.fv-iv-lbox'), handle = event.target.closest('.fv-iv-lbox-h');
        const point = at(event);
        if (editing >= 0) commit();
        if (node && !node.classList.contains('fv-iv-lbox-ghost')) {
            const index = Number(node.dataset.index);
            drag = {kind: handle ? 'resize' : 'move', side: handle?.dataset.side || '', index, start: point, from: [...items[index].box], moved: false};
            selected = index; paint();
        } else {
            if (items.length >= limit) { say(event); select(-1); return; }
            drag = {kind: 'draw', start: point, box: [...point, ...point], moved: false};
        }
        overlay.setPointerCapture?.(event.pointerId);
        event.preventDefault(); overlay.focus({preventScroll: true});
    });
    overlay.addEventListener('pointermove', event => {
        if (!drag) return;
        const [x, y] = at(event), [sx, sy] = drag.start;
        if (Math.abs(x - sx) + Math.abs(y - sy) > .005) drag.moved = true;
        if (drag.kind === 'draw') {
            drag.box = [Math.min(x, sx), Math.min(y, sy), Math.max(x, sx), Math.max(y, sy)];
            const [x0, y0, x1, y1] = drag.box;
            ghost.hidden = !drag.moved;
            Object.assign(ghost.style, {left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%`});
            return;
        }
        if (!drag.moved) return;
        if (!drag.remembered) { remember(); drag.remembered = true; }
        const [fx0, fy0, fx1, fy1] = drag.from, dx = x - sx, dy = y - sy, box = items[drag.index].box;
        if (drag.kind === 'move') {
            const w = fx1 - fx0, h = fy1 - fy0, nx = clamp(fx0 + dx, 0, 1 - w), ny = clamp(fy0 + dy, 0, 1 - h);
            box.splice(0, 4, nx, ny, nx + w, ny + h);
        } else {
            let [x0, y0, x1, y1] = drag.from;
            if (drag.side.includes('w')) x0 = clamp(fx0 + dx, 0, fx1 - SMALLEST);
            if (drag.side.includes('e')) x1 = clamp(fx1 + dx, fx0 + SMALLEST, 1);
            if (drag.side.includes('n')) y0 = clamp(fy0 + dy, 0, fy1 - SMALLEST);
            if (drag.side.includes('s')) y1 = clamp(fy1 + dy, fy0 + SMALLEST, 1);
            box.splice(0, 4, x0, y0, x1, y1);
        }
        paint();
    });
    const finish = () => {
        if (!drag) return;
        const was = drag; drag = null; ghost.hidden = true;
        if (was.kind === 'draw') {
            const [x0, y0, x1, y1] = was.box;
            // Too small to be meant as a box: a click on the empty frame, which lets go of the chosen box.
            if (x1 - x0 < SMALLEST || y1 - y0 < SMALLEST) { select(-1); return; }
            remember(); items.push({id: nextId++, box: was.box, line: '', textOff: false});
            select(items.length - 1, true); onChange();
            return;
        }
        if (!was.moved) { select(was.index, true); return; }
        changed();
    };
    overlay.addEventListener('pointerup', finish);
    overlay.addEventListener('pointercancel', finish);

    const unlistenField = listenDialogKey(field, 'keydown', event => {
        // Keys in the box are the words' own: nothing reaches the frame or the view, except Ctrl+Enter (Generate),
        // which takes the line as it is.
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { commit(); select(-1); return; }
        event.stopPropagation();
        if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); commit(); select(-1); onChange(); }
        // Escape closes the line and keeps the box chosen (Delete and the arrows then act on it); a second Escape lets go.
        else if (event.key === 'Escape') { event.preventDefault(); took(); const index = editing; commit(); selected = index; paint(); overlay.focus({preventScroll: true}); onChange(); }
    });
    field.addEventListener('input', () => { if (editing >= 0) { const item = items[editing]; item.line = field.value.slice(0, LINE_LIMIT); if (!quotedParts(item.line).length) item.textOff = false; onChange(); } });
    done.addEventListener('click', () => { commit(); select(-1); onChange(); });

    // Delete, arrows and undo act on the boxes only while the frame is shown and no box of words has the keys.
    function onKey(event) {
        if (!shown || overlay.hidden || !overlay.contains(event.target) || event.target === field) return;
        const mod = event.ctrlKey || event.metaKey;
        if (mod && event.key.toLowerCase() === 'z') { event.preventDefault(); event.stopPropagation(); step(event.shiftKey ? 1 : -1); return; }
        if (selected < 0) return;
        if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); event.stopPropagation(); remove(selected); return; }
        if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); took(); select(-1); return; }
        const moves = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[event.key];
        if (moves) {
            event.preventDefault(); event.stopPropagation(); remember();
            const by = event.shiftKey ? .05 : .01, box = items[selected].box, w = box[2] - box[0], h = box[3] - box[1];
            const nx = clamp(box[0] + moves[0] * by, 0, 1 - w), ny = clamp(box[1] + moves[1] * by, 0, 1 - h);
            box.splice(0, 4, nx, ny, nx + w, ny + h); changed();
        }
    }
    const unlisten = listenDialogKey(overlay, 'keydown', onKey);

    return {
        element: overlay,
        show(on) {
            shown = !!on; overlay.hidden = !shown;
            if (!shown && editing >= 0) { commit(); editing = -1; }
            if (!shown) selected = -1;
            paint();
        },
        get shown() { return shown; },
        tookEscape: () => performance.now() - tookAt < 400,
        // Escape however it arrives (the view's cancel, when the key did not reach the boxes): an open line closes and its
        // box stays chosen; else a chosen box is let go. True when it was used here.
        escape() {
            if (!shown) return false;
            if (editing >= 0) { const index = editing; commit(); selected = index; paint(); overlay.focus({preventScroll: true}); onChange(); took(); return true; }
            if (selected >= 0) { select(-1); took(); return true; }
            return false;
        },
        count: () => items.length,
        filled: () => items.filter(item => item.line.trim()).length,
        request: () => layoutRequest(items),
        caption: () => layoutCaption(items),
        snapshot: () => copy(items).map(({id, ...rest}) => rest),
        set(list) {
            if (editing >= 0) commit();
            remember();
            items = (Array.isArray(list) ? list : []).slice(0, limit).filter(item => Array.isArray(item?.box) && item.box.length === 4)
                .map(item => ({id: nextId++, box: item.box.map(Number), line: String(item.line || '').slice(0, LINE_LIMIT), textOff: !!item.textOff}));
            selected = editing = -1; changed();
        },
        dispose() { unlisten(); unlistenField(); viewport?.removeEventListener('resize', lift); viewport?.removeEventListener('scroll', lift); window.removeEventListener('resize', lift); bar.remove(); hint?.remove(); overlay.remove(); },
    };
}
