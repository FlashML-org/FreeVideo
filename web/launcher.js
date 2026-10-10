import { app } from '../../scripts/app.js';
import { api } from '../../scripts/api.js';
import { restorePromptDraft } from './prompt_draft.js';
import { takePortMarker } from './port_notice.js';

// health.js names what failed; files loaded before it queue their reports.
const health = item => (window.__freevideoHealth ||= []).push(item);
const studioWorkflow = graph => (graph?._nodes || graph?.nodes || []).some(n => n.comfyClass === 'FreeVideoGenerate' || n.type === 'FreeVideoGenerate')
    && !!graph?.extra?.freevideo_studio;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let configured = performance.now();

// ComfyUI restores the previous session's workflow and tabs after every
// extension's setup, so that restore can bring another workflow to the front
// over ours. Once startup is quiet, bring ours back unless the user has
// already moved on. Never awaited from setup: ComfyUI restores only after
// setup returns.
async function keepInFront(workflow, opened) {
    let touched = false;
    const touch = event => { if (!event.target?.closest?.('.fv-studio, .fv-health')) touched = true; };
    addEventListener('pointerdown', touch, true); addEventListener('keydown', touch, true);
    try {
        const begin = performance.now();
        while (performance.now() - begin < 15000
            && (app.extensionManager?.spinner || performance.now() - configured < 600)) await sleep(100);
        if (studioWorkflow(app.graph) || touched) return;
        const store = app.extensionManager?.workflow;
        // Our own tab when ComfyUI still has it; a new one otherwise.
        const target = opened && store?.getWorkflowByPath?.(opened.path) === opened ? opened : 'FreeVideo';
        await app.loadGraphData(structuredClone(workflow), true, true, target);
    } finally {
        removeEventListener('pointerdown', touch, true); removeEventListener('keydown', touch, true);
    }
}

// Explicit launcher navigation only; ordinary ComfyUI visits restore their own
// graph. Remove the flag before loading so refreshing never resets user edits.
app.registerExtension({
    name: 'FreeVideo.Launcher',
    afterConfigureGraph() { configured = performance.now(); },
    async setup() {
        takePortMarker();
        const url = new URL(window.location.href);
        if (url.searchParams.get('freevideo') !== 'launch') return;
        url.searchParams.delete('freevideo');
        window.history.replaceState(window.history.state, '', url);
        let workflow;
        try {
            const response = await api.fetchApi('/freevideo/launcher/workflow');
            if (!response.ok) throw new Error(`FreeVideo workflow: HTTP ${response.status}`);
            // A fresh copy of the bundled workflow, with the prompt last written.
            workflow = await restorePromptDraft(api, await response.json());
            await app.loadGraphData(structuredClone(workflow), true, true, 'FreeVideo');
        } catch (error) {
            console.error('FreeVideo launcher:', error);
            health({type: 'launch', error});
            return;
        }
        const opened = app.extensionManager?.workflow?.activeWorkflow;
        keepInFront(workflow, opened).then(() => health({type: 'launch'}), error => {
            console.error('FreeVideo launcher:', error);
            health({type: 'launch', error});
        });
    },
});
