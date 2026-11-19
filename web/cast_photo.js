// The four views of a new character, from one of three sources, in the order of the final picture: face close-up | front |
// side | back, and one 1 × 4 sheet of them.
//   photo: a photo of a real person. The face close-up is made from the photo first (Qwen-Image 2.1 when it is ready); the
//          front, side and back are made from the photo and that close-up, the side and back also from the front.
//   image: any picture of a character (a drawing, a render, a photo): the same steps, in the picture's own style.
//   ai:    a description. The front is made first (from the words, or wearing a clothes picture), then the face close-up
//          from the front, then the side and back from the close-up and the front.
// Clothes stay as in the picture, follow a description, or follow a clothes picture. Every finished view opens in the
// image editor on click (the edit takes its place on the sheet and the card) and can be drawn again on its own.
// Save puts the sheet, the four views and the source picture on the card.
//
// Privacy: everything stays on this computer. The pictures go to the local image engine only when Generate is pressed.
// Every view made here is a work of the picture library from the moment it is made (source: character), whether or not
// the card is saved; deleting it there is the only way it goes. The pictures uploaded for the views go when the dialog is left.
import {createImageGenClient, imagePackUnavailableMessage, renderImageFailure, IMAGE_GEN_INFO_POLL_MS} from './image_generation.js';
import {createCastGenerator} from './cast_generate.js';
import {faceCloseUp, dressMaskFile, facesCamera, repaintTook, closeUpShare, neutralCloseUp, CLOSEUP_FRAME} from './cast_face_crop.js';
import {createOtherUsageNotice} from './other_usage.js';

// Generation order per source; the order on screen and on the sheet is SHOW. The first view made is the one the others
// are drawn from (the face from a picture; the front from a description).
const MAKE = Object.freeze({photo: Object.freeze(['face', 'front', 'side', 'back']), image: Object.freeze(['face', 'front', 'side', 'back']),
    ai: Object.freeze(['front', 'face', 'side', 'back'])});
const SHOW = Object.freeze(['face', 'front', 'side', 'back']);
const VIEW_TITLES = Object.freeze({front: ['Front', '正面'], side: ['Side', '侧面'], back: ['Back', '背面'], face: ['Face', '脸部特写']});
// The sheet: the cast sheet's rules (equal height, light grey ground, 48 px gaps, each picture whole and centred), one
// row of four, the face cell 1.4 times as wide as a full-body cell.
const SHEET = Object.freeze({body: [736, 1302], face: [1030, 1302], gap: 48, ground: 'rgb(238 238 238)'});
const ORIGINAL = 'photo:original';
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const tip = (node, name, use) => { node.dataset.fvTipName = name; if (use) node.dataset.fvTipUse = use; else delete node.dataset.fvTipUse; return node; };

// The image client the views are made through. The engine removes a page upload when the job that sent it ends, and the
// photo (an edited view, the clothes picture) goes into several jobs: the first job takes the upload itself, each later
// one a fresh upload of the same picture (the job it goes with removes it, so it is not kept in references).
// references: name → used by a generation (what the dialog uploaded); purpose: the purpose each request carries ('' for none).
export function photoClient(client, references = new Map(), purpose = () => '') {
    const sources = new Map(), spent = new Set();
    async function upload(file) { const name = await client.upload(file); references.set(name, false); sources.set(name, file); return name; }
    async function fresh(name) {
        if (!sources.has(name)) return name;
        if (!spent.has(name)) { spent.add(name); return name; }
        return client.upload(sources.get(name));
    }
    return {...client, upload, generate: async args => {
        const images = await Promise.all((args.images || []).map(fresh)), why = purpose();
        return client.generate({...args, images, ...(why ? {purpose: why} : {})});
    }};
}

