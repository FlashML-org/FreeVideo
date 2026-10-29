import { createErrorPanel, errorText, createErrorReport, adviceFor } from './error_panel.js';
import { installNativeErrorGuard } from './native_errors.js';
import { installTranslateGuard } from './translate_guard.js';
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { openEditorView } from './editor_loader.js';
import { beginWorkspaceRestore, finishWorkspaceRestore, waitForLauncherRestore } from './workspace_restore.js';
import { openStudio, loraPanel, loraWarning, promptGuide } from "./studio.js";
import { createGenerationProgress, referenceTrimText, thermalText, sharedGpuText, keepSoundText, encoderFileText, encoderFilePath } from './generation_progress.js';
import { createProgressConnection } from './progress_connection.js';
import { notifyCompatibility } from './compatibility.js';
import { startUpdateChecks } from './updates.js';
import { startWhiteboxWorker } from './whitebox_worker.js';
import { startDoneNotify, askDesktopPermission, DESKTOP_SETTING } from './done_notify.js';
import { installNavigation, refreshNavigation, preferredView, collapseWorkspaces } from './view_navigation.js';
import { isLaunchVisit } from './launch_visit.js';
import { openFreeVideoWorkflow } from './launcher.js';
import { agentInterfaceState } from './agent_connect.js';
import { attachReferencePicker, referenceItems, syncReferencePrompt, referenceInputs, mediaRows, mediaNodeOf } from './prompt_references.js';
import { expandCast } from './cast_mention.js';
import { outputDownloadURL } from './output_download.js';
import { shareButton } from './share.js';
import { reportFileName } from './report_issue.js';
import { rememberPromptDraft, savePromptDraft, trackPromptDraft, watchPromptHistory, promptHistoryRestoring } from './prompt_draft.js';
import { cleanOptionalWidgets, cleanOptionalPrompt, optionalInputValue } from './optional_inputs.js';
import { regenerateResult, createSecondPassQueue, enableNode } from './studio_queue.js';
import { secondPassAction } from './result_actions.js';
import { openImageEditor, icon } from './image_editor.js';
import { imageGenerationSupported } from './image_generation.js';
import { createCastLibrary } from './cast_library.js';
import { uploadMedia } from './media_upload.js';
import { swapState, requestRows, swapMissing, withPrefix } from './replace_state.js';
import { swapMissingText } from './replace_person.js';
import { issueButton } from './issue_button.js';
import { AUDIO_USE, usesOriginal, checkOriginal, checkedLength, failureText, multipleText, createUseSwitch, originalBlocked } from './audio_original.js';

const languageOverride = typeof location !== 'undefined'
    ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en'
    && String(navigator.language || '').toLowerCase().startsWith('zh'));
const text = (en, zh) => cn ? zh : en;
const style = document.createElement("style");
style.textContent = `
.fv-panel{box-sizing:border-box;width:100%;height:100%;padding:16px;background:var(--fv-card);color:var(--fv-ink);border:1px solid var(--fv-border);border-radius:var(--fv-r-md,12px);font:13px/1.6 var(--fv-font);overflow:auto;color-scheme:dark}
.fv-panel *{box-sizing:border-box}.fv-toolbar,.fv-row,.fv-links{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.fv-panel .fv-links{justify-content:center}.fv-panel[data-freevideo=result]>.fv-note{text-align:center;text-wrap:balance}
.fv-panel .fv-toolbar{padding:4px 0 8px}.fv-panel .fv-stats{text-align:center}.fv-panel .fv-stat{padding:10px 3px;border-radius:var(--fv-r-md,12px);background:var(--fv-raised)}
.fv-panel .fv-stats[hidden]{display:none}
.fv-panel .fv-connected{display:flex;align-items:center;justify-content:center;gap:6px;flex-wrap:wrap;margin:10px 0}.fv-panel .fv-connection{border:1px solid var(--fv-border);border-radius:var(--fv-r-sm,8px);padding:5px 9px;background:var(--fv-selected);color:var(--fv-accent);font-size:var(--fv-micro,12px)}
.fv-panel :focus-visible{outline:2px solid var(--fv-accent);outline-offset:2px}
.fv-panel[data-freevideo=result]{display:flex;flex-direction:column;justify-content:center}.fv-panel[data-freevideo=result]:has(>.fv-generation-progress:not([hidden])){justify-content:flex-start}.fv-panel .fv-connected-card{text-align:center;padding:28px 12px;border:1px solid var(--fv-border);border-radius:var(--fv-r-md,12px);color:var(--fv-muted);background:var(--fv-raised)}
.fv-panel button,.fv-panel select,.fv-panel a{box-sizing:border-box;min-height:var(--fv-h-sm,32px);border:1px solid var(--fv-border);border-radius:var(--fv-r-sm,8px);background:var(--fv-raised);color:var(--fv-ink);padding:5px 10px;font:inherit;font-weight:var(--fv-medium,500);cursor:pointer;text-decoration:none}
.fv-panel button:hover,.fv-panel a:hover{background:var(--fv-hover)}.fv-panel button:disabled{opacity:.45;cursor:wait}
.fv-menu-failure{width:min(460px,calc(100vw - 32px));height:auto}.fv-menu-failure::backdrop{background:#0009}
.fv-panel select{max-width:170px}.fv-note{color:var(--fv-muted);font-size:var(--fv-micro,12px);margin:8px 0;white-space:pre-wrap}.fv-error{color:var(--fv-danger)}.fv-panel .fv-depth-only input,.fv-panel .fv-sound input{min-height:0;height:auto}.fv-panel .fv-depth-only{display:flex;align-items:center;gap:6px;margin:0 0 8px;white-space:nowrap;font-size:var(--fv-micro);cursor:pointer}
.fv-mode{font-size:var(--fv-micro,12px);color:var(--fv-accent)}
.fv-panel[data-freevideo=media]>.fv-mode{display:inline-block;margin:4px 0 2px;padding:2px 8px;border-radius:var(--fv-r-xs,3px);background:var(--fv-selected);font-weight:var(--fv-medium,500)}.fv-card{border:1px solid var(--fv-border);border-radius:var(--fv-r-md,12px);padding:10px;margin-top:10px;background:var(--fv-raised)}
.fv-card[data-enabled=false]{opacity:.5}.fv-file{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:60px}
.fv-preview{display:block;width:100%;max-height:160px;object-fit:contain;margin:8px 0;border-radius:var(--fv-r-sm,8px);background:var(--fv-bg)}
.fv-card audio{width:100%;height:36px;margin:8px 0}.fv-empty{padding:35px 12px;text-align:center;color:var(--fv-muted);border:1px dashed var(--fv-border);border-radius:var(--fv-r-md,12px);margin-top:12px}
.fv-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;margin-bottom:9px}.fv-stat strong{display:block;color:var(--fv-ink);font-size:var(--fv-strong,16px);font-weight:var(--fv-semibold,600);font-variant-numeric:tabular-nums}.fv-stat span{font-size:var(--fv-micro,12px);color:var(--fv-muted)}
`;
document.head.append(style);

