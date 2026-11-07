// Reference pictures for a new part, kept small inside the generation bar: thumbnails and one "+".
// A part that continues the one before keeps its reference pictures (people stay the same) but not its first or last frame.
// `source` is the media rows of the creative panel (editor_media_references.js); `size()` is the video's shape.
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const IMAGE = /\.(png|jpe?g|webp|bmp|tiff?)$/i;
const MAX = 32;
// The engine keeps three references of a part that continues: the first three, or, with two or more characters,
// the last picture of each (the face) and then the others (continuation.pick_pictures).
const CONTINUE_MAX = 3;
const personOf = ({row}) => typeof row.cast?.id === 'string' && row.cast.id && row.cast.setting !== true && row.cast.prop !== true ? row.cast.id : null;
const placeOf = ({row}) => row.cast?.setting === true;
const propOf = ({row}) => row.cast?.prop === true;
function pickContinued(list) {
    // A scene's picture keeps one place after the others (continuation.pick_pictures).
    const places = list.filter(placeOf), place = places[places.length - 1];
    if (place) list = list.filter(entry => !placeOf(entry));
    // Props come after the people and the place and keep only the positions left over.
    const props = list.filter(propOf);
    list = list.filter(entry => !propOf(entry));
    const room = CONTINUE_MAX - (place ? 1 : 0);
    const last = new Map();
    list.forEach((entry, at) => { const person = personOf(entry); if (person) last.set(person, at); });
    const chosen = last.size < 2 ? list.slice(0, room)
        : [...[...last.values()].sort((a, b) => a - b).map(at => list[at]), ...list.filter(entry => !personOf(entry))].slice(0, room);
    const kept = [...chosen, ...props.slice(0, room - chosen.length)];
    return place ? [...kept, place] : kept;
}
export function createEditorReferences({t, source, size = () => null, error = () => {}, notify = () => {}}) {
    const root = el('div', 'fv-ed-refs'), chips = el('div', 'fv-ed-ref-chips');
    const add = el('button', 'fv-ed-ref-add'); add.type = 'button';
    add.setAttribute('aria-haspopup', 'menu'); add.setAttribute('aria-expanded', 'false');
    add.setAttribute('aria-label', t('Reference image', '参考图')); add.title = t('Reference image', '参考图');
    add.textContent = '+'; add.dataset.fvGuide = 'editor-refs';
    const menu = el('div', 'fv-ed-ref-menu'); menu.hidden = true; menu.setAttribute('role', 'menu');
    const picker = el('input'); picker.type = 'file'; picker.accept = 'image/*'; picker.multiple = true; picker.hidden = true;
    root.append(add, chips, menu, picker);
    let shown = false, continuing = false, busy = false, working = false, uploading = false, opener = null, disposed = false, entries = [], waited = false;
    const active = () => source.rows().map((row, index) => ({row, index})).filter(({row}) => row.enabled !== false);
    const isImage = file => IMAGE.test(file || '');
    // The pictures this part uses: all of them, or the first reference images of a part that continues
    // (sound or video references do not count there, as the engine skips them).
    const used = () => continuing ? pickContinued(active().filter(({row}) => (row.role || 'reference') === 'reference' && isImage(row.file))) : active();
    const full = () => continuing ? used().length >= CONTINUE_MAX : active().length >= MAX;
    // On hover without a full stop; in the notice line with one.
    const waitText = () => t('Available after generation finishes', '生成完成后可改');
    const fullText = () => continuing ? t('Up to 3 images when continuing', '接续时最多 3\u00a0张') : t('Up to 32 images', '最多 32\u00a0张');
    const sentence = text => text + t('.', '。');
    const label = role => role === 'first' ? t('First frame', '首帧') : role === 'last' ? t('Last frame', '尾帧') : t('Reference', '参考');
    function close(focus = true) {
        menu.hidden = true; add.setAttribute('aria-expanded', 'false');
        const back = opener; opener = null;
        if (focus && back?.isConnected) back.focus();
    }
    // After a choice the menu is gone; a chip that was re-drawn by the change is found again by position so keyboard focus stays in the row.
    function choose(run) {
        const back = opener, at = Array.from(chips.children).indexOf(back);
        close(false); run();
        const target = back?.isConnected ? back : back === add ? add : chips.children[at] || add;
        if (target.isConnected && !target.disabled) target.focus();
    }
    function open(anchor, items) {
        menu.replaceChildren(...items); menu.hidden = false; opener = anchor;
        add.setAttribute('aria-expanded', String(anchor === add));
        // Under the clicked thumbnail, pulled back inside the bar when that is near its right edge.
        const bar = (root.offsetParent || root.parentNode)?.getBoundingClientRect?.(), at = anchor.getBoundingClientRect?.();
        if (bar && at) menu.style.left = Math.min(Math.max(0, at.left - bar.left), Math.max(0, bar.width - Math.min(menu.offsetWidth || 260, 260) - 4)) + 'px';
        else menu.style.left = Math.max(0, anchor.offsetLeft || 0) + 'px';
        entries[0]?.focus();
    }
    // Every button of the open menu, in order, for the arrow keys.
    const item = (text, run, role = 'menuitem') => {
        const b = el('button', null, text); b.type = 'button'; b.setAttribute('role', role);
        b.onclick = () => choose(run); entries.push(b); return b;
    };
    const thumb = (file, cls) => {
        const holder = el('span', cls);
        if (isImage(file)) { const image = el('img'); image.alt = ''; image.decoding = 'async'; image.src = source.url(file); image.onerror = () => { image.hidden = true; }; holder.append(image); }
        else holder.textContent = /\.(wav|mp3|flac|ogg|m4a|aac|opus)$/i.test(file) ? t('Audio', '音频') : t('Video', '视频');
        return holder;
    };
    const write = rows => { try { source.write(rows); } catch (value) { error(value); } };
    // A picture the page cannot draw would only fail minutes later, in the engine; tiff is not drawn by every browser, so it goes on unchecked.
    const readable = async file => {
        if (!/\.(png|jpe?g|webp|bmp)$/i.test(file.name || '')) return true;
        if (typeof Image !== 'function') return true;
        const url = URL.createObjectURL(file), image = new Image();
        try { image.src = url; await image.decode(); return image.naturalWidth > 0 && image.naturalHeight > 0; }
        catch { return false; }
        finally { URL.revokeObjectURL(url); }
    };
    async function addFiles(files) {
        if (working) return;
        working = uploading = true; update();
        let skipped = false;
        try {
            for (const file of files) {
                if (full()) break;
                if (!await readable(file)) { skipped = true; continue; }
                const result = await source.upload(file);
                write([...source.rows(), {file: result.file, role: 'reference', enabled: true}]);
            }
            if (skipped && !disposed) notify(t('Could not read this picture. Please choose another one.', '无法读取这张图片，请换一张。'), ['Editor reference images', '剪辑器参考图']);
        } catch (value) { error(value); }
        finally { working = uploading = false; picker.value = ''; update(); }
    }
    async function generate() {
        if (working) return;
        working = true; update();
        try { await source.newImage({target: size(), t}); } catch (value) { error(value); }
        finally { working = false; update(); }
    }
    function addMenu() {
        entries = [];
        const rows = source.rows();
        const items = [item(t('Upload image', '上传图片'), () => picker.click())];
        if (source.canGenerateImage?.()) items.push(item(t('New image', '生成一张'), generate));
        // Pictures sent to the creative panel earlier and not used now.
        const spare = rows.map((row, index) => ({row, index})).filter(({row}) => row.enabled === false && isImage(row.file));
        if (spare.length) {
            const separator = el('div'); separator.setAttribute('role', 'separator');
            const grid = el('div', 'fv-ed-ref-spare');
            for (const {row, index} of spare) {
                const b = el('button'); b.type = 'button'; b.setAttribute('role', 'menuitem');
                b.title = (row.source || row.file).split('/').pop(); b.append(thumb(row.file, 'fv-ed-ref-thumb'));
                b.onclick = () => choose(() => { const latest = source.rows(); if (latest[index]?.file === row.file) { latest[index] = {...latest[index], enabled: true}; write(latest); } });
                grid.append(b); entries.push(b);
            }
            items.push(separator, grid);
        }
        return items;
    }
    function chipMenu(entry) {
        entries = [];
        const remove = () => item(t('Remove', '移除'), () => {
            const rows = source.rows();
            if (rows[entry.index]?.file === entry.row.file) { rows[entry.index] = {...rows[entry.index], enabled: false}; write(rows); }
        });
        if (continuing) return [remove()];
        const roles = ['reference', 'first', 'last'].map(role => {
            const b = item(label(role), () => {
                const rows = source.rows();
                // One first frame and one last frame: the earlier one goes back to being a reference.
                if (role !== 'reference') for (const other of rows) if (other.role === role && other.enabled !== false) other.role = 'reference';
                if (rows[entry.index]?.file === entry.row.file) rows[entry.index] = {...rows[entry.index], role};
                write(rows);
            }, 'menuitemradio');
            b.setAttribute('aria-checked', String((entry.row.role || 'reference') === role)); return b;
        });
        const separator = el('div'); separator.setAttribute('role', 'separator');
        return [...roles, separator, remove()];
    }
    // The line said on a click while a part is made; it goes away with the busy state it describes.
    const wait = () => { waited = true; notify(sentence(waitText())); };
    function update(isBusy = busy, visible = shown) {
        if (waited && busy && !isBusy) { waited = false; notify(''); }
        const was = continuing;
        busy = isBusy; shown = visible; continuing = shown === 'continues';
        root.hidden = !shown || !source.available();
        if ((root.hidden || busy || was !== continuing) && !menu.hidden) close(false);
        // The panel holds at most MAX pictures, a continuing part uses three: a full list greys the plus out rather than ignoring a click.
        const isFull = full();
        // While a part is being made the controls stay clickable and say why they do nothing.
        add.disabled = working;
        add.dataset.uploading = String(uploading);
        add.setAttribute('aria-disabled', String(busy || isFull));
        add.title = uploading ? t('Uploading reference image…', '正在上传参考图…') : busy ? waitText() : isFull ? fullText() : t('Reference image', '参考图');
        chips.replaceChildren(...used().map(entry => {
            const chip = el('button', 'fv-ed-ref-chip'); chip.type = 'button'; chip.disabled = working; chip.setAttribute('aria-disabled', String(busy));
            chip.dataset.role = entry.row.role || 'reference';
            chip.title = busy ? waitText() : label(entry.row.role); chip.setAttribute('aria-haspopup', 'menu');
            chip.setAttribute('aria-label', label(entry.row.role));
            chip.append(thumb(entry.row.file, 'fv-ed-ref-thumb'));
            chip.onclick = () => { if (busy) { wait(); return; } if (!menu.hidden && opener === chip) close(); else open(chip, chipMenu(entry)); };
            return chip;
        }));
    }
    add.onclick = () => {
        if (busy) { wait(); return; }
        if (full()) { notify(sentence(fullText())); return; }
        if (!menu.hidden && opener === add) close(); else open(add, addMenu()); };
    picker.onchange = () => addFiles(Array.from(picker.files));
    // Keys stay in the menu: Escape closes it only, and the arrows move through it.
    menu.onkeydown = event => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); return; }
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) { event.stopPropagation(); return; }
        if (!entries.length) return;
        event.preventDefault(); event.stopPropagation();
        const at = entries.indexOf(document.activeElement), step = event.key === 'ArrowDown' ? 1 : -1;
        entries[event.key === 'Home' ? 0 : event.key === 'End' ? entries.length - 1 : at < 0 ? 0 : (at + step + entries.length) % entries.length].focus();
    };
    const outside = event => { if (!menu.hidden && !root.contains(event.target)) close(false); };
    document.addEventListener('pointerdown', outside, true);
    const unsubscribe = source.subscribe(() => { if (!disposed) update(); });
    update();
    return {element: root, update, dispose() { disposed = true; unsubscribe(); document.removeEventListener('pointerdown', outside, true); }};
}
