// The settings room's parts: icons, rows and cards, and one family of self-drawn controls (switch, segmented,
// radio list, stepper, check box, fold). Each control keeps the native element it replaces as the source of truth
// (`checked`, `value`, `disabled`, `onchange` behave as before), so the installation logic in setup.js, compatibility.js
// and the assistant settings runs unchanged; the drawn face repaints whenever the native value changes, also when code
// sets it. Styles: glass/settings.css (tokens from tokens.css).
import { listenDialogKey } from './dialog_keys.js';

export const ICONS = {
    cube: '<path d="M12 3.5l7.5 4.2v8.6L12 20.5l-7.5-4.2V7.7z"/><path d="M4.8 7.9L12 12l7.2-4.1M12 12v8.3"/>',
    down: '<path d="M12 4.5v10M7.5 10.5L12 15l4.5-4.5M5 19.5h14"/>',
    img: '<rect x="4" y="4.5" width="16" height="15" rx="3"/><circle cx="9" cy="9.5" r="1.5"/><path d="M4.5 16l4.5-4 3.5 3 2.5-2 4.5 4"/>',
    layers: '<path d="M12 4l8.5 4.5L12 13 3.5 8.5z"/><path d="M3.5 12.5L12 17l8.5-4.5M3.5 16.5L12 21l8.5-4.5"/>',
    film: '<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="M7.5 5v14M16.5 5v14M3.5 9.5h4M3.5 14.5h4M16.5 9.5h4M16.5 14.5h4"/>',
    palette: '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.2 0 1.8-.9 1.8-1.8 0-1.3-1-1.6-1-2.7 0-1 .8-1.6 1.8-1.6h2a3.9 3.9 0 0 0 3.9-3.9C20.5 6.6 16.7 3.5 12 3.5z"/><circle cx="7.8" cy="11.5" r="1.1"/><circle cx="10.5" cy="7.6" r="1.1"/><circle cx="15" cy="7.8" r="1.1"/>',
    auto: '<path d="M12 3.5l1.9 4.6 4.6 1.9-4.6 1.9L12 16.5l-1.9-4.6L5.5 10l4.6-1.9z"/><path d="M18.5 15l.8 1.7 1.7.8-1.7.8-.8 1.7-.8-1.7-1.7-.8 1.7-.8z"/>',
    disk: '<rect x="3.5" y="13" width="17" height="6.5" rx="2"/><path d="M5 13l2.3-7.2A2 2 0 0 1 9.2 4.5h5.6a2 2 0 0 1 1.9 1.3L19 13"/><path d="M16.5 16.3h.01"/>',
    shield: '<path d="M12 3.5l7 2.8v5.2c0 4.3-2.9 7.6-7 9-4.1-1.4-7-4.7-7-9V6.3z"/><path d="M8.8 12l2.2 2.2 4.2-4.4"/>',
    wrench: '<path d="M14.7 6.3a4 4 0 0 0 5 5L12 19a2.1 2.1 0 0 1-3-3z"/><path d="M14.7 6.3L17 4l3 3-2.3 2.3"/>',
    update: '<circle cx="12" cy="12" r="8.5"/><path d="M12 16.5v-9M8.5 11L12 7.5l3.5 3.5"/>',
    book: '<path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H19v15H7.5A2.5 2.5 0 0 0 5 20.5z"/><path d="M5 20.5A2.5 2.5 0 0 1 7.5 18H19v3H7.5"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    x: '<path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    warn: '<path d="M12 4 21 19.5H3z"/><path d="M12 10v4M12 16.8v.01"/>',
    info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.01"/>',
    chev: '<path d="m6.5 9.5 5.5 5.5 5.5-5.5"/>',
    right: '<path d="m9.5 6.5 5.5 5.5-5.5 5.5"/>',
    retry: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>',
    copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2.5"/><path d="M15.5 8.5v-2a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2"/>',
    folder: '<path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    person: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 19.5c1.2-3.4 4-5 7-5s5.8 1.6 7 5"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    key: '<circle cx="8" cy="15.5" r="3.5"/><path d="M10.5 13l8-8M15.5 8l2.5 2.5M17.5 6l2 2"/>',
    gauge: '<path d="M4.5 16.5a7.5 7.5 0 1 1 15 0"/><path d="M12 16.5l3.5-5"/>',
    plus: '<path d="M12 5.5v13M5.5 12h13"/>',
    minus: '<path d="M5.5 12h13"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.3 3.5 5.2 3.5 8.5s-1.1 6.2-3.5 8.5c-2.4-2.3-3.5-5.2-3.5-8.5s1.1-6.2 3.5-8.5z"/>',
    moon: '<path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z"/>',
    gpu: '<rect x="3.5" y="6.5" width="17" height="11" rx="2"/><circle cx="9" cy="12" r="2.5"/><path d="M14 10.5h3.5M14 13.5h3.5M7 17.5v2M17 17.5v2"/>',
    report: '<path d="M7 3.5h7l4.5 4.5v12.5H7z"/><path d="M14 3.5V8h4.5M9.5 12.5h6M9.5 16h6"/>',
};
const SVG = 'http://www.w3.org/2000/svg';
export function icon(name, cls = 'fv-i') {
    const svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('class', cls); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    svg.innerHTML = ICONS[name] || '';
    return svg;
}
export const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
};
// Hover text for tooltip.js: name, one sentence of use, key, and (for a greyed control) why.
export function tip(node, name, use, {key = '', why = ''} = {}) {
    if (!node) return node;
    node.dataset.fvTipName = name; node.dataset.fvTipUse = use;
    if (key) node.dataset.fvTipKey = key; else delete node.dataset.fvTipKey;
    if (why) node.dataset.fvTipWhy = why; else delete node.dataset.fvTipWhy;
    node.removeAttribute('title');
    return node;
}
export function button(text, {cls = '', iconName = '', kind = ''} = {}) {
    const node = el('button', `st-btn${kind ? ' is-' + kind : ''}${cls ? ' ' + cls : ''}`); node.type = 'button';
    if (iconName) node.append(icon(iconName, 'fv-i s'));
    if (text) node.append(el('span', 'st-btn-t', text));
    return node;
}
// The label text of a button made by button(): code that writes `textContent` on a native button keeps its icon.
export function setText(node, text) {
    const label = node.querySelector(':scope > .st-btn-t');
    if (label) label.textContent = text; else node.textContent = text;
}

