import { api } from '../../scripts/api.js';

const releasePage = 'https://github.com/FlashML-org/FreeVideo/releases/tag/windows-preview';
const storageKey = 'freevideo.dismissed-update';
const listeners = new Set();
let candidate, dismissed, started = false, pending = false, lastCheck = -Infinity;
try { dismissed = sessionStorage.getItem(storageKey); } catch { /* Session memory still works. */ }
const identity = value => value ? `${value.revision}:${value.built_at}` : '';
const publish = () => { for (const render of listeners) render(); };

export async function checkUpdates() {
    if (pending || Date.now() - lastCheck < 1500 || document.hidden) return;
    pending = true; lastCheck = Date.now();
    try {
        const response = await api.fetchApi('/freevideo/updates', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
        if (!response.ok) return;
        const value = await response.json();
        candidate = value.available || null;
        publish();
        // The server returns immediately while its initial network check runs.
        if (value.status === 'checking') setTimeout(checkUpdates, 2000);
    } catch { /* Offline checks do not affect generation; the next poll retries. */ }
    finally { pending = false; }
}

export function startUpdateChecks() {
    if (started) return;
    started = true;
    const css = document.createElement('link'); css.rel = 'stylesheet';
    css.href = new URL('./updates.css', import.meta.url).href; document.head.append(css);
    void checkUpdates();
    setInterval(checkUpdates, 60000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) void checkUpdates(); });
}

export function createUpdateNotice(cn) {
    const t = (en, zh) => cn ? zh : en;
    const element = document.createElement('aside'); element.className = 'fv-update-notice';
    element.hidden = true; element.setAttribute('role', 'status');
    const label = document.createElement('span');
    const link = document.createElement('a'); link.href = releasePage;
    link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.textContent = t('Download update', '下载新版');
    link.title = t('After your task finishes, open the new FreeVideo.exe to update.', '当前任务完成后，打开新版 FreeVideo.exe 更新。');
    const later = document.createElement('button'); later.type = 'button';
    later.textContent = t('Later', '稍后');
    later.onclick = () => {
        dismissed = identity(candidate);
        try { sessionStorage.setItem(storageKey, dismissed); } catch {}
        publish();
    };
    element.append(label, link, later);
    const render = () => {
        element.hidden = !candidate || dismissed === identity(candidate);
        label.textContent = candidate ? t('FreeVideo update available', 'FreeVideo 有新版本') + ' · ' + candidate.version : '';
    };
    listeners.add(render); render();
    return {element, dispose: () => listeners.delete(render)};
}
