import { createErrorPanel, errorText } from './error_panel.js';
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { openStudio, loraPanel, loraWarning, promptGuide } from "./studio.js";
import { createGenerationProgress } from './generation_progress.js';
import { createProgressConnection } from './progress_connection.js';
import { notifyCompatibility } from './compatibility.js';
import { startUpdateChecks } from './updates.js';
import { installNavigation, refreshNavigation, preferredView } from './view_navigation.js';
import { attachReferencePicker, referenceItems, syncReferencePrompt } from './prompt_references.js';
import { outputDownloadURL } from './output_download.js';
import { regenerateResult } from './studio_queue.js';

const languageOverride = typeof location !== 'undefined'
    ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en'
    && String(navigator.language || '').toLowerCase().startsWith('zh'));
const text = (en, zh) => cn ? zh : en;
const style = document.createElement("style");
style.textContent = `
.fv-panel{box-sizing:border-box;width:100%;height:100%;padding:16px;background:var(--fv-card);color:var(--fv-ink);border:1px solid var(--fv-border);border-radius:var(--fv-r-md,12px);font:13px/1.6 var(--fv-font);overflow:auto;color-scheme:dark}
.fv-panel *{box-sizing:border-box}.fv-toolbar,.fv-row,.fv-links{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.fv-panel .fv-links{justify-content:center}.fv-panel[data-freevideo=result]>.fv-note{text-align:center}
.fv-panel .fv-toolbar{padding:4px 0 8px}.fv-panel .fv-stats{text-align:center}.fv-panel .fv-stat{padding:10px 3px;border-radius:var(--fv-r-md,12px);background:var(--fv-raised)}
.fv-panel .fv-stats[hidden]{display:none}
.fv-panel .fv-connected{display:flex;align-items:center;justify-content:center;gap:6px;flex-wrap:wrap;margin:10px 0}.fv-panel .fv-connection{border:1px solid var(--fv-border);border-radius:var(--fv-r-sm,8px);padding:5px 9px;background:var(--fv-selected);color:var(--fv-accent);font-size:var(--fv-micro,12px)}
.fv-panel :focus-visible{outline:2px solid var(--fv-accent);outline-offset:2px}
.fv-panel[data-freevideo=result]{display:flex;flex-direction:column;justify-content:center}.fv-panel .fv-connected-card{text-align:center;padding:28px 12px;border:1px solid var(--fv-border);border-radius:var(--fv-r-md,12px);color:var(--fv-muted);background:var(--fv-raised)}
.fv-panel button,.fv-panel select,.fv-panel a{box-sizing:border-box;min-height:var(--fv-h-sm,32px);border:1px solid var(--fv-border);border-radius:var(--fv-r-sm,8px);background:var(--fv-raised);color:var(--fv-ink);padding:5px 10px;font:inherit;font-weight:var(--fv-medium,500);cursor:pointer;text-decoration:none}
.fv-panel button:hover,.fv-panel a:hover{background:var(--fv-hover)}.fv-panel button:disabled{opacity:.45;cursor:wait}
.fv-panel select{max-width:170px}.fv-note{color:var(--fv-muted);font-size:var(--fv-micro,12px);margin:8px 0;white-space:pre-wrap}.fv-error{color:var(--fv-danger)}
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
function mediaKind(name) {
    const ext = name.split(".").pop().toLowerCase();
    if (["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"].includes(ext)) return "image";
    if (["mp4", "mov", "webm", "mkv", "m4v"].includes(ext)) return "video";
    if (["wav", "mp3", "flac", "ogg", "m4a", "aac", "opus"].includes(ext)) return "audio";
    throw new Error(text("Choose an image, video or audio file", "请选择图片、视频或音频文件"));
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
    picker.accept = "image/*,video/*,audio/*"; picker.hidden = true;
    const add = button(text("Upload files", "上传文件"), text("Upload images, video or audio", "上传图片、视频或音频"), () => picker.click());
    const audioPicker = el('input'); audioPicker.type = 'file'; audioPicker.multiple = true;
    audioPicker.accept = 'audio/*,.wav,.mp3,.flac,.ogg,.m4a,.aac,.opus'; audioPicker.hidden = true;
    const addAudio = button(text('Reference audio', '参考音频'),
        text('Add a voice, music or rhythm reference (up to 15 seconds)', '添加音色、音乐或节奏参考（最长 15 秒）'), () => audioPicker.click());
    const audioHelp = el('div', '', 'fv-note'); audioHelp.hidden = true;
    const role = el("select"); role.setAttribute("aria-label", text("Use new images as", "新图片用途"));
    for (const [value, label] of [["reference", text("Reference", "参考")], ["first", text("First frame", "首帧")], ["last", text("Last frame", "尾帧")]]) {
        const option = el("option", label); option.value = value; role.append(option);
    }
    role.value = 'reference';
    toolbar.append(add, addAudio, role, picker, audioPicker); panel.append(toolbar, mode, note, audioHelp, connections, cards);
    const read = () => {
        const rows = JSON.parse(serialized.value || "[]");
        if (!Array.isArray(rows) || rows.length > 32) throw new Error(text("Use at most 32 media items", "最多使用 32 个素材"));
        return rows;
    };
    const message = (value, error = false) => { note.textContent = value; note.classList.toggle("fv-error", error); };
    const update = rows => { serialized.value = JSON.stringify(rows); node.graph?.change(); window.dispatchEvent(new CustomEvent('freevideo-media', {detail: node.id})); };
    const changed = event => { if (String(event.detail) === String(node.id)) render(); };
    window.addEventListener('freevideo-media', changed);
    function render() {
        for (const preview of cards.querySelectorAll("video,audio")) {
            preview.pause(); preview.removeAttribute("src"); preview.load();
        }
        cards.replaceChildren();
        let rows;
        try { rows = read(); } catch (error) { message(error.message, true); return; }
        const active = rows.filter(row => row.enabled !== false);
        const linked = name => node.inputs?.find(i => i.name === name)?.link != null;
        const first = active.filter(row => row.role === "first").length + Number(linked('first'));
        const last = active.filter(row => row.role === "last").length + Number(linked('last'));
        const audioRefs = active.filter(row => row.role === 'reference' && mediaKind(row.file) === 'audio').length + Number(linked('reference_audio'));
        const refs = active.filter(row => row.role === "reference").length + Number(linked('reference')) + Number(linked('reference_audio'));
        audioHelp.hidden = !audioRefs;
        audioHelp.textContent = text('Use <Audio 1>, <Audio 2>, … in your prompt to guide the generated voice, music or rhythm. Clips: up to 15 seconds. Set accompanying images to Reference.',
            '在提示词中用 <Audio 1>、<Audio 2> 等标签引导生成的音色、音乐或节奏。片段最长 15 秒，搭配的图片请选择“参考”用途。');
        connections.replaceChildren();
        for (const [name, label] of [['first', text('First frame', '首帧')], ['last', text('Last frame', '尾帧')], ['reference', text('Reference image', '参考图像')], ['reference_audio', text('Reference audio', '参考音频')]]) {
            if (!linked(name)) continue;
            const link = node.graph?.links[node.inputs.find(i => i.name === name).link];
            const source = link && node.graph?.getNodeById(link.origin_id);
            const chip = el('span', `${label} · ${text('Connected', '已连接')}`, 'fv-connection');
            chip.title = source?.title || source?.type || label; connections.append(chip);
        }
        mode.textContent = refs ? text("Reference video · experimental", "参考生成 · 实验性") : first && last ? text("First + last frame", "首尾帧生成") : first ? text("Image to video", "图生视频") : last ? text("Last frame to video", "尾帧生成") : text("Text to video · media is optional", "文生视频 · 素材可留空");
        message(refs && (first || last) ? text("Choose keyframes or references. Disable unused cards.", "首尾帧与参考模式不能混用，请关闭本次不用的素材。") : first > 1 || last > 1 ? text("Keep only one first frame and one last frame enabled.", "首帧和尾帧各只能启用一张图片。") : text("Connect IMAGE / AUDIO outputs on the left, or drop / paste files here.", "可连接左侧 IMAGE / AUDIO 输入，或拖入、粘贴文件。"), Boolean(refs && (first || last) || first > 1 || last > 1));
        if (!rows.length && !connections.childElementCount) cards.append(el("div", text("Connect an image / audio node or upload files.\nLeave empty for text to video.", "连接图像／音频节点或上传文件。\n留空即可文生视频。"), "fv-empty"));
        if (!rows.length && connections.childElementCount) cards.append(el('div', text('Media will come from the connected nodes.\nNo upload is needed.', '素材由已连接的节点提供。\n无需上传文件。'), 'fv-connected-card'));
        rows.forEach((row, index) => {
            const card = el("div", undefined, "fv-card"); card.dataset.enabled = String(row.enabled !== false);
            const top = el("div", undefined, "fv-row");
            const enabled = el("input"); enabled.type = "checkbox"; enabled.checked = row.enabled !== false;
            enabled.setAttribute("aria-label", text("Use media", "使用素材")); enabled.onchange = () => { rows[index].enabled = enabled.checked; update(rows); };
            const label = el("span", row.file.split("/").pop(), "fv-file"); label.title = row.file;
            const select = el("select"); select.setAttribute("aria-label", text("Media role", "素材用途"));
            const kind = mediaKind(row.file);
            for (const [value, title] of (kind === "image" ? [["reference", text("Reference", "参考")], ["first", text("First frame", "首帧")], ["last", text("Last frame", "尾帧")]] : [["reference", text("Reference", "参考")]])) {
                const option = el("option", title); option.value = value; select.append(option);
            }
            select.value = row.role; select.onchange = () => { rows[index].role = select.value; update(rows); };
            top.append(enabled, label, select); card.append(top);
            const preview = el(kind === "image" ? "img" : kind); preview.className = "fv-preview";
            preview.src = viewURL(row.file);
            if (kind === "image") { preview.loading = "lazy"; preview.alt = label.textContent; }
            else { preview.controls = true; preview.preload = "metadata"; if (kind === "video") { preview.muted = true; preview.playsInline = true; } }
            card.append(preview);
            const controls = el("div", undefined, "fv-row");
            const move = delta => { const target = index + delta; if (target < 0 || target >= rows.length) return; [rows[index], rows[target]] = [rows[target], rows[index]]; update(rows); };
            controls.append(button("↑", text("Move earlier", "向前移动"), () => move(-1)), button("↓", text("Move later", "向后移动"), () => move(1)),
                button(text("Remove", "移除"), text("Remove from this request; keep the uploaded file", "从本次请求移除，保留已上传文件"), () => { rows.splice(index, 1); update(rows); }));
            card.append(controls); cards.append(card);
        });
    }
    let uploading = false;
    serialized.serializeValue = () => {
        if (node.isUploading) throw new Error(text("Wait for media uploads to finish before running", "请等待素材上传完成后再生成"));
        return serialized.value;
    };
    async function upload(files) {
        if (node.isUploading || !files.length) return;
        uploading = true; node.isUploading = true; add.disabled = true; addAudio.disabled = true;
        try {
            for (const [index, file] of Array.from(files).entries()) {
                const rows = read(); if (rows.length >= 32) throw new Error(text("Use at most 32 media items", "最多使用 32 个素材"));
                const kind = mediaKind(file.name);
                message(`${text("Uploading", "正在上传")} ${index + 1}/${files.length} · ${file.name}`);
                const body = new FormData(); body.append("file", file);
                const response = await api.fetchApi("/freevideo/media/upload", {method: "POST", body});
                const result = await response.json(); if (!response.ok) throw new Error(result.error || response.statusText);
                rows.push({file: result.file, role: kind === "image" ? role.value : "reference", enabled: true}); update(rows);
            }
        } catch (error) { message(error.message, true); }
        finally { uploading = false; node.isUploading = false; add.disabled = false; addAudio.disabled = false; picker.value = ""; audioPicker.value = ''; }
    }
    picker.onchange = () => upload(picker.files);
    audioPicker.onchange = () => upload(audioPicker.files);
    panel.ondragover = event => { event.preventDefault(); event.stopPropagation(); };
    panel.ondrop = event => { event.preventDefault(); event.stopPropagation(); upload(event.dataTransfer.files); };
    panel.onpaste = event => { if (event.clipboardData?.files.length) { event.preventDefault(); event.stopPropagation(); upload(event.clipboardData.files); } };
    const dispose = () => window.removeEventListener('freevideo-media', changed);
    if (mount) { mount.append(panel); render(); return dispose; }
    node.freevideoCreateMediaEditor = target => mediaPanel(node, target);
    node.addDOMWidget("freevideo_media", "freevideo_media", panel, {serialize: false, getMinHeight: () => 300, getMaxHeight: () => 520});
    const removed = node.onRemoved;
    node.onRemoved = function (...args) { dispose(); return removed?.apply(this, args); };
    const configure = node.onConfigure;
    node.onConfigure = function (...args) { const result = configure?.apply(this, args); render(); return result; };
    const connectionChanged = node.onConnectionsChange;
    node.onConnectionsChange = function (...args) { const result = connectionChanged?.apply(this, args); update(read()); return result; };
    node.setSize([460, 460]); render();
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
    const progress = createGenerationProgress(text, undefined, {api}); panel.append(progress.element, progress.report);
    let showingResult = false;
    node.freevideoReportProgress = message => {
        progress.updateReport(message);
        if (message.new_request) node.freevideoReportId = null;
        if (message.report_id) node.freevideoReportId = message.report_id;
    };
    node.freevideoShowProgress = message => {
        if (showingResult) {
            panel.replaceChildren(progress.element, progress.report);
            panel.title = ''; node.freevideoPrewarm = '';
            showingResult = false; warn();
        }
        const retry = message.reset ? undefined : message.retry || node.freevideoProgress?.retry;
        node.freevideoProgress = {...message, retry, received_at: message.received_at ?? Date.now()}; progress.update(node.freevideoProgress);
        if (panel.firstElementChild !== progress.element) panel.prepend(progress.element);
        if (progress.report.parentNode !== panel) panel.append(progress.report);
        node.setSize([node.size[0], Math.max(node.size[1], node.computeSize()[1])]);
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
        panel.append(failure.element); node.setSize([node.size[0], Math.max(node.size[1], node.computeSize()[1])]);
    };
    node.freevideoClearFailure = () => { node.freevideoFailure = ''; node.freevideoFailureReport = null; failure.clear(); };
    const warning = el('div', '', 'fv-note');
    const hasLoRA = () => node.inputs?.some(input => input.name === 'loras' && input.link != null);
    const warn = () => {
        warning.hidden = !hasLoRA(); warning.textContent = hasLoRA() ? loraWarning() : '';
        if (panel.lastElementChild !== warning) panel.append(warning);
    };
    panel.append(el("div", text("Video, audio and timings appear here after generation.", "生成后在这里查看视频、音频和耗时。"), "fv-note"));
    const panelHeight = () => (hasLoRA() ? 240 : 140) + (node.freevideoFailure ? 280 : 0) + (node.freevideoProgress ? 190 : 0);
    node.addDOMWidget("freevideo_result", "freevideo_result", panel, {serialize: false, getMinHeight: panelHeight, getMaxHeight: panelHeight});
    const connected = node.onConnectionsChange, configured = node.onConfigure;
    node.onConnectionsChange = function (...args) { const result = connected?.apply(this, args); warn(); queueMicrotask(syncPrompts); return result; };
    node.onConfigure = function (...args) {
        const result = configured?.apply(this, args);
        // The earlier private test put a force checkbox at this position,
        // before the public version added sampling-step widgets.
        const base = this.widgets?.find(w => w.name === 'base_steps');
        if (base && typeof base.value === 'boolean') base.value = 8;
        warn(); return result;
    };
    warn();
    node.freevideoShowPrewarm = function (value) {
        let note = panel.querySelector('.fv-prewarm');
        if (!note) { note = el('div', '', 'fv-note fv-prewarm'); panel.append(note); }
        const onGPU = Number.isFinite(value.gpu_bytes_after);
        const size = onGPU ? ` · ${(value.gpu_bytes_after / 2 ** 30).toFixed(1)} GiB` : '';
        note.textContent = value.state === 'loading' ? text('Preparing text encoder on GPU in background', '正在后台预加载文本编码器到 GPU') + size
            : value.state === 'partial' ? text('Text encoder partially prepared on GPU', '文本编码器已部分预加载到 GPU') + size
            : value.state === 'reading' ? text('Preparing encoder files', '正在预读编码器文件') + ` · ${Math.min(100, Math.floor(100 * value.done_bytes / value.bytes))}%`
            : value.state === 'ready' ? (onGPU ? text('Text encoder ready on GPU', '文本编码器已在 GPU 就绪') + size
                : text('Encoder files warmed · no VRAM reserved', '编码器文件已预热 · 不占用显存')) : '';
        node.freevideoPrewarm = note.textContent;
        window.dispatchEvent(new CustomEvent('freevideo-prewarm', {detail: {node: node.id, label: note.textContent}}));
    };
    node.freevideoShowResult = function (message) {
        const value = message?.freevideo_summary?.[0]; if (!value) return;
        showingResult = true;
        node.freevideoReportId = null; progress.report.hidden = true;
        node.freevideoStopProgress();
        node.freevideoClearFailure();
        node.freevideoLastResult = value;
        window.dispatchEvent(new CustomEvent('freevideo-result', {detail: {node: node.id, value}}));
        panel.replaceChildren(); const stats = el("div", undefined, "fv-stats");
        stats.hidden = !!value.result_cache_hit;
        const number = (value, scale, unit) => Number.isFinite(value) && value >= 0 ? `${(value / scale).toFixed(1)} ${unit}` : "—";
        for (const [label, shown] of [[text("Sampling", "采样"), number(value.sample_seconds, 1, "s")], [text("Request total", "请求总计"), number(value.request_seconds, 1, "s")], [text("VRAM peak", "显存峰值"), number(value.vram_peak_bytes, 2 ** 30, "GiB")], [text("RAM peak", "内存峰值"), number(value.ram_peak_bytes, 2 ** 30, "GiB")]]) {
            const stat = el("div", undefined, "fv-stat"); stat.append(el("strong", shown), el("span", label)); stats.append(stat);
        }
        const links = el("div", undefined, "fv-links");
        for (const [label, file] of [[text("Download video", "下载视频"), value.video], [text("Report", "报告"), value.report]]) {
            if (!file) continue;
            const link = el("a", label); link.href = outputDownloadURL(api, file); link.download = file === value.video ? '' : file.split("/").pop(); links.append(link);
        }
        links.append(el("span", value.result_cache_hit ? text("Reused previous result", "已复用上次结果") : value.conditioning_cache_hit ? text("Input cache reused", "已复用输入缓存") : text("Inputs encoded", "已编码输入"), "fv-mode"));
        if (value.result_cache_hit) {
            const again = el('button', text('Regenerate', '重新生成')); again.type = 'button';
            again.onclick = async () => {
                again.disabled = true;
                try { await node.freevideoRegenerateResult(value); again.textContent = text('Queued', '已加入队列'); }
                catch (error) { again.disabled = false; node.freevideoShowFailure({exception_message: error.message}); }
            };
            links.append(again);
        }
        panel.append(stats, links);
        warn();
        panel.title = text("VRAM: engine allocator peak. RAM: measured request processes, ", "显存为引擎分配器峰值，内存为请求进程实测，") + (value.ram_metric || "unknown");
    };
    node.freevideoRegenerateResult = async value => {
        if (node.freevideoRegenerating) return;
        node.freevideoRegenerating = true;
        try { return await regenerateResult(api, node.id, value, () => app.graphToPrompt()); }
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
function syncPrompts(reset = false) {
    if (configuringGraph && reset !== true) return;
    for (const node of app.graph?._nodes || []) if (node.type === 'FreeVideoGenerate') syncReferencePrompt(node, text, reset === true);
}
window.addEventListener('freevideo-media', () => syncPrompts());

const progressConnection = createProgressConnection(api, message => {
    const node = app.graph?.getNodeById(message.node);
    if (!node?.freevideoShowProgress) return false;
    node.freevideoReportProgress?.(message);
    if (message.result) node.freevideoShowResult({freevideo_summary: [message.result]});
    else if (['failed', 'cancelled'].includes(message.phase)) node.freevideoStopProgress();
    else node.freevideoShowProgress(message);
    window.dispatchEvent(new CustomEvent('freevideo-progress', {detail: message}));
});

app.registerExtension({
    name: "FreeVideo.UnifiedMedia",
    beforeConfigureGraph() { configuringGraph = true; },
    async setup() { progressConnection.start(); installNavigation(openStudio); startUpdateChecks(); await notifyCompatibility(); },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === 'FreeVideoReference') {
            const connected = nodeType.prototype.onConnectionsChange;
            nodeType.prototype.onConnectionsChange = function (...args) { const result = connected?.apply(this, args); queueMicrotask(syncPrompts); return result; };
            return;
        }
        if (!["FreeVideoMedia", "FreeVideoGenerate", "FreeVideoLoRAStack"].includes(nodeData.name)) return;
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
                const quality = this.widgets?.find(w => w.name === 'two_pass');
                if (quality) {
                    quality.label = text('Two-pass sampling', '二次采样');
                    quality.tooltip = text('Generate the scene, then refine it at the target resolution.', '先生成画面，再以目标分辨率精修。');
                }
                for (const [name, en, zh] of [['base_steps', 'First-pass steps', '一采步数'], ['refine_steps', 'Second-pass steps', '二采步数']]) {
                    const steps = this.widgets?.find(w => w.name === name);
                    if (steps) {
                        steps.label = text(en, zh);
                        steps.tooltip = text('Changing sampling steps may reduce generation quality. Defaults: 8 + 2. Second-pass steps must be fewer than first-pass steps.',
                            '修改采样步数可能降低生成质量。默认一采 8 步、二采 2 步；二采步数必须小于一采步数。');
                    }
                }
                resultPanel(this);
                this.addWidget('button', text('Open creative workspace', '打开创作面板'), null, () => openStudio(this), {serialize: false});
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
        progressConnection.reset();
        progressConnection.refresh();
        refreshNavigation();
        if (app.graph?.extra?.freevideo_studio && preferredView() !== 'nodes') {
            const node = app.graph._nodes.find(n => n.comfyClass === 'FreeVideoGenerate' || n.type === 'FreeVideoGenerate');
            if (node) requestAnimationFrame(() => openStudio(node));
        }
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
api.addEventListener('freevideo_prewarm', event => {
    const value = event.detail;
    if (value) app.graph?.getNodeById(value.node)?.freevideoShowPrewarm?.(value);
});

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
