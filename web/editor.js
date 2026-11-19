import './has_mirror.js';
import {createLanAccess} from './lan_access.js';
import {createEditorText} from './editor_text.js';
import {isNetworkTypeError, connectionLostText} from './browser_extension.js';
import {createToolbarMore} from './editor_toolbar_more.js';
import {isText} from './editor_text_model.js';
import {viewSwitch, viewChanged} from './view_navigation.js';
import {wordmark} from './wordmark.js';
import {openSetup} from './setup.js';
import {feedbackButton, guideButton} from './feedback.js';
import {issueButton} from './issue_button.js';
import * as model from './editor_model.js';
import {discardEmptyEdit, hiddenEmptyEdit, deleteEditText} from './editor_lifecycle.js';
import {createEditorState} from './editor_state.js';
import {createEditorStore, sourceKey, mediaURL, isReportableSaveFailure} from './editor_store.js';
import {createPicturePoster} from './editor_poster.js';
import {generationSize, continuedSize} from './editor_generation.js';
import {uploadMedia} from './media_upload.js';
import {checkOriginal} from './audio_original.js';
import {createEditorLibrary} from './editor_library.js';
import {createEditorPlayer} from './editor_player.js';
import {createEditorTransport, registerEditorShortcuts} from './editor_transport.js';
import {createAdjustPanel} from './editor_adjust.js';
import {createAdjustPicture} from './editor_adjust_picture.js';
import {pictureSession} from './editor_picture_session.js';
import {createPictureTools} from './editor_frame.js';
import {openExportDialog} from './editor_export_dialog.js';
import {createAudioIntegration, createSpeechSpans, handleAudioShortcut, AUDIO_SHORTCUTS} from './editor_audio.js';
import {createStemsIntegration} from './editor_stems.js';
import {createSpeedComponent, readSpeedAudioSamples, handleSpeedKey, speedShortcuts} from './editor_speed.js';
import {createEditorGenbar, editorBlockStates, blockMessages} from './editor_genbar.js';
import {createEditorMusic} from './editor_music.js';
import {createEditorRecord, RECORD_ICON} from './editor_record.js';
import {createEditorVoice} from './editor_voice.js';
import {createVoiceStore, voiceFeatures} from './editor_voice_store.js';
import {createVoiceInstall} from './editor_voice_install.js';
import {createEditorNarration} from './editor_narration.js';
import {MAX_LINE_TEXT as NARRATION_TEXT_LIMIT} from './editor_narration_model.js';
import {voiceTarget} from './editor_voice_model.js';
import {MAX_MUSIC_TEXT as MUSIC_TEXT_LIMIT} from './editor_music_model.js';
import {createProgressConnection} from './progress_connection.js';
import {createSelectionActions, deletionStatus, isMultiple, isTyping, playheadIds, removableGaps, removeGaps} from './editor_select.js';
import {isolateDialogKeys, routeDialogKey, topDialog} from './dialog_keys.js';
import {createBlockNotes, describedTrack} from './editor_notes.js';
import {createFirstRunGuide} from './first_run_guide.js';
import {SHOWCASE_SCENE} from './entry_showcase.js';
import {createEditorFailures} from './editor_failure.js';
import {fitEditorHeader} from './editor_header_fit.js';
import {optionalText, clonePrompt, allowEmptyPrompt} from './optional_inputs.js';
import {outputNotWritable, modelTableDamaged} from './error_panel.js';
import {repairAction} from './repair_hint.js';
import {expandCast} from './cast_mention.js';
import {problem as timedProblem, timedText} from './timed_prompt.js';
import {tip, modKey} from './editor_tips.js';
import {jobs} from './task_store.js';
import {openingChoice} from './editor_opening.js';
import {CN} from './topbar_state.js';
import {setDisabled} from './dom_quiet.js';
const editorStyles = Promise.all(['result_actions.css', 'editor.css', 'editor_expand.css', 'editor_tracks.css', 'editor_frame.css', 'editor_adjust.css', 'editor_adjust_picture.css', 'editor_preview_picture.css', 'editor_select.css', 'editor_menu.css', 'editor_text.css', 'editor_notes.css', 'editor_transport.css', 'glass/editor.css', 'editor_touch.css'].map(name => {
    const style = document.createElement('link'); style.rel = 'stylesheet'; style.href = new URL(name, import.meta.url).href;
    const ready = 'sheet' in style ? new Promise(resolve => { style.onload = style.onerror = resolve; }) : Promise.resolve();
    document.head.append(style); return ready;
}));
// The timeline module is fetched with this one, so opening the editor never waits for it (no late rows).
const tracksModule = import('./editor_tracks.js');
tracksModule.catch(() => {});
// Waits for a promise, but no longer than `ms`: what has not settled by then is left to finish on its own.
const within = (promise, ms) => Promise.race([Promise.resolve(promise).catch(() => {}), new Promise(resolve => setTimeout(resolve, ms))]);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
// A half of a surrogate pair left by a cut or a paste is dropped; the description limit counts characters, not code units.
export const wellFormed = text => typeof text === 'string' ? text.replace(/[\ud800-\udbff][\udc00-\udfff]|[\ud800-\udfff]/g, pair => pair.length === 2 ? pair : '') : text;
export const characters = text => Array.from(text).length;
export const textLongText = (t, limit) => t('The description is too long. Shorten it to %d characters or fewer.', '描述过长，请缩短到 %d 字以内。').replace('%d', limit);
// The engine's limits on continuing parts, refused before a generation starts; the engine says the same.
export const PARTS_LIMIT_CODES = ['chain_too_long', 'chain_part_too_short', 'chain_short_parts', 'chain_source_full'];
export function partsLimitText(code, t = en => en, limit = null) {
    const {followSeconds: least, fullSeconds: full, maxParts, shortMaxParts} = model.RULES;
    if (code === 'chain_source_full') return t('This video already has the maximum of ' + maxParts + '\u00a0parts and cannot be extended.',
        '这个视频已达 ' + maxParts + '\u00a0段上限，无法再接着生成。');
    if (code === 'chain_part_too_short') return t(`A part followed by another part must be at least ${least} seconds. Make it longer.`, `后面还接着其他段时，这一段至少 ${least} 秒，请加长这一段。`);
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
        chain_part_too_short: t(`A part followed by another part must be at least ${model.RULES.followSeconds} seconds.`, `后面还接着其他段时，这一段至少 ${model.RULES.followSeconds} 秒。`),
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
    if (modelTableDamaged(whole))
        return t('Some of the model files are missing or damaged. ', '部分模型文件缺失或已损坏。') + t(...repairAction());
    if (['model_missing', 'missing_model', 'model_not_found'].includes(code) || /(?:model|checkpoint|模型).*(?:not found|missing|不存在|缺失)/i.test(raw))
        return t(`Loading the model failed: ${raw || code}`, `加载模型时出错：${raw || code}`);
    if (['timeout', 'timed_out', 'generation_timeout'].includes(code) || /timed?\s*out|timeout|超时/i.test(raw))
        return t(`${stage} timed out. Try again.`, `${stage}时等待超时，请再试一次。`);
    const received = raw || (/^[a-z_]+$/.test(code) ? '' : code);
    if (received) return t(`${generating} failed: ${received}`, `${generating}时出错：${received}`);
    // No readable text: say what the code names, and where the details are.
    const named = /^[a-z_]+$/.test(code) ? code : '';
    return t(`${generating} failed. Try again; if it keeps failing, download the report and report the problem.${named ? ` Error code: ${named}` : ''}`,
        `${generating}时出错。请重试；仍然失败请下载报告并提交问题。${named ? `错误代码：${named}` : ''}`);
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
// Names the failed delete for the report: a reason given by the server or the page as it is, else the HTTP status, a lost connection or the error's own code or name, prefixed with the step when it was not the delete request itself.
function deleteReason(value, step = 'delete') {
    if (value?.reason) return value.reason;
    const code = value?.status ? 'http_' + value.status : isNetworkTypeError(value) ? 'network' : value?.code || value?.name;
    return code && step !== 'delete' ? step + '_' + code : code;
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
// The last saved edit as it was when the view closed, with its media's details: the next opening draws it at once,
// then the server's copy replaces it (usually the same, so nothing moves). One edit is kept across a reload.
const recordCache = new Map(), RECORD_KEY = 'fv-editor-record', RECORD_LIMIT = 600000;
function cachedRecord(id) {
    if (!id) return null;
    if (recordCache.has(id)) return recordCache.get(id);
    try { const value = JSON.parse(localStorage.getItem(RECORD_KEY) || 'null'); if (value?.id === id && value.record?.id === id) return value; } catch {}
    return null;
}
function forgetCachedRecord(id) {
    recordCache.delete(id);
    try { if (JSON.parse(localStorage.getItem(RECORD_KEY) || 'null')?.id === id) localStorage.removeItem(RECORD_KEY); } catch {}
}

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
    // A kept view (hidden when the user went to another view) comes back as it was: no rebuild, no reload. Opening a
    // work, another node's panel or a new creation panel builds a fresh one instead.
    if (opened?.kept) {
        if (from?.work || panel.onProjectLoaded || opened.panel.node !== panel.node || opened.panel.progressElement !== panel.progressElement) opened.dispose();
        else return opened.reopen();
    }
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
    // The same language as the top bar and the other views, read once when the page opened (the app later drops the
    // address's query, so reading it again when the editor opened fell back to the browser's language).
    const t = (en, zh) => CN ? zh : en;
    const returnFocus = document.activeElement;
    const even = value => Math.max(2, Math.floor(value / 2) * 2);
    // A new edit opens with an empty second sound track, where music is made.
    const initial = () => model.addAudioTrack(model.emptyProject({width: even(panel.width || 1344), height: even(panel.height || 768), settings: panel.settings || null,
        name: t('Untitled edit', '未命名剪辑')}));
    // Reopening the last saved edit (not a work being opened, not a draft of this tab) starts from its cached copy.
    let fromCache = null;
    if (!from) try {
        const lastId = optionalText(localStorage.getItem('fv-editor-last')), draftKey = globalThis.sessionStorage?.getItem('fv-editor-draft');
        if (lastId && !draftKey?.startsWith('draft:')) fromCache = cachedRecord(lastId);
    } catch { fromCache = null; }
    let state;
    try { state = createEditorState(fromCache ? fromCache.record : initial()); } catch { fromCache = null; state = createEditorState(initial()); }
    let resumeCanvasPointer = suspendCanvasPointer(app?.canvas);
    const element = el('dialog', 'fv-studio fv-editor'); element.setAttribute('aria-label', t('Edit', '剪辑视图')); element.tabIndex = -1;
    // Until the edit to open is decided (no copy kept in this browser: the saved list or the work is still being read), the
    // blank stand-in is not presented as a new edit: no name and no empty-edit hint, only the view's frame.
    if (!fromCache) element.dataset.opening = 'true';
    const listeners = [];
    const listen = (type, handler) => { element.addEventListener(type, handler); listeners.push(() => element.removeEventListener(type, handler)); };
    // Keep editor mouse events from ComfyUI's document bubble handlers (ghost-node placement).
    for (const type of ['pointermove', 'mousemove', 'pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'dblclick', 'wheel', 'contextmenu'])
        listen(type, event => event.stopPropagation());
    const top = el('header', 'fv-header fv-ed-top'), libraryHost = el('aside', 'fv-ed-library'), preview = el('section', 'fv-ed-preview'),
        transport = el('div', 'fv-ed-transport'), genHost = el('div', 'fv-ed-genbar'), tracksHost = el('div', 'fv-ed-tracks'), zoomHost = el('div', 'fv-ed-zoom');
    const adjustHost = el('aside', 'fv-ed-adjust-host');
    tracksHost.dataset.fvGuide = 'editor-timeline'; tracksHost.dataset.tourId = 'editor-timeline';
    const button = (label, action, cls = '') => { const value = el('button', cls, label); value.type = 'button'; value.onclick = action; return value; };
    const navigation = viewSwitch('editor', () => handle.switchView('studio'), () => handle.switchView('nodes'), () => {}, () => handle.switchView('autopilot'), () => handle.switchView('image'));
    for (const control of navigation.querySelectorAll('button')) control.removeAttribute('title');
    const brand = el('div', 'fv-brand');
    const headerActions = el('div', 'fv-header-actions');
    const title = button('', () => menu.hidden ? showMenu() : menu.hidden = true, 'fv-ed-name'); title.setAttribute('aria-haspopup', 'menu');
    const toggleLibrary = () => { libraryHost.dataset.open = libraryHost.dataset.open !== 'true'; for (const control of [drawer, libraryRail]) control.setAttribute('aria-expanded', libraryHost.dataset.open); };
    const drawer = button(t('Media', '素材'), toggleLibrary, 'fv-ed-drawer-toggle');
    drawer.setAttribute('aria-expanded', 'false');
    // Between 681 and 1279 px the library and the inspector fold into two equal rails, one each side, so the picture
    // stays centred in the window; the library opens over the picture's left side, the inspector over its right.
    const railGlyph = path => { const glyph = icon(path); glyph.classList.add('fv-ed-rail-glyph'); return glyph; };
    const libraryRail = button('', toggleLibrary, 'fv-ed-rail-button'); libraryRail.setAttribute('aria-expanded', 'false');
    libraryRail.append(railGlyph('M2 2h5v5H2zM9 2h5v5H9zM2 9h5v5H2zM9 9h5v5H9z'), el('span', 'fv-ed-rail-label', t('Media', '素材')));
    tip(libraryRail, {name: t('Media', '素材'), use: t('Open the library to place clips', '打开素材库，把素材放进时间线')});
    tip(drawer, {name: t('Media', '素材'), use: t('Open the library to place clips', '打开素材库，把素材放进时间线')});
    const leftRail = el('nav', 'fv-ed-rail fv-ed-rail-left'), rightRail = el('nav', 'fv-ed-rail fv-ed-rail-right');
    leftRail.append(libraryRail);
    const exportBox = el('div', 'fv-ed-export-box'), exportHint = el('span', 'fv-ed-export-hint'), exportButton = button(t('Export', '导出'), async () => {
        try {
            if (explainExport()) return;
            if (exportStatus?.state === 'done' && exportMatches()) { await actions.export(); return; }
            const options = await openExportDialog({parent: element, state, sources: store.sources, t, onError: error, fps: exportFps});
            if (options && !disposed) await actions.export(options);
        } catch (value) { error(value, 'export'); }
    }, 'fv-primary fv-ed-export-main');
    const exportCancel = button('', () => store.cancelExport(exportStatus.id).catch(value => error(value, null, undefined, 'cancel-export'))); exportCancel.hidden = true; exportCancel.append(icon('m4 4 8 8M12 4l-8 8')); exportCancel.setAttribute('aria-label', t('Cancel', '取消'));
    exportBox.append(exportHint, exportButton, exportCancel); brand.append(wordmark());
    const phoneAccess = createLanAccess(t, api);
    listeners.push(phoneAccess.dispose);
    const settingsButton = button(t('Settings', '设置'), openSetup, 'fv-quiet');
    tip(settingsButton, {name: t('Settings', '设置'), use: t('Folders, downloads, language and more', '文件夹、下载、语言等设置')});
    // The header is the same in every view: the logo, the five views and the shared entries. The edit's own name,
    // whether it is saved, and Export sit in the project row over the picture.
    headerActions.append(phoneAccess.element, guideButton(t), feedbackButton(t), settingsButton);
    top.append(brand, navigation, headerActions);
    const projectRow = el('div', 'fv-ed-project-row');
    const saveState = button('', () => { if (queue.dirty) void retrySave(); }, 'fv-ed-save-state'); saveState.setAttribute('role', 'status');
    projectRow.append(title, saveState, exportBox);
    // The header fit only keeps the shared entries readable now; the name has its own row.
    const headerName = el('button'); headerName.append(el('span'));
    const headerFit = fitEditorHeader(top, brand, headerName, navigation, headerActions);
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
    // Changes not saved yet: said under the name, with the way to save them again (switching once more still leaves).
    const saveWarning = el('div', 'fv-ed-save-warning-card'); saveWarning.hidden = true; saveWarning.setAttribute('role', 'alert');
    const saveWarningText = el('span', 'fv-ed-save-warning'), saveAgain = button(t('Save again', '重新保存'), () => void retrySave());
    // A switch never leaves unsaved edits behind by itself: staying keeps trying, and only this button leaves without them.
    const leaveUnsaved = button(t('Leave without saving', '放弃改动并离开'), () => {
        const view = leaveTo; leaveTo = null; discardOnSwitch = true;
        void (view ? handle.switchView(view) : handle.close());
    }, 'fv-ed-save-leave');
    saveWarning.append(saveWarningText, saveAgain, leaveUnsaved);
    tip(saveAgain, {name: t('Save again', '重新保存'), use: t('Try saving the changes now', '现在再试一次保存改动')});
    tip(leaveUnsaved, {name: t('Leave without saving', '放弃改动并离开'), use: t('Go on; the latest changes are lost', '离开这里，最近的改动会丢失')});
    const notices = el('div', 'fv-ed-notice-stack');
    // Where the server cannot generate, the bar's row says so instead of standing empty.
    const goneLine = el('div', 'fv-ed-genbar-gone', t('Generating is not available here right now. Restart FreeVideo to try again.', '这台电脑现在不能生成。重新启动 FreeVideo 后再试。')); goneLine.hidden = true; goneLine.setAttribute('role', 'status');
    transport.append(drawer);
    projectRow.append(menu, saveWarning);
    element.append(top, projectRow, leftRail, rightRail, libraryHost, preview, transport, genHost, goneLine, tracksHost, zoomHost, notices);
    element.append(adjustHost);
    // The floating task window keeps clear of the timeline, the bar, the inspector and the library.
    for (const host of [libraryHost, genHost, tracksHost, adjustHost]) host.classList.add('sgf-avoid');
    // Until the timeline module has drawn, its rows hold their final places.
    const skeleton = el('div', 'fv-ed-tracks-skeleton'); skeleton.setAttribute('aria-hidden', 'true');
    for (const [y, h] of [[0, 24], [24, 64], [94, 40], [140, 40]]) { const row = el('i'); row.style.top = `${y}px`; row.style.height = `${h}px`; skeleton.append(row); }
    { const heads = el('b'); heads.style.top = '0'; heads.style.bottom = '0'; skeleton.append(heads); }
    tracksHost.append(skeleton);
    tracksHost.inert = genHost.inert = true; title.disabled = true;
    // CSS delays the pending indication; fast opens stay at full opacity.
    tracksHost.classList.add('fv-ed-pending'); genHost.classList.add('fv-ed-pending');
    let discardOnSwitch = false, leaveTo = null, initialSelection = null, offerInitial = null, extendEntry = null;
    let viewKey = null, viewReady = false;
    let library = null, closing = false, closePromise = null, exportSnapshot = null, lastSeed = null, projectEpoch = 0;
    let cleanupBar = null, stems = null, disposed = false, suppressSave = false, savedContent = '', initializing = true, tracks = null, genbar = null, adjustPanel = null, exportStatus = null, pointerEditing = false, deferredStamp = null;
    // Selects the clip and, when nothing follows it yet, adds one minimum draft. Returns false while the generation probe has not said yes.
    function enterClip(match, epoch, {select = true, settled = () => {}, fresh = false} = {}) {
        if (closing || disposed || epoch !== projectEpoch) return true;
        const clip = state.record.tracks[0].clips.findLast(match);
        if (!clip) return true;
        // Coming back to a clip that already has parts after it selects the waiting draft instead of adding another.
        // A clip just placed before older content (`fresh`) owns only the parts that continue it, not what it pushed back.
        const edge = clip.start + clip.frames;
        const after = state.record.tracks[0].clips.filter(other => other.id !== clip.id && other.start >= edge && (!fresh || other.follows === clip.id));
        const waiting = after.find(other => other.pending === 'draft');
        if (select) state.select({kind: 'clip', ids: model.pair(state.record, (waiting || clip).id).map(member => member.id)});
        if (after.length || !clip.extend || !clip.source || store.sources.get(sourceKey(clip.source))?.missing) { settled(); return true; }
        if (!store.generationAvailable) return false;
        const spare = Math.min(...model.pair(state.record, clip.id).map(member => member.length - member.in - member.frames));
        tracks.extendClip(clip.id, spare + model.defaultContinueSeconds(capability.maxFollowSeconds,
            {short: store.continueSeconds != null && store.continueSeconds < model.RULES.fullSeconds}) * model.FPS, {silent: true});
        settled();
        return true;
    }
    let capability = {maxSeconds: 15, maxFollowSeconds: 15, maxRequestFrames: null, estimate: () => null}, generationBusy = false, switching = false, generationDone = null, finishGeneration = null;
    let stopExport = null, mostRecentJob = null, exportPreparing = false, guide = null;
    const jobWatchers = new Map(), progress = new Map(), jobStatuses = new Map(), runProgress = new Map(), startPlayheads = new Map(), soundJobs = new Set(), shownJobs = new Set();
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
        // A plain Error says nothing by its class; when its message is itself a short code (chain_missing) that is the reason.
        // Any other message may hold a path or the user's words, so it is never copied.
        const named = value?.name === 'Error' && /^[a-z][a-z0-9_]{2,39}$/.test(value?.message) ? value.message : value?.name;
        // A failed export names the exception class (a type name, never a message), so the report says what broke.
        const code = (value?.reason === 'failed' && value?.class) || value?.reason || value?.code || named;
        return site + ':' + (String(code || '').replace(/[^A-Za-z0-9_.-]/g, '').slice(0, 40) || 'unknown');
    }
    // `where` marks a failure: the notice then carries the "Report a problem" button and stays longer to be reached.
    // Notices stack (newest last, at most three) instead of a new one wiping out the one being read; each can be closed.
    const noticeTimers = new Set();
    function notify(text, where = null, kind = null, action = null) {
        if (disposed) return;
        if (!text) { for (const timer of noticeTimers) clearTimeout(timer); noticeTimers.clear(); notices.replaceChildren(); return; }
        const last = notices.lastElementChild;
        if (last?.dataset.text === text) last.remove();
        const card = el('div', 'fv-ed-notice'); card.setAttribute('role', 'status'); card.dataset.text = text;
        const words = el('span', 'fv-ed-notice-text', text);
        // In the project row the line is cut to one row; its hover says it whole.
        words.setAttribute('data-fv-tip-name', where ? t('Problem', '问题') : t('Notice', '提示')); words.setAttribute('data-fv-tip-use', text);
        if (where) { words.classList.add('fv-copyable'); words.append(issueButton(t, () => ({where, message: text, kind}))); }
        const close = button('', () => { clearTimeout(timer); noticeTimers.delete(timer); card.remove(); }, 'fv-ed-notice-close');
        close.append(icon('m4 4 8 8M12 4l-8 8')); close.setAttribute('aria-label', t('Close', '关闭'));
        tip(close, {name: t('Close', '关闭'), use: t('Dismiss this notice', '关掉这条提示')});
        // A notice can carry one action (Undo after a delete), so a slip is one press away from being undone.
        if (action) { const act = button(action.label, () => { card.remove(); action.run(); }, 'fv-ed-notice-action'); tip(act, {name: action.label, use: action.use || '', key: action.key || ''}); card.append(words, act, close); }
        else card.append(words, close);
        notices.append(card);
        while (notices.children.length > 3) notices.firstElementChild.remove();
        const timer = setTimeout(() => { noticeTimers.delete(timer); card.remove(); }, where ? 20000 : 9000); noticeTimers.add(timer);
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
        if (value?.code === 'playback') {
            const text = t('This clip cannot be played.', '这段素材无法播放。');
            out(text, EDITOR_ISSUE, failureKind('playback', value)); return {ok: false, reason: code, say: text};
        }
        if (code === 'unsupported') { genbar?.update(null); return; }
        if (code === 'text_long') {
            const text = textLongText(t, Number.isInteger(value.limit) ? value.limit : 8000);
            out(text); return {ok: false, reason: code, say: text};
        }
        if (code === 'timed_text' || code === 'middle_frames') { out(value.say); return {ok: false, reason: code, say: value.say}; }
        if (PARTS_LIMIT_CODES.includes(code)) {
            const [en, zh] = (value.said || []).map(text => typeof text === 'string' && text ? text.slice(0, 200) : null);
            const text = en && zh ? t(en, zh) : partsLimitText(code, t, Number.isInteger(value.limit) ? value.limit : null);
            // A refused Generate keeps its reason on the bar's own line until the choice or the parts change.
            if (step === 'generate' && genbar && out === notify) genbar.say(text, false, null, true); else out(text);
            return {ok: false, reason: code, say: text};
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
        const text = step === 'generate' ? editorErrorText(value, t) : step === 'export' ? exportErrorText(value, t) : isNetworkTypeError(value) ? connectionLostText(t) : t('This step did not finish. Try again; if it keeps failing, report the problem.', '这一步没有完成。请重试；仍然失败时，请提交问题。');
        out(text, step === 'export' ? EXPORT_ISSUE : EDITOR_ISSUE, failureKind(site, value)); return {ok: false, reason: code, say: text};
    }
    const store = createEditorStore(api, {t, notify, standIn: given?.generationStandIn, onGenerationChecking: () => { if (!disposed) settleService(); }, onGenerationReady: ready => {
        if (disposed) return;
        // Only a server that said it cannot generate hides the bar; an unanswered check keeps it, greyed.
        generationAsked = true; genHost.hidden = !ready && store.generationGone; element.dataset.generationReady = ready ? 'true' : store.generationGone ? 'false' : 'waiting';
        goneLine.hidden = !genHost.hidden;
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
    // Media details from the cache draw at once and are read again from the server (marked as stand-ins).
    if (fromCache?.sources && Object.keys(fromCache.sources).length) try { store.cache(Object.fromEntries(Object.entries(fromCache.sources).map(([key, value]) => [key, {...value, _fallback: true}]))); } catch { /* Read again later. */ }
    let saveFailureShown = false;
    const queue = store.createSaveQueue({get record() { return state.record; },
        adopt(value) {
            if (pointerEditing) { deferredStamp = value; return; }
            if (!value.nameSuffix && state.record.id === value.id && state.record.revision === value.revision) return;
            suppressSave = true; try { state.adopt(value); } finally { suppressSave = false; }
            // A queued part kept under a new edit's first key moves to its saved one.
            if (held.length) rememberView();
        },
    }, {onStatus: () => {
        if (!queue.dirty) { discardOnSwitch = false; leaveTo = null; saveWarning.hidden = true; saveFailureShown = false; }
        updateSaveState();
    }, onFailed: error => {
        // An unexpected engine error says so once, with the way to report it; retries after it stay quiet.
        if (!isReportableSaveFailure(error) || saveFailureShown) return;
        saveFailureShown = true;
        notify(t('Saving changes failed because of an unexpected error. Your changes stay in this page and are saved again automatically.', '保存改动时出现意外错误。改动仍在这个页面里，会自动再次保存。'),
            EDITOR_ISSUE, failureKind('save', {reason: saveReason(error)}));
    }});
    // Whether the edit is saved, beside its name only when it needs attention: a failed save (which offers to save
    // again from there), changes left unsaved, or saving that takes longer than 2 s. A saved edit shows nothing; the
    // place is kept, so nothing beside it moves.
    let savingSince = 0, saveShowTimer = null;
    function updateSaveState() {
        if (disposed) return;
        const now = !queue.dirty ? 'saved' : queue.failure || discardOnSwitch ? 'unsaved' : 'saving';
        savingSince = now === 'saving' ? savingSince || performance.now() : 0;
        const slow = now === 'saving' && performance.now() - savingSince >= 2000;
        clearTimeout(saveShowTimer);
        if (now === 'saving' && !slow) saveShowTimer = setTimeout(updateSaveState, 2020 - (performance.now() - savingSince));
        saveState.dataset.shown = String(now === 'unsaved' || slow);
        if (saveState.dataset.state === now) return;
        saveState.dataset.state = now;
        saveState.textContent = {saved: t('Saved', '已保存'), saving: t('Saving…', '正在保存…'), unsaved: t('Not saved', '未保存')}[now];
        tip(saveState, {name: saveState.textContent, use: now === 'unsaved' ? t('Click to save the changes again', '点一下重新保存改动')
            : t('Changes save by themselves as you edit', '改动会自动保存')});
    }
    async function retrySave() {
        if (disposed) return;
        saveAgain.disabled = true; saveState.dataset.state = ''; updateSaveState();
        try { releaseStamp(); await queue.flush(); } finally { if (!disposed) { saveAgain.disabled = false; updateSaveState(); } }
    }
    const player = createEditorPlayer(preview, transport, {state, sources: store.sources, api, t, error, readAudioSamples: panel.readAudioSamples || readSpeedAudioSamples, preservePreview: () => initializing, prepareAudio: () => !initializing, speech: createSpeechSpans(store.speech)});
    // Until the first video frame arrives the picture shows the part's thumbnail at its own size, then fades to the video.
    const poster = createPicturePoster(player.element, {state, sources: store.sources, sourceKey, mediaURL: value => mediaURL(api, value)});
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
    // While a part is being made, the history keeps its generation step where it is.
    const making = () => jobActive() || held.length > 0 || sending.size > 0;
    state.holdGeneration(making);
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
    // A part at range.start whose first frames come from the finished clip right before it.
    function continuesEarlier(range) {
        const head = state.record.tracks[0].clips.find(clip => clip.start === range.start);
        return !!head && model.continuesBefore(state.record, head.id) && state.record.tracks[0].clips.some(clip =>
            clip.start + clip.frames === range.start && clip.source && clip.extend && clip.in + clip.frames === clip.length);
    }
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
        const range = selectionRange(selection), clips = state.record.tracks.filter(track => ['v1', 'a1', 'a2', 'a3'].includes(track.id)).flatMap(track => track.clips);
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
        const clips = state.record.tracks.filter(track => ['v1', 'a1', 'a2', 'a3'].includes(track.id)).flatMap(track => track.clips);
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
    function hold(selection, text, job = null, {texts = null, shown = selection === state.selection, allowEmpty = false} = {}) {
        const range = selectionRange(selection), selectedIds = selectionClips(selection).map(clip => clip.id);
        let ids = [];
        if (job) ids = selectedIds;
        if (!job) state.apply(t('Generate', '生成'), record => {
            const selected = selectedIds.map(id => model.find(record, id)).filter(Boolean), planned = selected.length && selected.every(clip => !clip.source && clip.pending && !generating(clip));
            const next = planned ? model.startAttempt(record, selected.map(clip => clip.id), texts ?? text) : model.planRange(record, {...range, target: 'both', text, maxSeconds: capability.maxSeconds, maxFollowSeconds: capability.maxFollowSeconds});
            ids = next.tracks[0].clips.filter(clip => clip.start >= range.start && clip.start < range.start + range.frames && clip.pending && !generating(clip)).map(clip => clip.id);
            return next;
        }, {checkpoint: true});
        if (!ids.every(isHeld)) held.push({ids, text, job, allowEmpty});
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
                try { await actions.generate({text: texts[clips[0].id], texts, selection, allowEmpty: entry.allowEmpty === true}); }
                catch (value) { const reason = error(value, 'generate'); if (reason && !PARTS_LIMIT_CODES.includes(reason.reason) && reason.reason !== 'timed_text' && reason.reason !== 'middle_frames') genbar?.say(reason.say); }
            }
        } finally { startingHeld = false; }
    }
    function completeSources() {
        return store.getSources(model.references(state.record).map(sourceKey)).then(() => { player.refresh(); tracks?.refresh?.(); });
    }
    // Music on the second sound track goes through the same bar; the bar shows a chosen piece's own job.
    const music = createEditorMusic({state, store, t, completeSources, onStatus: () => genbar?.update(barValue)});
    // Dubbing lines of a stretch of speech.
    let voiceReady = false, voiceInstallable = false, exportFps = false;
    const generatedSource = value => { const match = /^generated:(.+)$/.exec(String(value || '')); return match ? {kind: 'generated', id: match[1]} : null; };
    // Playing one line: the playhead goes to its start and the picture plays up to its end, then stops.
    let lineStop = null;
    function playVoiceLine(line) {
        const found = voice.target(); if (!found) return;
        lineStop?.();
        const from = Math.round(found.clip.start + (found.start + line.start) * model.FPS), to = Math.round(found.clip.start + (found.start + line.end) * model.FPS);
        state.seek(from); player.play();
        const finish = () => { unwatch(); if (lineStop === finish) lineStop = null; };
        const unwatch = state.subscribe(event => { if ((event.type ?? event) !== 'playhead') return; if (state.playhead >= to) { player.pause(); finish(); } else if (!player.playing) finish(); });
        lineStop = finish;
    }
    const voice = createEditorVoice({state, store: createVoiceStore({api, state, queue}), t, onChange: () => genbar?.update(barValue), available: () => voiceReady, recheck: readVoice, playLine: playVoiceLine, comparePlayback: async (clipId, name) => {
        // Hearing the other take changes nothing in the edit: only the preview swaps the sound.
        const source = generatedSource(name);
        if (!source) { player.previewClipSource(clipId, null); return; }
        await store.getSources([sourceKey(source)]);
        player.previewClipSource(clipId, source);
    }, place: async result => {
        // What to put into the project is what the service says in `apply`; the plain shape is the same as replacing a source.
        const apply = result.apply || {op: 'replaceSource', clip_id: result.clip_id, source: result.sources?.new};
        const dubbed = generatedSource(result.sources?.new);
        if (!dubbed || !apply.clip_id) throw new model.EditError('source');
        if (apply.op === 'placeStems' && !model.placeStems) throw new model.EditError('source');
        const stems = apply.op === 'placeStems' ? Object.fromEntries(model.STEM_TRACKS.map(name => [name, generatedSource(apply.sources?.[name])])) : null;
        if (stems && (!model.placeStems || !Object.values(stems).every(Boolean))) throw new model.EditError('source');
        const single = !stems && (generatedSource(apply.source) || dubbed);
        await store.getSources([...(stems ? Object.values(stems) : [single])].map(sourceKey));
        state.apply(t('Redub', '重新配音'), record => stems ? model.placeStems(record, apply.clip_id, stems) : model.replaceSource(record, apply.clip_id, single));
        const found = stems && state.record.tracks.flatMap(track => track.clips).find(clip => clip.stem_of === apply.clip_id && clip.source?.id === stems.dialogue.id);
        return {voiceClip: stems ? found?.id : apply.clip_id};
    }});
    let barValue = null;
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
    // Keeps the frame under the playhead as a picture in the image history, drawn as the export would draw it, from the saved project.
    let savingFrame = false;
    async function saveFrame() {
        if (savingFrame) return;
        if (initializing || switching) { notify(t('Opening the edit…', '正在打开…')); return; }
        if (!model.exportFrames(state.record)) { notify(t('The timeline is empty. Add a clip first.', '时间线是空的，请先放入素材。')); return; }
        const epoch = projectEpoch, shown = Math.min(state.playhead, Math.max(0, model.duration(state.record) - 1));
        savingFrame = true;
        try {
            if (!await saveForExport(epoch)) return;
            let picture = null;
            for (let attempt = 0; ; attempt++) {
                try { picture = await store.saveFrame(state.record.id, state.record.revision, shown); break; }
                catch (value) {
                    if (value.status !== 409 || attempt >= 2) throw value;
                    queue.enqueue(state.record);
                    if (!await saveForExport(epoch)) return;
                }
            }
            if (disposed || closing || epoch !== projectEpoch) return;
            if (picture) notify(t('Saved the current frame to Edit image > Generate with AI > Works.',
                '已保存当前帧，位于“编辑图片 > AI 生成 > 作品库”。'));
        } catch (value) {
            if (disposed || epoch !== projectEpoch) return;
            const said = {no_picture: t('There is no picture at this position. Move the playhead onto a clip.', '这个位置没有画面，请把播放头移到片段上。'),
                range: t('There is no picture at this position. Move the playhead onto a clip.', '这个位置没有画面，请把播放头移到片段上。'),
                generating: t('This part is still being generated. Save the frame when it is done.', '这一段还在生成，完成后再保存。'),
                unreadable: t('A source file cannot be read. Import it again from the library.', '有素材读不出来，请在素材库重新导入。'),
                not_writable: t('The picture folder cannot be written to. Check that the folder can be written to, then save the frame again.', '图片文件夹不可写。请检查这个文件夹的写入权限后再保存。'),
                disk_full: t('The disk is full. Free some space, then save the frame again.', '磁盘空间不足，请清理出空间后再保存。')}[value?.reason];
            if (said) notify(said); else error(value, 'frame');
        } finally { savingFrame = false; }
    }
    function updateTop() {
        updateSaveState();
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
        exportButton.disabled = false;
        const viewing = exportStatus?.state === 'done' && exportMatches();
        tip(exportButton, {name: t('Export', '导出'),
            use: viewing ? t('Open the exported video in the library', '在作品库里打开导出的视频') : running ? t('Exporting; it goes to the library when done', '正在导出，完成后进入作品库')
                : !model.exportFrames(state.record) ? t('The timeline is empty; add a clip first', '时间线是空的，先放入素材')
                : t('Save the whole edit as one video in the library', '把整条剪辑存成一个视频，放进作品库')});
        tip(exportCancel, {name: t('Cancel export', '取消导出'), use: t('Stop this export; no partial file is kept', '停止导出，不留下不完整的文件')});
    }
    async function snapshot() {
        let value = panel.snapshot ? clonePrompt(await panel.snapshot()) : clonePrompt(await app.graphToPrompt());
        if (value.output) {
            const node = String(panel.node?.id || panel.node || Object.keys(value.output).find(id => value.output[id].class_type === 'FreeVideoGenerate') || '');
            value = {node, prompt: value.output, extra_data: {extra_pnginfo: {workflow: value.workflow || {}}}};
        }
        if (!value.node || !value.prompt?.[value.node]) throw new Error('missing_node');
        const inputs = value.prompt[value.node].inputs;
        // With the creation panel's bar in the editor, its values are the request's (the edit's size is its starting point).
        if (!panel.barActive) Object.assign(inputs, state.record.settings || {}, capability.generationSize || generationSize(state.record));
        else if (bar?.overrides) for (const [name, setting] of Object.entries(bar.overrides())) if (name in inputs && !Array.isArray(inputs[name])) inputs[name] = setting;
        // The first-frame reference is the work's own setting, the one the length limit is worked out from.
        if (panel.barActive && 'first_frame_reference' in inputs && !Array.isArray(inputs.first_frame_reference)) inputs.first_frame_reference = state.record.settings?.first_frame_reference === true;
        // The media panel's pictures guide every part (the engine keeps only the references for a part that continues);
        // without a panel to show them they stay out of the request.
        if (!panel.references?.available()) delete inputs.media;
        else if (partStart() === 'fresh') {
            // Middle frames that cannot be used stop the run here, with the same sentence the picture strip shows.
            const range = selectionRange(), problem = panel.references.problem?.(t, range ? range.frames / model.FPS : null);
            if (problem) throw Object.assign(new Error('middle_frames'), {code: 'middle_frames', say: problem});
        }
        delete inputs.continue_from; delete inputs.continue_to;
        return {node: String(value.node), prompt: value.prompt, ...(value.extra_data ? {extra_data: value.extra_data} : {}), ...(api.clientId ? {client_id: api.clientId} : {})};
    }
    // A part drawn over imported sound follows that sound as it is: the picture is made for the range of the file the
    // parts cover, and the sound itself is kept.
    async function followSound(frozen, record, ids) {
        const videos = ids.map(id => model.find(record, id)).filter(Boolean).sort((a, b) => a.start - b.start);
        const sound = videos.map(clip => model.find(record, clip.link));
        if (!videos.length || sound.some(clip => clip?.source?.kind !== 'import' || store.sources.get(sourceKey(clip.source))?.has_video !== false)) return;
        const source = sound[0].source, metadata = store.sources.get(sourceKey(source));
        const blob = await (await fetch(metadata.audio)).blob(), extension = /\.[a-z0-9]{1,5}$/i.exec(String(metadata.name || metadata.path || ''))?.[0] || '.wav';
        const {file} = await uploadMedia(api, new File([blob], `editor-${source.id}${extension}`, {type: blob.type}));
        const rate = (await checkOriginal(api, {file})).sample_rate;
        if (!(rate > 0)) throw new Error('audio_unusable');
        const first = Math.round(sound[0].in * rate / model.FPS), frames = videos.reduce((sum, clip) => sum + clip.frames, 0);
        const row = {file, role: 'reference', use: 'original', range: {start_sample: first, end_sample: first + Math.round(frames * rate / model.FPS)}};
        const inputs = frozen.prompt[frozen.node].inputs, held = frozen.prompt[String(inputs.media?.[0])];
        if (held?.class_type === 'FreeVideoMedia') {
            const rows = JSON.parse(held.inputs.assets || '[]').filter(item => item.use !== 'original');
            held.inputs.assets = JSON.stringify([...rows, row]);
        } else {
            const id = String(1 + Math.max(0, ...Object.keys(frozen.prompt).map(Number).filter(Number.isFinite)));
            frozen.prompt[id] = {class_type: 'FreeVideoMedia', inputs: {assets: JSON.stringify([row])}}; inputs.media = [id, 0];
        }
    }
    // A job's news reaches only the parts that carry that job, so a step from before the part was sent keeps its draft.
    function pinJob(record, ids, job, pending) {
        return model.settle(record, ids.filter(id => model.attemptClip(record, id)?.job === job), pending);
    }
    function pinStoppedJob(record, ids, job) { return pinJob(record, ids, job, 'stopped'); }
    async function deliverChain(job) {
        if (disposed || !job) return false;
        const previous = jobStatuses.get(job.id);
        if (Number.isFinite(previous?.updated) && job.updated < previous.updated)
            return ['queued', 'running', 'cancelling'].includes(previous.state);
        failures.reconcile(job, previous);
        mostRecentJob = job.id; jobStatuses.set(job.id, job); paintJob(job);
        // The floating task window brings this run back to the editor, not to the creative panel.
        try { jobs.claim('chain:' + job.id, 'editor'); } catch { /* The task store is optional. */ }
        if (!startPlayheads.has(job.id)) startPlayheads.set(job.id, state.playhead);
        // Whether the job follows imported sound is read before a result replaces the sound's link target.
        if (!soundJobs.has(job.id) && (job.blocks || []).some(block => { const heard = model.find(state.record, model.attemptClip(state.record, block.id)?.link);
            return heard?.source?.kind === 'import' && store.sources.get(sourceKey(heard.source))?.has_video === false; })) soundJobs.add(job.id);
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
                    // A sound-led result the playhead has not reached is shown from its first frame, once per job and unless the viewer moved on meanwhile.
                    const done = model.find(state.record, block.id), picked = state.selection;
                    if (done && soundJobs.has(job.id) && !shownJobs.has(job.id)) {
                        shownJobs.add(job.id);
                        if (picked?.kind === 'clip' && picked.ids.includes(block.id) && !player.playing && state.playhead === startPlayheads.get(job.id)
                            && !(state.playhead >= done.start && state.playhead < done.start + done.frames)) state.seek(done.start);
                    }
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
            } else if (block.state === 'running' && clip.pending !== 'running') state.settle(record => pinJob(record, [block.id], job.id, 'running'));
        }
        // The status word of a part the engine dropped is read from the job, which arrives after the record is drawn.
        const dropped = job => (job?.blocks || []).filter(block => block.state === 'interrupted').length;
        if (dropped(job) !== dropped(previous)) tracks?.refresh?.();
        genbar?.update(barJob(job)); updateTop(); updateTransport();
        const active = ['queued', 'running', 'cancelling'].includes(job.state);
        if (completed.size || (!active && previous?.state !== job.state) || (job.output && job.output !== previous?.output))
            library?.refresh({outputs: job.output ? [job.output] : [...completed]});
        if (!active) jobWatchers.delete(job.id);
        if (job.state === 'done' && ['queued', 'running', 'cancelling'].includes(previous?.state)) offerNextPart(job);
        void startHeld();
        return active;
    }
    // A continued video that has just finished gets its next part ready at its end, as opening a work does, so the
    // next part continues it too. Only while the viewer is still on that video and nothing else is being sent.
    function offerNextPart(job) {
        const ids = new Set((job.blocks || []).map(block => block.id));
        const last = state.record.tracks[0].clips.filter(clip => ids.has(clip.id)).sort((a, b) => a.start - b.start).at(-1);
        if (!last || !model.continuesBefore(state.record, last.id) || generationBusy || held.length) return;
        const picked = state.selection;
        if (picked && !(picked.kind === 'clip' && picked.ids.some(id => ids.has(id) || model.find(state.record, id)?.link && ids.has(model.find(state.record, id).link)))) return;
        enterClip(clip => clip.id === last.id, projectEpoch, {select: false});
    }
    function watchJob(id) {
        if (disposed) return;
        if (!jobWatchers.has(id)) jobWatchers.set(id, store.watchChain(id, job => job ? deliverChain(job) : chainGone(id)));
    }
    // The engine no longer has this job: its unfinished parts stop where they are, as if the engine had dropped them.
    function chainGone(id) {
        if (disposed) return false;
        const previous = jobStatuses.get(id);
        if (previous) return deliverChain({...previous, state: 'interrupted',
            blocks: (previous.blocks || []).map(block => ['pending', 'queued', 'running'].includes(block.state) ? {...block, state: 'interrupted'} : block)});
        jobWatchers.delete(id);
        const ids = state.record.tracks[0].clips.filter(clip => clip.job === id && ['queued', 'running'].includes(clip.pending)).map(clip => clip.id);
        if (ids.length) state.settle(record => pinStoppedJob(record, ids, id));
        updateTop(); updateTransport();
        return false;
    }
    function recoverJobs() {
        for (const id of new Set(state.record.tracks[0].clips.map(clip => clip.job).filter(Boolean))) watchJob(id);
    }
    function rememberView() {
        if (!viewReady) return;
        const key = state.record.id || viewKey;
        if (state.record.id) {
            const sources = {};
            // The media's details without their descriptions: a work's prompt is kept once, with the work, not in this browser.
            for (const source of model.references(state.record).map(sourceKey)) { const value = store.sources.get(source); if (value) { const {text, prompt, ...kept} = value; sources[source] = kept; } }
            const entry = {id: state.record.id, record: state.record, sources};
            recordCache.set(entry.id, entry);
            try { const text = JSON.stringify(entry); if (text.length < RECORD_LIMIT) localStorage.setItem(RECORD_KEY, text); } catch {}
        }
        const value = {selection: state.selection, text: genbar.getText(), playhead: state.playhead,
            zoom: state.zoom, scrollLeft: tracks ? tracks.getScrollLeft() : editorViews.get(key)?.scrollLeft || 0};
        editorViews.set(key, value);
        try {
            // Queued parts stay queued across a reload; they are only planned drafts until sent.
            const waiting = held.map(entry => ({ids: entry.ids, text: entry.text, ...(entry.allowEmpty === true ? {allowEmpty: true} : {})}));
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
    // Recorded takes the edit no longer uses, once its history is gone (switching edits, leaving the view for good,
    // closing the page): their files are deleted. The server keeps a file another saved edit still uses.
    function releaseTakes({keepalive = false} = {}) {
        const ids = recording.unusedTakes(state.record);
        if (!ids.length) return;
        recording.forgetTakes(ids);
        for (const id of ids) {
            const sent = keepalive ? api.fetchApi('/freevideo/editor/import/delete', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id, confirm: false}), keepalive: true})
                : store.removeImport(id);
            Promise.resolve(sent).catch(() => {});
        }
    }
    async function replaceProject(record, key = null) {
        releaseTakes(); rememberView(); viewReady = false;
        await store.activateProject(record.id).catch(() => {});
        viewKey = record.id || key || 'draft:' + (globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`);
        let remembered = editorViews.get(viewKey);
        if (!remembered) try { remembered = JSON.parse(localStorage.getItem(viewPrefix + viewKey)); } catch {}
        initialSelection = offerInitial = extendEntry = null;
        projectEpoch++; held.length = 0;
        player.pause(); for (const stop of jobWatchers.values()) stop(); jobWatchers.clear(); jobStatuses.clear(); progress.clear(); runProgress.clear(); failures.clear(); progressConnection?.reset();
        mostRecentJob = null; submissionFailure = null; music.reset(); narration.reset(); genbar?.update(null); queue.reset(record); stopExport?.(); stopExport = null; exportStatus = exportSnapshot = null; exportButton.textContent = t('Export', '导出');
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
        await completeSources().catch(value => error(value, null, undefined, 'sources')); if (disposed) return; recoverJobs(); music.recover(); narration.recover();
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
            held.push({ids: [...entry.ids], text: entry.text, allowEmpty: entry.allowEmpty === true}); markHeld(entry.ids, true);
        }
        viewReady = true;
        if (record.id) try { localStorage.setItem('fv-editor-last', record.id); localStorage.setItem(usedPrefix + record.id, String(Date.now())); } catch {}
        updateTransport(); if (remembered?.held?.length) rememberView(); if (held.length) void startHeld();
    }
    async function switchProject(id) {
        if (switching || closing || id && id === state.record.id) return; switching = true; element.inert = true; updateTop();
        try {
            await within(queue.flush(), 6000);
            if (queue.dirty) { notify(saveErrorText(queue.failure, t), EDITOR_ISSUE, failureKind('save', {reason: saveReason(queue.failure)})); return; }
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
        for (const key of keys) { editorViews.delete(key); forgetCachedRecord(key); }
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
        let step = 'flush';
        try {
            const words = deleteEditText(t);
            if (jobActive()) throw {reason: 'generation_active'};
            await queue.flush(); releaseStamp();
            if (queue.dirty) { notify(saveErrorText(queue.failure, t), EDITOR_ISSUE, failureKind('save', {reason: saveReason(queue.failure)})); return; }
            // Load the replacement before deleting, so a read failure keeps the edit open.
            step = 'list';
            const rows = (await store.projects()).filter(row => row.id !== state.record.id);
            // Most recently used: the order the edits were opened, then the last save.
            const order = id => { try { return Number(localStorage.getItem(usedPrefix + id)) || 0; } catch { return 0; } };
            rows.sort((a, b) => order(b.id) - order(a.id) || String(b.updated_at).localeCompare(String(a.updated_at)));
            step = 'load';
            const record = rows.length ? await store.load(rows[0].id) : initial();
            if (!record) throw Object.assign(new Error(words.delete_failed), {code: 'no_replacement'});
            step = 'delete';
            await discardProject();
            step = 'open';
            await replaceProject(record); menu.hidden = true; title.setAttribute('aria-expanded', 'false'); title.focus();
        } catch (value) {
            const words = deleteEditText(t); notify(words[value?.reason] || words.delete_failed, EDITOR_ISSUE, failureKind('delete', {reason: deleteReason(value, step)}));
        } finally { switching = false; element.inert = false; updateTop(); }
    }
    function confirmDelete() {
        const words = deleteEditText(t, state.record.name);
        menu.replaceChildren();
        const question = el('p', 'fv-ed-delete-question', words.confirm);
        menu.append(question);
        const remove = button(words.remove, deleteCurrentProject, 'fv-ed-delete-confirm');
        const cancel = button(words.cancel, showMenu);
        menu.append(remove, cancel); cancel.focus();
        // What goes with it is listed as soon as the server answers; the question stays usable meanwhile.
        const id = state.record.id;
        if (id) void store.residue(id).then(found => {
            const line = words.also(found);
            if (line && question.isConnected && state.record.id === id) question.textContent = words.confirm + ' ' + line;
        });
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
        error, editError: error, notify, sources: store.sources, waveform: key => store.waveform(key), beats: key => store.beats(key), mediaURL: value => mediaURL(api, value),
        projectToken: () => initializing ? null : projectEpoch,
        // Earlier descriptions for the bar's list: the edit's parts (latest in time first), then the recent works' prompts
        // this page has read. Both are kept once, with the edit and the work; nothing is stored in the browser for this.
        descriptions: () => [...state.record.tracks.flatMap(track => track.clips).filter(clip => typeof clip.text === 'string' && clip.text.trim())
            .sort((a, b) => b.start - a.start).map(clip => clip.text),
            ...[...store.sources].filter(([key, value]) => key.startsWith('work:') && typeof value?.text === 'string').map(([, value]) => value.text)],
        togglePlay: () => player.toggle(),
        pause: () => player.pause(),
        async previewSource(source) {
            if (closing || disposed) return;
            const cached = store.sources.get(sourceKey(source));
            if (!initializing || (!cached?.video && !cached?.audio)) await store.getSources([sourceKey(source)]);
            // A preview whose details arrive after leaving never starts in the hidden editor.
            if (!closing && !disposed && !handle.kept) player.previewSource(source);
        },
        generationLimits: () => ({maxSeconds: capability.maxSeconds, maxFollowSeconds: capability.maxFollowSeconds}),
        generationAvailable: () => store.generationAvailable,
        // The server was asked and has not said yes: Generate is greyed until it does.
        generationWaiting: () => generationAsked && !store.generationAvailable,
        importFiles: (files, options) => library?.importFiles(files, options),
        clipIds: () => state.record.tracks[0]?.clips.map(clip => clip.id) || [],
        // The clip an import just added is selected and followed by one draft once it is placed. `known` holds the clip ids
        // recorded before that import, so an older imported clip at the start is not taken for the new one.
        async continueStart(known = []) {
            const epoch = projectEpoch, seen = new Set(known), match = clip => clip.source?.kind === 'import' && !seen.has(clip.id);
            for (let waited = 0; waited < 120 && !state.record.tracks[0]?.clips.some(match); waited++) {
                if (closing || disposed || epoch !== projectEpoch) return false;
                await new Promise(resolve => setTimeout(resolve, 100));
            }
            if (!state.record.tracks[0]?.clips.some(match)) return false;
            await store.generationReady();
            if (!enterClip(match, epoch, {fresh: true}) && !closing && !disposed) extendEntry = () => { if (enterClip(match, epoch, {select: false, fresh: true}) || closing || disposed) extendEntry = null; };
            return true;
        },
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
                    // Details that arrive after leaving never insert into the hidden edit.
                    if (!closing && !disposed && !handle.kept && epoch === projectEpoch) return actions.insertSource(value);
                });
            }
            const metadata = store.sources.get(sourceKey(value.source));
            if (metadata?.path && value.source?.kind !== 'import') void store.lineage([{key: sourceKey(value.source), reference: {work: metadata.path}}]).then(() => hintFullSource()).catch(() => {});
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
        // Stopping a run asks first (the floating window's own question); without that module the press stops at once.
        async confirmStop(anchor) {
            try { return await (await import('./task_float.js')).confirmStop(anchor, {kind: 'video'}); } catch { return true; }
        },
        undo: () => travel(true),
        reasonText: value => editorErrorText(value, t),
        failureText: id => failures.text(id),
        // A part that was running when the engine went away, as opposed to one the user stopped.
        interrupted: clip => clip.pending === 'stopped' && !clip.source && !!clip.job
            && (jobStatuses.get(clip.job)?.blocks || []).some(block => block.state === 'interrupted' && model.attemptClip(state.record, block.id)?.id === clip.id),
        selectedFailure() {
            const tune = music.failure() || narration.failure(); if (tune) return tune;
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
        // A range with no picture over imported sound: the picture is made to follow that sound.
        soundLed(selection = state.selection) {
            const range = selection?.kind === 'range' && selectionRange(selection);
            return !!range && !!model.soundRange(state.record, range.start, range.frames);
        },
        // Another part generating is no reason: the server runs parts one after another.
        canGenerate(text, selection = state.selection) {
            return store.generationAvailable && actions.supportedSelection(selection) && (!!text.trim() || !actions.needsText(selection));
        },
        // The description the box shows for a choice of planned parts, and the parts it is written to; null for
        // any other choice.
        partText(selection = state.selection) {
            if (music.target(selection)) return music.text(selection);
            if (narration.target(selection)) return narration.text(selection);
            const clips = selectionClips(selection).sort((a, b) => a.start - b.start);
            return clips.length && clips.every(editablePart) ? {ids: clips.map(clip => clip.id), text: clips[0].text} : null;
        },
        describeParts(ids, text) {
            text = wellFormed(text);
            if (ids.length === 1 && music.target({kind: 'clip', ids})) return music.describe(ids[0], text);
            if (ids.length === 1 && narration.target({kind: 'clip', ids})) return narration.describe(ids[0], text);
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
        // The creative panel's two-pass choices as this edit uses them: the edit's own two-pass setting decides
        // off or on, and the panel's "Preview first" applies when it samples twice. Null without an open panel.
        passMode() {
            const shared = panel.passMode; if (!shared) return null;
            const value = shared.state(), twoPass = state.record.settings?.two_pass ?? value.mode !== 'off';
            return {...value, mode: !twoPass ? 'off' : value.mode === 'off' ? 'on' : value.mode};
        },
        // One choice for both places: the panel takes it, and the edit takes the sampling settings it leaves.
        choosePass(mode) {
            const shared = panel.passMode; if (!shared) return;
            shared.choose(mode);
            const now = panel.sampling?.() || {}, set = state.record.settings || {};
            if (Object.entries(now).some(([key, value]) => set[key] !== value)) state.settle(record => model.setSampling(record, now));
            else genbar?.update(mostRecentJob ? jobStatuses.get(mostRecentJob) ?? null : null);
        },
        onPass: listener => panel.passMode?.subscribe(listener) || (() => {}),
        // The quality control as this edit uses it: the sampling settings the edit holds decide, the panel's fill in what it lacks.
        // Null without an open panel.
        effort() {
            const shared = panel.effort; if (!shared) return null;
            const now = shared.state(), set = state.record.settings || {};
            return {...now, baseSteps: Number(set.base_steps ?? now.baseSteps), refineSteps: Number(set.refine_steps ?? now.refineSteps),
                twoPass: set.two_pass ?? now.twoPass, stepCache: 'step_cache' in set ? set.step_cache : now.stepCache,
                upscale: set.upscale_factor ?? now.upscale};
        },
        effortLevels: () => panel.effort?.levels() || null,
        // Why the quality control cannot apply to what the next press makes: a part that continues another one, or any part of a video
        // made in parts, samples 8 + 3 steps whatever the control says (the engine continues in two passes only). null when it applies.
        effortLock(selection = state.selection) {
            if (!panel.effort || music.target(selection) || narration.target(selection)) return null;
            const run = partRun(selection), range = selectionRange(run ? clipChoice(run) : selection); if (!range) return null;
            if (continuesEarlier(range)) return 'continuation';
            try { return model.rangeParts(state.record, {...range, ...capability}).length > 1 ? 'parts' : null; }
            catch (value) { if (value instanceof model.EditError) return null; throw value; }
        },
        // One choice for both places, as for the two-pass choice above.
        chooseEffort(tier) {
            const shared = panel.effort; if (!shared) return;
            shared.choose(tier);
            const now = panel.sampling?.() || {}, set = state.record.settings || {};
            if (Object.entries(now).some(([key, value]) => set[key] !== value)) state.settle(record => model.setSampling(record, now));
            else genbar?.update(mostRecentJob ? jobStatuses.get(mostRecentJob) ?? null : null);
        },
        onEffort: listener => panel.effort?.subscribe(listener) || (() => {}),
        // A part made as a half-resolution preview: one part only, as the next part continues from this one's last
        // frames, which the second pass changes. It may continue the part before it where the engine can.
        previewFirst(selection = state.selection) {
            if (actions.passMode()?.mode !== 'preview' || music.target(selection)) return false;
            if ((state.record.settings?.base_steps ?? 8) !== 8) return false;
            const run = partRun(selection), chosen = run ? clipChoice(run) : selection, range = selectionRange(chosen); if (!range) return false;
            if (continuesEarlier(range) && !store.continuationPreview) return false;
            try {
                // The parts Generate would send, planned as it plans them.
                const selected = selectionClips(chosen), planned = selected.length && selected.every(clip => clip.pending && !clip.source);
                const next = planned ? state.record : model.planRange(state.record, {...range, target: 'both', maxSeconds: capability.maxSeconds, maxFollowSeconds: capability.maxFollowSeconds});
                const ids = planned ? selected.map(clip => clip.id) : next.tracks[0].clips.filter(clip => clip.start >= range.start && clip.start < range.start + range.frames
                    && clip.pending && !['queued', 'running'].includes(clip.pending)).map(clip => clip.id);
                return ids.length === 1 && (store.continuationPreview || !model.generationBefore(model.prepare(next, ids), ids).some(Boolean));
            } catch (value) { if (value instanceof model.EditError) return false; throw value; }
        },
        // The chosen preview part, shown from its first new frame, whose second pass can replace it in place.
        secondPassTarget(selection = state.selection) {
            if (selection?.kind !== 'clip' || !store.generationAvailable) return null;
            const clips = selectionClips(selection);
            if (clips.length !== 1) return null;
            const clip = clips[0], mate = clip.link ? model.find(state.record, clip.link) : null;
            // From the start of the preview: the second pass makes the same frames again, so a part cut short at its end still shows the same moment.
            if (!clip.source || clip.pending || mate?.pending || clip.in !== (clip.splice?.frames ?? 0) || clip.frames > clip.length) return null;
            const metadata = store.sources.get(sourceKey(clip.source));
            return metadata?.preview === true && metadata.path ? {clip, path: metadata.path} : null;
        },
        generationSending: () => generationBusy,
        async secondPass(selection = state.selection) {
            const found = actions.secondPassTarget(selection); if (!found) return;
            const choice = clipChoice([found.clip]);
            await actions.generate({text: found.clip.text || '', selection: choice, shown: selection === state.selection,
                label: t('Run second pass', '继续二采'), secondPass: found.path, allowEmpty: true});
        },
        // Pictures that guide a new part: the creative panel's media rows; a part that continues shows only its references.
        references: panel.references ? {source: panel.references, notify, seconds: () => { const range = selectionRange(); return range ? range.frames / model.FPS : null; }, size: () => capability.generationSize || generationSize(state.record),
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
            if (music.target(selection)) return music.estimate(selection);
            if (narration.target(selection)) return narration.estimate(selection);
            const run = partRun(selection), range = selectionRange(run ? clipChoice(run) : selection); if (!range) return null;
            let seconds = 0;
            for (const count of model.rangeParts(state.record, {...range, ...capability})) { const value = capability.estimate(count); if (value == null) return null; seconds += value; }
            return seconds;
        },
        // texts: a description for each planned part, by the part's picture; otherwise text is every part's.
        // secondPass: the preview video whose second pass replaces it; the engine continues that preview's own request.
        async generate({target = 'both', text = '', texts = null, label = t('Generate', '生成'), selection = state.selection, shown = selection === state.selection, allowEmpty = false, secondPass = null} = {}) {
            if (target !== 'both' || !actions.supportedSelection(selection)) throw new model.EditError('unsupported');
            // A half of a pair left by a cut or a paste is dropped; a description over the limit is said before anything is sent.
            text = wellFormed(text);
            // Only the user's own choice to go on without a description lets an empty one through.
            if (!(allowEmpty === true && !text.trim() && store.generationAvailable && actions.needsText(selection)) && !actions.canGenerate(text, selection)) throw new model.EditError('text');
            if (characters(text) > model.RULES.maxText) throw Object.assign(new model.EditError('text_long'), {limit: model.RULES.maxText});
            const previewing = !secondPass && actions.previewFirst(selection);
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
                const mark = state.step; // Replies about this generation leave the undo steps up to it as they were.
                for (const id of ids) sending.add(id);
                prepared = state.record;
                // Only a press on what is still chosen moves the choice; a part started in the background
                // leaves whatever the user has moved on to, and what they typed there.
                submittedSelection = shown && state.selection === chosen ? state.select({kind: 'clip', ids}) : state.selection; await completeSources();
                const paths = Object.fromEntries([...store.sources].map(([key, value]) => [key, value.path]));
                const request = model.generationRequest(prepared, ids, {text: texts ? undefined : text, paths, queue: true});
                // What the engine would refuse for its continuing parts is said here, before anything is sent.
                const beforeKeys = model.generationBefore(prepared, ids);
                const lineageWorks = beforeKeys.filter(key => key && !key.startsWith('chain:')).map(key => {
                    const metadata = store.sources.get(key);
                    return metadata?.path ? {key, reference: {work: metadata.path}} : null;
                }).filter(Boolean);
                if (lineageWorks.length) await store.lineage(lineageWorks);
                const problem = model.partsProblem(prepared, ids, {sourceLineage: store.knownLineage()});
                if (problem) {
                    const full = problem.code === 'chain_too_long' && beforeKeys.some(key => store.knownLineage()[key]?.parts >= model.RULES.maxParts);
                    throw Object.assign(new Error(full ? 'chain_source_full' : problem.code), {code: full ? 'chain_source_full' : problem.code, limit: problem.limit});
                }
                // Times in a description are read on the ruler; a part that continues keeps its text as typed.
                const timedSpan = block => ({offset: (model.find(prepared, block.id)?.start ?? 0) / model.FPS, seconds: block.frames / model.FPS});
                for (const block of request.blocks.filter(block => !block.after)) {
                    const {offset, seconds} = timedSpan(block), say = timedProblem(block.text, seconds, t, offset);
                    if (say) throw Object.assign(new Error('timed_text'), {code: 'timed_text', say});
                }
                const sizes = new Map(store.sources);
                for (const key of beforeKeys.filter(key => key?.startsWith('chain:'))) {
                    const job = await store.chain(key.split(':')[1]);
                    sizes.set(key, job?.generation_size);
                }
                const follow = continuedSize(beforeKeys, sizes), edit = capability.generationSize || generationSize(state.record);
                Object.assign(frozen.prompt[frozen.node].inputs, follow || {});
                // A preview is one part; any other part samples both passes, whatever the panel shows.
                const inputs = frozen.prompt[frozen.node].inputs, alone = request.blocks.length === 1 && (store.continuationPreview || !beforeKeys.some(Boolean));
                if (secondPass) {
                    if (!alone) throw new model.EditError('unsupported');
                    Object.assign(inputs, {two_pass: true, preview: false, refine_from: secondPass});
                } else {
                    if ('preview' in inputs || previewing) inputs.preview = previewing && alone;
                    if ('refine_from' in inputs) inputs.refine_from = '';
                }
                await followSound(frozen, prepared, ids);
                if (allowEmpty === true && [text, ...Object.values(texts ?? {})].some(value => !value.trim())) allowEmptyPrompt({output: frozen.prompt}, frozen.node);
                // Parts made at another size than the edit's have that size's limit.
                const limit = (follow && (follow.width !== edit.width || follow.height !== edit.height)
                    ? await store.requestFrames(follow, state.record.settings) : null) ?? capability.maxRequestFrames;
                const tooLong = request.blocks.filter(block => limit != null && block.frames > limit);
                if (tooLong.length) {
                    state.settle(record => tooLong.reduce((next, block) => model.fail(next, block.id, 'too_long', null), record), {after: mark});
                    return;
                }
                const values = new Uint32Array(1);
                if (globalThis.crypto?.getRandomValues) crypto.getRandomValues(values); else values[0] = Math.floor(Math.random() * 2 ** 32);
                const typed = panel.barActive ? bar?.overrides?.().seed : undefined; // a seed typed in the bar is the run's seed
                const seed = Number.isSafeInteger(typed) && typed >= 0 ? typed : values[0] === lastSeed ? (values[0] + 1) >>> 0 : values[0]; lastSeed = seed;
                // An @name in a description becomes <Subject N> and its definition, as in the creative panel; a part that
                // continues carries only the pictures the engine keeps for it, and is numbered among them.
                const castRows = panel.references?.available() ? panel.references.rows?.() ?? [] : [];
                const blocks = request.blocks.map(({before, ...block}, i) => ({...block, seed: seed + i,
                    text: expandCast(block.after ? block.text : (({seconds, offset}) => timedText(block.text, seconds, offset))(timedSpan(block)),
                        castRows, {continued: Boolean(block.after)}).text}));
                const job = await store.startChain({...frozen, blocks, queue: true});
                if (!job && !store.generationAvailable) {
                    if (epoch === projectEpoch) {
                        const restoreSelection = submittedSelection !== priorSelection && state.selection === submittedSelection;
                        // Reverse only this submission's changes. Keep save receipts,
                        // reserved IDs and unrelated edits made while POST was pending.
                        state.settle(record => restoreGeneration(record, beforeAttempt, prepared), {after: mark});
                        if (restoreSelection) state.select(priorSelection, {text: priorText});
                    }
                    return;
                }
                if (!job?.id) throw new Error('chain_missing');
                if (epoch !== projectEpoch) return;
                state.settle(record => model.assignJob(record, ids, job.id, {prepared}), {after: mark});
                queue.enqueue(state.record);
                failures.remember(job, frozen);
                mostRecentJob = job.id; jobStatuses.set(job.id, job); genbar.update(job); watchJob(job.id);
            } catch (value) {
                if (epoch === projectEpoch) {
                    if (ids.length) state.settle(record => model.settle(record, ids, 'draft'));
                    // A length limit is the user's to change and is said once in the notice; there is nothing to report.
                    if (!PARTS_LIMIT_CODES.includes(value?.code) && value?.code !== 'timed_text' && value?.code !== 'middle_frames') {
                        failures.submission(value, frozen, text); submissionFailure = JSON.stringify(state.selection);
                    }
                    genbar?.update(null);
                }
                throw value;
            } finally { sendingRanges.delete(range); for (const id of ids) sending.delete(id); if (disposed && ids.length) queue.enqueue(state.record); generationBusy = false; finishGeneration?.(); generationDone = null; finishGeneration = null; updateTop(); updateTransport(); startHeld(); }
        },
        // A press on Generate always answers: what can be done for the user is done, then said in one line.
        async press(text, {allowEmpty = false, allowDownload = false} = {}) {
            if (disposed || closing || initializing) return {say: t('Opening the edit…', '正在打开…')};
            text = wellFormed(text);
            if (voice.session() && ['ready', 'done'].includes(voice.session().phase)) { await voice.redub(); return {}; }
            // A stretch of the music track, or a piece of music there: music, with or without words.
            if (music.target()) {
                if (characters(text) > MUSIC_TEXT_LIMIT) return {say: textLongText(t, MUSIC_TEXT_LIMIT), focus: true};
                return music.press(text);
            }
            // An empty stretch of the recording track, or a line planned there: a new line in the voice chosen.
            if (narration.target()) {
                if (characters(text) > NARRATION_TEXT_LIMIT) return {say: textLongText(t, NARRATION_TEXT_LIMIT), focus: true};
                // The dubbing model is several GB: the first line asks before downloading it.
                const bytes = narration.downloadBytes();
                if (bytes > 0 && !allowDownload) return {confirmDownload: bytes};
                return narration.press(text);
            }
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
            if (characters(text) > model.RULES.maxText) return {say: textLongText(t, model.RULES.maxText), focus: true};
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
                        if (!found && !allowEmpty) return {say: said, empty: true, focus: true};
                        if (found) { value = found.text; used ??= found.before ? before : latest; }
                    }
                    texts[clip.id] = value;
                }
                text = texts[chosen[0].id];
                selection = clipChoice(run);
            } else if (!text.trim() && actions.needsText(selection)) {
                const found = nearbyText(selectionRange(selection).start);
                if (!found && !allowEmpty) return {say: said, empty: true, focus: true};
                if (found) { text = found.text; used = found.before ? before : latest; }
                state.setRangeText(text); genbar?.setText(text);
            } else if (!text.trim()) {
                // The box was emptied; the part keeps the description it took over, as an empty one cannot be generated.
                text = state.record.tracks[0].clips.find(clip => clip.start === selectionRange(selection).start).text;
                state.setRangeText(text); genbar?.setText(text);
            }
            const waiting = waitingFor(selection);
            if (waiting) {
                hold(selection, texts ? texts[run[0].id] : text, null, {texts, shown, allowEmpty});
                return {text, say: [said, used, waiting === 'ready' ? queuedReady()
                    : t('Queued. It starts after the previous part finishes.', '已排队，上一段完成后自动开始。')].filter(Boolean).join(' '), queued: true};
            }
            const busyBefore = jobActive();
            await actions.generate({text: texts ? texts[run[0].id] : text, texts, selection, shown, allowEmpty});
            return {text, say: [said, used, busyBefore ? t('Queued. It starts after the part now generating.', '已排队，正在生成的那段完成后自动开始。') : null].filter(Boolean).join(' ') || null, queued: busyBefore};
        },
        selectionRunning: () => music.target() ? music.running() : narration.target() ? narration.running() : busySelection(state.selection),
        // A choice over existing clips that Generate would move to an empty spot.
        selectionMovable: () => !music.target() && !narration.target() && !actions.supportedSelection() && !!emptySpot(state.selection),
        selectionSending() { const range = selectionRange(state.selection); return !!range && ([...sendingRanges].some(other => overlaps(other, range)) || [...sending].some(id => { const clip = model.find(state.record, id); return clip && overlaps(clip, range); })); },
        selectionHeld() { const ids = selectionClips(state.selection).map(clip => clip.id); return ids.length > 0 && ids.every(isHeld); },
        // Takes the selected parts out of the line; they stay as drafts.
        unhold() {
            const ids = selectionClips(state.selection).map(clip => clip.id);
            for (let i = held.length - 1; i >= 0; i--) if (held[i].ids.some(id => ids.includes(id))) { markHeld(held[i].ids, false); held.splice(i, 1); }
            rememberView();
        },
        async cancel() {
            if (music.target()) { await music.cancel(); return; }
            if (narration.target()) { await narration.cancel(); return; }
            if (stems?.active()) { await stems.cancel(); return; }
            if (voice.session()?.phase === 'dubbing') { voice.cancel(); return; }
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
            state.settle(record => pinJob(record, ids, id, 'queued')); if (job) { await deliverChain(job); watchJob(id); }
        },
        async retry(id) {
            state.select({kind: 'clip', ids: [id]}); const clip = model.find(state.record, id); if (!clip) return;
            if (clip.job) { mostRecentJob = clip.job; await actions.resume(); }
            else if (!store.generationAvailable) {
                const text = wellFormed(clip.text || panel.text || '');
                if (characters(text) > model.RULES.maxText) { error(Object.assign(new model.EditError('text_long'), {limit: model.RULES.maxText}), 'generate'); return; }
                if (!actions.selectionHeld()) hold(state.selection, text);
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
            watch: (id, fn, lost) => store.watchEars(id, fn, lost),
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
                    report(install.total > 0 ? install.done / install.total : 0, install.phase); return true;
                };
                store.installEars().then(value => { if (step(value)) store.watchEarsPack(step, reject); }, reject);
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
            if (await handle.close() === false) throw new Error(saveWarningText.textContent);
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
        // A running export the engine no longer knows was lost in a restart: free the button and say so.
        if (!value) { if (!disposed && exportStatus?.state === 'running') { exportStatus = null; exportCancel.hidden = true; updateTop();
            notify(t('FreeVideo restarted and the export stopped. Export again.', 'FreeVideo 已重启，导出已中断。请重新导出。'), EXPORT_ISSUE, 'export:gone'); } return false; }
        if (exportSnapshot) exportSnapshot = {...exportSnapshot, id: value.project_id, revision: value.revision};
        exportStatus = value; const running = value.state === 'running' || value.state === 'queued';
        if (disposed) { if (!running) window.FreeVideoNotifyTaskDone?.({kind: 'export', id: value.id, ok: value.state === 'done'}); return running; }
        exportCancel.hidden = !running; updateTop(); exportButton.classList.remove('fv-primary');
        if (!running) {
            if (value.state === 'failed') {
                if (['source_missing', 'missing_glyphs', 'MissingGlyphs'].includes(value.reason || value.code)) error(value, null, undefined, 'export');
                else notify(value.reason === 'not_writable' ? t('Export failed: the output folder cannot be written to. Check that the folder can be written to, then retry.', '导出时出错：输出目录不可写。请检查这个文件夹的写入权限后重试。')
                    : value.reason === 'disk_full' ? t('Export failed: the disk is full. Free some space, then export again.', '导出时出错：磁盘空间不够，空出空间后再导出。')
                    : value.reason === 'interp_download' ? t('Export failed: the model needed for 48/60 fps could not be downloaded. Check the connection and export again, or set the frame rate to 24.', '导出时出错：48/60 帧率需要的模型没能下载。请检查网络后重新导出，或把帧率改为 24。')
                    : value.reason === 'interp_memory' ? t('Export failed: the graphics card did not have enough free memory for 48/60 fps. Export again after the current generation finishes, or set the frame rate to 24.', '导出时出错：显卡空闲显存不足，无法导出 48/60 帧率。请等当前生成结束后重新导出，或把帧率改为 24。')
                    : value.reason === 'unreadable' ? t('Export failed: a source file cannot be read. Import it again from the library.', '导出时出错：有素材读不出来，请在素材库重新导入。')
                    : t('Export failed. Try again.', '导出时出错，请再试一次。'), EXPORT_ISSUE, failureKind('export', value));
            }
            if (value.state === 'cancelled') notify(t('Export cancelled. No unfinished file is kept.', '已取消导出，没有留下不完整的文件。'));
            window.FreeVideoNotifyTaskDone?.({kind: 'export', id: value.id, ok: value.state === 'done'});
        }
        return running;
    }

    // The dubbing model is downloaded from the bar where the lines would be; once it is there, the stretch chosen is looked at.
    async function readVoice() {
        const found = await voiceFeatures(api);
        // An unanswered question changes nothing: the lines stay as they were, and a failed recognition says so.
        if (found.unknown) return voiceReady;
        voiceReady = found.ready; voiceInstallable = found.install; exportFps = found.exportFps;
        if (!disposed) { voice.refresh(); narration.refresh(); if (found.install) voiceInstall.init(); genbar?.update(barValue); }
        return found.ready;
    }
    const voiceInstall = createVoiceInstall({api, available: () => voiceInstallable && !voiceReady, target: selection => !!state.record && !!voiceTarget(state.record, selection),
        onChange: () => { if (!disposed) genbar?.update(barValue); }, onReady: readVoice});
    // A new line said in a voice of the edit, on an empty stretch of the recording track; heard from the clip's own sound.
    let sampleStop = null;
    function playSample({clip: id, start, end}, onEnd) {
        sampleStop?.();
        const clip = model.find(state.record, id), metadata = clip?.source && store.sources.get(sourceKey(clip.source));
        const url = metadata && mediaURL(api, metadata.audio || metadata.video);
        if (!url) { onEnd(); return () => {}; }
        player.pause();
        const media = new Audio(url), from = clip.in / model.FPS + start, to = clip.in / model.FPS + end;
        let stopped = false;
        const stop = () => { if (stopped) return; stopped = true; media.pause(); media.removeAttribute('src'); media.load(); if (sampleStop === stop) sampleStop = null; };
        const finish = () => { stop(); onEnd(); };
        media.ontimeupdate = () => { if (media.currentTime >= to) finish(); };
        media.onended = media.onerror = finish;
        media.onloadedmetadata = () => { media.currentTime = from; media.play().catch(finish); };
        sampleStop = stop;
        return stop;
    }
    const narration = createEditorNarration({state, store: createVoiceStore({api, state, queue}), t, sources: () => store.sources,
        available: () => voiceReady || voiceInstallable, ready: () => voiceReady, install: voiceInstall, play: playSample,
        say: text => genbar?.say(text), completeSources, onStatus: () => genbar?.update(barValue)});
    readVoice();
    actions.voice = {controller: voice, install: voiceInstall};
    actions.narration = narration;
    actions.music = {target: selection => music.target(selection), preset: selection => music.preset(selection), choose: value => music.choose(value),
        styles: music.styles, moods: music.moods, downloadBytes: music.downloadBytes};
    let bar = null, barLabel = '', attachBar = null, detachBar = null, guideBack = [];
    actions.onLabel = text => { barLabel = text; if (bar) queueMicrotask(() => bar.sync()); };
    genbar = createEditorGenbar(genHost, {state, t, actions, text: ''}); settleService();
    // The creation panel's own bar (same chips, trays and rules) stands in for the description, size, duration and quality
    // controls here; what the editor adds (music, narration, dubbing, downloads) stays in its own bar. Without a panel to
    // borrow from, the editor's bar is shown as before.
    if (panel.borrowBar) {
        let observer = null, arranged = false, unlisten = null, echo = false, lent = false, home = '', arrangeNow = null; // the subscription below outlives one borrow, so what it reads is kept out here
        attachBar = () => {
        if (bar) return;
        bar = panel.borrowBar({mount: genHost, onSeconds: value => { const field = genbar.duration; if (!field || field.element.hidden || field.input.disabled || !(value > 0)) return; field.input.value = String(Math.round(value)); field.commit(); }, label: () => barLabel || null, disabled: () => !genbar.canGenerate(),
            submit: async options => { genbar.setText(bar.prompt.value); await genbar.press(options?.allowEmpty === true); },
            onFirstFrame: value => genbar.firstFrame?.set(value)});
        if (bar) {
            genHost.classList.add('fv-ed-genbar-borrowed');
            // The editor's own first-frame switch is hidden with its bar; the borrowed bar's switch (in its frame pop-up,
            // where the creation panel keeps it) follows the editor's rule and saves to the edit.
            genbar.firstFrame?.mirror(next => bar?.firstFrame?.(next));
            element.dataset.barBorrowed = 'true';
            // The guide's editor steps ring the borrowed bar's prompt, media and Generate, not the editor's hidden ones.
            guideBack = [['editor-describe', '[data-fv-guide="describe"]'], ['editor-refs', '.fvs-chip[data-chip="media"]'], ['editor-generate', '[data-fv-guide="generate"]']].flatMap(([mine, selector]) => {
                const own = genHost.querySelector(`[data-fv-guide="${mine}"]`), lent = bar.shell.querySelector(selector);
                if (!lent) return [];
                const theirs = lent.dataset.fvGuide;
                if (own) delete own.dataset.fvGuide;
                lent.dataset.fvGuide = mine;
                return [() => { if (theirs) lent.dataset.fvGuide = theirs; else delete lent.dataset.fvGuide; if (own) own.dataset.fvGuide = mine; }];
            });
            // The bar's height is the row's: the timeline below keeps what is left.
            const fit = () => element.style.setProperty('--fv-ed-generation-row', `${Math.ceil(bar.shell.getBoundingClientRect().height) + 18}px`);
            if (typeof ResizeObserver === 'function') { observer = new ResizeObserver(fit); observer.observe(bar.shell); }
            fit();
            // What the three ways in prefill and lock: a new part takes the edit's size and the selection's length; a part
            // that continues keeps the size it continues; one made again keeps its size and length.
            // The description each part keeps is bound in the editor's own box (hidden); the bar's box shows it and writes it back.
            echo = false; lent = false;
            const field = bar.prompt; home = field.value; // the panel's own text, given back when the bar is released
            const onType = () => { if (!echo) genbar.type(field.value); }, onLeave = () => genbar.leave();
            field.addEventListener('input', onType); field.addEventListener('blur', onLeave);
            unlisten = () => { field.removeEventListener('input', onType); field.removeEventListener('blur', onLeave); if (lent) { field.value = home; field.dispatchEvent(new Event('input', {bubbles: true})); } };
            const arrange = () => {
                if (!bar) return;
                const selection = state.selection, clips = selection?.kind === 'clip' ? selection.ids.map(id => model.find(state.record, id)).filter(Boolean) : [];
                const edit = capability.generationSize || generationSize(state.record), again = clips.some(clip => clip.source);
                // Parts that cannot be generated together (apart on the timeline) continue nothing: the bar shows their span unlocked.
                const continuing = !again && clips.length > 0 && (() => { try { return model.generationBefore(state.record, clips.map(clip => clip.id)).some(Boolean); }
                    catch (value) { if (value instanceof model.EditError) return false; throw value; } })();
                const range = selectionRange(), length = range ? range.frames / model.FPS : null; // the span, not a sum: a part's picture and sound are two linked clips over the same frames
                echo = true;
                if (clips.length) lent = true;
                try { bar.apply({initial: {size: edit, ...(clips.length ? {text: genbar.shown()} : {}), ...(length ? {seconds: Number(length.toFixed(3))} : {})}, locked: again ? ['size', 'seconds'] : continuing ? ['size'] : []}); } finally { echo = false; }
            };
            arrangeNow = arrange; arrange(); if (!arranged) { arranged = true; state.subscribe(event => {
                if (event.type === 'selection') arrangeNow?.();
                // An undo or a description written elsewhere shows in the bar too, unless it is being typed in.
                else if (event.type === 'record' && bar && state.selection?.kind === 'clip' && document.activeElement !== bar.prompt && bar.prompt.value !== genbar.shown()) { echo = true; lent = true; try { bar.apply({initial: {text: genbar.shown()}}); } finally { echo = false; } }
            }); }
        }
        };
        // Hidden, the editor gives the panel its bar back; shown again, it takes it once more.
        detachBar = () => { genbar.firstFrame?.mirror(null); observer?.disconnect(); observer = null; unlisten?.(); unlisten = null; for (const back of guideBack.splice(0)) back(); bar?.release(); bar = null; panel.barActive = false; genHost.classList.remove('fv-ed-genbar-borrowed'); delete element.dataset.barBorrowed; };
        cleanupBar = detachBar;
        attachBar();
    }
    {
        // What the editor gives the bar is the video job's; a chosen piece of music being made shows its own instead.
        const update = genbar.update;
        genbar.update = value => { barValue = value; update(music.barStatus() || narration.barStatus() || value); };
        state.subscribe(event => { if (event.type === 'selection' || event.type === 'record') update(music.barStatus() || narration.barStatus() || barValue); });
        music.refreshInfo();
    }
    const adjust = adjustPanel = createAdjustPanel(adjustHost, {state, t, actions});
    {
        const adjustRail = button('', () => adjust.toggleButton.click(), 'fv-ed-rail-button');
        adjustRail.append(railGlyph('M3 1v14M8 1v14M13 1v14M1 5h4M6 11h4M11 5h4'), el('span', 'fv-ed-rail-label', t('Adjust', '调整')));
        tip(adjustRail, {name: t('Adjust', '调整'), use: t('Volume, speed, picture and text of the selection', '调整选中片段的声音、速度、画面和文字')});
        rightRail.append(adjustRail);
        const sync = () => adjustRail.setAttribute('aria-expanded', adjust.toggleButton.getAttribute('aria-expanded') || 'false');
        new MutationObserver(sync).observe(adjust.toggleButton, {attributes: true, attributeFilter: ['aria-expanded']}); sync();
    }
    // What the generate bar shows for a while (the music and voice choices, the dubbing lines and model, a failure, its
    // questions) goes at the head of the inspector from 681 px up, beside the picture instead of over it (補充六). Between
    // 681 and 1279 px the inspector is a drawer: it opens for those by itself, without taking the keyboard, and folds again
    // when they are gone, unless the user opened it. On a phone the tray stays a sheet above the bar.
    {
        const tray = genbar.tray, wide = window.matchMedia?.('(min-width: 681px)');
        let openedForTray = false;
        const place = () => {
            if (disposed) return;
            const inspector = !wide || wide.matches;
            if (inspector && tray.parentNode !== adjustHost) adjustHost.prepend(tray);
            else if (!inspector && tray.parentNode !== genHost) genHost.append(tray);
            element.dataset.trayPlace = inspector ? 'inspector' : 'bar';
            // Notices take the free middle of the project row (a slot of fixed height) from 681 px up; on a phone, the top of the picture.
            if (inspector && notices.parentNode !== projectRow) projectRow.insertBefore(notices, exportBox);
            else if (!inspector && notices.parentNode !== element) element.append(notices);
        };
        const passive = node => node.classList.contains('fv-request-assets') || node.classList.contains('fv-ed-ref-notice');
        const follow = () => {
            if (disposed || element.dataset.trayPlace !== 'inspector' || !adjust.narrow) return;
            const shown = [...tray.children].some(node => !node.hidden && !passive(node));
            if (shown && !adjust.overlay) { adjust.open({focus: false}); openedForTray = true; }
            else if (!shown && openedForTray) { openedForTray = false; if (adjust.overlay) adjust.close(); }
        };
        place(); wide?.addEventListener?.('change', place);
        listeners.push(() => wide?.removeEventListener?.('change', place));
        if (typeof MutationObserver === 'function') {
            const watcher = new MutationObserver(() => follow());
            watcher.observe(tray, {childList: true, subtree: true, attributes: true, attributeFilter: ['hidden']});
            listeners.push(() => watcher.disconnect());
        }
        adjust.toggleButton.addEventListener('click', () => { openedForTray = false; });
    }
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
    // Generate is one step in the history; while a part is being made it is neither undone nor redone, and a press that changes nothing says so.
    function travel(back) {
        const step = state.step, could = back ? state.canUndo : state.canRedo; back ? state.undo() : state.redo();
        if (state.step !== step) return;
        if (could) notify(back ? t('This part is generating. Undo once it finishes.', '这一段正在生成，完成后再撤销。')
            : t('This part is generating. Redo once it finishes.', '这一段正在生成，完成后再重做。'));
        else if (back) notify(t('Nothing to undo.', '没有可以撤销的改动。'));
        else notify(t('Nothing to redo.', '没有可以重做的改动。'));
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
        // Each press steps one take further back (and from the first take round to the newest), instead of
        // swapping between the last two (E28).
        const clip = selectedContent().find(clip => clip.versions.length);
        const target = clip && previousTake(clip);
        if (target) try { state.apply(t('Previous version', '上一版'), record => model.restoreVersion(record, clip.id, target.index)); } catch (value) { error(value, null, undefined, 'restore'); }
    }, 'fv-ed-previous');
    function previousTake(clip) {
        const list = model.takes(clip), at = list.findIndex(item => item.index === -1);
        if (at < 0) return list.find(item => item.usable) || null;
        return [...list.slice(0, at)].reverse().find(item => item.usable) || [...list.slice(at + 1)].reverse().find(item => item.usable) || null;
    }
    // A whole preview part continues to its full-resolution video in place; any other part shows nothing here.
    const secondPassButton = toolButton(t('Run second pass', '继续二采'), 'M9.5 2.5h4v4M13.5 2.5 9 7M6.5 13.5h-4v-4M2.5 13.5 7 9',
        () => { void runSecondPass(); }, 'fv-ed-second-pass');
    const secondPassLabel = el('span', 'fv-ed-second-pass-label');
    secondPassButton.classList.remove('fv-ed-icon'); secondPassButton.append(secondPassLabel); secondPassButton.hidden = true;
    let secondPassFailed = null;
    function updateSecondPass() {
        const found = actions.secondPassTarget();
        secondPassButton.hidden = !found;
        if (!found) { secondPassFailed = null; return; }
        if (secondPassFailed !== found.clip.id) secondPassFailed = null;
        const words = secondPassFailed ? t('Second pass did not start. Try again', '二采没有开始，请重试') : t('Run second pass', '继续二采');
        secondPassLabel.textContent = words; secondPassButton.setAttribute('aria-label', words);
        // Its own hover words (the shared table still names it with an internal word).
        tip(secondPassButton, {name: words, use: t('Turn this preview part into the full-resolution video, in place', '把这段预览原地做成全分辨率成片'),
            why: generationBusy ? t('Wait for the current generation to finish', '等当前生成完成') : ''});
        secondPassButton.disabled = generationBusy;
    }
    async function runSecondPass(selection = state.selection) {
        const found = actions.secondPassTarget(selection);
        if (!found || generationBusy) return;
        secondPassFailed = null;
        try { await actions.secondPass(selection); }
        catch (value) { if (!disposed) { secondPassFailed = found.clip.id; error(value, 'generate'); } }
        finally { if (!disposed) updateSecondPass(); }
    }
    actions.runSecondPass = runSecondPass;
    const recordTool = toolButton(t('Record', '录音'), RECORD_ICON, () => {}, 'fv-ed-record-toggle'); recordTool.setAttribute('aria-pressed', 'false'); recordTool.dataset.tourId = 'record-entry';
    tip(recordTool, {name: t('Record', '录音'), use: t('Record from the microphone at the playhead', '用麦克风从播放头处录一段')});
    historyTools.append(toolUndo, toolRedo); editTools.append(split, remove, closeGap, previous, recordTool);
    const pictureTools = createPictureTools({state, sources: store.sources, t, capabilities: () => capability, error});
    toolbar.append(historyTools, editTools, zoomHost); element.insertBefore(toolbar, tracksHost);
    const transportControls = createEditorTransport({element, preview, transport, toolbar, editTools, zoomHost, state, player, t, notify, saveFrame});
    editTools.append(secondPassButton);
    toolbar.append(adjust.toggleButton);
    const textTools = createEditorText({element, state, t, actions, adjust, player, toolbar, exportBox, tracksHost});
    const toolbarMore = createToolbarMore({toolbar, t, keep: '.fv-ed-undo, .fv-ed-split, .fv-ed-delete, .fv-ed-adjust-toggle, .fv-ed-zoom, .fv-ed-stems-button, .fv-ed-second-pass',
        touchKeep: '.fv-ed-undo, .fv-ed-redo, .fv-ed-split, .fv-ed-delete, .fv-ed-snapping, .fv-ed-adjust-toggle, .fv-ed-zoom, .fv-ed-stems-button, .fv-ed-second-pass',
        touchWideKeep: '.fv-ed-record-toggle, .fv-ed-add-text'});
    actions.speech.ability().then(value => { if (!disposed) textTools.speech(value); }).catch(() => {});
    // The command palette (Ctrl/Cmd+K) offers this view's actions while it is open; a greyed one says why (the control's own reason).
    {
        const why = control => () => control.disabled || control.hidden ? (control.getAttribute('data-fv-tip-why') || t('Not available right now', '现在不能用')) : '';
        const press = selector => () => element.querySelector(selector)?.click();
        const offCommands = window.FreeVideoCommands?.registerCommands?.('editor', [
            {id: 'editor.generate', title: ['Generate the chosen part', '生成选中的这一段'], key: 'Mod+Enter', icon: 'spark',
                why: () => { const go = genHost.querySelector('.fv-ed-generate'); return !go || go.disabled || go.hidden ? (go?.getAttribute('data-fv-tip-why') || t('Drag a range on the timeline first', '先在时间线上拖出一段')) : ''; },
                run: press('.fv-ed-generate')},
            {id: 'editor.export', title: ['Export the edit', '导出剪辑'], use: ['Save the whole edit as one video', '把整条剪辑存成一个视频'], run: () => exportButton.click()},
            {id: 'editor.undo', title: ['Undo', '撤销'], key: 'Mod+Z', group: ['Editing', '编辑'], why: why(toolUndo), run: () => travel(true)},
            {id: 'editor.redo', title: ['Redo', '重做'], key: 'Mod+Shift+Z', group: ['Editing', '编辑'], why: why(toolRedo), run: () => travel(false)},
            {id: 'editor.split', title: ['Split at the playhead', '在播放头处剪开'], key: 'B', group: ['Editing', '编辑'], why: why(split), run: () => split.click()},
            {id: 'editor.delete', title: ['Delete the selection', '删除选中的内容'], key: 'Delete', group: ['Editing', '编辑'], why: why(remove), run: () => remove.click()},
            {id: 'editor.gaps', title: ['Remove gaps', '移除空隙'], group: ['Editing', '编辑'], why: why(closeGap), run: () => closeGap.click()},
            {id: 'editor.previous', title: ['Previous version of this part', '这一段换回上一版'], group: ['Editing', '编辑'], why: why(previous), run: () => previous.click()},
            {id: 'editor.record', title: ['Record from the microphone', '用麦克风录音'], group: ['Sound', '声音'], run: () => recordTool.click()},
            {id: 'editor.adjust', title: ['Adjust the selection', '调整选中的片段'], use: ['Volume, speed, picture and text', '声音、速度、画面和文字'], run: () => adjust.narrow ? adjust.open() : adjustHost.querySelector('[role=tab][aria-selected=true]')?.focus()},
            {id: 'editor.import', title: ['Import files', '导入文件'], keywords: ['media', 'library', '素材'], run: press('.fv-ed-library-import')},
            {id: 'editor.history', title: ['Earlier descriptions', '用过的描述'], keywords: ['prompt', 'history', '提示词', '历史'], why: () => genHost.querySelector('.fv-ed-history-button')?.disabled ? t('Choose a part first', '先选中一段') : '', run: press('.fv-ed-history-button')},
            {id: 'editor.frame', title: ['Save the current frame as a picture', '把当前帧存成图片'], run: () => void saveFrame()},
            {id: 'editor.new', title: ['New edit', '新建剪辑'], group: ['Edit', '剪辑'], run: () => void switchProject(null)},
            {id: 'editor.rename', title: ['Rename or open another edit', '改名或打开其他剪辑'], group: ['Edit', '剪辑'], run: () => void showMenu()},
        ]);
        if (typeof offCommands === 'function') listeners.push(offCommands);
    }
    // The shortcut sheet and the command palette list the keys this view added.
    import('./shortcuts.js').then(module => {
        if (disposed) return;
        listeners.push(module.registerShortcuts('editor', [
            {keys: 'Mod+Enter', title: ['Generate (in the description)', '生成（在描述框里）'], group: ['Generate', '生成']},
            {keys: 'Mod+Y', title: ['Redo', '重做'], group: ['Editing', '编辑']},
            {keys: 'Shift+← / Shift+→', title: ['Back / forward ten frames', '后退 / 前进 10 帧'], group: ['Playback', '播放']},
        ]));
    }).catch(() => {});
    const audio = createAudioIntegration({state, player, t, error, adjust, library: libraryHost, store});
    stems = createStemsIntegration({state, api, queue, store, t, error, notify, toolbar, show: job => genbar?.update(job),
        restore: () => genbar?.update(mostRecentJob ? barJob(jobStatuses.get(mostRecentJob)) : null)});
    const recording = createEditorRecord({state, player, t, error, notify, button: recordTool, bar: element, library: {importFiles: (files, options) => actions.importFiles(files, options)}});
    element.insertBefore(recording.element, tracksHost);
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
    function updateSplit() {
        const ids = splitIds(), busy = ids.some(id => model.pair(state.record, id).some(isBusy)); setDisabled(split, !ids.length || busy);
        tip(split, {name: t('Split', '剪开'), use: t('Cut the clip in two at the playhead', '在播放头处把片段分成两段'), key: `B / ${modKey}B`,
            why: !split.disabled ? '' : busy ? t('This part is generating', '这一段正在生成') : t('Put the playhead inside a clip first', '先把播放头放到片段中间')});
    }
    function updateZoom() {
        const zoomBar = zoomHost.querySelector('.fv-ed-track-zoom'), slider = zoomBar?.querySelector('input');
        if (slider) {
            const controls = zoomBar.querySelectorAll('button');
            controls[0].disabled = state.zoom <= Number(slider.min); controls[1].disabled = state.zoom >= Number(slider.max);
            controls[2].disabled = !model.duration(state.record) && state.selection?.kind !== 'range';
            tip(controls[0], {name: t('Zoom out', '缩小'), use: t('Show more of the timeline', '时间线显示得更长'), why: controls[0].disabled ? t('Already at the widest view', '已经缩到最小') : ''});
            tip(controls[1], {name: t('Zoom in', '放大'), use: t('Show the timeline in more detail', '时间线看得更细'), why: controls[1].disabled ? t('Already at the closest view', '已经放到最大') : ''});
            tip(controls[2], {name: t('Fit all', '看全部'), use: t('Fit the whole edit in view', '整条剪辑放进当前宽度'), why: controls[2].disabled ? t('The timeline is empty', '时间线是空的') : ''});
            tip(slider, {name: t('Track zoom', '轨道缩放'), use: t('Drag to show more or less time', '拖动调整时间线的疏密')});
        }
    }
    function updateTransport() {
        const clips = selectedContent(), busy = clips.some(isBusy);
        toolUndo.disabled = !state.canUndo; toolRedo.disabled = !state.canRedo;
        const named = (verb, label) => label ? verb + t(': ', '：') + label : verb;
        tip(toolUndo, {name: named(t('Undo', '撤销'), state.undoLabel), use: t('Take back the last edit', '退回上一步改动'), key: `${modKey}Z`,
            why: toolUndo.disabled ? t('Nothing to undo yet', '还没有可以撤销的改动') : ''});
        tip(toolRedo, {name: named(t('Redo', '重做'), state.redoLabel), use: t('Bring back the edit just undone', '恢复刚撤销的改动'), key: `${modKey}⇧Z / ${modKey}Y`,
            why: toolRedo.disabled ? t('Nothing to redo', '没有可以重做的改动') : ''});
        updateSplit();
        const deletion = deletionStatus(state.record, state.selection);
        remove.disabled = !deletion.ok;
        tip(remove, {name: t('Delete', '删除'), use: t('Remove the selection and leave its gap', '删掉选中的内容，留下空隙'), key: 'Delete',
            why: remove.disabled ? (deletion.reason === 'busy' ? t('This part is generating', '这一段正在生成') : deletion.reason === 'locked' ? t('This track is locked; unlock it first', '这条轨道已锁定，先解锁') : t('Select a clip first', '先选中一个片段')) : ''});
        const gaps = removableGaps(state.record, state.selection); closeGap.disabled = !gaps.length || !removeGaps(state.record, state.selection).removed;
        tip(closeGap, {name: t('Remove gaps', '移除空隙'), use: t('Pull the clips together over empty space', '把片段之间的空白收拢'), key: t('Delete on a selected gap', '选中空隙后按 Delete'),
            why: closeGap.disabled ? t('There is no gap that can close', '没有可以收拢的空隙') : ''});
        const multiple = isMultiple(state.record, clips.map(clip => clip.id));
        previous.disabled = busy || multiple || !clips.some(clip => clip.versions.length);
        const versioned = clips.find(clip => clip.versions.length), list = versioned ? model.takes(versioned) : [];
        const position = list.findIndex(item => item.index === -1) + 1;
        tip(previous, {name: t('Previous version', '上一版'), use: list.length > 1 ? t(`Step back to an earlier take (now take ${position} of ${list.length})`, `换到更早生成的一版（现在是第 ${position}/${list.length} 版）`) : t('Swap this part back to its earlier take', '把这一段换回之前生成的版本'),
            why: !previous.disabled ? '' : busy ? t('This part is generating', '这一段正在生成') : multiple ? t('Select only one part', '只选一段') : t('This part has no earlier take', '这一段没有更早的版本')});
        updateSecondPass();
        updateZoom();
    }
    updateTransport();
    const stopSources = store.subscribeSources(() => { player.refresh(); tracks?.refresh?.(); pictureTools.update(); updateTop(); updateSecondPass(); poster.update(); });
    let capabilitySize = `${state.record.width}x${state.record.height}`;
    // The reason, said as soon as the selected continuation follows a video that already has the most parts, once per selection.
    let hintedFor = '';
    function hintFullSource() {
        const ids = state.selection?.kind === 'clip' ? state.selection.ids : [];
        const signature = ids.join(',');
        if (!ids.length) { hintedFor = ''; return; }
        if (signature === hintedFor) return;
        try {
            const known = store.knownLineage();
            if (model.partsProblem(state.record, ids, {sourceLineage: known})?.code !== 'chain_too_long'
                || !model.generationBefore(state.record, ids).some(key => known[key]?.parts >= model.RULES.maxParts)) return;
        } catch { return; }
        hintedFor = signature; genbar?.say(partsLimitText('chain_source_full', t));
    }
    // Deleting asks nothing, so it is undone in one press from the notice it leaves (E17).
    const deleteLabels = new Set([t('Delete', '删除'), t('Ripple delete', '波纹删除'), t('Cut', '剪切')]);
    const unsubscribe = state.subscribe(event => {
        poster.update();
        // An undo replays the label too; only a fresh delete (nothing left to redo) leaves the notice.
        if (event.type === 'record' && deleteLabels.has(event.label) && !state.canRedo && !initializing) {
            const step = state.step;
            notify(event.label === t('Cut', '剪切') ? t('Cut to the clipboard.', '已剪切到剪贴板。') : t('Deleted.', '已删除。'), null, null,
                {label: t('Undo', '撤销'), key: `${modKey}Z`, use: t('Bring the clip back', '把删掉的内容恢复'), run: () => { if (state.step === step) travel(true); else notify(t('Other edits came after it; use Undo in the tool row.', '之后又有改动，请用工具行的撤销。')); }});
        }
        if (event.type === 'selection') hintFullSource();
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
            completeSources().catch(value => error(value, null, undefined, 'sources')); recoverJobs(); music.recover(); narration.recover();
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
        if (genbar?.dismiss(event)) return true;
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
        // ⇧←/→ moves ten frames, as Shift on the frame buttons and on the timeline does; ←/→ alone one frame.
        else if (key === 'arrowleft' || key === 'arrowright') { event.preventDefault(); const stride = event.shiftKey ? 10 : 1; state.seek(Math.max(0, state.playhead + (key === 'arrowright' ? stride : -stride))); }
        else if (mod && key === 'z') { event.preventDefault(); travel(!event.shiftKey); }
        else if (mod && key === 'y' && !event.shiftKey) { event.preventDefault(); travel(false); }
    }
    listen('keydown', keyboard); listen('keyup', event => event.stopPropagation());
    // The shell turns every Escape in a view into a cancel (shell.js), and for a real key press that cancel arrives before
    // the editor's own key handling. One Escape does one thing: it closes a popup, or stops a drag, or deselects; the
    // first to use it marks it taken for the rest of that key press, so a drag stopped by Escape adds no undo step.
    let escapeTaken = false;
    const tookEscape = () => { escapeTaken = true; setTimeout(() => { escapeTaken = false; }, 0); };
    listen('cancel', event => {
        if (event.target !== element) return;
        event.preventDefault();
        if (escapeTaken || topDialog() !== element) return;
        if (dismissPopup(event) || tracks?.cancelGesture()) { tookEscape(); return; }
        state.select(null, {history: true});
    });
    const stopKeys = isolateDialogKeys(element, (event, inside) => {
        // FreeVideo's shared menus (a text box's Cut/Copy/Paste, and any menu opened over the editor) live inside this
        // dialog and listen through dialog_keys: their arrows, Enter, Esc and Tab are theirs, with no editor shortcut.
        if (inside && event.target.closest?.('.fv-cm[role=menu]')) { routeDialogKey(event, element); return; }
        if (guide?.handleKey(event)) return;
        if (event.type === 'keyup') {
            // The Space that played from a media card does not also press the card.
            if (event.key === ' ' && inside && event.target.closest?.('.fv-ed-library') && !isTyping(event.target) && !event.target.closest?.('button:not(.fv-ed-source), [role=tab]')) event.preventDefault();
            transportControls.keyboard(event); return;
        }
        if (event.type !== 'keydown') return;
        if (event.isComposing || event.keyCode === 229) return;
        if (event.key.toLowerCase() === 'escape') {
            if (escapeTaken) { event.preventDefault(); return; }
            if (dismissPopup(event)) { tookEscape(); return; }
            if (tracks?.cancelGesture()) { event.preventDefault(); tookEscape(); return; }
            if (document.fullscreenElement) { event.preventDefault(); tookEscape(); document.exitFullscreen().catch(() => {}); return; }
        }
        if (recording.keyboard(event)) return;
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
            if (inLibrary) {
                // A media card keeps the focus after a click or a drag: Space still plays and pauses, and undo and redo still work.
                const key = event.key.toLowerCase(), mod = event.ctrlKey || event.metaKey;
                if (isTyping(event.target) || initializing || event.altKey) return;
                // Space on a media card plays (the card keeps the focus after a click or a drag); other buttons keep their own Space.
                if (key === ' ' && !mod && !event.shiftKey) { if (event.target.closest?.('button:not(.fv-ed-source), [role=tab]')) return; event.preventDefault(); player.toggle(); }
                else if (mod && key === 'z') { event.preventDefault(); travel(!event.shiftKey); }
                else if (mod && key === 'y' && !event.shiftKey) { event.preventDefault(); travel(false); }
                return;
            }
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
    // A page kept for going back (bfcache) keeps its history, so its takes stay.
    const leaving = event => { player.pause(); rememberView(); if (queue.dirty) queue.flush(); if (!event?.persisted) releaseTakes({keepalive: true}); };
    window.addEventListener('pagehide', leaving);
    // A workflow without FreeVideo nodes came to the front: the editing view makes way (unsaved edits stay open).
    const collapse = () => { void handle.close(); };
    window.addEventListener('freevideo-collapse', collapse);
    const handle = {element, state, actions, store, adjust, audio, music, notify,
        async switchView(view) {
            if (closing || disposed || view === 'editor') return;
            leaveTo = view;
            // A refusal answers false, so the switch stops waiting and the bar shows this view again at once.
            if (await handle.close() === false) return false;
            await onView(view);
        },
        async close() {
            if (handle.kept) return true;
            if (closePromise) return closePromise;
            textTools.flush();
            guide?.suspend();
            closing = true; element.inert = true; player.pause();
            closePromise = (async () => {
                // An edit still being read is kept as it is and goes on loading out of sight.
                if (!loaded) { hide(); return true; }
                // A server that does not answer never leaves the view frozen: each wait has a limit, and after it the
                // unsaved-changes card (or leaving, when the changes are kept on this computer) takes over.
                await within(handle.ready, 8000);
                // A Generate still being sent finishes first; say so, so the wait never looks like a frozen view.
                if (generationDone) { notify(t('Sending the generation first, then switching…', '正在发送生成请求，发送后切换…')); element.dataset.waiting = 'true'; await within(generationDone, 15000); delete element.dataset.waiting; }
                const slow = setTimeout(() => notify(t('Saving the changes, then switching…', '正在保存改动，保存后切换…')), 600);
                releaseStamp(); await within(queue.flush(), 6000); clearTimeout(slow);
                if (queue.dirty && !queue.locallyRetained && !discardOnSwitch) {
                    closing = false; element.inert = false; closePromise = null;
                    saveWarningText.textContent = t('Changes are not saved yet. Staying here keeps retrying.', '改动尚未保存，留在此页将继续重试。');
                    saveWarning.hidden = false; updateSaveState(); saveAgain.focus({preventScroll: true}); return false;
                }
                // The view is kept (hidden, not rebuilt): coming back shows it as it was. Only leaving without saving and
                // a saved edit left empty end it (the empty one is removed, as before).
                if (!discardOnSwitch && !(state.record.id && !queue.dirty && emptyEdit())) { rememberView(); hide(); return true; }
                if (!queue.dirty && emptyEdit()) {
                    try { await discardProject({automatic: true}); }
                    catch (value) {
                        notify(deleteEditText(t)[value?.reason] || deleteEditText(t).delete_failed, EDITOR_ISSUE, failureKind('delete', {reason: deleteReason(value)}));
                        closing = false; element.inert = false; closePromise = null; return false;
                    }
                }
                await store.activateProject(null).catch(() => {});
                teardown();
                if (returnFocus?.isConnected) returnFocus.focus({preventScroll: true});
            })();
            return closePromise;
        },
        // Hidden and kept: the dialog closes (its sheet keeps a closed editor out of sight) and turns inert; saving,
        // progress and the job watchers go on, so coming back needs nothing redrawn. The import list waits (it is
        // read again on coming back), unless an import it waits for is still on its way.
        kept: false, panel,
        reopen() {
            handle.kept = false;
            // A blank edit of this tab that nothing was done in follows the creation panel's quality as it is now: a level
            // chosen there while this view was away applies here too, as one chosen here goes there. An edit with parts,
            // a saved one, or one with a part queued or generating keeps its own settings. The blank edit stays unsaved.
            const now = panel.sampling?.(), set = state.record.settings || {};
            if (now && !state.record.id && state.record.tracks.every(track => !track.clips.length) && !held.length && !jobActive()
                    && Object.entries(now).some(([key, value]) => set[key] !== value)) {
                panel.settings = {...panel.settings, ...now};
                suppressSave = true; try { state.settle(record => model.setSampling(record, now)); } finally { suppressSave = false; }
                savedContent = content(state.record);
            }
            resumeCanvasPointer = suspendCanvasPointer(app?.canvas);
            // What the last switch left on the dialog (its fade, the leaving mark) goes, so it shows as it was.
            element.getAnimations?.().forEach(animation => animation.cancel());
            delete element.dataset.closing; delete element.dataset.fvLeaving;
            // Keys go to the last open dialog in page order: move it after one opened since (moving resets scrolling, so only then).
            const later = [...document.querySelectorAll('dialog[open]')].some(dialog => dialog !== element && element.compareDocumentPosition(dialog) & Node.DOCUMENT_POSITION_FOLLOWING);
            if (!element.isConnected || later) document.body.append(element);
            element.inert = false;
            attachBar?.(); library?.resume?.();
            if (!element.open) element.showModal();
            viewChanged('editor'); globalThis.FreeVideoStageLight?.refresh();
            // The tab takes focus after the first frame: focusing now would lay the whole view out a second time before it shows.
            const given = document.activeElement;
            requestAnimationFrame(() => setTimeout(() => {
                if (element.open && !handle.kept && document.activeElement === given) navigation.querySelector('[data-view=editor]').focus({preventScroll: true});
            }));
            return handle;
        },
        dispose() { teardown(); },
    };
    function hide() {
        closing = false; closePromise = null; handle.kept = true;
        player.pause(); player.stopPreview(); lineStop?.();
        // The microphone is let go with the view: an open record bar closes, and a take in progress lands as Stop would.
        recording.hide();
        detachBar?.();
        // The import list is not read while out of sight, unless an import it waits for is still on its way.
        library?.suspend?.();
        if (element.open) element.close();
        element.inert = true;
        resumeCanvasPointer(); resumeCanvasPointer = () => {};
    }
    // Any other view in front (the node view has no dialog of its own) keeps a closed editor out of reach.
    const otherView = event => { if (event.detail?.view !== 'editor' && !element.open) element.inert = true; };
    window.addEventListener('freevideo-view', otherView); listeners.push(() => window.removeEventListener('freevideo-view', otherView));
    function teardown() {
        if (disposed) return;
        releaseTakes(); rememberView(); disposed = true; handle.kept = false; poster.dispose(); music.dispose(); narration.dispose(); voice.dispose(); voiceInstall.dispose(); guide?.dispose(); transportControls.dispose(); toolbarMore.dispose(); player.dispose();
        textTools.dispose(); adjust.dispose(); notes.dispose(); pictureEdit.dispose();
        audio.dispose(); stems?.dispose(); recording.dispose(); headerFit.dispose();
        cleanupBar?.();
        listeners.splice(0).forEach(remove => remove());
        for (const node of [element, ...element.querySelectorAll('*')]) for (const name of ['onclick', 'ondblclick', 'oninput', 'onchange', 'onkeydown', 'ondragstart']) node[name] = null;
        unsubscribe(); stopSources(); library?.dispose(); genbar.dispose(); tracks?.dispose(); stopExport?.();
        progressConnection?.stop(); progressObserver?.disconnect(); runProgress.clear(); failures.clear();
        stopKeys();
        resumeCanvasPointer();
        window.removeEventListener('pagehide', leaving); window.removeEventListener('freevideo-collapse', collapse); for (const timer of noticeTimers) clearTimeout(timer);
        for (const stop of jobWatchers.values()) stop(); jobWatchers.clear();
        queue.dispose({discard: discardOnSwitch}); store.dispose(); if (element.open) element.close(); element.replaceChildren(); element.remove(); if (opened === handle) opened = null;
    }
    library = createEditorLibrary(libraryHost, {store, t, actions, state});
    opened = handle; document.body.append(element); element.showModal(); viewChanged('editor'); navigation.querySelector('[data-view=editor]').focus();
    // Every row has its final size from the first frame (fixed rows, placeholders); the shell holds the previous view's
    // picture until the edit itself is drawn (ready, after the timeline has laid out its rows, 120 ms at most for that).
    element.dataset.fvReady = 'false';
    let readyMarked = false;
    const markReady = async () => {
        if (readyMarked) return; readyMarked = true;
        await Promise.all([tracks?.ready ? within(tracks.ready, 120) : null, within(poster.ready, 600)]);
        if (!disposed) element.dataset.fvReady = 'true';
    };
    handle.ready = (async () => {
        const progressComponents = given && Object.hasOwn(given, 'generationStandIn') && Object.keys(given).length === 1 ? null : given;
        // The optional block-progress module is not part of this build; asking for it only produced a 404 on every open.
        const componentsReady = progressComponents || Promise.resolve({blocks: null});
        await editorStyles; if (closing || disposed) return;
        // The timeline draws before the edit is read (its module loads with this one), so the view has every part from
        // the first frames; the edit then arrives as a project switch does. The optional progress component, when a
        // build has one, is used by clips drawn after it arrives.
        const trackResult = (await Promise.allSettled([tracksModule]))[0]; if (closing || disposed) return;
        // A timeline module that cannot load still lets the edit open (and says so once it has).
        const tracksFailure = trackResult.status === 'rejected' ? trackResult.reason : null;
        const module = trackResult.value;
        let components = {};
        Promise.resolve(componentsReady).then(value => { if (!disposed) { components = value || {}; blockStates = components.blocks?.blockStates || null; } },
            reason => error(reason, null, undefined, 'components'));
        const blockProgress = (id, clipElement) => {
            if (!components.blocks?.createBlockProgress) return basicBlockProgress(id, clipElement, t);
            const component = components.blocks.createBlockProgress({t, showPercent: clipElement.dataset.track === 'v1'});
            let run = null;
            return {element: component.element, update(value) {
                if (value.run && value.run !== run) { component.update({state: 'waiting', fraction: 0}); run = value.run; }
                component.element.dataset.run = run;
                component.update(value);
            }, dispose: () => component.destroy()};
        };
        if (module) {
        skeleton.remove();
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
        if (bar) { zoomHost.append(bar); updateZoom(); }
        for (const [id, value] of progress) tracks.setProgress(id, value);
        }
        let record = null, key = null, lastMissing = false;
        try {
            await store.recoverOutbox(); if (closing || disposed) return;
            if (from?.work) {
                const work = from.work;
                const id = work.id || work.video?.replace(/^FreeVideo\//, '').replace(/\/video\.mp4$/, '');
                void store.getSources([`work:${id}`]).then(() => {
                    const source = store.sources.get(`work:${id}`);
                    if (source?.path) void store.lineage([{key: `work:${id}`, reference: {work: source.path}}]);
                }).catch(() => {});
                record = await store.byWork(id);
                if (!record) {
                    const key = `work:${id}`; await store.getSources([key]); const metadata = store.sources.get(key), g = work.geometry || {};
                    record = model.addAudioTrack(model.emptyProject({width: even(metadata?.width || g.width || panel.width || 1344), height: even(metadata?.height || g.height || panel.height || 768),
                        settings: panel.settings || null, name: t('Untitled edit', '未命名剪辑')}));
                    record = model.insertSource(record, {source: {kind: 'work', id}, frames: metadata?.frames || g.frames || Math.round((g.seconds || 15) * 24),
                        hasVideo: true, hasAudio: metadata?.has_audio ?? work.has_audio !== false, text: metadata?.text, at: 0});
                    if (!metadata) store.cache({[key]: {video: mediaURL(api, work.video), audio: mediaURL(api, work.video), frames: g.frames,
                        has_video: true, has_audio: true, path: work.video, missing: false, thumbnail: '/freevideo/library/thumbnail?' + new URLSearchParams({id})}});
                }
            } else {
                let id; try { id = optionalText(localStorage.getItem('fv-editor-last')); } catch {}
                let draft; try { draft = globalThis.sessionStorage?.getItem('fv-editor-draft'); } catch { draft = lastDraft; }
                // A browser that never opened an edit (a fresh profile) opens the latest saved edit with something in it.
                const rows = !id && !draft?.startsWith('draft:') ? await store.projects().catch(() => []) : null; if (closing || disposed) return;
                const choice = openingChoice({last: id, draft, rows});
                if (choice.kind === 'draft') key = draft;
                else if (choice.kind === 'last') {
                    record = await store.load(id);
                    // The last edit is gone (deleted or moved); say so once instead of opening a blank edit silently.
                    if (record) key = id;
                    else { lastMissing = true; try { localStorage.removeItem('fv-editor-last'); } catch {} }
                } else if (choice.kind === 'latest') {
                    record = await store.load(choice.id);
                    if (record) key = choice.id;
                }
            }
        } catch (value) { error(value, null, undefined, 'open-load'); }
        if (closing || disposed) return;
        try {
            // A cached copy never stands in for an edit the server could not give back.
            await replaceProject(record || (fromCache ? initial() : state.record), key);
            if (from?.work && !state.record.id) queue.enqueue(state.record);
        } catch (value) { error(value, null, undefined, 'open-session'); }
        if (closing || disposed) return;
        initializing = false; savedContent = content(state.record); player.refresh(); delete element.dataset.opening;
        loaded = true; settleGenbar();
        updateTop(); updateTransport(); void markReady();
        if (lastMissing && !state.record.id) {
            // Point at the name menu only when it lists another edit to open.
            store.projects().catch(() => ({})).then(rows => {
                if (closing || disposed) return;
                const others = Array.isArray(rows) && rows.some(row => row.id !== state.record.id);
                notify(t('The last edit could not be found, so a blank edit is open.', '找不到上次的剪辑，已打开空白剪辑。')
                    + (others ? t(' Click the edit name at the top to open another edit.', '点击顶部的剪⁠辑⁠名可打开其他剪辑。') : ''));
            });
        }
        if (tracksFailure) throw tracksFailure;
        if (from?.work && from.extend) {
            const id = from.work.id || from.work.video?.replace(/^FreeVideo\//, '').replace(/\/video\.mp4$/, '');
            const epoch = projectEpoch;
            const showFullSourceHint = () => {
                const key = `work:${id}`;
                void store.getSources([key]).then(() => {
                    const metadata = store.sources.get(key);
                    return metadata?.path ? store.lineage([{key, reference: {work: metadata.path}}]) : null;
                }).then(values => {
                    if (closing || disposed || epoch !== projectEpoch) return;
                    if (!values) return;
                    const history = values.get(key);
                    if (!history || history.known !== true || history.parts < model.RULES.maxParts) return;
                    const clips = state.record.tracks[0].clips, source = clips.find(clip => sourceKey(clip.source) === key);
                    if (!source) return;
                    const after = clips.filter(clip => clip.pending === 'draft' && clip.start >= source.start + source.frames
                        && model.continuesBefore(state.record, clip.id));
                    if (!after.length) return;
                    const problem = model.partsProblem(state.record, after.map(clip => clip.id), {sourceLineage: store.knownLineage()});
                    if (problem?.code === 'chain_too_long') genbar?.say(partsLimitText('chain_source_full', t));
                }).catch(() => {});
            };
            // Selects the work and, when nothing follows it yet, adds one minimum draft. Runs once; when the
            // generation probe failed this time it runs after a later probe succeeds.
            const enter = select => enterClip(clip => clip.source?.kind === 'work' && clip.source.id === id, epoch, {select, settled: showFullSourceHint});
            await store.generationReady();
            if (!enter() && !closing && !disposed) extendEntry = () => { if (enter(false) || closing || disposed) extendEntry = null; };
        }
    })().catch(value => error(value, null, undefined, 'init')).finally(() => {
        // Every exit (including close and partial failure) settles readiness.
        // After a completed start the saved baseline is already set; edits made since must stay unsaved.
        if (initializing) { initializing = false; savedContent = content(state.record); }
        delete element.dataset.opening;
        title.disabled = false; toolbar.inert = false; loaded = true; settleGenbar();
        tracksHost.inert = !tracks;
        for (const host of [tracksHost, toolbar]) host.classList.remove('fv-ed-pending');
        if (!closing && !disposed) { player.refresh(); updateTop(); updateTransport(); element.dataset.fvLoaded = 'true'; }
        void markReady();
    });
    if (typeof MutationObserver !== 'undefined') guide = createFirstRunGuide({dialog: element, api,
        cn: CN, group: 'editor',
        onShowcaseTry: async () => { if (await handle.close() !== false) await onView('autopilot', {initialScene: SHOWCASE_SCENE}); },
        isBusy: () => initializing || !tracks || genHost.inert || pointerEditing || jobActive() || ['running', 'queued'].includes(exportStatus?.state),
        targets: {'editor-trim': () => tracks?.guideTarget('trim'), 'editor-empty': () => tracks?.guideTarget('empty')}});
    element.freevideoSwitchView = view => handle.switchView(view);
    updateTop(); return handle;
}
