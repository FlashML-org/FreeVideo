import { app } from '../../scripts/app.js';
import { api } from '../../scripts/api.js';
import { restorePromptDraft } from './prompt_draft.js';
import { takePortMarker } from './port_notice.js';
import { launcherRestoreFinished } from './workspace_restore.js';
import { clonePrompt } from './optional_inputs.js';

// health.js names what failed; files loaded before it queue their reports.
const health = item => (window.__freevideoHealth ||= []).push(item);
const studioWorkflow = graph => (graph?._nodes || graph?.nodes || []).some(n => n.comfyClass === 'FreeVideoGenerate' || n.type === 'FreeVideoGenerate')
    && !!graph?.extra?.freevideo_studio;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let configured = performance.now();

// ComfyUI's own session storage holds the last project's tabs and drafts under
// several keys that differ between frontend versions; any of them naming one of
// our nodes (the generate node may have been removed) means a project from the previous visit is waiting to be restored.
function cachedStudioProject() {
    for (const area of ['localStorage', 'sessionStorage']) {
        try {
            const store = globalThis[area];
            for (let index = 0; index < (store?.length || 0); index++) {
                const key = store.key(index);
                if (key !== 'workflow' && !key?.startsWith('Comfy.')) continue;
                if (/FreeVideo(Generate|Media)|freevideo_studio/.test(store.getItem(key) || '')) return true;
            }
        } catch { /* Storage blocked: open the bundled workflow right away. */ }
    }
    return false;
}

const finite = (value, low, high) => typeof value === 'number' && Number.isFinite(value) && value >= low && value <= high;
// A tab restored from another FreeVideo version is replaced by the current default workflow.
// Only the creation choices come across, by name; a value that is not valid now is dropped.
// Positions 0-4 of the generate node and 0 of the media node have never moved between versions.
function currentStudioWorkflow(saved, bundled) {
    const stamp = bundled.nodes?.find(node => node.type === 'FreeVideoGenerate')?.properties?.freevideo_version;
    const old = saved.nodes?.find(node => node.type === 'FreeVideoGenerate');
    if (!stamp || !old || old.properties?.freevideo_version === stamp) return null;
    const graph = structuredClone(bundled), node = graph.nodes.find(row => row.type === 'FreeVideoGenerate');
    const [text, width, height, seconds, seed] = Array.isArray(old.widgets_values) ? old.widgets_values : [];
    if (typeof text === 'string' && text.trim()) node.widgets_values[0] = text;
    if (finite(width, 256, 4096) && Number.isInteger(width)) node.widgets_values[1] = width;
    if (finite(height, 256, 4096) && Number.isInteger(height)) node.widgets_values[2] = height;
    if (finite(seconds, 1.625, 60)) node.widgets_values[3] = seconds;
    if (finite(seed, 0, 2 ** 53 - 1) && Number.isInteger(seed)) node.widgets_values[4] = seed;
    // A tab saved by 0.4.13 or later keeps these at fixed positions (6, 7, 8, 17, 18); an unstamped one moved them.
    const [major, minor, patch] = String(old.properties?.freevideo_version).split('.').map(Number);
    const stamped = [major, minor, patch].every(Number.isInteger) && (major > 0 || minor > 4 || (minor === 4 && patch >= 13));
    if (stamped && Array.isArray(old.widgets_values)) {
        const values = node.widgets_values, kept = old.widgets_values;
        // The bundled workflow lists only the first widgets: fill up to the last one with the node's defaults.
        const defaults = [8, 3, false, false, '', '', 0, false, false, false, 'none', '2'];
        while (values.length < 19) values.push(defaults[values.length - 7]);
        if (typeof kept[6] === 'boolean') values[6] = kept[6];
        // The quality level is these steps: High and Max are 20 first-pass steps.
        if (Number.isInteger(kept[7]) && finite(kept[7], 1, 32)) values[7] = kept[7];
        if (Number.isInteger(kept[8]) && finite(kept[8], 1, 31)) values[8] = kept[8];
        if (['none', 'easycache-av', 'fbcache'].includes(kept[17])) values[17] = kept[17];
        if (['2', '1.5', '1.2'].includes(kept[18])) values[18] = kept[18];
    }
    const media = graph.nodes.find(row => row.type === 'FreeVideoMedia');
    const assets = (saved.nodes.find(row => row.type === 'FreeVideoMedia')?.widgets_values || [])[0];
    try {
        const rows = JSON.parse(assets);
        if (media && Array.isArray(rows) && rows.length && rows.every(row => row && typeof row === 'object' && !Array.isArray(row))) media.widgets_values[0] = assets;
    } catch { /* Not a media list: keep the empty one. */ }
    let rows = [];
    try { rows = JSON.parse(saved.nodes.find(row => row.type === 'FreeVideoLoRAStack')?.widgets_values?.[0]); } catch { /* No LoRA. */ }
    rows = Array.isArray(rows) ? rows.filter(row => row && typeof row.name === 'string' && row.name && finite(row.strength ?? 1, -10, 10)
        && (row.enabled === undefined || typeof row.enabled === 'boolean')).slice(0, 32) : [];
    const input = node.inputs?.findIndex(row => row.name === 'loras') ?? -1;
    if (rows.length && input >= 0) {
        const id = Math.max(graph.last_node_id || 0, ...graph.nodes.map(row => Number(row.id) || 0)) + 1, link = Math.max(graph.last_link_id || 0, ...graph.links.map(row => row[0])) + 1;
        graph.nodes.push({id, type: 'FreeVideoLoRAStack', pos: [60, 660], size: [460, 200], flags: {}, order: 2, mode: 0,
            inputs: [{name: 'adapters', type: 'STRING', widget: {name: 'adapters'}, link: null}, {name: 'previous', type: 'FREEVIDEO_LORAS', link: null}],
            outputs: [{name: 'loras', type: 'FREEVIDEO_LORAS', links: [link], slot_index: 0}],
            properties: {'Node name for S&R': 'FreeVideoLoRAStack'}, widgets_values: [JSON.stringify(rows)]});
        graph.links.push([link, id, 0, node.id, input, 'FREEVIDEO_LORAS']);
        node.inputs[input].link = link;
        graph.last_node_id = id; graph.last_link_id = link;
    }
    return graph;
}

