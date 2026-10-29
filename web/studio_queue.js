// ComfyUI owns execution. This controller only submits frozen workflows and
// replenishes a loop after its previous request has actually completed.
import {fetchQueueRead, detectBrowserExtension, isNetworkTypeError, wrappedFetchOnly} from './browser_extension.js';
import {clonePrompt} from './optional_inputs.js';
import {isStaleProgress} from './progress_connection.js';
const clone = clonePrompt;

// One dropped read is usually a connection reset or an engine restart. Only this many
// polls failing in a row are shown; the next successful read clears the notice.
// While the outage lasts the polls back off, and a loop is stopped once it passes the limit.
const OUTAGE_POLLS = 3, OUTAGE_LOOP_LIMIT_MS = 180000, OUTAGE_MAX_DELAY_MS = 8000;

// ComfyUI leaves a muted (2) or bypassed (4) node out of the request. The creation panel is where
// the node is used, so a request from it turns the node back on; true when it was off.
export function enableNode(node) {
    // Every request that wants the node on takes over from an earlier request's claim on its mode,
    // whether or not the node still needs switching.
    if (node) node.freevideoNodeOn = null;
    if (node?.mode !== 2 && node?.mode !== 4) return false;
    node.mode = 0;
    node.graph?.change?.(); node.setDirtyCanvas?.(true, true);
    return true;
}

// Like enableNode, for a request that may still fail: restore() puts the user's mode back, but only
// if nothing else has touched the node since (a hand edit, another request) and only once.
export function turnNodeOn(node) {
    const prior = node?.mode;
    if (!enableNode(node)) return null;
    const token = node.freevideoNodeOn = {};
    const release = () => { if (node.freevideoNodeOn === token) { node.freevideoNodeOn = null; return true; } return false; };
    return {
        done: release,
        restore() {
            if (!release() || node.mode !== 0) return;
            node.mode = prior;
            node.graph?.change?.(); node.setDirtyCanvas?.(true, true);
        },
    };
}

// Repeat the completed request, not an editor draft changed while it was running.
// Comfy's local history owns the prompt/media graph; no new server endpoint.
export async function regenerateResult(api, nodeId, result, currentPrompt) {
    nodeId = String(nodeId);
    const path = result.request_id ? '/history/' + encodeURIComponent(result.request_id) : '/history?max_items=64';
    let rows = [];
    try {
        const response = await fetchQueueRead(api, path);
        if (response.ok) rows = Object.values(await response.json());
    } catch (error) {
        if (detectBrowserExtension(error)) throw error;
        /* Cleared/unavailable history is an ordinary new generation. */
    }
    rows.sort((a, b) => (b.prompt?.[0] || 0) - (a.prompt?.[0] || 0));
    const saved = rows.find(row => row.outputs?.[nodeId]?.freevideo_summary?.[0]?.video === result.video
        && row.outputs[nodeId].freevideo_summary[0].result_cache_hit === true
        && row.status?.status_str === 'success' && row.status?.completed
        && row.prompt?.[2]?.[nodeId]?.class_type === 'FreeVideoGenerate');
    const snapshot = saved
        ? {output: clone(saved.prompt[2]), workflow: clone(saved.prompt[3]?.extra_pnginfo?.workflow || {})}
        : clone(await currentPrompt());
    snapshot.output[nodeId].inputs.force_regenerate = true;
    const reply = await api.queuePrompt(0, snapshot);
    if (!reply?.prompt_id) throw new Error('submit_failed');
    api.dispatchCustomEvent?.('promptQueued', {number: 0, batchCount: 1});
    return reply.prompt_id;
}
// Upscale a preview: queue the request that produced it with refine_from set.
// The bridge continues the preview's own saved request, so later editor
// changes cannot alter what is upscaled.
export async function upscaleResult(api, nodeId, result, currentPrompt) {
    nodeId = String(nodeId);
    if (!result?.preview || !result.video) throw new Error('not_preview');
    const path = result.request_id ? '/history/' + encodeURIComponent(result.request_id) : '/history?max_items=64';
    let rows = [];
    try {
        const response = await fetchQueueRead(api, path);
        if (response.ok) rows = Object.values(await response.json());
    } catch (error) {
        if (detectBrowserExtension(error)) throw error;
        /* Cleared/unavailable history: the bridge still uses the preview's request. */
    }
    rows.sort((a, b) => (b.prompt?.[0] || 0) - (a.prompt?.[0] || 0));
    const saved = rows.find(row => row.outputs?.[nodeId]?.freevideo_summary?.[0]?.video === result.video
        && row.status?.status_str === 'success' && row.status?.completed
        && row.prompt?.[2]?.[nodeId]?.class_type === 'FreeVideoGenerate');
    const snapshot = saved
        ? {output: clone(saved.prompt[2]), workflow: clone(saved.prompt[3]?.extra_pnginfo?.workflow || {})}
        : clone(await currentPrompt());
    if (!snapshot.output?.[nodeId]) throw new Error('missing_node');
    Object.assign(snapshot.output[nodeId].inputs, {preview: false, refine_from: result.video, force_regenerate: false});
    const reply = await api.queuePrompt(0, snapshot);
    if (!reply?.prompt_id) throw new Error('submit_failed');
    api.dispatchCustomEvent?.('promptQueued', {number: 0, batchCount: 1});
    return reply.prompt_id;
}

