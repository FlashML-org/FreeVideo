// Image generation's wire adapter, cancellable session and options UI. The editor
// owns its document/history; this module inserts a result through its commands.
import * as commands from './image_commands.js';
import {blankDocument, orientMatrix, rasterKinds, scaleSelection} from './image_editor_engine.js';
import {saveReport, reportIssue, showReportTip, focusReportTip, REPORT_GONE, reportFetchError} from './report_issue.js';
import {issueButton} from './issue_button.js';
import {skinSelect} from './fv_select.js';
import {about} from './request_assets.js';
import {createImageLoras} from './image_lora.js';
import {createOtherUsageNotice, otherUsageText} from './other_usage.js';
import {randomUUID} from './random_id.js';
// The page's background loading of works (work_media.js) waits while an upload runs.
const uploadRunning = active => { try { globalThis.dispatchEvent?.(new CustomEvent('freevideo-upload', {detail: {active}})); } catch { /* no page */ } };

// The folder of the input directory that holds the pictures a request sends (the engine's PAGE_FOLDER).
export const PAGE_UPLOAD_FOLDER = 'freevideo-image-ref';
export const IMAGE_GEN_PROTOCOL = Object.freeze({
    root: '/freevideo/image-gen', header: 'X-FreeVideo-Image', upload: '/upload/image',
    installed: 'ready', phase: 'phase', bytes: 'bytes', downloading: 'downloading', checking: 'checking',
    token: 'token', job: 'job', prompt: 'prompt', width: 'width', height: 'height', images: 'images', mask: 'mask', seed: 'seed', loras: 'loras',
    stage: 'stage', fraction: 'fraction', file: 'file', error: 'error', name: 'name', subfolder: 'subfolder',
    states: {queued: 'waiting', starting: 'queued', progress: 'running', complete: 'done', failed: 'error',
        cancelling: 'running', cancelled: 'error'},
});
export const IMAGE_GEN_POLL_MS = 500;
export const IMAGE_GEN_INFO_POLL_MS = 10000;
export const IMAGE_PACK_POLL_MS = 2000;
export const BLANK_CANVAS_RATIOS = Object.freeze(['16:9', '9:16', '1:1', '4:3', '3:4']);
// The one switch the editor, media panel and cast library consult. Every engine platform
// generates pictures now; a Mac too small for it gets the service's small_memory reason.
export function imageGenerationSupported() {
    return true;
}
// On a Mac the GPU's memory is the machine's: the same notes without the graphics card.
function memoryWords([en, zh], unified) {
    return unified ? [en.replace('GPU memory', 'memory').replace(' using the GPU', ''),
        zh.replace('显存', '内存').replace('占用显卡的程序', '程序')] : [en, zh];
}
// The preset a canvas of this size has exactly, or null.
export function presetRatio(width, height) {
    return BLANK_CANVAS_RATIOS.find(ratio => {
        const [w, h] = ratio.split(':').map(Number);
        return width > 0 && height > 0 && width * h === height * w;
    }) || null;
}
export function blankCanvasRatio(target) {
    return BLANK_CANVAS_RATIOS.find(ratio => {
        const [w, h] = ratio.split(':').map(Number);
        return target?.width > 0 && target?.height > 0 && target.width * h === target.height * w;
    }) || BLANK_CANVAS_RATIOS[0];
}
// 高清 is a square side of 1280 against 1024 (about 1.6 times the pixels).
export const BLANK_CANVAS_AREAS = Object.freeze({standard: 1024, high: 1280});
export function blankCanvasSize(ratio, sizes = null, tier = 'standard') {
    if (!BLANK_CANVAS_RATIOS.includes(ratio)) ratio = BLANK_CANVAS_RATIOS[0];
    if (sizes?.[ratio]) {
        const [width, height] = sizes[ratio];
        return {width, height};
    }
    const [w, h] = ratio.split(':').map(Number);
    const side = BLANK_CANVAS_AREAS[tier] || BLANK_CANVAS_AREAS.standard;
    const units = Math.floor(Math.sqrt(side ** 2 / (w * h * 32 ** 2)));
    return {width: w * units * 32, height: h * units * 32};
}
// The step word a caller names for a failure that carries no step. A failure with a reason of its own needs none; the
// catch-all generation_failed, with no stage and no report either, names where it was caught instead of "unreported".
export const originStep = (error, word) => error?.step || (!error?.code || (error.code === 'generation_failed' && !error.stage && !error.report_id) ? word : '');
const failure = (code, info = {}) => Object.assign(new Error(code || 'generation_failed'), {
    code: code || 'generation_failed', report_id: info.report_id || null, stage: info.stage || '',
    fitted: info.fitted || null, memory: info.memory || '',
    other_usage: info.other_usage || null, step: info.step || '',
});

export function createImageGenClient(fetchImpl = globalThis.fetch.bind(globalThis)) {
    let token = '';
    const p = IMAGE_GEN_PROTOCOL;
    async function request(path, options) {
        let response, value;
        try { response = await fetchImpl(path, options); } catch (error) { throw failure(error instanceof TypeError ? 'connection_lost' : 'engine_error', {stage: 'start'}); }
        try { value = await response.json(); } catch { throw failure('engine_error', {stage: 'start'}); }
        if (!response.ok || value[p.error] && !value[p.phase] && !value.state)
            throw failure(value[p.error], {...value, step: originStep({...value, code: value[p.error]}, `http_${response.status}`)});
        return value;
    }
    async function post(action, value, retried = false) {
        if (!token) await info();
        try {
            return await request(`${p.root}/${action}`, {method: 'POST', headers: {
                'Content-Type': 'application/json', [p.header]: token}, body: JSON.stringify(value)});
        } catch (error) {
            // The server restarted behind an open editor: take its new token once.
            if (error.code !== 'reload' || retried) throw error;
            token = '';
            return post(action, value, true);
        }
    }
    // The engine's last answer to info: what it offers (history_trash, history_save, purposes) decides the newer calls.
    let known = null;
    async function info() {
        const value = await request(p.root, {cache: 'no-store'});
        token = value[p.token];
        known = value;
        return {...value, installed: value.installed ?? value[p.installed] ?? false};
    }
    async function offers(flag) {
        try { if (!known) await info(); } catch { return false; }
        return known?.[flag] === true || (flag === 'request_folder' && (known?.history_trash === true || Array.isArray(known?.purposes)));
    }
    return {
        info, offers,
        // purpose: what the picture is for (for example 'cast-draft'), passed through as given.
        async generate({prompt, width, height, images = [], mask, seed, loras, cast_view, model, remember, purpose, layout}) {
            const value = await post('generate', {...(model ? {model} : {}), [p.prompt]: prompt, [p.width]: width, [p.height]: height,
                [p.images]: images, ...(mask ? {[p.mask]: mask} : {}), ...(seed === undefined ? {} : {[p.seed]: seed}),
                ...(loras === undefined ? {} : {[p.loras]: loras}), ...(cast_view === 'back' ? {cast_view} : {}), ...(remember === true ? {remember} : {}),
                ...(typeof purpose === 'string' && purpose ? {purpose} : {}), ...(Array.isArray(layout) && layout.length ? {layout} : {})});
            return {...value, job: value[p.job]};
        },
        async status(job, model) {
            const value = await post('status', {[p.job]: job, ...(model ? {model} : {})});
            const phase = value[p.phase];
            return {...value, state: value.state || p.states[phase] || 'error',
                stage: value[p.stage] || (phase === 'starting' ? 'load' : ''),
                waiting: phase === 'queued' ? value.queued_behind || 'image' : '', fitted: value.fitted || null,
                fraction: value[p.fraction], file: value[p.file], error: value[p.error],
                ...(phase === 'cancelled' ? {error: 'cancelled'} : {})};
        },
        cancel: job => post('cancel', {[p.job]: job}),
        // Pictures this page uploaded for a request that never became a job, or for an editor left without saving;
        // the service removes only its own temporary names (an engine without it answers with an error, ignored).
        discardUploads: images => post('discard-uploads', {images}),
        // The running job's newest step picture (a small JPEG of what the model predicts so far), or null.
        async previewImage(job) {
            if (!token) await info();
            const response = await fetchImpl(`${p.root}/preview`, {method: 'POST', cache: 'no-store', headers: {
                'Content-Type': 'application/json', [p.header]: token}, body: JSON.stringify({[p.job]: job})});
            return response.ok ? response.blob() : null;
        },
        // The image models are fetched by this page's own engine; the reply is the new info.
        async packInstall(model) { const value = await post('pack-install', model ? {model} : {}); return {...value, installed: value.installed ?? value[p.installed] ?? false}; },
        async packCancel(model) { const value = await post('pack-cancel', model ? {model} : {}); return {...value, installed: value.installed ?? value[p.installed] ?? false}; },
        // Click select: the engine finds the object under a point; the pack it needs is fetched on demand like the image models.
        async segment({image, points}) { return post('segment', {image, points}); },
        segmentInstall: () => post('segment-install', {}),
        segmentCancel: () => post('segment-cancel', {}),
        // Every picture generated on this machine, newest first; the engine keeps them in a folder.
        // Every picture, page by page (the engine has no cap; an older engine answers all of them in the first page).
        async history(model) {
            const items = [];
            for (let offset = 0; offset < 100000;) {
                const page = await post('history', {...(model ? {model} : {}), offset, limit: 200});
                const rows = page.items || [];
                items.push(...rows);
                if (page.more !== true || !rows.length) break;
                offset += rows.length;
            }
            return items;
        },
        async historyDelete(file) { return (await post('history-delete', {file})).items || []; },
        async historyDeleteMany(files) { const answer = await post('history-delete-many', {files}); return {items: answer.items || [], failed: answer.failed || []}; },
        historyOpen: () => post('history-open', {}),
        // An uploaded picture (an edit saved in the editor) filed in the works under the engine's own name and clock;
        // the engine removes the uploaded copy. Only when the engine says it offers it (info.history_save).
        historySave: value => post('history-save', value),
        // Deleting with the engine's own undo time (info.history_trash): the pictures move aside at once and go for good
        // after it, even when the page is closed; history-undo brings a batch back.
        async historyTrash(files) { const answer = await post('history-trash', {files}); return {items: answer.items || [], failed: answer.failed || [], batch: answer.batch || null}; },
        historyUndo: batch => post('history-undo', {batch}),
        // The prompts and sizes of a new character's five pictures (the Agent's cards use the same ones).
        castViews: async (description, kind = 'character') => (await post('cast-views', {description, kind})).views,
        async upload(file) {
            // Never under the user's own file name: a random name the service knows as a temporary page upload, removed
            // when the job ends. An engine that keeps these in their own folder (not in ComfyUI's Load Image list) gets
            // them there; an older one takes them in the input folder itself, named so it still removes them.
            const folder = await offers('request_folder');
            const ext = folder ? ({'image/jpeg': 'jpg', 'image/webp': 'webp'}[file.type] || 'png') : 'png';
            const body = new FormData();
            body.append('image', new File([file], folder ? `${randomUUID()}.${ext}` : `freevideo-image-reference-${randomUUID()}.png`, {type: file.type || 'image/png'}));
            body.append('type', 'input'); body.append('overwrite', 'false');
            if (folder) body.append('subfolder', PAGE_UPLOAD_FOLDER);
            uploadRunning(true);
            let value; try { value = await request(p.upload, {method: 'POST', body}); } finally { uploadRunning(false); }
            if (!value[p.name] || /[/\\:]/.test(value[p.name]) || (folder ? value[p.subfolder] !== PAGE_UPLOAD_FOLDER : value[p.subfolder])) throw failure('invalid_media');
            return folder ? `${PAGE_UPLOAD_FOLDER}/${value[p.name]}` : value[p.name];
        },
    };
}