async function existingStudioWorkflow(target, bundled) {
    // Restored background tabs are lazy: load() reads the host's saved draft,
    // whereas the workflow store's openWorkflow() only selects the tab record.
    await target.load?.();
    const saved = target.changeTracker?.activeState;
    // Leave the current graph intact; the existing startup check reports an
    // unopened workflow without replacing the user's project.
    if (!saved) return null;
    // Only the shared tab is ours to replace; a workflow the user saved under another name stays.
    const current = target.path === 'workflows/FreeVideo.json' ? currentStudioWorkflow(saved, bundled) : null;
    if (current) return restorePromptDraft(api, current, target.path);
    const graph = clonePrompt(saved);
    if (!graph.nodes?.some(node => node.type === 'FreeVideoGenerate')) {
        const node = structuredClone(bundled.nodes.find(node => node.type === 'FreeVideoGenerate'));
        node.id = Math.max(graph.last_node_id || 0, ...graph.nodes.map(node => Number(node.id) || 0)) + 1;
        graph.last_node_id = node.id;
        node.pos = [Math.max(0, ...graph.nodes.map(node => (node.pos?.[0] || 0) + (node.size?.[0] || 400))) + 80, 100];
        for (const input of node.inputs || []) input.link = null;
        for (const output of node.outputs || []) output.links = null;
        graph.nodes.push(node);
        // Reconnect a single unambiguous media source, leaving every existing
        // node and link intact. Multiple media sources keep their own topology.
        const media = graph.nodes.filter(node => node.type === 'FreeVideoMedia');
        const input = node.inputs?.findIndex(input => input.name === 'media') ?? -1;
        const output = media.length === 1 ? media[0].outputs?.findIndex(output => output.type === node.inputs?.[input]?.type) ?? -1 : -1;
        if (input >= 0 && output >= 0 && graph.version === .4) {
            graph.links ||= [];
            const id = Math.max(graph.last_link_id || 0, ...graph.links.map(link => link[0])) + 1;
            graph.links.push([id, media[0].id, output, node.id, input, node.inputs[input].type]);
            (media[0].outputs[output].links ||= []).push(id);
            node.inputs[input].link = id;
            graph.last_link_id = id;
        }
    }
    graph.extra = {...graph.extra, freevideo_studio: true};
    return restorePromptDraft(api, graph, target.path);
}

// Opens FreeVideo's own workflow in a tab of its own and returns its generate node. The workflow in front
// is left as it is: an existing FreeVideo tab comes back with its project, otherwise a new one opens.
export async function openFreeVideoWorkflow() {
    const response = await api.fetchApi('/freevideo/launcher/workflow');
    if (!response.ok) throw new Error(`FreeVideo workflow: HTTP ${response.status}`);
    const workflow = await restorePromptDraft(api, await response.json());
    const store = app.extensionManager?.workflow;
    const existing = store?.getWorkflowByPath?.('workflows/FreeVideo.json')
        || store?.openWorkflows?.find(row => studioWorkflow(row.changeTracker?.activeState));
    const restored = existing ? await existingStudioWorkflow(existing, workflow) : null;
    if (restored) await app.loadGraphData(restored, true, true, existing);
    else await app.loadGraphData(structuredClone(workflow), true, true, 'FreeVideo');
    return (app.graph?._nodes || []).find(n => n.comfyClass === 'FreeVideoGenerate' || n.type === 'FreeVideoGenerate') || null;
}

