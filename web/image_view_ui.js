import { listenDialogKey } from './dialog_keys.js';
// Small pieces every part of the picture page builds with: elements, line icons, buttons, and the hover
// description (name, one line of purpose, shortcut) that the shell's tooltip reads from data-fv-tip-*.
const languageOverride = typeof location !== 'undefined' ? new URLSearchParams(location.search).get('freevideo_lang') : null;
export const cn = languageOverride === 'zh' || (languageOverride !== 'en' && typeof navigator !== 'undefined' && String(navigator.language || '').toLowerCase().startsWith('zh'));
export const t = (en, zh) => cn ? zh : en;

export const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

// One stroke weight and one 24-unit box for every icon on the page.
const PATHS = {
    model: '<path d="M12 3.5l7.5 4.2v8.6L12 20.5l-7.5-4.2V7.7z"/><path d="M12 12l7.5-4.3M12 12v8.5M12 12L4.5 7.7"/>',
    prompt: '<path d="M5 18.5V7a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H8.5z"/><path d="M8.5 9.5h7M8.5 12.5h4"/>',
    layout: '<rect x="3.5" y="5" width="17" height="14" rx="2" stroke-dasharray="3 2.4"/><rect x="7" y="8.5" width="6" height="5" rx="1"/>',
    refs: '<rect x="3.5" y="4.5" width="13" height="13" rx="3"/><path d="M3.8 14.5l3.6-3.4 3 2.7 2.2-2 3.2 3"/><path d="M19.5 14v6M16.5 17h6"/>',
    size: '<rect x="4" y="6" width="16" height="12" rx="2.5"/><path d="M8 10v4M16 10v4" opacity=".6"/>',
    sliders: '<path d="M4 7.5h9M17 7.5h3M4 16.5h3M11 16.5h9"/><circle cx="15" cy="7.5" r="2"/><circle cx="9" cy="16.5" r="2"/>',
    preset: '<rect x="4" y="4" width="16" height="5" rx="1.5"/><rect x="4" y="10.5" width="16" height="5" rx="1.5"/><path d="M4 18.5h10"/>',
    history: '<path d="M4 12a8 8 0 1 0 2.3-5.6"/><path d="M4 5v4h4"/><path d="M12 8v4.5l3 1.8"/>',
    chev: '<path d="M7 9.5l5 5 5-5"/>',
    left: '<path d="M14.5 6.5L9 12l5.5 5.5"/>',
    right: '<path d="M9.5 6.5L15 12l-5.5 5.5"/>',
    plus: '<path d="M12 5.5v13M5.5 12h13"/>',
    x: '<path d="M7 7l10 10M17 7L7 17"/>',
    check: '<path d="M6 12.5l4 4 8-9"/>',
    down: '<path d="M12 4.5v10M7.5 10.5L12 15l4.5-4.5M5 19.5h14"/>',
    cloud: '<path d="M7 18.5a4.5 4.5 0 0 1-.6-9 6 6 0 0 1 11.6 1.5 3.8 3.8 0 0 1-.5 7.5z"/><path d="M12 11v6M9.5 14.5L12 17l2.5-2.5"/>',
    warn: '<path d="M12 4.5l8.5 15h-17z"/><path d="M12 10.5v4"/><circle cx="12" cy="17" r=".6" fill="currentColor"/>',
    retry: '<path d="M19 12a7 7 0 1 1-2.1-5"/><path d="M17.5 3.5v3.8h-3.8"/>',
    pause: '<rect x="7" y="6" width="3.5" height="12" rx="1" fill="currentColor" stroke="none"/><rect x="13.5" y="6" width="3.5" height="12" rx="1" fill="currentColor" stroke="none"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>',
    gen: '<path d="M12 3c1.2 5.8 3.2 7.8 9 9-5.8 1.2-7.8 3.2-9 9-1.2-5.8-3.2-7.8-9-9 5.8-1.2 7.8-3.2 9-9z"/>',
    more: '<circle cx="6" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="18" cy="12" r="1.3" fill="currentColor" stroke="none"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="M15.5 15.5L20 20"/>',
    select: '<rect x="4" y="4" width="16" height="16" rx="5"/><path d="M8.5 12.2l2.4 2.3 4.6-4.8"/>',
    star: '<path d="M12 4.2l2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.6l-4.8 2.5.9-5.4-3.9-3.8 5.4-.8z"/>',
    sort: '<path d="M8 5v14M4.5 15.5L8 19l3.5-3.5M16 19V5M12.5 8.5L16 5l3.5 3.5"/>',
    trash: '<path d="M5 7h14M10 4h4M7 7l.8 12a1.5 1.5 0 0 0 1.5 1.4h5.4a1.5 1.5 0 0 0 1.5-1.4L17 7"/><path d="M10.5 11v5.5M13.5 11v5.5"/>',
    restore: '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.5 4.5v4h4"/><path d="M12 8.5v4l2.5 1.5"/>',
    compare: '<rect x="3.5" y="5" width="7.5" height="14" rx="2"/><rect x="13" y="5" width="7.5" height="14" rx="2"/>',
    split: '<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="M12 3v18"/><path d="M9.5 10.5L8 12l1.5 1.5M14.5 10.5L16 12l-1.5 1.5"/>',
    single: '<rect x="4.5" y="4.5" width="15" height="15" rx="3"/>',
    folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2.5"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>',
    edit: '<path d="M14 5.5l4.5 4.5L9 19.5H4.5V15z"/><path d="M12 7.5l4.5 4.5"/>',
    asref: '<rect x="3.5" y="4.5" width="17" height="15" rx="3"/><path d="M3.8 15.5l4.2-4 3.4 3 2.6-2.3 6.2 5.3"/><circle cx="15.5" cy="9" r="1.5"/>',
    lock: '<rect x="5.5" y="10.5" width="13" height="9.5" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
    unlock: '<rect x="5.5" y="10.5" width="13" height="9.5" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 6.8-1.2"/>',
    dice: '<rect x="4.5" y="4.5" width="15" height="15" rx="3.5"/><circle cx="9" cy="9" r="1.1" fill="currentColor" stroke="none"/><circle cx="15" cy="15" r="1.1" fill="currentColor" stroke="none"/><circle cx="15" cy="9" r="1.1" fill="currentColor" stroke="none"/><circle cx="9" cy="15" r="1.1" fill="currentColor" stroke="none"/>',
    doc: '<path d="M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10A.5.5 0 0 1 7 20z"/><path d="M14 3.5v4h4M9.5 12.5h5M9.5 16h5"/>',
    panelL: '<rect x="3.5" y="4.5" width="17" height="15" rx="3"/><path d="M9.5 4.5v15"/>',
    panelR: '<rect x="3.5" y="4.5" width="17" height="15" rx="3"/><path d="M14.5 4.5v15"/>',
    save: '<path d="M5 4.5h11l3.5 3.5v11a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1V5.5a1 1 0 0 1 1-1z"/><path d="M8 4.5v4.5h7V4.5M8 20v-6h8v6"/>',
    clock: '<circle cx="12" cy="12" r="7.5"/><path d="M12 8v4.3l2.8 1.7"/>',
    info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5"/><circle cx="12" cy="8" r=".6" fill="currentColor"/>',
    grid3: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/>',
    grid4: '<path d="M4.5 6h15M4.5 10h15M4.5 14h15M4.5 18h15"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6 6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/>',
    moon: '<path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z"/>',
};
export function icon(name, cls = '') {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('class', 'fv-iv-icon' + (cls ? ' ' + cls : ''));
    svg.innerHTML = PATHS[name] || ''; return svg;
}

