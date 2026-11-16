// The launcher starts from the bundled graph. Keep creation choices by name,
// alongside the existing schema-1 prompt draft; ordinary reloads keep their graph.
const FILE = '/userdata/' + encodeURIComponent('freevideo/prompt-draft.json');
const WIDGETS = ['text', 'width', 'height', 'seconds', 'seed', 'control_after_generate', 'two_pass',
    'base_steps', 'refine_steps', 'force_regenerate', 'preview', 'refine_from', 'continue_from', 'frames', 'first_frame_reference',
    'voice_reference', 'hide_tail', 'step_cache', 'upscale_factor'];
const DEFAULTS = ['', 1344, 768, 10, 2026090903, 'randomize', true, 8, 3, false, false, '', '', 0, false, false, false, 'none', '2'];
const SAVED_WIDGETS = WIDGETS.filter(name => !['text', 'force_regenerate', 'refine_from', 'continue_from', 'frames',
    'voice_reference', 'hide_tail'].includes(name));
const STEP_CACHE_OPTIONS = ['none', 'easycache-av', 'fbcache'], UPSCALE_OPTIONS = ['2', '1.5', '1.2'];
const PROPERTIES = ['freevideo_aspect', 'freevideo_pixels', 'freevideo_seconds', 'freevideo_single',
    'freevideo_block_seconds', 'freevideo_run_mode', 'freevideo_batch_count'];
const OUTBOX = 'freevideo.prompt-pending';
const clone = value => JSON.parse(JSON.stringify(value));
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const watched = new WeakMap(), dirty = new Set();
const historyObservers = new WeakSet(), restoringHistory = new WeakSet();
let timer = null, pending = null, inFlight = null, sending = null;
// What the page could not do with the draft file, so the prompt box can say so and retry.
const trouble = {save: false, restore: false}, troubleWatchers = new Set();
function setTrouble(key, value) {
    if (trouble[key] === value) return;
    trouble[key] = value;
    for (const watcher of troubleWatchers) watcher({...trouble});
}
export function promptDraftTrouble() { return {...trouble}; }
export function watchPromptDraftTrouble(watcher) { troubleWatchers.add(watcher); return () => troubleWatchers.delete(watcher); }
try { pending = JSON.parse(globalThis.sessionStorage?.getItem(OUTBOX) || 'null'); } catch {}

function retain() {
    try {
        if (pending || sending) globalThis.sessionStorage?.setItem(OUTBOX, JSON.stringify(mergePatches(sending, pending)));
        else globalThis.sessionStorage?.removeItem(OUTBOX);
    } catch { /* Saving still works when browser storage is disabled. */ }
}

export function promptDraft(node) {
    const text = node?.widgets?.find(w => w.name === 'text')?.value;
    if (typeof text !== 'string') return null;
    const versions = node.properties?.freevideo_prompt_versions;
    // A cleared prompt leaves no copy in the draft file: its original and rewritten versions go with it.
    const draft = {schema: 1, text: node.freevideoStarterPrompt && text === node.freevideoStarterPrompt ? null : text,
        versions: String(text ?? '').trim() && versions && typeof versions === 'object' ? clone(versions) : null};
    const widgets = Object.fromEntries((node.widgets || []).filter(w => SAVED_WIDGETS.includes(w.name)).map(w => [w.name, w.value]));
    const properties = Object.fromEntries(PROPERTIES.filter(key => Object.hasOwn(node.properties || {}, key)).map(key => [key, node.properties[key]]));
    if (Object.keys(widgets).length) draft.widgets = clone(widgets);
    if (Object.keys(properties).length) draft.properties = clone(properties);
    return draft;
}

