// What the library shows that the engine does not keep yet: tags, picture favorites and the order, filters and
// day groups of the shelf. Everything here is plain data so the page and the Node tests share it; the stores keep to
// this browser (localStorage) until the engine offers /freevideo/library/tags and friends, and then only this module
// changes (see SHELL-TASKS.md, "library").

export const TAGS_KEY = 'freevideo.library.tags';
export const PICTURE_FAVORITES_KEY = 'freevideo.library.picture-favorites';
export const VIEW_KEY = 'freevideo.library.view';
export const TAG_COLORS = Object.freeze(['blue', 'green', 'amber', 'red', 'violet', 'grey']);
export const SORTS = Object.freeze(['new', 'old', 'long', 'large', 'bytes']);
export const PERIODS = Object.freeze(['any', 'today', 'week', 'month']);
const PICTURE_NAME = /(?:^|\/)image-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-\d{2}\.(?:png|jpg)$/;

// One key per creation across both kinds; tags and favorites are filed under it.
export const workKey = row => row.kind === 'image' ? 'image:' + row.file : 'video:' + (row.id || row.video);

// A picture from the image history: its time is in its name (image-YYYYMMDD-HHMMSS-NN), older pictures have none.
export function pictureRow(item, favorite = false) {
    const match = PICTURE_NAME.exec(item.file || '');
    const at = match ? new Date(+match[1], +match[2] - 1, +match[3], +match[4], +match[5], +match[6]) : null;
    return {kind: 'image', file: item.file, type: typeof item.type === 'string' ? item.type : undefined, name: item.name || item.file.slice(item.file.lastIndexOf('/') + 1),
        created_at: at && !Number.isNaN(at.getTime()) ? at.toISOString() : null, favorite: !!favorite,
        ...(typeof item.origin === 'string' ? {origin: item.origin} : {}),
        ...(typeof item.prompt === 'string' && item.prompt ? {prompt: item.prompt} : {})};
}

// Where a picture came from, kept by the engine with the picture: the image view, the image editor or a character card.
export const pictureSource = row => row.origin === 'cast' ? 'cast' : row.origin === 'edit' || row.origin === 'editor' ? 'edit' : 'image';

export const seconds = row => {
    const g = row.geometry || {};
    const value = g.seconds || (g.frames && g.frames / (g.fps || 24));
    return Number.isFinite(value) ? value : 0;
};
export const pixels = row => {
    const g = row.kind === 'image' ? row.size || {} : row.geometry || {};
    return (g.width || 0) * (g.height || 0);
};
const time = row => { const value = Date.parse(row.created_at || ''); return Number.isNaN(value) ? 0 : value; };

