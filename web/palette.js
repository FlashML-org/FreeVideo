// The command palette (Ctrl+K, ⌘K on a Mac): one field that finds and runs any action of any view, and finds the
// user's creations, pictures and cast (global_search.js). Views add their actions with registerCommands(viewId, list);
// the built-in ones switch views, open the library, settings, tutorial and feedback, change theme, material and
// language, go to the latest generation, open Recently deleted, notifications and the shortcut sheet.
// Recently used commands come first; the whole palette works from the keyboard; Esc closes it.
//
// A command: {id, title: [en, zh], use?: [en, zh] (one line on what it does), group?: [en, zh], keywords?: [...],
//   icon?, key?: 'Mod+Enter', when?: () => bool (hidden when false), why?: () => '' | [en, zh] (shown greyed with the
//   reason), confirm?: [en, zh] (asks first), anyView?: true (offered from every view), run: ctx => any}.
// This module also loads the rest of the command layer: shortcuts, notifications, search and the right-click menu.
import { t, words, CN, MAC, glyph, keyChips, formatKeys, createLayer, addSheet } from './context_menu.js';
import { openShortcuts, shortcutsOpen, closeShortcuts, VIEW_NAMES } from './shortcuts.js';
import { searchEverything, searchSettled, warmSearch, fillPrompt } from './global_search.js';
import { openNotifications, latestFinished, toast, visitJob, openWork } from './notifications.js';
import { jobs } from './task_store.js';
import { userMessage } from './user_message.js';
import { openIssue, preloadIssue } from './issue_button.js';
import { languageUrl } from './i18n.js';

const browser = typeof document !== 'undefined';
addSheet('palette.css', import.meta.url);

