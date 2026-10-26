import {hasTextData, readTextProject, isText, trimText, assertUnlocked, changeText} from './editor_text_model.js';
// Immutable editor records and frame arithmetic; usable without a browser.
import {pictureFields, pictureEditor} from './editor_picture_fields.js';
import {markerFields} from './editor_transport_model.js';
import {audioClipFields, audioTrackFields, normalizeAudioEdits, splitAudioFields} from './editor_audio_model.js';
import {speedRange, parseSpeedData, validateSpeedPair, sourceFrame, speedEdited, trimSpeed, splitSpeed, connectSpeedOperations} from './editor_speed_model.js';
import {CHAIN_RULES} from './chain_rules.js';
export const VERSION = 1;
export const FPS = 24;
// The continuing-part limits are chain_plan's (web/chain_rules.js): a part that continues another is at least
// followSeconds long; a video with a continuing part shorter than fullSeconds has at most shortMaxParts parts unless
// every part sees the first frame, any other at most maxParts. A new continuation starts at continueSeconds, or at
// shortContinueSeconds where a full part is too much for the device.
export const RULES = Object.freeze({maxSeconds: 15, singleSeconds: 30, minSeconds: 2, tailSeconds: 5,
    followSeconds: CHAIN_RULES.min_part_seconds, fullSeconds: CHAIN_RULES.full_part_seconds,
    continueSeconds: CHAIN_RULES.continue_seconds, shortContinueSeconds: CHAIN_RULES.short_continue_seconds,
    step: 17, first: 5, overlap: 22,
    maxTracks: 3, maxClips: 500, maxText: 8000, maxParts: CHAIN_RULES.max_parts, shortMaxParts: CHAIN_RULES.short_max_parts});
// Slice and bound user text by Unicode code points, never UTF-16 halves.
export const truncateText = (text, limit) => Array.from(text).slice(0, limit).join('');
export const copyName = (name, suffix) => truncateText(name, RULES.maxText - Array.from(suffix).length) + suffix;
const validText = text => typeof text === 'string' && Array.from(text).length <= RULES.maxText
    && !Array.from(text).some(c => c.length === 1 && c.charCodeAt(0) >= 0xd800 && c.charCodeAt(0) <= 0xdfff);