export function imagePackMessage(info, t) {
    const name = t(info?.pack_name_en || 'Image generation models', info?.pack_name_zh || '图像生成模型');
    // Match the launcher's binary size display; its optional pack_size can supply
    // the exact platform-specific label (macOS uses decimal sizes).
    const bytes = info?.[IMAGE_GEN_PROTOCOL.bytes];
    // A non-breaking space keeps the number and its unit on one line.
    const size = (info?.pack_size || (bytes > 0 ? (bytes / 2 ** 30).toFixed(1) + ' GiB' : '17.3 GB'))
        .replace(/ (?=[KMGT]i?B$)/, '\u00a0');
    return t(`Download "${name}" in the launcher first, ${size}.`, `先在启动器里下载“${name}”，${size}。`);
}
// What the page offers while the image models are missing: one button, one muted line, and the bar once loading.
export function packView(info, t) {
    const install = info?.install || {}, own = install.state === 'running' || install.state === 'paused_for_generation';
    // Another process (the launcher) is fetching or re-checking them: nothing to press here.
    if (!own && (info?.[IMAGE_GEN_PROTOCOL.downloading] || info?.[IMAGE_GEN_PROTOCOL.checking])) return null;
    const total = install.total || info?.[IMAGE_GEN_PROTOCOL.bytes] || 0;
    const gb = bytes => (bytes / 1e9).toFixed(1);
    const size = total > 0 ? gb(total) + '\u00a0GB' : '17.3\u00a0GB';
    const rate = install.bytes_per_second || info?.bytes_per_second || 0;
    const left = Math.max(0, total - (install.done || 0));
    const time = rate > 0 && left > 0 ? ' · ' + about(left / rate, t).replace(/^About/, 'about').replaceAll(' ', '\u00a0') : '';
    if (own) {
        const waiting = install.state === 'paused_for_generation';
        // A paused download offers to continue now; the note says what it waits for.
        // While a setup, update or cleanup holds the installation the download cannot go on at the user's request: no button, only the note.
        return {loading: true, waiting, noButton: waiting && install.reason === 'busy_setup', button: waiting ? t('Continue download', '继续下载') : t('Pause', '暂停'), fraction: total > 0 ? Math.min(1, (install.done || 0) / total) : 0,
            note: waiting ? (install.reason === 'busy_video' ? t('Paused while a video generates', '视频生成中，已暂停')
                : install.reason === 'busy_picture' ? t('Paused while a picture is made', '正在出图，已暂停')
                : install.reason === 'busy_setup' ? t('Paused while FreeVideo installs, updates or cleans up', 'FreeVideo 正在安装、更新或清理，已暂停')
                : t('Paused while FreeVideo uses the graphics card for another task', 'FreeVideo 正在用显卡做别的任务，已暂停'))
                : t(`Downloading · ${gb(install.done || 0)}\u00a0GB / ${size}${time}`, `正在下载 · ${gb(install.done || 0)}\u00a0GB / ${size}${time}`)};
    }
    const resume = install.state === 'failed' || install.state === 'cancelled' || install.done > 0;
    const reason = install.state === 'failed' ? t(install.message_en, install.message_zh) : '';
    // A file the video install shares differs: downloading again would stop at the same place, and the launcher's Repair
    // (named in the note) is the way on, so no button is offered here.
    if (install.state === 'failed' && install.reason === 'shared_mismatch' && reason) return {loading: false, fraction: null, reason: true, button: '', note: reason};
    return {loading: false, fraction: null, reason: !!reason, button: resume ? t('Resume download', '继续下载') : t('Download image generation models', '下载图像生成模型'),
        note: reason || (resume ? t(`${gb(install.done || 0)}\u00a0GB / ${size}${time}`, `${gb(install.done || 0)}\u00a0GB / ${size}${time}`) : size + time)};
}
export function imageGenError(code, info, t) {
    const reasons = {
        invalid_loras: ['The LoRA selection is invalid. Choose the files again.', 'LoRA 设置有误，请重新选择文件。'],
        invalid_lora_strength: ['LoRA strength must be between -2 and 2.', 'LoRA 强度必须在 -2 到 2 之间。'],
        duplicate_lora: ['Select each LoRA only once. Remove the duplicate.', '同一个 LoRA 只能选择一次，请移除重复项。'],
        too_many_loras: ['Use at most eight LoRAs per picture.', '一张图片最多使用 8 个 LoRA，请减少数量。'],
        lora_not_found: ['The LoRA file could not be found. Choose it again.', 'LoRA 文件找不到，请重新选择。'],
        invalid_lora_model: ['This LoRA cannot be used for pictures. Choose another one.', '此 LoRA 不适用于图片生成，请换一个。'],
        no_memory: ['Not enough GPU memory, even at the smallest size. Close other apps using the GPU, then try again.',
            '显存不足，最小尺寸也无法生成。请关闭其他占用显卡的程序后重试。'],
        busy_video: ['A video is generating. Try again when it finishes.', '视频正在生成，完成后再试。'],
        busy: ['An image is already being generated. Try again when it finishes.', '已有一张图在生成，完成后再试。'],
        model_unavailable: ['This computer does not support the chosen picture model. Use Qwen-Image 2.1.', '这台电脑不支持所选的图片模型。请改用 Qwen-Image 2.1。'],
        model_unreadable: ['The image generation model files could not be read. Repair them in the launcher and try again.',
            '图像生成模型的文件读不出来。在启动器里修复后再试。'],
        device_unavailable: ['The graphics card is not available right now. Try again later.', '显卡暂时用不了，稍后再试。'],
        worker_exit: ['Image generation stopped unexpectedly. Try again.', '图像生成意外停止了，请再试一次。'],
        timeout: ['This image took too long and was stopped. Try again.', '这一张用时太长，已停止。请再试一次。'],
        disk_full: ['Saving the image failed: the disk is full. Free some space, then generate again.', '保存图片时出错：磁盘空间不够，空出空间后再生成。'],
        cancelled: ['Image generation was cancelled.', '已取消图像生成。'],
        invalid_request: ['The image settings are invalid. Adjust them and try again.', '图片设置有误，请调整后重试。'],
        invalid_prompt: ['The prompt is empty, or longer than 2,000 characters once the picture references are counted. Shorten it and try again.',
            '提示词为空，或加上图片引用后超过 2000 字。请缩短提示词后重试。'],
        invalid_dimensions: ['The image size is outside what the chosen model supports. Change the size and try again.', '图片尺寸超出所选模型支持的范围。请调整尺寸后重试。'],
        invalid_seed: ['The seed is not valid. Clear it or use a smaller whole number, then try again.', '随机种子无效。请清空种子或改用较小的整数后重试。'],
        too_many_images: ['There are more reference images than the chosen model supports. Remove some and try again.', '参考图超过所选模型支持的数量。请减少参考图后重试。'],
        output_unwritable: ['The folder that keeps the pictures cannot be written (freevideo-image in the ComfyUI input folder). Check that it exists, is writable and has no file of the same name, then try again.',
            '无法写入保存图片的文件夹（ComfyUI 的 input 目录下的 freevideo-image）。请确认该目录存在、可写，且没有同名文件，然后重试。'],
        invalid_layout: ['A layout box cannot be used. Draw it larger or change its words, then generate again.', '有一个布局框用不了。请把它画大一些或改一下文字，再生成。'],
        too_many_boxes: ['A layout takes at most 8 boxes. Delete one, then generate again.', '布局最多 8 个框。请删掉一个，再生成。'],
        layout_too_long: ['The words in the layout are too long. Shorten the lines in the boxes, then generate again.', '布局里的文字太长。请把框里的话写短一些，再生成。'],
        invalid_media: ['The reference image cannot be read. Choose another image.', '参考图读不出来，请换一张图。'],
        reload: ['Image generation was interrupted. Try again.', '图片生成中途被打断，请重试。'],
        engine_error: ['The image engine returned an unreadable answer. Try again; if it keeps failing, report the problem.', '图像引擎返回了无法读取的结果。请重试；仍然失败请提交问题。'],
        connection_lost: ['Lost the connection to FreeVideo. Try again once it is back.', '与 FreeVideo 的连接已断开，恢复后请重试。'],
        engine_closed: ['FreeVideo stopped answering while this picture was being made. Start FreeVideo again, then generate again.', '生成过程中 FreeVideo 停止了响应。重新启动 FreeVideo 后再生成一次。'],
        engine_restarted: ['FreeVideo restarted while this picture was being made, so it did not finish. Generate it again.', 'FreeVideo 在生成过程中重新启动，这张图没有完成。请再生成一次。'],
        generation_failed: ['Image generation ran into an error. Try again.', '图像生成出错了，请再试一次。'],
        small_memory: ['This Mac has less than 16 GB of memory and cannot generate pictures.',
            '这台 Mac 的内存不足 16 GB，无法生成图片。'],
    };
    const fact = ['no_memory', 'small_memory'].includes(code) ? otherUsageText(info?.other_usage, t, true) : '';
    const say = (en, zh) => t(...memoryWords([en, zh], info?.unified)) + (fact ? t(' ', '') + fact : '');
    if (code === 'pack_missing') return imagePackUnavailableMessage(info, t);
    if (code === 'no_memory' && info?.memory === 'host')
        return t('Not enough virtual memory to load the image model. Close other apps, or increase virtual memory in system settings, then try again.',
            '虚拟内存不足，无法载入图像模型。请关闭其他程序，或在系统设置中调大虚拟内存，然后重试。');
    if (code === 'no_memory') {
        // Where it ran out decides what helps: the reference pictures are read at a fixed size,
        // so a smaller canvas does not change that stage.
        const stage = info?.stage, size = info?.fitted;
        if (stage === 'encode')
            return say('Not enough GPU memory to load the reference images. Use fewer reference images, or close other apps using the GPU, then try again.',
                '显存不足，无法载入参考图。请减少参考图，或关闭其他占用显卡的程序后重试。');
        if (stage === 'load' || stage === 'text')
            return say('Not enough GPU memory to load the model. Close other apps using the GPU, then try again.',
                '显存不足，无法载入模型。请关闭其他占用显卡的程序后重试。');
        if (size?.width && size?.height)
            return say(`Not enough GPU memory, even at ${size.width}\u00a0×\u00a0${size.height}. Close other apps using the GPU, then try again.`,
                `显存不足，${size.width}\u00a0×\u00a0${size.height} 也无法生成。请关闭其他占用显卡的程序后重试。`);
    }
    const known = Object.hasOwn(reasons, code) ? code : 'generation_failed';
    // An unnamed error still says which step it stopped at; a page with its own
    // Retry button does not repeat "try again" in the sentence.
    if (known === 'generation_failed' && Object.hasOwn(FAILED_AT, info?.stage)) {
        const [en, zh] = FAILED_AT[info.stage];
        return info?.retried ? say(en, zh) : say(`${en} Try again.`, `${zh}请再试一次。`);
    }
    return say(...(info?.retried && Object.hasOwn(RETRIED, known) ? RETRIED[known] : reasons[known]));
}
const RETRIED = Object.freeze({
    worker_exit: ['Image generation stopped unexpectedly.', '图像生成意外停止。'],
    timeout: ['This image took too long and was stopped.', '本次生成用时过长，已停止。'],
    reload: ['Image generation was interrupted.', '图像生成中途被打断。'],
    generation_failed: ['Image generation ran into an error.', '图像生成出错。'],
});
const FAILED_AT = Object.freeze({
    start: ['An error occurred while starting image generation.', '启动图像生成时出错。'],
    load: ['An error occurred while loading the image model.', '加载图像模型时出错。'],
    text: ['An error occurred while reading the prompt.', '读取提示词时出错。'],
    encode: ['An error occurred while reading the reference images.', '读取参考图时出错。'],
    denoise: ['An error occurred while generating the image.', '生成画面时出错。'],
    decode: ['An error occurred while producing the image.', '输出图像时出错。'],
});

