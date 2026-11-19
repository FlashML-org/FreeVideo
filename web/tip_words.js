// Hover and focus help words on any element: the page-wide tooltip (tooltip.js) reads the data-fv-tip-* attributes this
// writes, and the browser's own title is dropped so two tips never show at once. No DOM work happens at import time.
// Kept apart from the editor's tip catalogue (editor_tracks_tips.js) so the pages loaded first need none of the editor.
export function setTip(element, {name = '', use = '', key = '', why = ''} = {}) {
    if (!element) return element;
    // Writes only what changed: the transport refreshes on every playback frame.
    // (Plain lookups only, so the small test DOM is enough.)
    const read = attribute => element.getAttribute?.(attribute) ?? null;
    const set = (attribute, value) => {
        if (value) { if (read(attribute) !== value) element.setAttribute(attribute, value); }
        else if (read(attribute) != null) element.removeAttribute?.(attribute);
    };
    set('data-fv-tip-name', name); set('data-fv-tip-use', use); set('data-fv-tip-key', key); set('data-fv-tip-why', why);
    // An empty title, never a stale one: the browser's own tip must not show next to this one.
    if (element.title !== '') element.title = '';
    return element;
}
