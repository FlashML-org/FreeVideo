import {editorIcon} from './editor_icons.js';
import {isText} from './editor_text_model.js';
import {listenDialogKey} from './dialog_keys.js';
// Shared adjustment shell. Tab owners keep their controls and editing policy here.
const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
};
const kinds = ['video', 'audio', 'speed', 'text', 'subtitle'];
let serial = 0;

// The existing editor action error and the adjustment controls share this copy.
export const busyReason = t => t('Cancel generation before changing this part.', '先取消生成，再改这一段。');
export function disabledReason(context, {needs, single = false} = {}) {
    const {t} = context;
    if (context.busy) return busyReason(t);
    if (!context.single && !context.multiple) return t('Select a part first', '先选中一段');
    if (single && context.multiple) return t('Only for a single part', '只能对单独一段调整');
    if (needs === 'video' && !context.hasVideo) return t('This part has no video', '这一段没有画面');
    if (needs === 'audio' && !context.hasAudio) return t('This part has no audio', '这一段没有声音');
    return '';
}

function selectedContext(state, t, actions) {
    const record = state.record, selection = state.selection;
    const all = record.tracks.flatMap(track => track.clips.map(clip => ({...clip, track})));
    const ids = new Set(selection?.kind === 'clip' ? selection.ids : []);
    // Accept either half or both halves, including selection from another task.
    for (const clip of all) if (ids.has(clip.id) || ids.has(clip.link)) {
        ids.add(clip.id); if (clip.link) ids.add(clip.link);
    }
    const clips = all.filter(clip => ids.has(clip.id));
    const seen = new Set(), parts = [];
    for (const clip of clips) {
        if ((!clip.source && !isText(clip)) || seen.has(clip.id)) continue;
        parts.push(clip); seen.add(clip.id); if (clip.link) seen.add(clip.link);
    }
    const busy = clips.some(clip => ['queued', 'running'].includes(clip.pending)) ||
        (record.replacements || []).some(tx => tx.originals.some(track => track.clips.some(clip => ids.has(clip.id))) &&
            tx.clips.some(track => track.clips.some(clip => ['queued', 'running'].includes(clip.pending))));
    return {clips, single: parts.length === 1, multiple: parts.length > 1,
        hasVideo: clips.some(clip => clip.source && clip.track.kind === 'video'),
        hasAudio: clips.some(clip => clip.source && clip.track.kind === 'audio'),
        busy, record, t, state, actions, count: parts.length};
}
export function createAdjustPanel(host, {state, t, actions}) {
    const shell = host.closest('.fv-editor'), uid = `fv-ed-adjust-${++serial}`;
    host.classList.add('fv-ed-adjust-host');
    const element = el('section', 'fv-ed-adjust'); element.id = uid; element.dataset.tourId = 'editor-inspector';
    element.setAttribute('aria-label', t('Adjust', '调整'));
    const tabs = el('div', 'fv-ed-tabs fv-pass-mode'); tabs.setAttribute('role', 'tablist');
    tabs.setAttribute('aria-label', t('Adjust', '调整'));
    const status = el('p', 'fv-ed-adjust-status'); status.setAttribute('role', 'status');
    const buttons = new Map(), panes = new Map(), entries = new Map();
    const toggleButton = button('', {onClick: () => overlay ? close({restoreFocus: true}) : open()});
    toggleButton.append(editorIcon('adjust')); toggleButton.setAttribute('aria-label', t('Adjust', '调整')); toggleButton.title = t('Adjust', '调整');
    toggleButton.classList.add('fv-ed-adjust-toggle'); toggleButton.dataset.tourToggle = 'editor-inspector'; toggleButton.setAttribute('aria-controls', uid);
    const labels = {video: t('Video', '画面'), audio: t('Audio', '声音'), speed: t('Speed', '速度'), text: t('Text', '文字'), subtitle: t('Subtitles', '字幕')};
    for (const kind of kinds) {
        const tab = button(labels[kind], {onClick: () => activate(kind)});
        tab.id = `${uid}-${kind}-tab`; tab.dataset.adjustTab = kind;
        tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', `${uid}-${kind}`);
        const pane = el('div', 'fv-ed-adjust-pane'); pane.id = `${uid}-${kind}`;
        pane.setAttribute('role', 'tabpanel'); pane.setAttribute('aria-labelledby', tab.id);
        const controls = el('fieldset', 'fv-ed-adjust-controls');
        pane.append(controls); panes.set(kind, {pane, controls});
        buttons.set(kind, tab); tabs.append(tab);
    }
    element.append(tabs, status, ...[...panes.values()].map(value => value.pane)); host.append(element);
    const media = window.matchMedia?.('(min-width: 1280px)');
    let narrow = media ? !media.matches : false, overlay = false, active = 'video', disposed = false;
    let context = selectedContext(state, t, actions);
    function renderActive() {
        if (host.hidden) return;
        const entry = entries.get(active);
        if (entry && !entry.rendered) {
            entry.rendered = true;
            entry.component.render(panes.get(active).controls, context);
            entry.component.update?.(context);
        }
    }
    function paint() {
        if (disposed) return;
        const textKind = context.clips.length && context.clips.every(isText) ? context.clips[0].text_kind : null;
        // Text and subtitle pages show alone for their own parts; the others only once registered.
        const available = (textKind ? [textKind] : kinds.slice(0, 3)).filter(kind => entries.has(kind));
        if (!available.includes(active)) active = available[0];
        if (!available.length) overlay = false;
        host.hidden = !available.length || (narrow && !overlay); toggleButton.hidden = !available.length || !narrow;
        toggleButton.setAttribute('aria-expanded', String(narrow && overlay));
        if (shell) { shell.dataset.adjustOpen = String(narrow && overlay); shell.dataset.adjustAvailable = String(!!available.length); }
        status.textContent = context.multiple ? t(`${context.count} parts selected`, `已选 ${context.count} 段`) : '';
        status.hidden = !context.multiple;
        for (const kind of kinds) {
            const tab = buttons.get(kind), {pane, controls} = panes.get(kind);
            tab.hidden = !available.includes(kind);
            tab.setAttribute('aria-selected', String(kind === active)); tab.tabIndex = kind === active ? 0 : -1;
            pane.hidden = kind !== active;
            const reason = textKind ? (context.clips.some(c => c.track.locked) ? 'locked' : '') : disabledReason(context, {needs: kind === 'speed' ? undefined : kind});
            controls.disabled = !!reason; controls.title = '';
        }
        renderActive();
    }
    function activate(kind) { if (!disposed && entries.has(kind)) { if (active !== kind) entries.get(active)?.component.deactivate?.(); active = kind; paint(); } }
    tabs.onkeydown = event => {
        const directions = {ArrowLeft: -1, ArrowRight: 1};
        if (!(event.key in directions) && event.key !== 'Home' && event.key !== 'End') return;
        const available = kinds.filter(kind => entries.has(kind));
        if (!available.length) return;
        event.preventDefault(); event.stopPropagation();
        const visible = kinds.filter(k => !buttons.get(k).hidden);
        const index = event.key === 'Home' ? 0 : event.key === 'End' ? visible.length - 1
            : (visible.indexOf(active) + directions[event.key] + visible.length) % visible.length;
        activate(visible[index]); buttons.get(active).focus();
    };
    function update() {
        if (disposed) return;
        context = selectedContext(state, t, actions);
        // Already rendered tabs stay current while hidden; unvisited tabs stay lazy.
        const rendered = [...entries.values()].filter(entry => entry.rendered);
        paint();
        for (const entry of rendered) entry.component.update?.(context);
    }
    function register(kind, component) {
        if (!kinds.includes(kind) || typeof component?.render !== 'function') throw new TypeError('Invalid adjustment tab');
        if (disposed) throw new Error('Adjustment panel is disposed');
        const previous = entries.get(kind); previous?.component.dispose?.();
        panes.get(kind).controls.replaceChildren();
        const entry = {component, rendered: false}; entries.set(kind, entry); paint();
        // Optional unregister is useful when a separately loaded module goes away.
        return () => {
            if (disposed || entries.get(kind) !== entry) return;
            const focused = buttons.get(kind) === document.activeElement || panes.get(kind).pane.contains(document.activeElement);
            entries.delete(kind); component.dispose?.(); panes.get(kind).controls.replaceChildren(); paint();
            if (focused && active && !host.hidden) buttons.get(active).focus();
        };
    }
    function open() {
        if (disposed || !entries.size) return;
        if (narrow) overlay = true;
        // Text and subtitle pages are always registered but only shown for their own parts.
        paint(); if (active) buttons.get(active).focus();
    }
    function close({restoreFocus = false} = {}) {
        if (disposed || !narrow || !overlay) return;
        overlay = false; paint(); if (restoreFocus) toggleButton.focus();
    }
    function dismiss(event) {
        if (!narrow || !overlay || disposed) return false;
        event.preventDefault(); event.stopPropagation(); close({restoreFocus: true}); return true;
    }
    const outside = event => {
        if (overlay && event.target.closest('.fv-ed-preview')) return;
        if (overlay && !host.contains(event.target) && !toggleButton.contains(event.target)) close();
    };
    const escape = event => { if (event.key === 'Escape') dismiss(event); };
    const resize = () => {
        const focused = host.contains(document.activeElement);
        narrow = !media.matches; overlay = false; paint();
        if (narrow && focused && entries.size) toggleButton.focus();
        else if (!narrow && document.activeElement === toggleButton) buttons.get(active)?.focus();
    };
    document.addEventListener('pointerdown', outside, true);
    const stopEscape = listenDialogKey(document, 'keydown', escape);
    media?.addEventListener('change', resize);
    const unsubscribe = state.subscribe(event => { if (event.type === 'record' || event.type === 'selection') update(); });
    paint();
    function dispose() {
        if (disposed) return;
        disposed = true; unsubscribe();
        document.removeEventListener('pointerdown', outside, true); stopEscape();
        media?.removeEventListener('change', resize);
        tabs.onkeydown = null; for (const tab of buttons.values()) tab.dispose(); toggleButton.dispose();
        for (const entry of entries.values()) entry.component.dispose?.(); entries.clear();
        if (shell) { delete shell.dataset.adjustOpen; delete shell.dataset.adjustAvailable; }
        element.remove(); toggleButton.remove(); host.hidden = true;
    }
    return {element, register, update, dispose, open, close, disabledReason, toggleButton, dismiss};
}

