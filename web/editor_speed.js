import {row, number, disabledReason} from './editor_adjust.js';
import * as model from './editor_model.js';
import {setSpeed, normalizeSpeed, percent, sourceFrames} from './editor_speed_model.js';

const el = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text != null) node.textContent = text; return node; };
let styles;
function loadStyles() {
    if (!styles) {
        const link = el('link'); link.rel = 'stylesheet'; link.href = new URL('editor_speed.css', import.meta.url).href;
        styles = 'sheet' in link ? new Promise(resolve => { link.onload = link.onerror = resolve; }) : Promise.resolve();
        document.head.append(link);
    }
    return styles;
}
export const speedShortcuts = Object.freeze([]);
export function handleSpeedKey(event) {
    if (event.target.closest?.('input,textarea,select,[contenteditable=true]')) return false;
    return false;
}
export function speedErrorText(error, t) {
    if (error?.code === 'speed_pending') return t('Speed can be adjusted after generation is complete.', '生成完成后可以调整速度。');
    if (error?.code === 'speed_locked') return t('This track is locked', '这条轨道已锁定');
    if (error?.code === 'speed') return t('Enter a speed from 0.25× to 4×.', '请输入 0.25× 至 4× 的速度。');
    return null;
}
export function previewSpeed(media, clip, playing, sound = clip) {
    const rate = percent(clip) / 100;
    if (media.playbackRate !== rate) media.playbackRate = rate;
    if (media.preservesPitch !== true) media.preservesPitch = true;
    media._editorSpeedExact = media.tagName === 'VIDEO' && (percent(clip) !== 100 || (clip.speed_phase ?? 0) !== 0);
    const muted = !!sound?.reverse || !!((percent(sound || {}) !== 100 || sound?.reverse) && sound?.speed_audio === 'mute');
    return {running: playing && !clip.reverse, muted};
}
export function speedSeek(media) {
    if (!media._editorSpeedExact) return false;
    const changed = media._editorSpeedTime !== media._editorTime;
    media._editorSpeedTime = media._editorTime; return changed;
}
export const speedAudioFrame = (clip, k) => clip.in + (k * percent(clip) + (clip.speed_phase ?? 0)) / 100;
export const separateSpeedAudio = clip => !!clip && (percent(clip) !== 100 || clip.reverse === true || (clip.speed_phase ?? 0) !== 0);
export function renderSpeedBadge(host, clip, t) {
    let badge = host.querySelector('.fv-ed-speed-badges');
    if (percent(clip) === 100 && !clip.reverse) { badge?.remove(); return; }
    loadStyles();
    if (!badge) { badge = el('span', 'fv-ed-speed-badges'); host.append(badge); }
    const values = [];
    if (percent(clip) !== 100) values.push(el('span', 'fv-ed-speed-badge', `${percent(clip) / 100}×`));
    if (clip.reverse) values.push(el('span', 'fv-ed-reverse-badge', t('Reverse', '倒放')));
    badge.replaceChildren(...values);
}

