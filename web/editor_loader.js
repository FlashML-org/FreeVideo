// Keep this adapter independent of the editing module graph: even a parse or
// evaluation error in that graph must leave the current workspace usable.
import {app} from '../../scripts/app.js';

export async function openEditorView(node, options) {
    try {
        const entry = await import('./editor_entry.js');
        if (options?.isCurrent?.() === false) return null;
        return entry.openEditorView(node, options);
    } catch (error) {
        if (options?.isCurrent?.() === false) return null;
        console.warn('FreeVideo edit view:', error);
        // The page's language as the top bar has it (loaded on demand, so a failure here still reads it).
        const cn = await import('./topbar_state.js').then(module => module.CN, () => String(navigator.language || '').toLowerCase().startsWith('zh'));
        const detail = cn ? '没有完成，请重试' : 'Could not complete. Try again.';
        // The native modal is above the host's toast layer. Also use the
        // workspace's existing failure status so the message stays visible.
        const status = options?.studio?.querySelector('.fv-status');
        if (status) { status.dataset.error = 'true'; status.textContent = detail; }
        app.extensionManager?.toast?.add({severity: 'error', summary: 'FreeVideo', detail, life: 9000});
        options?.onFailure?.(error);
        // Do not retain a rejected promise. Each click tries import again;
        // browsers may still retain the failed module URL until a page reload.
        return null;
    }
}
