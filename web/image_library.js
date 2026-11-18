// Pictures made in the image view live in the asset library as one-picture cards of kind "image": saved when a
// generation succeeds, offered again wherever a reference picture is added.
import { agentGet, agentPost, agentSend } from './agent_session.js';
import { uploadMedia } from './media_upload.js';
import { listenDialogKey } from './dialog_keys.js';

const languageOverride = typeof location !== 'undefined' ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en' && String(navigator.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const LIMIT = 200;
const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('./image_library.css', import.meta.url).href; document.head.append(css);

// One finished picture (an output file) becomes a card named after its prompt; the card's description keeps the output file,
// so deleting the work deletes its card too.
// A generated picture is a work in ComfyUI's input folder: the card names it, so there is one copy. Any other picture (an
// edit uploaded for the moment, which the engine removes later) gets a copy of its own for the card, as before.
export async function saveGeneratedImage(api, {url, file, prompt = ''}) {
    let media = file;
    if (!/^freevideo-image\/[^/]+$/.test(file)) {
        const response = await fetch(url); if (!response.ok) throw new Error('image_unreadable');
        const blob = await response.blob();
        media = (await uploadMedia(api, new File([blob], 'picture.' + ((blob.type.split('/')[1] || 'png').replace('jpeg', 'jpg')), {type: blob.type || 'image/png'}))).file;
    }
    const typed = Array.from(String(prompt).replace(/\s+/g, ' ').trim()).slice(0, 16).join('').trim();
    const name = typed || t(`Image ${(await imageCards(api)).length + 1}`, `图片 ${(await imageCards(api)).length + 1}`);
    const answer = await agentPost(api, '/freevideo/agent/cast', {kind: 'image', name, description: file, images: [{media, label: 'other'}]});
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
// prefer: 'up' opens it above its button when there is room (so it covers what was written, not the rows below).
export function openReferenceMenu({api, viewURL, anchor, host = document.body, onComputer, onPick, state, prefer = 'down'}) {
    const box = el('div', 'fv-image-menu'); box.setAttribute('role', 'menu');
    const computer = el('button', 'fv-image-menu-item', t('From this computer…', '从电脑选择…')); computer.type = 'button';
    computer.onclick = () => { close(); onComputer(); };
    const label = el('div', 'fv-image-menu-label', t('Images', '图片')), grid = el('div', 'fv-image-menu-grid'); label.hidden = grid.hidden = true;
    box.append(computer, label, grid); host.append(box);
    const place = () => {
        const r = (typeof anchor === 'function' ? anchor() : anchor).getBoundingClientRect(), narrow = innerWidth <= 420;
        const width = narrow ? innerWidth - 32 : Math.min(innerWidth - 32, 4 * 72 + 3 * 6 + 16);
        const left = narrow ? 16 : Math.max(16, Math.min(r.left, innerWidth - width - 16));
        box.style.cssText = `left:${left}px;top:0;width:${width}px`;
        const height = box.offsetHeight, below = r.bottom + 6, above = r.top - 6 - height;
        const top = prefer === 'up' && above >= 16 ? above : below + height <= innerHeight - 16 ? below : above >= 16 ? above : Math.max(16, innerHeight - 16 - height);
        box.style.top = top + 'px';
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
                    // The card's own input file goes along, so a place that reads input files uses it without a copy.
                    onPick(new File([blob], `${card.id}.${ext}`, {type: blob.type || 'image/png'}), card.id, card.thumbnail.file);
                } catch { /* the picture stays available for another click */ }
                refresh();
            };
            cells.push([cell, card.id]); grid.append(cell);
        }
        label.hidden = grid.hidden = !cells.length; refresh(); place();
    }).catch(() => { /* only the computer item remains */ });
    return {close, refresh, box};
}

