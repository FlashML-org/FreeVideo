// Media, LoRA and Advanced: the creation panel's last three trays shown as small windows beside their toolbar buttons,
// the same kind of window as Frame & size, Duration and Quality, placed by the same rule (frame_picker.js placePop):
// right at its button, its lower edge 8 px above it, on the button's left edge (its right edge for LoRA and Advanced, at
// the right of the row); never over the button, Generate or the top bar; when it does not fit, it scrolls inside.
// Neither the stage nor the toolbar moves. Presentation only: each tray stays studio_console.js's <details>, with its
// open state, Escape, the press outside that puts it away, and every control's own handlers. A tray marked data-fv-pop
// is left out of studio.js's own tray placement (no docking beside the picture, no room made on the stage); if this
// module does not load, the trays are exactly as studio.js built them.
//
// A window whose inside keeps its own scrolling part (Media's tiles) sets tray.fvPopBefore (let that part take its full
// height) and tray.fvPopAfter (fit it again to the window's height); both run around each placement, in one go.
import {ASKING, placePop} from './frame_picker.js';

function sheet() {
    if (document.querySelector('link[data-fv-sheet="tray-pop"]')) return;
    const link = document.createElement('link'); link.rel = 'stylesheet'; link.dataset.fvSheet = 'tray-pop';
    link.href = new URL('./tray_pop.css', import.meta.url).href; document.head.append(link);
}

function place(tray) {
    if (!tray.open || !tray.isConnected) return;
    const chip = (tray.closest('dialog') || document).querySelector(`.fvs-chip[aria-controls="${tray.id}"]`);
    if (!chip?.getClientRects().length) return;
    tray.fvPopBefore?.();
    placePop(chip, tray);
    tray.fvPopAfter?.();
    // The window grows out of its button: from the button's side of it.
    const a = chip.getBoundingClientRect(), b = tray.getBoundingClientRect();
    const align = a.left + a.width / 2 > b.left + b.width / 2 ? 'end' : 'start';
    if (tray.dataset.align !== align) tray.dataset.align = align;
}

function adopt(tray) {
    if (tray.fvPop) return; tray.fvPop = true;
    tray.dataset.fvPop = tray.dataset.tray;
    const again = () => place(tray);
    tray.addEventListener('toggle', again);
    if (globalThis.ResizeObserver) {
        const watch = new ResizeObserver(again); watch.observe(tray);
        // The button moves when the row's labels change (a count, a size), and the console grows with the prompt.
        const dialog = tray.closest('dialog');
        for (const part of [dialog?.querySelector('.fvs-chips'), dialog?.querySelector('.fvs-console')]) if (part) watch.observe(part);
    }
    globalThis.addEventListener('resize', again);
    again();
}

export function scanPops() {
    for (const tray of document.querySelectorAll('dialog.fv-create .fvs-tray:is([data-tray=media],[data-tray=lora],[data-tray=advanced])')) adopt(tray);
}

if (!globalThis.FreeVideoTrayPop) {
    globalThis.FreeVideoTrayPop = {scan: scanPops, place};
    sheet();
    let timer = 0;
    new MutationObserver(() => { if (!timer) timer = setTimeout(() => { timer = 0; scanPops(); }, 120); }).observe(document.body, {childList: true, subtree: true});
    // A bar that asks before going on (frame_picker.js ASKING) puts every tray window away, as the other panels close.
    document.addEventListener(ASKING, () => { for (const tray of document.querySelectorAll('.fvs-tray[data-fv-pop][open]')) tray.open = false; });
    scanPops();
}
