// Thumbnails first, then the works themselves, for pages opened from another device.
//
// Grids show each work's small thumbnail. On a page opened over a link (another computer, a phone, a public link), the
// original videos and pictures then load in the background, one at a time and only after the thumbnails on screen are
// in: those on screen first, then those within a screen of them; what scrolls further away is not loaded. The file the
// user opens goes first: the background stops until that file is in. A new thumbnail, an upload and a generation that
// is being sent or runs stop it too. A stopped file is resumed later from what the browser's cache already holds, and
// a loaded one is taken from that cache by the player and the large picture, so nothing is downloaded twice.
// On this computer (127.0.0.1) every file comes from the disk at once, so nothing is loaded ahead.
// No imports: the queue runs under Node for the unit tests.

const LOOPBACK = /^(?:127(?:\.\d{1,3}){3}|localhost|\[?::1\]?)$/i;
export const isLocalHost = host => LOOPBACK.test(String(host || '')) || /\.localhost$/i.test(String(host || ''));
// For tests and for a check on this computer: 'on' loads ahead here too, 'off' never does.
export const AHEAD_KEY = 'freevideo.mediaAhead';

export function aheadEnabled({host = globalThis.location?.hostname, storage = null} = {}) {
    let forced = null;
    try { forced = (storage ?? globalThis.localStorage)?.getItem(AHEAD_KEY) ?? null; } catch { /* storage blocked */ }
    if (forced === 'on' || forced === 'off') return forced === 'on';
    // A device that asks to save data (a phone's data saver) gets thumbnails only.
    if (globalThis.navigator?.connection?.saveData === true) return false;
    return !isLocalHost(host);
}

// A video is fully in when what is buffered runs from its start to its end.
export function fullyBuffered(video) {
    const ranges = video?.buffered, duration = video?.duration;
    if (!ranges?.length || !Number.isFinite(duration) || duration <= 0) return false;
    return ranges.start(0) <= .1 && ranges.end(ranges.length - 1) >= duration - .1;
}

