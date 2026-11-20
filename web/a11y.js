// Keyboard and focus for every FreeVideo surface, in one place:
// - One visible focus ring (a11y.css). A ring that an ancestor's overflow would cut off is drawn inside the control.
// - Tab stays inside the layer in front (a modal dialog, an open popover or menu that holds focus) and wraps around.
// - A Tab stop that cannot be seen (faded to nothing, zero size, covered by its own panel being folded) is skipped.
// - Escape closes the open non-modal layer that holds focus (a phone panel, a status panel, a menu) when nothing else
//   took the key; modal dialogs keep their own Escape (the browser's cancel and the view's handlers).
// - Ctrl/Cmd+A selects all only inside a typing field (or inside the copyable text that holds focus); elsewhere it does
//   not paint the whole window blue. A view that handles the keys itself (select all clips) keeps them.
// - The text-selection rules (selection.css) and these styles load once, whoever loads this module first.
const doc = globalThis.document;

function sheet(name) {
    if (!doc?.head?.querySelector || doc.head.querySelector(`link[data-fv-sheet="${name}"]`)) return;
    const href = new URL(`./${name}.css`, import.meta.url).href;
    if ([...doc.querySelectorAll('link[rel=stylesheet]')].some(link => link.href === href)) return;
    const link = doc.createElement('link');
    link.rel = 'stylesheet'; link.href = href; link.dataset.fvSheet = name;
    doc.head.append(link);
}

export const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex]:not([tabindex="-1"]),[contenteditable=""],[contenteditable=true]';
const TYPING = 'input:not([type=button],[type=checkbox],[type=radio],[type=range],[type=submit],[type=reset],[type=file],[type=color],[type=image]),textarea,[contenteditable=""],[contenteditable=true]';
const LAYER = 'dialog[open],[popover]:popover-open,[role=dialog],[role=alertdialog],[role=menu],[role=listbox][data-fv-popup]';

const shown = element => {
    if (!element.isConnected || element.closest('[inert],[hidden]')) return false;
    const style = getComputedStyle(element);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    return element.getClientRects().length > 0;
};

// The focusable controls of a layer, in tab order (positive tabindex first, then document order).
export function tabbables(root) {
    const list = [...root.querySelectorAll(FOCUSABLE)].filter(element => {
        if (!shown(element)) return false;
        // A control inside a closed <details> body is not reachable.
        const details = element.closest('details:not([open])');
        if (details && !element.matches('summary') && details.contains(element) && element.parentElement !== details) return false;
        if (details && element.matches('summary') && element.parentElement !== details) return false;
        // Radios: only the checked one (or the first) of a group takes a stop.
        if (element.matches('input[type=radio]') && element.name) {
            const group = [...root.querySelectorAll(`input[type=radio][name="${CSS.escape(element.name)}"]`)];
            const stop = group.find(radio => radio.checked) || group[0];
            if (stop !== element) return false;
        }
        return true;
    });
    const positive = list.filter(element => element.tabIndex > 0).sort((a, b) => a.tabIndex - b.tabIndex);
    return [...positive, ...list.filter(element => !(element.tabIndex > 0))];
}

const isViewDialog = element => element.matches?.('dialog.fv-studio,[data-fv-surface=view]');
const isModal = element => { try { return element.matches(':modal'); } catch { return false; } };

// A layer that holds the keyboard while it is open: a modal dialog, a light-dismiss popover, a dialog or menu that says
// so, a small non-modal dialog. Not one that stays around while you work (the floating task window, a toast, the tip),
// and not a room opened inside a view (the library), whose shared top bar must stay reachable.
export function transient(layer) {
    if (layer.matches('.fv-tip,[data-fv-persistent],[role=tooltip],[role=status],[role=log]')) return false;
    if (layer.matches('dialog')) return isModal(layer) || !isViewDialog(layer);
    if (layer.matches('[popover]')) {
        const kind = layer.getAttribute('popover');
        return kind === '' || kind === 'auto' || layer.matches('[role=dialog],[role=alertdialog],[role=menu],[role=listbox]');
    }
    return true;
}

// The innermost layer holding the keyboard around `element`, or null.
function layerOf(element) {
    for (let layer = element?.closest?.(LAYER); layer; layer = layer.parentElement?.closest(LAYER)) if (transient(layer)) return layer;
    return null;
}

