// The editor owns its generation controls; progress can use the optional component.
import {createFirstFrameSwitch} from './editor_first_frame.js';
import {createEditorReferences} from './editor_references.js';
import {createExpandButton} from './editor_expand.js';
import {createRequestAssets} from './request_assets.js';
import {issueButton} from './issue_button.js';
import {waitingText} from './chain_support.js';
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
export const timeEstimate = (seconds, t) => seconds == null ? '' : seconds < 60
    ? t(`about ${Math.max(1, Math.ceil(seconds))} s`, `约 ${Math.max(1, Math.ceil(seconds))} 秒`)
    : t(`about ${Math.ceil(seconds / 60)} minutes`, `约 ${Math.ceil(seconds / 60)} 分钟`);
// Provisional copy keys for Iris; decode/save sentences follow Studio's stage vocabulary.
export const CLIP_FINALIZATION_COPY = {
    prepareDecode: ['Preparing video output', '正在准备输出视频'],
    loadDecoder: ['Loading the video decoder', '正在载入视频解码器'],
    decodeVideo: ['Rendering the video', '正在输出视频'],
    decodeAudio: ['Rendering the audio', '正在输出音频'],
    saveVideo: ['Saving the video', '正在保存视频'],
    openClip: ['Adding it to the timeline', '正在放到时间线上'],
    joinClips: ['Joining the parts', '正在拼接各段'],
};
export function clipFinalization(job) {
    if (!job || !['running', 'queued'].includes(job.state)) return null;
    const message = job.progressMessage || job.progress || {}, blocks = job.blocks || [];
    const phase = message.phase || message.timing_phase, label = message.label || '';
    let key;
    if (job.joining) key = 'joinClips';
    else if (job.loadingClip || phase === 'complete' || message.overall?.status === 'complete'
        || blocks.some(block => block.state === 'running' && block.fraction >= 1)) key = 'openClip';
    else if (phase === 'save' || /^Saving (MP4 and audio|decoded video|decoded audio)$/.test(label)) key = 'saveVideo';
    else if (phase === 'sample_finalize') key = 'prepareDecode';
    else if (phase === 'decode' || message.timing_phase === 'decode') key = /audio/i.test(label) && !/video and audio/i.test(label) ? 'decodeAudio'
        : label === 'Loading video VAE' ? 'loadDecoder' : 'decodeVideo';
    if (!key) return null;
    // Decode/save and joining retain engine cancellation. A saved final part waiting
    // for its receipt/media cannot be cancelled; later queued parts still can.
    const current = message.chain?.block || blocks.find(block => block.state === 'running')?.id;
    const later = blocks.some(block => block.id !== current && ['pending', 'queued', 'running'].includes(block.state));
    return {key, cancelable: key !== 'openClip' || !job.loadingClip && later};
}
// After the next frame is on screen; a page nobody sees has no frame to wait for.
const painted = () => typeof document === 'undefined' || document.visibilityState !== 'visible' || typeof requestAnimationFrame !== 'function' ? Promise.resolve()
    : new Promise(resolve => requestAnimationFrame(() => { const {port1, port2} = new MessageChannel(); port1.onmessage = () => { port1.close(); resolve(); }; port2.postMessage(null); }));
