// Pictures made in the image view live in the asset library as one-picture cards of kind "image": saved when a
// generation succeeds, offered again wherever a reference picture is added.
import { agentGet, agentPost, agentSend } from './agent_session.js';
import { uploadMedia } from './media_upload.js';

const languageOverride = typeof location !== 'undefined' ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en' && String(navigator.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const LIMIT = 200;
const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('./image_library.css', import.meta.url).href; document.head.append(css);

// One finished picture (an output file) becomes a card named after its prompt; the card's description keeps the output file,
// so deleting the work deletes its card too.
export async function saveGeneratedImage(api, {url, file, prompt = ''}) {
    const response = await fetch(url); if (!response.ok) throw new Error('image_unreadable');
    const blob = await response.blob();
    const uploaded = await uploadMedia(api, new File([blob], file.slice(file.lastIndexOf('/') + 1), {type: blob.type || 'image/png'}));
    const typed = Array.from(String(prompt).replace(/\s+/g, ' ').trim()).slice(0, 16).join('').trim();
    const name = typed || t(`Image ${(await imageCards(api)).length + 1}`, `图片 ${(await imageCards(api)).length + 1}`);
    const answer = await agentPost(api, '/freevideo/agent/cast', {kind: 'image', name, description: file, images: [{media: uploaded.file, label: 'other'}]});
    if (!answer.ok) throw new Error('save_failed');
    return answer.json();
}

// The saved pictures, newest first (the server lists by last change).
export async function imageCards(api) {
    const answer = await agentGet(api, `/freevideo/agent/cast?kind=image&limit=${LIMIT}`);
    if (!answer.ok) return [];
    return (await answer.json()).cards || [];
}

export async function removeSavedImages(api, files) {
    const wanted = new Set(files);
    for (const card of await imageCards(api)) {
        if (wanted.has(card.description)) await agentSend(api, 'DELETE', '/freevideo/agent/cast/' + encodeURIComponent(card.id), {}, {'If-Match': String(card.revision)});
    }
}

// The menu under the reference "+": "From this computer…", then the saved pictures as a grid. It stays open for several
// clicks; a picture already added, or one past the limit, is greyed. `state()` says which card ids are in use and whether the strip is full.
export function openReferenceMenu({api, viewURL, anchor, host = document.body, onComputer, onPick, state}) {
    const box = el('div', 'fv-image-menu'); box.setAttribute('role', 'menu');
    const computer = el('button', 'fv-image-menu-item', t('From this computer…', '从电脑选择…')); computer.type = 'button';
    computer.onclick = () => { close(); onComputer(); };
    const label = el('div', 'fv-image-menu-label', t('Images', '图片')), grid = el('div', 'fv-image-menu-grid'); label.hidden = grid.hidden = true;
    box.append(computer, label, grid); host.append(box);
    const place = () => {
        const r = (typeof anchor === 'function' ? anchor() : anchor).getBoundingClientRect(), width = Math.min(innerWidth - 32, 4 * 72 + 3 * 6 + 16);
        box.style.cssText = `left:${Math.max(16, Math.min(r.left, innerWidth - width - 16))}px;top:${Math.min(r.bottom + 6, innerHeight - 160)}px;width:${width}px`;
    };
    const close = () => { document.removeEventListener('pointerdown', away, true); document.removeEventListener('keydown', key, true); removeEventListener('resize', place); box.remove(); };
    const away = event => { if (!box.contains(event.target) && !(typeof anchor === 'function' ? anchor() : anchor).contains(event.target)) close(); };
    const key = event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); } };
    document.addEventListener('pointerdown', away, true); document.addEventListener('keydown', key, true); addEventListener('resize', place);
    place();
    const cells = [];
    const refresh = () => { place(); const now = state(); for (const [cell, id] of cells) cell.disabled = now.used.has(id) || now.full; };
    imageCards(api).then(cards => {
        if (!box.isConnected) return;
        for (const card of cards) {
            if (!card.thumbnail?.file) continue;
            const cell = el('button', 'fv-image-menu-cell'), img = el('img'); cell.type = 'button'; cell.setAttribute('aria-label', card.name);
            img.src = viewURL(card.thumbnail.file); img.alt = ''; img.loading = 'lazy'; cell.append(img);
            cell.onclick = async () => {
                cell.disabled = true;
                try {
                    const blob = await (await fetch(viewURL(card.thumbnail.file))).blob();
                    const ext = (blob.type.split('/')[1] || 'png').replace('jpeg', 'jpg');
                    onPick(new File([blob], `${card.id}.${ext}`, {type: blob.type || 'image/png'}), card.id);
                } catch { /* the picture stays available for another click */ }
                refresh();
            };
            cells.push([cell, card.id]); grid.append(cell);
        }
        label.hidden = grid.hidden = !cells.length; refresh(); place();
    }).catch(() => { /* only the computer item remains */ });
    return {close, refresh, box};
}