// Keep image failure copy here for editorial review. The submit card and its
// wording belong to report_issue.js, shared with video reports.
// Queue and size-fit notes, kept with the failure copy for editorial review.
export const QUEUE_LABELS = Object.freeze({
    busy: ['Another image is generating. This one starts when it finishes.', '前面有 1 张在生成，完成后自动开始。'],
    image: ['Added to the queue. It starts when the previous one finishes.', '已加入队列，前面的完成后自动开始。'],
    video: ['Queued. It starts when the video finishes.', '已排队，视频生成完后自动开始。'],
});
export const fittedNote = (size, t, unified = false) => t(...memoryWords([`Not enough GPU memory. Made at ${size.width}\u00a0×\u00a0${size.height} instead.`,
    `显存不足，已改为 ${size.width}\u00a0×\u00a0${size.height} 生成。`], unified));
export const REPORT_LABELS = Object.freeze({
    download: ['Download report', '下载报告'],
    description: ['Diagnostic report: hardware, settings and timings', '诊断报告：硬件、设置和耗时'],
    preparing: ['Preparing…', '正在整理…'],
    unavailable: ['Report unavailable · fetch again', '报告暂不可用 · 重新获取'],
    gone: REPORT_GONE,
});

// The report download and its "send as an issue" card; the request carries only the opaque report ID.
export function imageReportBar(card, report_id, t, {productVersion = () => null, fetchApi = globalThis.fetch.bind(globalThis), quiet = false} = {}) {
    // `quiet` is the picture page's own: the video page's weight, not a bordered button. Every surface says "Download report".
    const download = REPORT_LABELS.download;
    const doc = card.ownerDocument || document;
    const fetchReport = async () => {
        const response = await fetchApi(`${IMAGE_GEN_PROTOCOL.root}/report/${encodeURIComponent(report_id)}`, {cache: 'no-store'});
        if (!response.ok) throw reportFetchError(response);
        return response.blob();
    };
    const version = () => typeof productVersion === 'function' ? productVersion() : productVersion;
    const bar = doc.createElement('div'); bar.className = 'fv-actions';
    const anchor = doc.createElement('button'); anchor.type = 'button';
    anchor.className = 'fv-action fv-action-secondary fv-action-report' + (quiet ? ' fv-image-report-quiet' : '');
    anchor.textContent = t(...download); anchor.title = t(...REPORT_LABELS.description);
    // On a failure card it is drawn as the card's actions; beside a quiet report link it stays as it was.
    const issue = issueButton(t, () => ({where: ['Image diagnostic report', '生图诊断报告'], message: t(...REPORT_LABELS.unavailable)}),
        quiet ? {} : {className: 'fv-action'});
    issue.hidden = true;
    anchor.onclick = async () => {
        if (anchor.disabled) return;
        if (focusReportTip(bar)) return;
        const restoreFocus = doc.activeElement === anchor;
        issue.hidden = true;
        anchor.disabled = true; anchor.textContent = t(...REPORT_LABELS.preparing);
        let gone = false;
        try {
            await saveReport(await fetchReport());
            if (card.isConnected) showReportTip(bar, anchor, t, {mode: 'downloaded',
                submit: () => reportIssue(fetchReport, version(), {save: false}), onHide: () => anchor.focus()});
            anchor.textContent = t(...download);
        } catch (error) {
            gone = Boolean(error?.gone);
            anchor.textContent = t(...(gone ? REPORT_LABELS.gone : REPORT_LABELS.unavailable)); issue.hidden = gone;
        } finally {
            anchor.disabled = gone;
            if (restoreFocus && card.isConnected && doc.activeElement === doc.body) anchor.focus({preventScroll: true});
        }
    };
    bar.append(anchor, issue);
    return {bar, anchor};
}

