// "Upscale to W × H" on a finished video, three rows: the quick 1.5× size made frame by frame by a small model (first,
// on any card), the detailed 1.5× step that samples again (only when this computer can generate that size and length),
// and the 4K size by the same small model. Each row says about how long it takes on this computer. Menus are built
// synchronously, so answers and times are read ahead and kept per size.
import { capabilityPoints } from './chain_support.js';

// What one step runs, whatever the creation panel is set to (the engine fixes it, work_upscale.PLAN): two passes of
// 8 + 3 steps with a 1.5× learned upscale between them and no step cache. The capabilities reply groups its numbers
// by quality level, so the frames this computer admits for an upscale are read from the level with these steps.
export const UPSCALE_PLAN = Object.freeze({two_pass: true, base_steps: 8, refine_steps: 3, step_cache: null, upscale_factor: 1.5});

// The longest single upscale request at a size, as the planner admits it now; null when it cannot be told.
// `fetchApi` is ComfyUI's api.fetchApi; a reply still being computed is asked again a second later, five times.
export async function admittedFrames(fetchApi, width, height, wait = () => new Promise(done => setTimeout(done, 1000))) {
    for (let tries = 0; tries < 5; tries++) {
        const response = await fetchApi('/freevideo/capabilities?' + new URLSearchParams({width, height}));
        if (!response.ok) return null;
        const reply = await response.json();
        if (reply?.status === 'computing') { await wait(); continue; }
        return reply && (!reply.status || reply.status === 'ready')
            ? capabilityPoints(reply, {width, height, settings: UPSCALE_PLAN}).maxRequestFrames : null;
    }
    return null;
}

// 1.5× keeps the aspect exactly and lands on the engine's 32-pixel grid only for sides that are multiples of 64.
export function upscaleSize(width, height) {
    if (![width, height].every(n => Number.isSafeInteger(n) && n > 0 && n % 64 === 0)) return null;
    return {width: width * 3 / 2, height: height * 3 / 2};
}

// The work's own picture: finished (not a preview), 24 fps, of a known length and with its saved request.
export function upscaleSource(row) {
    const g = row?.geometry || {};
    if (!row || row.kind === 'image' || row.preview) return null;
    if ((g.fps ?? 24) !== 24 || !Number.isSafeInteger(g.frames) || g.frames < 1) return null;
    // One step resamples with the prompt and seed saved with the work: a merged video has neither (4K does not need them).
    if (row.saved_request === false) return null;
    const size = upscaleSize(g.width, g.height);
    return size && {...size, frames: g.frames};
}

// The 4K size (frame_upscale.size): the long side 3840 and the other side in proportion on an 8-pixel grid, scaled down
// in proportion until the picture has at most 36864 16 × 16 macroblocks (H.264's usual limit); null when that would
// not make the picture larger.
const LONG_SIDE = 3840, MAX_MACROBLOCKS = 36864;
const grid = value => Math.floor(value / 8 + .5) * 8;
export function upscale4kSize(width, height) {
    if (![width, height].every(n => Number.isSafeInteger(n) && n > 0)) return null;
    const longest = Math.max(width, height);
    for (let side = LONG_SIDE; side >= 8; side -= 8) {
        const w = grid(width * side / longest), h = grid(height * side / longest);
        if (Math.min(w, h) >= 8 && Math.ceil(w / 16) * Math.ceil(h / 16) <= MAX_MACROBLOCKS)
            return Math.max(w, h) > longest ? {width: w, height: h} : null;
    }
    return null;
}

// The quick 1.5× size (frame_upscale.fast_size): each side times 1.5 on the 8-pixel grid, the detailed step's size
// wherever that one is offered; null when it would not make the picture larger or passes the 4K size's limit.
export function fastSize(width, height) {
    if (![width, height].every(n => Number.isSafeInteger(n) && n > 0)) return null;
    const w = grid(width * 3 / 2), h = grid(height * 3 / 2);
    // Past the 4K size's picture limit, the 4K row is the largest.
    if (Math.ceil(w / 16) * Math.ceil(h / 16) > MAX_MACROBLOCKS) return null;
    return Math.min(w, h) >= 8 && Math.max(w, h) > Math.max(width, height) ? {width: w, height: h} : null;
}

// A finished 24 fps video the quick size makes larger. Like 4K, nothing is asked first and no saved request is needed.
export function upscaleFastOffer(row) {
    const g = row?.geometry || {};
    if (!row || row.kind === 'image' || row.preview) return null;
    if ((g.fps ?? 24) !== 24 || !Number.isSafeInteger(g.frames) || g.frames < 1) return null;
    return fastSize(g.width, g.height);
}

// The menu's times for a finished video (seconds or null for fast, detailed and 4k), from /freevideo/upscale-estimate.
export async function upscaleTimes(fetchApi, width, height, frames) {
    const response = await fetchApi('/freevideo/upscale-estimate?' + new URLSearchParams({width, height, frames}));
    if (!response.ok) return null;
    const reply = await response.json();
    return reply?.seconds && typeof reply.seconds === 'object' ? reply.seconds : null;
}

// A finished 24 fps video the 4K size makes larger. The small model runs on any card, so nothing is asked first.
export function upscale4kOffer(row) {
    const g = row?.geometry || {};
    if (!row || row.kind === 'image' || row.preview) return null;
    if ((g.fps ?? 24) !== 24 || !Number.isSafeInteger(g.frames) || g.frames < 1) return null;
    return upscale4kSize(g.width, g.height);
}

