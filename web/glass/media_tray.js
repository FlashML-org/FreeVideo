// The studio's Media tray: marks the parts of freevideo.js's media panel so media_tray.css can lay them out on one grid.
// Presentation only: every control stays the panel's own (its handlers, names and order). What is added: the frame slots,
// the row of recent works, the key that turns references off, a wrapper around a tile's move and remove keys, the drop
// line in the add tile, and the stylesheet link. What is moved: the tiles' container and the tool row into one box (the
// grid), the remark into a line with its key, and the drop-down for new pictures into the tray's title row.
const CARD_KEYS = {'↑': 'fv-mt-up', '↓': 'fv-mt-down'};
// The tray is one grid of twelve columns. On a wide tray a tile (frame or reference) is two columns, so six to a row; on a
// narrow one, half the row. The add tile takes the rest of the last row of tiles, or a row of its own.
const UNITS = 12, NARROW = 600;

function markCard(card) {
    const preview = card.querySelector('.fv-preview');
    card.dataset.kind = preview ? (preview.tagName === 'IMG' ? 'image' : preview.tagName.toLowerCase()) : '';
    // The shared drop-down face around the tile's own select.
    const select = card.querySelector(':scope > .fv-row > select');
    if (select && !select.parentElement.classList.contains('fv-select')) { const wrap = document.createElement('div'); wrap.className = 'fv-select'; select.replaceWith(wrap); wrap.append(select); }
    // The remark about a different shape becomes the name of its crop key.
    // The crop key sits on the picture's own corner (inside it), like the frame's tag.
    const shape = card.querySelector('.fv-shape-note'), crop = shape?.querySelector('button'), wrap = card.querySelector(':scope > .fv-preview-wrap');
    if (shape && wrap && shape.parentElement !== wrap) wrap.append(shape);
    card.classList.toggle('fv-mt-crop', Boolean(crop));
    if (crop && !crop.dataset.fvMt) { crop.dataset.fvMt = '1'; const said = shape.querySelector('span')?.textContent.trim() || ''; crop.title = `${said} ${crop.textContent}`.trim(); crop.setAttribute('aria-label', crop.title); }
    // A picture that cannot be shown leaves a plain ground with a small picture mark, never a broken-image mark or a file name.
    if (preview?.tagName === 'IMG') {
        const broken = () => card.classList.add('fv-mt-broken');
        if (preview.complete && !preview.naturalWidth && preview.getAttribute('src')) broken(); else preview.addEventListener('error', broken, {once: true});
    }
    const rows = [...card.children].filter(child => child.classList.contains('fv-row'));
    const controls = rows.length > 1 ? rows.at(-1) : null;
    if (!controls || controls.classList.contains('fv-mt-rest')) return;
    controls.classList.add('fv-mt-rest');
    const buttons = [...controls.querySelectorAll(':scope > button')], keys = document.createElement('div');
    keys.className = 'fv-mt-keys';
    const remove = buttons.at(-1), up = buttons.find(b => b.textContent === '↑'), down = buttons.find(b => b.textContent === '↓');
    // The picture itself opens the editor (the panel's own handler on it); 'Original' joins the drop-down, so tiles stay one height.
    const original = card.dataset.kind === 'image' && buttons.length > 3 ? buttons.find((b, index) => index > 0 && b !== up && b !== down && b !== remove) : null;
    original?.classList.add('fv-mt-original');
    // Moving earlier or later is in the tile's drop-down, under its uses; the two keys stay in the page for it to press.
    if (select && (up || down) && !select.dataset.fvMt) {
        select.dataset.fvMt = '1'; select.append(document.createElement('hr'));
        for (const [key, value] of [[up, 'fv-mt-earlier'], [down, 'fv-mt-later'], [original, 'fv-mt-original']]) {
            if (!key) continue;
            const option = document.createElement('option'); option.value = value; option.textContent = key.title || key.textContent; option.disabled = key.disabled; select.append(option);
        }
        const chosen = select.value;
        select.addEventListener('change', event => {
            if (!select.value.startsWith('fv-mt-')) return;
            event.stopImmediatePropagation(); const key = {'fv-mt-earlier': up, 'fv-mt-later': down, 'fv-mt-original': original}[select.value]; select.value = chosen; key?.click();
        }, true);
    }
    for (const button of [up, down, remove]) {
        if (!button) continue;
        button.classList.add(CARD_KEYS[button.textContent] || 'fv-mt-remove');
        // The words stay as the key's name for assistive technology; the face is an icon.
        if (!button.getAttribute('aria-label')) button.setAttribute('aria-label', button.title || button.textContent);
        keys.append(button);
    }
    // A picture already carries its Edit key on the preview.
    if (card.dataset.kind === 'image' && card.querySelector('.fv-preview-edit')) buttons.find(b => !b.classList.length)?.classList.add('fv-mt-edit');
    card.append(keys);
}

