// The one top bar of every view (the spec: design-jia-glass/work-glob/TOPBAR.md). Every view dialog builds its own
// `.fv-header` with the logo, the view switch (view_navigation.js viewSwitch) and its entries; this module takes that
// header over the moment it is in the page and rebuilds it to the same parts, in the same order, from the same code:
//   [logo]            [the five view tabs, centred on the window]            [status · search | works phone version tutorial feedback settings]
// So the bar is identical in all five views and does not move when the view changes (the shell holds it as its own
// layer during the cross-fade). The node view's bar is built with the same parts (installNavigation).
// How far the right side folds is measured, never guessed from breakpoints (data-fv-fit on the header):
//   0 full · 1 links become icons · 2 the status capsule keeps only its icon and number · 3 phone: the tabs become a
//   picker for the current view, the links and search go into one menu button.
// The tabs stay on the window's centre at every level: the outer columns are equal (minmax(0,1fr) auto minmax(0,1fr)).
import { api } from '../../scripts/api.js';
import { createLanAccess } from './lan_access.js';
import { openSetup } from './setup.js';
import { feedbackButton, guideButton } from './feedback.js';
import { createVersionInfo } from './updates.js';
import { paletteButton, openCreations } from './palette.js';
import { wordmark } from './wordmark.js';
import { jobs, ACTIVE } from './task_store.js';
import { createAgentTasks } from './agent_tasks.js';
import { title as agentTitle, failureReason } from './agent_activity.js';
import { isNetworkTypeError, connectionLostText } from './browser_extension.js';
import { onNotices, mountNotices, markNoticesRead, noteFromAgent, addAgentNote } from './notifications.js';
import { CN, t0, words, capsuleState, capsuleWords, machineLine, machineFigures, isFailure, FIGURES_KEY, figuresShown } from './topbar_state.js';
export { CN, words, capsuleState, machineLine };

const browser = typeof document !== 'undefined';

if (browser) {
    const sheet = document.createElement('link');
    sheet.rel = 'stylesheet'; sheet.href = new URL('./topbar.css', import.meta.url).href;
    document.head.append(sheet);
}

// ── Icons: drawn as CSS masks, so a component that rewrites its own text (version, phone) keeps its icon ──
const PATHS = {
    phone: '<rect x="7" y="3" width="10" height="18" rx="2.5"/><path d="M11 17.5h2"/>',
    tag: '<path d="M4 4h7.5l8.5 8.5-7.5 7.5L4 11.5z"/><circle cx="8.5" cy="8.5" r="1.3"/>',
    book: '<path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H19v15H7.5A2.5 2.5 0 0 0 5 20.5z"/><path d="M5 20.5A2.5 2.5 0 0 1 7.5 18H19v3H7.5"/>',
    chat: '<path d="M4.5 6.5A2.5 2.5 0 0 1 7 4h10a2.5 2.5 0 0 1 2.5 2.5v7A2.5 2.5 0 0 1 17 16h-5l-4.5 4v-4H7a2.5 2.5 0 0 1-2.5-2.5z"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M12 3.5v2.2M12 18.3v2.2M20.5 12h-2.2M5.7 12H3.5M18 6l-1.6 1.6M7.6 16.4 6 18M18 18l-1.6-1.6M7.6 7.6 6 6"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    menu: '<path d="M4.5 7h15M4.5 12h15M4.5 17h15"/>',
    chevron: '<path d="m6.5 9.5 5.5 5.5 5.5-5.5"/>',
    warn: '<path d="M12 4 21 19.5H3z"/><path d="M12 10v4M12 16.8v.01"/>',
    close: '<path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>',
    studio: '<rect x="3.5" y="5" width="17" height="14" rx="3"/><path d="M8 5v14M16 5v14M3.5 9.5H8M3.5 14.5H8M16 9.5h4.5M16 14.5h4.5"/>',
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="3"/><circle cx="9" cy="10" r="1.6"/><path d="m4 17 5-4.5 4 3.5 2.5-2 4.5 3.5"/>',
    nodes: '<rect x="3.5" y="4" width="7" height="6" rx="2"/><rect x="13.5" y="14" width="7" height="6" rx="2"/><path d="M10.5 7h3a3 3 0 0 1 3 3v4"/>',
    editor: '<circle cx="6.5" cy="7" r="2.5"/><circle cx="6.5" cy="17" r="2.5"/><path d="M8.6 8.4 19.5 18M8.6 15.6 19.5 6"/>',
    autopilot: '<path d="M12 3.5 13.9 9l5.6 1.9-5.6 1.9L12 18.5l-1.9-5.7L4.5 11 10.1 9z"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2"/>',
    works: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.6"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.6"/>',
    first: '<path d="M6 5.5h12M12 19V9.5M7.5 14l4.5-4.5 4.5 4.5"/>',
};
export const iconURL = name => `url("data:image/svg+xml,${encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'>${PATHS[name]}</svg>`)}")`;
function withIcon(element, name) { element.classList.add('fv-tb-ico'); element.style.setProperty('--fv-tb-icon', iconURL(name)); return element; }

const el = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text != null) node.textContent = text; return node; };
function tip(element, name, use, key = '') {
    element.dataset.fvTipName = name; element.dataset.fvTipUse = use;
    if (key) element.dataset.fvTipKey = key; else delete element.dataset.fvTipKey;
    element.removeAttribute('title');
}

// ── The status capsule (TOPBAR.md §4; its state in topbar_state.js) ──
// The AI assistant's tasks count with the others: a task row each, and the capsule's number.
const agentTasks = createAgentTasks(api);
const allJobs = () => [...jobs.list(), ...agentTasks.rows()];
let online = true;
const capsules = new Set();
if (browser) {
    try {
        api.addEventListener?.('reconnecting', () => { online = false; syncCapsules(); });
        api.addEventListener?.('reconnected', () => { online = true; syncCapsules(); });
    } catch { /* No socket events here: the capsule assumes the engine is there. */ }
}
const RING = 2 * Math.PI * 6.5;
function ringSVG() {
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('class', 'fv-tb-ring'); svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('aria-hidden', 'true');
    for (const cls of ['bg', 'fg']) { const c = document.createElementNS(ns, 'circle'); c.setAttribute('class', cls); c.setAttribute('cx', '8'); c.setAttribute('cy', '8'); c.setAttribute('r', '6.5'); svg.append(c); }
    svg.lastChild.setAttribute('stroke-dasharray', RING.toFixed(2));
    return svg;
}
// The notification centre's unread count (palette's notifications.js), when it is there.
let notices = null, promptJob = null;