// Shared by the editor and the other image entry points. Only the opaque report
// ID goes in the request; raw service messages are never shown in the page.
export function renderImageFailure(host, {error, report_id, stage, step, fitted, info, memory, other_usage} = {}, t,
    {productVersion = () => null, fetchApi = globalThis.fetch.bind(globalThis), retry = null, retried = Boolean(retry)} = {}) {
    // `retry` ({label: [en, zh], onClick}) puts the page's Retry first in the card's own action row;
    // `retried` alone says the caller shows a Retry of its own elsewhere.
    host.replaceChildren();
    if (!error || error === 'cancelled') return null;
    const doc = host.ownerDocument || document;
    const cssURL = new URL('./result_actions.css', import.meta.url).href;
    if (!doc.querySelector(`link[href="${cssURL}"]`)) {
        const css = doc.createElement('link'); css.rel = 'stylesheet'; css.href = cssURL; doc.head.append(css);
    }
    const card = doc.createElement('div'); card.className = 'fv-result-links';
    const reason = doc.createElement('div'); reason.className = 'fv-image-failure-reason';
    reason.setAttribute('role', 'status'); reason.textContent = imageGenError(error, {stage, fitted, memory, unified: info?.unified_memory, other_usage, retried}, t);
    card.append(reason); host.append(card);
    let again = null;
    if (retry) {
        again = doc.createElement('button'); again.type = 'button';
        again.className = 'fv-action fv-action-primary fv-image-retry'; again.textContent = t(...retry.label);
        again.onclick = () => retry.onClick();
    }
    // The card always has a way to report: the diagnostic report (with its own technical details) when the engine made one,
    // else a form that carries the code and stage, and the same facts under a details fold.
    let bar;
    if (report_id) ({bar} = imageReportBar(card, report_id, t, {productVersion, fetchApi}));
    else {
        const facts = [[['Error code', '错误码'], error], [['Stage', '阶段'], stage], [['Step', '步骤'], step || (stage ? '' : 'unreported')], [['Memory', '内存'], memory]].filter(([, v]) => v);
        bar = doc.createElement('div'); bar.className = 'fv-actions';
        // Drawn as the card's other actions (Retry, Download report): their border, focus ring and press.
        bar.append(issueButton(t, () => ({where: ['Image generation', '图像生成'], message: facts.map(([[en, zh], v]) => `${t(en, zh)}: ${v}`).join('\n')}), {className: 'fv-action'}));
        if (again) bar.prepend(again);
        card.append(bar);
        const details = doc.createElement('details'); details.className = 'fv-image-failure-details';
        const summary = doc.createElement('summary'); summary.textContent = t('Technical details', '技术详情'); details.append(summary);
        for (const [[en, zh], v] of facts) { const row = doc.createElement('div'); row.textContent = `${t(en, zh)}: ${v}`; details.append(row); }
        card.append(details);
        bar = null;
    }
    if (bar) { if (again) bar.prepend(again); card.append(bar); }
    // Let native Enter/Space activate these buttons without invoking editor
    // canvas shortcuts. Tab and the shared card's Escape handling still work.
    card.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') event.stopPropagation();
    });
    return card;
}
export function imagePackUnavailableMessage(info, t) {
    if (info?.[IMAGE_GEN_PROTOCOL.checking]) return t('Checking. Available when finished.', '正在检查，完成后可用。');
    return info?.[IMAGE_GEN_PROTOCOL.downloading]
        ? t('Downloading. Available when finished.', '正在下载，完成后可用。') : imagePackMessage(info, t);
}

// The service accepts 256..2048 on each side; a larger canvas is generated at its
// fitted size and the result is laid over the canvas like any other layer.
export function fitGenerationSize(width, height) {
    const scale = Math.min(1, 2048 / Math.max(width, height));
    const side = value => Math.min(2048, Math.max(256, Math.round(value * scale)));
    return {width: side(width), height: side(height)};
}

// A picture of another shape than the canvas (canvas: {width, height, ratio}) is made for a new, empty canvas.
export function generationContext(doc, selection, mode, canvas = null) {
    if (canvas && mode === 'new') {
        return {doc: blankDocument(canvas, canvas.ratio), selection: null, layer: null, mode, canvas,
            ...fitGenerationSize(canvas.width, canvas.height)};
    }
    const layer = mode === 'edit' ? doc.layers.find(l => l.id === doc.active && rasterKinds.has(l.kind)) : null;
    return {doc, selection, layer, mode, ...fitGenerationSize(doc.crop.w, doc.crop.h)};
}
export async function uploadGenerationReference(renderer, client, context, alive = () => true) {
    let blob;
    if (context.layer) {
        await renderer.preload(context.doc);
        if (!alive()) throw failure('cancelled');
        const raster = renderer.layerImage(context.doc, context.layer, 1, true);
        blob = await new Promise((resolve, reject) => raster.toBlob(b => b ? resolve(b) : reject(failure(undefined, {step: 'encode_reference'})), 'image/png'));
    } else blob = (await renderer.export(context.doc, 'image/png')).blob;
    if (!alive()) throw failure('cancelled');
    const file = new File([blob], `freevideo-image-reference-${randomUUID()}.png`, {type: 'image/png'});
    return client.upload(file);
}

// The selection as a black and white picture the size of the layer being edited, uploaded beside it.
// Nothing is sent when nothing is selected, so the whole picture is edited as before.
export async function uploadGenerationMask(renderer, client, context, alive = () => true) {
    if (!context.layer || !context.selection) return null;
    const shape = renderer.selectionMask(context.doc, context.selection, {layer: context.layer, scale: 1});
    const mask = globalThis.document.createElement('canvas');
    mask.width = shape.width; mask.height = shape.height;
    const draw = mask.getContext('2d', {willReadFrequently: true});
    draw.fillStyle = '#000'; draw.fillRect(0, 0, mask.width, mask.height);
    draw.drawImage(shape, 0, 0);
    const pixels = draw.getImageData(0, 0, mask.width, mask.height).data;
    let any = false;
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i] >= 128) { any = true; break; }
    if (!any) return null;
    const blob = await new Promise((resolve, reject) => mask.toBlob(b => b ? resolve(b) : reject(failure(undefined, {step: 'encode_mask'})), 'image/png'));
    if (!alive()) throw failure('cancelled');
    return client.upload(new File([blob], `freevideo-image-mask-${randomUUID()}.png`, {type: 'image/png'}));
}

// The object under a click, as the name of a black and white picture the size of the layer's pixels.
// The layer is uploaded once for as long as it looks the same.
// The engine removes that copy a while after its last use, so a later click sends the layer again.
const segmentUploads = new WeakMap();
// The pictures click select uploaded (one per look of a layer); the editor lets them go when it closes.
const segmentNames = new Set();
export function discardSegmentUploads(client) {
    const names = [...segmentNames]; segmentNames.clear();
    if (names.length) Promise.resolve().then(() => client.discardUploads?.(names)).catch(() => {});
}
export async function segmentAt(renderer, client, doc, layer, point, again = false) {
    const look = JSON.stringify([layer.ops.map(o => o.id), layer.adjust, layer.src, layer.mask?.ops.map(o => o.id)]);
    let known = segmentUploads.get(layer);
    if (known?.look !== look) {
        await renderer.preload(doc);
        const raster = renderer.layerImage(doc, layer, 1, true);
        const blob = await new Promise((resolve, reject) => raster.toBlob(b => b ? resolve(b) : reject(failure(undefined, {step: 'encode_segment'})), 'image/png'));
        // The layer's previous look is not needed any more: its upload goes.
        if (known?.name) { segmentNames.delete(known.name); Promise.resolve().then(() => client.discardUploads?.([known.name])).catch(() => {}); }
        known = {look, name: await client.upload(new File([blob], `freevideo-image-reference-${randomUUID()}.png`, {type: 'image/png'})), width: raster.width, height: raster.height};
        segmentUploads.set(layer, known); segmentNames.add(known.name);
    }
    const x = Math.min(known.width - 1, Math.max(0, Math.floor(point[0] * known.width))), y = Math.min(known.height - 1, Math.max(0, Math.floor(point[1] * known.height)));
    let value;
    try { value = await client.segment({image: known.name, points: [[x, y, 1]]}); }
    catch (error) {
        // The engine lets a point selection's picture go a while after its last use: send the layer again once.
        if (error?.code !== 'invalid_media' || again) throw error;
        segmentUploads.delete(layer); segmentNames.delete(known.name);
        return segmentAt(renderer, client, doc, layer, point, true);
    }
    if (typeof value?.mask !== 'string') throw failure(undefined, {step: 'segment_result'});
    const image = await renderer.image(value.mask);
    renderer.images.get(value.mask).value = image;
    return value.mask;
}

// "Edit this" can take more pictures beside the layer being edited: that layer is always image 1 and
// the others follow in the order they were added. The page offers fewer than the service accepts.
export const PAGE_REFERENCE_LIMIT = 3;
export function referenceLimit(info) {
    const most = Number(info?.max_images);
    return Number.isInteger(most) && most > 1 ? Math.min(PAGE_REFERENCE_LIMIT, most) : 1;
}
export function addReference(list, item, limit) {
    if (!item?.file || list.length >= limit - 1 || list.some(r => r.file === item.file)) return list;
    return [...list, {file: item.file, label: item.label || ''}];
}
export const removeReference = (list, file) => list.filter(r => r.file !== file);
// Media lives in folders the image worker does not read, so each extra picture is copied beside the
// first reference for every request; the service removes the copies when the job ends.
export async function uploadExtraReferences(client, references, render, alive = () => true) {
    const names = [];
    for (const item of references) {
        const blob = await render(item.file);
        if (!alive()) throw failure('cancelled');
        names.push(await client.upload(new File([blob], `freevideo-image-reference-${randomUUID()}.png`, {type: 'image/png'})));
        if (!alive()) throw failure('cancelled');
    }
    return names;
}

