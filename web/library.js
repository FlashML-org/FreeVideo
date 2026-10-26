import { api } from '../../scripts/api.js';
import { closeDialog } from './motion.js';
import { resultActions, previewBadge, icon } from './result_actions.js';
import { videoUnavailableText } from './studio_extend.js';
import { topDialog } from './dialog_keys.js';
import { isNetworkTypeError, connectionLostText } from './browser_extension.js';
import { issueButton } from './issue_button.js';

const style = document.createElement('link');
style.rel = 'stylesheet'; style.href = new URL('./library.css', import.meta.url).href; document.head.append(style);
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const button = (text, action, cls = '') => { const b = el('button', text, cls); b.type = 'button'; b.onclick = action; return b; };
const view = file => {
    const split = file.lastIndexOf('/');
    return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(split + 1), subfolder: file.slice(0, split), type: 'output'}));
};
let opened;

// Batches that can still be undone live outside the dialog: closing and reopening it, a
// page reload, a second delete within the undo time and other tabs all see the same list.
const UNDO_KEY = 'freevideo.library.undo';
const FILTER_KEY = 'freevideo.library.filter';
let undoMemory = [];
// Batches this page dropped: a store that cannot be written must not bring them back.
const forgotten = new Set();
const PENDING_LIMIT = 10 * 60 * 1000;
const jsonHeaders = {'Content-Type': 'application/json'};
const undoRows = () => {
    // What this page knows and what the store holds are joined by batch, so a failed or late write
    // from one side never drops a batch the other still has; the page's own copy is the newer one.
    const byToken = new Map();
    let stored = [];
    try { const value = JSON.parse(localStorage.getItem(UNDO_KEY)); if (Array.isArray(value)) stored = value; } catch { /* Memory only. */ }
    for (const row of [...stored, ...undoMemory]) if (row && typeof row.token === 'string' && !forgotten.has(row.token)) byToken.set(row.token, row);
    const now = Date.now();
    // A batch still being deleted is kept until the server has answered for it, however long that takes; the
    // time to undo only runs from the answer. A request the server has never heard of is dropped after a while.
    return [...byToken.values()].filter(row => row.count > 0 && (row.pending ? row.confirmed || now < (row.born || row.due) + PENDING_LIMIT : row.due > now));
};
const saveUndoRows = rows => {
    undoMemory = rows;
    try { localStorage.setItem(UNDO_KEY, JSON.stringify(rows)); } catch { /* Memory only. */ }
};
// A row is ``pending`` from the request until the server has answered it: the batch may exist already, but it is not offered yet.
const rememberUndo = (token, count, seconds, pending = false) =>
    saveUndoRows([...undoRows().filter(row => row.token !== token), {token, count, due: Date.now() + seconds * 1000, pending, born: Date.now()}]);
const forgetUndo = tokens => { for (const token of tokens) forgotten.add(token); saveUndoRows(undoRows().filter(row => !tokens.includes(row.token))); };
const setUndoDue = (tokens, seconds) => saveUndoRows(undoRows().map(row => tokens.includes(row.token) ? {...row, due: Date.now() + seconds * 1000} : row));
// Batches this page is still deleting: they are kept for a reload but are not offered for undo yet.
const deletingNow = new Set();
const offeredUndoRows = () => undoRows().filter(row => !deletingNow.has(row.token) && !row.pending);
const undoChangedHere = () => window.dispatchEvent(new CustomEvent('freevideo-library-undo'));
// The server is the authority on what can still be undone; what it says replaces what this page guessed.
const applyUndoStates = states => {
    const now = Date.now(), before = undoRows();
    const after = before.flatMap(row => {
        const state = deletingNow.has(row.token) ? null : states[row.token];
        if (!state) return [row];
        if (state.state === 'ready') {
            const due = now + Math.max(0, Number(state.seconds) || 0) * 1000, count = state.count > 0 ? state.count : row.count;
            // The server's time moves by the round trip only; that is not a change other tabs need to hear about.
            return [!row.pending && count === row.count && Math.abs(due - row.due) < 2000 ? row : {...row, pending: false, confirmed: false, count, due}];
        }
        // Once the server has said the batch exists, it is kept until the server says it is ready or gone.
        if (state.state === 'busy') return [row.confirmed && row.due > now + 30000 ? row : {...row, confirmed: true, due: now + 60000}];
        if (row.pending && !row.confirmed) return [row];
        forgotten.add(row.token);
        return [];
    });
    // Written only when something changed: two open tabs would otherwise answer each other's writes for ever.
    if (after.length !== before.length || after.some((row, index) => row !== before[index])) saveUndoRows(after);
};
async function askUndoState(path, body) {
    try {
        const response = await api.fetchApi(path, {method: 'POST', headers: jsonHeaders, body: JSON.stringify(body), signal: AbortSignal.timeout(5000)});
        if (!response.ok) return null;
        return (await response.json()).batches || {};
    } catch { return null; }
}
// Keeps earlier batches of one click undoable while its next request runs, then gives them the normal time again.
async function holdBatches(tokens, running) {
    const states = await askUndoState('/freevideo/library/hold', {batches: tokens, running});
    if (!states) return false;
    applyUndoStates(states);
    return true;
}
// The page names a batch before asking for the delete, so a reload while it runs still finds it.
const newBatchName = () => {
    const random = crypto.getRandomValues(new Uint8Array(3));
    return `${Date.now()}${String(Math.floor(Math.random() * 1e6)).padStart(6, '0')}-${[...random].map(byte => byte.toString(16).padStart(2, '0')).join('')}`;
};

export async function libraryPage(t, {limit = 24, before = null, favorite = null, signal} = {}) {
    const query = new URLSearchParams({limit: String(limit)});
    if (before) query.set('before', before);
    if (favorite !== null) query.set('favorite', String(favorite));
    const response = await api.fetchApi('/freevideo/library?' + query, {signal});
    if (response.status === 404) throw Object.assign(new Error(t('Restart ComfyUI to open your creations after updating.', '更新后重启 ComfyUI，可打开作品。')), {restart: true});
    if (!response.ok) throw new Error(t('Could not load your creations. Try Refresh.', '作品暂时无法读取，请点击刷新。'));
    return response.json();
}

