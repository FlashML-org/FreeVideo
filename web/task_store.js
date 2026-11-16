// One list of generation jobs for the whole page, apart from every view. A view starts a job and watches it;
// closing the view only stops watching. Only an explicit cancel stops a job; the engine keeps running it otherwise.
//
// Rows follow the shape of the planned GET /freevideo/jobs: id, kind, origin, status, phase, percent (0-1),
// eta_seconds, created_at, started_at, finished_at, prompt, result, failure, cancellable, position, model.
// Three local fields ride along and are kept in this browser only: view (where to go back to), meta, seen.
//
// That endpoint does not exist yet, so the rows come from sources that read today's interfaces: the picture
// worker's status (imageSource), the progress stream the page already receives (progressSource) and the list
// of long videos (chainSource). Moving to /freevideo/jobs replaces those three and nothing else.
// No imports: the store and its sources run under Node for the unit tests.

export const LOCAL_KEY = 'freevideo.jobs.local';
export const IMAGE_KEY = 'freevideo.jobs.image';
const LOCAL_LIMIT = 200, LOCAL_AGE_MS = 7 * 24 * 3600e3;
export const ACTIVE = new Set(['submitting', 'queued', 'running', 'cancelling']);
export const FINAL = new Set(['done', 'failed', 'cancelled', 'interrupted']);
const RANK = {running: 0, cancelling: 0, submitting: 1, queued: 2};
const finite = value => typeof value === 'number' && Number.isFinite(value);
const clamp01 = value => Math.min(1, Math.max(0, value));

function readJSON(storage, key) {
    try { const value = JSON.parse(storage?.getItem(key) || 'null'); return value && typeof value === 'object' ? value : {}; } catch { return {}; }
}
function writeJSON(storage, key, value) {
    try { storage?.setItem(key, JSON.stringify(value)); } catch { /* Private windows and full storage keep the list in memory only. */ }
}

