// A piece of music planned on a stretch of the second sound track, made by the music job and laid back in place.
// Pure edits on the project record; the generation bar decides when to call them.
import * as model from './editor_model.js';
import {FPS} from './editor_model.js';

export const MUSIC_TRACK = 'a2';
// The longest piece the music model makes in one go.
export const MAX_MUSIC_FRAMES = 600 * FPS;
// The longest description the music job takes.
export const MAX_MUSIC_TEXT = 512;
// The presets the server knows; the user's own words go instead when given.
export const MUSIC_STYLES = ['cinematic', 'pop', 'electronic', 'piano', 'folk', 'ambient'];
export const MUSIC_MOODS = ['calm', 'upbeat', 'tense', 'warm', 'sad'];
const fail = code => { throw new model.EditError(code); };
const end = clip => clip.start + clip.frames;
const lane = record => record.tracks.find(track => track.id === MUSIC_TRACK) || null;

// The music track's planned or running pieces: no file yet, waiting for (or made by) a music job.
export const isMusicPart = (record, clip) => !!clip && !clip.source && clip.pending !== null
    && !!lane(record)?.clips.some(other => other.id === clip.id);
// A finished piece the music job made: its file sits in the music folder.
export const isGeneratedMusic = clip => clip?.source?.kind === 'generated' && /^freevideo-music\/[a-z]+(-[a-z]+)?-[0-9a-f]{32}\.mp4$/.test(clip.source.id);

export const DEFAULT_PRESET = {style: 'cinematic', mood: 'calm'};
const preset = value => MUSIC_STYLES.includes(value?.style) && MUSIC_MOODS.includes(value?.mood) ? {style: value.style, mood: value.mood} : null;
// The style and mood last chosen in this edit; the default pair before any.
export const lastPreset = record => preset(record.settings?.music) || {...DEFAULT_PRESET};
// Remember the pair for the next piece in this edit.
export function rememberPreset(record, value) {
    const chosen = preset(value) || fail('preset');
    if (JSON.stringify(record.settings?.music) === JSON.stringify(chosen)) return model.parse(record);
    const next = model.serialize(record);
    next.settings = {...(next.settings || {}), music: chosen};
    return model.parse(next);
}
// What a selection on the music track asks for: an empty stretch drawn or clicked there, or one piece of music
// there (made again in place). Null for anything else.
export function musicTarget(record, selection) {
    const track = lane(record);
    if (!track || !selection) return null;
    if (selection.kind === 'range' && selection.tracks?.length === 1 && selection.tracks[0] === MUSIC_TRACK
        || selection.kind === 'gap' && selection.track === MUSIC_TRACK) {
        const frames = Math.min(selection.frames, MAX_MUSIC_FRAMES);
        if (track.clips.some(clip => clip.start < selection.start + frames && end(clip) > selection.start)) return null;
        return {start: selection.start, frames};
    }
    if (selection.kind !== 'clip' || selection.ids.length !== 1) return null;
    const clip = track.clips.find(clip => clip.id === selection.ids[0]);
    if (!clip || !(isMusicPart(record, clip) || isGeneratedMusic(clip))) return null;
    return {start: clip.start, frames: clip.frames, id: clip.id, clip};
}

