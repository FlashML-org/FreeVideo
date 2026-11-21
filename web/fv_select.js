// The one dropdown of every page: the face of the Resolution select (raised ground, 1 px border, 8 px corners, a drawn chevron).
// skinSelect puts the face on a native select, which stays the source of truth; menuSelect also opens a menu in the
// editor's context-menu style, with group headings and a tick on the chosen item, in place of the system list.
// `groups` is a map (or a function) from an option's data-group to the heading above its run. `search` ({placeholder, empty}) puts a
// filter box above a long list (more than SEARCH_FROM options): typing narrows the items, Enter takes the first, Down enters the list.
import { listenDialogKey } from './dialog_keys.js';
const SEARCH_FROM = 12;
const MAX_HEIGHT = 360;  // about ten rows: a long list scrolls instead of reaching the top of the window
const el = (tag, cls, label) => { const e = document.createElement(tag); if (cls) e.className = cls; if (label) e.textContent = label; return e; };

const shell = select => { const wrap = el('div', 'fv-select'); wrap.append(select); return wrap; };
const controlled = new WeakSet();

// Puts the face on a native select and opens our own menu on it instead of the system list; the select itself stays the visible face.
export function skinSelect(select) {
    return menuSelect(select, {overlay: true}).wrap;
}

const CHECK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>';

