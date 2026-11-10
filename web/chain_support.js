// Pure helpers the creative panel's long-video planning shares with the editor store; kept out of
// editor_store.js so the panel does not load the editing implementation.
import { effortFor, effortsFrom } from './sampling_effort.js';
const clockDefault = {now: () => Date.now(), setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: id => clearTimeout(id)};
export const chainRoute = (id, action) => '/freevideo/chain' + (id ? '/' + encodeURIComponent(id) : '') + (action ? '/' + action : '');

// The line for a chain that waits behind others: its own time when one video is ahead and its time left is known.
export function waitingText(waiting, t) {
    const ahead = waiting?.ahead, left = waiting?.remaining_seconds;
    if (!Number.isInteger(ahead) || ahead < 1) return t('Queued', '排队中');
    if (ahead > 1) return t(`Queued · ${ahead} ahead`, `排队中 · 前面还有 ${ahead} 个`);
    if (!Number.isFinite(left) || left < 0) return t('Queued · waiting for the previous one', '排队中 · 等前一个完成');
    const time = left < 60 ? t(`about ${Math.max(1, Math.ceil(left))} s`, `约 ${Math.max(1, Math.ceil(left))} 秒`)
        : t(`about ${Math.ceil(left / 60)} min`, `约 ${Math.ceil(left / 60)} 分钟`);
    return t(`Queued · starts in ${time}`, `排队中 · ${time}后开始`);
}

// The line while a long video's parts are joined: which part is being written once that is known.
export function joiningText(join, t) {
    const part = join?.part, count = join?.count;
    if (!Number.isInteger(part) || !Number.isInteger(count) || count < 2 || part < 1 || part > count) return t('Joining the parts', '正在接合各段');
    return t(`Joining · Part ${part} of ${count}`, `正在接合 · 第 ${part} 段，共 ${count} 段`);
}

// No overlapping polls and no delivery after disposal, including a slow response.
export function createPoller(read, deliver, {clock = clockDefault, interval = 2000, onError = () => {}} = {}) {
    let active = true, timer = null, running = null;
    async function poll() {
        if (!active || running) return running;
        running = (async () => {
            try { const value = await read(); if (active && await deliver(value) === false) active = false; }
            catch (error) { if (active) onError(error); }
        })().finally(() => {
            running = null;
            if (active) timer = clock.setTimeout(() => { timer = null; poll(); }, interval);
        });
        return running;
    }
    poll();
    return {poll, dispose() { active = false; clock.clearTimeout(timer); }};
}

export function interpolate(points, seconds) {
    const rows = points.filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y) && y >= 0).sort((a, b) => a[0] - b[0]);
    if (!rows.length) return null;
    if (rows.length === 1) return rows[0][1];
    let i = rows.findIndex(([x]) => x >= seconds);
    if (i < 1) i = seconds <= rows[0][0] ? 1 : rows.length - 1;
    const [a, b] = [rows[i - 1], rows[i]];
    return b[0] === a[0] ? b[1] : Math.max(0, a[1] + (seconds - a[0]) * (b[1] - a[1]) / (b[0] - a[0]));
}
// The reply groups its numbers by quality level. An edit keeps the panel's steps,
// not a level's name, so the level is the one those steps describe (as the
// creative panel's range view picks it); other steps have no numbers.
function capabilityTier(reply, settings) {
    const tiers = reply.tiers;
    if (!tiers || typeof tiers !== 'object') return reply;
    if (tiers[settings?.tier]) return tiers[settings.tier];
    const level = effortFor(Number(settings?.base_steps), !!settings?.two_pass, Number(settings?.refine_steps),
        settings?.step_cache, effortsFrom(reply), settings?.upscale_factor);
    return tiers[level?.name.toLowerCase()] || {};
}
export function capabilityPoints(reply, {width, height, settings = {}}) {
    const tier = capabilityTier(reply, settings);
    const i = reply.sizes?.findIndex(size => size.width === width && size.height === height) ?? -1;
    const max = Array.isArray(tier.max_seconds) ? tier.max_seconds[i] : tier.max_seconds;
    const known = Array.isArray(tier.max_frames) ? tier.max_frames[i] : tier.max_frames;
    const maxRequestFrames = Number.isSafeInteger(known) && known >= 0 ? known
        : Number.isFinite(max) ? Math.max(39, 5 + 17 * Math.ceil((24 * max - 5) / 17)) : null;
    const longest = continues => {
        if (maxRequestFrames === null) return 15;
        for (let seconds = 30; seconds >= 2; seconds--) {
            const frames = continues ? 17 * Math.ceil(seconds * 24 / 17) + 22 : Math.max(39, 5 + 17 * Math.ceil((seconds * 24 - 5) / 17));
            if (frames <= maxRequestFrames) return seconds;
        }
        return 2; // The final request check reports too_long when even 2s cannot fit.
    };
    const estimates = i >= 0 && Array.isArray(tier.estimate_seconds?.[i]) ? tier.estimate_seconds[i] : tier.estimate_seconds;
    const lengths = reply.lengths || reply.seconds || [5, 10, 15];
    return {maxSeconds: longest(false), maxFollowSeconds: longest(true), maxRequestFrames,
        points: lengths.map((length, j) => [Number(length.seconds ?? length),
            Array.isArray(estimates) ? estimates[j] : estimates?.[length.seconds ?? length]])};
}