export class EditError extends Error {
    constructor(code, message = code) { super(message); this.name = 'EditError'; this.code = code; }
}
const reject = (code, message) => { throw new EditError(code, message); };
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const whole = (value, low = 0, high = 1e7) => Number.isSafeInteger(value) && value >= low && value <= high;
const check = (yes, code = 'invalid', message = code) => { if (!yes) reject(code, message); };
const ID = /^c[1-9]\d{0,8}$/;
// Who last wrote a block's description (its note on the timeline), and an outside reference (a shot id).
const TEXT_BY = ['user', 'agent'], REF = /^[A-Za-z0-9_-]{1,128}$/;
// A short line about a block for whoever looks at the timeline; regenerating never reads it.
// One line: no control or line-break character anywhere, nothing at its ends that either side would strip.
const NOTE_BREAK = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/;
const validNote = text => validText(text) && text === text.trim() && !NOTE_BREAK.test(text) && Array.from(text).length >= 1 && Array.from(text).length <= 120;
const WORK = /^\d{4}-\d{2}-\d{2}\/[0-9a-f]{32}$/;
const PENDING = [null, 'draft', 'queued', 'running', 'failed', 'stopped'];
const key = source => `${source.kind}:${source.id}`;
const all = record => record.tracks.flatMap(track => track.clips);
const trackOf = (record, id) => record.tracks.find(track => track.clips.some(clip => clip.id === id));
const trackBy = (record, id) => { const track = record.tracks.find(track => track.id === id); check(track, 'track'); return track; };
const end = clip => clip.start + clip.frames;
const overlaps = (clip, start, frames) => clip.start < start + frames && end(clip) > start;
const busy = clips => check(!clips.some(clip => ['queued', 'running'].includes(clip.pending)), 'busy');
function frozen(value) {
    if (value && typeof value === 'object') { Object.values(value).forEach(frozen); Object.freeze(value); }
    return value;
}
function jsonData(value, depth = 0) {
    check(depth <= 32, 'invalid', 'settings too deep');
    if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
    if (typeof value === 'number') { check(Number.isFinite(value)); return value; }
    check(Array.isArray(value) || (object(value) && [null, Object.prototype].includes(Object.getPrototypeOf(value))));
    if (Array.isArray(value)) return value.map(item => jsonData(item, depth + 1));
    return Object.fromEntries(Object.entries(value).map(([name, item]) => [name, jsonData(item, depth + 1)]));
}
function sourceData(value) {
    check(object(value) && ['work', 'import', 'generated'].includes(value.kind), 'source');
    check(typeof value.id === 'string' && value.id.length > 0 && value.id.length <= 1024, 'source');
    if (value.kind === 'work') check(WORK.test(value.id), 'source');
    else if (value.kind === 'import') check(/^[A-Za-z0-9_-]{1,80}$/.test(value.id), 'source');
    else check(!value.id.includes(':') && value.id.toLowerCase().endsWith('.mp4') && !value.id.split('/').includes('.editor'), 'source');
    if (value.kind !== 'work') check(!/[\\\x00-\x1f]/.test(value.id) && !value.id.startsWith('/') && !/^[A-Za-z]:/.test(value.id)
        && value.id.split('/').every(part => part && part !== '.' && part !== '..'), 'source');
    return {kind: value.kind, id: value.id};
}
function pictureFit(value) {
    check(value === undefined || value === 'cover' || value === 'contain', 'fit');
    return value ?? 'cover';
}
// Follow only the transition from no real video to a first real video.
function followFirstSize(record, width, height) {
    if (record.size_mode !== 'fixed' && !trackBy(record, 'v1').clips.some(clip => clip.source)
        && whole(width, 2, 8193) && whole(height, 2, 8193)) {
        record.width = Math.floor(width / 2) * 2; record.height = Math.floor(height / 2) * 2;
    }
}
// The order a version was made in (1, 2, 3 …) and the description it was made from.
function madeFields(value, result) {
    if (value.take !== undefined) { check(whole(value.take, 1, 1e6), 'versions'); result.take = value.take; }
    if (value.made_text !== undefined) { check(validText(value.made_text), 'text'); result.made_text = value.made_text; }
}
function versionData(value, kind) {
    const source = sourceData(value);
    check(whole(value.in) && whole(value.length, value.in + 1), 'range');
    const result = {...source, in: value.in, length: value.length};
    // Old splice offsets must survive a version switch as well as a save.
    if (value.splice !== undefined) result.splice = spliceData(value.splice);
    madeFields(value, result);
    if (kind === 'video') { Object.assign(result, pictureFields(value)); result.fit = pictureFit(value.fit); }
    else check(value.fit === undefined, 'fit');
    return result;
}
function spliceData(value) {
    check(object(value) && whole(value.frames) && whole(value.audio_samples, 0, Number.MAX_SAFE_INTEGER), 'range');
    return {frames: value.frames, audio_samples: value.audio_samples};
}
export function emptyProject({width = 1344, height = 768, settings = null, name = ''} = {}) {
    return parse({version: VERSION, id: null, revision: 0, name, fps: FPS, width, height,
        settings, serial: 0, tracks: [{id: 'v1', kind: 'video', clips: []}, {id: 'a1', kind: 'audio', clips: []}]});
}
// One project-wide generation setting; a false flag is stored as no key at all.
export function setFlag(record, key, value) {
    check(typeof key === 'string' && /^[a-z][a-z0-9_]*$/.test(key) && typeof value === 'boolean', 'settings');
    const next = serialize(record), settings = {...(next.settings || {})};
    if (value) settings[key] = true; else delete settings[key];
    next.settings = Object.keys(settings).length ? settings : null;
    if (JSON.stringify(next.settings) !== JSON.stringify(record.settings)) next.keep_empty = true;
    return parse(next);
}
export function addAudioTrack(record) {
    const next = serialize(record);
    check(!next.tracks.some(track => track.id === 'a2'), 'track');
    next.tracks.push({id: 'a2', kind: 'audio', clips: []});
    return parse(next);
}
export function removeAudioTrack(record) {
    const next = serialize(record), track = trackBy(next, 'a2');
    busy(track.clips);
    const removed = new Set(track.clips.map(clip => clip.id));
    next.tracks = next.tracks.filter(track => track.id !== 'a2');
    for (const clip of all(next)) if (removed.has(clip.link)) clip.link = null;
    return parse(next);
}
export function parse(value) {
    check(object(value), 'invalid', 'not a project');
    if (hasTextData(value)) return readTextProject(value, parse);
    check(value.version === VERSION, 'version');
    check(value.id === null || (typeof value.id === 'string' && /^e-[0-9a-f]{32}$/.test(value.id)), 'id');
    check((value.id !== null || value.revision === 0) && whole(value.revision, 0, Number.MAX_SAFE_INTEGER) && whole(value.serial, 0, 999999999), 'invalid');
    check(validText(value.name), 'text');
    check(value.fps === FPS, 'fps');
    check(whole(value.width, 2, 8192) && whole(value.height, 2, 8192)
        && value.width % 2 === 0 && value.height % 2 === 0, 'size');
    check(value.settings === null || object(value.settings), 'settings');
    const settings = jsonData(value.settings);
    check(JSON.stringify(settings).length <= 1024 * 1024, 'settings');
    check(Array.isArray(value.tracks) && value.tracks.length >= 2 && value.tracks.length <= RULES.maxTracks, 'track');
    const ids = new Set();
    const tracks = value.tracks.map((raw, index) => {
        const id = ['v1', 'a1', 'a2'][index], kind = index === 0 ? 'video' : 'audio';
        check(object(raw) && raw.id === id && raw.kind === kind, 'track');
        check(Array.isArray(raw.clips) && raw.clips.length <= RULES.maxClips, 'track');
        let boundary = 0;
        const clips = raw.clips.map(item => {
            check(object(item) && typeof item.id === 'string' && ID.test(item.id)
                && !ids.has(item.id) && Number(item.id.slice(1)) <= value.serial, 'id');
            ids.add(item.id);
            check(item.text_kind === undefined && item.text_style === undefined, 'text');
            check(whole(item.start) && whole(item.frames, 1) && whole(item.in)
                && speedRange(item) && whole(item.start + item.frames), 'range');
            check(item.start >= boundary, 'overlap'); boundary = end(item);
            const source = item.source === null ? null : sourceData(item.source);
            const pending = item.pending ?? null;
            check(PENDING.includes(pending) && (source !== null || pending !== null), 'pending');
            check(source !== null || (item.in === 0 && item.length === item.frames), 'range');
            const link = item.link ?? null, part = item.part ?? null, job = item.job ?? null, reason = item.reason ?? null;
            check(link === null || (typeof link === 'string' && ID.test(link) && link !== item.id), 'link');
            check(part === null || (typeof part === 'string' && ID.test(part)), 'part');
            check(job === null || (typeof job === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(job)), 'job');
            check(reason === null || (typeof reason === 'string' && /^[a-z_]{1,80}$/.test(reason)), 'reason');
            check(pending === 'failed' || reason === null, 'reason');
            const text = item.text ?? '';
            check(validText(text), 'text');
            check(typeof item.extend === 'boolean', 'extend');
            check(Array.isArray(item.versions) && item.versions.length <= 50, 'versions');
            const versions = item.versions.map(value => versionData(value, kind)), versionKeys = versions.map(key);
            check(new Set(versionKeys).size === versions.length, 'versions');
            const clip = {id: item.id, start: item.start, frames: item.frames, in: item.in, length: item.length,
                source, link, text, part, pending, reason, job, extend: item.extend || (source?.kind === 'import' && item.length >= TAIL_FRAMES), versions};
            if (kind === 'video') { Object.assign(clip, pictureFields(item)); clip.fit = pictureFit(item.fit); }
            else check(item.fit === undefined, 'fit');
            if (kind === 'audio') { check(typeof item.volume === 'number' && Number.isFinite(item.volume) && item.volume >= 0 && item.volume <= 2, 'volume'); clip.volume = item.volume; }
            else check(item.volume === undefined, 'volume');
            if (item.splice !== undefined) { check(source !== null, 'range'); clip.splice = spliceData(item.splice); }
            // Persist the user's targets so prepare is idempotent after reload.
            if (item.target_frames !== undefined) { check(whole(item.target_frames, 1), 'range'); clip.target_frames = item.target_frames; }
            // Who last wrote the description, and an outside reference (a shot id) for callers.
            if (item.text_by !== undefined) { check(TEXT_BY.includes(item.text_by), 'text'); clip.text_by = item.text_by; }
            if (item.ref !== undefined) { check(typeof item.ref === 'string' && REF.test(item.ref), 'ref'); clip.ref = item.ref; }
            if (item.note !== undefined) { check(validNote(item.note), 'text'); clip.note = item.note; }
            // The part this one was pulled out of, or the part before it in the same range.
            if (item.follows !== undefined) { check(kind === 'video' && typeof item.follows === 'string' && ID.test(item.follows) && item.follows !== item.id, 'follows'); clip.follows = item.follows; }
            check(source !== null || (item.take === undefined && item.made_text === undefined), 'versions'); madeFields(item, clip);
            Object.assign(clip, audioClipFields(item, kind));
            Object.assign(clip, parseSpeedData(item, kind));
            return clip;
        });
        return {id, kind, clips, ...audioTrackFields(raw)};
    });
    const byId = new Map(tracks.flatMap(track => track.clips.map(clip => [clip.id, {clip, track}])));
    for (const {clip, track} of byId.values()) if (clip.link) {
        const mate = byId.get(clip.link);
        check(mate && mate.clip.link === clip.id && mate.track.kind !== track.kind, 'link');
        check(mate.clip.start === clip.start && mate.clip.frames === clip.frames && mate.clip.in === clip.in, 'link');
        validateSpeedPair(clip, mate.clip);
    }
    const result = {version: VERSION, id: value.id, revision: value.revision, name: value.name, fps: FPS,
        width: value.width, height: value.height, settings, serial: value.serial, tracks};
    check(value.size_mode === undefined || value.size_mode === 'first' || value.size_mode === 'fixed', 'size_mode');
    result.size_mode = value.size_mode ?? 'first';
    check(value.keep_empty === undefined || typeof value.keep_empty === 'boolean', 'invalid');
    if (value.keep_empty) result.keep_empty = true;
    Object.assign(result, markerFields(value));
    if (value.replacements !== undefined) {
        check(Array.isArray(value.replacements) && value.replacements.length > 0 && value.replacements.length <= RULES.maxClips, 'invalid');
        const parts = new Set();
        result.replacements = value.replacements.map(raw => {
            check(object(raw) && ID.test(raw.part) && !parts.has(raw.part) && typeof raw.rolledBack === 'boolean', 'invalid'); parts.add(raw.part);
            const read = rows => {
                check(Array.isArray(rows) && rows.length === 2 && rows.every(t => object(t) && Array.isArray(t.clips) && t.clips.length), 'invalid');
                return parse({...result, tracks: rows, replacements: undefined}).tracks;
            };
            const originals = read(raw.originals), clips = read(raw.clips);
            check(originals.every(t => t.clips.every(c => c.source && c.link && ['work', 'generated'].includes(c.source.kind))), 'invalid');
            check(clips.every(t => t.clips.every(c => c.part === raw.part && c.link)), 'invalid');
            const bounds = rows => rows.map(t => [t.clips[0].start, end(t.clips.at(-1))]);
            check(JSON.stringify(bounds(originals)) === JSON.stringify(bounds(clips)), 'invalid');
            return {part: raw.part, originals, clips, rolledBack: raw.rolledBack};
        });
    }
    return frozen(result);
}
export function serialize(record) {
    const value = JSON.parse(JSON.stringify(parse(record)));
    if (value.size_mode === 'first') delete value.size_mode;
    for (const tracks of [value.tracks, ...(value.replacements || []).flatMap(tx => [tx.originals, tx.clips])])
        for (const clip of tracks[0].clips) for (const item of [clip, ...clip.versions])
            if (item.fit === 'cover') delete item.fit;
    return value;
}
export const duration = record => Math.max(0, ...all(record).map(end));
// Pixels per second when an edit opens: what is there plus ten seconds of room, between twenty and ninety seconds wide.
export const openingZoom = (width, record) => {
    const frames = duration(record), seconds = frames ? Math.min(90, Math.max(20, frames / FPS + 10)) : 90;
    return width / seconds;
};
// An export runs as long as the picture and sound; text after them is not exported.
export const exportFrames = record => Math.max(0, ...record.tracks.filter(track => ['video', 'audio'].includes(track.kind)).flatMap(track => track.clips).map(end));
export const find = (record, id) => all(record).find(clip => clip.id === id) ?? null;
// A failed replacement displays its original timeline while retaining receipts
// for every block of the same resumable attempt, including subdivided blocks.
export const attemptClip = (record, id) => record.replacements?.flatMap(r => r.clips.flatMap(t => t.clips)).find(c => c.id === id) ?? find(record, id);
export function pair(record, id) {
    const clip = find(record, id);
    return clip ? all(record).filter(item => item.id === id || item.id === clip.link) : [];
}
export function gaps(record, trackId) {
    const result = [], track = trackBy(record, trackId); let start = 0;
    for (const clip of track.clips) { if (clip.start > start) result.push({kind: 'gap', track: trackId, start, frames: clip.start - start}); start = end(clip); }
    if (start < duration(record)) result.push({kind: 'gap', track: trackId, start, frames: duration(record) - start});
    return result;
}
export function at(record, frame) {
    check(whole(frame), 'range');
    return Object.fromEntries(record.tracks.map(track => {
        const clip = track.clips.find(item => overlaps(item, frame, 1));
        return [track.id, clip ? {clip, frame: sourceFrame(clip, frame - clip.start)} : null];
    }));
}
export function snapPoints(record, exceptIds = []) {
    const excluded = linkedIds(record, exceptIds);
    return [...new Set([0, ...all(record).filter(clip => !excluded.has(clip.id)).flatMap(clip => [clip.start, end(clip)])])].sort((a, b) => a - b);
}
// A selection is not stored content. Every clip on its occupied tracks counts,
// including queued/running work and placeholders left after a failed attempt.
export function rangeOccupied(record, {start, frames, tracks}) {
    return record.tracks.some(track => tracks.includes(track.id) && track.clips.some(clip => overlaps(clip, start, frames)));
}
export function references(record) {
    const result = new Map();
    const clips = [...all(record), ...(record.replacements || []).flatMap(r => [...r.originals, ...r.clips].flatMap(t => t.clips))];
    for (const clip of clips) for (const source of [clip.source, ...clip.versions]) if (source) result.set(key(source), {kind: source.kind, id: source.id});
    return [...result.values()];
}
export const clipState = clip => ['queued', 'running'].includes(clip.pending) ? 'running'
    : clip.pending === 'failed' ? 'failed' : clip.source || isText(clip) ? 'done' : 'planned';
