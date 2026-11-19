import { cutTip } from './cut_tip.js';
import {editorIcon} from './editor_icons.js';
import {pageRepair, repairButton} from './repair_hint.js';
import {displayTracks, isText, isTextTrack, textDropLocked} from './editor_text_model.js';
// Pointer gestures translate coordinates into model calls; the model owns edits.
// These exports deliberately have no DOM dependencies, so input arithmetic can
// be checked in Node with the same code that runs in the browser.
import * as model from './editor_model.js';
import {timeEstimate} from './editor_genbar.js';
import {clipPictureSize, updatePictureBadge} from './editor_frame.js';
import {createSelectionGestures, isMultiple, isTyping} from './editor_select.js';
import {createTrackMenu} from './editor_menu.js';
import {transportSnapPoints, snappingEnabled, setSnapping as setTransportSnapping} from './editor_transport_model.js';
import {beatSnapPoints} from './editor_beats_model.js';
import {attachAudioTracks} from './editor_audio.js';
import {seconds as secondsText} from './editor_audio_notes.js';
import {renderSpeedBadge} from './editor_speed.js';
import {speedEdited} from './editor_speed_model.js';
import {MUSIC_TRACK, MAX_MUSIC_FRAMES} from './editor_music_model.js';
import {applyTip, switchTip, setTip} from './editor_tracks_tips.js';
import {setAttr, setData, setHidden, setText} from './dom_quiet.js';
// A range drawn on the music track alone: music for that stretch.
const musicRange = selection => selection?.kind === 'range' && selection.tracks?.length === 1 && selection.tracks[0] === MUSIC_TRACK;

export const SOURCE_TYPE = 'application/x-freevideo-source';
export const SNAP_DISTANCE = 8;
export const frameToPixels = (frame, zoom, fps = model.FPS) => frame * zoom / fps;
export const pixelsToFrame = (pixels, zoom, fps = model.FPS) => Math.round(pixels * fps / zoom);

// `beats` are points of lower rank: they are used only when nothing else is within reach.
export function snapFrame(frame, points, zoom, {offsets = [0], distance = SNAP_DISTANCE, enabled = true, beats = []} = {}) {
    if (!enabled) return {frame: Math.max(0, frame), guide: null};
    for (const candidates of [points, beats]) {
        let best = null, delta = Infinity;
        for (const point of candidates) for (const offset of offsets) {
            const difference = point - (frame + offset);
            const pixels = Math.abs(frameToPixels(difference, zoom));
            if (pixels <= distance + 1e-9 && pixels < delta && frame + difference >= 0) {
                delta = pixels; best = {frame: frame + difference, guide: point};
            }
        }
        if (best) return best;
    }
    return {frame: Math.max(0, frame), guide: null};
}

export function insertionPoint(clips, frame, exceptIds = []) {
    const except = new Set(exceptIds);
    const hit = clips.find(clip => !except.has(clip.id) && frame >= clip.start && frame < clip.start + clip.frames);
    if (!hit) return {frame: Math.max(0, frame), hit: null, edge: null};
    const edge = frame === hit.start ? 'start' : 'end';
    return {frame: edge === 'start' ? hit.start : hit.start + hit.frames, hit: hit.id, edge};
}

// A range starts where it was pressed (ends there when drawn leftwards); its length is whole seconds, at least two.
export function rangeParameters(anchor, frame, tracks = ['v1', 'a1'], fps = model.FPS) {
    const from = Math.max(0, Math.round(anchor)), to = Math.max(0, Math.round(frame));
    let frames = Math.max(2 * fps, Math.round(Math.abs(to - from) / fps) * fps);
    // Drawn leftwards past the start, the end stays put: the length shrinks to the whole seconds that fit before it.
    if (to < from && frames > from) frames = Math.max(2 * fps, Math.floor(from / fps) * fps);
    return {kind: 'range', start: to < from ? Math.max(0, from - frames) : from, frames, tracks: [...tracks]};
}

export function moveParameters(clip, anchor, frame, points, zoom, beats = []) {
    const desired = Math.max(0, clip.start + frame - anchor);
    return snapFrame(desired, points, zoom, {offsets: [0, clip.frames], beats});
}

export function rangeMoveParameters(record, range, anchor, frame, points, zoom, {enabled = true, beats = []} = {}) {
    const desired = Math.max(0, range.start + frame - anchor);
    const occupied = start => model.rangeOccupied(record, {...range, start});
    if (!enabled) return {frame: desired, guide: null, blocked: occupied(desired)};
    const aligned = moveParameters(range, anchor, frame, points, zoom, beats);
    if (occupied(desired)) {
        // Prefer a free, flush content edge over an unrelated grid/playhead snap.
        let best = null, distance = Infinity;
        for (const track of record.tracks.filter(track => range.tracks.includes(track.id))) for (const clip of track.clips) {
            for (const [start, guide] of [[clip.start - range.frames, clip.start], [clip.start + clip.frames, clip.start + clip.frames]]) {
                const pixels = Math.abs(frameToPixels(start - desired, zoom));
                if (start >= 0 && pixels <= SNAP_DISTANCE && pixels < distance && !occupied(start)) {
                    best = {frame: start, guide, blocked: false}; distance = pixels;
                }
            }
        }
        return best || {frame: desired, guide: null, blocked: true};
    }
    // Snapping must not pull an otherwise free range into existing content.
    return occupied(aligned.frame) ? {frame: desired, guide: null, blocked: false} : {...aligned, blocked: false};
}

// Classification uses the source tail only to decide which gesture to preview.
// trim/extendClip still validate the requested edit (including pairs and limits).
export function edgeParameters(clip, edge, frame, points, zoom, {canExtend = true, maxFollowSeconds = model.RULES.maxSeconds, beats = []} = {}) {
    const snapped = snapFrame(frame, points, zoom, {beats});
    if (clip.pending && !clip.source) return {kind: 'planned', edge, frame: snapped.frame, guide: snapped.guide};
    const sourceEnd = clip.start + clip.length - clip.in;
    if (speedEdited(clip)) return {kind: 'trim', edge, frame: snapped.frame, guide: snapped.guide};
    if (edge === 'end' && canExtend && clip.extend && snapped.frame > sourceEnd) {
        // What is added at the tail is at least lastSeconds long; the preview already shows the raised length.
        const wanted = Math.round((snapped.frame - sourceEnd) / model.FPS), seconds = model.continuedSeconds(wanted, maxFollowSeconds);
        return {kind: 'extend', frame: sourceEnd, frames: seconds * model.FPS, guide: snapped.guide, raised: seconds !== wanted,
            least: wanted < model.RULES.lastSeconds && seconds >= model.RULES.lastSeconds};
    }
    return {kind: 'trim', edge, frame: snapped.frame, guide: snapped.guide};
}

export function sourcePayload(value) {
    try {
        const payload = typeof value === 'string' ? JSON.parse(value) : value;
        if (!payload || typeof payload.key !== 'string' || !Number.isSafeInteger(payload.frames) || payload.frames <= 0
            || typeof payload.hasVideo !== 'boolean' || typeof payload.hasAudio !== 'boolean'
            || !(payload.hasVideo || payload.hasAudio)) return null;
        const separator = payload.key.indexOf(':');
        if (separator < 1) return null;
        const kind = payload.key.slice(0, separator), id = payload.key.slice(separator + 1);
        if (!['work', 'import', 'generated'].includes(kind) || !id) return null;
        return {source: {kind, id}, frames: payload.frames, hasVideo: payload.hasVideo, hasAudio: payload.hasAudio};
    } catch { return null; }
}

export function tickSpacing(zoom) {
    // Keep the specified 5/15-second rhythm, doubling only when labels collide.
    const factor = 2 ** Math.max(0, Math.ceil(Math.log2(52 / (15 * zoom))));
    return {minor: 5 * factor, major: 15 * factor};
}

// `reach` is how far inside an edge a press still takes that edge, `outside` how far beyond it (6 px each by default).
// The first stretch where a sound track has a clip and the picture track has none: {start, frames} or null.
export function soundWithoutPicture(record) {
    const pictures = record.tracks.find(track => track.id === 'v1')?.clips || [];
    const sounds = record.tracks.filter(track => track.kind === 'audio').flatMap(track => track.clips).sort((a, b) => a.start - b.start);
    for (const clip of sounds) {
        let at = clip.start;
        const stop = clip.start + clip.frames;
        while (at < stop) {
            const cover = pictures.find(picture => picture.start <= at && picture.start + picture.frames > at);
            if (!cover) {
                const next = pictures.filter(picture => picture.start > at).reduce((low, picture) => Math.min(low, picture.start), stop);
                return {start: at, frames: Math.min(stop, next) - at};
            }
            at = cover.start + cover.frames;
        }
    }
    return null;
}
export function hitEdge(position, width, reach = 6, outside = reach) {
    if (position < -outside || position > width + outside) return null;
    const inside = Math.min(reach, width / 4);
    return position <= inside ? 'start' : position >= width - inside ? 'end' : null;
}
// Edges are easier to take with a finger. A mouse gets more room inside the clip; just past its end a press still
// starts a range there, so the outer reach stays as it was.
export const EDGE_REACH = {fine: [8, 6], coarse: [14, 14]};

