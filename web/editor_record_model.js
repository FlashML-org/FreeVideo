// Recording from a microphone onto the sound tracks. Pure helpers: where a take starts and stops,
// the 16-bit WAV a take is saved as (the server tells sound from picture by file suffix), the level shown while recording.
import {FPS} from './editor_model.js';

export const COUNTDOWN_SECONDS = 3;
const AUDIO_TRACKS = ['a1', 'a2', 'a3'];

// Whether the chosen stretch is on a sound track (a stretch of the picture track does not offer recording).
export function recordable(selection) {
    if (!selection) return true;
    if (selection.kind === 'gap') return AUDIO_TRACKS.includes(selection.track);
    if (selection.kind === 'range') return !selection.tracks?.length || selection.tracks.some(id => AUDIO_TRACKS.includes(id));
    return false;
}

// A stretch chosen starts the take at its start and ends it at its end; with none, the take starts at the play position and runs until stopped.
export function recordRange(selection, playhead) {
    if (selection && ['gap', 'range'].includes(selection.kind) && Number.isSafeInteger(selection.start) && selection.frames > 0)
        return {start: selection.start, frames: selection.frames};
    return {start: Math.max(0, Math.round(playhead) || 0), frames: null};
}

// The sound track a take lands on: a1 when its stretch is free, otherwise the recording sub-track a3 under it, where
// it replaces whatever an older take covers there. Null only when a3 is locked: a take never lands on the music track a2.
export function landingTrack(record, selection, start, frames) {
    const free = (track, ignoreLock) => !track || ((ignoreLock || !track.locked) && !track.clips.some(clip => clip.start < start + frames && start < clip.start + clip.frames));
    const a1 = record.tracks.find(row => row.id === 'a1'), a3 = record.tracks.find(row => row.id === 'a3');
    return free(a1) ? 'a1' : a3?.locked ? null : 'a3';
}

// Whether a take on the sub-track would cover part of an older take.
export function replacesTake(record, start, frames) {
    return !!record.tracks.find(row => row.id === 'a3')?.clips.some(clip => clip.start < start + frames && start < clip.start + clip.frames);
}

export const secondsOf = frames => frames / FPS;

// A take's name in the library: 录音 / Recording and the time it began.
export function takeName(date, t = en => en) {
    const pad = value => String(value).padStart(2, '0');
    return `${t('Recording', '录音')} ${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}.wav`;
}

// Loudness of a block of samples, 0 to 1, for the level meter.
export function level(samples) {
    if (!samples?.length) return 0;
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    return Math.min(1, Math.sqrt(sum / samples.length) * 3);
}

// Mono mix of channel arrays, trimmed to `limit` samples when given.
export function mixdown(channels, limit = Infinity) {
    const length = Math.min(limit, Math.max(0, ...channels.map(channel => channel.length)));
    const out = new Float32Array(length);
    for (const channel of channels) for (let i = 0; i < length && i < channel.length; i++) out[i] += channel[i] / channels.length;
    return out;
}

// 16-bit PCM mono WAV bytes.
export function encodeWav(samples, sampleRate) {
    if (!Number.isInteger(sampleRate) || sampleRate <= 0) throw new RangeError('sampleRate');
    const bytes = new Uint8Array(44 + samples.length * 2), view = new DataView(bytes.buffer);
    const text = (at, value) => { for (let i = 0; i < value.length; i++) view.setUint8(at + i, value.charCodeAt(i)); };
    text(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); text(8, 'WAVE'); text(12, 'fmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    text(36, 'data'); view.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
        const value = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(44 + i * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true);
    }
    return bytes;
}

// The elapsed time of a take, as m:ss.
export const elapsedText = seconds => `${Math.floor(Math.max(0, seconds) / 60)}:${String(Math.floor(Math.max(0, seconds) % 60)).padStart(2, '0')}`;

// A microphone by a short name: the system's default marker and the hardware id in brackets are dropped; a name that is empty or
// only an id falls back to the number.
export function deviceName(label, number, t) {
    const name = String(label || '').replace(/^(default|communications)\s*-\s*/i, '').replace(/\s*\([0-9a-f]{4}:[0-9a-f]{4}\)\s*$/i, '').trim();
    return name || t(`Microphone ${number}`, `麦克风 ${number}`);
}