// Repaints after code sets a property of a native element (value, checked, disabled), which fires no event.
export function hookProp(node, prop, after) {
    let proto = Object.getPrototypeOf(node), found = null;
    while (proto && !(found = Object.getOwnPropertyDescriptor(proto, prop))) proto = Object.getPrototypeOf(proto);
    if (!found?.set) return;
    Object.defineProperty(node, prop, {configurable: true, enumerable: true,
        get() { return found.get.call(this); },
        set(next) { found.set.call(this, next); after(); }});
}

// ── Rows and cards ──
// A row: optional icon tile, a title with a line under it, and whatever sits at its end.
export function row({iconName = '', title = '', sub = '', end = [], cls = ''} = {}) {
    const node = el('div', `st-row${iconName ? ' has-ic' : ''}${cls ? ' ' + cls : ''}`);
    if (iconName) { const tile = el('span', 'st-ic'); tile.append(icon(iconName, 'fv-i s')); node.append(tile); }
    const text = el('div', 'st-rt'), titleEl = el('b', '', title), subEl = el('span', '', sub);
    subEl.hidden = !sub; text.append(titleEl, subEl);
    const endEl = el('div', 'st-re'); endEl.append(...end);
    node.append(text, endEl);
    return {element: node, title: titleEl, sub: subEl, end: endEl};
}
export function card(...children) { const node = el('div', 'st-card'); node.append(...children.filter(Boolean)); return node; }
export function groupHead(text, right = null) {
    const node = el('h3', 'st-gh'); node.append(el('span', '', text));
    if (right) { right.classList.add('r'); node.append(right); }
    return node;
}
export function groupNote(text, iconName = 'info') {
    const node = el('p', 'st-gn'); if (iconName) node.append(icon(iconName, 'fv-i'));
    node.append(document.createTextNode(text)); return node;
}
// A status line that is always there (its text changes, its height does not).
export function slot(cls = '') {
    const node = el('p', `st-slot${cls ? ' ' + cls : ''}`); node.setAttribute('role', 'status'); node.setAttribute('aria-live', 'polite');
    return node;
}
// A fold: a card-sized summary row (icon, title, line, chevron) over a body.
export function fold({iconName = '', title = '', sub = '', body = [], cls = ''} = {}) {
    const details = el('details', `st-card st-fold${iconName ? '' : ' plain'}${cls ? ' ' + cls : ''}`);
    const summary = el('summary');
    if (iconName) { const tile = el('span', 'st-ic'); tile.append(icon(iconName, 'fv-i s')); summary.append(tile); }
    const text = el('div', 'st-rt'), titleEl = el('b', '', title), subEl = el('span', '', sub); subEl.hidden = !sub;
    text.append(titleEl, subEl);
    const end = el('div', 'st-re');
    summary.append(text, end, icon('chev', 'fv-i chev'));
    const inner = el('div', 'body'); inner.append(...body.filter(Boolean));
    details.append(summary, inner);
    return {element: details, summary, title: titleEl, sub: subEl, end, body: inner};
}