// Each preview owns its submission outcome; another preview waits its turn.
// Only duplicate clicks for the same pending preview share a promise.
export function createSecondPassQueue(api, nodeId, currentPrompt) {
    const pending = new Map();
    let tail = Promise.resolve();
    return result => {
        const key = result?.video;
        if (pending.has(key)) return pending.get(key);
        const request = tail.catch(() => {}).then(() => upscaleResult(api, typeof nodeId === 'function' ? nodeId() : nodeId, result, currentPrompt))
            .finally(() => { pending.delete(key); });
        pending.set(key, request); tail = request;
        return request;
    };
}

export function randomSeed() {
    const bytes = crypto.getRandomValues(new Uint32Array(2));
    return (bytes[0] & 0x1fffff) * 0x100000000 + bytes[1];
}

export function seededPrompt(snapshot, nodeId, seedIndex, seed) {
    const prompt = clone(snapshot);
    if (Number.isSafeInteger(seed)) {
        prompt.output[nodeId].inputs.seed = seed;
        const node = prompt.workflow?.nodes?.find(row => String(row.id) === String(nodeId));
        if (node && seedIndex >= 0 && Array.isArray(node.widgets_values)) node.widgets_values[seedIndex] = seed;
    }
    return prompt;
}

export function createStudioQueue(api, nodeId, {random = randomSeed, pollMs = 2000, onResult = () => {},
    formatError = error => error} = {}) {
    nodeId = String(nodeId);
    let running = null, pending = [], submitting = false, loop = null, timer = null;
    let runningAll = [], pooled = false;
    let refreshing = null, refreshAgain = false, lastError = null, disposed = false, outage = null;
    const listeners = new Set();
    const owned = new Set(), delivered = new Set();
    function remember(id) { owned.add(id); if (owned.size > 128) { const old = owned.values().next().value; owned.delete(old); delivered.delete(old); } }
    function result(id, output) {
        if (!owned.has(id) || delivered.has(id) || !output?.freevideo_summary?.[0]) return;
        delivered.add(id); onResult(output, id);
    }
    const state = () => ({running, pending, submitting, looping: Boolean(loop?.active),
        loopCompleted: loop?.completed || 0, error: lastError, ...(pooled ? {running_all: runningAll} : {})});
    const publish = () => { for (const listener of listeners) listener(state()); };
    function schedule() {
        clearTimeout(timer); timer = null;
        if (!disposed && pollMs && (listeners.size || loop?.active)) timer = setTimeout(() => refresh().catch(() => {}), outage && outage.failures > OUTAGE_POLLS
            ? Math.min(pollMs * 2 ** (outage.failures - OUTAGE_POLLS), OUTAGE_MAX_DELAY_MS) : pollMs);
    }
    function fail(error) {
        if (loop) loop.active = false;
        lastError = error; publish();
    }
    function matches(row) {
        return row[2]?.[nodeId]?.class_type === 'FreeVideoGenerate'
            && (!api.clientId || row[3]?.client_id === api.clientId);
    }
    const entry = row => {
        // An older snapshot never moves a task's progress backwards.
        const snapshot = row[3]?.freevideo_progress?.[nodeId], known = runningAll.find(item => item.id === row[1])?.progress;
        const progress = snapshot && !isStaleProgress(known, snapshot) ? snapshot : known || snapshot;
        return {id: row[1], inputs: clone(row[2][nodeId].inputs),
            ...(row[3]?.freevideo_dispatch ? {executor: row[3].freevideo_dispatch, ...(progress ? {progress} : {})} : {})};
    };
    async function cancel(id) {
        const response = await api.fetchApi('/freevideo/cancel', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt_id: id, node_id: nodeId})});
        if (!response.ok) throw new Error('cancel_failed');
        return response.json();
    }
    // The page changes its list only after the server says where the request now is.
    async function moveFirst(id) {
        const response = await api.fetchApi('/freevideo/queue/first', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt_id: id, node_id: nodeId})});
        if (!response.ok) throw new Error('move_failed');
        const reply = await response.json();
        if (reply.status === 'unavailable') throw new Error('move_failed');
        await refresh();
        return reply;
    }
    async function submit(session, randomized) {
        let seed = session.snapshot.output[nodeId].inputs.seed;
        if (randomized) {
            const candidate = random();
            seed = candidate === session.lastSeed ? (candidate + 1) % Number.MAX_SAFE_INTEGER : candidate;
        }
        session.lastSeed = seed;
        const prompt = seededPrompt(session.snapshot, nodeId, session.seedIndex, seed);
        let reply;
        try {
            reply = await api.queuePrompt(0, prompt);
            if (!reply?.prompt_id) throw new Error('submit_failed');
        } catch (error) {
            // Format while the submitted snapshot is still available. The
            // editor may already contain different text by the time it fails.
            throw formatError(error, prompt);
        }
        remember(reply.prompt_id);
        session.onAccepted?.();
        if (session.repeat) {
            session.waiting = reply.prompt_id;
            // Stop may be clicked while the server is acknowledging submission.
            if (!session.active) {
                const response = await fetchQueueRead(api, '/queue');
                if (response.ok && (await response.json()).queue_pending?.some(row => row[1] === reply.prompt_id)) await cancel(reply.prompt_id);
            }
        }
        api.dispatchCustomEvent?.('promptQueued', {number: 0, batchCount: 1});
        return reply.prompt_id;
    }
    async function advanceLoop() {
        const session = loop;
        if (!session?.active || session.sending || !session.waiting) return;
        if (runningAll.some(row => row.id === session.waiting) || pending.some(row => row.id === session.waiting)) return;
        const response = await fetchQueueRead(api, '/history/' + encodeURIComponent(session.waiting));
        if (!response.ok) throw new Error('queue_unavailable');
        const history = (await response.json())[session.waiting];
        if (loop !== session || !session.active) return;
        result(session.waiting, history?.outputs?.[nodeId]);
        // A missing entry can mean a deleted queued request or cleared history.
        // Never guess success and start another expensive GPU job.
        if (!history?.status?.completed || history.status.status_str !== 'success') {
            session.active = false; lastError = new Error('loop_stopped'); return;
        }
        session.completed++; session.sending = true; publish();
        try { await submit(session, true); refreshAgain = true; }
        finally { session.sending = false; }
    }
    async function readQueue() {
        const response = await fetchQueueRead(api, '/queue');
        if (!response.ok) throw new Error('queue_unavailable');
        const queue = await response.json();
        runningAll = (queue.queue_running || []).filter(matches).map(entry);
        running = runningAll[0] || null;
        // Comfy returns its heap array; the queue number gives the real running order.
        pending = (queue.queue_pending || []).filter(matches).sort((a, b) => a[0] - b[0]).map(entry);
        pooled = [...runningAll, ...pending].some(row => row.executor) || runningAll.length > 1;
        outage = null;
        if (isNetworkTypeError(lastError) && !lastError.loopStopped) lastError = null;
        for (const row of [...runningAll, ...pending]) remember(row.id);
        await advanceLoop(); publish();
    }
    function refresh() {
        if (disposed) return Promise.resolve();
        if (refreshing) { refreshAgain = true; return refreshing; }
        refreshing = (async () => {
            do { refreshAgain = false; await readQueue(); } while (refreshAgain && !disposed);
        })().catch(error => {
            // Only a wrapped fetch is not proof of a blocking extension: it is counted as an outage like any other.
            const extension = detectBrowserExtension(error);
            if (!isNetworkTypeError(error) || (extension && !wrappedFetchOnly(extension))) { fail(error); throw error; }
            // A lost connection does not stop a loop at first: it only continues after a successful read.
            outage = {failures: (outage?.failures || 0) + 1, since: outage?.since ?? Date.now()};
            if (outage.failures < OUTAGE_POLLS) return;
            // The notice for a stopped loop stays as it is while the outage goes on.
            if (lastError?.loopStopped) throw error;
            const lasted = Date.now() - outage.since, giveUp = lasted >= OUTAGE_LOOP_LIMIT_MS && loop?.active;
            const note = `Queue reads failed ${outage.failures} times in a row over ${Math.round(lasted / 1000)} s; `
                + `online=${globalThis.navigator?.onLine}, page hidden=${globalThis.document?.hidden}.${giveUp ? ' The loop was stopped.' : ''}`;
            if (typeof error === 'object' && !String(error.details || '').includes('Queue reads failed')) error.details = [error.details, note].filter(Boolean).join('\n');
            // A stopped loop keeps its notice after the connection returns, so the user sees why it stopped.
            if (giveUp) { if (typeof error === 'object') error.loopStopped = true; fail(error); throw error; }
            lastError = error; publish(); throw error;
        }).finally(() => { refreshing = null; schedule(); });
        return refreshing;
    }
    async function start(snapshot, {count = 1, repeat = false, seedIndex = -1, onAccepted = null} = {}) {
        if (submitting) throw new Error('submitting');
        if (!Number.isInteger(count) || count < 1 || count > 100) throw new Error('invalid_count');
        if (repeat && loop?.active) throw new Error('loop_active');
        if (!snapshot.output?.[nodeId]) throw new Error('missing_node');
        if ((repeat || count > 1) && !Number.isSafeInteger(snapshot.output[nodeId].inputs.seed)) throw new Error('linked_seed');
        const session = {snapshot: clone(snapshot), seedIndex, repeat, active: repeat, completed: 0, waiting: null, sending: true, onAccepted};
        if (repeat) loop = session;
        submitting = true; lastError = null; publish();
        try {
            for (let i = 0; i < (repeat ? 1 : count); i++) await submit(session, repeat || count > 1);
        } catch (error) { fail(error); throw error; }
        finally { session.sending = false; submitting = false; publish(); await refresh().catch(() => {}); }
    }
    async function stopLoop() {
        const session = loop;
        if (!session) return;
        session.active = false; publish();
        await refresh();
        if (pending.some(row => row.id === session.waiting)) await cancel(session.waiting);
        await refresh();
    }
    const onStatus = () => { if (listeners.size || loop?.active) refresh().catch(() => {}); };
    const onFailure = event => {
        if (event.detail?.prompt_id === loop?.waiting) { loop.active = false; lastError = new Error('loop_stopped'); publish(); }
        onStatus();
    };
    const onExecuted = event => {
        if (String(event.detail?.node) === nodeId) result(event.detail?.prompt_id, event.detail?.output);
    };
    const onProgress = event => {
        const message = event.detail;
        if (!message?.task_id || String(message.node) !== nodeId) return;
        const row = runningAll.find(row => row.id === message.task_id);
        if (row && !isStaleProgress(row.progress, message)) { row.progress = message; publish(); }
    };
    api.addEventListener('status', onStatus);
    api.addEventListener('execution_error', onFailure);
    api.addEventListener('execution_interrupted', onFailure);
    api.addEventListener('executed', onExecuted);
    api.addEventListener('freevideo_progress', onProgress);
    return {
        state, refresh, start, stopLoop, moveFirst,
        subscribe(listener) { listeners.add(listener); listener(state()); schedule(); return () => { listeners.delete(listener); schedule(); }; },
        async cancel(id) {
            if (loop?.waiting === id) { loop.active = false; publish(); }
            const reply = await cancel(id); await refresh(); return reply;
        },
        dispose() {
            disposed = true; if (loop) loop.active = false; clearTimeout(timer); listeners.clear();
            api.removeEventListener('status', onStatus); api.removeEventListener('execution_error', onFailure);
            api.removeEventListener('execution_interrupted', onFailure);
            api.removeEventListener('executed', onExecuted);
            api.removeEventListener('freevideo_progress', onProgress);
        },
    };
}
