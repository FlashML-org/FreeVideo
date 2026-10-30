import {agentGet, agentPost, agentSend} from './agent_session.js';
import {agentInterfaceEnabled} from './agent_connect.js';
import {issueButton} from './issue_button.js';
const CAST_ISSUE = ['Character library', '角色库'];

// The optional generator owns its UI and job lifecycle; keep its contract here.
async function mountGenerator(options) {
    try {
        const {mountCastGenerate, castGenerateSupported} = await import('./cast_generate.js');
        if (typeof mountCastGenerate !== 'function' || (castGenerateSupported && !castGenerateSupported())) return null;
        return await mountCastGenerate(options);
    } catch { return null; }
}

const el = (tag, className, value) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
};
const button = (className, label, action) => {
    const node = el('button', className, label); node.type = 'button'; node.onclick = action; return node;
};
const path = id => '/freevideo/agent/cast/' + encodeURIComponent(id);
// MAX_CAST_IMAGES and MAX_CAST_CAPTION in agent/schema.py.
const MAX_PICTURES = 24, MAX_CAPTION = 200;

export function createCastLibrary({api, text, viewURL, uploadOne, onChange = () => {}}) {
    if (!document.querySelector('link[data-freevideo-cast]')) {
        const css = el('link'); css.rel = 'stylesheet'; css.dataset.freevideoCast = '';
        css.href = new URL('./cast_library.css', import.meta.url).href; document.head.append(css);
    }
    let rows = [], unreadable = 0, mentioned = new Set(), disposed = false, request = 0, current = null, broadcasting = false;
    const subscribers = new Set();
    const element = el('div', 'fv-cast-library');
    // The row shows, and the page session is asked for, only once /freevideo/agent says the interface is on.
    let available = false; element.hidden = true;
    const probe = agentInterfaceEnabled(api).then(on => { available = on && !disposed; element.hidden = !available; return available; });
    const heading = el('span', 'fv-cast-heading', text('Characters', '角色'));
    const avatars = el('div', 'fv-cast-avatars');
    const skipped = el('div', 'fv-cast-unreadable'); skipped.hidden = true;
    const add = button('fv-cast-add', '+', () => openDialog());
    add.setAttribute('aria-label', text('New character', '新建角色'));
    element.append(heading, avatars, add, skipped);

    async function value(response) {
        const data = await response.json().catch(() => null);
        if (!response.ok) {
            // The dialog only sends pictures, so unreadable media here is always one of them.
            const unreadable = data?.error?.code === 'invalid_media' && text('Could not read this picture. Please choose another one.', '无法读取这张图片，请换一张。');
            const error = new Error(unreadable || data?.error?.message || (typeof data?.error === 'string' && data.error)
                || data?.message || response.statusText || text('Did not finish. Try again.', '没有完成，请重试。'));
            error.status = response.status;
            // Which picture it was, as /images/<n>/media of the request.
            if (unreadable) error.field = data.error.details?.find(detail => typeof detail?.field === 'string')?.field;
            throw error;
        }
        return data;
    }
    // The front picture always comes first: the @ popup and the edit dialog both rely on it.
    const get = async id => {
        const card = await value(await agentGet(api, path(id)));
        const images = card.images || [], front = images.find(image => image.label === 'front');
        return {...card, images: front ? [front, ...images.filter(image => image !== front)] : images};
    };
    function render() {
        avatars.replaceChildren(); heading.hidden = !rows.length;
        skipped.textContent = unreadable > 0 ? text(`${unreadable} ${unreadable === 1 ? 'character' : 'characters'} could not be read`, `另有 ${unreadable} 个角色无法读取`) : '';
        skipped.hidden = !unreadable;
        for (const row of rows) {
            const avatar = button('fv-cast-avatar', '', () => openDialog(row.id));
            avatar.classList.toggle('fv-cast-mentioned', mentioned.has(row.id));
            avatar.title = row.name; avatar.setAttribute('aria-label', row.name);
            const image = row.thumbnail?.file ? el('img') : el('span', 'fv-cast-initial', row.name.slice(0, 1));
            if (row.thumbnail?.file) { image.src = viewURL(row.thumbnail.file); image.alt = ''; image.loading = 'lazy'; }
            avatar.append(image); avatars.append(avatar);
        }
    }
    async function refresh() {
        if (disposed || !await probe) return rows;
        const serial = ++request;
        const data = await value(await agentGet(api, '/freevideo/agent/cast?kind=character&limit=200'));
        if (!disposed && serial === request) {
            rows = data.cards; unreadable = Number.isSafeInteger(data.unreadable) && data.unreadable > 0 ? data.unreadable : 0;
            render(); onChange(rows);
            for (const fn of subscribers) fn(rows);
        }
        return rows;
    }
    const changed = () => {
        if (!broadcasting) refresh().catch(error => current?.message(error.message));
    };
    async function saved() {
        broadcasting = true;
        try { window.dispatchEvent(new CustomEvent('freevideo-cast-changed')); }
        finally { broadcasting = false; }
        await refresh();
    }

    function openDialog(id = null) {
        if (disposed || !available) return;
        current?.close();
        let closed = false, loading = !!id, saving = false, revision, active = 'upload';
        let generator = null, aiState = {busy: false, done: false}, deleteTimer = null, confirming = false, frontless = false, mirrored = '', savedDescription = null;
        // slots[0] is the front; the other pictures follow it without gaps.
        const slots = [null], slotUI = [];
        const dialog = el('dialog', 'fv-cast-dialog');
        const top = el('div', 'fv-cast-top');
        const title = el('h2', '', id ? text('Edit character', '编辑角色') : text('New character', '新建角色'));
        dialog.setAttribute('aria-label', title.textContent);
        const closeButton = button('fv-cast-close', '×', () => close());
        closeButton.setAttribute('aria-label', text('Close', '关闭')); top.append(title, closeButton);
        const name = el('input', 'fv-cast-name'); name.type = 'text'; name.maxLength = 120;
        name.placeholder = text('Name', '名字'); name.setAttribute('aria-label', text('Name', '名字'));
        name.oninput = () => sync();
        name.onkeydown = event => { if (event.key === 'Enter') event.preventDefault(); };
        const description = el('textarea', 'fv-cast-description'); description.rows = 2; description.maxLength = 1000;
        description.placeholder = text('Description (optional): details the pictures may not show, e.g. a dragon tattoo on the back, a scar over the left eyebrow',
            '描述（可选）：图片里可能看不到的细节，例如背上的龙纹身、左眉上的疤');
        description.setAttribute('aria-label', text('Description', '描述'));
        const tabs = el('div', 'fv-cast-tabs'); tabs.setAttribute('role', 'tablist');
        tabs.setAttribute('aria-label', text('Images', '图片'));
        const uploadTab = button('', text('Upload images', '上传图片'), () => select('upload'));
        const aiTab = button('', text('Generate with AI', 'AI 生成'), () => select('ai')); aiTab.hidden = tabs.hidden = true;
        for (const tab of [uploadTab, aiTab]) tab.setAttribute('role', 'tab');
        // Tab panels are dialog-local, so multiple material panels can coexist.
        const uploadPane = el('div', 'fv-cast-upload'), views = el('div', 'fv-cast-views');
        const aiPane = el('div', 'fv-cast-generate'); aiPane.hidden = true;
        const status = el('div', 'fv-cast-message'); status.setAttribute('role', 'status'); status.hidden = true;
        const actions = el('div', 'fv-cast-actions');
        const remove = button('fv-cast-delete', text('Delete character', '删除角色'), () => deleteCard()); remove.hidden = !id;
        const cancel = button('', text('Cancel', '取消'), () => close());
        const save = button('fv-cast-save', text('Save', '保存'), () => saveCard());
        actions.append(remove, cancel, save);
        const issue = issueButton(text, () => ({where: CAST_ISSUE, message: status.textContent})); issue.hidden = true;
        dialog.append(top, name, description, tabs, uploadPane, aiPane, status, issue, actions); tabs.append(uploadTab, aiTab);

        // A refresh notice is plain secondary text; only failures are shown as errors.
        const message = (content, notice = false) => {
            if (closed) return;
            status.textContent = content; status.hidden = !content; status.classList.toggle('fv-cast-notice', notice);
            issue.hidden = !content || notice;
        };
        const aiBusy = () => aiState.busy || !!generator?.isBusy();
        // What is still missing before Save works ('' when nothing is); the button says so on hover and when pressed.
        let missing = '';
        const missingNow = () => !name.value.trim() ? text('Enter a name first', '先填写名字')
            : aiBusy() ? text('Wait for the five pictures to finish', '等待五张图生成完成')
            : active === 'upload' ? (frontless ? slots.some(Boolean) : slots[0]) ? '' : frontless ? text('Add an image first', '先添加图片') : text('Add a front image first', '先添加正面图片')
            : aiState.done ? '' : text('Generate the images first', '先生成角色图片');
        function sync() {
            const busy = loading || saving;
            name.disabled = description.disabled = busy; remove.disabled = busy;
            uploadTab.disabled = aiTab.disabled = busy;
            const before = missing;
            missing = busy ? '' : missingNow();
            save.disabled = busy;
            if (missing) { save.setAttribute('aria-disabled', 'true'); save.title = missing; }
            else { save.removeAttribute('aria-disabled'); save.removeAttribute('title'); }
            if (before && before !== missing && status.textContent === before) message('');
            for (let index = 0; index < slotUI.length; index++) {
                slotUI[index].choose.disabled = busy; slotUI[index].remove.disabled = busy; slotUI[index].caption.disabled = busy;
            }
        }
        // A refused picture is named on its own tile; choosing another picture there, or saving again, clears it.
        // A tile too narrow for the words keeps only its red border, and the words go on the line under the pictures.
        let markedBelow = -1;
        function mark(index, content = '') {
            const {slot, frame, problem} = slotUI[index];
            // Clear the line only while it still holds this tile's words, not a later message.
            if (markedBelow === index) { markedBelow = -1; if (status.textContent === problem.dataset.message) message(''); }
            problem.dataset.message = content;
            // One line per clause, so "这张图片" is never split across lines.
            problem.replaceChildren(...content.replace(/([，。]|\. )/g, '$1\n').split('\n').filter(Boolean).map(part => el('span', 'fv-cast-slot-line', part)));
            if (content) problem.append(issueButton(text, () => ({where: CAST_ISSUE, message: content})));
            problem.hidden = !content; slot.classList.toggle('fv-cast-bad', !!content);
            const width = frame.clientWidth;
            if (content && width > 0 && (width < 100 || problem.scrollWidth > problem.clientWidth || problem.offsetHeight > frame.clientHeight / 2)) {
                problem.hidden = true; markedBelow = index; message(content);
            }
        }
        function select(tab) {
            active = tab;
            uploadPane.hidden = tab !== 'upload'; aiPane.hidden = tab !== 'ai';
            for (const [key, node] of [['upload', uploadTab], ['ai', aiTab]]) {
                node.setAttribute('aria-selected', String(key === tab)); node.tabIndex = key === tab ? 0 : -1;
            }
            sync();
        }
        tabs.onkeydown = event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || aiTab.hidden || saving || loading) return;
            event.preventDefault();
            select(event.key === 'Home' ? 'upload' : event.key === 'End' ? 'ai' : active === 'upload' ? 'ai' : 'upload');
            (active === 'upload' ? uploadTab : aiTab).focus();
        };
        const revoke = picture => { if (picture?.preview) URL.revokeObjectURL(picture.preview); };
        function paint(index) {
            const {choose, remove, caption} = slotUI[index], picture = slots[index];
            caption.hidden = !picture; caption.value = picture?.caption || '';
            caption.setAttribute('aria-label', text(`What image ${index + 1} shows`, `图片 ${index + 1} 的内容`));
            choose.replaceChildren(); let image = null;
            choose.setAttribute('aria-label', !index ? text('Front image', '正面图片')
                : picture ? text(`Image ${index + 1}`, `图片 ${index + 1}`) : text('Add images', '添加图片'));
            if (picture) {
                image = el('img'); image.alt = '';
                // The remove button waits for the picture, so a slot still loading never looks like a filled one.
                const shown = () => { if (slots[index] === picture) remove.hidden = false; };
                image.onload = image.onerror = shown;
                image.src = picture.preview || viewURL(typeof picture.media === 'string' ? picture.media : picture.media.file);
                choose.append(image);
            } else {
                choose.append(el('span', 'fv-cast-slot-plus', '+'));
                choose.append(el('span', '', index ? text('Add images', '添加图片') : text('Front image', '正面图片')));
            }
            remove.hidden = true; if (image?.complete) remove.hidden = false; mark(index); sync();
        }
        // A tile for each picture and one empty tile after them, until the card is full.
        function layout() {
            const wanted = Math.min(slots.length + 1, MAX_PICTURES);
            while (slotUI.length > wanted) slotUI.pop().slot.remove();
            while (slotUI.length < wanted) addSlot();
        }
        // The first file goes on the tile it was given to; the others go after the last picture.
        function chooseFiles(index, files) {
            if (loading || saving || closed) return;
            const changed = [];
            for (const file of Array.from(files)) {
                if (index >= MAX_PICTURES) break;
                revoke(slots[index]);
                slots[index] = {file, label: index ? 'other' : 'front', preview: URL.createObjectURL(file)};
                changed.push(index); index = slots.length;
            }
            layout(); changed.forEach(at => paint(at)); message('');
        }
        function drop(index) {
            revoke(slots[index]);
            if (!index) { slots[0] = null; paint(0); return; }
            // A refused picture keeps its mark when the pictures after the removed one move up.
            const marks = slotUI.map(({problem}) => problem.dataset.message || '');
            if (markedBelow >= index) mark(markedBelow);
            slots.splice(index, 1); layout();
            for (let at = index; at < slotUI.length; at++) { paint(at); if (marks[at + 1]) mark(at, marks[at + 1]); }
        }
        function addSlot() {
            const index = slotUI.length;
            const slot = el('div', 'fv-cast-slot' + (index ? '' : ' fv-cast-front')), frame = el('div', 'fv-cast-frame');
            const input = el('input'); input.type = 'file'; input.accept = 'image/*'; input.multiple = true; input.hidden = true;
            const choose = button('fv-cast-choose', '', () => input.click());
            const remove = button('fv-cast-remove', '×', () => drop(index));
            remove.setAttribute('aria-label', text('Remove image', '移除图片'));
            const caption = el('input', 'fv-cast-caption'); caption.type = 'text'; caption.maxLength = MAX_CAPTION;
            caption.placeholder = text('What it shows', '图中内容');
            caption.title = text('Goes into the prompt with this image, e.g. "dragon tattoo on her back"', '会和这张图一起写进提示词，例如“背上的龙纹身”');
            caption.oninput = () => { if (slots[index]) slots[index].caption = caption.value; };
            caption.onkeydown = event => { if (event.key === 'Enter') event.preventDefault(); };
            input.onchange = () => { chooseFiles(index, input.files); input.value = ''; };
            slot.ondragover = event => { event.preventDefault(); event.stopPropagation(); };
            slot.ondrop = event => { event.preventDefault(); event.stopPropagation(); chooseFiles(index, event.dataTransfer.files); };
            const problem = el('div', 'fv-cast-slot-error'); problem.setAttribute('role', 'alert'); problem.hidden = true;
            // The remove button comes after the text so it stays above it on a narrow tile.
            frame.append(choose, problem, remove); slot.append(frame, caption, input);
            slotUI.push({slot, frame, choose, remove, problem, caption});
            (index ? views : uploadPane).append(slot); paint(index);
        }
        layout();
        uploadPane.append(views);
        function resetDelete() {
            clearTimeout(deleteTimer); deleteTimer = null; confirming = false;
            remove.textContent = text('Delete character', '删除角色'); remove.classList.remove('fv-cast-confirm');
        }
        async function load() {
            loading = true; sync();
            try {
                const card = await get(id);
                if (closed) return;
                revision = card.revision; name.value = card.name; mirrored = '';
                savedDescription = card.description ?? null; description.value = savedDescription || '';
                slots.forEach(revoke);
                // A card may have no front picture (the API does not require one): all its pictures stay, in order, and
                // saving does not ask for a front; the front slot is only reserved while there is a front to keep first.
                frontless = card.images.length > 0 && card.images[0].label !== 'front';
                slots.splice(0, slots.length, ...card.images.slice(0, MAX_PICTURES).map(picture => ({...picture})));
                if (!slots.length) slots.push(null);
                layout(); slotUI.forEach((_, index) => paint(index));
                generator?.cancel(); aiState = {busy: false, done: false}; select('upload'); resetDelete();
            } finally { loading = false; if (!closed) sync(); }
        }
        async function failure(error) {
            if (closed) return;
            if (error.status === 409 && id) {
                try {
                    await load();
                    message(text('Changed elsewhere. Showing the latest version.', '已在别处修改，已显示最新内容'), true);
                } catch (error) { message(error.message); }
            } else message(error.message);
        }
        async function saveCard() {
            if (closed || save.disabled) return;
            if (missing) { message(missing, true); return; }
            saving = true; resetDelete(); message(''); slotUI.forEach((_, index) => mark(index)); sync();
            let pictures = [];
            try {
                pictures = active === 'upload' ? slots.filter(Boolean) : generator.getPictures().map((picture, index) => ({
                    ...(typeof picture.file === 'string' ? {media: picture.file} : {file: picture.file}),
                    label: picture.label || (index ? 'other' : 'front'),
                }));
                const frontNeeded = active === 'ai' || !frontless;
                if (!pictures.length || pictures.length > MAX_PICTURES || (frontNeeded && !pictures.some(picture => picture.label === 'front'))) {
                    message(text(`Choose one front image and up to ${MAX_PICTURES - 1} more images`, `选一张正面图片，再加最多 ${MAX_PICTURES - 1} 张其他图片`), true); return;
                }
                const images = [];
                for (const picture of pictures) {
                    if (closed) return;
                    // Cache uploads on the slot so retrying a refused save does not upload twice.
                    if (picture.file && !picture.media) picture.media = (await uploadOne(picture.file)).file;
                    images.push({media: picture.media, label: picture.label, caption: String(picture.caption || '').replace(/\s+/g, ' ').trim()});
                }
                if (closed) return;
                // An untouched description goes back exactly as the card has it; a typed one goes on one line.
                const about = description.value === (savedDescription || '') ? savedDescription
                    : description.value.replace(/\s+/g, ' ').trim() || null;
                const body = {name: name.value.trim(), description: about, images};
                await value(await (id ? agentSend(api, 'PATCH', path(id), {revision, ...body})
                    : agentPost(api, '/freevideo/agent/cast', {kind: 'character', ...body})));
                close(); await saved();
            } catch (error) {
                const at = /^\/images\/(\d+)\/media$/.exec(error.field || '');
                const index = at && active === 'upload' ? slots.indexOf(pictures[Number(at[1])]) : -1;
                if (index >= 0 && !closed) mark(index, error.message); else await failure(error);
            }
            finally { saving = false; if (!closed) sync(); }
        }
        async function deleteCard() {
            if (loading || saving || closed || revision === undefined) return;
            if (!confirming) {
                confirming = true; remove.textContent = text('Confirm delete', '确认删除'); remove.classList.add('fv-cast-confirm');
                deleteTimer = setTimeout(resetDelete, 3000); return;
            }
            saving = true; resetDelete(); message(''); sync();
            try {
                await value(await agentSend(api, 'DELETE', path(id), {}, {'If-Match': String(revision)}));
                close(); await saved();
            } catch (error) { await failure(error); }
            finally { saving = false; if (!closed) sync(); }
        }
        function close() {
            if (closed) return;
            closed = true; clearTimeout(deleteTimer);
            generator?.cancel(); generator?.destroy();
            slots.forEach(revoke);
            if (dialog.open) dialog.close(); dialog.remove();
            if (current?.element === dialog) current = null;
            add.focus();
        }
        // A click anywhere else in the dialog takes the second step back.
        dialog.onpointerdown = event => { if (confirming && event.target !== remove) resetDelete(); };
        dialog.oncancel = event => { event.preventDefault(); close(); };
        dialog.onclose = close;
        current = {element: dialog, message, close};
        name.value = ''; select('upload'); document.body.append(dialog); dialog.showModal(); name.focus();
        if (id) load().catch(error => message(error.message));
        mountGenerator({container: aiPane, api, text, viewURL, onState: state => {
            if (closed) return;
            aiState = state;
            // The words that made the AI pictures describe the character too, unless the box says something else.
            const words = state.description || '', typed = description.value.trim();
            if (words && (!typed || typed === mirrored)) description.value = mirrored = words;
            sync();
        }}).then(instance => {
            if (closed) { instance?.cancel(); instance?.destroy(); return; }
            generator = instance; aiTab.hidden = tabs.hidden = !instance; sync();
        });
    }

    // The @ popup reports which characters the prompt currently mentions; they get an accent ring.
    const mentions = event => {
        mentioned = new Set(Array.isArray(event.detail) ? event.detail : []); render();
    };
    window.addEventListener('freevideo-cast-mentioned', mentions);
    window.addEventListener('focus', changed);
    window.addEventListener('freevideo-cast-changed', changed);
    render(); changed();
    return {element, list: () => rows, get, refresh,
        subscribe(fn) { subscribers.add(fn); return () => subscribers.delete(fn); },
        openNew: () => openDialog(), openEdit: id => openDialog(id),
        destroy() {
            if (disposed) return;
            disposed = true; request++; current?.close(); subscribers.clear(); element.remove();
            window.removeEventListener('freevideo-cast-mentioned', mentions);
            window.removeEventListener('focus', changed);
            window.removeEventListener('freevideo-cast-changed', changed);
        }};
}
