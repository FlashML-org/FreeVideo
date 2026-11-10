// The creation panel's header keeps its view switch centred. When the actions beside it would not fit, the phone entry steps
// aside first (after the status capsule gives way: first its words, then the capsule), then the feedback entry, then the switch moves to a second row; on two rows the same entries still leave
// in that order, and the Tutorial entry last.
export function fitStudioHeader(header, brand, navigation, actions) {
    if (typeof ResizeObserver !== 'function' || typeof getComputedStyle !== 'function') return {check() {}, dispose() {}};
    const px = value => parseFloat(value) || 0;
    function check() {
        if (!header.isConnected || !header.clientWidth) return;
        // Measure the plain layout, so the result never depends on the one applied before.
        delete header.dataset.fit;
        const box = getComputedStyle(header), gap = px(box.columnGap), inner = header.clientWidth - px(box.paddingLeft) - px(box.paddingRight);
        const apart = px(getComputedStyle(actions).columnGap), shown = [...actions.children].filter(child => child.offsetWidth > 0);
        const need = shown.reduce((sum, child) => sum + Math.max(child.getBoundingClientRect().width, child.scrollWidth), 0) + apart * Math.max(0, shown.length - 1);
        const width = selector => { const found = actions.querySelector(selector); return found && found.offsetWidth > 0 ? found.offsetWidth + apart : 0; };
        const away = width('.fv-lan'), spare = width('.fv-feedback-entry:not(.fv-guide-entry)'), last = width('.fv-guide-entry');
        const capsule = width('.fv-monitor'), label = actions.querySelector('.fv-monitor-text'), words = label && label.offsetWidth > 0 ? label.offsetWidth + px(getComputedStyle(label.parentElement).columnGap) : 0;
        const items = [...brand.children].filter(child => child.offsetWidth > 0), mark = items.reduce((sum, child) => sum + child.getBoundingClientRect().width, 0) + px(getComputedStyle(brand).columnGap) * Math.max(0, items.length - 1);
        const logo = brand.querySelector('.fv-wordmark'), smaller = logo ? Math.max(0, logo.offsetWidth - 150) : 0;
        // The one-row fit for a given brand width; the wrapped layout only needs the brand and the actions side by side.
        const one = (room, brandWidth) => (inner - navigation.offsetWidth - 2 * gap) / 2 >= Math.max(room, brandWidth);
        const row = (room, brandWidth) => inner - brandWidth - gap >= room;
        // Before anything gives way, the switch may sit a little left of centre when the actions only just overflow their half.
        const shifted = (room, brandWidth) => inner - brandWidth - navigation.offsetWidth - 2 * gap >= room;
        // What gives way, in order: the switch moves left, the wordmark shrinks, the capsule's words, the capsule, the phone entry, the feedback entry, the guide entry.
        // The capsule never comes back once it has gone: the wrapped layout starts without it.
        const order = [['compact', words], ['gone', capsule - words], ['tight', away], ['lean', spare], ['least', last]];
        const steps = [['', need, mark, one], ['shift', need, mark, shifted]];
        let room = need, tokens = ['shift'];
        if (smaller) { tokens.push('small'); steps.push([tokens.join(' '), room, mark - smaller, shifted]); }
        for (const [token, saved] of order.slice(0, 4)) { room -= saved; tokens.push(token); steps.push([tokens.join(' '), room, mark - smaller, shifted]); }
        room = need - capsule; tokens = ['wrap', 'gone'];
        steps.push([tokens.join(' '), room, mark, row]);
        for (const [token, saved] of order.slice(2)) { room -= saved; tokens.push(token); steps.push([tokens.join(' '), room, mark, row]); }
        const found = steps.find(([, room, width, fits]) => fits(room, width));
        const fit = found ? found[0] : steps[steps.length - 1][0];
        if (fit) header.dataset.fit = fit;
    }
    const observer = new ResizeObserver(check);
    observer.observe(header); observer.observe(navigation); observer.observe(actions);
    return {check, dispose: () => observer.disconnect()};
}