export function createJobStore({now = () => Date.now(), storage = null} = {}) {
    const rows = new Map(), subscribers = new Set(), events = {done: new Set(), failed: new Set(), change: new Set()};
    const sources = new Map();
    let snapshot = Object.freeze([]);
    // Browser storage keeps no text of a job (privacy: a prompt belongs to its work only): a local record is only where
    // the job came from, whether it was seen, and when. Records written before this rule lose anything else on load.
    const LOCAL_FIELDS = ['view', 'seen', 'at'];
    const plain = record => Object.fromEntries(LOCAL_FIELDS.filter(key => record?.[key] !== undefined).map(key => [key, record[key]]));
    let local = readJSON(storage, LOCAL_KEY);
    const loaded = JSON.stringify(local);
    local = Object.fromEntries(Object.entries(local).map(([id, record]) => [id, plain(record)]));
    // Old or surplus local records go; the newest 200 within a week stay.
    const prune = () => {
        const cutoff = now() - LOCAL_AGE_MS;
        const kept = Object.entries(local).filter(([, value]) => (value?.at || 0) >= cutoff).sort((a, b) => b[1].at - a[1].at).slice(0, LOCAL_LIMIT);
        local = Object.fromEntries(kept);
    };
    prune();
    const saveLocal = () => { prune(); writeJSON(storage, LOCAL_KEY, local); };
    if (JSON.stringify(local) !== loaded && Object.keys(local).length + loaded.length > 2) saveLocal();
    // Text of a job (its prompt) lives in memory only, and only until the job ends; listeners of done and failed still
    // receive it once (the library files the prompt with the work), then the row keeps no text.
    const withoutText = row => { const {prompt, ...rest} = row; const {text, ...meta} = row.meta || {}; return {...rest, prompt: '', meta}; };

    // Running first, then submitting and queued (oldest first, as they will run), then the latest finished.
    const order = (a, b) => {
        const ra = ACTIVE.has(a.status) ? RANK[a.status] : 3, rb = ACTIVE.has(b.status) ? RANK[b.status] : 3;
        if (ra !== rb) return ra - rb;
        if (ra === 3) return (b.finished_at || 0) - (a.finished_at || 0);
        return ra === 2 ? (a.created_at || 0) - (b.created_at || 0) : (b.created_at || 0) - (a.created_at || 0);
    };
    const matches = (row, filter) => !filter || Object.entries(filter).every(([key, value]) => value == null || row[key] === value);
    function publish(change) {
        snapshot = Object.freeze([...rows.values()].sort(order));
        for (const entry of [...subscribers]) {
            if (!subscribers.has(entry)) continue;
            if (change && entry.filter && !matches(change.row || {}, entry.filter) && !matches(change.previous || {}, entry.filter)) continue;
            try { entry.fn(snapshot.filter(row => matches(row, entry.filter)), change); } catch (error) { console.warn('FreeVideo jobs subscriber:', error); }
        }
        for (const fn of [...events.change]) try { fn(change); } catch { /* One listener never stops the others. */ }
    }
    function fire(name, row) { for (const fn of [...events[name]]) try { fn(row); } catch (error) { console.warn(`FreeVideo jobs ${name}:`, error); } }

    // Merges what a source knows into the row. A finished row stays finished unless the source says it runs
    // again (a long video continued after a stop); a late progress message cannot revive it.
    // force: the store itself puts a row back (a cancel the engine refused).
    function upsert(patch, {revive = false, force = false} = {}) {
        if (!patch?.id) throw new Error('job_id_required');
        const previous = rows.get(patch.id);
        if (previous && FINAL.has(previous.status) && patch.status && ACTIVE.has(patch.status) && !revive) return previous;
        // A stop on its way: reports sent before the engine took it do not bring the row back to running.
        if (previous?.status === 'cancelling' && ['running', 'queued'].includes(patch.status) && !revive && !force) patch = {...patch, status: 'cancelling'};
        const mine = local[patch.id] || {};
        const at = now();
        const row = {cancellable: true, origin: 'page', ...previous, ...Object.fromEntries(Object.entries(patch).filter(([, value]) => value !== undefined)),
            view: patch.view ?? mine.view ?? previous?.view ?? null, meta: {...(previous?.meta || {}), ...(patch.meta || {})},
            seen: mine.seen ?? previous?.seen ?? false};
        row.created_at ??= at;
        if (finite(row.percent)) row.percent = clamp01(row.percent);
        if (row.status === 'running' && !row.started_at) row.started_at = at;
        if (FINAL.has(row.status)) { row.finished_at ??= at; row.cancellable = false; } else if (ACTIVE.has(row.status)) delete row.finished_at;
        // Progress never goes backwards within one run; a new run (revive) starts again.
        if (previous && !revive && finite(previous.percent) && finite(row.percent) && row.status === previous.status && row.percent < previous.percent) row.percent = previous.percent;
        const ended = FINAL.has(row.status) && !FINAL.has(previous?.status);
        rows.set(row.id, Object.freeze(FINAL.has(row.status) ? withoutText(row) : row));
        if (patch.view != null) { local[row.id] = plain({...mine, at, view: patch.view}); saveLocal(); }
        const frozen = rows.get(row.id);
        publish({type: previous ? 'update' : 'add', row: frozen, previous});
        if (ended && row.status === 'done') fire('done', Object.freeze({...row}));
        if (ended && row.status === 'failed') fire('failed', Object.freeze({...row}));
        return frozen;
    }
    // Only this browser's fields: where the job came from, what to show for it, whether it was seen.
    function setLocal(id, patch) {
        const mine = plain({...(local[id] || {}), ...patch, at: now()}); local[id] = mine; saveLocal();
        const row = rows.get(id);
        if (row) { const next = Object.freeze({...row, ...('view' in patch ? {view: patch.view} : {}), ...('seen' in patch ? {seen: patch.seen} : {}), ...('meta' in patch ? {meta: {...row.meta, ...patch.meta}} : {})}); rows.set(id, next); publish({type: 'update', row: next, previous: row}); }
    }
    const sourceOf = row => sources.get(row?.source) || sources.get(row?.kind);
    const hiddenWorks = new Map();
    const baseName = value => String(value).split(/[\\/]/).pop();
    const workKeys = (ids, paths) => new Set([...ids, ...paths].filter(value => value != null && value !== '').flatMap(value => [String(value), baseName(value)]));
    const refersTo = (row, keys) => [row.result?.file, row.result?.video, row.meta?.file, row.meta?.work_id].filter(Boolean)
        .some(value => keys.has(String(value)) || keys.has(baseName(value)));

    return {
        subscribe(fn, filter = null) {
            const entry = {fn, filter}; subscribers.add(entry);
            try { fn(snapshot.filter(row => matches(row, filter)), null); } catch (error) { console.warn('FreeVideo jobs subscriber:', error); }
            return () => subscribers.delete(entry);
        },
        list: () => snapshot,
        get: id => rows.get(id) || null,
        active: filter => snapshot.filter(row => ACTIVE.has(row.status) && matches(row, filter)),
        on(name, fn) { events[name].add(fn); return () => events[name].delete(fn); },
        // Starts a job through the source that owns its kind; the row appears before the engine answers.
        async submit(kind, request, options = {}) {
            const source = sources.get(kind);
            if (!source?.submit) throw Object.assign(new Error('no_source'), {code: 'no_source'});
            return source.submit(request, options);
        },
        // The user asked to stop it (and confirmed). Nothing else calls this.
        async cancel(id) {
            const row = rows.get(id);
            if (!row || !ACTIVE.has(row.status)) return false;
            const source = sourceOf(row);
            if (!source?.cancel) return false;
            upsert({id, status: 'cancelling', cancellable: false});
            try { await source.cancel(row); return true; }
            catch (error) { upsert({id, status: row.status, cancellable: true}, {force: true}); throw error; }
        },
        // Takes a finished row off the floating window here; the job and its result stay where they are.
        dismiss(id) {
            const row = rows.get(id); if (!row || ACTIVE.has(row.status)) return;
            rows.delete(id); setLocal(id, {seen: true}); publish({type: 'remove', previous: row});
        },
        // A source takes back a row that never became a job (the worker was full).
        drop(id) { const row = rows.get(id); if (row) { rows.delete(id); publish({type: 'remove', previous: row}); } },
        // Works deleted in the library or the image view: the finished rows that point at them leave the list (and so the
        // floating window and anything listening to the store); a restore puts them back. Running jobs are not touched.
        hideWorks({ids = [], paths = []} = {}) {
            const keys = workKeys(ids, paths); if (!keys.size) return [];
            const gone = [...rows.values()].filter(row => row.status === 'done' && refersTo(row, keys));
            for (const row of gone) { hiddenWorks.set(row.id, row); rows.delete(row.id); publish({type: 'remove', reason: 'works-removed', previous: row}); }
            return gone.map(row => row.id);
        },
        showWorks({ids = [], paths = []} = {}) {
            const keys = workKeys(ids, paths); if (!keys.size) return [];
            const back = [...hiddenWorks.values()].filter(row => refersTo(row, keys) && !rows.has(row.id));
            for (const row of back) { hiddenWorks.delete(row.id); rows.set(row.id, row); publish({type: 'add', reason: 'works-restored', row}); }
            return back.map(row => row.id);
        },
        markSeen(id) { if (rows.get(id) && !rows.get(id).seen) setLocal(id, {seen: true}); },
        claim(id, view) { if ((rows.get(id)?.view ?? local[id]?.view) !== view) setLocal(id, {view}); },
        localOf: id => local[id] || null,
        async refresh() { await Promise.all([...new Set(sources.values())].map(source => source.refresh?.().catch?.(() => {}))); },
        // For sources only.
        upsert, addSource(name, source) { sources.set(name, source); return () => { if (sources.get(name) === source) sources.delete(name); }; },
        now,
    };
}

