// The thin play bar FreeVideo uses under a video instead of the browser's controls: play or pause, the position (click
// or drag to seek), the time, and sound. One bar per video; the library stage and the creation panel share it.
//
//   import {attach, detach} from './glass/player_bar.js';
//   const bar = attach(video, host, {compact: false});   // appends the bar to `host`, returns {element, detach, sync}
//   bar.detach();  or  detach(video);                     // removes the bar and its listeners
//
// The video keeps its own behaviour (autoplay, loop, muted); the bar turns the browser's controls off while attached and
// gives them back on detach. `compact` leaves the time out (narrow places). Keys stay with the page (Space, M).
//
// Viewing options, each off unless asked for, in this order after the time:
//   volume: true    the sound key still mutes; its slider shows while the key is hovered or focused (a tap on a touch
//                   screen shows it instead of muting). The level is kept for the next video (VOLUME_KEY).
//   speed: true     a key that reads the rate ("1×"); its menu offers RATES. The rate is kept for the next video (RATE_KEY).
//   full: {shown, toggle}   Full screen / Exit full screen: `shown()` says whether the video is enlarged now, `toggle()`
//                   enlarges it or puts it back (video_enlarge.js).
import { t as words } from '../context_menu.js';
// Keys reach the bar inside an isolated view dialog too (dialog_keys.js routes them to listeners registered this way).
import { listenDialogKey } from '../dialog_keys.js';

const ICONS = {
    play: '<path d="M5 3.5v9l7.5-4.5Z" fill="currentColor"/>',
    pause: '<path d="M5.5 3.5v9M10.5 3.5v9" stroke-width="2"/>',
    sound: '<path d="M2.5 6h2.5l3-2.5v9L5 10H2.5ZM10.5 5.5a3.5 3.5 0 0 1 0 5M12 4a5.5 5.5 0 0 1 0 8"/>',
    muted: '<path d="M2.5 6h2.5l3-2.5v9L5 10H2.5ZM10.5 6.5l3 3M13.5 6.5l-3 3"/>',
    // The library viewer's Full screen glyph, and its arrows turned inward to leave.
    full: '<path d="M9.5 2.5h4v4M13.5 2.5 9 7M6.5 13.5h-4v-4M2.5 13.5 7 9"/>',
    leave: '<path d="M13.5 6.5h-4v-4M9.5 6.5l4-4M2.5 9.5h4v4M6.5 9.5l-4 4"/>',
};
export const RATES = Object.freeze([0.5, 1, 1.25, 1.5, 2]);
export const VOLUME_KEY = 'fv.player.volume', RATE_KEY = 'fv.player.rate';
// The kept level and rate: a value only this bar wrote, read back strictly; storage that cannot be read keeps the defaults.
const kept = (key, ok) => { try { const value = Number(localStorage.getItem(key)); return localStorage.getItem(key) !== null && ok(value) ? value : null; } catch { return null; } };
const keep = (key, value) => { try { localStorage.setItem(key, String(value)); } catch { /* only this page remembers it */ } };
export const keptVolume = () => kept(VOLUME_KEY, v => Number.isFinite(v) && v >= 0 && v <= 1);
export const keptRate = () => kept(RATE_KEY, v => RATES.includes(v));
const rateText = rate => `${rate}×`;
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

