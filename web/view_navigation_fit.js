// The node view's floating bar in a narrow window: the same order as the creation panel's header. The phone entry steps
// aside first; then the entries move to a second row below the view switch, and on two rows the same entries leave in
// that order, Tutorial last. (The switch has the headers' size, so a single row that also gave up Feedback would show
// fewer entries than the two rows do.)
export function fitNavigation(bar, row, actions) {
    if (typeof ResizeObserver !== 'function' || typeof getComputedStyle !== 'function') return {check() {}, dispose() {}};
    const px = value => parseFloat(value) || 0;
    function check() {
        if (!bar.isConnected || bar.hidden) return;
        // Measure the plain layout, so the result never depends on the one applied before.
        delete bar.dataset.fit; bar.style.removeProperty('width');
        const box = getComputedStyle(bar);
        const limit = px(box.maxWidth) || innerWidth;
        const edges = px(box.paddingLeft) + px(box.paddingRight) + px(box.borderLeftWidth) + px(box.borderRightWidth);
        const room = limit - edges;
        const gap = px(getComputedStyle(row).columnGap), apart = px(getComputedStyle(actions).columnGap);
        const wide = element => element ? element.getBoundingClientRect().width : 0;
        const first = [...row.children].filter(child => child !== actions && child.offsetWidth > 0 && getComputedStyle(child).position !== 'absolute');
        const head = first.reduce((sum, child) => sum + wide(child), 0) + gap * Math.max(0, first.length - 1);
        const shown = [...actions.children].filter(child => child.offsetWidth > 0);
        const need = shown.reduce((sum, child) => sum + wide(child), 0) + apart * Math.max(0, shown.length - 1);
        const saved = selector => { const found = actions.querySelector(selector); return found && found.offsetWidth > 0 ? wide(found) + apart : 0; };
        const order = [['tight', saved('.fv-lan')], ['lean', saved('.fv-feedback-entry:not(.fv-guide-entry)')], ['least', saved('.fv-guide-entry')]];
        const steps = [['', need, (entries) => head + gap + entries <= room]];
        let left = need, tokens = [];
        for (const [token, less] of order.slice(0, 1)) { left -= less; tokens.push(token); steps.push([tokens.join(' '), left, entries => head + gap + entries <= room]); }
        // On two rows the first row may itself wrap (the wordmark above the switch), so each of its parts must fit.
        const widest = first.reduce((most, child) => Math.max(most, wide(child)), 0);
        const both = entries => widest <= room && entries <= room;
        left = need; tokens = ['wrap']; steps.push(['wrap', left, both]);
        for (const [token, less] of order) { left -= less; tokens.push(token); steps.push([tokens.join(' '), left, both]); }
        const found = steps.find(([, entries, fits]) => fits(entries));
        const [fit, entries] = found || steps[steps.length - 1];
        if (!fit) return;
        bar.dataset.fit = fit;
        // A fixed bar centred at 50% shrinks to half the window once its row may wrap; give it the width it needs.
        if (fit.startsWith('wrap')) bar.style.width = `${Math.ceil(Math.min(room, Math.max(head <= room ? head : widest, entries)) + edges + 1)}px`;
    }
    const observer = new ResizeObserver(check);
    for (const element of [bar, row, actions]) observer.observe(element);
    addEventListener('resize', check);
    return {check, dispose: () => { observer.disconnect(); removeEventListener('resize', check); }};
}
