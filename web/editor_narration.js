// A new line said in a voice of the edit, through the generation bar: an empty stretch of the recording track (or of
// the separated voice track) chosen there is where Generate puts it. The bar shows a voice to choose (the speakers of
// the edit, its own sound clips, or a clip picked on the timeline) and the description box takes the words. The line
// is planned in place, follows its job and takes the new sound when it is made, as a piece of music does.
import * as said from './editor_narration_model.js';
import {EditError, FPS} from './editor_model.js';
import {registerMenuItem} from './editor_menu.js';

const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const NBSP = ' ';
const SVG = 'http://www.w3.org/2000/svg';
// A take the recording bar saved: "Recording 2026-11-08 101500.wav" in either language.
const RECORDING = /^(Recording|录音) \d{4}-\d{2}-\d{2} \d{6}\.wav$/;
const clock = seconds => { const whole = Math.max(0, Math.round(seconds)); return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`; };
// The bar's pace for this machine: seconds of work per second of line, from the last line made.
const PACE_KEY = 'fv-editor-say-pace';
const readPace = storage => { try { const value = Number(storage?.getItem(PACE_KEY)); return value > 0 && value < 1000 ? value : null; } catch { return null; } };

// What the bar says while a line is made.
export function narrationBarStatus(job, t) {
    if (!job || !['queued', 'running', 'waiting'].includes(job.status)) return null;
    const stage = job.status !== 'running' ? t('Queued', '排队中')
        : job.phase === 'recognizing' ? t('Finding the lines', '正在识别台词')
        : job.phase === 'dubbing' ? t('Generating the voice', '正在生成配音') : t('Preparing', '正在准备');
    return {state: 'running', blocks: [], music: true, words: {stage}};
}
// A failed line's sentence on the bar's failure line.
export function narrationFailureText(reason, t) {
    return {
        out_of_memory: t('Not enough memory to generate the voice. Close other programs that use the graphics card, then generate again.', '生成配音时内存不足，请关闭其他占用显卡的程序后重新生成。'),
        storage_unavailable: t('The disk is full. Free some space, then generate again.', '磁盘空间不足，请清理出空间后再生成。'),
        no_speech: t('No speech was heard in this voice. Choose another voice.', '这个音色里没有听到说话声，请换一个音色。'),
        no_speech_model: t('The speech model could not be used, so the voice could not be generated.', '语音识别模型无法使用，未能生成配音。'),
        voice_model_missing: t('The dubbing model is not fully installed. Generate again to download it.', '配音模型没有装全，重新生成会再下载。'),
        missing: t('Generation stopped. Generate again.', '生成已中断，请重新生成。'),
    }[reason] || t('The voice could not be generated. Generate again.', '无法生成配音，请重新生成。');
}
// How much longer than the stretch a line came out, as the bar says it: one decimal, a no-break space before the unit.
const longer = (seconds, t) => { const value = (Math.round(seconds * 10) / 10).toFixed(1); return t(`${value}${NBSP}s`, `${value}${NBSP}秒`); };
export const placedLongerText = (seconds, t) => t(`${longer(seconds, t)} longer than the selection. Placed at full length.`, `比选区长 ${longer(seconds, t)}，已按完整长度放入。`);
export const blockedLongerText = (seconds, t) => t(`${longer(seconds, t)} longer than the selection and would cover the next clip. Shorten the line or extend the selection.`,
    `比选区长 ${longer(seconds, t)}，会盖住后面的片段。请缩短这句或拉长选区。`);

/**
 * state, t: the editor's. store: the dubbing calls (editor_voice_store). sources(): the source metadata map.
 * available(): the engine can say lines here now or after a download; ready(): the dubbing model is there;
 * install: the dubbing model's download (editor_voice_install); play({clip, start, end}, onEnd) plays a stretch of a
 * clip's sound and returns how to stop it; say(text) answers on the bar; completeSources() reads the new file in.
 */
export function createEditorNarration({state, store, t, sources = () => new Map(), available = () => false, ready = () => false, install = null,
    play = () => () => {}, say = () => {}, completeSources = async () => {}, onStatus = () => {}, storage = globalThis.localStorage, delay = ms => new Promise(r => setTimeout(r, ms))}) {
    const statuses = new Map(), owners = new Map(), heard = new Map(), failures = new Map();
    let chosen = null, picking = null, pickedClip = null, stopListening = null, listeningKey = null, pressing = false, disposed = false;
    let pace = readPace(storage);
    // The history step of each line laid on an empty stretch, while it is made.
    const planned = new Map();
    const emit = () => { if (!disposed) onStatus(); };
    const target = (selection = state.selection) => available() && state.record ? said.narrationTarget(state.record, selection) : null;
    const clips = () => state.record.tracks.flatMap(track => track.clips.map(clip => ({clip, track})));
    const settle = change => { try { state.settle(change); } catch (value) { if (!(value instanceof EditError)) throw value; } };
    const metaOf = clip => clip?.source ? sources().get(`${clip.source.kind}:${clip.source.id}`) : null;

    // The edit's speech, for its speakers: the separated voice when there is one, else the sound of the picture;
    // the clip nearest the stretch is heard, once for each version of it.
    function speechClip(found) {
        const speech = state.record.tracks.find(track => track.stem === 'dialogue')?.clips.filter(clip => clip.source && !clip.speed && !clip.reverse)
            || [];
        const pool = speech.length ? speech : (state.record.tracks.find(track => track.id === 'a1')?.clips || []).filter(clip => clip.source && !clip.speed && !clip.reverse);
        let best = null, away = Infinity;
        for (const clip of pool) {
            const distance = Math.max(0, clip.start - (found.start + found.frames), found.start - (clip.start + clip.frames));
            if (distance < away) { away = distance; best = clip; }
        }
        return best;
    }
    const heardKey = clip => `${clip.id}:${clip.source.kind}:${clip.source.id}:${clip.in}:${clip.frames}`;
    function hear(found) {
        const clip = speechClip(found); if (!clip) return null;
        const key = heardKey(clip);
        if (heard.has(key)) return heard.get(key);
        const entry = {key, clip, phase: 'finding', lines: [], own: {job: null}};
        heard.set(key, entry); emit();
        store.hear({found: {clipId: clip.id, start: 0, end: clip.frames / FPS}, own: entry.own}).then(result => {
            const base = clip.start / FPS;
            entry.lines = (result?.lines || []).filter(line => Number.isFinite(line.speaker) && line.end > line.start)
                .map(line => ({clip: clip.id, speaker: line.speaker, start: line.start, end: line.end, text: line.text || '', at: base + line.start, atEnd: base + line.end}));
            entry.phase = 'done';
        }, () => {
            // Speakers are left out when they cannot be heard; the clips of the edit still are voices.
            entry.phase = 'failed';
        }).finally(emit);
        return entry;
    }
    // The voices for a stretch, in the bar's order: speakers, then the edit's sound clips, then a clip picked
    // on the timeline that is none of them.
    function voices(found = target()) {
        if (!found) return {speakers: [], clips: [], finding: false};
        const entry = hear(found);
        const speakers = entry?.phase === 'done' ? said.speakerVoices(entry.lines).map(voice => ({...voice, label: t(`Speaker ${voice.number}`, `说话人 ${voice.number}`)})) : [];
        const list = [];
        let takes = 0;
        for (const {clip, track} of clips().sort((a, b) => a.clip.start - b.clip.start)) {
            if (track.kind !== 'audio' || !clip.source || clip.pending || clip.speed || clip.reverse || track.stem) continue;
            // The sound of a picture, music and lines made here are not voices to choose.
            if (clip.link || /^freevideo-(music|voice)\//.test(clip.source.id)) continue;
            const name = metaOf(clip)?.name;
            if (clip.source.kind !== 'import' || typeof name !== 'string' || !name) continue;
            const take = RECORDING.test(name) ? ++takes : 0;
            const label = take ? t(`Recording ${take}`, `录音 ${take}`) : name;
            list.push({key: `clip:${clip.id}`, kind: 'clip', label, reference: {clip_id: clip.id},
                listen: {clip: clip.id, start: 0, end: Math.min(clip.frames / FPS, 10)}});
        }
        const picked = pickedClip && state.record.tracks.some(track => track.clips.some(clip => clip.id === pickedClip.id && clip.source)) ? pickedClip : null;
        if (picked && !list.some(voice => voice.key === `clip:${picked.id}`)) {
            const clip = clips().find(item => item.clip.id === picked.id).clip;
            list.push({key: `clip:${clip.id}`, kind: 'clip', label: metaOf(clip)?.name || t(`Sound at ${clock(clip.start / FPS)}`, `${clock(clip.start / FPS)} 处的声音`),
                reference: {clip_id: clip.id}, listen: {clip: clip.id, start: 0, end: Math.min(clip.frames / FPS, 10)}});
        }
        return {speakers, clips: list, finding: entry?.phase === 'finding', lines: entry?.lines || []};
    }
    // The voice the bar shows: the one chosen while it is still there, else the speaker nearest the stretch, else the
    // first sound clip.
    function voice(found = target()) {
        const all = voices(found), every = [...all.speakers, ...all.clips];
        return every.find(item => item.key === chosen) || said.nearestSpeaker(all.speakers, all.lines, found ? found.start / FPS : 0) || all.clips[0] || null;
    }
    function choose(key) { chosen = key; stopListen(); emit(); }

    // Picking a clip on the timeline: the next sound clip clicked becomes the voice; the stretch stays chosen.
    function pick() { if (!target()) return; picking = state.selection; stopListen(); emit(); }
    function stopPicking() { if (!picking) return false; picking = null; emit(); return true; }
    const unsubscribe = state.subscribe(event => {
        if ((event.type ?? event) !== 'selection' || !picking) return;
        const selection = state.selection, back = picking;
        if (selection === back) return;
        picking = null;
        if (selection?.kind === 'clip') {
            const sound = clips().find(({clip, track}) => selection.ids.includes(clip.id) && track.kind === 'audio' && clip.source && !clip.pending);
            if (sound) {
                pickedClip = {id: sound.clip.id}; chosen = `clip:${sound.clip.id}`;
                state.select(back);
                // The timeline chooses the clip again when the press ends: the stretch comes back once more then.
                const since = Date.now(), again = () => setTimeout(() => {
                    if (Date.now() - since < 2000 && state.selection?.kind === 'clip' && state.selection.ids.includes(sound.clip.id)) state.select(back);
                }, 0);
                globalThis.addEventListener?.('pointerup', again, {once: true, capture: true});
                return;
            }
        }
        emit();
    });

    // Hearing the voice: its longest line, or the first seconds of its clip; a second press stops.
    function stopListen() { stopListening?.(); stopListening = null; listeningKey = null; }
    function listen(found = target()) {
        const current = voice(found);
        if (!current) return;
        if (listeningKey === current.key) { stopListen(); emit(); return; }
        stopListen();
        listeningKey = current.key;
        stopListening = play(current.listen, () => { if (listeningKey === current.key) { stopListening = null; listeningKey = null; emit(); } });
        emit();
    }

    function deliver(id, jobId, job) {
        if (disposed || !job) return;
        statuses.set(jobId, job);
        settle(record => said.narrationProgress(record, id, jobId, job.status === 'running' ? 'running' : 'queued'));
        emit();
    }
    // The new sound goes in place; a line longer than its stretch grows to its whole length, unless it would then
    // cover the next clip: then it is taken out, and the stretch and its words are chosen again.
    function finish(id, jobId, result, text) {
        const frames = said.linesFrames(result.seconds), before = said.narrationTarget(state.record, {kind: 'clip', ids: [id]});
        if (!before) return;
        const fit = said.narrationFit(state.record, id, frames);
        const over = Math.max(0, result.seconds - before.frames / FPS);
        const step = planned.get(id);
        planned.delete(id);
        if (!fit.fits) {
            // Still the newest step: taken back as if never pressed, so Undo has nothing empty to undo.
            if (!(step != null && state.withdraw?.(step))) settle(record => said.dropNarration(record, id));
            state.select({kind: 'range', tracks: [before.track], start: before.start, frames: before.frames});
            state.setRangeText?.(text);
            say(blockedLongerText(over, t), text);
            return;
        }
        settle(record => said.finishNarration(record, id, jobId, {source: result.source, frames}));
        completeSources().catch(() => {});
        if (fit.over > 0) say(placedLongerText(over, t));
        if (Number.isFinite(result.seconds_taken) && result.seconds > 0) {
            pace = result.seconds_taken / result.seconds;
            try { storage?.setItem(PACE_KEY, String(pace)); } catch { /* the estimate waits for the next line */ }
        }
    }
    function failed(id, jobId, value) {
        const code = value?.code === 'cancelled' ? 'cancelled' : value?.code || 'voice_failed';
        settle(record => said.failNarration(record, id, jobId, code));
        emit();
    }
    // Waits for the dubbing model when it is still to be downloaded; false when it cannot be had.
    async function modelReady() {
        if (ready()) return true;
        if (!install?.offered?.()) return false;
        install.start();
        while (!disposed && !ready()) {
            if (['failed', 'unsupported'].includes(install.view?.phase) || install.view?.phase === 'idle' && !install.hasJob?.()) return ready();
            await delay(500);
        }
        return ready();
    }
    async function run(id, text, reference) {
        const own = {job: null};
        owners.set(id, own);
        try {
            const result = await store.say({text, seconds: said.narrationTarget(state.record, {kind: 'clip', ids: [id]}).frames / FPS, reference, own,
                progress: info => {
                    if (own.job && !owners.get(id)?.assigned) { owners.get(id).assigned = true; settle(record => said.assignNarrationJob(record, id, own.job)); }
                    deliver(id, own.job, info);
                }});
            if (own.job && !own.assigned) settle(record => said.assignNarrationJob(record, id, own.job));
            finish(id, own.job ?? undefined, result, text);
        } catch (value) { failed(id, own.job ?? undefined, value); }
        finally { owners.delete(id); if (own.job) statuses.delete(own.job); emit(); }
    }

    const api = {
        target, voices, voice, choose, pick, stopPicking, listen,
        get picking() { return !!picking; },
        listening: () => !!listeningKey,
        // The line being made.
        running(selection = state.selection) {
            const found = target(selection);
            return !!found?.clip && (owners.has(found.clip.id) || ['queued', 'running'].includes(found.clip.pending));
        },
        barStatus(selection = state.selection) {
            const found = target(selection), own = found?.clip && owners.get(found.clip.id);
            if (!found?.clip || !['queued', 'running'].includes(found.clip.pending) && !own) return null;
            return narrationBarStatus(statuses.get(found.clip.job || own?.job) || {status: 'queued'}, t);
        },
        // The words a planned line shows in the box.
        text(selection = state.selection) {
            const found = target(selection);
            return found?.clip && !api.running(selection) ? {ids: [found.clip.id], text: found.clip.text} : null;
        },
        describe(id, text) {
            const found = target({kind: 'clip', ids: [id]});
            if (!found?.clip || api.running({kind: 'clip', ids: [id]}) || found.clip.text === text || !text.trim()) return false;
            state.apply(t('Edit description', '修改说明'), record => said.remakeNarration(record, id, text));
            return true;
        },
        failure(selection = state.selection) {
            const found = target(selection);
            return found?.clip?.pending === 'failed' ? {id: found.clip.id, text: narrationFailureText(found.clip.reason, t), report: false} : null;
        },
        // Seconds the line will take: this machine's pace from its last line; unknown before the first.
        estimate(selection = state.selection) {
            const found = target(selection);
            return found && pace ? pace * found.frames / FPS : null;
        },
        // Bytes still to download before the first line, for the bar's "Downloads before generating" line.
        downloadBytes: () => !ready() && install?.offered?.() ? install.view?.bytes || 0 : 0,
        downloading: () => !ready() && install?.view?.phase === 'downloading' ? install.view : null,
        // Generate on an empty stretch, or again on a planned line there, in the voice shown.
        async press(text) {
            const found = target();
            if (!found) return null;
            if (pressing || api.running()) return {say: t('This part is generating.', '这一段正在生成。')};
            const words = text.trim(), current = voice(found);
            if (!words || !current) return null;
            pressing = true;
            let id = found.id;
            try {
                if (!await modelReady()) return {say: narrationFailureText('voice_model_missing', t)};
                if (disposed) return null;
                const again = target();
                if (!again || again.start !== found.start || again.track !== found.track) return null;
                // The same plan made on the same record names the same clip: the undo entry chooses it.
                const plan = record => said.planNarration(record, {track: found.track, start: found.start, frames: found.frames, text: words});
                if (!id) id = plan(state.record).id;
                state.apply(t('Narration', '配音'), record => found.id ? said.remakeNarration(record, id, words) : plan(record).record,
                    {selectionAfter: {kind: 'clip', ids: [id]}});
                if (!found.id && Number.isFinite(state.step)) planned.set(id, state.step);
                stopListen();
            } finally { pressing = false; }
            run(id, words, current.reference);
            emit();
            return {text: words};
        },
        async cancel(selection = state.selection) {
            const found = target(selection), job = found?.clip && (found.clip.job || owners.get(found.clip.id)?.job);
            if (!job) return false;
            store.stop(job);
            return true;
        },
        // Lines still waiting for their job when the edit opens again.
        recover() {
            for (const {id, job} of said.narrationJobs(state.record)) {
                if (owners.has(id)) continue;
                const own = {job, assigned: true}, clip = clips().find(item => item.clip.id === id)?.clip;
                owners.set(id, own);
                store.follow(job, info => deliver(id, job, info)).then(result => finish(id, job, result, clip?.text || ''), value => failed(id, job, value))
                    .finally(() => { owners.delete(id); statuses.delete(job); emit(); });
            }
        },
        // The engine can say lines (now, or after the model is downloaded): the menu entry and the bar appear then.
        refresh() { emit(); },
        // Another edit opened: what was heard and chosen belongs to the one before.
        reset() {
            stopListen(); picking = null; pickedClip = null; chosen = null;
            for (const entry of heard.values()) if (entry.own.job && entry.phase === 'finding') store.stop(entry.own.job);
            heard.clear(); statuses.clear(); owners.clear();
        },
        dispose() { disposed = true; stopListen(); unsubscribe(); unregister(); for (const entry of heard.values()) if (entry.own.job && entry.phase === 'finding') store.stop(entry.own.job); },
    };
    // The menu entry on an empty spot of the sound tracks: the recording track (added when missing), or the separated
    // voice track, is chosen there from the spot clicked.
    const unregister = registerMenuItem({where: 'empty', group: 'sound', label: context => context.t('Narration', '配音'),
        visible: context => available() && (!context.track || ['a1', 'a2', 'a3', said.narrationTrack(state.record)].includes(context.track)),
        enabled: context => !!said.narrationStretch(context.record, Math.max(0, context.frame | 0)),
        run: context => {
            const stretch = said.narrationStretch(state.record, Math.max(0, context.frame | 0));
            if (!stretch) return;
            if (!state.record.tracks.some(track => track.id === stretch.track)) state.apply(t('Narration', '配音'), said.withRecordingTrack);
            state.select({kind: 'range', tracks: [stretch.track], start: stretch.start, frames: stretch.frames});
        }});
    return api;
}

// The voice row above the description: "Voice", the choice of voices and a play button; a sentence where there is none.
export function createNarrationRow(host, controller, {t}) {
    const element = el('div', 'fv-ed-say-voice'), label = el('span', 'fv-ed-say-label', t('Voice', '音色'));
    const select = el('select', 'fv-ed-say-select'); select.setAttribute('aria-label', t('Voice', '音色'));
    const listen = el('button', 'fv-ed-say-listen'); listen.type = 'button'; listen.title = t('Listen', '试听'); listen.setAttribute('aria-label', t('Listen', '试听'));
    const icon = document.createElementNS(SVG, 'svg'); icon.setAttribute('viewBox', '0 0 16 16'); icon.setAttribute('aria-hidden', 'true');
    const shape = document.createElementNS(SVG, 'path'); icon.append(shape); listen.append(icon);
    const none = el('span', 'fv-ed-say-none', t('No voice available. Record or import a voice clip first.', '没有可用的音色。请先录音或导入一段人声。'));
    element.append(label, select, listen, none); element.hidden = true; host.append(element);
    const PICK = '__pick__';
    select.onchange = () => {
        if (select.value === PICK) { controller.pick(); return; }
        controller.choose(select.value);
    };
    listen.onclick = () => controller.listen();
    function option(value, text) { const item = el('option', null, text); item.value = value; return item; }
    function update(shown, busy = false) {
        element.hidden = !shown;
        if (!shown) return;
        select.disabled = busy;
        const all = controller.voices(), current = controller.voice();
        const empty = !all.speakers.length && !all.clips.length && !all.finding;
        select.hidden = listen.hidden = empty; none.hidden = !empty; label.hidden = empty;
        if (empty) return;
        // Nothing shown as chosen while the speakers are still being found and no clip is a voice.
        const blank = option('', ''); blank.hidden = blank.disabled = true;
        const items = [blank, ...all.speakers.map(voice => option(voice.key, voice.label))];
        if (all.speakers.length && all.clips.length) { const line = el('hr'); items.push(line); }
        items.push(...all.clips.map(voice => option(voice.key, voice.label)), option(PICK, t('Pick a sound clip…', '选一个声音片段…')));
        const signature = items.map(item => item.tagName === 'HR' ? '-' : `${item.value}=${item.textContent}`).join('\n');
        if (select.dataset.signature !== signature) { select.replaceChildren(...items); select.dataset.signature = signature; }
        select.value = controller.picking ? PICK : current?.key ?? '';
        listen.disabled = !current || busy;
        const playing = controller.listening();
        listen.setAttribute('aria-pressed', String(playing));
        shape.setAttribute('d', playing ? 'M4 4h8v8H4z' : 'M5 3.2v9.6L12.6 8z');
    }
    return {element, update, dispose() { element.remove(); }};
}
