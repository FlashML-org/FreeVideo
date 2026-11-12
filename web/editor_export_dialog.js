import * as model from './editor_model.js';
import {clipPictureSize, differentPictureShape} from './editor_frame.js';
import {transportTime} from './editor_player.js';
import {selectedRange} from './editor_transport_model.js';
import {isolateDialogKeys, listenDialogKey, routeDialogKey} from './dialog_keys.js';

export const EXPORT_SETTINGS_KEY = 'freevideo.editor.export-settings';
const defaults = {short: null, quality: 'high', range: 'whole', only: null, sound_format: 'm4a'};
const active = new WeakSet();
const style = document.createElement('link');
style.rel = 'stylesheet'; style.href = new URL('editor_export_dialog.css', import.meta.url).href;
const stylesReady = 'sheet' in style ? new Promise(resolve => { style.onload = style.onerror = resolve; }) : Promise.resolve();
document.head.append(style);
const el = (tag, cls, text) => {
    const value = document.createElement(tag); value.className = cls;
    if (text != null) value.textContent = text;
    return value;
};

export function exportSizes(record) {
    const short = Math.min(record.width, record.height), long = Math.max(record.width, record.height);
    // Same rounding as the engine's export (nearest even long side), so the dialog and the saved work agree.
    const sized = n => {
        const other = Math.max(2, 2 * Math.floor((long * n + short) / (2 * short)));
        return record.width >= record.height ? {width: other, height: n} : {width: n, height: other};
    };
    return [{short: null, width: record.width, height: record.height}, ...[1080, 720, 480].filter(n => n < short).map(n => ({
        short: n, ...sized(n),
    }))];
}

// Q14: the selected range is the one playback uses; a selected part brings its linked partner along.
export function exportRange(record, selection) {
    const picked = selection?.kind === 'clip' ? {...selection, ids: [...model.linkedIds(record, selection.ids)]} : selection;
    const range = selectedRange(record, picked, model.exportFrames(record));
    if (!range) return null;
    const start = Math.max(0, range.start), end = range.end;
    return Number.isSafeInteger(start) && Number.isSafeInteger(end) && end > start ? {start, frames: end - start} : null;
}

function settings(record, selection, value) {
    value = value && typeof value === 'object' ? value : {};
    return {
        short: exportSizes(record).some(size => size.short === value.short) ? value.short : defaults.short,
        quality: ['high', 'standard', 'small'].includes(value.quality) ? value.quality : defaults.quality,
        range: value.range === 'selected' && exportRange(record, selection) ? 'selected' : 'whole',
        only: value.only === 'sound' ? 'sound' : null,
        sound_format: value.sound_format === 'wav' ? 'wav' : 'm4a',
    };
}

export function exportOptions(record, selection, value) {
    const clean = settings(record, selection, value), options = {};
    if (clean.only === 'sound') { options.only = 'sound'; options.sound_format = clean.sound_format; }
    else {
        if (clean.quality !== 'high') options.quality = clean.quality;
        if (clean.short != null) options.short = clean.short;
    }
    if (clean.range === 'selected') options.range = exportRange(record, selection);
    return options;
}

const overlapping = (clip, range) => !range || clip.start < range.start + range.frames && clip.start + clip.frames > range.start;
// Q16: sound-only needs some sound in the range.
export const hasSound = (record, range) => record.tracks.some(track => track.kind === 'audio'
    && track.clips.some(clip => clip.source && clip.volume !== 0 && overlapping(clip, range)));
// Q15: seconds of picture still generating with no earlier version; they export as black.
export function generatingSeconds(record, range) {
    const start = range?.start ?? 0, stop = range ? range.start + range.frames : model.exportFrames(record);
    const frames = record.tracks[0].clips.filter(clip => !clip.source && ['queued', 'running'].includes(clip.pending))
        .reduce((sum, clip) => sum + Math.max(0, Math.min(stop, clip.start + clip.frames) - Math.max(start, clip.start)), 0);
    return Math.ceil(frames / model.FPS);
}
function croppedParts(record, sources, range) {
    return record.tracks[0].clips.filter(clip => clip.fit !== 'contain'
        && (!range || clip.start < range.start + range.frames && clip.start + clip.frames > range.start)
        && differentPictureShape(clipPictureSize(clip, sources), record));
}

