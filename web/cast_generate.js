// "Generate with AI" page of the new-character dialog: one description becomes five pictures of the
// same person (front, three-quarter, side, back, face). Each picture shows as it is finished; the
// dialog's Save waits for all five. Pictures are made one by one through the page's image client
// and stay in the input folder; the dialog puts them on the card.
import {createImageGenClient, imageGenProgress, imagePackUnavailableMessage, renderImageFailure,
    IMAGE_GEN_POLL_MS, IMAGE_GEN_INFO_POLL_MS} from './image_generation.js';
import {createImageLoras} from './image_lora.js';
import {createOtherUsageNotice} from './other_usage.js';

export {imageGenerationSupported as castGenerateSupported} from './image_generation.js';

export const CAST_VIEWS = Object.freeze(['front', 'three_quarter', 'side', 'back', 'face']);
// A scene is not a sheet of views: one description is drawn three times with different seeds and the user picks one.
export const SCENE_CANDIDATES = Object.freeze(['a', 'b', 'c']);
const VIEW_TITLES = Object.freeze({
    front: ['Front', '正面'], three_quarter: ['Three-quarter', '四分之三'], side: ['Side', '侧面'],
    back: ['Back', '背面'], face: ['Face', '脸部'],
});

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
const failureOf = error => ({error: error?.code || 'generation_failed', report_id: error?.report_id || null,
    stage: error?.stage || '', fitted: error?.fitted || null, memory: error?.memory || '', other_usage: error?.other_usage || null});
const randomSeed = () => crypto.getRandomValues(new Uint32Array(1))[0] >>> 1;

// From a photo: the face close-up comes first, from the photo alone; the front is then made from the photo (<Picture 1>)
// and that close-up (<Picture 2>), so every view keeps the person's face, and the side and back also see the front
// (<Picture 3>) so they wear the same clothes. The engine reads each reference as <Picture N>. Every sentence says what
// to show (no "no …" phrasing). These words stay here until cast-views takes `photo: true`; then use its answer.
// The close-up names its model: Qwen-Image 2.1 when it is ready (it keeps a photo's likeness far better), else H3 Image
// with the Chinese words H3 follows best. The front, side and back use the model the engine would use for any picture
// (Qwen when a request names none); with H3 Image the only one ready, they name it too.
export const PHOTO_FACE_MODELS = Object.freeze({qwen: 'qwen-image-2.1-turbo', h3: 'minimax-h3-image'});
const PHOTO_WHO = 'The person in image 1';
const BACKDROP = 'on a clean light grey background with soft even light';
const WHO = '<Picture 1> is the original photo of this person and <Picture 2> is a close-up of the same person\'s face.';
const WHO_AND_FRONT = '<Picture 1> is the original photo of this person, <Picture 2> is a close-up of the same person\'s face and '
    + '<Picture 3> is the full-body front view of this person.';