// The local day a creation belongs to ('' when its time is unknown), the shelf's group heading.
export function dayOf(row) {
    const value = time(row);
    if (!value) return '';
    const day = new Date(value);
    return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`;
}

// filter: {kind: 'all'|'video'|'image', favorites, tag, query, period, preview, agent}; `tagsOf(key)` lists a creation's
// tag ids and `promptOf(row)` its prompt when known. A query matches the prompt, the agent's shot name and the file name.
export function matches(row, filter, {tagsOf = () => [], promptOf = () => '', now = Date.now()} = {}) {
    if (filter.kind === 'video' && row.kind === 'image') return false;
    if (filter.kind === 'image' && row.kind !== 'image') return false;
    if (filter.favorites && !row.favorite) return false;
    if (filter.source && (row.kind !== 'image' || pictureSource(row) !== filter.source)) return false;
    if (filter.tag && !tagsOf(workKey(row)).includes(filter.tag)) return false;
    if (filter.preview && !row.preview) return false;
    if (filter.agent && !row.agent) return false;
    if (filter.period && filter.period !== 'any') {
        const start = new Date(now); start.setHours(0, 0, 0, 0);
        const since = filter.period === 'today' ? start.getTime() : now - (filter.period === 'week' ? 7 : 30) * 864e5;
        if (time(row) < since) return false;
    }
    const query = String(filter.query || '').trim().toLowerCase();
    if (query) {
        const haystack = [promptOf(row), row.agent?.label, row.agent?.project, row.kind === 'image' ? row.name : row.id]
            .filter(Boolean).join('\n').toLowerCase();
        if (!query.split(/\s+/).every(word => haystack.includes(word))) return false;
    }
    return true;
}

// A stable order: ties keep the newest first, so the shelf never shuffles equal cards.
export function sortRows(rows, sort = 'new') {
    const key = {
        new: row => -time(row), old: row => time(row), long: row => -seconds(row),
        large: row => -pixels(row), bytes: row => -(row.bytes || 0),
    }[sort] || (row => -time(row));
    return rows.map((row, index) => ({row, index, key: key(row), at: -time(row)}))
        .sort((a, b) => a.key - b.key || a.at - b.at || a.index - b.index).map(item => item.row);
}

// Videos come a page at a time, newest first; pictures all at once. While older videos are still to come, a picture
// older than the last loaded video waits, so the newest-first shelf never jumps when the next page arrives.
export function inOrderPictures(pictures, videos, moreVideos) {
    if (!moreVideos || !videos.length) return pictures;
    const oldest = Math.min(...videos.map(time));
    return pictures.filter(picture => time(picture) >= oldest);
}

// Filters other than "all, newest first" need every creation: the page loads the rest before it shows them.
export const needsEverything = (filter, sort) => sort !== 'new' || !!(filter.query || filter.tag || filter.preview || filter.agent
    || (filter.period && filter.period !== 'any'));

function readJSON(storage, key, fallback) {
    try { const value = JSON.parse(storage?.getItem(key)); return value ?? fallback; } catch { return fallback; }
}
function writeJSON(storage, key, value) {
    try { storage?.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
}
const cleanName = name => String(name || '').replace(/\s+/g, ' ').trim().slice(0, 40);

// Tags and picture favorites, one store per page; other tabs see the change through the storage event.
export function createMeta(storage = globalThis.localStorage, target = globalThis) {
    const listeners = new Set();
    let tags = [], items = {}, favorites = new Set();
    function load() {
        const value = readJSON(storage, TAGS_KEY, {});
        tags = Array.isArray(value.tags) ? value.tags.filter(tag => tag && typeof tag.id === 'string' && cleanName(tag.name))
            .map(tag => ({id: tag.id, name: cleanName(tag.name), color: TAG_COLORS.includes(tag.color) ? tag.color : 'grey'})) : [];
        const known = new Set(tags.map(tag => tag.id));
        items = {};
        for (const [key, list] of Object.entries(value.items && typeof value.items === 'object' ? value.items : {})) {
            const kept = Array.isArray(list) ? [...new Set(list.filter(id => known.has(id)))] : [];
            if (kept.length) items[key] = kept;
        }
        const stored = readJSON(storage, PICTURE_FAVORITES_KEY, []);
        favorites = new Set(Array.isArray(stored) ? stored.filter(file => typeof file === 'string') : []);
    }
    const emit = () => { for (const fn of listeners) fn(); };
    const save = () => { const ok = writeJSON(storage, TAGS_KEY, {v: 1, tags, items}); emit(); return ok; };
    load();
    const onStorage = event => { if (!event.key || event.key === TAGS_KEY || event.key === PICTURE_FAVORITES_KEY) { load(); emit(); } };
    target?.addEventListener?.('storage', onStorage);
    const newId = () => 't' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    return {
        tags: () => tags.map(tag => ({...tag})),
        tag: id => tags.find(tag => tag.id === id) || null,
        // Returns the tag (an existing one with the same name is reused), or null for an empty name.
        addTag(name, color) {
            const clean = cleanName(name);
            if (!clean) return null;
            const same = tags.find(tag => tag.name.toLowerCase() === clean.toLowerCase());
            if (same) return {...same};
            const tag = {id: newId(), name: clean, color: TAG_COLORS.includes(color) ? color : TAG_COLORS[tags.length % TAG_COLORS.length]};
            tags = [...tags, tag]; save(); return {...tag};
        },
        renameTag(id, name) { const clean = cleanName(name); if (!clean) return false; tags = tags.map(tag => tag.id === id ? {...tag, name: clean} : tag); return save(); },
        removeTag(id) {
            tags = tags.filter(tag => tag.id !== id);
            for (const key of Object.keys(items)) { items[key] = items[key].filter(tagId => tagId !== id); if (!items[key].length) delete items[key]; }
            return save();
        },
        tagsOf: key => items[key] ? [...items[key]] : [],
        count: id => Object.values(items).filter(list => list.includes(id)).length,
        // Adds and removes tags on several creations at once.
        setTags(keys, add = [], remove = []) {
            const known = new Set(tags.map(tag => tag.id));
            for (const key of keys) {
                const next = new Set(items[key] || []);
                for (const id of add) if (known.has(id)) next.add(id);
                for (const id of remove) next.delete(id);
                if (next.size) items[key] = [...next]; else delete items[key];
            }
            return save();
        },
        // A deleted creation leaves its tags behind only until it is gone for good.
        forget(keys) { for (const key of keys) delete items[key]; return save(); },
        pictureFavorite: file => favorites.has(file),
        setPictureFavorite(files, value) {
            for (const file of files) value ? favorites.add(file) : favorites.delete(file);
            const ok = writeJSON(storage, PICTURE_FAVORITES_KEY, [...favorites]); emit(); return ok;
        },
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
        dispose() { listeners.clear(); target?.removeEventListener?.('storage', onStorage); },
    };
}

// The shelf's own choices (category, sort, density, sidebar) are remembered for the next opening.
export function readView(storage = globalThis.localStorage) {
    const value = readJSON(storage, VIEW_KEY, {});
    return {kind: ['all', 'video', 'image'].includes(value.kind) ? value.kind : 'all', sort: SORTS.includes(value.sort) ? value.sort : 'new',
        dense: value.dense === true};
}
export const saveView = (view, storage = globalThis.localStorage) => writeJSON(storage, VIEW_KEY, view);

// Pictures are deleted for good by the engine. The page holds a delete back for the undo time instead: the
// picture leaves the shelf at once and the files go only when the time is over, or at once when the page goes away
// (`flush`). Undo within the time never reaches the engine. `commit(files)` returns the files that could not be deleted.
// send(files) → Promise of {token, failed} or null: an engine that keeps deleted pictures itself takes them at once
// (and deletes them after its own undo time, even with the page closed); undo(token) asks a batch back. Without them, or
// when send answers null, the files are committed when the time is over, as before.
export function createPictureBin({commit, send = null, undo = null, seconds = 10, clock = {now: () => Date.now(), set: (fn, ms) => setTimeout(fn, ms), clear: id => clearTimeout(id)}} = {}) {
    const held = new Map(); // batch → {files, due, timer, sent}
    const listeners = new Set();
    const emit = () => { for (const fn of listeners) fn(); };
    let serial = 0;
    async function settle(batch) {
        const entry = held.get(batch);
        if (!entry) return [];
        held.delete(batch); clock.clear(entry.timer); emit();
        let failed = [];
        // commit(files, sent): with `sent`, the engine already has them and only the page's own follow-up is left.
        const sent = await (entry.sent || Promise.resolve(null)).catch(() => null);
        try { failed = (await commit(entry.files, sent)) || []; } catch { failed = sent ? (sent.failed || []) : entry.files; }
        if (failed.length) for (const fn of listeners) fn({failed, batch});
        return failed;
    }
    return {
        hold(files) {
            const batch = 'p' + (++serial), due = clock.now() + seconds * 1000;
            const sent = send ? Promise.resolve().then(() => send([...files])).catch(() => null) : null;
            held.set(batch, {files: [...files], due, timer: clock.set(() => settle(batch), seconds * 1000), sent});
            emit(); return batch;
        },
        // Undo: the batch's files come back untouched (from the engine's bin too, when it took them).
        restore(batch) {
            const entry = held.get(batch); if (!entry) return [];
            clock.clear(entry.timer); held.delete(batch); emit();
            if (entry.sent && undo) void entry.sent.then(sent => sent?.token ? undo(sent.token) : null).catch(() => {});
            return entry.files;
        },
        settle,
        flush() { return Promise.all([...held.keys()].map(settle)); },
        list: () => [...held.entries()].map(([batch, entry]) => ({batch, files: [...entry.files], due: entry.due})),
        holding: file => [...held.values()].some(entry => entry.files.includes(file)),
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    };
}