// A drag on the music track: the stretch from start for frames, which must be empty there. The track is added when missing.
export function planMusic(record, {start, frames, text = '', style, mood}) {
    if (!Number.isSafeInteger(start) || start < 0 || !Number.isSafeInteger(frames) || frames < 1) fail('range');
    if (frames > MAX_MUSIC_FRAMES) fail('too_long');
    if (typeof text !== 'string') fail('text');
    const chosen = style === undefined && mood === undefined ? {...DEFAULT_PRESET} : preset({style, mood}) || fail('preset');
    const next = model.serialize(lane(record) ? record : model.addAudioTrack(record)), track = next.tracks.find(track => track.id === MUSIC_TRACK);
    if (track.locked) fail('locked');
    if (track.clips.some(clip => clip.start < start + frames && end(clip) > start)) fail('overlap');
    const id = `c${++next.serial}`;
    track.clips.push({id, start, frames, in: 0, length: frames, source: null, link: null, text, part: null, pending: 'draft',
        reason: null, job: null, extend: false, versions: [], volume: 1, music: chosen, duck: true});
    track.clips.sort((a, b) => a.start - b.start);
    return {record: model.parse(next), id};
}
// Made again in place: a finished, failed or stopped piece becomes a planned one over the same stretch, with the
// words and preset given; its volume, fades and lowering stay. One undo brings the previous piece back.
export function remakeMusic(record, id, {text = '', style, mood}) {
    if (typeof text !== 'string') fail('text');
    const chosen = preset({style, mood}) || fail('preset');
    const next = model.serialize(record), clip = next.tracks.find(track => track.id === MUSIC_TRACK)?.clips.find(clip => clip.id === id);
    if (!clip || !(isGeneratedMusic(clip) || !clip.source && clip.pending !== null)) fail('missing');
    if (next.tracks.find(track => track.id === MUSIC_TRACK).locked) fail('locked');
    if (['queued', 'running'].includes(clip.pending)) fail('busy');
    Object.assign(clip, {source: null, in: 0, length: clip.frames, text, pending: 'draft', reason: null, job: null, versions: [], music: chosen});
    delete clip.splice;
    return model.parse(next);
}

// What the music job is asked for: the piece's length, its words when there are any, and its preset.
export function musicRequest(record, id, {seed} = {}) {
    const clip = model.find(record, id);
    if (!isMusicPart(record, clip)) fail('missing');
    const body = {frames: clip.frames, ...(clip.music || DEFAULT_PRESET)};
    if (clip.text.trim()) body.text = clip.text.trim();
    if (Number.isSafeInteger(seed)) body.seed = seed;
    return body;
}

function change(record, id, job, edit) {
    const next = model.serialize(record), clip = next.tracks.find(track => track.id === MUSIC_TRACK)?.clips.find(clip => clip.id === id);
    if (!clip || clip.source || clip.pending === null) return null;
    if (job !== undefined && clip.job !== job) return null;
    edit(clip);
    return model.parse(next);
}
// The job was accepted: the piece waits for it.
export function assignMusicJob(record, id, job) {
    if (typeof job !== 'string' || !/^m-[0-9a-f]{32}$/.test(job)) fail('job');
    return change(record, id, undefined, clip => { clip.job = job; clip.pending = 'queued'; clip.reason = null; }) ?? fail('missing');
}
// What the job says while it runs; a piece already finished, removed or given to another job stays as it is.
export function musicProgress(record, id, job, state) {
    if (!['queued', 'running'].includes(state)) return model.parse(record);
    return change(record, id, job, clip => { clip.pending = state; }) ?? model.parse(record);
}
// The file is made: it goes into the planned stretch. A shorter file shortens the piece; nothing else moves.
export function finishMusic(record, id, job, {source, frames}) {
    const [kind, path] = typeof source === 'string' ? [source.slice(0, source.indexOf(':')), source.slice(source.indexOf(':') + 1)] : [];
    if (kind !== 'generated' || !path || !Number.isSafeInteger(frames) || frames < 1) fail('source');
    return change(record, id, job, clip => {
        Object.assign(clip, {source: {kind, id: path}, in: 0, length: frames, frames: Math.min(clip.frames, frames), pending: null, reason: null, job: null});
    }) ?? model.parse(record);
}
// Failed or stopped: the piece stays where it was drawn, with its reason, ready to try again.
export function failMusic(record, id, job, reason = 'music_failed') {
    const state = reason === 'cancelled' ? 'stopped' : 'failed';
    const code = typeof reason === 'string' && /^[a-z_]{1,80}$/.test(reason) ? reason : 'music_failed';
    return change(record, id, job, clip => { clip.pending = state; clip.reason = state === 'failed' ? code : null; }) ?? model.parse(record);
}
// Try again: back to planned, with the same stretch and description.
export function retryMusic(record, id) {
    return change(record, id, undefined, clip => {
        if (['queued', 'running'].includes(clip.pending)) fail('busy');
        clip.pending = 'draft'; clip.reason = null; clip.job = null;
    }) ?? fail('missing');
}
// The pieces still waiting for a job after the page opens again, to watch or to mark failed.
export const musicJobs = record => (lane(record)?.clips || []).filter(clip => !clip.source && clip.job && ['queued', 'running'].includes(clip.pending))
    .map(clip => ({id: clip.id, job: clip.job}));