// The layer in front that Tab should stay inside: the innermost holding layer around focus; otherwise the top modal dialog.
export function frontLayer(active = doc.activeElement) {
    const modal = [...doc.querySelectorAll('dialog[open]')].filter(isModal).at(-1) || null;
    if (active && active !== doc.body) {
        const layer = layerOf(active);
        // A layer inside the modal (or with no modal at all) is the trap; one under the modal cannot hold focus anyway.
        if (layer && (!modal || modal.contains(layer) || layer === modal)) return layer;
    }
    return modal;
}

// Seen at all: some size, inside the window's reach, and not faded out by itself or a holder.
function seen(element) {
    const rect = element.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) < 0.05) return false;
        if (node.matches('dialog,[popover]')) break;
    }
    return true;
}

let lastTab = null, ringHost = null;
// A checkbox or switch that is itself invisible (a styled track drawn by its label) hands its ring to the label.
function hostRing(element) {
    ringHost?.removeAttribute('data-fv-ring-host'); ringHost = null;
    if (!(element instanceof Element) || !element.matches('input:is([type=checkbox],[type=radio]):focus-visible')) return;
    const label = element.closest('label') || (element.id && doc.querySelector(`label[for="${CSS.escape(element.id)}"]`));
    const box = element.getBoundingClientRect();
    if (!label || (box.width >= 4 && box.height >= 4 && seen(element))) return;   // a 1 px visually-hidden box counts as unseen
    label.setAttribute('data-fv-ring-host', ''); ringHost = label;
}
// A video or sound with built-in controls is focused inside the browser's own controls, so it never matches
// :focus-visible itself; reached by Tab, it is marked and a11y.css gives it the ring.
function markMedia(event) {
    const target = event.target;
    if (!(target instanceof Element) || !target.matches('video,audio')) return;
    if (lastTab && Date.now() - lastTab.at < 300) target.dataset.fvKbd = ''; else delete target.dataset.fvKbd;
}
// A Tab that landed on a control nobody can see moves on to the next one that can be seen, same direction.
function skipUnseen(event) {
    if (!lastTab || Date.now() - lastTab.at > 200) return;
    const target = event.target;
    if (!(target instanceof Element) || seen(target)) return;
    const layer = frontLayer(target) || doc.body;
    const stops = tabbables(layer).filter(seen);
    if (!stops.length) return;
    const after = stops.filter(stop => target.compareDocumentPosition(stop) & Node.DOCUMENT_POSITION_FOLLOWING);
    const before = stops.filter(stop => target.compareDocumentPosition(stop) & Node.DOCUMENT_POSITION_PRECEDING);
    const next = lastTab.back ? (before.at(-1) || stops.at(-1)) : (after[0] || stops[0]);
    if (next && next !== target) next.focus();
}

function onTab(event) {
    if (event.key === 'Tab') lastTab = {at: Date.now(), back: event.shiftKey};
    if (event.key !== 'Tab' || event.altKey || event.ctrlKey || event.metaKey || event.defaultPrevented) return;
    const active = doc.activeElement;
    const layer = frontLayer(active);
    if (!layer) return;
    // A container that moves focus on Tab by itself says so with data-fv-own-tab. Its handler runs after this
    // window-level trap, so the trap leaves the press to it (one move per press, not two), unless a layer opened
    // inside it is in front.
    const owner = active?.closest?.('[data-fv-own-tab]');
    if (owner && layer.contains(owner)) return;
    // Only FreeVideo's own layers; ComfyUI's dialogs keep their behaviour.
    if (!(layer.className && /(^|\s)(fv-|sgf)/.test(String(layer.className))) && !layer.closest('[class*="fv-"]:not(html)')) return;
    const stops = tabbables(layer);
    if (!stops.length) return;
    const first = stops[0], last = stops.at(-1);
    const inside = active && layer.contains(active) && active !== layer;
    if (!inside) { event.preventDefault(); (event.shiftKey ? last : first).focus(); return; }
    if (!event.shiftKey && (active === last || !stops.includes(active) && active.compareDocumentPosition(last) & Node.DOCUMENT_POSITION_PRECEDING)) { event.preventDefault(); first.focus(); }
    else if (event.shiftKey && (active === first || !stops.includes(active) && active.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING)) { event.preventDefault(); last.focus(); }
}

