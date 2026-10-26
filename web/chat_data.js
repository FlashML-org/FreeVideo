import {agentGet, agentPost, retryAgentPost} from './agent_session.js';
export const SEND_ATTEMPTS = 3, WAIT_FOR_AGENT = ['not_connected', 'not_listening'];
// A listening/connection race is queued; other 4xx refusals never repeat automatically.
export const sendRefused = (error, response) => error?.code === 'session_full' || error?.retryable === false
    || response?.status >= 400 && response.status < 500 && !WAIT_FOR_AGENT.includes(error?.code);
export async function sendError(response) {
    if (!response) return null;
    try { return (await (response.clone?.() || response).json())?.error || null; }
    catch { return null; }
}
export const emptyChat = () => ({session: undefined, items: new Map(), jobs: {}, failures: {}, confirms: {}, last: 0, read: 0, agent: {}, current: null, active: 0, abandoned: false, stage: null});
// One answer from the server folded into what the page already has: entries add up by their number, jobs are
// replaced by id (a card keeps its last state after the server stops listing its job), and so are the answers to its questions.
export function chatMerge(state, data) {
    const items = new Map(state.items);
    for (const item of data.items || []) if (['message', 'job', 'confirm', 'production', 'cast'].includes(item.type) && Number.isFinite(item.seq)) items.set(item.seq, item);
    const read = Math.max(state.read, Number(data.read_seq) || 0, ...(data.items || []).filter(item => item.read === true).map(item => item.seq));
    return {session: data.session ?? null, items, jobs: {...state.jobs, ...(data.jobs || {})}, failures: data.failures || {}, confirms: {...state.confirms, ...(data.confirms || {})}, read,
        last: Math.max(state.last, Number(data.last_seq) || 0, ...items.keys()), agent: data.agent || {},
        current: data.current || null, active: Number(data.active) || 0,
        abandoned: data.abandoned === true, stage: typeof data.stage === 'string' ? data.stage : null};
}
// The entries in order. A user's sentence counts as taken once the agent has read it or has said or done anything after it.
export function chatEntries(state) {
    const list = [...state.items.values()].sort((a, b) => a.seq - b.seq);
    let answered = 0;
    for (const item of list) if (item.type !== 'message' || item.role !== 'user') answered = item.seq;
    return list.map(item => item.type === 'message' && item.role === 'user'
        ? {...item, taken: item.read === true || item.seq <= state.read || item.seq < answered} : item);
}
// Whether a sentence sent now reaches anyone: the agent is waiting for one, or is at work and will come back for it.
export const canSend = state => state.agent?.connected === true && (state.agent.listening !== false || state.active > 0);

export function newId() {
    const bytes = crypto.getRandomValues(new Uint8Array(12));
    return 'm-' + [...bytes].map(b => b.toString(36).padStart(2, '0')).join('').slice(0, 20);
}



