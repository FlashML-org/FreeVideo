// Shared by the Studio and graph view. Text only; never render exception HTML.
import {repairAction} from './repair_hint.js';
import {detectBrowserExtension, browserExtensionAdvice, extensionSteps, isNetworkTypeError, connectionLostText} from './browser_extension.js';
import {SAMPLING_EFFORTS, STEP_CACHES, UPSCALE_FACTORS, tableLevel} from './sampling_effort.js';
import {failureIssue, failureStage} from './failure_issue.js';
import {focusReportTip, reportIssue, showReportTip} from './report_issue.js';
const reports = new WeakSet();
const captured = new WeakMap();
const diagnosticReports = new WeakSet();
const scalar = value => typeof value === 'string' ? value : typeof value === 'number' ? String(value) : '';
const list = value => Array.isArray(value) ? value : [];
const record = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};

// The engine's own messages for errors people can fix themselves.
const PLAIN_ERRORS = [
    {key: 'engine-busy', message: 'Setup, testing or generation is already using this installation/GPU lock. Retry after it finishes.',
        title: ['FreeVideo is busy with another task', 'FreeVideo 正在处理另一个任务'],
        summary: ['Try again when it finishes. If nothing else is running, close duplicate FreeVideo instances, then try again.',
            '请在它完成后重试。若没有其他任务在进行，请关闭重复运行的 FreeVideo 后重试。']},
    {key: 'continuation-missing', message: 'The previous video has no usable continuation data. Generate it again with this version of FreeVideo.',
        title: ['This video cannot be continued', '这个视频暂时不能接续'],
        summary: ['Generate the previous video again, then continue it.', '请重新生成上一段视频，再接续。']},
    {key: 'continuation-short', message: 'The previous video is too short to continue; it needs to be at least 1.7 s long.',
        title: ['The previous video is too short', '上一段视频太短'],
        summary: ['Choose a video that is at least 1.7 seconds long.', '请选择至少 1.7 秒的视频。']},
    {key: 'continuation-unreadable', message: 'The previous video cannot be read.',
        title: ['The previous video cannot be read', '无法读取上一段视频'],
        summary: ['Choose an MP4 video that plays normally.', '请选择能正常播放的 MP4 视频。']},
    {key: 'continuation-rate', message: "The previous video's frame rate is too low to continue; it needs at least 10 frames per second.",
        title: ["The previous video's frame rate is too low", '上一段视频帧率太低'],
        summary: ['Choose a video with at least 10 frames per second.', '请选择每秒至少 10 帧的视频。']},
    {key: 'continuation-size', message: 'Use the same aspect ratio as the previous video.',
        title: ['The two videos have different aspect ratios', '两段视频的画面比例不同'],
        summary: ['Choose a video size with the same aspect ratio as the previous video.', '请选择与上一段视频比例相同的画面大小。']},
    {key: 'continuation-route', message: 'Continue from video cannot be used with these settings. Continuing a video needs an NVIDIA GPU or a Mac. To make a new video, clear "Continue from video".',
        title: ['This computer can\'t continue a video', '这台电脑无法接续视频'],
        summary: ['Continuing a video needs an NVIDIA GPU or a Mac. To make a new video, clear "Continue from video".', '接续视频需要 NVIDIA 显卡或 Mac。要生成新视频，请清空“接着生成”。']},
    {key: 'continuation-passes', message: 'Continue from video cannot be used with these settings. Turn on "Two-pass acceleration" and turn off the first-pass preview.',
        title: ['Continuing needs two-pass acceleration', '接续需要二次采样加速'],
        summary: ['Turn on "Two-pass acceleration" and turn off the first-pass preview.', '请打开“二次采样加速”，并关闭先出预览。']},
    {key: 'continuation-refine', message: 'Continue from video cannot be used with these settings. Clear either "Second pass of preview" or "Continue from video".',
        title: ['A continued part is made in full', '接续会完整生成这一段'],
        summary: ['Clear either "Second pass of preview" or "Continue from video".', '请清空“继续二采”或“接着生成”其中一项。']},
    {key: 'continuation-media', message: 'Continue from video cannot be used with these settings. Connect up to 3 pictures as references, and remove video, audio and pre-encoded inputs.',
        title: ['A continued part takes picture references only', '接续段只能用图片参考'],
        summary: ['Connect up to 3 pictures as references, and remove video, audio and pre-encoded inputs.', '最多接 3 张图片作为参考图，并移除视频、音频参考和预编码输入。']},
    {key: 'media-mixed-layout', message: 'Choose keyframes or experimental references; mixed layouts are not supported',
        title: ['First and last frames cannot be used with references', '首尾帧不能和参考素材同时使用'],
        summary: ['Turn off the media cards or inputs you are not using, then generate again.', '请关闭本次不用的素材卡片或输入，再重新生成。']},
    {key: 'chain-too-long', prefixes: ['One continuous shot can have up to '],
        title: ['This shot has too many parts', '这个镜头的段数太多'],
        summary: ['Choose a shorter duration, or add the next shot in Edit.', '请缩短时长，或在剪辑视图中添加下一个镜头。']},
    {key: 'continuation-frames', message: 'Frame count must be 5 + 17n and at least 39 (39, 56, 73, 90, ...).',
        aliases: ['Frames must be 0 to use Duration, or 5 + 17n and at least 39 (for example, 39, 56, 141 or 243). The total includes 22 overlap frames when continuing.'],
        title: ['Choose a supported frame count', '请选择支持的帧数'],
        summary: ['Use 39, 56, 73, 90, or another value reached by adding 17. This includes the 22 repeated frames when continuing a video.', '请使用 39、56、73、90 等每次增加 17 的帧数；接续时，这个数包含开头重复的 22 帧。']},
    {key: 'continuation-file', message: 'Choose an existing MP4 video using its absolute path.',
        aliases: ['Continue from must be an MP4 path without ".." or a drive-relative path.'],
        title: ['Choose the previous video', '请选择上一段视频'],
        summary: ['Select an existing MP4 video generated by FreeVideo. In ComfyUI, keep it inside the output or input folder.', '请选择 FreeVideo 生成且仍存在的 MP4 视频；在 ComfyUI 中，请把它放在输出或输入文件夹内。']},
    {key: 'reference-too-short', message: 'The reference video is too short; it needs to be at least about 1 s long.',
        title: ['The reference video is too short', '参考视频太短'],
        summary: ['It needs to be at least about 1 s long. Choose a longer video.', '参考视频至少需要约 1 秒，请换一段更长的视频。']},
    {key: 'video-no-picture', message: 'No picture could be read from this video file. Choose another video.',
        title: ['This video could not be read', '无法读取这个视频'],
        summary: ['No picture could be read from this video file. Choose another video.', '无法从这个视频文件里读出画面，请换一个视频。']},
    {key: 'lora-unusable', message: 'This LoRA was not trained for the FreeVideo model and cannot be used.',
        prefixes: ['LoRA target/shape incompatible with this VDN model', 'Full-difference LoRA target/shape incompatible with this VDN model',
            'Unsupported LoRA tensor (no patches are ignored): ', 'Adapter contains no LoRA factors', 'Expected matching 2-D LoRA factors', 'Incomplete LoRA pair'],
        title: ['This LoRA cannot be used', '此 LoRA 无法使用'],
        summary: ['This LoRA is not made for the FreeVideo model. Turn it off or remove it, then generate again.', '此 LoRA 不适用于 FreeVideo 的模型。请关闭或移除后重新生成。']},
    {key: 'lora-acceleration', message: 'This acceleration LoRA was made for the original model and cannot be used. The FreeVideo model is already accelerated and does not need one.',
        title: ['This LoRA cannot be used', '此 LoRA 无法使用'],
        summary: ['The FreeVideo model is already accelerated and does not need an acceleration LoRA. Turn this LoRA off or remove it, then generate again.',
            'FreeVideo 的模型已经加速，无需加速 LoRA。请关闭或移除此 LoRA 后重新生成。']},
    {key: 'lora-int8', message: 'This LoRA changes parts of the model that the int8 version cannot merge yet, so it cannot be used for now. LoRAs that change only linear layers work.',
        title: ['This LoRA cannot be used', '此 LoRA 无法使用'],
        summary: ['The int8 version cannot merge the layers this LoRA changes yet. Turn it off or remove it, then generate again.',
            'int8 版本暂时无法合并此 LoRA 修改的层。请关闭或移除后重新生成。']},
    {key: 'fa4-update', message: 'The attention speed-up component on this computer needs an update. Run freevideo setup --fa4-guard in a terminal, then restart FreeVideo.',
        title: ['Runtime components need updating', '运行组件需要更新'],
        summary: ['The attention speed-up component on this computer needs an update. Run freevideo setup --fa4-guard in a terminal, then restart FreeVideo.', '注意力加速组件需要更新。请在终端运行 freevideo setup --fa4-guard，然后重新启动 FreeVideo。']},
    {key: 'duration-too-short', message: 'The video must be at least 1.625 s long.',
        title: ['The video is too short', '视频时长太短'],
        summary: ['The video must be at least 1.625 s long.', '视频时长不能短于 1.625 秒。']},
];