// The words of each source: the picture slot, the main action's tip, what is missing before Generate, and the button that
// redraws the rest after the first view changed.
function wordsOf(source, tr) {
    if (source === 'ai') return {
        use: tr('Make the face close-up, front, side and back from the description', '按描述生成脸部特写、正面、侧面和背面'),
        rest: [tr('Redraw the others to match', '按新正面重画其余三张'), tr('Make the face close-up, side and back again from the new front', '用新的正面重新生成脸部特写、侧面和背面')]};
    const image = source === 'image';
    return {
        slot: image ? [tr('Picture of the character', '角色图片'), tr('Add a picture', '添加图片'), tr('Drop a picture of the character here, or choose one', '把角色图片拖到这里，或点击选择'),
            tr('One clear picture of the character, in any style; the views keep its style', '一张清晰的角色图片，任何画风都可以，各视角保持原图风格')]
            : [tr('Photo of the person', '人物照片'), tr('Add a photo', '添加照片'), tr('Drop a photo of the person here, or choose one', '把人物照片拖到这里，或点击选择'),
                tr('One clear photo of the person; the face decides the likeness', '一张清晰的人物照片，长相以脸部为准')],
        first: image ? tr('Add a picture first', '先添加图片') : tr('Add a photo first', '先添加照片'),
        use: image ? tr('Make the four views from the picture, in its style', '用图片按原图风格生成四个视角') : tr('Make the four views from the photo', '用照片生成四个视角'),
        keep: image ? tr('Dress the views as in the picture', '各视角保持图片里的服装') : tr('Dress the views as in the photo', '各视角保持照片里的服装'),
        original: image ? [tr('Use the picture', '用原图'), tr('Put the picture itself in as the face close-up', '把原图作为脸部特写')]
            : [tr('Use the photo', '用原照片'), tr('Put the photo itself in as the face close-up', '把原照片作为脸部特写')],
        rest: [tr('Redraw the others to match', '按新脸重画其余三张'), tr('Make the front, side and back again from the new face', '用新的脸部特写重新生成正面、侧面和背面')]};
}

