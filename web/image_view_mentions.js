// Reference pictures named in the prompt. Typing "@" lists the reference pictures; choosing one writes "@图1"
// ("@Image1"): the engine's Picture 1. A mention is bound to its picture, not to the number: when pictures are put in
// front or taken out, the numbers in the text follow them, and a mention whose picture was taken out reads "@图?"
// and is marked until it is chosen again. The marks are drawn over the box (the text stays the box's own).
import { listenDialogKey } from './dialog_keys.js';
import { el, t, icon } from './image_view_ui.js';

const TOKEN = /@(?:图|Image) ?(\d+|\?)/g;
export const mentionText = n => t(`@Image${n}`, `@图${n}`);

// The text with each mention renumbered from `before` (the pictures as they were) to `after` (as they are now).
// `caret` is moved by the change in length of the mentions in front of it.
export function renumberMentions(text, before, after, caret = 0) {
    let shift = 0;
    const next = text.replace(TOKEN, (whole, n, at) => {
        if (n === '?') return whole;
        const picture = before[Number(n) - 1];
        const k = picture ? after.indexOf(picture) : -1;
        const written = whole.replace(/(\d+)$/, k >= 0 ? String(k + 1) : '?');
        if (at + whole.length <= caret) shift += written.length - whole.length;
        return written;
    });
    return {text: next, caret: caret + shift, changed: next !== text};
}

// What the engine reads: each mention as <Picture N>; one without a picture is left out.
export function mentionsToEngine(text, count) {
    return text.replace(TOKEN, (_, n) => n !== '?' && Number(n) >= 1 && Number(n) <= count ? `<Picture ${n}>` : '').replace(/ {2,}/g, ' ');
}
// A picture drawn as a sketch (image editor's sketch mode) gives the layout only. The words sent around it describe the
// finished picture the user wants, all in plain affirmative sentences (the models follow what is said, and a "do not"
// can bring about the very thing it names): a finished, fully coloured, detailed picture of the description, the
// sketch's composition, positions and poses, the style the description names, a complete scene behind.
// `numbers`: the sketches' places (1-based) in the reference list; `text`: the engine text with <Picture N>.
const SKETCH_LEAD = /^\s*(?:按\s*<Picture (\d+)>\s*的构图[，,]?|Following the layout of <Picture (\d+)>,?)\s*/;
export function sketchPrompt(text, numbers, model) {
    if (!numbers?.length) return text;
    const desc = String(text || '').replace(SKETCH_LEAD, '').trim().replace(/[。.]+$/, '');
    const pics = numbers.map(n => `<Picture ${n}>`).join(' and ');
    const what = desc || 'the scene in the sketch';
    const rest = `The composition, positions and poses match ${pics}. The style follows the description. The background is a complete scene that fits the content.`;
    // The user's words come first as the picture's own description (after a colon they were drawn as a caption).
    // Qwen keeps an input picture's plain paper unless the whole frame is described as filled.
    if (model === 'qwen-image-2.1-turbo') return `Redraw ${pics} as a finished, fully coloured and detailed image that fills the whole frame with a complete scene. ${what}. `
        + `The composition, positions and poses match ${pics}. The style follows the description. Every part of the picture, from edge to edge, shows the scene around and behind the subject: its ground, its surroundings and its light.`;
    return `${what}. A finished, fully coloured and detailed image. ${rest}`;
}
export const brokenMentions = (text, count) => [...text.matchAll(TOKEN)].filter(([, n]) => n === '?' || Number(n) < 1 || Number(n) > count).length;
export const hasMentions = text => new RegExp(TOKEN.source).test(text);