function el(tag, label, className) {
    const element = document.createElement(tag);
    if (label !== undefined) element.textContent = label;
    if (className) element.className = className;
    return element;
}
function button(label, title, action) {
    const b = el("button", label); b.type = "button"; b.title = title; b.onclick = action; return b;
}
function viewURL(file, type = "input") {
    const split = file.lastIndexOf("/");
    return api.apiURL("/view?" + new URLSearchParams({
        filename: file.slice(split + 1), subfolder: split < 0 ? "" : file.slice(0, split), type,
    }));
}
const MEDIA_EXTENSIONS = {
    image: ["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"],
    video: ["mp4", "mov", "webm", "mkv", "m4v"],
    audio: ["wav", "mp3", "flac", "ogg", "m4a", "aac", "opus"],
};
const extensionOf = name => name.includes(".") ? name.split(".").pop().toLowerCase() : "";
const acceptList = kinds => kinds.flatMap(kind => MEDIA_EXTENSIONS[kind]).map(ext => "." + ext).join(",");
function mediaKindOrNull(name) {
    const ext = extensionOf(name);
    return Object.keys(MEDIA_EXTENSIONS).find(kind => MEDIA_EXTENSIONS[kind].includes(ext)) ?? null;
}
function mediaKind(name) {
    const kind = mediaKindOrNull(name);
    if (!kind) throw new Error(text("Choose an image, video or audio file", "请选择图片、视频或音频文件"));
    return kind;
}
function skippedNote(names) {
    const found = [...new Set(names.map(extensionOf).filter(Boolean).map(ext => "." + ext))], kinds = found.slice(0, 3);
    const more = found.length > 3, n = names.length;
    return text(`Skipped ${n} unsupported file${n === 1 ? "" : "s"}${kinds.length ? ` (${kinds.join(", ")}${more ? " and more" : ""})` : ""}`,
        `已跳过 ${n} 个不支持的文件${kinds.length ? `（${kinds.join("、")}${more ? " 等" : ""}）` : ""}`);
}

const referenceHelp = () => text('Each reference image counts as one media item.', '每张参考图片各占一个素材名额。');
// Input names stay English like every other node's; the panel's chips are translated.
const referenceSocket = index => `Reference image ${index + 1}`;
const referenceLabel = index => text(`Reference image ${index + 1}`, `参考图片 ${index + 1}`);

function syncReferenceSockets(node) {
    if (!node.inputs || !node.addInput) return;
    const last = referenceInputs.reduce((at, name, index) => node.inputs.find(i => i.name === name)?.link != null ? index : at, -1);
    const count = Math.min(8, last + 2);
    for (let index = node.inputs.length - 1; index >= 0; index--) {
        const position = referenceInputs.indexOf(node.inputs[index].name);
        if (position >= count && node.inputs[index].link == null) node.removeInput(index);
    }
    for (let index = 0; index < count; index++) {
        const name = referenceInputs[index];
        if (!node.inputs.some(i => i.name === name)) node.addInput(name, 'IMAGE', {shape: node.inputs.find(i => i.name === 'reference')?.shape});
        const input = node.inputs.find(i => i.name === name);
        input.label = referenceSocket(index); input.tooltip = referenceHelp();
    }
    const images = referenceInputs.map(name => node.inputs.find(i => i.name === name)).filter(Boolean);
    const others = node.inputs.filter(i => !referenceInputs.includes(i.name));
    const audio = others.findIndex(i => i.name === 'reference_audio');
    const at = audio < 0 ? others.length : audio;
    node.inputs.splice(0, node.inputs.length, ...others.slice(0, at), ...images, ...others.slice(at));
    node.inputs.forEach((input, index) => {
        const link = node.graph?.links?.get?.(input.link) ?? node.graph?.links?.[input.link];
        if (link) link.target_slot = index;
    });
    node.setDirtyCanvas?.(true, true);
}