// source: 'photo' | 'image' | 'ai' (see the top of this file).
export function mountCastPhoto({container, api, text, viewURL, onState = () => {}, fetchApi, client, source = 'photo'}) {
    const tr = text || (en => en);
    const fetcher = fetchApi || (api ? api.fetchApi.bind(api) : globalThis.fetch.bind(globalThis));
    client = client || createImageGenClient(fetcher);
    const ai = source === 'ai', make = MAKE[source] || MAKE.photo, base = make[0], words = wordsOf(source, tr);
    // "Upload images": every view can be a picture of the user's own; Generate then makes only the views still empty.
    const canOwn = source === 'image';
    // Deletes go out with keepalive, so the ones sent as the tab closes still reach the engine.
    const keeper = createImageGenClient((url, options = {}) => fetcher(url, options.method === 'POST' ? {...options, keepalive: true} : options));
    for (const name of ['./cast_generate.css', './cast_photo.css']) {
        const href = new URL(name, import.meta.url).href;
        if (!document.querySelector(`link[href="${href}"]`)) { const css = el('link'); css.rel = 'stylesheet'; css.href = href; document.head.append(css); }
    }
    const root = el('div', 'fv-cg fv-cp'); root.dataset.mode = source;
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
            let outcome, loadFailed = false;
            try {
                const {openImageEditor} = await import('./image_editor.js').catch(error => { loadFailed = true; throw error; });
                outcome = await openImageEditor({source: record.original?.url || record.url, name: record.file.name || 'picture.png', edit: record.edit || null,
                    role: 'reference', t: tr, resolve: url => url, upload: async blob => client.upload(blob), imageGenClient: client});
            } catch { attention = loadFailed ? tr('The image editor did not load. Reload the page and try again.', '图片编辑器没有加载出来。请刷新页面后再试。') : tr('This picture could not be read. Choose another picture.', '这张图无法读取。请重新选择一张。'); render(); return; }
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

    // The source: a picture slot, or the description of the character.
    const photoSlot = ai ? null : slot({label: words.slot[0], empty: [words.slot[1], words.slot[2]], use: words.slot[3], onChange: () => { photoRef = ''; render(); }});
    photoSlot?.element.classList.add('fv-cp-photo');
    const describe = ai ? el('textarea', 'fv-cg-text fv-cp-describe') : null;
    if (describe) {
        describe.placeholder = tr('Describe the character', '描述角色'); describe.setAttribute('aria-label', describe.placeholder); describe.maxLength = 1000;
        describe.addEventListener('input', () => { if (attention) attention = ''; render(); });
        // Enter is a new line; Ctrl/Cmd+Enter generates.
        describe.addEventListener('keydown', event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !generator.state().running) { event.preventDefault(); void begin(); } });
    }
    // The LoRA picker of image generation, for a description (its trigger words go into the description).
    let picker = null;
    // Clothes: as in the picture, as described, or as in a clothes picture (with words for details if wanted). From a
    // description the clothes are in the description, or in a clothes picture.
    const wear = el('div', 'fv-cp-wear'); wear.setAttribute('role', 'radiogroup'); wear.setAttribute('aria-label', tr('Clothes', '服装'));
    const modes = ai ? [['text', tr('As described', '按描述'), tr('Dress the character as the description says', '按描述里的服装生成')],
        ['image', tr('Picture', '上传服装图'), tr('Dress the character in the clothes of a picture', '换上服装图里的衣服')]]
        : [['keep', tr('Keep clothes', '保持服装'), words.keep],
            ['text', tr('Describe', '按描述更换'), tr('Dress the views as described', '各视角按描述更换服装')],
            ['image', tr('Picture', '上传服装图'), tr('Dress the views in the clothes of a picture', '各视角换上服装图里的衣服')]];
    wear.dataset.count = String(modes.length);
    const modeButtons = modes.map(([value, name, use]) => { const b = el('button', null, name); b.type = 'button'; b.setAttribute('role', 'radio'); b.dataset.value = value; tip(b, name, use); b.onclick = () => setWear(value); wear.append(b); return b; });
    const outfitSlot = slot({label: tr('Clothes picture', '服装图'), empty: [tr('Add a clothes picture', '添加服装图'), tr('Drop it here, or choose one', '拖到这里，或点击选择')],
        use: tr('A picture of the clothes to wear', '要换上的服装图片'), onChange: () => { outfitRef = ''; render(); }});
    outfitSlot.element.classList.add('fv-cp-outfit-slot');
    const field = ai ? null : el('textarea', 'fv-cg-text fv-cp-outfit');
    if (field) { field.maxLength = 400; field.rows = 2; field.setAttribute('aria-label', tr('Clothes', '服装')); }
    const note = el('div', 'fv-cg-note'); note.setAttribute('role', 'status');
    const foot = el('div', 'fv-cg-foot'); foot.append(note);
    // After a new first view (the face from a picture, the front from a description: drawn again, edited, or the picture
    // itself), the other three can follow it; never on their own.
    const rest = el('button', 'fv-cp-tool fv-cp-rest', words.rest[0]); rest.type = 'button'; rest.hidden = true;
    tip(rest, words.rest[0], words.rest[1]);
    let faceChanged = false;
    rest.onclick = async () => {
        faceChanged = false; render();
        // An edited face (and front) is uploaded once and stands in for the made one as the reference.
        for (const view of ['face', 'front']) if (edits[view] && !edits[view].ref) { try { edits[view].ref = await referenceOf({file: edits[view].file}); generator.setOverride(view, edits[view].ref); } catch { /* the made picture stays the reference */ } }
        // The others in the order they are made; an edited face or front stays as edited.
        for (const view of make.slice(1)) { if (disposed) return; if (!((view === 'face' || view === 'front') && edits[view])) await redraw(view); }
    };
    foot.append(rest);
    if (ai) {
        // The clothes choice and the LoRA picker share one row as wide as the description.
        const row = el('div', 'fv-cg-row fv-cp-ai-row'); row.append(wear);
        side.append(describe, row, outfitSlot.element, foot);
        import('./image_lora.js').then(({createImageLoras}) => {
            if (disposed) return;
            picker = createImageLoras({t: tr, fetchApi: fetcher, getPrompt: () => describe.value, setPrompt: words => { describe.value = words; }, onChange: () => render()});
            row.append(picker.element); render();
        }).catch(() => { /* no LoRA picker: the views are made without LoRAs */ });
    } else side.append(photoSlot.element, wear, outfitSlot.element, field, foot);
    // The views, in the order of the sheet; the face cell is wider. A finished view opens in the image editor on click;
    // on hover it offers Draw again (and Use the photo on the face), and Restore after an edit.
    const grid = el('div', 'fv-cg-views');
    // edits: view → the view as edited in the image editor. own: view → a picture the user put in for it
    // ({file, url, ref}: ref is its upload to the engine, made only when a generation needs it).
    const edits = {}, own = {};
    const ownInput = el('input'); ownInput.type = 'file'; ownInput.accept = 'image/*'; ownInput.hidden = true;
    let ownFor = '';
    ownInput.onchange = () => { takeOwn(ownFor, ownInput.files); ownInput.value = ''; };
    const choosing = view => { if (generator.state().running) return; ownFor = view; ownInput.click(); };
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
        const usePhoto = view === 'face' && !ai ? tool(words.original[0], words.original[1], () => {
            if (!photoSlot.get()) return; dropEdit('face'); generator.useOriginal(ORIGINAL); faceChanged = true; render();
        }) : null;
        const restore = tool(tr('Restore', '恢复原图'), tr('Put the picture back as it was made', '换回生成时的样子'), () => { dropEdit(view); if (leads(view)) faceChanged = true; render(); });
        const mine = canOwn ? tool(tr('Add picture', '添加图片'), tr('Use a picture of your own for this view', '这个视角用自己的图片'), () => choosing(view)) : null;
        const clear = canOwn ? tool(tr('Remove', '移除'), tr('Remove this picture. Generate makes this view.', '移除这张图片，生成时补上这个视角。'), () => dropOwn(view)) : null;
        cell.append(image, label, edited, percent, meter, tools); grid.append(cell);
        tip(cell, title, canOwn ? tr('Click or drop a picture', '点击或拖入图片') : tr('Click to edit it in the image editor', '点击在图像编辑器中修改'));
        const open = () => { if (generator.state().running) return; if (cell.dataset.state === 'done') void editView(view); else if (canOwn && cell.dataset.state === 'empty') choosing(view); };
        if (canOwn) {
            cell.addEventListener('dragover', event => { event.preventDefault(); });
            cell.addEventListener('drop', event => { event.preventDefault(); event.stopPropagation(); if (!generator.state().running) takeOwn(view, event.dataTransfer.files); });
        }
        cell.addEventListener('click', open);
        cell.addEventListener('keydown', event => { if ((event.key === 'Enter' || event.key === ' ') && event.target === cell) { event.preventDefault(); open(); } });
        return [view, {cell, image, percent, fill, title, edited, again, usePhoto, restore, mine, clear}];
    }));
    // A change to these views is one the others can be redrawn from: the face and the front from a picture; the front
    // from a description.
    const leads = view => ai ? view === 'front' : view === 'face' || view === 'front';
    const sourceOf = view => { if (own[view]) return own[view].url; const r = pictureOf(view) || {}; return r.file ? (r.original ? photoSlot?.get()?.url || '' : viewURL(r.file)) : ''; };
    const dropEdit = view => { const e = edits[view]; if (!e) return; URL.revokeObjectURL(e.url); delete edits[view]; generator.setOverride(view, ''); };
    // A picture the user puts in for a view (choose or drop): it stands in the view; the others are made around it.
    function takeOwn(view, files) {
        const file = Array.from(files || []).find(f => /^image\//.test(f.type));
        if (!view || !file || generator.state().running) return;
        dropEdit(view); releaseOwn(view);
        own[view] = {file, url: URL.createObjectURL(file), ref: ''};
        generator.setOwn(make.indexOf(view), `own:${view}`);
        // The views made before were made around the old one of these.
        if (leads(view) && generator.state().pictures.some((row, at) => at !== make.indexOf(view) && row.file && !row.own)) faceChanged = true;
        attention = ''; render();
    }
    function releaseOwn(view) { const o = own[view]; if (!o) return; URL.revokeObjectURL(o.url); delete own[view]; }
    function dropOwn(view) {
        if (!own[view] || generator.state().running) return;
        dropEdit(view); releaseOwn(view); generator.setOwn(make.indexOf(view), ''); render();
    }
    // Before a generation uses them as references, the pictures the user put in are uploaded to the engine (once each,
    // and let go with the other uploads when the dialog is left); a set made only of them never is.
    async function ensureOwn() {
        for (const view of SHOW) {
            const o = own[view]; if (!o || o.ref) continue;
            const ref = await referenceOf({file: o.file});
            if (disposed || own[view] !== o) continue;
            o.ref = ref; generator.setOwn(make.indexOf(view), ref);
        }
    }
    async function redraw(view) {
        if (own[view]) return;
        try { await ensureOwn(); } catch (error) { attention = error.message || tr('Could not read this picture. Please choose another one.', '无法读取这张图片，请换一张。'); render(); return; }
        dropEdit(view);
        await generator.redo(make.indexOf(view));
        if (view === base && generator.state().pictures[0].file) { faceChanged = true; render(); }
    }
    // A view opens in the image editor; the edited picture takes its place on the card (marked, and Restore puts the
    // made one back). An edited face or front is what the others are redrawn from.
    async function editView(view) {
        let outcome, loadFailed = false;
        try {
            const {openImageEditor} = await import('./image_editor.js').catch(error => { loadFailed = true; throw error; });
            outcome = await openImageEditor({source: sourceOf(view), name: `${view}.png`, edit: edits[view]?.edit || null,
                role: 'reference', t: tr, resolve: url => url, upload: async blob => client.upload(blob), imageGenClient: client});
        } catch { attention = loadFailed ? tr('The image editor did not load. Reload the page and try again.', '图片编辑器没有加载出来。请刷新页面后再试。') : tr('This picture could not be read. Choose another picture.', '这张图无法读取。请重新选择一张。'); render(); return; }
        if (!outcome || disposed) return;
        if (outcome.restore) { dropEdit(view); if (leads(view)) faceChanged = true; render(); return; }
        if (!outcome.blob) return;
        dropEdit(view);
        const file = new File([outcome.blob], outcome.name || `${view}-edited.png`, {type: outcome.blob.type || 'image/png'});
        edits[view] = {file, url: URL.createObjectURL(file), edit: outcome.edit || null};
        if (leads(view)) faceChanged = true;
        render();
    }
    body.append(side, grid); root.append(body, ownInput); container.append(root);

    // The main action lives in the dialog's own button row (the host places it): Generate, Stop, then Generate all again.
    const action = el('button', 'fv-cp-action'); action.type = 'button';

    let photoRef = '', outfitRef = '', mode = ai ? 'text' : 'keep', info = null, infoTimer = null, disposed = false, failureKey = null, attention = '', last = '';
    // references: what this dialog uploaded to the image engine (name → used by a generation).
    const made = new Set(), saved = new Set(), references = new Map();
    let cardSaved = false;
    // Views are works of the library (the "cast-draft" purpose names their source); an engine that does not list it files them as plain works.
    let drafts = false;
    // The engine's copies let go: uploads, in one discard-uploads call (keepalive, so a closing tab still sends it).
    // Silent where the engine has no such call.
    const discardNames = names => { if (names.length) Promise.resolve().then(() => keeper.discardUploads(names)).catch(() => {}); };
    // A view drawn again stays in the library like every other picture.
    const discard = () => {};
    // The engine's copies of the uploaded pictures go too (discard-uploads; the engine keeps any a task still uses).
    // Closed without saving: the ones no generation used. Saved: all of them. Silent where the engine has no such call yet.
    const releaseReferences = (all = false) => {
        const names = [...references].filter(([, used]) => all || !used).map(([name]) => name);
        for (const name of names) references.delete(name);
        discardNames(names);
    };
    // Views as drafts when the engine offers them: the same generate call with the purpose.
    const draftClient = photoClient(client, references, () => drafts ? 'cast-draft' : '');
    const otherUsage = createOtherUsageNotice(side, tr, fetcher);
    // New clothes: the made close-up's collar and shoulders are repainted under a mask drawn from its face box.
    // The masks drawn here (uploaded name → file), so a repaint can be checked against its own mask.
    const masks = new Map();
    const dressMask = async file => { const mask = await dressMaskFile({client, fetcher, name: file, url: viewURL(file)}); if (!mask) return ''; const name = await referenceOf({file: mask}); masks.set(name, mask); return name; };
    const dressTook = (before, after, mask) => masks.has(mask) ? repaintTook({before: viewURL(before), after: viewURL(after), mask: masks.get(mask)}) : true;
    // A back that came out facing the camera (face-box finds a face looking at it) is drawn once more.
    const facing = file => facesCamera({client, fetcher, name: file});
    // A close-up framed too wide is drawn once more; a repaint that kept the old clothes is retried from the close-up with its clothes greyed out.
    const framing = file => closeUpShare({client, fetcher, name: file});
    const dressNeutral = async file => { const grey = await neutralCloseUp({client, fetcher, name: file, url: viewURL(file)}); return grey ? referenceOf({file: grey}) : ''; };
    const generator = createCastGenerator({client: draftClient, kind: 'character', views: make, dressMask, dressTook, dressNeutral, facing, framing, framingMin: CLOSEUP_FRAME.min, beforeGenerate: query => otherUsage.check(query), onDiscard: discard,
        onChange: state => {
            render(state);
        }});

    function setWear(value) { mode = value; render(); if (value === 'text' && field) field.focus(); }
    function missing() { return info && !info.installed && !info.ready; }
    const pictureOf = view => generator.state().pictures[make.indexOf(view)];
    function render(state = generator.state()) {
        const running = state.running, isMissing = missing(), finished = generator.done() && !running;
        root.dataset.state = isMissing ? 'missing' : running ? 'running' : finished ? 'done' : state.error ? 'failed' : 'idle';
        photoSlot?.disable(running); outfitSlot.disable(running); for (const b of modeButtons) { b.disabled = running; b.setAttribute('aria-checked', String(b.dataset.value === mode)); }
        if (describe) describe.readOnly = running;
        if (picker) picker.element.inert = running;
        outfitSlot.element.hidden = mode !== 'image';
        if (field) {
            field.hidden = mode === 'keep'; field.readOnly = running;
            field.placeholder = mode === 'image' ? tr('Details to add, if any (optional)', '可补充细节（选填）') : tr('Describe the clothes, e.g. a navy wool coat over a white shirt', '描述服装，例如：白衬衫外穿藏青色羊毛大衣');
        }
        // Shown or hidden by the dialog (only on this tab); missing image models hide it from here.
        action.dataset.missing = String(!!isMissing);
        // Pictures of the user's own stay; Generate makes the other views, and with all four in there is nothing to make.
        const ownCount = SHOW.filter(view => own[view]).length;
        action.textContent = running ? tr('Stop', '停止') : finished ? (ownCount ? tr('Generate the rest again', '重新生成其余视角') : tr('Generate all again', '重新生成全部'))
            : ownCount ? tr('Generate the rest', '生成其余视角') : tr('Generate', '生成');
        action.classList.toggle('fv-cp-none', ownCount === SHOW.length);
        action.dataset.primary = String(!running && !finished);
        const why = ai ? (!describe.value.trim() ? tr('Describe the character first', '先描述角色') : mode === 'image' && !outfitSlot.get() ? tr('Add a clothes picture first', '先添加服装图') : '')
            : !photoSlot.get() && !own.front && !own.face ? (ownCount ? tr('Add a source picture, or put in the front or face close-up.', '请添加来源图，或放入正面或脸部特写。') : words.first) : mode === 'image' && !outfitSlot.get() ? tr('Add a clothes picture first', '先添加服装图')
            : mode === 'text' && !field.value.trim() ? tr('Describe the clothes first', '先描述服装') : '';
        if (why && !running) { action.setAttribute('aria-disabled', 'true'); action.dataset.fvTipWhy = why; } else { action.removeAttribute('aria-disabled'); delete action.dataset.fvTipWhy; }
        tip(action, action.textContent, running ? tr('Stop making the views', '停止生成') : words.use);
        for (const view of SHOW) {
            const c = cells[view], r = pictureOf(view) || {}, index = make.indexOf(view), active = running && index === state.current && !r.file;
            const src = r.file ? edits[view]?.url || sourceOf(view) : '';
            if (c.image.getAttribute('src') !== src) src ? c.image.src = src : c.image.removeAttribute('src');
            c.cell.dataset.state = r.file ? 'done' : active ? 'running' : 'empty'; c.cell.dataset.original = String(!!r.original && !own[view]); c.cell.dataset.own = String(!!own[view]);
            c.cell.setAttribute('aria-label', c.title + (active ? ' ' + Math.floor(r.fraction * 100) + '%' : ''));
            c.percent.textContent = active && !state.waiting ? Math.floor(r.fraction * 100) + '%' : '';
            c.fill.style.width = (active ? r.fraction * 100 : 0) + '%';
            c.edited.hidden = !edits[view]; c.restore.hidden = !edits[view]; c.again.hidden = !!own[view] || (!r.file && !(state.error && !running));
            c.again.disabled = running || (index > 0 && !state.pictures[0].file);
            if (c.usePhoto) c.usePhoto.hidden = !photoSlot.get() || !!r.original || running;
            if (c.mine) {
                tip(c.cell, c.title, r.file ? tr('Click to edit it in the image editor, or drop a picture to replace it', '点击在图像编辑器中修改，或拖入图片替换') : tr('Click or drop a picture', '点击或拖入图片'));
                const name = r.file ? tr('Replace', '更换') : tr('Add picture', '添加图片');
                if (c.mine.textContent !== name) { c.mine.textContent = name; tip(c.mine, name, r.file ? tr('Use another picture of your own for this view', '这个视角换成自己的另一张图片') : tr('Use a picture of your own for this view', '这个视角用自己的图片')); }
                c.mine.hidden = running; c.clear.hidden = running || !own[view];
            }
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
        rest.hidden = !faceChanged || running || !state.pictures.slice(1).some(row => row.file && !row.own); rest.disabled = running;
        const count = state.pictures.filter(r => r.file).length, fraction = running && state.current >= 0 ? state.pictures[state.current]?.fraction || 0 : 0;
        const progress = generator.done() ? 1 : (count + fraction) / make.length;
        const summary = JSON.stringify([running, finished, Math.round(progress * 100)]);
        if (summary !== last) { last = summary; onState({busy: running, progress, done: finished}); }
    }
    field?.addEventListener('input', () => { if (attention) attention = ''; render(); });
    async function referenceOf(record) {
        return draftClient.upload(record.file);
    }
    // A small face in the photo: the close-up is made from it cut out and enlarged (cast_face_crop.js), uploaded once per
    // photo; '' keeps the whole photo.
    let closeUp = {photo: '', name: ''};
    async function closeUpOf(photo, file) {
        if (closeUp.photo !== photo) {
            const made = await faceCloseUp({client, fetcher, name: photo, file});
            closeUp = {photo, name: made && !disposed ? await referenceOf(made).catch(() => '') : ''};
        }
        return closeUp.name;
    }
    async function begin() {
        if (action.getAttribute('aria-disabled') === 'true') { attention = action.dataset.fvTipWhy || ''; render(); return; }
        attention = ''; render();
        let sourceRef = '';
        try {
            await ensureOwn();
            if (!ai) {
                // The picture the views are made from: the source picture, else the user's own front (or face close-up).
                const from = photoSlot.get() ? null : own.front || own.face;
                if (from) sourceRef = from.ref;
                else { if (!photoRef) photoRef = await referenceOf(photoSlot.get()); sourceRef = photoRef; }
                if (!own.face) await closeUpOf(sourceRef, (from || photoSlot.get()).file);
            }
            if (mode === 'image' && !outfitRef) outfitRef = await referenceOf(outfitSlot.get());
        } catch (error) { attention = error.message || tr('Could not read this picture. Please choose another one.', '无法读取这张图片，请换一张。'); render(); return; }
        if (disposed) return;
        for (const name of [sourceRef, own.face ? '' : closeUp.name, mode === 'image' ? outfitRef : '', ...SHOW.map(view => own[view]?.ref)]) if (name && references.has(name)) references.set(name, true);
        const outfitImage = mode === 'image' ? outfitRef : '';
        if (ai) {
            const selected = picker ? picker.value() : [];
            void generator.start(describe.value, {source, outfitImage, ...(selected.length ? {loras: selected} : {})});
        } else void generator.start('', {source, photo: sourceRef, closeUp: own.face ? '' : closeUp.name, outfit: mode === 'keep' ? '' : field.value, outfitImage});
    }
    action.onclick = () => generator.state().running ? void generator.stop() : void begin();
    async function checkInfo() {
        try { info = await client.info(); } catch { info = null; }
        drafts = Array.isArray(info?.purposes) && info.purposes.includes('cast-draft');
        if (disposed) return; render(); clearTimeout(infoTimer);
        if (missing()) infoTimer = setTimeout(checkInfo, IMAGE_GEN_INFO_POLL_MS);
    }
    // The tab closing or the page going away: the uploads go; the views stay in the library.
    const leaving = () => {
        releaseReferences(cardSaved);
    };
    window.addEventListener('pagehide', leaving);

    async function picture(src) { const blob = await (await fetch(src)).blob(); return createImageBitmap(blob); }
    const shownFile = view => { const r = pictureOf(view); return edits[view]?.file || own[view]?.file || (r.original ? photoSlot.get().file : r.file); };
    async function sheet() {
        const {body: [bw, bh], face: [fw], gap, ground} = SHEET;
        const width = fw + 3 * bw + 5 * gap, height = bh + 2 * gap;
        const canvas = new OffscreenCanvas(width, height), g = canvas.getContext('2d');
        g.fillStyle = ground; g.fillRect(0, 0, width, height);
        let x = gap;
        for (const view of SHOW) {
            const r = pictureOf(view), cw = view === 'face' ? fw : bw;
            const image = await picture(edits[view]?.url || own[view]?.url || (r.original ? photoSlot.get().url : viewURL(r.file)));
            const k = Math.min(cw / image.width, bh / image.height), w = Math.round(image.width * k), h = Math.round(image.height * k);
            g.drawImage(image, x + Math.round((cw - w) / 2), gap + Math.round((bh - h) / 2), w, h); image.close?.();
            x += cw + gap;
        }
        const blob = await canvas.convertToBlob({type: 'image/png'});
        return new File([blob], 'character-sheet.png', {type: 'image/png'});
    }

    setWear(mode); render(); void checkInfo();
    return {
        action,
        // The sheet, the four views, and the source picture unless it already is one of them (the card keeps what it was
        // made from). From a description there is no source picture.
        async getPictures() {
            const state = generator.state();
            if (!generator.done() || state.running) return [];
            const views = SHOW.map(view => ({file: shownFile(view), label: view}));
            const extra = photoSlot?.get() && !SHOW.some(view => pictureOf(view)?.original && !own[view]) ? [{file: photoSlot.get().file, label: 'other'}] : [];
            return [{file: await sheet(), label: 'sheet'}, ...views, ...extra];
        },
        // The card keeps its own copy of the views it shows; the views stay in the library.
        markSaved() { cardSaved = true; generator.state().pictures.forEach((r, at) => { if (r.file && !edits[make[at]] && !own[make[at]]) saved.add(r.file); }); },
        isBusy: () => generator.state().running,
        cancel: () => generator.stop(),
        destroy() {
            disposed = true; clearTimeout(infoTimer); otherUsage.dispose(); picker?.dispose(); window.removeEventListener('pagehide', leaving);
            // Cancel, close, Esc: the uploads go; the views stay in the library.
            leaving();
            for (const view of Object.keys(edits)) dropEdit(view);
            for (const view of Object.keys(own)) releaseOwn(view);
            void generator.dispose(); photoSlot?.dispose(); outfitSlot.dispose(); action.remove(); root.remove();
        },
        element: root,
    };
}