export const emptyFrames = record => duration(record) - trackBy(record, 'v1').clips.filter(clip => clip.source).reduce((sum, clip) => sum + clip.frames, 0);
export const secondsLabel = frames => { check(whole(frames), 'range'); return String(Math.round(frames / FPS)); };
const copy = record => JSON.parse(JSON.stringify(parse(record)));
function finish(record) {
    normalizeAudioEdits(record);
    for (const track of record.tracks) track.clips.sort((a, b) => a.start - b.start);
    // Track presence is an edit in its own right, including an empty a2.
    if (record.replacements) {
        record.replacements = record.replacements.filter(tx => {
            if (tx.rolledBack) {
                // Reordering and volume edits keep the attempt attached to its media.
                for (const row of tx.originals) {
                    const positions = row.clips.map(old => ({old, visible: find(record, old.id)}));
                    for (const clip of tx.clips.find(t => t.id === row.id).clips) {
                        const owner = positions.find(({old}) => clip.start >= old.start && end(clip) <= end(old));
                        if (!owner?.visible) continue;
                        clip.start += owner.visible.start - owner.old.start;
                        if (row.kind === 'audio') clip.volume = owner.visible.volume;
                    }
                    for (const {old, visible} of positions) if (visible) {
                        old.start = visible.start;
                        if (row.kind === 'audio') old.volume = visible.volume;
                    }
                }
                for (const row of [...tx.originals, ...tx.clips]) row.clips.sort((a, b) => a.start - b.start);
                const job = tx.clips.flatMap(t => t.clips).find(c => c.job)?.job ?? null;
                const media = c => [c.start, c.frames, c.in, c.length, c.link, c.source, c.versions, c.splice];
                return tx.originals.every(t => t.clips.every(c => { const visible = find(record, c.id); return visible?.part === tx.part && visible.pending && visible.job === job && JSON.stringify(media(visible)) === JSON.stringify(media(c)); }));
            }
            tx.clips = record.tracks.slice(0, 2).map(t => ({...t, clips: t.clips.filter(c => c.part === tx.part)}));
            return tx.clips.every(t => t.clips.length) && tx.clips.some(t => t.clips.some(c => c.pending));
        });
        if (!record.replacements.length) delete record.replacements;
    }
    return parse(record);
}
function materialize(record, tx, rows) {
    const ids = new Set([...tx.originals, ...tx.clips].flatMap(t => t.clips.map(c => c.id)));
    for (const t of rows) trackBy(record, t.id).clips = [...trackBy(record, t.id).clips.filter(c => !ids.has(c.id)), ...structuredClone(t.clips)].sort((a,b) => a.start-b.start);
}
function activate(record, id) {
    const next = copy(record), tx = next.replacements?.find(r => [...r.originals, ...r.clips].some(t => t.clips.some(c => c.id === id)));
    if (tx?.rolledBack) { materialize(next, tx, tx.clips); tx.rolledBack = false; }
    return next;
}
function rollback(record, id, pending, reason = null) {
    const next = copy(record), tx = next.replacements?.find(r => r.clips.some(t => t.clips.some(c => c.id === id)));
    if (!tx) return record;
    const job = tx.clips.flatMap(t => t.clips).find(c => c.job)?.job ?? null, text = tx.clips[0].clips[0].text;
    materialize(next, tx, tx.originals);
    for (const t of tx.originals) for (const old of t.clips) Object.assign(find(next, old.id), {part: tx.part, job, pending, reason, text});
    tx.rolledBack = true;
    return finish(next);
}
function audioTrack(record, id = 'a1') {
    if (!record.tracks.some(track => track.id === id)) record.tracks.push({id, kind: 'audio', clips: []});
    return trackBy(record, id);
}
const freshId = record => `c${++record.serial}`;
// A continued video needs this many frames for its encoded tail.
const TAIL_FRAMES = 39;
function newClip(record, track, {source = null, start, frames, part = null, text = ''}) {
    const clip = {id: freshId(record), start, frames, in: 0, length: frames, source, link: null,
        text, part, pending: source ? null : 'draft', reason: null, job: null,
        extend: source?.kind !== 'import' || frames >= TAIL_FRAMES, versions: []};
    if (track.kind === 'audio') clip.volume = 1;
    track.clips.push(clip); return clip;
}
function clearPlan(clip) { delete clip.target_frames; }
// The description of a block (what it shows: the note on the timeline that regenerating uses), and who wrote it.
// A linked pair shares it; a part being generated keeps the one it was started with.
export function describe(record, ids, text, {by = 'user'} = {}) {
    check(typeof text === 'string' && validText(text.trim()) && TEXT_BY.includes(by), 'text');
    return onBlocks(record, ids, clip => written(clip, text.trim(), by), true);
}
// An outside reference kept with the block (for example a shot id); an empty value removes it.
export function setRef(record, ids, ref) {
    check(typeof ref === 'string' && (!ref || REF.test(ref)), 'ref');
    return onBlocks(record, ids, clip => { if (ref) clip.ref = ref; else delete clip.ref; }, false);
}
// A note kept with the block; an empty value removes it. A block being generated can still be noted.
export function setNote(record, ids, text) {
    check(typeof text === 'string' && (!text.trim() || validNote(text.trim())), 'text');
    return onBlocks(record, ids, clip => { if (text.trim()) clip.note = text.trim(); else delete clip.note; }, false, {picture: true, kept: true});
}
// Only an agent's description is marked; no mark means the user wrote it, as in every earlier edit.
// A note was written about the description it came with, so it goes when the user writes another one.
function written(clip, text, by) {
    if (by === 'user' && text !== clip.text) delete clip.note;
    clip.text = text; if (text && by === 'agent') clip.text_by = by; else delete clip.text_by;
}
// A planned part's own description. The parts after it that still had the same one take the new one too;
// a part with none of its own is left so, and goes on using the one before it when generated.
export function describePlanned(record, ids, text, {by = 'user'} = {}) {
    const selected = linkedIds(record, ids), video = trackBy(record, 'v1').clips;
    const chosen = video.filter(clip => selected.has(clip.id)).sort((a, b) => a.start - b.start);
    const following = [], old = chosen.at(-1)?.text ?? '';
    for (let last = chosen.at(-1), next; old && (next = video.find(clip => continuing(clip, last))); last = next) {
        if (selected.has(next.id) || next.source || next.pending !== 'draft' || next.job || (next.text && next.text !== old)) break;
        if (next.text) following.push(next.id);
    }
    return describe(record, [...ids, ...following], text, {by});
}
function onBlocks(record, ids, change, refuseBusy, {picture = false, kept = false} = {}) {
    check(Array.isArray(ids) && ids.length > 0 && ids.every(id => typeof id === 'string'), 'id');
    for (const id of ids) check(find(record, id), 'missing');
    const selected = linkedIds(record, ids), clips = all(record).filter(clip => selected.has(clip.id));
    check(!clips.some(isText), 'text');
    // A sound block takes the change only together with the picture it is linked to.
    check(!picture || clips.every(clip => trackOf(record, clip.id).kind === 'video' || clip.link), 'text');
    assertUnlocked(record, [...selected]); if (refuseBusy) busy(clips);
    const next = copy(record); for (const clip of all(next)) if (selected.has(clip.id)) change(clip);
    // The blocks set aside while a part is being replaced take the change too: whichever of them comes back shows it.
    if (kept) for (const tx of next.replacements || []) for (const row of [...tx.originals, ...tx.clips]) {
        const shown = clips.filter(clip => trackOf(record, clip.id).id === row.id);
        for (const clip of row.clips) if (shown.some(v => v.id === clip.id || (clip.start >= v.start && end(clip) <= end(v)))) change(clip);
    }
    return finish(next);
}
export function linkedIds(record, ids) {
    check(Array.isArray(ids) && ids.every(id => typeof id === 'string'), 'id');
    const clips = new Map(all(record).map((clip, index) => [clip.id, {clip, index}]));
    return new Set(ids.flatMap(id => {
        const item = clips.get(id), mate = clips.get(item?.clip.link);
        return !item ? [] : !mate ? [id] : item.index < mate.index ? [id, mate.clip.id] : [mate.clip.id, id];
    }));
}
function shift(record, from, delta, excluded = new Set()) {
    for (const clip of all(record)) if (clip.start >= from && !excluded.has(clip.id)) clip.start += delta;
    // Keep retry/rollback receipts at the same position as the visible content.
    for (const tx of record.replacements || []) for (const row of [...tx.originals, ...tx.clips])
        for (const clip of row.clips) if (clip.start >= from && !excluded.has(clip.id)) clip.start += delta;
}
export function insertionAt(record, trackId, start) {
    check(whole(start), 'range');
    const hit = trackBy(record, trackId).clips.find(clip => clip.start < start && end(clip) > start);
    return hit ? end(hit) : start;
}
// Measure the gap only on the occupied tracks, then move the same suffix on
// every track. Earlier-starting content, including crossing music, stays put.
export function roomAt(record, from, frames, {tracks = record.tracks.map(t => t.id), excluded = []} = {}) {
    check(whole(from) && whole(frames), 'range');
    tracks = new Set(tracks); excluded = new Set(excluded);
    const later = all(record).filter(clip => clip.start >= from && !excluded.has(clip.id));
    const gap = Math.min(...later.filter(clip => tracks.has(trackOf(record, clip.id).id)).map(clip => clip.start - from));
    const shift = Math.max(0, frames - gap);
    const locked = shift > 0 && later.some(clip => trackOf(record, clip.id).locked);
    return {shift, limit: from + gap, blocked: locked || shift > 0 && later.some(clip => ['queued', 'running'].includes(clip.pending)),
        ...(locked ? {code: 'locked'} : {})};
}
function applyRoom(record, from, frames, {excluded = [], ...options} = {}) {
    const room = roomAt(record, from, frames, {...options, excluded});
    check(!room.blocked, room.code || 'busy');
    if (room.shift) {
        excluded = new Set(excluded);
        shift(record, from, room.shift, excluded);
    }
}
export function makeRoom(record, from, frames, options) {
    const room = roomAt(record, from, frames, options); check(!room.blocked, room.code || 'busy');
    const next = copy(record); applyRoom(next, from, frames, options); return finish(next);
}
function rightEdgeOptions(record, id, {generating = false, frames = 0} = {}) {
    const selected = pair(record, id); check(selected.length, 'missing');
    const tracks = new Set(selected.map(clip => trackOf(record, clip.id).id));
    if (generating) tracks.add(sourceAudioTrack(copy(record), end(selected[0]), frames, true).id);
    return {from: end(selected[0]), tracks, excluded: selected.map(clip => clip.id)};
}
export function rightEdgeRoom(record, id, frames, options) {
    const {from, ...roomOptions} = rightEdgeOptions(record, id, {...options, frames});
    return roomAt(record, from, frames, roomOptions);
}
function makeRightEdgeRoom(record, id, frames, options) {
    const {from, ...roomOptions} = rightEdgeOptions(record, id, {...options, frames});
    applyRoom(record, from, frames, roomOptions);
}
function sourceAudioTrack(record, start, frames, allowRoom = false) {
    // Keep a free lane when possible; otherwise only later starts can yield.
    if (allowRoom) try { return sourceAudioTrack(record, start, frames); } catch (error) { if (error.code !== 'audio_full') throw error; }
    for (const id of ['a1', 'a2']) {
        const track = record.tracks.find(row => row.id === id);
        if (!track || !track.clips.some(clip => (!allowRoom || clip.start < start) && overlaps(clip, start, frames))) return audioTrack(record, id);
    }
    throw new EditError('audio_full');
}
// A saved project file holds 8 MiB; every character outside plain ASCII is written there as six.
const STORED_ROOM = 8 * 1024 * 1024 - 64 * 1024;
function storedSize(record) {
    const written = JSON.stringify(record); let size = written.length;
    for (let i = 0; i < written.length; i++) { const code = written.charCodeAt(i); size += code > 126 ? 5 : code === 44 || code === 58 ? 1 : 0; }
    return size;
}
// A saved video brings the description it was generated from; one that cannot be kept is left out.
export function insertSource(record, {source, frames, hasVideo, hasAudio, width, height, at: position = 0, text = '', by = 'user', targetTrack}) {
    const given = source; source = sourceData(source); check(whole(frames, 1) && whole(position), 'range');
    check(typeof hasVideo === 'boolean' && typeof hasAudio === 'boolean' && (hasVideo || hasAudio), 'source');
    check(TEXT_BY.includes(by), 'text');
    const words = hasVideo && typeof text === 'string' && validText(text.trim()) ? text.trim() : '';
    const next = copy(record);
    if (hasVideo) followFirstSize(next, width, height);
    const explicitAudioTrack = !hasVideo && targetTrack !== undefined;
    if (explicitAudioTrack) check(['a1', 'a2'].includes(targetTrack), 'track');
    let track = explicitAudioTrack ? audioTrack(next, targetTrack) : trackBy(next, hasVideo ? 'v1' : 'a1');
    if (!hasVideo && !explicitAudioTrack && track.clips.some(clip => overlaps(clip, position, frames))) track = audioTrack(next, 'a2');
    check(!explicitAudioTrack || !track.locked, 'locked');
    const start = insertionAt(next, track.id, position);
    const available = hasVideo && hasAudio ? copy(makeRoom(next, start, frames, {tracks: [track.id]})) : null;
    const sound = available ? audioTrack(next, sourceAudioTrack(available, start, frames, true).id) : null;
    applyRoom(next, start, frames, {tracks: [track.id, ...(sound ? [sound.id] : [])]});
    const primary = newClip(next, track, {source, frames, start});
    if (words) written(primary, words, by);
    if (hasVideo && hasAudio) {
        const mate = newClip(next, sound, {source, frames, start});
        primary.link = mate.id; mate.link = primary.id;
        if (words) written(mate, words, by);
    }
    const result = finish(next);
    // The description is a convenience: a project it would make too large to save takes the video without it.
    if (words && storedSize(result) > STORED_ROOM) return insertSource(record, {source: given, frames, hasVideo, hasAudio, width, height, at: position, targetTrack});
    return result;
}
export function move(record, id, start, {targetTrack} = {}) {
    if (isText(find(record, id))) return changeText(record, id, {start});
    check(whole(start), 'range'); const selected = pair(record, id); check(selected.length, 'missing'); busy(selected);
    const next = copy(record), ids = new Set(selected.map(clip => clip.id));
    const clips = all(next).filter(clip => ids.has(clip.id)), primary = clips[0];
    let track = trackOf(next, primary.id);
    // Only independent audio changes rows; linked pairs keep automatic routing.
    if (clips.length === 1 && track.kind === 'audio' && targetTrack !== undefined) {
        check(['a1', 'a2'].includes(targetTrack), 'track');
        assertUnlocked(record, [primary.id]);
        track = audioTrack(next, targetTrack);
        check(!track.locked, 'locked');
    }
    for (const row of next.tracks) row.clips = row.clips.filter(clip => !ids.has(clip.id));
    start = insertionAt(next, track.id, start);
    const sound = track.kind === 'video' && clips.find(clip => trackOf(record, clip.id).kind === 'audio');
    const available = sound ? makeRoom(next, start, primary.frames, {tracks: [track.id]}) : null;
    const soundTrack = sound ? sourceAudioTrack(available, start, primary.frames, true).id : null;
    applyRoom(next, start, primary.frames, {tracks: [track.id, ...(soundTrack ? [soundTrack] : [])], excluded: ids});
    for (const clip of clips) { clip.start = start; (clip === sound ? audioTrack(next, soundTrack) : clip === primary ? track : trackBy(next, trackOf(record, clip.id).id)).clips.push(clip); }
    return finish(next);
}
// A selected group keeps its gaps and tracks; validation rejects occupied landings.
export function moveTogether(record, ids, delta) {
    assertUnlocked(record, ids);
    check(Number.isSafeInteger(delta), 'range');
    const selected = linkedIds(record, ids), clips = all(record).filter(clip => selected.has(clip.id));
    check(clips.length && ids.every(id => selected.has(id)), 'missing'); busy(clips);
    delta = Math.max(delta, -Math.min(...clips.map(clip => clip.start)));
    const next = copy(record);
    for (const clip of all(next)) if (selected.has(clip.id)) clip.start += delta;
    return finish(next);
}
function detachAttempt(record, ids) {
    const selected = new Set(ids);
    // Geometry/version edits detach only the affected source ranges.
    if (record.replacements) {
        record.replacements = record.replacements.filter(tx => {
            if (!tx.originals.some(t => t.clips.some(c => selected.has(c.id)))) return true;
            for (const row of tx.originals) {
                const removed = row.clips.filter(c => selected.has(c.id));
                tx.clips.find(t => t.id === row.id).clips = tx.clips.find(t => t.id === row.id).clips
                    .filter(c => !removed.some(old => c.start >= old.start && end(c) <= end(old)));
                row.clips = row.clips.filter(c => !selected.has(c.id));
            }
            if (tx.originals.some(t => !t.clips.length)) return false;
            if (tx.clips.some(t => t.clips.some(c => c.pending))) return true;
            materialize(record, tx, tx.clips); return false;
        });
        if (!record.replacements.length) delete record.replacements;
    }
    for (const clip of all(record)) if (selected.has(clip.id) && clip.job) {
        clip.job = clip.reason = clip.part = null; clip.pending = clip.source ? null : 'draft'; clearPlan(clip);
    }
}
export function trim(record, id, edge, frame, {minimumFrames = FPS, by = 'user'} = {}) {
    if (isText(find(record, id))) { check(['start', 'end'].includes(edge) && whole(frame), 'range'); return trimText(record, id, edge, frame); }
    check(['start', 'end'].includes(edge) && whole(frame) && whole(minimumFrames, 1) && TEXT_BY.includes(by), 'range');
    if (speedEdited(find(record, id))) return trimSpeed(record, id, edge, frame, by);
    const clips = pair(record, id); check(clips.length, 'missing'); busy(clips);
    const primary = clips[0], minimum = Math.min(minimumFrames, primary.frames);
    const delta = edge === 'start' ? Math.min(primary.frames - minimum, Math.max(-Math.min(...clips.map(clip => clip.in)), frame - primary.start))
        : Math.max(minimum - primary.frames, Math.min(Math.min(...clips.map(clip => clip.length - clip.in - clip.frames)), frame - end(primary)));
    const next = copy(record);
    if (delta) detachAttempt(next, clips.map(c => c.id));
    if (edge === 'end' && delta > 0) makeRightEdgeRoom(next, id, delta);
    for (const clip of all(next).filter(clip => clips.some(item => item.id === clip.id))) {
        if (edge === 'start') {
            clip.start += delta; clip.in += delta; clip.frames -= delta;
            clip.versions = clip.versions.map(version => ({...version, in: Math.max(0, version.in + delta)}))
                .filter(version => version.in < version.length);
        }
        else clip.frames += delta;
        if (!clip.source) { clip.in = 0; clip.length = clip.frames; }
        if (delta) touchedBy(clip, by);
        clearPlan(clip);
    }
    return finish(next);
}
export function split(record, id, frame, {by = 'user'} = {}) {
    assertUnlocked(record, [id]);
    check(whole(frame) && TEXT_BY.includes(by), 'range');
    if (speedEdited(find(record, id))) return splitSpeed(record, id, frame, by);
    const clips = pair(record, id); check(clips.length, 'missing'); busy(clips);
    const primary = clips[0]; check(frame >= primary.start && frame <= end(primary), 'range');
    if (frame === primary.start || frame === end(primary)) return parse(record);
    const next = copy(record), left = frame - primary.start, rights = [];
    detachAttempt(next, clips.map(c => c.id));
    for (const old of clips) {
        const clip = find(next, old.id); touchedBy(clip, by);
        const right = structuredClone(clip); right.id = freshId(next); right.start = frame; right.frames -= left; right.in += left;
        clip.frames = left; clearPlan(clip); clearPlan(right);
        splitAudioFields(clip, right);
        if (!clip.source) { clip.in = right.in = 0; clip.length = clip.frames; right.length = right.frames; }
        right.versions = right.versions.map(version => ({...version, in: version.in + left}));
        for (const item of [clip, right]) item.versions = item.versions.filter(version => version.in < version.length);
        // The two halves of a part that is not generated yet stay one video.
        delete right.follows; if (!clip.source && trackOf(next, clip.id).id === 'v1') right.follows = clip.id;
        // What continued the cut part now continues its second half.
        for (const later of trackOf(next, clip.id).clips) if (later.follows === clip.id && later.start === end(right)) later.follows = right.id;
        trackOf(next, clip.id).clips.push(right); rights.push(right);
    }
    if (rights.length === 2) { rights[0].link = rights[1].id; rights[1].link = rights[0].id; }
    return finish(next);
}
export function remove(record, ids) {
    assertUnlocked(record, ids);
    check(Array.isArray(ids), 'id'); const selected = linkedIds(record, ids);
    busy(all(record).filter(clip => selected.has(clip.id)));
    const next = copy(record); for (const track of next.tracks) track.clips = track.clips.filter(clip => !selected.has(clip.id));
    return finish(next);
}
export function closeGap(record, trackId, frame) {
    assertUnlocked(record, trackBy(record, trackId).clips.map(c => c.id));
    check(whole(frame), 'range');
    const gap = gaps(record, trackId).find(item => frame >= item.start && frame < item.start + item.frames);
    if (!gap) return parse(record);
    const from = gap.start + gap.frames;
    for (const track of record.tracks) {
        const moving = track.clips.filter(clip => clip.start >= from), fixed = track.clips.filter(clip => clip.start < from);
        const code = moving.some(clip => ['queued', 'running'].includes(clip.pending)) ? 'busy'
            : moving.length && track.locked ? 'locked'
                : moving.some(clip => fixed.some(other => overlaps(other, clip.start - gap.frames, clip.frames))) ? 'overlap' : null;
        if (code) { const error = new EditError(code); error.track = track.id; throw error; }
    }
    const next = copy(record); shift(next, from, -gap.frames); return finish(next);
}
export function setVolume(record, id, volume) {
    const next = copy(record), track = trackOf(next, id); check(track, 'missing'); check(track.kind === 'audio', 'track');
    find(next, id).volume = volume;
    for (const tx of next.replacements || []) if (tx.rolledBack) {
        const old = tx.originals.flatMap(t => t.clips).find(c => c.id === id);
        if (old) { old.volume = volume; for (const t of tx.clips) if (t.kind === 'audio') for (const c of t.clips) if (c.start >= old.start && end(c) <= end(old)) c.volume = volume; }
    }
    return finish(next);
}
export const rename = (record, name) => finish({...copy(record), name, ...(name !== record.name ? {keep_empty: true} : {})});
export const setPictureSize = (record, width, height, size_mode = 'fixed') => finish({...copy(record), width, height, size_mode,
    ...(width !== record.width || height !== record.height || size_mode !== record.size_mode ? {keep_empty: true} : {})});
