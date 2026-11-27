// The creation panel's right column in the three-column layout: the shared column of the image view (web/work_column.js)
// with the videos of the library as its works page, the shown video's facts as its info page, and the queue of every view.
import { api } from '../../scripts/api.js';
import { libraryPage, videoThumbnailURL, videoDimensions, videoTimestamp } from './library.js';
import { createWorkColumn } from './work_column.js';
import { mediaQueue, mediaURL, watchCards } from './work_media.js';
import { studioWorks } from './studio_works.js';
import { queuePanel } from './image_view_queue.js';
import { jobs } from './task_store.js';
import { confirmStop } from './task_float.js';
import { pictureURL } from './picture_url.js';
import { el, t, button, tip } from './image_view_ui.js';

// The column's parts are styled in image_view.css (for .fv-iv and .fv-work-column alike); the creation panel does not load
// the image view, so it asks for the sheet itself, once (not in work_column.js: the image view's own order of sheets stays as it was).
{
    const href = new URL('./image_view.css', import.meta.url).href;
    if (![...document.querySelectorAll('link[rel=stylesheet]')].some(link => link.href === href)) {
        const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = href; document.head.append(css);
    }
}

const RAIL_THUMBS = 6;

async function saveFavorite(row, favorite) {
    const id = row.id || row.video.replace(/^FreeVideo\//, '').replace(/\/video\.mp4$/, '');
    const response = await api.fetchApi('/freevideo/library/favorite', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id, favorite})});
    if (!response.ok) return false;
    return (await response.json()).favorite === favorite;
}

/**
 * @param {object} o
 * @param {HTMLElement} o.slot        the right column's element (aside.fv-work-column)
 * @param {{setPanel: (side: string, open: boolean) => void}} o.shell
 * @param {(row: object) => void} o.onShow   a work was chosen: show it on the stage
 * @param {(row: object) => Promise<string>} o.named   the words a video was made from
 * @param {(rows: object[]) => void} [o.onPick]   videos picked together to compare (at most two, in the order picked; [] when cleared)
 * @param {(row: object) => Promise<unknown>} [o.onReuse]   fill the panel with this video's settings (the info page's button)
 * @param {() => void} [o.onChange]   the videos or the shown one changed (also after the column reloads itself)
 * @param {HTMLElement} [o.entry]   the owner's button to the full library, put beside the search
 * @param {(row: object) => string} [o.errorOf]   what went wrong with this video's run, when something did (the info page says it)
 */
export function mountStudioColumn({slot, shell, onShow, onPick, onChange, onRun, entry, named, onReuse, errorOf}) {
    // The videos on and near the screen load in the background after their covers, so the one chosen next plays at once.
    const watch = (root, rowOf) => watchCards(mediaQueue(), root, tile => {
        const hit = rowOf(tile);
        return hit ? {url: mediaURL(hit.row.video, 'output'), kind: 'video', bytes: hit.row.bytes} : null;
    });
    const works = studioWorks({load: libraryPage, thumb: videoThumbnailURL, favorite: saveFavorite, onShow, onPick, onChange, onRun, entry, named, watch});
    const info = el('div', 'fv-iv-info');
    let shown = null;
    async function paintInfo() {
        if (!shown) { info.replaceChildren(); return; }
        const row = shown, words = await Promise.resolve(named(row)).catch(() => '');
        if (row !== shown) return;
        const line = (label, value) => value ? el('p', 'fv-iv-info-line', `${label}${t(': ', '：')}${value}`) : null;
        const problem = errorOf?.(row) || '';
        const reuse = onReuse ? button('fv-iv-text-btn fv-iv-info-reuse', {label: t('Reuse settings', '复用设置'), name: t('Reuse settings', '复用设置'), use: t('Fill the panel with this video\'s prompt, size and length', '把这条视频的提示词、尺寸和时长填回面板'),
            onClick: () => { void onReuse(row); }}) : null;
        info.replaceChildren(...[el('p', 'fv-iv-info-prompt', words || ''), line(t('Size', '尺寸'), videoDimensions(row, t)), line(t('Made', '时间'), videoTimestamp(row, t)),
            problem ? el('p', 'fv-iv-info-error', problem) : null, reuse].filter(Boolean));
    }
    const queue = queuePanel({jobs, confirmStop, pictureURL, onOpen: row => {
        const go = globalThis.FreeVideoShell?.go;
        if (row.view && row.view !== 'studio' && go) { void go(row.view); return; }
        const found = row.result?.video ? works.rows().find(r => r.video === row.result.video) : null;
        if (found) onShow(found);
    }});
    const column = createWorkColumn({works, info, queue, storeKey: 'freevideo.studio.rightTab',
        onCollapse: () => shell.setPanel('right', false), onOpen: () => shell.setPanel('right', true),
        onTab: name => { if (name === 'info') void paintInfo(); }});
    slot.replaceChildren(); column.mount(slot); slot.hidden = false; column.setTab(column.tab);
    // While the panel's task runs, the folded column starts with its progress ring too, as the image view's does.
    let running = null;
    const runThumb = el('button', 'fv-iv-rail-thumb fv-iv-rail-run'); runThumb.type = 'button'; runThumb.append(el('i', 'fv-iv-ring'));
    tip(runThumb, t('Generating', '正在生成'), t('Show its progress', '查看进度')); runThumb.addEventListener('click', () => onRun?.());
    const thumbs = () => column.railThumbs.replaceChildren(...(running ? [runThumb] : []), ...works.rows().slice(0, running ? RAIL_THUMBS - 1 : RAIL_THUMBS).map(row => {
        const b = el('button', 'fv-iv-rail-thumb'); b.type = 'button'; const i = el('img'); i.alt = ''; i.draggable = false; i.src = videoThumbnailURL(row); b.append(i);
        b.classList.toggle('fv-iv-current', row.video === shown?.video); b.addEventListener('click', () => onShow(row)); return b;
    }));
    const refresh = async () => { const rows = await works.refresh(); thumbs(); return rows; };
    return {
        refresh, setFavorite: (row, value) => works.setFavorite(row.video, value), rows: () => works.rows(), index: () => works.index(), picked: () => works.picked(), clearPicks: opts => works.clearPicks(opts),
        // The stage shows this video now.
        setRunning(value) {
            const was = Boolean(running); running = value || null; works.setRunning(value);
            if (running) runThumb.style.setProperty('--p', String(Math.max(0, Math.min(1, Number(running.fraction) || 0))));
            if (was !== Boolean(running)) thumbs();
        },
        setShown(row) { shown = row || null; works.setSelected(row?.video); thumbs(); if (column.tab === 'info') void paintInfo(); },
        dispose() { works.dispose(); queue.dispose?.(); },
    };
}