// The first time a notice is unread, a one-time hint under the capsule says the notices are there (Parker N2). It goes
// for good once "Got it" or the capsule is pressed (kept in this browser).
const HINT_KEY = 'freevideo.topbar.noticeHint';
let hint = null;
const hintSeen = () => { try { return localStorage.getItem(HINT_KEY) === '1'; } catch { return true; } };
function dropHint() { try { localStorage.setItem(HINT_KEY, '1'); } catch { /* This page only. */ } for (const bar of document.querySelectorAll('.fv-tb-hinting')) bar.classList.remove('fv-tb-hinting'); if (hint) { try { hint.hidePopover?.(); } catch { /* Hidden. */ } hint.remove(); hint = null; } }
function frontCapsule() {
    return [...capsules].filter(capsule => capsule.isConnected && capsule.getClientRects().length && capsule.closest('dialog[open], .fv-view-navigation:not([hidden])')).at(-1) || null;
}
// It sits inside the bar itself, beside the capsule over the bar's own entries (never over a view), pointing at the
// capsule; it fades after 6 s untouched, and a press anywhere or a view change also counts as seen. It never appears
// while something generates or the floating task window shows, nor at the phone level (no room on the bar).
function noticeHint() {
    if (!browser || hintSeen() || hint) return;
    // Busy (something generates, the floating window shows): ask again in a while, as long as a notice is unread.
    if (jobs.active().length || document.querySelector('#fvTaskFloat:popover-open')) { if (notices?.unread) setTimeout(noticeHint, 3000); return; }
    const capsule = frontCapsule(); if (!capsule) return;
    const bar = capsule.closest('.fv-topbar');
    if (!bar || bar.dataset.fvFit === '3') return;
    const t = capsule.fvCapsule.t;
    hint = el('div', 'fv-tb-hint'); hint.setAttribute('role', 'status'); hint.dataset.fvTipScope = '';
    if (hint.showPopover) hint.popover = 'manual';
    const ok = el('button', 'fv-tb-hint-ok', t('Got it', '知道了')); ok.type = 'button'; ok.onclick = dropHint;
    tip(ok, t('Got it', '知道了'), t('Do not show this again', '以后不再显示'));
    hint.append(el('span', 'fv-tb-hint-dot'), el('span', '', t('Notices are here', '通知在这里')), ok);
    (capsule.closest('dialog[open]') || document.body).append(hint);
    try { hint.showPopover?.(); } catch { /* Shown as a fixed element. */ }
    const r = capsule.getBoundingClientRect(), b = bar.getBoundingClientRect(), w = hint.offsetWidth, h = hint.offsetHeight;
    const left = r.right + 8;
    // No room to the right of the capsule inside the window: it waits for a wider window rather than cover a view.
    if (left + w > innerWidth - 8 || h > b.height) { hint.remove(); hint = null; return; }
    hint.style.left = `${Math.round(left)}px`;
    hint.style.top = `${Math.round(r.top + (r.height - h) / 2)}px`;
    // The entries it sits over step aside while it shows (no half-covered word peeks out beside it).
    bar.classList.add('fv-tb-hinting');
    const done = () => { bar.classList.remove('fv-tb-hinting'); if (!hint) return; const shown = hint; hint = null; dropHint(); shown.remove(); };
    const fade = setTimeout(() => {
        if (!hint) return;
        const shown = hint;
        if (shown.animate && !globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches) shown.animate([{opacity: 1}, {opacity: 0}], {duration: 200, fill: 'forwards'}).finished.then(done, done);
        else done();
    }, 6000);
    hint.addEventListener('pointerenter', () => clearTimeout(fade));
    const anyPress = event => { if (!hint?.contains(event.target)) { clearTimeout(fade); done(); } };
    document.addEventListener('pointerdown', anyPress, {capture: true, once: true});
    globalThis.addEventListener?.('freevideo-view', () => { clearTimeout(fade); done(); }, {once: true});
}
// A task started, added to the queue or finished: the capsule glows once (a soft blue ring that fades out, no bounce)
// and its count rises into place. Nothing moves with reduced motion; nothing runs while the bar is idle.
function bump(capsule) {
    if (globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    clearTimeout(capsule.fvBumpTimer);
    capsule.classList.remove('is-bump'); void capsule.offsetWidth; capsule.classList.add('is-bump');
    capsule.fvBumpTimer = setTimeout(() => capsule.classList.remove('is-bump'), 1000);
}
function renderCapsule(capsule) {
    const before = capsule.isConnected ? capsule.getBoundingClientRect().width : 0;
    const list = allJobs(), active = list.filter(row => ACTIVE.has(row.status)).length, finished = list.filter(row => row.status === 'done').length;
    const seen = capsule.fvCapsule.seen;
    if (seen && (active > seen.active || finished > seen.finished)) bump(capsule);
    capsule.fvCapsule.seen = {active, finished};
    const {t} = capsule.fvCapsule, state = capsuleState(list, online, promptJob ? {...(notices || {}), prompt: promptJob} : notices);
    const [word, number] = capsuleWords(t, state);
    const figures = state.kind === 'idle' && showFigures() ? machineFigures(machine) : null;
    capsule.dataset.state = state.kind;
    capsule.classList.toggle('is-warn', state.kind === 'off');
    capsule.classList.toggle('has-n', state.count > 0);
    capsule.classList.toggle('has-figures', !!figures);
    const {dot, ring, text, count} = capsule.fvCapsule;
    const ringed = state.kind === 'run' || state.kind === 'dl' || state.kind === 'wait' || state.kind === 'prompt';
    dot.hidden = ringed; ring.style.display = ringed ? '' : 'none';
    dot.className = `fv-tb-dot${state.kind === 'off' ? ' is-off' : state.warn ? ' is-warn' : ''}`;
    if (ringed) ring.lastChild.setAttribute('stroke-dashoffset', (RING * (1 - (state.percent || 0) / 100)).toFixed(2));
    // Figures sit in fixed-width digit slots (tabular digits), so 9 % → 10 % or a new temperature never shifts the words.
    const digits = (value, slot, warn = false) => { const node = el('b', `fv-tb-num${warn ? ' is-warn' : ''}`, String(value)); node.style.minWidth = `${slot}ch`; return node; };
    let said;
    if (figures) {
        const parts = [];
        if (figures.memory != null) parts.push(el('span', 'fv-tb-wide', `${t('Video memory', '显存')} `), digits(figures.memory, 2, figures.full), el('span', '', '%'));
        // At level 2 only the first figure stays (TOPBAR §3: icon and number); the temperature goes when memory is there.
        const extra = figures.memory != null ? ' fv-tb-more' : '';
        if (figures.memory != null && figures.temperature != null) parts.push(el('span', 'fv-tb-sepdot fv-tb-more', ' · '));
        if (figures.temperature != null) { const value = digits(figures.temperature, 2, figures.hot); value.className += extra; parts.push(value, el('span', extra.trim(), ' °C')); }
        text.replaceChildren(...parts);
        said = machineLine(machine, t);
    } else if (number) {
        const percent = String(number).endsWith('%');
        text.replaceChildren(el('span', 'fv-tb-wide', `${word} `), digits(String(number).replace('%', ''), percent ? 2 : 1), ...(percent ? [el('b', 'fv-tb-pct', '%')] : []));
        said = `${word} ${number}`;
    } else {
        text.replaceChildren(el('span', 'fv-tb-wide', word));
        said = word;
    }
    count.textContent = state.count > 99 ? '99+' : String(state.count); count.hidden = !state.count;
    count.classList.remove('is-warn');
    const [name] = words(t).status;
    capsule.setAttribute('aria-label', `${name}: ${said}${state.count ? ` (${state.count})` : ''}`);
    // As wide as what it now says (on the 4 px grid); it is the first item on the right, so its right edge stays and it
    // grows or shrinks leftwards, over 150 ms (at once with reduced motion). The bar refits if the right side no longer fits.
    const target = fitCapsuleWidth(capsule);
    if (before > 0 && target > 0 && Math.abs(before - target) > 0.5 && capsule.animate && !globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
        capsule.animate([{width: `${before}px`}, {width: `${target}px`}], {duration: 150, easing: 'cubic-bezier(.2,.8,.2,1)'});
    }
}
// Content width rounded up to the 4 px grid, set as the capsule's width (CSS rules decide it at the phone level).
function fitCapsuleWidth(capsule) {
    capsule.style.removeProperty('width'); capsule.style.removeProperty('min-width');
    const natural = capsule.isConnected ? capsule.getBoundingClientRect().width : 0;
    if (natural > 0) capsule.style.width = `${Math.ceil(natural / 4 - 0.01) * 4}px`;
    return natural > 0 ? Math.ceil(natural / 4 - 0.01) * 4 : 0;
}
function syncCapsules() { for (const capsule of [...capsules]) { if (capsule.isConnected) renderCapsule(capsule); } }
if (browser) { jobs.subscribe(() => syncCapsules()); agentTasks.subscribe(() => { noteAgentFailures(); syncCapsules(); }); void agentTasks.refresh(); }
// A failed assistant task becomes a notice (its name and cause), the same as a failed generation: it counts on the capsule.
function noteAgentFailures() {
    const t = t0;
    for (const job of agentTasks.failed()) addAgentNote(noteFromAgent(job, {title: agentTitle(job, t), reason: failureReason(job, t)}));
}
// Subscribed once everything it calls is defined (it answers at once with the current state).
// A description being written from a picture or video (prompt_enhance.js): the capsule says so while it runs.
if (browser) globalThis.addEventListener('freevideo-prompt-job', event => { promptJob = event.detail?.active ? {fraction: event.detail.fraction} : null; syncCapsules(); });
if (browser) onNotices(state => { const before = notices?.unread || 0; notices = state; syncCapsules(); if (!before && state.unread) noticeHint(); });

// The engine's monitor reading (the source of the old status capsule: video memory, temperature, disk): one shared
// read for every bar, at most every 2 s while a bar is on the page, every 10 s in a hidden tab.
let machine = null, machineAt = 0, machineTimer = 0, machineReading = false;
async function readMachine() {
    if (Date.now() - machineAt < 2000 || machineReading) return machine;
    machineReading = true; machineAt = Date.now();
    try {
        const response = await api.fetchApi('/freevideo/monitor', {signal: AbortSignal.timeout(4000)});
        if (response.ok) machine = await response.json();
    } catch { /* The capsule keeps its last figures, or says Ready. */ }
    finally { machineReading = false; }
    return machine;
}
// The figures follow the setting; with them off the capsule says Ready and nothing is read in the background.
const showFigures = () => { try { return figuresShown(localStorage.getItem(FIGURES_KEY)); } catch { return false; } };
if (browser) {
    const changed = () => { syncCapsules(); if (showFigures()) startMachine(); };
    globalThis.addEventListener?.('freevideo-monitor-shown', changed);
    globalThis.addEventListener?.('storage', event => { if (event.key === FIGURES_KEY) changed(); });
}
function pollMachine() {
    clearTimeout(machineTimer);
    if (!showFigures() || ![...capsules].some(capsule => capsule.isConnected)) { machineTimer = 0; return; }
    readMachine().then(() => syncCapsules(), () => {}).finally(() => { machineTimer = setTimeout(pollMachine, document.hidden ? 10000 : 2000); });
}
const startMachine = () => { if (!machineTimer) machineTimer = setTimeout(pollMachine, 0); };
function statusCapsule(t) {
    const capsule = el('button', 'fv-tb-cap'); capsule.type = 'button'; capsule.dataset.fvStatus = '';
    capsule.setAttribute('aria-haspopup', 'dialog'); capsule.setAttribute('aria-expanded', 'false');
    const dot = el('span', 'fv-tb-dot'), ring = ringSVG(), text = el('span', 'fv-tb-cap-txt'), count = el('span', 'fv-tb-n');
    const glow = el('i', 'fv-tb-glow'); glow.setAttribute('aria-hidden', 'true');
    capsule.append(dot, ring, text, count, glow);
    capsule.fvCapsule = {t, dot, ring, text, count};
    const [name, use] = words(t).status;
    tip(capsule, name, use);
    capsule.addEventListener('pointerenter', async () => {
        const line = machineLine(await readMachine(), t);
        capsule.dataset.fvTipUse = line ? `${use} · ${line}` : use;
    });
    // One panel for what runs, what just happened and this computer (TOPBAR.md §4, glob §7), under the capsule.
    // A press on the capsule while the panel is open closes it (the panel's own outside-press already did).
    capsule.addEventListener('pointerdown', () => { capsule.fvWasOpen = Boolean(activity?.isOpen() && activity.anchor === capsule); }, true);
    capsule.onclick = () => { if (hint) dropHint(); if (capsule.fvWasOpen) { capsule.fvWasOpen = false; activity?.close(); return; } toggleActivity(capsule, t); };
    capsules.add(capsule);
    renderCapsule(capsule);
    if (browser) startMachine();
    if (notices?.unread && !hintSeen()) setTimeout(noticeHint, 1500);
    return capsule;
}

// ── The status panel: what is running, what just finished or failed, this computer, the assistant's tasks ──
// Everything the bar used to show in separate capsules (run status with memory and temperature, the assistant's
// tasks) is here, with the queue's stop and resume. Nothing is read in the background: the computer's numbers are
// read while the panel is open (every 2 s), the assistant's state once per opening.
let activity = null;
const SECONDS = (t, value) => { const s = Math.max(1, Math.round(value)); return s < 120 ? t(`about ${s} s`, `约 ${s} 秒`) : t(`about ${Math.round(s / 60)} min`, `约 ${Math.round(s / 60)} 分钟`); };
function jobTitle(row, t) {
    if (row.source === 'agent') return agentTitle(row.job, t);
    const text = String(row.meta?.text ?? row.prompt ?? '').replace(/\s+/g, ' ').trim();
    return text || ({image: t('Picture', '图片'), video: t('Video', '视频'), chain: t('Long video', '长视频')}[row.kind] || t('Task', '任务'));
}
function jobLine(row, t) {
    if (row.status === 'queued' || row.status === 'submitting') return row.position > 0 ? t(`Waiting, ${row.position} ahead`, `排队中，前面 ${row.position} 个`) : t('Waiting', '排队中');
    if (row.status === 'cancelling') return t('Stopping', '正在停止');
    if (row.status === 'running') {
        const percent = Math.round(100 * (Number(row.percent) || 0));
        return [t(`Generating ${percent}%`, `生成中 ${percent}%`), Number.isFinite(row.eta_seconds) && row.eta_seconds > 0 ? SECONDS(t, row.eta_seconds) : ''].filter(Boolean).join(' · ');
    }
    if (row.status === 'done') return t('Finished', '已完成');
    if (row.status === 'cancelled') return t('Stopped', '已停止');
    const reason = String(row.failure?.message || '').trim();
    return reason ? `${t('Not finished', '未完成')} · ${reason}` : t('Not finished', '未完成');
}
// The right side of a task row's second line: "42% · about 48 s" while it runs, its place while it waits.
function jobFigures(row, t) {
    if (row.status === 'running') {
        const percent = Math.round(100 * (Number(row.percent) || 0));
        return [`${percent}%`, Number.isFinite(row.eta_seconds) && row.eta_seconds > 0 ? SECONDS(t, row.eta_seconds) : ''].filter(Boolean).join(' · ');
    }
    if (row.status === 'queued' || row.status === 'submitting') return row.position > 0 ? t(`${row.position} ahead`, `前面 ${row.position} 个`) : t('Waiting', '排队中');
    if (row.status === 'cancelling') return t('Stopping', '正在停止');
    if (row.status === 'done') return t('Finished', '已完成');
    if (row.status === 'cancelled') return t('Stopped', '已停止');
    return t('Not finished', '未完成');
}
function buildActivity(t) {
    const box = popover('fv-tb-act'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', words(t).status[0]); box.tabIndex = -1;
    // 1 · Title: "Run status", the graphics card and its memory, close.
    const head = el('div', 'fv-tb-act-h'), title = el('h2', '', words(t).status[0]), card = el('span', 'fv-tb-act-card');
    const shut = el('button', 'fv-tb-act-x'); shut.type = 'button'; withIcon(shut, 'close'); shut.setAttribute('aria-label', t('Close', '关闭'));
    tip(shut, t('Close', '关闭'), t('Hide the status panel', '收起运行状态'), 'Esc');
    const titles = el('div', 'fv-tb-act-titles'); titles.append(title, card);
    head.append(titles, shut);
    const body = el('div', 'fv-tb-act-b');
    const tasks = el('section', 'fv-tb-act-sec'), computer = el('section', 'fv-tb-act-sec'), notes = el('section', 'fv-tb-act-sec');
    const notesList = el('div', 'fv-tb-act-notes');
    body.append(tasks, computer, notes);
    // 5 · Foot: what runs, how many wait, stop (confirmed in place) or resume the queue.
    const foot = el('div', 'fv-tb-act-f');
    box.append(head, body, foot);
    let timer = 0, machineState = null, anchor = null, confirming = false, notesMount = null, notesShown = false;
    const close = () => closePop(box);
    shut.onclick = close;
    // Back to the job's view and its generation screen; works without the floating window (which may be off).
    const visit = async row => {
        close();
        const view = row.view || (row.kind === 'image' ? 'image' : 'studio');
        try { if (globalThis.FreeVideoShell?.view?.() !== view) await globalThis.FreeVideoShell?.go?.(view); } catch { /* The view did not open: stay. */ }
        if (row.source === 'agent') return;
        globalThis.dispatchEvent?.(new CustomEvent('freevideo-task-stage-open', {detail: {view, id: row.id}}));
        if (row.status === 'done' || isFailure(row)) jobs.markSeen?.(row.id);
    };
    const heading = (node, name, extra = null) => { const line = el('div', 'fv-tb-act-title'); line.append(el('span', '', name)); if (extra) line.append(extra); node.replaceChildren(line); };
    const VIEW = {studio: t('Studio', '创作面板'), image: t('Image', '图像视图'), editor: t('Edit', '剪辑视图'), autopilot: t('Autopilot', 'AI 全自动'), nodes: t('Nodes', '节点视图')};
    // 2 · Tasks: one row each; a press goes back to its picture; Stop (or Remove from queue) is confirmed first.
    const stopLost = new Map(), LOST_MS = 8000;
    const taskRow = row => {
        const line = el('div', `fv-tb-job${row.status === 'running' ? ' is-run' : ''}${row.status === 'queued' ? ' is-wait' : ''}`);
        const go = el('button', 'fv-tb-job-go'); go.type = 'button';
        const thumb = el('span', 'fv-tb-job-th'), src = row.meta?.thumb || row.preview || row.result?.thumb;
        if (src) { const image = el('img'); image.src = src; image.alt = ''; image.draggable = false; thumb.append(image); } else thumb.append(el('i'));
        const text = el('span', 'fv-tb-job-tx'), view = VIEW[row.view] || (row.kind === 'image' ? VIEW.image : VIEW.studio);
        // Title: the work's name; second line: the view on the left, the percentage and time left on the right.
        const sub = el('span', 'fv-tb-job-line');
        const lost = Date.now() - (stopLost.get(row.id) ?? -Infinity) < LOST_MS;
        sub.append(el('span', `fv-tb-job-t2${lost ? ' is-warn' : ''}`, lost ? connectionLostText(t) : view), el('span', `fv-tb-job-n${isFailure(row) ? ' is-warn' : ''}`, jobFigures(row, t)));
        text.append(el('span', 'fv-tb-job-t1', jobTitle(row, t)), sub);
        go.append(thumb, text);
        tip(go, jobTitle(row, t).slice(0, 40), t('Go back to its picture', '回到它的生成画面'));
        go.onclick = () => visit(row);
        line.append(go);
        if (row.status === 'running') { const bar = el('span', 'fv-tb-job-bar'); bar.style.setProperty('--p', String(Math.max(0, Math.min(1, Number(row.percent) || 0)))); line.append(bar); }
        // A waiting video behind another waiting one can go to the front of the queue (the engine moves it, then the
        // list is read again).
        if (row.kind === 'video' && row.status === 'queued' && row.prompt_id && row.node != null && row.position > 1) {
            const first = el('button', 'fv-tb-job-first'); first.type = 'button'; withIcon(first, 'first');
            first.setAttribute('aria-label', t('Move to front', '排到最前'));
            tip(first, t('Move to front', '排到最前'), t('Make it the next to start', '让它下一个开始'));
            first.onclick = async event => {
                event.stopPropagation(); first.disabled = true;
                try { await api.fetchApi('/freevideo/queue/first', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt_id: row.prompt_id, node_id: String(row.node)})}); }
                catch { /* The list is read again either way. */ }
                await jobs.refresh(); first.disabled = false;
            };
            line.append(first);
        }
        if (row.cancellable !== false && (row.status === 'running' || row.status === 'queued')) {
            const queued = row.status === 'queued', stop = el('button', 'fv-tb-job-stop'); stop.type = 'button'; withIcon(stop, 'stop');
            stop.setAttribute('aria-label', queued ? t('Remove from queue', '取消排队') : t('Stop', '停止'));
            tip(stop, queued ? t('Remove from queue', '取消排队') : t('Stop', '停止'), queued ? t('It will not be made', '这项不会再生成') : t('Stop this one; asks first', '停止这一项，会先确认'));
            stop.onclick = async event => {
                event.stopPropagation(); stopLost.delete(row.id);
                // The question sits in this panel, so answering it keeps the panel (and the rest of the list) open.
                try { const {confirmStop} = await import('./task_float.js'); if (await confirmStop(stop, {kind: row.kind === 'image' ? 'image' : 'video', queued, host: box, agent: row.source === 'agent'})) await (row.source === 'agent' ? agentTasks.cancel(row.agentId) : jobs.cancel(row.id)); }
                catch (error) {
                    // The row stays as it is; a lost connection is named where its view label is.
                    if (isNetworkTypeError(error)) { stopLost.set(row.id, Date.now()); drawTasks(); setTimeout(() => { if (isOpen()) drawTasks(); }, LOST_MS + 50); }
                }
            };
            line.append(stop);
        }
        return line;
    };
    const drawTasks = () => {
        const list = allJobs().filter(row => ACTIVE.has(row.status)).sort((a, b) => (b.status === 'running') - (a.status === 'running'));
        heading(tasks, t('Tasks', '任务'));
        if (list.length) tasks.append(...list.map(taskRow)); else tasks.append(el('p', 'fv-tb-act-empty', t('Nothing is running.', '没有进行中的任务。')));
        // Assistant tasks the engine could not read are counted, not hidden.
        const unreadable = agentTasks.unreadable();
        if (unreadable) tasks.append(el('p', 'fv-tb-act-empty fv-tb-act-unreadable', t(`${unreadable} ${unreadable === 1 ? 'job' : 'jobs'} could not be read`, `另有 ${unreadable} 个任务无法读取`)));
        drawFoot();
    };
    // 3 · This computer: video memory in large figures, then temperature, load and free disk, right-aligned.
    const drawComputer = () => {
        const state = machineState || {}, gb = bytes => (bytes / 2 ** 30).toFixed(1);
        card.textContent = [state.name, state.memory_total ? `${gb(state.memory_total)} GB` : ''].filter(Boolean).join(' · ');
        card.hidden = !card.textContent;
        heading(computer, t('This computer', '本机'));
        if (!machineState) { computer.append(el('p', 'fv-tb-act-empty', t('Reading…', '正在读取…'))); return; }
        if (state.memory_total) {
            const big = el('div', 'fv-tb-mem'), value = el('span', 'fv-tb-mem-v', gb(state.memory_used)), unit = el('span', 'fv-tb-mem-u', ` / ${gb(state.memory_total)} GB ${t('video memory', '显存')}`);
            big.append(value, unit);
            const fraction = state.memory_used / state.memory_total, bar = el('span', `fv-tb-bar${fraction >= 0.95 ? ' is-warn' : ''}`), fill = el('i'); fill.style.setProperty('--p', String(Math.min(1, fraction))); bar.append(fill);
            computer.append(big, bar);
        }
        const meters = [];
        const meter = (name, value, warn = false) => { const row = el('div', `fv-tb-meter${warn ? ' is-warn' : ''}`); row.append(el('span', '', name), el('b', '', value)); meters.push(row); };
        if (state.temperature != null) meter(t('Temperature', '温度'), `${state.temperature} °C`, state.temperature >= 85);
        if (state.utilization != null) meter(t('Graphics card load', '显卡占用'), `${state.utilization}%`);
        if (state.disk_free != null) meter(t('Free disk', '硬盘剩余'), `${gb(state.disk_free)} GB`);
        if (meters.length) { const list = el('div', 'fv-tb-meters'); list.append(...meters); computer.append(list); }
        if (!state.memory_total && !meters.length) computer.append(el('p', 'fv-tb-act-empty', t('No graphics card readings.', '读不到显卡信息。')));
        drawFoot();
    };
    // 4 · Notices: the notification centre's list (finished, not finished, installs); its unread count is on the capsule.
    const drawNotes = async () => {
        heading(notes, t('Notices', '通知'));
        notes.append(notesList);
        try {
            if (!notesMount) {
                notesMount = mountNotices(notesList, {live: false, limit: 6});
                // Opening a notice leaves the panel, so what it leads to is not left behind it.
                notesList.addEventListener('click', event => { if (event.target.closest('.fv-note') && !event.target.closest('.fv-note-x, [data-act]')) close(); });
                // An empty section does not show (only rows count, not the list's own empty state).
                new MutationObserver(() => { notes.hidden = !notesList.querySelector('.fv-note'); }).observe(notesList, {childList: true, subtree: true});
            }
            notesMount.refresh?.(); notesShown = true;
            notes.hidden = !notesList.querySelector('.fv-note');
        } catch { notes.hidden = true; }
    };
    const drawFoot = () => {
        const list = jobs.list(), running = list.find(row => row.status === 'running'), waiting = list.filter(row => row.status === 'queued').length;
        const state = machineState || {};
        foot.replaceChildren();
        if (state.paused) {
            foot.append(el('span', 'fv-tb-act-note', t('Stopped. The queue is paused.', '已停止，队列已暂停。')), el('span', 'fv-tb-grow'));
            const resume = el('button', 'fv-tb-act-btn', t('Resume queue', '继续队列')); resume.type = 'button';
            tip(resume, t('Resume queue', '继续队列'), t('Start the waiting tasks again', '让排队的任务继续开始'));
            resume.onclick = async () => { resume.disabled = true; try { await api.fetchApi('/freevideo/monitor/resume', {method: 'POST'}); machineState = {...state, paused: false}; } catch { /* Read again below. */ } drawFoot(); readNow(); };
            foot.append(resume); foot.hidden = false; return;
        }
        const busy = Boolean(running) || state.busy;
        foot.hidden = !busy && !waiting;
        if (foot.hidden) return;
        if (confirming) {
            const ask = el('p', 'fv-tb-act-ask', waiting ? t(`Stop the current video? What is being made is not kept; the ${waiting} waiting will pause.`, `停止当前视频？正在生成的不会保留，排队的 ${waiting} 个会暂停。`)
                : t('Stop the current video? What is being made is not kept.', '停止当前视频？正在生成的不会保留。'));
            const keep = el('button', 'fv-tb-act-btn', t('Keep running', '继续运行')), stop = el('button', 'fv-tb-act-btn is-armed', t('Stop', '停止'));
            keep.type = stop.type = 'button';
            keep.onclick = () => { confirming = false; drawFoot(); foot.querySelector('.fv-tb-act-stop')?.focus(); };
            stop.onclick = async () => {
                stop.disabled = true; confirming = false;
                try { await api.fetchApi('/freevideo/monitor/stop', {method: 'POST'}); } catch { /* Read again below. */ }
                machineState = {...state, paused: true, busy: false}; drawFoot(); readNow();
            };
            const row = el('div', 'fv-tb-act-row'); row.append(el('span', 'fv-tb-grow'), keep, stop);
            foot.classList.add('is-asking'); foot.append(ask, row);
            requestAnimationFrame(() => keep.focus());
            return;
        }
        foot.classList.remove('is-asking');
        const said = running ? jobLine(running, t) : t('Working', '运行中');
        foot.append(el('span', 'fv-tb-act-note', said));
        if (waiting) foot.append(el('span', 'fv-tb-act-note is-quiet', t(`${waiting} waiting`, `排队 ${waiting}`)));
        foot.append(el('span', 'fv-tb-grow'));
        const stop = el('button', 'fv-tb-act-stop', t('Stop', '停止')); stop.type = 'button';
        tip(stop, t('Stop', '停止'), t('Stop what runs and pause the queue; asks first', '停止正在生成的并暂停队列，会先确认'));
        stop.onclick = () => { confirming = true; drawFoot(); };
        foot.append(stop);
    };
    async function readNow() {
        clearTimeout(timer);
        try { const response = await api.fetchApi('/freevideo/monitor', {signal: AbortSignal.timeout(6000)}); if (response.ok) { machineState = await response.json(); machine = machineState; machineAt = Date.now(); } }
        catch { machineState ||= {}; }
        if (!isOpen()) return;
        drawComputer();
        timer = setTimeout(readNow, document.hidden ? 10000 : 2000);
    }
    const isOpen = () => { try { return box.matches(':popover-open'); } catch { return !box.hidden; } };
    jobs.subscribe(() => { if (isOpen()) drawTasks(); });
    agentTasks.subscribe(() => { if (isOpen()) drawTasks(); });
    box.addEventListener('toggle', async event => {
        anchor?.setAttribute('aria-expanded', String(event.newState === 'open'));
        if (event.newState !== 'closed') return;
        clearTimeout(timer); confirming = false; agentTasks.watch(false);
        // What was shown in Notices has been seen.
        if (notesShown) { notesShown = false; markNoticesRead(); }
    });
    // Over every view, the top right: 16 from the right, 16 under the bar, 320 wide, down to 16 above the bottom.
    const place = () => {
        const bar = anchor?.closest('.fv-topbar')?.getBoundingClientRect();
        const top = Math.round((bar ? bar.bottom : 56) + (innerWidth < 720 ? 4 : 16));
        box.style.top = `${top}px`; box.style.maxHeight = `calc(100vh - ${top + 16}px)`;
        if (innerWidth < 720) { box.style.left = '12px'; box.style.right = '12px'; box.style.width = 'auto'; }
        else { box.style.left = 'auto'; box.style.right = '16px'; box.style.width = ''; }
    };
    return {box, isOpen, close,
        show(capsule) {
            anchor = capsule; machineState = machine; confirming = false;
            drawTasks(); drawComputer(); void drawNotes(); agentTasks.watch(true);
            (capsule.closest('dialog[open]') || document.body).append(box);
            try { box.showPopover ? box.showPopover() : (box.hidden = false); } catch { box.hidden = false; }
            place();
            capsule.setAttribute('aria-expanded', 'true');
            readNow();
            // The panel itself takes focus (Tab then walks its rows), so no tip pops up over the list as it opens.
            requestAnimationFrame(() => box.focus({preventScroll: true}));
        }};
}
function toggleActivity(capsule, t) {
    // A view that docks the status panel itself (Studio's right-hand dock) takes the press: it listens for
    // freevideo-status-toggle on window and calls preventDefault() when it handled it.
    const view = capsule.closest('.fv-topbar')?.dataset.fvBar || '';
    const handed = new CustomEvent('freevideo-status-toggle', {cancelable: true, detail: {view, anchor: capsule}});
    if (!globalThis.dispatchEvent(handed)) return;
    activity ||= buildActivity(t);
    if (activity.isOpen() && activity.anchor === capsule) { activity.close(); return; }
    if (activity.isOpen()) activity.close();
    activity.anchor = capsule;
    activity.show(capsule);
}

