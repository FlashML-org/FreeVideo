// Pictures attached to a chat message: limits, checks, upload and thumbnail address.
import {agentPost, agentUpload} from './agent_session.js';

export const ATTACH = {
    count: 4, bytes: 10 * 1024 * 1024, types: ['image/png', 'image/jpeg', 'image/webp'],
    add: ['Add pictures', '添加图片'], remove: ['Remove', '移除'],
    tooMany: ['Up to 4 pictures per message.', '一条消息最多 4 张图片。'],
    tooLarge: ['Each picture must be under 10 MB.', '每张图片不能超过 10 MB。'],
    notPicture: ['Only pictures can be added.', '只能添加图片。'],
    unreadable: ['Could not read this picture. Please choose another one.', '无法读取这张图片，请换一张。'],
    failed: ['Could not upload the picture. Click it to try again.', '图片上传失败，请点击该图片重试。'],
    retry: ['Upload again', '重新上传'],
    icon: 'M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2zM4 16l4-4 4 4 3-3 5 5M15 9h.01',
};

// Splits files into the ones that fit and the first reason for leaving one out: {accepted, problem}.
export function pickPictures(files, room) {
    const accepted = []; let problem = null;
    for (const file of files) {
        if (!ATTACH.types.includes(file.type)) problem ||= 'notPicture';
        else if (file.size > ATTACH.bytes) problem ||= 'tooLarge';
        else if (accepted.length >= room) problem ||= 'tooMany';
        else accepted.push(file);
    }
    return {accepted, problem};
}

export const attachmentURL = (api, name) => {
    const path = '/freevideo/agent/chat/attachment?' + new URLSearchParams({name});
    return api.apiURL?.(path) || path;
};

const EXTENSION = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'};
export function randomName(file) {
    const bytes = globalThis.crypto?.getRandomValues?.(new Uint8Array(8)) || Array.from({length: 8}, () => Math.floor(Math.random() * 256));
    return 'picture-' + [...bytes].map(b => b.toString(16).padStart(2, '0')).join('') + (EXTENSION[file?.type] || '.png');
}
// The stored pictures a chat's entries name (for releasing them with the chat).
export const picturesOf = items => [...new Set((items || []).flatMap(item => (item?.attachments || []).map(picture => picture?.name)).filter(Boolean))];
// Releases each picture; an engine without the route answers the first with 404 and the rest are not asked (the
// engine's own cleanup, if any, keeps its old behaviour).
export async function releasePictures(api, names) {
    for (const name of names) {
        try {
            const response = await agentPost(api, '/freevideo/agent/chat/attachment/release', {name});
            if (response.status === 404 || response.status === 405) return;
        } catch { return; }
    }
}

// Resolves to the attachment object; rejects with {problem} naming a key of ATTACH.
export async function uploadPicture(api, file, signal) {
    let response;
    // Uploaded under a random name: the picture's own file name never leaves the page.
    try { response = await agentUpload(api, '/freevideo/agent/chat/attach?' + new URLSearchParams({name: randomName(file)}), file, signal); }
    catch (error) { if (signal?.aborted) throw {problem: null}; throw {problem: 'failed'}; }
    if (response.status === 201 || response.ok) {
        const value = await response.json().catch(() => null);
        // The page keeps the stored name and size only; the picture's own file name stays with the engine.
        if (value?.name) { const {original_name, ...kept} = value; return kept; }
        throw {problem: 'failed'};
    }
    const code = (await response.json().catch(() => null))?.error?.code;
    throw {problem: code === 'too_large' ? 'tooLarge' : code === 'invalid_media' ? 'unreadable' : 'failed', code};
}

// Tells the engine a staged picture is no longer wanted, so it can free the stored file. An engine without the route
// (404) or a failed request is not a problem for the page: the picture is gone from the message either way.
export async function releasePicture(api, name) {
    if (!name) return false;
    try { const response = await agentPost(api, '/freevideo/agent/chat/attachment/release', {name}); return response.ok && (await response.json().catch(() => ({}))).released === true; }
    catch { return false; }
}
// A send refused because a picture it names is no longer stored (another draft released the same picture).
export const pictureGone = error => error?.code === 'invalid_media'
    || error?.code === 'invalid_request' && (error.details || []).some(detail => /^\/attachments\//.test(detail?.path || ''));

// A thumbnail that turns into a grey cell with the picture glyph when the picture cannot be shown.
export function thumbnail(src) {
    const cell = document.createElement('span'), image = document.createElement('img');
    cell.className = 'fv-chat-thumb'; image.alt = ''; image.draggable = false;
    image.addEventListener('error', () => { cell.dataset.broken = 'true'; image.remove(); cell.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ATTACH.icon}"/></svg>`; });
    image.src = src; cell.append(image);
    return cell;
}
