// The FreeVideo shell: what every view shares. It knows which view is in front, makes each view fill the
// window edge to edge, keeps ComfyUI's own frame, title and icon out of sight while FreeVideo is in front,
// and moves between views with one cross-fade (switchView). Other modules use window.FreeVideoShell or
// these exports; see SHELL-TASKS.md for the agreed names.
const sheet = document.createElement('link');
sheet.rel = 'stylesheet'; sheet.href = new URL('./shell.css', import.meta.url).href;
document.head.append(sheet);
// Text selection rules (tips): loaded once, whoever comes first (a11y.js loads it too).
const selectionHref = new URL('./selection.css', import.meta.url).href;
if (![...document.querySelectorAll('link[data-fv-sheet="selection"]')].length && ![...document.querySelectorAll('link[rel=stylesheet]')].some(link => link.href === selectionHref)) {
    const selection = document.createElement('link');
    selection.rel = 'stylesheet'; selection.href = selectionHref; selection.dataset.fvSheet = 'selection';
    document.head.append(selection);
}

export const VIEWS = ['studio', 'image', 'nodes', 'editor', 'autopilot'];
const VIEW_CLASS = {image: 'fv-image', editor: 'fv-editor', autopilot: 'fv-autopilot'};
const root = document.documentElement;
let view = 'nodes', nodesShown = false;
// Until the first graph is in and FreeVideo has decided what to show, a browser that has used FreeVideo before keeps
// ComfyUI's frame hidden (no flash of it on a reload); a decision for the user's own workflow gives it back.
let provisional = false;
const listeners = new Set();
// The compatibility view (Settings → 兼容界面) counts as reduced motion: views switch at once.
const reduced = () => root.dataset.glass === 'solid' || root.classList.contains('lite') || globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;

// View transitions (the picture held while a view comes to the front) cost main-thread time on every switch, so they
// are for machines that carry them: "auto" (default) switches them off at four cores or fewer, or when the first two
// switches after the first took over 400 ms to lay out (this session only); a stored "on" or "off" overrides both. Off, a switch just
// runs (no held picture); the pages that name pictures for it (task_float, the library shelf) follow the same flag.
const TRANSITIONS = 'freevideo.transitions', SLOW_CORES = 4, SLOW_SWITCH_MS = 400, SLOW_SAMPLES = 2;
const stored = key => { try { return localStorage.getItem(key); } catch { return null; } };
const keep = (key, value) => { try { localStorage.setItem(key, value); } catch { /* This page only. */ } };
export const transitionMode = () => { const mode = stored(TRANSITIONS); return mode === 'on' || mode === 'off' ? mode : 'auto'; };
export function transitionsOn(mode = transitionMode()) {
    if (mode !== 'auto') return mode === 'on';
    const cores = globalThis.navigator?.hardwareConcurrency;
    return !(cores > 0 && cores <= SLOW_CORES) && !slowNow;
}
function applyTransitions() { root.dataset.fvTransitions = transitionsOn() ? 'on' : 'off'; }
export function setTransitions(mode) {
    if (mode !== 'auto' && mode !== 'on' && mode !== 'off') return;
    keep(TRANSITIONS, mode);
    if (mode === 'auto') { slowNow = false; slowSeen.length = 0; switchesSeen = 0; }
    applyTransitions();
}
// The second and third switches of an "auto" machine (the first loads modules and is skipped): both slow → off from the
// next switch on, for this session. A fast machine's cold switches measure 150-360 ms, so the bar sits at 400.
const slowSeen = [];
let slowNow = false, switchesSeen = 0;
function noteSwitch(ms) {
    if (transitionMode() !== 'auto' || slowNow || slowSeen.length >= SLOW_SAMPLES || !(ms >= 0) || ++switchesSeen < 2) return;
    slowSeen.push(ms);
    if (slowSeen.length === SLOW_SAMPLES && slowSeen.every(value => value > SLOW_SWITCH_MS)) { slowNow = true; applyTransitions(); }
}
const canTransit = () => typeof document.startViewTransition === 'function' && root.dataset.fvTransitions !== 'off';

export const currentView = () => view;
export function onView(listener) { listeners.add(listener); return () => listeners.delete(listener); }

// The open dialog of a view, topmost first. Studio is the plain dialog.fv-studio with a view switch.
export function viewDialog(name = view) {
    const open = [...document.querySelectorAll('dialog.fv-studio[open]')].filter(dialog => dialog.dataset.closing !== 'true' || !dialog.getAnimations?.().some(a => a.id === 'fv-close' && a.playState === 'running'));
    const match = open.filter(dialog => VIEW_CLASS[name] ? dialog.classList.contains(VIEW_CLASS[name])
        : name === 'studio' && !Object.values(VIEW_CLASS).some(cls => dialog.classList.contains(cls)) && dialog.querySelector(':scope > .fv-header .fv-view-switch'));
    return match.at(-1) || null;
}