// ── Fuzzy matching ──────────────────────────────────────────────────────────────────────────────────────────────
// Text is compared case-folded and width-folded. A query matches when its characters appear in order; a run of
// consecutive characters, a match at the start of the text or of a word, and a short text all score higher.
// Returns {score, ranges: [[start, end), ...]} or null.
export const fold = text => String(text ?? '').normalize('NFKC').toLowerCase();
const boundary = (text, at) => at === 0 || /[\s\-_/·.,:(（、]/.test(text[at - 1]) || (/[a-z]/.test(text[at]) && /[^a-z]/.test(text[at - 1]));
export function fuzzyMatch(query, text) {
    const q = fold(query).replace(/\s+/g, ''), s = fold(text);
    if (!q) return {score: 0, ranges: []};
    if (!s) return null;
    const direct = s.indexOf(q);
    if (direct >= 0) {
        // Prefer a match that starts a word over an earlier one inside a word.
        let at = direct;
        for (let i = direct; i >= 0 && i < s.length; i = s.indexOf(q, i + 1)) { if (boundary(s, i)) { at = i; break; } }
        const score = 1000 + (at === 0 ? 300 : boundary(s, at) ? 150 : 0) - at * 3 - (s.length - q.length);
        return {score, ranges: [[at, at + q.length]]};
    }
    // Characters in order, greedy from the left but jumping to word starts when they come up.
    const ranges = [];
    let score = 500, from = 0, last = -2;
    for (const ch of q) {
        let at = -1;
        for (let i = from; i < s.length; i++) {
            if (s[i] !== ch) continue;
            if (at < 0) at = i;
            if (i === last + 1 || boundary(s, i)) { at = i; break; }
        }
        if (at < 0) return null;
        if (at === last + 1) { score += 18; ranges.at(-1)[1] = at + 1; }
        else { score -= Math.min(40, (at - last - 1) * 4); if (boundary(s, at)) score += 30; ranges.push([at, at + 1]); }
        last = at; from = at + 1;
    }
    score -= (s.length - q.length) * 0.5 + ranges.length * 6;
    return {score, ranges};
}

// One command against a query: every word of the query has to match the title (either language) or a keyword.
// The title in the language shown counts most; the ranges are for highlighting that title. A searchOnly command
// (a tutorial chapter) matches its title and its other names (aliases) only, each word as typed.
export function scoreCommand(command, query, cn = CN) {
    const parts = fold(query).trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return {score: 0, ranges: []};
    const shown = words(command.title, cn), other = words(command.title, !cn);
    const extra = command.searchOnly ? [...(command.aliases || [])] : [...(command.keywords || []), words(command.group || '', cn), words(command.group || '', !cn)].filter(Boolean);
    let total = 0; const ranges = [];
    for (const part of parts) {
        // A chapter title is long: its words have to appear as typed, not as scattered letters.
        const allowed = text => !command.searchOnly || fold(text).includes(part);
        const here = allowed(shown) ? fuzzyMatch(part, shown) : null, there = other !== shown && allowed(other) ? fuzzyMatch(part, other) : null;
        let best = here ? here.score : -Infinity;
        if (there) best = Math.max(best, there.score * 0.85);
        for (const word of extra) { const hit = allowed(word) ? fuzzyMatch(part, word) : null; if (hit) best = Math.max(best, hit.score * 0.7); }
        if (best === -Infinity) return null;
        total += best;
        if (here) ranges.push(...here.ranges);
    }
    return {score: total, ranges: mergeRanges(ranges)};
}
export function mergeRanges(ranges) {
    const sorted = [...ranges].sort((a, b) => a[0] - b[0]), out = [];
    for (const range of sorted) { if (out.length && range[0] <= out.at(-1)[1]) out.at(-1)[1] = Math.max(out.at(-1)[1], range[1]); else out.push([...range]); }
    return out;
}

// Ranks visible commands for a query. Without a query: recently used first (up to 5), then this view's commands,
// then the rest in their groups. With one: best match first; a recent command gets a small lift.
// searchOnly commands (tutorial chapters) show only for a query, at most SEARCH_ONLY_LIMIT, after every matching command.
export const SEARCH_ONLY_LIMIT = 2;
export function rankCommands(entries, query, {recent = [], cn = CN} = {}) {
    const q = String(query || '').trim();
    if (!q) entries = entries.filter(entry => !entry.command.searchOnly);
    const recentAt = id => { const at = recent.indexOf(id); return at < 0 ? Infinity : at; };
    if (!q) {
        const recentRows = entries.filter(entry => recentAt(entry.command.id) < 5).sort((a, b) => recentAt(a.command.id) - recentAt(b.command.id))
            .map(entry => ({...entry, section: 'recent', ranges: []}));
        const taken = new Set(recentRows.map(entry => entry.command.id));
        const rest = entries.filter(entry => !taken.has(entry.command.id)).map(entry => ({...entry, section: entry.local ? 'here' : 'group', ranges: []}));
        // Groups keep the order they first appear in; within one, the order commands were registered in.
        const rank = {recent: 0, here: 1, group: 2}, groupAt = new Map();
        for (const entry of [...rest].sort((a, b) => a.order - b.order)) { const name = words(entry.command.group || '', cn); if (!groupAt.has(name)) groupAt.set(name, groupAt.size); }
        const groupOf = entry => entry.section === 'group' ? groupAt.get(words(entry.command.group || '', cn)) : 0;
        return [...recentRows, ...rest.sort((a, b) => rank[a.section] - rank[b.section] || groupOf(a) - groupOf(b) || a.order - b.order)];
    }
    const out = [];
    for (const entry of entries) {
        const hit = scoreCommand(entry.command, q, cn);
        if (!hit) continue;
        const lift = recentAt(entry.command.id) < Infinity ? Math.max(0, 40 - recentAt(entry.command.id) * 5) : 0;
        out.push({...entry, section: 'match', ranges: hit.ranges, score: hit.score + lift + (entry.local ? 10 : 0)});
    }
    out.sort((a, b) => Number(Boolean(a.command.searchOnly)) - Number(Boolean(b.command.searchOnly)) || b.score - a.score || a.order - b.order);
    let extra = 0;
    return out.filter(entry => !entry.command.searchOnly || ++extra <= SEARCH_ONLY_LIMIT);
}

// ── Recently used ───────────────────────────────────────────────────────────────────────────────────────────────
export const RECENT_KEY = 'freevideo.palette.recent';
export function createRecent(storage = null, limit = 8) {
    const read = () => { try { const value = JSON.parse(storage?.getItem(RECENT_KEY) || '[]'); return Array.isArray(value) ? value.filter(id => typeof id === 'string').slice(0, limit) : []; } catch { return []; } };
    let ids = read();
    return {
        list: () => [...ids],
        push(id) { ids = [id, ...ids.filter(other => other !== id)].slice(0, limit); try { storage?.setItem(RECENT_KEY, JSON.stringify(ids)); } catch { /* Kept for this page only. */ } },
    };
}

// ── Registry ────────────────────────────────────────────────────────────────────────────────────────────────────
// Views register under their id; built-ins live under 'global' and give way to any registered command with the
// same id. Commands of the view in front are "here"; another view's commands show only with anyView.
export function createRegistry() {
    const lists = new Map();   // viewId → Set of {commands, builtin, order}
    let order = 0;
    const listeners = new Set();
    const changed = () => { for (const fn of [...listeners]) try { fn(); } catch { /* One listener never stops the others. */ } };
    return {
        register(viewId, commands, {builtin = false} = {}) {
            const id = String(viewId || 'global');
            if (!lists.has(id)) lists.set(id, new Set());
            const entry = {commands: (commands || []).filter(command => command?.id && command.title && typeof command.run === 'function'), builtin, order: order++};
            lists.get(id).add(entry);
            changed();
            return () => { lists.get(id)?.delete(entry); changed(); };
        },
        // The commands to offer in `view`, each once: {command, viewId, local, order}.
        visible(view) {
            const byId = new Map();
            let n = 0;
            for (const [viewId, set] of lists) {
                for (const entry of set) {
                    for (const command of entry.commands) {
                        const local = viewId === view;
                        if (!local && viewId !== 'global' && !command.anyView) continue;
                        try { if (typeof command.when === 'function' && !command.when()) continue; } catch { continue; }
                        const row = {command, viewId, local, builtin: entry.builtin, order: entry.order * 1000 + n++};
                        const before = byId.get(command.id);
                        // Registered beats built-in; the view in front beats another view.
                        if (!before || (before.builtin && !row.builtin) || (!before.local && row.local && before.builtin === row.builtin)) byId.set(command.id, before ? {...row, order: before.order} : row);
                    }
                }
            }
            return [...byId.values()];
        },
        all: () => [...lists.entries()].flatMap(([viewId, set]) => [...set].flatMap(entry => entry.commands.map(command => ({viewId, command})))),
        onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    };
}
export const registry = createRegistry();
export const registerCommands = (viewId, commands) => registry.register(viewId, commands);

// Why a command cannot run now ('' when it can).
export function reasonOf(command, cn = CN) { try { return words(command.why?.() || '', cn); } catch { return ''; } }

// ── The shell, the views and the dialogs the built-in commands reach ──────────────────────────────────────────
const shell = () => globalThis.FreeVideoShell || null;
export const currentView = () => shell()?.view?.() || 'nodes';
const VIEW_ICONS = {studio: 'film', image: 'image', nodes: 'nodes', editor: 'scissors', autopilot: 'chat'};
const VIEW_KEYS = ['studio', 'image', 'nodes', 'editor', 'autopilot'];
// FreeVideo is on screen (a view, or the node view of a FreeVideo workflow), not the user's own ComfyUI workflow.
export const shellActive = () => browser && ['view', 'nodes'].includes(document.documentElement.dataset.fvShell || '');
const viewTab = view => document.querySelector(`dialog.fv-studio[open] .fv-view-switch [data-view="${view}"], .fv-view-navigation .fv-view-switch [data-view="${view}"]`);

export async function goToView(view) {
    if (currentView() === view) return true;
    try { await shell().go(view); return true; } catch { /* No switch on the view in front: the node toolbar's, if it shows. */ }
    const tab = document.querySelector(`.fv-view-navigation:not([hidden]) [data-view="${view}"]`);
    if (tab) { tab.click(); return true; }
    toast(t('Open a FreeVideo workflow first.', '请先打开 FreeVideo 工作流。'), {tone: 'caution'});
    return false;
}
// The library opens from the view's own Creations button when it has one (it carries that view's upscale and
// extend actions); otherwise directly, with a back button naming the view.
export async function openCreations({selectedWork = null} = {}) {
    const view = currentView();
    const own = shell()?.viewDialog?.(view)?.querySelector('[data-tour-id="works-entry"]');
    if (own && !selectedWork && own.getClientRects().length) { own.click(); return; }
    const {openLibrary} = await import('./library.js');
    const name = words(VIEW_NAMES[view] || VIEW_NAMES.studio);
    openLibrary(t, {selectedWork, back: view === 'studio' ? null : t(`Back to ${name}`, `返回${name}`)});
}
const THEME_KEY = 'freevideo.theme';
function setTheme(theme) {
    if (shell()?.setTheme) shell().setTheme(theme); else document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* This page only. */ }
}
const solidNow = () => document.documentElement.dataset.glass === 'solid' || document.documentElement.classList.contains('lite');
function reloadIn(lang) {
    const url = languageUrl(location.href, lang);
    location.assign(url.href);
}

