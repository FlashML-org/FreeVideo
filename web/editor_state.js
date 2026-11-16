// One bounded history holds record edits and committed range gestures.
// Generation results update record snapshots; ranges never change the record.
import {parse, EditError, duration, linkedIds, find, copyName, planRange} from './editor_model.js';
import {assertLockedUnchanged, unlockedSelection} from './editor_audio_model.js';
const freeze = value => {
    if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); }
    return value;
};
const validFrame = value => Number.isSafeInteger(value) && value >= 0 && value <= 1e7;
export function createEditorState(record, {limit = 200} = {}) {
    if (!Number.isSafeInteger(limit) || limit < 0 || limit > 10000) throw new EditError('range');
    let current = parse(record), selection = null, rangeDraft = null, playhead = 0, zoom = 12;
    let maxSerial = current.serial;
    const retainSerial = record => { maxSerial = Math.max(maxSerial, record.serial); return record.serial < maxSerial ? parse({...record, serial: maxSerial}) : record; };
    let undo = [], redo = [], step = 0, making = () => false; const listeners = new Set();
    const remember = entry => { step++; undo = limit ? [...undo, {...entry, at: step}].slice(-limit) : []; redo = []; };
    const selected = () => ({selection, rangeDraft});
    const restore = value => { selection = value.selection; rangeDraft = value.rangeDraft; };
    const emit = (type, label) => {
        const event = Object.freeze({type, ...(label === undefined ? {} : {label})});
        for (const listener of [...listeners]) listener(event);
    };
    function normalized(value, project = current) {
        value = unlockedSelection(project, value);
        if (value === null) return null;
        if (!value || typeof value !== 'object') throw new EditError('selection');
        if (value.kind === 'clip') {
            const available = new Set(project.tracks.flatMap(track => track.clips.map(clip => clip.id)));
            if (!Array.isArray(value.ids) || !value.ids.length || value.ids.some(id => typeof id !== 'string' || !available.has(id))) throw new EditError('selection');
            return freeze({kind: 'clip', ids: [...linkedIds(project, value.ids)]});
        }
        if (!['gap', 'range'].includes(value.kind) || !validFrame(value.start) || !validFrame(value.frames) || value.frames < 1 || !validFrame(value.start + value.frames)) throw new EditError('selection');
        const tracks = value.kind === 'gap' ? [value.track] : value.tracks;
        if (!Array.isArray(tracks) || !tracks.length || new Set(tracks).size !== tracks.length || tracks.some(id => !project.tracks.some(track => track.id === id))) throw new EditError('selection');
        if (value.kind === 'range' && value.parts !== undefined && (!Array.isArray(value.parts) || !value.parts.length || value.parts.length > 500
            || value.parts.some(count => !validFrame(count) || count < 48 || count % 24 !== 0)
            || value.parts.reduce((sum, count) => sum + count, 0) !== value.frames)) throw new EditError('selection');
        return freeze(value.kind === 'gap' ? {kind: 'gap', track: value.track, start: value.start, frames: value.frames}
            : {kind: 'range', start: value.start, frames: value.frames, tracks: [...tracks], ...(value.parts === undefined ? {} : {parts: [...value.parts]})});
    }
    function reconcile() {
        if (selection?.kind === 'clip') {
            const ids = selection.ids.filter(id => find(current, id));
            const value = ids.length ? normalized({kind: 'clip', ids}) : null;
            if (JSON.stringify(selection) !== JSON.stringify(value)) { selection = value; emit('selection'); }
        } else if (selection && (selection.kind === 'gap' ? !current.tracks.some(track => track.id === selection.track)
            : selection.tracks.some(id => !current.tracks.some(track => track.id === id)))) { selection = null; emit('selection'); }
        if (playhead > duration(current)) { playhead = duration(current); emit('playhead'); }
    }
    function travel(from, to, side) {
        if (!from.length) return current;
        step++; const entry = from.pop();
        to.push(entry.record ? {...entry, record: current} : entry);
        if (entry.record) current = retainSerial(entry.record);
        if (entry[side]) restore(entry[side]);
        if (entry.record) emit('record', entry.label);
        reconcile();
        if (entry[side]) emit('selection', entry.label);
        return current;
    }
    const session = {
        get record() { return current; }, get selection() { return selection; }, get playhead() { return playhead; }, get zoom() { return zoom; },
        // Text belongs to the range draft, not to a gesture: typing keeps its native undo.
        get rangeText() { return selection?.kind === 'range' ? rangeDraft?.text ?? null : null; },
        get canUndo() { return undo.length > 0; }, get canRedo() { return redo.length > 0; },
        // What the next undo and redo would do, for their labels.
        get undoLabel() { return undo.at(-1)?.label ?? null; }, get redoLabel() { return redo.at(-1)?.label ?? null; },
        apply(label, change, {clearRange = false, checkpoint = false, selectionAfter, rangeText = null} = {}) {
            const next = retainSerial(parse(change(current))); // Validate before touching history or UI state.
            assertLockedUnchanged(current, next);
            if (JSON.stringify(next) === JSON.stringify(current)) return current;
            const cleared = clearRange && selection?.kind === 'range';
            const selectionChanged = cleared || selectionAfter !== undefined;
            const entry = {record: current, label, ...(selectionChanged ? {before: selected(), after: {selection: normalized(selectionAfter ?? null, next), rangeDraft: selectionAfter?.kind === 'range' && typeof rangeText === 'string' ? {text: rangeText} : null}} : {})};
            // Generation is one step like any other: one undo takes it back and the edits before it stay reachable.
            // That undo (and its redo) waits while a part is still being made: see holdGeneration.
            remember(checkpoint ? {...entry, generation: true} : entry);
            if (selectionChanged) restore(entry.after);
            current = next; emit('record', label); reconcile();
            if (selectionChanged) emit('selection');
            return current;
        },
        undo() { return undo.at(-1)?.generation && making() ? current : travel(undo, redo, 'before'); },
        redo() { return redo.at(-1)?.generation && making() ? current : travel(redo, undo, 'after'); },
        // Tells whether a part is being made; until it is done, a generation step is neither undone nor redone.
        holdGeneration(busy) { making = typeof busy === 'function' ? busy : () => false; },
        // Counts the steps taken through the history, so a caller can tell that its own step is still the newest.
        get step() { return step; },
        // Takes the newest step back as if it never happened: nothing is left to redo.
        withdraw(at) { if (at !== step || !undo.length) return false; travel(undo, redo, 'before'); redo.pop(); return true; },
        settle(change, {after = null} = {}) {
            const attempt = record => { try { return retainSerial(parse(change(record))); } catch (error) { if (error instanceof EditError) return record; throw error; } };
            // With {after: mark}, the change is about a generation that took step mark: the undo steps up to and
            // including it keep what came before it, so undoing that generation still brings back the drafts.
            const update = entry => entry.record && !(after != null && entry.at <= after) ? {...entry, record: attempt(entry.record)} : entry;
            const next = retainSerial(parse(change(current))), nextUndo = undo.map(update), nextRedo = redo.map(update);
            const changed = JSON.stringify(next) !== JSON.stringify(current);
            current = next; undo = nextUndo; redo = nextRedo;
            if (changed) { emit('record'); reconcile(); } return current;
        },
        replace(record) { const next = parse(record); maxSerial = next.serial; current = next; step++; undo = []; redo = []; selection = null; rangeDraft = null; playhead = 0; emit('record'); emit('selection'); emit('playhead'); return current; },
        restoreView(value) {
            // View drafts are untrusted and may refer to content changed elsewhere.
            let next = null;
            try {
                next = normalized(value?.selection ?? null);
                if (next && next.kind !== 'clip') {
                    const tracks = next.kind === 'gap' ? [next.track] : next.tracks;
                    if (current.tracks.some(track => (next.kind === 'range' || tracks.includes(track.id)) && track.clips.some(clip =>
                        clip.start < next.start + next.frames && clip.start + clip.frames > next.start))) {
                        if (next.kind === 'gap') next = null;
                        else planRange(current, next); // Validate an existing-content range without applying it.
                    }
                }
            } catch { next = null; }
            session.select(next);
            if (validFrame(value?.playhead)) session.seek(value.playhead);
            if (typeof value?.zoom === 'number' && Number.isFinite(value.zoom) && value.zoom > 0) session.setZoom(value.zoom);
        },
        adopt({id, revision, nameSuffix}) {
            const stamp = record => parse({...record, id, revision, ...(nameSuffix ? {name: copyName(record.name, nameSuffix)} : {})});
            const update = entry => entry.record ? {...entry, record: stamp(entry.record)} : entry;
            const next = stamp(current), nextUndo = undo.map(update), nextRedo = redo.map(update);
            current = next; undo = nextUndo; redo = nextRedo; emit('record'); return current;
        },
        // A pasted range brings its own description (text); otherwise a range keeps the one being typed.
        select(value, {history = false, text} = {}) {
            const next = normalized(value);
            if (JSON.stringify(next) !== JSON.stringify(selection) || (next?.kind === 'range' && typeof text === 'string')) {
                const before = selected(), after = {selection: next, rangeDraft: next?.kind === 'range'
                    ? typeof text === 'string' ? {text} : selection?.kind === 'range' ? rangeDraft : {text: null} : null};
                if (history && (selection?.kind === 'range' || next?.kind === 'range')) remember({before, after});
                restore(after); emit('selection');
            }
            return selection;
        },
        setRangeText(value) { if (typeof value !== 'string') throw new EditError('text'); if (selection?.kind === 'range') rangeDraft.text = value; },
        seek(frame) { if (!validFrame(frame)) throw new EditError('range'); const next = Math.min(frame, Math.max(duration(current), selection?.kind === 'range' ? selection.start + selection.frames : 0)); if (next !== playhead) { playhead = next; emit('playhead'); } return playhead; },
        setZoom(value) { if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) throw new EditError('range'); if (value !== zoom) { zoom = value; emit('zoom'); } return zoom; },
        subscribe(listener) { if (typeof listener !== 'function') throw new EditError('listener'); listeners.add(listener); return () => listeners.delete(listener); },
    };
    return Object.freeze(session);
}
