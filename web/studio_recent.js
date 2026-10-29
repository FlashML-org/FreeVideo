import { libraryPage, videoTimestamp, videoDimensions, videoThumbnailURL } from './library.js';
import { icon } from './result_actions.js';

const KEY = 'fv-studio-recent-rail';
const el = (tag, text, cls) => {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (cls) element.className = cls;
    return element;
};

export function createRecentVideos(t, {body, onSelect}) {
    const element = el('aside', null, 'fv-recent'); element.id = 'fv-studio-recent';
    const title = t('Recent videos', '最近作品'); element.setAttribute('aria-label', title);
    const heading = el('div', title, 'fv-recent-head');
    const list = el('div', null, 'fv-recent-list');
    const message = el('p', '', 'fv-library-message'); message.setAttribute('role', 'status');
    element.append(heading, list);
    const toggle = el('button', null, 'fv-recent-toggle'); toggle.type = 'button';
    toggle.append(icon('sidebar')); toggle.setAttribute('aria-controls', element.id);
    element.tabIndex = -1;
    // Wide windows give the list its own column, remembered across visits. Narrower windows show it
    // as a panel over the preview; it starts closed each time, so a saved column never covers the picture.
    const wide = window.matchMedia('(min-width:1680px)');
    let expanded = true, open = false, selected = null, disposed = false, request = null, resizeTimer = null;
    const cards = new Map();
    try { expanded = localStorage.getItem(KEY) !== 'false'; } catch {}
    function visibility() {
        const shown = wide.matches ? expanded : open;
        body.dataset.recent = String(expanded);
        body.dataset.recentPanel = String(!wide.matches && open);
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
        try { localStorage.setItem(KEY, String(expanded)); } catch {}
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
    const thumbnails = new IntersectionObserver(entries => {
        for (const {target, isIntersecting} of entries) if (isIntersecting) {
            target.src = target.dataset.src; thumbnails.unobserve(target);
        }
    }, {root: list, rootMargin: '80px'});
    function setSelected(video) {
        selected = video;
        for (const [file, card] of cards) card.setAttribute('aria-pressed', String(file === selected));
    }
    async function refresh() {
        if (disposed) return [];
        request?.abort();
        const controller = new AbortController(); request = controller;
        try {
            const data = await libraryPage(t, {limit: 24, signal: controller.signal});
            if (disposed || controller.signal.aborted) return [];
            thumbnails.disconnect(); cards.clear(); list.replaceChildren();
            // The library API returns newest first, with the same preview metadata.
            const rows = (data.items || []).filter(row => row?.video).slice(0, 24);
            for (const row of rows) {
                const card = el('button', null, 'fv-recent-card'); card.type = 'button';
                const date = videoTimestamp(row, t), dimensions = videoDimensions(row);
                card.setAttribute('aria-label', `${date} · ${dimensions}`);
                card.onclick = () => { onSelect(row); hidePanel({restoreFocus: true}); };
                const picture = el('div', null, 'fv-library-picture');
                const image = el('img'); image.alt = ''; image.decoding = 'async';
                image.dataset.src = videoThumbnailURL(row);
                image.onerror = () => { image.hidden = true; };
                picture.append(image);
                card.append(picture, el('span', date), el('small', dimensions));
                cards.set(row.video, card); list.append(card); thumbnails.observe(image);
            }
            message.textContent = t('Videos you generate appear here.', '生成的视频会显示在这里。');
            message.hidden = rows.length > 0; list.append(message);
            setSelected(selected);
            return rows;
        } catch (error) {
            if (!disposed && !controller.signal.aborted) {
                // The library's own messages are translated; a network or reply error is not, and this column has no Refresh.
                message.textContent = error?.restart ? error.message
                    : t('Recent videos could not be loaded. FreeVideo tries again when the next video finishes.', '暂时无法读取最近作品，下一个视频生成完成后会自动再试。');
                message.hidden = false; list.append(message);
            }
            return [];
        }
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
    return {element, toggle, refresh, setSelected, dismiss: () => hidePanel({restoreFocus: true}), dispose() {
        disposed = true; request?.abort(); thumbnails.disconnect();
        document.removeEventListener('pointerdown', outside, true);
        wide.removeEventListener('change', layout); window.removeEventListener('resize', resized);
        clearTimeout(resizeTimer); body.classList.remove('fv-recent-resizing');
        window.removeEventListener('freevideo-result', completed);
        window.removeEventListener('freevideo-library-deleted', deleted);
        window.removeEventListener('freevideo-library-restored', refresh);
    }};
}