// The marks over the box and the "@" list. pictures(): [{url, label}] in order; onAdd(anchor): the way to add one.
export function promptMentions({prompt, host, pictures, onAdd, onPick = () => {}}) {
    const marks = el('div', 'fv-iv-marks'), sheet = el('div', 'fv-iv-marks-text'), caretMark = el('span', 'fv-iv-marks-caret');
    marks.setAttribute('aria-hidden', 'true'); marks.append(sheet);
    prompt.after(marks);
    const list = el('div', 'fv-iv-at fv-glass fv-g3'); list.setAttribute('role', 'listbox'); list.hidden = true;
    list.setAttribute('aria-label', t('Reference pictures', '参考图'));
    host.append(list);
    let open = null; // {start, active, items}
    const COPY = ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing', 'lineHeight', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'textIndent', 'wordSpacing', 'tabSize'];

    function paint() {
        const cs = getComputedStyle(prompt);
        for (const k of COPY) sheet.style[k] = cs[k];
        marks.style.left = `${prompt.offsetLeft + prompt.clientLeft}px`; marks.style.top = `${prompt.offsetTop + prompt.clientTop}px`;
        marks.style.width = `${prompt.clientWidth}px`; marks.style.height = `${prompt.clientHeight}px`;
        const text = prompt.value, count = pictures().length, caret = prompt.selectionStart ?? text.length;
        const parts = []; let last = 0, caretPlaced = false;
        const plain = (from, to) => {
            if (!caretPlaced && caret >= from && caret <= to) { parts.push(document.createTextNode(text.slice(from, caret)), caretMark, document.createTextNode(text.slice(caret, to))); caretPlaced = true; }
            else parts.push(document.createTextNode(text.slice(from, to)));
        };
        for (const m of text.matchAll(TOKEN)) {
            plain(last, m.index);
            const mark = el('mark', 'fv-iv-mark', m[0]);
            if (m[1] === '?' || Number(m[1]) > count) mark.dataset.broken = 'true';
            parts.push(mark); last = m.index + m[0].length;
        }
        plain(last, text.length);
        if (!caretPlaced) parts.push(caretMark);
        // A last empty line still has its height in the box.
        parts.push(document.createTextNode('​'));
        sheet.replaceChildren(...parts);
        sheet.style.transform = `translateY(${-prompt.scrollTop}px)`;
        // While the list is open the marks stay laid out: the list is placed at the "@".
        marks.hidden = !hasMentions(text) && !open;
        if (open) place();
    }

    // The list sits under the "@", inside the box's own left and right edges (moved left when the "@" is near the right).
    function place() {
        const box = marks.getBoundingClientRect(), r = caretMark.getBoundingClientRect(), edge = prompt.getBoundingClientRect();
        const line = parseFloat(getComputedStyle(prompt).lineHeight) || 20, width = Math.min(240, edge.width, innerWidth - 32);
        list.style.width = `${width}px`;
        const left = Math.max(edge.left, Math.min(edge.right - width, (r.left || box.left) - 12));
        let top = Math.min(box.bottom, (r.top || box.top) + line) + 4;
        const height = list.offsetHeight || 200;
        if (top + height > innerHeight - 16) top = Math.max(16, (r.top || box.top) - height - 4);
        list.style.left = `${Math.round(left)}px`; list.style.top = `${Math.round(top)}px`;
    }

    function items(query) {
        const all = pictures().map((p, at) => ({n: at + 1, ...p}));
        const q = query.replace(/^(图|image)/i, '').trim();
        return q ? all.filter(p => String(p.n).startsWith(q)) : all;
    }
    function draw() {
        const rows = open.items.map((p, k) => {
            const b = el('button', 'fv-iv-at-item'); b.type = 'button'; b.setAttribute('role', 'option'); b.tabIndex = -1;
            b.setAttribute('aria-selected', String(k === open.active));
            const thumb = el('img'); thumb.src = p.url; thumb.alt = '';
            b.append(thumb, el('span', 'fv-iv-at-name', p.label), el('span', 'fv-iv-at-tag', mentionText(p.n)));
            b.addEventListener('pointerdown', event => event.preventDefault());
            b.addEventListener('click', () => choose(p.n));
            return b;
        });
        if (!rows.length) {
            const empty = el('p', 'fv-iv-at-empty', pictures().length ? t('No picture with that number', '没有这个编号的参考图') : t('No reference pictures yet', '还没有参考图'));
            const add = el('button', 'fv-iv-at-item fv-iv-at-add'); add.type = 'button'; add.setAttribute('role', 'option'); add.tabIndex = -1;
            add.append(icon('plus'), el('span', 'fv-iv-at-name', t('Add a reference picture', '添加参考图')));
            add.setAttribute('aria-selected', 'true');
            add.addEventListener('pointerdown', event => event.preventDefault());
            add.addEventListener('click', () => { const at = open?.start; close(); if (at != null) removeAt(at); onAdd(prompt); });
            rows.push(empty, add);
        }
        list.replaceChildren(...rows);
        list.querySelector('[aria-selected=true]')?.scrollIntoView({block: 'nearest'});
    }
    function removeAt(start) {
        // The lone "@" that opened the list goes with it when the list leads elsewhere.
        if (prompt.value[start] === '@') { prompt.setRangeText('', start, Math.max(start + 1, prompt.selectionStart ?? start + 1), 'end'); prompt.dispatchEvent(new Event('input', {bubbles: true})); }
    }
    function openAt(start) {
        open = {start, active: 0, items: []};
        list.hidden = false; refresh();
    }
    function refresh() {
        if (!open) return;
        const caret = prompt.selectionStart ?? 0, query = prompt.value.slice(open.start + 1, caret);
        if (caret <= open.start || prompt.value[open.start] !== '@' || /\s/.test(query) || query.length > 8) { close(); return; }
        open.items = items(query); open.active = Math.min(open.active, Math.max(0, open.items.length - 1));
        draw(); paint();
    }
    function close() { if (!open) return; open = null; list.hidden = true; list.replaceChildren(); }
    function choose(n) {
        if (!open) return;
        const start = open.start, end = prompt.selectionStart ?? start + 1;
        close();
        // The text after it stays as it is; only a digit right after would read as part of the number.
        const after = /\d/.test(prompt.value[end] || '') ? ' ' : '';
        prompt.setRangeText(mentionText(n) + after, start, end, 'end');
        prompt.focus();
        prompt.dispatchEvent(new Event('input', {bubbles: true}));
        onPick(n);
    }

    const onInput = event => {
        if (open) { refresh(); return; }
        const caret = prompt.selectionStart ?? 0;
        // "@" after anything (a letter, a Chinese character, a digit, a space or the start) opens the list.
        if (event?.inputType?.startsWith('insert') && prompt.value[caret - 1] === '@') openAt(caret - 1);
        else paint();
    };
    prompt.addEventListener('input', onInput);
    prompt.addEventListener('scroll', () => { sheet.style.transform = `translateY(${-prompt.scrollTop}px)`; if (open) place(); }, {passive: true});
    prompt.addEventListener('click', () => { if (open) refresh(); });
    prompt.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== prompt) close(); }, 0));
    listenDialogKey(prompt, 'keydown', event => {
        if (!open) return;
        const n = open.items.length;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault(); if (!n) return;
            open.active = (open.active + (event.key === 'ArrowDown' ? 1 : n - 1)) % n; draw();
        } else if (event.key === 'Enter' || event.key === 'Tab') {
            event.preventDefault();
            if (n) choose(open.items[open.active].n); else list.querySelector('.fv-iv-at-add')?.click();
        } else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
        else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight' || event.key === 'Home' || event.key === 'End') setTimeout(refresh, 0);
    });
    const watch = typeof ResizeObserver === 'function' ? new ResizeObserver(() => paint()) : null; watch?.observe(prompt);
    const outside = event => { if (open && !list.contains(event.target) && event.target !== prompt) close(); };
    document.addEventListener('pointerdown', outside, true);
    return {paint, close, isOpen: () => !!open, element: list,
        dispose() { watch?.disconnect(); document.removeEventListener('pointerdown', outside, true); list.remove(); marks.remove(); }};
}