const BODY_SAME = 'The body shape and appearance are the same as the person in <Picture 1>.';
const PHOTO_END = 'A real photograph of one person, sharp focus.';
export const PHOTO_PROMPTS = Object.freeze({
    // From the whole photo these words keep the likeness best with Qwen (the edit words that turn the head to the front
    // score lower); kept as measured, "Image 1" included.
    face: 'Image 1 shows the person. A tight head-and-shoulders portrait of this person: the head from the top of the hair to the '
        + 'chin fills about 80% of the picture height and a little of the shoulders shows; the person faces the camera at eye level '
        + 'with a neutral expression; the eyes, nose, lips and skin are sharp. The face shape, eyes, nose, mouth, age, hairline, '
        + 'hairstyle, glasses and accessories match image 1. On a plain light grey studio backdrop with soft even light. One '
        + 'photograph of one person.',
    face_h3: '把<Picture 1>改成同一个人的近景正面特写照片：画面从头顶到下巴，再带一点肩膀，人物正对镜头，眼睛平视镜头。'
        + '脸型、眼睛、眉毛、鼻子、嘴巴、肤色、年龄、发型、发际线、表情和光线都与<Picture 1>中的人一致。'
        + '背景是干净的浅灰色，五官和皮肤纹理清晰，真实照片。',
    front: '{WHO} Turn this person into a full-body front photograph: standing and facing the camera, the whole body from the top '
        + `of the head to the feet, the arms relaxed at the sides, ${BACKDROP}. The face shape, eyes, eyebrows, nose, mouth, skin `
        + `tone, age, hairstyle and hairline are the same as the face in <Picture 2>. ${BODY_SAME} {CLOTHES} ${PHOTO_END}`,
    side: '{WHO} Turn this person into a full-body side view: the whole body turned 90 degrees to the left, the nose, chest and '
        + 'toes pointing to the left edge of the picture, one eye visible, the near shoulder in front of the far shoulder, the arms '
        + `relaxed, the whole body from the top of the head to the feet, ${BACKDROP}. The face, seen from the side, has the same `
        + 'shape, eyebrows, nose, lips, skin tone and age as the face in <Picture 2>, and the hairstyle and hairline are the same '
        + `as in <Picture 2>. ${BODY_SAME} {CLOTHES} ${PHOTO_END}`,
    back: '{WHO} Turn this person into a full-body view from directly behind: the back of the head, the hair, the shoulders, the '
        + 'back, the back of the clothes and the heels face the camera, the head and the body point the same way, the arms '
        + `relaxed, the whole body from the top of the head to the feet, ${BACKDROP}. The hair colour, hairstyle and hairline are `
        + `the same as in <Picture 2>. The body shape is the same as the person in <Picture 1>. {CLOTHES} ${PHOTO_END}`,
});
// The close-up's model from the engine's model list: Qwen-Image 2.1 when ready, else H3 Image when ready, else none named.
export function photoFaceModel(info) {
    const ready = id => Array.isArray(info?.models) && info.models.some(row => row?.id === id && row.ready === true);
    return ready(PHOTO_FACE_MODELS.qwen) ? PHOTO_FACE_MODELS.qwen : ready(PHOTO_FACE_MODELS.h3) ? PHOTO_FACE_MODELS.h3 : '';
}
// plan: the engine's view list (for the sizes); outfit: words for the clothes; outfitImage: a clothes picture is given;
// frontAsThird: the side and back also see the front (<Picture 3>) for the clothes; faceModel: photoFaceModel's answer.
export function photoPlan(plan, outfit = '', {outfitImage = false, frontAsThird = true, faceModel = ''} = {}) {
    outfit = String(outfit || '').trim().replace(/[.。]+$/, '');
    const size = name => { const view = plan.find(row => row.view === name) || plan.find(row => row.view === 'front') || {}; return {width: view.width || 832, height: view.height || 1472}; };
    const asPhoto = 'The clothes, shoes and accessories are the same as in <Picture 1>.';
    const frontClothes = outfitImage ? `The clothes, shoes and accessories are the ones in <Picture 3>${outfit ? `, with these details: ${outfit}` : ''}.`
        : outfit ? `The clothes are: ${outfit}.` : asPhoto;
    const laterClothes = frontAsThird ? 'The clothes, shoes and accessories are the same as in <Picture 3>, the front view.'
        : outfit ? `The clothes are: ${outfit}.` : asPhoto;
    const viewModel = faceModel === PHOTO_FACE_MODELS.h3 ? {model: faceModel} : {};
    const later = name => ({view: name, ...size(name), ...viewModel, refs: ['photo', 'face', ...(frontAsThird ? ['front'] : [])],
        prompt: PHOTO_PROMPTS[name].replace('{WHO}', frontAsThird ? WHO_AND_FRONT : WHO).replace('{CLOTHES}', laterClothes)});
    return [
        {view: 'face', width: 1024, height: 1280, refs: ['photo'], ...(faceModel ? {model: faceModel} : {}),
            prompt: faceModel === PHOTO_FACE_MODELS.h3 ? PHOTO_PROMPTS.face_h3 : PHOTO_PROMPTS.face},
        {view: 'front', ...size('front'), ...viewModel, refs: ['photo', 'face', ...(outfitImage ? ['outfit'] : [])],
            prompt: PHOTO_PROMPTS.front.replace('{WHO}', WHO + (outfitImage ? ' <Picture 3> shows the clothes to wear.' : '')).replace('{CLOTHES}', frontClothes)},
        later('side'),
        later('back'),
    ];
}

