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
// The tiles show the engine's small copy of a picture of the history (work_media.js); any other picture as itself.
export const worksThumbnailURL = file => /^freevideo-image\/(?:image-\d{8}-\d{6}-\d{2}\.(?:png|jpg)|[0-9a-f]{32}\.png)$/.test(file)
    ? api.apiURL('/freevideo/library/picture-thumbnail?' + new URLSearchParams({file})) : worksPictureURL(file);

const announce = (name, files, extra = {}) => {
    if (files.length) window.dispatchEvent(new CustomEvent(name, {detail: {ids: files.slice(), paths: files.slice(), ...extra}}));
};

// The pictures the engine has, newest first by its own clock (it names each file by that clock and sorts by name).
// Pictures waiting out their undo window are left out, as everywhere else.
// Every picture the engine made is a work: a card's views and the editor's pictures too.
export async function listWorks() {
    const rows = await worksClient().history();
    return rows.filter(row => !trash().has(row.file));
}

// One bin for the page, so a picture deleted in one editor and listed in the next stays out of the list.
let bin = null;
const listeners = new Set();
// Pictures the engine would not delete because a character card keeps them: file → the cards' names.
const cardsKeeping = new Map();
function trash() {
    if (bin) return bin;
    bin = createTrash({client: worksClient(), holdMs: UNDO_MS, onChange: (answer, list) => {
        if (answer) {
            for (const f of answer.failed || []) if (f.reason === 'card' && Array.isArray(f.cards)) cardsKeeping.set(f.file, f.cards.filter(Boolean));
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
    window.addEventListener('freevideo-works-removed', removed);
    window.addEventListener('freevideo-works-restored', restored);
    return () => { window.removeEventListener('freevideo-works-removed', removed); window.removeEventListener('freevideo-works-restored', restored); };
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
    if (row.meta?.source) setTimeout(() => remember(row.result.file, {source: row.meta.source, edit: row.meta.edit || 'edit'}), 0);
});

// The hide-list and the draft list that earlier versions kept in this browser are gone: every picture is a work.
try { localStorage.removeItem('freevideo.cast.files'); localStorage.removeItem('freevideo.imageEditor.drafts'); } catch { /* nothing to clear */ }

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
        const named = result.kept.flatMap(file => cardsKeeping.get(file) || []);
        const inCards = result.kept.filter(file => cardsKeeping.has(file)), others = result.kept.length - inCards.length;
        if (!undone && inCards.length) {
            const cards = [...new Set(named)].join(', ');
            toast(cards ? t(`Used by the character card ${cards}. Remove the picture from the card, or delete the card, then delete it here.`, `这张图正被角色卡 ${cards} 使用。先从角色卡中移除这张图，或删除角色卡后再删。`)
                : t('Used by a character card. Remove the picture from the card, or delete the card, then delete it here.', '这张图正被角色卡使用。先从角色卡中移除这张图，或删除角色卡后再删。'), {tone: 'warn'});
            for (const file of inCards) cardsKeeping.delete(file);
        }
        if (!undone && others > 0) toast(t(`${others} could not be deleted.`, `有 ${others} 张没能删除。`), {tone: 'warn'});
    })();
    return handle;
}
