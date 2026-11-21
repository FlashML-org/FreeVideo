// The notification centre: what finished, what failed and what is installing, in one list under the bell of the
// top bar. It reads the page-wide job store (task_store.js) and the image models' install state; it never starts or
// stops anything. A dot on the bell counts what has not been seen; a click on a row goes back to it the way the
// floating window does (a finished job opens where it was made, a failed one opens its failure card).
// The list is kept in this browser (freevideo.notifications, 50 rows, a week). Also here: toast(), one short line
// at the bottom of the window for things the command layer has to say.
import { cutTip } from './cut_tip.js';
import { setTip } from './tip_words.js';
import { t, words, CN, glyph, floatingHost, reducedMotion, EASE, addSheet, openContextMenu } from './context_menu.js';
import { jobs, ACTIVE, FINAL } from './task_store.js';

const browser = typeof document !== 'undefined';
addSheet('notifications.css', import.meta.url);

export const NOTES_KEY = 'freevideo.notifications';
export const DISMISSED_KEY = 'freevideo.notifications.dismissed';
const DISMISSED_LIMIT = 300;
const LIMIT = 50, AGE_MS = 7 * 24 * 3600e3, LATE_MS = 30 * 60e3;
const clean = text => String(text ?? '').replace(/\s+/g, ' ').trim();

