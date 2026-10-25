import {createLanAccess} from './lan_access.js';
import {createEditorText} from './editor_text.js';
import {isNetworkTypeError, connectionLostText} from './browser_extension.js';
import {createToolbarMore} from './editor_toolbar_more.js';
import {isText} from './editor_text_model.js';
import {viewSwitch, viewChanged} from './view_navigation.js';
import {wordmark} from './wordmark.js';
import {openSetup} from './setup.js';
import {feedbackButton} from './feedback.js';
import {issueButton} from './issue_button.js';
import * as model from './editor_model.js';
import {discardEmptyEdit, hiddenEmptyEdit, deleteEditText} from './editor_lifecycle.js';
import {createEditorState} from './editor_state.js';
import {createEditorStore, sourceKey, mediaURL} from './editor_store.js';
import {generationSize, continuedSize} from './editor_generation.js';
import {createEditorLibrary} from './editor_library.js';
import {createEditorPlayer} from './editor_player.js';
import {createEditorTransport, registerEditorShortcuts} from './editor_transport.js';
import {createAdjustPanel} from './editor_adjust.js';
import {createAdjustPicture} from './editor_adjust_picture.js';
import {pictureSession} from './editor_picture_session.js';
import {createPictureTools} from './editor_frame.js';
import {openExportDialog} from './editor_export_dialog.js';
import {createAudioIntegration, handleAudioShortcut, AUDIO_SHORTCUTS} from './editor_audio.js';
import {createSpeedComponent, readSpeedAudioSamples, handleSpeedKey, speedShortcuts} from './editor_speed.js';
import {createEditorGenbar, loadEditorComponents, editorBlockStates, blockMessages} from './editor_genbar.js';
import {createProgressConnection} from './progress_connection.js';
import {createSelectionActions, deletionStatus, isMultiple, isTyping, playheadIds, removableGaps, removeGaps} from './editor_select.js';
import {isolateDialogKeys, topDialog} from './dialog_keys.js';
import {createBlockNotes, describedTrack} from './editor_notes.js';
import {createFirstRunGuide} from './first_run_guide.js';
import {SHOWCASE_SCENE} from './entry_showcase.js';
import {createEditorFailures} from './editor_failure.js';
import {fitEditorHeader} from './editor_header_fit.js';
import {optionalText, clonePrompt} from './optional_inputs.js';
import {outputNotWritable} from './error_panel.js';
import {repairAction} from './repair_hint.js';
import {expandCast} from './cast_mention.js';
const editorStyles = Promise.all(['result_actions.css', 'editor.css', 'editor_expand.css', 'editor_tracks.css', 'editor_frame.css', 'editor_adjust.css', 'editor_adjust_picture.css', 'editor_preview_picture.css', 'editor_select.css', 'editor_menu.css', 'editor_text.css', 'editor_notes.css', 'editor_transport.css'].map(name => {
    const style = document.createElement('link'); style.rel = 'stylesheet'; style.href = new URL(name, import.meta.url).href;
    const ready = 'sheet' in style ? new Promise(resolve => { style.onload = style.onerror = resolve; }) : Promise.resolve();
    document.head.append(style); return ready;
}));
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const language = () => new URLSearchParams(location.search).get('freevideo_lang') || navigator.language;
// A half of a surrogate pair left by a cut or a paste is dropped; the description limit counts characters, not code units.
export const wellFormed = text => typeof text === 'string' ? text.replace(/[\ud800-\udbff][\udc00-\udfff]|[\ud800-\udfff]/g, pair => pair.length === 2 ? pair : '') : text;
export const characters = text => Array.from(text).length;
export const textLongText = (t, limit) => t('The description is too long. Shorten it to %d characters or fewer.', '描述过长，请缩短到 %d 字以内。').replace('%d', limit);
// The engine's limits on continuing parts, refused before a generation starts; the engine says the same.
export const PARTS_LIMIT_CODES = ['chain_too_long', 'chain_part_too_short', 'chain_short_parts'];
export function partsLimitText(code, t = en => en, limit = null) {
    const {followSeconds: least, fullSeconds: full, maxParts, shortMaxParts} = model.RULES;
    if (code === 'chain_part_too_short') return t(`Each continued part must be at least ${least} seconds. Make it longer.`, `接着生成的每一段至少 ${least} 秒，请加长这一段。`);
    if (code === 'chain_short_parts') { const most = limit ?? shortMaxParts; return t(`With a part shorter than ${full} seconds, one continuous shot can have up to ${most} parts. Make every part at least ${full} seconds long, or use fewer parts.`,
        `有短于 ${full} 秒的段时，一个连续镜头最多 ${most} 段。请把每段加长到至少 ${full} 秒，或减少段数。`); }
    return t(`One continuous shot can have up to ${limit ?? maxParts} parts. Make it shorter.`, `一个连续镜头最多 ${limit ?? maxParts} 段，请缩短。`);
}
// Shared by error() and actions.reasonText: one sentence naming the failed step.
export function editorErrorText(value, t = en => en) {
    if (isNetworkTypeError(value) || isNetworkTypeError(value?.detail)) return connectionLostText(t);
    // The worker's own "FreeVideo generation failed (exit 1)" header names nothing the user can act on.
    const oneLine = value => typeof value === 'string' ? value.trim().split(/[\r\n]+/).find(line => line.trim() && !/^\s*FreeVideo generation failed(?: \(exit -?\d+\))?\s*$/.test(line))?.trim() || '' : '';
    const detail = value?.detail || (value?.error && typeof value.error === 'object' ? value.error : value);
    const raw = [detail?.response?.error?.message, detail?.response?.error, detail?.response?.message,
        detail?.exception_message, detail?.message, value?.error?.message, value?.error, value].map(oneLine).find(Boolean) || '';
    const whole = (() => { try { return JSON.stringify(value?.detail ?? value?.error ?? value ?? '').slice(0, 65536); } catch { return ''; } })();
    const code = value?.code || detail?.code || value?.reason || detail?.reason || raw;
    const node = oneLine(detail?.node_type), step = `${value?.phase || ''} ${value?.stage || ''} ${node}`;
    const part = Number.isInteger(value?.index) ? value.index + 1 : null;
    const generating = part ? t(`Generating part ${part}`, `生成第 ${part} 段`) : t('Generation', '生成');
    const stage = /encod|编码/i.test(step) ? t('Preparing', '准备生成') : /decod|解码/i.test(step) ? t('Making the video', '合成视频')
        : /load|model|加载/i.test(step) ? t('Loading the model', '加载模型') : generating;
    if (code === 'gpu_memory' || /out of memory|cuda.*memory|显存不足|显存不够/i.test(raw))
        return t(`${stage}: not enough GPU memory.`, `${stage}时显存不足。`);
    const known = {
        chain_part_too_short: t(`Each continued part must be at least ${model.RULES.followSeconds} seconds.`, `接着生成的每一段至少 ${model.RULES.followSeconds} 秒。`),
        chain_short_parts: t(`With a part shorter than ${model.RULES.fullSeconds} seconds, one continuous shot can have up to ${model.RULES.shortMaxParts} parts.`,
            `有短于 ${model.RULES.fullSeconds} 秒的段时，一个连续镜头最多 ${model.RULES.shortMaxParts} 段。`),
        chain_too_long: t(`One continuous shot can have up to ${model.RULES.maxParts} parts.`, `一个连续镜头最多 ${model.RULES.maxParts} 段。`),
        too_long: t('This part is longer than the maximum length. Shorten it and try again.', '这一段超过了最长时长，请缩短后再试。'),
        missing_node: t('Generation is not ready. Restart FreeVideo.', '生成功能没有就绪，请重新启动 FreeVideo。'),
        chain_missing: t('Generation could not start. Choose "Generate" again.', '生成没能开始，请再点“生成”。'),
        result_mismatch: t('Could not read the result. Choose "Generate" again.', '没能读取结果，请再点“生成”。'),
        no_material: t('The media this part uses is missing. Import it again from the library.', '找不到这一段用到的素材，请在素材库重新导入。'),
    };
    if (known[code]) return known[code];
    if (outputNotWritable(whole))
        return t('The output folder cannot be written to, so the video was not saved. Check that the folder can be written to, then retry.', '输出目录不可写，视频没能保存。请检查这个文件夹的写入权限后重试。');
    if (/manifest\.json/i.test(whole) && /No such file|not found|Errno 2\b|FileNotFoundError/i.test(whole))
        return t('The model is not fully installed. ', '模型没有装全。') + t(...repairAction());
    if (['model_missing', 'missing_model', 'model_not_found'].includes(code) || /(?:model|checkpoint|模型).*(?:not found|missing|不存在|缺失)/i.test(raw))
        return t(`Loading the model failed: ${raw || code}`, `加载模型时出错：${raw || code}`);
    if (['timeout', 'timed_out', 'generation_timeout'].includes(code) || /timed?\s*out|timeout|超时/i.test(raw))
        return t(`${stage} timed out. Try again.`, `${stage}时等待超时，请再试一次。`);
    const received = raw || (/^[a-z_]+$/.test(code) ? '' : code);
    return received ? t(`${generating} failed: ${received}`, `${generating}时出错：${received}`)
        : t(`${generating} failed. Try again.`, `${generating}时出错，请再试一次。`);
}
function exportErrorText(value, t) {
    if (isNetworkTypeError(value)) return connectionLostText(t);
    const message = [value?.response?.error?.message, value?.response?.error, value?.response?.message, value?.message]
        .find(text => typeof text === 'string' && text.trim() && text !== 'editor_request')?.trim().split(/[\r\n]+/)[0];
    // A bare status code means nothing to the user; the server's own words do.
    return message ? t(`Export failed: ${message}`, `导出时出错：${message}`) : t('Export failed. Try again.', '导出时出错，请再试一次。');
}
// The save route's own messages are internal English, so only a full disk is named.
function saveErrorText(value, t) {
    if (isNetworkTypeError(value)) return connectionLostText(t);
    return value?.reason === 'disk_full' ? t('Saving changes failed: the disk is full. Free some space, then export again.', '保存改动时出错：磁盘空间不够，空出空间后再导出。')
        : t('Saving changes failed. Please export again.', '保存改动时出错，请再导出一次。');
}
// Names the failed save for the report: the server's reason, else the HTTP status, a lost connection, or no failure recorded yet.
function saveReason(failure) {
    if (!failure) return 'unsaved';
    return failure.reason || (failure.status ? 'http_' + failure.status : isNetworkTypeError(failure) ? 'network' : undefined);
}
function restoreGeneration(record, before, prepared) {
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    const entries = value => new Map(value.tracks.flatMap(track => track.clips.map(clip => [clip.id, {track: track.id, clip}])));
    const originals = entries(before), submitted = entries(prepared), current = entries(record), restore = new Map();
    const changed = new Set([...originals.keys(), ...submitted.keys()].filter(id => !same(originals.get(id), submitted.get(id))));
    for (const id of changed) if (same(current.get(id), submitted.get(id))) restore.set(id, originals.get(id));
    const next = {...record, tracks: record.tracks.map(track => ({...track, clips: [
        ...track.clips.filter(clip => !restore.has(clip.id)),
        ...[...restore.values()].filter(value => value?.track === track.id).map(value => value.clip),
    ].sort((a, b) => a.start - b.start)})).filter(track => track.clips.length || before.tracks.some(old => old.id === track.id) || !prepared.tracks.some(old => old.id === track.id))};
    for (const key of new Set([...Object.keys(before), ...Object.keys(prepared)])) {
        if (['tracks', 'id', 'revision', 'serial'].includes(key) || same(before[key], prepared[key]) || !same(record[key], prepared[key])) continue;
        if (Object.hasOwn(before, key)) next[key] = before[key]; else delete next[key];
    }
    return next;
}
const icon = path => { const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('aria-hidden', 'true'); svg.classList.add('fv-ed-glyph'); const line = document.createElementNS(svg.namespaceURI, 'path'); line.setAttribute('d', path); svg.append(line); return svg; };
function basicBlockProgress(clipId, clipElement, t = en => en) {
    const element = el('div', 'fv-ed-basic-progress'), water = el('div', 'fv-ed-basic-water'), percent = el('span', 'fv-ed-basic-percent');
    element.append(water, percent);
    let level = 0, run = null;
    return {element, update(value) {
        if (value.run && value.run !== run) { level = 0; run = value.run; }
        element.dataset.state = value.state;
        const fraction = level = value.state === 'waiting' ? 0 : Math.max(level, Math.max(0, Math.min(1, value.fraction || 0))); water.style.height = `${fraction * 100}%`;
        const queued = value.state === 'waiting' && !!value.queued;
        element.dataset.queued = queued ? 'true' : 'false';
        percent.textContent = queued ? t('Queued', '排队中') : `${Math.floor(fraction * 100 + 1e-8)}%`;
        percent.hidden = (value.state !== 'generating' && !queued) || clipElement.dataset.track !== 'v1' || clipElement.getBoundingClientRect().width < 44;
        water.hidden = value.state !== 'generating';
    }, dispose() { element.remove(); }};
}
let opened, lastDraft = null;
const editorViews = new Map(), viewPrefix = 'fv-editor-view:', usedPrefix = 'fv-editor-used:';