// ---------- Picture jobs: the worker's own status, polled from here so no view holds the loop ----------
// The secret job key the worker hands out stays in this browser (IMAGE_KEY), never in a row: the row id is our own.
const IMAGE_STATES = {waiting: 'queued', queued: 'running', running: 'running', done: 'done'};
const defaultImageProgress = (stage, fraction, withImages, overall) => finite(overall) ? Math.min(.99, overall) : finite(fraction) ? Math.min(.99, fraction) : 0;
// The worker forgot the job (it restarted): the run is over and its picture, if any, is in the history.
const GONE = new Set(['unknown_job', 'closed']);
// The worker runs one picture and holds one more; a third is refused. That is a state of the worker, not a failed job.
export const IMAGE_BUSY = new Set(['busy', 'busy_video']);

export function imageSource(store, {client, storage = null, poll = 500, now = store.now, sleep = ms => new Promise(done => setTimeout(done, ms)),
    progress = defaultImageProgress, objectURL = blob => URL.createObjectURL(blob), revokeURL = url => URL.revokeObjectURL(url),
    outageLimit = 8, newId = () => 'img-' + now().toString(36) + '-' + Math.random().toString(36).slice(2, 8)} = {}) {
    // The resume list holds the worker's job key, the model and the view: no prompt (old entries lose theirs on load).
    const keys = readJSON(storage, IMAGE_KEY), tracking = new Set();
    if (Object.values(keys).some(entry => entry && 'prompt' in entry)) { for (const entry of Object.values(keys)) if (entry) delete entry.prompt; writeJSON(storage, IMAGE_KEY, keys); }
    // A page may hand its own client for its jobs; jobs found after a reload use the default. Only a client that says
    // standIn: true (a stand-in engine) keeps its pictures out of the library.
    const clients = new Map(), clientOf = id => clients.get(id) || client;
    const save = () => writeJSON(storage, IMAGE_KEY, keys);
    const forget = id => { delete keys[id]; save(); };

    async function track(id) {
        if (tracking.has(id)) return; tracking.add(id);
        let serial = 0, outages = 0, preview = store.get(id)?.meta?.preview || '';
        try {
            for (;;) {
                const entry = keys[id], row = store.get(id);
                if (!entry || !row || FINAL.has(row.status)) return;
                await sleep(poll);
                let status;
                try { status = await clientOf(id).status(entry.job, entry.model); outages = 0; }
                catch (error) {
                    if (GONE.has(error?.code)) {
                        store.upsert({id, status: 'interrupted', failure: {code: 'engine_restarted'}}); forget(id); return;
                    }
                    // The engine is unreachable: a few misses are a reset, a run of them is a closed engine.
                    if (++outages >= outageLimit) { store.upsert({id, status: 'failed', failure: {code: 'engine_closed'}}); forget(id); return; }
                    await sleep(Math.min(8000, poll * 2 ** outages)); continue;
                }
                const state = status.state === 'error' ? (status.error === 'cancelled' ? 'cancelled' : 'failed') : IMAGE_STATES[status.state] || 'running';
                const percent = state === 'done' ? 1 : progress(status.stage, status.fraction, entry.withImages, status.overall);
                const patch = {id, status: status.phase === 'cancelling' ? 'cancelling' : state, phase: status.stage || (state === 'queued' ? 'waiting' : ''),
                    percent, eta_seconds: finite(status.remaining_seconds) ? status.remaining_seconds : undefined,
                    position: state === 'queued' ? 1 : 0, meta: {report_id: status.report_id || row.meta?.report_id || null}};
                const next = status.state === 'running' ? status.preview?.serial : undefined;
                if (Number.isInteger(next) && next > serial && clientOf(id).previewImage) {
                    serial = next;
                    const blob = await clientOf(id).previewImage(entry.job).catch(() => null);
                    if (blob) { const url = objectURL(blob); if (preview) revokeURL(preview); preview = url; patch.meta.preview = url; }
                }
                if (state === 'done') patch.result = {file: status.file};
                // The size the engine fell back to (too little memory), and what a queued picture waits for (image | video).
                if (status.fitted) patch.meta.fitted = status.fitted;
                if (state === 'queued' && status.waiting) patch.meta.waiting = status.waiting;
                if (state === 'failed') patch.failure = {code: status.error || 'generation_failed', report_id: status.report_id || null, stage: status.stage,
                    fitted: status.fitted || null, memory: status.memory, other_usage: status.other_usage, message_zh: status.message_zh};
                store.upsert(patch);
                if (FINAL.has(state)) { forget(id); clients.delete(id); return; }
            }
        } finally { tracking.delete(id); }
    }

    const source = {
        // request: what client.generate takes; options.meta.withImages says whether reference pictures went along.
        // A refusal because the worker is full drops the row and rejects with that code; the page says so in place.
        async submit(request, {view = 'image', meta = {}, client: own = null} = {}) {
            const id = newId(), mine = own || client;
            if (own && own !== client) clients.set(id, own);
            store.upsert({id, kind: 'image', source: 'image', status: 'submitting', prompt: meta.text ?? request.prompt ?? '', model: request.model || null,
                view, meta: {...meta, ...(own?.standIn === true ? {standIn: true} : {})}, percent: 0});
            const refuse = (code, value) => {
                clients.delete(id);
                if (IMAGE_BUSY.has(code)) store.drop(id); else store.upsert({id, status: 'failed', failure: {code, ...pick(value)}});
                return Object.assign(value instanceof Error ? value : new Error(code), {code, ...(value instanceof Error ? {} : value), jobId: id});
            };
            let reply;
            try { reply = await mine.generate(request); }
            catch (error) { throw refuse(error?.code || 'generation_failed', error); }
            if (!reply?.job) throw refuse(reply?.error || 'generation_failed', reply || {});
            keys[id] = {job: reply.job, model: request.model || null, withImages: Boolean(meta.withImages), created_at: store.get(id)?.created_at || now(), view};
            save();
            // Stopped while the request was on its way: the engine gets the cancel now that the job has a key.
            if (store.get(id)?.status === 'cancelling') { await source.cancel(store.get(id)).catch(() => {}); }
            else store.upsert({id, status: 'queued'});
            void track(id);
            return {id};
        },
        async cancel(row) {
            const entry = keys[row.id];
            if (!entry) return; // Not sent yet: submit sends the cancel once it has the key.
            await clientOf(row.id).cancel(entry.job);
            void track(row.id);
        },
        // After a reload: every job this browser started and has not seen end is asked about again.
        async refresh() {
            for (const [id, entry] of Object.entries(keys)) {
                if (!entry?.job) { forget(id); continue; }
                if (!store.get(id)) store.upsert({id, kind: 'image', source: 'image', status: 'running', prompt: '', model: entry.model,
                    created_at: entry.created_at, started_at: entry.created_at, view: entry.view || 'image'}, {revive: true});
                void track(id);
            }
        },
    };
    store.addSource('image', source);
    return source;
}
const pick = value => value && typeof value === 'object' ? Object.fromEntries(['report_id', 'stage', 'fitted', 'memory', 'other_usage', 'message_zh']
    .filter(key => value[key] !== undefined).map(key => [key, value[key]])) : {};

