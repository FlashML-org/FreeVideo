// The glass skin of versions, what's new, feedback, failures, the tutorial, feature cards, the character editor and
// phone access (glass/library.css). Its rules are written one step more specific than the modules' own (:root:root …),
// so it wins whatever order the sheets load in. The sheet is added lazily, the first time one of those surfaces is
// built (skin()); nothing touches the document when the module is only imported (Node tests with a fake DOM).
let link = null;
export function skin() {
    if (link || typeof document === 'undefined' || typeof document.createElement !== 'function' || !document.head) return;
    try {
        const node = document.createElement('link');
        node.setAttribute('rel', 'stylesheet'); node.setAttribute('data-fv-glass-library', '');
        node.setAttribute('href', new URL('./library.css', import.meta.url).href);
        document.head.append(node);
        link = node;
    } catch { /* A page without a full document keeps the modules' own styles. */ }
}
// In a real page the sheet is wanted from the start (Settings and the failure cards can open at any time).
if (typeof window !== 'undefined' && typeof HTMLElement === 'function' && window.document === globalThis.document) queueMicrotask(skin);
