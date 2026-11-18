// The light behind the views (the stage look): two or three main colours of the chosen work's cover, laid out as a soft
// gradient behind the whole window (top bar included), darkened on the dark theme and lightened on the light one, so
// no shape of the work can be made out. One light for every view: the colours live on <html> (--fv-amb-1..3), each
// open view dialog carries the same layer, so switching views, works or dialogs never starts from black or white.
// It changes only when the chosen work changes, once, as one 0.6 s blend (stage_light.css), and not at all when the
// new colours are close to the ones shown. A playing video never changes it: the cover is read, not the frames. Until
// a new cover has loaded the colours stay as they are. Read-only towards the views: it looks at pictures already on
// the page and makes no request. Off under the calm look, with glass off or with reduced transparency.
const root = document.documentElement;
const W = 32, H = 20, CLOSE = 10, KEY = 'freevideo.light';

function addSheet() {
    if (document.querySelector('link[data-fv-sheet="stage-light"]')) return;
    const link = document.createElement('link'); link.rel = 'stylesheet'; link.dataset.fvSheet = 'stage-light';
    link.href = new URL('./stage_light.css', import.meta.url).href; document.head.append(link);
}

const off = () => root.dataset.fvPolish === 'off' || root.dataset.fvLook === 'calm' || root.dataset.glass === 'solid'
    || root.classList.contains('lite') || globalThis.matchMedia?.('(prefers-reduced-transparency: reduce)').matches === true;
const shown = element => Boolean(element?.isConnected && element.getClientRects().length && !element.closest('[inert]'));
const loaded = image => Boolean(image?.complete && image.naturalWidth);

// ── Main colours ────────────────────────────────────────────────────────────────────────────────────────────────────
// Three groups of the picture's pixels (k-means from fixed starting points, so the same picture gives the same
// colours), largest first; a group under 8 % of the picture gives its place to the largest.
export function mainColours(data) {
    const pixels = [];
    for (let i = 0; i < data.length; i += 4) if (data[i + 3] >= 128) pixels.push([data[i], data[i + 1], data[i + 2]]);
    if (!pixels.length) return null;
    const luma = p => p[0] * .299 + p[1] * .587 + p[2] * .114;
    const sorted = [...pixels].sort((a, b) => luma(a) - luma(b));
    let centres = [1 / 6, 1 / 2, 5 / 6].map(q => [...sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))]]);
    let counts = [0, 0, 0];
    for (let round = 0; round < 8; round++) {
        const sums = centres.map(() => [0, 0, 0]); counts = [0, 0, 0];
        for (const p of pixels) {
            let best = 0, bestD = Infinity;
            centres.forEach((c, k) => { const d = (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2; if (d < bestD) { bestD = d; best = k; } });
            sums[best][0] += p[0]; sums[best][1] += p[1]; sums[best][2] += p[2]; counts[best]++;
        }
        centres = centres.map((c, k) => counts[k] ? sums[k].map(v => v / counts[k]) : c);
    }
    const groups = centres.map((c, k) => ({c, n: counts[k]})).sort((a, b) => b.n - a.n);
    return groups.map(g => (g.n >= pixels.length * .08 ? g.c : groups[0].c).map(Math.round));
}
// The work's colour leads: the most colourful of the groups (over 8 % of the picture) comes first, and a grey group
// takes that colour's hue at low strength, so a portrait by a white window still gives the shirt's warmth, not grey.
export function lead(colours) {
    const shades = colours.map(hsl);
    let best = 0; shades.forEach((x, k) => { if (x[1] * (1 - Math.abs(2 * x[2] - 1)) > shades[best][1] * (1 - Math.abs(2 * shades[best][2] - 1))) best = k; });
    const [h, s] = shades[best];
    if (s < .12) return colours;                                                        // a grey work stays grey
    const order = [best, ...colours.keys()].filter((k, i, all) => all.indexOf(k) === i);
    return order.map(k => shades[k][1] < .12 ? rgb([h, s * .45, shades[k][2]]) : colours[k]);
}

function hsl([r, g, b]) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2, d = max - min;
    if (!d) return [0, 0, l];
    const s = d / (1 - Math.abs(2 * l - 1));
    const h = max === r ? ((g - b) / d + 6) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [h * 60, s, l];
}
function rgb([h, s, l]) {
    const c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs((h / 60) % 2 - 1)), m = l - c / 2;
    const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
    return [r, g, b].map(v => Math.round((v + m) * 255));
}
// Toned for the theme: deep on dark, pale on light, with the colour kept strong enough to read; the hue stays, the picture's brightness only
// nudges the tone, so a white or a black work still gives a calm ground.
export function tone(colour, light) {
    const [h, s, l] = hsl(colour);
    return rgb(light ? [h, Math.min(.55, s * .96), .82 + l * .07] : [h, Math.min(.6, s), .17 + l * .1]);
}
const near = (a, b) => a && b && a.every((c, k) => c.every((v, i) => Math.abs(v - b[k][i]) <= CLOSE));