// ---------- Videos: the progress stream the page already receives (freevideo.js turns it into window events) ----------
// A part of a long video reports under its chain; the chain itself ends in chainSource.
export function progressSource(store, {target = globalThis, now = store.now, cancel = null, framePath = id => `/freevideo/frame-preview/${id}`} = {}) {
    // A plain run carries no request id; its node runs one video at a time, and a new one says new_request.
    const identity = message => message.chain?.id ? 'chain:' + message.chain.id
        : message.task_id ? 'video:' + message.task_id : message.prompt_id ? 'video:' + message.prompt_id
        : message.node != null ? 'video:node:' + message.node : null;
    function receive(message) {
        if (!message || typeof message !== 'object') return;
        const id = identity(message); if (!id) return;
        const chain = id.startsWith('chain:');
        const overall = (chain ? message.chain?.overall : null) || message.overall || {};
        const terminal = !chain && (message.result ? 'done' : message.phase === 'failed' ? 'failed' : message.phase === 'cancelled' ? 'cancelled' : null);
        if (!terminal && ['complete', 'failed', 'cancelled'].includes(message.phase) && !store.get(id)) return;
        // After a reload the snapshot repeats runs that ended a while ago: those are history, not news.
        const age = finite(message.received_at) ? now() - message.received_at : 0;
        if (terminal && !store.get(id) && (age > 30000 || store.localOf(id)?.seen)) return;
        const elapsed = finite(overall.elapsed_seconds) ? overall.elapsed_seconds : null;
        const reportId = typeof message.report_id === 'string' && /^[a-f0-9]{32}$/.test(message.report_id) ? message.report_id : null;
        const frames = message.frame_preview;
        const preview = reportId && frames && Number.isInteger(frames.serial) && frames.serial > 0 && Number.isInteger(frames.frames) && frames.frames > 0
            ? {url: framePath(reportId) + '?serial=' + frames.serial, frames: frames.frames, serial: frames.serial} : undefined;
        const patch = {id, kind: chain ? 'chain' : 'video', source: chain ? 'chain' : 'video', status: terminal || (message.phase === 'cancelling' ? 'cancelling' : 'running'),
            phase: message.phase || message.stage || '', node: message.node ?? undefined, prompt_id: message.prompt_id || undefined,
            cancellable: chain || Boolean(message.prompt_id || store.get(id)?.prompt_id),
            percent: finite(overall.fraction) ? overall.fraction : undefined,
            eta_seconds: finite(overall.remaining_seconds) ? overall.remaining_seconds : undefined,
            started_at: elapsed != null && !store.get(id)?.started_at ? now() - elapsed * 1000 : undefined,
            position: 0, meta: {...(reportId ? {report_id: reportId} : {}), ...(preview ? {frames: preview} : {}),
                ...(chain && Number.isInteger(message.chain?.index) ? {part: message.chain.index + 1, parts: message.chain.count} : {})}};
        if (terminal === 'done') patch.result = {video: message.result.video, report_id: message.result.report_id || reportId, preview: Boolean(message.result.preview)};
        if (terminal === 'failed') patch.failure = {code: message.error_code || message.error || 'generation_failed', message: message.message || message.error || '', report_id: reportId};
        // A new run on the same key, as the progress panel reads it (generation_progress.js).
        const fresh = message.new_request === true || message.overall?.reset === true || (message.reset === true && !message.phase);
        store.upsert(patch, {revive: fresh && !terminal});
    }
    const listener = event => receive(event.detail);
    target.addEventListener?.('freevideo-progress', listener);
    const source = {
        receive,
        async cancel(row) { if (!cancel) throw new Error('cancel_unavailable'); await cancel(row); },
        dispose() { target.removeEventListener?.('freevideo-progress', listener); },
    };
    store.addSource('video', source);
    return source;
}

