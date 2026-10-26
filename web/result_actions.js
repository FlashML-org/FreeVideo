import { api } from '../../scripts/api.js';
import { outputDownloadURL } from './output_download.js';
import { shareButton } from './share.js';
import { canExtend, extendText, extendSourceProblem, extendSourceState } from './studio_extend.js';
import { issueButton } from './issue_button.js';
import { savedSettings, differs } from './result_reuse.js';

const css = document.createElement('link');
css.rel = 'stylesheet'; css.href = new URL('./result_actions.css', import.meta.url).href; document.head.append(css);

const ICONS = {
    back: '<path d="M13 8H3m0 0 4-4M3 8l4 4"/>',
    sidebar: '<rect x="2" y="2.5" width="12" height="11" rx="1.5"/><path d="M9.5 2.5v11"/>',
    download: '<path d="M8 2.5v7.5m0 0 3-3m-3 3-3-3M3 11.5v1a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-1"/>',
    share: '<path d="M8 10V2.5m0 0L5.2 5.3M8 2.5l2.8 2.8M4.5 7.5H4a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-4a1 1 0 0 0-1-1h-.5"/>',
    report: '<path d="M9.5 2H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4.5L9.5 2Z"/><path d="M9.5 2v2.5H12M6.5 8h3M6.5 10.5h3"/>',
    chevron: '<path d="m4.5 6.5 3.5 3 3.5-3"/>',
    extend: '<rect x="1.5" y="4" width="8" height="8" rx="1.5"/><path d="M11.5 8h3m0 0-1.5-1.5M14.5 8 13 9.5"/>',
    lock: '<rect x="3.5" y="7" width="9" height="6.5" rx="1.5"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/>',
    close: '<path d="m4 4 8 8M12 4l-8 8"/>',
    expand: '<path d="M9.5 2.5h4v4M13.5 2.5 9 7M6.5 13.5h-4v-4M2.5 13.5 7 9"/>',
    copy: '<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5v-1a1 1 0 0 0-1-1h-5a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1h1"/>',
    trash: '<path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.1a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.6-8.1M7 7v4M9 7v4"/>',
    heart: '<path d="M8 13.5 2.6 8.3a3.3 3.3 0 0 1 4.7-4.6L8 4.4l.7-.7a3.3 3.3 0 0 1 4.7 4.6Z"/>',
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
export function secondPassAction(t, run) {
    const button = action('button', t('Run second pass', '继续二采'), 'expand', 'fv-action-primary fv-action-second-pass');
    const label = button.lastElementChild;
    let problem;
    button.title = t('Continue from this preview to the full-resolution video.',
        '从这个预览继续生成全分辨率视频。');
    button.onclick = async () => {
        if (button.disabled) return;
        problem?.remove();
        button.disabled = true; button.setAttribute('aria-busy', 'true');
        label.textContent = button.title = t('Adding…', '正在添加…');
        try { await run(); label.textContent = button.title = t('Queued', '已加入队列'); }
        catch {
            button.disabled = false;
            label.textContent = button.title = t('Second pass did not start · retry', '二采没有开始 · 重试');
            problem = issueButton(t, () => ({where: ['Second pass', '二采'], message: label.textContent}));
            button.after(problem);
        } finally { button.removeAttribute('aria-busy'); }
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
    const title = document.createElement('strong'); title.textContent = t('Delete this creation?', '删除这个作品？');
    const hint = document.createElement('small');
    hint.textContent = t('The video and its files will be deleted.',
        '视频和相关文件都会删除。');
    const problem = document.createElement('small'); problem.className = 'fv-confirm-problem'; problem.hidden = true;
    let problemIssue;
    const row = document.createElement('div'); row.className = 'fv-confirm-actions';
    const cancel = action('button', t('Cancel', '取消'), null, 'fv-action-secondary');
    const confirm = action('button', t('Delete', '删除'), 'trash', 'fv-action-danger');
    cancel.onclick = () => { menu.open = false; summary.focus(); };
    confirm.onclick = async () => {
        problem.hidden = true; problemIssue?.remove();
        confirm.disabled = cancel.disabled = true; confirm.lastElementChild.textContent = t('Deleting…', '正在删除…');
        try { await remove(); menu.open = false; }
        catch (error) {
            problem.textContent = error.message; problem.hidden = false;
            if (error.reportable !== false) {
                problemIssue = issueButton(t, () => ({where: ['Delete creation', '删除作品'], message: problem.textContent}));
                problem.after(problemIssue);
            }
        }
        finally { confirm.disabled = cancel.disabled = false; confirm.lastElementChild.textContent = t('Delete', '删除'); }
    };
    row.append(cancel, confirm);
    panel.append(title, hint, problem, row);
    menu.append(summary, panel);
    popover(menu, summary, () => { problem.hidden = true; problemIssue?.remove(); });
    menu.addEventListener('toggle', () => { if (menu.open) cancel.focus(); });
    return menu;
}

// The video cannot be opened: what works on the file (download, extend, share) goes quiet and cannot be used.
export function actionsUnavailable(bar) {
    if (!bar) return;
    bar.dataset.unavailable = 'true';
    for (const item of bar.querySelectorAll('a.fv-action[download], .fv-action-extend, .fv-share-trigger')) {
        item.setAttribute('aria-disabled', 'true'); item.tabIndex = -1;
        if (item.tagName === 'A') item.removeAttribute('href'); else item.disabled = true;
    }
}

// Writes text to the clipboard; resolves false when neither the clipboard API nor the old copy command is allowed.
export async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch { /* fall through to the selection copy */ }
    const area = document.createElement('textarea'); area.value = text; area.setAttribute('readonly', '');
    area.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.append(area); area.select();
    try { return document.execCommand('copy'); } catch { return false; } finally { area.remove(); }
}

// Fills the prompt, size and duration of an earlier video back into the panel. It is offered only when
// the video kept its prompt and the panel does not already hold the same settings. Resolves to the button or null.
// `reuse`: {now(saved), apply(saved), onEdit(listener) -> stop}
async function reuseAction(record, t, reuse) {
    const saved = await savedSettings(api, record);
    if (!saved) return null;
    const label = t('Reuse settings', '复用设置'), filled = t('Filled in', '已填入'), undoLabel = t('Undo', '撤销');
    const widest = [label, `${filled} · ${undoLabel}`, filled].sort((a, b) => b.length - a.length)[0];
    // The label sits over an invisible copy of the longest state, so the bar does not shift when it changes.
    const make = (...parts) => {
        const text = document.createElement('span'); text.className = 'fv-reuse-text'; text.dataset.widest = widest;
        const inner = document.createElement('span'); inner.append(...parts); text.append(inner);
        return text;
    };
    let timer = 0, stop = () => {}, current = null;
    const offered = () => differs(saved, reuse.now(saved));
    const idle = () => {
        clearTimeout(timer); stop();
        const button = action('button', '', 'back', 'fv-action-secondary fv-action-reuse');
        button.lastElementChild.replaceWith(make(label));
        button.hidden = !offered();
        button.onclick = () => {
            const before = reuse.now(saved), hadContent = Boolean(before.text.trim()) && differs(saved, before);
            reuse.apply(saved); show(hadContent ? before : null);
        };
        stop = reuse.onEdit(() => {
            if (!button.isConnected) { stop(); return; }
            button.hidden = !offered();
        });
        swap(button);
    };
    // A later state takes the place of the button in the bar.
    const swap = next => { if (current?.parentNode) current.replaceWith(next); current = next; };
    const show = before => {
        clearTimeout(timer); stop();
        const chip = document.createElement('span'); chip.className = 'fv-action fv-action-secondary fv-action-reuse fv-reuse-done';
        chip.setAttribute('role', 'status');
        chip.append(icon('back'));
        const parts = [before ? `${filled} · ` : filled];
        if (before) {
            const undo = document.createElement('button'); undo.type = 'button'; undo.className = 'fv-reuse-undo'; undo.textContent = undoLabel;
            undo.onclick = () => { reuse.apply(before); idle(); };
            parts.push(undo);
        }
        chip.append(make(...parts)); swap(chip);
        timer = setTimeout(idle, before ? 10000 : 1800);
        if (before) stop = reuse.onEdit(() => { if (!chip.isConnected) stop(); else idle(); });
    };
    idle();
    return current;
}

// Download the video first; share and the report follow. A first-pass preview
// leads with `secondPass` instead, and saved creations end with `remove`.
// `diagnostic` downloads the redacted report (and may offer to send it); a
// result without one shows no report entry, because the full generation
// record keeps local paths and is not meant to be shared.
export function resultActions(record, t, {diagnostic = null, secondPass = null, remove = null, extend = null, reuse = null, favorite = null} = {}) {
    const bar = document.createElement('div'); bar.className = 'fv-actions';
    if (secondPass) bar.append(secondPassAction(t, secondPass));
    if (favorite) {
        const button = action('button', '', 'heart', 'fv-action-secondary fv-action-favorite');
        const sync = () => {
            button.setAttribute('aria-pressed', String(!!record.favorite));
            button.lastElementChild.textContent = button.title = record.favorite
                ? t('Remove from favorites', '取消收藏') : t('Favorite', '收藏');
            button.setAttribute('aria-label', button.title);
        };
        sync();
        button.onclick = async () => {
            if (button.disabled) return;
            button.disabled = true; button.setAttribute('aria-busy', 'true');
            const value = !record.favorite;
            try { await favorite(value); record.favorite = value; sync(); }
            catch {
                button.lastElementChild.textContent = button.title = t('Could not save favorite · retry', '无法保存收藏 · 重试');
                button.setAttribute('aria-label', button.title);
            }
            finally { button.disabled = false; button.removeAttribute('aria-busy'); }
        };
        bar.append(button);
    }
    if (record.video) {
        const download = action('a', t('Download video', '下载视频'), 'download', secondPass ? 'fv-action-secondary' : 'fv-action-primary');
        download.href = outputDownloadURL(api, record.video); download.download = '';
        bar.append(download);
        const copy = action('button', t('Copy path', '复制路径'), 'copy', 'fv-action-secondary fv-action-copy');
        const text = copy.lastElementChild; let timer = 0;
        // The button keeps the width of its longest label, so the bar does not shift when the label changes.
        text.className = 'fv-copy-text';
        text.dataset.widest = [t('Copy path', '复制路径'), t('Copied', '已复制'), t('Could not copy', '复制失败')].sort((a, b) => b.length - a.length)[0];
        copy.onclick = async () => {
            clearTimeout(timer);
            text.textContent = await copyText(record.video) ? t('Copied', '已复制') : t('Could not copy', '复制失败');
            timer = setTimeout(() => { text.textContent = t('Copy path', '复制路径'); }, 1800);
        };
        bar.append(copy);
        if (reuse) reuseAction(record, t, reuse).then(button => { if (button && bar.isConnected) copy.after(button); });
    }
    if (extend && canExtend(record)) {
        const button = action('button', extendText(t, 'action'), 'extend', 'fv-action-secondary fv-action-extend');
        const label = button.lastElementChild;
        let problem;
        const fault = () => {
            problem?.remove();
            problem = issueButton(t, () => ({where: ['Continue video', '延长视频'], message: label.textContent}));
            button.after(problem);
        };
        // Only a video the server says is gone keeps the issue button; a check that could not finish just retries.
        const available = state => {
            label.textContent = state === 'ok' ? extendText(t, 'action') : `${extendSourceProblem(t, state)} · ${t('retry', '重试')}`;
            button.title = '';
            if (state === 'missing') fault(); else problem?.remove();
            return state === 'ok';
        };
        extendSourceState(api, record).then(available);
        button.onclick = async () => {
            if (button.disabled) return;
            problem?.remove();
            button.disabled = true;
            button.title = t('Checking video…', '正在检查视频…');
            try {
                if (!available(await extendSourceState(api, record))) return;
                if (await extend(record) === null) throw new Error(t('Could not open the editor. Try again.', '无法打开剪辑，请重试。'));
            } catch (error) {
                label.textContent = error.message || t('Could not open the editor. Try again.', '无法打开剪辑，请重试。');
                button.title = label.textContent;
                fault();
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
        report.title = t('Diagnostic report: hardware, settings and timings', '诊断报告：硬件、设置和耗时');
        const text = report.lastElementChild;
        let problem;
        report.onclick = async () => {
            if (report.disabled) return;
            problem?.remove();
            report.disabled = true; text.textContent = t('Preparing…', '正在整理…');
            try { await diagnostic(); text.textContent = reportLabel; }
            catch {
                text.textContent = t('Report unavailable · retry', '报告暂不可用 · 重试');
                problem = issueButton(t, () => ({where: ['Diagnostic report', '诊断报告'], message: text.textContent}));
                report.after(problem);
            }
            finally { report.disabled = false; }
        };
        bar.append(report);
    }
    if (remove) bar.append(deleteAction(t, remove));
    return bar;
}