// ── Controls ──
// Switch: the native check box drawn as a switch (glass/settings.css), still a real input for the code and forms.
export function switchify(input) {
    input.setAttribute('role', 'switch'); input.classList.add('st-sw');
    return input;
}
// Agreement check box: the native box drawn square with a tick.
export function checkify(input) { input.classList.add('st-cb'); return input; }

// Segmented control over a native select: one button per option, the ink slides under the chosen one.
export function segmented(select, {label = '', tips = {}, cls = ''} = {}) {
    const group = el('div', `st-seg${cls ? ' ' + cls : ''}`); group.setAttribute('role', 'radiogroup');
    if (label) group.setAttribute('aria-label', label);
    const ink = el('span', 'ink'); ink.setAttribute('aria-hidden', 'true'); group.append(ink);
    select.hidden = true; select.tabIndex = -1; select.setAttribute('aria-hidden', 'true');
    const buttons = [...select.options].map(option => {
        const choice = el('button'); choice.type = 'button'; choice.setAttribute('role', 'radio');
        choice.dataset.value = option.value;
        const word = el('span', 'w', option.textContent); word.dataset.t = option.textContent; choice.append(word);
        if (tips[option.value]) tip(choice, option.textContent, tips[option.value]);
        choice.onclick = () => choose(option.value);
        group.append(choice);
        return choice;
    });
    function choose(value) {
        if (select.disabled || select.value === value) return;
        select.value = value;
        select.dispatchEvent(new Event('input', {bubbles: true})); select.dispatchEvent(new Event('change', {bubbles: true}));
    }
    function place() {
        const on = buttons.find(b => b.getAttribute('aria-checked') === 'true');
        if (!on || !group.isConnected || !on.offsetWidth) return;
        group.style.setProperty('--l', `${on.offsetLeft - 2}px`);
        group.style.setProperty('--r', `${on.offsetLeft - 2 + on.offsetWidth}px`);
    }
    function paint() {
        for (const choice of buttons) {
            const on = choice.dataset.value === select.value;
            choice.setAttribute('aria-checked', String(on)); choice.tabIndex = on ? 0 : -1;
            choice.disabled = select.disabled;
        }
        group.setAttribute('aria-disabled', String(select.disabled));
        place();
    }
    listenDialogKey(group, 'keydown', event => {
        const step = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[event.key];
        if (!step) return;
        event.preventDefault();
        const index = buttons.findIndex(b => b.dataset.value === select.value);
        const next = buttons[(index + step + buttons.length) % buttons.length];
        choose(next.dataset.value); next.focus();
    });
    hookProp(select, 'value', paint); hookProp(select, 'disabled', paint);
    select.addEventListener('change', paint);
    new ResizeObserver(place).observe(group);
    group.append(select); paint();
    group.fvPaint = paint;
    return group;
}

