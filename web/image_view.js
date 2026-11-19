// The picture page, beside the studio, the editing view and the Agent: a workbench of three parts.
// Left: model, prompt, references, size, seed, prompt history and presets, and Generate under a fixed status slot.
// Centre: the picture, always centred on the window, with its actions under it; compare replaces it on demand.
// Right: the works, by day, with search, filters, sort, favourites, several at once and recently deleted.
// The wire adapter, failure card, report and reference rules are the editor's own (image_generation.js);
// a running picture belongs to the page-wide job store (task_store.js), so leaving the page never stops it.
import { api } from '../../scripts/api.js';
import { isNetworkTypeError, connectionLostText } from './browser_extension.js';
import { closeDialog } from './motion.js';
import { listenDialogKey, topDialog } from './dialog_keys.js';
import { openEditorView } from './editor_loader.js';
import { openSetup } from './setup.js';
import { feedbackButton, guideButton } from './feedback.js';
import { createPromptEnhancer } from './prompt_enhance.js';
import { createExpandButton } from './editor_expand.js';
import { issueButton } from './issue_button.js';
import { openStudio, showNodes } from './studio.js';
import { openAutopilot, viewChanged, viewSwitch } from './view_navigation.js';
import { wordmark } from './wordmark.js';
import { keepViewSwitchPlace } from './view_switch_place.js';
import { openReferenceMenu, removeSavedImages } from './image_library.js';
import { BLANK_CANVAS_AREAS, createImageGenClient, fittedNote, imageGenError, imageRemainingText, imageReportBar, renderImageFailure } from './image_generation.js';
import { duration } from './generation_progress.js';
import { randomSeed } from './studio_queue.js';
import { createPreviewScene } from './preview_scene.js?v=20261115-still';
import { jobs, ACTIVE, IMAGE_BUSY } from './task_store.js';
import { confirmStop, startJobSources } from './task_float.js';
import { el, icon, t, cn, tip, button, section, readStore, writeStore, mod } from './image_view_ui.js';
import { IMAGE_MODELS, modelPicker, modelState, verifyText, pausedText } from './image_view_models.js';
import { RANGES, RATIOS, areaRange, estimateSeconds, machineLimit, presetAt, ratioValue, sizePicker, sizeFor, sizeText, megapixels } from './image_view_size.js';
import { createMetaStore, createTrash, filterCounts, filterRows, groupRows, historyPanel, sessionPrompts } from './image_view_history.js';
import { brokenMentions, mentionText, mentionsToEngine, posePrompt, promptMentions, renumberMentions, sketchPrompt } from './image_view_mentions.js';
import { HISTORY_KEY, forgetDeletedPrompts, promptLibrary, rememberPrompt } from './image_view_prompts.js';
import { COMPARE_MAX, compareView } from './image_view_compare.js';
import { queuePanel } from './image_view_queue.js';
import { isHeld, worksClient, saveToWorks } from './image_works.js';
import { createWarmer } from './image_view_warm.js';
import { preloadMode } from './preload_setting.js';
import { openPictureViewer } from './image_view_viewer.js';
import { LORA_MODEL, imageViewLora } from './image_view_lora.js';
import { hasPictureThumbnail, mediaQueue, pictureThumbnailURL } from './work_media.js';

export { IMAGE_MODELS, RATIOS };
for (const name of ['./image_view.css']) {
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL(name, import.meta.url).href; document.head.append(css);
}
const MODEL_KEY = 'freevideo.imageModel', SIZE_KEY = 'freevideo.imageView.size', FIT_KEY = 'freevideo.imageView.fitted', PANEL_KEY = 'freevideo.imageView.panels';
const remembered = () => { try { return localStorage.getItem(MODEL_KEY); } catch { return null; } };
const remember = id => { try { localStorage.setItem(MODEL_KEY, id); } catch { /* the choice is only not kept */ } };

// H3 Image is made for 4 to 16 MP; a smaller picture would still cost a 4 MP canvas, so its tiers start there.
const H3_AREAS = Object.freeze({standard: 2048, high: 4096});
const MOSS_AREAS = Object.freeze({standard: 1024, high: 2048});
// The shape of a picture as a ratio, when it is one of the common ones; otherwise nothing is said.
const SHAPES = [[1, 1], [16, 9], [9, 16], [4, 3], [3, 4], [3, 2], [2, 3], [21, 9], [9, 21]];
export function shapeOf(width, height) {
    if (!(width > 0 && height > 0)) return '';
    const hit = SHAPES.find(([w, h]) => Math.abs(width / height - w / h) / (w / h) < .02);
    return hit ? `${hit[0]}:${hit[1]}` : '';
}
const pictureShape = shapeOf;
// A size with the ratio at about the tier's area, in steps of 32 (kept for callers of the two-tier page).
export function imageSize(ratio, tier = 'standard', same = null, model = '') {
    const [w, h] = (ratio === 'same' && same ? [same.width, same.height] : ratio.split(':').map(Number));
    const areas = model === 'minimax-h3-image' ? H3_AREAS : model === 'moss-image-sft' ? MOSS_AREAS : BLANK_CANVAS_AREAS;
    const side = areas[tier] || areas.standard;
    const scale = Math.sqrt(side * side / (w * h));
    const step = model === 'moss-image-sft' ? 16 : 32;
    const round = value => Math.max(model === 'moss-image-sft' ? 256 : 32, Math.min(model === 'moss-image-sft' ? 2048 : Infinity, Math.round(value * scale / step) * step));
    return {width: round(w), height: round(h)};
}

// Stands in for the engine until it answers (tests and the first skeleton): a flat picture after a short wait.
// references: how many pictures each model row reports it takes, as the engine does. history: rows to start with.
// install: per model, 'missing' | 'failed' | 'downloading' to start in that state. sizes: the engine's tier list.
export function createFakeImageClient({delay = 600, ready = {'minimax-h3-image': true, 'qwen-image-2.1-turbo': true, 'moss-image-sft': true}, fail = null, steps = 0,
    references = {'minimax-h3-image': 0, 'qwen-image-2.1-turbo': 4, 'moss-image-sft': 0}, history = [], install = {}, sizes = ['standard', 'high'], fitted = null, bytes = 12.5e9, rate = 5e6} = {}) {
    const items = history.map(row => typeof row === 'string' ? {file: row, name: row.slice(row.lastIndexOf('/') + 1)} : row); let count = 0;
    const jobs = new Map(), state = {...ready}; const loading = new Map(), failed = new Set(Object.keys(install).filter(id => install[id] === 'failed'));
    // 'paused:<reason>' (busy_video, busy_picture, busy_engine): a download waiting for the card, until Continue.
    const paused = new Map(Object.keys(install).filter(id => String(install[id]).startsWith('paused')).map(id => [id, install[id].split(':')[1] || 'busy_engine']));
    for (const id of Object.keys(install)) { if (install[id] !== 'ready') state[id] = false; if (install[id] === 'downloading') loading.set(id, Date.now() - bytes / rate * .4); }
    const models = () => IMAGE_MODELS.map(m => ({id: m.id, label: m.name, ready: !!state[m.id], downloading: loading.has(m.id), max_images: references[m.id] ?? 0,
        bytes_total: bytes, bytes_done: loading.has(m.id) ? Math.min(bytes, (Date.now() - loading.get(m.id)) * rate) : failed.has(m.id) ? bytes * .31 : 0,
        install: failed.has(m.id) ? {state: 'failed', done: bytes * .31, total: bytes, reason: 'network', message_en: 'The connection dropped.', message_zh: '网络连接中断了。'}
            : paused.has(m.id) ? {state: 'paused_for_generation', done: bytes * .2, total: bytes, bytes_per_second: 0, reason: paused.get(m.id)}
            : loading.has(m.id) ? {state: 'running', done: Math.min(bytes, (Date.now() - loading.get(m.id)) * rate), total: bytes} : null}));
    return {
        info: async () => { for (const [id, at] of loading) if (Date.now() - at > bytes / rate) { state[id] = true; loading.delete(id); } return {installed: true, ready: true, max_images: 16, sizes, models: models()}; },
        packInstall: async id => { failed.delete(id); if (paused.delete(id)) loading.set(id, Date.now() - bytes / rate * .2); else loading.set(id, Date.now()); return {installed: true, sizes, models: models()}; },
        packCancel: async id => { loading.delete(id); return {installed: true, sizes, models: models()}; },
        async generate({prompt, width, height, seed, model, images = []}) {
            if (fail) throw Object.assign(new Error(fail), {code: fail});
            if (images.length > (references[model] ?? 0)) return {error: 'too_many_images'};
            const job = 'fake-' + ++count; jobs.set(job, {at: Date.now(), prompt, width, height, seed, model}); (this.requests ||= []).push({prompt, width, height, seed, model, images}); return {job};
        },
        async status(job) {
            const row = jobs.get(job); if (!row) throw Object.assign(new Error('unknown_job'), {code: 'unknown_job'});
            if (row.cancelled) return {state: 'error', error: 'cancelled'};
            const fraction = Math.min(1, (Date.now() - row.at) / delay);
            // With steps, one picture per sampling step after the load, as the H3 worker sends them.
            const step = steps && fraction >= .3 ? Math.min(steps, 1 + Math.floor((fraction - .3) / .7 * steps)) : 0;
            const preview = step ? {step, total: steps, serial: step, width: 256, height: 256} : undefined;
            if (fraction < 1) return {state: 'running', stage: fraction < .3 ? 'load' : 'sample', fraction, overall: fraction * .97, remaining_seconds: Math.max(0, (1 - fraction) * delay / 1000), report_id: 'a'.repeat(32), preview};
            const file = `freevideo-image/fake-${job}.png`; if (!items.some(i => i.file === file)) items.unshift({file, name: file.slice(15), model: row.model});
            return {state: 'done', file, fraction: 1, report_id: 'a'.repeat(32), ...(fitted ? {fitted} : {})};
        },
        async previewImage(job) {
            const row = jobs.get(job); if (!steps || !row) return null;
            const c = document.createElement('canvas'); c.width = c.height = 256; const g = c.getContext('2d');
            g.fillStyle = `hsl(${(Date.now() - row.at) / delay * 300} 60% 50%)`; g.fillRect(0, 0, 256, 256);
            return new Promise(resolve => c.toBlob(resolve, 'image/jpeg', .8));
        },
        cancel: async job => { const row = jobs.get(job); if (row) row.cancelled = true; },
        history: async () => items.slice(),
        historyDelete: async file => { const at = items.findIndex(i => i.file === file); if (at >= 0) items.splice(at, 1); return items.slice(); },
        async historyDeleteMany(files) { for (const f of files) await this.historyDelete(f); return {items: items.slice(), failed: []}; },
        historyOpen: async () => ({}),
        upload: async file => file.name,
    };
}

const pictureURL = file => { const at = file.lastIndexOf('/'); return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(at + 1), subfolder: at < 0 ? '' : file.slice(0, at), type: 'input'})); };
// Tiles, the rail and the source line show a picture's small copy; the stage, the viewer and comparisons the picture.
const thumbURL = file => hasPictureThumbnail(file) ? pictureThumbnailURL(file) : pictureURL(file);
const SAMPLE_PROMPTS = {
    "jazz": "A stylish concert poster in a mid-century modern graphic style: a saxophone player drawn as a bold silhouette in deep navy against a warm mustard-yellow background, with abstract curved shapes in teal and coral. At the top, a large clean title reads \"MIDNIGHT JAZZ\". At the bottom, one smaller line reads \"FRIDAY 9 PM\". All text is spelled exactly as given, with no other words on the poster. Flat print texture, strong composition.",
    "window": "A realistic portrait photograph of a woman in her late twenties sitting by a tall window in a quiet old apartment, soft overcast daylight on one side of her face, loose dark hair tucked behind one ear, wearing a rust-coloured linen shirt, holding a white ceramic cup with both hands, calm thoughtful expression looking slightly away from the camera, natural skin texture with fine detail, muted warm colours, shallow depth of field, 85mm lens, one person, no text.",
    "perfume": "A luxury studio product photograph of an unlabelled faceted glass perfume bottle filled with pale amber liquid and a brushed gold cap, standing on a block of white travertine stone, surrounded by a few floating soap bubbles and a sprig of white jasmine, soft beige background, warm rim light making the glass glow, sharp reflections and caustics on the stone, high-end advertising look, no logo, no text.",
    "fjord": "A wide landscape photograph of a deep fjord at golden hour, steep green cliffs dropping into still dark-blue water, a thin waterfall falling from the left cliff, low clouds catching orange light, a small red wooden cabin on a grassy ledge near the shore, mirror-like reflections on the water, crisp detail from the foreground rocks to the distant peaks, rich natural colours, no people, no text.",
    "fox": "A cute 3D clay-style illustration of a small orange fox wearing a knitted green scarf, reading a tiny book while sitting on a mossy tree stump in a miniature forest, little mushrooms and fallen autumn leaves around it, soft studio lighting, visible fingerprint texture in the clay, pastel background with gentle bokeh, playful and warm, an original character, no text."
};
const STAGE_WORDS = {load: ['Loading the model', '加载模型'], text: ['Reading the prompt', '读取提示词'], encode: ['Reading the references', '读取参考图'],
    upload: ['Reading the references', '读取参考图'], sample: ['Generating', '正在生成'], decode: ['Generating', '正在生成']};

// What each finished picture was made with, kept in this browser for the works column (prompt, model, size,
// time) and for the prompt history. The store files the picture even when this page is closed, so this listens
// at the module, not the page.
const meta = createMetaStore();
if (typeof window !== 'undefined') window.addEventListener('freevideo-job-done', event => {
    const row = event.detail?.job;
    if (row?.kind !== 'image' || row.status !== 'done' || !row.result?.file) return;
    // The store keeps no text once a job has ended; the done event carries it this once (an empty one is no text).
    const text = row.meta?.text || row.prompt || '', made = row.meta?.fitted?.width ? row.meta.fitted : {width: row.meta?.width, height: row.meta?.height};
    const seconds = Math.max(1, Math.round(((row.finished_at || Date.now()) - (row.started_at || row.created_at || Date.now())) / 1000));
    // A picture made from another one (an AI edit in the picture editor) keeps where it came from, so its facts can lead back.
    const source = row.meta?.source ? {source: row.meta.source, edit: row.meta.edit || 'edit'} : {};
    // The words stay in this page's memory only; the browser keeps the picture's model, size and time.
    if (text) sessionPrompts.set(row.result.file, text);
    meta.set(row.result.file, {model: row.model || '', width: made.width, height: made.height, seconds, at: row.finished_at || Date.now(), references: row.meta?.withImages ? 1 : 0, ...source});
    // An edit's words describe a change, not a picture: they stay out of the prompt history.
    if (!row.meta?.source) writeStore(HISTORY_KEY, rememberPrompt(readStore(HISTORY_KEY, []), text, {model: row.model || ''}));
    if (row.meta?.fitted?.width && row.meta?.width && row.meta.fitted.width * row.meta.fitted.height < row.meta.width * row.meta.height) {
        const fitted = readStore(FIT_KEY, {}); fitted[row.model] = row.meta.fitted.width * row.meta.fitted.height; writeStore(FIT_KEY, fitted);
    }
});

// What the page showed last (the picture on the stage, the works list, its scroll), so coming back, or opening it
// again after a reload, shows the same at once instead of the empty state while the engine's list is on its way.
const VIEW_CACHE = 'freevideo.imageView.cache';
let viewCache = readStore(VIEW_CACHE, null);
// What the user is writing (the prompt, where the caret was, the reference pictures and the prompt helper's state) is
// kept while this page is open, so leaving the view and coming back finds it as it was. In memory only: none of it is
// written to the browser's storage.
let keptDraft = null;
const enhancerNode = {id: 'image-view', properties: {}};
// The prompt helper's record is keyed by the pictures themselves: each file the strip holds has a number for the page's
// life, so a picture edited, swapped or put back is a different scene from the one rewritten before.
const pictureIds = new WeakMap(); let pictureSerial = 0;
const pictureId = file => { if (!pictureIds.has(file)) pictureIds.set(file, ++pictureSerial); return pictureIds.get(file); };
const keepView = value => { viewCache = value; writeStore(VIEW_CACHE, value); };

