// How the preview while generating is shown: a still sheet of frames (default), or a loop of the video in progress.
// The choice is kept by the server, so the desktop page and the phone page follow the same one. Whether the
// first-time hint has been seen is each browser's own.
const HINT_KEY = 'freevideo.preview-hint-seen';
export const SHEET_FRAMES = 4;
let style = 'sheet', chosen = false, loaded = false, loading = null;
const listeners = new Set();
const notify = () => { for (const listener of [...listeners]) { try { listener(); } catch { /* A listener never blocks the others. */ } } };

export const previewStyle = () => style;
export const previewChosen = () => chosen;
export const previewStyleKnown = () => loaded;
export function onPreviewStyle(listener) { listeners.add(listener); return () => listeners.delete(listener); }

// The value as the server (or the phone's progress poll) gave it; anything else leaves the sheet.
export function applyPreviewStyle(value, wasChosen = chosen) {
    const next = value === 'loop' ? 'loop' : 'sheet';
    const same = loaded && next === style && wasChosen === chosen;
    style = next; chosen = wasChosen === true; loaded = true;
    if (!same) notify();
}

// Reads the server's choice; a page that cannot reach it keeps the default sheet.
export function loadPreviewStyle(api) {
    loading ??= (async () => {
        try {
            const response = await (api ? api.fetchApi('/freevideo/preview-style') : fetch('/freevideo/preview-style'));
            if (response.ok) { const value = await response.json(); applyPreviewStyle(value?.style, value?.chosen === true); }
        } catch { /* The default stays. */ }
        finally { loading = null; }
    })();
    return loading;
}

export function hintSeen() {
    try { return localStorage.getItem(HINT_KEY) === '1'; } catch { return false; }
}
export function markHintSeen() {
    try { localStorage.setItem(HINT_KEY, '1'); } catch { /* Seen only for this page. */ }
    seenHere = true;
}
let seenHere = false;
export const hintWanted = () => loaded && !chosen && !seenHere && !hintSeen();

// Which frames of a strip of `count` the sheet shows: `shown` of them, evenly spaced, the last frame included.
export function sheetIndices(count, shown) {
    const n = Math.max(1, Math.min(SHEET_FRAMES, shown, count));
    if (n === 1) return [count - 1];
    return Array.from({length: n}, (_, i) => Math.round(i * (count - 1) / (n - 1)));
}

// The size of the sheet's frames. `natural` is the height one frame has with room to spare, `room` the height left
// (null when nothing limits it), `width` the width of the column. Frames keep their ratio: when the height gets
// low the sheet shows fewer frames instead of squeezing them, and a sheet wider than the column shrinks as a whole.
export function sheetLayout({count, ratio, natural, room = null, width}) {
    const most = Math.min(SHEET_FRAMES, count);
    let height = natural;
    if (most * height * ratio > width) height = width / (most * ratio);
    if (room !== null && room < height) height = Math.max(24, room);
    let shown = Math.max(1, Math.min(most, Math.floor(width / (height * ratio) + 1e-6)));
    if (shown * height * ratio > width + 1e-6) height = width / (shown * ratio);
    return {shown, height: Math.floor(height), indices: sheetIndices(count, shown)};
}
