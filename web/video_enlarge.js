// A video enlarged to fill the window, and back.
//
//   import {enlarge, enlarged} from './video_enlarge.js';
//   enlarge(video, {t, opener});   // the video, with the shared play bar, over everything; Esc or the key puts it back
//
// The video element itself moves into a modal dialog (so it sits above any open view, which is a dialog too) and asks
// the browser for full screen; where full screen is refused, the dialog still fills the window. One Esc leaves both.
// Leaving puts the same element back where it was, so the moment, playing or paused, sound and speed all stay as they
// were (an element moved within one task does not pause), and focus returns to `opener`.
import { attach } from './glass/player_bar.js';

let current = null;
export const enlarged = video => current?.video === video;

function styleOnce() {
    if (typeof document === 'undefined' || document.querySelector('link[data-fv-video-enlarge]')) return;
    const link = document.createElement('link'); link.rel = 'stylesheet'; link.dataset.fvVideoEnlarge = 'true';
    link.href = new URL('./video_enlarge.css', import.meta.url).href; document.head.append(link);
}

export function enlarge(video, {t, opener = null} = {}) {
    if (!(video instanceof HTMLVideoElement) || !video.isConnected) return null;
    if (current) return current.video === video ? current : (current.leave(), enlarge(video, {t, opener}));
    styleOnce();
    const place = document.createComment('fv-enlarged-video'), playing = !video.paused && !video.ended;
    video.replaceWith(place);
    const dialog = document.createElement('dialog'); dialog.className = 'fv-video-full';
    dialog.setAttribute('aria-label', t('Full screen', '全屏'));
    const stage = document.createElement('div'); stage.className = 'fv-video-full-stage';
    const controls = document.createElement('div'); controls.className = 'fv-video-full-bar';
    stage.append(video); dialog.append(stage, controls);
    document.body.append(dialog);
    dialog.showModal();
    // The same element, in the same task: it keeps playing.
    if (playing && video.paused) video.play().catch(() => {});
    let left = false;
    const handle = {video, dialog, leave};
    const bar = attach(video, controls, {t, volume: true, speed: true, full: {shown: () => !left, toggle: () => leave()}});
    bar.element.classList.add('fv-player-on-dark');
    const onCancel = event => { event.preventDefault(); leave(); };
    const onFull = () => { if (!document.fullscreenElement && !left && dialog.dataset.full === 'true') leave(); };
    const onDouble = () => leave();
    dialog.addEventListener('cancel', onCancel);
    document.addEventListener('fullscreenchange', onFull);
    video.addEventListener('dblclick', onDouble);
    (async () => {
        try { await dialog.requestFullscreen({navigationUI: 'hide'}); if (!left) dialog.dataset.full = 'true'; }
        catch { /* the dialog already fills the window */ }
    })();
    bar.element.querySelector('.fv-player-full')?.focus();
    function leave() {
        if (left) return;
        left = true;
        const keep = !video.paused && !video.ended;
        dialog.removeEventListener('cancel', onCancel);
        document.removeEventListener('fullscreenchange', onFull);
        video.removeEventListener('dblclick', onDouble);
        bar.detach();
        if (document.fullscreenElement === dialog) document.exitFullscreen().catch(() => {});
        if (place.isConnected) place.replaceWith(video); else video.pause();
        if (keep && video.paused && video.isConnected) video.play().catch(() => {});
        dialog.close(); dialog.remove();
        if (current === handle) current = null;
        if (opener?.isConnected) opener.focus({preventScroll: true});
        video.dispatchEvent(new Event('fv-enlarge-change'));
    }
    current = handle;
    video.dispatchEvent(new Event('fv-enlarge-change'));
    return handle;
}