export const setPicture = pictureEditor({copy, trackBy, check, pictureFit, finish});
export const setPictureFit = (record, id, fit) => setPicture(record, id, {fit: pictureFit(fit)});

// The fewest frames a continuing part of `seconds` whole seconds asks for (chain_plan.part_frames): below
// MIN_PART_FRAMES a continuing part is too short, below FULL_PART_FRAMES it is a short part.
const partFrames = seconds => RULES.overlap + RULES.step * Math.ceil((FPS * seconds - (RULES.step - 1)) / RULES.step);
export const MIN_PART_FRAMES = partFrames(RULES.followSeconds), FULL_PART_FRAMES = partFrames(RULES.fullSeconds);
// The most parts one video may have (chain_plan.parts_limit).
export const partsLimit = (short, firstFrame = false) => short && !firstFrame ? Math.min(RULES.maxParts, RULES.shortMaxParts) : RULES.maxParts;
// The shortest a part that continues another may be; a device that cannot make a part that long keeps the general minimum.
const followLeast = maxFollowSeconds => maxFollowSeconds >= RULES.followSeconds ? RULES.followSeconds : RULES.minSeconds;
// A length to continue by, in whole seconds, raised to one that parts of followSeconds..maxFollowSeconds can add up to.
export function continuedSeconds(seconds, maxFollowSeconds = RULES.maxSeconds) {
    check(typeof seconds === 'number' && Number.isFinite(seconds) && typeof maxFollowSeconds === 'number' && Number.isFinite(maxFollowSeconds), 'range');
    const least = followLeast(maxFollowSeconds), follow = Math.max(least, Math.floor(Math.min(RULES.maxSeconds, maxFollowSeconds)));
    const length = Math.max(least, Math.round(seconds));
    return Math.max(length, Math.ceil(length / follow) * least);
}
// The length a new continuation starts with: a full part, or a short one where the device cannot make a full part
// or `short` asks for it.
export function defaultContinueSeconds(maxFollowSeconds = RULES.maxSeconds, {short = false} = {}) {
    const full = !short && maxFollowSeconds >= RULES.fullSeconds;
    return continuedSeconds(full ? RULES.continueSeconds : RULES.shortContinueSeconds, maxFollowSeconds);
}
function segmentLengths(frames, maxSeconds = RULES.maxSeconds, maxFollowSeconds = maxSeconds, continues = false) {
    check(typeof maxSeconds === 'number' && Number.isFinite(maxSeconds) && maxSeconds >= RULES.minSeconds, 'range');
    check(typeof maxFollowSeconds === 'number' && Number.isFinite(maxFollowSeconds) && maxFollowSeconds >= RULES.minSeconds, 'range');
    const max = Math.floor(Math.min(RULES.maxSeconds, maxSeconds)) * FPS, follow = Math.floor(Math.min(RULES.maxSeconds, maxFollowSeconds)) * FPS, lengths = [];
    let left = Math.max(RULES.minSeconds, Math.round(frames / FPS)) * FPS;
    // Under half a minute is one generation wherever this device makes it in one;
    // half a minute or more, or beyond the device, goes in parts of a quarter minute.
    if (!continues && left < RULES.singleSeconds * FPS && left <= Math.floor(maxSeconds) * FPS) return [left];
    check(left <= RULES.maxClips * max, 'range');
    // Every part after the first continues the one before it, as chain_plan lays them out: the parts after the first
    // share what is left evenly, and a rest too short for them comes off the first. They are fullSeconds or longer
    // whenever the length allows it, else at least followSeconds.
    const seconds = continues && follow >= RULES.followSeconds * FPS ? continuedSeconds(left / FPS, follow / FPS) : left / FPS;
    for (const least of [RULES.fullSeconds, RULES.followSeconds]) {
        if (follow < least * FPS) continue;
        const head = continues ? 0 : Math.min(max / FPS, seconds), rest = seconds - head, count = Math.ceil(rest / (follow / FPS));
        const shared = Math.max(rest, least * count);
        if (continues ? shared === rest : seconds - shared >= RULES.minSeconds) {
            check(count <= RULES.maxClips, 'range');
            const result = continues ? [] : [(seconds - shared) * FPS];
            for (let i = 0; i < count; i++) result.push((Math.floor(shared / count) + (i < shared % count ? 1 : 0)) * FPS);
            return result;
        }
    }
    while (left) { const length = Math.min(left, lengths.length || continues ? follow : max); lengths.push(length); left -= length; }
    const last = lengths.length - 1;
    if (last > 0 && lengths[last] < Math.min(follow, RULES.tailSeconds * FPS)) {
        const both = lengths[last - 1] + lengths[last]; lengths[last - 1] = Math.min(last === 1 && !continues ? max : follow, Math.max(both - follow, Math.ceil(both / 2 / FPS) * FPS)); lengths[last] = both - lengths[last - 1];
    }
    return lengths;
}
function oldVersion(clip) {
    return {...clip.source, in: clip.in, length: clip.length, ...pictureFields(clip), ...(clip.splice ? {splice: clip.splice} : {}),
        ...(clip.take ? {take: clip.take} : {}), ...(clip.made_text !== undefined ? {made_text: clip.made_text} : {})};
}
// Saved edits from before version numbers count their versions newest first.
function numberTakes(clip) {
    const items = [...clip.versions].reverse(); if (clip.source) items.push(clip);
    if (!items.every(item => item.take)) items.forEach((item, index) => { item.take = index + 1; });
    return items.reduce((most, item) => Math.max(most, item.take), 0);
}
// A part's versions in the order they were made; index -1 is the one in use.
export function takes(clip) {
    const items = clip.versions.map((version, index) => ({index, take: version.take,
        usable: version.in + clip.frames <= version.length})).reverse();
    if (clip.source) items.push({index: -1, take: clip.take, usable: true});
    if (items.every(item => item.take)) items.sort((a, b) => a.take - b.take);
    return items.map(({index, usable}, n) => ({index, number: n + 1, usable}));
}
// The description was changed after the version in use was made from it.
export const describedSince = clip => !!clip.source && clip.made_text !== undefined && clip.text !== clip.made_text;
// A user's own cut or version choice means an agent leaves the part alone.
function touchedBy(clip, by) { if (by === 'user') delete clip.text_by; }
function remember(clip, source = null) {
    const versions = clip.source ? [oldVersion(clip), ...clip.versions] : clip.versions;
    const seen = new Set();
    clip.versions = versions.filter(version => {
        const identity = key(version); if (identity === (source && key(source)) || seen.has(identity)) return false;
        seen.add(identity); return true;
    }).slice(0, 50);
}
// A part continues the one before it only when it was pulled out of that part's end, or both were drawn as one range.
// A range drawn on its own starts a new video, even when it touches the part before it.
const continuing = (clip, before) => !!clip && !!before && before.id !== clip.id && end(before) === clip.start
    && (clip.follows === before.id || (clip.part !== null && clip.part === before.part));