// A list of choices over a native select, one row each with a radio mark, a line under the name, and an end part.
// describe(value) → {sub, end} repaints with the select.
export function radioList(select, {label = '', describe = () => ({})} = {}) {
    const list = el('div', 'st-card st-radios'); list.setAttribute('role', 'radiogroup');
    if (label) list.setAttribute('aria-label', label);
    select.hidden = true; select.tabIndex = -1; select.setAttribute('aria-hidden', 'true');
    const rows = [...select.options].map(option => {
        const choice = el('button', 'st-src'); choice.type = 'button'; choice.setAttribute('role', 'radio'); choice.dataset.value = option.value;
        const mark = el('span', 'st-ra'); mark.setAttribute('aria-hidden', 'true');
        const text = el('span', 'st-rt'), name = el('b', '', option.textContent), sub = el('span', '');
        text.append(name, sub);
        const end = el('span', 'sp');
        choice.append(mark, text, end);
        choice.onclick = () => choose(option.value);
        list.append(choice);
        return {choice, sub, end, value: option.value};
    });
    function choose(value) {
        if (select.disabled || select.value === value) return;
        select.value = value;
        select.dispatchEvent(new Event('input', {bubbles: true})); select.dispatchEvent(new Event('change', {bubbles: true}));
    }
    function paint() {
        for (const item of rows) {
            const on = item.value === select.value;
            item.choice.setAttribute('aria-checked', String(on)); item.choice.tabIndex = on ? 0 : -1;
            item.choice.disabled = select.disabled;
            const {sub = '', end = null} = describe(item.value) || {};
            item.sub.textContent = sub; item.sub.hidden = !sub;
            tip(item.choice, item.choice.querySelector('b').textContent, sub || item.choice.querySelector('b').textContent);
            if (end) item.end.replaceChildren(...[].concat(end)); else item.end.replaceChildren();
        }
    }
    listenDialogKey(list, 'keydown', event => {
        const step = {ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1}[event.key];
        if (!step) return;
        event.preventDefault();
        const index = rows.findIndex(r => r.value === select.value);
        const next = rows[(index + step + rows.length) % rows.length];
        choose(next.value); next.choice.focus();
    });
    hookProp(select, 'value', paint); hookProp(select, 'disabled', paint);
    select.addEventListener('change', paint);
    list.append(select); paint();
    list.fvPaint = paint;
    return list;
}

// Stepper: − [number] unit +, around a native number input (min, max, step and validity stay the input's own).
export function stepper(input, unit = '', {label = ''} = {}) {
    const box = el('div', 'st-step');
    const less = el('button'); less.type = 'button'; less.tabIndex = -1; less.append(icon('minus', 'fv-i'));
    const more = el('button'); more.type = 'button'; more.tabIndex = -1; more.append(icon('plus', 'fv-i'));
    less.setAttribute('aria-label', label ? `${label} −` : '−'); more.setAttribute('aria-label', label ? `${label} +` : '+');
    input.classList.add('st-num'); input.inputMode = 'decimal';
    if (label) input.setAttribute('aria-label', label);
    const u = el('span', 'u', unit); u.hidden = !unit;
    const nudge = direction => {
        if (input.disabled) return;
        try { direction > 0 ? input.stepUp() : input.stepDown(); }
        catch { input.value = String((Number(input.value) || 0) + direction * (Number(input.step) || 1)); }
        input.dispatchEvent(new Event('input', {bubbles: true})); input.dispatchEvent(new Event('change', {bubbles: true}));
    };
    less.onclick = () => nudge(-1); more.onclick = () => nudge(1);
    const paint = () => { box.setAttribute('aria-disabled', String(input.disabled)); less.disabled = more.disabled = input.disabled; };
    hookProp(input, 'disabled', paint);
    box.append(less, input, u, more); paint();
    return box;
}

// A field: text input or textarea in the shared field face.
export function field(control, {mono = false} = {}) {
    const box = el('div', `st-field${mono ? ' mono' : ''}${control.tagName === 'TEXTAREA' ? ' is-area' : ''}`);
    box.append(control);
    return box;
}

// Progress bar: transform only.
export function bar(cls = '') {
    const node = el('div', `st-bar${cls ? ' ' + cls : ''}`), fill = el('i'); node.append(fill);
    node.setAttribute('role', 'progressbar'); node.setAttribute('aria-valuemin', '0'); node.setAttribute('aria-valuemax', '100');
    node.set = fraction => {
        if (fraction == null) { node.classList.add('is-ind'); node.removeAttribute('aria-valuenow'); return; }
        node.classList.remove('is-ind');
        const value = Math.max(0, Math.min(1, fraction));
        node.style.setProperty('--p', String(value)); node.setAttribute('aria-valuenow', String(Math.round(value * 100)));
    };
    return node;
}
