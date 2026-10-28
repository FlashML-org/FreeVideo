import {chainRoute, createPoller} from './chain_support.js';
import {buildChainRequest} from './length_plan.js?v=20261011-length';

export const CHAIN_PROPERTY = 'freevideo_chain';
const activeStates = new Set(['queued', 'running', 'cancelling', 'awaiting_review']);
const finite = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const post = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

export function chainPosition(chain, message = {}) {
    const count = message.chain?.count ?? chain?.count ?? chain?.blocks?.length ?? 0;
    let index = message.chain?.index ?? chain?.current?.index;
    if (!Number.isInteger(index)) index = chain?.blocks?.findIndex(block => block.id === chain.current) ?? -1;
    if (index < 0) index = chain?.blocks?.findIndex(block => ['queued', 'running', 'failed', 'cancelled'].includes(block.state)) ?? -1;
    if (index < 0) index = chain?.blocks?.findIndex(block => block.state === 'pending') ?? -1;
    if (index < 0) index = Math.max(0, count - 1);
    return {index: Math.min(Math.max(0, index), Math.max(0, count - 1)), count};
}

// A fallback part is explicitly distinct from a joined partial result.
export function readChainResult(chain) {
    if (!chain) return null;
    const joining = chain.output_state === 'joining' || (chain.output_state == null && chain.joining === true);
    const failedJoin = chain.output_state === 'failed';
    const stopped = ['failed', 'cancelled', 'interrupted'].includes(chain.state);
    const awaitingReview = chain.state === 'awaiting_review';
    const last = chain.blocks?.filter(block => block.state === 'done' && block.output).at(-1);
    const joined = chain.output_state === 'done' && typeof chain.output === 'string' && Boolean(chain.output);
    const video = joined ? chain.output : awaitingReview || (!joining && (failedJoin || stopped)) ? last?.output || null : null;
    return {video, joined, joining, awaitingReview, partial: Boolean(video) && (!joined || stopped || awaitingReview),
        reviewBlock: awaitingReview ? last : null,
        error: failedJoin ? chain.output_error || 'join_failed' : chain.error || chain.blocks?.find(block => block.state === 'failed')?.error || null};
}

export function sumPartEstimates(plan, estimate) {
    if (!plan || plan.count < 2 || typeof estimate !== 'function') return null;
    const values = plan.blocks.map(block => estimate(block.frames));
    return values.every(finite) ? values.reduce((total, value) => total + value, 0) : null;
}

export function singleRunSnapshot(snapshot, node, plan) {
    if (!plan?.single || plan.seconds <= 60) return snapshot;
    const frames = plan.blocks?.[0]?.frames, id = String(node), output = snapshot.output?.[id];
    if (plan.count !== 1 || !Number.isSafeInteger(frames) || frames <= 0 || !output) return null;
    return {...snapshot, output: {...snapshot.output, [id]: {...output, inputs: {...output.inputs, frames}}}};
}