// "Generate" belongs to the view: it is announced, and the view that listens marks it handled. A view that has not
// taken it yet gets its Generate button focused and outlined instead, so Enter starts it; nothing is sent from here.
export const GENERATE_BUTTONS = {studio: '[data-tour-id="generate-btn"]', image: '.fv-image-go', editor: '[data-fv-guide="editor-generate"]'};
function announceGenerate() {
    const view = currentView();
    const detail = {id: 'generate', view, handled: false};
    dispatchEvent(new CustomEvent('freevideo-command', {detail}));
    if (detail.handled) return;
    const button = shell()?.viewDialog?.(view)?.querySelector(GENERATE_BUTTONS[view] || '[data-fv-command="generate"]');
    if (!button || !button.getClientRects().length) { toast(t('This view has nothing to generate yet.', '这个视图里还没有可生成的内容。'), {tone: 'caution'}); return; }
    button.focus({preventScroll: false});
    button.classList.add('fv-cmd-pulse');
    setTimeout(() => button.classList.remove('fv-cmd-pulse'), 1400);
    toast(t('Press Enter to start.', '按回车开始生成。'), {icon: 'spark'});
}
const generateWhy = () => {
    const view = currentView();
    if (view === 'nodes') return ['Use Run in the node view', '节点视图里请用运行按钮'];
    if (view === 'autopilot') return ['Type a message to the assistant', '在对话框里告诉助手要做什么'];
    return '';
};

async function goToLatest() {
    const row = latestFinished();
    if (!row || !await openWork({kind: row.kind, view: row.view, file: row.result?.file, work: row.result?.id || row.result?.work, video: row.result?.video})) await openCreations();
}
async function goToRunning() {
    const row = jobs.active()[0]; if (row) await visitJob(row);
}