// The node whose list this panel shows: the one the shell names on the panel, else the one whose files match the tiles.
function mediaOf(panel) {
    if (panel.freevideoMediaNode?.freevideoMedia) return panel.freevideoMediaNode.freevideoMedia;
    const nodes = (globalThis.app?.graph?._nodes || globalThis.app?.graph?.nodes || []).filter(node => node.freevideoMedia);
    if (nodes.length < 2) return nodes[0]?.freevideoMedia || null;
    const shown = [...panel.querySelectorAll('.fv-mt-cards > .fv-card .fv-file')].map(label => label.title).join('\n');
    return (nodes.find(node => { try { return node.freevideoMedia.rows().map(row => row.file).join('\n') === shown; } catch { return false; } }) || nodes[0]).freevideoMedia;
}
const say = (panel, en, zh) => panel.fvMtChinese ? zh : en;
const make = (tag, cls, text) => { const element = document.createElement(tag); if (cls) element.className = cls; if (text != null) element.textContent = text; return element; };
const FRAME = role => role === 'first' || role === 'last' || role === 'middle';

// A picture from the saved works, as the file the panel's own upload takes.
async function fileOf(item) {
    const blob = await (await fetch(item.url)).blob(), ext = (blob.type.split('/')[1] || (item.kind === 'video' ? 'mp4' : 'png')).replace('jpeg', 'jpg').replace('quicktime', 'mov');
    return new File([blob], `${String(item.id).split('/').pop()}.${ext}`, {type: blob.type || (item.kind === 'video' ? 'video/mp4' : 'image/png')});
}

// An empty frame slot: says which frame it is, takes a dropped picture, one from this computer or one from the saved works.
function emptySlot(panel, role) {
    const media = mediaOf(panel), slot = make('div', 'fv-mt-slot-empty'); slot.dataset.slot = role; slot.setAttribute('role', 'group');
    const name = role === 'first' ? say(panel, 'First frame', '首帧') : say(panel, 'Last frame', '尾帧');
    slot.setAttribute('aria-label', name);
    const computer = make('button', 'fv-mt-slot-key', say(panel, 'Computer', '电脑')), saved = make('button', 'fv-mt-slot-key', say(panel, 'Saved works', '作品库'));
    computer.type = saved.type = 'button';
    computer.title = say(panel, `Choose the ${name.toLowerCase()} from this computer`, `从电脑选一张图片作${name}`);
    saved.title = say(panel, `Then choose a picture below as the ${name.toLowerCase()}`, `然后在下面的作品里点一张作${name}`);
    saved.setAttribute('aria-pressed', String(panel.fvMtArmed === role));
    const picker = make('input'); picker.type = 'file'; picker.accept = 'image/*'; picker.hidden = true;
    picker.onchange = () => { if (picker.files?.length) media?.upload([picker.files[0]], {role}); picker.value = ''; };
    computer.onclick = () => picker.click();
    saved.onclick = () => { panel.fvMtArmed = panel.fvMtArmed === role ? null : role; mark(panel); panel.querySelector('.fv-mt-recent button:not(:disabled)')?.focus(); };
    slot.ondragover = event => { event.preventDefault(); slot.dataset.over = 'true'; };
    slot.ondragleave = () => { delete slot.dataset.over; };
    slot.ondrop = event => { event.preventDefault(); event.stopPropagation(); delete slot.dataset.over; const file = [...event.dataTransfer.files].find(f => f.type.startsWith('image/')); if (file) media?.upload([file], {role}); };
    const keys = make('span', 'fv-mt-slot-keys'); keys.append(computer, saved);
    slot.append(make('span', 'fv-mt-slot-name', name), keys, picker);
    if (!media) computer.disabled = saved.disabled = true;
    if (!panel.fvMtPictures?.length) saved.disabled = true;
    return slot;
}

