// Dubbing lines through the generation bar: a stretch of speech chosen there shows its lines in place, each one
// editable; only the lines changed are dubbed again. The server (or a stand-in) recognises and dubs; this keeps what
// the user typed, the progress, the original/new choice for playback and the lines that did not fit.
import * as voice from './editor_voice_model.js';
import {isNetworkTypeError, connectionLostText} from './browser_extension.js';
import {issueButton} from './issue_button.js';
import {repairAction} from './repair_hint.js';

const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

// What a failed run says: the lines the user typed are kept, whatever went wrong; the code never reaches the words.
export function failureCopy(error, t) {
    if (isNetworkTypeError(error)) return {text: connectionLostText(t), report: false};
    switch (error?.code) {
        case 'out_of_memory': return {report: true, text: t('Not enough memory to finish dubbing. The edited lines are kept. Close other programs using the GPU, then redub.', '显存或内存不足，配音未完成，所改台词已保留。请关闭其他占用显卡的程序后重新配音。')};
        case 'storage_unavailable': return {report: false, text: t('Could not write to disk, so dubbing did not finish. The edited lines are kept. Check the free disk space, then redub.', '无法写入磁盘，配音未完成，所改台词已保留。请检查磁盘空间后重新配音。')};
        case 'voice_model_missing': return {report: false, text: t('The dubbing model is not fully installed. ', '配音模型没有装全。') + t(...repairAction())};
        default: return {report: true, text: t('Dubbing did not finish. The edited lines are kept. Redub them to try again.', '配音未完成，所改台词已保留，请重新配音。')};
    }
}