// The engine raises these exact sentences as the whole message. Only its first
// line counts (after an optional "ValueError: "), or the line right under the
// worker's "FreeVideo generation failed" header, so an input echoed inside or
// below another error, even one line of a longer prompt, never turns into this advice.
function knownError(text) {
    const lines = String(text).slice(0, 65536).split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    const index = /^FreeVideo generation failed(?: \(exit -?\d+\))?$/.test(lines[0] ?? '') ? 1 : 0;
    const line = (lines[index] ?? '').replace(/^(?:[A-Za-z_][\w.]*(?:Error|Exception)|(?:[\w.]+\.)?SamplingTableUnavailable):\s*/, '');
    if (line.startsWith('SAMPLING_TABLE_UNAVAILABLE: ')) {
        try {
            const d = JSON.parse(line.slice('SAMPLING_TABLE_UNAVAILABLE: '.length));
            if (Number.isInteger(d.base_steps) && d.base_steps >= 1 && d.base_steps <= 32
                && typeof d.two_pass === 'boolean' && Number.isInteger(d.refine_steps) && d.refine_steps >= 0 && d.refine_steps <= 31
                && Number.isSafeInteger(d.missing_bytes) && d.missing_bytes > 0 && typeof d.decimal_sizes === 'boolean'
                && Array.isArray(d.available) && d.available.every(i => Number.isInteger(i) && i >= 0 && i <= 3)
                && (d.step_cache === undefined || STEP_CACHES.includes(d.step_cache))
                && (d.scale === undefined || UPSCALE_FACTORS.includes(d.scale)))
                return {kind: 'sampling-table', base_steps: d.base_steps, two_pass: d.two_pass, refine_steps: d.refine_steps,
                    missing_bytes: d.missing_bytes, decimal_sizes: d.decimal_sizes, available: [...new Set(d.available)].sort(),
                    ...(d.step_cache ? {step_cache: d.step_cache} : {}), ...(d.scale && d.scale !== 2 ? {scale: d.scale} : {})};
        } catch { /* Malformed metadata stays in technical details. */ }
    }
    return PLAIN_ERRORS.find(error => line === error.message || error.aliases?.includes(line)
        || error.prefixes?.some(prefix => line.startsWith(prefix)))?.key ?? null;
}

// The media node's own check (comfy_assets.resolve_assets) names a file that is no longer in ComfyUI's
// input folder. Only ComfyUI's custom-validation entry counts, so typed text quoting the sentence never does.
// Returns the file's name, or null. A part of a long video carries the entry after its message (comfy_chain).
function missingMedia(detail) {
    if (reports.has(detail)) return null;   // A saved report holds no chosen input.
    let nodes = record(record(detail?.response).node_errors ?? detail?.node_errors);
    const part = /^Prompt outputs failed validation; (\{.*\})$/s.exec(typeof detail === 'string' ? detail : scalar(detail?.message));
    if (!Object.keys(nodes).length && part) {
        try { nodes = {part: JSON.parse(part[1])}; } catch { /* not the part's entry */ }
    }
    for (const node of Object.values(nodes)) for (const reason of list(node?.errors)) {
        const found = reason?.type === 'custom_validation_failed'
            && /^\w+ - Upload or select the missing media file: (.+)$/s.exec(scalar(reason.details));
        if (found) return found[1].split(/[\\/]/).filter(Boolean).at(-1) || '';
    }
    return null;
}

// When a diagnostic report exists the card hides Export report; the same advice
// then names Download report in the preview or node panel.
const DOWNLOAD_ADVICE = ['If it still fails, use Download report in the preview and submit it in a GitHub issue.',
    '仍然失败时，可点预览区的“下载报告”，提交到 GitHub issue。'];
const EXPORT_TO_DOWNLOAD = [
    ['Export the report if it still fails.', DOWNLOAD_ADVICE[0]],
    ['If it still fails, export the report and submit it in a GitHub issue.', DOWNLOAD_ADVICE[0]],
    ['仍失败时请导出报告。', DOWNLOAD_ADVICE[1]],
    ['仍然失败时，请导出报告并提交到 GitHub issue。', DOWNLOAD_ADVICE[1]],
    ['If it still fails, export the report.', DOWNLOAD_ADVICE[0]],
    ['仍然失败时，请导出报告。', DOWNLOAD_ADVICE[1]],
];

// A permission error counts as the output folder's only when the path it names is not a model or cache file.
export function outputNotWritable(text) {
    const value = String(text).slice(0, 65536);
    return /PermissionError|Permission denied|Read-only file system|\[Errno (?:13|30)\]/i.test(value)
        && !/models?[\\/]|cache|\.(?:safetensors|gguf|bin|pt)\b|manifest\.json/i.test(value);
}

