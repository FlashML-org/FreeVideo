// With ComfyUI's workflow drafts off, the copies an older session left in this
// browser are never read again, but they still hold the prompts. Remove them.
// Nothing changes while drafts are on: that ComfyUI is the user's to run.

const PERSIST = 'Comfy.Workflow.Persist';
// Drafts (current and older formats), their index and order, the last open path,
// and the two single-workflow keys older frontends kept.
const PREFIXES = ['Comfy.Workflow.Draft', 'Comfy.Workflow.LastActivePath'];
const EXACT = new Set(['Comfy.PreviousWorkflow', 'workflow']);

export const isWorkflowDraftKey = key => typeof key === 'string' && (EXACT.has(key) || PREFIXES.some(prefix => key.startsWith(prefix)));

// Returns how many keys were removed; storage errors leave the rest in place.
export function clearWorkflowDrafts({storage = globalThis.localStorage, persist} = {}) {
    if (persist !== false || !storage) return 0;
    let removed = 0;
    try {
        const keys = [];
        for (let i = 0; i < storage.length; i++) { const key = storage.key(i); if (isWorkflowDraftKey(key)) keys.push(key); }
        for (const key of keys) { storage.removeItem(key); removed++; }
    } catch { /* No storage access: nothing more to remove. */ }
    return removed;
}

// ComfyUI's settings store, or the older settings dialog.
export function workflowPersist(app) {
    const store = app?.extensionManager?.setting;
    if (typeof store?.get === 'function') return store.get(PERSIST);
    return app?.ui?.settings?.getSettingValue?.(PERSIST);
}
