import * as model from './editor_model.js';
import {registerMenuItem} from './editor_menu.js';
import {agentGet, agentPost, retryAgentPost} from './agent_session.js';
import {sourceKey} from './editor_store.js';

const ROOT = '/freevideo/agent/v1';
const ACTIVE = ['waiting', 'queued', 'running', 'cancelling'];
// Answers the separation can end with; anything else is reported by the editor's own error line.
const FAILURES = {
    stems_download_failed: ['The separation model could not be downloaded. Check the network and try again.', '分离模型下载失败，请检查网络后重试。'],
    out_of_memory: ['Not enough graphics memory to separate this sound.', '显存不足，无法分离这段声音。'],
    stems_failed: ['The sound could not be separated. Try again.', '声音分离失败，请重试。'],
    project_changed: ['The edit changed meanwhile. Try again.', '编辑内容已变化，请重试。'],
    clip_busy: ['This clip is still being made.', '这个片段还在生成。'],
    clip_locked: ['This clip is locked.', '这个片段已锁定。'],
    audio_full: ['The tracks for the separated sound are not free here.', '这里没有空闲的分轨位置。'],
};
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const generatedSource = text => {
    const match = /^generated:(.+)$/.exec(String(text || ''));
    if (!match) throw new model.EditError('source');
    return {kind: 'generated', id: match[1]};
};
const children = (record, id) => record.tracks.flatMap(track => track.clips).filter(clip => clip.stem_of === id);

