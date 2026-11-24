// The floating window: the generation the user left, in every view and room. It reads the page-wide job store
// (task_store.js); it never owns a job. Leaving a view does not stop anything; Stop asks first.
// Every view is a modal <dialog> today, and everything outside the topmost modal is inert, so the window is a
// manual popover that lives inside the topmost open modal dialog (or the body) and moves along as views change.
import { isNetworkTypeError, connectionLostText } from './browser_extension.js';
import { api } from '../../scripts/api.js';
import { app } from '../../scripts/app.js';
import { jobs, imageSource, progressSource, chainSource, queueSource, journalSource, ACTIVE, FINAL } from './task_store.js';
import { errorText, failureAdvice } from './error_panel.js';
import { createImageGenClient, imageGenError, imageReportBar } from './image_generation.js';
import { saveGeneratedImage } from './image_library.js';
import { isChinese, translator } from './i18n.js';

const cn = isChinese();
const t = translator(cn);
if (typeof document !== 'undefined') {
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('./task_float.css', import.meta.url).href; document.head.append(css);
}

// ---------- sources: started once per page, by the first view that needs them or by this module on load ----------
let sourcesStarted = false;
const post = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
async function sent(response) { if (!response.ok) throw Object.assign(new Error('cancel_failed'), {status: response.status}); return response.json().catch(() => ({})); }
const pictureURL = file => { const at = file.lastIndexOf('/'); return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(at + 1), subfolder: at < 0 ? '' : file.slice(0, at), type: 'input'})); };
const outputURL = file => api.apiURL('/view?' + new URLSearchParams({filename: file.split('/').pop(), subfolder: file.split('/').slice(0, -1).join('/'), type: 'output'}));

export function startJobSources() {
    if (sourcesStarted) return; sourcesStarted = true;
    let storage = null; try { storage = globalThis.localStorage; } catch { /* Private windows keep jobs in memory only. */ }
    imageSource(jobs, {client: createImageGenClient(), storage});
    // The request the engine runs on each node now, from its own events: a plain (one graphics card) run's progress
    // names no request, so this keeps every queued video a row of its own (task_store.js progressSource).
    const runningOn = new Map();
    api.addEventListener('executing', event => {
        const detail = event.detail, prompt = detail && typeof detail === 'object' ? detail.prompt_id : null;
        if (!prompt) return;
        if (detail.node != null) runningOn.set(String(detail.node), prompt);
        else for (const [node, id] of [...runningOn]) if (id === prompt) runningOn.delete(node);
    });
    let queue = null;
    const video = progressSource(jobs, {target: window, promptOf: node => runningOn.get(String(node)) || queue?.runningOn(node) || null,
        cancel: row => api.fetchApi('/freevideo/cancel', post({prompt_id: row.prompt_id, node_id: String(row.node)})).then(sent)});
    // Videos waiting in the engine's queue (this page's own requests): listed with their place; read again whenever the
    // engine says its queue changed, and every 3 s while one waits or runs.
    queue = queueSource(jobs, {clientId: () => api.clientId || api.initialClientId || null,
        fetchJson: async path => { const response = await api.fetchApi(path, {cache: 'no-store'}); if (!response.ok) throw new Error('queue'); return response.json(); }});
    for (const type of ['status', 'promptQueued', 'execution_start', 'execution_success', 'execution_interrupted', 'execution_error']) api.addEventListener(type, () => { void queue.refresh(); });
    // The page's own progress connection may have read the snapshot before this module loaded; read it once more here,
    // after the queue (so a video already running is keyed by its request).
    void queue.refresh().then(() => api.fetchApi('/freevideo/progress', {cache: 'no-store'})).then(response => response.ok ? response.json() : null).then(value => {
        // Only this workflow's generate nodes, as the live stream (the Agent's own runs show in its view).
        const ours = message => !app.graph?._nodes?.length || Boolean(app.graph.getNodeById?.(message.node));
        for (const message of value?.progress || []) if (message && ours(message) && !message.result && !['complete', 'failed', 'cancelled'].includes(message.phase))
            video.receive({...message, received_at: Date.now() - Math.max(0, Number(message.age_seconds) || 0) * 1000});
    }).catch(() => {});
    chainSource(jobs, {fetchJson: async path => { const response = await api.fetchApi(path, {cache: 'no-store'}); if (!response.ok) throw new Error('chains'); return response.json(); },
        cancel: row => api.fetchApi(`/freevideo/chain/${encodeURIComponent(row.id.slice('chain:'.length))}/cancel`, post({})).then(sent)});
    // Jobs that ended while this page was closed or the engine restarted, from the server's own record.
    journalSource(jobs, {storage, fetchJson: async path => { const response = await api.fetchApi(path, {cache: 'no-store'}); if (!response.ok) throw new Error('jobs'); return response.json(); }});
    // A finished picture is filed by the store, not by the page that started it, so it lands even with that page closed.
    // (Until the engine files it itself, a tab closed mid-run still misses the card; the picture is in the history.)
    // Two tabs of the same page both follow a picture started in one of them; only the first to see it end files it.
    const fileOnce = id => {
        try {
            const key = 'freevideo.jobs.filed', now = Date.now(), filed = JSON.parse(localStorage.getItem(key) || '{}');
            if (filed[id]) return false;
            filed[id] = now; for (const [other, at] of Object.entries(filed)) if (now - at > 7 * 864e5) delete filed[other];
            localStorage.setItem(key, JSON.stringify(filed)); return true;
        } catch { return true; }
    };
    jobs.on('done', row => {
        // A stand-in's picture, and a draft from the image editor (filed only when the user saves it), stay out.
        if (row.kind === 'image' && row.result?.file && !row.meta?.standIn && !row.meta?.draft && fileOnce(row.id))
            void saveGeneratedImage(api, {url: pictureURL(row.result.file), file: row.result.file, prompt: row.meta?.text ?? row.prompt ?? ''})
                .then(() => window.dispatchEvent(new CustomEvent('freevideo-image-saved', {detail: {file: row.result.file, job: row}}))).catch(() => {});
        window.dispatchEvent(new CustomEvent('freevideo-job-done', {detail: {job: row}}));
    });
    // Deleting works in the library or the image view takes their finished jobs off the window and the list; undoing
    // the delete brings them back (detail: {ids, paths, undoable}).
    window.addEventListener('freevideo-works-removed', event => jobs.hideWorks(event.detail || {}));
    window.addEventListener('freevideo-works-restored', event => jobs.showWorks(event.detail || {}));
    void jobs.refresh();
}

// ---------- where the user is ----------
const isModal = dialog => { try { return dialog.matches(':modal'); } catch { return dialog.open; } };
const openedAt = new WeakMap(); let openSerial = 0;
const live = dialog => dialog.isConnected && dialog.open && dialog.dataset.closing !== 'true';
// Open modal dialogs, oldest first (the last one is on top of the top layer).
function modals() {
    return [...document.querySelectorAll('dialog[open]')].filter(d => live(d) && isModal(d) && !d.classList.contains('sgf'))
        .sort((a, b) => (openedAt.get(a) || 0) - (openedAt.get(b) || 0));
}
const VIEW_CLASSES = [['fv-image', 'image'], ['fv-editor', 'editor'], ['fv-autopilot', 'autopilot'], ['fv-library', 'library']];
// The image editor opens as its own dialog over a view; it is a view in front, not a room to close.
const viewOfDialog = dialog => !dialog ? 'nodes' : dialog.classList.contains('fv-image-editor') ? 'image-editor'
    : dialog.classList.contains('fv-studio') ? (VIEW_CLASSES.find(([name]) => dialog.classList.contains(name))?.[1] || 'studio') : 'room';
const isViewDialog = dialog => dialog.classList.contains('fv-image-editor') || (dialog.classList.contains('fv-studio') && viewOfDialog(dialog) !== 'library');
const shell = () => globalThis.window?.FreeVideoShell || null;
// The view on screen and whether something covers it (a library, settings, any other dialog).
// The open dialogs decide (the shell names the new view only once its switch has finished); with no view dialog open
// it is the node view, or whatever the shell says.
function where() {
    const stack = modals(), top = stack.at(-1) || null;
    const views = stack.filter(isViewDialog);
    const view = views.length ? viewOfDialog(views.at(-1)) : (shell()?.view?.() || 'nodes');
    return {view, covered: Boolean(top && top !== views.at(-1)), top};
}
// A job started in the image editor belongs to the image view once the editor is closed (its picture is in the works).
const editorOpen = () => Boolean(document.querySelector('dialog.fv-image-editor[open]'));
const viewFor = row => row.view === 'image-editor' && !editorOpen() ? 'image' : row.view || (row.kind === 'image' ? 'image' : 'studio');
// At home: the job's own generation screen is the one in front, so the window has nothing to add.
// A view in front without a marked stage is taken to show its own job (the creation panel shows its video's progress).
// A view can show something else in its stage while its job runs (the image view showing another picture): it says so
// with freevideo-task-stage-leave / -return, and until it returns the job is away from home.
const away = new Set();
function atHome(row, place = where()) {
    if (place.covered || viewFor(row) !== place.view || away.has(place.view)) return false;
    const stage = document.querySelector(`[data-fv-task-stage="${viewFor(row)}"]`);
    return !stage || stage.getClientRects().length > 0;
}
function host() {
    const own = shell()?.overlayHost?.(); if (own) return own;
    return modals().at(-1) || document.body;
}

