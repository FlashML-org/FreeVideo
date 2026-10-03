// H3 numbers each reference kind separately, in the order of comfy_media.export.
const titles = {image: 'Picture', video: 'Video', audio: 'Audio'};
const widget = (node, name) => node?.widgets?.find(w => w.name === name);
const linkFor = (node, name) => node?.graph?.links?.[node.inputs?.find(i => i.name === name)?.link];
const sourceFor = (node, name) => node?.graph?.getNodeById(linkFor(node, name)?.origin_id);
export function referenceKind(file) {
    const ext = String(file).split('.').pop().toLowerCase();
    return ['png','jpg','jpeg','webp','bmp','tif','tiff'].includes(ext) ? 'image'
        : ['mp4','mov','webm','mkv','m4v'].includes(ext) ? 'video'
        : ['wav','mp3','flac','ogg','m4a','aac','opus'].includes(ext) ? 'audio' : null;
}

export function referenceItems(node) {
    if (linkFor(node, 'conditioning')) return [];
    const refs = [], anchors = {}, seen = new Set();
    const connected = (owner, name, kind) => {
        const link = linkFor(owner, name), source = sourceFor(owner, name);
        return link ? {key: `node:${owner.id}:${name}:${link.origin_id}:${link.origin_slot}`,
            kind, name: source?.title || source?.type || name} : null;
    };
    const media = sourceFor(node, 'media');
    if (media) {
        if (['first','last','references'].some(name => linkFor(node, name))) throw new Error('invalid_media');
        const serialized = widget(media, 'assets');
        if (!serialized) throw new Error('unknown_references');
        const rows = JSON.parse(serialized.value || '[]'), counts = new Map();
        if (!Array.isArray(rows)) throw new Error('invalid_media');
        for (const row of rows) {
            const occurrence = counts.get(row.file) || 0; counts.set(row.file, occurrence + 1);
            if (row.enabled === false) continue;
            const kind = referenceKind(row.file);
            if (!kind) throw new Error('invalid_media');
            const item = {key: `file:${media.id}:${row.file}:${occurrence}`, kind, file: row.file,
                name: row.file.split('/').pop(), assetIndex: rows.indexOf(row)};
            if (row.role === 'reference') refs.push(item);
            else if (kind === 'image' && ['first','last'].includes(row.role) && !anchors[row.role]) anchors[row.role] = item;
            else throw new Error('invalid_media');
        }
        for (const name of ['first','last']) {
            const item = connected(media, name, 'image');
            if (item && anchors[name]) throw new Error('invalid_media');
            if (item) anchors[name] = item;
        }
        for (const [name, kind] of [['reference','image'],['reference_audio','audio']]) {
            const item = connected(media, name, kind); if (item) refs.push(item);
        }
    } else {
        for (const name of ['first','last']) {
            const item = connected(node, name, 'image'); if (item) anchors[name] = item;
        }
        function stack(current) {
            if (!current) return;
            if (seen.has(current.id) || current.type !== 'FreeVideoReference') throw new Error('unknown_references');
            seen.add(current.id); stack(sourceFor(current, 'previous'));
            const items = ['image','video','audio'].map(kind => connected(current, kind, kind)).filter(Boolean);
            if (items.length !== 1) throw new Error('invalid_media');
            refs.push(items[0]);
        }
        stack(sourceFor(node, 'references'));
    }
    if (refs.length && Object.keys(anchors).length) throw new Error('invalid_media');
    const ordered = refs.length ? refs : ['first','last'].filter(k => anchors[k]).map(k => ({...anchors[k], role: k}));
    const counters = {image: 0, video: 0, audio: 0};
    return ordered.map(item => ({...item, number: ++counters[item.kind],
        token: `<${titles[item.kind]} ${counters[item.kind]}>`}));
}

export function remapReferences(text, previous, current, removed = '[Removed reference]') {
    const byKey = new Map(current.map(item => [item.key, item.token]));
    const replacement = new Map(previous.map(item => [item.token, byKey.get(item.key) || removed]));
    return text.replace(/<(?:Picture|Video|Audio) \d+>/g, token => replacement.get(token) || token);
}

