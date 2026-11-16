// The shortcut sheet: every keyboard shortcut of every view in one place (press ?, or open it from the command
// palette). Views list their own with registerShortcuts(viewId, rows); the shortcuts themselves stay with the views,
// this module only shows them. The built-in rows below are the shortcuts the views have today.
// A row is {keys: 'Mod+Z / Mod+Shift+Z', title: [en, zh], group?: [en, zh]}; Mod is Ctrl, or ⌘ on a Mac.
import { t, words, CN, glyph, keyChips, formatKeys, createLayer, addSheet } from './context_menu.js';

const browser = typeof document !== 'undefined';
addSheet('shortcuts.css', import.meta.url);

export const VIEW_NAMES = Object.freeze({
    global: ['Everywhere', '全局'], studio: ['Create', '创作面板'], image: ['Images', '图像视图'], nodes: ['Nodes', '节点视图'],
    editor: ['Edit', '剪辑视图'], autopilot: ['Autopilot', 'AI 全自动'], library: ['Creations', '作品库'],
});
export const VIEW_ORDER = Object.freeze(['global', 'studio', 'image', 'editor', 'autopilot', 'nodes', 'library']);

const G = {
    layer: ['Command layer', '命令与搜索'], views: ['Views', '视图'], general: ['General', '通用'],
    prompt: ['Prompt', '提示词'], generate: ['Generate', '生成'], result: ['Result', '结果'],
    history: ['History', '历史'], compare: ['Compare', '对比'], picture: ['Picture editing', '图像编辑'],
    playback: ['Playback', '播放'], timeline: ['Timeline', '时间线'], clips: ['Clips', '片段'], edit: ['Editing', '编辑'],
    chat: ['Conversation', '对话'], tasks: ['Running tasks', '进行中的任务'], library: ['Creations', '作品库'],
};