// The row of recent saved works: one press adds a work as a reference, or fills the frame slot that asked for one.
async function fillRecent(panel) {
    const media = mediaOf(panel), row = panel.fvMtRecent; if (!row) return;
    // The newest works of both kinds for the row; and the newest pictures on their own, for a frame slot that asks for one
    // (the newest works may all be videos).
    const [items, more] = media ? await Promise.all([media.recent({limit: 12}), media.recent({limit: 60})]) : [[], []];
    panel.fvMtItems = items; panel.fvMtPictures = [...items, ...more].filter((item, index, all) => item.kind !== 'video' && all.findIndex(other => other.id === item.id) === index).slice(0, 12);
    mark(panel);
}
function drawRecent(panel) {
    const media = mediaOf(panel), row = panel.fvMtRecent; if (!row) return;
    // A frame is a picture: while a slot is asking, only pictures are offered. The row shows as many works as fill it.
    const items = (panel.fvMtArmed ? panel.fvMtPictures || [] : panel.fvMtItems || []).slice(0, panel.fvMtRecentCount || 6);
    let full = true; try { full = !media || media.rows().length >= media.limit; } catch { /* Full. */ }
    row.hidden = !items.length;
    const armed = panel.fvMtArmed, list = row.querySelector('.fv-mt-recent-list');
    row.querySelector('.fv-mt-title').textContent = armed === 'first' ? say(panel, 'Choose the first frame', '选一张作首帧') : armed === 'last' ? say(panel, 'Choose the last frame', '选一张作尾帧') : say(panel, 'Recent works', '作品库最近');
    row.dataset.armed = armed || '';
    // Each work keeps its key and picture for as long as it is listed: a repaint only changes what the key may do, so a
    // picture is fetched once and fades in when it arrives.
    const keys = panel.fvMtKeys ||= new Map();
    list.replaceChildren(...items.map(item => {
        let key = keys.get(item.id);
        if (!key) {
            key = make('button', 'fv-mt-recent-item'); key.type = 'button'; key.dataset.kind = item.kind || 'image';
            const picture = make('img'); picture.alt = ''; picture.draggable = false; picture.decoding = 'async';
            picture.addEventListener('load', () => { key.dataset.shown = 'true'; sharpen(key, item); }, {once: true});
            picture.src = item.thumb || item.url; key.append(picture);
            if (picture.complete && picture.naturalWidth) { key.dataset.shown = 'true'; queueMicrotask(() => sharpen(key, item)); }
            keys.set(item.id, key);
        }
        const video = item.kind === 'video';
        key.disabled = full || Boolean(media?.busy());
        key.title = armed ? row.querySelector('.fv-mt-title').textContent : say(panel, 'Add as a reference', '加入为参考');
        key.setAttribute('aria-label', `${item.name || (video ? say(panel, 'Video', '视频') : say(panel, 'Picture', '图片'))}: ${key.title}`);
        key.onclick = async () => {
            const role = !video && panel.fvMtArmed ? panel.fvMtArmed : 'reference'; panel.fvMtArmed = null; key.disabled = true;
            try { await media.addFromLibrary(await fileOf(item), item.id, {role}); } catch { /* The panel's own line says what went wrong. */ }
            mark(panel);
        };
        return key;
    }));
}