// ── The entries on the right: the same set, in the same order, in every view (and the node view's bar) ──
// Returns {element (the right column), lan, version, dispose}.
export function topbarActions(t = t0) {
    const w = words(t);
    const right = el('div', 'fv-tb-right');
    const capsule = statusCapsule(t);
    const search = paletteButton(t, 'fv-tb-btn fv-tb-search'); withIcon(search, 'search'); search.replaceChildren(); tip(search, ...w.search);
    search.dataset.fvField = t('Search works, prompts and actions', '搜索作品、提示词和操作');
    const sep = el('span', 'fv-tb-sep'); sep.setAttribute('aria-hidden', 'true');
    // The works library is one click away in every view and at every width: never folded into the menu (CEO 11-17).
    // It is a link like the five beside it (same box, icon, hover), kept out of the phone menu so it stays on the bar.
    const works = el('button', 'fv-tb-link fv-tb-works'); works.type = 'button'; withIcon(works, 'works'); tip(works, ...w.works);
    works.append(el('span', 'fv-tb-lbl', w.works[0])); works.setAttribute('aria-label', w.works[0]);
    works.onclick = () => openCreations();
    const worksOpen = event => { if (event.detail?.open) works.setAttribute('aria-current', 'page'); else works.removeAttribute('aria-current'); };
    globalThis.addEventListener?.('freevideo-library', worksOpen);
    const links = el('div', 'fv-tb-links'); links.setAttribute('role', 'group'); links.setAttribute('aria-label', t('App', '应用'));
    const lan = createLanAccess(t, api), version = createVersionInfo(CN);
    const phone = lan.element.querySelector('.fv-lan-trigger'); withIcon(phone, 'phone'); phone.classList.add('fv-tb-link'); tip(phone, ...w.phone);
    lan.element.querySelector('.fv-lan-tip')?.remove();
    phone.onpointerenter = phone.onfocus = null; phone.onpointerleave = phone.onblur = null;
    withIcon(version.element, 'tag'); version.element.classList.add('fv-tb-link'); tip(version.element, ...w.version);
    version.element.setAttribute('aria-label', w.version[0]); version.element.dataset.fvNew = t('Update', '有更新');
    const tutorial = guideButton(t, 'fv-tb-link'); withIcon(tutorial, 'book'); tip(tutorial, ...w.tutorial);
    const feedback = feedbackButton(t, 'fv-tb-link'); withIcon(feedback, 'chat'); tip(feedback, ...w.feedback);
    const settings = el('button', 'fv-tb-link fv-settings-entry'); settings.type = 'button'; settings.append(el('span', 'fv-tb-lbl', w.settings[0]));
    settings.setAttribute('aria-label', w.settings[0]); settings.onclick = openSetup; withIcon(settings, 'gear'); tip(settings, ...w.settings);
    for (const label of [tutorial, feedback]) label.querySelector('.fv-feedback-label')?.classList.add('fv-tb-lbl');
    links.append(lan.element, version.element, tutorial, feedback, settings);
    const menuButton = el('button', 'fv-tb-btn fv-tb-menu'); menuButton.type = 'button'; withIcon(menuButton, 'menu'); tip(menuButton, ...w.menu);
    menuButton.setAttribute('aria-label', w.menu[0]); menuButton.setAttribute('aria-haspopup', 'menu'); menuButton.setAttribute('aria-expanded', 'false');
    right.append(capsule, search, sep, works, links, menuButton);
    const menu = phoneMenu(right, menuButton, [search, links]);
    return {element: right, capsule, lan, version, menu, dispose() { lan.dispose?.(); version.dispose?.(); capsules.delete(capsule); menu.dispose(); globalThis.removeEventListener?.('freevideo-library', worksOpen); }};
}

