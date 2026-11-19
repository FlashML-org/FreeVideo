// The picture models, as one dropdown: the chosen model in one row (name and state), the list in a popover with
// every model in it (name, installed or the size to download, Install), room for more later. Each model is
// installed, not installed, downloading (with progress) or failed (with the reason and Retry); choosing one that is
// not installed and pressing Generate installs it first, then generates.
import { listenDialogKey } from './dialog_keys.js';
import { el, icon, t, tip, button } from './image_view_ui.js';

export const IMAGE_MODELS = Object.freeze([
    {id: 'minimax-h3-image', name: 'MiniMax H3 Image', short: 'H3 Image'},
    {id: 'qwen-image-2.1-turbo', name: 'Qwen-Image 2.1', short: 'Qwen-Image 2.1'},
    {id: 'moss-image-sft', name: 'MOSS-Image', short: 'MOSS-Image'},
]);

// One model row from the engine's info as a state the page can draw. `info` absent: still asking.
export function modelState(info, id) {
    if (!info) return {state: 'checking'};
    const row = info.models?.find(m => m.id === id);
    if (!row || row.available === false) return {state: info.models ? 'unavailable' : 'checking'};
    if (row.ready) return {state: 'ready', row};
    // The engine is re-checking files that are already there: they are being verified, not missing.
    if (row.checking && !row.install?.state) return {state: 'downloading', row, total: row.bytes_total || 0, done: 0, fraction: 0, verifying: true, unknown: true, paused: false, rate: 0};
    const install = row.install || {};
    const total = install.total || row.bytes_total || 0, done = install.done || row.bytes_done || 0;
    const verifying = install.state === 'running' && install.phase === 'verify';
    const fraction = verifying ? (install.verify_total > 0 ? Math.min(1, (install.verify_done || 0) / install.verify_total) : 0)
        : total > 0 ? Math.min(1, done / total) : 0;
    if (row.downloading || install.state === 'running' || install.state === 'paused_for_generation')
        return {state: 'downloading', row, total, done, fraction, verifying, paused: install.state === 'paused_for_generation',
            waitsFor: install.state === 'paused_for_generation' ? install.reason || 'busy_engine' : '', rate: install.bytes_per_second || 0};
    if (install.state === 'failed')
        return {state: 'failed', row, total, done, fraction, reason: t(install.message_en || '', install.message_zh || '') || install.reason || ''};
    return {state: 'missing', row, total, done, fraction, resume: done > 0};
}

const gb = bytes => (bytes / 1e9).toFixed(1);
// Why a download waits, as the engine reports it: only a video or a picture being made pauses it, and the user can
// continue it at once.
// What pressing Continue download does: the download runs now, beside the video or picture being made.
export const continueUse = () => t('Download now; a video or picture being made may take longer', '现在就下载；正在进行的生成可能会变慢');
export function pausedText(state) {
    return state.waitsFor === 'busy_video' ? t('Paused while a video generates; resumes after it, or continue now', '视频生成中，下载已暂停，之后自动继续，也可以现在继续下载')
        : state.waitsFor === 'busy_picture' ? t('Paused while a picture is made; resumes after it, or continue now', '正在出图，下载已暂停，出完自动继续，也可以现在继续下载')
        : t('Paused while FreeVideo uses the graphics card for another task; resumes after it, or continue now', 'FreeVideo 正在用显卡做别的任务，下载已暂停，之后自动继续，也可以现在继续下载');
}
// Checking files already on disk: a percentage only when the engine reports one.
export const verifyText = state => state.unknown ? t('Verifying', '正在校验') : t(`Verifying ${Math.floor(state.fraction * 100)}%`, `正在校验 ${Math.floor(state.fraction * 100)}%`);
// The size to download, said once: files the video models already have are not fetched again.
export function downloadText(state) {
    if (!state.total) return t('Download needed', '需下载');
    if (state.resume) return t(`${gb(state.done)} of ${gb(state.total)} GB`, `已下载 ${gb(state.done)} / ${gb(state.total)} GB`);
    return t(`Download needed · ${gb(state.total)} GB`, `需下载 · ${gb(state.total)} GB`);
}
export function statusLine(state) {
    switch (state.state) {
    case 'ready': return t('Installed', '已安装');
    case 'downloading': return state.paused ? t('Paused', '已暂停') : state.verifying ? verifyText(state) : `${Math.floor(state.fraction * 100)}%`;
    case 'failed': return t('Download failed. Retry to continue.', '下载失败，请重试以继续。');
    case 'missing': return downloadText(state);
    case 'unavailable': return t('Not on this computer', '这台电脑不支持');
    default: return t('Checking…', '正在检查…');
    }
}

