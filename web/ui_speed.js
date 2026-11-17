// Notices once when the interface itself runs slowly on this computer and points to the compatibility view
// (Settings → 兼容界面). It never switches anything by itself.
//
// Frames are counted only while something should move: for two seconds after a pointer move, wheel or key, and while a
// video plays. Nothing runs between those moments. A window counts when the page was in front for all of it; the notice
// shows when three windows in a row (at least 20 s after the page opened) ran under 20 frames a second.
import { t, compatView } from './context_menu.js';

export const SLOW_FPS = 20;
export const WINDOWS = 3;
const WINDOW_MS = 2000, SETTLE_MS = 20000, SEEN = 'freevideo.compat.hinted';

// [{frames, ms}] → whether the last WINDOWS windows were all slow. Short windows (under a second) say nothing.
export function slowVerdict(windows, {fps = SLOW_FPS, count = WINDOWS} = {}) {
    const usable = windows.filter(w => w && w.ms >= 1000);
    if (usable.length < count) return false;
    return usable.slice(-count).every(w => w.frames * 1000 / w.ms < fps);
}

const seen = () => { try { return localStorage.getItem(SEEN) === '1'; } catch { return false; } };
const remember = () => { try { localStorage.setItem(SEEN, '1'); } catch { /* Asked again next time the page opens. */ } };

export function watchSpeed({now = () => performance.now(), notify, settle = SETTLE_MS} = {}) {
    if (typeof document === 'undefined' || seen() || compatView()) return () => {};
    const opened = now(), windows = [];
    let measuring = false, done = false, frame = 0, wake = 0, away = false;
    const show = notify || (async () => {
        const [{toast}, {openSetup}] = await Promise.all([import('./notifications.js'), import('./setup.js')]);
        toast(t('Interface is slow. Try Compatibility view.', '界面较卡，可在设置中开启兼容界面'), {tone: 'caution',
            action: {label: t('Open settings', '打开设置'), run: () => openSetup({section: 'appearance'})}});
    });
    const measure = () => {
        if (done || measuring || document.visibilityState !== 'visible' || now() - opened < settle) return;
        measuring = true;
        const began = now(); let frames = 0; away = false;
        const tick = () => {
            frames += 1;
            if (now() - began < WINDOW_MS) { frame = requestAnimationFrame(tick); return; }
            measuring = false;
            if (away || done) return; // Hidden for part of it: the browser held the frames back.
            windows.push({frames, ms: now() - began});
            if (windows.length > WINDOWS) windows.shift();
            if (compatView()) { stop(); return; }
            if (slowVerdict(windows)) { remember(); stop(); Promise.resolve().then(show).catch(() => {}); }
        };
        frame = requestAnimationFrame(tick);
    };
    const poke = () => { clearTimeout(wake); wake = setTimeout(measure, 0); };
    const playing = event => { if (event.target instanceof HTMLVideoElement) poke(); };
    const left = () => { if (document.visibilityState !== 'visible') away = true; };
    const options = {passive: true, capture: true};
    for (const name of ['pointermove', 'wheel', 'keydown']) window.addEventListener(name, poke, options);
    document.addEventListener('playing', playing, true);
    document.addEventListener('timeupdate', playing, true);
    document.addEventListener('visibilitychange', left);
    function stop() {
        done = true; cancelAnimationFrame(frame); clearTimeout(wake);
        for (const name of ['pointermove', 'wheel', 'keydown']) window.removeEventListener(name, poke, options);
        document.removeEventListener('playing', playing, true);
        document.removeEventListener('timeupdate', playing, true);
        document.removeEventListener('visibilitychange', left);
    }
    return stop;
}
