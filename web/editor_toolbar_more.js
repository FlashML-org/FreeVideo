// At phone widths the track toolbar keeps one row; the tools outside `keep` move behind ⋯ and come back on a wider window.
// On a wider touch screen the toolbar keeps `touchKeep` (plus `touchWideKeep` from 1180 px) at finger size and the rest go behind ⋯.
const NARROW = '(max-width:480px)', TOUCH = '(pointer:coarse) and (min-width:481px)', TOUCH_WIDE = '(pointer:coarse) and (min-width:1180px)';

export function createToolbarMore({toolbar, t, keep, touchKeep = null, touchWideKeep = null}) {
    const more = document.createElement('button'); more.type = 'button'; more.className = 'fv-ed-more fv-ed-icon';
    more.setAttribute('aria-label', t('More tools', '更多工具')); more.title = more.getAttribute('aria-label');
    more.setAttribute('aria-expanded', 'false'); more.hidden = true;
    const dots = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); dots.setAttribute('viewBox', '0 0 16 16'); dots.setAttribute('aria-hidden', 'true');
    for (const x of [3, 8, 13]) { const dot = document.createElementNS(dots.namespaceURI, 'circle'); dot.setAttribute('cx', x); dot.setAttribute('cy', 8); dot.setAttribute('r', 1.3); dots.append(dot); }
    more.append(dots);
    const panel = document.createElement('div'); panel.className = 'fv-ed-more-panel'; panel.setAttribute('role', 'group'); panel.setAttribute('aria-label', more.title); panel.hidden = true;
    toolbar.append(more, panel);
    // Without matchMedia (a bare DOM) the toolbar keeps its wide layout.
    const query = value => typeof window.matchMedia === 'function' ? window.matchMedia(value) : null;
    const media = query(NARROW), touch = touchKeep ? query(TOUCH) : null, touchWide = touchKeep && touchWideKeep ? query(TOUCH_WIDE) : null, moved = [];
    let movedKeep = '';
    // The keep list in force now; empty when every tool stays on the toolbar.
    const current = () => media?.matches ? keep : touch?.matches ? (touchWide?.matches ? `${touchKeep}, ${touchWideKeep}` : touchKeep) : '';
    // On a touch screen a nested group that holds a kept tool opens up too, so its kept tool stays while its neighbours move
    // (phones move nested groups whole, as before).
    const spread = (node, wanted) => node.classList.contains('fv-ed-tool-group') && (node.parentNode === toolbar || (wanted !== keep && node.querySelector(wanted))) ? [...node.children].flatMap(child => spread(child, wanted)) : [node];
    const items = wanted => [...toolbar.children].flatMap(node => spread(node, wanted))
        .filter(node => node !== more && node !== panel && !node.matches(wanted));
    // Each row names its tool; labels are read on opening because tools rename themselves (shortcut hints dropped).
    function open(value) {
        if (value) for (const control of panel.querySelectorAll('button')) { control.dataset.moreLabel = (control.getAttribute('aria-label') || control.textContent).replace(/\s*[（(][^（）()]*[）)]$/, ''); control.toggleAttribute('data-more-icon', !!control.querySelector('svg')); }
        panel.hidden = !value; more.setAttribute('aria-expanded', String(value));
        // Open downward when the rows fit below the toolbar, otherwise upward over the preview.
        if (value) { const room = innerHeight - toolbar.getBoundingClientRect().bottom; panel.dataset.up = String(panel.offsetHeight > room - 8 && toolbar.getBoundingClientRect().top > room); }
        if (value) panel.querySelector('button:not(:disabled):not([hidden])')?.focus();
    }
    function apply() {
        open(false);
        const wanted = current();
        if (moved.length && wanted !== movedKeep) {
            for (const [node, place] of moved.splice(0).reverse()) {
                place.replaceWith(node);
                for (const control of node.matches('button') ? [node] : node.querySelectorAll('button')) { delete control.dataset.moreLabel; control.removeAttribute('data-more-icon'); }
            }
        }
        if (wanted && !moved.length) {
            for (const node of items(wanted)) {
                const place = document.createComment('fv-ed-more'); node.before(place); moved.push([node, place]); panel.append(node);
            }
            movedKeep = wanted;
        }
        more.hidden = !wanted;
    }
    more.onclick = () => open(panel.hidden);
    // A chosen tool acts first, then the menu closes; switches keep their pressed look for the next opening.
    panel.addEventListener('click', event => { if (event.target.closest('button')) open(false); });
    // The editor routes keys itself: Escape arrives through dismiss(), Up and Down through onkeydown.
    panel.onkeydown = event => {
        if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
        const controls = [...panel.querySelectorAll('button')].filter(node => !node.disabled && node.offsetParent);
        if (!controls.length) return;
        event.preventDefault(); event.stopPropagation();
        const at = controls.indexOf(document.activeElement), step = event.key === 'ArrowDown' ? 1 : -1;
        controls[at < 0 ? 0 : (at + step + controls.length) % controls.length].focus();
    };
    const outside = event => { if (!panel.hidden && !panel.contains(event.target) && !more.contains(event.target)) open(false); };
    document.addEventListener('pointerdown', outside, true);
    const watched = [media, touch, touchWide].filter(Boolean);
    for (const list of watched) list.addEventListener('change', apply);
    apply();
    return {
        refresh: apply,
        dismiss(event) {
            if (panel.hidden) return false;
            event.preventDefault(); event.stopPropagation(); open(false); more.focus(); return true;
        },
        dispose() { for (const list of watched) list.removeEventListener('change', apply); document.removeEventListener('pointerdown', outside, true); }
    };
}
