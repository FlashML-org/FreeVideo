import {api} from '../../scripts/api.js';
import { app } from '../../scripts/app.js';
import { wordmark } from './wordmark.js';
import { createUpdateNotice } from './updates.js';
import { openEditorView } from './editor_loader.js';
import { installShell, noteView, hideCover, switchView, setViewResync, syncTabs } from './shell.js';
import { installTooltips } from './tooltip.js';
import './palette.js';
import { isLaunchVisit } from './launch_visit.js';
import { topbarActions, adoptWhenPlaced, buildBar } from './topbar.js';

const languageOverride = typeof location !== 'undefined'
    ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en'
    && String(navigator.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;
const preference = 'freevideo.view';
let toolbar, open, selected, currentView = 'nodes', agentTab = null, opening = null, place = null;
const style = document.createElement('link');
style.rel = 'stylesheet'; style.href = new URL('./view_navigation.css', import.meta.url).href;
document.head.append(style);

export function preferredView() {
    try { return localStorage.getItem(preference); } catch { return null; }
}

// The shell is in place before any view opens. A launcher visit that will open a view is covered until it has.
installShell({cover: isLaunchVisit(globalThis.location?.search, globalThis.sessionStorage) && preferredView() !== 'nodes'});
installTooltips();
// A switch that failed half way comes back to the view really in front (shell.js frontView).
setViewResync(view => viewChanged(view));

let collapsing = false;

export function viewChanged(view, node) {
    // A local count for the diagnostic report; the page never waits for it.
    if (view === 'nodes' && currentView !== 'nodes') try { api.fetchApi('/freevideo/node-view-opened', {method: 'POST'}).catch(() => {}); } catch { /* Counting never blocks the view. */ }
    currentView = view;
    if (node) selected = node;
    noteView(view);
    // The cover gives way once the view has painted.
    if (view !== 'nodes') requestAnimationFrame(() => requestAnimationFrame(hideCover));
    // Making way for the user's own workflow is not a choice of view: the FreeVideo workflow reopens as it was left.
    if (!collapsing) try { localStorage.setItem(preference, view); } catch { /* Private browsing still works. */ }
    refreshNavigation();
}

// Whether this FreeVideo has the Agent interface on: 'on', 'off' or 'unknown' (true and false read as on and off).
// The surfaces that open first (the toolbar, the studio) ask and tell it here. The fourth tab hides only when the
// interface is off; a check that failed leaves it in place and asks again, waiting longer each time.
let agentState = null, agentRun = null;
const agentTabs = new Set();
const asState = on => on === true || on === 'on' ? 'on' : on === false || on === 'off' ? 'off' : 'unknown';
// On the Autopilot page itself the tab always shows.
function showAgentTab(button, state = agentState) {
    if (!button.dataset.here) button.hidden = state === 'off';
    if (state === 'on') button.dataset.fvGuide = 'agent'; else delete button.dataset.fvGuide;
}
function settleAgentTab(state, recheck, run, tries) {
    if (run !== agentRun) return;
    agentState = state;
    for (const ref of agentTabs) { const button = ref.deref(); if (button) showAgentTab(button); else agentTabs.delete(ref); }
    if (state !== 'unknown' || !recheck || tries >= 6) return;
    setTimeout(() => {
        if (run === agentRun) Promise.resolve().then(recheck).then(asState, () => 'unknown').then(next => settleAgentTab(next, recheck, run, tries + 1));
    }, 2000 * 2 ** tries);
}
export function agentTabReady(answer, recheck = null) {
    const run = agentRun = {};
    agentTab = Promise.resolve(answer).then(asState, () => 'unknown').then(state => { settleAgentTab(state, recheck, run, 0); return state; });
    return agentTab;
}

// The Autopilot page loads on demand, as the editing view does; a failure leaves the current view as it is.
export async function openAutopilot(node, options) {
    try { return (await import('./autopilot.js')).openAutopilotView(node, options); }
    catch (error) { console.warn('FreeVideo autopilot view:', error); return null; }
}

// The Image page loads on demand too; a failure leaves the current view as it is.
export async function openImage(node, options) {
    try { return (await import('./image_view.js')).openImageView(node, options); }
    catch (error) { console.warn('FreeVideo image view:', error); return null; }
}

export function viewSwitch(view, onStudio, onNodes, onEditor, onAutopilot, onImage = () => {}) {
    const group = document.createElement('div'); group.className = 'fv-view-switch';
    group.setAttribute('role', 'group'); group.setAttribute('aria-label', t('FreeVideo view', 'FreeVideo 视图'));
    for (const [id, label, action] of [['studio', t('Create', '创作面板'), onStudio], ['image', t('Images', '图像视图'), onImage], ['nodes', t('Nodes', '节点视图'), onNodes], ['editor', t('Edit', '剪辑视图'), onEditor],
        ['autopilot', t('Autopilot', 'AI 全自动'), onAutopilot]]) {
        const button = document.createElement('button'); button.type = 'button';
        const text = document.createElement('span'); text.textContent = label; button.append(text);
        // Browser translation must leave the product name as written.
        if (id === 'autopilot') text.setAttribute('translate', 'no');
        button.dataset.label = label; button.dataset.view = id;
        if (id === 'editor') button.dataset.fvGuide = 'longer';
        button.setAttribute('aria-pressed', String(view === id)); button.onclick = () => {
            const go = () => {
                const result = action?.();
                if (id === 'autopilot') Promise.resolve(result).then(() => import('./feature_tour.js')).then(({triggerFeatureTour}) => triggerFeatureTour(id)).catch(() => {});
                return result;
            };
            if (id === view) return go();
            // The pressed tab lights at once, so the held picture of the old view already shows where it is going.
            for (const tab of group.querySelectorAll('[data-view]')) tab.setAttribute('aria-pressed', String(tab === button));
            // A view that says no (the editor keeping unsaved work) stays in front, and so does its tab.
            const result = switchView(id, go);
            Promise.resolve(result).then(answer => { if (answer === false) syncTabs(); else keepFocusOnTab(id); }, () => syncTabs());
            return result;
        };
        if (id === 'autopilot') {
            // Known to be on (or unknown after a failed check): shown from the first frame, so a view never opens with
            // four tabs and then widens the group to five. Only an interface known to be off, or not asked yet, hides it.
            button.hidden = view !== id && (agentState === null || agentState === 'off');
            if (view === id) button.dataset.here = 'true';
            // Settled once the tab is shown or known to stay away; the first-run guide waits for it.
            group.ready = (agentTab || Promise.resolve('off')).then(state => { agentTabs.add(new WeakRef(button)); showAgentTab(button, agentState ?? state); });
        }
        group.append(button);
    }
    // The header this group goes into becomes the shared top bar as soon as it is in the page (topbar.js).
    adoptWhenPlaced(group);
    return group;
}

// The view that was left hid what had focus; focus goes to the pressed tab of the bar now in front, so keys go on
// from where the press was (never to the page itself). Focus the user already moved elsewhere stays.
function keepFocusOnTab(id) {
    const lost = () => { const at = document.activeElement; return !at || at === document.body || !at.checkVisibility?.(); };
    requestAnimationFrame(() => requestAnimationFrame(() => {
        if (!lost()) return;
        const tab = [...document.querySelectorAll(`.fv-view-switch [data-view="${id}"][aria-pressed=true]`)].find(b => b.checkVisibility?.());
        tab?.focus({preventScroll: true});
    }));
}

// The entries on the right of the top bar: the same set, in the same order, in every view (the node view's bar too).
// topbar.js builds them (status, search | phone, version, tutorials, feedback, settings); every view's header is taken
// over by topbar.js, so a view never lists its own. Returns {elements, lan, version, dispose} as before.
export function headerActions(tt = t) {
    const bar = topbarActions(tt);
    return {elements: [bar.element], lan: bar.lan, version: bar.version, dispose: bar.dispose};
}

const generateNodes = () => (app.graph?._nodes || []).filter(n => n.comfyClass === 'FreeVideoGenerate' || n.type === 'FreeVideoGenerate');

// A workflow without a FreeVideo node is the user's own: nothing of ours stays over it.
// Each workspace listens for this and closes itself.
export function collapseWorkspaces() {
    globalThis.FreeVideoShell?.settleShell?.();
    collapsing = true; setTimeout(() => { collapsing = false; }, 1000);
    window.dispatchEvent(new Event('freevideo-collapse'));
    for (const dialog of document.querySelectorAll('dialog.fv-studio[open]')) dialog.close();
}

// In FreeVideo's node view the canvas ground takes FreeVideo's colour (the grid stays) and ComfyUI's render statistics
// (T/I/N/V/FPS in the corner) are not drawn; ComfyUI's own settings come back as soon as the user's own workflow is in front.
let canvasGround = null;
function tintCanvas(on) {
    const canvas = app.canvas;
    if (!canvas || !('clear_background_color' in canvas)) return;
    if (on && canvasGround === null) {
        canvasGround = {color: canvas.clear_background_color, info: canvas.show_info};
        canvas.clear_background_color = getComputedStyle(document.documentElement).getPropertyValue('--fv-shell-bg').trim() || '#06080c';
        canvas.show_info = false;
    } else if (!on && canvasGround !== null) { canvas.clear_background_color = canvasGround.color; canvas.show_info = canvasGround.info; canvasGround = null; }
    else return;
    canvas.setDirty?.(true, true);
}

// The node view's default framing, on the first visit of a workflow: the whole graph centred in the space under the
// top bar, at a zoom where node titles are at least 13 px and widget text 12 px (fitting a large graph would shrink
// them to 8–9 px), and no further than 125 %. Later visits keep the user's own pan and zoom.
let nodeViewShown = false, lastView = null, nodeViewTouched = false;
// Whether the user worked on the canvas (dragged, scrolled or pressed keys on it) during the visit. A visit nobody
// touched (the node view is briefly in front, under the cover, while FreeVideo opens its first view) leaves no pan and
// zoom worth keeping once the canvas is framed behind the views (placeWidgetsEarly).
for (const type of ['pointerdown', 'wheel', 'keydown']) {
    globalThis.addEventListener?.(type, event => { if (nodeViewShown && event.target === app.canvas?.canvas) nodeViewTouched = true; }, {capture: true, passive: true});
}
function frameNodeView(focus = null, {bump = false} = {}) {
    const canvas = app.canvas, ds = canvas?.ds, nodes = app.graph?._nodes || [];
    const element = canvas?.canvas;
    if (!ds || !element || !nodes.length || !Array.isArray(ds.offset)) return;
    const graph = globalThis.LiteGraph || {};
    const title = graph.NODE_TITLE_HEIGHT || 30;
    let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
    for (const node of nodes) {
        const [x, y] = node.pos || [0, 0], [w, h] = node.size || [0, 0];
        left = Math.min(left, x); top = Math.min(top, y - title); right = Math.max(right, x + w); bottom = Math.max(bottom, y + h);
    }
    const width = element.clientWidth || innerWidth, height = element.clientHeight || innerHeight;
    const bar = toolbar?.getBoundingClientRect().height || 56, pad = 32;
    const fit = Math.min((width - 2 * pad) / Math.max(1, right - left), (height - bar - 2 * pad) / Math.max(1, bottom - top));
    const readable = Math.max(13 / (graph.NODE_TEXT_SIZE || 14), 12 / (graph.NODE_SUBTEXT_SIZE || 12));
    const scale = Math.max(readable, Math.min(fit, 1.25));
    // ComfyUI writes an HTML widget's place on the canvas only when the view changes; a real change of the view
    // first (one pixel, one frame) means every widget, also one mounted since, gets its position and transform.
    const before = [ds.scale, ds.offset[0], ds.offset[1]];
    ds.scale = scale;
    ds.offset[0] = width / 2 / scale - (left + right) / 2;
    // Taller than the room at that zoom: the titles at the top stay in sight just under the bar instead of centring.
    const tall = (bottom - top) * scale > height - bar - 2 * pad;
    ds.offset[1] = tall ? (bar + pad) / scale - top : (bar + (height - bar) / 2) / scale - (top + bottom) / 2;
    // A node whose result just arrived: its preview (the widget holding the video, else the lower part of the node)
    // is brought into sight, as much of it as fits, never pushing its top above the bar.
    const node = focus != null ? app.graph?.getNodeById?.(focus) : null;
    if (node) {
        const preview = (node.widgets || []).find(widget => widget.element?.querySelector?.('video') || widget.element?.tagName === 'VIDEO');
        const previewTop = node.pos[1] + (preview ? (preview.y ?? preview.last_y ?? node.size[1] * .5) : node.size[1] * .5);
        const nodeBottom = node.pos[1] + node.size[1];
        const screen = y => (y + ds.offset[1]) * scale;
        const over = screen(nodeBottom) - (height - pad), room = screen(previewTop) - (bar + pad);
        if (over > 0 && room > 0) ds.offset[1] -= Math.min(over, room) / scale;
    }
    const same = Math.abs(before[0] - ds.scale) < 1e-6 && Math.abs(before[1] - ds.offset[0]) < .5 && Math.abs(before[2] - ds.offset[1]) < .5;
    if (same && bump) {
        const settled = ds.offset[1];
        ds.offset[1] = settled + 1 / scale; canvas.setDirty?.(true, true);
        requestAnimationFrame(() => requestAnimationFrame(() => { if (Math.abs(ds.offset[1] - settled - 1 / scale) < 1e-6) ds.offset[1] = settled; canvas.setDirty?.(true, true); }));
        return;
    }
    canvas.setDirty?.(true, true);
}

// As soon as a FreeVideo workflow is in (usually behind a view), the canvas takes the node view's framing, so ComfyUI
// places the nodes' HTML widgets then, not at the first visit of the node view, where moving them would show as a
// layout shift.
let placedFor = '';
function placeWidgetsEarly(nodes) {
    const workflow = nodes.map(node => node.id).join(',');
    const canvas = app.canvas, ds = canvas?.ds;
    if (!workflow || workflow === placedFor || !ds || !Array.isArray(ds.offset)) return;
    placedFor = workflow;
    // Behind the view in front, the canvas is framed the way the node view will show it, so entering it later does
    // not move the widgets (ComfyUI moves them by position, which counts as a layout shift).
    // A pan and zoom the user left the node view with stays; an untouched visit's framing gives way to this one, so
    // entering the node view does not bring back a framing the widgets have since moved away from.
    const step = () => requestAnimationFrame(() => {
        if (currentView === 'nodes' && !toolbar?.hidden) return;
        if (lastView?.workflow === workflow && lastView.touched) return;
        lastView = null; frameNodeView();
    });
    // ComfyUI mounts the widgets some time after the graph is in: step now and again once they have had time to mount.
    step(); setTimeout(step, 1500); setTimeout(step, 4000);
}

// A result that arrives while the node view is in front: once ComfyUI has mounted the result's widgets (the page has
// been quiet for 300 ms, at most 2 s), the node view is framed again the same way as when it comes to the front,
// with that node's preview brought into sight. Nothing happens while another view is in front.
function reframeAfterResult(event) {
    if (currentView !== 'nodes' || !toolbar || toolbar.hidden) return;
    const focus = event?.detail?.node ?? null;
    let quiet = 0;
    const start = performance.now();
    const observer = new MutationObserver(() => { clearTimeout(quiet); quiet = setTimeout(done, 300); });
    const done = () => {
        observer.disconnect(); clearTimeout(quiet); clearTimeout(limit);
        if (currentView === 'nodes' && !toolbar.hidden) requestAnimationFrame(() => frameNodeView(focus, {bump: true}));
    };
    const limit = setTimeout(done, 2000);
    observer.observe(document.body, {childList: true, subtree: true});
    quiet = setTimeout(done, 300);
    void start;
}
globalThis.addEventListener?.('freevideo-result', reframeAfterResult);

export function refreshNavigation() {
    if (!toolbar) return;
    const nodes = generateNodes();
    if (!nodes.includes(selected)) selected = nodes[0];
    toolbar.hidden = !app.graph || !nodes.length || currentView !== 'nodes';
    noteView(null, {nodes: !toolbar.hidden});
    tintCanvas(!toolbar.hidden);
    placeWidgetsEarly(nodes);
    // Each time the node view comes to the front: readable and centred.
    // Right after the switch's own code (showNodes fits the graph first), before frames resume: the first visit of a
    // workflow gets the readable, centred framing; later visits get back the pan and zoom the user left it with, so
    // the node view never jumps. Leaving the node view remembers that pan and zoom.
    const workflow = nodes.map(node => node.id).join(',');
    if (!toolbar.hidden && !nodeViewShown) nodeViewTouched = false;
    if (!toolbar.hidden && !nodeViewShown) queueMicrotask(() => {
        const ds = app.canvas?.ds;
        if (lastView && lastView.workflow === workflow && ds && Array.isArray(ds.offset)) {
            ds.scale = lastView.scale; ds.offset[0] = lastView.offset[0]; ds.offset[1] = lastView.offset[1]; app.canvas.setDirty?.(true, true);
        } else frameNodeView();
    });
    if (toolbar.hidden && nodeViewShown && app.canvas?.ds?.offset) lastView = {workflow, touched: nodeViewTouched, scale: app.canvas.ds.scale, offset: [...app.canvas.ds.offset]};
    nodeViewShown = !toolbar.hidden;
    // Placed before the bar is painted, so its tabs and entries never move after it appears.
    if (!toolbar.hidden) place?.check();
    for (const button of toolbar.querySelectorAll('[data-view]')) {
        button.setAttribute('aria-pressed', String(button.dataset.view === currentView));
    }
}

export function openCreativeWorkspace({studioOnly = false} = {}) {
    // Settings replay belongs to the visible editing workspace too.
    const editor = studioOnly ? document.querySelector('dialog.fv-editor[open]') : null;
    if (studioOnly && editor?.freevideoSwitchView) return editor.freevideoSwitchView('studio').then(() => Boolean(document.querySelector('dialog.fv-studio:not(.fv-editor)[open]')));
    if (!studioOnly && currentView === 'editor' && document.querySelector('dialog.fv-editor[open]')) return true;
    refreshNavigation();
    const focused = Object.values(app.canvas?.selected_nodes || {}).find(n => n.comfyClass === 'FreeVideoGenerate' || n.type === 'FreeVideoGenerate');
    const node = currentView === 'studio' ? selected : focused || selected;
    if (!node || !open) return false;
    open(node);
    return true;
}

export function installNavigation(openStudio, agent = false, {openWorkflow = null, recheck = null} = {}) {
    open = openStudio;
    if (!toolbar) agentTabReady(agent, recheck);
    if (toolbar) { refreshNavigation(); return; }
    toolbar = document.createElement('aside'); toolbar.className = 'fv-view-navigation';
    toolbar.setAttribute('aria-label', t('FreeVideo workspace', 'FreeVideo 工作区'));
    const row = document.createElement('div'); row.className = 'fv-view-row';
    const choose = (action, create = false) => {
        const focused = Object.values(app.canvas?.selected_nodes || {}).find(n => (app.graph?._nodes || []).includes(n)
            && (n.comfyClass === 'FreeVideoGenerate' || n.type === 'FreeVideoGenerate'));
        refreshNavigation();
        let node = focused || selected;
        if (!node && create) {
            // The user's own workflow stays as it is: FreeVideo gets a workflow of its own.
            return (async () => {
                try {
                    // Clicks while the workflow is opening share that one workflow.
                    const first = !opening;
                    opening ||= Promise.resolve(openWorkflow?.()).finally(() => { opening = null; });
                    node = await opening;
                    if (!node) throw new Error('FreeVideo workflow unavailable');
                    selected = node;
                    return await action(node, first);
                } catch (error) {
                    console.warn('FreeVideo edit project:', error);
                    app.extensionManager?.toast?.add({severity: 'error', summary: 'FreeVideo',
                        detail: t('Could not complete. Try again.', '没有完成，请重试'), life: 9000});
                    return null;
                }
            })();
        }
        if (node) return action(node, false);
    };
    const tabs = viewSwitch('nodes', () => choose(node => { open?.(node); }, true), () => {}, () => choose(async (node, created) => {
        const handle = await openEditorView(node);
        if (created) handle?.notify(t('Created an editing project for you.', '已新建一个剪辑工程'));
        return handle;
    }, true), () => choose(node => openAutopilot(node), true), () => choose(node => openImage(node), true));
    // The same bar as every view's: logo, the tabs on the window's centre, the shared entries (topbar.js).
    const bar = buildBar(row, tabs);
    toolbar.append(row);
    // In the node view the notice is the toolbar's second row, as wide as the toolbar.
    toolbar.append(createUpdateNotice(cn).element); document.body.append(toolbar);
    place = bar.fit; refreshNavigation(); place.check();
    // health.js asks for this before it reports the toolbar missing. A
    // workspace removed without closing must not keep the toolbar hidden.
    globalThis.window?.addEventListener('freevideo-navigation-refresh', () => {
        if (currentView !== 'nodes' && !document.querySelector('dialog.fv-studio[open]')) currentView = 'nodes';
        refreshNavigation();
    });
}