function mediaPanel(node, mount = null) {
    const serialized = node.widgets.find(w => w.name === "assets");
    serialized.type = "hidden";
    serialized.computeSize = () => [0, -4];
    serialized.draw = () => {};
    const panel = el("div", undefined, "fv-panel"); panel.dataset.freevideo = "media";
    panel.tabIndex = 0;
    const toolbar = el("div", undefined, "fv-toolbar");
    const note = el("div", "", "fv-note"); note.setAttribute("role", "status");
    const mode = el("div", "", "fv-mode");
    const cards = el("div"), connections = el("div", undefined, "fv-connected");
    const picker = el("input"); picker.type = "file"; picker.multiple = true;
    picker.accept = acceptList(["image", "video", "audio"]); picker.hidden = true;
    const add = button(text("Upload files", "上传文件"), text("Upload images, video or audio", "上传图片、视频或音频"), () => picker.click());
    const generateImage = button(text('Generate with AI', 'AI 生成'), text('Generate with AI', 'AI 生成'), () => newImage());
    generateImage.prepend(document.createRange().createContextualFragment(icon('generate')));
    generateImage.hidden = !imageGenerationSupported();
    const audioPicker = el('input'); audioPicker.type = 'file'; audioPicker.multiple = true;
    audioPicker.accept = acceptList(["audio"]); audioPicker.hidden = true;
    const addAudio = button(text('Reference audio', '参考音频'),
        text('Add a voice, music or rhythm reference (its first 15 seconds at most are used)', '添加音色、音乐或节奏参考（最多使用前 15 秒）'), () => audioPicker.click());
    const audioHelp = el('div', '', 'fv-note'); audioHelp.hidden = true;
    const role = el("select"); role.setAttribute("aria-label", text("Use new images as", "新图片用途"));
    for (const [value, label] of [["reference", text("Reference", "参考")], ["first", text("First frame", "首帧")], ["last", text("Last frame", "尾帧")]]) {
        const option = el("option", label); option.value = value; role.append(option);
    }
    role.value = 'reference';
    toolbar.append(add, generateImage, addAudio, role, picker, audioPicker); panel.append(toolbar, mode, note, audioHelp, connections, cards);
    const cast = createCastLibrary({api, text, viewURL, uploadOne});
    panel.prepend(cast.element);
    const read = () => {
        const rows = JSON.parse(serialized.value || "[]");
        if (!Array.isArray(rows) || rows.length > 32) throw new Error(text("Use at most 32 media items", "最多使用 32 个素材"));
        return rows;
    };
    const message = (value, error = false) => { note.textContent = value; note.classList.toggle("fv-error", error); };
    const update = rows => { serialized.value = JSON.stringify(rows); node.graph?.change(); window.dispatchEvent(new CustomEvent('freevideo-media', {detail: node.id})); };
    const changed = event => { if (String(event.detail) === String(node.id)) render(); };
    window.addEventListener('freevideo-media', changed);
    // The video this panel feeds: its canvas is the natural shape for a keyframe crop.
    const graphLink = id => node.graph?.links?.get?.(id) ?? node.graph?.links?.[id];
    const videoSize = () => {
        for (const other of node.graph?._nodes || node.graph?.nodes || []) {
            const input = other.inputs?.find(i => i.name === 'media' && i.link != null);
            if (!input || String(graphLink(input.link)?.origin_id) !== String(node.id)) continue;
            const size = name => Number(other.widgets?.find(w => w.name === name)?.value);
            if (size('width') > 0 && size('height') > 0) return {width: size('width'), height: size('height')};
        }
        return null;
    };
    // One file into ComfyUI's input folder, through FreeVideo's streaming upload.
    async function uploadOne(file) {
        return uploadMedia(api, file);
    }
    // An agent's edit leaves its editor document beside the picture: open that, so people continue its layers.
    async function agentEdit(file) {
        if (!file.startsWith('freevideo/agent/') || mediaKind(file) !== 'image') return null;
        try {
            const response = await fetch(viewURL(file.replace(/\.[^./]+$/, '.edit.json')), {cache: 'no-store'});
            if (!response.ok) return null;
            const doc = await response.json(), base = doc?.version === 2 && doc.layers?.find(l => l.id === 'base');
            return base?.src ? {edit: doc, source: base.src} : null;
        } catch { return null; }
    }
    async function editImage(file, tool = null) {
        if (node.isUploading) return;
        const original = read().find(row => row.file === file);
        if (!original) return;
        const agent = original.edit ? null : await agentEdit(original.file);
        const source = original.source || agent?.source || original.file;
        // Other pictures in this panel can be placed on the image as layers.
        const library = read().filter(row => row.file !== file && mediaKind(row.file) === 'image')
            .map(row => ({file: row.file, label: (row.source || row.file).split('/').pop()}));
        let outcome;
        try {
            outcome = await openImageEditor({source, name: source, target: videoSize(), edit: original.edit || agent?.edit || null, role: original.role,
                t: text, resolve: viewURL, upload: uploadOne, library, tool});
        } catch (error) { message(text('This image cannot be edited right now; choose an image again.', '图片暂时无法编辑，可重新选择图片。'), true); return; }
        if (!outcome) return;
        // The cards may have changed meanwhile; find this one again by its file.
        const rows = read(), at = rows.findIndex(row => row.file === file);
        if (at < 0) return;
        if (outcome.restore) {
            const {source: kept, edit: _, ...rest} = rows[at];
            rows[at] = {...rest, file: kept || rest.file}; update(rows);
            message(text('Restored the original image.', '已恢复原图。')); return;
        }
        node.isUploading = true; add.disabled = true; addAudio.disabled = true;
        message(text('Uploading the edited image…', '正在上传编辑后的图片…'));
        try {
            const result = await uploadOne(new File([outcome.blob], outcome.name, {type: outcome.blob.type}));
            const latest = read(), index = latest.findIndex(row => row.file === file);
            if (index < 0) return;
            const hasOriginal = outcome.edit.layers.some(layer => layer.id === 'base');
            latest[index] = {...latest[index], file: result.file, ...(hasOriginal ? {source} : {}), edit: outcome.edit}; update(latest);
            message(hasOriginal ? text('The edited image is saved, and the original is kept.',
                '编辑后的图片已保存，原图已保留。') : '');
        } catch (error) { message(error.message, true); }
        finally { node.isUploading = false; add.disabled = false; addAudio.disabled = false; }
    }
    async function newImage() {
        if (node.isUploading) return;
        if (read().length >= 32) { message(text('The media list is full (32 items, including disabled ones). Remove some to add more.', '素材已满 32 个（含未启用的），请先移除不用的素材。'), true); return; }
        const library = read().filter(row => mediaKind(row.file) === 'image')
            .map(row => ({file: row.file, label: (row.source || row.file).split('/').pop()}));
        let outcome;
        try {
            outcome = await openImageEditor({target: videoSize(), role: role.value, t: text,
                resolve: viewURL, upload: uploadOne, library});
        } catch { message(text('This image cannot be edited right now; choose an image again.', '图片暂时无法编辑，可重新选择图片。'), true); return; }
        if (!outcome?.blob) return;
        if (read().length >= 32) { message(text('The media list is full (32 items, including disabled ones). Remove some to add more.', '素材已满 32 个（含未启用的），请先移除不用的素材。'), true); return; }
        node.isUploading = true; add.disabled = addAudio.disabled = generateImage.disabled = true;
        try {
            const result = await uploadOne(new File([outcome.blob], outcome.name, {type: outcome.blob.type}));
            const latest = read();
            latest.push({file: result.file, role: role.value, enabled: true, edit: outcome.edit});
            update(latest);
        } catch (error) { message(error.message, true); }
        finally { node.isUploading = false; add.disabled = addAudio.disabled = generateImage.disabled = false; }
    }
    function restoreImage(file) {
        const rows = read(), at = rows.findIndex(row => row.file === file);
        if (at < 0 || !rows[at].source) return;
        const {source, edit: _, ...rest} = rows[at];
        rows[at] = {...rest, file: source}; update(rows);
        message(text('Restored the original image. Edit it again to reapply the changes.', '已恢复原图；再次编辑可以重新应用修改。'));
    }
    // Reference | Use as is: the choice is checked against the file before it is kept.
    function useSwitch(row) {
        const group = createUseSwitch({t: text, use: usesOriginal(row) ? AUDIO_USE.original : AUDIO_USE.reference, onPick: async value => {
            const current = read(), at = current.findIndex(r => r.file === row.file);
            if (at < 0) return;
            if (value === AUDIO_USE.reference) { const {use: _use, range: _range, ...rest} = current[at]; current[at] = rest; update(current); return; }
            if (current.some((other, i) => i !== at && other.enabled !== false && usesOriginal(other))) { message(multipleText(text), true); return; }
            group.set(AUDIO_USE.original, true);
            const result = await checkOriginal(api, {...current[at], use: AUDIO_USE.original});
            const latest = read(), now = latest.findIndex(r => r.file === row.file);
            if (now < 0) return;
            if (!result.ok) { group.set(AUDIO_USE.reference); message(failureText(result, text), true); return; }
            // A preview or a continued video turned on while the clip was checked: it stays a reference.
            if (originalBlocked()) { group.set(AUDIO_USE.reference); return; }
            latest[now] = {...latest[now], use: AUDIO_USE.original}; update(latest);
        }});
        return group;
    }
    function render() {
        for (const preview of cards.querySelectorAll("video,audio")) {
            preview.pause(); preview.removeAttribute("src"); preview.load();
        }
        cards.replaceChildren();
        let rows;
        try { rows = read(); } catch (error) { message(error.message, true); return; }
        const active = rows.filter(row => row.enabled !== false);
        const linked = name => node.inputs?.find(i => i.name === name)?.link != null;
        // A Generate node that continues a video takes these keyframes as pictures, so they may sit beside references.
        const continuing = (node.outputs?.[0]?.links || []).some(id => {
            const target = node.graph?.getNodeById(node.graph?.links[id]?.target_id);
            return target?.type === 'FreeVideoGenerate' && (target.freevideoContinues?.()
                || optionalInputValue('continue_from', target.widgets?.find(w => w.name === 'continue_from')?.value) !== '');
        });
        const first = active.filter(row => row.role === "first").length + Number(linked('first'));
        const last = active.filter(row => row.role === "last").length + Number(linked('last'));
        const audioRefs = active.filter(row => row.role === 'reference' && mediaKind(row.file) === 'audio').length + Number(linked('reference_audio'));
        const refs = active.filter(row => row.role === "reference").length + referenceInputs.filter(linked).length + Number(linked('reference_audio'));
        audioHelp.hidden = !audioRefs;
        audioHelp.textContent = text('Use <Audio 1>, <Audio 2> in your prompt to guide voice, music or rhythm; up to the first 15 s is used. Set accompanying images to Reference.',
            '在提示词里用 <Audio 1>、<Audio 2> 参考音色、音乐或节奏，最多取前 15 秒；搭配的图片选“参考”。');
        connections.replaceChildren();
        for (const [name, label] of [['first', text('First frame', '首帧')], ['last', text('Last frame', '尾帧')], ...referenceInputs.map((name, index) => [name, referenceLabel(index)]), ['reference_audio', text('Reference audio', '参考音频')]]) {
            if (!linked(name)) continue;
            const link = node.graph?.links[node.inputs.find(i => i.name === name).link];
            const source = link && node.graph?.getNodeById(link.origin_id);
            const chip = el('span', `${label} · ${text('Connected', '已连接')}`, 'fv-connection');
            chip.title = source?.title || source?.type || label; connections.append(chip);
        }
        mode.textContent = refs ? text("Reference video · experimental", "参考生成 · 实验性") : first && last ? text("First + last frame", "首尾帧生成") : first ? text("Image to video", "图生视频") : last ? text("Last frame to video", "尾帧生成") : text("Text to video · media is optional", "文生视频 · 素材可留空");
        message(refs > 32 ? text('There are more than 32 references; remove some before generating.', '参考素材超过 32 个，可移除部分后再生成。') : continuing && active.filter(row => row.role === 'reference' && mediaKind(row.file) === 'image').length > 3 ? text('When continuing, only the first 3 images are used.', '接续时只用前 3\u00a0张图片。') : (first || last) && continuing ? text("When continuing a video, the first and last frames are used as references.", "接续视频时，首尾帧会用作参考图。") : refs && (first || last) ? text("Choose keyframes or references. Disable unused cards.", "首尾帧与参考模式不能混用，请关闭本次不用的素材。") : first > 1 || last > 1 ? text("Keep only one first frame and one last frame enabled.", "首帧和尾帧各只能启用一张图片。") : text("Drop or paste files, or connect images and audio on the left.", "可拖入、粘贴文件，或连接左侧的图片和音频。"), Boolean(refs > 32 || refs && (first || last) && !continuing || first > 1 || last > 1));
        if (!rows.length && !connections.childElementCount) cards.append(el("div", text("Leave empty for text to video.", "留空则为文生视频。"), "fv-empty"));
        if (!rows.length && connections.childElementCount) cards.append(el('div', text('Media comes from the connected inputs; no upload is needed.', '素材来自已连接的输入，无需上传。'), 'fv-connected-card'));
        rows.forEach((row, index) => {
            const card = el("div", undefined, "fv-card"); card.dataset.enabled = String(row.enabled !== false);
            const top = el("div", undefined, "fv-row");
            const enabled = el("input"); enabled.type = "checkbox"; enabled.checked = row.enabled !== false;
            enabled.setAttribute("aria-label", text("Use media", "使用素材")); enabled.onchange = () => {
                if (enabled.checked && usesOriginal(rows[index]) && rows.some((other, i) => i !== index && other.enabled !== false && usesOriginal(other))) {
                    enabled.checked = false; message(multipleText(text), true); return;
                }
                rows[index].enabled = enabled.checked; update(rows);
            };
            const label = el("span", row.cast?.name || (row.source || row.file).split("/").pop(), "fv-file"); label.title = row.file;
            const select = el("select"); select.setAttribute("aria-label", text("Media role", "素材用途"));
            const kind = mediaKind(row.file);
            for (const [value, title] of (kind === "image" ? [["reference", text("Reference", "参考")], ["first", text("First frame", "首帧")], ["last", text("Last frame", "尾帧")]] : [["reference", text("Reference", "参考")]])) {
                const option = el("option", title); option.value = value; select.append(option);
            }
            select.value = row.role; select.onchange = () => { rows[index].role = select.value; update(rows); };
            top.append(enabled, label);
            if (row.source) top.append(el('span', text('Edited', '已编辑'), 'fv-edited'));
            if (kind === "audio") top.append(useSwitch(row)); else top.append(select);
            card.append(top);
            const preview = el(kind === "image" ? "img" : kind); preview.className = "fv-preview";
            preview.src = viewURL(row.file);
            if (kind === "image") {
                preview.loading = "lazy"; preview.alt = label.textContent;
                // The picture itself opens the editor; a badge says so on hover.
                const wrap = el("div", undefined, "fv-preview-wrap");
                const badge = el("button", undefined, "fv-preview-edit"); badge.type = "button";
                badge.innerHTML = icon("edit", 14); badge.append(el("span", text("Edit", "编辑")));
                badge.title = text("Crop, retouch, add text or layers", "裁剪、修图、加文字或图层");
                badge.onclick = event => { event.stopPropagation(); editImage(row.file); };
                preview.title = text("Edit image", "编辑图片"); preview.onclick = () => editImage(row.file);
                wrap.append(preview, badge); card.append(wrap);
                const size = videoSize();
                if (size && (row.role === "first" || row.role === "last")) {
                    // A keyframe of another shape is centre-cropped when the video starts; offer the crop now.
                    preview.addEventListener("load", () => {
                        const off = Math.abs(Math.log((preview.naturalWidth / preview.naturalHeight) / (size.width / size.height))) > .02;
                        if (!off || !preview.isConnected) return;
                        const note = el("div", undefined, "fv-shape-note");
                        note.append(el("span", text('Different aspect ratio from the video; it will be center-cropped.',
                            '和视频比例不同，会居中裁剪。')),
                            button(text("Adjust crop", "调整裁剪"), text("Choose what the video keeps", "选择视频保留的画面"), () => editImage(row.file, "crop")));
                        wrap.after(note);
                    }, {once: true});
                }
            } else {
                preview.controls = true; preview.preload = "metadata"; if (kind === "video") { preview.muted = true; preview.playsInline = true; }
                card.append(preview);
            }
            let showLength = () => {};
            if (kind === "video" && row.role === "reference") {
                // Off by default: the video itself is the reference. On: its depth picture is, and the picture and colours are not asked for.
                const depth = el("label", undefined, "fv-depth-only"), box = el("input");
                box.type = "checkbox"; box.checked = row.depth === true;
                box.onchange = () => { if (box.checked) rows[index].depth = true; else delete rows[index].depth; update(rows); };
                depth.append(box, el("span", text("Depth only", "仅参考深度")));
                depth.title = text("Uses a depth version of this video as the reference.", "转成深度视频后再作参考。");
                card.append(depth);
            }
            if (kind === "video") {
                const sound = el("label", undefined, "fv-sound"); const box = el("input"); box.type = "checkbox"; box.checked = row.sound !== false;
                box.onchange = () => { if (box.checked) delete rows[index].sound; else rows[index].sound = false; showLength(); update(rows); };
                sound.append(box, el("span", text("Use the video's sound", "使用视频声音"))); card.append(sound);
            }
            if (kind !== "image") {
                // References are cut to the generated length (at most 15 s); say so before the run.
                // A video used without its sound has no audio to cut; audio used as is is not cut.
                const length = el("div", "", "fv-note"); length.hidden = true; card.append(length);
                showLength = () => {
                    const long = preview.duration > 15.05 && Number.isFinite(preview.duration);
                    length.hidden = !long || row.sound === false || rows[index]?.sound === false || usesOriginal(row);
                    if (long) length.textContent = text(`Audio length: ${preview.duration.toFixed(1)} s; uses up to the first 15 s within the video length.`,
                        `音频长 ${preview.duration.toFixed(1)} 秒，最多取前 15 秒且不超过视频时长。`);
                };
                preview.onloadedmetadata = () => showLength();
            }
            const controls = el("div", undefined, "fv-row");
            const move = delta => { const target = index + delta; if (target < 0 || target >= rows.length) return; [rows[index], rows[target]] = [rows[target], rows[index]]; update(rows); };
            if (kind === "image") {
                controls.append(button(text("Edit", "编辑"), text("Crop, retouch, add text or layers", "裁剪、修图、加文字或图层"), () => editImage(row.file)));
                if (row.source) controls.append(button(text("Original", "恢复原图"), text("Use the original image again; the edit can be reopened", "改回原图；之后仍可重新编辑"), () => restoreImage(row.file)));
            }
            controls.append(button("↑", text("Move earlier", "向前移动"), () => move(-1)), button("↓", text("Move later", "向后移动"), () => move(1)),
                button(text("Remove", "移除"), text("Remove from this request; keep the uploaded file", "从本次请求移除，保留已上传文件"), () => { rows.splice(index, 1); update(rows); }));
            card.append(controls); cards.append(card);
        });
        // A saved choice is checked again; a clip the server now refuses goes back to Reference.
        const saved = rows.find(row => row.enabled !== false && usesOriginal(row));
        if (saved && !checkedLength(saved)) checkOriginal(api, saved).then(result => {
            if (result.ok || result.code === 'unreachable') return;
            const latest = read(), at = latest.findIndex(row => row.file === saved.file && usesOriginal(row));
            if (at < 0) return;
            const {use: _use, range: _range, ...rest} = latest[at]; latest[at] = rest; update(latest);
            message(failureText(result, text), true);
        });
    }
    let uploading = false;
    serialized.serializeValue = () => {
        if (node.isUploading) throw new Error(text("Wait for media uploads to finish before running", "请等待素材上传完成后再生成"));
        // Replace person: the two slots are the media, and both are needed.
        if (swapState(node)) {
            const missing = swapMissing(swapState(node));
            if (missing) throw new Error(swapMissingText(missing, text));
            return JSON.stringify(requestRows(swapState(node)));
        }
        // The run takes only what it uses (and which character a picture belongs to); the editing data of a picture stays in the workflow.
        try { return JSON.stringify(read().map(({file, role, enabled, cast, sound, depth, use, range}) => ({file, role, enabled, ...(use === AUDIO_USE.original ? {use, ...(range && typeof range === 'object' ? {range} : {})} : {}), ...(sound === false && mediaKindOrNull(file) === 'video' ? {sound: false} : {}), ...(depth === true && role === 'reference' && mediaKindOrNull(file) === 'video' ? {depth: true} : {}), ...(typeof cast?.id === 'string' && cast.id ? {cast: {id: cast.id}} : {})}))); } catch { return serialized.value; }
    };
    async function upload(files) {
        if (node.isUploading || !files.length) return;
        const chosen = Array.from(files);
        const usable = chosen.filter(file => mediaKindOrNull(file.name));
        const skipped = chosen.filter(file => !mediaKindOrNull(file.name)).map(file => file.name);
        uploading = true; node.isUploading = true; add.disabled = true; addAudio.disabled = true;
        try {
            for (const [index, file] of usable.entries()) {
                if (read().length >= 32) throw new Error(text("Use at most 32 media items", "最多使用 32 个素材"));
                const kind = mediaKind(file.name);
                message(`${text("Uploading", "正在上传")} ${index + 1}/${usable.length} · ${file.name}`);
                const result = await uploadOne(file);
                // Read again after the wait: another place may have changed the list meanwhile.
                const rows = read(); if (rows.length >= 32) throw new Error(text("Use at most 32 media items", "最多使用 32 个素材"));
                rows.push({file: result.file, role: kind === "image" ? role.value : "reference", enabled: true}); update(rows);
            }
            if (skipped.length) message(skippedNote(skipped), !usable.length);
        } catch (error) { message(error.message, true); }
        finally { uploading = false; node.isUploading = false; add.disabled = false; addAudio.disabled = false; picker.value = ""; audioPicker.value = ''; }
    }
    picker.onchange = () => upload(picker.files);
    audioPicker.onchange = () => upload(audioPicker.files);
    panel.ondragover = event => { event.preventDefault(); event.stopPropagation(); };
    panel.ondrop = event => { event.preventDefault(); event.stopPropagation(); upload(event.dataTransfer.files); };
    panel.onpaste = event => { if (event.clipboardData?.files.length) { event.preventDefault(); event.stopPropagation(); upload(event.clipboardData.files); } };
    const dispose = () => { window.removeEventListener('freevideo-media', changed); cast.destroy(); };
    if (mount) { mount.append(panel); render(); return dispose; }
    node.freevideoCreateMediaEditor = target => mediaPanel(node, target);
    node.addDOMWidget("freevideo_media", "freevideo_media", panel, {serialize: false, getMinHeight: () => 300, getMaxHeight: () => 520});
    const removed = node.onRemoved;
    node.onRemoved = function (...args) { dispose(); return removed?.apply(this, args); };
    const configure = node.onConfigure;
    node.onConfigure = function (...args) { const result = configure?.apply(this, args); queueMicrotask(() => { syncReferenceSockets(node); render(); }); return result; };
    const connectionChanged = node.onConnectionsChange;
    node.onConnectionsChange = function (...args) { const result = connectionChanged?.apply(this, args); queueMicrotask(() => { syncReferenceSockets(node); update(read()); }); return result; };
    syncReferenceSockets(node); node.setSize([460, 460]); render();
}

