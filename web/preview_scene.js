import { emptyFrameIcon } from './empty_frame.js';
// A small, local canvas scene. The sea keeps moving while the water level follows
// the existing estimate; animation never advances the reported progress itself.
export function createPreviewScene(stage, progress, media) {
    const canvas = document.createElement('canvas');
    canvas.className = 'fv-preview-scene'; canvas.setAttribute('aria-hidden', 'true');
    stage.prepend(canvas);
    const emptyIcon = emptyFrameIcon(); stage.append(emptyIcon);
    const ctx = canvas.getContext('2d', {alpha: false});
    if (!ctx) return () => canvas.remove();
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    let width = 0, height = 0, frame = null, last = null, disposed = false;
    let target = .16, level = target, visible = true, generating = false, phase = 0;

    function wave(x, layer) {
        const amplitude = Math.min(24, height * .075) * (reduced.matches ? .6 : 1);
        const drift = phase * (1 + layer * .12);
        const crest = Math.sin(x * Math.PI * 3.2 - drift + layer * 1.7);
        const swell = Math.sin(x * Math.PI * 5.4 + drift * .7 + layer * .8);
        const breath = .82 + .18 * Math.sin(phase * .55 + x * Math.PI * 2 + layer);
        return height * (1 - level) + (layer - 1) * amplitude * .32
            + amplitude * breath * (.7 * crest + .3 * swell);
    }

    function paint() {
        ctx.clearRect(0, 0, width, height);
        const sky = ctx.createLinearGradient(0, 0, width * .6, height);
        sky.addColorStop(0, '#0c1420'); sky.addColorStop(.55, '#101f30'); sky.addColorStop(1, '#152c41');
        ctx.fillStyle = sky; ctx.fillRect(0, 0, width, height);
        const glow = ctx.createRadialGradient(width * .7, height * .85, 0, width * .7, height * .85, width * .85);
        glow.addColorStop(0, '#477db822'); glow.addColorStop(1, '#477db800');
        ctx.fillStyle = glow; ctx.fillRect(0, 0, width, height);
        for (let layer = 0; layer < 3; layer++) {
            const points = [];
            for (let i = 0; i <= 56; i++) points.push([width * i / 56, wave(i / 56, layer)]);
            ctx.beginPath(); ctx.moveTo(...points[0]);
            for (const point of points.slice(1)) ctx.lineTo(...point);
            ctx.lineTo(width, height); ctx.lineTo(0, height); ctx.closePath();
            const water = ctx.createLinearGradient(0, height * (1 - level) - 24, width * .25, height);
            water.addColorStop(0, ['#659fbc38', '#418cae80', '#346790c9'][layer]);
            water.addColorStop(1, ['#24466538', '#19334fbb', '#172e49'][layer]);
            ctx.fillStyle = water; ctx.fill();
            ctx.save(); ctx.clip();
            // Broad, faint reflections rise through the water, with different
            // phases so there is no synchronized reset or marching pattern.
            if (layer === 2) {
                for (let i = 0; i < 3; i++) {
                    const travel = ((phase * .105 + i / 3) % 1 + 1) % 1;
                    const y = height * (1.3 - travel * 1.55);
                    const alpha = Math.sin(travel * Math.PI) * .13;
                    ctx.beginPath();
                    ctx.moveTo(-width * .2, y + height * .16);
                    ctx.bezierCurveTo(width * .2, y - height * .25, width * .6, y + height * .28, width * 1.2, y - height * .12);
                    for (const [size, opacity] of [[.09, .18], [.04, .28], [.002, .85]]) {
                        ctx.strokeStyle = `rgba(176,225,248,${alpha * opacity})`;
                        ctx.lineWidth = Math.max(.8, height * size); ctx.stroke();
                    }
                }
            }
            ctx.restore();
            ctx.beginPath(); ctx.moveTo(...points[0]);
            for (const point of points.slice(1)) ctx.lineTo(...point);
            ctx.strokeStyle = ['#b2dcf03d', '#b3edff4d', '#a9dcef66'][layer];
            ctx.lineWidth = .85; ctx.stroke();
        }
        const shade = ctx.createRadialGradient(width / 2, height / 2, 0, width / 2, height / 2, Math.max(width, height) * .68);
        shade.addColorStop(0, '#060c1700'); shade.addColorStop(1, '#060c174d');
        ctx.fillStyle = shade; ctx.fillRect(0, 0, width, height);
    }

    function draw(time) {
        frame = null;
        if (disposed || !visible || document.hidden || !width || !height) { last = null; return; }
        // Keep a gentle live surface even on remote Windows desktops that ask
        // for reduced motion. That preference lowers speed/amplitude; it must
        // not turn a running request into a frozen picture sliding upwards.
        const moving = generating || !reduced.matches;
        if (moving && last !== null && time - last < 1000 / (reduced.matches ? 15 : 30)) { frame = requestAnimationFrame(draw); return; }
        const elapsed = last === null ? 0 : Math.max(0, (time - last) / 1000); last = time;
        // Use elapsed time for the surface. Clamping it to 100 ms made waves
        // nearly freeze when a busy/remote browser delivered fewer frames.
        phase += moving ? elapsed * (reduced.matches ? .85 : generating ? 1.4 : .48) : 0;
        const delta = Math.min(.1, elapsed);
        level = moving ? level + (target - level) * (1 - Math.exp(-delta * 4)) : target;
        paint();
        if (moving) frame = requestAnimationFrame(draw);
    }
    function schedule() {
        if (frame === null && !disposed && visible && !document.hidden) frame = requestAnimationFrame(draw);
    }
    function visibility() {
        if (document.hidden) { cancelAnimationFrame(frame); frame = null; last = null; }
        else schedule();
    }
    function state() {
        generating = !progress.hidden;
        const empty = !generating && !media.firstElementChild;
        // Before the first video the frame is a plain placeholder; the sea only moves while a request runs.
        stage.dataset.empty = String(empty); canvas.hidden = empty;
        visible = !empty && (generating || !media.querySelector('video'));
        const value = parseFloat(progress.style.getPropertyValue('--fv-progress'));
        target = generating ? progress.dataset.counted === 'true' && Number.isFinite(value)
            ? Math.min(1.06, Math.max(0, value / 100) + (value === 100 ? .06 : 0)) : .08 : .16;
        // Stop scheduling completely once the video is on screen.
        if (!visible && frame !== null) { cancelAnimationFrame(frame); frame = null; last = null; }
        schedule();
    }
    const resize = new ResizeObserver(() => {
        width = stage.clientWidth; height = stage.clientHeight;
        const scale = Math.min(window.devicePixelRatio || 1, 1.5);
        canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
        ctx.setTransform(scale, 0, 0, scale, 0, 0);
        // Resizing clears the canvas; paint now so the throttled frame loop cannot leave a black frame.
        if (!disposed && visible && width && height) paint();
        schedule();
    });
    const observer = new MutationObserver(state);
    observer.observe(progress, {attributes: true, attributeFilter: ['hidden', 'style', 'data-counted']});
    observer.observe(media, {childList: true}); resize.observe(stage);
    document.addEventListener('visibilitychange', visibility); reduced.addEventListener('change', schedule);
    state();
    return () => {
        disposed = true; cancelAnimationFrame(frame); observer.disconnect(); resize.disconnect();
        document.removeEventListener('visibilitychange', visibility); reduced.removeEventListener('change', schedule);
        canvas.remove(); emptyIcon.remove();
    };
}
