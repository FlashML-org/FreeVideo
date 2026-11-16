// The image works seen from the picture editor: the engine's own list of generated pictures (the same list, in the same
// order, as the image view's works and the works library), deleting with the same undo window as the image view, and
// the page events every other surface listens to. One module so the editor never keeps a second idea of the works.
//
// Deleting: a picture leaves at once and is announced (freevideo-works-removed, undoable); for 10 s Undo brings it
// back (freevideo-works-restored); then the engine deletes the file (image-gen history-delete, the call the image view
// uses), its saved card goes too, and the removal is announced again, for good (undoable: false, and
// freevideo-pictures-deleted for the works library). Leaving the page ends the undo window: the delete happens then.
import { createImageGenClient } from './image_generation.js';
import { createMetaStore, createTrash } from './image_view_history.js';
import { removeSavedImages } from './image_library.js';
import { api } from '../../scripts/api.js';

export const UNDO_MS = 10000;
let client = null;
// keepalive: a delete sent as the page goes away (the undo window cut short) still reaches the engine.
export const worksClient = () => client ||= createImageGenClient((url, options = {}) => fetch(url, options.method === 'POST' ? {...options, keepalive: true} : options));
export const worksPictureURL = file => {
    const at = file.lastIndexOf('/');
    return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(at + 1), subfolder: at < 0 ? '' : file.slice(0, at), type: 'input'}));
};

const announce = (name, files, extra = {}) => {
    if (files.length) window.dispatchEvent(new CustomEvent(name, {detail: {ids: files.slice(), paths: files.slice(), ...extra}}));
};

// The pictures the engine has, newest first by its own clock (it names each file by that clock and sorts by name).
// Pictures waiting out their undo window are left out, as everywhere else.
// Pictures drawn for a cast card from a photo are not works either (cast_photo.js keeps their names in this browser).
const castFiles = () => { try { return new Set(JSON.parse(localStorage.getItem('freevideo.cast.files') || '[]')); } catch { return new Set(); } };
export async function listWorks() {
    const rows = await worksClient().history(), cast = castFiles();
    return rows.filter(row => !trash().has(row.file) && !cast.has(row.file));
}

// One bin for the page, so a picture deleted in one editor and listed in the next stays out of the list.
let bin = null;
const listeners = new Set();
function trash() {
    if (bin) return bin;
    bin = createTrash({client: worksClient(), holdMs: UNDO_MS, onChange: (answer, list) => {
        if (answer) {
            const kept = new Set((answer.failed || []).filter(f => f.reason !== 'missing').map(f => f.file));
            const gone = (list || []).filter(f => !kept.has(f));
            if (gone.length) {
                try { createMetaStore().forget(gone); } catch { /* the browser's record of the picture just stays */ }
                void removeSavedImages(api, gone).catch(() => {});
                announce('freevideo-works-removed', gone, {undoable: false});
                window.dispatchEvent(new CustomEvent('freevideo-pictures-deleted', {detail: {files: gone}}));
            }
            // A picture the engine kept (in use) comes back everywhere.
            if (kept.size) announce('freevideo-works-restored', [...kept]);
            for (const fn of [...listeners]) try { fn({gone, kept: [...kept]}); } catch { /* one listener never stops the others */ }
        }
    }});
    addEventListener('pagehide', () => { void bin.flush(); });
    return bin;
}

// Deletes now (undoable for UNDO_MS); the answer is a handle whose undo() brings the pictures back while it can.
export function deleteWorks(files) {
    const list = [...new Set(files.filter(Boolean))];
    if (!list.length) return {files: [], undo() {}, done: Promise.resolve({gone: [], kept: []})};
    const held = trash();
    // The engine's key is fetched now, so a delete sent while the page goes away needs one request only.
    void worksClient().info().catch(() => {});
    held.remove(list.map(file => ({file, name: file.slice(file.lastIndexOf('/') + 1)})));
    announce('freevideo-works-removed', list, {undoable: true});
    let settle; const done = new Promise(resolve => { settle = resolve; });
    const watch = result => { if (list.some(f => result.gone.includes(f) || result.kept.includes(f))) { listeners.delete(watch); settle(result); } };
    listeners.add(watch);
    return {
        files: list, done,
        undo() {
            const back = list.filter(f => held.has(f)); if (!back.length) return [];
            held.restore(back); announce('freevideo-works-restored', back);
            listeners.delete(watch); settle({gone: [], kept: back});
            return back;
        },
    };
}
export const isHeld = file => !!bin?.has(file);