// The cursor is scoped to its conversation; switching always starts at zero.
export function chatReadRequest(state, session) {
    return {session, after: state.session && (!session || session === state.session) ? state.last : 0};
}
// A send's id and attempt budget survive an uncertain response. Presentation can
// acknowledge the same entry from a concurrent poll before this request returns.
// Only an explicit field-validation refusal is safe to resend without the optional field.
// An older backend that ignores it already succeeds; unrelated errors keep the existing retry policy.
export async function previewUnsupported(response, field) {
    if (!field || ![400, 422].includes(response?.status)) return false;
    try {
        const value = await response.clone().json(), error = value.error;
        // The current server redacts unknown field names. The only boolean true we add is this flag.
        const detail = error?.details?.[0];
        if (error?.code === 'invalid_request' && error.details?.length === 1 && detail?.path === '/<unknown>'
            && detail.allowed === 'absent (unknown field)' && detail.got === 'True') return true;
        if (Array.isArray(value.detail) && value.detail.some(item => item.type === 'extra_forbidden'
            && item.loc?.includes(field))) return true;
        const named = error?.field === field || error?.message?.includes(field);
        return !!named && (['unknown_field', 'unsupported_field', 'unsupported_parameter', 'extra_forbidden'].includes(error?.code)
            || /unknown|unsupported|unexpected|unrecognized|not supported|extra field/i.test(error?.message || ''));
    } catch { return false; }
}
export async function sendChatMessage(api, entry, {sessionSupported = true, stopped = () => false, previewField = null} = {}) {
    const body = {id: entry.id, text: entry.text};
    if (entry.attachments?.length) body.attachments = entry.attachments.map(({preview, ...rest}) => rest);
    if (sessionSupported && entry.session) body.session = entry.session;
    if (entry.whiteboxPreview && previewField) body[previewField] = true;
    entry.attempts = (entry.attempts || 0) + 1;
    return retryAgentPost(api, '/freevideo/agent/chat', body, {retry: async response => {
        if (!stopped() && !entry.acknowledged && entry.whiteboxPreview && entry.attempts < SEND_ATTEMPTS && await previewUnsupported(response, previewField)) {
            entry.whiteboxPreview = false; delete body[previewField];
            entry.attempts++; return body;
        }
        if (stopped() || entry.acknowledged || sendRefused(await sendError(response), response) || entry.attempts >= SEND_ATTEMPTS) return null;
        entry.attempts++; return body;
    }});
}

// DOM-free transport and state folds shared by desktop and phone. Rendering,
// drafts and visibility decisions remain with the owning view.
export function chatData(api) {
    const read = (path, options) => agentGet(api, path, options);
    return {
        read({session, after, signal} = {}) {
            const query = new URLSearchParams();
            if (session) query.set('session', session);
            if (after) query.set('after', String(after));
            return read('/freevideo/agent/chat' + (query.size ? `?${query}` : ''), {signal});
        },
        now: options => read('/freevideo/agent/chat/now', options),
        list: options => read('/freevideo/agent/chat/list', options),
        create: () => agentPost(api, '/freevideo/agent/chat/new', {}),
        remove: (session, stop = false) => agentPost(api, '/freevideo/agent/chat/delete', stop ? {session, stop: true} : {session}),
        rename: (session, title) => agentPost(api, '/freevideo/agent/chat/rename', {session, title}),
        confirm: body => agentPost(api, '/freevideo/agent/chat/confirm', body),
        abandon: session => agentPost(api, '/freevideo/agent/chat/abandon', session ? {session} : {}),
        cancel: id => retryAgentPost(api, '/freevideo/agent/activity/cancel', {id}),
        activity: (cursor, options) => read('/freevideo/agent/activity' + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''), options),
        sendMessage: (entry, options) => sendChatMessage(api, entry, options),
        send: (body, options) => retryAgentPost(api, '/freevideo/agent/chat', body, options),
        video: work => '/freevideo/agent/chat/video?id=' + encodeURIComponent(work),
    };
}
export function chatDelay({hidden = false, phone = false, idle = false, interval = 1500, failures = 0} = {}) {
    // A page nobody is looking at asks less often; it only has to notice what the tab title announces.
    if (phone) return hidden ? 10000 : 2000;
    return Math.max(hidden ? 10000 : 0, idle ? Math.max(interval, 3000) : interval) * Math.min(4, 1 + failures);
}
// Serial polling, visibility rescheduling and disposal work without a browser.
export function chatPoller(read, {delay = () => 2000, error = () => {}, clock = globalThis} = {}) {
    let timer = null, running = null, disposed = false, again = false;
    function poll() {
        clock.clearTimeout(timer); timer = null;
        if (disposed) return Promise.resolve();
        if (running) { again = true; return running; }
        running = Promise.resolve().then(read).catch(error).finally(() => {
            running = null;
            if (!disposed) { const ms = again ? 0 : delay(); again = false; timer = clock.setTimeout(poll, ms); }
        });
        return running;
    }
    return {poll, reschedule() { clock.clearTimeout(timer); if (!disposed && !running) timer = clock.setTimeout(poll, delay()); },
        dispose() { disposed = true; clock.clearTimeout(timer); }};
}