export function videoTimestamp(row, t) {
    const value = new Date(row.created_at);
    if (Number.isNaN(value.getTime())) return t('Saved video', '已保存的视频');
    if (t('en', 'zh') === 'zh') return `${value.getMonth() + 1}月${value.getDate()}日 ${String(value.getHours()).padStart(2, '0')}:${String(value.getMinutes()).padStart(2, '0')}`;
    return value.toLocaleString('en-US', {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true});
}

export function videoDimensions(row) {
    const g = row.geometry || {}, parts = [];
    // A preview's file is its first pass; geometry names the finished size.
    const shown = row.preview && row.preview_geometry?.width ? row.preview_geometry : g;
    if (shown.width && shown.height) parts.push(`${shown.width} × ${shown.height}`);
    const duration = g.seconds || (g.frames && g.frames / (g.fps || 24));
    if (duration) parts.push(`${Number(duration).toFixed(1)} s`);
    return parts.join(' · ');
}

export function videoThumbnailURL(row) {
    const id = row.id || row.video.replace(/^FreeVideo\//, '').replace(/\/video\.mp4$/, '');
    return api.apiURL('/freevideo/library/thumbnail?' + new URLSearchParams({id}));
}

export async function latestVideo() {
    const response = await api.fetchApi('/freevideo/library?limit=1');
    if (!response.ok) return null;
    return (await response.json()).items?.[0] || null;
}

// Earlier videos can get their workflow embedded, but only after the user confirms it.
async function workflowBackfill(collection, t) {
    const status = async () => { try { const r = await api.fetchApi('/freevideo/workflow-backfill'); return r.ok ? r.json() : null; } catch { return null; } };
    const post = action => api.fetchApi('/freevideo/workflow-backfill', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action})});
    let value = await status();
    if (!value || value.disabled || value.dismissed || (!value.count && value.state !== 'running')) return;
    const box = el('div', null, 'fv-backfill'); box.setAttribute('role', 'status');
    const heading = el('strong'), text = el('p'), actions = el('div', null, 'fv-backfill-actions');
    box.append(heading, text, actions); collection.prepend(box);
    const gb = bytes => (bytes / 2 ** 30).toFixed(bytes < 2 ** 30 ? 2 : 1);
    async function follow() {
        heading.textContent = t('Adding workflows…', '正在补写工作流…');
        text.textContent = t('Each original video is kept next to it as video.before-workflow.mp4.', '每个原视频都会保留在同一目录，文件名为 video.before-workflow.mp4。');
        actions.replaceChildren();
        while ((value = await status()) && value.state === 'running') await new Promise(resolve => setTimeout(resolve, 1500));
        const result = value?.result || {};
        heading.textContent = result.embedded ? t(`Workflows added to ${result.embedded} videos`, `已为 ${result.embedded} 个视频补上工作流`) : t('No video was changed', '没有视频被改动');
        text.textContent = result.embedded
            ? t('Drag them onto the canvas to restore their workflow. The originals are kept next to them as video.before-workflow.mp4.', '把它们拖到画布上可还原工作流。原视频保留在各自目录，文件名为 video.before-workflow.mp4。')
            : t('Videos that could not be checked safely were left as they were.', '无法安全确认的视频都保持原样。');
        if (result.failed) text.textContent += ' ' + t(`${result.failed} videos were left unchanged and will be offered again.`, `另有 ${result.failed} 个视频未改动，之后会再次提示。`);
        const full = Math.min(Number.isSafeInteger(result.no_space) ? result.no_space : 0, result.failed || 0);
        if (full > 0) text.textContent += ' ' + t(full === result.failed ? 'The disk is full. Free some space first.' : `${full} of them could not be changed because the disk is full. Free some space first.`,
            full === result.failed ? '磁盘空间不够，请先空出空间。' : `其中 ${full} 个因磁盘空间不够，请先空出空间。`);
        actions.replaceChildren(button(t('Got it', '知道了'), () => box.remove(), 'fv-quiet'));
    }
    if (value.state === 'running') { follow(); return; }
    heading.textContent = t('Add workflows to earlier videos', '为之前的视频补上工作流');
    text.textContent = t(`${value.count} earlier videos can get the workflow they were made with, so dropping them on the canvas restores it. Nothing changes until you confirm, and each original is kept as a backup (about ${gb(value.bytes)} GB).`,
        `有 ${value.count} 个之前生成的视频可以补上生成时的工作流，补写后拖到画布可还原。确认前不会改动任何文件，每个原视频都会保留一份备份（约 ${gb(value.bytes)} GB）。`);
    const run = button(t('Add workflows', '补写工作流'), async () => {
        run.disabled = later.disabled = true;
        try { if (!(await post('run')).ok) throw new Error(); follow(); }
        catch { run.disabled = later.disabled = false; text.textContent = t('Could not start. Try again.', '没能开始，请重试。'); }
    }, 'fv-primary');
    const later = button(t('Don\'t ask again', '不再提示'), async () => { box.remove(); try { await post('dismiss'); } catch {} }, 'fv-quiet');
    actions.append(run, later);
}

