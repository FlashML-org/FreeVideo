// Image generation's wire adapter, cancellable session and options UI. The editor
// owns its document/history; this module inserts a result through its commands.
import * as commands from './image_commands.js';
import {orientMatrix, rasterKinds, scaleSelection} from './image_editor_engine.js';
import {saveReport, reportIssue, showReportTip, focusReportTip} from './report_issue.js';
import {issueButton} from './issue_button.js';
import {about} from './request_assets.js';

export const IMAGE_GEN_PROTOCOL = Object.freeze({
    root: '/freevideo/image-gen', header: 'X-FreeVideo-Image', upload: '/upload/image',
    installed: 'ready', phase: 'phase', bytes: 'bytes', downloading: 'downloading', checking: 'checking',
    token: 'token', job: 'job', prompt: 'prompt', width: 'width', height: 'height', images: 'images', mask: 'mask', seed: 'seed',
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
const failure = (code, info = {}) => Object.assign(new Error(code || 'generation_failed'), {
    code: code || 'generation_failed', report_id: info.report_id || null, stage: info.stage || '',
    fitted: info.fitted || null, memory: info.memory || '',
});

export function createImageGenClient(fetchImpl = globalThis.fetch.bind(globalThis)) {
    let token = '';
    const p = IMAGE_GEN_PROTOCOL;
    async function request(path, options) {
        const response = await fetchImpl(path, options);
        const value = await response.json();
        if (!response.ok || value[p.error] && !value[p.phase] && !value.state) throw failure(value[p.error], value);
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
    async function info() {
        const value = await request(p.root, {cache: 'no-store'});
        token = value[p.token];
        return {...value, installed: value.installed ?? value[p.installed] ?? false};
    }
    return {
        info,
        async generate({prompt, width, height, images = [], mask, seed, cast_view}) {
            const value = await post('generate', {[p.prompt]: prompt, [p.width]: width, [p.height]: height,
                [p.images]: images, ...(mask ? {[p.mask]: mask} : {}), ...(seed === undefined ? {} : {[p.seed]: seed}),
                ...(cast_view === 'back' ? {cast_view} : {})});
            return {...value, job: value[p.job]};
        },
        async status(job) {
            const value = await post('status', {[p.job]: job});
            const phase = value[p.phase];
            return {...value, state: value.state || p.states[phase] || 'error',
                stage: value[p.stage] || (phase === 'starting' ? 'load' : ''),
                waiting: phase === 'queued' ? value.queued_behind || 'image' : '', fitted: value.fitted || null,
                fraction: value[p.fraction], file: value[p.file], error: value[p.error],
                ...(phase === 'cancelled' ? {error: 'cancelled'} : {})};
        },
        cancel: job => post('cancel', {[p.job]: job}),
        // The image models are fetched by this page's own engine; the reply is the new info.
        async packInstall() { const value = await post('pack-install', {}); return {...value, installed: value.installed ?? value[p.installed] ?? false}; },
        async packCancel() { const value = await post('pack-cancel', {}); return {...value, installed: value.installed ?? value[p.installed] ?? false}; },
        // Click select: the engine finds the object under a point; the pack it needs is fetched on demand like the image models.
        async segment({image, points}) { return post('segment', {image, points}); },
        segmentInstall: () => post('segment-install', {}),
        segmentCancel: () => post('segment-cancel', {}),
        // Every picture generated on this machine, newest first; the engine keeps them in a folder.
        async history() { return (await post('history', {})).items || []; },
        async historyDelete(file) { return (await post('history-delete', {file})).items || []; },
        async historyDeleteMany(files) { const answer = await post('history-delete-many', {files}); return {items: answer.items || [], failed: answer.failed || []}; },
        historyOpen: () => post('history-open', {}),
        // The prompts and sizes of a new character's five pictures (the Agent's cards use the same ones).
        castViews: async description => (await post('cast-views', {description})).views,
        async upload(file) {
            // ComfyUI's existing image upload stores this at input/<unique name>.
            // FreeVideo's media upload uses subdirectories the image worker rejects.
            const body = new FormData(); body.append('image', file);
            body.append('type', 'input'); body.append('overwrite', 'false');
            const value = await request(p.upload, {method: 'POST', body});
            if (!value[p.name] || value[p.subfolder] || /[/\\:]/.test(value[p.name])) throw failure('invalid_media');
            return value[p.name];
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
        return {loading: true, button: t('Pause', '暂停'), fraction: total > 0 ? Math.min(1, (install.done || 0) / total) : 0,
            note: waiting ? t('Paused while a video generates', '视频生成中，已暂停')
                : t(`Downloading · ${gb(install.done || 0)}\u00a0GB / ${size}${time}`, `正在下载 · ${gb(install.done || 0)}\u00a0GB / ${size}${time}`)};
    }
    const resume = install.state === 'failed' || install.state === 'cancelled' || install.done > 0;
    const reason = install.state === 'failed' ? t(install.message_en, install.message_zh) : '';
    return {loading: false, fraction: null, reason: !!reason, button: resume ? t('Resume download', '继续下载') : t('Download image generation models', '下载图像生成模型'),
        note: reason || (resume ? t(`${gb(install.done || 0)}\u00a0GB / ${size}${time}`, `${gb(install.done || 0)}\u00a0GB / ${size}${time}`) : size + time)};
}
export function imageGenError(code, info, t) {
    const reasons = {
        no_memory: ['Not enough GPU memory, even at the smallest size. Close other apps using the GPU, then try again.',
            '显存不够，最小尺寸也没能生成。关掉其他占用显卡的程序后再试。'],
        busy_video: ['A video is generating. Try again when it finishes.', '视频正在生成，完成后再试。'],
        busy: ['An image is already being generated. Try again when it finishes.', '已有一张图在生成，完成后再试。'],
        model_unreadable: ['The image generation model files could not be read. Repair them in the launcher and try again.',
            '图像生成模型的文件读不出来。在启动器里修复后再试。'],
        device_unavailable: ['The graphics card is not available right now. Try again later.', '显卡暂时用不了，稍后再试。'],
        worker_exit: ['Image generation stopped unexpectedly. Try again.', '图像生成意外停止了，请再试一次。'],
        timeout: ['This image took too long and was stopped. Try again.', '这一张用时太长，已停止。请再试一次。'],
        disk_full: ['Saving the image failed: the disk is full. Free some space, then generate again.', '保存图片时出错：磁盘空间不够，空出空间后再生成。'],
        cancelled: ['Image generation was cancelled.', '已取消图像生成。'],
        invalid_request: ['The image settings are invalid. Adjust them and try again.', '图片设置有误，请调整后重试。'],
        invalid_media: ['The reference image cannot be read. Choose another image.', '参考图读不出来，请换一张图。'],
        reload: ['Image generation was interrupted. Try again.', '图片生成中途被打断，请重试。'],
        generation_failed: ['Image generation ran into an error. Try again.', '图像生成出错了，请再试一次。'],
        small_memory: ['This Mac has less than 16 GB of memory and cannot generate pictures.',
            '这台 Mac 的内存不足 16 GB，无法生成图片。'],
    };
    const say = (en, zh) => t(...memoryWords([en, zh], info?.unified));
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
                '显存不够，参考图没能载入。少用几张参考图，或关掉其他占用显卡的程序后再试。');
        if (stage === 'load' || stage === 'text')
            return say('Not enough GPU memory to load the model. Close other apps using the GPU, then try again.',
                '显存不够，模型没能载入。关掉其他占用显卡的程序后再试。');
        if (size?.width && size?.height)
            return say(`Not enough GPU memory, even at ${size.width}\u00a0×\u00a0${size.height}. Close other apps using the GPU, then try again.`,
                `显存不够，${size.width}\u00a0×\u00a0${size.height} 也没能生成。关掉其他占用显卡的程序后再试。`);
    }
    return say(...(Object.hasOwn(reasons, code) ? reasons[code] : reasons.generation_failed));
}

// Keep image failure copy here for editorial review. The submit card and its
// wording belong to report_issue.js, shared with video reports.
// Queue and size-fit notes, kept with the failure copy for editorial review.
export const QUEUE_LABELS = Object.freeze({
    image: ['Added to the queue. It starts when the previous one finishes.', '已加入队列，前面的完成后自动开始。'],
    video: ['Queued. It starts when the video finishes.', '已排队，视频生成完后自动开始。'],
});
export const fittedNote = (size, t, unified = false) => t(...memoryWords([`Not enough GPU memory. Made at ${size.width}\u00a0×\u00a0${size.height} instead.`,
    `显存不足，已改为 ${size.width}\u00a0×\u00a0${size.height} 生成。`], unified));
export const REPORT_LABELS = Object.freeze({
    download: ['Diagnostic report', '诊断报告'],
    description: ['Diagnostic report: hardware, settings and timings', '诊断报告：硬件、设置和耗时'],
    preparing: ['Preparing…', '正在整理…'],
    unavailable: ['Report unavailable · retry', '报告暂不可用 · 重试'],
});

// Shared by the editor and the other image entry points. Only the opaque report
// ID goes in the request; raw service messages are never shown in the page.
export function renderImageFailure(host, {error, report_id, stage, fitted, info, memory} = {}, t,
    {productVersion = () => null, fetchApi = globalThis.fetch.bind(globalThis)} = {}) {
    host.replaceChildren();
    if (!error || error === 'cancelled') return null;
    const doc = host.ownerDocument || document;
    const cssURL = new URL('./result_actions.css', import.meta.url).href;
    if (!doc.querySelector(`link[href="${cssURL}"]`)) {
        const css = doc.createElement('link'); css.rel = 'stylesheet'; css.href = cssURL; doc.head.append(css);
    }
    const card = doc.createElement('div'); card.className = 'fv-result-links';
    const reason = doc.createElement('div'); reason.className = 'fv-image-failure-reason';
    reason.setAttribute('role', 'status'); reason.textContent = imageGenError(error, {stage, fitted, memory, unified: info?.unified_memory}, t);
    card.append(reason); host.append(card);
    if (!report_id) return card;
    const fetchReport = async () => {
        const response = await fetchApi(`${IMAGE_GEN_PROTOCOL.root}/report/${encodeURIComponent(report_id)}`, {cache: 'no-store'});
        if (!response.ok) throw new Error('report_unavailable');
        return response.blob();
    };
    const version = () => typeof productVersion === 'function' ? productVersion() : productVersion;
    const bar = doc.createElement('div'); bar.className = 'fv-actions';
    const anchor = doc.createElement('button'); anchor.type = 'button';
    anchor.className = 'fv-action fv-action-secondary fv-action-report';
    anchor.textContent = t(...REPORT_LABELS.download); anchor.title = t(...REPORT_LABELS.description);
    const issue = issueButton(t, () => ({where: ['Image diagnostic report', '生图诊断报告'], message: t(...REPORT_LABELS.unavailable)}));
    issue.hidden = true;
    anchor.onclick = async () => {
        if (anchor.disabled) return;
        if (focusReportTip(bar)) return;
        const restoreFocus = doc.activeElement === anchor;
        issue.hidden = true;
        anchor.disabled = true; anchor.textContent = t(...REPORT_LABELS.preparing);
        try {
            await saveReport(await fetchReport());
            if (card.isConnected) showReportTip(bar, anchor, t, {mode: 'downloaded',
                submit: () => reportIssue(fetchReport, version(), {save: false}), onHide: () => anchor.focus()});
            anchor.textContent = t(...REPORT_LABELS.download);
        } catch { anchor.textContent = t(...REPORT_LABELS.unavailable); issue.hidden = false; }
        finally {
            anchor.disabled = false;
            if (restoreFocus && card.isConnected && doc.activeElement === doc.body) anchor.focus({preventScroll: true});
        }
    };
    // Let native Enter/Space activate these buttons without invoking editor
    // canvas shortcuts. Tab and the shared card's Escape handling still work.
    card.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') event.stopPropagation();
    });
    bar.append(anchor, issue); card.append(bar);
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

export function generationContext(doc, selection, mode) {
    const layer = mode === 'edit' ? doc.layers.find(l => l.id === doc.active && rasterKinds.has(l.kind)) : null;
    return {doc, selection, layer, mode, ...fitGenerationSize(doc.crop.w, doc.crop.h)};
}
export async function uploadGenerationReference(renderer, client, context, alive = () => true) {
    let blob;
    if (context.layer) {
        await renderer.preload(context.doc);
        if (!alive()) throw failure('cancelled');
        const raster = renderer.layerImage(context.doc, context.layer, 1, true);
        blob = await new Promise((resolve, reject) => raster.toBlob(b => b ? resolve(b) : reject(failure()), 'image/png'));
    } else blob = (await renderer.export(context.doc, 'image/png')).blob;
    if (!alive()) throw failure('cancelled');
    const file = new File([blob], `freevideo-image-reference-${crypto.randomUUID()}.png`, {type: 'image/png'});
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
    const blob = await new Promise((resolve, reject) => mask.toBlob(b => b ? resolve(b) : reject(failure()), 'image/png'));
    if (!alive()) throw failure('cancelled');
    return client.upload(new File([blob], `freevideo-image-mask-${crypto.randomUUID()}.png`, {type: 'image/png'}));
}

// The object under a click, as the name of a black and white picture the size of the layer's pixels.
// The layer is uploaded once for as long as it looks the same.
const segmentUploads = new WeakMap();
export async function segmentAt(renderer, client, doc, layer, point) {
    const look = JSON.stringify([layer.ops.map(o => o.id), layer.adjust, layer.src, layer.mask?.ops.map(o => o.id)]);
    let known = segmentUploads.get(layer);
    if (known?.look !== look) {
        await renderer.preload(doc);
        const raster = renderer.layerImage(doc, layer, 1, true);
        const blob = await new Promise((resolve, reject) => raster.toBlob(b => b ? resolve(b) : reject(failure()), 'image/png'));
        known = {look, name: await client.upload(new File([blob], `freevideo-image-reference-${crypto.randomUUID()}.png`, {type: 'image/png'})), width: raster.width, height: raster.height};
        segmentUploads.set(layer, known);
    }
    const x = Math.min(known.width - 1, Math.max(0, Math.floor(point[0] * known.width))), y = Math.min(known.height - 1, Math.max(0, Math.floor(point[1] * known.height)));
    const value = await client.segment({image: known.name, points: [[x, y, 1]]});
    if (typeof value?.mask !== 'string') throw failure();
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
// first reference once and its copy is used again for later requests.
export async function uploadExtraReferences(client, references, render, copies, alive = () => true) {
    const names = [];
    for (const item of references) {
        if (!copies.has(item.file)) {
            const blob = await render(item.file);
            if (!alive()) throw failure('cancelled');
            const name = await client.upload(new File([blob], `freevideo-image-reference-${crypto.randomUUID()}.png`, {type: 'image/png'}));
            if (!alive()) throw failure('cancelled');
            copies.set(item.file, name);
        }
        names.push(copies.get(item.file));
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

// The worker counts every stage from 0 to 1. One bar for the whole picture: each stage owns a
// slice in the order it runs (the reference encode only exists with reference images), and the
// bar stays below 100% until the result is placed.
const STAGE_SLICES = {
    new: [['load', .15], ['text', .15], ['denoise', .5], ['decode', .2]],
    edit: [['load', .15], ['text', .15], ['encode', .05], ['denoise', .45], ['decode', .2]],
};
export function imageGenProgress(stage, fraction, withImages = false) {
    const own = Math.min(1, Math.max(0, Number.isFinite(fraction) ? fraction : 0));
    let start = 0;
    for (const [name, weight] of STAGE_SLICES[withImages ? 'edit' : 'new']) {
        if (name === stage) return Math.min(.99, start + weight * own);
        start += weight;
    }
    return Math.min(.99, own);
}

// An epoch rejects late uploads/status/results. A generate response arriving
// after Stop still has to be cancelled, even though its job was not known yet.
export function createImageGenSession({client, prepare, apply, onChange = () => {},
    schedule = setTimeout, unschedule = clearTimeout, store = null,
    visible = () => globalThis.document?.visibilityState !== 'hidden'}) {
    let epoch = 0, timer = null, infoTimer = null, disposed = false, checking = null;
    let state = {info: null, checking: false, running: false, job: null, fraction: 0, stage: '', error: '', report_id: null,
        waiting: '', fitted: null};
    const emit = patch => { state = {...state, ...patch}; if (!disposed) onChange(state); };
    const failed = error => error.code === 'cancelled' ? {error: '', report_id: null, stage: '', memory: ''}
        : {error: error.code || 'generation_failed', report_id: error.report_id || null, stage: error.stage || '',
            memory: error.memory || '', ...(error.fitted ? {fitted: error.fitted} : {})};
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
    // One job from its first status to the result. A job taken up again after the page was
    // reloaded (``resumed``) that the service no longer knows ends quietly: there is nothing to say.
    function track(current, job, context, withImages, resumed = false) {
        const alive = () => current === epoch && !disposed;
        async function poll() {
            try {
                const row = await client.status(job);
                if (!alive()) return;
                const fraction = imageGenProgress(row.stage, row.fraction, withImages);
                emit({fraction: Math.max(state.fraction, fraction), stage: row.stage || '', waiting: row.waiting || '',
                    fitted: row.fitted || state.fitted});
                if (row.state === 'done') {
                    if (!row.file) throw failure();
                    await apply(row, context, alive);
                    if (alive()) { forget(); emit({running: false, job: null, fraction: 1, stage: '', waiting: ''}); }
                } else if (row.state === 'error') throw failure(row.error, row);
                else timer = schedule(() => { timer = null; void poll(); }, IMAGE_GEN_POLL_MS);
            } catch (error) {
                if (alive()) {
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
    async function start(request) {
        if (state.running || disposed || !request.prompt.trim() || !state.info?.installed) return;
        const current = ++epoch;
        // A picture that is edited sends its reference images first; there is no job to report on yet.
        emit({running: true, job: null, fraction: 0, stage: request.mode === 'edit' ? 'upload' : '', error: '', report_id: null, memory: '', waiting: '', fitted: null});
        const alive = () => current === epoch && !disposed;
        try {
            const {context, images = [], mask} = await prepare(request, alive);
            if (!alive()) return;
            if (state.stage === 'upload') emit({stage: ''});
            const withImages = images.length > 0;
            const row = await client.generate({prompt: request.prompt, width: context.width, height: context.height, images, mask});
            const {job} = row;
            if (!job) throw failure(row.error, row);
            if (row.fitted) emit({fitted: row.fitted});
            if (!alive()) { await cancelJob(job); return; }
            emit({job});
            try { store?.save({job, context, withImages}); } catch { /* the job is only not resumed after a reload */ }
            track(current, job, context, withImages);
        } catch (error) {
            if (alive()) emit({running: false, job: null, ...failed(error)});
        }
    }
    // The page was reloaded while a picture was being made: follow the same job and place its result.
    function resume({job, context, withImages = false}) {
        if (state.running || disposed || !job || !context) return false;
        const current = ++epoch;
        emit({running: true, job, fraction: 0, stage: '', error: '', report_id: null, memory: '', waiting: '', fitted: null});
        track(current, job, context, withImages, true);
        return true;
    }
    function cancel() {
        ++epoch; unschedule(timer); timer = null;
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
        dispose() { disposed = true; unschedule(infoTimer); infoTimer = null; return cancel(); }};
}

// The job being made, kept for this tab only so that a reloaded page can follow it and place the
// result. Only what placing needs is kept (never the document), and a record for another editor or
// older than the service keeps its results is left alone.
export const IMAGE_JOB_KEEP_MS = 30 * 60 * 1000;
export function imageJobStore(key, storage = globalThis.sessionStorage, now = Date.now, name = 'freevideo.imageGenJob') {
    return {
        save({job, context, withImages}) {
            const {layer, selection} = context;
            const keep = layer && {id: layer.id, cx: layer.cx, cy: layer.cy, w: layer.w, h: layer.h,
                rotation: layer.rotation, flipX: layer.flipX, flipY: layer.flipY};
            storage.setItem(name, JSON.stringify({key, job, withImages: !!withImages, at: now(), context: {
                mode: context.mode, doc: {width: context.doc.width, height: context.doc.height},
                layer: keep || null, selection: selection || null}}));
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
    library = [], resolve = file => file, icon = () => '', onRatioChange, onPlace, productVersion, fetchApi, resumeKey = null}) {
    let mode = 'edit', prompt = '', mounted = null, references = [], tier = 'standard', past = [];
    let promptRequired = false, attentionTimer = null;
    const copies = new Map();
    // A reference is sent no larger than the largest picture the service makes.
    async function referencePNG(file) {
        const image = await renderer.image(file);
        const k = Math.min(1, 2048 / Math.max(image.naturalWidth, image.naturalHeight));
        const canvas = el('canvas'); canvas.width = Math.max(1, Math.round(image.naturalWidth * k)); canvas.height = Math.max(1, Math.round(image.naturalHeight * k));
        canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
        return new Promise((done, fail) => canvas.toBlob(b => b ? done(b) : fail(failure()), 'image/png'));
    }
    let store = null;
    try { if (resumeKey != null && globalThis.sessionStorage) store = imageJobStore(resumeKey); } catch { /* no storage */ }
    const session = createImageGenSession({client, store,
        prepare: async (request, alive) => {
            const context = generationContext(getDoc(), getSelection(), request.mode);
            const images = request.mode === 'edit' ? [await uploadGenerationReference(renderer, client, context, alive),
                ...await uploadExtraReferences(client, request.references || [], referencePNG, copies, alive)] : [];
            // Only the selected part of the edited picture (the first) is changed.
            const mask = images.length ? await uploadGenerationMask(renderer, client, context, alive) : null;
            return {context: mask ? {...context, region: true} : context, images, mask};
        },
        apply: async (result, context, alive) => {
            const image = await renderer.image(result.file);
            if (!alive()) return;
            renderer.images.get(result.file).value = image;
            commit(insertGenerationResult(getDoc(), {src: result.file, nw: image.naturalWidth, nh: image.naturalHeight}, context, t));
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
    function start() {
        // The button is only marked unavailable, so a click while the service is being checked is turned away here.
        const now = session.state();
        if (now.checking || !now.info?.installed) return;
        if (!prompt.trim()) {
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
        return session.start({mode, prompt, references: chosen()});
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
        menu.append(summary, choices); refs.append(menu);
    }
    // Every picture made on this machine: place one on the canvas, use it as a reference, delete it,
    // or show the folder that holds them.
    function historyMenu() {
        const menu = el('details', null, 'fv-ie-menu fv-ie-gen-history');
        const summary = el('summary'); summary.innerHTML = icon('history', 16);
        summary.title = t('History', '历史'); summary.setAttribute('aria-label', summary.title);
        const list = el('div', null, 'fv-ie-menu-list');
        menu.append(summary, list);
        // Choosing several: the ticked files, the reason each one that stayed gives, and the two-step confirm of the delete button.
        let selecting = false, armed = null, failed = false, unopened = null;
        const chosen = new Set(), stayed = new Map();
        const reasons = {busy: t('In use', '被占用'), missing: t('Already gone', '已不存在'), invalid: t('Not deleted', '未删除')};
        const disarm = () => { clearTimeout(armed); armed = null; };
        const draw = () => {
            list.replaceChildren();
            if (failed) list.append(el('div', t('Could not delete this image', '无法删除这张图片'), 'fv-ie-menu-label'));
            if (unopened) {
                list.append(el('div', t('The folder could not be opened automatically. Open it in a file manager.', '无法自动打开该文件夹，请在文件管理器中手动打开'), 'fv-ie-menu-label'));
                if (unopened.folder) list.append(el('div', unopened.folder, 'fv-ie-menu-label fv-ie-gen-history-path'));
            }
            if (!past.length) { list.append(el('div', t('No images generated yet', '还没有生成过图片'), 'fv-ie-menu-label')); return; }
            const grid = el('div', null, 'fv-ie-gen-history-grid');
            for (const item of past) {
                const tile = el('div', null, 'fv-ie-gen-history-tile');
                const ticked = chosen.has(item.file);
                const place = button('', item.name, () => {
                    if (!selecting) { menu.open = false; onPlace?.(item.file, item.name); return; }
                    if (chosen.has(item.file)) chosen.delete(item.file); else chosen.add(item.file);
                    stayed.delete(item.file); disarm(); draw();
                }, 'fv-ie-gen-history-pick' + (ticked ? ' fv-ie-gen-history-ticked' : ''));
                if (selecting) place.setAttribute('aria-pressed', String(ticked));
                const picture = el('img'); picture.src = resolve(item.file); picture.alt = item.name; picture.loading = 'lazy';
                place.append(picture); tile.append(place);
                if (selecting) {
                    tile.append(el('span', ticked ? '✓' : '', 'fv-ie-gen-history-tick' + (ticked ? ' fv-ie-gen-history-on' : '')));
                    if (stayed.has(item.file)) tile.append(el('span', stayed.get(item.file), 'fv-ie-gen-history-why'));
                    grid.append(tile);
                    continue;
                }
                if (mode === 'edit' && getDoc().layers.length && limit() > 1) {
                    const use = button('+', t('Use as reference', '用作参考图'), () => {
                        references = addReference(references, {file: item.file, label: item.name}, limit()); menu.open = false; update();
                    }, 'fv-ie-gen-history-use');
                    use.setAttribute('aria-label', use.title); tile.append(use);
                }
                let single = null;
                const remove = button('×', t('Delete', '删除'), async () => {
                    if (!single) {
                        remove.textContent = t('Delete', '删除'); remove.classList.add('fv-ie-gen-history-confirm');
                        single = setTimeout(() => { single = null; remove.textContent = '×'; remove.classList.remove('fv-ie-gen-history-confirm'); }, 3000);
                        return;
                    }
                    clearTimeout(single);
                    try { past = await client.historyDelete(item.file); failed = false; } catch {
                        // Read the folder again: a picture that is gone just leaves the list, one that is still there is named as kept.
                        failed = true;
                        try { past = await client.history(); failed = past.some(row => row.file === item.file); } catch { /* keep the list shown */ }
                    }
                    draw();
                }, 'fv-ie-gen-ref-remove fv-ie-gen-history-delete');
                remove.setAttribute('aria-label', remove.title); tile.append(remove);
                grid.append(tile);
            }
            list.append(grid);
            const foot = el('div', null, 'fv-ie-gen-history-foot');
            if (selecting) {
                const all = button(chosen.size === past.length ? t('Deselect all', '全不选') : t('Select all', '全选'), null, () => {
                        if (chosen.size === past.length) chosen.clear(); else for (const row of past) chosen.add(row.file);
                        stayed.clear(); disarm(); draw();
                    }, 'fv-ie-button');
                const del = button(armed ? t('Confirm delete', '确认删除') : t('Delete ' + chosen.size, '删除 ' + chosen.size + ' 张'), null, async () => {
                        if (!chosen.size) return;
                        if (!armed) { armed = setTimeout(() => { armed = null; draw(); }, 3000); draw(); return; }
                        disarm();
                        let answer;
                        try { answer = await client.historyDeleteMany([...chosen]); } catch { answer = null; }
                        stayed.clear(); failed = false;
                        if (!answer) {
                            try { past = await client.history(); } catch { /* keep the list shown */ }
                            for (const file of chosen) if (past.some(row => row.file === file)) stayed.set(file, reasons.invalid);
                        } else {
                            past = answer.items;
                            for (const row of answer.failed) if (row.reason !== 'missing') stayed.set(row.file, reasons[row.reason] || reasons.invalid);
                        }
                        for (const file of [...chosen]) if (!stayed.has(file)) chosen.delete(file);
                        draw();
                    }, 'fv-ie-button fv-ie-danger fv-ie-gen-history-delete-all' + (armed ? ' fv-ie-gen-history-confirm' : '') + (chosen.size ? '' : ' fv-ie-gen-history-idle'));
                const cancel = button(t('Cancel', '取消'), null, () => { selecting = false; chosen.clear(); stayed.clear(); disarm(); draw(); }, 'fv-ie-button');
                foot.append(all, el('span', null, 'fv-ie-gen-history-foot-end'));
                foot.lastChild.append(cancel, del);
            } else {
                foot.append(button(t('Select', '选择'), null, () => { selecting = true; failed = false; draw(); }, 'fv-ie-menu-item'));
                if (['localhost', '127.0.0.1', '[::1]'].includes(location.hostname))
                    foot.append(button(t('Open folder', '打开文件夹'), null, async () => {
                        let answer; try { answer = await client.historyOpen(); } catch { answer = {opened: false}; }
                        unopened = answer.opened === false ? {folder: answer.folder || ''} : null; draw();
                    }, 'fv-ie-menu-item'));
            }
            list.append(foot);
        };
        menu.ontoggle = async () => {
            if (!menu.open) return;
            try { past = await client.history(); } catch { past = []; }
            selecting = false; chosen.clear(); stayed.clear(); disarm(); failed = false; unopened = null;
            draw();
            // Open upward when the list does not fit below, and keep it inside the window.
            list.classList.remove('fv-ie-gen-history-up'); list.style.right = '';
            const box = list.getBoundingClientRect();
            if (box.bottom > innerHeight - 8 && menu.getBoundingClientRect().top > box.height) list.classList.add('fv-ie-gen-history-up');
            if (box.left < 8) list.style.right = (box.left - 8) + 'px';
        };
        return menu;
    }
    function update() {
        if (!mounted?.bar.isConnected) return;
        const {input, action, note, meter, fill, group, bar, refs, ratio, size} = mounted, state = session.state();
        const missing = !state.running && state.info && !state.info.installed;
        const disabled = state.running || state.checking || !state.info?.installed;
        const empty = !getDoc().layers.length;
        if (empty) mode = 'new';
        bar.dataset.empty = String(empty);
        if (ratio) {
            ratio.hidden = !empty; ratio.disabled = state.running;
            ratio.value = BLANK_CANVAS_RATIOS.includes(getDoc().ratio) ? getDoc().ratio : blankCanvasRatio(getDoc());
            const tiers = state.info?.sizes?.includes('high') ? ['standard', 'high'] : ['standard'];
            if (!tiers.includes(tier)) tier = 'standard';
            size.hidden = !empty || tiers.length < 2; size.set(tier);
            size.querySelectorAll('button').forEach(b => { b.disabled = state.running; });
        }
        group.querySelectorAll('button').forEach(b => {
            b.disabled = disabled || (empty && b.dataset.value === 'edit');
            b.setAttribute('aria-pressed', String(b.dataset.value === mode));
        }); input.disabled = disabled;
        renderReferences(refs, disabled);
        input.placeholder = mode === 'edit' ? getSelection() ? t('What to change in the selection', '要改选区里的什么') : t('What to change', '要改什么') : t('Describe the picture', '描述画面');
        input.setAttribute('aria-label', input.placeholder);
        const pack = missing ? packView(state.info, t) : null;
        action.textContent = pack ? pack.button : state.running ? t('Stop', '停止') : t('Generate', '生成');
        action.classList.toggle('fv-ie-primary', !state.running);
        action.setAttribute('aria-disabled', String(!pack && !state.running && (disabled || !prompt.trim())));
        action.title = !pack && !state.running && !prompt.trim() ? t('Enter a prompt first', '请先填写提示词') : '';
        action.hidden = !!missing && !pack;
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
                : promptRequired ? t('Enter a prompt first.', '请先填写提示词。')
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
            value => { mode = value; update(); }, t('Generate with AI', 'AI 生成')).group;
        let ratio = null, size = null, shape = null;
        if (!getDoc().layers.length) {
            shape = el('div', null, 'fv-ie-gen-shape');
            ratio = el('select', null, 'fv-ie-select'); ratio.id = 'fv-ie-gen-ratio';
            ratio.setAttribute('aria-label', t('Shape', '比例'));
            for (const value of BLANK_CANVAS_RATIOS) {
                const option = el('option', value); option.value = value; ratio.append(option);
            }
            const seg = segmented([['standard', t('Standard', '标准')], ['high', t('HD', '高清')]], tier,
                value => { tier = value; changed(); }, t('Size', '尺寸'));
            size = seg.group; size.id = 'fv-ie-gen-size'; size.set = seg.set;
            const changed = () => {
                if (!getDoc().layers.length && !session.state().running) onRatioChange?.(ratio.value, tier);
                update();
            };
            ratio.onchange = changed;
            shape.append(ratio, size);
        }
        const field = el('div', null, 'fv-ie-gen-field');
        const refs = el('div', null, 'fv-ie-gen-refs'); refs.setAttribute('role', 'list');
        refs.setAttribute('aria-label', t('Reference images', '参考图'));
        const box = el('div', null, 'fv-ie-gen-box');
        const input = el('textarea', null, 'fv-ie-gen-prompt'); input.rows = 1; input.maxLength = 2000; input.value = prompt;
        box.append(input); field.append(refs, box, historyMenu());
        const note = el('div', '', 'fv-ie-opt-note fv-ie-gen-note'); note.setAttribute('role', 'status');
        const action = button(t('Generate', '生成'), null, () => {
            const now = session.state(), pack = !now.running && now.info && !now.info.installed ? packView(now.info, t) : null;
            if (pack) void session.pack(pack.loading ? 'cancel' : 'install');
            else if (now.running) void session.cancel(); else void start();
        }, 'fv-ie-button fv-ie-primary');
        const meter = el('div', null, 'fv-ie-gen-meter'), fill = el('i'); meter.append(fill);
        meter.setAttribute('role', 'progressbar'); meter.setAttribute('aria-label', t('Generate with AI', 'AI 生成'));
        meter.setAttribute('aria-valuemin', '0'); meter.setAttribute('aria-valuemax', '100');
        bar.append(group);
        if (shape) bar.append(shape);
        bar.append(field, note, action, meter);
        mounted = {bar, input, action, note, meter, fill, group, refs, ratio, size};
        const grow = () => {
            input.style.height = '0px';
            input.style.height = (prompt.trim() ? Math.min(80, Math.max(32, input.scrollHeight + 2)) : 32) + 'px';
        };
        input.oninput = () => {
            prompt = input.value;
            if (prompt.trim()) promptRequired = false;
            clearAttention(); session.clearError(); grow();
        };
        input.onkeydown = event => {
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !event.ctrlKey && !event.metaKey) {
                event.preventDefault(); void start();
            }
        };
        grow(); update();
    }
    return {mount, drawsSelection: () => mode === 'edit' && getDoc().layers.length > 0, unmount() { clearAttention(); promptRequired = false; mounted = null; },
        selected: () => session.check(), cancel: session.cancel,
        // The blank canvas a reloaded generation was started on, to be set again by the editor.
        resumedCanvas: resumed?.context?.mode === 'new'
            ? {width: resumed.context.doc.width, height: resumed.context.doc.height} : null,
        dispose() { clearAttention(); return session.dispose(); }, state: session.state};
}
