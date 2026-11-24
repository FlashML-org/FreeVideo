// One media file into ComfyUI's input folder through FreeVideo's streaming upload; every upload place shares it.
import { isChinese, translator } from './i18n.js';
const cn = isChinese();
const text = translator(cn);
// The page's background loading of works (work_media.js) waits while an upload runs.
const uploadRunning = active => { try { globalThis.dispatchEvent?.(new CustomEvent('freevideo-upload', {detail: {active}})); } catch { /* no page */ } };
// The server's limit; a bigger file is turned away before a byte is sent.
export const MAX_UPLOAD_BYTES = 2 * 1024 ** 3;
const failures = () => ({
    too_large: text('This file is over 2 GiB. Trim or compress it, then try again.', '文件超过 2 GiB，请先剪短或压缩后再上传。'),
    empty: text('This file is empty. Choose another file.', '这个文件是空的，请换一个文件。'),
    unsupported: text('Choose an image, video or audio file', '请选择图片、视频或音频文件'),
    multiple: text('Upload one file at a time.', '一次只能上传一个文件。'),
    invalid: text('This file name cannot be used. Rename the file and try again.', '这个文件名不能使用，请改名后再试。'),
    disk_full: text('The upload stopped: the disk is full. Free some space, then try again.', '上传中断：磁盘空间不足，请清理出空间后重试。'),
    not_writable: text('The upload stopped: the input folder cannot be written to. Check its write permission, then try again.', '上传中断：输入文件夹无法写入，请检查它的写入权限后重试。'),
});
const generic = () => text('The upload did not finish. Try again.', '上传没有完成，请再试一次。');
// A request that never answers is cut off, so the pickers unlock and the failure line shows: two minutes plus four seconds per MiB, so a slow link is not mistaken for a hang.
export const uploadTimeoutMs = size => 120000 + Math.ceil(size / 1024 ** 2) * 4000;
// With a progress callback the body goes through XMLHttpRequest, whose upload events report the bytes sent; fetch has none.
function sendWithProgress(api, body, onProgress, timeoutMs) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', api.apiURL('/freevideo/media/upload'), true);
        xhr.setRequestHeader('Comfy-User', api.user ?? '');
        xhr.timeout = timeoutMs;
        xhr.upload.onprogress = event => { if (event.lengthComputable && event.total > 0) onProgress(Math.min(1, event.loaded / event.total)); };
        xhr.onload = () => {
            let result = {};
            try { result = JSON.parse(xhr.responseText); } catch { /* an unreadable reply is handled by the status */ }
            resolve({ok: xhr.status >= 200 && xhr.status < 300, json: async () => result});
        };
        xhr.onerror = xhr.ontimeout = xhr.onabort = () => reject(new TypeError('Failed to fetch'));
        xhr.send(body);
    });
}
// What a media item is called on screen: its card's name, the name the user's file had (kept with the item, since an
// engine that stores one copy per content gives the file a plain name), or the stored file's own name.
export const mediaName = row => row?.cast?.name || row?.name || String(row?.source || row?.file || '').split('/').pop();
export async function uploadMedia(api, file, {onProgress = null, timeoutMs = uploadTimeoutMs(file.size)} = {}) {
    const found = failures();
    if (file.size > MAX_UPLOAD_BYTES) throw new Error(found.too_large);
    if (file.size === 0) throw new Error(found.empty);
    const body = new FormData(); body.append('file', file);
    let response, result = {};
    const abort = new AbortController(), timer = setTimeout(() => abort.abort(), timeoutMs);
    uploadRunning(true);
    try {
        response = onProgress && typeof api.apiURL === 'function' && typeof XMLHttpRequest !== 'undefined'
            ? await sendWithProgress(api, body, onProgress, timeoutMs)
            : await api.fetchApi('/freevideo/media/upload', {method: 'POST', body, signal: abort.signal});
        result = await response.json();
    } catch { if (!response || response.ok) throw new Error(generic()); }
    finally { clearTimeout(timer); uploadRunning(false); }
    if (!response.ok) throw Object.assign(new Error(found[result?.code] || generic()), {code: typeof result?.code === 'string' ? result.code : undefined});
    return result;
}