export function applyPromptDraft(workflow, draft) {
    const node = workflow?.nodes?.find(row => row.type === 'FreeVideoGenerate');
    if (!node || !Array.isArray(node.widgets_values) || draft?.schema !== 1) return workflow;
    if (typeof draft.text === 'string') node.widgets_values[0] = draft.text;
    if (draft.project && node.properties) {
        for (const key of PROPERTIES) if (!Object.hasOwn(draft.properties || {}, key)) delete node.properties[key];
        if (!draft.versions) delete node.properties.freevideo_prompt_versions;
    }
    for (const name of SAVED_WIDGETS) {
        const value = draft.widgets?.[name], index = WIDGETS.indexOf(name);
        if (value === undefined || typeof value !== typeof DEFAULTS[index]
            || (typeof value === 'number' && !Number.isFinite(value))
            || (name === 'step_cache' && !STEP_CACHE_OPTIONS.includes(value))
            || (name === 'upscale_factor' && !UPSCALE_OPTIONS.includes(value))) continue;
        while (node.widgets_values.length <= index) node.widgets_values.push(DEFAULTS[node.widgets_values.length]);
        node.widgets_values[index] = value;
    }
    for (const key of PROPERTIES) if (Object.hasOwn(draft.properties || {}, key)) {
        node.properties ||= {};
        node.properties[key] = draft.properties[key];
    }
    const versions = draft.versions;
    if (versions && typeof versions.original === 'string' && typeof versions.rewritten === 'string')
        node.properties = {...node.properties, freevideo_prompt_versions: clone(versions)};
    return workflow;
}

// Baseline comes from the configured node, never the shared server draft. A stale
// tab therefore contributes only its actual edits, including property resets.
export function trackPromptDraft(node, onChange, project) {
    if (dirty.has(node)) collect(node);
    watched.set(node, {last: promptDraft(node), onChange: onChange || watched.get(node)?.onChange || (() => {}),
        project: project || watched.get(node)?.project});
}

function combine(base, patch) {
    const value = {...(patch.project && (base?.schema !== 1 || (base.project && patch.project !== base.project)) ? patch.snapshot
        : base?.schema === 1 ? base : {schema: 1, text: null, versions: null})};
    for (const key of ['text', 'versions', 'project']) if (Object.hasOwn(patch, key)) value[key] = patch[key];
    for (const group of ['widgets', 'properties']) if (patch[group]) {
        value[group] = {...value[group]};
        for (const [key, entry] of Object.entries(patch[group])) {
            if (entry === null) delete value[group][key]; else value[group][key] = entry;
        }
    }
    return value;
}
function mergePatches(base, patch) {
    if (patch?.project && base?.project && patch.project !== base.project) base = null;
    return {...base, ...patch,
        widgets: {...base?.widgets, ...patch?.widgets}, properties: {...base?.properties, ...patch?.properties}};
}
function addPatch(patch) {
    pending = mergePatches(pending, patch);
    retain();
}
function collect(node, notify = true) {
    dirty.delete(node);
    const next = promptDraft(node), state = watched.get(node);
    if (!next) return;
    const previous = state?.last || {text: null, versions: null};
    const patch = {};
    if (!equal(next.text, previous.text) || !equal(next.versions, previous.versions)) {
        patch.text = next.text; patch.versions = next.versions;
    }
    for (const group of ['widgets', 'properties']) {
        for (const key of new Set([...Object.keys(previous[group] || {}), ...Object.keys(next[group] || {})])) {
            if (!equal(previous[group]?.[key], next[group]?.[key])) {
                (patch[group] ||= {})[key] = next[group]?.[key] ?? null;
            }
        }
    }
    if (Object.keys(patch).length) {
        const project = state?.project?.();
        if (project) { patch.project = project; patch.snapshot = next; }
        addPatch(patch); if (notify) state?.onChange();
    }
    watched.set(node, {last: next, onChange: state?.onChange || (() => {}), project: state?.project});
}

export function rememberPromptDraft(api, node) {
    dirty.add(node);
    // Compound edits finish their widget callbacks and properties in this turn.
    queueMicrotask(() => { if (dirty.has(node)) collect(node); });
    clearTimeout(timer);
    timer = setTimeout(() => savePromptDraft(api), 500);
}

export function promptHistoryRestoring(app) {
    return restoringHistory.has(app.extensionManager?.workflow?.activeWorkflow?.changeTracker);
}

