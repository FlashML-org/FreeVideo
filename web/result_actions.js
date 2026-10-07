import { api } from '../../scripts/api.js';
import { outputDownloadURL } from './output_download.js';
import { shareButton } from './share.js';

const css = document.createElement('link');
css.rel = 'stylesheet'; css.href = new URL('./result_actions.css', import.meta.url).href; document.head.append(css);

const ICONS = {
    download: '<path d="M8 2.5v7.5m0 0 3-3m-3 3-3-3M3 11.5v1a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-1"/>',
    share: '<path d="M8 10V2.5m0 0L5.2 5.3M8 2.5l2.8 2.8M4.5 7.5H4a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-4a1 1 0 0 0-1-1h-.5"/>',
    report: '<path d="M9.5 2H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4.5L9.5 2Z"/><path d="M9.5 2v2.5H12M6.5 8h3M6.5 10.5h3"/>',
    chevron: '<path d="m4.5 6.5 3.5 3 3.5-3"/>',
    expand: '<path d="M9.5 2.5h4v4M13.5 2.5 9 7M6.5 13.5h-4v-4M2.5 13.5 7 9"/>',
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
    badge.textContent = g?.width ? t(`First-pass preview · ${g.width} × ${g.height}`, `一采预览 · ${g.width} × ${g.height}`)
        : t('First-pass preview', '一采预览');
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
    const button = action('button', t('Run second pass', '继续二采'), 'expand', 'fv-action-primary fv-action-second-pass');
    const label = button.lastElementChild;
    button.title = t('Continue from this preview: latent upscale, the second pass and a full-resolution decode.',
        '从这个预览接着跑：潜空间放大、二采，再以全分辨率解码。');
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

// Download the video first; share and the reports follow, one entry each.
// A first-pass preview leads with `secondPass` instead, and saved creations end
// with `remove`. `diagnostic` is an async callback for the redacted support
// report, when one exists.
export function resultActions(record, t, {diagnostic = null, secondPass = null, remove = null} = {}) {
    const bar = document.createElement('div'); bar.className = 'fv-actions';
    const finish = () => { if (remove) bar.append(deleteAction(t, remove)); return bar; };
    if (secondPass) bar.append(secondPassAction(t, secondPass));
    if (record.video) {
        const download = action('a', t('Download video', '下载视频'), 'download', secondPass ? 'fv-action-secondary' : 'fv-action-primary');
        download.href = outputDownloadURL(api, record.video); download.download = '';
        bar.append(download);
    }
    const share = shareButton(record, t);
    share.className = 'fv-action fv-action-secondary fv-share-trigger';
    const label = document.createElement('span'); label.textContent = share.textContent;
    share.replaceChildren(icon('share'), label);
    bar.append(share);
    const recordLink = record.report ? outputDownloadURL(api, record.report) : null;
    if (!diagnostic) {
        if (recordLink) {
            const report = action('a', t('Report', '报告'), 'report', 'fv-action-secondary');
            report.href = recordLink; report.download = record.report.split('/').pop(); report.title = t('Generation record (JSON)', '生成记录（JSON）');
            bar.append(report);
        }
        return finish();
    }
    const menu = document.createElement('details'); menu.className = 'fv-action-menu';
    const summary = document.createElement('summary'); summary.className = 'fv-action fv-action-secondary';
    const text = document.createElement('span'); text.textContent = t('Report', '报告');
    summary.append(icon('report'), text, icon('chevron'));
    const list = document.createElement('div'); list.className = 'fv-menu';
    const item = (tag, title, hint) => {
        const e = document.createElement(tag); e.className = 'fv-menu-item';
        const strong = document.createElement('strong'); strong.textContent = title;
        const small = document.createElement('small'); small.textContent = hint;
        e.append(strong, small);
        return e;
    };
    const supportLabel = t('Diagnostic report', '诊断报告');
    const support = item('button', supportLabel, t('Redacted, for problem reports', '已脱敏，可用于反馈问题')); support.type = 'button';
    const supportTitle = support.querySelector('strong');
    support.onclick = async () => {
        if (support.disabled) return;
        support.disabled = true; supportTitle.textContent = t('Preparing…', '正在整理…');
        try { await diagnostic(); supportTitle.textContent = supportLabel; menu.open = false; }
        catch { supportTitle.textContent = t('Report unavailable · retry', '报告暂不可用 · 重试'); }
        finally { support.disabled = false; }
    };
    list.append(support);
    if (recordLink) {
        const full = item('a', t('Generation record', '生成记录'), t('Every setting of this run (JSON)', '本次生成的完整参数（JSON）'));
        full.href = recordLink; full.download = record.report.split('/').pop();
        full.onclick = () => { menu.open = false; };
        list.append(full);
    }
    menu.append(summary, list);
    popover(menu, summary, () => { if (!support.disabled) supportTitle.textContent = supportLabel; });
    bar.append(menu);
    return finish();
}
