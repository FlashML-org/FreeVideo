// The launcher opens a fresh copy of the bundled workflow each time, so the
// prompt being written, finished or not, is kept in ComfyUI's user data and put
// back into that copy. The untouched starter prompt is the default, not a draft;
// a box emptied after typing is one, and reopens empty.
const FILE = '/userdata/' + encodeURIComponent('freevideo/prompt-draft.json');
let timer = null, pending = null;

export function promptDraft(node) {
    const text = node?.widgets?.find(w => w.name === 'text')?.value;
    if (typeof text !== 'string') return null;
    const versions = node.properties?.freevideo_prompt_versions;
    return {schema: 1, text: node.freevideoStarterPrompt && text === node.freevideoStarterPrompt ? null : text,
        versions: versions && typeof versions === 'object' ? versions : null};
}

export function applyPromptDraft(workflow, draft) {
    const node = workflow?.nodes?.find(row => row.type === 'FreeVideoGenerate');
    if (!node || !Array.isArray(node.widgets_values) || draft?.schema !== 1) return workflow;
    if (typeof draft.text === 'string') node.widgets_values[0] = draft.text;
    const versions = draft.versions;
    if (versions && typeof versions.original === 'string' && typeof versions.rewritten === 'string')
        node.properties = {...node.properties, freevideo_prompt_versions: versions};
    return workflow;
}

export function rememberPromptDraft(api, node) {
    pending = node; clearTimeout(timer);
    timer = setTimeout(() => savePromptDraft(api), 500);
}

// keepalive lets the last edit outlive a closing page.
export async function savePromptDraft(api, keepalive = false) {
    clearTimeout(timer); timer = null;
    const draft = promptDraft(pending); pending = null;
    if (!draft) return false;
    try {
        const reply = await api.fetchApi(FILE + '?overwrite=true', {method: 'POST', body: JSON.stringify(draft), keepalive});
        return reply.ok;
    } catch { return false; }
}

export async function restorePromptDraft(api, workflow) {
    try {
        const reply = await api.fetchApi(FILE, {cache: 'no-store'});
        if (reply.ok) return applyPromptDraft(workflow, await reply.json());
    } catch { /* No draft yet, or unreadable: open the bundled prompt. */ }
    return workflow;
}
