// Music on the second sound track through the generation bar: a stretch chosen there (or a piece of music
// there) is what Generate makes, with the bar's own description box, a style and a mood. The pieces follow the
// music job's progress and take its file in place; the bar shows the job of the piece chosen.
import * as music from './editor_music_model.js';
import {EditError} from './editor_model.js';
import {timeEstimate} from './editor_genbar.js';

export const STYLE_NAMES = {cinematic: ['Cinematic', '电影感'], pop: ['Pop', '流行'], electronic: ['Electronic', '电子'],
    piano: ['Piano', '钢琴'], folk: ['Folk', '民谣'], ambient: ['Ambient', '氛围']};
export const MOOD_NAMES = {calm: ['Calm', '舒缓'], upbeat: ['Upbeat', '明快'], tense: ['Tense', '紧张'], warm: ['Warm', '温暖'], sad: ['Sad', '悲伤']};

// What the bar says about a piece's job: queued, downloading on first use, waiting for a video, or composing.
export function musicBarStatus(job, t, clock = Date.now) {
    if (!job || !['queued', 'running'].includes(job.state)) return null;
    let stage = '', time = '';
    if (job.state === 'queued') stage = t('Queued', '排队中');
    else if (job.phase === 'downloading') stage = t('Downloading files for generation', '正在下载生成所需文件');
    else if (job.phase === 'waiting') stage = t('Queued · waiting for the previous one', '排队中 · 等前一个完成');
    else if (job.phase === 'composing') {
        stage = t('Generating music', '正在生成配乐');
        // The server's elapsed time, carried forward between polls.
        const elapsed = Number.isFinite(job.elapsed) ? job.elapsed + Math.max(0, (clock() - (job.seen_at ?? clock())) / 1000) : null;
        if (Number.isFinite(job.estimate) && elapsed != null) time = elapsed > job.estimate * 1.2 + 5
            ? t('Taking longer than expected', '比预计久，仍在生成') : timeEstimate(Math.max(1, job.estimate - elapsed), t);
    } else stage = t('Preparing', '正在准备');
    return {state: 'running', blocks: [], music: true, words: {stage, ...(time ? {time} : {})}};
}

// A failed piece's sentence on the bar's failure line.
export function musicFailureText(reason, t) {
    return {
        music_download_failed: t('Download paused. Retry to resume.', '下载已暂停，重试可继续。'),
        disk_space: t('The disk is full. Free some space, then generate again.', '磁盘空间不足，请清理出空间后再生成。'),
        out_of_memory: t('Not enough memory to generate the music. Close other programs that use the graphics card, then generate again.', '生成配乐时内存不足，请关闭其他占用显卡的程序后重新生成。'),
        unsupported: t('Music cannot be generated on this computer yet.', '这台电脑暂不能生成配乐。'),
        missing: t('Generation stopped. Generate again.', '生成已中断，请重新生成。'),
    }[reason] || t('The music could not be generated. Generate again.', '无法生成配乐，请重新生成。');
}