// The works picker: the image works (the works library's own list and order, newest first by the engine's clock) as a
// grid under its button. A click places the picture; on a tile, Use as reference and Delete (undo for 10 s); below,
// Select several to delete, Show folder (this computer only) and Open the works library. It follows deletes made
// anywhere on the page. `t` gives the words; `canReference()` says whether Use as reference applies now.
const LOCAL = typeof location !== 'undefined' && ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
const GLYPH = {
    ref: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M8 3.5v9M3.5 8h9"/></svg>',
    trash: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5"/></svg>',
    open: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3h4v4M13 3 7.5 8.5M11.5 9.5V13h-8.5V4.5H6.5"/></svg>',
};
// `over`: a side column the picker sits over (the editor's panel), so it never covers the picture; else under the anchor.
export function openWorksPicker({anchor, t: tr = t, host = anchor.closest('dialog') || document.body, canReference = () => false, onPlace, onReference,
    onOpenLibrary = null, onClose = () => {}, over = null}) {
    const box = el('div', 'fv-works'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', tr('Works', '作品库')); box.dataset.fvTipScope = '';
    const head = el('div', 'fv-works-head'), title = el('b', 'fv-works-title', tr('Works', '作品库')), count = el('span', 'fv-works-count');
    head.append(title, count);
    if (onOpenLibrary) {
        const open = el('button', 'fv-works-link'); open.type = 'button'; open.innerHTML = GLYPH.open; open.append(el('span', null, tr('Open works library', '打开作品库')));
        tip(open, tr('Open works library', '打开作品库'), tr('See every picture and video, with their details', '查看全部图片和视频及其信息'));
        open.onclick = () => { close(); onOpenLibrary(); };
        head.append(open);
    }
    const grid = el('div', 'fv-works-grid'), foot = el('div', 'fv-works-foot'), empty = el('div', 'fv-works-empty');
    box.append(head, grid, empty, foot); host.append(box);
    let rows = [], selecting = false, unfollow = null, closed = false;
    const chosen = new Set(), tiles = new Map();
    const works = import('./image_works.js');
    function place() {
        const r = anchor.getBoundingClientRect(), side = over?.getBoundingClientRect();
        const width = side?.width ? Math.max(280, Math.min(368, side.width - 16)) : Math.min(innerWidth - 32, 4 * 80 + 3 * 8 + 2 * 12);
        const left = side?.width ? Math.max(16, side.right - width - 8) : Math.max(16, Math.min(r.right - width, innerWidth - width - 16));
        const top = r.bottom + 8;
        box.style.cssText = `left:${Math.round(left)}px;top:${Math.round(top)}px;width:${Math.round(width)}px;max-height:${Math.max(220, Math.round(innerHeight - top - 16))}px`;
    }
    function tip(node, name, use) { node.dataset.fvTipName = name; if (use) node.dataset.fvTipUse = use; node.setAttribute('aria-label', name); }
    function tile(row) {
        const item = el('div', 'fv-works-tile'); item.setAttribute('role', 'listitem');
        const pick = el('button', 'fv-works-pick'); pick.type = 'button';
        const img = el('img'); img.alt = ''; img.loading = 'lazy'; img.decoding = 'async';
        pick.append(img);
        const ref = el('button', 'fv-works-act fv-works-ref'); ref.type = 'button'; ref.innerHTML = GLYPH.ref;
        tip(ref, tr('Use as reference', '用作参考图'), tr('Add it to the references of this edit', '加到这次修改的参考图里'));
        const del = el('button', 'fv-works-act fv-works-del'); del.type = 'button'; del.innerHTML = GLYPH.trash;
        tip(del, tr('Delete', '删除'), tr('Can be undone for 10 seconds', '10 秒内可以撤销'));
        const tick = el('span', 'fv-works-tick'); tick.setAttribute('aria-hidden', 'true');
        item.append(pick, ref, del, tick);
        pick.onclick = () => {
            if (selecting) { if (chosen.has(row.file)) chosen.delete(row.file); else chosen.add(row.file); draw(); return; }
            close(); onPlace?.(row.file, row.name);
        };
        ref.onclick = () => { onReference?.(row.file, row.name); close(); };
        del.onclick = () => remove([row.file]);
        return {item, img, pick, ref, del};
    }
    async function remove(files) {
        const {deleteWithUndo} = await works;
        const handle = deleteWithUndo(files, tr);
        rows = rows.filter(row => !handle.files.includes(row.file));
        for (const file of handle.files) chosen.delete(file);
        if (!rows.length) selecting = false;
        draw();
    }
    // Tiles are kept by file, so a redraw (a delete, Select) never reloads a picture.
    function draw() {
        if (closed) return;
        const {worksThumbnailURL} = cached;
        count.textContent = rows.length ? String(rows.length) : '';
        empty.textContent = cached.failed ? tr('The works could not be read. Try again.', '没能读取作品，请再试一次。') : tr('No pictures yet', '还没有图片');
        empty.hidden = !!rows.length || !cached.loaded;
        const reference = !selecting && canReference();
        const keep = new Set(rows.map(row => row.file));
        for (const [file, part] of tiles) if (!keep.has(file)) { part.item.remove(); tiles.delete(file); }
        rows.forEach((row, index) => {
            let part = tiles.get(row.file);
            if (!part) { part = tile(row); part.img.src = worksThumbnailURL(row.file); tiles.set(row.file, part); }
            const on = chosen.has(row.file);
            part.item.dataset.selecting = String(selecting); part.item.dataset.on = String(on);
            part.ref.hidden = !reference; part.del.hidden = selecting;
            part.pick.setAttribute('aria-label', selecting ? row.name : tr('Place on the canvas', '放入画布') + ' · ' + row.name);
            if (selecting) part.pick.setAttribute('aria-pressed', String(on)); else part.pick.removeAttribute('aria-pressed');
            if (grid.children[index] !== part.item) grid.insertBefore(part.item, grid.children[index] || null);
        });
        foot.replaceChildren();
        const action = (label, run, cls = '') => { const b = el('button', 'fv-works-btn' + (cls ? ' ' + cls : ''), label); b.type = 'button'; b.onclick = run; return b; };
        if (selecting) {
            const all = chosen.size === rows.length && rows.length > 0;
            const del = action(tr(`Delete ${chosen.size}`, `删除 ${chosen.size} 张`), () => { if (chosen.size) void remove([...chosen]); }, 'fv-works-danger');
            del.disabled = !chosen.size; tip(del, del.textContent, tr('Can be undone for 10 seconds', '10 秒内可以撤销'));
            foot.append(action(all ? tr('Deselect all', '全不选') : tr('Select all', '全选'), () => { if (all) chosen.clear(); else for (const row of rows) chosen.add(row.file); draw(); }),
                el('span', 'fv-works-grow'), action(tr('Cancel', '取消'), () => { selecting = false; chosen.clear(); draw(); }), del);
        } else if (rows.length || LOCAL) {
            if (rows.length) foot.append(action(tr('Select', '选择'), () => { selecting = true; draw(); }));
            foot.append(el('span', 'fv-works-grow'));
            if (LOCAL) foot.append(action(tr('Show folder', '打开文件夹'), async () => {
                let answer; try { answer = await (await works).worksClient().historyOpen(); } catch { answer = {opened: false}; }
                if (answer?.opened === false) { folder.textContent = tr('The folder could not be opened. Open it in a file manager: ', '无法自动打开文件夹，请在文件管理器中打开：') + (answer.folder || ''); folder.hidden = false; }
            }));
        }
        foot.hidden = !foot.childElementCount;
        place();
    }
    const folder = el('div', 'fv-works-note'); folder.hidden = true; box.insertBefore(folder, foot);
    const cached = {loaded: false, failed: false, worksThumbnailURL: () => ''};
    async function load() {
        const module = await works; cached.worksThumbnailURL = module.worksThumbnailURL;
        try { rows = await module.listWorks(); cached.failed = false; } catch { rows = []; cached.failed = true; }
        cached.loaded = true; draw();
    }
    function close() {
        if (closed) return; closed = true;
        document.removeEventListener('pointerdown', away, true); unlistenKey(); removeEventListener('resize', place);
        unfollow?.(); box.remove(); onClose();
    }
    const away = event => { if (!box.contains(event.target) && !anchor.contains(event.target) && !event.target.closest?.('.fv-toasts, .fv-toast')) close(); };
    const key = event => { if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); if (selecting) { selecting = false; chosen.clear(); draw(); } else { close(); anchor.focus?.(); } } };
    // Esc belongs to the picker first: it leaves Select, then closes; the dialog's own Esc comes after. In a dialog the
    // key is heard through the views' key routing (a view below takes keys at the window and hands them on that way).
    const unlistenKey = host instanceof HTMLDialogElement ? listenDialogKey(host, 'keydown', key, true)
        : (addEventListener('keydown', key, true), () => removeEventListener('keydown', key, true));
    document.addEventListener('pointerdown', away, true); addEventListener('resize', place);
    grid.setAttribute('role', 'list'); empty.hidden = true; foot.hidden = true;
    place();
    void works.then(module => { if (!closed) unfollow = module.followWorks(() => void load()); });
    void load();
    return {close, box, refresh: draw};
}
