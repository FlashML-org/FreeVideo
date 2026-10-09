// All artwork is served by this custom node; no font/CDN request is needed.
// Not called branding.js: ad-block lists block scripts by that name.
const css = document.createElement('link');
css.rel = 'stylesheet'; css.href = new URL('./theme.css', import.meta.url).href;
document.head.append(css);

export function wordmark() {
    const image = document.createElement('img');
    image.className = 'fv-wordmark';
    image.src = new URL('./assets/freevideo.svg', import.meta.url).href;
    image.alt = 'FreeVideo'; image.width = 2016; image.height = 337;
    image.draggable = false;
    return image;
}
