// One place for the view switch, shared by every view whose header holds it (creation panel, images, editor, autopilot).
// The place depends only on the header's width and the switch's own width, never on what a view puts beside it, so
// changing views never moves the switch. It stays centred while the right side keeps room for the widest set of
// entries any view shows there (the editor's export button, phone, Tutorial, Feedback and Settings). Otherwise it moves
// left so far that the editor keeps its full set, but never past the room the editor needs for its small logo beside a
// readable project name: the logo stays in every view, and entries on the right give way first (each view's own order).
// When even the least set of entries no longer fits beside that room, the switch moves to a second row, where every
// view keeps its full set of entries. Each view then fits its own logo and entries into the two sides it is given.
// Widths in CSS pixels per interface language: [the editor's full set, least on one row].
const RESERVE = {zh: [272, 181], en: [387, 235]};
// The editor's small logo (90), its spacing (10), a readable name (120) and the name's frame and menu arrow (54).
const LOGO_LEFT = 274;

export function placeViewSwitch(header, navigation) {
    const px = value => parseFloat(value) || 0;
    header.style.removeProperty('grid-template-columns');
    delete header.dataset.place;
    const box = getComputedStyle(header), gap = px(box.columnGap);
    const inner = header.clientWidth - px(box.paddingLeft) - px(box.paddingRight);
    const [widest, least] = RESERVE[(header.closest('[lang]')?.lang || '').startsWith('zh') ? 'zh' : 'en'];
    const free = inner - navigation.offsetWidth - 2 * gap;
    if (matchMedia('(max-width:480px)').matches || free < LOGO_LEFT + least) {
        header.dataset.place = 'wrap';
        return {wrap: true, inner, gap};
    }
    const left = free / 2 >= widest ? free / 2 : Math.max(LOGO_LEFT, free - widest);
    header.style.gridTemplateColumns = `${left}px auto minmax(0,1fr)`;
    header.dataset.place = 'row';
    return {wrap: false, inner, gap, left, right: free - left};
}

// For a header without entries that give way (images, autopilot): keep the place current as the window changes.
export function keepViewSwitchPlace(header, navigation) {
    if (typeof ResizeObserver !== 'function' || typeof getComputedStyle !== 'function' || typeof matchMedia !== 'function') return {check() {}, dispose() {}};
    const check = () => { if (header.isConnected && header.clientWidth) placeViewSwitch(header, navigation); };
    const observer = new ResizeObserver(check);
    observer.observe(header); observer.observe(navigation);
    return {check, dispose: () => observer.disconnect()};
}