export function errorText(detail) {
    if (typeof detail === 'string') return detail;
    if (reports.has(detail)) return detail.details;
    const response = record(detail?.response);
    const error = response.error ?? detail?.error;
    const rows = [scalar(error?.message) || scalar(error) || scalar(response.message)
        || scalar(detail?.exception_message) || scalar(detail?.message) || 'FreeVideo generation failed.'];
    if (scalar(detail?.node_id)) rows.unshift(`Node ${scalar(detail.node_id)} (${scalar(detail.node_type) || 'unknown'}):`);
    if (scalar(detail?.exception_type)) rows.unshift(detail.exception_type);
    if (scalar(error?.details)) rows.push(error.details);
    if (scalar(detail?.details)) rows.push(detail.details);
    for (const [id, node] of Object.entries(record(response.node_errors ?? detail?.node_errors))) {
        rows.push(`Node ${id} (${scalar(node?.class_type) || 'unknown'}):`);
        for (const reason of list(node?.errors))
            rows.push('  - ' + [scalar(reason?.message), scalar(reason?.details)].filter(Boolean).join(': '));
    }
    const trace = list(detail?.traceback).map(scalar).join('') || scalar(detail?.traceback) || scalar(detail?.stack);
    if (trace) rows.push('', trace);
    return rows.join('\n');
}