export function createEditorGenbar(host, {state, t, actions, text = ''}) {
    const hint = el('span', 'fv-ed-gen-hint'), note = el('span', 'fv-ed-gen-note');
    note.setAttribute('role', 'status'); note.hidden = true;
    const input = el('textarea', 'fv-ed-description'); input.rows = 1; input.value = text;
    const description = el('div', 'fv-ed-description-box'); description.append(input);
    const generate = el('button', 'fv-ed-generate'), cancel = el('button', null, t('Cancel', '取消')), resume = el('button', null, t('Continue', '继续'));
    input.dataset.fvGuide = 'editor-describe'; generate.dataset.fvGuide = 'editor-generate';
    for (const button of [generate, cancel, resume]) button.type = 'button';
    host.append(hint, note, description, generate, cancel, resume);
    // The line's own "check again", shown whenever nothing else is said there.
    const retryService = el('button', 'fv-ed-gen-retry', t('Generation unavailable · retry', '生成服务暂不可用 · 重试')); retryService.type = 'button';
    let service = null;
    retryService.onclick = () => { if (service && !service.checking) { retryService.disabled = true; service.retry(); } };
    function showService() {
        if (disposed || !service) return;
        retryService.disabled = service.checking;
        if (note.hidden) { note.replaceChildren(retryService); note.hidden = false; }
    }
    // What this part will download first, above the bar like the studio's line above Generate.
    const assets = actions.assets ? createRequestAssets({t, api: actions.assets.api, request: () => actions.assets.query()}) : null;
    if (assets) host.prepend(assets.element);
    const firstFrame = createFirstFrameSwitch(host, {state, t, actions});
    description.after(firstFrame.element);
    const failure = el('div', 'fv-ed-gen-failure fv-result-links'), failureBar = el('div', 'fv-ed-failure-actions fv-actions');
    const failureReason = el('span', 'fv-ed-failure-reason'); failureReason.setAttribute('role', 'status');
    const report = el('button', 'fv-ed-failure-report fv-action fv-action-secondary', t('Download diagnostic report', '下载诊断报告')); report.type = 'button';
    failureBar.append(failureReason, report); failure.append(failureBar); failure.hidden = true; host.append(failure);
    let failureId = null, downloading = false;
    // Reference pictures for a part that starts fresh: thumbnails and a small "+" before the description.
    // What goes wrong with a reference picture is said above the "+", where the eyes are, not in the page-wide line.
    const refNotice = el('div', 'fv-ed-ref-notice'); refNotice.hidden = true; refNotice.setAttribute('role', 'status');
    let refNoticeTimer = null;
    const sayAtRefs = (text, where = null) => {
        clearTimeout(refNoticeTimer);
        if (!text || disposed) { refNotice.hidden = true; return; }
        refNotice.textContent = text; refNotice.hidden = false;
        if (where) refNotice.append(issueButton(t, () => ({where, message: text})));
        refNotice.style.left = Math.max(0, (references?.element.getBoundingClientRect().left || 0) - host.getBoundingClientRect().left) + 'px';
        refNoticeTimer = setTimeout(() => { refNotice.hidden = true; }, where ? 20000 : 9000);
    };
    const references = actions.references ? createEditorReferences({t, source: actions.references.source, size: actions.references.size, error: value => actions.error(value, null, sayAtRefs), notify: sayAtRefs}) : null;
    if (references) { description.before(references.element); host.append(refNotice); }
    let status = null, serial = 0, disposed = false, pressing = new Set(), previewSelection = null, estimateCache = null, noteTimer = null, queuedAt = null;
    // Which parts are generating, or how the job ended: a "Queued" line goes once this moves on.
    const progressKey = () => ['done', 'failed', 'cancelled', 'interrupted'].includes(status?.state) ? status.state
        : (status?.blocks || []).filter(block => block.state === 'running').map(block => block.id).join(',');
    async function downloadFailure(id = failureId) {
        if (disposed || downloading || id !== failureId) return;
        downloading = true; report.disabled = true;
        try {
            // A failed part offers its report on the part itself; the submit tip then hangs under this line's sentence.
            await actions.downloadFailureReport?.(id, {bar: failureBar, anchor: report.hidden ? failureReason : report, t,
                current: () => !disposed && failureId === id && !failure.hidden});
        } catch (value) {
            actions.notify?.(t(`Downloading the diagnostic report failed: ${value.message || value}`, `下载诊断报告时出错：${value.message || value}`), ['Editor diagnostic report', '剪辑器诊断报告']);
        } finally { downloading = false; report.disabled = false; }
    }
    report.onclick = () => downloadFailure();
    function say(text, queued = false, where = null) {
        if (disposed) return;
        queuedAt = text && queued ? progressKey() : null;
        clearTimeout(noteTimer); note.textContent = text || ''; note.hidden = !text;
        if (text && where) note.append(issueButton(t, () => ({where, message: text})));
        if (text) noteTimer = setTimeout(() => { note.hidden = true; showService(); }, where ? 20000 : 6000);
        else showService();
    }
    async function render() {
        if (disposed) return;
        if (queuedAt != null && progressKey() !== queuedAt) say('');
        const current = ++serial, selection = previewSelection || state.selection;
        const selectedFailure = !previewSelection && actions.selectedFailure?.();
        if (failureId !== (selectedFailure?.id || null)) {
            failure.querySelector('.fv-report-tip')?.remove();
            failureId = selectedFailure?.id || null;
        }
        failure.hidden = !selectedFailure;
        failureReason.textContent = selectedFailure?.text || '';
        report.hidden = !selectedFailure;
        const running = ['queued', 'running', 'cancelling'].includes(status?.state);
        const selected = !!selection && (!!previewSelection || (actions.supportedSelection?.() ?? true));
        // While a part generates, choosing another place offers Generate again; it is queued behind.
        // A press is being sent for the parts in `pressing`; only that same selection waits for it.
        const sending = pressing.has(JSON.stringify(state.selection ?? null)), starting = pressing.size > 0 && !running;
        const other = (running || starting) && !sending && selected && !previewSelection && actions.selectionRunning?.() === false;
        // A part pressed while it could not start yet waits in line; its Cancel takes it out of the line.
        const held = !previewSelection && !!actions.selectionHeld?.();
        const busy = sending || held || (running || starting) && !other;
        // The selected part is still being sent: it can only be stopped once it has started.
        const opening = (starting || !!actions.selectionSending?.()) && !held;
        const stopped = ['failed', 'cancelled', 'interrupted'].includes(status?.state);
        host.dataset.mode = busy ? 'running' : selected ? 'selected' : 'idle';
        hint.hidden = !busy && !other;
        // The description and Generate stay where they are while a part is made, greyed, so nothing around them moves.
        input.hidden = generate.hidden = false;
        // A choice that will move to an empty spot can still be described first.
        const writable = selected || !previewSelection && !!selection && !!actions.selectionMovable?.();
        input.disabled = !writable || busy;
        input.placeholder = writable ? t('Describe this part', '描述这一段')
            : '';
        input.setAttribute('aria-label', t('Description', '描述'));
        generate.classList.toggle('fv-primary', selected && !busy);
        // A cancel already accepted is not offered again; the bar says it is stopping until the run ends.
        const cancelling = status?.state === 'cancelling' && !held;
        const finalization = clipFinalization(status);
        cancel.hidden = !busy || opening || cancelling || !held && finalization?.cancelable === false; resume.hidden = !stopped;
        const waiting = status?.state === 'queued' && !(status.blocks || []).some(block => ['queued', 'running'].includes(block.state) && block.prompt_id);
        if (opening) hint.textContent = t('Starting…', '正在开始…');
        else if (held || waiting) hint.textContent = waiting && !held ? waitingText(status.waiting, t) : t('Queued', '排队中');
        else if (cancelling) hint.textContent = t('Stopping…', '正在停止…');
        else if (running) {
            const blocks = status?.blocks || [], done = blocks.filter(block => block.state === 'done').length;
            const number = Math.min(blocks.length || 1, Math.max(done, status?.chainProgress?.index || 0) + 1), count = blocks.length || 1;
            const remaining = status?.chainProgress?.overall?.remaining_seconds ?? (blocks.every(block => Number.isFinite(block.remaining_seconds)) ? blocks.reduce((sum, block) => sum + block.remaining_seconds, 0) : null);
            const words = status?.words;
            // The creation panel's own sentences, so a long step says what it is and how long it has taken.
            // At a narrow width only the stage sentence is cut; the part number and the time stay.
            // A single part needs no number; with nothing else to say it reads "Preparing".
            // The creation panel's own time when it has one, so the bar and the panel never differ; the job's sum until the panel has one.
            const left = words?.over ? null : words?.left ?? remaining;
            const time = finalization ? '' : words?.stall || (words?.over ? t('Taking longer than expected', '比预计久，仍在生成') : words?.time || (left != null ? timeEstimate(left, t) : ''));
            const stage = finalization ? t(...CLIP_FINALIZATION_COPY[finalization.key]) : words?.stage || (count === 1 && !time ? t('Preparing', '正在准备') : '');
            const spans = [['fv-ed-gen-part', count > 1 ? t(`Part ${number}/${count}`, `第 ${number}/${count} 段`) : ''],
                ['fv-ed-gen-stage', stage], ['fv-ed-gen-time', time]];
            const shown = spans.filter(([, text]) => text).map(([cls, text], index) => [cls, index ? ` · ${text}` : text]);
            hint.replaceChildren(...shown.map(([cls, text]) => el('span', cls, text)));
            hint.title = shown.map(([, text]) => text).join('');
        }
        firstFrame.update(busy);
        references?.update(busy, busy ? undefined : actions.references.visible() || false);
        generate.textContent = t('Generate', '生成');
        // Never disabled for a reason the press itself can explain or fix; only until the server says it can generate.
        generate.disabled = busy || !!previewSelection || !!actions.generationWaiting?.();
        resizeDescription();
        input.dispatchEvent(new Event('change'));
        generate.title = '';
        if (assets) { if (selected && !busy) assets.refresh(); else assets.element.hidden = true; }
        if (selected && !busy) {
            const key = JSON.stringify(selection);
            if (estimateCache?.key === key) generate.textContent += estimateCache.label;
            const seconds = await actions.estimate(selection);
            if (disposed || current !== serial) return;
            const label = seconds == null ? '' : ' · ' + timeEstimate(seconds, t);
            estimateCache = {key, label};
            generate.textContent = t('Generate', '生成') + label;
        }
    }
    function resizeDescription() {
        // 18 px lines + 16 px padding + 2 px border: one line is still 36 px, five lines 108 px.
        input.style.height = '0px';
        const height = Math.max(36, (input.scrollHeight || 34) + 2);
        input.style.height = `${Math.min(108, height)}px`;
        input.style.overflowY = height > 108 ? 'auto' : 'hidden';
        host.closest('.fv-editor')?.style.setProperty('--fv-ed-description-growth', `${Math.min(108, height) - 36}px`);
    }
    let width = null;
    const resize = typeof ResizeObserver === 'function' ? new ResizeObserver(entries => {
        const next = entries[0]?.contentRect.width;
        if (next === width) return;
        width = next; resizeDescription();
    }) : null;
    resize?.observe(description);
    // The box shows the description of the planned parts chosen and is written to them when the choice moves
    // on, the box is left or Generate is pressed. What is typed with no part or range chosen goes to the next
    // part chosen that has no description yet.
    // written: what is to be written, which the enlarged box has before the small one.
    let bound = null, dirty = false, loose = false, committing = false, written = '';
    const typed = (value = input.value) => {
        state.setRangeText(value); written = value;
        if (bound) dirty = true; else loose = state.selection?.kind !== 'range';
    };
    function commit() {
        if (!bound || !dirty) return;
        const {ids} = bound; dirty = false; committing = true;
        try { actions.describeParts?.(ids, written); } finally { committing = false; }
    }
    function bind() {
        const part = actions.partText?.(state.selection) ?? null;
        if (part && !part.text && loose && input.value.trim()) { bound = part; dirty = true; written = input.value; }
        else if (part) { bound = part; dirty = false; input.value = part.text; }
        else bound = null;
        if (part || state.selection?.kind === 'range') loose = false;
    }
    input.oninput = () => { typed(); render(); };
    input.onblur = () => { if (dirty) { commit(); bind(); } };
    input.onkeydown = event => {
        // The former text input did not submit or insert a line on Enter.
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) event.preventDefault();
    };
    generate.onclick = async () => {
        const key = JSON.stringify(state.selection ?? null);
        if (pressing.has(key)) return;
        pressing.add(key); say(''); render();
        commit();
        const shown = input.value;
        let reason = '';
        try {
            // The press shows before any planning, however long that takes.
            await painted();
            const result = actions.press ? await actions.press(input.value) : await actions.generate({target: 'both', text: input.value});
            // Never over what the user typed meanwhile, for another part.
            if (result?.text != null && result.text !== input.value && input.value === shown) input.value = result.text;
            if (result?.say) { say(result.say, !!result.queued); reason = result.say; }
            if (result?.focus) { pressing.delete(key); render(); input.disabled = false; input.focus(); }
        }
        // A specific reason is said once; the planning limits are already shown above.
        catch (error) { const result = actions.error(error, 'generate'); reason = result?.say || '';
            if (result?.say && !['chain_too_long', 'chain_part_too_short', 'chain_short_parts'].includes(result.reason)) say(result.say, false, ['Editor generation', '剪辑器生成']);
            else if (!result && !note.textContent) { reason = t('Could not start. Try again.', '没能开始，请重试。'); say(reason, false, ['Editor generation', '剪辑器生成']); } }
        finally { pressing.delete(key); render(); }
        return reason;
    };
    const expand = createExpandButton({t, textarea: input, onGenerate: () => generate.onclick(), canGenerate: () => !generate.disabled, onDraft: text => typed(text)});
    description.append(expand.element);
    cancel.onclick = () => (!previewSelection && actions.selectionHeld?.() ? Promise.resolve(actions.unhold()).then(render) : actions.cancel()).catch(actions.error);
    resume.onclick = () => actions.resume().catch(value => actions.error(value, 'generate'));
    const unsubscribe = state.subscribe(event => {
        if (event.type === 'selection' && state.selection?.kind === 'range') {
            if (state.rangeText === null) state.setRangeText(input.value);
            else if (input.value !== state.rangeText) input.value = state.rangeText;
        }
        if (event.type === 'selection') {
            commit(); bind();
            // A part being made shows the description it is made with.
            const selected = !bound && state.selection?.kind === 'clip' && state.record.tracks.flatMap(track => track.clips).find(clip => state.selection.ids.includes(clip.id) && clip.pending);
            if (selected?.text) input.value = selected.text;
        }
        // An undo, or a description written elsewhere, shows unless something not yet written is in the box.
        if (event.type === 'record' && !committing && !dirty && (bound || actions.partText?.(state.selection))) bind();
        if (event.type === 'selection' || event.type === 'record') render();
    });
    render();
    return {element: host, say, downloadFailure, commit,
        service(value) {
            service = value;
            if (!value && note.contains(retryService)) { retryService.remove(); note.hidden = true; }
            showService();
        }, getText() { expand.commit(); return input.value; }, update(value) { status = value; render(); }, preview(selection) { previewSelection = selection; render(); }, setText(value) { input.value = value || ''; state.setRangeText(input.value); if (bound && input.value !== bound.text) { dirty = true; written = input.value; } render(); },
        dispose() { disposed = true; ++serial; clearTimeout(noteTimer); clearTimeout(refNoticeTimer); unsubscribe(); resize?.disconnect(); expand.dispose(); references?.dispose(); assets?.dispose(); }};
}