const bindings = new WeakMap();
export function syncReferencePrompt(node, t = en => en, reset = false) {
    const text = widget(node, 'text');
    if (!text || linkFor(node, 'text') || linkFor(node, 'conditioning')) { bindings.delete(node); return; }
    let items; try { items = referenceItems(node); } catch { return; }
    const previous = reset ? null : bindings.get(node);
    const current = items.map(({key, token}) => ({key, token}));
    if (Array.isArray(previous) && JSON.stringify(previous) !== JSON.stringify(current)) {
        const updated = remapReferences(String(text.value || ''), previous, current, t('[Removed reference]', '[素材已移除]'));
        if (updated !== text.value) { text.value = updated; text.callback?.(updated); node.graph?.change(); }
    }
    bindings.set(node, current);
    window.dispatchEvent(new CustomEvent('freevideo-reference-prompt', {detail: node.id}));
}

export function mentionAt(text, start, end = start) {
    if (start < 1 || start !== end) return null;
    const at = text.lastIndexOf('@', start - 1);
    if (at < 0 || (at > 0 && /[\w.%+\-]/.test(text[at - 1]))) return null;
    const query = text.slice(at + 1, start);
    return /^[\p{L}\p{N}_. \-]{0,64}$/u.test(query) ? {start: at, end: start, query: query.trim().toLowerCase()} : null;
}