export function attach(video, host, {compact = false, t = words, volume = false, speed = false, full = null} = {}) {
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
    bar.append(play, track, time);
    // Sound, with its slider when asked for: the key and the slider in one group, so hover and focus cover both.
    let level = null, levelFill = null, levelKnob = null, soundGroup = sound;
    if (volume) {
        const remembered = keptVolume(); if (remembered !== null) video.volume = remembered;
        soundGroup = document.createElement('div'); soundGroup.className = 'fv-player-sound-group';
        level = document.createElement('div'); level.className = 'fv-player-level'; level.tabIndex = 0;
        level.setAttribute('role', 'slider'); level.setAttribute('aria-label', t('Volume', '音量'));
        level.setAttribute('aria-valuemin', '0'); level.setAttribute('aria-valuemax', '100');
        level.dataset.fvTipName = t('Volume', '音量'); level.dataset.fvTipKey = '← →';
        const levelRail = document.createElement('div'); levelRail.className = 'fv-player-rail';
        levelFill = document.createElement('div'); levelFill.className = 'fv-player-fill';
        levelKnob = document.createElement('div'); levelKnob.className = 'fv-player-knob'; levelRail.append(levelFill, levelKnob); level.append(levelRail);
        soundGroup.append(sound, level);
        const set = value => { video.volume = Math.max(0, Math.min(1, value)); if (video.volume > 0 && video.muted) video.muted = false; keep(VOLUME_KEY, video.volume); };
        const at = event => { const box = level.getBoundingClientRect(); if (box.width) set((event.clientX - box.left) / box.width); };
        level.addEventListener('pointerdown', event => { level.setPointerCapture(event.pointerId); at(event); level.addEventListener('pointermove', at); });
        const done = () => level.removeEventListener('pointermove', at);
        level.addEventListener('pointerup', done); level.addEventListener('pointercancel', done);
        listenDialogKey(level, 'keydown', event => {
            if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
            event.preventDefault(); event.stopPropagation(); set(video.volume + (event.key === 'ArrowLeft' ? -0.1 : 0.1));
        });
        // A tap on a touch screen opens the slider (it cannot be hovered); the next tap on the key mutes as with a mouse.
        sound.addEventListener('pointerdown', event => {
            if (event.pointerType !== 'touch' || soundGroup.dataset.open === 'true') return;
            soundGroup.dataset.open = 'true'; sound.dataset.skipClick = 'true';
        });
        sound.addEventListener('click', event => { if (sound.dataset.skipClick) { delete sound.dataset.skipClick; event.stopImmediatePropagation(); } }, true);
        soundGroup.addEventListener('focusout', event => { if (!soundGroup.contains(event.relatedTarget)) delete soundGroup.dataset.open; });
    }
    bar.append(soundGroup);
    let rateKey = null, menu = null;
    if (speed) {
        const remembered = keptRate(); if (remembered !== null) video.playbackRate = video.defaultPlaybackRate = remembered;
        rateKey = button('fv-player-rate', t('Speed', '倍速'), t('Play slower or faster', '放慢或加快播放'), '', () => { menu.hidden ? openMenu() : closeMenu(); });
        rateKey.setAttribute('aria-haspopup', 'menu'); rateKey.setAttribute('aria-expanded', 'false');
        menu = document.createElement('div'); menu.className = 'fv-player-menu'; menu.setAttribute('role', 'menu'); menu.setAttribute('aria-label', t('Speed', '倍速')); menu.hidden = true;
        for (const rate of RATES) {
            const item = document.createElement('button'); item.type = 'button'; item.className = 'fv-player-menu-item';
            item.setAttribute('role', 'menuitemradio'); item.dataset.rate = String(rate); item.textContent = rateText(rate);
            item.addEventListener('click', () => { video.playbackRate = video.defaultPlaybackRate = rate; keep(RATE_KEY, rate); closeMenu(true); });
            menu.append(item);
        }
        const items = () => [...menu.children];
        const openMenu = () => { menu.hidden = false; rateKey.setAttribute('aria-expanded', 'true'); (items().find(i => i.getAttribute('aria-checked') === 'true') || items()[1]).focus(); };
        const closeMenu = refocus => { menu.hidden = true; rateKey.setAttribute('aria-expanded', 'false'); if (refocus) rateKey.focus(); };
        listenDialogKey(menu, 'keydown', event => {
            const list = items(), index = list.indexOf(document.activeElement);
            if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeMenu(true); }
            else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); list[(index + (event.key === 'ArrowDown' ? 1 : list.length - 1)) % list.length].focus(); }
        });
        menu.addEventListener('focusout', event => { if (!menu.contains(event.relatedTarget) && event.relatedTarget !== rateKey) closeMenu(false); });
        const group = document.createElement('div'); group.className = 'fv-player-rate-group'; group.append(rateKey, menu);
        bar.append(group);
    }
    let fullKey = null;
    if (full) {
        fullKey = button('fv-player-full', '', '', '', () => full.toggle());
        bar.append(fullKey);
    }

    let frame = 0, shownTime = '', shownPercent = '', shownPlay = '', shownSound = '';
    // Runs every frame while the video plays, but writes only what changed: the fill moves once it has moved two pixels
    // of the rail and at least 50 ms have passed (at most 20 a second; every write restyles and repaints the view around
    // it, ~50 a second when written per frame), and at once when the video is paused, seeks or loops back. The time and
    // the percentage are written when their text changes (once a second).
    let railWidth = 0, shownAt = -1, shownStamp = 0;
    if (typeof ResizeObserver === 'function') new ResizeObserver(() => { railWidth = rail.clientWidth; shownAt = -1; }).observe(rail);
    const draw = () => {
        const now = video.currentTime, stamp = performance.now();
        const at = video.duration ? now / video.duration : 0;
        if (!railWidth) railWidth = rail.clientWidth;
        const moved = Math.abs(now - shownAt) / (video.duration || 1) * railWidth;
        if (video.paused || now < shownAt || shownAt < 0 || (moved >= 2 && stamp - shownStamp >= 50)) { shownAt = now; shownStamp = stamp; fill.style.transform = `scaleX(${at})`; }
        const percent = String(Math.round(at * 100));
        if (percent !== shownPercent) { shownPercent = percent; track.setAttribute('aria-valuenow', percent); }
        const clockText = `${clock(video.currentTime)} / ${clock(video.duration)}`;
        if (!compact && clockText !== shownTime) { shownTime = clockText; time.textContent = clockText; }
        // One loop only: a seek, a loop restart or a key press draws again while the loop is still pending.
        cancelAnimationFrame(frame);
        if (!video.paused && video.isConnected) frame = requestAnimationFrame(draw);
    };
    let shownPlayName = '', shownSoundName = '', shownRate = '', shownFull = null, shownLevel = '';
    const sync = () => {
        const playGlyph = video.paused ? 'play' : 'pause', soundGlyph = video.muted ? 'muted' : 'sound';
        if (playGlyph !== shownPlay) { shownPlay = playGlyph; play.replaceChildren(glyph(playGlyph)); }
        const name = video.paused ? t('Play', '播放') : t('Pause', '暂停');
        if (name !== shownPlayName) { shownPlayName = name; play.setAttribute('aria-label', name); play.dataset.fvTipName = name; }
        if (soundGlyph !== shownSound) { shownSound = soundGlyph; sound.replaceChildren(glyph(soundGlyph)); }
        const what = video.muted ? t('Sound on', '打开声音') : t('Sound off', '关闭声音');
        if (what !== shownSoundName) { shownSoundName = what; sound.setAttribute('aria-label', what); sound.dataset.fvTipName = what; }
        if (level) {
            const value = video.muted ? 0 : video.volume, percent = String(Math.round(value * 100));
            if (percent !== shownLevel) { shownLevel = percent; levelFill.style.transform = `scaleX(${value})`; levelKnob.style.left = `${value * 100}%`; level.setAttribute('aria-valuenow', percent); level.setAttribute('aria-valuetext', `${percent}%`); }
        }
        if (rateKey) {
            const rate = rateText(video.playbackRate);
            if (rate !== shownRate) {
                shownRate = rate; rateKey.textContent = rate;
                for (const item of menu.children) item.setAttribute('aria-checked', String(Number(item.dataset.rate) === video.playbackRate));
            }
        }
        if (fullKey) {
            const big = Boolean(full.shown());
            if (big !== shownFull) {
                shownFull = big; fullKey.replaceChildren(glyph(big ? 'leave' : 'full'));
                const name = big ? t('Exit full screen', '退出全屏') : t('Full screen', '全屏');
                fullKey.setAttribute('aria-label', name); fullKey.dataset.fvTipName = name; fullKey.dataset.fvTipKey = big ? 'Esc' : '';
                fullKey.dataset.fvTipUse = big ? '' : t('Fill the window; press Esc to leave', '铺满窗口播放；按 Esc 退出');
            }
        }
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
    track.addEventListener('pointerdown', down); track.addEventListener('pointerup', up); track.addEventListener('pointercancel', up); listenDialogKey(track, 'keydown', key);
    // The frame loop starts on play; playing (after a stall) and timeupdate (a few times a second while it plays) draw too
    // and start the loop again, so the bar follows the video even where frames are held back (a hidden or covered window).
    video.addEventListener('play', playing); video.addEventListener('playing', playing); video.addEventListener('timeupdate', draw);
    for (const name of ['pause', 'volumechange', 'loadedmetadata', 'seeked', 'durationchange', 'ratechange']) video.addEventListener(name, changed);
    // A new source resets the rate to the default rate, which the speed key keeps equal to the chosen one.
    sync(); draw();
    host.append(bar);
    const handle = {
        element: bar, sync: changed,
        detach() {
            cancelAnimationFrame(frame);
            video.removeEventListener('play', playing); video.removeEventListener('playing', playing); video.removeEventListener('timeupdate', draw);
            for (const name of ['pause', 'volumechange', 'loadedmetadata', 'seeked', 'durationchange', 'ratechange']) video.removeEventListener(name, changed);
            bar.remove(); if (hadControls) video.controls = true;
            if (bars.get(video) === handle) bars.delete(video);
        },
    };
    bars.set(video, handle);
    return handle;
}

export function detach(video) { bars.get(video)?.detach(); }
