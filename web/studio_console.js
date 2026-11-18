// The creative panel's console: the prompt, one row of chips and Generate, with the settings in trays that open
// above it, one at a time. It only moves the panel's existing controls into place; every control keeps its own
// element, handlers and state, so nothing the panel did before is lost.
import { lockMark } from './audio_original.js';
import { showTipFor } from './tooltip.js';
const SVG = {
    media: '<rect x="2" y="3" width="12" height="10" rx="2"/><circle cx="6" cy="6.8" r="1.2"/><path d="m2.5 11.5 3.2-3.2 2.3 2.3 1.8-1.8 3.7 3.7"/>',
    quality: '<path d="M2.5 12.5h2.2V9.5H2.5zM6.9 12.5h2.2V6.5H6.9zM11.3 12.5h2.2v-9h-2.2z"/>',
    lora: '<path d="m8 2.5 5.5 3L8 8.5l-5.5-3Z"/><path d="m2.5 8.2 5.5 3 5.5-3M2.5 10.9l5.5 3 5.5-3"/>',
    sliders: '<path d="M2.5 4.5h6M11.5 4.5h2M2.5 11.5h2M7.5 11.5h6"/><circle cx="10" cy="4.5" r="1.5"/><circle cx="6" cy="11.5" r="1.5"/>',
    minus: '<path d="M4 8h8"/>',
    plus: '<path d="M8 4v8M4 8h8"/>',
    close: '<path d="m4.5 4.5 7 7M11.5 4.5l-7 7"/>',
    chevron: '<path d="m4.5 6.5 3.5 3 3.5-3"/>',
};
export function glyph(name, size = 16) {
    const span = document.createElement('span'); span.className = 'fvs-glyph'; span.setAttribute('aria-hidden', 'true');
    span.innerHTML = `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${SVG[name] || ''}</svg>`;
    return span;
}
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

// Name, one line of purpose and the shortcut, for the shell's hover card; `title` stays until that card reads these.
export function tip(element, name, use = '', key = '') {
    element.dataset.fvTipName = name;
    if (use) element.dataset.fvTipUse = use; else delete element.dataset.fvTipUse;
    if (key) element.dataset.fvTipKey = key; else delete element.dataset.fvTipKey;
    element.title = [name, use].filter(Boolean).join(' · ') + (key ? ` (${key})` : '');
    return element;
}

// The prompt's tallest height: 170 px, 120 px in a narrow window so Generate stays on the first screen.
const promptMax = () => globalThis.matchMedia?.('(max-width:760px)').matches ? 120 : 170;