// Shortcuts the views have in the 0.5.4 code and in this branch (each one checked in the source it names).
export const BUILTIN_SHORTCUTS = Object.freeze({
    global: [
        {keys: 'Mod+K', title: ['Command palette', '命令面板'], group: G.layer},
        {keys: 'Mod+Shift+F', title: ['Search creations, images and cast', '搜索作品、图像和角色'], group: G.layer},
        {keys: '?', title: ['Keyboard shortcuts', '快捷键速查'], group: G.layer},
        {keys: 'Alt+1', title: ['Create', '创作面板'], group: G.views},
        {keys: 'Alt+2', title: ['Images', '图像视图'], group: G.views},
        {keys: 'Alt+3', title: ['Nodes', '节点视图'], group: G.views},
        {keys: 'Alt+4', title: ['Edit', '剪辑视图'], group: G.views},
        {keys: 'Alt+5', title: ['Autopilot', 'AI 全自动'], group: G.views},
        {keys: 'Esc', title: ['Close a menu, dialog or panel', '关闭菜单、对话框或浮层'], group: G.general},
        {keys: 'Shift+F10 / ContextMenu', title: ['Menu of the focused item', '焦点所在项的菜单'], group: G.general},
        {keys: 'Enter / Space', title: ['Back to the generation', '回到生成画面'], group: G.tasks},
        {keys: 'Esc', title: ['Fold the task list', '收起任务列表'], group: G.tasks},
    ],
    studio: [
        {keys: 'Mod+Enter', title: ['Generate video', '生成视频'], group: G.generate},
        {keys: '@', title: ['Mention a picture or character', '提及图片或角色'], group: G.prompt},
        {keys: 'Up / Down', title: ['Choose a suggestion', '选择候选项'], group: G.prompt},
        {keys: 'Enter / Tab', title: ['Insert the chosen item', '插入选中的项'], group: G.prompt},
        {keys: 'Esc', title: ['Close a panel, then Create', '先关浮层，再关创作面板'], group: G.general},
    ],
    image: [
        {keys: 'Mod+Enter', title: ['Generate image', '生成图片'], group: G.generate},
        {keys: 'Mod+V', title: ['Paste a picture as reference', '粘贴图片作为参考图'], group: G.prompt},
        {keys: 'Shift+Click / Mod+Click', title: ['Select several pictures', '多选图片'], group: G.history},
        {keys: 'Left / Right', title: ['Move the compare line', '移动对比分界线'], group: G.compare},
        {keys: 'Esc', title: ['Close a menu, then the view', '先关菜单，再关视图'], group: G.general},
        {keys: 'Mod+Z / Mod+Shift+Z', title: ['Undo / redo', '撤销 / 重做'], group: G.picture},
        {keys: 'Mod+Enter', title: ['Save the edit', '保存编辑'], group: G.picture},
        {keys: 'Space+Drag', title: ['Pan', '平移'], group: G.picture},
        {keys: 'Mod+Plus / Mod+Minus', title: ['Zoom in / out', '放大 / 缩小'], group: G.picture},
        {keys: 'Mod+0 / Mod+1', title: ['Fit to window / 100%', '适合窗口 / 100%'], group: G.picture},
        {keys: 'BracketLeft / BracketRight', title: ['Brush size', '笔刷大小'], group: G.picture},
        {keys: 'Mod+A / Mod+D / Mod+Shift+I', title: ['Select all / deselect / invert', '全选 / 取消选择 / 反选'], group: G.picture},
        {keys: 'Mod+J', title: ['Copy to a new layer', '复制到新图层'], group: G.picture},
        {keys: 'Delete / Alt+Delete', title: ['Clear / fill the selection', '清除 / 填充选区'], group: G.picture},
        {keys: 'X / D', title: ['Swap / default colors', '交换颜色 / 默认颜色'], group: G.picture},
        {keys: 'V / M / L / B / E / T', title: ['Move / marquee / lasso / brush / eraser / text tool', '移动 / 选框 / 套索 / 画笔 / 橡皮擦 / 文字工具'], group: G.picture},
    ],
    editor: [
        {keys: 'Space', title: ['Play / pause', '播放 / 暂停'], group: G.playback},
        {keys: 'J / K / L', title: ['Play backward / pause / forward', '向后播放 / 暂停 / 向前播放'], group: G.playback},
        {keys: 'Left / Right', title: ['Previous / next frame', '上一帧 / 下一帧'], group: G.playback},
        {keys: 'Up / Down', title: ['Previous / next cut', '上一个 / 下一个剪辑点'], group: G.playback},
        {keys: 'Home / End', title: ['Start / end', '开头 / 结尾'], group: G.playback},
        {keys: 'Shift+L', title: ['Loop', '循环播放'], group: G.playback},
        {keys: 'Shift+Space', title: ['Play the selected range only', '只播选中的范围'], group: G.playback},
        {keys: 'F', title: ['Full screen', '全屏'], group: G.playback},
        {keys: 'S', title: ['Snapping', '吸附'], group: G.timeline},
        {keys: 'M / Shift+M / Alt+M', title: ['Marker / previous / next', '标记 / 上一个 / 下一个'], group: G.timeline},
        {keys: 'Plus / Minus', title: ['Zoom in / out', '放大 / 缩小'], group: G.timeline},
        {keys: 'Shift+Z', title: ['Fit all', '看全部'], group: G.timeline},
        {keys: 'Mod+Z / Mod+Shift+Z', title: ['Undo / redo', '撤销 / 重做'], group: G.edit},
        {keys: 'Mod+B', title: ['Split', '剪开'], group: G.edit},
        {keys: 'Q / W', title: ['Trim before / after the playhead', '裁掉播放头前面 / 后面'], group: G.edit},
        {keys: 'Delete', title: ['Delete', '删除'], group: G.edit},
        {keys: 'Shift+Delete', title: ['Delete and close the gap', '删除并合上空隙'], group: G.edit},
        {keys: 'Mod+C / Mod+X / Mod+V', title: ['Copy / cut / paste', '复制 / 剪切 / 粘贴'], group: G.edit},
        {keys: 'Mod+D', title: ['Duplicate', '创建副本'], group: G.edit},
        {keys: 'Mod+A', title: ['Select all', '全选'], group: G.edit},
        {keys: 'Shift+Click / Mod+Click', title: ['Extend the selection', '扩展选择'], group: G.edit},
        {keys: 'T', title: ['Add text', '添加文字'], group: G.clips},
        {keys: 'R', title: ['Record or stop', '录音或停止'], group: G.clips},
        {keys: 'Mod+Enter', title: ['Generate', '生成'], group: G.generate},
    ],
    autopilot: [
        {keys: 'Enter', title: ['Send', '发送'], group: G.chat},
        {keys: 'Shift+Enter', title: ['New line', '换行'], group: G.chat},
        {keys: 'Mod+V', title: ['Attach pasted pictures', '粘贴图片作为附件'], group: G.chat},
        {keys: 'Esc', title: ['Close a list or drawer, then Autopilot', '先关列表或抽屉，再关 AI 全自动'], group: G.general},
    ],
    library: [
        {keys: 'Esc', title: ['Close the viewer, then the library', '先关大图，再关作品库'], group: G.library},
    ],
});

// ── Registry ──
const registered = new Map();   // viewId → Set of row lists
let changeListeners = new Set();
export function registerShortcuts(viewId, rows) {
    const id = String(viewId || 'global');
    if (!registered.has(id)) registered.set(id, new Set());
    const list = (rows || []).filter(row => row && row.keys && row.title);
    registered.get(id).add(list);
    changed();
    return () => { registered.get(id)?.delete(list); changed(); };
}
export function onShortcutsChange(fn) { changeListeners.add(fn); return () => changeListeners.delete(fn); }
function changed() { for (const fn of [...changeListeners]) try { fn(); } catch { /* One listener never stops the others. */ } }
export function resetShortcutsForTests() { registered.clear(); changeListeners = new Set(); }