// The hover description. The shell's tooltip reads the three data attributes; until it is there the title stands in.
// A disabled control says why and what to do in `use`.
export function tip(node, name, use = '', key = '') {
    node.dataset.fvTipName = name; node.dataset.fvTipUse = use || ''; if (key) node.dataset.fvTipKey = key; else delete node.dataset.fvTipKey;
    // The shell's tooltip reads the data attributes; the title stands in only where it is not loaded.
    if (!globalThis.FreeVideoShell) node.title = [name, use].filter(Boolean).join(' — ') + (key ? ` (${key})` : ''); else node.removeAttribute('title');
    if (!node.hasAttribute('aria-label') || node.dataset.fvTipLabel === 'auto') { node.setAttribute('aria-label', name); node.dataset.fvTipLabel = 'auto'; }
    return node;
}

// A button: optional icon, optional visible text, the hover description.
export function button(cls, {label = '', iconName = '', name = '', use = '', key = '', onClick = null} = {}) {
    const b = el('button', 'fv-iv-btn' + (cls ? ' ' + cls : '')); b.type = 'button';
    if (iconName) b.append(icon(iconName));
    if (label) b.append(el('span', 'fv-iv-btn-label', label));
    if (name) tip(b, name, use, key);
    if (onClick) b.addEventListener('click', onClick);
    return b;
}