export function insertGenerationResult(doc, result, context, t) {
    const sx = doc.width / context.doc.width, sy = doc.height / context.doc.height;
    const reference = context.mode === 'edit' && context.layer;
    const above = doc.layers.find(l => l.id === reference?.id) || doc.layers.at(-1);
    let next = commands.addImageLayer({...doc, active: above?.id || null}, {
        ...result, name: context.mode === 'new' ? t('Generated', '生成') : t('Edited', '改图')});
    let geometry;
    if (reference) {
        geometry = {cx: reference.cx * sx, cy: reference.cy * sy, w: reference.w * sx, h: reference.h * sy,
            rotation: reference.rotation, flipX: reference.flipX, flipY: reference.flipY};
    } else {
        const c = doc.crop, point = orientMatrix(doc).inverse().transformPoint(new DOMPoint(c.x + c.w / 2, c.y + c.h / 2));
        geometry = {cx: point.x, cy: point.y, w: c.w, h: c.h, rotation: -doc.rotate, flipX: doc.flipX, flipY: doc.flipY};
    }
    next = {...next, layers: next.layers.map(l => l.id === next.active ? {...l, ...geometry} : l)};
    if (context.selection && !context.region) next = commands.maskFromSelection(next, next.active, scaleSelection(context.selection, sx, sy));
    return next;
}

// One bar for the whole picture. The service predicts how long each stage takes on this machine
// and sends the finished share and the time left; the bar stays below 100% until the result is placed.
// A row without them (an older service) falls back to a plain share per stage.
const STAGE_SLICES = {
    new: [['load', .15], ['text', .15], ['denoise', .5], ['decode', .2]],
    edit: [['load', .15], ['text', .15], ['encode', .05], ['denoise', .45], ['decode', .2]],
};
export function imageGenProgress(stage, fraction, withImages = false, overall = null) {
    if (Number.isFinite(overall)) return Math.min(.99, Math.max(0, overall));
    const own = Math.min(1, Math.max(0, Number.isFinite(fraction) ? fraction : 0));
    let start = 0;
    for (const [name, weight] of STAGE_SLICES[withImages ? 'edit' : 'new']) {
        if (name === stage) return Math.min(.99, start + weight * own);
        start += weight;
    }
    return Math.min(.99, own);
}
// Seconds left as the service predicts them, in the video's words; null while there is none.
export function imageRemainingText(seconds, t) {
    if (!Number.isFinite(seconds)) return '';
    return seconds >= 60 ? t(`About ${Math.ceil(seconds / 60)}\u00a0min left`, `预计还需约 ${Math.ceil(seconds / 60)}\u00a0分钟`)
        : t('Less than a minute left', '预计还需不到 1\u00a0分钟');
}

// An epoch rejects late uploads/status/results. A generate response arriving
// after Stop still has to be cancelled, even though its job was not known yet.
const IMAGE_GEN_BUSY_RETRY_MS = 2000;

export function createImageGenSession({client, prepare, apply, onChange = () => {},
    beforeGenerate = async () => {},
    schedule = setTimeout, unschedule = clearTimeout, store = null,
    visible = () => globalThis.document?.visibilityState !== 'hidden', tasks = null}) {
    let epoch = 0, timer = null, infoTimer = null, disposed = false, checking = null, unwatch = null, wake = null;
    let state = {info: null, checking: false, running: false, job: null, fraction: 0, remaining: null, stage: '', error: '', report_id: null,
        waiting: '', fitted: null};
    const emit = patch => { state = {...state, ...patch}; if (!disposed) onChange(state); };
    const failed = error => error.code === 'cancelled' ? {error: '', report_id: null, stage: '', memory: '', step: ''}
        : {error: error.code || 'generation_failed', report_id: error.report_id || null, stage: error.stage || '', step: error.step || '',
            memory: error.memory || '', other_usage: error.other_usage || null, ...(error.fitted ? {fitted: error.fitted} : {})};
    const cancelJob = job => Promise.resolve().then(() => client.cancel(job)).catch(() => {});
    const forget = () => { try { store?.clear(); } catch { /* the job is only not resumed after a reload */ } };
    function scheduleInfo() {
        unschedule(infoTimer); infoTimer = null;
        if (disposed || state.info?.installed) return;
        const loading = ['running', 'paused_for_generation'].includes(state.info?.install?.state);
        infoTimer = schedule(() => {
            infoTimer = null;
            if (visible()) void check(); else scheduleInfo();
        }, loading ? IMAGE_PACK_POLL_MS : IMAGE_GEN_INFO_POLL_MS);
    }
    async function check() {
        if (checking || disposed) return checking;
        emit({checking: true});
        checking = (async () => {
            try { const info = await client.info(); if (!disposed) emit({info}); }
            catch (error) { if (!disposed && !state.running) emit(failed(error)); }
            finally { checking = null; if (!disposed) { emit({checking: false}); scheduleInfo(); } }
        })();
        return checking;
    }
    // With the page's job store (tasks): the store polls the engine and keeps the job when the editor closes; the
    // session only follows its row. Done: the result is placed (when this editor is still the one that asked);
    // the store files the picture in the works either way.
    function follow(current, id, context, onBusy = null) {
        const alive = () => current === epoch && !disposed;
        unwatch?.(); let ended = false, off = null;
        const stop = () => { ended = true; off?.(); off = null; if (unwatch === stop) unwatch = null; };
        unwatch = stop;
        off = tasks.jobs.subscribe(() => {
            if (ended || !alive()) return;
            const row = tasks.jobs.get(id);
            if (!row) { stop(); forget(); emit({running: false, job: null, fraction: 0, stage: '', waiting: '', stopping: false}); return; }
            if (tasks.ACTIVE.has(row.status)) {
                emit({job: id, fraction: Math.max(state.fraction, Number.isFinite(row.percent) ? row.percent : 0),
                    stage: row.phase === 'waiting' ? '' : row.phase || '', waiting: row.status === 'queued' ? row.meta?.waiting || 'image' : '',
                    remaining: Number.isFinite(row.eta_seconds) ? row.eta_seconds : null, report_id: row.meta?.report_id || state.report_id,
                    fitted: row.meta?.fitted || state.fitted, stopping: row.status === 'cancelling'});
                return;
            }
            stop();
            if (row.status === 'done' && row.result?.file) {
                void Promise.resolve(apply({file: row.result.file, fitted: row.meta?.fitted || null}, context, alive)).then(() => {
                    if (!alive()) return;
                    forget(); tasks.placed?.(row);
                    emit({running: false, job: null, fraction: 1, stage: '', waiting: '', stopping: false, ...(row.meta?.fitted ? {fitted: row.meta.fitted} : {})});
                }, error => { if (alive()) { forget(); emit({running: false, job: null, waiting: '', stopping: false, ...failed(error)}); } });
                return;
            }
            if (onBusy && row.status === 'failed' && row.failure?.code === 'busy') { tasks.placed?.(row); onBusy(); return; }
            forget(); tasks.placed?.(row);
            if (row.status === 'cancelled') emit({running: false, job: null, stage: '', fraction: 0, waiting: '', stopping: false, error: '', report_id: null, memory: ''});
            else { const reason = row.failure || {}; emit({running: false, job: null, waiting: '', stopping: false, ...failed({...reason, code: reason.code || 'generation_failed', step: originStep(reason, 'task_row')})}); }
        }, {id});
        // The row may have ended before subscribe returned.
        if (ended) { off(); off = null; }
    }
    // One job from its first status to the result. A job taken up again after the page was
    // reloaded (``resumed``) that the service no longer knows ends quietly: there is nothing to say.
    function track(current, job, context, withImages, resumed = false, onBusy = null) {
        const alive = () => current === epoch && !disposed;
        async function poll() {
            try {
                const row = await client.status(job);
                if (!alive()) return;
                if (onBusy && row.state === 'error' && row.error === 'busy') throw failure('busy', row);
                const fraction = imageGenProgress(row.stage, row.fraction, withImages, row.overall);
                emit({fraction: Math.max(state.fraction, fraction), stage: row.stage || '', waiting: row.waiting || '',
                    remaining: Number.isFinite(row.remaining_seconds) ? row.remaining_seconds : null,
                    report_id: row.report_id || state.report_id,
                    fitted: row.fitted || state.fitted});
                if (row.state === 'done') {
                    if (!row.file) throw failure(undefined, {step: 'result_missing'});
                    await apply(row, context, alive);
                    if (alive()) { forget(); emit({running: false, job: null, fraction: 1, stage: '', waiting: ''}); }
                } else if (row.state === 'error') throw failure(row.error, row);
                else timer = schedule(() => { timer = null; void poll(); }, IMAGE_GEN_POLL_MS);
            } catch (error) {
                if (alive()) {
                    if (onBusy && error.code === 'busy') { void cancelJob(job); onBusy(); return; }
                    forget();
                    if (resumed && (error.code === 'unknown_job' || error.code === 'reload')) {
                        emit({running: false, job: null, fraction: 0, stage: '', waiting: ''});
                        return;
                    }
                    void cancelJob(job);
                    emit({running: false, job: null, waiting: '', ...failed(error)});
                }
            }
        }
        timer = schedule(() => { timer = null; void poll(); }, IMAGE_GEN_POLL_MS);
    }
    // The engine makes one picture at a time: a request that finds it busy waits its turn here and starts by itself.
    // Stop (a new epoch) ends the wait.
    // Ends a wait for the engine at once (Stop, closing): the waiting start() then returns instead of hanging.
    const release = () => { const done = wake; wake = null; done?.(); };
    async function whenFree(submit, alive, resend = null) {
        for (;;) {
            try { return await submit(); } catch (error) {
                if (error.code !== 'busy') throw error;
                if (!alive()) return null;
                emit({waiting: 'busy'});
                await new Promise(done => { wake = done; timer = schedule(() => { timer = null; wake = null; done(); }, IMAGE_GEN_BUSY_RETRY_MS); });
                if (!alive()) return null;
                if (resend && !await resend()) return null;
            }
        }
    }
    async function start(request) {
        if (state.running || disposed || !request.prompt.trim() || !state.info?.installed) return;
        const current = ++epoch;
        // A picture that is edited sends its reference images first; there is no job to report on yet.
        emit({running: true, job: null, fraction: 0, stage: request.mode === 'edit' ? 'upload' : '', error: '', report_id: null, memory: '', waiting: '', fitted: null});
        const alive = () => current === epoch && !disposed;
        let sent = [], submitted = false;
        const gone = new Set();
        // Every picture the page sends is noted as it arrives: a stop after one has gone up still removes it.
        const uploaded = [];
        // The service has not taken these pictures: they go (once each), whichever path ends the request.
        const discard = () => {
            const names = [...new Set([...uploaded, ...sent])].filter(name => !gone.has(name));
            for (const name of names) gone.add(name);
            if (names.length) Promise.resolve().then(() => client.discardUploads?.(names)).catch(() => {});
        };
        const sender = {upload: async file => { const name = await client.upload(file); uploaded.push(name); return name; }};
        try {
            const {context, images = [], mask} = await prepare(request, alive, sender);
            sent = [...images, ...(mask ? [mask] : [])];
            if (!alive()) return;
            if (state.stage === 'upload') emit({stage: ''});
            const withImages = images.length > 0;
            await beforeGenerate({kind: 'image', width: context.width, height: context.height, image_references: images.length});
            if (!alive()) return;
            const request_ = {prompt: request.prompt, width: context.width, height: context.height, images, mask,
                ...(request.seed === undefined ? {} : {seed: request.seed}),
                ...(request.loras === undefined ? {} : {loras: request.loras}),
                ...(tasks?.purpose ? {purpose: tasks.purpose} : {})};
            // The engine can turn a request away as busy at submit or while following it: wait and send it again.
            // A turned-away request has its uploaded pictures removed by the service, so they are sent again before each retry.
            const resend = async () => {
                if (!sent.length) return true;
                const next = await prepare(request, alive, sender);
                if (!alive()) return false;
                request_.images = next.images || []; request_.mask = next.mask;
                sent = [...request_.images, ...(request_.mask ? [request_.mask] : [])];
                return true;
            };
            const again = () => {
                if (!alive()) return;
                emit({job: null, waiting: 'busy'});
                timer = schedule(() => { timer = null; if (alive()) void go(true); }, IMAGE_GEN_BUSY_RETRY_MS);
            };
            const go = async (retry = false) => {
                // The pictures of this attempt are the page's until the service takes the request: a stop or an error
                // before that, on a first send or a resend alike, removes them.
                let taken = false;
                try {
                    if (retry && !await resend()) return;
                    if (tasks) {
                        tasks.startJobSources?.();
                        const sent_ = await whenFree(() => tasks.jobs.submit('image', request_, {view: tasks.view || 'image-editor',
                            meta: {...(tasks.meta?.(request, context) || {}), text: request.text ?? request.prompt, withImages, width: context.width, height: context.height},
                            ...(tasks.client ? {client: tasks.client} : {})}), alive, resend);
                        if (!sent_) return;
                        const {id} = sent_;
                        submitted = taken = true;
                        if (!alive()) return;
                        emit({job: id, waiting: ''});
                        try { store?.save({job: id, context, withImages, task: true}); } catch { /* the job is only not picked up again here */ }
                        follow(current, id, context, again);
                        return;
                    }
                    const row = await whenFree(async () => {
                        const answer = await client.generate(request_);
                        if (!answer.job) throw failure(answer.error, {...answer, step: originStep({...answer, code: answer.error}, 'submit')});
                        return answer;
                    }, alive, resend);
                    if (!row) return;
                    const {job} = row;
                    submitted = taken = true;
                    emit({waiting: ''});
                    if (row.fitted) emit({fitted: row.fitted});
                    if (!alive()) { await cancelJob(job); return; }
                    emit({job});
                    try { store?.save({job, context, withImages}); } catch { /* the job is only not resumed after a reload */ }
                    track(current, job, context, withImages, false, again);
                } catch (error) {
                    if (alive()) emit({running: false, job: null, ...failed(error)});
                } finally {
                    if (!taken) discard();
                }
            };
            await go();
        } catch (error) {
            if (alive()) emit({running: false, job: null, ...failed(error)});
        } finally {
            // Stopped, turned away or failed before the service took the request: its pictures go now (a job removes
            // its own when it ends).
            if (!submitted) discard();
        }
    }
    // The page was reloaded while a picture was being made: follow the same job and place its result.
    function resume({job, context, withImages = false, task = false}) {
        if (state.running || disposed || !job || !context) return false;
        if (tasks) {
            // Only a job of the store is followed again; a record from before (an engine key) is let go.
            tasks.startJobSources?.();
            if (!task || !tasks.jobs.get(job)) { forget(); return false; }
            const current = ++epoch;
            emit({running: true, job, fraction: 0, stage: '', error: '', report_id: null, memory: '', waiting: '', fitted: null});
            follow(current, job, context);
            return true;
        }
        const current = ++epoch;
        emit({running: true, job, fraction: 0, stage: '', error: '', report_id: null, memory: '', waiting: '', fitted: null});
        track(current, job, context, withImages, true);
        return true;
    }
    function cancel() {
        if (tasks) {
            const id = state.job;
            if (!state.running) return Promise.resolve();
            if (!id) { ++epoch; release(); forget(); emit({running: false, job: null, stage: '', fraction: 0, waiting: '', stopping: false}); return Promise.resolve(); }
            emit({stopping: true});
            return Promise.resolve(tasks.jobs.cancel(id)).catch(() => { emit({stopping: false}); });
        }
        ++epoch; unschedule(timer); timer = null; release();
        const job = state.job;
        forget();
        emit({running: false, job: null, stage: '', fraction: 0, error: '', report_id: null, memory: '', waiting: '', fitted: null});
        return job ? cancelJob(job) : Promise.resolve();
    }
    // Start or pause the download of the image models; the reply is the new state, then polling follows it.
    async function pack(action) {
        try { const info = await (action === 'cancel' ? client.packCancel() : client.packInstall()); if (!disposed) emit({info, error: '', report_id: null, memory: ''}); }
        catch (error) { if (!disposed) emit(failed(error)); }
        finally { if (!disposed) scheduleInfo(); }
    }
    return {check, start, resume, cancel, pack, clearError: () => emit({error: '', report_id: null, memory: ''}), state: () => state,
        // Closing the editor only stops watching: the picture keeps going on the engine, and the job record stays so
        // opening the same picture again picks it up (resume). Only Stop cancels.
        dispose() { disposed = true; unschedule(infoTimer); infoTimer = null; ++epoch; unschedule(timer); timer = null; release(); unwatch?.(); unwatch = null; return Promise.resolve(); }};
}