// ---------- going back ----------
async function goTo(view) {
    if (view === 'image-editor' && !editorOpen()) view = 'image';
    // Rooms over the view (the library, settings) close first, so the view's own switch can be pressed. A view dialog
    // (the image editor among them) is never closed here: it leaves through its own flow.
    for (const dialog of modals().reverse()) {
        if (isViewDialog(dialog)) break;
        dialog.close();
    }
    if (view === 'image-editor') return;
    if (where().view === view) return;
    const own = shell()?.go;
    if (own) { try { await own(view); return; } catch { /* the shell had no switch to press: press it here */ } }
    const top = modals().filter(d => d.classList.contains('fv-studio')).at(-1);
    const button = top ? top.querySelector(`.fv-view-switch [data-view="${view}"]`) : document.querySelector(`.fv-view-navigation [data-view="${view}"]`);
    button?.click();
}
const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
async function stageAppears(view, limit = 2400) {
    const end = performance.now() + limit; let last = '';
    while (performance.now() < end) {
        const place = where(), stage = document.querySelector(`[data-fv-task-stage="${view}"]`);
        if (place.view === view && !place.covered && stage) {
            // Wait out the view's own opening motion: the picture lands where the stage settles (same box two frames running).
            const box = stage.getBoundingClientRect(), dialog = stage.closest('dialog');
            const moving = dialog?.getAnimations?.({subtree: true}).some(a => a.playState === 'running' && a.effect?.target?.contains?.(stage));
            const key = [box.left, box.top, box.width, box.height].map(Math.round).join();
            if (box.width > 0 && !moving && key === last) return stage;
            last = key;
        }
        await frame();
    }
    return null;
}

// ---------- text ----------
const KIND = {image: ['Image', '生成图像'], video: ['Video', '生成视频'], chain: ['Video', '生成视频']};
const VIEW_NAMES = {image: ['Images', '图像视图'], studio: ['Create', '创作面板'], editor: ['Edit', '剪辑视图'], autopilot: ['Autopilot', 'AI 全自动'], nodes: ['Nodes', '节点视图']};
const kindText = row => viewFor(row) === 'editor' ? t('Edit', '剪辑生成') : t(...(KIND[row.kind] || KIND.video));
const clock = seconds => { const s = Math.max(0, Math.floor(seconds)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
// Short enough for the card's one line next to the count and Stop.
const etaText = seconds => !Number.isFinite(seconds) ? '' : seconds >= 60 ? t(`About ${Math.ceil(seconds / 60)} min left`, `还需约 ${Math.ceil(seconds / 60)} 分钟`) : t('Under 1 min left', '不到 1 分钟');
const elapsedText = row => `${t('Elapsed', '已用时')} ${clock((Date.now() - (row.started_at || row.created_at || Date.now())) / 1000)}`;
const percentOf = row => Math.floor(100 * (Number.isFinite(row.percent) ? row.percent : 0));
const part = row => row.meta?.parts > 1 && row.meta?.part ? t(` · part ${row.meta.part}/${row.meta.parts}`, ` · 第 ${row.meta.part}/${row.meta.parts} 段`) : '';
function stateWord(row) {
    if (row.status === 'running') return null;
    return {submitting: t('Starting', '正在开始'), queued: t('Queued', '排队中'), cancelling: t('Stopping', '正在停止'), done: t('Done', '已完成'),
        failed: t('Interrupted', '已中断'), interrupted: t('Interrupted', '已中断'), cancelled: t('Stopped', '已停止')}[row.status];
}
function reasonShort(row) {
    const code = row.failure?.code;
    if (code === 'engine_closed') return t('The engine was closed', '引擎已关闭');
    if (code === 'engine_restarted' || row.status === 'interrupted') return t('The engine restarted', '引擎已重启');
    return t('Open for the reason', '点开查看原因');
}
function reasonLong(row) {
    const code = row.failure?.code;
    if (code === 'engine_closed') return t('FreeVideo stopped answering while this was being made. Start FreeVideo again, then generate again.', '生成过程中 FreeVideo 停止了响应。重新启动 FreeVideo 后再生成一次。');
    if (code === 'engine_restarted' || row.status === 'interrupted') return t('FreeVideo restarted while this was being made, so it did not finish. Finished parts are in your works.', 'FreeVideo 在生成过程中重新启动，这次没有完成。已经完成的部分在作品里。');
    if (row.kind === 'image') return imageGenError(code, row.failure, t) || t('The picture did not finish.', '这张图没有完成。');
    // The engine's own text is raw; it goes through the same advice as the create panel, and an unknown cause says so.
    const told = row.failure?.message ? failureAdvice(String(row.failure.message), t) : null;
    if (told?.summary) return told.summary + t(' ', '') + told.action;
    // A run closed from the history has no message, only the exception it raised.
    const advice = row.failure?.detail ? failureAdvice(errorText(row.failure.detail), t) : null;
    const said = advice && advice.kind !== 'unknown'
        ? [advice.summary || advice.title, advice.action].filter(Boolean).map(x => x.replace(/[.。]$/, '')).join(t('. ', '。')) + t('.', '。') : '';
    if (said) return said;
    return t('FreeVideo did not report a reason for this video. Try again, and report the problem if it keeps failing.',
        'FreeVideo 没有给出这段视频失败的原因。请重试，仍然失败请提交问题。');
}
function line2(row) {
    if (row.status === 'running') return etaText(row.eta_seconds) || elapsedText(row);
    if (row.status === 'queued' || row.status === 'submitting') return t('Starts after the one before it', '等前一项完成后开始');
    if (row.status === 'cancelling') return elapsedText(row);
    if (row.status === 'done') return t('Saved to your works', '已存入作品');
    return reasonShort(row);
}

// ---------- the picture for a row ----------
function paintThumb(box, row) {
    const key = JSON.stringify([row.id, row.status === 'done', row.meta?.preview, row.meta?.frames?.serial, row.result?.file]);
    if (box.dataset.key === key) return; box.dataset.key = key;
    box.style.backgroundImage = ''; box.style.backgroundSize = ''; box.style.backgroundPosition = '';
    const frames = row.meta?.frames;
    const picture = row.kind === 'image' ? (row.status === 'done' && row.result?.file ? pictureURL(row.result.file) : row.meta?.preview) : null;
    if (picture) { box.style.backgroundImage = `url("${picture}")`; box.style.backgroundSize = 'cover'; box.dataset.empty = 'false'; return; }
    if (frames?.url) {
        // The video's frame strip: its last frame stands for it.
        box.style.backgroundImage = `url("${api.apiURL(frames.url)}")`; box.style.backgroundSize = `${frames.frames * 100}% 100%`;
        box.style.backgroundPosition = frames.frames > 1 ? '100% 0' : '0 0'; box.dataset.empty = 'false'; return;
    }
    box.dataset.empty = 'true';
}

// ---------- icons (one line weight, as in the shell) ----------
const svg = path => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
const ICON = {check: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>'), alert: svg('<path d="M12 7.5v6"/><path d="M12 17h.01"/>'), stop: svg('<rect x="7" y="7" width="10" height="10" rx="2"/>'),
    x: svg('<path d="M7 7l10 10M17 7L7 17"/>'), up: svg('<path d="M7 14l5-5 5 5"/>'), down: svg('<path d="M7 10l5 5 5-5"/>'),
    image: svg('<rect x="4" y="5" width="16" height="14" rx="3"/><path d="M4 16l5-5 4 4 3-3 4 4"/>'), video: svg('<rect x="3.5" y="6" width="12" height="12" rx="3"/><path d="M15.5 10.5l5-3v9l-5-3"/>')};
// The shell's hover tip reads these three attributes (the window carries data-fv-tip-scope); a plain title stands in without it.
const tip = (element, name, use, key = '') => {
    element.dataset.fvTipName = name; element.dataset.fvTipUse = use; if (key) element.dataset.fvTipKey = key; else delete element.dataset.fvTipKey;
    if (shell()?.installed) element.removeAttribute('title'); else element.title = use ? `${name} · ${use}` : name;
};
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const EASE = 'cubic-bezier(.2, .8, .2, 1)';
const calm = () => document.documentElement.dataset.glass === 'solid' || document.documentElement.classList.contains('lite') || matchMedia('(prefers-reduced-motion: reduce)').matches;
// Phone width (the design system's breakpoint): one capsule along the bottom.
const narrow = () => innerWidth <= 720;
const raise = element => { try { if (element.matches(':popover-open')) element.hidePopover(); element.showPopover(); } catch { /* not connected */ } };

// ---------- the stop question, anchored to any button (the floating window's own, or a view's Stop) ----------
// host: where the question goes (a panel it belongs to, so a press on it does not close that panel); by default the
// open dialog the button is in, or the page.
export function confirmStop(anchor, {kind = 'video', queued = false, host = null, agent = false} = {}) {
    return new Promise(resolve => {
        const box = el('div', 'sgf sgf-confirm'); box.setAttribute('popover', 'manual'); box.dataset.fvTipScope = '';
        const sheet = el('div', 'sgf-sheet'); sheet.setAttribute('role', 'alertdialog');
        const title = el('b', null), text = el('p', null), voice = kind === 'voice' && !queued && !agent;
        title.textContent = queued ? t('Remove from the queue?', '取消排队？') : voice ? t('Stop dubbing?', '停止配音？') : t('Stop generating?', '停止生成？');
        if (agent) {
            title.textContent = queued ? t('Remove from the queue?', '取消排队？') : t('Stop this step?', '停止这一步？');
            text.textContent = queued ? t('It has not started; it will not run.', '这一步还没有开始，取消后不会进行。')
                : t('What this step has made so far is not kept. Finished works stay.', '这一步做到一半的不会保留，已经完成的作品不受影响。');
        } else text.textContent = queued ? t('It has not started; it will not be made.', '这项还没有开始，取消后不会生成。')
            : voice ? t('Lines already dubbed are kept; the rest can be dubbed again later.', '已配好的台词会保留，其余台词可以稍后重新配音。')
            : kind === 'image' ? t('This picture will not be kept.', '这张图不会保留。') : t('What has been made so far is not kept. Queued jobs go on.', '已生成的部分不会保留，排队的任务照常进行。');
        const ask = el('div', 'sgf-ask'), acts = el('div', 'sgf-acts');
        const keep = el('button', 'pill line'), stop = el('button', 'pill danger');
        keep.type = stop.type = 'button'; keep.textContent = agent ? t('Keep going', '继续') : voice ? t('Keep dubbing', '继续配音') : t('Keep going', '继续生成'); stop.textContent = queued ? t('Remove', '取消排队') : t('Stop', '停止');
        tip(keep, keep.textContent, agent ? t('Close this and let the step go on', '不停止，让这一步接着做') : voice ? t('Close this and keep dubbing', '不停止，接着配音') : t('Close this and keep generating', '不停止，接着生成'), 'Esc');
        tip(stop, stop.textContent, queued ? t('Take it out of the queue', '从队列里拿掉这一项') : agent ? t('Stop this step now; what it made so far is not kept', '立即停止这一步，做到一半的不保留') : voice ? t('Stop now and keep the lines already dubbed', '立即停止，保留已配好的台词') : t('Stop it now; nothing is kept', '立即停止，不保留结果'));
        acts.append(keep, stop); ask.append(title, text, acts); sheet.append(ask); box.append(sheet);
        (host || anchor.closest('dialog[open]') || document.body).append(box); raise(box);
        const a = anchor.getBoundingClientRect(), w = 300, h = box.offsetHeight || 132;
        const x = Math.min(innerWidth - w - 12, Math.max(12, a.left + a.width / 2 - w / 2)), above = a.bottom + 10 + h > innerHeight - 12 && a.top - h - 10 > 12;   // below the button; above only where below does not fit
        box.style.transform = `translate3d(${Math.round(x)}px, ${Math.round(above ? a.top - h - 10 : a.bottom + 10)}px, 0)`;
        box.dataset.corner = above ? 'b' : 't';
        let done = false;
        const finish = value => {
            if (done) return; done = true;
            document.removeEventListener('pointerdown', outside, true); box.removeEventListener('keydown', key);
            box.remove(); anchor.focus?.({preventScroll: true}); resolve(value);
        };
        const outside = event => { if (!box.contains(event.target)) finish(false); };
        const key = event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(false); } };
        keep.onclick = () => finish(false); stop.onclick = () => finish(true);
        setTimeout(() => document.addEventListener('pointerdown', outside, true), 0);
        box.addEventListener('keydown', key); keep.focus({preventScroll: true});
    });
}

