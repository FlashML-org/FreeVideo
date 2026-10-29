// A message is stale when the same stream already delivered an equal or later sequence.
export function isStaleProgress(previous, message) {
    return Number.isInteger(message?.sequence) && Number.isInteger(previous?.sequence) && previous.stream_id === message.stream_id
        && previous.sequence >= message.sequence;
}
// WebSocket updates plus a small snapshot recovery path for reloads/new tabs.
export function createProgressConnection(api, consume, now = () => Date.now()) {
    const seen = new Map();
    let pending = null, timer = null;
    function receive(message) {
        if (!message || message.node == null) return;
        const identity = message.task_id || message.prompt_id;
        const key = identity ? JSON.stringify([identity, String(message.node)]) : String(message.node), previous = seen.get(key);
        if (isStaleProgress(previous, message)) return;
        // The graph may still be loading. Do not acknowledge an undelivered
        // snapshot; the next poll restores it once the node exists.
        const age = Number.isFinite(message.age_seconds) ? Math.max(0, message.age_seconds) : 0;
        // A heartbeat refreshes only the estimate: it is younger than the engine message.
        const overallAge = Number.isFinite(message.overall_age_seconds) ? Math.max(0, message.overall_age_seconds) : age;
        if (consume({...message, received_at: now() - age * 1000, overall_received_at: now() - overallAge * 1000}) !== false)
            seen.set(key, message);
    }
    async function refresh() {
        if (pending) return pending;
        pending = (async () => {
            try {
                const response = await api.fetchApi('/freevideo/progress', {
                    cache: 'no-store', signal: AbortSignal.timeout(10000)});
                if (response.ok) for (const message of (await response.json()).progress || []) receive(message);
            } catch { /* Live WebSocket updates continue across a failed poll. */ }
        })();
        try { await pending; } finally { pending = null; }
    }
    const event = e => receive(e.detail);
    function start() {
        if (timer !== null) return;
        api.addEventListener('freevideo_progress', event);
        api.addEventListener('reconnected', refresh);
        refresh(); timer = setInterval(refresh, 2000);
    }
    function stop() {
        if (timer !== null) clearInterval(timer);
        timer = null;
        api.removeEventListener('freevideo_progress', event);
        api.removeEventListener('reconnected', refresh);
    }
    return {start, stop, refresh, receive, reset: () => seen.clear()};
}