const chainSource = clip => !!clip.job && !!clip.pending;
const continuationKey = clip => chainSource(clip) ? `chain:${clip.job}:${clip.id}` : clip.source ? key(clip.source) : null;
const wholeEnd = clip => !!clip.source && clip.extend && clip.in + clip.frames === clip.length;
const partBefore = (record, clip) => trackBy(record, 'v1').clips.find(before => continuing(clip, before)) ?? null;
// Whether the part is drawn attached to the one before it on its track.
export function continuesBefore(record, id) {
    const clip = find(record, id); if (!clip) return false;
    const video = trackOf(record, id).id === 'v1' ? clip : clip.link ? find(record, clip.link) : null;
    return !!video && trackBy(record, 'v1').clips.includes(video) && !!partBefore(record, video);
}
// The lengths one range is generated in, as planRange lays them out on empty tracks.
export const partLengths = (frames, {maxSeconds = RULES.maxSeconds, maxFollowSeconds = maxSeconds, continues = false} = {}) => segmentLengths(frames, maxSeconds, maxFollowSeconds, continues);
// One video is generated in at most partsLimit parts: the longest range that still fits. A device whose continuing
// parts are all short has the short-part limit.
export function maxRangeFrames(record, {continues = false, maxSeconds = RULES.maxSeconds, maxFollowSeconds = maxSeconds, firstFrame = false}) {
    const before = continues;
    const follow = Math.floor(Math.min(RULES.maxSeconds, maxFollowSeconds)) * FPS;
    const parts = partsLimit(follow < RULES.fullSeconds * FPS, firstFrame);
    // The part being continued is the first of them.
    return before ? (parts - 1) * follow : Math.floor(Math.min(RULES.maxSeconds, maxSeconds)) * FPS + (parts - 1) * follow;
}
// Manual boundaries use device limits, not the automatic fifteen-second preference.
export function rangeParts(record, {start, frames, parts, continues, maxSeconds = RULES.maxSeconds, maxFollowSeconds = maxSeconds}) {
    // A range that starts on a part pulled out of the one before it keeps that part's shorter limit.
    const head = trackBy(record, 'v1').clips.find(clip => clip.start === start);
    const following = continues ?? !!(head && partBefore(record, head)), first = following ? maxFollowSeconds : maxSeconds;
    // A part that continues another is at least followSeconds long.
    const least = index => (index || following ? followLeast(maxFollowSeconds) : RULES.minSeconds) * FPS;
    const valid = Array.isArray(parts) && parts.length > 0 && parts.length <= RULES.maxClips
        && parts.every((count, i) => whole(count, least(i)) && count % FPS === 0
            && count <= Math.floor(i ? maxFollowSeconds : first) * FPS)
        && parts.reduce((sum, count) => sum + count, 0) === frames;
    return valid ? [...parts] : partLengths(frames, {maxSeconds: first, maxFollowSeconds, continues: following});
}
export function adjustPartBoundary(parts, index, leftFrames, {maxSeconds = RULES.maxSeconds, maxFollowSeconds = maxSeconds, continues = false} = {}) {
    check(Array.isArray(parts) && whole(index) && index < parts.length - 1 && Number.isFinite(leftFrames), 'range');
    check(parts.every(count => whole(count, RULES.minSeconds * FPS) && count % FPS === 0), 'range');
    const minimum = RULES.minSeconds * FPS, total = parts[index] + parts[index + 1];
    const leftMax = Math.floor(index ? maxFollowSeconds : maxSeconds) * FPS, rightMax = Math.floor(maxFollowSeconds) * FPS;
    // Every part that continues another is at least followSeconds long; a pair too short for that keeps the general minimum.
    const follows = followLeast(maxFollowSeconds) * FPS, leftLeast = index || continues ? follows : minimum;
    const tight = total >= leftLeast + follows;
    const low = Math.max(tight ? leftLeast : minimum, total - rightMax), high = Math.min(leftMax, total - (tight ? follows : minimum));
    check(Number.isFinite(low) && Number.isFinite(high) && low <= high, 'range');
    const desired = Math.round(leftFrames / FPS) * FPS, left = Math.max(low, Math.min(high, desired));
    const result = [...parts]; result[index] = left; result[index + 1] = total - left;
    // The left part also stops where the part after it would fall under followSeconds.
    return {parts: result, limited: desired >= leftMax && left === leftMax ? leftMax / FPS
        : total - desired >= rightMax && total - left === rightMax ? rightMax / FPS
            : tight && desired > left && left === total - follows ? left / FPS : null};
}
export function resizePlannedBoundary(record, leftId, rightId, frame, limits = {}) {
    const left = find(record, leftId), right = find(record, rightId);
    check(left && right && trackOf(record, leftId) === trackOf(record, rightId) && end(left) === right.start
        && left.part && left.part === right.part, 'range');
    const members = [...pair(record, leftId), ...pair(record, rightId)]; busy(members);
    check(members.every(clip => clip.pending && !clip.source), 'unsupported');
    const video = trackOf(record, leftId).id === 'v1' ? left : find(record, left.link);
    const preceding = !!video && !!partBefore(record, video);
    const maxSeconds = limits.maxSeconds ?? RULES.maxSeconds, maxFollowSeconds = limits.maxFollowSeconds ?? maxSeconds;
    const result = adjustPartBoundary([left.frames, right.frames], 0, frame - left.start,
        {maxSeconds: preceding ? maxFollowSeconds : maxSeconds, maxFollowSeconds, continues: preceding});
    const next = copy(record); detachAttempt(next, members.map(clip => clip.id));
    for (const old of members) find(next, old.id).part = left.part;
    for (const clip of pair(next, leftId)) { clip.frames = clip.length = result.parts[0]; clearPlan(clip); }
    for (const clip of pair(next, rightId)) { clip.start = left.start + result.parts[0]; clip.frames = clip.length = result.parts[1]; clearPlan(clip); }
    return finish(next);
}
export function resizePlanned(record, id, edge, frame, limits = {}) {
    check(['start', 'end'].includes(edge) && whole(frame), 'range');
    const members = pair(record, id); check(members.length, 'missing'); busy(members);
    check(members.every(clip => clip.pending && !clip.source), 'unsupported');
    const primary = members[0];
    const video = trackOf(record, id).id === 'v1' ? primary : find(record, primary.link);
    const maxFollowSeconds = limits.maxFollowSeconds ?? limits.maxSeconds ?? RULES.maxSeconds;
    // A planned part that continues another is at least followSeconds long.
    const minimum = (video && partBefore(record, video) ? followLeast(maxFollowSeconds) : RULES.minSeconds) * FPS;
    const neighbors = members.flatMap(clip => trackOf(record, clip.id).clips.filter(other => other.id !== clip.id));
    const low = Math.max(0, ...neighbors.filter(clip => end(clip) <= primary.start).map(end));
    const high = Math.min(1e7, ...neighbors.filter(clip => clip.start >= end(primary)).map(clip => clip.start));
    const position = Math.round(frame / FPS) * FPS;
    let start = edge === 'start' ? Math.max(low, Math.min(end(primary) - minimum, position)) : primary.start;
    let stop = edge === 'end' ? Math.min(high, Math.max(start + minimum, position)) : end(primary);
    if (minimum > RULES.minSeconds * FPS) {
        // Lengths no run of continuing parts can fill (16–19 s at fifteen-second parts) are raised to the next one that fits.
        const fitted = continuedSeconds((stop - start) / FPS, maxFollowSeconds) * FPS;
        if (fitted > stop - start) {
            if (edge === 'end' && start + fitted <= high) stop = start + fitted;
            else if (edge === 'start' && stop - fitted >= low) start = stop - fitted;
        }
    }
    let next = copy(record); detachAttempt(next, members.map(clip => clip.id));
    const part = primary.part || freshId(next);
    for (const clip of pair(next, id)) { clip.start = start; clip.frames = clip.length = stop - start; clip.part = part; clearPlan(clip); }
    next = finish(next);
    const lengths = rangeParts(next, {start, frames: stop - start, ...limits});
    let positionAt = start, currentId = id;
    for (const count of lengths.slice(0, -1)) {
        positionAt += count; next = split(next, currentId, positionAt);
        currentId = trackOf(next, id).clips.find(clip => clip.start === positionAt).id;
    }
    return next;
}
export function planRange(record, {start, frames, parts, target = 'both', text = '', by = 'user', maxSeconds = RULES.maxSeconds, maxFollowSeconds = maxSeconds}) {
    check(TEXT_BY.includes(by), 'text');
    check(whole(start) && whole(frames, 1), 'range'); check(['both', 'video', 'audio'].includes(target), 'target');
    check(validText(text), 'text');
    check(typeof maxSeconds === 'number' && Number.isFinite(maxSeconds) && maxSeconds >= RULES.minSeconds, 'range');
    check(target === 'both', 'unsupported');
    if (!record.tracks.some(track => track.clips.some(clip => overlaps(clip, start, frames)))) frames = Math.max(RULES.minSeconds, Math.round(frames / FPS)) * FPS;
    const videoHits = trackBy(record, 'v1').clips.filter(clip => overlaps(clip, start, frames));
    const audioHits = trackBy(record, 'a1').clips.filter(clip => overlaps(clip, start, frames));
    const otherHits = record.tracks.find(track => track.id === 'a2')?.clips.filter(clip => overlaps(clip, start, frames)) || [];
    const hits = [...videoHits, ...audioHits];
    if (hits.length || otherHits.length) {
        check(hits.length > 0, 'unsupported');
        check(videoHits.length && audioHits.length === videoHits.length, 'unsupported');
        let boundary = start;
        for (const clip of videoHits) {
            const mate = find(record, clip.link);
            check(clip.start === boundary && end(clip) <= start + frames && mate && audioHits.includes(mate)
                && (!!clip.source === !!mate.source) && (!clip.source || ['work', 'generated'].includes(clip.source.kind))
                && (!mate.source || ['work', 'generated'].includes(mate.source.kind)), 'unsupported');
            boundary = end(clip);
        }
        check(boundary === start + frames, 'unsupported');
    }
    const firstMaximum = videoHits[0]?.start === start && partBefore(record, videoHits[0]) ? maxFollowSeconds : maxSeconds;
    let next = copy(record);
    const originals = videoHits.length && videoHits.every(c => c.source) ? record.tracks.slice(0, 2).map(t => ({...t, clips: t.clips.filter(c => overlaps(c, start, frames))})) : null;
    // A new attempt replaces an abandoned transaction over the same originals.
    if (next.replacements) { next.replacements = next.replacements.filter(tx => !tx.originals.some(t => t.clips.some(c => hits.some(hit => hit.id === c.id)))); if (!next.replacements.length) delete next.replacements; }
    const targetTracks = target === 'both' ? ['v1', 'a1'] : [target === 'video' ? 'v1' : 'a1'];
    const affected = () => targetTracks.flatMap(track => trackBy(next, track).clips.filter(clip => overlaps(clip, start, frames)));
    // A completed segment may be 362 rather than 360 frames. Selecting that
    // entire segment must not round off two frames and become a partial edit.
    if (!affected().length) frames = Math.max(RULES.minSeconds, Math.round(frames / FPS)) * FPS;

    let touched = affected();
    busy(touched);
    check(touched.every(clip => clip.start >= start && end(clip) <= start + frames), 'range');
    // Re-generation preserves existing editable boundaries and playable media.
    // Only the currently unproved single-track linked replacement is refused.
    check(target === 'both' || !touched.some(clip => clip.link), 'unsupported');
    if (target === 'audio') check(trackBy(next, 'v1').clips.some(clip => clip.source && clip.start <= start && end(clip) >= start + frames), 'range');
    // A long imported/work clip is split into playable source slices before
    // being replaced. Allow the small grid excess of an already prepared clip.
    const maximum = Math.floor(Math.min(firstMaximum, RULES.maxSeconds)) * FPS;
    const handled = new Set();
    for (const old of touched) {
        if (handled.has(old.id)) continue;
        pair(next, old.id).forEach(clip => handled.add(clip.id));
        if (old.frames <= maximum + RULES.step - 1) continue;
        const lengths = segmentLengths(old.frames, old.start === start ? firstMaximum : maxFollowSeconds, maxFollowSeconds);
        lengths[lengths.length - 1] += old.frames - lengths.reduce((sum, count) => sum + count, 0);
        let id = old.id, position = old.start;
        for (const count of lengths.slice(0, -1)) {
            position += count;
            next = copy(split(finish(next), id, position, {by}));
            id = trackBy(next, trackOf(record, old.id).id).clips.find(clip => clip.start === position).id;
        }
    }
    touched = affected();
    const boundaries = [...new Set([start, start + frames, ...touched.flatMap(clip => [clip.start, end(clip)])])].sort((a, b) => a - b);
    const part = freshId(next);
    for (const clip of touched) {
        clip.pending = 'draft'; clip.reason = clip.job = null; written(clip, text, by); clip.part = part; clearPlan(clip);
    }
    for (let i = 0; i < boundaries.length - 1; i++) {
        const from = boundaries[i], length = boundaries[i + 1] - from;
        const missing = targetTracks.filter(track => !trackBy(next, track).clips.some(clip => clip.start === from && clip.frames === length));
        if (!missing.length) continue;
        const occupied = targetTracks.some(track => trackBy(next, track).clips.some(clip => clip.start === from && clip.frames === length));
        const lengths = occupied ? [length] : rangeParts(record, {start: from, frames: length,
            parts: from === start && length === frames ? parts : undefined,
            maxSeconds: from === start ? firstMaximum : maxFollowSeconds, maxFollowSeconds});
        // Keep exact sub-second boundaries beside existing material.
        lengths[lengths.length - 1] += length - lengths.reduce((sum, count) => sum + count, 0);
        check(lengths.every(count => count > 0), 'range');
        let position = from;
        for (const count of lengths) {
            check(count <= from + length - position, 'range');
            const added = missing.map(track => newClip(next, trackBy(next, track), {start: position, frames: count, text, part}));
            for (const clip of added) written(clip, text, by);
            if (target === 'both') {
                const video = added.find(clip => trackOf(next, clip.id).id === 'v1') ?? trackBy(next, 'v1').clips.find(clip => clip.start === position && clip.frames === count);
                const audio = added.find(clip => trackOf(next, clip.id).id === 'a1') ?? trackBy(next, 'a1').clips.find(clip => clip.start === position && clip.frames === count);
                check(video && audio && (!video.link || video.link === audio.id) && (!audio.link || audio.link === video.id), 'range');
                // A missing counterpart uses the same old source slice. Its
                // absent media stream remains silent/black, and both in-points
                // stay identical while the old content is still playable.
                if (added.length === 1) {
                    const placeholder = added[0], mate = placeholder === video ? audio : video;
                    placeholder.source = mate.source; placeholder.in = mate.in; placeholder.length = mate.length;
                    placeholder.extend = mate.extend; placeholder.versions = structuredClone(mate.versions);
                    if (mate.splice) placeholder.splice = {...mate.splice};
                }
                video.link = audio.id; audio.link = video.id;
            }
            position += count;
        }
    }
    // The new parts of one range are one video: each continues the part drawn before it.
    const drawn = trackBy(next, 'v1').clips.filter(clip => clip.part === part).sort((a, b) => a.start - b.start);
    drawn.forEach((clip, index) => { if (index && !clip.source && end(drawn[index - 1]) === clip.start) clip.follows = drawn[index - 1].id; });
    if (originals) {
        const clips = next.tracks.slice(0, 2).map(t => ({...t, clips: t.clips.filter(c => c.part === part)}));
        next.replacements = [...(next.replacements || []), {part, originals: structuredClone(originals), clips: structuredClone(clips), rolledBack: false}];
    }
    return finish(next);
}
export function extendClip(record, id, frames, {maxSeconds = RULES.maxSeconds, maxFollowSeconds = maxSeconds} = {}) {
    if (speedEdited(find(record, id))) { check(whole(frames, 1), 'range'); return trim(record, id, 'end', end(find(record, id)) + frames); }
    check(whole(frames, 1), 'range'); const selected = pair(record, id); check(selected.length, 'missing'); busy(selected);
    const clip = selected[0]; check(clip.extend && clip.source && trackOf(record, clip.id).id === 'v1', 'unsupported');
    // frames is total growth beyond the current visible edge. Restore retained
    // media immediately, then plan only the part beyond the actual source end.
    const spare = Math.min(...selected.map(member => member.length - member.in - member.frames));
    const reveal = Math.min(spare, frames);
    if (frames <= spare) return trim(record, id, 'end', end(clip) + reveal);
    // Parts that continue this one are at least followSeconds long, so what is added is raised to a length they can fill.
    const count = continuedSeconds((frames - spare) / FPS, maxFollowSeconds) * FPS;
    // One shift measured from the original visible edge, including restored media.
    const room = copy(record); makeRightEdgeRoom(room, id, reveal + count, {generating: true});
    const restored = trim(finish(room), id, 'end', end(clip) + reveal);
    const start = end(find(restored, clip.id));
    // A continuation adds a new pair; crossing sound on either lane is not a
    // replacement. Plan in an empty record, then put the sound in the free lane.
    const next = copy(restored), sound = sourceAudioTrack(next, start, count);
    const parts = rangeParts(restored, {start, frames: count, continues: true, maxSeconds, maxFollowSeconds});
    const planned = planRange({...emptyProject(restored), serial: next.serial},
        {start, frames: count, parts, target: 'both', maxSeconds: maxFollowSeconds, maxFollowSeconds});
    // Pulled out of this part's end: the new parts continue it. They have no description of their own until one
    // is written, and are generated with the one of the part before them.
    trackBy(next, 'v1').clips.push(...planned.tracks[0].clips.map(added => added.start === start ? {...added, follows: clip.id} : added));
    sound.clips.push(...planned.tracks[1].clips);
    next.serial = planned.serial;
    return finish(next);
}
function generationClips(record, ids) {
    check(Array.isArray(ids) && ids.length > 0 && new Set(ids).size === ids.length, 'range');
    const selected = linkedIds(record, ids);
    check(ids.every(id => find(record, id)), 'missing');
    const clips = trackBy(record, 'v1').clips.filter(clip => selected.has(clip.id));
    check(clips.length > 0 && [...selected].every(id => clips.some(clip => clip.id === id || clip.link === id)), 'unsupported');
    check(clips.every(clip => clip.pending !== null), 'range');
    check(clips.every((clip, i) => i === 0 || end(clips[i - 1]) === clip.start), 'range');
    return clips;
}
const netFrames = (frames, continues) => continues ? Math.max(34, Math.ceil(frames / RULES.step) * RULES.step)
    : Math.max(39, RULES.first + Math.ceil((frames - RULES.first) / RULES.step) * RULES.step);
