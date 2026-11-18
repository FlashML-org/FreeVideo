// Getting the image model ready before Generate is pressed: the page tells the engine what the user is likely to
// make next, nothing more (the model, how far to go: "light" or "full", whether only free room may be used, and the
// user's preload setting; never the prompt or the pictures). The setting (Settings › Advanced, key
// freevideo.preload) is 'eager' unless set to 'conservative'; the page asks as before either way and the engine
// decides.
// Nothing of it shows on the page; when Generate is pressed the progress simply starts from where the engine got to.
// An engine without the warm call answers 404 once and is not asked again.
import { IMAGE_GEN_PROTOCOL } from './image_generation.js';
import { preloadMode } from './preload_setting.js';

export const WARM_GAP_MS = 2000;

// getToken(): the engine's page key (from its info); a page without one sends nothing.
export function createWarmer({getToken, fetchImpl = (...args) => globalThis.fetch(...args), now = () => Date.now()} = {}) {
    const p = IMAGE_GEN_PROTOCOL;
    let absent = false;
    const last = new Map();
    async function send(action, body, keepalive = false) {
        if (absent) return;
        let token = '';
        try { token = await getToken(); } catch { return; }
        if (!token) return;
        try {
            const response = await fetchImpl(`${p.root}/${action}`, {method: 'POST', keepalive, headers: {'Content-Type': 'application/json', [p.header]: token}, body: JSON.stringify(body)});
            if (response?.status === 404 || response?.status === 405) absent = true;
        } catch { /* a warm that does not arrive costs nothing */ }
    }
    return {
        // At most once per model and level every two seconds; a full warm also stands for a light one.
        // beside: the view was only opened, so the engine loads the model only into free room (never in place of
        // the video models it keeps) and otherwise reads the files ahead.
        warm(model, level = 'light', {beside = false} = {}) {
            if (absent || !model) return false;
            const at = now(), seen = key => at - (last.get(key) ?? -Infinity) < WARM_GAP_MS;
            const key = beside && level === 'full' ? 'beside' : level;
            if (seen(`${model}|${key}`) || (level === 'light' && seen(`${model}|full`))) return false;
            last.set(`${model}|${key}`, at);
            void send('warm', {model, level, mode: preloadMode(), ...(key === 'beside' ? {beside: true} : {})});
            return true;
        },
        // Leaving the view: what was being made ready can stop (sent even as the page goes away).
        cancel(model) { if (absent || !last.size) return; last.clear(); void send('warm-cancel', model ? {model} : {}, true); },
        get absent() { return absent; },
    };
}