export function createEditorVoice({state, store, t, onChange = () => {}, place = async () => {}, available = () => true, playLine = () => {}, comparePlayback = () => {}}) {
    const sessions = new Map();
    let disposed = false, placing = false;
    const target = (selection = state.selection) => available() && state.record && voice.voiceTarget(state.record, selection);
    const keyOf = found => `${found.clipId}:${found.start}:${found.end}`;
    const session = (found = target()) => found ? sessions.get(keyOf(found)) || null : null;
    const emit = () => { if (!disposed) onChange(); };

    // Recognition starts by itself when a stretch of speech is chosen.
    async function open(found = target()) {
        if (!found) return null;
        const key = keyOf(found);
        let current = sessions.get(key);
        if (current) return current;
        current = {key, found, phase: 'finding', lines: [], texts: new Map(), results: new Map(), shortened: new Map(), compare: 'new',
            active: null, index: 0, count: 0, remaining: null, failed: 0, revision: state.record.revision};
        sessions.set(key, current); emit();
        try {
            const result = await store.lines({found, revision: current.revision});
            if (disposed || sessions.get(key) !== current) return current;
            current.phase = result.speech && result.lines.length ? 'ready' : 'none';
            current.lines = result.lines; current.separated = result.separated; current.recognition = result.recognition;
            current.rate = new Map(result.lines.map(line => [line.id, line.rate]));
        } catch (value) {
            // A failed recognition is not kept: choosing the stretch again asks again.
            current.phase = 'error'; current.error = value;
            if (sessions.get(key) === current) sessions.delete(key);
            if (!disposed) onChange(); return current;
        }
        emit(); return current;
    }
    const failedCount = current => [...current.results.values()].filter(value => value.state === 'failed').length;
    const changed = current => voice.changedLines(current.lines, current.texts);
    function edit(id, text, current = session()) {
        if (!current || current.phase === 'dubbing') return;
        // What the last run said about this line no longer fits its new words.
        current.texts.set(id, text); current.results.delete(id); current.failed = failedCount(current); current.failure = null; emit();
    }
    async function redub(current = session()) {
        if (!current || current.phase === 'dubbing' || current.phase === 'finding') return null;
        const lines = changed(current);
        current.phase = 'dubbing'; current.index = 0; current.count = lines.length; current.remaining = null; current.failed = 0; current.results = new Map(); current.notice = null; current.failure = null; emit();
        try {
            const send = () => store.replace({found: current.found, revision: current.revision, recognition: current.recognition,
                lines: current.lines.map(line => ({id: line.id, start: line.start, end: line.end, text: current.texts.get(line.id) ?? line.text, original_text: line.text})),
                progress: info => { current.index = info.index; current.count = info.count || current.count; current.active = info.line || null; current.remaining = info.remaining_seconds ?? current.remaining; emit(); }});
            let result;
            try { result = await send(); } catch (value) {
                // The service no longer keeps the recognition (restart, age): recognise the same stretch again and put the typed words back by line id.
                if (value?.code !== 'invalid_request') throw value;
                const again = await store.lines({found: current.found, revision: current.revision});
                const ids = new Set(again.lines.map(line => line.id));
                if (again.lines.length !== current.lines.length || current.lines.some(line => !ids.has(line.id))) throw value;
                current.lines = again.lines; current.recognition = again.recognition;
                result = await send();
            }
            if (disposed) return result;
            // The service did not put anything into the project: every line was too long, or the stretch was edited meanwhile.
            if (result?.placed === false && (result.reason || !result.apply)) {
                current.results = voice.lineResults(result); current.failed = result.failed || 0; current.phase = 'ready'; current.active = null; current.sources = null;
                current.notice = result.reason === 'project_changed' ? 'changed' : null; current.revision = state.record.revision;
                emit(); return result;
            }
            current.notice = null; current.failure = null;
            placing = true;
            try { current.voiceClip = (await place(result))?.voiceClip || current.found.clipId; } finally { placing = false; }
            current.results = voice.lineResults(result); current.failed = result.failed || 0; current.sources = result.sources;
            for (const line of current.lines) if (current.results.get(line.id)?.state === 'placed') line.text = current.texts.get(line.id) ?? line.text;
            current.compare = 'new'; current.active = null; current.phase = 'done'; current.revision = state.record.revision;
            emit(); return result;
        } catch (value) {
            current.phase = 'ready'; current.active = null; current.error = value;
            // Said once, in the panel beside the lines: no pop-up, no second copy in the bar.
            current.failure = value?.code === 'cancelled' ? null : value; emit();
            return null;
        }
    }
    function cancel(current = session()) { if (current?.phase === 'dubbing') { current.failure = null; store.cancel?.(); } }
    function setCompare(value, current = session()) { if (!current?.sources) return; current.compare = value; comparePlayback(current.voiceClip || current.found.clipId, value === 'original' ? current.sources.original : null); emit(); }
    // The model's rewrite of a line that did not fit; the original wording stays beside it until the user lets go of it.
    async function shorten(id, current = session()) {
        const line = current?.lines.find(value => value.id === id), result = current?.results.get(id);
        if (!line || current.phase === 'dubbing' || current.shortened.get(id)?.busy) return;
        const entry = {busy: true, original: current.texts.get(id) ?? line.text}; current.shortened.set(id, entry); current.failure = null; emit();
        try {
            const out = await store.shorten({line: {id, text: entry.original, seconds: result?.seconds}, target_seconds: Math.max(.1, (line.end - line.start))});
            entry.busy = false; current.texts.set(id, out.text);
        } catch (value) { current.shortened.delete(id); if (value?.code === 'shorten_unavailable') current.noShorten = true; }
        emit();
    }
    function restore(id, current = session()) {
        const entry = current?.shortened.get(id); if (!entry || entry.busy || current.phase === 'dubbing') return;
        current.texts.set(id, entry.original); current.shortened.delete(id); current.failure = null; emit();
    }
    // Undo takes the new sound out again; the lines then are the old ones.
    const stillPlaced = current => {
        const clip = state.record.tracks.flatMap(track => track.clips).find(value => value.id === (current.voiceClip || current.found.clipId));
        return !!clip && `generated:${clip.source?.id}` === String(current.sources?.new);
    };
    // The original is only heard while its stretch stays chosen; a changed project (undo included) makes the lines stale.
    function clearCompare(current) { if (current.compare === 'original') { current.compare = 'new'; comparePlayback(current.voiceClip || current.found.clipId, null); } }
    const unsubscribe = state.subscribe(event => {
        const type = event.type ?? event;
        const here = target() ? keyOf(target()) : null;
        for (const [key, current] of [...sessions]) {
            if (key !== here) clearCompare(current);
            if (type === 'record' && !placing && current.phase === 'done' && !stillPlaced(current)) { clearCompare(current); sessions.delete(key); }
        }
        if (type === 'selection' || type === 'record') { const found = target(); if (found) open(found); emit(); }
    });
    // The entry appears once the engine says it can dub; a stretch already chosen is looked at then.
    function refresh() { const found = target(); if (found) open(found); emit(); }
    return {refresh, target, session, open, edit, redub, cancel, setCompare, shorten, restore, playLine,
        dispose() { for (const current of sessions.values()) clearCompare(current); disposed = true; unsubscribe(); sessions.clear(); }, changed};
}