// `groups` maps an option's data-group to the heading above its run; an option without a group stands alone below a line.
// overlay: the native select stays visible and is the trigger; bare: and it is not wrapped either (a select the page made by itself).
export function menuSelect(select, {groups = {}, search = null, overlay = false, bare = false} = {}) {
    controlled.add(select);
    overlay = overlay || bare;
    const wrap = bare ? null : shell(select);
    const menu = el('div', 'fv-select-menu'); menu.hidden = true; menu.setAttribute('role', 'listbox'); menu.tabIndex = -1;
    let trigger = select, face = null;
    if (overlay) { select.setAttribute('aria-haspopup', 'listbox'); }
    else {
        wrap.classList.add('fv-select-menu-host');
        select.classList.add('fv-select-native'); select.tabIndex = -1; select.setAttribute('aria-hidden', 'true');
        trigger = el('button', 'fv-select-trigger'); trigger.type = 'button';
        trigger.setAttribute('aria-haspopup', 'listbox');
        face = el('span', 'fv-select-face'); trigger.append(face);
    }
    trigger.setAttribute('aria-expanded', 'false');
    if (wrap && !overlay) wrap.append(trigger, menu);
    let items = [], box = null, watch = 0;
    const refresh = () => {
        if (overlay) return;
        const label = select.getAttribute('aria-label');
        if (label) trigger.setAttribute('aria-label', label);
        const chosen = select.selectedOptions[0]; face.textContent = chosen?.dataset.label || chosen?.textContent || '';
        trigger.disabled = select.disabled;
    };
    const close = (restore = false) => {
        if (menu.hidden) return;
        menu.hidden = true; trigger.setAttribute('aria-expanded', 'false'); menu.replaceChildren(); items = [];
        if (wrap && !overlay) { if (menu.parentNode !== wrap) wrap.append(menu); } else menu.remove();
        clearInterval(watch);
        document.removeEventListener('pointerdown', outside, true);
        window.removeEventListener('resize', closeNow); window.removeEventListener('blur', closeNow);
        if (restore) trigger.focus({preventScroll: true});
    };
    const closeNow = () => close();
    function outside(event) { if (!(wrap || select).contains(event.target) && !menu.contains(event.target)) close(); }
    function choose(value) {
        close(true);
        if (select.value === value) return;
        select.value = value; refresh();
        select.dispatchEvent(new Event('input', {bubbles: true})); select.dispatchEvent(new Event('change', {bubbles: true}));
    }
    // The options still shown for a query, with their group headings and separators.
    function fill(query = '') {
        for (const child of [...menu.children]) if (child !== box?.parentNode) child.remove();
        items = [];
        const needle = query.trim().toLocaleLowerCase();
        let previous = null, started = false, shown = 0;
        for (const child of select.children) {
            if (child.tagName === 'HR') { if (started && !needle) menu.append(Object.assign(el('div'), {role: 'separator'})); previous = null; continue; }
            if (child.hidden || child.disabled) continue;
            if (needle && !child.textContent.toLocaleLowerCase().includes(needle)) continue;  // matches the whole name, not just the shown label
            const group = child.dataset.group || '';
            if (started && group !== (previous ?? group)) menu.append(Object.assign(el('div'), {role: 'separator'}));
            const heading = group && group !== previous ? (typeof groups === 'function' ? groups(group) : groups[group]) : '';
            if (heading) menu.append(el('div', 'fv-select-group', heading));
            previous = group; started = true; shown++;
            const item = el('button', 'fv-select-item'); item.type = 'button'; item.tabIndex = -1; item.setAttribute('role', 'option');
            const mark = el('span', 'fv-select-mark'); mark.innerHTML = CHECK;
            const chosen = child.value === select.value; item.setAttribute('aria-selected', String(chosen));
            item.append(el('span', 'fv-select-label', child.dataset.label || child.textContent), mark);
            item.onclick = () => choose(child.value);
            items.push(item); menu.append(item);
        }
        // Leave a separator only between two things.
        for (const line of [...menu.querySelectorAll('[role=separator]')]) if (!line.nextElementSibling || line.nextElementSibling.matches('[role=separator]')) line.remove();
        if (!shown && search?.empty) menu.append(el('div', 'fv-select-empty', search.empty));
    }
    // Under the trigger, or above it when there is no room; a fixed menu inside a transformed or filtered ancestor is moved back to the wanted spot.
    function place() {
        const rect = trigger.getBoundingClientRect(), room = window.innerHeight - rect.bottom - 8;
        menu.style.minWidth = `${Math.round(rect.width)}px`;
        menu.style.maxHeight = `${Math.min(MAX_HEIGHT, Math.max(120, Math.max(room, rect.top - 8)))}px`;
        const height = menu.offsetHeight, above = height > room && room < rect.top - 8;
        const wantLeft = Math.max(8, Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8));
        const wantTop = Math.max(8, above ? rect.top - height - 4 : rect.bottom + 4);
        menu.style.left = `${wantLeft}px`; menu.style.top = `${wantTop}px`;
        const at = menu.getBoundingClientRect();
        if (Math.abs(at.left - wantLeft) > 1 || Math.abs(at.top - wantTop) > 1) {
            menu.style.left = `${wantLeft - (at.left - wantLeft)}px`; menu.style.top = `${wantTop - (at.top - wantTop)}px`;
        }
    }
    function open() {
        if (select.disabled || !menu.hidden) return;
        box = null; menu.replaceChildren();
        // A tray with a glass blur or its own overflow would clip a fixed menu; inside an open dialog it is shown from the dialog itself.
        const dialog = select.closest('dialog[open]');
        if (dialog) { if (menu.parentNode !== dialog) dialog.append(menu); }
        else if (!menu.isConnected || overlay) (wrap && !overlay ? wrap : select.parentNode).append(menu);
        if (search && select.querySelectorAll('option').length > SEARCH_FROM) {
            const holder = el('div', 'fv-select-search'); box = el('input'); box.type = 'text'; box.autocomplete = 'off'; box.spellcheck = false;
            box.placeholder = search.placeholder || ''; box.setAttribute('aria-label', search.placeholder || ''); holder.append(box); menu.append(holder);
            box.oninput = () => { fill(box.value); menu.scrollTop = 0; place(); };
        }
        fill();
        menu.hidden = false; trigger.setAttribute('aria-expanded', 'true');
        place();
        // A list that is drawn again while its menu is open (the row is replaced) would leave a menu that chooses nothing.
        watch = setInterval(() => { if (!select.isConnected || select.disabled) close(); }, 200);
        document.addEventListener('pointerdown', outside, true);
        window.addEventListener('resize', closeNow); window.addEventListener('blur', closeNow);
        const chosen = items.find(item => item.getAttribute('aria-selected') === 'true') || items[0];
        chosen?.focus({preventScroll: true});
        chosen?.scrollIntoView?.({block: 'center'});
        if (box && !chosen) box.focus({preventScroll: true});
    }
    if (overlay) trigger.addEventListener('mousedown', event => { if (event.button) return; event.preventDefault(); if (select.disabled) return; trigger.focus({preventScroll: true}); menu.hidden ? open() : close(true); });
    else trigger.onclick = () => menu.hidden ? open() : close(true);
    const keys = event => {
        const move = {ArrowDown: 1, ArrowUp: -1}[event.key];
        if (menu.hidden) {
            if (move || (overlay && (event.key === ' ' || event.key === 'Enter') && !event.ctrlKey && !event.metaKey)) { event.preventDefault(); event.stopPropagation(); open(); }
            return;
        }
        event.stopPropagation();
        if (event.key === 'Escape') { event.preventDefault(); close(true); }
        else if (event.key === 'Tab') close();
        else if (box && event.key === 'Enter' && event.target === box) { event.preventDefault(); items[0]?.click(); }
        else if (box && event.target !== box && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) box.focus({preventScroll: true});
        else if (move || event.key === 'Home' || event.key === 'End') {
            if (event.target === box && event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
            event.preventDefault();
            const ring = box ? [box, ...items] : items, at = ring.indexOf(document.activeElement);
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? ring.length - 1 : (at + move + ring.length) % ring.length;
            ring[next]?.focus({preventScroll: true}); ring[next]?.scrollIntoView?.({block: 'nearest'});
        }
    };
    // A modal dialog takes every key at the window and hands it only to listeners registered this way.
    listenDialogKey(wrap || select, 'keydown', keys); listenDialogKey(menu, 'keydown', keys);
    select.addEventListener('change', refresh);
    // A row that is switched on or off sets select.disabled in place; the face follows.
    if (!overlay) new MutationObserver(() => { refresh(); if (select.disabled) close(); }).observe(select, {attributes: true, attributeFilter: ['disabled']});
    refresh();
    return {wrap, trigger, refresh, close, open};
}

// A select that a FreeVideo page made without skinSelect gets the same menu when it is first reached. Only inside FreeVideo's own
// containers (where the theme variables exist, so the menu is opaque): the selects of ComfyUI, of other extensions and of the
// settings dialog (no theme variables there) stay as they are.
const OWN = '.fv-studio, .fv-setup, .fv-panel, .fv-image-editor, .fv-create';
const reach = event => {
    const select = event.target?.closest?.('select');
    if (!select || select.multiple || select.size > 1 || controlled.has(select) || select.classList.contains('fv-select-native') || !select.closest(OWN)) return;
    menuSelect(select, {bare: true});  // its own mousedown, added now, opens the menu in this same event
};
if (typeof document !== 'undefined' && document.addEventListener && !globalThis.__fvSelectMenus) {
    globalThis.__fvSelectMenus = true;
    document.addEventListener('mousedown', reach, true); document.addEventListener('focusin', reach, true);
}
