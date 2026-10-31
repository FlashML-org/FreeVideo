import {chainPosition} from './studio_chain.js';
// Data shaping only. Backend identifiers are never guessed from the current job.
export const phoneText = (language = '') => String(language).toLowerCase().startsWith('zh') ? (en, zh) => zh : en => en;
export const activeJob = job => ['running', 'cancelling'].includes(job.status);
export const queuedJob = job => ['waiting', 'queued'].includes(job.status);
export function jobGroups(jobs) {
    const rows = Object.values(jobs).filter(job => ['generate', 'assemble'].includes(job.kind));
    return {running: rows.filter(activeJob), queued: rows.filter(queuedJob).sort((a, b) => (a.progress?.queue_position ?? Infinity) - (b.progress?.queue_position ?? Infinity) || a.created_at - b.created_at),
        done: rows.filter(job => job.status === 'succeeded').sort((a, b) => b.finished_at - a.finished_at || b.created_at - a.created_at)};
}
export function mergePhoneJobs(previous, chat, activity, snapshots = []) {
    const jobs = {...previous};
    for (const row of [...Object.values(chat), ...activity]) {
        const old = jobs[row.id] || {};
        jobs[row.id] = {...old, ...row, progress: {...old.progress, ...row.progress}};
    }
    for (const job of Object.values(jobs)) {
        if (!activeJob(job)) continue;
        const message = snapshots.find(m => m.job_id === job.id || (job.node != null && job.prompt_id && String(m.node) === String(job.node) && m.prompt_id === job.prompt_id));
        if (message) job.progress = {...job.progress, ...message,
            fraction: message.overall?.fraction ?? job.progress?.fraction,
            remaining_seconds: message.overall?.remaining_seconds ?? job.progress?.remaining_seconds};
    }
    return jobs;
}
export function retryWords(seconds, t) {
    const count = Math.ceil(Math.max(0, seconds));
    return count > 90 ? t(`Too many attempts. Try again in ${Math.ceil(count / 60)} min.`, `尝试次数过多，请 ${Math.ceil(count / 60)} 分钟后再试。`)
        : t(`Too many attempts. Try again in ${count} s.`, `尝试次数过多，请 ${count} 秒后再试。`);
}
export function queueWords(index, t) {
    const n = index + 1, mod = n % 100;
    const suffix = mod >= 11 && mod <= 13 ? 'th' : ({1: 'st', 2: 'nd', 3: 'rd'}[n % 10] || 'th');
    return t(index === 0 ? 'Next' : `${n}${suffix}`, `第 ${n} 个`);
}
export function remainingWords(seconds, t) {
    if (!(seconds > 0)) return '';
    return seconds >= 60 ? t(`About ${Math.ceil(seconds / 60)} min left`, `还要约 ${Math.ceil(seconds / 60)} 分钟`) : t('Under 1 min', '不到 1 分钟');
}
export function mediaPath(value, kind = 'video') {
    if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) return '';
    const path = value.split('?')[0];
    return (kind === 'video' ? path === '/freevideo/agent/chat/video' : path === '/view' || /^\/freevideo\/frame-preview\/[^/]+$/.test(path)) ? value : '';
}
export const isIOS = navigator => /iPad|iPhone|iPod/.test(navigator.userAgent || '') || navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1;
export async function saveVideo(job, {navigator: nav = globalThis.navigator, fetch: get = globalThis.fetch, download, File: FileType = globalThis.File} = {}) {
    const path = mediaPath(job.play); if (!path) throw new Error('Video unavailable');
    if (isIOS(nav) && nav.share && FileType) {
        const response = await get(path, {credentials: 'same-origin'});
        if (!response.ok) throw new Error('Video unavailable');
        const file = new FileType([await response.blob()], 'FreeVideo.mp4', {type: 'video/mp4'});
        if (!nav.canShare || nav.canShare({files: [file]})) {
            try { await nav.share({files: [file]}); return 'shared'; }
            catch (error) { if (error.name === 'AbortError') return 'cancelled'; throw error; }
        }
    }
    download(path, 'FreeVideo.mp4'); return 'downloaded';
}

export function applyPhoneChain(job, chain) {
    if (!chain) return job;
    const position = chainPosition(chain), overall = chain.overall || {};
    return {...job, status: chain.state === 'queued' || chain.waiting === true ? 'waiting' : job.status,
        progress: {...job.progress, ...(Number.isFinite(overall.fraction) ? {fraction: overall.fraction} : {}),
            ...(Number.isFinite(overall.remaining_seconds) ? {remaining_seconds: overall.remaining_seconds} : {}),
            block: {index: position.index + 1, total: position.count}, waiting: chain.waiting === true}};
}
export function phonePartWords(job, t) {
    const b = job.progress?.block;
    if (!(b?.total > 1)) return '';
    return job.progress.waiting ? t(`Waiting in queue · Part ${b.index} of ${b.total}`, `正在排队 · 第 ${b.index} 段，共 ${b.total} 段`)
        : t(`Part ${b.index} of ${b.total}`, `第 ${b.index} 段，共 ${b.total} 段`);
}

// Ignore heartbeat-only fields when deciding whether a card needs repainting.
export function phoneJobKey(job = {}) {
    const p = job.progress || {};
    return [job.id, job.kind, job.status, job.label, job.play, job.thumbnail, job.width, job.height, job.node,
        job.seconds, job.finished_at, p.fraction, p.remaining_seconds, p.queue_position, p.block, p.waiting,
        p.frame_preview?.serial, p.frame_preview?.frames];
}

export function longGeneration(value) {
    if (!value || ['succeeded', 'failed', 'cancelled', 'complete'].includes(value.status || value.phase)) return false;
    if (value.status && value.status !== 'running' || ['queued', 'waiting'].includes(value.phase)) return false;
    const p = value.progress || value;
    return Number(p.remaining_seconds ?? p.overall?.remaining_seconds) > 120 || Number(p.elapsed_seconds ?? p.overall?.elapsed_seconds) >= 120;
}
