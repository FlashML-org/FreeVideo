// "Take me there": ring the real control a tutorial scene is about. Targets carry data-tour-id;
// a collapsed section or a hidden panel is opened first, and a missing target only opens the view.
if (!document.head.querySelector('link[href$="tour_goto.css"]')) {
    const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = new URL('./tour_goto.css', import.meta.url).href;
    document.head.append(link);
}
const RING_MS = 3000, FADE_MS = 300;
let current = null;
const visible = node => { const r = node?.getBoundingClientRect(); return !!r && r.width > 0 && r.height > 0; };
const find = id => document.querySelector(`[data-tour-id="${CSS.escape(id)}"]`);
function reveal(node) {
    for (let parent = node.parentElement; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS' && !parent.open) parent.open = true;
}
export function clearTourRing() { current?.(); }
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
    const ring = document.createElement('div'); ring.className = 'fv-tour-ring'; ring.setAttribute('aria-hidden', 'true');
    // A transformed dialog becomes the containing block of fixed children, so the ring corrects by what it measures.
    const place = () => {
        const r = node.getBoundingClientRect();
        Object.assign(ring.style, {left: `${r.left - 4}px`, top: `${r.top - 4}px`, width: `${r.width + 8}px`, height: `${r.height + 8}px`});
        if (!ring.isConnected) return;
        const g = ring.getBoundingClientRect();
        Object.assign(ring.style, {left: `${r.left - 4 - (g.left - (r.left - 4))}px`, top: `${r.top - 4 - (g.top - (r.top - 4))}px`});
    };
    // Smooth scrolling and a section that just opened can still be moving: wait until the target holds still.
    for (let last = null, i = 0; i < 30; i++) {
        const r = node.getBoundingClientRect(), key = `${r.top}|${r.left}`;
        if (key === last) break; last = key; await new Promise(resolve => requestAnimationFrame(resolve));
    }
    (node.closest('dialog[open]') || document.body).append(ring); place();
    const follow = () => { if (node.isConnected) place(); else done(); };
    addEventListener('scroll', follow, true); addEventListener('resize', follow);
    let timer = 0, gone = false;
    function done() {
        if (gone) return; gone = true; clearTimeout(timer);
        removeEventListener('scroll', follow, true); removeEventListener('resize', follow);
        document.removeEventListener('pointerdown', done, true);
        ring.classList.add('fv-tour-ring-out'); setTimeout(() => ring.remove(), reduced ? 0 : FADE_MS);
        if (current === done) current = null;
    }
    current = done; timer = setTimeout(done, RING_MS);
    document.addEventListener('pointerdown', done, true);
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
        await wait(300);
        const part = document.querySelector(select);
        const box = part?.getBoundingClientRect();
        if (box) for (const type of ['pointerdown', 'pointerup']) part.dispatchEvent(new PointerEvent(type, {bubbles: true, cancelable: true, pointerId: 1, isPrimary: true, button: 0, buttons: type === 'pointerdown' ? 1 : 0, clientX: box.left + box.width / 2, clientY: box.top + box.height / 2}));
        for (let i = 0; i < 20 && !ids.some(reachable); i++) await wait(100);
    }
    if (!ids.some(reachable)) return {ok: false, reason: 'hidden'};
    await beforeRing?.();
    for (const id of ids) if (await guideTo(id)) return {ok: true};
    return {ok: false, reason: 'hidden'};
}