const clock = frame => {
    const seconds = Math.max(0, Math.floor(frame / model.FPS));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
};
// Where a dragged thing lands, to a tenth of a second when it is not on a whole one (whole seconds hid sub-second moves).
export const preciseClock = (frame, fps = model.FPS) => {
    const tenths = Math.max(0, Math.floor(frame * 10 / fps)), seconds = Math.floor(tenths / 10);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}${tenths % 10 ? `.${tenths % 10}` : ''}`;
};
// The settle after a release: no overshoot, longer for longer ways, and it leaves at the pointer's speed (px per ms)
// when the pointer was still moving toward where the clip lands.
// `offset` is where it was let go relative to where it lands (positive: to the right of it).
export function settleMotion(offset, velocity = 0) {
    const way = Math.abs(offset);
    if (way < .5) return null;
    const duration = Math.round(Math.min(240, Math.max(120, 120 + way * .5)));
    const toward = Math.sign(offset) === -Math.sign(velocity) ? Math.abs(velocity) : 0;
    // Initial slope of the curve is y1 / x1 = speed * duration / distance; y1 stays within 0..1 so it never overshoots.
    const x1 = .25, y1 = Math.min(1, Math.max(0, toward * duration / way * x1));
    return {duration, easing: `cubic-bezier(${x1}, ${Math.round(y1 * 1000) / 1000}, .2, 1)`};
}
const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
// From an offset (where it was let go) back to its own place, by transform only.
export function settle(node, dx, dy = 0, velocity = 0) {
    const motion = settleMotion(Math.hypot(dx, dy) * Math.sign(dx || dy), velocity);
    if (!motion || !node?.animate || reducedMotion()) return null;
    return node.animate([{transform: `translate(${dx}px, ${dy}px)`}, {transform: 'translate(0, 0)'}], {...motion, fill: 'none'});
}
const sourceKey = clip => clip.source ? `${clip.source.kind}:${clip.source.id}` : null;
const busy = clip => ['queued', 'running'].includes(clip.pending);
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const mountedStates = new WeakSet();
const create = (tag, className, text) => {
    const element = document.createElement(tag);
    element.className = className;
    if (text != null) element.textContent = text;
    return element;
};
const icon = kind => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 20 20'); svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('d', kind === 'video' ? 'M2 4h16v12H2z M4 13l4-5 4 4 2-2 3 4'
        : kind === 'link' ? 'M8.5 11.5l3-3 M7.5 9.5l-1.5 1.5a3 3 0 0 0 4.2 4.2l1.5-1.5 M12.5 10.5l1.5-1.5a3 3 0 0 0-4.2-4.2l-1.5 1.5'
        : kind === 'warning' ? 'M10 2a8 8 0 1 0 0 16a8 8 0 0 0 0-16 M10 6v5 M10 14v.1'
        // Track heads: text, subtitles, music, the recording; other sound tracks keep the speaker.
        : kind === 'text' ? 'M4 4h12 M10 4v12 M7 16h6'
        : kind === 'subtitle' ? 'M2 4h16v12H2z M5 9h4 M11 9h4 M5 12h7'
        : kind === 'music' ? 'M8 15V4l9-2v11 M8 15a2.5 2.5 0 1 1-2.5-2.5A2.5 2.5 0 0 1 8 15z M17 13a2.5 2.5 0 1 1-2.5-2.5A2.5 2.5 0 0 1 17 13z'
        : kind === 'mic' ? 'M10 2a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z M4 10a6 6 0 0 0 12 0 M10 16v2'
            : 'M2 8h4l4-4v12l-4-4H2z M13 6c4 2 4 6 0 8 M13 9c1 1 1 1 0 2');
    svg.append(path); return svg;
};

export function createTracks(host, {state, sources = {}, t = en => en, actions = {}, blockProgress} = {}) {
    // The floating task window keeps clear of the tracks (sgf-avoid).
    // Resolves once the rows, clips and opening zoom are laid out at their final geometry (the view's readiness waits on it).
    let settleReady = null;
    const ready = new Promise(resolve => { settleReady = () => { settleReady = null; element.dataset.ready = 'true'; resolve(); }; });
    const element = create('section', 'fv-ed-track-view sgf-avoid');
    element.tabIndex = 0; element.setAttribute('role', 'region');
    element.setAttribute('aria-label', t('Editing tracks', '剪辑轨道'));
    const heads = create('div', 'fv-ed-track-heads');
    const addAudio = create('button', 'fv-ed-add-audio', t('+ Audio track', '＋ 声音轨'));
    addAudio.type = 'button'; heads.append(addAudio);
    // Narrow heads show only the track's icon; a press opens that track's switches beside it.
    // Pointer down, not click: the track view captures the pointer, so the click lands on the view.
    heads.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        const head = event.target.closest('.fv-ed-track-head'), controls = head?.querySelector('[class$=-track-controls]');
        if (!controls || controls.contains(event.target) || (head.dataset.open !== 'true' && controls.getClientRects().length)) return;
        const open = head.dataset.open !== 'true';
        for (const other of heads.querySelectorAll('.fv-ed-track-head[data-open]')) delete other.dataset.open;
        if (open) head.dataset.open = 'true';
    });
    const closeHeads = event => { if (!event.target.closest?.('.fv-ed-track-head[data-open]')) for (const head of heads.querySelectorAll('.fv-ed-track-head[data-open]')) delete head.dataset.open; };
    const scroll = create('div', 'fv-ed-track-scroll');
    const content = create('div', 'fv-ed-track-content');
    const ruler = create('div', 'fv-ed-ruler');
    ruler.setAttribute('aria-label', t('Time', '时间'));
    const overlays = create('div', 'fv-ed-track-overlays');
    const selectionBox = create('div', 'fv-ed-selection');
    const previewBox = create('div', 'fv-ed-gesture-preview');
    const guide = create('div', 'fv-ed-snap-line');
    const insertion = create('div', 'fv-ed-insertion-line');
    const playhead = create('div', 'fv-ed-playhead');
    const handle = create('button', 'fv-ed-playhead-handle');
    handle.type = 'button'; handle.setAttribute('aria-label', t('Play position', '播放位置'));
    applyTip(handle, 'playhead', t);
    playhead.append(handle);
    const plannedLines = create('div', 'fv-ed-planned-borders');
    const beatLines = create('div', 'fv-ed-beat-lines');
    overlays.append(plannedLines, beatLines, selectionBox, previewBox, guide, insertion);
    content.append(ruler, overlays, playhead); scroll.append(content);
    const tooltip = create('div', 'fv-ed-drag-label');
    tooltip.hidden = true; tooltip.setAttribute('role', 'status');
    const popover = create('div', 'fv-ed-clip-popover'); popover.hidden = true;
    let failedPopoverId = null;
    const zoomBar = create('div', 'fv-ed-track-zoom fv-ed-zoom');
    const zoomOut = create('button', '', '−'), zoomIn = create('button', '', '+');
    zoomOut.type = zoomIn.type = 'button';
    zoomOut.setAttribute('aria-label', t('Zoom out', '缩小'));
    zoomIn.setAttribute('aria-label', t('Zoom in', '放大'));
    const slider = create('input', 'fv-ed-zoom-slider');
    slider.type = 'range'; slider.min = '1'; slider.max = '160'; slider.step = '.1';
    slider.setAttribute('aria-label', t('Track zoom', '轨道缩放'));
    const fit = create('button', 'fv-ed-fit'); fit.type = 'button'; fit.append(editorIcon('fit')); fit.setAttribute('aria-label', t('Fit all', '看全部'));
    // Starting words; the page adds the reason when one is greyed at either end of the zoom.
    applyTip(zoomOut, 'zoomOut', t); applyTip(zoomIn, 'zoomIn', t); applyTip(slider, 'zoomSlider', t); applyTip(fit, 'fit', t);
    zoomBar.append(zoomOut, slider, zoomIn, fit);
    element.append(heads, scroll, zoomBar, tooltip, popover); host.append(element);

    const rows = new Map(), clips = new Map(), progresses = new Map(), waveforms = new Map(), beatTimes = new Map();
    let disposed = false, gesture = null, pointerFrame = null, pointerEvent = null;
    let previewSelection = null, selected = new Set(), estimateToken = 0, estimateTimer = null;
    let autoFrame = null, dragX = 0, cachedPoints = [], cachedPlan = '', plannedBorders = [];
    let beatSignature = '', contentWidth = 0, lastRuler = '', dropPoint = null, draggedSource = null, dropPreviewKey = '';
    const listeners = [];
    function on(target, type, listener, options) {
        target.addEventListener(type, listener, options);
        listeners.push(() => target.removeEventListener(type, listener, options));
    }
    // On a wider touch screen the sound and text rows are finger height (48 px); the picture row keeps its size.
    const touchRows = typeof matchMedia === 'function' ? matchMedia('(pointer:coarse) and (min-width:1024px)') : null;
    const laneHeight = () => touchRows?.matches ? 48 : 40;
    const locate = id => clips.get(id);
    const pairIds = id => {
        const clip = locate(id)?.clip;
        return clip ? [id, ...(clip.link && locate(clip.link) ? [clip.link] : [])] : [];
    };
    const selectedIds = selection => new Set(selection?.kind === 'clip' ? selection.ids.flatMap(pairIds) : []);
    const pixels = frame => frameToPixels(frame, state.zoom);
    const frameAt = event => Math.max(0, pixelsToFrame(event.clientX - content.getBoundingClientRect().left, state.zoom));
    const trackAt = event => event.target.closest?.('[data-track]')?.dataset.track || 'v1';
    // Pointer capture retargets events to the view; locate audio rows by geometry.
    const pointerTrackAt = event => {
        const y = event.clientY - content.getBoundingClientRect().top;
        return [...rows].find(([, row]) => y >= row.top && y < row.top + row.height)?.[0];
    };
    const audioTargetAt = (source, event) => {
        const track = pointerTrackAt(event);
        return !source.hasVideo && source.hasAudio && ['a1', 'a2', 'a3'].includes(track) ? track : undefined;
    };
    const sourceInfo = key => typeof sources === 'function' ? sources(key) : sources.get?.(key) || sources[key];
    // The snapping switch lives in the transport model: it empties the point list (and Alt flips it per gesture).
    const snap = snapFrame;
    const allPoints = except => [...new Set([0, state.playhead, ...model.snapPoints(state.record, except)])];
    const seekPlayhead = (frame, event) => {
        if (!Number.isSafeInteger(frame) || frame < 0) return {ok: false, reason: 'range'};
        const at = snap(frame, transportSnapPoints(state, model.snapPoints(state.record, []), event), state.zoom).frame;
        return {ok: true, frame: state.seek(at)};
    };
    const selectGestures = createSelectionGestures({state, element, content, scroll, clips, rows,
        hitTest: pointerHit, snap, t, actions, renderSelection, settle});
    // Said at the pointer while a dragged clip cannot be put down there; other refusals show only the red outline.
    const refusal = error => ({busy: t('Generating; move it when it is done', '正在生成，完成后再移动'), locked: t('This track is locked', '这条轨道已锁定'),
        track: t('It cannot go on this track', '不能放到这条轨上')})[error?.code] || '';
    function attempt(label, change, options) {
        try { state.apply(label, change, options); return true; }
        catch (error) {
            if (!(error instanceof model.EditError)) throw error;
            actions.editError?.(error); return false;
        }
    }
    function extendClip(id, frames, {silent = false} = {}) {
        if (disposed || actions.generationAvailable?.() === false) return false;
        let next;
        try { next = model.extendClip(state.record, id, frames, actions.generationLimits?.() || {}); }
        catch (error) {
            if (!(error instanceof model.EditError)) throw error;
            if (!silent) actions.editError?.(error);
            return false;
        }
        const ids = next.tracks[0].clips.filter(clip => clip.pending === 'draft' && !model.find(state.record, clip.id))
            .flatMap(clip => model.pair(next, clip.id).map(member => member.id));
        return attempt(t('Extend', '拉长'), () => next, ids.length ? {selectionAfter: {kind: 'clip', ids}} : undefined);
    }
    function position(node, start, frames) {
        node.style.left = `${pixels(start)}px`; node.style.width = `${Math.max(2, pixels(frames))}px`;
    }
    function geometry(tracks) {
        const visible = tracks.map(id => rows.get(id)).filter(Boolean);
        const top = Math.min(...visible.map(row => row.top));
        const bottom = Math.max(...visible.map(row => row.top + row.height));
        return {top, height: bottom - top};
    }
    function box(node, selection) {
        node.hidden = !selection;
        if (!selection) return;
        const shape = geometry(selection.tracks);
        position(node, selection.start, selection.frames);
        node.style.top = `${shape.top}px`; node.style.height = `${shape.height}px`;
    }
    function selectionShape(selection) {
        if (!selection) return null;
        if (selection.kind === 'range') return selection;
        if (selection.kind === 'gap') return {...selection, tracks: [selection.track]};
        const items = [...selectedIds(selection)].map(locate).filter(Boolean);
        if (!items.length) return null;
        const start = Math.min(...items.map(item => item.clip.start));
        const end = Math.max(...items.map(item => item.clip.start + item.clip.frames));
        return {start, frames: end - start, tracks: [...new Set(items.map(item => item.track))]};
    }
    // A range cannot be drawn longer than one video may be; `fromEnd` keeps its end where it is.
    function capRange(selection, fromEnd = false, continues = false) {
        const most = musicRange(selection) ? MAX_MUSIC_FRAMES : model.maxRangeFrames(state.record, {...actions.generationLimits?.(), continues});
        if (selection.frames <= most) return selection;
        return fromEnd ? {...selection, start: selection.start + selection.frames - most, frames: most} : {...selection, frames: most};
    }
    function rangeParts(selection, record = state.record) {
        // A stretch of the music track is one piece, never split into video parts.
        if (musicRange(selection)) return [selection.frames];
        return model.rangeParts(record, {...selection, ...actions.generationLimits?.()});
    }
    function boundaryLine(frame, index) {
        const line = create('span', 'fv-ed-range-border');
        line.style.left = `${pixels(frame)}px`; line.dataset.boundary = String(index);
        line.append(create('span', 'fv-ed-boundary-handle'));
        return line;
    }
    function borders(node, selection, plan = selection, record = state.record) {
        const key = JSON.stringify([selection, plan, actions.generationLimits?.()]);
        if (key !== cachedPlan) {
            cachedPlan = key; plannedBorders = [];
            if (selection) try {
                let frame = plan.start;
                for (const count of rangeParts(plan, record).slice(0, -1)) {
                    frame += count;
                    if (frame > selection.start && frame < selection.start + selection.frames) plannedBorders.push(frame);
                }
            } catch (error) { if (!(error instanceof model.EditError)) throw error; }
        }
        node.replaceChildren(...plannedBorders.map((frame, index) => boundaryLine(frame - selection.start, index)));
    }
    function handles(node) {
        for (const edge of ['start', 'end']) {
            const handle = create('span', `fv-ed-trim-handle fv-ed-trim-${edge}`); handle.dataset.edge = edge; node.append(handle);
        }
    }
    function plannedNeighbor(item) {
        if (!item || item.clip.source || !item.clip.pending || model.pair(state.record, item.clip.id).some(busy)
            || !item.clip.part || item.clip.frames % model.FPS) return null;
        const next = [...clips.values()].find(other => other.track === item.track && other.clip.start === item.clip.start + item.clip.frames
            && other.clip.part === item.clip.part && !other.clip.source && other.clip.pending && other.clip.frames % model.FPS === 0
            && !model.pair(state.record, other.clip.id).some(busy));
        return next || null;
    }
    function renderEmptyHints() {
        const selection = previewSelection || state.selection;
        const range = selection?.kind === 'range' ? selection : null;
        const bounds = range ? selectionBox.getBoundingClientRect() : null;
        for (const [id, row] of rows) {
            if (row.empty.hidden || !row.empty.firstChild) continue;
            // Measure the text, not its full-width sticky centering container.
            const textRange = document.createRange();
            textRange.selectNodeContents(row.empty);
            const text = textRange.getBoundingClientRect();
            const obscured = range?.tracks.includes(id)
                && bounds.left <= text.right + 12
                && bounds.right >= text.left - 12;
            row.empty.classList.toggle('fv-ed-hint-obscured', !!obscured);
        }
    }
    function renderSelection() {
        const next = selectedIds(state.selection);
        const value = state.selection;
        const onSecond = value?.kind === 'range' ? value.tracks.includes('a2') : value?.kind === 'gap' ? value.track === 'a2'
            : value?.kind === 'clip' ? [...next].some(id => locate(id)?.track === 'a2') : false;
        addAudio.dataset.shown = String(onSecond);
        for (const id of new Set([...selected, ...next])) locate(id)?.element.setAttribute('aria-selected', String(next.has(id)));
        selected = next;
        element.dataset.multiselect = String(isMultiple(state.record, [...next]));
        const selection = previewSelection || state.selection;
        const shape = selectionShape(selection);
        const pending = selection?.kind === 'clip' && [...next].every(id => locate(id)?.clip.pending && !busy(locate(id).clip));
        selectionBox.title = '';
        selectionBox.classList.toggle('fv-ed-range', selection?.kind === 'range');
        selectionBox.classList.toggle('fv-ed-planned-selection', !!pending);
        selectionBox.classList.toggle('fv-ed-gap', selection?.kind === 'gap');
        selectionBox.classList.toggle('fv-ed-dragging', !!gesture?.moved && gesture.type === 'rangeMove');
        selectionBox.classList.toggle('fv-ed-range-blocked', !!previewSelection && gesture?.type === 'rangeMove' && gesture.rangeBlocked);
        box(selectionBox, shape);
        if (selection?.kind === 'range') borders(selectionBox, shape);
        else selectionBox.replaceChildren();
        if (selection?.kind === 'range' || pending) handles(selectionBox);
        if (selection?.kind === 'range') {
            const width = pixels(shape.frames), seconds = Math.round(shape.frames / model.FPS);
            const label = create('span', 'fv-ed-planned-label', t(`${seconds} s`, `${seconds} 秒`));
            label.hidden = width < 44; selectionBox.append(label);
            if (label.scrollWidth > label.clientWidth) label.textContent = t(`${seconds} s`, `${seconds} 秒`);
        }
        plannedLines.replaceChildren();
        for (const item of clips.values()) {
            if (model.clipState(item.clip) === 'planned') {
                let label = item.element.querySelector('.fv-ed-planned-label');
                if (!label) { label = create('span', 'fv-ed-planned-label'); item.element.append(label); }
                const width = pixels(item.clip.frames), seconds = Math.round(item.clip.frames / model.FPS);
                label.hidden = width < 44;
                label.textContent = t(`${seconds} s`, `${seconds} 秒`);
                if (label.scrollWidth > label.clientWidth) label.textContent = t(`${seconds} s`, `${seconds} 秒`);
            } else item.element.querySelector('.fv-ed-planned-label')?.remove();
            const neighbor = item.track === 'v1' && plannedNeighbor(item);
            if (neighbor) {
                const line = boundaryLine(item.clip.start + item.clip.frames, item.clip.id);
                const shape = geometry(pairIds(item.clip.id).map(id => locate(id).track));
                line.style.top = `${shape.top}px`; line.style.height = `${shape.height}px`; line.style.bottom = 'auto';
                line.dataset.left = item.clip.id; line.dataset.right = neighbor.clip.id; plannedLines.append(line);
            }
        }
        renderEmptyHints();
    }
    function handleShift(x) {
        if (x + (content.offsetLeft || 0) >= 64) return 0;
        const line = playhead.getBoundingClientRect?.(), grip = handle.getClientRects?.().length ? handle.getBoundingClientRect() : null;
        if (!line || !grip) return Math.max(0, Math.min(14, 6 - x));
        const now = parseFloat(playhead.style.getPropertyValue('--fv-ed-handle-shift')) || 0;
        const reach = Math.max(0, line.left - (grip.left - now)), room = line.left - scroll.getBoundingClientRect().left;
        return Math.max(0, Math.min(reach, Math.ceil(reach - room)));
    }
    function renderPlayhead() {
        const x = pixels(state.playhead);
        playhead.style.left = `${x}px`;
        // Near time zero the grip moves right so it stays clear of the heads; the drawn tab stays on the line. How far the
        // grip reaches left of the line is measured (14 px with a mouse; more with the touch grip or the narrow press rim,
        // a11y.css), only near the start, against the room before time zero (the content's lead included).
        playhead.style.setProperty?.('--fv-ed-handle-shift', `${handleShift(x)}px`);
        setAttr(handle, 'aria-label', `${t('Play position', '播放位置')}, ${clock(state.playhead)}`);
    }
    function renderRuler() {
        const {minor, major} = tickSpacing(state.zoom);
        // Render visible marks plus one screen of overscan, even for long edits.
        const low = Math.max(0, Math.floor(scroll.scrollLeft / state.zoom / major) * major);
        const high = Math.ceil((scroll.scrollLeft + scroll.clientWidth * 2) / state.zoom / minor) * minor;
        const key = `${state.zoom}/${low}/${high}`;
        if (key === lastRuler) return; lastRuler = key;
        const marks = [];
        for (let second = low; second <= high; second += minor) {
            const tick = create('span', 'fv-ed-tick'); tick.style.left = `${second * state.zoom}px`;
            if (second % major === 0) {
                tick.classList.add('fv-ed-tick-major'); tick.append(create('span', '', clock(second * model.FPS)));
            }
            marks.push(tick);
        }
        ruler.replaceChildren(...marks);
    }
    function syncVerticalScroll() {
        const top = scroll.scrollTop;
        element.dataset.verticalOverflow = String(scroll.scrollHeight > scroll.clientHeight);
        element.style.setProperty('--fv-ed-track-offset', `${-top}px`);
        // Keep the handle beside the fixed ruler; the line still ends at the last row.
        playhead.style.top = `${18 + top}px`;
        playhead.style.height = `${Math.max(0, parseFloat(content.style.height) - 34 - 18 - top)}px`;
    }
    function setTrackHeight(height) {
        // The heads' 6px gap and 28px audio button belong to the same scroll extent.
        content.style.height = `${height + 34}px`;
        overlays.style.height = `${height}px`;
        syncVerticalScroll();
    }
    function layout() {
        // Defer the initial 90-second view until a detached shell is mounted.
        if (!mountedStates.has(state) && scroll.clientWidth) {
            mountedStates.add(state);
            if (state.zoom === 12) state.setZoom(model.openingZoom(scroll.clientWidth, state.record));
        }
        // The first layout with a real width has the opening zoom and every clip in its final place: the view may show now.
        if (scroll.clientWidth && mountedStates.has(state)) settleReady?.();
        const duration = Math.max(model.duration(state.record), state.selection?.kind === 'range' ? state.selection.start + state.selection.frames : 0);
        // The content may start a little in from the heads (CSS margin); it still fills the view without a scroll bar.
        const inset = typeof getComputedStyle === 'function' && content.isConnected ? parseFloat(getComputedStyle(content).marginLeft) || 0 : 0;
        contentWidth = Math.max(scroll.clientWidth - inset, pixels(duration + 30 * model.FPS));
        content.style.width = `${contentWidth}px`;
        for (const row of rows.values()) row.empty.style.width = `${scroll.clientWidth - inset}px`;
        for (const item of clips.values()) {
            position(item.element, item.clip.start, item.clip.frames);
            item.element.classList.toggle('fv-ed-tiny', pixels(item.clip.frames) < 2);
            item.status.hidden = pixels(item.clip.frames) < 24;
        }
        syncVerticalScroll(); renderRuler(); renderSelection(); renderPlayhead(); renderTourMark(); slider.value = String(state.zoom);
    }
    // For the guided tour (TOUR-SPEC §10.5): the first stretch of sound with no picture above it, as an invisible mark
    // on the picture row; none when there is no such stretch.
    let tourMark = null;
    function renderTourMark() {
        const row = rows.get('v1')?.element, range = soundWithoutPicture(state.record);
        if (!row || !range) { tourMark?.remove(); tourMark = null; return; }
        if (!tourMark) {
            tourMark = create('div', 'fv-ed-tour-mark'); tourMark.dataset.tourId = 'video-empty-range'; tourMark.setAttribute('aria-hidden', 'true');
            Object.assign(tourMark.style, {position: 'absolute', top: '0', bottom: '0', pointerEvents: 'none'});
        }
        if (tourMark.parentNode !== row) row.append(tourMark);
        position(tourMark, range.start, range.frames);
    }
    function waveform(item, key) {
        const viewKey = `${key}/${item.clip.in}/${item.clip.frames}/${item.clip.length}`;
        if (item.waveKey === viewKey) return; item.waveKey = viewKey;
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.classList.add('fv-ed-waveform'); svg.setAttribute('viewBox', '0 0 200 40');
        svg.setAttribute('preserveAspectRatio', 'none'); svg.setAttribute('aria-hidden', 'true');
        const path = document.createElementNS(svg.namespaceURI, 'path'); svg.append(path); item.media.replaceChildren(svg);
        const draw = peaks => {
            if (disposed || item.waveKey !== viewKey) return;
            if (!peaks?.length) { path.setAttribute('d', 'M0 20H200'); return; }
            const from = Math.floor(item.clip.in / item.clip.length * peaks.length);
            const to = Math.max(from + 1, Math.ceil((item.clip.in + item.clip.frames) / item.clip.length * peaks.length));
            peaks = peaks.slice(from, to);
            const stride = 200 / peaks.length;
            path.setAttribute('d', peaks.map((value, i) => {
                const height = Math.max(.5, clamp(Number(value) || 0, 0, 1) * 17);
                return `M${(i + .5) * stride} ${20 - height}v${height * 2}`;
            }).join(' '));
        };
        draw([]);
        if (key && actions.waveform) {
            if (!waveforms.has(key)) waveforms.set(key, Promise.resolve().then(() => actions.waveform(key)).catch(() => []));
            waveforms.get(key).then(draw);
        }
    }
    // Beats of the music under the clips, fetched once a source; a song without a clear pulse, or one not yet analysed, has none.
    function loadBeats(key) {
        if (!key || !actions.beats || beatTimes.has(key)) return;
        beatTimes.set(key, []);
        Promise.resolve().then(() => actions.beats(key)).then(times => { if (!disposed && Array.isArray(times)) beatTimes.set(key, times); }).catch(() => {});
    }
    const beatPoints = (event, ids) => snappingEnabled(state, event)
        ? beatSnapPoints(state.record, clip => beatTimes.get(sourceKey(clip)), state.zoom, ids) : [];
    // Grey lines on the music row while a clip or a cut is dragged: where the beats are that the drag may snap to.
    function showBeats(event) {
        const points = event && gesture?.moved && ['move', 'edge', 'rangeMove', 'rangeEdge'].includes(gesture.type) ? dragBeats(event) : [];
        const row = rows.get(MUSIC_TRACK);
        const signature = points.length && row ? `${state.zoom}|${row.top}|${row.height}|${points.length}|${points[0]}|${points[points.length - 1]}` : '';
        if (signature === beatSignature) return;
        beatSignature = signature;
        beatLines.replaceChildren(...(signature ? points.map(frame => {
            const line = create('div', 'fv-ed-beat-line'); line.style.left = `${pixels(frame)}px`; line.style.top = `${row.top}px`; line.style.height = `${row.height}px`; return line;
        }) : []));
    }
    function makeClip(clip, track) {
        const node = create('button', 'fv-ed-clip'); node.type = 'button'; node.dataset.clip = clip.id;
        node.dataset.track = track.id; node.dataset.state = model.clipState(clip);
        const media = create('span', 'fv-ed-clip-media');
        const fill = create('span', 'fv-ed-progress-fill');
        const status = create('span', 'fv-ed-clip-status');
        const left = create('span', 'fv-ed-trim-handle fv-ed-trim-start'); left.dataset.edge = 'start';
        const right = create('span', 'fv-ed-trim-handle fv-ed-trim-end'); right.dataset.edge = 'end';
        if (!isText(clip)) right.dataset.fvGuide = 'editor-trim';
        node.append(media, fill, status, left, right);
        const item = {element: node, media, fill, status, clip, kind: track.kind, component: null, waveKey: undefined};
        if (blockProgress && !isText(clip)) {
            item.component = blockProgress(clip.id, node);
            item.component.element.classList.add('fv-ed-block-progress'); node.append(item.component.element);
            fill.hidden = true;
        }
        return item;
    }
    function land(item) {
        const node = item.element;
        node.classList.remove('fv-ed-landed'); void node.offsetWidth; node.classList.add('fv-ed-landed');
        clearTimeout(item.landTimer); item.landTimer = setTimeout(() => node.classList.remove('fv-ed-landed'), 400);
    }
    function updateProgress(item) {
        const value = progresses.get(item.clip.id) || {state: item.clip.pending === 'queued' ? 'waiting'
            : busy(item.clip) ? 'generating' : model.clipState(item.clip) === 'done' ? 'done'
                : item.clip.pending === 'stopped' ? 'stopped' : model.clipState(item.clip) === 'failed' ? 'failed' : 'waiting',
        fraction: 0, remainingSeconds: null, ...(item.clip.pending === 'queued' ? {queued: true} : {})};
        item.element.style.setProperty('--fv-ed-progress', String(clamp(Number(value.fraction) || 0, 0, 1)));
        item.component?.update(value);
    }
    function renderRecord() {
        const openFailure = !popover.hidden && failedPopoverId;
        selectGestures.finish(null, true);
        cachedPlan = ''; popover.hidden = true;
        const record = state.record, retained = new Set();
        // The model keeps a2 and a3 so the separated parts sit at fixed places; while they hold nothing they stay out of sight.
        const separated = record.tracks.some(track => track.stem);
        const tracks = displayTracks(record).filter(track => !(separated && track.kind === 'audio' && !track.stem && track.id !== 'a1' && !track.clips.length));
        const twoAudio = tracks.some(track => track.id === 'a2');
        const STEM_NAMES = {dialogue: t('Dialogue', '人声'), music: t('Music', '音乐'), ambience: t('Ambience', '环境声')};
        let top = 24, parted = false;
        for (const track of tracks) {
            const name = track.kind === 'text' ? t('Text', '文字') : track.kind === 'subtitle' ? t('Subtitles', '字幕') : track.kind === 'video' ? t('Video', '画面') : track.stem ? STEM_NAMES[track.stem] : track.id === 'a3'
                ? t('Voice', '录音') : twoAudio ? t(`Audio ${track.id.slice(1)}`, `声音 ${track.id.slice(1)}`) : t('Audio', '声音');
            let row = rows.get(track.id);
            if (!row) {
                const node = create('div', 'fv-ed-track-row'); node.dataset.track = track.id;
                if (track.id === 'v1') node.dataset.fvGuide = 'editor-empty';
                const empty = create('div', 'fv-ed-track-empty');
                node.append(empty);
                const head = create('div', 'fv-ed-track-head'); head.dataset.track = track.id;
                const glyph = track.kind === 'audio' ? (track.stem ? 'audio' : track.id === MUSIC_TRACK ? 'music' : track.id === 'a3' ? 'mic' : 'audio') : track.kind;
                head.append(icon(glyph), create('span', '', name)); cutTip(head, name, head.querySelector('span'));
                heads.append(head); content.insertBefore(node, overlays);
                row = {element: node, head, empty}; rows.set(track.id, row); parted ||= !!track.stem;
            }
            row.head.querySelector('span').textContent = name; cutTip(row.head, name, row.head.querySelector('span'));
            // Guide targets ("take me there"): the lanes where a range is dragged out for speech or music, and the
            // separated music lane where new music replaces the old.
            const tourId = track.stem === 'music' ? 'audio-music-stem' : track.stem ? '' : {a1: 'audio-a1-track', a2: 'audio-a2-track'}[track.id] || '';
            if (tourId) row.element.dataset.tourId = tourId; else delete row.element.dataset.tourId;
            row.head.dataset.sub = row.element.dataset.sub = String(track.id === 'a3');
            row.empty.hidden = track.clips.length > 0;
            // A picture track with any block description grows a strip for it under the pictures.
            const noted = !!actions.describedTrack?.(track); row.element.dataset.noted = String(noted);
            row.top = top; row.height = track.kind === 'video' ? 64 + (noted ? 22 : 0) : laneHeight();
            row.element.style.top = row.head.style.top = `${top}px`;
            row.element.style.height = row.head.style.height = `${row.height}px`;
            row.element.setAttribute('aria-label', name);
            actions.textTrack?.(row, track);
            top += row.height + 6;
            let previous = null;
            track.clips.forEach((clip, index) => {
                retained.add(clip.id);
                let item = clips.get(clip.id);
                // Clip ids are local to a project. Opening another project can
                // reuse an id for a different media kind (and progress layout).
                if (item && item.kind !== track.kind) { item.waveKey = undefined; item.component?.dispose(); item.element.remove(); item = null; }
                if (!item) { item = makeClip(clip, track); clips.set(clip.id, item); row.element.append(item.element); }
                item.clip = clip; item.track = track.id;
                item.element.classList.toggle('fv-ed-tiny', pixels(clip.frames) < 2);
                renderSpeedBadge(item.element, clip, t);
                // Commits reorder only displaced buttons for sensible Tab order;
                // pointermove never enters this reconciliation path.
                const next = previous ? previous.nextElementSibling : row.element.firstElementChild;
                if (next !== item.element) row.element.insertBefore(item.element, next);
                previous = item.element;
                const visual = model.clipState(clip), key = sourceKey(clip);
                // A part that finishes generating lands in place: its picture or sound fades in and settles, once.
                if (item.visual === 'running' && visual === 'done') land(item);
                item.visual = visual;
                item.element.dataset.state = visual; item.element.dataset.track = track.id;
                item.element.dataset.split = String(track.kind === 'audio' && clip.stem_of === undefined && state.record.tracks.some(row => row.stem && row.clips.some(other => other.stem_of === clip.id)));
                item.element.dataset.busy = String(busy(clip)); item.element.dataset.extend = String(clip.extend && actions.generationAvailable?.() !== false && !busy(clip));
                // A part not generated yet is attached only to the part it continues; drawn on its own it stays a separate block.
                // Touching blocks are one video only when the later one continues the earlier one, or both are consecutive stretches of one source;
                // two separate shots that merely touch are drawn apart.
                const touching = (left, right) => !!left && !!right && left.start + left.frames === right.start;
                const sameStretch = (left, right) => !!left.source && sourceKey(left) === sourceKey(right) && left.in + left.frames === right.in;
                const attached = (left, right) => touching(left, right) && (model.continuesBefore(state.record, right.id) || sameStretch(left, right));
                const before = track.clips[index - 1];
                item.element.classList.toggle('fv-ed-joined-left', attached(before, clip));
                item.element.classList.toggle('fv-ed-joined-right', attached(clip, track.clips[index + 1]));
                // On the picture track a continuation carries a link; a separate shot is told apart by being drawn apart.
                const seam = track.id !== 'v1' || !touching(before, clip) ? null : model.continuesBefore(state.record, clip.id) ? 'continued' : sameStretch(before, clip) ? null : 'separate';
                let mark = item.element.querySelector(':scope > .fv-ed-seam-mark');
                if (seam === 'continued' && pixels(clip.frames) >= 26) {
                    if (!mark) { mark = create('span', 'fv-ed-seam-mark'); mark.setAttribute('aria-hidden', 'true'); mark.dataset.kind = seam; mark.append(icon('link')); item.element.append(mark); }
                } else mark?.remove();
                item.element.classList.toggle('fv-ed-seamed', !!item.element.querySelector(':scope > .fv-ed-seam-mark'));
                const failureText = visual === 'failed' ? actions.failureText?.(clip.id) || actions.reasonText?.(clip.reason) || t('Generation failed; the cause was not saved. Retry, or download the report.', '生成失败，原因没有保存下来。请重试，或下载报告。') : '';
                if (visual === 'failed') item.status.replaceChildren(icon('warning'), create('span', 'fv-ed-clip-failure-reason', failureText));
                else item.status.replaceChildren();
                const status = {planned: actions.interrupted?.(clip) ? t('Interrupted', '上次中断') : t('Not generated yet', '还没生成'), running: t('Generating', '正在生成'),
                    done: t('Complete', '完成'), failed: failureText}[visual];
                const seconds = Math.round(clip.frames / model.FPS);
                // Hover help: which clip it is, and its description (first 24 characters) or its length and state.
                const said = (clip.text || '').trim(), words = [...said];
                setTip(item.element, {name: t(`${name} · clip ${index + 1}`, `${name} · 第 ${index + 1} 段`),
                    use: words.length ? (words.length > 24 ? `${words.slice(0, 24).join('')}…` : said) : t(`${seconds} s · ${status}`, `${seconds} 秒 · ${status}`)});
                const seamText = seam === 'continued' ? t(', continues the clip before', '，接续上一段') : seam === 'separate' ? t(', separate shot', '，独立镜头') : '';
                item.element.setAttribute('aria-label', t(`${name}, clip ${index + 1}, ${seconds} s, ${status}${seamText}`,
                    `${name}，第 ${index + 1} 段，${seconds} 秒，${status}${seamText}`));
                // Pending replacements can retain their old media; state styling
                // overlays it until the model reports that the new media is done.
                // The imported sound under a sound-led range is kept as it is, so its waveform stays in view while the picture is made.
                item.media.hidden = visual !== 'done' && !(track.kind === 'audio' && clip.source?.kind === 'import' && key);
                if (track.kind === 'video') {
                    updatePictureBadge(item.element, clip, record, clipPictureSize(clip, sources), t);
                    actions.blockNote?.(item, {noted, index, record});
                    const thumbnail = sourceInfo(key)?.thumbnail;
                    item.media.style.backgroundImage = thumbnail ? `url(${JSON.stringify(thumbnail)})` : '';
                } else if (isText(clip)) actions.textClip?.(item, record);
                else { waveform(item, key); if (track.id === MUSIC_TRACK) loadBeats(key); }
                updateProgress(item);
            });
        }
        for (const [id, row] of rows) if (!tracks.some(track => track.id === id)) { row.element.remove(); row.head.remove(); rows.delete(id); }
        for (const [id, item] of clips) if (!retained.has(id)) { item.component?.dispose(); item.element.remove(); clips.delete(id); progresses.delete(id); }
        heads.append(addAudio);
        addAudio.style.top = `${top}px`; addAudio.disabled = tracks.find(track => track.id === 'a2')?.clips.some(busy) || false;
        // With Audio 2 present the key removes it: it shows beside that track's head only while Audio 2 (or something on it)
        // is selected, or the pointer is over its head; it is not there the rest of the time.
        addAudio.dataset.mode = twoAudio ? 'remove' : 'add';
        const second = rows.get('a2');
        if (second) addAudio.style.setProperty?.('--fv-ed-remove-top', `${second.top + second.height / 2 - 14}px`);
        addAudio.textContent = twoAudio ? t('Remove Audio 2', '移除声音 2') : t('+ Audio track', '＋ 声音轨');
        addAudio.dataset.sign = twoAudio ? '×' : '＋'; // Phone widths show the sign alone.
        // Wide heads draw the sign in the icon column and these words in the name column, like a track head.
        addAudio.dataset.label = twoAudio ? t('Remove Audio 2', '移除声音 2') : t('Audio track', '声音轨');
        applyTip(addAudio, twoAudio ? 'removeAudio' : 'addAudio', t,
            {why: addAudio.disabled ? t('A clip on Audio 2 is generating; wait for it to finish', '声音 2 上有片段在生成，等它完成') : ''});
        addAudio.setAttribute('aria-label', addAudio.textContent);
        // Without Audio 2 the guide points at the button that adds it.
        if (twoAudio) delete addAudio.dataset.tourId; else addAudio.dataset.tourId = 'audio-a2-track';
        setTrackHeight(top - 6);
        layout();
        // The parts of a fresh split appear under the sound they came from: show them from the top.
        if (parted) scroll.scrollTop = 0;
        const failure = openFailure && clips.get(openFailure);
        if (failure && model.clipState(failure.clip) === 'failed' && state.selection?.ids?.includes(openFailure)) failedPopover(failure);
        if (gesture?.type === 'move' && pointerEvent) updatePointer(pointerEvent);
    }
    // While the playhead is dragged, its time (to a tenth) rides above it on the ruler.
    function showTime() {
        const bounds = element.getBoundingClientRect(), x = content.getBoundingClientRect().left - bounds.left + pixels(state.playhead);
        setText(tooltip, preciseClock(state.playhead)); setHidden(tooltip, false); setData(tooltip, 'time', 'true');
        tooltip.style.left = `${clamp(x + 10, 8, Math.max(8, bounds.width - 80))}px`; tooltip.style.top = '28px';
    }
    function showGuide(frame) { guide.hidden = frame == null; if (frame != null) guide.style.left = `${pixels(frame)}px`; }
    function showInsertion(frame) { insertion.hidden = frame == null; if (frame != null) insertion.style.left = `${pixels(frame)}px`; }
    function hint(selection, event, extended = false, moving = false, limited = null) {
        const token = ++estimateToken; clearTimeout(estimateTimer);
        if (gesture?.type.startsWith('range')) actions.previewRange?.(selection);
        const seconds = Math.round(selection.frames / model.FPS);
        let count = 1;
        try { count = rangeParts(selection).length; } catch (error) { if (!(error instanceof model.EditError)) throw error; }
        const label = limited != null ? t(`Max ${limited} s`, `最长 ${limited} 秒`)
            : moving ? `${preciseClock(selection.start)} – ${preciseClock(selection.start + selection.frames)}`
                : t(`${extended ? '+' : ''}${seconds} s`, `${extended ? '+' : ''}${seconds} 秒`);
        tooltip.textContent = label; tooltip.hidden = false;
        const bounds = element.getBoundingClientRect();
        tooltip.style.left = `${clamp(event.clientX - bounds.left + 14, 8, Math.max(8, bounds.width - 300))}px`;
        tooltip.style.top = `${clamp(event.clientY - bounds.top + 14, 26, bounds.height - 32)}px`;
        if (!isText(gesture?.clip) && actions.estimate && !moving && limited == null) estimateTimer = setTimeout(() => {
            Promise.resolve().then(() => actions.estimate(selection)).then(value => {
                if (disposed || token !== estimateToken || value == null || !Number.isFinite(value)) return;
                tooltip.textContent = `${label} · ${timeEstimate(value, t)}`;
            }).catch(() => {});
        }, 120);
    }
    function clearPreview() {
        beatSignature = ''; beatLines.replaceChildren();
        previewSelection = null; previewBox.hidden = true; guide.hidden = true; insertion.hidden = true;
        actions.previewRange?.(null);
        tooltip.hidden = true; delete tooltip.dataset.time; plannedLines.hidden = false; cachedPlan = ''; clearTimeout(estimateTimer); estimateToken++;
        for (const id of [...(gesture?.ids || []), ...(gesture?.rightPreviewIds || [])]) {
            const item = locate(id);
            if (item) { item.element.classList.remove('fv-ed-dragging', 'fv-ed-cannot-drop'); item.element.style.transform = ''; position(item.element, item.clip.start, item.clip.frames); }
        }
        delete tooltip.dataset.refused;
        delete element.dataset.cursor; selectionBox.classList.remove('fv-ed-dragging', 'fv-ed-cannot-drop');
        plannedLines.querySelectorAll('[data-limited]').forEach(line => line.removeAttribute('data-limited'));
        renderSelection();
    }
    function failedPopover(item) {
        failedPopoverId = item.clip.id;
        const reason = create('p', '', actions.failureText?.(item.clip.id) || actions.reasonText?.(item.clip.reason) || t('Generation failed; the cause was not saved. Retry, or download the report.', '生成失败，原因没有保存下来。请重试，或下载报告。'));
        const report = create('button', 'fv-ed-clip-report', t('Download report', '下载报告')); report.type = 'button'; applyTip(report, 'report', t);
        report.onclick = event => { event.stopPropagation(); popover.hidden = true;
            Promise.resolve(actions.downloadFailure?.(item.clip.id)).catch(error => actions.editError?.(error)); };
        const retry = create('button', '', t('Retry', '重试')); retry.type = 'button'; applyTip(retry, 'retry', t);
        retry.onclick = () => { popover.hidden = true;
            Promise.resolve().then(() => actions.retry?.(item.clip.id)).catch(error => actions.editError?.(error, 'generate')); };
        // A reason whose hint names the page settings also offers to open them.
        const repair = pageRepair(reason.textContent) ? repairButton(t, {before: () => { popover.hidden = true; }}) : null;
        popover.replaceChildren(...[reason, report, repair, retry].filter(Boolean)); popover.hidden = false;
        const bounds = element.getBoundingClientRect(), clipBounds = item.element.getBoundingClientRect();
        // The card is as wide as the clip it belongs to (never narrower than before, so its buttons stay on one row) and sits against the clip's lower edge;
        // where that would cover the track below, it opens upward from the clip's top when there is room.
        const width = Math.min(Math.max(clipBounds.width, repair ? 360 : 280), bounds.width - 16);
        popover.style.width = `${width}px`;
        popover.style.left = `${clamp(clipBounds.left - bounds.left, 8, Math.max(8, bounds.width - width - 8))}px`;
        const height = popover.getBoundingClientRect().height, below = clipBounds.bottom - bounds.top + 4, above = clipBounds.top - bounds.top - 4 - height;
        const next = [...element.querySelectorAll('.fv-ed-track-row')].map(row => row.getBoundingClientRect().top - bounds.top).filter(top => top >= clipBounds.bottom - bounds.top - 1);
        const room = next.length ? Math.min(...next) : bounds.height;
        popover.style.top = `${Math.max(8, below + height <= room || above < 8 ? Math.min(below, bounds.height - height - 8) : above)}px`;
    }
    // A click inside a range drawn over empty space that makes several parts plans those parts and chooses the one
    // clicked, so each can be described on its own. What was typed for the range is the first part's description.
    function choosePart(event) {
        const range = state.selection;
        if (event.kind !== 'rangeMove' || range?.kind !== 'range' || rangeParts(range).length < 2
            || state.record.tracks.some(track => track.clips.some(clip => clip.start < range.start + range.frames && clip.start + clip.frames > range.start))) return;
        const frame = frameAt(event), text = state.rangeText?.trim() || '';
        let next, clicked;
        try {
            next = model.planRange(state.record, {start: range.start, frames: range.frames, ...(range.parts ? {parts: range.parts} : {}), target: 'both', ...actions.generationLimits?.()});
            const drawn = next.tracks[0].clips.filter(clip => !model.find(state.record, clip.id)).sort((a, b) => a.start - b.start);
            clicked = drawn.find(clip => frame >= clip.start && frame < clip.start + clip.frames) ?? (frame < range.start ? drawn[0] : drawn.at(-1));
            if (text) next = model.describe(next, [drawn[0].id], text);
        } catch (error) { if (error instanceof model.EditError) return; throw error; }
        attempt(t('Edit description', '修改说明'), () => next, {selectionAfter: {kind: 'clip', ids: model.pair(next, clicked.id).map(clip => clip.id)}});
    }
    function clickSelection(event) {
        if (event.range) { choosePart(event); return; }
        const id = event.clipId || event.target.closest?.('.fv-ed-clip')?.dataset.clip;
        if (id) {
            selectGestures.choose(id, event);
            const item = locate(id); if (model.clipState(item.clip) === 'failed') failedPopover(item);
            return;
        }
        const track = event.target.closest?.('.fv-ed-track-row')?.dataset.track;
        const frame = frameAt(event);
        const gap = track && model.gaps(state.record, track).find(item => frame >= item.start && frame < item.start + item.frames);
        state.select(gap ? {kind: 'gap', track, start: gap.start, frames: gap.frames} : null, {history: true});
    }
    // Coordinate hit testing includes the outside of an edge and works when the
    // transparent selection outline, or a child label, is the pointer target.
    function pointerHit(event) {
        const bounds = content.getBoundingClientRect(), y = event.clientY - bounds.top, x = event.clientX - bounds.left;
        const [reach, outside] = EDGE_REACH[event.pointerType === 'touch' || event.pointerType === 'pen' ? 'coarse' : 'fine'];
        const edgeAt = (position, width) => hitEdge(position, width, reach, outside);
        const track = [...rows].find(([, row]) => y >= row.top && y <= row.top + row.height)?.[0];
        const selection = state.selection;
        const planned = selection?.kind === 'range' ? state.record.tracks[0].clips.filter(clip =>
            clip.start >= selection.start && clip.start + clip.frames <= selection.start + selection.frames) : [];
        const materialized = planned.length && planned[0].start === selection.start
            && planned.at(-1).start + planned.at(-1).frames === selection.start + selection.frames
            && planned.every((clip, i) => clip.pending && !clip.source && (!i || planned[i - 1].start + planned[i - 1].frames === clip.start));
        if (selection?.kind === 'range' && !materialized) {
            const shape = geometry(selection.tracks), position = x - pixels(selection.start), width = pixels(selection.frames);
            if (y >= shape.top && y <= shape.top + shape.height && position >= -outside && position <= width + outside) {
                const edge = edgeAt(position, width);
                if (edge) return {range: selection, edge, track};
                let at = 0;
                const parts = rangeParts(selection);
                for (let i = 0; i < parts.length - 1; i++) {
                    at += parts[i];
                    if (Math.abs(position - pixels(at)) <= Math.min(reach, pixels(Math.min(parts[i], parts[i + 1])) / 4)) return {range: selection, boundary: i, parts, track};
                }
                return {range: selection, track};
            }
        }
        const candidates = [...clips.values()].filter(item => item.track === track);
        // Shared planned boundaries take precedence over the two outer handles.
        for (const item of candidates) {
            const neighbor = plannedNeighbor(item), at = item.clip.start + item.clip.frames;
            if (neighbor && Math.abs(x - pixels(at)) <= Math.min(reach, pixels(Math.min(item.clip.frames, neighbor.clip.frames)) / 4)) {
                const primary = item.track === 'a1' && item.clip.link ? locate(item.clip.link) : item;
                return {item: primary, neighbor: plannedNeighbor(primary), track: primary.track};
            }
        }
        const pointed = candidates.find(item => item.clip.id === event.target.closest?.('.fv-ed-clip')?.dataset.clip);
        // Expanded tiny parts need DOM priority; ordinary shared edges keep the
        // left part's precedence even when the next handle overlaps.
        const direct = (pointed && pixels(pointed.clip.frames) < 2 ? pointed : null)
            || candidates.find(item => x >= pixels(item.clip.start) && x <= pixels(item.clip.start) + Math.max(2, pixels(item.clip.frames)));
        const item = direct || candidates.find(item => edgeAt(x - pixels(item.clip.start), Math.max(2, pixels(item.clip.frames))));
        return {item, track, edge: item ? edgeAt(x - pixels(item.clip.start), Math.max(2, pixels(item.clip.frames))) : null};
    }
    function startPointer(event) {
        if (event.button !== 0 || !event.isPrimary || gesture || isTyping(event.target) || event.target.closest('.fv-ed-track-zoom, .fv-ed-clip-popover')) return;
        if (selectGestures.begin(event)) { popover.hidden = true; return; }
        const seeking = event.target.closest('.fv-ed-ruler, .fv-ed-playhead-handle');
        const hit = seeking ? {} : pointerHit(event);
        if (!seeking && !hit.track && !hit.range) return;
        event.preventDefault(); event.stopPropagation(); element.focus({preventScroll: true}); popover.hidden = true;
        const {item, edge, range, neighbor, boundary, parts} = hit, id = item?.clip.id;
        const anchor = frameAt(event), track = hit.track || trackAt(event);
        if (state.record.tracks.find(t => t.id === track)?.locked) { flashBlocked(anchor); return; }
        if (!item && isTextTrack(state.record.tracks.find(t => t.id === track))) { state.seek(anchor); state.select(null); return; }
        actions.gestureStart?.();
        gesture = {pointer: event.pointerId, x: event.clientX, y: event.clientY, anchor, track, target: event.target, moved: false,
            type: seeking ? 'seek' : range ? boundary != null ? 'rangeBoundary' : edge ? 'rangeEdge' : 'rangeMove'
                : neighbor ? 'plannedBoundary' : item ? edge ? 'edge' : 'move' : 'range', id, edge, range, boundary, parts, neighbor,
            clip: item ? {...item.clip, length: Math.min(...model.pair(state.record, id).map(clip => clip.length))} : null,
            ids: item ? [...pairIds(id), ...(neighbor ? pairIds(neighbor.clip.id) : [])] : [], previousSelection: state.selection, previousPlayhead: state.playhead};
        cachedPoints = seeking ? model.snapPoints(state.record, []) : allPoints(gesture.ids);
        if (item) state.select({kind: 'clip', ids: [id]});
        // A part that is generating still follows the pointer, outlined red with the reason, and goes back on release.
        if (item && model.pair(state.record, id).some(busy)) { if (gesture.type !== 'move') gesture.type = 'click'; gesture.blocked = true; }
        if (gesture.type === 'move') gesture.members = gesture.ids.map(id => locate(id).clip);
        gesture.samples = [[event.clientX, event.timeStamp || performance.now()]];
        // Visible within the press: the clip lightens, the playhead tab grows, a range shows where it starts.
        // A clip still settling from the last release is taken where it lands (a new press interrupts the glide).
        for (const member of gesture.ids) { const node = locate(member)?.element; node?.getAnimations?.().forEach(animation => animation.finish()); node?.setAttribute('data-pressed', 'true'); }
        if (seeking) { element.dataset.scrub = 'true'; seekPlayhead(anchor); showTime(); }
        else if (gesture.type === 'range') showGuide(snap(anchor, transportSnapPoints(state, cachedPoints, event), state.zoom).frame);
        element.dataset.cursor = ['move', 'rangeMove'].includes(gesture.type) ? 'grabbing' : gesture.type === 'click' ? 'default' : 'ew-resize';
        element.setPointerCapture(event.pointerId);
    }
    function dragPoints(frame, length = 0) {
        const grid = 5 * model.FPS, points = [...cachedPoints];
        for (const value of [frame, frame + length]) points.push(Math.round(value / grid) * grid);
        return transportSnapPoints(state, points, pointerEvent);
    }
    // Beats rank below every other point. A range being moved leaves the beats of the music it carries.
    function dragBeats(event = pointerEvent) {
        let ids = gesture?.ids || [];
        if (gesture?.type === 'rangeMove') {
            const {start, frames, tracks} = gesture.range;
            ids = state.record.tracks.filter(track => tracks.includes(track.id))
                .flatMap(track => track.clips.filter(clip => clip.start < start + frames && clip.start + clip.frames > start).map(clip => clip.id));
        }
        return beatPoints(event, ids);
    }
    // A clip's end can be dragged longer only over hidden source, by generating more, or as a planned part.
    const canGrow = clip => (clip.pending && !clip.source) || isText(clip) || speedEdited(clip) || clip.in + clip.frames < clip.length
        || (clip.extend && actions.generationAvailable?.() !== false);
    // Only a part that continues another is held to the length of a continued part; a range being drawn never is.
    function boundaryLimits(clip = null) {
        const limits = actions.generationLimits?.() || {}, maxSeconds = limits.maxSeconds ?? model.RULES.maxSeconds;
        const maxFollowSeconds = limits.maxFollowSeconds ?? maxSeconds;
        const before = !!clip && model.continuesBefore(state.record, clip.id);
        return {maxSeconds: before ? maxFollowSeconds : maxSeconds, maxFollowSeconds};
    }
    function updatePointer(event) {
        if (!gesture || gesture.pointer !== event.pointerId) return;
        const frame = frameAt(event); dragX = event.clientX; pointerEvent = event;
        const points = transportSnapPoints(state, cachedPoints, event);
        gesture.samples?.push([event.clientX, event.timeStamp || performance.now()]); if (gesture.samples?.length > 6) gesture.samples.shift();
        gesture.moved ||= Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) >= 4;
        // A busy part pressed on its edge cannot change; dragging its middle is shown at the pointer (below).
        if (gesture.blocked && gesture.moved && !gesture.warned && gesture.type === 'click') {
            gesture.warned = true; actions.editError?.(new model.EditError('busy'));
        }
        if (gesture.type === 'seek') {
            seekPlayhead(frame, pointerEvent); showTime(); return;
        }
        showBeats(event);
        if (!gesture.moved || gesture.type === 'click') return;
        if (gesture.type === 'rangeBoundary' || gesture.type === 'plannedBoundary') {
            const isRange = gesture.type === 'rangeBoundary';
            const parts = isRange ? gesture.parts : [gesture.clip.frames, gesture.neighbor.clip.frames];
            const index = isRange ? gesture.boundary : 0, start = isRange ? gesture.range.start : gesture.clip.start;
            const from = start + parts.slice(0, index).reduce((sum, count) => sum + count, 0);
            const result = model.adjustPartBoundary(parts, index, frame - from, boundaryLimits(isRange ? null : gesture.clip));
            gesture.result = {...result, frame: from + result.parts[index]};
            if (isRange) {
                gesture.selection = {...gesture.range, parts: result.parts}; previewSelection = gesture.selection; renderSelection();
                selectionBox.querySelectorAll('.fv-ed-range-border')[index]?.setAttribute('data-limited', String(result.limited != null));
            } else {
                const preview = model.resizePlannedBoundary(state.record, gesture.id, gesture.neighbor.clip.id, gesture.result.frame, actions.generationLimits?.());
                for (const id of gesture.ids) { const clip = model.find(preview, id); position(locate(id).element, clip.start, clip.frames); }
                const line = plannedLines.querySelector(`[data-left="${gesture.id}"]`);
                if (line) { line.style.left = `${pixels(gesture.result.frame)}px`; line.setAttribute('data-limited', String(result.limited != null)); }
                box(selectionBox, {start, frames: result.parts[0], tracks: pairIds(gesture.id).map(id => locate(id).track)});
            }
            hint(isRange ? gesture.selection : {start, frames: parts.reduce((a, b) => a + b, 0)}, event, false, false, result.limited);
        } else if (gesture.type === 'rangeMove' || gesture.type === 'rangeEdge') {
            const original = gesture.range;
            let aligned;
            if (gesture.type === 'rangeMove') {
                const desired = original.start + frame - gesture.anchor;
                const rangePoints = dragPoints(desired, original.frames), beats = dragBeats();
                aligned = rangeMoveParameters(state.record, original, gesture.anchor, frame, rangePoints, state.zoom, {enabled: rangePoints.length > 0 || beats.length > 0, beats});
                gesture.rangeBlocked = aligned.blocked;
                element.dataset.cursor = aligned.blocked ? 'not-allowed' : 'grabbing';
                gesture.selection = {...original, start: aligned.frame};
            } else {
                aligned = snap(frame, dragPoints(frame), state.zoom, {beats: dragBeats()});
                // The edge held still stays exactly where it is; the dragged one follows the pointer by whole seconds.
                const end = original.start + original.frames, fixed = gesture.edge === 'start' ? end : original.start;
                const drawn = rangeParameters(fixed, gesture.edge === 'start' ? Math.min(aligned.frame, end - 2 * model.FPS) : Math.max(aligned.frame, fixed), original.tracks);
                gesture.selection = capRange(drawn, gesture.edge === 'start');
            }
            previewSelection = gesture.selection; showGuide(aligned.guide); renderSelection();
            hint(gesture.selection, event, false, gesture.type === 'rangeMove');
            if (gesture.type === 'rangeMove' && gesture.rangeBlocked) tooltip.hidden = true;
        } else if (gesture.type === 'range') {
            const aligned = snap(frame, points, state.zoom);
            // Where it was pressed, drawn to a clip edge, marker or the playhead nearby.
            gesture.from ??= snap(gesture.anchor, points, state.zoom).frame;
            // Drawn on the music track, the range is that track's alone: music for that stretch.
            const drawn = rangeParameters(gesture.from, aligned.frame, gesture.track === MUSIC_TRACK ? [MUSIC_TRACK] : undefined);
            // Drawn from a clip's end, the range starts exactly there.
            if (gesture.flush != null && aligned.frame > gesture.flush) Object.assign(drawn, {start: gesture.flush,
                frames: Math.max(2 * model.FPS, Math.round((aligned.frame - gesture.flush) / model.FPS) * model.FPS)});
            gesture.selection = capRange(drawn, aligned.frame < gesture.from);
            previewSelection = gesture.selection; showGuide(aligned.guide);
            renderSelection(); hint(gesture.selection, event);
        } else if (gesture.type === 'move') {
            plannedLines.hidden = true;
            const desired = gesture.clip.start + frame - gesture.anchor;
            const result = moveParameters(gesture.clip, gesture.anchor, frame, dragPoints(desired, gesture.clip.frames), state.zoom, dragBeats());
            result.track = isText(gesture.clip) ? pointerHit(event).track : null;
            const pointedTrack = pointerTrackAt(event);
            result.targetTrack = gesture.ids.length === 1 && ['a1', 'a2', 'a3'].includes(gesture.track)
                && ['a1', 'a2', 'a3'].includes(pointedTrack) ? pointedTrack : undefined;
            gesture.result = result;
            const track = state.record.tracks.find(row => row.id === (result.targetTrack || gesture.track));
            const drop = isText(gesture.clip) ? {hit: false} : insertionPoint(track.clips, result.frame, gesture.ids);
            showInsertion(drop.hit ? drop.frame : null); showGuide(result.guide);
            for (const id of gesture.rightPreviewIds || []) {
                const item = locate(id); if (item) position(item.element, item.clip.start, item.clip.frames);
            }
            gesture.rightPreviewIds = [];
            let preview, error, shifted = 0;
            try {
                if (gesture.blocked) throw new model.EditError('busy');
                if (textDropLocked(state.record, gesture.members, result.track)) throw new model.EditError('locked');
                preview = model.move(state.record, gesture.id, result.frame, {targetTrack: result.targetTrack});
            }
            catch (caught) { if (!(caught instanceof model.EditError)) throw caught; error = caught; }
            if (preview) for (const row of preview.tracks) for (const clip of row.clips) {
                const item = locate(clip.id);
                if (!gesture.ids.includes(clip.id) && clip.start !== item.clip.start) {
                    position(item.element, clip.start, clip.frames); gesture.rightPreviewIds.push(clip.id);
                    shifted = Math.max(shifted, clip.start - item.clip.start);
                }
            }
            const landing = preview ? model.find(preview, gesture.id).start : result.frame;
            element.dataset.cursor = error ? 'not-allowed' : 'grabbing';
            const previewTracks = new Map(result.targetTrack ? preview?.tracks.flatMap(row => row.clips.map(clip => [clip.id, row.id])) : []);
            for (const id of gesture.ids) {
                const item = locate(id); item.element.classList.add('fv-ed-dragging'); position(item.element, landing, item.clip.frames);
                const row = previewTracks.get(id) || item.track;
                item.element.style.transform = row === item.track ? '' : `translateY(${rows.get(row).top - rows.get(item.track).top}px)`;
            }
            box(selectionBox, {start: landing, frames: gesture.clip.frames, tracks: gesture.ids.map(id => previewTracks.get(id) || locate(id).track)});
            hint({start: landing, frames: gesture.clip.frames}, event, false, true);
            selectionBox.classList.toggle('fv-ed-cannot-drop', !!error);
            for (const id of gesture.ids) locate(id)?.element.classList.toggle('fv-ed-cannot-drop', !!error);
            gesture.refused = error || null;
            if (error) { const words = refusal(error); tooltip.textContent = words; tooltip.hidden = !words; tooltip.dataset.refused = 'true'; }
            else delete tooltip.dataset.refused;
        } else if (gesture.type === 'edge') {
            // Past the end of a clip that cannot grow there, the drag draws the next part from that end.
            const clipEnd = gesture.clip.start + gesture.clip.frames;
            if (gesture.edge === 'end' && frame > clipEnd && !canGrow(gesture.clip)) {
                for (const id of [...gesture.ids, ...(gesture.rightPreviewIds || [])]) { const item = locate(id); if (item) position(item.element, item.clip.start, item.clip.frames); }
                Object.assign(gesture, {type: 'range', anchor: clipEnd, flush: clipEnd, id: undefined, ids: [], clip: null, edge: null, rightPreviewIds: []});
                cachedPoints = allPoints([]); tooltip.hidden = true; updatePointer(event); return;
            }
            let result = edgeParameters(gesture.clip, gesture.edge, frame, points, state.zoom,
                {beats: beatPoints(event, gesture.ids), canExtend: actions.generationAvailable?.() !== false, maxFollowSeconds: boundaryLimits().maxFollowSeconds});
            if (result.kind === 'extend') result.frames = capRange({start: result.frame, frames: result.frames}, false, true).frames;
            if (result.kind === 'planned') {
                let preview;
                try { preview = model.resizePlanned(state.record, gesture.id, gesture.edge, result.frame, actions.generationLimits?.()); }
                catch (error) { if (!(error instanceof model.EditError)) throw error; return; }
                const clip = model.find(preview, gesture.id);
                const end = Math.max(...preview.tracks.find(row => row.id === gesture.track).clips
                    .filter(c => c.id === gesture.id || !clips.has(c.id)).map(c => c.start + c.frames));
                gesture.result = result;
                const selection = {kind: 'range', start: clip.start, frames: end - clip.start, tracks: gesture.ids.map(id => locate(id).track)};
                for (const id of gesture.ids) position(locate(id).element, selection.start, selection.frames);
                box(selectionBox, selection); borders(selectionBox, selection); handles(selectionBox); hint(selection, event); showGuide(result.guide);
                return;
            }
            const originalEnd = gesture.clip.start + gesture.clip.frames;
            const edit = value => value.kind === 'extend'
                ? model.extendClip(state.record, gesture.id, value.frame - originalEnd + value.frames, actions.generationLimits?.() || {})
                : model.trim(state.record, gesture.id, gesture.edge, value.frame);
            let preview, blocked = false;
            try {
                preview = edit(result);
            } catch (error) {
                if (!(error instanceof model.EditError)) throw error;
                // A text part stays as it is while blocked; release flashes it red, with no words.
                if (isText(gesture.clip)) { gesture.result = result; tooltip.hidden = true; return; }
                // A blocked left edge keeps the part as it is while dragging; release reports why.
                if (gesture.edge !== 'end') preview = state.record;
                else {
                    if (error.code !== 'busy') return;
                    const growth = result.frame - originalEnd + (result.kind === 'extend' ? result.frames : 0);
                    const room = model.rightEdgeRoom(state.record, gesture.id, Math.max(0, growth), {generating: result.kind === 'extend'});
                    if (!room.blocked) return;
                    blocked = true;
                    result = edgeParameters(gesture.clip, 'end', room.limit, [], state.zoom, {maxFollowSeconds: boundaryLimits().maxFollowSeconds});
                    if (result.kind === 'extend') {
                        result.frames = Math.floor((room.limit - result.frame) / model.FPS) * model.FPS;
                        // Less room than a continued part needs: the edge only trims.
                        if (result.frames < model.continuedSeconds(0, boundaryLimits().maxFollowSeconds) * model.FPS) result = {kind: 'trim', edge: 'end', frame: result.frame, guide: null};
                    }
                    preview = edit(result);
                }
            }
            for (const id of gesture.rightPreviewIds || []) {
                const item = locate(id); if (item) position(item.element, item.clip.start, item.clip.frames);
            }
            gesture.rightPreviewIds = [];
            let shifted = 0;
            for (const track of preview.tracks) for (const clip of track.clips) {
                const item = locate(clip.id); if (!item) continue;
                if (clip.start !== item.clip.start || clip.frames !== item.clip.frames) {
                    position(item.element, clip.start, clip.frames); gesture.rightPreviewIds.push(clip.id);
                    if (!gesture.ids.includes(clip.id)) shifted = Math.max(shifted, clip.start - item.clip.start);
                }
            }
            for (const line of plannedLines.querySelectorAll('[data-left]')) {
                const clip = model.find(preview, line.dataset.left);
                if (clip) line.style.left = `${pixels(clip.start + clip.frames)}px`;
            }
            gesture.result = result; showGuide(result.guide);
            const tracks = gesture.ids.map(id => locate(id).track);
            if (result.kind === 'extend') {
                for (const id of gesture.ids) position(locate(id).element, gesture.clip.start, result.frame - gesture.clip.start);
                const selection = {kind: 'range', start: result.frame, frames: result.frames, tracks};
                const start = gesture.clip.start + gesture.clip.frames;
                const plan = {...selection, start, frames: result.frame - start + result.frames};
                box(previewBox, selection); borders(previewBox, selection, plan, model.emptyProject(state.record)); hint(selection, event, true);
                box(selectionBox, {start: gesture.clip.start, frames: result.frame - gesture.clip.start, tracks});
            } else {
                previewBox.hidden = true; tooltip.hidden = true;
                const clip = model.find(preview, gesture.id);
                for (const id of gesture.ids) position(locate(id).element, clip.start, clip.frames);
                box(selectionBox, {start: clip.start, frames: clip.frames, tracks});
                hint({start: clip.start, frames: clip.frames}, event);
            }
            const growth = model.find(preview, gesture.id).start + model.find(preview, gesture.id).frames - originalEnd
                + (result.kind === 'extend' ? result.frames : 0);
            if (gesture.edge === 'end' && (growth > 0 || blocked)) {
                clearTimeout(estimateTimer); estimateToken++;
                // A length below the shortest continued part shows that rule; release sets it to that length.
                tooltip.textContent = blocked ? '' : result.raised ? t(`At least ${result.frames / model.FPS} s`, `至少 ${result.frames / model.FPS} 秒`)
                    : t(`+${Math.round(growth / model.FPS)} s`, `＋${Math.round(growth / model.FPS)} 秒`);
                tooltip.hidden = !!blocked;
                selectionBox.classList.toggle('fv-ed-cannot-drop', !!blocked);
            }
        }
    }
    function stopPointer(event, cancel = false) {
        if (!gesture || (event && gesture.pointer !== event.pointerId)) return;
        cancelAnimationFrame(pointerFrame); pointerFrame = null; pointerEvent = null;
        if (!cancel && event) updatePointer(event);
        const finished = gesture;
        // Where each dragged clip was let go, and how fast the pointer went, for the settle into its place.
        // Clips pushed aside by the preview glide too (back on a refusal; nowhere when the edit keeps them there).
        const released = finished.type === 'move' && finished.moved ? [...finished.ids, ...(finished.rightPreviewIds || [])].map(id => {
            const item = locate(id); if (!item) return null;
            const transform = /translateY\((-?[\d.]+)px\)/.exec(item.element.style.transform || '');
            return {id, left: parseFloat(item.element.style.left) || 0, y: transform ? Number(transform[1]) : 0, track: item.track};
        }).filter(Boolean) : [];
        const samples = finished.samples || [], first = samples[0], last = samples.at(-1);
        const velocity = first && last && last[1] > first[1] ? (last[0] - first[0]) / (last[1] - first[1]) : 0;
        for (const member of finished.ids) locate(member)?.element.removeAttribute('data-pressed');
        delete element.dataset.scrub;
        clearPreview(); gesture = null; selectionBox.classList.remove('fv-ed-dragging'); stopAutoScroll();
        const settleReleased = () => {
            for (const value of released) {
                const item = locate(value.id); if (!item) continue;
                const dy = value.y + (rows.get(value.track)?.top ?? 0) - (rows.get(item.track)?.top ?? 0);
                settle(item.element, value.left - pixels(item.clip.start), dy, velocity);
            }
        };
        if (element.hasPointerCapture(finished.pointer)) element.releasePointerCapture(finished.pointer);
        try {
        if (cancel) { state.select(finished.previousSelection); if (finished.type === 'seek') state.seek(finished.previousPlayhead); settleReleased(); return; }
        if (finished.type === 'seek') return;
        if (!finished.moved || finished.type === 'click') {
            clickSelection({target: finished.target, clientX: event.clientX, range: finished.range, kind: finished.type, clipId: finished.id}); return;
        }
        // Refused (generating, locked, no room): it glides back to where it was.
        if (finished.type === 'move' && (finished.blocked || finished.refused)) {
            settleReleased();
            // The reason also stays in the notices, once, after the clip is back.
            if (finished.refused instanceof model.EditError) actions.editError?.(finished.refused);
            return;
        }
        if (finished.type === 'rangeMove' && finished.rangeBlocked) return;
        if (['range', 'rangeMove', 'rangeEdge', 'rangeBoundary'].includes(finished.type)) { state.select(finished.selection, {history: true}); return; }
        const result = finished.result;
        if (!result) return;
        if (finished.type === 'move') {
            attempt(t('Move', '移动'), record => {
                if (textDropLocked(record, finished.members, result.track)) throw new model.EditError('locked');
                return model.move(record, finished.id, result.frame, {targetTrack: result.targetTrack});
            });
            settleReleased();
        }
        else if (finished.type === 'plannedBoundary') attempt(t('Resize parts', '调整段长'), record => model.resizePlannedBoundary(record, finished.id, finished.neighbor.clip.id, result.frame, actions.generationLimits?.()));
        else if (result.kind === 'planned') attempt(t('Resize', '改长短'), record => model.resizePlanned(record, finished.id, finished.edge, result.frame, actions.generationLimits?.()));
        else if (result.kind === 'extend') {
            const frames = result.frame - finished.clip.start - finished.clip.frames + result.frames;
            if (extendClip(finished.id, frames) && result.raised) {
                // Say the new length (tenths, rounded down like other durations) so the change is visible.
                const least = model.RULES.lastSeconds, length = secondsText(result.frames, model.FPS);
                actions.notify?.(result.least ? t(`Lengthened to ${length} s. The last part must be at least ${least} seconds.`, `已加长到 ${length} 秒。最后一段至少 ${least} 秒。`)
                    : t(`Lengthened to ${length} s.`, `已加长到 ${length} 秒。`));
            }
        } else attempt(t('Trim', '裁短'), record => model.trim(record, finished.id, finished.edge, result.frame));
        renderSelection();
        } finally { actions.gestureEnd?.(); }
    }
    function stopAutoScroll() { cancelAnimationFrame(autoFrame); autoFrame = null; }
    function autoScroll() {
        autoFrame = null;
        if (!gesture?.moved || gesture.type === 'click') return;
        const bounds = scroll.getBoundingClientRect();
        const speed = dragX < bounds.left + 28 ? -12 : dragX > bounds.right - 28 ? 12 : 0;
        if (speed) {
            const old = scroll.scrollLeft; scroll.scrollLeft += speed;
            if (scroll.scrollLeft !== old && pointerEvent) updatePointer(pointerEvent);
        }
        autoFrame = requestAnimationFrame(autoScroll);
    }
    function pointerMove(event) {
        if (selectGestures.move(event)) return;
        if (!gesture) {
            const hit = pointerHit(event);
            // A part that is generating cannot change yet: the pointer says so before the press.
            // Empty space below the rows, or Shift over empty track, draws a selection box: the pointer says so.
            const below = scroll.contains(event.target) && event.clientY - content.getBoundingClientRect().top > Math.max(0, ...[...rows.values()].map(row => row.top + row.height));
            element.dataset.cursor = hit.item && busy(hit.item.clip) ? 'not-allowed' : hit.edge || hit.boundary != null || hit.neighbor ? 'ew-resize'
                : hit.item || hit.range ? 'grab' : below || event.shiftKey ? 'crosshair' : 'default';
            return;
        }
        if (!gesture || gesture.pointer !== event.pointerId) return;
        event.preventDefault(); event.stopPropagation(); pointerEvent = event;
        if (!pointerFrame) pointerFrame = requestAnimationFrame(() => { pointerFrame = null; updatePointer(pointerEvent); });
        if (!autoFrame) autoFrame = requestAnimationFrame(autoScroll);
    }
    function dismissPopup(event) {
        const head = heads.querySelector('.fv-ed-track-head[data-open]');
        if (head) { event.preventDefault(); event.stopPropagation(); delete head.dataset.open; return true; }
        if (popover.hidden) return false;
        event.preventDefault(); event.stopPropagation(); popover.hidden = true; return true;
    }
    function keyboard(event) {
        if (event.isComposing || event.keyCode === 229) return;
        if (menu.keyboard(event)) return;
        if (event.key.toLowerCase() === 'escape' && dismissPopup(event)) return;
        if (isTyping(event.target) || event.target.closest('.fv-ed-library')) { event.stopPropagation(); return; }
        event.stopPropagation();
        if (!gesture && selectGestures.keyboard(event)) return;
        const key = event.key.toLowerCase(), modifier = event.ctrlKey || event.metaKey;
        if (key === 'escape') { event.preventDefault(); if (gesture) stopPointer(null, true); else { state.select(null, {history: true}); popover.hidden = true; } }
        else if (gesture) { event.preventDefault(); }
        else if (key === ' ' && !modifier) { event.preventDefault(); actions.togglePlay?.(); }
        // Shift moves ten frames at a time.
        else if (key === 'arrowleft' || key === 'arrowright') { event.preventDefault(); state.seek(Math.max(0, state.playhead + (key === 'arrowleft' ? -1 : 1) * (event.shiftKey ? 10 : 1))); }
        else if ((key === 'arrowup' || key === 'arrowdown') && !modifier && !event.altKey) {
            event.preventDefault(); actions.pause?.();
            const points = model.snapPoints(state.record), range = state.selection;
            if (range?.kind === 'range') {
                points.push(range.start, range.start + range.frames);
                let boundary = range.start;
                try { for (const count of rangeParts(range).slice(0, -1)) { boundary += count; points.push(boundary); } }
                catch (error) { if (!(error instanceof model.EditError)) throw error; }
            }
            const ordered = [...new Set([0, model.duration(state.record), ...points])].sort((a, b) => a - b);
            const target = key === 'arrowup' ? ordered.findLast(point => point < state.playhead) : ordered.find(point => point > state.playhead);
            if (target != null) state.seek(target);
            if (range?.kind === 'range') layout();
            // Going left, the line keeps the lead before it in view (a jump to the start scrolls fully back to 0).
            const x = scrolledX(state.playhead), lead = content.offsetLeft;
            if (x - lead < scroll.scrollLeft) scroll.scrollLeft = Math.max(0, x - lead);
            else if (x > scroll.scrollLeft + scroll.clientWidth) scroll.scrollLeft = x - scroll.clientWidth;
        }
        else if (modifier && key === 'z') { event.preventDefault(); event.shiftKey ? state.redo() : state.undo(); }
        // Ctrl/Cmd+Y redoes too, as in other editors (the page handles it elsewhere; the timeline keeps its keys to itself).
        else if (modifier && key === 'y' && !event.shiftKey) { event.preventDefault(); state.redo(); }
        else if (key === 'z' && event.shiftKey && !event.altKey) { event.preventDefault(); fitAll(); }
        else if ((key === 'enter' || key === ' ') && event.target.matches('.fv-ed-clip')) { event.preventDefault(); clickSelection({target: event.target}); }
    }
    let sourcePreview = null;
    function clearSourcePreview() {
        if (!sourcePreview) return;
        sourcePreview.nodes.forEach(node => node.remove());
        if (sourcePreview.restore) {
            if (sourcePreview.record === state.record) sourcePreview.restore();
            else renderRecord();
        }
        sourcePreview = null; tooltip.hidden = true;
    }
    function previewSourcePlacement(record) {
        const nodes = [], added = record.tracks.flatMap(track => track.clips
            .filter(clip => !clips.has(clip.id)).map(clip => ({track: track.id, clip})));
        sourcePreview = {record: state.record, nodes};
        if (added.some(item => item.track === 'a2') && !rows.has('a2')) {
            const first = rows.get('a1'), top = first.top + first.height + 6;
            const row = create('div', 'fv-ed-track-row'); row.dataset.track = 'a2'; row.dataset.preview = 'true';
            const head = create('div', 'fv-ed-track-head'); head.dataset.track = 'a2'; head.dataset.preview = 'true';
            const name = t('Audio 2', '声音 2'); head.append(icon('audio'), create('span', '', name)); cutTip(head, name, head.querySelector('span'));
            row.setAttribute('aria-label', name);
            row.style.top = head.style.top = `${top}px`; row.style.height = head.style.height = `${laneHeight()}px`;
            const oldName = first.head.querySelector('span').textContent, oldTop = addAudio.style.top, oldHeight = content.style.height;
            sourcePreview.restore = () => {
                first.head.querySelector('span').textContent = oldName; cutTip(first.head, oldName, first.head.querySelector('span'));
                addAudio.style.top = oldTop; setTrackHeight(parseFloat(oldHeight) - 34);
            };
            first.head.querySelector('span').textContent = t('Audio 1', '声音 1'); cutTip(first.head, first.head.querySelector('span').textContent, first.head.querySelector('span'));
            addAudio.style.top = `${top + laneHeight() + 6}px`; setTrackHeight(top + laneHeight());
            heads.insertBefore(head, addAudio); content.insertBefore(row, overlays); nodes.push(head, row);
        }
        for (const {track, clip} of added) {
            const node = create('div', 'fv-ed-gesture-preview fv-ed-source-placement'); node.dataset.track = track;
            const row = rows.get(track), top = row?.top ?? rows.get('a1').top + rows.get('a1').height + 6;
            node.style.top = `${top}px`; node.style.height = `${row?.height ?? laneHeight()}px`;
            position(node, clip.start, clip.frames); overlays.append(node); nodes.push(node);
        }
        return added;
    }
    const droppingFiles = event => actions.importFiles && !Array.from(event.dataTransfer?.types || []).includes(SOURCE_TYPE)
        && Array.from(event.dataTransfer?.types || []).includes('Files');
    function dropOver(event) {
        if (droppingFiles(event)) {
            // Files from the computer: only the place is known until they are imported.
            event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = 'copy';
            const frame = snap(frameAt(event), transportSnapPoints(state, allPoints([]), event), state.zoom);
            const track = state.record.tracks.find(row => row.id === trackAt(event));
            showInsertion(insertionPoint(track?.clips || [], frame.frame).frame); insertion.classList.remove('fv-ed-blocked-insertion');
            showGuide(frame.guide); tooltip.hidden = true;
            return;
        }
        if (!Array.from(event.dataTransfer?.types || []).includes(SOURCE_TYPE)) return;
        event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = 'copy';
        const frame = snap(frameAt(event), transportSnapPoints(state, allPoints([]), event), state.zoom);
        const key = `${frame.frame}/${state.zoom}/${pointerTrackAt(event)}/${draggedSource ? JSON.stringify(draggedSource) : ''}`;
        if (key !== dropPreviewKey || sourcePreview?.record !== state.record) {
            clearSourcePreview();
            dropPreviewKey = key;
            if (draggedSource) try {
                const preview = model.insertSource(state.record, {...draggedSource, at: frame.frame, targetTrack: audioTargetAt(draggedSource, event)});
                const added = previewSourcePlacement(preview), sound = added.find(item => item.track !== 'v1');
                const name = sound ? !preview.tracks.some(track => track.id === 'a3') && preview.tracks.length === 3
                    ? t(`Audio ${sound.track === 'a1' ? 1 : 2}`, `声音 ${sound.track === 'a1' ? 1 : 2}`) : t('Audio', '声音') : '';
                dropPoint = {frame: added[0].clip.start, label: `${preciseClock(added[0].clip.start)} – ${preciseClock(added[0].clip.start + draggedSource.frames)}${name ? ` · ${name}` : ''}`};
            } catch (error) {
                if (!(error instanceof model.EditError)) throw error;
                sourcePreview = {record: state.record, nodes: []};
                dropPoint = {frame: frame.frame, error, label: null};
            }
            else {
                const track = state.record.tracks.find(row => row.id === pointerTrackAt(event));
                dropPoint = insertionPoint(track?.clips || [], frame.frame);
            }
        }
        event.dataTransfer.dropEffect = dropPoint.error ? 'none' : 'copy';
        showInsertion(dropPoint.frame); insertion.classList.toggle('fv-ed-blocked-insertion', !!dropPoint.error); showGuide(frame.guide);
        tooltip.hidden = !dropPoint.label;
        if (dropPoint.label) {
            tooltip.textContent = dropPoint.label;
            const bounds = element.getBoundingClientRect();
            tooltip.style.left = `${clamp(event.clientX - bounds.left + 14, 8, Math.max(8, bounds.width - 300))}px`;
            tooltip.style.top = `${clamp(event.clientY - bounds.top + 14, 26, bounds.height - 32)}px`;
        }
    }
    function dropSource(event) {
        clearSourcePreview();
        if (droppingFiles(event) && event.dataTransfer.files?.length) {
            event.preventDefault(); event.stopPropagation();
            const at = snap(frameAt(event), transportSnapPoints(state, allPoints([]), event), state.zoom).frame;
            actions.importFiles([...event.dataTransfer.files], {at});
            showInsertion(null); showGuide(null); dropPoint = null; dropPreviewKey = '';
            return;
        }
        const payload = sourcePayload(event.dataTransfer?.getData(SOURCE_TYPE));
        if (!payload) { showInsertion(null); showGuide(null); return; }
        event.preventDefault(); event.stopPropagation();
        // Audio-only library drops honor the pointed row through normal insertion rules.
        const at = snap(frameAt(event), transportSnapPoints(state, allPoints([]), event), state.zoom).frame;
        const targetTrack = audioTargetAt(payload, event);
        if (actions.insertSource) Promise.resolve(actions.insertSource({...payload, at, targetTrack}, {loadMetadata: true})).catch(error => actions.editError?.(error));
        else attempt(t('Insert clip', '放入素材'), record => model.insertSource(record, {...payload, at, targetTrack}), {clearRange: true});
        showInsertion(null); showGuide(null); dropPoint = null; dropPreviewKey = ''; draggedSource = null;
    }
    // Time zero sits a little in from the scrolling area's left edge (the content's margin, editor_tracks.css), so a
    // frame's place in the scrolled area is its pixels plus that lead.
    function scrolledX(frames) { return pixels(frames) + content.offsetLeft; }
    // The time under the anchor stays under it: time zero sits the content's lead in from the scrolled area's edge.
    function zoom(value, anchor = scroll.clientWidth / 2) {
        const lead = content.offsetLeft, center = (scroll.scrollLeft + anchor - lead) / state.zoom;
        state.setZoom(clamp(value, Number(slider.min), Number(slider.max)));
        scroll.scrollLeft = Math.max(0, center * state.zoom + lead - anchor);
    }
    function fitAll() {
        if (fit.disabled) return;
        const rangeEnd = state.selection?.kind === 'range' ? state.selection.start + state.selection.frames : 0;
        zoom(scroll.clientWidth / Math.max(2, Math.max(model.duration(state.record), rangeEnd) / model.FPS)); scroll.scrollLeft = 0;
    }
    function wheel(event) {
        if (!event.ctrlKey && !event.metaKey) return;
        event.preventDefault(); event.stopPropagation();
        const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? scroll.clientWidth : 1;
        const delta = (event.deltaY || event.deltaX || 0) * unit;
        const anchor = clamp(event.clientX - scroll.getBoundingClientRect().left, 0, scroll.clientWidth);
        zoom(state.zoom * Math.exp(-clamp(delta, -600, 600) * .002), anchor);
    }
    on(addAudio, 'pointerdown', event => event.stopPropagation());
    const activateAudio = event => {
        event.stopPropagation();
        if (!addAudio.disabled) {
            const removing = state.record.tracks.some(track => track.id === 'a2');
            attempt(removing ? t('Remove Audio 2', '移除声音 2') : t('Add audio track', '添加声音轨'),
                removing ? record => {
                    const next = model.removeAudioTrack(record), selection = state.selection;
                    // Record subscribers must not see a gap on a removed track.
                    if (selection?.kind === 'gap' && selection.track === 'a2') state.select(null);
                    else if (selection?.kind === 'range' && selection.tracks.includes('a2')) {
                        const tracks = selection.tracks.filter(id => id !== 'a2');
                        state.select(tracks.length ? {...selection, tracks} : null);
                    }
                    return next;
                } : model.addAudioTrack);
        }
    };
    // The shell also routes captured keys through control onkeydown handlers.
    addAudio.onkeydown = event => {
        if ((event.key === ' ' || event.key === 'Enter') && !event.ctrlKey && !event.metaKey && !event.altKey) {
            event.preventDefault(); activateAudio(event);
        }
    };
    on(addAudio, 'click', activateAudio);
    // Focus taken by a press (for the timeline's keys) is not keyboard focus: no ring round the whole area then.
    // Focus that arrives by Tab still shows the page's ring inside the edge.
    on(element, 'pointerdown', () => { element.dataset.pointerFocus = 'true'; }, true);
    on(element, 'blur', () => { delete element.dataset.pointerFocus; });
    on(element, 'pointerdown', startPointer); on(element, 'pointermove', pointerMove);
    const menu = createTrackMenu(element, {state, t, actions, hitTest: pointerHit, frameAt, trackAt, choose: id => selectGestures.choose(id)});
    on(element, 'pointerup', event => { if (selectGestures.finish(event)) return; event.stopPropagation(); stopPointer(event); });
    on(element, 'pointercancel', event => { if (!selectGestures.finish(event, true)) stopPointer(event, true); });
    on(element, 'lostpointercapture', event => { selectGestures.finish(event, true); if (gesture?.pointer === event.pointerId) stopPointer(event, true); });
    on(element, 'keydown', keyboard); on(element, 'keyup', event => event.stopPropagation());
    on(element, 'click', event => {
        event.stopPropagation();
        // Native keyboard/assistive activation has no preceding pointer gesture.
        if (event.detail === 0 && event.target.closest('.fv-ed-clip')) clickSelection(event);
    });
    on(scroll, 'scroll', renderRuler, {passive: true});
    on(scroll, 'scroll', renderEmptyHints, {passive: true});
    on(scroll, 'scroll', syncVerticalScroll, {passive: true});
    on(scroll, 'wheel', wheel, {passive: false});
    on(heads, 'wheel', event => {
        if (event.ctrlKey || event.metaKey) { wheel(event); return; }
        event.preventDefault(); event.stopPropagation();
        const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? scroll.clientHeight : 1;
        if (event.shiftKey) scroll.scrollLeft += (event.deltaX || event.deltaY) * unit;
        else { scroll.scrollLeft += event.deltaX * unit; scroll.scrollTop += event.deltaY * unit; }
    }, {passive: false});
    on(scroll, 'dragover', dropOver); on(scroll, 'drop', dropSource);
    on(document, 'dragstart', event => {
        clearSourcePreview();
        draggedSource = sourcePayload(event.dataTransfer?.getData(SOURCE_TYPE)); dropPreviewKey = '';
    });
    on(document, 'pointerdown', closeHeads, true);
    on(document, 'dragend', () => { clearSourcePreview(); draggedSource = null; dropPreviewKey = ''; showInsertion(null); showGuide(null); });
    on(scroll, 'dragleave', event => { if (!scroll.contains(event.relatedTarget)) { clearSourcePreview(); dropPreviewKey = ''; showInsertion(null); showGuide(null); } });
    on(zoomOut, 'click', () => zoom(state.zoom / 1.25)); on(zoomIn, 'click', () => zoom(state.zoom * 1.25));
    on(slider, 'input', () => zoom(Number(slider.value)));
    on(fit, 'click', fitAll);
    const unsubscribe = state.subscribe(event => {
        if (event.type === 'record') {
            // Status updates elsewhere must not cancel an unchanged moving pair.
            if (gesture && !(gesture.type === 'move' && (gesture.members ?? []).every(clip =>
                JSON.stringify(model.find(state.record, clip.id)) === JSON.stringify(clip)))) stopPointer(null, true);
            if (gesture) cachedPoints = allPoints(gesture.ids);
            renderRecord();
        } else if (event.type === 'selection') renderSelection();
        else if (event.type === 'playhead') renderPlayhead();
        else if (event.type === 'zoom') { if (gesture) stopPointer(null, true); layout(); }
    });
    const observer = new ResizeObserver(layout); observer.observe(scroll);
    if (touchRows) on(touchRows, 'change', () => { if (!disposed) renderRecord(); });
    selectionBox.hidden = previewBox.hidden = guide.hidden = insertion.hidden = true;
    const audioControls = attachAudioTracks(element, {state, t, actions});
    // Track head switches are drawn by the audio and text modules on every change; each gets its name, use and state.
    const tipSwitches = () => { for (const button of heads.querySelectorAll('.fv-ed-audio-track-switch, .fv-ed-text-track-controls button')) switchTip(button, t); };
    const headObserver = typeof MutationObserver === 'function' ? new MutationObserver(tipSwitches) : null;
    headObserver?.observe(heads, {childList: true, subtree: true, attributes: true, attributeFilter: ['aria-pressed', 'disabled', 'title']});
    renderRecord(); audioControls.refresh(); tipSwitches();
    let blockedTimer = null, blockedIds = [];
    function flashBlocked(frame = state.playhead) {
        clearTimeout(blockedTimer);
        for (const id of blockedIds) locate(id)?.element.classList.remove('fv-ed-cannot-drop');
        for (const id of selected) locate(id)?.element.classList.add('fv-ed-cannot-drop');
        blockedIds = [...selected];
        showInsertion(frame); insertion.classList.add('fv-ed-blocked-insertion');
        blockedTimer = setTimeout(() => {
            for (const id of blockedIds) locate(id)?.element.classList.remove('fv-ed-cannot-drop');
            blockedIds = [];
            showInsertion(null); insertion.classList.remove('fv-ed-blocked-insertion');
        }, 700);
    }
    function guideTarget(kind) {
        const viewport = scroll.getBoundingClientRect();
        const reveal = (left, width) => () => {
            if (left < viewport.left + 12 || left + width > viewport.right - 12)
                scroll.scrollLeft = Math.max(0, scroll.scrollLeft + left - viewport.left - 12);
        };
        if (kind === 'trim') {
            const item = [...clips.values()].filter(item => item.clip.source && !isText(item.clip))
                .sort((a, b) => (b.track === 'v1') - (a.track === 'v1') || b.clip.start - a.clip.start)[0];
            if (!item) return null;
            const node = item.element.querySelector('.fv-ed-trim-end');
            const r = item.element.getBoundingClientRect(), left = r.right - 12;
            return {node, rect: {left, right: left + 24, top: r.top, bottom: r.bottom, width: 24, height: r.height}, reveal: reveal(left, 24)};
        }
        const node = rows.get('v1')?.element;
        if (!node) return null;
        // Leave a small gap beyond all tracks, outside the trim hit area. At
        // frame zero an empty project starts exactly at the timeline origin.
        const end = model.duration(state.record), start = end ? Math.ceil(end / model.FPS + 12 / state.zoom) * model.FPS : 0;
        const r = node.getBoundingClientRect(), left = content.getBoundingClientRect().left + pixels(start), width = 3 * state.zoom;
        return {node, rect: {left, right: left + width, top: r.top, bottom: r.bottom, width, height: r.height}, reveal: reveal(left, width)};
    }
    return {element, ready, keyboard, menu, dismissPopup, flashBlocked, seekPlayhead, extendClip, guideTarget,
        setSnapping(enabled) {
            if (typeof enabled !== 'boolean') return {ok: false, reason: 'snapping'};
            setTransportSnapping(state, enabled); cachedPoints = allPoints(gesture?.ids || []);
            return {ok: true, enabled};
        },
        refresh() { if (!disposed) { renderRecord(); audioControls.refresh(); } },
        cancelGesture() { if (selectGestures.finish(null, true)) return true; if (!gesture) return false; stopPointer(null, true); return true; },
        getScrollLeft() { return scroll.scrollLeft; },
        setScrollLeft(value) { if (Number.isFinite(value) && value >= 0) { scroll.scrollLeft = value; renderRuler(); } },
        setProgress(clipId, value) {
            if (disposed) return;
            const normalized = typeof value === 'number' ? {state: 'generating', fraction: value, remainingSeconds: null} : value;
            for (const id of pairIds(clipId)) { progresses.set(id, normalized); const item = locate(id); if (item) updateProgress(item); }
        },
        dispose() {
            if (disposed) return;
            if (gesture) stopPointer(null, true);
            selectGestures.dispose();
            menu.dispose();
            disposed = true; clearTimeout(blockedTimer); unsubscribe(); observer.disconnect(); headObserver?.disconnect(); listeners.forEach(remove => remove());
            audioControls.dispose();
            addAudio.onkeydown = null;
            cancelAnimationFrame(pointerFrame); stopAutoScroll(); clearTimeout(estimateTimer); estimateToken++;
            clips.forEach(item => item.component?.dispose()); element.remove();
        }};
}