export function createEditorMusic({state, store, t, completeSources = async () => {}, onStatus = () => {}, clock = Date.now, random = Math.random}) {
    const statuses = new Map(), watching = new Map();
    let info = null, infoAsked = null, chosen = null, chosenFor = null, disposed = false;
    const target = (selection = state.selection) => music.musicTarget(state.record, selection);
    const refreshInfo = () => infoAsked ??= Promise.resolve().then(() => store.musicInfo()).then(value => { info = value || {available: false}; return info; })
        .catch(() => (info = null)).finally(() => { infoAsked = null; onStatus(); });
    // The style and mood the bar shows: the chosen piece's own, else the edit's last choice; a pick on the bar holds
    // until the choice moves elsewhere.
    function preset(selection = state.selection) {
        const key = JSON.stringify(selection ?? null);
        if (chosen && chosenFor === key) return chosen;
        const found = target(selection);
        return found?.clip?.music && music.MUSIC_STYLES.includes(found.clip.music.style) && music.MUSIC_MOODS.includes(found.clip.music.mood)
            ? {...found.clip.music} : music.lastPreset(state.record);
    }
    function choose(value) {
        chosen = {...preset(), ...value}; chosenFor = JSON.stringify(state.selection ?? null);
    }
    // A piece removed or changed meanwhile is left as it is.
    const settle = change => { try { state.settle(change); } catch (value) { if (!(value instanceof EditError)) throw value; } };
    function deliver(id, jobId, job) {
        if (disposed || !job) return;
        statuses.set(jobId, {...job, seen_at: clock()});
        if (job.state === 'done' && job.result) {
            stopWatching(jobId);
            settle(record => music.finishMusic(record, id, jobId, {source: job.result.source, frames: job.result.frames}));
            completeSources().catch(() => {});
            // The piece just made sets this machine's pace, so the next Generate can show its estimate.
            refreshInfo();
        } else if (['failed', 'cancelled'].includes(job.state)) {
            stopWatching(jobId);
            settle(record => music.failMusic(record, id, jobId, job.state === 'cancelled' ? 'cancelled' : job.reason || 'music_failed'));
        } else settle(record => music.musicProgress(record, id, jobId, job.state));
        onStatus();
    }
    const starting = new Set(); let pressing = false;
    function stopWatching(jobId) { watching.get(jobId)?.(); watching.delete(jobId); }
    function watch(id, jobId) {
        if (watching.has(jobId)) return;
        watching.set(jobId, store.watchMusic(jobId, job => deliver(id, jobId, job), () => deliver(id, jobId, {id: jobId, state: 'failed', reason: 'missing'})));
    }
    async function begin(id, words) {
        state.select({kind: 'clip', ids: [id]});
        chosen = null;
        let job;
        try { job = await store.startMusic(music.musicRequest(state.record, id, {seed: Math.floor(random() * 2 ** 32)})); }
        catch (value) {
            settle(record => music.failMusic(record, id, undefined, value?.reason === 'unsupported' ? 'unsupported' : 'music_failed'));
            onStatus();
            return {say: musicFailureText(value?.reason === 'unsupported' ? 'unsupported' : 'music_failed', t)};
        }
        if (!job?.id) { settle(record => music.failMusic(record, id, undefined)); onStatus(); return {say: musicFailureText('music_failed', t)}; }
        settle(record => music.assignMusicJob(record, id, job.id));
        statuses.set(job.id, {...job, seen_at: clock()});
        watch(id, job.id); onStatus();
        return {text: words};
    }
    const api = {
        target, preset, choose, refreshInfo,
        get info() { return info; },
        styles: () => music.MUSIC_STYLES.map(id => [id, t(...STYLE_NAMES[id])]),
        moods: () => music.MUSIC_MOODS.map(id => [id, t(...MOOD_NAMES[id])]),
        // The chosen piece is being made.
        running(selection = state.selection) {
            const found = target(selection);
            return !!found?.clip && (starting.has(found.clip.id) || ['queued', 'running'].includes(found.clip.pending));
        },
        // The bar's status for the chosen piece's job; null when it has none running.
        barStatus(selection = state.selection) {
            const found = target(selection), job = found?.clip?.job && statuses.get(found.clip.job);
            return job && ['queued', 'running'].includes(found.clip.pending) ? musicBarStatus(job, t, clock) : null;
        },
        // The words the box shows for a piece of music; null for a stretch not made yet.
        text(selection = state.selection) {
            const found = target(selection);
            return found?.clip && !api.running(selection) ? {ids: [found.clip.id], text: found.clip.text} : null;
        },
        describe(id, text) {
            const found = target({kind: 'clip', ids: [id]});
            if (!found?.clip || api.running({kind: 'clip', ids: [id]}) || found.clip.text === text) return false;
            state.apply(t('Edit description', '修改说明'), record => {
                const next = JSON.parse(JSON.stringify(record));
                next.tracks.find(track => track.id === music.MUSIC_TRACK).clips.find(clip => clip.id === id).text = text;
                return next;
            });
            return true;
        },
        failure(selection = state.selection) {
            const found = target(selection);
            return found?.clip?.pending === 'failed' ? {id: found.clip.id, text: musicFailureText(found.clip.reason, t), report: false} : null;
        },
        // Seconds the piece will take: this machine's pace from its last piece; unknown before the first.
        estimate(selection = state.selection) {
            const found = target(selection);
            return found && Number.isFinite(info?.pace) ? info.pace * found.frames / 24 : null;
        },
        // First-use download for the bar's "Downloads before generating" line: bytes still to fetch, or 0.
        async downloadBytes() {
            const pack = await Promise.resolve().then(() => store.musicPack()).catch(() => null);
            return pack && !pack.ready && !pack.running ? pack.bytes || 0 : 0;
        },
        // Generate on a stretch of the music track, or again on a piece there. {say} answers the press.
        // A second press while the first is still checking or starting its job is the same press.
        async press(text) {
            const found = target();
            if (!found) return null;
            if (pressing || api.running()) return {say: t('This part is generating.', '这一段正在生成。')};
            pressing = true;
            let id = found.id, words;
            try {
                const current = info || await refreshInfo();
                if (current && current.available === false) return {say: musicFailureText('unsupported', t)};
                const pack = await Promise.resolve().then(() => store.musicPack()).catch(() => null);
                if (pack && !pack.ready && pack.disk_ok === false) return {say: musicFailureText('disk_space', t)};
                const pick = preset(); words = text.trim();
                state.apply(t('Generate', '生成'), record => {
                    const next = music.rememberPreset(record, pick);
                    if (id) return music.remakeMusic(next, id, {text: words, ...pick});
                    const planned = music.planMusic(next, {start: found.start, frames: found.frames, text: words, ...pick});
                    id = planned.id; return planned.record;
                }, {selectionAfter: null});
                starting.add(id);
            } finally { pressing = false; }
            try { return await begin(id, words); } finally { starting.delete(id); }
        },
        async cancel(selection = state.selection) {
            const found = target(selection);
            if (!found?.clip?.job || !api.running(selection)) return false;
            deliver(found.clip.id, found.clip.job, await store.cancelMusic(found.clip.job).then(value => value?.job || value));
            return true;
        },
        // Pieces still waiting for their job when the edit opens again.
        recover() { for (const {id, job} of music.musicJobs(state.record)) watch(id, job); },
        reset() { for (const jobId of [...watching.keys()]) stopWatching(jobId); statuses.clear(); chosen = null; },
        dispose() { disposed = true; api.reset(); },
    };
    return api;
}
