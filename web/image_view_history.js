// The works column: every picture made on this machine, grouped by day, with search, filters, sort,
// favourites, several at once, compare and delete. The engine lists files only; what each picture was made
// with (model, size, time) and the favourites are kept in this browser. The words a picture was made from are not
// written to the browser: they are known while the page that made it stays open (sessionPrompts), or from the
// engine's own record of the picture when it gives one (row.prompt). Recently deleted is an adapter:
// today the engine deletes at once, so a deleted picture waits here for a short undo window before it is gone.
import { listenDialogKey } from './dialog_keys.js';
import { el, icon, t, tip, button, readStore, writeStore, mod } from './image_view_ui.js';

export const META_KEY = 'freevideo.imageView.meta';
const META_LIMIT = 600;
// Text a picture was made from: never stored in the browser (older records lose it the first time they are read).
const TEXT_FIELDS = ['prompt'];
const withoutText = value => { if (!value || typeof value !== 'object') return value; const copy = {...value}; for (const k of TEXT_FIELDS) delete copy[k]; return copy; };
// The prompts of pictures made while this page is open, in memory only.
export const sessionPrompts = new Map();
export const promptOfRow = row => String(row?.prompt || sessionPrompts.get(row?.file) || '');

export function createMetaStore(storage = null) {
    const read = () => storage ? (() => { try { return JSON.parse(storage.getItem(META_KEY) || 'null') || {}; } catch { return {}; } })() : readStore(META_KEY, {});
    const write = value => { if (storage) { try { storage.setItem(META_KEY, JSON.stringify(value)); } catch { /* not kept */ } } else writeStore(META_KEY, value); };
    const clean = () => {
        data.items ||= {}; data.fav ||= {};
        let dirty = false;
        for (const [file, item] of Object.entries(data.items)) if (item && TEXT_FIELDS.some(k => k in item)) { data.items[file] = withoutText(item); dirty = true; }
        return dirty;
    };
    let data = read(); if (clean()) write(data);
    const save = () => {
        const names = Object.keys(data.items);
        if (names.length > META_LIMIT) names.sort((a, b) => (data.items[a].at || 0) - (data.items[b].at || 0)).slice(0, names.length - META_LIMIT).forEach(n => { delete data.items[n]; });
        write(data);
    };
    return {
        get: file => data.items[file] || null,
        set(file, patch) { data.items[file] = {...(data.items[file] || {}), ...withoutText(patch)}; save(); },
        isFav: file => !!data.fav[file],
        setFav(files, value) { for (const f of files) { if (value) data.fav[f] = 1; else delete data.fav[f]; } save(); },
        forget(files) { for (const f of files) { delete data.items[f]; delete data.fav[f]; } save(); },
        // Durations of recent runs, for the size estimate.
        samples: () => Object.values(data.items).filter(m => m.seconds > 0 && m.width > 0).sort((a, b) => (a.at || 0) - (b.at || 0))
            .map(m => ({model: m.model, area: m.width * m.height, seconds: m.seconds, references: m.references || 0})),
        reload() { data = read(); if (clean()) write(data); },
    };
}

// The engine names each picture image-YYYYMMDD-HHMMSS-NN; that is its time when nothing else is known.
// The engine names each picture by its own clock; that one clock orders and groups them all (this browser's record
// of when a picture finished is another clock, and mixing the two put pictures out of order across time zones).
export function stampOf(row, meta = null) {
    const m = /image-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(row?.name || row?.file || '');
    return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime() : (meta?.at || 0);
}
const dayStart = ms => { const d = new Date(ms); d.setHours(0, 0, 0, 0); return d.getTime(); };

