// "Generate with AI" page of the new-character dialog: one description becomes five pictures of the
// same person (front, three-quarter, side, back, face). Each picture shows as it is finished; the
// dialog's Save waits for all five. Pictures are made one by one through the page's image client
// and stay in the input folder; the dialog puts them on the card.
import { setTip } from './tip_words.js';
import {createImageGenClient, imageGenProgress, imagePackUnavailableMessage, renderImageFailure, originStep,
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
    stage: error?.stage || '', step: originStep(error, 'cast'), fitted: error?.fitted || null, memory: error?.memory || '', other_usage: error?.other_usage || null});
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
// With new clothes (described or from a picture) only the body comes from the photo; the clothes come from the new ones.
const BODY_ONLY = 'The body shape, height and skin tone are the same as the person in <Picture 1>.';
const SWAP = 'These clothes take the place of the clothes in <Picture 1>.';
// With new clothes the side and back see the close-up (<Picture 1>) and the front (<Picture 2>), and the photo stays out:
// its clothes would come back.
const NEW_WHO = '<Picture 1> is a close-up of this person\'s face and <Picture 2> is the full-body front view of this person in the clothes to wear.';
const PHOTO_END = 'A real photograph of one person, sharp focus.';
export const PHOTO_PROMPTS = Object.freeze({
    // From the whole photo these words keep the likeness best with Qwen (the edit words that turn the head to the front
    // score lower); kept as measured, "Image 1" included.
    face: 'Image 1 shows the person. A tight close-up portrait of this person: the face from the forehead to the chin fills about '
        + 'half of the picture height, the eyes lie on the upper third line, the top of the hair reaches the top edge, and the neck '
        + 'and the tops of the shoulders fill the lower part; the person faces the camera at eye level with a neutral expression; the eyes, nose, lips and skin are sharp. The face shape, eyes, nose, mouth, age, hairline, '
        + 'hairstyle, glasses and accessories match image 1. On a plain light grey studio backdrop with soft even light. One '
        + 'photograph of one person.',
    face_h3: '把<Picture 1>改成同一个人的近景正面特写照片：画面从头顶到下巴，再带一点肩膀，人物正对镜头，眼睛平视镜头。'
        + '脸型、眼睛、眉毛、鼻子、嘴巴、肤色、年龄、发型、发际线、表情和光线都与<Picture 1>中的人一致。'
        + '背景是干净的浅灰色，五官和皮肤纹理清晰，真实照片。',
    front: '{WHO} Turn this person into a full-body front photograph: standing and facing the camera, the whole body from the top '
        + `of the head to the feet, the arms relaxed at the sides, ${BACKDROP}. The face shape, eyes, eyebrows, nose, mouth, skin `
        + `tone, age, hairstyle and hairline are the same as the face in <Picture 2>. {BODY} {CLOTHES} ${PHOTO_END}`,
    side: '{WHO} Turn this person into a full-body side view: the whole body turned 90 degrees to the left, the nose, chest and '
        + 'toes pointing to the left edge of the picture, one eye visible, the near shoulder in front of the far shoulder, the arms '
        + `relaxed, the whole body from the top of the head to the feet, ${BACKDROP}. The face, seen from the side, has the same `
        + 'shape, eyebrows, nose, lips, skin tone and age as the face in <Picture 2>, and the hairstyle and hairline are the same '
        + `as in <Picture 2>. {BODY} {CLOTHES} ${PHOTO_END}`,
    back: '{WHO} Turn this person into a full-body view from directly behind: the back of the head, the hair, the shoulders, the '
        + 'back, the back of the clothes and the heels face the camera, the head and the body point the same way, the arms '
        + `relaxed, the whole body from the top of the head to the feet, ${BACKDROP}. The hair colour, hairstyle and hairline are `
        + `the same as in <Picture 2>. {BACKBODY} {CLOTHES} ${PHOTO_END}`,
});
// The close-up's model from the engine's model list: Qwen-Image 2.1 when ready, else H3 Image when ready, else none named.
export function photoFaceModel(info) {
    const ready = id => Array.isArray(info?.models) && info.models.some(row => row?.id === id && row.ready === true);
    return ready(PHOTO_FACE_MODELS.qwen) ? PHOTO_FACE_MODELS.qwen : ready(PHOTO_FACE_MODELS.h3) ? PHOTO_FACE_MODELS.h3 : '';
}
// From an uploaded picture of any style (a drawing, a render, a photo): the same steps and words as from a photo, in the
// picture's own style. The close-up has its own words (the photo's stay as measured); the front, side and back swap the
// "real photograph" for the picture's style.
const STYLE = 'The art style, line work, colours and shading are the same as in <Picture 1>.';
export const IMAGE_PROMPTS = Object.freeze({
    face: 'Image 1 shows the character. A tight close-up portrait of this character: the face from the forehead to the chin '
        + 'fills about half of the picture height, the eyes lie on the upper third line, the top of the hair reaches the top edge, and '
        + 'the neck and the tops of the shoulders fill the lower part; the character faces the viewer at eye level with a neutral expression. The face shape, eyes, nose, mouth, age, hairline, hairstyle, glasses and accessories '
        + 'match image 1, and the art style, line work, colours and shading are the same as in image 1. On a plain light grey '
        + 'backdrop with soft even light. One picture of one character.',
    face_h3: '把<Picture 1>改成同一个角色的近景正面特写：画面从头顶到下巴，再带一点肩膀，角色正对画面，眼睛平视。'
        + '脸型、眼睛、眉毛、鼻子、嘴巴、肤色、年龄、发型、发际线和表情都与<Picture 1>中的角色一致，画风、线条、色彩和明暗也与<Picture 1>一致。'
        + '背景是干净的浅灰色，五官清晰。',
});
const inStyle = prompt => prompt.replace('a full-body front photograph', 'a full-body front view').replace(PHOTO_END, `${STYLE} One picture of one character, sharp focus.`);
// From a description: the front first, from the words (wearing the clothes of a clothes picture, <Picture 1>, when one is
// given); then the face close-up from the front; then the side and back from the close-up and the front, as from a photo
// with new clothes. The style is the one the description gives, so these words name the framing and take the style from
// the front.
export const AI_PROMPTS = Object.freeze({
    front: `{WHO}. {WEAR}One person stands alone and faces the camera: the whole body from the top of the head to the feet, the arms relaxed at the sides, ${BACKDROP}, sharp focus.`,
    face: 'Image 1 shows the person. A tight close-up portrait of this person: the face from the forehead to the chin fills about '
        + 'half of the picture height, the eyes lie on the upper third line, the top of the hair reaches the top edge, and the neck '
        + 'and the tops of the shoulders fill the lower part; the person faces the camera at eye level with a neutral expression; the eyes, nose, lips and skin are sharp. The face shape, eyes, nose, mouth, age, hairline, '
        + 'hairstyle, glasses, accessories and the clothes at the collar match image 1, and the style of the picture is the same as '
        + 'image 1. On a plain light grey studio backdrop with soft even light. One picture of one person.',
    face_h3: '把<Picture 1>中的人改成同一个人的近景正面特写：画面从头顶到下巴，再带一点肩膀，人物正对镜头，眼睛平视镜头。'
        + '脸型、眼睛、眉毛、鼻子、嘴巴、肤色、年龄、发型、发际线、领口的衣服和画面风格都与<Picture 1>一致。背景是干净的浅灰色，五官清晰。',
});
// The side or back from the close-up and the front only: the words of PHOTO_PROMPTS, numbered for the two pictures (the
// close-up becomes <Picture 1>, the front <Picture 2>). end: the last sentence, before the numbering.
function fromCloseUpAndFront(name, clothes, end = PHOTO_END) {
    return PHOTO_PROMPTS[name].replace('{WHO}', '').replace('{BODY}', '').replace('{BACKBODY}', '').replace('{CLOTHES}', clothes).replace(PHOTO_END, end)
        .replace(/<Picture 2>/g, '<Picture 1>').replace(/<Picture 3>/g, '<Picture 2>').replace(/^ */, NEW_WHO + ' ').replace(/ {2,}/g, ' ');
}
// The words for repainting a close-up's collar and shoulders in the new clothes (image 1: the close-up; image 2: the clothes
// picture). The clothes are shown, never named here: words that name trousers or shoes made the model draw a small whole
// body under the head, so described clothes are first drawn as a clothes picture (CLOTHES_PROMPT).
function dressWords(style = false) {
    return 'Image 1 shows a person; image 2 shows the clothes to wear. A head-and-shoulders portrait of this person wearing the clothes '
        + 'from image 2 at the collar and the shoulders; the face, the hair and the backdrop are the same as in image 1. '
        + (style ? 'The style is the same as in image 1. One picture of one character.' : 'One photograph of one person.');
}
// Described clothes as a picture: a flat lay of every piece, which then dresses the close-up, the front, side and back.
export const CLOTHES_PROMPT = 'A flat-lay product photograph seen from above on a plain light grey background: {OUTFIT}, every piece '
    + 'laid out neatly side by side on its own, soft even light, sharp focus.';