// Builders return DOM nodes; owners set .value/.disabled/.title during update.
export function row(label, ...controls) {
    const element = el('div', 'fv-ed-adjust-row'), text = el('span', 'fv-ed-adjust-label', label);
    const group = el('div', 'fv-ed-adjust-row-controls'); group.append(...controls); element.append(text, group);
    group.setAttribute('role', 'group'); group.setAttribute('aria-label', label);
    for (const control of controls) {
        const inputs = control.matches('input') ? [control] : control.querySelectorAll('input');
        for (const input of inputs) if (!input.getAttribute('aria-label')) input.setAttribute('aria-label', label);
    }
    return element;
}
function numeric(type, {min, max, step = 1, value = 0, format = String, onInput, onChange, title = '', label} = {}) {
    const element = el('span', `fv-ed-adjust-${type}`), input = el('input'), output = el('output');
    input.type = type === 'slider' ? 'range' : 'number'; input.step = String(step);
    if (min != null) input.min = String(min); if (max != null) input.max = String(max);
    element.title = title; if (label) input.setAttribute('aria-label', label);
    const refresh = () => { output.textContent = format(Number(input.value)); };
    let committed;
    Object.defineProperties(element, {
        value: {get: () => Number(input.value), set: value => { input.value = String(value); committed = Number(input.value); refresh(); }},
        disabled: {get: () => input.disabled, set: value => { input.disabled = !!value; }},
    });
    element.input = input; element.output = output; element.value = value;
    input.oninput = () => { refresh(); if (input.value !== '' && Number.isFinite(Number(input.value))) onInput?.(Number(input.value)); };
    input.onchange = () => {
        if (input.value === '' || !Number.isFinite(Number(input.value)) || input.validity?.valid === false) { input.value = String(committed); refresh(); return; }
        const next = Number(input.value); refresh();
        if (next !== committed) { committed = next; onChange?.(next); }
    };
    element.append(input); if (type === 'slider') element.append(output);
    element.dispose = () => { input.oninput = input.onchange = null; };
    return element;
}
export const slider = options => numeric('slider', options);
export const number = options => numeric('number', options);
export function button(label, {onClick, title = ''} = {}) {
    const element = el('button', 'fv-ed-adjust-button', label); element.type = 'button'; element.title = title;
    element.onclick = onClick; element.dispose = () => { element.onclick = null; }; return element;
}
export function segmented(options, {value, onChange} = {}) {
    const element = el('div', 'fv-pass-mode fv-ed-adjust-segmented'); element.setAttribute('role', 'group');
    let current = value;
    const buttons = options.map(option => button(option.label, {onClick: () => {
        if (current === option.value) return;
        element.value = option.value; onChange?.(option.value);
    }}));
    const paint = () => buttons.forEach((control, index) => control.setAttribute('aria-pressed', String(options[index].value === current)));
    Object.defineProperties(element, {
        value: {get: () => current, set: value => { current = value; paint(); }},
        disabled: {get: () => buttons.every(control => control.disabled), set: value => buttons.forEach(control => { control.disabled = !!value; })},
    });
    element.append(...buttons); paint(); element.dispose = () => buttons.forEach(control => control.dispose()); return element;
}
export function toggle(label, {value = false, onChange} = {}) {
    const element = el('label', 'fv-ed-adjust-toggle-control'), input = el('input'); input.type = 'checkbox'; input.checked = !!value;
    const text = el('span', '', label); element.append(input, text); input.setAttribute('aria-label', label);
    Object.defineProperties(element, {
        value: {get: () => input.checked, set: value => { input.checked = !!value; }},
        disabled: {get: () => input.disabled, set: value => { input.disabled = !!value; }},
    });
    input.onchange = () => onChange?.(input.checked); element.input = input;
    element.dispose = () => { input.onchange = null; }; return element;
}
