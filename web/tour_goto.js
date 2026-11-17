// "Take me there": ring the real control a tutorial scene is about. Targets carry data-tour-id;
// a collapsed section or a hidden panel is opened first, and a missing target only opens the view.
// The ring is the shared anchored layer at level 1 (feature_tour_layer.js): no dim, it fades after 3 s or on any press,
// and it leaves with its view.
import {openAnchor, closeAnchor, anchorOpen} from './feature_tour_layer.js';

const RING_MS = 3000;
const visible = node => { const r = node?.getBoundingClientRect(); return !!r && r.width > 0 && r.height > 0; };
const find = id => document.querySelector(`[data-tour-id="${CSS.escape(id)}"]`);
function reveal(node) {
    for (let parent = node.parentElement; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS' && !parent.open) parent.open = true;
}
let ringed = null;
export function clearTourRing() { if (ringed && anchorOpen() === ringed) closeAnchor('now'); ringed = null; }
/** Resolve with true when the ring was drawn, false when the target is absent or hidden. */
export async function guideTo(id) {
    clearTourRing();
    let node = find(id);
    if (!node) return false;
    reveal(node);
    if (!visible(node)) {
        // A narrow layout keeps some panels behind a toggle that names the panel it opens.
        document.querySelector(`[data-tour-toggle="${CSS.escape(id)}"]`)?.click();
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        node = find(id);
        if (!node || !visible(node)) return false;
    }
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    node.scrollIntoView({block: 'center', inline: 'nearest', behavior: reduced ? 'auto' : 'smooth'});
    await new Promise(resolve => setTimeout(resolve, reduced ? 0 : 320));
    if (!visible(node)) return false;
    // Smooth scrolling and a section that just opened can still be moving: wait until the target holds still.
    for (let last = null, i = 0; i < 30; i++) {
        const r = node.getBoundingClientRect(), key = `${r.top}|${r.left}`;
        if (key === last) break; last = key; await new Promise(resolve => requestAnimationFrame(resolve));
    }
    ringed = openAnchor({owner: node.closest('dialog[open]') || null, target: node, level: 1, closeAfter: RING_MS});
    return true;
}

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
// Whether guideTo can show the target: drawn already, inside a closed section, or behind a toggle that names it.
const reachable = id => {
    const node = find(id);
    return !!node && (visible(node) || !!node.closest('details:not([open])') || !!document.querySelector(`[data-tour-toggle="${CSS.escape(id)}"]`));
};
/**
 * Open the view a scene belongs to, wait for one of its targets to appear, then ring it.
 * `select` is a selector for the part a selection-bound control needs. Resolves with {ok: true} once ringed, or
 * {ok: false, reason: 'view' | 'target' | 'hidden'}; `beforeRing` runs only when the ring is certain to be drawn.
 */
export async function goToScene({view = null, ids = [], select = null, beforeRing = null} = {}) {
    if (view === 'editor' && !document.querySelector('dialog.fv-editor[open]')) {
        if (!document.querySelector('dialog.fv-studio[open]')) await (await import('./view_navigation.js')).openCreativeWorkspace();
        document.querySelector('dialog.fv-studio [data-view="editor"]')?.click();
        for (let i = 0; i < 40 && !document.querySelector('dialog.fv-editor[open]'); i++) await wait(100);
        if (!document.querySelector('dialog.fv-editor[open]')) return {ok: false, reason: 'view'};
    }
    for (let i = 0; i < 30 && !ids.some(find); i++) await wait(100);
    if (!ids.some(find)) return {ok: false, reason: 'target'};
    if (select && !ids.some(reachable)) {
        // A view that just opened draws its parts a moment later, and can lay them out again once its project settles
        // (the Edit view does both within about 1 s, and the second pass drops the selection): press the part once it is
        // drawn, and again while the control has not appeared.
        const press = part => {
            const box = part.getBoundingClientRect();
            for (const type of ['pointerdown', 'pointerup']) part.dispatchEvent(new PointerEvent(type, {bubbles: true, cancelable: true, pointerId: 1, isPrimary: true, button: 0, buttons: type === 'pointerdown' ? 1 : 0, clientX: box.left + box.width / 2, clientY: box.top + box.height / 2}));
        };
        for (let i = 0, since = Infinity; i < 40 && !ids.some(reachable); i++, since++) {
            const part = document.querySelector(select);
            if (visible(part) && since >= 4) { press(part); since = 0; }
            await wait(100);
        }
    }
    if (!ids.some(reachable)) return {ok: false, reason: 'hidden'};
    await beforeRing?.();
    for (const id of ids) if (await guideTo(id)) return {ok: true};
    return {ok: false, reason: 'hidden'};
}
