export const EXTEND_PROPERTY = 'freevideo_extend';
export const EXTEND_TEXT = Object.freeze({
    action: ['Extend', '接着生成'],
    source: ['Continues this video', '接在这条视频后面'],
    remove: ['Remove', '移除'],
    prompt: ['Prompt', '描述'],
    unavailable: ['Video unavailable', '视频不可用'],
});
export const extendText = (t, key) => t(...EXTEND_TEXT[key]);

// The saved video of a continuation holds only what it adds, so the result card and the library keep this entry
// closed until the joined whole video is made; the editor's own extension is unaffected.
export const EXTEND_ENTRY_OPEN = false;
export function canExtend(record) {
    return Boolean(record?.video && /\.mp4$/i.test(record.video) && !record.preview
        && !record.early_stop && record.continuation
        && record.has_video !== false && !['image', 'audio'].includes(record.kind)
        && record.exists !== false && record.missing !== true);
}

export function extendVideoURL(record) {
    const split = record.video.lastIndexOf('/');
    return '/view?' + new URLSearchParams({filename: record.video.slice(split + 1),
        subfolder: split < 0 ? '' : record.video.slice(0, split), type: 'output'});
}

export async function extendSourceExists(api, record) {
    if (!canExtend(record)) return false;
    try { return (await api.fetchApi(extendVideoURL(record), {method: 'HEAD'})).ok; }
    catch { return false; }
}

export async function readExtendRecord(api, record) {
    if (record.geometry?.width && record.geometry?.height && (record.geometry.seconds || record.geometry.frames)) return record;
    let before = null;
    do {
        const query = new URLSearchParams({limit: '48'}); if (before) query.set('before', before);
        const response = await api.fetchApi('/freevideo/library?' + query);
        if (!response.ok) return record;
        const page = await response.json(), saved = page.items?.find(row => row.video === record.video);
        if (saved) return saved;
        before = page.next;
    } while (before);
    return record;
}

export function createExtendState(node, read, write) {
    const keys = ['width', 'height', 'seconds', 'two_pass', 'preview', 'base_steps', 'refine_steps'];
    return {
        get source() { return node.properties?.[EXTEND_PROPERTY]?.source || null; },
        enter(record) {
            const g = record.geometry || {};
            let seconds = record.continuation?.new_frames ? record.continuation.new_frames / (g.fps || 24)
                : g.seconds || g.frames / (g.fps || 24);
            if (!canExtend(record) || ![g.width, g.height, seconds].every(n => Number.isFinite(n) && n > 0)) return false;
            node.properties ||= {};
            const previous = node.properties[EXTEND_PROPERTY]?.previous || {
                values: Object.fromEntries(keys.map(key => [key, read(key)])),
                aspect: node.properties.freevideo_aspect, pixels: node.properties.freevideo_pixels,
                seconds: node.properties.freevideo_seconds,
            };
            // Whole milliseconds, never rounded up past the frame count.
            seconds = Math.floor(seconds * 1000 + 1e-6) / 1000;
            const source = structuredClone(record); source.geometry = {...g, seconds};
            node.properties[EXTEND_PROPERTY] = {source, previous};
            for (const [key, value] of Object.entries({width: g.width, height: g.height,
                seconds: Math.min(60, seconds), text: '', preview: false,
                // A continuation always runs the Light plan: two passes, 8 + 3 steps.
                two_pass: true, base_steps: 8, refine_steps: 3})) write(key, value);
            node.properties.freevideo_seconds = seconds;
            node.properties.freevideo_aspect = 'custom';
            node.properties.freevideo_pixels = g.width * g.height / 1024 ** 2;
            return true;
        },
        remove() {
            const previous = node.properties?.[EXTEND_PROPERTY]?.previous;
            if (!previous) return;
            delete node.properties[EXTEND_PROPERTY];
            for (const [key, value] of Object.entries(previous.values)) if (value !== undefined) write(key, value);
            for (const [key, value] of Object.entries({freevideo_aspect: previous.aspect,
                freevideo_pixels: previous.pixels, freevideo_seconds: previous.seconds})) {
                if (value === undefined) delete node.properties[key]; else node.properties[key] = value;
            }
        },
    };
}

export function extendSnapshot(snapshot, node, source, {multiple = false} = {}) {
    if (!source) return snapshot;
    const copy = structuredClone(snapshot), inputs = copy.output[String(node)].inputs;
    inputs.width = source.geometry.width; inputs.height = source.geometry.height;
    inputs.preview = false; inputs.refine_from = ''; inputs.two_pass = true;
    for (const [key, value] of [['base_steps', 8], ['refine_steps', 3]]) if (key in inputs) inputs[key] = value;
    // A continuation takes no first or last frame and no pre-encoded conditioning.
    for (const key of ['first', 'last', 'conditioning']) delete inputs[key];
    if (multiple) delete inputs.continue_from; else inputs.continue_from = source.video;
    // The predecessor's tail replaces the draft's first frame.
    const media = copy.output[String(inputs.media?.[0])];
    if (media?.class_type === 'FreeVideoMedia') {
        // The continuation's own last frames stand in for every first, last and reference item.
        for (const key of Object.keys(media.inputs)) if (key !== 'assets') delete media.inputs[key];
        if (media.inputs.assets) media.inputs.assets = '[]';
    }
    return copy;
}

// Seek the actual video tail; the library thumbnail is its first frame.
export function mountExtendThumbnail(image, api, record) {
    const video = document.createElement('video'); video.preload = 'auto'; video.muted = true;
    video.playsInline = true; video.setAttribute('aria-hidden', 'true');
    Object.assign(video.style, {position: 'absolute', width: '1px', height: '1px', opacity: '.01', pointerEvents: 'none'});
    image.parentElement.append(video);
    let disposed = false, captured = false, target = 0, callback = null;
    const capture = () => {
        if (disposed || captured || !video.videoWidth) return;
        captured = true;
        video.pause();
        const canvas = document.createElement('canvas'); canvas.width = 192;
        canvas.height = Math.round(192 * video.videoHeight / video.videoWidth);
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
        image.src = canvas.toDataURL('image/jpeg', .8);
        video.removeAttribute('src'); video.load(); video.remove();
    };
    const waitForFrame = () => {
        callback = video.requestVideoFrameCallback((_now, metadata) => {
            if (disposed || captured) return;
            if (metadata.mediaTime + .001 >= target) capture(); else waitForFrame();
        });
    };
    video.onloadedmetadata = () => {
        if (!Number.isFinite(video.duration)) return;
        target = Math.max(0, video.duration - 1 / (record.geometry?.fps || 24));
        if (video.requestVideoFrameCallback) waitForFrame();
        video.currentTime = target;
    };
    let retry = 0, attempts = 0;
    video.onseeked = () => {
        // A paused video may not present its newly decoded frame.
        if (video.requestVideoFrameCallback) video.play().catch(() => requestAnimationFrame(capture));
        else requestAnimationFrame(capture);
        // A busy machine may never deliver that frame: seek to the same place again, a few times.
        clearTimeout(retry);
        retry = setTimeout(() => {
            if (disposed || captured || ++attempts > 4) return;
            video.currentTime = Math.max(0, target - attempts / 1000);
        }, 1500);
    };
    video.src = api.apiURL(extendVideoURL(record));
    return () => { disposed = true; clearTimeout(retry); video.pause(); if (callback !== null) video.cancelVideoFrameCallback?.(callback);
        video.onloadedmetadata = video.onseeked = null; video.removeAttribute('src'); video.load(); video.remove(); };
}
