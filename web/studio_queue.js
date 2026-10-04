// ComfyUI owns execution. This controller only submits frozen workflows and
// replenishes a loop after its previous request has actually completed.
const clone = value => structuredClone(value);

// Repeat the completed request, not an editor draft changed while it was running.
// Comfy's local history owns the prompt/media graph; no new server endpoint.
export async function regenerateResult(api, nodeId, result, currentPrompt) {
    nodeId = String(nodeId);
    const path = result.request_id ? '/history/' + encodeURIComponent(result.request_id) : '/history?max_items=64';
    let rows = [];
    try {
        const response = await api.fetchApi(path);
        if (response.ok) rows = Object.values(await response.json());
    } catch { /* Cleared/unavailable history is an ordinary new generation. */ }
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
    let refreshing = null, refreshAgain = false, lastError = null, disposed = false;
    const listeners = new Set();
    const owned = new Set(), delivered = new Set();
    function remember(id) { owned.add(id); if (owned.size > 128) { const old = owned.values().next().value; owned.delete(old); delivered.delete(old); } }
    function result(id, output) {
        if (!owned.has(id) || delivered.has(id) || !output?.freevideo_summary?.[0]) return;
        delivered.add(id); onResult(output, id);
    }
    const state = () => ({running, pending, submitting, looping: Boolean(loop?.active),
        loopCompleted: loop?.completed || 0, error: lastError});
    const publish = () => { for (const listener of listeners) listener(state()); };
    function schedule() {
        clearTimeout(timer); timer = null;
        if (!disposed && pollMs && (listeners.size || loop?.active)) timer = setTimeout(() => refresh().catch(() => {}), pollMs);
    }
    function fail(error) {
        if (loop) loop.active = false;
        lastError = error; publish();
    }
    function matches(row) {
        return row[2]?.[nodeId]?.class_type === 'FreeVideoGenerate'
            && (!api.clientId || row[3]?.client_id === api.clientId);
    }
    const entry = row => ({id: row[1], inputs: clone(row[2][nodeId].inputs)});
    async function cancel(id) {
        const response = await api.fetchApi('/freevideo/cancel', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt_id: id, node_id: nodeId})});
        if (!response.ok) throw new Error('cancel_failed');
        return response.json();
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
        if (session.repeat) {
            session.waiting = reply.prompt_id;
            // Stop may be clicked while the server is acknowledging submission.
            if (!session.active) {
                const response = await api.fetchApi('/queue');
                if (response.ok && (await response.json()).queue_pending?.some(row => row[1] === reply.prompt_id)) await cancel(reply.prompt_id);
            }
        }
        api.dispatchCustomEvent?.('promptQueued', {number: 0, batchCount: 1});
        return reply.prompt_id;
    }
    async function advanceLoop() {
        const session = loop;
        if (!session?.active || session.sending || !session.waiting) return;
        if (running?.id === session.waiting || pending.some(row => row.id === session.waiting)) return;
        const response = await api.fetchApi('/history/' + encodeURIComponent(session.waiting));
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
        const response = await api.fetchApi('/queue');
        if (!response.ok) throw new Error('queue_unavailable');
        const queue = await response.json();
        running = (queue.queue_running || []).filter(matches).map(entry)[0] || null;
        pending = (queue.queue_pending || []).filter(matches).map(entry);
        for (const row of [running, ...pending]) if (row) remember(row.id);
        await advanceLoop(); publish();
    }
    function refresh() {
        if (disposed) return Promise.resolve();
        if (refreshing) { refreshAgain = true; return refreshing; }
        refreshing = (async () => {
            do { refreshAgain = false; await readQueue(); } while (refreshAgain && !disposed);
        })().catch(error => { fail(error); throw error; }).finally(() => { refreshing = null; schedule(); });
        return refreshing;
    }
    async function start(snapshot, {count = 1, repeat = false, seedIndex = -1} = {}) {
        if (submitting) throw new Error('submitting');
        if (!Number.isInteger(count) || count < 1 || count > 100) throw new Error('invalid_count');
        if (repeat && loop?.active) throw new Error('loop_active');
        if (!snapshot.output?.[nodeId]) throw new Error('missing_node');
        if ((repeat || count > 1) && !Number.isSafeInteger(snapshot.output[nodeId].inputs.seed)) throw new Error('linked_seed');
        const session = {snapshot: clone(snapshot), seedIndex, repeat, active: repeat, completed: 0, waiting: null, sending: true};
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
    api.addEventListener('status', onStatus);
    api.addEventListener('execution_error', onFailure);
    api.addEventListener('execution_interrupted', onFailure);
    api.addEventListener('executed', onExecuted);
    return {
        state, refresh, start, stopLoop,
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
        },
    };
}