// ── Phone level: one menu button holds search and the links (moved in while it is open, back when it closes) ──
function popover(cls) {
    const box = el('div', `fv-tb-pop ${cls}`); box.dataset.fvTipScope = '';
    if (box.showPopover) box.popover = 'auto'; else box.hidden = true;
    return box;
}
// A menu from the bar closes when focus leaves it or the view changes.
function closeWithFocus(box) {
    if (box.fvClosing) return;
    box.fvClosing = true;
    box.addEventListener('focusout', event => { if (!box.contains(event.relatedTarget) && event.relatedTarget) closePop(box); });
    globalThis.addEventListener?.('freevideo-view', () => closePop(box));
}
function openPop(box, anchor, align = 'end') {
    closeWithFocus(box);
    (anchor.closest('dialog[open]') || document.body).append(box);
    try { box.showPopover ? box.showPopover() : (box.hidden = false); } catch { box.hidden = false; }
    // At the phone level the view list and the app menu are sheets under the bar, 12 px from both edges (TOPBAR §3b).
    const bar = anchor.closest('.fv-topbar');
    if (bar?.dataset.fvFit === '3') {
        box.classList.add('is-sheet');
        box.style.top = `${Math.round(bar.getBoundingClientRect().bottom + 4)}px`; box.style.left = '12px'; box.style.right = '12px';
        return;
    }
    box.classList.remove('is-sheet'); box.style.right = '';
    const r = anchor.getBoundingClientRect(), w = box.offsetWidth;
    box.style.top = `${Math.round(r.bottom + 8)}px`;
    const left = align === 'center' ? r.left + r.width / 2 - w / 2 : r.right - w;
    box.style.left = `${Math.round(Math.max(8, Math.min(left, innerWidth - 8 - w)))}px`;
}
function closePop(box) { try { if (box.matches(':popover-open')) box.hidePopover(); } catch { /* Closed. */ } if (!box.showPopover) box.hidden = true; }
function phoneMenu(right, button, parts) {
    const box = popover('fv-tb-sheet'); box.setAttribute('role', 'menu');
    const homes = parts.map(part => [part, part.parentElement, part.nextSibling]);
    let moved = false;
    const back = () => {
        if (!moved) return; moved = false;
        for (const [part, , ] of homes) right.insertBefore(part, button);
        // Keep the original order: capsule, search, separator, links, menu.
        const [search, links] = parts, sep = right.querySelector('.fv-tb-sep');
        right.insertBefore(search, sep); right.insertBefore(links, button);
        button.setAttribute('aria-expanded', 'false');
    };
    box.addEventListener('toggle', event => { if (event.newState === 'closed') back(); });
    button.onclick = () => {
        if (moved) { closePop(box); back(); return; }
        moved = true; box.replaceChildren(...parts); button.setAttribute('aria-expanded', 'true'); openPop(box, button);
    };
    // A choice in the menu closes it (the phone panel stays open on its own).
    box.addEventListener('click', event => { if (event.target.closest('.fv-tb-link:not(.fv-lan-trigger), .fv-tb-search')) { closePop(box); back(); } });
    return {dispose() { closePop(box); box.remove(); }};
}