let serial = 0;
export function attachReferencePicker(input, {items, t = en => en, view, host, addMedia} = {}) {
    if (!document.querySelector('link[data-freevideo-references]')) {
        const css = document.createElement('link'); css.rel = 'stylesheet';
        css.dataset.freevideoReferences = ''; css.href = new URL('./prompt_references.css', import.meta.url).href;
        document.head.append(css);
    }
    const popup = document.createElement('div'); popup.className = 'fv-reference-picker';
    popup.setAttribute('popover', 'manual'); popup.hidden = true;
    const list = document.createElement('div'); list.className = 'fv-reference-options';
    list.id = `fv-reference-options-${++serial}`; list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', t('Reference media', '参考素材'));
    popup.append(list); (host || input.closest('dialog') || document.body).append(popup);
    const saved = new Map(['role','aria-autocomplete','aria-controls','aria-expanded','aria-activedescendant']
        .map(name => [name, input.getAttribute(name)]));
    input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', list.id); input.setAttribute('aria-expanded', 'false');
    let matches = [], active = 0, range = null, composing = false, disposed = false;
    const listeners = [];
    const listen = (target, name, fn, options) => { target.addEventListener(name, fn, options); listeners.push(() => target.removeEventListener(name, fn, options)); };
    function close() {
        if (popup.hidePopover && popup.matches(':popover-open')) popup.hidePopover();
        popup.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant');
    }
    function place() {
        if (popup.hidden) return;
        const rect = input.getBoundingClientRect();
        popup.style.width = `${Math.min(Math.max(rect.width, 260), innerWidth - 24)}px`;
        const height = Math.min(popup.scrollHeight, 280);
        popup.style.left = `${Math.max(12, Math.min(rect.left, innerWidth - popup.offsetWidth - 12))}px`;
        popup.style.top = `${Math.max(12, rect.bottom + height + 8 < innerHeight ? rect.bottom + 6 : rect.top - height - 6)}px`;
    }
    function select(index) {
        active = index;
        for (const [i, option] of [...list.children].entries()) option.setAttribute('aria-selected', String(i === active));
        const selected = list.children[active];
        if (selected && matches.length) {
            input.setAttribute('aria-activedescendant', selected.id);
            if (selected.offsetTop < list.scrollTop) list.scrollTop = selected.offsetTop;
            else if (selected.offsetTop + selected.offsetHeight > list.scrollTop + list.clientHeight)
                list.scrollTop = selected.offsetTop + selected.offsetHeight - list.clientHeight;
        }
        else input.removeAttribute('aria-activedescendant');
    }
    function insert(value, start, end) {
        input.focus(); input.setSelectionRange(start, end);
        // Preserve the browser's native undo history for prompt edits.
        if (!document.execCommand('insertText', false, value)) {
            input.setRangeText(value, start, end, 'end'); input.dispatchEvent(new Event('input', {bubbles: true}));
        }
    }
    function choose(index) {
        if (!range || !matches[index]) return;
        const item = matches[index], selection = range;
        close(); insert(item.token + (/^\s/.test(input.value.slice(selection.end)) ? '' : ' '), selection.start, selection.end); close();
    }
    function refresh() {
        if (disposed || composing || input.disabled || input.readOnly || document.activeElement !== input) { close(); return; }
        range = mentionAt(input.value, input.selectionStart, input.selectionEnd);
        if (!range) { close(); return; }
        let available = [], invalid = false;
        try { available = items(); } catch { invalid = true; }
        matches = available.filter(item => `${item.name} ${item.token} ${t(item.kind, {image:'图片',video:'视频',audio:'音频'}[item.kind])} ${item.number}`.toLowerCase().includes(range.query));
        list.replaceChildren();
        if (!matches.length) {
            const empty = document.createElement('div'); empty.className = 'fv-reference-empty';
            empty.textContent = invalid ? t('Check your media selection.', '请检查素材用途是否冲突。')
                : available.length ? t('No matching references', '没有匹配的素材') : t('Add reference media to use @', '添加参考素材后即可使用 @');
            list.append(empty);
            if (!available.length && !invalid && addMedia) {
                const add = document.createElement('button'); add.type = 'button'; add.textContent = t('Add media', '添加素材');
                add.onpointerdown = e => e.preventDefault();
                add.onclick = () => { close(); addMedia(); }; list.append(add);
            }
        }
        for (const [index, item] of matches.entries()) {
            const option = document.createElement('div'); option.id = `${list.id}-${index}`;
            option.className = 'fv-reference-option'; option.setAttribute('role', 'option');
            const thumb = document.createElement(item.file && item.kind === 'image' ? 'img' : 'span');
            thumb.className = 'fv-reference-thumb';
            if (thumb.tagName === 'IMG') { thumb.src = view(item.file); thumb.alt = ''; }
            else thumb.textContent = item.kind === 'audio' ? '♫' : item.kind === 'video' ? '▸' : '▧';
            const label = document.createElement('span'), name = document.createElement('strong'), detail = document.createElement('small');
            name.textContent = t({image:'Image',video:'Video',audio:'Audio'}[item.kind], {image:'图片',video:'视频',audio:'音频'}[item.kind]) + ` ${item.number}`;
            detail.textContent = item.name; label.append(name, detail);
            const token = document.createElement('code'); token.textContent = item.token;
            option.append(thumb, label, token); option.title = item.name;
            option.onpointerdown = e => e.preventDefault(); option.onclick = () => choose(index);
            option.onpointermove = () => select(index); list.append(option);
        }
        const wasHidden = popup.hidden; popup.hidden = false;
        if (wasHidden && popup.showPopover) popup.showPopover();
        input.setAttribute('aria-expanded', 'true'); active = 0; select(active); place();
    }
    listen(input, 'input', refresh); listen(input, 'click', refresh);
    listen(input, 'compositionstart', () => { composing = true; close(); });
    listen(input, 'compositionend', () => { composing = false; refresh(); });
    listen(input, 'keydown', e => {
        if (composing || e.isComposing || e.keyCode === 229 || popup.hidden || e.ctrlKey || e.metaKey || e.altKey) return;
        if (['ArrowDown','ArrowUp'].includes(e.key) && matches.length) {
            e.preventDefault(); e.stopPropagation(); select((active + (e.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length);
        } else if ((e.key === 'Enter' || e.key === 'Tab') && !e.shiftKey && matches.length) {
            e.preventDefault(); e.stopPropagation(); choose(active);
        } else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
    });
    listen(input, 'keyup', e => { if (['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) refresh(); });
    listen(input, 'blur', () => { if (!popup.contains(document.activeElement)) close(); });
    listen(document, 'pointerdown', e => { if (e.target !== input && !popup.contains(e.target)) close(); });
    listen(window, 'resize', place);
    listen(window, 'scroll', e => { if (!popup.contains(e.target)) place(); }, true);
    return {
        refresh,
        open() {
            if (input.disabled || input.readOnly) return;
            if (!mentionAt(input.value, input.selectionStart, input.selectionEnd)) {
                const prefix = input.value[input.selectionStart - 1] || '';
                insert((/[\w.%+\-]/.test(prefix) ? ' ' : '') + '@', input.selectionStart, input.selectionEnd);
            }
            input.focus(); refresh();
        },
        dispose() { disposed = true; close(); listeners.forEach(stop => stop()); popup.remove(); for (const [name, value] of saved) { if (value === null) input.removeAttribute(name); else input.setAttribute(name, value); } },
    };
}
