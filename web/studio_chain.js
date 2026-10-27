import {chainRoute, createPoller} from './chain_support.js';
import {buildChainRequest} from './length_plan.js?v=20261027-length';

export const CHAIN_PROPERTY = 'freevideo_chain';
const activeStates = new Set(['queued', 'running', 'cancelling']);
const MAX_WAITING = 5;
const FPS = 24;
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
    const last = chain.blocks?.filter(block => block.state === 'done' && block.output).at(-1);
    const joined = chain.output_state === 'done' && typeof chain.output === 'string' && Boolean(chain.output);
    const video = joined ? chain.output : !joining && (failedJoin || stopped) ? last?.output || null : null;
    return {video, joined, joining, partial: Boolean(video) && (!joined || stopped),
        error: failedJoin ? chain.output_error || 'join_failed' : chain.error || chain.blocks?.find(block => block.state === 'failed')?.error || null};
}

// Before the first part starts the server writes one description per part; until then no part can be changed.
export function chainSplit(chain) {
    const split = chain?.split;
    return {writing: ['pending', 'running'].includes(split?.state), reason: split?.state === 'failed' ? split.reason || 'failed' : null};
}
// A split part lists only its own events: the continuation sentence and the shared description the server
// repeats in every part stay as they are around an edit (part_words: [start, end] of the part's own words).
function ownWords(chain, index, text) {
    const span = chain?.part_words?.[index];
    return Array.isArray(span) && span.length === 2 && span.every(Number.isInteger)
        && 0 <= span[0] && span[0] < span[1] && span[1] <= text.length ? span : null;
}
function ownText(chain, index, text) {
    const span = ownWords(chain, index, text);
    return span ? text.slice(...span) : text;
}
// Once the video has ended, each part offers one way on: a stopped video continues from its first unfinished part,
// and a finished part, with every part before it done, can be made again as a new video.
const STOPPED = new Set(['failed', 'cancelled', 'interrupted']);
export function chainParts(chain) {
    const writing = chainSplit(chain).writing, blocks = chain?.blocks || [];
    const idle = Boolean(chain) && !activeStates.has(chain.state) && chain.output_state !== 'joining' && !writing;
    const unfinished = blocks.findIndex(block => block.state !== 'done' || !block.output);
    // Where each part begins in the joined video: the parts are joined end to end, each without the opening it repeats.
    const joined = readChainResult(chain)?.joined, kept = Number.isInteger(chain?.output_blocks) ? chain.output_blocks : blocks.length;
    let start = 0;
    const starts = blocks.map(block => { const at = start; start = Number.isInteger(block.new_frames) && start != null ? start + block.new_frames : null; return at; });
    return blocks.map((block, index) => ({id: block.id, index, text: ownText(chain, index, block.inputs?.text ?? ''),
        editable: !writing && block.state === 'pending' && block.prompt_id == null,
        // The seconds this part adds to the video; a continuing part repeats the end of the one before it.
        seconds: Math.max(1, Math.round((block.new_frames ?? block.inputs?.frames ?? 0) / FPS)),
        continues: typeof block.after === 'string' ? 'part' : block.after ? 'video' : null,
        seam: joined && index > 0 && index < kept && typeof block.after === 'string' && starts[index] != null ? starts[index] / FPS : null,
        action: !idle ? null : index === unfinished ? (STOPPED.has(chain.state) ? 'resume' : null)
            : unfinished < 0 || index < unfinished ? 'redo' : null}));
}
export function composePartText(chain, block, words) {
    const index = (chain?.blocks || []).findIndex(row => row.id === block);
    const text = chain?.blocks?.[index]?.inputs?.text ?? '', span = index < 0 ? null : ownWords(chain, index, text);
    return span ? text.slice(0, span[0]) + words + text.slice(span[1]) : words;
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
    let chain = null, poller = null, disposed = false, epoch = 0, submitting = false, promoting = false;
    // Chains the server took while another was running; the next one is shown when the running one stops.
    const waiting = [];
    let previous = {}, lastMessage = {}, delivered = null, errorShown = null, corrected = false, etaAt = 0, lastIndex = -1;
    const timers = new Set();
    const clock = {now, setTimeout(fn, ms) { const id = setTimeout(() => { timers.delete(id); fn(); }, ms); timers.add(id); return id; },
        clearTimeout(id) { clearTimeout(id); timers.delete(id); }};
    function clearPolls() { for (const id of timers) clearTimeout(id); timers.clear(); }
    function refresh() { clearPolls(); return poller?.poll(); }
    const running = () => activeStates.has(chain?.state) || chain?.output_state === 'joining';
    const active = () => submitting || running();
    async function json(path, options, allowMissing = false) {
        const response = await api.fetchApi(path, options);
        if (response.status === 404 && allowMissing) return null;
        const value = await response.json().catch(() => ({}));
        if (!response.ok) throw Object.assign(new Error(value.error || 'chain_request_failed'), {code: value.code, status: response.status,
            ...(value.error_zh ? {said: [value.error, value.error_zh]} : {})});
        return value;
    }
    function progress(message = {}, fresh = false) {
        if (!chain || disposed || (!activeStates.has(chain.state) && chain.output_state !== 'joining')) return;
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
        const phase = readChainResult(chain).joining ? 'save' : chain.split?.state === 'running' ? 'describe'
            : ['complete', 'failed', 'cancelled'].includes(lastMessage.phase) ? 'prepare' : lastMessage.phase || 'prepare';
        onProgress({...lastMessage, phase, result: null, reset: false, new_request: fresh,
            chain: {id: chain.id, ...position, split_failed: chainSplit(chain).reason}, overall}, chain);
    }
    function deliver(value, fresh = false) {
        if (disposed) return false;
        if (!value?.id) return active();
        chain = value; onState(chain);
        const result = readChainResult(chain);
        if (active()) progress({}, fresh);
        else if (waiting.length) void promote();
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
    async function promote() {
        if (promoting || disposed) return;
        promoting = true;
        let again = false;
        try {
            while (waiting.length && !disposed && !running()) {
                const next = waiting[0];
                let value;
                try { value = (await json(chainRoute(next.id), {cache: 'no-store'}, true))?.chain; }
                catch { again = true; break; }
                waiting.shift();
                if (!value?.id || value.state === 'cancelled') continue;
                adopt(value, next.metadata); break;
            }
        } finally { promoting = false; }
        if (again && !disposed) clock.setTimeout(() => void promote(), 3000);
        else if (!disposed && chain) onState(chain);
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
            const reply = await json('/freevideo/chains', undefined, true);
            if (disposed || version !== epoch || chain) return;
            const mine = row => String(row.node) === String(node.id) && activeStates.has(row.state) && row.client_id && row.client_id === api.clientId;
            const found = reply?.chains?.find(row => String(row.node) === String(node.id) && (known ? row.id === known : mine(row)));
            if (!found) return;
            for (const row of reply.chains) if (row.id !== found.id && mine(row)) waiting.push({id: row.id, metadata: null});
            const value = (await json(chainRoute(found.id), undefined, true))?.chain;
            if (disposed || version !== epoch || chain || !value) return;
            adopt(value);
            if (node.freevideoProgress?.chain?.id === value.id) progress(node.freevideoProgress);
        } catch {} // Recovery must never enqueue a request or block ordinary generation.
    }
    function acceptAction(value, restarted) {
        epoch++; poller?.dispose(); clearPolls();
        if (restarted) {
            previous = {}; lastMessage = {}; lastIndex = -1; corrected = false; etaAt = 0;
            delivered = errorShown = null;
        }
        deliver(value, restarted); if (active()) watch();
    }
    async function changeState(action, body) {
        const id = chain.id, version = epoch;
        try {
            const value = (await json(chainRoute(id, action), post(body)))?.chain;
            if (!value?.id) throw new Error('chain_missing');
            if (version === epoch && !disposed) acceptAction(value, action === 'resume');
        } catch (cause) {
            // The action may have reached the server even if its reply failed.
            // Read directly: a stopped chain has no active poller to refresh.
            if (version === epoch && !disposed) {
                try {
                    const latest = (await json(chainRoute(id), {cache: 'no-store'}))?.chain;
                    if (latest?.id === id && version === epoch && !disposed) {
                        const confirmed = action === 'cancel' ? ['cancelling', 'cancelled'].includes(latest.state)
                            : ['queued', 'running'].includes(latest.state) || (latest.state === 'done' && latest.output_state !== 'failed');
                        acceptAction(latest, action === 'resume' && confirmed);
                        if (confirmed) return;
                    }
                } catch {} // Keep the last known state; the action still fails visibly.
            }
            throw Object.assign(new Error(`chain_${action}_failed`), {cause, action, status: cause.status});
        }
    }
    recover();
    return {
        get active() { return active(); }, get state() { return chain; }, get waiting() { return waiting.length; },
        // A chain that is running does not stop the next one from being added behind it.
        get canQueue() { return !disposed && !submitting && running() && chain?.state !== 'cancelling' && waiting.length < MAX_WAITING; },
        async start(snapshot, options, {onAccepted = null} = {}) {
            if (disposed || submitting || (running() && waiting.length >= MAX_WAITING)) throw new Error('chain_busy');
            submitting = true;
            try {
                const body = buildChainRequest(options, {node: node.id, prompt: snapshot.output,
                    extra_data: {extra_pnginfo: {workflow: snapshot.workflow || {}}}, ...(api.clientId ? {client_id: api.clientId} : {})});
                const value = (await json(chainRoute(), post(body), true))?.chain;
                if (!value) return null;
                if (!value.id) throw new Error('chain_missing');
                onAccepted?.();
                const inputs = snapshot.output[String(node.id)].inputs;
                const metadata = {width: inputs.width, height: inputs.height, seconds: options.seconds};
                submitting = false;
                if (running() && chain.id !== value.id) { waiting.push({id: value.id, metadata}); onState(chain); return value; }
                epoch++; adopt(value, metadata);
                return value;
            } finally { submitting = false; }
        },
        refresh,
        // The reply carries the whole chain, so the part shows the words the server keeps.
        async setText(block, text) {
            if (!chain) throw new Error('chain_missing');
            const id = chain.id, version = epoch;
            const value = (await json(chainRoute(id, `blocks/${encodeURIComponent(block)}/text`), post({text})))?.chain;
            if (value?.id === id && version === epoch && !disposed) deliver(value);
            return value;
        },
        // A new video from this part on; the finished one stays in Creations as it was.
        async redo(block) {
            if (!chain || active()) throw new Error('chain_busy');
            const id = chain.id, version = epoch;
            const value = (await json(chainRoute(id, `blocks/${encodeURIComponent(block)}/redo`),
                post(api.clientId ? {client_id: api.clientId} : {})))?.chain;
            if (!value?.id) throw new Error('chain_missing');
            if (version !== epoch || disposed) return value;
            const {id: _, ...metadata} = node.properties?.[CHAIN_PROPERTY] || {};
            epoch++; adopt(value, metadata);
            return value;
        },
        async cancel() {
            if (!chain) return;
            await changeState('cancel', {});
        },
        async resume() {
            if (!chain || active()) return;
            await changeState('resume', {client_id: api.clientId});
        },
        progress(message) { if (!chain || message.chain?.id !== chain.id) return false; progress(message);
            if (message.result || ['complete', 'failed', 'cancelled'].includes(message.phase)) refresh(); return true; },
        isBlockOutput(result) { return Boolean(chain?.blocks?.some(block => result?.request_id && block.prompt_id === result.request_id || result?.video && block.output === result.video)); },
        dispose() { disposed = true; epoch++; poller?.dispose(); clearPolls(); },
    };
}