// Where something that floats over every view goes: inside the top view dialog (a modal makes the rest inert), else body.
export function overlayHost() {
    const open = [...document.querySelectorAll('dialog[open]')].filter(dialog => !dialog.matches('.fv-tip,.fv-shell-cover'));
    return open.at(-1) || document.body;
}

function markSurface() {
    const dialog = view === 'nodes' ? null : viewDialog(view);
    if (dialog) dialog.dataset.fvSurface = 'view';
    // A view opened again (kept alive) carries nothing of its last close: no fade end state, no closing mark.
    if (dialog) { for (const animation of dialog.getAnimations?.() || []) if (animation.id === 'fv-close') animation.cancel(); }
    // Views left open underneath (Create under Images, for example) are not painted while another view is in front,
    // so nothing of them can show through a view that is still filling in.
    // A view kept alive (closed, not destroyed) that is still open over the one in front, or any view still open in the
    // node view, is closed: an open modal view would cover the canvas or block the view in front. Create, left open
    // underneath another view by design, is only hidden.
    for (const other of document.querySelectorAll('dialog.fv-studio[data-fv-surface=view][open]')) {
        if (other === dialog) { delete other.dataset.fvBehind; continue; }
        const base = !Object.values(VIEW_CLASS).some(cls => other.classList.contains(cls));
        other.dataset.fvBehind = 'true';
        if (view === 'nodes' || !base) queueMicrotask(() => {
            if (other.open && other !== (view === 'nodes' ? null : viewDialog(view))) { other.close(); delete other.dataset.fvBehind; }
        });
    }
    // Arriving inside a cross-fade, the view does not also fade in on its own; one that faded in on its own never
    // fades in again (a later style change must not replay its entrance under another view).
    if (dialog && !dialog.dataset.fvQuiet) {
        if (switching) dialog.dataset.fvQuiet = 'true';
        else setTimeout(() => { dialog.dataset.fvQuiet = 'true'; }, 260);
    }
    const shell = view !== 'nodes' ? 'view' : nodesShown ? 'nodes' : provisional || cover ? 'cover' : '';
    if (shell === 'view' || shell === 'nodes') provisional = false;
    if (shell) root.dataset.fvShell = shell; else delete root.dataset.fvShell;
    // The whole page is FreeVideo's while it is in front: interface text is not selectable (shell.css).
    root.classList.toggle('fv-app', !!shell);
    root.dataset.fvView = view;
}

// view_navigation.js calls this whenever a view comes to the front (viewChanged) and when the node toolbar shows or hides.
export function noteView(next, {nodes} = {}) {
    if (nodes !== undefined) nodesShown = !!nodes;
    const changed = next && next !== view;
    if (next) view = next;
    markSurface();
    // Every tab group shows the view in front, also the bars of views still open underneath (a tab lit there before
    // a switch must not stay lit when that view comes back to the front).
    if (next) syncTabs();
    guardTitle();
    if (changed) {
        globalThis.requestAnimationFrame?.(raiseOverlays);
        for (const listener of [...listeners]) try { listener(view); } catch (error) { console.warn('FreeVideo shell listener:', error); }
        globalThis.dispatchEvent?.(new CustomEvent('freevideo-view', {detail: {view}}));
    }
}

// The view actually in front, read from the open dialogs (the node view when none is open).
export function frontView() {
    const open = [...document.querySelectorAll('dialog.fv-studio[open]')].filter(dialog => dialog.dataset.closing !== 'true');
    for (const dialog of open.reverse()) {
        for (const [name, cls] of Object.entries(VIEW_CLASS)) if (dialog.classList.contains(cls)) return name;
        if (dialog.querySelector(':scope > .fv-header .fv-view-switch')) return 'studio';
    }
    return 'nodes';
}
// view_navigation.js hands in its viewChanged, so a switch that failed half way (the old view closed, the new one
// never opened) puts every part back on the view the user actually sees.
let resync = null;
export function setViewResync(handler) { resync = handler; }

export function syncTabs(shown = view) {
    for (const tab of document.querySelectorAll('.fv-view-switch [data-view]')) {
        const on = String(tab.dataset.view === shown);
        if (tab.getAttribute('aria-pressed') !== on) tab.setAttribute('aria-pressed', on);
    }
}

// Go to a view as if its tab were pressed; resolves once it is in front (or after 4 s).
export function go(target) {
    if (!VIEWS.includes(target)) return Promise.reject(new Error(`unknown view ${target}`));
    if (target === view) return Promise.resolve(view);
    const header = view === 'nodes' ? document.querySelector('.fv-view-navigation:not([hidden])') : viewDialog(view);
    const tab = header?.querySelector(`.fv-view-switch [data-view="${target}"]`);
    if (!tab) return Promise.reject(new Error('view switch unavailable'));
    const arrived = new Promise(resolve => {
        const timer = setTimeout(() => { off(); resolve(view); }, 4000);
        const off = onView(now => { if (now === target) { clearTimeout(timer); off(); resolve(now); } });
    });
    tab.click();
    return arrived;
}

