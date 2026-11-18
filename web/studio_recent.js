import { libraryPage, videoThumbnailURL } from './library.js';
import { icon } from './result_actions.js';
import { glyph } from './context_menu.js';
import { applyTasks, toggledTask } from './task_groups.js';
import { mediaQueue, mediaURL, watchCards } from './work_media.js';

const KEY = 'fv-studio-recent-rail';
const el = (tag, text, cls) => {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (cls) element.className = cls;
    return element;
};

// rail: the history stays beside the picture as a column of thumbnails, nothing written on them; a card's hover and
// spoken name is the work's name (the opening words of its prompt, from named(row); "Video" without one).
// Without rail, the earlier behaviour: a column on wide windows, a panel over the preview on narrower ones.
// onPick(rows): the videos picked together (Ctrl/⌘ or Shift and a click, a long press, or a card's check), at most two,
// in the order picked; [] when the picks are cleared. A plain click is a single choice (onSelect) and clears them.
export function createRecentVideos(t, {body, onSelect, onPick = () => {}, rail = false, onChange = () => {}, named = () => Promise.resolve('')}) {
    const element = el('aside', null, 'fv-recent'); element.id = 'fv-studio-recent';
    const title = t('Recent videos', '最近作品'); element.setAttribute('aria-label', title);
    const heading = el('div', title, 'fv-recent-head');
    const list = el('div', null, 'fv-recent-list');
    const message = el('p', '', 'fv-library-message'); message.setAttribute('role', 'status');
    element.append(heading, list);
    const toggle = el('button', null, 'fv-recent-toggle'); toggle.type = 'button';
    toggle.append(icon(rail ? 'chevron' : 'sidebar')); toggle.setAttribute('aria-controls', element.id);
    element.tabIndex = -1;
    if (rail) { element.classList.add('fvs-rail'); element.append(toggle); }
    // Wide windows give the list its own column, remembered across visits. Narrower windows show it
    // as a panel over the preview; it starts closed each time, so a saved column never covers the picture.
    const wide = window.matchMedia(rail ? '(min-width:0px)' : '(min-width:1680px)');
    let expanded = !rail, open = false, selected = null, disposed = false, request = null, resizeTimer = null;
    let picked = [];
    function paintPicks() {
        for (const [video, card] of cards) {
            const at = picked.indexOf(video);
            if (at >= 0) { card.dataset.picked = String(at + 1); } else delete card.dataset.picked;
            card.querySelector('.fv-recent-check')?.setAttribute('aria-pressed', String(at >= 0));
        }
        element.dataset.picking = String(picked.length > 0);
    }
    // Picks one more (or takes it back); a third pick replaces the first.
    function pick(video) {
        if (!byVideo.has(video)) return;
        if (picked.length === 0 && selected && selected !== video && byVideo.has(selected)) picked = [selected];
        picked = picked.includes(video) ? picked.filter(v => v !== video) : [...picked, video].slice(-2);
        paintPicks(); onPick(picked.map(v => byVideo.get(v)));
    }
    function clearPicks({quiet = false} = {}) { if (!picked.length) return; picked = []; paintPicks(); if (!quiet) onPick([]); }
    const cards = new Map(), byVideo = new Map();
    let order = [], unfolded = null;
    try { const saved = localStorage.getItem(rail ? KEY + '-wide' : KEY); expanded = rail ? saved === 'true' : saved !== 'false'; } catch {}
    // The rail is thumbnails only: with nothing written on the cards a wider column would add nothing, so it has no widen key.
    if (rail) { expanded = false; toggle.hidden = true; }
    function visibility() {
        const shown = wide.matches ? expanded : open;
        body.dataset.recent = String(expanded);
        body.dataset.recentPanel = String(!wide.matches && open);
        if (rail) {
            element.dataset.expanded = String(expanded);
            toggle.setAttribute('aria-pressed', String(expanded)); toggle.removeAttribute('aria-expanded');
            const label = expanded ? t('Narrow the history', '收窄历史') : t('Widen the history', '展开历史');
            toggle.setAttribute('aria-label', label);
            toggle.dataset.fvTipName = label;
            toggle.dataset.fvTipUse = expanded ? t('Thumbnails only, a larger picture', '只留缩略图，画面更大') : t('Show dates and sizes', '显示时间和尺寸');
            toggle.title = `${label} · ${toggle.dataset.fvTipUse}`;
            return;
        }
        // The column is a pressed state of the layout; the panel is a disclosure that opens and closes.
        toggle.setAttribute(wide.matches ? 'aria-pressed' : 'aria-expanded', String(shown));
        toggle.removeAttribute(wide.matches ? 'aria-expanded' : 'aria-pressed');
        const label = shown ? t('Hide recent videos', '收起最近作品') : t('Show recent videos', '显示最近作品');
        toggle.title = label; toggle.setAttribute('aria-label', label);
        element.inert = !shown; element.setAttribute('aria-hidden', String(!shown));
    }
    visibility();
    // The panel hangs below the preview's tools, its right edge in line with theirs.
    function place() {
        const tools = toggle.parentElement?.getBoundingClientRect(), frame = body.getBoundingClientRect();
        if (!tools) return;
        body.style.setProperty('--fv-recent-top', `${Math.max(8, Math.round(tools.bottom - frame.top + 8))}px`);
        body.style.setProperty('--fv-recent-right', `${Math.max(8, Math.round(frame.right - tools.right))}px`);
    }
    function showPanel() {
        if (open || wide.matches) return;
        place(); open = true; visibility();
        const card = cards.get(selected) || cards.values().next().value;
        (card || element).focus({preventScroll: true});
        card?.scrollIntoView({block: 'nearest'});
        element.scrollIntoView({block: 'nearest'});
    }
    // Returns whether a panel was open. Escape and a chosen video hand focus back to the button.
    function hidePanel({restoreFocus = false} = {}) {
        if (!open) return false;
        open = false; visibility();
        if (restoreFocus) toggle.focus({preventScroll: true});
        return true;
    }
    toggle.onclick = () => {
        if (!wide.matches) { if (open) hidePanel(); else showPanel(); return; }
        // Let fitPreview follow the 200 ms grid change without a second size transition.
        clearTimeout(resizeTimer); body.classList.add('fv-recent-resizing');
        resizeTimer = setTimeout(() => body.classList.remove('fv-recent-resizing'), 250);
        expanded = !expanded; visibility();
        try { localStorage.setItem(rail ? KEY + '-wide' : KEY, String(expanded)); } catch {}
    };
    // A press anywhere else, or focus moving elsewhere, closes the panel without taking focus.
    const outside = event => {
        if (open && !element.contains(event.target) && !toggle.contains(event.target)) hidePanel();
    };
    element.addEventListener('focusout', event => {
        const next = event.relatedTarget;
        if (open && next && !element.contains(next) && next !== toggle) hidePanel();
    });
    document.addEventListener('pointerdown', outside, true);
    const layout = () => { open = false; visibility(); };
    const resized = () => { if (open) place(); };
    wide.addEventListener('change', layout);
    window.addEventListener('resize', resized);
    const queue = mediaQueue();
    const thumbnails = new IntersectionObserver(entries => {
        for (const {target, isIntersecting} of entries) if (isIntersecting) {
            queue.thumbnail(target); target.src = target.dataset.src; thumbnails.unobserve(target); nameCard(target.dataset.video);
        }
    }, {root: list, rootMargin: '80px'});
    // Over a link, the videos of the cards on screen and near them load in the background after their thumbnails, so
    // the one picked next plays at once.
    const originals = watchCards(queue, list, card => {
        const row = byVideo.get(card.dataset.video);
        return row ? {url: mediaURL(row.video, 'output'), kind: 'video', bytes: row.bytes} : null;
    });
    // A card is named by its video's size and length; works show no date or time.
    const names = new Map(), unnamed = t('Video', '视频');
    const cardLabel = video => names.get(video) || unnamed;
    // The work's name: its prompt's opening words, read once per card when it comes into view (or is pointed at).
    function nameCard(video) {
        const row = byVideo.get(video);
        if (!row || names.has(video)) return;
        names.set(video, '');
        Promise.resolve(named(row)).then(prompt => {
            const letters = Array.from(String(prompt || '').replace(/\s+/g, ' ').trim());
            names.set(video, letters.length ? letters.slice(0, 24).join('').trim() + (letters.length > 24 ? '…' : '') : '');
            if (!disposed && cards.has(video)) fold();
        }, () => names.delete(video));
    }
    function fold() {
        applyTasks({cards, rows: byVideo, order, open: unfolded, selected, t, label: cardLabel});
        // Hover names: a cover says how many results it holds and that it opens them; any other card shows its video.
        for (const [video, card] of cards) {
            const head = card.classList.contains('fv-task-head');
            card.dataset.fvTipName = head ? card.getAttribute('aria-label') || cardLabel(video) : cardLabel(video);
            card.dataset.fvTipUse = head ? t('Show or fold the results of this run', '展开或收起这次生成的结果') : t('Show this video on the stage', '在画面里显示这条视频');
        }
    }
    function setSelected(video) {
        selected = video;
        for (const [file, card] of cards) card.setAttribute('aria-pressed', String(file === selected));
        fold();
        if (rail) cards.get(selected)?.scrollIntoView?.({block: 'nearest'});
        onChange();
    }
    async function refresh() {
        if (disposed) return [];
        request?.abort();
        const controller = new AbortController(); request = controller;
        try {
            const data = await libraryPage(t, {limit: 24, signal: controller.signal});
            if (disposed || controller.signal.aborted) return [];
            thumbnails.disconnect(); originals.disconnect(); cards.clear(); byVideo.clear(); list.replaceChildren();
            // The library API returns newest first, with the same preview metadata.
            const rows = (data.items || []).filter(row => row?.video).slice(0, 24);
            for (const row of rows) {
                const card = el('button', null, 'fv-recent-card'); card.type = 'button';
                card.setAttribute('aria-label', cardLabel(row.video));
                // Ctrl/⌘ or Shift and a click, a long press, or the card's check: pick it with another to compare.
                let held = null, longPressed = false;
                card.addEventListener('pointerdown', event => {
                    if (event.button !== 0) return; longPressed = false;
                    held = setTimeout(() => { longPressed = true; pick(row.video); }, 500);
                });
                for (const type of ['pointerup', 'pointerleave', 'pointercancel']) card.addEventListener(type, () => clearTimeout(held));
                const check = el('span', null, 'fv-recent-check'); check.setAttribute('role', 'button'); check.setAttribute('aria-pressed', 'false');
                check.setAttribute('aria-label', t('Pick to compare', '选中以对比'));
                check.dataset.fvTipName = t('Pick', '选中'); check.dataset.fvTipUse = t('Pick two videos to compare them side by side', '选两条视频并排对比');
                check.addEventListener('click', event => { event.stopPropagation(); pick(row.video); });
                card.onclick = event => {
                    if (longPressed) { longPressed = false; return; }
                    if (event.ctrlKey || event.metaKey || event.shiftKey || picked.length) {
                        if (event.ctrlKey || event.metaKey || event.shiftKey) { pick(row.video); return; }
                        clearPicks({quiet: true});
                    }
                    // A cover with others under it opens them in place and keeps the panel; any other card is the choice.
                    const next = toggledTask(byVideo, order, row.video, unfolded);
                    onSelect(row);
                    if (next !== unfolded || card.classList.contains('fv-task-head')) { unfolded = next; fold(); return; }
                    hidePanel({restoreFocus: true});
                };
                const picture = el('div', null, 'fv-library-picture');
                const image = el('img'); image.alt = ''; image.decoding = 'async';
                image.dataset.src = videoThumbnailURL(row);
                image.onerror = () => { image.hidden = true; };
                picture.append(image);
                image.dataset.video = row.video;
                for (const type of ['pointerenter', 'focus']) card.addEventListener(type, () => nameCard(row.video));
                card.append(picture, check);
                card.dataset.video = row.video;
                cards.set(row.video, card); byVideo.set(row.video, row); list.append(card); thumbnails.observe(image); originals.observe(card);
            }
            failMark = null;  // the list was rebuilt without it
            order = rows.map(row => row.video);
            if (!order.some(video => byVideo.get(video).task === unfolded)) unfolded = null;
            message.textContent = t('Videos you generate appear here.', '生成的视频会显示在这里。');
            message.hidden = rows.length > 0; list.append(message);
            element.dataset.empty = String(rows.length === 0);
            picked = picked.filter(video => byVideo.has(video)); paintPicks();
            setSelected(selected);
            onChange();
            return rows;
        } catch (error) {
            if (!disposed && !controller.signal.aborted) {
                // The library's own messages are translated; a network or reply error is not, and this column has no Refresh.
                message.textContent = error?.restart ? error.message
                    : t('Recent videos could not be loaded. FreeVideo tries again when the next video finishes.', '暂时无法读取最近作品，下一个视频生成完成后会自动再试。');
                message.hidden = false; list.append(message);
                if (rail) railFailed(error);
            }
            return [];
        }
    }
    // The rail is too narrow for the sentence: a picture-sized mark at the top of the list names the failure on hover and
    // reads again on a press (disabled while it reads; it stays if the read fails again and goes once it works). A reply
    // that asks for a restart has nothing to retry, so its mark is not a control, only the reason on hover.
    let failMark = null;
    function railFailed(error) {
        const restart = Boolean(error?.restart);
        if (!failMark || failMark.dataset.restart !== String(restart)) {
            failMark?.remove();
            failMark = el(restart ? 'span' : 'button', null, 'fv-recent-fail');
            failMark.dataset.restart = String(restart);
            if (restart) failMark.setAttribute('role', 'img');
            else { failMark.type = 'button'; failMark.onclick = () => { failMark.disabled = true; refresh(); }; }
            failMark.innerHTML = glyph('alert');  // the notices' own warning mark
        }
        const name = restart ? error.message : t('Recent videos could not be loaded', '无法读取最近作品');
        const use = restart ? '' : t('Read the recent videos again', '重新读取最近作品');
        failMark.setAttribute('aria-label', use ? `${name}. ${use}` : name);
        failMark.dataset.fvTipName = name;
        if (use) failMark.dataset.fvTipUse = use; else delete failMark.dataset.fvTipUse;
        if (!restart) failMark.disabled = false;
        list.prepend(failMark);
    }
    const completed = event => { if (event.detail?.value?.video) refresh(); };
    // A video deleted in Creations leaves this column at once; the next one moves up.
    const deleted = event => {
        const video = event.detail?.video; if (!video || disposed) return;
        cards.get(video)?.remove(); cards.delete(video); refresh();
    };
    window.addEventListener('freevideo-result', completed);
    window.addEventListener('freevideo-library-deleted', deleted);
    window.addEventListener('freevideo-library-restored', refresh);
    return {element, toggle, refresh, setSelected, clearPicks, picked: () => picked.map(v => byVideo.get(v)), dismiss: () => hidePanel({restoreFocus: true}),
        // The videos in list order (newest first) and the place of the one shown, for the history buttons.
        rows: () => order.map(video => byVideo.get(video)), index: () => order.indexOf(selected),
        dispose() {
        disposed = true; request?.abort(); thumbnails.disconnect(); originals.disconnect();
        document.removeEventListener('pointerdown', outside, true);
        wide.removeEventListener('change', layout); window.removeEventListener('resize', resized);
        clearTimeout(resizeTimer); body.classList.remove('fv-recent-resizing');
        window.removeEventListener('freevideo-result', completed);
        window.removeEventListener('freevideo-library-deleted', deleted);
        window.removeEventListener('freevideo-library-restored', refresh);
    }};
}
