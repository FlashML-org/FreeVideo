import {isolateDialogKeys, listenDialogKey, routeDialogKey} from './dialog_keys.js';
// The creative panel loads this file eagerly, so the hover-help writer is local (the timeline's tips module stays lazy).
function setTip(element, {name, use, why = ''}) {
    for (const [attribute, value] of [['data-fv-tip-name', name], ['data-fv-tip-use', use], ['data-fv-tip-why', why]]) {
        if (value) { if (element.getAttribute(attribute) !== value) element.setAttribute(attribute, value); } else if (element.hasAttribute(attribute)) element.removeAttribute(attribute);
    }
    if (element.getAttribute('title') !== '') element.title = '';
}

let style;
function loadStyle() {
    if (style) return;
    const href = new URL('editor_expand.css', import.meta.url).href;
    style = [...document.querySelectorAll('link')].find(link => link.href === href);
    if (style) return;
    style = document.createElement('link');
    style.rel = 'stylesheet'; style.href = href;
    document.head.append(style);
}
const el = (tag, cls, text) => {
    const node = document.createElement(tag); node.className = cls;
    if (text != null) node.textContent = text;
    return node;
};

// The supplied textarea owns the text. Both views use its input handler; callers
// changing its value or generation policy programmatically send input or change.
export function createExpandButton({t = en => en, textarea, onGenerate, canGenerate, onDraft = () => {}}) {
    loadStyle();
    const element = el('button', 'fv-ed-expand'); element.type = 'button';
    // It opens a large box for a long description (it does not zoom anything): named for what it does.
    element.setAttribute('aria-label', t('Write in a large box', '在大窗口里写'));
    setTip(element, {name: t('Expand', '放大编辑'), use: t('Write a long description in a large box', '在大窗口里写长描述')});
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', 'M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7'); svg.append(path); element.append(svg);
    let dialog = null, large = null, notice = null, count = null, seen = '', pending = false, disposed = false, cleanups = [];
    // The small box is written as a whole while the panel is open, which ends its own undo history; so each panel session is one
    // step of the box's own, undone and redone from the box with the usual keys (a person who typed in the box afterwards keeps the native ones).
    let startText = null; const undone = [], redone = [];
    const setText = value => { textarea.value = value; seen = value; textarea.dispatchEvent(new Event('input', {bubbles: true})); };
    const stepKeys = event => {
        if (event.target !== textarea || event.defaultPrevented) return;
        if (!(event.ctrlKey || event.metaKey) || event.altKey || event.isComposing) return;
        // The letter the layout types, so QWERTZ (Z on the physical Y key) is right; only a non-Latin layout, whose keys type other
        // letters, falls back to the physical key.
        const typed = event.key.toLowerCase();
        const key = /^[a-z]$/.test(typed) ? typed : event.code === 'KeyZ' ? 'z' : event.code === 'KeyY' ? 'y' : typed;
        if (key === 'z' && !event.shiftKey) {
            const step = undone.at(-1);
            if (step && textarea.value === step.after) { event.preventDefault(); event.stopPropagation(); setText(step.before); redone.push(undone.pop()); }
        } else if ((key === 'z' && event.shiftKey) || key === 'y') {
            const step = redone.at(-1);
            if (step && textarea.value === step.before) { event.preventDefault(); event.stopPropagation(); setText(step.after); undone.push(redone.pop()); }
        }
    };
    // What the person typed in the box themself (script writes carry no input type) is a step too, taken in when a session opens.
    let typing = null;
    const watchTyping = event => {
        if (!event.isTrusted || !event.inputType) return;
        if (!typing) typing = {before: typingBefore, after: textarea.value}; else typing.after = textarea.value;
    };
    let typingBefore = textarea.value;
    const markBefore = () => { if (!typing) typingBefore = textarea.value; };
    textarea.addEventListener('beforeinput', markBefore); textarea.addEventListener('input', watchTyping);
    // On the box itself, and nowhere else: a listener on the window would stay after the box is gone. A dialog that takes its keys at the
    // window (Studio) routes them to what listenDialogKey registered; the editor's calls the box's onkeydown (the genbar's calls undoKeys).
    // Each reaches the same function, and a key it has handled is not handled again.
    const stopStepKeys = listenDialogKey(textarea, 'keydown', stepKeys);
    // Where the writer left off (text, scroll and caret) for the next visit to the same text.
    let reading = {text: null, top: 0, from: 0, to: 0};
    const writable = () => !textarea.disabled && !textarea.readOnly;
    const allowed = () => writable() && !pending && !!canGenerate();
    const counted = () => {
        if (!count) return;
        const n = Array.from(large.value).length, max = textarea.maxLength > 0 ? textarea.maxLength : 0;
        count.textContent = max ? `${n} / ${max}` : t(`${n} characters`, `${n} 字`);
    };
    function sync() {
        if (disposed) return;
        element.disabled = !writable();
        setTip(element, {name: t('Expand', '放大编辑'), use: t('Read or edit the description in a large box', '在大窗口里看或改描述'),
            why: element.disabled ? t('Select a part or draw a range to describe first', '先选中一段或画出范围，再写描述') : ''});
        if (!dialog) return;
        // The small box changed from outside (another part selected, text restored): the panel follows.
        if (textarea.value !== seen) { seen = textarea.value; if (large.value !== seen) large.value = seen; counted(); }
        large.disabled = !writable();
    }
    // Every edit in the panel reaches the small box at once (there is nothing to save).
    function commit() {
        if (!dialog || !textarea.isConnected || !writable()) return;
        // The caret and selection of the large box are the small box's too.
        if (large.value === textarea.value) { textarea.setSelectionRange?.(large.selectionStart, large.selectionEnd); return; }
        textarea.value = large.value; seen = textarea.value;
        textarea.setSelectionRange?.(large.selectionStart, large.selectionEnd);
        textarea.dispatchEvent(new Event('input', {bubbles: true}));
    }
    // Ctrl/⌘+Enter still generates (said in the hover help only); the panel closes once the run is asked for.
    async function submit() {
        if (!onGenerate || !allowed()) return;
        commit(); notice.textContent = '';
        pending = true; sync();
        let reason = '';
        try { reason = await onGenerate(); }
        finally {
            pending = false;
            if (notice) {
                notice.textContent = typeof reason === 'string' ? reason : '';
                if (!notice.textContent) close(); else if (!large.value.trim()) large.focus();
            }
            sync();
        }
    }
    function close() {
        if (!dialog) return;
        commit();
        if (startText !== null && textarea.value !== startText) { undone.push({before: startText, after: textarea.value}); redone.length = 0; }
        startText = null;
        reading = {text: large.value, top: large.scrollTop, from: textarea.selectionStart, to: textarea.selectionEnd};
        const current = dialog; dialog = large = notice = count = null;
        cleanups.splice(0).forEach(stop => stop());
        current.oncancel = current.onclose = null;
        if (current.open) current.close(); current.remove();
        if (element.isConnected) element.focus({preventScroll: true});
    }
    // A quiet reading and editing panel: the title and Close on top, the text large in the middle, the length and Done
    // below. No Generate button.
    function open() {
        sync();
        if (disposed || element.disabled || dialog) return;
        startText = textarea.value;
        if (typing && typing.after === startText && typing.before !== typing.after) { undone.push({before: typing.before, after: typing.after}); redone.length = 0; }
        typing = null;
        dialog = el('dialog', 'fv-ed-expand-dialog');
        dialog.dataset.fvOwnTab = ''; // Tab cycles its three controls below; a11y.js stands aside
        dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'true');
        dialog.setAttribute('aria-label', t('Description', '描述'));
        const head = el('div', 'fv-ed-expand-head'), title = el('strong', 'fv-ed-expand-title', t('Description', '描述'));
        const dismiss = el('button', 'fv-ed-expand-close'); dismiss.type = 'button'; dismiss.onclick = close;
        dismiss.setAttribute('aria-label', t('Close', '关闭'));
        setTip(dismiss, {name: t('Close', '关闭'), use: t('Back to the panel; the text is already in it', '回到面板，改动已经在里面')});
        const x = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); x.setAttribute('viewBox', '0 0 16 16'); x.setAttribute('aria-hidden', 'true');
        const cross = document.createElementNS('http://www.w3.org/2000/svg', 'path'); cross.setAttribute('d', 'M4 4l8 8M12 4l-8 8'); x.append(cross); dismiss.append(x);
        head.append(title, dismiss);
        seen = textarea.value;
        large = el('textarea', 'fv-ed-expand-text'); large.value = seen;
        if (textarea.maxLength >= 0) large.maxLength = textarea.maxLength;
        large.setAttribute('aria-label', textarea.getAttribute('aria-label') || t('Description', '描述'));
        large.placeholder = textarea.placeholder || '';
        const body = el('div', 'fv-ed-expand-body'); body.append(large);
        const foot = el('div', 'fv-ed-expand-foot');
        count = el('span', 'fv-ed-expand-count');
        notice = el('div', 'fv-ed-expand-note'); notice.setAttribute('role', 'status');
        const done = el('button', 'fv-ed-expand-done', t('Done', '完成')); done.type = 'button'; done.onclick = close;
        const mod = /Mac|iPhone|iPad/.test(navigator.platform || '') ? '⌘' : 'Ctrl+';
        setTip(done, {name: t('Done', '完成'), use: onGenerate ? t(`Close the panel; ${mod}Enter generates`, `关闭面板；${mod}Enter 直接生成`) : t('Close the panel', '关闭面板')});
        foot.append(count, notice, done);
        dialog.append(head, body, foot);
        large.oninput = () => {
            notice.textContent = '';
            if (writable()) { commit(); onDraft(large.value); }
            counted(); sync();
        };
        cleanups.push(listenDialogKey(dialog, 'keydown', event => {
            if (event.isComposing || event.keyCode === 229) return;
            if (event.key === 'Escape') { event.preventDefault(); close(); }
            else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                event.preventDefault(); if (!event.repeat) submit();
            } else if (event.key === 'Tab') {
                event.preventDefault();
                const controls = [large, done, dismiss].filter(node => node && !node.disabled);
                const index = controls.indexOf(document.activeElement);
                controls[(index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length]?.focus();
            }
        }));
        cleanups.push(isolateDialogKeys(dialog, event => routeDialogKey(event, dialog)));
        dialog.oncancel = event => { event.preventDefault(); event.stopPropagation(); close(); };
        dialog.onclose = () => { if (dialog && !dialog.open) close(); };
        const parent = textarea.closest('dialog') || document.body;
        parent.addEventListener('close', close); cleanups.push(() => parent.removeEventListener('close', close));
        // A click outside the panel (on its backdrop) closes it.
        dialog.addEventListener('mousedown', event => {
            if (event.target !== dialog) return;
            const r = dialog.getBoundingClientRect();
            if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) { event.preventDefault(); close(); }
        });
        const stop = event => event.stopPropagation();
        for (const type of ['pointerdown', 'pointerup', 'pointermove', 'mousedown', 'mouseup', 'mousemove', 'click', 'dblclick', 'wheel', 'contextmenu']) dialog.addEventListener(type, stop);
        parent.append(dialog); counted(); sync(); dialog.showModal();
        // The caret and selection are the small box's (a box never given one has them at the end); the caret's line is centred, not
        // pressed to an edge, unless the same text is being returned to, which opens where it was left.
        const from = textarea.selectionStart ?? large.value.length, to = textarea.selectionEnd ?? large.value.length;
        if (reading.text === large.value && reading.from === from && reading.to === to) { large.focus(); large.setSelectionRange?.(from, to); large.scrollTop = reading.top; }
        else {
            const full = large.value; large.value = full.slice(0, to);
            large.scrollTop = 0; const above = large.scrollHeight, view = large.clientHeight; large.value = full;
            const top = above <= view ? 0 : above - view / 2;
            large.focus(); large.setSelectionRange?.(from, to); large.scrollTop = top;
        }
    }
    element.onclick = open;
    textarea.addEventListener('input', sync); textarea.addEventListener('change', sync);
    const observer = typeof MutationObserver === 'function' ? new MutationObserver(sync) : null;
    observer?.observe(textarea, {attributes: true, attributeFilter: ['disabled', 'readonly']});
    sync();
    return {element, open, close, commit, undoKeys: stepKeys, get isOpen() { return !!dialog; }, dispose() {
        close(); disposed = true; observer?.disconnect();
        textarea.removeEventListener('input', sync); textarea.removeEventListener('change', sync);
        stopStepKeys(); textarea.removeEventListener('beforeinput', markBefore); textarea.removeEventListener('input', watchTyping);
        element.onclick = null; element.remove();
    }};
}
