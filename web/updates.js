import { api } from '../../scripts/api.js';
import { setLauncherPresent, setOfflineInstall } from './repair_hint.js';
import { issueButton } from './issue_button.js';
import { userMessage } from './user_message.js';
import './glass/library.js';
import { hasWhatsNew } from './whatsnew_pages.js';
import { releaseMedia, mediaKind, mediaPath, splitChange } from './release_media.js';
import { LANGUAGE_PARAM, askedLanguage } from './i18n.js';

// Every platform downloads from one release: the latest vX.Y.Z.
const releasePage = 'https://github.com/FlashML-org/FreeVideo/releases/latest';
const storageKey = 'freevideo.dismissed-update';
const CANCEL_FAILURE = {en: 'Cancellation failed. Please use the launcher.', zh: '取消没成功，请在启动器里操作'};
const listeners = new Set();
// Launcher phases during which the server may disappear and come back updated.
const working = new Set(['checking', 'downloading', 'waiting', 'restarting', 'engine']);
let value = null, dismissed, started = false, pending = false, retrying = false, lastCheck = -Infinity, timer = null;
let loadedVersion, updating = false, offline = false, failure = '';
try { dismissed = sessionStorage.getItem(storageKey); } catch { /* Session memory still works. */ }
// The launcher passes its language in the first address, which ComfyUI later
// rewrites; keep it when reloading into an updated engine.
const languageHint = askedLanguage();
const identity = candidate => candidate ? [candidate.version, candidate.revision, candidate.built_at].join(':') : '';
const displayVersion = release => release?.product_version ? 'v' + release.product_version : release?.version || '—';
// The running release's product version ("0.3.4"), once the first check has answered.
export const productVersion = () => value?.current_release?.product_version || null;
const localizedNotes = (release, cn) => release?.release_notes?.[cn ? 'zh' : 'en'];
const versionParts = value => String(value || '').split('.').map(Number);
const newerThan = (a, b) => {
    const [x, y] = [versionParts(a), versionParts(b)];
    for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i];
    return false;
};
// Versions between the installed one and the update, newest first: an
// update that skips versions still shows what each of them changed.
export const earlierNotes = (release, installed) => installed
    ? (release?.release_history || []).filter(row => newerThan(row.product_version, installed)) : [];
const publish = () => { for (const render of listeners) render(); };
const later = ms => { clearTimeout(timer); timer = setTimeout(checkUpdates, ms); };

// The picture or video above a version's notes: release.media = {url, poster, sha256} from the update metadata. The
// engine keeps a verified copy (GET /freevideo/updates/media); the page shows the cover first, and the cover only
// when the file is slow, offline or does not match. Without media nothing is drawn.
const MEDIA_WAIT_MS = 8000;
const mediaAddress = (which, part, media) => api.apiURL(mediaPath(which, part, media));
const reducedMotion = () => { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } };
const SOUND_OFF = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4z"/><path d="m23 9-6 6M17 9l6 6"/></svg>';
const SOUND_ON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/></svg>';