// A missing optional module leaves the editor's existing progress in place.
export async function loadEditorComponents(load = path => import(path)) {
    const results = await Promise.allSettled([load('./block_progress.js')]);
    return {blocks: results[0].status === 'fulfilled' ? results[0].value : null};
}

// What a part's progress reads from each message: Studio's displayed percentage while it
// shows this part, never a stage's own fraction (one loading step or one pass), which
// read as the part's showed a part still generating as finished.
export function blockMessages(messages, snapshot = () => null) {
    return messages.map(message => {
        const part = {...message}, shown = snapshot(message); delete part.display_fraction;
        return shown ? {...part, ...shown} : part;
    });
}
export function editorBlockStates(job, messages = [], resolve = null) {
    if (resolve) return resolve(job, messages);
    const states = {pending: 'waiting', queued: 'waiting', running: 'generating', done: 'done',
        failed: 'failed', cancelled: 'stopped', interrupted: 'stopped'};
    // A part that never started in a run that was stopped or failed is stopped too, not still queued.
    // Once a cancel is accepted (cancelling), such a part can only end cancelled, so it reads stopped at once.
    const ended = ['failed', 'cancelled', 'interrupted', 'cancelling'].includes(job.state);
    return Object.fromEntries((job.blocks || []).map(block => {
        if (ended && ['pending', 'queued'].includes(block.state)) return [block.id, {state: 'stopped', fraction: 0, remainingSeconds: null}];
        const live = messages.find(message => message.chain?.id === job.id && message.chain?.block === block.id);
        const running = block.state === 'running' || block.state === 'queued';
        const fraction = block.state === 'done' ? 1 : running && live
            ? Number.isFinite(live.percent) ? live.percent / 100 : live.bar ?? live.overall?.fraction ?? block.fraction
            : block.fraction;
        return [block.id, {state: running && live ? 'generating' : states[block.state] || 'waiting',
            ...(['pending', 'queued'].includes(block.state) && !live ? {queued: true} : {}),
            fraction: Math.max(0, Math.min(block.state === 'done' ? 1 : .99, fraction || 0)),
            remainingSeconds: running && live ? live.remaining_seconds ?? live.overall?.remaining_seconds ?? block.remaining_seconds ?? null
                : block.remaining_seconds ?? null}];
    }));
}
