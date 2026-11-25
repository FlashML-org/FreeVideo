// The characters shelf of the library room: characters, scenes and props side by side, grouped by kind, and the chosen
// one large on the right with its pictures, its voice and what can be done with it. The data and the edit dialog are
// the cast library's (cast_library.js); this file only lays them out in the room.
import { createCastLibrary } from './cast_library.js';
import { agentInterfaceState } from './agent_connect.js';
import { agentSend } from './agent_session.js';
import { uploadMedia } from './media_upload.js';
import { issueButton } from './issue_button.js';
import { createPlayBar } from './fv_player.js';
import { userMessage } from './user_message.js';
import { copyText } from './clipboard_text.js';

const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('./cast_room.css', import.meta.url).href; document.head.append(css);
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const button = (text, action, cls = '') => { const b = el('button', text, cls); b.type = 'button'; b.onclick = action; return b; };
const tip = (element, name, use = '', key = '') => { element.dataset.fvTipName = name; if (use) element.dataset.fvTipUse = use; if (key) element.dataset.fvTipKey = key; return element; };
const LINE = {
    grid: '<rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="9" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="2.5" y="9" width="4.5" height="4.5" rx="1"/><rect x="9" y="9" width="4.5" height="4.5" rx="1"/>',
    person: '<circle cx="8" cy="5.5" r="2.5"/><path d="M3 13.5a5 5 0 0 1 10 0"/>',
    scene: '<path d="m1.5 13 4-6 3 4 2-2.5 4 4.5Z"/><circle cx="11.5" cy="4.5" r="1.3"/>',
    prop: '<path d="M8 1.5 14 5v6l-6 3.5L2 11V5Z"/><path d="M2 5l6 3.5L14 5M8 8.5v6"/>',
    voice: '<path d="M8 2.5v11M5 5.5v5M11 5.5v5M2.5 7v2M13.5 7v2"/>',
    search: '<circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/>',
    plus: '<path d="M8 3v10M3 8h10"/>', sort: '<path d="M5 2.5v11m0 0-2.5-2.5M5 13.5 7.5 11M11 13.5v-11m0 0L8.5 5M11 2.5 13.5 5"/>',
    at: '<circle cx="8" cy="8" r="2.5"/><path d="M10.5 8v1a2 2 0 0 0 4 0V8a6.5 6.5 0 1 0-2.5 5.1"/>',
    edit: '<path d="m10.5 2.5 3 3-8 8h-3v-3Z"/>', trash: '<path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.1a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.6-8.1M7 7v4M9 7v4"/>',
    left: '<path d="m10 3-5 5 5 5"/>', check: '<path d="m3.5 8.5 3 3 6-7"/>',
};
const line = name => {
    const span = el('span', null, 'fv-icon fv-lib-icon'); span.setAttribute('aria-hidden', 'true');
    span.innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${LINE[name]}</svg>`;
    return span;
};
const KINDS = ['character', 'setting', 'prop'];
const fileOf = media => typeof media === 'string' ? media : media?.file || '';

export function createCastRoom({api, t, viewURL: outputURL}) {
    const inputURL = (file, type = 'input') => {
        const split = file.lastIndexOf('/');
        return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(split + 1), subfolder: split < 0 ? '' : file.slice(0, split), type}));
    };
    void outputURL;
    const uploadOne = (file, onProgress = null) => uploadMedia(api, file, {onProgress});
    const libraries = Object.fromEntries(KINDS.map(kind => [kind, createCastLibrary({api, text: t, viewURL: inputURL, uploadOne, kind})]));
    const NAMES = {character: t('Characters', '人物'), setting: t('Scenes', '场景'), prop: t('Props', '道具')};
    const ONE = {character: t('Character', '人物'), setting: t('Scene', '场景'), prop: t('Prop', '道具')};
    const element = el('div', null, 'fv-library-body fv-cast-room'); element.hidden = true;
    let place = 'all', query = '', sort = 'new', selected = null, card = null, view = 0, state = 'loading', disposed = false;

    // Places
    const side = el('nav', null, 'fv-library-side'); side.setAttribute('aria-label', t('Characters', '角色'));
    const places = el('div', null, 'fv-library-side-group'); places.append(el('div', t('Characters', '角色'), 'fv-library-side-label'));
    const placeButton = (key, name, glyph, use) => {
        const item = button('', () => { place = key; render(); }, 'fv-library-place'); item.dataset.place = key;
        item.append(line(glyph), el('span', name, 'fv-library-place-name'), el('span', '', 'fv-library-count'));
        item.setAttribute('aria-label', name); tip(item, name, use); places.append(item); return item;
    };
    placeButton('all', t('All', '全部'), 'grid', t('Every character, scene and prop', '全部角色、场景和道具'));
    placeButton('character', NAMES.character, 'person', t('People to keep the same across shots', '在多个镜头里保持一致的人物'));
    placeButton('setting', NAMES.setting, 'scene', t('Places to reuse', '可重复使用的场景'));
    placeButton('prop', NAMES.prop, 'prop', t('Objects to reuse', '可重复使用的道具'));
    const voices = el('div', null, 'fv-library-side-group'); voices.append(el('div', t('Voice', '声音'), 'fv-library-side-label'));
    const voicePlace = button('', () => { place = 'voice'; render(); }, 'fv-library-place'); voicePlace.dataset.place = 'voice';
    voicePlace.append(line('voice'), el('span', t('With a voice', '有声音的角色'), 'fv-library-place-name'), el('span', '', 'fv-library-count'));
    tip(voicePlace, t('With a voice', '有声音的角色'), t('Characters that keep their own voice', '带有专属声音的人物'));
    voices.append(voicePlace);
    side.append(places, voices);

    // Shelf
    const collection = el('section', null, 'fv-library-collection fv-cast-collection'); collection.setAttribute('aria-label', t('Characters', '角色'));
    const toolbar = el('div', null, 'fv-library-toolbar');
    const searchBox = el('label', null, 'fv-library-search');
    const search = el('input'); search.type = 'search'; search.placeholder = t('Search names', '搜索名称'); search.setAttribute('aria-label', t('Search characters', '搜索角色'));
    tip(search, t('Search', '搜索'), t('Find a character, scene or prop by name', '按名称查找角色、场景或道具'), '/');
    search.oninput = () => { query = search.value.trim().toLowerCase(); render(); };
    search.onkeydown = event => { if (event.key === 'Escape' && search.value) { event.preventDefault(); event.stopPropagation(); search.value = ''; search.oninput(); } };
    searchBox.append(line('search'), search);
    const create = button('', () => { const kind = KINDS.includes(place) ? place : 'character'; libraries[kind].openNew(); }, 'fv-library-tool fv-cast-new');
    create.append(line('plus'), el('span', t('New', '新建'), 'fv-library-tool-label'));
    tip(create, t('New', '新建'), t('Add a character, scene or prop from pictures', '用图片新建角色、场景或道具'));
    const sortButton = button('', () => { sort = sort === 'new' ? 'name' : 'new'; syncSort(); render(); }, 'fv-library-tool fv-cast-sort');
    sortButton.append(line('sort'), el('span', '', 'fv-library-tool-label'));
    const syncSort = () => {
        sortButton.lastElementChild.textContent = sort === 'new' ? t('Newest first', '最新在前') : t('By name', '按名称');
        tip(sortButton, t('Sort', '排序'), sort === 'new' ? t('Switch to sorting by name', '改为按名称排列') : t('Switch to newest first', '改为最新在前'));
    };
    syncSort();
    toolbar.append(searchBox, create, sortButton);
    const shelf = el('div', null, 'fv-cast-shelf');
    const message = el('div', null, 'fv-library-message'); message.setAttribute('role', 'status');
    collection.append(toolbar, shelf, message);

    // Detail
    const detail = el('section', null, 'fv-library-detail fv-cast-detail'); detail.dataset.empty = 'true';
    const back = button('', () => { element.dataset.detail = 'false'; }, 'fv-quiet fv-library-all'); back.append(line('left'), el('span', t('All characters', '全部角色')));
    tip(back, t('All characters', '全部角色'), t('Back to the shelf', '回到角色列表'), 'Esc');
    const frame = el('div', null, 'fv-cast-frame sgf-avoid');
    const viewsRow = el('div', null, 'fv-cast-views-row');
    const title = el('div', null, 'fv-cast-title');
    const nameLine = el('strong'), metaLine = el('span'); title.append(nameLine, metaLine);
    const actions = el('div', null, 'fv-actions fv-cast-actions');
    const use = button('', () => copyMention(), 'fv-action fv-action-primary'); use.append(line('at'), el('span'));
    const edit = button('', () => card && libraries[card.kind]?.openEdit(card.id), 'fv-action fv-action-secondary'); edit.append(line('edit'), el('span', t('Edit', '编辑')));
    tip(edit, t('Edit', '编辑'), t('Change the name or pictures', '修改名字或图片'));
    let arm = 0;
    const remove = button('', () => removeCard(), 'fv-action fv-action-secondary fv-cast-remove-card'); remove.append(line('trash'), el('span', t('Delete', '删除')));
    tip(remove, t('Delete', '删除'), t('Delete it from the library', '从角色库里删除'));
    actions.append(use, edit, remove);
    const hint = el('p', null, 'fv-cast-hint');
    const voiceBox = el('div', null, 'fv-cast-voice');
    const notice = el('p', null, 'fv-library-message fv-library-notice'); notice.setAttribute('role', 'status');
    const empty = el('div', null, 'fv-library-detail-empty'); empty.append(line('person'), el('span', t('Choose one to see it here', '选一个角色，在这里查看')));
    detail.append(back, frame, viewsRow, title, actions, hint, voiceBox, notice, empty);
    element.append(side, collection, detail);

    const all = () => KINDS.flatMap(kind => libraries[kind].list().map(row => ({...row, kind: row.kind || kind})));
    const matchesPlace = row => place === 'all' || place === row.kind || (place === 'voice' && row.voice);
    function render() {
        if (disposed) return;
        const rows = all();
        for (const item of side.querySelectorAll('[data-place]')) {
            const key = item.dataset.place;
            item.setAttribute('aria-current', String(key === place ? 'page' : false));
            item.querySelector('.fv-library-count').textContent = String(rows.filter(row => key === 'all' || key === row.kind || (key === 'voice' && row.voice)).length || '');
        }
        voices.hidden = !rows.some(row => row.voice);
        shelf.replaceChildren();
        const shown = rows.filter(matchesPlace).filter(row => !query || String(row.name || '').toLowerCase().includes(query));
        const ordered = sort === 'name' ? [...shown].sort((a, b) => String(a.name).localeCompare(String(b.name), t('en', 'zh-CN'))) : shown;
        for (const kind of KINDS) {
            const group = ordered.filter(row => row.kind === kind);
            if (!group.length) continue;
            const heading = el('div', null, 'fv-library-day fv-cast-group'); heading.append(el('span', NAMES[kind], 'fv-library-day-name'), el('span', String(group.length), 'fv-library-day-count'));
            const tiles = el('div', null, 'fv-cast-tiles'); tiles.dataset.kind = kind;
            for (const row of group) tiles.append(tile(row));
            shelf.append(heading, tiles);
        }
        message.replaceChildren();
        collection.dataset.empty = String(!ordered.length);
        if (state === 'off') message.textContent = t('The assistant features are turned off on this computer, so the character library is unavailable. Turn that setting back on and restart FreeVideo to use it.', '这台电脑关闭了助手功能，角色库因此不可用。重新打开该设置并重启 FreeVideo 后可以使用。');
        else if (state === 'error') { message.textContent = t('The character library could not be read. Try Refresh.', '角色库暂时无法读取，请点击刷新。'); message.append(issueButton(t, () => ({where: ['Character library', '角色库'], message: message.firstChild?.textContent || ''}))); }
        else if (state === 'loading' && !rows.length) message.textContent = t('Loading…', '正在读取…');
        else if (!ordered.length) message.textContent = query ? t('No name matches this search.', '没有符合搜索的名称。')
            : t('Saved characters, scenes and props appear here. Press New to add one from pictures.', '保存的角色、场景和道具会出现在这里。点“新建”用图片添加。');
        create.disabled = state === 'off';
        if (selected && !rows.some(row => row.id === selected)) clearDetail();
        else if (!selected && ordered.length && !matchMedia('(max-width: 1180px)').matches) choose(ordered[0], false);
        for (const node of shelf.querySelectorAll('.fv-cast-tile')) node.setAttribute('aria-pressed', String(node.dataset.id === selected));
    }
    function tile(row) {
        const node = button('', () => choose(row), 'fv-cast-tile'); node.dataset.id = row.id;
        const picture = el('div', null, 'fv-cast-picture');
        if (row.thumbnail?.file) { const image = el('img'); image.alt = ''; image.loading = 'lazy'; image.src = inputURL(row.thumbnail.file); image.onerror = () => { image.hidden = true; }; picture.append(image); }
        else picture.append(el('span', String(row.name || '?').slice(0, 1), 'fv-cast-initial'));
        if (row.voice) { const mark = el('span', null, 'fv-cast-voice-mark'); mark.append(line('voice')); picture.append(mark); }
        node.append(picture, el('span', row.name || ONE[row.kind]), el('small', ONE[row.kind]));
        node.setAttribute('aria-label', row.name || ONE[row.kind]);
        tip(node, row.name || ONE[row.kind], t('Show it on the right', '在右侧查看'));
        return node;
    }
    function clearDetail() { selected = null; card = null; detail.dataset.empty = 'true'; frame.replaceChildren(); viewsRow.replaceChildren(); voiceBox.replaceChildren(); }
    async function choose(row, reveal = true) {
        selected = row.id; view = 0; detail.dataset.empty = 'false'; notice.textContent = ''; resetRemove();
        for (const node of shelf.querySelectorAll('.fv-cast-tile')) node.setAttribute('aria-pressed', String(node.dataset.id === selected));
        card = {...row, images: row.thumbnail ? [{media: row.thumbnail, label: 'front'}] : []};
        paint();
        if (reveal) element.dataset.detail = 'true';
        try {
            const full = await libraries[row.kind].get(row.id);
            if (disposed || selected !== row.id) return;
            card = {...full, kind: full.kind || row.kind}; paint();
        } catch (error) { if (selected === row.id) notice.textContent = userMessage(error, t, t('Could not open it. Try again.', '没能打开，请重试。')); }
    }
    function paint() {
        if (!card) return;
        const images = card.images || [];
        frame.dataset.kind = card.kind; frame.replaceChildren();
        const shown = images[view] || images[0];
        if (shown) { const image = el('img'); image.alt = ''; image.src = inputURL(fileOf(shown.media)); frame.append(image); }
        viewsRow.replaceChildren();
        if (images.length > 1) images.forEach((picture, index) => {
            const thumb = button('', () => { view = index; paint(); }, 'fv-cast-view-thumb'); thumb.setAttribute('aria-pressed', String(index === view));
            const image = el('img'); image.alt = ''; image.src = inputURL(fileOf(picture.media)); thumb.append(image);
            const label = picture.label === 'front' ? t('Front', '正面') : t(`Picture ${index + 1}`, `图片 ${index + 1}`);
            thumb.setAttribute('aria-label', label); tip(thumb, label, t('Show this picture large', '放大显示这张图片'));
            viewsRow.append(thumb);
        });
        nameLine.textContent = card.name || ONE[card.kind];
        metaLine.textContent = [ONE[card.kind], images.length ? t(`${images.length} ${images.length === 1 ? 'picture' : 'pictures'}`, `${images.length} 张图`) : ''].filter(Boolean).join(' · ');
        use.lastElementChild.textContent = t(`Copy @${card.name}`, `复制 @${card.name}`);
        tip(use, t('Copy mention', '复制引用'), t('Paste it in a prompt to use this one', '粘贴到提示词里即可引用'));
        hint.textContent = t(`Type @${card.name} in a prompt to use it in a shot.`, `在提示词中输入 @${card.name} 即可在镜头里使用。`);
        voiceBox.replaceChildren();
        if (card.voice?.clip?.file || card.voice?.clip?.url) {
            voiceBox.append(el('div', t('Voice', '声音'), 'fv-cast-voice-label'));
            const audio = el('audio'); audio.preload = 'none';
            audio.src = card.voice.clip.url ? api.apiURL(card.voice.clip.url) : inputURL(card.voice.clip.file);
            voiceBox.append(audio, createPlayBar(audio, t).bar);
        }
    }
    async function copyMention() {
        if (!card) return;
        const text = '@' + card.name + ' ';
        const ok = await copyText(text);
        notice.textContent = ok ? t(`Copied @${card.name}. Paste it in a prompt.`, `已复制 @${card.name}，粘贴到提示词里使用。`) : t('Could not copy. Type the name after @ in a prompt.', '没能复制。可在提示词中输入 @ 加名字。');
    }
    function resetRemove() { clearTimeout(arm); arm = 0; remove.classList.remove('fv-library-arming'); remove.lastElementChild.textContent = t('Delete', '删除'); }
    async function removeCard() {
        if (!card || card.revision === undefined) return;
        if (!arm) { remove.classList.add('fv-library-arming'); remove.lastElementChild.textContent = t('Confirm delete', '确认删除'); arm = setTimeout(resetRemove, 3000); return; }
        resetRemove(); remove.disabled = true;
        try {
            const response = await agentSend(api, 'DELETE', '/freevideo/agent/cast/' + encodeURIComponent(card.id), {}, {'If-Match': String(card.revision)});
            if (!response.ok) {
                const data = await response.json().catch(() => null);
                throw new Error(response.status === 409 ? t('Changed elsewhere. Refresh and try again.', '已在别处修改，请刷新后重试。') : data?.error?.message || t('Could not delete. Try again.', '没能删除，请重试。'));
            }
            window.dispatchEvent(new CustomEvent('freevideo-cast-changed'));
            clearDetail(); await refresh();
        } catch (error) {
            notice.textContent = userMessage(error, t, t('Could not delete. Try again.', '没能删除，请重试。'));
            notice.append(issueButton(t, () => ({where: ['Character library', '角色库'], message: error.message})));
        } finally { remove.disabled = false; }
    }
    const offs = KINDS.map(kind => libraries[kind].subscribe(() => render()));
    async function refresh() {
        state = (await agentInterfaceState(api)) === 'off' ? 'off' : 'loading'; render();
        if (state === 'off') return;
        try { await Promise.all(KINDS.map(kind => libraries[kind].refresh())); state = 'ready'; }
        catch { state = 'error'; }
        render();
    }
    element.addEventListener('keydown', event => {
        if (event.key === '/' && !event.target.closest('input,textarea')) { event.preventDefault(); search.focus(); }
    });
    return {
        element, refresh,
        // Escape: back from the detail on a narrow window first.
        cancel() { if (element.dataset.detail === 'true' && matchMedia('(max-width: 1180px)').matches) { element.dataset.detail = 'false'; return true; } return false; },
        destroy() { disposed = true; clearTimeout(arm); for (const off of offs) off(); for (const kind of KINDS) libraries[kind].destroy(); element.remove(); },
    };
}