// ── Moving between views: one cross-fade, no empty frame ─────────────────────────────────────────
// The old view's picture is held (with the pressed tab already lit) while the next view opens; once it is in front
// and ready, the two cross-fade in 200 ms, eased out, opacity only, so the top bar, identical in both, does not move.
// A click during a switch skips the running one and starts from what is on screen. Reduced motion swaps at once.
// Browsers without view transitions run the switch as before (each view fades in on its own).
export const SWITCH_HOLD = 800, READY_WAIT = 800;
let switching = null;
export const isSwitching = () => !!switching;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const frames = n => new Promise(resolve => { const step = left => left ? requestAnimationFrame(() => step(left - 1)) : resolve(); step(n); });
function arrival(target) {
    if (view === target) return Promise.resolve();
    return new Promise(resolve => { const off = onView(now => { if (now === target) { off(); resolve(); } }); });
}
// While the old picture is held, the new view fills in underneath, so its placeholders never show:
// - data-fv-ready="false" → "true": the first screen is drawn with its content (waited for at most READY_WAIT, 800 ms,
//   which also covers a slow connection; after that the view rises as it is);
// - data-fv-loaded="false" → "true": its content (project, list, picture) is drawn (waited for at most LOADED_WAIT).
// The editor fills in late by nature, so its content is waited for even before it marks data-fv-loaded.
// A view that never marks itself is waited for READY_WAIT at most.
export const LOADED_WAIT = 800;
function readiness(dialog) {
    if (!dialog) return Promise.resolve();
    // The four views mark their first screen with data-fv-ready="true"; until they do (absent or "false"), it is not ready.
    const pendingReady = () => dialog.dataset.fvReady !== 'true';
    const pendingLoaded = () => dialog.dataset.fvLoaded === 'false' || (dialog.classList.contains('fv-editor') && dialog.dataset.fvLoaded !== 'true');
    // The picture of the main block (a video's current frame, an image) has something to show.
    const pendingMedia = () => [...dialog.querySelectorAll('[data-fv-main] :is(img,video), :scope > .fv-header img')].some(media => media.getClientRects().length
        && (media.tagName === 'IMG' ? !(media.complete && media.naturalWidth) : (media.currentSrc || media.src) && media.readyState < 2));
    const limit = Math.max(READY_WAIT, LOADED_WAIT), start = performance.now();
    // Frames run in this phase, so the view's own observers and loads go on; checked once a frame.
    return (async () => {
        while ((pendingReady() || pendingLoaded() || pendingMedia()) && performance.now() - start < limit) await frames(1);
    })();
}
function markLeaving(name) {
    // The node view's canvas menus close with it.
    if (name === 'nodes') for (const menu of document.querySelectorAll('.litecontextmenu')) menu.remove();
    const element = name === 'nodes' ? document.querySelector('.fv-view-navigation') : viewDialog(name);
    if (element) element.dataset.fvLeaving = 'true';
    globalThis.dispatchEvent?.(new CustomEvent('freevideo-view-leave', {detail: {view: name, element}}));
    return element;
}
// The bar in front is its own layer during a switch, so the incoming view's rise does not move it. Only one element
// may carry the name at a time: the front bar of the old picture, then the front bar of the new one.
function frontBar() {
    if (view === 'nodes') return document.querySelector('.fv-view-navigation:not([hidden])');
    return viewDialog(view)?.querySelector(':scope > .fv-header') || null;
}
// Two elements with the same view-transition-name make the browser skip the whole switch (a hard cut); keep the last.
function uniqueNames() {
    const seen = new Map();
    for (const element of document.querySelectorAll('[style*="view-transition-name"]')) {
        const name = element.style.getPropertyValue('view-transition-name');
        if (!name || name === 'none') continue;
        if (seen.has(name)) seen.get(name).style.removeProperty('view-transition-name');
        seen.set(name, element);
    }
}
function nameBar(bar) {
    for (const named of document.querySelectorAll('[data-fv-topbar]')) { named.style.removeProperty('view-transition-name'); delete named.dataset.fvTopbar; }
    if (bar) { bar.style.setProperty('view-transition-name', 'fv-topbar'); bar.dataset.fvTopbar = 'true'; }
}
// A press during a switch, before the rise has started, changes where the switch goes (the last press wins, nothing
// queues): the new target is kept (replacing any earlier one) and, once the previous press's view is in front, the
// switch starts over from the old picture still on screen (pressPending), so two views never open at once and the
// held picture never changes. A press during the rise ends that rise (one frame from where it was to its end) and
// starts a new switch from what is on screen.
function retarget(token, target) {
    if (target === (token.pending ?? token.target)) return Promise.resolve(token.result);
    token.pending = target; token.mark.retargets = (token.mark.retargets || 0) + 1;
    return Promise.resolve(token.result);
}
// The kept target, once the previous press's view is in front. That view is still invisible (its picture is held at
// opacity 0 over the old one), so the switch starts over from what is on screen: the arrived view is hidden, this
// switch gives way, and the kept target is pressed on the arrived view's bar (its own action knows how to leave it).
// The arrived view's opacity comes back as soon as the new switch has taken its old picture.
const afterCapture = [];
function pressPending(token) {
    const next = token.pending;
    if (!next) return false;
    token.pending = null;
    const tab = frontBar()?.querySelector(`.fv-view-switch [data-view="${next}"]`);
    if (!tab || next === view) { token.target = view; token.mark.target = view; return false; }
    const shown = view === 'nodes' ? null : viewDialog(view);
    if (shown) { shown.style.opacity = '0'; afterCapture.push(() => shown.style.removeProperty('opacity')); }
    token.mark.restarted = next;
    if (switching === token) switching = null;
    tab.click();
    return true;
}

