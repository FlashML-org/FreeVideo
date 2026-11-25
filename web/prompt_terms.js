// Camera and motion words offered while a prompt is typed. The list is local; nothing is sent anywhere,
// a word is written only when it is chosen, and the typed language decides which form is inserted.
import {caretRect} from './caret_rect.js';
import {listenDialogKey} from './dialog_keys.js';
import { translatorFor } from './i18n.js';
export const TERMS = [
    ['dolly in', '推镜头'], ['dolly out', '拉镜头'], ['pan left', '左摇镜头'], ['pan right', '右摇镜头'],
    ['tilt up', '上摇镜头'], ['tilt down', '下摇镜头'], ['tracking shot', '跟拍镜头'], ['orbit shot', '环绕镜头'],
    ['crane up', '升镜头'], ['crane down', '降镜头'], ['zoom in', '变焦推近'], ['zoom out', '变焦拉远'],
    ['handheld shot', '手持镜头'], ['static shot', '固定镜头'], ['low angle', '仰拍'], ['high angle', '俯拍'],
    ['aerial shot', '航拍'], ['close-up', '特写'], ['wide shot', '远景'], ['slow motion', '慢动作'],
].map(([en, zh]) => ({en, zh}));
const MAX_ROWS = 6, MIN_LATIN = 3, MIN_WORD_LATER = 4, MIN_CJK = 2, MAX_CJK = 6;
const CJK = /[\u3400-\u9fff]/;

// The words ending at `caret` that begin a known term: {start, end, matches: [{term, form}]} or null.
export function termMatches(text, caret = text.length, terms = TERMS) {
    const before = String(text).slice(0, caret);
    if (!before || /[@<]$/.test(before)) return null;
    const last = before[before.length - 1];
    if (CJK.test(last)) {
        const run = before.match(/[㐀-鿿]+$/)[0];
        for (let length = Math.min(run.length, MAX_CJK); length >= MIN_CJK; length--) {
            const query = run.slice(-length), found = terms.filter(term => term.zh.startsWith(query) && term.zh !== query);
            if (found.length) return {start: caret - length, end: caret, matches: found.slice(0, MAX_ROWS).map(term => ({term, form: 'zh'}))};
        }
        return null;
    }
    // One, two or three trailing words, so that "dolly i" still finds "dolly in" and "dolly " lists what follows.
    const tail = before.match(/[A-Za-z][A-Za-z -]*$/);
    if (!tail) return null;
    const parts = tail[0].split(' ');
    for (const words of [3, 2, 1]) {
        if (parts.length < words) continue;
        const fragment = parts.slice(-words).join(' '), query = fragment.toLowerCase(), start = caret - fragment.length;
        if (query.length < MIN_LATIN || (start > 0 && /[\w@<]/.test(before[start - 1]))) continue;
        const found = terms.filter(term => term.en.startsWith(query) && term.en !== query)
            .concat(words === 1 && query.length >= MIN_WORD_LATER ? terms.filter(term => !term.en.startsWith(query)
                && term.en.split(/[ -]/).some(word => word.startsWith(query))) : []);
        if (found.length) return {start, end: caret, matches: found.slice(0, MAX_ROWS).map(term => ({term, form: 'en'}))};
    }
    return null;
}