export function createStudioChain({api, node, onState = () => {}, onProgress = () => {}, onResult = () => {},
    onError = () => {}, pollMs = 2000, now = () => Date.now()}) {
    let chain = null, poller = null, disposed = false, epoch = 0, submitting = false;
    let previous = {}, lastMessage = {}, delivered = null, errorShown = null, corrected = false, etaAt = 0, lastIndex = -1;
    const timers = new Set();
    const clock = {now, setTimeout(fn, ms) { const id = setTimeout(() => { timers.delete(id); fn(); }, ms); timers.add(id); return id; },
        clearTimeout(id) { clearTimeout(id); timers.delete(id); }};
    function clearPolls() { for (const id of timers) clearTimeout(id); timers.clear(); }
    function refresh() { clearPolls(); return poller?.poll(); }
    const active = () => submitting || activeStates.has(chain?.state) || chain?.output_state === 'joining';
    async function json(path, options) {
        const response = await api.fetchApi(path, options);
        if (response.status === 404) return null;
        const value = await response.json();
        if (!response.ok) throw Object.assign(new Error(value.error || 'chain_request_failed'), {code: value.code, status: response.status,
            ...(value.error_zh ? {said: [value.error, value.error_zh]} : {})});
        return value;
    }
    function progress(message = {}, fresh = false) {
        if (!chain || disposed) return;
        if (message.chain?.id && message.chain.id !== chain.id) return;
        const position = chainPosition(chain, message);
        if (position.index !== lastIndex) { lastMessage = {}; lastIndex = position.index; }
        const raw = message.chain?.overall || chain.overall;
        const overall = {...previous};
        if (raw) {
            if (finite(raw.fraction)) overall.fraction = Math.max(previous.fraction || 0, Math.min(.99, raw.fraction));
            if (finite(raw.elapsed_seconds)) overall.elapsed_seconds = Math.max(previous.elapsed_seconds || 0, raw.elapsed_seconds);
            if (finite(raw.remaining_seconds) && (etaAt === 0 || now() - etaAt >= 5000)) {
                const rises = finite(previous.remaining_seconds) && raw.remaining_seconds > previous.remaining_seconds;
                if (!rises || (position.index > 0 && !corrected)) {
                    overall.remaining_seconds = raw.remaining_seconds; etaAt = now(); if (rises) corrected = true;
                }
            }
        }
        if (Object.keys(overall).length) Object.assign(overall, {status: 'running', reset: false, estimated: false, sampling_time_weighted: true});
        previous = overall;
        lastMessage = {...lastMessage, ...message};
        const phase = readChainResult(chain).joining ? 'save'
            : ['complete', 'failed', 'cancelled'].includes(lastMessage.phase) ? 'prepare' : lastMessage.phase || 'prepare';
        onProgress({...lastMessage, phase, result: null, reset: false, new_request: fresh,
            chain: {id: chain.id, ...position}, overall}, chain);
    }
    function deliver(value, fresh = false) {
        if (disposed) return false;
        if (!value?.id) return active();
        chain = value; onState(chain);
        const result = readChainResult(chain);
        if (active()) progress({}, fresh);
        if (result.error && errorShown !== result.error) {
            errorShown = result.error; onError(Object.assign(new Error(result.error), {joining: chain.output_state === 'failed', ...chainPosition(chain)}));
        }
        // Continuing a stopped chain can write the joined video to the path already shown.
        const version = `${chain.blocks?.filter(block => block.state === 'done').length || 0}-${result.joined ? 'joined' : 'part'}`;
        const key = result.video && `${result.video}\n${version}`;
        if (key && delivered !== key) {
            delivered = key;
            const metadata = node.properties?.[CHAIN_PROPERTY] || {};
            onResult({video: result.video, request_id: chain.id, version,
                geometry: {width: metadata.width, height: metadata.height}, report_id: chain.last_report_id}, result, chain);
        }
        return active();
    }
    function watch(fresh = false) {
        poller?.dispose(); clearPolls();
        const id = chain.id, version = epoch;
        poller = createPoller(async () => (await json(chainRoute(id)))?.chain || null,
            value => version === epoch && !disposed ? deliver(value, fresh) : false,
            {interval: pollMs, clock});
    }
    function adopt(value, metadata = null) {
        if (chain?.id !== value.id) { epoch++; previous = {}; lastMessage = {}; delivered = errorShown = null; corrected = false; etaAt = 0; lastIndex = -1; }
        if (metadata) { node.properties ||= {}; node.properties[CHAIN_PROPERTY] = {id: value.id, ...metadata}; node.graph?.change(); }
        if (disposed) return;
        deliver(value, true); if (active()) watch();
    }
    async function recover() {
        const version = epoch;
        try {
            const known = node.properties?.[CHAIN_PROPERTY]?.id;
            const reply = await json('/freevideo/chains');
            if (disposed || version !== epoch || chain) return;
            const found = reply?.chains?.find(row => String(row.node) === String(node.id) && (known ? row.id === known
                : activeStates.has(row.state) && row.client_id && row.client_id === api.clientId));
            if (!found) return;
            const value = (await json(chainRoute(found.id)))?.chain;
            if (disposed || version !== epoch || chain || !value) return;
            adopt(value);
            if (node.freevideoProgress?.chain?.id === value.id) progress(node.freevideoProgress);
        } catch {} // Recovery must never enqueue a request or block ordinary generation.
    }
    recover();
    return {
        get active() { return active(); }, get state() { return chain; },
        async start(snapshot, options) {
            if (disposed || active()) throw new Error('chain_busy');
            submitting = true; epoch++;
            try {
                const body = buildChainRequest(options, {node: node.id, prompt: snapshot.output,
                    extra_data: {extra_pnginfo: {workflow: snapshot.workflow || {}}}, ...(api.clientId ? {client_id: api.clientId} : {})});
                const value = (await json(chainRoute(), post(body)))?.chain;
                if (!value) return null;
                if (!value.id) throw new Error('chain_missing');
                const inputs = snapshot.output[String(node.id)].inputs;
                submitting = false; adopt(value, {width: inputs.width, height: inputs.height, seconds: options.seconds});
                return value;
            } finally { submitting = false; }
        },
        refresh,
        async cancel() {
            if (!chain) return;
            const id = chain.id, version = epoch;
            const value = (await json(chainRoute(id, 'cancel'), post({})))?.chain;
            if (version === epoch && !disposed && value) { epoch++; poller?.dispose(); clearPolls(); deliver(value); if (active()) watch(); }
        },
        async resume() {
            if (!chain || active()) return;
            const id = chain.id, version = epoch;
            const value = (await json(chainRoute(id, 'resume'), post({client_id: api.clientId})))?.chain;
            if (version === epoch && !disposed && value) { epoch++; poller?.dispose(); clearPolls(); previous = {}; corrected = false; etaAt = 0; delivered = errorShown = null; deliver(value, true); if (active()) watch(); }
        },
        async approve() {
            if (!chain) return;
            const id = chain.id, version = epoch;
            const value = (await json(chainRoute(id, 'approve'), post({client_id: api.clientId})))?.chain;
            if (version === epoch && !disposed && value) { epoch++; deliver(value, true); if (active()) watch(); }
        },
        async redo({seed, text} = {}) {
            if (!chain) return;
            const id = chain.id, version = epoch;
            const body = {client_id: api.clientId};
            if (seed !== undefined) body.seed = seed;
            if (text !== undefined) body.text = text;
            const value = (await json(chainRoute(id, 'redo'), post(body)))?.chain;
            if (version === epoch && !disposed && value) { epoch++; poller?.dispose(); clearPolls(); previous = {}; corrected = false; etaAt = 0; delivered = errorShown = null; deliver(value, true); if (active()) watch(); }
        },
        async updateBlock(blockId, {text, seed} = {}) {
            if (!chain) return;
            const id = chain.id;
            const body = {client_id: api.clientId};
            if (text !== undefined) body.text = text;
            if (seed !== undefined) body.seed = seed;
            const value = (await json(chainRoute(id, `block/${encodeURIComponent(blockId)}`), post(body)))?.chain;
            if (value && !disposed) deliver(value);
            return value;
        },
        progress(message) { if (!chain || message.chain?.id !== chain.id) return false; progress(message);
            if (message.result || ['complete', 'failed', 'cancelled'].includes(message.phase)) refresh(); return true; },
        isBlockOutput(result) { return Boolean(chain?.blocks?.some(block => result?.request_id && block.prompt_id === result.request_id || result?.video && block.output === result.video)); },
        dispose() { disposed = true; epoch++; poller?.dispose(); clearPolls(); },
    };
}