// The node view is ready when ComfyUI has put the nodes' HTML widgets in place: their boxes unchanged for two frames
// (at most READY_WAIT), so they never move after the node view rises.
async function nodesSettled() {
    const read = () => [...document.querySelectorAll('.dom-widget')].map(element => { const box = element.getBoundingClientRect(); return `${box.x | 0},${box.y | 0},${box.width | 0},${box.height | 0}`; }).join(';');
    const start = performance.now();
    let last = read(), steady = 0;
    while (steady < 2 && performance.now() - start < READY_WAIT) { await frames(1); const now = read(); steady = now === last ? steady + 1 : 0; last = now; }
}

// The rise of the new picture over the held old one (the view, and its bar over the old bar), then the hold ends.
function riseOver(token, {shift = true} = {}) {
    token.animating = true;
    const hold = document.getAnimations().filter(a => String(a.effect?.pseudoElement || '').startsWith('::view-transition-group(root)'));
    const end = () => hold.forEach(a => { try { a.finish(); } catch { /* Already gone. */ } });
    token.mark.animating ??= +(performance.now() - token.mark.press).toFixed(1);
    const instant = reduced() || root.dataset.fvSwitching === 'instant';
    token.rise = root.animate(instant ? [{opacity: 1}, {opacity: 1}] : [{opacity: 0, transform: shift ? 'translateY(4px)' : 'none'}, {opacity: 1, transform: 'none'}],
        {duration: instant ? 1 : 200, easing: 'cubic-bezier(.2,.8,.2,1)', pseudoElement: '::view-transition-new(root)', fill: 'forwards'});
    // The bar: the old one stays whole underneath while the new one (same place, same parts) fades in over it,
    // so no frame lacks the logo or an entry, even if the new bar's pictures are still decoding.
    try { root.animate([{opacity: 0}, {opacity: 1}], {duration: instant ? 1 : 200, easing: 'cubic-bezier(.2,.8,.2,1)', pseudoElement: '::view-transition-new(fv-topbar)', fill: 'forwards'}); } catch { /* No bar layer here. */ }
    token.rise.finished.then(end, end);
}

// For a panel that opens without a view switch (Creations, settings, a large sheet): the current picture is held
// while `open()` builds the panel, until the panel says its first screen is drawn, at most 800 ms, then the panel
// fades in over it. `open()` returns the panel element (or a promise of it); the panel marks itself
// data-fv-ready="false" → "true" (and may use data-fv-main media), or the caller passes `ready`, a promise.
//   FreeVideoShell.holdWhile(() => openLibrary(...), {ready: libraryShown})
// Without view transitions (or during a view switch) open() simply runs.
export function holdWhile(open, {ready = null} = {}) {
    if (!canTransit() || switching) return Promise.resolve().then(open);
    const mark = {target: 'panel', from: view, press: performance.now()}, log = globalThis.__fvSwitches ||= [];
    log.push(mark); if (log.length > 10) log.shift();
    const token = {target: view, mark, animating: false, panel: true};
    switching = token;
    root.dataset.fvSwitching = reduced() ? 'instant' : 'fade';
    nameBar(frontBar()); uniqueNames();
    let result, release = () => {};
    const done = () => { release(); if (switching === token) { switching = null; delete root.dataset.fvSwitching; nameBar(null); } };
    try {
        token.transition = document.startViewTransition(async () => {
            mark.captured = +(performance.now() - mark.press).toFixed(1);
            try { result = open(); } catch (error) { result = Promise.reject(error); }
            await Promise.race([Promise.resolve(result).catch(() => null), sleep(SWITCH_HOLD)]);
            release = holdFocus();
        });
    } catch (error) { done(); return Promise.resolve().then(open); }
    token.transition.ready.then(async () => {
        const panel = await Promise.race([Promise.resolve(result).catch(() => null), sleep(READY_WAIT).then(() => null)]);
        const waits = [readiness(panel instanceof Element ? panel : null)];
        if (ready) waits.push(Promise.resolve(ready).catch(() => {}));
        await Promise.race([Promise.all(waits), sleep(READY_WAIT)]);
        await frames(2);
        mark.ready = +(performance.now() - mark.press).toFixed(1);
        if (switching === token) riseOver(token, {shift: false});
    }, () => {});
    token.transition.finished.then(() => { mark.shown = +(performance.now() - mark.press).toFixed(1); done(); }, done);
    return token.transition.updateCallbackDone.then(() => result, () => result);
}