// A work's picture fills its key at the key's own pixels (two to a point on a sharp screen), cropped about the centre,
// never stretched. A picture is its own file at full size; a video's saved thumbnail is small (384 wide, less for a
// tall or square video), so when it would be enlarged the video's first frame is shown over it instead.
function sharpen(key, item) {
    const picture = key.querySelector(':scope > img'); if (item.kind !== 'video' || !picture?.naturalWidth || key.querySelector(':scope > video')) return;
    const box = key.getBoundingClientRect(), dpr = Math.min(2, globalThis.devicePixelRatio || 1);
    if (!box.width || Math.max(box.width * dpr / picture.naturalWidth, box.height * dpr / picture.naturalHeight) <= 1.05) return;
    const frame = make('video'); frame.muted = true; frame.playsInline = true; frame.preload = 'auto'; frame.disablePictureInPicture = true;
    frame.tabIndex = -1; frame.setAttribute('aria-hidden', 'true');
    frame.addEventListener('loadeddata', () => { key.dataset.frame = 'true'; }, {once: true});
    frame.addEventListener('error', () => frame.remove(), {once: true});
    frame.src = `${item.url}#t=0.1`; key.append(frame);
}

// Where each tile and the add tile sit on the twelve columns. Placed here rather than left to the grid, so the add tile
// can take exactly what is left of the last row, and the frames keep their place in front.
function layout(panel) {
    const width = panel.clientWidth, cards = panel.fvMtCards, toolbar = panel.fvMtToolbar; if (!width || !cards) return;
    const narrow = width < NARROW;
    panel.dataset.fvMtSize = narrow ? 'narrow' : 'wide';
    const span = narrow ? {frame: 6, tile: 6} : {frame: 2, tile: 2};
    const items = [...cards.children].filter(item => getComputedStyle(item).display !== 'none')
        .sort((a, b) => (parseInt(a.style.order, 10) || 0) - (parseInt(b.style.order, 10) || 0));
    let row = 0, col = 0;
    for (const item of items) {
        const size = item.matches('.fv-mt-slot-empty,[data-fv-slot]') ? span.frame : item.matches('.fv-card') ? span.tile : UNITS;
        if (col + size > UNITS) { row++; col = 0; }
        item.style.gridArea = `${row + 1} / ${col + 1} / span 1 / span ${size}`;
        col += size;
    }
    if (toolbar) {
        if (col >= UNITS) { row++; col = 0; }
        const rest = UNITS - col;
        toolbar.style.gridArea = `${row + 1} / ${col + 1} / span 1 / span ${rest}`;
        // What the add tile holds follows its width: one line, two lines, or "+ Add" over two small picture keys.
        const gap = parseFloat(getComputedStyle(panel.fvMtMedia).columnGap) || 0, wide = rest * (width - (UNITS - 1) * gap) / UNITS + (rest - 1) * gap;
        toolbar.dataset.fvMtFit = wide >= 560 ? 'line' : wide >= 200 ? 'tile' : 'stack';
    }
    const count = narrow ? 4 : 6;
    if (panel.fvMtRecentCount !== count) { panel.fvMtRecentCount = count; drawRecent(panel); }
    fitMedia(panel);
}
// When everything does not fit under the top bar, only the tiles scroll (in their own box); the title row, the remark,
// recent works and the groups stay in view.
function fitMedia(panel) {
    const tray = panel.closest('.fvs-tray'), box = panel.fvMtMedia; if (!tray?.open || !box || !panel.clientWidth) return;
    const style = getComputedStyle(tray), px = name => parseFloat(style[name]) || 0, most = parseFloat(style.maxHeight);
    if (!Number.isFinite(most)) return;
    // The most the tray's inside may hold, in the same measure as its scrollHeight (padding in, border out).
    const inside = style.boxSizing === 'border-box' ? most - px('borderTopWidth') - px('borderBottomWidth') : most + px('paddingTop') + px('paddingBottom');
    const value = `${Math.max(96, Math.floor(inside - (tray.scrollHeight - box.offsetHeight)))}px`;
    if (box.style.maxHeight !== value) box.style.maxHeight = value;
    scrolled(box);
}
function scrolled(box) {
    const more = box.scrollHeight - box.clientHeight > 1;
    box.dataset.fvMtScroll = !more ? 'false' : box.scrollTop + box.clientHeight >= box.scrollHeight - 1 ? 'end' : 'true';
}