// The lines in the bar: a row each, time on the left, the words themselves to edit.
export function createVoicePanel(host, controller, {t}) {
    const link = el('link'); link.rel = 'stylesheet'; link.href = new URL('./editor_voice.css', import.meta.url).href; document.head.append(link);
    const element = el('div', 'fv-ed-voice'), top = el('div', 'fv-ed-voice-top'), voiceName = el('span', 'fv-ed-voice-name', t('Lines', '台词'));
    const compare = el('div', 'fv-ed-voice-compare'); compare.setAttribute('role', 'group');
    const choices = [['original', t('Original', '原声')], ['new', t('New', '新声')]].map(([value, label]) => {
        const button = el('button', null, label); button.type = 'button'; button.dataset.value = value;
        button.onclick = () => controller.setCompare(value); compare.append(button); return button;
    });
    const list = el('div', 'fv-ed-voice-lines'), note = el('div', 'fv-ed-voice-note'); note.setAttribute('role', 'status');
    top.append(voiceName, compare); element.append(top, list, note); element.hidden = true; host.append(element);
    const rows = new Map();
    function row(line) {
        let entry = rows.get(line.id); if (entry) return entry;
        const wrap = el('div', 'fv-ed-voice-line'), main = el('div', 'fv-ed-voice-main'), time = el('button', 'fv-ed-voice-time', voice.clockText(line.start)),
            text = el('textarea', 'fv-ed-voice-text'), mark = el('span', 'fv-ed-voice-mark'), extra = el('div', 'fv-ed-voice-extra');
        time.type = 'button'; time.setAttribute('aria-label', t('Play this line', '播放这一句'));
        text.rows = 1; text.value = line.text; text.setAttribute('aria-label', t('Line', '台词'));
        time.onclick = () => controller.playLine(line); text.oninput = () => { grow(text); controller.edit(line.id, text.value); };
        text.onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) event.preventDefault(); };
        main.append(mark, time, text); wrap.append(main, extra); entry = {wrap, text, time, mark, extra}; rows.set(line.id, entry); return entry;
    }
    // The box is border-box: the border is added to what the words need. Measured again when the bar's width or its style sheet changes.
    const grow = area => { area.style.height = '0px'; area.style.height = (area.scrollHeight + area.offsetHeight - area.clientHeight) + 'px'; };
    const regrow = () => { if (!element.hidden) list.querySelectorAll('textarea').forEach(grow); };
    let seen = 0;
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { if (list.clientWidth !== seen) { seen = list.clientWidth; regrow(); } }) : null;
    observer?.observe(list);
    link.onload = regrow;
    function update() {
        const found = controller.target(), current = found && controller.session(found);
        element.hidden = !found || !current;
        if (current && ['ready', 'done', 'dubbing'].includes(current.phase)) list.dataset.tourId = 'voice-lines'; else delete list.dataset.tourId;
        if (element.hidden) return;
        const dubbing = current.phase === 'dubbing', ready = ['ready', 'done', 'dubbing'].includes(current.phase);
        compare.hidden = !current.sources;
        for (const button of choices) button.setAttribute('aria-pressed', String(button.dataset.value === current.compare));
        voiceName.hidden = !ready;
        const failure = current.failure && failureCopy(current.failure, t);
        note.replaceChildren();
        if (failure) {
            note.append(failure.text);
            if (failure.report) note.append(issueButton(t, () => ({where: ['Editor generation', '剪辑器生成'], message: failure.text, kind: current.failure?.code || null})));
        } else note.textContent = current.notice === 'changed' ? t('This part changed during dubbing, so the new voice was not placed. Redub it.', '这一段在配音期间有改动，新声未放入。请重新配音。') : current.phase === 'none' ? t('No lines were found in this part.', '这一段没有识别到台词。') : current.phase === 'done' && current.failed ? voice.failedText(current.failed, t) : '';
        note.hidden = !note.textContent;
        if (current.phase === 'finding') {
            list.replaceChildren(...[0, 1, 2].map(() => el('div', 'fv-ed-voice-placeholder'))); rows.clear(); return;
        }
        if (!ready) { list.replaceChildren(); rows.clear(); return; }
        const changedIds = new Set(controller.changed(current).map(line => line.id));
        list.replaceChildren(...current.lines.map(line => {
            const entry = row(line), result = current.results.get(line.id), short = current.shortened.get(line.id);
            const value = current.texts.get(line.id) ?? line.text;
            if (entry.text.value !== value) { entry.text.value = value; }
            entry.text.disabled = dubbing; entry.text.readOnly = dubbing;
            entry.wrap.dataset.changed = String(changedIds.has(line.id)); entry.wrap.dataset.active = String(current.active === line.id); entry.wrap.dataset.failed = String(result?.state === 'failed');
            const extra = [];
            if (short?.busy) extra.push(el('span', 'fv-ed-voice-shortening', t('Shortening', '正在改短')));
            else if (short) {
                const old = el('s', 'fv-ed-voice-old', short.original), undo = el('button', 'fv-ed-voice-link', t('Restore line', '恢复原句')); undo.type = 'button';
                undo.onclick = () => controller.restore(line.id); extra.push(old, undo);
            } else if (result?.state === 'kept' && result.overrun > 0) {
                const over = el('span', 'fv-ed-voice-over', voice.overrunText(result.overrun, t, !current.noShorten));
                extra.push(over);
                if (!current.noShorten) {
                    const cut = el('button', 'fv-ed-voice-link', t('Shorten this line', '改短这句')); cut.type = 'button';
                    cut.onclick = () => controller.shorten(line.id); extra.push(cut);
                }
            } else if (result?.state === 'placed' && result.short) extra.push(el('span', 'fv-ed-voice-short', t('Shorter than the original, with a pause after it', '比原句短，句尾留有空白')));
            entry.extra.replaceChildren(...extra); entry.extra.hidden = !extra.length;
            return entry.wrap;
        }));
        regrow();
        // A result that kept the original voice scrolls to its first line that did not fit; the list may hide it.
        const over = current.lines.find(line => current.results.get(line.id)?.state === 'kept' && current.results.get(line.id).overrun > 0);
        if (current.scrollOver !== current.results && over) { current.scrollOver = current.results; rows.get(over.id)?.wrap.scrollIntoView({block: 'nearest'}); }
    }
    return {element, update, dispose() { observer?.disconnect(); element.remove(); link.remove(); }};
}
