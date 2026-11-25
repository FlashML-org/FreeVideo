import {editorIcon} from './editor_icons.js';
import {sourceKey} from './editor_store.js';
import {issueButton} from './issue_button.js';
import {connectionLostText} from './browser_extension.js';
import {tip} from './editor_tips.js';
import {setAttr, setData, setHidden} from './dom_quiet.js';
const LIBRARY_ISSUE = ['Editor media library', '剪辑器素材库'];
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const stamp = frames => { const seconds = Math.floor((frames || 0) / 24); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`; };
const FORMATS = 'MP4, MOV, WebM, MKV, M4V, WAV, MP3, FLAC, OGG, M4A, AAC, Opus';
// The first page of creations as last seen, by address only (id, file, thumbnail; never a prompt, name or date): the
// next opening draws those cards at once, with blank captions of the final height, and the server's list fills them.
// A work deleted anywhere on the page leaves this list at once.
const LIST_KEY = 'fv-editor-library';
let listCache = null;
const bare = row => ({id: row.id, video: row.video, ...(typeof row.thumbnail === 'string' ? {thumbnail: row.thumbnail} : {})});
function writeList() { try { localStorage.setItem(LIST_KEY, JSON.stringify(listCache.map(bare))); } catch {} }
function cachedList() {
    if (listCache) return listCache;
    try {
        const value = JSON.parse(localStorage.getItem(LIST_KEY) || 'null');
        if (Array.isArray(value)) {
            const rows = value.filter(row => row && typeof row.id === 'string' && typeof row.video === 'string');
            listCache = rows.map(row => ({...bare(row), cached: true}));
            // A list written by an earlier version (with prompts) is written again without them.
            if (rows.some(row => Object.keys(row).some(key => !['id', 'video', 'thumbnail'].includes(key)))) writeList();
            return listCache;
        }
    } catch {}
    return [];
}
function keepList(rows) { listCache = rows.slice(0, 24).map(bare); writeList(); }
globalThis.addEventListener?.('freevideo-library-deleted', event => {
    const video = event.detail?.video, rows = video ? cachedList() : [];
    if (rows.some(row => row.video === video)) { listCache = rows.filter(row => row.video !== video); writeList(); }
});
export function createEditorLibrary(host, {store, t, actions, state}) {
    const tabs = el('div', 'fv-ed-tabs fv-pass-mode'); tabs.setAttribute('role', 'tablist');
    const works = el('button', null, t('Creations', '作品')), imports = el('button', null, t('Imported', '已导入'));
    for (const tab of [works, imports]) { tab.type = 'button'; tab.setAttribute('role', 'tab'); tabs.append(tab); }
    const search = el('input', 'fv-ed-search'); search.type = 'search'; search.placeholder = t('Search', '搜索'); search.setAttribute('aria-label', search.placeholder);
    tip(search, {name: t('Search media', '搜索素材'), use: t('Find by name or description', '按名称和描述查找')});
    tip(works, {name: t('Creations', '作品'), use: t('Videos made here, ready to place', '在这里生成的视频，可直接放入')});
    tip(imports, {name: t('Imported', '已导入'), use: t('Files added from this computer', '从电脑加入的文件')});
    const searchTools = el('div', 'fv-ed-library-tools'), importButton = el('button', 'fv-ed-library-import', t('Import', '导入'));
    importButton.type = 'button'; searchTools.append(search, importButton);
    tip(importButton, {name: t('Import', '导入'), use: t('Add video or sound files from this computer', '从电脑加入视频或声音文件')});
    const grid = el('div', 'fv-ed-library-grid'); grid.setAttribute('role', 'tabpanel');
    const message = el('p', 'fv-ed-library-message'); message.setAttribute('role', 'status');
    const unreadable = el('p', 'fv-ed-unreadable');
    const file = el('input'); file.type = 'file'; file.multiple = true; file.hidden = true;
    file.accept = '.mp4,.mov,.webm,.mkv,.m4v,.wav,.mp3,.flac,.ogg,.m4a,.aac,.opus';
    importButton.onclick = () => file.click();
    host.append(tabs, searchTools, grid, message, unreadable, file);
    let tab = 'work', rows = cachedList(), imported = [], importUnread = 0, next = null, disposed = false, loading = false, listed = rows.length > 0;
    let listRead = Promise.resolve(), refreshTask = null, retryTimer = null, finishRetry = null, refreshAgain = false;
    const expectedOutputs = new Set();
    const uploading = new Map(), recentImports = new Map(), pendingAdds = new Map();
    const removal = new Map(); // import id → {users, busy, failed}: the clips whose deletion is being asked about
    let focusAfter = null; // {cancel: id} | {card: key} | {next: key}: where focus goes once the list is drawn again
    const preparing = () => t('Preparing this clip. It goes on the timeline when ready.', '素材准备好后会自动放到时间线上。');
    const cannotOpen = () => t('This file cannot be opened. Supported formats: ', '这个文件打不开。支持的格式：') + FORMATS;
    // Why an import did not work, and where to report it: only a file that could not be read lists the formats and asks for a report.
    const importFailure = (reason, status) => status === 413 ? [t('This file is over 8 GiB. Trim or compress it, then try again.', '文件超过 8 GiB，请先剪短或压缩后再导入。')]
        : reason === 'disk_full' ? [t('The disk is full. Free some space, then import again.', '磁盘空间不足，请清理出空间后再导入。')]
        : reason === 'import_failed' ? [connectionLostText(t)] : [cannotOpen(), LIBRARY_ISSUE];
    function metadata(row, kind) {
        const g = row.geometry || row, cached = store.sources.get(`${kind}:${row.id}`);
        return {frames: cached?.frames || Math.round(g.frames || (g.seconds || 0) * (g.fps || 24)), hasVideo: cached?.has_video ?? (kind === 'work' || row.kind === 'video'),
            hasAudio: cached?.has_audio ?? (kind === 'work' ? row.has_audio !== false : row.has_audio === true || row.kind === 'audio')};
    }
    // The "＋" puts a video after the last picture and a sound at the play position;
    // a double click leaves the place to the page, which appends.
    async function add(row, kind, plus = false, {queued = false, project = actions.projectToken?.()} = {}) {
        const source = {kind, id: row.id}, key = sourceKey(source), playhead = state?.playhead;
        await store.getSources([key]);
        if (disposed || project != null && project !== actions.projectToken?.()) return;
        const value = store.sources.get(key), fallback = metadata(row, kind), hasVideo = value?.has_video ?? fallback.hasVideo;
        const pictures = state?.record.tracks.find(track => track.id === 'v1')?.clips || [];
        const at = !plus || !state ? undefined : hasVideo ? Math.max(0, ...pictures.map(clip => clip.start + clip.frames)) : playhead;
        const result = await actions.insertSource({source, at, frames: value?.frames || fallback.frames,
            hasVideo, hasAudio: value?.has_audio ?? fallback.hasAudio});
        if (queued && !disposed && result?.ok !== false) actions.notify?.(t('Added to the timeline.', '已放到时间线上。'));
    }
    function requestAdd(row, kind, plus = false) {
        if (disposed) return;
        if (kind === 'work' || row.state === 'ready') return add(row, kind, plus).catch(actions.error);
        if (row.state === 'failed') { actions.notify?.(...importFailure(row.reason)); return; }
        const key = `${kind}:${row.id}`;
        if (!pendingAdds.has(key)) pendingAdds.set(key, {plus, project: actions.projectToken?.()});
        watchImports();
        actions.notify?.(preparing());
    }
    function startPending(items) {
        for (const row of items) {
            const key = `import:${row.id}`, pending = pendingAdds.get(key);
            if (!pending) continue;
            if (pending.project != null && pending.project !== actions.projectToken?.()) { pendingAdds.delete(key); continue; }
            if (row.state === 'ready') {
                pendingAdds.delete(key);
                add(row, 'import', pending.plus, {...pending, queued: true}).catch(actions.error);
            } else if (row.state === 'failed') { pendingAdds.delete(key); actions.notify?.(...importFailure(row.reason)); }
        }
    }
    function card(row, kind) {
        const container = el('div', 'fv-ed-source-card');
        const button = el('button', 'fv-ed-source'); button.type = 'button'; button.dataset.key = `${kind}:${row.id}`;
        const ready = kind === 'work' || row.state === 'ready';
        // A card is named by what it shows: the prompt's first 24 characters (a creation's prompt comes with its media
        // details), else the file's name, else its kind and time; the date goes to the line under it.
        const details = kind === 'work' ? store.sources.get(`work:${row.id}`) : null;
        const prompt = String(row.text || details?.text || '').replace(/\s+/g, ' ').trim();
        const created = row.created_at ? new Date(row.created_at) : null;
        const when = created && !Number.isNaN(created.getTime()) ? created.toLocaleString([], {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'}) : '';
        const kindWord = kind === 'work' || row.kind === 'video' ? t('Video', '视频') : row.kind === 'audio' ? t('Sound', '声音') : t('Clip', '素材');
        const cut = text => Array.from(text).length > 24 ? Array.from(text).slice(0, 24).join('') + '…' : text;
        // A card kept from the last visit knows only its picture until the list arrives: blank lines of the final height.
        const label = row.name || (prompt ? cut(prompt) : row.cached ? '' : [kindWord, when].filter(Boolean).join(' · '));
        const g = details || row.geometry || {}, framesShown = metadata(row, kind).frames;
        const size = g.width && g.height ? `${g.width} × ${g.height}` : '';
        const facts = [when, framesShown ? stamp(framesShown) : '', size].filter(Boolean).join(' · '), line = when;
        button.setAttribute('aria-label', label); button.draggable = ready; button.disabled = false;
        const picture = el('div', 'fv-ed-source-picture');
        const url = kind === 'work' ? (store.sources.get(`work:${row.id}`)?.thumbnail
            || '/freevideo/library/thumbnail?' + new URLSearchParams({id: row.id})) : row.thumbnail;
        if (url) { const image = el('img'); image.src = actions.mediaURL(url); image.alt = ''; image.loading = 'lazy'; image.onerror = () => { image.hidden = true; }; picture.append(image); }
        else if (row.kind === 'audio') picture.append(el('span', 'fv-ed-audio-icon', '♫'));
        if (ready) { if (!row.cached || framesShown) picture.append(el('span', 'fv-ed-source-time', stamp(framesShown))); }
        else if (row.state === 'failed') {
            picture.append(el('span', 'fv-ed-source-preparing', row.reason === 'disk_full' ? t('Disk full', '磁盘已满') : t('Cannot open', '打不开')));
        } else {
            // Sending, then waiting behind another import, then being made; only the first and the last have a percentage.
            const percent = Number.isFinite(row.fraction) ? ` ${Math.round(Math.max(0, Math.min(1, row.fraction)) * 100)}%` : '';
            picture.append(el('span', 'fv-ed-source-preparing', row.sending ? t('Uploading', '上传中') + percent
                : row.started ? t('Processing', '处理中') + percent : t('Queued', '排队中')));
        }
        button.append(picture);
        const caption = el('span', 'fv-ed-source-caption'), title = el('span', 'fv-ed-source-title', label), sub = el('span', 'fv-ed-source-sub', line);
        if (row.cached) { title.textContent ||= '\u00a0'; sub.textContent ||= '\u00a0'; }
        caption.append(title, sub); sub.hidden = !line && !row.cached; button.append(caption);
        const used = el('span', 'fv-ed-source-used'); used.append(editorIcon('check')); used.setAttribute('aria-label', t('Used', '已用')); used.hidden = true; picture.append(used);
        button.ondblclick = () => requestAdd(row, kind);
        button.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); requestAdd(row, kind); } };
        button.ondragstart = event => {
            if (!ready) { event.preventDefault(); return; }
            const value = metadata(row, kind), key = `${kind}:${row.id}`;
            event.dataTransfer.effectAllowed = 'copy';
            event.dataTransfer.setData('application/x-freevideo-source', JSON.stringify({key, ...value}));
            // The picture follows the pointer, not a see-through copy of the whole button; a sound has no picture and keeps the default.
            const thumb = picture.querySelector('img');
            if (thumb && !thumb.hidden && thumb.complete && thumb.naturalWidth) {
                const box = thumb.getBoundingClientRect();
                event.dataTransfer.setDragImage(thumb, Math.round(box.width / 2), Math.round(box.height / 2));
            }
            store.getSources([key]).catch(actions.error);
        };
        // Single click previews the source; double click also inserts it.
        button.onclick = () => { if (ready) Promise.resolve().then(() => actions.previewSource?.({kind, id: row.id})).catch(actions.error); else requestAdd(row, kind); };
        const plus = el('button', 'fv-ed-source-add', '＋'); plus.type = 'button'; plus.disabled = button.disabled; plus.title = button.title;
        plus.setAttribute('aria-label', `${t('Add to the tracks', '加到轨道上')}: ${label}`);
        tip(plus, {name: t('Add to the timeline', '加入时间线'), use: t('A video goes after the last picture, a sound at the playhead', '视频接在最后，声音放在播放头处'), key: 'Enter'});
        tip(button, {name: label, use: [prompt && prompt !== label ? prompt : '', facts, ready ? t('Click to preview; drag or double-click to place', '单击预览，拖动或双击放入时间线') : t('Preparing; it is placed when ready', '正在准备，好了会自动放入')].filter(Boolean).join(' · '), key: ready ? 'Enter' : ''});
        const insert = event => { event.preventDefault(); event.stopPropagation(); if (!(event.detail > 1)) requestAdd(row, kind, true); };
        plus.onclick = insert;
        plus.ondblclick = event => { event.preventDefault(); event.stopPropagation(); };
        plus.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') insert(event); };
        container.append(button, plus);
        if (kind === 'import' && (row.state === 'ready' || row.state === 'failed')) {
            const remove = el('button', 'fv-ed-source-remove'); remove.type = 'button'; remove.append(editorIcon('trash'));
            remove.setAttribute('aria-label', `${t('Delete', '删除')}: ${label}`);
            tip(remove, {name: t('Delete', '删除'), use: t('Remove this import from the library (asks first)', '从素材库删除这个文件（会先确认）')});
            remove.onclick = event => {
                event.preventDefault(); event.stopPropagation();
                if (!removal.has(row.id)) { removal.set(row.id, {users: 0, busy: false, failed: false}); focusAfter = {cancel: row.id}; render(); }
            };
            container.append(remove);
            const asking = removal.get(row.id);
            if (asking) { container.dataset.asking = 'true'; container.append(removeStrip(row, asking)); }
        }
        if (kind !== 'work' && row.state === 'failed' && row.reason !== 'disk_full') container.append(issueButton(t, () => ({where: LIBRARY_ISSUE, message: t('Cannot open', '打不开'), kind: row.detail || null})));
        return container;
    }
    // Asked inside the card, so a list that redraws keeps the question. Only FreeVideo's copy goes; the original file stays where it is.
    function removeStrip(row, asking) {
        const strip = el('div', 'fv-ed-source-confirm'); strip.setAttribute('role', 'group'); strip.dataset.id = row.id;
        strip.setAttribute('aria-label', t('Delete this clip', '删除这个素材'));
        const users = asking.users, hint = el('small', asking.failed && !asking.busy ? 'fv-ed-source-confirm-problem' : null,
            asking.busy ? t('Deleting…', '正在删除…') : asking.failed ? t('Could not delete', '删除失败，请重试')
            : users ? t(`Used in ${users} ${users === 1 ? 'edit' : 'edits'}`, `已用于 ${users} 个剪辑`) : t('Original file kept', '原文件保留'));
        const buttons = el('div', 'fv-ed-source-confirm-actions');
        const no = el('button', 'fv-ed-source-confirm-no', t('Cancel', '取消')), yes = el('button', 'fv-ed-source-confirm-yes', t('Delete', '删除'));
        for (const control of [no, yes]) { control.type = 'button'; control.disabled = asking.busy; }
        const cancel = () => { removal.delete(row.id); focusAfter = {card: `import:${row.id}`}; render(); };
        yes.onclick = event => { event.stopPropagation(); removeImport(row); };
        no.onclick = event => { event.stopPropagation(); cancel(); };
        // The editor hands a key to the control's own handler, as for the tabs above; Escape closes only the question.
        strip.onkeydown = event => { if (event.key === 'Escape' && !asking.busy) { event.preventDefault(); event.stopPropagation(); cancel(); } };
        buttons.append(no, yes);
        strip.append(el('strong', null, t('Delete this clip?', '删除这个素材？')), hint, buttons);
        return strip;
    }
    async function removeImport(row) {
        const asking = removal.get(row.id);
        if (!asking || asking.busy || disposed) return;
        asking.busy = true; asking.failed = false; render();
        try {
            await store.removeImport(row.id, {confirm: asking.users > 0});
            // Focus moves to the next clip, or to Import when it was the last.
            const keys = [...grid.querySelectorAll('.fv-ed-source')].map(card => card.dataset.key), at = keys.indexOf(`import:${row.id}`);
            focusAfter = {next: keys[at + 1] || null};
            removal.delete(row.id); recentImports.delete(row.id); pendingAdds.delete(`import:${row.id}`);
            imported = imported.filter(item => item.id !== row.id);
        } catch (error) {
            asking.busy = false;
            if (error?.reason === 'import_referenced') asking.users = Math.max(1, Number(error.response?.users) || 1);
            else if (error?.reason === 'import_in_use') {
                removal.delete(row.id); focusAfter = {card: `import:${row.id}`};
                actions.notify?.(t('This clip is still being prepared. Try again in a moment.', '素材还在处理，请稍后再试。'));
            } else asking.failed = true;
        } finally { render(); }
    }
    function updateUsed() {
        const keys = new Set((state?.record.tracks || []).flatMap(track => track.clips)
            .filter(clip => clip.source).map(clip => sourceKey(clip.source)));
        for (const button of grid.querySelectorAll('.fv-ed-source')) setHidden(button.querySelector('.fv-ed-source-used'), !keys.has(button.dataset.key));
    }
    // What a card shows, for reusing it: everything card() reads except the picture's address (a picture already
    // decoded is kept), plus the removal question when one is open.
    const cards = new Map();
    function cardSignature(row, kind) {
        const value = kind === 'work' ? store.sources.get(`work:${row.id}`) : null;
        return JSON.stringify([kind, row.id, row.name, row.text, row.created_at, row.state, row.reason, row.fraction, row.sending, row.started, row.kind,
            kind === 'work' ? !!(value?.thumbnail) : row.thumbnail, value?.frames ?? row.geometry?.frames ?? row.frames ?? null, value?.has_video, value?.has_audio, value?.text || '', value?.width, value?.height, removal.get(row.id) || null]);
    }
    function render() {
        if (disposed) return;
        const scroll = host.scrollTop || 0, top = host.getBoundingClientRect().top;
        const anchor = scroll > 0 ? [...grid.querySelectorAll('.fv-ed-source')].find(node => node.getBoundingClientRect().bottom > top) : null;
        const anchorTop = anchor?.getBoundingClientRect().top;
        for (const [button, value] of [[works, tab === 'work'], [imports, tab === 'import']]) { setAttr(button, 'aria-selected', value); setAttr(button, 'aria-pressed', value); }
        setHidden(search, tab !== 'work');
        // A redraw (the imports are read again every few seconds) keeps the focus on the same control.
        const held = document.activeElement, strip = held && grid.contains?.(held) ? held.closest?.('.fv-ed-source-confirm') : null;
        const control = ['fv-ed-source-remove', 'fv-ed-source-add'].find(name => held?.className === name);
        const owner = control ? held.closest?.('.fv-ed-source-card')?.querySelector('.fv-ed-source')?.dataset.key : null;
        const keep = !held || !grid.contains?.(held) ? null : strip ? {button: held.className, id: strip.dataset.id}
            : owner ? {control, card: owner} : held.dataset?.key ? {card: held.dataset.key} : null;
        const term = search.value.trim().toLocaleLowerCase();
        const shown = tab === 'work' ? rows.filter(row => row.video && (!term || [row.name, row.text, row.created_at, row.id].some(value => String(value || '').toLocaleLowerCase().includes(term)))) : [...imported, ...uploading.values()];
        // A redraw (the list and the imports are read again every few seconds) keeps every card whose content is the same,
        // and a picture already shown stays as it is: only cards that changed are made again, nothing goes blank or fades in.
        // One way to import: the Import button beside the search, on both tabs (the grid has no Import tile of its own).
        const nodes = [];
        const kept = new Set();
        for (const row of shown) {
            const key = `${tab}:${row.id}`, signature = cardSignature(row, tab);
            let held = cards.get(key);
            if (!held || held.signature !== signature) {
                const node = card(row, tab), old = held?.node.querySelector('.fv-ed-source-picture > img'), fresh = node.querySelector('.fv-ed-source-picture > img');
                // The new card takes over the picture already decoded for the same work, instead of loading it again.
                if (old && fresh && old.complete && old.naturalWidth > 0 && !old.hidden) fresh.replaceWith(old);
                held = {node, signature}; cards.set(key, held);
            }
            kept.add(key); nodes.push(held.node);
        }
        for (const key of [...cards.keys()]) if (!kept.has(key)) cards.delete(key);
        // Until the first list arrives, cards of the final size hold the place, so the library fills without jumping.
        if (tab === 'work' && !listed && !shown.length) for (let i = 0; i < 6; i++) { const ghost = el('div', 'fv-ed-source-skeleton'); ghost.setAttribute('aria-hidden', 'true'); nodes.push(ghost); }
        nodes.forEach((node, index) => { if (grid.children[index] !== node) grid.insertBefore(node, grid.children[index] || null); });
        while (grid.children.length > nodes.length) grid.lastElementChild.remove();
        updateUsed();
        message.textContent = ''; // Empty states stay silent.
        const failed = shown.findLast(row => row.state === 'failed');
        if (failed) {
            const [text, where] = importFailure(failed.reason); message.textContent = text;
            if (where) message.append(issueButton(t, () => ({where, message: text})));
        }
        const unread = tab === 'import' ? importUnread : 0;
        unreadable.textContent = unread ? t(`${unread} imported ${unread === 1 ? 'file' : 'files'} could not be read`, `另有 ${unread} 个导入的文件无法读取`) : '';
        if (!focusAfter && keep) focusAfter = keep.button ? {again: keep} : keep.control ? {control: keep} : keep.card ? {card: keep.card} : {next: null};
        if (focusAfter) {
            const target = focusAfter; focusAfter = null;
            const card = key => [...grid.querySelectorAll('.fv-ed-source')].find(node => node.dataset.key === key);
            const node = target.control ? card(target.control.card)?.parentNode?.querySelector('.' + target.control.control)
                : target.again ? [...grid.querySelectorAll('.fv-ed-source-confirm')].find(box => box.dataset.id === target.again.id)?.querySelector('.' + target.again.button)
                : target.cancel ? [...grid.querySelectorAll('.fv-ed-source-confirm')].find(strip => strip.dataset.id === target.cancel)?.querySelector('.fv-ed-source-confirm-no')
                : target.card ? card(target.card) : (target.next && card(target.next)) || (tab === 'import' ? importButton : null);
            node?.focus?.();
        }
        globalThis.requestAnimationFrame?.(() => { if (!disposed) markEnd(); });
        if (scroll > 0) {
            const replacement = anchor && [...grid.querySelectorAll('.fv-ed-source')].find(node => node.dataset.key === anchor.dataset.key);
            host.scrollTop = replacement ? scroll + replacement.getBoundingClientRect().top - anchorTop : scroll;
        }
    }
    function load(append = false, merge = false) {
        const read = listRead.then(async () => {
            if (disposed || (append && !next)) return null;
            loading = true; let succeeded = false;
            try {
                const value = await store.works(append ? next : null); if (disposed) return null;
                const items = value.items || [], ids = new Set(items.map(row => row.id));
                if (merge && rows.length) {
                    // The returned prefix is authoritative, including removals after a
                    // joined result hides its inputs. Keep already loaded older pages.
                    const covered = value.next ? rows.reduce((end, row, i) => ids.has(row.id) ? i + 1 : end, 0) : rows.length;
                    rows = [...items, ...rows.slice(covered).filter(row => !ids.has(row.id))];
                    if (!value.next) next = null;
                } else {
                    rows = append ? [...rows, ...items.filter(row => !rows.some(old => old.id === row.id))] : items;
                    next = value.next;
                }
                succeeded = true; if (!append) keepList(rows); render();
                store.getSources(items.filter(row => row.video).map(row => `work:${row.id}`)).then(render).catch(value => { if (!disposed) actions.error(value); });
                return value;
            } catch (error) { if (!disposed) actions.error(error); return null; }
            finally { loading = false; if (!listed) { listed = true; if (!succeeded && !disposed) render(); } if (succeeded) scheduleMore(); }
        });
        listRead = read; return read;
    }
    function refresh({outputs = []} = {}) {
        if (disposed) return Promise.resolve();
        outputs.forEach(output => expectedOutputs.add(output)); refreshAgain = true;
        if (refreshTask) return refreshTask;
        refreshTask = (async () => {
            while (refreshAgain && !disposed) {
                refreshAgain = false;
                const expected = [...expectedOutputs]; expectedOutputs.clear();
                for (let attempt = 0; attempt < 4 && !disposed; attempt++) {
                    const value = await load(false, true);
                    if (value && expected.every(output => value.items?.some(row => row.video === output))) break;
                    if (attempt === 3) break;
                    await new Promise(resolve => {
                        finishRetry = resolve;
                        retryTimer = setTimeout(() => { retryTimer = null; finishRetry = null; resolve(); }, [250, 750, 1500][attempt]);
                    });
                }
            }
        })().finally(() => { refreshTask = null; });
        return refreshTask;
    }
    // The Creations panel shares this page: what it generates, deletes or restores changes this list too.
    const generated = event => { const video = event.detail?.value?.video; if (video) refresh({outputs: [video]}); };
    const deleted = event => {
        const video = event.detail?.video; if (!video || disposed) return;
        if (rows.some(row => row.video === video)) { rows = rows.filter(row => row.video !== video); render(); }
    };
    const restored = () => { refresh(); };
    window.addEventListener('freevideo-result', generated);
    window.addEventListener('freevideo-library-deleted', deleted);
    window.addEventListener('freevideo-library-restored', restored);
    function choose(value) { tab = value; render(); scheduleMore(); }
    works.onclick = () => choose('work'); imports.onclick = () => choose('import');
    tabs.onkeydown = event => { if (['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); choose(tab === 'work' ? 'import' : 'work'); (tab === 'work' ? works : imports).focus(); } };
    search.oninput = () => { render(); scheduleMore(); };
    let moreFrame = null, pagePending = false;
    function scheduleMore() {
        if (disposed || !next || tab !== 'work' || moreFrame != null) return;
        moreFrame = requestAnimationFrame(() => { moreFrame = null; loadMore(); });
    }
    function loadMore() {
        if (disposed || tab !== 'work' || loading || pagePending || !next) return;
        if (!search.value && !(host.clientHeight > 0 && host.scrollTop + host.clientHeight >= host.scrollHeight - 2)) return;
        pagePending = true;
        load(true).finally(() => { pagePending = false; });
    }
    host.addEventListener('scroll', scheduleMore, {passive: true});
    // The last row cut by the pane's foot fades out (24 px) instead of looking cut off; at the very end there is no fade.
    function markEnd() { setData(host, 'atEnd', host.scrollTop + host.clientHeight >= host.scrollHeight - 2); }
    host.addEventListener('scroll', markEnd, {passive: true});
    const resize = new ResizeObserver(scheduleMore); resize.observe(host);
    // Files dropped on the tracks go to the drop point once ready, in drop order.
    const placing = [];
    let placed = Promise.resolve();
    function placeReady(items) {
        while (placing.length && !disposed) {
            const head = placing[0], row = items.find(item => item.id === head.id);
            if (row?.state === 'preparing' || (!row && recentImports.has(head.id))) return;
            placing.shift();
            if (row?.state !== 'ready') continue;
            const value = metadata(row, 'import'), at = head.place.at;
            if (value.hasVideo) head.place.at += value.frames;
            const insert = {source: {kind: 'import', id: row.id}, at, frames: value.frames, hasVideo: value.hasVideo, hasAudio: value.hasAudio, ...(!value.hasVideo && head.place.track ? {targetTrack: head.place.track, replace: head.place.replace === true} : {})};
            placed = placed.then(() => disposed ? null : actions.insertSource(insert, {loadMetadata: true})).catch(actions.error);
        }
    }
    async function importFiles(files, {at, track, replace = false} = {}) {
        choose('import'); watchImports();
        const place = Number.isSafeInteger(at) && at >= 0 ? {at, ...(['a1', 'a2', 'a3'].includes(track) ? {track, replace} : {})} : null;
        for (const value of files) {
            if (disposed) break;
            const id = 'upload-' + Math.random(), row = {id, name: value.name, kind: value.type.startsWith('audio') ? 'audio' : 'video', state: 'preparing', fraction: 0, sending: true};
            uploading.set(id, row); render();
            try {
                const item = await store.upload(value, fraction => { row.fraction = fraction; render(); });
                if (item && !disposed) {
                    const pending = pendingAdds.get(`import:${id}`);
                    pendingAdds.delete(`import:${id}`);
                    if (pending) pendingAdds.set(`import:${item.id}`, pending);
                    recentImports.set(item.id, item);
                    imported = [item, ...imported.filter(existing => existing.id !== item.id)];
                    if (place) { placing.push({id: item.id, place}); placeReady(imported); }
                    else startPending([item]);
                }
            } catch (error) { actions.notify(...importFailure(error?.message === 'import_failed' ? 'import_failed' : error?.reason, error?.status)); }
            finally { pendingAdds.delete(`import:${id}`); uploading.delete(id); render(); }
        }
    }
    file.onchange = () => { importFiles([...file.files]); file.value = ''; };
    // The import list is read every 2 s while the editor is shown. A hidden (kept) editor reads it only while an
    // import it waits for is still on its way (sent, asked to be placed, or dropped on the tracks), then stops; it
    // reads it again at once when it is shown again.
    let stopImports = null, suspended = false;
    const waiting = () => uploading.size > 0 || pendingAdds.size > 0 || placing.length > 0;
    function watchImports() {
        if (stopImports || disposed) return;
        stopImports = store.watchImports(items => {
            for (const item of items) recentImports.delete(item.id);
            // A list request started before an upload may finish after it.
            imported = [...recentImports.values(), ...items]; importUnread = items.unreadable || 0; render();
            placeReady(imported); startPending(imported);
            if (suspended && !waiting()) { stopImports?.(); stopImports = null; }
        });
    }
    watchImports();
    const stopUsed = state?.subscribe(event => { if (event.type === 'record') updateUsed(); });
    render(); load();
    return {element: host, importFiles, refresh,
        suspend() { suspended = true; if (!waiting()) { stopImports?.(); stopImports = null; } },
        resume() { suspended = false; watchImports(); },
        dispose() { disposed = true; pendingAdds.clear(); window.removeEventListener('freevideo-result', generated); window.removeEventListener('freevideo-library-deleted', deleted); window.removeEventListener('freevideo-library-restored', restored); host.removeEventListener('scroll', scheduleMore); resize.disconnect(); cancelAnimationFrame(moreFrame); stopImports?.(); stopImports = null; stopUsed?.(); clearTimeout(retryTimer); finishRetry?.(); }};
}
