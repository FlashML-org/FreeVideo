import { api } from '../../scripts/api.js';

const releasePage = 'https://github.com/FlashML-org/FreeVideo/releases/tag/windows-preview';
const storageKey = 'freevideo.dismissed-update';
const listeners = new Set();
// Launcher phases during which the server may disappear and come back updated.
const working = new Set(['checking', 'downloading', 'waiting', 'restarting', 'engine']);
let value = null, dismissed, started = false, pending = false, lastCheck = -Infinity, timer = null;
let loadedVersion, updating = false, offline = false, failure = '';
try { dismissed = sessionStorage.getItem(storageKey); } catch { /* Session memory still works. */ }
// The launcher passes its language in the first address, which ComfyUI later
// rewrites; keep it when reloading into an updated engine.
const languageHint = (() => { try { return new URLSearchParams(location.search).get('freevideo_lang'); } catch { return null; } })();
const identity = candidate => candidate ? String(candidate.version || '') : '';
const publish = () => { for (const render of listeners) render(); };
const later = ms => { clearTimeout(timer); timer = setTimeout(checkUpdates, ms); };

export async function checkUpdates() {
    if (pending || Date.now() - lastCheck < 1500 || (document.hidden && !updating)) return;
    pending = true; lastCheck = Date.now();
    try {
        const response = await api.fetchApi('/freevideo/updates?client=1', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
        if (!response.ok) throw new Error(response.statusText);
        const next = await response.json();
        const version = next.current_version ?? null;
        if (loadedVersion === undefined) loadedVersion = version;
        else if (version !== loadedVersion) {
            // The server restarted with another engine; load its matching
            // interface. ComfyUI keeps the workflow, including the prompt.
            const url = new URL(location.href);
            if (languageHint && !url.searchParams.has('freevideo_lang')) {
                url.searchParams.set('freevideo_lang', languageHint);
                location.replace(url.href);
            } else location.reload();
            return;
        }
        value = next; offline = false;
        const phase = next.launcher?.phase || '';
        if (working.has(phase)) updating = true;
        else if (updating && phase !== 'restarting') updating = false;
        publish();
        // The server returns immediately while its initial network check runs.
        if (next.status === 'checking' || updating) later(2000);
    } catch {
        // Offline checks do not affect generation. During an update the
        // server restarts; keep asking until the updated one answers.
        offline = true;
        if (updating) { publish(); later(2000); }
    } finally { pending = false; }
}

export async function applyUpdate() {
    failure = '';
    try {
        const response = await api.fetchApi('/freevideo/updates/apply', {method: 'POST', cache: 'no-store', signal: AbortSignal.timeout(10000)});
        if (!response.ok) throw new Error(response.statusText);
        updating = true;
    } catch {
        failure = 'launcher';
    }
    publish();
    lastCheck = -Infinity;
    later(800);
}

export async function cancelUpdate() {
    try {
        const response = await api.fetchApi('/freevideo/updates/cancel', {method: 'POST', cache: 'no-store', signal: AbortSignal.timeout(10000)});
        if (response.ok) updating = false;
    } catch { /* The launcher keeps its state; the next poll shows it. */ }
    publish();
    lastCheck = -Infinity;
    later(800);
}

export function startUpdateChecks() {
    if (started) return;
    started = true;
    const css = document.createElement('link'); css.rel = 'stylesheet';
    css.href = new URL('./updates.css', import.meta.url).href; document.head.append(css);
    void checkUpdates();
    setInterval(checkUpdates, 60000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) void checkUpdates(); });
    // A reconnect after a restart answers at once instead of at the next poll.
    try { api.addEventListener('reconnected', () => { lastCheck = -Infinity; void checkUpdates(); }); } catch { /* Polling still works. */ }
}

export function createUpdateNotice(cn) {
    const t = (en, zh) => cn ? zh : en;
    const element = document.createElement('aside'); element.className = 'fv-update-notice';
    element.hidden = true; element.setAttribute('role', 'status');
    const label = document.createElement('span');
    const apply = document.createElement('button'); apply.type = 'button'; apply.className = 'fv-update-apply';
    apply.textContent = t('Update now', '立即更新');
    apply.title = t('FreeVideo restarts once; running videos finish first.', 'FreeVideo 会重启一次，正在生成的视频会先完成。');
    apply.onclick = () => { apply.disabled = true; void applyUpdate().finally(() => { apply.disabled = false; }); };
    const link = document.createElement('a'); link.href = releasePage;
    link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.textContent = t('Download update', '下载新版');
    link.title = t('After your task finishes, open the new FreeVideo.exe to update.', '当前任务完成后，打开新版 FreeVideo.exe 更新。');
    const dismiss = document.createElement('button'); dismiss.type = 'button';
    dismiss.textContent = t('Later', '稍后');
    dismiss.onclick = () => {
        dismissed = identity(value?.available);
        failure = '';
        try { sessionStorage.setItem(storageKey, dismissed); } catch {}
        publish();
    };
    const stop = document.createElement('button'); stop.type = 'button';
    stop.textContent = t('Cancel update', '取消更新');
    stop.onclick = () => { stop.disabled = true; void cancelUpdate().finally(() => { stop.disabled = false; }); };
    element.append(label, apply, link, dismiss, stop);
    const render = () => {
        const launcher = value?.launcher, phase = launcher?.phase || '', candidate = value?.available;
        const progress = launcher?.progress, percent = progress?.total ? ' ' + Math.floor(100 * progress.done / progress.total) + '%' : '';
        const busy = updating && (working.has(phase) || offline);
        let text = '', actions = false;
        if (busy) {
            text = offline || phase === 'restarting' || phase === 'engine'
                ? t('Restarting FreeVideo to finish the update… this page refreshes by itself.', '正在重启 FreeVideo 完成更新…页面会自动刷新。')
                : phase === 'downloading' ? t('Downloading the update', '正在下载更新') + percent
                : phase === 'waiting' ? t('The update installs as soon as the current video finishes.', '当前视频生成完成后会自动更新。')
                : t('Checking for updates…', '正在检查更新…');
        } else if (phase === 'review') {
            text = t('Confirm the update in the FreeVideo launcher.', '请在 FreeVideo 启动器中确认本次更新。');
        } else if (failure) {
            text = t('Open the FreeVideo launcher to update, or download the new version.', '请在 FreeVideo 启动器中更新，或下载新版。');
            actions = true;
        } else if (candidate && dismissed !== identity(candidate)) {
            text = t('FreeVideo update available', 'FreeVideo 有新版本') + ' · ' + candidate.version;
            if (launcher?.status === 'error' && launcher.error) text += ' · ' + t('last attempt failed', '上次更新未完成');
            actions = true;
        }
        element.hidden = !text;
        element.classList.toggle('fv-update-working', busy);
        label.textContent = text;
        const direct = !!launcher && !launcher.manual && !failure;
        apply.hidden = !actions || !direct;
        link.hidden = !actions || direct;
        dismiss.hidden = !actions;
        stop.hidden = !(busy && phase === 'waiting' && !offline);
    };
    listeners.add(render); render();
    return {element, dispose: () => listeners.delete(render)};
}
