// Beat points for the music track: where the beats of a music clip fall on the timeline, offered to the snapping
// of a drag only while they are far enough apart on screen to be told apart. Analysis adapted from Imagination Studio.
import * as model from './editor_model.js';
import {percent} from './editor_speed_model.js';
import {trackAudible} from './editor_audio_model.js';
import {MUSIC_TRACK} from './editor_music_model.js';

// Neighbouring beats closer than this on screen would take every spot away from free placement.
export const BEAT_MIN_PIXELS = 32;

// Timeline frames of a clip's beats (seconds in its source): trim, speed and reverse are applied, only frames the clip shows are kept.
export function clipBeatFrames(clip, beats, fps = model.FPS) {
    const speed = percent(clip), phase = clip.speed_phase ?? 0, frames = [];
    for (const time of beats || []) {
        if (typeof time !== 'number' || !Number.isFinite(time) || time < 0) continue;
        const along = ((time * fps - clip.in) * 100 - phase) / speed;
        const index = Math.round(clip.reverse ? clip.frames - 1 - along : along);
        if (index >= 0 && index < clip.frames) frames.push(clip.start + index);
    }
    return frames.sort((a, b) => a - b);
}

// `beatsOf(clip)` gives the analysed beat times of a clip's source or nothing; clips in `exceptIds` (being dragged) bring none.
export function beatSnapPoints(record, beatsOf, zoom, exceptIds = [], fps = model.FPS) {
    const track = record.tracks.find(row => row.id === MUSIC_TRACK);
    if (!track || !trackAudible(track, record.tracks)) return [];
    const excluded = new Set(exceptIds), points = new Set();
    for (const clip of track.clips) {
        if (excluded.has(clip.id) || !clip.source) continue;
        const frames = clipBeatFrames(clip, beatsOf(clip), fps);
        let nearest = Infinity;
        for (let i = 1; i < frames.length; i++) nearest = Math.min(nearest, (frames[i] - frames[i - 1]) * zoom / fps);
        if (!frames.length || nearest < BEAT_MIN_PIXELS) continue;
        for (const frame of frames) points.add(frame);
    }
    return [...points].sort((a, b) => a - b);
}