// most: files loading at once. biggest: a larger file is never loaded ahead. stall: a thumbnail that has not arrived
// after this long no longer holds the background back.
export function createMediaQueue({fetchImpl = (...args) => globalThis.fetch(...args), enabled = true, most = 1,
    biggest = 512 * 1024 ** 2, stall = 8000, settle = 150, setTimer = (fn, ms) => setTimeout(fn, ms),
    clearTimer = id => clearTimeout(id)} = {}) {
    const items = new Map();   // url -> {url, kind, rank, bytes, shown, state: waiting | loading | done | failed, abort}
    const holds = new Map();   // reason -> count
    const thumbs = new Map();  // a thumbnail still loading -> its stall timer
    const listeners = new Set();
    let timer = null, recheck = null, focused = null;

    const held = () => thumbs.size > 0 || [...holds.values()].some(count => count > 0);
    const loading = () => [...items.values()].filter(item => item.state === 'loading');
    const emit = () => { for (const listener of [...listeners]) { try { listener(); } catch { /* a listener's own problem */ } } };
    const schedule = (delay = 0) => { if (timer === null) timer = setTimer(pump, delay); };
    // What is loading stops; the browser keeps what it has and the next request resumes from there.
    function pause() {
        for (const item of loading()) { item.state = 'waiting'; item.abort?.abort(); }
    }
    async function load(item) {
        const controller = new AbortController();
        item.abort = controller; item.state = 'loading';
        try {
            const response = await fetchImpl(item.url, {signal: controller.signal, priority: 'low', credentials: 'same-origin'});
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            // Read to the end and let go of each piece: the file lands in the browser's cache, not in this page.
            const reader = response.body?.getReader?.();
            if (reader) { while (!(await reader.read()).done) { /* only the cache keeps it */ } }
            else await response.arrayBuffer?.();
            if (item.abort === controller) item.state = 'done';
        } catch {
            if (item.abort === controller && !controller.signal.aborted) item.state = 'failed';
        } finally {
            if (item.abort === controller) item.abort = null;
            emit(); schedule();
        }
    }
    function pump() {
        timer = null;
        if (!enabled || held()) return;
        let hidden = false;
        while (loading().length < most) {
            let next = null;
            for (const item of items.values()) {
                if (item.state !== 'waiting' || item.bytes > biggest) continue;
                // A card covered by another view (the image view over the studio) waits until it is shown again.
                if (item.shown?.() === false) { hidden = true; continue; }
                if (!next || item.rank < next.rank) next = item;
            }
            if (!next) break;
            void load(next);
        }
        if (hidden && !loading().length && recheck === null) recheck = setTimer(() => { recheck = null; pump(); }, 2000);
    }
    function hold(reason) {
        holds.set(reason, (holds.get(reason) || 0) + 1);
        pause();
        let released = false;
        return () => {
            if (released) return; released = true;
            const count = (holds.get(reason) || 1) - 1;
            if (count > 0) holds.set(reason, count); else holds.delete(reason);
            schedule(settle);
        };
    }
    const queue = {
        enabled,
        // A file of a work on screen (rank 0 first) or near it; bytes when known.
        // shown(): whether its card can be seen now (false while another view covers it).
        // owner: the grid that wants it (one file can be on two grids at once).
        want(url, {kind = 'video', rank = 0, bytes = null, shown = null, owner = null} = {}) {
            if (!enabled || !url) return;
            let item = items.get(url);
            if (item) { item.rank = rank; if (bytes != null) item.bytes = bytes; if (shown) item.shown = shown; }
            else items.set(url, item = {url, kind, rank, bytes, shown, owners: new Set(), state: 'waiting', abort: null});
            if (owner !== null) item.owners?.add(owner);
            schedule();
        },
        // Scrolled away: not loaded unless it comes back (or another grid still shows it). A loaded file stays known as loaded.
        drop(url, owner = null) {
            const item = items.get(url);
            if (!item || item.state === 'done' || item.state === 'failed') return;
            if (owner !== null) { item.owners?.delete(owner); if (item.owners?.size) return; }
            items.delete(url);
            if (item.state === 'loading') { item.state = 'waiting'; item.abort?.abort(); }
            schedule();
        },
        // A deleted work: nothing of it is loaded or remembered.
        forget(url) {
            const item = items.get(url);
            if (!item) return;
            items.delete(url);
            if (item.state === 'loading') { item.state = 'waiting'; item.abort?.abort(); }
            schedule();
        },
        loaded: url => items.get(url)?.state === 'done',
        state: url => items.get(url)?.state || null,
        // An <img> thumbnail on screen: the background waits until it is in (or has failed, or is late).
        thumbnail(image) {
            if (!enabled || !image || thumbs.has(image) || (image.complete && image.getAttribute?.('src'))) return;
            const done = () => {
                if (!thumbs.has(image)) return;
                clearTimer(thumbs.get(image)); thumbs.delete(image);
                image.removeEventListener?.('load', done); image.removeEventListener?.('error', done);
                schedule(settle);
            };
            thumbs.set(image, setTimer(done, stall));
            image.addEventListener?.('load', done); image.addEventListener?.('error', done);
            pause();
        },
        // The user opened this file in `element` (<img> or <video>): it goes first, the background waits until it is in
        // (a video: buffered to its end, or the browser has stopped loading it for now). Returns a function that ends
        // the wait early (the element was closed).
        open(url, element) {
            focused?.(false);
            if (!enabled || !element) return () => {};
            const release = hold('open');
            const video = element.tagName === 'VIDEO' || 'buffered' in element;
            const events = video ? ['progress', 'suspend', 'canplaythrough', 'error', 'emptied'] : ['load', 'error'];
            let check = null, cap = null;
            const end = complete => {
                if (focused !== end) return;
                focused = null; clearInterval(check); clearTimer(cap);
                for (const type of events) element.removeEventListener?.(type, listen);
                if (complete && url) {
                    const item = items.get(url);
                    if (item) { item.state = 'done'; item.abort?.abort(); item.abort = null; }
                    else items.set(url, {url, kind: video ? 'video' : 'image', rank: 0, bytes: null, state: 'done', abort: null});
                }
                release(); emit();
            };
            const listen = event => {
                if (video) {
                    if (fullyBuffered(element)) end(true);
                    else if (event?.type === 'suspend' || event?.type === 'error' || event?.type === 'emptied') end(false);
                } else end(event?.type === 'load');
            };
            focused = end;
            for (const type of events) element.addEventListener?.(type, listen);
            check = setInterval(() => { if (element.isConnected === false) end(false); }, 1000);
            // However it goes, the background does not wait more than two minutes for one file.
            cap = setTimer(() => end(false), 120000);
            if (video ? fullyBuffered(element) : element.complete && element.naturalWidth > 0) end(true);
            return () => end(false);
        },
        // Uploads and generations: the background waits while the returned function has not been called.
        hold,
        // Generations: holds while any job of the page's list is being sent, waits or runs.
        followJobs(store, active) {
            let release = null;
            return store.subscribe(rows => {
                const busy = rows.some(row => active.has(row.status));
                if (busy && !release) release = hold('generation');
                else if (!busy && release) { release(); release = null; }
            });
        },
        subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
        // For the measurements and tests: what is wanted, loading and in.
        snapshot: () => [...items.values()].map(({url, kind, rank, bytes, state, shown}) => ({url, kind, rank, bytes, state, shown: shown?.() !== false})),
        holding: () => [...holds.keys()].concat(thumbs.size ? ['thumbnails'] : []),
        // The cards of a scrolling grid (watchCards below).
        watch: (root, source, options) => watchCards(queue, root, source, options),
    };
    return queue;
}

