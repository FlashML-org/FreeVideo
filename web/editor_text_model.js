// Text records use the ordinary clip envelope so timeline gestures/history stay shared.
// Media lanes keep their on-disk order; displayTracks puts overlays above them.
import * as model from './editor_model.js';
export const TEXT_LIMIT = 8000;
export const TEXT_DEFAULTS = Object.freeze({font: 'sans', size: 64 / 1080, color: '#ffffff', backing: 'outline', bold: true, align: 'center', x: .5, y: .5, fade: false});
export const SUBTITLE_DEFAULTS = Object.freeze({font: 'sans', size: 44 / 1080, color: '#ffffff', backing: 'outline', position: 'bottom', margin: .08});
export const isText = clip => ['text', 'subtitle'].includes(clip?.text_kind);
export const isTextTrack = track => ['text', 'subtitle'].includes(track?.kind);
export const hasTextData = record => record?.subtitle_style !== undefined || record?.tracks?.some(isTextTrack);
// The recording sub-track (a3) is drawn directly under a1.
const media = rows => { const sub = rows.find(t => t.id === 'a3'); return sub ? rows.filter(t => t !== sub).flatMap(t => t.id === 'a1' ? [t, sub] : [t]) : rows; };
export const displayTracks = record => [...record.tracks.filter(t => t.kind === 'subtitle'), ...record.tracks.filter(t => t.kind === 'text'), ...media(record.tracks.filter(t => !isTextTrack(t)))];
export const defaults = kind => kind === 'subtitle' ? SUBTITLE_DEFAULTS : TEXT_DEFAULTS;
export const styleOf = (record, clip) => ({...defaults(clip.text_kind), ...(clip.text_kind === 'subtitle' ? record.subtitle_style : clip.text_style)});
const check = (ok, code = 'text') => { if (!ok) throw new model.EditError(code); };
const object = v => !!v && typeof v === 'object' && !Array.isArray(v);
const whole = (v, low = 0) => Number.isSafeInteger(v) && v >= low && v <= 1e7;
export function cleanStyle(raw = {}, kind = 'text') {
    check(object(raw)); const base = defaults(kind), result = {};
    for (const [key, value] of Object.entries(raw)) {
        check(Object.hasOwn(base, key));
        if (key === 'font') check(['sans', 'serif', 'kai', 'mono'].includes(value));
        else if (key === 'size') check(typeof value === 'number' && Number.isFinite(value) && value >= 16 / 1080 && value <= 200 / 1080);
        else if (key === 'color') check(typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value));
        else if (key === 'backing') check(['none', 'outline', 'box'].includes(value));
        else if (key === 'align') check(['left', 'center', 'right'].includes(value));
        else if (key === 'position') check(['top', 'bottom'].includes(value));
        else if (key === 'bold' || key === 'fade') check(typeof value === 'boolean');
        else check(typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1);
        const normalized = key === 'color' ? value.toLowerCase() : value;
        if (normalized !== base[key]) result[key] = normalized;
    }
    return result;
}
const freeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; };
export function readTextProject(raw, parseMedia) {
    check(Array.isArray(raw.tracks) && raw.tracks.length <= 9, 'track');
    const media = {...raw, tracks: raw.tracks.filter(t => !isTextTrack(t))}; delete media.subtitle_style;
    const result = structuredClone(parseMedia(media)), ids = new Set(result.tracks.flatMap(t => t.clips.map(c => c.id))), seen = new Set();
    for (const track of raw.tracks.filter(isTextTrack)) {
        check(!seen.has(track.kind) && track.id === (track.kind === 'text' ? 't1' : 's1'), 'track'); seen.add(track.kind);
        check(Array.isArray(track.clips) && track.clips.length <= 500, 'track');
        const row = {id: track.id, kind: track.kind, clips: []};
        for (const flag of ['hidden', 'locked']) { check(track[flag] === undefined || typeof track[flag] === 'boolean'); if (track[flag]) row[flag] = true; }
        let end = 0;
        for (const c of track.clips) {
            check(object(c) && typeof c.id === 'string' && /^c[1-9]\d{0,8}$/.test(c.id) && !ids.has(c.id) && Number(c.id.slice(1)) <= raw.serial, 'id'); ids.add(c.id);
            check(whole(c.start) && whole(c.frames, 1) && whole(c.start + c.frames), 'range'); check(c.start >= end, 'overlap'); end = c.start + c.frames;
            check(c.text_kind === undefined || c.text_kind === track.kind);
            check(typeof c.text === 'string' && Array.from(c.text).length <= TEXT_LIMIT && !/[\u0000-\u0008\u000b-\u001f\u007f\ud800-\udfff]/u.test(c.text));
            check(['source','pending','link','part','job','reason'].every(k => c[k] == null) && (c.extend === undefined || c.extend === false) && (c.versions === undefined || (Array.isArray(c.versions) && c.versions.length === 0)));
            check((c.in === undefined || c.in === 0) && (c.length === undefined || c.length === c.frames));
            check(!['volume', 'fit', 'splice', 'target_frames', 'text_by', 'ref', 'note', 'take', 'made_text'].some(k => c[k] !== undefined));
            const clip = {id: c.id, start: c.start, frames: c.frames, in: 0, length: c.frames, source: null, link: null,
                text: c.text, part: null, pending: null, reason: null, job: null, extend: false, versions: [], text_kind: track.kind};
            check(track.kind !== 'subtitle' || c.text_style === undefined);
            if (track.kind === 'text') { const style = cleanStyle(c.text_style); if (Object.keys(style).length) clip.text_style = style; }
            row.clips.push(clip);
        }
        if (row.clips.length) result.tracks.push(row);
    }
    const style = cleanStyle(raw.subtitle_style, 'subtitle');
    if (Object.keys(style).length && result.tracks.some(t => t.kind === 'subtitle')) result.subtitle_style = style;
    result.tracks.sort((a,b) => ['v1','a1','a2','a3','a4','a5','a6','t1','s1'].indexOf(a.id) - ['v1','a1','a2','a3','a4','a5','a6','t1','s1'].indexOf(b.id));
    return freeze(result);
}
export function assertUnlocked(record, ids) {
    check(!record.tracks.some(t => t.locked && t.clips.some(c => ids.includes(c.id))), 'locked');
}
// Timeline moves keep their original lanes, but text cannot land over a locked overlay lane.
export const textDropLocked = (record, clips, trackId) => clips.some(isText)
    && record.tracks.some(track => track.id === trackId && isTextTrack(track) && track.locked);