export const FILTERS = Object.freeze(['all', 'fav', 'minimax-h3-image', 'qwen-image-2.1-turbo', 'moss-image-sft']);
export function filterRows(rows, {filter = 'all', query = '', sort = 'new'} = {}, meta, now = Date.now()) {
    const q = query.trim().toLowerCase(), today = dayStart(now);
    const kept = rows.filter(row => {
        const m = meta.get(row.file);
        if (filter === 'fav' && !meta.isFav(row.file)) return false;
        if (filter === 'today' && stampOf(row, m) < today) return false;
        if (filter.includes('-') && filter !== 'all' && m?.model !== filter) return false;
        if (q && !promptOfRow(row).toLowerCase().includes(q) && !String(row.name || row.file).toLowerCase().includes(q)) return false;
        return true;
    });
    const at = row => stampOf(row, meta.get(row.file));
    // Same second: the engine's own order (newest first) decides.
    const order = new Map(rows.map((row, index) => [row.file, index]));
    // Newest first is the engine's own order (it sorts by its clock); the browser does not re-sort by another clock.
    const byNew = (a, b) => order.get(a.file) - order.get(b.file);
    if (sort === 'old') kept.sort((a, b) => -byNew(a, b));
    else if (sort === 'fav') kept.sort((a, b) => (meta.isFav(b.file) - meta.isFav(a.file)) || byNew(a, b));
    else kept.sort(byNew);
    return kept;
}
export function filterCounts(rows, meta, now = Date.now()) {
    return Object.fromEntries(FILTERS.map(f => [f, filterRows(rows, {filter: f}, meta, now).length]));
}

// The works are one list in their order (newest first unless chosen otherwise): no dates or times are written.
export function groupRows(rows) {
    return [{key: 'all', label: '', rows}];
}

// Recently deleted, over today's engine: a deletion waits `holdMs` (restorable), then the engine deletes the files.
// The engine's own 30-day bin will replace `purge` and `holdMs`; the page asks only list/remove/restore/purge.
// With an engine that keeps deleted pictures itself (info.history_trash) a removal is sent at once (history-trash) and the
// engine deletes them for good after its undo time, also when this page is closed; undo asks it back (history-undo).
// Without it, the pictures are held here and deleted (history-delete) when the time is over, as before.
export function createTrash({client, holdMs = 10000, now = () => Date.now(), setTimer = setTimeout, clearTimer = clearTimeout, onChange = () => {}} = {}) {
    const held = new Map(); let timer = null;
    let engineBin = null;
    const usesEngine = () => engineBin ||= Promise.resolve(typeof client?.historyTrash === 'function' && typeof client?.offers === 'function'
        ? client.offers('history_trash') : false).catch(() => false);
    // Files sent to the engine's bin, by file: the promise of {batch, failed} for the request that moved them.
    const sent = new Map();
    const send = files => {
        const moving = usesEngine().then(async yes => {
            if (!yes) return null;
            try { const answer = await client.historyTrash(files); return {batch: answer.batch, failed: answer.failed || []}; }
            catch { return null; }  // not moved: deleted the earlier way when the time is over
        });
        for (const file of files) sent.set(file, moving);
    };
    const schedule = () => {
        if (timer) clearTimer(timer); timer = null;
        if (!held.size) return;
        const next = Math.min(...[...held.values()].map(h => h.at + holdMs));
        timer = setTimer(() => { timer = null; const due = [...held.values()].filter(h => h.at + holdMs <= now() + 5).map(h => h.row.file); void trash.purge(due); }, Math.max(0, next - now()));
    };
    const trash = {
        holdMs,
        list: () => [...held.values()].sort((a, b) => b.at - a.at),
        has: file => held.has(file),
        remove(rows) { const at = now(); for (const row of rows) held.set(row.file, {row, at}); send(rows.map(row => row.file)); schedule(); onChange(); },
        restore(files) {
            const batches = new Map();
            for (const f of files) { held.delete(f); const moving = sent.get(f); sent.delete(f); if (moving) batches.set(moving, true); }
            // A batch comes back whole: what is still held from it is sent to the bin again.
            for (const moving of batches.keys()) void moving.then(async moved => {
                if (!moved?.batch) return;
                try { await client.historyUndo(moved.batch); } catch { return; }
                const rest = [...sent.entries()].filter(([, p]) => p === moving).map(([file]) => file).filter(file => held.has(file));
                if (rest.length) send(rest);
            });
            schedule(); onChange();
        },
        // Deletes for good; the answer is the engine's list after it, and the files it kept with why.
        async purge(files = [...held.keys()]) {
            const list = files.filter(f => held.has(f)); if (!list.length) return null;
            for (const f of list) held.delete(f); schedule();
            // What the engine's bin took is deleted by the engine; only the rest needs the earlier delete.
            const failed = [], earlier = [];
            for (const file of list) {
                const moved = await (sent.get(file) || Promise.resolve(null)); sent.delete(file);
                if (!moved) { earlier.push(file); continue; }
                const refused = moved.failed.find(f => f.file === file);
                if (refused) failed.push(refused);
            }
            let answer = {items: null, failed};
            if (earlier.length) {
                try {
                    const done = earlier.length === 1 ? {items: await client.historyDelete(earlier[0]), failed: []} : await client.historyDeleteMany(earlier);
                    answer = {items: done.items, failed: [...failed, ...(done.failed || [])]};
                } catch (error) { answer = {items: null, failed: [...failed, ...earlier.map(file => ({file, reason: error?.code || 'failed'}))]}; }
            }
            onChange(answer, list); return answer;
        },
        flush() { return trash.purge(); },
        dispose() { if (timer) clearTimer(timer); timer = null; },
    };
    return trash;
}

