// One media file into ComfyUI's input folder through FreeVideo's streaming upload; every upload place shares it.
const languageOverride = typeof location !== 'undefined'
    ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en'
    && String(navigator.language || '').toLowerCase().startsWith('zh'));
const text = (en, zh) => cn ? zh : en;
// The server's limit; a bigger file is turned away before a byte is sent.
export const MAX_UPLOAD_BYTES = 2 * 1024 ** 3;
const failures = () => ({
    too_large: text('This file is over 2 GiB. Trim or compress it, then try again.', '文件超过 2 GiB，请先剪短或压缩后再上传。'),
    empty: text('This file is empty. Choose another file.', '这个文件是空的，请换一个文件。'),
    unsupported: text('Choose an image, video or audio file', '请选择图片、视频或音频文件'),
    multiple: text('Upload one file at a time.', '一次只能上传一个文件。'),
    invalid: text('This file name cannot be used. Rename the file and try again.', '这个文件名不能使用，请改名后再试。'),
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
export async function uploadMedia(api, file, {onProgress = null, timeoutMs = uploadTimeoutMs(file.size)} = {}) {
    const found = failures();
    if (file.size > MAX_UPLOAD_BYTES) throw new Error(found.too_large);
    if (file.size === 0) throw new Error(found.empty);
    const body = new FormData(); body.append('file', file);
    let response, result = {};
    const abort = new AbortController(), timer = setTimeout(() => abort.abort(), timeoutMs);
    try {
        response = onProgress && typeof api.apiURL === 'function' && typeof XMLHttpRequest !== 'undefined'
            ? await sendWithProgress(api, body, onProgress, timeoutMs)
            : await api.fetchApi('/freevideo/media/upload', {method: 'POST', body, signal: abort.signal});
        result = await response.json();
    } catch { if (!response || response.ok) throw new Error(generic()); }
    finally { clearTimeout(timer); }
    if (!response.ok) throw new Error(found[result?.code] || generic());
    return result;
}
