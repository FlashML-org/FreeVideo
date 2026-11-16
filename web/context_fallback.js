// FreeVideo's own right-click menu wherever a part has none of its own, so the browser's menu never shows over
// FreeVideo. Works, pictures, videos and timeline clips keep their own menus (attachContextMenu and the views' own
// handlers run first and take the event); this is what is left:
//   - a text box: Cut, Copy, Paste, Select all (each greyed with the reason when it cannot be done);
//   - selected text elsewhere: Copy;  a link: open it in a new tab, copy its address;  a picture: open it in a new tab;
//   - anywhere else: Reload, the command palette, keyboard shortcuts, Settings.
// The menu is the shared one (context_menu.js): the same glass and rows as every FreeVideo menu, arrow keys, Enter,
// typing a letter, Esc to close. Shift+F10 and the menu key open it at the focused part. Only FreeVideo's own parts
// are covered: the node view's canvas and ComfyUI's own panels keep theirs.
import {openContextMenu, floatingHost, MAC} from './context_menu.js';

const root = document.documentElement;
const TEXT_INPUTS = new Set(['text', 'search', 'url', 'tel', 'email', 'password', 'number']);
const pair = (en, zh) => [en, zh];

// FreeVideo's parts: a view or anything FreeVideo floats over the page (its classes start with fv-, its dialogs and
// popovers), the top bar of the node view. ComfyUI's canvas and panels are not.
const OURS = 'dialog.fv-studio, .fv-view-navigation, .fv-topbar, .sgf, [class^="fv-"], [class*=" fv-"]';
function ours(target) {
    if (!(target instanceof Element) || target.closest('[data-fv-native-menu]')) return false;
    // The page root carries fv-app while FreeVideo is shown; only a part below it counts.
    for (let node = target; node && node !== document.body && node !== root; node = node.parentElement) if (node.matches(OURS)) return true;
    return false;
}

function fieldOf(target) {
    const field = target instanceof Element ? target.closest('input, textarea, [contenteditable]:not([contenteditable=false])') : null;
    if (!field) return null;
    if (field.matches('input') && !TEXT_INPUTS.has((field.getAttribute('type') || 'text').toLowerCase())) return null;
    return field;
}
const plainField = field => field.matches('input, textarea');
function selectionIn(field) {
    if (plainField(field)) {
        try {
            const start = field.selectionStart, end = field.selectionEnd;
            if (start == null || end == null) return {start: null, end: null, text: ''};
            return {start, end, text: field.value.slice(start, end)};
        } catch { return {start: null, end: null, text: ''}; }
    }
    const selection = getSelection();
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    return {range: range && field.contains(range.commonAncestorContainer) ? range.cloneRange() : null, text: range && field.contains(range.commonAncestorContainer) ? selection.toString() : ''};
}
// Back into the box, with the selection it had when the menu opened.
function refocus(field, kept) {
    field.focus({preventScroll: true});
    if (plainField(field)) { if (kept.start != null) try { field.setSelectionRange(kept.start, kept.end); } catch { /* Not selectable. */ } }
    else if (kept.range) { const selection = getSelection(); selection.removeAllRanges(); selection.addRange(kept.range); }
}
function announce(field, inputType, data = null) {
    field.dispatchEvent(new InputEvent('input', {bubbles: true, inputType, data}));
}

async function writeText(text) {
    try { if (globalThis.isSecureContext && navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return true; } } catch { /* The older way below. */ }
    // Without the clipboard interface (a page opened by address on the network): copied through a hidden box, inside
    // the dialog in front so it can take the selection.
    const area = document.createElement('textarea');
    area.value = text; area.readOnly = true; area.setAttribute('aria-hidden', 'true');
    area.style.cssText = 'position:fixed;left:-10000px;top:0;width:1px;height:1px;opacity:0';
    const back = document.activeElement;
    floatingHost().append(area); area.select();
    let done = false;
    try { done = document.execCommand('copy'); } catch { done = false; }
    area.remove(); back?.focus?.({preventScroll: true});
    return done;
}
const canRead = () => Boolean(globalThis.isSecureContext && navigator.clipboard?.readText);
const keyName = combo => MAC ? combo.replace('Mod+', '⌘') : combo.replace('Mod+', 'Ctrl+');