const FILTER_WORDS = {all: ['All', '全部'], fav: ['Favourites', '收藏'], today: ['Today', '今天'], 'minimax-h3-image': ['H3', 'H3'], 'qwen-image-2.1-turbo': ['Qwen', 'Qwen'], 'moss-image-sft': ['MOSS', 'MOSS']};
const SORT_WORDS = {new: ['Newest first', '最新在前'], old: ['Oldest first', '最早在前'], fav: ['Favourites first', '收藏在前']};

// The column itself. `paint(state)` redraws; tiles are kept by file so pictures never reload on a redraw.
// thumbURL: a picture's small copy for the tiles (the picture itself without one). queue: the page's background
// queue (media_queue.js), which loads the pictures on screen after their tiles over a link.
export function historyPanel({pictureURL, thumbURL = pictureURL, queue = null, onShow, onToggleFav, onSelect, onSelectMode, onSelectAll, onDelete, onFilter, onQuery, onSort,
    onTrash, onRestore, onPurge, onOpenFolder, onCollapse, onRun = () => {}}) {
    const root = el('div', 'fv-iv-works');
    // Head: title and count, search, sort, select, fold.
    const head = el('div', 'fv-iv-panel-head');
    const title = el('h2', 'fv-iv-panel-title', t('Works', '作品')), count = el('span', 'fv-iv-count');
    const sortBtn = button('fv-iv-icon-btn', {iconName: 'sort'});
    const selectBtn = button('fv-iv-icon-btn', {iconName: 'select', name: t('Select', '选择'), use: t('Pick several to compare or delete', '选几张来对比或删除')});
    const collapse = button('fv-iv-icon-btn fv-iv-collapse', {iconName: 'panelR', name: t('Hide works', '收起作品栏'), use: t('More room for the picture', '给画面腾出空间')});
    head.append(title, count, el('span', 'fv-iv-grow'), collapse);
    const searchRow = el('label', 'fv-iv-search'); const query = el('input'); query.type = 'search'; query.placeholder = t('Search prompts', '搜索提示词');
    tip(query, t('Search', '搜索'), t('Find pictures by their prompt', '按提示词查找图片'), `${mod}+F`); searchRow.append(icon('search'), query);
    const toolsRow = el('div', 'fv-iv-works-tools'); toolsRow.append(searchRow, sortBtn, selectBtn);
    const chips = el('div', 'fv-iv-chips'); chips.setAttribute('role', 'tablist');
    const chipButtons = FILTERS.map(f => { const b = button('fv-iv-chip'); b.dataset.filter = f; b.setAttribute('role', 'tab'); b.append(el('span', 'fv-iv-btn-label', t(...FILTER_WORDS[f])), el('span', 'fv-iv-chip-n')); b.addEventListener('click', () => onFilter(f)); return b; });
    chips.append(...chipButtons);
    const sortMenu = el('div', 'fv-iv-menu'); sortMenu.hidden = true; sortMenu.setAttribute('role', 'menu');
    const sortItems = Object.keys(SORT_WORDS).map(s => { const b = el('button', 'fv-iv-menu-item'); b.type = 'button'; b.setAttribute('role', 'menuitemradio'); b.dataset.sort = s; b.append(el('span', '', t(...SORT_WORDS[s])), icon('check', 'fv-iv-menu-mark')); b.addEventListener('click', () => { sortMenu.hidden = true; onSort(s); }); return b; });
    sortMenu.append(...sortItems);
    // Body: the grid, or the recently deleted list.
    const body = el('div', 'fv-iv-works-body'); const grid = el('div', 'fv-iv-grid-wrap');
    const empty = el('div', 'fv-iv-empty');
    const trashView = el('div', 'fv-iv-trash'); trashView.hidden = true;
    body.append(grid, empty, trashView);
    // Foot: the selection bar, or the way to recently deleted and the folder.
    const foot = el('div', 'fv-iv-panel-foot');
    const batch = el('div', 'fv-iv-batch'), picked = el('b', 'fv-iv-picked');
    const allBtn = button('fv-iv-text-btn', {label: t('All', '全选'), name: t('Select all', '全选'), use: t('Pick every picture shown', '选中当前显示的全部图片'), key: `${mod}+A`});
    const favBtn = button('fv-iv-icon-btn', {iconName: 'star'});
    const deleteBtn = button('fv-iv-icon-btn fv-iv-danger', {iconName: 'trash'});
    const doneBtn = button('fv-iv-text-btn', {label: t('Done', '完成'), name: t('Done', '完成'), use: t('Leave selection', '退出多选'), key: 'Esc'});
    // Two or more picked are compared on the stage by themselves; the bar keeps what is done to all of them.
    batch.append(picked, allBtn, el('span', 'fv-iv-grow'), favBtn, deleteBtn, doneBtn);
    const links = el('div', 'fv-iv-links');
    const trashLink = button('fv-iv-link', {iconName: 'trash', label: t('Recently deleted', '最近删除'), name: t('Recently deleted', '最近删除'), use: t('Bring back a picture you just deleted', '找回刚删除的图片')});
    const trashN = el('span', 'fv-iv-link-n'); trashLink.append(trashN);
    const folderLink = button('fv-iv-icon-btn', {iconName: 'folder', name: t('Open folder', '打开文件夹'), use: t('Show the pictures on this computer', '在电脑上打开保存图片的文件夹')});
    links.append(trashLink, el('span', 'fv-iv-grow'), folderLink);
    foot.append(batch, links);
    root.append(head, toolsRow, chips, sortMenu, body, foot);

    query.addEventListener('input', () => onQuery(query.value));
    listenDialogKey(query, 'keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); event.preventDefault(); query.value = ''; onQuery(''); query.blur(); } });
    sortBtn.addEventListener('click', () => { sortMenu.hidden = !sortMenu.hidden; if (!sortMenu.hidden) sortMenu.querySelector('[aria-checked=true]')?.focus(); });
    document.addEventListener('pointerdown', event => { if (!sortMenu.hidden && !sortMenu.contains(event.target) && !sortBtn.contains(event.target)) sortMenu.hidden = true; }, true);
    listenDialogKey(sortMenu, 'keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); sortMenu.hidden = true; sortBtn.focus(); } });
    selectBtn.addEventListener('click', () => onSelectMode());
    collapse.addEventListener('click', () => onCollapse());
    allBtn.addEventListener('click', () => onSelectAll());
    favBtn.addEventListener('click', () => onToggleFav(null));
    deleteBtn.addEventListener('click', () => onDelete(null));
    doneBtn.addEventListener('click', () => onSelectMode(false));
    trashLink.addEventListener('click', () => onTrash());
    folderLink.addEventListener('click', () => onOpenFolder());

    const tiles = new Map(); let lastClicked = null; let order = [];
    // A tile's small picture is asked for when the tile comes within about a screen of the visible part of the list (as
    // the browser's lazy loading did), so a long history does not load at once; over a link the pictures themselves then
    // follow in the background, nearest first.
    const nearby = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
        for (const {target, isIntersecting} of entries) if (isIntersecting) { nearby.unobserve(target); showTile(target); }
    }, {root: body, rootMargin: '800px 0px'}) : null;
    const originals = queue ? queue.watch(body, tile => tile.dataset.file ? {url: pictureURL(tile.dataset.file), kind: 'image'} : null,
        {order: tile => { const at = order.indexOf(tile.dataset.file); return at < 0 ? 1e5 : at; }}) : null;
    function showTile(image) {
        if (image.getAttribute('src')) return;
        queue?.thumbnail(image);
        image.src = image.dataset.src; if (image.complete && image.naturalWidth) image.dataset.loaded = 'true';
    }
    const blockCache = new Map();
    // Children made exactly `nodes`, in order, with the fewest moves; a node already in place is not touched.
    const reconcile = (parent, nodes) => {
        nodes.forEach((node, at) => { if (parent.children[at] !== node) parent.insertBefore(node, parent.children[at] || null); });
        while (parent.children.length > nodes.length) parent.lastElementChild.remove();
    };
    const runTile = el('div', 'fv-iv-tile fv-iv-tile-run'); runTile.append(el('i', 'fv-iv-ring'));
    tip(runTile, t('Generating', '正在生成'), t('Back to its progress', '回到生成画面'));
    runTile.setAttribute('role', 'button'); runTile.tabIndex = 0; runTile.addEventListener('click', () => onRun());
    listenDialogKey(runTile, 'keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onRun(); } });
    function tileFor(row) {
        let tile = tiles.get(row.file);
        if (tile) return tile;
        tile = el('div', 'fv-iv-tile'); tile.dataset.file = row.file;
        const open = el('button', 'fv-iv-tile-open'); open.type = 'button';
        const image = el('img'); image.alt = ''; image.loading = 'lazy'; image.decoding = 'async'; image.draggable = false;
        // Shown whole once loaded; until then the tile is a placeholder (and shows no "selected" ring on nothing).
        image.dataset.loaded = 'false'; image.addEventListener('load', () => { image.dataset.loaded = 'true'; }); image.addEventListener('error', () => { image.dataset.loaded = 'true'; image.dataset.missing = 'true'; });
        image.dataset.src = thumbURL(row.file);
        if (nearby) nearby.observe(image); else showTile(image);
        open.append(image);
        const check = el('button', 'fv-iv-tile-check'); check.type = 'button'; check.append(icon('check'));
        tip(check, t('Select', '选择'), t('Add to the selection; Shift picks a run', '加入多选；按住 Shift 连选'));
        const star = el('span', 'fv-iv-tile-star'); star.append(icon('star'));
        tile.append(open, check, star);
        open.addEventListener('click', event => {
            if (event.shiftKey || event.metaKey || event.ctrlKey || root.dataset.selecting === 'true') { pickTile(row.file, event.shiftKey); return; }
            lastClicked = row.file; onShow(row.file);
        });
        check.addEventListener('click', event => { event.stopPropagation(); pickTile(row.file, event.shiftKey); });
        tiles.set(row.file, tile); originals?.observe(tile); return tile;
    }
    function pickTile(file, range) {
        if (range && lastClicked && order.includes(lastClicked)) {
            const a = order.indexOf(lastClicked), b = order.indexOf(file);
            onSelect(order.slice(Math.min(a, b), Math.max(a, b) + 1), true);
        } else onSelect([file]);
        lastClicked = file;
    }

    // state: {rows, groups, running, shownFile, selecting, picked:Set, filter, query, sort, counts, total, trash:[...], view, meta}
    function paint(state) {
        root.dataset.selecting = String(state.selecting); root.dataset.view = state.view;
        count.textContent = '';
        for (const b of chipButtons) {
            const f = b.dataset.filter, n = state.counts[f] || 0;
            b.setAttribute('aria-selected', String(state.filter === f)); b.setAttribute('aria-pressed', String(state.filter === f));
            b.querySelector('.fv-iv-chip-n').textContent = '';
            b.hidden = (f.includes('-') && !n && state.filter !== f);
            tip(b, t(...FILTER_WORDS[f]), {all: t('Show every picture', '显示全部图片'), fav: t('Only the starred ones', '只看收藏的图片'), today: t('Only pictures made today', '只看今天生成的'),
                'minimax-h3-image': t('Only pictures made with H3 Image', '只看 H3 Image 生成的'), 'qwen-image-2.1-turbo': t('Only pictures made with Qwen-Image', '只看 Qwen-Image 生成的'),
                'moss-image-sft': t('Only pictures made with MOSS-Image', '只看 MOSS-Image 生成的')}[f]);
        }
        for (const item of sortItems) item.setAttribute('aria-checked', String(item.dataset.sort === state.sort));
        tip(sortBtn, t('Sort', '排序'), t(`Now: ${t(...SORT_WORDS[state.sort])}`, `当前：${t(...SORT_WORDS[state.sort])}`));
        selectBtn.setAttribute('aria-pressed', String(state.selecting));
        if (query.value !== state.query && document.activeElement !== query) query.value = state.query;
        // Grid.
        grid.hidden = state.view !== 'works'; trashView.hidden = state.view !== 'trash'; chips.hidden = state.view !== 'works';
        order = state.rows.map(r => r.file);
        if (state.view === 'works') {
            // The list is reconciled, never rebuilt: tiles, day headers and grids are kept by key and only moved,
            // added or removed where the order changed; a click changes classes only, so nothing scrolls or reloads.
            const old = state.sort === 'old';
            if (state.running) {
                runTile.style.setProperty('--p', String(state.running.percent || 0));
                const img = runTile.querySelector('img');
                if (state.running.preview) { if (!img) runTile.prepend(Object.assign(el('img'), {alt: ''})); const i = runTile.querySelector('img'); if (i.src !== state.running.preview) i.src = state.running.preview; }
                else if (img) img.remove();
            }
            const run = state.running ? runTile : null;
            let groups = state.groups;
            if (run && !groups.length) groups = [{key: 'run', label: '', rows: []}];
            const runGroup = run ? (old ? groups.at(-1) : groups[0]) : null;
            const blocks = [], seen = new Set();
            for (const group of groups) {
                const k = String(group.label || group.key);
                seen.add(k);
                let block = blockCache.get(k);
                if (!block) { const head = el('div', 'fv-iv-day'); head.append(el('span', ''), el('span', 'fv-iv-day-n')); block = {head, grid: el('div', 'fv-iv-grid')}; blockCache.set(k, block); }
                if (group.label) {
                    if (block.head.firstChild.textContent !== group.label) block.head.firstChild.textContent = group.label;
                    blocks.push(block.head);
                }
                const nodes = [];
                if (group === runGroup && !old) nodes.push(run);
                for (const row of group.rows) {
                    const tile = tileFor(row);
                    tile.classList.toggle('fv-iv-current', row.file === state.shownFile && !state.selecting);
                    tile.classList.toggle('fv-iv-picked', state.picked.has(row.file));
                    tile.classList.toggle('fv-iv-fav', state.meta.isFav(row.file));
                    const open = tile.firstChild, words = promptOfRow(row);
                    tip(open, words ? (words.length > 40 ? words.slice(0, 40) + '…' : words) : t('Picture', '图片'),
                        state.selecting ? t('Click to pick; Shift picks a run', '点选；按住 Shift 连选') : t('Show it on the stage; pick several to compare', '在中间显示；多选即可对比'));
                    open.setAttribute('aria-pressed', String(state.selecting ? state.picked.has(row.file) : row.file === state.shownFile));
                    open.setAttribute('aria-selected', String(state.selecting ? state.picked.has(row.file) : row.file === state.shownFile));
                    nodes.push(tile);
                }
                if (group === runGroup && old) nodes.push(run);
                reconcile(block.grid, nodes);
                blocks.push(block.grid);
            }
            for (const k of [...blockCache.keys()]) if (!seen.has(k)) blockCache.delete(k);
            reconcile(grid, blocks);
            for (const [file, tile] of tiles) if (!order.includes(file) && !state.allFiles.has(file)) originals?.unobserve(tile), tiles.delete(file), tile.remove();
            empty.hidden = state.rows.length > 0 || !!state.running || !!state.loading;
            empty.replaceChildren(icon(state.total ? 'search' : 'asref'), el('span', '', state.total ? t('Nothing matches', '没有符合条件的图片') : t('Pictures you make appear here', '生成的图片会出现在这里')));
        } else {
            empty.hidden = true;
            const rows = state.trash;
            const headRow = el('div', 'fv-iv-trash-head');
            const back = button('fv-iv-text-btn', {iconName: 'left', label: t('Works', '作品'), name: t('Back to works', '回到作品'), use: t('Leave recently deleted', '离开最近删除'), key: 'Esc'});
            back.addEventListener('click', () => onTrash(false));
            headRow.append(back, el('span', 'fv-iv-grow'));
            if (rows.length) { const all = button('fv-iv-text-btn fv-iv-danger', {label: t('Delete now', '立即删除'), name: t('Delete now', '立即删除'), use: t('Remove these for good, without waiting', '不再等待，永久删除这些图片')}); all.addEventListener('click', () => onPurge(null)); headRow.append(all); }
            const note = el('p', 'fv-iv-trash-note', t(`Deleted pictures stay here for ${Math.round(state.holdSeconds)} s, then they are gone.`, `删除的图片在这里保留 ${Math.round(state.holdSeconds)} 秒，之后永久删除。`));
            const list = el('div', 'fv-iv-trash-list');
            for (const h of rows) {
                const item = el('div', 'fv-iv-trash-row'); const th = el('img'); th.alt = ''; th.src = pictureURL(h.row.file);
                const left = Math.max(0, Math.ceil((h.at + state.holdSeconds * 1000 - Date.now()) / 1000));
                const tx = el('span', 'fv-iv-trash-tx'); tx.append(el('b', '', promptOfRow(h.row) || h.row.name || h.row.file), el('small', '', t(`Gone in ${left} s`, `${left} 秒后永久删除`)));
                const restore = button('fv-iv-text-btn', {iconName: 'restore', label: t('Restore', '恢复'), name: t('Restore', '恢复'), use: t('Put it back in works', '放回作品里')});
                restore.addEventListener('click', () => onRestore([h.row.file]));
                item.append(th, tx, restore); list.append(item);
            }
            if (!rows.length) { const none = el('div', 'fv-iv-empty'); none.append(icon('trash'), el('span', '', t('Nothing was deleted just now', '最近没有删除的图片'))); list.append(none); }
            trashView.replaceChildren(headRow, note, list);
        }
        // Foot.
        batch.hidden = !state.selecting; links.hidden = state.selecting;
        picked.textContent = t(`${state.picked.size} selected`, `已选 ${state.picked.size} 张`);
        const none = state.picked.size === 0;
        const allFav = !none && [...state.picked].every(f => state.meta.isFav(f));
        favBtn.setAttribute('aria-disabled', String(none)); deleteBtn.setAttribute('aria-disabled', String(none));
        tip(favBtn, allFav ? t('Remove star', '取消收藏') : t('Star', '收藏'), none ? t('Pick a picture first', '先选中图片') : allFav ? t('Take these out of favourites', '把选中的移出收藏') : t('Keep these in favourites', '把选中的加入收藏'), 'F');
        tip(deleteBtn, t('Delete', '删除'), none ? t('Pick a picture first', '先选中图片') : t('Move to recently deleted; can be undone', '移到最近删除，可以撤销'), 'Delete');
        trashN.textContent = state.trash.length ? String(state.trash.length) : '';
        trashLink.setAttribute('aria-pressed', String(state.view === 'trash'));
    }
    return {element: root, paint, query, focusSearch: () => { query.focus(); query.select(); }, collapseButton: collapse};
}