// The five pictures of one description. No DOM: the page below draws its state.
export function createCastGenerator({client, kind = 'character', views = null, frontAsThird = true, onChange = () => {}, beforeGenerate = async () => {}, onDiscard = () => {}, schedule = setTimeout, unschedule = clearTimeout}) {
    const scene = kind === 'setting' || kind === 'prop', names = views || (scene ? SCENE_CANDIDATES : CAST_VIEWS);
    const fresh = () => names.map(view => ({view, file: '', fraction: 0}));
    let state = {pictures: fresh(), running: false, current: -1, error: '', report_id: null, waiting: '', description: '', photo: '', outfit: ''};
    let epoch = 0, timer = null, job = null, plan = null, seed = 0, disposed = false, loras, outfit = '';
    // From a photo with a small face: the face cut out and enlarged by the page, for the face close-up only.
    let closeUp = '';
    const seeds = [];
    // Pictures standing in for a view as a reference (an edited face or front, uploaded by the page).
    const overrides = {};
    const emit = patch => { state = {...state, ...patch}; if (!disposed) onChange(state); };
    const setPicture = (index, patch) => emit({pictures: state.pictures.map((row, i) => i === index ? {...row, ...patch} : row)});
    const cancelJob = id => Promise.resolve().then(() => client.cancel(id)).catch(() => {});
    const done = () => state.pictures.every(row => row.file);

    async function runOne(index, alive) {
        const view = plan[index], front = state.pictures[0].original ? state.photo : state.pictures[0].file;
        const dress = view.outfit && state.outfit ? [state.outfit] : [];
        // From a photo: each view names the pictures it sees (the photo, the face close-up, the front, the clothes).
        const picked = name => { const at = names.indexOf(name), row = at >= 0 ? state.pictures[at] : null; return row?.original ? state.photo : row?.file || ''; };
        // The face close-up sees the face cut out of the photo where the page made one (a small face), else the photo.
        const roles = {photo: () => (view.view === 'face' && closeUp) || state.photo, face: () => overrides.face || picked('face'), front: () => overrides.front || picked('front'), outfit: () => state.outfit};
        const images = view.refs ? view.refs.map(role => roles[role]?.() || '').filter(Boolean)
            : view.photo ? [state.photo, ...dress] : view.from_front ? [front, ...dress] : [];
        await beforeGenerate({kind: 'image', width: view.width, height: view.height, image_references: images.length});
        if (!alive()) return null;
        const row = await client.generate({...(view.model ? {model: view.model} : {}), prompt: view.prompt, width: view.width, height: view.height, seed: seeds[index] ?? (scene ? seed + index : seed),
            images, ...(view.view === 'back' && images.length === 1 ? {cast_view: 'back'} : {}),
            ...(loras === undefined ? {} : {loras})});
        if (!row.job) throw Object.assign(new Error(row.error || 'generation_failed'), {code: row.error, report_id: row.report_id, memory: row.memory});
        if (!alive()) { await cancelJob(row.job); return null; }
        job = row.job;
        for (;;) {
            await new Promise(resolve => { timer = schedule(() => { timer = null; resolve(); }, IMAGE_GEN_POLL_MS); });
            if (!alive()) return null;
            const status = await client.status(job);
            if (!alive()) return null;
            if (status.state === 'error') {
                throw Object.assign(new Error(status.error), {code: status.error, report_id: status.report_id,
                    stage: status.stage, fitted: status.fitted, memory: status.memory, other_usage: status.other_usage});
            }
            if (status.state === 'done') {
                if (!status.file) throw new Error('generation_failed');
                return status.file;
            }
            setPicture(index, {fraction: Math.max(state.pictures[index].fraction,
                imageGenProgress(status.stage, status.fraction, view.from_front)), stage: status.stage || ''});
            emit({waiting: status.waiting || ''});
        }
    }

    async function start(description, {loras: selectedLoras, photo = '', closeUp: face = '', outfit: wear = '', outfitImage = ''} = {}) {
        description = String(description || '').trim();
        if (photo && !description) description = PHOTO_WHO;
        if (state.running || disposed || !description) return;
        closeUp = photo ? face : '';
        if (photo && (photo !== state.photo || wear !== outfit || outfitImage !== state.outfit)) { plan = null; emit({photo, outfit: outfitImage}); outfit = wear; for (const row of state.pictures) if (row.file && !row.original) onDiscard(row.file); emit({pictures: fresh(), description: ''}); }
        // After a failure the same description goes on from the picture that failed.
        const resume = !!state.error && description === state.description && plan && !done()
            && JSON.stringify(selectedLoras) === JSON.stringify(loras);
        const current = ++epoch;
        const alive = () => current === epoch && !disposed;
        if (!resume) {
            seed = randomSeed(); plan = null; seeds.length = 0; loras = structuredClone(selectedLoras);
            // Generating all again replaces every picture: the ones made before are let go (deleted by the caller).
            for (const row of state.pictures) if (row.file && !row.original) onDiscard(row.file);
        }
        emit({running: true, error: '', report_id: null, waiting: '', description,
            ...(resume ? {} : {pictures: fresh(), current: 0})});
        try {
            if (!plan) {
                plan = await client.castViews(description, kind);
                if (scene) plan = names.map(() => plan[0]);
                if (state.photo) {
                    // The close-up's model from the engine's newest list (a model may have finished installing since the page opened).
                    let faceModel = '';
                    try { faceModel = photoFaceModel(await client.info?.()); } catch { /* none named: the engine's own choice */ }
                    if (!alive()) return;
                    const made = photoPlan(plan, outfit, {outfitImage: !!state.outfit, frontAsThird, faceModel});
                    plan = names.map(name => made.find(view => view.view === name)).filter(Boolean);
                }
                if (!alive()) return;
            }
            for (let index = 0; index < plan.length; index++) {
                if (state.pictures[index].file) continue;
                emit({current: index});
                const file = await runOne(index, alive);
                if (file === null || !alive()) return;
                job = null;
                setPicture(index, {file, fraction: 1});
            }
            emit({running: false, current: -1, waiting: ''});
        } catch (error) {
            if (!alive()) return;
            const id = job; job = null;
            if (id) void cancelJob(id);
            // A failure keeps the pictures already made; the next Generate goes on from the failed one.
            emit({running: false, current: -1, waiting: '', ...failureOf(error),
                pictures: state.pictures.map(row => row.file ? row : {...row, fraction: 0})});
        }
    }

    function stop() {
        const id = job; job = null;
        ++epoch; unschedule(timer); timer = null; plan = null;
        // A stopped run is dropped whole: the next Generate starts again from the description.
        emit({running: false, current: -1, waiting: '', error: '', report_id: null, pictures: fresh()});
        return id ? cancelJob(id) : Promise.resolve();
    }
    // The description changed: pictures made from the old one no longer belong to it.
    function reset() {
        if (state.running || !(state.pictures.some(row => row.file) || state.error)) return;
        plan = null;
        emit({pictures: fresh(), error: '', report_id: null, current: -1});
    }
    // One view again with a new seed; the other views stay. The replaced picture is discarded when the new one is in.
    async function redo(index) {
        if (state.running || disposed || !plan || !state.pictures[index]) return;
        const current = ++epoch, alive = () => current === epoch && !disposed;
        const before = state.pictures[index];
        seeds[index] = randomSeed();
        setPicture(index, {file: '', fraction: 0, original: false});
        emit({running: true, current: index, error: '', report_id: null, waiting: ''});
        try {
            const file = await runOne(index, alive);
            if (file === null || !alive()) return;
            job = null; setPicture(index, {file, fraction: 1});
            if (before.file && !before.original) onDiscard(before.file);
            emit({running: false, current: -1, waiting: ''});
        } catch (error) {
            if (!alive()) return;
            const id = job; job = null; if (id) void cancelJob(id);
            setPicture(index, before);
            emit({running: false, current: -1, waiting: '', ...failureOf(error)});
        }
    }
    // The photo itself as the front (it was uploaded for the reference); the generated front is discarded.
    function useOriginal(file) {
        if (state.running || disposed) return;
        const before = state.pictures[0];
        setPicture(0, {file, fraction: 1, original: true});
        if (before.file && !before.original) onDiscard(before.file);
    }
    return {start, stop, reset, redo, useOriginal, state: () => state, done,
        setOverride(role, name) { if (name) overrides[role] = name; else delete overrides[role]; },
        dispose() { disposed = true; return stop(); }};
}

