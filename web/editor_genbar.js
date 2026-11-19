// The editor owns its generation controls; progress can use the optional component.
import {createFirstFrameSwitch} from './editor_first_frame.js';
import {createDurationField} from './editor_duration.js';
import {createEditorReferences} from './editor_references.js';
import {createExpandButton} from './editor_expand.js';
import {createRequestAssets} from './request_assets.js';
import {issueButton} from './issue_button.js';
import {pageRepair, repairButton} from './repair_hint.js';
import {waitingText} from './chain_support.js';
import {showEmptyPrompt} from './empty_prompt_notice.js';
import {segmented} from './editor_adjust.js';
import {createPassMode} from './pass_mode.js';
import {createSamplingEffort, continuationUpscale, effortFor} from './sampling_effort.js';
import {createVoicePanel} from './editor_voice.js';
import {createInstallPanel} from './editor_voice_install.js';
import * as dubbing from './editor_voice_model.js';
import {createNarrationRow} from './editor_narration.js';
import {tip, modKey} from './editor_tips.js';
// The quality control's own sheet; the creative panel loads the same file, so a second link is skipped.
const effortSheet = new URL('./sampling_effort.css', import.meta.url).href;
function loadEffortSheet() {
    if ([...document.querySelectorAll('link[rel=stylesheet]')].some(link => link.href === effortSheet)) return;
    const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = effortSheet; document.head.append(link);
}
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
export const timeEstimate = (seconds, t) => seconds == null ? '' : seconds < 60
    ? t(`about ${Math.max(1, Math.ceil(seconds))} s`, `约 ${Math.max(1, Math.ceil(seconds))} 秒`)
    : t(`about ${Math.ceil(seconds / 60)} minutes`, `约 ${Math.ceil(seconds / 60)} 分钟`);
