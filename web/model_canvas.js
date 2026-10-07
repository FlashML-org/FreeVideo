// Each video model keeps its own canvas and sampling steps on a FreeVideo node:
// MiniMax H3 a 32-pixel grid and 1-32 steps, Prism (preview) 1280 x 720 for 8.5 s
// and its own quality levels. The creative workspace and the node's Model widget
// share this swap. Only a change of model swaps them; loading a workflow (or
// undo, which reloads it) records the model and keeps every saved value.
import { knownPrismLevels } from './sampling_effort.js';

// The FreeVideoGenerate model combo stores these names; absent means MiniMax H3.
export const H3_MODEL = 'MiniMax H3', PRISM_MODEL = 'Prism (preview)';
// Prism (preview): 24 fps, 4n + 1 frames, sizes in multiples of 16; 1280 x 720 x 205 frames is official.
export const PRISM_OFFICIAL = Object.freeze({width: 1280, height: 720, frames: 205, seconds: 8.5});
const H3_DEFAULT = Object.freeze({width: 1344, height: 768, seconds: 10, base_steps: 8, refine_steps: 3,
    two_pass: true, aspect: '16:9', pixels: .98});
const H3_MAX_STEPS = 32;
const FIELDS = ['width', 'height', 'seconds', 'base_steps', 'refine_steps', 'two_pass'];

// Prism's starter: a short example of its prompt, with the <speech> and <sfx> tags
// of Prism's own examples. Chinese speech is generated as Mandarin.
export const PRISM_STARTER = Object.freeze({
    en: 'Continuous <sfx>soft rain on the window and a distant city hum</sfx> throughout. Medium close-up, eye-level view, '
        + 'warm indoor light. A young woman in a gray sweater sits by a rainy window, holding a mug of tea. She looks up at '
        + 'the camera, smiles and says in a calm voice <speech>The rain finally slowed down. Let\'s go out for a walk.</speech> '
        + 'Then she takes a sip of tea.',
    zh: '持续的<sfx>细雨敲打窗户的声音和远处城市的低鸣</sfx>。近景，平视角度，温暖的室内光线。一位穿灰色毛衣的年轻女性坐在下雨的窗边，'
        + '手捧一杯热茶。她抬头看向镜头，微笑着用平静的语气说<speech>雨终于小了，我们出去走走吧。</speech>然后她低头喝了一口茶。',
});
const languageOverride = typeof location !== 'undefined' ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const chinese = () => languageOverride === 'zh' || (languageOverride !== 'en'
    && String(globalThis.navigator?.language || '').toLowerCase().startsWith('zh'));
const sameText = (a, b) => typeof a === 'string' && typeof b === 'string'
    && a.replace(/\r\n/g, '\n').trim() === b.replace(/\r\n/g, '\n').trim();

const widget = (node, name) => node?.widgets?.find(w => w.name === name);
const linked = (node, name) => node?.inputs?.some(input => input.name === name && input.link != null);
const value = (node, name) => widget(node, name)?.value;
function set(node, name, next) {
    const w = widget(node, name); if (!w || linked(node, name) || w.value === next) return;
    w.value = next; w.callback?.(next); node.graph?.change?.(); node.setDirtyCanvas?.(true, true);
}

// ComfyUI snaps integer widgets to their step: give width / height Prism's 16-pixel grid.
export function modelGrid(node) {
    const step = value(node, 'model') === PRISM_MODEL ? 16 : 32;
    for (const name of ['width', 'height']) {
        const w = widget(node, name);
        if (w?.options) { w.options.step2 = step; w.options.step = 10 * step; }
    }
}

// The model whose canvas the node holds. A combo runs its callback after its
// value changed, so the previous model is remembered here, never serialized.
export function trackModel(node) { if (node) node.freevideoCanvasModel = value(node, 'model') ?? H3_MODEL; }

function snapshot(node) {
    return {width: value(node, 'width'), height: value(node, 'height'), seconds: value(node, 'seconds'),
        base_steps: value(node, 'base_steps'), refine_steps: value(node, 'refine_steps'), two_pass: value(node, 'two_pass'),
        aspect: node.properties?.freevideo_aspect, pixels: node.properties?.freevideo_pixels};
}

