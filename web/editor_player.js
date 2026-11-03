import {isText} from './editor_text_model.js';
import * as model from './editor_model.js';
import {pictureSession} from './editor_picture_session.js';
import {createPreviewPicture} from './editor_preview_picture.js';
import {sourceKey, mediaURL} from './editor_store.js';
import {clipPictureSize, createPictureGuide} from './editor_frame.js';
import {createPlaybackClock} from './editor_transport_model.js';
import {createAudioPreview} from './editor_audio.js';
import {previewSpeed, speedAudioFrame, separateSpeedAudio} from './editor_speed.js';
import {sourceFrame, percent} from './editor_speed_model.js';
const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };
export function audioSeconds(clip, sourceFrame) {
    // The chain receipt counts samples at 32 kHz, even if the media is 48 kHz.
    return clip.splice ? clip.splice.audio_samples / 32000 + (sourceFrame - clip.splice.frames) / 24 : sourceFrame / 24;
}
export const transportTime = frames => {
    const seconds = Math.floor(Math.max(0, frames) / 24);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
};

// Only changes to playable audio ask the server again; transport ticks never do.
export const playbackAudioSignature = record => JSON.stringify([record.id, record.tracks.filter(t => t.kind === 'audio').map(track =>
    [track.id, track.clips.map(c => [c.id, c.source, c.start, c.frames, c.in, c.length, c.volume, c.splice])])]);
