// Hover and focus help for the editor's controls: a name, one line on what it does, its key, and when the control is
// greyed the reason and how to clear it. The page-wide tooltip (tooltip.js) reads the data-fv-tip-* attributes; the
// native title is dropped so two tips never show at once.
export const modKey = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/i.test(navigator.userAgentData?.platform || navigator.platform || '') ? '⌘' : 'Ctrl+';

export function tip(element, {name = '', use = '', key = '', why = ''} = {}) {
    if (!element) return element;
    const set = (attribute, value) => { if (value) element.setAttribute(attribute, value); else element.removeAttribute(attribute); };
    set('data-fv-tip-name', name); set('data-fv-tip-use', use); set('data-fv-tip-key', key); set('data-fv-tip-why', why);
    element.removeAttribute('title');
    return element;
}

// The words a tooltip shows, for tests and for the copy table.
export function tipWords(element) {
    return {name: element.getAttribute('data-fv-tip-name') || '', use: element.getAttribute('data-fv-tip-use') || '',
        key: element.getAttribute('data-fv-tip-key') || '', why: element.getAttribute('data-fv-tip-why') || ''};
}