let serial = 0;
export function attachTermSuggest(input, {t = translatorFor(null, false), host, suppressed = () => false} = {}) {
    if (!document.querySelector('link[data-freevideo-references]')) {
        const css = document.createElement('link'); css.rel = 'stylesheet';
        css.dataset.freevideoReferences = ''; css.href = new URL('./prompt_references.css', import.meta.url).href;
        document.head.append(css);
    }
    const popup = document.createElement('div'); popup.className = 'fv-reference-picker fv-term-picker';
    popup.setAttribute('popover', 'manual'); popup.hidden = true;
    const list = document.createElement('div'); list.className = 'fv-reference-options';
    list.id = `fv-term-options-${++serial}`; list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', t('Camera and motion words', '运镜与动作词'));
    popup.append(list); (host || input.closest('dialog') || document.body).append(popup);
    let found = null, active = -1, composing = false, disposed = false, dismissed = null;
    let saved = null;
    const listeners = [];
    const listen = (target, name, fn, options) => {
        if (name.startsWith('key')) { listeners.push(listenDialogKey(target, name, fn, options)); return; }
        target.addEventListener(name, fn, options); listeners.push(() => target.removeEventListener(name, fn, options));
    };
    function close() {
        if (popup.hidePopover && popup.matches(':popover-open')) popup.hidePopover();
        popup.hidden = true; found = null;
        if (saved) {
            for (const [name, value] of saved) { if (value === null) input.removeAttribute(name); else input.setAttribute(name, value); }
            saved = null;
        }
    }
    function place() {
        if (popup.hidden) return;
        const rect = input.getBoundingClientRect();
        popup.style.width = `${Math.min(Math.max(rect.width * .6, 240), innerWidth - 24)}px`;
        const height = Math.min(popup.scrollHeight, 220);
        popup.style.left = `${Math.max(12, Math.min(rect.left, innerWidth - popup.offsetWidth - 12))}px`;
        const line = caretRect(input);
        popup.style.top = `${Math.max(12, line.bottom + height + 8 < innerHeight ? line.bottom + 4 : line.top - height - 4)}px`;
    }
    function select(index) {
        active = index;
        for (const [i, option] of [...list.children].entries()) option.setAttribute('aria-selected', String(i === active));
        if (list.children[active]) input.setAttribute('aria-activedescendant', list.children[active].id);
        else input.removeAttribute('aria-activedescendant');
    }
    function choose(index) {
        const hit = found?.matches[index]; if (!hit) return;
        const {start, end} = found, word = hit.term[hit.form];
        const spacer = hit.form === 'en' && !/^\s/.test(input.value.slice(end)) ? ' ' : '';
        close(); input.focus(); input.setSelectionRange(start, end);
        // The browser's own undo history keeps working: one undo gives back what was typed.
        if (!document.execCommand('insertText', false, word + spacer)) {
            input.setRangeText(word + spacer, start, end, 'end'); input.dispatchEvent(new Event('input', {bubbles: true}));
        }
    }
    function refresh() {
        if (disposed || composing || input.disabled || input.readOnly || document.activeElement !== input
            || input.selectionStart !== input.selectionEnd || suppressed()) { close(); return; }
        const next = termMatches(input.value, input.selectionStart);
        if (!next) { dismissed = null; close(); return; }
        if (dismissed && dismissed.start === next.start && dismissed.value === input.value.slice(next.start, next.end)) { close(); return; }
        dismissed = null; found = next;
        list.replaceChildren();
        for (const [index, hit] of next.matches.entries()) {
            const option = document.createElement('div'); option.id = `${list.id}-${index}`;
            option.className = 'fv-reference-option fv-term-option'; option.setAttribute('role', 'option');
            const main = document.createElement('strong'); main.textContent = hit.term[hit.form];
            option.append(main);
            option.onpointerdown = e => e.preventDefault(); option.onclick = () => choose(index);
            option.onpointermove = () => select(index); list.append(option);
        }
        const wasHidden = popup.hidden; popup.hidden = false;
        if (wasHidden && popup.showPopover) popup.showPopover();
        if (!saved) {
            saved = ['aria-controls', 'aria-expanded', 'aria-activedescendant'].map(name => [name, input.getAttribute(name)]);
        }
        input.setAttribute('aria-controls', list.id); input.setAttribute('aria-expanded', 'true');
        select(-1); place();
    }
    listen(input, 'input', refresh);
    listen(input, 'compositionstart', () => { composing = true; close(); });
    listen(input, 'compositionend', () => { composing = false; refresh(); });
    listen(input, 'keydown', e => {
        if (composing || e.isComposing || e.keyCode === 229 || popup.hidden || !found || e.ctrlKey || e.metaKey || e.altKey) return;
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault(); e.stopPropagation(); const n = found.matches.length;
            select(active < 0 ? (e.key === 'ArrowDown' ? 0 : n - 1) : (active + (e.key === 'ArrowDown' ? 1 : -1) + n) % n);
        } else if ((e.key === 'Tab' || e.key === 'Enter') && !e.shiftKey && active >= 0) {
            // Nothing is highlighted until an arrow key moves into the list, so typing, Enter and Tab keep their usual meaning.
            e.preventDefault(); e.stopPropagation(); choose(active);
        } else if (e.key === 'Escape') {
            e.preventDefault(); e.stopPropagation();
            dismissed = {start: found.start, value: input.value.slice(found.start, found.end)}; close();
        }
    });
    listen(input, 'blur', () => { if (!popup.contains(document.activeElement)) close(); });
    listen(document, 'pointerdown', e => { if (e.target !== input && !popup.contains(e.target)) close(); });
    listen(window, 'resize', place);
    listen(window, 'scroll', e => { if (!popup.contains(e.target)) place(); }, true);
    return {refresh, dispose() { disposed = true; close(); listeners.forEach(stop => stop()); popup.remove(); }};
}
