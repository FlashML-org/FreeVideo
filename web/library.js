import { api } from '../../scripts/api.js';
import { closeDialog } from './motion.js';
import { outputDownloadURL } from './output_download.js';

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

export function openLibrary(t) {
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
    detail.append(all, frame, caption, stats, budget, links);
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
        if (g.width && g.height) parts.push(`${g.width} × ${g.height}`);
        const duration = g.seconds || (g.frames && g.frames / (g.fps || 24));
        if (duration) parts.push(`${Number(duration).toFixed(1)} s`);
        return parts.join(' · ');
    };
    function releasePlayer() {
        if (!player) return;
        player.pause(); player.removeAttribute('src'); player.load(); player = null;
    }
    function select(row, reveal = true) {
        if (selected !== row.video) {
            releasePlayer(); frame.replaceChildren();
            selected = row.video;
            player = el('video'); player.controls = true; player.preload = 'metadata'; player.playsInline = true;
            player.src = view(row.video); frame.append(player);
            player.addEventListener('error', () => {
                if (!disposed && selected === row.video) {
                    const note = el('p', t('This video is unavailable. Refresh if it was moved.', '视频暂时无法打开，若文件已移动请刷新。'), 'fv-library-message');
                    frame.replaceChildren(note);
                }
            }, {once: true});
            date.textContent = timestamp(row); geometry.textContent = dimensions(row);
            links.replaceChildren();
            for (const [label, file, cls] of [[t('Download video', '下载视频'), row.video, 'fv-primary'], [t('View report', '查看报告'), row.report, 'fv-quiet']]) {
                if (!file) continue;
                const a = el('a', label, cls); a.href = outputDownloadURL(api, file); a.download = file === row.video ? '' : file.split('/').pop(); links.append(a);
            }
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
        stats.hidden = ![row.sample_seconds, row.request_seconds, row.vram_peak_bytes, row.ram_peak_bytes].some(measured);
        budget.textContent = measured(row.gpu_budget_bytes)
            ? `${t('VRAM budget', '可用显存预算')} ${number(row.gpu_budget_bytes, 2 ** 30, 'GiB')} · ${t('Device', '显卡总量')} ${number(row.gpu_total_bytes, 2 ** 30, 'GiB')}` : '';
        if (reveal) dialog.dataset.detail = 'true';
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
        picture.append(image); card.append(picture, el('span', timestamp(row)), el('small', dimensions(row)));
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