// The control that opened a non-modal layer, if it says so (aria-controls, or aria-expanded next to it).
function triggerOf(layer) {
    if (layer.id) { const named = doc.querySelector(`[aria-controls="${CSS.escape(layer.id)}"][aria-expanded=true]`); if (named) return named; }
    const host = layer.parentElement;
    return host?.querySelector(':scope > [aria-expanded=true],:scope > button[aria-haspopup]') || null;
}

function closeLayer(layer) {
    if (layer.matches('[popover]:popover-open')) { try { layer.hidePopover(); return true; } catch { /* fall through */ } }
    if (layer.matches('dialog[open]')) {
        if (layer.requestClose) layer.requestClose(); else layer.close();
        return true;
    }
    const trigger = triggerOf(layer);
    if (trigger) { trigger.click(); return true; }
    const close = layer.querySelector(':scope [data-fv-close],:scope [class*="-close"]:is(button,[role=button])');
    if (close) { close.click(); return true; }
    return false;
}

function onEscape(event) {
    if (event.key !== 'Escape' || event.isComposing) return;
    const active = doc.activeElement;
    const layer = layerOf(active);
    if (!layer || isViewDialog(layer) || isModal(layer)) return;   // modal: the browser's cancel and the dialog's own handlers
    // After everyone else had the key: only when nobody took it and the layer is still open.
    setTimeout(() => {
        if (event.defaultPrevented || !layer.isConnected || !shown(layer)) return;
        if (layer.matches('[popover]') && !layer.matches(':popover-open')) return;
        const trigger = triggerOf(layer);
        if (closeLayer(layer) && trigger?.isConnected) trigger.focus({preventScroll: true});
    }, 0);
}

// Ctrl/Cmd+A outside a typing field: nothing (or only the copyable text that holds focus), never the whole window.
function onSelectAll(event) {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || String(event.key).toLowerCase() !== 'a') return;
    const target = event.target instanceof Element ? event.target : doc.activeElement;
    if (!target || target.closest(TYPING)) return;
    if (!doc.documentElement.classList.contains('fv-app') && !target.closest('[class*="fv-"]:not(html),[data-fv-tip-scope]')) return;
    const copyable = target.closest('.fv-copyable');
    // Decide after the event has been everywhere: a view that selects its own things calls preventDefault.
    setTimeout(() => {
        if (event.defaultPrevented) return;
        const selection = globalThis.getSelection?.();
        if (!selection) return;
        if (copyable) { const range = doc.createRange(); range.selectNodeContents(copyable); selection.removeAllRanges(); selection.addRange(range); }
        else selection.removeAllRanges();
    }, 0);
}

// A ring that an overflow-clipping ancestor would cut off is drawn inside the control instead (a11y.css).
function fitRing(element) {
    if (!(element instanceof Element) || !element.matches(':focus-visible')) return;
    const rect = element.getBoundingClientRect(), pad = 5;
    let clipped = false;
    for (let node = element.parentElement; node && node !== doc.body; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (style.overflowX === 'visible' && style.overflowY === 'visible' && style.clipPath === 'none' && style.contain.indexOf('paint') < 0) continue;
        const box = node.getBoundingClientRect();
        if (rect.left - pad < box.left || rect.right + pad > box.right || rect.top - pad < box.top || rect.bottom + pad > box.bottom) { clipped = true; break; }
        if (node.matches('dialog,[popover]')) break;
    }
    if (clipped) element.dataset.fvRing = 'inset'; else delete element.dataset.fvRing;
}

let installed = false;
export function installA11y() {
    if (installed || globalThis.__fvA11y || !doc?.addEventListener) return;
    installed = globalThis.__fvA11y = true;
    sheet('a11y'); sheet('selection'); sheet('slider');
    // Window capture, registered at start: dialog_keys.js stops keys there for the views that route their own.
    globalThis.addEventListener('keydown', event => { onTab(event); onEscape(event); onSelectAll(event); }, true);
    doc.addEventListener('focusin', event => { skipUnseen(event); fitRing(doc.activeElement); markMedia(event); hostRing(doc.activeElement); }, true);
    doc.addEventListener('focusout', event => { if (event.target instanceof Element) delete event.target.dataset.fvKbd; ringHost?.removeAttribute('data-fv-ring-host'); ringHost = null; }, true);
}

// Loaded on its own (the shell's entry imports it), it installs itself; tooltip.js also calls installA11y().
if (doc?.addEventListener) installA11y();
