// WebSocket updates plus a small snapshot recovery path for reloads/new tabs.
export function createProgressConnection(api, consume, now = () => Date.now()) {
    const seen = new Map();
    let pending = null, timer = null, generation = 0;
    function receive(message) {
        if (!message || message.node == null) return;
        const key = String(message.node), previous = seen.get(key);
        if (Number.isInteger(message.sequence) && previous?.stream_id === message.stream_id
            && previous.sequence >= message.sequence) return;
        // The graph may still be loading. Do not acknowledge an undelivered
        // snapshot; the next poll restores it once the node exists.
        const age = Number.isFinite(message.age_seconds) ? Math.max(0, message.age_seconds) : 0;
        if (consume({...message, received_at: now() - age * 1000}) !== false)
            seen.set(key, message);
    }
    async function refresh() {
        if (pending) return pending.promise;
        const visit = generation, previous = new Map(seen);
        const request = {controller: new AbortController(), promise: null};
        pending = request;
        request.promise = (async () => {
            try {
                const response = await api.fetchApi('/freevideo/progress', {
                    cache: 'no-store', signal: AbortSignal.any([request.controller.signal, AbortSignal.timeout(10000)])});
                if (response.ok) {
                    const messages = (await response.json()).progress || [];
                    if (visit !== generation) return;
                    for (const message of messages) {
                        // A live update received while this poll was in flight
                        // owns the node, even when the server restarted and its
                        // new stream has a lower sequence than the old snapshot.
                        const key = String(message?.node);
                        if (seen.get(key) === previous.get(key)) receive(message);
                    }
                }
            } catch { /* Live WebSocket updates continue across a failed poll. */ }
        })();
        try { await request.promise; }
        finally { if (pending === request) pending = null; }
    }
    function invalidate() {
        generation++;
        const previous = pending; pending = null;
        previous?.controller.abort();
    }
    const event = e => receive(e.detail);
    function start() {
        if (timer !== null) return;
        api.addEventListener('freevideo_progress', event);
        api.addEventListener('reconnected', refresh);
        refresh(); timer = setInterval(refresh, 2000);
    }
    function stop() {
        invalidate();
        if (timer !== null) clearInterval(timer);
        timer = null;
        api.removeEventListener('freevideo_progress', event);
        api.removeEventListener('reconnected', refresh);
    }
    return {start, stop, refresh, receive, reset: () => { invalidate(); seen.clear(); }};
}