// 'Mod+Z / Mod+Shift+Z' with 'Undo / redo' reads better as two rows; a row whose keys and title (in both languages)
// have the same number of ' / ' parts is shown as that many rows. 'Delete / Backspace' with one title stays one row.
export function splitRow(row) {
    const keys = String(row.keys).split(/\s*\/\s*/);
    const titles = [0, 1].map(i => words(row.title, i === 1).split(/\s+\/\s+/));
    if (keys.length < 2 || titles.some(list => list.length !== keys.length)) return [row];
    // "Undo / redo": the later parts take the first part's capital.
    const cap = text => text.charAt(0).toUpperCase() + text.slice(1);
    return keys.map((key, i) => ({...row, keys: key, title: [cap(titles[0][i]), titles[1][i]]}));
}
// Every row of one view: the built-in rows, then the view's own; a registered row with the same keys and group
// replaces the built-in one (the view knows best what its key does now).
export function rowsOf(viewId, cn = CN) {
    const own = [...(registered.get(viewId) || [])].flat();
    const keyOf = row => `${words(row.group || '', cn)}|${formatKeys(row.keys, false)}`;
    const taken = new Set(own.map(keyOf));
    return [...(BUILTIN_SHORTCUTS[viewId] || []).filter(row => !taken.has(keyOf(row))), ...own]
        .filter(row => typeof row.when !== 'function' || row.when()).flatMap(splitRow);
}
// Sections go into columns by height, each to the shortest column, so none is split between two.
export function columnsOf(list, count) {
    const columns = Array.from({length: Math.max(1, count)}, () => ({height: 0, items: []}));
    for (const section of list) {
        const target = columns.reduce((low, column) => column.height < low.height ? column : low, columns[0]);
        target.items.push(section); target.height += section.rows.length + 1.5;
    }
    return columns.map(column => column.items);
}
export const viewsWithShortcuts = () => VIEW_ORDER.filter(view => (BUILTIN_SHORTCUTS[view]?.length || registered.get(view)?.size));

// The sections the sheet shows: {view, group, title, rows}, in view order then group order.
// filter: 'all' or a view id (that view, then Everywhere); query: words of the action or the keys.
export function sections({filter = 'all', query = '', cn = CN, mac} = {}) {
    const views = filter === 'all' ? viewsWithShortcuts() : [filter, ...(filter === 'global' ? [] : ['global'])];
    const want = String(query || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
    const out = [];
    for (const view of views) {
        const groups = new Map();
        for (const row of rowsOf(view, cn)) {
            if (want.length) {
                const hay = [words(row.title, true), words(row.title, false), formatKeys(row.keys, mac), formatKeys(row.keys, !mac), row.keys].join(' ').toLowerCase();
                if (!want.every(part => hay.includes(part))) continue;
            }
            const group = words(row.group || VIEW_NAMES[view] || ['', ''], cn);
            if (!groups.has(group)) groups.set(group, []);
            groups.get(group).push(row);
        }
        for (const [group, rows] of groups) {
            const viewName = words(VIEW_NAMES[view] || [view, view], cn);
            out.push({view, group, title: filter === 'all' || view !== filter ? (group === viewName ? viewName : `${viewName} · ${group}`) : group, rows});
        }
    }
    return out;
}

// ── The sheet ──
let layer = null, state = {filter: 'all', query: ''}, parts = null;
const currentView = () => globalThis.FreeVideoShell?.view?.() || 'nodes';

function build() {
    layer = createLayer({className: 'fv-keys-sheet', label: ['Keyboard shortcuts', '快捷键'], onKey: event => {
        // Arrow keys move between the view tabs when one has focus (one tab stop for the whole row).
        const tab = event.target.closest?.('[role=tab]');
        if (tab && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
            event.preventDefault();
            const tabs = [...parts.tabs.querySelectorAll('[role=tab]')], at = tabs.indexOf(tab);
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (at + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
            choose(tabs[next].dataset.view); tabs[next].focus();
            return true;
        }
        // Typing anywhere in the sheet goes to the filter.
        if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && event.target !== parts.input && event.key !== '?' && event.key !== ' ') { parts.input.focus(); return false; }
        if (event.key === '?' && event.target !== parts.input) { event.preventDefault(); layer.close(); return true; }
        if (event.key === 'Escape' && parts.input.value && event.target === parts.input) { event.preventDefault(); parts.input.value = ''; state.query = ''; draw(); return true; }
        return false;
    }});
    const d = layer.dialog;
    d.innerHTML = `<header class="fv-ks-head">
        <span class="fv-ks-mark">${glyph('keyboard')}</span><h2 class="fv-ks-title" id="fv-ks-title"></h2>
        <label class="fv-ks-find">${glyph('search')}<input type="search" spellcheck="false" autocomplete="off"></label>
        <button type="button" class="fv-ks-close">${glyph('close')}</button></header>
      <div class="fv-ks-tabs" role="tablist"></div>
      <div class="fv-ks-body" role="tabpanel" tabindex="-1"></div>`;
    d.setAttribute('aria-labelledby', 'fv-ks-title');
    parts = {title: d.querySelector('.fv-ks-title'), input: d.querySelector('input'), close: d.querySelector('.fv-ks-close'), tabs: d.querySelector('.fv-ks-tabs'), body: d.querySelector('.fv-ks-body')};
    parts.title.textContent = t('Keyboard shortcuts', '快捷键');
    parts.input.placeholder = t('Find a shortcut', '查找快捷键');
    parts.input.setAttribute('aria-label', t('Find a shortcut', '查找快捷键'));
    parts.close.setAttribute('aria-label', t('Close', '关闭'));
    Object.assign(parts.close.dataset, {fvTipName: t('Close', '关闭'), fvTipUse: t('Back to the previous screen', '回到刚才的界面'), fvTipKey: 'Esc'});
    parts.close.onclick = () => layer.close();
    parts.input.addEventListener('input', () => { state.query = parts.input.value; draw(); });
    parts.tabs.addEventListener('click', event => { const tab = event.target.closest('[role=tab]'); if (tab) choose(tab.dataset.view); });
    onShortcutsChange(() => { if (layer?.isOpen()) draw(); });
    // Columns follow the sheet's width (also when its style arrives after it opened, or the window is resized).
    let columns = 0;
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { const next = columnCount(); if (layer?.isOpen() && next !== columns) draw(); }) : null;
    observer?.observe(parts.body);
    parts.columns = () => { columns = columnCount(); return columns; };
}