function resultPanel(node) {
    const input = node.widgets?.find(w => w.name === 'text')?.inputEl;
    if (input?.tagName === 'TEXTAREA') {
        const picker = attachReferencePicker(input, {items: () => referenceItems(node), t: text, view: viewURL});
        const changed = e => {
            if (String(e.detail) !== String(node.id)) return;
            const value = node.widgets.find(w => w.name === 'text').value;
            if (input.value !== value) input.value = value;
            picker.refresh();
        };
        window.addEventListener('freevideo-reference-prompt', changed);
        const removed = node.onRemoved;
        node.onRemoved = function (...args) { picker.dispose(); window.removeEventListener('freevideo-reference-prompt', changed); return removed?.apply(this, args); };
    }
    node.addDOMWidget('freevideo_prompt_guide', 'freevideo_prompt_guide', promptGuide(),
        {serialize: false, getMinHeight: () => 58, getMaxHeight: () => 76});
    const panel = el("div", undefined, "fv-panel"); panel.dataset.freevideo = "result";
    // Grow the node for the panel's new height. A node that is already tall
    // enough keeps its size, so lay its widgets out again; otherwise the result
    // panel keeps its earlier height and clips the progress area.
    const fitPanel = () => {
        node.setSize([node.size[0], Math.max(node.size[1], node.computeSize()[1])]);
        node.arrange?.(); node.setDirtyCanvas?.(true, true);
    };
    const progress = createGenerationProgress(text, undefined, {api, onLayout: fitPanel});
    panel.append(progress.element, progress.stall, progress.report);
    let showingResult = false;
    node.freevideoReportProgress = message => {
        node.freevideoReportId = progress.updateReport(message);
    };
    node.freevideoShowProgress = message => {
        if (showingResult) {
            panel.replaceChildren(progress.element, progress.stall, progress.report);
            panel.title = ''; node.freevideoPrewarm = '';
            showingResult = false; warn();
        }
        node.freevideoReportProgress(message);
        const fresh = message.reset || message.new_request || message.overall?.reset;
        const retry = message.retry || (fresh ? undefined : node.freevideoProgress?.retry);
        node.freevideoProgress = {...message, retry, received_at: message.received_at ?? Date.now()}; progress.update(node.freevideoProgress);
        if (panel.firstElementChild !== progress.element) panel.prepend(progress.element);
        if (progress.report.parentNode !== panel) panel.append(progress.stall, progress.report);
        fitPanel();
    };
    node.freevideoStopProgress = () => { node.freevideoProgress = null; progress.hide(); };
    const removed = node.onRemoved;
    node.onRemoved = function (...args) { progress.dispose(); return removed?.apply(this, args); };
    const failure = createErrorPanel(text);
    node.freevideoShowFailure = detail => {
        const context = {stage: 'execution', node, graph: app.graph, progress: node.freevideoProgress};
        node.freevideoStopProgress();
        node.freevideoFailureReport = failure.show(detail, true, context);
        node.freevideoFailure = errorText(node.freevideoFailureReport);
        panel.append(failure.element); fitPanel();
    };
    node.freevideoClearFailure = () => { node.freevideoFailure = ''; node.freevideoFailureReport = null; failure.clear(); };
    const warning = el('div', '', 'fv-note');
    const hasLoRA = () => node.inputs?.some(input => input.name === 'loras' && input.link != null);
    const warn = () => {
        warning.hidden = !hasLoRA(); warning.textContent = hasLoRA() ? loraWarning() : '';
        if (panel.lastElementChild !== warning) panel.append(warning);
    };
    panel.append(el("div", text("Video, audio and timings appear here after generation.", "生成后在这里查看视频、音频和耗时。"), "fv-note"));
    const panelMargin = 10;
    const panelHeight = () => {
        // A hidden canvas (the creation panel is open) has no layout to measure; estimate 40 px per notice.
        if (showingResult && !panel.getClientRects().length) {
            const notes = [...panel.children].filter(child => child !== warning && !child.hidden && /\bfv-note\b/.test(child.className)).length;
            return (hasLoRA() ? 240 : 140) + 40 * notes + (progress.stallShown() ? Math.max(90, (progress.stall.scrollHeight || 0) + 18) : 0);
        }
        if (showingResult) {
            const style = getComputedStyle(panel);
            // ComfyUI insets the DOM element by the widget margin on both sides.
            let height = 2 * panelMargin + (parseFloat(style.borderTopWidth) || 0) + (parseFloat(style.borderBottomWidth) || 0)
                + (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
            for (const child of panel.children) {
                const style = getComputedStyle(child);
                if (style.display === 'none') continue;
                height += child.offsetHeight + (parseFloat(style.marginTop) || 0) + (parseFloat(style.marginBottom) || 0);
            }
            return height;
        }
        // The 218-character English notice is 8 characters shorter than before.
        // Reserve at least 90px; a narrow node or larger font may wrap further.
        return (hasLoRA() ? 240 : 140) + (node.freevideoFailure ? 280 : 0) + (node.freevideoProgress ? (progress.percentShown?.() === false ? 142 : 190) + 40 * progress.noticeCount() : 0)
            + (progress.stallShown() ? Math.max(90, (progress.stall.scrollHeight || 0) + 18) : 0);
    };
    node.addDOMWidget("freevideo_result", "freevideo_result", panel, {serialize: false, margin: panelMargin, getMinHeight: panelHeight, getMaxHeight: panelHeight});
    const connected = node.onConnectionsChange, configured = node.onConfigure;
    node.onConnectionsChange = function (...args) { const result = connected?.apply(this, args); warn(); queueMicrotask(syncPrompts); return result; };
    // Nodes made from now on carry the 8 + 3 default; a later choice of 8 + 2 is kept.
    node.properties ??= {};
    node.properties.freevideo_refine_v3 = true;
    node.onConfigure = function (...args) {
        const result = configured?.apply(this, args);
        // The earlier private test put a force checkbox at this position,
        // before the public version added sampling-step widgets.
        const base = this.widgets?.find(w => w.name === 'base_steps');
        if (base && typeof base.value === 'boolean') base.value = 8;
        cleanOptionalWidgets(this);
        // Workflows saved with the earlier 8 + 2 default move to the 8 + 3 default once.
        if (!args[0]?.properties?.freevideo_refine_v3) {
            const refine = this.widgets?.find(w => w.name === 'refine_steps');
            const pass = this.widgets?.find(w => w.name === 'two_pass');
            if (refine?.value === 2 && base?.value === 8 && pass?.value !== false) refine.value = 3;
        }
        warn(); return result;
    };
    warn();
    node.freevideoShowPrewarm = function (value) {
        let note = panel.querySelector('.fv-prewarm');
        if (!note) { note = el('div', '', 'fv-note fv-prewarm'); panel.append(note); }
        const onGPU = Number.isFinite(value.gpu_bytes_after);
        const size = onGPU ? ` · ${(value.gpu_bytes_after / 2 ** 30).toFixed(1)} GiB` : '';
        const percent = ` · ${Math.min(100, Math.floor(100 * value.done_bytes / value.bytes))}%`;
        note.textContent = value.purpose === 'upscale'
            ? (value.state === 'reading' ? text('Preparing model files', '正在准备模型文件') + percent
                : value.state === 'ready' ? text('Model files ready', '模型文件已就绪') : '')
            : value.state === 'loading' ? text('Preparing the prompt model', '正在准备提示词模型') + size
            : value.state === 'partial' ? text('Prompt model still preparing', '提示词模型仍在准备') + size
            : value.state === 'reading' ? text('Preparing the prompt model', '正在准备提示词模型') + percent
            : value.state === 'ready' ? (onGPU ? text('Prompt model ready', '提示词模型已就绪') + size
                : text('Prompt model ready', '提示词模型已就绪')) : '';
        node.freevideoPrewarm = note.textContent;
        window.dispatchEvent(new CustomEvent('freevideo-prewarm', {detail: {node: node.id, label: note.textContent}}));
    };
    node.freevideoShowResult = function (message) {
        const value = message?.freevideo_summary?.[0]; if (!value) return;
        showingResult = true;
        node.freevideoStopProgress();
        node.freevideoClearFailure();
        node.freevideoLastResult = value;
        window.dispatchEvent(new CustomEvent('freevideo-result', {detail: {node: node.id, value}}));
        panel.replaceChildren(); const resultNotes = [], stats = el("div", undefined, "fv-stats");
        stats.hidden = !!value.result_cache_hit;
        const number = (value, scale, unit) => Number.isFinite(value) && value >= 0 ? `${(value / scale).toFixed(1)} ${unit}` : "—";
        const unified = value.memory_model === 'unified';
        for (const [label, shown] of [[text("Generation", "生成用时"), number(value.sample_seconds, 1, "s")], [text("Total time", "总用时"), number(value.request_seconds, 1, "s")], [unified ? text("Unified memory", "统一内存总量") : text("Peak VRAM", "最高显存"), number(unified ? value.unified_total_bytes : value.vram_peak_bytes, 2 ** 30, "GiB")], [unified ? text("Process peak RAM", "进程最高内存") : text("Peak RAM", "最高内存"), number(value.ram_peak_bytes, 2 ** 30, "GiB")]]) {
            const stat = el("div", undefined, "fv-stat"); stat.append(el("strong", shown), el("span", label)); stats.append(stat);
        }
        const links = el("div", undefined, "fv-links");
        for (const [label, file] of [[text("Download video", "下载视频"), value.video], [text("Report", "报告"), value.report]]) {
            if (!file) continue;
            const link = el("a", label); link.href = outputDownloadURL(api, file); link.download = file === value.video ? '' : reportFileName();
            // Named when clicked, like the creation panel's report: saving it again is not "video.debug (1).json".
            if (file !== value.video) link.onclick = () => { link.download = reportFileName(); };
            links.append(link);
        }
        links.append(shareButton(value, text));
        links.append(el("span", value.result_cache_hit ? text("Reused previous result", "已复用上次结果") : value.conditioning_cache_hit ? text("Previous inputs reused", "已使用上次的输入") : text("Inputs ready", "输入已准备好"), "fv-mode"));
        if (value.preview) {
            links.append(secondPassAction(text, async () => {
                try { await node.freevideoUpscaleResult(value); }
                catch (error) { node.freevideoShowFailure(error); throw error; }
            }));
        }
        if (value.result_cache_hit) {
            const again = el('button', text('Regenerate', '重新生成')); again.type = 'button';
            again.onclick = async () => {
                again.disabled = true;
                try { await node.freevideoRegenerateResult(value); again.textContent = text('Queued', '已加入队列'); }
                catch (error) { again.disabled = false; node.freevideoShowFailure(error); }
            };
            links.append(again);
        }
        panel.append(stats, links, progress.stall, progress.report);
        for (const row of value.reference_trims || []) panel.append(el("div", referenceTrimText(row, text), "fv-note fv-trim-note"));
        if (value.low_memory && !value.result_cache_hit) resultNotes.push(el("div", text('Generation was slow; try a lower resolution or shorter video.', '生成较慢，可降低分辨率或缩短时长后再试。'), "fv-note"));
        if (value.thermal && !value.result_cache_hit) resultNotes.push(el("div", thermalText(value.thermal, text), "fv-note"));
        const shared = value.shared_gpu && !value.result_cache_hit ? sharedGpuText(value.shared_gpu, text) : '';
        if (shared) resultNotes.push(el("div", shared, "fv-note"));
        if (value.keep_source_audio_failed) resultNotes.push(el("div", keepSoundText(text), "fv-note"));
        if (value.encoder_file) {
            const note = el("div", encoderFileText(value.encoder_file, text), "fv-note");
            const path = encoderFilePath(value.encoder_file);
            if (path) note.append(el('pre', path, 'fv-encoder-path'));
            resultNotes.push(note);
        }
        panel.append(...resultNotes);
        warn();
        fitPanel();
    };
    node.freevideoUpscaleResult = createSecondPassQueue(api, () => node.id, async () => { enableNode(node); return cleanOptionalPrompt(await app.graphToPrompt()); });
    node.freevideoRegenerateResult = async value => {
        if (node.freevideoRegenerating) return;
        node.freevideoRegenerating = true;
        try { return await regenerateResult(api, node.id, value, async () => { enableNode(node); return cleanOptionalPrompt(await app.graphToPrompt()); }); }
        finally { node.freevideoRegenerating = false; }
    };
    const executed = node.onExecuted;
    node.onExecuted = function (message) {
        const result = executed?.apply(this, arguments);
        node.freevideoShowResult(message);
        return result;
    };
}

let configuringGraph = false;
let configuration = 0;
const watchedGraphs = new WeakSet();
function captureWorkflow(graph) {
    if (!graph || graph !== app.graph || promptHistoryRestoring(app)) return;
    watchPromptHistory(api, app);
    const tracker = app.extensionManager?.workflow?.activeWorkflow?.changeTracker;
    // Current ComfyUI and older installations expose different names.
    (tracker?.captureCanvasState || tracker?.checkState)?.call(tracker);
}
function trackCreationDraft(node) {
    trackPromptDraft(node, () => captureWorkflow(node.graph), () => app.extensionManager?.workflow?.activeWorkflow?.path);
}
function watchCreationGraph(graph) {
    if (!graph || watchedGraphs.has(graph)) return;
    watchedGraphs.add(graph);
    const changed = graph.on_change;
    let queued = false;
    graph.on_change = function (...args) {
        const result = changed?.apply(this, args);
        if (!configuringGraph && graph === app.graph && !queued) {
            queued = true;
            const revision = configuration;
            queueMicrotask(() => {
                queued = false;
                if (configuringGraph || graph !== app.graph || revision !== configuration || promptHistoryRestoring(app)) return;
                // graph.change() only redraws. Capture the finished edit, including
                // properties and the asynchronous media/LoRA panel updates.
                captureWorkflow(graph);
                for (const node of graph._nodes || []) {
                    if (node.comfyClass === 'FreeVideoGenerate' || node.type === 'FreeVideoGenerate') rememberPromptDraft(api, node);
                }
            });
        }
        return result;
    };
}
function syncPrompts(reset = false) {
    if (configuringGraph && reset !== true) return;
    for (const node of app.graph?._nodes || []) if (node.type === 'FreeVideoGenerate') syncReferencePrompt(node, text, reset === true);
}
window.addEventListener('freevideo-media', () => syncPrompts());

// health.js names what failed; files loaded before it queue their reports.
const health = item => (window.__freevideoHealth ||= []).push(item);
function showStudio(node) {
    try { return openStudio(node); } catch (error) { console.error('FreeVideo workspace:', error); health({type: 'error', where: 'studio', error}); }
}

const progressConnection = createProgressConnection(api, message => {
    const node = app.graph?.getNodeById(message.node);
    if (!node?.freevideoShowProgress) return false;
    node.freevideoReportProgress?.(message);
    if (message.result) node.freevideoShowResult({freevideo_summary: [message.result]});
    else if (['failed', 'cancelled'].includes(message.phase)) node.freevideoStopProgress();
    else node.freevideoShowProgress(message);
    window.dispatchEvent(new CustomEvent('freevideo-progress', {detail: message}));
});

const isGenerate = node => node?.comfyClass === 'FreeVideoGenerate' || node?.type === 'FreeVideoGenerate';
const isFreeVideoNode = id => /^FreeVideo/.test(String(app.graph?.getNodeById(id)?.comfyClass || app.graph?.getNodeById(id)?.type || ''));
// Only the launcher, a click or a key brings a workspace over a page; a restored tab alone never does.
const launchVisit = isLaunchVisit(globalThis.location?.search, globalThis.sessionStorage);
let userActed = false;
for (const type of ['pointerdown', 'keydown']) globalThis.addEventListener?.(type, () => { userActed = true; }, {capture: true, once: true});

let menuFailure;
async function openFromMenu() {
    menuFailure?.close();
    try {
        const node = (app.graph?._nodes || []).find(isGenerate) || await openFreeVideoWorkflow();
        if (!node) throw new Error('FreeVideo workflow unavailable');
        showStudio(node);
    } catch (error) {
        console.warn('FreeVideo menu:', error);
        const message = text('Could not complete. Try again.', '没有完成，请重试');
        const dialog = el('dialog', undefined, 'fv-panel fv-menu-failure'); menuFailure = dialog;
        dialog.setAttribute('aria-label', 'FreeVideo');
        const actions = el('div', undefined, 'fv-row');
        actions.append(issueButton(text, () => ({where: ['Open FreeVideo', '打开 FreeVideo'], message})),
            button(text('Close', '关闭'), '', () => dialog.close()));
        dialog.append(el('p', message), actions);
        dialog.onclose = () => { dialog.remove(); if (menuFailure === dialog) menuFailure = null; };
        document.body.append(dialog); dialog.showModal();
    }
}

const desktopNoticeEnabled = () => (app.extensionManager?.setting?.get?.(DESKTOP_SETTING) ?? app.ui?.settings?.getSettingValue?.(DESKTOP_SETTING) ?? true) === true;

app.registerExtension({
    name: "FreeVideo.UnifiedMedia",
    settings: [{id: DESKTOP_SETTING, name: text('Desktop notification when done', '完成时发桌面通知'),
        type: 'boolean', defaultValue: true,
        // ComfyUI calls onChange(value, undefined) while registering; only a switch from off to on is a user request.
        onChange: (value, oldValue) => { if (oldValue === false && value === true) askDesktopPermission(window, {explicit: true}); }}],
    // The one entry that stays in ComfyUI's own menu, whatever workflow is in front.
    commands: [{id: 'FreeVideo.Open', label: 'FreeVideo', function: openFromMenu}],
    menuCommands: [{path: ['Extensions'], commands: ['FreeVideo.Open']}],
    beforeConfigureGraph() { configuringGraph = true; configuration++; },
    async setup() {
        // One failing part must not take the others down; health.js shows what failed.
        for (const [where, start] of [['progress', () => progressConnection.start()], ['navigation', () => installNavigation(showStudio, agentInterfaceState(api), {openWorkflow: openFreeVideoWorkflow, recheck: () => agentInterfaceState(api)})],
            ['updates', startUpdateChecks], ['done-notify', () => startDoneNotify({api, text, desktop: desktopNoticeEnabled, isFreeVideoNode,
                isFreeVideoWorkflow: () => (app.graph?._nodes || []).some(isGenerate)})],
            ['whitebox', () => startWhiteboxWorker({api})]]) {
            try { start(); } catch (error) { health({type: 'error', where, error}); }
        }
        window.addEventListener('pagehide', () => savePromptDraft(api, true));
        void savePromptDraft(api); // Retry this tab's outbox after an interrupted save.
        window.FreeVideoUI = {...window.FreeVideoUI, ready: true};
        await notifyCompatibility();
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === 'FreeVideoMedia') {
            nodeData.description = text(nodeData.description,
                '连接图片、音频或上传文件。可以连接多张参考图片，上传的素材排在前面，连接的图片按从上到下的顺序排在后面。选择首尾帧或实验性参考生成，留空则为文生视频。');
            for (const name of referenceInputs) {
                const input = nodeData.input?.optional?.[name];
                if (input) { input[1].tooltip = referenceHelp(); input[1].display_name = referenceSocket(referenceInputs.indexOf(name)); }
            }
        }
        if (nodeData.name === 'FreeVideoReference') {
            const connected = nodeType.prototype.onConnectionsChange;
            nodeType.prototype.onConnectionsChange = function (...args) { const result = connected?.apply(this, args); queueMicrotask(syncPrompts); return result; };
            return;
        }
        if (!["FreeVideoMedia", "FreeVideoGenerate", "FreeVideoLoRAStack"].includes(nodeData.name)) return;
        if (nodeData.name === "FreeVideoGenerate") nodeType.prototype.freevideoStarterPrompt = nodeData.input?.required?.text?.[1]?.default || '';
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            this.color = '#212a37'; this.bgcolor = '#1a222e';
            if (nodeData.name === "FreeVideoMedia") mediaPanel(this);
            else if (nodeData.name === "FreeVideoLoRAStack") {
                const value = this.widgets.find(w => w.name === 'adapters');
                value.type = 'hidden'; value.computeSize = () => [0, -4]; value.draw = () => {};
                const panel = document.createElement('div'); panel.className = 'fv-panel fv-lora-panel';
                const cleanup = loraPanel(this, panel);
                this.addDOMWidget('freevideo_loras', 'freevideo_loras', panel, {serialize: false, getMinHeight: () => 180});
                const removed = this.onRemoved;
                this.onRemoved = function (...args) { cleanup(); return removed?.apply(this, args); };
                this.setSize([460, 240]);
            }
            else {
                const force = this.widgets?.find(w => w.name === 'force_regenerate');
                if (force) {
                    force.type = 'hidden'; force.computeSize = () => [0, -4]; force.draw = () => {};
                    force.value = false; force.serializeValue = () => false;
                }
                // Characters mentioned with @ travel as <Subject N> plus their definitions; the draft keeps the typed names.
                const prompt = this.widgets?.find(w => w.name === 'text');
                if (prompt) {
                    const owner = this;
                    // Replace person asks for its prefix here, where every way of running the node reads the text.
                    prompt.serializeValue = () => {
                        const body = expandCast(prompt.value, mediaRows(owner), {continued: Boolean(owner.freevideoContinues?.())}).text;
                        return swapState(mediaNodeOf(owner)) ? withPrefix(body) : body;
                    };
                }
                const quality = this.widgets?.find(w => w.name === 'two_pass');
                if (quality) {
                    quality.label = text('Two-pass acceleration', '二次采样加速');
                    quality.tooltip = text('Usually faster: generates at a lower resolution, then upscales and refines.', '通常更快：先用低分辨率生成，再放大细化。');
                }
                for (const [name, en, zh] of [['base_steps', 'First-pass steps', '一采步数'], ['refine_steps', 'Second-pass steps', '二采步数']]) {
                    const steps = this.widgets?.find(w => w.name === name);
                    if (steps) {
                        steps.label = text(en, zh);
                        steps.tooltip = text('Changing sampling steps may lower generation quality. Default: 8 first-pass and 3 second-pass steps. Two-pass acceleration runs only with 8 first-pass steps. Other step counts use a single pass. Other second-pass counts must be fewer than first-pass steps.',
                            '修改采样步数可能降低生成质量。默认一采 8 步、二采 3 步；只有一采为 8 步时才会使用二次采样加速，其他步数只采样一遍；其他二采步数需小于一采步数。');
                    }
                }
                if (cn) {
                    // Reuse product wording; leave untranslated names with no established Chinese copy.
                    for (const [name, label, tooltip] of [
                        ['width', '宽度'],
                        ['height', '高度'],
                        ['seconds', '时长（秒）'],
                        ['seed', '种子'],
                        ['force_regenerate', '重新生成', '跳过已保存结果的缓存。'],
                        ['preview', '先出预览', '先出半分辨率预览，满意后在结果里点“继续二采”，成片与直接生成一致。'],
                        ['refine_from', '继续二采', '从这个预览继续生成全分辨率视频。'],
                        ['continue_from', '接着生成'],
                        ['frames', '帧数'],
                        ['first_frame_reference', '首帧参考', '减小漂移'],
                        ['step_cache', '步间缓存', '复用相近采样步之间的计算，更快完成，结果会有细微变化。'],
                        ['upscale_factor', '放大倍数', '二次采样时成片相对首遍的放大倍数。'],
                    ]) {
                        const widget = this.widgets?.find(w => w.name === name);
                        if (widget) {
                            widget.label = label;
                            if (tooltip) widget.tooltip = tooltip;
                        }
                    }
                }
                for (const widget of this.widgets || []) {
                    // Node and creation-panel edits use the same named draft.
                    const node = this, callback = widget.callback;
                    widget.callback = function (...args) {
                        const result = callback?.apply(this, args);
                        if (!configuringGraph) rememberPromptDraft(api, node);
                        return result;
                    };
                    widget.inputEl?.addEventListener?.('input', () => { if (!configuringGraph) rememberPromptDraft(api, node); });
                }
                trackCreationDraft(this);
                resultPanel(this);
                this.addWidget('button', text('Open creative workspace', '打开创作面板'), null, () => showStudio(this), {serialize: false});
                const removed = this.onRemoved;
                this.onRemoved = function (...args) { const value = removed?.apply(this, args); requestAnimationFrame(refreshNavigation); return value; };
                requestAnimationFrame(refreshNavigation);
            }
            return result;
        };
    },
    afterConfigureGraph() {
        syncPrompts(true);
        configuringGraph = false;
        for (const node of app.graph?._nodes || []) {
            if (node.comfyClass !== 'FreeVideoGenerate' && node.type !== 'FreeVideoGenerate') continue;
            trackCreationDraft(node);
        }
        watchCreationGraph(app.graph);
        // The host activates the target workflow after afterConfigureGraph.
        requestAnimationFrame(() => watchPromptHistory(api, app));
        progressConnection.reset();
        progressConnection.refresh();
        refreshNavigation();
        const rememberedView = preferredView();
        const node = (app.graph?._nodes || []).find(isGenerate);
        // The user's own workflow is shown as it is. A FreeVideo workflow opens its workspace once the
        // launcher or the user asked for it, never on its own for a tab restored with the page. The startup
        // cover follows this same decision and is always released here when the workspace will not open.
        const opens = !!node && app.graph?.extra?.freevideo_studio && rememberedView !== 'nodes' && (launchVisit || userActed);
        if (!opens) { finishWorkspaceRestore(); if (!node) collapseWorkspaces(); return; }
        const opening = rememberedView === 'editor' ? beginWorkspaceRestore() : null, epoch = configuration;
        requestAnimationFrame(async () => {
            const launch = waitForLauncherRestore(); if (launch) await launch;
            if (epoch !== configuration) return;
            if (!opening) { showStudio(node); return; }
            try {
                const handle = await openEditorView(node, {isCurrent: () => epoch === configuration,
                    onProjectLoaded: record => opening.setName(record.name),
                    onFailure: error => health({type: 'error', where: 'editor', error})});
                if (epoch !== configuration) return;
                if (handle) { opening.cover(); await handle.ready; }
                else {
                    const studio = showStudio(node), status = studio?.querySelector?.('.fv-status');
                    if (status) {
                        const message = text('Could not complete. Try again.', '没有完成，请重试');
                        status.dataset.error = 'true'; status.textContent = message;
                        status.append(issueButton(text, () => ({where: ['Open editor', '打开剪辑'], message})));
                    }
                }
            } finally { if (epoch === configuration) opening.finish(); }
        });
    },
    onNodeOutputsUpdated(outputs) {
        for (const [id, output] of Object.entries(outputs)) {
            app.graph?.getNodeById(id)?.freevideoShowResult?.(output);
        }
    },
});