function textItems(field) {
    const kept = selectionIn(field);
    const locked = field.disabled || field.readOnly || (!plainField(field) && !field.isContentEditable);
    const secret = field.matches('input[type=password]');
    const empty = plainField(field) ? !field.value : !field.textContent;
    const none = !kept.text;
    const why = {
        locked: pair('This box cannot be changed', '这个框不能修改'),
        none: pair('Select some text first', '先选中文字'),
        secret: pair('A password is not copied', '密码不能复制'),
        empty: pair('The box is empty', '框里没有文字'),
        paste: pair(`Press ${keyName('Mod+V')} to paste here`, `请按 ${keyName('Mod+V')} 粘贴`),
    };
    return [
        {id: 'text-cut', label: pair('Cut', '剪切'), icon: 'scissors', key: 'Mod+X', use: pair('Move the selected text to the clipboard', '把选中的文字移到剪贴板'),
            disabled: locked ? why.locked : secret ? why.secret : none ? why.none : '',
            run: async () => {
                refocus(field, kept);
                let done = false;
                try { done = document.execCommand('cut'); } catch { done = false; }
                if (done) return;
                if (!(await writeText(kept.text))) return;
                refocus(field, kept);
                if (plainField(field)) { field.setRangeText('', kept.start, kept.end, 'end'); announce(field, 'deleteByCut'); }
                else { document.execCommand('delete'); }
            }},
        {id: 'text-copy', label: pair('Copy', '复制'), icon: 'copy', key: 'Mod+C', use: pair('Copy the selected text', '复制选中的文字'),
            disabled: secret ? why.secret : none ? why.none : '',
            run: async () => { await writeText(kept.text); refocus(field, kept); }},
        {id: 'text-paste', label: pair('Paste', '粘贴'), icon: 'paste', key: 'Mod+V', use: pair('Put the copied text here', '把复制的文字放到这里'),
            disabled: locked ? why.locked : !canRead() ? why.paste : '',
            run: async () => {
                let text = '';
                try { text = await navigator.clipboard.readText(); } catch { refocus(field, kept); return; }
                refocus(field, kept);
                if (!text) return;
                let done = false;
                try { done = document.execCommand('insertText', false, text); } catch { done = false; }
                if (!done && plainField(field)) { field.setRangeText(text, field.selectionStart ?? field.value.length, field.selectionEnd ?? field.value.length, 'end'); announce(field, 'insertFromPaste', text); }
            }},
        '-',
        {id: 'text-select-all', label: pair('Select all', '全选'), icon: 'select-all', key: 'Mod+A', use: pair('Select all the text in this box', '选中这个框里的全部文字'),
            disabled: empty ? why.empty : '',
            run: () => {
                field.focus({preventScroll: true});
                if (plainField(field)) { try { field.select(); } catch { /* Not selectable. */ } }
                else { const selection = getSelection(); selection.removeAllRanges(); selection.selectAllChildren(field); }
            }},
    ];
}

function command(id) {
    const registry = globalThis.FreeVideoCommands?.registry;
    const row = registry?.visible(root.dataset.fvView || 'nodes').find(entry => entry.command.id === id);
    return row?.command || null;
}
function generalItems() {
    const run = (id, fallback) => () => { const found = command(id); if (found) return found.run(); return fallback?.(); };
    return [
        {id: 'page-reload', label: pair('Reload', '刷新'), icon: 'reload', key: 'Mod+R', use: pair('Load this page again; the view in front comes back', '重新载入页面，回到当前视图'),
            run: () => location.reload()},
        '-',
        {id: 'palette-open', label: pair('Command palette', '命令面板'), icon: 'search', key: 'Mod+K', use: pair('Search works and run any action', '搜索作品、提示词并执行操作'),
            when: () => Boolean(globalThis.FreeVideoCommands?.openPalette), run: () => globalThis.FreeVideoCommands.openPalette()},
        {id: 'shortcuts-open', label: pair('Keyboard shortcuts', '快捷键速查'), icon: 'keyboard', key: '?', use: pair('Every shortcut of every view', '所有视图的快捷键'),
            when: () => Boolean(command('shortcuts.open')), run: run('shortcuts.open')},
        {id: 'settings-open', label: pair('Settings', '设置'), icon: 'gear', use: pair('Models, storage, network and more', '模型、存储、网络等设置'),
            run: run('settings.open', async () => (await import('./setup.js')).openSetup())},
    ];
}

