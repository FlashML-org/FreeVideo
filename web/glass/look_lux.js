// The look layer over the finished views: deep layered blacks, real glass, and one slow blue light (look_lux.css).
// On by default. ?look=off, or localStorage 'freevideo.look' = 'off', leaves it out (for comparison and for finding a
// fault); then this module returns at once: no sheet, no listener.
// It changes no view code. It writes three attributes on <html> (data-fv-lux, data-fv-lux-flow, data-fv-lux-run) and
// adds one decorative, aria-hidden, absolutely placed element at the end of the studio console: the light inside
// the glass.
// The light moves only while someone is using the page or a task runs; it rests when the page is hidden, a video
// plays, or nothing has been touched for 8 s, so an idle page costs nothing.
const root = document.documentElement;
const REST_AFTER = 8000;

function wanted() {
    try { return (new URLSearchParams(location.search).get('look') || localStorage.getItem('freevideo.look')) !== 'off'; } catch { return true; }
}

if (wanted() && !globalThis.FreeVideoLux) {
    const state = {touched: performance.now(), timer: 0, flowing: null};
    globalThis.FreeVideoLux = state;
    root.dataset.fvLux = 'on';
    const sheet = document.createElement('link'); sheet.rel = 'stylesheet'; sheet.dataset.fvSheet = 'lux';
    sheet.href = new URL('./look_lux.css', import.meta.url).href; document.head.append(sheet);

    const still = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)');
    const shown = element => Boolean(element?.getClientRects().length);
    // A task is running: the status capsule says so, or the stage in front shows its progress.
    const running = () => Boolean(document.querySelector('.fv-tb-cap:is([data-state=run],[data-state=wait])'))
        || shown(document.querySelector('dialog[open] .fv-stage .fv-generation-progress:not([hidden])'));
    const playing = () => [...document.querySelectorAll('dialog[open] video')].some(video => !video.paused && !video.ended && video.readyState > 2);

    // The console's decorative part: the light inside the glass.
    const dress = () => {
        for (const dock of document.querySelectorAll('dialog.fv-create .fvs-console')) {
            if (dock.querySelector(':scope>.fv-lux-flow')) continue;
            const part = document.createElement('i'); part.className = 'fv-lux-flow'; part.setAttribute('aria-hidden', 'true'); dock.append(part);
        }
    };

    const settle = () => {
        const busy = running();
        if (busy !== (root.dataset.fvLuxRun === 'on')) { if (busy) root.dataset.fvLuxRun = 'on'; else delete root.dataset.fvLuxRun; }
        const recent = performance.now() - state.touched < REST_AFTER;
        const flow = !document.hidden && !still?.matches && !playing() && (busy || recent);
        if (flow !== state.flowing) { state.flowing = flow; root.dataset.fvLuxFlow = flow ? 'on' : 'off'; }
        clearTimeout(state.timer); state.timer = 0;
        // One timer, only while the light is moving because of a recent touch: it brings the rest.
        if (flow && !busy) state.timer = setTimeout(settle, Math.max(250, REST_AFTER - (performance.now() - state.touched) + 50));
    };

    // A touch: at most one piece of work a second, whatever the pointer does.
    let last = -Infinity;
    const touch = () => {
        const now = performance.now(); state.touched = now;
        if (now - last < 1000 && state.flowing) return;
        last = now; dress(); settle();
    };
    for (const type of ['pointermove', 'pointerdown', 'keydown', 'wheel', 'touchstart']) globalThis.addEventListener(type, touch, {capture: true, passive: true});
    document.addEventListener('visibilitychange', settle);
    for (const type of ['play', 'playing', 'pause', 'ended', 'emptied']) document.addEventListener(type, settle, true);
    still?.addEventListener?.('change', settle);
    // A task starting or ending shows on the status capsule and on the stage; a view opening adds its dialog to the page.
    new MutationObserver(settle).observe(root, {subtree: true, attributes: true, attributeFilter: ['data-state', 'hidden']});
    new MutationObserver(() => setTimeout(() => { dress(); settle(); }, 300)).observe(document.body, {childList: true});
    globalThis.addEventListener('freevideo-view', () => setTimeout(() => { dress(); settle(); }, 300));
    dress(); settle();
    for (const wait of [1000, 3000]) setTimeout(() => { dress(); settle(); }, wait);   // the view may still be building
}
