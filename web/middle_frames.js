// Middle frames: still pictures a video passes through between its first and last frame, each at a time in seconds.
// The engine puts each at a frame number (round(seconds * 24)) strictly between the first frame (0) and the last (frames - 1).
export const MAX_MIDDLE = 3;
const FPS = 24;
// The generated length is aligned up to 17 n + 5 frames (freevideo_engine/geometry.py).
export const alignedFrames = seconds => {
    const requested = Math.ceil(seconds * FPS);
    return requested + (((5 - requested) % 17) + 17) % 17;
};
// One decimal for typing, two for the legal range: 0.04 and 4.92, without a trailing zero.
export const shown = value => String(Math.round(value * 100) / 100);
export const frameOf = at => Math.round(at * FPS);
// The time stored on a row; a missing or unreadable one is NaN, never 0.
export const secondsOf = row => row?.seconds === null || row?.seconds === undefined || row?.seconds === '' ? NaN : Number(row.seconds);
// null when the length is not known (the panel is not connected to a video yet): nothing can be said about the range.
export const SINGLE_SECONDS = 30; // from this length a video is made in parts, and the middle frames belong to the first part only
export function timeRange(seconds) {
    if (!(seconds > 0) || seconds >= SINGLE_SECONDS) return null;
    return {min: 1 / FPS, max: (alignedFrames(seconds) - 2) / FPS, last: (alignedFrames(seconds) - 1) / FPS};
}
// The middle of the longest stretch between the first frame, the last frame and the middle frames already set,
// to 0.1 s. The first picture goes to the centre, the next ones to 1/4 and 3/4.
export function defaultTime(existing, seconds) {
    const range = timeRange(seconds);
    if (!range) return 2.5;
    const stops = [0, ...existing.filter(Number.isFinite).sort((a, b) => a - b), range.last];
    let best = [stops[0], stops[1]];
    for (let i = 1; i + 1 < stops.length; i++) if (stops[i + 1] - stops[i] > best[1] - best[0] + 1e-9) best = [stops[i], stops[i + 1]];
    const middle = Math.round((best[0] + best[1]) / 2 * 10) / 10;
    return Math.min(Math.max(middle, Math.ceil(range.min * 10) / 10), Math.floor(range.max * 10) / 10);
}
// One finding per picture: 'range' (outside the video) or 'same' (the same frame as an earlier picture).
export function timeProblems(times, seconds) {
    const range = timeRange(seconds), seen = new Map(), found = [];
    times.forEach((at, index) => {
        if (!Number.isFinite(at)) { found.push({index, code: 'range'}); return; }
        const frame = frameOf(at);
        if (frame < 1 || (range && frame > Math.round(range.max * FPS))) found.push({index, code: 'range'});
        else if (seen.has(frame)) found.push({index, code: 'same'});
        else seen.set(frame, index);
    });
    return found;
}
export function problemText(problem, seconds, t) {
    if (problem.code === 'same') return t('Same time as another middle frame. Choose a different time.', '和另一张中间帧的时间相同，请错开。');
    const range = timeRange(seconds);
    return range ? t(`Must be between ${shown(range.min)} and ${shown(range.max)} s.`, `时间需在 ${shown(range.min)} 到 ${shown(range.max)} 秒之间。`)
        : t('Enter a time in seconds.', '请填写时间（秒）。');
}
export const labels = {
    role: t => t('Middle frame', '中间帧'),
    roleFull: t => t('Middle frame (up to 3)', '中间帧（最多 3 张）'),
    time: t => t('At', '时间'),
    unit: t => t('s', '秒'),
    tooMany: t => t('Enable up to 3 middle frames.', '中间帧最多启用 3 张图片。'),
    needsBoth: t => t('Middle frames need both a first and a last frame.', '中间帧需要同时有首帧和尾帧。'),
    firstPartOnly: t => t('Middle frames apply to the first part only.', '中间帧只用于第一段。'),
    continuing: t => t('When continuing a video, middle frames are not used; the first and last frames are used as references.',
        '接续视频时不使用中间帧，首尾帧会用作参考图。'),
    mode: (count, t) => t(`First + last frame · ${count} middle ${count === 1 ? 'frame' : 'frames'}`, `首尾帧生成 · ${count} 张中间帧`),
    chipTitle: (at, t) => `${t('Middle frame', '中间帧')} · ${shown(at)} ${t('s', '秒')}`,
};
// The sentence for the notice line when the middle frames cannot be used as they are; '' when they can.
// `rows` are the enabled media rows of the panel, `linked` the number of first/last/reference sockets connected.
// A line that is not a fault: a long video is made in parts and only its first part takes the middle frames.
export const middleInfo = (rows, seconds, t) => seconds >= SINGLE_SECONDS && rows.some(row => row.role === 'middle') ? labels.firstPartOnly(t) : '';
export function middleNotice(rows, {first, last, references, seconds}, t) {
    const middle = rows.filter(row => row.role === 'middle');
    if (!middle.length) return '';
    if (middle.length > MAX_MIDDLE) return labels.tooMany(t);
    if (!first || !last) return labels.needsBoth(t);
    if (references) return '';
    const problem = timeProblems(middle.map(secondsOf), seconds)[0];
    return problem ? problemText(problem, seconds, t) : '';
}