// The host replaces node instances when replaying history. Observe the completed
// undo/redo transaction, comparing against the nodes it replaced, without adding
// a new undo step or treating an ordinary project load as an edit.
export function watchPromptHistory(api, app) {
    const workflow = app.extensionManager?.workflow?.activeWorkflow, tracker = workflow?.changeTracker;
    if (!tracker || historyObservers.has(tracker)) return;
    historyObservers.add(tracker);
    for (const method of ['undo', 'redo']) {
        const replay = tracker[method];
        if (typeof replay !== 'function') continue;
        tracker[method] = async function (...args) {
            if (app.extensionManager?.workflow?.activeWorkflow !== workflow) return replay.apply(this, args);
            for (const node of dirty) collect(node);
            const previous = new Map((app.graph?._nodes || []).map(node => [node.id, promptDraft(node)]));
            restoringHistory.add(tracker);
            let result;
            try { result = await replay.apply(this, args); }
            finally { restoringHistory.delete(tracker); }
            if (app.extensionManager?.workflow?.activeWorkflow !== workflow) return result;
            for (const node of app.graph?._nodes || []) {
                if (node.type !== 'FreeVideoGenerate' && node.comfyClass !== 'FreeVideoGenerate') continue;
                const state = watched.get(node);
                watched.set(node, {...state, project: () => workflow.path,
                    last: previous.get(node.id) || {text: null, versions: null}});
                rememberPromptDraft(api, node);
                collect(node, false);
            }
            return result;
        };
    }
}

// Serialize read/merge/write across tabs and keep failed patches for retry. The
// per-tab outbox also survives refreshing during an in-flight request.
export async function savePromptDraft(api, keepalive = false) {
    clearTimeout(timer); timer = null;
    for (const node of dirty) collect(node);
    if (inFlight) {
        if (!await inFlight) return false;
        return pending ? savePromptDraft(api, keepalive) : true;
    }
    if (!pending) return false;
    const patch = pending; pending = null; sending = patch;
    const write = async () => {
        try {
            const reply = await api.fetchApi(FILE, {cache: 'no-store', keepalive});
            if (!reply.ok && reply.status !== 404) throw new Error('draft_read');
            // A damaged file can never be merged into; the next save replaces it.
            const current = reply.ok ? await reply.json().catch(() => null) : null;
            const saved = await api.fetchApi(FILE + '?overwrite=true', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify(combine(current, patch)), keepalive});
            if (!saved.ok) throw new Error('draft_write');
            sending = null; retain(); setTrouble('save', false); return true;
        } catch {
            const newer = pending; pending = patch; sending = null;
            if (newer) addPatch(newer); else retain();
            setTrouble('save', true);
            return false;
        }
    };
    inFlight = globalThis.navigator?.locks ? navigator.locks.request('freevideo-prompt-draft', write) : write();
    try { return await inFlight; } finally { inFlight = null; }
}

export async function restorePromptDraft(api, workflow, project = null) {
    // A refreshed page retries its own unsaved patch before launcher restoration.
    if (pending || inFlight || dirty.size) await savePromptDraft(api);
    let draft, unread = false;
    try {
        const reply = await api.fetchApi(FILE, {cache: 'no-store'});
        // Only a missing file means there is no earlier prompt.
        if (reply.ok) draft = await reply.json(); else unread = reply.status !== 404;
    } catch { unread = true; /* Keep the workflow and any local edits if the server is offline. */ }
    setTrouble('restore', unread);
    // A failed retry remains authoritative over the older remote copy. Retain
    // the outbox until a later explicit save succeeds, including across reloads.
    const local = pending || sending ? mergePatches(sending, pending) : null;
    if (local && (!project || !local.project || local.project === project)) draft = combine(draft, local);
    // Unscoped schema-1 drafts predate project ownership and must migrate too.
    if (!project || !draft?.project || draft.project === project) return applyPromptDraft(workflow, draft);
    return workflow;
}
