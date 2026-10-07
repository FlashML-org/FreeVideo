import { api } from '../../scripts/api.js';
import { closeDialog } from './motion.js';
import { resultActions, previewBadge } from './result_actions.js';

const style = document.createElement('link');
style.rel = 'stylesheet'; style.href = new URL('./library.css', import.meta.url).href; document.head.append(style);
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const button = (text, action, cls = '') => { const b = el('button', text, cls); b.type = 'button'; b.onclick = action; return b; };
const view = file => {
    const split = file.lastIndexOf('/');
    return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(split + 1), subfolder: file.slice(0, split), type: 'output'}));
};
let opened;

export async function latestVideo() {
    const response = await api.fetchApi('/freevideo/library?limit=1');
    if (!response.ok) return null;
    return (await response.json()).items?.[0] || null;
}

// Earlier videos can get their workflow embedded, but only after the user confirms it.
async function workflowBackfill(collection, t) {
    const status = async () => { try { const r = await api.fetchApi('/freevideo/workflow-backfill'); return r.ok ? r.json() : null; } catch { return null; } };
    const post = action => api.fetchApi('/freevideo/workflow-backfill', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action})});
    let value = await status();
    if (!value || value.disabled || value.dismissed || (!value.count && value.state !== 'running')) return;
    const box = el('div', null, 'fv-backfill'); box.setAttribute('role', 'status');
    const heading = el('strong'), text = el('p'), actions = el('div', null, 'fv-backfill-actions');
    box.append(heading, text, actions); collection.prepend(box);
    const gb = bytes => (bytes / 2 ** 30).toFixed(bytes < 2 ** 30 ? 2 : 1);
    async function follow() {
        heading.textContent = t('Adding workflows…', '正在补写工作流…');
        text.textContent = t('Each original video is kept next to it as video.before-workflow.mp4.', '每个原视频都会保留在同一目录，文件名为 video.before-workflow.mp4。');
        actions.replaceChildren();
        while ((value = await status()) && value.state === 'running') await new Promise(resolve => setTimeout(resolve, 1500));
        const result = value?.result || {};
        heading.textContent = result.embedded ? t(`Workflows added to ${result.embedded} videos`, `已为 ${result.embedded} 个视频补上工作流`) : t('No video was changed', '没有视频被改动');
        text.textContent = result.embedded
            ? t('Drag them onto the canvas to restore their workflow. The originals are kept next to them as video.before-workflow.mp4.', '把它们拖到画布上即可还原工作流。原视频保留在各自目录，文件名为 video.before-workflow.mp4。')
            : t('Videos that could not be checked safely were left as they were.', '无法安全确认的视频都保持原样。');
        if (result.failed) text.textContent += ' ' + t(`${result.failed} videos were left unchanged and will be offered again.`, `另有 ${result.failed} 个视频未改动，之后会再次提示。`);
        actions.replaceChildren(button(t('Done', '好的'), () => box.remove(), 'fv-quiet'));
    }
    if (value.state === 'running') { follow(); return; }
    heading.textContent = t('Add workflows to earlier videos', '为之前的视频补上工作流');
    text.textContent = t(`${value.count} earlier videos can get the workflow they were made with, so dropping them on the canvas restores it. Nothing changes until you confirm, and each original is kept as a backup (about ${gb(value.bytes)} GB).`,
        `有 ${value.count} 个之前生成的视频可以补上生成时的工作流，补写后拖到画布即可还原。确认前不会改动任何文件，每个原视频都会保留一份备份（约 ${gb(value.bytes)} GB）。`);
    const run = button(t('Add workflows', '补写工作流'), async () => {
        run.disabled = later.disabled = true;
        try { if (!(await post('run')).ok) throw new Error(); follow(); }
        catch { run.disabled = later.disabled = false; text.textContent = t('Could not start. Try again.', '没能开始，请重试。'); }
    }, 'fv-primary');
    const later = button(t('Don\'t ask again', '不再提示'), async () => { box.remove(); try { await post('dismiss'); } catch {} }, 'fv-quiet');
    actions.append(run, later);
}

