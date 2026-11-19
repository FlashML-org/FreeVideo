// "Upscale to W × H" on a finished video: one 1.5× step, offered only when this computer can generate
// that size and length. Menus are built synchronously, so answers are read ahead and kept per size.

// 1.5× keeps the aspect exactly and lands on the engine's 32-pixel grid only for sides that are multiples of 64.
export function upscaleSize(width, height) {
    if (![width, height].every(n => Number.isSafeInteger(n) && n > 0 && n % 64 === 0)) return null;
    return {width: width * 3 / 2, height: height * 3 / 2};
}

// The work's own picture: finished (not a preview), 24 fps and of a known length.
export function upscaleSource(row) {
    const g = row?.geometry || {};
    if (!row || row.kind === 'image' || row.preview) return null;
    if ((g.fps ?? 24) !== 24 || !Number.isSafeInteger(g.frames) || g.frames < 1) return null;
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

// A finished 24 fps video the 4K size makes larger. The small model runs on any card, so nothing is asked first.
export function upscale4kOffer(row) {
    const g = row?.geometry || {};
    if (!row || row.kind === 'image' || row.preview) return null;
    if ((g.fps ?? 24) !== 24 || !Number.isSafeInteger(g.frames) || g.frames < 1) return null;
    return upscale4kSize(g.width, g.height);
}

// `read(width, height)` resolves to the longest single request in frames at that size, or null when not known.
export function createUpscaleOffer(read) {
    const answers = new Map(), pending = new Map();
    const key = size => `${size.width}x${size.height}`;
    function prepare(rows) {
        const sizes = new Map();
        for (const row of rows) { const size = upscaleSource(row); if (size) sizes.set(key(size), size); }
        return Promise.all([...sizes].map(([name, size]) => {
            if (answers.has(name)) return answers.get(name);
            if (!pending.has(name)) pending.set(name, Promise.resolve().then(() => read(size.width, size.height)).catch(() => null)
                .then(frames => { pending.delete(name); answers.set(name, Number.isSafeInteger(frames) ? frames : null); return answers.get(name); }));
            return pending.get(name);
        }));
    }
    // The target size when this work can be upscaled here now; null hides the row.
    function offer(row) {
        const size = upscaleSource(row);
        const frames = size && answers.get(key(size));
        return size && Number.isSafeInteger(frames) && size.frames <= frames ? {width: size.width, height: size.height} : null;
    }
    // A change of free memory or engine state can change every answer.
    const forget = () => answers.clear();
    return {prepare, offer, forget};
}
