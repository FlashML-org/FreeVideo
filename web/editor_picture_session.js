// One ephemeral transaction shared by controls and preview; never saved in records.
import {find, setPicture} from './editor_model.js';
import {PICTURE_DEFAULTS, pictureFields} from './editor_picture_fields.js';
const sessions = new WeakMap();
export function pictureSession(state, t) {
    if (sessions.has(state)) return sessions.get(state);
    let draft = null, panelOpen = false; const listeners = new Set();
    const emit = () => { for (const listener of listeners) listener(); };
    const cancel = () => { if (draft) { draft = null; emit(); } };
    const session = {
        get draft() { return draft; },
        get panelOpen() { return panelOpen; },
        setPanelOpen(value) {
            if (panelOpen === value) return;
            panelOpen = value;
            if (!value) cancel();
            emit();
        },
        selected() {
            const ids = state.selection?.kind === 'clip' ? state.selection.ids : [];
            return state.record.tracks[0].clips.find(c => c.source && ids.includes(c.id)
                && ids.every(id => id === c.id || id === c.link)) || null;
        },
        output(clip) { return !!clip && (panelOpen && session.selected()?.id === clip.id || draft?.id === clip.id); },
        previewRow(row, position) {
            const clip = panelOpen ? session.selected() : draft && find(state.record, draft.id);
            return clip ? {clip, frame: clip.in + Math.max(0, Math.min(clip.frames - 1, position - clip.start))} : row;
        },
        editReason(clip) {
            if (!clip?.source) return t('This part has no video', '这一段没有画面');
            const ids = [clip.id, clip.link].filter(Boolean);
            const tracks = state.record.tracks.filter(track => track.clips.some(c => ids.includes(c.id)));
            if (tracks.some(track => track.clips.some(c => ids.includes(c.id) && ['queued', 'running'].includes(c.pending)))
                || state.record.replacements?.some(tx => tx.originals.some(track => track.clips.some(c => ids.includes(c.id)))
                    && tx.clips.some(track => track.clips.some(c => ['queued', 'running'].includes(c.pending)))))
                return t('Cancel generation before changing this part.', '先取消生成，再改这一段。');
            return tracks.some(track => track.locked) ? t('Unlock the track before changing this part.', '先解锁轨道，再改这一段。') : '';
        },
        canEdit(clip) { return !session.editReason(clip); },
        clip(clip) { return draft && draft.id === clip?.id ? {...clip, ...PICTURE_DEFAULTS, ...draft.fields} : clip; },
        begin(id, mode = 'adjust') {
            if (draft?.id === id && draft.mode === mode) return;
            cancel(); const clip = find(state.record, id);
            if (session.canEdit(clip)) { draft = {id, mode, original: pictureFields(clip), fields: pictureFields(clip), baseline: JSON.stringify(clip), size: [state.record.width, state.record.height].join(',')}; emit(); }
        },
        preview(fields) { if (draft) { draft.fields = pictureFields({...PICTURE_DEFAULTS, ...draft.fields, ...fields}); emit(); } },
        commit() {
            if (!draft) return;
            const {id, fields} = draft; draft = null;
            if (session.canEdit(find(state.record, id))) state.apply(t('Video', '画面'), r => setPicture(r, id, {...PICTURE_DEFAULTS, ...fields})); emit();
        },
        cancel,
        // Escape stops a preview drag, and that is the whole of that key press (the editor then leaves the selection alone).
        // A number box of the picture page takes its own Escape (clearing what was typed or its correction) the same way.
        cancelDraft() {
            if (draft) { cancel(); return true; }
            const box = document.activeElement;
            if (box?.tagName === 'INPUT' && box.type !== 'range' && box.closest?.('.fv-ed-adjust-picture') && box.onkeydown) { box.onkeydown(new KeyboardEvent('keydown', {key: 'Escape'})); return true; }
            return false;
        },
        change(id, fields) { cancel(); if (!session.canEdit(find(state.record, id))) return; state.apply(t('Video', '画面'), r => setPicture(r, id, fields)); },
        keyboard(event) {
            const input = event.target?.closest?.('.fv-ed-adjust-picture') && event.target?.tagName === 'INPUT';
            if ((event.isComposing || event.keyCode === 229) && (draft || input)) { event.stopPropagation(); return true; }
            if (event.key === 'Escape' && input && event.target.type !== 'range' && !draft) { event.target.onkeydown?.(event); return true; }
            if (!draft || !['Escape', 'Enter'].includes(event.key)) return false;
            if (event.key === 'Enter' && draft.mode !== 'crop') return false;
            event.preventDefault(); event.stopPropagation(); event.key === 'Escape' ? cancel() : session.commit(); return true;
        },
        subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
        dispose() { cancel(); unsubscribe(); listeners.clear(); sessions.delete(state); },
    };
    const unsubscribe = state.subscribe(event => { if ((event.type === 'selection' || (event.type === 'playhead' && draft?.mode !== 'slider')) || (event.type === 'record' && draft && (!session.canEdit(find(state.record, draft.id)) || draft.baseline !== JSON.stringify(find(state.record, draft.id)) || draft.size !== [state.record.width, state.record.height].join(',')))) cancel(); });
    sessions.set(state, session); return session;
}
