// The picture page, beside the studio, the editing view and the Agent: one input, one Generate button,
// either image model, and the works made here. The wire adapter, failure card, history and reference
// rules are the editor's own (image_generation.js); this page only lays them out.
import { api } from '../../scripts/api.js';
import { closeDialog } from './motion.js';
import { openEditorView } from './editor_loader.js';
import { openSetup } from './setup.js';
import { feedbackButton, guideButton } from './feedback.js';
import { createPromptEnhancer } from './prompt_enhance.js';
import { issueButton } from './issue_button.js';
import { openStudio, showNodes } from './studio.js';
import { openAutopilot, viewChanged, viewSwitch } from './view_navigation.js';
import { segmented } from './editor_adjust.js';
import { menuSelect } from './fv_select.js';
import { wordmark } from './wordmark.js';
import { openReferenceMenu, removeSavedImages, saveGeneratedImage } from './image_library.js';
import { BLANK_CANVAS_AREAS, createImageGenClient, imageGenError, imageGenProgress, renderImageFailure } from './image_generation.js';

const languageOverride = typeof location !== 'undefined' ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en' && String(navigator.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
for (const name of ['./editor_adjust.css', './image_view.css']) {
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL(name, import.meta.url).href; document.head.append(css);
}

export const IMAGE_MODELS = Object.freeze([
    {id: 'minimax-h3-image', name: 'MiniMax H3 Image', short: 'H3 Image', references: 0},
    {id: 'qwen-image-2.1-turbo', name: 'Qwen-Image 2.1', short: 'Qwen-Image 2.1', references: 3},
]);
export const RATIOS = Object.freeze(['1:1', '4:3', '3:4', '16:9', '9:16', '3:2', '2:3']);
const MODEL_KEY = 'freevideo.imageModel';
const remembered = () => { try { return localStorage.getItem(MODEL_KEY); } catch { return null; } };
const remember = id => { try { localStorage.setItem(MODEL_KEY, id); } catch { /* the choice is only not kept */ } };

// H3 Image is made for 4 to 16 MP; a smaller picture would still cost a 4 MP canvas, so its tiers start there.
const H3_AREAS = Object.freeze({standard: 2048, high: 4096});
// A size with the ratio at about the tier's area, in steps of 32.
export function imageSize(ratio, tier = 'standard', same = null, model = '') {
    const [w, h] = (ratio === 'same' && same ? [same.width, same.height] : ratio.split(':').map(Number));
    const areas = model === 'minimax-h3-image' ? H3_AREAS : BLANK_CANVAS_AREAS;
    const side = areas[tier] || areas.standard;
    const scale = Math.sqrt(side * side / (w * h));
    const round = value => Math.max(32, Math.round(value * scale / 32) * 32);
    return {width: round(w), height: round(h)};
}

// Stands in for the engine until it answers (tests and the first skeleton): a flat picture after a short wait.
export function createFakeImageClient({delay = 600, ready = {'minimax-h3-image': true, 'qwen-image-2.1-turbo': true}, fail = null} = {}) {
    const items = []; let count = 0; const jobs = new Map(), state = {...ready}; const loading = new Map();
    const models = () => IMAGE_MODELS.map(m => ({id: m.id, label: m.name, ready: !!state[m.id], downloading: loading.has(m.id),
        bytes_total: 12.5e9, bytes_done: loading.has(m.id) ? Math.min(12.5e9, (Date.now() - loading.get(m.id)) * 5e6) : 0}));
    return {
        info: async () => { for (const [id, at] of loading) if (Date.now() - at > 12.5e9 / 5e6) { state[id] = true; loading.delete(id); } return {installed: true, ready: true, max_images: 16, models: models()}; },
        packInstall: async id => { loading.set(id, Date.now()); return {installed: true, models: models()}; },
        packCancel: async id => { loading.delete(id); return {installed: true, models: models()}; },
        async generate({prompt, width, height}) {
            if (fail) throw Object.assign(new Error(fail), {code: fail});
            const job = 'fake-' + ++count; jobs.set(job, {at: Date.now(), prompt, width, height}); return {job};
        },
        async status(job) {
            const row = jobs.get(job), fraction = Math.min(1, (Date.now() - row.at) / delay);
            if (fraction < 1) return {state: 'running', stage: fraction < .3 ? 'load' : 'sample', fraction};
            const file = `freevideo-image/fake-${job}.png`; if (!items.some(i => i.file === file)) items.unshift({file, name: file.slice(15)});
            return {state: 'done', file, fraction: 1};
        },
        cancel: async job => { jobs.delete(job); },
        history: async () => items.slice(),
        historyDelete: async file => { const at = items.findIndex(i => i.file === file); if (at >= 0) items.splice(at, 1); return items.slice(); },
        async historyDeleteMany(files) { for (const f of files) await this.historyDelete(f); return {items: items.slice(), failed: []}; },
        historyOpen: async () => ({}),
        upload: async file => file.name,
    };
}

