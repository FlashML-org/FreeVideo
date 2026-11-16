// The stage look's light takes the colour of the work in front (polish.css reads --fv-look-tint on the view's dialog).
// Read-only towards the views: it looks at the picture already on the page, averages it on a 16 x 16 canvas and
// writes one custom property. No request is made. Only under html[data-fv-look=stage] with the layer on; a picture
// that cannot be read (not loaded, another origin) leaves the neutral light.
const root = document.documentElement;
const SIZE = 16;
const cache = new Map();          // picture address -> "r g b" or '' (neutral)
let canvas = null, context = null, timer = 0;

const active = () => root.dataset.fvPolish !== 'off' && root.dataset.fvLook !== 'calm';

// The mean colour, brought to one brightness and eased towards grey, so a dark picture and a bright one give the
// same amount of light and no picture gives a loud one. Near-grey pictures give the neutral light.
export function tintOf(pixels) {
    let r = 0, g = 0, b = 0, n = 0;
    for (let i = 0; i < pixels.length; i += 4) { if (pixels[i + 3] < 8) continue; r += pixels[i]; g += pixels[i + 1]; b += pixels[i + 2]; n++; }
    if (!n) return '';
    r /= n; g /= n; b /= n;
    const high = Math.max(r, g, b), low = Math.min(r, g, b);
    if (high < 6 || high - low < 10) return '';
    const grey = (r + g + b) / 3, keep = 0.7, scale = 176 / high;
    return [r, g, b].map(value => Math.round(Math.min(255, (grey + (value - grey) * keep) * scale))).join(' ');
}

function read(element) {
    try {
        canvas ||= document.createElement('canvas'); canvas.width = canvas.height = SIZE;
        context ||= canvas.getContext('2d', {willReadFrequently: true});
        context.clearRect(0, 0, SIZE, SIZE); context.drawImage(element, 0, 0, SIZE, SIZE);
        return tintOf(context.getImageData(0, 0, SIZE, SIZE).data);
    } catch { return ''; }        // A picture from another origin: neutral.
}

// The picture in front of a view, with the address that names it; null while there is none or it has not loaded.
function subject(dialog) {
    const shown = picture => picture.complete && picture.naturalWidth && picture.getClientRects().length;
    const named = element => ({element, key: element.currentSrc || element.src});
    if (dialog.classList.contains('fv-image')) {
        const picture = [...dialog.querySelectorAll('.fv-iv-frame img')].find(shown);
        return picture ? named(picture) : null;
    }
    // The chosen work's thumbnail stands for the video (a video's first frame is often black).
    const chosen = dialog.querySelector('.fv-recent-card[aria-pressed=true] img');
    if (chosen && shown(chosen)) return named(chosen);
    const video = dialog.querySelector('.fv-stage video');
    return video && video.readyState >= 2 && video.videoWidth && video.getClientRects().length ? named(video) : null;
}

function refresh() {
    timer = 0;
    if (!active()) return;
    for (const dialog of document.querySelectorAll('dialog.fv-create[open],dialog.fv-image[open]')) {
        const found = subject(dialog);
        if (!found?.key) continue;                               // Keep the last light until a picture is there.
        if (!cache.has(found.key)) { if (cache.size > 200) cache.clear(); cache.set(found.key, read(found.element)); }
        const tint = cache.get(found.key);
        if (dialog.dataset.fvLookTint === tint) continue;
        dialog.dataset.fvLookTint = tint;
        if (tint) dialog.style.setProperty('--fv-look-tint', tint); else dialog.style.removeProperty('--fv-look-tint');
    }
}
const soon = () => { if (!timer) timer = setTimeout(refresh, 200); };

if (!globalThis.FreeVideoPolish) {
    globalThis.FreeVideoPolish = {refresh, tintOf};
    new MutationObserver(soon).observe(document.documentElement, {subtree: true, childList: true, attributes: true,
        attributeFilter: ['src', 'poster', 'aria-pressed', 'open', 'data-fv-look', 'data-fv-polish', 'data-fv-view']});
    for (const type of ['load', 'loadeddata']) document.addEventListener(type, soon, true);
    soon();
}

// The studio's Media tray is laid out by its own module (presentation only; it installs itself).
import('./media_tray.js').catch(error => console.warn('FreeVideo media tray:', error));
// The studio's Advanced tray, likewise (its controls stay studio.js's own; the run mode joins it from under Generate).
// If it cannot load, the run mode stays under Generate where studio.js built it (fv-at-moved is taken off again).
import('./advanced_tray.js').catch(error => {
    console.warn('FreeVideo advanced tray:', error); document.documentElement.dataset.fvAt = 'failed';
    const keep = () => { for (const row of document.querySelectorAll('.fv-runrow.fv-at-moved')) row.classList.remove('fv-at-moved'); };
    keep(); new MutationObserver(keep).observe(document.body, {childList: true, subtree: true});   // also rows built later
});
// The look layer over the views (glass and light). It installs itself; ?look=off leaves it out.
import('./look_lux.js').catch(error => console.warn('FreeVideo look layer:', error));