// Watches the cards of a scrolling grid for the queue: a card on screen is wanted first (in the grid's order), then a
// card within a screen of it; a card further away is dropped. source(card) gives {url, kind, bytes} or null. The grid
// opened last goes first (Creations opened over the studio, whose rail stays on screen behind it).
let grids = 0;
export function watchCards(queue, root, source, {near = '100%', order = null} = {}) {
    if (!queue.enabled || typeof IntersectionObserver !== 'function') return {observe() {}, unobserve() {}, disconnect() {}};
    const seen = new Set(), close = new Set(), layer = -1e9 * ++grids;
    // Views are modal dialogs stacked in the page: a card outside the top one is covered by another view.
    const shown = card => () => {
        try { const top = [...document.querySelectorAll('dialog:modal')].at(-1); return !top || top.contains(card); } catch { return true; }
    };
    // The grid's own order when given (cards split over day sections), else the card's place among its siblings.
    order ||= card => {
        const parent = card.parentElement;
        return parent ? Array.prototype.indexOf.call(parent.children, card) : 0;
    };
    // The address each card last asked for, so a card that leaves (or no longer names a file) takes it back.
    const asked = new Map();
    const release = card => { const url = asked.get(card); asked.delete(card); if (url) queue.drop(url, layer); };
    const update = card => {
        const found = (seen.has(card) || close.has(card)) && source(card);
        if (!found?.url) { release(card); return; }
        if (asked.get(card) !== found.url) release(card);
        asked.set(card, found.url);
        queue.want(found.url, {kind: found.kind, bytes: found.bytes, rank: layer + (seen.has(card) ? 0 : 1e6) + order(card), shown: shown(card), owner: layer});
    };
    const onScreen = new IntersectionObserver(entries => {
        for (const {target, isIntersecting} of entries) { if (isIntersecting) seen.add(target); else seen.delete(target); update(target); }
    }, {root});
    const nearby = new IntersectionObserver(entries => {
        for (const {target, isIntersecting} of entries) { if (isIntersecting) close.add(target); else close.delete(target); update(target); }
    }, {root, rootMargin: `${near} 0px`});
    return {
        observe(card) { onScreen.observe(card); nearby.observe(card); },
        unobserve(card) {
            onScreen.unobserve(card); nearby.unobserve(card);
            seen.delete(card); close.delete(card); release(card);
        },
        disconnect() {
            for (const card of [...asked.keys()]) release(card);
            onScreen.disconnect(); nearby.disconnect(); seen.clear(); close.clear();
        },
    };
}
