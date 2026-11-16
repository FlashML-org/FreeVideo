import { emptyFrameIcon } from './empty_frame.js';
// The frame behind a running request: one calm, flat surface under the progress words and times (no waves, nothing
// moving). It also covers the previous video while a new one is made. Before the first video the frame is a plain
// placeholder.
export function createPreviewScene(stage, progress, media) {
    const canvas = document.createElement('canvas');
    canvas.className = 'fv-preview-scene'; canvas.setAttribute('aria-hidden', 'true');
    stage.prepend(canvas);
    const emptyIcon = emptyFrameIcon(); stage.append(emptyIcon);
    const ctx = canvas.getContext('2d', {alpha: false});
    if (!ctx) return () => { canvas.remove(); emptyIcon.remove(); };
    let width = 0, height = 0, visible = true, disposed = false;

    function paint() {
        if (disposed || !visible || !width || !height) return;
        ctx.fillStyle = '#101f30'; ctx.fillRect(0, 0, width, height);
    }
    function state() {
        const generating = !progress.hidden;
        const empty = !generating && !media.firstElementChild;
        stage.dataset.empty = String(empty); canvas.hidden = empty;
        visible = !empty && (generating || !media.querySelector('video'));
        paint();
    }
    const resize = new ResizeObserver(() => {
        width = stage.clientWidth; height = stage.clientHeight;
        const scale = Math.min(window.devicePixelRatio || 1, 1.5);
        canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
        ctx.setTransform(scale, 0, 0, scale, 0, 0);
        // Resizing clears the canvas: paint again at once so it never shows a black frame.
        paint();
    });
    const observer = new MutationObserver(state);
    observer.observe(progress, {attributes: true, attributeFilter: ['hidden']});
    observer.observe(media, {childList: true}); resize.observe(stage);
    state();
    return () => {
        disposed = true; observer.disconnect(); resize.disconnect();
        canvas.remove(); emptyIcon.remove();
    };
}
