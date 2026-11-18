// Zoom and move a large picture (the library viewer, the image view's viewer). Wheel or double-click zooms around the
// pointer. Once zoomed, the wheel and the trackpad move it in every direction (a pinch or Ctrl/⌘ + wheel keeps
// zooming), and so do a drag and pan() (the arrow keys of its viewer); it stops at its edges, and back at its own size
// it sits in the middle again. The picture is centred in `stage` and drawn contained in its own box.
export function zoomable(stage, image) {
    let scale = 1, x = 0, y = 0, drag = null;
    // How far the zoomed picture may move each way: half of what its drawn box overhangs the stage.
    const reach = () => {
        const w = stage.clientWidth, h = stage.clientHeight, bw = image.clientWidth || w, bh = image.clientHeight || h;
        const nw = image.naturalWidth || bw, nh = image.naturalHeight || bh, fit = Math.min(bw / nw, bh / nh) || 1;
        return [Math.max(0, (nw * fit * scale - w) / 2), Math.max(0, (nh * fit * scale - h) / 2)];
    };
    const apply = () => {
        const [rx, ry] = reach(); x = Math.max(-rx, Math.min(rx, x)); y = Math.max(-ry, Math.min(ry, y));
        image.style.transform = scale === 1 ? '' : `translate(${x}px, ${y}px) scale(${scale})`; stage.dataset.zoomed = String(scale > 1);
    };
    const zoomAt = (next, cx, cy) => {
        next = Math.max(1, Math.min(8, next));
        const box = stage.getBoundingClientRect(), dx = cx - (box.left + box.width / 2), dy = cy - (box.top + box.height / 2);
        x = dx - (dx - x) * next / scale; y = dy - (dy - y) * next / scale; scale = next;
        if (scale === 1) x = y = 0;
        apply();
    };
    const pan = (dx, dy) => { x += dx; y += dy; apply(); };
    stage.addEventListener('wheel', event => {
        event.preventDefault();
        if (scale > 1 && !event.ctrlKey && !event.metaKey) {
            const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? stage.clientHeight : 1;
            const [dx, dy] = event.shiftKey && !event.deltaX ? [event.deltaY, 0] : [event.deltaX, event.deltaY];
            pan(-dx * unit, -dy * unit); return;
        }
        zoomAt(scale * Math.exp(-event.deltaY * (event.ctrlKey ? .01 : .0015)), event.clientX, event.clientY);
    }, {passive: false});
    image.addEventListener('dblclick', event => zoomAt(scale > 1 ? 1 : 2.5, event.clientX, event.clientY));
    image.addEventListener('pointerdown', event => {
        if (scale === 1 || event.button !== 0) return;
        drag = {id: event.pointerId, x: event.clientX - x, y: event.clientY - y, sx: event.clientX, sy: event.clientY, moved: false};
        image.setPointerCapture(event.pointerId);
    });
    image.addEventListener('pointermove', event => {
        if (drag?.id !== event.pointerId) return;
        if (Math.abs(event.clientX - drag.sx) + Math.abs(event.clientY - drag.sy) > 4) drag.moved = true;
        x = event.clientX - drag.x; y = event.clientY - drag.y; apply();
    });
    // The click that ends a drag is not a click on what lies beside the picture (which closes a viewer).
    // The browser sends that click right after the pointerup, before any timer runs.
    const swallow = event => { event.stopPropagation(); event.preventDefault(); };
    const end = () => {
        if (drag?.moved) { window.addEventListener('click', swallow, {capture: true, once: true}); setTimeout(() => window.removeEventListener('click', swallow, {capture: true}), 0); }
        drag = null;
    };
    image.addEventListener('pointerup', end); image.addEventListener('pointercancel', () => { drag = null; });
    return {zoomed: () => scale > 1 && image.isConnected, pan, reset: () => { scale = 1; x = y = 0; apply(); }};
}
