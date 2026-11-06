// The dubbing calls of the editor page, through the engine's agent routes like the sound separation: each one is a job
// read until it ends. The job leaves the project alone (lay: false); the page puts the new sound in itself, so its own
// saves never clash with the job and the change can be undone.
import {agentGet, agentPost, retryAgentPost} from './agent_session.js';

const ROOT = '/freevideo/agent/v1';
const ACTIVE = ['waiting', 'queued', 'running', 'cancelling'];
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const detail = progress => { try { return JSON.parse(progress?.detail || 'null') || {}; } catch { return {}; } };
const failure = (value, fallback) => Object.assign(new Error(value?.code || value?.error?.code || fallback), {code: value?.code || value?.error?.code || fallback, detail: String(value?.message || value?.error?.message || '')});

// What the engine offers for changing a voice: `ready` once the dubbing model is there, `install` where this machine could
// change a voice but the model is still to be downloaded.
export async function voiceFeatures(api) {
    try {
        const response = await agentGet(api, ROOT);
        if (!response.ok) return {ready: false, install: false, unknown: true};
        const features = (await response.json())?.features;
        return {ready: !!features?.['projects.voice'], install: !!features?.['projects.voice.install']};
    } catch { return {ready: false, install: false, unknown: true}; }
}

export async function voiceAvailable(api) { return (await voiceFeatures(api)).ready; }

export function createVoiceStore({api, state, queue}) {
    let running = null;
    const uuid = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    const path = name => `${ROOT}/projects/${encodeURIComponent(state.record.id)}/voice/${name}`;
    const selection = found => ({clip_id: found.clipId, start: found.start, end: found.end});
    async function saved() {
        // A change made a moment ago is queued after its own event; let it be seen before asking.
        for (let round = 0; round < 3; round++) { await new Promise(resolve => setTimeout(resolve, 0)); await queue.flush(); if (!queue.dirty) break; }
        if (queue.dirty || !state.record.id) throw failure(null, 'unsaved');
    }
    async function job(name, body, progress = () => {}) {
        await saved();
        const project = state.record.id, tool = running = {job: null};
        try {
            const response = await retryAgentPost(api, path(name), {revision: state.record.revision, ...body, ref: uuid()});
            const answer = await response?.json().catch(() => null);
            let current = answer?.job || answer?.jobs?.[0];
            if (!response?.ok || !current?.id) throw failure(answer, 'voice_failed');
            tool.job = current.id;
            while (ACTIVE.includes(current.status)) {
                progress({phase: current.progress?.phase, status: current.status, ...detail(current.progress)});
                const reply = await agentGet(api, `${ROOT}/jobs/${encodeURIComponent(tool.job)}?wait=25`);
                if (!reply.ok) { await wait(1000); continue; }
                current = await reply.json();
                if (state.record.id !== project) throw failure(null, 'project_changed');
            }
            if (current.status === 'succeeded' || (current.status === 'cancelled' && current.result)) return current.result;
            throw failure(current.error, current.status === 'cancelled' ? 'cancelled' : 'voice_failed');
        } finally { if (running === tool) running = null; }
    }
    async function sync(name, body) {
        const response = await agentPost(api, path(name), body);
        const answer = await response.json().catch(() => null);
        if (!response.ok) throw failure(answer, 'voice_failed');
        return answer;
    }
    return {
        lines: ({found, language}) => job('lines', {selection: selection(found), ...(language ? {language} : {})}),
        estimate: ({lines}) => sync('estimate', {lines}),
        replace: ({found, lines, recognition, progress}) => job('replace', {selection: selection(found), lines, recognition, lay: false}, info => {
            if (info.phase === 'dubbing') progress({index: info.index, count: info.count, line: info.line, remaining_seconds: info.remaining_seconds});
        }),
        shorten: ({line, target_seconds}) => sync('shorten', {line, target_seconds}),
        cancel: () => { if (running?.job) agentPost(api, `${ROOT}/jobs/${encodeURIComponent(running.job)}/cancel`, {}).catch(() => {}); },
    };
}
