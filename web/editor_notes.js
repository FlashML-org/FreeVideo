import * as model from './editor_model.js';
import {button, segmented} from './editor_adjust.js';
import {isText} from './editor_text_model.js';
import {tip as setTip} from './editor_tips.js';
// A block's description (Iris 10-11): what the block shows and what 重新生成 uses.
// The timeline shows its first line under the pictures; the Picture page edits it.
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
function glyph(d) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('aria-hidden', 'true'); svg.classList.add('fv-ed-glyph');
    const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', d); svg.append(path); return svg;
}
const AGAIN = 'M13.5 2.5v3.5H10M13.2 6A5.5 5.5 0 1 0 13.5 9.5', PENCIL = 'M10.5 2.5l3 3-8 8h-3v-3z';
const generating = clip => ['queued', 'running'].includes(clip?.pending);
const firstLine = text => (text || '').trim().split('\n')[0];
// A note written about a block (by an agent working on the project) is what the strip shows; without one, the description.
export const stripText = clip => clip.note || firstLine(clip.text);
// Only the picture track grows a description strip, and only once one block has something to show.
export const describedTrack = track => track.kind === 'video' && track.clips.some(clip => !isText(clip) && stripText(clip));
const agentWrote = record => record.tracks.some(track => track.clips.some(clip => clip.text_by === 'agent'));
// Status words appear only in the hover tip and the panels (Iris 10-11).
export function blockStatus(clip, t, fraction = 0, interrupted = false) {
    if (clip.pending === 'running') return t(`Generating ${Math.round(fraction * 100)}%`, `生成中 ${Math.round(fraction * 100)}%`);
    if (clip.pending === 'stopped') return interrupted ? t('Interrupted', '上次中断') : t('Stopped', '已停止');
    if (clip.pending === 'failed') return t('Unfinished', '未完成');
    if (clip.pending) return t('Not generated', '未生成');
    const current = model.takes(clip).find(take => take.index === -1);
    return model.takes(clip).length > 1 ? t(`Version ${current.number}`, `第 ${current.number} 版`) : '';
}
let serial = 0;
export function createBlockNotes({state, t, actions}) {
    const element = el('div', 'fv-ed-notes'), head = el('div', 'fv-ed-notes-head');
    const label = el('label', 'fv-ed-adjust-label', t('Description', '说明'));
    const again = button(t('Regenerate', '重新生成'), {onClick: () => regenerate()}); again.classList.add('fv-ed-notes-again'); again.prepend(glyph(AGAIN));
    setTip(again, {name: t('Regenerate', '重新生成'), use: t('Make this block again from its description', '按这一段的说明重新生成')});
    const box = el('textarea', 'fv-ed-notes-text'); box.id = `fv-ed-notes-${++serial}`; box.rows = 1; label.htmlFor = box.id;
    const versions = el('div', 'fv-ed-notes-versions'), versionsLabel = el('span', 'fv-ed-adjust-label', t('Version', '版本'));
    head.append(label, again); element.append(head, box, versions);
    let block = null, editing = null, picker = null, pickerKey = null, context = null;
    const grow = () => { box.style.height = 'auto'; if (box.scrollHeight) box.style.height = `${box.scrollHeight + 2}px`; };
    function selectedBlock() {
        const ids = state.selection?.kind === 'clip' ? state.selection.ids : [];
        const first = ids.length ? model.find(state.record, ids[0]) : null;
        if (!first || isText(first)) return null;
        const pair = model.pair(state.record, first.id);
        if (!ids.every(id => pair.some(clip => clip.id === id))) return null;
        return state.record.tracks[0].clips.find(clip => pair.some(item => item.id === clip.id)) || pair[0];
    }
    function save(id, text) {
        const clip = model.find(state.record, id); text = text.trim();
        if (!clip || text === clip.text) return;
        try { state.apply(t('Edit description', '修改说明'), record => model.describe(record, [id], text)); } catch (value) { actions.editError?.(value); }
    }
    function commit() { const id = editing; editing = null; if (id) save(id, box.value); }
    box.oninput = () => { editing ||= block?.id || null; grow(); };
    box.onblur = commit;
    box.onkeydown = event => {
        if (event.isComposing || event.keyCode === 229) return;
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); commit(); update(); }
        else if (event.key === 'Escape' && editing) { event.preventDefault(); event.stopPropagation(); editing = null; update(); }
    };
    async function regenerate() {
        commit(); const clip = block && model.find(state.record, block.id);
        if (!clip?.text.trim()) return;
        try { await actions.generate({text: clip.text, label: t('Regenerate', '重新生成')}); } catch (value) { actions.editError?.(value); }
    }
    function choose(index) {
        const id = block?.id; if (!id) return;
        try { state.apply(t('Switch version', '换版本'), record => model.restoreVersion(record, id, index)); } catch (value) { actions.editError?.(value); }
    }
    function paintVersions(off) {
        const list = block?.source ? model.takes(block) : [];
        versions.hidden = list.length <= 1;
        const key = list.map(take => `${take.index}:${take.number}`).join();
        if (key !== pickerKey) {
            picker?.dispose(); pickerKey = key;
            picker = segmented(list.map(take => ({value: take.index, label: String(take.number)})), {value: -1, onChange: choose});
            picker.classList.add('fv-ed-notes-picker');
            versions.replaceChildren(versionsLabel, picker);
        }
        [...picker.children].forEach((control, index) => {
            control.setAttribute('aria-label', t(`Version ${list[index].number}`, `第 ${list[index].number} 版`));
            control.disabled = off || !list[index].usable;
        });
        picker.value = -1;
    }
    function update(ctx = context) {
        context = ctx;
        const next = selectedBlock();
        if (editing && next?.id !== editing) commit();
        block = next;
        const off = !block || !!ctx?.busy || generating(block);
        box.disabled = off;
        if (document.activeElement !== box || editing !== block?.id) { box.value = block?.text || ''; grow(); }
        again.hidden = actions.generationAvailable?.() === false;
        again.disabled = off || !block.text.trim() || !actions.canGenerate?.(block.text);
        // Blue once the description no longer matches the one the version in use was made from.
        again.classList.toggle('fv-primary', !again.disabled && model.describedSince(block));
        paintVersions(off);
    }
    // Timeline blocks: description strip, block number, and the pencil on the user's own blocks.
    const items = new Map();
    let tip = null, tipTimer = 0, inline = null;
    const hideTip = () => { clearTimeout(tipTimer); if (tip) tip.hidden = true; };
    function showTip(item) {
        const view = item.element.closest('.fv-ed-track-view'), clip = item.clip; if (!view || !item.element.isConnected) return;
        const fraction = Number(item.element.style.getPropertyValue('--fv-ed-progress')) || 0;
        const text = [clip.note || clip.text.trim(), blockStatus(clip, t, fraction, !!actions.interrupted?.(clip))].filter(Boolean).join(' · ');
        if (!text) return;
        if (!tip) { tip = el('div', 'fv-ed-note-tip'); tip.setAttribute('role', 'tooltip'); }
        if (tip.parentElement !== view) view.append(tip);
        tip.textContent = text; tip.hidden = false;
        const bounds = view.getBoundingClientRect(), strip = item.note.getBoundingClientRect();
        tip.style.left = `${Math.max(8, Math.min(strip.left - bounds.left, bounds.width - tip.offsetWidth - 8))}px`;
        tip.style.top = `${Math.max(4, strip.top - bounds.top - tip.offsetHeight - 6)}px`;
    }
    function decorate(item, {noted, index, record}) {
        if (!item.note) {
            item.note = el('span', 'fv-ed-note'); item.number = el('span', 'fv-ed-block-number'); item.mark = el('span', 'fv-ed-user-mark');
            item.mark.append(glyph(PENCIL)); item.element.append(item.note, item.number, item.mark);
            item.note.addEventListener('pointerenter', () => { hideTip(); tipTimer = setTimeout(() => showTip(item), 500); });
            item.note.addEventListener('pointerleave', hideTip);
        }
        items.set(item.clip.id, item);
        item.element.dataset.noted = String(noted);
        item.note.hidden = item.number.hidden = !noted;
        item.note.textContent = stripText(item.clip); item.number.textContent = String(index + 1);
        // The pencil marks blocks an agent leaves alone; a project nobody else wrote in has none.
        item.mark.hidden = !agentWrote(record) || !!item.clip.text_by;
    }
    function stripAt(event) {
        for (const item of items.values()) {
            if (!item.element.isConnected || item.note.hidden) continue;
            const r = item.note.getBoundingClientRect();
            if (event.clientX >= r.left && event.clientX <= r.right && event.clientY >= r.top && event.clientY <= r.bottom) return item;
        }
        return null;
    }
    function closeInline() { inline?.remove(); inline = null; }
    // Pressing a block captures the pointer on the timeline, so double-clicks arrive there.
    function editInPlace(event) {
        const item = stripAt(event); if (!item) return false;
        const clip = model.find(state.record, item.clip.id);
        if (!clip || model.pair(state.record, clip.id).some(generating)) return false;
        event.preventDefault(); hideTip(); closeInline();
        if (clip.text.trim().includes('\n')) { box.focus(); return true; }
        const input = inline = el('input', 'fv-ed-note-input'); input.value = clip.text; input.setAttribute('aria-label', t('Description', '说明'));
        input.style.left = item.element.style.left; input.style.width = item.element.style.width;
        let done = false;
        const finish = keep => { if (done) return; done = true; const text = input.value; closeInline(); if (keep) save(clip.id, text); };
        input.onkeydown = key => {
            if (key.isComposing || key.keyCode === 229) return;
            if (key.key === 'Enter') { key.preventDefault(); finish(true); }
            else if (key.key === 'Escape') { key.preventDefault(); key.stopPropagation(); finish(false); }
        };
        input.onblur = () => finish(true);
        item.element.parentNode.append(input); input.focus(); input.select();
        return true;
    }
    function attach(view) {
        const dblclick = event => editInPlace(event);
        view.addEventListener('dblclick', dblclick); view.addEventListener('pointerdown', hideTip, true);
        return () => { view.removeEventListener('dblclick', dblclick); view.removeEventListener('pointerdown', hideTip, true); };
    }
    function dispose() {
        hideTip(); tip?.remove(); closeInline(); picker?.dispose(); again.dispose();
        box.oninput = box.onblur = box.onkeydown = null; items.clear(); element.remove();
    }
    return {element, update, decorate, attach, dispose};
}
