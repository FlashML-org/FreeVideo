import { api } from '../../scripts/api.js';
import { outputDownloadURL } from './output_download.js';
import { shareButton } from './share.js';
import { EXTEND_ENTRY_OPEN, canExtend, extendText, extendSourceExists } from './studio_extend.js';

const css = document.createElement('link');
css.rel = 'stylesheet'; css.href = new URL('./result_actions.css', import.meta.url).href; document.head.append(css);

const ICONS = {
    sidebar: '<rect x="2" y="2.5" width="12" height="11" rx="1.5"/><path d="M9.5 2.5v11"/>',
    download: '<path d="M8 2.5v7.5m0 0 3-3m-3 3-3-3M3 11.5v1a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-1"/>',
    share: '<path d="M8 10V2.5m0 0L5.2 5.3M8 2.5l2.8 2.8M4.5 7.5H4a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-4a1 1 0 0 0-1-1h-.5"/>',
    report: '<path d="M9.5 2H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4.5L9.5 2Z"/><path d="M9.5 2v2.5H12M6.5 8h3M6.5 10.5h3"/>',
    chevron: '<path d="m4.5 6.5 3.5 3 3.5-3"/>',
    extend: '<rect x="1.5" y="4" width="8" height="8" rx="1.5"/><path d="M11.5 8h3m0 0-1.5-1.5M14.5 8 13 9.5"/>',
    lock: '<rect x="3.5" y="7" width="9" height="6.5" rx="1.5"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/>',
    close: '<path d="m4 4 8 8M12 4l-8 8"/>',
    expand: '<path d="M9.5 2.5h4v4M13.5 2.5 9 7M6.5 13.5h-4v-4M2.5 13.5 7 9"/>',
    upscale: '<rect x="2" y="6.5" width="7.5" height="7.5" rx="1.5"/><path d="M8.5 3h4.5v4.5M13 3 8 8"/>',
    trash: '<path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.1a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.6-8.1M7 7v4M9 7v4"/>',
};

export function icon(name) {
    const span = document.createElement('span'); span.className = 'fv-icon'; span.setAttribute('aria-hidden', 'true');
    span.innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;
    return span;
}

// Marks a first-pass preview on its picture, with the preview's own size.
export function previewBadge(record, t) {
    const badge = document.createElement('span'); badge.className = 'fv-preview-badge';
    const g = record.preview_geometry;
    if (record.pixel_upscale) {
        const g = record.geometry;
        badge.textContent = g?.width ? t(`Upscaled 2× · ${g.width} × ${g.height}`, `已 2× 放大 · ${g.width} × ${g.height}`)
            : t('Upscaled 2×', '已 2× 放大');
    } else if (record.early_stop?.completed_steps) {
        badge.textContent = t(`Kept after step ${record.early_stop.completed_steps} of ${record.early_stop.planned_steps || '?'}`,
            `在第 ${record.early_stop.completed_steps} / ${record.early_stop.planned_steps || '?'} 步保留`);
    } else {
        badge.textContent = g?.width ? t(`First-pass preview · ${g.width} × ${g.height}`, `一采预览 · ${g.width} × ${g.height}`)
            : t('First-pass preview', '一采预览');
    }
    return badge;
}

function action(tag, label, name, cls) {
    const e = document.createElement(tag); e.className = 'fv-action ' + cls;
    const text = document.createElement('span'); text.textContent = label;
    if (name) e.append(icon(name));
    e.append(text);
    if (tag === 'button') e.type = 'button';
    return e;
}

// A popover above its button that closes on an outside click or Escape, like a
// native menu. Escape must not also reach the dialog, which would close the window.
function popover(menu, summary, onClose = () => {}) {
    const outside = event => { if (menu.open && !menu.contains(event.target)) menu.open = false; };
    menu.addEventListener('toggle', () => {
        if (menu.open) document.addEventListener('pointerdown', outside, true);
        else { document.removeEventListener('pointerdown', outside, true); onClose(); }
    });
    menu.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || !menu.open) return;
        event.preventDefault(); event.stopPropagation(); menu.open = false; summary.focus();
    });
}

// The next step of a first-pass preview. `run` reports its own errors and throws.
function secondPassAction(t, run) {
    const button = action('button', t('Continue remaining steps', '继续剩余步数'), 'expand', 'fv-action-primary fv-action-second-pass');
    const label = button.lastElementChild;
    button.title = t('Keep going from this checkpoint: remaining sampling steps on the same schedule, then a full decode with audio. Prompt, seed and size stay as they were.',
        '从这个检查点接着跑同一日程的剩余采样步，再解码带音频的成片。提示词、种子和尺寸保持不变。');
    button.onclick = async () => {
        if (button.disabled) return;
        button.disabled = true;
        try { await run(); label.textContent = t('Queued', '已加入队列'); }
        catch { button.disabled = false; }
    };
    return button;
}

export function canPixelUpscale(record) {
    if (!record?.video || record.pixel_upscale) return false;
    const g = record.preview_geometry?.width ? record.preview_geometry : record.geometry || {};
    const long = Math.max(Number(g.width) || 0, Number(g.height) || 0);
    if (long && long * 2 > 2048) return false;
    return true;
}

