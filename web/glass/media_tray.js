// The studio's Media window: marks the parts of freevideo.js's media panel so media_tray.css can show it as a small window
// beside the toolbar's Media button (the same kind of window as Frame & size, Duration and Quality; tray_pop.js places
// it). Inside, top to bottom: the title row with what new pictures become; what is added, as small squares in a row (first
// frame, last frame, references), the last square "+ Add"; one line to choose from the saved works, make a picture with
// AI or add reference audio; characters, scenes and props. "Choose from works" turns the window into a grid of the saved
// works (all, videos or pictures); a choice adds it and brings the window back.
// Presentation only: every control stays the panel's own (its handlers, names and order). What is added: the frame
// slots, the works page, the key that turns references off, a wrapper around a tile's move and remove keys, and the
// stylesheet link. What is moved: the tiles' container and the tool row into one box (the grid), the AI and audio keys
// into the line under it, the remark into a line with its key, and the drop-down for new pictures into the title row.
import './tray_pop.js';
import { mediaQueue } from '../work_media.js';
import {setHidden} from '../dom_quiet.js';

const CARD_KEYS = {'↑': 'fv-mt-up', '↓': 'fv-mt-down'};
// How many saved works the works page lists (newest first, pictures and videos together).
const WORKS = 60;

function markCard(card) {
    const preview = card.querySelector('.fv-preview');
    card.dataset.kind = preview ? (preview.tagName === 'IMG' ? 'image' : preview.tagName.toLowerCase()) : '';
    // The shared drop-down face around the tile's own select.
    const select = card.querySelector(':scope > .fv-row > select');
    if (select && !select.parentElement.classList.contains('fv-select')) { const wrap = document.createElement('div'); wrap.className = 'fv-select'; select.replaceWith(wrap); wrap.append(select); }
    // The remark about a different shape becomes the name of its crop key, on the picture's own corner.
    const shape = card.querySelector('.fv-shape-note'), crop = shape?.querySelector('button'), wrap = card.querySelector(':scope > .fv-preview-wrap');
    if (shape && wrap && shape.parentElement !== wrap) wrap.append(shape);
    card.classList.toggle('fv-mt-crop', Boolean(crop));
    if (crop && !crop.dataset.fvMt) { crop.dataset.fvMt = '1'; const said = shape.querySelector('span')?.textContent.trim() || ''; crop.title = `${said} ${crop.textContent}`.trim(); crop.setAttribute('aria-label', crop.title); }
    // A picture that cannot be shown leaves a plain ground with a small picture mark, never a broken-image mark or a file name.
    if (preview?.tagName === 'IMG') {
        const broken = () => card.classList.add('fv-mt-broken');
        if (preview.complete && !preview.naturalWidth && preview.getAttribute('src')) broken(); else preview.addEventListener('error', broken, {once: true});
    }
    // A tile with more to set than its use (a middle frame's time, a video's depth and sound, audio) is three squares
    // wide: the picture, and its settings beside it.
    card.dataset.fvMtWide = String(Boolean(card.querySelector(':scope > :is(.fv-time,.fv-depth-only,.fv-lines-only,.fv-sound,audio.fv-preview)')));
    const rows = [...card.children].filter(child => child.classList.contains('fv-row'));
    const controls = rows.length > 1 ? rows.at(-1) : null;
    // Edited is a small mark on the picture; its words stay as the mark's name.
    const edited = card.querySelector('.fv-edited');
    if (edited && !edited.hasAttribute('role')) { edited.setAttribute('role', 'img'); edited.setAttribute('aria-label', edited.textContent); }
    if (!controls || controls.classList.contains('fv-mt-rest')) return;
    controls.classList.add('fv-mt-rest');
    const buttons = [...controls.querySelectorAll(':scope > button')], keys = document.createElement('div');
    keys.className = 'fv-mt-keys';
    const remove = buttons.at(-1), up = buttons.find(b => b.textContent === '↑'), down = buttons.find(b => b.textContent === '↓');
    // The picture itself opens the editor (the panel's own handler on it); 'Original' joins the drop-down, so tiles stay one size.
    const original = card.dataset.kind === 'image' && buttons.length > 3 ? buttons.find((b, index) => index > 0 && b !== up && b !== down && b !== remove) : null;
    original?.classList.add('fv-mt-original');
    // Moving earlier or later, and Remove, are in the tile's drop-down, under its uses; the two keys stay in the page for it to press.
    if (select && (up || down || remove) && !select.dataset.fvMt) {
        select.dataset.fvMt = '1'; select.append(document.createElement('hr'));
        for (const [key, value] of [[up, 'fv-mt-earlier'], [down, 'fv-mt-later'], [original, 'fv-mt-original'], [remove, 'fv-mt-remove']]) {
            if (!key) continue;
            const option = document.createElement('option'); option.value = value; option.textContent = key === remove ? key.textContent : key.title || key.textContent; option.disabled = key.disabled; select.append(option);
        }
        const chosen = select.value;
        select.addEventListener('change', event => {
            if (!select.value.startsWith('fv-mt-')) return;
            event.stopImmediatePropagation(); const key = {'fv-mt-earlier': up, 'fv-mt-later': down, 'fv-mt-original': original, 'fv-mt-remove': remove}[select.value]; select.value = chosen; key?.click();
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
const rowsOf = media => { try { return media ? media.rows() : []; } catch { return []; } };

// A saved video, as the file the panel's own upload takes (a picture needs no copy: it is already an input file).
async function fileOf(item) {
    const blob = await (await fetch(item.url)).blob(), ext = (blob.type.split('/')[1] || 'mp4').replace('quicktime', 'mov');
    return new File([blob], `${String(item.id).split('/').pop()}.${ext}`, {type: blob.type || 'video/mp4'});
}

// An empty frame slot: says which frame it is, takes a dropped picture, one from this computer or one from the saved works.
function emptySlot(panel, role) {
    const media = mediaOf(panel), slot = make('div', 'fv-mt-slot-empty'); slot.dataset.slot = role; slot.setAttribute('role', 'group');
    const name = role === 'first' ? say(panel, 'First frame', '首帧') : say(panel, 'Last frame', '尾帧');
    slot.setAttribute('aria-label', name);
    // Two small picture keys, named in their hover cards: this computer, and the saved works.
    const computer = make('button', 'fv-mt-slot-key fv-mt-computer', say(panel, 'Computer', '电脑')), saved = make('button', 'fv-mt-slot-key fv-mt-saved', say(panel, 'Works', '作品库'));
    computer.setAttribute('aria-label', `${name}: ${say(panel, 'from this computer', '从电脑选择')}`); saved.setAttribute('aria-label', `${name}: ${say(panel, 'from the works', '从作品库选择')}`);
    computer.type = saved.type = 'button';
    computer.dataset.fvTipName = say(panel, 'From this computer', '从电脑选择'); computer.dataset.fvTipUse = say(panel, `A picture as the ${name.toLowerCase()}`, `选一张图片作${name}`);
    saved.dataset.fvTipName = say(panel, 'From the works', '从作品库选择'); saved.dataset.fvTipUse = say(panel, `A saved picture as the ${name.toLowerCase()}`, `选一张作品作${name}`);
    const picker = make('input'); picker.type = 'file'; picker.accept = 'image/*'; picker.hidden = true;
    picker.onchange = () => { if (picker.files?.length) media?.upload([picker.files[0]], {role}); picker.value = ''; };
    computer.onclick = () => picker.click();
    saved.onclick = () => openWorks(panel, role);
    slot.ondragover = event => { event.preventDefault(); slot.dataset.over = 'true'; };
    slot.ondragleave = () => { delete slot.dataset.over; };
    slot.ondrop = event => { event.preventDefault(); event.stopPropagation(); delete slot.dataset.over; const file = [...event.dataTransfer.files].find(f => f.type.startsWith('image/')); if (file) media?.upload([file], {role}); };
    const keys = make('span', 'fv-mt-slot-keys'); keys.append(computer, saved);
    slot.append(make('span', 'fv-mt-slot-name', name), keys, picker);
    if (!media) computer.disabled = saved.disabled = true;
    return slot;
}

// ── The works page: the saved works as a grid of squares, all, videos or pictures. A frame slot that asks for one sees
// pictures only. A choice adds the work (as what new pictures become, or as the frame that asked) and the window comes back.
function worksPage(panel) {
    if (panel.fvMtWorks) return panel.fvMtWorks;
    const page = make('div', 'fv-mt-works'); page.hidden = true; page.setAttribute('role', 'group');
    const head = make('div', 'fv-mt-works-head'), back = make('button', 'fv-mt-back'), title = make('span', 'fv-mt-works-title');
    back.type = 'button'; back.setAttribute('aria-label', say(panel, 'Back to media', '返回素材'));
    back.dataset.fvTipName = say(panel, 'Back', '返回'); back.dataset.fvTipUse = say(panel, 'Back to what is added', '回到已加入的素材');
    back.onclick = () => showPage(panel, 'media', true);
    const filter = make('div', 'fv-mt-filter'); filter.setAttribute('role', 'radiogroup'); filter.setAttribute('aria-label', say(panel, 'Show', '显示'));
    const glide = make('span', 'fv-mt-glide'); glide.setAttribute('aria-hidden', 'true'); filter.append(glide);
    for (const [kind, en, zh] of [['all', 'All', '全部'], ['video', 'Videos', '视频'], ['image', 'Pictures', '图片']]) {
        const key = make('button', '', say(panel, en, zh)); key.type = 'button'; key.dataset.kind = kind; key.setAttribute('role', 'radio');
        key.onclick = () => { if (key.disabled) return; panel.fvMtFilter = kind; drawWorks(panel); };
        filter.append(key);
    }
    filter.addEventListener('keydown', event => {
        const step = {ArrowLeft: -1, ArrowRight: 1}[event.key]; if (!step) return;
        const all = [...filter.querySelectorAll('button:not(:disabled)')], next = all[all.indexOf(event.target) + step];
        if (next) { event.preventDefault(); next.click(); next.focus(); }
    });
    head.append(back, title, filter);
    const grid = make('div', 'fv-mt-works-grid'), empty = make('p', 'fv-mt-works-empty');
    grid.setAttribute('role', 'list');
    page.append(head, grid, empty);
    panel.fvMtWorks = page;
    return page;
}
async function openWorks(panel, role = null) {
    const media = mediaOf(panel); if (!media) return;
    panel.fvMtArmed = role; panel.fvMtFilter ||= 'all';   // (a slot that asks sees pictures, whatever is chosen here)
    showPage(panel, 'works');
    drawWorks(panel);
    const asked = panel.fvMtAsked = Symbol();
    try { panel.fvMtItems = await media.recent({limit: WORKS}); } catch { panel.fvMtItems = panel.fvMtItems || []; }
    if (panel.fvMtAsked !== asked) return;
    panel.fvMtLoaded = true; drawWorks(panel);
    panel.fvMtWorks.querySelector('.fv-mt-work:not(:disabled), .fv-mt-filter [aria-checked=true]')?.focus({preventScroll: true});
}
function showPage(panel, page, refocus = false) {
    const tray = panel.closest('.fvs-tray'); if (!tray) return;
    const works = worksPage(panel);
    if (works.parentElement !== tray) tray.append(works);
    if (page !== 'works') { panel.fvMtArmed = null; panel.fvMtAsked = null; }
    works.hidden = page !== 'works';
    if (tray.dataset.fvMtPage !== page) { tray.dataset.fvMtPage = page; globalThis.FreeVideoTrayPop?.place(tray); }
    if (refocus && page === 'media') panel.querySelector('.fv-mt-browse')?.focus({preventScroll: true});
}
function drawWorks(panel) {
    const page = panel.fvMtWorks; if (!page || page.hidden) return;
    const media = mediaOf(panel), armed = panel.fvMtArmed, rows = rowsOf(media);
    const full = !media || rows.length >= media.limit;
    page.querySelector('.fv-mt-works-title').textContent = armed === 'first' ? say(panel, 'Choose the first frame', '选一张作首帧')
        : armed === 'last' ? say(panel, 'Choose the last frame', '选一张作尾帧') : say(panel, 'Works', '作品库');
    page.dataset.armed = armed || '';
    const filter = armed ? 'image' : panel.fvMtFilter || 'all';
    for (const key of page.querySelectorAll('.fv-mt-filter > button')) {
        key.setAttribute('aria-checked', String(key.dataset.kind === filter));
        key.disabled = Boolean(armed) && key.dataset.kind !== 'image';
    }
    const items = (panel.fvMtItems || []).filter(item => filter === 'all' || (filter === 'video') === (item.kind === 'video'));
    const used = new Set(rows.map(row => row.file));
    // Each work keeps its key and picture for as long as it is listed: a repaint only changes what the key may do.
    const keys = panel.fvMtKeys ||= new Map();
    page.querySelector('.fv-mt-works-grid').replaceChildren(...items.map(item => {
        let key = keys.get(item.id);
        if (!key) {
            key = make('button', 'fv-mt-work'); key.type = 'button'; key.dataset.kind = item.kind || 'image'; key.setAttribute('role', 'listitem');
            const picture = make('img'); picture.alt = ''; picture.draggable = false; picture.decoding = 'async'; picture.loading = 'lazy';
            picture.addEventListener('load', () => { key.dataset.shown = 'true'; sharpen(key, item); }, {once: true});
            picture.src = item.thumb || item.url; key.append(picture);
            if (picture.complete && picture.naturalWidth) { key.dataset.shown = 'true'; queueMicrotask(() => sharpen(key, item)); }
            keys.set(item.id, key);
        }
        const video = item.kind === 'video', name = item.name || (video ? say(panel, 'Video', '视频') : say(panel, 'Picture', '图片'));
        const here = !video && used.has(item.file);
        key.disabled = full || here || Boolean(media?.busy());
        key.toggleAttribute('data-used', here);
        const use = here ? say(panel, 'Already added', '已加入') : armed ? page.querySelector('.fv-mt-works-title').textContent : say(panel, 'Add', '加入');
        key.setAttribute('aria-label', `${name}: ${use}`);
        key.dataset.fvTipName = name; key.dataset.fvTipUse = use;
        key.onclick = () => pick(panel, item, key);
        return key;
    }));
    const empty = page.querySelector('.fv-mt-works-empty');
    empty.hidden = Boolean(items.length) || !panel.fvMtLoaded;
    empty.textContent = filter === 'video' ? say(panel, 'No saved videos yet.', '作品库里还没有视频。')
        : filter === 'image' ? say(panel, 'No saved pictures yet.', '作品库里还没有图片。') : say(panel, 'No saved works yet.', '作品库里还没有作品。');
    page.dataset.loading = String(!panel.fvMtLoaded);
    glideTo(page.querySelector('.fv-mt-filter'));
}
// The highlight sits behind the chosen choice and slides there (as in the other windows' strips).
function glideTo(strip) {
    const glide = strip?.querySelector('.fv-mt-glide'), on = strip?.querySelector('[aria-checked=true]'); if (!glide) return;
    glide.dataset.on = String(Boolean(on && on.offsetWidth));
    if (on && on.offsetWidth) Object.assign(glide.style, {width: `${on.offsetWidth}px`, height: `${on.offsetHeight}px`, transform: `translate(${on.offsetLeft}px, ${on.offsetTop}px)`});
}
async function pick(panel, item, key) {
    const media = mediaOf(panel); if (!media || key.disabled) return;
    const armed = item.kind !== 'video' ? panel.fvMtArmed : null;
    key.disabled = true;
    // Without a slot asking, a picture becomes what new pictures are set to be (the title row's choice).
    const options = armed ? {role: armed} : {};
    try {
        if (item.kind === 'video') await media.addFromLibrary(await fileOf(item), item.id, options);
        else await media.addFromLibrary(null, item.id, {...options, source: item.file});
    } catch { /* The panel's own line says what went wrong. */ }
    showPage(panel, 'media', true); mark(panel);
}

// A work's picture fills its square at the square's own pixels (two to a point on a sharp screen), cropped about the
// centre, never stretched. A picture is its own file; a video's saved thumbnail is small (384 wide, less for a tall or
// square video), so when it would be enlarged the video's first frame is shown over it instead.
function sharpen(key, item) {
    const picture = key.querySelector(':scope > img'); if (item.kind !== 'video' || !picture?.naturalWidth || key.querySelector(':scope > video')) return;
    // Over a link a square does not download a whole video for a sharper picture, unless that video is already here.
    const queue = mediaQueue(); if (queue.enabled && !queue.loaded(item.url)) return;
    const box = key.getBoundingClientRect(), dpr = Math.min(2, globalThis.devicePixelRatio || 1);
    if (!box.width || Math.max(box.width * dpr / picture.naturalWidth, box.height * dpr / picture.naturalHeight) <= 1.05) return;
    const frame = make('video'); frame.muted = true; frame.playsInline = true; frame.preload = 'auto'; frame.disablePictureInPicture = true;
    frame.tabIndex = -1; frame.setAttribute('aria-hidden', 'true');
    frame.addEventListener('loadeddata', () => { key.dataset.frame = 'true'; }, {once: true});
    frame.addEventListener('error', () => frame.remove(), {once: true});
    frame.src = `${item.url}#t=0.1`; key.append(frame);
}

// The squares are one size, five to a row (four on a phone); a tile with more to set takes three. The add tile takes what
// is left of the last row (or a row of its own), so no row ends in an empty gap.
function layout(panel) {
    const box = panel.fvMtMedia, toolbar = panel.fvMtToolbar; if (!box || !panel.clientWidth) return;
    const cols = Math.max(1, getComputedStyle(box).gridTemplateColumns.split(' ').filter(Boolean).length);
    // In the order they are shown (the frame slots lead by their CSS order), as the grid places them.
    const items = [...panel.fvMtCards.children].filter(item => getComputedStyle(item).display !== 'none')
        .sort((a, b) => (parseInt(a.style.order, 10) || 0) - (parseInt(b.style.order, 10) || 0));
    if (!toolbar) return;
    let col = 0;
    for (const item of items) {
        const size = item.matches('.fv-mt-slot-empty') ? 1 : item.matches('.fv-card') ? Math.min(cols, item.dataset.fvMtWide === 'true' ? 3 : 1) : cols;
        if (col % cols + size > cols) col += cols - col % cols;
        col += size;
    }
    const rest = cols - col % cols;
    // An add tile alone on its row is as tall as a square.
    const gap = parseFloat(getComputedStyle(box).columnGap) || 0, square = `${Math.floor((box.clientWidth - gap * (cols - 1)) / cols)}px`;
    if (box.style.getPropertyValue('--fv-mt-square') !== square) box.style.setProperty('--fv-mt-square', square);
    // The line of keys: words while they fit, else the AI and audio keys show their pictures only.
    const line = panel.fvMtActions;
    if (line?.clientWidth) { line.dataset.compact = 'false'; if (line.scrollWidth > line.clientWidth + 1) line.dataset.compact = 'true'; }
    const span = `span ${rest}`;
    if (toolbar.style.gridColumn !== span) toolbar.style.gridColumn = span;
    const fit = rest === 1 ? 'one' : 'more';
    if (toolbar.dataset.fvMtFit !== fit) toolbar.dataset.fvMtFit = fit;
    fitMedia(panel);
}
// When the window is short (a small screen, many tiles), only the tiles scroll, in their own box; the title row, the
// line of keys and the groups stay in view. On the works page, the grid of works is the part that scrolls.
function fitMedia(panel) {
    const tray = panel.closest('.fvs-tray'); if (!tray?.open || !panel.fvMtMedia) return;
    const box = tray.dataset.fvMtPage === 'works' ? panel.fvMtWorks?.querySelector('.fv-mt-works-grid') : panel.fvMtMedia; if (!box) return;
    const style = getComputedStyle(tray), px = name => parseFloat(style[name]) || 0, most = parseFloat(style.maxHeight);
    // The window is as tall as what it holds: the part keeps its own height.
    if (!Number.isFinite(most)) { if (box.style.maxHeight) box.style.maxHeight = ''; scrolled(box); return; }
    const inside = most - px('borderTopWidth') - px('borderBottomWidth');
    const value = `${Math.max(96, Math.floor(inside - (tray.scrollHeight - box.offsetHeight)))}px`;
    if (box.style.maxHeight !== value) box.style.maxHeight = value;
    scrolled(box);
}
function scrolled(box) {
    const more = box.scrollHeight - box.clientHeight > 1;
    const value = !more ? 'false' : box.scrollTop + box.clientHeight >= box.scrollHeight - 1 ? 'end' : 'true';
    if (box.dataset.fvMtScroll !== value) box.dataset.fvMtScroll = value;
}

function mark(panel) {
    if (panel.fvMtMarking) return;
    panel.fvMtMarking = true;
    // What this pass changed is not news to the watcher: drop its record of it, or it would call for another pass forever.
    try { markNow(panel); layout(panel); drawWorks(panel); } finally { panel.fvMediaTray?.takeRecords(); panel.fvMtMarking = false; }
}
// The tool row: "+ Add" is the add tile's face and goes straight to this computer (the saved works have their own page);
// AI and Reference audio join the line under the tiles.
function markKeys(panel, toolbar) {
    const [add, generate, audio] = toolbar.querySelectorAll(':scope > button');
    if (add && !add.classList.contains('fv-mt-add-key')) {
        add.classList.add('fv-mt-add-key');
        add.dataset.fvMtHint = say(panel, 'Drop or choose pictures, video or audio', '拖入或选择图片、视频、音频');
        const picker = toolbar.querySelector(':scope > input[type=file]');
        if (picker) add.onclick = () => picker.click();
        add.dataset.fvTipName = say(panel, 'Add', '添加'); add.dataset.fvTipUse = say(panel, 'Pictures, video or audio from this computer', '从电脑添加图片、视频或音频');
    }
    generate?.classList.add('fv-mt-ai'); audio?.classList.add('fv-mt-audio');
    for (const [key, use] of [[generate, say(panel, 'Make a reference picture from words', '用一句话生成一张参考图')], [audio, audio?.title]]) {
        if (!key || key.dataset.fvTipName) continue;
        key.dataset.fvTipName = key.textContent.trim(); if (use) key.dataset.fvTipUse = use;
        if (!key.getAttribute('aria-label')) key.setAttribute('aria-label', key.textContent.trim());
    }
    return {generate, audio};
}
function markNow(panel) {
    panel.classList.add('fv-mt');
    // The tiles and the add tile share one box (the panel's own two containers, moved in whole): it is the grid, and the
    // part that scrolls when the window is short.
    const toolbar = panel.fvMtToolbar ||= panel.querySelector(':scope > .fv-toolbar');
    const cards = panel.fvMtCards ||= [...panel.children].find(child => child.tagName === 'DIV' && !child.className);
    const note = panel.fvMtNote ||= panel.querySelector(':scope > .fv-note');
    if (!cards) return;
    cards.classList.add('fv-mt-cards');
    if (!panel.fvMtMedia) {
        const box = panel.fvMtMedia = make('div', 'fv-mt-media'); cards.before(box); box.append(cards); if (toolbar) box.append(toolbar);
        box.addEventListener('scroll', () => scrolled(box), {passive: true});
        // Something dragged over the window lights the add tile (a drop anywhere in it adds, as before).
        const over = on => { if (toolbar) { if (on) toolbar.dataset.over = 'true'; else delete toolbar.dataset.over; } };
        panel.addEventListener('dragover', () => over(true));
        for (const type of ['dragleave', 'drop']) panel.addEventListener(type, event => { if (type === 'drop' || !panel.contains(event.relatedTarget)) over(false); });
    }
    panel.fvMtChinese ??= /[一-鿿]/.test(toolbar?.querySelector('select')?.getAttribute('aria-label') || '');
    // What new pictures become: in the title row, at its right end.
    const tray = panel.closest('.fvs-tray'), head = tray?.querySelector(':scope > .fvs-tray-head');
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
    // Characters, scenes and props: the group's title says what it is, so its add key says only "+ New".
    for (const add of panel.querySelectorAll('.fv-cast-add:not([data-fv-mt-short])')) add.dataset.fvMtShort = say(panel, 'New', '新建');
    // The line under the tiles: the saved works, then AI and Reference audio at its right end.
    if (!panel.fvMtActions) {
        const line = panel.fvMtActions = make('div', 'fv-mt-actions');
        const browse = make('button', 'fv-mt-browse'); browse.type = 'button';
        browse.append(make('span', 'fv-mt-browse-name', say(panel, 'Choose from works', '从作品库选择')));
        browse.dataset.fvTipName = say(panel, 'Choose from works', '从作品库选择'); browse.dataset.fvTipUse = say(panel, 'Add a saved video or picture', '加入作品库里的视频或图片');
        browse.onclick = () => openWorks(panel);
        line.append(browse, make('span', 'fv-mt-actions-gap'));
        panel.append(line);
    }
    if (toolbar) {
        const {generate, audio} = markKeys(panel, toolbar);
        for (const key of [generate, audio]) if (key && key.parentElement !== panel.fvMtActions) panel.fvMtActions.append(key);
    }
    const tiles = [...cards.querySelectorAll(':scope > .fv-card')];
    for (const card of tiles) markCard(card);
    const media = mediaOf(panel), rows = rowsOf(media);
    const known = rows.length === tiles.length, on = row => row.enabled !== false;
    // The two frame slots lead the tiles: the picture that is the first or last frame, or an empty slot that takes one.
    for (const [slot, order] of [['first', '-2'], ['last', '-1']]) {
        const at = known ? rows.findIndex(row => row.role === slot && on(row)) : -1;
        for (const [index, card] of tiles.entries()) if (card.dataset.fvSlot === slot && index !== at) { delete card.dataset.fvSlot; card.style.order = ''; }
        // An empty slot is made once and kept (so a key keeps its focus while the list repaints around it).
        const slots = panel.fvMtSlots ||= {};
        if (at >= 0) { tiles[at].dataset.fvSlot = slot; tiles[at].style.order = order; slots[slot]?.remove(); }
        else if (media) {
            const empty = slots[slot] ||= emptySlot(panel, slot); empty.style.order = order;
            if (empty.parentElement !== cards) cards.append(empty);
        } else slots[slot]?.remove();
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
        if (held) (card.querySelector('.fv-preview-wrap') || card).append(make('span', 'fv-mt-held-note', say(panel, 'Not used with frames', '不与首尾帧同用')));
    }
    setHidden(panel.fvMtMix, !blocked);
    setHidden(panel.fvMtRemark, Boolean(!note || note.hidden) && !blocked);
    if (tray && !tray.fvMtPaged) {
        tray.fvMtPaged = true; showPage(panel, 'media');
        // Placed by tray_pop.js: measured with the scrolling parts at their full height, then fitted to the window.
        tray.fvPopBefore = () => { for (const box of [panel.fvMtMedia, panel.fvMtWorks?.querySelector('.fv-mt-works-grid')]) if (box?.style.maxHeight) box.style.maxHeight = ''; };
        tray.fvPopAfter = () => fitMedia(panel);
        // Put away, the window opens on what is added next time.
        tray.addEventListener('toggle', () => { if (!tray.open) showPage(panel, 'media'); });
    }
}

function scan() {
    for (const panel of document.querySelectorAll('.fvs-tray .fv-panel[data-freevideo=media]')) {
        mark(panel);
        if (panel.fvMediaTray) continue;
        panel.fvMediaTray = new MutationObserver(() => mark(panel));
        if (panel.fvMtNote) panel.fvMediaTray.observe(panel.fvMtNote, {childList: true, characterData: true, attributes: true, attributeFilter: ['hidden', 'class']});
        if (panel.fvMtCards) panel.fvMediaTray.observe(panel.fvMtCards, {childList: true, subtree: true});
        // A new width can change how many tiles fit in a row; a new height, how much of them shows.
        let width = 0;
        const tray = panel.closest('.fvs-tray');
        const again = () => { if (panel.clientWidth && panel.clientWidth !== width) { width = panel.clientWidth; layout(panel); } else fitMedia(panel); };
        if (globalThis.ResizeObserver) { const watch = new ResizeObserver(again); watch.observe(panel); if (tray) watch.observe(tray); }
        tray?.addEventListener('toggle', () => requestAnimationFrame(() => { layout(panel); fitMedia(panel); }));
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