// One 16:9 block: the cover at once, the video or moving picture over it once it plays. A video plays muted and loops;
// a click on it or on the speaker turns the sound on or off. With reduced motion nothing moves until it is clicked.
export function createReleaseMedia(release, which, cn) {
    const media = releaseMedia(release);
    if (!media) return null;
    const t = (en, zh) => cn ? zh : en, kind = mediaKind(media);
    const element = document.createElement('figure'); element.className = 'fv-release-media';
    element.dataset.kind = kind; element.dataset.state = 'cover';
    const cover = document.createElement('img'); cover.className = 'fv-release-cover'; cover.alt = '';
    cover.decoding = 'async'; cover.draggable = false;
    element.append(cover);
    let motion = null, sound = null, timer = 0, coverFailed = false, closed = false, seen = true;
    const give = () => {
        // The file did not come in time or cannot be shown: the cover stays, or nothing when it has none either.
        clearTimeout(timer);
        if (motion) { motion.removeAttribute('src'); if (kind === 'video') try { motion.load(); } catch {} motion.remove(); motion = null; }
        sound?.remove(); sound = null;
        element.classList.remove('is-clickable'); element.onclick = null;
        element.dataset.state = 'cover';
        if (coverFailed) element.hidden = true;
    };
    // Without its cover the block waits hidden for the file itself; shown() brings it back.
    cover.onerror = () => { coverFailed = true; if (element.dataset.state !== 'media') element.hidden = true; };
    cover.src = mediaAddress(which, 'poster', media);
    const shown = () => { clearTimeout(timer); element.hidden = false; element.dataset.state = 'media'; if (sound) sound.hidden = false; };
    const setSound = on => {
        if (!motion || kind !== 'video') return;
        motion.muted = !on;
        if (on) void motion.play()?.catch?.(() => {});
        if (sound) {
            sound.innerHTML = on ? SOUND_ON : SOUND_OFF; sound.setAttribute('aria-pressed', String(on));
            sound.dataset.fvTipName = on ? t('Sound on', '声音已打开') : t('Sound off', '声音已关闭');
        }
    };
    const load = ({withSound = false} = {}) => {
        if (motion || closed) return;
        if (kind === 'video') {
            motion = document.createElement('video'); motion.className = 'fv-release-motion';
            motion.muted = !withSound; motion.defaultMuted = !withSound; motion.loop = true; motion.playsInline = true;
            motion.setAttribute('playsinline', ''); motion.preload = 'auto'; motion.disablePictureInPicture = true;
            motion.addEventListener('playing', shown, {once: true});
            motion.addEventListener('error', give, {once: true});
            motion.src = mediaAddress(which, 'media', media);
            sound.before(motion); setSound(withSound);
            void motion.play()?.catch?.(() => {});
        } else {
            motion = document.createElement('img'); motion.className = 'fv-release-motion'; motion.alt = ''; motion.decoding = 'async'; motion.draggable = false;
            motion.addEventListener('load', shown, {once: true});
            motion.addEventListener('error', give, {once: true});
            motion.src = mediaAddress(which, 'media', media);
            element.append(motion);
        }
        timer = setTimeout(() => { if (element.dataset.state !== 'media') give(); }, MEDIA_WAIT_MS);
    };
    const still = reducedMotion() && kind !== 'image';
    if (kind === 'video') {
        // The speaker: shown once the video plays; with reduced motion at once, and it starts the video with sound.
        sound = document.createElement('button'); sound.type = 'button'; sound.className = 'fv-release-sound'; sound.hidden = !still;
        sound.innerHTML = SOUND_OFF; sound.setAttribute('aria-pressed', 'false');
        sound.setAttribute('aria-label', t('Sound', '声音'));
        sound.dataset.fvTipName = t('Sound off', '声音已关闭'); sound.dataset.fvTipUse = t('Turn the sound on or off', '打开或关闭声音');
        sound.onclick = event => { event.stopPropagation(); if (!motion) load({withSound: true}); else setSound(motion.muted); };
        element.append(sound);
    }
    if (kind === 'video' || still) {
        element.classList.add('is-clickable');
        element.onclick = () => { if (!motion) load({withSound: kind === 'video'}); else setSound(motion.muted); };
    }
    if (!still) load();
    // Off screen or in a hidden tab the video waits, so it costs nothing there.
    const playing = () => motion && kind === 'video' && element.dataset.state === 'media';
    const visibility = () => { if (!playing()) return; if (document.hidden || !seen) motion.pause(); else void motion.play()?.catch?.(() => {}); };
    document.addEventListener('visibilitychange', visibility);
    let observer = null;
    try { observer = new IntersectionObserver(rows => { seen = rows.at(-1).isIntersecting; visibility(); }); observer.observe(element); } catch {}
    return {element, media, which, dispose() {
        closed = true; clearTimeout(timer); observer?.disconnect(); document.removeEventListener('visibilitychange', visibility);
        if (motion) { motion.removeAttribute('src'); if (kind === 'video') try { motion.load(); } catch {} }
        element.remove();
    }};
}
// Keeps one media block in a holder while the release it shows stays the same, so a status poll does not restart it.
function mediaHolder(holder, cn) {
    let current = null;
    return {
        set(release, which) {
            const media = releaseMedia(release), key = media ? which + ':' + media.sha256 + ':' + media.poster : '';
            if (key === (current ? current.which + ':' + current.media.sha256 + ':' + current.media.poster : '')) return current;
            current?.dispose(); current = null;
            if (media) { current = createReleaseMedia(release, which, cn); holder.prepend(current.element); }
            return current;
        },
        dispose() { current?.dispose(); current = null; },
    };
}
// The release shown first: the available update, else the version in use.
const topRelease = () => value?.available ? [value.available, 'available'] : [value?.current_release, 'current'];

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
            if (languageHint && !url.searchParams.has(LANGUAGE_PARAM)) {
                url.searchParams.set(LANGUAGE_PARAM, languageHint);
                location.replace(url.href);
            } else location.reload();
            return;
        }
        value = next; offline = false;
        setLauncherPresent(next.launcher);
        setOfflineInstall(next.offline_installation);
        const phase = next.launcher?.phase || '';
        // Switching to a faster model also restarts ComfyUI once.
        if (working.has(phase) || next.launcher?.model) updating = true;
        else if (updating && phase !== 'restarting') updating = false;
        publish();
        // The server returns immediately while its initial network check runs.
        if (next.status === 'checking' || updating) later(2000);
    } catch {
        // Offline checks do not affect generation. During an update the
        // server restarts; keep asking until the updated one answers.
        offline = true;
        if (updating) { publish(); later(2000); }
        else if (!value) publish();
    } finally {
        pending = false;
        if (retrying) { retrying = false; publish(); }
    }
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
    failure = '';
    for (let attempt = 0; attempt < 2; attempt++) {
        try {
            const response = await api.fetchApi('/freevideo/updates/cancel', {method: 'POST', cache: 'no-store', signal: AbortSignal.timeout(10000)});
            if (!response.ok) throw new Error(response.statusText);
            updating = false;
            break;
        } catch {
            if (attempt === 1) failure = 'cancel';
        }
    }
    publish();
    lastCheck = -Infinity;
    later(800);
}

