import * as model from './editor_model.js';
import {row, slider, number, toggle, disabledReason} from './editor_adjust.js';
import * as sound from './editor_audio_model.js';
import * as rules from './editor_audio_rules.js';
import {replaceMode, attachReplacePicker} from './editor_audio_replace.js';
import {listenDialogKey} from './dialog_keys.js';
import {registerMenuItem} from './editor_menu.js';
import {clickSelection} from './editor_select.js';
import {noteAtPointer, startPointerNotes, seconds as secondsText} from './editor_audio_notes.js';
import {sourceKey, mediaURL} from './editor_store.js';
const el = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text != null) node.textContent = text; return node; };
let styled = false;
function styles() {
    if (styled) return; styled = true;
    const link = el('link'); link.rel = 'stylesheet'; link.href = new URL('./editor_audio.css', import.meta.url).href; document.head.append(link);
}
const controllers = new WeakMap();
function controller(state) {
    if (!controllers.has(state)) controllers.set(state, {junction: null, listeners: new Set()});
    return controllers.get(state);
}
const announce = value => { for (const listener of [...value.listeners]) listener(); };
const tenths = frames => String(Math.round(frames / 24 * 10) / 10);
const selectedAudio = state => state.selection?.kind === 'clip' ? state.record.tracks.filter(track => track.kind === 'audio')
    .flatMap(track => track.clips).filter(clip => state.selection.ids.includes(clip.id) && clip.source && !['queued', 'running'].includes(clip.pending)) : [];
