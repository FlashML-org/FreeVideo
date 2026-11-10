import {editorIcon} from './editor_icons.js';
import {sourceKey} from './editor_store.js';
import {issueButton} from './issue_button.js';
import {connectionLostText} from './browser_extension.js';
const LIBRARY_ISSUE = ['Editor media library', '剪辑器素材库'];
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const stamp = frames => { const seconds = Math.floor((frames || 0) / 24); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`; };
const FORMATS = 'MP4, MOV, WebM, MKV, M4V, WAV, MP3, FLAC, OGG, M4A, AAC, Opus';
export function createEditorLibrary(host, {store, t, actions, state}) {
    const tabs = el('div', 'fv-ed-tabs fv-pass-mode'); tabs.setAttribute('role', 'tablist');
    const works = el('button', null, t('Creations', '作品')), imports = el('button', null, t('Imported', '已导入'));
    for (const tab of [works, imports]) { tab.type = 'button'; tab.setAttribute('role', 'tab'); tabs.append(tab); }
    const search = el('input', 'fv-ed-search'); search.type = 'search'; search.placeholder = t('Search', '搜索'); search.setAttribute('aria-label', search.placeholder);
    const searchTools = el('div', 'fv-ed-library-tools'), importButton = el('button', 'fv-ed-library-import', t('Import', '导入'));
    importButton.type = 'button'; searchTools.append(search, importButton);
    const grid = el('div', 'fv-ed-library-grid'); grid.setAttribute('role', 'tabpanel');
    const message = el('p', 'fv-ed-library-message'); message.setAttribute('role', 'status');
    const unreadable = el('p', 'fv-ed-unreadable');
    const file = el('input'); file.type = 'file'; file.multiple = true; file.hidden = true;
    file.accept = '.mp4,.mov,.webm,.mkv,.m4v,.wav,.mp3,.flac,.ogg,.m4a,.aac,.opus';
    importButton.onclick = () => file.click();
    host.append(tabs, searchTools, grid, message, unreadable, file);
    let tab = 'work', rows = [], imported = [], importUnread = 0, next = null, disposed = false, loading = false;
    let listRead = Promise.resolve(), refreshTask = null, retryTimer = null, finishRetry = null, refreshAgain = false;
    const expectedOutputs = new Set();
    const uploading = new Map(), recentImports = new Map(), pendingAdds = new Map();
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
        const label = row.name || row.text || new Date(row.created_at).toLocaleDateString();
        button.setAttribute('aria-label', label); button.draggable = ready; button.disabled = false;
        const picture = el('div', 'fv-ed-source-picture');
        const url = kind === 'work' ? (store.sources.get(`work:${row.id}`)?.thumbnail
            || '/freevideo/library/thumbnail?' + new URLSearchParams({id: row.id})) : row.thumbnail;
        if (url) { const image = el('img'); image.src = actions.mediaURL(url); image.alt = ''; image.loading = 'lazy'; image.onerror = () => { image.hidden = true; }; picture.append(image); }
        else if (row.kind === 'audio') picture.append(el('span', 'fv-ed-audio-icon', '♫'));
        if (ready) picture.append(el('span', 'fv-ed-source-time', stamp(metadata(row, kind).frames)));
        else if (row.state === 'failed') {
            picture.append(el('span', 'fv-ed-source-preparing', row.reason === 'disk_full' ? t('Disk full', '磁盘已满') : t('Cannot open', '打不开')));
        } else {
            // Sending, then waiting behind another import, then being made; only the first and the last have a percentage.
            const percent = Number.isFinite(row.fraction) ? ` ${Math.round(Math.max(0, Math.min(1, row.fraction)) * 100)}%` : '';
            picture.append(el('span', 'fv-ed-source-preparing', row.sending ? t('Uploading', '上传中') + percent
                : row.started ? t('Processing', '处理中') + percent : t('Queued', '排队中')));
        }
        button.append(picture);
        const used = el('span', 'fv-ed-source-used'); used.append(editorIcon('check')); used.setAttribute('aria-label', t('Used', '已用')); used.hidden = true; picture.append(used);
        button.ondblclick = () => requestAdd(row, kind);
        button.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); requestAdd(row, kind); } };
        button.ondragstart = event => {
            if (!ready) { event.preventDefault(); return; }
            const value = metadata(row, kind), key = `${kind}:${row.id}`;
            event.dataTransfer.effectAllowed = 'copy';
            event.dataTransfer.setData('application/x-freevideo-source', JSON.stringify({key, ...value}));
            store.getSources([key]).catch(actions.error);
        };
        // Single click previews the source; double click also inserts it.
        button.onclick = () => { if (ready) Promise.resolve().then(() => actions.previewSource?.({kind, id: row.id})).catch(actions.error); else requestAdd(row, kind); };
        const plus = el('button', 'fv-ed-source-add', '＋'); plus.type = 'button'; plus.disabled = button.disabled; plus.title = button.title;
        plus.setAttribute('aria-label', `${t('Add to the tracks', '加到轨道上')}: ${label}`);
        const insert = event => { event.preventDefault(); event.stopPropagation(); if (!(event.detail > 1)) requestAdd(row, kind, true); };
        plus.onclick = insert;
        plus.ondblclick = event => { event.preventDefault(); event.stopPropagation(); };
        plus.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') insert(event); };
        container.append(button, plus);
        if (kind !== 'work' && row.state === 'failed' && row.reason !== 'disk_full') container.append(issueButton(t, () => ({where: LIBRARY_ISSUE, message: t('Cannot open', '打不开'), kind: row.detail || null})));
        return container;
    }
    function updateUsed() {
        const keys = new Set((state?.record.tracks || []).flatMap(track => track.clips)
            .filter(clip => clip.source).map(clip => sourceKey(clip.source)));
        for (const button of grid.querySelectorAll('.fv-ed-source')) button.querySelector('.fv-ed-source-used').hidden = !keys.has(button.dataset.key);
    }
    function render() {
        if (disposed) return;
        const scroll = host.scrollTop || 0, top = host.getBoundingClientRect().top;
        const anchor = scroll > 0 ? [...grid.querySelectorAll('.fv-ed-source')].find(node => node.getBoundingClientRect().bottom > top) : null;
        const anchorTop = anchor?.getBoundingClientRect().top;
        works.setAttribute('aria-selected', String(tab === 'work')); imports.setAttribute('aria-selected', String(tab === 'import'));
        works.setAttribute('aria-pressed', String(tab === 'work')); imports.setAttribute('aria-pressed', String(tab === 'import'));
        search.hidden = tab !== 'work';
        grid.replaceChildren();
        if (tab === 'import') {
            const add = el('button', 'fv-ed-import', t('Import', '导入')); add.type = 'button'; add.onclick = () => file.click(); grid.append(add);
        }
        const term = search.value.trim().toLocaleLowerCase();
        const shown = tab === 'work' ? rows.filter(row => row.video && (!term || [row.name, row.text, row.created_at, row.id].some(value => String(value || '').toLocaleLowerCase().includes(term)))) : [...imported, ...uploading.values()];
        for (const row of shown) grid.append(card(row, tab));
        updateUsed();
        message.textContent = ''; // Empty states stay silent.
        const failed = shown.findLast(row => row.state === 'failed');
        if (failed) {
            const [text, where] = importFailure(failed.reason); message.textContent = text;
            if (where) message.append(issueButton(t, () => ({where, message: text})));
        }
        const unread = tab === 'import' ? importUnread : 0;
        unreadable.textContent = unread ? t(`${unread} imported ${unread === 1 ? 'file' : 'files'} could not be read`, `另有 ${unread} 个导入的文件无法读取`) : '';
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
                succeeded = true; render();
                store.getSources(items.filter(row => row.video).map(row => `work:${row.id}`)).then(render).catch(value => { if (!disposed) actions.error(value); });
                return value;
            } catch (error) { if (!disposed) actions.error(error); return null; }
            finally { loading = false; if (succeeded) scheduleMore(); }
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
        choose('import');
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
    const stop = store.watchImports(items => {
        for (const item of items) recentImports.delete(item.id);
        // A list request started before an upload may finish after it.
        imported = [...recentImports.values(), ...items]; importUnread = items.unreadable || 0; render();
        placeReady(imported); startPending(imported);
    });
    const stopUsed = state?.subscribe(event => { if (event.type === 'record') updateUsed(); });
    render(); load();
    return {element: host, importFiles, refresh, dispose() { disposed = true; pendingAdds.clear(); window.removeEventListener('freevideo-result', generated); window.removeEventListener('freevideo-library-deleted', deleted); window.removeEventListener('freevideo-library-restored', restored); host.removeEventListener('scroll', scheduleMore); resize.disconnect(); cancelAnimationFrame(moreFrame); stop(); stopUsed?.(); clearTimeout(retryTimer); finishRetry?.(); }};
}