// ComfyUI restores the previous session's workflow and tabs after every
// extension's setup, so that restore can bring another workflow to the front
// over ours. Once startup is quiet, bring our tab back into view even if a
// click or key arrived during startup. Never awaited from setup: ComfyUI restores only after
// setup returns.
async function keepInFront(workflow, opened) {
    let touched = false;
    const touch = event => { if (!event.target?.closest?.('.fv-health')) touched = true; };
    addEventListener('pointerdown', touch, true); addEventListener('keydown', touch, true);
    try {
        const begin = performance.now();
        while (performance.now() - begin < 15000
            && (app.extensionManager?.spinner || performance.now() - configured < 600)) await sleep(100);
        const store = app.extensionManager?.workflow;
        // Opening the bundled graph before session restore overwrites the cached
        // FreeVideo tab, including its media and connections. Reuse that project.
        if (studioWorkflow(app.graph)) {
            const current = app.graph.serialize(), target = store?.activeWorkflow;
            const replaced = target?.path === 'workflows/FreeVideo.json' ? currentStudioWorkflow(current, workflow) : null;
            const restored = await restorePromptDraft(api, replaced || clonePrompt(current), target?.path || '__existing__');
            if (JSON.stringify(restored) !== JSON.stringify(current))
                await app.loadGraphData(restored, true, true, target || 'FreeVideo');
            return;
        }
        // The user kept working in our own tab: its edits stay, even without the generate node.
        if (touched && opened && store?.activeWorkflow === opened) return;
        const existing = store?.getWorkflowByPath?.('workflows/FreeVideo.json')
            || store?.openWorkflows?.find(row => studioWorkflow(row.changeTracker?.activeState));
        if (existing) {
            const restored = await existingStudioWorkflow(existing, workflow);
            if (restored) await app.loadGraphData(restored, true, true, existing);
            return;
        }
        // Our own tab when ComfyUI still has it; a new one otherwise.
        const target = opened && store?.getWorkflowByPath?.(opened.path) === opened ? opened : 'FreeVideo';
        await app.loadGraphData(structuredClone(workflow), true, true, target);
    } finally {
        removeEventListener('pointerdown', touch, true); removeEventListener('keydown', touch, true);
    }
}

// While extensions are still starting, ComfyUI has not set its canvas store yet. Frontends from 1.57 on
// then reject one internal step of loadGraphData (the address hash) with this error, un-awaited, after
// the graph itself has loaded. Nothing in the page depends on that step; absorb exactly this rejection
// for the duration of the call instead of leaving it unhandled. The listener runs before the page
// check's own, which therefore never mistakes it for a FreeVideo failure.
const canvasNotReady = reason => reason instanceof Error && reason.message === 'getCanvas: canvas is null';
async function loadWhileStarting(graph, name) {
    const absorb = event => { if (canvasNotReady(event.reason)) { event.preventDefault(); event.stopImmediatePropagation(); } };
    addEventListener('unhandledrejection', absorb, true);
    try { return await app.loadGraphData(graph, true, true, name); }
    finally { setTimeout(() => removeEventListener('unhandledrejection', absorb, true), 2000); }
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
        const cached = cachedStudioProject();
        try {
            const response = await api.fetchApi('/freevideo/launcher/workflow');
            if (!response.ok) throw new Error(`FreeVideo workflow: HTTP ${response.status}`);
            // A fresh copy of the bundled workflow, with the prompt last written.
            workflow = await restorePromptDraft(api, await response.json());
            // Opening the bundled graph now would overwrite the cached project's
            // media and connections before ComfyUI restores them; keepInFront
            // brings the restored tab forward instead.
            if (!cached) await loadWhileStarting(structuredClone(workflow), 'FreeVideo');
        } catch (error) {
            console.error('FreeVideo launcher:', error);
            health({type: 'launch', error});
            launcherRestoreFinished();
            return;
        }
        const opened = cached ? undefined : app.extensionManager?.workflow?.activeWorkflow;
        keepInFront(workflow, opened).then(() => { health({type: 'launch'}); launcherRestoreFinished(); }, error => {
            console.error('FreeVideo launcher:', error);
            health({type: 'launch', error});
            launcherRestoreFinished();
        });
    },
});