// After the model changed: keep the previous model's canvas and steps in the
// node's properties and restore the new model's (its defaults the first time).
// Returns whether it swapped; a second call for the same change does nothing.
export function swapModelCanvas(node, {tiers = knownPrismLevels(), defaultTier} = {}) {
    const next = value(node, 'model') ?? H3_MODEL, previous = node.freevideoCanvasModel;
    node.freevideoCanvasModel = next;
    if (previous === undefined || previous === next) return false;
    const toPrism = next === PRISM_MODEL;
    node.properties ??= {};
    const saved = node.properties.freevideo_model_canvas = {...(node.properties.freevideo_model_canvas || {})};
    saved[toPrism ? 'h3' : 'prism'] = snapshot(node);
    const tier = tiers?.find(row => row.id === value(node, 'prism_quality'))
        || tiers?.find(row => row.id === defaultTier) || tiers?.[0];
    let canvas = saved[toPrism ? 'prism' : 'h3'] || (toPrism
        ? {width: PRISM_OFFICIAL.width, height: PRISM_OFFICIAL.height, seconds: PRISM_OFFICIAL.seconds,
           base_steps: tier?.steps ?? value(node, 'base_steps'), refine_steps: 3, two_pass: false, aspect: '16:9'}
        : H3_DEFAULT);
    const steps = Number(canvas.base_steps);
    // MiniMax H3 samples 1-32 steps: a Prism level's 50 must never carry over.
    if (!toPrism && !(Number.isInteger(steps) && steps >= 1 && steps <= H3_MAX_STEPS))
        canvas = {...canvas, base_steps: H3_DEFAULT.base_steps, refine_steps: H3_DEFAULT.refine_steps, two_pass: H3_DEFAULT.two_pass};
    for (const name of FIELDS) if (canvas[name] !== undefined && canvas[name] !== null) set(node, name, canvas[name]);
    node.properties.freevideo_aspect = canvas.aspect || 'custom';
    if (Number.isFinite(canvas.pixels)) node.properties.freevideo_pixels = canvas.pixels;
    starterPrompt(node, toPrism);
    modelGrid(node);
    return true;
}

// MiniMax H3's untouched default prompt (the node's own default, a multi-shot
// H3 script) becomes Prism's starter, in the interface language, and back.
// Anything the user typed or edited stays as it is.
function starterPrompt(node, toPrism) {
    const text = widget(node, 'text'), h3 = node.freevideoDefaultPrompt;
    if (!text || linked(node, 'text') || typeof h3 !== 'string' || !h3.trim()) return;
    const next = toPrism ? (sameText(text.value, h3) ? PRISM_STARTER[chinese() ? 'zh' : 'en'] : null)
        : (Object.values(PRISM_STARTER).some(starter => sameText(text.value, starter)) ? h3 : null);
    if (next === null) return;
    set(node, 'text', next);
    // The node's and the workspace's prompt editors show the new text.
    if (typeof window !== 'undefined' && typeof CustomEvent !== 'undefined')
        window.dispatchEvent(new CustomEvent('freevideo-reference-prompt', {detail: node.id}));
}

// A Prism-only installation cannot run MiniMax H3, so a node on H3 moves to
// Prism (with Prism's canvas, as when the user switches). `installed` is
// /freevideo/models' list. Returns whether the node changed.
export const prismOnly = installed => Array.isArray(installed) && installed.length === 1 && installed[0] === 'prism';
export function preferInstalledModel(node, installed) {
    const w = widget(node, 'model');
    if (!prismOnly(installed) || !w || linked(node, 'model') || w.value === PRISM_MODEL) return false;
    if (node.freevideoCanvasModel === undefined) trackModel(node);
    w.value = PRISM_MODEL; w.callback?.(PRISM_MODEL);  // the node's Model callback swaps the canvas
    swapModelCanvas(node);  // covers a node without that callback; otherwise nothing is left to swap
    node.graph?.change?.(); node.setDirtyCanvas?.(true, true);
    return true;
}