// The job being made, kept for this tab only so that a reloaded page can follow it and place the
// result. Only what placing needs is kept (never the document), and a record for another editor or
// older than the service keeps its results is left alone.
export const IMAGE_JOB_KEEP_MS = 30 * 60 * 1000;
export function imageJobStore(key, storage = globalThis.sessionStorage, now = Date.now, name = 'freevideo.imageGenJob') {
    return {
        save({job, context, withImages, task = false}) {
            const {layer, selection} = context;
            const keep = layer && {id: layer.id, cx: layer.cx, cy: layer.cy, w: layer.w, h: layer.h,
                rotation: layer.rotation, flipX: layer.flipX, flipY: layer.flipY};
            storage.setItem(name, JSON.stringify({key, job, withImages: !!withImages, at: now(), ...(task ? {task: true} : {}), context: {
                mode: context.mode, doc: {width: context.doc.width, height: context.doc.height},
                layer: keep || null, selection: selection || null, canvas: context.canvas || null}}));
        },
        load() {
            try {
                const record = JSON.parse(storage.getItem(name) || 'null');
                if (!record || record.key !== key || typeof record.job !== 'string' || !record.context?.doc
                    || !(now() - record.at < IMAGE_JOB_KEEP_MS)) return null;
                return record;
            } catch { return null; }
        },
        // Only this editor's own record: closing another picture's editor leaves it alone.
        clear() {
            try {
                let record = null;
                try { record = JSON.parse(storage.getItem(name) || 'null'); } catch { /* unreadable: drop it */ }
                if (!record || record.key === key) storage.removeItem(name);
            } catch { /* nothing kept */ }
        },
    };
}

