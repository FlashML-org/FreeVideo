import { app } from '../../scripts/app.js';
import { api } from '../../scripts/api.js';
import { restorePromptDraft } from './prompt_draft.js';

// Explicit launcher navigation only; ordinary ComfyUI visits restore their own
// graph. Remove the flag before loading so refreshing never resets user edits.
app.registerExtension({
    name: 'FreeVideo.Launcher',
    async setup() {
        const url = new URL(window.location.href);
        if (url.searchParams.get('freevideo') !== 'launch') return;
        url.searchParams.delete('freevideo');
        window.history.replaceState(window.history.state, '', url);
        try {
            const response = await api.fetchApi('/freevideo/launcher/workflow');
            if (!response.ok) throw new Error(`FreeVideo workflow: HTTP ${response.status}`);
            // A fresh copy of the bundled workflow, with the prompt last written.
            const workflow = await restorePromptDraft(api, await response.json());
            await app.loadGraphData(workflow, true, true, 'FreeVideo');
        } catch (error) {
            app.extensionManager?.toast?.add({severity: 'error', summary: 'FreeVideo', detail: String(error), life: 15000});
            console.error('FreeVideo launcher:', error);
        }
    },
});