// The update bar and sheet styles, added once (the sheet can open before the checks start).
function loadStyles() {
    const href = new URL('./updates.css', import.meta.url).href;
    if ([...document.querySelectorAll('link[rel=stylesheet]')].some(link => link.href === href)) return;
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = href; document.head.append(css);
}
export function startUpdateChecks() {
    if (started) return;
    started = true;
    loadStyles();
    void checkUpdates();
    setInterval(checkUpdates, 60000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) void checkUpdates(); });
    // A reconnect after a restart answers at once instead of at the next poll.
    try { api.addEventListener('reconnected', () => { lastCheck = -Infinity; void checkUpdates(); }); } catch { /* Polling still works. */ }
}

export function createUpdateNotice(cn) {
    const t = (en, zh) => cn ? zh : en;
    const element = document.createElement('aside'); element.className = 'fv-update-notice fv-update-bar';
    element.hidden = true; element.setAttribute('role', 'status');
    const label = document.createElement('span');
    // Read by the issue button when clicked, so the report carries the latest render's reason.
    let problem, issueText = '';
    const details = document.createElement('button'); details.type = 'button';
    details.textContent = t("What's new", '更新内容');
    let closeNotes = () => {};
    // The update's own window: its picture or video on top, the full notes below, Later and Update now at the bottom.
    details.onclick = () => { closeNotes(); closeNotes = showReleaseNotes(cn); };
    // The update's cover, when it has one; it opens the same window. The button beside it is the keyboard's way in.
    const thumb = document.createElement('img'); thumb.className = 'fv-update-thumb'; thumb.alt = ''; thumb.hidden = true;
    thumb.draggable = false; thumb.decoding = 'async'; thumb.setAttribute('aria-hidden', 'true');
    thumb.onload = () => { thumb.dataset.ready = thumb.getAttribute('src'); render(); };
    thumb.onerror = () => { thumb.dataset.failed = thumb.getAttribute('src'); render(); };
    thumb.onclick = () => details.click();
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
    const close = document.createElement('button'); close.type = 'button'; close.className = 'fv-update-close';
    close.textContent = '×'; close.setAttribute('aria-label', t('Close', '关闭'));
    // Closing only hides the notice for this opening (this tab); a later opening reminds again.
    // Storage may be unavailable, and nothing is sent to the server.
    const dismissNotice = () => {
        dismissed = identity(value?.available);
        failure = '';
        try { sessionStorage.setItem(storageKey, dismissed); } catch {}
        publish();
    };
    dismiss.onclick = close.onclick = dismissNotice;
    const onKey = event => { if (event.key === 'Escape' && !event.defaultPrevented && !document.querySelector?.('dialog[open]:not(.fv-studio)') && !element.hidden && !dismiss.hidden) dismissNotice(); };
    document.addEventListener?.('keydown', onKey);
    const stop = document.createElement('button'); stop.type = 'button';
    stop.textContent = t('Cancel update', '取消更新');
    stop.onclick = () => { stop.disabled = true; void cancelUpdate().finally(() => { stop.disabled = false; }); };
    element.append(thumb, label, details, apply, link, dismiss, stop, close);
    const render = () => {
        const launcher = value?.launcher, phase = launcher?.phase || '', candidate = value?.available;
        const mac = value?.channel === 'macos-preview', linux = value?.channel === 'linux-preview';
        link.href = releasePage;
        link.title = mac
            ? t('After your task finishes, open the new FreeVideo.app to update.', '当前任务完成后，打开新版 FreeVideo.app 更新。')
            : linux ? t('After your task finishes, open the new FreeVideo AppImage to update.', '当前任务完成后，打开新版 FreeVideo AppImage 更新。')
            : t('After your task finishes, open the new FreeVideo.exe to update.', '当前任务完成后，打开新版 FreeVideo.exe 更新。');
        const progress = launcher?.progress, percent = progress?.total ? ' ' + Math.floor(100 * progress.done / progress.total) + '%' : '';
        // The last model state seen stays while ComfyUI is away.
        const model = launcher?.model || '';
        const busy = updating && (working.has(phase) || !!model || offline);
        let text = '', actions = false, reason = '', cause = '', short = '';
        issueText = '';
        if (failure === 'cancel') {
            text = t(CANCEL_FAILURE.en, CANCEL_FAILURE.zh);
        } else if (busy) {
            text = model === 'switching' || (model && offline)
                ? t('Switching to the faster model. This page reconnects by itself.', '正在切换到更快的模型，页面会自动恢复。')
                : model === 'waiting' ? t('The faster model is ready. FreeVideo switches after the current video.', '更快的模型已下载好，当前视频完成后自动切换。')
                : offline || phase === 'restarting' || phase === 'engine'
                ? t('Restarting FreeVideo to finish the update. The page refreshes by itself.', '正在重启 FreeVideo 完成更新，页面会自动刷新。')
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
            // The launcher's own reason (its copy, in the user's language) follows “last attempt failed”: its first
            // sentence, the cause, like the other parts without a full stop. The whole reason is the tooltip and goes in
            // the issue; program text from the launcher leaves only the prefix.
            if (launcher?.status === 'error' && launcher.error) {
                const prefix = t('Last attempt failed', '上次更新未完成');
                reason = userMessage(launcher.error, t, '');
                const first = (/^.+?(?:[。！？]|[.!?](?=\s|$))/u.exec(reason)?.[0] || reason).replace(/[。！？.!?]+$/u, '');
                // Lower-case the first word unless it is a name (FreeVideo, GitHub).
                cause = first && t(first.replace(/^[A-Z][a-z]*\b/, w => w.toLowerCase()), first);
                text += ' · ' + prefix; short = text;
                if (cause) text += t(': ', '：') + cause;
                issueText = short + (reason ? t(': ', '：') + reason : '');
            }
            actions = true;
        }
        element.hidden = !text;
        element.classList.toggle('fv-update-working', busy);
        label.textContent = text;
        label.removeAttribute('title'); if (reason) label.setAttribute('data-fv-tip-name', reason); else label.removeAttribute('data-fv-tip-name');
        // A cause that does not fit leaves only the prefix; the tooltip still has it.
        const fit = () => { if (cause && label.textContent === text && label.scrollWidth > label.clientWidth + 1) label.textContent = short; };
        if (cause) { fit(); globalThis.requestAnimationFrame?.(fit); }
        const failed = failure === 'cancel' || failure === 'launcher' && !busy && phase !== 'review'
            || actions && launcher?.status === 'error' && !!launcher.error;
        if (failed) {
            problem ||= issueButton(t, () => {
                const launcher = value?.launcher;
                return {where: ['Update FreeVideo', '更新 FreeVideo'], message: issueText || label.textContent,
                    facts: launcher?.error ? {launcher_error: String(launcher.error), launcher_status: launcher.status, launcher_phase: launcher.phase || ''} : null};
            });
            element.append(problem);
        } else problem?.remove();
        details.hidden = !actions;
        const media = actions && !failure && candidate ? releaseMedia(candidate) : null;
        const cover = media ? mediaAddress('available', 'poster', media) : '';
        if ((thumb.getAttribute('src') || '') !== cover) {
            if (cover) thumb.src = cover; else thumb.removeAttribute('src');
        }
        thumb.hidden = !cover || thumb.dataset.ready !== cover || thumb.dataset.failed === cover;
        element.classList.toggle('has-thumb', !thumb.hidden);
        const direct = !!launcher && !launcher.manual && !failure;
        apply.hidden = !actions || !direct;
        link.hidden = !actions || direct;
        dismiss.hidden = !actions || dismissed === identity(candidate);
        close.hidden = dismiss.hidden;
        stop.hidden = !(busy && phase === 'waiting' && !offline);
    };
    listeners.add(render); render();
    return {element, dispose: () => { listeners.delete(render); document.removeEventListener?.('keydown', onKey); closeNotes(); }};
}

