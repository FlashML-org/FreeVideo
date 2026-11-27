// The reload an update makes keeps the workflow in front. FreeVideo keeps ComfyUI's workflow drafts off, so any reload
// opens the default workflow; this reload is the update's, not the user's, so the workflow (with a prompt not yet sent
// in the creation panel, which lives on its node) is handed to the server this page talks to, the updated one already
// running, and taken back once after the reload. The server keeps it in its memory only, for two minutes at most
// (reload_keep.py); this tab keeps only the token that takes it back. Nothing goes to localStorage or IndexedDB, the
// drafts setting is left off, and a refresh the user makes is unchanged.
import { api } from '../../scripts/api.js';
import { app } from '../../scripts/app.js';
import { translator } from './i18n.js';
import { CN } from './topbar_state.js';

export const RELOAD_KEY = 'freevideo.update-reload-token';
// Still on this page this long after the reload was asked for: the user stayed (a question before leaving), or it
// failed. The copy is dropped then, so a later refresh of the user's own does not bring it back.
export const STAY_MS = 15000;
const t = translator(CN);
const json = {'Content-Type': 'application/json'};
const post = (path, body) => api.fetchApi(path, {method: 'POST', headers: json, body, cache: 'no-store', signal: AbortSignal.timeout(10000)});

// Hands the workflow in front to the server; the token on success (also kept in this tab), null when it could not be
// kept (the server did not answer or refused it, or this tab cannot hold the token).
export async function keepForReload({graph = app.graph, workflow = app.extensionManager?.workflow?.activeWorkflow, storage = globalThis.sessionStorage} = {}) {
    let token = null;
    try {
        const value = graph?.serialize?.();
        if (!value || !storage) return null;
        const response = await post('/freevideo/reload-keep', JSON.stringify({schema: 1, workflow: value, name: typeof workflow?.filename === 'string' ? workflow.filename : null}));
        token = response.ok ? (await response.json())?.token : null;
        if (typeof token !== 'string' || !token) return null;
        storage.setItem(RELOAD_KEY, token);
        if (storage.getItem(RELOAD_KEY) !== token) throw new Error('token not kept');
        return token;
    } catch {
        if (token) dropKept(token, storage);
        return null;
    }
}

// Forgets the copy: the token here and the workflow on the server.
export function dropKept(token, storage = globalThis.sessionStorage) {
    try { if (storage?.getItem(RELOAD_KEY) === token) storage.removeItem(RELOAD_KEY); } catch { /* Nothing more here. */ }
    post('/freevideo/reload-keep/drop', JSON.stringify({token})).catch(() => {});
}

// The kept workflow, taken back once. The token is removed first, whatever the server answers.
export async function takeKept(storage = globalThis.sessionStorage) {
    let token = null;
    try { token = storage?.getItem(RELOAD_KEY) ?? null; storage?.removeItem(RELOAD_KEY); } catch { return null; }
    if (!token) return null;
    try {
        const response = await post('/freevideo/reload-keep/take', JSON.stringify({token}));
        if (!response.ok) return null;
        const value = await response.json();
        if (value?.schema !== 1 || !value.workflow || typeof value.workflow !== 'object' || !Array.isArray(value.workflow.nodes)) return null;
        return {workflow: value.workflow, name: typeof value.name === 'string' && value.name ? value.name : null};
    } catch { return null; }
}

// Reloads for the update. ComfyUI asks before leaving while a workflow counts as changed; the one in front is kept (or
// the user chose to reload without it), so it is marked unchanged first and that question is not asked for it. Another
// changed tab still makes ComfyUI ask: only the one in front is kept. When something did ask and the page is still here
// a while after the answer, the user stayed: the workflow counts as changed again, the copy is dropped and `stayed` lets
// the page check again later. When nothing asked, the reload is under way however long the server takes, and nothing is
// dropped.
function leave(reload, token, stayed) {
    const front = app.extensionManager?.workflow?.activeWorkflow, tracker = front?.changeTracker;
    const was = front?.isModified && typeof tracker?.reset === 'function' ? {initial: tracker.initialState} : null;
    if (was) { tracker.reset(); front.isModified = false; }
    let gone = false, decided = false;
    addEventListener('pagehide', () => { gone = true; }, {once: true});
    const stay = () => {
        if (gone) return;
        if (was) { tracker.initialState = was.initial; front.isModified = true; }
        if (token) dropKept(token);
        stayed?.();
    };
    // Added last, so it runs after every other listener: whether any of them asked before leaving. A question holds
    // this page's timers while it shows, so the wait starts once it is answered (the zero timer runs only then).
    addEventListener('beforeunload', event => {
        decided = true;
        if (event.defaultPrevented || (typeof event.returnValue === 'string' && event.returnValue !== '')) setTimeout(() => setTimeout(stay, STAY_MS), 0);
    }, {once: true});
    reload();
    // The reload did not even begin.
    setTimeout(() => { if (!decided) stay(); }, STAY_MS);
}