api.addEventListener('executed', event => {
    const {node: nodeId, prompt_id, output} = event.detail || {};
    const node = app.graph?.getNodeById(nodeId);
    if (prompt_id && node?.freevideoShowResult && output?.freevideo_summary?.[0]) {
        node.freevideoShowResult({...output, freevideo_summary: [{...output.freevideo_summary[0], request_id: prompt_id}]});
    }
});
api.addEventListener('execution_start', () => {
    // A workflow of the user's own is running: nothing of ours stays over it.
    if (!(app.graph?._nodes || []).some(isGenerate)) collapseWorkspaces();
});
api.addEventListener('freevideo_prewarm', event => {
    const value = event.detail;
    if (value) app.graph?.getNodeById(value.node)?.freevideoShowPrewarm?.(value);
});

installTranslateGuard({chinese: cn});
installNativeErrorGuard({api, app, describe: detail => {
    const {advice} = adviceFor(createErrorReport(detail, {stage: 'execution', graph: app.graph}), text);
    return [advice.title, advice.summary].filter(Boolean).join('\n');
}});
api.addEventListener('execution_error', event => {
    // An upstream loader/media node can stop FreeVideo before its own node is
    // entered. Retain that error even while the creative workspace is closed.
    for (const node of app.graph?._nodes || []) {
        if (!node.freevideoShowFailure) continue;
        const pending = [node], seen = new Set();
        while (pending.length) {
            const current = pending.pop();
            if (!current || seen.has(String(current.id))) continue;
            seen.add(String(current.id));
            for (const input of current.inputs || []) {
                const link = input.link != null ? node.graph?.links[input.link] : null;
                if (link) pending.push(node.graph?.getNodeById(link.origin_id));
            }
        }
        if (seen.has(String(event.detail?.node_id))) node.freevideoShowFailure(event.detail);
    }
});
api.addEventListener('executing', event => {
    const id = typeof event.detail === 'object' ? event.detail?.node : event.detail;
    if (id != null) {
        const node = app.graph?.getNodeById(id); node?.freevideoClearFailure?.();
        if (!node?.freevideoProgress) node?.freevideoShowProgress?.({label: text('Preparing video', '正在准备视频'), reset: true, new_request: true});
        progressConnection.refresh();
    } else for (const node of app.graph?._nodes || []) node.freevideoStopProgress?.();
});
