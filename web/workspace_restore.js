import { isLaunchVisit } from './launch_visit.js';
import { isChinese } from './i18n.js';
// Small, eager startup surface: no editing-module or host imports to delay its first paint.
// Provisional copy keys for Iris. Keep project names as text, never markup.
export const WORKSPACE_OPENING_COPY = {
    opening: ['Opening', '正在打开'],
    openingNamed: ['Opening "{name}"', '正在打开“{name}”'],
};
let opening = null;
const query = new URLSearchParams(typeof location === 'undefined' ? '' : location.search);
const cn = isChinese();
let resolveLaunch;
const launchReady = query.get('freevideo') === 'launch' ? new Promise(resolve => { resolveLaunch = resolve; }) : null;
export const waitForLauncherRestore = () => launchReady;
export function launcherRestoreFinished() { resolveLaunch?.(); resolveLaunch = null; }

function cachedName() {
    try {
        const value = JSON.parse(localStorage.getItem('fv-editor-opening'));
        return value?.id === localStorage.getItem('fv-editor-last') ? value.name : '';
    } catch { return ''; }
}
export function beginWorkspaceRestore() {
    if (opening) return opening;
    const dialog = document.createElement('dialog'), label = document.createElement('span'), style = document.createElement('style');
    dialog.className = 'fv-workspace-opening';
    dialog.setAttribute('aria-busy', 'true'); label.setAttribute('role', 'status'); label.setAttribute('aria-live', 'polite');
    // Inline sizing works before the product styles arrive. Suppress cached/live update
    // banners until the recovered workspace is ready, without delaying update checks.
    style.textContent = '.fv-workspace-opening[open]{position:fixed;inset:0;box-sizing:border-box;width:100vw;height:100vh;max-width:none;max-height:none;margin:0;border:0;padding:24px;display:grid;place-items:center;background:#10151e;color:#e6edf6;font:16px/1.6 system-ui,sans-serif}.fv-workspace-opening::backdrop{background:#10151e}.fv-update-notice{display:none!important}';
    dialog.append(label); document.head.append(style); document.body.append(dialog);
    const setName = name => {
        const known = typeof name === 'string' ? name.trim() : '';
        const words = WORKSPACE_OPENING_COPY[known ? 'openingNamed' : 'opening'];
        label.textContent = words[cn ? 1 : 0].replace('{name}', () => known);
    };
    dialog.oncancel = event => event.preventDefault();
    const handle = {setName, cover() {
        // openEditor creates a native modal; keep this status above its unfinished UI.
        if (opening !== handle) return;
        if (dialog.open) dialog.close();
        dialog.showModal();
    }, finish() {
        if (opening !== handle) return;
        opening = null; if (dialog.open) dialog.close(); dialog.remove(); style.remove();
    }};
    opening = handle; setName(cachedName()); dialog.showModal();
    return handle;
}
export function finishWorkspaceRestore() { opening?.finish(); }
// ComfyUI loads extension modules before setup/session restoration. Show words at
// this first opportunity, including ordinary refreshes after the launcher flag is gone.
// Only a launcher visit opens the workspace on its own (see afterConfigureGraph), so only it is covered here.
try { if (localStorage.getItem('freevideo.view') === 'editor' && isLaunchVisit(location.search, globalThis.sessionStorage)) beginWorkspaceRestore(); } catch { /* Storage may be unavailable. */ }
