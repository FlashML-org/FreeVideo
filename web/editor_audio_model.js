// Audio edits and sample envelopes shared by the panel, tracks and preview.
import * as model from './editor_model.js';
const fail = code => { throw new model.EditError(code); };
const all = record => record.tracks.flatMap(track => track.clips);
export const trackOf = (record, id) => record.tracks.find(track => track.clips.some(clip => clip.id === id));
const end = clip => clip.start + clip.frames;
const busy = clip => ['queued', 'running'].includes(clip.pending);
const whole = value => Number.isSafeInteger(value) && value >= 0;
export function audioClipFields(raw, kind) {
    const fields = {};
    for (const name of ['fade_in', 'fade_out', 'crossfade']) if (raw[name] !== undefined) {
        if (kind !== 'audio' || !whole(raw[name]) || raw[name] > 120) fail('audio');
        fields[name] = raw[name];
    }
    if (raw.detached_from !== undefined) {
        if (typeof raw.detached_from !== 'string' || !/^c[1-9]\d{0,8}$/.test(raw.detached_from) || raw.detached_from === raw.id || raw.link) fail('link');
        fields.detached_from = raw.detached_from;
    }
    // A music piece's preset (what it was made with, prefilled when it is made again) and whether it is
    // lowered while the first sound track is heard.
    if (raw.music !== undefined) {
        const value = raw.music;
        if (kind !== 'audio' || !value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join() !== 'mood,style'
            || ![value.style, value.mood].every(name => typeof name === 'string' && /^[a-z]{1,20}$/.test(name))) fail('audio');
        fields.music = {style: value.style, mood: value.mood};
    }
    if (raw.duck !== undefined) {
        if (kind !== 'audio' || typeof raw.duck !== 'boolean') fail('audio');
        fields.duck = raw.duck;
    }
    if (raw.stem_of !== undefined) {
        if (kind !== 'audio' || typeof raw.stem_of !== 'string' || !/^c[1-9]\d{0,8}$/.test(raw.stem_of) || raw.stem_of === raw.id || raw.link) fail('stems');
        fields.stem_of = raw.stem_of;
    }
    if (raw.stem_volume !== undefined) {
        if (kind !== 'audio' || typeof raw.stem_volume !== 'number' || !(raw.stem_volume >= 0 && raw.stem_volume <= 2)) fail('stems');
        fields.stem_volume = raw.stem_volume;
    }
    return fields;
}
export function audioTrackFields(raw) {
    const fields = {};
    for (const name of ['muted', 'solo', 'locked']) if (raw[name] !== undefined) {
        if (typeof raw[name] !== 'boolean') fail('track');
        fields[name] = raw[name];
    }
    return fields;
}
export function normalizeAudioEdits(record) {
    // A clip silenced for its separated sound speaks again once no stem clip refers to it.
    const stemmed = new Set(record.tracks.flatMap(track => track.clips).map(clip => clip.stem_of));
    for (const track of record.tracks) for (const clip of track.clips) {
        if (clip.stem_volume !== undefined && !stemmed.has(clip.id)) { clip.volume = clip.stem_volume; delete clip.stem_volume; }
    }
    for (const track of record.tracks) for (const clip of track.clips) {
        for (const name of ['fade_in', 'fade_out']) if (clip[name] !== undefined) clip[name] = Math.min(clip[name], Math.floor(clip.frames / 2));
    }
}
export function splitAudioFields(left, right) {
    delete left.fade_out; delete right.fade_in; delete right.crossfade;
    // A new object cannot restore the original pair after a separate split.
    delete right.detached_from;
}
export function assertLockedUnchanged(before, after) {
    for (const track of before.tracks) if (track.locked) {
        const next = after.tracks.find(row => row.id === track.id);
        if (!next || JSON.stringify(track.clips) !== JSON.stringify(next.clips)) fail('locked');
    }
}
// Content on a locked track cannot be selected. A pair with one locked half stays
// selectable through its free half: it can be looked at, and every edit is refused whole.
export function unlockedSelection(record, value) {
    if (!record.tracks.some(track => track.locked)) return value;
    if (value === null || !value || typeof value !== 'object') return value;
    if (value.kind === 'clip') {
        if (!Array.isArray(value.ids) || !value.ids.length) return value;
        const ids = value.ids.filter(id => !trackOf(record, id)?.locked);
        return ids.length ? {...value, ids} : null;
    }
    const ids = value.kind === 'gap' ? [value.track] : value.tracks;
    if (!Array.isArray(ids)) return value;
    return ids.some(id => record.tracks.some(track => track.id === id && track.locked)) ? null : value;
}
// Select all leaves out every pair that touches a locked track.
export function unlockedIds(record) {
    return record.tracks.flatMap(track => track.clips).filter(clip => model.pair(record, clip.id).every(item => !trackOf(record, item.id)?.locked)).map(clip => clip.id);
}
function editable(record, ids) {
    for (const id of ids) {
        const clip = model.find(record, id);
        if (!clip) fail('missing');
        if (trackOf(record, id).locked) fail('locked');
        if (busy(clip)) fail('busy');
    }
}
export function setTrackFlag(record, id, name, value) {
    if (!['muted', 'solo', 'locked'].includes(name) || typeof value !== 'boolean') fail('track');
    const next = model.serialize(record), track = next.tracks.find(row => row.id === id);
    if (!track) fail('track');
    if (value) track[name] = true; else delete track[name];
    return model.parse(next);
}
export function detachAudio(record, id) {
    const pair = model.pair(record, id);
    if (pair.length !== 2) fail('link');
    editable(record, pair.map(clip => clip.id));
    const next = model.serialize(record);
    for (const old of pair) { const clip = model.find(next, old.id); clip.detached_from = clip.link; clip.link = null; }
    return model.parse(next);
}
// Fades stop at half of the shortest selected clip, so every selected clip gets the same value.
export const fadeCap = clips => Math.min(120, ...clips.map(clip => Math.floor(clip.frames / 2)));
export function setFades(record, ids, name, frames) {
    if (!['fade_in', 'fade_out'].includes(name) || !whole(frames)) fail('audio');
    editable(record, ids);
    const next = model.serialize(record), cap = fadeCap(ids.map(id => model.find(next, id)).filter(Boolean));
    for (const id of ids) {
        const clip = model.find(next, id);
        if (trackOf(next, id).kind !== 'audio' || !clip.source) fail('audio');
        const value = Math.min(cap, frames);
        if (value) clip[name] = value; else delete clip[name];
    }
    return model.parse(next);
}
// Lower during speech is a property of generated music only.
export const duckable = clip => !!clip?.music && !!clip.source;
export function setDuck(record, ids, on) {
    if (typeof on !== 'boolean' || !ids.length) fail('audio');
    editable(record, ids);
    const next = model.serialize(record);
    for (const id of ids) {
        const clip = model.find(next, id);
        if (trackOf(next, id)?.kind !== 'audio' || !duckable(clip)) fail('audio');
        clip.duck = on;
    }
    return model.parse(next);
}
export function setCrossfade(record, id, frames) {
    if (!whole(frames)) fail('audio');
    const next = model.serialize(record), track = trackOf(next, id), right = model.find(next, id);
    const left = track?.clips[track.clips.indexOf(right) - 1];
    if (track?.kind !== 'audio' || !left?.source || !right?.source || end(left) !== right.start) fail('audio');
    editable(record, [left.id, right.id]);
    const value = Math.min(crossfadeLimit(record, id).limit, frames);
    if (value) right.crossfade = value; else delete right.crossfade;
    return model.parse(next);
}
export function putAudio(record, payload, trackId, at, replaceId = null) {
    const track = record.tracks.find(row => row.id === trackId);
    if (!track || track.kind !== 'audio' || !payload.hasAudio || payload.hasVideo || !whole(at)) fail('audio');
    if (track.locked) fail('locked');
    const original = replaceId && model.find(record, replaceId);
    if (original && (trackOf(record, replaceId).id !== trackId || original.link)) fail('link');
    if (original) editable(record, [original.id]);
    const template = model.insertSource(model.emptyProject(), {...payload, at: 0}).tracks[1].clips[0];
    const next = model.serialize(record), row = next.tracks.find(item => item.id === trackId);
    const clip = {...template, ...(original?.detached_from ? {detached_from: original.detached_from} : {}), id: original?.id || `c${++next.serial}`, start: original?.start ?? at,
        frames: original ? Math.min(original.frames, template.frames) : template.frames};
    if (row.clips.some(item => item.id !== replaceId && item.start < end(clip) && end(item) > clip.start)) fail('overlap');
    row.clips = row.clips.filter(item => item.id !== replaceId); row.clips.push(clip); row.clips.sort((a, b) => a.start - b.start);
    return model.parse(next);
}
export const trackAudible = (track, tracks) => !track.muted && (!tracks.some(row => row.solo) || !!track.solo);
export const clipAudible = (clip, track, tracks) => trackAudible(track, tracks) && !(clip.speed_audio === 'mute' && ((clip.speed ?? 1) !== 1 || clip.reverse))
    && !tracks.some(row => row.kind === 'video' && row.muted && row.clips.some(video => video.id === clip.link));
