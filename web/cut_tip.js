// Words that are already on screen get a tip only when the screen shows them cut short; the tip then carries the whole
// text. The page-wide tooltip (tooltip.js) shows it, so the browser's own title tip never appears next to it.
// The check runs when the pointer or focus arrives, so it follows the width the element has at that moment.
import { setTip } from './tip_words.js';

const cutOff = node => !!node && (node.scrollWidth > node.clientWidth + 1 || node.scrollHeight > node.clientHeight + 1);

export function cutTip(host, full, measure = host, {always = false} = {}) {
    if (!host) return host;
    host.__fvCutText = String(full ?? '');
    host.__fvCutMeasure = measure;
    host.__fvCutAlways = always;
    if (host.__fvCutBound) return refresh(host);
    host.__fvCutBound = true;
    listen();
    return refresh(host);
}

// One listener on the window, in the capture phase: it runs before the page-wide tooltip (a document capture
// listener) looks at the element, so the very first hover already finds the tip words.
let listening = false;
function listen() {
    if (listening || typeof window === 'undefined') return;
    listening = true;
    const check = event => {
        for (let node = event.target; node; node = node.parentNode) {
            if (node.__fvCutBound) refresh(node);
        }
    };
    window.addEventListener('pointerover', check, true);
    window.addEventListener('focusin', check, true);
}

// A tip not yet needed leaves the element without any title and without tip words.
function refresh(host) {
    const text = host.__fvCutText;
    const cut = text && (host.__fvCutAlways || cutOff(host.__fvCutMeasure || host));
    setTip(host, cut ? {name: text} : {});
    return host;
}
