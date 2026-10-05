import { api } from '../../scripts/api.js';

const releasePage = 'https://github.com/FlashML-org/FreeVideo/releases/tag/windows-preview';
const macReleasePage = 'https://github.com/FlashML-org/FreeVideo/releases/tag/macos-preview';
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
const identity = candidate => candidate ? [candidate.version, candidate.revision, candidate.built_at].join(':') : '';
const displayVersion = release => release?.product_version ? 'v' + release.product_version : release?.version || '—';
const localizedNotes = (release, cn) => release?.release_notes?.[cn ? 'zh' : 'en'];
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
    const summary = document.createElement('small'); summary.className = 'fv-update-summary';
    const details = document.createElement('button'); details.type = 'button';
    details.textContent = t("What's new", '更新内容');
    let closeNotes = () => {};
    details.onclick = () => { closeNotes(); closeNotes = showReleaseNotes(cn); };
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
    element.append(label, details, apply, link, dismiss, stop, summary);
    const render = () => {
        const launcher = value?.launcher, phase = launcher?.phase || '', candidate = value?.available;
        const mac = value?.channel === 'macos-preview';
        link.href = mac ? macReleasePage : releasePage;
        link.title = mac
            ? t('After your task finishes, open the new FreeVideo.app to update.', '当前任务完成后，打开新版 FreeVideo.app 更新。')
            : t('After your task finishes, open the new FreeVideo.exe to update.', '当前任务完成后，打开新版 FreeVideo.exe 更新。');
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
            text = t('FreeVideo update available', 'FreeVideo 有新版本') + ' · ' + displayVersion(candidate);
            if (launcher?.status === 'error' && launcher.error) text += ' · ' + t('last attempt failed', '上次更新未完成');
            actions = true;
        }
        element.hidden = !text;
        element.classList.toggle('fv-update-working', busy);
        label.textContent = text;
        summary.textContent = actions ? localizedNotes(candidate, cn)?.summary || '' : '';
        summary.hidden = !summary.textContent;
        details.hidden = !actions;
        const direct = !!launcher && !launcher.manual && !failure;
        apply.hidden = !actions || !direct;
        link.hidden = !actions || direct;
        dismiss.hidden = !actions;
        stop.hidden = !(busy && phase === 'waiting' && !offline);
    };
    listeners.add(render); render();
    return {element, dispose: () => { listeners.delete(render); closeNotes(); }};
}

function showReleaseNotes(cn) {
    const t = (en, zh) => cn ? zh : en;
    const dialog = document.createElement('dialog'); dialog.className = 'fv-release-notes';
    const title = document.createElement('h2'); title.textContent = t('Version & release notes', '版本与更新说明');
    dialog.setAttribute('aria-label', title.textContent);
    const body = document.createElement('div'); body.className = 'fv-release-body';
    const close = document.createElement('button'); close.type = 'button'; close.autofocus = true;
    close.textContent = t('Close', '关闭'); close.onclick = () => dialog.close();
    dialog.append(title, body, close);
    const render = () => {
        body.replaceChildren();
        for (const [label, release] of [[t('Available update', '可用更新'), value?.available],
            [t('Current version', '当前版本'), value?.current_release || (value?.current_version ? {version: value.current_version} : null)]]) {
            if (!release) continue;
            const section = document.createElement('section'), heading = document.createElement('h3');
            heading.textContent = label + ' · ' + displayVersion(release); section.append(heading);
            const build = document.createElement('p'); build.className = 'fv-release-build';
            build.textContent = release.development ? t('Development version', '开发版本') : t('Build: ', '构建号：') + (release.version || '—');
            section.append(build);
            const notes = localizedNotes(release, cn), summary = document.createElement('p');
            summary.textContent = notes?.summary || t('No release notes were included with this version.', '此版本未附带更新说明。'); section.append(summary);
            const list = document.createElement('ul');
            for (const item of notes?.changes || []) {
                const row = document.createElement('li'); row.textContent = item; list.append(row);
            }
            section.append(list); body.append(section);
        }
        if (!body.childElementCount) body.textContent = t('Version information is not available yet.', '暂时无法获取版本信息。');
    };
    const dispose = () => { listeners.delete(render); dialog.remove(); };
    dialog.addEventListener('close', dispose, {once: true});
    // Keep Escape local to this sheet; the creative workspace stays open.
    dialog.addEventListener('keydown', event => event.stopPropagation());
    listeners.add(render); render(); document.body.append(dialog); dialog.showModal();
    return () => { if (dialog.open) dialog.close(); dispose(); };
}

export function createVersionInfo(cn) {
    const element = document.createElement('button'); element.type = 'button'; element.className = 'fv-version-info';
    element.title = cn ? '版本与更新说明' : 'Version & release notes'; element.setAttribute('aria-label', element.title);
    let closeNotes = () => {};
    element.onclick = () => { closeNotes(); closeNotes = showReleaseNotes(cn); };
    const render = () => {
        const current = value?.current_release || (value?.current_version ? {version: value.current_version} : null);
        element.textContent = current ? displayVersion(current) : cn ? '版本' : 'Version';
    };
    listeners.add(render); render();
    return {element, dispose: () => { listeners.delete(render); closeNotes(); }};
}
