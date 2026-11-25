// The creation panel's works page: the library's videos in the same column and tiles as the image view's works
// (same classes, so the shared column looks one way), read from the library the page already lists. It owns no data:
// a tile shows a video on the stage (onShow), search filters by the work's name, the star filter reads the library's favourites.
// The library's own calls come in as options (load: the page of videos, thumb: a video's cover, favorite: save a star,
// watch: loads the videos near the screen ahead), so the page needs no engine to be tried.
//
// What the earlier Recent videos strip did, this page does too:
//   - runs of one task fold under their newest video, which opens them when pressed (task_groups.js);
//   - two videos picked together (Ctrl/⌘ or Shift and a click, a 500 ms press, or the tile's check) are reported by
//     onPick(rows) in the order picked, [] when the picks are cleared; picked() and clearPicks({quiet}) are the same;
//   - a read that fails leaves a mark that reads again on a press (only the reason when the reply asks for a restart);
//   - a tile's hover name is the opening words of its prompt (24 letters), from named(row);
//   - onChange() is called when the rows or the shown video changed (also after the page's own reload);
//   - entry: a button of the owner's (the full library) put in the foot, left, where the column shows it whenever it is open;
//   - sort (newest, oldest, favourites first) and select (every tile's click picks, for two to compare) sit beside the search, as in the picture view;
//   - rows() are the videos newest first, index() is the place of the shown one.
import { el, icon, t, tip, button } from './image_view_ui.js';
import { glyph } from './context_menu.js';
import { applyTasks, toggledTask } from './task_groups.js';

const idle = () => ({observe() {}, unobserve() {}, disconnect() {}});