// Keeps a list of works in step with deletes and restores made anywhere on the page. `apply(removed, restored)`.
export function followWorks(apply) {
    const removed = event => apply(event.detail?.paths || event.detail?.ids || [], []);
    const restored = event => apply([], event.detail?.paths || event.detail?.ids || []);
    const cast = event => { if (event.type !== 'storage' || event.key === 'freevideo.cast.files') apply([], []); };
    window.addEventListener('freevideo-works-removed', removed);
    window.addEventListener('freevideo-works-restored', restored);
    window.addEventListener('freevideo-cast-files', cast); window.addEventListener('storage', cast);
    return () => { window.removeEventListener('freevideo-works-removed', removed); window.removeEventListener('freevideo-works-restored', restored);
        window.removeEventListener('freevideo-cast-files', cast); window.removeEventListener('storage', cast); };
}

// Where a picture came from, kept with the browser's record of each work (the image view's info reads the same record):
// the picture an AI edit or a saved edit started from. A job's record is written when the store reports it done, after
// the image view's own record of that job (prompt, model, size), so neither write hides the other.
function remember(file, patch) {
    try { const meta = createMetaStore(); meta.set(file, patch); } catch { /* the work just shows no source */ }
}
if (typeof window !== 'undefined') window.addEventListener('freevideo-job-done', event => {
    const row = event.detail?.job;
    if (row?.kind !== 'image' || row.status !== 'done' || !row.result?.file) return;
    // A picture made in the editor is a draft until the edit is saved: hidden from every works list at once (before
    // any list reads the engine again), deleted when the editor is left without saving.
    if (row.meta?.draft) markDrafts([row.result.file]);
    if (row.meta?.source) setTimeout(() => remember(row.result.file, {source: row.meta.source, edit: row.meta.edit || 'edit'}), 0);
});