// ``upscale`` queues the second pass of a first-pass preview (the studio's node).
export function openLibrary(t, {upscale = null} = {}) {
    if (opened?.open) { opened.focus(); return; }
    const dialog = el('dialog', null, 'fv-studio fv-library'); opened = dialog;
    dialog.setAttribute('aria-label', t('Your creations', '我的作品'));
    const header = el('header', null, 'fv-library-header');
    const back = button(t('Back to create', '返回创作'), () => closeDialog(dialog), 'fv-quiet');
    const title = el('h2', t('Your creations', '我的作品'));
    const refresh = button(t('Refresh', '刷新'), () => load(false), 'fv-quiet');
    header.append(back, title, refresh);
    const body = el('div', null, 'fv-library-body');
    const collection = el('section', null, 'fv-library-collection'); collection.setAttribute('aria-label', t('Saved videos', '已保存的视频'));
    const grid = el('div', null, 'fv-library-grid');
    const message = el('p', '', 'fv-library-message'); message.setAttribute('role', 'status');
    const more = button(t('Show more', '加载更多'), () => load(true), 'fv-quiet'); more.hidden = true;
    collection.append(grid, message, more);
    workflowBackfill(collection, t);
    const detail = el('section', null, 'fv-library-detail');
    const all = button(t('All creations', '全部作品'), () => {
        dialog.dataset.detail = 'false'; player?.pause(); cards.get(selected)?.focus({preventScroll: true});
    }, 'fv-quiet fv-library-all');
    const frame = el('div', null, 'fv-library-frame');
    const caption = el('div', null, 'fv-library-caption');
    const date = el('strong'), geometry = el('span'); caption.append(date, geometry);
    const stats = el('div', null, 'fv-stats'); stats.hidden = true;
    stats.setAttribute('aria-label', t('Generation statistics', '生成统计'));
    const budget = el('div', '', 'fv-budget');
    const links = el('div', null, 'fv-result-links');
    const notice = el('p', '', 'fv-library-message fv-library-notice'); notice.setAttribute('role', 'status');
    detail.append(all, frame, caption, links, notice, stats, budget);
    body.append(collection, detail); dialog.append(header, body);
    let disposed = false, loading = false, next = null, selected = null, player = null, arrivals = [];
    const rows = new Map(), cards = new Map();
    const abort = new AbortController();
    const narrow = matchMedia('(max-width: 680px)');
    const timestamp = row => {
        const value = new Date(row.created_at);
        return Number.isNaN(value.getTime()) ? t('Saved video', '已保存的视频')
            : value.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
    };
    const dimensions = row => {
        const g = row.geometry || {}, parts = [];
        // A preview's file is its first pass; geometry names the finished size.
        const shown = row.preview && row.preview_geometry?.width ? row.preview_geometry : g;
        if (shown.width && shown.height) parts.push(`${shown.width} × ${shown.height}`);
        const duration = g.seconds || (g.frames && g.frames / (g.fps || 24));
        if (duration) parts.push(`${Number(duration).toFixed(1)} s`);
        return parts.join(' · ');
    };
    function releasePlayer() {
        if (!player) return;
        player.pause(); player.removeAttribute('src'); player.load(); player = null;
    }
    function mountPlayer(row) {
        releasePlayer(); frame.replaceChildren();
        player = el('video'); player.controls = true; player.preload = 'metadata'; player.playsInline = true;
        player.src = view(row.video);
        // The picture's own box, so a preview's badge sits on its corner.
        const media = el('div', null, 'fv-library-media');
        const g = row.geometry || {};
        if (g.width > 0 && g.height > 0) frame.style.setProperty('--fv-ratio', String(g.width / g.height));
        media.append(player);
        if (row.preview) media.append(previewBadge(row, t));
        frame.append(media);
        player.addEventListener('error', () => {
            if (!disposed && selected === row.video) {
                const note = el('p', t('This video is unavailable. Refresh if it was moved.', '视频暂时无法打开，若文件已移动请刷新。'), 'fv-library-message');
                frame.replaceChildren(note);
            }
        }, {once: true});
    }
    function select(row, reveal = true) {
        if (selected !== row.video) {
            selected = row.video;
            mountPlayer(row);
            date.textContent = timestamp(row); geometry.textContent = dimensions(row);
            links.replaceChildren(resultActions(row, t, {
                secondPass: row.preview && upscale ? () => secondPass(row) : null,
                remove: () => remove(row)}));
            notice.textContent = '';
            for (const [file, card] of cards) card.setAttribute('aria-pressed', String(file === selected));
        }
        // These are the saved generation's measurements, including when the
        // same MP4 has since been reused. Never substitute the active request.
        const measured = value => Number.isFinite(value) && value >= 0;
        const number = (value, scale, unit) => measured(value) ? `${(value / scale).toFixed(1)} ${unit}` : '—';
        stats.replaceChildren();
        for (const [label, value, scale, unit] of [
            [t('Sampling', '采样耗时'), row.sample_seconds, 1, 's'],
            [t('Request total', '请求总计'), row.request_seconds, 1, 's'],
            [t('VRAM peak', '显存峰值'), row.vram_peak_bytes, 2 ** 30, 'GiB'],
            [t('RAM peak', '内存峰值'), row.ram_peak_bytes, 2 ** 30, 'GiB'],
        ]) {
            const item = el('div', null, 'fv-stat');
            item.append(el('strong', number(value, scale, unit)), el('span', label)); stats.append(item);
        }
        // A preview's timings describe only its first pass; they stay in its report.
        stats.hidden = row.preview || ![row.sample_seconds, row.request_seconds, row.vram_peak_bytes, row.ram_peak_bytes].some(measured);
        budget.textContent = !row.preview && measured(row.gpu_budget_bytes)
            ? `${t('VRAM budget', '可用显存预算')} ${number(row.gpu_budget_bytes, 2 ** 30, 'GiB')} · ${t('Device', '显卡总量')} ${number(row.gpu_total_bytes, 2 ** 30, 'GiB')}` : '';
        if (reveal) dialog.dataset.detail = 'true';
    }
    async function secondPass(row) {
        notice.textContent = '';
        try {
            await upscale(row);
            notice.textContent = t('Queued. The finished video appears here when the second pass is done.', '已加入队列，二采完成后成片会出现在作品里。');
        } catch (error) {
            notice.textContent = t('Could not start the second pass: ', '无法开始二采：') + error.message;
            throw error;
        }
    }
    // Throws a readable Error for the confirmation to show; on success the
    // creation leaves the collection.
    async function remove(row) {
        // Windows keeps a streamed video open; release it before deleting.
        if (selected === row.video) releasePlayer();
        let value = {};
        try {
            const response = await api.fetchApi('/freevideo/library/delete', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: row.id})});
            value = await response.json().catch(() => ({}));
            if (!response.ok || !value.deleted) throw new Error(value.reason || 'failed');
        } catch (error) {
            if (!disposed && selected === row.video) mountPlayer(row);
            throw new Error(error.message === 'foreign-files'
                ? t('Not deleted: the folder also holds files FreeVideo did not write: ', '未删除：文件夹里还有不是 FreeVideo 生成的文件：') + (value.kept || []).join(', ')
                : error.message === 'in-use' ? t('Not deleted: its second pass is running, or the video is still open. Try again in a moment.', '未删除：正在用它跑二采，或视频仍被占用，请稍后再试。')
                : t('Could not delete this creation.', '无法删除这条作品。'));
        }
        if (disposed) return;
        rows.delete(row.video); cards.get(row.video)?.remove(); cards.delete(row.video);
        if (selected === row.video) {
            selected = null; frame.replaceChildren(); links.replaceChildren(); date.textContent = geometry.textContent = '';
            stats.hidden = true; stats.replaceChildren(); budget.textContent = '';
            const next = rows.values().next().value;
            if (next && !narrow.matches) select(next, false); else dialog.dataset.detail = 'false';
        }
        notice.textContent = t('Deleted.', '已删除。');
        if (!rows.size) message.textContent = t('Your finished videos will appear here.', '生成完成的视频会保存在这里。');
    }
    const thumbnails = new IntersectionObserver(entries => {
        for (const {target, isIntersecting} of entries) if (isIntersecting) {
            target.src = target.dataset.src; thumbnails.unobserve(target);
        }
    }, {root: collection, rootMargin: '80px'});
    function append(row, first = false) {
        if (!row?.video) return;
        rows.set(row.video, row);
        if (cards.has(row.video)) return;
        const card = button('', () => select(rows.get(row.video)), 'fv-library-card');
        card.setAttribute('aria-label', `${timestamp(row)} · ${dimensions(row)}`);
        card.setAttribute('aria-pressed', String(row.video === selected));
        const picture = el('div', null, 'fv-library-picture');
        const image = el('img'); image.alt = ''; image.decoding = 'async';
        const id = row.id || row.video.replace(/^FreeVideo\//, '').replace(/\/video\.mp4$/, '');
        image.dataset.src = api.apiURL('/freevideo/library/thumbnail?' + new URLSearchParams({id}));
        image.onerror = () => { image.hidden = true; };
        picture.append(image);
        if (row.preview) picture.append(el('span', t('First-pass preview', '一采预览'), 'fv-preview-badge'));
        card.append(picture, el('span', timestamp(row)), el('small', dimensions(row)));
        cards.set(row.video, card); first ? grid.prepend(card) : grid.append(card); thumbnails.observe(image);
    }
    async function load(appendPage) {
        if (loading || disposed) return;
        loading = true; arrivals = []; refresh.disabled = more.disabled = true;
        message.textContent = rows.size ? '' : t('Loading your creations…', '正在读取作品…');
        try {
            const query = new URLSearchParams({limit: '24'});
            if (appendPage && next) query.set('before', next);
            const response = await api.fetchApi('/freevideo/library?' + query, {signal: abort.signal});
            if (response.status === 404) throw new Error(t('Restart ComfyUI to open your creations after updating.', '更新后重启 ComfyUI，即可打开作品。'));
            if (!response.ok) throw new Error(t('Could not load your creations. Try Refresh.', '作品暂时无法读取，请点击刷新。'));
            const data = await response.json(); if (disposed) return;
            if (!appendPage) { thumbnails.disconnect(); grid.replaceChildren(); cards.clear(); rows.clear(); }
            for (const row of data.items || []) append(row);
            for (const row of arrivals) append(row, true);
            next = data.next; more.hidden = !next;
            message.textContent = rows.size ? '' : t('Your finished videos will appear here.', '生成完成的视频会保存在这里。');
            if (!rows.has(selected)) {
                releasePlayer(); selected = null; frame.replaceChildren(); links.replaceChildren(); date.textContent = geometry.textContent = '';
                stats.hidden = true; stats.replaceChildren(); budget.textContent = '';
                if (!narrow.matches && rows.size) select(rows.values().next().value, false);
                else dialog.dataset.detail = 'false';
            } else select(rows.get(selected), false);
        } catch (error) { if (!disposed && error.name !== 'AbortError') message.textContent = error.message; }
        finally { if (!disposed) { loading = false; refresh.disabled = more.disabled = false; } }
    }
    const completed = event => {
        const row = event.detail?.value;
        if (!row?.video || disposed) return;
        const saved = {...row, created_at: row.created_at || rows.get(row.video)?.created_at || new Date().toISOString()};
        if (loading) arrivals.push(saved);
        append(saved, true);
        message.textContent = '';
        // Do not interrupt playback of a previous video when a new one arrives.
        if (!selected && !narrow.matches) select(rows.get(row.video), false);
    };
    window.addEventListener('freevideo-result', completed);
    dialog.addEventListener('cancel', event => { event.preventDefault(); closeDialog(dialog); });
    dialog.onclose = () => {
        disposed = true; abort.abort(); thumbnails.disconnect(); releasePlayer();
        window.removeEventListener('freevideo-result', completed); dialog.remove();
        if (opened === dialog) opened = null;
    };
    document.body.append(dialog); dialog.showModal(); load(false);
}