export function studioWorks({load, thumb, favorite = async () => true, onShow, onPick = () => {}, onChange = () => {}, entry = null, named = () => Promise.resolve(''), watch = idle, limit = 48}) {
    const root = el('div', 'fv-iv-works');
    const head = el('div', 'fv-iv-panel-head'), title = el('h2', 'fv-iv-panel-title', t('Works', '作品')), count = el('span', 'fv-iv-count');
    head.append(title, count);
    const searchRow = el('label', 'fv-iv-search'), query = el('input'); query.type = 'search'; query.placeholder = t('Search prompts', '搜索提示词');
    tip(query, t('Search', '搜索'), t('Find videos by their prompt', '按提示词查找视频')); searchRow.append(icon('search'), query);
    const SORTS = {new: ['Newest first', '最新在前'], old: ['Oldest first', '最早在前'], fav: ['Favourites first', '收藏在前']};
    const sortBtn = button('fv-iv-icon-btn', {iconName: 'sort'});
    const selectBtn = button('fv-iv-icon-btn', {iconName: 'select', name: t('Select', '选择'), use: t('Pick two videos to compare them side by side', '选两条视频并排对比')});
    selectBtn.setAttribute('aria-pressed', 'false');
    const tools = el('div', 'fv-iv-works-tools'); tools.append(searchRow, sortBtn, selectBtn);
    const sortMenu = el('div', 'fv-iv-menu'); sortMenu.hidden = true; sortMenu.setAttribute('role', 'menu');
    const chips = el('div', 'fv-iv-chips'); chips.setAttribute('role', 'tablist');
    const chipAll = button('fv-iv-chip', {}), chipFav = button('fv-iv-chip', {});
    chipAll.append(el('span', 'fv-iv-btn-label', t('All', '全部'))); chipFav.append(el('span', 'fv-iv-btn-label', t('Favourites', '收藏')));
    for (const b of [chipAll, chipFav]) b.setAttribute('role', 'tab');
    chips.append(chipAll, chipFav);
    const body = el('div', 'fv-iv-works-body'), grid = el('div', 'fv-iv-grid');
    const empty = el('div', 'fv-iv-empty'); empty.hidden = true;
    body.append(grid, empty);
    // Foot, as the picture view's: a link on the left (the full library); the right stays empty (the picture view's Recently deleted has no
    // counterpart here). The shown video's star is a key of the bar under the picture (setFavorite).
    const foot = el('div', 'fv-iv-panel-foot'), links = el('div', 'fv-iv-links');
    if (entry) entry.classList.add('fv-iv-btn', 'fv-iv-link');
    links.append(...[entry, el('span', 'fv-iv-grow')].filter(Boolean)); foot.append(links);
    root.append(head, tools, chips, sortMenu, body, foot);

    let rows = [], selected = '', filter = 'all', sort = 'new', selecting = false, words = '', request = 0, disposed = false, order = [], unfolded = null, picks = [], failMark = null;
    const tiles = new Map(), prompts = new Map(), byVideo = new Map(), names = new Map(), unnamed = t('Video', '视频');
    const originals = watch(grid, tile => { const row = byVideo.get(tile.dataset.file); return row ? {row} : null; });
    const key = row => row.video;
    sortMenu.append(...Object.keys(SORTS).map(name => {
        const item = el('button', 'fv-iv-menu-item'); item.type = 'button'; item.setAttribute('role', 'menuitemradio'); item.dataset.sort = name;
        item.append(el('span', '', t(...SORTS[name])), icon('check', 'fv-iv-menu-mark'));
        item.addEventListener('click', () => { sortMenu.hidden = true; sort = name; paint(); sortBtn.focus(); });
        return item;
    }));
    const onPointer = event => { if (!sortMenu.hidden && !sortMenu.contains(event.target) && !sortBtn.contains(event.target)) sortMenu.hidden = true; };
    document.addEventListener('pointerdown', onPointer, true);
    sortBtn.addEventListener('click', () => { sortMenu.hidden = !sortMenu.hidden; if (!sortMenu.hidden) sortMenu.querySelector('[aria-checked=true]')?.focus(); });
    sortMenu.addEventListener('keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); sortMenu.hidden = true; sortBtn.focus(); } });
    function paintSort() {
        for (const item of sortMenu.querySelectorAll('[data-sort]')) item.setAttribute('aria-checked', String(item.dataset.sort === sort));
        // (Whole sentences, one for each order: the catalogue collects plain strings, not a sentence put together.)
        const now = {new: t('Now: Newest first', '当前：最新在前'), old: t('Now: Oldest first', '当前：最早在前'), fav: t('Now: Favourites first', '当前：收藏在前')};
        tip(sortBtn, t('Sort', '排序'), now[sort]);
    }
    // Select: a press on a tile picks it (two at most, to compare) until select is pressed again or Esc.
    function setSelecting(on) {
        selecting = on; root.dataset.selecting = String(on); selectBtn.setAttribute('aria-pressed', String(on));
        tip(selectBtn, on ? t('Done', '完成') : t('Select', '选择'), on ? t('Leave selection', '退出多选') : t('Pick two videos to compare them side by side', '选两条视频并排对比'));
        if (!on) clearPicks();
    }
    selectBtn.addEventListener('click', () => setSelecting(!selecting));
    root.addEventListener('keydown', event => { if (event.key === 'Escape' && selecting) { event.stopPropagation(); setSelecting(false); } });
    const nameOf = video => names.get(video) || unnamed;

    // ── picking two videos to compare ──
    function paintPicks() {
        for (const [video, tile] of tiles) {
            const at = picks.indexOf(video);
            if (at >= 0) tile.dataset.picked = String(at + 1); else delete tile.dataset.picked;
            tile.querySelector('.fv-recent-check')?.setAttribute('aria-pressed', String(at >= 0));
        }
        root.dataset.picking = String(picks.length > 0);
    }
    // Picks one more (or takes it back); a third pick replaces the first.
    function pick(video) {
        if (!byVideo.has(video)) return;
        if (picks.length === 0 && selected && selected !== video && byVideo.has(selected)) picks = [selected];
        picks = picks.includes(video) ? picks.filter(v => v !== video) : [...picks, video].slice(-2);
        paintPicks(); onPick(picks.map(v => byVideo.get(v)));
    }
    function clearPicks({quiet = false} = {}) { if (!picks.length) return; picks = []; paintPicks(); if (!quiet) onPick([]); }

    // ── tasks: the newest video of a run covers the others ──
    function fold() {
        applyTasks({cards: tiles, rows: byVideo, order, open: unfolded, selected, t, label: nameOf});
        for (const [video, tile] of tiles) {
            const open = tile.querySelector('.fv-iv-tile-open'), cover = tile.classList.contains('fv-task-head');
            // applyTasks marks the tile; the press target is the button inside it.
            for (const attribute of ['aria-label', 'aria-expanded', 'aria-pressed']) {
                const value = tile.getAttribute(attribute);
                if (value !== null) { open.setAttribute(attribute, value); tile.removeAttribute(attribute); } else if (attribute !== 'aria-label') open.removeAttribute(attribute);
            }
            if (!cover) open.setAttribute('aria-pressed', String(video === selected));
            const badge = tile.querySelector('.fv-iv-tile-n');
            if (cover) {
                const group = order.filter(item => byVideo.get(item)?.task && byVideo.get(item).task === byVideo.get(video).task).length;
                const n = badge || tile.appendChild(el('span', 'fv-iv-tile-n')); n.textContent = String(group);
                tip(open, open.getAttribute('aria-label') || nameOf(video), t('Show or fold the results of this run', '展开或收起这次生成的结果'));
            } else {
                badge?.remove();
                tip(open, nameOf(video), t('Show this video on the stage', '在画面里显示这条视频'));
            }
            open.dataset.fvTipLabel = 'auto';
        }
    }
    // The work's name: its prompt's opening words, read once per video (it also feeds the search).
    // The prompts are read a few at a time (a library of many videos would otherwise ask for every one at once).
    const NAME_READS = 4; let reading = 0; const waiting = [];
    function readNamed(run) {
        return new Promise((resolve, reject) => {
            const start = () => { reading++; Promise.resolve().then(run).then(resolve, reject).finally(() => { reading--; waiting.shift()?.(); }); }; // (a throw from run() releases the slot too)
            if (reading < NAME_READS) start(); else waiting.push(start);
        });
    }
    function nameCard(video) {
        const row = byVideo.get(video);
        if (!row || prompts.has(video)) return;
        prompts.set(video, '');
        readNamed(() => Promise.resolve(named(row))).then(text => {
            const prompt = String(text || ''), letters = Array.from(prompt.replace(/\s+/g, ' ').trim());
            prompts.set(video, prompt);
            names.set(video, letters.length ? letters.slice(0, 24).join('').trim() + (letters.length > 24 ? '…' : '') : '');
            if (disposed) return;
            if (words) paint(); else if (tiles.has(video)) fold();
        }, () => { prompts.delete(video); });
    }

    function tileFor(row) {
        let tile = tiles.get(key(row));
        if (tile) return tile;
        const video = key(row);
        tile = el('div', 'fv-iv-tile'); tile.dataset.file = video; tile.dataset.video = video;
        const open = el('button', 'fv-iv-tile-open'); open.type = 'button';
        const image = el('img'); image.alt = ''; image.loading = 'lazy'; image.decoding = 'async'; image.draggable = false;
        image.dataset.loaded = 'false'; image.addEventListener('load', () => { image.dataset.loaded = 'true'; }); image.addEventListener('error', () => { image.dataset.loaded = 'true'; image.dataset.missing = 'true'; });
        image.src = thumb(row);
        open.append(image); tile.append(open);
        const star = el('span', 'fv-iv-tile-star'); star.append(icon('star')); tile.append(star);
        // Ctrl/⌘ or Shift and a click, a long press, or the tile's check: pick it with another to compare.
        const check = el('span', 'fv-recent-check'); check.setAttribute('role', 'button'); check.setAttribute('aria-pressed', 'false');
        check.setAttribute('aria-label', t('Pick to compare', '选中以对比'));
        check.dataset.fvTipName = t('Pick', '选中'); check.dataset.fvTipUse = t('Pick two videos to compare them side by side', '选两条视频并排对比');
        check.addEventListener('click', event => { event.stopPropagation(); pick(video); });
        tile.append(check);
        let held = null, longPressed = false;
        open.addEventListener('pointerdown', event => {
            if (event.button !== 0) return; longPressed = false;
            held = setTimeout(() => { longPressed = true; pick(video); }, 500);
        });
        for (const type of ['pointerup', 'pointerleave', 'pointercancel']) open.addEventListener(type, () => clearTimeout(held));
        for (const type of ['pointerenter', 'focus']) open.addEventListener(type, () => nameCard(video));
        open.addEventListener('click', event => {
            if (longPressed) { longPressed = false; return; }
            if (event.ctrlKey || event.metaKey || event.shiftKey || selecting) { pick(video); return; }
            if (picks.length) clearPicks({quiet: true});
            // A cover with others under it opens them in place; any other tile is the choice.
            const next = toggledTask(byVideo, order, video, unfolded);
            onShow?.(byVideo.get(video) || row);
            if (next !== unfolded || tile.classList.contains('fv-task-head')) { unfolded = next; fold(); }
        });
        tiles.set(video, tile); originals.observe(tile); return tile;
    }
    // The videos in the order the column shows them (the sort chosen; no filter): the stage's previous and next follow it.
    const ordered = () => (sort === 'old' ? rows.slice().reverse() : sort === 'fav' ? rows.slice().sort((a, b) => Number(!!b.favorite) - Number(!!a.favorite)) : rows.slice());
    const shown = () => {
        const list = rows.filter(row => (filter !== 'fav' || row.favorite) && (!words || (prompts.get(key(row)) || '').toLowerCase().includes(words)));
        if (sort === 'old') list.reverse(); else if (sort === 'fav') list.sort((a, b) => Number(!!b.favorite) - Number(!!a.favorite));
        return list;
    };
    function paint() {
        const list = shown();
        for (const b of [chipAll, chipFav]) { const on = (b === chipFav) === (filter === 'fav'); b.setAttribute('aria-selected', String(on)); b.setAttribute('aria-pressed', String(on)); }
        count.textContent = rows.length ? String(rows.length) : '';
        const keep = new Set(list.map(key));
        for (const [file, tile] of tiles) if (!keep.has(file)) { originals.unobserve(tile); tile.remove(); tiles.delete(file); }
        grid.replaceChildren(...list.map(row => { const tile = tileFor(row); tile.classList.toggle('fv-iv-current', key(row) === selected); tile.classList.toggle('fv-iv-fav', !!row.favorite); return tile; }));
        order = list.map(key);
        fold(); paintPicks(); paintSort();
        // No work at all: the page stays empty; a search or the star that hides every work says so.
        empty.hidden = list.length > 0 || !rows.length;
        empty.replaceChildren(...(empty.hidden ? [] : [icon('search'), el('span', '', t('Nothing matches', '没有符合条件的视频'))]));
    }
    // The works page is too narrow for the sentence: a small mark beside the search names the failure on hover and reads again on a press (disabled
    // while it reads; it stays if the read fails again and goes once it works). A reply that asks for a restart has nothing
    // to retry, so its mark is not a control, only the reason on hover.
    function failed(error) {
        const restart = Boolean(error?.restart);
        if (!failMark || failMark.dataset.restart !== String(restart)) {
            failMark?.remove();
            failMark = el(restart ? 'span' : 'button', 'fv-recent-fail');
            failMark.dataset.restart = String(restart);
            if (restart) failMark.setAttribute('role', 'img');
            else { failMark.type = 'button'; failMark.onclick = () => { failMark.disabled = true; void refresh(); }; }
            failMark.innerHTML = glyph('alert');
        }
        const name = restart ? error.message : t('Creations could not be loaded', '无法读取作品');
        const use = restart ? '' : t('Read the creations again', '重新读取作品');
        failMark.setAttribute('aria-label', use ? `${name}. ${use}` : name);
        failMark.dataset.fvTipName = name;
        if (use) failMark.dataset.fvTipUse = use; else delete failMark.dataset.fvTipUse;
        if (!restart) failMark.disabled = false;
        tools.prepend(failMark);
    }
    async function refresh() {
        const mine = ++request;
        try {
            const data = await load(t, {limit});
            if (disposed || mine !== request) return rows;
            rows = (data.items || []).filter(row => row?.video && row.kind !== 'image');
            byVideo.clear(); for (const row of rows) byVideo.set(key(row), row);
            for (const video of [...tiles.keys()]) if (!byVideo.has(video)) { originals.unobserve(tiles.get(video)); tiles.get(video).remove(); tiles.delete(video); }
            // The tiles already made show the new row (a star, a task).
            if (!rows.some(row => row.task === unfolded)) unfolded = null;
            picks = picks.filter(video => byVideo.has(video));
            failMark?.remove(); failMark = null;
            for (const row of rows) nameCard(key(row));
        } catch (error) { if (!disposed && mine === request) failed(error); /* the list stays as it was */ }
        paint(); if (!disposed && mine === request) onChange(); return rows;
    }
    // The star of the shown video (pressed from the bar under the picture): kept in the library, marked on its tile, undone if it is refused.
    async function setFavorite(video, value) {
        const row = byVideo.get(video), before = !!row?.favorite;
        if (row) { row.favorite = value; paint(); }
        try { if (await favorite(row || {video}, value) === false) throw new Error('refused'); }
        catch (error) { if (row) { row.favorite = before; paint(); } throw error; }
    }
    query.addEventListener('input', () => { words = query.value.trim().toLowerCase(); paint(); });
    chipAll.addEventListener('click', () => { filter = 'all'; paint(); });
    chipFav.addEventListener('click', () => { filter = 'fav'; paint(); });
    const completed = event => { if (event.detail?.value?.video) void refresh(); };
    const changed = () => { void refresh(); };
    window.addEventListener('freevideo-result', completed);
    window.addEventListener('freevideo-library-deleted', changed); window.addEventListener('freevideo-library-restored', changed);
    return {
        element: root, refresh, setFavorite, rows: () => ordered(), index: () => ordered().findIndex(row => key(row) === selected), focusSearch: () => query.focus(),
        picked: () => picks.map(video => byVideo.get(video)).filter(Boolean), clearPicks,
        setSelected(video) { selected = video || ''; for (const [file, tile] of tiles) tile.classList.toggle('fv-iv-current', file === selected); fold(); tiles.get(selected)?.scrollIntoView?.({block: 'nearest'}); onChange(); },
        dispose() { disposed = true; document.removeEventListener('pointerdown', onPointer, true); originals.disconnect(); window.removeEventListener('freevideo-result', completed); window.removeEventListener('freevideo-library-deleted', changed); window.removeEventListener('freevideo-library-restored', changed); },
    };
}