function pixelUpscaleAction(t, run) {
    const button = action('button', t('Upscale 2×', '2× 放大'), 'upscale', 'fv-action-secondary fv-action-pixel-upscale');
    const label = button.lastElementChild;
    button.title = t('Enlarge this finished video with RealESRGAN. Original audio is kept. MiniMax is not sampled again.',
        '用 RealESRGAN 放大这段成片。原音频保留，不再采样 MiniMax。');
    button.onclick = async () => {
        if (button.disabled) return;
        button.disabled = true;
        try { await run(); label.textContent = t('Queued', '已加入队列'); }
        catch { button.disabled = false; }
    };
    return button;
}

// Delete asks in a popover first. `remove` throws an Error whose message is shown there.
function deleteAction(t, remove) {
    const menu = document.createElement('details'); menu.className = 'fv-action-menu fv-action-delete';
    const summary = document.createElement('summary'); summary.className = 'fv-action fv-action-secondary';
    const label = document.createElement('span'); label.textContent = t('Delete', '删除');
    summary.append(icon('trash'), label);
    const panel = document.createElement('div'); panel.className = 'fv-menu fv-confirm';
    const title = document.createElement('strong'); title.textContent = t('Delete this creation?', '删除这条作品？');
    const hint = document.createElement('small');
    hint.textContent = t('The video, its generation records and preview data are removed. This cannot be undone.',
        '视频、生成记录和一采中间结果都会删除，无法恢复。');
    const problem = document.createElement('small'); problem.className = 'fv-confirm-problem'; problem.hidden = true;
    const row = document.createElement('div'); row.className = 'fv-confirm-actions';
    const cancel = action('button', t('Cancel', '取消'), null, 'fv-action-secondary');
    const confirm = action('button', t('Delete', '删除'), 'trash', 'fv-action-danger');
    cancel.onclick = () => { menu.open = false; summary.focus(); };
    confirm.onclick = async () => {
        confirm.disabled = cancel.disabled = true; confirm.lastElementChild.textContent = t('Deleting…', '正在删除…');
        try { await remove(); menu.open = false; }
        catch (error) { problem.textContent = error.message; problem.hidden = false; }
        finally { confirm.disabled = cancel.disabled = false; confirm.lastElementChild.textContent = t('Delete', '删除'); }
    };
    row.append(cancel, confirm);
    panel.append(title, hint, problem, row);
    menu.append(summary, panel);
    popover(menu, summary, () => { problem.hidden = true; });
    menu.addEventListener('toggle', () => { if (menu.open) cancel.focus(); });
    return menu;
}

// Download the video first; share and the report follow. A first-pass preview
// leads with `secondPass` instead, and saved creations end with `remove`.
// `diagnostic` downloads the redacted report (and may offer to send it); a
// result without one shows no report entry, because the full generation
// record keeps local paths and is not meant to be shared.
export function resultActions(record, t, {diagnostic = null, secondPass = null, remove = null, extend = null,
    pixelUpscale = null} = {}) {
    const bar = document.createElement('div'); bar.className = 'fv-actions';
    if (secondPass) bar.append(secondPassAction(t, secondPass));
    if (pixelUpscale && canPixelUpscale(record)) bar.append(pixelUpscaleAction(t, pixelUpscale));
    if (record.video) {
        const download = action('a', t('Download video', '下载视频'), 'download', secondPass ? 'fv-action-secondary' : 'fv-action-primary');
        download.href = outputDownloadURL(api, record.video); download.download = '';
        bar.append(download);
    }
    if (EXTEND_ENTRY_OPEN && extend && canExtend(record)) {
        const button = action('button', extendText(t, 'action'), 'extend', 'fv-action-secondary fv-action-extend');
        button.hidden = true;
        extendSourceExists(api, record).then(exists => { button.hidden = !exists; });
        button.onclick = async () => {
            if (button.disabled) return;
            button.disabled = true;
            try {
                if (!await extendSourceExists(api, record)) { button.hidden = true; return; }
                await extend(record);
            } finally { button.disabled = false; }
        };
        bar.append(button);
    }
    const share = shareButton(record, t);
    share.className = 'fv-action fv-action-secondary fv-share-trigger';
    const label = document.createElement('span'); label.textContent = share.textContent;
    share.replaceChildren(icon('share'), label);
    bar.append(share);
    if (diagnostic) {
        const reportLabel = t('Diagnostic report', '诊断报告');
        const report = action('button', reportLabel, 'report', 'fv-action-secondary fv-action-report');
        report.title = t('Redacted diagnostic report: hardware, settings and timings', '已脱敏的诊断报告：硬件、设置和耗时');
        const text = report.lastElementChild;
        report.onclick = async () => {
            if (report.disabled) return;
            report.disabled = true; text.textContent = t('Preparing…', '正在整理…');
            try { await diagnostic(); text.textContent = reportLabel; }
            catch { text.textContent = t('Report unavailable · retry', '报告暂不可用 · 重试'); }
            finally { report.disabled = false; }
        };
        bar.append(report);
    }
    if (remove) bar.append(deleteAction(t, remove));
    return bar;
}