function reportRedactor(detail, context) {
    // Input values are used only to remove echoed private content. They are
    // never included in the report, nor are workflow/inputs/extra_info objects.
    const hidden = new Set(), seen = new WeakSet();
    function remember(value) {
        if (typeof value === 'string' && value) {
            hidden.add(value);
            hidden.add(JSON.stringify(value).slice(1, -1));
            hidden.add(value.replace(/\\/g, '\\\\').replace(/'/g, "\\'")
                .replace(/\r/g, '\\r').replace(/\n/g, '\\n').replace(/\t/g, '\\t'));
            for (const line of value.split(/\r?\n/)) if (line.trim()) hidden.add(line.trim());
            if (/^\s*[\[{]/.test(value)) {
                try { remember(JSON.parse(value)); } catch { /* ordinary prompt text */ }
            }
        } else if (value && typeof value === 'object' && !seen.has(value)) {
            seen.add(value);
            for (const item of Object.values(value)) remember(item);
        }
    }
    remember(detail?.current_inputs);
    for (const row of Object.values(record(context.snapshot?.output)))
        for (const input of Object.values(record(row?.inputs))) {
            // API links contain node IDs, not private input text.
            if (Array.isArray(input) && input.length === 2 && Number.isInteger(input[1])) continue;
            remember(input);
        }
    for (const node of list(context.graph?._nodes)) {
        remember(node.properties?.freevideo_prompt_versions?.original);
        remember(node.properties?.freevideo_prompt_versions?.rewritten);
        for (const widget of list(node.widgets)) remember(widget.value);
    }
    remember(context.node?.properties?.freevideo_prompt_versions?.original);
    remember(context.node?.properties?.freevideo_prompt_versions?.rewritten);
    for (const widget of list(context.node?.widgets)) remember(widget.value);
    for (const node of Object.values(record(detail?.response?.node_errors ?? detail?.node_errors)))
        for (const reason of list(node?.errors)) remember(reason?.extra_info?.received_value);
    const values = [...hidden].sort((a, b) => b.length - a.length);
    const removeInputs = value => {
        let text = scalar(value);
        for (const secret of values) {
            // Short widget values (e.g. "a") must not erase every occurrence
            // inside exception names. Still remove standalone/quoted values.
            if (secret.length >= 4) text = text.split(secret).join('<INPUT REMOVED>');
            else {
                const escaped = secret.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                text = text.replace(new RegExp(`(^|[^\\p{L}\\p{N}_])${escaped}(?=$|[^\\p{L}\\p{N}_])`, 'gu'), '$1<INPUT REMOVED>');
            }
        }
        return text;
    };
    const redact = value => {
        let text = removeInputs(value);
        text = text.replace(/\b(?:https?|wss?|ftp|file):\/\/[^\s<>"']+/gi, '<URL REMOVED>')
            .replace(/\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+/gi, '<CREDENTIAL REMOVED>')
            .replace(/\b(?:hf_|sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{8,}\b/g, '<CREDENTIAL REMOVED>')
            .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, '<CREDENTIAL REMOVED>')
            .replace(/(["']?(?:[\w-]*(?:token|password|secret|credential|api[_-]?key)|authorization|cookie|username|hostname|computer_name)["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}]+)/gi, '$1<REDACTED>')
            .replace(/(["']?(?:prompt|negative_prompt|prompt_text|text)["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')/gi, '$1<INPUT REMOVED>')
            .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, '<EMAIL REMOVED>')
            .replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, '<ADDRESS REMOVED>');
        // Keep traceback source basenames and line numbers, never drive names,
        // home directories or private media filenames. Spaces in paths count.
        text = text.replace(/(^|[^A-Za-z0-9_:/<>])(\.\/setup\.sh(?![\w\/\\-]|\.\w)|\.{1,2}\/[^\r\n"'<>;,\)\]]*|(?:[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/]|\/(?![\/\s]))[^\r\n"'<>;,\)\]]+)/gm, (_, prefix, path) => {
            if (path === './setup.sh') return prefix + path;
            const source = path.replace(/\\/g, '/').match(/\/([A-Za-z0-9_.-]+\.(?:py|js|ts|mjs))(?=[:\s]|$)/);
            return prefix + (source ? '<PATH>/' + source[1] + path.slice(path.indexOf(source[1]) + source[1].length) : '<PATH REMOVED>');
        });
        text = text.replace(/data:[^\s"'<>]+/gi, '<DATA REMOVED>');
        return text;
    };
    // Echoed input text only, for deciding what the error is about.
    redact.inputsOnly = removeInputs;
    return redact;
}

export function createErrorReport(detail, context = {}) {
    if (reports.has(detail)) return detail;
    if (captured.has(detail)) return captured.get(detail);
    const clean = reportRedactor(detail, context);
    const nodeIdentifier = value => /^\d+(?::\d+)*$/.test(scalar(value)) ? scalar(value) : clean(scalar(value));
    const response = record(detail?.response);
    const sourceNodes = record(response.node_errors ?? detail?.node_errors);
    const nodes = Object.entries(sourceNodes).map(([id, node]) => ({
        id: nodeIdentifier(id), type: clean(scalar(node?.class_type)),
        errors: list(node?.errors).map(reason => ({
            type: clean(scalar(reason?.type)), message: clean(scalar(reason?.message)),
            details: clean(scalar(reason?.details)), input: clean(scalar(reason?.extra_info?.input_name)),
        })),
    }));
    const stage = context.stage || (Object.keys(response).length ? 'submission' : 'execution');
    const nodeId = scalar(detail?.node_id) || (stage === 'submission' ? scalar(context.node?.id) : '');
    const graphNode = context.graph?.getNodeById?.(nodeId);
    const nodeType = scalar(detail?.node_type) || scalar(graphNode?.comfyClass || graphNode?.type);
    if (!nodes.length && nodeId) nodes.push({id: nodeIdentifier(nodeId), type: clean(nodeType), errors: []});
    const inputs = context.snapshot?.output?.[context.node?.id]?.inputs
        || (nodeType === 'FreeVideoGenerate' ? detail?.current_inputs : null);
    const settings = {};
    for (const key of ['width', 'height', 'seconds', 'seed', 'two_pass', 'base_steps', 'refine_steps']) {
        const value = Array.isArray(inputs?.[key]) && inputs[key].length === 1 ? inputs[key][0] : inputs?.[key];
        if (typeof value === 'boolean' || typeof value === 'number' && Number.isFinite(value)) settings[key] = value;
    }
    const cache = Array.isArray(inputs?.step_cache) && inputs.step_cache.length === 1 ? inputs.step_cache[0] : inputs?.step_cache;
    if (typeof cache === 'string' && (cache === 'none' || STEP_CACHES.includes(cache))) settings.step_cache = cache;
    const factor = Array.isArray(inputs?.upscale_factor) && inputs.upscale_factor.length === 1 ? inputs.upscale_factor[0] : inputs?.upscale_factor;
    if (typeof factor === 'string' && UPSCALE_FACTORS.map(String).includes(factor)) settings.upscale_factor = factor;
    const progress = {};
    for (const key of ['phase', 'stage']) if (typeof context.progress?.[key] === 'string') progress[key] = clean(context.progress[key]);
    for (const key of ['done', 'total']) if (Number.isFinite(context.progress?.[key])) progress[key] = context.progress[key];
    const message = scalar(response.error?.message) || scalar(response.error) || scalar(response.message)
        || scalar(detail?.exception_message) || scalar(detail?.message) || scalar(detail) || 'FreeVideo generation failed.';
    // The missing file's name is not kept: the report never holds typed or chosen input.
    const known = knownError(message) ?? (missingMedia(detail) === null ? null : 'media-missing');
    const kind = failureAdvice(errorText(detail), (en) => en, known).kind;
    const found = detectBrowserExtension(detail, {kind, known, scrub: clean.inputsOnly});
    // A script name the report would redact is not shown or saved either.
    const extension = found && {...found, script: found.script && clean(found.script) === found.script ? found.script : null};
    const report = {
        schema: 'freevideo.error-report', schema_version: 1, created_at: new Date().toISOString(),
        stage, message: clean(message),
        // Chosen from the original message before redaction removes any input
        // text from it, so a card shown again from this report keeps its advice.
        known_error: known,
        browser_extension: extension,
        error_details: clean(scalar(response.error?.details) || scalar(detail?.details)),
        exception_type: clean(scalar(detail?.exception_type) || scalar(response.error?.type) || scalar(detail?.name)),
        http_status: Number.isInteger(detail?.status) ? detail.status : null,
        nodes, settings, progress, details: clean(errorText(detail)),
        privacy: {local_only: true, workflow_and_inputs_included: false,
            media_and_logs_included: false,
            redacted: ['known input text', 'absolute paths', 'URLs', 'email addresses', 'common credentials'],
            note: 'Review the report before sharing. It contains the reported error, not a full installation diagnostic bundle.'},
    };
    reports.add(report);
    const failedNode = graphNode || (String(context.node?.id) === nodeId ? context.node : null);
    const reportId = failedNode?.freevideoReportId || context.progress?.report_id;
    if (stage === 'execution' && nodeType === 'FreeVideoGenerate' && /^[a-f0-9]{32}$/.test(reportId || ''))
        diagnosticReports.add(report);
    if (detail && typeof detail === 'object') captured.set(detail, report);
    return report;
}

// Match explicit failure signatures, not a guessed GPU/RAM capacity. Keep the
// original error separately so an explanation never replaces diagnostic data.
// known: a report's known_error. Pass null when the report has none, so an
// echoed sentence in its redacted text is not taken for the engine's message.
export function failureAdvice(value, t, known = knownError(value)) {
    const text = String(value).slice(0, 65536);
    const row = (kind, title, summary, action) => ({kind, title: t(...title), summary: t(...summary), action: t(...action)});
    if (known?.kind === 'sampling-table') {
        // Names and plan matching follow sampling_effort.js; metadata carries no user text.
        const levels = SAMPLING_EFFORTS.map(tier => [tier.name, tier.zh]);
        const level = tableLevel(known.base_steps, known.two_pass, known.refine_steps, known.step_cache ?? null, known.scale ?? 2);
        const index = SAMPLING_EFFORTS.findIndex(tier => tier.name === level?.name);
        const custom = index < 0;
        // Tables follow the step count: a level with the same steps and passes (Light and Medium) needs the same download.
        const available = known.available.filter(i => SAMPLING_EFFORTS[i]?.steps !== known.base_steps || SAMPLING_EFFORTS[i].twoPass !== known.two_pass);
        // Iris: a level name and its 档 never break apart. The summary is set as text,
        // so word joiners (U+2060) keep each group on one line instead of markup.
        const keep = value => [...value].join('\u2060');
        const join = language => {
            const names = available.map(i => language ? `“${levels[i][1]}”` : levels[i][0]);
            if (language) names[names.length - 1] += '档';
            const kept = language ? names.map(keep) : names;
            return kept.length < 2 ? kept[0] || '' : kept.slice(0, -1).join(language ? '、' : ', ') + (language ? '或' : ' or ') + kept.at(-1);
        };
        const bytes = known.missing_bytes;
        // As launcher_copy.size_text: one decimal for GiB, whole MiB; decimal units on macOS.
        let size = bytes >= 2**30 ? (bytes / 2**30).toFixed(1) + ' GiB' : Math.floor(bytes / 2**20) + ' MiB';
        if (known.decimal_sizes) {
            const unit = [[1e12, 'TB', 2], [1e9, 'GB', 2], [1e6, 'MB', 1]].find(([scale]) => bytes >= scale);
            size = unit ? Number((bytes / unit[0]).toFixed(unit[2])) + ' ' + unit[1] : Math.round(bytes / 1e3) + ' KB';
        }
        // A custom step count is not a quality level: no "档" / "quality level" for it.
        const title = custom
            ? [`Custom (${known.base_steps} steps) is not downloaded yet`, `“自定义（${known.base_steps} 步）”还没下载`]
            : [`The ${levels[index][0]} quality level is not downloaded yet`, `${keep(`“${levels[index][1]}”档`)}还没下载`];
        const what = custom ? ['This step count', '这个步数'] : ['This level', '这一档'];
        return {...row('sampling-table', title,
            [`${what[0]} needs a download (about ${size}) that did not finish this time. Generate again when online and it downloads automatically.${available.length ? ` You can also use ${join(0)} for now.` : ''}`,
             `${what[1]}要先下载（约 ${size}），这次没能下载。联网后再生成一次，会自动下载。${available.length ? `也可以先用${join(1)}。` : ''}`], ['', '']), plain: true};
    }
    // ComfyUI and worker tracebacks carry English exceptions. Translate these
    // actionable failures through the existing signature/advice path.
    for (const {key, title, summary} of PLAIN_ERRORS) {
        if (key === known)
            return {...row(key, title, summary, ['', '']), plain: true};
    }
    if (known === 'media-missing')
        return {...row('media-missing', ['A media file is missing', '找不到素材'],
            ['Upload or select the missing media file again, then generate.', '请重新上传或选择缺少的素材后再生成。'], ['', '']), plain: true};
    if (/Prompt outputs failed validation|Required input is missing|Value not in list|Return type mismatch|Failed to validate prompt|value (?:\S+ )?(?:smaller|bigger) than/i.test(text))
        return row('validation', ['Check generation settings', '检查生成设置'],
            ['The generation settings did not pass validation; see the error details below.', '生成设置未通过检查，可查看下方错误详情。'],
            ['Check the named inputs, connections and model choices. Copy the details or export the error report when asking for help.', '检查下方指出的输入、节点连接和模型选项；反馈时可复制详情或导出错误报告。']);
    if (/FreeVideo resource planning could not admit/i.test(text)) {
        // The planner names smaller requests it admitted on this GPU just now.
        const fits = [...(/Fits now: ([^\n]+?)\.?(?:\n|$)/.exec(text)?.[1] || '').matchAll(/(\d+)x(\d+) (up to|at) ([\d.]+) s/g)]
            .map(([, width, height, kind, seconds]) => ({width, height, longest: kind === 'up to', seconds: Number(seconds)}));
        if (fits.length) {
            const en = fits.map(f => f.longest ? `${f.width}\u00a0×\u00a0${f.height} up to ${f.seconds.toFixed(1)}\u00a0s` : `${f.width}\u00a0×\u00a0${f.height} at ${f.seconds.toFixed(1)}\u00a0s`).join(', or ');
            const zh = fits.map(f => f.longest ? `${f.width}\u00a0×\u00a0${f.height} 最长 ${f.seconds.toFixed(1)}\u00a0秒` : `${f.width}\u00a0×\u00a0${f.height} 生成 ${f.seconds.toFixed(1)}\u00a0秒`).join('，或 ');
            return row('resources', ['Not enough GPU memory', '显存不够'],
                ['Generation needs more GPU memory than is currently available.', '生成所需显存超过当前可用量。'],
                [`This computer can make ${en} right now. Change the duration or resolution and generate again; closing other programs that use the GPU also frees memory.`,
                 `现在可以生成：${zh}。请调整时长或清晰度后重新生成；关闭其他占用显卡的程序也可以腾出显存。`]);
        }
        return row('resources', ['The request exceeds available memory', '当前任务的可用内存不足'],
            ['Generation did not start because there is not enough available RAM or GPU memory.', '可用内存或显存不足，本次生成未开始。'],
            ['Close memory-heavy applications, or lower the resolution or duration, then retry.', '可关闭占用内存较多的程序，或调低分辨率、缩短时长后重试。']);
    }
    if (/commit headroom exhausted|paging file is too small|WinError 1455|os error 1455/i.test(text))
        return row('commit', ['Not enough Windows memory', 'Windows 内存不足'],
            ['Windows cannot provide enough memory for generation right now.', 'Windows 暂时无法提供生成所需的内存。'],
            ['Close memory-heavy applications and retry. In Windows virtual memory settings, use a system-managed paging file on a drive with free space.', '关闭占用内存较多的程序后重试。在 Windows 虚拟内存设置中，使用系统管理的分页文件，并确保所在磁盘有空闲空间。']);
    // Refused before it starts: more than this Mac can allocate at all, so closing
    // other applications cannot help (macos_generate.request_admission).
    if (/needs at least [\d.]+ GiB of unified memory for .*this Mac can allocate at most/i.test(text))
        return row('unified-memory-limit', ['Not enough unified memory', '统一内存不足'],
            ['These settings need more memory than the device can provide.', '这组设置需要的内存超过设备上限。'],
            ['Choose a lower resolution or a shorter duration and retry.', '请降低分辨率或缩短时长后重试。']);
    if (/MPS backend out of memory|MPS (?:generation|encoding|decoding) reached|No unified-memory allowance|Native generation needs at least .* unified memory/i.test(text))
        return row('unified-memory', ['Not enough unified memory', '统一内存不足'],
            ['Generation needed more memory than was available.', '生成所需内存超过当时可用量。'],
            ['Close memory-heavy applications, or lower the resolution or duration, then retry. Export the report if it still fails.', '可关闭占用内存较多的程序，或调低分辨率、缩短时长后重试。仍然失败时，请导出报告。']);
    if (/CUDA out of memory|torch\.OutOfMemoryError|cudaErrorMemoryAllocation/i.test(text))
        return row('vram', ['GPU memory unavailable', '显存暂不可用'],
            ['', ''],
            ['Close other programs using the GPU, then retry.', '关闭其他占用显卡的程序后重试。']);
    const ramReasons = /RAM guard:\s*([a-z_, ]+)\./i.exec(text)?.[1] || '';
    if (ramReasons.includes('system_or_commit_pressure') && !ramReasons.includes('working_memory_budget'))
        return row('ram', ['System memory headroom is too low', '系统可用内存不足'],
            ['Generation stopped because available memory was too low.', '可用内存不足，生成已停止。'],
            ['Close memory-heavy applications and duplicate FreeVideo/ComfyUI instances, then retry. Export the report if it still fails.', '关闭占用内存较多的程序及重复运行的 FreeVideo／ComfyUI 后重试。仍失败时请导出报告。']);
    if (ramReasons.includes('working_memory_budget') && !ramReasons.includes('system_or_commit_pressure'))
        return row('ram', ['Memory limit reached', '已达到内存上限'],
            ['This generation exceeded the configured memory limit.', '本次生成超过了设置的内存上限。'],
            ['Check the memory limit in Settings, or switch back to automatic. Export the report if it still fails.', '请在设置里检查内存上限，或改回自动。仍然失败时，请导出报告。']);
    if (/RAM guard:|crossed its RAM budget|Insufficient currently available memory/i.test(text))
        return row('ram', ['Memory is low or its limit was reached', '内存不足或已达到上限'],
            ['Generation stopped because memory was low or its configured limit was reached.', '内存不足或已达到设置的上限，生成已停止。'],
            ['Close memory-heavy applications and retry. If you set a memory limit, remove or raise it. Copy the details below if it still fails.', '关闭占用内存较多的程序后重试；设置过内存上限的，请取消或调高。仍然失败时，可复制下方详情。']);
    if (outputNotWritable(text))
        return row('output-folder', ['The output folder cannot be written to', '输出目录不可写'],
            ['The video could not be saved.', '视频没能保存。'],
            ['Check that the output folder can be written to, then retry.', '请检查输出文件夹的写入权限后重试。']);
    if (/ENCODER_MODEL_MISSING:|Model in folder ['"]text_encoders['"] with filename ['"][^\r\n]+?['"] not found\./i.test(text)
            || /manifest\.json/i.test(text) && /No such file|FileNotFoundError|\[Errno 2\]/i.test(text))
        return row('model-files', ['The model is not fully installed', '模型没有装全'],
            ['Some of the model files FreeVideo needs are missing.', 'FreeVideo 需要的部分模型文件缺失。'],
            repairAction());
    if (/No space left on device|WinError 112|disk (?:is )?full/i.test(text))
        return row('disk', ['Disk space is insufficient', '磁盘空间不足'],
            ['FreeVideo could not write a required file.', 'FreeVideo 无法写入所需文件。'],
            ['Free space on the installation/output drive and retry. Keep model downloads and retained outputs if you want to resume or inspect them.', '清理安装目录或输出目录所在磁盘的空间后重试；保留模型下载和输出文件，便于续传或排查。']);
    if (/Model download paused|unexpected-transfer-size|ConnectionError|ConnectTimeout|ReadTimeout|Every download source failed|All Git sources failed|All package sources failed|curl-28/i.test(text))
        return row('download', ['Download needs attention', '下载未完成'],
            ['A transfer failed or its received data did not match the expected file.', '传输中断，或收到的数据与预期文件不一致。'],
            ['Choose another download source in Settings and retry; download progress is kept.', '可在设置中切换下载源后重试，下载进度会保留。']);
    if (/ModuleNotFoundError|No module named|DLL load failed/i.test(text))
        return row('dependencies', ['A runtime component could not load', '运行组件无法加载'],
            ['A component needed to run FreeVideo could not load.', 'FreeVideo 所需的运行组件未能加载。'],
            ['Select "Repair" in the launcher under Settings > Environment. If it still fails, export the report and submit it in a GitHub issue.', '可在启动器“设置 → 环境”里点“修复”。仍然失败时，请导出报告并提交到 GitHub issue。']);
    return row('unknown', ['Generation stopped', '生成已停止'],
        ['', ''], ['', '']);
}

let activeDialog = null;
function showFailureDialog(report, t) {
    // Both graph and Studio receive execution_error. Show one modal for that
    // event, above the Studio modal if it is open, with keyboard dismissal.
    const identity = report.stage + '\n' + report.details;
    if (activeDialog?.open && activeDialog.freevideoError === identity) return;
    activeDialog?.close();
    const dialog = document.createElement('dialog'); dialog.className = 'fv-failure-dialog';
    dialog.freevideoError = identity;
    dialog.setAttribute('aria-label', t('FreeVideo error', 'FreeVideo 错误'));
    const panel = createErrorPanel(t); panel.show(report, false);
    // The dialog has no run's report to download; submitting stays on the failure card.
    if (diagnosticReports.has(report)) panel.element.querySelector('.fv-failure-github:not([data-lora])')?.remove();
    const close = document.createElement('button'); close.type = 'button';
    close.textContent = t('Got it', '知道了'); close.autofocus = true;
    close.onclick = () => dialog.close();
    if (panel.element.freevideoKind === 'browser-extension') {
        // The steps end with a reload: that is the main button. Copy and export
        // sit at the left of the same row, so a long title keeps its own line.
        const footer = document.createElement('div'); footer.className = 'fv-failure-footer';
        const actions = panel.element.querySelector('.fv-failure-actions');
        actions.querySelector('.fv-failure-reload')?.remove();
        const reload = document.createElement('button'); reload.type = 'button'; reload.className = 'fv-failure-primary';
        reload.textContent = t('Reload page', '刷新页面'); reload.autofocus = true; reload.onclick = () => globalThis.location?.reload();
        close.textContent = t('Close', '关闭'); close.autofocus = false;
        footer.append(actions, close, reload);
        dialog.append(panel.element, footer);
    } else dialog.append(panel.element, close);
    dialog.onclose = () => { if (activeDialog === dialog) activeDialog = null; dialog.remove(); };
    document.body.append(dialog);
    if (typeof dialog.showModal !== 'function') { dialog.remove(); return; }
    activeDialog = dialog; dialog.showModal();
}

// PyTorch ends its MPS out-of-memory message by suggesting a watermark setting
// that may make the system fail; the card advises otherwise. Only the shown
// cause drops it: the technical details and the report keep the original text.
const shownCause = text => text.replace(/\s*Use PYTORCH_MPS_HIGH_WATERMARK_RATIO=[\d.]+ to disable upper limit for memory allocations(?: \(may cause system failure\))?\.?/g, '');

/** The written title and explanation for a report, and whether it gets the packaged layout (not a rejected input). */
export function adviceFor(report, t, value) {
    const advice = report.browser_extension
        ? browserExtensionAdvice(report.browser_extension, t)
        : failureAdvice(report.details, t, report.known_error ?? null);
    // A dropped connection reads as one sentence; the browser's own wording stays in the details.
    if (advice.kind === 'unknown' && isNetworkTypeError(value) && ['submission', 'queue'].includes(report.stage)) {
        Object.assign(advice, {kind: 'connection', title: t('Lost the connection to FreeVideo', '与 FreeVideo 的连接已断开'), summary: connectionLostText(t), action: ''});
    }
    if (report.stage === 'submission' && advice.kind === 'unknown') {
        advice.title = t('The task could not be submitted', '任务提交失败');
        advice.summary = '';
    }
    if (report.stage === 'queue' && advice.kind === 'unknown') {
        advice.title = t('The queue operation did not complete', '队列操作未完成');
        advice.summary = t('Check that ComfyUI is still running and connected, then retry.', '请确认 ComfyUI 仍在运行、连接正常后重试。');
    }
    // The name comes from the error as it arrived; a card shown again from its report says it without one.
    const media = advice.kind === 'media-missing' ? missingMedia(value) : null;
    if (media) advice.summary = t(`"${media}" could not be found. Upload or select it again, then generate.`, `素材“${media}”已不存在，请重新上传或选择后再生成。`);
    // Every failure except a rejected input (which the user fixes in place) has one layout: written title,
    // Export report / Submit to GitHub, the raw error only inside the technical details.
    const generated = !['validation', 'media-missing'].includes(advice.kind);
    // A generation that stopped without a known cause is named by the stage it stopped in.
    if (generated && advice.kind === 'unknown' && report.stage === 'execution') {
        const stage = failureStage(report);
        advice.title = stage ? t(`Generation did not finish: ${stage[0]}`, `生成未完成：${stage[1]}`) : t('Generation did not finish', '生成未完成');
    }
    return {advice, generated};
}

// route: where a generation's diagnostic report comes from ({download, fetch, version, has}); without it the
// button opens the feedback form.
export function createErrorPanel(t, route = null) {
    if (!document.getElementById('freevideo-error-style')) {
        const style = document.createElement('style'); style.id = 'freevideo-error-style';
        style.textContent = `.fv-failure{border:1px solid var(--fv-danger,#dc7c7c);border-radius:var(--fv-r-md,12px);padding:12px 14px;margin:10px 0;text-align:left;min-width:0}
.fv-failure[hidden],.fv-failure button[hidden]{display:none!important}.fv-failure-head{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:8px}.fv-failure-actions{display:flex;flex-wrap:wrap;gap:8px}
.fv-failure textarea{box-sizing:border-box;width:100%;height:180px;min-height:100px;resize:vertical;border:1px solid var(--fv-border,#45454a);border-radius:var(--fv-r-sm,8px);background:var(--fv-bg,#202126);color:var(--fv-ink,#eee);font:12px/1.5 monospace;padding:9px;white-space:pre-wrap}
.fv-failure button,.fv-failure-dialog>button{min-height:var(--fv-h-sm,32px);border:1px solid var(--fv-border,#45454a);border-radius:var(--fv-r-sm,8px);background:var(--fv-raised,#303138);color:var(--fv-ink,#eee);padding:5px 12px;font:inherit;font-weight:var(--fv-medium,500);cursor:pointer}
.fv-failure p{font-size:14px;line-height:1.6;margin:8px 0}.fv-failure summary{cursor:pointer;margin-top:12px}.fv-failure .fv-failure-cause{white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto}.fv-failure .fv-failure-privacy{font-size:12px;color:var(--fv-muted,#bbb)}
.fv-failure code{display:inline-block;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:inherit;white-space:nowrap;background:var(--fv-raised);border-radius:var(--fv-r-xs,3px);padding:0 3px}
.fv-failure-dialog{box-sizing:border-box;font-size:14px;width:min(560px,94vw);max-height:88vh;overflow:auto;padding:22px 24px;border:1px solid var(--fv-border,#45454a);border-radius:var(--fv-r-lg,16px);background:var(--fv-bg,#202126);color:var(--fv-ink,#eee);box-shadow:0 24px 80px #0008}
.fv-failure-dialog::backdrop{background:#070a0db8;backdrop-filter:blur(6px)}.fv-failure-dialog>.fv-failure{border:0;margin:0 0 16px;padding:0}.fv-failure-dialog>button,.fv-failure-dialog .fv-failure-primary{display:block;margin-left:auto;background:var(--fv-accent,#6db8fa);border-color:var(--fv-accent,#6db8fa);color:var(--fv-bg,#111720);font-weight:var(--fv-semibold,600)}
.fv-failure .fv-extension-steps{font-size:14px;line-height:1.6}.fv-failure-reload{order:-1}.fv-failure .fv-failure-copy{margin-top:8px}.fv-failure-footer{display:flex;flex-wrap:wrap;align-items:center;gap:8px}.fv-failure-footer>.fv-failure-actions{margin-right:auto}
.fv-failure-footer button{min-height:var(--fv-h-sm,32px);border:1px solid var(--fv-border,#45454a);border-radius:var(--fv-r-sm,8px);background:var(--fv-raised,#303138);color:var(--fv-ink,#eee);padding:5px 12px;font:inherit;font-weight:var(--fv-medium,500);cursor:pointer}.fv-failure-footer .fv-failure-primary{margin-left:0}`;
        document.head.append(style);
    }
    const element = document.createElement('section'); element.className = 'fv-failure'; element.hidden = true;
    const head = document.createElement('div'); head.className = 'fv-failure-head';
    const title = document.createElement('strong'); title.textContent = t('Generation failed', '生成失败');
    const copy = document.createElement('button'); copy.type = 'button'; copy.textContent = t('Copy error', '复制错误');
    const download = document.createElement('button'); download.type = 'button'; download.textContent = t('Export report', '导出报告');
    // A generation that stopped offers two buttons: the report file, or the same facts as a GitHub issue.
    const github = document.createElement('button'); github.type = 'button'; github.className = 'fv-failure-github';
    github.textContent = t('Report a problem', '提交问题');
    // Shown only for a browser extension, whose steps end with a reload.
    const reload = document.createElement('button'); reload.type = 'button'; reload.className = 'fv-failure-reload';
    reload.textContent = t('Reload page', '刷新页面'); reload.hidden = true; reload.onclick = () => globalThis.location?.reload();
    const actions = document.createElement('div'); actions.className = 'fv-failure-actions';
    // Last in the DOM, first on screen (CSS order), so copy stays the first child.
    actions.append(copy, download, reload, github);
    const explanation = document.createElement('p'), action = document.createElement('p');
    const location = document.createElement('p'), cause = document.createElement('p'); cause.className = 'fv-failure-cause';
    location.className = 'fv-failure-location';
    const privacy = document.createElement('p'); privacy.className = 'fv-failure-privacy';
    privacy.textContent = t('Paths and typed text are removed.', '已去掉路径和输入的文字。');
    const privacyInside = document.createElement('p'); privacyInside.className = 'fv-failure-privacy'; privacyInside.hidden = true;
    privacyInside.textContent = t('Paths and typed text are removed.', '已去掉路径和输入的文字。');
    const downloadStatus = document.createElement('p'); downloadStatus.setAttribute('role', 'status');
    let currentReport = null, githubLora = false;
    const disclosure = document.createElement('details'), summary = document.createElement('summary');
    summary.textContent = t('Technical details', '技术详情');
    const detailLocation = document.createElement('p'); detailLocation.className = 'fv-failure-location'; detailLocation.hidden = true;
    const detailCause = document.createElement('p'); detailCause.className = 'fv-failure-cause'; detailCause.hidden = true;
    const details = document.createElement('textarea'); details.readOnly = true;
    details.setAttribute('aria-label', t('Error details', '错误详情')); details.spellcheck = false;
    const copyDetails = async button => {
        let copied = false;
        try { await navigator.clipboard.writeText(details.value); copied = true; } catch {
            // Remote ComfyUI commonly uses plain HTTP, where Clipboard API is
            // unavailable. Keep selectable text even if the browser denies copy.
            disclosure.open = true; details.focus(); details.select();
            try { copied = document.execCommand('copy'); } catch { /* manual Ctrl+C */ }
        }
        button.textContent = copied ? t('Copied', '已复制') : t('Press Ctrl+C', '请按 Ctrl+C');
    };
    copy.onclick = () => copyDetails(copy);
    // A generation failure keeps the raw text inside the collapsed technical details, with its own copy button.
    const copyInside = document.createElement('button'); copyInside.type = 'button'; copyInside.className = 'fv-failure-copy';
    copyInside.hidden = true; copyInside.onclick = () => copyDetails(copyInside);
    // The issue address needs two browser-only modules. They load when the card is shown, so the click opens the
    // page at once (a page opened after waiting can be blocked as a pop-up).
    let issueModules = null;
    const loadIssueModules = () => import('./feedback.js').then(feedback => import('./updates.js').then(updates => { issueModules = {feedback, updates}; })).catch(() => {});
    github.onclick = async () => {
        if (!currentReport) return;
        // A generation with a diagnostic report goes the report route: save it, then the tip opens the form.
        if (route?.has() && diagnosticReports.has(currentReport) && !githubLora) {
            if (focusReportTip(head)) return;
            try { await route.download(); } catch { return; }
            showReportTip(head, github, t, {mode: 'downloaded', submit: () => reportIssue(route.fetch, route.version(), {save: false})});
            return;
        }
        if (!issueModules) await loadIssueModules();
        if (!issueModules) return;
        const draft = failureIssue(currentReport, element.freevideoKind, {lora: githubLora, t});
        const url = issueModules.feedback.feedbackIssueURL(draft.text, issueModules.updates.productVersion(), draft.summary, draft.title);
        globalThis.open?.(url, '_blank', 'noopener');
    };
    download.onclick = () => {
        if (!currentReport) return;
        let url, link;
        try {
            const payload = JSON.stringify(currentReport, null, 2) + '\n';
            url = URL.createObjectURL(new Blob([payload], {type: 'application/json;charset=utf-8'}));
            link = document.createElement('a'); link.href = url;
            link.download = 'freevideo-error-' + currentReport.created_at.replace(/[:.]/g, '-') + '.json';
            document.body.append(link); link.click();
            downloadStatus.textContent = t('Error summary download started.', '已开始下载错误摘要。');
        } catch {
            // Keep the same redacted JSON available even if downloads are
            // disabled by the browser or the page is served over plain HTTP.
            disclosure.open = true; details.value = JSON.stringify(currentReport, null, 2);
            details.focus(); details.select();
            downloadStatus.textContent = t('The download did not finish; select "Copy error" to copy the report below.', '下载未完成，可点“复制错误”复制下方报告。');
        } finally {
            link?.remove();
            if (url) setTimeout(() => URL.revokeObjectURL(url), 30000);
        }
    };
    disclosure.append(summary, detailLocation, detailCause, details, copyInside, privacyInside);
    head.append(title, actions); element.append(head, explanation, location, cause, action, disclosure, privacy, downloadStatus);
    return {element, show(value, popup = true, context = {}) {
        const report = createErrorReport(value, context); currentReport = report;
        const {advice, generated} = adviceFor(report, t, value);
        title.textContent = advice.title; explanation.textContent = advice.summary; explanation.hidden = !advice.summary; action.textContent = advice.action;
        element.querySelector('.fv-extension-steps')?.remove();
        if (advice.kind === 'browser-extension') action.after(extensionSteps(advice));
        element.freevideoKind = advice.kind; reload.hidden = advice.kind !== 'browser-extension';
        if (advice.kind === 'fa4-update') {
            const command = 'freevideo setup --fa4-guard';
            const [before, after] = advice.summary.split(command);
            const code = document.createElement('code'); code.textContent = command;
            explanation.replaceChildren(document.createTextNode(before), code, document.createTextNode(after));
        }
        action.hidden = !advice.action;
        const stage = report.stage === 'submission' ? t('Submitting task', '提交任务')
            : report.stage === 'queue' ? t('Queue operation', '队列操作') : t('Generating', '正在生成');
        location.textContent = stage + (report.http_status ? ` · HTTP ${report.http_status}` : '')
            + report.nodes.map(node => ` · ${node.type || t('Node', '节点')} #${node.id}`).join('');
        const reasons = report.nodes.flatMap(node => node.errors.map(error =>
            `${node.type || t('Node', '节点')} #${node.id}${error.input ? ' · ' + error.input : ''}: ${error.message}${error.details ? ': ' + error.details : ''}`));
        const causeText = shownCause(reasons.length ? reasons.join('\n') : [report.message, report.error_details].filter(Boolean).join('\n'));
        const internal = advice.kind === 'unknown' && !['submission', 'queue'].includes(report.stage);
        // A missing file is said in the explanation; where it happened and ComfyUI's wording stay in the technical details.
        const causeInDetails = generated || advice.kind === 'media-missing';
        location.hidden = causeInDetails; detailLocation.hidden = !causeInDetails;
        detailLocation.textContent = causeInDetails ? location.textContent : '';
        if (causeInDetails) location.textContent = '';
        cause.hidden = causeInDetails; detailCause.hidden = !causeInDetails;
        cause.textContent = causeInDetails ? '' : causeText;
        detailCause.textContent = causeInDetails ? causeText : '';
        details.value = report.details; disclosure.open = false; element.hidden = false;
        downloadStatus.textContent = '';
        download.hidden = diagnosticReports.has(report);
        if (internal && download.hidden)
            action.textContent = t('Use Download report in the preview and submit it in a GitHub issue.',
                '请点预览区的“下载报告”，提交到 GitHub issue。');
        else if (download.hidden) {
            // Advice that ends with "export the report" points at the report this card does offer.
            for (const [from, to] of EXPORT_TO_DOWNLOAD)
                if (action.textContent.includes(from)) action.textContent = action.textContent.replace(from, to);
        }
        action.hidden = !action.textContent;
        privacy.textContent = download.hidden
            ? t('Select "Download report" in the preview to save the full diagnostic information.', '可在预览区点“下载报告”保存完整诊断信息。')
            : t('Paths and typed text are removed.', '已去掉路径和输入的文字。');
        copy.textContent = advice.kind === 'sampling-table' ? t('Copy details', '复制详情') : t('Copy error', '复制错误');
        // A generation failure shows two buttons; copying moves into the technical details.
        copy.hidden = generated; copyInside.hidden = !generated; copyInside.textContent = copy.textContent;
        // For a generation failure the note on removed text sits in the technical details, beside the copy button.
        privacy.hidden = generated; privacyInside.hidden = !generated;
        const lora = ['lora-unusable', 'lora-acceleration', 'lora-int8'].includes(advice.kind);
        // An int8 limit is not a judgement, so there is no mistake to report.
        github.hidden = !generated || advice.kind === 'lora-int8'; githubLora = lora;
        github.textContent = lora ? t('Report a mistake', '报告误判') : t('Report a problem', '提交问题');
        github.toggleAttribute('data-lora', lora);
        if (generated && typeof window !== 'undefined') loadIssueModules();
        if (popup) showFailureDialog(report, t);
        return report;
    }, clear() { currentReport = null; details.value = ''; element.hidden = true; }};
}