// plan: the engine's view list (for the sizes); outfit: words for the clothes; outfitImage: a clothes picture is given;
// frontAsThird: the side and back also see the front (<Picture 3>) for the clothes; faceModel: photoFaceModel's answer;
// style: the picture is not a photo (an uploaded picture of any style), and the views keep its style.
export function photoPlan(plan, outfit = '', {outfitImage = false, frontAsThird = true, faceModel = '', style = false} = {}) {
    outfit = String(outfit || '').trim().replace(/[.。]+$/, '');
    const size = name => { const view = plan.find(row => row.view === name) || plan.find(row => row.view === 'front') || {}; return {width: view.width || 832, height: view.height || 1472}; };
    const asPhoto = 'The clothes, shoes and accessories are the same as in <Picture 1>.';
    const changed = outfitImage || !!outfit, body = changed ? BODY_ONLY : BODY_SAME;
    const frontClothes = outfitImage ? `This person wears the clothes, shoes and accessories shown in <Picture 3>${outfit ? `, with these details: ${outfit}` : ''}. ${SWAP}`
        : outfit ? `This person wears: ${outfit}. ${SWAP}` : asPhoto;
    const laterClothes = frontAsThird ? `The clothes, shoes and accessories are the same as in <Picture 3>, the front view${!outfitImage && outfit ? `: ${outfit}` : ''}.`
        : outfit ? `This person wears: ${outfit}. ${SWAP}` : asPhoto;
    const viewModel = faceModel === PHOTO_FACE_MODELS.h3 ? {model: faceModel} : {};
    const later = name => changed && frontAsThird ? {view: name, ...size(name), ...viewModel, refs: ['face', 'front'],
        // the same words, numbered for the two pictures: the close-up becomes <Picture 1>, the front <Picture 2>
        prompt: fromCloseUpAndFront(name, `The body shape, clothes, shoes and accessories are the same as in <Picture 3>, the front view${!outfitImage && outfit ? `: ${outfit}` : ''}.`)}
        : {view: name, ...size(name), ...viewModel, refs: ['photo', 'face', ...(frontAsThird ? ['front'] : [])],
            prompt: PHOTO_PROMPTS[name].replace('{WHO}', frontAsThird ? WHO_AND_FRONT : WHO).replace('{BODY}', body)
                .replace('{BACKBODY}', 'The body shape is the same as the person in <Picture 1>.').replace('{CLOTHES}', laterClothes)};
    const made = [
        {view: 'face', width: 1024, height: 1280, refs: ['photo'], ...(faceModel ? {model: faceModel} : {}),
            prompt: (style ? IMAGE_PROMPTS : PHOTO_PROMPTS)[faceModel === PHOTO_FACE_MODELS.h3 ? 'face_h3' : 'face'],
            // New clothes: the finished close-up is repainted below the chin and beside the neck only (a mask the page
            // makes from the face box), so the face keeps its pixels. Masks are edited with Qwen-Image 2.1.
            // Described clothes are drawn as a clothes picture first (the generator does it when it can repaint).
            ...(outfitImage && faceModel !== PHOTO_FACE_MODELS.h3 ? {dress: {outfit: true, prompt: dressWords(style)}} : {})},
        {view: 'front', ...size('front'), ...viewModel, refs: ['photo', 'face', ...(outfitImage ? ['outfit'] : [])],
            prompt: PHOTO_PROMPTS.front.replace('{WHO}', WHO + (outfitImage ? ' <Picture 3> shows the clothes to wear.' : '')).replace('{BODY}', body).replace('{CLOTHES}', frontClothes)},
        later('side'),
        later('back'),
    ];
    return style ? made.map(view => view.view === 'face' ? view : {...view, prompt: inStyle(view.prompt)}) : made;
}

