import {copyName} from './editor_model.js';
import {generationSize} from './editor_generation.js';
// HTTP and scheduling live here; records and editing stay in the data modules.
const clockDefault = {now: () => Date.now(), setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: id => clearTimeout(id)};
const copy = value => structuredClone(value);
const memoryProjects = new Map();
const errorFor = response => Object.assign(new Error('editor_request'), {status: response.status});
// A failed save keeps the server's reason so the page can say what went wrong.
async function saveFailure(response) {
    let body = null; try { body = await response.json(); } catch {}
    return Object.assign(errorFor(response), body && typeof body === 'object' ? {reason: body.reason, response: body} : {});
}
const post = value => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
export const sourceKey = source => source ? `${source.kind}:${source.id}` : null;
import {chainRoute, createPoller, interpolate, capabilityPoints} from './chain_support.js';
export {chainRoute, createPoller, interpolate, capabilityPoints};

// Debounce from the first edit, rather than postponing indefinitely while dragging.
// Writes are serialized; a response supplies metadata to the next snapshot only.
const savedContent = record => JSON.stringify({...record, id: null, revision: 0});
const OUTBOX = 'fv-editor-outbox';
const operationId = () => globalThis.crypto?.randomUUID?.().replaceAll('-', '') || Array.from({length: 32}, () => Math.floor(Math.random() * 16).toString(16)).join('');
const lockName = session => OUTBOX + ':' + session;
function holdSession(session = operationId()) {
    let release = () => {}; const owner = {session, held: !globalThis.navigator?.locks};
    let ready = Promise.resolve();
    if (globalThis.navigator?.locks) {
        ready = new Promise(resolve => {
            navigator.locks.request(lockName(session), () => { owner.held = true; resolve(); return new Promise(done => { release = done; }); });
        });
    }
    return Object.assign(owner, {ready, dispose: () => ready.then(() => release())});
}
function outbox() {
    const rows = [];
    try {
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (!key?.startsWith(OUTBOX + ':')) continue;
            try { const entry = JSON.parse(localStorage.getItem(key)); if (entry?.session && entry.project) rows.push([key, entry]); } catch {}
        }
        // Preserve pending records written by the previous page version.
        const legacy = JSON.parse(localStorage.getItem(OUTBOX) || '{}');
        for (const [key, entry] of Object.entries(legacy)) if (entry?.project) rows.push([key, {...entry, legacy: true}]);
    } catch {}
    return rows;
}
function saveOutbox(session, entry) {
    if (typeof localStorage === 'undefined') return false;
    try {
        if (entry) {
            if (new TextEncoder().encode(JSON.stringify(entry.project)).length > 2 * 1024 * 1024) {
                console.info('[FreeVideo editor] Record exceeds 2 MB; outbox skipped.'); return false;
            }
            localStorage.setItem(lockName(session), JSON.stringify(entry));
        } else localStorage.removeItem(lockName(session));
        return true;
    } catch { console.info('[FreeVideo editor] Local outbox unavailable.'); return false; }
}
export function createSaveQueue({write, read, onSaved = () => {}, onConflict = () => {}, onCopied = () => {},
    onMemory = () => {}, onError = () => {}, onStatus = () => {}, clock = clockDefault, delay = 600,
    initial = null, getRecord = null, copySuffix = ' (copy)', session = null, create = null, attempt: resumedAttempt = null}) {
    const owner = session || holdSession();
    let pending = null, inFlight = null, timer = null, heartbeat = null, meta = null, retry = 0, disposed = false;
    let memory = false, memoryNoticed = false, lastSaved = initial && copy(initial), failed = false, failure = null, copied = false, retained = false;
    let createOperation = create || (initial?.id ? null : operationId());
    let attempt = resumedAttempt && copy(resumedAttempt);
    const localKey = owner.session;
    function retain(record) {
        if (!owner.held) { owner.ready.then(() => { if (!disposed && (pending || attempt || inFlight)) retain({...((getRecord?.() || pending) ?? record), ...(meta || {})}); }); return false; }
        retained = saveOutbox(localKey, {session: localKey, id: record.id, project: copy(record), baseline: lastSaved, create: createOperation, attempt: copy(attempt), updated_at: clock.now()});
        if (heartbeat === null && !disposed) heartbeat = clock.setTimeout(() => {
            heartbeat = null;
            if (pending || attempt || inFlight) retain({...((getRecord?.() || pending) ?? record), ...(meta || {})});
        }, 10000);
        return retained;
    }
    function schedule(ms) {
        if (disposed || timer !== null) return;
        timer = clock.setTimeout(() => { timer = null; drain(); }, ms);
    }
    async function drain() {
        if (disposed || inFlight || (!pending && !attempt)) return inFlight;
        if (!attempt) { attempt = {operation: operationId(), project: {...pending, ...(meta || {})}}; pending = null; }
        const sent = attempt.project, body = copy(sent); failed = false;
        if (!body.id) createOperation ||= operationId();
        retain(pending || body);
        inFlight = (async () => {
            await owner.ready;
            try {
                if (disposed) return;
                if (memory) { attempt = null; onSaved(body, true, localKey); return; }
                const response = await write(body, createOperation, attempt.operation);
                if (disposed) return;
                if (response.status === 404) {
                    attempt = null; memory = true;
                    if (!memoryNoticed) { memoryNoticed = true; onMemory(); }
                    onSaved(body, true, localKey); return;
                }
                if (response.status === 409) {
                    const conflict = await response.json();
                    const remote = await read(body.id || conflict.id);
                    if (disposed) return;
                    if (!remote) throw errorFor(response);
                    const latest = copy(getRecord?.() || pending || sent);
                    if (lastSaved && savedContent(remote) === savedContent(lastSaved)) {
                        meta = {id: remote.id, revision: remote.revision};
                    } else {
                        meta = {id: null, revision: 0}; createOperation = operationId();
                        latest.name = copyName(latest.name, copySuffix);
                        onConflict({...latest, ...meta}, copySuffix);
                        copied = true;
                    }
                    attempt = null; pending = {...latest, ...meta}; retain(pending); retry = 0; return;
                }
                if (!response.ok) throw await saveFailure(response);
                const saved = (await response.json()).project;
                if (disposed) return;
                attempt = null;
                if (pending && savedContent(pending) === savedContent(sent)) pending = null;
                meta = {id: saved.id, revision: saved.revision}; retry = 0; lastSaved = copy(saved);
                onSaved(saved, false);
                if (copied) { copied = false; onCopied(saved); }
                if (pending) retain({...pending, ...meta});
                else { saveOutbox(localKey, null); retained = false; clock.clearTimeout(heartbeat); heartbeat = null; }
            } catch (error) {
                failed = true; failure = error;
                if (!disposed) {
                    retain({...(pending || sent), ...(meta || {})});
                    schedule(Math.min(30000, 1000 * 2 ** retry++)); onError(error);
                }
            }
        })().finally(() => {
            inFlight = null; onStatus();
            if ((pending || attempt) && timer === null && !disposed && !failed) drain();
        });
        return inFlight;
    }
    return {
        enqueue(record) { if (!disposed) { pending = copy(record); if (!record.id) createOperation ||= operationId(); retain({...pending, ...(meta || {})}); schedule(delay); } },
        async flush() {
            clock.clearTimeout(timer); timer = null;
            if (inFlight) await inFlight;
            clock.clearTimeout(timer); timer = null;
            while ((pending || attempt) && !disposed) {
                await drain();
                if (failed) break;
                if (inFlight) await inFlight;
                clock.clearTimeout(timer); timer = null;
            }
        },
        reset(record = null, recovery = null) {
            pending = recovery ? copy(recovery.project) : null; attempt = recovery?.attempt ? copy(recovery.attempt) : null;
            meta = null; retry = 0; copied = false; retained = false; lastSaved = copy(recovery ? recovery.baseline : record);
            createOperation = recovery?.create || (record?.id ? null : operationId());
            clock.clearTimeout(timer); timer = null; clock.clearTimeout(heartbeat); heartbeat = null;
            if (pending || attempt) { retain(pending || attempt.project); schedule(delay); } else saveOutbox(localKey, null);
        },
        get recovery() { return {project: copy({...(pending || attempt?.project), ...(meta || {})}), baseline: copy(lastSaved), create: createOperation, attempt: copy(attempt)}; },
        dispose({discard = false} = {}) { disposed = true; clock.clearTimeout(timer); timer = null; clock.clearTimeout(heartbeat); heartbeat = null; if (discard) saveOutbox(localKey, null); if (!session) owner.dispose(); },
        get dirty() { return !!pending || !!attempt || !!inFlight; },
        // Why the last write failed, while that failure still stands.
        get failure() { return failed ? failure : null; },
        get locallyRetained() { return retained; },
    };
}