function mark(panel) {
    if (panel.fvMtMarking) return;
    panel.fvMtMarking = true;
    // What this pass changed is not news to the watcher: drop its record of it, or it would call for another pass forever.
    try { markNow(panel); layout(panel); } finally { panel.fvMediaTray?.takeRecords(); panel.fvMtMarking = false; }
}
// The tool row's keys: the face of each in the narrowest add tile, and words for the two that become small picture keys.
function markKeys(panel, toolbar) {
    const [add, generate, audio] = toolbar.querySelectorAll(':scope > button');
    add?.classList.add('fv-mt-add-key'); generate?.classList.add('fv-mt-ai'); audio?.classList.add('fv-mt-audio');
    for (const [key, use] of [[generate, say(panel, 'Make a reference picture from words', '用一句话生成一张参考图')], [audio, audio?.title]]) {
        if (!key || key.dataset.fvTipName) continue;
        key.dataset.fvTipName = key.textContent.trim(); if (use) key.dataset.fvTipUse = use;
        if (!key.getAttribute('aria-label')) key.setAttribute('aria-label', key.textContent.trim());
    }
}
function markNow(panel) {
    panel.classList.add('fv-mt');
    // The tiles and the add tile share one box (the panel's own two containers, moved in whole): it is the grid, and the
    // part that scrolls when the tray is short.
    const toolbar = panel.fvMtToolbar ||= panel.querySelector(':scope > .fv-toolbar');
    const cards = panel.fvMtCards ||= [...panel.children].find(child => child.tagName === 'DIV' && !child.className);
    const note = panel.fvMtNote ||= panel.querySelector(':scope > .fv-note');
    if (!cards) return;
    cards.classList.add('fv-mt-cards');
    if (!panel.fvMtMedia) {
        const box = panel.fvMtMedia = make('div', 'fv-mt-media'); cards.before(box); box.append(cards); if (toolbar) box.append(toolbar);
        box.addEventListener('scroll', () => scrolled(box), {passive: true});
    }
    panel.fvMtChinese ??= /[一-鿿]/.test(toolbar?.querySelector('select')?.getAttribute('aria-label') || '');
    // What new pictures become: in the title row, left of the close key, wherever the tray is.
    const head = panel.closest('.fvs-tray')?.querySelector(':scope > .fvs-tray-head');
    const role = panel.fvMtRole ||= toolbar?.querySelector(':scope > .fv-select');
    if (role && !panel.fvMtRoleGroup) {
        const label = make('span', 'fv-mt-role-label', say(panel, 'Use new images as', '新图片用作')); label.setAttribute('aria-hidden', 'true');
        const group = panel.fvMtRoleGroup = make('span', 'fv-mt-role'); group.append(label, role);
    }
    if (panel.fvMtRoleGroup && head && panel.fvMtRoleGroup.parentElement !== head) {
        for (const old of head.querySelectorAll(':scope > .fv-mt-role')) old.remove();
        const close = head.querySelector(':scope > .fvs-tray-close');
        if (close) close.before(panel.fvMtRoleGroup); else head.append(panel.fvMtRoleGroup);
    }
    if (toolbar) {
        markKeys(panel, toolbar);
        if (!toolbar.querySelector(':scope > .fv-mt-drop')) {
            toolbar.prepend(make('span', 'fv-mt-drop', say(panel, 'Drop pictures, video or audio', '拖入图片、视频或音频')));
            toolbar.addEventListener('dragover', () => { toolbar.dataset.over = 'true'; });
            for (const type of ['dragleave', 'drop']) toolbar.addEventListener(type, event => { if (type === 'drop' || !toolbar.contains(event.relatedTarget)) delete toolbar.dataset.over; });
        }
    }
    for (const old of cards.querySelectorAll(':scope > .fv-mt-slot-empty')) old.remove();
    const tiles = [...cards.querySelectorAll(':scope > .fv-card')];
    for (const card of tiles) markCard(card);
    const media = mediaOf(panel);
    let rows = []; try { rows = media ? media.rows() : []; } catch { rows = []; }
    const known = rows.length === tiles.length, on = row => row.enabled !== false;
    // The two frame slots lead the tiles: the picture that is the first or last frame, or an empty slot that takes one.
    for (const [slot, order] of [['first', '-2'], ['last', '-1']]) {
        const at = known ? rows.findIndex(row => row.role === slot && on(row)) : -1;
        for (const [index, card] of tiles.entries()) if (card.dataset.fvSlot === slot && index !== at) { delete card.dataset.fvSlot; card.style.order = ''; }
        if (at >= 0) { tiles[at].dataset.fvSlot = slot; tiles[at].dataset.fvSlotName = slot === 'first' ? say(panel, 'First frame', '首帧') : say(panel, 'Last frame', '尾帧'); tiles[at].style.order = order; const wrap = tiles[at].querySelector('.fv-preview-wrap'); if (wrap) wrap.dataset.fvSlotName = tiles[at].dataset.fvSlotName; }
        else if (media) { const empty = emptySlot(panel, slot); empty.style.order = order; cards.append(empty); }
    }
    // Frames and references cannot be used together: while a frame is on, the references that are also on are held
    // back in plain sight, and one key beside the remark turns them off.
    if (!panel.fvMtMix) {
        const remark = panel.fvMtRemark = make('div', 'fv-mt-remark'), mix = panel.fvMtMix = make('div', 'fv-mt-mix');
        const off = make('button', '', say(panel, 'Turn off references', '停用参考')); off.type = 'button';
        off.onclick = () => { const current = mediaOf(panel); try { current?.update(current.rows().map(row => row.role === 'reference' ? {...row, enabled: false} : row)); } catch { /* The list is as it was. */ } };
        mix.append(off); if (note) { note.before(remark); remark.append(note); } else panel.append(remark);
        remark.append(mix);
    }
    const blocked = Boolean(note && !note.hidden && note.classList.contains('fv-error')) && known && rows.some(row => on(row) && FRAME(row.role)) && rows.some(row => on(row) && row.role === 'reference');
    for (const [index, card] of tiles.entries()) {
        const held = blocked && on(rows[index]) && rows[index].role === 'reference';
        card.classList.toggle('fv-mt-held', held);
        card.querySelector('.fv-mt-held-note')?.remove();
        if (held) (card.querySelector('.fv-preview-wrap') || card).append(make('span', 'fv-mt-held-note', say(panel, 'Not used with frames', '不能与首尾帧同用')));
    }
    panel.fvMtMix.hidden = !blocked;
    panel.fvMtRemark.hidden = Boolean(!note || note.hidden) && !blocked;
    if (!panel.fvMtRecent) {
        const recent = panel.fvMtRecent = make('div', 'fv-mt-recent'); recent.hidden = true;
        recent.append(make('span', 'fv-mt-title'), make('div', 'fv-mt-recent-list')); panel.append(recent);
        fillRecent(panel);
        panel.closest('details')?.addEventListener('toggle', event => { if (event.target.open) fillRecent(panel); });
    } else drawRecent(panel);
}

