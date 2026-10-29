import {showHowTo} from './first_run_guide.js';

// Short walk-throughs played over the live interface (the same ring and bubble as the first-run guide).
// A step names its control; when that control is not on screen (nothing generated yet, say) `image` stands in for it.
const shown = node => Boolean(node?.isConnected && node.getClientRects().length);

// The whole Media section, from its title to the last line, and it is opened if it was folded.
const mediaSection = dialog => {
    const title = dialog.querySelector('[data-fv-guide="media"]'), details = title?.closest('details');
    if (!details || !shown(details)) return null;
    const r = details.getBoundingClientRect();
    return {node: details, rect: {left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height}, reveal: () => { details.open = true; }};
};
// Only the part on the timeline: the selected clip or range, else the first part.
const selectedPart = dialog => {
    const find = selector => [...dialog.querySelectorAll(selector)].filter(shown)[0];
    const node = find('.fv-ed-clip[aria-selected="true"]') || find('[data-fv-guide="editor-timeline"] .fv-ed-selection') || find('.fv-ed-clip');
    if (!node) return null;
    const r = node.getBoundingClientRect();
    return {node, rect: {left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height}};
};
const HOWTOS = [
    {id: 'reference-video', view: 'studio', title: ['Make a video from reference images', '参考图生成视频'],
        steps: [
            {id: 'media', target: mediaSection, en: 'Add images here and set each one as a reference, first frame, or last frame.',
                zh: '在此添加图片，每张可设为参\u2060考、首\u2060帧或尾\u2060帧。'},
            {id: 'describe', anchor: '[data-fv-guide="describe"]', en: 'Type @ in the prompt and choose an image, then say what it does in the scene.',
                zh: '在描述中输入 @ 选择图片，并写明它在画面中的作用。'},
            {id: 'media-av', target: mediaSection, en: 'Videos and audio can be references too: add them with "Upload files" and mention them in the prompt with @.',
                zh: '视频和音频也可作为参考：点“上\u2060传\u2060文\u2060件”添加，在描述中输入 @ 引用。'},
            {id: 'generate', anchor: '[data-fv-guide="generate"]', en: 'Choose "Generate video".', zh: '点“生成视频”。'}]},
    {id: 'long-video', view: 'studio', needsParts: true, title: ['Make a longer video', '连续的长视频'],
        steps: [
            {id: 'seconds', anchor: '[data-fv-guide="seconds"]', en: 'Enter a longer duration. It is made in parts automatically.',
                zh: '填写更长的时长，会自动分段生成。'},
            {id: 'generate', anchor: '[data-fv-guide="generate"]', en: 'Choose "Generate video".', zh: '点“生成视频”。'},
            {id: 'extend', anchor: '.fv-action-extend', image: 'extend-button',
                en: 'Choose "Extend" on a finished video to continue it. Each continued part is at least 5 seconds; with a part shorter than 10 seconds, one video has at most 4 parts.',
                zh: '在生成好的视频上点“接着生成”，继续下一段。接着生成的每一段至少 5 秒；有短于 10 秒的段时，一条视频最多 4 段。'},
            {id: 'longer', anchor: '[data-view="editor"]', en: 'Parts can also be added one by one in "Edit".', zh: '也可在“剪辑视图”中逐段接续。'}]},
    {id: 'editor-extend-refs', view: 'editor', title: ['Add reference images when extending in Edit', '在剪辑视图延长时加参考图'],
        steps: [
            {id: 'editor-timeline', target: selectedPart, en: 'Select the part to continue.', zh: '选中要接续的一段。'},
            {id: 'editor-refs', anchor: '[data-fv-guide="editor-refs"]', image: 'editor-refs',
                en: 'Click + next to the prompt to upload or generate images, up to 3.', zh: '点描述框旁的 +，上传或生成图片，最多 3 张。'},
            {id: 'editor-describe', anchor: '[data-fv-guide="editor-describe"]', en: 'Describe it, then choose "Generate".', zh: '填写描述，然后点“生成”。'}]},
];

export const howToList = cn => HOWTOS.map(({id, title}) => ({id, title: title[cn ? 1 : 0]}));

// Devices that cannot make longer videos in parts have no Extend button, so that how-to is not offered.
// An unreadable answer keeps it, as the length field's own line does.
async function canMakeParts() {
    try {
        const {api} = await import('../../scripts/api.js');
        const reply = await api.fetchApi('/freevideo/chains');
        return !(reply?.ok && (await reply.json())?.continuation === false);
    } catch { return true; }
}
export async function howToAvailable(id) {
    const howTo = HOWTOS.find(item => item.id === id);
    return Boolean(howTo) && (!howTo.needsParts || await canMakeParts());
}
export async function availableHowToList(cn) {
    const parts = await canMakeParts();
    return HOWTOS.filter(item => parts || !item.needsParts).map(({id, title}) => ({id, title: title[cn ? 1 : 0]}));
}

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

// Plays one how-to. The editor ones open the Edit view first; the others open the creation panel.
export async function startHowTo(id, {openWorkspace = async () => (await import('./view_navigation.js')).openCreativeWorkspace()} = {}) {
    const howTo = HOWTOS.find(item => item.id === id);
    if (!howTo || !await howToAvailable(id) || !await openWorkspace()) return false;
    if (howTo.view === 'editor') {
        document.querySelector('dialog.fv-studio [data-view="editor"]')?.click();
        for (let i = 0; i < 40 && !document.querySelector('dialog.fv-editor[open]'); i++) await wait(100);
    }
    await document.fonts.ready;
    await wait(250);
    return showHowTo(howTo.steps);
}
