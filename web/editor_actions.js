// Shared selection actions (record is a parsed editor_model project):
// linkedIds      (record, ids)                 <-> linked picture/audio selection
// splitAt        (record, ids, frame, {by})    <-> Split (B)
// trimAt         (record, ids, frame, edge, {by}) <-> Trim before/after (Q/W)
// removeParts    (record, ids)                 <-> Delete / Backspace / Cut
// rippleRemoveParts (record, ids)              <-> Ripple delete (Shift+Delete / Shift+Backspace)
// removeGaps     (record, gap|null)            <-> Remove gaps / Delete a gap
// duplicateParts (record, ids)                 <-> Duplicate (D)
// copyTo         (record, ids, at)             <-> Copy + Paste at the playhead
// moveParts      (record, ids, {frames, track}) <-> release a group drag
// Frames are integer timeline frames; move frames are a relative delta, clamped
// at zero like a group drag. Tracks stay fixed: null keeps all original lanes;
// an explicit track must be an original lane of the group. No snapping here.
// A cut by the user (the default) takes the block from the agent; by: 'agent' keeps its mark.
import * as model from './editor_model.js';
import {assertUnlocked, isText} from './editor_text_model.js';
import {copyContent, pasteContent, playheadTargets, removableGaps} from './editor_room.js';

const end = clip => clip.start + clip.frames;
const codes = new Set(['busy', 'locked', 'audio_full', 'overlap', 'range', 'text']);
const check = ok => { if (!ok) throw new model.EditError('range'); };
// Low-level model/clipboard validation has additional codes. This API exposes
// only the shared editing codes, without changing the page clipboard contract.
function edit(run) {
    try { return run(); }
    catch (error) {
        if (!(error instanceof model.EditError) || codes.has(error.code)) throw error;
        throw new model.EditError('range');
    }
}
function frameNumber(frame) { check(Number.isSafeInteger(frame) && frame >= 0 && frame <= 1e7); }
export function linkedIds(record, ids) {
    check(Array.isArray(ids) && ids.every(id => typeof id === 'string'));
    return [...model.linkedIds(record, ids)];
}
function cutTargets(record, ids, frame) {
    frameNumber(frame);
    const targets = playheadTargets(record, linkedIds(record, ids), frame);
    check(targets.length);
    assertUnlocked(record, linkedIds(record, targets));
    return targets;
}
export function splitAt(record, ids, frame, {by = 'user'} = {}) {
    return edit(() => cutTargets(record, ids, frame).reduce((next, id) => model.split(next, id, frame, {by}), record));
}
export function trimAt(record, ids, frame, edge, {by = 'user'} = {}) {
    check(['start', 'end'].includes(edge));
    return edit(() => cutTargets(record, ids, frame).reduce((next, id) => model.trim(next, id, edge, frame, {minimumFrames: 1, by}), record));
}
export function removeParts(record, ids) {
    return edit(() => model.remove(record, linkedIds(record, ids)));
}
// Delete the parts, then close only the time they took (a gap that was already there stays), right to left
// so earlier spans keep their frames. A span that cannot close stays open and is listed in `skipped`.
// Text and subtitles only lie over the picture, so deleting them closes nothing.
export function rippleRemoveParts(record, ids) {
    ids = linkedIds(record, ids);
    const taken = record.tracks.flatMap(track => track.clips).filter(clip => ids.includes(clip.id) && !isText(clip))
        .sort((a, b) => a.start - b.start), spans = [];
    for (const clip of taken) {
        const last = spans.at(-1);
        if (last && clip.start <= last.end) last.end = Math.max(last.end, end(clip));
        else spans.push({start: clip.start, end: end(clip)});
    }
    let next = removeParts(record, ids); const skipped = [];
    for (const span of spans.reverse()) {
        try { next = edit(() => model.closeSpan(next, span.end, span.end - span.start)); }
        catch (error) {
            if (!(error instanceof model.EditError) || !['busy', 'locked', 'overlap'].includes(error.code)) throw error;
            skipped.push({start: span.start, track: error.track, code: error.code});
        }
    }
    return {record: next, closed: spans.length - skipped.length, skipped: skipped.reverse()};
}
export function removeGaps(record, gap) {
    if (gap !== null) {
        check(gap && record.tracks.some(track => track.id === gap.track));
        frameNumber(gap.start);
    }
    const skipped = []; let removed = 0;
    // Right to left keeps skipped timestamps relative to the input project.
    for (const item of removableGaps(record, gap && {...gap, kind: 'gap'}).reverse()) {
        try { record = edit(() => model.closeGap(record, item.track, item.start)); removed++; }
        catch (error) {
            if (!(error instanceof model.EditError) || !['busy', 'locked', 'overlap'].includes(error.code)) throw error;
            skipped.push({start: item.start, track: error.track ?? item.track, code: error.code});
        }
    }
    return {record, removed, skipped: skipped.reverse()};
}
function contentFor(record, ids) {
    ids = linkedIds(record, ids);
    return edit(() => {
        try { return copyContent(record, ids); }
        catch (error) {
            if (error instanceof model.EditError && error.code === 'not_generated'
                && ids.some(id => ['queued', 'running'].includes(model.find(record, id)?.pending))) throw new model.EditError('busy');
            throw error;
        }
    });
}
export function duplicateParts(record, ids) {
    ids = linkedIds(record, ids);
    const content = contentFor(record, ids);
    const at = Math.max(...record.tracks.flatMap(track => track.clips).filter(clip => ids.includes(clip.id)).map(end));
    return edit(() => pasteContent(record, content, at));
}
export function copyTo(record, ids, at) {
    frameNumber(at);
    return edit(() => pasteContent(record, contentFor(record, ids), at));
}
export function moveParts(record, ids, offset) {
    check(offset && Number.isSafeInteger(offset.frames));
    ids = linkedIds(record, ids);
    if (offset.track != null) check(record.tracks.some(track => track.id === offset.track && track.clips.some(clip => ids.includes(clip.id))));
    return edit(() => model.moveTogether(record, ids, offset.frames));
}