// ── What the view in front shows ────────────────────────────────────────────────────────────────────────────────────
const covers = new Map();         // cover address -> Image (loaded once; a load asks for a look)
function cover(url) {
    let image = covers.get(url);
    if (!image) {
        if (covers.size > 40) covers.clear();
        image = new Image(); image.decoding = 'async'; image.onload = () => soon(); image.src = url; covers.set(url, image);
    }
    return loaded(image) ? image : null;
}
const frontDialog = () => {
    const open = [...document.querySelectorAll('dialog.fv-studio[open]')].filter(shown);
    const view = root.dataset.fvView;
    const cls = {image: 'fv-image', editor: 'fv-editor', autopilot: 'fv-autopilot'}[view];
    return (cls ? open.find(d => d.classList.contains(cls)) : open.find(d => d.classList.contains('fv-create'))) || null;
};
// The chosen work's cover; null while it is still loading (the light then stays as it is).
function subject(dialog) {
    if (dialog.classList.contains('fv-create')) {
        const video = [...dialog.querySelectorAll('.fv-stage video')].find(shown);
        if (video?.poster) { const poster = cover(video.poster); if (poster) return {element: poster, key: poster.src}; }
        const chosen = dialog.querySelector('.fv-recent-card[aria-pressed=true] img');
        if (loaded(chosen)) return {element: chosen, key: chosen.currentSrc || chosen.src};
        if (video?.poster) return null;
        const picture = [...dialog.querySelectorAll('.fv-stage img')].find(image => loaded(image) && image.naturalWidth > 32 && shown(image));
        if (picture) return {element: picture, key: picture.currentSrc || picture.src};
        // A video without a cover: its first frame, read once for that video (never again while it plays).
        if (video && video.readyState >= 2 && video.videoWidth) return {element: video, key: video.currentSrc};
        return null;
    }
    if (dialog.classList.contains('fv-image')) {
        const picture = dialog.querySelector('.fv-iv-picture');
        if (loaded(picture) && picture.dataset.loaded === 'true' && shown(picture)) return {element: picture, key: picture.currentSrc || picture.src};
        return null;
    }
    if (dialog.classList.contains('fv-editor')) {
        const video = dialog.querySelector('video.fv-ed-visible');
        if (!video || video.readyState < 2 || !video.videoWidth || !video.paused) return null;   // a playing timeline keeps its light
        return {element: video, key: video.currentSrc};
    }
    return null;                                                                                  // other views keep the light they found
}

// ── The light ───────────────────────────────────────────────────────────────────────────────────────────────────────
let raw = null, toned = null, lastKey = '';
try { const saved = JSON.parse(localStorage.getItem(KEY) || 'null'); if (Array.isArray(saved) && saved.length === 3) raw = saved; } catch { /* none */ }
const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H;

function paint(instant) {
    if (!raw) return;
    const next = lead(raw).map(c => tone(c, root.dataset.theme === 'light'));
    if (!instant && near(next, toned)) return;
    toned = next;
    if (instant) root.dataset.fvLightInstant = 'true';
    next.forEach((c, k) => root.style.setProperty(`--fv-amb-${k + 1}`, `rgb(${c.join(' ')})`));
    if (instant) requestAnimationFrame(() => requestAnimationFrame(() => { delete root.dataset.fvLightInstant; }));
}
const VIEWS = 'dialog.fv-studio:is(.fv-create,.fv-image,.fv-editor,.fv-autopilot)';   // the views, not share, library or viewers
function dress() {
    for (const dialog of document.querySelectorAll(VIEWS + '[open]')) {
        if (dialog.querySelector(':scope>.fv-look-light')) continue;
        const box = document.createElement('div'); box.className = 'fv-look-light'; box.setAttribute('aria-hidden', 'true');
        dialog.prepend(box);
    }
    // On the page root: an unchanged value is left alone, or the whole page would restyle.
    if (root.dataset.fvLight !== (raw ? 'on' : 'off')) root.dataset.fvLight = raw ? 'on' : 'off';
}

let timer = 0, theme = root.dataset.theme;
function refresh() {
    timer = 0;
    if (document.visibilityState === 'hidden' || off()) return;
    if (theme !== root.dataset.theme) { theme = root.dataset.theme; paint(false); }
    const dialog = frontDialog();
    if (dialog) {
        const found = subject(dialog);
        if (found && found.key !== lastKey) {
            try {
                const context = canvas.getContext('2d', {willReadFrequently: true});
                context.clearRect(0, 0, W, H);
                context.drawImage(found.element, 0, 0, W, H);
                const colours = mainColours(context.getImageData(0, 0, W, H).data);   // a picture from another origin throws here
                if (colours) {
                    lastKey = found.key; raw = colours;
                    try { localStorage.setItem(KEY, JSON.stringify(raw)); } catch { /* the light still changes */ }
                    paint(false);
                }
            } catch { /* a picture that cannot be read leaves the light as it was */ }
        }
    }
    dress();
}
const soon = () => { if (!timer) timer = setTimeout(refresh, 80); };

if (!globalThis.FreeVideoStageLight) {
    globalThis.FreeVideoStageLight = {refresh};
    addSheet();
    paint(true);
    // A view that opens gets its layer at once (in the same frame), not after the pause: the dialog's own ground would
    // otherwise show for a frame (seen as a flash on the light theme).
    new MutationObserver(records => { if (records.some(r => r.attributeName === 'open' || r.attributeName === 'data-fv-view')) dress(); soon(); })
        .observe(root, {subtree: true, childList: false, attributes: true,
        attributeFilter: ['src', 'poster', 'aria-pressed', 'open', 'data-loaded', 'data-fv-view', 'data-theme', 'data-fv-look', 'data-fv-polish', 'data-glass']});
    for (const type of ['load', 'loadeddata', 'pause']) document.addEventListener(type, soon, true);
    document.addEventListener('visibilitychange', soon);
    soon();
}
