// The stage light: the picture under the playhead, drawn now and then into a 64×40 canvas behind the editor and scaled
// up by CSS (never blurred live), so the glass panes carry a soft trace of the picture's colours. Off for the solid
// material, reduced transparency, and a hidden page; one small draw every 900 ms at most, none while nothing changed.
const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-transparency: reduce)').matches;
const solid = () => document.documentElement.dataset.glass === 'solid' || document.documentElement.classList.contains('lite');

export function createStageLight(host, {picture}) {
    if (typeof document === 'undefined' || typeof HTMLCanvasElement === 'undefined') return {dispose() {}};
    const box = document.createElement('div'); box.className = 'fv-ed-ambient'; box.setAttribute('aria-hidden', 'true');
    const canvases = [0, 1].map(() => { const canvas = document.createElement('canvas'); canvas.width = 64; canvas.height = 40; box.append(canvas); return canvas; });
    host.prepend(box);
    let flip = 0, timer = 0, last = '', disposed = false;
    function draw() {
        if (disposed || document.visibilityState === 'hidden' || reduced() || solid()) { box.dataset.on = 'false'; return; }
        // A kept editor out of sight (its dialog closed) draws nothing until it comes back.
        if (!host.isConnected || host.closest('dialog:not([open])')) return;
        const video = picture.querySelector('video.fv-ed-visible');
        if (!video || video.readyState < 2 || !video.videoWidth) return;
        const stamp = `${video.currentSrc}|${Math.round(video.currentTime * 2)}`;
        if (stamp === last) return;
        last = stamp;
        const next = canvases[flip], previous = canvases[1 - flip];
        try {
            const context = next.getContext('2d');
            context.filter = 'blur(3px) saturate(1.15)';
            context.drawImage(video, -6, -6, 76, 52);
            context.filter = 'none';
        } catch { return; } // A picture from another origin cannot be read: no light, nothing else changes.
        flip = 1 - flip; next.classList.add('fv-on'); previous.classList.remove('fv-on'); box.dataset.on = 'true';
    }
    timer = setInterval(draw, 900);
    return {refresh: draw, dispose() { disposed = true; clearInterval(timer); box.remove(); }};
}