// ── The view tabs (viewSwitch builds them; this adds what the bar needs) ──
// Tips, the sliding capsule under the current tab, arrow keys between tabs, and at the phone level a picker: only the
// current tab shows (with a chevron) and pressing it lists the five views.
export function decorateTabs(group, view, t = t0) {
    if (group.fvTabs) return group;
    const w = words(t).views;
    group.classList.add('fv-tb-tabs');
    for (const button of group.querySelectorAll('[data-view]')) {
        const [name, use, key] = w[button.dataset.view] || [];
        if (name) tip(button, name, use, key);
        button.classList.add('fv-tb-tab');
    }
    // Arrow keys move between tabs (the group is one stop for Tab: only the pressed tab is focusable).
    const tabs = () => [...group.querySelectorAll('[data-view]')].filter(button => !button.hidden);
    const roving = () => {
        for (const button of tabs()) {
            const here = button.getAttribute('aria-pressed') === 'true';
            button.tabIndex = here ? 0 : -1;
            if (here) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
        }
    };
    group.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        const list = tabs(), at = list.indexOf(document.activeElement); if (at < 0) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? list.length - 1 : (at + (event.key === 'ArrowRight' ? 1 : -1) + list.length) % list.length;
        list[next].tabIndex = 0; list[next].focus();
    });
    group.addEventListener('focusout', () => setTimeout(() => { if (!group.contains(document.activeElement)) roving(); }));
    new MutationObserver(() => { roving(); slide(group); }).observe(group, {subtree: true, attributes: true, attributeFilter: ['aria-pressed', 'hidden']});
    roving();
    // A view that comes back to the front (one left open under another) lights its own tab again: the tab pressed on
    // the way out stayed lit on its bar.
    const own = view || group.querySelector('[aria-pressed=true]')?.dataset.view || '';
    const back = event => {
        if (!group.isConnected) { globalThis.removeEventListener?.('freevideo-view', back); return; }
        if (event.detail?.view !== own) return;
        for (const tab of group.querySelectorAll('[data-view]')) { const next = String(tab.dataset.view === own); if (tab.getAttribute('aria-pressed') !== next) tab.setAttribute('aria-pressed', next); }
    };
    if (own) globalThis.addEventListener?.('freevideo-view', back);
    // The picker: at the phone level the pressed tab opens the list instead of re-opening its own view.
    const list = popover('fv-tb-sheet fv-tb-views'); list.setAttribute('role', 'menu'); list.setAttribute('aria-label', words(t).picker[0]);
    group.addEventListener('click', event => {
        const button = event.target.closest('[data-view]');
        if (!button || group.closest('[data-fv-fit]')?.dataset.fvFit !== '3' || button.getAttribute('aria-pressed') !== 'true') return;
        event.stopPropagation(); event.preventDefault();
        list.replaceChildren(...tabs().map(tab => {
            const item = el('button', 'fv-tb-item', tab.dataset.label || tab.textContent); item.type = 'button'; item.setAttribute('role', 'menuitemradio');
            withIcon(item, tab.dataset.view);
            item.setAttribute('aria-checked', tab.getAttribute('aria-pressed'));
            item.onclick = () => { closePop(list); if (tab !== button) tab.click(); };
            return item;
        }));
        openPop(list, button, 'center');
        list.querySelector('[aria-checked=true]')?.focus();
    }, true);
    group.fvTabs = {list};
    return group;
}
// The lighter capsule under the pressed tab slides to the new tab (clip-path, eased out, no overshoot).
function slide(group) {
    const pressed = group.querySelector('[data-view][aria-pressed=true]:not([hidden])');
    if (!pressed || !pressed.offsetWidth) return;
    const left = `${pressed.offsetLeft}px`, right = `${pressed.offsetLeft + pressed.offsetWidth}px`;
    if (group.style.getPropertyValue('--fv-tb-l') !== left) group.style.setProperty('--fv-tb-l', left);
    if (group.style.getPropertyValue('--fv-tb-r') !== right) group.style.setProperty('--fv-tb-r', right);
    // The first place is taken at once; only later moves slide (a new bar never animates in).
    if (!group.hasAttribute('data-fv-tb-ink')) { void getComputedStyle(group, '::before').clipPath; group.setAttribute('data-fv-tb-ink', ''); }
}

