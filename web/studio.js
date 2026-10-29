import {createLanAccess} from './lan_access.js';
import { createErrorPanel, errorText, createErrorReport } from './error_panel.js';
import {createExpandButton} from './editor_expand.js';
import {allowEmptyPrompt, cleanOptionalPrompt, clonePrompt} from './optional_inputs.js';
import {showEmptyPrompt, dismissEmptyPrompt} from './empty_prompt_notice.js';
import { app } from '../../scripts/app.js';
import { api } from '../../scripts/api.js';
import { openSetup } from './setup.js';
import { feedbackButton, feedbackIssueURL } from './feedback.js';
import { wordmark } from './wordmark.js';
import { createGenerationProgress, referenceTrimText, thermalText, sharedGpuText, keepSoundText, encoderFileText, encoderFilePath } from './generation_progress.js';
import { viewSwitch, viewChanged, agentTabReady, openAutopilot } from './view_navigation.js';
import { agentInterfaceState } from './agent_connect.js';
import { createUpdateNotice, createVersionInfo, productVersion } from './updates.js';
import { createPortNotice } from './port_notice.js';
import { focusReportTip, hideReportTip, reportIssue, reportTipClosed, showReportTip } from './report_issue.js';
import { issueButton } from './issue_button.js';
import { createPreviewScene } from './preview_scene.js?v=20260929-swell';
import { animateDetails, closeDialog } from './motion.js';
import { openLibrary } from './library.js';
import { createRecentVideos } from './studio_recent.js';
import { createAgentActivity } from './agent_activity.js';
import { createStudioQueue, randomSeed, turnNodeOn } from './studio_queue.js';
import { isNetworkTypeError, connectionLostText } from './browser_extension.js';
import { attachReferencePicker, referenceItems, enhancementMedia, syncReferencePrompt, referenceInputs, mentionAt, handleNames, mediaRows } from './prompt_references.js';
import { attachTermSuggest } from './prompt_terms.js';
import { castInRows, expandCast, mentionHandle, mentionState, mentionedIds, withCharacter, MAX_PICTURES_EACH } from './cast_mention.js';
import { attachMentionOverlay } from './cast_overlay.js';
import { openCastLibrary } from './cast_source.js';
import { uploadMedia } from './media_upload.js';
import { reconcileLoraTriggers } from './lora_triggers.js';
import { createVerdicts, describeVerdict, feedbackDraft } from './lora_check.js';
import { continuationUpscale, createSamplingEffort, effortFor, effortsFrom, passSwitch, stepCacheOf, upscaleOf } from './sampling_effort.js';
import { resultActions, previewBadge, icon, actionsUnavailable } from './result_actions.js';
import { framesOf } from './result_reuse.js';
import { createPromptEnhancer } from './prompt_enhance.js';
import { createRequestAssets, enabledLoras, referenceFiles } from './request_assets.js';
import { promptDraftTrouble, rememberPromptDraft, restorePromptDraft, savePromptDraft, trackPromptDraft, watchPromptDraftTrouble } from './prompt_draft.js';
import { canvasDimensions, imageSizeSelection } from './studio_size.js';
import { createCapabilityView } from './capability_view.js';
import { openEditorView } from './editor_loader.js';
import { frameShapes, frameSize } from './frame_sizes.js';
import { createFirstRunGuide, createFirstStart } from './first_run_guide.js';
import { SHOWCASE_SCENE } from './entry_showcase.js';
import { registerTourWorkspace, triggerFeatureTour, offerVersionTour } from './feature_tour.js';
import { createStudioLength, savedSeconds } from './studio_length.js?v=20261011-length';
import { firstFrameRow } from './first_frame_row.js';
import { createReplacePerson, swapMissingText } from './replace_person.js';
import { swapState, swapMissing, effectiveRows, prepareSwapLora, releaseSwapLora, SWAP_LORA } from './replace_state.js';
import { createStudioChain, chainPosition, chainParts, chainSplit, composePartText, readChainResult, sumPartEstimates, singleRunSnapshot } from './studio_chain.js?v=20261019-words';
import { activeOriginal, checkOriginal, lockMark, blockOriginal, originalBlocked, usesOriginal, referenceRow } from './audio_original.js';
import { waitingText } from './chain_support.js';
import { createExtendState, extendText, extendSourceProblem, extendSourceState, readExtendRecord, extendSnapshot, mountExtendThumbnail, videoUnavailableText } from './studio_extend.js';
import { topDialog, isolateDialogKeys, listenDialogKey, routeDialogKey } from './dialog_keys.js';

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
function loraStrengthBox(initial, commit) {
    const input = document.createElement('input'), mark = document.createElement('span');
    input.type = 'number'; input.step = '.05'; input.value = initial;
    mark.id = 'fv-lora-range-' + ++loraRangeCount; input.setAttribute('aria-describedby', mark.id);
    mark.className = 'fv-lora-range'; mark.textContent = t('Outside usual range', '超出常用范围');
    const sync = () => {
        const number = Number(input.value), filled = input.value !== '';
        mark.hidden = !(filled && Math.abs(number) > LORA_USUAL && Math.abs(number) <= LORA_LIMIT);
        input.setCustomValidity(filled && !(Math.abs(number) <= LORA_LIMIT) ? t('Enter a value from -100 to 100.', '请输入 -100 到 100 之间的数。') : '');
    };
    input.oninput = sync; input.onchange = () => { sync(); if (input.reportValidity()) commit(); };
    sync();
    return {input, mark};
}
const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('./studio.css', import.meta.url).href; document.head.append(css);
const effortCss = document.createElement('link'); effortCss.rel = 'stylesheet'; effortCss.href = new URL('./sampling_effort.css', import.meta.url).href; document.head.append(effortCss);
const enhanceCss = document.createElement('link'); enhanceCss.rel = 'stylesheet'; enhanceCss.href = new URL('./prompt_enhance.css', import.meta.url).href; document.head.append(enhanceCss);
const el = (tag, label, cls) => { const e = document.createElement(tag); if (label != null) e.textContent = label; if (cls) e.className = cls; return e; };
// One line per part of a long video, under the preview. A part that has not started opens in
// place for editing and saves when it loses focus; the server's reply decides what stays.
// After the video stops, an ended part can be made again from there on as a new video.
function createChainPartList(t, save, {onShow = () => {}, onHide = () => {}, redo = async () => {}, resume = async () => {}, playSeam = null} = {}) {
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
            if (rows.get(row.part.id) === row) flash(row, error.status === 409 ? t('Another video is generating. Try again when it finishes.', '正在生成别的视频，完成后再试。')
                : t('Could not start. Try again.', '没能开始，请重试。'), 'error');
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
const value = (node, name) => widget(node, name)?.value;
function set(node, name, value) {
    const w = widget(node, name); if (!w || linked(node, name)) return false;
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

export const loraWarning = () => t(
    'FreeVideo needs no acceleration LoRA.',
    'FreeVideo 无需添加加速 LoRA');

export function promptGuide() {
    const note = el('p', null, 'fv-prompt-guide');
    const link = el('a', 'MiniMax H3 Prompt Skill ↗');
    link.href = 'https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md';
    link.target = '_blank'; link.rel = 'noopener noreferrer';
    note.append(document.createTextNode(t('For better results, ask AI to rewrite your prompt using ', '让 AI 参考 ')),
        link, document.createTextNode(t('. We recommend ', ' 重写提示词效果会更好。推荐使用 ')));
    const freeToken = el('a', 'FreeToken ↗');
    freeToken.href = 'https://www.flashml.ai/';
    freeToken.target = '_blank'; freeToken.rel = 'noopener noreferrer';
    note.append(freeToken, document.createTextNode(t('.', '。')));
    return note;
}

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
    const idleNote = () => loraWarning() + (skipped ? t(skipped === 1 ? ' 1 other LoRA file in the folder is not in .safetensors format and is not listed.' : ` ${skipped} other LoRA files in the folder are not in .safetensors format and are not listed.`, ` 文件夹中另有 ${skipped} 个 LoRA 文件不是 .safetensors 格式，未列出。`) : '');
    const verdicts = createVerdicts((path, options) => api.fetchApi(path, options));
    let rowChecks = [];
    let focusGrip = null;
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
        } catch (error) { note.textContent = error.message; }
    }
    const write = rows => { if (set(node, 'adapters', JSON.stringify(rows))) { syncPrompt(rows); window.dispatchEvent(new CustomEvent('freevideo-loras', {detail: node.id})); } };
    // A LoRA the model cannot take is switched off here, so it is not sent with the request.
    function disableUnusable(name) {
        if (disposed) return;
        try {
            const rows = read(); let changed = false;
            for (const row of rows) if (row.name === name && row.enabled !== false && verdicts.peek(name)?.usable === false) { row.enabled = false; changed = true; }
            if (changed) write(rows);
        } catch (error) { note.textContent = error.message; }
    }
    const reorder = order => {
        const rows = read();
        if (order.length !== rows.length || order.every((from, to) => from === to)) return;
        write(order.map(from => rows[from]));
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
            syncPrompt(rows);
            rows.forEach((row, index) => {
                const wrap = el('div', null, 'fv-lora-row'); wrap.dataset.enabled = String(row.enabled !== false);
                const enabled = el('input'); enabled.type = 'checkbox'; enabled.checked = row.enabled !== false;
                enabled.setAttribute('aria-label', t('Enable LoRA', '启用 LoRA')); enabled.onchange = () => { row.enabled = enabled.checked; write(rows); };
                const select = el('select'); select.setAttribute('aria-label', t('LoRA model', 'LoRA 模型'));
                const pinned = recentLoras().filter(n => names.includes(n));
                for (const name of [...new Set([...pinned, ...names, row.name])].filter(Boolean)) { const option = el('option', name); option.value = name; select.append(option); }
                select.value = row.name; select.title = row.name;
                select.onchange = () => {
                    // Leaving a LoRA that could not be used turns the row back on for the new choice.
                    if (verdicts.peek(row.name)?.usable === false) row.enabled = true;
                    row.name = select.value; rememberLora(row.name); write(rows);
                };
                const {input: strength, mark: strengthMark} = loraStrengthBox(row.strength ?? 1, () => { if (strength.value !== '') { row.strength = Number(strength.value); write(rows); } });
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
                const remove = button('×', () => { rows.splice(index, 1); write(rows); }); remove.setAttribute('aria-label', t('Remove LoRA', '移除 LoRA'));
                wrap.append(enabled, select, strength, remove, trigger, strengthMark);
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
                loadTriggers(row.name).catch(error => { if (!disposed && trigger.isConnected) note.textContent = error.message; });
            });
            if (rows.length > 1) actions.append(clear);
            if (focusGrip != null) { list.querySelector(`.fv-lora-row[data-index="${focusGrip}"] .fv-lora-grip`)?.focus(); focusGrip = null; }
            note.textContent = rows.map(row => errors.get(row.name)).find(Boolean) || idleNote();
        } catch (error) {
            if (error instanceof DOMException) throw error;
            note.textContent = error.message;
        }
        finally {
            rendering = false;
            if (renderAgain) { renderAgain = false; queueMicrotask(render); }
        }
    }
    const add = button(t('Add', '添加'), async () => {
        await refresh(); // The folder may have changed since the list was last read.
        try { const rows = read(); if (rows.length >= 32) throw new Error(t('Use at most 32 LoRAs', '最多使用 32 个 LoRA')); if (!names.length) throw new Error(t('Put LoRA files (.safetensors) in the models/loras folder, then choose "Add".', '把 LoRA 文件（.safetensors）放进 models/loras 文件夹，再点“添加”。')); const next = recentLoras().find(n => names.includes(n) && !rows.some(row => row.name === n)) ?? names[0]; rememberLora(next); rows.push({name: next, strength: 1, enabled: true}); write(rows); } catch (error) { note.textContent = error.message; }
    });
    async function refresh() {
        try { const response = await api.fetchApi('/models/loras', {cache: 'no-store'}); if (!response.ok) throw new Error(response.statusText); const listed = await response.json(); verdicts.clear(); skipped = (Array.isArray(listed) ? listed : []).filter(n => typeof n === 'string' && /\.(ckpt|pt|pth|bin|sft)$/i.test(n)).length; names = (Array.isArray(listed) ? listed : []).filter(n => typeof n === 'string' && n.toLowerCase().endsWith('.safetensors')); render(); }
        catch (error) { if (!disposed) note.textContent = shownMessage(error); }
    }
    // Only deselects: the files stay in the folder and the recent names are kept.
    const clear = button(t('Clear', '清空'), () => { write([]); });
    actions.append(add);
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
    if (dialog?.open) closeDialog(dialog);
    viewChanged('nodes', node);
    // Fit the graph in the visible canvas; never rewrite users' node positions.
    const nodes = node.graph?._nodes || [], canvas = app.canvas?.canvas;
    if (!nodes.length || !canvas) return;
    const left = Math.min(...nodes.map(n => n.pos[0])), top = Math.min(...nodes.map(n => n.pos[1] - 32));
    const width = Math.max(...nodes.map(n => n.pos[0] + n.size[0])) - left;
    const height = Math.max(...nodes.map(n => n.pos[1] + n.size[1])) - top;
    const scale = Math.min(1, (canvas.clientWidth - 96) / width, (canvas.clientHeight - 96) / height);
    if (scale > 0) {
        app.canvas.ds.scale = scale;
        app.canvas.ds.offset = [-left + (canvas.clientWidth / scale - width) / 2, -top + (canvas.clientHeight / scale - height) / 2];
        app.canvas.setDirty(true, true);
    }
}