export async function openExportDialog({parent, state, sources, t = en => en, onError = () => {}}) {
    // Only the first trigger owns confirmation, even while the stylesheet loads.
    if (active.has(parent)) return null;
    let resolve;
    const result = new Promise(done => { resolve = done; });
    active.add(parent);
    await stylesReady;
    if (!parent.isConnected || !parent.open) { active.delete(parent); resolve(null); return result; }
    let saved;
    try { saved = JSON.parse(localStorage.getItem(EXPORT_SETTINGS_KEY)); } catch { /* Private browsing or old data. */ }
    const value = settings(state.record, state.selection, saved), returnFocus = document.activeElement;
    const dialog = el('dialog', 'fv-ed-export-dialog');
    dialog.setAttribute('aria-label', t('Export', '导出'));
    const heading = el('h2', '', t('Export', '导出')), rows = el('div', 'fv-ed-export-rows');
    const notices = el('div', 'fv-ed-export-notices');
    const links = el('div', 'fv-ed-export-links'), footer = el('div', 'fv-ed-export-footer');
    const summary = el('span', 'fv-ed-export-summary'), buttons = el('div', 'fv-ed-export-buttons');
    const button = (label, action, cls = '') => {
        const node = el('button', cls, label); node.type = 'button'; node.onclick = action; return node;
    };
    const groups = [];
    function group(name, label, choices, host = rows) {
        const row = el('div', 'fv-ed-export-row'), title = el('span', 'fv-ed-export-label', label);
        const controls = el('div', 'fv-ed-export-segments');
        controls.setAttribute('role', 'group'); controls.setAttribute('aria-label', label); controls.dataset.option = name;
        for (const [choice, text] of choices) {
            const node = button(text, () => { value[name] = choice; update(); });
            node.dataset.value = String(choice ?? 'same'); node.dataset.label = text; controls.append(node);
        }
        row.append(title, controls); host.append(row); groups.push({name, row, controls, choices});
        return row;
    }
    group('short', t('Frame size', '画面大小'), exportSizes(state.record).map(size => [size.short,
        size.short == null ? t('Same as the edit', '与剪辑相同') : `${size.width} × ${size.height}`]));
    group('quality', t('Quality', '画质'), [['high', t('High', '高')], ['standard', t('Standard', '标准')], ['small', t('Small file', '省空间')]]);
    group('range', t('Range', '范围'), [['whole', t('Whole edit', '整条')], ['selected', t('Selected range', '选中的范围')]]);
    const contentRow = group('only', t('Content', '内容'), [[null, t('Video and audio', '画面和声音')], ['sound', t('Audio only', '只声音')]]);
    const formatRow = group('sound_format', t('Audio only', '只声音'), [['m4a', 'M4A'], ['wav', 'WAV']], contentRow);
    formatRow.classList.add('fv-ed-export-format'); formatRow.querySelector('.fv-ed-export-label').hidden = true;
    const chosenRange = () => value.range === 'selected' ? exportRange(state.record, state.selection) : null;
    const affected = () => croppedParts(state.record, sources, chosenRange());
    const review = button(t('Review them', '逐段查看'), () => {
        const parts = affected(), ids = state.selection?.ids || [], index = parts.findIndex(c => ids.includes(c.id));
        const clip = parts[(index + 1) % parts.length];
        close(null);
        if (clip) { state.select({kind: 'clip', ids: [clip.id]}); state.seek(clip.start); }
    });
    const fit = button(t('Fit all', '全部完整显示'), () => {
        try { state.apply(t('Fit all', '全部完整显示'), record => affected().reduce((r, clip) => model.setPictureFit(r, clip.id, 'contain'), record)); }
        catch (error) { onError(error); }
        update();
    });
    const pending = el('p', 'fv-ed-export-pending');
    links.append(review, fit); notices.append(pending, links);
    const cancel = button(t('Cancel', '取消'), () => close(null), 'fv-ed-export-cancel');
    const confirm = button(t('Export', '导出'), () => {
        try { localStorage.setItem(EXPORT_SETTINGS_KEY, JSON.stringify(value)); } catch { /* Export still works without storage. */ }
        close(exportOptions(state.record, state.selection, value));
    }, 'fv-primary fv-ed-export-confirm');
    buttons.append(cancel, confirm); footer.append(summary, buttons); dialog.append(heading, rows, notices, footer);
    function update() {
        if (!exportRange(state.record, state.selection)) value.range = 'whole';
        const audible = hasSound(state.record, chosenRange());
        if (!audible) value.only = null;
        const sound = value.only === 'sound';
        for (const {name, controls, choices} of groups) [...controls.children].forEach((node, i) => {
            node.setAttribute('aria-pressed', String(value[name] === choices[i][0]));
            node.disabled = sound && ['short', 'quality'].includes(name)
                || name === 'range' && choices[i][0] === 'selected' && !exportRange(state.record, state.selection)
                || name === 'only' && choices[i][0] === 'sound' && !audible;
        });
        formatRow.hidden = !sound;
        const range = chosenRange(), parts = affected(), n = parts.length;
        const seconds = sound ? 0 : generatingSeconds(state.record, range);
        pending.hidden = !seconds;
        pending.textContent = t(`${seconds} s still generating; that part exports as black.`, `有 ${seconds} 秒还在生成，导出后这部分是黑屏`);
        links.hidden = sound || !n; notices.hidden = links.hidden && pending.hidden;
        if (dialog.open) fitSegments();
        fit.disabled = parts.some(clip => ['queued', 'running'].includes(clip.pending));
        const size = exportSizes(state.record).find(size => size.short === value.short) || state.record;
        // Same rounding as the player's total, so one edit never reads 0:31 there and 0:32 here.
        const total = model.exportFrames(state.record), part = range && !(range.start === 0 && range.frames === total);
        const time = part ? `${transportTime(range.start)} – ${transportTime(range.start + range.frames)}` : transportTime(total);
        summary.textContent = `${time} · ${sound ? value.sound_format.toUpperCase() : `${size.width} × ${size.height}`}`;
    }
    let closed = false;
    const cleanups = [];
    function close(options) {
        if (closed) return;
        closed = true; cleanups.splice(0).forEach(stop => stop());
        if (dialog.open) dialog.close(); dialog.remove(); active.delete(parent);
        if (returnFocus?.isConnected) returnFocus.focus({preventScroll: true});
        resolve(options);
    }
    cleanups.push(listenDialogKey(dialog, 'keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); close(null); }
        else if (event.key === 'Tab') {
            event.preventDefault();
            const focusable = [...dialog.querySelectorAll('button')].filter(node => !node.disabled && !node.closest('[hidden]'));
            const current = focusable.indexOf(document.activeElement);
            focusable[(current + (event.shiftKey ? -1 : 1) + focusable.length) % focusable.length]?.focus();
        }
    }));
    dialog.oncancel = event => { event.preventDefault(); event.stopPropagation(); close(null); };
    dialog.onclose = () => close(null);
    // Parent and host capture shortcuts must route to this modal before the editor.
    cleanups.push(isolateDialogKeys(dialog, event => routeDialogKey(event, dialog)));
    const parentClosed = () => close(null);
    parent.addEventListener('close', parentClosed);
    cleanups.push(() => parent.removeEventListener('close', parentClosed), state.subscribe(update));
    // Pointer events stay inside the modal; names stay literal for the shared key-listener guard.
    const stop = event => event.stopPropagation();
    dialog.addEventListener('pointermove', stop); dialog.addEventListener('mousemove', stop);
    dialog.addEventListener('pointerdown', stop); dialog.addEventListener('pointerup', stop);
    dialog.addEventListener('mousedown', stop); dialog.addEventListener('mouseup', stop);
    dialog.addEventListener('click', stop); dialog.addEventListener('dblclick', stop);
    dialog.addEventListener('wheel', stop); dialog.addEventListener('contextmenu', stop);
    // Equal shares unless a choice's words, bold when picked, would not fit on one line.
    const fitSegments = () => {
        for (const {controls} of groups) {
            // A hidden row has no width yet; it is measured again when update() shows it.
            if (!controls.clientWidth) { delete controls.dataset.fit; continue; }
            controls.dataset.fit = 'measure';
            const nodes = [...controls.children], css = getComputedStyle(controls);
            const room = controls.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight) - parseFloat(css.columnGap || 0) * (nodes.length - 1);
            const tight = nodes.some(node => node.offsetWidth > room / nodes.length + .5);
            if (tight) controls.dataset.fit = 'text'; else delete controls.dataset.fit;
        }
    };
    addEventListener('resize', fitSegments); cleanups.push(() => removeEventListener('resize', fitSegments));
    parent.append(dialog); update(); dialog.showModal(); fitSegments(); confirm.focus();
    return result;
}