// The capsule is as wide as what it says (Iris W6: a fixed width left "Ready" in an empty pill); its figures sit in
// fixed digit slots so a changing number never changes its width, and the bar refits when its words change.
// The capsule fits its content (no reserved minimum: a wide pill around "Waiting 1" looked unfinished).
function reserveCapsule(header) { header.style.removeProperty('--fv-tb-cap-w'); header.style.removeProperty('--fv-tb-cap-min'); }
// The capsule's widest everyday state at a level ("Generating 100%" with a count), used only when the bar picks its
// fold: the room is kept in the empty space left of the capsule, not inside it, so going from Ready to Generating
// never refolds the bar and nothing else moves.
let measurer = null;
function textWidth(text, font) {
    if (!text) return 0;
    measurer ||= document.createElement('canvas').getContext('2d');
    if (!measurer) return text.length * 8;
    measurer.font = font; return measurer.measureText(text).width;
}
function widestCapsule(capsule, level) {
    if (!capsule?.fvCapsule || level === '3') return 0;
    const style = getComputedStyle(capsule), font = `${style.fontSize} ${style.fontFamily}`, t = capsule.fvCapsule.t;
    const px = value => parseFloat(value) || 0, gap = px(style.columnGap) || 8;
    const [word] = capsuleWords(t, {kind: 'run', percent: 100});
    const text = (level === '2' ? 0 : textWidth(`${word} `, `400 ${font}`)) + textWidth('100%', `600 ${font}`) + (level === '2' ? 0 : gap + textWidth('99', `500 ${font}`));
    return Math.ceil((px(style.paddingLeft) + px(style.paddingRight) + 16 + gap + text) / 4) * 4;
}

