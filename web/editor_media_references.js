// The editing view's reference pictures are the creative panel's own media rows: one list, two places to change it.
import {api} from '../../scripts/api.js';
import {openImageEditor} from './image_editor.js';
import {imageGenerationSupported} from './image_generation.js';
import {uploadMedia} from './media_upload.js';
import {middleNotice} from './middle_frames.js';
export const MAX_REFERENCES = 32;
const IMAGE = /\.(png|jpe?g|webp|bmp|tiff?)$/i;
export const isImageFile = file => IMAGE.test(file || '');
const inUse = rows => rows.filter(row => row.enabled !== false).length;
export const ROLES = ['reference', 'first', 'middle', 'last'];
const upstream = (node, name) => {
    const input = node?.inputs?.find(i => i.name === name);
    const links = node?.graph?.links;
    const link = input?.link != null ? links?.get?.(input.link) ?? links?.[input.link] : null;
    return link ? node.graph.getNodeById(link.origin_id) : null;
};
// `node` is the generate node; null while no media panel is connected to it.
export function mediaReferences(node) {
    const media = () => upstream(node, 'media');
    const widget = () => media()?.widgets?.find(w => w.name === 'assets') || null;
    const read = () => {
        try { const rows = JSON.parse(widget()?.value || '[]'); return Array.isArray(rows) ? rows : []; } catch { return []; }
    };
    return {
        available: () => !!widget(),
        // What stops a run with middle frames, as a sentence ('' when nothing does); `seconds` is the length of the part about to be made.
        problem(t, seconds) {
            const rows = read().filter(row => row.enabled !== false);
            if (!rows.some(row => row.role === 'middle')) return '';
            const node = media(), linked = name => node?.inputs?.find(i => i.name === name)?.link != null;
            const count = role => rows.filter(row => row.role === role).length;
            const references = count('reference') + ['reference', ...Array.from({length: 7}, (_, i) => 'reference_' + (i + 2)), 'reference_audio'].filter(linked).length;
            if (references) return t('Choose keyframes or references. Disable unused cards.', '首尾帧与参考模式不能混用，请关闭本次不用的素材。');
            return middleNotice(rows, {first: count('first') + Number(linked('first')), last: count('last') + Number(linked('last')), references, seconds}, t);
        },
        // The length of the video these pictures are for; null while no generate node is connected.
        seconds: () => { const value = Number(node?.widgets?.find(w => w.name === 'seconds')?.value); return value > 0 ? value : null; },
        rows: read,
        write(rows) {
            const field = widget(), owner = media();
            // The limit counts pictures in use; when the list would grow past it, the oldest spare ones go first.
            for (let at = rows.findIndex(row => row.enabled === false); rows.length > MAX_REFERENCES && at >= 0;
                at = rows.findIndex(row => row.enabled === false)) rows.splice(at, 1);
            if (!field || rows.length > MAX_REFERENCES) throw new Error('references');
            field.value = JSON.stringify(rows);
            owner.graph?.change?.();
            window.dispatchEvent(new CustomEvent('freevideo-media', {detail: owner.id}));
        },
        url(file) {
            const split = file.lastIndexOf('/');
            return api.apiURL('/view?' + new URLSearchParams({
                filename: file.slice(split + 1), subfolder: split < 0 ? '' : file.slice(0, split), type: 'input'}));
        },
        // Into ComfyUI's input folder through FreeVideo's streaming upload; a run waits for it, as in the creative panel.
        async upload(file) {
            const owner = media();
            // One upload at a time across the panel and this view, so neither writes over the other's list.
            for (let waited = 0; owner?.isUploading && waited < 600; waited++) await new Promise(done => setTimeout(done, 100));
            if (owner?.isUploading) throw new Error('uploading');
            if (owner) owner.isUploading = true;
            try {
                return await uploadMedia(api, file);
            } finally { if (owner) owner.isUploading = false; }
        },
        canGenerateImage: imageGenerationSupported,
        // The image editor opens on a blank picture the shape of the video, ready to generate one; a saved one is added as a reference.
        async newImage({target, t}) {
            const rows = read();
            if (inUse(rows) >= MAX_REFERENCES) return null;
            const library = rows.filter(row => isImageFile(row.file)).map(row => ({file: row.file, label: (row.source || row.file).split('/').pop()}));
            const outcome = await openImageEditor({target, role: 'reference', t, resolve: file => this.url(file), upload: file => this.upload(file), library});
            if (!outcome?.blob) return null;
            const result = await this.upload(new File([outcome.blob], outcome.name, {type: outcome.blob.type}));
            const latest = read();
            if (inUse(latest) >= MAX_REFERENCES) return null;
            latest.push({file: result.file, role: 'reference', enabled: true, edit: outcome.edit}); this.write(latest);
            return result.file;
        },
        subscribe(listener) {
            const changed = event => { if (String(event.detail) === String(media()?.id)) listener(); };
            window.addEventListener('freevideo-media', changed);
            return () => window.removeEventListener('freevideo-media', changed);
        },
    };
}
