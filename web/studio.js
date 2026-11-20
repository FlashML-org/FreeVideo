import { cutTip } from './cut_tip.js';
import './has_mirror.js';
import {createLanAccess} from './lan_access.js';
import { createErrorPanel, errorText, createErrorReport } from './error_panel.js';
import {createExpandButton} from './editor_expand.js';
import {allowEmptyPrompt, cleanOptionalPrompt, clonePrompt} from './optional_inputs.js';
import {showEmptyPrompt, dismissEmptyPrompt} from './empty_prompt_notice.js';
import { app } from '../../scripts/app.js';
import { api } from '../../scripts/api.js';
import { openSetup } from './setup.js';
import { feedbackButton, guideButton, feedbackIssueURL } from './feedback.js';
import { wordmark } from './wordmark.js';
import { createGenerationProgress, recordReportId, referenceTrimText, thermalText, sharedGpuText, keepSoundText, encoderFileText, encoderFilePath } from './generation_progress.js';
import { viewSwitch, viewChanged, agentTabReady, openAutopilot, openImage } from './view_navigation.js';
import { fitStudioHeader } from './studio_header_fit.js';
import { agentInterfaceState } from './agent_connect.js';
import { createUpdateNotice, createVersionInfo, productVersion } from './updates.js';
import { createPortNotice } from './port_notice.js';
import { focusReportTip, hideReportTip, reportIssue, reportTipClosed, showReportTip } from './report_issue.js';
import { issueButton } from './issue_button.js';
import { skinSelect } from './fv_select.js';
import { loraMenu } from './lora_menu.js';
import { createPreviewScene } from './preview_scene.js?v=20261115-still';
import { animateDetails, closeDialog } from './motion.js';
import { openLibrary, setDefaultUpscale, setDefaultUpscaleWork, videoThumbnailURL } from './library.js';
import { createRecentVideos } from './studio_recent.js';
import { createStudioQueue, randomSeed, turnNodeOn } from './studio_queue.js';
import { isNetworkTypeError, connectionLostText, wrappedFetchOnly } from './browser_extension.js';
import { newEngineExit, engineExitText, watchEngineExit } from './engine_exit.js';
import { attachReferencePicker, referenceItems, enhancementMedia, syncReferencePrompt, referenceInputs, mentionAt, handleNames, mediaRows } from './prompt_references.js';
import { attachTermSuggest } from './prompt_terms.js';
import { problem as timedProblem, timedText, parse as timedParse } from './timed_prompt.js';
import { castInRows, expandCast, mentionHandle, mentionState, mentionedIds, restoreCast, withCharacter, MAX_PICTURES_EACH } from './cast_mention.js';
import { attachMentionOverlay } from './cast_overlay.js';
import { openCastLibrary } from './cast_source.js';
import { uploadMedia } from './media_upload.js';
import { reconcileLoraTriggers, promptLines } from './lora_triggers.js';
import { createVerdicts, describeVerdict, feedbackDraft } from './lora_check.js';
import { continuationUpscale, createSamplingEffort, effortFor, effortsFrom, passSwitch, stepCacheOf, upscaleOf } from './sampling_effort.js';
import { resultActions, previewBadge, icon, actionsUnavailable, liftMenu } from './result_actions.js';
import { TIPS } from './tooltip_copy.js';
import { createFrameChip, createFrameIcon, createPopChip } from './glass/frame_picker.js';
import { createWarm } from './studio_warm.js';
import { attach as attachPlayer } from './glass/player_bar.js';
import { framesOf, savedPrompt } from './result_reuse.js';
import { addVideoLabel, addVideoModel, missingVideoText, videoModelState } from './video_model.js';
import { createPromptEnhancer } from './prompt_enhance.js';
import { requestText, shownRewrite } from './prompt_sections.js';
import { createRequestAssets, enabledLoras, referenceFiles } from './request_assets.js';
import { promptDraftTrouble, rememberPromptDraft, restorePromptDraft, savePromptDraft, trackPromptDraft, watchPromptDraftTrouble } from './prompt_draft.js';
import { canvasDimensions, imageSizeSelection } from './studio_size.js';
import { createCapabilityView, capabilityContext } from './capability_view.js';
import { createOtherUsageNotice } from './other_usage.js';
import { openEditorView } from './editor_loader.js';
import { frameShapes, frameSize } from './frame_sizes.js';
// The creation panel's frames: the shared three, then the other common ratios (named by their ratio).
const studioShapes = t => [...frameShapes(t), ...[[4, 3], [3, 4], [3, 2], [2, 3], [21, 9]].map(([a, b]) => [`${a}:${b}`, `${a}:${b}`, a / b])];
import { createFirstRunGuide, createFirstStart } from './first_run_guide.js';
import { SHOWCASE_SCENE } from './entry_showcase.js';
import { registerTourWorkspace, triggerFeatureTour, offerVersionTour } from './feature_tour.js';
import { createStudioLength, savedSeconds } from './studio_length.js?v=20261011-length';
import { firstFrameRow } from './first_frame_row.js';
import { createReplacePerson, swapMissingText } from './replace_person.js';
import { swapState, swapMissing, effectiveRows, prepareSwapLora, releaseSwapLora, peopleOf } from './replace_state.js';
import { swapLoraDownload } from './swap_lora.js';
import { createStudioChain, chainPosition, chainParts, chainSplit, composePartText, readChainResult, sumPartEstimates, singleRunSnapshot } from './studio_chain.js?v=20261117-chain-errors';
import { activeOriginal, checkOriginal, lockMark, blockOriginal, originalBlocked, usesOriginal, referenceRow, partsNeedTwoPassText } from './audio_original.js';
import { joiningText, waitingText } from './chain_support.js';
import { createExtendState, extendText, extendSourceProblem, extendSourceState, readExtendRecord, extendSnapshot, mountExtendThumbnail, videoUnavailableText } from './studio_extend.js';
import { topDialog, isolateDialogKeys, listenDialogKey, routeDialogKey } from './dialog_keys.js';
import { createPassMode } from './pass_mode.js';
import { createConsole, durationStepper, tip, glyph } from './studio_console.js';
import { createStageBar } from './studio_stage.js';
import { confirmStop, startJobSources } from './task_float.js';
import { resultEffort, effortName } from './sampling_effort.js';
import { createPromptLibrary, rememberPrompt } from './prompt_library.js';
import { createCompare, COMPARE_ICON } from './studio_compare.js';
import { toast, toastHeight } from './notifications.js';
import { mediaQueue } from './work_media.js';
import { userMessage } from './user_message.js';
import { fieldNote } from './field_note.js';

const languageOverride = typeof location !== 'undefined'
    ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en'
    && String(navigator.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;
// A dropped connection gets one plain sentence, not the browser's "Failed to fetch".
const shownMessage = error => isNetworkTypeError(error) ? connectionLostText(t) : error.message;
// The usual LoRA strength range is -4 to 4; a value the user types by hand may go further, up to 100.
const LORA_USUAL = 4, LORA_LIMIT = 100;
let loraRangeCount = 0;
// `said` ({text}) is the line under the box that outlives the list being drawn again: every change writes the list, which
// rebuilds its rows, so the row remembers what its line said and shows it again until the next edit.
function loraStrengthBox(initial, commit, said = null) {  // said: {text, value, name}
    const input = document.createElement('input'), mark = document.createElement('span');
    input.type = 'number'; input.step = '.05'; input.min = String(-LORA_LIMIT); input.max = String(LORA_LIMIT); input.value = initial;
    const note = fieldNote(input, {t, place: () => mark.isConnected ? mark : null});
    mark.id = 'fv-lora-range-' + ++loraRangeCount; input.setAttribute('aria-describedby', mark.id);
    mark.className = 'fv-lora-range'; mark.textContent = t('Outside usual range', '超出常用范围');
    const sync = () => {
        const number = Number(input.value), filled = input.value !== '';
        mark.hidden = !(filled && Math.abs(number) > LORA_USUAL && Math.abs(number) <= LORA_LIMIT);
    };
    // A strength past ±100 becomes ±100, and the line under it says so.
    // An emptied box goes back to the strength in use, and the line says so.
    let kept = String(initial);
    input.oninput = () => { if (said) said.text = ''; sync(); }; input.onchange = () => {
        if (input.value === '' && kept !== '') { input.value = kept; sync(); note.say(t(`Back to ${kept}`, `已恢复为 ${kept}`)); mark.hidden = true; return; }
        const ok = note.settle(); sync();
        if (said) { said.text = note.line && !note.line.hidden ? note.line.textContent : ''; said.value = Number(input.value); }
        if (note.line && !note.line.hidden) mark.hidden = true; // one line under the box: this one replaces the range note until the next input
        if (ok) { kept = input.value; commit(); }
    };
    sync();
    return {input, mark, restore: () => { if (said?.text && said.value === Number(input.value)) { note.say(said.text); mark.hidden = true; } }};
}
const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('./studio.css', import.meta.url).href; document.head.append(css);
const effortCss = document.createElement('link'); effortCss.rel = 'stylesheet'; effortCss.href = new URL('./sampling_effort.css', import.meta.url).href; document.head.append(effortCss);
const enhanceCss = document.createElement('link'); enhanceCss.rel = 'stylesheet'; enhanceCss.href = new URL('./prompt_enhance.css', import.meta.url).href; document.head.append(enhanceCss);
// The glass layer of the creative panel loads last, so it settles every rule above for dialog.fv-create.
const glassCss = document.createElement('link'); glassCss.rel = 'stylesheet'; glassCss.href = new URL('./glass/studio.css', import.meta.url).href;
// The panel reports ready only once this layer applies, so its first frame is never laid out without it.
const glassReady = new Promise(resolve => { glassCss.addEventListener('load', resolve, {once: true}); glassCss.addEventListener('error', resolve, {once: true}); setTimeout(resolve, 3000); });
document.head.append(glassCss);
const el = (tag, label, cls) => { const e = document.createElement(tag); if (label != null) e.textContent = label; if (cls) e.className = cls; return e; };
// One line per part of a long video, under the preview. A part that has not started opens in
// place for editing and saves when it loses focus; the server's reply decides what stays.
// After the video stops, an ended part can be made again from there on as a new video.
function createChainPartList(t, save, {onShow = () => {}, onHide = () => {}, redo = async () => {}, resume = async () => {}, onError = () => {}, playSeam = null} = {}) {
    const element = el('div', null, 'fv-chain-parts'); element.hidden = true;
    const rows = new Map(), timers = new Map();
    let editing = null, showing = null;
    function tag(row, text, kind = '') { row.tag.textContent = text; row.tag.dataset.kind = kind; }
    function removeEditor(row) {
        row.expand?.dispose(); row.expand = null;
        row.editBox?.remove(); row.editBox = row.editor = null; row.draft = null;
        row.text.hidden = false;
    }
    function draw(row) {
        const {part} = row, writing = editing === row;
        row.element.dataset.state = part.editable ? 'pending' : part.state;
        row.text.textContent = part.text;
        // A part waits for its save to come back before it can be edited again, so two saves never cross.
        row.text.disabled = !part.editable || row.saving != null;
        if (!writing && row.editor) removeEditor(row);
        // A note about a lost edit stays on its line until that part is done.
        if (row.tag.dataset.kind === 'error' && part.state === 'done') row.tag.dataset.kind = '';
        // In the joined video a continuing part plays its seam: two seconds either side.
        const seam = playSeam && part.seam != null;
        row.meta.disabled = !seam; row.meta.dataset.seam = String(seam);
        if (seam) row.meta.setAttribute('aria-label', t(`Play the seam before part ${part.index + 1}`, `播放第 ${part.index + 1} 段的接缝`));
        else row.meta.removeAttribute('aria-label');
        // One way on per part: a stopped video continues from its first unfinished part; a finished part makes a new video.
        const action = part.action, number = part.index + 1;
        row.redo.hidden = !action;
        if (action) {
            row.redo.textContent = action === 'resume' ? t('Continue from here', '从这段继续') : t('Redo from here', '从这段重新生成');
            row.redo.setAttribute('aria-label', action === 'resume' ? t(`Continue from part ${number}`, `从第 ${number} 段继续`)
                : t(`Redo from part ${number}`, `从第 ${number} 段重新生成`));
        }
        if (timers.has(part.id) || row.tag.dataset.kind === 'error' || row.starting) return;
        const ended = {done: t('Done', '已完成'), failed: t('Failed', '失败'), cancelled: t('Stopped', '已停止'), interrupted: t('Stopped', '已停止')}[part.state];
        tag(row, ended ?? (part.live && !part.editable && ['queued', 'running', 'pending'].includes(part.state) ? t('Generating', '生成中') : ''),
            part.state === 'failed' ? 'failed' : '');
    }
    async function remake(row) {
        if (row.starting) return;
        // The click answers at once; the new video replaces this list when the server takes it.
        row.starting = true; row.redo.disabled = true; tag(row, t('Starting…', '正在开始…'));
        try { await (row.part.action === 'resume' ? resume() : redo(row.part.id)); }
        catch (error) {
            if (error.status === 409) {
                if (rows.get(row.part.id) === row) flash(row, t('Another video is generating. Try again when it finishes.', '正在生成别的视频，完成后再试。'), 'error');
                return;
            }
            // The line says the server's own sentence when it gave one; the card carries what failed and a report.
            const said = (error.said || error.cause?.said);
            if (rows.get(row.part.id) === row) flash(row, said ? t(said[0], said[1])
                : t('Could not start. See the details on the picture.', '没能开始，原因见画面上的说明。'), 'error');
            onError(error);
        } finally { row.starting = false; row.redo.disabled = false; }
    }
    function flash(row, text, kind) {
        clearTimeout(timers.get(row.part.id)); tag(row, text, kind);
        if (kind === 'saved') timers.set(row.part.id, setTimeout(() => { timers.delete(row.part.id); tag(row, ''); draw(row); }, 2000));
    }
    async function finish(row, keep) {
        if (editing !== row) return;
        row.expand?.commit();
        const before = row.part.text, value = row.editor.value;
        editing = null;
        if (!keep || !value.trim() || value === before) { draw(row); return; }
        row.part = {...row.part, text: value}; row.saving = value; draw(row);
        try {
            await save(row.part.id, value);
            row.saving = null;
            if (rows.get(row.part.id) === row) { draw(row); flash(row, t('Saved', '已保存'), 'saved'); }
        } catch (error) {
            row.saving = null;
            if (rows.get(row.part.id) !== row) return;
            row.part = {...row.part, text: before}; draw(row);
            flash(row, error.status === 409 ? t('This part is already generating. Your edit was not saved.', '这一段已在生成，改动没有保存')
                : t('Your edit was not saved.', '改动没有保存'), 'error');
        }
    }
    function open(row) {
        if (!row.part.editable || row.saving != null || editing === row) return;
        if (editing) finish(editing, true);
        editing = row; clearTimeout(timers.get(row.part.id)); timers.delete(row.part.id); tag(row, '');
        const editor = row.editor = el('textarea', null, 'fv-chain-part-editor');
        editor.maxLength = 20000; editor.value = row.draft = row.part.text;
        editor.setAttribute('aria-label', t(`Part ${row.part.index + 1} description`, `第 ${row.part.index + 1} 段描述`));
        editor.addEventListener('input', () => { row.draft = editor.value; });
        listenDialogKey(editor, 'keydown', event => {
            // Escape leaves the edit, never the studio.
            if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(row, false); row.text.focus(); }
        });
        const box = row.editBox = el('div', null, 'fv-chain-part-edit');
        row.expand = createExpandButton({t, textarea: editor, canGenerate: () => false,
            // Like the main description, keep a live draft while preserving the small box's undo history.
            onDraft: text => { row.draft = text; }});
        box.append(editor, row.expand.element);
        box.addEventListener('focusout', event => {
            if (!row.expand?.isOpen && !box.contains(event.relatedTarget)) finish(row, true);
        });
        row.text.hidden = true; row.text.after(box); editor.focus();
        editor.setSelectionRange(editor.value.length, editor.value.length);
        editor.scrollIntoView({block: 'nearest'});
    }
    function clear() {
        if (!element.hidden) onHide();
        editing = null;
        for (const row of rows.values()) removeEditor(row);
        element.hidden = true; element.replaceChildren(); rows.clear(); showing = null;
        for (const id of timers.values()) clearTimeout(id); timers.clear();
    }
    return {element, clear, resumes: () => !element.hidden && [...rows.values()].some(row => row.part.action === 'resume'), update(chain, active, shown = false) {
        // The list belongs to one long video: it shows once the descriptions are written, and stays after the
        // video stops while the preview still shows it.
        if (!(active || shown) || !chain?.split || chainSplit(chain).writing) { clear(); return; }
        if (showing !== chain.id) clear();
        showing = chain.id;
        const first = element.hidden; element.hidden = false;
        const parts = chainParts(chain), ids = new Set(parts.map(part => part.id));
        for (const [id, row] of rows) if (!ids.has(id)) {
            if (editing === row) editing = null;
            removeEditor(row); row.element.remove(); rows.delete(id);
            clearTimeout(timers.get(id)); timers.delete(id);
        }
        parts.forEach((part, index) => {
            part.state = chain.blocks[index].state === 'pending' && chain.blocks[index].prompt_id != null ? 'queued' : chain.blocks[index].state;
            part.live = active;
            let row = rows.get(part.id);
            if (!row) {
                row = {element: el('div', null, 'fv-chain-part'), tag: el('span', null, 'fv-chain-part-tag')};
                row.text = el('button', null, 'fv-chain-part-text'); row.text.type = 'button';
                row.redo = el('button', null, 'fv-chain-part-redo'); row.redo.type = 'button';
                row.redo.onclick = event => { event.stopPropagation(); remake(row); };
                row.element.onclick = () => open(row);
                const joins = part.continues === 'part' ? t('After the previous part', '接上一段') : part.continues === 'video' ? t('After the video', '接原视频') : '';
                row.meta = el('button', [joins, t(`${part.seconds} s`, `${part.seconds} 秒`)].filter(Boolean).join(' · '), 'fv-chain-part-meta');
                row.meta.type = 'button';
                row.meta.onclick = event => { event.stopPropagation(); if (row.part.seam != null) playSeam?.(row.part.seam); };
                row.element.append(el('span', t(`Part ${index + 1}`, `第 ${index + 1} 段`), 'fv-chain-part-name'), row.meta, row.text, row.tag, row.redo);
                rows.set(part.id, row); element.append(row.element);
            }
            if (editing === row && !part.editable) {
                // The part began while it was open: the words typed so far cannot reach it. A video that ended just closes it.
                const changed = row.draft !== row.part.text;
                editing = null; row.part = part; draw(row);
                if (changed && active) flash(row, t('This part is already generating. Your edit was not saved.', '这一段已在生成，改动没有保存'), 'error');
                return;
            }
            if (editing === row) { row.part = {...part, text: row.part.text}; return; }
            // A poll answered before the save shows the words being saved, not the older ones.
            row.part = row.saving != null ? {...part, text: row.saving} : part; draw(row);
        });
        if (first) onShow();
    }};
}
const button = (label, action, cls = '') => { const e = el('button', label, cls); e.type = 'button'; e.onclick = action; return e; };
const widget = (node, name) => node?.widgets?.find(w => w.name === name);
const linked = (node, name) => node?.inputs?.some(input => input.name === name && input.link != null);
// While the editor borrows the bar, edits stay in a shadow copy so the workflow's own widgets are never touched.
let shadow = null;
const value = (node, name) => shadow?.get(node)?.has(name) ? shadow.get(node).get(name) : widget(node, name)?.value;
function set(node, name, value) {
    const w = widget(node, name); if (!w || linked(node, name)) return false;
    if (shadow) { if (!shadow.has(node)) shadow.set(node, new Map()); shadow.get(node).set(name, value); return true; }
    w.value = value; w.callback?.(value); node.graph?.change(); node.setDirtyCanvas?.(true, true);
    rememberPromptDraft(api, node); return true;
}
function upstream(node, name) {
    const input = node.inputs?.find(i => i.name === name);
    const link = input?.link != null ? node.graph.links[input.link] : null;
    return link ? node.graph.getNodeById(link.origin_id) : null;
}
function view(file, type = 'input') {
    const split = file.lastIndexOf('/');
    return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(split + 1), subfolder: split < 0 ? '' : file.slice(0, split), type}));
}
const number = (n, divisor = 1, unit = 's') => Number.isFinite(n) && n >= 0 ? `${(n / divisor).toFixed(1)} ${unit}` : '—';

const LORA_TRIGGERS = 'freevideo_lora_triggers';
// Node and creation panels share lookups and serialize saves for each filename.
const loraTriggers = new Map();
function triggerEntry(name) {
    if (!loraTriggers.has(name)) loraTriggers.set(name, {version: 0});
    return loraTriggers.get(name);
}
// The server's own wording is technical; the panel always says the same plain sentence.
const triggerReadError = () => new Error(t('Could not read trigger words.', '无法读取触发词。'));
const triggerSaveError = () => new Error(t('Could not save. Try again.', '没有保存，请再试一次。'));
async function triggerResponse(response, fail) {
    let result = null;
    try { result = await response.json(); } catch { /* not JSON: same sentence */ }
    if (!response.ok || typeof result?.words !== 'string') throw fail();
    return result;
}
function announceTriggers(name) {
    window.dispatchEvent(new CustomEvent('freevideo-lora-triggers', {detail: name}));
}
function loadTriggers(name) {
    const entry = triggerEntry(name);
    if (entry.data && Date.now() - entry.updated < 60000) return Promise.resolve(entry.data);
    if (entry.loading) return entry.loading;
    const version = entry.version;
    entry.loading = (async () => {
        // The first read of a large file hashes it once on the server; keep asking while that finishes.
        let result;
        for (let attempt = 0; ; attempt++) {
            try {
                result = await triggerResponse(await api.fetchApi('/freevideo/lora/triggers?' + new URLSearchParams({name}),
                    {cache: 'no-store', signal: AbortSignal.timeout(6500)}), triggerReadError);
                break;
            } catch (error) {
                if (error?.name === 'TimeoutError' && attempt < 9) continue;
                throw triggerReadError();
            }
        }
        if (entry.version === version) { entry.data = result; entry.updated = Date.now(); announceTriggers(name); }
        return result;
    })().finally(() => { entry.loading = null; });
    return entry.loading;
}
function saveTriggers(name, words) {
    const entry = triggerEntry(name), version = ++entry.version;
    entry.saving = (entry.saving || Promise.resolve()).catch(() => {}).then(async () => {
        let result;
        try {
            result = await triggerResponse(await api.fetchApi('/freevideo/lora/triggers', {
                method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name, words: words.trim()}),
            }), triggerSaveError);
        } catch { throw triggerSaveError(); }
        if (entry.version === version) { entry.data = result; entry.updated = Date.now(); announceTriggers(name); }
        return result;
    });
    return entry.saving;
}

// Writing a value an element already has still wakes every observer, so only real changes are written.
const put = (target, key, value) => { if (target[key] !== value) target[key] = value; };
const setAttr = (node, key, value) => { if (node.getAttribute(key) !== value) node.setAttribute(key, value); };
export const loraWarning = () => t(
    'FreeVideo needs no acceleration LoRA.',
    'FreeVideo 无需添加加速 LoRA');

// The last three LoRAs chosen, kept in this browser. Names that are no longer in the folder are never offered.
const LORA_RECENT = 'freevideo_lora_recent', LORA_RECENT_MAX = 3;
const recentLoras = () => {
    try { const names = JSON.parse(localStorage.getItem(LORA_RECENT) || '[]'); return Array.isArray(names) ? names.filter(n => typeof n === 'string').slice(0, LORA_RECENT_MAX) : []; }
    catch { return []; }
};
const rememberLora = name => {
    if (!name) return;
    try { localStorage.setItem(LORA_RECENT, JSON.stringify([name, ...recentLoras().filter(n => n !== name)].slice(0, LORA_RECENT_MAX))); } catch {}
};
export function loraPanel(node, panel, {promptNode = null} = {}) {
    const list = el('div', null, 'fv-lora-list'), note = el('p', '', 'fv-lora-note');
    const actions = el('div', null, 'fv-lora-add'); panel.append(list, actions, note);
    let names = [], disposed = false, rendering = false, renderAgain = false;
    const drafts = new Map(), errors = new Map(), saves = new Map();
    let skipped = 0;
    const idleNote = () => (skipped ? t(skipped === 1 ? ' 1 other LoRA file in the folder is not in .safetensors format and is not listed.' : ` ${skipped} other LoRA files in the folder are not in .safetensors format and are not listed.`, ` 文件夹中另有 ${skipped} 个 LoRA 文件不是 .safetensors 格式，未列出。`) : '');
    const verdicts = createVerdicts((path, options) => api.fetchApi(path, options));
    let rowChecks = [];
    const strengthSaid = new Map(); // row index -> {text}: the "At most 100" line of its strength box
    let focusGrip = null;
    // The list lives in the node as JSON; a broken list is not a reply from FreeVideo, so it gets its own sentence.
    const listProblem = error => {
        const unreadable = t('The LoRA list cannot be read. Add the LoRAs again.', 'LoRA 列表无法读取，请重新添加 LoRA。');
        return error?.name === 'SyntaxError' ? unreadable : userMessage(error, t, unreadable);
    };
    const read = () => { const rows = JSON.parse(value(node, 'adapters') || '[]'); if (!Array.isArray(rows) || rows.length > 32) throw new Error(t('Use at most 32 LoRAs', '最多使用 32 个 LoRA')); return rows; };
    function syncPrompt(rows) {
        const targets = new Set([promptNode, ...(node.graph?._nodes || []).filter(target =>
            (target.type === 'FreeVideoGenerate' || target.comfyClass === 'FreeVideoGenerate') && upstream(target, 'loras') === node)].filter(Boolean));
        const words = Object.fromEntries(rows.filter(row => triggerEntry(row.name).data)
            .map(row => [row.name, triggerEntry(row.name).data.words]));
        for (const target of targets) {
            if (!widget(target, 'text')) continue;
            const saved = target.properties?.[LORA_TRIGGERS];
            const previous = Array.isArray(saved) ? saved : [];
            const next = reconcileLoraTriggers(value(target, 'text'), previous, rows, words);
            if (linked(target, 'text')) {
                // Never claim an insertion that cannot be written to a linked prompt.
                next.records = next.records.filter(row => previous.some(old => old.name === row.name && old.words === row.words));
            } else if (next.prompt !== value(target, 'text')) {
                set(target, 'text', next.prompt);
                syncReferencePrompt(target, t);
                // Encoded conditioning skips reference remapping, but both text
                // editors still display edits to the unlinked prompt widget.
                if (linked(target, 'conditioning')) window.dispatchEvent(new CustomEvent('freevideo-reference-prompt', {detail: target.id}));
            }
            if (JSON.stringify(previous) !== JSON.stringify(next.records)) {
                (target.properties ||= {})[LORA_TRIGGERS] = next.records;
                target.graph?.change(); target.setDirtyCanvas?.(true, true);
            }
        }
    }
    function refreshTriggerWords(name) {
        if (disposed) return;
        try {
            const rows = read();
            if (!rows.some(row => row.name === name)) return;
            syncPrompt(rows);
            for (const trigger of list.querySelectorAll('.fv-lora-trigger')) {
                if (trigger.dataset.name === name && !drafts.has(name)) trigger.value = triggerEntry(name).data?.words ?? '';
            }
            note.textContent = rows.map(row => errors.get(row.name)).find(Boolean) || idleNote();
        } catch (error) { note.textContent = listProblem(error); }
    }
    const write = rows => { if (set(node, 'adapters', JSON.stringify(rows))) { syncPrompt(rows); window.dispatchEvent(new CustomEvent('freevideo-loras', {detail: node.id})); } };
    // A LoRA the model cannot take is switched off here, so it is not sent with the request.
    function disableUnusable(name) {
        if (disposed) return;
        try {
            const rows = read(); let changed = false;
            for (const row of rows) if (row.name === name && row.enabled !== false && verdicts.peek(name)?.usable === false) { row.enabled = false; changed = true; }
            if (changed) write(rows);
        } catch (error) { note.textContent = listProblem(error); }
    }
    const reorder = order => {
        const rows = read();
        if (order.length !== rows.length || order.every((from, to) => from === to)) return;
        strengthSaid.clear(); write(order.map(from => rows[from]));
    };
    // The grip moves the whole row with the pointer; arrow keys do the same one step at a time.
    function sortGrip(wrap, index) {
        const grip = el('span', null, 'fv-lora-grip'); grip.tabIndex = 0; grip.setAttribute('role', 'button');
        grip.setAttribute('aria-label', t('Drag to reorder', '拖动调整顺序')); grip.title = t('Drag to reorder', '拖动调整顺序');
        grip.onkeydown = event => {
            const delta = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
            const count = list.children.length;
            if (!delta || index + delta < 0 || index + delta >= count) return;
            event.preventDefault(); event.stopPropagation();
            const order = [...Array(count).keys()];
            [order[index], order[index + delta]] = [order[index + delta], order[index]];
            focusGrip = index + delta; reorder(order);
        };
        grip.onpointerdown = event => {
            if (event.button > 0) return;
            event.preventDefault(); wrap.classList.add('fv-lora-dragging');
            const move = e => {
                const next = [...list.children].find(row => row !== wrap && e.clientY < row.getBoundingClientRect().top + row.getBoundingClientRect().height / 2);
                if (next) { if (wrap.nextElementSibling !== next) list.insertBefore(wrap, next); }
                else if (list.lastElementChild !== wrap) list.append(wrap);
            };
            const end = e => {
                for (const [type, handler] of [['pointermove', move], ['pointerup', end], ['pointercancel', end]]) window.removeEventListener(type, handler);
                wrap.classList.remove('fv-lora-dragging');
                const order = [...list.children].map(row => Number(row.dataset.index));
                if (e.type === 'pointercancel') { render(); return; }
                reorder(order);
            };
            // Moving the row in the DOM drops pointer capture, so the drag listens on the window.
            for (const [type, handler] of [['pointermove', move], ['pointerup', end], ['pointercancel', end]]) window.addEventListener(type, handler);
        };
        return grip;
    }
    function render() {
        if (disposed) return;
        if (rendering) { renderAgain = true; return; }
        rendering = true;
        try {
            rowChecks = [];
            list.replaceChildren(); clear.remove();
            const rows = read();
            for (const key of [...strengthSaid.keys()]) if (key >= rows.length) strengthSaid.delete(key);
            syncPrompt(rows);
            rows.forEach((row, index) => {
                const wrap = el('div', null, 'fv-lora-row'); wrap.dataset.enabled = String(row.enabled !== false);
                const enabled = el('input'); enabled.type = 'checkbox'; enabled.checked = row.enabled !== false;
                enabled.setAttribute('aria-label', t('Enable LoRA', '启用 LoRA')); enabled.onchange = () => { row.enabled = enabled.checked; write(rows); };
                enabled.dataset.fvTipName = t('Use this LoRA', '使用这个 LoRA'); enabled.dataset.fvTipUse = t('Off keeps it listed but unused', '关闭后保留在列表里但不使用');
                const select = el('select'); select.setAttribute('aria-label', t('LoRA model', 'LoRA 模型'));
                const pinned = recentLoras().filter(n => names.includes(n));
                for (const name of [...new Set([...pinned, ...names, row.name])].filter(Boolean)) { const option = el('option', name); option.value = name; select.append(option); }
                select.value = row.name; select.title = row.name;
                select.onchange = () => {
                    // Leaving a LoRA that could not be used turns the row back on for the new choice.
                    if (verdicts.peek(row.name)?.usable === false) row.enabled = true;
                    row.name = select.value; rememberLora(row.name); write(rows);
                };
                if (!strengthSaid.has(index)) strengthSaid.set(index, {text: '', value: null, name: row.name});
                // A line belongs to the model and strength it was said for: another model in this place, or a strength set from outside, drops it.
                { const said = strengthSaid.get(index); if (said.name !== row.name || said.value !== Number(row.strength ?? 1)) { said.text = ''; said.name = row.name; } }
                const {input: strength, mark: strengthMark, restore: restoreStrengthNote} = loraStrengthBox(row.strength ?? 1, () => { if (strength.value !== '') { row.strength = Number(strength.value); write(rows); } }, strengthSaid.get(index));
                strength.setAttribute('aria-label', t('LoRA strength', 'LoRA 强度'));
                const trigger = el('input', null, 'fv-lora-trigger'); trigger.type = 'text'; trigger.maxLength = 300;
                trigger.dataset.name = row.name;
                trigger.placeholder = t('Trigger words', '触发词'); trigger.setAttribute('aria-label', trigger.placeholder);
                trigger.value = drafts.get(row.name) ?? triggerEntry(row.name).data?.words ?? '';
                trigger.oninput = () => drafts.set(trigger.dataset.name, trigger.value);
                trigger.onchange = async () => {
                    // Switching models can blur the old field after row.name changes.
                    const name = trigger.dataset.name, draft = trigger.value;
                    const request = Symbol(); saves.set(name, request);
                    drafts.set(name, draft); errors.delete(name);
                    try {
                        await saveTriggers(name, draft);
                        if (saves.get(name) !== request) return;
                        errors.delete(name);
                        if (drafts.get(name) === draft) drafts.delete(name);
                        refreshTriggerWords(name);
                    } catch (error) {
                        if (!disposed && saves.get(name) === request && drafts.get(name) === draft) {
                            errors.set(name, error.message); refreshTriggerWords(name);
                        }
                    }
                };
                select.disabled = strength.disabled = trigger.disabled = row.enabled === false;
                const remove = button('×', () => { rows.splice(index, 1); strengthSaid.clear(); write(rows); }); remove.setAttribute('aria-label', t('Remove LoRA', '移除 LoRA'));
                remove.dataset.fvTipName = t('Remove LoRA', '移除 LoRA'); remove.dataset.fvTipUse = t('Take it off the list; the file stays', '从列表移除，文件不删');
                wrap.append(enabled, loraMenu(select, t, pinned).wrap, strength, remove, trigger, strengthMark);
                wrap.dataset.index = String(index); wrap.dataset.sortable = String(rows.length > 1);
                if (rows.length > 1) wrap.prepend(sortGrip(wrap, index));
                const problem = el('div', null, 'fv-lora-problem'); problem.hidden = true; wrap.append(problem);
                // A switched-off row says so, and a click anywhere on it turns it back on.
                const off = el('div', t('Off. Click to turn on', '已停用，点击启用'), 'fv-lora-off'); off.hidden = row.enabled !== false; wrap.append(off);
                wrap.onclick = event => {
                    if (row.enabled !== false || wrap.dataset.unusable === 'true' || event.target.closest('button,select,input,.fv-lora-grip')) return;
                    row.enabled = true; write(rows);
                };
                let marked = null;
                const mark = verdict => {
                    const stamp = JSON.stringify(verdict);
                    if (stamp === marked) return;
                    marked = stamp;
                    const described = describeVerdict(verdict, t);
                    const unusable = verdict.usable === false;
                    wrap.dataset.unusable = String(unusable);
                    problem.classList.toggle('fv-lora-partial', Boolean(described?.partial));
                    problem.hidden = !described || (described.partial && (row.enabled === false || Number(row.strength ?? 1) === 0));
                    off.hidden = row.enabled !== false || unusable;
                    enabled.checked = row.enabled !== false; enabled.disabled = unusable;
                    strength.disabled = trigger.disabled = unusable || row.enabled === false;
                    select.disabled = !unusable && row.enabled === false;
                    if (!described) return;
                    if (described.partial) { problem.textContent = described.reason; return; }
                    enabled.checked = false; enabled.disabled = true; strength.disabled = trigger.disabled = true; select.disabled = false;
                    const fold = el('details', null, 'fv-lora-why'), why = el('pre', described.lines.join('\n'));
                    const report = button(t('Report a mistake', '报告误判'), () => {
                        const draft = feedbackDraft(row.name, verdict, described, t);
                        window.open(feedbackIssueURL(draft.text, productVersion(), draft.diagnostics, draft.title), '_blank', 'noopener');
                    });
                    fold.append(el('summary', t('Details', '详情')), why, report);
                    problem.replaceChildren(el('p', (verdict.reason === 'int8-unmergeable' ? '' : t('Cannot be used: ', '不能使用：')) + described.reason), fold);
                    if (row.enabled !== false) queueMicrotask(() => disableUnusable(row.name));
                };
                const check = refresh => verdicts.get(row.name, {refresh}).then(verdict => {
                    if (verdict && !disposed && wrap.isConnected && select.value === row.name) mark(verdict);
                });
                rowChecks.push(check);
                const known = verdicts.peek(row.name);
                if (known) mark(known);
                else check(false);
                list.append(wrap);
                if (list.isConnected) restoreStrengthNote();
                loadTriggers(row.name).catch(error => { if (!disposed && trigger.isConnected) note.textContent = userMessage(error, t, t('Could not read the trigger words. Try again.', '没能读取触发词，请重试。')); });
            });
            if (rows.length > 1) actions.append(clear);
            if (focusGrip != null) { list.querySelector(`.fv-lora-row[data-index="${focusGrip}"] .fv-lora-grip`)?.focus(); focusGrip = null; }
            note.textContent = rows.map(row => errors.get(row.name)).find(Boolean) || idleNote();
        } catch (error) {
            if (error instanceof DOMException) throw error;
            note.textContent = listProblem(error);
        }
        finally {
            rendering = false;
            if (renderAgain) { renderAgain = false; queueMicrotask(render); }
        }
    }
    const add = button(t('Add LoRA', '添加 LoRA'), async () => {
        await refresh(); // The folder may have changed since the list was last read.
        try { const rows = read(); if (rows.length >= 32) throw new Error(t('Use at most 32 LoRAs', '最多使用 32 个 LoRA')); if (!names.length) throw new Error(t('Put LoRA files (.safetensors) in the models/loras folder, then choose "Add LoRA".', '把 LoRA 文件（.safetensors）放进 models/loras 文件夹，再点“添加 LoRA”。')); const next = recentLoras().find(n => names.includes(n) && !rows.some(row => row.name === n)) ?? names[0]; rememberLora(next); rows.push({name: next, strength: 1, enabled: true}); write(rows); } catch (error) { note.textContent = error.message; }
    });
    async function refresh() {
        try { const response = await api.fetchApi('/models/loras', {cache: 'no-store'}); if (!response.ok) throw new Error(response.statusText); const listed = await response.json(); verdicts.clear(); skipped = (Array.isArray(listed) ? listed : []).filter(n => typeof n === 'string' && /\.(ckpt|pt|pth|bin|sft)$/i.test(n)).length; names = (Array.isArray(listed) ? listed : []).filter(n => typeof n === 'string' && n.toLowerCase().endsWith('.safetensors')); render(); }
        catch (error) { if (!disposed) note.textContent = shownMessage(error); }
    }
    // Only deselects: the files stay in the folder and the recent names are kept.
    const clear = button(t('Clear', '清空'), () => { write([]); });
    add.dataset.tourId = 'lora-add'; actions.append(add);
    add.dataset.fvTipName = t('Add LoRA', '添加 LoRA'); add.dataset.fvTipUse = t('Add a style or character model from models/loras', '从 models/loras 文件夹加一个风格或人物模型');
    clear.dataset.fvTipName = t('Clear', '清空'); clear.dataset.fvTipUse = t('Deselect every LoRA; the files stay', '取消选中所有 LoRA，文件不删');
    const changed = e => { if (String(e.detail) === String(node.id)) render(); };
    window.addEventListener('freevideo-loras', changed);
    const triggersChanged = e => refreshTriggerWords(e.detail);
    window.addEventListener('freevideo-lora-triggers', triggersChanged);
    const configure = node.onConfigure;
    // Only the persistent node panel participates in graph loading.
    if (panel.classList.contains('fv-lora-panel')) node.onConfigure = function (...args) { const result = configure?.apply(this, args); queueMicrotask(render); return result; };
    // Setup can bind another installation while this panel stays open. Recheck
    // its model context without rebuilding rows or interrupting edits.
    const modelTimer = setInterval(() => { for (const check of rowChecks) check(true); }, 5000);
    render(); refresh();
    const dispose = () => { disposed = true; clearInterval(modelTimer); window.removeEventListener('freevideo-loras', changed); window.removeEventListener('freevideo-lora-triggers', triggersChanged); };
    dispose.refresh = refresh;
    return dispose;
}

