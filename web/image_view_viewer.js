// The shown picture as large as the window allows, over the whole view: the close button, Escape or a click beside
// the picture closes it; ← → step to the newer or older work (the stage follows, so closing leaves that one shown).
// The wheel or a double-click zooms it (picture_zoom.js); zoomed, the wheel, a drag and the arrow keys move it.
import { listenDialogKey } from './dialog_keys.js';
import { el, t, button } from './image_view_ui.js';
import { zoomable } from './picture_zoom.js';

// step(by) → the URL of the picture now shown, or '' at an end.
export function openPictureViewer({host, url, step = null, onClose = () => {}}) {
    const root = el('div', 'fv-iv-viewer'); root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true');
    root.setAttribute('aria-label', t('Picture', '图片')); root.tabIndex = -1;
    const img = el('img', 'fv-iv-viewer-img'); img.alt = ''; img.draggable = false; img.src = url;
    const close = button('fv-iv-icon-btn fv-iv-viewer-x', {iconName: 'x', name: t('Close', '关闭'), use: t('Back to the view', '回到视图'), key: 'Esc'});
    root.append(img, close); host.append(root);
    const zoom = zoomable(root, img);
    const before = document.activeElement;
    requestAnimationFrame(() => { root.dataset.shown = 'true'; });
    root.focus({preventScroll: true});
    let open = true;
    const done = () => { if (!open) return; open = false; root.remove(); before?.focus?.({preventScroll: true}); onClose(); };
    close.addEventListener('click', done);
    root.addEventListener('click', event => { if (event.target === root) done(); });
    listenDialogKey(root, 'keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); done(); }
        else if (event.key.startsWith('Arrow') && zoom.zoomed()) {
            event.preventDefault(); event.stopPropagation();
            const by = event.shiftKey ? 240 : 80;
            zoom.pan({ArrowLeft: by, ArrowRight: -by}[event.key] || 0, {ArrowUp: by, ArrowDown: -by}[event.key] || 0);
        }
        else if ((event.key === 'ArrowLeft' || event.key === 'ArrowRight') && step) {
            event.preventDefault(); event.stopPropagation();
            const next = step(event.key === 'ArrowRight' ? 1 : -1); if (next) { zoom.reset(); img.src = next; }
        }
    });
    return {close: done, element: root, isOpen: () => open};
}