const samples = (frames, rate, fps) => Math.round(frames * rate / fps);
const sourceStart = (clip, rate, fps) => clip.splice
    ? Math.round((clip.splice.audio_samples / 32000 + (clip.in - clip.splice.frames) / fps) * rate) : samples(clip.in, rate, fps);
export function crossfadeReads(track, rate = 48000, fps = 24) {
    const result = [];
    for (let index = 1; index < track.clips.length; index++) {
        const left = track.clips[index - 1], right = track.clips[index];
        if (!left.source || !right.source || end(left) !== right.start || !right.crossfade) continue;
        const count = samples(Math.min(right.crossfade, 120, left.frames, right.frames), rate, fps), pre = Math.floor(count / 2), post = count - pre;
        const extend = clip => (clip.speed ?? 1) === 1 && !clip.reverse;
        const leftTail = extend(left) ? Math.min(post, Math.max(0, samples(left.length, rate, fps) - sourceStart(left, rate, fps) - samples(left.frames, rate, fps))) : 0;
        const rightHead = extend(right) ? Math.min(pre, Math.max(0, sourceStart(right, rate, fps))) : 0;
        result.push({left: left.id, right: right.id, boundary: samples(right.start, rate, fps), pre, post,
            left_tail: leftTail, right_head: rightHead,
            left_read_start: sourceStart(left, rate, fps) + samples(left.frames, rate, fps),
            right_read_start: sourceStart(right, rate, fps) - rightHead});
    }
    return result;
}
// The longest crossfade whose two halves both come from the neighbours' own spare
// material. A side with no spare material at all (`bare`) does a plain fade instead.
export function crossfadeLimit(record, id, rate = 48000, fps = 24) {
    const track = trackOf(record, id), right = model.find(record, id), left = track?.clips[track.clips.indexOf(right) - 1];
    if (track?.kind !== 'audio' || !left?.source || !right?.source || end(left) !== right.start) fail('audio');
    const most = Math.min(120, left.frames, right.frames), plan = frames => crossfadeReads({clips: [left, {...right, crossfade: frames}]}, rate, fps)[0];
    const widest = plan(most), bare = {left: !widest.left_tail, right: !widest.right_head};
    const fits = frames => { const read = plan(frames); return (bare.left || read.left_tail >= read.post) && (bare.right || read.right_head >= read.pre); };
    let limit = most;
    while (limit > 1 && !fits(limit)) limit--;
    return {limit, bare};
}
const ramp = (position, length) => length <= 1 ? (position >= 0 ? 1 : 0) : Math.max(0, Math.min(1, position / (length - 1)));
export function fadeGain(clip, local, length, rate = 48000, fps = 24) {
    return (clip.fade_in ? ramp(local, samples(Math.min(120, Math.floor(clip.frames / 2), clip.fade_in), rate, fps)) : 1)
        * (clip.fade_out ? ramp(length - 1 - local, samples(Math.min(120, Math.floor(clip.frames / 2), clip.fade_out), rate, fps)) : 1);
}
// Planar samples already include clip volume. Extra reads contain raw samples.
export function applyTrackAudio(block, blockStart, track, tracks, {rate = 48000, fps = 24, extras = {}} = {}) {
    if (!Array.isArray(block) || !whole(blockStart) || !Number.isFinite(rate) || rate <= 0 || !Number.isFinite(fps) || fps <= 0) fail('audio');
    const output = block.map(channel => Float64Array.from(channel));
    const count = output[0]?.length || 0, added = output.map(() => new Float64Array(count));
    if (output.some(channel => channel.length !== count)) fail('audio');
    if (!trackAudible(track, tracks)) { output.forEach(channel => channel.fill(0)); return output; }
    const plans = crossfadeReads(track, rate, fps).map(plan => {
        const extra = extras[plan.right] || {};
        return {...plan, tail: Math.min(plan.left_tail, extra.left_tail?.[0]?.length || 0),
            head: Math.min(plan.right_head, extra.right_head?.[0]?.length || 0), extra};
    });
    for (const clip of track.clips) {
        const start = samples(clip.start, rate, fps), stop = samples(end(clip), rate, fps);
        const incoming = plans.find(plan => plan.right === clip.id), outgoing = plans.find(plan => plan.left === clip.id);
        const lo = start - (incoming?.head || 0), hi = stop + (outgoing?.tail || 0);
        const audible = clipAudible(clip, track, tracks);
        const gain = position => {
            let value = audible ? fadeGain(clip, position - lo, hi - lo, rate, fps) : 0;
            for (const [plan, right] of [[incoming, true], [outgoing, false]]) if (plan) {
                if (plan.head + plan.tail) {
                    const a = plan.boundary - plan.head, b = plan.boundary + plan.tail;
                    const weight = ramp(position - a, b - a);
                    value *= right ? weight : 1 - weight;
                } else value *= right ? ramp(position - start, plan.post) : ramp(stop - 1 - position, plan.pre);
            }
            return value;
        };
        for (let position = Math.max(blockStart, start); position < Math.min(blockStart + count, stop); position++) {
            const value = gain(position);
            for (const channel of output) channel[position - blockStart] *= value;
        }
        for (const [plan, name, begin, size] of [[incoming, 'right_head', lo, incoming?.head || 0], [outgoing, 'left_tail', stop, outgoing?.tail || 0]]) {
            if (!plan || !size) continue;
            const data = plan.extra[name], offset = name === 'right_head' ? (data[0].length - size) : 0;
            for (let position = Math.max(blockStart, begin); position < Math.min(blockStart + count, begin + size); position++) {
                const value = gain(position) * (clip.volume ?? 1);
                added.forEach((channel, index) => { channel[position - blockStart] += (data[index]?.[offset + position - begin] || 0) * value; });
            }
        }
    }
    output.forEach((channel, index) => { for (let i = 0; i < count; i++) channel[i] += added[index][i]; });
    return output;
}
// Lower during speech: a clip with duck true sits DUCK_DB lower wherever another audible clip has speech,
// with DUCK_RAMP_SECONDS ramps just outside each stretch. freevideo_engine/editor_audio.py has the same rules.
export const DUCK_DB = -8;
export const DUCK_RAMP_SECONDS = .3;
const changedSpeed = clip => (clip.speed ?? 1) !== 1 || !!clip.reverse;
// spans: Map or object of 'kind:id' → [[start, end] seconds]; ranges are timeline samples [first, last).
export function duckRanges(tracks, spans, rate = 48000, fps = 24) {
    const found = [], lookup = key => (spans instanceof Map ? spans.get(key) : spans?.[key]) || [];
    for (const track of tracks) {
        if (track.kind !== 'audio' || !trackAudible(track, tracks)) continue;
        for (const clip of track.clips) {
            if (!clip.source || clip.duck || (clip.volume ?? 1) === 0 || !clipAudible(clip, track, tracks)) continue;
            const first = samples(clip.start, rate, fps), last = samples(end(clip), rate, fps);
            const origin = changedSpeed(clip) || clip.speed_phase ? (clip.in * 2000 + (clip.speed_phase || 0) * 20) * rate / 48000 : sourceStart(clip, rate, fps);
            const speed = changedSpeed(clip) ? (clip.speed ?? 1) : 1;
            for (const [begin, stop] of lookup(`${clip.source.kind}:${clip.source.id}`)) {
                let a = (begin * rate - origin) / speed, b = (stop * rate - origin) / speed;
                if (clip.reverse) [a, b] = [(last - first) - b, (last - first) - a];
                a = Math.max(first, Math.floor(first + a + .5)); b = Math.min(last, Math.floor(first + b + .5));
                if (b > a) found.push([a, b]);
            }
        }
    }
    found.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    const gap = 2 * Math.max(1, Math.floor(DUCK_RAMP_SECONDS * rate + .5)), joined = [];
    for (const [a, b] of found) {
        if (joined.length && a <= joined.at(-1)[1] + gap) joined.at(-1)[1] = Math.max(joined.at(-1)[1], b);
        else joined.push([a, b]);
    }
    return joined;
}
export function duckGain(ranges, blockStart, count, rate = 48000) {
    const gain = new Float64Array(count).fill(1), ramp = Math.max(1, Math.floor(DUCK_RAMP_SECONDS * rate + .5)), low = 10 ** (DUCK_DB / 20);
    for (const [first, last] of ranges) {
        if (last + ramp <= blockStart || first - ramp >= blockStart + count) continue;
        for (let i = 0; i < count; i++) {
            const position = blockStart + i;
            const into = Math.max(0, Math.min(1, (position - (first - ramp)) / ramp)), outOf = Math.max(0, Math.min(1, ((last + ramp) - position) / ramp));
            gain[i] = Math.min(gain[i], 1 - (1 - low) * Math.min(into, outOf));
        }
    }
    return gain;
}
// The lowered clips' sources need speech spans from the clips they sit under.
export const duckSources = tracks => tracks.some(track => track.clips.some(clip => clip.duck && clip.source))
    ? [...new Set(tracks.filter(track => track.kind === 'audio').flatMap(track => track.clips)
        .filter(clip => clip.source && !clip.duck).map(clip => `${clip.source.kind}:${clip.source.id}`))] : [];
