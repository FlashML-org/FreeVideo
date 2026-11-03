// Shared copy and placement rules; the model owns gap/suffix/rollback arithmetic.
import * as model from './editor_model.js';
import {isText, assertUnlocked} from './editor_text_model.js';
import {EditError, insertionAt, makeRoom} from './editor_model.js';

const end = clip => clip.start + clip.frames;
const busy = clip => ['queued', 'running'].includes(clip.pending);
const all = record => record.tracks.flatMap(track => track.clips);

export function pasteRoom(record, entries, at, placeAudio) {
    const video = entries.some(item => item.kind === 'video');
    for (const {clip} of entries) clip.start += at;
    // Text alone stays where it is pasted; landing on existing text refuses the whole paste (pasteContent).
    if (!entries.some(item => ['video', 'audio'].includes(item.kind))) return record;
    // Preserve an available audio lane before considering a displaced suffix.
    try { placeAudio(record, entries); } catch (error) { if (error.code !== 'overlap') throw error; }
    const anchor = video ? 'v1' : entries[0].track;
    const start = record.tracks.some(track => track.id === anchor) ? insertionAt(record, anchor, at) : at;
    for (const {clip} of entries) clip.start += start - at;
    const frames = Math.max(...entries.map(({clip}) => clip.start + clip.frames)) - start;
    const pictureRoom = makeRoom(record, start, frames, {tracks: video ? ['v1'] : []});
    try { placeAudio(pictureRoom, entries); return pictureRoom; } catch (error) {
        if (error.code !== 'overlap') throw error;
        // Crossing content is fixed. Later content can yield, but cannot force
        // a third audio lane or displace a crossing soundtrack.
        try { placeAudio({...record, tracks: record.tracks.map(track => ({...track,
            clips: track.clips.filter(clip => clip.start < start)}))}, entries); }
        catch (error) { if (error.code === 'overlap') throw new EditError('audio_full'); throw error; }
    }
    const tracks = entries.map(item => item.kind === 'video' ? 'v1' : item.track);
    const next = makeRoom(record, start, frames, {tracks});
    placeAudio(next, entries);
    return next;
}

export function copyContent(record, ids) {
    const wanted = model.linkedIds(record, ids), byId = new Map(all(record).map(clip => [clip.id, clip]));
    const eligible = clip => (clip?.source || isText(clip)) && !busy(clip);
    const clips = record.tracks.flatMap(track => track.clips.filter(clip => wanted.has(clip.id)
        && eligible(clip) && (!clip.link || eligible(byId.get(clip.link)))).map(clip => ({track: track.id, kind: track.kind, clip: structuredClone(clip)})));
    if (!clips.length) throw new model.EditError('not_generated');
    const start = Math.min(...clips.map(item => item.clip.start));
    for (const {clip} of clips) {
        clip.start -= start; clip.versions = []; clip.pending = clip.reason = clip.job = null;
        // A shot reference names one part; a copy keeps its description and version origin but not the reference.
        delete clip.target_frames; delete clip.ref;
    }
    return {clips};
}
// Two audio lanes are a two-colour interval graph. Fixed existing intervals
// constrain the colour; free components prefer their original lanes. This also
// handles a long copied interval whose shorter companion must change lanes.
export function placeAudio(record, entries) {
    const nodes = record.tracks.filter(track => track.kind === 'audio').flatMap(track => track.clips.map(clip =>
        ({clip, preferred: track.id === 'a1' ? 0 : 1, fixed: true, neighbors: []})));
    nodes.push(...entries.filter(item => item.kind === 'audio').map(item =>
        ({clip: item.clip, preferred: item.track === 'a1' ? 0 : 1, item, neighbors: []})));
    nodes.sort((a, b) => a.clip.start - b.clip.start);
    let active = [];
    for (const node of nodes) {
        active = active.filter(other => end(other.clip) > node.clip.start);
        if (active.length > 1) throw new model.EditError('overlap');
        for (const other of active) { node.neighbors.push(other); other.neighbors.push(node); }
        active.push(node);
    }
    for (const root of nodes) {
        if (root.colour !== undefined) continue;
        root.colour = 0;
        const component = [root]; let required = null;
        for (let i = 0; i < component.length; i++) {
            const node = component[i];
            if (node.fixed) {
                const flip = node.colour ^ node.preferred;
                if (required !== null && required !== flip) throw new model.EditError('overlap');
                required = flip;
            }
            for (const other of node.neighbors) if (other.colour === undefined) { other.colour = 1 - node.colour; component.push(other); }
        }
        const preference = component.reduce((sum, node) => sum + (node.colour === node.preferred ? 1 : -1), 0);
        const flip = required ?? (preference < 0 ? 1 : 0);
        for (const node of component) if (node.item) node.item.track = (node.colour ^ flip) ? 'a2' : 'a1';
    }
}
export function pasteContent(record, content, at) {
    if (!content?.clips.length) throw new model.EditError('clipboard_empty');
    if (!Number.isSafeInteger(at) || at < 0) throw new model.EditError('range');
    const entries = structuredClone(content.clips), ids = new Map(), parts = new Map();
    const next = model.serialize(pasteRoom(record, entries, at, placeAudio));
    for (const {clip} of entries) ids.set(clip.id, `c${++next.serial}`);
    for (const item of entries) {
        const clip = item.clip;
        clip.id = ids.get(clip.id); clip.link = ids.get(clip.link) ?? null;
        // A copy continues only what was copied with it.
        if (clip.follows !== undefined) { if (ids.has(clip.follows)) clip.follows = ids.get(clip.follows); else delete clip.follows; }
        if (clip.part) { if (!parts.has(clip.part)) parts.set(clip.part, `c${++next.serial}`); clip.part = parts.get(clip.part); }
        if (item.kind === 'video') item.track = 'v1';
        let track = next.tracks.find(track => track.id === item.track);
        if (!track) { track = {id: item.track, kind: item.kind, clips: []}; next.tracks.push(track); }
        assertUnlocked(next, track.clips.map(c => c.id));
        if (isText(clip) && track.clips.some(other => other.start < end(clip) && end(other) > clip.start)) throw new EditError('overlap');
        track.clips.push(clip);
    }
    next.tracks.sort((a,b) => ['v1','a1','a2','a3','a4','a5','a6','t1','s1'].indexOf(a.id) - ['v1','a1','a2','a3','a4','a5','a6','t1','s1'].indexOf(b.id));
    next.tracks.forEach(track => track.clips.sort((a, b) => a.start - b.start));
    return {record: model.parse(next), ids: [...ids.values()], end: Math.max(...entries.map(item => end(item.clip)))};
}

