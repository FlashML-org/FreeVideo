// Compare: two to four pictures picked in the works, shown together on the stage while they are picked.
import { listenDialogKey } from './dialog_keys.js';
import { el, icon, t, tip, button } from './image_view_ui.js';

export const COMPARE_MAX = 4;
export const clampCut = value => Math.min(100, Math.max(0, Number.isFinite(value) ? value : 50));

// Compare follows the works' selection: two picked works under one divider, three or four in a 2 × 2 grid (the first
// four, in the order picked). Each says its number and size in a corner; clicking one shows it alone.
// labelOf(file) → its size (or name); ratioOf(file) → width / height when known. onPick(file): show it alone;
// onRemove(file): unpick it; onClose(): leave the selection.
// Two pictures of about the same shape share one divider; two of different shapes (a square and a tall one) stand side
// by side, each whole, since one divider would crop the wider one.
export const SAME_SHAPE = 1.15;
export function pairMode(a, b) { return a > 0 && b > 0 && Math.max(a, b) / Math.min(a, b) <= SAME_SHAPE ? 'slider' : 'pair'; }
export function compareView({pictureURL, labelOf, ratioOf = () => 0, onPick, onRemove, onClose}) {
    const root = el('div', 'fv-iv-compare'); root.dataset.shown = 'false'; root.inert = true; root.setAttribute('aria-hidden', 'true');
    const bar = el('div', 'fv-iv-compare-bar');
    const note = el('span', 'fv-iv-compare-note');
    const close = button('fv-iv-text-btn', {iconName: 'x', label: t('Done', '完成'), name: t('Leave compare', '退出对比'), use: t('Clear the selection and see one picture', '取消多选，回到单张'), key: 'Esc'});
    bar.append(note, el('span', 'fv-iv-grow'), close);
    const panes = el('div', 'fv-iv-compare-panes');
    root.append(bar, panes);
    close.addEventListener('click', () => onClose());
    let cut = 50, current = null;
    // The corner label: its number in the selection and its size (the picture's own once it has loaded).
    const corner = (node, file, at) => { const size = el('span', '', labelOf(file)); node.append(el('b', 'fv-iv-cnum', String(at + 1)), size); return size; };
    const sized = (img, size) => img.addEventListener('load', () => { if (img.naturalWidth) size.textContent = `${img.naturalWidth} × ${img.naturalHeight}`; });

    function pane(file, at) {
        const p = el('div', 'fv-iv-cpane'); const frame = el('div', 'fv-iv-cframe'); const i = el('img'); i.alt = ''; i.draggable = false; i.src = pictureURL(file);
        i.addEventListener('load', () => { frame.style.setProperty('--ar', String(i.naturalWidth / i.naturalHeight || 1)); });
        frame.append(i);
        const pick = button('fv-iv-cpick', {name: t('Show this one', '只看这张'), use: t('Open it alone on the stage', '在中间单独显示')});
        pick.addEventListener('click', () => onPick(file));
        const x = button('fv-iv-icon-btn fv-iv-cx', {iconName: 'x', name: t('Remove from compare', '移出对比'), use: t('Unpick it; compare the others', '取消选中这张，只对比其余几张')});
        x.addEventListener('click', event => { event.stopPropagation(); onRemove(file); });
        const label = el('span', 'fv-iv-clabel'); sized(i, corner(label, file, at));
        frame.append(pick, label, x); p.append(frame); return p;
    }
    // Two pictures: one over the other, a divider between them; each corner label opens its picture alone.
    function sliderPane(a, b) {
        const box = el('div', 'fv-iv-cslider'); box.style.setProperty('--cut', `${cut}%`);
        const frame = el('div', 'fv-iv-cframe');
        const ia = el('img', 'fv-iv-cs-a'), ib = el('img', 'fv-iv-cs-b'); ia.alt = ib.alt = ''; ia.draggable = ib.draggable = false; ia.src = pictureURL(a); ib.src = pictureURL(b);
        ia.addEventListener('load', () => { frame.style.setProperty('--ar', String(ia.naturalWidth / ia.naturalHeight || 1)); });
        const handle = el('div', 'fv-iv-cs-handle'); handle.tabIndex = 0; handle.setAttribute('role', 'slider'); handle.setAttribute('aria-valuemin', '0'); handle.setAttribute('aria-valuemax', '100');
        handle.append(el('b', '', ''), icon('split'));
        tip(handle, t('Divider', '分割线'), t('Drag, or use the arrow keys', '拖动，或用方向键'), '← →');
        const labels = [[a, 0, 'fv-iv-cl-a', ia], [b, 1, 'fv-iv-cl-b', ib]].map(([file, at, cls, img]) => {
            const go = el('button', 'fv-iv-clabel fv-iv-clabel-btn ' + cls); go.type = 'button';
            sized(img, corner(go, file, at));
            tip(go, t('Show this one', '只看这张'), t('Open it alone on the stage', '在中间单独显示'));
            go.addEventListener('pointerdown', event => event.stopPropagation());
            go.addEventListener('click', () => onPick(file));
            return go;
        });
        frame.append(ia, ib, handle, ...labels); box.append(frame);
        const set = value => { cut = clampCut(value); frame.style.setProperty('--cut', `${cut}%`); handle.setAttribute('aria-valuenow', String(Math.round(cut))); };
        set(cut);
        // Follows the pointer one to one; no easing while dragging.
        frame.addEventListener('pointerdown', event => {
            if (event.button !== 0) return; frame.setPointerCapture(event.pointerId); frame.dataset.drag = 'true';
            const move = e => { const r = frame.getBoundingClientRect(); set((e.clientX - r.left) / r.width * 100); };
            move(event);
            const up = () => { frame.removeEventListener('pointermove', move); frame.removeEventListener('pointerup', up); frame.removeEventListener('pointercancel', up); delete frame.dataset.drag; };
            frame.addEventListener('pointermove', move); frame.addEventListener('pointerup', up); frame.addEventListener('pointercancel', up);
        });
        listenDialogKey(handle, 'keydown', event => {
            const step = event.shiftKey ? 10 : 2;
            if (event.key === 'ArrowLeft') { set(cut - step); event.preventDefault(); }
            if (event.key === 'ArrowRight') { set(cut + step); event.preventDefault(); }
            if (event.key === 'Home') { set(0); event.preventDefault(); }
            if (event.key === 'End') { set(100); event.preventDefault(); }
        });
        return box;
    }
    // state: {files, total} or null. It fades in and out (200 ms); what it showed stays drawn while it fades out.
    function paint(state) {
        const shown = !!state && state.files.length >= 2;
        root.dataset.shown = String(shown); root.inert = !shown; root.setAttribute('aria-hidden', String(!shown));
        if (!shown) return;
        const n = state.files.length, total = state.total || n;
        note.textContent = total > n ? t(`Comparing the first ${n} of ${total}`, `对比前 ${n} 张（已选 ${total} 张）`) : t(`Comparing ${n}`, `对比 ${n} 张`);
        const mode = n === 2 ? pairMode(ratioOf(state.files[0]), ratioOf(state.files[1])) : 'grid';
        const key = mode + '|' + state.files.join('|') + '|' + state.files.map(labelOf).join('|');
        if (key === current) return; current = key;
        root.dataset.mode = mode; root.dataset.count = String(n);
        if (mode === 'slider') panes.replaceChildren(sliderPane(state.files[0], state.files[1]));
        else panes.replaceChildren(...state.files.map(pane));
    }
    return {element: root, paint, isShown: () => root.dataset.shown === 'true'};
}