// ── The logo: the official mark in the version for the current theme (wordmark.js follows theme changes) ──
function logo(t) {
    const button = el('button', 'fv-tb-logo fv-brand'); button.type = 'button';
    const image = wordmark(); image.classList.add('fv-topbar-logo'); button.append(image);
    const [name, use] = words(t).logo;
    tip(button, name, use); button.setAttribute('aria-label', 'FreeVideo');
    // Back to the top of this view: the view's own scrollers go back to their start.
    button.onclick = () => {
        const host = button.closest('dialog') || document;
        for (const scroller of host.querySelectorAll('.fv-body,.fv-controls,.fv-iv-panel-scroll,.fv-chat-log')) scroller.scrollTo?.({top: 0, behavior: 'smooth'});
    };
    return button;
}

// Every box on the bar sits on the 4 px grid: tabs, text links and the capsule take the next multiple of 4 of their width.
function snap(header) {
    const items = header.querySelectorAll(':scope > .fv-view-switch > [data-view], :scope > .fv-tb-right .fv-tb-link');
    for (const item of items) item.style.removeProperty('min-width');
    const widths = [...items].map(item => item.getBoundingClientRect().width);
    items.forEach((item, i) => { if (widths[i] > 0) item.style.minWidth = `${Math.ceil(widths[i] / 4 - 0.01) * 4}px`; });
    const capsule = header.querySelector(':scope > .fv-tb-right > .fv-tb-cap'); if (capsule) fitCapsuleWidth(capsule);
}