// `read(width, height)` resolves to the longest single request in frames at that size, or null when not known;
// `readTimes(width, height, frames)` to the rows' seconds for a source of that size and length ({fast, detailed, 4k}).
export function createUpscaleOffer(read, readTimes = null) {
    const answers = new Map(), pending = new Map(), times = new Map(), timing = new Map();
    const key = size => `${size.width}x${size.height}`;
    const timeKey = g => `${g.width}x${g.height}x${g.frames}`;
    function ask(store, waiting, name, question, keep) {
        if (store.has(name)) return store.get(name);
        if (!waiting.has(name)) waiting.set(name, Promise.resolve().then(question).catch(() => null)
            .then(value => { waiting.delete(name); store.set(name, keep(value)); return store.get(name); }));
        return waiting.get(name);
    }
    function prepare(rows) {
        const sizes = new Map(), sources = new Map();
        for (const row of rows) {
            const size = upscaleSource(row); if (size) sizes.set(key(size), size);
            const g = row?.geometry || {};
            if (readTimes && upscaleFastOffer(row)) sources.set(timeKey(g), g);
        }
        return Promise.all([
            ...[...sizes].map(([name, size]) => ask(answers, pending, name, () => read(size.width, size.height),
                frames => Number.isSafeInteger(frames) ? frames : null)),
            ...[...sources].map(([name, g]) => ask(times, timing, name, () => readTimes(g.width, g.height, g.frames),
                value => value && typeof value === 'object' ? value : null))]);
    }
    // Seconds this row's kind ('fast', 'detailed', '4k') takes for this work here, or null when not known yet.
    function seconds(row, kind) {
        const value = times.get(timeKey(row?.geometry || {}))?.[kind];
        return Number.isFinite(value) && value > 0 ? value : null;
    }
    // The target size when this work can be upscaled here now; null hides the row.
    function offer(row) {
        const size = upscaleSource(row);
        const frames = size && answers.get(key(size));
        return size && Number.isSafeInteger(frames) && size.frames <= frames ? {width: size.width, height: size.height} : null;
    }
    // A change of free memory or engine state can change every answer, and a finished upscale every time.
    const forget = () => { answers.clear(); times.clear(); };
    return {prepare, offer, forget, seconds};
}

// The upscale rows of one finished video, the same wherever they are offered (the library's right-click menu, the
// result's "⋯"): one 1.5× step where this computer can make it (`offer`, from createUpscaleOffer), then the 4K size.
// A row whose condition is not met is left out, never shown disabled.
export const UPSCALE_USE = ['Make a larger copy as a new creation', '生成一个更大的新作品，原作品不变'];
// Each row's tip: what it is like, then UPSCALE_USE.
const ROW_USE = {
    fast: ['Quick and sharper; closest to the original. Make a larger copy as a new creation.', '很快，画面更清晰，和原片最接近。生成一个更大的新作品，原作品不变。'],
    detailed: ['Slower and adds more detail; small details may differ from the original. Make a larger copy as a new creation.', '较慢，补出更多细节；小细节可能和原片不同。生成一个更大的新作品，原作品不变。'],
    '4k': ['Upscales to 4K the same way as the first row; a larger file. Make a larger copy as a new creation.', '放大到 4K，做法和第一行相同，文件较大。生成一个更大的新作品，原作品不变。'],
    // Without the detailed row (a merged or imported video), the 4K row says what it is on its own.
    '4k-alone': ['Upscales to 4K; sharper and closest to the original; a larger file. Make a larger copy as a new creation.', '放大到 4K，画面更清晰，和原片最接近；文件较大。生成一个更大的新作品，原作品不变。'],
};

// How long, in one number (menu rows are narrow): under a minute, whole minutes, then half hours.
export function upscaleTime(seconds) {
    if (!(Number.isFinite(seconds) && seconds > 0)) return null;
    if (seconds < 60) return ['Under 1 min', '不到 1 分钟'];
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return ['About {minutes} min', '约 {minutes} 分钟', { minutes }];
    return ['About {hours} h', '约 {hours} 小时', { hours: Math.round(seconds / 1800) / 2 }];
}
export const UPSCALE_QUEUED = ['Queued. It appears in "Creations" when done.', '已加入队列，完成后在“作品”里。'];
export const UPSCALE_FAILED = ['Could not start upscaling: ', '无法开始放大：'];
export function upscaleChoices(row, offer) {
    const rows = [['upscale-fast', 'fast', upscaleFastOffer(row), {to: 'fast'}], ['upscale', 'detailed', offer?.offer(row), {}],
        ['upscale-4k', '4k', upscale4kOffer(row), {to: '4k'}]].filter(([, , larger]) => larger);
    const detailed = rows.some(([, kind]) => kind === 'detailed');
    return rows.map(([id, kind, larger, options]) => {
        const size = `${larger.width} × ${larger.height}`;
        const label = kind === 'detailed' ? ['Upscale to {size} (detailed)', '放大到 {size}（精细）', { size }] : ['Upscale to {size}', '放大到 {size}', { size }];
        const use = ROW_USE[kind === '4k' && !detailed ? '4k-alone' : kind];
        return {id, kind, width: larger.width, height: larger.height, options, label, use, note: upscaleTime(offer?.seconds?.(row, kind))};
    });
}
