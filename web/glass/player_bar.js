// The thin play bar FreeVideo uses under a video instead of the browser's controls: play or pause, the position (click
// or drag to seek), the time, and sound. One bar per video; the library stage and the creation panel share it.
//
//   import {attach, detach} from './glass/player_bar.js';
//   const bar = attach(video, host, {compact: false});   // appends the bar to `host`, returns {element, detach, sync}
//   bar.detach();  or  detach(video);                     // removes the bar and its listeners
//
// The video keeps its own behaviour (autoplay, loop, muted); the bar turns the browser's controls off while attached and
// gives them back on detach. `compact` leaves the time out (narrow places). Keys stay with the page (Space, M).
import { t as words } from '../context_menu.js';

const ICONS = {
    play: '<path d="M5 3.5v9l7.5-4.5Z" fill="currentColor"/>',
    pause: '<path d="M5.5 3.5v9M10.5 3.5v9" stroke-width="2"/>',
    sound: '<path d="M2.5 6h2.5l3-2.5v9L5 10H2.5ZM10.5 5.5a3.5 3.5 0 0 1 0 5M12 4a5.5 5.5 0 0 1 0 8"/>',
    muted: '<path d="M2.5 6h2.5l3-2.5v9L5 10H2.5ZM10.5 6.5l3 3M13.5 6.5l-3 3"/>',
};
const bars = new WeakMap();
let styled = false;
function style() {
    if (styled || typeof document === 'undefined') return;
    styled = true;
    const href = new URL('./player_bar.css', import.meta.url).href;
    if (!document.querySelector(`link[href="${href}"]`)) { const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = href; document.head.append(link); }
}
const glyph = name => {
    const span = document.createElement('span'); span.className = 'fv-player-icon'; span.setAttribute('aria-hidden', 'true');
    span.innerHTML = `<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;
    return span;
};
const clock = value => { const total = Math.max(0, Math.floor(Number(value) || 0)); return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`; };

export function attach(video, host, {compact = false, t = words} = {}) {
    if (!(video instanceof HTMLVideoElement)) throw new TypeError('attach needs a video element');
    bars.get(video)?.detach();
    style();
    const hadControls = video.controls;
    video.controls = false; video.removeAttribute('controls');
    const bar = document.createElement('div'); bar.className = 'fv-player-bar'; if (compact) bar.dataset.compact = 'true';
    const button = (cls, name, use, key, run) => {
        const node = document.createElement('button'); node.type = 'button'; node.className = 'fv-player-button ' + cls;
        node.setAttribute('aria-label', name); node.dataset.fvTipName = name; node.dataset.fvTipUse = use; if (key) node.dataset.fvTipKey = key;
        node.addEventListener('click', run); return node;
    };
    const play = button('fv-player-play', t('Play', '播放'), t('Play or pause', '播放或暂停'), 'Space', () => { if (video.paused) video.play().catch(() => {}); else video.pause(); });
    const track = document.createElement('div'); track.className = 'fv-player-track';
    track.setAttribute('role', 'slider'); track.setAttribute('aria-label', t('Position', '播放位置')); track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100'); track.tabIndex = 0;
    track.dataset.fvTipName = t('Position', '播放位置'); track.dataset.fvTipUse = t('Click or drag to go to a moment', '点按或拖动，跳到某一刻'); track.dataset.fvTipKey = '← →';
    const rail = document.createElement('div'); rail.className = 'fv-player-rail';
    const fill = document.createElement('div'); fill.className = 'fv-player-fill'; rail.append(fill); track.append(rail);
    const time = document.createElement('span'); time.className = 'fv-player-time'; time.textContent = '0:00 / 0:00'; time.hidden = compact;
    const sound = button('fv-player-sound', t('Sound', '声音'), t('Turn the sound on or off', '打开或关闭声音'), 'M', () => { video.muted = !video.muted; });
    bar.append(play, track, time, sound);

    let frame = 0, shownTime = '', shownPercent = '', shownPlay = '', shownSound = '';
    // Runs every frame while the video plays. Only the fill moves each frame; the time and the percentage are written
    // when they change (once a second), because each new text node makes the page restyle the whole view around it.
    const draw = () => {
        const at = video.duration ? video.currentTime / video.duration : 0;
        fill.style.transform = `scaleX(${at})`;
        const percent = String(Math.round(at * 100));
        if (percent !== shownPercent) { shownPercent = percent; track.setAttribute('aria-valuenow', percent); }
        const clockText = `${clock(video.currentTime)} / ${clock(video.duration)}`;
        if (!compact && clockText !== shownTime) { shownTime = clockText; time.textContent = clockText; }
        // One loop only: a seek, a loop restart or a key press draws again while the loop is still pending.
        cancelAnimationFrame(frame);
        if (!video.paused && video.isConnected) frame = requestAnimationFrame(draw);
    };
    const sync = () => {
        const playGlyph = video.paused ? 'play' : 'pause', soundGlyph = video.muted ? 'muted' : 'sound';
        if (playGlyph !== shownPlay) { shownPlay = playGlyph; play.replaceChildren(glyph(playGlyph)); }
        const name = video.paused ? t('Play', '播放') : t('Pause', '暂停'); play.setAttribute('aria-label', name); play.dataset.fvTipName = name;
        if (soundGlyph !== shownSound) { shownSound = soundGlyph; sound.replaceChildren(glyph(soundGlyph)); }
        const what = video.muted ? t('Sound on', '打开声音') : t('Sound off', '关闭声音'); sound.setAttribute('aria-label', what); sound.dataset.fvTipName = what;
    };
    const seek = event => { const box = track.getBoundingClientRect(); if (video.duration && box.width) video.currentTime = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)) * video.duration; draw(); };
    const down = event => { track.setPointerCapture(event.pointerId); seek(event); track.addEventListener('pointermove', seek); };
    const up = () => track.removeEventListener('pointermove', seek);
    const key = event => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault(); event.stopPropagation();
        if (video.duration) video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 5 : 1)));
        draw();
    };
    const playing = () => { sync(); cancelAnimationFrame(frame); frame = requestAnimationFrame(draw); };
    const changed = () => { sync(); draw(); };
    track.addEventListener('pointerdown', down); track.addEventListener('pointerup', up); track.addEventListener('pointercancel', up); track.addEventListener('keydown', key);
    video.addEventListener('play', playing);
    for (const name of ['pause', 'volumechange', 'loadedmetadata', 'seeked', 'durationchange']) video.addEventListener(name, changed);
    sync(); draw();
    host.append(bar);
    const handle = {
        element: bar, sync: changed,
        detach() {
            cancelAnimationFrame(frame);
            video.removeEventListener('play', playing);
            for (const name of ['pause', 'volumechange', 'loadedmetadata', 'seeked', 'durationchange']) video.removeEventListener(name, changed);
            bar.remove(); if (hadControls) video.controls = true;
            if (bars.get(video) === handle) bars.delete(video);
        },
    };
    bars.set(video, handle);
    return handle;
}

export function detach(video) { bars.get(video)?.detach(); }
