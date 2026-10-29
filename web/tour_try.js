// "Try it" for the feature tour: put one ready-made example (its pictures or videos, its prompt and
// its settings) into the creation panel. The panel supplies the writers, so this file only decides
// what goes where, and nothing is written unless every file of the example could be added first.
import {uploadMedia} from './media_upload.js';
import {referenceItems, referenceKind} from './prompt_references.js';

const ASSETS = 'assets/tour/';
const assetURL = name => new URL(`./${ASSETS}${name}`, import.meta.url).href;

// {video1}, {image2} … in a prompt stand for the example's own first video, second image and so on;
// they become the reference tokens the engine assigns, wherever those files end up in the list.
export const TOUR_EXAMPLES = {
    'whitebox-product': {
        assets: [{file: 'whitebox-sneaker.mp4', role: 'reference'}, {file: 'product-sneaker.webp', role: 'reference'}],
        prompt: [
            'The sneaker in {image1} follows the motion and camera of {video1}: it tilts and turns slowly on a clean studio tabletop, soft light sweeping across the upper, sharp detail on the stitching and sole.',
            '{image1} 中的运动鞋沿用 {video1} 的动作和镜头：在干净的摄影台面上缓缓倾斜、转动，柔和的光扫过鞋面，缝线和鞋底清晰锐利。',
        ],
        settings: {},
    },
};

export function exampleAssetURL(entry) { return assetURL(entry.file); }

export function fillPrompt(template, files, items) {
    const token = (kind, n) => items.find(item => item.file === files.filter(f => referenceKind(f) === kind)[n - 1])?.token;
    return template.replace(/\{(video|image|audio)(\d+)\}/g, (all, kind, n) => token(kind, Number(n)) || all);
}

async function fetchFile(entry, fetchAsset) {
    const reply = await fetchAsset(assetURL(entry.file));
    if (!reply.ok) throw new Error(`missing_example_asset:${entry.file}`);
    const blob = await reply.blob();
    if (!blob.size) throw new Error(`missing_example_asset:${entry.file}`);
    return new File([blob], entry.file, {type: blob.type});
}

// panel: {node, api, cn, rows() -> current rows, write(rows) -> bool, setPrompt(text), setSetting(name, value)}.
// Returns {ok: true} or {ok: false, reason}. The existing media rows are kept and the example is added after them,
// except that rows already holding the same example file are not added twice.
export async function applyTourExample(panel, example, {upload = uploadMedia, fetchAsset = fetch} = {}) {
    const entry = typeof example === 'string' ? TOUR_EXAMPLES[example] : example;
    if (!entry) return {ok: false, reason: 'unknown_example'};
    if (entry.status === 'pending-assets') return {ok: false, reason: 'pending-assets'};
    if (panel.canApply?.(entry) === false) return {ok: false, reason: 'unavailable'};
    let uploaded;
    try {
        uploaded = [];
        for (const asset of entry.assets || []) uploaded.push({role: asset.role, result: await upload(panel.api, await fetchFile(asset, fetchAsset))});
    } catch (error) { return {ok: false, reason: 'asset', error}; }
    const names = new Set(panel.rows().map(row => row.file));
    const added = uploaded.filter(({result}) => !names.has(result.file))
        .map(({role, result}) => ({file: result.file, role, enabled: true}));
    if (uploaded.length && !panel.write([...panel.rows(), ...added])) return {ok: false, reason: 'write'};
    const items = referenceItems(panel.node);
    if (entry.prompt) panel.setPrompt(fillPrompt(entry.prompt[panel.cn ? 1 : 0], uploaded.map(({result}) => result.file), items));
    for (const [name, value] of Object.entries(entry.settings || {})) panel.setSetting(name, value);
    return {ok: true, added: added.length};
}