// ---------- Long videos (the creation panel's parts and the editor's generations) ----------
const CHAIN_STATES = {queued: 'queued', pending: 'queued', running: 'running', cancelling: 'cancelling', done: 'done', complete: 'done',
    failed: 'failed', cancelled: 'cancelled', interrupted: 'interrupted', stopped: 'cancelled'};
export function chainSource(store, {fetchJson, interval = 3000, idle = 0, setTimer = setTimeout, clearTimer = clearTimeout, cancel = null, maxAgeMs = 6 * 3600e3, now = store.now} = {}) {
    let timer = null, asking = null, disposed = false;
    function apply(summary) {
        if (!summary?.id) return;
        const id = 'chain:' + summary.id, known = store.get(id);
        let status = CHAIN_STATES[summary.state] || 'running';
        if (status === 'done' && summary.output_state === 'joining') status = 'running';
        const updated = Date.parse(summary.updated || summary.created || '') || 0;
        // Old finished chains are history (the library has them), not jobs to show.
        if (!known && (!ACTIVE.has(status) || (updated && now() - updated > maxAgeMs))) return;
        const patch = {id, kind: 'chain', source: 'chain', status, node: summary.node ?? undefined,
            created_at: known?.created_at ?? (Date.parse(summary.created || '') || undefined),
            meta: {parts: summary.count, done_parts: summary.done, ...(summary.last_report_id ? {report_id: summary.last_report_id} : {})}};
        if (!finite(known?.percent) && summary.count) patch.percent = summary.done / summary.count;
        if (status === 'done') patch.result = {video: summary.output || null, report_id: summary.last_report_id || null};
        if (status === 'failed') patch.failure = {code: summary.output_error || 'generation_failed', report_id: summary.last_report_id || null};
        store.upsert(patch, {revive: Boolean(known && FINAL.has(known.status) && ACTIVE.has(status))});
    }
    async function refresh() {
        if (asking) return asking;
        asking = (async () => {
            try { const value = await fetchJson('/freevideo/chains'); for (const summary of value?.chains || []) apply(summary); }
            catch { /* Asked again on the next turn. */ }
            finally { asking = null; schedule(); }
        })();
        return asking;
    }
    // Only while a long video runs (or once more after one ends); idle > 0 keeps a slow watch otherwise.
    function schedule() {
        if (disposed) return; clearTimer(timer); timer = null;
        const busy = store.active({kind: 'chain'}).length > 0;
        if (busy || idle > 0) timer = setTimer(refresh, busy ? interval : idle);
    }
    const stop = store.on('change', change => { if (!timer && !asking && change?.row?.kind === 'chain' && ACTIVE.has(change.row.status)) schedule(); });
    const source = {refresh, apply, async cancel(row) { if (!cancel) throw new Error('cancel_unavailable'); await cancel(row); void refresh(); },
        dispose() { disposed = true; clearTimer(timer); stop(); }};
    store.addSource('chain', source);
    return source;
}

// The page's one store. Sources are attached by task_float.js, which runs in the page.
export const jobs = createJobStore({storage: (() => { try { return globalThis.localStorage || null; } catch { return null; } })()});