// When the held picture starts fading over a panel just opened as a modal dialog, the browser drops the panel's focus
// to the page and will not take it back until the fade has ended, so its keys (Escape, the arrows) would go nowhere.
// When the fade ends, focus that fell to the page goes back to what the panel had focused; focus moved on purpose
// (to another element) is left alone.
function holdFocus() {
    const held = document.activeElement;
    if (!held || held === document.body || held === document.documentElement) return () => {};
    return () => {
        const now = document.activeElement;
        if ((!now || now === document.body) && held.isConnected && !held.closest('[inert]') && (held.tagName !== 'DIALOG' || held.open)) held.focus({preventScroll: true});
    };
}

// ── No placeholder at a press ─────────────────────────────────────────────────────────────────────
// A press lights its tab at once (no slide); the picture held while the view comes to the front is the view the user
// was on, never a placeholder. A view comes back as it was left (the views are kept, or draw from what they kept), so
// it is ready within a few frames and rises over the held picture at once; what is new arrives in place afterwards.

export function switchView(target, run) {
    if (switching && !switching.animating) return retarget(switching, target);
    if (target === view) return run();
    const from = view, leaving = markLeaving(from);
    const unmark = () => { if (leaving) delete leaving.dataset.fvLeaving; };
    if (!canTransit()) { switching?.transition?.skipTransition?.(); try { return run(); } finally { unmark(); } }
    switching?.transition?.skipTransition?.();
    // Phase times of the last switches (ms since the press), for tests/shell_switch_check.py.
    const mark = {target, from, press: performance.now()}, log = globalThis.__fvSwitches ||= [];
    log.push(mark); if (log.length > 10) log.shift();
    const at = name => { if (mark[name] === undefined) mark[name] = +(performance.now() - mark.press).toFixed(1); };
    const token = {target, mark, result: undefined, animating: false, pending: null, kick: null, watch: null};
    switching = token;
    root.dataset.fvSwitching = reduced() ? 'instant' : 'fade';
    // In front: the view the switch now aims at (it may change while waiting).
    const arrived = () => new Promise(resolve => {
        let off = () => {};
        const check = () => { if (view === token.target) { off(); resolve(); } };
        off = onView(check); token.kick = check; check();
    });
    // The press's action said no (the editor keeping unsaved work, for example).
    const refused = () => new Promise(resolve => {
        token.watch = value => Promise.resolve(value).then(answer => { if (value === token.result && (answer === false || answer === null)) resolve(); },
            () => { if (value === token.result) resolve(); });
        token.watch(token.result);
    });
    nameBar(frontBar());
    uniqueNames();
    try {
        // Phase 1 (frames held by the browser): the old picture is taken, the switch runs, the new view comes to the front.
        token.transition = document.startViewTransition(async () => {
            at('captured');
            for (const restore of afterCapture.splice(0)) restore();
            try { token.result = run(); } catch (error) { token.result = Promise.reject(error); }
            await Promise.race([arrived(), refused(), sleep(SWITCH_HOLD)]);
            at('arrived');
            nameBar(frontBar());
        });
        // Phase 2 (frames run again, the new picture still at opacity 0): the new view lays out and fills in for real
        // (at most READY_WAIT), then rises over the old picture; the hold ends when the rise is done. A press in the
        // meantime changes the target and the wait starts over for it.
        token.transition.ready.then(async () => {
            for (;;) {
                await Promise.race([arrived(), refused(), sleep(SWITCH_HOLD)]);
                    if (switching !== token) return;
                if (pressPending(token)) return;
                if (view === token.target) await (token.target === 'nodes' ? nodesSettled() : readiness(viewDialog(token.target)));
                await frames(2);
                if (switching !== token) return;
                if (!token.pending) break;
            }
            nameBar(frontBar());
            at('ready');
            noteSwitch(mark.ready);
            riseOver(token);
        }, () => {});
        token.transition.ready.catch(() => at('skipped'));
        token.transition.finished.then(() => at('shown'), () => at('skipped'));
    } catch (error) { switching = null; delete root.dataset.fvSwitching; nameBar(null); unmark(); throw error; }
    const done = () => {
        if (switching === token) { switching = null; delete root.dataset.fvSwitching; nameBar(null); for (const restore of afterCapture.splice(0)) restore(); }
        unmark();
        // A switch that did not happen (or was overtaken) leaves the tabs showing where the user is.
        if (switching === null) {
            const actual = frontView();
            if (actual !== view) { if (resync) resync(actual); else noteView(actual); }
            syncTabs();
        }
    };
    token.transition.finished.then(done, done);
    return token.transition.updateCallbackDone.then(() => token.result, () => token.result);
}

