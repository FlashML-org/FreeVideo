// "From a photo" page of the new-character dialog: one photo of a real person becomes a character sheet in the order of
// the final picture, face close-up | front | side | back, keeping the person's face. The face close-up is made from the
// photo first (Qwen-Image 2.1 when it is ready); the front, side and back are made from the photo and that close-up, the
// side and back also from the front. Clothes stay as in the photo, follow a description, or follow a clothes picture.
// Save puts the four views and one 1 × 4 sheet of them on the card.
//
// Privacy: everything stays on this computer. The photo and the clothes picture go to the local image engine only when
// Generate is pressed. Views made here are hidden from the picture lists while they are drafts and after they belong to
// a card (freevideo.cast.files); views replaced by drawing again, and every unsaved view when the dialog closes, the page
// is left or the tab closes, are deleted at once (keepalive, so a closing tab still deletes them).
import {createImageGenClient, imagePackUnavailableMessage, renderImageFailure, IMAGE_GEN_INFO_POLL_MS} from './image_generation.js';
import {createCastGenerator} from './cast_generate.js';
import {createOtherUsageNotice} from './other_usage.js';

// Generation order: the face close-up first (from the photo alone), then the front, side and back from the photo and
// that close-up. The order on screen and on the sheet is the same.
const MAKE = Object.freeze(['face', 'front', 'side', 'back']);
const SHOW = Object.freeze(['face', 'front', 'side', 'back']);
const VIEW_TITLES = Object.freeze({front: ['Front', '正面'], side: ['Side', '侧面'], back: ['Back', '背面'], face: ['Face', '脸部']});
// The sheet: the cast sheet's rules (equal height, light grey ground, 48 px gaps, each picture whole and centred), one
// row of four, the face cell 1.4 times as wide as a full-body cell.
const SHEET = Object.freeze({body: [736, 1302], face: [1030, 1302], gap: 48, ground: 'rgb(238 238 238)'});
export const CAST_FILES_KEY = 'freevideo.cast.files';
const ORIGINAL = 'photo:original';
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const tip = (node, name, use) => { node.dataset.fvTipName = name; if (use) node.dataset.fvTipUse = use; else delete node.dataset.fvTipUse; return node; };

// Cast pictures are not works: the picture lists leave out what this set names.
export function castFiles() { try { return new Set(JSON.parse(localStorage.getItem(CAST_FILES_KEY) || '[]')); } catch { return new Set(); } }
function markCastFiles(add = [], remove = []) {
    const files = castFiles(); for (const file of add) files.add(file); for (const file of remove) files.delete(file);
    try { localStorage.setItem(CAST_FILES_KEY, JSON.stringify([...files].slice(-2000))); } catch { /* The lists may show it; it is still deleted when unsaved. */ }
    window.dispatchEvent(new CustomEvent('freevideo-cast-files'));
}