// Q3: a range not generated yet is copied as itself; it pastes back as a range with its description,
// landing like other content (after a part under the playhead; later content yields).
export function copyRange(selection, text) {
    return {range: {frames: selection.frames, tracks: [...selection.tracks], ...(selection.parts ? {parts: [...selection.parts]} : {}), text: text ?? ''}};
}
export function pasteRange(record, copied, at) {
    if (!Number.isSafeInteger(at) || at < 0) throw new EditError('range');
    const present = copied.tracks.filter(id => record.tracks.some(track => track.id === id));
    const tracks = present.length ? present : ['v1', 'a1'], anchor = tracks.includes('v1') ? 'v1' : tracks[0];
    const start = insertionAt(record, anchor, at);
    return {record: makeRoom(record, start, copied.frames, {tracks}),
        range: {kind: 'range', start, frames: copied.frames, tracks, ...(copied.parts ? {parts: [...copied.parts]} : {})}};
}

export function playheadTargets(record, ids, frame) {
    const selected = new Set([...model.linkedIds(record, ids)]), crosses = clip => clip.start < frame && end(clip) > frame;
    let candidates = all(record).filter(clip => selected.has(clip.id) && crosses(clip));
    if (!candidates.length) {
        const rows = record.tracks.filter(track => ['video', 'audio'].includes(track.kind));
        const hit = rows.find(track => track.clips.some(crosses))?.clips.find(crosses);
        candidates = hit ? [hit] : [];
    }
    const handled = new Set();
    return candidates.filter(clip => {
        if (handled.has(clip.id)) return false;
        model.pair(record, clip.id).forEach(member => handled.add(member.id)); return true;
    }).map(clip => clip.id);
}

export function removableGaps(record, value) {
    const track = value?.kind === 'gap' ? record.tracks.find(track => track.id === value.track)
        : record.tracks.find(track => track.kind === 'video' && track.clips.length)
            || record.tracks.find(track => track.kind === 'audio' && track.clips.length);
    if (!track) return [];
    return model.gaps(record, track.id).filter(gap => track.clips.some(clip => clip.start >= end(gap))
        && (value?.kind !== 'gap' || gap.start === value.start)).map(gap => ({...gap, track: track.id}));
}