// ``upscale`` queues the second pass of a first-pass preview (the studio's node).
export function openLibrary(t, {upscale = null, selectedWork = null, extend = null, back: backLabel = null} = {}) {
    if (opened?.open) { if (selectedWork) opened.close(); else { opened.focus(); return; } }
    const dialog = el('dialog', null, 'fv-studio fv-library'); opened = dialog;
    // `kind` (text, or a function read when the button is clicked) tells the feedback what the sentence alone cannot.
    const fault = (target, text, where = ['Creations', '作品库'], kind = null) => {
        target.textContent = text;
        target.append(issueButton(t, () => ({where, message: text, kind: typeof kind === 'function' ? kind() : kind})));
    };
    dialog.setAttribute('aria-label', t('Your creations', '我的作品'));
    const header = el('header', null, 'fv-library-header');
    // Closing returns to the view this was opened from; that view names itself on the button.
    const backText = backLabel || t('Back to create', '返回创作');
    const back = button('', () => closeDialog(dialog), 'fv-quiet fv-library-back');
    back.setAttribute('aria-label', backText); back.title = backText;
    back.append(icon('back'), el('span', backText, 'fv-library-back-label'));
    const title = el('h2', t('Your creations', '我的作品'));
    const refresh = button(t('Refresh', '刷新'), () => load(false), 'fv-quiet');
    const choose = button(t('Select', '选择'), () => setSelecting(!selecting), 'fv-quiet fv-library-choose');
    const tools = el('div', null, 'fv-library-tools'); tools.append(choose, refresh);
    header.append(back, title, tools);
    // Try the full label on each resize, so wider windows restore it too.
    const headerObserver = new ResizeObserver(() => {
        header.dataset.compact = 'false';
        header.dataset.compact = String(title.scrollWidth > title.clientWidth || header.scrollWidth > header.clientWidth);
    });
    headerObserver.observe(header); headerObserver.observe(tools);
    const body = el('div', null, 'fv-library-body');
    const collection = el('section', null, 'fv-library-collection'); collection.setAttribute('aria-label', t('Saved videos', '已保存的视频'));
    let favoritesOnly = false;
    try { favoritesOnly = localStorage.getItem(FILTER_KEY) === 'favorites'; } catch { /* Show all. */ }
    const filters = el('div', null, 'fv-pass-mode fv-library-filter');
    filters.setAttribute('role', 'radiogroup'); filters.setAttribute('aria-label', t('Show', '显示'));
    const filterAll = button(t('All', '全部'), () => changeFilter(false));
    const filterFavorites = button(t('Favorites', '收藏'), () => changeFilter(true));
    for (const option of [filterAll, filterFavorites]) option.setAttribute('role', 'radio');
    filters.append(filterAll, filterFavorites);
    function syncFilter() {
        filterAll.setAttribute('aria-checked', String(!favoritesOnly));
        filterFavorites.setAttribute('aria-checked', String(favoritesOnly));
    }
    function changeFilter(value) {
        if (deleting || favoritesOnly === value) return;
        favoritesOnly = value;
        try { localStorage.setItem(FILTER_KEY, value ? 'favorites' : 'all'); } catch { /* This view still works. */ }
        syncFilter(); setSelecting(false); next = null; more.hidden = true;
        thumbnails.disconnect(); grid.replaceChildren(); cards.clear(); rows.clear(); unread = 0; unreadable.textContent = '';
        releasePlayer(); selected = null; frame.replaceChildren(); links.replaceChildren(); date.textContent = geometry.textContent = '';
        stats.hidden = true; stats.replaceChildren(); budget.textContent = ''; dialog.dataset.detail = 'false';
        load(false);
    }
    syncFilter();
    const grid = el('div', null, 'fv-library-grid');
    const message = el('p', '', 'fv-library-message'); message.setAttribute('role', 'status');
    const more = button(t('Show more', '加载更多'), () => load(true), 'fv-quiet'); more.hidden = true;
    // Creations whose saved record could not be read stay on disk; say how many the loaded pages skipped.
    const unreadable = el('p', '', 'fv-library-skipped fv-library-unreadable'); let unread = 0;
    // Selecting several creations: a strip above the cards, the delete bar below them.
    const picking = el('div', null, 'fv-library-picking'); picking.hidden = true;
    const pickAll = button(t('Select all', '全选'), () => selectAll(), 'fv-quiet');
    const pickNone = button(t('Select none', '全不选'), () => { picked.clear(); syncSelection(); }, 'fv-quiet');
    picking.append(pickAll, pickNone);
    const undoBar = el('p', null, 'fv-library-undo'); undoBar.setAttribute('role', 'status'); undoBar.hidden = true;
    const bar = el('div', null, 'fv-library-selection'); bar.hidden = true;
    const barLabel = el('strong'), barText = el('small'), barActions = el('div', null, 'fv-library-selection-actions');
    const barCancel = button(t('Cancel', '取消'), () => { confirming = false; syncSelection(); }, 'fv-quiet');
    const barDelete = button('', () => confirmDelete(), 'fv-library-danger');
    barActions.append(barCancel, barDelete);
    bar.append(barLabel, barText, barActions);
    // The undo note and the delete bar share one sticky dock so they stack instead of covering each other.
    const dock = el('div', null, 'fv-library-dock'); dock.append(undoBar, bar);
    collection.append(filters, picking, grid, message, more, unreadable, dock);
    workflowBackfill(collection, t);
    const detail = el('section', null, 'fv-library-detail');
    const all = button(t('All creations', '全部作品'), () => {
        dialog.dataset.detail = 'false'; player?.pause(); cards.get(selected)?.focus({preventScroll: true});
    }, 'fv-quiet fv-library-all');
    const frame = el('div', null, 'fv-library-frame');
    const caption = el('div', null, 'fv-library-caption');
    const date = el('strong'), geometry = el('span'); caption.append(date, geometry);
    const stats = el('div', null, 'fv-stats'); stats.hidden = true;
    stats.setAttribute('aria-label', t('Generation statistics', '生成统计'));
    const budget = el('div', '', 'fv-budget');
    const links = el('div', null, 'fv-result-links');
    const notice = el('p', '', 'fv-library-message fv-library-notice'); notice.setAttribute('role', 'status');
    detail.append(all, frame, caption, links, notice, stats, budget);
    body.append(collection, detail); dialog.append(header, body);
    let disposed = false, loading = false, next = null, selected = null, player = null, arrivals = [];
    let listAbort = null, loadRound = 0;
    let selecting = false, confirming = false, deleting = false, anchor = null, undoTimer = null, sizeTimer = null, sizeRound = 0;
    const picked = new Set(), sizes = new Map();
    const rows = new Map(), cards = new Map();
    const abort = new AbortController();
    const narrow = matchMedia('(max-width: 680px)');
    const timestamp = row => videoTimestamp(row, t), dimensions = videoDimensions;
    function releasePlayer() {
        if (!player) return;
        player.pause(); player.removeAttribute('src'); player.load(); player = null;
    }
    function mountPlayer(row) {
        releasePlayer(); frame.replaceChildren();
        player = el('video'); player.controls = true; player.preload = 'metadata'; player.playsInline = true;
        player.src = view(row.video);
        // The picture's own box, so a preview's badge sits on its corner.
        const media = el('div', null, 'fv-library-media');
        const g = row.geometry || {};
        if (g.width > 0 && g.height > 0) frame.style.setProperty('--fv-ratio', String(g.width / g.height));
        media.append(player);
        if (row.preview) media.append(previewBadge(row, t));
        frame.append(media);
        player.addEventListener('error', event => {
            if (!disposed && selected === row.video && event.currentTarget === player) {
                const note = el('p', videoUnavailableText(t), 'fv-library-message');
                // The player only says it failed: its error code and what the file's address answers tell a moved or
                // empty file from an unsupported encoding, and travel with the feedback.
                let detail = 'media-error-' + (player.error?.code ?? 'none');
                const source = player.currentSrc || player.src;
                fault(note, videoUnavailableText(t), undefined, () => detail);
                frame.replaceChildren(note);
                fetch(source, {method: 'HEAD', signal: AbortSignal.timeout(5000)}).then(response => {
                    const type = (response.headers.get('content-type') || '').replace(/[^\w./+-]/g, '').slice(0, 40);
                    detail += ` http-${response.status} length-${response.headers.get('content-length') ?? 'none'} type-${type || 'none'}`;
                }, () => { detail += ' head-failed'; });
            }
        }, {once: true});
    }
    function select(row, reveal = true) {
        if (selected !== row.video || links.dataset.favorite !== String(!!row.favorite)) {
            const changedVideo = selected !== row.video;
            selected = row.video;
            if (changedVideo) mountPlayer(row);
            links.dataset.favorite = String(!!row.favorite);
            date.textContent = timestamp(row); geometry.textContent = dimensions(row);
            links.replaceChildren(resultActions(row, t, {
                extend: async saved => {
                    const isCurrent = () => !disposed && dialog.open && dialog.dataset.closing !== 'true' && topDialog() === dialog && selected === saved.video;
                    if (!isCurrent()) return;
                    player?.pause();
                    const run = extend || (async (record, options) => (await import('./studio.js')).openExtendEditor(record, undefined, options));
                    try {
                        const editor = await run(saved, {isCurrent});
                        if (!editor && isCurrent()) throw new Error(t('Could not open the editor. Try again.', '无法打开剪辑，请重试。'));
                        if (editor && !disposed) { dialog.close(); editor.element?.focus({preventScroll: true}); }
                        return editor;
                    }
                    catch (error) { fault(notice, error.message, ['Continue video', '延长视频']); }
                },
                secondPass: row.preview && upscale ? () => secondPass(row) : null,
                favorite: value => saveFavorite(row, value),
                remove: () => remove(row)}));
            notice.textContent = '';
            for (const [file, card] of cards) card.setAttribute('aria-pressed', String(selecting ? picked.has(file) : file === selected));
        }
        // These are the saved generation's measurements, including when the
        // same MP4 has since been reused. Never substitute the active request.
        const measured = value => Number.isFinite(value) && value >= 0;
        const number = (value, scale, unit) => measured(value) ? `${(value / scale).toFixed(1)} ${unit}` : '—';
        stats.replaceChildren();
        for (const [label, value, scale, unit] of [
            [t('Generation', '生成用时'), row.sample_seconds, 1, 's'],
            [t('Total time', '总用时'), row.request_seconds, 1, 's'],
            [t('Peak VRAM', '最高显存'), row.vram_peak_bytes, 2 ** 30, 'GiB'],
            [t('Peak RAM', '最高内存'), row.ram_peak_bytes, 2 ** 30, 'GiB'],
        ]) {
            const item = el('div', null, 'fv-stat');
            item.append(el('strong', number(value, scale, unit)), el('span', label)); stats.append(item);
        }
        // A preview's timings describe only its first pass; they stay in its report.
        stats.hidden = row.preview || ![row.sample_seconds, row.request_seconds, row.vram_peak_bytes, row.ram_peak_bytes].some(measured);
        budget.textContent = !row.preview && measured(row.gpu_budget_bytes)
            ? `${t('VRAM budget', '可用显存预算')} ${number(row.gpu_budget_bytes, 2 ** 30, 'GiB')} · ${t('Device', '显卡总量')} ${number(row.gpu_total_bytes, 2 ** 30, 'GiB')}` : '';
        if (reveal) dialog.dataset.detail = 'true';
    }
    const cardLabel = row => `${timestamp(row)} · ${dimensions(row)}${row.favorite ? ` · ${t('Favorite', '已收藏')}` : ''}`;
    function syncFavorite(row) {
        const card = cards.get(row.video), badge = card?.querySelector('.fv-library-favorite');
        if (badge) badge.hidden = !row.favorite;
        card?.setAttribute('aria-label', cardLabel(row));
    }
    async function saveFavorite(row, favorite) {
        const id = row.id || row.video.replace(/^FreeVideo\//, '').replace(/\/video\.mp4$/, '');
        notice.textContent = '';
        try {
            const response = await api.fetchApi('/freevideo/library/favorite', {method: 'POST',
                headers: jsonHeaders, body: JSON.stringify({id, favorite}), signal: abort.signal});
            if (!response.ok) throw new Error('Favorite unavailable');
            const value = await response.json();
            if (value.favorite !== favorite) throw new Error('Invalid favorite response');
        } catch (error) {
            if (!disposed && error.name !== 'AbortError') {
                const text = isNetworkTypeError(error) ? connectionLostText(t)
                    : favorite ? t('Could not save the favorite. Try again.', '无法保存收藏，请重试。')
                    : t('Could not remove the favorite. Try again.', '无法取消收藏，请重试。');
                fault(notice, text, ['Favorites', '收藏']);
            }
            throw error;
        }
        row.favorite = favorite;
        if (disposed) return;
        const current = rows.get(row.video);
        if (current) current.favorite = favorite;
        syncFavorite(row);
        if (selected === row.video) links.dataset.favorite = String(favorite);
        // Under Favorites an un-favorited card stays until the filter changes or the list is refreshed,
        // so the creation being viewed does not vanish and one more click restores it.
        if (loading || (favoritesOnly && favorite && !current)) await load(false);
    }
    async function secondPass(row) {
        notice.textContent = '';
        try {
            await upscale(row);
            if (!disposed && selected === row.video) notice.textContent = t('Queued. It appears in "Creations" when done.', '已加入队列，完成后在“作品”里。');
        } catch (error) {
            if (!disposed && selected === row.video) fault(notice, t('Could not start the second pass: ', '无法继续二采：') + error.message, ['Second pass', '二采']);
            throw error;
        }
    }
    // Throws a readable Error for the confirmation to show; on success the
    // creation leaves the collection.
    async function remove(row) {
        // Windows keeps a streamed video open; release it before deleting.
        if (selected === row.video) releasePlayer();
        let value = {};
        try {
            const response = await api.fetchApi('/freevideo/library/delete', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: row.id})});
            value = await response.json().catch(() => ({}));
            if (!response.ok || !value.deleted) throw new Error(value.reason || 'failed');
        } catch (error) {
            if (!disposed && selected === row.video) mountPlayer(row);
            throw Object.assign(new Error(error.message === 'foreign-files'
                ? t('Not deleted: the folder also holds files FreeVideo did not write: ', '未删除：文件夹里还有不是 FreeVideo 生成的文件：') + (value.kept || []).join(', ')
                : error.message === 'editor-in-use' ? t('Not deleted: these edits use it: ', '未删除：这些剪辑正在使用它：') + (value.kept || []).join(', ')
                : error.message === 'editor-unavailable' ? t('Not deleted: saved edits could not be checked. Try again in a moment.', '未删除：暂时无法检查已保存的剪辑，请稍后再试。')
                : error.message === 'continuation-in-use' ? t('Not deleted: a video continuing from it is running or queued. Try again after it finishes.', '未删除：接在这条视频后面的生成正在运行或排队，请等它完成后再试。')
                : error.message === 'in-use' ? t('Not deleted: its second pass is running, or the video is still open. Try again in a moment.', '未删除：正在用它跑二采，或视频仍被占用，请稍后再试。')
                : t('Could not delete this creation.', '无法删除这条作品。')), {
                    reportable: !['foreign-files', 'editor-in-use', 'continuation-in-use', 'in-use'].includes(error.message),
                });
        }
        // Other views of the library (the studio's Recent videos) drop it too.
        window.dispatchEvent(new CustomEvent('freevideo-library-deleted', {detail: {video: row.video}}));
        if (disposed) return;
        rows.delete(row.video); cards.get(row.video)?.remove(); cards.delete(row.video);
        if (selected === row.video) {
            selected = null; frame.replaceChildren(); links.replaceChildren(); date.textContent = geometry.textContent = '';
            stats.hidden = true; stats.replaceChildren(); budget.textContent = '';
            const next = rows.values().next().value;
            if (next && !narrow.matches) select(next, false); else dialog.dataset.detail = 'false';
        }
        notice.textContent = t('Deleted.', '已删除。');
        if (!rows.size) message.textContent = t('Your finished videos will appear here.', '生成完成的视频会保存在这里。');
    }
    // --- Selecting several creations ---
    const gb = bytes => `${(bytes / 2 ** 30).toFixed(bytes < 2 ** 30 ? 2 : 1)} GB`;
    const ordered = () => [...grid.querySelectorAll('.fv-library-card')];
    const dayOf = video => (rows.get(video)?.id || '').slice(0, 10);
    const lastDay = () => dayOf(ordered().at(-1)?.dataset.video);
    const dayName = day => {
        const [y, m, d] = day.split('-').map(Number), value = new Date(y, (m || 1) - 1, d || 1);
        return Number.isNaN(value.getTime()) ? day : (t('en', 'zh') === 'zh' ? `${value.getMonth() + 1}月${value.getDate()}日 ${value.toLocaleDateString('zh-CN', {weekday: 'short'})}` : value.toLocaleDateString('en-US', {month: 'long', day: 'numeric', weekday: 'short'}));
    };
    // Day headings exist only while selecting; the cards keep their order.
    function regroup() {
        for (const heading of grid.querySelectorAll('.fv-library-day')) heading.remove();
        if (!selecting) return;
        let day = null;
        for (const card of ordered()) {
            const value = dayOf(card.dataset.video);
            if (value === day) continue;
            day = value;
            const heading = el('div', null, 'fv-library-day'); heading.dataset.day = value;
            const name = el('span', dayName(value));
            const whole = button('', async () => {
                if (deleting) return;
                // The day goes on until a card of an earlier day or the end of the list is loaded.
                whole.disabled = true;
                while (next && !disposed && lastDay() === value && await load(true));
                if (disposed || (next && lastDay() === value)) { whole.disabled = false; return; }
                const videos = ordered().map(card => card.dataset.video).filter(video => dayOf(video) === value);
                const all = videos.every(video => picked.has(video));
                for (const video of videos) all ? picked.delete(video) : picked.add(video);
                anchor = null; syncSelection();
            }, 'fv-quiet');
            heading.append(name, whole);
            grid.insertBefore(heading, card);
        }
        syncSelection();
    }
    function pick(video, range) {
        const list = ordered().map(card => card.dataset.video), to = list.indexOf(video), from = list.indexOf(anchor);
        if (range && from >= 0 && to >= 0) {
            const on = !picked.has(video);
            for (const item of list.slice(Math.min(from, to), Math.max(from, to) + 1)) on ? picked.add(item) : picked.delete(item);
        } else picked.has(video) ? picked.delete(video) : picked.add(video);
        anchor = video; confirming = false; syncSelection();
    }
    function wantedSizes() {
        const ids = [...picked].map(video => rows.get(video)?.id).filter(id => id && !sizes.has(id));
        if (!ids.length) return;
        clearTimeout(sizeTimer);
        sizeTimer = setTimeout(async () => {
            const round = ++sizeRound;
            try {
                for (let at = 0; at < ids.length; at += 500) {
                    const response = await api.fetchApi('/freevideo/library/sizes', {method: 'POST',
                        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ids: ids.slice(at, at + 500)}), signal: abort.signal});
                    if (!response.ok) return;
                    for (const [id, bytes] of Object.entries((await response.json()).sizes || {})) sizes.set(id, bytes);
                }
            } catch { return; }
            if (!disposed && round === sizeRound) syncSelection(false);
        }, 250);
    }
    const pickedSize = () => {
        let total = 0;
        for (const video of picked) { const bytes = sizes.get(rows.get(video)?.id); if (bytes == null) return null; total += bytes; }
        return total;
    };
    function syncSelection(fetchSizes = true) {
        dialog.dataset.selecting = String(selecting);
        choose.textContent = selecting ? t('Cancel', '取消') : t('Select', '选择');
        picking.hidden = bar.hidden = !selecting;
        for (const video of [...picked]) if (!rows.has(video)) picked.delete(video);
        for (const [video, card] of cards) {
            card.classList.toggle('fv-picked', selecting && picked.has(video));
            card.setAttribute('aria-pressed', String(selecting ? picked.has(video) : video === selected));
        }
        pickAll.disabled = deleting; pickNone.disabled = deleting || !picked.size;
        for (const heading of grid.querySelectorAll('.fv-library-day')) {
            const videos = ordered().map(card => card.dataset.video).filter(video => dayOf(video) === heading.dataset.day);
            const loaded = !(next && heading.dataset.day === lastDay());
            heading.lastElementChild.textContent = loaded && videos.length && videos.every(video => picked.has(video)) ? t('Deselect day', '取消这一天') : t('Select day', '选择这一天');
        }
        if (!selecting) return;
        const count = picked.size, size = pickedSize();
        if (fetchSizes) wantedSizes();
        const about = size == null ? '' : ` · ${t('about', '约')} ${gb(size)}`;
        barActions.hidden = !count;
        barCancel.hidden = !confirming;
        if (confirming) {
            barLabel.textContent = t(`Delete ${count} ${count === 1 ? 'creation' : 'creations'}?${size == null ? '' : ` About ${gb(size)}.`}`, `删除 ${count} 个作品？${size == null ? '' : `共约 ${gb(size)}。`}`);
            barText.textContent = t('The videos and their files will be deleted.', '视频和相关文件都会删除。');
            barDelete.textContent = deleting ? t('Deleting…', '正在删除…') : t('Delete', '删除');
            barCancel.disabled = barDelete.disabled = deleting;
        } else {
            barLabel.textContent = count ? '' : t('Select creations to delete', '选择要删除的作品');
            barText.textContent = '';
            barDelete.textContent = t(`Delete ${count}`, `删除 ${count} 个`) + about;
            barDelete.disabled = deleting;
        }
        barLabel.hidden = !barLabel.textContent; barText.hidden = !barText.textContent;
    }
    function setSelecting(value) {
        if (selecting === value || deleting) return;
        selecting = value; confirming = false; anchor = null; picked.clear();
        if (value) dialog.dataset.detail = 'false';
        else if (!narrow.matches && selected) dialog.dataset.detail = 'true';
        regroup(); syncSelection();
    }
    async function selectAll() {
        pickAll.disabled = true;
        while (next && !disposed && await load(true));
        if (disposed) return;
        for (const video of rows.keys()) picked.add(video);
        confirming = false; syncSelection();
    }
    async function confirmDelete() {
        if (!picked.size || deleting) return;
        if (!confirming) {
            confirming = true; syncSelection(); barDelete.focus();
            // The confirmation names the size; wait briefly for it if it has not arrived.
            if (pickedSize() == null) {
                for (let wait = 0; wait < 20 && !disposed && pickedSize() == null && confirming; wait++) await new Promise(resolve => setTimeout(resolve, 100));
                if (!disposed) syncSelection(false);
            }
            return;
        }
        await removeSelected();
    }
    let undoNote = null, undoFailure = '';
    // Shows what can still be undone: every batch deleted within the undo time, from any
    // dialog, tab or page load. Without any, only the notes remain.
    function showUndo() {
        clearTimeout(undoTimer);
        if (disposed) return;
        const live = offeredUndoRows(), deletedCount = live.reduce((sum, row) => sum + row.count, 0);
        const notes = [undoNote ? el('span', undoNote, 'fv-library-skipped') : null, undoFailure ? el('span', undoFailure, 'fv-library-skipped') : null].filter(Boolean);
        if (undoFailure) notes.push(issueButton(t, () => ({where: ['Delete or restore creations', '删除或恢复作品'], message: undoFailure})));
        const settle = () => { if (undoRows().length) { showUndo(); return; } undoNote = null; undoFailure = ''; undoBar.replaceChildren(); undoBar.hidden = true; };
        undoBar.replaceChildren(); undoBar.hidden = !deletedCount && !notes.length;
        if (!deletedCount) { undoBar.append(...notes); return; }
        const tokens = live.map(row => row.token);
        const text = el('span', t(`Deleted ${deletedCount}`, `已删除 ${deletedCount} 个`));
        const undo = button(t('Undo', '撤销'), async () => {
            undo.disabled = true; clearTimeout(undoTimer);
            let lost = 0, expired = false;
            const settled = [], again = [];
            for (const batch of tokens) {
                try {
                    const response = await api.fetchApi('/freevideo/library/undo', {method: 'POST',
                        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch})});
                    if (response.ok) { lost += ((await response.json()).lost || []).length; settled.push(batch); }
                    // Only the server saying the time is over ends a batch; any other failure can be tried again.
                    else if (response.status === 410) { expired = true; settled.push(batch); } else again.push(batch);
                } catch { again.push(batch); }
            }
            forgetUndo(settled);
            if (settled.length) window.dispatchEvent(new CustomEvent('freevideo-library-restored'));
            if (disposed) return;
            if (again.length) {
                undoFailure = t('Could not undo. Try again.', '没能撤销，请重试。');
                showUndo();
                if (settled.length) load(false);
                return;
            }
            undoFailure = '';
            undoBar.replaceChildren(el('span', expired ? t('The undo time has passed.', '已过了撤销时间。')
                : lost ? t(`Restored, but ${lost} could not be brought back.`, `已恢复，其中 ${lost} 个没能恢复。`) : t('Restored.', '已恢复。')));
            if (lost && !expired) {
                const message = undoBar.firstElementChild.textContent;
                undoBar.append(issueButton(t, () => ({where: ['Restore creations', '恢复作品'], message})));
            }
            undoTimer = setTimeout(settle, 4000);
            load(false);
        }, 'fv-quiet');
        undoBar.append(text, undo, ...notes);
        undoTimer = setTimeout(settle, Math.min(...live.map(row => row.due)) - Date.now() + 50);
    }
    const skippedText = skipped => {
        const using = skipped.filter(row => row.reason !== 'foreign-files').length, other = skipped.length - using;
        const folders = (count, en) => count === 1 ? en.replace('folders have', 'folder has') : en;
        if (using && other) return t(`${using + other} kept: ${using} in use, ${other} ${folders(other, 'folders have')} other files.`, `${using + other} 个已保留：${using} 个正在使用，${other} 个文件夹里有别的文件。`);
        if (using) return t(`${using} in use, kept.`, `${using} 个正在使用，已保留。`);
        return t(`${other} ${folders(other, 'folders have')} other files, kept.`, `${other} 个文件夹里有别的文件，已保留。`);
    };
    async function removeSelected() {
        deleting = true; syncSelection();
        const videos = [...picked], idOf = new Map(videos.map(video => [video, rows.get(video)?.id])), ids = videos.map(video => idOf.get(video)).filter(Boolean);
        // Windows keeps a streamed video open; release it before deleting.
        const selectedRow = rows.get(selected);
        if (picked.has(selected)) releasePlayer();
        const skipped = [], removed = new Set();
        let failed = false, unsaved = false;
        const earlier = [];
        // A chunk's creations leave the page as soon as the chunk is done, so an undo during a later chunk is not undone by it.
        const leave = gone => {
            for (const video of videos) {
                if (removed.has(video) || !gone.has(idOf.get(video))) continue;
                removed.add(video);
                window.dispatchEvent(new CustomEvent('freevideo-library-deleted', {detail: {video}}));
                if (disposed) continue;
                rows.delete(video); cards.get(video)?.remove(); cards.delete(video);
            }
        };
        let undoFor = 10;
        try {
            for (let at = 0; at < ids.length; at += 500) {
                const part = ids.slice(at, at + 500), name = newBatchName();
                // Kept before asking: a page reloaded or closed while the server works still finds the batch and offers the undo.
                rememberUndo(name, part.length, 120, true);
                deletingNow.add(name);
                try {
                    // The chunks already deleted stay undoable while this one runs; the server holds them the same way.
                    if (earlier.length && !await holdBatches(earlier, true)) { forgetUndo([name]); throw new Error('failed'); }
                    // Only an answer from the server that it refused drops the row; a request cut off by a reload
                    // or a lost connection may still have been carried out, so the batch is looked up again.
                    const response = await api.fetchApi('/freevideo/library/delete-batch', {method: 'POST',
                        headers: jsonHeaders, body: JSON.stringify({ids: part, batch: name, extend: earlier})});
                    if (!response.ok) { forgetUndo([name]); throw new Error('failed'); }
                    const value = await response.json();
                    const gone = new Set(value.deleted || []);
                    // A creation that is already gone from disk only needs to leave the page.
                    for (const row of value.skipped || []) {
                        // 'unsaved' and 'failed': the creation could not be noted or touched, so it stayed and is still selected.
                        if (row.reason === 'unavailable') gone.add(row.id); else if (row.reason === 'unsaved' || row.reason === 'failed') unsaved = true; else skipped.push(row);
                    }
                    // The server's own undo time, kept at once so a later chunk that fails, or a closed dialog, cannot lose it.
                    if (value.batch && value.batch !== name) forgetUndo([name]);
                    if (value.batch) { undoFor = Number(value.undo_seconds) || 10; rememberUndo(value.batch, (value.deleted || []).length, earlier.length || at + 500 < ids.length ? 120 : undoFor); earlier.push(value.batch); }
                    else forgetUndo([name]);
                    leave(gone);
                } finally {
                    deletingNow.delete(name);
                }
                // Offered as soon as this chunk is done, not only when the last one is.
                undoChangedHere();
            }
        } catch { failed = true; }
        if (unsaved) failed = true;
        // However it ended, the earlier chunks get the normal undo time from now; the server is asked, and
        // this page's own estimate stands when it cannot answer.
        if (earlier.length) { setUndoDue(earlier, undoFor); await holdBatches(earlier, false); }
        deleting = false;
        undoChangedHere();
        syncUndo();
        if (disposed) return;
        if (!rows.has(selected) && selected) {
            selected = null; frame.replaceChildren(); links.replaceChildren(); date.textContent = geometry.textContent = '';
            stats.hidden = true; stats.replaceChildren(); budget.textContent = '';
        } else if (selectedRow && rows.has(selected) && !player) mountPlayer(selectedRow);
        if (!rows.size) message.textContent = t('Your finished videos will appear here.', '生成完成的视频会保存在这里。');
        const kept = new Set(skipped.map(row => row.id));
        for (const video of videos) if (kept.has(idOf.get(video))) picked.delete(video);
        undoNote = skipped.length ? skippedText(skipped) : null;
        if (failed) {
            // What was deleted keeps its Undo; only the part that is still selected is tried again.
            for (const video of removed) picked.delete(video);
            confirming = false;
            undoFailure = removed.size
                ? t(`${picked.size} could not be deleted. Try again.`, `还有 ${picked.size} 个没能删除，请重试。`)
                : t('Could not delete. Try again.', '没能删除，请重试。');
            if (!picked.size) setSelecting(false); else regroup();
        } else { undoFailure = ''; setSelecting(false); }
        if (!selected && !narrow.matches && rows.size) select(rows.values().next().value, false);
        showUndo();
        // The delete button is gone; keep the keyboard inside the dialog.
        if (!dialog.contains(document.activeElement)) choose.focus();
    }
    // Asks the server about batches this page did not make or cannot vouch for (after a reload, or while a
    // request of an earlier page still runs), and again until every pending one is settled.
    let syncTimer = 0, syncing = false;
    async function syncUndo() {
        clearTimeout(syncTimer);
        if (disposed || syncing) return;
        const unsure = undoRows().filter(row => !deletingNow.has(row.token)).map(row => row.token).slice(0, 200);
        if (!unsure.length) return;
        syncing = true;
        const states = await askUndoState('/freevideo/library/undo-state', {batches: unsure});
        syncing = false;
        if (disposed) return;
        if (states) applyUndoStates(states);
        showUndo();
        if (undoRows().some(row => !deletingNow.has(row.token) && (row.pending || states?.[row.token]?.state === 'busy'))) syncTimer = setTimeout(syncUndo, 1500);
    }
    const thumbnails = new IntersectionObserver(entries => {
        for (const {target, isIntersecting} of entries) if (isIntersecting) {
            target.src = target.dataset.src; thumbnails.unobserve(target);
        }
    }, {root: collection, rootMargin: '80px'});
    function append(row, first = false) {
        if (!row?.video || (favoritesOnly && !row.favorite)) return;
        rows.set(row.video, row);
        if (cards.has(row.video)) { syncFavorite(row); return; }
        const card = button('', event => selecting ? pick(row.video, event.shiftKey) : select(rows.get(row.video)), 'fv-library-card');
        card.dataset.video = row.video;
        card.setAttribute('aria-label', cardLabel(row));
        card.setAttribute('aria-pressed', String(selecting ? picked.has(row.video) : row.video === selected));
        card.classList.toggle('fv-picked', selecting && picked.has(row.video));
        const picture = el('div', null, 'fv-library-picture');
        const image = el('img'); image.alt = ''; image.decoding = 'async';
        image.dataset.src = videoThumbnailURL(row);
        image.onerror = () => { image.hidden = true; };
        picture.append(image, el('span', '✓', 'fv-library-check'));
        const favorite = el('span', null, 'fv-library-favorite'); favorite.append(icon('heart'));
        favorite.setAttribute('aria-hidden', 'true');
        favorite.hidden = !row.favorite; picture.append(favorite);
        if (row.preview) picture.append(el('span', t('First-pass preview', '一采预览'), 'fv-preview-badge'));
        // Made by an AI agent: say so, with the name the agent gave the shot.
        if (row.agent) {
            const badge = el('span', 'Agent', 'fv-agent-badge');
            badge.title = [row.agent.label, row.agent.project, row.agent.client].filter(Boolean).join(' · ');
            picture.append(badge);
        }
        card.append(picture, el('span', row.agent?.label || timestamp(row)), el('small', row.agent?.label ? timestamp(row) : dimensions(row)));
        cards.set(row.video, card); first ? grid.prepend(card) : grid.append(card); thumbnails.observe(image);
        if (selecting) regroup();
    }
    async function load(appendPage) {
        if (disposed || (loading && appendPage)) return false;
        listAbort?.abort(); listAbort = new AbortController();
        const round = ++loadRound;
        loading = true; arrivals = []; refresh.disabled = more.disabled = true; let ok = false;
        message.textContent = rows.size ? '' : t('Loading your creations…', '正在读取作品…');
        try {
            const data = await libraryPage(t, {before: appendPage ? next : null, favorite: favoritesOnly ? true : null,
                signal: listAbort.signal}); if (disposed || round !== loadRound) return false;
            if (!appendPage) { thumbnails.disconnect(); grid.replaceChildren(); cards.clear(); rows.clear(); unread = 0; }
            unread += Number.isSafeInteger(data.unreadable) && data.unreadable > 0 ? data.unreadable : 0;
            unreadable.textContent = unread ? t(`${unread} ${unread === 1 ? 'creation' : 'creations'} could not be read`, `另有 ${unread}\u00a0个作品无法读取`) : '';
            for (const row of data.items || []) append(row);
            for (const row of arrivals) append(row, true);
            next = data.next; more.hidden = !next;
            message.textContent = rows.size ? '' : favoritesOnly ? t("No favorite creations. Click the heart in a creation's details to add one.", '暂无收藏的作品。点作品详情中的爱心图标收藏。') : t('Your finished videos will appear here.', '生成完成的视频会保存在这里。');
            if (!rows.has(selected)) {
                releasePlayer(); selected = null; frame.replaceChildren(); links.replaceChildren(); date.textContent = geometry.textContent = '';
                stats.hidden = true; stats.replaceChildren(); budget.textContent = '';
                if (!narrow.matches && rows.size) select(rows.values().next().value, false);
                else dialog.dataset.detail = 'false';
            } else select(rows.get(selected), false);
            ok = true; regroup();
            if (selectedWork) { const row = [...rows.values()].find(row => row.id === selectedWork || row.video === selectedWork); if (row) { select(row); selectedWork = null; } else if (next) queueMicrotask(() => load(true)); }
        } catch (error) {
            if (!disposed && round === loadRound && error.name !== 'AbortError') {
                if (error.restart) message.textContent = error.message;
                else fault(message, isNetworkTypeError(error) ? connectionLostText(t) : error.message);
            }
        }
        finally { if (!disposed && round === loadRound) { loading = false; refresh.disabled = more.disabled = false; } }
        return ok;
    }
    const completed = event => {
        const row = event.detail?.value;
        if (!row?.video || disposed) return;
        const saved = {...row, favorite: row.favorite ?? rows.get(row.video)?.favorite ?? false,
            created_at: row.created_at || rows.get(row.video)?.created_at || new Date().toISOString()};
        if (favoritesOnly && !saved.favorite) return;
        if (loading) arrivals.push(saved);
        append(saved, true);
        message.textContent = '';
        // Do not interrupt playback of a previous video when a new one arrives.
        if (!selected && !narrow.matches) select(rows.get(row.video), false);
    };
    window.addEventListener('freevideo-result', completed);
    dialog.addEventListener('cancel', event => { event.preventDefault(); if (selecting && !deleting) setSelecting(false); else closeDialog(dialog); });
    dialog.onclose = () => {
        headerObserver.disconnect();
        disposed = true; abort.abort(); listAbort?.abort(); thumbnails.disconnect(); releasePlayer(); clearTimeout(undoTimer); clearTimeout(sizeTimer); clearTimeout(syncTimer);
        window.removeEventListener('freevideo-result', completed); window.removeEventListener('storage', undoChanged); window.removeEventListener('freevideo-library-undo', showUndo); window.removeEventListener('freevideo-library-deleted', deletedElsewhere); dialog.remove();
        if (opened === dialog) opened = null;
    };
    // Another tab undid or added a batch, or this one is opened again within the undo time.
    const undoChanged = event => { if (!event.key || event.key === UNDO_KEY) { showUndo(); syncUndo(); } };
    window.addEventListener('storage', undoChanged); window.addEventListener('freevideo-library-undo', showUndo);
    // A delete that an earlier opening of the dialog is still carrying out takes its creations off this one too.
    const deletedElsewhere = event => {
        const video = event.detail?.video;
        if (!video || !rows.has(video)) return;
        rows.delete(video); cards.get(video)?.remove(); cards.delete(video);
        if (selected === video) { selected = null; frame.replaceChildren(); links.replaceChildren(); date.textContent = geometry.textContent = ''; stats.hidden = true; stats.replaceChildren(); budget.textContent = ''; dialog.dataset.detail = 'false'; }
        picked.delete(video);
    };
    window.addEventListener('freevideo-library-deleted', deletedElsewhere);
    document.body.append(dialog); dialog.showModal(); load(false); showUndo(); syncUndo();
}
