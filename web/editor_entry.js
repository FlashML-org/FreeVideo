// The existing views call these small adapters; editor logic stays in editor.js.
import {openEditor} from './editor.js';
import {api} from '../../scripts/api.js';
import {app} from '../../scripts/app.js';
import {openStudio, showNodes} from './studio.js';
import {cleanOptionalPrompt, clonePrompt} from './optional_inputs.js';
import {openAutopilot, viewChanged} from './view_navigation.js';
import {mediaReferences} from './editor_media_references.js';
import {turnNodeOn} from './studio_queue.js';
function editorPanel(node) {
    const values = Object.fromEntries((node.widgets || []).filter(widget => widget.name).map(widget => [widget.name, widget.value]));
    return {node: node.id, width: values.width, height: values.height, text: values.text || '', settings: Object.fromEntries(Object.entries({
        two_pass: values.two_pass, base_steps: values.base_steps, refine_steps: values.refine_steps,
        step_cache: values.step_cache, upscale_factor: values.upscale_factor, seed: values.seed, sampling_effort: values.sampling_effort}).filter(([, value]) => value !== undefined)),
        references: mediaReferences(node),
        async snapshot() {
            if (node.freevideoEditorLooping?.()) throw new Error('loop_active');
            // A muted or bypassed node would be left out of the request; if no request comes out, it stays as it was.
            const held = turnNodeOn(node);
            let value;
            try { value = cleanOptionalPrompt(clonePrompt(await app.graphToPrompt())); } catch (error) { held?.restore(); throw error; }
            held?.done();
            return {node: String(node.id), prompt: value.output, extra_data: {extra_pnginfo: {workflow: value.workflow || {}}}};
        }};
}
export function openEditorView(node, {studio, work = null, extend = false, onProjectLoaded} = {}) {
    studio?.querySelectorAll('video,audio').forEach(media => media.pause());
    const panel = editorPanel(node);
    if (onProjectLoaded) panel.onProjectLoaded = onProjectLoaded;
    const progressElement = studio?.querySelector('.fv-generation-progress');
    if (progressElement) {
        panel.progressElement = progressElement;
        panel.progressSnapshot = message => {
            if (node.freevideoProgress?.chain?.id !== message.chain?.id || node.freevideoProgress?.chain?.block !== message.chain?.block || progressElement.hidden) return null;
            const number = progressElement.querySelector('.fv-generation-count')?.textContent?.match(/(\d+)%/);
            return number ? {percent: Number(number[1])} : {percent: 0};
        };
        // What the creation panel is saying now; the editor covers that panel, so its bar repeats the words.
        panel.progressWords = () => {
            if (progressElement.hidden) return null;
            const words = selector => {
                const element = progressElement.querySelector(selector);
                return element && !element.hidden && element.getClientRects().length ? element.textContent.replace(/\s+/g, ' ').trim() : '';
            };
            // The block already shows the overall percentage; the bar keeps only the stage sentence.
            const stage = words('.fv-generation-label').replace(/（本步 \d+%）$| \(\d+% of this step\)$/, '');
            const eta = progressElement.querySelector('.fv-generation-eta');
            const shown = eta && !eta.hidden ? eta.dataset : null, left = shown?.seconds != null ? Number(shown.seconds) : null;
            return {stage, time: words('.fv-generation-status'), stall: words('.fv-generation-stall'),
                left: Number.isFinite(left) ? left : null, over: words('.fv-generation-late') !== ''};
        };
    }
    const handle = openEditor({api, app, panel, from: work ? {work, ...(extend ? {extend: true} : {})} : null, onView(view, options = {}) {
        if (view === 'nodes') showNodes(node, studio);
        else if (view === 'autopilot') openAutopilot(node, {studio, ...options});
        else if (studio?.open) viewChanged('studio', node);
        else openStudio(node);
    }});
    viewChanged('editor', node);
    // Every route into Edit (including the first video's next step) offers the same contextual card.
    Promise.resolve(handle.ready).then(() => import('./feature_tour.js')).then(({triggerFeatureTour}) => {
        const choice = new URLSearchParams(location.search).get('freevideo_lang');
        const cn = choice === 'zh' || (choice !== 'en' && String(navigator.language || '').toLowerCase().startsWith('zh'));
        return triggerFeatureTour('editor', {api, cn, owner: handle.element});
    }).catch(() => {});
    return handle;
}
