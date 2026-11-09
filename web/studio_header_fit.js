// The creation panel's header keeps its view switch centred. When the actions beside it would not fit, the phone entry steps
// aside first, then the feedback entry, then the switch moves to a second row; on two rows the same entries still leave
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
        const items = [...brand.children].filter(child => child.offsetWidth > 0), mark = items.reduce((sum, child) => sum + child.getBoundingClientRect().width, 0) + px(getComputedStyle(brand).columnGap) * Math.max(0, items.length - 1), side = (inner - navigation.offsetWidth - 2 * gap) / 2;
        const one = room => side >= Math.max(room, mark);
        const row = room => inner - mark - gap >= room;
        const steps = [['', need, one], ['tight', need - away, one], ['tight lean', need - away - spare, one],
            ['wrap', need, row], ['wrap tight', need - away, row], ['wrap tight lean', need - away - spare, row], ['wrap tight lean least', need - away - spare - last, row]];
        const found = steps.find(([, room, fits]) => fits(room));
        const fit = found ? found[0] : 'wrap tight lean least';
        if (fit) header.dataset.fit = fit;
    }
    const observer = new ResizeObserver(check);
    observer.observe(header); observer.observe(navigation); observer.observe(actions);
    return {check, dispose: () => observer.disconnect()};
}