// ── Fitting: from level 0 up, the first level whose right side fits its column (TOPBAR.md §3) ──
const PHONE = 720;
export function fitLevel(header) {
    const right = header.querySelector(':scope > .fv-tb-right'), brand = header.querySelector(':scope > .fv-tb-logo');
    if (!right || !header.clientWidth) return header.dataset.fvFit || '0';
    if (header.clientWidth < PHONE) { header.dataset.fvWorks = 'icon'; return '3'; }
    // The links are icons with hover tips in every language (level 1 up): Chinese and English get the same layout.
    // The works entry carries its name only on room left over at level 1, so the name never folds anything else.
    for (const [level, works] of [['1', 'name'], ['1', 'icon'], ['2', 'icon']]) {
        header.dataset.fvFit = level; header.dataset.fvWorks = works; reserveCapsule(header, level); snap(header);
        const columns = getComputedStyle(header).gridTemplateColumns.split(' ').map(parseFloat);
        const side = columns.length >= 3 ? Math.min(columns[0], columns[2]) : 0;
        const capsule = right.querySelector(':scope > .fv-tb-cap');
        const room = capsule ? Math.max(0, widestCapsule(capsule, level) - capsule.getBoundingClientRect().width) : 0;
        if (right.scrollWidth + room <= side + 0.5 && (!brand || brand.scrollWidth <= side + 0.5)) return level;
    }
    header.dataset.fvWorks = 'icon';
    return '3';
}
function keepFit(header) {
    if (typeof ResizeObserver !== 'function') return {check() {}, dispose() {}};
    let busy = false;
    const check = () => {
        if (busy || !header.isConnected) return;
        busy = true;
        try {
            const level = fitLevel(header);
            header.dataset.fvFit = level; reserveCapsule(header, level); snap(header);
            slide(header.querySelector(':scope > .fv-view-switch') || header);
            // The right column's width, for what grows out of the capsule (shell's toast host reads --fv-right-col).
            const columns = getComputedStyle(header).gridTemplateColumns.split(' ').map(parseFloat);
            if (columns.length >= 3 && columns[2] > 0) document.documentElement.style.setProperty('--fv-right-col', `${Math.floor(columns[2])}px`);
        }
        finally { busy = false; }
    };
    const observer = new ResizeObserver(check);
    observer.observe(header);
    const tabs = header.querySelector(':scope > .fv-view-switch'); if (tabs) observer.observe(tabs);
    const right = header.querySelector(':scope > .fv-tb-right'); if (right) observer.observe(right);
    document.fonts?.ready?.then(check, () => {});
    return {check, dispose: () => observer.disconnect()};
}

// ── Taking a view's header over ──
// header: the view dialog's `:scope > .fv-header` holding a `.fv-view-switch`. The view keeps every function: the
// tabs are its own (with its actions), the right side is the shared set. Idempotent.
export function adoptHeader(header, {t = t0} = {}) {
    if (!header || header.fvTopbar) return header?.fvTopbar || null;
    const tabs = header.querySelector(':scope > .fv-view-switch');
    if (!tabs) return null;
    const view = tabs.querySelector('[aria-pressed=true]')?.dataset.view || '';
    decorateTabs(tabs, view, t);
    const brand = logo(t), actions = topbarActions(t);
    header.classList.add('fv-topbar'); header.dataset.fvBar = view;
    header.style.setProperty('--fv-tb-chevron', iconURL('chevron'));
    header.setAttribute('role', 'banner');
    // The view's own entries leave the bar (their components stay alive until the view disposes them). The tabs stay
    // in place, so a tab the view has already focused keeps the focus (moving a node out of the page drops it).
    for (const child of [...header.children]) if (child !== tabs) child.remove();
    header.prepend(brand); header.append(actions.element);
    delete header.dataset.fit; delete header.dataset.place;
    const fit = keepFit(header);
    const host = header.closest('dialog');
    let disposed = false;
    const dispose = () => { if (disposed) return; disposed = true; fit.dispose(); actions.dispose(); };
    // A view that closes and is shown again (the editor re-entering the top layer) keeps its bar; one that is removed
    // from the page lets the bar's parts go.
    host?.addEventListener('close', () => setTimeout(() => { if (!header.isConnected) dispose(); }, 0));
    header.fvTopbar = {view, actions, fit, dispose};
    fit.check();
    return header.fvTopbar;
}

// A header whose tabs viewSwitch has just made: taken over as soon as it is in the page (before it is painted).
export function adoptWhenPlaced(group) {
    if (!browser) return;
    const attempt = () => {
        const header = group.parentElement;
        if (!group.isConnected || !header?.matches?.('.fv-header') || header.fvTopbar) return Boolean(header?.fvTopbar);
        adoptHeader(header);
        return true;
    };
    queueMicrotask(() => {
        if (attempt()) return;
        const observer = new MutationObserver(() => { if (attempt()) observer.disconnect(); });
        observer.observe(document.body || document.documentElement, {childList: true, subtree: true});
        setTimeout(() => observer.disconnect(), 15000);
    });
}

// The node view's bar: the same parts, built directly (view_navigation.js installNavigation).
export function buildBar(row, tabs, {t = t0} = {}) {
    decorateTabs(tabs, 'nodes', t);
    const brand = logo(t), actions = topbarActions(t);
    row.classList.add('fv-topbar'); row.dataset.fvBar = 'nodes';
    row.style.setProperty('--fv-tb-chevron', iconURL('chevron'));
    row.replaceChildren(brand, tabs, actions.element);
    const fit = keepFit(row);
    row.fvTopbar = {view: 'nodes', actions, fit, dispose() { fit.dispose(); actions.dispose(); }};
    return row.fvTopbar;
}