// During a switch the window shows pictures, but presses pass through them (shell.css): a view name in the bar takes
// its press as always (the bars are the same in every view, so the name under the pointer is the one seen), which
// changes where the switch goes. Every other press, wheel or menu does nothing until the new view is shown, so
// nothing on a page that cannot be seen yet is pressed. A press that still lands on the page root (a browser that
// hit-tests the pictures) goes to the tab of the bar in front under it.
const SWITCH_INPUT = ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'dblclick', 'auxclick', 'contextmenu', 'wheel', 'touchstart'];
function pressDuringSwitch(event) {
    if (!switching || !(event.target instanceof Element)) return;
    const onTab = event.target !== root && event.target.closest('.fv-view-switch [data-view]');
    if (onTab && !(event.button > 0) && event.type !== 'contextmenu' && event.type !== 'wheel') return;
    if (event.cancelable) event.preventDefault();
    event.stopImmediatePropagation();
    if (event.type !== 'click' || event.target !== root || event.button > 0) return;
    const bar = frontBar();
    const tab = bar && [...bar.querySelectorAll('.fv-view-switch [data-view]')].find(button => {
        if (button.hidden || !button.getClientRects().length) return false;
        const box = button.getBoundingClientRect();
        return event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
    });
    tab?.click();
}

// ── Mount points for what floats over every view (glob's top bar spec, approved) ───────────────────
// Reminders and undo bars (one at a time) live in the top bar's right column (its width from --fv-right-col, which
// the top bar writes) and, below 720 px, in a floating strip at the bottom. Notifications have no bell of their own:
// they open from the run status capsule, the element marked data-fv-status in the bar in front.
let toasts = null;
function raise(element) {
    if (!element?.showPopover) return;
    try { if (element.matches(':popover-open')) element.hidePopover(); element.showPopover(); } catch { /* Not connected. */ }
}
export function toastHost() {
    if (toasts?.isConnected) return toasts;
    toasts = document.createElement('div'); toasts.className = 'fv-toasts'; toasts.id = 'fv-toasts';
    toasts.setAttribute('role', 'status'); toasts.setAttribute('aria-live', 'polite'); toasts.dataset.fvTipScope = '';
    if (toasts.showPopover) toasts.popover = 'manual';
    document.body.append(toasts);
    // Shown while it holds something, above whatever view opened last.
    new MutationObserver(() => {
        if (toasts.childElementCount) raise(toasts); else { try { toasts.hidePopover?.(); } catch { /* Hidden. */ } }
    }).observe(toasts, {childList: true});
    return toasts;
}
export function statusAnchor() { return frontBar()?.querySelector('[data-fv-status]') || null; }
function raiseOverlays() { if (toasts?.childElementCount) raise(toasts); }

// ── The browser tab says FreeVideo, with FreeVideo's icon, whatever ComfyUI writes there. ──
const ICON = new URL('./assets/icon.svg', import.meta.url).href;
const TITLE = 'FreeVideo';
// ComfyUI writes "[42%][Node] *Workflow - ComfyUI"; FreeVideo's own notices ("生成好了", a prefix) are kept.
export const shellTitle = title => /ComfyUI/i.test(String(title || '')) || !String(title || '').trim() ? TITLE : String(title);
let guarding = false;
function guardTitle() {
    if (guarding || !document.head) return;
    const wanted = shellTitle(document.title);
    if (document.title !== wanted) { guarding = true; document.title = wanted; guarding = false; }
    for (const link of document.querySelectorAll('link[rel~="icon"]')) {
        if (link.dataset.fvIcon !== 'true') { link.dataset.fvIcon = 'true'; }
        if (link.href !== ICON) { link.href = ICON; link.type = 'image/svg+xml'; }
    }
    if (!document.querySelector('link[rel~="icon"]')) {
        const link = document.createElement('link'); link.rel = 'icon'; link.type = 'image/svg+xml'; link.href = ICON; link.dataset.fvIcon = 'true';
        document.head.append(link);
    }
}
function watchHead() {
    if (!globalThis.MutationObserver || !document.head) return;
    new MutationObserver(() => guardTitle()).observe(document.head, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['href']});
}

// ── ComfyUI's loading screen reads its colours from these keys before any extension loads. Ink equal to the ground
// leaves a plain FreeVideo-coloured screen instead of ComfyUI's mark on the next load. ComfyUI rewrites them when its
// palette is applied, so they are written again after that. ──
function quietSplash() {
    try {
        const ground = getComputedStyle(root).getPropertyValue('--fv-shell-bg').trim() || '#06080c';
        localStorage.setItem('comfy-splash-bg', ground); localStorage.setItem('comfy-splash-fg', ground);
    } catch { /* Private browsing: the loading screen keeps ComfyUI's colours. */ }
}