// The tray reaches up to 8 px under the top bar at most (as the Advanced tray), 16 px on a phone.
function placeRoom(tray) {
    // Measured from where the tray is laid out, not from its opening motion (which moves and scales it a little).
    const home = tray.offsetParent, bottom = home ? home.getBoundingClientRect().top + home.clientTop + tray.offsetTop + tray.offsetHeight : 0;
    if (!tray.open || !bottom) return;
    const bar = document.querySelector('.fv-topbar')?.getBoundingClientRect().bottom || 0;
    const top = Math.max(bar, parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--fv-header-h')) || 56);
    const value = `${Math.max(200, Math.floor(bottom - top - (globalThis.innerWidth <= 640 ? 16 : 8)))}px`;
    if (tray.style.getPropertyValue('--fv-mt-room') !== value) tray.style.setProperty('--fv-mt-room', value);
}
// While the tray is open on a wide window, the strip of the stage that shows beside and above it fades into the page, so
// nothing of the stage's own controls peeks out around the tray. It is outside the tray: a press on it closes the tray
// the way any press outside does.
function placeScrim(tray) {
    const scrim = tray.fvMtScrim, stage = tray.closest('dialog')?.querySelector('.fv-stage'), home = tray.offsetParent;
    if (!scrim || !stage || !home || !tray.open) return;
    const s = stage.getBoundingClientRect(), h = home.getBoundingClientRect(), t = tray.getBoundingClientRect();
    scrim.style.left = `${Math.round(s.left - h.left)}px`; scrim.style.width = `${Math.round(s.width)}px`;
    scrim.style.height = `${Math.max(0, Math.round(h.top - Math.max(s.top, t.top - 32)))}px`;
}
function scrimFor(tray) {
    if (tray.fvMtScrim) return;
    const scrim = tray.fvMtScrim = make('div', 'fv-mt-scrim'); scrim.setAttribute('aria-hidden', 'true'); tray.before(scrim);
    const place = () => { placeRoom(tray); placeScrim(tray); const panel = tray.querySelector('.fv-mt'); if (panel) fitMedia(panel); };
    tray.addEventListener('toggle', place);
    new ResizeObserver(place).observe(tray);
    globalThis.addEventListener('resize', place);
    place();
}

function scan() {
    for (const panel of document.querySelectorAll('.fvs-tray .fv-panel[data-freevideo=media]')) {
        mark(panel);
        const tray = panel.closest('.fvs-tray'); if (tray) scrimFor(tray);
        if (panel.fvMediaTray) continue;
        panel.fvMediaTray = new MutationObserver(() => mark(panel));
        if (panel.fvMtNote) panel.fvMediaTray.observe(panel.fvMtNote, {childList: true, characterData: true, attributes: true, attributeFilter: ['hidden', 'class']});
        if (panel.fvMtCards) panel.fvMediaTray.observe(panel.fvMtCards, {childList: true, subtree: true});
        // A new width can change how many tiles fit in a row.
        let width = 0;
        new ResizeObserver(() => { if (panel.clientWidth && panel.clientWidth !== width) { width = panel.clientWidth; layout(panel); } else fitMedia(panel); }).observe(panel);
    }
}

if (!globalThis.FreeVideoMediaTray) {
    globalThis.FreeVideoMediaTray = {scan};
    if (!document.querySelector('link[data-fv-sheet="media-tray"]')) {
        const sheet = document.createElement('link'); sheet.rel = 'stylesheet'; sheet.dataset.fvSheet = 'media-tray';
        sheet.href = new URL('./media_tray.css', import.meta.url).href; document.head.append(sheet);
    }
    let timer = 0;
    new MutationObserver(() => { if (!timer) timer = setTimeout(() => { timer = 0; scan(); }, 150); }).observe(document.body, {childList: true, subtree: true});
    scan();
}