export function createSpeedPanel(host, {state, t, onError = () => {}, context}) {
    const ready = loadStyles();
    const panel = el('div', 'fv-ed-speed-panel'), hint = el('p', 'fv-ed-speed-hint'); hint.setAttribute('role', 'status');
    const speedNumber = number({min: .25, max: 4, step: .01, value: 1, label: t('Speed', '速度'),
        onChange: speed => apply({speed})});
    const input = speedNumber.input; input.className = 'fv-ed-speed-input';
    const commit = input.onchange;
    // A speed outside 0.25–4× is set to the nearest limit and said so; text that is not a number is put back with the rule.
    input.onchange = () => {
        const value = Number(input.value);
        if (input.value === '' || !Number.isFinite(value)) { say(t('Enter a speed from 0.25× to 4×.', '请输入 0.25× 至 4× 的速度。')); commit(); return; }
        if (value < .25 || value > 4) {
            const limit = value < .25 ? .25 : 4;
            input.value = String(limit);
            say(value < .25 ? t('The slowest is 0.25×; set to 0.25×.', '最慢 0.25×，已设为 0.25×。') : t('The fastest is 4×; set to 4×.', '最快 4×，已设为 4×。'));
        } else { say(''); input.value = String(normalizeSpeed(value)); }
        commit();
    };
    const speedLabel = row(t('Speed', '速度'), speedNumber, el('span', '', '×')); speedLabel.classList.add('fv-ed-speed-label');
    const presets = el('div', 'fv-ed-speed-presets'); presets.setAttribute('role', 'group'); presets.setAttribute('aria-label', t('Speed', '速度'));
    const buttons = [.25, .5, 1, 2, 4].map(speed => {
        const button = el('button', '', `${speed}×`); button.type = 'button'; button.dataset.speed = String(speed);
        button.onclick = () => apply({speed}); presets.append(button); return button;
    });
    const length = el('p', 'fv-ed-speed-length');
    const reverseLabel = el('label', 'fv-ed-speed-reverse', t('Reverse', '倒放'));
    const reverse = el('input'); reverse.type = 'checkbox'; reverse.setAttribute('role', 'switch'); reverse.setAttribute('aria-label', t('Reverse', '倒放')); reverseLabel.append(reverse);
    const audio = el('div', 'fv-ed-speed-audio'); audio.setAttribute('role', 'group'); audio.setAttribute('aria-label', t('Audio', '声音'));
    const audioLabel = el('span', '', t('Audio', '声音'));
    const follows = el('button', '', t('Follow speed', '跟随速度')), mute = el('button', '', t('Mute', '静音'));
    for (const [button, value] of [[follows, 'follow'], [mute, 'mute']]) { button.type = 'button'; button.dataset.speedAudio = value; button.onclick = () => apply({speed_audio: value}); }
    audio.append(audioLabel, follows, mute);
    const reverseHint = el('p', 'fv-ed-speed-reverse-hint', t('Reverse audio can be heard after export.', '倒放的声音在导出后才能听到。'));
    // One line under the speed for what was just refused or changed (fixed place; it does not move the controls below).
    const note = el('p', 'fv-ed-speed-note'); note.setAttribute('role', 'status'); note.hidden = true;
    let noteTimer = null;
    function say(text) {
        clearTimeout(noteTimer); note.textContent = text; note.hidden = !text;
        if (text) noteTimer = setTimeout(() => { note.hidden = true; note.textContent = ''; }, 6000);
    }
    const reset = el('button', 'fv-ed-speed-reset', t('Reset speed', '恢复原速')); reset.type = 'button'; reset.onclick = () => apply({speed: 1});
    panel.append(hint, speedLabel, note, presets, length, reverseLabel, audio, reverseHint, reset); host.append(panel);
    let disposed = false;
    function selection() {
        if (state.selection?.kind !== 'clip') return [];
        const seen = new Set(), values = [];
        for (const id of state.selection.ids) {
            if (seen.has(id)) continue;
            const clips = model.pair(state.record, id); clips.forEach(clip => seen.add(clip.id));
            if (clips.length) values.push(clips);
        }
        return values;
    }
    function update(nextContext) {
        if (disposed) return;
        if (nextContext) context = nextContext;
        const groups = selection(), clips = groups.flat(), primary = groups[0]?.[0];
        const busy = clips.some(clip => !clip.source || clip.pending), locked = state.record.tracks.some(track => track.locked && track.clips.some(clip => clips.includes(clip)));
        const disabled = !primary || busy || locked, sounds = clips.filter(clip => clip.volume !== undefined);
        for (const control of [input, ...buttons, reverse, follows, mute, reset]) control.title = '';
        hint.textContent = groups.length > 1 ? t(`${groups.length} parts selected`, `已选 ${groups.length} 段`) : '';
        hint.hidden = !hint.textContent;
        const sameSpeed = !primary || clips.every(clip => percent(clip) === percent(primary));
        speedNumber.value = percent(primary || {}) / 100; if (!sameSpeed) input.value = ''; input.placeholder = sameSpeed ? '' : t('Mixed', '不同'); input.disabled = disabled;
        for (const button of buttons) { button.disabled = disabled; button.setAttribute('aria-pressed', String(sameSpeed && Number(button.dataset.speed) === percent(primary || {}) / 100)); }
        const single = groups.length <= 1;
        reverse.checked = !!primary?.reverse; reverse.disabled = disabled || !single;
        reverse.title = t('Reverse', '倒放');
        length.textContent = primary && single ? t(`Length ${(sourceFrames(primary) / 24).toFixed(1)} s → ${(primary.frames / 24).toFixed(1)} s`, `时长 ${(sourceFrames(primary) / 24).toFixed(1)} 秒 → ${(primary.frames / 24).toFixed(1)} 秒`) : '';
        length.hidden = !length.textContent;
        for (const button of [follows, mute]) {
            button.disabled = disabled || !single || !sounds.length;
            button.setAttribute('aria-pressed', String(!!sounds.length && sounds.every(clip => (clip.speed_audio ?? 'follow') === button.dataset.speedAudio)));
            button.title = button.textContent;
        }
        if (context) {
            const reason = disabledReason(context) || (locked ? 'locked' : '');
            for (const control of [input, ...buttons, reverse, follows, mute, reset]) if (reason) control.disabled = true;
        }
        // Back to normal speed only: reverse and the sound mode stay as they are.
        reset.hidden = !clips.some(clip => percent(clip) !== 100); reset.disabled = disabled;
        // Backward sound is not played in the preview: say it where Reverse is, while it is on and the part has sound that follows.
        const reversedSound = single && !!primary?.reverse && sounds.some(clip => (clip.speed_audio ?? 'follow') !== 'mute');
        reverseHint.hidden = !reversedSound;

    }
    function apply(options) {
        if (disposed) return;
        const groups = selection();
        try { state.apply(t('Speed', '速度'), record => groups.reduce((next, clips) => setSpeed(next, clips[0].id, options), record)); update(); }
        catch (error) {
            // The reason is said instead of the change silently not happening.
            say(speedErrorText(error, t) || t('This speed cannot be used here.', '这里不能用这个速度。')); update();
            return {ok: false, reason: error.code || 'speed'};
        }
    }
    reverse.onchange = () => apply({reverse: reverse.checked});
    const unsubscribe = context ? () => {} : state.subscribe(event => { if (event.type === 'record' || event.type === 'selection') update(); });
    update();
    return {element: panel, ready, refresh: update, dispose() { disposed = true; clearTimeout(noteTimer); unsubscribe(); speedNumber.dispose(); reverse.onchange = reset.onclick = null; [...buttons, follows, mute].forEach(button => { button.onclick = null; }); panel.remove(); }};
}

export function createSpeedComponent(options) {
    let panel;
    return {render(container, context) { panel = createSpeedPanel(container, {...options, context}); },
        update: context => panel?.refresh(context), dispose: () => { panel?.dispose(); panel = null; }};
}
// Native media preserves pitch but does not expose timeline samples to the envelope.
export async function readSpeedAudioSamples({clip, count}) {
    if (clip.speed_audio === 'mute') return [new Float64Array(count), new Float64Array(count)];
    throw new model.EditError('audio_prepare');
}