// A sound clip is split into dialogue, music and ambience. The job leaves the project alone (lay: false); this page
// places the three parts itself, so its own saves never clash with the job and the placement can be undone.
export function createStemsIntegration({state, api, queue, store, t, error, notify, show, restore, toolbar}) {
    let running = null, disposed = false, available = false, unsubscribe = null;
    const say = (value, fallback) => {
        const code = value?.code || value?.error?.code, text = FAILURES[code];
        if (text) notify(t(...text)); else error(value?.error || value || new Error(fallback), 'stems');
    };
    const phaseWords = progress => {
        const detail = (() => { try { return JSON.parse(progress?.detail || 'null'); } catch { return null; } })();
        switch (progress?.phase) {
            case 'downloading': return Number.isFinite(detail?.fraction)
                ? t(`Downloading the separation model ${Math.round(detail.fraction * 100)}%`, `正在下载分离模型 ${Math.round(detail.fraction * 100)}%`)
                : t('Downloading the separation model', '正在下载分离模型');
            case 'separating': return t('Separating the sound', '正在分离声音');
            case 'waiting': return t('Waiting for the video to finish', '等待视频生成完成');
            case 'placing': return t('Adding the tracks', '正在放入时间线');
            default: return t('Preparing', '正在准备');
        }
    };
    const paint = (job, stage) => show({id: `stems:${job}`, state: 'running', blocks: [], words: {stage}});
    const parent = context => context.count === 1 && context.clip;
    const splittable = context => {
        const clip = parent(context), track = clip && context.record.tracks.find(row => row.clips.includes(clip));
        return !!clip && track?.kind === 'audio' && !!clip.source && !clip.pending && clip.stem_of === undefined
            && !track.locked && !children(context.record, clip.id).length && !running;
    };
    const stemmed = context => !!parent(context) && children(context.record, context.clip.id).length > 0;
    // The sound clip the toolbar button acts on: the one sound clip inside the selection, whether it was picked alone or with its picture.
    const target = () => {
        const selection = state.selection;
        if (selection?.kind !== 'clip') return null;
        const sound = state.record.tracks.filter(track => track.kind === 'audio')
            .flatMap(track => track.clips.filter(clip => selection.ids.includes(clip.id)).map(clip => ({clip, track})));
        return sound.length === 1 && !!sound[0].clip.source && sound[0].clip.stem_of === undefined ? sound[0] : null;
    };
    let button = null, holder = null;
    const sync = () => {
        if (!button) return;
        const found = available && target();
        button.hidden = holder.hidden = !found;
        if (!found) return;
        const {clip, track} = found;
        button.disabled = !!running || !!clip.pending || track.locked || children(state.record, clip.id).length > 0;
    };
    // The entries exist only where the engine offers the separation, so other computers keep the menu they had.
    let stops = [];
    const capability = (async () => {
        try {
            const response = await api.fetchApi('/freevideo/stems-support', {cache: 'no-store'});
            const value = response.ok ? await response.json() : null;
            available = value?.available === true;
        } catch { available = false; }
        if (available && !disposed && toolbar) {
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('aria-hidden', 'true'); svg.classList.add('fv-ed-glyph');
            const path = document.createElementNS(svg.namespaceURI, 'path');
            path.setAttribute('d', 'M2 8h1.5M5 5v6M8 3v10M11 5.5v5M14 8h-1'); path.setAttribute('fill', 'none'); path.setAttribute('stroke', 'currentColor');
            path.setAttribute('stroke-width', '1.4'); path.setAttribute('stroke-linecap', 'round'); svg.append(path);
            const text = document.createElement('span'); text.className = 'fv-ed-stems-label'; text.textContent = t('Separate tracks', '分离音轨');
            button = document.createElement('button'); button.type = 'button'; button.className = 'fv-action fv-ed-stems-button'; button.hidden = true;
            button.setAttribute('aria-label', t('Separate tracks', '分离音轨')); button.title = t('Separate tracks', '分离音轨'); button.dataset.tourId = 'audio-track-button';
            button.append(svg, text);
            button.onclick = () => { const found = target(); if (found && !button.disabled) void separate(found.clip.id); };
            holder = document.createElement('div'); holder.className = 'fv-ed-tool-group fv-ed-stems-group'; holder.hidden = true; holder.append(button);
            const anchor = toolbar.querySelector('.fv-ed-split')?.closest('.fv-ed-tool-group');
            if (anchor) anchor.after(holder); else toolbar.insertBefore(holder, toolbar.querySelector('.fv-ed-zoom'));
            unsubscribe = state.subscribe(event => { if (event.type === 'selection' || event.type === 'record') sync(); });
            sync();
        }
        if (available && !disposed) stops = [
            registerMenuItem({where: 'part', group: 'sound', label: context => context.t('Separate tracks', '分离音轨'),
                enabled: splittable, tourId: 'audio-track-entry', run: context => { void separate(context.clip.id); }}),
            registerMenuItem({where: 'part', group: 'sound', label: context => context.t('Export stems', '导出分轨'),
                enabled: stemmed, run: context => { void exportStems(context.clip.id); }}),
        ];
    })();
    async function ready() {
        await capability;
        if (!available) { notify(t('Separating audio is not available on this computer.', '这台电脑暂不支持分离音轨。')); return false; }
        await queue.flush();
        if (queue.dirty || !state.record.id) { notify(t('The edit could not be saved yet. Try again.', '编辑内容尚未保存，请稍后重试。')); return false; }
        return true;
    }
    async function separate(clipId) {
        if (running) { notify(t('A sound is already being separated.', '正在分离另一段声音。')); return false; }
        const tool = running = {cancelled: false, job: null}; sync();
        try {
            if (!await ready()) return false;
            const project = state.record.id, ref = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
            const body = {revision: state.record.revision, clip_id: clipId, lay: false, ref};
            paint('start', phaseWords());
            const response = await retryAgentPost(api, `${ROOT}/projects/${encodeURIComponent(project)}/stems`, body);
            const answer = await response?.json().catch(() => null);
            if (!response?.ok || !answer?.job?.id && !answer?.jobs?.[0]?.id) { say(answer, 'stems_failed'); return false; }
            tool.job = (answer.job || answer.jobs[0]).id;
            let job = answer.job || answer.jobs[0];
            while (!disposed && ACTIVE.includes(job.status)) {
                paint(tool.job, tool.cancelled ? t('Stopping…', '正在停止…') : job.status === 'queued' || job.status === 'waiting' ? t('Queued', '排队中') : phaseWords(job.progress));
                const reply = await agentGet(api, `${ROOT}/jobs/${encodeURIComponent(tool.job)}?wait=25`);
                if (!reply.ok) { await wait(1000); continue; }
                job = await reply.json();
            }
            if (disposed || state.record.id !== project) return false;
            if (job.status !== 'succeeded') {
                if (job.status !== 'cancelled' && !tool.cancelled) say(job.error, 'stems_failed');
                return false;
            }
            const sources = Object.fromEntries(model.STEM_TRACKS.map(name => [name, generatedSource(job.result?.sources?.[name])]));
            await store.getSources(Object.values(sources).map(sourceKey));
            if (disposed || state.record.id !== project) return false;
            try {
                state.apply(t('Separate tracks', '分离音轨'), record => model.placeStems(record, clipId, sources));
            } catch (value) {
                if (value instanceof model.EditError) notify(t(...FAILURES[value.code === 'audio_full' ? 'audio_full' : 'stems_failed'])); else error(value, 'stems');
                return false;
            }
            return true;
        } catch (value) {
            if (value?.name !== 'AbortError') say(value, 'stems_failed');
            return false;
        } finally { running = null; sync(); restore(); }
    }
    async function cancel() {
        const tool = running; if (!tool) return false;
        tool.cancelled = true;
        if (tool.job) await agentPost(api, `${ROOT}/jobs/${encodeURIComponent(tool.job)}/cancel`, {}).catch(() => {});
        return true;
    }
    async function exportStems(clipId) {
        try {
            if (!await ready()) return false;
            const body = {revision: state.record.revision, clip_id: clipId, ref: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`};
            const response = await retryAgentPost(api, `${ROOT}/projects/${encodeURIComponent(state.record.id)}/stems/export`, body);
            const answer = await response?.json().catch(() => null);
            if (!response?.ok) { say(answer, 'stems_failed'); return false; }
            notify(t(`Saved the tracks to ${answer.folder}`, `分轨已保存到 ${answer.folder}`));
            return true;
        } catch (value) { say(value, 'stems_failed'); return false; }
    }
    return {separate, cancel, exportStems, active: () => !!running,
        dispose() { disposed = true; stops.forEach(stop => stop()); stops = []; unsubscribe?.(); holder?.remove(); button = holder = null; }};
}