// trays: [{id, title, element}] where element is a <details> (its summary is hidden; open is the tray's state)
// or any element, which is then wrapped in one. chips: what each chip shows, read again on sync().
export function createConsole({t, body, controls, prompt, runbar, trays, chips, status = [], onTray = () => {}}) {
    const host = el('div', 'fvs-trays');
    const box = el('div', 'fvs-console');
    // The console's edge lights up whenever the prompt has the caret; focus that did not come from a press in the
    // console (Tab, or the panel putting the caret back) also rings it, so the keys always show where they are.
    let pressed = -Infinity;
    box.addEventListener('pointerdown', () => { pressed = performance.now(); }, true);
    box.addEventListener('focusin', () => box.toggleAttribute('data-pointer', performance.now() - pressed < 600));
    const main = el('div', 'fvs-console-prompt');
    const row = el('div', 'fvs-chips'); row.setAttribute('role', 'toolbar'); row.setAttribute('aria-label', t('Settings', '设置'));
    const slot = el('div', 'fvs-console-status'); slot.setAttribute('aria-live', 'polite');
    main.append(prompt);
    box.append(main, row, runbar, slot);
    for (const item of status) slot.append(item);
    const rows = new Map();
    for (const tray of trays) {
        let details = tray.element;
        if (details.tagName !== 'DETAILS') {
            const wrap = el('details', 'fv-section'); wrap.append(el('summary', 'fv-section-title', tray.title), details);
            details = wrap;
        }
        // An open tray is a tool region the floating windows keep out of, like the console under it (sgf-avoid).
        details.classList.add('fvs-tray', 'sgf-avoid'); details.dataset.tray = tray.id;
        const head = el('div', 'fvs-tray-head'), title = el('strong', 'fvs-tray-title', tray.title);
        const close = el('button', 'fvs-tray-close'); close.type = 'button'; close.append(glyph('close', 14));
        close.setAttribute('aria-label', t('Close', '关闭')); tip(close, t('Close', '关闭'), t('Hide this panel', '收起这个面板'), 'Esc');
        close.onclick = () => { details.open = false; rows.get(tray.id)?.chip.focus({preventScroll: true}); };
        head.append(title, close);
        details.querySelector(':scope>summary')?.after(head);
        host.append(details);
        rows.set(tray.id, {details, chip: null});
        details.addEventListener('toggle', () => {
            if (details.open) for (const [id, other] of rows) if (id !== tray.id && other.details.open) other.details.open = false;
            paint();
        });
    }
    controls.replaceChildren(host, box);
    // A chip opens its tray; the chip row keeps the panel's summary of each setting.
    const built = [];
    for (const spec of chips) {
        if (spec.element) { spec.element.classList.add('fvs-chip'); row.append(spec.element); built.push(spec); continue; }
        if (spec.spacer) { row.append(el('span', 'fvs-chip-spacer')); continue; }
        const chip = el('button', 'fvs-chip'); chip.type = 'button'; chip.dataset.chip = spec.id;
        if (spec.icon) chip.append(glyph(spec.icon));
        const label = el('span', 'fvs-chip-label'), extra = el('span', 'fvs-chip-extra');
        chip.append(label, extra);
        if (spec.tray) {
            chip.setAttribute('aria-expanded', 'false');
            chip.setAttribute('aria-controls', `fvs-tray-${spec.tray}`);
            rows.get(spec.tray).details.id = `fvs-tray-${spec.tray}`;
            rows.get(spec.tray).chip = chip;
            chip.onclick = () => toggle(spec.tray);
        } else if (spec.action) chip.onclick = spec.action;
        row.append(chip); built.push({...spec, chip, label, extra});
    }
    // Putting a tray away plays its opening in reverse (160 ms; at once with reduced motion). Opening a tray, or switching to
    // another one, is immediate and cancels a closing in progress, so a switch never closes and reopens.
    let closing = null;
    const calm = () => globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    function stopClosing() { if (!closing) return; clearTimeout(closing.timer); delete closing.details.dataset.closing; closing = null; }
    function putAway(item, animate) {
        if (!item?.details.open) return;
        if (!animate || calm()) { stopClosing(); item.details.open = false; return; }
        if (closing?.details === item.details) return;
        stopClosing();
        item.details.dataset.closing = 'true';
        closing = {details: item.details, timer: setTimeout(() => { const details = closing?.details; closing = null; if (details) { delete details.dataset.closing; details.open = false; } }, 160)};
    }
    function toggle(id, force) {
        const item = rows.get(id); if (!item) return;
        stopClosing();
        item.details.open = force ?? !item.details.open;
        if (item.details.open) item.details.scrollTop = 0;
    }
    function openId() { for (const [id, item] of rows) if (item.details.open) return id; return ''; }
    function paint() {
        const open = openId();
        body.dataset.tray = open;
        for (const [id, item] of rows) item.chip?.setAttribute('aria-expanded', String(id === open));
        onTray(open, open ? rows.get(open).details : null);
    }
    // The prompt grows with its text up to 170 px (120 px in a narrow window). Browsers that size a text box by its content (field-sizing) do it
    // in CSS; the others get the same height here, so the box is never a fixed strip with a scroll bar inside it.
    const sizesItself = Boolean(globalThis.CSS?.supports?.('field-sizing', 'content'));
    function fitPrompt() {
        if (sizesItself) return;
        const area = box.querySelector('.fv-prompt-editor textarea'); if (!area || !area.offsetParent) return;
        area.style.height = 'auto';
        const edge = area.offsetHeight - area.clientHeight;
        area.style.height = `${Math.min(area.scrollHeight + edge, promptMax())}px`;
    }
    box.addEventListener('input', event => { if (event.target?.matches?.('.fv-prompt-editor textarea')) fitPrompt(); });
    if (!sizesItself && globalThis.ResizeObserver) new ResizeObserver(() => fitPrompt()).observe(box);
    function sync() {
        fitPrompt();
        for (const spec of built) {
            if (!spec.read) continue;
            const value = spec.read() || {};
            if (spec.label && spec.label.textContent !== (value.label ?? '')) spec.label.textContent = value.label ?? '';
            if (spec.extra) { if (spec.extra.textContent !== (value.extra ?? '')) spec.extra.textContent = value.extra ?? ''; if (spec.extra.hidden !== !value.extra) spec.extra.hidden = !value.extra; }
            if (spec.chip) {
                if (spec.chip.disabled !== Boolean(value.disabled)) spec.chip.disabled = Boolean(value.disabled);
                if (value.badge != null) { if (spec.chip.dataset.badge !== String(value.badge)) spec.chip.dataset.badge = String(value.badge); } else if ('badge' in spec.chip.dataset) delete spec.chip.dataset.badge;
                if (value.tip) tip(spec.chip, value.tip[0], value.tip[1], value.tip[2]);
            }
        }
    }
    // Every tray starts closed: the picture keeps its full size until a setting is opened.
    for (const item of rows.values()) item.details.open = false;
    paint(); sync();
    return {
        element: box, trays: host, slot, sync, toggle, open: openId,
        chip: id => rows.get(id)?.chip || built.find(spec => spec.id === id)?.chip || null,
        trayOf: element => element?.closest?.('.fvs-tray')?.dataset.tray || '',
        // Escape closes an open tray before anything else.
        dismiss() { const id = openId(); if (!id) return false; const item = rows.get(id); item.details.open = false; item.chip?.focus({preventScroll: true}); return true; },
        // Closes whatever tray is open without moving focus (Generate pressed, a click elsewhere).
        close({animate = false} = {}) { const id = openId(); if (id) putAway(rows.get(id), animate); return Boolean(id); },
    };
}