// The notes, newest first: the available update (with every version it skips), then the current version. Returns
// whether this version has a "What's new" tour. Used by the release-notes sheet and by Settings › Version & updates.
function fillNotes(body, cn) {
    const t = (en, zh) => cn ? zh : en;
    body.replaceChildren();
    for (const [label, release, kind] of [[t('Available update', '可用更新'), value?.available, 'new'],
        [t('Current version', '当前版本'), value?.current_release || (value?.current_version ? {version: value.current_version} : null), 'now']]) {
        if (!release) continue;
        const section = document.createElement('section'), heading = document.createElement('h3');
        section.dataset.kind = kind;
        // The update is named by its version alone; the version in use keeps its label.
        heading.textContent = (kind === 'new' ? '' : label + ' · ') + displayVersion(release); section.append(heading);
        const build = document.createElement('p'); build.className = 'fv-release-build fv-copyable';
        build.textContent = release.development ? t('Development version', '开发版本') : t('Build: ', '构建号：') + (release.version || '—');
        section.append(build);
        // Settings hides the build line; the version heading carries it on hover.
        heading.dataset.fvTipName = displayVersion(release); heading.dataset.fvTipUse = build.textContent;
        const notes = localizedNotes(release, cn), summary = document.createElement('p');
        summary.textContent = notes?.summary || t('No release notes were included with this version.', '此版本未附带更新说明。'); section.append(summary);
        section.append(changeList(notes?.changes));
        if (release === value?.available) {
            for (const row of earlierNotes(release, value?.current_release?.product_version)) {
                const earlier = localizedNotes(row, cn);
                const name = document.createElement('h4'); name.textContent = 'v' + row.product_version;
                const text = document.createElement('p'); text.textContent = earlier?.summary || '';
                section.append(name, text, changeList(earlier?.changes));
            }
        }
        body.append(section);
    }
    if (!body.childElementCount) emptyNotes(body, t);
    return hasWhatsNew(productVersion());
}
// No version information yet: while the first check runs, say so; once it has failed, say why and offer a retry.
function emptyNotes(body, t) {
    if (!started && !offline) void checkUpdates();
    const empty = document.createElement('p'); empty.className = 'fv-release-empty';
    if (retrying || (!value && !offline)) { empty.textContent = t('Reading version information…', '正在读取版本信息…'); body.append(empty); return; }
    const message = t('Version information is not available: FreeVideo on this computer did not return it. Click Retry; if it still fails, report a problem.',
        '暂时无法获取版本信息：本机的 FreeVideo 服务没有返回结果。请点“重试”；仍然失败时请提交问题。');
    empty.textContent = message;
    const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'fv-release-retry';
    retry.textContent = t('Retry', '重试');
    // Keep what the last check returned; a failed retry then leaves the update bar as it was.
    retry.onclick = () => { retrying = true; offline = false; lastCheck = -Infinity; publish(); void checkUpdates(); };
    const row = document.createElement('div'); row.className = 'fv-release-empty-actions';
    row.append(retry, issueButton(t, () => ({where: ['Version information', '版本信息'], message})));
    body.append(empty, row);
}
export function releaseNotesInto(body, cn, {onNews = () => {}} = {}) {
    body.classList.add('fv-release-body');
    // The picture or video of the newest version above the notes; kept across status polls.
    const top = document.createElement('div'); top.className = 'fv-release-top';
    const sections = document.createElement('div'); sections.className = 'fv-release-sections';
    body.replaceChildren(top, sections);
    const media = mediaHolder(top, cn);
    const render = () => { const [release, which] = topRelease(); top.hidden = !media.set(release, which); onNews(fillNotes(sections, cn)); };
    listeners.add(render); render(); body.scrollTop = 0;
    return {dispose: () => { listeners.delete(render); media.dispose(); }};
}