const studioQueues = new WeakMap();
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
    if (current?.node === node && current.dialog.open) {
        if (extendFrom) current.enterExtend(extendFrom); else current.dialog.focus();
        viewChanged('studio', node); return current.dialog;
    }
    current?.dialog.close();
    const extension = createExtendState(node, name => value(node, name), (name, v) => set(node, name, v));
    if (extendFrom && !extension.enter(extendFrom)) throw new Error(extendText(t, 'unavailable'));
    const dialog = el('dialog', null, 'fv-studio'); dialog.setAttribute('aria-label', t('FreeVideo creative workspace', 'FreeVideo 创作面板'));
    // ComfyUI's background dialogs can lock body pointer events. This native
    // modal is above them and must remain interactive when the launcher reopens it.
    dialog.style.pointerEvents = 'auto';
    current = {node, dialog}; const cleanup = [];
    let firstStart = null, exampleFocused = false;
    const header = el('header', null, 'fv-header'), brand = el('div', null, 'fv-brand');
    brand.append(wordmark());
    const tools = el('div', null, 'fv-header-actions');
    const agents = createAgentActivity(t, api, dialog); tools.append(agents.element); cleanup.push(agents.dispose);
    const phoneAccess = createLanAccess(t, api); tools.append(phoneAccess.element); cleanup.push(phoneAccess.dispose);
    const versionInfo = createVersionInfo(cn); tools.append(versionInfo.element); cleanup.push(versionInfo.dispose);
    tools.append(feedbackButton(t), button(t('Settings', '设置'), openSetup, 'fv-quiet'));
    // The Agent's page is the fourth tab, shown where the Agent interface is on; the guide's last step circles it.
    agentTabReady(agentInterfaceState(api), () => agentInterfaceState(api));
    // The length hint links to the same two views, through the same actions.
    const toEditor = () => { firstStart?.dismiss(); return openEditorView(node, {studio: dialog}); };
    const toAutopilot = () => { firstStart?.dismiss(); return openAutopilot(node, {studio: dialog}); };
    const navigation = viewSwitch('studio', () => {}, () => showNodes(node, dialog), toEditor, toAutopilot);
    const autopilotTab = navigation.querySelector('[data-view="autopilot"]');
    // The guide starts after the tab settles: it fixes its steps from the anchors present at that moment.
    const agentDiscovery = navigation.ready;
    header.append(brand, navigation, tools);
    const body = el('div', null, 'fv-body'), controls = el('div', null, 'fv-controls'), output = el('div', null, 'fv-preview-column');
    dialog.append(header, body); body.append(controls, output);
    const portNotice = createPortNotice(cn);
    if (portNotice) output.append(portNotice);
    const updateNotice = createUpdateNotice(cn);
    output.append(updateNotice.element); cleanup.push(updateNotice.dispose);
    const section = (label, parent = controls) => { const wrap = el('section', null, 'fv-section'); wrap.append(el('div', label, 'fv-label')); parent.append(wrap); return wrap; };
    const field = (name, input) => { const label = el('label', null, 'fv-field'); label.append(el('span', name), input); return label; };
    const expand = label => { const d = el('details', null, 'fv-section'); d.append(el('summary', label, 'fv-section-title')); const content = el('div', null, 'fv-expand'); d.append(content); controls.append(d); cleanup.push(animateDetails(d)); return [d, content]; };
    syncReferencePrompt(node, t);
    // An untouched node shows its starter prompt as a placeholder and generates it.
    // Once something was typed, an emptied box stays empty and asks for a scene.
    const starter = node.freevideoStarterPrompt || '';
    const untouched = () => Boolean(starter) && value(node, 'text') === starter;
    const shown = text => starter && text === starter ? '' : String(text || '');
    const prompt = el('textarea'); prompt.value = shown(value(node, 'text')); prompt.setAttribute('aria-label', t('Prompt', '提示词'));
    prompt.dataset.fvGuide = 'describe';
    const placeholder = () => { prompt.placeholder = extension.source ? extendText(t, 'prompt') : untouched() ? starter : t('Describe the scene… Type @ to reference media', '描述画面… 输入 @ 引用素材'); };
    placeholder();
    prompt.disabled = linked(node, 'text'); prompt.oninput = () => { set(node, 'text', prompt.value); placeholder(); };
    const promptEditor = el('div', null, 'fv-prompt-editor'); promptEditor.append(prompt);
    // Library characters offered in the @ list; a mention puts the character's pictures in the media list.
    let castLibrary = null;
    const castAssets = () => { const owner = upstream(node, 'media'); const field = owner && widget(owner, 'assets'); return field ? {owner, field} : null; };
    // The library's own upload of a picture goes to the same streaming upload the media panel uses.
    const castUpload = file => uploadMedia(api, file);
    const castBlocked = reason => ({frames: t('Not available with first and last frames', '首尾帧不能和角色同时使用'),
        people: t('Up to 3 characters', '最多 3 个角色'), full: t('Up to 32 references', '参考素材最多 32 个'),
        continued: t('Up to 3 images when continuing', '接续时最多 3\u00a0张')}[reason] || '');
    const castPending = new Map();
    // The feature tour's "try it": one example into this panel's media list, prompt and settings.
    const canTryTour = entry => !busy && !queued && !capturing && (!entry.prompt || !linked(node, 'text'))
        && (!entry.assets?.length || Boolean(castAssets()))
        && Object.keys(entry.settings || {}).every(name => name === 'quality'
            ? Boolean(effort.levels().find(tier => tier.name === entry.settings.quality)) && !baseSteps.disabled && ['base_steps', 'two_pass', 'step_cache', 'upscale_factor'].every(key => widget(node, key) && !linked(node, key))
            : Boolean(widget(node, name)) && !linked(node, name))
        && ((entry.settings?.two_pass !== true && entry.settings?.preview !== true) || !twoPass.disabled);
    const tryTourExample = async example => {
        if (!canTryTour(example)) { fault(t('Could not complete. Try again.', '没有完成，请重试')); return {ok: false, reason: 'unavailable'}; }
        const {applyTourExample} = await import('./tour_try.js');
        const target = castAssets();
        const result = await applyTourExample({node, api, cn, rows: () => mediaRows(node), canApply: canTryTour,
            setPrompt: text => { set(node, 'text', text); prompt.value = text; placeholder(); },
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
                selected = frameShapes(t).find(([, , aspect]) => Math.abs(aspect - ratio) < .01)?.[0] || 'custom'; node.properties.freevideo_pixels = pixels; node.properties.freevideo_aspect = selected;
            }
            syncSamplingSteps(); syncCanvasSize(); seconds.dispatchEvent(new Event('input')); updateRunButton();
            if (example.assets?.length) mediaDetails.open = true;
            // The lazy entry switches back through the editor before applying an example.
            if (topDialog() !== dialog) { fault(t('Could not complete. Try again.', '没有完成，请重试')); return {ok: false, reason: 'workspace_changed'}; }
            exampleFocused = true; generate.focus({preventScroll: true});
        } else fault(t('Could not complete. Try again.', '没有完成，请重试'));
        return result;
    };
    function castChoices() {
        if (!castLibrary || linked(node, 'text') || !castAssets()) return [];
        const rows = mediaRows(node);
        return castLibrary.list().filter(card => card.name).map(card => {
            const state = mentionState(rows, card, Math.min(MAX_PICTURES_EACH, Number(card.images) || 0), {continued: Boolean(extension.source)});
            return {id: card.id, name: card.name, handle: mentionHandle(rows, card, castPending), thumb: card.thumbnail?.file ? view(card.thumbnail.file) : card.thumbnail?.url || '',
                blocked: state.ok ? '' : castBlocked(state.reason)};
        });
    }
    // The name goes into the text at once; the card's pictures follow when the full card has been read.
    function mentionCharacter(person) {
        if (!castLibrary) return false;
        if (castInRows(mediaRows(node)).some(entry => entry.id === person.id)) return true;
        if (!mentionState(mediaRows(node), {id: person.id}, 1, {continued: Boolean(extension.source)}).ok) return false;
        const typed = person.handle || person.name;
        castPending.set(person.id, typed);
        castLibrary.get(person.id).then(card => {
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
        draftNote.textContent = save ? t('Prompt not saved · retry', '描述没能保存 · 重试') : t('Last prompt not loaded · retry', '上次的描述没能读取 · 重试');
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
    cleanup.push(() => { castLibrary?.destroy?.(); castLibrary = null; window.removeEventListener('freevideo-media', castChanged); prompt.removeEventListener('input', announceCast); castOverlay.dispose(); delete node.freevideoContinues; });
    openCastLibrary({api, text: t, viewURL: view, uploadOne: castUpload, onChange: () => { castOverlay.refresh(); references.refresh(); }})
        .then(library => { if (disposed) library?.destroy?.(); else castLibrary = library; });
    let requestAssets = null;
    const enhancer = createPromptEnhancer({node, input: prompt, editor: promptEditor, api, t, starter, issueButton,
        prepare: text => expandCast(text, mediaRows(node), {continued: Boolean(extension.source)}).text,
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
    const promptChanged = e => { if (String(e.detail) === String(node.id)) { if (prompt.value !== shown(value(node, 'text'))) prompt.value = shown(value(node, 'text')); prompt.disabled = linked(node, 'text'); mention.disabled = prompt.disabled; placeholder(); castOverlay.refresh(); references.refresh(); announceCast(); enhancer.sync(); } };
    window.addEventListener('freevideo-reference-prompt', promptChanged);
    cleanup.push(() => window.removeEventListener('freevideo-reference-prompt', promptChanged));
    const promptSection = section(t('Describe your scene', '描述画面'));
    const promptHelp = el('a', 'Skill ↗', 'fv-prompt-help');
    promptHelp.href = 'https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md';
    promptHelp.target = '_blank'; promptHelp.rel = 'noopener noreferrer';
    promptHelp.title = t('Prompt guide', '提示词指南');
    promptSection.querySelector('.fv-label').append(promptHelp);
    promptSection.append(enhancer.element);
    const canvas = section(t('Frame & duration', '画幅与时长'));
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
    function syncResolutionLabel() { resolutionValue.textContent = `${Number(mp.value).toFixed(2)} MP`; }
    syncResolutionLabel(); mp.addEventListener('input', syncResolutionLabel);
    resolutionSelect.append(mp, resolutionValue, icon('chevron'));
    const legacyOption = [...mp.options].find(o => Number(o.value) === 1);
    mp.disabled = dimensionsLinked;
    const seconds = el('input'); seconds.type = 'number'; seconds.required = true; seconds.min = '1.625'; seconds.step = 'any'; seconds.value = savedSeconds(node, value(node, 'seconds')); seconds.disabled = linked(node, 'seconds'); seconds.setAttribute('aria-label', t('Duration in seconds', '时长（秒）')); seconds.dataset.fvGuide = 'seconds';
    const audioLock = lockMark(), durationField = field(t('Duration · seconds', '时长 · 秒'), seconds); durationField.firstChild.append(audioLock);
    const fields = el('div', null, 'fv-fields'); fields.append(field(t('Resolution', '清晰度'), resolutionSelect), durationField); canvas.append(fields);
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
    const passMode = el('div', null, 'fv-pass-mode'); passMode.setAttribute('role', 'radiogroup');
    passMode.setAttribute('aria-label', t('Two-pass sampling', '二次采样'));
    const passOptions = [['off', t('Off', '关闭')], ['on', t('On', '开启')], ['preview', t('Preview first', '先出预览')]].map(([mode, label]) => {
        const option = el('button', label); option.type = 'button'; option.dataset.mode = mode; option.setAttribute('role', 'radio');
        option.onclick = () => choosePassMode(mode);
        return option;
    });
    passOptions[2].title = t('Stops at a half-resolution preview. Choose "Run second pass" in the result when it looks right; the video matches a direct run.',
        '先出半分辨率预览，满意后在结果里点“继续二采”，成片与直接生成一致。');
    const previewPassHint = passOptions[2].title;
    passMode.append(...passOptions);
    cleanup.push(listenDialogKey(passMode, 'keydown', event => {
        const step = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[event.key];
        if (!step) return;
        event.preventDefault();
        const enabled = passOptions.filter(option => !option.disabled && !option.hidden);
        const current = enabled.findIndex(option => option.getAttribute('aria-checked') === 'true');
        const next = enabled[(current + step + enabled.length) % enabled.length];
        if (next) { next.click(); next.focus(); }
    }));
    twoPassLabel.append(el('span', t('Two-pass', '二次采样')), twoPassStatus, passMode);
    let effortEstimate = {}, effortEstimateKey = '', effortEstimateTimer = null, effortEstimateRequest = null, lengthControl = null, chainController = null;
    // A level's step cache; custom steps and workflows without the input sample every step.
    const stepCacheNow = () => stepCacheOf(value(node, 'step_cache'));
    const clearStepCache = () => { if (stepCacheNow()) set(node, 'step_cache', 'none'); };
    // The two-pass learned upscale factor; a single pass has none (2).
    const upscaleNow = () => upscaleOf(value(node, 'upscale_factor'));
    const setUpscale = factor => { if (widget(node, 'upscale_factor') && value(node, 'upscale_factor') !== String(factor)) set(node, 'upscale_factor', String(factor)); };
    const effort = createSamplingEffort(t, {onChange: tier => {
        baseSteps.value = tier.steps;
        refineSteps.value = 3;
        twoPass.checked = tier.twoPass;
        autoSinglePass = false;
        set(node, 'two_pass', twoPass.checked);
        set(node, 'refine_steps', 3);
        set(node, 'step_cache', tier.stepCache || 'none');
        set(node, 'upscale_factor', String(tier.upscale));
        syncSamplingSteps();
    }, onPreview: tier => tier ? showEffortEstimate(tier.steps, tier.twoPass, tier.stepCache, tier.upscale) : showEffortEstimate()});
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
    function syncPassMode() {
        const mode = !twoPass.checked ? 'off' : previewFirst.checked ? 'preview' : 'on';
        const passLinked = linked(node, 'two_pass'), previewLinked = linked(node, 'preview');
        for (const option of passOptions) {
            const selected = option.dataset.mode === mode;
            option.setAttribute('aria-checked', String(selected)); option.tabIndex = selected ? 0 : -1;
            option.title = !(passLinked || (option.dataset.mode === 'preview' && previewLinked)) && option.dataset.mode === 'preview' ? previewPassHint : '';
        }
        passOptions[0].disabled = Boolean(extension.source) || passLinked || !widget(node, 'two_pass');
        passOptions[1].disabled = twoPass.disabled;
        passOptions[2].disabled = Boolean(extension.source) || twoPass.disabled || previewLinked || !widget(node, 'preview');
        passOptions[2].hidden = !widget(node, 'preview');
    }
    samplingSettings.append(effort.element, twoPassLabel);
    canvas.append(samplingSettings); cleanup.push(() => effort.dispose());
    cleanup.push(() => { clearTimeout(effortEstimateTimer); effortEstimateRequest?.abort(); });
    const mediaNode = upstream(node, 'media');
    const mediaFilled = () => swapState(mediaNode) ? Boolean(swapState(mediaNode).source || swapState(mediaNode).person) : JSON.parse(value(mediaNode, 'assets') || '[]').some(r => r.enabled !== false)
        || Boolean(mediaNode.inputs?.some(input => ['first', 'last', ...referenceInputs, 'reference_audio'].includes(input.name) && input.link != null));
    // The line beside the closed title says what the section is for, until something is added.
    // The guide opens the section it points at; the ring stays on the title row.
    const mediaGuideTarget = () => {
        if (!mediaNode?.freevideoCreateMediaEditor) return null;
        const r = mediaTitle.getBoundingClientRect();
        return {node: mediaTitle, rect: {left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height},
            reveal: () => { mediaDetails.open = true; }};
    };
    const paintMediaHint = () => { try { mediaHint.hidden = mediaDetails.open || !mediaNode?.freevideoCreateMediaEditor || mediaFilled(); } catch { mediaHint.hidden = true; } };
    const [mediaDetails, mediaMount] = expand(t('Media', '素材'));
    const mediaTitle = mediaDetails.querySelector('summary'), mediaHint = el('span', t('Reference images, first and last frames', '参考图、首帧、尾帧'), 'fv-section-hint');
    mediaTitle.dataset.fvGuide = 'media'; mediaTitle.append(mediaHint);
    const extendMount = el('div', null, 'fv-extend-source'); extendMount.hidden = true; mediaMount.prepend(extendMount);
    let releaseExtendThumbnail = () => {};
    cleanup.push(() => releaseExtendThumbnail());
    if (mediaNode?.freevideoCreateMediaEditor) { cleanup.push(mediaNode.freevideoCreateMediaEditor(mediaMount)); mediaDetails.open = mediaFilled(); }
    else mediaMount.append(el('p', t('Connect a FreeVideo Media panel in node view to upload here.', '在节点视图连接 FreeVideo 素材面板，可在这里上传。'), 'fv-legacy'));
    paintMediaHint(); mediaDetails.addEventListener('toggle', paintMediaHint);
    mediaTitle.addEventListener('click', () => {
        queueMicrotask(() => { if (mediaDetails.open && mediaDetails.dataset.expanded !== 'false') { firstStart?.dismiss(); triggerFeatureTour('media'); } });
    });
    controls.addEventListener('change', paintMediaHint, true); controls.addEventListener('input', paintMediaHint, true);
    const connectedLora = upstream(node, 'loras');
    const loraNode = connectedLora?.properties?.[CREATED_BY_PANEL] ? pendingLoras(node, connectedLora) : connectedLora || pendingLoras(node);
    // Replace person: its LoRA row is found among the registered names (it may sit in a subfolder), a row the user had
    // switched off is switched on for the run, and everything done is undone when the option goes off.
    // Returns '' when all is well, else the line to show; runs one at a time.
    let swapLoraBusy = Promise.resolve('');
    const swapLoraText = reason => reason === 'missing'
        ? t(`"Replace person" needs the ${SWAP_LORA} LoRA in the models/loras folder.`, `“替换人物”需要把 ${SWAP_LORA} 放进 models/loras 文件夹。`)
        : reason === 'ambiguous' ? t(`More than one ${SWAP_LORA} was found in models/loras. Add the one to use under "LoRA".`, `models/loras 中有多个 ${SWAP_LORA}，请在“LoRA”一栏添加要使用的那个。`)
        : t('Could not read the LoRA list. Try again.', '无法读取 LoRA 列表，请重试。');
    async function applySwapLora() {
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
        if (!result.ok) return swapLoraText(result.reason);
        state.lora = result.record;
        if (result.adapters !== now) { set(loraNode, 'adapters', result.adapters); window.dispatchEvent(new CustomEvent('freevideo-loras', {detail: loraNode.id})); }
        return '';
    }
    const ensureSwapLora = () => (swapLoraBusy = swapLoraBusy.then(applySwapLora, applySwapLora));
    let swapPanel = null;
    function swapChanged(info) {
        mediaHint.textContent = swapState(mediaNode) ? t('Source video, person image', '源视频、人物图') : t('Reference images, first and last frames', '参考图、首帧、尾帧');
        // Switching the option only changes which media the prompt reads: its pictures are re-bound, never renumbered.
        syncReferencePrompt(node, t, info?.toggled === true); paintMediaHint();
        ensureSwapLora().then(problem => { if (problem) swapPanel?.notify(problem); });
        window.dispatchEvent(new CustomEvent('freevideo-media', {detail: mediaNode.id}));
    }
    if (mediaNode?.freevideoCreateMediaEditor) {
        const swap = swapPanel = createReplacePerson({t, api, media: mediaNode, mount: mediaMount, view, onChange: swapChanged});
        cleanup.push(() => swap.dispose());
        if (swapState(mediaNode)) { mediaHint.textContent = t('Source video, person image', '源视频、人物图'); mediaDetails.open = true; }
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
        loraMount.append(field(t('LoRA', 'LoRA'), select), field(t('Strength', '强度'), strength), strengthMark, el('p', loraWarning(), 'fv-lora-note'));
    } else loraMount.append(el('p', loraWarning(), 'fv-lora-note'));
    const [, advanced] = expand(t('Advanced', '高级选项'));
    const custom = el('div', null, 'fv-fields');
    const width = el('input'), height = el('input');
    for (const [input, name, label] of [[width, 'width', t('Width', '宽度')], [height, 'height', t('Height', '高度')]]) {
        input.type = 'number'; input.required = true; input.min = '256'; input.max = '4096'; input.step = '32';
        input.value = value(node, name); input.disabled = dimensionsLinked; input.setAttribute('aria-label', label);
        custom.append(field(label, input));
        input.onchange = () => {
            if (!input.value || !input.reportValidity()) return;
            set(node, name, Number(input.value)); selected = 'custom'; node.properties.freevideo_aspect = selected;
            ratio = Number(value(node, 'width')) / Number(value(node, 'height'));
            updateCanvas();
        };
    }
    advanced.append(custom);
    lengthControl = createStudioLength({api, node, seconds, advanced, fields, t, field, promptModel: enhancer.model,
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
    firstFrame.input.onchange = () => { set(node, 'first_frame_reference', firstFrame.input.checked); firstFrame.input.checked = value(node, 'first_frame_reference') === true; };
    let manyParts = false, originalOn = false;
    function updateFirstFrame() {
        // While a new plan is being worked out the row keeps its place.
        const plan = lengthControl.plan; if (plan || !lengthControl.needsPlan) manyParts = plan?.count > 1;
        firstFrame.row.hidden = !widget(node, 'first_frame_reference') || !(manyParts || extension.source);
        // Audio used as is fixes the picture to the sound, so this switch is off while it applies.
        firstFrame.input.checked = !originalOn && value(node, 'first_frame_reference') === true;
        firstFrame.setDisabled(Boolean(originalOn || capturing || queueState?.submitting || queueState?.running || chainController?.active));
    }
    const samplingFields = el('div', null, 'fv-fields'); samplingFields.style.marginTop = '14px';
    const baseSteps = el('input'), refineSteps = el('input');
    const samplingWarning = el('p', t('Changing steps may lower quality. Default: 8 + 3 steps.', '改步数可能降低质量。默认 8 + 3 步。'), 'fv-muted');
    samplingWarning.id = `fv-sampling-warning-${node.id}`;
    samplingWarning.style.color = 'var(--fv-warning)';
    for (const [input, name, label, fallback, maximum] of [
        [baseSteps, 'base_steps', t('First-pass steps', '一采步数'), 8, 32],
        [refineSteps, 'refine_steps', t('Second-pass steps', '二采步数'), 3, 31],
    ]) {
        input.type = 'number'; input.min = '1'; input.max = String(maximum); input.step = '1'; input.required = true;
        input.value = value(node, name) ?? fallback;
        input.setAttribute('aria-label', label); input.setAttribute('aria-describedby', samplingWarning.id);
        input.oninput = () => syncSamplingSteps();
        input.onchange = () => input.reportValidity();
        samplingFields.append(field(label, input));
    }
    // Typed steps are custom sampling, which has no step cache.
    baseSteps.oninput = () => { clearStepCache(); syncSamplingSteps(); };
    // Apply the two-pass rule to committed values, never while typing.
    baseSteps.onchange = () => {
        // Before validation: one step is valid only once two-pass is off.
        const steps = Number(baseSteps.value);
        if (baseSteps.value !== '' && Number.isInteger(steps) && steps >= 1 && steps <= 32
                && !linked(node, 'two_pass') && widget(node, 'two_pass')) {
            if (steps !== 8 && twoPass.checked) { twoPass.checked = false; set(node, 'two_pass', false); autoSinglePass = true; }
            else if (steps === 8 && autoSinglePass && !extension.source) { twoPass.checked = true; set(node, 'two_pass', true); autoSinglePass = false; }
        }
        syncSamplingSteps();
        baseSteps.reportValidity();
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
        twoPassStatus.textContent = Number(baseSteps.value) !== 8 && !onePassLevel ? t('Needs an 8-step first pass', '一采需 8 步') : '';
        twoPassLabel.classList.toggle('fv-two-pass-unavailable', unavailable);
        // A preview stops after the first pass, so it needs two-pass sampling.
        if (!twoPass.checked && previewFirst.checked) {
            previewFirst.checked = false; set(node, 'preview', false);
            if (runButtonReady) updateRunButton();
        }
        previewFirst.disabled = !twoPass.checked || linked(node, 'preview') || !widget(node, 'preview');
        blockOriginal('preview', previewFirst.checked && !previewFirst.disabled);
        syncPassMode();
        baseSteps.disabled = baseLinked || !widget(node, 'base_steps') || Boolean(extension.source);
        refineSteps.disabled = !twoPass.checked || linked(node, 'refine_steps') || !widget(node, 'refine_steps') || Boolean(extension.source);
        baseSteps.min = twoPass.checked ? '2' : '1';
        refineSteps.max = baseLinked ? '31' : String(Math.max(3, Math.min(31, Number(baseSteps.value || 8) - 1)));
        refineSteps.setCustomValidity(twoPass.checked && !baseLinked && !refineSteps.disabled && Number(refineSteps.value) !== 3 && Number(refineSteps.value) >= Number(baseSteps.value)
            ? t('Use 3 second-pass steps, or fewer steps than in the first pass.', '二采步数须为 3，或少于一采步数。') : '');
        if (twoPass.checked) refineSteps.removeAttribute('title');
        else refineSteps.title = t('Turn on two-pass acceleration to use this setting.', '开启二次采样加速后生效。');
        for (const [input, name] of [[baseSteps, 'base_steps'], [refineSteps, 'refine_steps']]) {
            if (!input.disabled && input.checkValidity() && Number(input.value) !== value(node, name)) {
                set(node, name, Number(input.value));
            }
        }
        effort.update({baseSteps: Number(baseSteps.value), refineSteps: Number(refineSteps.value),
            twoPass: twoPass.checked, stepCache: stepCacheNow(), upscale: upscaleNow(),
            disabled: baseSteps.disabled || linked(node, 'two_pass') || linked(node, 'refine_steps') || linked(node, 'step_cache') || linked(node, 'upscale_factor'),
            reason: extension.source ? continuationReason() : ''});
        refreshEffortEstimate();
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
                const audio = refs.some(r => r.kind === 'audio'), visual = refs.some(r => r.kind !== 'audio');
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
    seed.onchange = () => { if (seed.value && seed.reportValidity()) set(node, 'seed', Number(seed.value)); };
    const mode = el('select'); for (const [v, label] of [['randomize', t('New each run', '每次随机')], ['fixed', t('Keep fixed', '保持固定')], ['increment', t('Increment', '递增')], ['decrement', t('Decrement', '递减')]]) { const o = el('option', label); o.value = v; mode.append(o); }
    mode.value = value(node, 'control_after_generate') || 'fixed'; mode.onchange = () => set(node, 'control_after_generate', mode.value);
    seedFields.append(field(t('Seed', '种子'), seed), field(t('After generation', '生成后'), mode)); advanced.append(seedFields);
    const previewHead = el('div', null, 'fv-preview-head'); previewHead.append(el('strong', t('Preview', '预览')));
    const previewTools = el('div', null, 'fv-preview-tools');
    previewTools.append(button(t('Creations', '作品'), () => {
        stage.querySelector('video')?.pause(); openLibrary(t, {upscale: value => node.freevideoUpscaleResult(value),
            extend: (saved, options) => openExtendEditor(saved, node, {studio: dialog, ...options})});
    })); previewHead.append(previewTools); output.append(previewHead);
    const previewSpace = el('div', null, 'fv-preview-space'); output.append(previewSpace);
    // The preview keeps the size it has once the list is in; an opened part grows the column, which scrolls.
    const partList = createChainPartList(t, (block, text) => chainController.setText(block, composePartText(chainController.state, block, text)), {
        onShow: () => { previewSpace.style.flex = `0 0 ${previewSpace.getBoundingClientRect().height}px`; },
        onHide: () => { previewSpace.style.flex = ''; },
        redo: block => { failure.clear(); return chainController.redo(block); },
        resume: () => { failure.clear(); return chainController.resume(); },
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
    const stage = el('div', null, 'fv-stage');
    stage.setAttribute('aria-label', t('Video preview', '视频预览'));
    const stageMedia = el('div', null, 'fv-stage-media');
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
    // The queued count sits after the part number; a line too narrow for both keeps the part number only.
    const setChainLine = text => {
        const main = el('span', text), more = chainController?.waiting;
        chainLine.replaceChildren(main, ...(more ? [el('span', t(` · ${more} more queued`, ` · 另有 ${more} 条排队`), 'fv-chain-queued')] : []));
    };
    progress.updateReport({report_id: node.freevideoReportId});
    cleanup.push(() => progress.dispose());
    cleanup.push(createPreviewScene(stage, progress.element, stageMedia));
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
    }
    cleanup.push(hideProgress);
    let previewRatio = Number(initialWidth) / Number(initialHeight);
    // The space takes no more height than the picture can use at full width.
    const ratioStyle = () => { if (Number.isFinite(previewRatio) && previewRatio > 0) previewSpace.style.setProperty('--fv-ratio', String(previewRatio)); };
    ratioStyle();
    function fitPreview() {
        const {width, height} = previewSpace.getBoundingClientRect();
        if (!width || !height || !Number.isFinite(previewRatio) || previewRatio <= 0) return;
        const fittedWidth = Math.min(width, height * previewRatio);
        stage.style.width = `${fittedWidth}px`; stage.style.height = `${fittedWidth / previewRatio}px`;
        stage.dataset.short = String(fittedWidth / previewRatio < 150);
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
    previewObserver.observe(previewSpace); cleanup.push(() => previewObserver.disconnect());
    function showProgress(message) {
        phoneAccess.observe(message);
        watchedRun = true; reportTipFor = null; clearTimeout(reportTipTimer);
        reuseRow.hidden = true;
        stats.hidden = true;
        budget.textContent = '';
        links.replaceChildren();
        delete progress.report.dataset.covered;
        prewarm.textContent = '';
        clearTimeout(revealTimer); revealTimer = null; delete stage.dataset.revealing;
        if (progress.element.hidden) stage.querySelector('video')?.pause();
        progress.update(message);
    }
    const status = el('div', '', 'fv-status'); status.setAttribute('role', 'status');
    // A fault in the status line carries the report button; the next text written to the line removes it.
    // The run's diagnostic report: errors beside it go the report route instead of the feedback form.
    const reportRoute = {has: () => progress.hasReport(), download: () => progress.downloadReport(), fetch: progress.fetchReport, version: productVersion};
    const statusIssue = issueButton(t, () => ({where: ['Generate', '生成'], message: status.firstChild?.textContent}), {className: 'fv-status-issue', route: reportRoute});
    function fault(text) { status.dataset.error = 'true'; status.textContent = text; status.append(statusIssue); }
    const stats = el('div', null, 'fv-stats'), budget = el('div', '', 'fv-budget'), links = el('div', null, 'fv-result-links');
    // New text in the status line replaces the report card under the result.
    const statusWatch = new MutationObserver(() => { if (!status.textContent.trim()) return; hideReportTip(links.querySelector('.fv-actions')); hideReportTip(status); });
    statusWatch.observe(status, {childList: true, characterData: true, subtree: true});
    cleanup.push(() => statusWatch.disconnect());
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
    output.append(status, reuseRow, trimNote, memoryNote, thermalNote, sharedGpuNote, keepSoundNote, encoderNote, links, stats, budget, progress.stall, progress.report, prewarm);
    const failure = createErrorPanel(t, reportRoute); output.append(failure.element);
    // With the picture, the result's actions and numbers form one block, centred below the header.
    output.append(el('div', null, 'fv-preview-end'));
    if (node.freevideoFailure) failure.show(node.freevideoFailureReport || node.freevideoFailure, false);
    let result = node.freevideoLastResult || app.nodeOutputs?.[node.id]?.freevideo_summary?.[0];
    const recent = createRecentVideos(t, {body, onSelect: row => showResult(row, {chosen: true})});
    body.append(recent.element); previewTools.insertBefore(recent.toggle, previewTools.lastElementChild);
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
            return {text: prompt.disabled ? saved.text : prompt.value, width: sizeLocked ? saved.width : Number(value(node, 'width')),
                height: sizeLocked ? saved.height : Number(value(node, 'height')),
                frames: lengthLocked ? saved.frames : framesOf(seconds.value), seconds: Number(seconds.value)};
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
            } finally { reuseApplying = false; }
        },
        onEdit: listener => {
            const heard = () => { if (!reuseApplying) listener(); };
            controls.addEventListener('input', heard, true); controls.addEventListener('change', heard, true);
            return () => { controls.removeEventListener('input', heard, true); controls.removeEventListener('change', heard, true); };
        },
    };
    let reportTipTimer = null, watchedRun = false, reportTipFor = null, shownReport = null;
    // chosen: a video picked from Recent videos. It only changes the picture; a run in progress keeps its progress.
    function showResult(r, {chosen = false} = {}) {
        if (!r?.video || disposed) return; result = r;
        recent.setSelected(r.video); syncPartList();
        refreshEffortEstimate(true);
        // The report belongs to the video that run made; a video picked from Recent videos
        // shows its own report, or none when this window never saw its run.
        const reports = node.freevideoVideoReports || (node.freevideoVideoReports = new Map());
        if (!chosen && node.freevideoReportId) reports.set(r.video, node.freevideoReportId);
        const wanted = reports.get(r.video) || null;
        if (!(chosen && busy) && wanted !== shownReport) {
            shownReport = wanted; progress.updateReport({new_request: true});
            if (wanted) progress.updateReport({report_id: wanted});
        }
        if (wanted && !(chosen && busy)) progress.pinReport();
        stage.querySelector('video')?.pause(); stageMedia.replaceChildren();
        const video = el('video'); video.src = view(r.video, 'output') + (r.version ? `&fv=${encodeURIComponent(r.version)}` : ''); video.controls = true; video.preload = 'metadata'; video.playsInline = true; stageMedia.append(video);
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
                                                   secondPass: r.preview ? secondPass(r) : null, reuse: r.preview ? null : reuse}));
        if (runButtonReady) updateExtendButton();
        // After a video finished just now, point at the report menu until the
        // user closes the tip once, whichever version they first saw it in.
        if (!chosen && watchedRun && reported && !r.preview && !r.result_cache_hit && !reportTipClosed()) reportTipFor = r.video;
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
            setChainLine(joining ? t('Joining the parts.', '正在接合各段。')
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
            setChainLine(outcome?.joining ? t('Joining the parts.', '正在接合各段。')
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
    const runrow = el('div', null, 'fv-runrow'); cleanup.push(dismissEmptyPrompt);
    const runOptions = el('details', null, 'fv-run-options');
    const optionsToggle = el('summary'); optionsToggle.title = t('Generation mode', '生成方式');
    optionsToggle.setAttribute('aria-label', optionsToggle.title);
    optionsToggle.append(el('span', null, 'fv-chevron'));
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
    const modeHelp = el('p', '', 'fv-muted'); optionsPanel.append(modeHelp);
    count.oninput = () => {
        if (count.value && count.checkValidity()) {
            node.properties.freevideo_batch_count = Number(count.value); rememberPromptDraft(api, node);
        }
        updateRunButton();
    };
    runOptions.append(optionsToggle, optionsPanel);
    const generate = button('', submitDraft, 'fv-primary');
    generate.dataset.fvGuide = 'generate';
    runrow.append(generate, runOptions);
    runButtonReady = true;
    const queueActions = el('div', null, 'fv-queue-actions');
    const queueDetails = el('details', null, 'fv-queue-details');
    const queueToggle = el('summary'), queueList = el('div', null, 'fv-queue-list');
    queueDetails.append(queueToggle, queueList);
    const cancelCurrent = button(t('Cancel current', '取消当前'), cancelGeneration, 'fv-quiet');
    const resumeChain = button(t('Continue generating', '继续生成'), async () => {
        resumeChain.disabled = true; failure.clear();
        try { await chainController.resume(); } catch (error) { showQueueError(error); }
        finally { resumeChain.disabled = false; }
    }, 'fv-quiet'); resumeChain.hidden = true;
    const stopLoop = button(t('Stop after this video', '完成当前后停止'), async () => {
        stopLoop.disabled = true;
        try { await queueController.stopLoop(); }
        catch (error) { showQueueError(error); }
        finally { stopLoop.disabled = false; }
    }, 'fv-quiet');
    queueActions.append(queueDetails, cancelCurrent, stopLoop, resumeChain);
    syncResume = () => {
        resumeChain.hidden = !['cancelled', 'failed', 'interrupted'].includes(chainController?.state?.state) || partList.resumes();
        queueActions.hidden = !chainController?.active && !busy && !queueState?.looping && resumeChain.hidden;
    };
    runbar.append(requestAssets.element, runrow, queueActions); controls.append(runbar);
    const queueError = error => ({
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
    }[error?.message] || (error?.said ? t(error.said[0], error.said[1]) : null) || (isNetworkTypeError(error) ? connectionLostText(t) : error?.message) || String(error));
    let lastQueueError = null, connectionNotice = false;
    function showQueueError(error) {
        if (disposed) return;
        status.dataset.error = 'true';
        // Polling and loop shutdown must not replace the original exception
        // with a generic "loop stopped" notice or reopen a dismissed dialog.
        if (['loop_stopped', 'queue_unavailable'].includes(error?.message)
            && node.freevideoFailureReport && node.freevideoFailureReport.stage !== 'queue') return;
        status.textContent = error?.schema === 'freevideo.error-report'
            ? t('Could not start. See the details below.', '没能开始生成，原因见下方。')
            : extensionCause(error) ? t('A browser extension is interfering with FreeVideo. See details below.', '浏览器扩展干扰了 FreeVideo，详见下方。')
            : queueError(error);
        // Recoverable chain actions need one visible sentence, not a second error card.
        if (error?.action) { status.scrollIntoView({block: 'nearest'}); return; }
        // A loop stopped by a long outage is a new state of the same notice: show its final details once.
        const identity = error?.schema === 'freevideo.error-report' ? error : error?.loopStopped ? `${error.message} (loop stopped)` : error?.message || error;
        if (lastQueueError === identity) return;
        lastQueueError = identity;
        const report = failure.show(error, error?.schema === 'freevideo.error-report', {stage: 'queue', node, graph: app.graph});
        node.freevideoFailureReport = report; node.freevideoFailure = errorText(report);
        connectionNotice = isNetworkTypeError(error) && !report.browser_extension;
    }
    // The queue read works again: take the lost-connection notice down. A failure card for anything else stays.
    function clearConnectionNotice() {
        if (!connectionNotice) return;
        connectionNotice = false;
        if (node.freevideoFailureReport?.stage !== 'queue') return;
        lastQueueError = null; failure.clear(); node.freevideoFailure = ''; node.freevideoFailureReport = null;
        status.dataset.error = 'false'; status.textContent = '';
    }
    // A part that stopped is one sentence on the status line; what ComfyUI said goes in the card, as a generation failure.
    function showPartError(error) {
        if (disposed) return;
        status.dataset.error = 'true';
        status.textContent = error.joining ? t('Could not join the parts. See the details below.', '未能接合各段，原因见下方。')
            : t(`Part ${error.index + 1} could not be generated. See the details below.`, `第 ${error.index + 1} 段未能生成，原因见下方。`);
        if (lastQueueError === error.message) return;
        lastQueueError = error.message;
        const report = failure.show(error.message, false,
            {stage: 'execution', node, graph: app.graph, ...(error.joining ? {progress: {phase: 'save'}} : {})});
        node.freevideoFailureReport = report; node.freevideoFailure = errorText(report);
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
            ? t('Joining parts…', '正在拼接…')
            : chain?.state === 'queued' ? (chain.waiting ? waitingText(chain.waiting, t) : t('Queued…', '排队中…'))
            : t(`Generating part ${position.index + 1}…`, `正在生成第 ${position.index + 1} 段…`);
        generate.disabled = submitting || chainWait || lengthControl.blocked || (runMode === 'loop' && queueState?.looping);
        generate.textContent = submitting ? t('Adding…', '正在添加…')
            : chainReason || (runMode === 'batch' ? t(`Add ${count.value || '…'} videos`, `添加 ${count.value || '…'} 个视频`)
            : runMode === 'loop' ? (queueState?.looping ? t('Looping', '循环中') : t('Start loop', '开始循环'))
            : busy ? t('Add to queue', '加入队列')
            : previewFirst.checked && !previewFirst.disabled ? t('Generate preview', '生成预览') : t('Generate video', '生成视频'));
        generate.title = (lengthControl.blocked ? lengthControl.message : submitting ? t('Adding…', '正在添加…') : '');
        for (const b of modeButtons) b.setAttribute('aria-pressed', String(b.dataset.mode === runMode));
        for (const b of modeButtons) if (b.dataset.mode !== 'single') b.disabled = multiple;
        countField.hidden = runMode !== 'batch'; count.disabled = runMode !== 'batch';
        modeHelp.hidden = runMode === 'single' && !multiple;
        modeHelp.textContent = multiple ? t('Videos made in parts cannot use batch or loop.', '分段视频不能批量或循环生成。') : runMode === 'loop'
            ? t('Same settings, a new seed each time. Stops when this page closes.', '用当前设置，每次换一个种子；关闭网页后停止。')
            : t('Same settings, a new seed for each video.', '用当前设置，每个视频换一个种子。');
        // With one video per GPU each running row carries its own Cancel; "current" would be ambiguous.
        cancelCurrent.hidden = !chainController?.active && (!queueState?.running || Boolean(queueState?.running_all));
        cancelCurrent.disabled = cancelling;
        cancelCurrent.textContent = cancelling ? t('Cancelling…', '正在取消…') : t('Cancel current', '取消当前');
        stopLoop.hidden = !queueState?.looping;
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
        item.insertBefore(chip, item.querySelector('button'));
        queueList.scrollTop = 0;
        clearTimeout(movedTimer); movedTimer = setTimeout(clearMoved, 2800);
    }
    function renderQueue(state) {
        if (disposed) return;
        queueState = state;
        if (chainController?.active && !state.running_all) { queueActions.hidden = false; updateRunButton(); return; }
        const active = state.running || state.pending[0];
        activePrompt = active?.id || null;
        const wasBusy = busy;
        busy = Boolean(active) || progressLive(); queued = !state.running && Boolean(state.pending.length);
        // A finished, failed or cancelled video may have downloaded what the line above Generate still lists.
        if (wasBusy && !busy) { requestAssets?.refresh(true); paintMediaHint(); }
        const g = active?.inputs;
        // The right-hand frame belongs to the submitted video. Draft edits on
        // the left must never reshape a video currently being generated.
        if (Number.isFinite(g?.width) && Number.isFinite(g?.height)) previewSize(g.width, g.height);
        const queueRows = state.running_all ? [...state.running_all, ...state.pending] : state.pending;
        const running = state.running_all?.length || 0, waiting = state.pending.length;
        queueToggle.textContent = !state.running_all ? t(`Queued · ${waiting}`, `排队 ${waiting} 个`)
            : [running && t(`${running} generating`, `生成中 ${running} 个`), waiting && t(`${waiting} queued`, `排队 ${waiting} 个`)]
                .filter(Boolean).join(' · ');
        queueDetails.hidden = queueRows.length === 0;
        queueActions.hidden = !busy && !state.looping && resumeChain.hidden;
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
                if (summary) { const line = el('span', summary, 'fv-queue-summary'); line.title = summary; description.append(line); }
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
                if (!executing && index > 0) {
                    const first = button(t('Move to front', '排到最前'), async () => {
                        first.disabled = remove.disabled = true;
                        movedId = row.id;
                        try { await queueController.moveFirst(row.id); showMoved(); }
                        catch (error) { movedId = null; showQueueError(error); first.disabled = remove.disabled = false; }
                    }, 'fv-quiet');
                    first.setAttribute('aria-label', t(`Move queued video ${index + 1} to front`, `把排队的第 ${index + 1} 个排到最前`));
                    item.append(first);
                }
                item.append(remove); queueList.append(item);
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
        const emptyAllowed = options?.allowEmpty === true;
        if (capturing || queueState?.submitting) return;
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
            invalid.focus(); invalid.reportValidity(); return;
        }
        if (!emptyAllowed && !extension.source && !prompt.disabled && !prompt.value.trim() && !untouched() && !swapState(mediaNode)) {
            showEmptyPrompt({t, anchor: runrow, field: prompt, onContinue: () => submitDraft({allowEmpty: true})}); return;
        }
        if (swapState(mediaNode)) {
            const missing = swapMissing(swapState(mediaNode));
            if (missing) { status.dataset.error = 'true'; status.textContent = swapMissingText(missing, t); return; }
        }
        const chosenMode = runMode, batchCount = chosenMode === 'batch' ? Number(count.value) : 1;
        const originalSeed = value(node, 'seed'), seedMode = mode.value;
        // A request that is not accepted leaves the node mode as the user had it; a retry turns it on again.
        // Once the server has taken a request it stays taken; only a request it never took is undone.
        let held = null, accepted = false;
        const onAccepted = () => { accepted = true; };
        try {
            stopNote = ''; lastQueueError = null; failure.clear(); node.freevideoFailure = ''; node.freevideoFailureReport = null;
            capturing = true; updateRunButton(); status.dataset.error = 'false'; runOptions.open = false;
            lengthControl.freeze();
            if ((!extension.source || prompt.value.trim()) && !swapState(mediaNode) && await enhancer.beforeGenerate() === false) return;
            if (disposed) return;
            if (selected === 'input' && !dimensionsLinked) await inputRatio(true);
            const lengthPlan = await lengthControl.ready();
            if (disposed || lengthControl.blocked) return;
            if (lengthControl.absent && !seconds.reportValidity()) return;
            if (lengthPlan?.count > 1 && chosenMode !== 'single') { status.textContent = t('Videos made in parts cannot use batch or loop.', '分段视频不能批量或循环生成。'); return; }
            const lengthOptions = lengthControl.options();
            const source = extension.source;
            const sourceState = source ? await extendSourceState(api, source) : 'ok';
            if (sourceState !== 'ok') throw new Error(extendSourceProblem(t, sourceState));
            if (source && lengthPlan?.count > 1) lengthOptions.after = {work: source.video};
            const swapProblem = await ensureSwapLora();
            if (swapProblem) { status.dataset.error = 'true'; status.textContent = swapProblem; return; }
            // Only serialization briefly locks the editor. Network submission
            // and all GPU execution leave the next draft fully editable.
            for (const section of controls.querySelectorAll('.fv-section')) section.inert = true;
            held = turnNodeOn(node);
            const graph = cleanOptionalPrompt(clonePrompt(await app.graphToPrompt()));
            if (emptyAllowed && !prompt.value.trim() && !extension.source) allowEmptyPrompt(graph, node.id);
            const snapshot = singleRunSnapshot(extendSnapshot(graph, node.id, source,
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
                if (!chain) { if (source) throw new Error(extendText(t, 'unavailable')); lengthControl.fallback(); if (!seconds.reportValidity()) return;
                    await queueController.start(snapshot, {count: batchCount, repeat: chosenMode === 'loop', seedIndex, onAccepted}); }
            } else await queueController.start(snapshot, {count: batchCount, repeat: chosenMode === 'loop', seedIndex, onAccepted});
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
        } catch (error) { showQueueError(createErrorReport(error, {stage: 'submission', node, graph: app.graph})); }
        finally {
            if (accepted) held?.done(); else held?.restore();
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
        for (const b of shapes.children) b.setAttribute('aria-pressed', String(b.dataset.ratio === selected));
        const shapeIndex = [...shapes.children].findIndex(b => b.dataset.ratio === selected);
        shapes.style.setProperty('--fv-shape-index', Math.max(0, shapeIndex));
        shapes.dataset.custom = String(shapeIndex < 0);
        if (!busy) previewSize(Number(w), Number(h));
        refreshEffortEstimate();
    }
    function syncCanvasSize() {
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
    for (const [id, label, r] of [...frameShapes(t), ['input', t('Match image', '跟随图片'), null]]) {
        const b = button('', async () => { const previous = selected; try { selected = id; pixels = Number(mp.value); if (id === 'input') await inputRatio(true); else { ratio = r; applySize(); } status.textContent = ''; status.dataset.error = 'false'; } catch (error) { selected = previous; updateCanvas(); status.dataset.error = 'true'; status.textContent = error.message; } }, 'fv-shape');
        b.dataset.ratio = id; b.setAttribute('aria-label', label); b.disabled = dimensionsLinked;
        b.append(el('span', null, 'fv-shape-icon'), el('span', label)); shapes.append(b);
    }
    mp.onchange = () => { try { pixels = Number(mp.value); applySize(); if (pixels !== 1) legacyOption?.remove(); } catch (error) { status.dataset.error = 'true'; status.textContent = error.message; } };
    // The note describes the request just sent; once the draft changes it could read as the new one.
    const addedNote = t('Added to the queue. It starts when the current video finishes.', '已加入队列，当前视频完成后自动开始。');
    const clearAddedNote = () => { if (status.textContent === addedNote) status.textContent = ''; };
    seconds.addEventListener('input', clearAddedNote); prompt.addEventListener('input', clearAddedNote);
    seconds.onchange = () => { if (lengthControl.commit({report: false})) updateCanvas(); };
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
    const mediaChanged = async e => { if (String(e.detail) !== String(mediaNode?.id)) return; syncOriginalAudio(); refreshEffortEstimate(true); if (selected !== 'input') return; try { await inputRatio(); } catch (error) { status.textContent = error.message; } };
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

    dialog.addEventListener('cancel', e => { e.preventDefault(); closeDialog(dialog); });
    dialog.onclose = () => { disposed = true; blockOriginal('extend', false); blockOriginal('preview', false); for (const f of cleanup) f(); stage.querySelector('video')?.pause(); dialog.remove(); if (current?.dialog === dialog) { current = null; viewChanged('nodes', node); } };
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
        extendMount.hidden = !source; mediaMount.classList.toggle('fv-extend-active', Boolean(source)); blockOriginal('extend', Boolean(source));
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
        prompt.value = shown(value(node, 'text')); placeholder(); enhancer.sync();
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
    document.body.append(dialog); dialog.showModal(); viewChanged('studio', node); updateCanvas();
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
    const tourContext = {dialog, api, cn, onError: () => fault(t('Could not complete. Try again.', '没有完成，请重试')), onTry: tryTourExample, canTry: canTryTour, isBusy: () => busy || queued || capturing};
    cleanup.push(registerTourWorkspace(tourContext));
    const tourTimer = setTimeout(async () => { const state = await firstStart.ready; if (!closed && !state?.firstRun && !tourContext.isBusy()) offerVersionTour(tourContext); }, 1400);
    cleanup.push(() => clearTimeout(tourTimer));
    const recentLoad = recent.refresh();
    if (node.freevideoProgress && !node.freevideoProgress.chain) showProgress(node.freevideoProgress);
    else if (result) showResult(result);
    // Do not change any saved width/height simply by opening a workflow.
    syncQueue().then(async () => {
        if (disposed) return;
        if (busy) { showProgress(node.freevideoProgress || {label: queued ? t('Waiting in queue', '正在排队') : t('Loading progress', '正在读取进度')}); }
        else if (!result) {
            const saved = (await recentLoad)[0];
            if (!disposed && !busy && !result && saved) showResult(saved);
        }
    }).catch(() => {});
    // Opening leaves focus on the panel itself, not on the main button with a ring as if already pressed; Tab still reaches it.
    dialog.tabIndex = -1;
    requestAnimationFrame(() => { if (dialog.open && !exampleFocused && !dialog.querySelector('.fv-guide-layer') && !document.querySelector('dialog.fv-editor[open]')) (extension.source ? prompt : dialog).focus({preventScroll: true}); });
    // Establish the baseline after UI initialization; opening a panel is not an edit.
    trackPromptDraft(node);
    return dialog;
}