const ICON_OF = {ready: 'check', failed: 'warn', downloading: 'down', unavailable: 'info', missing: 'cloud', checking: 'clock'};
const gbOf = state => state.total ? (state.total / 1e9).toFixed(1) : '';
function useOf(model, state) {
    const shared = model.id !== 'moss-image-sft';
    const size = state.total ? shared ? t(`about ${gbOf(state)} GB, files shared with video are not downloaded again`, `约 ${gbOf(state)} GB，和视频共用的文件不重复下载`)
        : t(`about ${gbOf(state)} GB`, `约 ${gbOf(state)} GB`) : '';
    return {
        ready: model.id === IMAGE_MODELS[0].id ? t('Large, detailed pictures, up to 16 references', '大尺寸、细节多，最多 16 张参考图')
            : model.id === 'moss-image-sft' ? t('Pictures from a text prompt', '根据文字提示出图') : t('Fast pictures, up to 4 references', '出图快，最多 4 张参考图'),
        missing: state.row?.blocked_by ? t('Not installed: another model is downloading; this one installs after it, or when you press Generate', '未安装：另一个模型正在下载，完成后才会安装这个，也可点生成')
            : t(`Not installed: Generate installs it first, ${size}`, `未安装：点生成会先安装，${size}`),
        downloading: state.paused ? pausedText(state) : state.verifying ? (state.unknown ? t('Verifying the files; usable when done', '正在校验文件，完成后可以使用') : t(`Verifying the files ${Math.floor(state.fraction * 100)}%; usable when done`, `正在校验文件 ${Math.floor(state.fraction * 100)}%，完成后即可使用`)) : t(`Downloading ${Math.floor((state.fraction || 0) * 100)}%; usable when done`, `正在下载 ${Math.floor((state.fraction || 0) * 100)}%，完成后即可使用`),
        failed: state.reason || t('The download stopped; press Retry', '下载中断了，点重试'),
        unavailable: model.id === 'moss-image-sft' ? t('MOSS-Image needs an NVIDIA graphics card, which this computer does not have. Choose another model', 'MOSS-Image 需要 NVIDIA 显卡，这台电脑没有。请选别的模型')
            : t('This model needs an NVIDIA graphics card, which this computer does not have. Choose another model', '这个模型需要 NVIDIA 显卡，这台电脑没有。请选别的模型'),
        checking: t('Asking the engine', '正在询问引擎'),
    }[state.state];
}
const actionOf = state => state.state === 'missing' ? (state.resume ? t('Resume', '继续') : t('Install', '安装')) : state.state === 'failed' ? t('Retry', '重试') : state.state === 'downloading' && state.paused ? t('Continue download', '继续下载') : state.state === 'downloading' && !state.verifying ? t('Pause', '暂停') : '';

