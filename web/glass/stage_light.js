// The picture is the light (the stage look): behind the Create view, the work in front, drawn small (64 x 40) and
// stretched by CSS, so the ground around the stage and the glass over it carry the work's colours. The image view and
// the editor draw their own (fv-iv-ambient, fv-ed-ambient; stage_light.css shows them again under this look).
// Read-only towards the view: it looks at the picture already on the page and makes no request. One small draw when
// the work changes, and while a video plays at most one every 900 ms; nothing while the page is hidden, under the calm
// look, with glass off or with reduced transparency. A picture that cannot be read leaves the light as it was.
const root = document.documentElement;
const W = 64, H = 40, PLAY_EVERY = 900;

function addSheet() {
    if (document.querySelector('link[data-fv-sheet="stage-light"]')) return;
    const link = document.createElement('link'); link.rel = 'stylesheet'; link.dataset.fvSheet = 'stage-light';
    link.href = new URL('./stage_light.css', import.meta.url).href; document.head.append(link);
}

const off = () => root.dataset.fvPolish === 'off' || root.dataset.fvLook === 'calm' || root.dataset.glass === 'solid'
    || root.classList.contains('lite') || globalThis.matchMedia?.('(prefers-reduced-transparency: reduce)').matches === true;

// What the stage shows: the video's current frame once it has one (while it plays, the frame of the moment), else
// its cover picture, else a picture on the stage, else the chosen work's thumbnail.
const covers = new Map();         // cover address -> Image (loaded once; a load asks for a redraw)
function cover(url) {
    let image = covers.get(url);
    if (!image) {
        if (covers.size > 40) covers.clear();
        image = new Image(); image.decoding = 'async'; image.onload = () => soon(); image.src = url; covers.set(url, image);
    }
    return image.complete && image.naturalWidth ? image : null;
}
function subject(dialog) {
    const video = [...dialog.querySelectorAll('.fv-stage video')].find(element => element.getClientRects().length);
    if (video && video.readyState >= 2 && video.videoWidth) {
        const key = `${video.currentSrc}|${Math.round(video.currentTime * 1.1)}`;
        return {element: video, key, playing: !video.paused && !video.ended};
    }
    const poster = video?.poster ? cover(video.poster) : null;
    if (poster) return {element: poster, key: poster.src};
    const picture = [...dialog.querySelectorAll('.fv-stage img')].find(image => image.complete && image.naturalWidth > 32 && image.getClientRects().length);
    if (picture) return {element: picture, key: picture.currentSrc || picture.src};
    const chosen = dialog.querySelector('.fv-recent-card[aria-pressed=true] img');
    if (chosen && chosen.complete && chosen.naturalWidth) return {element: chosen, key: chosen.currentSrc || chosen.src};
    return null;
}

const lights = new WeakMap();      // dialog -> {box, canvases, flip, last}
function light(dialog) {
    let state = lights.get(dialog);
    if (state && state.box.isConnected) return state;
    const box = document.createElement('div'); box.className = 'fv-look-light'; box.setAttribute('aria-hidden', 'true');
    const canvases = [0, 1].map(() => { const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H; box.append(canvas); return canvas; });
    dialog.prepend(box);
    state = {box, canvases, flip: 0, last: ''};
    lights.set(dialog, state);
    return state;
}

let timer = 0, playTimer = 0;
function refresh() {
    timer = 0;
    if (document.visibilityState === 'hidden' || off()) return;
    let playing = false;
    for (const dialog of document.querySelectorAll('dialog.fv-create[open]')) {
        const found = subject(dialog);
        if (!found) continue;                                    // Keep the last light until a picture is there.
        playing ||= !!found.playing;
        const state = light(dialog);
        if (state.last === found.key) continue;
        const next = state.canvases[state.flip], previous = state.canvases[1 - state.flip];
        try {
            const context = next.getContext('2d');
            context.filter = 'blur(3px) saturate(1.15)';
            context.drawImage(found.element, -6, -6, W + 12, H + 12);
            context.filter = 'none';
            context.getImageData(0, 0, 1, 1);                    // A picture from another origin throws here.
        } catch { continue; }
        state.last = found.key; state.flip = 1 - state.flip;
        next.classList.add('fv-on'); previous.classList.remove('fv-on'); state.box.dataset.on = 'true';
    }
    // While a video plays, its light follows it now and then; it rests as soon as nothing plays.
    if (playing && !playTimer) playTimer = setInterval(() => { if (!timer) refresh(); }, PLAY_EVERY);
    if (!playing && playTimer) { clearInterval(playTimer); playTimer = 0; }
}
const soon = () => { if (!timer) timer = setTimeout(refresh, 120); };

if (!globalThis.FreeVideoStageLight) {
    globalThis.FreeVideoStageLight = {refresh};
    addSheet();
    new MutationObserver(soon).observe(root, {subtree: true, childList: false, attributes: true,
        attributeFilter: ['src', 'poster', 'aria-pressed', 'open', 'data-fv-look', 'data-fv-polish', 'data-glass']});
    for (const type of ['load', 'loadeddata', 'seeked', 'play', 'pause']) document.addEventListener(type, soon, true);
    document.addEventListener('visibilitychange', soon);
    soon();
}
