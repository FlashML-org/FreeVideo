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
        if (!dialog || !textarea.isConnected || !writable() || large.value === textarea.value) return;
        textarea.value = large.value; seen = textarea.value;
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
                if (!notice.textContent) close();
            }
            sync();
        }
    }
    function close() {
        if (!dialog) return;
        commit();
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
        // The caret goes to the end of the text, which is scrolled into view.
        const end = large.value.length;
        large.focus(); large.setSelectionRange?.(end, end); large.scrollTop = large.scrollHeight;
    }
    element.onclick = open;
    textarea.addEventListener('input', sync); textarea.addEventListener('change', sync);
    const observer = typeof MutationObserver === 'function' ? new MutationObserver(sync) : null;
    observer?.observe(textarea, {attributes: true, attributeFilter: ['disabled', 'readonly']});
    sync();
    return {element, open, close, commit, get isOpen() { return !!dialog; }, dispose() {
        close(); disposed = true; observer?.disconnect();
        textarea.removeEventListener('input', sync); textarea.removeEventListener('change', sync);
        element.onclick = null; element.remove();
    }};
}