function noteRows(release, cn) {
    const t = (en, zh) => cn ? zh : en, notes = localizedNotes(release, cn), rows = [];
    const summary = document.createElement('p'); summary.className = 'fv-release-summary';
    summary.textContent = notes?.summary || t('No release notes were included with this version.', '此版本未附带更新说明。');
    rows.push(summary);
    if (notes?.changes?.length) rows.push(changeList(notes.changes));
    return rows;
}
// The changes as "label + sentence" rows (the update window and Settings › Version & updates alike).
function changeList(changes) {
    const list = document.createElement('ul'); list.className = 'fv-release-list';
    for (const item of changes || []) {
        const [topic, sentence] = splitChange(item), row = document.createElement('li');
        if (topic) { const tag = document.createElement('span'); tag.className = 'fv-release-tag'; tag.textContent = topic; row.append(tag); }
        const text = document.createElement('span'); text.className = 'fv-release-text'; text.textContent = sentence; row.append(text);
        list.append(row);
    }
    return list;
}
const versionName = release => release?.product_version || release?.version || '—';
// The update window: the newest version's picture or video, its full notes (with every version it skips), then the
// version in use in one line. Returns whether the version in use has a "What's new" tour.
function fillSheet(body, cn) {
    const t = (en, zh) => cn ? zh : en;
    body.replaceChildren();
    const available = value?.available, current = value?.current_release || (value?.current_version ? {version: value.current_version} : null);
    const release = available || current;
    if (!release) { emptyNotes(body, t); return false; }
    const head = document.createElement('h2'); head.className = 'fv-release-ver';
    const name = document.createElement('b'); name.textContent = versionName(release);
    // The version alone heads the notes; the buttons and the version-in-use line below say the rest.
    head.append(name);
    const build = release.development ? t('Development version', '开发版本') : t('Build: ', '构建号：') + (release.version || '—');
    head.dataset.fvTipName = displayVersion(release); head.dataset.fvTipUse = build;
    body.append(head, ...noteRows(release, cn));
    if (available) {
        for (const row of earlierNotes(available, value?.current_release?.product_version)) {
            const name = document.createElement('h3'); name.className = 'fv-release-earlier'; name.textContent = row.product_version;
            body.append(name, ...noteRows(row, cn));
        }
        if (current) {
            const line = document.createElement('p'); line.className = 'fv-release-current';
            line.textContent = t('Current version ', '当前版本 ') + versionName(current);
            line.dataset.fvTipName = displayVersion(current);
            line.dataset.fvTipUse = current.development ? t('Development version', '开发版本') : t('Build: ', '构建号：') + (current.version || '—');
            body.append(line);
        }
    }
    return !available && hasWhatsNew(productVersion());
}