// The duration chip: minus, the seconds, plus. It edits the panel's own seconds field and lets that field's handlers run.
// max: () => the longest length; + stops there (the field's own handlers bring a typed length back within it).
// What decides a locked duration: 'audio' (an audio clip used as is), 'linked' (a node is connected to it) or null.
export function durationStepper(t, seconds, {onOpen, max = () => Infinity, lockedBy = () => null}) {
    const element = el('div', 'fvs-stepper'); element.setAttribute('role', 'group'); element.setAttribute('aria-label', t('Duration', '时长'));
    const minus = el('button', 'fvs-step'); minus.type = 'button'; minus.append(glyph('minus', 14));
    const plus = el('button', 'fvs-step'); plus.type = 'button'; plus.append(glyph('plus', 14));
    const value = el('button', 'fvs-step-value'); value.type = 'button';
    minus.setAttribute('aria-label', t('One second shorter', '缩短 1 秒')); plus.setAttribute('aria-label', t('One second longer', '加长 1 秒'));
    tip(minus, t('Shorter', '缩短'), t('One second shorter', '时长减少 1 秒'));
    tip(plus, t('Longer', '加长'), t('One second longer', '时长增加 1 秒'));
    tip(value, t('Duration', '时长'), t('Click to type an exact length', '点一下输入精确时长'));
    element.append(minus, value, plus);
    const step = delta => {
        if (seconds.disabled) return;
        const now = Number(seconds.value) || 0;
        const next = Math.min(Math.max(2, Math.round(now) + delta), Math.max(2, max()));
        if (next === now) return;
        seconds.value = String(next);
        seconds.dispatchEvent(new Event('input', {bubbles: true}));
        seconds.dispatchEvent(new Event('change', {bubbles: true}));
    };
    minus.onclick = () => step(-1); plus.onclick = () => step(1);
    // The number is edited where it is: Enter or leaving the field applies it, Escape keeps the old one.
    // The number is typed in a field as wide as the number it replaces, with the unit kept after it, so nothing beside
    // the stepper moves.
    value.onclick = event => {
        // A length something else decides answers with the reason its hover gives, instead of doing nothing.
        if (seconds.disabled && reason()) { showTipFor(value); return; }
        if (seconds.disabled || element.querySelector('.fvs-step-input')) return;
        const edit = el('span', 'fvs-step-edit'); edit.style.width = `${value.offsetWidth}px`;
        const input = el('input', 'fvs-step-input'); input.type = 'text'; input.inputMode = 'decimal';
        // Opened from the keyboard (a click with no pointer): the field shows its focus line.
        if (!event?.detail) edit.dataset.fvKeyboard = 'true';
        edit.append(input, el('span', 'fvs-step-unit', t('s', '秒')));
        input.value = String(Number(seconds.value) || ''); input.setAttribute('aria-label', t('Duration in seconds', '时长（秒）'));
        let done = false;
        const finish = apply => {
            if (done) return; done = true;
            const typed = String(input.value).trim(), n = Number(typed.replace(',', '.'));
            // A cleared field leaves the duration empty (Generate then asks for it); the old length does not come back.
            if (apply && !typed && seconds.value !== '') {
                seconds.value = '';
                seconds.dispatchEvent(new Event('input', {bubbles: true})); seconds.dispatchEvent(new Event('change', {bubbles: true}));
            } else if (apply && Number.isFinite(n) && n > 0 && n !== Number(seconds.value)) {
                seconds.value = String(n);
                seconds.dispatchEvent(new Event('input', {bubbles: true})); seconds.dispatchEvent(new Event('change', {bubbles: true}));
            }
            edit.replaceWith(value); sync(); value.focus({preventScroll: true});
        };
        // (The view routes keys to its controls' on… handlers, so this one is a property.)
        input.onkeydown = event => {
            if (event.key === 'Enter') { event.preventDefault(); finish(true); }
            else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(false); }
        };
        input.addEventListener('blur', () => finish(true));
        value.replaceWith(edit); input.focus(); input.select();
    };
    void onOpen;
    let shownNow = null, lockedNow = null, freeUse = null;
    const reason = () => seconds.disabled ? lockedBy() : null;
    const LOCKED_USE = {
        audio: () => t('While the audio is set to "Use as is", the duration matches it.', '音频选“原样使用”时，时长与音频相同。'),
        linked: () => t('Set by connected nodes', '由连接的节点决定'),
    };
    function sync() {
        const n = seconds.value === '' ? NaN : Number(seconds.value);
        const shown = Number.isFinite(n) ? (Number.isInteger(n) ? String(n) : n.toFixed(n < 10 ? 2 : 1).replace(/0+$/, '').replace(/\.$/, '')) : '—';
        // The number and its unit apart, so a narrow row can show the number alone; the name says both.
        const locked = reason();
        if (shownNow !== shown || lockedNow !== locked || !value.firstChild) {
            // The hover words the page set for a free length come back when the lock goes.
            if (locked && !lockedNow) freeUse = value.dataset.fvTipUse ?? null;
            if (!locked && lockedNow) { if (freeUse) value.dataset.fvTipUse = freeUse; else delete value.dataset.fvTipUse; }
            if (locked) value.dataset.fvTipUse = LOCKED_USE[locked]?.() ?? '';
            shownNow = shown; lockedNow = locked;
            const unit = el('span', 'fvs-step-unit', t('s', '秒')), number = el('span', 'fvs-step-number', shown);
            // The audio decides the length: its lock, the same as beside the old duration field, before the number.
            // It sits inside the number (not a grid column of its own), so the button grows by the lock and 4 px only.
            if (locked === 'audio') { const mark = lockMark(); mark.hidden = false; mark.classList.add('fvs-step-lock'); number.classList.add('fvs-step-locked'); number.prepend(mark); }
            value.replaceChildren(number, unit);
            value.setAttribute('aria-label', locked === 'audio' ? t(`${shown} s, set by the audio`, `${shown} 秒，由音频决定`)
                : locked === 'linked' ? t(`${shown} s, set by connected nodes`, `${shown} 秒，由连接的节点决定`) : t(`${shown} s`, `${shown} 秒`));
        }
        // The page writes its own hover words for the length (how many parts it is made in) as it changes; while the
        // length is locked those are kept for later and the reason stays.
        if (locked) { const said = LOCKED_USE[locked]?.() ?? ''; if (value.dataset.fvTipUse !== said) { freeUse = value.dataset.fvTipUse ?? null; value.dataset.fvTipUse = said; } }
        const lessOff = seconds.disabled || !(n > 2), moreOff = seconds.disabled || n >= max();
        if (minus.disabled !== lessOff) minus.disabled = lessOff;
        if (plus.disabled !== moreOff) plus.disabled = moreOff;
    }
    seconds.addEventListener('input', sync); seconds.addEventListener('change', sync);
    // Locking and unlocking the field changes only its disabled state, which fires no event.
    if (globalThis.MutationObserver) new MutationObserver(sync).observe(seconds, {attributes: true, attributeFilter: ['disabled']});
    sync();
    return {element, sync};
}
