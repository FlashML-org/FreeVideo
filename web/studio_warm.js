// Warming the video model ahead of Generate, out of sight: the page asks the engine to load (light, then full) when the
// user shows they may generate soon, and to stop when they leave the creation panel. Nothing shows on screen; the
// progress after Generate simply starts from what is already loaded. No prompt or setting is sent, only the level and the
// preload preference (preload_setting.js), which the engine applies. An engine without these routes is left alone.
//
//   POST /freevideo/video/warm         {level: 'light' | 'full', mode: 'eager' | 'conservative'}
//   POST /freevideo/video/warm-cancel  {}
import { preloadMode } from './preload_setting.js';

const GAP = 2000; // at most one warm request every 2 s

export function createWarm(api, {now = () => performance.now(), later = (fn, ms) => setTimeout(fn, ms), clear = id => clearTimeout(id)} = {}) {
    let missing = false, last = -Infinity, sent = '', pending = '', timer = null, disposed = false;
    async function post(path, body, options = {}) {
        if (missing) return;
        try {
            const reply = await api.fetchApi(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), ...options});
            if (reply?.status === 404 || reply?.status === 405) missing = true;
        } catch { /* Out of sight: a failed warm request changes nothing. */ }
    }
    const rank = level => (level === 'full' ? 2 : level === 'light' ? 1 : 0);
    function flush() {
        timer = null;
        if (disposed || missing || !pending) return;
        const level = pending; pending = '';
        // Nothing new to ask: full already asked, or light again.
        if (rank(level) <= rank(sent)) return;
        last = now(); sent = level;
        void post('/freevideo/video/warm', {level, mode: preloadMode()});
    }
    return {
        // light: a weak sign (a few seconds here, any touch); full: a strong one (typing a prompt, choosing a frame or a
        // length, pointing at Generate).
        warm(level) {
            if (disposed || missing || rank(level) <= rank(sent) || rank(level) <= rank(pending) && timer) return;
            pending = level;
            const wait = last + GAP - now();
            if (wait <= 0) { if (timer) { clear(timer); timer = null; } flush(); }
            else if (!timer) timer = later(flush, wait);
        },
        // Leaving the creation panel (or the page): the engine may unload again; the next visit starts over.
        cancel({keepalive = false} = {}) {
            if (timer) { clear(timer); timer = null; }
            pending = '';
            if (!sent || missing) { sent = ''; return; }
            sent = ''; last = -Infinity;
            void post('/freevideo/video/warm-cancel', {}, keepalive ? {keepalive: true} : {});
        },
        dispose() { this.cancel(); disposed = true; },
        get state() { return {sent, pending, missing}; },
    };
}