// A start of a few seconds reads "Starting…"; a longer one counts in the same form as the other waiting lines.
export const startingText = (s, t) => {
    const m = Math.floor(s / 60), r = s % 60;
    return s < 3 ? t('Starting…', '正在开始…') : s < 60 ? t(`Starting · ${s} s`, `正在开始 · ${s} 秒`)
        : t(`Starting · ${m} min${r ? ` ${r} s` : ''}`, `正在开始 · ${m} 分${r ? ` ${r} 秒` : ''}`);
};
// Provisional copy keys for Iris; decode/save sentences follow Studio's stage vocabulary.
export const CLIP_FINALIZATION_COPY = {
    prepareDecode: ['Preparing video output', '正在准备输出视频'],
    loadDecoder: ['Loading the video decoder', '正在载入视频解码器'],
    decodeVideo: ['Rendering the video', '正在输出视频'],
    decodeAudio: ['Rendering the audio', '正在输出音频'],
    saveVideo: ['Saving the video', '正在保存视频'],
    openClip: ['Adding it to the timeline', '正在放到时间线上'],
    joinClips: ['Joining the parts', '正在接合各段'],
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
    input.dataset.fvGuide = 'editor-describe'; input.dataset.tourId = 'segment-prompt'; generate.dataset.fvGuide = 'editor-generate';
    for (const button of [generate, cancel, resume]) button.type = 'button';
    // The press keeps one width while its estimate arrives: the verb on top, the time under it (the separator is
    // kept for screen readers and copied text, so the words read as before: "Generate · about 6 minutes").
    const verbText = el('span', 'fv-ed-generate-verb'), timeText = el('small', 'fv-ed-generate-time');
    generate.append(verbText, timeText);
    // What must download before this part (the creative panel's own line, and the music and dubbing models'): the press says
    // "Downloads first" under its verb, and its hover says what and how much; nothing is added over the picture.
    let lastTime = '';
    const downloadText = () => [assets?.element, musicAssets, sayAssets].filter(node => node && !node.hidden && node.textContent.trim()).map(node => node.textContent.trim()).join(' ');
    const label = (verb, time = '') => {
        lastTime = time;
        const downloads = downloadText(), line = downloads ? t('Downloads first', '需先下载') : time;
        verbText.textContent = verb; timeText.textContent = line; timeText.hidden = !line;
        // The creation panel's key shows its own estimate on its second line, so the label carries the verb alone; a plain Generate is the panel's own "Generate video".
        actions.onLabel?.(verb === t('Generate', '生成') ? '' : verb, !generate.disabled);
        timeText.classList.toggle('fv-ed-generate-download', !!downloads);
        generate.setAttribute('aria-label', [verb, time, downloads].filter(Boolean).join(' · '));
        if (downloads && generate.getAttribute('data-fv-tip-name')) generate.setAttribute('data-fv-tip-use', downloads);
    };
    // Lines and panels the bar shows for a while (what downloads first, a failure, a reason that waits, the music and
    // voice choices, the dubbing lines) float in a tray anchored above the bar: the bar keeps its one height, so the
    // picture and the timeline never move when one comes or goes.
    const tray = el('div', 'fv-ed-gen-tray');
    host.append(hint, note, description, generate, cancel, resume, tray);
    note.classList.add('fv-copyable');
    host.dataset.fvTaskStage = 'editor'; host.classList.add('sgf-avoid');
    // Beside Generate, as beside the creative panel's Generate video: the panel's own two-pass choices, the same setting.
    const passMenu = actions.passMode ? el('details', 'fv-ed-pass-options') : null;
    // The editor routes keys itself and calls a control's onkeydown, as for the toolbar's ⋯ panel.
    const routed = (target, type, handler) => { target.onkeydown = handler; return () => { target.onkeydown = null; }; };
    const passControl = passMenu ? createPassMode({t, onChoose: mode => { actions.choosePass(mode); render(); }, listen: routed}) : null;
    // The creative panel's quality control (same component, same levels), above the two-pass choice in the same menu.
    // The steps are told where the control shows its estimate, which an edit has no use for: "8 + 3 steps", "8 steps", "12 steps".
    const stepsText = (steps, twoPass, refine) => `${steps}${twoPass ? ' + ' + refine : ''} ${steps === 1 && !twoPass ? t('step', '步') : t('steps', '步')}`;
    let quality = null, previewing = false, twoPassTitle = null;
    const effortControl = passMenu && actions.effort ? createSamplingEffort(t, {onChange: tier => { previewing = false; actions.chooseEffort(tier); render(); },
        onPreview: tier => {
            previewing = !!tier;
            if (tier) effortControl.setEstimate(stepsText(tier.steps, tier.twoPass, 3));
            else if (quality) effortControl.setEstimate(stepsText(quality.baseSteps, quality.twoPass, quality.refineSteps));
        },
        levels: actions.effortLevels?.() || undefined}) : null;
    if (effortControl) loadEffortSheet();
    let stopPass = null, stopEffort = null;
    if (passMenu) {
        const toggle = el('summary'); toggle.setAttribute('aria-label', t('Sampling', '采样')); toggle.append(el('span', 'fv-chevron'));
        tip(toggle, {name: t('Sampling', '采样'), use: t('Quality level and the two-pass choice for this part', '这一段的质量档位和二采')});
        const panel = el('div', 'fv-ed-pass-menu'); twoPassTitle = el('strong', 'fv-ed-pass-sub', t('Two-pass', '二采'));
        panel.append(el('strong', null, t('Sampling', '采样')), ...(effortControl ? [effortControl.element, twoPassTitle] : []), passControl.element);
        passMenu.append(toggle, panel); passMenu.hidden = true; generate.after(passMenu);
        stopPass = actions.onPass?.(() => render()) || null;
        stopEffort = effortControl ? actions.onEffort?.(() => render()) || null : null;
    }
    const closePass = event => { if (passMenu?.open && !passMenu.contains(event.target)) passMenu.open = false; };
    document.addEventListener('pointerdown', closePass, true);
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
    if (assets) tray.append(assets.element);
    let downloadWatch = null;
    if (typeof MutationObserver === 'function') {
        const relabel = new MutationObserver(() => { if (!disposed) label(verbText.textContent, lastTime); });
        for (const node of [assets?.element]) if (node) relabel.observe(node, {attributes: true, attributeFilter: ['hidden'], childList: true, subtree: true, characterData: true});
        downloadWatch = relabel;
    }
    const firstFrame = createFirstFrameSwitch(host, {state, t, actions});
    description.after(firstFrame.element);
    // On the music track: a style and a mood above the description, each a row; the music's own first-use download
    // in the same line above the bar as the video's.
    const music = actions.music || null;
    const presets = music ? el('div', 'fv-ed-music-presets') : null;
    const musicAssets = music ? el('div', 'fv-request-assets fv-muted') : null;
    let musicBytes = null, musicBytesAt = 0;
    const size = bytes => bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + '\u00a0GB' : Math.max(1, Math.round(bytes / 1e6)) + '\u00a0MB';
    const pickers = {};
    if (music) {
        for (const [key, label, options] of [['style', t('Style', '风格'), music.styles()], ['mood', t('Mood', '情绪'), music.moods()]]) {
            const row = el('div', 'fv-ed-music-row'), name = el('span', 'fv-ed-music-label', label);
            const picker = segmented(options.map(([value, text]) => ({value, label: text})), {onChange: value => { music.choose({[key]: value}); render(); }});
            picker.setAttribute('aria-label', label); pickers[key] = picker;
            row.append(name, picker); presets.append(row);
        }
        presets.hidden = true; musicAssets.hidden = true;
        tray.append(musicAssets, presets);
    }
    async function showMusicAssets(shown) {
        if (!music) return;
        if (!shown) { musicAssets.hidden = true; return; }
        // Asked again at most every 5 s while a stretch of the music track is chosen.
        if (musicBytes === null || Date.now() - musicBytesAt > 5000) {
            musicBytesAt = Date.now();
            musicBytes = await music.downloadBytes();
            if (disposed) return;
        }
        musicAssets.textContent = musicBytes > 0 ? t(`Downloads before generating: music model ${size(musicBytes)}`, `生成前会先下载：配乐模型 ${size(musicBytes)}`) : '';
        musicAssets.hidden = !(musicBytes > 0) || host.dataset.mode !== 'selected' || !music.target();
    }
    // A new line said on an empty stretch of the recording track: the voice row above the description, and the dubbing
    // model's first-use download in the same line above the bar.
    const narration = actions.narration || null;
    const voiceRow = narration ? createNarrationRow(host, narration, {t}) : null;
    const sayAssets = narration ? el('div', 'fv-request-assets fv-muted') : null;
    if (narration) { sayAssets.hidden = true; tray.append(sayAssets, voiceRow.element); }
    for (const node of [musicAssets, sayAssets]) if (node && downloadWatch) downloadWatch.observe(node, {attributes: true, attributeFilter: ['hidden'], childList: true, characterData: true, subtree: true});
    // How long the parts not generated yet are, between the description and the first-frame switch.
    const duration = createDurationField({state, t, actions});
    description.after(duration.element);
    // Where a narrow English bar starts Generate on a line of its own (editor.css); hidden elsewhere.
    const lineBreak = el('span', 'fv-ed-genbar-break'); lineBreak.setAttribute('aria-hidden', 'true');
    generate.before(lineBreak); host.lang = t('en', 'zh-CN');
    // Dubbing: the lines of a chosen stretch of speech take the description box's place.
    const voice = actions.voice || null, voicePanel = voice ? createVoicePanel(host, voice.controller, {t}) : null;
    if (voicePanel) tray.append(voicePanel.element);
    // Where the dubbing model is missing and the engine offers it, the same place offers its download. The panel exists only
    // while it is offered; with the model there, or on a machine that cannot change a voice, nothing of it is in the bar.
    let installPanel = null;
    const syncInstall = () => {
        const offered = !!voice?.install?.offered?.() && !disposed;
        if (offered && !installPanel) { installPanel = createInstallPanel(tray, voice.install, {t, selection: () => previewSelection || state.selection}); tray.append(installPanel.element); }
        else if (!offered && installPanel) { installPanel.dispose(); installPanel = null; }
    };
    const failure = el('div', 'fv-ed-gen-failure fv-result-links'), failureBar = el('div', 'fv-ed-failure-actions fv-actions');
    const failureReason = el('span', 'fv-ed-failure-reason'); failureReason.setAttribute('role', 'status');
    const report = el('button', 'fv-ed-failure-report fv-action fv-action-secondary', t('Download report', '下载报告')); report.type = 'button';
    // Beside a reason whose hint names the page settings.
    const repair = repairButton(t, {className: 'fv-action fv-action-secondary'}); repair.hidden = true;
    failureBar.append(failureReason, repair, report); failure.append(failureBar); failure.hidden = true; tray.append(failure);
    failureReason.classList.add('fv-copyable');
    let failureId = null, downloading = false;
    // Reference pictures for a part that starts fresh: thumbnails and a small "+" before the description.
    // What goes wrong with a reference picture is said above the "+", where the eyes are, not in the page-wide line.
    const refNotice = el('div', 'fv-ed-ref-notice'); refNotice.hidden = true; refNotice.setAttribute('role', 'status');
    let refNoticeTimer = null;
    // A reference picture's problem is said on the bar's own line (it no longer floats over the picture).
    const sayAtRefs = (text, where = null) => {
        clearTimeout(refNoticeTimer); refNotice.hidden = true;
        if (disposed) return;
        say(text || '', false, where);
    };
    const references = actions.references ? createEditorReferences({t, source: actions.references.source, size: actions.references.size, seconds: actions.references.seconds, error: value => actions.error(value, null, sayAtRefs), notify: sayAtRefs}) : null;
    if (references) { description.before(references.element); tray.append(refNotice); }
    // A field that comes or goes beside the description (the reference pictures before it, the duration after it) grows
    // or shrinks over 150 ms, together with its gap, so the description box narrows or widens smoothly instead of
    // jumping; at once under reduced motion, while the bar's contents are not shown yet, and out of sight.
    const besideMoves = new Map();
    const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    function slideBeside(node, showing) {
        besideMoves.get(node)?.cancel();
        if (reducedMotion() || host.classList.contains('fv-ed-pending') || !host.getClientRects().length || typeof node.animate !== 'function') return;
        if (!showing) node.classList.add('fv-ed-beside-leaving');
        const width = node.getBoundingClientRect().width, gap = parseFloat(getComputedStyle(host).columnGap) || 0;
        if (!width) { node.classList.remove('fv-ed-beside-leaving'); return; }
        const side = node === references?.element ? 'marginRight' : 'marginLeft';
        const shut = {width: '0px', [side]: `${-gap}px`, opacity: 0}, open = {width: `${width}px`, [side]: '0px', opacity: 1};
        node.style.overflow = 'hidden';
        const move = node.animate(showing ? [shut, open] : [open, shut], {duration: 150, easing: 'ease-out'});
        besideMoves.set(node, move);
        const done = () => { if (besideMoves.get(node) !== move) return; besideMoves.delete(node); node.style.removeProperty('overflow'); node.classList.remove('fv-ed-beside-leaving'); };
        move.onfinish = done; move.oncancel = done;
    }
    const besideWatch = typeof MutationObserver === 'function' ? new MutationObserver(rows => {
        for (const row of rows) {
            const was = row.oldValue !== null, now = row.target.hidden;
            if (was !== now) slideBeside(row.target, !now);
        }
    }) : null;
    for (const node of [references?.element, duration.element].filter(Boolean)) besideWatch?.observe(node, {attributes: true, attributeFilter: ['hidden'], attributeOldValue: true});
    let status = null, serial = 0, disposed = false, pressing = new Set(), previewSelection = null, estimateCache = null, noteTimer = null, queuedAt = null, heldAt = null, openedAt = null, openTimer = null;
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
    // A held line is a reason the user acts on (such as a part-length limit): it stays until another
    // part is chosen or the timeline is edited, instead of fading after a few seconds.
    const heldKey = () => JSON.stringify([state.selection ?? null, state.step ?? 0]);
    function say(text, queued = false, where = null, held = false) {
        if (disposed) return;
        queuedAt = text && queued ? progressKey() : null;
        heldAt = text && held ? heldKey() : null;
        clearTimeout(noteTimer); note.hidden = !text;
        // One row in the bar: the words are cut with an ellipsis (said whole on hover), the report button keeps its place.
        const words = el('span', 'fv-ed-gen-note-text', text || '');
        if (text) { words.setAttribute('data-fv-tip-name', t('Message', '提示')); words.setAttribute('data-fv-tip-use', text); }
        note.replaceChildren(words);
        if (text && where) note.append(issueButton(t, () => ({where, message: text})));
        if (text && !held) noteTimer = setTimeout(() => { note.hidden = true; showService(); }, where ? 20000 : 6000);
        else if (!text) showService();
    }
    async function render() {
        if (disposed) return;
        if (queuedAt != null && progressKey() !== queuedAt) say('');
        if (heldAt != null && heldKey() !== heldAt) say('');
        const current = ++serial, selection = previewSelection || state.selection;
        const selectedFailure = !previewSelection && actions.selectedFailure?.();
        if (failureId !== (selectedFailure?.id || null)) {
            failure.querySelector('.fv-report-tip')?.remove();
            failureId = selectedFailure?.id || null;
        }
        failure.hidden = !selectedFailure;
        failureReason.textContent = selectedFailure?.text || '';
        repair.hidden = !pageRepair(selectedFailure?.text);
        // A piece of music has no generation receipt to report.
        report.hidden = !selectedFailure || selectedFailure.report === false;
        const running = ['queued', 'running', 'cancelling'].includes(status?.state);
        const musical = !!music?.target(selection);
        const narrated = !previewSelection && !musical && !!narration?.target(selection);
        const dub =!previewSelection && voice?.controller.target(selection) ? voice.controller.session(voice.controller.target(selection)) : null;
        const voiced = !!dub && dub.phase !== 'none' && dub.phase !== 'error';
        const installing = !previewSelection && !!voice?.install?.visible(selection);
        const selected = !!selection && (!!previewSelection || musical || narrated || (actions.supportedSelection?.() ?? true));
        // While a part generates, choosing another place offers Generate again; it is queued behind.
        // A press is being sent for the parts in `pressing`; only that same selection waits for it.
        const sending = pressing.has(JSON.stringify(state.selection ?? null)), starting = pressing.size > 0 && !running;
        const other = (running || starting) && !sending && selected && !previewSelection && actions.selectionRunning?.() === false;
        // A part pressed while it could not start yet waits in line; its Cancel takes it out of the line.
        const held = !previewSelection && !!actions.selectionHeld?.();
        const busy = sending || held || (running || starting) && !other;
        // The selected part is still being sent: it can only be stopped once it has started.
        const opening = (starting || !!actions.selectionSending?.()) && !held;
        // Nothing has started yet, so there is nothing to cancel; a long start counts its seconds instead.
        clearTimeout(openTimer);
        if (!opening) openedAt = null;
        else { openedAt ??= Date.now(); openTimer = setTimeout(render, 500); }
        const stopped = ['failed', 'cancelled', 'interrupted'].includes(status?.state);
        host.dataset.mode = busy ? 'running' : selected ? 'selected' : 'idle';
        host.dataset.voice = voiced || installing ? 'true' : dub?.phase === 'error' ? 'note' : 'false'; voicePanel?.update(); syncInstall(); installPanel?.update();
        hint.hidden = !busy && !other;
        // The description and Generate stay where they are while a part is made, greyed, so nothing around them moves;
        // music being made shows only its progress and Cancel.
        input.hidden = false; generate.hidden = (musical || narrated) && busy || installing;
        // A choice that will move to an empty spot can still be described first.
        const writable = selected || !previewSelection && !!selection && !!actions.selectionMovable?.();
        input.disabled = !writable || busy;
        const sound = writable && !musical && !!actions.soundLed?.();
        input.placeholder = !writable ? t('Describe the part to generate', '描述要生成的片段') : musical ? t('Describe the music', '描述配乐') : narrated ? t('Write what to say', '写下要说的话') : sound ? t('Describe the picture', '描述画面') : t('Describe this part', '描述这一段');
        // The timeline's own stretch carries 'video-empty-range' (drawn on the picture row); the bar has a name of its own.
        if (sound) host.dataset.tourId = 'sound-range-bar'; else delete host.dataset.tourId;
        input.setAttribute('aria-label', t('Description', '描述'));
        // A video part becomes the next step once it has words: with the box empty Generate stays a secondary key (never
        // greyed; pressing it still asks whether to go on without a description).
        const wordless = !voiced && !musical && !narrated && !input.value.trim();
        generate.dataset.wordless = String(wordless);
        generate.classList.toggle('fv-primary', (voiced ? voice.controller.changed(dub).length > 0 : selected && !wordless) && !busy);
        // A cancel already accepted is not offered again; the bar says it is stopping until the run ends.
        const cancelling = status?.state === 'cancelling' && !held;
        const finalization = clipFinalization(status);
        cancel.hidden = !busy || opening || cancelling || !held && finalization?.cancelable === false; resume.hidden = !stopped;
        const waiting = status?.state === 'queued' && !(status.blocks || []).some(block => ['queued', 'running'].includes(block.state) && block.prompt_id);
        const dubbingNow = voiced && dub.phase === 'dubbing', finding = voiced && dub.phase === 'finding';
        if (dubbingNow || finding) {
            hint.hidden = false;
            hint.textContent = finding ? t('Finding the lines', '正在识别台词') : dubbing.dubbingText(Math.max(1, dub.index), dub.count, dub.remaining, t);
        } else if (narrated && !busy && narration.picking) { hint.hidden = false; hint.textContent = t('Click a sound clip on the timeline', '在时间线上点一个声音片段'); }
        else if (narrated && !busy && narration.voices().finding) { hint.hidden = false; hint.textContent = t('Finding the lines', '正在识别台词'); }
        else if (opening) hint.textContent = startingText(Math.floor((Date.now() - openedAt) / 1000), t);
        else if (held || waiting) hint.textContent = waiting && !held ? waitingText(status.waiting, t) : t('Queued', '排队中');
        else if (cancelling) hint.textContent = t('Stopping…', '正在停止…');
        else if (running) {
            const blocks = status?.blocks || [], done = blocks.filter(block => block.state === 'done').length;
            const number = Math.min(blocks.length || 1, Math.max(done, status?.chainProgress?.index || 0) + 1), count = blocks.length || 1;
            // No parts (music, a download) means no time to add up, not "about 1 s".
            const remaining = status?.chainProgress?.overall?.remaining_seconds ?? (blocks.length && blocks.every(block => Number.isFinite(block.remaining_seconds)) ? blocks.reduce((sum, block) => sum + block.remaining_seconds, 0) : null);
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
        duration.update(busy);
        firstFrame.update(busy);
        if (presets) {
            const musicBar = musical && (selected || busy);
            // Written words alone make the music: the style and mood show only while there are none (the pick is kept).
            presets.hidden = !musicBar || !!input.value.trim();
            if (musicBar) {
                const value = music.preset(selection);
                for (const key of ['style', 'mood']) { pickers[key].value = value[key]; pickers[key].disabled = busy || !!previewSelection; }
            }
            host.classList.toggle('fv-ed-genbar-music', musicBar);
        }
        references?.update(busy, busy ? undefined : actions.references.visible() || false);
        // A piece already made, failed or stopped is made again in place; separated music is replaced in place.
        const aimed = musical ? music.target(selection) : narrated ? narration.target(selection) : null;
        voiceRow?.update(narrated, busy);
        if (narrated && voiceRow) host.classList.add('fv-ed-genbar-say'); else host.classList.remove('fv-ed-genbar-say');
        const made = !!aimed?.clip && aimed.clip.pending !== 'draft';
        // "Preview first" names the press after what it makes; a part already made is made again as a preview.
        const previewing = !musical && !narrated && !!actions.previewFirst?.(selection);
        const remade = previewing && selection?.kind === 'clip' && state.record.tracks[0].clips.some(clip =>
            (selection.ids.includes(clip.id) || selection.ids.includes(clip.link)) && clip.source);
        const verb = aimed?.replace ? t('Replace music', '替换配乐') : made ? t('Regenerate', '重新生成')
            : remade ? t('Regenerate preview', '重新生成预览') : previewing ? t('Generate preview', '生成预览') : t('Generate', '生成');
        label(verb);
        if (passMenu) {
            const pass = actions.passMode();
            // While a part generates, the bar shows its progress and Cancel; the choice waits for the next press.
            passMenu.hidden = !pass || musical || narrated || voiced || generate.hidden || busy;
            if (pass) passControl.sync(pass);
            quality = effortControl && actions.effort();
            // Without a quality state (a panel that has only the two-pass choice) the menu shows that choice alone.
            if (effortControl) { effortControl.element.hidden = !quality; if (twoPassTitle) twoPassTitle.hidden = !quality; }
            if (quality) {
                const levels = actions.effortLevels?.(); if (levels) effortControl.setLevels(levels);
                // A part that continues, or one of several parts, samples 8 + 3 steps at the Light or Medium level: the control says so and is not offered.
                const lock = !musical && !narrated ? actions.effortLock?.(selection) : null;
                if (lock) {
                    const factor = continuationUpscale(quality.baseSteps, quality.twoPass, quality.refineSteps, quality.stepCache, quality.upscale, levels || undefined);
                    const medium = effortFor(8, true, 3, null, levels || undefined, Number(factor))?.name === 'Medium';
                    quality = {baseSteps: 8, refineSteps: 3, twoPass: true, stepCache: null, upscale: Number(factor), disabled: true,
                        reason: lock === 'continuation'
                            ? (medium ? t('A continuation uses the Medium level.', '接着生成使用标准档。') : t('A continuation uses the Light level.', '接着生成使用轻量档。'))
                            : (medium ? t('Each part of a video made in parts uses the Medium level.', '分段生成时，每段使用标准档。') : t('Each part of a video made in parts uses the Light level.', '分段生成时，每段使用轻量档。'))};
                }
                effortControl.update(quality);
                // On the wrapper too: a disabled control may not raise its own hover.
                effortControl.element.title = lock ? quality.reason : '';
                if (!previewing) effortControl.setEstimate(stepsText(quality.baseSteps, quality.twoPass, quality.refineSteps));
            }
            if (passMenu.hidden) passMenu.open = false;
        }
        if (voiced) label(dubbing.redubLabel(voice.controller.changed(dub).length, t));
        // Never disabled for a reason the press itself can explain or fix; only until the server says it can generate.
        // A new line needs a voice and its words; with either missing there is nothing the press could say.
        generate.disabled = busy || !!previewSelection || !narrated && !!actions.generationWaiting?.() || narrated && (!input.value.trim() || !narration.voice()) || (voiced &&((dub.phase !== 'ready' && dub.phase !== 'done') || !voice.controller.changed(dub).length));
        // While it dubs only the progress sentence and Cancel stay; while it recognises there is nothing to press yet.
        if (voiced) { generate.hidden = dub.phase === 'dubbing' || dub.phase === 'finding'; cancel.hidden = dub.phase !== 'dubbing'; cancel.disabled = false; }
        resizeDescription();
        input.dispatchEvent(new Event('change'));
        tip(generate, {name: verbText.textContent, key: `${modKey}↵`,
            use: downloadText() || (musical ? t('Make music for the chosen stretch', '为选中的范围生成配乐') : narrated ? t('Say the words in the chosen voice', '用选中的音色说出这段话')
                : voiced ? t('Say the changed lines again', '把改过的台词重新配音') : !selected ? t('Choose a part or drag a range on the timeline first', '先在时间线上选中一段或拖出范围')
                : generate.dataset.wordless === 'true' ? t('Write a description first', '先写描述') : t('Make the chosen part from the description', '按描述生成选中的这一段')),
            why: !generate.disabled ? '' : busy ? t('This part is generating; Cancel stops it', '这一段正在生成，可按“取消”停止')
                : previewSelection ? t('Release the range first', '先松开拖动的范围')
                : !narrated && actions.generationWaiting?.() ? t('Waiting for generation to be ready', '正在等生成服务就绪')
                : narrated ? t('Choose a voice and write the words', '先选音色并写下要说的话')
                : voiced ? t('Change a line first', '先改一句台词') : t('Drag a range on the timeline first', '先在时间线上拖出一段')});
        tip(cancel, {name: t('Cancel', '取消'), use: t('Stop this part; it stays as a draft', '停止生成，这一段留作草稿')});
        tip(resume, {name: t('Continue', '继续'), use: t('Start the stopped parts again', '把停下的段接着生成')});
        historyButton.disabled = input.disabled;
        tip(historyButton, {name: t('Earlier descriptions', '用过的描述'), use: t('Reuse a description you generated with', '重用以前生成时写过的描述'), why: input.disabled ? t('Choose a part first', '先选中一段') : ''});
        tip(input, {name: t('Description', '描述'), use: t('What this part should show', '写下这一段要生成的内容'), key: `${modKey}↵ ${t('generates', '生成')}`,
            why: input.disabled ? (busy ? t('This part is generating', '这一段正在生成') : t('Choose a part or drag a range first', '先选中一段或在时间线上拖出范围')) : ''});
        if (assets) { if (selected && !busy && !musical && !narrated) assets.refresh(); else assets.element.hidden = true; }
        showMusicAssets(selected && !busy && musical && !previewSelection);
        if (sayAssets) {
            const bytes = narrated && !busy ? narration.downloadBytes() : 0;
            sayAssets.textContent = bytes > 0 ? t(`Downloads before generating: dubbing model ${size(bytes)}`, `生成前会先下载：配音模型 ${size(bytes)}`) : '';
            sayAssets.hidden = !(bytes > 0);
        }
        if (selected && !busy && !voiced && !installing) {
            const key = JSON.stringify(selection);
            if (estimateCache?.key === key) label(verb, estimateCache.time);
            const seconds = await actions.estimate(selection);
            if (disposed || current !== serial) return;
            const time = seconds == null ? '' : timeEstimate(seconds, t);
            estimateCache = {key, time};
            label(verb, time);
        }
    }
    function resizeDescription() {
        // 18 px lines + 16 px padding + 2 px border: one line is still 36 px, five lines 108 px.
        input.style.height = '0px';
        // One line is the box's own minimum (36 px, 44 px on a phone); only lines beyond it grow the bar.
        const style = typeof getComputedStyle === 'function' ? getComputedStyle(input) : null;
        const base = Math.max(36, parseFloat(style?.minHeight) || 36);
        // The box's own top and bottom borders (none when the frame is drawn around the box instead).
        const borders = style ? (parseFloat(style.borderTopWidth) || 0) + (parseFloat(style.borderBottomWidth) || 0) : 2;
        const height = Math.max(base, (input.scrollHeight || 34) + borders);
        input.style.height = `${Math.min(108, height)}px`;
        input.style.overflowY = height > 108 ? 'auto' : 'hidden';
        host.closest('.fv-editor')?.style.setProperty('--fv-ed-description-growth', `${Math.max(0, Math.min(108, height) - base)}px`);
    }
    let width = null;
    const resize = typeof ResizeObserver === 'function' ? new ResizeObserver(entries => {
        const next = entries[0]?.contentRect.width;
        if (next === width) return;
        width = next; resizeDescription();
    }) : null;
    resize?.observe(description);
    // On a phone a shown note takes a line of its own above the box; the generation row grows by that line so the
    // buttons below stay clear of the timeline toolbar.
    const phone = typeof matchMedia === 'function' ? matchMedia('(max-width: 680px)') : null;
    const fitNote = () => {
        if (disposed) return;
        const line = phone?.matches && !note.hidden ? Math.ceil(note.getBoundingClientRect().height) + 8 : 0;
        host.closest('.fv-editor')?.style.setProperty('--fv-ed-note-growth', `${line}px`);
    };
    const noteResize = typeof ResizeObserver === 'function' ? new ResizeObserver(fitNote) : null;
    noteResize?.observe(note); phone?.addEventListener?.('change', fitNote); fitNote();
    // The box shows the description of the planned parts chosen and is written to them when the choice moves
    // on, the box is left or Generate is pressed. What is typed with no part or range chosen goes to the next
    // part chosen that has no description yet.
    // written: what is to be written, which the enlarged box has before the small one.
    let bound = null, dirty = false, loose = false, committing = false, written = '';
    let boxFor = null;
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
        if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
        // ⌘/Ctrl+Enter generates, as in the creative panel and the image view; Enter alone starts a new line.
        if ((event.ctrlKey || event.metaKey) && !event.shiftKey) {
            event.preventDefault();
            if (!generate.disabled && !generate.hidden) generate.onclick();
        }
    };
    // A large first download (the dubbing model) is asked about in the tray before it starts (E26).
    const askDownload = el('div', 'fv-ed-gen-ask'); askDownload.hidden = true; askDownload.setAttribute('role', 'alertdialog'); tray.append(askDownload);
    function confirmDownload(bytes) {
        const words = el('span', 'fv-ed-gen-ask-text', t(`The first line downloads the dubbing model (${size(bytes)}) before it is made.`, `第一次配音要先下载配音模型（${size(bytes)}），下载完再生成。`));
        const go = el('button', 'fv-primary', t('Download and generate', '下载并生成')), no = el('button', null, t('Not now', '先不下载'));
        go.type = no.type = 'button';
        tip(go, {name: t('Download and generate', '下载并生成'), use: t('Fetch the model once, then say the line', '下载一次模型，然后生成这句')});
        tip(no, {name: t('Not now', '先不下载'), use: t('Nothing is downloaded', '不下载任何内容'), key: 'Esc'});
        go.onclick = () => { askDownload.hidden = true; void press(false, true); };
        no.onclick = () => { askDownload.hidden = true; generate.focus(); };
        askDownload.replaceChildren(words, no, go); askDownload.hidden = false; go.focus();
    }
    // The empty-description question stands on the bar right over Generate, at every width, in the box's own word.
    // It goes last in the bar, so Generate keeps its split half (the pass options) as its next sibling.
    let emptyWatch = null;
    const askEmpty = () => {
        emptyWatch?.disconnect();
        const watch = new ResizeObserver(() => place());
        const box = showEmptyPrompt({t, anchor: host.lastElementChild, field: input, onContinue: () => press(true),
            text: t('The description is empty.', '描述为空。'), onGone: () => watch.disconnect()});
        // The press is still settling (Generate's label, Cancel), so it follows the keys until it is gone.
        const place = () => {
            if (!box.isConnected) { watch.disconnect(); return; }
            const bar = host.getBoundingClientRect(), key = generate.getBoundingClientRect(), edge = getComputedStyle(host);
            const end = passMenu && !passMenu.hidden ? passMenu.getBoundingClientRect().right : key.right;
            box.style.right = `${Math.round(bar.right - parseFloat(edge.borderRightWidth) - end)}px`;
            box.style.bottom = `${Math.round(bar.bottom - parseFloat(edge.borderBottomWidth) - key.top) + 8}px`;
        };
        for (const node of [host, ...host.children]) if (node !== box) watch.observe(node);
        emptyWatch = watch;
        place();
    };
    const press = async (allowEmpty = false, allowDownload = false) => {
        const key = JSON.stringify(state.selection ?? null);
        if (pressing.has(key)) return;
        pressing.add(key); say(''); render();
        commit();
        remember(input.value);
        const shown = input.value;
        let reason = '';
        try {
            // The press shows before any planning, however long that takes.
            await painted();
            const result = actions.press ? await actions.press(input.value, allowEmpty || allowDownload ? {allowEmpty, allowDownload} : undefined) : await actions.generate({target: 'both', text: input.value});
            if (result?.confirmDownload) confirmDownload(result.confirmDownload);
            // Never over what the user typed meanwhile, for another part.
            if (result?.text != null && result.text !== input.value && input.value === shown) input.value = result.text;
            if (result?.say) { say(result.say, !!result.queued); reason = result.say; }
            if (result?.empty) askEmpty();
            if (result?.focus) { pressing.delete(key); render(); input.disabled = false; input.focus(); }
        }
        // A specific reason is said once; the planning limits are already shown above.
        catch (error) { const result = actions.error(error, 'generate'); reason = result?.say || '';
            if (result?.say && !['chain_too_long', 'chain_part_too_short', 'chain_short_parts', 'chain_source_full', 'timed_text', 'middle_frames'].includes(result.reason)) say(result.say, false, ['Editor generation', '剪辑器生成']);
            else if (!result && !note.textContent) { reason = t('Generation could not start. Try again; if it keeps failing, report the problem.', '生成没能开始。请重试；仍然失败时，请提交问题。'); say(reason, false, ['Editor generation', '剪辑器生成']); } }
        finally { pressing.delete(key); render(); }
        return reason;
    };
    generate.onclick = () => press();
    const expand = createExpandButton({t, textarea: input, onGenerate: () => generate.onclick(), canGenerate: () => !generate.disabled, onDraft: text => typed(text)});
    description.append(expand.element);
    // Descriptions used before, one press away (the box no longer starts from nothing every time). Nothing is stored in this
    // browser: the list is what this page has generated with, then the descriptions the edit's parts and the recent works
    // carry (kept once, with the edit and the work, and gone with them). A list an earlier version kept here is removed.
    const HISTORY_MAX = 20, used = [];
    try { localStorage.removeItem('fv-editor-descriptions'); } catch { /* No storage: nothing to remove. */ }
    const readHistory = () => [...new Set([...used, ...(actions.descriptions?.() || [])].map(text => String(text || '').trim()).filter(Boolean))].slice(0, HISTORY_MAX);
    function remember(text) {
        const value = String(text || '').trim(); if (!value) return;
        used.splice(0, used.length, value, ...used.filter(old => old !== value).slice(0, HISTORY_MAX - 1));
    }
    const historyButton = el('button', 'fv-ed-history-button'); historyButton.type = 'button'; historyButton.setAttribute('aria-haspopup', 'menu'); historyButton.setAttribute('aria-expanded', 'false');
    { const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('aria-hidden', 'true'); svg.classList.add('fv-ed-glyph');
      const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', 'M2.5 8a5.5 5.5 0 1 0 1.6-3.9M2.5 2.5v2.6h2.6M8 5v3l2 1.5'); svg.append(path); historyButton.append(svg); }
    historyButton.setAttribute('aria-label', t('Earlier descriptions', '用过的描述'));
    const historyList = el('div', 'fv-ed-history'); historyList.hidden = true; historyList.setAttribute('role', 'menu'); tray.append(historyList);
    function closeHistory(focus = false) { historyList.hidden = true; historyButton.setAttribute('aria-expanded', 'false'); if (focus) historyButton.focus(); }
    function openHistory() {
        const items = readHistory();
        historyList.replaceChildren(el('div', 'fv-ed-history-title', t('Earlier descriptions', '用过的描述')));
        if (!items.length) historyList.append(el('p', 'fv-ed-history-empty', t('Descriptions you generate with show here.', '生成过的描述会出现在这里。')));
        for (const text of items) {
            const item = el('button', 'fv-ed-history-item', text); item.type = 'button'; item.setAttribute('role', 'menuitem');
            tip(item, {name: t('Use this description', '用这段描述'), use: t('Put it in the box; nothing is generated yet', '放进描述框，还不会生成')});
            item.onclick = () => { input.value = text; typed(text); closeHistory(); render(); input.focus(); };
            historyList.append(item);
        }
        historyList.hidden = false; historyButton.setAttribute('aria-expanded', 'true');
        historyList.querySelector('.fv-ed-history-item')?.focus();
    }
    historyButton.onclick = () => historyList.hidden ? openHistory() : closeHistory();
    historyList.onkeydown = event => {
        const items = [...historyList.querySelectorAll('.fv-ed-history-item')]; if (!items.length) return;
        const at = items.indexOf(document.activeElement);
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); items[(at + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus(); }
    };
    const closeHistoryOutside = event => { if (!historyList.hidden && !historyList.contains(event.target) && !historyButton.contains(event.target)) closeHistory(); };
    document.addEventListener('pointerdown', closeHistoryOutside, true);
    description.insertBefore(historyButton, expand.element);
    // A part waiting in line leaves the line at once (it stays a draft); a run that has started asks before it stops.
    cancel.onclick = async () => {
        try {
            if (!previewSelection && actions.selectionHeld?.()) { await actions.unhold(); render(); return; }
            if (actions.confirmStop && !await actions.confirmStop(cancel)) return;
            await actions.cancel();
        } catch (value) { actions.error(value); }
    };
    resume.onclick = () => actions.resume().catch(value => actions.error(value, 'generate'));
    // Words written for the picture are not words for the music, and the other way round: moving between the two
    // keeps each one's own box text, also across a dropped choice, so music never starts from a picture prompt.
    const boxDrafts = {music: '', video: ''};
    const unsubscribe = state.subscribe(event => {
        if (event.type === 'selection') {
            const now = !state.selection ? null : music?.target(state.selection) ? 'music' : 'video';
            if (!now) { if (boxFor) boxDrafts[boxFor] = input.value; }
            else {
                if (boxFor && now !== boxFor) {
                    boxDrafts[boxFor] = input.value;
                    input.value = boxDrafts[now];
                    if (state.selection.kind === 'range') state.setRangeText(input.value);
                }
                boxFor = now;
            }
        }
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
        if (event.type === 'selection') askDownload.hidden = true;
        if (event.type === 'selection' || event.type === 'record') render();
    });
    render();
    // Escape arrives through the editor's popup chain: an open two-pass menu closes first.
    function dismiss(event) {
        // Picking a voice on the timeline stops; the stretch stays chosen.
        if (narration?.picking) { event.preventDefault(); event.stopPropagation(); narration.stopPicking(); return true; }
        if (!askDownload.hidden) { event.preventDefault(); event.stopPropagation(); askDownload.hidden = true; generate.focus(); return true; }
        if (!historyList.hidden) { event.preventDefault(); event.stopPropagation(); closeHistory(true); return true; }
        if (!passMenu?.open) return false;
        // Escape while the knob is dragged is the control's own cancel (it handles it); the menu stays.
        if (effortControl?.element.querySelector('.fv-effort-rail[data-dragging]')) return false;
        event.preventDefault(); event.stopPropagation(); passMenu.open = false; passMenu.querySelector('summary').focus();
        return true;
    }
    return {element: host, duration, firstFrame, tray, say, downloadFailure, commit, dismiss, press: allowEmpty => press(allowEmpty === true), canGenerate: () => !generate.disabled,
        service(value) {
            service = value;
            if (!value && note.contains(retryService)) { retryService.remove(); note.hidden = true; }
            showService();
        }, getText() { expand.commit(); return input.value; }, shown: () => input.value, type(value) { input.value = value || ''; typed(); render(); }, leave() { input.onblur?.(); }, update(value) { status = value; render(); }, preview(selection) { previewSelection = selection; render(); }, setText(value) { input.value = value || ''; state.setRangeText(input.value); if (bound && input.value !== bound.text) { dirty = true; written = input.value; } render(); },
        dispose() { disposed = true; ++serial; downloadWatch?.disconnect(); besideWatch?.disconnect(); for (const move of besideMoves.values()) move.cancel(); clearTimeout(noteTimer); clearTimeout(openTimer); stopPass?.(); stopEffort?.(); effortControl?.dispose(); passControl?.dispose(); document.removeEventListener('pointerdown', closePass, true); document.removeEventListener('pointerdown', closeHistoryOutside, true); clearTimeout(refNoticeTimer); duration.dispose(); unsubscribe(); resize?.disconnect(); noteResize?.disconnect(); emptyWatch?.disconnect(); phone?.removeEventListener?.('change', fitNote); host.closest('.fv-editor')?.style.removeProperty('--fv-ed-note-growth'); expand.dispose(); references?.dispose(); voicePanel?.dispose(); installPanel?.dispose(); voiceRow?.dispose(); assets?.dispose(); Object.values(pickers).forEach(picker => picker.dispose()); }};
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
