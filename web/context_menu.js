// The one right-click menu of every FreeVideo view, and the small pieces the command layer shares (language,
// icons, how a shortcut is written). The library, the image view and the editor build their menus with it:
//   attachContextMenu(element, event => items | null)   right click, Shift+F10, the menu key, a long press on touch
//   openContextMenu({x, y} | {anchor}, items)           from a button or code
// An item is {id, label: [en, zh], icon?, key?, danger?, disabled?: '' | [en, zh] (why), checked?, run, submenu?: [items]};
// '-' draws a separator. The menu floats in the top layer (it never moves the page), stays inside the window,
// flips at the edges and closes on a click outside, Esc, Tab, scrolling or a change of view.
// Nothing here runs at import under Node, so the unit tests load it as it is.

import { isolateDialogKeys, listenDialogKey } from './dialog_keys.js';
import { isChinese, translator } from './i18n.js';

const browser = typeof document !== 'undefined';
export const CN = isChinese();
export const t = translator(CN);
// [en, zh] pairs, plain strings and functions returning either all read the same way.
export const words = (value, cn = CN) => typeof value === 'function' ? words(value(), cn) : Array.isArray(value) ? String(value[cn ? 1 : 0] ?? value[0] ?? '') : String(value ?? '');
export const MAC = /Mac|iPhone|iPad/.test(globalThis.navigator?.platform || globalThis.navigator?.userAgent || '');
// The compatibility view (Settings → 兼容界面, stored as freevideo.glass = solid): solid panels, fewer animations,
// and nothing plays until asked. It counts as reduced motion everywhere.
export const compatView = () => globalThis.document?.documentElement?.dataset.glass === 'solid' || globalThis.document?.documentElement?.classList.contains('lite') === true;
export const reducedMotion = () => compatView() || globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;
export const EASE = 'cubic-bezier(.2,.8,.2,1)';

export function addSheet(name, base) {
    if (!browser || document.querySelector(`link[data-fv-sheet="${name}"]`)) return;
    const link = document.createElement('link'); link.rel = 'stylesheet'; link.dataset.fvSheet = name;
    link.href = new URL(`./${name}`, base).href; document.head.append(link);
}
addSheet('context_menu.css', import.meta.url);

// ── How a shortcut is written: 'Mod+Shift+K' → ⌘⇧K on a Mac, Ctrl+Shift+K elsewhere. ──
const MAC_KEYS = {mod: '⌘', ctrl: '⌃', shift: '⇧', alt: '⌥', enter: '↵', backspace: '⌫', delete: '⌦', escape: 'Esc', esc: 'Esc', tab: '⇥'};
const PC_KEYS = {mod: 'Ctrl', ctrl: 'Ctrl', shift: 'Shift', alt: 'Alt', enter: 'Enter', backspace: 'Backspace', delete: 'Del', escape: 'Esc', esc: 'Esc', tab: 'Tab'};
const COMMON = {arrowleft: '←', arrowright: '→', arrowup: '↑', arrowdown: '↓', left: '←', right: '→', up: '↑', down: '↓', space: 'Space', ' ': 'Space', pageup: 'PgUp', pagedown: 'PgDn', home: 'Home', end: 'End',
    plus: '+', minus: '−', equal: '=', slash: '/', bracketleft: '[', bracketright: ']', contextmenu: 'Menu'};
// Words that are not keys: written in the language shown.
const GESTURES = {click: ['Click', '点击'], drag: ['Drag', '拖动'], wheel: ['Wheel', '滚轮'], hold: ['Hold', '按住']};
// One combination as its keys, in order: ['⌘', 'K'] or ['Ctrl', 'K']. Several combinations are separated by ' / ';
// a key that is itself + or / is written Plus or Slash.
export function keyParts(combo, mac = MAC, cn = CN) {
    return String(combo || '').split('+').map(part => part.trim()).filter(Boolean).map(part => {
        const low = part.toLowerCase();
        if (GESTURES[low]) return GESTURES[low][cn ? 1 : 0];
        return (mac ? MAC_KEYS : PC_KEYS)[low] ?? COMMON[low] ?? (part.length === 1 ? part.toUpperCase() : part);
    });
}
export function formatKeys(keys, mac = MAC) {
    return String(keys || '').split(/\s*\/\s*/).filter(Boolean).map(combo => keyParts(combo, mac).join(mac ? '' : '+')).join(' / ');
}
// The <kbd> chips for a shortcut; several combinations get a thin slash between them.
export function keyChips(keys, mac = MAC) {
    const box = document.createElement('span'); box.className = 'fv-keys';
    String(keys || '').split(/\s*\/\s*/).filter(Boolean).forEach((combo, index) => {
        if (index) { const or = document.createElement('span'); or.className = 'fv-keys-or'; or.textContent = '/'; box.append(or); }
        for (const part of keyParts(combo, mac)) { const kbd = document.createElement('kbd'); kbd.textContent = part; box.append(kbd); }
    });
    return box;
}

