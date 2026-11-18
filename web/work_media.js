// The page's addresses for a work's thumbnail and original, and the one background queue that loads originals ahead
// on a page opened from another device (media_queue.js).
import { api } from '../../scripts/api.js';
import { aheadEnabled, createMediaQueue, watchCards } from './media_queue.js';
import { jobs, ACTIVE } from './task_store.js';

// A saved picture of the image history (freevideo-image/<name>): its small copy, long edge 480 px, made by the engine.
export const pictureThumbnailURL = file => api.apiURL('/freevideo/library/picture-thumbnail?' + new URLSearchParams({file}));
// Pictures of the history have small copies; anything else is shown as itself.
export const hasPictureThumbnail = file => /^freevideo-image\/(?:image-\d{8}-\d{6}-\d{2}\.(?:png|jpg)|[0-9a-f]{32}\.png)$/.test(String(file || ''));
// The address every view uses for a file of ComfyUI's folders, so a file loaded ahead is the one the player asks for.
export function mediaURL(file, type) {
    const split = file.lastIndexOf('/');
    return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(split + 1), subfolder: split < 0 ? '' : file.slice(0, split), type}));
}

// A deleted work leaves nothing of it in this browser's cache: each of its addresses is asked again past the cache, which
// drops the stored copy; the engine no longer has the file, answers not found and nothing is stored again. (An address
// that still answers, a work kept because it is in use, is cut off at its first bytes.)
export function forgetInBrowser(urls) {
    for (const url of new Set(urls.filter(Boolean))) {
        const controller = new AbortController();
        fetch(url, {cache: 'reload', priority: 'low', credentials: 'same-origin', signal: controller.signal})
            .then(response => { if (response.ok) controller.abort(); else return response.body?.cancel?.(); }).catch(() => {});
    }
}
export function videoAddresses({video, id = null}) {
    id ||= String(video || '').replace(/^FreeVideo\//, '').replace(/\/video\.mp4$/, '');
    return [mediaURL(video, 'output'), api.apiURL('/freevideo/library/thumbnail?' + new URLSearchParams({id}))];
}
export const pictureAddresses = file => [mediaURL(file, 'input'), ...(hasPictureThumbnail(file) ? [pictureThumbnailURL(file)] : [])];
if (typeof window !== 'undefined') {
    window.addEventListener('freevideo-library-deleted', event => { if (event.detail?.video) forgetInBrowser(videoAddresses(event.detail)); });
    const pictures = files => forgetInBrowser((files || []).filter(file => typeof file === 'string' && /^freevideo-image\//.test(file)).flatMap(pictureAddresses));
    window.addEventListener('freevideo-pictures-deleted', event => pictures(event.detail?.files));
    window.addEventListener('freevideo-works-removed', event => { if (event.detail?.undoable === false) pictures(event.detail.paths || event.detail.ids); });
}

let queue = null;
export function mediaQueue() {
    if (queue) return queue;
    queue = createMediaQueue({enabled: aheadEnabled()});
    if (queue.enabled) {
        try { queue.followJobs(jobs, ACTIVE); } catch (error) { console.warn('FreeVideo media queue:', error); }
        // A deleted work is not loaded ahead any more.
        window.addEventListener('freevideo-library-deleted', event => { const video = event.detail?.video; if (video) queue.forget(mediaURL(video, 'output')); });
        // Uploads (media_upload.js, the image page and the editor announce theirs) go before anything loaded ahead.
        let uploads = 0, release = null;
        window.addEventListener('freevideo-upload', event => {
            uploads = Math.max(0, uploads + (event.detail?.active ? 1 : -1));
            if (uploads && !release) release = queue.hold('upload');
            else if (!uploads && release) { release(); release = null; }
        });
    }
    return queue;
}

export { watchCards };