// A workflow without a LoRA node can still take LoRAs from the creation panel: the first one adds a FreeVideoLoRAStack node
// wired to `loras`, and clearing the list removes it with the graph counters it had, so a request with no LoRA is unchanged.
// The node keeps those counters in its properties, so the same holds after the workflow is saved and opened again.
const CREATED_BY_PANEL = 'freevideo_creation_lora';
function pendingLoras(node, existing = null) {
    const slot = node.inputs?.findIndex(input => input.name === 'loras');
    const LiteGraph = globalThis.LiteGraph;
    if (!(slot >= 0) || !node.graph || (!existing && !LiteGraph?.registered_node_types?.FreeVideoLoRAStack)) return null;
    let stack = existing;
    let adaptersValue = existing ? value(existing, 'adapters') || '[]' : '[]';
    const adapters = {name: 'adapters', get value() { return stack ? value(stack, 'adapters') || '[]' : adaptersValue; },
        set value(next) { adaptersValue = next; }, callback(next) {
        let rows; try { rows = JSON.parse(next); } catch { return; }
        const graph = node.graph, active = Array.isArray(rows) && rows.length > 0;
        if (active && !stack) {
            stack = LiteGraph.createNode('FreeVideoLoRAStack');
            stack.properties = {...stack.properties, [CREATED_BY_PANEL]: {node: graph.last_node_id, link: graph.last_link_id}};
            stack.pos = [node.pos[0] - 500, node.pos[1] + node.size[1] - 240];
            graph.add(stack); stack.connect(0, node, slot);
        } else if (!active && stack) {
            const counters = stack.properties?.[CREATED_BY_PANEL];
            graph.remove(stack);
            // Ids are numbers in the graph's counters but may be strings on the node.
            if (counters && Number(graph.last_node_id) === Number(stack.id)) { graph.last_node_id = counters.node; graph.last_link_id = counters.link; }
            stack = null; graph.change?.(); return;
        }
        if (!stack) return;
        const target = widget(stack, 'adapters');
        if (target) { target.value = next; target.callback?.(next); }
        graph.change?.();
        window.dispatchEvent(new CustomEvent('freevideo-loras', {detail: stack.id}));
    }};
    return {get id() { return stack?.id ?? 'creation-lora'; }, graph: node.graph, widgets: [adapters], inputs: []};
}

let current;
// Both top bars use the same exit and canvas fit.
export function showNodes(node, dialog = null) {
    // The creation panel stays mounted behind the node view (parked): coming back shows it as it was, with the same
    // picture and frame, nothing asked again.
    if (dialog?.open && current?.dialog === dialog) dialog.dataset.fvParked = 'true';
    if (dialog?.open) closeDialog(dialog);
    // The shell frames the node view each time it comes to the front (readable and centred); a fit here would only
    // show one frame at another zoom first.
    viewChanged('nodes', node);
}

const studioQueues = new WeakMap();
// The editor's way to the creation bar: the panel of this node is made (parked, out of sight) when none is open yet.
export function borrowCreationBar(node, options) {
    if (current?.node !== node || !current.dialog.isConnected) {
        const dialog = openStudio(node);
        if (current?.dialog === dialog && dialog.open) { dialog.dataset.fvParked = 'true'; closeDialog(dialog); }
    }
    return current?.node === node ? current.borrow?.(options) ?? null : null;
}
// The video each node's panel showed last: the panel opens on it at once, before the library answers.
const lastShown = new WeakMap();
// The last saved creation the panel showed, kept in this browser, so the first open after a reload shows it (and its
// cover) at once while the library answers in the background. Only where it is and its shape are kept (no time, no
// settings), and the entry goes as soon as that creation is deleted, so the browser keeps no trace of a deleted work.
const LAST_SHOWN_KEY = 'freevideo.studio.lastShown';
const SAVED_VIDEO = /^FreeVideo\/\d{4}-\d{2}-\d{2}\/[0-9a-f]{32}\/video\.mp4$/;
function rememberShown(r) {
    if (!SAVED_VIDEO.test(r?.video || '') || r.preview) return;
    const {width, height} = r.geometry || {};
    try { localStorage.setItem(LAST_SHOWN_KEY, JSON.stringify({video: r.video, version: r.version, geometry: {width, height}})); }
    catch { /* Private browsing: the library answers instead. */ }
}
function recallShown() {
    try {
        const r = JSON.parse(localStorage.getItem(LAST_SHOWN_KEY) || 'null');
        if (!SAVED_VIDEO.test(r?.video || '') || !(r.geometry?.width > 0 && r.geometry?.height > 0)) return null;
        return {video: r.video, version: r.version, geometry: {width: r.geometry.width, height: r.geometry.height}};
    } catch { return null; }
}
// Forget it when that creation is deleted (in Creations: one event per video; or the works list's removal, by path).
export function forgetShown(video) {
    if (!video) return;
    try { if (JSON.parse(localStorage.getItem(LAST_SHOWN_KEY) || 'null')?.video === video) localStorage.removeItem(LAST_SHOWN_KEY); }
    catch { try { localStorage.removeItem(LAST_SHOWN_KEY); } catch { /* No storage: nothing kept. */ } }
}
globalThis.addEventListener?.('freevideo-library-deleted', event => forgetShown(event.detail?.video));
globalThis.addEventListener?.('freevideo-works-removed', event => {
    const {ids = [], paths = []} = event.detail || {};
    for (const video of [...paths, ...ids]) if (typeof video === 'string') forgetShown(video);
});
export async function extendVideo(record, node = current?.node || app.graph?._nodes?.find(row => row.type === 'FreeVideoGenerate')) {
    if (!node) throw new Error(extendText(t, 'unavailable'));
    const state = await extendSourceState(api, record);
    if (state !== 'ok') throw new Error(extendSourceProblem(t, state));
    return openStudio(node, {extendFrom: await readExtendRecord(api, record)});
}

export async function openExtendEditor(record, node = current?.node || app.graph?._nodes?.find(row => row.type === 'FreeVideoGenerate'),
    {studio = current?.node === node ? current?.dialog : undefined, isCurrent} = {}) {
    if (isCurrent?.() === false) return null;
    if (!node || !app.graph?._nodes?.includes(node)) {
        node = app.graph?._nodes?.find(row => row.type === 'FreeVideoGenerate');
        if (!node) {
            node = globalThis.LiteGraph?.createNode('FreeVideoGenerate');
            if (!node || !app.graph) throw new Error(t('Could not open the creative panel. Try again.', '没能打开创作面板，请重试。'));
            app.graph.add(node); app.graph.change?.();
        }
    }
    const handle = await openEditorView(node, {studio, work: record, extend: true, isCurrent});
    await handle?.ready;
    return handle;
}