function generationPlan(record, clips) {
    const replacing = clips.some(clip => clip.source), videos = [];
    // Each run of parts that continue one another is one video and keeps its own length.
    for (const clip of clips) { if (videos.length && continuing(clip, videos.at(-1).at(-1))) videos.at(-1).push(clip); else videos.push([clip]); }
    return videos.flatMap(video => videoPlan(record, video, replacing));
}
function videoPlan(record, clips, replacing) {
    const first = clips[0], before = trackBy(record, 'v1').clips.find(clip => continuing(first, clip) && (chainSource(clip) || wholeEnd(clip)));
    // Revealing a predecessor's hidden tail reduces the new visible range.
    const credit = clips.every(clip => clip.target_frames !== undefined && !clip.source)
        ? clips.reduce((sum, clip) => sum + clip.target_frames - clip.frames, 0) : 0;
    let ideal = 0, real = credit;
    return clips.map((clip, index) => {
        const target_frames = clip.target_frames ?? clip.frames; ideal += target_frames;
        // Existing playback bounds and fill's visible length are invariant.
        // A replacement may therefore need one more grid step than a draft.
        const previous = index ? (continuing(clip, clips[index - 1]) ? clips[index - 1] : null) : before ?? null;
        const continues = !!previous;
        const net_frames = netFrames(Math.max(ideal - real, replacing ? clip.frames : 0), continues); real += net_frames;
        const frames = replacing ? clip.frames : index === clips.length - 1 ? ideal - (real - net_frames) : net_frames;
        check(frames >= 1, 'range');
        return {clip, before: previous, target_frames, net_frames,
            request_frames: net_frames + (continues ? RULES.overlap : 0), frames};
    });
}
export function prepare(record, ids) {
    const clips = generationClips(record, ids); busy(clips);
    const next = copy(record);
    const replacing = clips.some(clip => clip.source);
    if (!replacing && clips.every(clip => clip.target_frames === undefined)) {
        const before = trackBy(next, 'v1').clips.find(clip => continuing(clips[0], clip) && clip.source && clip.extend);
        if (before) {
            const members = pair(next, before.id);
            const spare = Math.min(...members.map(clip => clip.length - clip.in - clip.frames));
            const first = find(next, clips[0].id);
            // Leave at least one visible frame in every planned segment.
            const credit = spare > 0 && spare < first.frames ? spare : 0;
            if (credit) {
                busy(members);
                for (const clip of members) clip.frames += credit;
                for (const old of clips) for (const clip of pair(next, old.id)) clip.target_frames = old.frames;
                for (const clip of pair(next, first.id)) { clip.start += credit; clip.frames -= credit; clip.length = clip.frames; }
            }
        }
    }
    const selected = generationClips(finish(next), ids), plan = generationPlan(next, selected);
    let position = selected[0].start;
    for (const row of plan) {
        const members = pair(next, row.clip.id);
        // While replacing existing media, retain its visible bounds until fill.
        for (const clip of members) {
            clip.target_frames = row.target_frames;
            if (!replacing) { clip.start = position; clip.frames = row.frames; clip.length = row.frames; }
        }
        position += row.frames;
    }
    return finish(next);
}
// The finished footage each video of a generation request continues, as source keys; null for one that starts fresh.
export function generationBefore(record, ids) {
    const clips = generationClips(record, ids);
    return generationPlan(record, clips).filter(row => !row.before || !clips.some(clip => clip.id === row.before.id))
        .map(row => row.before ? continuationKey(row.before) : null);
}
// An imported video is continued from the copy the editor plays, which is upright and at 24 fps.
function continuedPath(source, path) {
    if (source.kind !== 'import') return sourceData({kind: 'generated', id: path}).id;
    check(path === `FreeVideo/.editor/imports/${source.id}/preview.mp4`, 'source');
    return path;
}
export function generationRequest(record, ids, {text, paths = {}, queue = false} = {}) {
    check(object(paths), 'source');
    if (text !== undefined) check(validText(text), 'text');
    const clips = generationClips(record, ids), rows = generationPlan(record, clips), plan = {}, blocks = [];
    for (const row of rows) {
        let after = null;
        if (row.before) {
            if (clips.some(clip => clip.id === row.before.id)) after = row.before.id;
            else if (queue && chainSource(row.before)) after = {chain: row.before.job, block: row.before.id};
            else { const path = paths[continuationKey(row.before)]; check(typeof path === 'string' && path.length > 0, 'source', 'missing source path'); after = {work: continuedPath(row.before.source, path)}; }
        }
        blocks.push({id: row.clip.id, part: row.clip.part ?? row.clip.id, after, before: null, text: text ?? row.clip.text, frames: row.request_frames});
        plan[row.clip.id] = {target_frames: row.target_frames, net_frames: row.net_frames, request_frames: row.request_frames, audio: row.clip.link};
    }
    return frozen({blocks, plan});
}
// Whether a part made before a request is a short continuing part: what the engine kept of it is its new frames.
const madeShort = clip => (clip.source ? clip.length : clip.frames) + RULES.overlap < FULL_PART_FRAMES;
// The parts the finished or queued part `clip` was continued through on the timeline, itself included, and whether
// a continuing part among them is short.
function madeLineage(record, clip) {
    let parts = 1, short = false;
    for (let before = partBefore(record, clip), seen = new Set([clip.id]); before && !seen.has(before.id); before = partBefore(record, before)) {
        short = short || madeShort(clip); parts += 1; seen.add(before.id); clip = before;
    }
    return {parts, short};
}
// What the engine refuses in a generation request (chain_runner checks the same before anything runs): a continuing
// part shorter than followSeconds, a video continued through more than maxParts parts, or one with a short
// continuing part through more than partsLimit allows. Parts made before the request count as the timeline shows
// them. Returns {code, parts, limit} for the first problem, or null.
export function partsProblem(record, ids, {firstFrame = record.settings?.first_frame_reference === true} = {}) {
    const rows = generationPlan(record, generationClips(record, ids));
    if (rows.some(row => row.before && row.request_frames < MIN_PART_FRAMES)) return {code: 'chain_part_too_short', parts: null, limit: RULES.followSeconds};
    const lineage = new Map();
    for (const row of rows) {
        const previous = !row.before ? {parts: 0, short: false} : lineage.get(row.before.id) ?? madeLineage(record, row.before);
        lineage.set(row.clip.id, {parts: previous.parts + 1, short: previous.short || !!row.before && row.request_frames < FULL_PART_FRAMES});
    }
    const values = [...lineage.values()], parts = Math.max(...values.map(value => value.parts));
    if (parts > RULES.maxParts) return {code: 'chain_too_long', parts, limit: RULES.maxParts};
    const limit = partsLimit(true, firstFrame), short = Math.max(0, ...values.filter(value => value.short).map(value => value.parts));
    return short > limit ? {code: 'chain_short_parts', parts: short, limit} : null;
}
// Sets the continuation draft `id` (a planned part that continues the one before it) to `seconds` whole seconds,
// keeping its start; the drafts that continue it move with its end. Refused with 'range' for a length a continuing
// part cannot have on this device or where there is no room, and with the engine's code (partsProblem) where the
// new length would make the video one the engine refuses.
export function setContinuationSeconds(record, id, seconds, {maxSeconds = RULES.maxSeconds, maxFollowSeconds = maxSeconds, firstFrame} = {}) {
    const members = pair(record, id); check(members.length, 'missing'); busy(members);
    check(members.every(clip => clip.pending && !clip.source), 'unsupported');
    const video = members.find(clip => trackOf(record, clip.id).id === 'v1');
    check(!!video && !!partBefore(record, video), 'unsupported');
    check(Number.isInteger(seconds) && seconds >= followLeast(maxFollowSeconds)
        && seconds <= Math.floor(Math.min(RULES.maxSeconds, maxFollowSeconds)), 'range');
    const delta = seconds * FPS - video.frames;
    if (!delta) return record;
    const run = [video];
    for (let next; (next = trackBy(record, 'v1').clips.find(clip => clip.pending && !clip.source && continuing(clip, run.at(-1)))); ) run.push(next);
    const moving = run.flatMap(clip => pair(record, clip.id)); busy(moving);
    const moved = new Set(moving.map(clip => clip.id));
    let next = copy(record); detachAttempt(next, [...moved]);
    for (const clip of moving) {
        const changed = find(next, clip.id);
        if (pair(record, video.id).some(member => member.id === clip.id)) { changed.frames = changed.length = seconds * FPS; clearPlan(changed); }
        else { changed.start += delta; clearPlan(changed); }
        // Nothing else on the track may be under the part's new span.
        check(!trackOf(next, clip.id).clips.some(other => !moved.has(other.id) && overlaps(other, changed.start, changed.frames)), 'range');
    }
    next = finish(next);
    // The drafts it continues are generated with it, so the whole video is checked.
    const earlier = [];
    for (let before = partBefore(record, video); before && before.pending === 'draft' && !before.source && !earlier.includes(before); before = partBefore(record, before)) earlier.unshift(before);
    const ids = [...earlier, ...run].map(clip => clip.id), options = firstFrame === undefined ? {} : {firstFrame};
    const problem = partsProblem(next, ids, options);
    if (problem && problem.code !== partsProblem(record, ids, options)?.code) throw Object.assign(new EditError(problem.code), {parts: problem.parts, limit: problem.limit});
    return next;
}
function update(record, ids, change) {
    const next = copy(record), selected = linkedIds(next, ids);
    for (const clip of all(next)) if (selected.has(clip.id)) change(clip);
    return finish(next);
}
// text is one description for every part, or one per part keyed by the part's picture.
export function startAttempt(record, ids, text, {by = 'user'} = {}) {
    const textOf = clip => typeof text === 'string' ? text : text?.[clip.id] ?? text?.[clip.link];
    const next = copy(record), selected = linkedIds(next, ids), parts = new Map();
    check(TEXT_BY.includes(by) && all(next).every(clip => !selected.has(clip.id) || validText(textOf(clip))), 'text');
    busy(all(next).filter(clip => selected.has(clip.id)));
    // Parts that continue one another stay one video; ranges drawn apart stay apart, however many are started together.
    let part = null, last = null;
    for (const clip of trackBy(record, 'v1').clips.filter(clip => selected.has(clip.id)).sort((a, b) => a.start - b.start)) {
        if (!last || !continuing(clip, last)) part = freshId(next);
        parts.set(clip.id, part); if (clip.link) parts.set(clip.link, part); last = clip;
    }
    const other = () => part ??= freshId(next);
    for (const clip of all(next)) if (selected.has(clip.id)) { written(clip, textOf(clip), by); clip.part = parts.get(clip.id) ?? other(); clip.pending = 'draft'; clip.reason = clip.job = null; }
    return finish(next);
}
export function assignJob(record, ids, job, {prepared = record} = {}) {
    check(typeof job === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(job), 'job');
    return update(record, ids, clip => {
        const expected = find(prepared, clip.id);
        if (clip.pending === 'draft' && clip.job === null && clip.target_frames !== undefined
            && expected && expected.part === clip.part && expected.target_frames === clip.target_frames) {
            clip.job = job; clip.pending = 'queued'; clip.reason = null;
        }
    });
}
export function fill(record, id, {source, frames, width, height, skip_frames = 0, skip_audio_samples = 0, job = null}) {
    if (!attemptClip(record, id) || attemptClip(record, id).job !== job) return parse(record);
    const wasRolledBack = record.replacements?.find(tx => tx.rolledBack && tx.clips.some(t => t.clips.some(c => c.id === id)));
    const active = activate(record, id);
    source = sourceData(source); check(whole(frames, 1) && whole(skip_frames) && whole(skip_audio_samples, 0, Number.MAX_SAFE_INTEGER), 'range');
    check(pair(active, id).every(clip => clip.job === job), 'job');
    check(pair(active, id).every(clip => skip_frames + clip.frames <= frames), 'range');
    const sized = copy(active);
    if (pair(active, id).some(clip => trackBy(active, 'v1').clips.includes(clip))) followFirstSize(sized, width, height);
    const next = update(sized, [id], clip => {
        const take = numberTakes(clip) + 1;
        remember(clip, source); clip.source = source; clip.in = skip_frames; clip.length = frames; clip.take = take;
        if (clip.text) clip.made_text = clip.text; else delete clip.made_text;
        clip.pending = clip.reason = clip.job = null; clip.extend = true;
        delete clip.splice;
        if (skip_frames || skip_audio_samples) clip.splice = {frames: skip_frames, audio_samples: skip_audio_samples};
        clearPlan(clip);
    });
    const transaction = next.replacements?.find(tx => tx.clips.some(t => t.clips.some(c => c.id === id)));
    if (!transaction) return next;
    const visible = wasRolledBack ? find(record, wasRolledBack.originals[0].clips[0].id)
        : transaction.clips.flatMap(t => t.clips).find(c => c.pending);
    // Publish all replacement blocks together. Until then playback/export keep
    // the original media, even after an earlier block has finished.
    return rollback(next, id, visible.pending, visible.reason);
}
export function fail(record, id, reason, job = null) {
    check(typeof reason === 'string' && /^[a-z_]{1,80}$/.test(reason), 'reason');
    if (!attemptClip(record, id)?.pending || attemptClip(record, id).job !== job) return parse(record);
    const next = update(activate(record, id), [id], clip => { if (clip.pending !== null && clip.job === job) { clip.pending = 'failed'; clip.reason = reason; } });
    return rollback(next, id, 'failed', reason);
}
export function settle(record, ids, pending = 'stopped') {
    check(['draft', 'queued', 'running', 'failed', 'stopped'].includes(pending), 'pending');
    let active = record, selected = [...ids];
    for (const tx of record.replacements || []) if (tx.rolledBack && tx.originals.some(t => t.clips.some(c => ids.includes(c.id))) && ['queued', 'draft'].includes(pending)) selected.push(...tx.clips.flatMap(t => t.clips.filter(c => c.pending).map(c => c.id)));
    for (const id of selected) active = activate(active, id);
    let next = update(active, [...new Set(selected)], clip => { if (clip.pending !== null) { clip.pending = pending; clip.reason = null; if (pending === 'draft') clip.job = null; } });
    if (['stopped', 'failed', 'draft', 'queued', 'running'].includes(pending)) for (const id of selected) next = rollback(next, id, pending);
    return next;
}
export function dropPlanned(record, ids) {
    const selected = linkedIds(record, ids), next = copy(record);
    busy(all(record).filter(clip => selected.has(clip.id) && !clip.source));
    for (const track of next.tracks) track.clips = track.clips.filter(clip => !selected.has(clip.id) || clip.source || (clip.link && find(record, clip.link).source));
    return finish(next);
}
export function restoreVersion(record, id, index, {by = 'user'} = {}) {
    check(whole(index, 0, 49) && TEXT_BY.includes(by), 'range'); const clips = pair(record, id); check(clips.length, 'missing'); busy(clips);
    const next = copy(record);
    detachAttempt(next, clips.map(c => c.id));
    for (const old of clips) {
        const clip = find(next, old.id); numberTakes(clip);
        const version = clip.versions[index]; check(version && speedRange({...clip, in: version.in, length: version.length}), 'range');
        remember(clip, version); clip.source = {kind: version.kind, id: version.id}; clip.in = version.in; clip.length = version.length;
        delete clip.take; delete clip.made_text; madeFields(version, clip); touchedBy(clip, by);
        // Placement belongs to this timeline part, including when its media changes.
        clip.pending = clip.reason = clip.job = null; clip.extend = version.kind !== 'import' || version.length >= TAIL_FRAMES; clearPlan(clip);
        delete clip.splice; if (version.splice) clip.splice = version.splice;
    }
    return finish(next);
}
connectSpeedOperations({copy, finish, detachAttempt, trackOf, freshId, splitAudioFields,
    makeRoom: (record, track, start, frames, excluded) => applyRoom(record, start, frames, {tracks: [].concat(track), excluded: [...excluded]}),
});