export function mountCastGenerate({container, api, text, t = text, viewURL, onState = () => {}, onPick = () => {}, fetchApi, client, loras, kind = 'character'}) {
    const scene = kind === 'setting' || kind === 'prop', names = scene ? SCENE_CANDIDATES : CAST_VIEWS;
    const tr = t || ((en) => en);
    const fetcher = fetchApi || (api ? api.fetchApi.bind(api) : globalThis.fetch.bind(globalThis));
    client = client || createImageGenClient(fetcher);
    const url = viewURL || (file => {
        const i = file.lastIndexOf('/');
        const query = new URLSearchParams({filename: file.slice(i + 1), subfolder: file.slice(0, Math.max(0, i)), type: 'input'});
        return (api?.apiURL ? api.apiURL('/view?' + query) : '/view?' + query);
    });
    const cssURL = new URL('./cast_generate.css', import.meta.url).href;
    if (!document.querySelector(`link[href="${cssURL}"]`)) {
        const css = el('link'); css.rel = 'stylesheet'; css.href = cssURL; document.head.append(css);
    }

    const root = el('div', 'fv-cg');
    if (scene) root.dataset.kind = 'scene';
    if (kind === 'prop') root.dataset.shape = 'square';
    const body = el('div', 'fv-cg-body');
    const side = el('div', 'fv-cg-side');
    const foot = el('div', 'fv-cg-foot');
    const field = el('textarea', 'fv-cg-text');
    field.placeholder = kind === 'prop' ? tr('Describe the prop', '描述道具') : scene ? tr('Describe the scene', '描述场景') : tr('Describe the character', '描述角色');
    field.setAttribute('aria-label', field.placeholder);
    field.maxLength = 1000;
    const note = el('div', 'fv-cg-note'); note.setAttribute('role', 'status');
    const action = el('button', 'fv-cg-action'); action.type = 'button';
    // A caller that passes a fixed list owns the choice; otherwise the panel offers the same picker as image generation.
    const picker = loras === undefined ? createImageLoras({t: tr, fetchApi: fetcher, getPrompt: () => field.value,
        setPrompt: text => { field.value = text; }, onChange: () => render()}) : null;
    // The LoRA capsule and the generate button share one row, at its left and right ends.
    const row = el('div', 'fv-cg-row');
    row.append(...(picker ? [picker.element] : []), action);
    foot.append(note);
    side.append(field, row, foot);
    const grid = el('div', 'fv-cg-views');
    const cells = names.map((view, index) => {
        const cell = el('div', 'fv-cg-view');
        const title = scene ? tr(`Option ${index + 1}`, `方案 ${index + 1}`) : tr(...VIEW_TITLES[view]);
        cell.title = title; cell.setAttribute('role', scene ? 'button' : 'img');
        if (scene) {
            cell.tabIndex = 0;
            const pick = () => { if (cell.dataset.state !== 'done') return; chosen = index; render(); };
            cell.onclick = pick;
            cell.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pick(); } };
        }
        const image = el('img'); image.alt = ''; image.draggable = false;
        const percent = el('span', 'fv-cg-percent');
        const meter = el('i', 'fv-cg-meter'); const fill = el('b'); meter.append(fill);
        cell.append(image, percent, meter);
        grid.append(cell);
        return {cell, image, percent, fill, title};
    });
    // A scene shows the chosen option large on narrow widths, where three small ones cannot show its mood.
    const stage = el('div', 'fv-cg-stage'), stageImage = el('img'); stageImage.alt = ''; stage.append(stageImage); stage.hidden = true;
    body.append(side, ...(scene ? [stage] : []), grid);
    root.append(body);
    container.append(root);

    let chosen = 0, picked = '', info = null, infoTimer = null, disposed = false, failureKey = null, attention = null;
    const otherUsage = createOtherUsageNotice(side, tr, fetcher);
    const generator = createCastGenerator({client, kind, onChange: render, beforeGenerate: query => otherUsage.check(query)});
    let last = '';

    function missing() { return info && !info.installed && !info.ready; }
    function render(state = generator.state()) {
        const running = state.running;
        const isMissing = missing();
        root.dataset.state = isMissing ? 'missing' : running ? 'running' : generator.done() ? 'done' : state.error ? 'failed' : 'idle';
        field.readOnly = running;
        if (picker) picker.element.inert = running;
        action.hidden = !!isMissing;
        // One accent button at a time: Generate until the five pictures exist, then Regenerate is neutral.
        const finishedAll = generator.done() && !running;
        action.textContent = running ? tr('Stop', '停止') : finishedAll ? tr('Regenerate', '重新生成') : tr('Generate', '生成');
        action.dataset.primary = String(!running && !finishedAll);
        const finished = state.pictures.filter(row => row.file).length;
        const currentFraction = running && state.current >= 0 ? state.pictures[state.current]?.fraction || 0 : 0;
        const progress = generator.done() ? 1 : (finished + currentFraction) / names.length;
        state.pictures.forEach((row, i) => {
            const cell = cells[i];
            const active = running && i === state.current && !row.file;
            const src = row.file ? url(row.file) : '';
            if (cell.image.getAttribute('src') !== src) src ? cell.image.src = src : cell.image.removeAttribute('src');
            cell.cell.dataset.state = row.file ? 'done' : active ? 'running' : 'empty';
            if (scene) cell.cell.setAttribute('aria-pressed', String(!!row.file && i === chosen));
            cell.cell.setAttribute('aria-label', cell.title + (row.file ? '' : active
                ? ' ' + Math.floor(row.fraction * 100) + '%' : ''));
            cell.percent.textContent = active && !state.waiting ? Math.floor(row.fraction * 100) + '%' : '';
            cell.fill.style.width = (active ? row.fraction * 100 : 0) + '%';
        });
        if (scene) {
            const file = generator.done() && !running ? state.pictures[chosen]?.file || '' : '';
            const shown = file ? url(file) : '';
            stage.hidden = !shown; if (stageImage.getAttribute('src') !== shown) shown ? stageImage.src = shown : stageImage.removeAttribute('src');
            if (file && file !== picked) onPick(picked = file);
            if (!file && !running) picked = '';
        }
        if (state.error) {
            const key = `${state.error}:${state.report_id || ''}:${state.stage || ''}:${state.memory || ''}`;
            if (failureKey !== key) { renderImageFailure(note, {...state, info}, tr, {fetchApi: fetcher}); failureKey = key; }
        } else {
            failureKey = null;
            note.textContent = isMissing ? imagePackUnavailableMessage(info, tr)
                : running && state.waiting ? tr('Added to the queue. It starts when the previous one finishes.',
                    '已加入队列，前面的完成后自动开始。')
                : attention ? attention : '';
        }
        note.hidden = !note.textContent && !note.firstChild;
        note.dataset.failed = String(!!state.error);
        // The last picture lands a moment before the run ends: done means finished, not just five files.
        const complete = generator.done() && !running;
        const summary = JSON.stringify([running, complete, Math.round(progress * 100)]);
        if (summary !== last) {
            last = summary;
            onState({busy: running, progress, done: complete});
        }
    }

    async function checkInfo() {
        try { info = await client.info(); } catch { info = null; }
        if (disposed) return;
        render();
        clearTimeout(infoTimer);
        if (missing()) infoTimer = setTimeout(checkInfo, IMAGE_GEN_INFO_POLL_MS);
    }

    function begin() {
        if (!field.value.trim()) {
            attention = kind === 'prop' ? tr('Describe the prop first.', '请先描述道具。') : scene ? tr('Describe the scene first.', '请先描述场景。') : tr('Describe the character first.', '请先描述角色。');
            render(); field.focus();
            return;
        }
        attention = null; chosen = 0;
        const selected = picker ? picker.value() : loras;
        void generator.start(field.value, picker && !selected.length ? {} : {loras: selected});
    }
    action.onclick = () => generator.state().running ? void generator.stop() : begin();
    field.addEventListener('input', () => {
        if (attention) { attention = null; render(); }
        if (field.value.trim() !== generator.state().description) generator.reset();
    });
    // Enter in the description box is a new line; Ctrl/Cmd+Enter generates.
    field.addEventListener('keydown', event => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !generator.state().running) { event.preventDefault(); begin(); }
    });

    render();
    void checkInfo();
    return {
        getPictures: () => !generator.done() || generator.state().running ? []
            : scene ? [{file: generator.state().pictures[chosen].file, label: 'other'}]
            : generator.state().pictures.map(row => ({file: row.file, label: row.view})),
        isBusy: () => generator.state().running,
        cancel: () => generator.stop(),
        destroy() { disposed = true; picker?.dispose(); clearTimeout(infoTimer); otherUsage.dispose(); void generator.dispose(); root.remove(); },
        element: root,
    };
}
