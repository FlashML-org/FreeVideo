// The optional advanced inputs were appended after the first public workflow
// shape. A workflow restored from an older version, or a widget list that moved,
// can leave an empty or missing value in them, and Comfy then rejects the whole
// request. Keep each of them to a value the server accepts.
const DEFAULTS = {refine_from: '', continue_from: '', frames: 0, first_frame_reference: false, force_regenerate: false, preview: false, voice_reference: false, hide_tail: false, step_cache: 'none', upscale_factor: '2'};
// A workflow saved before Step cache existed holds '' where it now sits.
const STEP_CACHE_OPTIONS = ['none', 'easycache-av', 'fbcache'];
// Likewise for Upscale factor, appended after Step cache.
const UPSCALE_OPTIONS = ['2', '1.5', '1.2'];

const BOOLEANS = new Set(['first_frame_reference', 'force_regenerate', 'preview', 'voice_reference', 'hide_tail']);

// The words a script, an older workflow or String(null) leaves behind for "not given".
const EMPTY_WORDS = new Set(['none', 'null', 'undefined', 'nan']);

// Text of an optional input, or '' when it was not given.
export function optionalText(value) {
    if (typeof value !== 'string') return '';
    const text = value.trim();
    return !text || EMPTY_WORDS.has(text.toLowerCase()) ? '' : value;
}

// Total frames is 0 (follow Duration) or 5 + 17n from 39 on, as the engine reads it (node_frames).
// Any other number came from somewhere else, such as a moved widget, and is not a requested length.
export function frameCount(value) {
    return Number.isSafeInteger(value) && value >= 39 && (value - 5) % 17 === 0 ? value : 0;
}

export function optionalInputValue(name, value) {
    if (name === 'frames') return frameCount(value);
    if (name === 'refine_from' || name === 'continue_from') return optionalText(value);
    if (BOOLEANS.has(name)) return value === true;
    if (name === 'step_cache') return STEP_CACHE_OPTIONS.includes(value) ? value : 'none';
    if (name === 'upscale_factor') return UPSCALE_OPTIONS.includes(value) ? value : '2';
    return value;
}

// A saved workflow is read back by position, so a control can hold another type's value, a choice the
// node no longer offers or a number outside its range. The node's own definition says what is valid:
// such a value becomes the default, the others (prompt, size, seed, links, other nodes) are left as saved.
export function definedWidgetValue(entry, value) {
    if (!Array.isArray(entry)) return value;
    const [kind, options = {}] = entry, choices = Array.isArray(kind) ? kind : options.options;
    const fallback = options.default ?? (choices ? choices[0] : undefined);
    if (fallback === undefined) return value;
    if (choices) return choices.includes(value) ? value : fallback;
    const inRange = number => (options.min === undefined || number >= options.min) && (options.max === undefined || number <= options.max);
    if (kind === 'INT') return Number.isSafeInteger(value) && inRange(value) ? value : fallback;
    if (kind === 'FLOAT') return typeof value === 'number' && Number.isFinite(value) && inRange(value) ? value : fallback;
    if (kind === 'BOOLEAN') return typeof value === 'boolean' ? value : fallback;
    if (kind === 'STRING') return typeof value === 'string' ? value : fallback;
    return value;
}

export function migrateDefinedWidgets(node) {
    const input = node?.constructor?.nodeData?.input;
    for (const widget of node?.widgets || []) {
        const value = definedWidgetValue(input?.required?.[widget.name] || input?.optional?.[widget.name], widget.value);
        if (value !== widget.value) widget.value = value;
    }
}

export function cleanOptionalWidgets(node) {
    migrateDefinedWidgets(node);
    for (const widget of node?.widgets || []) {
        if (!(widget.name in DEFAULTS)) continue;
        const value = optionalInputValue(widget.name, widget.value);
        if (value !== widget.value) widget.value = value;
    }
}

// The user chose to generate without a description: this request alone says so; the node's own widgets never keep it.
export function allowEmptyPrompt(prompt, nodeId) {
    const inputs = prompt?.output?.[String(nodeId)]?.inputs;
    if (inputs) inputs.allow_empty_prompt = true;
    return prompt;
}

export function cleanOptionalPrompt(prompt) {
    for (const entry of Object.values(prompt?.output || {})) {
        if (entry?.class_type !== 'FreeVideoGenerate' || !entry.inputs) continue;
        for (const name of Object.keys(DEFAULTS)) {
            if (name in entry.inputs) entry.inputs[name] = optionalInputValue(name, entry.inputs[name]);
        }
    }
    return prompt;
}

// A graph taken from the host can carry a value structuredClone refuses, such as a reactive proxy another
// extension left in the workflow's extra data. The request is JSON on the wire, so when the native copy
// fails the JSON copy stands in for it; a graph that clones natively is copied exactly as before.
export function clonePrompt(value) {
    try { return structuredClone(value); }
    catch (error) {
        if (error?.name !== 'DataCloneError') throw error;
        return JSON.parse(JSON.stringify(value));
    }
}
