// Writes that leave a value as it was still make the page restyle around the element, and rules such as :has() carry
// that to the whole view. Refreshes that run on every playhead move write through these, so only real changes reach the page.
export function setHidden(node, value) { if (node && node.hidden !== !!value) node.hidden = !!value; }
export function setDisabled(node, value) { if (node && node.disabled !== !!value) node.disabled = !!value; }
// A lone text child is changed in place: a replaced child counts as an insertion, which :has() rules also follow.
export function setText(node, text) {
    if (!node || node.textContent === text) return;
    const only = node.firstChild;
    if (only && only === node.lastChild && only.nodeType === 3 && text) only.data = text; else node.textContent = text;
}
// null or undefined removes the attribute. A node that cannot be read (a test stand-in) is always written.
export function setAttr(node, name, value) {
    if (!node) return;
    const current = typeof node.getAttribute === 'function' ? node.getAttribute(name) : undefined;
    if (value == null) { if (current !== null) node.removeAttribute(name); }
    else if (current !== String(value)) node.setAttribute(name, String(value));
}
export function setData(node, key, value) { if (node && node.dataset[key] !== String(value)) node.dataset[key] = String(value); }