export const mod = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '') ? '⌘' : 'Ctrl';

// Safe localStorage: private windows and blocked storage only lose the convenience.
export function readStore(key, fallback) {
    try { const raw = globalThis.localStorage?.getItem(key); return raw == null ? fallback : JSON.parse(raw); } catch { return fallback; }
}
export function writeStore(key, value) {
    try { globalThis.localStorage?.setItem(key, JSON.stringify(value)); } catch { /* only not kept */ }
}

// A collapsible group with a fixed header row; its open state is kept per name.
// fixed: a group that is always open (its head is a plain title, nothing to fold).
export function section(name, {title, iconName, open = true, meta = null, actions = [], fixed = false} = {}) {
    const key = 'freevideo.imageView.sections';
    const saved = readStore(key, {});
    const box = el('section', 'fv-iv-sec'); box.dataset.section = name; if (fixed) box.dataset.fixed = 'true';
    const head = el('div', 'fv-iv-sec-head');
    const toggle = el(fixed ? 'div' : 'button', 'fv-iv-sec-toggle'); if (!fixed) toggle.type = 'button';
    const metaHost = el('span', 'fv-iv-sec-meta'); if (meta) metaHost.append(meta);
    toggle.append(icon(iconName || 'sliders', 'fv-iv-sec-icon'), el('h3', '', title), metaHost);
    if (!fixed) toggle.append(icon('chev', 'fv-iv-sec-chev'));
    const tools = el('span', 'fv-iv-sec-tools'); tools.append(...actions);
    head.append(toggle, tools);
    const body = el('div', 'fv-iv-sec-body'), inner = el('div', 'fv-iv-sec-inner'); body.append(inner);
    box.append(head, body);
    // keep: false folds or opens for this screen only (the page fitting itself), the saved choice stays.
    const set = (value, keep = true) => {
        if (fixed) value = true;
        box.dataset.open = String(value); if (fixed) return;
        toggle.setAttribute('aria-expanded', String(value));
        tip(toggle, title, value ? t('Fold this group away', '收起这一组') : t('Show this group', '展开这一组'));
        if (keep) { const all = readStore(key, {}); all[name] = value; writeStore(key, all); delete box.dataset.fitted; }
    };
    if (fixed) box.dataset.open = 'true';
    else {
        toggle.addEventListener('click', () => { set(box.dataset.open !== 'true'); box.dispatchEvent(new CustomEvent('fv-iv-section', {bubbles: true})); });
        box.dataset.open = String(saved[name] ?? open); toggle.setAttribute('aria-expanded', box.dataset.open);
        tip(toggle, title, box.dataset.open === 'true' ? t('Fold this group away', '收起这一组') : t('Show this group', '展开这一组'));
    }
    return {element: box, body: inner, meta: metaHost, tools, open: (value, keep = true) => set(value, keep), isOpen: () => box.dataset.open === 'true', saved: () => readStore(key, {})[name] ?? open};
}

// A radio group the keyboard can walk: Tab reaches the chosen one, the arrow keys move and choose, Home and End jump.
export function rovingRadios(group) {
    listenDialogKey(group, 'keydown', event => {
        const keys = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1};
        if (!(event.key in keys) && event.key !== 'Home' && event.key !== 'End') return;
        const radios = [...group.querySelectorAll('[role=radio]')].filter(r => !r.disabled);
        const at = radios.indexOf(event.target.closest('[role=radio]')); if (at < 0 || !radios.length) return;
        event.preventDefault(); event.stopPropagation();
        const next = event.key === 'Home' ? radios[0] : event.key === 'End' ? radios.at(-1) : radios[(at + keys[event.key] + radios.length) % radios.length];
        next.click(); requestAnimationFrame(() => (group.querySelector('[role=radio][aria-checked=true]') || next).focus());
    });
    return group;
}