// A pending ComfyUI node listens on document, including above sibling dialogs
// and during native library drags. Suspend the canvas, preserving its placement
// state and handlers so returning to Nodes can finish the same gesture.
export function suspendCanvasPointer(canvas) {
    if (!canvas) return () => {};
    const saved = [];
    for (const name of ['processMouseMove', 'processMouseDown', 'processMouseUp', 'processMouseWheel', 'processMouseCancel']) {
        if (typeof canvas[name] !== 'function') continue;
        const descriptor = Object.getOwnPropertyDescriptor(canvas, name), blocked = () => {};
        saved.push({name, descriptor, blocked}); canvas[name] = blocked;
    }
    const pan = canvas._autoPan;
    pan?.stop();
    return () => {
        for (const {name, descriptor, blocked} of saved) if (canvas[name] === blocked) {
            if (descriptor) Object.defineProperty(canvas, name, descriptor);
            else delete canvas[name];
        }
        if (pan && canvas._autoPan === pan && canvas.state?.ghostNodeId != null) pan.start();
    };
}

// `components` replaces the optional progress module (see loadEditorComponents); tests also pass their generation stand-in here.
export function openEditor({api, app, from = null, panel = {}, components: given = undefined, onView = view => viewChanged(view)}) {
    if (opened) {
        const dialog = opened.element;
        const upper = topDialog();
        if (upper !== dialog && !dialog.contains(upper) && upper?.querySelector('.fv-view-switch')) {
            // Re-enter the native modal top layer and keep keyboard routing in
            // the same order. The restored editor and other views stay alive.
            if (dialog.open) dialog.close();
            dialog.parentNode.append(dialog); dialog.showModal();
        }
        dialog.focus(); return opened;
    }
    const t = (en, zh) => String(language()).toLowerCase().startsWith('zh') ? zh : en;
    const returnFocus = document.activeElement;
    const even = value => Math.max(2, Math.floor(value / 2) * 2);
    const initial = () => model.emptyProject({width: even(panel.width || 1344), height: even(panel.height || 768), settings: panel.settings || null,
        name: t('Untitled edit', '未命名剪辑')});
    const state = createEditorState(initial());
    const resumeCanvasPointer = suspendCanvasPointer(app?.canvas);
    const element = el('dialog', 'fv-studio fv-editor'); element.setAttribute('aria-label', t('Edit', '剪辑视图')); element.tabIndex = -1;
    const listeners = [];
    const listen = (type, handler) => { element.addEventListener(type, handler); listeners.push(() => element.removeEventListener(type, handler)); };
    // Keep editor mouse events from ComfyUI's document bubble handlers (ghost-node placement).
    for (const type of ['pointermove', 'mousemove', 'pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'dblclick', 'wheel', 'contextmenu'])
        listen(type, event => event.stopPropagation());
    const top = el('header', 'fv-header fv-ed-top'), libraryHost = el('aside', 'fv-ed-library'), preview = el('section', 'fv-ed-preview'),
        transport = el('div', 'fv-ed-transport'), genHost = el('div', 'fv-ed-genbar'), tracksHost = el('div', 'fv-ed-tracks'), zoomHost = el('div', 'fv-ed-zoom');
    const adjustHost = el('aside', 'fv-ed-adjust-host');
    tracksHost.dataset.fvGuide = 'editor-timeline';
    const button = (label, action, cls = '') => { const value = el('button', cls, label); value.type = 'button'; value.onclick = action; return value; };
    const navigation = viewSwitch('editor', () => handle.switchView('studio'), () => handle.switchView('nodes'), () => {}, () => handle.switchView('autopilot'));
    for (const control of navigation.querySelectorAll('button')) control.removeAttribute('title');
    const brand = el('div', 'fv-brand');
    const headerActions = el('div', 'fv-header-actions');
    const title = button('', () => menu.hidden ? showMenu() : menu.hidden = true, 'fv-ed-name'); title.setAttribute('aria-haspopup', 'menu');
    const drawer = button(t('Media', '素材'), () => { libraryHost.dataset.open = libraryHost.dataset.open !== 'true'; drawer.setAttribute('aria-expanded', libraryHost.dataset.open); }, 'fv-ed-drawer-toggle');
    drawer.setAttribute('aria-expanded', 'false');
    const exportBox = el('div', 'fv-ed-export-box'), exportHint = el('span', 'fv-ed-export-hint'), exportButton = button(t('Export', '导出'), async () => {
        try {
            if (explainExport()) return;
            if (exportStatus?.state === 'done' && exportMatches()) { await actions.export(); return; }
            const options = await openExportDialog({parent: element, state, sources: store.sources, t, onError: error});
            if (options && !disposed) await actions.export(options);
        } catch (value) { error(value, 'export'); }
    }, 'fv-primary fv-ed-export-main');
    const exportCancel = button('', () => store.cancelExport(exportStatus.id).catch(value => error(value, null, undefined, 'cancel-export'))); exportCancel.hidden = true; exportCancel.append(icon('m4 4 8 8M12 4l-8 8')); exportCancel.setAttribute('aria-label', t('Cancel', '取消'));
    exportBox.append(exportHint, exportButton, exportCancel); brand.append(wordmark(), title);
    const phoneAccess = createLanAccess(t, api);
    listeners.push(phoneAccess.dispose);
    headerActions.append(exportBox, phoneAccess.element, feedbackButton(t), button(t('Settings', '设置'), openSetup, 'fv-quiet'));
    top.append(brand, navigation, headerActions);
    const headerFit = fitEditorHeader(top, brand, title, navigation, headerActions);
    const menu = el('div', 'fv-ed-name-menu'); menu.hidden = true; menu.setAttribute('role', 'menu');
    // Up and Down move through the name box and the items; Home and End go to either end.
    menu.onkeydown = event => {
        if (event.defaultPrevented || event.isComposing || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)
            || (['Home', 'End'].includes(event.key) && event.target.matches('input'))) return;
        const items = [...menu.querySelectorAll('input, [role=menuitem], [role=menuitemradio]')].filter(node => !node.disabled && !node.closest('[hidden]'));
        if (!items.length) return;
        event.preventDefault(); event.stopPropagation();
        const at = items.indexOf(document.activeElement), step = event.key === 'ArrowDown' ? 1 : -1;
        items[event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : at < 0 ? 0 : (at + step + items.length) % items.length].focus();
    };
    const saveWarning = el('span', 'fv-ed-save-warning'); saveWarning.hidden = true; saveWarning.setAttribute('role', 'status'); exportBox.prepend(saveWarning);
    const notice = el('div', 'fv-ed-notice'); notice.hidden = true; notice.setAttribute('role', 'status');
    transport.append(drawer);
    brand.append(menu);
    element.append(top, libraryHost, preview, transport, genHost, tracksHost, zoomHost, notice);
    element.append(adjustHost);
    tracksHost.inert = genHost.inert = true; title.disabled = true;
    // CSS delays the pending indication; fast opens stay at full opacity.
    tracksHost.classList.add('fv-ed-pending'); genHost.classList.add('fv-ed-pending');
    let discardOnSwitch = false, initialSelection = null, offerInitial = null, extendEntry = null;
    let viewKey = null, viewReady = false;
    let library = null, closing = false, closePromise = null, exportSnapshot = null, lastSeed = null, projectEpoch = 0;
    let disposed = false, suppressSave = false, savedContent = '', initializing = true, tracks = null, genbar = null, adjustPanel = null, exportStatus = null, pointerEditing = false, deferredStamp = null;
    let capability = {maxSeconds: 15, maxFollowSeconds: 15, maxRequestFrames: null, estimate: () => null}, generationBusy = false, switching = false, generationDone = null, finishGeneration = null;
    let noticeTimer = null, stopExport = null, mostRecentJob = null, exportPreparing = false, guide = null;
    const jobWatchers = new Map(), progress = new Map(), jobStatuses = new Map(), runProgress = new Map();
    const failures = createEditorFailures({api, state, jobStatuses, reasonText: value => editorErrorText(value, t)});
    let submissionFailure = null;
    let blockStates = null, progressObserver = null, progressConnection = null;
    function barJob(job) {
        if (!job) return job;
        const latest = [...runProgress.values()].filter(message => message.chain.id === job.id)
            .filter(message => job.blocks?.some(block => block.id === message.chain.block && ['running', 'queued'].includes(block.state)))
            .sort((a, b) => (a.received_at || 0) - (b.received_at || 0)).at(-1);
        const words = panel.progressWords?.() || null;
        const loadingClip = !job.blocks?.some(block => ['pending', 'queued', 'running'].includes(block.state))
            && job.blocks?.some(block => block.state === 'done' && block.output && ['queued', 'running'].includes(model.attemptClip(state.record, block.id)?.pending));
        return {...job, ...(loadingClip ? {state: 'running', loadingClip: true} : {}),
            ...(latest ? {chainProgress: latest.chain, progressMessage: latest} : {}), words};
    }
    function paintJob(job) {
        const messages = blockMessages([...runProgress.values()].filter(message => message.chain.id === job.id),
            message => panel.progressSnapshot?.(message));
        const values = editorBlockStates(job, messages, blockStates);
        for (const block of job.blocks || []) {
            const clip = model.attemptClip(state.record, block.id); if (!clip || clip.job !== job.id) continue;
            const loading = block.state === 'done' && block.output && ['queued', 'running'].includes(clip.pending);
            const value = {...values[block.id], ...(loading ? {state: 'generating', fraction: .99} : {}), run: `${job.id}:${block.prompt_id || block.attempts || 0}`};
            if (value.state === 'generating') value.fraction = Math.min(.99, value.fraction || 0);
            for (const pair of model.pair(state.record, block.id)) { progress.set(pair.id, value); tracks?.setProgress(pair.id, value); }
        }
    }
    function receiveProgress(message) {
        phoneAccess.observe(message);
        if (disposed || !message.chain?.id || !message.chain.block) return false;
        const job = jobStatuses.get(message.chain.id);
        if (!job || String(job.node ?? panel.node?.id ?? panel.node ?? message.node) !== String(message.node)) return false;
        const block = job.blocks?.find(block => block.id === message.chain.block);
        if (!block || !model.attemptClip(state.record, block.id)) return false;
        const key = `${job.id}:${block.id}`, previous = runProgress.get(key);
        // A resumed attempt has a new prompt_id. Never replay the cancelled run.
        if (message.prompt_id && block.prompt_id && message.prompt_id !== block.prompt_id) return false;
        if (previous?.prompt_id === message.prompt_id && previous?.stream_id === message.stream_id
            && Number.isFinite(previous?.sequence) && previous.sequence >= message.sequence) return true;
        failures.receive(job, block, message);
        runProgress.set(key, message); paintJob(job);
        if (model.attemptClip(state.record, block.id)?.pending === 'failed') tracks?.refresh?.();
        if (mostRecentJob === job.id) genbar?.update(barJob(job));
        return true;
    }
    const content = record => JSON.stringify({...record, id: null, revision: 0});
    function failureKind(site, value) {
        const code = value?.reason || value?.code || value?.name;
        return site + ':' + (String(code || '').replace(/[^A-Za-z0-9_.-]/g, '').slice(0, 40) || 'unknown');
    }
    // `where` marks a failure: the notice then carries the "Report a problem" button and stays longer to be reached.
    function notify(text, where = null, kind = null) {
        if (disposed) return;
        if (!text) { clearTimeout(noticeTimer); notice.hidden = true; return; }
        notice.textContent = text;
        if (where) notice.append(issueButton(t, () => ({where, message: text, kind})));
        notice.hidden = false; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { notice.hidden = true; }, where ? 20000 : 9000);
    }
    const EDITOR_ISSUE = ['Editor', '编辑器'], EXPORT_ISSUE = ['Export', '导出'];
    // `step` names what was being done, so a failure there says which step went wrong; `out` is where the line is said (the bar's own line for a reference picture).
    function error(value, step = null, out = notify, site = step || 'editor') {
        const code = value?.reason || value?.code || value?.message;
        if (['missing_glyphs', 'MissingGlyphs', 'source_missing'].includes(code)) {
            out(code === 'source_missing' ? t('A source file is missing. Import it again from the library.', '有素材的原文件找不到了，请在素材库重新导入。')
                : t('The chosen font is missing some characters. Choose another font.', '所选字体缺少部分字符，请换一种字体。'));
            return {ok: false, reason: code};
        }
        if (['audio_prepare', 'speed_pending', 'speed_locked', 'speed'].includes(code)) return {ok: false, reason: code};
        if (code === 'unsupported') { genbar?.update(null); return; }
        if (code === 'text_long') {
            const text = textLongText(t, Number.isInteger(value.limit) ? value.limit : 8000);
            out(text); return {ok: false, reason: code, say: text};
        }
        if (PARTS_LIMIT_CODES.includes(code)) {
            const [en, zh] = (value.said || []).map(text => typeof text === 'string' && text ? text.slice(0, 200) : null);
            const text = en && zh ? t(en, zh) : partsLimitText(code, t, Number.isInteger(value.limit) ? value.limit : null);
            out(text); return {ok: false, reason: code, say: text};
        }
        if (['busy', 'locked', 'text', 'audio_full', 'overlap', 'range', 'no_audio'].includes(code)) {
            if (['locked', 'audio_full', 'overlap', 'range'].includes(code)) tracks?.flashBlocked?.(value.displayFrame);
            const said = {busy: t('This part is generating.', '这一段正在生成。'), locked: t('This track is locked.', '这条轨道已锁定。'),
                text: t('Write a description first.', '先写一句描述。'), overlap: t('That spot is taken. Use an empty spot.', '这里已经有片段了，请放到空白处。'),
                audio_full: t('Both audio tracks are full here. Move an audio clip first.', '这里两条声音轨都满了，请先挪开一段声音。'),
                no_audio: t('This clip has no sound. Please choose one with sound.', '这个素材没有声音，请选一段有声音的。')}[code];
            if (said) out(said);
            return {ok: false, reason: code};
        }
        const text = step === 'generate' ? editorErrorText(value, t) : step === 'export' ? exportErrorText(value, t) : t('Could not complete. Try again.', '没有完成，请重试');
        out(text, step === 'export' ? EXPORT_ISSUE : EDITOR_ISSUE, failureKind(site, value)); return {ok: false, reason: code, say: text};
    }
    const store = createEditorStore(api, {t, notify, standIn: given?.generationStandIn, onGenerationChecking: () => { if (!disposed) settleService(); }, onGenerationReady: ready => {
        if (disposed) return;
        // Only a server that said it cannot generate hides the bar; an unanswered check keeps it, greyed.
        generationAsked = true; genHost.hidden = !ready && store.generationGone; element.dataset.generationReady = ready ? 'true' : store.generationGone ? 'false' : 'waiting';
        settleGenbar(); settleService(); if (ready) { offerInitial?.(); extendEntry?.(); void startHeld(); }
        // Parts queued while waiting for the answer never start here; they go back to drafts with the reason.
        else if (store.generationGone && held.length) {
            for (const entry of held.splice(0)) markHeld(entry.ids, false);
            rememberView(); notify(t('Restart FreeVideo to generate.', '请重新启动 FreeVideo 后再生成。'));
        }
        tracks?.refresh?.(); genbar?.update(mostRecentJob ? barJob(jobStatuses.get(mostRecentJob)) : null);
        updateTransport(); adjustPanel?.update();
    }});
    // Until the server has answered, the bar keeps its place but cannot be used.
    let generationAsked = store.generationAvailable, loaded = false;
    const settleGenbar = () => {
        // While the engine is still answering, a press queues the part instead of doing nothing.
        genHost.inert = !loaded; genHost.classList.toggle('fv-ed-pending', !loaded);
    };
    // When every automatic check went unanswered, the bar's line offers to check again; not while a check runs.
    const settleService = () => genbar?.service(store.generationStalled ? {checking: store.generationChecking, retry: () => store.retryGeneration()} : null);
    store.generationReady();
    const queue = store.createSaveQueue({get record() { return state.record; },
        adopt(value) {
            if (pointerEditing) { deferredStamp = value; return; }
            if (!value.nameSuffix && state.record.id === value.id && state.record.revision === value.revision) return;
            suppressSave = true; try { state.adopt(value); } finally { suppressSave = false; }
            // A queued part kept under a new edit's first key moves to its saved one.
            if (held.length) rememberView();
        },
    }, {onStatus: () => {
        if (!queue.dirty) { discardOnSwitch = false; saveWarning.hidden = true; }
    }});
    const player = createEditorPlayer(preview, transport, {state, sources: store.sources, api, t, error, readAudioSamples: panel.readAudioSamples || readSpeedAudioSamples, preservePreview: () => initializing, prepareAudio: () => !initializing});
    function selectionClips(selection = state.selection) {
        if (selection?.kind !== 'clip') return [];
        return state.record.tracks[0].clips.filter(clip => selection.ids.includes(clip.id) || selection.ids.includes(clip.link));
    }
    function selectionRange(selection = state.selection) {
        if (!selection) return null;
        if (selection.kind === 'range' || selection.kind === 'gap') return {start: selection.start, frames: selection.frames, ...(selection.parts ? {parts: selection.parts} : {})};
        const clips = selectionClips(selection); if (!clips.length) return null;
        const start = Math.min(...clips.map(clip => clip.start));
        return {start, frames: Math.max(...clips.map(clip => clip.start + clip.frames)) - start};
    }
    // How the part about to be made starts: 'fresh' (pictures can guide it), 'continues' (from the footage before it) or null.
    function partStart() {
        const range = selectionRange(); if (!range) return null;
        try {
            const selected = selectionClips();
            const planned = selected.length && selected.every(clip => clip.pending && !clip.source)
                ? model.startAttempt(state.record, selected.map(clip => clip.id), 'x')
                : model.planRange(state.record, {...range, target: 'both', text: 'x', maxSeconds: capability.maxSeconds, maxFollowSeconds: capability.maxFollowSeconds});
            const ids = planned.tracks[0].clips.filter(clip => clip.start >= range.start && clip.start < range.start + range.frames
                && clip.pending && !['queued', 'running'].includes(clip.pending)).map(clip => clip.id);
            if (!ids.length) return null;
            return model.generationBefore(model.prepare(planned, ids), ids).some(before => before == null) ? 'fresh' : 'continues';
        } catch (value) { if (value instanceof model.EditError) return null; throw value; }
    }
    const jobActive = () => generationBusy || [...jobStatuses.values()].some(job => ['queued', 'running', 'cancelling'].includes(job.state));
    // Only an engine that has not answered yet leaves parts held locally.
    // Once ready, the server owns waiting parts and their dependencies.
    // Parts, and ranges not yet planned into parts, whose request is being sent.
    const held = [], sending = new Set(), sendingRanges = new Set();
    const overlaps = (a, b) => a.start < b.start + b.frames && a.start + a.frames > b.start;
    let startingHeld = false;
    const generating = clip => ['queued', 'running'].includes(clip?.pending);
    const isHeld = id => held.some(entry => entry.ids.includes(id));
    const partBefore = start => state.record.tracks[0].clips.find(clip => clip.frames > 0 && clip.start + clip.frames === start);
    // A planned part whose description is still the user's to write: not started, not being sent.
    const editablePart = clip => !!clip && !clip.source && clip.pending === 'draft' && !clip.job && !sending.has(clip.id);
    const attached = (clip, before) => !!before && before.start + before.frames === clip.start && model.continuesBefore(state.record, clip.id);
    // The planned parts Generate makes from a choice of planned parts: with the parts before them that they
    // continue and that are not started yet, as one video; a part whose earlier part is not started would
    // otherwise start a new one.
    function partRun(selection) {
        const free = clip => editablePart(clip) && !isHeld(clip.id);
        const clips = selectionClips(selection).sort((a, b) => a.start - b.start);
        if (!clips.length || !clips.every(free)) return null;
        if (!clips.every((clip, i) => !i || attached(clip, clips[i - 1]))) return clips;
        const run = [...clips];
        for (let before; attached(run[0], before = partBefore(run[0].start)) && free(before);) run.unshift(before);
        return run;
    }
    const clipChoice = clips => ({kind: 'clip', ids: clips.flatMap(clip => clip.link ? [clip.id, clip.link] : [clip.id])});
    function busySelection(selection) {
        const range = selectionRange(selection); if (!range) return false;
        if ([...sendingRanges].some(other => overlaps(other, range))) return true;
        return state.record.tracks[0].clips.some(clip => (generating(clip) || isHeld(clip.id) || sending.has(clip.id))
            && clip.start < range.start + range.frames && clip.start + clip.frames > range.start);
    }
    function waitingFor() {
        if (!store.generationAvailable) return 'ready';
        return null;
    }
    // The nearest spot at or after the chosen one where a part of the same length fits.
    function emptySpot(selection) {
        if (!['range', 'gap'].includes(selection?.kind)) return null;
        const range = selectionRange(selection), clips = state.record.tracks.filter(track => ['v1', 'a1', 'a2'].includes(track.id)).flatMap(track => track.clips);
        const starts = [range.start, ...clips.map(clip => clip.start + clip.frames).filter(at => at > range.start)].sort((a, b) => a - b);
        for (const start of starts) {
            if (clips.some(clip => clip.start < start + range.frames && clip.start + clip.frames > start)) continue;
            const moved = {kind: 'range', start, frames: range.frames, tracks: ['v1', 'a1']};
            if (actions.supportedSelection(moved)) return moved;
        }
        return null;
    }
    // A range left a sliver (under the shortest part) after a clip closes up to that clip's end.
    function flushSpot(selection) {
        if (selection?.kind !== 'range') return null;
        const range = selectionRange(selection), most = model.RULES.minSeconds * model.FPS;
        const clips = state.record.tracks.filter(track => ['v1', 'a1', 'a2'].includes(track.id)).flatMap(track => track.clips);
        const end = Math.max(-1, ...state.record.tracks[0].clips.map(clip => clip.start + clip.frames).filter(at => at < range.start));
        if (end < 0 || range.start - end >= most || clips.some(clip => clip.start < range.start && clip.start + clip.frames > end)) return null;
        const moved = {kind: 'range', start: end, frames: range.frames, tracks: ['v1', 'a1']};
        return actions.supportedSelection(moved) ? moved : null;
    }
    // A part with no description borrows the one before it, else the nearest earlier one, else any.
    function nearbyText(start) {
        const before = partBefore(start);
        if (before?.text?.trim()) return {text: before.text, before: true};
        const described = state.record.tracks[0].clips.filter(clip => clip.text?.trim()).sort((a, b) => a.start - b.start);
        const found = described.filter(clip => clip.start < start).at(-1) || described.at(-1);
        return found ? {text: found.text, before: false} : null;
    }
    // Kept in the progress map too: the tracks may not exist yet when a reopened edit restores its queued parts.
    const markHeld = (ids, queued) => { for (const id of ids) {
        const value = {state: 'waiting', fraction: 0, remainingSeconds: null, ...(queued ? {queued: true} : {})};
        progress.set(id, value); tracks?.setProgress(id, value);
    } };
    const queuedReady = () => t('Queued. It starts once generation is ready.', '已排队，准备好后自动开始。');
    function hold(selection, text, job = null, {texts = null, shown = selection === state.selection} = {}) {
        const range = selectionRange(selection), selectedIds = selectionClips(selection).map(clip => clip.id);
        let ids = [];
        if (job) ids = selectedIds;
        else state.apply(t('Generate', '生成'), record => {
            const selected = selectedIds.map(id => model.find(record, id)).filter(Boolean), planned = selected.length && selected.every(clip => !clip.source && clip.pending && !generating(clip));
            const next = planned ? model.startAttempt(record, selected.map(clip => clip.id), texts ?? text) : model.planRange(record, {...range, target: 'both', text, maxSeconds: capability.maxSeconds, maxFollowSeconds: capability.maxFollowSeconds});
            ids = next.tracks[0].clips.filter(clip => clip.start >= range.start && clip.start < range.start + range.frames && clip.pending && !generating(clip)).map(clip => clip.id);
            return next;
        }, {checkpoint: true});
        if (!ids.every(isHeld)) held.push({ids, text, job});
        markHeld(ids, true);
        if (shown) state.select({kind: 'clip', ids});
        rememberView();
    }
    async function startHeld() {
        if (startingHeld || disposed || closing || generationBusy || !store.generationAvailable) return;
        startingHeld = true;
        try {
            while (held.length && !disposed) {
                if (generationBusy || !store.generationAvailable || closing) break;
                if (held[0].job) {
                    const entry = held.shift(); markHeld(entry.ids, false);
                    if (entry.ids.some(id => model.find(state.record, id)?.job === entry.job)) {
                        try { await actions.resume(entry.job); } catch (value) { error(value, 'generate'); }
                    }
                    continue;
                }
                const entry = held[0], clips = entry.ids.map(id => model.find(state.record, id)).filter(clip => clip && clip.pending && !generating(clip));
                if (!clips.length) { held.shift(); markHeld(entry.ids, false); rememberView(); continue; }
                const selection = {kind: 'clip', ids: clips.map(clip => clip.id)}, before = partBefore(Math.min(...clips.map(clip => clip.start)));
                held.shift(); markHeld(entry.ids, false); rememberView();
                if (before && !before.source && before.pending && !before.job) { genbar?.say(t('The part before did not finish, so this one did not start.', '上一段没有完成，这一段没有开始。')); continue; }
                // Each part goes with its own description, as written while it waited.
                const texts = Object.fromEntries(clips.map(clip => [clip.id, clip.text.trim() || entry.text]));
                try { await actions.generate({text: texts[clips[0].id], texts, selection}); }
                catch (value) { const reason = error(value, 'generate'); if (reason && !PARTS_LIMIT_CODES.includes(reason.reason)) genbar?.say(reason.say); }
            }
        } finally { startingHeld = false; }
    }
    function completeSources() {
        return store.getSources(model.references(state.record).map(sourceKey)).then(() => { player.refresh(); tracks?.refresh?.(); });
    }
    const exportMatches = () => exportSnapshot && exportSnapshot.id === state.record.id && exportSnapshot.revision === state.record.revision && exportSnapshot.content === content(state.record);
    function explainExport() {
        if (initializing || switching) { notify(t('Opening the edit…', '正在打开…')); return true; }
        if (exportPreparing || ['running', 'queued'].includes(exportStatus?.state)) {
            notify(t('Exporting. You can export again when it finishes.', '正在导出，完成后可以再导出一次。'));
            exportBox.scrollIntoView?.({block: 'nearest'}); exportButton.focus({preventScroll: true}); return true;
        }
        if (!model.exportFrames(state.record)) { notify(t('The timeline is empty. Add a clip first.', '时间线是空的，请先放入素材。')); return true; }
        return false;
    }
    async function saveForExport(epoch) {
        await queue.flush();
        for (let retry = 0; queue.dirty && retry < 3; retry++) {
            await new Promise(resolve => setTimeout(resolve, 200));
            if (disposed || closing || epoch !== projectEpoch) return false;
            await queue.flush();
        }
        if (disposed || closing || epoch !== projectEpoch) return false;
        if (queue.dirty) { notify(saveErrorText(queue.failure, t), EDITOR_ISSUE, failureKind('save', {reason: saveReason(queue.failure)})); return false; }
        return !!state.record.id;
    }
    function updateTop() {
        const name = state.record.name || new Date().toLocaleDateString();
        if (title.firstElementChild?.textContent !== name) { title.replaceChildren(el('span', '', name), icon('m4 6 4 4 4-4')); headerFit.check(); }
        title.title = state.record.name;
        exportButton.classList.toggle('fv-primary', !!model.exportFrames(state.record) && !state.selection && !jobActive() && !exportStatus);
        const running = exportPreparing || ['running', 'queued'].includes(exportStatus?.state);
        if (['running', 'queued'].includes(exportStatus?.state) && exportStatus.queued) exportButton.textContent = t('Queued', '排队中');
        else if (['running', 'queued'].includes(exportStatus?.state)) exportButton.textContent = t(`Export ${Math.round((exportStatus.fraction || 0) * 100)}%`, `导出 ${Math.round((exportStatus.fraction || 0) * 100)}%`);
        else if (exportPreparing) exportButton.textContent = queue.dirty ? t('Saving changes…', '正在保存改动…') : t('Exporting', '正在导出');
        else exportButton.textContent = exportStatus?.state === 'done' && exportMatches() ? t('View', '查看') : t('Export', '导出');
        // Never disabled: a press that cannot export says why next to it.
        exportButton.disabled = false; exportButton.title = '';
    }
    async function snapshot() {
        let value = panel.snapshot ? clonePrompt(await panel.snapshot()) : clonePrompt(await app.graphToPrompt());
        if (value.output) {
            const node = String(panel.node?.id || panel.node || Object.keys(value.output).find(id => value.output[id].class_type === 'FreeVideoGenerate') || '');
            value = {node, prompt: value.output, extra_data: {extra_pnginfo: {workflow: value.workflow || {}}}};
        }
        if (!value.node || !value.prompt?.[value.node]) throw new Error('missing_node');
        const inputs = value.prompt[value.node].inputs;
        Object.assign(inputs, state.record.settings || {}, capability.generationSize || generationSize(state.record));
        // The media panel's pictures guide every part (the engine keeps only the references for a part that continues);
        // without a panel to show them they stay out of the request.
        if (!panel.references?.available()) delete inputs.media;
        delete inputs.continue_from; delete inputs.continue_to;
        return {node: String(value.node), prompt: value.prompt, ...(value.extra_data ? {extra_data: value.extra_data} : {}), ...(api.clientId ? {client_id: api.clientId} : {})};
    }
    function pinStoppedJob(record, ids, job) {
        return model.settle(record, ids.filter(id => model.attemptClip(record, id)?.job === job), 'stopped');
    }
    async function deliverChain(job) {
        if (disposed || !job) return false;
        const previous = jobStatuses.get(job.id);
        if (Number.isFinite(previous?.updated) && job.updated < previous.updated)
            return ['queued', 'running', 'cancelling'].includes(previous.state);
        failures.reconcile(job, previous);
        mostRecentJob = job.id; jobStatuses.set(job.id, job); paintJob(job);
        genbar?.update(barJob(job));
        const completed = new Set();
        for (const block of job.blocks || []) {
            const clip = model.attemptClip(state.record, block.id); if (!clip || clip.job !== job.id) continue;
            if (block.state === 'done' && block.output) {
                if (['queued', 'running'].includes(clip.pending) || !previous?.blocks?.some(old => old.state === 'done' && old.output === block.output))
                    completed.add(block.output);
                const source = {kind: 'generated', id: block.output};
                await store.getSources([sourceKey(source)]); if (disposed) return false;
                if (model.attemptClip(state.record, block.id)?.job !== job.id) continue;
                const metadata = store.sources.get(sourceKey(source));
                const result = {job: job.id, source, frames: metadata?.frames || block.inputs?.frames,
                    width: metadata?.width, height: metadata?.height,
                    skip_frames: block.skip_frames || 0, skip_audio_samples: block.skip_audio_samples || 0};
                try {
                    if (!metadata || metadata.missing) throw new model.EditError('source');
                    // settle intentionally skips incompatible undo snapshots.
                    // Validate the current clip first so a bad receipt is visible.
                    model.fill(state.record, block.id, result);
                    state.settle(record => model.fill(record, block.id, result));
                } catch (value) {
                    if (!(value instanceof model.EditError)) throw value;
                    state.settle(record => model.fail(record, block.id, 'result_mismatch', job.id));
                    error(value, null, undefined, 'fill');
                }
            } else if (block.state === 'failed') {
                const cause = block.reason || block.error;
                const reason = /^[a-z_]{1,80}$/.test(cause || '') ? cause : 'generation_failed';
                state.settle(record => model.fail(record, block.id, reason, job.id));
            } else if (['cancelled', 'interrupted'].includes(block.state) || (['cancelled', 'interrupted', 'failed'].includes(job.state) && block.state === 'pending')) {
                state.settle(record => pinStoppedJob(record, [block.id], job.id));
            } else if (block.state === 'running' && clip.pending !== 'running') state.settle(record => model.settle(record, [block.id], 'running'));
        }
        // The status word of a part the engine dropped is read from the job, which arrives after the record is drawn.
        const dropped = job => (job?.blocks || []).filter(block => block.state === 'interrupted').length;
        if (dropped(job) !== dropped(previous)) tracks?.refresh?.();
        genbar?.update(barJob(job)); updateTop(); updateTransport();
        const active = ['queued', 'running', 'cancelling'].includes(job.state);
        if (completed.size || (!active && previous?.state !== job.state) || (job.output && job.output !== previous?.output))
            library?.refresh({outputs: job.output ? [job.output] : [...completed]});
        if (!active) jobWatchers.delete(job.id);
        void startHeld();
        return active;
    }
    function watchJob(id) {
        if (disposed) return;
        if (!jobWatchers.has(id)) jobWatchers.set(id, store.watchChain(id, deliverChain));
    }
    function recoverJobs() {
        for (const id of new Set(state.record.tracks[0].clips.map(clip => clip.job).filter(Boolean))) watchJob(id);
    }
    function rememberView() {
        if (!viewReady) return;
        const key = state.record.id || viewKey;
        const value = {selection: state.selection, text: genbar.getText(), playhead: state.playhead,
            zoom: state.zoom, scrollLeft: tracks ? tracks.getScrollLeft() : editorViews.get(key)?.scrollLeft || 0};
        editorViews.set(key, value);
        try {
            // Queued parts stay queued across a reload; they are only planned drafts until sent.
            const waiting = held.map(entry => ({ids: entry.ids, text: entry.text}));
            value.held = waiting;
            localStorage.setItem(viewPrefix + key, JSON.stringify({selection: value.selection, text: value.text, ...(waiting.length ? {held: waiting} : {})}));
            if (state.record.id) localStorage.setItem('fv-editor-last', key);
            localStorage.setItem('fv-editor-opening', JSON.stringify({id: key, name: state.record.name}));
        } catch {}
        // An untouched edit belongs to this tab until it has a saved identity.
        lastDraft = !state.record.id && state.record.tracks.every(track => !track.clips.length) ? key : null;
        try {
            if (lastDraft) globalThis.sessionStorage?.setItem('fv-editor-draft', lastDraft);
            else globalThis.sessionStorage?.removeItem('fv-editor-draft');
        } catch {}
    }
    async function replaceProject(record, key = null) {
        rememberView(); viewReady = false;
        await store.activateProject(record.id).catch(() => {});
        viewKey = record.id || key || 'draft:' + (globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`);
        let remembered = editorViews.get(viewKey);
        if (!remembered) try { remembered = JSON.parse(localStorage.getItem(viewPrefix + viewKey)); } catch {}
        initialSelection = offerInitial = extendEntry = null;
        projectEpoch++; held.length = 0;
        player.pause(); for (const stop of jobWatchers.values()) stop(); jobWatchers.clear(); jobStatuses.clear(); progress.clear(); runProgress.clear(); failures.clear(); progressConnection?.reset();
        mostRecentJob = null; submissionFailure = null; genbar?.update(null); queue.reset(record); stopExport?.(); stopExport = null; exportStatus = exportSnapshot = null; exportButton.textContent = t('Export', '导出');
        suppressSave = true; try { state.replace(record); savedContent = content(record); } finally { suppressSave = false; }
        panel.onProjectLoaded?.(record);
        const width = tracksHost.clientWidth - 148; if (width > 0) state.setZoom(model.openingZoom(width, record));
        tracks?.setScrollLeft(0);
        // An empty edit opens with one part's length selected at the start, ready to generate.
        const selectInitial = () => { if (!store.generationAvailable) return; initialSelection = state.select({kind: 'range', start: 0,
            frames: Math.round(Math.min(15, capability.maxSeconds ?? 15) * model.FPS), tracks: ['v1', 'a1']}); };
        const epoch = projectEpoch;
        let pointer = null;
        const rememberPointer = event => { if (event.isTrusted) pointer = event; };
        // Once the pointer has left the tracks there is no hint to refresh there.
        const leftTracks = () => { pointer = null; };
        tracksHost.addEventListener('pointermove', rememberPointer, true); tracksHost.addEventListener('pointerleave', leftTracks);
        const forgetPointer = () => { tracksHost.removeEventListener('pointermove', rememberPointer, true); tracksHost.removeEventListener('pointerleave', leftTracks); };
        listeners.push(forgetPointer);
        // Explicit parts are the existing persisted marker for manual borders.
        // Keep them even if a later response lowers the limit; maxRequestFrames
        // still checks the real request before submission.
        const keepManualParts = value => ({...value,
            get maxSeconds() { return Math.max(value.maxSeconds, (state.selection?.parts?.[0] || 0) / model.FPS); },
            get maxFollowSeconds() { return Math.max(value.maxFollowSeconds, ...((state.selection?.parts || []).map(count => count / model.FPS))); }});
        capability = keepManualParts(store.capabilities(record, value => {
            if (disposed || epoch !== projectEpoch) return;
            capability = keepManualParts(value); tracks?.refresh?.();
            pictureTools.update();
            if (initialSelection && state.selection === initialSelection) selectInitial();
            genbar?.update(mostRecentJob ? barJob(jobStatuses.get(mostRecentJob)) : null);
            // Refresh the active gesture's existing hint through the track pointer handler.
            if (pointer) tracks?.element.dispatchEvent(new PointerEvent('pointermove', {pointerId: pointer.pointerId,
                clientX: pointer.clientX, clientY: pointer.clientY, buttons: pointer.buttons, bubbles: true}));
            forgetPointer();
        })); if (disposed) return;
        // A restored edit remains usable offline with its existing placeholders.
        await completeSources().catch(value => error(value, null, undefined, 'sources')); if (disposed) return; recoverJobs();
        state.restoreView(remembered);
        // Also when generating is confirmed only after the edit has opened.
        offerInitial = () => { if (epoch === projectEpoch && !state.selection && state.record.tracks.every(track => !track.clips.length)) selectInitial(); };
        offerInitial();
        genbar?.setText(typeof remembered?.text === 'string' ? remembered.text : '');
        tracks?.setScrollLeft(remembered?.scrollLeft);
        // Only parts still waiting as drafts come back; one that has since been sent or changed is left alone.
        for (const entry of Array.isArray(remembered?.held) && !store.generationGone ? remembered.held : []) {
            if (!Array.isArray(entry?.ids) || typeof entry.text !== 'string') continue;
            const clips = entry.ids.map(id => model.find(state.record, id));
            if (!clips.length || clips.some(clip => !clip || clip.source || clip.pending !== 'draft' || clip.job) || entry.ids.some(isHeld)) continue;
            held.push({ids: [...entry.ids], text: entry.text}); markHeld(entry.ids, true);
        }
        viewReady = true;
        if (record.id) try { localStorage.setItem('fv-editor-last', record.id); localStorage.setItem(usedPrefix + record.id, String(Date.now())); } catch {}
        updateTransport(); if (remembered?.held?.length) rememberView(); if (held.length) void startHeld();
    }
    async function switchProject(id) {
        if (switching || closing || id && id === state.record.id) return; switching = true; element.inert = true; updateTop();
        try {
            await queue.flush();
            if (queue.dirty) { return; }
            const record = id ? await store.load(id) : initial();
            if (!disposed && record) {
                if (emptyEdit()) await discardProject({automatic: true});
                await replaceProject(record); if (!id) { queue.enqueue(state.record); } menu.hidden = true;
            }
        } catch (value) {
            const reason = deleteEditText(t)[value?.reason];
            if (reason) notify(reason); else error(value, null, undefined, 'open-load');
        } finally { switching = false; element.inert = false; updateTop(); }
    }
    const emptyEdit = () => discardEmptyEdit(state.record, initial(), {busy: jobActive(), hasMedia: store.uploading});
    function forgetProject() {
        const keys = new Set([state.record.id, viewKey].filter(Boolean));
        for (const key of keys) editorViews.delete(key);
        if (keys.has(lastDraft)) lastDraft = null;
        try {
            for (const key of keys) { localStorage.removeItem(viewPrefix + key); localStorage.removeItem(usedPrefix + key); }
            if (keys.has(localStorage.getItem('fv-editor-last'))) localStorage.removeItem('fv-editor-last');
            if (keys.has(globalThis.sessionStorage?.getItem('fv-editor-draft'))) globalThis.sessionStorage?.removeItem('fv-editor-draft');
        } catch {}
        viewReady = false; // replaceProject/close must not remember the deleted edit again.
    }
    async function discardProject({automatic = false} = {}) {
        if (state.record.id) try { await store.deleteProject(state.record.id); }
        catch (value) {
            // The last window to leave will remove a shared empty edit. Earlier
            // windows must still be able to leave, quietly releasing their lease.
            if (!automatic || value?.reason !== 'project_in_use') throw value;
        }
        queue.reset(); forgetProject();
    }
    async function deleteCurrentProject() {
        if (switching || closing) return;
        switching = true; element.inert = true; updateTop();
        try {
            const words = deleteEditText(t);
            if (jobActive()) throw {reason: 'generation_active'};
            await queue.flush(); releaseStamp();
            if (queue.dirty) { notify(saveErrorText(queue.failure, t), EDITOR_ISSUE, failureKind('save', {reason: saveReason(queue.failure)})); return; }
            // Load the replacement before deleting, so a read failure keeps the edit open.
            const rows = (await store.projects()).filter(row => row.id !== state.record.id);
            // Most recently used: the order the edits were opened, then the last save.
            const order = id => { try { return Number(localStorage.getItem(usedPrefix + id)) || 0; } catch { return 0; } };
            rows.sort((a, b) => order(b.id) - order(a.id) || String(b.updated_at).localeCompare(String(a.updated_at)));
            const record = rows.length ? await store.load(rows[0].id) : initial();
            if (!record) throw new Error(words.delete_failed);
            await discardProject();
            await replaceProject(record); menu.hidden = true; title.setAttribute('aria-expanded', 'false'); title.focus();
        } catch (value) {
            const words = deleteEditText(t); notify(words[value?.reason] || words.delete_failed, EDITOR_ISSUE, failureKind('delete', {reason: value?.reason}));
        } finally { switching = false; element.inert = false; updateTop(); }
    }
    function confirmDelete() {
        const words = deleteEditText(t, state.record.name);
        menu.replaceChildren();
        menu.append(el('p', 'fv-ed-delete-question', words.confirm));
        const remove = button(words.remove, deleteCurrentProject, 'fv-ed-delete-confirm');
        const cancel = button(words.cancel, showMenu);
        menu.append(remove, cancel); cancel.focus();
    }
    async function showMenu() {
        menu.replaceChildren(); menu.hidden = false; title.setAttribute('aria-expanded', 'true');
        const name = el('input', 'fv-ed-rename'); name.value = state.record.name; name.setAttribute('aria-label', t('Edit name', '修改名字'));
        const commit = () => { if (name.value.trim()) state.apply(t('Rename', '改名'), record => model.rename(record, name.value.trim())); };
        name.onchange = commit; name.onkeydown = event => { if (event.key === 'Enter' && !event.isComposing && event.keyCode !== 229) {
            event.preventDefault(); commit(); menu.hidden = true; title.setAttribute('aria-expanded', 'false'); title.focus();
        } };
        const fresh = button(t('New', '新建'), () => switchProject(null)); fresh.setAttribute('role', 'menuitem');
        menu.append(name, fresh);
        pictureTools.appendMenu(menu);
        const remove = button(deleteEditText(t).item, confirmDelete, 'fv-ed-delete'); remove.setAttribute('role', 'menuitem'); menu.append(remove);
        try {
            // The current edit is already named in the box at the top.
            const listed = await store.projects(), unread = listed.unreadable || 0;
            const rows = listed.filter(row => row.id !== state.record.id && !hiddenEmptyEdit(row, initial())); if (disposed || menu.hidden || !menu.contains(name)) return;
            if (rows.length || unread) { const line = el('div'); line.setAttribute('role', 'separator'); menu.append(line); }
            for (const row of rows) { const item = button(row.name || new Date(row.updated_at).toLocaleDateString(), () => switchProject(row.id), 'fv-ed-project'); item.setAttribute('role', 'menuitem'); item.title = item.textContent; menu.append(item); }
            if (unread) menu.append(el('p', 'fv-ed-unreadable', t(`${unread} ${unread === 1 ? 'edit' : 'edits'} could not be read`, `另有 ${unread} 个剪辑无法读取`)));
        } catch (value) { error(value, null, undefined, 'projects'); }
        name.focus(); name.select();
    }
    const actions = {
        error, editError: error, notify, sources: store.sources, waveform: key => store.waveform(key), mediaURL: value => mediaURL(api, value),
        projectToken: () => initializing ? null : projectEpoch,
        togglePlay: () => player.toggle(),
        pause: () => player.pause(),
        async previewSource(source) {
            if (closing || disposed) return;
            const cached = store.sources.get(sourceKey(source));
            if (!initializing || (!cached?.video && !cached?.audio)) await store.getSources([sourceKey(source)]);
            if (!closing && !disposed) player.previewSource(source);
        },
        generationLimits: () => ({maxSeconds: capability.maxSeconds, maxFollowSeconds: capability.maxFollowSeconds}),
        generationAvailable: () => store.generationAvailable,
        // The server was asked and has not said yes: Generate is greyed until it does.
        generationWaiting: () => generationAsked && !store.generationAvailable,
        importFiles: (files, options) => library?.importFiles(files, options),
        gestureStart({selectionGesture = false} = {}) {
            // A description being typed is written to its part before the gesture, which a later edit would stop.
            genbar?.commit();
            pointerEditing = true;
            if (selectionGesture && !menu.hidden) { menu.hidden = true; updateTransport(); }
        },
        gestureEnd() { releaseStamp(); },
        insertSource(value, {loadMetadata = false} = {}) {
            // Early library double-clicks/Enter must target the restored edit.
            if (initializing) return handle.ready.then(() => {
                if (!initializing && !closing && !disposed) return actions.insertSource(value, {loadMetadata});
            });
            if (loadMetadata) {
                const epoch = projectEpoch;
                return store.getSources([sourceKey(value.source)]).then(() => {
                    if (!closing && !disposed && epoch === projectEpoch) return actions.insertSource(value);
                });
            }
            const metadata = store.sources.get(sourceKey(value.source));
            try { state.apply(t('Insert clip', '放入素材'), record => model.insertSource(record, {
                ...value, width: metadata?.width ?? value.width, height: metadata?.height ?? value.height, text: value.text ?? metadata?.text,
                at: value.at ?? model.duration(record)}), {clearRange: state.selection !== initialSelection});
                return {ok: true};
            } catch (valueError) {
                if (!(valueError instanceof model.EditError)) throw valueError;
                valueError.displayFrame = value.at ?? model.duration(state.record); error(valueError, null, undefined, 'insert');
                return {ok: false, reason: valueError.code};
            }
        },
        reasonText: value => editorErrorText(value, t),
        failureText: id => failures.text(id),
        // A part that was running when the engine went away, as opposed to one the user stopped.
        interrupted: clip => clip.pending === 'stopped' && !clip.source && !!clip.job
            && (jobStatuses.get(clip.job)?.blocks || []).some(block => block.state === 'interrupted' && model.attemptClip(state.record, block.id)?.id === clip.id),
        selectedFailure() {
            const clip = state.selection?.kind === 'clip' && state.record.tracks.flatMap(track => track.clips).find(clip => state.selection.ids.includes(clip.id) && clip.pending === 'failed');
            if (clip) return {id: clip.id, text: failures.text(clip.id)};
            return submissionFailure === JSON.stringify(state.selection) ? {id: 'submission', text: failures.text('submission')} : null;
        },
        async downloadFailure(id) {
            if (disposed || id !== 'submission' && !model.find(state.record, id)) return;
            if (id !== 'submission') state.select({kind: 'clip', ids: [id]});
            await genbar.downloadFailure(actions.selectedFailure()?.id || id);
        },
        downloadFailureReport: (id, options) => failures.download(id, options),
        supportedSelection(selection = state.selection) {
            if (selection?.kind === 'clip' && selection.ids.some(id => isText(model.find(state.record, id)))) return false;
            const range = selectionRange(selection); if (!range) return false;
            if (selection.kind === 'range' && !['v1', 'a1'].every(id => selection.tracks.includes(id))) return false;
            if (selection.kind === 'gap' && selection.track !== 'v1') return false;
            try {
                const selected = selectionClips(selection);
                if (selected.length && selected.every(clip => clip.pending && !clip.source))
                    model.prepare(state.record, selected.map(clip => clip.id));
                else model.planRange(state.record, {...range, target: 'both'});
                return true;
            }
            catch (value) { if (value instanceof model.EditError) return value.code === 'busy'; throw value; }
        },
        // Another part generating is no reason: the server runs parts one after another.
        canGenerate(text, selection = state.selection) {
            return store.generationAvailable && actions.supportedSelection(selection) && (!!text.trim() || !actions.needsText(selection));
        },
        // The description the box shows for a choice of planned parts, and the parts it is written to; null for
        // any other choice.
        partText(selection = state.selection) {
            const clips = selectionClips(selection).sort((a, b) => a.start - b.start);
            return clips.length && clips.every(editablePart) ? {ids: clips.map(clip => clip.id), text: clips[0].text} : null;
        },
        describeParts(ids, text) {
            if (!ids.every(id => editablePart(model.find(state.record, id)))) return false;
            try { state.apply(t('Edit description', '修改说明'), record => model.describePlanned(record, ids, text)); return true; }
            catch (value) { if (value instanceof model.EditError) return false; throw value; }
        },
        needsText(selection = state.selection) {
            const range = selectionRange(selection); if (!range) return true;
            // Only a part pulled out of a finished one needs no description: it continues that one, with that one's
            // description when it has none of its own. A finished video without one (an exported edit) still needs it.
            const head = state.record.tracks[0].clips.find(clip => clip.start === range.start);
            const before = !!head && model.continuesBefore(state.record, head.id) && state.record.tracks[0].clips.some(clip =>
                clip.start + clip.frames === range.start && clip.source && clip.extend && clip.in + clip.frames === clip.length
                && !!(head.text?.trim() || clip.text?.trim()));
            return !before;
        },
        // Only where earlier footage is continued: a part right after an extendable clip, or a range split into several parts.
        firstFrameVisible() {
            if (!store.generationAvailable || !actions.supportedSelection()) return false;
            const range = selectionRange(); if (!range) return false;
            const head = state.record.tracks[0].clips.find(clip => clip.start === range.start);
            const before = !!head && model.continuesBefore(state.record, head.id) && state.record.tracks[0].clips.some(clip =>
                clip.start + clip.frames === range.start && clip.source && clip.extend && clip.in + clip.frames === clip.length);
            return before || model.rangeParts(state.record, {...range, ...capability}).length > 1;
        },
        // Pictures that guide a new part: the creative panel's media rows; a part that continues shows only its references.
        references: panel.references ? {source: panel.references, notify, size: () => capability.generationSize || generationSize(state.record),
            visible: () => store.generationAvailable && actions.supportedSelection() ? partStart() : null} : null,
        setFirstFrame: value => { state.apply(t('First-frame reference', '首帧参考'), record => model.setFlag(record, 'first_frame_reference', value)); },
        // The sampling data and models the next generated part would still download first.
        assets: {api, query: () => {
            const size = capability.generationSize || generationSize(state.record), set = state.record.settings || {};
            return new URLSearchParams({width: size.width, height: size.height, seconds: 5, task: 't2va', enhance: '0',
                base_steps: set.base_steps ?? 8, refine_steps: set.refine_steps ?? 3, two_pass: set.two_pass === false ? '0' : '1'});
        }},
        previewRange: selection => genbar?.preview(selection),
        async estimate(selection) {
            const run = partRun(selection), range = selectionRange(run ? clipChoice(run) : selection); if (!range) return null;
            let seconds = 0;
            for (const count of model.rangeParts(state.record, {...range, ...capability})) { const value = capability.estimate(count); if (value == null) return null; seconds += value; }
            return seconds;
        },
        // texts: a description for each planned part, by the part's picture; otherwise text is every part's.
        async generate({target = 'both', text = '', texts = null, label = t('Generate', '生成'), selection = state.selection, shown = selection === state.selection} = {}) {
            if (target !== 'both' || !actions.supportedSelection(selection)) throw new model.EditError('unsupported');
            // A half of a pair left by a cut or a paste is dropped; a description over the limit is said before anything is sent.
            text = wellFormed(text);
            if (!actions.canGenerate(text, selection)) throw new model.EditError('text');
            if (characters(text) > model.RULES.maxText) throw Object.assign(new model.EditError('text_long'), {limit: model.RULES.maxText});
            submissionFailure = null; genbar?.update(null);
            const chosen = state.selection;
            generationBusy = true; updateTop(); updateTransport();
            generationDone = new Promise(resolve => { finishGeneration = resolve; });
            let ids = [], prepared = null, beforeAttempt = null, priorSelection = null, priorText = null, submittedSelection = null, frozen = null;
            const epoch = projectEpoch, range = selectionRange(selection), selectedIds = selectionClips(selection).map(clip => clip.id);
            sendingRanges.add(range);
            try {
                frozen = await snapshot(); if (epoch !== projectEpoch || closing || disposed || !store.generationAvailable) return;
                beforeAttempt = state.record; priorSelection = state.selection; priorText = state.rangeText;
                state.apply(label, record => {
                    const selected = selectedIds.map(id => model.find(record, id)).filter(Boolean), alreadyPlanned = selected.length && selected.every(clip => !clip.source && clip.pending && !['queued', 'running'].includes(clip.pending));
                    let next = alreadyPlanned ? model.startAttempt(record, selected.map(clip => clip.id), texts ?? text) : model.planRange(record, {...range, target: 'both', text, maxSeconds: capability.maxSeconds, maxFollowSeconds: capability.maxFollowSeconds});
                    if (!record.tracks[0].clips.length && text.trim()) next = model.rename(next, model.truncateText(text.trim(), 40));
                    ids = next.tracks[0].clips.filter(clip => clip.start >= range.start && clip.start < range.start + range.frames && clip.pending && !['queued', 'running'].includes(clip.pending)).map(clip => clip.id);
                    return model.prepare(next, ids);
                }, {checkpoint: true});
                for (const id of ids) sending.add(id);
                prepared = state.record;
                // Only a press on what is still chosen moves the choice; a part started in the background
                // leaves whatever the user has moved on to, and what they typed there.
                submittedSelection = shown && state.selection === chosen ? state.select({kind: 'clip', ids}) : state.selection; await completeSources();
                const paths = Object.fromEntries([...store.sources].map(([key, value]) => [key, value.path]));
                const request = model.generationRequest(prepared, ids, {text: texts ? undefined : text, paths, queue: true});
                // What the engine would refuse for its continuing parts is said here, before anything is sent.
                const problem = model.partsProblem(prepared, ids);
                if (problem) throw Object.assign(new Error(problem.code), {code: problem.code, limit: problem.limit});
                const beforeKeys = model.generationBefore(prepared, ids), sizes = new Map(store.sources);
                for (const key of beforeKeys.filter(key => key?.startsWith('chain:'))) {
                    const job = await store.chain(key.split(':')[1]);
                    sizes.set(key, job?.generation_size);
                }
                const follow = continuedSize(beforeKeys, sizes), edit = capability.generationSize || generationSize(state.record);
                Object.assign(frozen.prompt[frozen.node].inputs, follow || {});
                // Parts made at another size than the edit's have that size's limit.
                const limit = (follow && (follow.width !== edit.width || follow.height !== edit.height)
                    ? await store.requestFrames(follow, state.record.settings) : null) ?? capability.maxRequestFrames;
                const tooLong = request.blocks.filter(block => limit != null && block.frames > limit);
                if (tooLong.length) {
                    state.settle(record => tooLong.reduce((next, block) => model.fail(next, block.id, 'too_long', null), record));
                    return;
                }
                const values = new Uint32Array(1);
                if (globalThis.crypto?.getRandomValues) crypto.getRandomValues(values); else values[0] = Math.floor(Math.random() * 2 ** 32);
                const seed = values[0] === lastSeed ? (values[0] + 1) >>> 0 : values[0]; lastSeed = seed;
                // An @name in a description becomes <Subject N> and its definition, as in the creative panel; a part that
                // continues carries only the pictures the engine keeps for it, and is numbered among them.
                const castRows = panel.references?.available() ? panel.references.rows?.() ?? [] : [];
                const blocks = request.blocks.map(({before, ...block}, i) => ({...block, seed: seed + i,
                    text: expandCast(block.text, castRows, {continued: Boolean(block.after)}).text}));
                const job = await store.startChain({...frozen, blocks, queue: true});
                if (!job && !store.generationAvailable) {
                    if (epoch === projectEpoch) {
                        const restoreSelection = submittedSelection !== priorSelection && state.selection === submittedSelection;
                        // Reverse only this submission's changes. Keep save receipts,
                        // reserved IDs and unrelated edits made while POST was pending.
                        state.settle(record => restoreGeneration(record, beforeAttempt, prepared));
                        if (restoreSelection) state.select(priorSelection, {text: priorText});
                    }
                    return;
                }
                if (!job?.id) throw new Error('chain_missing');
                if (epoch !== projectEpoch) return;
                state.settle(record => model.assignJob(record, ids, job.id, {prepared}));
                queue.enqueue(state.record);
                failures.remember(job, frozen);
                mostRecentJob = job.id; jobStatuses.set(job.id, job); genbar.update(job); watchJob(job.id);
            } catch (value) {
                if (epoch === projectEpoch) {
                    if (ids.length) state.settle(record => model.settle(record, ids, 'draft'));
                    // A length limit is the user's to change and is said once in the notice; there is nothing to report.
                    if (!PARTS_LIMIT_CODES.includes(value?.code)) {
                        failures.submission(value, frozen, text); submissionFailure = JSON.stringify(state.selection);
                    }
                    genbar?.update(null);
                }
                throw value;
            } finally { sendingRanges.delete(range); for (const id of ids) sending.delete(id); if (disposed && ids.length) queue.enqueue(state.record); generationBusy = false; finishGeneration?.(); generationDone = null; finishGeneration = null; updateTop(); updateTransport(); startHeld(); }
        },
        // A press on Generate always answers: what can be done for the user is done, then said in one line.
        async press(text) {
            if (disposed || closing || initializing) return {say: t('Opening the edit…', '正在打开…')};
            let selection = state.selection, said = null;
            if (actions.selectionHeld()) return {say: t('Queued', '排队中')};
            if (busySelection(selection)) return {say: t('This part is generating.', '这一段正在生成。')};
            // A preceding POST must supply its durable ID before a dependant can reference it.
            const epoch = projectEpoch, awaiting = selectionRange(selection);
            if (generationBusy) {
                if (awaiting) sendingRanges.add(awaiting);
                updateTransport();
                try { while (generationBusy) await generationDone; }
                finally { sendingRanges.delete(awaiting); }
            }
            if (disposed || closing || epoch !== projectEpoch) return;
            if (!selectionRange(selection)) return {say: t('Drag on the timeline to choose a part first.', '先在时间线上拖出一段。')};
            if (characters(wellFormed(text)) > model.RULES.maxText) return {say: textLongText(t, model.RULES.maxText), focus: true};
            if (actions.selectionHeld()) return {say: t('Queued', '排队中')};
            if (busySelection(selection)) return {say: t('This part is generating.', '这一段正在生成。')};
            const flush = flushSpot(selection);
            if (flush) { selection = state.select(flush); said = t('Moved right after the previous part.', '已接在上一段后面。'); genbar?.say(said); }
            else if (!actions.supportedSelection(selection)) {
                const moved = emptySpot(selection);
                if (!moved) return {say: t('Choose an empty spot on the timeline.', '请在时间线上选一段空白。')};
                selection = state.select(moved); said = t('Moved to the next empty spot.', '已挪到后面的空位。'); genbar?.say(said);
            }
            let used = null, texts = null, shown = selection === state.selection;
            const before = t('Used the description of the part before.', '沿用了上一段的描述。'), latest = t('Used the latest description.', '沿用了最近一次的描述。');
            let run = partRun(selection);
            if (run) {
                // What is in the box is the chosen parts' description; each part goes with its own, and one with
                // none uses the one before it.
                const chosen = selectionClips(selection).sort((a, b) => a.start - b.start);
                if (text.trim() && text.trim() !== chosen[0].text && actions.describeParts(chosen.map(clip => clip.id), text)) run = partRun(selection);
                texts = {};
                for (const [i, clip] of run.entries()) {
                    let value = clip.text.trim();
                    if (!value && i && attached(clip, run[i - 1])) { value = texts[run[i - 1].id]; used ??= before; }
                    else if (!value) {
                        const found = nearbyText(clip.start);
                        if (!found) return {say: [said, t('Write a description first.', '先写一句描述。')].filter(Boolean).join(' '), focus: true};
                        value = found.text; used ??= found.before ? before : latest;
                    }
                    texts[clip.id] = value;
                }
                text = texts[chosen[0].id];
                selection = clipChoice(run);
            } else if (!text.trim() && actions.needsText(selection)) {
                const found = nearbyText(selectionRange(selection).start);
                if (!found) return {say: [said, t('Write a description first.', '先写一句描述。')].filter(Boolean).join(' '), focus: true};
                text = found.text; used = found.before ? before : latest;
                state.setRangeText(text); genbar?.setText(text);
            } else if (!text.trim()) {
                // The box was emptied; the part keeps the description it took over, as an empty one cannot be generated.
                text = state.record.tracks[0].clips.find(clip => clip.start === selectionRange(selection).start).text;
                state.setRangeText(text); genbar?.setText(text);
            }
            const waiting = waitingFor(selection);
            if (waiting) {
                hold(selection, texts ? texts[run[0].id] : text, null, {texts, shown});
                return {text, say: [said, used, waiting === 'ready' ? queuedReady()
                    : t('Queued. It starts after the previous part finishes.', '已排队，上一段完成后自动开始。')].filter(Boolean).join(' '), queued: true};
            }
            const busyBefore = jobActive();
            await actions.generate({text: texts ? texts[run[0].id] : text, texts, selection, shown});
            return {text, say: [said, used, busyBefore ? t('Queued. It starts after the part now generating.', '已排队，正在生成的那段完成后自动开始。') : null].filter(Boolean).join(' ') || null, queued: busyBefore};
        },
        selectionRunning: () => busySelection(state.selection),
        // A choice over existing clips that Generate would move to an empty spot.
        selectionMovable: () => !actions.supportedSelection() && !!emptySpot(state.selection),
        selectionSending() { const range = selectionRange(state.selection); return !!range && ([...sendingRanges].some(other => overlaps(other, range)) || [...sending].some(id => { const clip = model.find(state.record, id); return clip && overlaps(clip, range); })); },
        selectionHeld() { const ids = selectionClips(state.selection).map(clip => clip.id); return ids.length > 0 && ids.every(isHeld); },
        // Takes the selected parts out of the line; they stay as drafts.
        unhold() {
            const ids = selectionClips(state.selection).map(clip => clip.id);
            for (let i = held.length - 1; i >= 0; i--) if (held[i].ids.some(id => ids.includes(id))) { markHeld(held[i].ids, false); held.splice(i, 1); }
            rememberView();
        },
        async cancel() {
            const range = selectionRange(state.selection);
            const own = range && state.record.tracks[0].clips.find(clip => clip.job && generating(clip)
                && clip.start < range.start + range.frames && clip.start + clip.frames > range.start)?.job;
            // A part still being sent has no job yet; never stop another one in its place.
            if (!own && actions.selectionSending()) return;
            const id = own || mostRecentJob; if (id) await deliverChain(await store.chainAction(id, 'cancel'));
        },
        async resume(jobId = null) {
            const epoch = projectEpoch;
            const id = jobId || mostRecentJob || selectionClips().find(clip => clip.job)?.job; if (!id) return;
            if (!store.generationAvailable) {
                const ids = state.record.tracks[0].clips.filter(clip => clip.job === id && clip.pending).map(clip => clip.id);
                hold({kind: 'clip', ids}, '', id); genbar?.say(queuedReady());
                if (genHost.hidden) notify(queuedReady());
                void startHeld(); return;
            }
            const job = await store.chainAction(id, 'resume'); if (epoch !== projectEpoch || disposed) return;
            for (const block of job?.blocks || []) if (block.state !== 'done') runProgress.delete(`${id}:${block.id}`);
            const ids = state.record.tracks[0].clips.filter(clip => clip.job === id && clip.pending).map(clip => clip.id);
            state.settle(record => model.settle(record, ids, 'queued')); if (job) { await deliverChain(job); watchJob(id); }
        },
        async retry(id) {
            state.select({kind: 'clip', ids: [id]}); const clip = model.find(state.record, id); if (!clip) return;
            if (clip.job) { mostRecentJob = clip.job; await actions.resume(); }
            else if (!store.generationAvailable) {
                if (!actions.selectionHeld()) hold(state.selection, clip.text || panel.text || '');
                genbar?.say(queuedReady()); if (genHost.hidden) notify(queuedReady()); void startHeld();
            } else await actions.generate({text: clip.text || panel.text || ''});
        },
        // Subtitles from speech hear the saved project, so it is saved first; a project never saved has nothing in it to hear.
        speech: {
            async start() {
                await queue.flush();
                if (queue.dirty) return null;
                if (!state.record.id) return {state: 'failed', reason: 'no_sound'};
                for (let attempt = 0; ; attempt++) {
                    try { return await store.startEars(state.record.id, state.record.revision); }
                    catch (value) {
                        if (value.status !== 409 || attempt >= 2) throw value;
                        queue.enqueue(state.record); await queue.flush();
                        if (queue.dirty) return null;
                    }
                }
            },
            watch: (id, fn) => store.watchEars(id, fn),
            cancel: id => store.cancelEars(id),
            // Whether speech can be recognised here now, and how many bytes must be downloaded first.
            async ability() {
                const [ears, pack] = await Promise.all([store.ears(), store.earsPack().catch(() => null)]);
                return {available: ears?.transcribe === true, need: pack?.installable === true && pack.bytes > 0 ? pack.bytes : 0};
            },
            download: report => new Promise((resolve, reject) => {
                const step = value => {
                    const install = value?.install;
                    if (install?.state !== 'running') { resolve(install?.state === 'done' || install?.reason || false); return false; }
                    report(install.total > 0 ? install.done / install.total : 0); return true;
                };
                store.installEars().then(value => { if (step(value)) store.watchEarsPack(step); }, reject);
            }),
            cancelDownload: () => store.cancelEarsPack(),
        },
        async export(options) {
            if (explainExport()) return;
            if (exportStatus?.state === 'done' && exportMatches()) { await viewExport(exportStatus); return; }
            const epoch = projectEpoch; exportPreparing = true; updateTop();
            try {
                if (!await textTools.beforeExport()) return;
                if (!await saveForExport(epoch)) return;
                exportSnapshot = {id: state.record.id, revision: state.record.revision, content: content(state.record)};
                for (let attempt = 0; ; attempt++) {
                    try {
                        const status = await store.export(state.record.id, state.record.revision, options);
                        if (disposed || closing || epoch !== projectEpoch) return;
                        exportStatus = status; break;
                    }
                    catch (value) {
                        if (value.status !== 409 || attempt >= 2) throw value;
                        queue.enqueue(state.record);
                        if (!await saveForExport(epoch)) return;
                        exportSnapshot = {id: state.record.id, revision: state.record.revision, content: content(state.record)};
                    }
                }
                if (!exportStatus) return;
                updateExport(exportStatus); stopExport?.();
                stopExport = store.watchExport(exportStatus.id, updateExport, exportLost);
            } finally { exportPreparing = false; updateTop(); }
        },
    };
    async function viewExport(value) {
        // A sound-only export is not a work: it opens as a file the browser plays and can save.
        if (value.only === 'sound') { if (value.url) window.open(mediaURL(api, value.url), '_blank', 'noopener'); return; }
        const module = await import('./library.js');
        module.openLibrary(t, {selectedWork: value.work, extend: async (record, options) => {
            if (await handle.close() === false) throw new Error(saveWarning.textContent);
            await onView('studio');
            return (await import('./studio.js')).openExtendEditor(record, undefined, options);
        }});
    }
    // The engine stopped answering mid-export: leave the percentage and say so.
    function exportLost() {
        if (disposed) return;
        exportStatus = null; exportCancel.hidden = true; updateTop();
        notify(t('Lost the connection to FreeVideo, so the export progress cannot be read. Export again once it is back.', '与 FreeVideo 的连接已断开，无法读取导出进度。恢复后请重新导出。'), EXPORT_ISSUE, 'export:lost');
    }
    function updateExport(value) {
        if (!value) return false;
        if (exportSnapshot) exportSnapshot = {...exportSnapshot, id: value.project_id, revision: value.revision};
        exportStatus = value; const running = value.state === 'running' || value.state === 'queued';
        if (disposed) { if (!running) window.FreeVideoNotifyTaskDone?.({kind: 'export', id: value.id, ok: value.state === 'done'}); return running; }
        exportCancel.hidden = !running; updateTop(); exportButton.classList.remove('fv-primary');
        if (!running) {
            if (value.state === 'failed') {
                if (['source_missing', 'missing_glyphs', 'MissingGlyphs'].includes(value.reason || value.code)) error(value, null, undefined, 'export');
                else notify(value.reason === 'not_writable' ? t('Export failed: the output folder cannot be written to. Check that the folder can be written to, then retry.', '导出时出错：输出目录不可写。请检查这个文件夹的写入权限后重试。')
                    : value.reason === 'disk_full' ? t('Export failed: the disk is full. Free some space, then export again.', '导出时出错：磁盘空间不够，空出空间后再导出。')
                    : value.reason === 'unreadable' ? t('Export failed: a source file cannot be read. Import it again from the library.', '导出时出错：有素材读不出来，请在素材库重新导入。')
                    : t('Export failed. Try again.', '导出时出错，请再试一次。'), EXPORT_ISSUE, failureKind('export', value));
            }
            if (value.state === 'cancelled') notify(t('Export cancelled. No unfinished file is kept.', '已取消导出，没有留下不完整的文件。'));
            window.FreeVideoNotifyTaskDone?.({kind: 'export', id: value.id, ok: value.state === 'done'});
        }
        return running;
    }

    genbar = createEditorGenbar(genHost, {state, t, actions, text: ''}); settleService();
    const adjust = adjustPanel = createAdjustPanel(adjustHost, {state, t, actions});
    const pictureEdit = pictureSession(state, t);
    const notes = createBlockNotes({state, t, actions}); actions.blockNote = notes.decorate; actions.describedTrack = describedTrack;
    adjust.register('video', createAdjustPicture({state, sources: store.sources, t, player, notes}));
    const toolbar = el('div', 'fv-ed-toolbar'); toolbar.setAttribute('role', 'toolbar'); toolbar.setAttribute('aria-label', t('Track tools', '轨道工具')); toolbar.inert = true; toolbar.classList.add('fv-ed-pending');
    const historyTools = el('div', 'fv-ed-tool-group'), editTools = el('div', 'fv-ed-tool-group');
    const toolButton = (label, path, action, cls, iconOnly = false) => {
        const control = button('', action, cls); control.append(icon(path));
        control.classList.add('fv-ed-icon');
        control.setAttribute('aria-label', label); return control;
    };
    // Undo cannot take back a part while it generates; a press that changes nothing says so.
    function travel(back) {
        const before = content(state.record); back ? state.undo() : state.redo();
        if (content(state.record) === before && jobActive()) notify(back ? t('This part is generating. Undo once it finishes.', '这一段正在生成，完成后再撤销。')
            : t('This part is generating. Redo once it finishes.', '这一段正在生成，完成后再重做。'));
    }
    const toolUndo = toolButton(t('Undo', '撤销'), 'M5 4 2 7l3 3M2 7h7a4 4 0 0 1 0 8', () => travel(true), 'fv-ed-undo fv-ed-icon', true);
    const toolRedo = toolButton(t('Redo', '重做'), 'm11 4 3 3-3 3M14 7H7a4 4 0 0 0 0 8', () => travel(false), 'fv-ed-redo fv-ed-icon', true);
    const split = toolButton(t('Split', '剪开'), 'M6 5l8 8M6 11l8-8M6 4a2 2 0 1 1-4 0 2 2 0 0 1 4 0M6 12a2 2 0 1 1-4 0 2 2 0 0 1 4 0',
        () => selectionActions.run('b'), 'fv-ed-split');
    const remove = toolButton(t('Delete', '删除'), 'M2 4h12M6 4V2h4v2M4 4l1 10h6l1-10M7 6v5M9 6v5',
        () => selectionActions.run('delete'), 'fv-ed-delete');
    const closeGap = toolButton(t('Remove gaps', '移除空隙'), 'M1 3v10M15 3v10M3 8h4m-2-2 2 2-2 2M13 8H9m2-2-2 2 2 2',
        () => selectionActions.run('gaps'), 'fv-ed-close-gap');
    const previous = toolButton(t('Previous version', '上一版'), 'M3 6V2M3 6h4M3 6a5 5 0 1 1-1 5M8 5v3l2 2', () => {
        const clip = selectedContent().find(clip => clip.versions.length);
        if (clip) try { state.apply(t('Previous version', '上一版'), record => model.restoreVersion(record, clip.id, 0)); } catch (value) { error(value, null, undefined, 'restore'); }
    }, 'fv-ed-previous');
    historyTools.append(toolUndo, toolRedo); editTools.append(split, remove, closeGap, previous);
    const pictureTools = createPictureTools({state, sources: store.sources, t, capabilities: () => capability, error});
    toolbar.append(historyTools, editTools, zoomHost); element.insertBefore(toolbar, tracksHost);
    const transportControls = createEditorTransport({element, preview, transport, toolbar, editTools, zoomHost, state, player, t, notify});
    toolbar.append(adjust.toggleButton);
    const textTools = createEditorText({element, state, t, actions, adjust, player, toolbar, exportBox, tracksHost});
    const toolbarMore = createToolbarMore({toolbar, t, keep: '.fv-ed-undo, .fv-ed-split, .fv-ed-delete, .fv-ed-adjust-toggle, .fv-ed-zoom'});
    actions.speech.ability().then(value => { if (!disposed) textTools.speech(value); }).catch(() => {});
    const audio = createAudioIntegration({state, player, t, error, adjust, library: libraryHost, store});
    registerEditorShortcuts(AUDIO_SHORTCUTS, speedShortcuts);
    adjust.register('speed', createSpeedComponent({state, t, onError: error}));
    function selectedContent() {
        const selection = state.selection;
        if (!selection) return [];
        if (selection.kind === 'clip') return state.record.tracks.flatMap(track => track.clips).filter(clip => selection.ids.includes(clip.id));
        return state.record.tracks.filter(track => selection.tracks?.includes(track.id) || track.id === selection.track).flatMap(track => track.clips)
            .filter(clip => clip.start >= selection.start && clip.start + clip.frames <= selection.start + selection.frames);
    }
    const isBusy = clip => ['queued', 'running'].includes(clip.pending);
    function splitIds() {
        return playheadIds(state.record, state.selection, state.playhead).filter(id => {
            const clip = model.find(state.record, id); return state.playhead > clip.start && state.playhead < clip.start + clip.frames;
        });
    }
    const modifier = /Mac|iPhone|iPad/i.test(navigator.userAgentData?.platform || navigator.platform || '') ? '⌘' : 'Ctrl';
    function updateSplit() {
        const ids = splitIds(); split.disabled = !ids.length || ids.some(id => model.pair(state.record, id).some(isBusy));
        split.title = t('Split', '剪开') + ` ${modifier}+B`;
    }
    function updateZoom() {
        const zoomBar = zoomHost.querySelector('.fv-ed-track-zoom'), slider = zoomBar?.querySelector('input');
        if (slider) {
            const controls = zoomBar.querySelectorAll('button');
            controls[0].disabled = state.zoom <= Number(slider.min); controls[1].disabled = state.zoom >= Number(slider.max);
            controls[2].disabled = !model.duration(state.record) && state.selection?.kind !== 'range';
            controls[0].title = '';
            controls[1].title = '';
            controls[2].title = t('Fit all', '看全部');
        }
    }
    function updateTransport() {
        const clips = selectedContent(), busy = clips.some(isBusy);
        toolUndo.disabled = !state.canUndo; toolRedo.disabled = !state.canRedo;
        toolUndo.title = t('Undo', '撤销') + ` ${modifier}+Z`;
        toolRedo.title = t('Redo', '重做') + ` ${modifier}+Shift+Z`;
        updateSplit();
        remove.disabled = !deletionStatus(state.record, state.selection).ok;
        remove.title = t('Delete Delete', '删除 Delete');
        const gaps = removableGaps(state.record, state.selection); closeGap.disabled = !gaps.length || !removeGaps(state.record, state.selection).removed;
        closeGap.title = t('Remove gaps Delete', '移除空隙 Delete');
        const multiple = isMultiple(state.record, clips.map(clip => clip.id));
        previous.disabled = busy || multiple || !clips.some(clip => clip.versions.length);
        previous.title = t('Previous version', '上一版');
        updateZoom();
    }
    updateTransport();
    const stopSources = store.subscribeSources(() => { player.refresh(); tracks?.refresh?.(); pictureTools.update(); updateTop(); });
    let capabilitySize = `${state.record.width}x${state.record.height}`;
    const unsubscribe = state.subscribe(event => {
        if (event.type === 'record') {
            const size = `${state.record.width}x${state.record.height}`;
            if (!initializing && size !== capabilitySize) {
                capabilitySize = size;
                capability = store.capabilities(state.record, value => {
                    capability = value; pictureTools.update(); tracks?.refresh?.(); genbar?.update(null);
                });
            }
            if (initialSelection && state.selection === initialSelection && state.record.tracks.some(track => track.clips.some(clip => clip.source))) state.select(null);
            const next = content(state.record);
            if (!suppressSave && !initializing && next !== savedContent && exportStatus?.state === 'done') { exportStatus = null; exportButton.textContent = t('Export', '导出'); }
            if (!suppressSave && !initializing && next !== savedContent) { savedContent = next; queue.enqueue(state.record); }
            completeSources().catch(value => error(value, null, undefined, 'sources')); recoverJobs();
        }
        if (event.type === 'selection') { if (state.selection !== initialSelection) initialSelection = null; }
        if (event.type === 'record' || event.type === 'selection') { updateTop(); updateTransport(); pictureTools.update(); }
        else if (event.type === 'playhead') updateSplit();
        else if (event.type === 'zoom') updateZoom();
    });
    function dismissPopup(event) {
        if (tracks?.menu.dismiss(event)) return true;
        if (tracks?.dismissPopup(event)) return true;
        if (pictureEdit.keyboard(event)) return true;
        if (adjust.dismiss(event)) return true;
        if (toolbarMore.dismiss(event)) return true;
        if (menu.hidden) return false;
        event.preventDefault(); event.stopPropagation();
        if (!menu.hidden) { menu.hidden = true; title.setAttribute('aria-expanded', 'false'); title.focus(); }
        return true;
    }
    const selectionActions = createSelectionActions({state, t, actions});
    actions.editSelection = key => selectionActions.run(key);
    actions.setSnapping = enabled => tracks.setSnapping(enabled);
    actions.seekPlayhead = frame => tracks.seekPlayhead(frame);
    function keyboard(event) {
        if (event.isComposing || event.keyCode === 229) return;
        if (event.target.closest('.fv-ed-library')) return;
        if (!initializing && textTools.keyboard(event)) return;
        if (pictureEdit.keyboard(event)) return;
        if (event.key.toLowerCase() === 'escape' && dismissPopup(event)) return;
        if (event.target.closest('.fv-ed-adjust') && event.key.toLowerCase() !== 'escape'
            && !((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z')) return;
        // Track gestures have their own keyboard handler (including Escape rollback).
        if (event.target.closest('.fv-ed-tracks')) return;
        event.stopPropagation();
        const typing = isTyping(event.target) && !(event.target.type === 'range' && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z');
        const key = event.key.toLowerCase(), mod = event.ctrlKey || event.metaKey;
        if (key === 'escape') { event.preventDefault(); state.select(null, {history: true}); menu.hidden = true; libraryHost.dataset.open = 'false'; return; }
        if (typing || initializing) return;
        if (selectionActions.keyboard(event)) return;
        if (!mod && !event.altKey && (key === 'arrowup' || key === 'arrowdown' || (key === 'z' && event.shiftKey))) { tracks?.keyboard(event); return; }
        if (handleSpeedKey(event)) return;
        if (key === ' ') { event.preventDefault(); player.toggle(); }
        else if (key === 'arrowleft' || key === 'arrowright') { event.preventDefault(); state.seek(Math.max(0, state.playhead + (key === 'arrowright' ? 1 : -1))); }
        else if (mod && key === 'z') { event.preventDefault(); travel(!event.shiftKey); }
    }
    listen('keydown', keyboard); listen('keyup', event => event.stopPropagation());
    listen('cancel', event => {
        if (event.target !== element) return;
        event.preventDefault();
        if (topDialog() === element && !dismissPopup(event)) state.select(null, {history: true});
    });
    const stopKeys = isolateDialogKeys(element, (event, inside) => {
        if (guide?.handleKey(event)) return;
        if (event.type === 'keyup') { transportControls.keyboard(event); return; }
        if (event.type !== 'keydown') return;
        if (event.isComposing || event.keyCode === 229) return;
        if (event.key.toLowerCase() === 'escape') {
            if (dismissPopup(event)) return;
            if (tracks?.cancelGesture()) { event.preventDefault(); return; }
            if (document.fullscreenElement) { event.preventDefault(); document.exitFullscreen().catch(() => {}); return; }
        }
        if (inside && event.target.closest?.('.fv-ed-refs')) {
            // The reference pictures' own buttons and menu keep their keys: no timeline, audio or transport shortcut.
            for (let target = event.target; target && target !== element; target = target.parentNode) target.onkeydown?.(event);
            return;
        }
        const inLibrary = inside && !!event.target.closest('.fv-ed-library');
        if ((!inLibrary || ['Enter', ' '].includes(event.key)) && handleAudioShortcut(event, state, {t, error})) return;
        if (!inside && transportControls.keyboard(event, {ready: !initializing})) return;
        if (inside) {
            // Preserve the input/library controls' own handlers before shortcuts.
            for (let target = event.target; target && target !== element; target = target.parentNode) {
                if (target.onkeydown?.(event) === false) event.preventDefault();
                if (event.defaultPrevented) return;
            }
            if (inLibrary) return;
            if (!event.target.closest('.fv-ed-adjust,.fv-ed-library') && transportControls.keyboard(event, {ready: !initializing})) return;
            if (event.target.closest('.fv-ed-tracks')) { if (!textTools.keyboard(event)) tracks?.keyboard(event); return; }
        }
        keyboard(event);
    });
    const releaseStamp = () => {
        pointerEditing = false;
        if (deferredStamp) {
            const value = deferredStamp; deferredStamp = null;
            suppressSave = true; try { state.adopt(value); } finally { suppressSave = false; }
            if (held.length) rememberView();
        }
    };
    listen('dragover', event => { if ([...event.dataTransfer.types].includes('Files')) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; } });
    listen('drop', event => { if (event.dataTransfer.files.length) { event.preventDefault(); event.stopPropagation(); library?.importFiles([...event.dataTransfer.files]); } });
    listen('pointerdown', event => {
        if (!menu.hidden && !menu.contains(event.target) && event.target !== title) menu.hidden = true;
    });
    const leaving = () => { player.pause(); rememberView(); if (queue.dirty) queue.flush(); };
    window.addEventListener('pagehide', leaving);
    // A workflow without FreeVideo nodes came to the front: the editing view makes way (unsaved edits stay open).
    const collapse = () => { void handle.close(); };
    window.addEventListener('freevideo-collapse', collapse);
    const handle = {element, state, actions, store, adjust, audio, notify,
        async switchView(view) {
            if (closing || disposed || view === 'editor') return;
            if (await handle.close() !== false) await onView(view);
        },
        async close() {
            if (closePromise) return closePromise;
            textTools.flush();
            guide?.suspend();
            closing = true; element.inert = true; player.pause();
            closePromise = (async () => {
                await handle.ready;
                if (generationDone) await generationDone;
                releaseStamp(); await queue.flush();
                if (queue.dirty && !queue.locallyRetained && !discardOnSwitch) {
                    discardOnSwitch = true; closing = false; element.inert = false; closePromise = null;
                    saveWarning.textContent = t('Not saved yet. Switching again will lose recent changes.', '还没保存好，再切换会丢掉最近的改动');
                    saveWarning.hidden = false; return false;
                }
                if (!queue.dirty && emptyEdit()) {
                    try { await discardProject({automatic: true}); }
                    catch (value) {
                        notify(deleteEditText(t)[value?.reason] || deleteEditText(t).delete_failed, EDITOR_ISSUE, failureKind('delete', {reason: value?.reason}));
                        closing = false; element.inert = false; closePromise = null; return false;
                    }
                }
                await store.activateProject(null).catch(() => {});
                rememberView(); disposed = true; guide?.dispose(); transportControls.dispose(); toolbarMore.dispose(); player.dispose();
                textTools.dispose(); adjust.dispose(); notes.dispose(); pictureEdit.dispose();
                audio.dispose(); headerFit.dispose();
                listeners.splice(0).forEach(remove => remove());
                for (const node of [element, ...element.querySelectorAll('*')]) for (const name of ['onclick', 'ondblclick', 'oninput', 'onchange', 'onkeydown', 'ondragstart']) node[name] = null;
                unsubscribe(); stopSources(); library?.dispose(); genbar.dispose(); tracks?.dispose(); stopExport?.();
                progressConnection?.stop(); progressObserver?.disconnect(); runProgress.clear(); failures.clear();
                stopKeys();
                resumeCanvasPointer();
                window.removeEventListener('pagehide', leaving); window.removeEventListener('freevideo-collapse', collapse); clearTimeout(noticeTimer);
                for (const stop of jobWatchers.values()) stop(); jobWatchers.clear();
                queue.dispose({discard: discardOnSwitch}); store.dispose(); if (element.open) element.close(); element.replaceChildren(); element.remove(); if (opened === handle) opened = null;
                if (returnFocus?.isConnected) returnFocus.focus({preventScroll: true});
            })();
            return closePromise;
        },
    };
    library = createEditorLibrary(libraryHost, {store, t, actions, state});
    opened = handle; document.body.append(element); element.showModal(); viewChanged('editor'); navigation.querySelector('[data-view=editor]').focus();
    handle.ready = (async () => {
        const progressComponents = given && Object.hasOwn(given, 'generationStandIn') && Object.keys(given).length === 1 ? null : given;
        const componentsReady = Promise.allSettled([import('./editor_tracks.js'), progressComponents || loadEditorComponents()]);
        await editorStyles; if (closing || disposed) return;
        let record = null, key = null;
        try {
            await store.recoverOutbox(); if (closing || disposed) return;
            if (from?.work) {
                const work = from.work;
                const id = work.id || work.video?.replace(/^FreeVideo\//, '').replace(/\/video\.mp4$/, '');
                record = await store.byWork(id);
                if (!record) {
                    const key = `work:${id}`; await store.getSources([key]); const metadata = store.sources.get(key), g = work.geometry || {};
                    record = model.emptyProject({width: even(metadata?.width || g.width || panel.width || 1344), height: even(metadata?.height || g.height || panel.height || 768),
                        settings: panel.settings || null, name: t('Untitled edit', '未命名剪辑')});
                    record = model.insertSource(record, {source: {kind: 'work', id}, frames: metadata?.frames || g.frames || Math.round((g.seconds || 15) * 24),
                        hasVideo: true, hasAudio: metadata?.has_audio ?? work.has_audio !== false, text: metadata?.text, at: 0});
                    if (!metadata) store.cache({[key]: {video: mediaURL(api, work.video), audio: mediaURL(api, work.video), frames: g.frames,
                        has_video: true, has_audio: true, path: work.video, missing: false, thumbnail: '/freevideo/library/thumbnail?' + new URLSearchParams({id})}});
                }
            } else {
                let id; try { id = optionalText(localStorage.getItem('fv-editor-last')); } catch {}
                let draft; try { draft = globalThis.sessionStorage?.getItem('fv-editor-draft'); } catch { draft = lastDraft; }
                if (draft?.startsWith('draft:')) key = draft;
                else if (id) { record = await store.load(id); if (record) key = id; }
            }
        } catch (value) { error(value, null, undefined, 'open-load'); }
        if (closing || disposed) return;
        try {
            await replaceProject(record || state.record, key);
            if (from?.work && !state.record.id) queue.enqueue(state.record);
        } catch (value) { error(value, null, undefined, 'open-session'); }
        if (closing || disposed) return;
        initializing = false; savedContent = content(state.record); player.refresh();
        loaded = true; settleGenbar();
        updateTop(); updateTransport();
        const [trackResult, componentResult] = await componentsReady; if (closing || disposed) return;
        // Optional progress components may fail without taking the tracks with them.
        if (componentResult.status === 'rejected') error(componentResult.reason, null, undefined, 'components');
        if (trackResult.status === 'rejected') throw trackResult.reason;
        const module = trackResult.value, components = componentResult.status === 'fulfilled' ? componentResult.value : {};
        blockStates = components.blocks?.blockStates || null;
        let blockProgress = (id, clipElement) => basicBlockProgress(id, clipElement, t);
        if (components.blocks?.createBlockProgress) blockProgress = (id, clipElement) => {
            const component = components.blocks.createBlockProgress({t, showPercent: clipElement.dataset.track === 'v1'});
            let run = null;
            return {element: component.element, update(value) {
                if (value.run && value.run !== run) { component.update({state: 'waiting', fraction: 0}); run = value.run; }
                component.element.dataset.run = run;
                component.update(value);
            }, dispose: () => component.destroy()};
        };
        tracks = module.createTracks(tracksHost, {state, sources: store.sources, t, actions, blockProgress});
        listeners.push(notes.attach(tracks.element));
        transportControls.attachTracks(tracks, module.snapFrame);
        if (api.addEventListener && api.removeEventListener) {
            progressConnection = createProgressConnection(api, receiveProgress); progressConnection.start();
            const executionError = event => {
                if (!failures.capture(event.detail)) return;
                tracks?.refresh?.(); genbar?.update(mostRecentJob ? barJob(jobStatuses.get(mostRecentJob)) : null);
            };
            api.addEventListener('execution_error', executionError);
            listeners.push(() => api.removeEventListener('execution_error', executionError));
        }
        if (panel.progressElement && typeof MutationObserver !== 'undefined') {
            progressObserver = new MutationObserver(() => {
                for (const job of jobStatuses.values()) paintJob(job);
                if (mostRecentJob && jobStatuses.has(mostRecentJob)) genbar?.update(barJob(jobStatuses.get(mostRecentJob)));
            });
            progressObserver.observe(panel.progressElement, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['aria-valuenow', 'hidden']});
        }
        const bar = tracks.element.querySelector('.fv-ed-track-zoom');
        if (bar) {
            zoomHost.append(bar);
            for (const control of bar.querySelectorAll('button')) control.title = control.getAttribute('aria-label') || control.textContent;
        }
        for (const [id, value] of progress) tracks.setProgress(id, value);
        tracks.setScrollLeft(editorViews.get(viewKey)?.scrollLeft);
        if (from?.work && from.extend) {
            const id = from.work.id || from.work.video?.replace(/^FreeVideo\//, '').replace(/\/video\.mp4$/, '');
            const epoch = projectEpoch;
            // Selects the work and, when nothing follows it yet, adds one minimum draft. Runs once; when the
            // generation probe failed this time it runs after a later probe succeeds.
            const enter = (select = true) => {
                if (closing || disposed || epoch !== projectEpoch) return true;
                const clip = state.record.tracks[0].clips.findLast(clip => clip.source?.kind === 'work' && clip.source.id === id);
                if (!clip) return true;
                // Coming back to a work that already has parts after it selects the waiting draft instead of adding another.
                const edge = clip.start + clip.frames;
                const after = state.record.tracks[0].clips.filter(other => other.id !== clip.id && other.start >= edge);
                const waiting = after.find(other => other.pending === 'draft');
                if (select) state.select({kind: 'clip', ids: model.pair(state.record, (waiting || clip).id).map(member => member.id)});
                if (after.length || !clip.extend || !clip.source || store.sources.get(sourceKey(clip.source))?.missing) return true;
                if (!store.generationAvailable) return false;
                const spare = Math.min(...model.pair(state.record, clip.id).map(member => member.length - member.in - member.frames));
                tracks.extendClip(clip.id, spare + model.defaultContinueSeconds(capability.maxFollowSeconds,
                    {short: store.continueSeconds != null && store.continueSeconds < model.RULES.fullSeconds}) * model.FPS, {silent: true});
                return true;
            };
            await store.generationReady();
            if (!enter() && !closing && !disposed) extendEntry = () => { if (enter(false) || closing || disposed) extendEntry = null; };
        }
    })().catch(value => error(value, null, undefined, 'init')).finally(() => {
        // Every exit (including close and partial failure) settles readiness.
        // After a completed start the saved baseline is already set; edits made since must stay unsaved.
        if (initializing) { initializing = false; savedContent = content(state.record); }
        title.disabled = false; toolbar.inert = false; loaded = true; settleGenbar();
        tracksHost.inert = !tracks;
        for (const host of [tracksHost, toolbar]) host.classList.remove('fv-ed-pending');
        if (!closing && !disposed) { player.refresh(); updateTop(); updateTransport(); }
    });
    if (typeof MutationObserver !== 'undefined') guide = createFirstRunGuide({dialog: element, api,
        cn: String(language()).toLowerCase().startsWith('zh'), group: 'editor',
        onShowcaseTry: async () => { if (await handle.close() !== false) await onView('autopilot', {initialScene: SHOWCASE_SCENE}); },
        isBusy: () => initializing || !tracks || genHost.inert || pointerEditing || jobActive() || ['running', 'queued'].includes(exportStatus?.state),
        targets: {'editor-trim': () => tracks?.guideTarget('trim'), 'editor-empty': () => tracks?.guideTarget('empty')}});
    element.freevideoSwitchView = view => handle.switchView(view);
    updateTop(); return handle;
}
