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
