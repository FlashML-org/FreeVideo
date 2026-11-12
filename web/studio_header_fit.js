import {placeViewSwitch} from './view_switch_place.js';

// The view switch takes the place every view shares (view_switch_place.js). Beside it, the creation panel's logo takes
// its smaller size when its side is narrower; the entries give way in order: the status capsule's words, the capsule,
// the phone entry, the feedback entry, and the Tutorial entry last. On two rows the capsule starts gone and the same
// entries leave in that order.
export function fitStudioHeader(header, brand, navigation, actions) {
    if (typeof ResizeObserver !== 'function' || typeof getComputedStyle !== 'function' || typeof matchMedia !== 'function') return {check() {}, dispose() {}};
    const px = value => parseFloat(value) || 0;
    function check() {
        if (!header.isConnected || !header.clientWidth) return;
        // Measure the plain entries, so the result never depends on the fit applied before.
        delete header.dataset.fit;
        const place = placeViewSwitch(header, navigation);
        const apart = px(getComputedStyle(actions).columnGap), shown = [...actions.children].filter(child => child.offsetWidth > 0);
        const need = shown.reduce((sum, child) => sum + Math.max(child.getBoundingClientRect().width, child.scrollWidth), 0) + apart * Math.max(0, shown.length - 1);
        const width = selector => { const found = actions.querySelector(selector); return found && found.offsetWidth > 0 ? found.offsetWidth + apart : 0; };
        const away = width('.fv-lan'), spare = width('.fv-feedback-entry:not(.fv-guide-entry)'), last = width('.fv-guide-entry');
        const capsule = width('.fv-monitor'), label = actions.querySelector('.fv-monitor-text'), words = label && label.offsetWidth > 0 ? label.offsetWidth + px(getComputedStyle(label.parentElement).columnGap) : 0;
        const order = [['compact', words], ['gone', capsule - words], ['tight', away], ['lean', spare], ['least', last]];
        const tokens = [];
        let room, left = need;
        if (place.wrap) {
            // On two rows the actions share the first row with the logo only.
            const items = [...brand.children].filter(child => child.offsetWidth > 0);
            const mark = items.reduce((sum, child) => sum + child.getBoundingClientRect().width, 0) + px(getComputedStyle(brand).columnGap) * Math.max(0, items.length - 1);
            room = place.inner - mark - place.gap;
            tokens.push('wrap', 'gone'); left -= capsule;
            for (const [token, saved] of order.slice(2)) { if (left <= room) break; left -= saved; tokens.push(token); }
        } else {
            const logo = brand.querySelector('.fv-wordmark');
            if (logo && logo.offsetWidth > place.left) tokens.push('small');
            room = place.right;
            for (const [token, saved] of order) { if (left <= room) break; left -= saved; tokens.push(token); }
        }
        if (tokens.length) header.dataset.fit = tokens.join(' ');
    }
    const observer = new ResizeObserver(check);
    observer.observe(header); observer.observe(navigation); observer.observe(actions);
    return {check, dispose: () => observer.disconnect()};
}