// From a description: front, face close-up, side, back, in the order they are made. plan: the engine's view list (for the
// front's size); outfitImage: a clothes picture is given; faceModel: photoFaceModel's answer (Qwen-Image 2.1 makes the
// close-up when ready; with H3 Image the only one ready, every view names it).
export function aiPlan(plan, description, {outfitImage = false, faceModel = ''} = {}) {
    const who = String(description || '').trim().replace(/[.。]+$/, '');
    const front = plan.find(row => row.view === 'front') || {}, size = {width: front.width || 832, height: front.height || 1472};
    const h3 = faceModel === PHOTO_FACE_MODELS.h3, viewModel = h3 ? {model: faceModel} : {};
    const later = name => ({view: name, ...size, ...viewModel, refs: ['face', 'front'],
        prompt: fromCloseUpAndFront(name, 'The body shape, clothes, shoes and accessories are the same as in <Picture 3>, the front view.',
            'The style of the picture is the same as in <Picture 3>. One picture of one person, sharp focus.')});
    return [
        {view: 'front', ...size, ...viewModel, refs: outfitImage ? ['outfit'] : [],
            prompt: (outfitImage ? '<Picture 1> shows the clothes to wear. ' : '') + AI_PROMPTS.front.replace('{WHO}', who)
                .replace('{WEAR}', outfitImage ? 'This person wears the clothes, shoes and accessories shown in <Picture 1>. ' : '')},
        {view: 'face', width: 1024, height: 1280, refs: ['front'], ...(faceModel ? {model: faceModel} : {}), prompt: AI_PROMPTS[h3 ? 'face_h3' : 'face']},
        later('side'),
        later('back'),
    ];
}

