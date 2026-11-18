// Whether this computer's figures show in the status (Settings › Appearance). The top bar reads the same key
// (topbar_state.js FIGURES_KEY) and draws the figures in its own status capsule; the old header capsule that this
// module also built is gone since the redesign, and with it its second reading of /freevideo/monitor.
const shownKey = 'freevideo.monitor.shown', changed = 'freevideo-monitor-shown';
export function monitorShown() { try { return localStorage.getItem(shownKey) === '1'; } catch { return false; } }
export function setMonitorShown(value) {
    try { localStorage.setItem(shownKey, value ? '1' : '0'); } catch {}
    window.dispatchEvent(new Event(changed));
}
export function onMonitorShown(listener) {
    const storage = event => { if (event.key === shownKey) listener(); };
    window.addEventListener(changed, listener); window.addEventListener('storage', storage);
    return () => { window.removeEventListener(changed, listener); window.removeEventListener('storage', storage); };
}