let current = null;
// The view kept after it closes (as the editor and the AI view are): {node, dialog, reopen, dispose}.
let kept = null;
// Opens above the studio when one is open, as the editing view does, so going back finds it as it was.
export function openImageView(node, {studio = null, client: given = null, poll = 500} = {}) {
    const client = given || createImageGenClient();
    // Deletes go through the page's keepalive client, so one sent as the page goes away still reaches the engine.
    const deleter = given || worksClient();
    if (current?.open) { viewChanged('image', node); return current; }
    // Coming back finds the view exactly as it was left, at once; its works are brought up to date in the background.
    if (kept && kept.node === node && !given && kept.dialog.isConnected) return kept.reopen(studio);
    kept?.dispose(); kept = null;
    void poll;
    studio?.querySelectorAll('video,audio').forEach(media => media.pause());
    const dialog = el('dialog', 'fv-studio fv-image'); dialog.dataset.fvReady = 'false'; dialog.lang = cn ? 'zh' : 'en'; dialog.setAttribute('aria-label', t('Images', '图像视图'));
    const header = el('header', 'fv-header'), brand = el('div', 'fv-brand'), tools = el('div', 'fv-header-actions');
    brand.append(wordmark());
    const settings = el('button', 'fv-quiet', t('Settings', '设置')); settings.type = 'button'; settings.onclick = openSetup;
    tip(settings, t('Settings', '设置'), t('Folders, language and engine options', '文件夹、语言和引擎选项'));
    tools.append(guideButton(t), feedbackButton(t), settings);
    let next = () => studio?.open ? viewChanged('studio', node) : viewChanged('nodes', node);
    const leave = action => { next = action; closeDialog(dialog); };
    const navigation = viewSwitch('image', () => leave(() => studio?.open ? viewChanged('studio', node) : openStudio(node)),
        () => leave(() => showNodes(node, studio)), () => leave(() => openEditorView(node, {studio: studio?.open ? studio : undefined})),
        () => leave(() => openAutopilot(node, {studio: studio?.open ? studio : undefined})), () => {});
    header.append(brand, navigation, tools);
    const place = keepViewSwitchPlace(header, navigation);

    // ───── State ─────
    const sizes = readStore(SIZE_KEY, {}), fittedAreas = readStore(FIT_KEY, {}), panels = readStore(PANEL_KEY, {});
    // The engine's last answer stands in until the new one arrives, so the model row never changes its words on the way in.
    let info = viewCache?.info || null, model = IMAGE_MODELS.find(m => m.id === remembered()) || IMAGE_MODELS[0];
    // A model the engine's list (even the cached one) does not offer is never the starting point: H3 Image on a Mac.
    const offers = m => !!info?.models?.some(r => r.id === m.id && r.available !== false);
    if (info?.models && !offers(model)) model = IMAGE_MODELS.find(offers) || model;
    let ratio = sizes.ratio || '1:1', area = sizes[model.id] || RANGES[model.id].presets.standard, locked = sizes.locked !== false, tier = 'standard';
    const promptOf = {};
    // A picture's words: from this page's memory (made while it is open) or the engine's record of it; never the browser's store.
    const promptFor = file => promptOf[file] || sessionPrompts.get(file) || rows.find(r => r.file === file)?.prompt || '';
    // running: the job this page shows ({id} once the store has it); the job itself belongs to the store and outlives the page.
    let ratioChosen = !!sizes.ratio, running = null, failure = null, lastReport = null, alive = true, shownFile = '', started = 0, selecting = false, unwatch = null, asking = false;
    let pendingGenerate = false, fitted = null, undoNote = null, view = 'works', filter = 'all', query = '', sort = readStore('freevideo.imageView.sort', 'new');
    // compare: two or more works picked in the works column are compared on the stage ({files, total}); soloKey: the
    // selection whose compare was left for one picture (it comes back when the selection changes).
    let compare = null, soloKey = '', beforeCompare = '';
    // draft: the user is setting up the next picture (shape or size changed): the stage shows its empty frame, the same
    // one Generate then fills, until a work is chosen again.
    let draft = false, draftPreview = null;
    let menu = null; const references = [], picked = new Set(); let rows = Array.isArray(viewCache?.rows) ? viewCache.rows.filter(r => r && typeof r.file === 'string') : [];
    // historyLoaded: the engine's list has come back; until then the cached list (or a neutral frame) stands in, never the samples.
    let historyLoaded = false;
    const readyParts = {info: false, history: false, picture: false};
    function markReady(part) {
        readyParts[part] = true;
        const pictureReady = readyParts.picture || !shownFile || (picture.complete && picture.naturalWidth > 0 && picture.dataset.file === shownFile);
        if ((readyParts.history || rows.length > 0) && pictureReady) dialog.dataset.fvReady = 'true';
    }
    meta.reload();
    if (viewCache?.shownFile && rows.some(r => r.file === viewCache.shownFile)) shownFile = viewCache.shownFile;
    else shownFile = rows[0]?.file || '';
    // The model made ready ahead (image_view_warm.js); only the model and the level go to the engine.
    const warmer = createWarmer({getToken: async () => info?.token || ''});
    function warmUp(level, beside = false) { if (alive && !running && modelState(info, model.id).state === 'ready') warmer.warm(model.id, level, {beside}); }
    const entry = () => info?.models?.find(m => m.id === model.id) || null;
    const missing = () => entry() ? entry().ready === false : false;
    const loading = () => modelState(info, model.id).state === 'downloading';

    // ───── Layout ─────
    const body = el('div', 'fv-image-body fv-iv');
    const left = el('aside', 'fv-iv-panel fv-iv-left fv-image-left fv-glass fv-g2'), stage = el('main', 'fv-iv-stage fv-image-stage'), column = el('aside', 'fv-iv-panel fv-iv-right fv-image-works-column fv-glass fv-g2');
    left.setAttribute('aria-label', t('Make a picture', '生成图片')); column.setAttribute('aria-label', t('Works', '作品'));
    body.append(left, stage, column);

    // Left panel: head, the groups, the foot with the status slot and Generate.
    const leftHead = el('div', 'fv-iv-panel-head');
    const leftCollapse = button('fv-iv-icon-btn fv-iv-collapse', {iconName: 'panelL', name: t('Hide settings', '收起设置栏'), use: t('More room for the picture', '给画面腾出空间')});
    leftHead.append(el('h2', 'fv-iv-panel-title', t('Create', '生成')), el('span', 'fv-iv-grow'), leftCollapse);
    const scroll = el('div', 'fv-iv-panel-scroll');
    const inputs = el('div', 'fv-image-inputs');

    // Model: both, always.
    // Model: one dropdown row; the list holds every model (both today, more later).
    const modelBox = el('section', 'fv-iv-sec fv-iv-sec-model'); modelBox.dataset.section = 'model'; modelBox.dataset.open = 'true';
    const modelGroup = {element: modelBox, body: modelBox, meta: el('span'), open() {}, isOpen: () => true};
    const cards = modelPicker({host: dialog, onPick: m => { chooseModel(m); }, onInstall: m => { chooseModel(m); void install(); }, onPause: m => { void pauseInstall(m.id); }});
    modelBox.append(cards.element);

    // Prompt: the enhancer wraps the box, as on the video page.
    // Every group is open as it is (CEO: nobody folds these); when the panel has no room it scrolls.
    const promptGroup = section('prompt', {title: t('Prompt', '描述'), iconName: 'prompt', fixed: true});
    const prompt = el('textarea', 'fv-image-prompt fv-iv-prompt'); prompt.rows = 3; prompt.maxLength = 2000;
    // Prompt help (the creation panel's own: switch, original and rewritten, rewrite again, back to the original,
    // download and enable, errors) for a picture: the engine rewrites in the chosen model's own style, with the
    // reference pictures the model takes in view; they are uploaded only when a rewrite runs.
    // Mentions travel as <Picture N> and come back as the page writes them; a rewrite with reference pictures names what
    // it keeps from them <Subject N>, which the page writes "主体 N" / "Subject N".
    if (keptDraft?.prompt) prompt.value = keptDraft.prompt;
    // The rewrite and the original are put in the box by the helper: the mention marks are drawn again over the new text.
    let paintMentions = () => {};
    const enhancer = createPromptEnhancer({node: enhancerNode, input: prompt, api, t, context: enhanceScene, setText: () => { render(); paintMentions(); }, issueButton,
        resolve: async ({references: ids, ...scene}) => {
            const media = [];
            for (const id of ids) {
                // A picture removed meanwhile makes this rewrite stale; it is dropped when the answer comes back.
                const record = references.find(r => pictureId(r.file) === id);
                if (record) media.push({file: await client.upload(record.file), role: 'reference'});
            }
            return {...scene, media};
        },
        // The rewrite names only the pictures sent with it, as the generation does.
        prepare: text => {
            const sent = references.slice(0, cap_(model)).length;
            return mentionsToEngine(text, sent).replace(/(?:图\s?|Image\s)(\d+)/g, (all, n) => Number(n) >= 1 && Number(n) <= sent ? `<Picture ${n}>` : all)
                .replace(/(?:主体\s?|Subject\s)(\d+)/g, (_, n) => `<Subject ${n}>`);
        },
        restore: text => text.replace(/<Picture (\d+)>/g, (_, n) => mentionText(n)).replace(/<Subject (\d+)>/g, (_, n) => subject(Number(n)))});
    let enhancerReady = false;
    const enhancerOff = () => false;
    enhancer.element.classList.add('fv-image-enhance');
    // Expand: the same quiet reading and editing panel as the studio and the editing view; it writes back live.
    const expand = createExpandButton({t, textarea: prompt, onGenerate: () => go.click(),
        canGenerate: () => !running && modelState(info, model.id).state === 'ready' && (!!prompt.value.trim() || references.length > 0), onDraft: () => render()});
    enhancer.model.read().then(value => { enhancerReady = value?.ready === true; render(); fitSoon(); }).catch(() => {});
    const promptBar = el('div', 'fv-iv-prompt-bar');
    const promptCount = el('span', 'fv-iv-prompt-count');
    // The prompt's tool row also adds reference pictures; their strip sits under the box when there are any.
    const refAdd = button('fv-iv-text-btn fv-iv-ref-tool', {iconName: 'refs', label: t('References', '参考图')}); const refCount = el('span', 'fv-iv-ref-count'); refAdd.append(refCount);
    // Expand sits in the tool row under the box (as in the studio), not over the text.
    // A sketch: a blank canvas at this frame in the picture editor; what is drawn becomes Image 1 and gives the layout.
    const sketchAdd = button('fv-iv-text-btn fv-iv-sketch-tool', {iconName: 'edit', label: t('Sketch', '画草图'), name: t('Draw a sketch', '画草图'),
        use: t('Draw the layout on a blank canvas of this frame; it becomes Image 1', '在当前画幅的空白画布上画出构图，作为图 1')});
    sketchAdd.addEventListener('click', () => void drawSketch());
    // Prompt enhancement is a key in this row beside References and Sketch, lit while on (no switch row of its own).
    enhancer.chip.classList.add('fv-iv-text-btn', 'fv-iv-enhance-tool'); enhancer.chip.querySelector('span')?.classList.add('fv-iv-btn-label');
    promptBar.append(refAdd, sketchAdd, enhancer.chip, el('span', 'fv-iv-grow'), promptCount, expand.element);
    // The row stays on one line inside the panel, Expand at the same inset as the box: when its words do not fit (a
    // narrow panel, longer words), its keys keep their icons only (their hover cards still name them). Measured again
    // whenever the row's own contents change (the count near the limit), not only when the panel is resized.
    const fitPromptBar = () => {
        if (!promptBar.isConnected) return;
        delete promptBar.dataset.compact;
        if (promptBar.scrollWidth > promptBar.clientWidth + 1) promptBar.dataset.compact = 'true';
    };
    const promptBarWatch = typeof ResizeObserver === 'function' ? new ResizeObserver(fitPromptBar) : null;
    promptBarWatch?.observe(promptBar); document.fonts?.ready?.then(fitPromptBar).catch?.(() => {});
    // "@" in the box lists the reference pictures; a chosen one is written as its mention and stays bound to that picture.
    const mentions = promptMentions({prompt, host: dialog, pictures: () => references.map((r, at) => ({url: r.url, label: label(at + 1)})), onAdd: () => openRefMenu(refAdd)});
    paintMentions = () => mentions.paint();
    promptGroup.body.append(enhancer.element, promptBar);

    // References.
    const refGroup = section('refs', {title: t('Reference pictures', '参考图'), iconName: 'refs'});
    const strip = el('div', 'fv-image-references fv-iv-refs'), notice = el('p', 'fv-image-notice'); notice.hidden = true; notice.setAttribute('aria-hidden', 'true');
    const refNote = el('p', 'fv-iv-note');
    const pick = el('input'); pick.type = 'file'; pick.accept = 'image/*'; pick.multiple = true; pick.hidden = true;
    promptGroup.body.append(strip, refNote, notice, pick);
    const refHeadAdd = button('fv-iv-icon-btn fv-iv-sec-add', {iconName: 'plus', name: t('Add a reference picture', '添加参考图'), use: t('From works, the library or this computer', '从作品、素材库或电脑选择')});
    refHeadAdd.addEventListener('click', () => openRefMenu(refHeadAdd));
    refAdd.addEventListener('click', () => openRefMenu(refAdd));
    refGroup.tools.append(refHeadAdd);

    // Frame and size.
    const sizeGroup = section('size', {title: t('Frame & size', '画幅与尺寸'), iconName: 'size', fixed: true});

    // Seed: the video panel's own field and wording; empty draws a new seed for each picture, a number keeps it.
    const advGroup = section('advanced', {title: t('Advanced', '高级选项'), iconName: 'sliders', fixed: true});
    advGroup.element.classList.add('fv-image-advanced');
    const advanced = {get open() { return advGroup.isOpen(); }, set open(value) { advGroup.open(!!value); }};
    const seedInput = el('input'); seedInput.type = 'number'; seedInput.min = '0'; seedInput.max = String(2 ** 53 - 1); seedInput.step = '1';
    seedInput.placeholder = t('New each run', '每次随机'); tip(seedInput, t('Seed', '种子'), t('Empty: new each time; a number repeats a picture', '留空每次不同；填数字可复现同一张'));
    const dice = button('fv-iv-icon-btn', {iconName: 'dice', name: t('Random seed', '随机种子'), use: t('Fill in a random seed', '填入一个随机种子')});
    dice.addEventListener('click', () => { seedInput.value = String(randomSeed()); render(); });
    seedInput.addEventListener('input', () => render());
    const seedField = el('label', 'fv-image-seed fv-iv-field fv-iv-field-wide'); seedField.append(el('span', 'fv-iv-field-key', t('Seed', '种子')), seedInput, dice);
    // LoRA: a button whose panel opens beside it; it answers for the model that takes LoRA files.
    const loraRow = imageViewLora({host: dialog, fetchApi: (path, options) => api.fetchApi(path, options),
        modelName: () => IMAGE_MODELS.find(m => m.id === LORA_MODEL)?.name || 'Qwen-Image 2.1', onChange: () => render()});
    advGroup.body.append(loraRow.element, seedField);

    // Prompt history and presets.
    const libGroup = section('library', {title: t('History and presets', '提示词与预设'), iconName: 'history', fixed: true});
    const library = promptLibrary({onUsePrompt: text => { prompt.value = text; prompt.dispatchEvent(new Event('input', {bubbles: true})); promptGroup.open(true); prompt.focus(); },
        onApplyPreset: preset => applyPreset(preset), currentSettings: () => ({model: model.id, ratio, area, seed: seedInput.value || '', prompt: prompt.value}),
        describe: p => [IMAGE_MODELS.find(m => m.id === p.model)?.short, p.ratio && p.ratio !== 'same' && RATIOS.includes(p.ratio) ? p.ratio : p.ratio && p.ratio !== 'same' ? t('Custom', '自定') : '', p.area ? `${megapixels(p.area)} MP` : '', p.prompt ? t('with prompt', '含提示词') : ''].filter(Boolean).join(' · ')});
    libGroup.body.append(library.element);

    inputs.append(modelGroup.element, promptGroup.element, sizeGroup.element, advGroup.element, libGroup.element);
    inputs.addEventListener('fv-iv-section', event => { const box = event.target.closest('.fv-iv-sec'); if (box) { if (box.dataset.open === 'true') box.dataset.pinned = 'true'; else delete box.dataset.pinned; } render(); });
    scroll.append(inputs);
    const leftFoot = el('div', 'fv-iv-panel-foot fv-iv-gen-foot sgf-avoid');
    // The status slot: always this tall; its words change, nothing below or around it moves.
    const slot = el('div', 'fv-iv-slot'); slot.setAttribute('role', 'status');
    const note = el('p', 'fv-image-note'); note.hidden = true;
    const go = el('button', 'fv-image-go fv-iv-go'); go.type = 'button';
    const goFill = el('i', 'fv-iv-go-fill'), goLabel = el('span', 'fv-iv-go-label'), goSub = el('span', 'fv-iv-go-sub');
    go.append(goFill, goLabel, goSub);
    leftFoot.append(slot, note, go);
    const leftRail = el('div', 'fv-iv-rail');
    const railOpen = button('fv-iv-icon-btn', {iconName: 'panelL', name: t('Show settings', '展开设置栏'), use: t('Model, prompt, size and more', '模型、描述、尺寸等')});
    const railGen = button('fv-iv-icon-btn fv-iv-rail-go', {iconName: 'gen'});
    leftRail.append(railOpen, el('i', 'fv-iv-rail-sep'), ...['model', 'prompt', 'refs', 'size'].map(name => {
        const b = button('fv-iv-icon-btn', {iconName: name, name: {model: t('Model', '模型'), prompt: t('Prompt', '描述'), refs: t('Reference pictures', '参考图'), size: t('Frame & size', '画幅与尺寸')}[name], use: t('Open the settings at this group', '展开设置栏并跳到这一组')});
        b.addEventListener('click', () => { setPanel('left', true); const s = inputs.querySelector(`[data-section=${name}]`); s?.scrollIntoView({block: 'nearest'}); });
        return b;
    }), el('span', 'fv-iv-grow'), railGen);
    // Generate sits at the panel's foot, its bottom level with the stage's action row.
    left.append(leftHead, scroll, leftFoot, leftRail);

    // Stage: the picture with its actions under it, from the top edge of the panels down; compare over it.
    // (No bar above: ← → step through the works, and compare comes from picking works.)

    const canvasArea = el('div', 'fv-iv-canvas');
    const shot = el('div', 'fv-iv-shot');
    // The picture's frame: the floating window grows out of it and back into it; the centring check measures it.
    const canvas = el('div', 'fv-iv-frame fv-image-canvas sgf-avoid'); canvas.dataset.fvTaskStage = 'image'; canvas.setAttribute('data-fv-main', '');
    const frame = canvas;
    const pic = el('div', 'fv-iv-pic');
    const picture = el('img', 'fv-iv-picture'); picture.alt = ''; picture.draggable = false;
    const stepFrame = el('div', 'fv-image-step'), stepPicture = el('img'); stepPicture.alt = ''; stepFrame.hidden = true; stepFrame.append(stepPicture); let stepURL = '';
    // The step picture's URL belongs to the job store (the floating window shows the same one); the page only points at it.
    const showStep = url => { if (stepURL === url) return; stepURL = url; stepPicture.src = url; stepFrame.hidden = false; block.classList.add('fv-image-block-low'); };
    const clearStep = () => { stepURL = ''; stepPicture.removeAttribute('src'); stepFrame.hidden = true; block.classList.remove('fv-image-block-low'); };
    const block = el('div', 'fv-image-block'), progressText = el('p', 'fv-image-progress');
    const progressLine = el('b'), progressSub = el('span'), runReport = el('div', 'fv-image-run-report');
    progressLine.setAttribute('role', 'status'); progressText.append(progressLine, progressSub); block.append(progressText, runReport);
    const meter = el('i', 'fv-iv-meter');
    // The video's rising wave: it reads the level from this element, as in the studio.
    const sceneHost = el('div', 'fv-iv-scene'), level = el('div'); level.hidden = true; sceneHost.append(level);
    const stopScene = createPreviewScene(sceneHost, level, el('div'));
    const grid = el('div', 'fv-image-samples fv-iv-samples');
    for (const key of cn ? ['fox', 'window', 'perfume', 'fjord'] : ['jazz', 'window', 'perfume', 'fjord']) {
        const b = el('button'), i = el('img'); b.type = 'button'; i.alt = ''; i.draggable = false; i.src = new URL(`./assets/tour/sample-${key}.webp`, import.meta.url).href;
        if (key === 'jazz') i.style.objectPosition = '50% 0';
        tip(b, t('Try this prompt', '试试这条描述'), SAMPLE_PROMPTS[key].slice(0, 48) + '…'); b.append(i);
        b.onclick = () => { prompt.value = SAMPLE_PROMPTS[key]; prompt.dispatchEvent(new Event('input', {bubbles: true})); promptGroup.open(true); prompt.focus(); };
        grid.append(b);
    }
    const failHost = el('div', 'fv-image-fail fv-iv-fail fv-glass fv-g3'); failHost.setAttribute('role', 'alert');
    const draftNote = el('div', 'fv-iv-draft'), draftSize = el('b'), draftHint = el('span'); draftNote.append(draftSize, draftHint); draftNote.hidden = true;
    pic.append(sceneHost, picture, stepFrame, block, draftNote, meter);
    frame.append(pic, grid, failHost);
    // The caption row (facts, Edit, reference, star, download, more) is where the hand goes after a picture: the floating window keeps off it.
    const cap = el('div', 'fv-iv-cap fv-image-actions sgf-avoid');
    const info_ = el('p', 'fv-image-info fv-iv-cap-info'), infoText = el('span'), doneReport = el('span', 'fv-image-done-report');
    // The picture's facts, the prompt among them (selectable, to copy), one click from its caption.
    const infoBtn = button('fv-iv-icon-btn fv-iv-info-btn', {iconName: 'info', name: t('About this picture', '图片信息'), use: t('Prompt, model, size and time', '提示词、模型、尺寸和用时'), key: 'I'});
    const infoPop = el('div', 'fv-iv-info'); infoPop.setAttribute('aria-label', t('About this picture', '图片信息'));
    infoBtn.addEventListener('click', () => { if (rightTab === 'info' && column.dataset.collapsed !== 'true') setRightTab('works'); else { setRightTab('info'); setPanel('right', true); } });
    info_.append(infoBtn, infoText, doneReport);
    const editThis = button('fv-iv-text-btn fv-image-action', {iconName: 'edit', label: t('Edit', '编辑'), name: t('Edit', '编辑'), use: t('Make a new version: it becomes Image 1', '以这张为图 1，描述要改的地方')});
    const asReference = button('fv-iv-text-btn fv-image-action', {iconName: 'asref', label: t('Use as reference', '用作参考图'), name: t('Use as reference', '用作参考图'), use: t('Add it to the reference pictures', '加入参考图')});
    const favBtn = button('fv-iv-icon-btn fv-iv-fav-btn', {iconName: 'star'});
    const save = el('a', 'fv-iv-icon-btn fv-iv-btn fv-image-action'); save.setAttribute('download', ''); save.append(icon('down'));
    tip(save, t('Download', '下载'), t('Save the full-size picture', '保存原尺寸图片'));
    const moreButton = button('fv-iv-icon-btn', {iconName: 'more', name: t('More', '更多'), use: t('Folder, copy prompt, delete', '打开文件夹、复制提示词、删除')});
    moreButton.setAttribute('aria-haspopup', 'true');
    const more = el('div', 'fv-iv-menu fv-image-more fv-glass fv-g3'); more.hidden = true; more.setAttribute('role', 'menu');
    const menuItem = (iconName, text, use, onClick, cls = '') => { const b = el('button', 'fv-iv-menu-item ' + cls); b.type = 'button'; b.setAttribute('role', 'menuitem'); b.append(icon(iconName), el('span', '', text)); tip(b, text, use); b.addEventListener('click', () => { more.hidden = true; onClick(); }); return b; };
    const copyPrompt = () => { const text = promptFor(shownFile); try { void navigator.clipboard?.writeText(text); } catch { /* the clipboard may be refused */ } };
    const usePromptItem = menuItem('prompt', t('Use prompt', '使用提示词'), t('Put its prompt back in the box', '把它的提示词填回描述框'), () => { const text = promptFor(shownFile); if (text) { prompt.value = text; prompt.dispatchEvent(new Event('input', {bubbles: true})); promptGroup.open(true); } });
    more.append(menuItem('folder', t('Open folder', '打开文件夹'), t('Show the pictures on this computer', '在电脑上打开保存图片的文件夹'), () => void openFolder()),
        menuItem('copy', t('Copy prompt', '复制提示词'), t('Copy the words this picture was made from', '复制这张图的提示词'), copyPrompt), usePromptItem,
        menuItem('trash', t('Delete', '删除'), t('Move to recently deleted; can be undone', '移到最近删除，可以撤销'), () => deleteFiles([shownFile]), 'fv-iv-danger'));
    const moreHost = el('span', 'fv-image-more-host fv-iv-more-host'); moreHost.append(moreButton, more);
    // Where there is room the menu's actions sit in the row itself; the menu is left for a phone.
    const inline = el('span', 'fv-iv-cap-inline');
    const inlineBtn = (iconName, name, use, onClick, cls = '') => { const b = button('fv-iv-icon-btn ' + cls, {iconName, name, use}); b.addEventListener('click', onClick); return b; };
    const copyInline = inlineBtn('copy', t('Copy prompt', '复制提示词'), t('Copy the words this picture was made from', '复制这张图的提示词'), copyPrompt);
    const useInline = inlineBtn('prompt', t('Use prompt', '使用提示词'), t('Put its prompt back in the box', '把它的提示词填回描述框'), () => usePromptItem.click());
    inline.append(copyInline, useInline,
        inlineBtn('folder', t('Open folder', '打开文件夹'), t('Show the pictures on this computer', '在电脑上打开保存图片的文件夹'), () => void openFolder()),
        inlineBtn('trash', t('Delete', '删除'), t('Move to recently deleted; can be undone', '移到最近删除，可以撤销'), () => deleteFiles([shownFile]), 'fv-iv-danger'));
    const capActions = el('div', 'fv-iv-cap-actions'); capActions.append(editThis, asReference, el('i', 'fv-iv-sep'), favBtn, save, inline, moreHost);
    cap.append(info_, capActions);
    function paintInfo() {
        const m = meta.get(shownFile) || {}, text = promptFor(shownFile);
        const row = (k, v) => { const r = el('div', 'fv-iv-info-row'); r.append(el('span', 'fv-iv-info-k', k), el('span', 'fv-iv-info-v fv-copyable', v)); return r; };
        const parts = [];
        if (text) parts.push(el('p', 'fv-iv-info-prompt fv-copyable', text));
        const model_ = IMAGE_MODELS.find(x => x.id === m.model)?.name;
        if (model_) parts.push(row(t('Model', '模型'), model_));
        const w = picture.dataset.file === shownFile && picture.naturalWidth ? picture.naturalWidth : m.width, h = picture.dataset.file === shownFile && picture.naturalHeight ? picture.naturalHeight : m.height;
        if (w) parts.push(row(t('Size', '尺寸'), `${w} × ${h} · ${megapixels(w * h)} MP`));
        if (m.seconds) parts.push(row(t('Time', '用时'), t(`${m.seconds} s`, `${m.seconds} 秒`)));
        if (m.seed) parts.push(row(t('Seed', '种子'), String(m.seed)));
        // Where it came from (an edit of another work): its thumbnail and name, opening it on the stage.
        if (m.source) {
            const name = String(m.source).slice(String(m.source).lastIndexOf('/') + 1), there = rows.find(r => r.file === m.source || r.name === name);
            const line = el('div', 'fv-iv-info-row fv-iv-info-row-src'), go = el('button', 'fv-iv-info-src'); go.type = 'button';
            if (there) { const img = el('img'); img.src = thumbURL(there.file); img.alt = ''; img.loading = 'lazy'; go.append(img); }
            go.append(el('span', '', name));
            if (there) { tip(go, t('Open the source', '打开来源'), t('Show the work this picture was made from', '显示这张图的来源作品')); go.addEventListener('click', () => showFile(there.file)); }
            else { go.disabled = true; tip(go, t('Source', '来源'), t('The source is no longer in the works', '来源作品已不在作品里')); }
            line.append(el('span', 'fv-iv-info-k', t('Source', '来源')), go); parts.push(line);
        }
        if (!text) parts.push(el('p', 'fv-iv-info-none', t('Prompts are not kept in this browser; one shows while the page that made the picture is open.', '提示词不保存在浏览器里，只在生成它的页面打开期间显示。')));
        if (text) {
            const acts = el('div', 'fv-iv-info-acts');
            const copy = button('fv-iv-text-btn', {iconName: 'copy', label: t('Copy prompt', '复制提示词'), name: t('Copy prompt', '复制提示词'), use: t('Copy the words this picture was made from', '复制这张图的提示词')});
            copy.addEventListener('click', copyPrompt);
            const reuse = button('fv-iv-text-btn', {iconName: 'prompt', label: t('Use prompt', '使用提示词'), name: t('Use prompt', '使用提示词'), use: t('Put its prompt back in the box', '把它的提示词填回描述框')});
            reuse.addEventListener('click', () => { prompt.value = text; prompt.dispatchEvent(new Event('input', {bubbles: true})); promptGroup.open(true); if (narrowQuery.matches || tallQuery.matches) setPanel('left', true); });
            acts.append(copy, reuse); parts.push(acts);
        }
        if (!shownFile) { const e = el('div', 'fv-iv-empty fv-iv-empty-small'); e.append(icon('info'), el('span', '', t('Pick a picture to see its facts', '选一张图片查看信息'))); parts.splice(0, parts.length, e); }
        infoPop.replaceChildren(...parts);
    }
    // The picture is centred in the room above; the action row stays at the foot of the stage whatever the picture's shape.
    const frameBox = el('div', 'fv-iv-frame-box'); frameBox.append(frame);
    shot.append(frameBox, cap);
    canvasArea.append(shot);
    const sizeOf = file => { const m = meta.get(file); return m?.width ? `${m.width} × ${m.height}` : ''; };
    const comparer = compareView({pictureURL, labelOf: sizeOf, ratioOf: file => { const m = meta.get(file); return m?.width ? m.width / m.height : 0; },
        onPick: file => { soloKey = pickedKey(); showFile(file); },
        onRemove: file => { picked.delete(file); drawWorks(); render(); },
        onClose: () => { selecting = false; picked.clear(); drawWorks(); render(); }});
    comparer.element.classList.add('sgf-avoid'); canvasArea.append(comparer.element);
    stage.append(canvasArea);
    // The shared frame and size picker. An option pointed at shows the next picture's empty frame at that shape (over the
    // shown work too, which comes back when the pointer leaves without a choice); nothing is drawn over a work. A picture
    // being made, or a compare, keeps the stage.
    const sizer = sizePicker({onChange: patch => changeSize(patch), onPreview: frame => {
        if ((running && !browsing) || compare) return;
        draftPreview = frame?.width ? {width: frame.width, height: frame.height} : null; drawStage();
    }});
    sizeGroup.body.append(sizer.element);
    // Opening "Custom size" is the user opening this group: the first-screen fit never folds it away under them.
    sizer.element.addEventListener('click', event => { if (event.target.closest?.('.fvp-custom-item')) { sizeGroup.element.dataset.pinned = 'true'; fitSoon(); } }, true);
    // The floating window keeps inside the stage here: the four corners of the window belong to the panels.
    // The whole stage, bar included: its empty right end above the picture is the least covering place when the picture fills the canvas.
    stage.dataset.floatBounds = ''; stage.id = stage.id || 'fv-image-stage';

    // The status line: what is here and the page's own settings. It never holds progress. A delete is not announced:
    // the picture is in recently deleted, where it can be put back.
    const sbar = el('div', 'fv-iv-sbar');
    const sbarState = el('span', 'fv-iv-sbar-state');
    const densityBtn = button('fv-iv-icon-btn', {iconName: 'sliders'}), themeBtn = button('fv-iv-icon-btn', {iconName: 'info'}), keysBtn = button('fv-iv-icon-btn', {iconName: 'doc', name: t('Shortcuts', '快捷键'), use: t('Every key this page answers to', '这个页面的全部快捷键'), key: '?'});
    const keysPop = el('div', 'fv-iv-menu fv-iv-keys fv-glass fv-g3'); keysPop.hidden = true; keysPop.setAttribute('role', 'dialog');
    const keysHost = el('span', 'fv-iv-more-host'); keysHost.append(keysBtn, keysPop);
    sbar.append(sbarState, el('span', 'fv-iv-grow'), densityBtn, themeBtn, keysHost);
    // Held back for now: the shell settles one status area for all five views.
    void sbar;
    let density = readStore('freevideo.imageView.density', 'comfortable');
    function paintSettings() {
        body.dataset.density = density;
        densityBtn.replaceChildren(icon(density === 'compact' ? 'grid4' : 'grid3'));
        tip(densityBtn, density === 'compact' ? t('Compact', '紧凑') : t('Comfortable', '舒适'), density === 'compact' ? t('Smaller tiles, more at once', '缩略图更小，一屏更多') : t('Larger tiles and rows', '缩略图和行更大'));
        const light = document.documentElement.dataset.theme === 'light';
        themeBtn.replaceChildren(icon(light ? 'moon' : 'sun'));
        tip(themeBtn, light ? t('Dark appearance', '深色外观') : t('Light appearance', '浅色外观'), t('Switch every view\'s colours', '切换所有视图的颜色'));
    }
    densityBtn.addEventListener('click', () => { density = density === 'compact' ? 'comfortable' : 'compact'; writeStore('freevideo.imageView.density', density); paintSettings(); });
    themeBtn.addEventListener('click', () => {
        const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
        if (globalThis.FreeVideoShell?.setTheme) globalThis.FreeVideoShell.setTheme(next); else document.documentElement.dataset.theme = next;
        paintSettings();
    });
    const KEYS = [[`${mod}+Enter`, t('Generate', '生成')], ['← →', t('Previous / next picture', '上一张 / 下一张')], ['F', t('Star or unstar', '收藏 / 取消收藏')], [`${mod} / Shift`, t('Pick several works to compare', '多选作品即可对比')], ['I', t('Picture facts', '图片信息')], ['Delete', t('Delete (to recently deleted)', '删除（移到最近删除）')],
        [`${mod}+F`, t('Search works', '搜索作品')], [`${mod}+A`, t('Select all while selecting', '多选时全选')], ['Esc', t('Close the top layer', '关闭最上层')]];
    keysPop.append(...KEYS.map(([k, what]) => { const r = el('div', 'fv-iv-key-row'); r.append(el('span', '', what), el('kbd', 'fv-iv-kbd', k)); return r; }));
    keysBtn.addEventListener('click', () => { keysPop.hidden = !keysPop.hidden; keysBtn.setAttribute('aria-pressed', String(!keysPop.hidden)); });
    function paintStatus() {
        const live = rows.filter(r => !isGone(r.file)).length;
        sbarState.replaceChildren(el('i', 'fv-iv-dot' + (info ? '' : ' fv-iv-dot-wait')), el('span', '', info ? t(`Ready · ${live} pictures`, `就绪 · ${live} 张`) : t('Connecting…', '正在连接…')));
    }
    paintSettings();

    // Right panel: the works.
    const works = historyPanel({pictureURL, thumbURL, queue: mediaQueue(),
        onShow: file => showFile(file),
        onToggleFav: files => { const list = files || [...picked]; if (!list.length) return; const all = list.every(f => meta.isFav(f)); meta.setFav(list, !all); drawWorks(); render(); },
        onSelect: (files, add) => { if (!selecting) selecting = true; if (add) files.forEach(f => picked.add(f)); else files.forEach(f => picked.has(f) ? picked.delete(f) : picked.add(f)); drawWorks(); render(); },
        onSelectMode: value => { selecting = value ?? !selecting; picked.clear(); drawWorks(); render(); },
        onSelectAll: () => { visible().forEach(r => picked.add(r.file)); drawWorks(); render(); },
        onDelete: files => deleteFiles(files || [...picked]),
        onFilter: value => { filter = value; drawWorks(); drawStage(); },
        onQuery: value => { query = value; drawWorks(); drawStage(); },
        onSort: value => { sort = value; writeStore('freevideo.imageView.sort', value); drawWorks(); drawStage(); },
        onTrash: value => { view = value === false ? 'works' : view === 'trash' ? 'works' : 'trash'; drawWorks(); },
        onRestore: files => { restoreWorks(files); if (undoNote) undoNote.files = undoNote.files.filter(f => !files.includes(f)); if (!undoNote?.files.length) undoNote = null; drawWorks(); render(); },
        onPurge: () => { void trash.flush(); },
        onOpenFolder: () => void openFolder(),
        onCollapse: () => setPanel('right', false), onRun: () => returnToRun()});
    const rightRail = el('div', 'fv-iv-rail fv-iv-rail-right');
    const rightRailOpen = button('fv-iv-icon-btn', {iconName: 'panelR', name: t('Show works', '展开作品栏'), use: t('Your pictures, newest first', '你的图片，最新在前')});
    const railThumbs = el('div', 'fv-iv-rail-thumbs');
    rightRail.append(rightRailOpen, el('i', 'fv-iv-rail-sep'), railThumbs);
    // The right column's three pages: works, the shown picture's facts, and the queue of every view.
    const colHead = el('div', 'fv-iv-panel-head fv-iv-col-head');
    const tabs = el('div', 'fv-iv-seg fv-iv-col-tabs'); tabs.setAttribute('role', 'tablist');
    const tabWorks = button('', {label: t('Works', '作品'), name: t('Works', '作品'), use: t('Every picture made here', '在这里生成的所有图片')});
    const tabInfo = button('', {label: t('Info', '信息'), name: t('Info', '信息'), use: t('Prompt, model, size and time of the shown picture', '当前图片的提示词、模型、尺寸和用时'), key: 'I'});
    const tabQueue = button('', {label: t('Queue', '队列'), name: t('Queue', '队列'), use: t('What is generating in every view', '各个视图正在生成和排队的任务')});
    const tabWorksN = el('span', 'fv-iv-tab-n'), tabQueueN = el('span', 'fv-iv-tab-n'); tabWorks.append(tabWorksN); tabQueue.append(tabQueueN);
    for (const b of [tabWorks, tabInfo, tabQueue]) b.setAttribute('role', 'tab');
    tabs.append(tabWorks, tabInfo, tabQueue);
    const colCollapse = button('fv-iv-icon-btn fv-iv-collapse', {iconName: 'panelR', name: t('Hide works', '收起作品栏'), use: t('More room for the picture', '给画面腾出空间')});
    colHead.append(tabs, colCollapse);
    const infoPane = el('div', 'fv-iv-col-pane fv-iv-info-pane'); infoPane.append(infoPop);
    const queue = queuePanel({jobs, confirmStop, pictureURL, onOpen: row => openQueued(row)});
    queue.element.classList.add('fv-iv-col-pane');
    let rightTab = readStore('freevideo.imageView.rightTab', 'works');
    function setRightTab(name) {
        rightTab = name; writeStore('freevideo.imageView.rightTab', name === 'info' ? 'works' : name);
        column.dataset.tab = name;
        for (const [b, n] of [[tabWorks, 'works'], [tabInfo, 'info'], [tabQueue, 'queue']]) { b.setAttribute('aria-pressed', String(n === name)); b.setAttribute('aria-selected', String(n === name)); }
        works.element.hidden = name !== 'works'; infoPane.hidden = name !== 'info'; queue.element.hidden = name !== 'queue';
        infoBtn.setAttribute('aria-pressed', String(name === 'info'));
        if (name === 'info') paintInfo();
    }
    tabWorks.addEventListener('click', () => setRightTab('works'));
    tabInfo.addEventListener('click', () => setRightTab('info'));
    tabQueue.addEventListener('click', () => setRightTab('queue'));
    colCollapse.addEventListener('click', () => setPanel('right', false));
    column.addEventListener('fv-iv-queue', event => { tabQueueN.textContent = event.detail.active ? String(event.detail.active) : ''; });
    column.append(colHead, works.element, infoPane, queue.element, rightRail);
    // A queued row opens its own generation screen: here for a picture, in its view otherwise.
    function openQueued(row) {
        if (row.kind === 'image' && (row.view === 'image' || !row.view)) {
            if (row.result?.file && !ACTIVE.has(row.status)) { showFile(row.result.file); return; }
            if (running) { returnToRun(); return; }
        }
        const shell = globalThis.FreeVideoShell; if (row.view && row.view !== 'image' && shell?.go) void shell.go(row.view);
    }
    dialog.append(header, body);

    // Other surfaces (notifications, the floating window) drop or bring back the same pictures.
    const announce = (name, files, extra = {}) => { if (files.length) window.dispatchEvent(new CustomEvent(name, {detail: {ids: files.slice(), paths: files.slice(), source: 'image-view', ...extra}})); };
    function restoreWorks(files) { const list = files.filter(f => trash.has(f)); trash.restore(files); announce('freevideo-works-restored', list); }
    // A picture is out of the works while it waits out an undo window: this page's, the picture editor's, or one
    // announced by another surface (the works library).
    const elsewhere = new Set();
    const isGone = file => trash.has(file) || elsewhere.has(file) || isHeld(file);
    const trash = createTrash({client: deleter, onChange: (answer, list) => {
        if (answer) {
            // A file the engine no longer has is gone, not kept.
            const kept = new Set((answer.failed || []).filter(f => f.reason !== 'missing').map(f => f.file));
            const gone = (list || []).filter(f => !kept.has(f));
            // The engine's own bin answers without a list: what it deleted leaves the works here, not only on the next load.
            if (answer.items) rows = answer.items; else rows = rows.filter(r => !gone.includes(r.file));
            if (gone.length) { meta.forget(gone); void removeSavedImages(api, gone).catch(() => {}); announce('freevideo-works-removed', gone, {undoable: false}); }
            if (kept.size) say(t(`Could not delete ${kept.size}.`, `有 ${kept.size} 张没能删除。`));
            if (undoNote && list?.some(f => undoNote.files.includes(f))) undoNote = null;
        }
        if (alive) { drawWorks(); render(); }
    }});

    // ───── Panels fold to a rail; the stage stays centred on the window either way ─────
    // Under 1280 wide both panels rest as rails and open as drawers over the stage, one at a time, so the picture
    // keeps the room; the stage still measures from the two rails, so it stays centred. Wide, they dock and fold.
    const narrowQuery = matchMedia('(min-width: 720px) and (max-width: 1279px)');
    // A tall window (portrait, wider than a phone): the settings open as a drawer over the works, and the works panel
    // opens below them, taking the height they leave (at least 320 px), so it never narrows them from the side.
    const tallQuery = matchMedia('(orientation: portrait) and (min-width: 720px)');
    let drawer = '', below = false;
    function paintPanels() {
        if (tallQuery.matches) {
            if (drawer === 'right') drawer = '';
            left.dataset.collapsed = String(drawer !== 'left'); column.dataset.collapsed = String(!below);
            body.dataset.tall = 'true'; body.dataset.below = String(below); body.dataset.narrow = 'true'; body.dataset.drawer = drawer;
            body.dataset.left = 'rail'; body.dataset.right = 'rail';
            paintCover(); fitTallSoon();
            return;
        }
        delete body.dataset.tall; delete body.dataset.below;
        const narrow = narrowQuery.matches;
        const leftShown = narrow ? drawer === 'left' : panels.left !== false, rightShown = narrow ? drawer === 'right' : panels.right !== false;
        left.dataset.collapsed = String(!leftShown); column.dataset.collapsed = String(!rightShown);
        body.dataset.narrow = String(narrow); body.dataset.drawer = narrow ? drawer : '';
        body.dataset.left = leftShown && !narrow ? 'open' : 'rail'; body.dataset.right = rightShown && !narrow ? 'open' : 'rail';
        paintCover();
    }
    // The next picture's empty frame is shown whole where the stage is not covered: an open settings drawer (narrow
    // windows) covers its left part, so the frame centres in the rest.
    function paintCover() {
        let cover = 0;
        if (stage.dataset.state === 'draft' && body.dataset.drawer === 'left' && !tallQuery.matches && !matchMedia('(max-width: 719px)').matches) {
            const l = left.getBoundingClientRect(), c = canvasArea.getBoundingClientRect();
            cover = Math.max(0, Math.round(l.right + 12 - c.left));
        }
        shot.style.setProperty('--iv-cover', `${cover}px`);
    }
    function setPanel(side, open) {
        if (tallQuery.matches && side === 'right') below = open;
        else if (tallQuery.matches || narrowQuery.matches) drawer = open ? side : drawer === side ? '' : drawer;
        else { panels[side] = open; writeStore(PANEL_KEY, panels); }
        paintPanels(); if (open && side === 'left') fitSoon();
    }
    narrowQuery.addEventListener('change', () => { drawer = ''; paintPanels(); fitSoon(); });
    tallQuery.addEventListener('change', () => { drawer = ''; below = false; paintPanels(); fitSoon(); });
    // A drawer closes when the stage is touched; on a tall window, when anything outside it is.
    stage.addEventListener('pointerdown', () => { if (drawer) { drawer = ''; paintPanels(); } });
    body.addEventListener('pointerdown', event => { if (tallQuery.matches && drawer === 'left' && !left.contains(event.target)) { drawer = ''; paintPanels(); } });
    // The works grid on a tall window (the four samples, or three or four works compared): of one to four columns, the
    // layout that gives each picture the most room in the width and height there are; it keeps to the top. With the
    // works panel open below, the panel takes the height the grid leaves.
    const TALL_PANEL = 320;
    let tallFrame = 0;
    function fitTallSoon() { cancelAnimationFrame(tallFrame); tallFrame = requestAnimationFrame(() => { tallFrame = 0; fitTall(); }); }
    function bestGrid(n, ratio, width, height, gap) {
        let best = null;
        for (let cols = 1; cols <= Math.min(4, n); cols++) {
            const rows = Math.ceil(n / cols);
            const w = Math.min((width - (cols - 1) * gap) / cols, ((height - (rows - 1) * gap) / rows) * ratio);
            if (w > 0 && (!best || w > best.w + 0.5)) best = {cols, rows, w: Math.floor(w), h: Math.floor(w / ratio)};
        }
        return best && {...best, height: best.rows * best.h + (best.rows - 1) * gap, width: best.cols * best.w + (best.cols - 1) * gap};
    }
    function fitTall() {
        if (!alive) return;
        const panes = comparer.element.querySelector('.fv-iv-compare-panes');
        if (!tallQuery.matches) {
            for (const target of [shot, panes]) for (const name of ['--iv-cols', '--iv-rows', '--iv-tw', '--iv-th', '--iv-grid-ar']) target?.style.removeProperty(name);
            body.style.removeProperty('--iv-below');
            return;
        }
        const now = parseFloat(body.style.getPropertyValue('--iv-below')) || TALL_PANEL + 12;
        let used = 0, spare = 0;
        if (stage.dataset.mode === 'compare' && comparer.element.dataset.mode === 'grid' && panes) {
            const files = compare?.files || [], ratios = files.map(file => { const m = meta.get(file); return m?.width ? m.width / m.height : 0; }).filter(r => r > 0);
            const ratio = ratios.length ? ratios.reduce((a, b) => a + b, 0) / ratios.length : 1;
            const extra = below ? now - (TALL_PANEL + 12) : 0, height = panes.clientHeight + extra;
            const g = bestGrid(files.length, ratio, panes.clientWidth, height, 12);
            if (g) {
                for (const [name, value] of [['--iv-cols', g.cols], ['--iv-rows', g.rows], ['--iv-tw', `${g.w}px`], ['--iv-th', `${g.h}px`]]) panes.style.setProperty(name, String(value));
                used = 1; spare = height - g.height;
            }
        } else if (stage.dataset.state === 'blank') {
            const pictures = [...grid.querySelectorAll('img')], first = pictures.find(i => i.naturalWidth);
            const ratio = first ? first.naturalWidth / first.naturalHeight : 1;
            const extra = below ? now - (TALL_PANEL + 12) : 0, height = canvasArea.clientHeight - 88 + extra;
            const g = bestGrid(pictures.length, ratio, canvasArea.clientWidth, height, 12);
            if (g) {
                for (const [name, value] of [['--iv-cols', g.cols], ['--iv-rows', g.rows]]) shot.style.setProperty(name, String(value));
                shot.style.setProperty('--iv-grid-ar', String(g.width / g.height));
                used = 1; spare = height - g.height;
            }
        }
        const want = below ? Math.round(TALL_PANEL + 12 + (used ? Math.max(0, spare) : 0)) : 0;
        if (want) { if (Math.abs(want - now) > 1 || !body.style.getPropertyValue('--iv-below')) body.style.setProperty('--iv-below', `${want}px`); }
        else body.style.removeProperty('--iv-below');
    }
    const tallWatch = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { if (tallQuery.matches) fitTallSoon(); }) : null;
    tallWatch?.observe(body);
    for (const image of grid.querySelectorAll('img')) image.addEventListener('load', () => { if (tallQuery.matches) fitTallSoon(); });
    comparer.element.addEventListener('load', () => { if (tallQuery.matches) fitTallSoon(); }, true);
    leftCollapse.addEventListener('click', () => setPanel('left', false));
    railOpen.addEventListener('click', () => setPanel('left', true));
    rightRailOpen.addEventListener('click', () => setPanel('right', true));
    railGen.addEventListener('click', () => go.click());
    paintPanels(); setRightTab(rightTab);

    // ───── Drop and paste ─────
    for (const target of [inputs, frame]) {
        target.addEventListener('dragover', event => { if (event.dataTransfer?.types?.includes('Files') && cap_(model) > 0) { event.preventDefault(); promptGroup.element.dataset.drop = 'true'; } });
        target.addEventListener('dragleave', () => { delete promptGroup.element.dataset.drop; });
        target.addEventListener('drop', event => { delete promptGroup.element.dataset.drop; if (event.dataTransfer?.files?.length) { event.preventDefault(); promptGroup.open(true); addFiles([...event.dataTransfer.files]); } });
    }
    listenDialogKey(prompt, 'paste', event => { const files = [...(event.clipboardData?.files || [])].filter(f => f.type.startsWith('image/')); if (files.length) { event.preventDefault(); addFiles(files); } });

    const say = text => { note.textContent = text || ''; if (alive) paintSlot(); };
    // Open folder: when the engine cannot show it (no desktop, or a remote engine: expected there, not a fault), the line
    // above Generate says so as a plain note and gives the folder on a line of its own, so the click is never silent;
    // opening it later clears that line. (The wording is the one the image history used before 0.6.0.)
    let folderNote = null;
    async function openFolder() {
        let answer;
        try { answer = await client.historyOpen?.(); } catch { answer = {opened: false}; }
        if (!alive) return;
        if (answer?.opened === false) {
            const sentence = t('The folder could not be opened automatically. Open it in a file manager.', '无法自动打开该文件夹，请在文件管理器中手动打开');
            const folder = typeof answer.folder === 'string' ? answer.folder : '';
            folderNote = {sentence, folder, text: folder ? `${sentence} ${folder}` : sentence};
            say(folderNote.text);
        } else if (folderNote && note.textContent === folderNote.text) { folderNote = null; say(''); }
    }
    // The folder note's two lines. Its path breaks only where it does not fit (its spaces are shown unbreakable and
    // copied as plain spaces, so a copied path still works).
    function folderParts() {
        const tx = el('span', 'fv-iv-slot-tx'); tx.append(el('span', 'fv-iv-folder-say', folderNote.sentence));
        if (folderNote.folder) tx.append(el('span', 'fv-iv-folder-path', folderNote.folder.replace(/ /g, '\u00a0')));
        tx.addEventListener('copy', event => {
            const text = String(globalThis.getSelection?.() || '').replace(/\u00a0/g, ' ');
            if (!text || !event.clipboardData) return;
            event.clipboardData.setData('text/plain', text); event.preventDefault();
        });
        return [icon('info'), tx];
    }
    const label = number => t(`Image ${number}`, `图 ${number}`);
    const subject = number => t(`Subject ${number}`, `主体 ${number}`);
    // Each model row says how many pictures it takes; the strip holds as many as the most generous one.
    // Until the engine answers, each model's usual reference count, so the strip has its final height from the first frame.
    const USUAL_CAPS = {'minimax-h3-image': 16, 'qwen-image-2.1-turbo': 4, 'moss-image-sft': 0};
    function cap_(m) { if (!info?.models) return USUAL_CAPS[m.id] || 0; const n = Number(info.models.find(r => r.id === m.id)?.max_images); return Number.isInteger(n) && n > 0 ? n : 0; }
    const most = () => Math.max(0, ...IMAGE_MODELS.map(cap_));
    const over = () => Math.max(0, references.length - cap_(model));
    function addFiles(files, card) {
        if (cap_(model) === 0) return;
        let skipped = 0;
        for (const file of files) {
            if (!file.type.startsWith('image/')) continue;
            if (references.length >= most()) { skipped++; continue; }
            const record = {file, url: URL.createObjectURL(file), card}; references.push(record);
            const probe = new Image(); probe.onload = () => { record.width = probe.naturalWidth; record.height = probe.naturalHeight; if (alive) render(); }; probe.src = record.url;
        }
        // The note stays until the strip changes again.
        notice.dataset.keep = String(skipped > 0);
        notice.textContent = skipped ? t(`At most ${most()}, ${skipped} more were not added.`, `最多 ${most()} 张，多出的 ${skipped} 张没有加入。`) : '';
        render();
    }
    async function useAsReference(file, first) {
        if (!file) return;
        try {
            const blob = await (await fetch(pictureURL(file))).blob(), name = file.slice(file.lastIndexOf('/') + 1);
            const record = {file: new File([blob], name, {type: blob.type || 'image/png'}), url: URL.createObjectURL(blob)};
            if (references.length >= most()) { URL.revokeObjectURL(record.url); return; }
            changeRefs(() => { if (first) references.unshift(record); else references.push(record); });
            const probe = new Image(); probe.onload = () => { record.width = probe.naturalWidth; record.height = probe.naturalHeight; if (alive) render(); }; probe.src = record.url;
            promptGroup.open(true); render(); prompt.focus();
        } catch { say(t('That picture could not be read.', '这张图读不了。')); }
    }
    // Any change to the pictures' order goes through here: the mentions in the prompt follow their pictures.
    function changeRefs(mutate) {
        const before = references.slice(); mutate();
        const caret = prompt.selectionStart ?? prompt.value.length, next = renumberMentions(prompt.value, before, references, caret);
        if (next.changed) {
            const focused = document.activeElement === prompt;
            prompt.value = next.text; if (focused) prompt.setSelectionRange(next.caret, next.caret);
        }
        mentions.paint();
    }
    // The engine reads <Picture N>; the page writes "@图N" / "@Image N" (and older prompts "图 N"). A prompt that names none gets them
    // listed, all but the sketches: the words around the description name those.
    function engineText(seed) {
        const swapped = mentionsToEngine(prompt.value, references.length).replace(/(?:图\s?|Image\s)(\d+)/g, (_, n) => `<Picture ${n}>`)
            .replace(/(?:主体\s?|Subject\s)(\d+)/g, (_, n) => `<Subject ${n}>`);
        // A sketch with mannequins takes the pose words (the other pictures listed in front when the text names none).
        const poseAt = references.findIndex((r, at) => r.sketch && r.pose && at < cap_(model));
        if (poseAt >= 0) {
            const rest = references.map((_, at) => at === poseAt || at >= cap_(model) ? '' : `<Picture ${at + 1}>`).filter(Boolean);
            return posePrompt(rest.length && !/<Picture \d+>/.test(swapped) ? rest.join(' ') + ' ' + swapped : swapped, poseAt + 1, references[poseAt].pose, seed);
        }
        const sketches = references.map((r, at) => r.sketch && at < cap_(model) ? at + 1 : 0).filter(Boolean);
        const others = references.map((_, at) => sketches.includes(at + 1) ? '' : `<Picture ${at + 1}>`).filter(Boolean);
        const listed = others.length && !/<Picture \d+>/.test(swapped) ? others.join(' ') + ' ' + swapped : swapped;
        return sketchPrompt(listed, sketches, model.id);
    }
    // What the rewrite sees: the model, the shape asked for (Image 1's own when it follows it), and the pictures sent.
    function enhanceScene() {
        const used = references.slice(0, cap_(model)), first = used[0];
        const divisor = (a, b) => b ? divisor(b, a % b) : a, d = first?.width ? divisor(first.width, first.height) : 0;
        const own = d && first.width / d < 10000 && first.height / d < 10000 ? `${first.width / d}:${first.height / d}` : 'adaptive';
        const wanted = ratio === 'same' || (used.length && !ratioChosen) ? 'same' : ratio;
        return {target: 'image', image_model: model.id, aspect: wanted === 'same' ? own : wanted, references: used.map(r => pictureId(r.file))};
    }
    // A reference opens in the image editor; the edited picture takes its place (marked), and the original is kept to put back.
    // The sketch canvas: the current frame, its long side at most 1536; a sketch opens again with its strokes.
    function sketchTarget() { const s = size(), k = Math.min(1, 1536 / Math.max(s.width, s.height)); return {width: Math.max(256, Math.round(s.width * k / 32) * 32), height: Math.max(256, Math.round(s.height * k / 32) * 32)}; }
    async function drawSketch(record = null) {
        if (cap_(model) === 0) { say(t(`${model.short} does not take reference pictures; choose the other model`, `${model.short} 不使用参考图，请换另一个模型`)); return; }
        if (!record && references.length >= most()) { say(t('The reference slots are full; remove one', '参考图已满，先移除一张')); return; }
        let outcome;
        try {
            const {openImageEditor} = await import('./image_editor.js');
            outcome = await openImageEditor({source: null, name: 'sketch.png', target: record ? {width: record.width, height: record.height} : sketchTarget(), edit: record?.edit || null,
                role: 'reference', tool: 'brush', mode: 'sketch', t, resolve: url => url, upload: async blob => client.upload(blob), imageGenClient: client});
        } catch { say(t('The sketch canvas cannot open right now.', '草图画布暂时打不开。')); return; }
        if (!alive || !outcome?.blob) return;
        const file = new File([outcome.blob], 'sketch.png', {type: outcome.blob.type || 'image/png'});
        if (record) {
            if (!references.includes(record)) return;
            URL.revokeObjectURL(record.url); Object.assign(record, {file, url: URL.createObjectURL(file), width: outcome.width, height: outcome.height, edit: outcome.edit || null, pose: outcome.pose || null}); render(); return;
        }
        const fresh = {file, url: URL.createObjectURL(file), width: outcome.width, height: outcome.height, edit: outcome.edit || null, sketch: true, pose: outcome.pose || null};
        changeRefs(() => references.unshift(fresh));
        // A sketch is made into a picture with H3 Image when it is there; a sketch with mannequins with Qwen-Image 2.1, on
        // which its words were settled (the user may still choose the other model).
        const best = IMAGE_MODELS.find(m => m.id === (fresh.pose ? 'qwen-image-2.1-turbo' : 'minimax-h3-image'));
        if (best && model.id !== best.id && modelState(info, best.id).state === 'ready') chooseModel(best);
        // The box is left to say what the sketch shows (its placeholder asks for that while it is empty); the words around
        // the description already say the sketch gives the layout.
        promptGroup.open(true); render(); prompt.focus({preventScroll: true});
    }
    async function editReference(record) {
        if (record.sketch) { void drawSketch(record); return; }
        let outcome, loadFailed = false;
        try {
            const {openImageEditor} = await import('./image_editor.js').catch(error => { loadFailed = true; throw error; });
            outcome = await openImageEditor({source: record.original?.url || record.url, name: record.original?.file?.name || record.file?.name || 'reference.png', edit: record.edit || null,
                role: 'reference', t, resolve: url => url, upload: async blob => client.upload(blob), imageGenClient: client});
        } catch { say(loadFailed ? t('The image editor did not load. Reload the page and try again.', '图片编辑器没有加载出来。请刷新页面后再试。') : t('This picture could not be read. Choose another picture.', '这张图无法读取。请重新选择一张。')); return; }
        if (!alive || !outcome || !references.includes(record)) return;
        if (outcome.restore) { restoreReference(record); return; }
        if (!outcome.blob) return;
        const edited = new File([outcome.blob], outcome.name || 'reference-edit.png', {type: outcome.blob.type || 'image/png'});
        if (record.original) URL.revokeObjectURL(record.url);
        else record.original = {file: record.file, url: record.url, width: record.width, height: record.height};
        Object.assign(record, {file: edited, url: URL.createObjectURL(edited), width: outcome.width, height: outcome.height, edit: outcome.edit || null});
        render();
    }
    function restoreReference(record) {
        if (!record.original) return;
        URL.revokeObjectURL(record.url);
        Object.assign(record, record.original); delete record.original; delete record.edit;
        render();
    }

    function drawReferences() {
        const cells = references.map((r, at) => {
            const cell = el('span', 'fv-image-reference fv-iv-ref'), use = el('button', 'fv-image-reference-use'), x = el('button', 'fv-image-reference-remove');
            cell.dataset.over = String(at >= cap_(model)); cell.dataset.edited = String(!!r.original);
            use.type = x.type = 'button';
            tip(use, t('Edit', '编辑'), r.original ? t(`${label(at + 1)}, edited. Crop or retouch it again`, `${label(at + 1)}（已编辑）：继续裁剪或修图`) : t(`Crop or retouch ${label(at + 1)}; it stays in its place`, `裁剪或修图${label(at + 1)}，改完放回原位`));
            tip(x, t('Remove', '移除'), cell.dataset.over === 'true' ? t(`${model.short} takes ${cap_(model)}; remove this one`, `${model.short} 最多 ${cap_(model)} 张，请移除这张`) : t('Take this picture out', '去掉这张参考图'));
            x.append(icon('x'));
            const i = el('img'); i.src = r.url; i.alt = ''; i.draggable = false; use.append(i, el('b', '', String(at + 1)));
            use.onclick = () => void editReference(r);
            x.onclick = () => { notice.dataset.keep = 'false'; URL.revokeObjectURL(r.url); if (r.original) URL.revokeObjectURL(r.original.url); changeRefs(() => references.splice(references.indexOf(r), 1)); render(); };
            cell.append(use, x);
            if (r.sketch) { const badge = el('i', 'fv-iv-ref-sketch', t('Sketch', '草图')); cell.append(badge); cell.dataset.sketch = 'true'; }
            // Edited here: a mark, and in its place on pointing the way back to the original.
            if (r.original) {
                const mark = el('i', 'fv-iv-ref-mark'); mark.append(icon('edit'));
                const back = el('button', 'fv-iv-ref-restore'); back.type = 'button'; back.append(icon('restore'));
                tip(back, t('Restore original', '恢复原图'), t('Put back the picture as it was before editing', '换回编辑前的原图'));
                back.onclick = () => restoreReference(r);
                cell.append(mark, back);
            }
            return cell;
        });
        const room = cap_(model) > 0 && references.length < most() && (references.length < cap_(model) || over() === 0);
        // With pictures in the strip, one more small tile adds the next; with none, the tool row's button is the way in.
        if (room && references.length) {
            const add = el('button', 'fv-image-add fv-iv-ref fv-iv-ref-add'); add.type = 'button'; add.append(icon('plus'));
            tip(add, t('Add a reference picture', '添加参考图'), t('From works, the library or this computer; or drop or paste', '从作品、素材库或电脑选择；也可拖入或粘贴'));
            add.onclick = () => openRefMenu(add);
            cells.push(add);
        }
        strip.replaceChildren(...cells);
        strip.hidden = !cells.length;
        // No running count; only a full strip says so.
        refCount.textContent = cap_(model) && references.length >= cap_(model) ? t('Full', '已满') : '';
        refAdd.setAttribute('aria-disabled', String(!room));
        tip(refAdd, t('Reference pictures', '参考图'), room ? t('Add from works, the library or this computer; or drop or paste', '从作品、素材库或电脑添加；也可拖入或粘贴') : '');
        if (!room) refAdd.dataset.fvTipWhy = cap_(model) === 0 ? t(`${model.short} does not take reference pictures; choose the other model`, `${model.short} 不使用参考图，请换另一个模型`) : t('The reference slots are full; remove one', '参考图已满，先移除一张'); else delete refAdd.dataset.fvTipWhy;
        refNote.textContent = info && cap_(model) === 0 && references.length ? t(`${model.short} does not take reference pictures.`, `${model.short} 不使用参考图。`) : '';
        refNote.hidden = !refNote.textContent;
        refHeadAdd.hidden = true;
    }
    function openRefMenu(anchor) {
        if (refAdd.getAttribute('aria-disabled') === 'true' && anchor === refAdd) return;
        if (menu?.box.isConnected) { menu.close(); return; }
        menu = openReferenceMenu({api, viewURL: pictureURL, anchor: () => anchor.isConnected ? anchor : refAdd, host: dialog, prefer: 'up', onComputer: () => pick.click(),
            onPick: (file, id) => { addFiles([file], id); }, state: () => ({used: new Set(references.map(r => r.card)), full: references.length >= most()})});
    }
    pick.onchange = () => { addFiles([...pick.files]); pick.value = ''; };
    // The reference pictures the user had when the view was left come back as they were (marks, sketches, originals).
    if (keptDraft?.references?.length) {
        for (const kept of keptDraft.references) {
            const record = {...kept, url: URL.createObjectURL(kept.file)};
            if (kept.original) record.original = {...kept.original, url: URL.createObjectURL(kept.original.file)};
            references.push(record);
        }
    }
    if (keptDraft && Number.isInteger(keptDraft.caret)) { try { prompt.setSelectionRange(keptDraft.caret, keptDraft.caret); } catch { /* the box may not take a caret yet */ } }
    if (keptDraft?.prompt) queueMicrotask(() => mentions.paint());

    // ───── Size ─────
    const firstRef = () => references[0]?.width ? references[0] : null;
    const shapeOf = () => ratio === 'same' ? (firstRef() || '1:1') : ratio;
    function size() { return sizeFor(model.id, ratioValue(shapeOf()), area); }
    function keepSize() {
        sizes[model.id] = area; if (ratioChosen && ratio !== 'same') sizes.ratio = ratio; sizes.locked = locked;
        if (ratio !== 'same' && !RATIOS.includes(ratio)) sizes.custom = ratio;
        writeStore(SIZE_KEY, sizes);
        tier = area > RANGES[model.id].presets.standard * 1.02 ? 'high' : 'standard';
    }
    function changeSize(patch) {
        if (patch.ratio) {
            ratio = patch.ratio; ratioChosen = true;
            const limits = areaRange(model.id, ratioValue(shapeOf())); area = Math.min(limits.max, Math.max(limits.min, area));
        }
        if (patch.area != null) area = patch.area;
        if (patch.locked != null) locked = patch.locked;
        // Typed width × height: with the lock the shape stays and the area follows; without it the shape follows too
        // (a typed shape that is one of the usual ones becomes that one).
        if (patch.dims) {
            const {width, height} = patch.dims;
            if (!locked && width > 0 && height > 0) { ratio = RATIOS.find(r => Math.abs(ratioValue(r) - width / height) < .002) || `${width}:${height}`; ratioChosen = true; }
            const next = sizeFor(model.id, ratioValue(shapeOf()), width * height); area = next.width * next.height;
        }
        startDraft(); keepSize(); render(); warmUp('full');
    }
    // Another model keeps the shape; a quick point stays the same quick point, any other size is kept inside the new range.
    function chooseModel(m) {
        if (m.id === model.id) return;
        const at = presetAt(model.id, area);
        model = m; remember(m.id);
        const limits = areaRange(model.id, ratioValue(shapeOf()));
        area = at ? RANGES[model.id].presets[at] : sizes[model.id] || RANGES[model.id].presets.standard;
        area = Math.min(limits.max, Math.max(limits.min, area));
        if (!missing() && !loading()) pendingGenerate = false;
        keepSize(); render(); warmUp('full');
    }
    function applyPreset(preset) {
        const m = IMAGE_MODELS.find(x => x.id === preset.model); if (m) chooseModel(m);
        if (preset.ratio) { ratio = preset.ratio; ratioChosen = true; }
        if (preset.area) area = preset.area;
        if (preset.ratio || preset.area) startDraft();
        seedInput.value = preset.seed ?? '';
        if (preset.prompt) { prompt.value = preset.prompt; prompt.dispatchEvent(new Event('input', {bubbles: true})); }
        keepSize(); render();
    }

    // ───── Drawing ─────
    const visible = () => filterRows(rows.filter(r => !isGone(r.file)), {filter, query, sort}, meta);
    const labelOf = file => { const m = meta.get(file); return [IMAGE_MODELS.find(x => x.id === m?.model)?.short, m?.width ? `${m.width} × ${m.height}` : '', m?.seconds ? t(`${m.seconds} s`, `${m.seconds} 秒`) : ''].filter(Boolean).join(' · ') || t('Picture', '图片'); };
    const runRow = () => running?.id ? jobs.get(running.id) : null;

    function paintSlot() {
        // One fixed-height line above Generate: the most pressing thing there is to say.
        const s = modelState(info, model.id), tooMany = over() > 0;
        let kind = '', parts = [];
        if (note.textContent && folderNote && note.textContent === folderNote.text) { kind = 'note'; parts = folderParts(); }
        else if (note.textContent) { kind = note.dataset.kind === 'busy' ? 'info' : 'warn'; parts = [icon(kind === 'info' ? 'clock' : 'warn'), el('span', 'fv-iv-slot-tx', note.textContent)]; }
        else if (tooMany) {
            kind = 'warn';
            parts = [icon('warn'), el('span', 'fv-iv-slot-tx', cap_(model) === 0 ? t(`${model.short} does not take reference pictures. Remove ${over()}.`, `${model.short} 暂不支持参考图，请移除 ${over()} 张。`) : t(`${model.short} takes at most ${cap_(model)}. Remove ${over()}.`, `${model.short} 最多 ${cap_(model)} 张，请移除 ${over()} 张。`))];
        } else if (notice.dataset.keep === 'true' && notice.textContent) { kind = 'warn'; parts = [icon('warn'), el('span', 'fv-iv-slot-tx', notice.textContent)]; }
        else if (brokenMentions(prompt.value, references.length)) {
            kind = 'warn'; parts = [icon('warn'), el('span', 'fv-iv-slot-tx', t('A picture named in the prompt was removed; choose it again with @', '描述里引用的参考图已移除，请用 @ 重新选择'))];
        }
        else if (s.state === 'failed') {
            kind = 'warn'; parts = [icon('warn'), el('span', 'fv-iv-slot-tx', t(`${model.short} download failed${s.reason ? ': ' + s.reason.replace(/[.。]$/, '') : ''}. Retry to continue; if it keeps failing, report the problem.`, `${model.short} 下载失败${s.reason ? '：' + s.reason.replace(/[.。]$/, '') : ''}。请重试以继续下载；仍然失败时，请提交问题。`))];
        } else if (s.state === 'downloading' && s.paused) {
            // What the download waits for, and that the model row's Continue download runs it now.
            kind = 'info'; parts = [icon('clock'), el('span', 'fv-iv-slot-tx', t(`${model.short}: ${pausedText(s)}`, `${model.short}：${pausedText(s)}`))];
        } else if (s.state === 'downloading') {
            kind = 'info'; parts = [icon(s.unknown ? 'clock' : 'down'), el('span', 'fv-iv-slot-tx', s.verifying ? (pendingGenerate ? t(`Verifying ${model.short}; it generates when done`, `正在校验 ${model.short}，完成后自动生成`) : t(`Verifying ${model.short}`, `正在校验 ${model.short}`))
                : pendingGenerate ? t(`Installing ${model.short}; it generates when done`, `正在安装 ${model.short}，完成后自动生成`) : t(`Installing ${model.short}`, `正在安装 ${model.short}`))];
        } else if (s.state === 'missing') {
            kind = 'info'; parts = [icon('cloud'), el('span', 'fv-iv-slot-tx', t(`${model.short} is not installed; Generate installs it first`, `${model.short} 未安装，点生成会先安装`))];
        } else if (sizeWarning()) { kind = 'warn'; parts = [icon('warn'), el('span', 'fv-iv-slot-tx', sizeWarning())]; }
        else if (fitted && fitted.file === shownFile) { kind = 'info'; parts = [icon('info'), el('span', 'fv-iv-slot-tx', fittedNote(fitted, t, !!info?.unified_memory))]; }
        else parts = running ? [el('span', 'fv-iv-slot-tx', t('Generating; you can leave this view', '正在生成，可以离开这个视图'))] : [];
        slot.dataset.kind = kind; slot.replaceChildren(...parts);
        // A folder that could not be opened is read (and copied) in full: that line takes the height it needs.
        slot.toggleAttribute('data-whole', kind === 'note');
        paintStatus();
    }

    // A size above what this computer has shown it can make: said before Generate, with the size it will fall back to.
    function sizeWarning() {
        const limit = machineLimit(model.id, info, fittedAreas[model.id]), now = size();
        if (!Number.isFinite(limit.area) || now.width * now.height <= limit.area * 1.02) return '';
        const smaller = sizeFor(model.id, ratioValue(shapeOf()), limit.area);
        return t(`May not fit this computer; it would be made at about ${sizeText(smaller)}`, `这台电脑可能放不下，会自动改为约 ${sizeText(smaller)}`);
    }
    // The left panel's first screen is whole: groups that would be cut at its bottom fold for this screen only
    // (the saved choice stays), least needed first; more room opens them again. A group the user opens stays open.
    // Order: the groups least needed at first, then a shorter prompt box (down to three lines), then size and model.
    const FIT_ORDER = [];
    const PROMPT_MIN = 76;
    function fitLeft() {
        if (!alive || !scroll.clientHeight || left.dataset.collapsed === 'true' || matchMedia('(max-width: 719px)').matches) return;
        // While a long prompt is being typed the box is taller for a moment; the panel fits again when it closes.
        if (prompt.dataset.grown === 'true') return;
        scroll.classList.add('fv-iv-instant');
        for (const group of FIT_ORDER) if (group.element.dataset.fitted === 'true') { group.open(true, false); delete group.element.dataset.fitted; }
        if (prompt.dataset.fitted === 'true') { prompt.style.height = ''; delete prompt.dataset.fitted; }
        const over = () => scroll.scrollHeight - scroll.clientHeight;
        for (let step of FIT_ORDER) {
            // A few pixels are the focus rings' room, not content.
            if (over() <= 8) break;
            if (step === 'prompt') {
                const now = prompt.offsetHeight, target = Math.max(PROMPT_MIN, now - over());
                if (target < now) { prompt.style.height = `${target}px`; prompt.dataset.fitted = 'true'; }
                continue;
            }
            if (typeof step === 'function') step = step();
            // A group the user opened on this screen stays open; scrolling is then their choice.
            if (step && step.isOpen() && step.element.dataset.pinned !== 'true') { step.open(false, false); step.element.dataset.fitted = 'true'; }
        }
        void scroll.offsetHeight; scroll.classList.remove('fv-iv-instant');
    }
    let fitFrame = 0;
    const fitSoon = () => { cancelAnimationFrame(fitFrame); fitFrame = requestAnimationFrame(() => { fitFrame = 0; fitLeft(); }); };
    // Blur costs most while content moves under it: a panel being scrolled turns solid until it rests.
    for (const panel of [left, column]) {
        let rest = 0;
        panel.addEventListener('scroll', () => { panel.dataset.scrolling = 'true'; clearTimeout(rest); rest = setTimeout(() => { delete panel.dataset.scrolling; }, 160); }, {capture: true, passive: true});
    }
    const fitWatch = typeof ResizeObserver === 'function' ? new ResizeObserver(fitSoon) : null;
    fitWatch?.observe(scroll); fitWatch?.observe(inputs);

    function paintGo() {
        const s = modelState(info, model.id), busy = !!running, now = size(), est = estimateSeconds(meta.samples(), model.id, now.width * now.height);
        go.className = 'fv-image-go fv-iv-go ' + (failure ? 'fv-image-go-quiet' : 'fv-primary');
        go.disabled = false; go.dataset.state = busy ? 'running' : s.state === 'downloading' ? 'downloading' : (s.state === 'missing' || s.state === 'failed') ? 'install' : 'ready';
        goFill.style.setProperty('--p', String(s.state === 'downloading' ? s.fraction : 0));
        delete go.dataset.fvTipWhy;
        if (busy) {
            goLabel.textContent = t('Stop', '停止'); goSub.textContent = '';
            tip(go, t('Stop', '停止'), t('Cancel this picture; asks first', '取消这一张，会先确认'));
        } else if (s.state === 'downloading') {
            goLabel.textContent = s.verifying ? verifyText(s) : s.paused ? t(`Paused ${Math.floor(s.fraction * 100)}%`, `已暂停 ${Math.floor(s.fraction * 100)}%`)
                : t(`Installing ${Math.floor(s.fraction * 100)}%`, `正在安装 ${Math.floor(s.fraction * 100)}%`);
            goSub.textContent = pendingGenerate ? t('then generates', '完成后生成') : '';
            tip(go, pendingGenerate ? t('Generates when installed', '安装完自动生成') : t('Generate when installed', '安装完再生成'), pendingGenerate ? t('Press to not generate after it', '再按一次取消自动生成') : t('Press to generate as soon as it is installed', '按下后，安装完成即自动生成'));
        } else if (s.state === 'missing' || s.state === 'failed') {
            goLabel.textContent = t('Install and generate', '安装并生成'); goSub.textContent = s.total ? t(`about ${(s.total / 1e9).toFixed(1)} GB`, `约 ${(s.total / 1e9).toFixed(1)} GB`) : '';
            tip(go, t('Install and generate', '安装并生成'), t(`Downloads ${model.short}, then makes the picture`, `先下载 ${model.short}，再生成图片`), `${mod}+Enter`);
        } else {
            // The size is said once, on its own chip; Generate says only how long it may take.
            goLabel.textContent = t('Generate', '生成'); goSub.textContent = est ? t(`about ${est} s`, `约 ${est} 秒`) : '';
            tip(go, t('Generate', '生成'), t(`Makes a ${sizeText(now)} picture`, `生成一张 ${sizeText(now)} 的图片`), `${mod}+Enter`);
            if (!prompt.value.trim() && !references.length) go.dataset.fvTipWhy = t('Describe the picture first', '先写一句描述');
        }
        // With no time (or size) to say beside it, the word sits alone in the middle of the key.
        go.dataset.eta = String(!!goSub.textContent);
        railGen.replaceChildren(icon(busy ? 'stop' : 'gen')); tip(railGen, goLabel.textContent, go.dataset.fvTipUse, `${mod}+Enter`);
    }

    // The works picked, in the order picked, that are still there.
    const pickedFiles = () => [...picked].filter(f => rows.some(r => r.file === f) && !isGone(f));
    const pickedKey = () => pickedFiles().join('|');
    // Compare follows the selection: two or more picked works are compared (the first four); fewer, and the stage shows
    // one picture again. It wins over the next picture's empty frame; a picture being made moves to the floating window.
    // Back to one picture: the one still picked, or with none picked the one viewed before the compare.
    function syncCompare() {
        const files = pickedFiles(), key = files.join('|');
        if (files.length < 2) {
            if (compare) { const back = files[0] || beforeCompare; if (back && rows.some(r => r.file === back) && !isGone(back)) shownFile = back; }
            soloKey = ''; compare = null; return;
        }
        if (key === soloKey) { compare = null; return; }
        if (!compare) { beforeCompare = shownFile; if (running && !browsing) leaveRun(); }
        compare = {files: files.slice(0, COMPARE_MAX), total: files.length};
    }
    function render() {
        if (!alive) return;
        syncCompare();
        // Generate fills the draft frame in place; when the picture is done the stage shows it.
        if (running && !browsing) { draft = false; draftPreview = null; }
        enhancer.element.dataset.off = String(enhancerOff());
        cards.paint(info, model.id, pendingGenerate);
        loraRow.paint(model.id);
        drawReferences();
        const tooMany = over() > 0, busy = !!running;
        // No words inside the empty box (the group says what it is for), except beside a sketch: what it shows is known only
        // from the words (a stick figure is a person when the words say so), so the empty box asks for them.
        prompt.placeholder = references.some((r, at) => r.sketch && r.pose && at < cap_(model))
            ? t('Describe the people, their clothes and the place, for example: a young woman in a beige trench coat on a pier at dusk', '写下人物、衣着和地点，例如：穿米色风衣的年轻女性，站在黄昏的海边栈桥上')
            : references.some((r, at) => r.sketch && at < cap_(model))
                ? t('Write what the sketch shows, for example: a person raising both hands', '写下草图画的是什么，例如：一个人举起双手') : '';
        prompt.setAttribute('aria-label', references.length ? t('What to make or change', '要画什么或要改什么') : t('Describe the picture', '描述画面'));
        // Said only near the limit.
        promptCount.textContent = prompt.value.length >= 1600 ? `${prompt.value.length} / 2000` : '';
        // The count shows only near the limit; the row is fitted again when it comes or goes.
        const counted = promptCount.hidden; promptCount.hidden = prompt.value.length < 1600;
        if (counted !== promptCount.hidden) fitPromptBar();
        if (tooMany) notice.dataset.keep = 'false';
        if (!tooMany && notice.dataset.keep !== 'true') notice.textContent = '';
        if (ratio === 'same' && !references.length) ratio = sizes.ratio && sizes.ratio !== 'same' ? sizes.ratio : '1:1';
        if (!ratioChosen && references.length && firstRef()) ratio = 'same';
        const sent = size();
        const limit = machineLimit(model.id, info, fittedAreas[model.id]);
        const custom = ratio !== 'same' && !RATIOS.includes(ratio) ? ratio : sizes.custom || null;
        sizer.paint({model: model.id, ratio, ref: references.length && firstRef() ? {url: references[0].url, ratio: ratioValue(firstRef())} : null, custom,
            area, size: sent, locked, limit, ratioValue: ratioValue(shapeOf())});
        seedInput.disabled = busy;
        advGroup.meta.textContent = seedInput.value ? t(`Seed ${seedInput.value}`, `种子 ${seedInput.value}`) : '';
        paintGo(); paintSlot();
        drawStage();
        const row = runRow(); paintRunTile(row?.percent || 0);
    }

    // While a picture is being made the user may look at other works: the generation leaves the stage for the floating
    // window and comes back from the works' first tile, the rail or the queue.
    let browsing = false;
    function stageState() { return running && !browsing ? 'running' : (draft || draftPreview) && !compare ? 'draft' : failure && !browsing ? 'failed' : shownFile ? 'done' : running ? 'running' : historyLoaded ? 'blank' : 'waiting'; }
    // Setting up the next picture: called for each change of shape or size. A picture being made stays on the stage.
    function startDraft() { if (running && !browsing) return; draft = true; draftPreview = null; }
    function leaveRun() {
        browsing = true;
        window.dispatchEvent(new CustomEvent('freevideo-task-stage-leave', {detail: {view: 'image', element: canvas, id: running?.id || null}}));
    }
    function returnToRun() {
        if (!running) return;
        browsing = false; soloKey = pickedKey(); compare = null;
        window.dispatchEvent(new CustomEvent('freevideo-task-stage-return', {detail: {view: 'image', element: canvas, id: running?.id || null}}));
        render(); drawWorks();
    }
    function drawStage() {
        const state = stageState();
        frame.dataset.state = stage.dataset.state = state; paintCover();
        frame.dataset.loading = String(state === 'waiting' || (state === 'done' && picture.dataset.loaded !== 'true'));
        picture.hidden = state !== 'done'; stepFrame.hidden = state !== 'running' || !stepURL; block.hidden = state !== 'running'; failHost.hidden = state !== 'failed'; grid.hidden = state !== 'blank';
        sceneHost.hidden = state !== 'running'; draftNote.hidden = state !== 'draft';
        // While running the caption keeps its room unseen, so a step picture and the finished one sit in the same place.
        // The caption row also waits for the picture's shape: it takes the frame's width, so it shows at its final one.
        // A work whose file is gone keeps its row (without the actions that need the picture), so it can still be deleted here.
        const gone = picture.dataset.file === shownFile && picture.dataset.missing === 'true'; cap.dataset.missing = String(gone);
        cap.dataset.hidden = String(state !== 'done' || (!gone && !meta.get(shownFile)?.width && !(picture.dataset.file === shownFile && picture.naturalWidth)));
        const row = runRow(), asked = row?.meta?.width ? row.meta : running ? size() : failure?.sent || null;
        const shown = meta.get(shownFile);
        const next = state === 'draft' ? draftPreview || size() : null;
        const ar = state === 'done' ? (picture.naturalWidth && picture.dataset.file === shownFile ? picture.naturalWidth / picture.naturalHeight : shown?.width ? shown.width / shown.height : 1)
            : next ? next.width / next.height : asked?.width ? asked.width / asked.height : 1;
        shot.style.setProperty('--ar', String(ar));
        if (next) {
            draftSize.textContent = sizeText(next);
            const est = estimateSeconds(meta.samples(), model.id, next.width * next.height);
            draftHint.textContent = draftPreview ? '' : [t('Next picture', '下一张'), est ? t(`about ${est} s`, `约 ${est} 秒`) : ''].filter(Boolean).join(' · ');
        }
        if (state === 'done') {
            const mine = lastReport?.file === shownFile ? lastReport.id : null;
            if (doneReport.dataset.id !== String(mine)) {
                doneReport.dataset.id = String(mine); doneReport.replaceChildren();
                if (mine) doneReport.append(imageReportBar(doneReport, mine, t, {productVersion: () => null, quiet: true, fetchApi: (path, options) => api.fetchApi(path, options)}).bar);
            }
            const url = pictureURL(shownFile);
            // A picture shows whole once decoded (never painted half-loaded); until then its frame holds a placeholder.
            if (picture.dataset.file !== shownFile) {
                picture.dataset.file = shownFile; picture.dataset.loaded = 'false'; delete picture.dataset.missing; picture.src = url; mediaQueue().open(url, picture);
                if (picture.complete && picture.naturalWidth) picture.dataset.loaded = 'true';
                // A picture of unknown shape: its size is read from its first bytes (long before it has loaded), so the
                // frame and the caption row take their final size then, and the view is ready with them.
                else if (!shown?.width) {
                    const file = shownFile, early = () => {
                        if (!alive || picture.dataset.file !== file || picture.dataset.loaded === 'true') return;
                        if (picture.naturalWidth) { meta.set(file, {width: picture.naturalWidth, height: picture.naturalHeight}); drawStage(); markReady('picture'); return; }
                        requestAnimationFrame(early);
                    };
                    requestAnimationFrame(early);
                }
            }
            save.href = url; save.setAttribute('download', shownFile.slice(shownFile.lastIndexOf('/') + 1));
            const full = references.length >= most() || cap_(model) === 0;
            editThis.disabled = asReference.disabled = full;
            const why = cap_(model) === 0 ? t(`${model.short} takes no references; choose the other model`, `${model.short} 不用参考图，请换另一个模型`) : t('The reference slots are full; remove one', '参考图已满，先移除一张');
            tip(editThis, t('Edit', '编辑'), t('Make a new version: it becomes Image 1', '以这张为图 1，描述要改的地方'));
            tip(asReference, t('Use as reference', '用作参考图'), t('Add it to the reference pictures', '加入参考图'));
            for (const b of [editThis, asReference]) { if (full) b.dataset.fvTipWhy = why; else delete b.dataset.fvTipWhy; }
            const fav = meta.isFav(shownFile); favBtn.setAttribute('aria-pressed', String(fav));
            tip(favBtn, fav ? t('Remove star', '取消收藏') : t('Star', '收藏'), fav ? t('Take it out of favourites', '移出收藏') : t('Keep it in favourites', '加入收藏'), 'F');
            const known = promptFor(shownFile); usePromptItem.hidden = !known; copyInline.hidden = useInline.hidden = !known;
            // The line under the picture is about the picture shown: the model that made it, its size, its shape and the time it took.
            const width = picture.naturalWidth && picture.dataset.file === shownFile ? picture.naturalWidth : shown?.width || 0, height = picture.naturalWidth && picture.dataset.file === shownFile ? picture.naturalHeight : shown?.height || 0;
            const madeBy = IMAGE_MODELS.find(m => m.id === (shown?.model || rows.find(r => r.file === shownFile)?.model))?.name;
            infoText.textContent = [madeBy, width ? `${width} × ${height}` : '', width ? pictureShape(width, height) : '',
                shown?.seconds ? t(`${shown.seconds} s`, `${shown.seconds} 秒`) : started ? t(`${started} s`, `${started} 秒`) : ''].filter(Boolean).join(' · ');
        }
        comparer.paint(compare);
        stage.dataset.mode = compare ? 'compare' : 'single';
        if (tallQuery.matches) fitTallSoon();
        if (rightTab === 'info' && infoPop.dataset.file !== shownFile) { infoPop.dataset.file = shownFile; paintInfo(); }
        tabWorksN.textContent = '';
    }
    picture.onload = () => {
        // Its real shape is kept, so the next time this picture opens its frame is the right size from the first frame.
        picture.dataset.loaded = 'true'; frame.dataset.loading = 'false';
        const file = picture.dataset.file, known = meta.get(file);
        if (file && picture.naturalWidth && (!known?.width || known.width * picture.naturalHeight !== known.height * picture.naturalWidth))
            meta.set(file, {width: picture.naturalWidth, height: picture.naturalHeight});
        markReady('picture'); if (!alive) return; drawStage();
    };
    // A click on the shown picture opens it large; ← → there step through the works and the stage follows.
    let viewer = null;
    picture.addEventListener('click', () => {
        if (stageState() !== 'done' || !shownFile || viewer?.isOpen()) return;
        viewer = openPictureViewer({host: body, url: pictureURL(shownFile), onClose: () => { viewer = null; },
            step: by => { const before = shownFile; step(by); return shownFile !== before ? pictureURL(shownFile) : ''; }});
    });
    // A picture that can no longer be read (its file gone) leaves a quiet empty frame, never the browser's broken-image mark.
    picture.onerror = () => { picture.dataset.loaded = 'true'; picture.dataset.missing = 'true'; frame.dataset.loading = 'false'; markReady('picture'); if (alive) drawStage(); };

    function paintRunTile(fraction) {
        if (!running && browsing) browsing = false;
        for (const tile of body.querySelectorAll('.fv-iv-tile-run,.fv-iv-rail-run')) tile.style.setProperty('--p', String(fraction));
        meter.style.setProperty('--p', String(fraction));
    }

    function drawWorks() {
        if (!alive) return;
        const list = visible();
        if (selecting) for (const f of [...picked]) if (!rows.some(r => r.file === f) || isGone(f)) picked.delete(f);
        const live = rows.filter(r => !isGone(r.file)), row = runRow();
        works.paint({rows: list, groups: groupRows(list, meta, sort), running: running ? {percent: row?.percent || 0, preview: row?.meta?.preview || ''} : null,
            shownFile, selecting, picked, filter, query, sort, counts: filterCounts(live, meta), total: live.length, trash: trash.list(), view, meta,
            loading: !historyLoaded && !rows.length, holdSeconds: trash.holdMs / 1000, allFiles: new Set(rows.map(r => r.file))});
        const runThumb = running ? (() => { const b = el('button', 'fv-iv-rail-thumb fv-iv-rail-run'); b.type = 'button'; b.style.setProperty('--p', String(row?.percent || 0)); b.append(el('i', 'fv-iv-ring'));
            tip(b, t('Generating', '正在生成'), t('Show its progress', '查看进度')); b.addEventListener('click', () => returnToRun()); return b; })() : null;
        railThumbs.replaceChildren(...(runThumb ? [runThumb] : []), ...live.slice(0, runThumb ? 5 : 6).map(r => { const b = el('button', 'fv-iv-rail-thumb'); b.type = 'button'; const i = el('img'); i.alt = ''; i.draggable = false; i.src = thumbURL(r.file); b.append(i); b.classList.toggle('fv-iv-current', r.file === shownFile); tip(b, t('Show', '显示'), t('Put it on the stage', '在中间显示')); b.addEventListener('click', () => showFile(r.file)); return b; }));
    }
    function showFile(file) { if (!file) return; if (running && !browsing) leaveRun(); draft = false; draftPreview = null; shownFile = file; if (historyLoaded) queueMicrotask(rememberView); started = 0; if (!running) failure = null; compare = null; render(); drawWorks(); }
    // ← → step through the works shown (not while comparing); the works column follows without jumping.
    function step(by) {
        if (compare) return; const list = visible(), at = list.findIndex(r => r.file === shownFile); const target = list[at + by]; if (!target) return;
        showFile(target.file);
        works.element.querySelector(`.fv-iv-tile[data-file="${CSS.escape(target.file)}"]`)?.scrollIntoView({block: 'nearest'});
    }
    async function loadWorks() {
        try {
            rows = await client.history(); meta.reload(); historyLoaded = true;
            if (alive) {
                if (shownFile && (!rows.some(r => r.file === shownFile) || isGone(shownFile)) && !running) shownFile = visible()[0]?.file || '';
                if (!shownFile && !running && !failure) shownFile = visible()[0]?.file || '';
                drawWorks(); render(); rememberView();
            }
        } catch { historyLoaded = true; if (alive) { drawWorks(); render(); } }
        markReady('history');
    }
    function rememberView() { keepView({shownFile, rows: rows.slice(0, 120), scroll: works.element.querySelector('.fv-iv-works-body')?.scrollTop || 0,
        info: info?.models ? {models: info.models, sizes: info.sizes, max_images: info.max_images, ...(info.unified_memory ? {unified_memory: true} : {})} : viewCache?.info || null}); }
    // Delete goes to recently deleted: gone from the works at once, with nothing said; it is put back from recently deleted
    // (or with Ctrl+Z while it is still held there).
    function deleteFiles(files) {
        const list = files.filter(Boolean); if (!list.length) return;
        // The engine's key is fetched now, so a delete sent while the page goes away needs one request only.
        void deleter.info?.().catch(() => {});
        trash.remove(list.map(f => rows.find(r => r.file === f) || {file: f, name: f.slice(f.lastIndexOf('/') + 1)}));
        announce('freevideo-works-removed', list, {undoable: true});
        const mine = undoNote = {files: list, at: Date.now()};
        setTimeout(() => { if (undoNote === mine) undoNote = null; }, trash.holdMs);
        if (list.includes(shownFile)) { const rest = visible(); shownFile = rest[0]?.file || ''; }
        picked.clear(); selecting = false; drawWorks(); render();
    }

    // Installing from the page: the engine fetches the model; the page watches until it is ready, then generates if asked to.
    async function install() {
        const mine = model.id;
        try { info = await client.packInstall(mine); } catch (error) {
            // A failed download says so in its own words; the generation sentences do not describe it.
            say(error?.code === 'connection_lost'
                ? t('The download did not start: FreeVideo could not be reached. Start FreeVideo again, then try again.', '下载没有开始：连不上 FreeVideo。请重新启动 FreeVideo 后再试。')
                : error?.code === 'disk_full'
                    ? t('The download did not start: the disk is full. Free some space, then download again.', '下载没有开始：磁盘空间不够。空出空间后再下载。')
                : ['no_memory', 'pack_missing', 'model_unreadable'].includes(error?.code)
                    ? t('The download did not start: ', '下载没有开始：') + imageGenError(error.code, info, t)
                    : t('The download did not start. Try again; if it keeps failing, report the problem.', '下载没有开始。请重试；仍然失败请提交问题。')); return;
        }
        render();
        while (alive && modelState(info, mine).state === 'downloading') {
            await new Promise(r => setTimeout(r, 1500)); try { info = await client.info(); } catch { /* asked again */ } render();
        }
        if (alive && pendingGenerate && model.id === mine && modelState(info, mine).state === 'ready') { pendingGenerate = false; render(); if (!running) void generate(); }
    }
    async function pauseInstall(id) {
        try { info = await client.packCancel?.(id); info = await client.info(); } catch { /* the next look shows it */ }
        if (id === model.id) pendingGenerate = false;
        render();
    }
    // Over the model's limit, Generate stays solid: a click flashes the note and moves to the first greyed picture's remove.
    function blockOver() {
        slot.classList.remove('fv-image-notice-flash'); void slot.offsetWidth; slot.classList.add('fv-image-notice-flash');
        strip.querySelector('.fv-image-reference[data-over=true] .fv-image-reference-remove')?.focus();
    }
    // ── run: tasks owns this block (job store) ──
    // Uploads and the prompt step stay with the page; once the worker has the job, the store follows it.
    async function generate() {
        if (over()) { blockOver(); return; }
        if (!seedInput.checkValidity()) { advanced.open = true; seedInput.reportValidity(); return; }
        const seed = seedInput.value === '' ? undefined : Number(seedInput.value);
        say(''); note.dataset.kind = ''; failure = null; const mine = running = {cancelled: false}, withImages = references.length > 0, sent = size(), tierNow = tier;
        render(); drawWorks(); progressLine.textContent = progressSub.textContent = ''; runReport.replaceChildren(); runReport.dataset.id = ''; doneReport.replaceChildren(); lastReport = null;
        level.hidden = false; level.dataset.counted = 'false'; level.style.setProperty('--fv-progress', '0');
        clearStep();
        const names = [];
        try {
            for (const r of references.slice(0, cap_(model))) names.push(await client.upload(r.file));
            if (mine.cancelled) return;
            if (!enhancerOff() && await enhancer.beforeGenerate() === false) { say(''); if (running === mine) running = null; level.hidden = true; render(); drawWorks(); return; }
            startJobSources();
            const loras = loraRow.value(model.id);
            let id;
            // The engine makes one picture at a time: a second request waits its turn here and starts by itself; Stop cancels the wait.
            for (;;) {
                try {
                    ({id} = await jobs.submit('image', {prompt: engineText(seed ?? Math.floor(Math.random() * 1e9)), ...sent, model: model.id, remember: true, images: names, ...(seed === undefined ? {} : {seed}), ...(loras.length ? {loras} : {})},
                        {view: 'image', client, meta: {text: prompt.value, withImages, tier: tierNow, width: sent.width, height: sent.height}}));
                    break;
                } catch (error) {
                    if (error.code !== 'busy') throw error;
                    if (!alive || mine.cancelled || running !== mine) return;
                    note.dataset.kind = 'busy';
                    say(t('Another image is generating. This one starts when it finishes.', '前面有 1 张在生成，完成后自动开始。'));
                    progressLine.textContent = t('Waiting', '等待开始');
                    await new Promise(done => setTimeout(done, 2000));
                    if (!alive || mine.cancelled || running !== mine) return;
                }
            }
            say(''); note.dataset.kind = '';
            if (!alive || running !== mine) return;
            mine.id = id;
            if (mine.cancelled) { void jobs.cancel(id); return; }
            follow(id);
        } catch (error) {
            if (!alive || mine.cancelled || running !== mine) return;
            running = null; level.hidden = true; clearStep();
            // The worker is full: a state of the worker, shown in place, not a failed picture.
            if (IMAGE_BUSY.has(error.code)) { note.dataset.kind = 'busy'; say(imageGenError(error.code, info, t)); render(); drawWorks(); return; }
            failure = {error: error.code || 'generation_failed', report_id: error.report_id, stage: error.stage, step: error.step, fitted: error.fitted, memory: error.memory, other_usage: error.other_usage, retryStandard: tierNow === 'high' && error.code === 'no_memory'};
            renderFailure(); render(); drawWorks();
        } finally {
            // Pictures sent for a request that never became a job; a job's own go when it ends.
            if (!mine.id && names.length) void client.discardUploads?.(names).catch(() => {});
        }
    }
    // Shows a job of the store: its progress while it runs, its picture or its reason when it ends.
    function follow(id) {
        unwatch?.();
        unwatch = jobs.subscribe(() => showJob(jobs.get(id)), {id});
    }
    function showJob(row) {
        if (!alive) return;
        if (!row) { unwatch?.(); unwatch = null; if (running) { running = null; level.hidden = true; clearStep(); render(); drawWorks(); } return; }
        if (!running || running.id !== row.id) running = {cancelled: false, id: row.id};
        canvas.dataset.fvTaskId = row.id;
        if (ACTIVE.has(row.status)) {
            const percent = Number.isFinite(row.percent) ? row.percent : 0;
            const words = row.status === 'cancelling' ? t('Stopping', '正在停止') : row.status === 'queued' || row.status === 'submitting' ? t('Waiting', '等待开始') : t(...(STAGE_WORDS[row.phase] || STAGE_WORDS.sample));
            progressLine.textContent = row.status === 'running' ? [words, `${Math.floor(percent * 100)}%`].join(' · ') : words;
            // The second line changes with every report, so the page never looks stuck; elapsed counts from the job's own start.
            const begun = row.started_at || row.created_at || Date.now();
            progressSub.textContent = [`${t('Elapsed', '已用时')} ${duration((Date.now() - begun) / 1000)}`, imageRemainingText(row.eta_seconds, t)].filter(Boolean).join(' · ');
            level.hidden = false; level.dataset.counted = row.status === 'running' ? 'true' : 'false'; level.style.setProperty('--fv-progress', String(percent * 100));
            if (row.meta?.preview) showStep(row.meta.preview);
            const reportId = row.meta?.report_id;
            if (reportId && runReport.dataset.id !== reportId) {
                runReport.dataset.id = reportId; runReport.replaceChildren(imageReportBar(runReport, reportId, t, {productVersion: () => null, quiet: true, fetchApi: (path, options) => api.fetchApi(path, options)}).bar);
            }
            render();
            return;
        }
        // Ended: the page lets go of the job; the floating window has nothing more to say about it once seen here.
        unwatch?.(); unwatch = null; running = null; level.hidden = true; clearStep(); delete canvas.dataset.fvTaskId;
        jobs.markSeen(row.id);
        if (row.status === 'done' && row.result?.file) {
            shownFile = row.result.file; promptOf[shownFile] = row.meta?.text ?? row.prompt ?? '';
            started = Math.max(1, Math.round(((row.finished_at || Date.now()) - (row.started_at || row.created_at || Date.now())) / 1000));
            const reportId = row.meta?.report_id; lastReport = reportId ? {file: shownFile, id: reportId} : null;
            failure = null; jobs.dismiss(row.id);
        } else if (row.status === 'failed' || row.status === 'interrupted') {
            const reason = row.failure || {};
            failure = {error: reason.code || 'generation_failed', report_id: reason.report_id, stage: reason.stage, step: reason.step, fitted: reason.fitted, memory: reason.memory, other_usage: reason.other_usage,
                retryStandard: row.meta?.tier === 'high' && reason.code === 'no_memory'};
            renderFailure(); jobs.dismiss(row.id);
        } else jobs.dismiss(row.id);
        void loadWorks().then(() => { if (alive) { render(); drawWorks(); } });
        render(); drawWorks();
    }
    // ── end run ──
    // The failure card sits in the picture's own frame: what went wrong, where it stopped, Retry and the report.
    function renderFailure() {
        const asked = failure.sent || size();
        failure.sent = asked;
        // A model this computer does not run: the way on is the other model, one press.
        const other = failure.error === 'model_unavailable' ? IMAGE_MODELS.find(m => m.id !== model.id && offers(m)) : null;
        renderImageFailure(failHost, {...failure, info}, t, {retry: other ? {
            label: [`Use ${other.name}`, `改用 ${other.name}`],
            onClick: () => { chooseModel(other); void generate(); }} : {
            label: failure.retryStandard ? ['Retry at Standard', '改用标准并重试'] : ['Retry', '重试'],
            onClick: () => { if (failure.noList) { failure = null; render(); go.onclick(); return; } if (failure.retryStandard) { area = RANGES[model.id].presets.standard; keepSize(); } void generate(); }}});
        const head = el('div', 'fv-iv-fail-head'), mark = el('i', 'fv-iv-fail-icon'); mark.append(icon('warn'));
        head.append(mark, el('h3', '', failure.error === 'engine_closed' || failure.error === 'engine_restarted' ? t('The engine stopped', '引擎停止了') : t('The picture was not made', '这张没有生成出来')));
        failHost.prepend(head);
        const stage_ = STAGE_WORDS[failure.stage];
        // No request was made when the list could not be fetched: there is no model or size to name.
        if (!failure.noList) failHost.append(el('p', 'fv-iv-fail-facts', [model.short, sizeText(asked), stage_ ? t(`stopped at: ${stage_[0].toLowerCase()}`, `停在：${stage_[1]}`) : ''].filter(Boolean).join(' · ')));
    }
    // ── run: tasks owns this block (job store) ──
    // Stop asks first; closing the page or switching views never stops the job.
    go.onclick = async () => {
        if (running) {
            const mine = running;
            if (!await confirmStop(go, {kind: 'image', queued: jobs.get(mine.id)?.status === 'queued'})) return;
            if (running !== mine) return;
            mine.cancelled = true;
            if (mine.id) { try { await jobs.cancel(mine.id); } catch (error) { say(isNetworkTypeError(error) ? connectionLostText(t) : t('Could not stop. Try again; if it keeps failing, report the problem.', '没有停止成功。请重试；仍然失败时，请提交问题。')); } return; }
            running = null; level.hidden = true; render(); drawWorks();
        }
    // ── end run ──
        // The engine has not said which models this computer offers yet: ask first, so a request never names a model that is not here.
        else if (!info?.models) {
            if (asking) return;
            // Another press while it waits is ignored; the bar shows the press was taken.
            asking = true; level.hidden = false; level.dataset.counted = 'false'; level.style.setProperty('--fv-progress', '0');
            let answered = true;
            try { settleModel(await client.info(), true); } catch { answered = false; }
            asking = false; level.hidden = true;
            if (!alive) return;
            render();
            if (info?.models) go.onclick();
            // No answer: no request goes out, because it would name a model nobody has said is here.
            else if (answered) void generate();
            else { failure = {error: 'connection_lost', stage: 'start', noList: true}; renderFailure(); render(); drawWorks(); }
        }
        // The model on show is not one this computer offers (a remembered H3 Image on a Mac): the press moves to the one it offers.
        else if (!offers(model) && IMAGE_MODELS.some(offers)) { chooseModel(IMAGE_MODELS.find(offers)); go.onclick(); }
        // A model being installed: Generate says whether the picture follows the install.
        else if (loading()) { pendingGenerate = !pendingGenerate && (!!prompt.value.trim() || references.length > 0); if (!prompt.value.trim() && !references.length) { promptGroup.open(true); prompt.focus(); } render(); }
        // A model not installed: installs it, then generates.
        else if (missing() || modelState(info, model.id).state === 'failed') { pendingGenerate = !!prompt.value.trim() || references.length > 0; void install(); }
        else if (!prompt.value.trim() && !references.length) { promptGroup.open(true); prompt.focus(); }
        else void generate();
    };
    go.addEventListener('click', () => { if (drawer === 'left' && go.dataset.state === 'ready' && (prompt.value.trim() || references.length)) { drawer = ''; paintPanels(); } }, {capture: true});
    prompt.addEventListener('input', () => { growPrompt(); render(); });
    // Getting the model ready before Generate is pressed (nothing of it shows): being here a few seconds loads the model
    // where the card has free room for it (eager loading; the engine reads the files ahead where it would take the
    // video models' room, and the conservative setting keeps it to that); touching anything says "light"; a prompt
    // being written, a model or shape chosen, or the pointer on Generate says "full".
    const warmSoon = setTimeout(() => preloadMode() === 'eager' ? warmUp('full', true) : warmUp('light'), 3000);
    let warmTyping = 0;
    prompt.addEventListener('input', () => { clearTimeout(warmTyping); warmTyping = setTimeout(() => { if (prompt.value.trim()) warmUp('full'); }, 500); });
    for (const type of ['pointerdown', 'keydown']) dialog.addEventListener(type, () => warmUp('light'), {capture: true, passive: true});
    go.addEventListener('pointerenter', () => warmUp('full'));
    // Three lines at rest; while focused it grows with what is typed (up to eight lines) and settles back on leaving.
    function growPrompt() {
        // An empty box never grows (its sheet may still be loading when the page first focuses it).
        // The box fills the panel's free height now; it scrolls inside instead of growing.
        if (true) { prompt.style.height = ''; delete prompt.dataset.grown; return; }
        const rest = parseFloat(getComputedStyle(prompt).minHeight) || 0;
        prompt.style.height = ''; const need = prompt.scrollHeight + 2;
        if (need > rest + 2) { prompt.style.height = `${Math.min(need, rest * 8 / 3)}px`; prompt.dataset.grown = 'true'; } else delete prompt.dataset.grown;
    }
    prompt.addEventListener('focus', growPrompt);
    prompt.addEventListener('blur', () => { growPrompt(); fitSoon(); });
    favBtn.addEventListener('click', () => { if (!shownFile) return; meta.setFav([shownFile], !meta.isFav(shownFile)); drawWorks(); render(); });
    editThis.addEventListener('click', () => void editPicture(shownFile));
    // Crop, paint, add text, or change a part with AI in the image editor; the result comes back as Image 1 and into the library.
    async function editPicture(file) {
        if (!file) return;
        let outcome, loadFailed = false;
        try {
            const {openImageEditor} = await import('./image_editor.js').catch(error => { loadFailed = true; throw error; });
            outcome = await openImageEditor({source: file, name: file, role: 'reference', t, resolve: pictureURL, upload: async blob => client.upload(blob), imageGenClient: client});
        } catch { say(loadFailed ? t('The image editor did not load. Reload the page and try again.', '图片编辑器没有加载出来。请刷新页面后再试。') : t('This picture could not be read. Choose another picture.', '这张图无法读取。请重新选择一张。')); return; }
        if (!alive || !outcome?.blob) return;
        try {
            const edited = new File([outcome.blob], outcome.name || 'edited.png', {type: outcome.blob.type || 'image/png'});
            const name = await client.upload(edited);
            // The edited picture is one work of the library (one copy, no card of its own); the upload above only serves as the reference.
            void saveToWorks(outcome.blob, {name: outcome.name || 'edited.png', source: file, client}).catch(() => {});
            const record = {file: edited, url: URL.createObjectURL(edited), width: outcome.width, height: outcome.height};
            if (references.length >= most()) URL.revokeObjectURL(record.url); else { changeRefs(() => references.unshift(record)); promptGroup.open(true); }
            render();
        } catch (error) { say(isNetworkTypeError(error) ? connectionLostText(t) : t('The edited picture could not be saved. Try again; if it keeps failing, report the problem.', '编辑后的图片没能保存。请重试；仍然失败时，请提交问题。')); }
    }
    asReference.addEventListener('click', () => void useAsReference(shownFile, false));
    // Opens downward when it fits inside the stage, otherwise upward over the picture.
    moreButton.addEventListener('click', () => {
        more.hidden = !more.hidden; if (more.hidden) return;
        delete more.dataset.up;
        const box = canvasArea.getBoundingClientRect(), below = more.getBoundingClientRect().bottom;
        if (below > box.bottom + 40) more.dataset.up = 'true';
    });
    document.addEventListener('click', closeMore, true);
    function closeMore(event) {
        if (!more.hidden && !moreHost.contains(event.target)) more.hidden = true;
        if (!keysPop.hidden && !keysHost.contains(event.target)) { keysPop.hidden = true; keysBtn.setAttribute('aria-pressed', 'false'); }
    }
    // A picture finished anywhere (here, or an AI edit in the picture editor) joins the works at once.
    const onJobDone = event => {
        if (!alive) return; meta.reload(); library.paint(); drawWorks(); render();
        const job = event?.detail?.job, file = job?.kind === 'image' && job.status === 'done' ? job.result?.file : null;
        if (file && !rows.some(r => r.file === file)) void loadWorks();
    };
    window.addEventListener('freevideo-job-done', onJobDone);
    // A picture added to the works elsewhere (a saved edit) too.
    const onWorksAdded = event => { if (alive && event.detail?.source !== 'image-view') void loadWorks(); };
    window.addEventListener('freevideo-works-added', onWorksAdded);
    // Pictures deleted elsewhere (the picture editor, the works library) leave the works here at once: hidden while
    // their undo window runs, dropped when the engine has deleted them, and back if the delete is undone there.
    const fileOf = path => { const name = String(path).slice(String(path).lastIndexOf('/') + 1); return rows.find(r => r.file === path || r.name === name)?.file || null; };
    // Works deleted for good (here or anywhere): their words leave the prompt history unless still in use.
    const forgetPrompts = () => { void forgetDeletedPrompts().then(n => { if (n && alive) library.paint(); }); };
    const onWorksGone = event => { if (alive && event.detail?.undoable === false) { forgetPrompts(); setTimeout(forgetPrompts, 5000); } };
    window.addEventListener('freevideo-works-removed', onWorksGone);
    forgetPrompts();
    const onWorksRemoved = event => {
        const detail = event.detail || {}; if (!alive || detail.source === 'image-view') return;
        const files = (detail.paths || detail.ids || []).map(fileOf).filter(Boolean); if (!files.length) return;
        if (detail.undoable === false) { rows = rows.filter(r => !files.includes(r.file)); files.forEach(f => elsewhere.delete(f)); }
        else files.forEach(f => elsewhere.add(f));
        files.forEach(f => picked.delete(f));
        if (files.includes(shownFile) && !running) shownFile = visible()[0]?.file || '';
        drawWorks(); render();
    };
    const onWorksRestored = event => {
        const detail = event.detail || {}; if (!alive || detail.source === 'image-view') return;
        let unknown = false;
        for (const path of detail.paths || detail.ids || []) { const file = fileOf(path); if (file) elsewhere.delete(file); else unknown = true; }
        if (unknown) void loadWorks(); else { drawWorks(); render(); }
    };
    window.addEventListener('freevideo-works-removed', onWorksRemoved); window.addEventListener('freevideo-works-restored', onWorksRestored);
    // Leaving the page ends this page's undo window: the pictures are deleted as asked, not left on disk.
    const onPageHide = () => { void trash.flush(); };
    window.addEventListener('pagehide', onPageHide);
    // Global search and notifications open a picture here: shown on the stage, the works list reloaded if it is new.
    const onOpenWork = async event => {
        const file = event.detail?.kind === 'image' ? event.detail.file : null; if (!alive || !file) return;
        if (!rows.some(r => r.file === file)) await loadWorks();
        if (filter !== 'all' && !visible().some(r => r.file === file)) filter = 'all';
        showFile(file);
    };
    window.addEventListener('freevideo-open-work', onOpenWork);
    // The floating window asks for the generation screen back while this page is showing another picture.
    const onStageOpen = event => { if (alive && (event.detail?.view || 'image') === 'image' && running) returnToRun(); };
    window.addEventListener('freevideo-task-stage-open', onStageOpen);

    // Keys: the page's own, never while typing.
    const onKeys = event => {
        const typing = event.target.closest?.('input,textarea,[contenteditable=true]');
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { if (!running && (prompt.value.trim() || references.length)) { event.preventDefault(); go.click(); } return; }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f' && !typing) { event.preventDefault(); setPanel('right', true); setRightTab('works'); works.focusSearch(); return; }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !typing && undoNote) { event.preventDefault(); restoreWorks(undoNote.files); undoNote = null; drawWorks(); render(); return; }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a' && selecting && !typing) { event.preventDefault(); visible().forEach(r => picked.add(r.file)); drawWorks(); render(); return; }
        if (typing || event.altKey || event.ctrlKey || event.metaKey) return;
        const steering = event.target.closest?.('[role=slider],input[type=range],[role=radiogroup],[role=menu]');
        if (event.key === 'ArrowLeft' && !steering) { step(-1); event.preventDefault(); }
        else if (event.key === 'ArrowRight' && !steering) { step(1); event.preventDefault(); }
        else if (event.key.toLowerCase() === 'f' && selecting && picked.size) { const list = [...picked]; meta.setFav(list, !list.every(f => meta.isFav(f))); drawWorks(); }
        else if (event.key.toLowerCase() === 'f' && shownFile && !selecting) favBtn.click();
        else if (event.key.toLowerCase() === 'i') infoBtn.click();
        else if (event.key === '?') keysBtn.click();
        // Only the Delete key deletes (as the delete buttons do); Backspace never deletes a picture.
        else if (event.key === 'Delete' && (selecting ? picked.size : shownFile && !running)) { event.preventDefault(); deleteFiles(selecting ? [...picked] : [shownFile]); }
    };
    listenDialogKey(dialog, 'keydown', onKeys);
    // A click that moves the focus off a control leaves it on the page body; the page's keys still work then.
    const strayKeys = event => { if (event.target === document.body && dialog.open && topDialog() === dialog) onKeys(event); };
    document.addEventListener('keydown', strayKeys);
    // Escape steps back one layer at a time; the view itself closes last.
    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        if (!more.hidden) more.hidden = true;
        else if (!keysPop.hidden) { keysPop.hidden = true; keysBtn.setAttribute('aria-pressed', 'false'); }
        else if (drawer) { drawer = ''; paintPanels(); }
        else if (compare) { selecting = false; picked.clear(); drawWorks(); render(); }
        else if (draft && shownFile) { draft = false; draftPreview = null; render(); }
        else if (view === 'trash') { view = 'works'; drawWorks(); }
        else if (selecting) { selecting = false; picked.clear(); drawWorks(); render(); }
        else closeDialog(dialog);
    });
    // Leaving keeps the view, closed and out of reach (as the editor and the AI view are): its picture, works, prompt,
    // references and panels stay as they were, and the page's events (a picture finished or deleted elsewhere) keep it
    // up to date. Only a view opened for another node, or with a client of its own (tests), is taken down.
    const keepable = !given;
    let refreshSoon = 0;
    function dispose() {
        window.removeEventListener('freevideo-job-done', onJobDone); window.removeEventListener('freevideo-open-work', onOpenWork); window.removeEventListener('freevideo-task-stage-open', onStageOpen); fitWatch?.disconnect(); cancelAnimationFrame(fitFrame); sizer.dispose(); mentions.dispose();
        clearTimeout(warmSoon); clearTimeout(warmTyping); if (!running) warmer.cancel(model.id); viewer?.close();
        window.removeEventListener('freevideo-works-removed', onWorksRemoved); window.removeEventListener('freevideo-works-restored', onWorksRestored); window.removeEventListener('pagehide', onPageHide); window.removeEventListener('freevideo-works-added', onWorksAdded);
        window.removeEventListener('freevideo-works-removed', onWorksGone);
        // Kept for the next time the view opens on this page (the pictures as files; their addresses are made again).
        keptDraft = {prompt: prompt.value, caret: prompt.selectionStart ?? prompt.value.length,
            references: references.map(({url, original, ...rest}) => ({...rest, ...(original ? {original: {file: original.file, width: original.width, height: original.height}} : {})}))};
        references.forEach(r => { if (r.original) URL.revokeObjectURL(r.original.url); });
        // Deleted pictures waiting for their undo window go now: nothing is left half-deleted behind a closed page.
        void trash.flush(); trash.dispose(); loraRow.dispose();
        // ── run: tasks owns this block (job store) ──
        // Leaving only stops watching: the job runs on, the floating window carries it, and its picture reaches the works.
        rememberView(); document.removeEventListener('keydown', strayKeys); expand.dispose?.();
        alive = false; place.dispose(); stopScene(); enhancer.dispose(); promptBarWatch?.disconnect(); document.removeEventListener('click', closeMore, true);
        unwatch?.(); unwatch = null; references.forEach(r => URL.revokeObjectURL(r.url)); clearStep(); dialog.remove();
        // ── end run ──
        if (kept?.dialog === dialog) kept = null;
    }
    function hide() {
        clearTimeout(refreshSoon); clearTimeout(warmSoon); clearTimeout(warmTyping); if (!running) warmer.cancel(model.id); viewer?.close();
        more.hidden = true; keysPop.hidden = true; keysBtn.setAttribute('aria-pressed', 'false');
        // Deleted pictures waiting for their undo window go now, as when the view closed for good.
        void trash.flush();
        rememberView(); dialog.inert = true;
    }
    dialog.onclose = () => {
        if (keepable && alive) hide(); else dispose();
        if (current === dialog) current = null;
        next();
    };
    function reopen(nextStudio) {
        studio = nextStudio;
        next = () => studio?.open ? viewChanged('studio', node) : viewChanged('nodes', node);
        // What the last close left on the dialog (its fade, the leaving mark) goes, so it shows as it was.
        dialog.getAnimations?.().forEach(animation => animation.cancel());
        delete dialog.dataset.closing; delete dialog.dataset.fvLeaving; dialog.inert = false;
        // Keys go to the last open dialog in page order: move it after one opened since.
        const later = [...document.querySelectorAll('dialog[open]')].some(other => other !== dialog && dialog.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING);
        if (!dialog.isConnected || later) document.body.append(dialog);
        current = dialog; if (!dialog.open) dialog.showModal(); viewChanged('image', node);
        // The view's own tab takes the focus after the first frame (not the first control, which would wear a ring),
        // as in the editor and the AI view.
        const focused = document.activeElement;
        requestAnimationFrame(() => setTimeout(() => { if (dialog.open && document.activeElement === focused) navigation.querySelector('[data-view=image]')?.focus({preventScroll: true}); }));
        // ── run: tasks owns this block (job store) ──
        startJobSources();
        const live = jobs.active({kind: 'image', view: 'image'})[0];
        if (live && !running) { running = {cancelled: false, id: live.id}; follow(live.id); }
        // ── end run ──
        // Nothing is redrawn at the press (the view is as it was, and the page's events kept it current while away):
        // the works are read again once it is in front, after the switch has finished, and redrawn only from that answer.
        clearTimeout(refreshSoon);
        refreshSoon = setTimeout(() => { if (alive && dialog.open) { meta.reload(); fitLeft(); void loadWorks(); } }, 700);
        return dialog;
    }
    if (keepable) kept = {node, dialog, reopen, dispose};
    // ── run: tasks owns this block (job store) ──
    // A picture still being made (started here before, or before a reload) is shown before the first paint, so the page never looks idle.
    startJobSources();
    // Only the image view's own job: a picture the image editor is making is a draft of the editor's.
    const live = jobs.active({kind: 'image', view: 'image'})[0];
    if (live) { running = {cancelled: false, id: live.id}; if (!prompt.value && live.meta?.text) prompt.value = live.meta.text; follow(live.id); }
    // ── end run ──
    current = dialog; document.body.append(dialog); dialog.showModal(); viewChanged('image', node); keepSize(); render(); drawWorks(); fitLeft();
    if (viewCache?.scroll) requestAnimationFrame(() => { const list = works.element.querySelector('.fv-iv-works-body'); if (list) list.scrollTop = viewCache.scroll; });
    // The shell reveals the view when it says ready (it waits 800 ms at most): say so once the first screen is the final
    // one (the engine's model states, its works list and the shown picture), so nothing changes after it appears.
    setTimeout(() => { dialog.dataset.fvReady = 'true'; }, 1500);
    markReady('start');
    // The first model to offer: the last used, or an installed one (H3 Image first). Both are always shown.
    // A model the engine lists no row for is not offered on this computer (H3 Image on a Mac) and is never kept.
    function settleModel(value, keepChosen = false) {
        info = value; const before = model;
        const ready = IMAGE_MODELS.filter(m => value.models?.find(r => r.id === m.id)?.ready);
        const offered = IMAGE_MODELS.filter(m => value.models?.some(r => r.id === m.id && r.available !== false));
        if (value.models && !remembered() && ready.length) model = ready[0];
        if (value.models && offered.length && !offered.includes(model)) model = offered[0];
        // A press that waited for this list keeps the size the user already chose, unless the model changed.
        if (keepChosen && model === before) return;
        const limits = areaRange(model.id, ratioValue(shapeOf()));
        area = Math.min(limits.max, Math.max(limits.min, sizes[model.id] || RANGES[model.id].presets.standard));
        keepSize();
    }
    client.info().then(value => {
        settleModel(value);
        const poller = setInterval(() => { if (!alive) return clearInterval(poller); if (IMAGE_MODELS.some(m => modelState(info, m.id).state === 'downloading')) client.info().then(v => { info = v; render(); }).catch(() => {}); }, 2000);
        if (alive) { render(); fitLeft(); if (historyLoaded) rememberView(); }
        markReady('info');
    }).catch(() => markReady('info'));
    // No focus on open: the box stays neutral until the user goes to it.
    void loadWorks();
    return dialog;
}