// ── The list, apart from the page (the tests drive it) ──
export function createNotes({storage = null, now = () => Date.now(), limit = LIMIT} = {}) {
    let rows = (() => { try { const value = JSON.parse(storage?.getItem(NOTES_KEY) || '[]'); return Array.isArray(value) ? value.filter(row => row?.id && row.at) : []; } catch { return []; } })();
    const listeners = new Set();
    let hidden = [];
    // Notices the user closed or cleared stay closed: a reload or a job's later updates do not bring them back.
    let dismissed = (() => { try { const value = JSON.parse(storage?.getItem(DISMISSED_KEY) || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; } catch { return {}; } })();
    const forget = ids => {
        const at = now();
        for (const id of ids) dismissed[id] = at;
        const cutoff = at - AGE_MS;
        dismissed = Object.fromEntries(Object.entries(dismissed).filter(([, when]) => when >= cutoff).sort((a, b) => b[1] - a[1]).slice(0, DISMISSED_LIMIT));
        try { storage?.setItem(DISMISSED_KEY, JSON.stringify(dismissed)); } catch { /* This page only. */ }
    };
    const keep = () => { const cutoff = now() - AGE_MS; rows = rows.filter(row => row.at >= cutoff).sort((a, b) => b.at - a.at).slice(0, limit); };
    const save = () => { keep(); try { storage?.setItem(NOTES_KEY, JSON.stringify(rows)); } catch { /* This page only. */ } for (const fn of [...listeners]) try { fn(rows); } catch { /* One listener never stops the others. */ } };
    keep();
    return {
        list: () => rows,
        has: id => rows.some(row => row.id === id),
        add(note) { if (!note?.id || dismissed[note.id] || rows.some(row => row.id === note.id)) return false; rows = [{read: false, at: now(), ...note}, ...rows]; save(); return true; },
        markRead(id = null) { let changed = false; rows = rows.map(row => (id === null || row.id === id) && !row.read ? (changed = true, {...row, read: true}) : row); if (changed) save(); },
        remove(id) { const before = rows.length; rows = rows.filter(row => row.id !== id); forget([id]); if (rows.length !== before) save(); },
        clear() { if (rows.length) { forget(rows.map(row => row.id)); rows = []; save(); } },
        wasDismissed: id => Boolean(dismissed[id]),
        // Creations deleted elsewhere take their notices with them; restored ones bring them back where they were
        // (rows are kept in time order, so they land in their old place). Unread counts follow.
        hideWorks(works) {
            const gone = rows.filter(row => noteMatchesWorks(row, works));
            if (!gone.length) return [];
            rows = rows.filter(row => !gone.includes(row)); hidden.push(...gone); save();
            return gone;
        },
        restoreWorks(works) {
            const back = hidden.filter(row => noteMatchesWorks(row, works));
            if (!back.length) return [];
            hidden = hidden.filter(row => !back.includes(row));
            rows = [...rows, ...back.filter(row => !rows.some(other => other.id === row.id))]; save();
            return back;
        },
        unread: () => rows.filter(row => !row.read).length,
        subscribe(fn) { listeners.add(fn); fn(rows); return () => listeners.delete(fn); },
    };
}

// Whether a notice is about one of these creations: by creation id or by output file, whichever each side has.
// 'FreeVideo/<id>/video.mp4', '<id>' and 'freevideo-image/x.jpg' all name the same thing as their short forms.
const workKey = value => String(value || '').trim().replace(/\\/g, '/').replace(/^\/+/, '').replace(/^FreeVideo\//, '').replace(/\/video\.mp4$/, '');
export function noteMatchesWorks(note, {ids = [], paths = []} = {}) {
    const wanted = new Set([...ids, ...paths].map(workKey).filter(Boolean));
    if (!wanted.size) return false;
    return [note.work, note.file, note.video].map(workKey).some(key => key && wanted.has(key));
}

// A job that has just finished, failed or been cut off becomes one note. A job first seen already finished (the
// page was closed while it ran) counts only when it finished in the last half hour.
const NOTE_STATES = new Set(['done', 'failed', 'interrupted']);
// A person-readable cause only: the raw code is never shown, so an unknown one gives nothing and the line says where to look.
const reasonOf = failure => {
    const code = failure?.code;
    if (code === 'engine_closed') return words(['The engine was closed', '引擎已关闭']);
    if (code === 'engine_restarted') return words(['The engine restarted', '引擎已重启']);
    return '';
};
export function noteFromJob(row, previous, now = Date.now()) {
    if (!row || !NOTE_STATES.has(row.status)) return null;
    if (previous && previous.status === row.status) return null;
    if (!previous && (!row.finished_at || now - row.finished_at > LATE_MS)) return null;
    return {id: `job:${row.id}:${row.status}`, type: 'job', job: row.id, kind: row.kind || 'video', status: row.status, view: row.view || null,
        prompt: clean(row.meta?.text ?? row.prompt ?? '').slice(0, 140), file: row.result?.file || null, work: row.result?.id || row.result?.work || null, video: row.result?.video || null,
        reason: reasonOf(row.failure), at: row.finished_at || now};
}
// The image models' install, between two reads of the image worker's info: a note when one finishes or fails.
// A failed assistant task: one note with the task's name and its cause; it opens the failure card in the conversation.
export function noteFromAgent(job, {title: name = '', reason = ''} = {}, now = Date.now()) {
    if (!job?.id || job.status !== 'failed') return null;
    const finished = Date.parse(job.finished_at || '') || 0;
    if (!finished || now - finished > LATE_MS) return null;
    return {id: `agent:${job.id}:failed`, type: 'agent', job: job.id, status: 'failed', view: 'autopilot', title: String(name).slice(0, 140), reason: String(reason).slice(0, 160), at: finished};
}
export function addAgentNote(note) { if (note && !store().has(note.id)) store().add(note); }
export function installChanges(before = {}, after = {}) {
    const notes = [];
    for (const [id, now] of Object.entries(after)) {
        const was = before[id];
        if (!was || was.state === now.state) continue;
        if (now.state === 'done' || (now.ready && !was.ready)) notes.push({id: `install:${id}:${Date.now()}`, type: 'install', status: 'done', label: now.label, model: id});
        else if (now.state === 'failed') notes.push({id: `install:${id}:${Date.now()}`, type: 'install', status: 'failed', label: now.label, model: id, reason: now.reason || ''});
    }
    return notes;
}
// What the worker says about each model, reduced to {state, done, total, label, ready, reason}.
export function installState(info, cn = CN) {
    const out = {};
    for (const model of info?.models || []) {
        const install = model.install || {};
        const state = model.ready ? 'done' : install.state === 'running' || install.state === 'paused_for_generation' || model.downloading ? 'running' : install.state || 'idle';
        out[model.id] = {state, ready: Boolean(model.ready), done: install.done ?? model.bytes_done ?? 0, total: install.total ?? model.bytes_total ?? 0, label: model.label || model.id,
            paused: install.state === 'paused_for_generation', reason: clean(cn ? install.message_zh || install.message_en : install.message_en || install.message_zh)};
    }
    return out;
}

// The newest job that finished well (for "Go to the latest generation").
export function latestFinished(list = jobs.list()) {
    return list.filter(row => row.status === 'done').sort((a, b) => (b.finished_at || 0) - (a.finished_at || 0))[0] || null;
}

// ── Words ──
const KIND = {image: ['Picture', '图像'], video: ['Video', '视频'], chain: ['Long video', '长视频']};
const ago = (at, cn = CN) => {
    const s = Math.max(0, Math.round((Date.now() - at) / 1000));
    if (s < 60) return cn ? '刚刚' : 'Just now';
    if (s < 3600) return cn ? `${Math.floor(s / 60)} 分钟前` : `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return cn ? `${Math.floor(s / 3600)} 小时前` : `${Math.floor(s / 3600)} h ago`;
    return cn ? `${Math.floor(s / 86400)} 天前` : `${Math.floor(s / 86400)} d ago`;
};
export function noteWords(note, cn = CN) {
    const w = pair => words(pair, cn);
    if (note.type === 'toast') return {title: note.title || w(['Notice', '提醒']), sub: note.reason || (note.status === 'notice' ? '' : w(['Did not finish', '没有完成']))};
    if (note.type === 'agent') return {title: note.title || w(['Assistant task', '助手任务']), sub: note.reason || w(['See why', '查看原因'])};
    if (note.type === 'install') {
        return note.status === 'done' ? {title: w([`${note.label} is installed`, `${note.label} 已安装`]), sub: w(['Ready to use', '可以使用了'])}
            : {title: w([`${note.label} did not install`, `${note.label} 安装失败`]), sub: note.reason || w(['Open Images to try again', '打开图像视图重试'])};
    }
    const kind = w(KIND[note.view === 'editor' ? 'video' : note.kind] || KIND.video);
    const head = note.status === 'done' ? w([`${kind} finished`, `${kind}生成完成`]) : note.status === 'failed' ? w([`${kind} failed`, `${kind}生成失败`]) : w([`${kind} stopped`, `${kind}生成中断`]);
    return {title: note.prompt || head, sub: note.prompt ? head : note.status === 'done' ? w(['In Creations', '已放进作品库']) : note.reason || w(['See why', '查看原因'])};
}
const ICON = {done: 'check', failed: 'alert', interrupted: 'alert', notice: 'alert'};

// ── The page part ──
let notes = null, panel = null, bells = new Set(), installs = {}, installTimer = 0, liveRows = [];
const store = () => notes ||= createNotes({storage: (() => { try { return localStorage; } catch { return null; } })()});

function syncBells() {
    const count = store().unread();
    for (const ref of [...bells]) {
        const bell = ref.deref(); if (!bell) { bells.delete(ref); continue; }
        const dot = bell.querySelector('.fv-bell-dot');
        dot.hidden = count === 0; dot.textContent = count > 9 ? '9+' : String(count);
        bell.dataset.unread = String(count);
        bell.dataset.live = String(liveRows.length > 0);
        const name = t('Notifications', '通知');
        bell.setAttribute('aria-label', count ? t(`${name}, ${count} unread`, `${name}，${count} 条未读`) : name);
    }
}

// The bell for the top bar of every view (shell.js puts it there).
export function notificationsButton(translate = t, className = 'fv-cmd-entry') {
    const button = document.createElement('button'); button.type = 'button'; button.className = `${className} fv-bell`;
    button.innerHTML = `${glyph('bell')}<span class="fv-bell-dot" hidden></span>`;
    button.setAttribute('aria-haspopup', 'dialog'); button.setAttribute('aria-expanded', 'false');
    Object.assign(button.dataset, {fvTipName: translate('Notifications', '通知'), fvTipUse: translate('What finished, failed or is installing', '完成、失败和安装中的任务')});
    button.onclick = () => panel?.isConnected && panel.matches(':popover-open') ? closeNotifications() : openNotifications({anchor: button});
    bells.add(new WeakRef(button));
    syncBells();
    return button;
}

// Back to a job the way the status panel does it: its view first, then that view is asked to show the job's stage.
export async function visitJob(job) {
    const view = job.view || (job.kind === 'image' ? 'image' : 'studio');
    try { if (globalThis.FreeVideoShell?.view?.() !== view) await globalThis.FreeVideoShell?.go?.(view); } catch { /* The view did not open: stay. */ }
    dispatchEvent(new CustomEvent('freevideo-task-stage-open', {detail: {view, id: job.id}}));
}
// A finished creation opens itself: a picture is selected in the image view's works, a video in Creations.
export async function openWork({kind = 'video', view = null, file = null, work = null, video = null} = {}) {
    if (kind === 'image' || view === 'image') {
        try { if (globalThis.FreeVideoShell?.view?.() !== 'image') await globalThis.FreeVideoShell?.go?.('image'); } catch { /* Stay. */ }
        if (file) dispatchEvent(new CustomEvent('freevideo-open-work', {detail: {kind: 'image', file}}));
        return true;
    }
    const selectedWork = work || video;
    if (!selectedWork) return false;
    const {openLibrary} = await import('./library.js');
    openLibrary(t, {selectedWork});
    return true;
}
async function openNote(note) {
    closeNotifications({focus: false});
    store().markRead(note.id);
    // A notice of a page message has nowhere to go; reading it was the point.
    if (note.type === 'toast') {
        if (note.setup) { const {openSetup} = await import('./setup.js'); openSetup({section: note.setup}); }
        return;
    }
    if (note.type === 'install') { await globalThis.FreeVideoShell?.go?.('image').catch(() => {}); return; }
    if (note.type === 'agent') {
        try { if (globalThis.FreeVideoShell?.view?.() !== 'autopilot') await globalThis.FreeVideoShell?.go?.('autopilot'); } catch { /* The view did not open: stay. */ }
        dispatchEvent(new CustomEvent('freevideo-agent-task-open', {detail: {id: note.job}}));
        return;
    }
    const row = jobs.get(note.job);
    if (note.status === 'done' && await openWork({kind: note.kind, view: note.view, file: note.file || row?.result?.file, work: note.work || row?.result?.id || row?.result?.work, video: note.video || row?.result?.video})) return;
    await visitJob({id: note.job, view: note.view || row?.view, kind: note.kind});
}

function liveItems() {
    const items = jobs.active().map(row => ({type: 'job', row, title: clean(row.meta?.text ?? row.prompt ?? '') || words(KIND[row.kind] || KIND.video),
        fraction: Number.isFinite(row.percent) ? row.percent : null, state: row.status === 'queued' ? t('Waiting', '排队中') : row.status === 'cancelling' ? t('Stopping', '正在停止') : t('Generating', '生成中')}));
    for (const [id, item] of Object.entries(installs)) {
        if (item.state !== 'running') continue;
        const gb = bytes => (bytes / 1e9).toFixed(1);
        items.push({type: 'install', id, title: t(`Installing ${item.label}`, `正在安装 ${item.label}`), fraction: item.total ? Math.min(1, item.done / item.total) : null,
            state: item.paused ? t('Paused while a video generates', '视频生成中，已暂停') : item.total ? `${gb(item.done)} / ${gb(item.total)} GB` : t('Downloading', '正在下载')});
    }
    return items;
}

function rowButton(className) { const row = document.createElement('button'); row.type = 'button'; row.className = className; return row; }
function drawPanel() {
    if (!panel) return;
    const rows = store().list();
    panel.querySelector('.fv-notes-read').disabled = !rows.some(row => !row.read);
    panel.querySelector('.fv-notes-clear').disabled = !rows.length;
    renderNotes(panel.querySelector('.fv-notes-list'));
}
// The list itself, into any element: the panel under the bell, or the notices section of a status panel.
function renderNotes(list, {live: withLive = true, limit = Infinity} = {}) {
    const live = withLive ? liveItems() : [], rows = store().list().slice(0, limit);
    const fragment = document.createDocumentFragment();
    const heading = text => { const head = document.createElement('div'); head.className = 'fv-notes-group'; head.textContent = text; fragment.append(head); };
    if (live.length) {
        heading(t('In progress', '进行中'));
        for (const item of live) {
            const row = rowButton('fv-note is-live'); row.dataset.id = item.type === 'install' ? `live:install:${item.id}` : `live:${item.row.id}`;
            row.innerHTML = `<span class="fv-note-icon">${glyph(item.type === 'install' ? 'download' : item.row?.kind === 'image' ? 'image' : 'film')}</span><span class="fv-note-text"><span class="fv-note-title"></span><span class="fv-note-sub"></span><span class="fv-note-bar"><i></i></span></span><span class="fv-note-pct"></span>`;
            row.querySelector('.fv-note-title').textContent = item.title;
            row.querySelector('.fv-note-sub').textContent = item.state;
            const bar = row.querySelector('.fv-note-bar i');
            if (item.fraction === null) row.querySelector('.fv-note-bar').classList.add('is-unknown'); else bar.style.transform = `scaleX(${item.fraction})`;
            row.querySelector('.fv-note-pct').textContent = item.fraction === null ? '' : `${Math.floor(item.fraction * 100)}%`;
            Object.assign(row.dataset, {fvTipName: item.title, fvTipUse: item.type === 'install' ? t('Open Images to see the download', '打开图像视图查看下载') : t('Back to this generation', '回到这次生成的画面')});
            row.onclick = async () => {
                closeNotifications({focus: false});
                if (item.type === 'install') { await globalThis.FreeVideoShell?.go?.('image').catch(() => {}); return; }
                await visitJob(item.row);
            };
            fragment.append(row);
        }
    }
    if (rows.length) {
        if (live.length) heading(t('Earlier', '更早'));
        for (const note of rows) {
            const said = noteWords(note);
            // A row is the notice (a button that opens it) and its own close button, side by side.
            const item = document.createElement('div'); item.className = 'fv-note-row'; item.setAttribute('role', 'listitem');
            item.dataset.id = note.id;
            const row = rowButton('fv-note'); row.dataset.id = note.id; row.dataset.status = note.status;
            if (!note.read) { row.dataset.unread = 'true'; item.dataset.unread = 'true'; }
            row.innerHTML = `<span class="fv-note-icon">${glyph(note.type === 'install' ? (note.status === 'done' ? 'download' : 'alert') : ICON[note.status] || 'check')}</span><span class="fv-note-text"><span class="fv-note-title"></span><span class="fv-note-sub"></span></span><span class="fv-note-when"></span>`;
            row.querySelector('.fv-note-title').textContent = said.title;
            row.querySelector('.fv-note-sub').textContent = said.sub;
            row.querySelector('.fv-note-when').textContent = ago(note.at);
            Object.assign(row.dataset, {fvTipName: said.title.slice(0, 40), fvTipUse: note.status === 'done' ? t('Open the result', '打开生成结果') : note.type === 'install' ? t('Open Images', '打开图像视图') : t('See why it failed and what to do', '查看失败原因和可以做的事'), fvTipKey: 'Enter'});
            row.onclick = () => openNote(note);
            row.addEventListener('keydown', event => { if ((event.key === 'Delete' || event.key === 'Backspace') && !list.closest('.fv-notes')) { event.preventDefault(); store().remove(note.id); } });
            row.oncontextmenu = event => {
                event.preventDefault();
                openContextMenu({x: event.clientX, y: event.clientY}, [
                    {id: 'open', label: ['Open', '打开'], icon: 'arrow', use: note.status === 'done' ? ['Go back to the result', '回到生成结果'] : ['See why and what to do', '查看原因和可以做的事'], run: () => openNote(note)},
                    {id: 'read', label: ['Mark as read', '标为已读'], icon: 'check', disabled: note.read ? ['Already read; nothing to clear', '已经读过，无需再标'] : '', run: () => store().markRead(note.id)},
                    '-',
                    {id: 'remove', label: ['Remove', '移除'], icon: 'close', key: 'Delete', run: () => store().remove(note.id)},
                ], {returnFocus: row});
            };
            const close = document.createElement('button'); close.type = 'button'; close.className = 'fv-note-x'; close.innerHTML = glyph('close', 14);
            close.setAttribute('aria-label', t('Remove this notice', '移除这条通知'));
            Object.assign(close.dataset, {fvTipName: t('Remove', '移除'), fvTipUse: t('Take this notice off the list', '把这条通知从列表移除'), fvTipKey: 'Delete'});
            close.onclick = event => {
                event.stopPropagation();
                const rowsNow = [...list.querySelectorAll('.fv-note:not(.is-live)')], at = rowsNow.indexOf(row), next = rowsNow[at + 1] || rowsNow[at - 1];
                store().remove(note.id);
                (next && list.querySelector(`.fv-note[data-id="${CSS.escape(next.dataset.id)}"]`) || panel?.querySelector('.fv-notes-clear:not(:disabled), .fv-notes-read'))?.focus({preventScroll: true});
            };
            item.append(row, close);
            fragment.append(item);
        }
    }
    // Cleared while something still runs: one quiet line under the running rows.
    if (live.length && !rows.length) { const none = document.createElement('div'); none.className = 'fv-notes-none'; none.textContent = t('No other notices', '没有其他通知'); fragment.append(none); }
    if (!live.length && !rows.length) {
        const empty = document.createElement('div'); empty.className = 'fv-notes-empty';
        empty.innerHTML = `<span class="fv-notes-empty-mark">${glyph('bell', 24)}</span><b></b><span></span>`;
        empty.querySelector('b').textContent = t('Nothing new', '没有新通知');
        empty.querySelector('span:last-child').textContent = t('Finished and failed generations show up here.', '生成完成或失败时会出现在这里。');
        fragment.append(empty);
    }
    const had = list.contains(document.activeElement), focused = document.activeElement?.closest?.('.fv-note')?.dataset.id;
    list.replaceChildren(fragment);
    if (had) (focused && list.querySelector(`.fv-note[data-id="${CSS.escape(focused)}"]`) || list.querySelector('.fv-note'))?.focus({preventScroll: true});
}

let anchorButton = null;
function place() {
    if (!panel) return;
    const width = Math.min(360, innerWidth - 16);
    panel.style.width = `${width}px`;
    const box = anchorButton?.isConnected && anchorButton.getClientRects().length ? anchorButton.getBoundingClientRect() : null;
    const top = box ? box.bottom + 8 : (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--fv-header-h')) || 56) + 8;
    const left = box ? Math.min(innerWidth - 8 - width, Math.max(8, box.right - width)) : innerWidth - 8 - width - 8;
    panel.style.left = `${Math.round(left)}px`; panel.style.top = `${Math.round(top)}px`;
    panel.style.maxHeight = `${Math.round(innerHeight - top - 16)}px`;
}

function buildPanel() {
    panel = document.createElement('div');
    panel.className = 'fv-notes fv-glass fv-g2'; panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', t('Notifications', '通知'));
    panel.dataset.fvTipScope = ''; panel.dataset.fvCommandLayer = '';
    if (panel.showPopover) panel.popover = 'manual';
    panel.innerHTML = `<header class="fv-notes-head"><h2></h2><button type="button" class="fv-notes-read">${glyph('check')}</button><button type="button" class="fv-notes-clear"></button></header><div class="fv-notes-list" role="list"></div>`;
    panel.querySelector('h2').textContent = t('Notifications', '通知');
    const read = panel.querySelector('.fv-notes-read'), clear = panel.querySelector('.fv-notes-clear');
    read.setAttribute('aria-label', t('Mark all as read', '全部标为已读'));
    Object.assign(read.dataset, {fvTipName: t('Mark all as read', '全部标为已读'), fvTipUse: t('Clear the dot on the bell', '清掉铃铛上的数字'), fvTipWhy: t('Everything is read', '都已读过')});
    clear.textContent = t('Clear all', '全部清除');
    Object.assign(clear.dataset, {fvTipName: t('Clear all', '全部清除'), fvTipUse: t('Empty this list; creations stay', '清空列表，作品仍在作品库'), fvTipWhy: t('The list is empty', '列表是空的')});
    read.onclick = () => store().markRead();
    clear.onclick = () => { store().clear(); panel.querySelector('.fv-notes-read')?.focus({preventScroll: true}); };
    panel.addEventListener('keydown', event => {
        const rows = [...panel.querySelectorAll('.fv-note')], at = rows.indexOf(document.activeElement);
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeNotifications(); }
        else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); const next = rows[(at + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length] || rows[0]; next?.focus(); }
        else if ((event.key === 'Delete' || event.key === 'Backspace') && at >= 0 && rows[at].dataset.id && !rows[at].classList.contains('is-live')) { event.preventDefault(); const next = rows[at + 1] || rows[at - 1]; store().remove(rows[at].dataset.id); panel.querySelector(`.fv-note[data-id="${CSS.escape(next?.dataset.id || '')}"]`)?.focus(); }
        else if (event.key === 'Tab') {
            // Focus stays in the panel while it is open; Esc or a click outside leaves.
            const all = [...panel.querySelectorAll('button:not(:disabled)')]; if (!all.length) return;
            const i = all.indexOf(document.activeElement); event.preventDefault();
            all[event.shiftKey ? (i <= 0 ? all.length - 1 : i - 1) : (i + 1) % all.length].focus();
        }
    });
}

let outside = null, returnTo = null, panelSerial = 0;
export function openNotifications({anchor = null} = {}) {
    if (!browser) return;
    if (!panel) buildPanel();
    panelSerial++;
    anchorButton = anchor || [...document.querySelectorAll('dialog[open] .fv-bell, .fv-view-navigation .fv-bell')].filter(bell => bell.getClientRects().length).at(-1) || null;
    returnTo = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    floatingHost().append(panel);
    // A fade from an earlier close may still hold the panel at nothing (an element out of the page lists none).
    for (const running of panel.getAnimations?.() || []) running.cancel();
    try { if (panel.matches(':popover-open')) panel.hidePopover(); panel.showPopover?.(); } catch { /* Shown as a fixed element. */ }
    drawPanel(); place();
    anchorButton?.setAttribute('aria-expanded', 'true');
    if (!reducedMotion() && panel.animate) panel.animate([{opacity: 0, transform: 'translateY(-4px) scale(.985)'}, {opacity: 1, transform: 'none'}], {duration: 180, easing: EASE});
    checkInstalls();
    requestAnimationFrame(() => (panel.querySelector('.fv-note') || panel.querySelector('button:not(:disabled)'))?.focus({preventScroll: true}));
    setTimeout(() => {
        outside = event => { if (!panel.contains(event.target) && !event.target.closest?.('.fv-bell') && !event.target.closest?.('.fv-cm')) closeNotifications({focus: false}); };
        document.addEventListener('pointerdown', outside, true);
    }, 0);
    addEventListener('resize', place);
}
export function closeNotifications({focus = true} = {}) {
    if (!panel?.isConnected) return;
    document.removeEventListener('pointerdown', outside, true); removeEventListener('resize', place);
    anchorButton?.setAttribute('aria-expanded', 'false');
    // What was shown has been seen.
    store().markRead();
    const current = panel, serial = ++panelSerial;
    // The fade holds its end; it goes with the panel, or the next opening would end on it (invisible).
    // Opened again meanwhile: the newer opening keeps it.
    const done = () => { if (serial !== panelSerial) return; for (const running of current.getAnimations?.() || []) running.cancel(); try { current.hidePopover?.(); } catch { /* Already hidden. */ } current.remove(); };
    if (reducedMotion() || !current.animate) done();
    else current.animate([{opacity: 1}, {opacity: 0, transform: 'translateY(-4px)'}], {duration: 160, easing: EASE, fill: 'forwards'}).finished.then(done, done);
    if (focus && returnTo?.isConnected) returnTo.focus({preventScroll: true});
}
export const notificationsOpen = () => Boolean(panel?.isConnected && panel.matches(':popover-open'));

// For a status panel that shows notices in its own section (the top bar spec puts them under the status capsule):
// mountNotices(element, {live, limit}) fills the element and keeps it current; noticeState() and onNotices(fn) give
// the unread count (and unread failures) for a badge. Opening a row works as it does in the panel.
const mounts = new Set();
export function mountNotices(element, options = {}) {
    const entry = {element, options}; mounts.add(entry);
    element.classList.add('fv-notes-list'); element.setAttribute('role', 'list'); element.dataset.fvTipScope = '';
    renderNotes(element, options);
    return {refresh: () => renderNotes(element, options), dispose() { mounts.delete(entry); }};
}
export function noticeState() {
    const rows = store().list();
    return {unread: rows.filter(row => !row.read).length, failed: rows.filter(row => !row.read && row.status !== 'done' && row.status !== 'notice').length, running: jobs.active().length};
}
const noticeListeners = new Set();
export function onNotices(fn) { noticeListeners.add(fn); fn(noticeState()); return () => noticeListeners.delete(fn); }
export const markNoticesRead = () => store().markRead();
function refreshMounts() {
    for (const entry of [...mounts]) { if (!entry.element.isConnected) { mounts.delete(entry); continue; } renderNotes(entry.element, entry.options); }
    const state = noticeState();
    for (const fn of [...noticeListeners]) try { fn(state); } catch { /* One listener never stops the others. */ }
}

// ── The image models' install, read every 30 s (every 3 s while one downloads, never while the tab is hidden) ──
let infoURL = null;
async function checkInstalls() {
    clearTimeout(installTimer);
    if (!browser) return;
    try {
        if (!document.hidden) {
            infoURL ||= (await import('../../scripts/api.js')).api;
            const response = await infoURL.fetchApi('/freevideo/image-gen', {cache: 'no-store'});
            if (response.ok) {
                const next = installState(await response.json());
                for (const note of installChanges(installs, next)) store().add(note);
                installs = next; refreshLive();
            }
        }
    } catch { /* The worker is not there yet: try again later. */ }
    const running = Object.values(installs).some(item => item.state === 'running');
    installTimer = setTimeout(checkInstalls, running ? 3000 : 30000);
}

function refreshLive() { liveRows = liveItems(); syncBells(); if (notificationsOpen()) drawPanel(); refreshMounts(); }

// ── toast(): notices and undo bars (glob.css §9) ──
// toast(text, {tone, detail, icon, ms, sticky, keep: {id, setup, title, sub}, action: {label, run, use, key}}) → {close()}
//   keep: a notice that must not be missed is also kept in the notification list until closed there; setup is the
//   settings section its row opens; title and sub, when given, are the row's shorter words (the toast keeps its text).
//   tone: 'ok' | 'info' | 'caution' | 'warn' | 'fail'; detail: a second, quieter part of the line.
//   Lives 5 s, 8 s with an action (an undo); sticky ones (failures by default) stay until closed.
// One shows at a time, in the top bar's right column (a bottom strip above the view's own bar under 720 px, at
// --fv-bottombar-top, which each view writes on its dialog); the rest wait their turn and the visible one shows "+N".
// Every notice has a close button; Esc closes the visible one; a pointer or focus on it holds its time.
// A failure is also kept in the notification list.
export const TOAST_LIFE = 5000, UNDO_LIFE = 8000;
const TONE_ICON = {ok: 'check', info: 'check', caution: 'alert', warn: 'alert', fail: 'alert'};
// What a call asks for, with the defaults filled in (the tests read this).
export function toastSpec(message, options = {}) {
    const tone = options.tone || 'info';
    const action = options.action && typeof options.action.run === 'function' ? options.action : null;
    // Nothing stays for good: a failure (or a notice asked to stay) lives 8 s, held while the pointer is on it, and
    // is kept in the notification list to close there.
    const long = tone === 'fail' || options.sticky === true;
    const given = Number.isFinite(options.ms) && options.ms > 0 ? options.ms : 0;
    return {text: words(message), detail: words(options.detail || ''), tone, icon: options.icon || TONE_ICON[tone] || 'check', action,
        keep: options.keep && typeof options.keep === 'object' ? {id: String(options.keep.id || ''), setup: String(options.keep.setup || ''), title: String(options.keep.title || ''), sub: String(options.keep.sub || '')} : null,
        sticky: false, ms: long ? Math.max(given, UNDO_LIFE) : given || (action ? UNDO_LIFE : TOAST_LIFE)};
}
// The queue, apart from the page: show(next) when the visible one goes; "+N" is the waiting count.
export function createToastQueue({show = () => {}, hide = () => {}} = {}) {
    const waiting = []; let current = null;
    const queue = {
        push(spec) { const item = {spec, closed: false}; if (current) { waiting.push(item); show(current, waiting.length); } else { current = item; show(item, 0); } return item; },
        close(item = current) {
            if (!item || item.closed) return;
            item.closed = true;
            if (item !== current) { waiting.splice(waiting.indexOf(item), 1); if (current) show(current, waiting.length); return; }
            hide(item);
            current = waiting.shift() || null;
            if (current) show(current, waiting.length);
        },
        current: () => current, waiting: () => waiting.length,
    };
    return queue;
}

let toastRegion = null, toastItem = null, toastTimer = 0, toastLeft = 0, toastStarted = 0, toastHeld = false;
const queue = createToastQueue({show: showToast, hide: hideToast});

function region() {
    if (toastRegion) return toastRegion;
    toastRegion = document.createElement('div'); toastRegion.className = 'fv-toasts'; toastRegion.dataset.fvTipScope = '';
    toastRegion.setAttribute('role', 'region'); toastRegion.setAttribute('aria-label', t('Notices', '提醒'));
    if (toastRegion.showPopover) toastRegion.popover = 'manual';
    return toastRegion;
}
// The region follows the view in front (a modal view makes everything outside it inert; the view writes the
// bottom bar's place on its own dialog, which the region inherits there).
function placeRegion() {
    const box = region(), host = floatingHost();
    if (box.parentNode !== host) host.append(box);
    try { if (!box.matches(':popover-open')) box.showPopover?.(); } catch { /* Shown as a fixed element. */ }
}
function lifeRing() {
    const ring = document.createElement('span'); ring.className = 'fv-toast-life'; ring.setAttribute('aria-hidden', 'true');
    ring.innerHTML = '<svg viewBox="0 0 20 20"><circle class="bg" cx="10" cy="10" r="8"/><circle class="fg" cx="10" cy="10" r="8"/></svg>';
    return ring;
}
function buildToast(item, more) {
    const {spec} = item;
    const box = document.createElement('div'); box.className = 'fv-toast fv-glass fv-g3';
    box.dataset.tone = spec.tone; box.setAttribute('role', spec.tone === 'fail' ? 'alert' : 'status');
    if (spec.tone === 'fail') box.classList.add('is-fail');
    const icon = document.createElement('span'); icon.className = 'fv-toast-ic'; icon.innerHTML = glyph(spec.icon); box.append(icon);
    const msg = document.createElement('span'); msg.className = 'fv-toast-msg'; msg.textContent = spec.text;
    if (spec.detail) { const small = document.createElement('small'); small.textContent = spec.detail; msg.append(small); }
    cutTip(msg, [spec.text, spec.detail].filter(Boolean).join(' · '));
    box.append(msg);
    if (spec.action) {
        const act = document.createElement('button'); act.type = 'button'; act.className = 'fv-toast-act';
        act.textContent = words(spec.action.label || ['Undo', '撤销']);
        Object.assign(act.dataset, {fvTipName: act.textContent, fvTipUse: words(spec.action.use || ['Take back what just happened', '撤回刚才的操作'])});
        if (spec.action.key) act.dataset.fvTipKey = spec.action.key;
        act.onclick = () => { queue.close(item); try { spec.action.run(); } catch (error) { console.warn('FreeVideo notice action:', error); } };
        box.append(act);
    }
    if (more > 0) { const chip = document.createElement('span'); chip.className = 'fv-toast-more'; chip.textContent = `+${more}`; setTip(chip, {name: t(`${more} more after this`, `后面还有 ${more} 条`)}); box.append(chip); }
    if (!spec.sticky) { const ring = lifeRing(); ring.style.setProperty('--life', `${spec.ms}ms`); box.append(ring); }
    const close = document.createElement('button'); close.type = 'button'; close.className = 'fv-toast-x'; close.innerHTML = glyph('close', 14);
    close.setAttribute('aria-label', t('Close', '关闭'));
    Object.assign(close.dataset, {fvTipName: t('Close', '关闭'), fvTipUse: t('Close this notice now', '现在收起这条提醒'), fvTipKey: 'Esc'});
    close.onclick = () => queue.close(item);
    box.append(close);
    // A pointer or keyboard focus on it holds its time; it goes on from where it was.
    const hold = () => { if (toastItem !== item || toastHeld) return; toastHeld = true; clearTimeout(toastTimer); toastLeft -= performance.now() - toastStarted; box.classList.add('is-held'); };
    const resume = () => { if (toastItem !== item || !toastHeld || box.matches(':hover') || box.contains(document.activeElement)) return; toastHeld = false; box.classList.remove('is-held'); runTimer(item); };
    box.addEventListener('pointerenter', hold); box.addEventListener('focusin', hold);
    box.addEventListener('pointerleave', resume); box.addEventListener('focusout', () => setTimeout(resume, 0));
    return box;
}
function runTimer(item) {
    clearTimeout(toastTimer);
    if (item.spec.sticky) return;
    toastStarted = performance.now();
    toastTimer = setTimeout(() => queue.close(item), Math.max(0, toastLeft));
}
function showToast(item, more) {
    placeRegion();
    const box = region();
    const fresh = toastItem !== item;
    const old = box.querySelector('.fv-toast:not(.is-leaving)');
    const next = buildToast(item, more);
    if (!fresh && old) {
        // The same notice, only the "+N" changed: swap without moving and keep the ring where it was.
        const ring = old.querySelector('.fv-toast-life'), nextRing = next.querySelector('.fv-toast-life');
        if (ring && nextRing) nextRing.replaceWith(ring);
        old.replaceWith(next);
        if (toastHeld) next.classList.add('is-held');
        stackLong(next); toastResized();
        return;
    }
    if (old) old.remove();
    toastItem = item; toastHeld = false; toastLeft = item.spec.ms;
    box.append(next);
    stackLong(next);
    if (!reducedMotion() && next.animate) next.animate([{opacity: 0, transform: 'scaleX(.6)'}, {opacity: 1, transform: 'none'}], {duration: 200, easing: EASE});
    runTimer(item);
    toastResized();
    if (item.spec.tone === 'fail') store().add({id: `toast:${Date.now()}`, type: 'toast', status: 'failed', title: item.spec.text, reason: item.spec.detail, at: Date.now()});
    else if (item.spec.keep) store().add({id: item.spec.keep.id || `toast:${Date.now()}`, type: 'toast', status: 'notice', title: item.spec.keep.title || item.spec.text, reason: item.spec.keep.sub || item.spec.detail || (item.spec.action ? words(item.spec.action.label) : ''), setup: item.spec.keep.setup || null, at: Date.now()});
}
// On a phone strip a message that wraps beside its controls takes its own row (notifications.css, is-stacked).
function stackLong(el) {
    el.classList.remove('is-stacked');
    const msg = el.querySelector('.fv-toast-msg');
    if (!msg || !matchMedia('(max-width: 719px)').matches) return;
    if (msg.offsetHeight > 1.5 * (parseFloat(getComputedStyle(msg).lineHeight) || 18)) el.classList.add('is-stacked');
}
// A view that keeps a slot for the strip (the studio on a phone) hears when the visible notice comes, goes or changes
// height, and reads it from toastHeight().
export function toastHeight() { return toastRegion?.querySelector('.fv-toast:not(.is-leaving)')?.offsetHeight || 0; }
function toastResized() { dispatchEvent(new CustomEvent('freevideo-toast-resize', {detail: {height: toastHeight()}})); }
function hideToast(item) {
    if (toastItem !== item) return;
    clearTimeout(toastTimer); toastItem = null; toastHeld = false;
    const box = region().querySelector('.fv-toast:not(.is-leaving)');
    const done = () => {
        box?.remove();
        if (!toastItem && toastRegion && !toastRegion.querySelector('.fv-toast')) { try { toastRegion.hidePopover?.(); } catch { /* Already hidden. */ } toastRegion.remove(); }
        toastResized();
    };
    if (!box) { done(); return; }
    box.classList.add('is-leaving');
    if (reducedMotion() || !box.animate) done(); else box.animate([{opacity: 1}, {opacity: 0}], {duration: 160, easing: EASE, fill: 'forwards'}).finished.then(done, done);
}

export function toast(message, options = {}) {
    if (!browser || !message) return {close() {}};
    const item = queue.push(toastSpec(message, options));
    return {close: () => queue.close(item)};
}
// Views check this before sending notices with an undo (it was added after the plain ones).
toast.actions = true;
toast.queue = true;
export const closeToast = () => queue.close();

// Esc closes the visible notice when nothing above it (a menu, a dialog over the view, a command-layer panel)
// takes the key first; then the view's own Esc is left for the next press.
function onToastEscape(event) {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    // The notice panel is the top layer while it is open, wherever focus went (a redraw can take it away).
    if (notificationsOpen() && !document.querySelector('.fv-cm:popover-open')) { event.preventDefault(); event.stopImmediatePropagation(); closeNotifications(); return; }
    if (!toastItem) return;
    const top = [...document.querySelectorAll('dialog[open]')].filter(dialog => dialog.dataset.closing !== 'true').at(-1);
    const view = globalThis.FreeVideoShell?.viewDialog?.(globalThis.FreeVideoShell?.view?.());
    if (top && top !== view) return;
    if (document.querySelector('.fv-cm:popover-open, .fv-notes:popover-open')) return;
    if (event.target instanceof Element && event.target.closest('[aria-expanded=true], [popover]:popover-open:not(.fv-toasts)')) return;
    event.preventDefault(); event.stopImmediatePropagation();
    queue.close();
}

export function installNotifications() {
    if (!browser || globalThis.FreeVideoNotifications?.installed) return;
    store().subscribe(() => { syncBells(); if (notificationsOpen()) drawPanel(); refreshMounts(); });
    jobs.subscribe((list, change) => {
        if (change?.row) { const note = noteFromJob(change.row, change.previous); if (note) store().add(note); }
        else if (!change) for (const row of list) { const note = noteFromJob(row, null); if (note) store().add(note); }
        refreshLive();
    });
    // The library and the image view say when creations are deleted (and when an undo brings them back).
    addEventListener('freevideo-works-removed', event => { const works = event.detail || {}; store().hideWorks({ids: works.ids || [], paths: works.paths || []}); });
    addEventListener('freevideo-works-restored', event => { const works = event.detail || {}; store().restoreWorks({ids: works.ids || [], paths: works.paths || []}); });
    setTimeout(checkInstalls, 5000);
    addEventListener('keydown', onToastEscape, true);
    // Times like "3 min ago" move on while the panel is open.
    setInterval(() => { if (notificationsOpen()) drawPanel(); }, 30000);
    globalThis.FreeVideoNotifications = {installed: true, openNotifications, closeNotifications, notificationsButton, toast, closeToast, mountNotices, noticeState, onNotices, markNoticesRead};
}
if (browser) installNotifications();
