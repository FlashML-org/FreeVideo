// Dubbing lines of a chosen stretch of speech: which lines the user changed, what the bar says about them and which
// ones did not fit. Pure helpers; the server does the recognition and the dubbing.
import {FPS} from './editor_model.js';

export const SPEECH_TRACKS = ['a1', 'a4'];
const NBSP = ' ';
const clean = value => String(value ?? '').trim();
const end = clip => clip.start + clip.frames;

// What a chosen stretch asks to be dubbed: a sound clip of the original sound (or its separated voice), whole or the
// part a stretch covers, in seconds from the clip's start. Null for anything else.
export function voiceTarget(record, selection) {
    if (!selection) return null;
    let track, clip, from, to;
    if (selection.kind === 'clip') {
        // A sound clip is chosen together with the picture it is linked to; exactly one speech clip may be among them.
        // Choosing a picture clip (which brings its linked sound along) is not choosing the voice.
        if (record.tracks.some(row => row.kind !== 'audio' && row.clips.some(value => selection.ids.includes(value.id)))) return null;
        const found = record.tracks.filter(row => SPEECH_TRACKS.includes(row.id)).flatMap(row => row.clips.filter(value => selection.ids.includes(value.id)).map(value => [row, value]));
        if (found.length !== 1 || !found[0][1].source) return null;
        [track, clip] = found[0];
        from = 0; to = clip.frames;
    } else if (selection.kind === 'range') {
        if (selection.tracks?.length !== 1 || !SPEECH_TRACKS.includes(selection.tracks[0])) return null;
        track = record.tracks.find(row => row.id === selection.tracks[0]);
        clip = track?.clips.find(value => value.source && value.start <= selection.start && end(value) >= selection.start + selection.frames);
        if (!clip) return null;
        from = selection.start - clip.start; to = from + selection.frames;
    } else return null;
    return {clipId: clip.id, track: track.id, separated: track.id !== 'a1', start: from / FPS, end: to / FPS, clip};
}

// The lines whose text differs from the recognised one; spaces at the ends do not count.
export const changedLines = (lines, texts) => lines.filter(line => clean(texts.get(line.id) ?? line.text) !== clean(line.text));

// Seconds as the bar shows them: m:ss.
export const clockText = seconds => { const whole = Math.max(0, Math.floor(seconds)); return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`; };

// The button names what it does; the time is not shown: the figure the service has is the new sentence's length, not the wait.
export function redubLabel(count, t) {
    if (!count) return t('Redub', '重新配音');
    return t(`Redub ${count} ${count === 1 ? 'line' : 'lines'}`, `重新配音 ${count} 句`);
}

// One line needs no number; the time only once the service has given one.
export const dubbingText = (index, count, seconds, t) => {
    const time = Number.isFinite(seconds) ? ` · ${t(`about ${Math.max(1, Math.ceil(seconds))}${NBSP}s`, `约 ${Math.max(1, Math.ceil(seconds))}${NBSP}秒`)}` : '';
    return count > 1 ? t(`Dubbing · line ${index} of ${count}${time}`, `正在配音 · 第 ${index}/${count} 句${time}`)
        : t(`Dubbing${time}`, `正在配音${time}`);
};

// "2.6 s too long. Original voice kept.": one decimal, never "0.0". Without a way to shorten it, the row says what to do.
export const overrunText = (seconds, t, canShorten = true) => {
    const value = Math.max(0.1, Math.round(seconds * 10) / 10).toFixed(1);
    return canShorten ? t(`${value}${NBSP}s too long. Original voice kept.`, `超出 ${value}${NBSP}秒，已保留原声`)
        : t(`${value}${NBSP}s too long. Original voice kept. Shorten this line.`, `超出 ${value}${NBSP}秒，已保留原声。请缩短这句。`);
};

export const failedText = (count, t) => count === 1
    ? t('1 line could not be dubbed. Redub it.', '有 1 句无法配音，请重新配音。')
    : t(`${count} lines could not be dubbed. Redub them.`, `有 ${count} 句无法配音，请重新配音。`);

// What a result says about each line it dubbed: kept the original voice because it did not fit, failed, or placed.
export function lineResults(result) {
    const out = new Map();
    // A line the run was stopped before is simply not dubbed: no mark.
    for (const line of (result?.lines || []).filter(value => value.code !== 'cancelled')) out.set(line.id, line.code ? {state: 'failed', code: line.code}
        : line.replaced === false ? {state: 'kept', overrun: line.overrun || 0}
        : {state: 'placed', overrun: 0, short: line.warning === 'too_short'});
    return out;
}