export function selectAudioChain(chains, track, clip, position, volumePreview = new Map()) {
    const chain = chains.find(c => c.ready && c.audio && c.track === track && c.clips.includes(clip?.id)
        && position >= c.start && position < c.start + c.frames
        && !c.clips.some(id => volumePreview.has(id) && volumePreview.get(id) !== 1));
    return chain ? {chain, seconds: (position - chain.start) / 24 + (chain.offset || 0)} : null;
}
export function createEditorPlayer(host, transport, {state, sources, api, t, error = () => {}, readAudioSamples = null, preservePreview = () => false, prepareAudio = () => true, speech = null}) {
    const frame = el('div', 'fv-ed-picture'); frame.setAttribute('aria-label', t('Video preview', '视频预览'));
    const empty = el('div', 'fv-ed-empty-preview'), pictureIcon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    pictureIcon.setAttribute('viewBox', '0 0 24 24'); pictureIcon.setAttribute('aria-hidden', 'true');
    const picturePath = document.createElementNS(pictureIcon.namespaceURI, 'path');
    picturePath.setAttribute('d', 'M3 4h18v16H3zM3 16l5-6 5 6 3-4 5 6M15 8h.01'); pictureIcon.append(picturePath);
    empty.append(pictureIcon); frame.append(empty);
    const closePreview = el('button', 'fv-ed-preview-close'); closePreview.type = 'button'; closePreview.hidden = true;
    closePreview.setAttribute('aria-label', t('Close preview', '关闭预览'));
    const closeIcon = document.createElementNS(pictureIcon.namespaceURI, 'svg'), closePath = document.createElementNS(pictureIcon.namespaceURI, 'path');
    closeIcon.setAttribute('viewBox', '0 0 16 16'); closeIcon.setAttribute('aria-hidden', 'true'); closePath.setAttribute('d', 'm4 4 8 8M12 4l-8 8'); closeIcon.append(closePath); closePreview.append(closeIcon); frame.append(closePreview);
    const videos = [el('video'), el('video')]; videos.forEach(video => { video.playsInline = true; video.preload = 'auto'; video.controls = false; video.setAttribute('aria-hidden', 'true'); frame.append(video); });
    // One element per sound track (a1–a6): the three separated parts play together with the voice and music tracks.
    const audio = Array.from({length: 6}, () => el('audio')); audio.forEach(value => { value.preload = 'auto'; frame.append(value); });
    const chainAudio = new Map(); // Separate players keep the other track's media slot stable.
    const allMedia = () => [...videos, ...audio, ...chainAudio.values()];
    const pictureGuide = createPictureGuide(host, frame, {state, t});
    const pictureEdit = pictureSession(state, t);
    const picturePreview = createPreviewPicture(host, frame, {state, t});
    const playButton = el('button', 'fv-ed-play'); playButton.type = 'button'; playButton.title = t('Space', '空格');
    const time = el('span', 'fv-ed-time'); time.setAttribute('aria-live', 'off'); transport.append(playButton, time);
    let active = 0, clipId = null, playing = false, disposed = false, raf = null, clockStart = 0, frameStart = 0, ticking = false, preview = null, previewSize = null, previewProject = null;
    let audioContext = null, textRenderer = null, chains = [], chainSignature = null, chainRequest = null, chainEpoch = 0;
    const failedChains = new Set(), entries = new Map();
    let seekGeneration = 0, forcePosition = false, pictureView = null, processedSound = false;
    const chainSeconds = (chain, position) => Math.max(0, (position - chain.start) / 24 + (chain.offset || 0));
    const playback = createPlaybackClock();
    const gains = new WeakMap(), volumePreview = new Map();
    const audioPreview = createAudioPreview({state, sources, api, volumePreview, readSamples: readAudioSamples, error, speech});
    const clipVolume = clip => volumePreview.get(clip?.id) ?? clip?.volume ?? 1;
    function volume(media, value) {
        // HTMLMediaElement stops at 1. A GainNode implements the record's 0–2 range.
        if (value > 1 || gains.has(media)) {
            try {
                audioContext ||= new (window.AudioContext || window.webkitAudioContext)();
                if (!gains.has(media)) { const source = audioContext.createMediaElementSource(media), gain = audioContext.createGain(); source.connect(gain); gain.connect(audioContext.destination); gains.set(media, gain); }
                media.volume = 1; gains.get(media).gain.value = value; if (playing) audioContext.resume().catch(() => {}); return;
            } catch { /* Browser without Web Audio: retain sound up to unity. */ }
        }
        media.volume = Math.min(1, Math.max(0, value));
    }
    function startMedia(media) {
        if (disposed || !media._editorRunning || !media.paused || media.seeking || media.readyState < 3 || media._editorPlay || media._editorPlayFailed) return;
        const attempt = {url: media.dataset.source}; media._editorPlay = attempt;
        const current = () => !disposed && media._editorRunning && media._editorPlay === attempt && media.dataset.source === attempt.url;
        // A rejected play() is usually an interruption (a seek or load in flight), so the clip
        // holds off for a moment and then tries again; only a source change or stop resets it sooner.
        // A refused start or an unplayable file never succeeds by asking again, nor does a run of refusals.
        const rejected = reason => {
            if (!current()) return;
            media._editorPlay = null; media._editorPlayFailed = true;
            if (reason?.name === 'NotAllowedError') { media._editorPlayStuck = 'blocked'; return; }
            if (reason?.name === 'NotSupportedError' || (media._editorPlayTries = (media._editorPlayTries || 0) + 1) > 25) { media._editorPlayStuck = 'failed'; return; }
            setTimeout(() => {
                if (disposed || media.dataset.source !== attempt.url) return;
                media._editorPlayFailed = false; startMedia(media);
            }, 120);
        };
        try {
            Promise.resolve(media.play()).then(() => {
                if (!current()) { if (disposed || !media._editorRunning) media.pause(); return; }
                media._editorPlay = null; media._editorPlayTries = 0;
            }, rejected);
        } catch (reason) { rejected(reason); }
    }
    const freshStart = media => { media._editorPlayFailed = false; media._editorPlayTries = 0; media._editorPlayStuck = null; };
    function seekMedia(media, url, seconds, running, gain = 1, force = forcePosition) {
        media.playbackRate *= Math.max(1, playback.rate); if (media.tagName === 'AUDIO') media.muted ||= playback.rate < 0;
        running = running && playback.rate > 0;
        const changed = media.dataset.source !== url;
        if (changed) { stopMedia(media); media.dataset.source = url; media.src = url; freshStart(media); media.load(); }
        if (running && !media._editorRunning) freshStart(media);
        media._editorTime = Math.max(0, seconds); media._editorRunning = running;
        volume(media, gain);
        const drift = Math.abs(media.currentTime - media._editorTime);
        // Forward speed uses the native media clock. Exact paused/reverse frames
        // and explicit seeks still position precisely; ticks never cancel a seek.
        if (media.readyState >= 1 && (force || !media.seeking) && drift > (changed || force || !running ? .000001 : .08)) {
            try { media.currentTime = media._editorTime; } catch {}
        }
        if (running) startMedia(media); else stopMedia(media);
    }
    function stopMedia(media) { if (!media.paused || media._editorPlay) media.pause(); media._editorPlay = null; media._editorRunning = false; delete media.dataset.audioChain; }
    function metadataHandler(media) {
        media.onloadedmetadata = () => {
            if (disposed) return;
            if (!media.seeking && Math.abs(media.currentTime - (media._editorTime || 0)) > .000001) {
                try { media.currentTime = media._editorTime || 0; } catch {}
            }
            startMedia(media);
        };
        const ready = () => { startMedia(media); if (media === videos[active]) showPicture(); };
        media.addEventListener('seeked', ready);
        media.addEventListener('canplay', ready);
    }
    [...videos, ...audio].forEach(metadataHandler);
    function failChain(media) {
        if (disposed || media._chainEpoch !== chainEpoch) return;
        clearTimeout(media._startTimer);
        failedChains.add(media._chain.key);
        stopMedia(media); media.muted = true; media._attempt = null;
        render();
    }
    function chainMedia(chain) {
        if (!chainAudio.has(chain.key)) {
            const media = el('audio'); media.preload = 'auto'; media.muted = true;
            media._chain = chain; media._chainEpoch = chainEpoch;
            media.dataset.preparedChain = chain.key;
            media.dataset.source = mediaURL(api, chain.audio); media.src = media.dataset.source;
            const prepared = () => {
                if (disposed || media._chainEpoch !== chainEpoch) return;
                if (!media._editorRunning && !media.seeking) {
                    const target = chainSeconds(chain, state.playhead);
                    if (Math.abs(media.currentTime - target) > .001) {
                        try { media.currentTime = target; } catch {}
                    }
                }
            };
            media.onloadedmetadata = prepared;
            media.onerror = () => failChain(media);
            media.onwaiting = media.onstalled = () => {
                // Once selected, a buffer underrun restores the original path.
                if (media._editorRunning) failChain(media);
            };
            chainAudio.set(chain.key, media); frame.append(media); media.load();
        }
        return chainAudio.get(chain.key);
    }
    function resetEntries() {
        seekGeneration++; entries.clear();
        for (const media of chainAudio.values()) {
            clearTimeout(media._startTimer);
            stopMedia(media); media.muted = true; media.playbackRate = 1; media._attempt = null;
            const target = chainSeconds(media._chain, state.playhead);
            if (media.readyState >= 1 && Math.abs(media.currentTime - target) > .001) {
                try { media.currentTime = target; } catch {}
            }
        }
    }
    function removeChains() {
        resetEntries();
        for (const media of chainAudio.values()) {
            media.onloadedmetadata = media.onerror = media.onwaiting = media.onstalled = null;
            media.removeAttribute('src'); media.load(); media.remove();
        }
        chainAudio.clear();
    }
    function useChain(joined, track) {
        if (!joined) { entries.delete(track); return null; }
        const {chain, seconds} = joined, media = chainMedia(chain);
        if (!playing) {
            stopMedia(media); media.muted = true; media._editorTime = seconds;
            if (media.readyState >= 1 && !media.seeking && Math.abs(media.currentTime - seconds) > .001) {
                try { media.currentTime = seconds; } catch {}
            }
            // Paused positioning is preparation, never a sound takeover.
            media.dataset.audioChain = chain.key;
            return null;
        }
        let entry = entries.get(track);
        if (!entry || entry.key !== chain.key) {
            entry = {key: chain.key, mode: 'legacy', epoch: chainEpoch, generation: seekGeneration};
            entries.set(track, entry);
            // Lock this passage to originals if data or the seek target is not ready.
            if (media.readyState < 3 || media.seeking || Math.abs(media.currentTime - seconds) > .08) return null;
            entry.mode = 'starting'; media._editorRunning = true; media.muted = true;
            media.dataset.audioChain = chain.key; media._attempt = entry;
            const url = media.dataset.source;
            const current = () => !disposed && playing && entries.get(track) === entry
                && entry.epoch === chainEpoch && entry.generation === seekGeneration
                && media._attempt === entry && media.dataset.source === url;
            const rejected = () => { if (current()) failChain(media); };
            media._startTimer = setTimeout(rejected, 1500);
            try {
                Promise.resolve(media.play()).then(() => {
                    if (!current()) return;
                    const target = chainSeconds(chain, state.playhead);
                    if (media.paused || media.seeking || media.readyState < 3 || Math.abs(media.currentTime - target) > .08) {
                        rejected(); return;
                    }
                    clearTimeout(media._startTimer); entry.mode = 'active'; render();
                }, rejected);
            } catch { rejected(); }
        }
        if (entry.mode !== 'active') return null;
        if (media.paused || media.seeking || media.readyState < 3) { failChain(media); return null; }
        const drift = seconds - media.currentTime;
        // 80 ms triggers gentle clock correction, not a bound on presented A/V.
        // Explicit user seeks reset the entry; normal ticks do not repeatedly seek.
        if (Math.abs(drift) > .25) { failChain(media); return null; }
        media.playbackRate = Math.abs(drift) > .08 ? (drift > 0 ? 1.03 : .97) : 1;
        media.muted = false; media.dataset.audioChain = chain.key;
        return joined;
    }
    function refreshChains() {
        if (!prepareAudio()) return;
        const signature = playbackAudioSignature(state.record);
        if (chainSignature === signature) return;
        chainSignature = signature; chains = []; failedChains.clear(); chainRequest?.abort(); removeChains();
        const epoch = ++chainEpoch;
        if (typeof api.fetchApi !== 'function') return;
        chainRequest = new AbortController();
        // Send the current edit, including unsaved trims; do not wait for autosave.
        api.fetchApi('/freevideo/editor/audio-chains', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({project: model.serialize(state.record)}), signal: chainRequest.signal})
            .then(response => response.ok ? response.json() : null).then(value => {
                if (disposed || epoch !== chainEpoch) return;
                chains = Array.isArray(value?.chains) ? value.chains : [];
                // Preload independently of selection; a late response never takes
                // over a passage which started with the original sound.
                for (const chain of chains) if (chain.ready && chain.audio) {
                    chainMedia(chain);
                    if (playing && state.playhead >= chain.start && state.playhead < chain.start + chain.frames)
                        entries.set(chain.track, {key: chain.key, mode: 'legacy'});
                }
                render();
            }).catch(() => { /* Missing route, cancellation or preparation failure: ordinary playback. */ });
    }
    function located(record, position) {
        // The model owns which content covers the frame. Normalize its query rows.
        const value = model.at(record, position);
        const rows = Array.isArray(value) ? value : Object.entries(value || {}).map(([track, row]) => ({track, ...row}));
        const result = new Map();
        for (const row of rows) {
            if (!row) continue;
            const track = row.track?.id || row.track || row.trackId;
            const clip = row.clip || (row.id ? row : null);
            if (clip) result.set(track, {clip, frame: row.frame ?? row.sourceFrame ?? clip.in + position - clip.start});
        }
        return result;
    }
    function showPicture() {
        if (disposed || !pictureView) return;
        const {guide, picture} = pictureView, video = picture.video;
        pictureGuide.update(guide);
        // Keep the outgoing decoded picture while an incoming part is loading
        // or seeking. Its slot is recycled only after the new picture is shown.
        if (picture.clip && picture.playing && !picture.preview && (video.seeking || video.readyState < 3)) return;
        videos.forEach(value => value.classList.toggle('fv-ed-visible', !!picture.clip && value === video));
        picturePreview.update(picture);
    }
    function render() {
        if (disposed) return;
        refreshChains();
        // The total is what export writes (picture and sound); text past it is not exported.
        const record = state.record, position = state.playhead, total = model.exportFrames(record);
        const hasContent = record.tracks.some(track => track.clips.some(clip => clip.source || isText(clip)));
        empty.hidden = hasContent || preview?.tagName === 'VIDEO'; frame.dataset.empty = String(!empty.hidden);
        playButton.disabled = !total || !hasContent;
        time.textContent = `${transportTime(position)} / ${transportTime(total)}`;
        transport.dataset.rate = String(playing ? playback.rate : 0);
        if (playButton.dataset.playing !== String(playing)) {
            playButton.dataset.playing = String(playing);
            const glyph = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); glyph.setAttribute('viewBox', '0 0 16 16'); glyph.setAttribute('aria-hidden', 'true'); glyph.style.width = glyph.style.height = '16px';
            const path = document.createElementNS(glyph.namespaceURI, 'path'); path.setAttribute('d', playing ? 'M4 3h3v10H4zM9 3h3v10H9z' : 'm5 3 8 5-8 5z'); path.setAttribute('fill', 'currentColor'); glyph.append(path); playButton.replaceChildren(glyph);
        }
        playButton.setAttribute('aria-label', playing ? t('Pause', '暂停') : t('Play', '播放'));
        const shown = Math.min(position, Math.max(0, model.duration(record) - 1));
        const map = located(record, shown), videoRow = pictureEdit.previewRow(map.get('v1'), shown), clip = videoRow?.clip;
        const source = clip?.source && sources.get(sourceKey(clip.source));
        const hasPicture = !!source?.video && !source.missing;
        const fallbackSize = record.size_mode === 'fixed' || record.tracks[0].clips.some(c => c.source) ? record : {width: 1344, height: 768};
        const guide = {size: previewSize || (hasPicture && clipPictureSize(clip, sources)) || fallbackSize, record, clip,
            preview: !!preview, playing};
        if (!hasPicture) {
            clipId = null; videos.forEach(video => { video.classList.remove('fv-ed-visible'); stopMedia(video); });
        } else if (clipId !== clip.id) {
            const url = mediaURL(api, source.video);
            if (clipId !== null || videos[1 - active].dataset.source === url) active = 1 - active;
            clipId = clip.id;
        }
        const linked = hasPicture ? [...map.values()].find(row => row.clip.id === clip.link) : null;
        const processedAudio = audioPreview.render({record, position, playing: playing && playback.rate > 0, playbackRate: playback.rate});
        processedSound = !!processedAudio;
        // The joined sound is the plain forward path only: sound edits, changed speed and
        // backward playback keep their own routes and the original sound.
        const plainSpeed = id => !separateSpeedAudio(model.find(record, id));
        const availableChains = processedAudio || playback.rate !== 1 ? [] : chains.filter(c => !failedChains.has(c.key) && c.clips.every(plainSpeed));
        const selected = new Map([...map].filter(([track]) => track !== 'v1').map(([track, row]) =>
            [track, useChain(selectAudioChain(availableChains, track, row.clip, position, volumePreview), track)]));
        const linkedChain = linked && [...selected.values()].some(value => value?.chain.clips.includes(linked.clip.id));
        const originalSeparate = processedAudio || !!linked?.clip.splice || !hasPicture || separateSpeedAudio(linked?.clip);
        const separate = originalSeparate || linkedChain;
        if (hasPicture) {
            const video = videos[active], speedPlayback = previewSpeed(video, clip, playing, linked?.clip); video.muted = !linked || separate || playback.rate < 0 || speedPlayback.muted;
            video._timelineClip = clip;
            delete video.dataset.preparedClip;
            seekMedia(video, mediaURL(api, source.video), (videoRow.frame + (playing && playback.rate > 0 ? 0 : .5)) / 24, speedPlayback.running && map.get('v1')?.clip.id === clip.id, clipVolume(linked?.clip));
            videos.forEach((value, index) => { if (index !== active) stopMedia(value); });
        }
        pictureView = {guide, picture: {clip: hasPicture ? clip : null, size: hasPicture && clipPictureSize(clip, sources), video: videos[active], preview: !!preview, playing}};
        showPicture();
        let slot = 0;
        const usedChains = new Set();
        for (const [track, row] of map) {
            if (processedAudio) break;
            if (isText(row.clip) || track === 'v1') continue;
            const metadata = sources.get(sourceKey(row.clip.source));
            if (!metadata || metadata.missing) continue;
            const carriedByVideo = hasPicture && row.clip.link === clip.id && !originalSeparate;
            const joined = selected.get(track);
            if (joined) {
                usedChains.add(joined.chain.key);
                // Reserve precisely the slot this row used before joining.
                if (!carriedByVideo && metadata.audio && slot < audio.length) stopMedia(audio[slot++]);
                continue;
            }
            if (carriedByVideo || slot >= audio.length) continue;
            const url = mediaURL(api, metadata.audio); if (!url) continue;
            const media = audio[slot++], speed = previewSpeed(media, row.clip, playing); delete media.dataset.audioChain; media.muted = speed.muted;
            media._timelineClip = row.clip;
            seekMedia(media, url, audioSeconds(row.clip, speedAudioFrame(row.clip, shown - row.clip.start)), speed.running, clipVolume(row.clip));
        }
        while (slot < audio.length) stopMedia(audio[slot++]);
        for (const [key, media] of chainAudio) if (!usedChains.has(key)) {
            if (media._attempt && entries.get(media._chain.track) === media._attempt && media._attempt.mode === 'starting') continue;
            const pausedSelection = !playing && availableChains.some(c => c.key === key
                && selectAudioChain([c], c.track, map.get(c.track)?.clip, position, volumePreview));
            stopMedia(media); media.muted = true;
            if (pausedSelection) media.dataset.audioChain = key;
        }
        const next = record.tracks[0].clips.find(value => value.start >= (clip ? clip.start + clip.frames : position) && value.source);
        const metadata = next && sources.get(sourceKey(next.source));
        if (metadata?.video && !metadata.missing && (!hasPicture || videos[active].matches('.fv-ed-visible'))) {
            const idle = videos[1 - active]; idle.muted = true;
            const url = mediaURL(api, metadata.video), seconds = sourceFrame(next, 0) / 24;
            const prepared = JSON.stringify([next.id, url, seconds]);
            if (idle.dataset.preparedClip !== prepared) {
                seekMedia(idle, url, seconds, false, 1, true); idle.dataset.preparedClip = prepared;
            }
        }
        textRenderer?.();
        forcePosition = false;
    }
    function mediaFrame() {
        // Edited sound and reverse playback retain their existing clocks. For
        // native forward playback, wall time must not outrun emitted media and
        // force repeated seeks or a false joined-sound drift failure.
        if (playback.rate !== 1 || processedSound) return null;
        const usable = media => media._editorRunning && !media.error && !media._editorPlayFailed && typeof media.duration === 'number';
        const ready = media => media.readyState >= 3 && !media.seeking && (!media.paused || media.ended);
        for (const media of chainAudio.values()) if (usable(media) && ['starting', 'active'].includes(media._attempt?.mode)
            && entries.get(media._chain.track) === media._attempt && ready(media))
            return media.ended ? media._chain.start + media._chain.frames
                : media._chain.start + (media.currentTime - (media._chain.offset || 0)) * 24;
        const original = [...audio.filter(media => !media.muted), videos[active]];
        for (const media of original) {
            const clip = media._timelineClip;
            if (!usable(media) || !clip || clip.reverse) continue;
            // A shortened proxy or stale duration receipt must not pin the
            // transport forever beyond the media's physical end.
            if (media._editorTime >= media.duration && media.currentTime < media._editorTime - .001) return null;
            if (!ready(media)) continue;
            const start = media.tagName === 'AUDIO' ? audioSeconds(clip, clip.in) : clip.in / 24;
            if (start < 0) continue;
            const position = clip.start + ((media.currentTime - start) * 24 - (clip.speed_phase || 0) / 100) / (percent(clip) / 100);
            return media.ended && position < clip.start + clip.frames - .01 ? null : position;
        }
        // A native decoder buffering at a new entry keeps its requested frame.
        // Missing media continues through the ordinary fallback; failed media has already stopped playback.
        return original.some(usable) ? state.playhead : null;
    }
    function tick(now) {
        if (!playing || disposed) return;
        // Picture or sound this spot needs cannot play: stop here rather than let time run over black or silence.
        // A refused start stops quietly; pressing Play again is the gesture the browser asks for.
        const broken = [...videos, ...audio].find(media => media._editorRunning && (media.error || media._editorPlayStuck));
        if (broken) {
            const reason = broken.error ? 'media_' + broken.error.code : broken._editorPlayStuck;
            pause(); if (reason !== 'blocked') error({code: 'playback', reason}); return;
        }
        const emitted = mediaFrame();
        if (emitted !== null && Number.isFinite(emitted)) {
            clockStart = now; frameStart = Math.max(state.playhead, Math.floor(emitted + .0001)); playback.reset(clockStart, frameStart);
        }
        const total = model.exportFrames(state.record), {frame: position, stop} = playback.advance(now, total);
        const target = stop && playback.rate > 0 && !playback.range ? Math.max(0, total - 1) : position;
        // A loop that starts over is a jump like a user's: the joined sound and the clock start again from there.
        const previous = state.playhead, start = Math.min(total, playback.range?.start ?? 0), end = Math.min(total, playback.range?.end ?? total);
        const raw = frameStart + Math.floor(Math.max(0, now - clockStart) * 24 / 1000) * playback.rate;
        const wrapped = playback.loop && end > start && (playback.rate > 0 ? raw >= end : raw < start);
        ticking = !wrapped && (target - previous) * playback.rate >= 0; state.seek(target); ticking = false;
        if (wrapped && target === previous) {
            resetEntries(); forcePosition = true; render(); clockStart = performance.now(); frameStart = target; playback.reset(clockStart, frameStart);
        }
        if (stop) { pause(); return; }
        raf = requestAnimationFrame(tick);
    }
    const pictureLayout = record => JSON.stringify([record.width, record.height, record.tracks, record.replacements]);
    function clearPreview() { if (preview) { preview.onended = null; preview.pause(); preview.removeAttribute('src'); preview.load(); preview.remove(); preview = null; } previewSize = previewProject = null; closePreview.hidden = true; }
    closePreview.onclick = () => { clearPreview(); render(); };
    function play() {
        if (disposed || playing || playButton.disabled) return;
        clearPreview();
        const total = model.exportFrames(state.record), restart = !playback.range && !playback.loop && playback.rate > 0 && state.playhead >= Math.max(0, total - 1);
        state.seek(playback.begin(restart ? 0 : state.playhead, total));
        resetEntries(); playing = true; frameStart = state.playhead; render();
        // Preview preparation can include a decode and layout. Start the clock
        // after that work so its cost is not charged as already-played frames.
        clockStart = performance.now(); raf = requestAnimationFrame(tick);
        playback.reset(clockStart, frameStart);
    }
    function pause() { playing = false; playback.rate = 1; resetEntries(); forcePosition = true; cancelAnimationFrame(raf); allMedia().forEach(stopMedia); render(); }
    playButton.onclick = () => playing ? pause() : play();
    const unsubscribe = state.subscribe(event => {
        if (event.type === 'selection') { if (pictureEdit.panelOpen && pictureEdit.selected()) clearPreview(); render(); return; }
        if (event.type !== 'playhead' && event.type !== 'record') return;
        if (preview && preservePreview()) previewProject = pictureLayout(state.record);
        else if (event.type === 'playhead' || previewProject !== pictureLayout(state.record)) clearPreview();
        if (!ticking) { resetEntries(); forcePosition = true; }
        if (playing && !ticking) frameStart = state.playhead;
        render();
        if (playing && !ticking) { clockStart = performance.now(); playback.reset(clockStart, frameStart); }
    });
    const stopPictureEdit = pictureEdit.subscribe(() => {
        // Draft geometry updates in picturePreview; only a changed preview part
        // needs media routing, otherwise slider/crop movement would re-seek video.
        const row = pictureEdit.previewRow(located(state.record, state.playhead).get('v1'), state.playhead);
        if (preview && pictureEdit.panelOpen && pictureEdit.selected()) { clearPreview(); render(); }
        else if ((row?.clip.id || null) !== clipId) render();
    });
    render();
    return {element: frame, playback, picturePreview, play, pause, toggle: () => playing ? pause() : play(), refresh: render,
        clearPreview() { clearPreview(); render(); },
        get playing() { return playing; },
        setTextRenderer(callback) { textRenderer = callback; },
        previewVolume(id, value) { volumePreview.set(id, value); render(); },
        clearVolumePreview() { volumePreview.clear(); render(); },
        async previewSource(source) {
            if (disposed) return;
            const previousSize = previewSize || {width: state.record.width, height: state.record.height};
            pause(); clearPreview(); const metadata = sources.get(sourceKey(source)); if (!metadata?.video && !metadata?.audio) { render(); return; }
            preview = el(metadata.video ? 'video' : 'audio', 'fv-ed-source-preview'); preview.controls = true; preview.playsInline = true;
            if (metadata.video && Number.isFinite(metadata.width) && Number.isFinite(metadata.height) && metadata.width >= 2 && metadata.height >= 2)
                previewSize = {width: metadata.width, height: metadata.height};
            else if (!metadata.video) previewSize = previousSize;
            previewProject = pictureLayout(state.record);
            preview.onended = () => { clearPreview(); render(); };
            preview.src = mediaURL(api, metadata.video || metadata.audio); frame.append(preview); closePreview.hidden = false; render(); preview.play().catch(() => {});
        },
        dispose() { disposed = true; playing = false; chainRequest?.abort(); removeChains(); cancelAnimationFrame(raf); unsubscribe(); stopPictureEdit(); clearPreview(); audioPreview.dispose();
            allMedia().forEach(media => { media.pause(); media.onloadedmetadata = media.onerror = null; media.removeAttribute('src'); delete media.dataset.source; media.load(); media.remove(); }); playButton.onclick = closePreview.onclick = null; picturePreview.dispose(); pictureGuide.dispose(); audioContext?.close().catch(() => {}); },
    };
}