const G = {views: ['Views', '视图'], generate: ['Generate', '生成'], works: ['Creations', '作品'], look: ['Appearance', '外观'], help: ['Help', '帮助']};
export function builtinCommands() {
    const viewCommands = VIEW_KEYS.map((view, index) => ({
        id: `view.${view}`, group: G.views, icon: VIEW_ICONS[view], key: `Alt+${index + 1}`,
        title: [`Go to ${words(VIEW_NAMES[view], false)}`, `切换到${/^[A-Za-z]/.test(words(VIEW_NAMES[view], true)) ? ' ' : ''}${words(VIEW_NAMES[view], true)}`],
        use: {studio: ['Make videos from a description', '用描述生成视频'], image: ['Make and edit pictures', '生成和编辑图片'], nodes: ['The workflow behind the views', '各视图背后的工作流'],
            editor: ['Cut, join and add sound', '剪辑、拼接和配声音'], autopilot: ['Let the assistant make a film', '让助手做一部片子']}[view],
        keywords: {studio: ['create', 'video', 'chuangzuo', 'czmb'], image: ['picture', 'tuxiang', 'txst'], nodes: ['workflow', 'graph', 'jiedian', 'jdst'], editor: ['edit', 'cut', 'timeline', 'jianji', 'jjst'], autopilot: ['agent', 'assistant', 'ai', 'quanzidong', 'qzd']}[view],
        when: () => currentView() !== view,
        why: () => view === 'autopilot' && viewTab('autopilot')?.hidden ? ['Turn on the assistant interface in Settings', '先在设置里打开助手接口'] : '',
        run: () => goToView(view),
    }));
    return [
        ...viewCommands,
        {id: 'generate', group: G.generate, icon: 'spark', key: 'Mod+Enter', title: ['Generate in this view', '在当前视图开始生成'], use: ['Start this view\'s generation', '开始这个视图的生成'],
            keywords: ['make', 'run', 'start', 'shengcheng', 'sc'], why: generateWhy, run: announceGenerate},
        {id: 'jobs.running', group: G.generate, icon: 'pulse', title: ['Back to the running generation', '回到进行中的生成'], use: ['Open the generation in progress', '打开正在进行的生成画面'],
            keywords: ['progress', 'task', 'jindu', 'renwu'], when: () => jobs.active().length > 0, run: goToRunning},
        {id: 'jobs.latest', group: G.works, icon: 'clock', title: ['Go to the latest generation', '跳到最近生成'], use: ['Show what was made last', '打开最近一次生成的结果'],
            keywords: ['recent', 'last', 'result', 'zuijin', 'zjsc'], run: goToLatest},
        {id: 'library.open', group: G.works, icon: 'grid', title: ['Open Creations', '打开作品库'], use: ['Every video and picture made here', '生成过的全部视频和图片'],
            keywords: ['library', 'works', 'gallery', 'zuopin', 'zpk'], run: () => openCreations()},
        {id: 'library.trash', group: G.works, icon: 'trash', title: ['Recently deleted', '最近删除'], use: ['Bring back deleted creations', '找回删掉的作品'],
            keywords: ['trash', 'bin', 'restore', 'undo', 'shanchu', 'zjsc'], run: async () => { await openCreations(); dispatchEvent(new CustomEvent('freevideo-command', {detail: {id: 'library.trash', view: 'library', handled: false}})); }},
        {id: 'search.open', group: G.works, icon: 'search', key: 'Mod+Shift+F', title: ['Search creations, images and cast', '搜索作品、图像和角色'], use: ['Find by prompt or file name', '按提示词或文件名查找'],
            keywords: ['find', 'sousuo', 'ss'], run: () => openPalette({scope: 'works'})},
        {id: 'notifications.open', group: G.works, icon: 'bell', title: ['Notifications', '通知'], use: ['Finished, failed and installing', '完成、失败和安装进度'],
            keywords: ['inbox', 'done', 'failed', 'tongzhi', 'tz'], run: () => openNotifications()},
        {id: 'theme.toggle', scope: 'settings', group: G.look, icon: 'moon', title: () => document.documentElement.dataset.theme === 'light' ? ['Dark appearance', '深色外观'] : ['Light appearance', '浅色外观'],
            use: ['Switch between light and dark', '在浅色和深色之间切换'], keywords: ['theme', 'dark', 'light', 'zhuti', 'qianse', 'shense'],
            run: () => setTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light')},
        {id: 'glass.toggle', scope: 'settings', group: G.look, icon: 'layers', title: () => solidNow() ? ['Turn off compatibility view', '关闭兼容界面'] : ['Turn on compatibility view', '开启兼容界面'],
            use: ['For older computers', '适合较旧的电脑'], keywords: ['glass', 'blur', 'lite', 'performance', 'compatibility', 'caizhi', 'boli', 'jianrong', 'ka'],
            run: () => { const solid = solidNow(); if (shell()?.setGlass) shell().setGlass(solid); else document.documentElement.dataset.glass = solid ? '' : 'solid'; }},
        {id: 'language.toggle', scope: 'settings', group: G.look, icon: 'globe', title: CN ? ['English interface', '切换到英文界面'] : ['Chinese interface', '切换到中文界面'],
            use: ['Reloads the page; generations keep running', '会重新载入页面，生成不受影响'], keywords: ['language', 'english', 'chinese', 'yuyan', 'zhongwen', 'yingwen'],
            confirm: ['Reload the page in the other language? Generations keep running.', '重新载入页面并切换语言？正在进行的生成不受影响。'],
            run: () => reloadIn(CN ? 'en' : 'zh')},
        {id: 'shortcuts.open', group: G.help, icon: 'keyboard', key: '?', title: ['Keyboard shortcuts', '快捷键速查'], use: ['Every shortcut of every view', '所有视图的快捷键'],
            keywords: ['keys', 'hotkeys', 'kuaijiejian', 'kjj'], run: () => openShortcuts()},
        {id: 'tutorial.open', group: G.help, icon: 'book', title: ['Tutorial', '教程'], use: ['Learn FreeVideo step by step', '一步步学会 FreeVideo'],
            keywords: ['guide', 'help', 'learn', 'jiaocheng', 'jc'], run: async () => (await import('./tutorial_book.js')).openTutorialBook({chapter: 'quick-start'})},
        {id: 'feedback.open', group: G.help, icon: 'message', title: ['Feedback', '反馈'], use: ['Tell us what to improve', '告诉我们哪里需要改进'],
            keywords: ['report', 'bug', 'issue', 'fankui', 'fk'], run: async () => (await import('./feedback.js')).openFeedback(t)},
        {id: 'settings.open', scope: 'settings', group: G.help, icon: 'gear', title: ['Settings', '设置'], use: ['Models, storage, network and more', '模型、存储、网络等设置'],
            keywords: ['preferences', 'options', 'shezhi', 'sz'], run: async () => (await import('./setup.js')).openSetup()},
    ];
}

// Each finished tutorial chapter, found by its title or another name for it (either language) and opened at that chapter.
export const chapterCommands = (chapters, aliases = {}) => (chapters || []).filter(chapter => chapter?.id && chapter.title?.en && chapter.title?.zh).map(chapter => ({
    id: `tutorial.chapter.${chapter.id}`, searchOnly: true, group: G.help, icon: 'book', title: [chapter.title.en, chapter.title.zh],
    aliases: [...(aliases[chapter.id]?.zh || []), ...(aliases[chapter.id]?.en || [])],
    run: async () => (await import('./tutorial_book.js')).openTutorialBook({chapter: chapter.id}),
}));

// ── The palette ─────────────────────────────────────────────────────────────────────────────────────────────────
// One surface with six scopes (Tab and Shift+Tab move between them): All, Actions, Creations, Prompts, Cast, Settings.
// All without a query: recently used, this view's actions, the newest creations as a grid, then every action in its
// group. All with a query: creations, prompts and cast that match, and the matching actions (first when one starts
// with the query), ending with "make a picture / video of it".
export const SCOPES = Object.freeze([
    {id: 'all', label: ['All', '全部']}, {id: 'actions', label: ['Actions', '操作']}, {id: 'works', label: ['Creations', '作品']},
    {id: 'prompt', label: ['Prompts', '提示词']}, {id: 'cast', label: ['Cast', '角色']}, {id: 'settings', label: ['Settings', '设置']},
]);
export const scopeAfter = (id, step) => SCOPES[(SCOPES.findIndex(scope => scope.id === id) + step + SCOPES.length) % SCOPES.length].id;
let layer = null, ui = null, recent = null;
let scope = 'all', rows = [], active = 0, confirming = null, searchRun = 0, searchTimer = 0, searchGroups = [], searchState = 'idle';
const SECTION_NAMES = {recent: ['Recently used', '最近使用'], match: ['Actions', '操作']};

function build() {
    recent = createRecent((() => { try { return localStorage; } catch { return null; } })());
    // The tutorial's chapters, read when the palette first opens.
    import('./tutorial_book_content.js').then(({TUTORIAL_CHAPTERS, TUTORIAL_ALIASES}) => registry.register('global', chapterCommands(TUTORIAL_CHAPTERS, TUTORIAL_ALIASES), {builtin: true}), () => {});
    layer = createLayer({className: 'fv-palette', label: ['Search and commands', '搜索与命令'], onKey, onClose: () => { clearTimeout(searchTimer); searchRun++; }});
    const d = layer.dialog;
    d.innerHTML = `<div class="fv-pal-field">
        <span class="fv-pal-lead">${glyph('search')}</span>
        <input type="text" spellcheck="false" autocomplete="off" role="combobox" aria-expanded="true" aria-autocomplete="list" aria-controls="fv-pal-list">
        <span class="fv-pal-esc"></span></div>
      <div class="fv-pal-scopes" role="tablist"></div>
      <div class="fv-pal-list" id="fv-pal-list" role="listbox"></div>
      <div class="fv-pal-confirm" hidden role="alertdialog" aria-modal="false"></div>
      <footer class="fv-pal-foot"><span class="fv-pal-about" aria-live="polite"></span><span class="fv-pal-hints"></span></footer>`;
    ui = {input: d.querySelector('input'), esc: d.querySelector('.fv-pal-esc'), scopes: d.querySelector('.fv-pal-scopes'),
        list: d.querySelector('.fv-pal-list'), confirm: d.querySelector('.fv-pal-confirm'), about: d.querySelector('.fv-pal-about'), hints: d.querySelector('.fv-pal-hints')};
    ui.list.setAttribute('aria-label', t('Results', '结果'));
    ui.esc.append(keyChips('Esc'));
    ui.hints.append(hint('Up / Down', t('Move', '选择')), hint('Enter', t('Open', '打开')), hint('Tab', t('Scope', '切换范围')));
    ui.input.placeholder = t('Search actions, creations, prompts and cast', '搜索操作、作品、提示词和角色');
    ui.input.setAttribute('aria-label', ui.input.placeholder);
    for (const item of SCOPES) {
        const chip = document.createElement('button'); chip.type = 'button'; chip.className = 'fv-pal-chip'; chip.tabIndex = -1;
        chip.setAttribute('role', 'tab'); chip.dataset.scope = item.id; chip.textContent = words(item.label);
        Object.assign(chip.dataset, {fvTipName: words(item.label), fvTipUse: words({all: ['Everything at once', '一次看全部'], actions: ['Only things to do', '只看可执行的操作'], works: ['Videos and pictures made here', '生成过的视频和图片'],
            prompt: ['Prompts used before', '用过的描述'], cast: ['Characters, scenes and props', '角色、场景和道具'], settings: ['Appearance, language, settings', '外观、语言和设置']}[item.id]), fvTipKey: formatKeys('Tab')});
        chip.onclick = () => { setScope(item.id); ui.input.focus({preventScroll: true}); };
        ui.scopes.append(chip);
    }
    ui.input.addEventListener('input', () => { active = 0; confirming = null; refresh(); });
    ui.list.addEventListener('pointermove', event => {
        const option = event.target.closest('[role=option]'); if (!option) return;
        const index = Number(option.dataset.index); if (index !== active) { active = index; mark(false); }
    });
    ui.list.addEventListener('click', event => {
        const more = event.target.closest('[data-more]'); if (more) { setScope(more.dataset.more); ui.input.focus({preventScroll: true}); return; }
        const option = event.target.closest('[role=option]'); if (option) { active = Number(option.dataset.index); runActive(); }
    });
    // Clicks in the list keep focus in the field, so typing goes on where it was.
    ui.list.addEventListener('pointerdown', event => { if (event.target.closest('[role=option],[data-more]')) event.preventDefault(); });
    registry.onChange(() => { if (layer.isOpen() && !confirming) refresh(); });
}
function hint(keys, label) { const box = document.createElement('span'); box.className = 'fv-pal-hint'; box.append(keyChips(keys), document.createTextNode(label)); return box; }

function onKey(event) {
    if (confirming) {
        if (event.key === 'Escape') { event.preventDefault(); confirming = null; drawConfirm(); ui.input.focus(); return true; }
        if (event.key === 'Enter' && event.target === ui.input) { event.preventDefault(); accept(); return true; }
        return false;
    }
    const step = n => { event.preventDefault(); if (rows.length) { active = (active + n + rows.length) % rows.length; mark(); } return true; };
    const grid = rows[active]?.grid;
    switch (event.key) {
        case 'Tab': event.preventDefault(); setScope(scopeAfter(scope, event.shiftKey ? -1 : 1)); return true;
        case 'ArrowDown': return grid ? step(gridStep(1)) : step(1);
        case 'ArrowUp': return grid ? step(gridStep(-1)) : step(-1);
        case 'ArrowRight': if (grid) return step(1); return false;
        case 'ArrowLeft': if (grid) return step(-1); return false;
        case 'PageDown': return step(Math.max(1, Math.min(5, rows.length - 1 - active)));
        case 'PageUp': return step(-Math.max(1, Math.min(5, active)));
        case 'Enter': event.preventDefault(); runActive(); return true;
        case 'Escape': event.preventDefault(); closePalette(); return true;
        case '?': if (!ui.input.value && scope === 'all') { event.preventDefault(); closePalette({animate: false}); openShortcuts(); return true; } return false;
        default:
            // Control+N / Control+P move too on a Mac, as in its own lists.
            if (MAC && event.ctrlKey && !event.metaKey && !event.altKey && (event.key === 'n' || event.key === 'p')) return step(event.key === 'n' ? 1 : -1);
            return false;
    }
}
// In the grid of creations, up and down move a whole row; past the edge they leave the grid.
const GRID_COLUMNS = () => (innerWidth < 720 ? 4 : 6);
function gridStep(direction) {
    const row = rows[active], cols = GRID_COLUMNS();
    const first = rows.findIndex(other => other.grid === row.grid), last = rows.findLastIndex(other => other.grid === row.grid);
    const target = active + direction * cols;
    if (target >= first && target <= last) return direction * cols;
    return direction > 0 ? Math.max(1, last + 1 - active) : -Math.max(1, active - first + 1);
}

function setScope(next) {
    scope = next; active = 0;
    for (const chip of ui.scopes.children) { const on = chip.dataset.scope === scope; chip.setAttribute('aria-selected', String(on)); chip.classList.toggle('is-on', on); }
    ui.scopes.querySelector('.is-on')?.scrollIntoView?.({inline: 'nearest', block: 'nearest'});
    refresh();
}

// Commands at once; search results after a short pause, then drawn with them.
function refresh() {
    const q = ui.input.value;
    const wantSearch = ['works', 'prompt', 'cast'].includes(scope) || (scope === 'all');
    clearTimeout(searchTimer);
    if (!wantSearch) { searchGroups = []; searchState = 'idle'; draw(); return; }
    const run = ++searchRun;
    searchState = 'loading';
    searchTimer = setTimeout(async () => {
        try {
            const groups = await searchEverything(q, {limit: scope === 'all' ? (q.trim() ? 4 : 6) : 24});
            if (run !== searchRun) return;
            searchGroups = groups; searchState = 'done';
            // Prompts of older videos may still be arriving: search once more when they have.
            searchSettled().then(more => { if (more && run === searchRun && layer.isOpen()) refresh(); }, () => {});
        } catch {
            if (run !== searchRun) return;
            searchGroups = []; searchState = 'error';
        }
        draw();
    }, q.trim() ? 140 : 0);
    draw();
}

// The two rows that turn the query into a generation (always last among the actions).
function makeOfQuery(q) {
    const text = q.trim(); if (!text) return [];
    const short = text.length > 24 ? text.slice(0, 23) + '…' : text;
    return [
        {id: 'make.image', icon: 'image', title: [`Make a picture of “${short}”`, `用“${short}”作为描述生成图像`], use: ['Puts it in the Images prompt', '填进图像视图的描述框'], key: '', run: () => fillPrompt(text, {view: 'image', fallbackView: 'image'})},
        {id: 'make.video', icon: 'film', title: [`Make a video of “${short}”`, `用“${short}”作为描述生成视频`], use: ['Puts it in the Create prompt', '填进创作面板的描述框'], key: '', run: () => fillPrompt(text, {view: 'studio', fallbackView: 'studio'})},
    ].map((command, order) => ({command, section: 'make', ranges: [], order: 1e6 + order}));
}

function draw() {
    const q = ui.input.value.trim(), view = currentView();
    rows = [];
    const fragment = document.createDocumentFragment();
    let grids = 0;
    const heading = (text, more = null) => {
        const head = document.createElement('div'); head.className = 'fv-pal-group'; head.setAttribute('role', 'presentation');
        const label = document.createElement('span'); label.textContent = text; head.append(label);
        if (more) { const link = document.createElement('button'); link.type = 'button'; link.className = 'fv-pal-more'; link.tabIndex = -1; link.dataset.more = more.scope; link.textContent = more.label; head.append(link); }
        fragment.append(head);
    };
    const visible = registry.visible(view).filter(entry => scope !== 'settings' || entry.command.scope === 'settings');
    const ranked = ['all', 'actions', 'settings'].includes(scope) ? rankCommands(visible, q, {recent: recent.list()}) : [];
    const group = id => searchGroups.find(item => item.id === id);
    const addCommands = (list, label) => {
        let section = null, name = null;
        for (const entry of list) {
            const text = label || (entry.section === 'recent' ? words(SECTION_NAMES.recent) : entry.section === 'here' ? words(VIEW_NAMES[view] || VIEW_NAMES.studio)
                : entry.section === 'match' || entry.section === 'make' ? words(SECTION_NAMES.match) : words(entry.command.group || ['Other', '其他']));
            if (entry.section !== section && !(entry.section === 'make' && section === 'match') || text !== name) { if (text !== name) heading(text); section = entry.section; name = text; }
            fragment.append(commandOption(entry, rows.length)); rows.push({type: 'command', entry});
        }
    };
    const addResults = (id, {limit = Infinity, label = null, gridded = false} = {}) => {
        const found = group(id); if (!found?.items.length) return 0;
        const items = found.items.slice(0, limit);
        const more = scope === 'all' && found.total > items.length ? {scope: id === 'prompt' ? 'prompt' : id, label: t(`See all ${found.total}`, `查看全部 ${found.total} 项`)} : null;
        heading(label || words(found.label), more);
        if (gridded) {
            const grid = document.createElement('div'); grid.className = 'fv-pal-grid'; grid.setAttribute('role', 'group'); const gridId = ++grids;
            for (const item of items) { grid.append(tileOption(item, rows.length)); rows.push({type: 'result', item, grid: gridId}); }
            fragment.append(grid);
        } else for (const item of items) { fragment.append(resultOption(item, rows.length)); rows.push({type: 'result', item}); }
        return items.length;
    };

    if (scope === 'all' && !q) {
        const top = ranked.filter(entry => entry.section === 'recent' || entry.section === 'here');
        addCommands(top);
        addResults('works', {gridded: true, limit: GRID_COLUMNS()});
        addCommands(ranked.filter(entry => entry.section === 'group'));
    } else if (scope === 'all') {
        const best = ranked[0];
        const actionsFirst = best && scoreCommand(best.command, q).score >= 1150;
        // One group of actions: first when one of them starts with the query, else after what was found.
        // Chapters come after the actions, never in place of one.
        const kept = new Set(ranked.filter(entry => !entry.command.searchOnly).slice(0, actionsFirst ? 6 : 8));
        const actions = [...ranked.filter(entry => entry.command.searchOnly || kept.has(entry)), ...makeOfQuery(q)];
        if (actionsFirst) addCommands(actions, words(SECTION_NAMES.match));
        addResults('works', {limit: 4});
        addResults('prompt', {limit: 3});
        addResults('cast', {limit: 3});
        if (!actionsFirst) addCommands(actions, words(SECTION_NAMES.match));
    } else if (scope === 'actions' || scope === 'settings') {
        addCommands(scope === 'actions' && q ? [...ranked, ...makeOfQuery(q)] : ranked, q ? words(SECTION_NAMES.match) : null);
    } else if (scope === 'works') {
        addResults('works', {gridded: true});
    } else addResults(scope);

    const found = rows.some(row => row.type === 'result');
    if (searchState === 'loading' && !found && scope !== 'actions' && scope !== 'settings') {
        const wait = document.createElement('div'); wait.className = 'fv-pal-wait'; wait.setAttribute('role', 'status');
        wait.innerHTML = '<i></i><i></i><i></i>'; wait.setAttribute('aria-label', t('Searching', '正在搜索'));
        fragment.append(wait);
    }
    // Nothing (or only "make it") found: say so, and keep the two make rows below.
    const onlyMake = rows.length && rows.every(row => row.entry?.section === 'make');
    if ((!rows.length || onlyMake) && searchState !== 'loading') {
        const empty = document.createElement('div'); empty.className = 'fv-pal-empty';
        const art = document.createElement('span'); art.className = 'fv-pal-empty-art'; art.innerHTML = glyph('search', 24);
        const head = document.createElement('b');
        const line = document.createElement('span');
        if (searchState === 'error' && scope !== 'actions') { head.textContent = t('Could not search the creations', '暂时无法搜索作品'); line.textContent = t('Try again in a moment.', '请稍后再试。'); }
        else if (q) { head.textContent = t(`No results for “${q}”`, `没有找到“${q}”`); line.textContent = onlyMake ? t('Try another word, or use it as a prompt.', '换个词，或直接用它作为描述生成。') : t('Try another word.', '换个词试试。'); }
        else { head.textContent = t('Nothing here yet', '这里还没有内容'); line.textContent = {works: t('Videos and pictures show here once made.', '生成的视频和图片会出现在这里。'), prompt: t('Prompts show here once used.', '用过的描述会出现在这里。'), cast: t('Characters show here once created.', '创建的角色会出现在这里。')}[scope] || ''; }
        empty.append(art, head, line);
        if (onlyMake) ui.list.replaceChildren(empty, fragment); else { ui.list.replaceChildren(empty); }
        active = Math.min(active, Math.max(0, rows.length - 1)); mark(false);
        return;
    }
    ui.list.replaceChildren(fragment);
    active = Math.min(active, Math.max(0, rows.length - 1));
    mark(false);
}

function highlighted(text, ranges, className = 'fv-pal-title') {
    const box = document.createElement('span'); box.className = className;
    let at = 0;
    for (const [start, end] of ranges || []) {
        if (start > at) box.append(text.slice(at, start));
        const mark = document.createElement('mark'); mark.textContent = text.slice(start, end); box.append(mark);
        at = end;
    }
    if (at < text.length) box.append(text.slice(at));
    return box;
}
const goMark = () => { const go = document.createElement('span'); go.className = 'fv-pal-go'; go.innerHTML = glyph('arrow'); return go; };
function commandOption(entry, index) {
    const {command} = entry;
    const option = document.createElement('div'); option.className = 'fv-pal-option'; option.id = `fv-pal-opt-${index}`;
    option.setAttribute('role', 'option'); option.dataset.index = String(index);
    const why = reasonOf(command);
    if (why) option.setAttribute('aria-disabled', 'true');
    // Its hover tip: name, what it does, its key; a greyed one says why and how to make it available.
    option.dataset.fvTipName = words(command.title);
    if (command.use) option.dataset.fvTipUse = words(command.use);
    if (command.key) option.dataset.fvTipKey = formatKeys(command.key);
    if (why) option.dataset.fvTipWhy = why;
    const icon = document.createElement('span'); icon.className = 'fv-pal-icon'; icon.innerHTML = glyph(command.icon || 'arrow');
    option.append(icon, highlighted(words(command.title), entry.ranges));
    const tail = document.createElement('span'); tail.className = 'fv-pal-tail';
    if (command.key) tail.append(keyChips(command.key)); else tail.append(goMark());
    option.append(tail);
    return option;
}
function thumbOf(item, className) {
    const thumb = document.createElement('span'); thumb.className = className + (item.round ? ' is-round' : '');
    if (item.thumb) { const img = document.createElement('img'); img.alt = ''; img.loading = 'lazy'; img.decoding = 'async'; img.draggable = false; img.src = item.thumb; img.onerror = () => { img.remove(); thumb.innerHTML = glyph(item.icon || 'image'); }; thumb.append(img); }
    else thumb.innerHTML = glyph(item.icon || 'image');
    return thumb;
}
const resultTip = (node, item) => Object.assign(node.dataset, {fvTipName: item.title.length > 40 ? item.title.slice(0, 39) + '…' : item.title, fvTipUse: words(item.use || ''), fvTipKey: formatKeys('Enter')});
function resultOption(item, index) {
    const option = document.createElement('div'); option.className = 'fv-pal-option is-two'; option.id = `fv-pal-opt-${index}`;
    option.setAttribute('role', 'option'); option.dataset.index = String(index); resultTip(option, item);
    const text = document.createElement('span'); text.className = 'fv-pal-text';
    text.append(highlighted(item.title, item.ranges));
    if (item.sub) { const sub = document.createElement('span'); sub.className = 'fv-pal-sub'; sub.textContent = item.sub; text.append(sub); }
    const tail = document.createElement('span'); tail.className = 'fv-pal-tail'; tail.append(goMark());
    option.append(thumbOf(item, 'fv-pal-thumb'), text, tail);
    return option;
}
function tileOption(item, index) {
    const tile = document.createElement('div'); tile.className = 'fv-pal-tile'; tile.id = `fv-pal-opt-${index}`;
    tile.setAttribute('role', 'option'); tile.dataset.index = String(index); tile.setAttribute('aria-label', item.title); resultTip(tile, item);
    tile.append(thumbOf(item, 'fv-pal-tile-pic'));
    if (item.badge) { const badge = document.createElement('span'); badge.className = 'fv-pal-badge'; badge.textContent = item.badge; tile.append(badge); }
    return tile;
}

function mark(scroll = true) {
    const options = ui.list.querySelectorAll('[role=option]');
    options.forEach((option, index) => {
        const on = index === active;
        option.setAttribute('aria-selected', String(on));
        if (on && scroll) option.scrollIntoView({block: 'nearest'});
    });
    const row = rows[active];
    if (row) ui.input.setAttribute('aria-activedescendant', `fv-pal-opt-${active}`); else ui.input.removeAttribute('aria-activedescendant');
    // The line at the bottom says what the marked row does, or why it cannot run now; for a tile, what it is.
    ui.about.classList.remove('is-why');
    if (!row) { ui.about.textContent = ''; return; }
    if (row.type === 'command') {
        const why = reasonOf(row.entry.command);
        ui.about.textContent = why || words(row.entry.command.use || '');
        ui.about.classList.toggle('is-why', Boolean(why));
    } else ui.about.textContent = row.grid ? `${row.item.title} · ${row.item.sub}` : words(row.item.use || '');
}

function runActive() {
    const row = rows[active]; if (!row) return;
    if (row.type === 'result') { closePalette({animate: false}); queueMicrotask(() => { try { row.item.open(); } catch (error) { console.warn('FreeVideo search:', error); } }); return; }
    const {command} = row.entry;
    const why = reasonOf(command);
    if (why) {
        // Said again, with a small nudge, so the press is not lost.
        ui.about.textContent = why; ui.about.classList.add('is-why');
        ui.list.querySelector(`[data-index="${active}"]`)?.animate?.([{transform: 'translateX(0)'}, {transform: 'translateX(3px)'}, {transform: 'translateX(0)'}], {duration: 180, easing: 'cubic-bezier(.2,.8,.2,1)'});
        return;
    }
    if (command.confirm) { confirming = command; drawConfirm(); return; }
    execute(command);
}
function execute(command) {
    if (!command.id.startsWith('make.')) recent.push(command.id);
    closePalette({animate: false});
    // The palette is gone and focus is back where it was before the action runs.
    queueMicrotask(async () => {
        try { await command.run({view: currentView(), source: 'palette'}); }
        catch (error) { console.warn('FreeVideo command:', error); commandFailed(command, error); }
    });
}
// A known cause (offline, an extension, FreeVideo's own sentence) is the whole notice. An unknown one or a server
// error names the command and offers a report that carries the raw error and the command id.
export function commandFailure(command, error, report = openIssue) {
    const name = words(command.title);
    const reason = userMessage(error, t, '');
    const server = /\bHTTP\s*\d{3}\b/.test(String(error?.message ?? error ?? '')) || Number.isInteger(error?.status);
    if (reason && !server) return [reason, {tone: 'fail'}];
    // The pill holds one short line beside the button; the server's sentence goes in the detail (the bell keeps both).
    return [t(`Failed: “${name}”`, `“${name}”没有完成`), {tone: 'fail', ...(reason ? {detail: reason} : {}), action: {
        label: t('Report', '提交问题'), use: t('Open the feedback form with this error filled in', '打开反馈表，已填好这条错误'),
        run: () => report(t, {where: ['Command palette', '命令面板'], message: `${name}: ${String(error?.message ?? error ?? '')}`, kind: 'command', facts: {command: command.id}})}}];
}
function commandFailed(command, error) { const notice = commandFailure(command, error); if (notice[1].action) preloadIssue(); toast(...notice); }
function accept() { const command = confirming; confirming = null; drawConfirm(); if (command) execute(command); }
function drawConfirm() {
    ui.confirm.hidden = !confirming; ui.list.hidden = Boolean(confirming); ui.scopes.hidden = Boolean(confirming);
    if (!confirming) { ui.confirm.replaceChildren(); return; }
    const message = document.createElement('p'); message.className = 'fv-pal-ask'; message.id = 'fv-pal-ask'; message.textContent = words(confirming.confirm);
    const actions = document.createElement('div'); actions.className = 'fv-pal-ask-actions';
    const no = document.createElement('button'); no.type = 'button'; no.className = 'fv-pal-btn'; no.textContent = t('Cancel', '取消');
    Object.assign(no.dataset, {fvTipName: t('Cancel', '取消'), fvTipUse: t('Back to the list', '回到列表'), fvTipKey: 'Esc'});
    const yes = document.createElement('button'); yes.type = 'button'; yes.className = 'fv-pal-btn is-primary'; yes.textContent = words(confirming.title);
    Object.assign(yes.dataset, {fvTipName: words(confirming.title), fvTipUse: words(confirming.use || ''), fvTipKey: formatKeys('Enter')});
    no.onclick = () => { confirming = null; drawConfirm(); ui.input.focus(); };
    yes.onclick = accept;
    actions.append(no, yes);
    ui.confirm.setAttribute('aria-labelledby', 'fv-pal-ask');
    ui.confirm.replaceChildren(message, actions);
    ui.about.textContent = ''; ui.about.classList.remove('is-why');
    yes.focus({preventScroll: true});
}

export function openPalette({scope: wanted = 'all', query = ''} = {}) {
    if (!browser) return;
    if (!layer) build();
    if (shortcutsOpen()) closeShortcuts();
    confirming = null; drawConfirm();
    ui.input.value = query;
    setScope(wanted);
    layer.open();
    warmSearch();
    requestAnimationFrame(() => { ui.input.focus({preventScroll: true}); ui.input.select(); });
}
export function closePalette(options) { layer?.close(options); }
export const paletteOpen = () => Boolean(layer?.isOpen());
export function togglePalette(options = {}) {
    const wanted = options.scope || 'all';
    if (paletteOpen() && wanted === scope) closePalette();
    else if (paletteOpen()) { ui.input.value = ''; setScope(wanted); }
    else openPalette(options);
}

// The search button for the top bar of every view (shell.js puts it there).
export function paletteButton(translate = t, className = 'fv-cmd-entry') {
    const button = document.createElement('button'); button.type = 'button'; button.className = className;
    button.innerHTML = glyph('search');
    const name = translate('Search and commands', '搜索与命令');
    button.setAttribute('aria-label', name); button.setAttribute('aria-keyshortcuts', MAC ? 'Meta+K' : 'Control+K');
    Object.assign(button.dataset, {fvTipName: name, fvTipUse: translate('Search works and run any action', '搜索作品、提示词并执行操作'), fvTipKey: formatKeys('Mod+K')});
    button.onclick = () => togglePalette();
    return button;
}

// ── Keys that work in every view ───────────────────────────────────────────────────────────────────────────────
// Ctrl/⌘+K the palette, Ctrl/⌘+Shift+F search, ? the shortcut sheet (not while typing), Alt+1…5 the views.
export function typing(target) {
    if (typeof Element === 'undefined' || !(target instanceof Element)) return false;
    return Boolean(target.closest('input:not([type=checkbox],[type=radio],[type=range],[type=button],[type=submit]),textarea,select,[contenteditable=""],[contenteditable=true]')) || target.isContentEditable === true;
}
export function globalAction(event, {mac = MAC, active = true, isTyping = typing(event.target)} = {}) {
    if (!active) return null;
    const mod = mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
    const code = event.code || '', key = String(event.key || '').toLowerCase();
    if (mod && !event.altKey && !event.shiftKey && (code === 'KeyK' || key === 'k')) return {type: 'palette'};
    if (mod && event.shiftKey && !event.altKey && (code === 'KeyF' || key === 'f')) return {type: 'search'};
    if (event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey && /^Digit[1-5]$/.test(code) && !(mac && isTyping)) return {type: 'view', view: VIEW_KEYS[Number(code.slice(5)) - 1]};
    if (key === '?' && !event.ctrlKey && !event.metaKey && !event.altKey && !isTyping) return {type: 'shortcuts'};
    return null;
}
function onGlobalKey(event) {
    if (event.repeat || event.isComposing) return;
    const inLayer = event.target instanceof Element && event.target.closest('[data-fv-command-layer]');
    const action = globalAction(event, {active: shellActive() || paletteOpen() || shortcutsOpen()});
    if (!action) return;
    // The command layer's own fields handle ? themselves (it may be typed into a search).
    if (action.type === 'shortcuts' && inLayer) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (action.type === 'palette') togglePalette();
    else if (action.type === 'search') togglePalette({scope: 'works'});
    else if (action.type === 'shortcuts') { if (shortcutsOpen()) closeShortcuts(); else { closePalette({animate: false}); openShortcuts(); } }
    else if (action.type === 'view') { closePalette({animate: false}); closeShortcuts(); const command = registry.visible(currentView()).find(row => row.command.id === `view.${action.view}`)?.command; if (!command || !reasonOf(command)) void goToView(action.view); else toast(reasonOf(command), {tone: 'caution'}); }
}

export function installPalette() {
    if (!browser || globalThis.FreeVideoCommands?.installed) return globalThis.FreeVideoCommands;
    // A stored appearance comes back before any view opens.
    try { const theme = localStorage.getItem(THEME_KEY); if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme; } catch { /* Default appearance. */ }
    registry.register('global', builtinCommands(), {builtin: true});
    // Before any view's own key gate (those are added when a view opens), so these keys reach here first.
    addEventListener('keydown', onGlobalKey, true);
    globalThis.FreeVideoCommands = {installed: true, registerCommands, openPalette, closePalette, togglePalette, paletteButton, registry};
    return globalThis.FreeVideoCommands;
}

// The rest of the command layer (shortcuts, notifications, search, menus) installs itself on import.
if (browser) installPalette();
