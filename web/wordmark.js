// All artwork is served by this custom node; no font/CDN request is needed.
// Not called branding.js: ad-block lists block scripts by that name.
const css = document.createElement('link');
css.rel = 'stylesheet'; css.href = new URL('./theme.css', import.meta.url).href;
document.head.append(css);

// The official logo in the version made for the ground it sits on: light letters on the dark theme, dark letters on
// the light one (html[data-theme=light]). Every logo on the page follows a change of theme at once.
const DARK_GROUND = new URL('./assets/freevideo.svg', import.meta.url).href;
const LIGHT_GROUND = new URL('./assets/freevideo-light.svg', import.meta.url).href;
export const wordmarkSource = theme => theme === 'light' ? LIGHT_GROUND : DARK_GROUND;
const current = () => wordmarkSource(document.documentElement?.dataset?.theme);
let watching = false;
function watchTheme() {
    if (watching || typeof MutationObserver !== 'function' || !document.documentElement) return;
    watching = true;
    new MutationObserver(() => {
        const src = current();
        for (const image of document.querySelectorAll('img.fv-wordmark[data-fv-themed]')) if (image.src !== src) image.src = src;
    }).observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
}

export function wordmark() {
    const image = document.createElement('img');
    image.className = 'fv-wordmark';
    image.src = current(); image.dataset.fvThemed = 'true';
    image.alt = 'FreeVideo'; image.width = 2016; image.height = 337;
    image.setAttribute('translate', 'no');
    image.draggable = false;
    watchTheme();
    return image;
}