// The pictures of one set: the engine's views of a description (or a scene's three candidates), or a character's four
// views made by the page's plan from a photo, a picture or a description. No DOM: the page below draws its state.
// facing: async (made picture) => whether it shows a face toward the camera; a back that does is drawn once more.
// framing: async (made close-up) => the face's height in the picture's height (null where unknown); a close-up under framingMin
// (cropped at the waist, a half-length portrait) is drawn once more from another seed, and the larger face stays.
// dressTook: async (close-up, repainted, mask) => whether the repaint changed the clothes; one that did not is tried once more,
// from dressNeutral's answer (async close-up => the close-up with its clothes greyed out, '' for none) where there is one.
export function createCastGenerator({client, kind = 'character', views = null, frontAsThird = true, dressMask = null, dressTook = null, dressNeutral = null, facing = null, framing = null, framingMin = .21, onChange = () => {}, beforeGenerate = async () => {}, onDiscard = () => {}, schedule = setTimeout, unschedule = clearTimeout}) {
    const scene = kind === 'setting' || kind === 'prop', names = views || (scene ? SCENE_CANDIDATES : CAST_VIEWS);
    const fresh = () => names.map(view => ({view, file: '', fraction: 0}));
    let state = {pictures: fresh(), running: false, current: -1, error: '', report_id: null, waiting: '', description: '', photo: '', outfit: ''};
    let epoch = 0, timer = null, job = null, plan = null, seed = 0, disposed = false, loras, outfit = '', inputs = '', given = '';
    // Described clothes on a photo or a picture: the clothes picture drawn from the words ({words, file}), or null.
    let clothes = null;
    // From a photo with a small face: the face cut out and enlarged by the page, for the face close-up only.
    let closeUp = '';
    const seeds = [];
    // Pictures standing in for a view as a reference (an edited face or front, uploaded by the page).
    const overrides = {};
    const emit = patch => { state = {...state, ...patch}; if (!disposed) onChange(state); };
    const setPicture = (index, patch) => emit({pictures: state.pictures.map((row, i) => i === index ? {...row, ...patch} : row)});
    const cancelJob = id => Promise.resolve().then(() => client.cancel(id)).catch(() => {});
    const done = () => state.pictures.every(row => row.file);
    // The set starts again: what was made goes, the pictures the user put in stay (own rows).
    const kept = () => state.pictures.map(row => row.own ? row : {view: row.view, file: '', fraction: 0});

    // over: the dress pass of a close-up ({file: the close-up, mask: the uploaded mask, again: the second try}).
    async function runOne(index, alive, over = null) {
        const view = plan[index], front = state.pictures[0].original && !state.pictures[0].own ? state.photo : state.pictures[0].file;
        const dress = view.outfit && state.outfit ? [state.outfit] : [];
        // From a photo: each view names the pictures it sees (the photo, the face close-up, the front, the clothes).
        // A picture the user put in is an upload of its own (row.file), not the photo.
        const picked = name => { const at = names.indexOf(name), row = at >= 0 ? state.pictures[at] : null; return row?.original && !row.own ? state.photo : row?.file || ''; };
        // The face close-up sees the face cut out of the photo where the page made one (a small face), else the photo.
        const roles = {photo: () => (view.view === 'face' && closeUp) || state.photo, face: () => overrides.face || picked('face'), front: () => overrides.front || picked('front'), outfit: () => state.outfit};
        const images = over ? [over.file, ...(view.dress.outfit && state.outfit ? [state.outfit] : [])]
            : view.refs ? view.refs.map(role => roles[role]?.() || '').filter(Boolean)
            : view.photo ? [state.photo, ...dress] : view.from_front ? [front, ...dress] : [];
        await beforeGenerate({kind: 'image', width: view.width, height: view.height, image_references: images.length});
        if (!alive()) return null;
        // Every picture draws from a seed of its own. A view started from the same seed as the picture it is made from
        // (at the same size, or one the engine scales the reference to) grows a mottled lattice over the backdrop and
        // the clothes, and a repaint started from the close-up's own seed a scaly one over the neck.
        const own = seeds[index] ?? seed + index;
        const row = await client.generate({...(view.model ? {model: view.model} : {}), prompt: over ? view.dress.prompt : view.prompt, width: view.width, height: view.height, seed: over ? own + (over.again ? 2 : 1) * names.length + index + 1 : own,
            images, ...(over ? {mask: over.mask} : {}), ...(view.view === 'back' && images.length === 1 && !over ? {cast_view: 'back'} : {}),
            ...(loras === undefined ? {} : {loras})});
        return waitFor(row, alive, status => setPicture(index, {fraction: Math.max(state.pictures[index].fraction,
            imageGenProgress(status.stage, status.fraction, view.from_front)), stage: status.stage || ''}));
    }
    // A sent request until its picture is made (the picture's name), or null when the run was stopped.
    async function waitFor(row, alive, progress) {
        if (!row.job) throw Object.assign(new Error(row.error || 'generation_failed'), {code: row.error, report_id: row.report_id, memory: row.memory, step: originStep({code: row.error, report_id: row.report_id}, 'submit')});
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
                if (!status.file) throw Object.assign(new Error('generation_failed'), {step: 'result_missing'});
                return status.file;
            }
            progress(status);
            emit({waiting: status.waiting || ''});
        }
    }
    // The clothes picture of described clothes, drawn before the first view (Qwen-Image 2.1, a seed of its own).
    async function drawClothes(alive) {
        const query = {kind: 'image', width: 1024, height: 1024, image_references: 0};
        await beforeGenerate(query);
        if (!alive()) return null;
        const row = await client.generate({model: PHOTO_FACE_MODELS.qwen, prompt: CLOTHES_PROMPT.replace('{OUTFIT}', clothes.words), width: 1024, height: 1024,
            seed: seed + 4 * names.length + 1, images: []});
        const file = await waitFor(row, alive, () => {});
        if (file !== null) job = null;
        return file;
    }
    // The drawn clothes picture goes: the set starts again, or Generate all again draws it anew.
    function dropClothes() {
        if (clothes?.file) onDiscard(clothes.file);
        clothes = null;
        if (state.outfit !== given || state.extras?.length) emit({outfit: given, extras: []});
    }

    // A close-up made for new clothes is repainted at the collar and the shoulders (the page's mask); a failed or skipped
    // repaint keeps the close-up as made. null: the run was stopped.
    async function dressed(index, file, alive) {
        const view = plan[index];
        if (!view.dress || !dressMask) return file;
        let mask = '';
        try { mask = await dressMask(file); } catch { mask = ''; }
        if (!alive()) return null;
        if (!mask) return file;
        try {
            let made = await runOne(index, alive, {file, mask});
            if (made === null) return null;
            job = null;
            // A repaint that kept the old clothes (the same garment in another colour, at times) is tried once more with
            // another seed; the second one stays.
            let took = true;
            if (dressTook) { try { took = await dressTook(file, made, mask); } catch { took = true; } }
            if (!alive()) return null;
            if (!took) {
                try {
                    let base = '';
                    if (dressNeutral) { try { base = await dressNeutral(file); } catch { base = ''; } }
                    if (!alive()) return null;
                    const again = await runOne(index, alive, {file: base || file, mask, again: true});
                    if (again === null) return null;
                    job = null; onDiscard(made); made = again;
                } catch { const id = job; job = null; if (id) void cancelJob(id); if (!alive()) return null; }
            }
            onDiscard(file);
            return made;
        } catch {
            const id = job; job = null; if (id) void cancelJob(id);
            return alive() ? file : null;
        }
    }

    // A back view that came out facing the camera (the face found on it) is drawn once more from another seed; the second
    // one stays whatever it shows, and a failed redraw keeps the first. null: the run was stopped.
    async function turned(index, file, alive) {
        if (plan[index].view !== 'back' || !facing) return file;
        let face = false;
        try { face = await facing(file); } catch { face = false; }
        if (!alive()) return null;
        if (!face) return file;
        const before = seeds[index];
        seeds[index] = randomSeed(); setPicture(index, {fraction: 0});
        try {
            const again = await runOne(index, alive);
            if (again === null) return null;
            job = null; onDiscard(file);
            return again;
        } catch {
            const id = job; job = null; if (id) void cancelJob(id);
            if (before === undefined) delete seeds[index]; else seeds[index] = before;
            return alive() ? file : null;
        }
    }
    // A face close-up that came out framed too wide (the head small in a half-length picture) is drawn once more from another
    // seed; whichever shows the larger face stays, and a failed redraw keeps the first. null: the run was stopped.
    async function framed(index, file, alive) {
        if (plan[index].view !== 'face' || !framing) return file;
        let share = null;
        try { share = await framing(file); } catch { share = null; }
        if (!alive()) return null;
        if (!Number.isFinite(share) || share >= framingMin) return file;
        const before = seeds[index];
        seeds[index] = randomSeed(); setPicture(index, {fraction: 0});
        const restore = () => { if (before === undefined) delete seeds[index]; else seeds[index] = before; };
        try {
            const again = await runOne(index, alive);
            if (again === null) return null;
            job = null;
            let second = null;
            try { second = await framing(again); } catch { second = null; }
            if (!alive()) return null;
            if (second > share) { onDiscard(file); return again; }
            onDiscard(again); restore();
            return file;
        } catch {
            const id = job; job = null; if (id) void cancelJob(id);
            restore();
            return alive() ? file : null;
        }
    }
    // A made picture as it goes on the sheet: a close-up framed again when it came out too wide, then dressed in new clothes;
    // a back facing the camera drawn again.
    async function finished(index, made, alive) {
        const wide = await framed(index, made, alive);
        if (wide === null) return null;
        const file = await dressed(index, wide, alive);
        return file === null ? null : turned(index, file, alive);
    }

    // source: 'photo' or 'image' (made from the picture `photo`: photoPlan), 'ai' (made from the description: aiPlan); none:
    // the engine's views of the description.
    async function start(description, {loras: selectedLoras, source = '', photo = '', closeUp: face = '', outfit: wear = '', outfitImage = ''} = {}) {
        description = String(description || '').trim();
        if (photo && !source) source = 'photo';
        if (photo && !description) description = PHOTO_WHO;
        if (state.running || disposed || !description || (source && source !== 'ai' && !photo)) return;
        closeUp = photo ? face : '';
        // Another source, picture or clothes: the set starts again and the pictures made before go.
        const asked = source ? JSON.stringify([source, photo, wear, outfitImage]) : '';
        if (source && asked !== inputs) { inputs = asked; plan = null; dropClothes(); given = outfitImage; emit({photo, outfit: outfitImage}); outfit = wear; for (const row of state.pictures) if (row.file && !row.original) onDiscard(row.file); emit({pictures: kept(), description: ''}); }
        // After a failure the same description goes on from the picture that failed.
        const resume = !!state.error && description === state.description && plan && !done()
            && JSON.stringify(selectedLoras) === JSON.stringify(loras);
        const current = ++epoch;
        const alive = () => current === epoch && !disposed;
        if (!resume) {
            seed = randomSeed(); plan = null; seeds.length = 0; dropClothes(); loras = structuredClone(selectedLoras);
            // Generating all again replaces every picture: the ones made before are let go (deleted by the caller).
            for (const row of state.pictures) if (row.file && !row.original) onDiscard(row.file);
        }
        emit({running: true, error: '', report_id: null, waiting: '', description,
            ...(resume ? {} : {pictures: kept(), current: 0})});
        try {
            if (!plan) {
                plan = await client.castViews(description, kind);
                if (scene) plan = names.map(() => plan[0]);
                if (source) {
                    // The close-up's model from the engine's newest list (a model may have finished installing since the page opened).
                    let faceModel = '';
                    try { faceModel = photoFaceModel(await client.info?.()); } catch { /* none named: the engine's own choice */ }
                    if (!alive()) return;
                    // Described clothes, where the close-up can be repainted: drawn as a clothes picture first, then the set
                    // is made as with a clothes picture, the words kept as details.
                    const drawn = source !== 'ai' && !!outfit && !state.outfit && !!dressMask && faceModel === PHOTO_FACE_MODELS.qwen;
                    if (drawn) clothes = {words: outfit, file: ''};
                    const made = source === 'ai' ? aiPlan(plan, description, {outfitImage: !!state.outfit, faceModel})
                        : photoPlan(plan, outfit, {outfitImage: !!state.outfit || drawn, frontAsThird, faceModel, style: source === 'image'});
                    plan = names.map(name => made.find(view => view.view === name)).filter(Boolean);
                }
                if (!alive()) return;
            }
            if (clothes && !clothes.file) {
                emit({current: 0});
                const file = await drawClothes(alive);
                if (file === null || !alive()) return;
                clothes.file = file; emit({outfit: file, extras: [file]});
            }
            for (let index = 0; index < plan.length; index++) {
                if (state.pictures[index].file) continue;
                emit({current: index});
                const made = await runOne(index, alive);
                if (made === null || !alive()) return;
                job = null;
                const file = await finished(index, made, alive);
                if (file === null || !alive()) return;
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
        ++epoch; unschedule(timer); timer = null; plan = null; clothes = null;
        // A stopped run is dropped whole: the next Generate starts again from the description.
        emit({running: false, current: -1, waiting: '', error: '', report_id: null, pictures: kept()});
        return id ? cancelJob(id) : Promise.resolve();
    }
    // The description changed: pictures made from the old one no longer belong to it.
    function reset() {
        if (state.running || !(state.pictures.some(row => row.file) || state.error)) return;
        plan = null;
        emit({pictures: kept(), error: '', report_id: null, current: -1});
    }
    // One view again with a new seed; the other views stay. The replaced picture is discarded when the new one is in.
    async function redo(index) {
        if (state.running || disposed || !plan || !state.pictures[index] || state.pictures[index].own) return;
        const current = ++epoch, alive = () => current === epoch && !disposed;
        const before = state.pictures[index];
        seeds[index] = randomSeed();
        setPicture(index, {file: '', fraction: 0, original: false});
        emit({running: true, current: index, error: '', report_id: null, waiting: ''});
        try {
            const made = await runOne(index, alive);
            if (made === null || !alive()) return;
            job = null;
            const file = await finished(index, made, alive);
            if (file === null || !alive()) return;
            setPicture(index, {file, fraction: 1});
            if (before.file && !before.original) onDiscard(before.file);
            emit({running: false, current: -1, waiting: ''});
        } catch (error) {
            if (!alive()) return;
            const id = job; job = null; if (id) void cancelJob(id);
            setPicture(index, before);
            emit({running: false, current: -1, waiting: '', ...failureOf(error)});
        }
    }
    // A picture the user put in for one view (file: its upload name, or a stand-in until it is uploaded; '' takes it out).
    // Generate then makes only the views that are still empty. A made picture it replaces is discarded.
    function setOwn(index, file) {
        const before = state.pictures[index];
        if (state.running || disposed || !before) return;
        if (file) setPicture(index, {file, fraction: 1, original: true, own: true});
        else if (before.own) setPicture(index, {file: '', fraction: 0, original: false, own: false});
        if (file && before.file && !before.original) onDiscard(before.file);
    }
    // The photo itself as the front (it was uploaded for the reference); the generated front is discarded.
    function useOriginal(file) {
        if (state.running || disposed) return;
        const before = state.pictures[0];
        setPicture(0, {file, fraction: 1, original: true, own: false});
        if (before.file && !before.original) onDiscard(before.file);
    }
    return {start, stop, reset, redo, useOriginal, setOwn, state: () => state, done,
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
        setTip(cell, {name: title}); cell.setAttribute('aria-label', title); cell.setAttribute('role', scene ? 'button' : 'img');
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