// What updates.js calls when the server came back as another version.
export async function reloadAfterUpdate(reload, stayed) {
    const token = await keepForReload();
    if (token) leave(reload, token, stayed);
    else askReload(() => leave(reload, null, stayed));
}

// The workflow in front as a file, the same JSON ComfyUI's Export writes. Saved directly: ComfyUI's own Export asks
// for a file name in a dialog that a FreeVideo view (a modal dialog in the top layer) would cover.
export function downloadWorkflow({graph = app.graph, workflow = app.extensionManager?.workflow?.activeWorkflow} = {}) {
    const name = String(workflow?.filename || 'workflow').replace(/[\\/:*?"<>|]+/g, '-') || 'workflow';
    const url = URL.createObjectURL(new Blob([JSON.stringify(graph.serialize(), null, 2)], {type: 'application/json'}));
    const link = document.createElement('a'); link.href = url; link.download = name + '.json';
    document.body.append(link); link.click(); link.remove();
    // The reload follows at once; the file is already handed to the browser.
    setTimeout(() => URL.revokeObjectURL(url), 60000);
}

// Asked only when the workflow could not be kept: the update is done and this page must reload, so there is no
// Cancel; the workflow can be exported first.
export function askReload(reload, {exportWorkflow = downloadWorkflow} = {}) {
    const dialog = document.createElement('dialog');
    dialog.className = 'fv-setup fv-setup-small fv-update-reload';
    dialog.setAttribute('aria-label', t('FreeVideo has been updated', 'FreeVideo 已更新'));
    const text = document.createElement('p');
    text.textContent = t('FreeVideo has been updated and needs to reload the page. The current workflow could not be kept, so the page will open the default workflow after reloading.',
        'FreeVideo 已更新，需要刷新页面。当前的工作流未能暂存，刷新后会回到默认工作流。');
    const actions = document.createElement('div'); actions.className = 'fv-actions fv-end';
    const make = (label, primary, run) => {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
        button.className = primary ? 'fv-primary' : 'fv-quiet'; button.onclick = run; actions.append(button); return button;
    };
    // The file is saved, then the page reloads. This is the default (focus, emphasis): reloading alone loses the
    // workflow for good, so Enter must not do that.
    const save = make(t('Export workflow first', '先导出工作流'), true, async () => {
        try { await exportWorkflow(); } catch (error) { console.warn('FreeVideo export before reload:', error); }
        dialog.close(); setTimeout(reload, 300);
    });
    make(t('Reload page', '刷新页面'), false, () => { dialog.close(); reload(); });
    // Escape does not dismiss it: this page has to reload.
    dialog.addEventListener('cancel', event => event.preventDefault());
    dialog.addEventListener('close', () => dialog.remove());
    dialog.append(text, actions);
    document.body.append(dialog); dialog.showModal(); save.focus();
    return dialog;
}

// After startup ComfyUI opens its own (default) workflow; the kept one is loaded once that is done.
let configured = 0;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function restore(taking) {
    const kept = await taking;
    if (!kept) return;
    const begin = performance.now();
    while (performance.now() - begin < 15000 && (app.extensionManager?.spinner || !configured || performance.now() - configured < 600)) await sleep(100);
    // Into the tab ComfyUI just opened when it is its untouched default, so the page has the tabs it had; otherwise a
    // tab of its own.
    const front = app.extensionManager?.workflow?.activeWorkflow;
    const target = front && front.isTemporary && !front.isModified ? front : kept.name || 'FreeVideo';
    try {
        await app.loadGraphData(kept.workflow, true, true, target);
    } catch (error) {
        console.warn('FreeVideo kept workflow:', error);
        return;
    }
    const {toast} = await import('./notifications.js');
    toast(t('FreeVideo was updated. The current workflow was kept.', 'FreeVideo 已更新，当前的工作流已保留。'), {tone: 'ok'});
}

app.registerExtension({
    name: 'FreeVideo.UpdateReload',
    afterConfigureGraph() { configured = performance.now(); },
    setup() {
        // Asked for at once (the token leaves this tab now); not awaited: ComfyUI opens its workflow only after every
        // extension's setup has returned.
        void restore(takeKept());
    },
});
