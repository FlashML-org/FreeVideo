import { api } from '../../scripts/api.js';
import { closeDialog } from './motion.js';
import { resultActions, previewBadge, icon } from './result_actions.js';
import { videoUnavailableText, canExtend } from './studio_extend.js';
import { savedSettings } from './result_reuse.js';
import { listenDialogKey } from './dialog_keys.js';
import { isNetworkTypeError, connectionLostText } from './browser_extension.js';
import { issueButton } from './issue_button.js';
import { fileFailureText, responseFileReason } from './file_failure.js';
import { userMessage } from './user_message.js';
import { applyTasks, toggledTask } from './task_groups.js';
import { outputDownloadURL } from './output_download.js';
import { attachContextMenu, openContextMenu, contextMenuOpen, compatView, reducedMotion } from './context_menu.js';
import { createMeta, createPictureBin, dayOf, inOrderPictures, matches, needsEverything, pictureRow, readView, saveView,
    sortRows, workKey, TAG_COLORS } from './library_meta.js';
import { createMetaStore, promptOfRow } from './image_view_history.js';
import { attach as attachPlayerBar } from './glass/player_bar.js';
import { hasPictureThumbnail, mediaQueue, pictureThumbnailURL, watchCards } from './work_media.js';
import { zoomable } from './picture_zoom.js';
import { createUpscaleOffer, upscale4kOffer } from './upscale_offer.js';
import { capabilityPoints } from './chain_support.js';

