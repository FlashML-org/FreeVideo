// The AI assistant's tasks as rows of the top bar's run status (one row each, the same as a generation task).
// Nothing is read in the background while no task runs: one read when the page starts, one each time the panel opens
// and every few seconds while it is open or a task runs.
import {retryAgentPost} from './agent_session.js';

const LIVE = {waiting: 'queued', queued: 'queued', running: 'running', cancelling: 'cancelling'};
const FAST = 2500, SLOW = 8000, IDLE = 30000;   // IDLE: only where the assistant is on, so tasks it starts show on the capsule

export function createAgentTasks(api) {
    let data = {jobs: [], unreadable: 0}, enabled = null, timer = 0, wanted = false, reading = null;
    const listeners = new Set();
    const emit = () => { for (const fn of [...listeners]) { try { fn(); } catch { /* A listener must not stop the others. */ } } };
    const rows = () => (data.jobs || []).filter(job => job.status in LIVE).map(job => ({
        id: `agent:${job.id}`, agentId: job.id, source: 'agent', kind: 'agent', view: 'autopilot', cancellable: true,
        status: LIVE[job.status], position: Number(job.progress?.queue_position) || 0,
        percent: Math.max(0, Math.min(1, Number(job.progress?.fraction) || 0)),
        eta_seconds: Number(job.progress?.remaining_seconds) || null,
        job}));
    async function read() {
        try {
            // Only an answer is kept; an engine that is not up yet is asked again at the next read.
            if (enabled === null) { const response = await api.fetchApi('/freevideo/agent', {cache: 'no-store'}); if (response.ok) enabled = (await response.json()).enabled === true; }
            if (!enabled) return;
            const response = await api.fetchApi('/freevideo/agent/activity', {cache: 'no-store'});
            if (response.ok) data = await response.json();
        } catch { /* Keep what was read; the next read tries again. */ }
    }
    async function refresh() {
        clearTimeout(timer); timer = 0;
        reading ||= read().finally(() => { reading = null; });
        await reading; emit();
        const live = rows().length > 0;
        // Not yet answered (the engine was not up) or on: keep asking, slowly when nothing runs. Off: no requests.
        if (wanted || live || enabled !== false) timer = setTimeout(refresh, Math.max(wanted ? FAST : live ? SLOW : IDLE, document.hidden ? IDLE : 0));
    }
    // Coming back to the page reads at once (a task may have been started meanwhile).
    if (typeof window !== 'undefined') window.addEventListener('focus', () => { if (enabled !== false) void refresh(); });
    return {
        rows, refresh,
        failed: () => (data.jobs || []).filter(job => job.status === 'failed'),
        unreadable: () => (Number.isSafeInteger(data.unreadable) && data.unreadable > 0 ? data.unreadable : 0),
        // While the status panel is open the list is read often; closed, only while a task runs.
        watch(on) { wanted = on; if (on) { if (enabled === false) enabled = null; void refresh(); } },
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
        async cancel(id) {
            const response = await retryAgentPost(api, '/freevideo/agent/activity/cancel', {id});
            if (!response?.ok) throw new Error('cancel failed');
            await refresh();
        },
    };
}