// The picker: one row for the chosen model; the list opens below it (above when there is no room), takes the
// arrow keys, Home, End, Enter and Escape, and scrolls when there are more models than fit.
export function modelPicker({host, onPick, onInstall, onPause}) {
    const root = el('div', 'fv-iv-mpick');
    const trigger = el('button', 'fv-iv-mpick-btn'); trigger.type = 'button'; trigger.setAttribute('aria-haspopup', 'listbox'); trigger.setAttribute('aria-expanded', 'false');
    const name = el('b', 'fv-iv-mpick-name'), status = el('span', 'fv-iv-mpick-status');
    trigger.append(name, status, icon('chev', 'fv-iv-mpick-chev'));
    const action = button('fv-iv-text-btn fv-iv-mpick-act'), bar = el('i', 'fv-iv-mpick-bar');
    root.append(trigger, action, bar);
    const list = el('div', 'fv-iv-mlist fv-glass fv-g3'); list.hidden = true; list.setAttribute('role', 'listbox'); list.setAttribute('aria-label', t('Model', '模型'));
    let last = {info: null, chosen: '', pending: false};
    const states = () => IMAGE_MODELS.map(model => ({model, state: modelState(last.info, model.id)}));
    function open() {
        paintList(); list.hidden = false; trigger.setAttribute('aria-expanded', 'true');
        (host || document.body).append(list); place();
        requestAnimationFrame(() => (list.querySelector('[aria-selected=true]') || list.querySelector('[role=option]'))?.focus());
    }
    function close(focus = true) { if (list.hidden) return; list.hidden = true; trigger.setAttribute('aria-expanded', 'false'); if (focus) trigger.focus({preventScroll: true}); }
    // Fixed to the row, so the panel's clipping and scrolling never cut it.
    function place() {
        const r = root.getBoundingClientRect(), h = Math.min(list.scrollHeight, 320), below = innerHeight - r.bottom - 8;
        list.style.left = `${Math.round(r.left)}px`; list.style.width = `${Math.round(r.width)}px`;
        if (below >= h || below >= r.top) { list.style.top = `${Math.round(r.bottom + 4)}px`; list.style.bottom = ''; }
        else { list.style.top = ''; list.style.bottom = `${Math.round(innerHeight - r.top + 4)}px`; }
    }
    function paintList() {
        list.replaceChildren(...states().map(({model, state}) => {
            const row = el('div', 'fv-iv-mopt'); row.setAttribute('role', 'option'); row.tabIndex = -1; row.dataset.model = model.id; row.dataset.state = state.state;
            row.setAttribute('aria-selected', String(model.id === last.chosen)); row.setAttribute('aria-disabled', String(state.state === 'unavailable'));
            const text = el('span', 'fv-iv-mopt-tx'); text.append(el('b', '', model.name), el('small', '', statusLine(state)));
            row.append(icon('check', 'fv-iv-mopt-mark'), text);
            const act = actionOf(state);
            if (act) {
                const b = button('fv-iv-text-btn fv-iv-mopt-act', {label: act, name: act, use: state.paused ? continueUse() : useOf(model, state)});
                b.addEventListener('click', event => { event.stopPropagation(); if (state.state === 'downloading' && !state.paused) onPause(model); else onInstall(model); close(false); });
                row.append(b);
            }
            if (state.state === 'downloading') { const p = el('i', 'fv-iv-mpick-bar'); p.style.setProperty('--p', String(state.fraction || 0)); row.append(p); }
            tip(row, model.name, useOf(model, state));
            row.addEventListener('click', () => { if (state.state === 'unavailable') return; onPick(model); close(); });
            return row;
        }));
    }
    trigger.addEventListener('click', () => list.hidden ? open() : close());
    listenDialogKey(trigger, 'keydown', event => { if (['ArrowDown', 'ArrowUp'].includes(event.key) && list.hidden) { event.preventDefault(); open(); } });
    listenDialogKey(list, 'keydown', event => {
        const rows = [...list.querySelectorAll('[role=option]:not([aria-disabled=true])')], at = rows.indexOf(document.activeElement);
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); rows[(at + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length]?.focus(); }
        else if (event.key === 'Home' || event.key === 'End') { event.preventDefault(); (event.key === 'Home' ? rows[0] : rows.at(-1))?.focus(); }
        else if ((event.key === 'Enter' || event.key === ' ') && document.activeElement?.matches('[role=option]')) { event.preventDefault(); document.activeElement.click(); }
        else if (event.key === 'Escape' || event.key === 'Tab') { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); } close(event.key === 'Escape'); }
    });
    document.addEventListener('pointerdown', event => { if (!list.hidden && !list.contains(event.target) && !root.contains(event.target)) close(false); }, true);
    addEventListener('resize', () => { if (!list.hidden) place(); });
    action.addEventListener('click', () => { const model = IMAGE_MODELS.find(m => m.id === last.chosen); const state = modelState(last.info, last.chosen); if (!model) return; if (state.state === 'downloading' && !state.paused) onPause(model); else onInstall(model); });

    function paint(info, chosen, pending) {
        last = {info, chosen, pending};
        const model = IMAGE_MODELS.find(m => m.id === chosen) || IMAGE_MODELS[0], state = modelState(info, model.id);
        root.dataset.state = state.state;
        name.textContent = model.name;
        status.replaceChildren(icon(state.unknown ? 'clock' : ICON_OF[state.state] || 'cloud'), el('span', '', statusLine(state)));
        // Closed, the row names the model only; it speaks when something needs doing (to download, downloading, failed).
        status.hidden = state.state === 'ready' || state.state === 'checking';
        tip(trigger, t('Model', '模型'), t(`${model.name}: ${useOf(model, state)}. Click to choose another`, `${model.name}：${useOf(model, state)}。点开换别的模型`));
        const act = actionOf(state); action.hidden = !act; action.replaceChildren(el('span', 'fv-iv-btn-label', act));
        if (act) tip(action, state.paused ? t('Continue download', '继续下载') : state.state === 'downloading' ? t('Pause download', '暂停下载') : state.state === 'failed' ? t('Retry download', '重新下载') : t(`Install ${model.short}`, `安装 ${model.short}`),
            state.paused ? continueUse() : useOf(model, state));
        bar.hidden = state.state !== 'downloading'; bar.style.setProperty('--p', String(state.fraction || 0));
        if (!list.hidden) { paintList(); place(); }
    }
    return {element: root, paint, close};
}