// ── The cover over ComfyUI while FreeVideo opens its first view (a launcher visit or a reload of one). ──
let cover = null;
export function showCover() {
    if (cover || !document.body) return cover;
    cover = document.createElement('div'); cover.className = 'fv-shell-cover'; cover.setAttribute('aria-hidden', 'true');
    const mark = document.createElement('img'); mark.src = new URL(root.dataset.theme === 'light' ? './assets/freevideo-light.svg' : './assets/freevideo.svg', import.meta.url).href; mark.alt = ''; mark.draggable = false;
    cover.append(mark);
    // A popover sits in the top layer: above ComfyUI's own overlays, below any view dialog opened after it.
    if (cover.showPopover) { cover.popover = 'manual'; document.body.append(cover); try { cover.showPopover(); } catch { /* Not in the top layer: fixed and on top still. */ } }
    else document.body.append(cover);
    markSurface();
    const safety = setTimeout(hideCover, 20000);
    cover.fvSafety = safety;
    return cover;
}
// FreeVideo has decided what to show (freevideo.js after the first graph, collapseWorkspaces for the user's own one).
export function settleShell() { bootDone(); if (provisional) { provisional = false; markSurface(); } }
// The engine's opening screen (freevideo_engine/comfy_boot.py), in place from the page's first frame on a visit that
// opens FreeVideo: it stands in for the cover and gives way when FreeVideo has decided what to show.
const booting = () => globalThis.__freevideoBoot?.active === true;
function bootDone() { try { globalThis.__freevideoBoot?.done?.(); } catch { /* Its own timer still ends it. */ } }
export function hideCover() {
    settleShell();
    const current = cover; if (!current) return;
    cover = null; clearTimeout(current.fvSafety);
    markSurface();
    const done = () => { try { current.hidePopover?.(); } catch { /* Already hidden. */ } current.remove(); };
    if (reduced() || !current.animate) { done(); return; }
    current.animate([{opacity: 1}, {opacity: 0}], {duration: 200, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'forwards'}).finished.then(done, done);
}

// ── Theme and material: dark unless ComfyUI's palette is light; a stored choice can switch glass off. ──
export function setTheme(theme) { if (theme === 'light' || theme === 'dark') root.dataset.theme = theme; }
// The material: 'calm' (default: solid panels, glass only on floating layers) or 'glass' (the earlier material).
export function setMaterial(material) {
    if (material === 'glass') root.dataset.fvMaterial = 'glass'; else delete root.dataset.fvMaterial;
    try { localStorage.setItem('freevideo.material', material === 'glass' ? 'glass' : 'calm'); } catch { /* This page only. */ }
}
export function setLook(look) {
    if (look !== 'calm' && look !== 'stage') return;
    root.dataset.fvLook = look;
    try { localStorage.setItem('freevideo.look', look); } catch { /* This page only. */ }
}
export function setGlass(on) {
    if (on) delete root.dataset.glass; else root.dataset.glass = 'solid';
    try { localStorage.setItem('freevideo.glass', on ? 'on' : 'solid'); } catch { /* Kept for this page only. */ }
}

// Something floating that Escape should close first: a menu, popover, inner dialog, listbox, coach card or an
// expanded control, inside the view or anywhere on the page (popovers live in the top layer).
const FLOATING = '[popover]:popover-open:not(.fv-tip,.fv-shell-cover),[role=dialog]:not(dialog),[role=menu],[role=listbox][data-fv-popup],dialog[open] dialog[open],[aria-expanded=true],details[open][data-fv-menu],[data-fv-layer]';
function floatingOpen(dialog) {
    const visible = element => element.getClientRects().length > 0 && !element.closest('[hidden],[inert]') && getComputedStyle(element).visibility !== 'hidden';
    for (const element of dialog.querySelectorAll(FLOATING)) if (visible(element)) return true;
    for (const element of document.querySelectorAll('[popover]:popover-open:not(.fv-tip,.fv-shell-cover)')) if (visible(element)) return true;
    return false;
}