export function addText(record, {kind = 'text', at = 0, text = '', style, frames = kind === 'subtitle' ? 48 : 72} = {}) {
    check(['text', 'subtitle'].includes(kind));
    const next = model.serialize(record); let row = next.tracks.find(t => t.kind === kind);
    check(!row?.locked, 'locked');
    if (!row) { row = {id: kind === 'text' ? 't1' : 's1', kind, clips: []}; next.tracks.push(row); }
    row.clips.push({id: `c${++next.serial}`, start: at, frames, text, ...(style && kind === 'text' ? {text_style: style} : {})});
    row.clips.sort((a,b) => a.start-b.start); return model.parse(next);
}
export function changeText(record, id, changes) {
    assertUnlocked(record, [id]); const next = model.serialize(record), c = model.find(next, id); check(isText(c), 'missing');
    check(Object.keys(changes).every(k => ['text','start','frames','text_style'].includes(k)));
    Object.assign(c, changes); c.length = c.frames;
    next.tracks.forEach(t => t.clips.sort((a,b) => a.start-b.start)); return model.parse(next);
}
export function changeSubtitleStyle(record, changes) {
    assertUnlocked(record, record.tracks.filter(t => t.kind === 'subtitle').flatMap(t => t.clips.map(c => c.id)));
    return model.parse({...record, subtitle_style: cleanStyle({...record.subtitle_style, ...changes}, 'subtitle')});
}
export function trimText(record, id, edge, frame) {
    const clip = model.find(record,id), end = clip.start + clip.frames, minimum = Math.min(24, clip.frames);
    const start = edge === 'start' ? Math.max(0, Math.min(end - minimum, frame)) : clip.start;
    const stop = edge === 'end' ? Math.max(start + minimum, frame) : end;
    return changeText(record, id, {start, frames: stop-start});
}
export function setTextTrack(record, id, flag, value) {
    check(['hidden','locked'].includes(flag) && typeof value === 'boolean');
    const next = model.serialize(record), track = next.tracks.find(t => t.id === id); check(isTextTrack(track), 'track');
    track[flag] = value; return model.parse(next);
}
export function activeText(record, frame) {
    return record.tracks.filter(t => isTextTrack(t) && !t.hidden).sort((a,b) => (a.kind === 'text' ? -1 : 1) - (b.kind === 'text' ? -1 : 1))
        .flatMap(t => t.clips.filter(c => c.start <= frame && c.start+c.frames > frame).map(c => ({...c, style: styleOf(record,c), opacity: c.text_kind === 'text' && styleOf(record,c).fade ? Math.min(1,(frame-c.start+.5)/7.2,(c.start+c.frames-frame-.5)/7.2) : 1})));
}
// A subtitle file's bytes as text: UTF-8, UTF-16 with its byte-order mark, or else the older local encoding
// (`legacy`, e.g. GBK for Chinese), so such a file never turns into replacement characters.
export function decodeSRT(bytes, legacy = 'windows-1252') {
    const view = new Uint8Array(bytes);
    if (view[0] === 0xff && view[1] === 0xfe) return new TextDecoder('utf-16le').decode(view);
    if (view[0] === 0xfe && view[1] === 0xff) return new TextDecoder('utf-16be').decode(view);
    try { return new TextDecoder('utf-8', {fatal: true}).decode(view); } catch { return new TextDecoder(legacy).decode(view); }
}
export function parseSRT(text, result = {}) {
    check(typeof text === 'string' && text.length <= 4*1024*1024, 'srt');
    const blocks = text.replace(/^\ufeff/, '').replace(/\r\n?/g,'\n').trim().split(/\n\s*\n/);
    const entries = []; let skipped = 0, trimmed = 0;
    for (const block of blocks) {
        let entry;
        try {
            const lines = block.split('\n'); if (/^\d+$/.test(lines[0])) lines.shift();
            const match = /^(\d{2,3}):([0-5]\d):([0-5]\d)[,.](\d{3}) --> (\d{2,3}):([0-5]\d):([0-5]\d)[,.](\d{3})$/.exec(lines.shift()); check(match && lines.length, 'srt');
            const time = i => Math.round((Number(match[i])*3600+Number(match[i+1])*60+Number(match[i+2])+Number(match[i+3])/1000)*24);
            const start = time(1), stop = time(5), content = lines.join('\n');
            check(stop > start && stop <= 1e7 && Array.from(content).length <= TEXT_LIMIT && !/[\u0000-\u0008\u000b-\u001f\u007f\ud800-\udfff]/u.test(content), 'srt');
            entry = {at:start, frames:stop-start, text:content, kind:'subtitle'};
        } catch { skipped++; continue; }
        // Keep readable cues in file order, shortening or removing earlier overlaps.
        while (entries.length && entries.at(-1).at + entries.at(-1).frames > entry.at) {
            const previous = entries.at(-1); previous.frames = entry.at - previous.at; trimmed++;
            if (previous.frames > 0) break;
            entries.pop();
        }
        entries.push(entry);
    }
    if (skipped) console.warn(`FreeVideo SRT import skipped ${skipped} unreadable blocks.`);
    if (trimmed) console.warn(`FreeVideo SRT import trimmed ${trimmed} overlapping subtitles.`);
    check(entries.length > 0, 'srt');
    result.skipped = skipped; result.truncated = entries.length > 500;
    if (result.truncated) console.warn('FreeVideo SRT import kept only the first 500 subtitles.');
    return entries.slice(0, 500);
}
// An import replaces every subtitle already there, in one edit that one undo reverses.
export function importSRT(record, text) {
    const entries = parseSRT(text), next = model.serialize(record);
    let row = next.tracks.find(t => t.kind === 'subtitle'); check(!row?.locked, 'locked');
    if (!row) { row = {id: 's1', kind: 'subtitle', clips: []}; next.tracks.push(row); }
    row.clips = entries.map(entry => ({id: `c${++next.serial}`, start: entry.at, frames: entry.frames, text: entry.text}));
    return model.parse(next);
}
// Writes subtitle lines in one edit that one undo reverses (auto subtitles, an agent).
// cues: [{start, frames, text}] in timeline frames; a bilingual line is one cue of two
// lines (text with one "\n"). replace: true swaps out the whole subtitle track; otherwise
// the lines join it and any overlap with a line already there refuses the whole call.
// Every line takes the track's one style (changeSubtitleStyle); a line placed on its own is a text part.
export function addSubtitles(record, cues, {replace = false} = {}) {
    check(Array.isArray(cues) && cues.length >= 1 && cues.length <= 500 && typeof replace === 'boolean', 'subtitles');
    const lines = cues.map(cue => {
        check(object(cue) && Object.keys(cue).every(k => ['start', 'frames', 'text'].includes(k)), 'subtitles');
        check(whole(cue.start) && whole(cue.frames, 1) && whole(cue.start + cue.frames), 'range');
        check(typeof cue.text === 'string' && cue.text.trim() !== '' && cue.text.split('\n').length <= 2);
        return {start: cue.start, frames: cue.frames, text: cue.text};
    }).sort((a, b) => a.start - b.start);
    const next = model.serialize(record);
    let row = next.tracks.find(t => t.kind === 'subtitle'); check(!row?.locked, 'locked');
    if (!row) { row = {id: 's1', kind: 'subtitle', clips: []}; next.tracks.push(row); }
    const kept = replace ? [] : row.clips, all = [...kept, ...lines.map(line => ({id: `c${++next.serial}`, ...line}))].sort((a, b) => a.start - b.start);
    for (let i = 1; i < all.length; i++) check(all[i].start >= all[i - 1].start + all[i - 1].frames, 'overlap');
    row.clips = all;
    return model.parse(next);
}