function itemsFor(target) {
    const field = fieldOf(target);
    if (field) return textItems(field);
    const items = [];
    const selected = String(getSelection?.() || '').trim();
    if (selected) items.push({id: 'selection-copy', label: pair('Copy', '复制'), icon: 'copy', key: 'Mod+C', use: pair('Copy the selected text', '复制选中的文字'), run: () => writeText(selected)});
    const link = target.closest('a[href]');
    const href = link && !/^(javascript:|#)/i.test(link.getAttribute('href') || '') ? link.href : '';
    if (href) items.push(
        {id: 'link-open', label: pair('Open link in a new tab', '在新标签页打开链接'), icon: 'external', use: pair('Open where this link goes', '打开这个链接'), run: () => window.open(href, '_blank', 'noopener')},
        {id: 'link-copy', label: pair('Copy link address', '复制链接地址'), icon: 'copy', use: pair('Copy where this link goes', '复制这个链接的地址'), run: () => writeText(href)});
    // A picture by itself (not the face of a card or button, which stands for its work: those get the general menu).
    const picture = target.closest('img');
    const standalone = picture && !picture.closest('button, [role=button], [role=option], [role=tab], a, label');
    const source = picture?.currentSrc || picture?.src || '';
    if (standalone && source && !source.startsWith('data:')) items.push({id: 'picture-open', label: pair('Open picture in a new tab', '在新标签页打开图片'), icon: 'image', use: pair('See the picture by itself', '单独查看这张图片'), run: () => window.open(source, '_blank', 'noopener')});
    if (items.length) return items;
    return generalItems();
}

function openFor(event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const items = itemsFor(target);
    if (!items?.length) return;
    event.preventDefault();
    const keyboard = event.button === -1 || (event.clientX === 0 && event.clientY === 0);
    let at = {x: event.clientX, y: event.clientY};
    if (keyboard) { const box = target.getBoundingClientRect(); at = {x: box.left + 8, y: box.bottom, keyboard: true}; }
    openContextMenu(at, items, {returnFocus: fieldOf(target) || target.closest('button,[tabindex],a,input,textarea') || null});
}

// The views' own menus run first: their handlers sit on the parts and the views' dialogs, and they take the event
// (preventDefault, often stopPropagation). This one waits for the event to come back up to the dialog or popover it
// happened in (or to the document), after every handler there, and only then, if nobody took it, opens the menu.
// A view that keeps its events to itself (stopPropagation on its own dialog) still reaches this: the listener is on
// that same dialog, added before the event gets there, so it runs after the view's own.
let pending = null;
function late(event) {
    if (event !== pending) return;
    pending = null;
    if (event.defaultPrevented || !ours(event.target)) return;
    openFor(event);
}
function early(event) {
    if (!(event.target instanceof Element) || !ours(event.target)) return;
    pending = event;
    const host = event.target.closest('dialog, [popover]') || document;
    host.addEventListener('contextmenu', late, {once: true});
    // Taken by a part's own menu before it got this far: the waiting listener goes with the event.
    setTimeout(() => { host.removeEventListener('contextmenu', late); if (pending === event) pending = null; }, 0);
}

export function installContextFallback() {
    if (globalThis.FreeVideoContextFallback) return;
    globalThis.FreeVideoContextFallback = {itemsFor};
    globalThis.addEventListener('contextmenu', early, {capture: true});
}
installContextFallback();
