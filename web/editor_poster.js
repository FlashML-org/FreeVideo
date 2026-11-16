// The picture's cover while the edit's first video frame is on its way: the thumbnail of the part under the playhead,
// filling the same box as the video (same fit), so the preview shows its final picture from the first frames. Once a
// shown video has a frame the cover fades out and goes; nothing changes size. Used once per opening of the view.
const LIMIT = 10000;
export function createPicturePoster(frame, {state, sources, sourceKey, mediaURL}) {
    if (typeof document === 'undefined' || !frame) return {ready: Promise.resolve(), update() {}, dispose() {}};
    const image = document.createElement('img'); image.className = 'fv-ed-poster'; image.alt = ''; image.setAttribute('aria-hidden', 'true');
    image.decoding = 'async'; image.hidden = true; frame.append(image);
    let done = false, shown = '', loop = 0, drawn = Promise.resolve();
    const started = performance.now();
    function clipUnderPlayhead() {
        const record = state.record, clips = record?.tracks?.[0]?.clips || [];
        const position = Math.max(0, state.playhead || 0);
        return clips.find(clip => clip.source && position >= clip.start && position < clip.start + clip.frames)
            || (position === 0 ? clips.find(clip => clip.source) : null) || null;
    }
    function cover(clip) {
        if (!clip?.source) return null;
        const key = sourceKey(clip.source), metadata = sources.get(key);
        if (metadata?.missing) return null;
        if (metadata?.thumbnail) return metadata.thumbnail;
        return clip.source.kind === 'work' && clip.source.id ? '/freevideo/library/thumbnail?' + new URLSearchParams({id: clip.source.id}) : null;
    }
    function finish({fade = true} = {}) {
        if (done) return;
        done = true; cancelAnimationFrame(loop);
        if (!fade || image.hidden) { image.remove(); return; }
        image.dataset.gone = 'true';
        setTimeout(() => image.remove(), 220);
    }
    // A shown video with a decoded frame at its place takes over.
    function watch() {
        loop = 0;
        if (done) return;
        const video = frame.querySelector('video.fv-ed-visible');
        if (video && video.readyState >= 2 && !video.seeking) { finish(); return; }
        if (performance.now() - started > LIMIT) { finish({fade: false}); return; }
        loop = requestAnimationFrame(watch);
    }
    function update() {
        if (done) return;
        const url = cover(clipUnderPlayhead());
        if (!url || url === shown) return;
        shown = url;
        image.onerror = () => { image.hidden = true; };
        image.src = mediaURL(url); image.hidden = false;
        drawn = image.decode ? image.decode().catch(() => {}) : Promise.resolve();
        if (!loop) loop = requestAnimationFrame(watch);
    }
    update();
    // `ready`: settles once the cover of the edit as it is now is drawn (at once when it needs none).
    return {get ready() { update(); return drawn; }, update, dispose() { finish({fade: false}); }};
}