export function mediaURL(api, value) {
    if (!value) return null;
    if (/^(?:https?:|blob:|data:|\/)/.test(value)) return api.apiURL?.(value) || value;
    const i = value.lastIndexOf('/');
    const path = '/view?' + new URLSearchParams({filename: value.slice(i + 1), subfolder: value.slice(0, Math.max(0, i)), type: 'output'});
    return api.apiURL?.(path) || path;
}

export function createEditorStore(api, {t = en => en, notify = () => {}, onGenerationReady = () => {}, onGenerationChecking = () => {}, clock = clockDefault, pollMs = 2000, standIn: injected = null} = {}) {
    const sources = new Map(), sourceReads = new Map(), watchers = new Set(), pollers = new Set(), once = new Set();
    let recovered = null, capabilityPoll = null;
    // The length a new continuation starts with where the engine names one (a device that continues in short parts).
    let continueSeconds = null;
    let generationAvailable = false, generationGone = false, generationProbe = null, generationRetry = null, generationAttempts = 0, generationStalled = false, generationAsking = false;
    let disposed = false; const uploads = new Set();
    const session = holdSession();
    let activeProject = null, sessionTimer = null, sessionWrites = Promise.resolve();
    // A stalled lease request is cut off so opening, switching and closing never wait on it.
    async function lease(body, extra = {}) {
        const controller = new AbortController(), timer = clock.setTimeout(() => controller.abort(), 8000);
        try { await json('/freevideo/editor/project/session', {...post(body), ...extra, signal: controller.signal}); }
        finally { clock.clearTimeout(timer); }
    }
    function activateProject(id) {
        clock.clearTimeout(sessionTimer); sessionTimer = null;
        const previous = activeProject; activeProject = id;
        sessionWrites = sessionWrites.catch(() => {}).then(async () => {
            // A failed release must not skip registering the edit that is already open.
            if (previous && previous !== id) await lease({id: previous, session: session.session, release: true}, {keepalive: true}).catch(() => {});
            if (id) await lease({id, session: session.session});
        });
        if (id && !disposed) sessionTimer = clock.setTimeout(() => { activateProject(activeProject).catch(() => {}); }, 20000);
        return sessionWrites;
    }
    const notice = (key, en, zh) => { if (!once.has(key)) { once.add(key); notify(t(en, zh)); } };
    const request = (path, options) => api.fetchApi(path, options);
    async function json(path, options, fallback = null) {
        const response = await request(path, options);
        if (response.status === 404) return fallback;
        if (!response.ok) throw errorFor(response);
        return response.json();
    }
    function cache(values) {
        for (const [key, value] of Object.entries(values || {})) sources.set(key, value);
        if (!disposed) for (const fn of watchers) fn();
    }
    async function load(query) {
        // Finish the retained edit before allowing a different project to replace it.
        if (recovered) return copy(recovered.project);
        const value = await json('/freevideo/editor/project?' + new URLSearchParams(query));
        if (value?.project) { cache(value.sources); return value.project; }
        if (query.id) return copy(memoryProjects.get(query.id) || (query.id === 'memory' ? memoryProjects.get('memory') : null));
        return copy([...memoryProjects.values()].find(record => record.tracks[0].clips[0]?.source?.id === query.work) || null);
    }
    // A list keeps how many damaged records the server left out of it.
    function counted(value) {
        const items = Array.isArray(value?.items) ? value.items : [];
        const unreadable = Number.isSafeInteger(value?.unreadable) && value.unreadable > 0 ? value.unreadable : 0;
        return Object.defineProperty(items, 'unreadable', {value: unreadable});
    }
    // lost: called once after three reads in a row fail; the watch then stops.
    function watch(read, deliver, lost = null) {
        let failures = 0, poller = null;
        const stop = () => { poller?.dispose(); pollers.delete(poller); };
        poller = createPoller(async () => { const value = await read(); failures = 0; return value; }, deliver, {clock, interval: pollMs,
            onError: error => { if (lost && ++failures >= 3) { stop(); lost(error); } }});
        pollers.add(poller);
        return stop;
    }
    function generationAbsent() {
        generationGone = true; generationRetry?.dispose();
        generationAvailable = false;
        if (!disposed) onGenerationReady(false);
    }
    function askGenerationAgain() {
        const delay = [2000, 5000, 15000, 30000][generationAttempts++];
        if (delay === undefined) { generationStalled = true; return; }
        const timer = clock.setTimeout(() => { retry.dispose(); store.generationReady(); }, delay);
        const retry = generationRetry = {dispose() { clock.clearTimeout(timer); pollers.delete(retry); if (generationRetry === retry) generationRetry = null; }};
        pollers.add(retry);
    }
    // Optional adapter (or factory): startChain, owns, chain, chainAction, and
    // optional restoreSources and generationAvailable/generationReady readiness
    // hooks for a delayed test generator. Production callers provide none.
    const standIn = typeof injected === 'function'
        ? injected({clock, sources, request: json, mediaURL: value => mediaURL(api, value)}) : injected;
    const store = {
        sources, cache,
        activateProject,
        get uploading() { return uploads.size > 0; },
        async deleteProject(id) {
            await sessionWrites;
            const response = await request('/freevideo/editor/project/delete', post({id, session: session.session}));
            if (!response.ok) throw await saveFailure(response);
            memoryProjects.delete(id);
            if (activeProject === id) { activeProject = null; clock.clearTimeout(sessionTimer); sessionTimer = null; }
        },
        get continueSeconds() { return standIn ? standIn.continueSeconds ?? null : continueSeconds; },
        get generationAvailable() { return standIn ? standIn.generationAvailable ?? true : generationAvailable; },
        // The server said for certain it cannot generate (not just a missed answer that is asked again).
        get generationGone() { return !standIn && generationGone; },
        // Every automatic check went unanswered: the page offers to check again. It stays so until an answer comes.
        get generationStalled() { return !standIn && generationStalled && !generationAvailable && !generationGone; },
        get generationChecking() { return !standIn && generationAsking; },
        // Checks now and starts the automatic checks over; the page is told of every answer as before.
        retryGeneration() {
            if (standIn || generationAvailable || generationGone || disposed) return Promise.resolve(generationAvailable);
            generationRetry?.dispose(); generationAttempts = 0;
            return store.generationReady();
        },
        generationReady() {
            if (standIn) return standIn.generationReady
                ? Promise.resolve(standIn.generationReady(ready => { if (!disposed) onGenerationReady(ready); }))
                : Promise.resolve(true);
            if (generationAvailable || generationGone || disposed) return Promise.resolve(generationAvailable);
            // Generating stays off until the list of long videos answers in its
            // expected shape. One bounded request at a time, shared by concurrent
            // callers; a missing route is final, an error or timeout is asked again.
            // Every answer is reported, so the page can stop waiting for the first.
            return generationProbe ||= (async () => {
                const controller = new AbortController();
                let timer, finish, answer = null;
                const timeout = new Promise(resolve => { finish = resolve; timer = clock.setTimeout(() => {
                    controller.abort(); resolve(null);
                }, 5000); });
                const probe = {dispose() { clock.clearTimeout(timer); controller.abort(); finish(null); pollers.delete(probe); }};
                pollers.add(probe); generationAsking = true; if (!disposed) onGenerationChecking();
                try {
                    // true: present; false: no such route; null: no usable answer.
                    answer = await Promise.race([Promise.resolve().then(() => request('/freevideo/chains', {signal: controller.signal}))
                        .then(async response => {
                            if (response.status === 404) return false;
                            if (!response.ok) return null;
                            const body = await response.json();
                            if (Number.isInteger(body?.continue_seconds) && body.continue_seconds > 0) continueSeconds = body.continue_seconds;
                            return Array.isArray(body?.chains) ? true : null;
                        })
                        .catch(() => null), timeout]);
                } finally { probe.dispose(); generationProbe = null; generationAsking = false; }
                if (disposed || generationGone) return false;
                if (answer === false) generationAbsent();
                else if (answer) { generationAvailable = true; onGenerationReady(true); }
                else { askGenerationAgain(); onGenerationReady(false); }
                return generationAvailable;
            })();
        },
        async recoverOutbox() {
            await session.ready;
            for (const [key, entry] of outbox()) {
                if (entry.session === session.session) continue;
                const recover = async () => {
                    // Re-read after claiming; another recovering page may have finished.
                    if (!entry.legacy && localStorage.getItem(key) !== JSON.stringify(entry)) return;
                    const q = createSaveQueue({clock, initial: entry.baseline, create: entry.create, attempt: entry.attempt,
                        session: {session: entry.session || operationId(), held: true, ready: Promise.resolve()},
                        copySuffix: t(' (copy)', '（副本）'), read: id => store.load(id),
                        write: (record, create, operation) => request('/freevideo/editor/project', post({project: record, operation, ...(create ? {create} : {})})),
                        onSaved: (record, memory, local) => { if (memory) memoryProjects.set(local, copy(record)); try { localStorage.setItem('fv-editor-last', record.id || local); } catch {} },
                        onCopied: () => notice('conflict', 'This edit changed elsewhere. Your changes here were saved as a copy.', '这条剪辑在别处改过，这里的改动已存为副本')});
                    try {
                        q.enqueue(entry.project); await q.flush();
                        if (q.dirty) {
                            // Transfer the unresolved request and latest edit to this live
                            // session before releasing the abandoned session's lock.
                            const value = {...q.recovery, session: session.session, updated_at: clock.now()};
                            if (!saveOutbox(session.session, value)) return;
                            recovered = value;
                            if (!entry.legacy) localStorage.removeItem(key);
                            try { localStorage.setItem('fv-editor-last', value.project.id || session.session); } catch {}
                        }
                        if (entry.legacy && (!q.dirty || recovered)) {
                            const rows = JSON.parse(localStorage.getItem(OUTBOX) || '{}');
                            if (JSON.stringify(rows[key]) === JSON.stringify({...entry, legacy: undefined})) { delete rows[key]; localStorage.setItem(OUTBOX, JSON.stringify(rows)); }
                        }
                    } finally { q.dispose(); }
                };
                if (globalThis.navigator?.locks && entry.session) {
                    await navigator.locks.request(lockName(entry.session), {ifAvailable: true}, lock => lock ? recover() : undefined);
                } else if (clock.now() - (entry.updated_at || 0) > 60000) await recover();
                if (recovered) break;
            }
        }, load: id => load({id}), byWork: work => load({work}),
        async projects() { return counted(await json('/freevideo/editor/projects', undefined, {items: []})); },
        createSaveQueue(state, {onStatus = () => {}} = {}) {
            const queue = createSaveQueue({clock, session, onStatus, initial: state.record, getRecord: () => state.record, copySuffix: t(' (copy)', '（副本）'), write: (record, create, operation) => { const options = post({project: record, operation, ...(create ? {create} : {})}); return request('/freevideo/editor/project', {...options, keepalive: new TextEncoder().encode(options.body).length < 60000}); },
                read: id => store.load(id),
                onSaved: (record, memory, local) => {
                    memoryProjects.set(record.id || local, copy({...state.record, id: record.id, revision: record.revision}));
                    state.adopt({id: record.id, revision: record.revision});
                    if (record.id && record.id !== activeProject) activateProject(record.id).catch(() => {});
                    try { localStorage.setItem('fv-editor-last', record.id || local); } catch {}
                    if (memory) memoryProjects.set(record.id || local, copy(state.record));
                },
                onConflict: (record, nameSuffix) => { state.adopt({id: null, revision: 0, nameSuffix}); },
                onCopied: () => notice('conflict', 'This edit changed elsewhere. Your changes here were saved as a copy.', '这条剪辑在别处改过，这里的改动已存为副本'),
                onMemory: () => notice('memory', 'Changes are kept in this page for now. Update and restart to save them.', '改动暂存在这个页面里，更新并重启后会保存。'),
                // Automatic retries stay quiet; switching away while unsaved warns in editor.js.
                onError: () => {}});
            const reset = queue.reset;
            queue.reset = record => { const entry = recovered; recovered = null; reset(record, entry); };
            return queue;
        },
        async getSources(keys, {refresh = false} = {}) {
            const missing = [...new Set(keys)].filter(key => refresh || !sources.has(key) || sources.get(key)._fallback);
            if (!missing.length) return sources;
            const signature = missing.slice().sort().join('\n');
            if (!sourceReads.has(signature)) {
                const query = new URLSearchParams(); missing.forEach(key => query.append('key', key));
                sourceReads.set(signature, (async () => {
                    const value = await json('/freevideo/editor/sources?' + query, undefined, {sources: {}});
                    cache(value.sources);
                    standIn?.restoreSources?.(missing);
                })().finally(() => sourceReads.delete(signature)));
            }
            await sourceReads.get(signature); return sources;
        },
        subscribeSources(fn) { watchers.add(fn); return () => watchers.delete(fn); },
        async waveform(key, points = 200) {
            return (await json('/freevideo/editor/waveform?' + new URLSearchParams({key, points}), undefined, {peaks: []})).peaks || [];
        },
        async works(before = null) {
            const query = new URLSearchParams({limit: '24'}); if (before) query.set('before', before);
            const value = await json('/freevideo/library?' + query, {cache: 'no-store'}, {items: [], next: null});
            const fallback = {};
            for (const row of value.items || []) if (row.video && row.id && !sources.has(`work:${row.id}`)) {
                fallback[`work:${row.id}`] = {...row.geometry, _fallback: true, video: mediaURL(api, row.video), audio: mediaURL(api, row.video),
                    has_video: true, has_audio: row.has_audio !== false, path: row.video, missing: false,
                    thumbnail: api.apiURL?.('/freevideo/library/thumbnail?' + new URLSearchParams({id: row.id})) || '/freevideo/library/thumbnail?' + new URLSearchParams({id: row.id})};
            }
            if (Object.keys(fallback).length) cache(fallback);
            return value;
        },
        async imports() { return counted(await json('/freevideo/editor/imports', undefined, {items: []})); },
        watchImports(fn) { return watch(() => store.imports(), items => { fn(items); return true; }); },
        upload(file, progress = () => {}) {
            if (disposed) return Promise.reject(new Error('editor_closed'));
            return new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                const release = () => { uploads.delete(upload); xhr.onload = xhr.onerror = null; xhr.upload.onprogress = null; };
                const upload = {xhr, release, reject}; uploads.add(upload);
                xhr.open('POST', api.apiURL?.('/freevideo/editor/import') || '/freevideo/editor/import');
                xhr.setRequestHeader('X-FreeVideo-Filename', encodeURIComponent(file.name));
                xhr.setRequestHeader('Content-Type', 'application/octet-stream');
                xhr.upload.onprogress = event => { if (event.lengthComputable) progress(event.loaded / event.total); };
                xhr.onload = () => {
                    release();
                    if (xhr.status === 404) { notice('import', 'Import is unavailable until you restart after updating.', '更新后重启可导入素材。'); resolve(null); return; }
                    // The server's reason (a full disk) comes along, as with a failed save.
                    if (xhr.status < 200 || xhr.status >= 300) { let body = null; try { body = JSON.parse(xhr.responseText); } catch {} reject(Object.assign(errorFor(xhr), {reason: body?.reason})); return; }
                    try { resolve(JSON.parse(xhr.responseText).import); } catch (error) { reject(error); }
                };
                xhr.onerror = () => { release(); reject(new Error('import_failed')); };
                try { xhr.send(file); } catch (error) { release(); reject(error); }
            });
        },
        capabilities(options, onReady = () => {}) {
            capabilityPoll?.dispose();
            const size = generationSize(options);
            const fallback = {maxSeconds: 15, maxFollowSeconds: 15, maxRequestFrames: null, estimate: () => null, generationSize: size};
            if (disposed) return fallback;
            const started = clock.now();
            let active = true, timer = null;
            const controller = new AbortController();
            const poller = {dispose() {
                active = false; clock.clearTimeout(timer); controller.abort(); pollers.delete(poller);
            }};
            capabilityPoll = poller; pollers.add(poller);
            async function read() {
                try {
                    const reply = await json('/freevideo/capabilities?' + new URLSearchParams(size), {signal: controller.signal});
                    if (!active || disposed) return;
                    if (reply?.status === 'computing') {
                        const seconds = Number(reply.retry_after_seconds);
                        const delay = clock.now() - started >= 60000 ? 10000
                            : Number.isFinite(seconds) && seconds > 0 ? Math.max(1000, Math.min(60000, seconds * 1000)) : 1000;
                        timer = clock.setTimeout(() => { timer = null; read(); }, delay);
                        return;
                    }
                    if (!reply || (reply.status && reply.status !== 'ready')) { poller.dispose(); return; }
                    // Advertised presets must not change the size we just asked
                    // about: both estimates and the final request use this size.
                    const value = capabilityPoints(reply, {...options, ...size});
                    const ready = {maxSeconds: value.maxSeconds, maxFollowSeconds: value.maxFollowSeconds, maxRequestFrames: value.maxRequestFrames,
                        sizes: reply.sizes, generationSize: size,
                        estimate: frames => interpolate(value.points, frames / 24)};
                    onReady(ready); poller.dispose();
                } catch { if (active) poller.dispose(); }
            }
            // The first HTTP response can itself be slow. Editing starts with
            // these limits; only the callback publishes a measured capability.
            read(); return fallback;
        },
        // The longest single part this computer makes at a size other than the edit's; null when it cannot be told.
        async requestFrames(size, settings) {
            try {
                for (let tries = 0; tries < 5 && !disposed; tries++) {
                    const reply = await json('/freevideo/capabilities?' + new URLSearchParams(size));
                    if (reply?.status !== 'computing') return !reply || (reply.status && reply.status !== 'ready') ? null
                        : capabilityPoints(reply, {...size, settings}).maxRequestFrames;
                    await new Promise(done => clock.setTimeout(done, 1000));
                }
            } catch { /* not known */ }
            return null;
        },
        async startChain(payload) {
            if (standIn) return standIn.startChain(payload);
            if (!generationAvailable) return null;
            const response = await request(chainRoute(), post(payload));
            if (response.status === 404) {
                generationAbsent();
                notice('generate', 'Restart FreeVideo to generate.', '请重新启动 FreeVideo 后再生成。');
                return null;
            }
            if (!response.ok) {
                // The server's own sentence for a video with too many parts, or a continued part too short, is shown as it is.
                const body = await response.json().catch(() => null);
                if (['chain_too_long', 'chain_part_too_short', 'chain_short_parts'].includes(body?.code)) throw Object.assign(new Error(body.code), {code: body.code, said: [body.error, body.error_zh]});
                throw Object.assign(new Error(typeof body?.error === 'string' ? body.error : body?.message || `HTTP ${response.status}`),
                    {status: response.status, reason: body?.reason || body?.code, response: body});
            }
            return (await response.json())?.chain || null;
        },
        async chain(id) { return standIn?.owns(id) ? standIn.chain(id) : (await json(chainRoute(id)))?.chain || null; },
        watchChain(id, fn) { return watch(() => store.chain(id), fn); },
        async chainAction(id, action) {
            if (standIn?.owns(id)) return standIn.chainAction(id, action);
            const payload = action === 'resume' && api.clientId ? {client_id: api.clientId} : {};
            return (await json(chainRoute(id, action), post(payload)))?.chain || null;
        },
        async export(id, revision, options) {
            const response = await request('/freevideo/editor/export', post({id, revision, ...(options && Object.keys(options).length ? {options} : {})}));
            const value = await response.json().catch(() => null);
            if (!response.ok && (response.status !== 404 || value?.reason || value?.code))
                throw Object.assign(errorFor(response), {reason: value?.reason, code: value?.code, items: value?.items});
            if (response.status === 404 || !value) { notice('export', 'To export, update FreeVideo and restart it.', '请更新 FreeVideo 并重新启动后再导出。'); return null; }
            return value?.export || null;
        },
        watchExport(id, fn, lost) { return watch(async () => (await json('/freevideo/editor/export?' + new URLSearchParams({id})))?.export || null, fn, lost); },
        cancelExport: id => json('/freevideo/editor/export/cancel', post({id})),
        // Subtitles from speech. What this computer can recognise; null where it cannot or the server is older.
        async ears() { return (await json('/freevideo/editor/ears'))?.available || null; },
        async startEars(id, revision) { return (await json('/freevideo/editor/ears', post({kind: 'transcribe', id, revision})))?.job || null; },
        watchEars(id, fn) { return watch(async () => (await json('/freevideo/editor/ears?' + new URLSearchParams({id})))?.job || null, fn); },
        cancelEars: id => json('/freevideo/editor/ears/cancel', post({id})),
        // What recognising speech needs is downloaded on first use; null where the server has no such download.
        earsPack: () => json('/freevideo/editor/subtitle-pack'),
        installEars: () => json('/freevideo/editor/subtitle-pack', post({action: 'install', language: true})),
        cancelEarsPack: () => json('/freevideo/editor/subtitle-pack', post({action: 'cancel'})),
        watchEarsPack(fn) { return watch(() => json('/freevideo/editor/subtitle-pack'), fn); },
        dispose() { disposed = true; activateProject(null).catch(() => {}); session.dispose(); for (const poller of pollers) poller.dispose(); pollers.clear(); watchers.clear();
            for (const upload of [...uploads]) { upload.release(); upload.xhr.abort(); upload.reject(new Error('editor_closed')); } },
    };
    return store;
}
