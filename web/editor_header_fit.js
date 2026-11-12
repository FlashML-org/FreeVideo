import {placeViewSwitch} from './view_switch_place.js';

// The view switch takes the place every view shares (view_switch_place.js); it never moves to keep the editor's own
// entries. Beside it the project name stays readable: the logo takes its phone size, then gives its place to the name.
// On the right the entries give way in order: the phone entry, the feedback entry, then the Tutorial entry.
// On two rows the name shares the first row with the actions only; the logo shrinks, then the same entries leave.
// About six Chinese characters or ten Latin letters of the name itself, without the menu arrow.
const READABLE = 120, COMPACT_MARK = 90;

export function fitEditorHeader(header, brand, title, navigation, actions) {
    if (typeof ResizeObserver !== 'function' || typeof getComputedStyle !== 'function' || typeof matchMedia !== 'function') return {check() {}, dispose() {}};
    const px = value => parseFloat(value) || 0;
    function check() {
        const span = title.firstElementChild, mark = brand.firstElementChild;
        if (!header.isConnected || !span || !header.clientWidth) return;
        // Measure the plain layout, so the result never depends on the one applied before.
        delete header.dataset.fit;
        const place = placeViewSwitch(header, navigation);
        const spacing = px(getComputedStyle(brand).columnGap);
        const own = getComputedStyle(title), chevron = span.nextElementSibling, text = document.createRange(); text.selectNodeContents(span);
        const frame = px(own.paddingLeft) + px(own.paddingRight) + px(own.borderLeftWidth) + px(own.borderRightWidth) + (chevron ? chevron.getBoundingClientRect().width + px(own.columnGap) : 0);
        const wanted = Math.min(READABLE, text.getBoundingClientRect().width) + frame;
        // The actions' own width: the box itself, and the buttons in it, may already be squeezed below their content.
        const shown = [...actions.children].filter(child => child.offsetWidth > 0), apart = px(getComputedStyle(actions).columnGap);
        const need = Math.max(actions.offsetWidth, shown.reduce((sum, child) => sum + Math.max(child.getBoundingClientRect().width, child.scrollWidth), 0) + apart * Math.max(0, shown.length - 1));
        const width = selector => { const found = actions.querySelector(selector); return found && found.offsetWidth > 0 ? found.offsetWidth + apart : 0; };
        const order = [['tight', width('.fv-lan')], ['lean', width('.fv-feedback-entry:not(.fv-guide-entry)')], ['least', width('.fv-guide-entry')]];
        const compact = Math.min(COMPACT_MARK, mark.offsetWidth);
        const tokens = [];
        let left = need;
        if (place.wrap) {
            // The name and the actions share the first row: the logo shrinks first, then entries leave while the name is still tight.
            const row = (markWidth, entries) => place.inner - entries - place.gap - markWidth - spacing;
            tokens.push('wrap');
            if (row(mark.offsetWidth, left) < wanted) tokens.push('compact');
            for (const [token, saved] of order) { if (row(compact, left) >= wanted) break; left -= saved; tokens.push(token); }
        } else {
            // The space the shared place leaves on the left goes to the logo first, then the name; the right holds the entries.
            if (mark.offsetWidth + spacing + wanted > place.left) tokens.push(compact + spacing + wanted <= place.left ? 'compact' : 'bare');
            for (const [token, saved] of order) { if (left <= place.right) break; left -= saved; tokens.push(token); }
        }
        if (tokens.length) header.dataset.fit = tokens.join(' ');
    }
    const observer = new ResizeObserver(check);
    observer.observe(header); observer.observe(navigation); observer.observe(actions);
    return {check, dispose: () => observer.disconnect()};
}