export function openStudio(node, {extendFrom = null} = {}) {
    // The page-wide task list (the top bar's status and its panel) follows this panel's videos, waiting ones included.
    try { startJobSources(); } catch (error) { console.warn('FreeVideo tasks:', error); }
    if (current?.node === node && current.dialog.open) {
        if (extendFrom) current.enterExtend(extendFrom); else current.dialog.focus();
        viewChanged('studio', node); return current.dialog;
    }
    // A parked panel of this node comes back as it was: no rebuild, no request, the stage keeps its frame.
    if (current?.node === node && current.dialog.dataset.fvParked === 'true' && current.dialog.isConnected) {
        const parked = current.dialog; delete parked.dataset.fvParked; delete parked.dataset.closing;
        parked.showModal(); viewChanged('studio', node);
        if (extendFrom) current.enterExtend(extendFrom);
        return parked;
    }
    if (current?.dialog.dataset.fvParked === 'true') current.dispose?.(); else current?.dialog.close();
    const extension = createExtendState(node, name => value(node, name), (name, v) => set(node, name, v));
    if (extendFrom && !extension.enter(extendFrom)) throw new Error(extendText(t, 'unavailable'));
    const dialog = el('dialog', null, 'fv-studio fv-create'); dialog.setAttribute('aria-label', t('FreeVideo creative workspace', 'FreeVideo 创作面板'));
    // The shell holds the previous view's picture until both are "true": ready = final geometry, loaded = first content.
    dialog.dataset.fvReady = 'false'; dialog.dataset.fvLoaded = 'false';
    // ComfyUI's background dialogs can lock body pointer events. This native
    // modal is above them and must remain interactive when the launcher reopens it.
    dialog.style.pointerEvents = 'auto';
    current = {node, dialog}; const cleanup = [];
    let firstStart = null, exampleFocused = false;
    // The console (studio_console.js) is put together once every control exists; until then nothing reads it.
    let consoleUI = null, promptLibrary = null;
    // Set while the editor borrows this panel's bar (current.borrow below).
    let borrowed = null;
    // Frame & size picker and the stage's outline of a new frame (set with the size tray).
    let framePicker = null;
    const header = el('header', null, 'fv-header'), brand = el('div', null, 'fv-brand');
    brand.append(wordmark());
    const tools = el('div', null, 'fv-header-actions');
    const phoneAccess = createLanAccess(t, api); tools.append(phoneAccess.element); cleanup.push(phoneAccess.dispose);
    const versionInfo = createVersionInfo(cn); tools.append(versionInfo.element); cleanup.push(versionInfo.dispose);
    tools.append(guideButton(t), feedbackButton(t), button(t('Settings', '设置'), openSetup, 'fv-quiet'));
    // The Agent's page is the fourth tab, shown where the Agent interface is on; the guide's last step circles it.
    agentTabReady(agentInterfaceState(api), () => agentInterfaceState(api));
    // The length hint links to the same two views, through the same actions.
    const toEditor = () => { firstStart?.dismiss(); return openEditorView(node, {studio: dialog}); };
    const toAutopilot = () => { firstStart?.dismiss(); return openAutopilot(node, {studio: dialog}); };
    const toImage = () => openImage(node, {studio: dialog});
    const navigation = viewSwitch('studio', () => {}, () => showNodes(node, dialog), toEditor, toAutopilot, toImage);
    const autopilotTab = navigation.querySelector('[data-view="autopilot"]');
    // The guide starts after the tab settles: it fixes its steps from the anchors present at that moment.
    const agentDiscovery = navigation.ready;
    header.append(brand, navigation, tools);
    const headerFit = fitStudioHeader(header, brand, navigation, tools); cleanup.push(headerFit.dispose);
    const body = el('div', null, 'fv-body'), controls = el('div', null, 'fv-controls'), output = el('div', null, 'fv-preview-column');
    // Nothing of the panel shows before its glass layer applies (first open after a reload), so it never jumps into place.
    body.style.visibility = 'hidden';
    glassReady.then(() => { body.style.visibility = ''; });
    dialog.append(header, body); body.append(controls, output);
    const portNotice = createPortNotice(cn);
    if (portNotice) output.append(portNotice);
    const updateNotice = createUpdateNotice(cn);
    header.after(updateNotice.element); cleanup.push(updateNotice.dispose);
    const section = (label, parent = controls) => { const wrap = el('section', null, 'fv-section'); wrap.append(el('div', label, 'fv-label')); parent.append(wrap); return wrap; };
    const field = (name, input) => { const label = el('label', null, 'fv-field'); label.append(el('span', name), input); return label; };
    const expand = label => { const d = el('details', null, 'fv-section'); d.append(el('summary', label, 'fv-section-title')); const content = el('div', null, 'fv-expand'); d.append(content); controls.append(d); cleanup.push(animateDetails(d)); return [d, content]; };
    syncReferencePrompt(node, t);
    // An untouched node shows its starter prompt as a placeholder, in the same readable form as a rewrite, and generates
    // it as written. Once something was typed, an emptied box stays empty and asks for a scene.
    const starter = node.freevideoStarterPrompt || '';
    const starterShown = shownRewrite(starter, t('en', 'zh')) ?? starter;
    const untouched = () => Boolean(starter) && value(node, 'text') === starter;
    const shown = text => starter && text === starter ? '' : String(text || '');
    const prompt = el('textarea'); prompt.value = shown(value(node, 'text')); prompt.setAttribute('aria-label', t('Prompt', '提示词'));
    prompt.dataset.fvGuide = 'describe'; prompt.dataset.tourId = 'prompt-box';
    const placeholder = () => { prompt.placeholder = extension.source ? extendText(t, 'prompt') : untouched() ? starterShown : t('Describe the scene… Type @ to reference media', '描述画面… 输入 @ 引用素材'); };
    placeholder();
    prompt.disabled = linked(node, 'text'); prompt.oninput = () => { set(node, 'text', prompt.value); placeholder(); };
    const promptEditor = el('div', null, 'fv-prompt-editor'); promptEditor.append(prompt);
    // Library characters offered in the @ list; a mention puts the character's pictures in the media list.
    let castLibrary = null, sceneLibrary = null, propLibrary = null;
    const castAssets = () => { const owner = upstream(node, 'media'); const field = owner && widget(owner, 'assets'); return field ? {owner, field} : null; };
    // The library's own upload of a picture goes to the same streaming upload the media panel uses.
    const castUpload = file => uploadMedia(api, file);
    const castBlocked = reason => ({people: t('Up to 3 characters', '最多 3 个角色'), props: t('Up to 2 props', '最多 2 个道具'), full: t('Up to 32 references', '参考素材最多 32 个'),
        continued: t('No room left when continuing', '接续段位置已满')}[reason] || '');
    const castPending = new Map();
    // The feature tour's "try it": one example into this panel's media list, prompt and settings.
    const canTryTour = entry => !busy && !queued && !capturing && (!entry.prompt || !linked(node, 'text'))
        && (!entry.assets?.length || Boolean(castAssets()))
        && Object.keys(entry.settings || {}).every(name => name === 'quality'
            ? Boolean(effort.levels().find(tier => tier.name === entry.settings.quality)) && !baseSteps.disabled && ['base_steps', 'two_pass', 'step_cache', 'upscale_factor'].every(key => widget(node, key) && !linked(node, key))
            : Boolean(widget(node, name)) && !linked(node, name))
        && ((entry.settings?.two_pass !== true && entry.settings?.preview !== true) || !twoPass.disabled);
    // A tour example that could not be filled in says why and what to do, not only "try again" (the report button stays beside it).
    const tourFault = (reason, error) => {
        const missing = /^missing_example_asset:/.test(String(error?.message || ''));
        const say = {
            busy: ['A video is being made. Try the example when it finishes.', '正在生成，完成后再试示例'],
            linked: ['Some settings this example needs are connected to other nodes, so it cannot fill them in. Disconnect those links in the node view, then try again.', '示例要改的设置已连到别的节点，无法填入；在节点视图断开这些连线后再试'],
            unavailable: ['The current model cannot use this example\'s settings.', '当前模型用不了这个示例的设置'],
            asset: missing ? ['An example file is missing from this installation. Select "Repair" under Settings > Environment, then try again.', '示例素材不在本机安装里，在“设置 → 环境”里点“修复”后重试']
                : ['The example files could not be read from FreeVideo. Check that FreeVideo is still running, then try again.', '没能从 FreeVideo 读取示例素材，确认 FreeVideo 仍在运行后再试'],
            write: ['The example files could not be added to this panel\'s media list. Try again.', '示例素材没能加入这个面板的素材列表，请再试一次'],
            workspace_changed: ['Another view was opened, so the example was not filled in. Go back to the creation panel and try again.', '已切到别的视图，示例没有填入，回到创作面板再试'],
            no_editor: ['The editor did not open, so the start clip was not placed. Open the editor, then select the example again.', '剪辑视图没有打开，起始片段没有放上去；打开剪辑视图后再点一次示例'],
            'pending-assets': ['This example is not available yet.', '这个示例暂时不能用'],
            unknown_example: ['This example is not available yet.', '这个示例暂时不能用'],
        }[reason];
        const detail = String(error?.message || error || '').split('\n')[0].slice(0, 160);
        fault(say ? t(...say) : detail ? t(`The example was not filled in: ${detail}`, `示例没有填入：${detail}`) : t('The example was not filled in.', '示例没有填入'));
    };
    const tourBlock = example => busy || queued || capturing ? 'busy'
        : (example.prompt && linked(node, 'text')) || Object.keys(example.settings || {}).some(name => linked(node, name)) ? 'linked' : 'unavailable';
    const tryTourExample = async example => {
        if (!canTryTour(example)) { tourFault(tourBlock(example)); return {ok: false, reason: 'unavailable'}; }
        const {applyTourExample} = await import('./tour_try.js');
        const target = castAssets();
        const result = await applyTourExample({node, api, cn, rows: () => mediaRows(node), canApply: canTryTour,
            setPrompt: text => { set(node, 'text', text); prompt.value = text; placeholder(); castOverlay.refresh(); },
            // Two-pass and preview settle into one pass mode, whichever of the two the example lists first.
            setSetting: (name, input) => {
                if (name === 'quality') {
                    const tier = effort.levels().find(tier => tier.name === input);
                    baseSteps.value = tier.steps; twoPass.checked = tier.twoPass; refineSteps.value = 3;
                    set(node, 'two_pass', tier.twoPass); set(node, 'step_cache', tier.stepCache || 'none'); setUpscale(tier.upscale);
                } else if (name === 'two_pass') { choosePassMode(!input ? 'off' : example.settings?.preview === true ? 'preview' : 'on'); }
                else if (name === 'preview') { choosePassMode(input && example.settings?.two_pass !== false ? 'preview' : (twoPass.checked ? 'on' : 'off')); }
                else { set(node, name, input); if (name === 'width') width.value = input; if (name === 'height') height.value = input; if (name === 'seconds') seconds.value = input; }
            },
            write: rows => { if (!target) return false; target.field.value = JSON.stringify(rows); target.owner.graph?.change?.();
                window.dispatchEvent(new CustomEvent('freevideo-media', {detail: target.owner.id})); return true; }}, example);
        if (result.ok) {
            firstStart?.dismiss();
            if (example.settings?.width && example.settings?.height) {
                pixels = Number(example.settings.width) * Number(example.settings.height) / 1024 ** 2;
                ratio = Number(example.settings.width) / Number(example.settings.height);
                selected = studioShapes(t).find(([, , aspect]) => Math.abs(aspect - ratio) < .01)?.[0] || 'custom'; node.properties.freevideo_pixels = pixels; node.properties.freevideo_aspect = selected;
            }
            syncSamplingSteps(); syncCanvasSize(); seconds.dispatchEvent(new Event('input')); updateRunButton();
            if (example.assets?.length) mediaDetails.open = true;
            // The lazy entry switches back through the editor before applying an example.
            if (topDialog() !== dialog) { tourFault('workspace_changed'); return {ok: false, reason: 'workspace_changed'}; }
            exampleFocused = true; generate.focus({preventScroll: true});
        } else tourFault(result.reason === 'unavailable' ? tourBlock(example) : result.reason, result.error);
        return result;
    };
    // A start clip goes onto the editor timeline, where it can be continued; nothing is placed unless the file loaded.
    const tryStartClip = async entry => {
        if (busy || queued || capturing) { tourFault('busy'); return {ok: false, reason: 'unavailable'}; }
        let file;
        try { file = await (await import('./tour_try.js')).fetchStartClip(entry); } catch (error) { tourFault('asset', error); return {ok: false, reason: 'asset', error}; }
        const handle = await openEditorView(node, {studio: dialog});
        await handle?.ready;
        if (!handle?.actions?.importFiles) { tourFault('no_editor'); return {ok: false, reason: 'no_editor'}; }
        // The clips already on the timeline are noted first, so the draft follows the clip this import adds and not an older one.
        const known = handle.actions.clipIds?.() || [];
        await handle.actions.importFiles([file], {at: 0});
        await handle.actions.continueStart?.(known);
        return {ok: true};
    };
    function castChoices() {
        if (!castLibrary || linked(node, 'text') || !castAssets()) return [];
        const rows = mediaRows(node);
        const places = (sceneLibrary?.list() || []).map(card => ({...card, kind: 'setting'}));
        const objects = (propLibrary?.list() || []).map(card => ({...card, kind: 'prop'}));
        return [...castLibrary.list(), ...places, ...objects].filter(card => card.name).map(card => {
            const state = mentionState(rows, card, Math.min(MAX_PICTURES_EACH, Number(card.images) || 0), {continued: Boolean(extension.source)});
            return {id: card.id, name: card.name, handle: mentionHandle(rows, card, castPending), thumb: card.thumbnail?.file ? view(card.thumbnail.file) : card.thumbnail?.url || '',
                ...(card.kind === 'setting' ? {setting: true} : card.kind === 'prop' ? {prop: true} : {}), blocked: state.ok ? '' : castBlocked(state.reason) || true};
        });
    }
    // The name goes into the text at once; the card's pictures follow when the full card has been read.
    function mentionCharacter(person) {
        if (!castLibrary) return false;
        if (castInRows(mediaRows(node)).some(entry => entry.id === person.id)) return true;
        const library = person.setting ? sceneLibrary : person.prop ? propLibrary : castLibrary;
        if (!library) return false;
        if (!mentionState(mediaRows(node), {id: person.id, ...(person.setting ? {kind: 'setting'} : person.prop ? {kind: 'prop'} : {})}, 1, {continued: Boolean(extension.source)}).ok) return false;
        const typed = person.handle || person.name;
        castPending.set(person.id, typed);
        library.get(person.id).then(card => {
            if (disposed) return;
            const rows = mediaRows(node), target = castAssets();
            if (!target || !mentionState(rows, card, undefined, {continued: Boolean(extension.source)}).ok) return;
            target.field.value = JSON.stringify(withCharacter(rows, card, typed));
            target.owner.graph?.change?.();
            window.dispatchEvent(new CustomEvent('freevideo-media', {detail: target.owner.id}));
        }).catch(() => { if (!disposed) fault(t('Could not add the character\'s pictures', '无法加入角色的图片')); })
            .finally(() => castPending.delete(person.id));
        return true;
    }
    const references = attachReferencePicker(prompt, {items: () => referenceItems(node), t, view, host: dialog,
        characters: castChoices, onCharacter: mentionCharacter,
        addMedia: () => { firstStart?.dismiss(); mediaDetails.open = true; triggerFeatureTour('media'); mediaDetails.scrollIntoView({block: 'nearest', behavior: 'smooth'}); mediaMount.querySelector('button')?.focus(); }});
    const mention = button('@', () => references.open(), 'fv-reference-trigger');
    mention.setAttribute('aria-label', t('Reference media (@)', '引用素材（@）'));
    mention.title = mention.getAttribute('aria-label');
    mention.disabled = prompt.disabled; mention.onpointerdown = e => e.preventDefault();
    // The prompt file could not be written, or the last prompt could not be read at launch.
    const draftNote = button('', async () => {
        draftNote.disabled = true;
        draftNote.textContent = promptDraftTrouble().save ? t('Saving…', '正在保存…') : t('Loading…', '正在读取…');
        try {
            if (promptDraftTrouble().save) await savePromptDraft(api);
            else {
                // Read again into the open project; edits not yet saved still win over the file.
                const current = app.graph.serialize(), target = app.extensionManager?.workflow?.activeWorkflow;
                const restored = await restorePromptDraft(api, structuredClone(current), target?.path || '__existing__');
                if (JSON.stringify(restored) !== JSON.stringify(current)) await app.loadGraphData(restored, true, true, target || 'FreeVideo');
            }
        } catch { /* The note stays with its retry. */ }
        draftNote.disabled = false; showDraftTrouble(promptDraftTrouble());
    }, 'fv-prompt-draft-note');
    const showDraftTrouble = ({save, restore}) => {
        if (draftNote.disabled) return;
        draftNote.hidden = !save && !restore;
        draftNote.textContent = save ? t('Prompt not saved. Click to retry', '描述没能保存，点此重试') : t('Last prompt not loaded. Click to retry', '上次的描述没能读取，点此重试');
        // The note is a small plain dot; its hover card says what happened and what a click does.
        draftNote.dataset.fvTipName = save ? t('Prompt not saved', '描述没能保存') : t('Last prompt not loaded', '上次的描述没能读取');
        draftNote.dataset.fvTipUse = t('Click to retry', '点此重试');
        draftNote.setAttribute('aria-label', draftNote.textContent);
    };
    showDraftTrouble(promptDraftTrouble()); cleanup.push(watchPromptDraftTrouble(showDraftTrouble));
    const promptTools = el('div', null, 'fv-prompt-tools'); promptTools.append(draftNote, mention); promptEditor.append(promptTools);
    cleanup.push(() => references.dispose());
    const terms = attachTermSuggest(prompt, {t, host: dialog, suppressed: () => !!mentionAt(prompt.value, prompt.selectionStart, prompt.selectionEnd, handleNames(castChoices()))});
    cleanup.push(() => terms.dispose());
    const castOverlay = attachMentionOverlay(prompt, {people: () => castInRows(mediaRows(node))});
    // The library's entry row rings the characters this prompt uses.
    let castShown = '';
    const announceCast = () => {
        const ids = mentionedIds(prompt.value, mediaRows(node));
        if (ids.join() !== castShown) { castShown = ids.join(); window.dispatchEvent(new CustomEvent('freevideo-cast-mentioned', {detail: ids})); }
    };
    prompt.addEventListener('input', announceCast);
    const castChanged = event => { if (String(event.detail) === String(mediaNode?.id)) { castOverlay.refresh(); references.refresh(); announceCast(); } };
    window.addEventListener('freevideo-media', castChanged);
    node.freevideoContinues = () => Boolean(extension.source);
    // Time lines are read for one video made in a single run; parts of a long video keep the typed text.
    const timedRun = () => !extension.source && (lengthControl?.plan ? lengthControl.plan.count === 1 : Number(value(node, 'seconds')) <= 15);
    node.freevideoSingleRun = timedRun;
    cleanup.push(() => { castLibrary?.destroy?.(); castLibrary = null; sceneLibrary?.destroy?.(); sceneLibrary = null; propLibrary?.destroy?.(); propLibrary = null; window.removeEventListener('freevideo-media', castChanged); prompt.removeEventListener('input', announceCast); castOverlay.dispose(); delete node.freevideoContinues; delete node.freevideoSingleRun; });
    openCastLibrary({api, text: t, viewURL: view, uploadOne: castUpload, onChange: () => { castOverlay.refresh(); references.refresh(); }})
        .then(library => { if (disposed) library?.destroy?.(); else castLibrary = library; });
    openCastLibrary({api, text: t, viewURL: view, uploadOne: castUpload, kind: 'setting', onChange: () => { references.refresh(); }})
        .then(library => { if (disposed) library?.destroy?.(); else sceneLibrary = library; });
    openCastLibrary({api, text: t, viewURL: view, uploadOne: castUpload, kind: 'prop', onChange: () => { references.refresh(); }})
        .then(library => { if (disposed) library?.destroy?.(); else propLibrary = library; });
    let requestAssets = null, rewriteSubjects = [];
    const enhancer = createPromptEnhancer({node, input: prompt, editor: promptEditor, api, t, starter, issueButton,
        // One rewrite runs at a time, so the subjects of the text it was given are the ones its numbers refer to.
        prepare: text => { const out = expandCast(timedRun() ? timedText(text, Number(value(node, 'seconds'))) : text, mediaRows(node), {continued: Boolean(extension.source)}); rewriteSubjects = out.subjects; return out.text; },
        restore: text => restoreCast(text, rewriteSubjects), display: text => shownRewrite(text, t('en', 'zh')) ?? text,
        // The rewrite has its own report; the video's report entry is not touched.
        onReport: row => { node.freevideoPromptReportId = row.rewrite_report_id || row.report_id; },
        onChange: () => { rememberPromptDraft(api, node); requestAssets?.refresh(); },
        setText: text => { set(node, 'text', text || (untouched() ? starter : '')); placeholder(); castOverlay.refresh(); announceCast(); }, context: () => {
        const media = enhancementMedia(referenceItems(node), linked(node, 'conditioning'));
        return {seconds: Number(value(node, 'seconds')), media};
    }});
    cleanup.push(() => enhancer.dispose());
    requestAssets = createRequestAssets({t, api, request: () => requestAssetsQuery?.()});
    cleanup.push(() => requestAssets.dispose());
    let requestAssetsQuery = null;
    // H3 text put in the box from elsewhere (a reused prompt, an older workflow) shows in the same readable form as a
    // rewrite; the enhancer keeps the model's words and sends them as written until the box is cleared or replaced.
    const showPrompt = () => {
        const text = value(node, 'text');
        const readable = untouched() || linked(node, 'text') ? null : shownRewrite(text, t('en', 'zh'));
        if (readable !== null) set(node, 'text', readable);
        if (prompt.value !== shown(value(node, 'text'))) prompt.value = shown(value(node, 'text'));
        enhancer.sync(readable === null ? '' : text);
    };
    showPrompt();
    const promptChanged = e => { if (String(e.detail) === String(node.id)) { prompt.disabled = linked(node, 'text'); showPrompt(); mention.disabled = prompt.disabled; placeholder(); castOverlay.refresh(); references.refresh(); announceCast(); } };
    window.addEventListener('freevideo-reference-prompt', promptChanged);
    cleanup.push(() => window.removeEventListener('freevideo-reference-prompt', promptChanged));
    const promptSection = section(t('Describe your scene', '描述画面'));
    // The writing guide opens in the browser; on screen it is a book, named in its hover card.
    const promptHelp = el('a', null, 'fv-prompt-help');
    promptHelp.href = 'https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md';
    promptHelp.target = '_blank'; promptHelp.rel = 'noopener noreferrer';
    promptHelp.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 4.2C6.6 3.2 4.7 2.8 2.5 3v9.6c2.2-.2 4.1.2 5.5 1.2 1.4-1 3.3-1.4 5.5-1.2V3c-2.2-.2-4.1.2-5.5 1.2ZM8 4.2v9.6"/></svg>';
    promptHelp.setAttribute('aria-label', t('How to write a prompt', '描述写法'));
    tip(promptHelp, t('How to write a prompt', '描述写法'), t('Open the guide to writing a good prompt', '打开描述写法指南，写得更准'));
    // Expand: a large box for long prompts; Generate works from there too.
    const promptExpand = createExpandButton({t, textarea: prompt, canGenerate: () => !generate.disabled, onGenerate: () => { generate.click(); return ''; }});
    tip(promptExpand.element, t('Expand', '放大'), t('Write a long prompt in a large box', '在大文本框里写长描述'));
    cleanup.push(() => promptExpand.dispose?.());
    promptTools.append(promptHelp, promptExpand.element);
    promptSection.append(enhancer.element);
    const canvas = section(t('Frame & size', '画幅与尺寸'));
    const canvasLock = el('span', null, 'fv-canvas-lock'); canvasLock.hidden = true; canvasLock.append(icon('lock'));
    canvas.querySelector('.fv-label').append(canvasLock);
    const shapes = el('div', null, 'fv-shapes'); canvas.append(shapes);
    const dimensionsLinked = linked(node, 'width') || linked(node, 'height');
    let selected = node.properties?.freevideo_aspect || 'custom', ratio = Number(value(node, 'width')) / Number(value(node, 'height'));
    const initialWidth = value(node, 'width'), initialHeight = value(node, 'height');
    if (selected === 'custom') {
        const match = [['16:9', 16 / 9], ['9:16', 9 / 16], ['1:1', 1]].find(([, r]) => Math.abs(Math.log(ratio / r)) < .035);
        if (match) selected = match[0];
    }
    // Preserve the requested pixel target, not the rounded canvas area. Using
    // the latter repeatedly shrinks a followed image after save/reload.
    let pixels = selected !== 'custom' && Number.isFinite(node.properties?.freevideo_pixels)
        ? node.properties.freevideo_pixels : (initialWidth * initialHeight) / 1024 ** 2;
    let disposed = false;
    const mp = el('select'); mp.setAttribute('aria-label', t('Resolution', '清晰度'));
    const presetRatio = {'16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1}[selected] || ratio;
    const sizeSelection = imageSizeSelection(pixels, selected === 'custom' ? null : presetRatio, Number(initialWidth), Number(initialHeight));
    if (pixels === 1 && sizeSelection.value === .98) pixels = .98;
    for (const n of sizeSelection.options) { const option = el('option', n === .98 ? t('0.98 MP · Recommended', '0.98 MP · 推荐') : `${n.toFixed(2)} MP`); option.value = n; mp.append(option); }
    mp.value = sizeSelection.value;
    // Keep the native menu's full labels; its closed face only needs the MP value.
    const resolutionSelect = el('div', null, 'fv-resolution-select'), resolutionValue = el('span', '', 'fv-resolution-value');
    resolutionValue.setAttribute('aria-hidden', 'true');
    let syncSizeSlider = null; // set with the resolution slider below
    function syncResolutionLabel() { resolutionValue.textContent = `${Number(mp.value).toFixed(2)} MP`; syncSizeSlider?.(); }
    syncResolutionLabel(); mp.addEventListener('input', syncResolutionLabel);
    resolutionSelect.append(mp, resolutionValue, icon('chevron'));
    const legacyOption = [...mp.options].find(o => Number(o.value) === 1);
    mp.disabled = dimensionsLinked;
    const seconds = el('input'); seconds.type = 'number'; seconds.required = true; seconds.min = '1.625'; seconds.step = 'any'; seconds.value = savedSeconds(node, value(node, 'seconds')); seconds.disabled = linked(node, 'seconds'); seconds.setAttribute('aria-label', t('Duration in seconds', '时长（秒）')); seconds.dataset.fvGuide = 'seconds';
    const audioLock = lockMark(), durationField = field(t('Duration · seconds', '时长 · 秒'), seconds); durationField.firstChild.append(audioLock);
    // Resolution is continuous: the node takes any width and height in 32-pixel steps (256–4096), so a slider in
    // megapixels sets them at the chosen shape; the five usual sizes stay as marks to snap to, and width × height
    // can be typed below it, with the shape kept or set free.
    const PRESET_PIXELS = [.5, .75, .98, 1.5, 2], PIXELS_MIN = .25, PIXELS_MAX = 2;
    const resolutionControl = el('div', null, 'fvs-res');
    const sizeReadout = el('span', '', 'fvs-res-value'), sizeSlider = el('input');
    sizeSlider.type = 'range'; sizeSlider.min = String(PIXELS_MIN); sizeSlider.max = String(PIXELS_MAX); sizeSlider.step = '0.01';
    sizeSlider.setAttribute('aria-label', t('Resolution in megapixels', '清晰度（百万像素）'));
    tip(sizeSlider, t('Resolution', '清晰度'), t('Drag for more or fewer pixels; marks are the usual sizes', '拖动调像素数，刻度是常用尺寸'), '← →');
    const sizeTicks = el('div', null, 'fvs-res-ticks');
    const tickAt = pixels => (pixels - PIXELS_MIN) / (PIXELS_MAX - PIXELS_MIN);
    function choosePixels(next) {
        if (dimensionsLinked || extension.source) return;
        const previous = pixels;
        pixels = next;
        if (![...mp.options].some(option => Number(option.value) === pixels)) { const option = el('option', `${pixels.toFixed(2)} MP`); option.value = pixels; mp.append(option); }
        mp.value = String(pixels);
        try { applySize(); if (pixels !== 1) legacyOption?.remove(); status.dataset.error = 'false'; }
        catch (error) { pixels = previous; mp.value = String(previous); syncResolutionLabel(); status.dataset.error = 'true'; status.textContent = userMessage(error, t, t('Could not change the resolution. Try again.', '没能更改清晰度，请重试。')); }
    }
    for (const preset of PRESET_PIXELS) {
        const mark = button(String(preset), () => choosePixels(preset), 'fvs-res-tick');
        if (preset === .98) mark.dataset.recommended = 'true';
        mark.style.setProperty('--fvs-at', String(tickAt(preset))); mark.dataset.pixels = String(preset);
        tip(mark, `${preset} MP`, preset === .98 ? t('The recommended size', '推荐尺寸') : t('Snap to this usual size', '回到这个常用尺寸'));
        sizeTicks.append(mark);
    }
    sizeSlider.oninput = () => {
        const raw = Number(sizeSlider.value), near = PRESET_PIXELS.find(preset => Math.abs(preset - raw) < .03);
        choosePixels(near ?? Math.round(raw * 100) / 100);
    };
    syncSizeSlider = () => {
        const shown = Math.min(PIXELS_MAX, Math.max(PIXELS_MIN, Number(mp.value) || .98));
        if (document.activeElement !== sizeSlider || Math.abs(Number(sizeSlider.value) - shown) > .031) sizeSlider.value = String(shown);
        sizeSlider.style.setProperty('--fvs-fill', String(tickAt(shown)));
        sizeSlider.disabled = mp.disabled;
        for (const mark of sizeTicks.children) { mark.disabled = mp.disabled; setAttr(mark, 'aria-pressed', String(Math.abs(Number(mark.dataset.pixels) - Number(mp.value)) < .005)); }
        const w = extension.source?.geometry.width ?? value(node, 'width'), h = extension.source?.geometry.height ?? value(node, 'height');
        sizeReadout.textContent = dimensionsLinked && !extension.source ? t('Size from connected nodes', '尺寸来自已连接节点') : `${Number(mp.value).toFixed(2)} MP · ${w} × ${h}`;
        if (mp.disabled) sizeSlider.dataset.fvTipWhy = extension.source ? t('A continued video keeps its size', '接着生成时沿用原视频尺寸') : t('Set by connected nodes', '由连接的节点决定');
        else delete sizeSlider.dataset.fvTipWhy;
    };
    resolutionControl.append(sizeSlider, sizeTicks, resolutionSelect);
    // Duration: the same seconds field, with a slider for 2–60 seconds beside it (longer lengths are typed).
    const durationSlider = el('input'); durationSlider.type = 'range'; durationSlider.min = '2'; durationSlider.max = '60'; durationSlider.step = '1';
    durationSlider.setAttribute('aria-label', t('Duration in seconds', '时长（秒）'));
    tip(durationSlider, t('Duration', '时长'), t('Drag to set the length, one second a step', '拖动设定时长，每格 1 秒'), '← →');
    const syncDurationSlider = () => {
        const n = Math.min(Number(durationSlider.max) || 60, Math.max(2, Math.round(Number(seconds.value) || 5)));
        if (document.activeElement !== durationSlider) durationSlider.value = String(n);
        durationSlider.style.setProperty('--fvs-fill', String((n - 2) / ((Number(durationSlider.max) || 60) - 2)));
        durationSlider.disabled = seconds.disabled;
    };
    durationSlider.oninput = () => { seconds.value = durationSlider.value; durationSlider.style.setProperty('--fvs-fill', String((Number(durationSlider.value) - 2) / ((Number(durationSlider.max) || 60) - 2))); seconds.dispatchEvent(new Event('input', {bubbles: true})); };
    durationSlider.onchange = () => seconds.dispatchEvent(new Event('change', {bubbles: true}));
    seconds.addEventListener('input', syncDurationSlider); seconds.addEventListener('change', syncDurationSlider);
    const durationRow = el('div', null, 'fvs-duration'); durationRow.append(seconds, durationSlider); syncDurationSlider();
    durationField.append(durationRow);
    const resolutionField = field(t('Resolution', '清晰度'), resolutionControl);
    resolutionField.firstChild.classList.add('fvs-field-head'); resolutionField.firstChild.append(sizeReadout);
    const fields = el('div', null, 'fv-fields fvs-size-fields'); fields.append(resolutionField, durationField); canvas.append(fields);
    const canvasNote = el('div', null, 'fv-canvas-note'), dimensions = el('span'), duration = el('span'); canvasNote.append(dimensions, duration); canvas.append(canvasNote);
    const rangeMount = el('div'); canvas.append(rangeMount);
    let capabilityView;
    // State of the two-pass choice below; the radio group is what is shown.
    const twoPass = el('input'); twoPass.type = 'checkbox'; twoPass.checked = value(node, 'two_pass') !== false;
    twoPass.disabled = linked(node, 'two_pass') || !widget(node, 'two_pass');
    // Two-pass switched off only because first-pass steps left 8; returning to 8 restores it.
    let autoSinglePass = false;
    const twoPassLabel = el('div', null, 'fv-two-pass fv-pass-row');
    twoPassLabel.title = t('Generates at a lower resolution, then upscales. Faster.', '先用低分辨率生成，再放大成片，更快。');
    const twoPassStatus = el('small', '', 'fv-two-pass-status');
    // Off, on, or on and paused after the first pass: a preview is one way to run two-pass.
    const passControl = createPassMode({t, onChoose: mode => choosePassMode(mode), listen: listenDialogKey});
    const passMode = passControl.element;
    cleanup.push(passControl.dispose);
    const previewPassHint = t('Stops at a lower-resolution preview. Choose "Run second pass" in the result when it looks right; the video matches a direct run.',
        '先出低分辨率预览，满意后在结果里点“继续二采”，成片与直接生成一致。');
    twoPassLabel.append(el('span', t('Two-pass', '二采')), twoPassStatus, passMode);
    let effortEstimate = {}, effortEstimateKey = '', effortEstimateTimer = null, effortEstimateRequest = null, lengthControl = null, chainController = null;
    // A level's step cache; custom steps and workflows without the input sample every step.
    const stepCacheNow = () => stepCacheOf(value(node, 'step_cache'));
    const clearStepCache = () => { if (stepCacheNow()) set(node, 'step_cache', 'none'); };
    // The two-pass learned upscale factor; a single pass has none (2).
    const upscaleNow = () => upscaleOf(value(node, 'upscale_factor'));
    const setUpscale = factor => { if (widget(node, 'upscale_factor') && value(node, 'upscale_factor') !== String(factor)) set(node, 'upscale_factor', String(factor)); };
    const chooseEffort = tier => {
        baseSteps.value = tier.steps;
        refineSteps.value = 3;
        twoPass.checked = tier.twoPass;
        autoSinglePass = false;
        set(node, 'two_pass', twoPass.checked);
        set(node, 'refine_steps', 3);
        set(node, 'step_cache', tier.stepCache || 'none');
        set(node, 'upscale_factor', String(tier.upscale));
        syncSamplingSteps();
    };
    // (effortPointed: the quality panel's line on what the pointed-at level gives; set with that panel.)
    let effortPointed = () => {};
    const effort = createSamplingEffort(t, {onChange: chooseEffort, onPreview: tier => { if (tier) showEffortEstimate(tier.steps, tier.twoPass, tier.stepCache, tier.upscale); else showEffortEstimate(); effortPointed(tier); }});
    // What the quality control shows now; the editor's generation bar shows the same control from this.
    const effortState = () => ({baseSteps: Number(baseSteps.value), refineSteps: Number(refineSteps.value),
        twoPass: twoPass.checked, stepCache: stepCacheNow(), upscale: upscaleNow(),
        disabled: baseSteps.disabled || linked(node, 'two_pass') || linked(node, 'refine_steps') || linked(node, 'step_cache') || linked(node, 'upscale_factor'),
        reason: extension.source ? continuationReason() : ''});
    const effortListeners = new Set();
    const sharedEffort = {state: effortState, choose: chooseEffort, levels: () => effort.levels(),
        subscribe(listener) { effortListeners.add(listener); return () => effortListeners.delete(listener); }};
    node.freevideoEffort = sharedEffort;
    cleanup.push(() => { effortListeners.clear(); if (node.freevideoEffort === sharedEffort) delete node.freevideoEffort; });
    const samplingSettings = el('div', null, 'fv-sampling-settings');
    const previewFirst = el('input'); previewFirst.type = 'checkbox'; previewFirst.checked = value(node, 'preview') === true;
    function choosePassMode(mode) {
        if (extension.source) return;
        const wantsTwoPass = mode !== 'off', wantsPreview = mode === 'preview';
        if (twoPass.checked !== wantsTwoPass) {
            // Switching off uses Max including its cache; switching on uses uncached Light. Typed steps stay.
            const next = passSwitch(wantsTwoPass, Number(baseSteps.value), Number(refineSteps.value), stepCacheNow(), effort.levels());
            twoPass.checked = wantsTwoPass; autoSinglePass = false; set(node, 'two_pass', wantsTwoPass); set(node, 'step_cache', next.stepCache || 'none'); setUpscale(next.upscale);
            if (next.steps !== Number(baseSteps.value) && !linked(node, 'base_steps') && widget(node, 'base_steps')) {
                baseSteps.value = String(next.steps); set(node, 'base_steps', next.steps);
                if (next.refine !== Number(refineSteps.value) && set(node, 'refine_steps', next.refine)) refineSteps.value = String(next.refine);
            }
        }
        if (previewFirst.checked !== wantsPreview) { previewFirst.checked = wantsPreview; set(node, 'preview', wantsPreview); }
        syncSamplingSteps(); updateRunButton();
    }
    function passState() {
        const passLinked = linked(node, 'two_pass'), previewLinked = linked(node, 'preview');
        return {mode: !twoPass.checked ? 'off' : previewFirst.checked ? 'preview' : 'on',
            disabled: {off: Boolean(extension.source) || passLinked || !widget(node, 'two_pass'), on: twoPass.disabled,
                preview: Boolean(extension.source) || twoPass.disabled || previewLinked || !widget(node, 'preview')},
            hidden: {preview: !widget(node, 'preview')}, hint: passLinked || previewLinked ? '' : previewPassHint};
    }
    // The editor's generation bar shows the same choices; it hears every change made here and chooses through here.
    const passListeners = new Set();
    function syncPassMode() {
        const value = passState();
        // The creative panel's tray keeps Off / On; a preview is chosen from the Generate key's menu.
        passControl.sync({...value, mode: value.mode === 'preview' ? 'on' : value.mode, hidden: {...value.hidden, preview: true}});
        for (const listener of [...passListeners]) listener(value);
    }
    const sharedPass = {state: passState, choose: mode => choosePassMode(mode),
        subscribe(listener) { passListeners.add(listener); return () => passListeners.delete(listener); }};
    node.freevideoPassMode = sharedPass;
    cleanup.push(() => { passListeners.clear(); if (node.freevideoPassMode === sharedPass) delete node.freevideoPassMode; });
    samplingSettings.append(effort.element, twoPassLabel);
    canvas.append(samplingSettings); cleanup.push(() => effort.dispose());
    cleanup.push(() => { clearTimeout(effortEstimateTimer); effortEstimateRequest?.abort(); });
    const mediaNode = upstream(node, 'media');
    const mediaFilled = () => swapState(mediaNode) ? Boolean(swapState(mediaNode).source || peopleOf(swapState(mediaNode)).length) : JSON.parse(value(mediaNode, 'assets') || '[]').some(r => r.enabled !== false)
        || Boolean(mediaNode.inputs?.some(input => ['first', 'last', ...referenceInputs, 'reference_audio'].includes(input.name) && input.link != null));
    // The line beside the closed title says what the section is for, until something is added.
    // The guide opens the section it points at; the ring stays on the title row.
    const mediaGuideTarget = () => {
        if (!mediaNode?.freevideoCreateMediaEditor) return null;
        // The Media chip of the console stands for the section; its tray opens when the guide points there.
        const anchor = consoleUI?.chip('media') || mediaTitle, r = anchor.getBoundingClientRect();
        return {node: anchor, rect: {left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height},
            reveal: () => { mediaDetails.open = true; }};
    };
    const [mediaDetails, mediaMount] = expand(t('Media', '素材'));
    const mediaTitle = mediaDetails.querySelector('summary');
    mediaTitle.dataset.fvGuide = 'media';
    const extendMount = el('div', null, 'fv-extend-source'); extendMount.hidden = true; mediaMount.prepend(extendMount);
    let releaseExtendThumbnail = () => {};
    cleanup.push(() => releaseExtendThumbnail());
    if (mediaNode?.freevideoCreateMediaEditor) { cleanup.push(mediaNode.freevideoCreateMediaEditor(mediaMount)); mediaDetails.open = mediaFilled(); }
    else mediaMount.append(el('p', t('Connect a FreeVideo Media panel in node view to upload here.', '在节点视图连接 FreeVideo 素材面板，可在这里上传。'), 'fv-legacy'));
    mediaTitle.addEventListener('click', () => {
        queueMicrotask(() => { if (mediaDetails.open && mediaDetails.dataset.expanded !== 'false') { firstStart?.dismiss(); triggerFeatureTour('media'); } });
    });
    const connectedLora = upstream(node, 'loras');
    const loraNode = connectedLora?.properties?.[CREATED_BY_PANEL] ? pendingLoras(node, connectedLora) : connectedLora || pendingLoras(node);
    // Replace person: its LoRA row is found among the registered names (it may sit in a subfolder), a row the user had
    // switched off is switched on for the run, and everything done is undone when the option goes off.
    // Not installed: it is downloaded (progress in the panel), then added. Returns '' when all is well, else the line
    // to show; runs one at a time.
    let swapLoraBusy = Promise.resolve('');
    const swapLoraText = reason => reason === 'ambiguous'
        ? t('More than one Replace person model was found. Add the one to use under "LoRA".', '找到多个替换人物模型，请在“LoRA”一栏添加要使用的那个。')
        : t('Could not read the LoRA list. Try again.', '无法读取 LoRA 列表，请重试。');
    let swapPanel = null;
    const swapDownload = swapLoraDownload({t, api, onReady: () => ensureSwapLora().then(problem => swapPanel?.notify(problem))});
    cleanup.push(() => swapDownload.dispose());
    async function applySwapLora(retry = false) {
        const state = mediaNode?.properties?.freevideo_replace_person;
        if (!state || !loraNode || !widget(loraNode, 'adapters')) return '';
        const current = value(loraNode, 'adapters') || '[]';
        const record = state.lora && typeof state.lora === 'object' ? state.lora : null;
        if (!state.on) {
            state.lora = null;
            if (record) { set(loraNode, 'adapters', releaseSwapLora(current, record)); window.dispatchEvent(new CustomEvent('freevideo-loras', {detail: loraNode.id})); }
            return '';
        }
        let names;
        try { const response = await api.fetchApi('/models/loras', {cache: 'no-store'}); if (!response.ok) throw new Error(response.statusText); names = await response.json(); }
        catch { return swapLoraText('unreadable'); }
        if (!state.on) return '';
        const now = value(loraNode, 'adapters') || '[]';
        const result = prepareSwapLora(now, names, record);
        if (!result.ok && result.reason === 'missing') return (await swapDownload.need(retry)) || swapLoraText('unreadable');
        if (!result.ok) return swapLoraText(result.reason);
        state.lora = result.record;
        if (result.adapters !== now) { set(loraNode, 'adapters', result.adapters); window.dispatchEvent(new CustomEvent('freevideo-loras', {detail: loraNode.id})); }
        return '';
    }
    const ensureSwapLora = (retry = false) => (swapLoraBusy = swapLoraBusy.then(() => applySwapLora(retry), () => applySwapLora(retry)));
    function swapChanged(info) {
        // Switching the option only changes which media the prompt reads: its pictures are re-bound, never renumbered.
        syncReferencePrompt(node, t, info?.toggled === true);
        ensureSwapLora(info?.toggled === true).then(problem => { if (problem && !swapDownload.shown) swapPanel?.notify(problem); });
        window.dispatchEvent(new CustomEvent('freevideo-media', {detail: mediaNode.id}));
    }
    if (mediaNode?.freevideoCreateMediaEditor) {
        const swap = swapPanel = createReplacePerson({t, api, media: mediaNode, mount: mediaMount, view, onChange: swapChanged, extra: swapDownload.element});
        cleanup.push(() => swap.dispose());
        if (swapState(mediaNode)) mediaDetails.open = true;
    }
    const [loraDetails, loraMount] = expand(t('LoRA', 'LoRA'));
    if (loraNode && widget(loraNode, 'adapters')) {
        const stop = loraPanel(loraNode, loraMount, {promptNode: node}); cleanup.push(stop);
        loraDetails.addEventListener('toggle', () => { if (loraDetails.open) stop.refresh(); });
        loraDetails.open = JSON.parse(value(loraNode, 'adapters') || '[]').length > 0;
    }
    else if (loraNode && widget(loraNode, 'lora')) {
        const select = el('select'); select.setAttribute('aria-label', t('LoRA model', 'LoRA 模型'));
        for (const name of widget(loraNode, 'lora').options?.values || ['None']) { const o = el('option', name); o.value = name; select.append(o); }
        select.value = value(loraNode, 'lora'); select.onchange = () => { set(loraNode, 'lora', select.value); requestAssets?.refresh(); };
        const {input: strength, mark: strengthMark} = loraStrengthBox(value(loraNode, 'strength'), () => { set(loraNode, 'strength', Number(strength.value)); requestAssets?.refresh(); });
        loraMount.append(field(t('LoRA', 'LoRA'), skinSelect(select)), field(t('Strength', '强度'), strength), strengthMark);
    }
    const [advancedDetails, advanced] = expand(t('Advanced', '高级选项'));
    const custom = el('div', null, 'fv-fields fvs-wh');
    const width = el('input'), height = el('input');
    // Each number field's line for a typed value brought into range (field_note.js); the submit check says through it.
    const fieldNotes = new Map();
    // Width × height, typed: with the shape kept, the other side follows (32-pixel steps); set free, the shape is custom.
    let shapeKept = true;
    // The side the frame panel's width × height left empty: it stays empty until a size is set again.
    let emptySide = '';
    const keepShape = button('', () => { shapeKept = !shapeKept; paintKeepShape(); }, 'fvs-keep-shape');
    keepShape.append(icon('lock'));
    function paintKeepShape() {
        setAttr(keepShape, 'aria-pressed', String(shapeKept));
        keepShape.setAttribute('aria-label', shapeKept ? t('Shape kept', '保持比例') : t('Shape free', '比例不限'));
        tip(keepShape, shapeKept ? t('Shape kept', '保持比例') : t('Shape free', '比例不限'),
            shapeKept ? t('The other side follows the chosen shape', '改一边，另一边按比例跟着变') : t('Each side is set on its own', '两边各自设定'));
    }
    paintKeepShape();
    const snap = n => Math.min(4096, Math.max(256, Math.round(n / 32) * 32));
    for (const [input, name, label] of [[width, 'width', t('Width', '宽度')], [height, 'height', t('Height', '高度')]]) {
        input.type = 'number'; input.required = true; input.min = '256'; input.max = '4096'; input.step = '32';
        input.value = value(node, name); input.disabled = dimensionsLinked; input.setAttribute('aria-label', label);
        custom.append(field(label, input));
        if (name === 'width') custom.append(keepShape);
        const note = fieldNote(input, {t}); fieldNotes.set(input, note);
        input.onchange = () => {
            if (!note.settle()) return;
            const kept = shapeKept && selected !== 'custom' && Number.isFinite(ratio) && ratio > 0;
            let w = name === 'width' ? Number(input.value) : Number(value(node, 'width'));
            let h = name === 'height' ? Number(input.value) : Number(value(node, 'height'));
            if (kept) { if (name === 'width') h = snap(w / ratio); else w = snap(h * ratio); }
            set(node, 'width', w); set(node, 'height', h);
            if (kept) {
                pixels = w * h / 1024 ** 2; node.properties.freevideo_pixels = pixels;
                if (![...mp.options].some(option => Number(option.value) === pixels)) { const option = el('option', `${pixels.toFixed(2)} MP`); option.value = pixels; mp.append(option); }
                mp.value = String(pixels); syncCanvasSize(); return;
            }
            selected = 'custom'; node.properties.freevideo_aspect = selected;
            ratio = w / h; pixels = w * h / 1024 ** 2; node.properties.freevideo_pixels = pixels;
            if (![...mp.options].some(option => Number(option.value) === pixels)) { const option = el('option', `${pixels.toFixed(2)} MP`); option.value = pixels; mp.append(option); }
            mp.value = String(pixels);
            syncCanvasSize();
        };
    }
    keepShape.disabled = dimensionsLinked;
    resolutionControl.append(custom);
    lengthControl = createStudioLength({api, node, seconds, advanced, fields, t, field, promptModel: enhancer.model,
        settleSeconds: () => durationNote.settle(),
        setSeconds: seconds => set(node, 'seconds', seconds), onChange: () => { if (runButtonReady) { updateRunButton(); showEffortEstimate(); updateDuration(); } },
        onEditor: toEditor, onAutopilot: toAutopilot, autopilot: !autopilotTab.hidden});
    cleanup.push(() => lengthControl.dispose());
    // The tab settles once the Agent interface has answered, and again if a failed check is retried.
    const autopilotWatch = new MutationObserver(() => { lengthControl.autopilot = !autopilotTab.hidden; });
    autopilotWatch.observe(autopilotTab, {attributes: true, attributeFilter: ['hidden']}); cleanup.push(() => autopilotWatch.disconnect());
    // Under the length row, only where a video is continued and the node can take it.
    const firstFrame = firstFrameRow(t, 'fv-first-frame');
    firstFrame.row.hidden = true; fields.after(firstFrame.row);
    firstFrame.input.checked = value(node, 'first_frame_reference') === true;
    firstFrame.input.onchange = () => {
        if (borrowed?.firstFrame) { borrowed.onFirstFrame?.(firstFrame.input.checked); return; } // the edit keeps it, not this panel
        set(node, 'first_frame_reference', firstFrame.input.checked); firstFrame.input.checked = value(node, 'first_frame_reference') === true;
    };
    let manyParts = false, originalOn = false;
    function updateFirstFrame() {
        // While the editor borrows the bar, the editor says when the switch applies and what is on.
        const lent = borrowed?.firstFrame;
        if (lent) {
            if (firstFrame.row.hidden !== !lent.shown) firstFrame.row.hidden = !lent.shown;
            firstFrame.input.checked = lent.checked; firstFrame.setDisabled(Boolean(lent.disabled)); return;
        }
        // While a new plan is being worked out the row keeps its place.
        const plan = lengthControl.plan; if (plan || !lengthControl.needsPlan) manyParts = plan?.count > 1;
        { const gone = !widget(node, 'first_frame_reference') || !(manyParts || extension.source); if (firstFrame.row.hidden !== gone) firstFrame.row.hidden = gone; }
        // Audio used as is fixes the picture to the sound, so this switch is off while it applies.
        firstFrame.input.checked = !originalOn && value(node, 'first_frame_reference') === true;
        firstFrame.setDisabled(Boolean(originalOn || capturing || queueState?.submitting || queueState?.running || chainController?.active));
    }
    const samplingFields = el('div', null, 'fv-fields'); samplingFields.style.marginTop = '14px';
    const baseSteps = el('input'), refineSteps = el('input');
    const samplingWarning = el('p', t('Changing steps may lower quality. Default: 8 + 3 steps.', '改步数可能降低质量。默认 8 + 3 步。'), 'fv-muted');
    samplingWarning.id = `fv-sampling-warning-${node.id}`;
    samplingWarning.style.color = 'var(--fv-warning)';
    // Steps past their range become its end, and the line under the field says so (no browser bubble).
    for (const [input, name, label, fallback, maximum] of [
        [baseSteps, 'base_steps', t('First-pass steps', '一采步数'), 8, 32],
        [refineSteps, 'refine_steps', t('Second-pass steps', '二采步数'), 3, 31],
    ]) {
        input.type = 'number'; input.min = '1'; input.max = String(maximum); input.step = '1'; input.required = true;
        input.value = value(node, name) ?? fallback;
        input.setAttribute('aria-label', label); input.setAttribute('aria-describedby', samplingWarning.id);
        input.oninput = () => syncSamplingSteps();
        fieldNotes.set(input, fieldNote(input, {t}));
        input.onchange = () => { fieldNotes.get(input).settle(); syncSamplingSteps(); };
        samplingFields.append(field(label, input));
    }
    // Typed steps are custom sampling, which has no step cache.
    baseSteps.oninput = () => { clearStepCache(); syncSamplingSteps(); };
    // Apply the two-pass rule to committed values, never while typing.
    const twoPassRule = () => {
        const steps = Number(baseSteps.value);
        if (baseSteps.value !== '' && Number.isInteger(steps) && steps >= 1 && steps <= 32
                && !linked(node, 'two_pass') && widget(node, 'two_pass')) {
            if (steps !== 8 && twoPass.checked) { twoPass.checked = false; set(node, 'two_pass', false); autoSinglePass = true; baseSteps.min = '1'; }
            else if (steps === 8 && autoSinglePass && !extension.source) { twoPass.checked = true; set(node, 'two_pass', true); autoSinglePass = false; }
        }
    };
    baseSteps.onchange = () => {
        // Before the range is applied: one step is allowed once two-pass is off. A value brought into range is
        // checked against that rule again.
        twoPassRule();
        const typed = baseSteps.value;
        fieldNotes.get(baseSteps).settle();
        if (baseSteps.value !== typed) twoPassRule();
        syncSamplingSteps();
    };
    // The run button exists only further down; until then its label is set there.
    let runButtonReady = false;
    function syncSamplingSteps() {
        if (extension.source) {
            const factor = continuationUpscale(baseSteps.value, twoPass.checked, refineSteps.value, stepCacheNow(), value(node, 'upscale_factor'), effort.levels());
            baseSteps.value = '8'; refineSteps.value = '3'; twoPass.checked = true; set(node, 'two_pass', true); clearStepCache(); setUpscale(factor); autoSinglePass = false;
        }
        const baseLinked = linked(node, 'base_steps');
        // Two-pass is offered at 8 first-pass steps, and from a single-pass level (it then gives Light).
        // A saved workflow that already uses it with other steps is shown as custom and left unchanged.
        const onePassLevel = !twoPass.checked && !baseLinked && Boolean(effortFor(Number(baseSteps.value), false, Number(refineSteps.value), stepCacheNow(), effort.levels()));
        const unavailable = Number(baseSteps.value) !== 8 && !twoPass.checked && !onePassLevel;
        twoPass.disabled = Boolean(extension.source) || unavailable || linked(node, 'two_pass') || !widget(node, 'two_pass');
        twoPassStatus.textContent = Number(baseSteps.value) !== 8 && !onePassLevel ? t('Needs 8 first-pass steps', '一采需 8 步') : '';
        twoPassLabel.classList.toggle('fv-two-pass-unavailable', unavailable);
        // A preview stops after the first pass, so it needs two-pass sampling.
        if (!twoPass.checked && previewFirst.checked) {
            previewFirst.checked = false; set(node, 'preview', false);
            if (runButtonReady) updateRunButton();
        }
        previewFirst.disabled = !twoPass.checked || linked(node, 'preview') || !widget(node, 'preview');
        blockOriginal('preview', previewFirst.checked && !previewFirst.disabled, {release() { choosePassMode('on'); return ['Preview first was turned off for "Use as is".', '已为“原样使用”关闭“先出预览”。']; }});
        syncPassMode();
        baseSteps.disabled = baseLinked || !widget(node, 'base_steps') || Boolean(extension.source);
        refineSteps.disabled = !twoPass.checked || linked(node, 'refine_steps') || !widget(node, 'refine_steps') || Boolean(extension.source);
        baseSteps.min = twoPass.checked ? '2' : '1';
        refineSteps.max = baseLinked ? '31' : String(Math.max(3, Math.min(31, Number(baseSteps.value || 8) - 1)));
        refineSteps.setCustomValidity(twoPass.checked && !baseLinked && !refineSteps.disabled && Number(refineSteps.value) !== 3 && Number(refineSteps.value) >= Number(baseSteps.value)
            ? t('Use 3 second-pass steps, or fewer than the first-pass steps.', '二采步数须为 3，或少于一采步数。') : '');
        if (twoPass.checked) refineSteps.removeAttribute('title');
        else refineSteps.title = t('Turn on two-pass acceleration to use this setting.', '开启二采加速后生效。');
        for (const [input, name] of [[baseSteps, 'base_steps'], [refineSteps, 'refine_steps']]) {
            if (!input.disabled && input.checkValidity() && Number(input.value) !== value(node, name)) {
                set(node, name, Number(input.value));
            }
        }
        const effortValue = effortState();
        effort.update(effortValue);
        for (const listener of [...effortListeners]) listener(effortValue);
        refreshEffortEstimate();
        consoleUI?.sync();
    }
    syncSamplingSteps();
    advanced.append(samplingFields, samplingWarning);
    // A continuation keeps a two-pass level (Light or Medium).
    function continuationReason() {
        const level = effortFor(8, true, 3, null, effort.levels(), upscaleNow());
        return level?.name === 'Medium' ? t('A continuation uses the Medium level.', '接着生成使用标准档。') : t('A continuation uses the Light level.', '接着生成使用轻量档。');
    }
    function showEffortEstimate(steps = Number(baseSteps.value), twoPassPlan = twoPass.checked, cache = stepCacheNow(), factor = upscaleNow()) {
        effort.setWarning('');
        const parts = lengthControl.plan?.count > 1 ? t(`${lengthControl.plan.count} parts`, `分 ${lengthControl.plan.count} 段`) : '';
        // A request that fits one run keeps its own estimate while the longest run is unknown.
        if ((lengthControl.capabilityState === 'pending' || lengthControl.capabilityState === 'unavailable')
            && (lengthControl.needsPlan || lengthControl.plan?.count > 1)) {
            const waiting = lengthControl.estimating ? t('Estimating time…', '正在估算用时…') : '';
            effort.setEstimate([parts, waiting].filter(Boolean).join(' · '));
            return;
        }
        if (lengthControl.plan?.single && Number(seconds.value) > 60) { effort.setEstimate(''); return; }
        if (lengthControl.needsPlan && !lengthControl.plan) { effort.setEstimate(''); return; }
        if (lengthControl.plan?.count > 1) {
            const plan = lengthControl.plan;
            const seconds = steps === Number(baseSteps.value) && twoPassPlan === twoPass.checked && cache === stepCacheNow() && factor === upscaleNow()
                ? sumPartEstimates(plan, lengthControl.estimate) : null;
            effort.setEstimate(seconds > 0 ? t(`${plan.count} parts · about ${Math.ceil(seconds / 60)} min`, `分 ${plan.count} 段 · 约 ${Math.ceil(seconds / 60)} 分钟`)
                : t(`${plan.count} parts`, `分 ${plan.count} 段`));
            return;
        }
        const rangeEstimate = capabilityView?.estimate(steps, twoPassPlan, arguments.length ? 3 : Number(refineSteps.value), cache, factor);
        if (rangeEstimate) {
            effort.setEstimate(rangeEstimate);
            effort.setWarning(capabilityView.warning(steps, twoPassPlan, arguments.length ? 3 : Number(refineSteps.value), cache, factor));
            return;
        }
        const mode = twoPassPlan ? 'two_pass' : 'single';
        // Keys: "<steps>[:<cache>][x<factor> when two-pass and not 2]".
        const estimate = effortEstimate[mode]?.[`${steps}${cache ? ':' + cache : ''}${twoPassPlan && factor !== 2 ? 'x' + factor : ''}`];
        // A previewed level always refines three steps; the current plan may not.
        if (estimate?.status !== 'estimated' || (mode === 'two_pass' && !arguments.length && Number(refineSteps.value) !== 3)) {
            effort.setEstimate('', t('No estimate is available for these settings.', '当前设置暂无预计用时。'));
            return;
        }
        const minutes = estimate.high_seconds >= 90, scale = minutes ? 60 : 1;
        const low = Math.max(1, Math.floor(estimate.low_seconds / scale));
        const high = Math.max(low + 1, Math.ceil(estimate.high_seconds / scale));
        const unit = minutes ? t('min', '分钟') : t('s', '秒');
        const extra = estimate.includes_text_encoding ? '' : t(' + prompt', ' + 提示词准备');
        effort.setEstimate(`${t('~', '约 ')}${low}–${high} ${unit}${extra}`);
    }
    function refreshEffortEstimate(force = false) {
        capabilityView?.update();
        let task = 'external';
        let requestRefs = [];
        try {
            if (!linked(node, 'conditioning')) {
                const media = referenceItems(node), refs = media.filter(r => !r.role);
                requestRefs = refs;
                const audio = refs.some(r => r.kind === 'audio');
                // Next to references, keyframes are still guides, which the engine counts as pictures.
                const keyframes = (() => { try { return JSON.parse(value(upstream(node, 'media'), 'assets') || '[]')
                    .some(r => r.enabled !== false && ['first', 'last'].includes(r.role)); } catch { return false; } })()
                    || ['first', 'last'].some(name => linked(upstream(node, 'media'), name));
                const visual = refs.some(r => r.kind !== 'audio') || refs.length > 0 && keyframes;
                const first = media.some(r => r.role === 'first'), last = media.some(r => r.role === 'last');
                task = audio ? visual ? 'ref2va_av' : 'ref2va_audio' : visual ? 'ref2va'
                    : first && last ? 'fl2va' : first ? 'i2va' : last ? 'l2va' : 't2va';
            }
        } catch {} // Unknown connected inputs must not reuse text-only timings.
        let adapters = false;
        try { adapters = JSON.parse(value(loraNode, 'adapters') || '[]').some(r => r.enabled !== false && Number(r.strength ?? 1) !== 0); } catch {}
        if (loraNode && !widget(loraNode, 'adapters')) adapters = value(loraNode, 'lora') !== 'None';
        const requestWidth = extension.source?.geometry.width ?? value(node, 'width');
        const requestHeight = extension.source?.geometry.height ?? value(node, 'height');
        lengthControl?.refresh({width: requestWidth, height: requestHeight, task, adapters,
            settings: {tier: effortFor(Number(baseSteps.value), twoPass.checked, Number(refineSteps.value), stepCacheNow(), effort.levels(), upscaleNow())?.name.toLowerCase() || 'custom',
                base_steps: Number(baseSteps.value), refine_steps: Number(refineSteps.value), two_pass: twoPass.checked,
                step_cache: stepCacheNow(), upscale_factor: upscaleNow()}}, force);
        requestAssetsQuery = () => {
            const query = new URLSearchParams({width: requestWidth, height: requestHeight, seconds: value(node, 'seconds'), task,
                base_steps: baseSteps.value, refine_steps: refineSteps.value, two_pass: twoPass.checked ? '1' : '0',
                enhance: node.properties?.freevideo_prompt_versions?.enabled ? '1' : '0'});
            for (const name of enabledLoras(loraNode, {upstream, widget, value})) query.append('lora', name);
            for (const row of referenceFiles(requestRefs)) query.append('ref', row);
            return query;
        };
        requestAssets.refresh(force);
        const query = new URLSearchParams({width: requestWidth, height: requestHeight, seconds: value(node, 'seconds'), task, adapters: adapters ? '1' : '0'}).toString();
        if (query === effortEstimateKey && !force) { showEffortEstimate(); return; }
        clearTimeout(effortEstimateTimer);
        effortEstimateKey = query; effortEstimate = {}; showEffortEstimate(); effortEstimateRequest?.abort();
        effortEstimateTimer = setTimeout(async () => {
            const controller = new AbortController(); effortEstimateRequest = controller;
            try {
                const reply = await api.fetchApi('/freevideo/sampling-estimate?' + query, {signal: controller.signal});
                if (!reply.ok) return;
                const estimate = await reply.json();
                if (disposed || controller.signal.aborted || effortEstimateKey !== query) return;
                effortEstimate = estimate; showEffortEstimate();
            } catch {} // An unavailable estimate must not block the editor.
        }, 220);
    }
    const seedFields = el('div', null, 'fv-fields'); seedFields.style.marginTop = '14px';
    const seed = el('input'); seed.type = 'number'; seed.required = true; seed.min = '0'; seed.max = String(2 ** 53 - 1); seed.step = '1'; seed.value = value(node, 'seed'); seed.disabled = linked(node, 'seed'); seed.setAttribute('aria-label', t('Seed', '种子'));
    const seedNote = fieldNote(seed, {t}); fieldNotes.set(seed, seedNote);
    seed.onchange = () => { if (seedNote.settle()) set(node, 'seed', Number(seed.value)); };
    const mode = el('select'); for (const [v, label] of [['randomize', t('New each run', '每次随机')], ['fixed', t('Keep fixed', '保持固定')], ['increment', t('Increment', '递增')], ['decrement', t('Decrement', '递减')]]) { const o = el('option', label); o.value = v; mode.append(o); }
    mode.value = value(node, 'control_after_generate') || 'fixed'; mode.onchange = () => set(node, 'control_after_generate', mode.value);
    mode.dataset.fvTipName = t('After generation', '生成后'); mode.dataset.fvTipUse = t('How the seed changes after each video', '每次生成后种子怎么变');
    seedFields.append(field(t('Seed', '种子'), seed), field(t('After generation', '生成后'), skinSelect(mode))); advanced.append(seedFields);
    const previewHead = el('div', null, 'fv-preview-head'); previewHead.append(el('strong', t('Preview', '预览')));
    const previewTools = el('div', null, 'fv-preview-tools');
    // Creations opened from a notification, the palette or search finish a first-pass preview through this node too.
    setDefaultUpscale(value => node.freevideoUpscaleResult(value));
    setDefaultUpscaleWork((value, options) => node.freevideoUpscaleWork(value, options));
    const worksButton = button('', () => {
        stage.querySelector('video')?.pause(); openLibrary(t, {upscale: value => node.freevideoUpscaleResult(value),
            extend: (saved, options) => openExtendEditor(saved, node, {studio: dialog, ...options}),
            // The library's Reuse settings (card menu and details) fills this panel.
            reuse});
    }, 'fv-works-entry'); worksButton.dataset.tourId = 'works-entry';
    worksButton.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1.2"/><rect x="9" y="2.5" width="4.5" height="4.5" rx="1.2"/><rect x="2.5" y="9" width="4.5" height="4.5" rx="1.2"/><rect x="9" y="9" width="4.5" height="4.5" rx="1.2"/></svg>';
    worksButton.append(el('span', t('Creations', '作品库'), 'fv-works-label'));
    worksButton.setAttribute('aria-label', t('Creations', '作品库'));
    tip(worksButton, t('Creations', '作品库'), t('Open your works to sort and manage them', '打开作品库，筛选和管理'));
    previewHead.append(previewTools); previewHead.hidden = true; output.append(previewHead);
    // The picture's space: its size comes from the column and the shape alone (fitPreview), never from what is below it.
    const previewSpace = el('div', null, 'fv-preview-space sgf-avoid'); previewSpace.dataset.fvMain = ''; output.append(previewSpace);
    // The bar under the picture; the result's numbers and notes fold into its Details.
    const stageBar = createStageBar(t, {onStep: delta => stepHistory(delta)});
    // The shared play bar (glass/player_bar.js) sits 8 px under the video, as wide as it, in a row kept for it so the
    // picture never moves when a video comes or goes; the browser's own controls and menu are off.
    const playerRow = el('div', null, 'fvs-player'); output.append(playerRow);
    let playerBar = null;
    output.append(stageBar.element); cleanup.push(() => stageBar.dispose()); cleanup.push(() => playerBar?.detach());
    // The list of parts sits below the picture and its bar: opening it never moves the picture.
    const partList = createChainPartList(t, (block, text) => chainController.setText(block, composePartText(chainController.state, block, text)), {
        redo: block => { failure.clear(); return chainController.redo(block); },
        resume: () => { failure.clear(); return chainController.resume(); },
        onError: error => showChainActionError(error),
        playSeam: seconds => playAround(seconds)});
    // Set once the queue buttons exist: "Continue generating" stays hidden while a listed part offers the same.
    let syncResume = () => {};
    // A stopped long video keeps its parts listed while the preview shows it.
    const syncPartList = () => {
        const chain = chainController?.state;
        if (chain) partList.update(chain, chainController.active, Boolean(result?.request_id) && result.request_id === chain.id);
        syncResume();
    };
    output.append(partList.element);
    cleanup.push(() => partList.clear());
    const stage = el('div', null, 'fv-stage sgf-avoid');
    stage.setAttribute('aria-label', t('Video preview', '视频预览'));
    // The floating task window grows out of this picture and returns to it.
    stage.dataset.fvTaskStage = 'studio';
    // Before the first video: one quiet line under the empty frame's icon says where to start.
    stage.style.setProperty('--fvs-empty-hint', JSON.stringify(t('Describe a scene below to start', '在下方写一句描述，开始生成')));
    const stageMedia = el('div', null, 'fv-stage-media');
    // The next generation's empty frame: the stage itself, at the chosen (or pointed-at) shape, with its pixels.
    const nextSize = el('span', null, 'fvs-next-size');
    let nextShown = false;
    stage.append(stageMedia); previewSpace.append(stage);
    // Plays two seconds either side of a seam and stops there; pausing or jumping elsewhere hands the video back.
    let seamFrame = 0;
    function playAround(seconds) {
        const video = stageMedia.querySelector('video');
        if (!video) return;
        cancelAnimationFrame(seamFrame);
        const end = seconds + 2;
        let last = Math.max(0, seconds - 2);
        video.currentTime = last;
        video.play().catch(() => {});
        const watch = () => {
            const now = video.currentTime;
            if (Math.abs(now - last) > 1 || (video.paused && !video.seeking)) return;
            if (now >= end) { video.pause(); return; }
            last = now; seamFrame = requestAnimationFrame(watch);
        };
        seamFrame = requestAnimationFrame(watch);
    }
    cleanup.push(() => cancelAnimationFrame(seamFrame));
    const progress = createGenerationProgress(t, undefined, {compact: true, api}); stage.append(progress.element);
    const chainLine = el('div', '', 'fv-muted fv-chain-position'); chainLine.hidden = true; progress.element.prepend(chainLine);
    // The queued count sits after the part number; a line too narrow for both puts the count on its own line.
    const setChainLine = text => {
        const main = el('span', text), more = chainController?.waiting, queued = el('span', null, 'fv-chain-queued');
        if (more) queued.append(el('span', ' · ', 'fv-chain-sep'), t(`${more} more queued`, `另有 ${more} 条排队`));
        chainLine.replaceChildren(main, ...(more ? [queued] : []));
    };
    progress.updateReport({report_id: node.freevideoReportId});
    cleanup.push(() => progress.dispose());
    cleanup.push(createPreviewScene(stage, progress.element, stageMedia));
    // While a video is made, soft water rises from the bottom of the frame with the made share and its surface keeps
    // rolling (glass/studio.css). CSS only (transform and translate): nothing runs when idle.
    const making = el('div', null, 'fvs-making'); making.setAttribute('aria-hidden', 'true');
    const level = el('i', null, 'fvs-making-level');
    level.append(el('i', null, 'fvs-wave fvs-wave-back'), el('i', null, 'fvs-wave fvs-wave-mid'), el('i', null, 'fvs-wave fvs-wave-front'));
    making.append(level);
    stage.append(making);
    const madeTrack = progress.element.querySelector('.fv-generation-track');
    const syncMade = () => {
        const value = Number(madeTrack?.getAttribute('aria-valuenow') ?? 0), made = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) / 100 : 0;
        // Lower than before is a new run: it starts from the bottom at once.
        if (made < (Number(making.style.getPropertyValue('--fvs-made')) || 0)) {
            making.dataset.instant = 'true'; requestAnimationFrame(() => requestAnimationFrame(() => delete making.dataset.instant));
        }
        making.style.setProperty('--fvs-made', String(made));
    };
    const madeWatch = new MutationObserver(syncMade);
    if (madeTrack) madeWatch.observe(madeTrack, {attributes: true, attributeFilter: ['aria-valuenow']});
    syncMade(); cleanup.push(() => { madeWatch.disconnect(); making.remove(); });
    // The report card under the result: 'invite' after a watched video,
    // 'downloaded' once the user saved the report themselves.
    function reportCard(mode, animate = true) {
        const bar = links.querySelector('.fv-actions'), anchor = bar?.querySelector('.fv-action-report');
        if (disposed || !anchor) return;
        showReportTip(bar, anchor, t, {mode, animate, onHide: () => { reportTipFor = null; },
            submit: () => reportIssue(progress.fetchReport, productVersion(), {save: mode === 'invite'})});
    }
    async function downloadReport() { if (focusReportTip(links.querySelector('.fv-actions'))) return; await progress.downloadReport(); reportCard('downloaded'); }
    function placeReportTip(delay) {
        clearTimeout(reportTipTimer);
        reportTipTimer = setTimeout(() => {
            // A card already on screen is redrawn in place, without opening again.
            if (!reportTipClosed() && result?.video === reportTipFor) reportCard('invite', delay > 0);
        }, delay);
    }
    let revealTimer = null;
    function hideProgress() {
        clearTimeout(revealTimer); revealTimer = null;
        delete stage.dataset.revealing; progress.hide();
        if (result?.video) showSummary(result); else stageBar.setSummary('');
        fitPreview();
    }
    // The bar's line for a result: the work's name (the prompt's first 24 characters; untitled without one, the whole
    // prompt on hover) and then its facts. The name follows once the saved prompt is read.
    let summaryFor = null;
    function showSummary(r) {
        // The bar names the work and keeps its actions; its size and length are not written (only a preview says so).
        const parts = resultSummary(r, true).filter(part => part.key === 'level'), facts = parts.map(part => part.text).join(' · '); summaryFor = r;
        stageBar.setSummary(facts, {parts});
        savedPrompt(api, r).then(prompt => {
            if (summaryFor !== r || disposed) return;
            const letters = Array.from(prompt.replace(/\s+/g, ' '));
            const name = letters.length ? letters.slice(0, 24).join('').trim() + (letters.length > 24 ? '…' : '') : t('Untitled', '未命名作品');
            stageBar.setSummary(facts, {name, full: prompt, parts});
        });
    }
    // One line about the video on screen: size, length and quality level.
    function resultSummary(r, asParts = false) {
        const g = r?.geometry || {}, parts = [];
        const length = g.seconds || (g.frames && g.frames / (g.fps || 24));
        if (g.width && g.height) parts.push({key: 'size', text: `${g.width} × ${g.height}`});
        if (length) parts.push({key: 'length', text: t(`${Number(length).toFixed(1)} s`, `${Number(length).toFixed(1)} 秒`)});
        // The work's own size and length; the level is the console's (a first-pass preview is still named).
        if (r?.preview) parts.push({key: 'level', text: t('First-pass preview', '一采预览')});
        return asParts ? parts : parts.map(part => part.text).join(' · ');
    }
    cleanup.push(hideProgress);
    let previewRatio = Number(initialWidth) / Number(initialHeight);
    // The space takes no more height than the picture can use at full width.
    const ratioStyle = () => { if (Number.isFinite(previewRatio) && previewRatio > 0) previewSpace.style.setProperty('--fv-ratio', String(previewRatio)); };
    ratioStyle();
    // The column's own box decides the picture: its width, and its height less the bar under the picture and one
    // status line. Notes, cards and lists that arrive later go below and scroll, so the picture never shrinks for them.
    // The bar's room is fixed (one row, two on phones where the actions wrap), so actions that arrive late never move the picture.
    const STATUS_LINE = 24, phone = window.matchMedia?.('(max-width:760px)');
    // The play bar's row: 32 px, 44 on phones where its buttons are touch targets (glass/studio.css).
    const TOAST_SLOT = 56, playerRoom = () => (phone?.matches ? 44 : 32);
    // A status line that takes more than its row on a phone (a failure that wraps, with its report button under it)
    // lifts the picture by the difference, so it never runs into the toast slot.
    // A notice that wraps to more lines than the slot holds widens the slot while it shows (8 px clear on each side).
    const toastSlot = () => Math.max(TOAST_SLOT, toastHeight() + 16);
    const statusRoom = () => Math.max(STATUS_LINE, output.querySelector(':scope > .fv-status')?.offsetHeight || 0);
    // A tall window (portrait, wider than a phone) gives the picture the column less 48 px each way (the works button's
    // strip left out); when the picture fills the width and 320 px or more is left under it, the recent works take that
    // room as a grid (glass/studio.css) and the picture keeps to the top.
    const tall = window.matchMedia?.('(orientation: portrait) and (min-width: 761px)');
    const TALL_EDGE = 48, TALL_RAIL = 122, GRID_MIN = 320;
    let recentCount = () => 0;
    let consoleSnap = {shape: '', held: false};
    // Set once the failure card exists (below): whether it is on the picture, and the height it needs to read whole.
    let failureShown = () => false, failureNeed = () => 0;
    const tallChange = () => fitPreview();
    tall?.addEventListener?.('change', tallChange); cleanup.push(() => tall?.removeEventListener?.('change', tallChange));
    function fitTall(gap) {
        const playerShown = getComputedStyle(playerRow).display !== 'none';
        const room = output.clientHeight - 2 * (TALL_EDGE / 2) - 40 - gap - (playerShown ? playerRoom() + gap : 0);
        const gridWidth = output.clientWidth - 2 * TALL_EDGE, gridHeight = gridWidth / previewRatio;
        const grid = recentCount() > 0 && gridHeight <= room && room - gridHeight >= GRID_MIN;
        body.dataset.grid = String(grid);
        return {room, width: grid ? gridWidth : output.clientWidth - TALL_EDGE - TALL_RAIL, grid, barRoom: 40 + gap + (playerShown ? playerRoom() + gap : 0)};
    }
    function fitPreview() {
        const isTall = !phone?.matches && !!tall?.matches;
        if (isTall) body.dataset.tall = 'true'; else { delete body.dataset.tall; delete body.dataset.grid; }
        const style = getComputedStyle(output), gap = parseFloat(style.rowGap) || 0;
        const fit = isTall && Number.isFinite(previewRatio) && previewRatio > 0 ? fitTall(gap) : null;
        const width = fit ? fit.width : output.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        // On a phone a 56 px slot stays free above the console for the shared toast strip, so a toast never covers the
        // result's bar and its actions; the status line keeps its own row there. Wider windows show only the picture and
        // its bar here (the status line reads in the notes line at the top of the dock).
        // The play bar's row is left out while there is no video to play (glass/studio.css), and its room with it.
        const playerShown = getComputedStyle(playerRow).display !== 'none';
        const room = fit ? fit.room : output.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)
            - (phone?.matches ? 88 + toastSlot() + statusRoom() + 2 * gap : 40 + gap) - (playerShown ? playerRoom() + gap : 0);
        if (!(width > 0) || !(room > 0) || !Number.isFinite(previewRatio) || previewRatio <= 0) return;
        const height = Math.max(Math.min(room, 160), Math.min(room, width / previewRatio));
        const fittedWidth = Math.min(width, height * previewRatio), fittedHeight = fittedWidth / previewRatio;
        // A failure card on the picture keeps its cause, next step and buttons whole: the picture widens to the card
        // (560 px at most) and grows to its height, and the column scrolls if that is taller than the room.
        const failed = failureShown();
        const stageWidth = failed ? Math.max(fittedWidth, Math.min(width, 560 + 32)) : fittedWidth;
        stage.style.width = `${stageWidth}px`;
        const stageHeight = failed ? Math.max(fittedHeight, failureNeed()) : fittedHeight;
        stage.style.height = `${stageHeight}px`;
        // The picture, its bar and the status line sit as one block in the middle of the column.
        previewSpace.style.height = `${stageHeight}px`;
        // The bar and the status line under the picture keep its width, so their ends line up with the picture's edges.
        output.style.setProperty('--fvs-stage-w', `${Math.round(stageWidth)}px`);
        // On a phone the picture block sits just above the console (no empty band between them); wider windows centre it.
        const free = Math.max(0, Math.floor(room - stageHeight));
        const marginTop = fit?.grid ? 0 : phone?.matches ? free : Math.floor(free / 2);
        previewSpace.style.marginTop = `${marginTop}px`;
        // The next generation's empty frame stays whole while a tray floats over the picture's lower part: it is fitted
        // and centred in the part left free, and takes the full stage again when the tray closes (200 ms).
        stage.style.translate = '';
        // (A tray shown as a small window beside its button, glass/tray_pop.js, floats over the stage and takes no room.)
        const openTray = nextShown && !failed && body.dataset.traySide !== 'true' ? controls.querySelector('.fvs-tray[open]:not([data-closing]):not([data-fv-pop])') : null;
        if (openTray) {
            const spaceTop = output.getBoundingClientRect().top + parseFloat(style.paddingTop) + marginTop;
            const freeHeight = Math.min(fittedHeight, openTray.getBoundingClientRect().top - spaceTop - 16);
            if (freeHeight > 48 && freeHeight < fittedHeight) {
                const h2 = freeHeight, w2 = Math.min(fittedWidth, h2 * previewRatio), hh = w2 / previewRatio;
                stage.style.width = `${w2}px`; stage.style.height = `${hh}px`;
                stage.style.translate = `0 ${Math.round((freeHeight - fittedHeight) / 2)}px`;
            }
        }
        stage.dataset.narrow = String((parseFloat(stage.style.width) || stageWidth) < 220);
        // The history rail centres on the picture and is never taller than it (read from the layout, not the
        // animated boxes).
        const pictureTop = output.getBoundingClientRect().top - body.getBoundingClientRect().top + parseFloat(style.paddingTop) + marginTop;
        body.style.setProperty('--fvs-stage-top', `${Math.round(pictureTop)}px`);
        body.style.setProperty('--fvs-stage-h', `${Math.round(stageHeight)}px`);
        if (fit?.grid) body.style.setProperty('--fvs-grid-top', `${Math.round(pictureTop + stageHeight + fit.barRoom + 16)}px`);
        else body.style.removeProperty('--fvs-grid-top');
        // The console and the picture are either the same width or clearly apart (at least 24 px a side), never a
        // few pixels off.
        if (!phone?.matches) {
            const dock = getComputedStyle(controls), edge = parseFloat(dock.getPropertyValue('--fvs-edge')) || 16;
            const room = body.clientWidth - 2 * edge, preset = dock.getPropertyValue('--fvs-dock-w').trim();
            const natural = Math.min(preset.endsWith('px') ? parseFloat(preset) : room, room);
            // The console takes the picture's width when a window or picture shape is first fitted, and lets go for good
            // (until that shape changes) once they are 48 px apart: the console's own height moves the picture (the room
            // left under it), so a row of chips right at a wrap would otherwise flip it between the two widths every frame.
            // A failure card widens the picture, so the console is matched to the picture as shown, and fitted afresh
            // when the card comes or goes.
            const shape = `${body.clientWidth}x${body.clientHeight}:${previewRatio}:${failed}`;
            if (consoleSnap.shape !== shape) consoleSnap = {shape, held: true};
            consoleSnap.held &&= Math.abs(stageWidth - natural) < 48 && stageWidth <= room;
            const same = consoleSnap.held;
            if (same) controls.style.setProperty('--fvs-console-w', `${Math.round(stageWidth)}px`); else controls.style.removeProperty('--fvs-console-w');
        } else controls.style.removeProperty('--fvs-console-w');
        stage.dataset.short = String(fittedHeight < 150);
        // The history rail centres itself on the stage, above the console.
        body.style.setProperty('--fvs-dock-h', `${controls.offsetHeight}px`);
        // On phones the shared toast strip sits in that slot, just above the console (the view's bottom bar).
        dialog.style.setProperty('--fv-bottombar-top', `${Math.max(0, Math.round(window.innerHeight - controls.getBoundingClientRect().top))}px`);
        placeTray();
    }
    function previewSize(w, h) {
        if (!(w > 0 && h > 0)) return;
        previewRatio = w / h; ratioStyle();
        fitPreview();
    }
    function dimensionText(element, text) {
        if (element.textContent === text) return;
        const animate = element.textContent && !matchMedia('(prefers-reduced-motion: reduce)').matches;
        element.textContent = text;
        if (animate && element.animate) {
            for (const animation of element.getAnimations()) animation.cancel();
            element.animate([{opacity: .25, transform: 'translateY(3px)'}, {opacity: 1, transform: 'translateY(0)'}],
                {duration: 280, easing: 'ease-out'});
        }
    }
    const previewObserver = new ResizeObserver(fitPreview);
    previewObserver.observe(output); cleanup.push(() => previewObserver.disconnect());
    function showProgress(message) {
        phoneAccess.observe(message);
        // The bar under the picture says nothing about the run (the top bar's task shows it and its place).
        summaryFor = null; stageBar.setSummary('');
        watchedRun = true; reportTipFor = null; clearTimeout(reportTipTimer);
        reuseRow.hidden = true;
        stats.hidden = true;
        budget.textContent = '';
        links.replaceChildren();
        delete progress.report.dataset.covered;
        prewarm.textContent = '';
        clearTimeout(revealTimer); revealTimer = null; delete stage.dataset.revealing;
        const appearing = progress.element.hidden;
        if (appearing) stage.querySelector('video')?.pause();
        progress.update(message);
        if (appearing) fitPreview();
    }
    const status = el('div', '', 'fv-status'); status.setAttribute('role', 'status');
    // A fault in the status line carries the report button; the next text written to the line removes it.
    // The run's diagnostic report: errors beside it go the report route instead of the feedback form.
    const reportRoute = {has: () => progress.hasReport(), download: () => progress.downloadReport(), fetch: progress.fetchReport, version: productVersion};
    const statusIssue = issueButton(t, () => ({where: ['Generate', '生成'], message: status.firstChild?.textContent}), {className: 'fv-status-issue', route: reportRoute});
    function fault(text) { status.dataset.error = 'true'; status.textContent = text; status.append(statusIssue); }
    const stats = el('div', null, 'fv-stats'), budget = el('div', '', 'fv-budget'), links = el('div', null, 'fv-result-links');
    // New text in the status line replaces the report card under the result.
    let statusHeight = 0;
    const statusWatch = new MutationObserver(() => {
        // On a phone the picture makes room for the line's height (fitPreview); refit when that height changes.
        if (phone?.matches && status.offsetHeight !== statusHeight) { statusHeight = status.offsetHeight; fitPreview(); }
        if (!status.textContent.trim()) return; hideReportTip(links.querySelector('.fv-actions')); hideReportTip(status);
    });
    statusWatch.observe(status, {childList: true, characterData: true, subtree: true});
    cleanup.push(() => statusWatch.disconnect());
    let toastRoom = TOAST_SLOT;
    const onToastResize = () => { if (phone?.matches && toastSlot() !== toastRoom) { toastRoom = toastSlot(); fitPreview(); } };
    addEventListener('freevideo-toast-resize', onToastResize);
    cleanup.push(() => removeEventListener('freevideo-toast-resize', onToastResize));
    stats.hidden = true;
    const metrics = [], metricLabels = [];
    for (const label of [t('Generation', '生成用时'), t('Total time', '总用时'), t('Peak VRAM', '最高显存'), t('Peak RAM', '最高内存')]) { const box = el('div', null, 'fv-stat'), n = el('strong', '—'), caption = el('span', label); box.append(n, caption); stats.append(box); metrics.push(n); metricLabels.push(caption); }
    const prewarm = el('div', node.freevideoPrewarm || '', 'fv-prewarm');
    const reuseRow = el('div', null, 'fv-reuse-result'); reuseRow.hidden = true;
    const reuseNotice = el('span', t('Reused previous result', '已复用上次结果'));
    const regenerate = button(t('Regenerate', '重新生成'), async () => {
        const saved = result;
        regenerate.disabled = true;
        try { await node.freevideoRegenerateResult(saved); regenerate.textContent = t('Queued', '已加入队列'); await syncQueue(); }
        catch (error) { regenerate.disabled = false; resultActionFailed(error); }
    }, 'fv-quiet');
    reuseRow.append(reuseNotice, regenerate);
    // The card's own diagnosis, so a known cause (e.g. out of GPU memory) keeps its advice.
    // createErrorReport caches by error, so the card shown next uses the same report.
    function extensionCause(error) { return !!createErrorReport(error, {stage: 'queue', node, graph: app.graph}).browser_extension; }
    // A browser extension gets the shared card; other failures stay in the status line.
    function resultActionFailed(error) {
        if (extensionCause(error)) { showQueueError(error); return; }
        fault(shownMessage(error));
    }
    const trimNote = el('div', '', 'fv-note fv-trim-note'); trimNote.hidden = true; trimNote.setAttribute('role', 'status');
    // Run second pass is the preview's primary action in the result bar.
    const secondPass = saved => async () => {
        try { await node.freevideoUpscaleResult(saved); await syncQueue(); }
        catch (error) { resultActionFailed(error); throw error; }
    };
    const memoryNote = el('div', t('This run used the low-memory mode, so it was slower. A lower resolution or a shorter video is faster.', '这次用了省显存方式，所以较慢。降低分辨率或缩短时长会更快。'), 'fv-note'); memoryNote.hidden = true;
    const thermalNote = el('div', '', 'fv-note'); thermalNote.hidden = true;
    const sharedGpuNote = el('div', '', 'fv-note'); sharedGpuNote.hidden = true;
    const encoderNote = el('div', '', 'fv-note'); encoderNote.hidden = true;
    const keepSoundNote = el('div', keepSoundText(t), 'fv-note'); keepSoundNote.hidden = true;
    output.append(status, reuseRow, progress.stall, progress.report, prewarm);
    // The actions join the bar under the picture; the numbers and notes go into its Details (one place to look).
    stageBar.end.append(links);
    stageBar.element.querySelector('.fvs-info-panel').prepend(stats, budget, trimNote, memoryNote, thermalNote, sharedGpuNote, keepSoundNote, encoderNote);
    stageBar.details = [stats, budget, trimNote, memoryNote, thermalNote, sharedGpuNote, keepSoundNote, encoderNote];
    const failure = createErrorPanel(t, reportRoute);
    // The failure card opens on the picture it replaces (no new video was made), so it is seen without scrolling;
    // its close button puts the earlier picture back, and the next run clears it anyway.
    const failureLayer = el('div', null, 'fvs-failure-layer'); failureLayer.hidden = true;
    const failureClose = button('', () => { failureLayer.hidden = true; }, 'fvs-icon-button fvs-failure-close');
    failureClose.append(glyph('close', 14)); failureClose.setAttribute('aria-label', t('Close', '关闭'));
    tip(failureClose, t('Close', '关闭'), t('Hide the card and show the picture', '收起这张卡片，显示画面'));
    failureLayer.append(failure.element, failureClose); stage.append(failureLayer);
    failureShown = () => !failureLayer.hidden;
    // The card's whole height (its content, border and the layer's padding), read at the picture's final width.
    failureNeed = () => { const card = failure.element, pad = parseFloat(getComputedStyle(failureLayer).paddingTop) || 0;
        return Math.ceil(card.scrollHeight + card.offsetHeight - card.clientHeight + 2 * pad); };
    let failureFit = 0;
    const refitFailure = () => { if (!failureFit) failureFit = requestAnimationFrame(() => { failureFit = 0; fitPreview(); }); };
    cleanup.push(() => cancelAnimationFrame(failureFit));
    const failureWatch = new MutationObserver(() => {
        const hidden = failure.element.hidden;
        if (failureLayer.hidden !== hidden) failureLayer.hidden = hidden;
        refitFailure();
    });
    failureWatch.observe(failure.element, {attributes: true, attributeFilter: ['hidden'], childList: true, subtree: true, characterData: true});
    cleanup.push(() => failureWatch.disconnect());
    // Opening its details changes the card's height; a width change is read again once the picture has finished it.
    failure.element.addEventListener('toggle', refitFailure, true);
    stage.addEventListener('transitionend', event => { if (event.target === stage && event.propertyName === 'width' && failureShown()) refitFailure(); });
    const layerWatch = new MutationObserver(refitFailure);
    layerWatch.observe(failureLayer, {attributes: true, attributeFilter: ['hidden']}); cleanup.push(() => layerWatch.disconnect());
    // With the picture, the result's actions and numbers form one block, centred below the header.
    output.append(el('div', null, 'fv-preview-end'));
    if (node.freevideoFailure) failure.show(node.freevideoFailureReport || node.freevideoFailure, false);
    failureLayer.hidden = failure.element.hidden;
    let result = node.freevideoLastResult || app.nodeOutputs?.[node.id]?.freevideo_summary?.[0] || lastShown.get(node) || recallShown();
    // The history stays beside the picture as a rail of thumbnails; Creations opens the full library from its top.
    // Compare (F9): the video on screen against another one, on the stage; a card picked in the rail becomes the other.
    const savedVideo = video => /^FreeVideo\/\d{4}-\d{2}-\d{2}\/[0-9a-f]{32}\/video\.mp4$/.test(video || '');
    const compare = createCompare({t, host: stage,
        source: row => ({src: view(row.video, 'output') + (row.version ? `&fv=${encodeURIComponent(row.version)}` : ''), poster: savedVideo(row.video) && !row.preview ? videoThumbnailURL(row) : ''}),
        // The two sides are A and B only: nothing about the works is written on them.
        describe: () => '',
        onChange: active => { stage.dataset.comparing = String(active); compareButton.setAttribute('aria-pressed', String(active)); if (!active) recent?.clearPicks({quiet: true}); }});
    cleanup.push(() => compare.dispose());
    const compareButton = button('', () => {
        if (compare.active) { compare.close(); return; }
        const rows = recent.rows(), index = recent.index();
        const other = rows[index + 1] || rows[index - 1] || rows.find(row => row?.video !== result?.video);
        if (!result?.video || !other) return;
        stageMedia.querySelector('video')?.pause();
        compare.open(result, other);
    }, 'fvs-icon-button fvs-compare-open');
    compareButton.innerHTML = `<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true">${COMPARE_ICON}</svg>`;
    compareButton.setAttribute('aria-label', t('Compare', '对比'));
    tip(compareButton, t('Compare', '对比'), t('Side by side or with a divider, with another video', '和另一条视频并排或滑动对比'));
    // No Compare button on the bar: two videos picked together in the rail compare on the stage (F9 still opens it).
    let shownCount = 0;
    const recent = createRecentVideos(t, {body, rail: true, named: row => savedPrompt(api, row),
        onSelect: row => { if (compare.active) compare.close(); showResult(row, {chosen: true}); },
        onPick: rows => {
            if (rows.length === 2) { if (compare.active) compare.close(); compare.open(rows[0], rows[1]); }
            else { if (compare.active) compare.close(); if (rows[0]) showResult(rows[0], {chosen: true}); }
        },
        onChange: () => { syncHistory(); const count = recent.rows().length; if (count !== shownCount) { shownCount = count; fitPreview(); } }});
    recentCount = () => recent.rows().length;
    recent.element.classList.add('sgf-avoid');
    recent.element.prepend(worksButton); body.append(recent.element);
    // Phones have no rail; Creations is the top bar's works entry, there at every width.
    function syncHistory() {
        stageBar.element.dataset.empty = String(!result?.video);
        stageBar.setHistory(recent.index(), recent.rows().length);
        compareButton.disabled = !result?.video || recent.rows().length < 2;
        compareButton.dataset.fvTipWhy = compareButton.disabled ? t('Needs a second video in the history', '历史里还要有另一条视频') : '';
        if (!compareButton.disabled) delete compareButton.dataset.fvTipWhy;
    }
    function stepHistory(delta) {
        const rows = recent.rows(), index = recent.index(), row = rows[index + delta];
        if (row) showResult(row, {chosen: true});
    }
    cleanup.push(() => recent.dispose());
    // A run this window watched. Its result can arrive before or after the
    // final progress message, and more than once; the tip stays with that video.
    // "Reuse settings" in the result bar: what the panel holds now, filling an earlier video's settings back,
    // and the edits that withdraw the offer. A field the panel cannot change counts as already matching.
    let reuseApplying = false;
    function setSize(w, h) {
        set(node, 'width', w); set(node, 'height', h);
        ratio = w / h; pixels = w * h / 1024 ** 2;
        selected = [['16:9', 16 / 9], ['9:16', 9 / 16], ['1:1', 1]].find(([, r]) => Math.abs(Math.log(ratio / r)) < .035)?.[0] || 'custom';
        node.properties.freevideo_aspect = selected; node.properties.freevideo_pixels = pixels;
        if (![...mp.options].some(option => Number(option.value) === pixels)) { const option = el('option', `${pixels.toFixed(2)} MP`); option.value = pixels; mp.append(option); }
        mp.value = String(pixels); syncCanvasSize();
    }
    const reuse = {
        now: saved => {
            const sizeLocked = dimensionsLinked || Boolean(extension.source), lengthLocked = linked(node, 'seconds') || Boolean(extension.source);
            return {text: prompt.disabled ? saved.text : requestText(prompt.value, node.properties?.freevideo_prompt_versions), width: sizeLocked ? saved.width : Number(value(node, 'width')),
                height: sizeLocked ? saved.height : Number(value(node, 'height')),
                frames: lengthLocked ? saved.frames : framesOf(seconds.value), seconds: Number(seconds.value),
                seed: linked(node, 'seed') || !widget(node, 'seed') ? saved.seed ?? null : Number(value(node, 'seed'))};
        },
        apply: saved => {
            reuseApplying = true;
            try {
                if (!prompt.disabled) { set(node, 'text', saved.text); window.dispatchEvent(new CustomEvent('freevideo-reference-prompt', {detail: node.id})); }
                if (!dimensionsLinked && !extension.source) setSize(saved.width, saved.height);
                if (saved.seconds !== null && !linked(node, 'seconds') && !extension.source) {
                    seconds.value = String(saved.seconds);
                    seconds.dispatchEvent(new Event('input')); seconds.dispatchEvent(new Event('change'));
                }
                // The next run uses this seed; after it, the panel's own "after generating" choice moves it as usual.
                if (saved.seed != null && !linked(node, 'seed') && widget(node, 'seed')) { set(node, 'seed', saved.seed); seed.value = String(saved.seed); }
            } finally { reuseApplying = false; }
        },
        onEdit: listener => {
            const heard = () => { if (!reuseApplying) listener(); };
            controls.addEventListener('input', heard, true); controls.addEventListener('change', heard, true);
            return () => { controls.removeEventListener('input', heard, true); controls.removeEventListener('change', heard, true); };
        },
    };
    // The library opened from elsewhere hands Reuse settings to this panel once it is in front
    // (window 'freevideo-command', id 'settings.reuse'; it falls back to filling the prompt when nobody answers).
    const reuseCommand = event => {
        const detail = event.detail;
        if (detail?.id !== 'settings.reuse' || detail.handled || !detail.saved || !dialog.open) return;
        reuse.apply(detail.saved); detail.handled = true;
    };
    window.addEventListener('freevideo-command', reuseCommand); cleanup.push(() => window.removeEventListener('freevideo-command', reuseCommand));
    let reportTipTimer = null, watchedRun = false, reportTipFor = null, shownReport = null, freshVideo = null;
    // chosen: a video picked from Recent videos. It only changes the picture; a run in progress keeps its progress.
    function showResult(r, {chosen = false} = {}) {
        if (!r?.video || disposed) return; result = r; leaveNext();
        recent.setSelected(r.video); syncPartList();
        refreshEffortEstimate(true);
        // The report belongs to the video that run made; a video picked from Recent videos
        // shows its own report, or none when this window never saw its run.
        const reports = node.freevideoVideoReports || (node.freevideoVideoReports = new Map());
        if (!chosen && node.freevideoReportId) reports.set(r.video, node.freevideoReportId);
        const wanted = recordReportId(r, reports.get(r.video));
        if (!(chosen && busy) && wanted !== shownReport) {
            shownReport = wanted; progress.updateReport({new_request: true});
            if (wanted) progress.updateReport({report_id: wanted});
        }
        if (wanted && !(chosen && busy)) progress.pinReport();
        // The stage shows the video's own first frame, never the small thumbnail blown up: the video loads at once and
        // takes the stage in one step when its first frame is decoded. Until then the last clear picture stays (another
        // work's video, on top), or the stage's plain colour on a first open. Only over a slow link does the thumbnail
        // stand in: after 400 ms on a plain stage, 1 s over a kept picture; it goes as soon as the frame is there (the
        // browser would keep a poster up until Play). The engine's thumbnail is the first frame, so the still is frame 0
        // and Play starts from the beginning.
        stage.querySelector('video')?.pause(); for (const old of stageMedia.querySelectorAll('video')) old.pause();
        playerBar?.detach(); playerBar = null;
        const kept = [...stageMedia.querySelectorAll('video')].filter(v => v.readyState >= 2 && v.videoWidth && !v.dataset.pending).at(-1) || null;
        for (const child of [...stageMedia.children]) if (child !== kept) child.remove();
        if (kept) { kept.dataset.leaving = 'true'; kept.controls = false; kept.removeAttribute('controls'); kept.tabIndex = -1; }
        const video = el('video'); video.src = view(r.video, 'output') + (r.version ? `&fv=${encodeURIComponent(r.version)}` : ''); video.controls = true; video.preload = 'auto'; video.playsInline = true;
        video.controlsList?.add?.('nodownload', 'noplaybackrate', 'noremoteplayback'); video.disablePictureInPicture = true; video.disableRemotePlayback = true;
        const cover = /^FreeVideo\/\d{4}-\d{2}-\d{2}\/[0-9a-f]{32}\/video\.mp4$/.test(r.video) && !r.preview ? videoThumbnailURL(r) : '';
        video.dataset.pending = 'true'; stageMedia.prepend(video); lastShown.set(node, r); rememberShown(r);
        // The video on the stage goes before anything the page loads ahead in the background (work_media.js).
        mediaQueue().open(video.getAttribute('src'), video);
        playerBar = attachPlayer(video, playerRow, {t});
        // A click on the picture plays or pauses it, as the play bar's key does.
        video.addEventListener('click', event => {
            if (event.button !== 0 || video.dataset.leaving || !video.isConnected) return;
            if (video.paused) video.play().catch(() => {}); else video.pause();
        });
        const take = () => { if (!video.dataset.pending) return; delete video.dataset.pending; if (kept?.isConnected) kept.remove(); };
        // The view reports ready once the first frame is there, so the first frame the shell fades in is the picture;
        // over a slow link it waits up to 700 ms (inside the shell's 800 ms), then fades in the stage's plain colour.
        let done = false; const ready = () => { if (!done) { done = true; markReady(); } };
        video.addEventListener('loadeddata', () => { video.removeAttribute('poster'); take(); ready(); }, {once: true});
        // The stage wraps the video itself: when the work's recorded size is missing or does not match the file (an old
        // or imported work), it takes the video's own shape once that is known.
        video.addEventListener('loadedmetadata', () => {
            if (disposed || result !== r || nextShown || !video.isConnected || !(video.videoWidth > 0 && video.videoHeight > 0)) return;
            const own = video.videoWidth / video.videoHeight;
            if (!(Math.abs(own - previewRatio) <= own * .01)) previewSize(video.videoWidth, video.videoHeight);
        }, {once: true});
        setTimeout(() => { if (disposed || !video.isConnected || video.readyState >= 2) return; if (cover) video.poster = cover; take(); }, kept ? 1000 : 400);
        setTimeout(ready, 700);
        video.addEventListener('error', () => {
            if (!disposed && result === r && video.isConnected) {
                stageMedia.replaceChildren(el('p', videoUnavailableText(t), 'fv-note fv-video-problem'));
                actionsUnavailable(links.querySelector('.fv-actions'));
                if (r.preview) stageMedia.append(previewBadge(r, t));
            }
        }, {once: true});
        // A preview is marked on the picture itself; its timings describe only
        // the half-resolution first pass, so they stay in the report.
        if (r.preview) stageMedia.append(previewBadge(r, t));
        stats.hidden = !!r.result_cache_hit || !!r.preview;
        showSummary(r);
        reuseRow.hidden = !r.result_cache_hit;
        const trims = Array.isArray(r.reference_trims) ? r.reference_trims : [];
        trimNote.textContent = trims.map(row => referenceTrimText(row, t)).join('\n'); trimNote.hidden = !trims.length;
        memoryNote.hidden = !r.low_memory || !!r.result_cache_hit;
        thermalNote.textContent = r.thermal ? thermalText(r.thermal, t) : ''; thermalNote.hidden = !r.thermal || !!r.result_cache_hit;
        sharedGpuNote.textContent = r.shared_gpu ? sharedGpuText(r.shared_gpu, t) : ''; sharedGpuNote.hidden = !sharedGpuNote.textContent || !!r.result_cache_hit;
        keepSoundNote.hidden = !r.keep_source_audio_failed;
        encoderNote.textContent = r.encoder_file ? encoderFileText(r.encoder_file, t) : ''; encoderNote.hidden = !r.encoder_file;
        const encoderPath = encoderFilePath(r.encoder_file);
        if (encoderPath) encoderNote.append(el('pre', encoderPath, 'fv-encoder-path'));
        regenerate.disabled = false; regenerate.textContent = t('Regenerate', '重新生成');
        const unified = r.memory_model === 'unified';
        metricLabels[2].textContent = unified ? t('Unified memory', '统一内存总量') : t('Peak VRAM', '最高显存');
        metricLabels[3].textContent = unified ? t('Process peak RAM', '进程最高内存') : t('Peak RAM', '最高内存');
        const shown = [number(r.sample_seconds), number(r.request_seconds), number(unified ? r.unified_total_bytes : r.vram_peak_bytes, 2 ** 30, 'GiB'), number(r.ram_peak_bytes, 2 ** 30, 'GiB')]; metrics.forEach((e, i) => { e.textContent = shown[i]; e.parentElement.hidden = shown[i] === '—'; });
        // Cells without a number are left out, and the rest share the width; none at all hides the strip.
        const present = shown.filter(value => value !== '—').length;
        stats.dataset.count = String(present);
        if (!present) stats.hidden = true;
        budget.textContent = r.result_cache_hit || r.preview ? '' : unified
            ? (Number.isFinite(r.unified_reserve_bytes) ? `${t('Reserved unified memory', '预留统一内存')} ${number(r.unified_reserve_bytes, 2 ** 30, 'GiB')}` : '')
            : (Number.isFinite(r.gpu_budget_bytes) ? `${t('VRAM budget', '可用显存预算')} ${number(r.gpu_budget_bytes, 2 ** 30, 'GiB')} · ${t('Device', '显卡总量')} ${number(r.gpu_total_bytes, 2 ** 30, 'GiB')}` : '');
        // One report entry: the diagnostic export joins the report menu once a result exists.
        progress.report.dataset.covered = 'true';
        const reported = progress.hasReport();
        const tipShowing = !!links.querySelector('.fv-report-tip:not(.fv-report-tip-leaving)');
        links.replaceChildren(resultActions(r, t, {diagnostic: reported ? downloadReport : null,
                                                   extend: saved => openExtendEditor(saved, node, {studio: dialog,
                                                       isCurrent: () => dialog.open && dialog.dataset.closing !== 'true' && topDialog() === dialog && result === r}),
                                                   secondPass: r.preview ? secondPass(r) : null, reuse: r.preview ? null : reuse, layout: 'stage', more: [guideItem()]}));
        if (runButtonReady) updateExtendButton();
        stageBar.sync(); syncHistory();
        // After a video finished just now, point at the report menu until the
        // user closes the tip once, whichever version they first saw it in.
        if (!chosen && watchedRun && reported && !r.preview && !r.result_cache_hit && !reportTipClosed()) reportTipFor = r.video;
        // A video this window just watched being made: settings trays open beside it, not over it.
        if (!chosen && watchedRun) freshVideo = r.video;
        if (!chosen) watchedRun = false;
        if (reportTipFor && reportTipFor === r.video) placeReportTip(tipShowing ? 0 : 650);
        const g = r.geometry; if (g?.width && g?.height) previewSize(g.width, g.height);
        if (!chosen && !progress.element.hidden) {
            progress.update({phase: 'complete', ...(r.preview ? {label: 'Preview saved'} : {}),
                overall: {status: 'complete', fraction: 1, remaining_seconds: 0}});
            stage.dataset.revealing = 'true';
            clearTimeout(revealTimer); revealTimer = setTimeout(hideProgress, 550);
        }
    }
    const runbar = el('div', null, 'fv-runbar');
    let busy = false, queued = false, cancelling = false, activePrompt = null, capturing = false;
    // What the bar under the picture says while a video is being made: its size and length.
    let runningSummary = '';
    // The queue lists only this page's own requests. A video started elsewhere (another tab, a
    // reloaded page) is still running on the server and reports progress here, so the button must
    // not fall back to "Generate video" between two reports, e.g. while decoding.
    const LIVE_PROGRESS_MS = 120000;
    let liveProgressAt = 0;
    const progressLive = () => Date.now() - liveProgressAt < LIVE_PROGRESS_MS;
    let runMode = ['single', 'batch', 'loop'].includes(node.properties?.freevideo_run_mode)
        ? node.properties.freevideo_run_mode : 'single', queueState;
    if (!studioQueues.has(node)) studioQueues.set(node, createStudioQueue(api, node.id, {
        formatError: (error, snapshot) => createErrorReport(error, {stage: 'submission', snapshot, node, graph: app.graph}),
        // Direct snapshot submission still feeds the same graph result panel.
        onResult: (output, requestId) => {
            if (node.freevideoStudioChain?.isBlockOutput({...output.freevideo_summary[0], request_id: requestId})) return;
            output = {...output, freevideo_summary: [{...output.freevideo_summary[0], request_id: requestId}]};
            if (app.graph?.getNodeById(node.id) !== node) { node.freevideoLastResult = output.freevideo_summary[0]; return; }
            app.nodeOutputs = {...app.nodeOutputs, [node.id]: output};
            if (node.freevideoLastResult?.request_id !== requestId) node.freevideoShowResult?.(output);
        },
    }));
    const queueController = studioQueues.get(node);
    let chainWasActive = false, stopNote = '';
    chainController = createStudioChain({api, node, onProgress: (message, chain) => {
            const position = chainPosition(chain, message);
            const joining = readChainResult(chain).joining, waiting = chain.state === 'queued' && message.phase === 'prepare';
            setChainLine(joining ? joiningText(chain.joining_part, t)
                : waiting ? (chain.waiting ? waitingText(chain.waiting, t) : t(`Waiting in queue · Part ${position.index + 1} of ${position.count}`, `正在排队 · 第 ${position.index + 1} 段，共 ${position.count} 段`))
                : t(`Part ${position.index + 1} of ${position.count}`, `第 ${position.index + 1} 段，共 ${position.count} 段`));
            progress.element.querySelector('.fv-generation-label').hidden = joining || waiting;
            progress.element.classList.toggle('fv-generation-waiting', waiting);
            progress.updateReport(message); showProgress(message);
        },
        onState: chain => {
            const active = chainController.active, position = chainPosition(chain), outcome = readChainResult(chain);
            if (active && !chainWasActive) {
                failure.clear(); node.freevideoFailure = ''; node.freevideoFailureReport = null;
                lastQueueError = null; status.textContent = ''; status.dataset.error = 'false';
            }
            // Parts download what they need while they run; the line above Generate is read again when they stop.
            if (chainWasActive && !active) requestAssets?.refresh(true);
            chainWasActive = active;
            partList.update(chain, active, Boolean(result?.request_id) && result.request_id === chain?.id);
            busy = active || Boolean(queueState?.running || queueState?.pending.length); queued = chain?.state === 'queued';
            chainLine.hidden = !active;
            setChainLine(outcome?.joining ? joiningText(chain?.joining_part, t)
                : t(`Part ${position.index + 1} of ${position.count}`, `第 ${position.index + 1} 段，共 ${position.count} 段`));
            if (active && node.properties.freevideo_chain?.width) previewSize(node.properties.freevideo_chain.width, node.properties.freevideo_chain.height);
            if (!active) { cancelling = false; hideProgress(); }
            if (!active) { progress.element.querySelector('.fv-generation-label').hidden = false; progress.element.classList.remove('fv-generation-waiting'); }
            if (chain?.state === 'cancelled') status.textContent = t('Cancelled', '已取消');
            // After a stop with videos queued the next one starts at once; its line says so until something else is done.
            if (stopNote) { status.dataset.error = 'false'; status.textContent = stopNote; }
            syncResume();
            updateRunButton();
        },
        onResult: (result, outcome) => { node.freevideoLastResult = result; showResult(result, {chosen: outcome.partial});
            if (!outcome.partial) firstStart?.complete(result);
            // A part that failed keeps its sentence; the part list already marks which ones finished.
            if (outcome.partial && !outcome.joined && status.dataset.error !== 'true') status.textContent = t('Showing the last completed part.', '正在显示最后完成的一段。'); },
        onError: showPartError});
    cleanup.push(() => chainController.dispose());
    node.freevideoStudioChain = chainController;
    node.freevideoEditorLooping = () => queueController.state().looping;
    if (!node.freevideoQueueCleanup) {
        node.freevideoQueueCleanup = true;
        const removed = node.onRemoved;
        node.onRemoved = function (...args) {
            queueController.stopLoop().catch(() => {}).finally(() => queueController.dispose());
            return removed?.apply(this, args);
        };
    }
    // The run mode lives in the Advanced tray (glass/advanced_tray.js), unless that module could not load.
    const runrow = el('div', null, document.documentElement.dataset.fvAt === 'failed' ? 'fv-runrow' : 'fv-runrow fv-at-moved'); cleanup.push(dismissEmptyPrompt);
    const runOptions = el('details', null, 'fv-run-options');
    const optionsToggle = el('summary'); optionsToggle.title = t('Generation mode', '生成方式');
    const optionsLabel = el('span', '', 'fv-run-mode-label');
    optionsToggle.setAttribute('aria-label', optionsToggle.title);
    optionsToggle.append(optionsLabel, el('span', null, 'fv-chevron'));
    tip(optionsToggle, t('Generation mode', '生成方式'), t('Once, a batch, or a loop', '单次、批量或循环生成'));
    const optionsPanel = el('div', null, 'fv-run-menu');
    optionsPanel.append(el('strong', t('Generation mode', '生成方式')));
    const modeButtons = [];
    for (const [id, label] of [['single', t('Single', '单次')], ['batch', t('Batch', '批量')], ['loop', t('Loop', '循环')]]) {
        const b = button(label, () => {
            runMode = id; node.properties.freevideo_run_mode = id;
            rememberPromptDraft(api, node); updateRunButton();
        });
        b.dataset.mode = id; modeButtons.push(b); optionsPanel.append(b);
    }
    const count = el('input'); count.type = 'number'; count.min = '2'; count.max = '100'; count.step = '1';
    count.value = node.properties?.freevideo_batch_count ?? 4;
    count.setAttribute('aria-label', t('Number of videos', '生成数量'));
    const countField = field(t('Number of videos', '生成数量'), count); optionsPanel.append(countField);
    // One prompt per line: each non-empty line of the description is its own video, so several ideas queue at once.
    const perLine = el('input'); perLine.type = 'checkbox'; perLine.setAttribute('role', 'switch');
    perLine.checked = node.properties?.freevideo_batch_lines === true;
    const perLineField = el('label', null, 'fv-batch-lines');
    perLineField.append(el('span', t('One prompt per line', '每行一条描述')), perLine); optionsPanel.append(perLineField);
    const linesUsable = () => !linked(node, 'text') && !linked(node, 'conditioning');
    const byLine = () => runMode === 'batch' && perLine.checked && linesUsable();
    const lineTexts = () => promptLines(shown(value(node, 'text')), (node.properties?.[LORA_TRIGGERS] || []).map(row => row?.words));
    prompt.addEventListener('input', () => { if (byLine()) updateRunButton(); });
    perLine.onchange = () => { node.properties.freevideo_batch_lines = perLine.checked; rememberPromptDraft(api, node); updateRunButton(); };
    const modeHelp = el('p', '', 'fv-muted'); optionsPanel.append(modeHelp);
    count.oninput = () => {
        if (count.value && count.checkValidity()) {
            node.properties.freevideo_batch_count = Number(count.value); rememberPromptDraft(api, node);
        }
        updateRunButton();
    };
    // A count past 2–100 becomes that end, and the line under the field says so.
    const countNote = fieldNote(count, {t, place: () => countField}); fieldNotes.set(count, countNote);
    count.onchange = () => { if (countNote.settle()) count.oninput(); };
    runOptions.append(optionsToggle, optionsPanel);
    runOptions.addEventListener('toggle', () => { if (runOptions.open) updateRunButton(); });
    // The run-mode menu opens in the top layer above its button, so it never sits under the prompt's tools.
    liftMenu(runOptions, optionsPanel);
    const generate = button('', submitDraft, 'fv-primary');
    generate.dataset.fvGuide = 'generate'; generate.dataset.tourId = 'generate-btn';
    // Generate reads as one key: what it does, and under it about how long the video takes (the quality's estimate).
    const keyLabel = el('span', null, 'fvs-key-label'), keyEta = el('span', null, 'fvs-key-eta');
    let keyShowsEta = false, keySetup = false;
    // A one-time note (the first build) is not a standing line: it goes to the key's hover card instead.
    const setupPart = part => /首次准备|first.time setup/i.test(part.replace(/\u2060/g, '').replace(/\u00a0/g, ' '));
    const paintEta = () => {
        const parts = (effort?.element?.querySelector('.fv-effort-estimate')?.textContent || '').trim().split(' · ').filter(Boolean);
        keySetup = parts.some(setupPart);
        // A preview has no estimate of its own (the figures are for the whole two-pass run), so the line stays empty rather than show the wrong time.
        const estimate = keyShowsEta && !(previewFirst.checked && !previewFirst.disabled) ? parts.filter(part => !setupPart(part)).join(' · ') : '';
        if (keyEta.textContent !== estimate) keyEta.textContent = estimate;
        // Kept in place when empty (not hidden), so the label above it never moves.
        keyEta.dataset.empty = String(!estimate);
    };
    const paintKey = (label, eta) => {
        if (generate.firstChild !== keyLabel || generate.lastChild !== keyEta) generate.replaceChildren(keyLabel, keyEta);
        if (keyLabel.textContent !== label) keyLabel.textContent = label;
        keyShowsEta = eta; paintEta();
    };
    // The key has its two lines from the first frame, so its label is in place before anything else is known.
    paintKey(t('Generate video', '生成视频'), true);
    // Generate's second segment: the menu that says what the key makes, a video or a preview (the same choice as the
    // two-pass state; a preview turns two-pass on).
    const keyMore = el('details', null, 'fv-key-more');
    const keyMoreToggle = el('summary'); keyMoreToggle.title = t('What to generate', '生成什么'); keyMoreToggle.setAttribute('aria-label', keyMoreToggle.title);
    keyMoreToggle.append(el('span', null, 'fv-chevron'));
    const keyMenu = el('div', null, 'fv-run-menu fv-key-menu');
    const keyChoices = [['video', t('Generate video', '生成视频')], ['preview', t('Generate preview', '生成预览')]].map(([id, label]) => {
        const b = button(label, () => { choosePassMode(id === 'preview' ? 'preview' : twoPass.checked ? 'on' : 'off'); keyMore.open = false; });
        b.dataset.choice = id; keyMenu.append(b); return b;
    });
    keyMore.append(keyMoreToggle, keyMenu);
    liftMenu(keyMore, keyMenu);
    const syncKeyMore = () => {
        const state = passState();
        keyMore.hidden = runMode !== 'single' || videoMissing || Boolean(state.hidden.preview) || Boolean(extension.source);
        for (const b of keyChoices) setAttr(b, 'aria-pressed', String((b.dataset.choice === 'preview') === (state.mode === 'preview')));
        keyChoices[1].disabled = state.disabled.preview && !(state.mode === 'preview');
        if (keyMore.hidden) keyMore.open = false;
    };
    runrow.append(generate, runOptions, keyMore);
    runButtonReady = true;
    // An installation with MiniMax H3 Image alone: the main button adds the video model instead of generating.
    let videoMissing = false;
    const addVideoNote = el('p', '', 'fv-add-video-note'); addVideoNote.hidden = true;
    addVideoNote.setAttribute('role', 'status');
    // A length the device cannot make greys the main button: the reason and the next step are said beside it, not only on hover.
    const lengthWhy = el('p', '', 'fv-length-why'); lengthWhy.hidden = true;
    lengthWhy.setAttribute('role', 'status');
    videoModelState((path, options) => api.fetchApi(path, options)).then(state => {
        if (disposed || state.installed) return;
        videoMissing = true;
        if (!status.textContent.trim()) status.textContent = missingVideoText(t);
        updateRunButton();
    });
    const queueActions = el('div', null, 'fv-queue-actions');
    const queueDetails = el('details', null, 'fv-queue-details');
    const queueToggle = el('summary'), queueList = el('div', null, 'fv-queue-list');
    queueDetails.append(queueToggle, queueList);
    // Stopping asks first (the floating window's confirm, anchored on this button); leaving the view never stops.
    const cancelCurrent = button(t('Cancel current', '取消当前'), async () => {
        if (cancelling || !await confirmStop(cancelCurrent, {kind: 'video'})) return;
        await cancelGeneration();
    }, 'fv-quiet');
    tip(cancelCurrent, t('Cancel current', '取消当前'), t('Stop this video; the queue goes on', '停止这一条，排队的照常进行'));
    const resumeChain = button(t('Continue generating', '继续生成'), async () => {
        resumeChain.disabled = true; failure.clear();
        try { await chainController.resume(); } catch (error) { showQueueError(error); }
        finally { resumeChain.disabled = false; }
    }, 'fv-quiet'); resumeChain.hidden = true;
    tip(resumeChain, t('Continue generating', '继续生成'), t('Pick up the long video where it stopped', '从停下的位置接着生成长视频'));
    const stopLoop = button(t('Stop after this video', '完成当前后停止'), async () => {
        stopLoop.disabled = true;
        try { await queueController.stopLoop(); }
        catch (error) { showQueueError(error); }
        finally { stopLoop.disabled = false; }
    }, 'fv-quiet');
    // The queue (each video with its place, Move to front, Remove) and Cancel are in the top bar's task panel
    // (topbar.js); under the picture only a loop's stop and a stopped long video's Continue remain, while they apply.
    queueActions.append(stopLoop, resumeChain);
    tip(stopLoop, t('Stop after this video', '完成当前后停止'), t('Finish this one, then start no more', '做完这一个就不再继续'));
    tip(queueToggle, t('Queue', '队列'), t('See, reorder or remove waiting videos', '查看、调整或移除排队的视频'));
    syncResume = () => {
        resumeChain.hidden = !['cancelled', 'failed', 'interrupted'].includes(chainController?.state?.state) || partList.resumes();
        queueActions.hidden = stopLoop.hidden && resumeChain.hidden;
    };
    const otherUsage = createOtherUsageNotice(runbar, t, api.fetchApi.bind(api));
    cleanup.push(() => otherUsage.dispose());
    runbar.append(requestAssets.element, runrow, addVideoNote, queueActions); controls.append(runbar);
    // A chain action the server refused with its own sentence says that sentence, not the general "could not continue".
    const queueError = error => (error?.action && error.said ? t(error.said[0], error.said[1]) : null) || ({
        queue_unavailable: t('Could not read the queue. The loop has stopped; videos already added stay queued.', '暂时读不到队列，循环已停止；已加入的视频还在队列中。'),
        cancel_failed: t('Could not cancel. Try again.', '没有取消成功，请重试。'),
        move_failed: t('Could not move it. Try again.', '没有移动成功，请重试。'),
        submit_failed: t('Could not add to the queue. Check the settings in node view.', '没能加入队列，请在节点视图检查设置。'),
        loop_active: t('Stop the current loop before starting another.', '请先停止当前循环。'),
        loop_stopped: t('The last video did not finish. The loop has stopped.', '上一个视频没有生成完，循环已停止。'),
        linked_seed: t('Batch and loop use the seed in this panel. Disconnect the seed link in node view.', '批量和循环要用面板里的种子，请在节点视图断开种子的连线。'),
        missing_node: t('This node is disabled or no longer in the workflow.', '该节点已禁用或已从工作流移除。'),
        chain_cancel_failed: t('Could not cancel. Refresh the page and try again.', '没有取消成功，请刷新页面后再试。'),
        chain_resume_failed: t('Could not continue. Refresh the page and try again.', '没有继续生成，请刷新页面后再试。'),
    }[error?.message] || (error?.said ? t(error.said[0], error.said[1]) : null) || userMessage(error, t));
    let lastQueueError = null, connectionNotice = false;
    watchEngineExit(api).catch(() => {});
    function showQueueError(error) {
        if (disposed) return;
        status.dataset.error = 'true';
        // Polling and loop shutdown must not replace the original exception
        // with a generic "loop stopped" notice or reopen a dismissed dialog.
        if (['loop_stopped', 'queue_unavailable'].includes(error?.message)
            && node.freevideoFailureReport && node.freevideoFailureReport.stage !== 'queue') return;
        status.textContent = error?.schema === 'freevideo.error-report'
            ? t('Could not start. See the details on the picture.', '没能开始生成，原因见画面上的说明。')
            : extensionCause(error) ? t('A browser extension is interfering with FreeVideo. See the details on the picture.', '浏览器扩展干扰了 FreeVideo，详见画面上的说明。')
            : queueError(error);
        // Recoverable chain actions need one visible sentence, not a second error card.
        if (error?.action) { status.scrollIntoView({block: 'nearest'}); return; }
        // A loop stopped by a long outage is a new state of the same notice: show its final details once.
        const identity = error?.schema === 'freevideo.error-report' ? error : error?.loopStopped ? `${error.message} (loop stopped)` : error?.message || error;
        if (lastQueueError === identity) return;
        lastQueueError = identity;
        const report = failure.show(error, error?.schema === 'freevideo.error-report', {stage: 'queue', node, graph: app.graph});
        node.freevideoFailureReport = report; node.freevideoFailure = errorText(report);
        connectionNotice = isNetworkTypeError(error) && (!report.browser_extension || wrappedFetchOnly(report.browser_extension));
    }
    // The queue read works again: take the lost-connection notice down. A failure card for anything else stays.
    function clearConnectionNotice() {
        if (!connectionNotice) return;
        connectionNotice = false;
        if (node.freevideoFailureReport?.stage !== 'queue') return;
        lastQueueError = null; failure.clear(); node.freevideoFailure = ''; node.freevideoFailureReport = null;
        status.dataset.error = 'false'; status.textContent = '';
        // The engine is back: say so when the launcher saw it stop by itself.
        newEngineExit(api).then(record => {
            if (record && !disposed && !status.textContent) status.textContent = engineExitText(record, t);
        }).catch(() => {});
    }
    // A part that did not start says so on its own line; what the server said goes in the card, with a report.
    function showChainActionError(error) {
        if (disposed) return;
        lastQueueError = null;
        const report = failure.show(error?.cause || error, false, {stage: 'queue', node, graph: app.graph});
        node.freevideoFailureReport = report; node.freevideoFailure = errorText(report);
    }
    // A part that stopped is one sentence on the status line; what ComfyUI said goes in the card, as a generation failure.
    function showPartError(error) {
        if (disposed) return;
        status.dataset.error = 'true';
        // The server's plain sentence (a full disk, unreadable storage) when it has one; what failed stays in the card.
        status.textContent = error.said ? (error.joining ? '' : t(`Part ${error.index + 1}: `, `第 ${error.index + 1} 段：`)) + t(error.said[0], error.said[1])
            : error.joining ? t('Could not join the parts. See the details on the picture.', '未能接合各段，原因见画面上的说明。')
            : t(`Part ${error.index + 1} could not be generated. See the details on the picture.`, `第 ${error.index + 1} 段未能生成，原因见画面上的说明。`);
        if (lastQueueError === error.message) return;
        lastQueueError = error.message;
        const report = failure.show(error.message, false,
            {stage: 'execution', node, graph: app.graph, ...(error.joining ? {progress: {phase: 'save'}} : {})});
        node.freevideoFailureReport = report; node.freevideoFailure = errorText(report);
    }
    function lineLabel() {
        const n = lineTexts().length;
        return n ? t(n === 1 ? 'Add 1 video' : `Add ${n} videos`, `添加 ${n} 个视频`) : t('Add videos', '添加视频');
    }
    function updateRunButton() {
        const submitting = capturing || queueState?.submitting;
        updateFirstFrame();
        const multiple = lengthControl.plan?.count > 1;
        // A long/continued video temporarily uses one run. Keep the user's mode
        // preference so returning to a short video and reloading show the same choice.
        runMode = multiple || extension.source ? 'single'
            : ['single', 'batch', 'loop'].includes(node.properties?.freevideo_run_mode) ? node.properties.freevideo_run_mode : runMode;
        const chain = chainController?.state, position = chainPosition(chain);
        // Only another long video can join a running chain; a single-part request would be submitted
        // straight to the busy GPU, so its button waits like a full queue does.
        const chainWait = Boolean(chainController?.active && (!chainController.canQueue || !multiple));
        const chainReason = !chainWait ? '' : readChainResult(chain)?.joining
            ? t('Joining the parts…', '正在接合各段…')
            : chain?.state === 'queued' ? (chain.waiting ? waitingText(chain.waiting, t) : t('Queued…', '排队中…'))
            : t(`Generating part ${position.index + 1}…`, `正在生成第 ${position.index + 1} 段…`);
        generate.disabled = submitting || chainWait || lengthControl.blocked || (runMode === 'loop' && queueState?.looping);
        // Planning the parts and adding a batch are different waits; a count is only shown when the queue really has one.
        const adding = queueState?.batch ? t(`Adding ${Math.min(queueState.batch.done + 1, queueState.batch.total)}/${queueState.batch.total}…`,
            `正在添加 ${Math.min(queueState.batch.done + 1, queueState.batch.total)}/${queueState.batch.total}…`)
            : capturing ? t('Planning segments…', '正在规划分段…') : t('Adding…', '正在添加…');
        const label = submitting ? adding
            : chainReason || (byLine() ? lineLabel()
            : runMode === 'batch' ? t(`Add ${count.value || '…'} videos`, `添加 ${count.value || '…'} 个视频`)
            : runMode === 'loop' ? (queueState?.looping ? t('Looping', '循环中') : t('Start loop', '开始循环'))
            : busy ? t('Add to queue', '加入队列')
            : previewFirst.checked && !previewFirst.disabled ? t('Generate preview', '生成预览') : t('Generate video', '生成视频'));
        paintKey(borrowed?.label?.() ?? label, borrowed ? true : !submitting && !chainReason);
        syncKeyMore();
        if (borrowed) generate.disabled = borrowed.disabled?.() ?? false;
        else if (videoMissing) { paintKey(addVideoLabel(t), false); generate.disabled = false; }
        // The hover card names what the button does now (its own label), and when it is greyed, why and how to free it.
        const lineUse = enhancer.element.dataset.enabled === 'true'
            ? t('Each line is one video, sent as written without enhancement', '每行生成一个视频，按原文发送，不做描述增强')
            : t('Each line of the description is one video', '描述的每一行生成一个视频');
        const use = videoMissing ? t('Download the video model before generating', '下载视频模型后才能生成视频')
            : generate.disabled ? ''
            : byLine() ? lineUse
            : runMode === 'batch' ? t(...TIPS['studio.mode.batch'].use)
            : runMode === 'loop' ? t(...TIPS['studio.mode.loop'].use)
            : busy ? t('Starts when the current video finishes', '当前视频完成后自动开始')
            : previewFirst.checked && !previewFirst.disabled ? t(...TIPS['studio.twoPass.preview'].use) : t(...TIPS['studio.generate'].use);
        const why = !generate.disabled ? ''
            : submitting ? t('Adding to the queue. One moment.', '正在加入队列，请稍候')
            : chainWait ? t('Parts are generating. Wait, or cancel them in the queue.', '分段视频生成中，完成或在队列里取消后可再生成')
            : lengthControl.blocked ? lengthControl.message || ''
            : t('A loop is running. Stop it to start again.', '正在循环生成，停止后可重新开始');
        const lengthSaid = lengthControl.blocked && !videoMissing ? lengthControl.visibleMessage : '';
        if (lengthWhy.textContent !== lengthSaid) lengthWhy.textContent = lengthSaid;
        if (lengthWhy.hidden !== !lengthSaid) lengthWhy.hidden = !lengthSaid;
        const words = {fvTipName: keyLabel.textContent.replace(/…$/, ''), fvTipUse: use && keySetup && keyShowsEta ? `${use} · ${t('Includes first-time setup', '含首次准备')}` : use, fvTipKey: TIPS['studio.generate'].key, fvTipWhy: why};
        for (const [field, value] of Object.entries(words)) {
            if (value) { if (generate.dataset[field] !== value) generate.dataset[field] = value; } else delete generate.dataset[field];
        }
        if (generate.title) generate.title = '';
        for (const b of modeButtons) { const on = String(b.dataset.mode === runMode); if (b.getAttribute('aria-pressed') !== on) b.setAttribute('aria-pressed', on); }
        { const word = {single: t('Single', '单次'), batch: t('Batch', '批量'), loop: t('Loop', '循环')}[runMode] || ''; if (optionsLabel.textContent !== word) optionsLabel.textContent = word; }
        for (const b of modeButtons) if (b.dataset.mode !== 'single' && b.disabled !== multiple) b.disabled = multiple;
        // Outside Batch both rows are greyed (the tray keeps them in place); with one prompt per line the lines set the number.
        if (countField.hidden !== (runMode !== 'batch')) countField.hidden = runMode !== 'batch'; if (count.disabled !== (runMode !== 'batch' || byLine())) count.disabled = runMode !== 'batch' || byLine();
        const countWhy = byLine() ? t('Set by the number of lines', '按描述的行数生成') : '';
        if (count.title !== countWhy) count.title = countWhy;
        // The greyed box shows how many videos the lines make; the saved number comes back when the switch is off.
        if (byLine() || count.dataset.fvLines) {
            const shownCount = byLine() ? String(lineTexts().length) : String(node.properties?.freevideo_batch_count ?? 4);
            if (byLine()) count.dataset.fvLines = 'true'; else delete count.dataset.fvLines;
            if (count.value !== shownCount) { count.value = shownCount; count.fvAtPaint?.(); }
        }
        put(perLineField, 'hidden', !linesUsable()); put(perLine, 'disabled', runMode !== 'batch');
        const lineTip = {fvTipName: t('One prompt per line', '每行一条描述'), fvTipUse: lineUse,
            fvTipWhy: perLine.disabled ? t('Choose Batch to use it', '选“批量”后可用') : ''};
        for (const [key, text] of Object.entries(lineTip)) {
            if (text) { if (perLineField.dataset[key] !== text) perLineField.dataset[key] = text; } else delete perLineField.dataset[key];
        }
        put(modeHelp, 'hidden', runMode === 'single' && !multiple);
        put(modeHelp, 'textContent', multiple ? t('Videos made in parts cannot use batch or loop.', '分段视频不能批量或循环生成。') : runMode === 'loop'
            ? t('Same settings, a new seed each time. Stops when this page closes.', '用当前设置，每次换一个种子；关闭网页后停止。')
            : byLine() ? (enhancer.element.dataset.enabled === 'true'
                ? t('Each line of the description is one video, sent as written without enhancement.', '描述的每一行生成一个视频，按原文发送，不做描述增强。')
                : t('Each line of the description is one video.', '描述的每一行生成一个视频。'))
            : t('Same settings, a new seed for each video.', '用当前设置，每个视频换一个种子。'));
        // With one video per GPU each running row carries its own Cancel; "current" would be ambiguous.
        put(cancelCurrent, 'hidden', !chainController?.active && (!queueState?.running || Boolean(queueState?.running_all)));
        put(cancelCurrent, 'disabled', Boolean(cancelling));
        put(cancelCurrent, 'textContent', cancelling ? t('Cancelling…', '正在取消…') : t('Cancel current', '取消当前'));
        put(stopLoop, 'hidden', !queueState?.looping);
        put(queueActions, 'hidden', stopLoop.hidden && resumeChain.hidden);
        updateExtendButton();
    }
    function updateExtendButton() {
        const button = links.querySelector('.fv-action-extend');
        if (button) {
            button.disabled = capturing;
            button.title = capturing ? t('Adding…', '正在添加…') : '';
        }
    }
    // The moved video keeps its place in view and is marked for a moment, so the list does not just look the same.
    let movedId = null, movedTimer = 0;
    function clearMoved() {
        clearTimeout(movedTimer); movedId = null;
        queueList.querySelectorAll('.fv-queue-moved').forEach(item => { item.classList.remove('fv-queue-moved'); item.querySelector('.fv-queue-chip')?.remove(); });
    }
    function showMoved() {
        const item = [...queueList.children].find(child => child.dataset.id === movedId);
        if (!item || item.classList.contains('fv-queue-moved')) return;
        item.classList.add('fv-queue-moved');
        const chip = el('span', t('Moved to front', '已排到最前'), 'fv-queue-chip');
        item.querySelector('.fv-queue-buttons').prepend(chip);
        queueList.scrollTop = 0;
        clearTimeout(movedTimer); movedTimer = setTimeout(clearMoved, 2800);
    }
    function renderQueue(state) {
        if (disposed) return;
        queueState = state;
        if (chainController?.active && !state.running_all) { updateRunButton(); return; }
        const active = state.running || state.pending[0];
        activePrompt = active?.id || null;
        const wasBusy = busy;
        busy = Boolean(active) || progressLive(); queued = !state.running && Boolean(state.pending.length);
        // A finished, failed or cancelled video may have downloaded what the line above Generate still lists.
        if (wasBusy && !busy) { requestAssets?.refresh(true); }
        const g = active?.inputs;
        runningSummary = Number.isFinite(g?.width) && Number.isFinite(g?.height)
            ? [t('Generating', '正在生成'), `${g.width} × ${g.height}`, Number(g.seconds) > 0 ? t(`${g.seconds} s`, `${g.seconds} 秒`) : ''].filter(Boolean).join(' · ') : '';
        // The right-hand frame belongs to the submitted video. Draft edits on
        // the left must never reshape a video currently being generated.
        if (Number.isFinite(g?.width) && Number.isFinite(g?.height)) previewSize(g.width, g.height);
        const queueRows = state.running_all ? [...state.running_all, ...state.pending] : state.pending;
        const running = state.running_all?.length || 0, waiting = state.pending.length;
        queueToggle.textContent = !state.running_all ? t(`Queued · ${waiting}`, `排队 ${waiting} 个`)
            : [running && t(`${running} generating`, `生成中 ${running} 个`), waiting && t(`${waiting} queued`, `排队 ${waiting} 个`)]
                .filter(Boolean).join(' · ');
        queueDetails.hidden = queueRows.length === 0;
        const signature = JSON.stringify(queueRows.map(row => [row.id, row.inputs.width, row.inputs.height,
            ...(state.running_all ? [state.running_all.includes(row), row.progress?.sequence] : [])]));
        if (queueList.dataset.signature !== signature) {
            queueList.dataset.signature = signature; queueList.replaceChildren();
            queueRows.forEach(row => {
                const executing = Boolean(state.running_all?.includes(row));
                // Only waiting rows are numbered: the number is the place in the queue.
                const index = state.pending.indexOf(row);
                const size = `${row.inputs.width} × ${row.inputs.height}`;
                const item = el('div', null, 'fv-queue-item');
                item.dataset.id = row.id;
                // Waiting videos often share every setting; the start of the prompt tells them apart.
                const summary = String(row.inputs.text ?? '').replace(/\s+/g, ' ').trim();
                const description = el('div', null, 'fv-queue-text');
                if (summary) { const line = el('span', summary, 'fv-queue-summary'); cutTip(line, summary); description.append(line); }
                description.append(el('span', executing ? `${size} · ${row.inputs.seconds} s`
                    : `${index + 1} · ${size} · ${row.inputs.seconds} s`, 'fv-queue-meta'));
                if (executing) {
                    const bar = el('div', null, 'fv-queue-bar'), fill = el('div', null, 'fv-queue-fill');
                    bar.setAttribute('role', 'progressbar'); bar.setAttribute('aria-valuemin', '0'); bar.setAttribute('aria-valuemax', '100');
                    bar.setAttribute('aria-label', t(`${size} · ${row.inputs.seconds} s`, `${size} · ${row.inputs.seconds} 秒`));
                    const fraction = row.progress?.overall?.fraction;
                    if (Number.isFinite(fraction)) {
                        const percent = Math.min(100, Math.max(0, fraction * 100));
                        fill.style.width = `${percent}%`; bar.setAttribute('aria-valuenow', String(Math.round(percent)));
                    } else bar.dataset.indeterminate = 'true';
                    bar.append(fill); description.append(bar);
                }
                const remove = button(executing ? t('Cancel', '取消') : t('Remove', '移除'), async () => {
                    remove.disabled = true;
                    try { await queueController.cancel(row.id); }
                    catch (error) { showQueueError(error); remove.disabled = false; }
                }, 'fv-quiet');
                remove.setAttribute('aria-label', executing ? t(`Cancel ${size} · ${row.inputs.seconds} s`, `取消 ${size} · ${row.inputs.seconds} 秒`)
                    : t(`Remove queued video ${index + 1}`, `移除排队的第 ${index + 1} 个`));
                item.append(description);
                // The buttons move together: on a narrow list they drop below the text, aligned right.
                const actions = el('div', null, 'fv-queue-buttons');
                if (!executing && index > 0) {
                    const first = button(t('Move to front', '排到最前'), async () => {
                        first.disabled = remove.disabled = true;
                        movedId = row.id;
                        try { await queueController.moveFirst(row.id); showMoved(); }
                        catch (error) { movedId = null; showQueueError(error); first.disabled = remove.disabled = false; }
                    }, 'fv-quiet');
                    first.setAttribute('aria-label', t(`Move queued video ${index + 1} to front`, `把排队的第 ${index + 1} 个排到最前`));
                    actions.append(first);
                }
                actions.append(remove); item.append(actions); queueList.append(item);
            });
        }
        if (movedId && state.pending.some(row => row.id === movedId) && !queueList.querySelector('.fv-queue-moved')) showMoved();
        if (!busy) cancelling = false;
        if (state.error) showQueueError(state.error); else clearConnectionNotice();
        updateRunButton();
    }
    const syncQueue = () => queueController.refresh();
    async function cancelGeneration() {
        if (cancelling) return;
        try {
            cancelling = true; updateRunButton();
            if (chainController.active) {
                stopNote = chainController.waiting > 0 ? t('Stopped. The next queued video is starting.', '已停止，队列中的下一条开始生成。') : '';
                await chainController.cancel(); cancelling = false; updateRunButton();
                if (stopNote) { status.dataset.error = 'false'; status.textContent = stopNote; }
                return;
            }
            await syncQueue();
            const id = queueController.state().running?.id;
            if (!id) { cancelling = false; updateRunButton(); return; }
            cancelling = true; updateRunButton();
            const reply = await queueController.cancel(id);
            status.dataset.error = 'false';
            status.textContent = reply.status === 'cancelling' ? t('Stopping generation…', '正在停止生成…') : '';
        } catch (error) { cancelling = false; updateRunButton(); showQueueError(error); }
    }
    async function submitDraft(options) {
        // Borrowed by the editor: its Generate runs the editor's own submission (editor_bar.js), with this bar's values.
        if (borrowed) {
            // The enhancement and the replace-person check are this bar's; what is then made is the editor's.
            if (swapState(mediaNode)) {
                const missing = swapMissing(swapState(mediaNode));
                if (missing) { status.dataset.error = 'true'; status.textContent = swapMissingText(missing, t); return; }
            }
            if (prompt.value.trim() && !swapState(mediaNode) && await enhancer.beforeGenerate() === false) return;
            return borrowed.submit(options);
        }
        if (videoMissing) {
            // The answer comes from this computer at once; the button waits for it so a click is never silent.
            generate.disabled = true;
            // The answer sits right under the button; the status under the preview keeps saying what is missing.
            try { addVideoNote.textContent = await addVideoModel((path, request) => api.fetchApi(path, request), t); }
            finally { generate.disabled = false; addVideoNote.hidden = !addVideoNote.textContent; }
            return;
        }
        const emptyAllowed = options?.allowEmpty === true;
        if (capturing || queueState?.submitting) return;
        // Pressing Generate puts the open tray away; it does not open again while the video is made.
        consoleUI?.close();
        // Ctrl+Enter leaves focus in a field: commit its edit (change fires on blur) first.
        const editing = dialog.contains(document.activeElement) ? document.activeElement : null;
        if (editing && editing !== generate) { editing.blur(); editing.focus({preventScroll: true}); }
        const invalid = [...dialog.querySelectorAll('input:not(:disabled)')].find(e => !e.checkValidity());
        if (invalid) {
            const details = invalid.closest('details'); if (details) details.open = true;
            if (invalid.validity.valueMissing) {
                const message = new Map([[seconds, t('Fill in the duration.', '请填写时长。')],
                    [width, t('Fill in the width.', '请填写宽度。')], [height, t('Fill in the height.', '请填写高度。')],
                    [seed, t('Fill in the seed.', '请填写种子。')],
                    [baseSteps, t('Fill in the first-pass steps.', '请填写一采步数。')],
                    [refineSteps, t('Fill in the second-pass steps.', '请填写二采步数。')]]).get(invalid);
                if (message) { status.dataset.error = 'true'; status.textContent = message; }
            }
            // A value out of range is brought in and said under its field (the length: in the console's line); a rule
            // between fields is said there too. No browser bubble.
            const said = invalid.validity.valueMissing ? null : invalid === seconds ? durationNote : fieldNotes.get(invalid);
            if (said) { said.settle(); if (invalid !== seconds) invalid.focus(); return; }
            if (!invalid.validity.valueMissing) { status.dataset.error = 'true'; status.textContent = invalid.validationMessage; }
            // Width and height are typed in the frame panel: it opens at the empty field.
            const side = (invalid === width || invalid === height) && framePicker?.element.checkVisibility() ? [...framePicker.pop.querySelectorAll('.fvp-dim')][invalid === width ? 0 : 1] : null;
            if (side) { framePicker.set({empty: emptySide, ask: true}); if (framePicker.pop.dataset.open !== 'true') framePicker.element.click(); side.focus(); return; }
            // The duration is typed in the stepper's number: it opens for typing.
            const stepValue = invalid === seconds ? dialog.querySelector('.fvs-step-value') : null;
            if (stepValue?.checkVisibility()) { stepValue.click(); return; }
            invalid.focus(); return;
        }
        // One prompt per line with no lines: say what to write (generating without a prompt is not offered here).
        if (byLine() && !lineTexts().length) { status.dataset.error = 'true'; status.textContent = t('Write one prompt per line, then add them.', '每行写一条描述后再添加。'); prompt.focus(); return; }
        if (!emptyAllowed && !extension.source && !prompt.disabled && !prompt.value.trim() && !untouched() && !swapState(mediaNode)) {
            showEmptyPrompt({t, anchor: runrow, field: prompt, onContinue: () => submitDraft({allowEmpty: true})}); return;
        }
        if (swapState(mediaNode)) {
            const missing = swapMissing(swapState(mediaNode));
            if (missing) { status.dataset.error = 'true'; status.textContent = swapMissingText(missing, t); return; }
        }
        const timedNote = !prompt.disabled && timedRun() ? timedProblem(prompt.value, Number(value(node, 'seconds')), t) : '';
        if (timedNote) { status.dataset.error = 'true'; status.textContent = timedNote; prompt.focus(); return; }
        const chosenMode = runMode, texts = byLine() ? lineTexts() : null;
        const batchCount = texts ? texts.length : chosenMode === 'batch' ? Number(count.value) : 1;
        if (texts && !texts.length) { status.dataset.error = 'true'; status.textContent = t('Write one prompt per line, then add them.', '每行写一条描述后再添加。'); prompt.focus(); return; }
        if (texts && texts.length > 100) { status.dataset.error = 'true'; status.textContent = t(`At most 100 lines at a time; there are ${texts.length}.`, `一次最多 100 行，现在有 ${texts.length} 行。`); prompt.focus(); return; }
        if (texts && timedParse(texts.join('\n'))[1].length) { status.dataset.error = 'true'; status.textContent = t('Timed lines cannot be split into separate videos. Turn off One prompt per line.', '带时间的台词不能拆成多个视频，请关闭“每行一条描述”。'); return; }
        const originalSeed = value(node, 'seed'), seedMode = mode.value;
        // A request that is not accepted leaves the node mode as the user had it; a retry turns it on again.
        // Once the server has taken a request it stays taken; only a request it never took is undone.
        let held = null, accepted = false, snapshot = null;
        const onAccepted = () => { accepted = true; };
        try {
            stopNote = ''; lastQueueError = null; failure.clear(); node.freevideoFailure = ''; node.freevideoFailureReport = null;
            capturing = true; updateRunButton(); status.dataset.error = 'false'; runOptions.open = false;
            lengthControl.freeze();
            // Lines are sent as written: an enhanced rewrite would merge them into one description.
            if (!texts && (!extension.source || prompt.value.trim()) && !swapState(mediaNode) && await enhancer.beforeGenerate() === false) return;
            if (disposed) return;
            if (selected === 'input' && !dimensionsLinked) await inputRatio(true);
            const lengthPlan = await lengthControl.ready();
            if (disposed || lengthControl.blocked) return;
            if (lengthControl.absent && !durationNote.settle()) return;
            if (originalOn && lengthPlan?.count > 1 && !twoPass.checked) { status.dataset.error = 'true'; status.textContent = partsNeedTwoPassText(t); return; }
            if (lengthPlan?.count > 1 && chosenMode !== 'single') { status.textContent = t('Videos made in parts cannot use batch or loop.', '分段视频不能批量或循环生成。'); return; }
            const lengthOptions = lengthControl.options();
            const usageContext = capabilityContext(node, {custom: true});
            const usageQuery = usageContext && !extension.source && !seconds.disabled ? new URLSearchParams(usageContext) : null;
            // Missing material dimensions or linked settings cannot describe the same request reliably.
            if (usageQuery && (!usageQuery.get('task').startsWith('ref2va') || usageQuery.has('references'))) {
                for (const [key, val] of Object.entries({width: value(node, 'width'), height: value(node, 'height'),
                    seconds: lengthPlan?.partSeconds?.[0] ?? Number(seconds.value), base_steps: baseSteps.value,
                    refine_steps: refineSteps.value, two_pass: twoPass.checked ? '1' : '0',
                    // No cache is sent as the engine's own 'none': an absent one would read as the tier's default cache.
                    step_cache: stepCacheNow() ?? 'none', upscale_factor: upscaleNow()})) usageQuery.set(key, val);
                await otherUsage.check(usageQuery);
            } else await otherUsage.check(null);
            if (disposed) return;
            const source = extension.source;
            const sourceState = source ? await extendSourceState(api, source) : 'ok';
            if (sourceState !== 'ok') throw new Error(extendSourceProblem(t, sourceState));
            if (source && lengthPlan?.count > 1) lengthOptions.after = {work: source.video};
            const swapProblem = await ensureSwapLora(true);
            if (swapProblem) { status.dataset.error = 'true'; status.textContent = swapProblem; return; }
            // Only serialization briefly locks the editor. Network submission
            // and all GPU execution leave the next draft fully editable.
            for (const section of controls.querySelectorAll('.fv-section')) section.inert = true;
            held = turnNodeOn(node);
            const graph = cleanOptionalPrompt(clonePrompt(await app.graphToPrompt()));
            if (emptyAllowed && !prompt.value.trim() && !extension.source) allowEmptyPrompt(graph, node.id);
            snapshot = singleRunSnapshot(extendSnapshot(graph, node.id, source,
                {multiple: lengthPlan?.count > 1}), node.id, lengthPlan);
            if (!snapshot) { fault(t('Could not prepare this run. Try again.', '没能准备这次生成，请重试。')); return; }
            const seedIndex = node.widgets.findIndex(w => w.name === 'seed');
            capturing = false;
            for (const section of controls.querySelectorAll('.fv-section')) section.inert = false;
            if (lengthPlan?.count > 1) {
                const waitingBefore = chainController.waiting;
                const chain = await chainController.start(snapshot, lengthOptions, {onAccepted});
                if (disposed) return;
                if (chain && chainController.waiting > waitingBefore) { status.dataset.error = 'false'; status.textContent = addedNote; return; }
                if (!chain) { if (source) throw new Error(extendText(t, 'unavailable')); lengthControl.fallback(); if (!durationNote.settle()) return;
                    await queueController.start(snapshot, {count: batchCount, repeat: chosenMode === 'loop', seedIndex, onAccepted}); }
            } else await queueController.start(snapshot, {count: batchCount, repeat: chosenMode === 'loop', seedIndex, onAccepted,
                ...(texts ? {texts, textIndex: node.widgets.findIndex(w => w.name === 'text')} : {})});
            // Preserve draft changes made while the POST was in flight.
            if (chosenMode === 'single' && !linked(node, 'seed') && value(node, 'seed') === originalSeed) {
                const max = Number.MAX_SAFE_INTEGER;
                const next = seedMode === 'randomize' ? randomSeed() : seedMode === 'increment'
                    ? (originalSeed >= max ? 0 : originalSeed + 1) : seedMode === 'decrement'
                    ? (originalSeed <= 0 ? max : originalSeed - 1) : originalSeed;
                set(node, 'seed', next); seed.value = next;
            }
            if (!queueController.state().error) status.textContent = held
                ? t('The FreeVideo node was off in Nodes view. It has been turned back on.', 'FreeVideo 节点在节点视图里是关闭的，已自动打开。') : '';
        } catch (error) { showQueueError(createErrorReport(error, {stage: 'submission', snapshot, node, graph: app.graph})); }
        finally {
            if (accepted) held?.done(); else held?.restore();
            // A prompt that was generated goes to the top of the history, with the size, length and level it used.
            if (accepted && prompt.value.trim()) rememberPrompt({text: prompt.value, w: Number(value(node, 'width')), h: Number(value(node, 'height')),
                seconds: Number(seconds.value), level: effortFor(Number(baseSteps.value), twoPass.checked, Number(refineSteps.value), stepCacheNow(), effort.levels(), upscaleNow())?.name || null});
            capturing = false;
            for (const section of controls.querySelectorAll('.fv-section')) section.inert = false;
            updateRunButton();
        }
    }
    cleanup.push(queueController.subscribe(renderQueue));
    const closeMenus = event => {
        if (!runOptions.contains(event.target)) runOptions.open = false;
        if (!queueDetails.contains(event.target)) queueDetails.open = false;
    };
    dialog.addEventListener('pointerdown', closeMenus);
    function updateDuration() {
        const requested = Math.ceil(Number(seconds.value) * 24), frames = requested + ((5 - requested) % 17 + 17) % 17;
        const length = lengthControl.plan?.count > 1 ? lengthControl.plan.seconds : Number.isFinite(frames) ? frames / 24 : null;
        const whole = Math.round(length), minutes = Math.floor(whole / 60), rest = whole % 60;
        duration.textContent = length === null ? '—' : `${length <= 60 ? `${lengthControl.plan?.count > 1 ? length : length.toFixed(3)} s`
            : rest ? t(`${minutes} min ${rest} s`, `${minutes} 分 ${rest} 秒`) : t(`${minutes} min`, `${minutes} 分钟`)} · 24 fps`;
    }
    function updateCanvas() {
        syncResolutionLabel();
        // Render committed dimensions without replacing either input draft.
        // Duration/estimate refreshes and edits to the other dimension also
        // come here; only an explicit size choice should fill both fields.
        const w = extension.source?.geometry.width ?? value(node, 'width');
        const h = extension.source?.geometry.height ?? value(node, 'height');
        dimensionText(dimensions, dimensionsLinked && !extension.source ? t('Size from connected nodes', '尺寸来自已连接节点') : `${w} × ${h}`);
        updateDuration();
        for (const b of shapes.children) setAttr(b, 'aria-pressed', String(b.dataset.ratio === selected));
        const shapeIndex = [...shapes.children].findIndex(b => b.dataset.ratio === selected);
        shapes.style.setProperty('--fv-shape-index', Math.max(0, shapeIndex));
        shapes.dataset.custom = String(shapeIndex < 0);
        // An empty frame takes the chosen shape; a work on the stage keeps its own (the outline shows the new one).
        if (!busy && (nextShown || !stageMedia.querySelector('video, img'))) { previewSize(Number(w), Number(h)); if (nextShown) nextSize.textContent = `${w} × ${h}`; }
        framePicker?.set({shape: selected, ratio: studioShapes(t).find(([id]) => id === selected)?.[2] || Number(w) / Number(h) || ratio, pixels: Number(mp.value), width: Number(w), height: Number(h), locked: shapeKept, empty: emptySide,
            unavailable: {input: mediaPicture() ? '' : t('Add a picture in Media first', '先在素材里添加图片')},
            disabled: dimensionsLinked || Boolean(extension.source)});
        refreshEffortEstimate();
        consoleUI?.sync();
    }
    function syncCanvasSize() {
        emptySide = '';
        width.value = extension.source?.geometry.width ?? value(node, 'width');
        height.value = extension.source?.geometry.height ?? value(node, 'height');
        updateCanvas();
    }
    function applySize() {
        if (dimensionsLinked || extension.source) return;
        const [w, h] = canvasDimensions(pixels, ratio);
        if (w < 256 || h < 256 || w > 4096 || h > 4096) throw new Error(t('This aspect ratio and resolution are out of range. Use a custom width and height.', '这个比例和清晰度超出范围，请改用自定义宽高。'));
        set(node, 'width', w); set(node, 'height', h); node.properties.freevideo_aspect = selected; node.properties.freevideo_pixels = pixels; syncCanvasSize();
    }
    // Whether Media has a picture for "Match image" to follow (the same choice inputRatio makes).
    function mediaPicture() {
        try { return (mediaNode ? effectiveRows(mediaNode, widget(mediaNode, 'assets')) : []).some(r => r.enabled !== false && (r.role === 'first' || r.role === 'last' || /\.(png|jpe?g|webp|bmp|tiff?)$/i.test(r.file))); }
        catch { return false; }
    }
    async function inputRatio(required = false) {
        const rows = mediaNode ? effectiveRows(mediaNode, widget(mediaNode, 'assets')) : [];
        const active = rows.filter(r => r.enabled !== false);
        const source = active.find(r => r.role === 'first') || active.find(r => r.role === 'last') || active.find(r => /\.(png|jpe?g|webp|bmp|tiff?)$/i.test(r.file));
        if (!source) { if (required) throw new Error(t('Add an image in Media to follow its aspect ratio.', '请先在素材中添加图片，再跟随输入比例。')); return; }
        const image = new Image(); image.src = view(source.file);
        const unreadable = () => new Error(t('Could not read the image dimensions. Choose another image.', '无法读取图片尺寸，请换一张图片。'));
        try { await image.decode(); } catch { if (disposed) return; throw unreadable(); }
        if (disposed) return;
        if (!image.naturalWidth || !image.naturalHeight) throw unreadable();
        if (selected === 'input') { ratio = image.naturalWidth / image.naturalHeight; applySize(); }
    }
    for (const [id, label, r] of [...studioShapes(t), ['input', t('Match image', '跟随图片'), null]]) {
        const b = button('', async () => { const previous = selected; try { selected = id; pixels = Number(mp.value); if (id === 'input') await inputRatio(true); else { ratio = r; applySize(); } status.textContent = ''; status.dataset.error = 'false'; } catch (error) { selected = previous; updateCanvas(); status.dataset.error = 'true'; status.textContent = error.message; } }, 'fv-shape');
        b.dataset.ratio = id; b.setAttribute('aria-label', label); b.disabled = dimensionsLinked;
        b.append(el('span', null, 'fv-shape-icon'), el('span', label)); shapes.append(b);
    }
    // Frame & size (glass/frame_picker.js): every frame and size in sight, with width × height and the lock. The choices
    // go through the controls above (kept, hidden), so the rules stay in one place. Pointing at or choosing a frame or size
    // turns the stage into the next generation's empty frame at that shape, with its pixels; a picked work brings the work
    // back (showResult).
    stage.append(nextSize);
    function showNext(frame) {
        if (busy || extension.source) return;
        const w = Number(value(node, 'width')), h = Number(value(node, 'height')), shown = frame || {width: w, height: h};
        if (!(shown.width > 0 && shown.height > 0)) return;
        if (!nextShown) { nextShown = true; stage.dataset.next = 'true'; stage.querySelector('video')?.pause(); }
        nextSize.textContent = `${shown.width} × ${shown.height}`;
        // The bar under the picture names the next generation instead of the work.
        // The frame itself shows the shape; no line names it or its pixels (the chip in the console says them).
        stageBar.setNext(true); summaryFor = null; stageBar.setSummary('');
        previewSize(shown.width, shown.height);
        // An upright frame is too slim for the hint sentence: it shows its size only (glass/frame_picker.css).
        stage.dataset.nextSlim = String(shown.width < shown.height);
    }
    function leaveNext() {
        if (!nextShown) return; nextShown = false; delete stage.dataset.next; stage.style.translate = '';
        // The stage takes back the shape of what it shows: the work's own, or the chosen frame when it is empty.
        const own = [...stageMedia.querySelectorAll('video')].find(v => !v.dataset.leaving && v.videoWidth > 0 && v.videoHeight > 0);
        const g = own ? {width: own.videoWidth, height: own.videoHeight}
            : stageMedia.querySelector('video, img') && result?.geometry?.width > 0 && result.geometry.height > 0 ? result.geometry
            : {width: Number(value(node, 'width')), height: Number(value(node, 'height'))};
        stageBar.setNext(false); previewSize(g.width, g.height); fitPreview();
    }
    // One button in the console's row ([▭ 16:9 · 1M]) with a small panel beside it: Frame, Size, and a typed width × height.
    // Under them, what the frame tray held besides (canvas): a long length's notes and the first-frame switch, each
    // only while it applies.
    framePicker = createFrameChip({t, placement: 'auto', host: dialog, extra: canvas,
        // A panel closing gives the stage back to the work that was on it, or to the empty stage (at the chosen frame).
        // The panel floats over the page; the stage stays as it is until a frame or size is pointed at or chosen.
        onClose: () => { if (!busy) { leaveNext(); fitPreview(); } },
        shapes: [...studioShapes(t).map(([id, label, r]) => ({id, label, ratio: r})), {id: 'input', label: t('Match image', '跟随图片'), ratio: null}],
        // Five steps, named by their megapixels (written out, so 0.75 is not read as 0.8M and 1.5 not as 2M); any
        // other size is typed as width × height.
        sizes: [{pixels: .5, label: '0.5M'}, {pixels: .75, label: '0.75M'}, {pixels: .98, label: '1M'}, {pixels: 1.5, label: '1.5M'}, {pixels: 2, label: '2M'}],
        sizeFor: (r, p) => { const [w, h] = canvasDimensions(p, r); return {width: w, height: h}; },
        custom: {minSide: 256, maxSide: 4096, snap: 32, lock: true, keepEmpty: true},
        onChange: change => {
            if (change.source === 'shape') [...shapes.children].find(b => b.dataset.ratio === change.shape)?.click();
            else if (change.source === 'size') choosePixels(change.pixels);
            // Typed width × height and the lock go through the panel's own fields, so their rules stay in one place.
            else if (change.source === 'lock') { if (shapeKept !== change.locked) keepShape.click(); }
            else if (change.source === 'empty') { emptySide = change.side; (change.side === 'height' ? height : width).value = ''; }
            else if (change.source === 'dimensions') { const input = change.side === 'height' ? height : width; input.value = String(change[change.side === 'height' ? 'height' : 'width']); input.onchange(); }
            updateCanvas(); showNext();
        },
        // Only while the panel is open: its options lose the pointer or the focus as it closes, after onClose.
        onPreview: frame => { if (framePicker?.pop.dataset.open === 'true') showNext(frame); }});
    shapes.hidden = true; sizeTicks.hidden = true;
    resolutionField.hidden = true;
    mp.onchange = () => { try { pixels = Number(mp.value); applySize(); if (pixels !== 1) legacyOption?.remove(); } catch (error) { status.dataset.error = 'true'; status.textContent = error.message; } };
    // The note describes the request just sent; once the draft changes it could read as the new one.
    const addedNote = t('Added to the queue. It starts when the current video finishes.', '已加入队列，当前视频完成后自动开始。');
    const clearAddedNote = () => { if (status.textContent === addedNote) status.textContent = ''; };
    seconds.addEventListener('input', clearAddedNote); prompt.addEventListener('input', clearAddedNote);
    seconds.onchange = () => { if (lengthControl.commit({report: false})) updateCanvas(); if (borrowed?.onSeconds && !borrowed.applying) borrowed.onSeconds(Number(seconds.value)); };
    // Audio used as is fixes the duration; long selections use the background chain.
    const SECONDS_BEFORE_AUDIO = 'freevideo_seconds_before_audio';
    let audioRevision = 0;
    // Where Use as is does not apply (a preview first, continuing a video), a clip chosen as is goes back to Reference,
    // whichever was chosen first; the run never carries it.
    function releaseOriginal() {
        const assets = widget(mediaNode, 'assets');
        if (!originalBlocked() || !assets) return false;
        const rows = JSON.parse(assets.value || '[]');
        if (!Array.isArray(rows) || !rows.some(usesOriginal)) return false;
        assets.value = JSON.stringify(rows.map(referenceRow)); mediaNode.graph?.change();
        window.dispatchEvent(new CustomEvent('freevideo-media', {detail: mediaNode.id}));
        return true;
    }
    async function syncOriginalAudio() {
        if (releaseOriginal()) return;
        const revision = ++audioRevision;
        const row = activeOriginal(mediaNode ? JSON.parse(value(mediaNode, 'assets') || '[]') : []);
        const result = row ? await checkOriginal(api, row) : null;
        if (disposed || revision !== audioRevision || result?.code === 'unreachable') return;
        originalOn = Boolean(result?.ok); updateFirstFrame();
        if (result?.ok) {
            lengthControl.forceSingle(result.seconds <= 30);
            if (linked(node, 'seconds')) return;
            node.properties ||= {};
            if (!Object.hasOwn(node.properties, SECONDS_BEFORE_AUDIO)) { node.properties[SECONDS_BEFORE_AUDIO] = Number(seconds.value); node.graph?.change(); }
            const length = Math.ceil(result.seconds * 1000) / 1000;
            seconds.disabled = false; audioLock.hidden = false;
            if (Number(seconds.value) !== length) { seconds.value = length; if (lengthControl.commit({exact: true})) updateCanvas(); }
            seconds.disabled = true;
            return;
        }
        lengthControl.forceSingle(false); audioLock.hidden = true; seconds.disabled = linked(node, 'seconds');
        if (node.properties && Object.hasOwn(node.properties, SECONDS_BEFORE_AUDIO)) {
            seconds.value = node.properties[SECONDS_BEFORE_AUDIO]; delete node.properties[SECONDS_BEFORE_AUDIO]; node.graph?.change();
            if (lengthControl.commit()) updateCanvas();
        }
    }
    const mediaChanged = async e => { if (String(e.detail) !== String(mediaNode?.id)) return; syncOriginalAudio(); refreshEffortEstimate(true); framePicker?.set({unavailable: {input: mediaPicture() ? '' : t('Add a picture in Media first', '先在素材里添加图片')}}); if (selected !== 'input') return; try { await inputRatio(); } catch (error) { status.textContent = error.message; } };
    const lorasChanged = e => { if (String(e.detail) === String(loraNode?.id)) refreshEffortEstimate(true); };
    window.addEventListener('freevideo-loras', lorasChanged);
    cleanup.push(() => window.removeEventListener('freevideo-loras', lorasChanged));
    const resultChanged = e => { if (String(e.detail.node) === String(node.id)) { if (chainController.active || chainController.isBlockOutput(e.detail.value)) { node.freevideoLastResult = result; return; } failure.clear(); showResult(e.detail.value); firstStart?.complete(e.detail.value); cancelling = false; status.textContent = ''; syncQueue().catch(() => {}); } };
    const prewarmChanged = e => { if (String(e.detail.node) === String(node.id)) prewarm.textContent = e.detail.label; };
    window.addEventListener('freevideo-prewarm', prewarmChanged);
    cleanup.push(() => window.removeEventListener('freevideo-prewarm', prewarmChanged));
    const originalBlockChanged = () => { syncOriginalAudio(); };
    window.addEventListener('freevideo-media', mediaChanged); window.addEventListener('freevideo-result', resultChanged); window.addEventListener('freevideo-audio-blocked', originalBlockChanged); syncOriginalAudio();
    cleanup.push(() => { window.removeEventListener('freevideo-media', mediaChanged); window.removeEventListener('freevideo-result', resultChanged); window.removeEventListener('freevideo-audio-blocked', originalBlockChanged); });
    const listen = (type, handler) => { api.addEventListener(type, handler); cleanup.push(() => api.removeEventListener(type, handler)); };
    listen('status', () => { syncQueue().catch(() => {}); });
    listen('executing', e => { const id = typeof e.detail === 'object' ? e.detail?.node : e.detail; if (String(id) === String(node.id)) { if (chainController.active) { chainController.refresh(); return; } failure.clear(); node.freevideoFailure = ''; node.freevideoFailureReport = null; liveProgressAt = Date.now(); busy = true; queued = false; updateRunButton(); showProgress(node.freevideoProgress || {label: t('Preparing your video', '正在准备视频'), reset: true, new_request: true}); status.textContent = ''; } syncQueue().catch(() => {}); });
    const progressChanged = e => {
        if (String(e.detail.node) !== String(node.id)) return;
        if (e.detail.task_id && activePrompt && e.detail.task_id !== activePrompt) return;
        if (chainController.progress(e.detail)) return;
        progress.updateReport(e.detail);
        if (['failed', 'cancelled'].includes(e.detail.phase) || e.detail.result) {
            liveProgressAt = 0; busy = cancelling = false; updateRunButton();
            if (e.detail.phase === 'cancelled') { hideProgress(); status.textContent = t('Cancelled', '已取消'); }
            else if (e.detail.phase === 'failed') { hideProgress(); status.textContent = t('Generation stopped', '生成已停止'); }
            else if (revealTimer === null) hideProgress();
            return;
        }
        liveProgressAt = Date.now(); busy = true; queued = false; updateRunButton(); status.dataset.error = 'false';
        if (cancelling) return;
        status.textContent = '';
        showProgress(e.detail);
    };
    window.addEventListener('freevideo-progress', progressChanged);
    cleanup.push(() => window.removeEventListener('freevideo-progress', progressChanged));
    listen('execution_error', e => {
        if (e.detail?.task_id && activePrompt && e.detail.task_id !== activePrompt) return;
        if (chainController.active && String(e.detail?.node_id) === String(node.id)) { chainController.refresh(); return; }
        const seen = new Set(), pending = [node];
        while (pending.length) {
            const currentNode = pending.pop(); if (!currentNode || seen.has(String(currentNode.id))) continue;
            seen.add(String(currentNode.id));
            for (const input of currentNode.inputs || []) pending.push(upstream(currentNode, input.name));
        }
        if (!seen.has(String(e.detail?.node_id))) return;
        liveProgressAt = 0; busy = cancelling = false; updateRunButton(); hideProgress(); status.dataset.error = 'true';
        node.freevideoFailureReport = failure.show(e.detail, true, {stage: 'execution', node, graph: app.graph, progress: node.freevideoProgress});
        node.freevideoFailure = errorText(node.freevideoFailureReport);
        status.textContent = t('Generation stopped.', '生成已停止。');
    });
    listen('execution_interrupted', e => {
        if (chainController.active && String(e.detail?.node_id) === String(node.id)) { chainController.refresh(); return; }
        if (activePrompt ? e.detail?.prompt_id !== activePrompt : String(e.detail?.node_id) !== String(node.id)) return;
        liveProgressAt = 0; busy = cancelling = false; activePrompt = null; updateRunButton(); hideProgress();
        failure.clear(); node.freevideoFailure = ''; node.freevideoFailureReport = null; status.dataset.error = 'false'; status.textContent = t('Cancelled', '已取消');
    });
    // Left and right step through the history when no field, slider or video has the keys.
    const historyKeys = e => {
        if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || !['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
        if (e.target?.closest?.('input,textarea,select,video,[contenteditable],[role=slider],[role=radiogroup],.fvs-tray,.fvs-library,.fvs-compare')) return;
        e.preventDefault(); stepHistory(e.key === 'ArrowLeft' ? -1 : 1);
    };
    cleanup.push(listenDialogKey(dialog, 'keydown', historyKeys));
    const escape = e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        if (generate.disabled) {
            generate.scrollIntoView({block: 'center'});
            return;
        }
        generate.click();
    } };
    cleanup.push(listenDialogKey(dialog, 'keydown', escape));
    cleanup.push(isolateDialogKeys(dialog, event => routeDialogKey(event, dialog)));

    // Escape first closes the recent videos panel of a narrower window, then the studio.
    // An open result menu (More, delete) closes first, wherever the focus is, and gives it back to the menu's button.
    const closeMenu = () => {
        const open = [...dialog.querySelectorAll('details.fv-action-menu[open]')].pop();
        if (!open) return false;
        open.open = false; open.querySelector(':scope>summary')?.focus({preventScroll: true}); return true;
    };
    dialog.addEventListener('cancel', e => { e.preventDefault(); if (!closeMenu() && !compare.close() && !promptLibrary?.hide() && !consoleUI?.dismiss() && !recent.dismiss()) closeDialog(dialog); });
    const dispose = () => { if (disposed) return; disposed = true; borrowed?.release({disposing: true}); blockOriginal('extend', false); blockOriginal('preview', false); for (const f of cleanup) f(); stage.querySelector('video')?.pause(); dialog.remove(); if (current?.dialog === dialog) { current = null; viewChanged('nodes', node); } };
    const onClose = () => {
        // The browser's own close event of a switch comes with the next frame; if the panel was opened again before it
        // (back from the node view at once), that late event is not a close and must not take the panel away.
        if (dialog.open) return;
        // Parked behind the node view: everything stays as it is (closeDialog may have taken this handler while a
        // switch held the frames, so it is put back for the real close later).
        if (dialog.dataset.fvParked === 'true') { stage.querySelector('video')?.pause(); warm?.cancel(); dialog.onclose = onClose; return; }
        dispose();
    };
    dialog.onclose = onClose; current.dispose = dispose;
    // The editor borrows this panel's bar: the same controls and pop-ups move into the editor's dialog, so both show the
    // same chips and their rules stay in one place. Pressing Generate runs the editor's submission instead; the values
    // come from the same node the bar edits. Release puts everything back as it was.
    let popHolder = null;
    current.borrow = ({mount, submit, label = null, disabled = null, onSeconds = null, onFirstFrame = null, initial = {}, locked = []}) => {
        if (borrowed || disposed) return null;
        const outer = mount.closest('dialog') || document.body;
        const home = {parent: controls.parentNode, next: controls.nextSibling};
        // The pop-ups travel inside one display:contents holder, so a switch moves one node instead of every pop-up.
        if (!popHolder) { popHolder = el('div', null, 'fv-pop-holder'); popHolder.style.display = 'contents'; }
        popHolder.append(...[...dialog.children].filter(child => child !== header && child !== body && child !== popHolder));
        const shell = el('div', null, 'fv-studio fv-create fv-borrowed'), shellBody = el('div', null, 'fv-body');
        shell.append(shellBody); shellBody.append(controls); mount.append(shell); outer.append(popHolder);
        const lockedNow = []; let secondsLocked = false;
        const lock = (element, name) => {
            if (!element) return;
            for (const control of [element, ...element.querySelectorAll('button, input, select')]) {
                if (control.disabled) continue;
                control.disabled = true; control.dataset.fvLocked = name; lockedNow.push(control);
            }
            element.dataset.fvLocked = name;
        };
        const state = {shell, prompt, controls, sync: () => { updateRunButton(); consoleUI?.sync?.(); },
            values: () => ({width: Number(value(node, 'width')), height: Number(value(node, 'height')), seconds: Number(value(node, 'seconds'))}),
            // Every setting changed in the bar while it is borrowed (the request is built from these).
            overrides: () => Object.fromEntries(shadow?.get(node) || []),
            setLabel: next => { state.labelFn = next; updateRunButton(); },
            // {shown, checked, disabled} from the editor; null gives the switch back to this panel's own rule.
            firstFrame: next => { if (!borrowed) return; borrowed.firstFrame = next || null; updateFirstFrame(); },
            release({disposing = false} = {}) {
                if (!borrowed) return;
                borrowed = null; shadow = null;
                unlock(); updateFirstFrame();
                shell.remove();
                if (!disposing) {
                    if (home.next?.parentNode === home.parent) home.parent.insertBefore(controls, home.next); else home.parent.append(controls);
                    dialog.append(popHolder);
                }
                updateRunButton();
            }};
        shadow = new Map();
        borrowed = {submit, label: () => (state.labelFn ?? label)?.(), disabled, onSeconds, onFirstFrame, firstFrame: null, applying: false, release: state.release};
        const unlock = () => {
            for (const control of lockedNow.splice(0)) { control.disabled = false; delete control.dataset.fvLocked; }
            for (const element of [framePicker?.element, stepper?.element]) delete element?.dataset.fvLocked;
            if (secondsLocked) { secondsLocked = false; if (!linked(node, 'seconds')) { seconds.disabled = false; stepper?.sync?.(); } }
        };
        // What an entry prefills and locks (new part, continuation, one made again); called again as the choice moves.
        state.apply = ({initial = {}, locked = []}) => {
            unlock(); if (borrowed) borrowed.applying = true;
            if (initial.size) {
                for (const [input, key] of [[width, 'width'], [height, 'height']]) {
                    if (initial.size[key] && Number(input.value) !== Number(initial.size[key])) { input.value = String(initial.size[key]); input.onchange?.(); }
                }
            }
            if (initial.seconds != null && Number(seconds.value) !== Number(initial.seconds)) { seconds.value = String(initial.seconds); seconds.onchange?.(); }
            if (typeof initial.text === 'string') { prompt.value = initial.text; prompt.dispatchEvent(new Event('input', {bubbles: true})); }
            if (locked.includes('size')) lock(framePicker?.element, 'size');
            if (locked.includes('seconds')) { lock(stepper?.element, 'seconds'); if (!seconds.disabled) { secondsLocked = true; seconds.disabled = true; } stepper?.sync?.(); }
            if (borrowed) borrowed.applying = false;
            consoleUI?.sync?.(); updateRunButton();
        };
        state.apply({initial, locked});
        consoleUI?.sync?.(); updateRunButton();
        return state;
    };
    capabilityView = createCapabilityView({node, api, t, getState: () => ({ratio,
        custom: selected === 'custom', steps: Number(baseSteps.value), twoPass: twoPass.checked,
        refine: Number(refineSteps.value), stepCache: stepCacheNow(), upscale: upscaleNow(), disabled: Boolean(extension.source) || dimensionsLinked || seconds.disabled}),
        onEstimate: () => showEffortEstimate(), onGrid: grid => effort.setLevels(effortsFrom(grid)), onPick: point => {
        pixels = point.pixels; mp.value = String(pixels);
        set(node, 'width', point.width); set(node, 'height', point.height);
        node.properties.freevideo_pixels = pixels;
        seconds.value = point.seconds; set(node, 'seconds', point.seconds); syncCanvasSize();
    }});
    rangeMount.append(capabilityView.element); cleanup.push(capabilityView.dispose);
    controls.addEventListener('change', () => capabilityView.update());
    function syncExtension() {
        releaseExtendThumbnail(); extendMount.replaceChildren();
        const source = extension.source;
        extendMount.hidden = !source; mediaMount.classList.toggle('fv-extend-active', Boolean(source)); blockOriginal('extend', Boolean(source), {text: ['"Use as is" is not available while continuing a video. Remove "Continues this video" first.', '接在视频之后时不能“原样使用”，请先移除“接在这条视频后面”。']});
        if (source) runMode = 'single';
        canvasLock.hidden = !source;
        mp.disabled = width.disabled = height.disabled = Boolean(source) || dimensionsLinked;
        for (const shape of shapes.children) shape.disabled = Boolean(source) || dimensionsLinked;
        seconds.value = savedSeconds(node, value(node, 'seconds'));
        pixels = node.properties.freevideo_pixels ?? Number(value(node, 'width')) * Number(value(node, 'height')) / 1024 ** 2;
        if (![...mp.options].some(option => Number(option.value) === pixels)) {
            const option = el('option', `${pixels.toFixed(2)} MP`); option.value = pixels; mp.append(option);
        }
        mp.value = String(pixels);
        ratio = source ? source.geometry.width / source.geometry.height : Number(value(node, 'width')) / Number(value(node, 'height'));
        selected = node.properties.freevideo_aspect || 'custom';
        if (source) {
            selected = [['16:9', 16 / 9], ['9:16', 9 / 16], ['1:1', 1]].find(([, r]) => Math.abs(Math.log(ratio / r)) < .035)?.[0] || 'custom';
            const image = el('img'); image.alt = extendText(t, 'source');
            const label = el('span', extendText(t, 'source'));
            const remove = button('', () => { extension.remove(); syncExtension(); }, 'fv-quiet fv-extend-remove');
            remove.append(icon('close')); remove.title = remove.ariaLabel = extendText(t, 'remove');
            extendMount.append(image, label, remove); mediaDetails.open = true;
            releaseExtendThumbnail = mountExtendThumbnail(image, api, source);
        }
        twoPass.checked = Boolean(source) || value(node, 'two_pass') !== false;
        previewFirst.checked = !source && value(node, 'preview') === true;
        showPrompt(); placeholder();
        // The media panel and the @ list read whether a video is being continued.
        if (mediaNode) window.dispatchEvent(new CustomEvent('freevideo-media', {detail: mediaNode.id}));
        syncSamplingSteps(); syncCanvasSize(); seconds.dispatchEvent(new Event('input'));
        prompt.focus({preventScroll: true});
    }
    current.enterExtend = record => {
        if (!extension.enter(record)) throw new Error(extendText(t, 'unavailable'));
        syncExtension();
    };
    const sourceDeleted = event => {
        if (event.detail?.video === result?.video) links.querySelector('.fv-action-extend')?.remove();
        if (event.detail?.video === extension.source?.video) { extension.remove(); syncExtension(); }
    };
    window.addEventListener('freevideo-library-deleted', sourceDeleted);
    cleanup.push(() => window.removeEventListener('freevideo-library-deleted', sourceDeleted));
    // Trays float: over the lower edge of the picture, anchored on the console, at most 35 % of the stage's height,
    // so opening one never moves the picture. Beside a video this window just made (or while one is being made),
    // on windows of 1280 px and more with room at the right, a tray docks at the right of the picture instead and
    // covers none of it (the history rail steps aside meanwhile).
    function placeTray() {
        const id = consoleUI?.open(), tray = id && consoleUI.trays.querySelector(`[data-tray="${id}"]`);
        // A tray shown as a small window beside its button (glass/tray_pop.js) is placed by that module.
        if (!tray || tray.hasAttribute('data-fv-pop')) { body.dataset.traySide = 'false'; return; }
        // The picture's full box (not the stage, which an empty next frame shrinks while a tray is open), so the tray
        // never moves under the pointer when the stage changes shape.
        const dock = controls.getBoundingClientRect(), space = previewSpace.getBoundingClientRect(), edge = 16;
        const picture = {top: space.top, height: space.height, right: stage.getBoundingClientRect().right};
        const room = window.innerWidth - edge - (picture.right + edge);
        const side = (busy || (freshVideo && result?.video === freshVideo)) && window.innerWidth >= 1280 && room >= 220;
        body.dataset.traySide = String(side);
        if (side) {
            tray.style.left = `${Math.round(picture.right + edge - dock.left)}px`; tray.style.width = `${Math.round(Math.min(400, room))}px`;
            tray.style.maxHeight = `${Math.max(200, Math.round(dock.top - picture.top))}px`;
        } else {
            tray.style.left = '0px'; tray.style.width = '100%';
            // The frame tray shows its options whole (both rows), reaching over the picture if it must but leaving at
            // least 96 px of it for the outline of the new frame.
            // (scrollHeight is inside the border; the limit is on the whole box.)
            const options = id === 'size' && framePicker?.element.isConnected ? tray.scrollHeight + (tray.offsetHeight - tray.clientHeight) : 0;
            const most = Math.max(160, Math.round(dock.top - picture.top - 96));
            tray.style.maxHeight = `${Math.min(most, Math.max(160, Math.round(picture.height * .35), options))}px`;
        }
    }
    function guideItem() {
        const item = el('a', null, 'fv-action fv-action-secondary fvs-phone-only');
        item.href = promptHelp.href; item.target = '_blank'; item.rel = 'noopener noreferrer';
        item.append(el('span', t('How to write a prompt', '描述写法')));
        item.dataset.fvTipName = t('How to write a prompt', '描述写法'); item.dataset.fvTipUse = t('Open the guide to writing a good prompt', '打开描述写法指南，写得更准');
        return item;
    }
    // A press or focus anywhere outside the open tray (the description, another control, the picture, empty space) puts
    // it away with its closing motion, and the press or focus goes on where it was meant (the caret appears at once).
    // Inside the tray, on a chip (which switches trays or toggles its own), or in a menu the tray opened, nothing closes.
    const keepsTray = target => {
        const open = consoleUI?.open(); if (!open || !target?.closest) return true;
        if (target.closest('.fvs-tray[open]') || target.closest('.fvs-chip[aria-controls]')) return true;
        if (target.closest('[popover], [role=listbox], [role=menu], .fv-select-menu, .fv-tip, .fv-ed-expand-dialog')) return true;
        return target.closest('dialog') !== dialog;
    };
    dialog.addEventListener('pointerdown', event => { if (!keepsTray(event.target)) consoleUI.close({animate: true}); }, true);
    // A press on words that take no focus (a switched-off LoRA row's "Click to turn on", a reason) leaves the focus on the
    // panel itself: that is no move elsewhere, and the press above has already decided.
    dialog.addEventListener('focusin', event => { if (event.target !== dialog && !keepsTray(event.target)) consoleUI.close({animate: true}); });
    // ── The console: the prompt, a row of chips and Generate. Each settings group moves, as it is, into a tray
    // above the console; temporary lines go to fixed slots (status slot above the console, the bar under the picture).
    runbar.replaceChildren(runrow);
    stageBar.end.append(queueActions);
    // Duration: the − 5 s + stepper in the console's row (its number is typed in place). It stops at the shortest and
    // at the longest this computer makes (what one run holds, the planner's figure, times the 8 parts a video may
    // have; 60 s until that is known); a typed length beyond either becomes that end. Its hover card says when the
    // length is made in parts.
    const longest = () => { try { const one = lengthControl?.absent ? null : lengthControl?.options().singleLimit; return one > 0 ? Math.max(5, Math.floor(one) * 8) : 60; } catch { return 60; } };
    // A locked length says what decides it: the audio used as is (the old field's lock shows that) or a connected node.
    // The line that says a typed length was brought to the shortest or longest sits with the console's other lines.
    const durationNote = fieldNote(seconds, {t, place: () => lengthWhy.isConnected ? lengthWhy : null, className: 'fv-length-why'});
    const stepper = durationStepper(t, seconds, {max: longest,
        lockedBy: () => (!audioLock.hidden ? 'audio' : linked(node, 'seconds') ? 'linked' : null)});
    seconds.addEventListener('change', () => {
        const n = Number(seconds.value), most = longest(), least = 2;
        if (seconds.value === '' || !Number.isFinite(n) || seconds.disabled) return;
        if (most > 0 && n > most) { seconds.value = String(most); durationNote.say(t(`At most ${most} s`, `最长 ${most} 秒`)); }
        else if (n < least) { seconds.value = String(least); durationNote.say(t(`At least ${least} s`, `最短 ${least} 秒`)); }
    }, true);
    const stepValue = stepper.element.querySelector('.fvs-step-value');
    const paintDuration = () => {
        const plan = lengthControl?.plan, parts = plan?.count > 1 ? plan.count : 0;
        stepValue.dataset.fvTipUse = parts ? t(`Made in ${parts} parts and joined`, `分 ${parts} 段生成并拼接`) : t('Click to type an exact length', '点一下输入精确时长');
        stepper.sync();
    };
    seconds.addEventListener('input', paintDuration); seconds.addEventListener('change', paintDuration);
    // Quality: a button with a small panel beside it, like Frame & size. In the panel, the levels' slider (the thick
    // track with the knob inside, as before), one line under it on what the level gives (no times), and the two-pass
    // choice.
    const qualityChip = createPopChip({host: dialog, kind: 'quality', onOpen: () => paintQuality(), name: t('Quality', '质量'), icon: 'M8 2l1.6 3.6L13.5 6l-2.9 2.7.8 3.9L8 10.7 4.6 12.6l.8-3.9L2.5 6l3.9-.4Z'});
    const LEVEL_WORDS = {Light: ['Fastest, for trying ideas', '最快，适合试效果'], Medium: ['For everyday use', '日常使用'], High: ['More detail, slower', '细节更多，较慢'], Max: ['The most detail, slowest', '细节最多，最慢']};
    const levelUse = el('p', '', 'fvp-level-use'); levelUse.setAttribute('aria-live', 'polite');
    effort.element.after(levelUse); qualityChip.body.append(samplingSettings);
    const qualityLevel = () => effortFor(Number(baseSteps.value), twoPass.checked, Number(refineSteps.value), stepCacheNow(), effort.levels(), upscaleNow());
    const sayLevel = tier => { const said = tier ? t(...(LEVEL_WORDS[tier.name] || ['', ''])) : t('Custom steps', '自定义步数'); if (levelUse.textContent !== said) levelUse.textContent = said; };
    effortPointed = tier => sayLevel(tier || qualityLevel());
    const paintQuality = () => {
        const level = qualityLevel();
        // (While the knob is dragged, the line follows the knob.)
        if (!effort.element.querySelector('.fv-effort-rail[data-dragging]')) sayLevel(level);
        qualityChip.setLabel(level ? effortName(t, level) : t('Custom', '自定义'));
    };
    cleanup.push(() => qualityChip.dispose());
    durationField.hidden = true;
    stepper.element.dataset.fvGuide = 'seconds'; delete seconds.dataset.fvGuide;
    tip(qualityChip.element, t('Quality', '质量'), t('Finer levels take longer', '越细用时越长'));
    // The chip shows the current frame drawn the same way as in the tray (dashed when it follows an image).
    const shapeIcon = createFrameIcon(16), shapeGlyph = shapeIcon.element;
    const enabledCount = rows => Array.isArray(rows) ? rows.filter(row => row && row.enabled !== false).length : 0;
    const mediaCount = () => {
        try {
            const swap = mediaNode && swapState(mediaNode);
            if (swap) return (swap.source ? 1 : 0) + peopleOf(swap).length;
            return enabledCount(JSON.parse(value(mediaNode, 'assets') || '[]'));
        } catch { return 0; }
    };
    const loraCount = () => { try { return enabledCount(JSON.parse(value(loraNode, 'adapters') || '[]')); } catch { return 0; } };
    const shapeName = () => selected === 'input' ? t('Match image', '跟随图片') : studioShapes(t).find(([id]) => id === selected)?.[1] || t('Custom size', '自定义');
    consoleUI = createConsole({t, body, controls, prompt: promptSection, runbar,
        trays: [{id: 'media', title: t('Media', '素材'), element: mediaDetails},
            {id: 'lora', title: 'LoRA', element: loraDetails},
            {id: 'advanced', title: t('Advanced', '高级选项'), element: advancedDetails}],
        chips: [
            {id: 'media', tray: 'media', icon: 'media', read: () => {
                const n = mediaCount();
                return {label: t('Media', '素材'), badge: n || null, tip: [t('Media', '素材'), t('Add reference pictures, video and sound', '加参考图、视频和声音')]};
            }},
            {element: mention},
            {element: framePicker.element},
            {element: stepper.element}, {element: qualityChip.element},
            // Prompt enhancement as a key in this row (lit while on); its versions and Rewrite follow it while it is on.
            {element: enhancer.chip},
            {spacer: true},
            {id: 'lora', tray: 'lora', icon: 'lora', read: () => {
                const n = loraCount();
                return {label: 'LoRA', badge: n || null, tip: ['LoRA', t('Add a style or character model', '叠加风格或人物模型')]};
            }},
            {id: 'advanced', tray: 'advanced', icon: 'sliders', read: () => ({label: '', tip: [t('Advanced', '高级选项'), t('Run mode, seed, steps and parts', '生成方式、种子、步数和分段')]})},
        ],
        status: [requestAssets.element, addVideoNote, lengthWhy, otherUsage.element], onTray: open => {
            placeTray(); fitPreview();
            // An open tray: the play bar and the bar under the picture step back (CSS); they show again when it closes.
            void open;
        }});
    // The two panels repaint whenever the console's row does.
    { const sync = consoleUI.sync; consoleUI.sync = () => { sync(); paintDuration(); paintQuality(); }; }
    paintDuration(); paintQuality();
    // Space plays or pauses the stage video and M turns its sound on or off, unless a field or a control has the key.
    cleanup.push(listenDialogKey(dialog, 'keydown', event => {
        if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing || event.repeat) return;
        if (!(event.key === ' ' || event.key === 'm' || event.key === 'M')) return;
        if (event.target?.closest?.('input, textarea, select, [contenteditable=""], [contenteditable=true], button, summary, a, [role=button], [role=slider], .fvs-tray')) return;
        const video = stage.querySelector('.fv-stage-media video');
        if (!video || stage.dataset.next === 'true') return;
        event.preventDefault();
        if (event.key === ' ') { if (video.paused) video.play().catch(() => {}); else video.pause(); }
        else video.muted = !video.muted;
        playerBar?.sync?.();
    }));
    // The video model warms ahead of Generate, out of sight (studio_warm.js): light on a weak sign (three seconds here, any
    // touch), full on a strong one (typing a prompt, back from a tray to the prompt, choosing a frame, size or length,
    // pointing at Generate); leaving the panel lets it go. Nothing is shown.
    var warm = createWarm(api);
    cleanup.push(() => warm.dispose());
    const warmSoon = setTimeout(() => { if (!disposed && dialog.open) warm.warm('light'); }, 3000);
    cleanup.push(() => clearTimeout(warmSoon));
    const touched = () => { if (dialog.open) warm.warm('light'); };
    dialog.addEventListener('pointerdown', touched, true); cleanup.push(listenDialogKey(dialog, 'keydown', touched));
    prompt.addEventListener('input', () => { if (prompt.value.trim()) warm.warm('full'); });
    seconds.addEventListener('change', () => warm.warm('full'));
    controls.addEventListener('click', event => { if (event.target.closest?.('.fvp-opt, .fvp-seg, .fvp-menu-item')) warm.warm('full'); });
    generate.addEventListener('pointerenter', () => warm.warm('full'));
    prompt.addEventListener('focus', () => { if (prompt.value.trim() && body.dataset.tray) warm.warm('full'); });
    const viewLeft = event => { if (event.detail?.view && event.detail.view !== 'studio') warm.cancel(); };
    const pageLeft = () => warm.cancel({keepalive: true});
    window.addEventListener('freevideo-view', viewLeft); window.addEventListener('pagehide', pageLeft);
    cleanup.push(() => { window.removeEventListener('freevideo-view', viewLeft); window.removeEventListener('pagehide', pageLeft); });
    // An open tray that changes height (its rows wrap, a note appears) refits the next generation's empty frame above it.
    const trayWatch = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { if (nextShown) fitPreview(); }) : null;
    for (const tray of consoleUI.trays.querySelectorAll('.fvs-tray')) trayWatch?.observe(tray);
    cleanup.push(() => trayWatch?.disconnect());
    // The one-line slot sits at the top of the dock, above an open tray and the console, in room kept for it.
    controls.prepend(consoleUI.slot);
    // The run's status line: in that notes line on wider windows (one band between the picture's bar and the console),
    // on its own row under the bar on phones.
    const placeStatus = () => {
        if (!phone?.matches) { if (status.parentElement !== consoleUI.slot) consoleUI.slot.append(status); }
        else if (status.parentElement !== output) reuseRow.before(status);
        fitPreview();
    };
    placeStatus(); phone?.addEventListener?.('change', placeStatus); cleanup.push(() => phone?.removeEventListener?.('change', placeStatus));
    // The band above the console is kept only while its line has something to say (glass/studio.css), so an empty band
    // never sits between the picture and the console.
    const syncBand = () => {
        const said = [...consoleUI.slot.children].some(item => !item.hidden && item.textContent.trim() && getComputedStyle(item).display !== 'none');
        if (controls.dataset.status !== String(said)) controls.dataset.status = String(said);
    };
    const bandWatch = new MutationObserver(syncBand);
    bandWatch.observe(consoleUI.slot, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['hidden', 'data-state']});
    syncBand(); cleanup.push(() => bandWatch.disconnect());
    // A note that takes two lines (the add-video answer on a short line) makes the band as tall as the line it fills, so
    // it never covers the console (glass/studio.css --fvs-band).
    const bandSize = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
        const height = `${Math.ceil(consoleUI.slot.getBoundingClientRect().height)}px`;
        if (controls.style.getPropertyValue('--fvs-band') !== height) controls.style.setProperty('--fvs-band', height);
    }) : null;
    bandSize?.observe(consoleUI.slot); cleanup.push(() => bandSize?.disconnect());
    // Receipts (filled in, preset applied or saved) go to the shared toast in the top bar's right column (a bottom strip
    // on phones): 5 s, 8 s with Undo, held while hovered, Esc closes, failures stay. Until the shared toast takes an
    // Undo action (toast.actions), a receipt with Undo uses the console's status slot instead, so Undo is never lost.
    const notice = el('div', '', 'fvs-console-notice'); notice.hidden = true; notice.setAttribute('role', 'status');
    consoleUI.slot.prepend(notice);
    let noticeTimer = 0;
    const notify = (text, undo = null, tone = 'ok') => {
        const withUndo = typeof undo === 'function';
        if (!withUndo || toast.actions === true) {
            // A failure stays until closed (sticky); a toast without that option keeps it for 15 s.
            toast(text, {tone, ms: tone === 'fail' && toast.actions !== true ? 15000 : withUndo ? 8000 : 5000, sticky: tone === 'fail',
                ...(withUndo ? {action: {label: t('Undo', '撤销'), run: undo}} : {})});
            return true;
        }
        clearTimeout(noticeTimer); notice.replaceChildren(text);
        if (typeof undo === 'function') {
            const back = button(t('Undo', '撤销'), () => { undo(); notice.hidden = true; }, 'fvs-notice-undo');
            notice.append(' · ', back);
        }
        notice.hidden = false; noticeTimer = setTimeout(() => { notice.hidden = true; }, 8000);
        return false;
    };
    // Reuse settings in the bar under the picture reports the same way once the shared toast takes Undo.
    reuse.receipt = (text, undo) => toast.actions === true && notify(text, undo);
    cleanup.push(() => clearTimeout(noticeTimer));
    // Prompt history, templates and presets (F4): history is written when a request is accepted, below in submitDraft.
    const levelNow = () => effortFor(Number(baseSteps.value), twoPass.checked, Number(refineSteps.value), stepCacheNow(), effort.levels(), upscaleNow());
    const enhanceSwitch = () => enhancer.element.querySelector('.fv-enhance-toggle input');
    const describeSettings = item => {
        const level = item.level && SAMPLING_LEVELS().find(tier => tier.name === item.level);
        const shape = item.aspect && studioShapes(t).find(([id]) => id === item.aspect)?.[1];
        const size = item.w && item.h ? `${item.w} × ${item.h}` : item.pixels ? `${Number(item.pixels).toFixed(2)} MP` : '';
        return [shape, size, Number(item.seconds) > 0 ? t(`${item.seconds} s`, `${item.seconds} 秒`) : '', level ? effortName(t, level) : '',
            typeof item.prompt === 'string' ? t('with prompt', '含描述') : ''].filter(Boolean).join(' · ');
    };
    const SAMPLING_LEVELS = () => effort.levels();
    promptLibrary = createPromptLibrary({t, host: consoleUI.element, notify, describe: describeSettings, guide: promptHelp.href,
        fill: text => {
            if (prompt.disabled) return null;
            const before = value(node, 'text');
            set(node, 'text', text); window.dispatchEvent(new CustomEvent('freevideo-reference-prompt', {detail: node.id}));
            prompt.focus({preventScroll: true});
            return () => { set(node, 'text', before); window.dispatchEvent(new CustomEvent('freevideo-reference-prompt', {detail: node.id})); };
        },
        snapshot: ({prompt: withPrompt, seed: withSeed}) => {
            let loras = [];
            try { loras = JSON.parse(value(loraNode, 'adapters') || '[]').map(row => ({name: row.name, strength: row.strength ?? 1, enabled: row.enabled !== false})); } catch {}
            return {...(withPrompt ? {prompt: prompt.value} : {}), aspect: selected, pixels, w: Number(value(node, 'width')), h: Number(value(node, 'height')),
                seconds: Number(seconds.value), level: levelNow()?.name || null,
                steps: {base: Number(baseSteps.value), refine: Number(refineSteps.value), twoPass: twoPass.checked, stepCache: stepCacheNow(), upscale: upscaleNow()},
                seedMode: mode.value, ...(withSeed ? {seed: Number(seed.value)} : {}), loras, enhance: Boolean(enhanceSwitch()?.checked)};
        },
        apply: async settings => {
            const problems = [];
            if (typeof settings.prompt === 'string' && !prompt.disabled) { set(node, 'text', settings.prompt); window.dispatchEvent(new CustomEvent('freevideo-reference-prompt', {detail: node.id})); }
            if (!dimensionsLinked && !extension.source) {
                const shape = studioShapes(t).find(([id]) => id === settings.aspect);
                if (shape && Number(settings.pixels) > 0) { selected = shape[0]; ratio = shape[2]; choosePixels(Number(settings.pixels)); }
                else if (settings.w > 0 && settings.h > 0) setSize(settings.w, settings.h);
            }
            if (Number(settings.seconds) > 0 && !seconds.disabled) {
                seconds.value = String(settings.seconds);
                seconds.dispatchEvent(new Event('input', {bubbles: true})); seconds.dispatchEvent(new Event('change', {bubbles: true}));
            }
            if (!baseSteps.disabled) {
                const tier = settings.level && effort.levels().find(level => level.name === settings.level);
                if (tier) chooseEffort(tier);
                else if (settings.steps) {
                    baseSteps.value = String(settings.steps.base); refineSteps.value = String(settings.steps.refine); twoPass.checked = Boolean(settings.steps.twoPass);
                    set(node, 'two_pass', twoPass.checked); set(node, 'step_cache', settings.steps.stepCache || 'none'); setUpscale(settings.steps.upscale || 2);
                    syncSamplingSteps();
                }
            }
            if (settings.seedMode && !linked(node, 'control_after_generate')) { mode.value = settings.seedMode; set(node, 'control_after_generate', mode.value); }
            if (Number.isSafeInteger(settings.seed) && !seed.disabled) { set(node, 'seed', settings.seed); seed.value = String(settings.seed); }
            if (Array.isArray(settings.loras) && loraNode && widget(loraNode, 'adapters')) {
                let names = [];
                try { const reply = await api.fetchApi('/models/loras', {cache: 'no-store'}); names = reply.ok ? await reply.json() : []; } catch {}
                const rows = settings.loras.filter(row => names.includes(row.name)), missing = settings.loras.filter(row => !names.includes(row.name)).map(row => row.name);
                if (set(loraNode, 'adapters', JSON.stringify(rows))) window.dispatchEvent(new CustomEvent('freevideo-loras', {detail: loraNode.id}));
                if (missing.length) problems.push(t(`LoRA not found: ${missing.join(', ')}`, `找不到 LoRA：${missing.join('、')}`));
            }
            const toggle = enhanceSwitch();
            if (typeof settings.enhance === 'boolean' && toggle && !toggle.disabled && toggle.checked !== settings.enhance) toggle.click();
            consoleUI.sync();
            return problems.join(' · ');
        }});
    promptTools.prepend(promptLibrary.button);
    cleanup.push(() => promptLibrary.dispose());
    prompt.addEventListener('keydown', event => {
        if ((event.metaKey || event.ctrlKey) && event.key === 'ArrowUp' && !event.isComposing) { event.preventDefault(); promptLibrary.show('history'); }
    });
    // The command palette (Ctrl/Cmd+K) and the shortcut sheet list this panel's actions while it is open.
    const offCommands = window.FreeVideoCommands?.registerCommands?.('studio', [
        {id: 'studio.generate', title: ['Generate video', '生成视频'], key: 'Mod+Enter', icon: 'spark',
            why: () => generate.disabled ? [generate.dataset.fvTipWhy || 'Not available right now', generate.dataset.fvTipWhy || '现在不能开始'] : '', run: () => generate.click()},
        {id: 'studio.tray.size', title: ['Frame & size', '画幅与尺寸'], group: ['Settings', '设置'], run: () => framePicker.element.click()},
        {id: 'studio.tray.duration', title: ['Duration', '时长'], group: ['Settings', '设置'], run: () => stepValue.click()},
        {id: 'studio.tray.quality', title: ['Quality', '质量'], group: ['Settings', '设置'], run: () => qualityChip.show()},
        ...[['media', ['Media', '素材']], ['lora', ['LoRA', 'LoRA']], ['advanced', ['Advanced', '高级选项']]]
            .map(([id, title]) => ({id: `studio.tray.${id}`, title, group: ['Settings', '设置'], run: () => consoleUI.toggle(id, true)})),
        {id: 'studio.history', title: ['Prompt history', '提示词历史'], key: 'Mod+ArrowUp', run: () => promptLibrary.show('history')},
        {id: 'studio.templates', title: ['Prompt templates', '提示词模板'], run: () => promptLibrary.show('templates')},
        {id: 'studio.presets', title: ['Presets', '参数预设'], keywords: ['preset', '预设'], run: () => promptLibrary.show('presets')},
        {id: 'studio.compare', title: ['Compare with another video', '和另一条视频对比'],
            why: () => compareButton.disabled ? ['Needs a second video in the history', '历史里还要有另一条视频'] : '', run: () => compareButton.click()},
    ]);
    if (typeof offCommands === 'function') cleanup.push(offCommands);
    import('./shortcuts.js').then(({registerShortcuts}) => {
        if (disposed || typeof registerShortcuts !== 'function') return;
        const off = registerShortcuts('studio', [{keys: 'Mod+Enter', title: ['Generate', '生成']}, {keys: 'Mod+ArrowUp', title: ['Prompt history', '提示词历史']},
            {keys: 'Escape', title: ['Close the open panel or tray', '关闭打开的面板或托盘']}]);
        if (typeof off === 'function') cleanup.push(off);
    }).catch(() => {});
    // Enhancement progress and its errors read in the console's status slot (one line, fixed height).
    const enhanceStatus = enhancer.element.querySelector('.fv-enhance-status');
    if (enhanceStatus) consoleUI.slot.append(enhanceStatus);
    enhancer.chip.after(enhancer.bar);
    consoleUI.trays.querySelector('[data-tray=size] .fvs-tray-title')?.append(canvasLock);
    consoleUI.element.classList.add('sgf-avoid');
    mention.dataset.fvTipName = t('Mention', '引用');
    mention.dataset.fvTipUse = t('Bring a picture or character into the prompt', '在描述里引用素材或角色'); mention.dataset.fvTipKey = '@';
    // The Generate button's hover words follow its label (updateRunButton).
    updateRunButton();
    // The chips repeat what the trays hold: read them again whenever a setting or its estimate changes.
    const setEstimate = effort.setEstimate;
    effort.setEstimate = (...args) => { setEstimate(...args); consoleUI?.sync(); updateRunButton(); };
    for (const type of ['freevideo-media', 'freevideo-loras']) {
        const resync = () => consoleUI?.sync();
        window.addEventListener(type, resync); cleanup.push(() => window.removeEventListener(type, resync));
    }
    controls.addEventListener('change', () => consoleUI?.sync());
    document.body.append(dialog); dialog.showModal(); viewChanged('studio', node); updateCanvas();
    // The picture's box is fitted before the first frame is painted (and again if the glass layer was still loading).
    // The picture's box is fitted as soon as the glass layer applies; data-fv-ready waits for the content (markReady).
    glassReady.then(() => { if (!disposed) fitPreview(); });
    // The shell waits for this before it fades the view in: the picture has its final size now.
    // The shell fades the view in once this is set: the picture has its size and its first content (a video, the
    // progress, or the empty frame once the queue and the library have answered), at most 600 ms after opening.
    function markReady() {
        glassReady.then(() => { if (disposed || dialog.dataset.fvLoaded === 'true') return; fitPreview(); dialog.dataset.fvReady = 'true'; dialog.dataset.fvLoaded = 'true'; });
    }
    const readyTimer = setTimeout(markReady, 600); cleanup.push(() => clearTimeout(readyTimer));
    if (extension.source) syncExtension();
    let guide = null, closed = false;
    cleanup.push(() => { closed = true; guide?.dispose(); });
    agentDiscovery.then(() => {
        if (!closed) guide = createFirstRunGuide({dialog, api, cn, isBusy: () => busy || queued || capturing, targets: {media: mediaGuideTarget},
            onShowcaseTry: () => openAutopilot(node, {studio: dialog, initialScene: SHOWCASE_SCENE}), onTry: tryTourExample});
    });
    firstStart = createFirstStart({dialog, api, cn, prompt,
        onPrefill: text => {
            if (linked(node, 'text') || extension.source || prompt.value.trim()) return false;
            set(node, 'text', text); prompt.value = text; placeholder(); updateRunButton(); return true;
        }, onGenerate: () => { if (generate.disabled) return false; generate.click(); return true; }, onEdit: () => openEditorView(node, {studio: dialog}),
        isBusy: () => busy || queued || capturing});
    cleanup.push(() => firstStart.dispose());
    const tourContext = {dialog, api, cn, onError: error => tourFault('', error), onTry: tryTourExample, onStartClip: tryStartClip, canTry: canTryTour, isBusy: () => busy || queued || capturing};
    cleanup.push(registerTourWorkspace(tourContext));
    const tourTimer = setTimeout(async () => { const state = await firstStart.ready; if (!closed && !state?.firstRun && !tourContext.isBusy()) offerVersionTour(tourContext); }, 1400);
    cleanup.push(() => clearTimeout(tourTimer));
    const recentLoad = recent.refresh();
    if (node.freevideoProgress && !node.freevideoProgress.chain) { showProgress(node.freevideoProgress); markReady(); }
    else if (result) showResult(result);
    // Do not change any saved width/height simply by opening a workflow.
    syncQueue().then(async () => {
        if (disposed) return;
        if (busy) { showProgress(node.freevideoProgress || {label: queued ? t('Waiting in queue', '正在排队') : t('Loading progress', '正在读取进度')}); }
        else if (!result) {
            const saved = (await recentLoad)[0];
            if (!disposed && !busy && !result && saved) showResult(saved);
        }
        markReady();
    }).catch(() => markReady());
    // Opening leaves focus on the panel itself, not on the main button with a ring as if already pressed; Tab still reaches it.
    dialog.tabIndex = -1;
    requestAnimationFrame(() => { if (dialog.open && !exampleFocused && !dialog.querySelector('.fv-guide-layer') && !document.querySelector('dialog.fv-editor[open]')) (extension.source ? prompt : dialog).focus({preventScroll: true}); });
    // Establish the baseline after UI initialization; opening a panel is not an edit.
    trackPromptDraft(node);
    return dialog;
}