export const AUDIO_SHORTCUTS = Object.freeze([{key: 'delete', action: 'removeCrossfade'}, {key: 'backspace', action: 'removeCrossfade'}, {key: 'escape', action: 'clearCrossfade'}]);
export function createAudioActions(state, {t = en => en, error = () => {}} = {}) {
    const apply = (label, change, options, report = false) => { try { return state.apply(label, change, options); } catch (value) { if (report) error(value); return {ok: false, reason: value.code || 'audio'}; } };
    return {
        detachAudio: id => { const result = apply(t('Detach audio', '分离原声'), record => sound.detachAudio(record, id)); if (result && result.ok !== false) state.select({kind: 'clip', ids: [id]}); return result; },
        restoreAudio: id => apply(t('Reattach audio', '放回原声'), record => rules.restoreOriginal(record, id), undefined, true),
        replaceAudio: (id, payload) => apply(t('Replace audio', '替换声音'), record => rules.replaceAudio(record, id, payload), {clearRange: true}, true),
        setCrossfade: (id, frames) => apply(t('Crossfade', '交叉淡化'), record => sound.setCrossfade(record, id, frames)),
    };
}
export function handleAudioShortcut(event, state, options = {}) {
    if (event.target.closest?.('input, textarea, select, [contenteditable="true"]') || event.ctrlKey || event.metaKey || event.altKey) return false;
    const value = controller(state), entry = AUDIO_SHORTCUTS.find(row => row.key === event.key.toLowerCase());
    // A pending "replace audio" is a command in progress: Esc ends it and nothing else.
    if (event.key === 'Escape' && replaceMode(state).cancel()) { event.preventDefault(); return true; }
    // The editing view routes keys itself: a media card's keys reach a pending replace here.
    if ((event.key === 'Enter' || event.key === ' ') && replaceMode(state).pick?.(event)) return true;
    if (!value.junction || !entry) return false;
    event.preventDefault();
    if (entry.action === 'removeCrossfade') createAudioActions(state, options).setCrossfade(value.junction, 0);
    value.junction = null; announce(value); return true;
}
export function createAudioIntegration({state, player, t, error, adjust, library, store}) {
    const panels = new Set(), value = controller(state), mode = replaceMode(state);
    startPointerNotes();
    const integration = {actions: createAudioActions(state, {t, error}), createPanel(container, context) {
        const panel = createAudioPanel(container, {state, player, t, error, context}); panels.add(panel); return panel;
    }, dispose() { unsubscribe(); unregister?.(); unregisterMenu(); detachPicker?.(); value.listeners.delete(showJunction); panels.forEach(panel => panel.dispose()); panels.clear(); }};
    const unsubscribe = state.subscribe(event => { if (event.type === 'selection') { value.junction = null; announce(value); } });
    let panel, unregister, controls;
    // Replacing is a command of its own: the menu and the Audio tab start it, a library pick or a drop finishes it.
    // The menu entry exists while some piece can be replaced, so menus of other projects stay as they were.
    let unregisterItem = null;
    const replaceable = () => state.record.tracks.some(track => track.kind === 'audio' && track.clips.some(clip => rules.canReplaceAudio(state.record, clip.id)));
    const syncMenu = () => {
        if (replaceable() && !unregisterItem) unregisterItem = registerMenuItem({where: 'part', group: 'sound', label: context => context.t('Replace audio', '替换声音'),
            enabled: context => context.count === 1 && !!context.clip && rules.canReplaceAudio(context.record, context.clip.id),
            run: context => { mode.start(context.clip.id); }});
        else if (!replaceable() && unregisterItem) { unregisterItem(); unregisterItem = null; }
    };
    const unregisterMenu = () => { unregisterItem?.(); unregisterItem = null; stopMenu(); };
    const stopMenu = state.subscribe(event => { if (event.type === 'record') syncMenu(); });
    syncMenu();
    const detachPicker = library && store ? attachReplacePicker({state, library, store, actions: integration.actions, t, error}) : null;
    const showJunction = () => { if (value.junction) { adjust?.element.querySelector('[data-adjust-tab="audio"]').click(); adjust?.open(); } };
    if (adjust) {
        unregister = adjust.register('audio', {
            render: (container, context) => { controls = container; panel = integration.createPanel(container, context); },
            update(context) {
                // Restoring a detached picture has no selected audio member yet.
                if (context.hasVideo) controls.disabled = !!disabledReason(context);
                panel?.refresh(context);
            },
            dispose: () => { panel?.dispose(); panels.delete(panel); panel = null; },
        });
        value.listeners.add(showJunction);
    }
    return integration;
}
export function createAudioPanel(host, {state, player, t = en => en, error = () => {}, context}) {
    styles(); const value = controller(state), actions = createAudioActions(state, {t, error}), mode = replaceMode(state);
    const panel = el('div', 'fv-ed-audio-panel'), message = el('p', 'fv-ed-audio-message');
    const form = el('div', 'fv-ed-audio-form'), cross = el('div', 'fv-ed-audio-cross');
    const mixedText = t('Mixed', '不同');
    const controls = [];
    let editingVolume = false, disposed = false;
    // A control whose selected clips differ shows "Mixed"; typing a value sets them all.
    function mixable(widget, commit) {
        const native = widget.input.onchange;
        widget.input.onchange = () => {
            if (!widget.mixed) { native(); return; }
            const next = Number(widget.input.value);
            if (widget.input.value === '' || !Number.isFinite(next) || widget.input.validity?.valid === false) return;
            setMixed(widget, false); widget.value = next; commit(next);
        };
    }
    function setMixed(widget, on) {
        widget.mixed = on; widget.input.placeholder = on ? mixedText : '';
        if (on) widget.dataset.mixed = 'true'; else delete widget.dataset.mixed;
    }
    function input(parent, name, label, min, max, step, type = 'number', callbacks = {}) {
        const widget = (type === 'range' ? slider : number)({min, max, step, label, ...callbacks});
        if (type === 'number') {
            const commit = widget.input.onchange;
            widget.input.onchange = () => {
                const value = Number(widget.input.value);
                if (widget.input.value !== '' && Number.isFinite(value)) {
                    const limit = Number(widget.input.max);
                    if (value > limit) callbacks.onClamp?.(value, limit);
                    widget.input.value = String(Math.round(Math.max(Number(widget.input.min), Math.min(limit, value)) / step) * step);
                }
                commit();
            };
        }
        if (callbacks.onChange) mixable(widget, callbacks.onChange);
        widget.input.className = `fv-ed-audio-${name}${name === 'volume' ? ' fv-ed-volume' : ''}`;
        parent.append(row(label, widget)); controls.push(widget); return widget;
    }
    const volume = input(form, 'volume', t('Volume', '音量'), 0, 200, 1, 'range', {
        value: 100, format: value => `${value}%`,
        onInput: gain => { editingVolume = true; delete volume.dataset.mixed; selectedAudio(state).forEach(clip => player?.previewVolume(clip.id, gain / 100)); },
        onChange: gain => { const ids = selectedAudio(state).map(clip => clip.id); editingVolume = false;
            apply(t('Volume', '音量'), record => ids.reduce((next, id) => model.setVolume(next, id, gain / 100), record)); },
    });
    // Generated music only: lowered while someone speaks in the other sound. On by default.
    const duck = toggle(t('Lower during speech', '对白时降低音量'), {
        onChange: on => apply(t('Lower during speech', '对白时降低音量'), record => sound.setDuck(record, selectedAudio(state).filter(sound.duckable).map(clip => clip.id), on)),
    });
    duck.classList.add('fv-ed-audio-duck'); form.append(duck); controls.push(duck);
    const fadeIn = input(form, 'fade-in', t('Fade in', '淡入'), 0, 5, 1 / 24, 'number', {
        onChange: seconds => apply(t('Fade in', '淡入'), record => sound.setFades(record, selectedAudio(state).map(clip => clip.id), 'fade_in', Math.round(seconds * 24))),
    });
    const fadeOut = input(form, 'fade-out', t('Fade out', '淡出'), 0, 5, 1 / 24, 'number', {
        onChange: seconds => apply(t('Fade out', '淡出'), record => sound.setFades(record, selectedAudio(state).map(clip => clip.id), 'fade_out', Math.round(seconds * 24))),
    });
    const fadeNote = el('p', 'fv-ed-audio-note'); fadeNote.hidden = true; form.append(fadeNote);
    const detach = el('button', 'fv-ed-audio-detach', t('Detach audio', '分离原声')); detach.type = 'button'; form.append(detach);
    const replace = el('button', 'fv-ed-audio-replace', t('Replace audio', '替换声音')); replace.type = 'button'; form.append(replace);
    cross.append(el('p', '', t('Crossfade', '交叉淡化')));
    const limitNote = frames => noteAtPointer(t(`At most ${secondsText(frames)} s`, `最多 ${secondsText(frames)} 秒`), panel);
    const junctionLimit = () => { try { return sound.crossfadeLimit(state.record, value.junction); } catch { return null; } };
    const duration = input(cross, 'duration', t('Duration', '时长'), 0, 5, 1 / 24, 'number', {
        onChange: seconds => { if (value.junction) actions.setCrossfade(value.junction, Math.round(seconds * 24)); render(); },
        onClamp: () => { const reach = junctionLimit(); if (reach) limitNote(reach.limit); },
    });
    // At the longest length, one more step says why it stops.
    duration.input.onkeydown = event => {
        if (event.key !== 'ArrowUp') return;
        const reach = junctionLimit(); if (reach && Math.round(Number(duration.input.value) * 24) >= reach.limit) limitNote(reach.limit);
    };
    const remove = el('button', 'fv-ed-audio-remove', t('Remove', '移除')); remove.type = 'button'; cross.append(remove);
    panel.append(message, form, cross); host.append(panel);
    // Shows the common value, or "Mixed" when the selected clips differ.
    function show(widget, values, paint) {
        const mixed = values.length > 1 && values.some(item => item !== values[0]);
        if (document.activeElement === widget.input) return;
        paint(mixed ? 0 : (values[0] ?? 0)); setMixed(widget, mixed);
        if (mixed) widget.input.value = '';
    }
    function render(nextContext) {
        if (disposed) return;
        if (nextContext) context = nextContext;
        if (value.junction && !model.find(state.record, value.junction)?.crossfade) value.junction = null;
        const junction = value.junction && model.find(state.record, value.junction);
        form.hidden = !!junction; cross.hidden = !junction; message.hidden = !!context || !!junction;
        if (junction) {
            if (document.activeElement !== duration.input) duration.value = junction.crossfade / 24;
            const reach = junctionLimit();
            duration.input.max = String((reach?.limit ?? Math.min(120, junction.frames)) / 24);
            return;
        }
        const clips = selectedAudio(state), selected = context?.clips || (state.selection?.kind === 'clip' ? state.record.tracks.flatMap(track => track.clips).filter(clip => state.selection.ids.includes(clip.id)) : []);
        const count = context?.count ?? selected.filter(clip => !selected.some(other => other.link === clip.id && sound.trackOf(state.record, other.id).kind === 'video')).length;
        const ctx = context || {t, single: count === 1, multiple: count > 1, hasAudio: !!clips.length, hasVideo: selected.some(clip => sound.trackOf(state.record, clip.id).kind === 'video'), busy: selected.some(clip => ['queued', 'running'].includes(clip.pending))};
        message.textContent = count > 1 ? t(`${count} parts selected`, `已选 ${count} 段`) : '';
        message.hidden = !!context || count === 1;
        const locked = selected.some(clip => sound.trackOf(state.record, clip.id).locked);
        const reason = disabledReason(ctx, {needs: 'audio'}) || (locked ? t('This track is locked', '这条轨道已锁定') : '');
        for (const field of [volume, fadeIn, fadeOut]) { field.disabled = !!reason; field.title = ''; field.input.title = ''; }
        const music = clips.filter(sound.duckable);
        duck.hidden = !music.length || music.length !== clips.length; duck.disabled = !!reason;
        if (!duck.hidden) { duck.value = music.every(clip => !!clip.duck); duck.input.indeterminate = music.some(clip => !!clip.duck) && !duck.value; }
        if (!editingVolume) {
            const gains = clips.map(clip => Math.round((clip.volume ?? 1) * 100)), mixed = gains.length > 1 && gains.some(gain => gain !== gains[0]);
            volume.value = mixed ? 100 : gains[0] ?? 100; setMixed(volume, mixed);
            if (mixed) volume.output.textContent = mixedText;
        }
        const cap = clips.length ? sound.fadeCap(clips) : 120;
        for (const [field, name] of [[fadeIn, 'fade_in'], [fadeOut, 'fade_out']]) {
            field.input.max = String((clips.length ? cap : 120) / 24);
            show(field, clips.map(clip => clip[name] || 0), frames => { field.value = frames / 24; });
        }
        if (clips.length > 1) {
            fadeNote.textContent = t(`At most ${tenths(cap)} s`, `最多 ${tenths(cap)} 秒`);
        } else fadeNote.textContent = '';
        fadeNote.hidden = !fadeNote.textContent;
        const video = count === 1 ? selected.find(clip => sound.trackOf(state.record, clip.id).kind === 'video') : null;
        detach.textContent = video?.detached_from ? t('Reattach audio', '放回原声') : t('Detach audio', '分离原声');
        const detachReason = disabledReason(ctx, {needs: 'video', single: true}) || (locked ? t('This track is locked', '这条轨道已锁定') : '');
        detach.disabled = !!detachReason || !video?.source || !(video.link || rules.canRestoreOriginal(state.record, video.id));
        detach.title = detach.textContent;
        detach.onclick = () => { if (video) video.detached_from ? actions.restoreAudio(video.id) : actions.detachAudio(video.id); };
        const target = count === 1 && clips.length === 1 && rules.canReplaceAudio(state.record, clips[0].id) ? clips[0] : null;
        replace.hidden = !target && mode.id === null; replace.disabled = !target || locked;
        replace.setAttribute('aria-pressed', String(!!target && mode.id === target.id)); replace.title = replace.textContent;
        replace.onclick = () => { if (target) mode.id === target.id ? mode.cancel() : mode.start(target.id); };
    }
    function apply(label, change) { try { state.apply(label, change); } catch (value) { error(value); return {ok: false, reason: value.code || 'audio'}; } finally { player?.clearVolumePreview(); render(); } }
    remove.onclick = () => { if (value.junction) actions.setCrossfade(value.junction, 0); value.junction = null; announce(value); };
    const unsubscribe = context ? () => {} : state.subscribe(event => { if (event.type === 'selection') value.junction = null; if (event.type === 'record' || event.type === 'selection') render(); });
    const refreshJunction = () => render(); value.listeners.add(refreshJunction); mode.listeners.add(refreshJunction); render();
    return {element: panel, refresh: render, dispose() { if (disposed) return; disposed = true; unsubscribe(); value.listeners.delete(refreshJunction); mode.listeners.delete(refreshJunction); player?.clearVolumePreview(); panel.remove();
        controls.forEach(control => control.dispose()); detach.onclick = replace.onclick = remove.onclick = null; }};
}
const paths = {muted: 'M2 6h3l4-3v10l-4-3H2zM12 5l3 6M15 5l-3 6', solo: 'M5 4h6M5 4v4h6v4H5', locked: 'M4 7h8v7H4zM5 7V4a3 3 0 0 1 6 0v3'};
export function attachAudioTracks(element, {state, t = en => en, actions = {}}) {
    styles(); const value = controller(state), audioActions = createAudioActions(state, {t, error: actions.editError}), mode = replaceMode(state);
    const notice = el('div', 'fv-ed-audio-notice'); notice.hidden = true; notice.setAttribute('role', 'status'); element.append(notice);
    let noticeTimer = null;
    const lockedText = t('This track is locked', '这条轨道已锁定');
    function notify() { return {ok: false, reason: 'locked'}; }
    function render() {
        element.dataset.trackSwitches = 'true';
        for (const track of state.record.tracks) {
            if (!['video', 'audio'].includes(track.kind)) continue;
            const head = [...element.querySelectorAll('.fv-ed-track-head')].find(node => node.dataset.track === track.id);
            const row = [...element.querySelectorAll('.fv-ed-track-row')].find(node => node.dataset.track === track.id);
            if (!head || !row) continue;
            row.dataset.locked = String(!!track.locked); row.dataset.muted = String(!!track.muted);
            let controls = head.querySelector('.fv-ed-audio-track-controls');
            if (!controls) { controls = el('span', 'fv-ed-audio-track-controls'); head.append(controls); }
            controls.replaceChildren();
            for (const name of track.kind === 'audio' ? ['muted', 'solo', 'locked'] : ['muted', 'locked']) {
                const active = !!track[name], button = el('button', 'fv-ed-audio-track-switch'); button.type = 'button'; button.dataset.flag = name; button.dataset.track = track.id;
                const labels = {muted: active ? t('Unmute', '取消静音') : t('Mute this track', '静音这条轨道'), solo: active ? t('Hear all tracks', '恢复全部轨道') : t('Solo this track', '只听这条轨道'), locked: active ? t('Unlock', '解除锁定') : t('Lock this track', '锁定这条轨道')};
                button.title = labels[name];
                // The recording track is mixed like the others but is only ever heard or muted: solo and lock stay in place, unavailable.
                if (track.id === 'a3' && name !== 'muted') { button.disabled = true; button.title = name === 'solo' ? t('Not available on the recording track', '录音轨不能单独试听') : t('Not available on the recording track', '录音轨不能锁定'); }
                button.setAttribute('aria-label', button.title); button.setAttribute('aria-pressed', String(active));
                const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'), path = document.createElementNS(svg.namespaceURI, 'path'); svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('aria-hidden', 'true'); path.setAttribute('d', paths[name]); svg.append(path); button.append(svg);
                // Switches are view settings: they change every snapshot alike and never enter the undo history.
                const activate = () => { try { state.settle(record => sound.setTrackFlag(record, track.id, name, !active)); } catch (error) { actions.editError?.(error); } };
                button.onclick = activate; button.onkeydown = event => { if ((event.key === ' ' || event.key === 'Enter') && !event.ctrlKey && !event.metaKey) { event.preventDefault(); activate(); } };
                controls.append(button);
            }
            for (const clip of track.clips) {
                const node = [...row.querySelectorAll('.fv-ed-clip')].find(item => item.dataset.clip === clip.id);
                if (!node) continue;
                node.title = ''; node.tabIndex = track.locked ? -1 : 0; node.dataset.replacing = String(mode.id === clip.id);
                let line = node.querySelector('.fv-ed-audio-fades');
                if (!line) { line = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); line.setAttribute('class', 'fv-ed-audio-fades'); line.setAttribute('viewBox', '0 0 100 100'); line.setAttribute('preserveAspectRatio', 'none'); line.setAttribute('aria-hidden', 'true'); node.append(line); }
                line.replaceChildren();
                for (const [name, incoming] of [['fade_in', true], ['fade_out', false]]) if (clip[name]) {
                    const width = Math.min(clip[name], clip.frames / 2) / clip.frames * 100, path = document.createElementNS(line.namespaceURI, 'path');
                    path.setAttribute('d', incoming ? `M0 100L${width} 0` : `M${100 - width} 0L100 100`); line.append(path);
                }
            }
            const junctions = new Map([...row.querySelectorAll('.fv-ed-audio-junction')].map(node => [node.dataset.right, node]));
            if (track.kind !== 'audio') { junctions.forEach(node => node.remove()); continue; }
            track.clips.forEach((right, index) => {
                const left = track.clips[index - 1]; if (!left?.source || !right.source || left.start + left.frames !== right.start) return;
                const diamond = junctions.get(right.id) || el('button', 'fv-ed-audio-junction'); junctions.delete(right.id); diamond.type = 'button'; diamond.dataset.right = right.id; diamond.dataset.active = String(!!right.crossfade);
                diamond.setAttribute('aria-label', t('Crossfade', '交叉淡化')); diamond.title = t('Crossfade', '交叉淡化'); diamond.disabled = !!track.locked || [left, right].some(clip => ['queued', 'running'].includes(clip.pending));
                diamond.style.left = `${right.start * state.zoom / 24}px`; diamond.setAttribute('aria-pressed', String(value.junction === right.id));
                const activate = () => {
                    if (!right.crossfade) {
                        const reach = sound.crossfadeLimit(state.record, right.id);
                        audioActions.setCrossfade(right.id, 12); if (reach.limit < 12) noteAtPointer(t(`At most ${secondsText(reach.limit)} s`, `最多 ${secondsText(reach.limit)} 秒`), diamond);
                        return;
                    } state.select({kind: 'clip', ids: [right.id]}); value.junction = right.id; announce(value); render(); };
                diamond.onclick = activate; diamond.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); } };
                if (diamond.parentNode !== row) row.append(diamond);
            });
            junctions.forEach(node => node.remove());
        }
    }
    function hit(event) {
        const direct = event.target.closest?.('.fv-ed-track-row');
        if (direct) return state.record.tracks.find(track => track.id === direct.dataset.track && ['video', 'audio'].includes(track.kind));
        const content = element.querySelector('.fv-ed-track-content'), bounds = content?.getBoundingClientRect();
        const row = bounds && [...element.querySelectorAll('.fv-ed-track-row')].find(node => event.clientY - bounds.top >= parseFloat(node.style.top) && event.clientY - bounds.top <= parseFloat(node.style.top) + parseFloat(node.style.height));
        return row && state.record.tracks.find(track => track.id === row.dataset.track && ['video', 'audio'].includes(track.kind));
    }
    function pointer(event) {
        if (event.target.closest?.('.fv-ed-audio-track-controls,.fv-ed-audio-junction')) { event.stopPropagation(); return; }
        if (event.target.closest?.('.fv-ed-ruler,.fv-ed-playhead-handle,.fv-ed-track-heads,.fv-ed-track-zoom')) return;
        const track = hit(event); if (!track) return;
        const bounds = element.querySelector('.fv-ed-track-content').getBoundingClientRect(), at = (event.clientX - bounds.left) * 24 / state.zoom;
        const clipId = event.target.closest?.('.fv-ed-clip')?.dataset.clip || track?.clips.find(clip => at >= clip.start && at < clip.start + clip.frames)?.id;
        if (track?.locked) { event.preventDefault(); event.stopImmediatePropagation(); notify(event); return; }
        // The free half of a pair with a locked mate can be looked at, never dragged.
        if (clipId && model.pair(state.record, clipId).some(clip => sound.trackOf(state.record, clip.id).locked)) {
            event.preventDefault(); event.stopImmediatePropagation(); notify(event);
            if (event.button === 0) state.select(clickSelection(state.record, state.selection, clipId, {toggle: event.ctrlKey || event.metaKey, shift: event.shiftKey}));
        }
    }
    // A drop never replaces by itself: it replaces only the piece the Replace audio command is waiting on.
    function drop(event) {
        if (mode.id === null || !Array.from(event.dataTransfer?.types || []).includes('application/x-freevideo-source')) return;
        const track = hit(event); if (!track || track.locked || track.kind !== 'audio') return;
        let payload; try { payload = JSON.parse(event.dataTransfer.getData('application/x-freevideo-source')); } catch { return; }
        if (!payload?.hasAudio) return;
        const bounds = element.querySelector('.fv-ed-track-content').getBoundingClientRect(), at = Math.max(0, Math.round((event.clientX - bounds.left) * 24 / state.zoom));
        const clip = track.clips.find(clip => clip.id === mode.id && at >= clip.start && at < clip.start + clip.frames);
        if (!clip) return;
        event.preventDefault(); event.stopImmediatePropagation(); event.dataTransfer.dropEffect = 'copy';
        if (event.type !== 'drop') return;
        const separator = payload.key?.indexOf(':'); if (!(separator > 0)) return;
        const source = {kind: payload.key.slice(0, separator), id: payload.key.slice(separator + 1)};
        if (audioActions.replaceAudio(clip.id, {...payload, source}).ok !== false) mode.cancel();
        element.querySelector('.fv-ed-insertion-line').hidden = true; element.querySelector('.fv-ed-snap-line').hidden = true;
    }
    const keyboard = event => { if (handleAudioShortcut(event, state, {t, error: actions.editError})) event.stopImmediatePropagation(); };
    const stopKeys = listenDialogKey(element, 'keydown', keyboard, true);
    element.addEventListener('pointerdown', pointer, true); element.addEventListener('dragover', drop, true); element.addEventListener('drop', drop, true);
    const unsubscribe = state.subscribe(event => { if (event.type === 'record' || event.type === 'zoom') render(); });
    value.listeners.add(render); mode.listeners.add(render); render();
    return {refresh: render, dispose() { unsubscribe(); value.listeners.delete(render); mode.listeners.delete(render); clearTimeout(noticeTimer); stopKeys(); element.removeEventListener('pointerdown', pointer, true); element.removeEventListener('dragover', drop, true); element.removeEventListener('drop', drop, true); notice.remove(); }};
}