// ---------- the window ----------
let built = null;
export function installTaskFloat() {
    if (built || typeof document === 'undefined') return built;
    startJobSources();
    const F = el('div', 'sgf'); F.id = 'fvTaskFloat'; F.setAttribute('popover', 'manual'); F.dataset.corner = 'br'; F.dataset.fvTipScope = '';
    F.setAttribute('role', 'region'); F.setAttribute('aria-label', t('Generation in progress', '进行中的生成'));
    F.innerHTML = `<div class="sgf-sheet" hidden></div>
      <div class="sgf-card" role="button" tabindex="0">
        <span class="sgf-pv"><i class="sgf-img"></i><i class="sgf-glyph"></i><i class="sgf-scrim"></i><svg class="sgf-ring" viewBox="0 0 30 30" aria-hidden="true"><circle class="bg" cx="15" cy="15" r="12.5"/><circle class="fg" cx="15" cy="15" r="12.5"/></svg><i class="sgf-mark"></i></span>
        <span class="sgf-tx"><b class="sgf-l1"></b><span class="sgf-l2"></span></span>
        <button class="sgf-n" type="button" aria-expanded="false"></button>
        <button class="sgf-x" type="button"></button>
      </div>`;
    const $ = selector => F.querySelector(selector);
    const card = $('.sgf-card'), sheet = $('.sgf-sheet'), thumb = $('.sgf-img'), glyph = $('.sgf-glyph'), ringFg = $('.sgf-ring .fg'), more = $('.sgf-n'), close = $('.sgf-x');
    const RING = 2 * Math.PI * 12.5; ringFg.style.strokeDasharray = String(RING);
    const slot = el('div', 'sgf-slot'); slot.setAttribute('popover', 'manual');
    let rows = [], open = false, ask = null, failCard = null, doneTimer = 0, shown = false, pos = {x: 0, y: 0}, drag = null, ticker = 0, stopError = '';
    // The corner the user last dragged to, kept across reloads; the window goes back to it whenever it is free.
    const CORNER_KEY = 'freevideo.taskFloat.corner';
    let corner = (() => { try { return localStorage.getItem(CORNER_KEY) || 'br'; } catch { return 'br'; } })();
    // Dropped by hand in this view: it stays where it was put, covered or not, until the view changes.
    let pinned = false, lastView = null, stayed = -1, stayedAt = 0;

    // ----- placement: four corners; the user's corner unless it would cover something, then the nearest free one -----
    // No per-view list of class names: what lies under a corner is looked at directly. Each corner is sampled on a grid;
    // a sample is taken when the topmost thing there (under the window itself) is a control, text, a picture, or inside
    // an element marked sgf-avoid. Empty panels and backgrounds are free. A region marked sgf-avoid that holds controls
    // (a console, a timeline, a column of settings) costs most anywhere inside it; a marked picture costs less, so when no
    // corner is free the window would rather overlap the edge of a picture than the edge of the tools.
    const headerHeight = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--fv-header-h')) || 56;
    const CONTROL = 'button, a[href], input, textarea, select, summary, label, [role=button], [role=tab], [role=slider], [role=option], [role=radio], [role=switch], [role=checkbox], [role=menuitem], [role=combobox], [role=spinbutton], [role=link], [contenteditable=""], [contenteditable=true], [tabindex]:not([tabindex="-1"])';
    // Not [data-fv-main]: that marks a layout block (often most of the window), not something on screen.
    const PICTURE = 'img, video, canvas, svg, picture';
    const ownText = node => [...node.childNodes].some(child => child.nodeType === 3 && child.textContent.trim());
    // Never to be covered (HARD): a generation screen or its picture ([data-fv-task-stage], [data-fv-task-pic]), a video,
    // and any control (the image view's picture-info button, a console's buttons). Then, softer: the empty part of a
    // marked tool region (it shows controls): 6; a marked region without controls on screen: 5; text: 5; a picture: 3.
    // When no place is wholly free: a control is never covered (CONTROL_COST), the top 28 px of a picture may be
    // (EDGE_COST), the rest of a picture may not (HARD).
    const HARD = 1000, CONTROL_COST = 100000, EDGE_COST = 40, EDGE_BAND = 28;
    let tools = new WeakMap(), boxes = new WeakMap(), fieldRects = null;
    const onScreen = node => node.getClientRects().length > 0 && !node.closest('dialog:not([open]), [data-fv-leaving="true"]') && !F.contains(node);
    const showsControls = region => {
        if (!tools.has(region)) tools.set(region, [...region.querySelectorAll(CONTROL)].some(onScreen));
        return tools.get(region);
    };
    // A marked region that holds a text box (the creation panel's console, a composer) is never covered, even where it
    // looks empty: it is where the user types.
    const FIELD = 'textarea, [contenteditable=""], [contenteditable=true], input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=range]):not([type=button]):not([type=submit])';
    const BOX = 'textarea, [contenteditable=""], [contenteditable=true]';
    const holdsBox = region => { if (!boxes.has(region)) boxes.set(region, [...region.querySelectorAll(BOX)].some(onScreen)); return boxes.get(region); };
    // Every text field's whole box counts as a control, whatever is drawn over it (a highlight layer, a mention list).
    // With 6 px of clearance, so the card does not graze a box's edge between two samples.
    const CLEAR = 6, within = (r, x, y) => x >= r.left - CLEAR && x <= r.right + CLEAR && y >= r.top - CLEAR && y <= r.bottom + CLEAR;
    const inField = (x, y) => (fieldRects ||= [...document.querySelectorAll(FIELD)].filter(onScreen).map(node => node.getBoundingClientRect())).some(r => within(r, x, y));
    // Marked regions are weighed by their boxes, not by what the pointer would hit there: a console may let clicks
    // through its empty parts to the page below, and that is still the console. One that holds a text box is never covered.
    let avoidRects = null;
    const avoidAt = (x, y) => {
        avoidRects ||= [...document.querySelectorAll('.sgf-avoid')].filter(onScreen)
            .map(node => ({r: node.getBoundingClientRect(), w: holdsBox(node) ? CONTROL_COST : showsControls(node) ? 6 : 5}));
        let weight = 0; for (const a of avoidRects) if (a.w > weight && within(a.r, x, y)) weight = a.w;
        return weight;
    };
    function weightAt(x, y) {
        if (inField(x, y)) return CONTROL_COST;
        const marked = avoidAt(x, y);
        if (marked >= CONTROL_COST) return marked;
        return Math.max(marked, hitAt(x, y));
    }
    function hitAt(x, y) {
        for (const node of document.elementsFromPoint(x, y)) {
            if (F.contains(node) || node === slot || flying.has(node) || node.classList?.contains('fv-tip')) continue;
            // A view on its way out (closed, or marked leaving) may still be drawn during a switch: it is not what stays.
            if (node.closest('dialog:not([open]), [data-fv-leaving="true"]')) continue;
            if (node === document.documentElement || node === document.body || node.tagName === 'DIALOG') return 0;
            // A picture (a generation screen, a video, a work's thumbnail even inside its button) may lose only its top
            // 28 px to the card; any other control is never covered.
            // (A drawn preview counts too when it is picture-sized: the editor's preview canvas.)
            const picture = node.closest('[data-fv-task-stage], [data-fv-task-pic], video, img, canvas');
            const big = picture && (picture.tagName === 'IMG' ? picture.getBoundingClientRect().width >= 40 : picture.tagName === 'CANVAS' ? picture.getBoundingClientRect().width >= 120 : true);
            if (big) return y - picture.getBoundingClientRect().top <= EDGE_BAND ? EDGE_COST : HARD;
            if (node.closest(CONTROL)) return CONTROL_COST;
            if (ownText(node)) return 5;
            if (node.closest(PICTURE)) return 3;
            return 0;
        }
        return 0;
    }
    const size = () => ({w: card.offsetWidth || 288, h: card.offsetHeight || 68});
    const M = 16;
    // A view may bound where the window lands (data-float-bounds: the image view's stage between its two side panels);
    // otherwise the corners of the window below the top bar.
    const windowBounds = () => ({left: 0, top: headerHeight() + 12 - M, right: innerWidth, bottom: innerHeight});
    function boundsArea() {
        // A covered view (a dialog over it) bounds nothing: host is null then, never false.
        const place = where(), host = !place.covered ? place.top : null, area = host ? host.querySelector('[data-float-bounds]') : null;
        const r = area?.getBoundingClientRect();
        if (!r || r.width < size().w + 2 * M || r.height < size().h + 2 * M) return null;
        return {left: r.left, top: Math.max(r.top, headerHeight() + 12 - M), right: r.right, bottom: Math.min(r.bottom, innerHeight)};
    }
    const bounds = () => boundsArea() || windowBounds();
    const spot = c => { const {w, h} = size(), b = bounds(); return {x: c[1] === 'l' ? b.left + M : b.right - M - w, y: c[0] === 't' ? b.top + M : b.bottom - M - h}; };
    // A 10 × 4 grid: fine enough to catch a button's edge under a corner of the card.
    // The watch every 0.7 s asks a coarse 5 × 2 grid first and the fine one only when that finds something.
    // Sampled 6 px beyond the card's edges, so it keeps clear of a control's edge instead of sitting flush against it.
    function costAt(x, y, w, h, cols = 10, rows = 4) {
        let sum = 0; const r = CLEAR_HIT;
        for (let i = 0; i < cols; i++) for (let j = 0; j < rows; j++) sum += weightAt(x - r + (w + 2 * r) * i / (cols - 1), y - r + (h + 2 * r) * j / (rows - 1));
        return sum;
    }
    const CLEAR_HIT = 6;
    // With the list, the stop question or the failure card open, the second layer counts too: it opens above a bottom
    // corner and below a top one, so the corner is chosen for both.
    const cost = (c, quick = false) => {
        const {w, h} = size(), p = spot(c);
        let sum = quick ? costAt(p.x, p.y, w, h, 5, 2) : costAt(p.x, p.y, w, h);
        if (!sheet.hidden) {
            const sw = sheet.offsetWidth || w, sh = sheet.offsetHeight || 0, sx = c[1] === 'r' ? p.x + w - sw : p.x;
            if (sh) sum += costAt(sx, c[0] === 't' ? p.y + h + 12 : p.y - 12 - sh, sw, sh);
        }
        return sum;
    };
    const NEAR = {br: ['br', 'bl', 'tr', 'tl'], bl: ['bl', 'br', 'tl', 'tr'], tr: ['tr', 'tl', 'br', 'bl'], tl: ['tl', 'tr', 'bl', 'br']};
    // Where the window may go: each corner (of the view's bounds, then of the window), and from each corner along its
    // two edges in 24 px steps, so a free strip beside the picture is found when no corner is free.
    function placements() {
        const {w, h} = size(), out = [];
        const add = (b, inBounds, m = M) => {
            const L = b.left + m, R = b.right - m - w, T = b.top + m, B = b.bottom - m - h;
            if (R < L || B < T) return;
            for (const c of NEAR[corner]) {
                const x0 = c[1] === 'l' ? L : R, y0 = c[0] === 't' ? T : B;
                out.push({c, x: x0, y: y0, d: 0, inBounds});
                for (let k = 1; k <= 24; k++) { const y = c[0] === 't' ? y0 + 24 * k : y0 - 24 * k; if (y < T || y > B) break; out.push({c, x: x0, y, d: 24 * k, inBounds}); }
                for (let k = 1; k <= 24; k++) { const x = c[1] === 'l' ? x0 + 24 * k : x0 - 24 * k; if (x < L || x > R) break; out.push({c, x, y: y0, d: 24 * k, inBounds}); }
            }
        };
        // Inside the view's bounds also with a 4 px margin: in the image view that is what lets the card sit in the stage
        // bar's free end and lose only the picture's top edge.
        const area = boundsArea(); if (area) { add(area, true); add({...area, top: Math.max(area.top, headerHeight() + 4 - 4)}, true, 4); }
        add(windowBounds(), false);
        return out;
    }
    // The open list (or question, or failure card) is placed by its own real box: above a bottom place, below a top
    // one. It must touch nothing (no picture, no player control, no caption); when the room there is smaller than the
    // list, the list gets that room's height and scrolls inside (down to one row), rather than covering anything.
    // The list always shows at least two whole rows (136 px or more) and at most three before it scrolls inside. Where no
    // free room holds two rows it still opens at that height, where it covers least (a picture's edge before a control).
    function sheetNeeds() {
        const full = sheet.scrollHeight || sheet.offsetHeight || 0, lines = [...sheet.querySelectorAll('.sgf-row')];
        const bottomOf = i => lines[i].offsetTop + lines[i].offsetHeight + 8;
        const two = lines.length >= 2 ? Math.max(Math.min(full, 136), Math.min(full, bottomOf(1))) : full;
        const most = lines.length > 3 ? Math.min(full, bottomOf(2)) : full;
        return {full, two: Math.min(two, most), most};
    }
    function sheetFit(p, quick) {
        if (sheet.hidden) return {k: 0, h: 0};
        const {w, h} = size(), sw = sheet.offsetWidth || w, {full, two, most} = sheetNeeds();
        if (!full) return {k: 0, h: 0};
        const sx = p.c[1] === 'r' ? p.x + w - sw : p.x, up = p.c[0] !== 't';
        const topOf = sh => up ? p.y - 12 - sh : p.y + h + 12;
        const fits = sh => topOf(sh) >= headerHeight() + 4 && topOf(sh) + sh <= innerHeight - 4;
        const at = sh => costAt(sx, topOf(sh), sw, sh, quick ? 5 : 8, quick ? 2 : Math.max(2, Math.ceil(sh / 24) + 1));
        const asked = sh => sh >= full ? 0 : sh;
        for (let sh = most; sh >= two; sh = sh === two ? 0 : Math.max(two, sh - 24)) if (fits(sh) && at(sh) === 0) return {k: 0, h: asked(sh)};
        // No free room for two rows: two rows anyway, wherever that covers least; off the screen is not a place.
        return {k: fits(two) ? at(two) : CONTROL_COST * 10, h: asked(two)};
    }
    function costOf(p, quick = false) {
        const {w, h} = size();
        const card = quick ? costAt(p.x, p.y, w, h, 5, 2) : costAt(p.x, p.y, w, h);
        const fit = sheetFit(p, quick); p.sheetH = fit.h;
        return card + fit.k;
    }
    // Puts the window at a chosen place; an open list takes the height found for it there.
    function applySpot(p, animate = true) {
        setWidth(p.width || WIDE);
        F.dataset.corner = p.c; moveTo(p, animate);
        if (!sheet.hidden) {
            if (p.sheetH === undefined) costOf(p);
            sheet.style.maxHeight = p.sheetH ? `${p.sheetH}px` : '';
            // More rows than shown: a fade at the bottom says the list scrolls.
            sheet.toggleAttribute('data-scrolls', sheet.scrollHeight > sheet.clientHeight + 1);
        } else { sheet.style.maxHeight = ''; sheet.removeAttribute('data-scrolls'); }
    }
    // The least covering place: the user's corner when free; else nothing hard covered if anywhere possible, the view's
    // bounds before the window, the nearest to a corner. A coarse look ranks every place, the fine one decides.
    // A view may keep an empty place for the window (data-float-slot: the image view's stage bar, right end). It is the
    // fallback when no place is wholly free; the card's top right goes to the slot's top right.
    function slotSpot() {
        const place = where(), host = !place.covered ? place.top : null, slot = host ? host.querySelector('[data-float-slot]') : null;
        const r = slot?.getBoundingClientRect(); if (!r || !r.width || !r.height) return null;
        const {w, h} = size();
        return {c: 'tr', x: Math.max(8, Math.min(innerWidth - 8 - w, r.right - w)), y: Math.max(headerHeight() + 4, Math.min(innerHeight - 8 - h, r.top)), d: 0, inBounds: true, slot: true};
    }
    // Full width (288) first; when that finds no wholly free place, the compact window (232 wide, smaller picture) is
    // tried, so it can sit in a view's side margin (the creation panel's beside its video) instead of covering anything.
    const WIDE = 288, COMPACT = 232;
    const setWidth = width => { F.style.setProperty('--sgf-card-w', `${width}px`); F.toggleAttribute('data-compact', width < WIDE); };
    function bestSpot() {
        setWidth(WIDE); const wide = placeBest(); wide.width = WIDE;
        if (wide.k === 0 || narrow()) return wide;
        setWidth(COMPACT); const compact = placeBest(); compact.width = COMPACT;
        if (compact.k < wide.k) return compact;
        setWidth(WIDE); return wide;
    }
    function placeBest() {
        tools = new WeakMap(); boxes = new WeakMap(); fieldRects = null; avoidRects = null;
        // The user's own corner whenever it is wholly free.
        const home = {c: corner, ...spot(corner), d: 0, k: 0, inBounds: Boolean(boundsArea())};
        if (costOf(home, true) === 0 && costOf(home) === 0) return home;
        // Inside the view's bounds first whenever a place there is usable (covering at most a picture's top edge); outside
        // only when the bounds hold none. Then the least covering, the nearest to a corner.
        const tier = p => p.k < HARD ? (p.inBounds ? 0 : 1) : 2;
        const order = (a, b) => tier(a) - tier(b) || a.k - b.k || (a.inBounds === b.inBounds ? 0 : a.inBounds ? -1 : 1) || a.d - b.d || NEAR[corner].indexOf(a.c) - NEAR[corner].indexOf(b.c);
        const ranked = placements().map(p => ({...p, k: costOf(p, true)})).sort(order);
        const finals = ranked.slice(0, 8).map(p => ({...p, k: costOf(p)})).sort(order);
        const best = finals[0];
        if (best && (best.k === 0 || (best.inBounds && best.k < HARD))) return best;
        // Nothing wholly free: the view's own slot if it covers no control, else the least: a picture's top edge before
        // the rest of it, and never a control while anything else is possible.
        const slot = slotSpot();
        if (slot) { slot.k = costOf(slot); if (slot.k < CONTROL_COST && (!best || slot.k <= best.k || best.k >= HARD)) return slot; }
        return best || home;
    }
    function bestCorner() { return bestSpot().c; }
    // Phone: one capsule along the bottom, raised above whatever the view keeps there (its console, its bar).
    // Same rules as the corners: never a control, a picture at most its top 28 px. From the bottom up to the top bar,
    // the lowest wholly free height; when there is none, the least covering of the best few (a coarse look ranks every
    // height, the fine one decides; lower wins a tie).
    function capsuleY() {
        tools = new WeakMap(); boxes = new WeakMap(); fieldRects = null; avoidRects = null;
        const {w, h} = size(), bottom = innerHeight - 12 - h, top = headerHeight() + 4, heights = [];
        for (let y = bottom; y >= top; y -= 8) {
            const k = costAt(12, y, w, h, 7, 3);
            if (k === 0 && costAt(12, y, w, h) === 0) return y;
            heights.push({y, k});
        }
        heights.sort((a, b) => a.k - b.k || b.y - a.y);
        const finals = heights.slice(0, 6).map(p => ({y: p.y, k: costAt(12, p.y, w, h)})).sort((a, b) => a.k - b.k || b.y - a.y);
        return finals[0]?.y ?? bottom;
    }
    function moveTo(p, animate = true) {
        pos = p; F.style.transition = animate && !calm() ? `transform 260ms ${EASE}` : 'none';
        F.style.transform = `translate3d(${Math.round(p.x)}px, ${Math.round(p.y)}px, 0)`;
    }
    function placeNow(animate = true) {
        if (narrow()) { F.classList.add('cap'); F.dataset.corner = 'b'; moveTo({x: 12, y: capsuleY()}, animate); return; }
        F.classList.remove('cap');
        const p = pinned ? (setWidth(WIDE), {c: corner, ...spot(corner), width: WIDE}) : bestSpot(); applySpot(p, animate);
    }

    // ----- the rows the window speaks for -----
    // The user's own workflow (no FreeVideo view, no FreeVideo node view): nothing of FreeVideo stays over it, as the
    // shell rules; the jobs go on and the window comes back with the next FreeVideo view.
    const ownWorkflow = () => Boolean(shell()?.installed) && !document.documentElement.dataset.fvShell && !modals().length;
    const visible = () => { if (ownWorkflow()) return []; const place = where(); return rows.filter(row => !(FINAL.has(row.status) && row.seen) && row.status !== 'cancelled' && !atHome(row, place)); };
    const primary = list => list.find(r => r.status === 'running') || list.find(r => ACTIVE.has(r.status)) || list.find(r => r.status === 'failed' || r.status === 'interrupted') || list[0];

    function draw() {
        // Ended jobs the user is already looking at need no window: they are seen.
        // A stopped job needs no word either (the user asked for it). Dismissing redraws through the subscription.
        const place = where();
        const settled = rows.filter(row => row.status === 'cancelled' || (FINAL.has(row.status) && !row.seen && atHome(row, place)));
        if (settled.length) { for (const row of settled) { jobs.markSeen(row.id); jobs.dismiss(row.id); } return; }
        const list = visible(), row = primary(list);
        remember();
        if (!row) { hide(); return; }
        const state = row.status === 'running' || row.status === 'cancelling' ? 'run' : ACTIVE.has(row.status) ? 'queue' : row.status === 'done' ? 'done' : 'fail';
        F.dataset.st = state; F.dataset.n = String(Math.min(list.length, 3)); if (list.length < 2) delete F.dataset.n;
        F.dataset.kind = row.kind === 'image' ? 'image' : 'video';
        paintThumb(thumb, row); glyph.innerHTML = row.kind === 'image' ? ICON.image : ICON.video;
        // The step picture sharpens as the picture comes together (the worker sends rough ones first).
        thumb.style.filter = state === 'run' && row.kind === 'image' && row.meta?.preview ? `blur(${Math.max(0, 5 * (1 - (row.percent || 0) * 1.25)).toFixed(1)}px)` : '';
        ringFg.style.strokeDashoffset = String(RING * (1 - (state === 'run' ? (row.percent || 0) : state === 'queue' ? 0 : 1)));
        $('.sgf-mark').innerHTML = state === 'done' ? ICON.check : state === 'fail' ? ICON.alert : '';
        const word = stateWord(row);
        // The label may shorten with an ellipsis; the percentage never does.
        const l1 = $('.sgf-l1'), label = el('span', 'sgf-k'); label.textContent = row.status === 'running' ? kindText(row) : word;
        l1.replaceChildren(label);
        if (row.status === 'running') { const p = el('span', 'sgf-p'); p.textContent = `${percentOf(row)}%`; l1.append(p); }
        $('.sgf-l2').textContent = stopError && row.id === ask ? stopError : line2(row) + (row.status === 'running' ? part(row) : '');
        more.hidden = list.length < 2;
        more.innerHTML = `<span>${list.length}</span>${open ? ICON.down : ICON.up}`; more.setAttribute('aria-expanded', String(open));
        more.setAttribute('aria-label', t(`${list.length} jobs`, `${list.length} 项任务`));
        // While the list is open it speaks for itself (names and states): the count button gives no tip over it.
        if (open) for (const key of ['fvTipName', 'fvTipUse', 'fvTipKey']) delete more.dataset[key];
        else tip(more, t('Jobs', '任务列表'), t('Show every job in progress', '展开全部进行中的任务'));
        const stoppable = ACTIVE.has(row.status) && row.cancellable !== false && row.status !== 'cancelling';
        close.hidden = ACTIVE.has(row.status) && !stoppable;
        close.innerHTML = stoppable ? ICON.stop : ICON.x;
        close.setAttribute('aria-label', stoppable ? t('Stop', '停止') : t('Dismiss', '收起'));
        if (stoppable) tip(close, t('Stop', '停止'), t('Stop this generation (asks first)', '停止这次生成（会先确认）'));
        else tip(close, t('Dismiss', '收起'), row.status === 'done' ? t('Hide this; the result stays in your works', '收起提示，成品仍在作品里') : t('Hide this message', '收起这条提示'));
        const action = state === 'done' ? [t('Open', '打开'), t('See what was made', '查看这次生成的成品')]
            : state === 'fail' ? [t('See why', '查看原因'), t('Reason, what to do, and the report', '原因、可做的事和诊断报告')]
            : [t('Back to it', '回到生成'), t('Return to the generation; nothing restarts', '展开回生成画面，进度不中断')];
        // The hover tip says the whole state, whatever the card had to shorten, then what a click does.
        const full = `${[label.textContent, l1.querySelector('.sgf-p')?.textContent].filter(Boolean).join(' ')} · ${$('.sgf-l2').textContent}`;
        tip(card, full, `${action[0]}：${action[1]}`.replace('：', cn ? '：' : ': '), 'Enter');
        card.setAttribute('aria-label', `${full}. ${action[0]}`);
        drawSheet(list);
        if (!shown) show();
        if (state === 'done') armDone(row); else { clearTimeout(doneTimer); doneFor = null; }
        ticker ||= setInterval(() => { if (shown && !drag) { const r = primary(visible()); if (r && ACTIVE.has(r.status)) $('.sgf-l2').textContent = stopError && r.id === ask ? stopError : line2(r) + (r.status === 'running' ? part(r) : ''); } }, 1000);
    }
    let sheetKey = '';
    function drawSheet(list = visible()) {
        const key = JSON.stringify([ask, failCard, open && list.length > 1 ? list.map(r => [r.id, r.status, Boolean(r.meta?.preview || r.meta?.frames)]) : null, stopError]);
        if (key === sheetKey) {
            for (const line of sheet.querySelectorAll('.sgf-row')) {
                const row = list.find(r => r.id === line.dataset.id); if (!row) continue;
                const fill = line.querySelector('.sgf-rbar b'); if (fill) fill.style.width = `${percentOf(row)}%`;
                const sub = line.querySelector('.sgf-rtx span'); if (sub) sub.textContent = `${t(...(VIEW_NAMES[viewFor(row)] || VIEW_NAMES.studio))} · ${row.status === 'running' ? `${percentOf(row)}%` : stateWord(row)}`;
            }
            return;
        }
        sheetKey = key;
        sheet.replaceChildren();
        const target = ask && rows.find(r => r.id === ask);
        if (target) {
            const queued = target.status === 'queued';
            const box = el('div', 'sgf-ask'); box.setAttribute('role', 'alertdialog');
            const title = el('b'), text = el('p'); title.textContent = queued ? t('Remove from the queue?', '取消排队？') : t('Stop generating?', '停止生成？');
            text.textContent = queued ? t('It has not started; it will not be made.', '这项还没有开始，取消后不会生成。') : t('What has been made so far is not kept. Queued jobs go on.', '已生成的部分不会保留，排队的任务照常进行。');
            const acts = el('div', 'sgf-acts'), keep = el('button', 'pill line'), stop = el('button', 'pill danger');
            keep.type = stop.type = 'button'; keep.dataset.k = 'keep'; stop.dataset.k = 'stop';
            keep.textContent = t('Keep going', '继续生成'); stop.textContent = queued ? t('Remove', '取消排队') : t('Stop', '停止');
            tip(keep, keep.textContent, t('Close this and keep generating', '不停止，接着生成'), 'Esc');
            tip(stop, stop.textContent, queued ? t('Take it out of the queue', '从队列里拿掉这一项') : t('Stop it now; nothing is kept', '立即停止，不保留结果'));
            acts.append(keep, stop); box.append(title, text, acts); sheet.append(box);
        } else if (failCard && rows.find(r => r.id === failCard)) {
            const row = rows.find(r => r.id === failCard);
            const box = el('div', 'sgf-fail'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', t('What happened', '发生了什么'));
            const title = el('b'), text = el('p', 'fv-selectable'), acts = el('div', 'sgf-acts');
            title.textContent = row.kind === 'image' ? t("The picture didn't finish", '这张图没有完成') : t("The video didn't finish", '这段视频没有完成');
            text.textContent = reasonLong(row);
            const report = row.failure?.report_id || row.meta?.report_id;
            if (report && row.kind === 'image') {
                const holder = el('span', 'sgf-report');
                try { holder.append(imageReportBar(holder, report, t, {productVersion: () => null, quiet: true, fetchApi: (path, options) => api.fetchApi(path, options)}).bar); } catch { /* no report bar */ }
                acts.append(holder);
            } else if (report) {
                const link = el('a', 'pill line'); link.textContent = t('Download report', '下载报告'); link.href = api.apiURL(`/freevideo/report/${report}`); link.download = '';
                tip(link, t('Download report', '下载报告'), t('Save the diagnostic report to send us', '保存诊断报告，可发给我们'));
                acts.append(link);
            }
            const back = el('button', 'pill line'), dismiss = el('button', 'pill');
            back.type = dismiss.type = 'button'; back.dataset.k = 'go'; back.dataset.id = row.id; dismiss.dataset.k = 'dismiss';
            back.textContent = t('Open', '去看看'); dismiss.textContent = t('Close', '关闭');
            tip(back, back.textContent, t('Go to where it was started', '回到发起这次生成的视图'));
            tip(dismiss, dismiss.textContent, t('Hide this message', '收起这条提示'), 'Esc');
            acts.append(back, dismiss); box.append(title, text, acts); sheet.append(box);
        } else if (open && list.length > 1) {
            const head = el('header'); const count = el('b'); count.textContent = t(`${list.length} jobs`, `${list.length} 项任务`); head.append(count); sheet.append(head);
            for (const row of list) {
                const line = el('button', 'sgf-row'); line.type = 'button'; line.dataset.k = 'go'; line.dataset.id = row.id;
                line.dataset.st = row.status === 'running' ? 'run' : ACTIVE.has(row.status) ? 'queue' : row.status === 'done' ? 'done' : 'fail';
                const th = el('span', 'sgf-rth'), pic = el('i', 'sgf-img'); th.append(pic, Object.assign(el('i', 'sgf-glyph', row.kind === 'image' ? ICON.image : ICON.video))); paintThumb(pic, row);
                const tx = el('span', 'sgf-rtx'), name = el('b'), sub = el('span');
                // A row's title is its kind, never its prompt (a prompt belongs to the work only).
                name.textContent = row.kind === 'image' ? t('Picture', '图片任务') : viewFor(row) === 'editor' ? t('Edit', '剪辑任务') : t('Video', '视频任务');
                sub.textContent = `${t(...(VIEW_NAMES[viewFor(row)] || VIEW_NAMES.studio))} · ${row.status === 'running' ? `${percentOf(row)}%` : stateWord(row)}`;
                tx.append(name, sub); line.append(th, tx);
                if (row.status === 'running') { const bar = el('i', 'sgf-rbar'), fill = el('b'); fill.style.width = `${percentOf(row)}%`; bar.append(fill); line.append(bar); }
                tip(line, name.textContent, FINAL.has(row.status) ? t('Open it', '打开它') : t('Return to this generation', '回到这项生成'));
                sheet.append(line);
            }
        }
        const was = sheet.hidden;
        sheet.hidden = !sheet.childElementCount;
        F.classList.toggle('sheet-on', !sheet.hidden);
        // Opening the second layer may need another corner (it must not cover a button or a field); closing it goes back.
        if (shown && was !== sheet.hidden && !narrow()) {
            const p = sheet.hidden && pinned ? {c: corner, ...spot(corner)} : bestSpot();
            applySpot(p, true);
        }
    }
    function show() {
        shown = true; rehome(); placeNow(false);
        if (calm() || shared?.mode === 'collapse') return;
        F.animate([{opacity: 0, transform: F.style.transform + ' scale(.96)'}, {opacity: 1, transform: F.style.transform}], {duration: 220, easing: EASE});
    }
    function hide(animate = true) {
        if (!shown) return; shown = false; open = false; ask = null; failCard = null; clearInterval(ticker); ticker = 0;
        const gone = () => { if (!shown) try { F.hidePopover(); } catch { /* already hidden */ } };
        if (!animate || calm() || shared?.mode === 'expand') { gone(); return; }
        F.animate([{opacity: 1}, {opacity: 0, transform: F.style.transform + ' scale(.96)'}], {duration: 180, easing: EASE}).onfinish = gone;
    }
    // Moves into the topmost modal dialog (outside it everything is inert) and back on top of the top layer.
    function rehome() {
        const target = host();
        if (F.parentNode !== target) { target.append(F); }
        if (slot.parentNode !== document.body) document.body.append(slot);
        if (shown) raise(F);
    }
    // A finished job folds away after five seconds without the pointer on the window (counting again once it leaves).
    let doneFor = null;
    function armDone(row, again = false) {
        if (doneFor === row.id && !again) return;
        clearTimeout(doneTimer); doneFor = row.id;
        // The pointer is asked directly: a window hidden under the pointer gets no pointerleave.
        doneTimer = setTimeout(() => { if (!F.matches(':hover') && !open && !failCard) jobs.dismiss(row.id); else { doneFor = null; } }, 5000);
    }

    // ----- the shared element: one picture flies between the generation screen and the window -----
    // As stu's fly(): the same picture moves and scales evenly on one timeline (translate + uniform scale, clipped to the
    // target's shape and corner radius), 260 ms eased out; the source is hidden from lift-off, the destination stays
    // hidden until landing, then the copy hands over in 90 ms. Going into the window, its glass fades in 60 ms late.
    const FLIGHT = 260, HANDOFF = 90;
    // The picture inside a generation screen: one the view marks, else its largest picture or video on screen (never a
    // drawn canvas: those are the screen's decoration, such as the progress wave), else the screen itself.
    function pictureOf(stage) {
        const marked = stage.querySelector('[data-fv-task-pic]'); if (marked?.getClientRects().length) return marked;
        const box = stage.getBoundingClientRect(); let best = null, area = box.width * box.height * .3;
        for (const node of stage.querySelectorAll('img, video')) {
            if (node.closest('[hidden]') || !node.getClientRects().length) continue;
            const r = node.getBoundingClientRect(), a = r.width * r.height;
            if (a > area && getComputedStyle(node).visibility !== 'hidden' && Number(getComputedStyle(node).opacity) > .05) { best = node; area = a; }
        }
        return best || stage;
    }
    const lookOf = node => { if (!node || node.tagName === 'DIV') return {}; const style = getComputedStyle(node); return {filter: style.filter !== 'none' ? style.filter : '', transform: style.transform !== 'none' ? style.transform : ''}; };
    const radiusOf = node => parseFloat(getComputedStyle(node).borderTopLeftRadius) || 12;
    // Outside a View Transition (or with no shell): a copy flies above everything.
    function fly(from, to, source, rFrom, rTo, {look = {}, hideFrom = [], hideTo = []} = {}) {
        if (calm() || !from || !to || !from.width || !to.width || !source?.style.backgroundImage) return null;
        const big = from.width * from.height >= to.width * to.height ? from : to, small = big === from ? to : from;
        const s = Math.max(small.width / big.width, small.height / big.height);
        const ex = (big.width * s - small.width) / 2 / s, ey = (big.height * s - small.height) / 2 / s;
        const dx = small.left + small.width / 2 - (big.left + big.width * s / 2), dy = small.top + small.height / 2 - (big.top + big.height * s / 2);
        const ghost = el('div', 'sgf-fly'), picture = el('i', 'sgf-fly-im'); ghost.setAttribute('popover', 'manual');
        Object.assign(ghost.style, {left: big.left + 'px', top: big.top + 'px', width: big.width + 'px', height: big.height + 'px'});
        Object.assign(picture.style, {backgroundImage: source.style.backgroundImage, backgroundSize: source.style.backgroundSize || 'cover',
            backgroundPosition: source.style.backgroundPosition || 'center', filter: look.filter || '', transform: look.transform || ''});
        ghost.append(picture); document.body.append(ghost); raise(ghost); flying.add(ghost);
        const rb = big === from ? rFrom : rTo, rs = (big === from ? rTo : rFrom) / s;
        const K0 = {transform: 'translate(0px, 0px) scale(1)', clipPath: `inset(0px round ${rb}px)`};
        const K1 = {transform: `translate(${dx}px, ${dy}px) scale(${s})`, clipPath: `inset(${ey}px ${ex}px round ${rs}px)`};
        const [A0, A1] = big === from ? [K0, K1] : [K1, K0], total = FLIGHT + HANDOFF;
        const animation = ghost.animate([{...A0, opacity: 1, easing: EASE}, {...A1, opacity: 1, offset: FLIGHT / total}, {...A1, opacity: 0}], {duration: total, fill: 'forwards'});
        for (const node of hideFrom) node?.animate([{visibility: 'hidden'}, {visibility: 'hidden'}], {duration: total});
        for (const node of hideTo) node?.animate([{visibility: 'hidden'}, {visibility: 'hidden'}], {duration: FLIGHT});
        animation.onfinish = () => { flying.delete(ghost); ghost.remove(); };
        return animation;
    }
    const flying = new Set();
    // Inside the shell's View Transition the live page is not drawn, so the browser carries the picture by name with the
    // same rules (task_float.css: one picture, cover-fitted and clipped, the other side hidden, a 90 ms handoff); the
    // window's card has its own name so its glass can arrive 60 ms late. Names last one switch.
    const SHARED = 'fv-task-pic', CARD = 'fv-task-card';
    let shared = null; // {mode: 'collapse'|'expand', view, picture}
    const switching = () => typeof document.startViewTransition === 'function' && document.documentElement.dataset.fvTransitions !== 'off' && !calm();
    const root = document.documentElement;
    function clearShared() {
        if (!shared) return;
        if (shared.picture) shared.picture.style.viewTransitionName = '';
        thumb.style.viewTransitionName = ''; card.style.viewTransitionName = ''; delete root.dataset.fvTaskFlight; shared = null;
    }
    let sharedTimer = 0;
    const releaseShared = () => { clearTimeout(sharedTimer); sharedTimer = setTimeout(clearShared, 1200); };
    function startShared(mode, view, picture = null) {
        clearShared(); shared = {mode, view, picture}; root.dataset.fvTaskFlight = mode;
        if (picture) picture.style.viewTransitionName = SHARED;
        releaseShared();
    }
    // Leaving the generation screen: its picture shrinks into the window.
    function collapseFrom(stage, {transition = false} = {}) {
        const view = stage.dataset.fvTaskStage, from = stage.getBoundingClientRect();
        const row = rows.find(r => ACTIVE.has(r.status) && viewFor(r) === view);
        if (!row || !from.width) return;
        const picture = pictureOf(stage);
        // Going back from the window already names the picture for this switch; one flight per switch.
        if (transition && switching()) { if (shared?.mode !== 'expand') startShared('collapse', view, picture); return; }
        const source = el('i'); paintThumb(source, row);
        pendingCollapse = {from: picture.getBoundingClientRect(), source, radius: radiusOf(picture), look: lookOf(picture), hide: [picture]};
    }
    let pendingCollapse = null;
    // Where the generation screen is while the user looks at it: a view that closes at once is no longer measurable.
    let memo = null;
    function remember() {
        const place = where(), row = rows.find(r => ACTIVE.has(r.status) && atHome(r, place));
        const stage = row && document.querySelector(`[data-fv-task-stage="${viewFor(row)}"]`), picture = stage && pictureOf(stage), box = picture?.getBoundingClientRect();
        memo = box?.width ? {rect: box, row, dialog: stage.closest('dialog'), radius: radiusOf(picture), look: lookOf(picture)} : null;
    }
    function landCollapse() {
        // In a View Transition: the old picture was taken when the switch began; the leaving view may still be drawn, so
        // its picture gives the name up and the window's picture and card take theirs for the new one.
        if (shared?.mode === 'collapse' && shown && !shared.landed) {
            shared.landed = true; if (shared.picture) shared.picture.style.viewTransitionName = '';
            thumb.style.viewTransitionName = SHARED; card.style.viewTransitionName = CARD; releaseShared(); return;
        }
        if (!pendingCollapse || !shown) return;
        const {from, source, radius, look, hide} = pendingCollapse; pendingCollapse = null;
        const pv = $('.sgf-pv'), to = pv.getBoundingClientRect();
        const animation = fly(from, to, source, radius, 13, {look, hideFrom: hide, hideTo: [pv]});
        if (animation) card.animate([{opacity: 0, transform: 'scale(.96)'}, {opacity: 1, transform: 'none'}], {duration: 200, delay: 60, easing: EASE, fill: 'backwards'});
    }
    // Going back: the window's picture grows into the generation screen, which keeps counting from where it was.
    async function expandTo(row, {visit = false} = {}) {
        if (!visit && (row.status === 'failed' || row.status === 'interrupted')) { failCard = row.id; open = false; ask = null; drawSheet(); return; }
        const pv = $('.sgf-pv'), fromBox = pv.getBoundingClientRect(), source = el('i'); paintThumb(source, row);
        const view = viewFor(row);
        open = false; ask = null; failCard = null; drawSheet();
        if (row.status === 'done') jobs.markSeen(row.id);
        // Already in that view, looking at something else: the view brings its generation screen back, the picture flies in.
        if (where().view === view && away.has(view)) {
            window.dispatchEvent(new CustomEvent('freevideo-task-stage-open', {detail: {view, id: row.id}}));
            away.delete(view); await frame(); await frame();
            const stage = document.querySelector(`[data-fv-task-stage="${view}"]`);
            if (stage) { const picture = pictureOf(stage); fly(fromBox, picture.getBoundingClientRect(), source, 13, radiusOf(picture), {look: lookOf(picture), hideFrom: [pv], hideTo: [picture]}); }
            if (row.status === 'done' || visit) jobs.dismiss(row.id);
            draw(); return;
        }
        const viaShell = switching() && where().view !== view && shell()?.go;
        if (viaShell) { startShared('expand', view); thumb.style.viewTransitionName = SHARED; card.style.viewTransitionName = CARD; }
        await goTo(view);
        const stage = await stageAppears(view);
        if (!viaShell && stage) { const picture = pictureOf(stage); fly(fromBox, picture.getBoundingClientRect(), source, 13, radiusOf(picture), {look: lookOf(picture), hideFrom: [pv], hideTo: [picture]}); }
        if (row.status === 'done' || visit) jobs.dismiss(row.id);
        draw();
    }

    // ----- input: a click goes back, a drag moves and snaps to the nearest corner (with the throw), keys work too -----
    card.addEventListener('pointerdown', event => {
        if (event.button !== 0 || event.target.closest('button')) return;
        drag = {x0: event.clientX, y0: event.clientY, p0: {...pos}, moved: false, hist: [[performance.now(), event.clientX, event.clientY]]};
        card.setPointerCapture(event.pointerId);
    });
    card.addEventListener('pointermove', event => {
        if (!drag) return;
        const dx = event.clientX - drag.x0, dy = event.clientY - drag.y0;
        if (!drag.moved && Math.hypot(dx, dy) < 4) return;
        if (!drag.moved) { drag.moved = true; F.classList.add('drag'); open = false; ask = null; failCard = null; drawSheet(); }
        drag.hist.push([performance.now(), event.clientX, event.clientY]); if (drag.hist.length > 6) drag.hist.shift();
        if (narrow()) moveTo({x: 12, y: drag.p0.y + Math.min(0, dy)}, false);
        else { moveTo({x: drag.p0.x + dx, y: drag.p0.y + dy}, false); showSlot(); }
    });
    const projected = d => {
        const h = d.hist, a = h[0], b = h[h.length - 1], dt = Math.max(16, b[0] - a[0]);
        const vx = (b[1] - a[1]) / dt, vy = (b[2] - a[2]) / dt, {w, h: hh} = size();
        return {x: pos.x + w / 2 + vx * 180, y: pos.y + hh / 2 + vy * 180};
    };
    const snapTarget = d => { const p = projected(d); return (p.y < innerHeight / 2 ? 't' : 'b') + (p.x < innerWidth / 2 ? 'l' : 'r'); };
    function showSlot() {
        const c = snapTarget(drag), p = spot(c), {w, h} = size();
        Object.assign(slot.style, {transform: `translate3d(${p.x}px, ${p.y}px, 0)`, width: w + 'px', height: h + 'px'});
        if (!slot.matches(':popover-open')) { raise(slot); raise(F); }
    }
    const hideSlot = () => { try { slot.hidePopover(); } catch { /* not shown */ } };
    card.addEventListener('pointerup', () => {
        if (!drag) return; const d = drag; drag = null; F.classList.remove('drag'); hideSlot();
        if (!d.moved) { const row = primary(visible()); if (row) void expandTo(row); return; }
        if (narrow()) { placeNow(true); return; }
        corner = snapTarget(d); pinned = true; try { localStorage.setItem(CORNER_KEY, corner); } catch { /* the choice lasts this page only */ }
        F.dataset.corner = corner; moveTo(spot(corner), true); return;
        placeNow(true);
    });
    card.addEventListener('pointercancel', () => { drag = null; F.classList.remove('drag'); hideSlot(); placeNow(true); });
    card.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); const row = primary(visible()); if (row) void expandTo(row); } });
    F.addEventListener('pointerleave', () => { const row = primary(visible()); if (row?.status === 'done') armDone(row, true); });
    F.addEventListener('keydown', event => { if (event.key === 'Escape' && (open || ask || failCard)) { event.preventDefault(); event.stopPropagation(); open = false; ask = null; failCard = null; drawSheet(); card.focus({preventScroll: true}); } });
    F.addEventListener('click', async event => {
        const button = event.target.closest('button'); if (!button) return;
        const row = primary(visible()); if (!row) return;
        if (button === more) {
            open = !open; ask = null; failCard = null;
            more.setAttribute('aria-expanded', String(open)); more.lastElementChild?.replaceWith(document.createRange().createContextualFragment(open ? ICON.down : ICON.up));
            drawSheet(); draw(); return;
        }
        if (button === close) {
            if (ACTIVE.has(row.status)) { ask = row.id; stopError = ''; open = false; failCard = null; drawSheet(); setTimeout(() => sheet.querySelector('[data-k="keep"]')?.focus({preventScroll: true}), 0); }
            else { jobs.dismiss(row.id); }
            return;
        }
        const k = button.dataset.k;
        if (k === 'keep') { ask = null; drawSheet(); card.focus({preventScroll: true}); }
        else if (k === 'stop') {
            const id = ask; ask = null; drawSheet();
            try { await jobs.cancel(id); } catch (error) { ask = id; stopError = isNetworkTypeError(error) ? connectionLostText(t) : t('Could not stop. Try again; if it keeps failing, report the problem.', '没有停止成功。请重试；仍然失败时，请提交问题。'); draw(); }
        } else if (k === 'dismiss') { const id = failCard; failCard = null; if (id) jobs.dismiss(id); draw(); }
        else if (k === 'go') { const target = rows.find(r => r.id === button.dataset.id); if (target) { const visit = failCard === target.id; failCard = null; await expandTo(target, {visit}); } }
    });

    // ----- following the page: jobs, views opening and closing, size -----
    jobs.subscribe(list => { rows = list; draw(); });
    const observer = new MutationObserver(records => {
        let changed = false;
        for (const record of records) {
            const node = record.target;
            if (record.type === 'attributes' && node.tagName === 'DIALOG' && node !== F) {
                if (record.attributeName === 'open' && node.open) openedAt.set(node, ++openSerial);
                if (record.attributeName === 'open' && !node.open && memo?.dialog === node && !shared && !pendingCollapse) {
                    const source = el('i'); paintThumb(source, memo.row); pendingCollapse = {from: memo.rect, source, radius: memo.radius, look: memo.look, hide: []};
                }
                if (record.attributeName === 'data-closing' && node.dataset.closing === 'true') { const stage = node.querySelector('[data-fv-task-stage]'); if (stage) collapseFrom(stage); }
                if (node.dataset.fvLeaving === 'true' && !shared) { const stage = node.querySelector('[data-fv-task-stage]'); if (stage) collapseFrom(stage); }
                changed = true;
            } else if (record.type === 'attributes' && record.attributeName === 'data-fv-leaving' && node.dataset?.fvLeaving === 'true' && !shared) {
                const stage = node.matches?.('[data-fv-task-stage]') ? node : node.querySelector?.('[data-fv-task-stage]'); if (stage) collapseFrom(stage); changed = true;
            } else if (record.type === 'childList' && (!F.isConnected || [...record.removedNodes].some(n => n.nodeType === 1 && (n.tagName === 'DIALOG' || n.querySelector?.('dialog'))))) changed = true;
        }
        if (!changed) return;
        const view = where().view; if (view !== lastView) { lastView = view; pinned = false; }
        // A view that closed comes back showing its generation screen: it is no longer away.
        for (const name of [...away]) if (name !== view) away.delete(name);
        if (shared?.mode === 'expand' && !shared.picture) {
            const stage = document.querySelector(`[data-fv-task-stage="${shared.view}"]`);
            if (stage && stage.closest('dialog')?.open) {
                thumb.style.viewTransitionName = ''; shared.picture = pictureOf(stage); shared.picture.style.viewTransitionName = SHARED; releaseShared();
            }
        }
        if (shown) { rehome(); placeNow(true); }
        // Ghosts in flight stay above a view that opens under them.
        for (const ghost of flying) raise(ghost);
        draw(); landCollapse();
    });
    observer.observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['open', 'data-closing', 'data-fv-leaving']});
    new MutationObserver(() => draw()).observe(document.documentElement, {attributes: true, attributeFilter: ['data-fv-shell']});
    for (const dialog of document.querySelectorAll('dialog[open]')) openedAt.set(dialog, ++openSerial);
    // The shell marks the view it leaves before its View Transition takes the old picture: name the stage now.
    // In-view leave and return (the image view's stage showing another picture while its job runs).
    const stageLeave = (view, element) => {
        if (!view || away.has(view)) return;
        const stage = element?.dataset?.fvTaskStage ? element : document.querySelector(`[data-fv-task-stage="${view}"]`);
        if (stage) collapseFrom(stage);
        away.add(view); pinned = false; draw(); landCollapse();
    };
    const stageReturn = view => { if (away.delete(view)) draw(); };
    window.addEventListener('freevideo-task-stage-leave', event => stageLeave(event.detail?.view, event.detail?.element));
    window.addEventListener('freevideo-task-stage-return', event => stageReturn(event.detail?.view));
    Object.assign(globalThis.FreeVideoTasks ||= {}, {jobs, collapseFrom: stageLeave, expand: id => { const row = jobs.get(id); if (row) void expandTo(row, {visit: !ACTIVE.has(row.status)}); }});
    window.addEventListener('freevideo-view-leave', event => { const stage = event.detail?.element?.querySelector?.('[data-fv-task-stage]') || event.detail?.element; if (stage?.dataset?.fvTaskStage) collapseFrom(stage, {transition: true}); });
    addEventListener('resize', () => shown && placeNow(false));
    // What must stay visible moves (a panel opens, the library shows a picture): look again twice a second.
    // Only when the corner it sits in gets covered, or the user's own corner comes free again.
    setInterval(() => {
        if (!shown) { remember(); return; }
        if (drag || open || ask || failCard || pinned) return;
        if (narrow()) {
            // Looked at again when what is under it changed, or every 3 s (a lower height may have come free).
            tools = new WeakMap(); boxes = new WeakMap(); fieldRects = null; avoidRects = null;
            const quick = costAt(12, pos.y, size().w, size().h, 7, 3);
            if (quick > 0 || Date.now() - stayedAt > 3000) { stayedAt = Date.now(); const y = capsuleY(); if (Math.abs(y - pos.y) > 4) moveTo({x: 12, y}, true); }
            return;
        }
        tools = new WeakMap(); boxes = new WeakMap(); fieldRects = null; avoidRects = null;
        const home = {c: corner, ...spot(corner)}, atUser = Math.abs(pos.x - home.x) < 1 && Math.abs(pos.y - home.y) < 1;
        if (!atUser && costOf(home, true) === 0 && costOf(home) === 0) { F.dataset.corner = corner; moveTo(home, true); return; }
        // Covered: choose again, unless nothing changed since it last chose to stay here (the least covered place).
        const quick = costOf({c: F.dataset.corner || corner, ...pos}, true);
        // (A decision to stay is looked at again after 3 s: a view may open a slot or a free strip without changing view.)
        if (quick > 0 && (quick !== stayed || Date.now() - stayedAt > 3000)) {
            const p = bestSpot(), same = Math.abs(p.x - pos.x) < 1 && Math.abs(p.y - pos.y) < 1;
            stayed = same ? quick : -1; stayedAt = Date.now(); if (!same) applySpot(p, true);
        } else if (quick === 0) stayed = -1;
    }, 700);
    document.body.append(F);
    // corners(): what each corner would cover (for checks and the QA scripts).
    built = {element: F, draw, expandTo, goTo, where, corners: () => { tools = new WeakMap(); boxes = new WeakMap(); fieldRects = null; avoidRects = null; return Object.fromEntries(['tl', 'tr', 'bl', 'br'].map(c => [c, cost(c)])); },
        here: () => { tools = new WeakMap(); boxes = new WeakMap(); fieldRects = null; avoidRects = null; return costOf({c: F.dataset.corner || corner, ...pos}); }};
    return built;
}