// ── Icons: one stroke set, 16 px on a 24 grid, drawn in currentColor. ──
const PATHS = {
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    spark: '<path d="M12 3.5 13.9 9l5.6 1.9-5.6 1.9L12 18.5l-1.9-5.7L4.5 11 10.1 9z"/>',
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="3"/><circle cx="9" cy="10" r="1.6"/><path d="m4 17 5-4.5 4 3.5 2.5-2 4.5 3.5"/>',
    film: '<rect x="3.5" y="5" width="17" height="14" rx="3"/><path d="M8 5v14M16 5v14M3.5 9.5H8M3.5 14.5H8M16 9.5h4.5M16 14.5h4.5"/>',
    nodes: '<rect x="3.5" y="4" width="7" height="6" rx="2"/><rect x="13.5" y="14" width="7" height="6" rx="2"/><path d="M10.5 7h3a3 3 0 0 1 3 3v4"/>',
    scissors: '<circle cx="6.5" cy="7" r="2.5"/><circle cx="6.5" cy="17" r="2.5"/><path d="M8.6 8.4 19.5 18M8.6 15.6 19.5 6"/>',
    chat: '<path d="M5 18.5V7a2.5 2.5 0 0 1 2.5-2.5h9A2.5 2.5 0 0 1 19 7v6a2.5 2.5 0 0 1-2.5 2.5H9z"/>',
    grid: '<rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M12 3.5v2.2M12 18.3v2.2M20.5 12h-2.2M5.7 12H3.5M18 6l-1.6 1.6M7.6 16.4 6 18M18 18l-1.6-1.6M7.6 7.6 6 6"/>',
    book: '<path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H19v15H7.5A2.5 2.5 0 0 0 5 20.5z"/><path d="M5 20.5A2.5 2.5 0 0 1 7.5 18H19v3H7.5"/>',
    message: '<path d="M4.5 6.5A2.5 2.5 0 0 1 7 4h10a2.5 2.5 0 0 1 2.5 2.5v7A2.5 2.5 0 0 1 17 16h-5l-4.5 4v-4H7a2.5 2.5 0 0 1-2.5-2.5z"/>',
    sun: '<circle cx="12" cy="12" r="3.8"/><path d="M12 2.8v2M12 19.2v2M21.2 12h-2M4.8 12h-2M18.5 5.5l-1.4 1.4M6.9 17.1l-1.4 1.4M18.5 18.5l-1.4-1.4M6.9 6.9 5.5 5.5"/>',
    moon: '<path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z"/>',
    layers: '<path d="m12 4 8.5 4.5L12 13 3.5 8.5z"/><path d="m3.5 12.5 8.5 4.5 8.5-4.5"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.6 3.5 5.4 3.5 8.5s-1 5.9-3.5 8.5c-2.5-2.6-3.5-5.4-3.5-8.5s1-5.9 3.5-8.5z"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l.8 12.5h9.4L17.5 7"/>',
    bell: '<path d="M6.5 16V11a5.5 5.5 0 0 1 11 0v5l1.5 2h-14z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    keyboard: '<rect x="3" y="6" width="18" height="12" rx="3"/><path d="M7 10h.01M10.3 10h.01M13.7 10h.01M17 10h.01M7.5 14h9"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    close: '<path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>',
    alert: '<path d="M12 4 21 19.5H3z"/><path d="M12 10v4M12 16.8v.01"/>',
    info: '<circle cx="12" cy="12" r="6.5"/><path d="M12 11.3v4.2M12 8.6v.01"/>',
    download: '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14"/>',
    person: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
    play: '<path d="M8 5.5v13l10.5-6.5z"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
    arrow: '<path d="M5 12h14M13.5 6.5 19 12l-5.5 5.5"/>',
    chevron: '<path d="m9.5 6 6 6-6 6"/>',
    copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2.5"/><path d="M15.5 8.5V6A2.5 2.5 0 0 0 13 3.5H6A2.5 2.5 0 0 0 3.5 6v7A2.5 2.5 0 0 0 6 15.5h2.5"/>',
    star: '<path d="m12 4 2.4 5 5.4.7-4 3.8 1 5.4L12 16.3 7.2 18.9l1-5.4-4-3.8 5.4-.7z"/>',
    tag: '<path d="M4 4h7.5l8.5 8.5-7.5 7.5L4 11.5z"/><circle cx="8.5" cy="8.5" r="1.3"/>',
    folder: '<path d="M3.5 7A2.5 2.5 0 0 1 6 4.5h3.5l2 2.5H18A2.5 2.5 0 0 1 20.5 9.5V17a2.5 2.5 0 0 1-2.5 2.5H6A2.5 2.5 0 0 1 3.5 17z"/>',
    compare: '<rect x="3.5" y="5" width="17" height="14" rx="3"/><path d="M12 3v18"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    undo: '<path d="M9 7 4.5 11.5 9 16"/><path d="M5 11.5h9.5a5 5 0 0 1 0 10H12"/>',
    pulse: '<path d="M3.5 12h4l2.5-6 4 12 2.5-6h4"/>',
    reload: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>',
    history: '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.5 4.5v4h4"/><path d="M12 8v4.2l2.6 1.6"/>',
    paste: '<rect x="5.5" y="5" width="13" height="15.5" rx="2.5"/><path d="M9 5V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V5"/>',
    enlarge: '<path d="M14 4.5h5.5V10M19.5 4.5 13 11M10 19.5H4.5V14M4.5 19.5 11 13"/>',
    upscale: '<circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 4.5 4.5M10.5 8v5M8 10.5h5"/>',
    'select-all': '<rect x="4" y="4" width="16" height="16" rx="3" stroke-dasharray="2.6 2.6"/>',
    external: '<path d="M13.5 4.5h6v6M19.5 4.5 11 13"/><path d="M18 14v3.5A2.5 2.5 0 0 1 15.5 20h-9A2.5 2.5 0 0 1 4 17.5v-9A2.5 2.5 0 0 1 6.5 6H10"/>',
};
export const ICONS = Object.freeze(Object.keys(PATHS));
export function glyph(name, size = 16) {
    const body = PATHS[name] || PATHS.arrow;
    return `<svg class="fv-glyph" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
}

// Where something floating and clickable goes. A modal view makes the rest of the page inert, so a popover has to
// live inside the topmost open dialog (the shell names it); the command layer's own dialogs are skipped once closing.
export function floatingHost() {
    const own = globalThis.FreeVideoShell?.overlayHost?.();
    if (own && own.matches?.('dialog[open]') && own.dataset.closing !== 'true') return own;
    const open = [...document.querySelectorAll('dialog[open]')].filter(dialog => dialog.dataset.closing !== 'true' && !dialog.matches('.fv-tip,.fv-shell-cover'));
    return open.at(-1) || document.body;
}

// ── Menu items: pure helpers the tests drive. ──
const isSeparator = item => item === '-' || item?.separator === true;
export const enabledOf = item => !isSeparator(item) && !words(item?.disabled);
// Separators never lead, trail or double up; hidden items (when() false) go.
export function cleanItems(items) {
    const out = [];
    for (const item of items || []) {
        if (!item) continue;
        if (!isSeparator(item) && typeof item.when === 'function' && !item.when()) continue;
        if (isSeparator(item) && (!out.length || isSeparator(out.at(-1)))) continue;
        out.push(item);
    }
    while (out.length && isSeparator(out.at(-1))) out.pop();
    return out;
}
// The next enabled row from `from` going `step` (+1/-1), wrapping; -1 when none is enabled.
export function stepIndex(items, from, step) {
    const n = items.length;
    for (let i = 1; i <= n; i++) {
        const at = ((from + step * i) % n + n) % n;
        if (enabledOf(items[at])) return at;
    }
    return -1;
}
// Typing a letter moves to the next enabled item whose label starts with it (in the language shown).
export function typeahead(items, from, letter, cn = CN) {
    const want = String(letter || '').toLowerCase(); if (!want) return from;
    const n = items.length;
    for (let i = 1; i <= n; i++) {
        const at = ((from + i) % n + n) % n, item = items[at];
        if (enabledOf(item) && words(item.label, cn).trim().toLowerCase().startsWith(want)) return at;
    }
    return from;
}
// Where a menu of `size` opens for a point or an anchor box, inside a `view` with an 8 px margin.
export function placeMenu(at, size, view, margin = 8) {
    let left, top;
    if (at.anchor) {
        const a = at.anchor;
        left = at.align === 'end' ? a.right - size.width : a.left;
        top = a.bottom + 4;
        if (top + size.height > view.height - margin && a.top - 4 - size.height >= margin) top = a.top - 4 - size.height;
    } else if (at.beside) {
        // A submenu: to the right of its row, or to the left when the right has no room.
        const a = at.beside;
        left = a.right - 4; top = a.top - 6;
        if (left + size.width > view.width - margin) left = a.left - size.width + 4;
    } else {
        left = at.x; top = at.y;
        if (left + size.width > view.width - margin) left = at.x - size.width;
        if (top + size.height > view.height - margin) top = at.y - size.height;
    }
    left = Math.max(margin, Math.min(left, view.width - margin - size.width));
    top = Math.max(margin, Math.min(top, view.height - margin - size.height));
    return {left: Math.round(left), top: Math.round(top)};
}

// ── What the usual rows do, for their hover tips (name + use + key). A row's own `use` wins; ids not listed here
// show their name and key only. Unavailable rows show their `disabled` text, which says why and how to undo it. ──
const USE = {
    open: ['View it large', '打开大图查看'], download: ['Save the file to this computer', '把文件存到这台电脑'],
    reuse: ['Fill in its prompt and settings', '填入它的提示词和设置'], reference: ['Use it as a reference picture', '作为参考图放进当前视图'],
    'copy-prompt': ['Copy its prompt to paste elsewhere', '复制提示词，可粘贴到别处'], favorite: ['Keep it under Favorites', '加入收藏，方便再找'],
    tags: ['Group it with a tag', '用标签给它分组'], compare: ['Compare it with another one', '和另一个并排或滑块对比'],
    folder: ['Show the file in its folder', '在文件夹里找到这个文件'], trash: ['Move it to Recently deleted', '移到最近删除，可恢复'],
    restore: ['Put it back where it was', '放回原来的位置'], purge: ['Delete it for good; asks first', '永久删除，会先确认'],
    'new-tag': ['Make a new tag for it', '新建一个标签'], extend: ['Continue it into a longer video', '接着生成更长的视频'],
    rename: ['Give it another name', '换一个名字'], duplicate: ['Make a copy next to it', '在旁边复制一份'],
    read: ['Clear its unread dot', '去掉未读标记'], remove: ['Take it off this list', '从列表里移除'],
};
const ALIAS = {continue: 'extend', 'continue-from': 'extend', dl: 'download', ref: 'reference', copy: 'copy-prompt', fav: 'favorite', tag: 'tags', cmp: 'compare', del: 'trash', delete: 'trash', 'delete-forever': 'purge', newtag: 'new-tag'};
export const menuUse = item => item?.use || USE[item?.id] || USE[ALIAS[item?.id]] || null;

// ── The menu itself. ──
let current = null;   // {root, close}
let serial = 0;

function itemRow(item, index, menuId) {
    if (isSeparator(item)) { const line = document.createElement('div'); line.className = 'fv-cm-sep'; line.setAttribute('role', 'separator'); return line; }
    const row = document.createElement('button'); row.type = 'button'; row.className = 'fv-cm-item'; row.tabIndex = -1;
    row.id = `${menuId}-${index}`; row.dataset.index = String(index);
    const checkable = typeof item.checked === 'boolean';
    row.setAttribute('role', checkable ? 'menuitemcheckbox' : 'menuitem');
    if (checkable) row.setAttribute('aria-checked', String(item.checked));
    const why = words(item.disabled);
    if (why) { row.setAttribute('aria-disabled', 'true'); row.dataset.fvTipWhy = why; }
    if (item.danger) row.dataset.danger = 'true';
    const label = words(item.label);
    // Every row's tip: its name, what it does, its key (and, when unavailable, why and how to undo that).
    row.dataset.fvTipName = label;
    const use = menuUse(item);
    if (use) row.dataset.fvTipUse = words(use);
    if (item.key) row.dataset.fvTipKey = formatKeys(item.key);
    const mark = document.createElement('span'); mark.className = 'fv-cm-icon';
    mark.innerHTML = checkable ? (item.checked ? glyph('check') : '') : item.icon ? glyph(item.icon) : '';
    const text = document.createElement('span'); text.className = 'fv-cm-label'; text.textContent = label;
    row.append(mark, text);
    if (item.submenu) { row.setAttribute('aria-haspopup', 'menu'); row.setAttribute('aria-expanded', 'false'); const more = document.createElement('span'); more.className = 'fv-cm-more'; more.innerHTML = glyph('chevron'); row.append(more); }
    else if (item.key) { const key = document.createElement('span'); key.className = 'fv-cm-key'; key.textContent = formatKeys(item.key); row.append(key); }
    return row;
}

function animateIn(element, from = 'top') {
    if (reducedMotion() || !element.animate) return;
    element.animate([{opacity: 0, transform: `translateY(${from === 'top' ? -4 : 4}px) scale(.98)`}, {opacity: 1, transform: 'none'}], {duration: 160, easing: EASE});
}

// Opens a menu; returns {close, element}. One menu (with its submenu) is open at a time.
export function openContextMenu(at, rawItems, {label = '', returnFocus = null, onClose = null, parent = null} = {}) {
    if (!browser) return null;
    const items = cleanItems(rawItems);
    if (!items.length) return null;
    if (!parent) current?.close({focus: false});
    const menuId = `fv-cm-${++serial}`;
    const root = document.createElement('div');
    root.className = 'fv-cm fv-glass fv-g3'; root.id = menuId; root.setAttribute('role', 'menu');
    root.dataset.fvTipScope = ''; root.dataset.fvOwnTab = ''; root.tabIndex = -1; // Tab closes the menu (below); a11y.js stands aside
    if (label) root.setAttribute('aria-label', words(label));
    if (root.showPopover) root.popover = 'manual';
    const rows = items.map((item, index) => itemRow(item, index, menuId));
    root.append(...rows);
    const host = parent ? parent.root.parentNode : floatingHost();
    host.append(root);
    try { root.showPopover?.(); } catch { /* Shown as a fixed element. */ }
    const box = at.anchor instanceof Element ? at.anchor.getBoundingClientRect() : at.anchor;
    const position = () => {
        const size = root.getBoundingClientRect();
        const place = placeMenu({...at, anchor: box}, {width: size.width, height: size.height}, {width: innerWidth, height: innerHeight});
        root.style.left = `${place.left}px`; root.style.top = `${place.top}px`;
        return place;
    };
    const place = position();
    // Placed again on the next frame, in case its size changed after the first layout (a style sheet still arriving).
    requestAnimationFrame(position);
    animateIn(root, place.top < (box?.top ?? at.y ?? 0) ? 'bottom' : 'top');

    let active = -1, child = null, closed = false, typed = '', typedTimer = 0;
    const invoker = returnFocus || (parent ? null : document.activeElement);
    const rowOf = index => root.querySelector(`[data-index="${index}"]`);
    function focusRow(index) {
        active = index;
        for (const row of root.querySelectorAll('.fv-cm-item')) row.classList.toggle('is-active', Number(row.dataset.index) === index);
        const row = rowOf(index);
        if (row) { row.focus({preventScroll: true}); row.scrollIntoView?.({block: 'nearest'}); root.setAttribute('aria-activedescendant', row.id); }
    }
    function openChild(index, viaKey) {
        const item = items[index]; if (!item?.submenu || !enabledOf(item)) return;
        if (child?.index === index) { if (viaKey) child.handle.focusFirst(); return; }
        child?.handle.close({focus: false});
        const row = rowOf(index); row.setAttribute('aria-expanded', 'true');
        const handle = openContextMenu({beside: row.getBoundingClientRect()}, item.submenu, {parent: self, onClose: () => { row.setAttribute('aria-expanded', 'false'); if (child?.handle === handle) child = null; }});
        if (!handle) return;
        child = {index, handle};
        if (viaKey) handle.focusFirst();
    }
    function activate(index) {
        const item = items[index];
        if (!item || !enabledOf(item)) return;
        if (item.submenu) { openChild(index, true); return; }
        closeAll({focus: true});
        // The menu is gone and focus is back before the action runs, so a dialog it opens takes focus cleanly.
        queueMicrotask(() => { try { item.run?.(item); } catch (error) { console.warn('FreeVideo menu:', error); } });
    }
    function close({focus = true} = {}) {
        if (closed) return; closed = true;
        child?.handle.close({focus: false});
        document.removeEventListener('pointerdown', outside, true);
        removeEventListener('blur', blurClose); removeEventListener('resize', blurClose);
        document.removeEventListener('scroll', scrollClose, true); removeEventListener('freevideo-view', blurClose);
        const done = () => { try { root.hidePopover?.(); } catch { /* Already hidden. */ } root.remove(); };
        if (reducedMotion() || !root.animate) done();
        else { root.style.pointerEvents = 'none'; root.animate([{opacity: 1}, {opacity: 0}], {duration: 160, easing: EASE, fill: 'forwards'}).finished.then(done, done); }
        if (focus && invoker?.isConnected) invoker.focus?.({preventScroll: true});
        if (!parent && current?.root === root) current = null;
        onClose?.();
    }
    const closeAll = options => { let top = self; while (top.parent) top = top.parent; top.close(options); };
    const outside = event => { if (!(event.target instanceof Node)) return; let menu = self; while (menu.parent) menu = menu.parent; if (!menu.contains(event.target)) closeAll({focus: false}); };
    const blurClose = () => closeAll({focus: false});
    const scrollClose = event => { if (!root.contains(event.target)) closeAll({focus: false}); };

    // Through dialog_keys.js: a view that keeps its keys to itself (a modal view routes them from the window) still
    // hands the menu its arrows, Enter and Esc.
    listenDialogKey(root, 'keydown', event => {
        const key = event.key;
        if (key === 'ArrowDown' || key === 'ArrowUp') { event.preventDefault(); const next = stepIndex(items, active < 0 ? (key === 'ArrowDown' ? -1 : 0) : active, key === 'ArrowDown' ? 1 : -1); if (next >= 0) focusRow(next); }
        else if (key === 'Home' || key === 'End') { event.preventDefault(); const next = key === 'Home' ? stepIndex(items, -1, 1) : stepIndex(items, 0, -1); if (next >= 0) focusRow(next); }
        else if (key === 'ArrowRight') { event.preventDefault(); if (items[active]?.submenu) openChild(active, true); }
        else if (key === 'ArrowLeft' || (key === 'Escape' && parent)) { event.preventDefault(); event.stopPropagation(); if (parent) { close({focus: false}); parent.focusActive(); } else if (key === 'Escape') close(); }
        else if (key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
        else if (key === 'Enter' || key === ' ') { event.preventDefault(); if (active >= 0) activate(active); }
        else if (key === 'Tab') { event.preventDefault(); closeAll({focus: true}); }
        else if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
            clearTimeout(typedTimer); typed += key; typedTimer = setTimeout(() => { typed = ''; }, 600);
            const next = typeahead(items, typed.length > 1 ? active - 1 : active, typed); if (next !== active && next >= 0) focusRow(next);
        }
    });
    root.addEventListener('click', event => { const row = event.target.closest('.fv-cm-item'); if (row) activate(Number(row.dataset.index)); });
    root.addEventListener('pointermove', event => {
        const row = event.target.closest('.fv-cm-item'); if (!row) return;
        const index = Number(row.dataset.index);
        if (index !== active) { focusRow(index); if (items[index]?.submenu) openChild(index, false); else child?.handle.close({focus: false}); }
    });
    root.addEventListener('contextmenu', event => event.preventDefault());
    // Listeners arrive after the opening event has finished, so the right click that opened it does not close it.
    setTimeout(() => {
        if (closed) return;
        document.addEventListener('pointerdown', outside, true);
        addEventListener('blur', blurClose); addEventListener('resize', blurClose);
        document.addEventListener('scroll', scrollClose, true); addEventListener('freevideo-view', blurClose);
    }, 0);

    const self = {
        root, parent, items, close,
        contains: node => root.contains(node) || Boolean(child?.handle.contains(node)),
        focusFirst: () => { const first = stepIndex(items, -1, 1); if (first >= 0) focusRow(first); },
        focusActive: () => { if (active >= 0) focusRow(active); else root.focus({preventScroll: true}); },
        element: root,
    };
    if (!parent) current = self;
    // Opened from the keyboard: the first item has focus. From a pointer: the menu has focus, nothing is marked yet.
    if (at.keyboard || at.anchor) self.focusFirst(); else root.focus({preventScroll: true});
    return self;
}

// ── A layer of the command layer (palette, shortcut sheet): a modal dialog above every view. ──
// Modal, so the view under it is inert and focus stays inside (Tab and Shift+Tab wrap). Views that route keys
// through dialog_keys.js leave this dialog's keys alone while it is on top. Opening and closing ease in 180 ms and
// out in 160 ms, and either can be turned around halfway; reduced motion makes both instant.
const FOCUSABLE = 'button:not([disabled]),[href],input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
export function focusables(root) {
    return [...root.querySelectorAll(FOCUSABLE)].filter(node => !node.closest('[hidden],[inert]') && node.getClientRects().length);
}
export function createLayer({className, label, onKey = () => false, onClose = () => {}}) {
    const dialog = document.createElement('dialog');
    dialog.className = `fv-layer fv-glass fv-g3 ${className}`;
    dialog.dataset.fvTipScope = ''; dialog.dataset.fvCommandLayer = ''; dialog.dataset.fvOwnTab = ''; // Tab is the layer's own (below)
    dialog.setAttribute('aria-label', words(label));
    let animation = null, release = null, invoker = null, state = 'closed';
    const keys = event => {
        if (event.type !== 'keydown') return;
        // The layer's own keys first (the palette uses Tab for its scopes); then focus stays inside and Esc closes.
        if (onKey(event)) return;
        if (event.key === 'Tab') {
            const list = focusables(dialog); if (!list.length) { event.preventDefault(); return; }
            const at = list.indexOf(document.activeElement);
            const next = event.shiftKey ? (at <= 0 ? list.length - 1 : at - 1) : (at < 0 || at === list.length - 1 ? 0 : at + 1);
            event.preventDefault(); list[next].focus();
            return;
        }
        if (event.key === 'Escape') { event.preventDefault(); close(); }
    };
    // Without a view that gates keys, this dialog listens for itself.
    dialog.addEventListener('keydown', event => { if (!release) keys(event); });
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    // A click on the dim backdrop (outside the panel box) closes.
    dialog.addEventListener('pointerdown', event => {
        if (event.target !== dialog) return;
        const box = dialog.getBoundingClientRect();
        if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) close();
    });
    const settle = () => { animation = null; };
    function open() {
        if (state === 'open' || state === 'opening') return;
        if (state === 'closing' && animation) {
            // Turned around halfway: from where it is back to fully shown.
            state = 'opening'; delete dialog.dataset.closing; animation.reverse();
            animation.finished.then(() => { if (state === 'opening') { state = 'open'; settle(); } }, () => {});
            return;
        }
        invoker = document.activeElement instanceof HTMLElement && !dialog.contains(document.activeElement) ? document.activeElement : null;
        document.body.append(dialog);
        delete dialog.dataset.closing;
        dialog.showModal();
        release = isolateDialogKeys(dialog, (event, inside) => { if (inside || event.type === 'keydown') keys(event); });
        if (reducedMotion() || !dialog.animate) { state = 'open'; return; }
        state = 'opening';
        animation = dialog.animate([{opacity: 0, transform: 'translateY(-6px) scale(.985)'}, {opacity: 1, transform: 'none'}], {duration: 180, easing: EASE});
        dialog.animate([{opacity: 0}, {opacity: 1}], {duration: 180, easing: EASE, pseudoElement: '::backdrop'});
        animation.finished.then(() => { if (state === 'opening') { state = 'open'; settle(); } }, () => {});
    }
    function finish(restore) {
        state = 'closed'; settle(); delete dialog.dataset.closing;
        // A fade that holds its end (fill: forwards) stays on the element after it leaves the page; the next opening
        // would end on it and leave the layer invisible. Every animation on it goes with the close.
        for (const running of dialog.getAnimations?.({subtree: true}) || []) running.cancel();
        release?.(); release = null;
        if (dialog.open) dialog.close();
        dialog.remove();
        const back = invoker; invoker = null;
        if (restore && back?.isConnected && !back.closest('[inert]')) back.focus({preventScroll: true});
        onClose();
    }
    // animate: false closes at once (a command runs next and brings its own motion).
    function close({animate = true, restore = true} = {}) {
        if (state === 'closed' || (state === 'closing' && animate)) return;
        if (!animate || reducedMotion() || !dialog.animate) { animation?.cancel(); finish(restore); return; }
        dialog.dataset.closing = 'true';
        if (state === 'opening' && animation) { state = 'closing'; animation.reverse(); }
        else {
            state = 'closing';
            animation = dialog.animate([{opacity: 1, transform: 'none'}, {opacity: 0, transform: 'translateY(-4px) scale(.99)'}], {duration: 160, easing: EASE, fill: 'forwards'});
            dialog.animate([{opacity: 1}, {opacity: 0}], {duration: 160, easing: EASE, fill: 'forwards', pseudoElement: '::backdrop'});
        }
        const mine = animation;
        mine.finished.then(() => { if (state === 'closing' && animation === mine) finish(restore); }, () => {});
    }
    return {dialog, open, close, isOpen: () => state === 'open' || state === 'opening'};
}

export const closeContextMenu = () => current?.close({focus: false});
export const contextMenuOpen = () => Boolean(current);

// Right click (or Shift+F10, the menu key, a long press on touch) on `element` opens build(event)'s items.
// build returns null to leave the browser's own menu (e.g. inside a text field).
export function attachContextMenu(element, build, {label = ''} = {}) {
    if (!browser || !element) return () => {};
    let press = null, pressed = false;
    const openFor = (event, at) => {
        const items = build(event);
        if (!items) return false;
        event.preventDefault(); event.stopPropagation();
        openContextMenu(at, items, {label, returnFocus: event.target instanceof Element ? event.target.closest('button,[tabindex],a,input,textarea') || element : element});
        return true;
    };
    const onMenu = event => {
        if (pressed) { pressed = false; event.preventDefault(); return; }
        const keyboard = event.button === -1 || (event.clientX === 0 && event.clientY === 0);
        if (keyboard) { const box = (event.target instanceof Element ? event.target : element).getBoundingClientRect(); openFor(event, {x: box.left + 8, y: box.bottom, keyboard: true}); }
        else openFor(event, {x: event.clientX, y: event.clientY});
    };
    // Windows and Linux send a contextmenu event for the menu key and Shift+F10 themselves; a Mac does not.
    const onKey = event => {
        if (MAC && event.key === 'F10' && event.shiftKey) {
            const target = event.target instanceof Element ? event.target : element, box = target.getBoundingClientRect();
            openFor(event, {x: box.left + 8, y: box.bottom, keyboard: true});
        }
    };
    const onDown = event => {
        if (event.pointerType !== 'touch') return;
        const start = {x: event.clientX, y: event.clientY};
        clearTimeout(press?.timer);
        press = {start, timer: setTimeout(() => { if (openFor(event, start)) pressed = true; press = null; }, 500)};
    };
    const onMove = event => { if (press && Math.hypot(event.clientX - press.start.x, event.clientY - press.start.y) > 8) { clearTimeout(press.timer); press = null; } };
    const onUp = () => { if (press) { clearTimeout(press.timer); press = null; } };
    element.addEventListener('contextmenu', onMenu);
    element.addEventListener('keydown', onKey);
    element.addEventListener('pointerdown', onDown);
    element.addEventListener('pointermove', onMove, {passive: true});
    element.addEventListener('pointerup', onUp); element.addEventListener('pointercancel', onUp);
    return () => {
        element.removeEventListener('contextmenu', onMenu); element.removeEventListener('keydown', onKey);
        element.removeEventListener('pointerdown', onDown); element.removeEventListener('pointermove', onMove);
        element.removeEventListener('pointerup', onUp); element.removeEventListener('pointercancel', onUp);
    };
}

if (browser) globalThis.FreeVideoMenu = {openContextMenu, attachContextMenu, closeContextMenu};
