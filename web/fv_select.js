// The one dropdown of every page: the face of the Resolution select (raised ground, 1 px border, 8 px corners, a drawn chevron).
// skinSelect puts the face on a native select, which stays the source of truth; menuSelect also opens a menu in the
// editor's context-menu style, with group headings and a tick on the chosen item, in place of the system list.
const el = (tag, cls, label) => { const e = document.createElement(tag); if (cls) e.className = cls; if (label) e.textContent = label; return e; };

export function skinSelect(select) {
    const wrap = el('div', 'fv-select');
    wrap.append(select);
    return wrap;
}

const CHECK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>';

// `groups` maps an option's data-group to the heading above its run; an option without a group stands alone below a line.
export function menuSelect(select, {groups = {}} = {}) {
    const wrap = skinSelect(select);
    wrap.classList.add('fv-select-menu-host');
    select.classList.add('fv-select-native'); select.tabIndex = -1; select.setAttribute('aria-hidden', 'true');
    const trigger = el('button', 'fv-select-trigger'); trigger.type = 'button';
    trigger.setAttribute('aria-haspopup', 'listbox'); trigger.setAttribute('aria-expanded', 'false');
    const face = el('span', 'fv-select-face'); trigger.append(face);
    const menu = el('div', 'fv-select-menu'); menu.hidden = true; menu.setAttribute('role', 'listbox'); menu.tabIndex = -1;
    wrap.append(trigger, menu);
    let items = [];
    const refresh = () => {
        const label = select.getAttribute('aria-label');
        if (label) trigger.setAttribute('aria-label', label);
        face.textContent = select.selectedOptions[0]?.textContent || '';
        trigger.disabled = select.disabled;
    };
    const close = (restore = false) => {
        if (menu.hidden) return;
        menu.hidden = true; trigger.setAttribute('aria-expanded', 'false'); menu.replaceChildren(); items = [];
        document.removeEventListener('pointerdown', outside, true);
        window.removeEventListener('resize', closeNow); window.removeEventListener('blur', closeNow);
        if (restore) trigger.focus({preventScroll: true});
    };
    const closeNow = () => close();
    function outside(event) { if (!wrap.contains(event.target)) close(); }
    function choose(value) {
        close(true);
        if (select.value === value) return;
        select.value = value; refresh();
        select.dispatchEvent(new Event('input', {bubbles: true})); select.dispatchEvent(new Event('change', {bubbles: true}));
    }
    function open() {
        if (select.disabled || !menu.hidden) return;
        let previous = null, started = false;
        for (const child of select.children) {
            if (child.tagName === 'HR') { if (started) menu.append(Object.assign(el('div'), {role: 'separator'})); previous = null; continue; }
            if (child.hidden || child.disabled) continue;
            const group = child.dataset.group || '';
            if (started && group !== (previous ?? group)) menu.append(Object.assign(el('div'), {role: 'separator'}));
            if (group && group !== previous && groups[group]) menu.append(el('div', 'fv-select-group', groups[group]));
            previous = group; started = true;
            const item = el('button', 'fv-select-item'); item.type = 'button'; item.tabIndex = -1; item.setAttribute('role', 'option');
            const mark = el('span', 'fv-select-mark'); mark.innerHTML = CHECK;
            const chosen = child.value === select.value; item.setAttribute('aria-selected', String(chosen));
            item.append(el('span', 'fv-select-label', child.textContent), mark);
            item.onclick = () => choose(child.value);
            items.push(item); menu.append(item);
        }
        // Leave a separator only between two things.
        for (const line of [...menu.querySelectorAll('[role=separator]')]) if (!line.nextElementSibling || line.nextElementSibling.matches('[role=separator]')) line.remove();
        menu.hidden = false; trigger.setAttribute('aria-expanded', 'true');
        const box = trigger.getBoundingClientRect(), height = menu.offsetHeight, room = window.innerHeight - box.bottom - 8;
        menu.style.minWidth = `${Math.round(box.width)}px`;
        menu.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - menu.offsetWidth - 8))}px`;
        menu.style.top = `${Math.max(8, height <= room || room >= box.top - 8 ? box.bottom + 4 : box.top - height - 4)}px`;
        menu.style.maxHeight = `${Math.max(120, Math.max(room, box.top - 8))}px`;
        document.addEventListener('pointerdown', outside, true);
        window.addEventListener('resize', closeNow); window.addEventListener('blur', closeNow);
        (items.find(item => item.getAttribute('aria-selected') === 'true') || items[0])?.focus({preventScroll: true});
    }
    trigger.onclick = () => menu.hidden ? open() : close(true);
    const keys = event => {
        const move = {ArrowDown: 1, ArrowUp: -1}[event.key];
        if (menu.hidden) {
            if (move) { event.preventDefault(); event.stopPropagation(); open(); }
            return;
        }
        event.stopPropagation();
        if (event.key === 'Escape') { event.preventDefault(); close(true); }
        else if (event.key === 'Tab') close();
        else if (move || event.key === 'Home' || event.key === 'End') {
            event.preventDefault();
            const at = items.indexOf(document.activeElement);
            items[event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (at + move + items.length) % items.length]?.focus({preventScroll: true});
        }
    };
    wrap.addEventListener('keydown', keys);
    select.addEventListener('change', refresh);
    refresh();
    return {wrap, trigger, refresh, close};
}