// The page loads every script in this folder; the window installs itself once the page is there.
// The floating window is off: the top bar's run status shows the jobs. Its code stays behind one switch
// (FreeVideoTasks.float.enabled, or localStorage freevideo.taskFloat = 'on'); nothing in Settings shows it.
// The job sources always start, so leaving a view never cancels, finished pictures are filed, and the run status,
// notifications and library hear about done and failed jobs as before.
const floatEnabled = () => {
    if (globalThis.FreeVideoTasks?.float?.enabled === true) return true;
    try { return globalThis.localStorage?.getItem('freevideo.taskFloat') === 'on'; } catch { return false; }
};
if (typeof document !== 'undefined' && typeof window !== 'undefined' && !globalThis.__freevideoTaskFloatManual) {
    const tasks = globalThis.FreeVideoTasks ||= {};
    tasks.jobs = jobs;
    tasks.float = {enabled: floatEnabled(), install: () => { tasks.float.enabled = true; return installTaskFloat(); }};
    const start = () => {
        try { startJobSources(); } catch (error) { console.warn('FreeVideo jobs:', error); }
        if (tasks.float.enabled) try { installTaskFloat(); } catch (error) { console.warn('FreeVideo floating window:', error); }
    };
    if (document.body) start(); else addEventListener('DOMContentLoaded', start, {once: true});
}