// ── Drafts: pictures the editor's AI made that are not works (yet) ──
// The engine writes every picture it makes into the works folder; a draft is kept out of the works lists through the
// shared list of pictures that are not works (the one cast_photo.js keeps for a card's views, which the works library,
// the image view and the works picker all leave out), and deleted when its editor is left without saving it. A draft that
// is part of a saved edit (a layer of an edit the page keeps) stays hidden and is never swept; one no editor took back
// within the time a job is kept is swept.
const DRAFTS_KEY = 'freevideo.imageEditor.drafts', HIDDEN_KEY = 'freevideo.cast.files', DRAFT_KEEP_MS = 30 * 60 * 1000;
const readJSON = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; } catch { return fallback; } };
const writeJSON = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* kept for this page only */ } };
function hide(files, on) {
    const list = new Set(readJSON(HIDDEN_KEY, []));
    for (const file of files) { if (on) list.add(file); else list.delete(file); }
    writeJSON(HIDDEN_KEY, [...list].slice(-2000));
    window.dispatchEvent(new CustomEvent('freevideo-cast-files'));
}
export function markDrafts(files) {
    const list = files.filter(Boolean); if (!list.length) return;
    const drafts = readJSON(DRAFTS_KEY, {});
    for (const file of list) drafts[file] = {...(drafts[file] || {}), at: drafts[file]?.at || Date.now()};
    writeJSON(DRAFTS_KEY, drafts); hide(list, true);
}
export const isDraft = file => !!readJSON(DRAFTS_KEY, {})[file];
// Part of a saved edit that the page keeps: stays out of the works, never swept.
export function keepDrafts(files) {
    const drafts = readJSON(DRAFTS_KEY, {});
    for (const file of files) if (drafts[file]) drafts[file].kept = true;
    writeJSON(DRAFTS_KEY, drafts);
}
// Deletes drafts for good (the engine's delete, no undo: they never were works) with anything filed for them.
export async function discardDrafts(files) {
    const drafts = readJSON(DRAFTS_KEY, {}), list = files.filter(file => drafts[file]);
    if (!list.length) return [];
    for (const file of list) delete drafts[file];
    writeJSON(DRAFTS_KEY, drafts);
    let failed = [];
    try { failed = (await worksClient().historyDeleteMany(list)).failed?.filter(f => f.reason !== 'missing').map(f => f.file) || []; } catch { failed = list; }
    const gone = list.filter(file => !failed.includes(file));
    if (gone.length) {
        void removeSavedImages(api, gone).catch(() => {});
        try { createMetaStore().forget(gone); } catch { /* nothing kept */ }
        announce('freevideo-works-removed', gone, {undoable: false});
        hide(gone, false);
    }
    // One the engine kept (in use) stays a draft and is tried again by the next sweep.
    if (failed.length) { const again = readJSON(DRAFTS_KEY, {}); for (const file of failed) again[file] = {at: 0}; writeJSON(DRAFTS_KEY, again); }
    return gone;
}
// Drafts no editor took back within the time a job is kept, and not part of a saved edit, are deleted.
export function sweepDrafts(now = Date.now()) {
    const drafts = readJSON(DRAFTS_KEY, {});
    const old = Object.entries(drafts).filter(([, d]) => !d.kept && now - (d.at || 0) > DRAFT_KEEP_MS).map(([file]) => file);
    return old.length ? discardDrafts(old) : Promise.resolve([]);
}
if (typeof window !== 'undefined') setTimeout(() => { void sweepDrafts().catch(() => {}); }, 15000);

// An edit saved in the editor joins the works: uploaded, then filed by the engine under its own name and clock, with
// the picture it came from. Null when the engine does not offer it yet (the caller keeps the picture as before).
export async function saveToWorks(blob, {name = 'edit.png', source = null, client = worksClient()} = {}) {
    const info = await client.info();
    if (info?.history_save !== true) return null;
    // The engine takes the upload as it names it (client.upload) and, as the source, a work's name or a content id only.
    const image = await client.upload(new File([blob], name, {type: blob.type || 'image/png'}));
    const known = typeof source === 'string' && (/^freevideo-image\/image-\d{8}-\d{6}-\d{2}\.(?:png|jpg)$/.test(source) || /^[0-9a-f]{64}$/.test(source));
    const answer = await client.historySave({image, ...(known ? {source} : {})});
    const file = answer?.file;
    if (!file) return null;
    remember(file, {source: source || null, edit: 'saved', at: Date.now()});
    window.dispatchEvent(new CustomEvent('freevideo-works-added', {detail: {ids: [file], paths: [file], source}}));
    window.dispatchEvent(new CustomEvent('freevideo-image-saved', {detail: {file, source}}));
    return file;
}

// Deletes with the shared undo notice ("2 pictures deleted · Undo", 10 s); the engine's refusals are said after.
// Returns the handle at once, so a list can drop the pictures before the notice shows.
export function deleteWithUndo(files, t) {
    const handle = deleteWorks(files);
    if (!handle.files.length) return handle;
    void (async () => {
        const {toast} = await import('./notifications.js');
        const count = handle.files.length;
        let undone = false;
        const note = toast(t(count === 1 ? 'Picture deleted' : `${count} pictures deleted`, `已删除 ${count} 张图片`), {ms: UNDO_MS,
            action: {label: t('Undo', '撤销'), use: t('Put the pictures back', '把图片放回去'), run: () => { undone = true; handle.undo(); }}});
        const result = await handle.done;
        note.close();
        if (!undone && result.kept.length) toast(t(`${result.kept.length} could not be deleted.`, `有 ${result.kept.length} 张没能删除。`), {tone: 'warn'});
    })();
    return handle;
}