const style = document.createElement('link');
style.rel = 'stylesheet'; style.href = new URL('./library.css', import.meta.url).href; document.head.append(style);
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const button = (text, action, cls = '') => { const b = el('button', text, cls); b.type = 'button'; b.onclick = action; return b; };
// The hover tip (tooltip.js): a name, what it does, and its key.
const tip = (element, name, use = '', key = '') => {
    element.dataset.fvTipName = name; if (use) element.dataset.fvTipUse = use; else delete element.dataset.fvTipUse;
    if (key) element.dataset.fvTipKey = key; else delete element.dataset.fvTipKey;
    return element;
};
const view = (file, type = 'output') => {
    const split = file.lastIndexOf('/');
    return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(split + 1), subfolder: file.slice(0, split), type}));
};
// A picture lives where the image engine keeps it: the history row's own folder type when it names one, else ComfyUI's
// input folder (the image engine's output directory sits there); videos stay in the output folder.
const pictureURL = row => view(row.file, ['input', 'output', 'temp'].includes(row.type) ? row.type : 'input');
const LINE = {
    grid: '<rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="9" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="2.5" y="9" width="4.5" height="4.5" rx="1"/><rect x="9" y="9" width="4.5" height="4.5" rx="1"/>',
    video: '<rect x="1.5" y="3.5" width="9" height="9" rx="1.5"/><path d="m10.5 7 4-2.5v7l-4-2.5"/>',
    picture: '<rect x="2" y="2.5" width="12" height="11" rx="1.5"/><circle cx="6" cy="6.5" r="1.2"/><path d="m2.5 12 3.5-3.5 2.5 2.5 2-2 3 3"/>',
    heart: '<path d="M8 13.5 2.6 8.3a3.3 3.3 0 0 1 4.7-4.6L8 4.4l.7-.7a3.3 3.3 0 0 1 4.7 4.6Z"/>',
    tag: '<path d="M2.5 2.5h5l6 6-5 5-6-6Z"/><circle cx="5.5" cy="5.5" r="1"/>',
    trash: '<path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.1a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.6-8.1M7 7v4M9 7v4"/>',
    search: '<circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/>',
    filter: '<path d="M2.5 4h11M4.5 8h7M6.5 12h3"/>',
    sort: '<path d="M5 2.5v11m0 0-2.5-2.5M5 13.5 7.5 11M11 13.5v-11m0 0L8.5 5M11 2.5 13.5 5"/>',
    dense: '<rect x="2" y="2" width="3.4" height="3.4" rx=".8"/><rect x="6.3" y="2" width="3.4" height="3.4" rx=".8"/><rect x="10.6" y="2" width="3.4" height="3.4" rx=".8"/><rect x="2" y="6.3" width="3.4" height="3.4" rx=".8"/><rect x="6.3" y="6.3" width="3.4" height="3.4" rx=".8"/><rect x="10.6" y="6.3" width="3.4" height="3.4" rx=".8"/><rect x="2" y="10.6" width="3.4" height="3.4" rx=".8"/><rect x="6.3" y="10.6" width="3.4" height="3.4" rx=".8"/><rect x="10.6" y="10.6" width="3.4" height="3.4" rx=".8"/>',
    plus: '<path d="M8 3v10M3 8h10"/>',
    close: '<path d="m4 4 8 8M12 4l-8 8"/>',
    folder: '<path d="M2 4.5a1 1 0 0 1 1-1h3l1.5 1.5H13a1 1 0 0 1 1 1V12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1Z"/>',
    expand: '<path d="M9.5 2.5h4v4M13.5 2.5 9 7M6.5 13.5h-4v-4M2.5 13.5 7 9"/>',
    left: '<path d="m10 3-5 5 5 5"/>', right: '<path d="m6 3 5 5-5 5"/>',
    restore: '<path d="M3 8a5 5 0 1 0 1.5-3.5M3 2.5v2.5h2.5"/>',
    spark: '<path d="M8 2v3M8 11v3M2 8h3M11 8h3M4 4l2 2M10 10l2 2M12 4l-2 2M6 10l-2 2"/>',
    check: '<path d="m3.5 8.5 3 3 6-7"/>',
    info: '<circle cx="8" cy="8" r="5.75"/><path d="M8 7.25v3.75M8 5h.01"/>',
    download: '<path d="M8 2.5v7.5m0 0 3-3m-3 3-3-3M3 11.5v1a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-1"/>',
    select: '<rect x="2.5" y="2.5" width="11" height="11" rx="2.5"/><path d="m5.5 8 2 2 3-4"/>',
    copy: '<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5v-1a1 1 0 0 0-1-1h-5a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1h1"/>',
    compare: '<rect x="1.5" y="3" width="13" height="10" rx="1.5"/><path d="M8 2v12"/>', swap: '<path d="M3 5.5h9.5m0 0L10 3m2.5 2.5L10 8M13 10.5H3.5m0 0L6 8m-2.5 2.5L6 13"/>',
    play: '<path d="M5 3.5v9l7.5-4.5Z" fill="currentColor"/>', pause: '<path d="M5.5 3.5v9M10.5 3.5v9" stroke-width="2"/>',
    sound: '<path d="M2.5 6h2.5l3-2.5v9L5 10H2.5ZM10.5 5.5a3.5 3.5 0 0 1 0 5M12 4a5.5 5.5 0 0 1 0 8"/>', muted: '<path d="M2.5 6h2.5l3-2.5v9L5 10H2.5ZM10.5 6.5l3 3M13.5 6.5l-3 3"/>',
};
const line = name => {
    const span = el('span', null, 'fv-icon fv-lib-icon'); span.setAttribute('aria-hidden', 'true');
    span.innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${LINE[name]}</svg>`;
    return span;
};
let opened;
// Leaving the room keeps it (closed, with its state, its stage and its scroll); opening again shows it as it was.
let parked = null;
const CACHE_KEY = 'freevideo.library.cache';
// A picture's thumbnail: made once here from the picture (about 320 px wide, WebP) and kept in Cache Storage under its
// file name (the engine never reuses a name), with the picture's own size; later openings and reloads use it at once.
// The key is the file's path only (never its prompt).
const THUMBS = 'freevideo-library-thumbs-v1';
const thumbsMade = new Map();
const thumbKey = file => new URL(`/freevideo-library-thumb/${encodeURIComponent(file)}`, location.origin).href;
// Deleted is gone: when a picture is really deleted (from here, the image view or the works list) its small copy goes
// too; one brought back by Undo was never dropped, and one missing is simply made again.
function dropThumbs(files) {
    files = (files || []).filter(file => typeof file === 'string' && file);
    if (!files.length) return;
    for (const file of files) { const made = thumbsMade.get(file); thumbsMade.delete(file); made?.then(({url}) => { if (url.startsWith('blob:')) URL.revokeObjectURL(url); }).catch(() => {}); }
    if (globalThis.caches) caches.open(THUMBS).then(store => Promise.all(files.map(file => store.delete(thumbKey(file))))).catch(() => {});
}
// What the engine no longer lists (deleted while this page was closed) leaves the store too.
async function pruneThumbs(files) {
    if (!globalThis.caches) return;
    try {
        const store = await caches.open(THUMBS), keep = new Set(files.map(thumbKey));
        for (const request of await store.keys()) if (!keep.has(request.url)) await store.delete(request);
    } catch { /* The store is optional. */ }
}
// A picture deleted for good leaves nothing of it in this browser: the prompt the image view kept for it and its thumbnail.
function forgetPictures(files) {
    files = (files || []).filter(file => typeof file === 'string' && /^freevideo-image\//.test(file));
    if (!files.length) return;
    try { createMetaStore().forget(files); } catch { /* Nothing kept. */ }
    dropThumbs(files);
}
if (typeof window !== 'undefined') {
    window.addEventListener('freevideo-works-removed', event => { if (event.detail?.undoable === false) forgetPictures(event.detail.paths || event.detail.ids); });
    window.addEventListener('freevideo-pictures-deleted', event => forgetPictures(event.detail?.files));
}
function pictureThumb(file, source, fallback = source) {
    if (thumbsMade.has(file)) return thumbsMade.get(file);
    const made = (async () => {
        const key = thumbKey(file);
        const store = globalThis.caches ? await caches.open(THUMBS).catch(() => null) : null;
        const hit = store && await store.match(key).catch(() => null);
        if (hit) return {url: URL.createObjectURL(await hit.blob()), width: Number(hit.headers.get('x-width')) || 0, height: Number(hit.headers.get('x-height')) || 0};
        const response = await fetch(source);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const blob = await response.blob();
        // The engine's small copy (work_media.js) names the picture's own size; it is kept as it came.
        const named = [response.headers.get('x-picture-width'), response.headers.get('x-picture-height')].map(Number);
        const bitmap = named.every(value => value > 0) ? null : await createImageBitmap(blob);
        const width = bitmap ? bitmap.width : named[0], height = bitmap ? bitmap.height : named[1], scale = bitmap ? Math.min(1, 320 / width) : 1;
        let small = blob;
        if (bitmap && typeof OffscreenCanvas === 'function' && scale < 1) {
            const canvas = new OffscreenCanvas(Math.round(width * scale), Math.round(height * scale));
            canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
            small = await canvas.convertToBlob({type: 'image/webp', quality: .82});
        }
        bitmap?.close?.();
        if (store) store.put(key, new Response(small, {headers: {'content-type': small.type || 'image/webp', 'x-width': String(width), 'x-height': String(height)}})).catch(() => {});
        return {url: URL.createObjectURL(small), width, height};
    })().catch(() => ({url: fallback, width: 0, height: 0}));
    thumbsMade.set(file, made);
    return made;
}
const readCache = () => { try { const value = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null'); return value && Array.isArray(value.videos) ? value : null; } catch { return null; } };

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
// Tells the notices and the task window which creations left the library (or came back): ids are the creations' ids
// (a picture's is its file), paths the files; undoable is true while Undo can still bring them back.
// The bar's Creations entry shows the room as current while it is open (topbar.js).
const announceOpen = open => window.dispatchEvent(new CustomEvent('freevideo-library', {detail: {open}}));
const worksEvent = (name, ids, paths, undoable) => { if (ids.length) window.dispatchEvent(new CustomEvent(name, {detail: {ids, paths, undoable}})); };
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
// Shows the saved videos (or one video's folder) in this computer's file manager; the page says where they are when that fails.
async function openVideoFolder(id) {
    try {
        const response = await api.fetchApi('/freevideo/library/open-folder', {method: 'POST', headers: {...jsonHeaders, 'X-FreeVideo-Library': '1'}, body: JSON.stringify(id ? {id} : {}), signal: AbortSignal.timeout(5000)});
        if (response.status === 404) return {missing: true};
        if (!response.ok) return null;
        return await response.json();
    } catch { return null; }
}
const videoFolderMessage = (t, answer, single = false) => !answer
    ? t('Could not open the folder. Open it on the computer that runs FreeVideo.', '没能打开文件夹。请在运行 FreeVideo 的电脑上打开。')
    : answer.missing ? t('The video file cannot be found. If you moved the file, refresh the page.', '找不到这条视频的文件。如果移动过文件，请刷新页面。')
    : answer.opened === false && answer.folder
        ? t(`Could not open the folder. ${single ? 'This video is' : 'The videos are'} in ${answer.folder}`, `没能打开文件夹。视频在 ${answer.folder}`) : '';
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
// What each undoable batch held, so "Recently deleted" can show the creations and not only a count. This page only.
const batchContents = new Map();

// Pictures: the image engine deletes for good, so the page keeps a deleted picture for the undo time and only then
// asks the engine (library_meta.js). The image client is made on first use; its module is the image view's.
let imageClient = null;
const pictures = () => imageClient ||= import('./image_generation.js').then(module => module.createImageGenClient());
const pictureBin = createPictureBin({seconds: 10,
    // An engine that keeps deleted pictures itself (history_trash) takes them at once; undo asks them back.
    async send(files) {
        const client = await pictures();
        if (!(await client.offers?.('history_trash'))) return null;
        const answer = await client.historyTrash(files.map(file => file?.file || file));
        return {token: answer.batch, failed: (answer.failed || []).filter(row => row.reason !== 'missing').map(row => row.file)};
    },
    async undo(token) { const client = await pictures(); await client.historyUndo(token); },
    async commit(files, sent) {
    let failed;
    if (sent) failed = (sent.failed || []).filter(file => files.includes(file));
    else {
        const client = await pictures();
        const answer = await client.historyDeleteMany(files);
        failed = (answer.failed || []).filter(row => row.reason !== 'missing').map(row => row.file);
    }
    const gone = files.filter(file => !failed.includes(file));
    if (gone.length) {
        forgetPictures(gone);
        worksEvent('freevideo-works-removed', gone, gone, false);
        import('./image_library.js').then(module => module.removeSavedImages(api, gone)).catch(() => {});
        window.dispatchEvent(new CustomEvent('freevideo-pictures-deleted', {detail: {files: gone}}));
    }
    return failed;
}});
// Leaving the page ends the undo time of a held picture: it is deleted as the user asked.
addEventListener('pagehide', () => { pictureBin.flush(); });

export async function libraryPage(t, {limit = 24, before = null, favorite = null, signal} = {}) {
    const query = new URLSearchParams({limit: String(limit)});
    if (before) query.set('before', before);
    if (favorite !== null) query.set('favorite', String(favorite));
    const response = await api.fetchApi('/freevideo/library?' + query, {signal});
    if (response.status === 404) throw Object.assign(new Error(t('Restart FreeVideo after updating to open the creations.', '更新后重启 FreeVideo，可打开作品。')), {restart: true});
    if (!response.ok) throw new Error(t('The creations could not be loaded.', '作品暂时无法读取。'));
    return response.json();
}

export function videoTimestamp(row, t) {
    const value = new Date(row.created_at);
    if (Number.isNaN(value.getTime())) return row.kind === 'image' ? t('Saved picture', '已保存的图片') : t('Saved video', '已保存的视频');
    if (t('en', 'zh') === 'zh') return `${value.getMonth() + 1}月${value.getDate()}日 ${String(value.getHours()).padStart(2, '0')}:${String(value.getMinutes()).padStart(2, '0')}`;
    return value.toLocaleString('en-US', {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true});
}

export function videoDimensions(row, t = null) {
    const g = row.geometry || {}, parts = [];
    // A preview's file is its first pass; geometry names the finished size.
    const shown = row.preview && row.preview_geometry?.width ? row.preview_geometry : g;
    if (shown.width && shown.height) parts.push(`${shown.width} × ${shown.height}`);
    const duration = g.seconds || (g.frames && g.frames / (g.fps || 24));
    if (duration) parts.push(t ? t(`${Number(duration).toFixed(1)} s`, `${Number(duration).toFixed(1)} 秒`) : `${Number(duration).toFixed(1)} s`);
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
async function workflowBackfill(collection, t, before = null) {
    const status = async () => { try { const r = await api.fetchApi('/freevideo/workflow-backfill'); return r.ok ? r.json() : null; } catch { return null; } };
    const post = action => api.fetchApi('/freevideo/workflow-backfill', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action})});
    let value = await status();
    if (!value || value.disabled || value.dismissed || (!value.count && value.state !== 'running')) return;
    const box = el('div', null, 'fv-backfill'); box.setAttribute('role', 'status');
    const heading = el('strong'), text = el('p'), actions = el('div', null, 'fv-backfill-actions');
    box.append(heading, text, actions);
    if (before?.parentNode === collection) collection.insertBefore(box, before); else collection.prepend(box);
    const gb = bytes => (bytes / 2 ** 30).toFixed(bytes < 2 ** 30 ? 2 : 1);
    async function follow() {
        heading.textContent = t('Adding the settings…', '正在补写生成设置…');
        text.textContent = t('Each original video is kept as a backup.', '每个原视频都会另存一份备份。');
        actions.replaceChildren();
        while ((value = await status()) && value.state === 'running') await new Promise(resolve => setTimeout(resolve, 1500));
        const result = value?.result || {};
        heading.textContent = result.embedded ? t(`Settings added to ${result.embedded} videos`, `已为 ${result.embedded} 个视频补上生成设置`) : t('No video was changed', '没有视频被改动');
        text.textContent = result.embedded
            ? t('Drop them back onto FreeVideo to bring back the settings they were made with. Each original is kept as a backup.', '把它们拖回 FreeVideo 即可还原生成时的设置。每个原视频都另存了一份备份。')
            : t('Videos that could not be checked safely were left as they were.', '无法安全确认的视频都保持原样。');
        if (result.failed) text.textContent += ' ' + t(`${result.failed} videos were left unchanged and will be offered again.`, `另有 ${result.failed} 个视频未改动，之后会再次提示。`);
        const full = Math.min(Number.isSafeInteger(result.no_space) ? result.no_space : 0, result.failed || 0);
        if (full > 0) text.textContent += ' ' + t(full === result.failed ? 'The disk is full. Free some space first.' : `${full} of them could not be changed because the disk is full. Free some space first.`,
            full === result.failed ? '磁盘空间不够，请先空出空间。' : `其中 ${full} 个因磁盘空间不够，请先空出空间。`);
        actions.replaceChildren(button(t('Got it', '知道了'), () => box.remove(), 'fv-quiet'));
    }
    if (value.state === 'running') { follow(); return; }
    heading.textContent = t('Add settings to earlier videos', '为之前的视频补上生成设置');
    text.textContent = t(`${value.count} earlier videos can get the settings they were made with, so dropping them back onto FreeVideo brings those settings back. Nothing changes before confirming, and each original is kept as a backup (about ${gb(value.bytes)} GB).`,
        `有 ${value.count} 个之前生成的视频可以补上生成时的设置，之后拖回 FreeVideo 即可还原。确认前不会改动任何文件，每个原视频都会另存一份备份（约 ${gb(value.bytes)} GB）。`);
    const run = button(t('Add settings', '补上设置'), async () => {
        run.disabled = later.disabled = true;
        try { if (!(await post('run')).ok) throw new Error(); follow(); }
        catch { run.disabled = later.disabled = false; text.textContent = t('Could not start. Try again.', '没能开始，请重试。'); }
    }, 'fv-primary');
    tip(run, t('Add settings', '补上设置'), t('Write each video\'s settings into it, keeping a backup', '给每个视频写入生成设置，并保留备份'));
    const later = button(t('Don\'t ask again', '不再提示'), async () => { box.remove(); try { await post('dismiss'); } catch {} }, 'fv-quiet');
    tip(later, t('Don\'t ask again', '不再提示'), t('Hide this offer for good', '以后不再显示这条提示'));
    actions.append(run, later);
}

// The room's own player: one play button, a scrubber, the time and the sound, in the same glass as the room.
function playerControls(video, t) {
    video.controls = false;
    const time = seconds => Number.isFinite(seconds) ? `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}` : '0:00';
    const big = button('', () => toggle(), 'fv-library-player-big'); big.append(line('play'));
    const bar = el('div', null, 'fv-library-player');
    const play = button('', () => toggle(), 'fv-library-player-button');
    const seek = el('input'); seek.type = 'range'; seek.min = '0'; seek.max = '1000'; seek.step = '1'; seek.value = '0'; seek.className = 'fv-library-player-seek';
    seek.setAttribute('aria-label', t('Position', '播放位置'));
    tip(seek, t('Position', '播放位置'), t('Drag to move through the video', '拖动跳到视频的任意位置'), '← →');
    const clock = el('span', '0:00 / 0:00', 'fv-library-player-time');
    const sound = button('', () => { video.muted = !video.muted; }, 'fv-library-player-button');
    bar.append(play, seek, clock, sound);
    const toggle = () => { if (video.paused || video.ended) video.play().catch(() => {}); else video.pause(); };
    const sync = () => {
        const paused = video.paused || video.ended;
        play.replaceChildren(line(paused ? 'play' : 'pause'));
        const name = paused ? t('Play', '播放') : t('Pause', '暂停');
        play.setAttribute('aria-label', name); tip(play, name, paused ? t('Play the video', '播放视频') : t('Pause the video', '暂停视频'), 'Space');
        big.hidden = !paused; big.setAttribute('aria-label', t('Play', '播放')); tip(big, t('Play', '播放'), t('Play the video', '播放视频'), 'Space');
        sound.replaceChildren(line(video.muted ? 'muted' : 'sound'));
        const soundName = video.muted ? t('Sound off', '已静音') : t('Sound on', '有声音');
        sound.setAttribute('aria-label', soundName); tip(sound, soundName, video.muted ? t('Turn the sound on', '打开声音') : t('Mute the sound', '关闭声音'), 'M');
        bar.dataset.paused = String(paused);
    };
    const progress = () => {
        const duration = video.duration;
        clock.textContent = `${time(video.currentTime)} / ${time(duration)}`;
        if (document.activeElement !== seek) seek.value = String(Number.isFinite(duration) && duration > 0 ? Math.round(video.currentTime / duration * 1000) : 0);
    };
    seek.oninput = () => { if (Number.isFinite(video.duration)) video.currentTime = video.duration * Number(seek.value) / 1000; progress(); };
    for (const type of ['play', 'pause', 'ended', 'volumechange']) video.addEventListener(type, sync);
    for (const type of ['timeupdate', 'loadedmetadata', 'durationchange', 'seeked']) video.addEventListener(type, progress);
    video.addEventListener('click', toggle);
    sync(); progress();
    return [big, bar];
}

// The view the room opens in: the open view dialog with the shared top bar, else none.
function libraryHost() {
    const views = [...document.querySelectorAll('dialog.fv-studio[open]')].filter(d => d.querySelector(':scope > .fv-header .fv-view-switch'));
    const front = globalThis.FreeVideoShell?.viewDialog?.() || views.at(-1);
    return front && views.includes(front) ? front : null;
}

// ``upscale`` queues the second pass of a first-pass preview (the studio's node). A room opened from anywhere else
// (a notification, the palette, search, a chat or autopilot job) carries none, so the studio registers its own as the default.
let defaultUpscale = null;
export function setDefaultUpscale(run) { defaultUpscale = typeof run === 'function' ? run : null; }
// ``upscaleWork`` makes a larger copy of a finished video; registered by the studio like the second pass.
let defaultUpscaleWork = null;
export function setDefaultUpscaleWork(run) { defaultUpscaleWork = typeof run === 'function' ? run : null; }
// The longest single request at a size, as the planner admits it now; null when it cannot be told.
async function admittedFrames(width, height) {
    for (let tries = 0; tries < 5; tries++) {
        const response = await api.fetchApi('/freevideo/capabilities?' + new URLSearchParams({width, height}));
        if (!response.ok) return null;
        const reply = await response.json();
        if (reply?.status === 'computing') { await new Promise(done => setTimeout(done, 1000)); continue; }
        return reply && (!reply.status || reply.status === 'ready') ? capabilityPoints(reply, {width, height}).maxRequestFrames : null;
    }
    return null;
}
// ``reuse`` is the creation panel's {now, apply}: given, Reuse settings fills that panel from a creation.
export function openLibrary(t, options = {}) {
    if (parked?.dialog.isConnected !== false && parked && !parked.dialog.open) return parked.reopen(options);
    if (parked?.dialog.open) { parked.reopen(options); return; }
    let {upscale = null, selectedWork = null, extend = null, reuse = null, back: backLabel = null} = options;
    const secondPassRunner = () => upscale || defaultUpscale;
    const upscaleOffer = createUpscaleOffer(admittedFrames);
    const dialog = el('dialog', null, 'fv-studio fv-library'); opened = dialog;
    let host = libraryHost();
    const hostClosed = () => { if (dialog.open) dialog.close(); };
    // Closing gives the keyboard back to the control that opened the room.
    const opener = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null;
    // `kind` (text, or a function read when the button is clicked) tells the feedback what the sentence alone cannot.
    // `error`, when given, is what the report sends instead of the plain sentence shown.
    const fault = (target, text, where = ['Creations', '作品库'], kind = null, error = null) => {
        target.textContent = text;
        target.append(issueButton(t, () => ({where, message: error?.message || text, kind: typeof kind === 'function' ? kind() : kind})));
    };
    dialog.setAttribute('aria-label', t('Creations', '作品库'));
    const header = el('header', null, 'fv-library-header');
    // Closing returns to the view this was opened from; that view names itself on the button.
    const backText = backLabel || t('Back to create', '返回创作');
    const back = button('', () => closeDialog(dialog), 'fv-quiet fv-library-back');
    back.setAttribute('aria-label', backText); back.title = backText;
    back.append(icon('back'), el('span', backText, 'fv-library-back-label'));
    const title = el('h2', t('Creations', '作品库'));
    const refresh = button(t('Refresh', '刷新'), () => load(false), 'fv-quiet');
    // The recovery sits before the report button, in the same bordered style.
    const retryRead = target => {
        const retry = button(t('Retry', '重试'), () => load(false), 'fv-library-message-retry');
        const report = target.querySelector(':scope > .fv-issue-button');
        if (report) report.before(retry); else target.append(retry);
    };
    const choose = button(t('Select', '选择'), () => setSelecting(!selecting), 'fv-quiet fv-library-choose');
    // Stage: the creation large in the middle, thumbnails on the right to switch it. Grid: the shelf for sorting and selecting.
    const LAYOUT_KEY = 'freevideo.library.layout';
    let preferred = (() => { try { return localStorage.getItem(LAYOUT_KEY) === 'grid' ? 'grid' : 'stage'; } catch { return 'stage'; } })(), layout = 'grid';
    const layoutToggle = button('', () => { preferred = layout === 'stage' ? 'grid' : 'stage'; try { localStorage.setItem(LAYOUT_KEY, preferred); } catch { /* Not kept. */ } setLayout(preferred); }, 'fv-quiet fv-library-layout');
    const tools = el('div', null, 'fv-library-tools'); tools.append(layoutToggle, choose, refresh);
    // The shelf follows changes by itself, selecting starts from the thumbnails (check box, Ctrl/⌘, Shift, ⌘A) and the
    // layout switch is the grid icon in the tool row, so the header keeps only Back and the tabs.
    layoutToggle.hidden = choose.hidden = refresh.hidden = true; tools.hidden = true;
    header.append(back, title, tools);
    // Try the full label on each resize, so wider windows restore it too.
    const headerObserver = new ResizeObserver(() => {
        header.dataset.compact = 'false';
        header.dataset.compact = String(title.scrollWidth > title.clientWidth || header.scrollWidth > header.clientWidth);
    });
    headerObserver.observe(header); headerObserver.observe(tools);
    const body = el('div', null, 'fv-library-body');
    // The room has two shelves: creations, and the characters, scenes and props (cast_room.js).
    title.className = 'fv-library-tabs'; title.setAttribute('role', 'tablist'); title.setAttribute('aria-label', t('Library', '资料库'));
    const tabWorks = button(t('Creations', '作品'), () => showTab('works'), 'fv-library-tab');
    const tabCast = button(t('Cast', '角色'), () => showTab('cast'), 'fv-library-tab');
    for (const tab of [tabWorks, tabCast]) tab.setAttribute('role', 'tab');
    tip(tabWorks, t('Creations', '作品'), t('Generated videos and pictures', '生成的视频和图片'));
    tip(tabCast, t('Cast', '角色'), t('Characters, scenes and props to reuse', '可反复使用的角色、场景和道具'));
    title.replaceChildren(tabWorks, tabCast);
    let tab = 'works', castRoom = null;
    async function showTab(next) {
        tab = next;
        tabWorks.setAttribute('aria-selected', String(next === 'works')); tabCast.setAttribute('aria-selected', String(next === 'cast'));
        tabWorks.tabIndex = next === 'works' ? 0 : -1; tabCast.tabIndex = next === 'cast' ? 0 : -1;
        dialog.dataset.tab = next;
        if (next === 'works') { closeMenus(); body.hidden = false; if (castRoom) castRoom.element.hidden = true; choose.hidden = false; return; }
        setSelecting(false); choose.hidden = true; player?.pause();
        if (!castRoom) {
            try {
                const {createCastRoom} = await import('./cast_room.js');
                if (disposed || tab !== 'cast') return;
                const room = createCastRoom({api, t, viewURL: view});
                // On the stage the viewer sits in the page body, not in the dialog.
                if (viewer.parentNode === dialog) dialog.insertBefore(room.element, viewer); else dialog.append(room.element);
                castRoom = room;
            } catch (error) { castRoom = null; console.warn('FreeVideo cast room:', error); showTab('works'); return; }
        }
        if (tab !== 'cast') return;
        body.hidden = true; castRoom.element.hidden = false; castRoom.refresh();
    }
    title.onkeydown = event => {
        if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        event.preventDefault(); const target = tab === 'works' ? 'cast' : 'works'; showTab(target); (target === 'works' ? tabWorks : tabCast).focus();
    };
    tip(back, backText, t('Close the library and go back', '关闭作品库，回到之前的视图'), 'Esc');
    tip(refresh, t('Refresh', '刷新'), t('Read the creations again', '重新读取作品'));
    refresh.onclick = () => { if (tab === 'cast') castRoom?.refresh(); else load(false); };
    // The tip follows the button's label (Select / Cancel), set in syncSelection.
    const meta = createMeta();
    const remembered = readView();
    // The shelf's filter: category from the side bar, the rest from the tool row. Opened to show one creation from
    // elsewhere, every creation is listed this time so it is found; the saved choices stay for the next opening.
    const filter = {kind: remembered.kind, favorites: false, tag: null, query: '', period: 'any', preview: false, agent: false, source: ''};
    // Every opening starts newest first; the sort button still changes it for this opening.
    let sort = 'new', dense = remembered.dense, trash = false;
    if (!selectedWork) try { filter.favorites = localStorage.getItem(FILTER_KEY) === 'favorites'; } catch { /* Show all. */ }
    else { filter.kind = 'all'; sort = 'new'; }

    // --- The side bar: categories, tags, recently deleted ---
    const side = el('nav', null, 'fv-library-side');
    side.setAttribute('aria-label', t('Library', '资料库'));
    const sideGroup = (label, cls = '') => { const group = el('div', null, 'fv-library-side-group ' + cls); if (label) group.append(el('div', label, 'fv-library-side-label')); side.append(group); return group; };
    const places = sideGroup(null, 'fv-library-places');
    const placeButton = (key, name, glyph, use) => {
        const item = button('', () => goPlace(key), 'fv-library-place'); item.dataset.place = key;
        item.append(line(glyph), el('span', name, 'fv-library-place-name'), el('span', '', 'fv-library-count'));
        item.setAttribute('aria-label', name); tip(item, name, use);
        places.append(item); return item;
    };
    const placeAll = placeButton('all', t('All', '全部'), 'grid', t('Every video and picture', '所有视频和图片'));
    const placeVideo = placeButton('video', t('Videos', '视频'), 'video', t('Only the generated videos', '只看生成的视频'));
    const placeImage = placeButton('image', t('Pictures', '图片'), 'picture', t('Only the generated pictures', '只看生成的图片'));
    const placeFavorites = placeButton('favorites', t('Favorites', '收藏'), 'heart', t('Creations marked with the heart', '点过爱心的作品'));
    const tagGroup = sideGroup(t('Tags', '标签'), 'fv-library-tags');
    const tagList = el('div', null, 'fv-library-tag-list');
    const newTag = button('', () => startNewTag(), 'fv-library-place fv-library-new-tag');
    newTag.append(line('plus'), el('span', t('New tag', '新建标签'), 'fv-library-place-name'));
    tip(newTag, t('New tag', '新建标签'), t('Make a tag to group creations', '新建一个标签来归类作品'));
    tagGroup.append(tagList, newTag);
    const bottom = sideGroup(null, 'fv-library-side-bottom');
    const placeTrash = button('', () => goPlace('trash'), 'fv-library-place fv-library-place-trash'); placeTrash.dataset.place = 'trash';
    placeTrash.append(line('trash'), el('span', t('Recently deleted', '最近删除'), 'fv-library-place-name'), el('span', '', 'fv-library-count'));
    tip(placeTrash, t('Recently deleted', '最近删除'), t('Bring back what was just deleted', '恢复刚删除的作品'));
    const openFolder = button('', async () => {
        try { const answer = await (await pictures()).historyOpen(); if (answer && answer.opened === false && answer.folder) say(t(`The pictures are in ${answer.folder}`, `图片在 ${answer.folder}`)); }
        catch { say(t('Could not open the folder.', '没能打开文件夹。'), true); }
    }, 'fv-library-place fv-library-open-folder');
    openFolder.append(line('folder'), el('span', t('Picture folder', '图片文件夹'), 'fv-library-place-name'));
    tip(openFolder, t('Picture folder', '图片文件夹'), t('Show the pictures in the file manager', '在文件管理器里打开图片所在文件夹'));
    const openVideoFolderButton = button('', async () => {
        const answer = await openVideoFolder(); const text = videoFolderMessage(t, answer);
        if (text) say(text, !answer || answer.missing, answer?.folder ? 20000 : 4000);  // a path stays long enough to be copied
    }, 'fv-library-place fv-library-open-folder fv-library-open-video-folder');
    openVideoFolderButton.append(line('folder'), el('span', t('Video folder', '视频文件夹'), 'fv-library-place-name'));
    tip(openVideoFolderButton, t('Video folder', '视频文件夹'), t('Show the videos in the file manager', '在文件管理器里打开视频所在文件夹'));
    bottom.append(placeTrash, openVideoFolderButton, openFolder);

    // --- The shelf: tool row, day groups, the dock for undo and the selection ---
    const collection = el('section', null, 'fv-library-collection'); collection.setAttribute('aria-label', t('Saved creations', '已保存的作品'));
    const toolbar = el('div', null, 'fv-library-toolbar');
    const searchBox = el('label', null, 'fv-library-search');
    const search = el('input'); search.type = 'search'; search.placeholder = t('Search prompts and names', '搜索提示词和名称');
    search.setAttribute('aria-label', t('Search creations', '搜索作品'));
    tip(search, t('Search', '搜索'), t('Find creations by prompt or name', '按提示词或名称查找作品'), '/');
    searchBox.append(line('search'), search);
    const filterButton = button('', () => toggleMenu(filterButton, filterMenu), 'fv-library-tool fv-library-filter-button');
    filterButton.append(line('filter'), el('span', t('Filter', '筛选'), 'fv-library-tool-label'), el('span', '', 'fv-library-badge'));
    filterButton.setAttribute('aria-haspopup', 'menu'); filterButton.setAttribute('aria-expanded', 'false');
    tip(filterButton, t('Filter', '筛选'), t('Show only creations that match', '只显示符合条件的作品'));
    const sortButton = button('', () => toggleMenu(sortButton, sortMenu), 'fv-library-tool fv-library-sort-button');
    sortButton.append(line('sort'), el('span', '', 'fv-library-tool-label'));
    sortButton.setAttribute('aria-haspopup', 'menu'); sortButton.setAttribute('aria-expanded', 'false');
    tip(sortButton, t('Sort', '排序'), t('Change the order of the shelf', '改变作品的排列顺序'));
    const denseButton = button('', () => layoutToggle.click(), 'fv-library-tool fv-library-dense-button');
    denseButton.append(line('dense'));
    toolbar.append(searchBox, filterButton, sortButton, denseButton);
    const filterMenu = el('div', null, 'fv-library-menu'); filterMenu.setAttribute('role', 'menu'); filterMenu.hidden = true;
    const sortMenu = el('div', null, 'fv-library-menu'); sortMenu.setAttribute('role', 'menu'); sortMenu.hidden = true;
    const tagMenu = el('div', null, 'fv-library-menu fv-library-tag-menu'); tagMenu.setAttribute('role', 'menu'); tagMenu.hidden = true;
    const grid = el('div', null, 'fv-library-grid');
    const message = el('p', '', 'fv-library-message'); message.setAttribute('role', 'status');
    // Creations whose saved record could not be read stay on disk; say how many the loaded pages skipped.
    const unreadable = el('p', '', 'fv-library-skipped fv-library-unreadable'); let unread = 0;
    // Selecting several creations: a strip above the cards, the action bar below them.
    const picking = el('div', null, 'fv-library-picking'); picking.hidden = true;
    const pickAll = button(t('Select all', '全选'), () => selectAll(), 'fv-quiet');
    tip(pickAll, t('Select all', '全选'), t('Select every creation shown', '选中当前显示的全部作品'), '⌘A');
    const pickNone = button(t('Select none', '全不选'), () => { picked.clear(); syncSelection(); }, 'fv-quiet');
    tip(pickNone, t('Select none', '全不选'), t('Clear the selection', '清空已选'));
    const pickCount = el('strong', '', 'fv-library-pick-count');
    picking.append(pickCount, pickAll, pickNone);
    const undoBar = el('p', null, 'fv-library-undo'); undoBar.setAttribute('role', 'status'); undoBar.hidden = true;
    const bar = el('div', null, 'fv-library-selection'); bar.hidden = true;
    const barLabel = el('strong'), barText = el('small'), barActions = el('div', null, 'fv-library-selection-actions');
    const barCancel = button(t('Cancel', '取消'), () => { confirming = false; syncSelection(); }, 'fv-quiet');
    tip(barCancel, t('Cancel', '取消'), t('Keep these creations', '不删除，保留这些作品'), 'Esc');
    const barFavorite = button('', () => favoritePicked(), 'fv-quiet fv-library-bar-favorite');
    barFavorite.append(line('heart'), el('span', t('Favorite', '收藏')));
    tip(barFavorite, t('Favorite', '收藏'), t('Add the selected creations to Favorites', '把选中的作品加入收藏'));
    const barTag = button('', () => openTagMenu(barTag, [...picked]), 'fv-quiet fv-library-bar-tag');
    barTag.append(line('tag'), el('span', t('Tag', '标签')));
    tip(barTag, t('Tag', '标签'), t('Add or remove tags on the selection', '给选中的作品加上或去掉标签'));
    const barDelete = button('', () => confirmDelete(), 'fv-library-danger');
    tip(barDelete, t('Delete', '删除'), t('Delete the selection; undo for 10 seconds', '删除选中的作品，10 秒内可撤销'), 'Delete');
    const barCompare = button('', () => { const keys = [...picked]; if (keys.length === 2) openCompare(keys); }, 'fv-quiet fv-library-bar-compare');
    barCompare.append(line('compare'), el('span', t('Compare', '对比')));
    tip(barCompare, t('Compare', '对比'), t('See two creations side by side or with a slider', '并排或用滑块对比两个作品'));
    barActions.append(barFavorite, barTag, barCompare, barCancel, barDelete);
    bar.append(barLabel, barText, barActions);
    // The undo note and the selection bar share one sticky dock so they stack instead of covering each other.
    const dock = el('div', null, 'fv-library-dock'); dock.append(undoBar, bar);
    const trashView = el('div', null, 'fv-library-trash'); trashView.hidden = true;
    toolbar.append(picking);
    // The end of the shelf: the next page is read before it is reached (about 1.5 screens ahead); while it comes, cards
    // of the same size hold its place; a failure leaves one small Retry card; at the very end, nothing.
    const tail = el('div', null, 'fv-library-grid fv-library-tail'); tail.setAttribute('aria-hidden', 'true');
    collection.append(toolbar, grid, tail, trashView, message, unreadable, dock, filterMenu, sortMenu, tagMenu);
    workflowBackfill(collection, t, grid);

    // --- The detail: the picture, its tags, its actions, and the run's numbers folded away ---
    const detail = el('section', null, 'fv-library-detail');
    const all = button(t('All creations', '全部作品'), () => {
        dialog.dataset.detail = 'false'; player?.pause(); cards.get(selected)?.focus({preventScroll: true});
    }, 'fv-quiet fv-library-all');
    all.prepend(line('left'));
    tip(all, t('All creations', '全部作品'), t('Back to the shelf', '回到作品列表'), 'Esc');
    const frame = el('div', null, 'fv-library-frame sgf-avoid');
    const caption = el('div', null, 'fv-library-caption');
    const date = el('strong'), geometry = el('span'); caption.append(date, geometry);
    const tagRow = el('div', null, 'fv-library-tag-row');
    const stats = el('div', null, 'fv-stats'); stats.hidden = true;
    stats.setAttribute('aria-label', t('Generation statistics', '生成统计'));
    const budget = el('div', '', 'fv-budget');
    const run = el('details', null, 'fv-library-run'); run.hidden = true;
    const runSummary = el('summary', t('Run details', '运行详情')); run.append(runSummary, stats, budget);
    tip(runSummary, t('Run details', '运行详情'), t('Time and memory this generation used', '这次生成的用时和内存'));
    const links = el('div', null, 'fv-result-links');
    const notice = el('p', '', 'fv-library-message fv-library-notice'); notice.setAttribute('role', 'status');
    const promptBox = el('div', null, 'fv-library-prompt'); promptBox.hidden = true;
    const promptHead = el('div', null, 'fv-library-prompt-head'), promptText = el('p', null, 'fv-library-prompt-text');
    const promptCopy = button('', async () => {
        let ok = false; try { await navigator.clipboard.writeText(promptText.textContent); ok = true; } catch { /* Shown on the button. */ }
        promptCopy.lastElementChild.textContent = ok ? t('Copied', '已复制') : t('Could not copy', '复制失败');
        setTimeout(() => { promptCopy.lastElementChild.textContent = t('Copy', '复制'); }, 1600);
    }, 'fv-library-prompt-copy');
    promptCopy.append(icon('copy'), el('span', t('Copy', '复制')));
    tip(promptCopy, t('Copy prompt', '复制提示词'), t('Copy the words this was made from', '复制生成它所用的提示词'));
    promptHead.append(el('span', t('Prompt', '提示词')), promptCopy); promptBox.append(promptHead, promptText);
    detail.append(all, frame, caption, tagRow, links, notice, promptBox, run);
    const empty = el('div', null, 'fv-library-detail-empty');
    empty.append(line('grid'), el('span', t('Choose a creation to see it here', '选一个作品，在这里查看')));
    detail.append(empty);
    // While selecting, the detail sums up the selection instead of one creation.
    const summary = el('div', null, 'fv-library-selection-summary');
    const summaryTitle = el('strong'), summaryNote = el('p', t('Deleted creations can be brought back for 10 seconds.', '删除的作品 10 秒内可以撤销。')), summaryGrid = el('div', null, 'fv-library-summary-grid');
    summary.append(summaryTitle, summaryNote, summaryGrid); detail.append(summary);
    function drawSummary(size) {
        const count = picked.size;
        summaryTitle.textContent = count ? t(`${count} selected`, `已选 ${count} 个作品`) + (size == null ? '' : ` · ${t('about', '约')} ${gb(size)}`) : '';
        summaryTitle.hidden = !count;
        summaryNote.hidden = !count;
        const keys = [...picked].slice(0, 12), have = [...summaryGrid.children].map(node => node.dataset.key);
        if (keys.join() === have.join()) return;
        summaryGrid.replaceChildren(...keys.map(key => {
            const row = works.get(key), cell = el('div', null, 'fv-library-summary-cell'); cell.dataset.key = key;
            if (!row) return cell;
            const image = el('img'); image.alt = ''; image.src = row.kind === 'image' ? pictureURL(row) : videoThumbnailURL(row); image.onerror = () => { image.hidden = true; };
            cell.append(image); return cell;
        }));
        if (picked.size > 12) summaryGrid.append(el('div', `+${picked.size - 12}`, 'fv-library-summary-cell fv-library-summary-more'));
    }
    // The big viewer over the whole room; the floating task window keeps out of its way (sgf-avoid).
    const viewer = el('div', null, 'fv-library-viewer sgf-avoid'); viewer.hidden = true;
    viewer.setAttribute('role', 'dialog'); viewer.setAttribute('aria-label', t('Viewer', '查看大图'));
    body.append(side, collection, detail); dialog.append(header, body, viewer);
    function syncLayoutButton() {
        const toGrid = layout === 'stage';
        denseButton.replaceChildren(line(toGrid ? 'dense' : 'expand')); denseButton.setAttribute('aria-label', toGrid ? t('Grid', '网格') : t('Large', '大图'));
        tip(denseButton, toGrid ? t('Grid', '网格') : t('Large', '大图'), toGrid ? t('All creations as a grid, to sort and select', '以网格查看全部作品，便于整理和多选') : t('One creation large, thumbnails beside it', '大图查看，旁边是缩略图'));
        denseButton.hidden = drawer.matches || narrow.matches;
        layoutToggle.textContent = layout === 'stage' ? t('Grid', '网格') : t('Stage', '大图');
        tip(layoutToggle, layoutToggle.textContent, layout === 'stage' ? t('All creations as a grid, to sort and select', '以网格查看全部作品，便于整理和多选') : t('One creation large, thumbnails beside it', '大图查看，旁边是缩略图'));
    }
    function cardTip(card, row) {
        if (layout === 'stage') { delete card.dataset.fvTipName; delete card.dataset.fvTipUse; delete card.dataset.fvTipKey; return; }
        // The tip says what the card is called, nothing more.
        tip(card, workName(row));
    }
    function setLayout(value) {
        if (value === 'stage' && (drawer.matches || narrow.matches)) value = 'grid';
        const was = layout; layout = value; dialog.dataset.layout = value; syncLayoutButton(); layoutToggle.hidden = drawer.matches || narrow.matches;
        // The thumbnail column is narrow: a short placeholder there, the whole line on hover.
        search.placeholder = value === 'stage' ? t('Search', '搜索') : t('Search prompts and names', '搜索提示词和名称');
        search.setAttribute('aria-label', t('Search prompts and names', '搜索提示词和名称'));
        tip(search, t('Search', '搜索'), t('Search prompts and names', '搜索提示词和名称'), '/');
        for (const [key, card] of cards) { const row = works.get(key); if (row) cardTip(card, row); }
        if (was === value && value === 'grid') return;
        if (value === 'stage') {
            if (selecting) setSelecting(false);
            body.insertBefore(viewer, collection); viewer.hidden = false; viewer.dataset.idle = 'false';
            // The selection's actions sit above the thumbnails on the stage.
            collection.insertBefore(dock, grid);
            const key = selected && works.has(selected) ? selected : ordered().find(card => !card.hidden)?.dataset.key;
            if (key) { viewing = null; select(key, false); }
        } else {
            exitFull(); stageWatch?.disconnect(); stageWatch = null; for (const media of viewer.querySelectorAll('video')) media.pause();
            dialog.append(viewer); viewer.hidden = true; viewer.replaceChildren(); viewing = null; comparedKey = '';
            unreadable.after(dock);
            notice.after(promptBox); promptBox.after(run); run.open = false;
        }
    }
    let disposed = false, loading = false, next = null, selected = null, player = null, arrivals = [];
    let listAbort = null, loadRound = 0, everything = false, gathering = false;
    let selecting = false, confirming = false, deleting = false, anchor = null, undoTimer = null, sizeTimer = null, sizeRound = 0;
    const picked = new Set(), sizes = new Map();
    // works: every loaded creation by key (video:…, image:…); rows: the videos by file, as the task folding reads them.
    const works = new Map(), rows = new Map(), cards = new Map();
    let pictureRows = [], picturesState = 'loading';
    // Pictures deleted in the image view or the image editor: hidden while their undo lasts, gone when the engine
    // has deleted them, back when undone (freevideo-works-removed / -restored from image_works.js).
    const heldElsewhere = new Set();
    const dayHeadings = new Map();
    const prompts = new Map();
    // What each picture was made with (prompt, time), as the image view keeps it in this browser.
    const pictureFacts = createMetaStore();
    // Reuse settings works wherever the library was opened. From the creation panel it fills that panel in place;
    // otherwise the panel comes to the front and is asked to take the settings (window 'freevideo-command', id
    // 'settings.reuse', which sets detail.handled); a panel that does not answer gets the prompt the way the command
    // palette fills it.
    const reuseElsewhere = {
        now: () => ({text: '', width: 0, height: 0, seconds: null, frames: null, seed: null}),
        apply: saved => { void reuseInPanel(saved); },
        onEdit: () => () => {},
        receipt: () => true,
    };
    const reuseNow = () => reuse || reuseElsewhere;
    async function reuseInPanel(saved) {
        closeDialog(dialog);
        const shell = globalThis.FreeVideoShell;
        if (shell?.view?.() !== 'studio') { try { await shell?.go?.('studio'); } catch { /* Stay where we are. */ } }
        const command = {id: 'settings.reuse', saved, handled: false};
        dispatchEvent(new CustomEvent('freevideo-command', {detail: command}));
        if (!command.handled) await (await import('./global_search.js')).fillPrompt(saved.text, {view: 'studio'});
    }
    // A picture opens in the image view, where it can be a reference or the start of more pictures.
    async function openInImageView(row) {
        closeDialog(dialog);
        try { await globalThis.FreeVideoShell?.go?.('image'); } catch { /* Stay where we are. */ }
        dispatchEvent(new CustomEvent('freevideo-open-work', {detail: {kind: 'image', file: row.file}}));
    }
    async function usePicturePrompt(text) {
        closeDialog(dialog);
        await (await import('./global_search.js')).fillPrompt(text, {view: 'image', fallbackView: 'image'});
    }
    // A picture's words: the engine's record of it (row.prompt) or, while the page that made it is open, the image view's
    // memory; the browser no longer keeps prompts.
    const picturePrompt = row => promptOfRow(row).trim();
    const abort = new AbortController();
    const narrow = matchMedia('(max-width: 680px)');
    const drawer = matchMedia('(max-width: 1180px)');
    const timestamp = row => videoTimestamp(row, t), dimensions = row => videoDimensions(row, t);
    const isVideo = key => key?.startsWith('video:');
    const keyOfVideo = video => { for (const [key, row] of works) if (row.video === video) return key; return null; };
    function persistView() { saveView({kind: filter.kind, sort, dense}); }
    function releasePlayer() {
        if (!player) return;
        player.pause(); player.removeAttribute('src'); player.load(); player = null;
    }
    function clearDetail() {
        releasePlayer(); selected = null; frame.replaceChildren(); links.replaceChildren(); tagRow.replaceChildren(); date.textContent = geometry.textContent = '';
        stats.hidden = true; stats.replaceChildren(); budget.textContent = ''; run.hidden = true; detail.dataset.empty = 'true';
    }
    function mountPlayer(row) {
        releasePlayer(); frame.replaceChildren();
        // The picture's own box, so a preview's badge sits on its corner.
        const media = el('div', null, 'fv-library-media');
        // Where the window has no room for the stage, View large shows the video or picture over the whole screen.
        const big = button('', () => { if (drawer.matches || narrow.matches) frame.querySelector('video, img')?.requestFullscreen?.()?.catch?.(() => {}); else openViewer(selected); }, 'fv-library-enlarge'); big.append(line('expand'));
        big.setAttribute('aria-label', t('View large', '查看大图')); tip(big, t('View large', '查看大图'), t('Open it over the whole window', '铺满窗口查看'), 'Space');
        if (row.kind === 'image') {
            const picture = el('img'); picture.alt = ''; picture.decoding = 'async'; picture.src = pictureURL(row);
            mediaQueue().open(pictureURL(row), picture);
            picture.onload = () => {
                row.size = {width: picture.naturalWidth, height: picture.naturalHeight};
                frame.style.setProperty('--fv-ratio', String(picture.naturalWidth / picture.naturalHeight));
                if (selected === workKey(row)) geometry.textContent = pictureDimensions(row);
            };
            picture.onerror = () => { if (selected === workKey(row)) frame.replaceChildren(pictureProblem()); };
            if (row.size?.width) frame.style.setProperty('--fv-ratio', String(row.size.width / row.size.height)); else frame.style.setProperty('--fv-ratio', '1');
            media.append(picture, big); frame.append(media);
            return;
        }
        player = el('video'); player.preload = 'metadata'; player.playsInline = true;
        player.src = view(row.video); mediaQueue().open(view(row.video), player);
        const g = row.geometry || {};
        if (g.width > 0 && g.height > 0) frame.style.setProperty('--fv-ratio', String(g.width / g.height));
        media.append(player, ...playerControls(player, t), big);
        if (row.preview) media.append(previewBadge(row, t));
        frame.append(media);
        player.addEventListener('error', event => {
            if (!disposed && selected === workKey(row) && event.currentTarget === player) frame.replaceChildren(videoProblem(player));
        }, {once: true});
    }
    // A video that cannot be opened, in the details and on the stage: the sentence and the feedback button.
    function videoProblem(video) {
        const note = el('p', null, 'fv-library-message');
        // The player only says it failed: its error code and what the file's address answers tell a moved or
        // empty file from an unsupported encoding, and travel with the feedback.
        let detail = 'media-error-' + (video.error?.code ?? 'none');
        const source = video.currentSrc || video.src;
        fault(note, videoUnavailableText(t), undefined, () => detail);
        fetch(source, {method: 'HEAD', signal: AbortSignal.timeout(5000)}).then(response => {
            const type = (response.headers.get('content-type') || '').replace(/[^\w./+-]/g, '').slice(0, 40);
            detail += ` http-${response.status} length-${response.headers.get('content-length') ?? 'none'} type-${type || 'none'}`;
        }, () => { detail += ' head-failed'; });
        return note;
    }
    const pictureProblem = () => { const note = el('p', null, 'fv-library-message'); fault(note, t('This picture can no longer be read. It may have been moved or deleted.', '这张图片已无法读取，可能已被移动或删除。')); return note; };
    const pictureDimensions = row => [t('Picture', '图片'), row.size?.width ? `${row.size.width} × ${row.size.height}` : ''].filter(Boolean).join(' · ');
    // The hover tips of the shared action bar, which the studio's module builds.
    function tipActions(root) {
        for (const [selector, name, use] of [
            ['.fv-action-favorite', t('Favorite', '收藏'), t('Keep it in Favorites', '加入或移出收藏')],
            ['a.fv-action', t('Download video', '下载视频'), t('Save the MP4 to this computer', '把视频文件保存到电脑')],
            ['.fv-action-copy', t('Copy path', '复制路径'), t('Copy where the file is saved', '复制文件保存的位置')],
            ['.fv-action-reuse', t('Reuse settings', '复用设置'), t('Fill the creation panel with these settings', '把这次的设置填回创作面板')],
            ['.fv-action-extend', t('Continue', '接着生成'), t('Make a longer video from its end', '从结尾继续生成更长的视频')],
            ['.fv-share-trigger', t('Share', '分享'), t('Make a picture or clip to share', '生成可分享的图片或短片')],
            ['.fv-action-report', t('Download report', '下载报告'), t('Save the diagnostic report of this run', '保存这次生成的诊断报告')],
            ['.fv-action-delete > summary', t('Delete', '删除'), t('Delete it; undo for 10 seconds', '删除这个作品，10 秒内可撤销')],
            ['.fv-action-second-pass', t('Run second pass', '继续二采'), t('Finish the full-resolution video', '从预览继续生成全分辨率视频')],
        ]) for (const node of root.querySelectorAll(selector)) if (!node.dataset.fvTipName) tip(node, name, use);
    }
    function pictureActions(row) {
        const bar = el('div', null, 'fv-actions');
        const heart = button('', async () => {
            const value = !row.favorite;
            row.favorite = value; meta.setPictureFavorite([row.file], value); syncHeart(); syncFavorite(row); render();
        }, 'fv-action fv-action-secondary fv-action-favorite');
        heart.append(icon('heart'), el('span'));
        const syncHeart = () => {
            heart.setAttribute('aria-pressed', String(!!row.favorite));
            heart.lastElementChild.textContent = row.favorite ? t('Remove from favorites', '取消收藏') : t('Add to favorites', '收藏');
            heart.setAttribute('aria-label', heart.lastElementChild.textContent);
            tip(heart, heart.lastElementChild.textContent, t('Keep it in Favorites', '加入或移出收藏'));
        };
        syncHeart();
        const download = el('a', null, 'fv-action fv-action-primary'); download.href = pictureURL(row); download.download = row.name || '';
        download.append(icon('download'), el('span', t('Download picture', '下载图片')));
        tip(download, t('Download picture', '下载图片'), t('Save the picture to this computer', '把图片保存到电脑'));
        // The image view's own next steps for a picture: its prompt back in the box, and the picture itself.
        const text = picturePrompt(row);
        const usePrompt = button('', () => usePicturePrompt(text), 'fv-action fv-action-secondary fv-action-use-prompt');
        usePrompt.append(icon('reuse'), el('span', t('Use prompt', '使用提示词')));
        tip(usePrompt, t('Use prompt', '使用提示词'), t('Put its prompt in the box of the image view', '把它的提示词填回图像视图的描述框'));
        usePrompt.hidden = !text;
        const imageView = button('', () => openInImageView(row), 'fv-action fv-action-secondary fv-action-image-view');
        imageView.append(line('picture'), el('span', t('Open in Images', '在图像视图打开')));
        tip(imageView, t('Open in Images', '在图像视图打开'), t('Use it as a reference or make more like it there', '在那里用作参考图，或接着生成'));
        let arm = 0;
        const remove = button('', () => {
            if (!arm) {
                remove.classList.add('fv-library-arming'); remove.lastElementChild.textContent = t('Confirm delete', '确认删除');
                arm = setTimeout(() => { arm = 0; remove.classList.remove('fv-library-arming'); remove.lastElementChild.textContent = t('Delete', '删除'); }, 3000);
                return;
            }
            clearTimeout(arm); arm = 0;
            holdPictures([workKey(row)]);
        }, 'fv-action fv-action-secondary fv-library-picture-delete');
        remove.append(icon('trash'), el('span', t('Delete', '删除')));
        tip(remove, t('Delete', '删除'), t('Delete it; undo for 10 seconds', '删除这张图片，10 秒内可撤销'));
        bar.append(heart, download, usePrompt, imageView, remove);
        return bar;
    }
    function select(key, reveal = true) {
        selectDetail(key, reveal && layout !== 'stage');
        if (layout === 'stage' && works.has(key) && viewing !== key && dialog.dataset.closing !== 'true') paint(key);
    }
    function selectDetail(key, reveal = true) {
        const row = works.get(key); if (!row) return;
        detail.dataset.empty = 'false';
        if (selected !== key || links.dataset.favorite !== String(!!row.favorite)) {
            const changed = selected !== key;
            selected = key;
            if (changed) mountPlayer(row);
            links.dataset.favorite = String(!!row.favorite);
            date.textContent = workName(row); geometry.textContent = row.kind === 'image' ? pictureDimensions(row) : dimensions(row);
            links.replaceChildren(row.kind === 'image' ? pictureActions(row) : resultActions(row, t, {
                extend: extendSaved,
                reuse: row.preview ? null : reuseNow(),
                secondPass: row.preview && secondPassRunner() ? () => secondPass(row) : null,
                favorite: value => saveFavorite(row, value),
                remove: () => remove(row)}));
            tipActions(links);
            notice.textContent = '';
            for (const [file, card] of cards) card.setAttribute('aria-pressed', String(selecting ? picked.has(file) : file === selected));
            fold();
        }
        drawDetailTags(key);
        showPrompt(row);
        if (row.kind === 'image') {
            // A picture's time, as the image view shows it; kept only for pictures made in this browser.
            const seconds = Number(pictureFacts.get(row.file)?.seconds);
            stats.replaceChildren();
            if (seconds > 0) { const item = el('div', null, 'fv-stat'); item.append(el('strong', t(`${seconds} s`, `${seconds} 秒`)), el('span', t('Time', '用时'))); stats.append(item); }
            stats.hidden = !stats.childElementCount; budget.textContent = ''; run.hidden = stats.hidden;
            if (reveal) dialog.dataset.detail = 'true'; return;
        }
        // These are the saved generation's measurements, including when the
        // same MP4 has since been reused. Never substitute the active request.
        const measured = value => Number.isFinite(value) && value >= 0;
        const number = (value, scale, unit) => measured(value) ? `${(value / scale).toFixed(1)} ${unit}` : '—';
        stats.replaceChildren();
        for (const [label, value, scale, unit] of [
            [t('Generation', '生成用时'), row.sample_seconds, 1, t('s', '秒')],
            [t('Total time', '总用时'), row.request_seconds, 1, t('s', '秒')],
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
        run.hidden = stats.hidden && !budget.textContent;
        if (reveal) dialog.dataset.detail = 'true';
    }
    // Continue a video into a longer one: the editor opens on it and the room closes.
    async function extendSaved(saved) {
        const isCurrent = () => !disposed && dialog.open && dialog.dataset.closing !== 'true' && works.has(workKey(saved));
        if (!isCurrent()) return;
        player?.pause();
        const run = extend || (async (record, options) => (await import('./studio.js')).openExtendEditor(record, undefined, options));
        try {
            const editor = await run(saved, {isCurrent});
            if (!editor && isCurrent()) throw new Error(t('Could not open the editor. Try again.', '无法打开剪辑，请重试。'));
            if (editor && !disposed) { dialog.close(); editor.element?.focus({preventScroll: true}); }
            return editor;
        }
        catch (error) { fault(notice, userMessage(error, t, t('Could not open the editor. Try again.', '无法打开剪辑，请重试。')), ['Continue video', '延长视频'], null, error); }
    }
    // Fill the creation panel with a video's prompt, size, length and seed, then go back to it.
    async function reuseSaved(row) {
        const saved = await savedSettings(api, row);
        if (disposed) return;
        if (!saved) { say(t('This video kept no prompt to reuse.', '这条视频没有保存可复用的提示词。'), true); return; }
        try { reuseNow().apply(saved); if (reuse) closeDialog(dialog); } catch { say(t('Could not fill in the settings. Try again.', '没能填入设置，请重试。'), true); }
    }
    const cardLabel = row => [workName(row), row.favorite ? t('Favorite', '已收藏') : ''].filter(Boolean).join(' · ');
    function syncFavorite(row) {
        const card = cards.get(workKey(row)), badge = card?.querySelector('.fv-library-favorite');
        if (badge) badge.hidden = !row.favorite;
        card?.setAttribute('aria-label', cardLabel(row));
    }
    async function saveFavorite(row, favorite, quiet = false) {
        const id = row.id || row.video.replace(/^FreeVideo\//, '').replace(/\/video\.mp4$/, '');
        notice.textContent = '';
        try {
            const response = await api.fetchApi('/freevideo/library/favorite', {method: 'POST',
                headers: jsonHeaders, body: JSON.stringify({id, favorite}), signal: abort.signal});
            if (!response.ok) throw Object.assign(new Error('Favorite unavailable'), {status: response.status});
            const value = await response.json();
            if (value.favorite !== favorite) throw new Error('Invalid favorite response');
        } catch (error) {
            if (!disposed && !quiet && error.name !== 'AbortError') {
                // The creation is gone (deleted or moved): trying again cannot help.
                const text = isNetworkTypeError(error) ? connectionLostText(t)
                    : error.status === 404 ? videoUnavailableText(t)
                    : favorite ? t('Could not save the favorite. Try again.', '无法保存收藏，请重试。')
                    : t('Could not remove the favorite. Try again.', '无法取消收藏，请重试。');
                fault(notice, text, ['Favorites', '收藏']);
            }
            throw error;
        }
        row.favorite = favorite;
        if (disposed) return;
        const current = works.get(workKey(row));
        if (current) current.favorite = favorite;
        syncFavorite(row);
        if (selected === workKey(row)) links.dataset.favorite = String(favorite);
        // Under Favorites an un-favorited card stays until the filter changes or the list is refreshed,
        // so the creation being viewed does not vanish and one more click restores it.
        if (loading || (filter.favorites && favorite && !current)) await load(false);
        syncCounts();
    }
    async function secondPass(row) {
        notice.textContent = '';
        try {
            await secondPassRunner()(row);
            if (!disposed && selected === workKey(row)) notice.textContent = t('Queued. It appears in "Creations" when done.', '已加入队列，完成后在“作品”里。');
        } catch (error) {
            if (!disposed && selected === workKey(row)) fault(notice, userMessage(error, t, t('Could not start the second pass. Try again.', '没能开始二采，请重试。')), ['Second pass', '二采'], null, error);
            throw error;
        }
    }
    const refusal = (reason, kept = []) => reason === 'foreign-files'
        ? t('Not deleted: the folder also holds files FreeVideo did not write: ', '未删除：文件夹里还有不是 FreeVideo 生成的文件：') + kept.join(', ')
        : reason === 'editor-in-use' ? t('Not deleted: these edits use it: ', '未删除：这些剪辑正在使用它：') + kept.join(', ')
        : reason === 'editor-unavailable' ? t('Not deleted: saved edits could not be checked. Try again in a moment.', '未删除：暂时无法检查已保存的剪辑，请稍后再试。')
        : reason === 'continuation-in-use' ? t('Not deleted: a video continuing from it is running or queued. Try again after it finishes.', '未删除：接在这条视频后面的生成正在运行或排队，请等它完成后再试。')
        : reason === 'in-use' ? t('Not deleted: its second pass is running, or the video is still open. Try again in a moment.', '未删除：正在用它跑二采，或视频仍被占用，请稍后再试。')
        : t('Could not delete this creation.', '无法删除这条作品。');
    // Delete from the detail: the same undoable batch as a selection, so it can be brought back for 10 seconds.
    // Throws a readable Error for the confirmation to show.
    async function remove(row) {
        const key = workKey(row);
        const result = await removeKeys([key]);
        if (result.removed.has(key)) { notice.textContent = ''; return; }
        const skipped = result.skipped.find(item => item.id === row.id);
        throw Object.assign(new Error(skipped ? refusal(skipped.reason, skipped.kept || []) : result.why || t('Could not delete this creation.', '无法删除这条作品。')), {
            reportable: skipped ? !['foreign-files', 'editor-in-use', 'continuation-in-use', 'in-use'].includes(skipped.reason) : !result.why,
        });
    }
    // --- Selecting several creations ---
    const gb = bytes => `${(bytes / 2 ** 30).toFixed(bytes < 2 ** 30 ? 2 : 1)} GB`;
    const ordered = () => [...grid.querySelectorAll('.fv-library-card')];
    // The videos of one task fold under the newest; one task is unfolded at a time, and picking shows every card.
    let unfolded = null;
    function fold() {
        const order = ordered().map(card => card.dataset.key).filter(isVideo).map(key => works.get(key)?.video).filter(Boolean);
        if (!order.some(video => rows.get(video)?.task === unfolded)) unfolded = null;
        const byVideo = new Map(order.map(video => [video, cards.get(keyOfVideo(video))]));
        const heads = applyTasks({cards: byVideo, rows, order, open: unfolded, selecting, selected: works.get(selected)?.video ?? null, t, label: video => cardLabel(rows.get(video))});
        for (const [video, card] of byVideo) {
            if (!card) continue;
            const head = heads.get(video), folded = head && !card.classList.contains('fv-task-open') && !selecting;
            let stack = card.querySelector('.fv-library-stack');
            if (folded && !stack) { stack = el('span', null, 'fv-library-stack'); card.querySelector('.fv-library-picture')?.append(stack); }
            if (stack) { stack.hidden = !folded; if (folded) stack.textContent = String(head.videos.length); }
        }
        countDays();
    }
    const dayName = day => {
        if (!day) return t('Earlier', '更早');
        const [y, m, d] = day.split('-').map(Number), value = new Date(y, (m || 1) - 1, d || 1);
        const today = dayOf({created_at: new Date().toISOString()});
        const yesterday = dayOf({created_at: new Date(Date.now() - 864e5).toISOString()});
        if (day === today) return t('Today', '今天');
        if (day === yesterday) return t('Yesterday', '昨天');
        return Number.isNaN(value.getTime()) ? day : (t('en', 'zh') === 'zh' ? `${value.getMonth() + 1}月${value.getDate()}日 ${value.toLocaleDateString('zh-CN', {weekday: 'short'})}` : value.toLocaleDateString('en-US', {month: 'long', day: 'numeric', weekday: 'short'}));
    };
    const dayOfKey = key => dayOf(works.get(key) || {});
    const lastDay = () => dayOfKey(ordered().at(-1)?.dataset.key);
    // What the shelf shows now: the filter and sort over what is loaded.
    function shown() {
        const videos = filter.kind === 'image' ? [] : [...works.values()].filter(row => row.kind !== 'image');
        const loadedVideos = [...works.values()].filter(row => row.kind !== 'image');
        const pics = filter.kind === 'video' ? [] : sort === 'new' && !everything ? inOrderPictures(pictureRows, loadedVideos, !!next) : pictureRows;
        const context = {tagsOf: key => meta.tagsOf(key), promptOf: row => prompts.get(row.id) || ''};
        return sortRows([...videos, ...pics.filter(row => !pictureBin.holding(row.file) && !heldElsewhere.has(row.file))].filter(row => matches(row, filter, context)), sort);
    }
    const seconds = row => { const g = row.geometry || {}; return g.seconds || (g.frames && g.frames / (g.fps || 24)) || 0; };
    // A video's card is named after its prompt, read when the card first comes into view (four at a time).
    const promptWanted = [], promptAsked = new Set();
    let promptWorkers = 0;
    const snippet = text => { const first = String(text || '').replace(/\s+/g, ' ').trim(); return first.length > 40 ? first.slice(0, 40) + '…' : first; };
    const clock = row => { const value = new Date(row.created_at); return Number.isNaN(value.getTime()) ? '' : `${String(value.getHours()).padStart(2, '0')}:${String(value.getMinutes()).padStart(2, '0')}`; };
    // A creation is named by its words (or by what it is), never by a date or a time; the line under it is its size.
    const workName = row => row.agent?.label || snippet(row.kind === 'image' ? picturePrompt(row) : prompts.get(row.id)) || (row.kind === 'image' ? t('Picture', '图片') : t('Video', '视频'));
    function labelCard(key) {
        const row = works.get(key), card = cards.get(key);
        if (!row || !card) return;
        // A card is its picture and its name; size and length stay in the details.
        card.children[1].textContent = workName(row);
        card.children[2].textContent = '';
        card.setAttribute('aria-label', cardLabel(row)); cardTip(card, row);
    }
    function wantPrompt(row) {
        if (!row?.id || row.kind === 'image' || prompts.has(row.id) || promptAsked.has(row.id)) return;
        promptAsked.add(row.id); promptWanted.push(row);
        while (promptWorkers < 4 && promptWanted.length) { promptWorkers++; readPrompts(); }
    }
    async function readPrompts() {
        while (promptWanted.length && !disposed) {
            const row = promptWanted.shift();
            try {
                const response = await api.fetchApi('/freevideo/library/prompt?' + new URLSearchParams({id: row.id}), {signal: abort.signal});
                prompts.set(row.id, response.ok ? (await response.json()).prompt || '' : '');
            } catch { if (disposed) break; prompts.set(row.id, ''); }
            const key = workKey(row);
            labelCard(key, sort === 'new' || sort === 'old');
            if (selected === key) showPrompt(row);
            if (viewing === key) { const name = viewer.querySelector('.fv-library-viewer-name'); if (name) name.title = name.textContent = viewerName(row); }
        }
        promptWorkers--;
    }
    function showPrompt(row) {
        if (row.kind === 'image') {
            // A picture's words come from the engine's record (or this page's memory); one without says so.
            const text = picturePrompt(row);
            promptText.textContent = text || t('This picture kept no prompt.', '这张图片没有保存提示词。');
            promptText.dataset.none = String(!text); promptCopy.hidden = !text; promptBox.hidden = false;
            return;
        }
        delete promptText.dataset.none; promptCopy.hidden = false;
        const text = prompts.get(row.id);
        if (text === undefined) wantPrompt(row);
        promptText.textContent = text || ''; promptBox.hidden = !text;
    }
    function makeCard(row) {
        const key = workKey(row);
        // A click opens the creation in the viewer. The check box on the corner (on hover, always while selecting)
        // picks it; the stack mark on a cover unfolds the others of its task in place.
        const card = button('', event => {
            if (event.target.closest?.('.fv-library-check')) { if (!selecting) setSelecting(true); pick(key, event.shiftKey); return; }
            // Ctrl/⌘ adds, Shift adds the range from the creation on the stage, as in the image view's works.
            if (!selecting && (event.ctrlKey || event.metaKey || event.shiftKey) && layout === 'stage') {
                const from = viewing && viewing !== 'compare' ? viewing : selected;
                setSelecting(true);
                if (from && works.has(from)) { picked.add(from); anchor = from; }
                pick(key, event.shiftKey); return;
            }
            if (selecting) { pick(key, event.shiftKey); return; }
            if (isVideo(key) && event.target.closest?.('.fv-library-stack')) {
                unfolded = toggledTask(rows, ordered().map(item => item.dataset.key).filter(isVideo).map(item => works.get(item)?.video), row.video, unfolded);
                fold(); return;
            }
            if (layout === 'stage') select(key, false); else openViewer(key);
        }, 'fv-library-card');
        card.dataset.key = key; card.dataset.kind = row.kind === 'image' ? 'image' : 'video';
        if (row.video) card.dataset.video = row.video;
        // A video card plays a silent preview while the pointer rests on it.
        if (row.kind !== 'image') {
            let wait = 0, preview = null;
            card.addEventListener('pointerenter', event => {
                if (event.pointerType === 'touch' || selecting || layout === 'stage') return;
                if (compatView()) return; // The compatibility view plays nothing until asked.
                wait = setTimeout(() => {
                    // Over a link the pointer only moves the video to the front of the queue; it plays here once it is in.
                    const queue = mediaQueue();
                    if (queue.enabled && !queue.loaded(view(row.video))) { queue.want(view(row.video), {kind: 'video', rank: -1e15 - performance.now(), bytes: row.bytes}); return; }
                    preview = el('video', null, 'fv-library-hover'); preview.muted = true; preview.loop = true; preview.playsInline = true; preview.preload = 'auto';
                    preview.src = view(row.video); preview.addEventListener('playing', () => { preview.dataset.on = 'true'; }, {once: true});
                    card.querySelector('.fv-library-picture')?.append(preview); preview.play().catch(() => {});
                }, 350);
            });
            card.addEventListener('pointerleave', () => { clearTimeout(wait); if (preview) { preview.pause(); preview.removeAttribute('src'); preview.load(); preview.remove(); preview = null; } });
        }
        // Right click, Shift+F10, the menu key or a long press: the shared menu (context_menu.js), each row with its own hover line.
        attachContextMenu(card, event => {
            if (!selecting) select(key, false);
            return cardMenuItems(card, selecting && picked.has(key) ? [...picked] : [key], {x: event.clientX, y: event.clientY});
        }, {label: t('Creation', '作品')});
        const picture = el('div', null, 'fv-library-picture');
        const image = el('img'); image.alt = ''; image.decoding = 'async';
        // Until it arrives the card shows its solid ground at its final size; the picture then only fades in.
        image.dataset.src = row.kind === 'image' ? pictureURL(row) : videoThumbnailURL(row);
        if (row.kind === 'image') image.dataset.picture = row.file;
        image.onerror = () => { image.hidden = true; };
        image.onload = () => { image.dataset.loaded = 'true'; };
        picture.append(image, el('span', null, 'fv-library-check'));
        picture.lastElementChild.append(line('check'));
        const favorite = el('span', null, 'fv-library-favorite'); favorite.append(icon('heart'));
        favorite.setAttribute('aria-hidden', 'true');
        favorite.hidden = !row.favorite; picture.append(favorite);
        if (row.kind === 'image') { const kind = el('span', null, 'fv-library-kind'); kind.append(line('picture')); picture.append(kind); }
        else { const kind = el('span', null, 'fv-library-kind'); kind.append(line('video')); picture.append(kind); }
        if (row.preview) picture.append(el('span', t('First-pass preview', '一采预览'), 'fv-preview-badge'));
        // Made by an AI agent: a mark on the corner, the shot's name on hover and as the card's title line.
        if (row.agent) {
            const badge = el('span', null, 'fv-agent-badge'); badge.append(line('spark'));
            badge.setAttribute('aria-label', t('Made by the assistant', '助手生成'));
            tip(badge, t('Made by the assistant', '助手生成'), [row.agent.label, row.agent.project].filter(Boolean).join(' · '));
            picture.append(badge);
        }
        card.append(picture, el('span', ''), el('small', ''));
        card.setAttribute('aria-label', cardLabel(row));
        cardTip(card, row);
        cards.set(key, card); thumbnails.observe(image); originals.observe(card);
        return card;
    }
    // Lays the shelf out from the filter: cards are kept and moved, days get a heading while the order is by time.
    function render() {
        if (disposed) return;
        const list = shown(), keys = new Set(list.map(workKey));
        // The anchor: the first card at least partly in view, and where it is now.
        const view = collection.getBoundingClientRect();
        let anchorCard = null, anchorTop = 0;
        if (collection.scrollTop > 0) for (const node of grid.children) {
            if (node.hidden || !node.classList.contains('fv-library-card')) continue;
            const box = node.getBoundingClientRect(); if (box.bottom > view.top + 1) { anchorCard = node; anchorTop = box.top; break; }
        }
        for (const [key, card] of cards) if (!keys.has(key)) card.remove();
        const byTime = false;  // No day headings: the order (newest first) is enough, and creations carry no dates.
        const wanted = [], usedDays = new Set();
        let day = null;
        for (const row of list) {
            const key = workKey(row);
            const value = dayOf(row);
            if (byTime && value !== day) {
                day = value; usedDays.add(value);
                let heading = dayHeadings.get(value);
                if (!heading) {
                    heading = el('div', null, 'fv-library-day'); heading.dataset.day = value;
                    const whole = button('', () => pickDay(value, whole), 'fv-quiet');
                    heading.append(el('span', dayName(value), 'fv-library-day-name'), el('span', '', 'fv-library-day-count'), whole);
                    dayHeadings.set(value, heading);
                }
                wanted.push(heading);
            }
            wanted.push(cards.get(key) || makeCard(row));
            labelCard(key);
        }
        for (const [value, heading] of dayHeadings) if (!usedDays.has(value)) { heading.remove(); dayHeadings.delete(value); }
        // Move only what is out of place, so focus, hover and the scroll stay where they are.
        let at = grid.firstChild;
        for (const node of wanted) {
            if (node === at) { at = at.nextSibling; continue; }
            grid.insertBefore(node, at);
        }
        while (at) { const after = at.nextSibling; if (at.classList?.contains('fv-library-day') || at.classList?.contains('fv-library-card')) at.remove(); at = after; }
        if (anchorCard?.isConnected) { const moved = anchorCard.getBoundingClientRect().top - anchorTop; if (Math.abs(moved) > .5) collection.scrollTop += moved; }
        if (selected && !keys.has(selected) && !narrow.matches) { const first = list[0]; if (first) select(workKey(first), false); else clearDetail(); }
        else if (!selected && !narrow.matches && !drawer.matches && list.length) select(workKey(list[0]), false);
        collection.dataset.empty = String(!list.length && !trash);
        for (const ghost of grid.querySelectorAll(':scope > .fv-library-ghost')) ghost.remove();
        if (loading && !list.length && !trash) grid.append(...Array.from({length: layout === 'stage' ? 8 : 9}, () => el('div', null, 'fv-library-ghost')));
        message.textContent = loading && !works.size ? ''
            : gathering ? t('Reading all creations…', '正在读取全部作品…')
            : list.length ? '' : emptyText();
        // The header's Refresh stays hidden (the shelf follows changes by itself), so a failed read carries its own retry.
        if (!list.length && !trash && !loading && filter.kind === 'image' && picturesState === 'failed') retryRead(message);
        drawTail();
        fold(); syncSelection(false); syncCounts();
        clearTimeout(cacheTimer); cacheTimer = setTimeout(saveCache, 1000);
    }
    let cacheTimer = 0, fromCache = false;
    function saveCache() {
        if (disposed || filter.favorites || trash) return;
        const videos = [...works.values()].filter(row => row.kind !== 'image').sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)).slice(0, 24);
        const oldest = Math.min(...videos.map(row => Date.parse(row.created_at)).filter(Number.isFinite), Infinity);
        const pictures = pictureRows.filter(row => !(Date.parse(row.created_at) < oldest)).slice(0, 48).map(({file, name, type, created_at}) => ({file, name, type, created_at}));
        try { localStorage.setItem(CACHE_KEY, JSON.stringify({videos, pictures, selected: selected || null})); } catch { /* Too big or refused: the next opening reads the engine first. */ }
    }
    // A day's count is what it shows: a folded task counts once, with its own number on the cover.
    function countDays() {
        for (const heading of grid.querySelectorAll('.fv-library-day')) {
            let count = 0; for (let node = heading.nextElementSibling; node && !node.classList.contains('fv-library-day'); node = node.nextElementSibling) if (!node.hidden) count++;
            heading.children[1].textContent = String(count);
        }
    }
    function emptyText() {
        if (filter.query) return t('Nothing matches this search.', '没有符合搜索的作品。');
        if (filter.tag) return t('No creation has this tag yet. Select creations and press Tag.', '还没有作品带这个标签。选中作品后点“标签”。');
        if (filter.period !== 'any' || filter.source || filter.preview || filter.agent) return t('Nothing matches these filters.', '没有符合筛选条件的作品。');
        if (filter.favorites) return t("No favorite creations. Click the heart in a creation's details to add one.", '暂无收藏的作品。点作品详情中的爱心图标收藏。');
        if (filter.kind === 'image') return picturesState === 'failed' ? t('Pictures could not be read just now.', '图片暂时无法读取。') : t('Pictures made in the image view appear here.', '在图像视图里生成的图片会保存在这里。');
        return t('Finished videos and pictures appear here.', '生成完成的视频和图片会保存在这里。');
    }
    function syncCounts() {
        const videos = [...works.values()].filter(row => row.kind !== 'image');
        const pics = pictureRows.filter(row => !pictureBin.holding(row.file));
        const plus = next ? '+' : '';
        placeAll.lastElementChild.textContent = filter.favorites ? '' : `${videos.length + pics.length}${plus}`;
        placeVideo.lastElementChild.textContent = filter.favorites ? '' : `${videos.length}${plus}`;
        placeImage.lastElementChild.textContent = pics.length ? String(pics.length) : '';
        placeFavorites.lastElementChild.textContent = '';
        const held = offeredUndoRows().reduce((sum, row) => sum + row.count, 0) + pictureBin.list().reduce((sum, row) => sum + row.files.length, 0);
        placeTrash.lastElementChild.textContent = held ? String(held) : '';
        const current = trash ? 'trash' : filter.tag ? 'tag:' + filter.tag : filter.favorites ? 'favorites' : filter.kind;
        for (const item of side.querySelectorAll('.fv-library-place[data-place]')) {
            const on = item.dataset.place === current;
            item.setAttribute('aria-current', on ? 'page' : 'false');
        }
        for (const item of tagList.querySelectorAll('[data-tag]')) item.querySelector('.fv-library-count').textContent = String(meta.count(item.dataset.tag) || '');
    }
    // --- Side bar places and tags ---
    function goPlace(key) {
        if (deleting) return;
        closeMenus();
        const wasTrash = trash;
        trash = key === 'trash';
        if (!trash) {
            const favorites = key === 'favorites';
            const changedServer = favorites !== filter.favorites;
            filter.favorites = favorites; filter.tag = key.startsWith('tag:') ? key.slice(4) : null;
            filter.kind = ['all', 'video', 'image'].includes(key) ? key : 'all';
            // The place is marked in this frame; the shelf follows from what is loaded (no request, no overlay, nothing
            // disabled), so the next click works at once.
            syncCounts();
            try { localStorage.setItem(FILTER_KEY, favorites ? 'favorites' : 'all'); } catch { /* This view still works. */ }
            persistView();
            setSelecting(false);
            dialog.dataset.trash = 'false'; trashView.hidden = true; grid.hidden = false; choose.hidden = tab !== 'works';
            if (wasTrash) drawTrash();
            // The shelf is laid out after this frame is painted, once for a run of quick clicks (the last one wins).
            renderSoon();
            // Favorites among creations not read yet come in the background, page by page.
            if (changedServer && favorites) void gatherFavorites();
            return;
        }
        setSelecting(false); choose.hidden = true;
        dialog.dataset.trash = 'true'; grid.hidden = true; trashView.hidden = false; message.textContent = ''; drawTail();
        drawTrash(); syncCounts();
    }
    // The shelf swaps by a cross-fade: the old cards stay until the new order is there.
    function transition(change) {
        if (!document.startViewTransition || reducedMotion() || !dialog.isConnected) { change(); return; }
        grid.style.viewTransitionName = 'fv-library-shelf';
        const done = document.startViewTransition(change);
        done.finished.finally(() => { grid.style.viewTransitionName = ''; });
    }
    function tagInput(placeholder, value, done) {
        const input = el('input', null, 'fv-library-tag-input'); input.type = 'text'; input.maxLength = 40; input.value = value; input.placeholder = placeholder;
        input.setAttribute('aria-label', placeholder);
        let finished = false;
        const finish = keep => { if (finished) return; finished = true; done(keep ? input.value : null); };
        input.onkeydown = event => {
            if (event.key === 'Enter') { event.preventDefault(); finish(true); }
            else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(false); }
        };
        input.onblur = () => finish(true);
        return input;
    }
    function startNewTag() {
        const input = tagInput(t('Tag name', '标签名称'), '', value => {
            input.remove(); newTag.hidden = false;
            const tag = value && meta.addTag(value);
            if (tag && selected && !selecting) meta.setTags([selected], [tag.id]);
            if (tag && selecting && picked.size) meta.setTags([...picked], [tag.id]);
            newTag.focus({preventScroll: true});
        });
        newTag.hidden = true; tagGroup.append(input); input.focus();
    }
    function renameTag(item, tag) {
        const input = tagInput(t('Tag name', '标签名称'), tag.name, value => { if (value && value.trim()) meta.renameTag(tag.id, value); drawSideTagsList(); });
        item.replaceWith(input); input.focus(); input.select();
    }
    // --- Menus (filter, sort, tags): glass popovers under their button; Escape closes only the menu ---
    let menuOpen = null, menuAnchor = null;
    function closeMenus() {
        if (!menuOpen) return;
        menuOpen.hidden = true; menuAnchor?.setAttribute('aria-expanded', 'false'); menuOpen = null; menuAnchor = null;
        document.removeEventListener('pointerdown', outside, true);
    }
    const outside = event => { if (menuOpen && !menuOpen.contains(event.target) && !menuAnchor?.contains(event.target)) closeMenus(); };
    function placeMenu(menu, anchor, point = null) {
        const box = collection.getBoundingClientRect(), r = anchor.getBoundingClientRect();
        menu.style.left = '0px'; menu.style.top = '0px'; menu.hidden = false;
        const width = menu.offsetWidth, height = menu.offsetHeight;
        let left = (point ? point.x : r.left) - box.left, top = (point ? point.y : r.bottom + 6) - box.top + collection.scrollTop;
        left = Math.max(8, Math.min(left, collection.clientWidth - width - 8));
        if (top - collection.scrollTop + height > collection.clientHeight - 8) top = Math.max(collection.scrollTop + 8, (point ? point.y : r.top - 6) - box.top + collection.scrollTop - height);
        menu.style.left = left + 'px'; menu.style.top = top + 'px';
    }
    function openMenu(anchor, menu, point = null) {
        closeMenus();
        menuOpen = menu; menuAnchor = anchor; anchor.setAttribute('aria-expanded', 'true');
        placeMenu(menu, anchor, point);
        document.addEventListener('pointerdown', outside, true);
        menu.querySelector('[role^=menuitem]:not([aria-disabled=true]),input')?.focus({preventScroll: true});
    }
    function toggleMenu(anchor, menu) {
        if (menuOpen === menu) { closeMenus(); return; }
        (menu === filterMenu ? drawFilterMenu : drawSortMenu)();
        openMenu(anchor, menu);
    }
    const menuItem = (label, checked, action, role = 'menuitemradio', use = '') => {
        const item = button('', action, 'fv-library-menu-item'); item.setAttribute('role', role); item.setAttribute('aria-checked', String(!!checked));
        item.append(line('check'), el('span', label)); if (use) tip(item, label, use);
        return item;
    };
    function drawFilterMenu() {
        const period = [['any', t('Any time', '任何时间')], ['today', t('Today', '今天')], ['week', t('Last 7 days', '最近 7 天')], ['month', t('Last 30 days', '最近 30 天')]];
        filterMenu.replaceChildren(el('div', t('When', '时间'), 'fv-library-menu-label'));
        for (const [key, label] of period) filterMenu.append(menuItem(label, filter.period === key, () => { filter.period = key; drawFilterMenu(); applyFilter(); }));
        filterMenu.append(el('div', null, 'fv-library-menu-sep'), el('div', t('Pictures from', '图片来源'), 'fv-library-menu-label'));
        for (const [key, label] of [['', t('Any', '全部')], ['image', t('Image', '图片')], ['edit', t('Edit', '编辑')], ['cast', t('Character', '角色')]]) {
            filterMenu.append(menuItem(label, filter.source === key, () => { filter.source = key; drawFilterMenu(); applyFilter(); }));
        }
        filterMenu.append(el('div', null, 'fv-library-menu-sep'), el('div', t('Only', '只看'), 'fv-library-menu-label'));
        filterMenu.append(menuItem(t('First-pass previews', '一采预览'), filter.preview, () => { filter.preview = !filter.preview; drawFilterMenu(); applyFilter(); }, 'menuitemcheckbox', t('Videos that still need a second pass', '还没有继续二采的视频')));
        filterMenu.append(menuItem(t('Made by the assistant', '助手生成'), filter.agent, () => { filter.agent = !filter.agent; drawFilterMenu(); applyFilter(); }, 'menuitemcheckbox', t('Videos made by the assistant', '由助手生成的视频')));
        const clear = button(t('Clear filters', '清除筛选'), () => { filter.period = 'any'; filter.source = ''; filter.preview = filter.agent = false; drawFilterMenu(); applyFilter(); }, 'fv-library-menu-clear');
        clear.disabled = filter.period === 'any' && !filter.source && !filter.preview && !filter.agent;
        tip(clear, t('Clear filters', '清除筛选'), t('Show everything again', '恢复显示全部'));
        filterMenu.append(el('div', null, 'fv-library-menu-sep'), clear);
    }
    const SORT_NAMES = () => ({new: t('Newest first', '最新在前'), old: t('Oldest first', '最早在前'), long: t('Longest first', '时长最长'),
        large: t('Largest size first', '尺寸最大'), bytes: t('Biggest file first', '文件最大')});
    function drawSortMenu() {
        sortMenu.replaceChildren(el('div', t('Order', '排序'), 'fv-library-menu-label'));
        for (const [key, label] of Object.entries(SORT_NAMES())) sortMenu.append(menuItem(label, sort === key, () => { sort = key; persistView(); closeMenus(); applyFilter(); }));
    }
    function syncTools() {
        const count = (filter.period !== 'any') + !!filter.source + filter.preview + filter.agent;
        filterButton.querySelector('.fv-library-badge').textContent = count ? String(count) : '';
        filterButton.dataset.active = String(!!count);
        sortButton.querySelector('.fv-library-tool-label').textContent = SORT_NAMES()[sort];
        denseButton.setAttribute('aria-pressed', String(dense));
        dialog.dataset.dense = String(dense);
    }
    let renderPending = 0;
    function renderSoon() {
        if (renderPending) return;
        renderPending = requestAnimationFrame(() => setTimeout(() => { renderPending = 0; if (!disposed) { render(); gatherIfNeeded(); } }, 0));
    }
    function applyFilter() { syncTools(); if (trash) goPlace(filter.tag ? 'tag:' + filter.tag : filter.favorites ? 'favorites' : filter.kind); else { render(); gatherIfNeeded(); } }
    let favoriteRound = 0;
    async function gatherFavorites() {
        const round = ++favoriteRound;
        while (filter.favorites && next && !disposed && round === favoriteRound && await load(true)) { /* next page */ }
    }
    // Tags on one or several creations: a check list, tri-state for a selection, and a new tag at the bottom.
    function openTagMenu(anchor, keys, point = null) {
        if (!keys.length) return;
        const draw = () => {
            tagMenu.replaceChildren(el('div', keys.length > 1 ? t(`Tags for ${keys.length} creations`, `${keys.length} 个作品的标签`) : t('Tags', '标签'), 'fv-library-menu-label'));
            for (const tag of meta.tags()) {
                const have = keys.filter(key => meta.tagsOf(key).includes(tag.id)).length;
                const item = menuItem(tag.name, have === keys.length, () => { meta.setTags(keys, have === keys.length ? [] : [tag.id], have === keys.length ? [tag.id] : []); draw(); }, 'menuitemcheckbox');
                if (have && have < keys.length) item.setAttribute('aria-checked', 'mixed');
                const dot = el('span', null, 'fv-library-dot'); dot.dataset.color = tag.color; item.insertBefore(dot, item.lastElementChild);
                tagMenu.append(item);
            }
            const input = tagInput(t('New tag…', '新建标签…'), '', value => {
                const tag = value && meta.addTag(value);
                if (tag) meta.setTags(keys, [tag.id]);
                if (menuOpen === tagMenu) { draw(); tagMenu.querySelector('input')?.focus(); }
            });
            input.onblur = null;
            tagMenu.append(el('div', null, 'fv-library-menu-sep'), input);
        };
        draw();
        openMenu(anchor, tagMenu, point);
    }
    // What can be done with a card (or with the selection it belongs to), as rows of the shared menu. Ids name the
    // usual rows, whose hover lines context_menu.js already has; a row's own `use` replaces that line.
    function cardMenuItems(target, keys, point) {
        keys = keys.filter(key => works.has(key));
        if (!keys.length) return null;
        const one = keys.length === 1 ? works.get(keys[0]) : null;
        const allFavorite = keys.every(key => works.get(key)?.favorite);
        const items = [];
        if (one) items.push({id: 'open', icon: 'open', label: ['View large', '查看大图'], key: 'Space', run: () => openViewer(keys[0])});
        items.push({id: 'favorite', icon: 'star', label: allFavorite ? ['Remove from favorites', '取消收藏'] : ['Add to favorites', '收藏'], run: async () => {
            const value = !allFavorite, files = keys.filter(key => !isVideo(key)).map(key => works.get(key).file);
            if (files.length) meta.setPictureFavorite(files, value);
            for (const key of keys) { const row = works.get(key); if (!isVideo(key)) { row.favorite = value; syncFavorite(row); } }
            let failed = 0;
            for (const key of keys.filter(isVideo)) { try { await saveFavorite(works.get(key), value, true); } catch { failed++; } }
            if (!disposed) { if (failed) say(t(`${failed} could not be changed. Try again.`, `有 ${failed} 个没能更改，请重试。`), true); if (selected) select(selected, false); render(); }
        }});
        const tagRows = meta.tags().map(tag => {
            const have = keys.filter(key => meta.tagsOf(key).includes(tag.id)).length;
            return {id: 'tags', label: [tag.name, tag.name], checked: have === keys.length, use: have && have < keys.length ? ['Some of the selection have it; choose to add to all', '选中的部分作品带着它，点一下全部加上'] : null,
                run: () => meta.setTags(keys, have === keys.length ? [] : [tag.id], have === keys.length ? [tag.id] : [])};
        });
        items.push({id: 'tags', icon: 'tag', label: ['Tags', '标签'], submenu: [...tagRows, ...(tagRows.length ? ['-'] : []),
            {id: 'new-tag', icon: 'plus', label: ['New tag…', '新建标签…'], run: () => openTagMenu(target, keys, point)}]});
        const comparing = keys.length === 2 ? keys : selecting && picked.size === 2 && picked.has(keys[0]) ? [...picked] : null;
        items.push({id: 'compare', icon: 'compare', label: ['Compare', '对比'], disabled: comparing ? '' : ['Select exactly two creations first', '先正好选中两个作品'], run: () => openCompare(comparing)});
        if (one) {
            items.push('-');
            items.push({id: 'download', icon: 'download', label: one.kind === 'image' ? ['Download picture', '下载图片'] : ['Download video', '下载视频'], run: () => {
                const link = el('a'); link.href = one.kind === 'image' ? pictureURL(one) : outputDownloadURL(api, one.video); link.download = one.kind === 'image' ? (one.name || '') : '';
                document.body.append(link); link.click(); link.remove();
            }});
            if (one.kind !== 'image') {
                if (one.preview) items.push({id: 'second-pass', icon: 'history', label: ['Run second pass', '继续二采'],
                    use: ['Finish the full-resolution video', '从预览继续生成全分辨率视频'],
                    disabled: secondPassRunner() ? '' : ['Open the create view first, then run the second pass', '请先打开创作页，再继续二采'],
                    run: () => secondPass(one).then(() => say(t('Queued. It appears in "Creations" when done.', '已加入队列，完成后在“作品”里。')), error => say(t('Could not start the second pass: ', '无法继续二采：') + error.message, true))});
                // One 1.5× step where this computer can generate it (gap-04a), and the 4K size by a small model (gap-04b):
                // each shown on its own, the same way.
                for (const [id, larger, options] of [['upscale', defaultUpscaleWork && upscaleOffer.offer(one), {}],
                                                     ['upscale-4k', defaultUpscaleWork && upscale4kOffer(one), {to: '4k'}]]) {
                    if (!larger) continue;
                    const size = `${larger.width} × ${larger.height}`;
                    items.push({id, icon: 'upscale', label: [`Upscale to ${size}`, `放大到 ${size}`],
                        use: ['Make a larger copy as a new creation', '生成一个更大的新作品，原作品不变'],
                        run: () => defaultUpscaleWork(one, options).then(() => say(t('Queued. It appears in "Creations" when done.', '已加入队列，完成后在“作品”里。')),
                            error => say(t('Could not start upscaling: ', '无法开始放大：') + error.message, true))});
                }
                items.push({id: 'reuse', icon: 'reuse', label: ['Reuse settings', '复用设置'],
                    disabled: one.preview ? ['A first-pass preview: finish it first', '这是一采预览，先继续二采'] : '',
                    run: () => reuseSaved(one)});
                items.push({id: 'extend', icon: 'arrow', label: ['Continue', '接着生成'], disabled: canExtend(one) ? '' : ['This video cannot be continued', '这条视频不能接着生成'],
                    run: () => extendSaved(one)});
            }
            if (one.kind === 'image') {
                const text = picturePrompt(one), none = text ? '' : ['This picture kept no prompt', '这张图片没有保存提示词'];
                items.push({id: 'use-prompt', icon: 'reuse', label: ['Use prompt', '使用提示词'], disabled: none,
                    use: ['Put its prompt in the box of the image view', '把它的提示词填回图像视图的描述框'], run: () => usePicturePrompt(text)});
                items.push({id: 'image-view', icon: 'image', label: ['Open in Images', '在图像视图打开'],
                    use: ['Use it as a reference or make more like it there', '在那里用作参考图，或接着生成'], run: () => openInImageView(one)});
            }
            const promptOf = one.kind === 'image' ? picturePrompt(one) : prompts.get(one.id);
            items.push({id: 'copy-prompt', icon: 'copy', label: ['Copy prompt', '复制提示词'],
                disabled: promptOf ? '' : one.kind === 'image' ? ['This picture kept no prompt', '这张图片没有保存提示词'] : ['This video kept no prompt', '这条视频没有保存提示词'],
                run: async () => { try { await navigator.clipboard.writeText(promptOf); say(t('Prompt copied.', '已复制提示词。')); } catch { say(t('Could not copy.', '没能复制。'), true); } }});
        }
        if (one) items.push({id: 'folder', icon: 'folder', label: ['Show in folder', '在文件夹中显示'],
            run: async () => {
                if (one.kind !== 'image') {
                    const answer = await openVideoFolder(one.id); const text = videoFolderMessage(t, answer, true);
                    if (text) say(text, !answer || answer.missing, answer?.folder ? 20000 : 4000);
                    return;
                }
                try { const answer = await (await pictures()).historyOpen(); if (answer && answer.opened === false && answer.folder) say(t(`The pictures are in ${answer.folder}`, `图片在 ${answer.folder}`)); }
                catch { say(t('Could not open the folder.', '没能打开文件夹。'), true); }
            }});
        if (!selecting) items.push({id: 'select', icon: 'check', label: ['Select', '选择'], use: ['Start selecting with this one', '从这一个开始多选'],
            run: () => { setSelecting(true); picked.add(keys[0]); anchor = keys[0]; syncSelection(); }});
        items.push('-');
        items.push({id: 'trash', icon: 'trash', danger: true, label: keys.length > 1 ? [`Delete ${keys.length}`, `删除 ${keys.length} 个`] : ['Delete', '删除'], key: 'Delete',
            use: ['Delete; undo for 10 seconds', '删除，10 秒内可撤销'], run: async () => {
                const result = await removeKeys(keys);
                if (disposed) return;
                undoNote = result.skipped.length ? skippedText(result.skipped) : null;
                undoFailure = result.failed ? t('Could not delete. Try again.', '没能删除，请重试。') : '';
                for (const key of result.removed) picked.delete(key);
                render(); showUndo();
            }});
        return items;
    }
    function openTagActions(anchor, tag, point) {
        tagMenu.replaceChildren(el('div', tag.name, 'fv-library-menu-label'));
        const renameItem = button('', () => { closeMenus(); const item = tagList.querySelector(`[data-tag="${tag.id}"]`); if (item) renameTag(item, tag); }, 'fv-library-menu-item');
        renameItem.setAttribute('role', 'menuitem'); renameItem.append(line('tag'), el('span', t('Rename', '改名')));
        tip(renameItem, t('Rename', '改名'), t('Give the tag another name', '给标签换个名字'));
        let armed = false;
        const removeItem = button('', () => {
            if (!armed) { armed = true; removeItem.lastElementChild.textContent = t('Delete the tag? Press again', '删除这个标签？再点一次'); removeItem.classList.add('fv-library-arming'); return; }
            closeMenus(); meta.removeTag(tag.id); if (filter.tag === tag.id) goPlace('all');
        }, 'fv-library-menu-item fv-library-menu-danger');
        removeItem.setAttribute('role', 'menuitem'); removeItem.append(line('trash'), el('span', t('Delete tag', '删除标签')));
        tip(removeItem, t('Delete tag', '删除标签'), t('Remove the tag; the creations stay', '只删除标签，作品保留'));
        tagMenu.append(renameItem, removeItem);
        openMenu(anchor, tagMenu, point);
    }
    // --- Selection ---
    async function pickDay(value, whole) {
        if (deleting) return;
        if (!selecting) setSelecting(true);
        // The day goes on until a card of an earlier day or the end of the list is loaded.
        whole.disabled = true;
        while (next && !disposed && lastDay() === value && sort === 'new' && await load(true));
        if (disposed) return;
        whole.disabled = false;
        const keys = ordered().map(card => card.dataset.key).filter(key => dayOfKey(key) === value);
        const all = keys.every(key => picked.has(key));
        for (const key of keys) all ? picked.delete(key) : picked.add(key);
        anchor = null; syncSelection();
    }
    function pick(key, range) {
        const list = ordered().map(card => card.dataset.key), to = list.indexOf(key), from = list.indexOf(anchor);
        if (range && from >= 0 && to >= 0) {
            const on = !picked.has(key);
            for (const item of list.slice(Math.min(from, to), Math.max(from, to) + 1)) on ? picked.add(item) : picked.delete(item);
        } else picked.has(key) ? picked.delete(key) : picked.add(key);
        anchor = key; confirming = false; syncSelection();
    }
    function wantedSizes() {
        const ids = [...picked].filter(isVideo).map(key => works.get(key)?.id).filter(id => id && !sizes.has(id));
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
    // Only the videos' sizes are known; a selection of pictures alone has no size line.
    const pickedSize = () => {
        let total = 0, any = false;
        for (const key of picked) { if (!isVideo(key)) continue; any = true; const bytes = sizes.get(works.get(key)?.id); if (bytes == null) return null; total += bytes; }
        return any ? total : null;
    };
    const pickedNeedsSize = () => [...picked].some(isVideo);
    function syncSelection(fetchSizes = true) {
        dialog.dataset.selecting = String(selecting);
        choose.textContent = selecting ? t('Cancel', '取消') : t('Select', '选择');
        tip(choose, choose.textContent, selecting ? t('Stop selecting', '退出多选') : t('Pick several creations to act on', '选中多个作品一起操作'), selecting ? 'Esc' : '');
        picking.hidden = bar.hidden = !selecting;
        for (const key of [...picked]) if (!works.has(key) || !cards.get(key)?.isConnected) picked.delete(key);
        for (const [key, card] of cards) {
            card.classList.toggle('fv-picked', selecting && picked.has(key));
            card.setAttribute('aria-pressed', String(selecting ? picked.has(key) : key === selected));
        }
        pickAll.disabled = deleting; pickNone.disabled = deleting || !picked.size;
        for (const heading of grid.querySelectorAll('.fv-library-day')) {
            const keys = ordered().map(card => card.dataset.key).filter(key => dayOfKey(key) === heading.dataset.day);
            const loaded = !(next && heading.dataset.day === lastDay());
            const whole = heading.lastElementChild;
            whole.textContent = loaded && keys.length && keys.every(key => picked.has(key)) ? t('Deselect day', '取消这一天') : t('Select day', '选择这一天');
            tip(whole, whole.textContent, t('Select every creation of this day', '选中这一天的全部作品'));
        }
        if (!selecting) { stageFollowsSelection(); return; }
        const count = picked.size, size = pickedSize();
        pickCount.textContent = t(`${count} selected`, `已选 ${count} 个`);
        drawSummary(size);
        if (fetchSizes) wantedSizes();
        const about = size == null ? '' : ` · ${t('about', '约')} ${gb(size)}`;
        barActions.hidden = false;
        barCancel.hidden = !confirming; barFavorite.hidden = barTag.hidden = barCompare.hidden = confirming;
        barCompare.disabled = count !== 2 || deleting;
        if (count !== 2) barCompare.dataset.fvTipWhy = t('Select exactly two creations', '正好选中两个作品时可用'); else delete barCompare.dataset.fvTipWhy;
        const why = count ? '' : t('Select creations first', '先选择作品');
        for (const control of [barFavorite, barTag, barDelete]) { if (why) control.dataset.fvTipWhy = why; else delete control.dataset.fvTipWhy; }
        if (confirming) {
            barLabel.textContent = t(`Delete ${count} ${count === 1 ? 'creation' : 'creations'}?${size == null ? '' : ` About ${gb(size)}.`}`, `删除 ${count} 个作品？${size == null ? '' : `共约 ${gb(size)}。`}`);
            barText.textContent = t('They can be brought back for 10 seconds.', '10 秒内可以撤销。');
            barDelete.textContent = deleting ? t('Deleting…', '正在删除…') : t('Delete', '删除');
            barCancel.disabled = barDelete.disabled = deleting;
        } else {
            barLabel.textContent = count ? t(`${count} selected`, `已选 ${count} 个`) : t('Select creations on the shelf', '点作品来选择');
            barText.textContent = '';
            barDelete.textContent = count ? t(`Delete ${count}`, `删除 ${count} 个`) + about : t('Delete', '删除');
            barDelete.disabled = deleting || !count;
            barFavorite.disabled = barTag.disabled = deleting || !count;
        }
        barLabel.hidden = !barLabel.textContent; barText.hidden = !barText.textContent;
        stageFollowsSelection();
    }
    // On the stage: two picked compare side by side (or with the slider), three or four in a 2 × 2, more than four the
    // first four; one plays alone; none (or leaving selection) shows the creation that was there.
    let comparedKey = '';
    function stageFollowsSelection() {
        if (layout !== 'stage') return;
        const keys = selecting ? ordered().map(card => card.dataset.key).filter(key => picked.has(key)).slice(0, 4) : [];
        const sig = keys.join('|');
        if (keys.length >= 2) {
            if (sig !== comparedKey) {
                comparedKey = sig; const focused = document.activeElement;
                if (keys.length === 2) openCompare(keys); else openGrid(keys);
                // The thumbnails keep the keyboard (arrows, Space, Esc) while the stage compares.
                if (focused instanceof HTMLElement && collection.contains(focused)) focused.focus({preventScroll: true});
            }
            return;
        }
        if (comparedKey) { comparedKey = ''; viewing = null; viewer.replaceChildren(); delete viewer.dataset.mode; }
        const one = keys[0] || (selected && works.has(selected) ? selected : null);
        if (one && viewing !== one) { viewing = null; select(one, false); }
    }
    function openGrid(keys) {
        const rows = keys.map(key => works.get(key)).filter(Boolean); if (rows.length < 3) return;
        for (const media of viewer.querySelectorAll('video')) { media.pause(); media.removeAttribute('src'); media.load(); }
        viewing = 'compare'; player?.pause(); viewer.replaceChildren(); viewer.dataset.mode = 'grid';
        const stage = el('div', null, 'fv-library-viewer-stage fv-library-compare fv-library-compare-grid');
        for (const row of rows) {
            const cell = el('figure', null, 'fv-library-compare-cell');
            let media;
            if (row.kind === 'image') { media = el('img'); media.alt = ''; media.src = pictureURL(row); }
            else { media = el('video'); media.muted = true; media.loop = true; media.playsInline = true; media.autoplay = !compatView(); media.preload = 'auto'; media.poster = coverOf(workKey(row)) || videoThumbnailURL(row); media.src = view(row.video); }
            cell.append(media, el('figcaption', viewerName(row))); stage.append(cell);
        }
        const top = el('div', null, 'fv-library-viewer-bar');
        const label = el('div', null, 'fv-library-viewer-label'); label.append(el('strong', t(`Comparing ${rows.length}`, `对比 ${rows.length} 个`)));
        const close = button('', () => setSelecting(false), 'fv-library-viewer-button'); close.append(line('close'));
        close.setAttribute('aria-label', t('Stop selecting', '退出多选')); tip(close, t('Stop selecting', '退出多选'), t('Back to one creation', '回到单个作品'), 'Esc');
        top.append(label, close);
        viewer.append(stage, top);
        if (!compatView()) for (const media of stage.querySelectorAll('video')) media.play().catch(() => {});
    }
    function setSelecting(value) {
        if (selecting === value || deleting) return;
        // Selecting keeps the layout: on the stage the thumbnails get their check boxes and the stage compares.
        selecting = value; confirming = false; anchor = null; picked.clear();
        if (value) dialog.dataset.detail = 'false';
        else if (!narrow.matches && !drawer.matches && selected) dialog.dataset.detail = 'true';
        syncSelection(); fold();
    }
    async function selectAll() {
        pickAll.disabled = true;
        if (!needsEverything(filter, sort)) while (next && !disposed && await load(true));
        if (disposed) return;
        for (const card of ordered()) picked.add(card.dataset.key);
        confirming = false; syncSelection();
    }
    async function favoritePicked() {
        const keys = [...picked], value = !keys.every(key => works.get(key)?.favorite);
        barFavorite.disabled = true;
        const files = keys.filter(key => !isVideo(key)).map(key => works.get(key)?.file).filter(Boolean);
        if (files.length) { meta.setPictureFavorite(files, value); for (const key of keys) if (!isVideo(key) && works.get(key)) { works.get(key).favorite = value; syncFavorite(works.get(key)); } }
        let failed = 0;
        for (const key of keys.filter(isVideo)) { try { await saveFavorite(works.get(key), value, true); } catch { failed++; } }
        if (disposed) return;
        barFavorite.disabled = false;
        say(failed ? t(`${failed} could not be changed. Try again.`, `有 ${failed} 个没能更改，请重试。`) : value ? t('Added to Favorites.', '已加入收藏。') : t('Removed from Favorites.', '已移出收藏。'), !!failed);
        render();
    }
    async function confirmDelete() {
        if (!picked.size || deleting) return;
        if (!confirming) {
            confirming = true; syncSelection(); barDelete.focus();
            // The confirmation names the size; wait briefly for it if it has not arrived.
            if (pickedNeedsSize() && pickedSize() == null) {
                for (let wait = 0; wait < 20 && !disposed && pickedSize() == null && confirming; wait++) await new Promise(resolve => setTimeout(resolve, 100));
                if (!disposed) syncSelection(false);
            }
            return;
        }
        await removeSelected();
    }
    let undoNote = null, undoFailure = '';
    // A short line in the dock (a favorite saved for many, a folder that could not open).
    let sayTimer = 0;
    function say(text, warn = false, hold = 4000) {
        clearTimeout(sayTimer);
        undoNote = text; undoBar.dataset.warn = String(warn); showUndo();
        sayTimer = setTimeout(() => { if (undoNote === text) { undoNote = null; showUndo(); } }, hold);
    }
    // Shows what can still be undone: every batch deleted within the undo time, from any
    // dialog, tab or page load, and the pictures this page holds. Without any, only the notes remain.
    function showUndo() {
        clearTimeout(undoTimer);
        if (disposed) return;
        const live = offeredUndoRows(), held = pictureBin.list();
        const deletedCount = live.reduce((sum, row) => sum + row.count, 0) + held.reduce((sum, row) => sum + row.files.length, 0);
        const notes = [undoNote ? el('span', undoNote, 'fv-library-skipped') : null, undoFailure ? el('span', undoFailure, 'fv-library-skipped') : null].filter(Boolean);
        if (undoFailure) notes.push(issueButton(t, () => ({where: ['Delete or restore creations', '删除或恢复作品'], message: undoFailure})));
        const settle = () => { if (undoRows().length || pictureBin.list().length) { showUndo(); return; } undoNote = null; undoFailure = ''; undoBar.replaceChildren(); undoBar.hidden = true; };
        undoBar.replaceChildren(); undoBar.hidden = !deletedCount && !notes.length;
        if (trash) drawTrash();
        syncCounts();
        if (!deletedCount) { undoBar.append(...notes); return; }
        const tokens = live.map(row => row.token), batches = held.map(row => row.batch);
        const due = Math.min(...live.map(row => row.due), ...held.map(row => row.due));
        const ring = el('span', null, 'fv-library-ring'); ring.style.setProperty('--fv-left', String(Math.max(0, Math.min(1, (due - Date.now()) / 10000))));
        ring.style.setProperty('--fv-ms', `${Math.max(0, due - Date.now())}ms`);
        const text = el('span', t(`Deleted ${deletedCount}`, `已删除 ${deletedCount} 个`));
        const undo = button(t('Undo', '撤销'), () => undoAll(tokens, batches, undo), 'fv-quiet');
        tip(undo, t('Undo', '撤销'), t('Bring the deleted creations back', '恢复刚删除的作品'), '⌘Z');
        undoBar.append(ring, text, undo, ...notes);
        undoTimer = setTimeout(settle, due - Date.now() + 50);
    }
    async function undoAll(tokens, batches, control = null) {
        if (control) control.disabled = true;
        clearTimeout(undoTimer);
        let lost = 0, expired = false, back = 0;
        for (const batch of batches) { const files = pictureBin.restore(batch); back += files.length; worksEvent('freevideo-works-restored', files.map(file => file?.file || file), files.map(file => file?.file || file), true); }
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
        for (const batch of settled) { const held = batchContents.get(batch) || []; worksEvent('freevideo-works-restored', held.map(row => row.id), held.map(row => row.video), true); }
        for (const batch of settled) batchContents.delete(batch);
        if (settled.length) window.dispatchEvent(new CustomEvent('freevideo-library-restored'));
        if (disposed) return;
        if (back) render();
        if (again.length) {
            undoFailure = t('Could not undo. Try again.', '没能撤销，请重试。');
            showUndo();
            if (settled.length) load(false);
            return;
        }
        undoFailure = '';
        undoBar.hidden = false;
        undoBar.replaceChildren(el('span', expired ? t('The undo time has passed.', '已过了撤销时间。')
            : lost ? t(`Restored, but ${lost} could not be brought back.`, `已恢复，其中 ${lost} 个没能恢复。`) : t('Restored.', '已恢复。')));
        if (lost && !expired) {
            const text = undoBar.firstElementChild.textContent;
            undoBar.append(issueButton(t, () => ({where: ['Restore creations', '恢复作品'], message: text})));
        }
        undoTimer = setTimeout(() => { if (!undoRows().length && !pictureBin.list().length) { undoBar.replaceChildren(); undoBar.hidden = true; } else showUndo(); }, 4000);
        if (trash) drawTrash();
        syncCounts();
        if (settled.length) load(false);
    }
    // Recently deleted: what can still be brought back, batch by batch, with the time left.
    let trashTimer = 0;
    function drawTrash() {
        clearTimeout(trashTimer);
        if (!trash || disposed) return;
        const live = offeredUndoRows(), held = pictureBin.list(), now = Date.now();
        const blocks = [];
        for (const row of live) blocks.push({kind: 'video', token: row.token, due: row.due, count: row.count, rows: batchContents.get(row.token) || []});
        for (const row of held) blocks.push({kind: 'image', batch: row.batch, due: row.due, count: row.files.length, rows: row.files.map(file => pictureRows.find(item => item.file === file) || pictureRow({file}))});
        blocks.sort((a, b) => b.due - a.due);
        trashView.replaceChildren();
        const head = el('div', null, 'fv-library-trash-head');
        head.append(el('strong', t('Recently deleted', '最近删除')), el('span', t('Deleted creations can be brought back for 10 seconds.', '删除的作品在 10 秒内可以恢复。')));
        trashView.append(head);
        collection.dataset.empty = String(!blocks.length);
        if (!blocks.length) {
            const quiet = el('div', null, 'fv-library-trash-empty'); quiet.append(line('trash'), el('span', t('Nothing to bring back.', '没有可以恢复的作品。')));
            trashView.append(quiet); return;
        }
        for (const block of blocks) {
            const group = el('section', null, 'fv-library-trash-group');
            const left = Math.max(0, Math.ceil((block.due - now) / 1000));
            const top = el('div', null, 'fv-library-trash-top');
            const restore = button('', () => undoAll(block.token ? [block.token] : [], block.batch ? [block.batch] : [], restore), 'fv-quiet fv-library-restore');
            restore.append(line('restore'), el('span', t('Restore', '恢复')));
            tip(restore, t('Restore', '恢复'), t('Put these creations back on the shelf', '把这些作品放回作品库'));
            top.append(el('strong', t(`${block.count} ${block.count === 1 ? 'creation' : 'creations'}`, `${block.count} 个作品`)),
                el('span', t(`${left} s left`, `还剩 ${left} 秒`), 'fv-library-trash-left'), restore);
            const strip = el('div', null, 'fv-library-trash-strip');
            for (const row of block.rows.slice(0, 12)) {
                const cell = el('div', null, 'fv-library-trash-cell');
                const image = el('img'); image.alt = ''; image.src = row.kind === 'image' ? pictureURL(row) : videoThumbnailURL(row); image.onerror = () => { image.hidden = true; };
                cell.append(image); strip.append(cell);
            }
            group.append(top, strip); trashView.append(group);
        }
        trashTimer = setTimeout(drawTrash, 1000);
    }
    const skippedText = skipped => {
        const using = skipped.filter(row => row.reason !== 'foreign-files').length, other = skipped.length - using;
        const folders = (count, en) => count === 1 ? en.replace('folders have', 'folder has') : en;
        if (using && other) return t(`${using + other} kept: ${using} in use, ${other} ${folders(other, 'folders have')} other files.`, `${using + other} 个已保留：${using} 个正在使用，${other} 个文件夹里有别的文件。`);
        if (using) return t(`${using} in use, kept.`, `${using} 个正在使用，已保留。`);
        return t(`${other} ${folders(other, 'folders have')} other files, kept.`, `${other} 个文件夹里有别的文件，已保留。`);
    };
    function leaveShelf(key) {
        const row = works.get(key);
        if (cards.has(key)) originals.unobserve(cards.get(key));
        works.delete(key); if (row?.video) rows.delete(row.video);
        cards.get(key)?.remove(); cards.delete(key);
    }
    // Pictures leave the shelf at once and are deleted when the undo time is over.
    function holdPictures(keys) {
        const files = keys.map(key => works.get(key)?.file).filter(Boolean);
        if (!files.length) return;
        if (selected && keys.includes(selected)) clearDetail();
        pictureBin.hold(files);
        worksEvent('freevideo-works-removed', files, files, true);
        render(); showUndo();
    }
    // Deletes the given creations: pictures are held back for the undo time, videos go through the server's undoable batches.
    async function removeKeys(keys) {
        const videoKeys = keys.filter(isVideo), pictureKeys = keys.filter(key => !isVideo(key));
        const removed = new Set(pictureKeys.filter(key => works.has(key)));
        holdPictures(pictureKeys);
        // A held picture stays known (the shelf hides it while held), so Undo puts its card straight back; it leaves for
        // good only when the engine has deleted it (freevideo-pictures-deleted).
        const skipped = [];
        let failed = false, unsaved = false, why = '';
        if (!videoKeys.length) return {removed, skipped, failed, why};
        const idOf = new Map(videoKeys.map(key => [key, works.get(key)?.id])), ids = videoKeys.map(key => idOf.get(key)).filter(Boolean);
        // Windows keeps a streamed video open; release it before deleting.
        const selectedRow = works.get(selected);
        if (videoKeys.includes(selected)) releasePlayer();
        const earlier = [];
        // A chunk's creations leave the page as soon as the chunk is done, so an undo during a later chunk is not undone by it.
        const leave = (gone, batch) => {
            const left = [];
            for (const key of videoKeys) {
                if (removed.has(key) || !gone.has(idOf.get(key))) continue;
                removed.add(key);
                const row = works.get(key);
                if (batch && row) batchContents.set(batch, [...(batchContents.get(batch) || []), {id: row.id, video: row.video, created_at: row.created_at}]);
                window.dispatchEvent(new CustomEvent('freevideo-library-deleted', {detail: {video: row?.video, id: row?.id}}));
                if (row) left.push(row);
                if (disposed) continue;
                leaveShelf(key);
            }
            worksEvent('freevideo-works-removed', left.map(row => row.id), left.map(row => row.video), Boolean(batch));
            fold();
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
                    if (!response.ok) { why = fileFailureText(await responseFileReason(response), t); forgetUndo([name]); throw new Error('failed'); }
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
                    leave(gone, value.batch);
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
        undoChangedHere();
        syncUndo();
        if (!disposed) {
            if (selected && !works.has(selected)) clearDetail();
            else if (selectedRow && works.has(selected) && !player && selectedRow.kind !== 'image') mountPlayer(selectedRow);
        }
        return {removed, skipped, failed, why};
    }
    async function removeSelected() {
        deleting = true; syncSelection();
        const keys = [...picked];
        const {removed, skipped, failed, why} = await removeKeys(keys);
        deleting = false;
        if (disposed) return;
        const kept = new Set(skipped.map(row => row.id));
        for (const key of keys) if (kept.has(works.get(key)?.id)) picked.delete(key);
        undoNote = skipped.length ? skippedText(skipped) : null;
        if (failed) {
            // What was deleted keeps its Undo; only the part that is still selected is tried again.
            for (const key of removed) picked.delete(key);
            confirming = false;
            undoFailure = removed.size
                ? t(`${picked.size} could not be deleted. Try again.`, `还有 ${picked.size} 个没能删除，请重试。`)
                : why || t('Could not delete. Try again.', '没能删除，请重试。');
            if (!picked.size) setSelecting(false);
        } else { undoFailure = ''; setSelecting(false); }
        render();
        showUndo();
        // The confirm button is gone; keep the keyboard inside the dialog, so Escape still walks back from here: on the
        // bar's Delete while a part that failed is still selected (another try is one press away), on the dialog itself
        // otherwise, as when it opened. (The header's Select button is hidden since selecting starts from the cards.)
        if (!dialog.contains(document.activeElement) || document.activeElement.closest('[hidden]'))
            (selecting && picked.size && !barDelete.hidden ? barDelete : dialog).focus({preventScroll: true});
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
            thumbnails.unobserve(target);
            if (target.dataset.picture) {
                const file = target.dataset.picture;
                // A picture of the image history comes as the engine's small copy; any other is made small here.
                const source = hasPictureThumbnail(file) ? pictureThumbnailURL(file) : `${target.dataset.src}&preview=${encodeURIComponent('jpeg;70')}`;
                // Counted from now: the originals wait for it while it is fetched and drawn.
                mediaQueue().thumbnail(target);
                pictureThumb(file, source, target.dataset.src).then(({url, width, height}) => {
                    const row = works.get('image:' + file) || pictureRows.find(item => item.file === file);
                    if (row && width && !row.size) { row.size = {width, height}; labelCard(workKey(row)); if (selected === workKey(row)) geometry.textContent = pictureDimensions(row); }
                    target.src = url;
                });
            } else { mediaQueue().thumbnail(target); target.src = target.dataset.src; }
            const key = target.closest('.fv-library-card')?.dataset.key;
            if (isVideo(key)) wantPrompt(works.get(key));
        }
    }, {root: collection, rootMargin: '160px'});
    // Over a link, the originals of the cards on screen and near them load in the background after their thumbnails.
    const originals = watchCards(mediaQueue(), collection, card => {
        const row = works.get(card.dataset.key);
        if (!row) return null;
        return row.kind === 'image' ? {url: pictureURL(row), kind: 'image'} : {url: view(row.video), kind: 'video', bytes: row.bytes};
    });
    let serverOffset = null;
    const PICTURE_TIME = /image-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/;
    function restampPictures() {
        for (const row of pictureRows) {
            const made = Number(pictureFacts.get(row.file)?.at);
            if (made > 0) { row.created_at = new Date(made).toISOString(); continue; }
            const m = PICTURE_TIME.exec(row.name || row.file || ''); if (!m || serverOffset === null) continue;
            row.created_at = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) - serverOffset * 60000).toISOString();
        }
    }
    function append(row) {
        if (!row?.video) return;
        if (serverOffset === null) {
            const zone = /(?:([+-])(\d{2}):?(\d{2})|Z)$/.exec(String(row.created_at || ''));
            if (zone) { serverOffset = zone[1] ? (zone[1] === '-' ? -1 : 1) * (Number(zone[2]) * 60 + Number(zone[3])) : 0; restampPictures(); }
        }
        const key = workKey(row);
        const known = works.get(key);
        if (known) { Object.assign(known, row); syncFavorite(known); return; }
        works.set(key, row); rows.set(row.video, row);
    }
    function reset() {
        thumbnails.disconnect(); originals.disconnect(); grid.replaceChildren(); cards.clear(); works.clear(); rows.clear(); unread = 0; unreadable.textContent = '';
        next = null; everything = false; clearDetail(); dialog.dataset.detail = 'false'; drawTail();
    }
    async function loadPictures() {
        try {
            const items = await (await pictures()).history();
            if (disposed) return;
            pictureRows = items.map(item => pictureRow(item, meta.pictureFavorite(item.file)));
            pictureFacts.reload(); restampPictures();
            picturesState = 'ready';
            void pruneThumbs(pictureRows.map(row => row.file));
        } catch { pictureRows = []; picturesState = 'failed'; }
        // Pictures share the shelf's keys with videos; a picture is a work like any other.
        for (const row of pictureRows) works.set(workKey(row), row);
        for (const key of [...works.keys()]) if (!isVideo(key) && !pictureRows.some(row => workKey(row) === key)) works.delete(key);
    }
    let tailState = '';
    function drawTail() {
        const want = trash || !next || (everything && needsEverything(filter, sort)) ? '' : tailState || 'waiting';
        tail.hidden = !want; tail.dataset.state = want;
        if (want === 'loading' && tail.dataset.drawn !== 'loading') {
            tail.replaceChildren(...Array.from({length: layout === 'stage' ? 4 : 6}, () => el('div', null, 'fv-library-ghost')));
        } else if (want === 'failed' && tail.dataset.drawn !== 'failed') {
            const retry = button(t('Retry', '重试'), () => { tailState = ''; void load(true); }, 'fv-library-ghost fv-library-retry');
            tip(retry, t('Retry', '重试'), t('Read the next creations again', '重新读取后面的作品')); tail.replaceChildren(retry); tail.removeAttribute('aria-hidden');
        } else if (want !== 'loading' && want !== 'failed') tail.replaceChildren();
        tail.dataset.drawn = want;
    }
    const tailWatch = new IntersectionObserver(entries => {
        if (entries.some(entry => entry.isIntersecting) && next && !loading && !trash && tailState !== 'failed') void load(true);
    }, {root: collection, rootMargin: '0px 0px 150% 0px'});
    tailWatch.observe(tail);
    async function load(appendPage) {
        if (disposed || (loading && appendPage)) return false;
        if (appendPage) { tailState = 'loading'; tail.hidden = false; drawTail(); }
        listAbort?.abort(); listAbort = new AbortController();
        const round = ++loadRound;
        loading = true; arrivals = []; refresh.disabled = true; let ok = false;
        if (!works.size) message.textContent = '';
        try {
            const [data] = await Promise.all([libraryPage(t, {limit: appendPage && gathering ? 48 : 24, before: appendPage ? next : null,
                signal: listAbort.signal}), appendPage ? null : loadPictures()]);
            if (disposed || round !== loadRound) return false;
            const kept = !appendPage && !fromCache && [...works.keys()].some(isVideo) ? {next, everything} : null;
            if (!appendPage) {
                unread = 0;
                const listed = new Set((data.items || []).filter(row => row?.video).map(workKey));
                const oldest = Math.min(...(data.items || []).map(row => Date.parse(row.created_at)).filter(Number.isFinite), Infinity);
                for (const [key, row] of [...works]) {
                    if (!isVideo(key) || listed.has(key)) continue;
                    if (fromCache || !data.next || (Date.parse(row.created_at) || 0) >= oldest) { leaveShelf(key); if (selected === key) clearDetail(); }
                }
                fromCache = false;
            }
            unread += Number.isSafeInteger(data.unreadable) && data.unreadable > 0 ? data.unreadable : 0;
            unreadable.textContent = unread ? t(`${unread} ${unread === 1 ? 'creation' : 'creations'} could not be read`, `另有 ${unread} 个作品无法读取`) : '';
            for (const row of data.items || []) append(row);
            for (const row of arrivals) append(row);
            // Pages already read below the first stay; their cursor carries on from where it was.
            const below = kept && [...works.values()].some(row => isVideo(workKey(row)) && Date.parse(row.created_at) < Math.min(...(data.items || []).map(item => Date.parse(item.created_at)).filter(Number.isFinite), Infinity));
            if (below) { next = kept.next; everything = kept.everything; } else { next = data.next; everything = !next; }
            ok = true;
            if (selected && !works.has(selected)) clearDetail();
            if (selectedWork) {
                const row = [...works.values()].find(row => row.id === selectedWork || row.video === selectedWork);
                if (row) { selectedWork = null; render(); select(workKey(row)); cards.get(workKey(row))?.scrollIntoView({block: 'nearest'}); }
                else if (next) queueMicrotask(() => load(true));
            }
        } catch (error) {
            if (!disposed && round === loadRound && error.name !== 'AbortError') {
                if (appendPage) tailState = 'failed';
                else if (error.restart) message.textContent = error.message;
                else {
                    fault(message, userMessage(error, t, t('Could not read the creations. Try again.', '没能读取作品，请重试。')), undefined, null, error); retryRead(message);
                    for (const ghost of grid.querySelectorAll(':scope > .fv-library-ghost')) ghost.remove();
                }
            }
        }
        finally {
            if (!disposed && round === loadRound) { loading = false; refresh.disabled = false; if (appendPage && tailState === 'loading') tailState = ''; drawTail(); }
        }
        // Still near the end after a page (a tall window, few creations): read on.
        if (ok && appendPage && next && !disposed && round === loadRound) requestAnimationFrame(() => { const box = tail.getBoundingClientRect(), view = collection.getBoundingClientRect(); if (box.top < view.bottom + view.height * 1.5 && !loading) void load(true); });
        if (ok && !disposed && round === loadRound) {
            if (selected && works.has(selected)) select(selected, false);
            render();
            if (!appendPage) upscaleOffer.forget();
            void upscaleOffer.prepare(works.values());
            if (!appendPage) { gatherIfNeeded(); if (filter.favorites) void gatherFavorites(); }
            if (filter.query) fetchPrompts();
        }
        return ok;
    }
    // Filters and orders other than "newest first" read every creation once; the shelf says so while it does.
    async function gatherIfNeeded() {
        if (gathering || disposed || everything || !next || !needsEverything(filter, sort)) { if (filter.query) fetchPrompts(); return; }
        gathering = true; render();
        try { while (next && !disposed && needsEverything(filter, sort) && await load(true)) { /* next page */ } }
        finally { gathering = false; if (!disposed) render(); }
        if (filter.query) fetchPrompts();
    }
    // The prompts are read only for a search, four at a time, and kept for this opening.
    let promptRound = 0;
    async function fetchPrompts() {
        const round = ++promptRound;
        const wanted = [...works.values()].filter(row => row.kind !== 'image' && row.id && !prompts.has(row.id));
        for (const row of wanted) promptAsked.add(row.id);
        let changed = 0;
        const worker = async () => {
            while (wanted.length && !disposed && round === promptRound) {
                const row = wanted.shift();
                try {
                    const response = await api.fetchApi('/freevideo/library/prompt?' + new URLSearchParams({id: row.id}), {signal: abort.signal});
                    prompts.set(row.id, response.ok ? (await response.json()).prompt || '' : '');
                } catch { prompts.set(row.id, ''); }
                labelCard(workKey(row), sort === 'new' || sort === 'old');
                if (++changed % 12 === 0 && round === promptRound) render();
            }
        };
        await Promise.all([worker(), worker(), worker(), worker()]);
        if (!disposed && round === promptRound && changed) render();
    }
    let searchTimer = 0;
    search.oninput = () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => { filter.query = search.value; if (trash) goPlace(filter.kind); else { render(); gatherIfNeeded(); if (filter.query) fetchPrompts(); } }, 160);
    };
    search.onkeydown = event => { if (event.key === 'Escape' && search.value) { event.preventDefault(); event.stopPropagation(); search.value = ''; search.oninput(); } };
    // --- The big viewer ---
    let viewing = null;
    // The viewer: only the creation, whole (contain, never cropped) on a solid near-black stage. Above it a quiet line
    // (name left; size · length, info and close right), below it at most four actions, the rest under More. Arrows, the
    // line, the actions and the play bar fade after 2 s without the mouse or a key and come back with either.
    let viewerMuted = true, idleTimer = 0, infoOpen = false, menuClosedAt = 0;
    const viewerName = row => {
        const words = String(row.agent?.label || (row.kind === 'image' ? picturePrompt(row) : prompts.get(row.id)) || '').replace(/\s+/g, ' ').trim();
        return words ? words.slice(0, 300) : row.kind === 'image' ? t('Picture', '图片') : t('Video', '视频');
    };
    function wake() {
        if (viewing == null || viewing === 'compare') return;
        viewer.dataset.idle = 'false'; clearTimeout(idleTimer);
        idleTimer = setTimeout(() => { if (viewing != null && viewing !== 'compare' && !contextMenuOpen()) viewer.dataset.idle = 'true'; else if (viewing != null) wake(); }, 2000);
    }
    viewer.addEventListener('pointermove', wake); viewer.addEventListener('pointerdown', wake);
    function viewerButton(name, use, iconNode, run, key = '') {
        const node = button('', run, 'fv-library-viewer-button'); node.append(iconNode);
        node.setAttribute('aria-label', name); tip(node, name, use, key); return node;
    }
    function viewerAction(name, use, iconNode, run) {
        const node = button('', run, 'fv-library-viewer-action'); node.append(iconNode, el('span', name));
        tip(node, name, use); return node;
    }
    // The picture in the viewer zooms and moves (picture_zoom.js); the arrow keys move it while it is zoomed.
    let zoomer = null;
    // The play bar under a stage video: the shared thin bar (glass/player_bar.js), as in the creation panel.
    function playBar(video) {
        const host = document.createDocumentFragment();
        const handle = attachPlayerBar(video, host, {t});
        handle.element.classList.add('fv-library-viewer-play', 'fv-player-on-dark');
        return handle.element;
    }
    // The four actions; everything else of the details and the card menu under More.
    function viewerActions(row, key) {
        const foot = el('div', null, 'fv-library-viewer-actions');
        const more = viewerButton(t('More', '更多'), t('Favorite, share, copy the prompt, delete and more', '收藏、分享、复制提示词、删除等'), icon('more'), () => {
            const box = more.getBoundingClientRect();
            openContextMenu({x: box.left, y: box.top - 8}, viewerMenu(row, key, more), {label: t('More', '更多'), returnFocus: more, onClose: () => { menuClosedAt = performance.now(); }});
        });
        more.classList.add('fv-library-viewer-more');
        if (row.kind === 'image') {
            const download = el('a', null, 'fv-library-viewer-action'); download.href = pictureURL(row); download.download = row.name || '';
            download.append(icon('download'), el('span', t('Download', '下载'))); tip(download, t('Download picture', '下载图片'), t('Save the picture to this computer', '把图片保存到电脑'));
            const text = picturePrompt(row);
            const usePrompt = viewerAction(t('Use prompt', '使用提示词'), t('Put its prompt in the box of the image view', '把它的提示词填回图像视图的描述框'), icon('reuse'), () => usePicturePrompt(text));
            usePrompt.hidden = !text;
            const imageView = viewerAction(t('Open in Images', '在图像视图打开'), t('Use it as a reference or make more like it there', '在那里用作参考图，或接着生成'), line('picture'), () => openInImageView(row));
            foot.append(download, imageView, usePrompt, more);
            return foot;
        }
        const download = el('a', null, 'fv-library-viewer-action'); download.href = outputDownloadURL(api, row.video); download.download = '';
        download.append(icon('download'), el('span', t('Download', '下载'))); tip(download, t('Download video', '下载视频'), t('Save the MP4 to this computer', '把视频文件保存到电脑'));
        const extendButton = viewerAction(t('Continue', '接着生成'), canExtend(row) ? t('Make a longer video from its end', '从结尾继续生成更长的视频') : t('This video cannot be continued', '这条视频不能接着生成'), icon('extend'), () => { closeViewer(); extendSaved(row); });
        extendButton.disabled = !canExtend(row);
        const again = row.preview && secondPassRunner()
            ? viewerAction(t('Run second pass', '继续二采'), t('Finish the full-resolution video', '从预览继续生成全分辨率视频'), icon('reuse'), () => { closeViewer(); secondPass(row); })
            : viewerAction(t('Reuse settings', '复用设置'), row.preview ? t('A first-pass preview: finish it first', '这是一采预览，先继续二采') : t('Fill the creation panel with these settings', '把这次的设置填回创作面板'), icon('reuse'), () => { closeViewer(); reuseSaved(row); });
        if (row.preview && !secondPassRunner()) again.disabled = true;
        foot.append(download, extendButton, again, more);
        return foot;
    }
    function viewerMenu(row, key, anchorNode) {
        const outside = new Set(['open', 'select', 'download', 'reuse', 'extend', 'use-prompt', 'image-view']);
        const items = (cardMenuItems(anchorNode, [key], null) || []).filter(item => typeof item === 'string' || !outside.has(item.id));
        const extra = [{id: 'info', icon: 'info', label: infoOpen ? ['Hide info', '收起信息'] : ['Info', '信息'], use: ['Prompt and run details', '提示词和运行详情'], key: 'I', run: () => toggleInfo()}];
        if (row.kind !== 'image') {
            const share = links.querySelector('.fv-share-trigger'), copy = links.querySelector('.fv-action-copy');
            if (share) extra.unshift({id: 'share', icon: 'globe', label: ['Share', '分享'], use: ['Make a picture or clip to share', '生成可分享的图片或短片'], run: () => share.click()});
            if (copy) extra.push({id: 'copy-path', icon: 'folder', label: ['Copy path', '复制路径'], use: ['Copy where the file is saved', '复制文件保存的位置'], run: () => copy.click()});
        }
        // Deleting from the viewer moves on to the next creation (or closes when none is left).
        const trash = items.find(item => item?.id === 'trash');
        if (trash) { const run = trash.run; trash.run = async () => { const list = ordered().filter(card => !card.hidden).map(card => card.dataset.key), at = list.indexOf(key); const after = list[at + 1] || list[at - 1]; await run(); if (after && works.has(after)) openViewer(after); else closeViewer(); }; }
        const at = items.findIndex(item => item?.id === 'trash');
        items.splice(at < 0 ? items.length : Math.max(0, at - 1), 0, ...extra);
        return items;
    }
    function toggleInfo(value = !infoOpen) {
        infoOpen = value; viewer.dataset.info = String(infoOpen);
        const panel = viewer.querySelector('.fv-library-viewer-panel'); if (panel) panel.hidden = !infoOpen;
        viewer.querySelector('.fv-library-viewer-info')?.setAttribute('aria-pressed', String(infoOpen));
    }
    // Full screen (F): only the creation; the controls fade after 2 s still and come back with the mouse or a key.
    const isFull = () => document.fullscreenElement === viewer || viewer.dataset.full === 'true';
    async function goFull() {
        if (layout !== 'stage' || isFull()) return;
        try { await viewer.requestFullscreen({navigationUI: 'hide'}); } catch { viewer.dataset.full = 'true'; }
        wake();
    }
    function exitFull() {
        if (document.fullscreenElement === viewer) document.exitFullscreen().catch(() => {});
        delete viewer.dataset.full;
    }
    const fullChanged = () => { viewer.dataset.idle = 'false'; if (isFull()) wake(); };
    document.addEventListener('fullscreenchange', fullChanged);
    // The stage: the creation whole (contain), the next one fading in over the last within 120 ms, so it never flashes black.
    let stageWatch = null, settled = null;
    // The thumbnail already drawn for a creation (the rail's card), to stand in on the stage until the creation is in.
    const coverOf = key => { const image = cards.get(key)?.querySelector('.fv-library-picture > img'); return image?.complete && image.naturalWidth > 0 ? image.currentSrc || image.src : ''; };
    const dropLayer = layer => { const gone = layer.querySelector('video'); if (gone) { gone.pause(); gone.removeAttribute('src'); gone.load(); } layer.remove(); };
    function paint(key) {
        let ready = null;
        const row = works.get(key); if (!row) return;
        if (viewing === 'compare') { for (const media of viewer.querySelectorAll('video')) { media.pause(); media.removeAttribute('src'); media.load(); } viewer.replaceChildren(); }
        stageWatch?.disconnect(); stageWatch = null;
        // Only the last fully shown creation fades out; creations clicked past on the way (not yet fully in) go at once,
        // so fast clicks never bring one of them back.
        const old = settled?.isConnected ? settled : null;
        for (const gone of viewer.querySelectorAll('.fv-library-viewer-stage > .fv-layer')) if (gone !== old) dropLayer(gone);
        viewing = key; player?.pause(); releasePlayer();
        delete viewer.dataset.mode; viewer.dataset.kind = row.kind === 'image' ? 'image' : 'video';
        // The bottom row, fixed: name and size · length on the left, the actions and full screen on the right.
        const bottom = el('div', null, 'fv-library-viewer-top');
        const sizeOf = () => row.size?.width ? `${row.size.width} × ${row.size.height}` : '';
        const spec = el('span', row.kind === 'image' ? sizeOf() : dimensions(row), 'fv-library-viewer-spec');
        const info = viewerButton(t('Info', '信息'), t('Prompt and run details', '提示词和运行详情'), line('info'), () => toggleInfo(), 'I');
        info.classList.add('fv-library-viewer-info');
        const full = viewerButton(t('Full screen', '全屏'), t('Only the creation, over the whole screen', '全屏只看作品'), line('expand'), () => isFull() ? exitFull() : goFull(), 'F');
        full.classList.add('fv-library-viewer-full');
        const close = viewerButton(t('Exit full screen', '退出全屏'), t('Back to the library', '回到作品库'), line('close'), () => exitFull(), 'Esc');
        close.classList.add('fv-library-viewer-close');
        const name = el('strong', viewerName(row), 'fv-library-viewer-name'); name.title = name.textContent;
        const words = el('div', null, 'fv-library-viewer-words'); words.append(name, spec);
        bottom.append(words, viewerActions(row, key), info, full, close);
        const main = el('div', null, 'fv-library-viewer-main'), stage = el('div', null, 'fv-library-viewer-stage');
        const layer = el('div', null, 'fv-layer'); layer.dataset.key = key;
        let media;
        const cover = coverOf(key);
        // Until something of the creation is drawn (its thumbnail, its picture or the video's cover), the stage holds a
        // quiet placeholder and its name and actions wait, so they all arrive together.
        viewer.dataset.drawn = cover ? 'true' : 'false';
        const drawn = () => { if (viewing === key) viewer.dataset.drawn = 'true'; };
        if (row.kind === 'image') {
            // The small thumbnail at once, the picture itself fading in over it when it is in.
            if (cover) { const stand = el('img', null, 'fv-cover'); stand.alt = ''; stand.draggable = false; stand.src = cover; layer.append(stand); }
            media = el('img', null, 'fv-full'); media.alt = ''; media.draggable = false; media.decoding = 'async'; media.src = pictureURL(row);
            media.addEventListener('load', () => { media.dataset.loaded = 'true'; drawn(); if (!row.size) row.size = {width: media.naturalWidth, height: media.naturalHeight}; spec.textContent = sizeOf(); });
            // A picture that cannot be read says so where it would be, with the feedback button, as in the details.
            media.addEventListener('error', () => { if (!disposed && viewing === key && layer.isConnected) { layer.replaceChildren(pictureProblem()); drawn(); } }, {once: true});
            if (!cover) media.dataset.loaded = 'true';
            layer.append(media); zoomer = zoomable(stage, media);
            // Opened: it goes before anything loading in the background.
            mediaQueue().open(pictureURL(row), media);
        } else {
            media = el('video'); media.autoplay = !compatView(); media.loop = true; media.playsInline = true; media.muted = viewerMuted; media.preload = 'auto';
            // Its cover shows until the first frame arrives, so the stage is drawn as soon as the small cover is in.
            media.poster = cover || videoThumbnailURL(row); media.src = view(row.video);
            mediaQueue().open(view(row.video), media);
            if (!cover) { const poster = new Image(); poster.onload = drawn; poster.src = media.poster; media.addEventListener('loadeddata', drawn, {once: true}); }
            media.addEventListener('volumechange', () => { viewerMuted = media.muted; });
            media.addEventListener('click', () => { if (media.paused) media.play().catch(() => {}); else media.pause(); });
            // The video and its play bar as one block at the video's displayed width, centred.
            const group = el('div', null, 'fv-library-viewer-group'); group.append(media, playBar(media)); layer.append(group);
            // A video that cannot be opened: the sentence and the feedback button in its place, as in the details.
            media.addEventListener('error', () => {
                if (disposed || viewing !== key || !layer.isConnected) return;
                const note = videoProblem(media);
                media.pause(); media.removeAttribute('src'); media.load(); group.replaceWith(note); drawn();
            }, {once: true});
            const g = row.geometry || {};
            const fit = () => {
                const w0 = media.videoWidth || g.width || 16, h0 = media.videoHeight || g.height || 9, box = stage.getBoundingClientRect();
                const k = Math.max(0, Math.min(box.width / w0, (box.height - 48) / h0));
                group.style.width = `${Math.floor(w0 * k)}px`; media.style.height = `${Math.floor(h0 * k)}px`;
            };
            stageWatch = new ResizeObserver(fit); stageWatch.observe(stage); media.addEventListener('loadedmetadata', fit);
            requestAnimationFrame(fit);
        }
        // The switch starts in this frame: the new creation (its cover until it is in) fades in over 180 ms while the last
        // one fades out and is gone after it; nothing of the previous creation stays behind.
        if (old) { old.classList.add('fv-leaving'); old.querySelector('video')?.pause(); stage.append(old); setTimeout(() => { if (old !== settled) dropLayer(old); }, 220); }
        layer.classList.add('fv-entering'); stage.append(layer);
        void ready;
        requestAnimationFrame(() => {
            layer.classList.remove('fv-entering');
            setTimeout(() => { if (layer.isConnected && !layer.classList.contains('fv-leaving')) settled = layer; }, 200);
        });
        const list = ordered().filter(card => !card.hidden).map(card => card.dataset.key), at = list.indexOf(key);
        const prev = viewerButton(t('Previous', '上一个'), t('Show the creation before', '查看前一个作品'), line('left'), () => step(-1), '←');
        const nextButton = viewerButton(t('Next', '下一个'), t('Show the creation after', '查看后一个作品'), line('right'), () => step(1), '→');
        prev.classList.add('fv-library-viewer-prev'); nextButton.classList.add('fv-library-viewer-next');
        prev.hidden = at <= 0; nextButton.hidden = at < 0 || at >= list.length - 1;
        main.append(stage, prev, nextButton, bottom);
        const panel = el('aside', null, 'fv-library-viewer-panel'); panel.setAttribute('aria-label', t('Info', '信息'));
        panel.append(promptBox, run); run.open = true; panel.hidden = !infoOpen;
        const hadFocus = viewer.contains(document.activeElement);
        viewer.replaceChildren(main, panel); viewer.hidden = false;
        toggleInfo(infoOpen);
        if (hadFocus) (isFull() ? close : full).focus({preventScroll: true});
        if (row.kind !== 'image' && !compatView()) media.play().catch(() => {});
        wake();
    }
    // "View large" and Space: the stage when it shows something else, else full screen.
    function openViewer(key) {
        if (layout !== 'stage') setLayout('stage');
        // A window too narrow for the stage keeps the grid: the creation opens in the details (the drawer, or the whole
        // room on a phone), where the video plays; Esc or All creations goes back to the grid with it still selected.
        // On a phone the details take the room's place, so the keyboard goes with them (to All creations).
        if (layout !== 'stage') { select(key); if (narrow.matches && dialog.dataset.detail === 'true') all.focus({preventScroll: true}); return; }
        if (viewing === key) goFull(); else select(key, false);
    }
    // Two creations: side by side, or one over the other with a slider moved 1:1 by hand or with the arrow keys.
    // Videos play together from one button; a table under them names what differs.
    let compareMode = 'side', compareToggle = null;
    function openCompare(keys, swapped = false) {
        let [a, b] = keys.map(key => works.get(key)); if (!a || !b) return;
        if (swapped) [a, b] = [b, a];
        viewing = 'compare'; player?.pause();
        viewer.replaceChildren(); viewer.dataset.mode = compareMode;
        const top = el('div', null, 'fv-library-viewer-bar');
        const close = button('', () => closeViewer(), 'fv-library-viewer-button'); close.append(line('close'));
        close.setAttribute('aria-label', t('Close', '关闭')); tip(close, t('Close', '关闭'), t('Back to the library', '回到作品库'), 'Esc');
        const modes = el('div', null, 'fv-library-viewer-modes'); modes.setAttribute('role', 'radiogroup'); modes.setAttribute('aria-label', t('Compare as', '对比方式'));
        for (const [key, name, use] of [['side', t('Side by side', '并排'), t('Both at once, next to each other', '两个并排显示')], ['slider', t('Slider', '滑块'), t('One over the other, drag to reveal', '叠在一起，拖动分界线查看')]]) {
            const mode = button(name, () => { compareMode = key; openCompare(keys, swapped); }, 'fv-library-viewer-mode'); mode.setAttribute('role', 'radio'); mode.setAttribute('aria-checked', String(compareMode === key));
            tip(mode, name, use); modes.append(mode);
        }
        const swap = button('', () => openCompare(keys, !swapped), 'fv-library-viewer-button'); swap.append(line('swap'));
        swap.setAttribute('aria-label', t('Swap', '左右互换')); tip(swap, t('Swap', '左右互换'), t('Put the other one first', '交换两边的位置'));
        const media = row => {
            if (row.kind === 'image') { const image = el('img'); image.alt = ''; image.src = pictureURL(row); return image; }
            const video = el('video'); video.muted = true; video.loop = true; video.playsInline = true; video.preload = 'auto'; video.src = view(row.video); return video;
        };
        const stage = el('div', null, 'fv-library-viewer-stage fv-library-compare');
        const left = media(a), right = media(b);
        if (compareMode === 'side') {
            for (const [node, row] of [[left, a], [right, b]]) { const cell = el('figure', null, 'fv-library-compare-cell'); cell.append(node, el('figcaption', workName(row))); stage.append(cell); }
        } else {
            const box = el('div', null, 'fv-library-compare-slider'); box.style.setProperty('--fv-split', '50%');
            right.classList.add('fv-library-compare-over');
            const handle = el('div', null, 'fv-library-compare-handle'); handle.tabIndex = 0; handle.setAttribute('role', 'slider');
            handle.setAttribute('aria-label', t('Divider', '分界线')); handle.setAttribute('aria-valuemin', '0'); handle.setAttribute('aria-valuemax', '100'); handle.setAttribute('aria-valuenow', '50');
            tip(handle, t('Divider', '分界线'), t('Drag, or use the arrow keys', '拖动或用方向键移动'), '← →');
            let split = 50;
            const setSplit = value => { split = Math.max(0, Math.min(100, value)); box.style.setProperty('--fv-split', `${split}%`); handle.setAttribute('aria-valuenow', String(Math.round(split))); };
            const fromPointer = event => { const r = box.getBoundingClientRect(); setSplit((event.clientX - r.left) / r.width * 100); };
            box.onpointerdown = event => { box.setPointerCapture(event.pointerId); fromPointer(event); box.onpointermove = fromPointer; };
            box.onpointerup = box.onpointercancel = event => { box.onpointermove = null; try { box.releasePointerCapture(event.pointerId); } catch { /* Released already. */ } };
            handle.onkeydown = event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); event.stopPropagation(); setSplit(split + (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 10 : 2)); } };
            box.append(left, right, handle); stage.append(box);
        }
        const videos = [left, right].filter(node => node.tagName === 'VIDEO');
        const playBoth = button('', () => { const paused = videos[0]?.paused; for (const video of videos) { if (paused) { video.currentTime = videos[0].currentTime; video.play().catch(() => {}); } else video.pause(); } syncPlay(); }, 'fv-library-viewer-button');
        const syncPlay = () => { const paused = !videos.length || videos[0].paused; playBoth.replaceChildren(line(paused ? 'play' : 'pause')); const name = paused ? t('Play both', '一起播放') : t('Pause both', '一起暂停'); playBoth.setAttribute('aria-label', name); tip(playBoth, name, t('Both videos from the same moment', '两段视频从同一时刻播放'), 'Space'); };
        playBoth.hidden = !videos.length; syncPlay(); compareToggle = videos.length ? () => playBoth.click() : null;
        const label = el('div', null, 'fv-library-viewer-label'); label.append(el('strong', t('Compare', '对比')));
        top.append(modes, swap, playBoth, label, close);
        const facts = el('table', null, 'fv-library-compare-facts');
        const rowsOf = [
            [t('Size', '尺寸'), a.kind === 'image' ? pictureDimensions(a) : dimensions(a), b.kind === 'image' ? pictureDimensions(b) : dimensions(b)],
            [t('Prompt', '提示词'), snippet(prompts.get(a.id)) || '—', snippet(prompts.get(b.id)) || '—']];
        for (const [name, x, y] of rowsOf) {
            const tr = el('tr'); if (x !== y) tr.dataset.differs = 'true';
            tr.append(el('th', name), el('td', x), el('td', y)); facts.append(tr);
        }
        stage.onclick = event => { if (event.target === stage) closeViewer(); };
        viewer.append(stage, facts, top); viewer.hidden = false; dialog.dataset.viewing = 'true';
        if (videos.length && !compatView()) for (const video of videos) video.play().catch(() => {});
        setTimeout(syncPlay, 300);
        close.focus({preventScroll: true});
    }
    function closeViewer() {
        if (viewing == null) return;
        if (isFull()) { exitFull(); return; }
        if (layout === 'stage') {
            if (viewing !== 'compare') return;
            if (selecting) { setSelecting(false); return; }
            viewing = null; viewer.replaceChildren(); delete viewer.dataset.mode;
            const key = selected && works.has(selected) ? selected : null; if (key) paint(key);
            return;
        }
        for (const media of viewer.querySelectorAll('video')) { media.pause(); media.removeAttribute('src'); media.load(); }
        delete viewer.dataset.mode; clearTimeout(idleTimer);
        notice.after(promptBox); promptBox.after(run); run.open = false;
        const key = viewing; viewing = null; viewer.hidden = true; viewer.replaceChildren(); dialog.dataset.viewing = 'false'; viewer.dataset.idle = 'false';
        cards.get(key)?.focus({preventScroll: true});
    }
    function step(by) {
        const list = ordered().filter(card => !card.hidden).map(card => card.dataset.key), at = list.indexOf(viewing);
        const target = list[Math.max(0, Math.min(list.length - 1, at + by))]; if (!target || target === viewing) return;
        select(target, false);
        // The thumbnail follows: focused (when the focus was among them) and scrolled into view.
        const card = cards.get(target);
        if (card) { if (collection.contains(document.activeElement)) card.focus({preventScroll: true}); card.scrollIntoView({block: 'nearest', inline: 'nearest'}); }
    }
    // --- Keys: / searches, ⌘A selects all, Delete deletes the selection, arrows move in the viewer ---
    // Keys reach the room through dialog_keys.js (a view underneath routes them), so every handler is registered there.
    listenDialogKey(dialog, 'keydown', event => {
        // Escape walks back one step: viewer, menu, selection, drawer, then the room itself.
        // The Escape that closed the More menu stops there.
        if (event.key === 'Escape' && (contextMenuOpen() || performance.now() - menuClosedAt < 120)) { event.stopPropagation(); return; }
        if (event.key === 'Escape' && !event.defaultPrevented) { event.preventDefault(); event.stopPropagation(); cancelStep(); return; }
        // Nothing typed in the room reaches the view underneath (its own shortcuts would act on it).
        event.stopPropagation();
        const typing = event.target.closest?.('input,textarea,[contenteditable=true]');
        if (viewing != null && !(typing && viewing !== 'compare')) {
            wake();
            const video = viewer.querySelector('video');
            if (viewing === 'compare') { if (event.key === ' ' && !event.target.closest?.('button,input')) { event.preventDefault(); compareToggle?.(); } return; }
            const across = () => Math.max(1, getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean).length);
            // A zoomed picture: the arrows move it, a step of 80 px; at its own size they step through the works.
            if (event.key.startsWith('Arrow') && zoomer?.zoomed() && !event.target.closest?.('input,textarea,[role=slider]')) {
                event.preventDefault();
                const by = event.shiftKey ? 240 : 80;
                zoomer.pan({ArrowLeft: by, ArrowRight: -by}[event.key] || 0, {ArrowUp: by, ArrowDown: -by}[event.key] || 0);
            }
            else if (event.key === 'ArrowLeft') { event.preventDefault(); step(-1); }
            else if (event.key === 'ArrowRight') { event.preventDefault(); step(1); }
            else if ((event.key === 'ArrowUp' || event.key === 'ArrowDown') && !isFull()) { event.preventDefault(); step((event.key === 'ArrowUp' ? -1 : 1) * across()); }
            else if (event.key.toLowerCase() === 'f' && !event.metaKey && !event.ctrlKey) { event.preventDefault(); if (isFull()) exitFull(); else goFull(); }
            else if (event.key === ' ' && video && !event.target.closest?.('button,input')) { event.preventDefault(); if (video.paused) video.play().catch(() => {}); else video.pause(); }
            else if (event.key.toLowerCase() === 'm' && video) { event.preventDefault(); video.muted = !video.muted; }
            else if (event.key.toLowerCase() === 'i' && !event.metaKey && !event.ctrlKey) { event.preventDefault(); toggleInfo(); }
            return;
        }
        if (typing) return;
        if (event.key === '/' && !event.metaKey && !event.ctrlKey) { event.preventDefault(); search.focus(); }
        else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'a' && !trash) { event.preventDefault(); if (!selecting) setSelecting(true); selectAll(); }
        // Only the Delete key deletes (as the delete button does); Backspace never deletes a work.
        else if (event.key === 'Delete' && selecting && picked.size) { event.preventDefault(); confirmDelete(); }
        else if ((event.metaKey || event.ctrlKey) && !event.shiftKey && event.key.toLowerCase() === 'z' && !undoBar.hidden && undoBar.querySelector('button')) { event.preventDefault(); undoBar.querySelector('button').click(); }
        else if (event.key === ' ' && !selecting && event.target.closest?.('.fv-library-card,.fv-library-detail') && !event.target.closest('video,.fv-library-player,.fv-library-player-big')) { const key = event.target.closest('.fv-library-card')?.dataset.key || selected; if (key) { event.preventDefault(); openViewer(key); } }
        else if (event.key.toLowerCase() === 'm' && player && !event.metaKey && !event.ctrlKey) { event.preventDefault(); player.muted = !player.muted; }
    });
    const completed = event => {
        const row = event.detail?.value;
        if (!row?.video || disposed) return;
        const key = workKey(row);
        const saved = {...row, favorite: row.favorite ?? works.get(key)?.favorite ?? false,
            created_at: row.created_at || works.get(key)?.created_at || new Date().toISOString()};
        if (loading) arrivals.push(saved);
        append(saved);
        render();
        // Do not interrupt playback of a previous video when a new one arrives.
        if (!selected && !narrow.matches) select(key, false);
    };
    window.addEventListener('freevideo-result', completed);
    // A picture finished elsewhere (the image view) joins the shelf on the next read.
    const picturesChanged = () => { if (!disposed) loadPictures().then(() => render()); };
    window.addEventListener('freevideo-image-saved', picturesChanged);
    const picturesGone = event => {
        const gone = new Set(event.detail?.files || []);
        if (disposed || !gone.size) return;
        pictureRows = pictureRows.filter(row => !gone.has(row.file));
        for (const file of gone) { const key = workKey({kind: 'image', file}); if (cards.has(key)) originals.unobserve(cards.get(key)); mediaQueue().forget(view(file, 'input')); works.delete(key); cards.get(key)?.remove(); cards.delete(key); picked.delete(key); }
        render();
    };
    window.addEventListener('freevideo-pictures-deleted', picturesGone);
    dialog.addEventListener('cancel', event => { event.preventDefault(); cancelStep(); });
    function cancelStep() {
        if (tab === 'cast' && castRoom?.cancel()) { /* handled inside the cast shelf */ }
        else if (viewing === 'compare' || isFull() || (viewing != null && layout !== 'stage')) closeViewer();
        else if (menuOpen) { const anchorButton = menuAnchor; closeMenus(); anchorButton?.focus(); }
        else if (selecting && !deleting) setSelecting(false);
        else if (dialog.dataset.detail === 'true' && (narrow.matches || drawer.matches)) all.click();
        else closeDialog(dialog);
    }
    const metaOff = meta.subscribe(() => {
        if (disposed) return;
        for (const row of pictureRows) row.favorite = meta.pictureFavorite(row.file);
        drawSideTagsList(); if (selected) drawDetailTags(selected);
        if (filter.tag && !meta.tag(filter.tag)) filter.tag = null;
        if (filter.tag || filter.favorites) render(); else syncCounts();
    });
    const drawDetailTags = key => {
        tagRow.replaceChildren();
        for (const id of meta.tagsOf(key)) {
            const tag = meta.tag(id); if (!tag) continue;
            const chip = el('span', null, 'fv-library-chip'); chip.dataset.color = tag.color;
            const x = button('', () => { meta.setTags([key], [], [id]); }, 'fv-library-chip-remove'); x.append(line('close'));
            x.setAttribute('aria-label', t(`Remove tag ${tag.name}`, `去掉标签“${tag.name}”`)); tip(x, t('Remove tag', '去掉标签'), t(`Take "${tag.name}" off this creation`, `从这个作品上去掉“${tag.name}”`));
            chip.append(el('span', tag.name), x); tagRow.append(chip);
        }
        const add = button('', () => openTagMenu(add, [key]), 'fv-library-chip fv-library-chip-add');
        add.append(line('tag'), el('span', t('Tag', '标签')));
        tip(add, t('Tag', '标签'), t('Group it with a tag', '给作品加上标签，方便查找'));
        tagRow.append(add);
    };
    function drawSideTagsList() {
        tagList.replaceChildren();
        for (const tag of meta.tags()) {
            const item = button('', () => goPlace('tag:' + tag.id), 'fv-library-place fv-library-tag'); item.dataset.place = 'tag:' + tag.id; item.dataset.tag = tag.id;
            const dot = el('span', null, 'fv-library-dot'); dot.dataset.color = tag.color;
            item.append(dot, el('span', tag.name, 'fv-library-place-name'), el('span', String(meta.count(tag.id) || ''), 'fv-library-count'));
            item.ondblclick = () => renameTag(item, tag);
            item.oncontextmenu = event => { event.preventDefault(); openTagActions(item, tag, {x: event.clientX, y: event.clientY}); };
            tip(item, tag.name, t('Show creations with this tag; right-click to rename or delete', '显示带这个标签的作品；右键可改名或删除'));
            tagList.append(item);
        }
        syncCounts();
    }
    const park = () => {
        // Parked: the room keeps its creations, stage, selection and scroll; listeners keep it current in the background.
        exitFull(); closeMenus(); if (viewing === 'compare') closeViewer();
        for (const media of dialog.querySelectorAll('video')) media.pause();
        parked = {dialog, reopen}; dialog.onclose = park; dialog.inert = true; announceOpen(false);
        if (opener?.isConnected && !opener.closest('[inert]') && (!document.activeElement || document.activeElement === document.body || !document.activeElement.isConnected)) opener.focus({preventScroll: true});
    };
    dialog.onclose = park;
    function reopen(next = {}) {
        dialog.onclose = park; delete dialog.dataset.closing; dialog.inert = false; dialog.dataset.fvReopened = 'true';
        // The closing motion holds its last frame (slightly smaller and lower); a room shown again starts from its own place.
        for (const motion of dialog.getAnimations?.() || []) motion.cancel();
        ({upscale = null, extend = null, reuse = null} = next);
        const label = next.back || t('Back to create', '返回创作');
        back.setAttribute('aria-label', label); back.title = label; const span = back.querySelector('.fv-library-back-label'); if (span) span.textContent = label;
        const where = libraryHost();
        if (!dialog.open) {
            if (where) { dialog.dataset.fvEmbedded = 'true'; if (dialog.parentNode !== where) where.append(dialog); dialog.show(); where.addEventListener('close', hostClosed); }
            else { delete dialog.dataset.fvEmbedded; if (dialog.parentNode !== document.body) document.body.append(dialog); dialog.showModal(); }
        }
        host = where; opened = dialog; dialog.dataset.fvReady = 'true'; announceOpen(true);
        dialog.focus({preventScroll: true});
        if (next.selectedWork) {
            const row = [...works.values()].find(item => item.id === next.selectedWork || item.video === next.selectedWork);
            if (row) { select(workKey(row), false); cards.get(workKey(row))?.scrollIntoView({block: 'nearest'}); } else { selectedWork = next.selectedWork; }
        }
        if (layout === 'stage' && !compatView()) viewer.querySelector('.fv-layer:not(.fv-leaving) video')?.play().catch(() => {});
        load(false); showUndo(); syncUndo();
        return dialog;
    }
    // Ends the room for good (not used by closing; kept for a page that wants the memory back).
    function destroy() {
        headerObserver.disconnect();
        disposed = true; abort.abort(); listAbort?.abort(); thumbnails.disconnect(); originals.disconnect(); releasePlayer(); closeViewer(); closeMenus();
        clearTimeout(undoTimer); clearTimeout(sizeTimer); clearTimeout(syncTimer); clearTimeout(trashTimer); clearTimeout(sayTimer); clearTimeout(searchTimer);
        metaOff(); meta.dispose(); pictureOff(); castRoom?.destroy(); host?.removeEventListener('close', hostClosed);
        window.removeEventListener('freevideo-view-leave', viewLeft); window.removeEventListener('freevideo-view', viewLeft); drawer.removeEventListener('change', drawerChanged);
        window.removeEventListener('freevideo-result', completed); window.removeEventListener('freevideo-image-saved', picturesChanged); window.removeEventListener('freevideo-pictures-deleted', picturesGone);
        window.removeEventListener('storage', undoChanged); window.removeEventListener('freevideo-library-undo', showUndo); window.removeEventListener('freevideo-library-deleted', deletedElsewhere); dialog.remove();
        if (opened === dialog) opened = null;
        if (parked?.dialog === dialog) parked = null;
    }
    void destroy;
    // Another tab undid or added a batch, or this one is opened again within the undo time.
    const undoChanged = event => { if (!event.key || event.key === UNDO_KEY) { showUndo(); syncUndo(); } };
    window.addEventListener('storage', undoChanged); window.addEventListener('freevideo-library-undo', showUndo);
    // A picture whose delete could not be done comes back with a note.
    const pictureOff = pictureBin.subscribe(event => {
        if (disposed) return;
        if (event?.failed?.length) { loadPictures().then(() => { render(); say(t(`${event.failed.length} could not be deleted; they are back.`, `有 ${event.failed.length} 个没能删除，已放回。`), true); }); return; }
        showUndo();
    });
    // A delete that an earlier opening of the dialog is still carrying out takes its creations off this one too.
    const deletedElsewhere = event => {
        const video = event.detail?.video, key = video && keyOfVideo(video);
        if (!key) return;
        leaveShelf(key); fold();
        if (selected === key) { clearDetail(); dialog.dataset.detail = 'false'; }
        picked.delete(key);
    };
    window.addEventListener('freevideo-library-deleted', deletedElsewhere);
    const isPicture = file => typeof file === 'string' && pictureRows.some(row => row.file === file);
    const worksRemoved = event => {
        const files = (event.detail?.paths || []).filter(isPicture);
        if (!files.length) return;
        if (event.detail.undoable === false) {
            pictureRows = pictureRows.filter(row => !files.includes(row.file));
            for (const file of files) { heldElsewhere.delete(file); works.delete('image:' + file); if (selected === 'image:' + file) clearDetail(); }
        } else for (const file of files) if (!pictureBin.holding(file)) heldElsewhere.add(file);
        if (!disposed) render();
    };
    const worksRestored = event => {
        const files = (event.detail?.paths || []).filter(file => heldElsewhere.has(file));
        if (!files.length) return;
        for (const file of files) heldElsewhere.delete(file);
        if (!disposed) render();
    };
    window.addEventListener('freevideo-works-removed', worksRemoved); window.addEventListener('freevideo-works-restored', worksRestored);
    // Leaving the view puts the room's menus and viewer away in the same frame; the room itself stays with its view.
    // On the stage the room stays as it is (a comparison of the selection included); full screen ends.
    const viewLeft = () => { closeMenus(); if (layout === 'stage') exitFull(); else closeViewer(); };
    window.addEventListener('freevideo-view-leave', viewLeft); window.addEventListener('freevideo-view', viewLeft);
    // Below 1180 px the detail floats over the shelf from the right, so the shelf keeps the middle of the window.
    const drawerChanged = () => { setLayout(preferred); if (layout !== 'stage' && !drawer.matches && selected && !narrow.matches && selecting) dialog.dataset.detail = 'true'; };
    drawer.addEventListener('change', drawerChanged);
    clearDetail(); drawSideTagsList(); syncTools(); showTab('works');
    const cache = !selectedWork ? readCache() : null;
    if (cache) {
        fromCache = true;
        for (const row of cache.videos) append(row);
        pictureRows = (cache.pictures || []).filter(row => row?.file).map(row => ({...pictureRow(row, meta.pictureFavorite(row.file)), created_at: row.created_at}));
        for (const row of pictureRows) works.set(workKey(row), row);
    }
    setLayout(preferred);
    if (cache) { render(); if (cache.selected && works.has(cache.selected)) select(cache.selected, false); }
    if (filter.favorites) placeFavorites.setAttribute('aria-current', 'page');
    dialog.tabIndex = -1;
    // Inside a view the room opens under that view's top bar, which stays in place and usable; it closes with the view.
    // Without a view (the node view of an older layout) it covers the window.
    parked = {dialog, reopen};
    // The first opening: the shell holds the current picture while the room draws (stage and first thumbnails, at most
    // 700 ms), then the room fades in whole; without the shell it opens hidden and shows when drawn.
    let drawn = null;
    const drawnNow = new Promise(resolve => { drawn = resolve; });
    const show = () => {
        if (host) { dialog.dataset.fvEmbedded = 'true'; host.append(dialog); dialog.show(); host.addEventListener('close', hostClosed); }
        else { document.body.append(dialog); dialog.showModal(); }
        dialog.focus({preventScroll: true}); load(false); showUndo(); syncUndo(); announceOpen(true);
        // Nothing cached yet: quiet cards hold the shelf while the first list comes.
        if (!works.size) render();
        holdUntilDrawn();
        return dialog;
    };
    const holdWhile = globalThis.FreeVideoShell?.holdWhile;
    if (typeof holdWhile === 'function') holdWhile(show, {ready: drawnNow}); else show();
    function holdUntilDrawn() {
        dialog.dataset.fvReady = 'false'; dialog.dataset.fvHold = 'true';
        const started = performance.now();
        const inView = node => { const a = node.getBoundingClientRect(), b = collection.getBoundingClientRect(); return a.bottom > b.top && a.top < b.bottom && a.width > 0; };
        const check = () => {
            if (disposed || !dialog.open) { delete dialog.dataset.fvHold; drawn(); return; }
            const stageDrawn = layout !== 'stage' || !selected || !!viewer.querySelector('.fv-layer:not(.fv-entering)');
            const firstThumbs = [...grid.querySelectorAll('.fv-library-card .fv-library-picture > img')].filter(inView).slice(0, 8);
            const thumbsDrawn = firstThumbs.length ? firstThumbs.every(image => image.hidden || (image.complete && image.naturalWidth > 0)) : !loading && !works.size;
            if ((stageDrawn && thumbsDrawn) || performance.now() - started > 700) { dialog.dataset.fvReady = 'true'; delete dialog.dataset.fvHold; drawn(); return; }
            requestAnimationFrame(check);
        };
        requestAnimationFrame(check);
    }
}