const pictureURL = file => { const at = file.lastIndexOf('/'); return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(at + 1), subfolder: at < 0 ? '' : file.slice(0, at), type: 'output'})); };
const SAMPLE_PROMPTS = {
    "jazz": "A stylish concert poster in a mid-century modern graphic style: a saxophone player drawn as a bold silhouette in deep navy against a warm mustard-yellow background, with abstract curved shapes in teal and coral. At the top, a large clean title reads \"MIDNIGHT JAZZ\". At the bottom, one smaller line reads \"FRIDAY 9 PM\". All text is spelled exactly as given, with no other words on the poster. Flat print texture, strong composition.",
    "window": "A realistic portrait photograph of a woman in her late twenties sitting by a tall window in a quiet old apartment, soft overcast daylight on one side of her face, loose dark hair tucked behind one ear, wearing a rust-coloured linen shirt, holding a white ceramic cup with both hands, calm thoughtful expression looking slightly away from the camera, natural skin texture with fine detail, muted warm colours, shallow depth of field, 85mm lens, one person, no text.",
    "perfume": "A luxury studio product photograph of an unlabelled faceted glass perfume bottle filled with pale amber liquid and a brushed gold cap, standing on a block of white travertine stone, surrounded by a few floating soap bubbles and a sprig of white jasmine, soft beige background, warm rim light making the glass glow, sharp reflections and caustics on the stone, high-end advertising look, no logo, no text.",
    "fjord": "A wide landscape photograph of a deep fjord at golden hour, steep green cliffs dropping into still dark-blue water, a thin waterfall falling from the left cliff, low clouds catching orange light, a small red wooden cabin on a grassy ledge near the shore, mirror-like reflections on the water, crisp detail from the foreground rocks to the distant peaks, rich natural colours, no people, no text.",
    "fox": "A cute 3D clay-style illustration of a small orange fox wearing a knitted green scarf, reading a tiny book while sitting on a mossy tree stump in a miniature forest, little mushrooms and fallen autumn leaves around it, soft studio lighting, visible fingerprint texture in the clay, pastel background with gentle bokeh, playful and warm, an original character, no text."
};
const STAGE_WORDS = {load: ['Loading the model', '加载模型'], text: ['Reading the prompt', '读取提示词'], encode: ['Reading the references', '读取参考图'],
    upload: ['Reading the references', '读取参考图'], sample: ['Generating', '正在生成'], decode: ['Generating', '正在生成']};
const seconds = value => value >= 90 ? t(`about ${Math.round(value / 60)} min`, `约 ${Math.round(value / 60)} 分钟`) : t(`about ${Math.max(5, Math.round(value / 5) * 5)} s`, `约 ${Math.max(5, Math.round(value / 5) * 5)} 秒`);