function showReleaseNotes(cn) {
    const t = (en, zh) => cn ? zh : en;
    loadStyles();
    const dialog = document.createElement('dialog'); dialog.className = 'fv-release-notes fv-release-rich';
    dialog.setAttribute('aria-label', t('Version & updates', '版本与更新'));
    const top = document.createElement('div'); top.className = 'fv-release-top';
    const body = document.createElement('div'); body.className = 'fv-release-body';
    const button = (text, className = '') => { const b = document.createElement('button'); b.type = 'button'; b.textContent = text; if (className) b.className = className; b.dataset.fvTipName = text; return b; };
    const close = button(t('Close', '关闭'), 'fv-primary'); close.onclick = () => dialog.close();
    close.dataset.fvTipUse = t('Close the release notes', '关闭版本说明'); close.dataset.fvTipKey = 'Esc';
    const news = button(t("What's new", '新功能'));
    news.dataset.fvTipUse = t('Play the tour of this version', '播放这一版的新功能介绍');
    news.onclick = () => { dialog.close(); import('./whatsnew.js').then(({showWhatsNew}) => showWhatsNew(cn)); };
    const later = button(t('Later', '稍后'));
    later.dataset.fvTipUse = t('Keep this version for now', '暂不更新'); later.dataset.fvTipKey = 'Esc';
    later.onclick = () => {
        dismissed = identity(value?.available); failure = '';
        try { sessionStorage.setItem(storageKey, dismissed); } catch {}
        dialog.close(); publish();
    };
    const apply = button(t('Update now', '立即更新'), 'fv-primary fv-update-apply');
    apply.dataset.fvTipUse = t('FreeVideo restarts once; running videos finish first.', 'FreeVideo 会重启一次，正在生成的视频会先完成。');
    apply.onclick = () => { apply.disabled = true; dialog.close(); void applyUpdate().finally(() => { apply.disabled = false; }); };
    const link = document.createElement('a'); link.className = 'fv-primary'; link.href = releasePage; link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.textContent = t('Download update', '下载新版'); link.onclick = () => dialog.close();
    const foot = document.createElement('footer'); foot.className = 'fv-release-foot'; foot.append(news, later, apply, link, close);
    dialog.append(top, body, foot);
    const media = mediaHolder(top, cn);
    const render = () => {
        const [release, which] = topRelease();
        const shown = media.set(release, which);
        top.hidden = !shown; dialog.classList.toggle('has-media', !!shown);
        news.hidden = !fillSheet(body, cn);
        // As the bar: Later and Update now while an update can start; Close otherwise.
        const launcher = value?.launcher, phase = launcher?.phase || '';
        const busy = updating && (working.has(phase) || !!launcher?.model || offline);
        const offer = !!value?.available && !busy && !failure && phase !== 'review';
        const direct = !!launcher && !launcher.manual;
        later.hidden = !offer; apply.hidden = !offer || !direct; link.hidden = !offer || direct; close.hidden = offer;
        const mac = value?.channel === 'macos-preview', linux = value?.channel === 'linux-preview';
        link.title = mac ? t('After your task finishes, open the new FreeVideo.app to update.', '当前任务完成后，打开新版 FreeVideo.app 更新。')
            : linux ? t('After your task finishes, open the new FreeVideo AppImage to update.', '当前任务完成后，打开新版 FreeVideo AppImage 更新。')
            : t('After your task finishes, open the new FreeVideo.exe to update.', '当前任务完成后，打开新版 FreeVideo.exe 更新。');
    };
    // While more notes are below, their lower edge fades out instead of cutting a line of text in half.
    const edge = () => { body.dataset.more = String(body.scrollTop + body.clientHeight < body.scrollHeight - 1); };
    body.addEventListener('scroll', edge, {passive: true});
    let sized = null;
    try { sized = new ResizeObserver(edge); sized.observe(body); } catch {}
    const dispose = () => { listeners.delete(render); listeners.delete(edge); sized?.disconnect(); media.dispose(); dialog.remove(); };
    dialog.addEventListener('close', dispose, {once: true});
    // Keep Escape local to this sheet; the creative workspace stays open.
    dialog.addEventListener('keydown', event => event.stopPropagation());
    listeners.add(render); listeners.add(edge); render(); document.body.append(dialog); dialog.showModal(); body.scrollTop = 0; edge();
    // The main action takes the focus: Update now, or Close.
    (([...foot.children].reverse().find(b => !b.hidden)) || close).focus({preventScroll: true});
    return () => { if (dialog.open) dialog.close(); dispose(); };
}

export function createVersionInfo(cn) {
    const element = document.createElement('button'); element.type = 'button'; element.className = 'fv-version-info';
    element.title = cn ? '版本与更新说明' : 'Version & release notes'; element.setAttribute('aria-label', element.title);
    let closeNotes = () => {};
    // Version & updates is a page of Settings (one place for the version, the update and the notes); the sheet is the
    // fallback when Settings cannot open.
    element.onclick = async () => {
        closeNotes();
        try { const {openSetup} = await import('./setup.js'); await openSetup({section: 'about'}); }
        catch { closeNotes = showReleaseNotes(cn); }
    };
    const render = () => {
        element.textContent = cn ? '版本' : 'Version';
        if (value?.available) { const dot = document.createElement('i'); dot.className = 'fv-version-dot'; dot.setAttribute('aria-hidden', 'true'); element.append(dot); }
    };
    listeners.add(render); render();
    return {element, dispose: () => { listeners.delete(render); closeNotes(); }};
}
