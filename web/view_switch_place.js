// One place for the view switch, shared by every view's top bar and the node view's bar: always on the window's
// centre (the bar's outer columns are equal: minmax(0,1fr) auto minmax(0,1fr), see shell.css), never moved by what a
// view puts beside it. When the entries on the right do not fit in their half, they go into one "More" menu button
// (shell's overflow), so the logo and the tabs never overlap and no entry is lost. When even the logo does not fit
// beside the tabs (or the window is 480 px or narrower) the tabs move to a second row of their own, centred, and the
// first row holds the logo and the entries (or their menu button).
// Returns the geometry the views' own fits read: {wrap, inner, gap, left, right} (left === right on one row).
const LOGO_ROOM = 96;
const query = new URLSearchParams(globalThis.location?.search || '');
const language = query.get('freevideo_lang');
const cn = language === 'zh' || (language !== 'en' && String(globalThis.navigator?.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;

function actionsOf(header) { return header.querySelector(':scope > :is(.fv-header-actions,.fv-view-actions)'); }

// The overflow menu of one bar: a button that stays in the entries' place, and a popover holding the entries.
function overflow(header) {
    if (header.fvOverflow) return header.fvOverflow;
    const button = document.createElement('button'); button.type = 'button'; button.className = 'fv-quiet fv-header-more';
    button.setAttribute('aria-haspopup', 'menu'); button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-label', t('More', '更多')); button.dataset.fvTipName = t('More', '更多');
    button.dataset.fvTipUse = t('Phone, version, tutorial, feedback and settings', '手机、版本、教程、反馈和设置');
    const glyph = document.createElement('span'); glyph.className = 'fv-header-more-glyph'; glyph.setAttribute('aria-hidden', 'true'); button.append(glyph);
    const menu = document.createElement('div'); menu.className = 'fv-header-menu'; menu.setAttribute('role', 'menu'); menu.dataset.fvTipScope = '';
    if (menu.showPopover) menu.popover = 'auto'; else menu.hidden = true;
    const place = () => {
        const r = button.getBoundingClientRect(), w = menu.offsetWidth;
        menu.style.top = `${Math.round(r.bottom + 8)}px`;
        menu.style.left = `${Math.round(Math.max(8, Math.min(r.right - w, innerWidth - 8 - w)))}px`;
    };
    menu.addEventListener('toggle', event => { button.setAttribute('aria-expanded', String(event.newState === 'open')); if (event.newState === 'open') place(); });
    button.onclick = () => {
        if (menu.showPopover) { try { menu.togglePopover(); } catch { /* Not connected yet. */ } }
        else { menu.hidden = !menu.hidden; button.setAttribute('aria-expanded', String(!menu.hidden)); place(); }
    };
    header.append(menu);
    return header.fvOverflow = {button, menu, moved: []};
}

function expand(header, actions) {
    const box = header.fvOverflow;
    if (!box || !box.moved.length) return;
    for (const child of box.moved) actions.insertBefore(child, box.button);
    box.moved = []; box.button.remove();
    try { if (box.menu.matches(':popover-open')) box.menu.hidePopover(); } catch { /* Closed. */ }
    delete header.dataset.overflow;
}

function collapse(header, actions) {
    const box = overflow(header);
    box.moved = [...actions.children].filter(child => child !== box.button);
    box.menu.replaceChildren(...box.moved);
    actions.append(box.button);
    header.dataset.overflow = 'true';
}

const widthOf = element => element ? Math.max(element.getBoundingClientRect().width, element.scrollWidth) : 0;

export function placeViewSwitch(header, navigation) {
    const px = value => parseFloat(value) || 0;
    // A bar taken over by topbar.js folds by its own measure (data-fv-fit); here it only reports its geometry, and
    // nothing is moved, so a view's own fit (studio_header_fit.js, editor_header_fit.js) never changes the shared bar.
    if (header.classList.contains('fv-topbar')) {
        const box = getComputedStyle(header), gap = px(box.columnGap);
        const inner = header.clientWidth - px(box.paddingLeft) - px(box.paddingRight);
        const side = Math.max(0, (inner - navigation.offsetWidth - 2 * gap) / 2);
        return {wrap: false, inner, gap, left: side, right: side};
    }
    const actions = actionsOf(header);
    if (actions) expand(header, actions);
    header.style.removeProperty('grid-template-columns');
    delete header.dataset.place;
    const box = getComputedStyle(header), gap = px(box.columnGap);
    const inner = header.clientWidth - px(box.paddingLeft) - px(box.paddingRight);
    const side = (inner - navigation.offsetWidth - 2 * gap) / 2;
    const wrap = matchMedia('(max-width:480px)').matches || side < LOGO_ROOM;
    header.dataset.place = wrap ? 'wrap' : 'row';
    if (actions) {
        const shown = [...actions.children].filter(child => child.offsetWidth > 0);
        const apart = px(getComputedStyle(actions).columnGap);
        const need = shown.reduce((sum, child) => sum + widthOf(child), 0) + apart * Math.max(0, shown.length - 1);
        const brand = header.querySelector(':scope > :is(.fv-brand,.fv-wordmark)');
        const room = wrap ? inner - widthOf(brand) - gap : side;
        if (need > room + 0.5) collapse(header, actions);
    }
    return wrap ? {wrap: true, inner, gap, left: inner, right: inner} : {wrap: false, inner, gap, left: side, right: side};
}

// Keep the place current as the window, the tabs or the entries change.
export function keepViewSwitchPlace(header, navigation) {
    if (typeof ResizeObserver !== 'function' || typeof getComputedStyle !== 'function' || typeof matchMedia !== 'function') return {check() {}, dispose() {}};
    let busy = false;
    const check = () => {
        if (busy || !header.isConnected || !header.clientWidth) return;
        busy = true; try { placeViewSwitch(header, navigation); } finally { busy = false; }
    };
    // In the observer's own turn (after layout, before paint), so a changed place never shows for a frame.
    const observer = new ResizeObserver(check);
    observer.observe(header); observer.observe(navigation);
    return {check, dispose: () => observer.disconnect()};
}