let current = null;
// Opens above the studio when one is open, as the editing view does, so going back finds it as it was.
export function openImageView(node, {studio = null, client = createImageGenClient(), poll = 500} = {}) {
    if (current?.open) { viewChanged('image', node); return current; }
    studio?.querySelectorAll('video,audio').forEach(media => media.pause());
    const dialog = el('dialog', 'fv-studio fv-image'); dialog.lang = cn ? 'zh' : 'en'; dialog.setAttribute('aria-label', t('Images', '图像视图'));
    const header = el('header', 'fv-header'), brand = el('div', 'fv-brand'), tools = el('div', 'fv-header-actions');
    brand.append(wordmark());
    const settings = el('button', 'fv-quiet', t('Settings', '设置')); settings.type = 'button'; settings.onclick = openSetup;
    tools.append(guideButton(t), feedbackButton(t), settings);
    let next = () => studio?.open ? viewChanged('studio', node) : viewChanged('nodes', node);
    const leave = action => { next = action; closeDialog(dialog); };
    const navigation = viewSwitch('image', () => leave(() => studio?.open ? viewChanged('studio', node) : openStudio(node)),
        () => leave(() => showNodes(node, studio)), () => leave(() => openEditorView(node, {studio: studio?.open ? studio : undefined})),
        () => leave(() => openAutopilot(node, {studio: studio?.open ? studio : undefined})), () => {});
    header.append(brand, navigation, tools);

    // State of the page.
    let info = null, model = IMAGE_MODELS.find(m => m.id === remembered()) || IMAGE_MODELS[0], ratio = '1:1', tier = 'standard';
    const promptOf = {};
    let ratioChosen = false, running = null, failure = null, alive = true, shownFile = '', started = 0, selecting = false;
    let menu = null; const references = [], picked = new Set(); let rows = [];
    const entry = () => info?.models?.find(m => m.id === model.id) || null;
    const missing = () => entry() ? entry().ready === false : false;
    const loading = () => !!entry()?.downloading;

    // Left column: model, prompt, references, shape, clarity, Generate.
    const body = el('div', 'fv-image-body'), left = el('section', 'fv-image-left'), stage = el('section', 'fv-image-stage'), column = el('aside', 'fv-image-works-column');
    // Two models are alternatives, not a pair: the choice appears only once both are installed, as one small menu in the input area.
    const modelSelect = el('select'); modelSelect.setAttribute('aria-label', t('Model', '模型'));
    modelSelect.append(...IMAGE_MODELS.map(m => Object.assign(el('option', '', m.name), {value: m.id})));
    const modelMenu = menuSelect(modelSelect), modelPick = modelMenu.wrap; modelPick.classList.add('fv-image-models'); modelPick.hidden = true;
    modelSelect.addEventListener('input', () => { model = IMAGE_MODELS.find(m => m.id === modelSelect.value); remember(model.id); render(); });
    const inputs = el('div', 'fv-image-inputs');
    const prompt = el('textarea', 'fv-image-prompt'); prompt.rows = 4;
    const strip = el('div', 'fv-image-references'), notice = el('p', 'fv-image-notice'); notice.setAttribute('role', 'status');
    const pick = el('input'); pick.type = 'file'; pick.accept = 'image/*'; pick.multiple = true; pick.hidden = true;
    const ratioSelect = el('select'); ratioSelect.setAttribute('aria-label', t('Shape', '比例'));
    const ratioMenu = menuSelect(ratioSelect), ratioHost = ratioMenu.wrap;
    const tierPick = segmented([{value: 'standard', label: t('Standard', '标准')}, {value: 'high', label: t('High', '高清')}], {value: tier, onChange: v => { tier = v; tierPick.value = v; render(); }});
    tierPick.setAttribute('aria-label', t('Clarity', '清晰度'));
    const shape = el('div', 'fv-image-shape'); const addModel = el('button', 'fv-image-add-model', t('Add MiniMax H3 Image', '添加 MiniMax H3 Image')); addModel.type = 'button'; addModel.hidden = true;
    addModel.onclick = () => { model = IMAGE_MODELS[0]; render(); void install(); };
    const go = el('button', 'fv-primary fv-image-go'); go.type = 'button';
    const note = el('p', 'fv-image-note'); note.setAttribute('role', 'status');
    shape.append(modelPick, ratioHost, tierPick, addModel);
    // FreeToken rewrites the text for H3 only, and only once its model is installed; nothing is offered to download here.
    // It keeps its own record, apart from the video's on the generate node.
    const enhancer = createPromptEnhancer({node: {id: 'image-view', properties: {}}, input: prompt, api, t, context: () => ({target: 'image'}),
        setText: () => render(), issueButton});
    let enhancerReady = false;
    const enhancerOff = () => !enhancerReady || model.id !== 'minimax-h3-image';
    enhancer.element.classList.add('fv-image-enhance');
    enhancer.model.read().then(info => { enhancerReady = info?.ready === true; render(); }).catch(() => {});
    inputs.append(enhancer.element, strip, notice, pick, shape, go, note);
    left.append(inputs);

    // Stage: the work, or the state that stands in for it; below it the actions and one line of facts.
    const canvas = el('div', 'fv-image-canvas'), picture = el('img'), samples = el('div', 'fv-image-card'), block = el('div', 'fv-image-block'), failHost = el('div', 'fv-image-fail');
    picture.alt = ''; const progressText = el('p', 'fv-image-progress'); progressText.setAttribute('role', 'status');
    block.append(el('i'), progressText);
    const grid = el('div', 'fv-image-samples');
    for (const key of cn ? ['fox', 'window', 'perfume', 'fjord'] : ['jazz', 'window', 'perfume', 'fjord']) {
        const b = el('button'), i = el('img'); b.type = 'button'; i.alt = ''; i.src = new URL(`./assets/tour/sample-${key}.webp`, import.meta.url).href;
        if (key === 'jazz') i.style.objectPosition = '50% 0';
        b.setAttribute('aria-label', SAMPLE_PROMPTS[key].slice(0, 60)); b.append(i);
        b.onclick = () => { prompt.value = SAMPLE_PROMPTS[key]; prompt.dispatchEvent(new Event('input', {bubbles: true})); prompt.focus(); };
        grid.append(b);
    }
    canvas.append(picture, samples, grid, block, failHost);
    const actions = el('div', 'fv-image-actions'), info_ = el('p', 'fv-image-info');
    const mk = (text, onClick) => { const b = el('button', 'fv-image-action', text); b.type = 'button'; b.onclick = onClick; return b; };
    const editThis = mk(t('Edit this', '改这张'), () => void useAsReference(shownFile, true));
    const asReference = mk(t('Use as reference', '用作参考图'), () => void useAsReference(shownFile, false));
    const save = el('a', 'fv-image-action', t('Download', '下载')); save.setAttribute('download', '');
    const moreButton = mk('⋯', () => { more.hidden = !more.hidden; }); moreButton.setAttribute('aria-label', t('More', '更多')); moreButton.setAttribute('aria-haspopup', 'true');
    const more = el('div', 'fv-image-more'); more.hidden = true;
    const folder = mk(t('Open folder', '打开文件夹'), () => { more.hidden = true; void client.historyOpen?.(); });
    const remove = mk(t('Delete', '删除'), () => { more.hidden = true; void deleteFiles([shownFile]); });
    const copyPrompt = mk(t('Copy prompt', '复制提示词'), () => { more.hidden = true; const text = promptOf[shownFile] ?? rows.find(r => r.file === shownFile)?.prompt ?? ''; try { void navigator.clipboard?.writeText(text); } catch { /* the clipboard may be refused */ } });
    save.addEventListener('click', () => { more.hidden = true; });
    more.append(save, folder, copyPrompt, remove);
    const moreHost = el('span', 'fv-image-more-host'); moreHost.append(moreButton, more);
    actions.append(editThis, asReference, moreHost);
    stage.append(canvas, actions, info_);

    // Works: newest first, two columns; "Select" lets several go at once.
    const works = el('div', 'fv-image-works'); works.setAttribute('aria-label', t('Works', '作品'));
    const selectBar = el('div', 'fv-image-select'), selectToggle = mk(t('Select', '选择'), () => { selecting = !selecting; picked.clear(); drawWorks(); });
    const deleteSelected = mk('', () => void deleteFiles([...picked])); deleteSelected.hidden = true;
    selectBar.append(selectToggle, deleteSelected);
    column.append(works, selectBar);
    const main = el('div', 'fv-image-main'); main.append(stage, column);
    body.append(left, main); dialog.append(header, body);
    inputs.addEventListener('dragover', event => { if (event.dataTransfer?.types?.includes('Files')) event.preventDefault(); });
    inputs.addEventListener('drop', event => { if (event.dataTransfer?.files?.length) { event.preventDefault(); addFiles([...event.dataTransfer.files]); } });
    prompt.addEventListener('paste', event => { const files = [...(event.clipboardData?.files || [])].filter(f => f.type.startsWith('image/')); if (files.length) { event.preventDefault(); addFiles(files); } });

    const say = text => { note.textContent = text || ''; };
    const label = number => t(`Image ${number}`, `图 ${number}`);
    const over = () => Math.max(0, references.length - model.references);
    function addFiles(files, card) {
        if (model.references === 0) return;
        const room = Math.max(0, 16 - references.length); let skipped = 0;
        for (const file of files) {
            if (!file.type.startsWith('image/')) continue;
            if (references.length >= 16) { skipped++; continue; }
            const record = {file, url: URL.createObjectURL(file), card}; references.push(record);
            const probe = new Image(); probe.onload = () => { record.width = probe.naturalWidth; record.height = probe.naturalHeight; if (alive) render(); }; probe.src = record.url;
        }
        notice.textContent = skipped ? t(`At most 16, ${skipped} more were not added.`, `最多 16 张，多出的 ${skipped} 张没有加入。`) : '';
        void room; render();
    }
    async function useAsReference(file, first) {
        if (!file) return;
        try {
            const blob = await (await fetch(pictureURL(file))).blob(), name = file.slice(file.lastIndexOf('/') + 1);
            const record = {file: new File([blob], name, {type: blob.type || 'image/png'}), url: URL.createObjectURL(blob)};
            if (references.length >= 16) { URL.revokeObjectURL(record.url); return; }
            first ? references.unshift(record) : references.push(record);
            const probe = new Image(); probe.onload = () => { record.width = probe.naturalWidth; record.height = probe.naturalHeight; if (alive) render(); }; probe.src = record.url;
            render(); prompt.focus();
        } catch { say(t('That picture could not be read.', '这张图读不了。')); }
    }
    function insertTag(number) {
        const at = prompt.selectionStart ?? prompt.value.length, tag = (at && !/\s/.test(prompt.value[at - 1]) ? ' ' : '') + label(number) + ' ';
        prompt.setRangeText(tag, at, prompt.selectionEnd ?? at, 'end'); prompt.focus(); render();
    }
    // The engine reads <Picture N>; the page says "图 N" / "Image N". A prompt that names none gets them listed.
    function engineText() {
        const swapped = prompt.value.replace(/(?:图\s?|Image\s)(\d+)/g, (_, n) => `<Picture ${n}>`);
        return references.length && !/<Picture \d+>/.test(swapped) ? references.map((_, at) => `<Picture ${at + 1}>`).join(' ') + ' ' + swapped : swapped;
    }

    function drawReferences() {
        strip.replaceChildren(...references.map((r, at) => {
            const cell = el('span', 'fv-image-reference'), use = el('button', 'fv-image-reference-use'), x = el('button', 'fv-image-reference-remove', '×');
            cell.dataset.over = String(at >= model.references);
            use.type = x.type = 'button'; use.title = t(`Refer to ${label(at + 1)} in the prompt`, `在描述里引用${label(at + 1)}`); use.setAttribute('aria-label', use.title);
            x.title = t('Remove', '移除'); x.setAttribute('aria-label', x.title);
            const i = el('img'); i.src = r.url; i.alt = ''; use.append(i, el('b', '', label(at + 1)));
            use.onclick = () => insertTag(at + 1);
            x.onclick = () => { URL.revokeObjectURL(r.url); references.splice(references.indexOf(r), 1); render(); };
            cell.append(use, x); return cell;
        }));
        if (model.references > 0 && references.length < 16) {
            const add = el('button', 'fv-image-add', '+'); add.type = 'button'; add.setAttribute('aria-label', t('Add a reference picture', '添加参考图')); add.onclick = () => { if (menu?.box.isConnected) { menu.close(); return; } menu = openReferenceMenu({api, viewURL: pictureURL, anchor: () => dialog.querySelector('.fv-image-add') || add, host: dialog, onComputer: () => pick.click(),
                onPick: (file, id) => { addFiles([file], id); }, state: () => ({used: new Set(references.map(r => r.card)), full: references.length >= 16})}); };
            if (references.length < model.references || over() === 0) strip.append(add);
        }
    }
    pick.onchange = () => { addFiles([...pick.files]); pick.value = ''; };

    function drawRatios() {
        const wanted = references.length && !ratioChosen ? 'same' : ratio;
        const options = [...(references.length ? [['same', t('Same as Image 1', '与图 1 相同')]] : []), ...RATIOS.map(r => [r, r])];
        if (ratioSelect.dataset.key !== options.map(o => o[0]).join()) {
            ratioSelect.replaceChildren(...options.map(([v, text]) => { const o = el('option', '', text); o.value = v; return o; }));
            ratioSelect.dataset.key = options.map(o => o[0]).join();
        }
        ratioSelect.value = options.some(o => o[0] === wanted) ? wanted : '1:1';
        ratioMenu.refresh();
    }
    ratioSelect.addEventListener('input', () => { ratio = ratioSelect.value; ratioChosen = true; });

    function size() {
        const first = references[0];
        const same = (!ratioChosen && references.length) || ratioSelect.value === 'same';
        return imageSize(same && first?.width ? 'same' : (ratioSelect.value === 'same' ? '1:1' : ratioSelect.value || ratio), tier, same && first?.width ? first : null, model.id);
    }

    function render() {
        enhancer.element.dataset.off = String(enhancerOff());
        const both = info?.models?.length > 1 && info.models.every(m => m.ready !== false);
        const only = info?.models?.find(m => m.id === IMAGE_MODELS[1].id)?.ready && info.models.find(m => m.id === IMAGE_MODELS[0].id)?.ready === false;
        addModel.hidden = !only || model.id !== IMAGE_MODELS[1].id; modelPick.hidden = !both; if (modelSelect.value !== model.id) { modelSelect.value = model.id; modelMenu.refresh(); }
        drawReferences();
        if (alive) drawRatios();
        const tooMany = over() > 0, busy = !!running, canUse = !missing();
        prompt.placeholder = references.length ? t('What to make or change', '要画什么或要改什么') : t('Describe the picture', '描述画面');
        prompt.setAttribute('aria-label', prompt.placeholder);
        notice.textContent = tooMany ? (model.references === 0 ? t(`${model.name} does not take reference pictures. Remove ${over()}.`, `${model.name} 暂不支持参考图，请移除 ${over()} 张。`) : t(`${model.name} takes at most ${model.references}. Remove ${over()}.`, `${model.name} 最多 ${model.references} 张，请移除 ${over()} 张。`)) : notice.dataset.keep === 'true' ? notice.textContent : '';
        go.className = 'fv-image-go ' + (failure ? 'fv-image-go-quiet' : 'fv-primary');
        go.disabled = false;
        if (busy) go.textContent = t('Stop', '停止');
        else if (loading()) go.textContent = t('Pause', '暂停');
        else if (missing()) go.textContent = t('Download model', '下载模型');
        else { go.textContent = t('Generate', '生成'); go.disabled = tooMany; }
        go.removeAttribute('title');
        drawStage();
    }

    function drawStage() {
        const row = entry(), busy = !!running;
        const state = busy ? 'running' : failure ? 'failed' : missing() || loading() ? 'missing' : shownFile ? 'done' : 'blank';
        canvas.dataset.state = stage.dataset.state = state;
        picture.hidden = state !== 'done'; block.hidden = state !== 'running'; failHost.hidden = state !== 'failed'; samples.hidden = state !== 'missing'; grid.hidden = state !== 'blank';
        actions.hidden = info_.hidden = state !== 'done';
        if (state === 'missing') {
            const total = row?.bytes_total || 0, done = row?.bytes_done || 0, gb = n => (n / 1e9).toFixed(1);
            samples.replaceChildren(el('b', '', model.name), loading()
                ? el('p', '', t(`Downloading · ${gb(done)} GB / ${gb(total)} GB`, `正在下载 · ${gb(done)} GB / ${gb(total)} GB`))
                : el('p', '', total ? t(`About ${gb(total)} GB to download. Files shared with video are not downloaded again.`, `下载约 ${gb(total)} GB。和视频共用的文件不重复下载。`) : t('Not installed yet.', '尚未安装。')));
            if (loading()) { const bar = el('i', 'fv-image-download'); bar.style.setProperty('--p', String(total ? Math.min(100, done / total * 100) : 0)); samples.append(bar); }
        }
        if (state === 'running') { const size_ = size(); block.style.aspectRatio = `${size_.width} / ${size_.height}`; }
        if (state === 'done') {
            picture.src = pictureURL(shownFile); save.href = pictureURL(shownFile); save.setAttribute('download', shownFile.slice(shownFile.lastIndexOf('/') + 1));
            editThis.disabled = asReference.disabled = references.length >= 16 || model.references === 0;
            info_.textContent = [model.short, picture.naturalWidth ? `${picture.naturalWidth}×${picture.naturalHeight}` : '', started ? t(`${started} s`, `${started} 秒`) : ''].filter(Boolean).join(' · ');
        }
    }
    picture.onload = () => { if (!alive) return; const tail = info_.textContent.split(' · ').filter(Boolean); info_.textContent = [model.short, `${picture.naturalWidth}×${picture.naturalHeight}`, started ? t(`${started} s`, `${started} 秒`) : ''].filter(Boolean).join(' · '); void tail; };

    function drawWorks() {
        column.hidden = rows.length === 0 && !running; body.classList.toggle('fv-image-no-works', column.hidden);
        works.replaceChildren(...(running ? [Object.assign(el('div', 'fv-image-work fv-image-work-running'), {})] : []),
            ...rows.map(row => {
                const b = el('button', 'fv-image-work'); b.type = 'button'; const i = el('img'); i.loading = 'lazy'; i.alt = ''; i.src = pictureURL(row.file);
                b.setAttribute('aria-pressed', String(selecting ? picked.has(row.file) : row.file === shownFile));
                b.append(i); b.onclick = () => { if (selecting) { picked.has(row.file) ? picked.delete(row.file) : picked.add(row.file); drawWorks(); } else { shownFile = row.file; started = 0; failure = null; render(); drawWorks(); } };
                return b;
            }));
        selectToggle.textContent = selecting ? t('Done', '完成') : t('Select', '选择'); selectToggle.hidden = rows.length === 0;
        deleteSelected.hidden = !selecting || picked.size === 0; deleteSelected.textContent = t(`Delete ${picked.size}`, `删除 ${picked.size} 张`);
    }
    async function loadWorks() { try { rows = await client.history(); if (alive) { if (!shownFile && rows[0]) shownFile = rows[0].file; drawWorks(); render(); } } catch { /* the list stays as it was */ } }
    async function deleteFiles(files) {
        const list = files.filter(Boolean); if (!list.length) return;
        try { const answer = list.length === 1 ? {items: await client.historyDelete(list[0])} : await client.historyDeleteMany(list); rows = answer.items || []; void removeSavedImages(api, list).catch(() => {});
            if (!rows.some(r => r.file === shownFile)) shownFile = rows[0]?.file || ''; picked.clear(); selecting = false; drawWorks(); render(); }
        catch { say(t('Could not delete.', '没能删除。')); }
    }

    async function install() {
        const mine = model.id;
        try { info = await client.packInstall(mine); } catch (error) { say(imageGenError(error.code, info, t)); return; }
        render();
        while (alive && entry() && !entry().ready && entry().downloading) {
            await new Promise(r => setTimeout(r, 1500)); try { info = await client.info(); } catch { /* asked again */ } render();
        }
    }
    async function generate() {
        say(''); failure = null; const mine = running = {cancelled: false}, begun = Date.now(), withImages = references.length > 0, sent = size(), tierNow = tier;
        render(); drawWorks(); progressText.textContent = '';
        try {
            const names = []; for (const r of references.slice(0, model.references)) names.push(await client.upload(r.file));
            if (mine.cancelled) return;
            if (!enhancerOff() && await enhancer.beforeGenerate() === false) { say(''); return; }
            const row = await client.generate({prompt: engineText(), ...sent, model: model.id, remember: true, images: names});
            mine.job = row.job; if (!row.job) throw Object.assign(new Error(row.error), {code: row.error, ...row});
            if (mine.cancelled) { void client.cancel(row.job); return; }
            for (;;) {
                await new Promise(done => setTimeout(done, poll)); if (!alive || mine.cancelled) return;
                const status = await client.status(row.job, model.id);
                const fraction = imageGenProgress(status.stage, status.fraction, withImages), left = fraction > .05 ? (Date.now() - begun) / 1000 * (1 - fraction) / fraction : null;
                progressText.textContent = [t(...(STAGE_WORDS[status.stage] || STAGE_WORDS.sample)), `${Math.floor(fraction * 100)}%`, left != null ? t(`${seconds(left)} left`, `剩余${seconds(left)}`) : ''].filter(Boolean).join(' · ');
                block.style.setProperty('--p', String(fraction * 100));
                if (status.state === 'done') { shownFile = status.file; promptOf[shownFile] = prompt.value; void saveGeneratedImage(api, {url: pictureURL(status.file), file: status.file, prompt: prompt.value}).catch(() => { /* the picture stays in Works */ }); started = Math.max(1, Math.round((Date.now() - begun) / 1000)); break; }
                if (status.state === 'error') throw Object.assign(new Error(status.error), {...status, code: status.error});
            }
        } catch (error) {
            if (alive && !mine.cancelled) { failure = {error: error.code || 'generation_failed', report_id: error.report_id, stage: error.stage, fitted: error.fitted, memory: error.memory, other_usage: error.other_usage, retryStandard: tierNow === 'high' && error.code === 'no_memory'};
                renderFailure(); }
        } finally { if (running === mine) { running = null; if (alive) { await loadWorks(); render(); drawWorks(); } } }
    }
    function renderFailure() {
        renderImageFailure(failHost, {...failure, info}, t);
        const retry = el('button', 'fv-primary fv-image-retry', failure.retryStandard ? t('Retry at Standard', '改用标准并重试') : t('Retry', '重试')); retry.type = 'button';
        retry.onclick = () => { if (failure.retryStandard) { tier = 'standard'; tierPick.value = 'standard'; } void generate(); };
        failHost.append(retry);
    }
    go.onclick = () => {
        if (running) { running.cancelled = true; if (running.job) void client.cancel(running.job); running = null; render(); drawWorks(); }
        else if (loading()) void client.packCancel?.(model.id).then(value => { info = value; return client.info(); }).then(value => { info = value; render(); }).catch(() => {});
        else if (missing()) void install();
        else if (!prompt.value.trim() && !references.length) prompt.focus();
        else void generate();
    };
    prompt.addEventListener('input', render);
    prompt.addEventListener('keydown', event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !go.disabled && !running && (prompt.value.trim() || references.length)) void generate(); });
    document.addEventListener('click', closeMore, true);
    function closeMore(event) { if (!more.hidden && !moreHost.contains(event.target)) more.hidden = true; }

    dialog.addEventListener('cancel', event => { event.preventDefault(); if (!more.hidden) more.hidden = true; else closeDialog(dialog); });
    dialog.onclose = () => {
        alive = false; enhancer.dispose(); document.removeEventListener('click', closeMore, true);
        if (running?.job) void client.cancel(running.job); references.forEach(r => URL.revokeObjectURL(r.url)); dialog.remove();
        if (current === dialog) current = null;
        next();
    };
    current = dialog; document.body.append(dialog); dialog.showModal(); viewChanged('image', node); render(); drawWorks();
    // The first model to offer: the last used, or H3 Image when installed, else the other.
    client.info().then(value => {
        info = value;
        // Only one installed: that one. Neither: H3 Image, in its not-installed state.
        const ready = IMAGE_MODELS.filter(m => value.models?.find(r => r.id === m.id)?.ready !== false);
        if (value.models && !ready.includes(model)) model = ready[0] || IMAGE_MODELS[0];
        if (value.models && !remembered() && ready.length === 2) model = IMAGE_MODELS[0];
        const poller = setInterval(() => { if (!alive) return clearInterval(poller); if (loading()) client.info().then(v => { info = v; render(); }).catch(() => {}); }, 2000);
        if (alive) render();
    }).catch(() => {});
    void loadWorks(); prompt.focus();
    return dialog;
}