function choose(view) { state.filter = view; draw(); }
function columnCount() { const width = parts.body.clientWidth || innerWidth; return width >= 760 ? 3 : width >= 500 ? 2 : 1; }

function draw() {
    const views = viewsWithShortcuts();
    if (state.filter !== 'all' && !views.includes(state.filter)) state.filter = 'all';
    parts.tabs.replaceChildren(...['all', ...views].map(view => {
        const tab = document.createElement('button'); tab.type = 'button'; tab.setAttribute('role', 'tab'); tab.dataset.view = view;
        const selected = view === state.filter;
        tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
        tab.textContent = view === 'all' ? t('All', '全部') : words(VIEW_NAMES[view] || view);
        tab.dataset.fvTipName = tab.textContent;
        tab.dataset.fvTipUse = view === 'all' ? t('Shortcuts of every view', '所有视图的快捷键') : view === 'global' ? t('Keys that work in every view', '在任何视图都能用的按键') : t('This view and the keys of every view', '这个视图加全局按键');
        return tab;
    }));
    const list = sections({filter: state.filter, query: state.query});
    if (!list.length) {
        const empty = document.createElement('p'); empty.className = 'fv-ks-empty';
        empty.textContent = state.query ? t('No shortcut matches.', '没有匹配的快捷键。') : t('No shortcuts here yet.', '这里还没有快捷键。');
        parts.body.replaceChildren(empty);
        return;
    }
    const count = parts.columns();
    parts.body.style.setProperty('--fv-ks-cols', String(count));
    parts.body.replaceChildren(...columnsOf(list, count).map(items => {
        const column = document.createElement('div'); column.className = 'fv-ks-col';
        column.append(...items.map(drawSection));
        return column;
    }));
}

function drawSection(section) {
    {
        const box = document.createElement('section'); box.className = 'fv-ks-section';
        const head = document.createElement('h3'); head.textContent = section.title; box.append(head);
        for (const row of section.rows) {
            const line = document.createElement('div'); line.className = 'fv-ks-row';
            const name = document.createElement('span'); name.className = 'fv-ks-name'; name.textContent = words(row.title);
            line.append(name, keyChips(row.keys));
            box.append(line);
        }
        return box;
    }
}

export function openShortcuts({view = null} = {}) {
    if (!browser) return;
    if (!layer) build();
    const here = view || currentView();
    const views = viewsWithShortcuts();
    state = {filter: views.includes(here) && here !== 'global' ? here : 'all', query: ''};
    parts.input.value = '';
    layer.open();
    draw();
    requestAnimationFrame(() => parts.input.focus({preventScroll: true}));
}
export const closeShortcuts = () => layer?.close();
export const shortcutsOpen = () => Boolean(layer?.isOpen());

if (browser) globalThis.FreeVideoShortcuts = {registerShortcuts, openShortcuts};