export function mountCastPhoto({container, api, text, viewURL, onState = () => {}, fetchApi, client}) {
    const tr = text || (en => en);
    const fetcher = fetchApi || (api ? api.fetchApi.bind(api) : globalThis.fetch.bind(globalThis));
    client = client || createImageGenClient(fetcher);
    // Deletes go out with keepalive, so the ones sent as the tab closes still reach the engine.
    const keeper = createImageGenClient((url, options = {}) => fetcher(url, options.method === 'POST' ? {...options, keepalive: true} : options));
    for (const name of ['./cast_generate.css', './cast_photo.css']) {
        const href = new URL(name, import.meta.url).href;
        if (!document.querySelector(`link[href="${href}"]`)) { const css = el('link'); css.rel = 'stylesheet'; css.href = href; document.head.append(css); }
    }
    const root = el('div', 'fv-cg fv-cp'); root.dataset.mode = 'photo';
    const body = el('div', 'fv-cg-body'), side = el('div', 'fv-cg-side');

    // A picture slot (the photo, the clothes picture): empty → choose or drop; filled → click edits it in the image
    // editor, Replace chooses another, Restore puts the original back after an edit.
    function slot({label, empty, use, onChange}) {
        const box = el('div', 'fv-cp-slot');
        const face = el('button', 'fv-cp-drop'); face.type = 'button'; face.setAttribute('aria-label', label);
        const input = el('input'); input.type = 'file'; input.accept = 'image/*'; input.hidden = true;
        const shown = el('img'); shown.alt = ''; shown.hidden = true;
        const hint = el('span', 'fv-cp-hint'); hint.append(el('strong', null, empty[0]), el('span', null, empty[1]));
        const tools = el('div', 'fv-cp-slot-tools');
        const edited = el('span', 'fv-cp-edited', tr('Edited', '已编辑'));
        const restore = el('button', 'fv-cp-tool', tr('Restore', '恢复原图')); restore.type = 'button';
        const replace = el('button', 'fv-cp-tool', tr('Replace', '更换')); replace.type = 'button';
        const remove = el('button', 'fv-cp-tool', tr('Remove', '移除')); remove.type = 'button';
        tip(restore, tr('Restore', '恢复原图'), tr('Put the original picture back', '换回编辑前的原图'));
        tip(replace, tr('Replace', '更换'), tr('Choose another picture', '换一张图片'));
        tip(remove, tr('Remove', '移除'), tr('Take this picture out', '拿掉这张图片'));
        tools.append(edited, restore, replace, remove);
        face.append(shown, hint); box.append(face, input, tools);
        let record = null;
        const paint = () => {
            box.dataset.filled = String(!!record); shown.hidden = !record; if (record) shown.src = record.url; else shown.removeAttribute('src');
            edited.hidden = restore.hidden = !record?.original; replace.hidden = remove.hidden = !record;
            tip(face, label, record ? tr('Click to edit it in the image editor', '点击在图像编辑器中修改') : use);
        };
        const take = files => {
            const file = Array.from(files || []).find(f => /^image\//.test(f.type)); if (!file) return;
            if (record) { URL.revokeObjectURL(record.url); if (record.original) URL.revokeObjectURL(record.original.url); }
            record = {file, url: URL.createObjectURL(file)}; paint(); onChange();
        };
        async function edit() {
            let outcome;
            try {
                const {openImageEditor} = await import('./image_editor.js');
                outcome = await openImageEditor({source: record.original?.url || record.url, name: record.file.name || 'picture.png', edit: record.edit || null,
                    role: 'reference', t: tr, resolve: url => url, upload: async blob => client.upload(blob), imageGenClient: client});
            } catch { attention = tr('This picture cannot be edited right now.', '这张图暂时无法编辑。'); render(); return; }
            if (!outcome || !record || disposed) return;
            if (outcome.restore) { back(); return; }
            if (!outcome.blob) return;
            const file = new File([outcome.blob], outcome.name || 'edited.png', {type: outcome.blob.type || 'image/png'});
            if (record.original) URL.revokeObjectURL(record.url); else record.original = {file: record.file, url: record.url};
            Object.assign(record, {file, url: URL.createObjectURL(file), edit: outcome.edit || null}); paint(); onChange();
        }
        const back = () => { if (!record?.original) return; URL.revokeObjectURL(record.url); Object.assign(record, record.original); delete record.original; delete record.edit; paint(); onChange(); };
        face.onclick = () => { if (generator.state().running) return; if (record) void edit(); else input.click(); };
        replace.onclick = () => { if (!generator.state().running) input.click(); };
        remove.onclick = () => { if (generator.state().running || !record) return; URL.revokeObjectURL(record.url); if (record.original) URL.revokeObjectURL(record.original.url); record = null; paint(); onChange(); };
        restore.onclick = back;
        input.onchange = () => { take(input.files); input.value = ''; };
        face.ondragover = event => { event.preventDefault(); box.dataset.over = 'true'; };
        face.ondragleave = () => { delete box.dataset.over; };
        face.ondrop = event => { event.preventDefault(); delete box.dataset.over; if (!generator.state().running) take(event.dataTransfer.files); };
        paint();
        return {element: box, face, get: () => record, disable: value => { face.disabled = replace.disabled = remove.disabled = restore.disabled = value; },
            dispose: () => { if (record) { URL.revokeObjectURL(record.url); if (record.original) URL.revokeObjectURL(record.original.url); } }};
    }

    const photoSlot = slot({label: tr('Photo of the person', '人物照片'), empty: [tr('Add a photo', '添加照片'), tr('Drop a photo of the person here, or choose one', '把人物照片拖到这里，或点击选择')],
        use: tr('One clear photo of the person; the face decides the likeness', '一张清晰的人物照片，长相以脸部为准'), onChange: () => { photoRef = ''; render(); }});
    photoSlot.element.classList.add('fv-cp-photo');
    // Clothes: as in the photo, as described, or as in a clothes picture (with words for details if wanted).
    const wear = el('div', 'fv-cp-wear'); wear.setAttribute('role', 'radiogroup'); wear.setAttribute('aria-label', tr('Clothes', '服装'));
    const modes = [['keep', tr('Keep clothes', '保持服装'), tr('Dress the views as in the photo', '各视角保持照片里的服装')],
        ['text', tr('Describe', '按描述更换'), tr('Dress the views as described', '各视角按描述更换服装')],
        ['image', tr('Picture', '上传服装图'), tr('Dress the views in the clothes of a picture', '各视角换上服装图里的衣服')]];
    const modeButtons = modes.map(([value, name, use]) => { const b = el('button', null, name); b.type = 'button'; b.setAttribute('role', 'radio'); b.dataset.value = value; tip(b, name, use); b.onclick = () => setWear(value); wear.append(b); return b; });
    const outfitSlot = slot({label: tr('Clothes picture', '服装图'), empty: [tr('Add a clothes picture', '添加服装图'), tr('Drop it here, or choose one', '拖到这里，或点击选择')],
        use: tr('A picture of the clothes to wear', '要换上的服装图片'), onChange: () => { outfitRef = ''; render(); }});
    outfitSlot.element.classList.add('fv-cp-outfit-slot');
    const field = el('textarea', 'fv-cg-text fv-cp-outfit'); field.maxLength = 400; field.rows = 2;
    field.setAttribute('aria-label', tr('Clothes', '服装'));
    const note = el('div', 'fv-cg-note'); note.setAttribute('role', 'status');
    const foot = el('div', 'fv-cg-foot'); foot.append(note);
    // After a new face (drawn again, or the photo itself), the other three can follow it; never on their own.
    const rest = el('button', 'fv-cp-tool fv-cp-rest', tr('Redraw the others to match', '按新脸重画其余三张')); rest.type = 'button'; rest.hidden = true;
    tip(rest, tr('Redraw the others to match', '按新脸重画其余三张'), tr('Make the front, side and back again from the new face', '用新的脸部特写重新生成正面、侧面和背面'));
    let faceChanged = false;
    rest.onclick = async () => {
        faceChanged = false; render();
        // An edited face (and front) is uploaded once and stands in for the made one as the reference.
        for (const view of ['face', 'front']) if (edits[view] && !edits[view].ref) { try { edits[view].ref = await referenceOf({file: edits[view].file}); generator.setOverride(view, edits[view].ref); } catch { /* the made picture stays the reference */ } }
        for (const view of ['front', 'side', 'back']) { if (disposed) return; if (view !== 'front' || !edits.front) await redraw(view); }
    };
    foot.append(rest);
    side.append(photoSlot.element, wear, outfitSlot.element, field, foot);
    // The views, in the order of the sheet; the face cell is wider. A finished view opens in the image editor on click;
    // on hover it offers Draw again (and Use the photo on the face), and Restore after an edit.
    const grid = el('div', 'fv-cg-views');
    const edits = {};
    const cells = Object.fromEntries(SHOW.map(view => {
        const cell = el('div', 'fv-cg-view'); cell.dataset.view = view; cell.tabIndex = 0; cell.setAttribute('role', 'button');
        const title = tr(...VIEW_TITLES[view]);
        const image = el('img'); image.alt = ''; image.draggable = false;
        const label = el('span', 'fv-cp-label', title);
        const edited = el('span', 'fv-cp-edited', tr('Edited', '已编辑')); edited.hidden = true;
        const percent = el('span', 'fv-cg-percent'); const meter = el('i', 'fv-cg-meter'); const fill = el('b'); meter.append(fill);
        const tools = el('div', 'fv-cp-cell-tools');
        const tool = (name, use, run) => { const b = el('button', 'fv-cp-tool', name); b.type = 'button'; tip(b, name, use); b.addEventListener('click', event => { event.stopPropagation(); run(); }); tools.append(b); return b; };
        const again = tool(tr('Draw again', '重画'), tr('Make this view again; the others stay', '重新生成这个视角，其余不变'), () => void redraw(view));
        const usePhoto = view === 'face' ? tool(tr('Use the photo', '用原照片'), tr('Put the photo itself in as the face close-up', '把原照片作为脸部特写'), () => {
            if (!photoSlot.get()) return; dropEdit('face'); generator.useOriginal(ORIGINAL); faceChanged = true; render();
        }) : null;
        const restore = tool(tr('Restore', '恢复原图'), tr('Put the picture back as it was made', '换回生成时的样子'), () => { dropEdit(view); if (view === 'face' || view === 'front') faceChanged = true; render(); });
        cell.append(image, label, edited, percent, meter, tools); grid.append(cell);
        tip(cell, title, tr('Click to edit it in the image editor', '点击在图像编辑器中修改'));
        const open = () => { if (cell.dataset.state === 'done' && !generator.state().running) void editView(view); };
        cell.addEventListener('click', open);
        cell.addEventListener('keydown', event => { if ((event.key === 'Enter' || event.key === ' ') && event.target === cell) { event.preventDefault(); open(); } });
        return [view, {cell, image, percent, fill, title, edited, again, usePhoto, restore}];
    }));
    const sourceOf = view => { const r = pictureOf(view) || {}; return r.file ? (r.original ? photoSlot.get()?.url || '' : viewURL(r.file)) : ''; };
    const dropEdit = view => { const e = edits[view]; if (!e) return; URL.revokeObjectURL(e.url); delete edits[view]; generator.setOverride(view, ''); };
    async function redraw(view) {
        dropEdit(view);
        await generator.redo(MAKE.indexOf(view));
        if (view === 'face' && generator.state().pictures[0].file) { faceChanged = true; render(); }
    }
    // A view opens in the image editor; the edited picture takes its place on the card (marked, and Restore puts the
    // made one back). An edited face or front is what the others are redrawn from.
    async function editView(view) {
        let outcome;
        try {
            const {openImageEditor} = await import('./image_editor.js');
            outcome = await openImageEditor({source: sourceOf(view), name: `${view}.png`, edit: edits[view]?.edit || null,
                role: 'reference', t: tr, resolve: url => url, upload: async blob => client.upload(blob), imageGenClient: client});
        } catch { attention = tr('This picture cannot be edited right now.', '这张图暂时无法编辑。'); render(); return; }
        if (!outcome || disposed) return;
        if (outcome.restore) { dropEdit(view); if (view === 'face' || view === 'front') faceChanged = true; render(); return; }
        if (!outcome.blob) return;
        dropEdit(view);
        const file = new File([outcome.blob], outcome.name || `${view}-edited.png`, {type: outcome.blob.type || 'image/png'});
        edits[view] = {file, url: URL.createObjectURL(file), edit: outcome.edit || null};
        if (view === 'face' || view === 'front') faceChanged = true;
        render();
    }
    body.append(side, grid); root.append(body); container.append(root);

    // The main action lives in the dialog's own button row (the host places it): Generate, Stop, then Generate all again.
    const action = el('button', 'fv-cp-action'); action.type = 'button';

    let photoRef = '', outfitRef = '', mode = 'keep', info = null, infoTimer = null, disposed = false, failureKey = null, attention = '', last = '';
    // references: what this dialog uploaded to the image engine (name → used by a generation).
    const made = new Set(), saved = new Set(), references = new Map();
    let cardSaved = false;
    // Drafts (engines that list the "cast-draft" purpose): views are made as drafts in FreeVideo's own folder, never works;
    // Save hands their names to the card, which makes them its own files; a draft let go is discarded, and the engine
    // deletes any left a day. Older engines: views are pictures in the history, hidden by freevideo.cast.files and deleted
    // through history-delete.
    let drafts = false;
    // The engine's copies let go: drafts and uploads, in one discard-uploads call (keepalive, so a closing tab still
    // sends it). Silent where the engine has no such call.
    const discardNames = names => { if (names.length) Promise.resolve().then(() => keeper.discardUploads(names)).catch(() => {}); };
    // A view made here and then replaced, or left unsaved, goes at once.
    const discard = file => {
        if (!file || saved.has(file)) return;
        if (/^freevideo-draft\//.test(file)) { made.delete(file); discardNames([file]); return; }
        if (!/^freevideo-image\//.test(file)) return;
        made.delete(file); markCastFiles([], [file]);
        void keeper.historyDelete?.(file).catch(() => {});
    };
    // The engine's copies of the uploaded pictures go too (discard-uploads; the engine keeps any a task still uses).
    // Closed without saving: the ones no generation used. Saved: all of them. Silent where the engine has no such call yet.
    const releaseReferences = (all = false) => {
        const names = [...references].filter(([, used]) => all || !used).map(([name]) => name);
        for (const name of names) references.delete(name);
        discardNames(names);
    };
    // Views as drafts when the engine offers them: the same generate call with the purpose.
    const draftClient = {...client, generate: args => client.generate({...args, ...(drafts ? {purpose: 'cast-draft'} : {})})};
    const otherUsage = createOtherUsageNotice(side, tr, fetcher);
    const generator = createCastGenerator({client: draftClient, kind: 'character', views: MAKE, beforeGenerate: query => otherUsage.check(query), onDiscard: discard,
        onChange: state => {
            const fresh = state.pictures.filter(r => r.file && !r.original && !made.has(r.file)).map(r => r.file);
            for (const file of fresh) made.add(file);
            if (fresh.length && !drafts) markCastFiles(fresh.filter(file => /^freevideo-image\//.test(file)));
            render(state);
        }});

    function setWear(value) { mode = value; render(); if (value === 'text') field.focus(); }
    function missing() { return info && !info.installed && !info.ready; }
    const pictureOf = view => generator.state().pictures[MAKE.indexOf(view)];
    function render(state = generator.state()) {
        const running = state.running, isMissing = missing(), finished = generator.done() && !running;
        root.dataset.state = isMissing ? 'missing' : running ? 'running' : finished ? 'done' : state.error ? 'failed' : 'idle';
        photoSlot.disable(running); outfitSlot.disable(running); for (const b of modeButtons) { b.disabled = running; b.setAttribute('aria-checked', String(b.dataset.value === mode)); }
        outfitSlot.element.hidden = mode !== 'image';
        field.hidden = mode === 'keep'; field.readOnly = running;
        field.placeholder = mode === 'image' ? tr('Details to add, if any (optional)', '可补充细节（选填）') : tr('Describe the clothes, e.g. a navy wool coat over a white shirt', '描述服装，例如：白衬衫外穿藏青色羊毛大衣');
        // Shown or hidden by the dialog (only on this tab); missing image models hide it from here.
        action.dataset.missing = String(!!isMissing);
        action.textContent = running ? tr('Stop', '停止') : finished ? tr('Generate all again', '重新生成全部') : tr('Generate', '生成');
        action.dataset.primary = String(!running && !finished);
        const why = !photoSlot.get() ? tr('Add a photo first', '先添加照片') : mode === 'image' && !outfitSlot.get() ? tr('Add a clothes picture first', '先添加服装图')
            : mode === 'text' && !field.value.trim() ? tr('Describe the clothes first', '先描述服装') : '';
        if (why && !running) { action.setAttribute('aria-disabled', 'true'); action.dataset.fvTipWhy = why; } else { action.removeAttribute('aria-disabled'); delete action.dataset.fvTipWhy; }
        tip(action, action.textContent, running ? tr('Stop making the views', '停止生成') : tr('Make the four views from the photo', '用照片生成四个视角'));
        for (const view of SHOW) {
            const c = cells[view], r = pictureOf(view) || {}, index = MAKE.indexOf(view), active = running && index === state.current && !r.file;
            const src = r.file ? edits[view]?.url || sourceOf(view) : '';
            if (c.image.getAttribute('src') !== src) src ? c.image.src = src : c.image.removeAttribute('src');
            c.cell.dataset.state = r.file ? 'done' : active ? 'running' : 'empty'; c.cell.dataset.original = String(!!r.original);
            c.cell.setAttribute('aria-label', c.title + (active ? ' ' + Math.floor(r.fraction * 100) + '%' : ''));
            c.percent.textContent = active && !state.waiting ? Math.floor(r.fraction * 100) + '%' : '';
            c.fill.style.width = (active ? r.fraction * 100 : 0) + '%';
            c.edited.hidden = !edits[view]; c.restore.hidden = !edits[view]; c.again.hidden = !r.file && !(state.error && !running);
            c.again.disabled = running || (index > 0 && !state.pictures[0].file);
            if (c.usePhoto) c.usePhoto.hidden = !photoSlot.get() || !!r.original || running;
        }
        if (state.error) {
            const key = `${state.error}:${state.report_id || ''}`;
            if (failureKey !== key) { renderImageFailure(note, {...state, info}, tr, {fetchApi: fetcher}); failureKey = key; }
        } else {
            failureKey = null;
            note.textContent = isMissing ? imagePackUnavailableMessage(info, tr)
                : running && state.waiting ? tr('Added to the queue. It starts when the previous one finishes.', '已加入队列，前面的完成后自动开始。') : attention;
        }
        note.hidden = !note.textContent && !note.firstChild; note.dataset.failed = String(!!state.error);
        rest.hidden = !faceChanged || running || !state.pictures.slice(1).some(row => row.file); rest.disabled = running;
        const count = state.pictures.filter(r => r.file).length, fraction = running && state.current >= 0 ? state.pictures[state.current]?.fraction || 0 : 0;
        const progress = generator.done() ? 1 : (count + fraction) / MAKE.length;
        const summary = JSON.stringify([running, finished, Math.round(progress * 100)]);
        if (summary !== last) { last = summary; onState({busy: running, progress, done: finished}); }
    }
    field.addEventListener('input', () => { if (attention) attention = ''; render(); });
    async function referenceOf(record) {
        const name = await client.upload(record.file); references.set(name, false); return name;
    }
    async function begin() {
        if (action.getAttribute('aria-disabled') === 'true') { attention = action.dataset.fvTipWhy || ''; render(); return; }
        attention = ''; render();
        try {
            if (!photoRef) photoRef = await referenceOf(photoSlot.get());
            if (mode === 'image' && !outfitRef) outfitRef = await referenceOf(outfitSlot.get());
        } catch (error) { attention = error.message || tr('Could not read this picture. Please choose another one.', '无法读取这张图片，请换一张。'); render(); return; }
        if (disposed) return;
        for (const name of [photoRef, mode === 'image' ? outfitRef : '']) if (name && references.has(name)) references.set(name, true);
        void generator.start('', {photo: photoRef, outfit: mode === 'keep' ? '' : field.value, outfitImage: mode === 'image' ? outfitRef : ''});
    }
    action.onclick = () => generator.state().running ? void generator.stop() : void begin();
    async function checkInfo() {
        try { info = await client.info(); } catch { info = null; }
        drafts = Array.isArray(info?.purposes) && info.purposes.includes('cast-draft');
        if (disposed) return; render(); clearTimeout(infoTimer);
        if (missing()) infoTimer = setTimeout(checkInfo, IMAGE_GEN_INFO_POLL_MS);
    }
    // The tab closing or the page going away: unsaved views go with it.
    const leaving = () => {
        // Drafts go in one discard-uploads call (with keepalive, so a closing tab still sends it); older pictures one by one.
        const left = [...made].filter(file => /^freevideo-draft\//.test(file) && !saved.has(file));
        for (const file of left) made.delete(file);
        discardNames(left);
        for (const file of [...made]) discard(file);
        releaseReferences(cardSaved);
    };
    window.addEventListener('pagehide', leaving);

    async function picture(src) { const blob = await (await fetch(src)).blob(); return createImageBitmap(blob); }
    async function sheet() {
        const {body: [bw, bh], face: [fw], gap, ground} = SHEET;
        const width = fw + 3 * bw + 5 * gap, height = bh + 2 * gap;
        const canvas = new OffscreenCanvas(width, height), g = canvas.getContext('2d');
        g.fillStyle = ground; g.fillRect(0, 0, width, height);
        let x = gap;
        for (const view of SHOW) {
            const r = pictureOf(view), cw = view === 'face' ? fw : bw;
            const image = await picture(edits[view]?.url || (r.original ? photoSlot.get().url : viewURL(r.file)));
            const k = Math.min(cw / image.width, bh / image.height), w = Math.round(image.width * k), h = Math.round(image.height * k);
            g.drawImage(image, x + Math.round((cw - w) / 2), gap + Math.round((bh - h) / 2), w, h); image.close?.();
            x += cw + gap;
        }
        const blob = await canvas.convertToBlob({type: 'image/png'});
        return new File([blob], 'character-sheet.png', {type: 'image/png'});
    }

    setWear('keep'); render(); void checkInfo();
    return {
        action,
        // The sheet, the four views, and the photo itself unless it already is the front (the card keeps what it was made from).
        async getPictures() {
            const state = generator.state();
            if (!generator.done() || state.running) return [];
            const views = SHOW.map(view => { const r = pictureOf(view); return {file: edits[view]?.file || (r.original ? photoSlot.get().file : r.file), label: view}; });
            const extra = photoSlot.get() && !pictureOf('front').original ? [{file: photoSlot.get().file, label: 'other'}] : [];
            return [{file: await sheet(), label: 'sheet'}, ...views, ...extra];
        },
        // The card keeps the made views it shows; a view replaced by its edit is let go like any unsaved one.
        markSaved() { cardSaved = true; generator.state().pictures.forEach((r, at) => { if (r.file && !edits[MAKE[at]]) saved.add(r.file); }); },
        isBusy: () => generator.state().running,
        cancel: () => generator.stop(),
        destroy() {
            disposed = true; clearTimeout(infoTimer); otherUsage.dispose(); window.removeEventListener('pagehide', leaving);
            // Cancel, close, Esc: the views made here and not saved are deleted.
            leaving();
            for (const view of Object.keys(edits)) dropEdit(view);
            void generator.dispose(); photoSlot.dispose(); outfitSlot.dispose(); action.remove(); root.remove();
        },
        element: root,
    };
}