export function installShell({cover: wantCover = false} = {}) {
    if (globalThis.FreeVideoShell?.installed) return globalThis.FreeVideoShell;
    root.dataset.theme ||= 'dark';
    try { if (localStorage.getItem('freevideo.glass') === 'solid') root.dataset.glass = 'solid'; } catch { /* Glass stays on. */ }
    applyTransitions();
    // Says once, with a button to Settings, when the interface runs slowly here (ui_speed.js); never switches by itself.
    import('./ui_speed.js').then(({watchSpeed}) => watchSpeed()).catch(() => {});
    try { if (localStorage.getItem('freevideo.material') === 'glass' || new URLSearchParams(location.search).get('freevideo_material') === 'glass') root.dataset.fvMaterial = 'glass'; } catch { /* Calm. */ }
    // The look over the material: "stage" (default: polish.css brings back restrained light and texture) or "calm".
    let look = 'stage';
    try { const asked = new URLSearchParams(location.search).get('freevideo_look') || localStorage.getItem('freevideo.look'); if (asked === 'calm' || asked === 'stage') look = asked; } catch { /* Stage. */ }
    root.dataset.fvLook = look;
    if (!document.querySelector('link[data-fv-sheet="polish"]')) {
        const polish = document.createElement('link'); polish.rel = 'stylesheet'; polish.dataset.fvSheet = 'polish';
        polish.href = new URL('./glass/polish.css', import.meta.url).href; document.head.append(polish);
        // The stage light follows the work in front (the look's own module; it installs itself).
        import('./glass/polish.js').catch(error => console.warn('FreeVideo look:', error));
        // The picture as the light behind the views, and glass on the windows over it (the stage look's own module).
        import('./glass/stage_light.js').catch(error => console.warn('FreeVideo stage light:', error));
    }
    // FreeVideo's own right-click menu wherever a part has none of its own (text boxes, links, the empty page).
    // ComfyUI's Templates window: Chinese words for its search box and first category, and the light theme.
    if (!document.querySelector('link[data-fv-sheet="comfy-templates"]')) {
        const sheet = document.createElement('link'); sheet.rel = 'stylesheet'; sheet.dataset.fvSheet = 'comfy-templates';
        sheet.href = new URL('./comfy_templates.css', import.meta.url).href; document.head.append(sheet);
    }
    import('./comfy_templates.js').then(({watchTemplates}) => watchTemplates()).catch(error => console.warn('FreeVideo templates:', error));
    import('./context_fallback.js').catch(error => console.warn('FreeVideo menu:', error));
    guardTitle(); watchHead(); quietSplash();
    for (const type of SWITCH_INPUT) globalThis.addEventListener?.(type, pressDuringSwitch, {capture: true, passive: false});
    // Edge to edge on phones and tablets too: the page may use the area under a notch (the bars pad themselves with
    // the safe-area insets), and the browser's own frame takes FreeVideo's ground colour.
    const viewport = document.querySelector('meta[name=viewport]');
    if (viewport && !/viewport-fit/.test(viewport.content)) viewport.content = `${viewport.content},viewport-fit=cover`;
    if (!document.querySelector('meta[name=theme-color]') && document.head) {
        const tint = document.createElement('meta'); tint.name = 'theme-color';
        tint.content = getComputedStyle(root).getPropertyValue('--fv-shell-bg').trim() || '#06080c'; document.head.append(tint);
    }
    setTimeout(quietSplash, 4000);
    // Only a launcher visit that will open a view is held back by the cover; the user's own ComfyUI is never painted over.
    try { provisional = wantCover && !!localStorage.getItem('freevideo.view'); } catch { provisional = false; }
    if (provisional) { markSurface(); setTimeout(settleShell, 15000); }
    if (wantCover && !booting()) showCover();
    // Escape closes what floats inside a view, never the view itself: views are tabs. The browser's own handling would
    // close a view on a second Escape even when the view refuses the first, so on a view the key's default is taken
    // and the view gets a cancel event of its own (its menus and panels still close on it); closeDialog then keeps
    // the view open. An open popover menu keeps the browser's handling, so it still closes on Escape.
    // Only when nothing inside the view would take the key (no open menu, popover, panel or coach card): then
    // there is nothing for Escape to close, and taking its default only stops the browser from closing the view.
    globalThis.addEventListener?.('keydown', event => {
        if (event.key !== 'Escape' || event.isComposing) return;
        const dialog = event.target instanceof Element ? event.target.closest('dialog') : null;
        if (!dialog || !dialog.matches('dialog.fv-studio[data-fv-surface=view]') || dialog.inert) return;
        if (floatingOpen(dialog)) return;
        event.preventDefault();
        queueMicrotask(() => { if (dialog.open) dialog.dispatchEvent(new Event('cancel', {cancelable: true})); });
    }, true);
    document.addEventListener('cancel', event => {
        if (!(event.target instanceof Element) || !event.target.matches('dialog.fv-studio[data-fv-surface=view]')) return;
        event.target.fvEscapedAt = performance.now();
        // A cancel the view cannot refuse means the browser closes the view right after (a repeated Escape):
        // the close that follows is undone below.
        if (!event.cancelable) event.target.fvForcedClose = true;
    }, true);
    // Safety net: a view the browser closed on Escape opens again at once, before its own close handler runs.
    document.addEventListener('close', event => {
        const dialog = event.target;
        if (!(dialog instanceof HTMLDialogElement) || !dialog.fvForcedClose) return;
        dialog.fvForcedClose = false;
        if (dialog.open || !dialog.isConnected) return;
        event.stopImmediatePropagation();
        try { dialog.showModal(); } catch (error) { console.warn('FreeVideo view reopen:', error); }
    }, true);
    globalThis.FreeVideoShell = {installed: true, view: currentView, onView, go, overlayHost, viewDialog, VIEWS, hideCover, settleShell, setTheme, setGlass, setTransitions, transitionMode, transitionsOn, setMaterial, setLook, switchView, switching: isSwitching, toastHost, statusAnchor, holdWhile};
    return globalThis.FreeVideoShell;
}
