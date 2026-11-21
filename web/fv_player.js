// Our own play bar for a video or audio element in place of the browser's: a play/pause key, a progress bar that can be
// dragged, and the time. Video also plays and pauses on a click. Every part is at least 44 px to hit.
// Loaded at once so the first bar is never drawn bare; a page without a document (the module checks, the page check) skips it.
if (typeof document !== 'undefined') { const sheet = document.createElement('link'); sheet.rel = 'stylesheet'; sheet.href = new URL('./fv_player.css', import.meta.url).href; document.head.append(sheet); }

const clock = seconds => {
    if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
    const whole = Math.floor(seconds), h = Math.floor(whole / 3600), m = Math.floor(whole % 3600 / 60), s = String(whole % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
};
const PLAY = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z"/></svg>';
const LOUD = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5v5h3.5l4.5 3.5v-12l-4.5 3.5zM15.5 9a4 4 0 0 1 0 6"/></svg>';
const MUTE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5v5h3.5l4.5 3.5v-12l-4.5 3.5zM15.5 9.5l5 5M20.5 9.5l-5 5"/></svg>';
const PAUSE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13M16 5.5v13"/></svg>';

export function createPlayBar(media, t = (en) => en) {
    const bar = document.createElement('div'); bar.className = 'fv-player-bar';
    const key = document.createElement('button'); key.type = 'button'; key.className = 'fv-player-key';
    const track = document.createElement('div'); track.className = 'fv-player-track'; track.tabIndex = 0; track.setAttribute('role', 'slider');
    track.setAttribute('aria-label', t('Progress', '进度'));
    const rail = document.createElement('span'); rail.className = 'fv-player-rail';
    const done = document.createElement('span'); done.className = 'fv-player-done'; rail.append(done); track.append(rail);
    const time = document.createElement('span'); time.className = 'fv-player-time';
    const sound = document.createElement('button'); sound.type = 'button'; sound.className = 'fv-player-key fv-player-sound';
    bar.append(key, track, time, sound);
    let shownPaused = null, shownMuted = null;
    let dragging = false;
    const length = () => Number.isFinite(media.duration) && media.duration > 0 ? media.duration : 0;
    const paint = () => {
        const total = length(), at = media.currentTime || 0, share = total ? Math.min(1, at / total) : 0;
        if (shownPaused !== media.paused) {
            shownPaused = media.paused; key.innerHTML = shownPaused ? PLAY : PAUSE;
            key.setAttribute('aria-label', shownPaused ? t('Play', '播放') : t('Pause', '暂停'));
        }
        const quiet = media.muted || media.volume === 0;
        if (shownMuted !== quiet) {
            shownMuted = quiet; sound.innerHTML = quiet ? MUTE : LOUD;
            sound.setAttribute('aria-label', quiet ? t('Turn sound on', '开启声音') : t('Mute', '静音')); sound.setAttribute('aria-pressed', String(quiet));
        }
        done.style.width = share * 100 + '%'; track.style.setProperty('--fv-player-at', share * 100 + '%');
        track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', String(Math.round(total)));
        track.setAttribute('aria-valuenow', String(Math.round(at))); track.setAttribute('aria-valuetext', `${clock(at)} / ${clock(total)}`);
        track.setAttribute('aria-disabled', total ? 'false' : 'true');
        time.textContent = `${clock(at)} / ${clock(total)}`;
    };
    const toggle = () => { if (media.paused) media.play().catch(() => {}); else media.pause(); };
    const seek = seconds => { const total = length(); if (!total) return; media.currentTime = Math.max(0, Math.min(total, seconds)); paint(); };
    const from = event => { const box = track.getBoundingClientRect(); return length() * Math.max(0, Math.min(1, (event.clientX - box.left) / (box.width || 1))); };
    key.onclick = toggle;
    sound.onclick = () => { if (media.muted || media.volume === 0) { media.muted = false; if (media.volume === 0) media.volume = 1; } else media.muted = true; paint(); };
    track.onpointerdown = event => { if (!length()) return; dragging = true; track.setPointerCapture?.(event.pointerId); seek(from(event)); event.preventDefault(); };
    track.onpointermove = event => { if (dragging) seek(from(event)); };
    track.onpointerup = track.onpointercancel = () => { dragging = false; };
    track.onkeydown = event => {
        const step = event.shiftKey ? 10 : 5, total = length();
        const to = {ArrowLeft: media.currentTime - step, ArrowDown: media.currentTime - step, ArrowRight: media.currentTime + step, ArrowUp: media.currentTime + step, Home: 0, End: total}[event.key];
        if (to === undefined || !total) return; seek(to); event.preventDefault(); event.stopPropagation();
    };
    const events = ['play', 'pause', 'timeupdate', 'loadedmetadata', 'durationchange', 'ended', 'seeked', 'emptied', 'volumechange'];
    events.forEach(name => media.addEventListener(name, paint)); paint();
    const click = () => toggle();
    if (media.tagName === 'VIDEO') { media.addEventListener('click', click); media.classList.add('fv-player-video'); }
    return {bar, destroy() { events.forEach(name => media.removeEventListener(name, paint)); media.removeEventListener('click', click); bar.remove(); }};
}
