// A new line said in a voice of the edit, planned on an empty stretch of the recording track (or of the separated
// voice track) and laid in place when the voice job has made it. Pure edits on the project record; the generation bar
// decides when to call them. The planned line is a clip with no file yet, as a planned piece of music is.
import * as model from './editor_model.js';
import {FPS} from './editor_model.js';

export const RECORDING_TRACK = 'a3';
// The voice job makes lines between half a second and a minute long.
export const MIN_LINE_FRAMES = Math.ceil(.5 * FPS);
export const MAX_LINE_FRAMES = 60 * FPS;
// The longest line the job takes.
export const MAX_LINE_TEXT = 2000;
const fail = code => { throw new model.EditError(code); };
const end = clip => clip.start + clip.frames;
const ORDER = ['v1', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6'];
// The separated voice of a clip, when the edit has one.
const dialogue = record => record.tracks.find(track => track.kind === 'audio' && track.stem === 'dialogue') || null;
// Where a new line goes: the separated voice track once the sound is separated, else the recording track.
export const narrationTrack = record => dialogue(record)?.id || RECORDING_TRACK;
// Where lines are said: the recording track and the separated voice track.
const lanes = record => record.tracks.filter(track => track.kind === 'audio' && (track.id === RECORDING_TRACK || track.stem === 'dialogue'));
const laneOf = (record, id) => lanes(record).find(track => track.clips.some(clip => clip.id === id)) || null;

// A planned or running line: no file yet, on a track where lines are said.
export const isNarrationPart = (record, clip) => !!clip && !clip.source && clip.pending !== null && !!laneOf(record, clip.id);

// What a selection asks for: an empty stretch drawn or clicked on a track where lines are said, or a planned line
// there (made again in place). A stretch over sound that is there already is the redubbing of that sound, not this.
export function narrationTarget(record, selection) {
    if (!selection) return null;
    const on = selection.kind === 'range' && selection.tracks?.length === 1 ? selection.tracks[0] : selection.kind === 'gap' ? selection.track : null;
    if (on) {
        const track = lanes(record).find(row => row.id === on);
        if (!track || track.locked) return null;
        const frames = Math.min(selection.frames, MAX_LINE_FRAMES);
        if (frames < MIN_LINE_FRAMES || track.clips.some(clip => clip.start < selection.start + frames && end(clip) > selection.start)) return null;
        return {track: track.id, start: selection.start, frames};
    }
    if (selection.kind !== 'clip' || selection.ids.length !== 1) return null;
    const track = laneOf(record, selection.ids[0]), clip = track?.clips.find(item => item.id === selection.ids[0]);
    if (!clip || !isNarrationPart(record, clip)) return null;
    return {track: track.id, start: clip.start, frames: clip.frames, id: clip.id, clip};
}

// The track where the menu's entry puts a line, added when missing, and the empty stretch there from `frame`: up to
// the next clip, at most `frames` long. Null where that track has a clip at `frame` or is locked.
export function narrationStretch(record, frame, frames = 5 * FPS) {
    if (!Number.isSafeInteger(frame) || frame < 0) fail('range');
    const id = narrationTrack(record), track = record.tracks.find(row => row.id === id);
    if (track?.locked) return null;
    const clips = track?.clips || [];
    if (clips.some(clip => clip.start <= frame && end(clip) > frame)) return null;
    const next = clips.filter(clip => clip.start > frame).reduce((first, clip) => Math.min(first, clip.start), Infinity);
    const length = Math.min(frames, MAX_LINE_FRAMES, next - frame);
    if (length < MIN_LINE_FRAMES) return null;
    return {track: id, start: frame, frames: length};
}
// The edit with the recording track there.
export function withRecordingTrack(record) {
    if (record.tracks.some(track => track.id === RECORDING_TRACK)) return model.parse(record);
    const next = model.serialize(record);
    next.tracks.push({id: RECORDING_TRACK, kind: 'audio', clips: []});
    next.tracks.sort((a, b) => ORDER.indexOf(a.id) - ORDER.indexOf(b.id));
    return model.parse(next);
}

// A line on the stretch from start for frames, which must be empty there. The recording track is added when missing.
export function planNarration(record, {track: id = RECORDING_TRACK, start, frames, text = ''}) {
    if (!Number.isSafeInteger(start) || start < 0 || !Number.isSafeInteger(frames) || frames < MIN_LINE_FRAMES) fail('range');
    if (frames > MAX_LINE_FRAMES) fail('too_long');
    if (typeof text !== 'string' || !text.trim() || text.length > MAX_LINE_TEXT) fail('text');
    const next = model.serialize(id === RECORDING_TRACK ? withRecordingTrack(record) : record);
    const track = lanes(next).find(row => row.id === id) || fail('track');
    if (track.locked) fail('locked');
    if (track.clips.some(clip => clip.start < start + frames && end(clip) > start)) fail('overlap');
    const clip = `c${++next.serial}`;
    track.clips.push({id: clip, start, frames, in: 0, length: frames, source: null, link: null, text: text.trim(), part: null, pending: 'draft',
        reason: null, job: null, extend: false, versions: [], volume: 1});
    track.clips.sort((a, b) => a.start - b.start);
    return {record: model.parse(next), id: clip};
}
// Said again in place: a failed or stopped line becomes a planned one over the same stretch, with the words given.
export function remakeNarration(record, id, text) {
    if (typeof text !== 'string' || !text.trim() || text.length > MAX_LINE_TEXT) fail('text');
    return change(record, id, undefined, clip => {
        if (['queued', 'running'].includes(clip.pending)) fail('busy');
        Object.assign(clip, {text: text.trim(), pending: 'draft', reason: null, job: null});
    }) ?? fail('missing');
}

function change(record, id, job, edit) {
    const next = model.serialize(record), clip = laneOf(next, id)?.clips.find(clip => clip.id === id);
    if (!clip || clip.source || clip.pending === null) return null;
    if (job !== undefined && clip.job !== job) return null;
    edit(clip, laneOf(next, id));
    return model.parse(next);
}
// The job was accepted: the line waits for it.
export function assignNarrationJob(record, id, job) {
    if (typeof job !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(job)) fail('job');
    return change(record, id, undefined, clip => { clip.job = job; clip.pending = 'queued'; clip.reason = null; }) ?? fail('missing');
}
// What the job says while it runs; a line already finished, removed or given to another job stays as it is.
export function narrationProgress(record, id, job, state) {
    if (!['queued', 'running'].includes(state)) return model.parse(record);
    return change(record, id, job, clip => { clip.pending = state; }) ?? model.parse(record);
}
// Frames of a new sound `seconds` long, as the timeline counts them.
export const linesFrames = seconds => Math.max(1, Math.round(seconds * FPS));
// Whether a line `frames` long fits where the planned one is: it grows to its whole length unless it would then
// cover the next clip on its track. {fits, over} with `over` the frames past the planned stretch.
export function narrationFit(record, id, frames) {
    const track = laneOf(record, id), clip = track?.clips.find(item => item.id === id);
    if (!clip) return {fits: false, over: 0};
    const over = Math.max(0, frames - clip.frames);
    const next = track.clips.find(item => item.start >= end(clip) && item.id !== id);
    return {fits: !over || !next || clip.start + frames <= next.start, over};
}
// The file is made: it goes into the planned stretch, growing to the line's whole length when it is longer.
export function finishNarration(record, id, job, {source, frames}) {
    const [kind, path] = typeof source === 'string' ? [source.slice(0, source.indexOf(':')), source.slice(source.indexOf(':') + 1)] : [];
    if (kind !== 'generated' || !path || !Number.isSafeInteger(frames) || frames < 1) fail('source');
    if (!narrationFit(record, id, frames).fits) fail('overlap');
    return change(record, id, job, clip => {
        Object.assign(clip, {source: {kind, id: path}, in: 0, length: frames, frames, pending: null, reason: null, job: null});
    }) ?? model.parse(record);
}
// Taken out again: a line too long for its stretch that would cover the next clip.
export function dropNarration(record, id) {
    const next = model.serialize(record), track = laneOf(next, id);
    const clip = track?.clips.find(item => item.id === id);
    if (!clip || clip.source) return model.parse(record);
    track.clips = track.clips.filter(item => item.id !== id);
    return model.parse(next);
}
// Failed or stopped: the line stays where it was drawn, with its reason, ready to try again.
export function failNarration(record, id, job, reason = 'voice_failed') {
    const state = reason === 'cancelled' ? 'stopped' : 'failed';
    const code = typeof reason === 'string' && /^[a-z_]{1,80}$/.test(reason) ? reason : 'voice_failed';
    return change(record, id, job, clip => { clip.pending = state; clip.reason = state === 'failed' ? code : null; }) ?? model.parse(record);
}
// The lines still waiting for a job after the page opens again, to watch or to mark failed.
export const narrationJobs = record => lanes(record).flatMap(track => track.clips).filter(clip => !clip.source && clip.job && ['queued', 'running'].includes(clip.pending))
    .map(clip => ({id: clip.id, job: clip.job}));

// The voices a line can be said in. `speakers`: the recognised lines of the edit's speech, each with its clip and its
// speaker number; `clips`: the edit's sound clips with a file, each {id, name} in timeline order.
// Speakers are numbered by first appearance; each one's voice is its longest run of lines, at most 20 s.
export function speakerVoices(found) {
    const order = [], lines = new Map();
    for (const line of [...found].sort((a, b) => a.at - b.at)) {
        const key = `${line.clip}:${line.speaker}`;
        if (!lines.has(key)) { lines.set(key, []); order.push(key); }
        lines.get(key).push(line);
    }
    return order.map((key, index) => {
        const own = lines.get(key), longest = own.reduce((best, line) => line.end - line.start > best.end - best.start ? line : best);
        // The longest line and the lines of the same speaker right around it, while together they last at most 20 s.
        const same = own.filter(line => line.clip === longest.clip).sort((a, b) => a.start - b.start);
        let from = same.indexOf(longest), to = from;
        const span = (a, b) => same[b].end - same[a].start;
        for (let grown = true; grown;) {
            grown = false;
            if (from > 0 && span(from - 1, to) <= 20) { from--; grown = true; }
            if (to < same.length - 1 && span(from, to + 1) <= 20) { to++; grown = true; }
        }
        const run = same.slice(from, to + 1), cjk = run.some(line => /[぀-ヿ㐀-鿿가-힯]/.test(line.text));
        return {key: `speaker:${key}`, kind: 'speaker', number: index + 1, at: own[0].at,
            reference: {clip_id: longest.clip, start: run[0].start, end: run.at(-1).end, text: run.map(line => line.text.trim()).join(cjk ? '' : ' ')},
            listen: {clip: longest.clip, start: longest.start, end: longest.end}};
    });
}
// The speaker whose line is nearest the stretch; the only one when there is one.
export function nearestSpeaker(voices, found, at) {
    const speakers = voices.filter(voice => voice.kind === 'speaker');
    if (speakers.length < 2) return speakers[0] || null;
    let best = null, distance = Infinity;
    for (const line of found) {
        const away = Math.max(0, line.at - at, at - line.atEnd);
        if (away < distance) { distance = away; best = line; }
    }
    return best ? speakers.find(voice => voice.key === `speaker:${best.clip}:${best.speaker}`) || speakers[0] : speakers[0];
}