export function createImageGeneration({client, renderer, t, getDoc, getSelection, commit, el, button, segmented,
    library = [], resolve = file => file, icon = () => '', onRatioChange, onNewCanvas, onPlace, productVersion, fetchApi, resumeKey = null, tasks = null}) {
    let mode = 'edit', prompt = '', mounted = null, references = [], loras = null, tier = 'standard', pendingRatio = null;
    let promptRequired = false, attentionTimer = null, wantFocus = false;
    // A reference is sent no larger than the largest picture the service makes.
    async function referencePNG(file) {
        const image = await renderer.image(file);
        const k = Math.min(1, 2048 / Math.max(image.naturalWidth, image.naturalHeight));
        const canvas = el('canvas'); canvas.width = Math.max(1, Math.round(image.naturalWidth * k)); canvas.height = Math.max(1, Math.round(image.naturalHeight * k));
        canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
        return new Promise((done, fail) => canvas.toBlob(b => b ? done(b) : fail(failure(undefined, {step: 'encode_editor'})), 'image/png'));
    }
    let store = null;
    try { if (resumeKey != null && globalThis.sessionStorage) store = imageJobStore(resumeKey); } catch { /* no storage */ }
    // A result placed (or a failure shown) here needs no card in the floating window.
    const placed = row => { tasks.jobs.markSeen(row.id); tasks.jobs.dismiss(row.id); if (row.status === 'done' && row.result?.file) tasks.onResult?.(row.result.file); };
    const session = createImageGenSession({client, store, tasks: tasks && {...tasks, placed},
        beforeGenerate: query => mounted?.otherUsage.check(query),
        prepare: async (request, alive, sender = client) => {
            const context = generationContext(getDoc(), getSelection(), request.mode, request.canvas);
            const images = request.mode === 'edit' ? [await uploadGenerationReference(renderer, sender, context, alive),
                ...await uploadExtraReferences(sender, request.references || [], referencePNG, alive)] : [];
            // Only the selected part of the edited picture (the first) is changed.
            const mask = images.length ? await uploadGenerationMask(renderer, sender, context, alive) : null;
            return {context: mask ? {...context, region: true} : context, images, mask};
        },
        apply: async (result, context, alive) => {
            const image = await renderer.image(result.file);
            if (!alive()) return;
            renderer.images.get(result.file).value = image;
            // One step: undo brings back the old canvas with its pictures.
            const base = context.canvas && onNewCanvas ? onNewCanvas(context.canvas) : getDoc();
            pendingRatio = null;
            commit(insertGenerationResult(base, {src: result.file, nw: image.naturalWidth, nh: image.naturalHeight}, context, t));
        }, onChange: update,
    });
    const saved = store?.load();
    const resumed = saved && session.resume(saved) ? saved : null;
    const limit = () => referenceLimit(session.state().info);
    const chosen = () => mode === 'edit' ? references.slice(0, limit() - 1) : [];
    function clearAttention() {
        clearTimeout(attentionTimer); attentionTimer = null;
        mounted?.input.classList.remove('fv-ie-active');
    }
    const crop = () => getDoc().crop;
    const currentRatio = () => presetRatio(Math.round(crop().w), Math.round(crop().h));
    // Another shape chosen for a new picture on a canvas that already has pictures.
    function newCanvas() {
        if (mode !== 'new' || !getDoc().layers.length || !pendingRatio || pendingRatio === currentRatio()) return null;
        return {...blankCanvasSize(pendingRatio), ratio: pendingRatio};
    }
    function start() {
        // The button is only marked unavailable, so a click while the service is being checked is turned away here.
        const now = session.state();
        if (now.checking || !now.info?.installed) return;
        if (!prompt.trim() || prompt.length > 2000) {
            promptRequired = true; update();
            if (mounted) {
                const {input} = mounted;
                clearAttention(); input.focus(); input.classList.add('fv-ie-active');
                attentionTimer = setTimeout(() => {
                    input.classList.remove('fv-ie-active'); attentionTimer = null;
                }, 300);
            }
            return;
        }
        const chosenLoras = loras?.value() || [];
        return session.start({mode, prompt, references: chosen(), canvas: newCanvas(), ...(chosenLoras.length ? {loras: chosenLoras} : {})});
    }
    function drawEdited(canvas) {
        try {
            const doc = getDoc(), layer = generationContext(doc, null, 'edit').layer;
            const scale = Math.min(1, 128 / (layer ? Math.max(layer.nw, layer.nh) : Math.max(doc.width, doc.height)));
            const image = layer ? renderer.layerImage(doc, layer, scale) : renderer.composite(doc, scale);
            const k = Math.max(canvas.width / image.width, canvas.height / image.height);
            canvas.getContext('2d').drawImage(image, (canvas.width - image.width * k) / 2, (canvas.height - image.height * k) / 2, image.width * k, image.height * k);
        } catch { /* drawn on the next update once its image has loaded */ }
    }
    function renderReferences(refs, disabled) {
        const most = limit(), list = chosen();
        refs.hidden = mode !== 'edit' || most < 2 || !(library.length || list.length);
        refs.replaceChildren();
        if (refs.hidden) return;
        const slot = (number, picture, name) => {
            const item = el('span', null, 'fv-ie-gen-ref'); item.setAttribute('role', 'listitem');
            item.setAttribute('aria-label', name);
            item.append(picture, el('b', String(number)));
            return item;
        };
        const edited = el('canvas'); edited.width = edited.height = 56; drawEdited(edited);
        refs.append(slot(1, edited, t('Image 1', '图 1')));
        list.forEach((reference, index) => {
            const picture = el('img'); picture.src = resolve(reference.file); picture.alt = '';
            const item = slot(index + 2, picture, `${t('Image', '图')} ${index + 2}`);
            const remove = button('×', t('Remove', '移除'), () => { references = removeReference(references, reference.file); update(); }, 'fv-ie-gen-ref-remove');
            remove.setAttribute('aria-label', t('Remove', '移除')); remove.disabled = disabled;
            item.append(remove); refs.append(item);
        });
        const rest = library.filter(item => !list.some(r => r.file === item.file));
        const menu = el('details', null, 'fv-ie-menu fv-ie-gen-ref-add');
        const summary = el('summary'); summary.innerHTML = icon('plus', 16);
        summary.title = t('Reference image', '参考图'); summary.setAttribute('aria-label', summary.title);
        const full = disabled || list.length >= most - 1 || !rest.length;
        summary.setAttribute('aria-disabled', String(full)); if (full) summary.tabIndex = -1;
        summary.onclick = event => { if (full) event.preventDefault(); };
        const choices = el('div', null, 'fv-ie-menu-list');
        for (const item of rest) {
            const choice = button('', null, () => { references = addReference(references, item, most); update(); }, 'fv-ie-menu-item');
            const picture = el('img'); picture.src = resolve(item.file); picture.alt = ''; picture.loading = 'lazy';
            choice.append(picture, el('span', item.label)); choices.append(choice);
        }
        menu.append(summary, choices); refs.append(menu); closeOutside(menu);
    }
    // A menu of the AI row closes on a click anywhere else (the description field too), as every tray does.
    function closeOutside(menu) {
        const away = event => { if (!menu.isConnected) document.removeEventListener('pointerdown', away, true); else if (!menu.contains(event.target)) menu.open = false; };
        menu.addEventListener('toggle', () => { if (menu.open) document.addEventListener('pointerdown', away, true); else document.removeEventListener('pointerdown', away, true); });
    }
    // The works (the works library's own list): the button opens the shared works picker under it. A picture is placed
    // on the canvas, or used as a reference of this edit; deleting, choosing several and the folder are in the picker too.
    function worksButton() {
        const open = el('button', null, 'fv-ie-button fv-ie-gen-works'); open.type = 'button';
        open.innerHTML = icon('image', 16); open.append(el('span', t('Works', '作品库')));
        open.dataset.fvTipName = t('Works', '作品库');
        open.dataset.fvTipUse = t('Place a picture from the works, or use it as a reference', '从作品库放入一张图，或用作参考图');
        open.setAttribute('aria-haspopup', 'dialog'); open.setAttribute('aria-expanded', 'false');
        let picker = null;
        open.onclick = async () => {
            if (picker) { picker.close(); return; }
            const {openWorksPicker} = await import('./image_library.js');
            open.setAttribute('aria-expanded', 'true');
            picker = openWorksPicker({anchor: open, t,
                canReference: () => mode === 'edit' && getDoc().layers.length > 0 && limit() > 1,
                onPlace: (file, name) => onPlace?.(file, name),
                onReference: (file, name) => { references = addReference(references, {file, label: name}, limit()); update(); },
                // Over the editor's side panel, so the picture stays in view.
                over: open.closest('dialog')?.querySelector('.fv-ie-panel') || null,
                onClose: () => { picker = null; open.setAttribute('aria-expanded', 'false'); }});
        };
        return open;
    }
    function update() {
        if (!mounted?.bar.isConnected) return;
        const {input, action, note, meter, fill, group, bar, refs, ratio, size, shape} = mounted, state = session.state();
        const missing = !state.running && state.info && !state.info.installed;
        const disabled = state.running || state.checking || !state.info?.installed;
        const empty = !getDoc().layers.length;
        if (empty) mode = 'new';
        bar.dataset.empty = String(empty);
        if (ratio) {
            if (empty || mode === 'edit') pendingRatio = null;
            const shown = empty || mode === 'new', current = currentRatio();
            shape.hidden = !shown; ratio.hidden = !shown; ratio.disabled = state.running;
            // A canvas of no preset shape is listed first, with its size.
            const own = !empty && !current ? `${Math.round(crop().w)}\u00a0×\u00a0${Math.round(crop().h)}` : '';
            if (mounted.listed !== own) {
                mounted.own.textContent = own;
                ratio.replaceChildren(...(own ? [mounted.own] : []), ...mounted.presets); mounted.listed = own;
            }
            ratio.value = empty ? BLANK_CANVAS_RATIOS.includes(getDoc().ratio) ? getDoc().ratio : blankCanvasRatio(getDoc())
                : pendingRatio || current || 'own';
            bar.dataset.shape = String(shown);
            const tiers = state.info?.sizes?.includes('high') ? ['standard', 'high'] : ['standard'];
            if (!tiers.includes(tier)) tier = 'standard';
            size.hidden = !empty || tiers.length < 2; size.set(tier);
            size.querySelectorAll('button').forEach(b => { b.disabled = state.running; });
        }
        group.querySelectorAll('button').forEach(b => {
            b.disabled = disabled || (empty && b.dataset.value === 'edit');
            b.setAttribute('aria-pressed', String(b.dataset.value === mode));
        }); input.disabled = disabled;
        // Asked to take the focus on opening: as soon as the field can be typed in (the engine has answered).
        if (wantFocus && !disabled) { wantFocus = false; input.focus({preventScroll: true}); }
        if (loras) { if (state.running) loras.element.setAttribute('inert', ''); else loras.element.removeAttribute('inert'); }
        renderReferences(refs, disabled);
        input.placeholder = mode === 'edit' ? getSelection() ? t('What to change in the selection', '要改选区里的什么') : t('What to change', '要改什么') : t('Describe the picture', '描述画面');
        input.setAttribute('aria-label', input.placeholder);
        const pack = missing ? packView(state.info, t) : null;
        action.textContent = pack ? pack.button : state.running ? state.stopping ? t('Stopping', '正在停止') : t('Stop', '停止') : t('Generate', '生成');
        action.classList.toggle('fv-ie-primary', !state.running);
        action.setAttribute('aria-disabled', String(!pack && !state.running && (disabled || !prompt.trim())));
        action.title = !pack && !state.running && !prompt.trim() ? t('Enter a prompt first', '请先填写提示词') : '';
        action.hidden = !!missing && !(pack && pack.button) || !!pack?.noButton;
        const percent = Math.floor(state.fraction * 100) + '%';
        if (state.error && !promptRequired) {
            const failureKey = `${state.error}:${state.report_id || ''}:${state.stage || ''}:${state.memory || ''}`;
            if (mounted.failureKey !== failureKey) {
                renderImageFailure(note, state, t, {productVersion, fetchApi});
                mounted.failureKey = failureKey;
            }
        } else {
            mounted.failureKey = null;
            note.textContent = pack ? pack.note
                : missing ? imagePackUnavailableMessage(state.info, t)
                // A trigger word a LoRA adds in front can take the prompt past what the engine accepts.
                : promptRequired ? (prompt.length > 2000
                    ? t(`The prompt is over 2,000 characters. Shorten it by ${prompt.length - 2000} and try again.`, `提示词超过 2000 字。请缩短 ${prompt.length - 2000} 字后重试。`)
                    : t('Enter a prompt first.', '请先填写提示词。'))
                : state.running && state.waiting ? t(...(QUEUE_LABELS[state.waiting] || QUEUE_LABELS.image))
                : state.running && state.fitted ? t(...memoryWords([`Not enough GPU memory. Retrying at ${state.fitted.width}\u00a0×\u00a0${state.fitted.height}`,
                    `显存不足，改用 ${state.fitted.width}\u00a0×\u00a0${state.fitted.height} 重试`], state.info?.unified_memory)) + ' · ' + percent
                : state.running && state.stage === 'upload' ? t('Uploading reference images', '正在上传参考图')
                : state.running ? (state.stage === 'load' ? t('Loading the model', '加载模型') + ' '
                    : state.stage === 'encode' ? t('Reading reference images', '读取参考图') + ' ' : '') + percent
                : state.fitted ? fittedNote(state.fitted, t, state.info?.unified_memory) : '';
        }
        note.hidden = !note.textContent;
        bar.dataset.running = String(state.running);
        // A queue note is a sentence, not a progress figure: it may wrap (image_editor.css).
        bar.dataset.waiting = String(!!(state.running && state.waiting) && !pack && !missing && !promptRequired);
        bar.dataset.message = String(!!missing || promptRequired || !!state.error || !!note.textContent);
        bar.dataset.failed = String(!!state.error && !promptRequired);
        bar.dataset.pack = String(!!pack && !pack.reason && !state.error);
        const shown = pack ? pack.fraction : state.fraction;
        meter.hidden = pack ? pack.fraction == null : !state.running || !!state.waiting;
        meter.setAttribute('aria-valuenow', String(Math.floor(shown * 100)));
        fill.style.width = shown * 100 + '%';
    }
    function mount(bar) {
        bar.classList.add('fv-ie-generation');
        const group = segmented([['edit', t('Edit this', '改这张')], ['new', t('New image', '生成一张')]], mode,
            value => { mode = value; update(); mounted?.grow(); }, t('Generate with AI', 'AI 生成')).group;
        let ratio = null, size = null, shape = null, own = null, presets = [];
        {
            shape = el('div', null, 'fv-ie-gen-shape');
            ratio = el('select', null, 'fv-ie-select'); ratio.id = 'fv-ie-gen-ratio';
            ratio.setAttribute('aria-label', t('Shape', '比例'));
            presets = BLANK_CANVAS_RATIOS.map(value => { const option = el('option', value); option.value = value; return option; });
            ratio.append(...presets);
            const seg = segmented([['standard', t('Standard', '标准')], ['high', t('HD', '高清')]], tier,
                value => { tier = value; changed(); }, t('Size', '尺寸'));
            size = seg.group; size.id = 'fv-ie-gen-size'; size.set = seg.set;
            own = el('option'); own.value = 'own';
            const changed = () => {
                if (!getDoc().layers.length) { if (!session.state().running) onRatioChange?.(ratio.value, tier); }
                else pendingRatio = ratio.value === 'own' ? null : ratio.value;
                update();
            };
            ratio.onchange = changed;
            shape.append(skinSelect(ratio), size);
        }
        const field = el('div', null, 'fv-ie-gen-field');
        const refs = el('div', null, 'fv-ie-gen-refs'); refs.setAttribute('role', 'list');
        refs.setAttribute('aria-label', t('Reference images', '参考图'));
        const box = el('div', null, 'fv-ie-gen-box');
        const input = el('textarea', null, 'fv-ie-gen-prompt'); input.rows = 1; input.maxLength = 2000; input.value = prompt;
        const fold = el('div', null, 'fv-ie-gen-fold'); fold.setAttribute('aria-hidden', 'true');
        box.append(input, fold);
        loras ||= createImageLoras({t, fetchApi, el, onChange: () => mounted && update(), getPrompt: () => prompt,
            setPrompt: value => { prompt = value; if (mounted) { mounted.input.value = value; mounted.grow(); } }});
        if (!loras.element.fvCloseOutside) { loras.element.fvCloseOutside = true; closeOutside(loras.element); }
        field.append(refs, box, worksButton(), loras.element);
        const note = el('div', '', 'fv-ie-opt-note fv-ie-gen-note'); note.setAttribute('role', 'status');
        const action = button(t('Generate', '生成'), null, () => {
            const now = session.state(), pack = !now.running && now.info && !now.info.installed ? packView(now.info, t) : null;
            if (pack && !pack.button) return;
            if (pack) void session.pack(pack.loading && !pack.waiting ? 'cancel' : 'install');
            else if (now.running) void stop(); else void start();
        }, 'fv-ie-button fv-ie-primary');
        // Stop asks first (the same glass confirm as everywhere); leaving the editor never stops the job.
        async function stop() {
            const now = session.state(); if (now.stopping) return;
            if (tasks?.confirmStop && !await tasks.confirmStop(action, {kind: 'image', queued: !!now.waiting})) return;
            if (session.state().running) void session.cancel();
        }
        const meter = el('div', null, 'fv-ie-gen-meter'), fill = el('i'); meter.append(fill);
        meter.setAttribute('role', 'progressbar'); meter.setAttribute('aria-label', t('Generate with AI', 'AI 生成'));
        meter.setAttribute('aria-valuemin', '0'); meter.setAttribute('aria-valuemax', '100');
        bar.append(group);
        bar.append(shape);
        bar.append(field, note, action, meter);
        const otherUsage = createOtherUsageNotice(bar, t, fetchApi);
        const grow = () => {
            input.style.height = '0px';
            const open = prompt.trim() && input.ownerDocument.activeElement === input;
            input.style.height = (open ? Math.min(80, Math.max(32, input.scrollHeight + 2)) : 32) + 'px';
            if (!open) input.scrollTop = 0;
            const folded = !open && !!prompt.trim();
            box.dataset.folded = String(folded);
            if (folded) { fold.textContent = input.value.replace(/\s*\n\s*/g, ' '); fold.style.font = input.ownerDocument.defaultView?.getComputedStyle?.(input).font || ""; }
        };
        input.addEventListener('focus', () => grow());
        input.addEventListener('blur', () => grow());
        input.oninput = () => {
            prompt = input.value;
            if (prompt.trim() && prompt.length <= 2000) promptRequired = false;
            clearAttention(); session.clearError(); grow();
        };
        input.onkeydown = event => {
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !event.ctrlKey && !event.metaKey) {
                event.preventDefault(); void start();
            }
        };
        mounted = {bar, input, action, note, meter, fill, group, refs, ratio, size, shape, own, presets, listed: '', otherUsage, grow};
        grow(); update();
    }
    return {mount, drawsSelection: () => mode === 'edit' && getDoc().layers.length > 0, unmount() { clearAttention(); promptRequired = false; mounted?.otherUsage.dispose(); mounted = null; },
        selected: () => session.check(), cancel: session.cancel,
        focusPrompt() { wantFocus = true; update(); },
        // The blank canvas a reloaded generation was started on, to be set again by the editor.
        resumedCanvas: resumed?.context?.mode === 'new'
            ? {width: resumed.context.doc.width, height: resumed.context.doc.height} : null,
        // Closing only stops watching: a job still running goes on; its picture is a draft that opening the same picture
        // again takes back (and that is deleted if no editor does).
        dispose() { clearAttention(); loras?.dispose(); mounted?.otherUsage.dispose(); return session.dispose(); }, state: session.state};
}