// A decoded, bounded look-ahead path gives the sample envelope to Web Audio.
// The unchanged media path remains active for records without audio edits.
// Speech spans per source for "Lower during speech": asked once, polled while the server works them out.
// A source that cannot be read, or a server without the route, has no speech: nothing is lowered under it.
export function createSpeechSpans(fetchSpans, {retry = 1000} = {}) {
    const map = new Map(), asking = new Set(); let version = 0, disposed = false;
    const ask = key => {
        if (disposed || map.has(key) || asking.has(key)) return;
        asking.add(key);
        Promise.resolve().then(() => fetchSpans(key)).then(value => {
            asking.delete(key); if (disposed) return;
            if (value?.state === 'pending' || value?.reason === 'model_missing') { setTimeout(() => ask(key), value?.state === 'pending' ? retry : retry * 10); return; }
            map.set(key, Array.isArray(value?.spans) ? value.spans : []); version++;
        }).catch(() => { asking.delete(key); if (!disposed) { map.set(key, []); version++; } });
    };
    return {map, want(keys) { keys.forEach(ask); }, get version() { return version; }, dispose() { disposed = true; }};
}
// A picture's own sound: still joined to it, or taken from it and kept.
export const isOriginalSound = clip => !!clip.link || !!clip.detached_from;
export function createAudioPreview({state, sources, api, volumePreview = new Map(), sourcePreview = new Map(), readSamples = null, error = () => {}, speech = null}) {
    let context = null, generation = 0, timer = null, identity = null, pending = false, disposed = false;
    const cache = new Map(), nodes = new Set(); let origin = 0, cursor = 0, previousPosition = 0;
    const ducking = (record, rate = 48000) => speech && sound.duckSources(record.tracks).length ? sound.duckRanges(record.tracks, speech.map, rate) : [];
    const edited = record => record.tracks.some(track => track.muted || track.solo || track.clips.some(clip => clip.fade_in || clip.fade_out || clip.crossfade))
        || sourcePreview.size > 0 || ducking(record).length > 0;
    function stop() { generation++; clearTimeout(timer); timer = null; pending = false; identity = null; nodes.forEach(node => { try { node.stop(); } catch {} }); nodes.clear(); }
    async function decoded(clip) {
        const metadata = sources.get(sourceKey(clip.source)), url = mediaURL(api, metadata?.audio);
        if (!url || metadata?.missing) throw new Error('source_missing');
        if (!cache.has(url)) {
            while (cache.size >= 4) cache.delete(cache.keys().next().value);
            const request = fetch(url).then(response => { if (!response.ok) throw new Error('source_missing'); return response.arrayBuffer(); })
                .then(data => context.decodeAudioData(data));
            cache.set(url, request); request.catch(() => cache.delete(url));
        }
        return cache.get(url);
    }
    function raw(buffer, clip, offset, count) {
        const start = Math.round((clip.splice ? clip.splice.audio_samples / 32000 + (clip.in - clip.splice.frames) / 24 : clip.in / 24) * context.sampleRate) + offset;
        return [0, 1].map(channel => { const data = buffer.getChannelData(Math.min(channel, buffer.numberOfChannels - 1)), output = new Float64Array(count);
            for (let i = 0; i < count; i++) output[i] = data[start + i] || 0; return output; });
    }
    async function prepared(clip, offset, count) {
        if ((clip.speed ?? 1) !== 1 || clip.reverse) {
            if (readSamples) return readSamples({clip, offset, count, rate: context.sampleRate});
            throw new model.EditError('audio_prepare');
        }
        return raw(await decoded(clip), clip, offset, count);
    }
    async function chunk(record, at, count) {
        const rate = context.sampleRate, total = [new Float64Array(count), new Float64Array(count)], lowered = ducking(record, rate);
        for (const track of record.tracks.filter(track => track.kind === 'audio')) {
            if (!sound.trackAudible(track, record.tracks)) continue;
            const block = [new Float64Array(count), new Float64Array(count)], extras = {};
            for (const clip of track.clips) {
                if (!clip.source || !sound.clipAudible(clip, track, record.tracks)) continue;
                const start = Math.round(clip.start * rate / 24), stop = Math.round((clip.start + clip.frames) * rate / 24);
                const a = Math.max(start, at), b = Math.min(stop, at + count);
                if (b <= a) continue;
                const data = await prepared(clip, a - start, b - a);
                const duck = clip.duck && lowered.length ? sound.duckGain(lowered, a, b - a, rate) : null;
                data.forEach((channel, c) => { for (let i = 0; i < channel.length; i++) {
                    const local = a - start + i, length = stop - start;
                    const prior = track.clips[track.clips.indexOf(clip) - 1], next = track.clips[track.clips.indexOf(clip) + 1];
                    const edge = Math.max(2, Math.round(rate * .005));
                    let gain = clip.volume ?? 1;
                    if (!clip.fade_in && (!prior?.source || prior.start + prior.frames !== clip.start || !prior.volume)) gain *= Math.min(1, local / (edge - 1));
                    if (!clip.fade_out && (!next?.source || next.start !== clip.start + clip.frames || !next.volume)) gain *= Math.min(1, (length - 1 - local) / (edge - 1));
                    if (duck) gain *= duck[i];
                    block[c][a - at + i] = channel[i] * gain;
                }});
            }
            for (const plan of sound.crossfadeReads(track, rate)) {
                if (plan.boundary + plan.post < at || plan.boundary - plan.pre >= at + count) continue;
                const left = model.find(record, plan.left), right = model.find(record, plan.right);
                const empty = () => [new Float64Array(0), new Float64Array(0)];
                extras[right.id] = {left_tail: plan.left_tail && sound.clipAudible(left, track, record.tracks) ? raw(await decoded(left), left, Math.round(left.frames * rate / 24), plan.left_tail) : empty(),
                    right_head: plan.right_head && sound.clipAudible(right, track, record.tracks) ? raw(await decoded(right), right, -plan.right_head, plan.right_head) : empty()};
            }
            const processed = sound.applyTrackAudio(block, at, track, record.tracks, {rate, extras});
            processed.forEach((channel, c) => { for (let i = 0; i < count; i++) total[c][i] += channel[i]; });
        }
        return total;
    }
    async function schedule(record, token) {
        if (pending || disposed || token !== generation) return; pending = true;
        try {
            const rate = context.sampleRate, count = Math.min(Math.round(rate / 4), Math.round(model.duration(record) * rate / 24) - cursor);
            if (count <= 0) return;
            const data = await chunk(record, cursor, count);
            if (token !== generation || disposed) return;
            const buffer = context.createBuffer(2, count, rate);
            data.forEach((channel, c) => { const target = buffer.getChannelData(c); for (let i = 0; i < count; i++) target[i] = Math.min(1, Math.max(-1, channel[i])); });
            const node = context.createBufferSource(); node.buffer = buffer; node.connect(context.destination); nodes.add(node); node.onended = () => { nodes.delete(node); node.disconnect(); };
            const when = origin + cursor / rate;
            // Delayed fetches skip elapsed samples instead of replaying old sound.
            const late = Math.max(0, context.currentTime - when);
            if (late < count / rate) node.start(Math.max(when, context.currentTime), late);
            else { nodes.delete(node); node.disconnect(); }
            cursor += count; timer = setTimeout(() => schedule(record, token), Math.max(0, (origin + cursor / rate - context.currentTime - .15) * 1000));
        } catch (value) { stop(); error(value); }
        finally { pending = false; }
    }
    return {
        render({record, position, playing, playbackRate = 1, silenceOriginal = false}) {
            speech?.want(sound.duckSources(record.tracks));
            if (!edited(record)) { if (identity) stop(); return false; }
            if (sourcePreview.size) record = {...record, tracks: record.tracks.map(track => ({...track, clips: track.clips.map(clip => sourcePreview.has(clip.id) ? {...clip, source: sourcePreview.get(clip.id)} : clip)}))};
            if (volumePreview.size) record = {...record, tracks: record.tracks.map(track => ({...track, clips: track.clips.map(clip => volumePreview.has(clip.id) ? {...clip, volume: volumePreview.get(clip.id)} : clip)}))};
            // While a take is recorded the picture's own sound stays out of the mix.
            if (silenceOriginal) record = {...record, tracks: record.tracks.map(track => ({...track, clips: track.clips.filter(clip => !isOriginalSound(clip))}))};
            if (!playing) { stop(); previousPosition = position; return true; }
            if (playbackRate !== 1) {
                const unavailable = `shuttle:${playbackRate}`;
                if (identity !== unavailable) { stop(); identity = unavailable; error(new model.EditError('audio_prepare')); }
                return true;
            }
            const nextIdentity = JSON.stringify(record.tracks) + `:${speech?.version ?? 0}`;
            if (identity !== nextIdentity || Math.abs(position - previousPosition) > 3) {
                stop(); identity = nextIdentity;
                try { context ||= new (window.AudioContext || window.webkitAudioContext)({sampleRate: 48000}); context.resume().catch(error); }
                catch (value) { error(value); return true; }
                cursor = Math.round(position * context.sampleRate / 24); origin = context.currentTime - cursor / context.sampleRate;
                schedule(record, generation);
            }
            previousPosition = position; return true;
        },
        dispose() { disposed = true; stop(); cache.clear(); speech?.dispose(); context?.close().catch(() => {}); },
    };
}
