// Checks that FreeVideo's page loaded completely and says what is wrong when
// it did not: which file, why, and what to do. It imports nothing at load and
// styles itself inline, so it still runs when every other FreeVideo file fails.
// Other FreeVideo files report through `window.__freevideoHealth.push(...)`.
// The shared browser-extension check and error advice load only once the page
// check runs, after the listeners are installed; each may fail on its own.
let extensionUI = null, errorUI = null;
const base = new URL('./', import.meta.url);
const query = new URLSearchParams(location.search);
const language = query.get('freevideo_lang');
const cn = language === 'zh' || (language !== 'en' && String(navigator.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;
// launcher.js removes this flag during setup; read it before that.
const launchVisit = query.get('freevideo') === 'launch';
// Every script this folder ships; a test keeps the list in step with web/*.js.
export const MODULES = ['audio_original.js', 'chat_data.js', 'phone_model.js', 'phone.js', 'lan_access.js', 'qrcodegen.js', 'agent_activity.js', 'agent_connect.js', 'agent_session.js', 'autopilot.js', 'autopilot_chat.js', 'chat_attachments.js', 'autopilot_conversations.js', 'browser_extension.js', 'caret_rect.js', 'chain_support.js', 'chain_rules.js', 'capability_view.js', 'cast_generate.js', 'cast_mention.js', 'cast_overlay.js', 'cast_source.js', 'compatibility.js', 'entry_showcase.js', 'error_panel.js', 'first_run_guide.js', 'fonts.js', 'freevideo.js', 'generation_progress.js',
    'editor.js', 'editor_actions.js', 'editor_lifecycle.js', 'editor_adjust.js', 'editor_adjust_picture.js', 'editor_picture_fields.js', 'editor_picture_session.js', 'editor_preview_picture.js', 'editor_entry.js', 'editor_expand.js', 'editor_export_dialog.js', 'editor_first_frame.js', 'editor_media_references.js', 'editor_references.js', 'editor_genbar.js', 'editor_geometry.js', 'editor_library.js', 'editor_menu.js', 'editor_model.js', 'editor_frame.js', 'editor_player.js', 'editor_room.js', 'editor_select.js', 'editor_state.js', 'editor_store.js', 'editor_tracks.js', 'editor_generation.js', 'editor_transport.js', 'editor_transport_model.js', 'editor_audio.js', 'editor_audio_model.js', 'editor_audio_notes.js', 'editor_audio_replace.js', 'editor_audio_rules.js', 'editor_speed.js', 'editor_speed_model.js', 'editor_failure.js', 'editor_header_fit.js',
    'editor_loader.js', 'editor_text_coverage.js', 'editor_text.js', 'editor_toolbar_more.js', 'editor_text_model.js', 'editor_text_layout.js', 'editor_notes.js', 'editor_icons.js', 'dialog_keys.js', 'done_notify.js', 'failure_issue.js', 'feedback.js', 'frame_sizes.js', 'health.js', 'image_commands.js', 'image_editor.js', 'image_editor_engine.js', 'image_generation.js', 'launch_visit.js', 'launcher.js', 'length_plan.js', 'lora_triggers.js', 'lora_check.js', 'library.js', 'motion.js', 'native_errors.js', 'translate_guard.js', 'output_download.js', 'port_notice.js', 'preview_scene.js',
    'progress_connection.js', 'prompt_draft.js', 'prompt_enhance.js', 'prompt_references.js', 'prompt_sections.js', 'prompt_terms.js', 'request_assets.js', 'repair_hint.js', 'report_issue.js', 'issue_button.js', 'studio_recent.js',
    'result_actions.js', 'result_reuse.js', 'sampling_effort.js', 'setup.js', 'share.js', 'share_layout.js', 'studio.js', 'studio_chain.js', 'studio_extend.js', 'studio_length.js', 'first_frame_row.js', 'replace_person.js', 'replace_state.js', 'optional_inputs.js', 'studio_queue.js', 'studio_size.js',
    'toolbar_position.js', 'updates.js', 'view_navigation.js', 'wordmark.js', 'workspace_restore.js', 'cast_library.js', 'media_upload.js', 'whitebox_encode.js', 'whitebox_mp4.js', 'whitebox_recording.js', 'whitebox_render.js', 'whitebox_scenes.js', 'whitebox_worker.js'];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let visibleAt = 0;
async function waitForVisible() {
    for (;;) {
        if (document.hidden) await new Promise(resolve => {
            const visible = () => {
                if (document.hidden) return;
                document.removeEventListener('visibilitychange', visible);
                resolve();
            };
            document.addEventListener('visibilitychange', visible);
        });
        // Let the animation frame that opens the workspace run first.
        const remaining = 1500 - (Date.now() - visibleAt);
        if (remaining > 0) { await sleep(remaining); continue; }
        if (!document.hidden) return;
    }
}
// A file the browser failed to load stays failed for the page's lifetime even
// once the server sends it again, and a plain reload can take the same broken
// copy from the HTTP cache. So when every script is reachable but some would
// not run, the page refreshes the cache and reloads itself once.
const RETRY_KEY = 'freevideo.page-retry';
const RETRY_WINDOW = 5 * 60000;
// null when this browser keeps no session storage: then nothing retries.
const retryRecord = (() => {
    try { return JSON.parse(sessionStorage.getItem(RETRY_KEY) || '{}') || {}; } catch { return null; }
})();
const retried = !!retryRecord && Date.now() - (Number(retryRecord.at) || 0) < RETRY_WINDOW;
// Any click or key outside FreeVideo's own panels: a reload could lose edits.
let touched = false;
const fileName = url => decodeURIComponent(String(url).split(/[?#]/)[0].split('/').pop() || String(url));
const ours = url => typeof url === 'string' && url.startsWith(base.href);

// Errors seen while the page loads: failed files, and console errors from
// ComfyUI that name the extension or file that failed.
const seen = [];
const note = (kind, text, extra = {}) => { if (seen.length < 60) seen.push({kind, text: String(text).slice(0, 500), ...extra}); };
// Browser-extension frames without query or fragment, and user scripts by file
// name only: enough to name the culprit, never a private URL, path or token.
const extensionFrames = text => [...new Set(String(text || '').match(
    /(?:chrome|moz|safari-web)-extension:\/\/[^\s)?#'"]+|[^\s()'"?#/\\]+\.user\.js\b|ERR_BLOCKED_BY_CLIENT/gi) || [])].slice(0, 5);
// Recent extension evidence for the shared check, kept apart from what is
// reported and timed so an old console error cannot explain a later failure.
const extensionSeen = [];
const describe = value => value instanceof Error ? [`${value.name}: ${value.message}`, ...extensionFrames(value.stack)].join('\n')
    : value && typeof value === 'object' && value.error instanceof Error ? describe(value.error)
    : typeof value === 'string' ? value : (() => { try { return JSON.stringify(value); } catch { return String(value); } })();
const consoleError = console.error;
const reports = [];
let listener = null;
function receive(item) {
    if (!item || typeof item !== 'object') return;
    reports.push(item);
    listener?.(item);
}

// Only the first copy of this file listens; see the end of the file.
function install() {
    const touch = event => { if (!event.target?.closest?.('.fv-studio, .fv-health')) touched = true; };
    addEventListener('pointerdown', touch, true); addEventListener('keydown', touch, true);
    addEventListener('error', event => {
        const target = event.target;
        if (target && target !== window && (target.href || target.src)) note('resource', target.href || target.src, {url: target.href || target.src});
        else if (event.filename && ours(event.filename)) note('script', `${fileName(event.filename)}: ${event.message}`);
    }, true);
    addEventListener('unhandledrejection', event => {
        const reason = event.reason;
        if (String(reason?.stack || '').includes(base.href)) note('script', describe(reason));
    });
    console.error = function (...args) {
        try {
            const text = args.map(describe).join(' ');
            const frames = extensionFrames(text);
            if (frames.length) {
                extensionSeen.push({at: Date.now(), text: frames.join('\n')});
                if (extensionSeen.length > 20) extensionSeen.shift();
            }
            if (/Error loading extension|Error calling extension|already registered|Failed to fetch dynamically imported module|does not provide an export/i.test(text)
                || text.includes(base.pathname)) note('console', text);
            else if (frames.length) note('console', frames.join('\n'));
        } catch { /* Never interfere with the page's own logging. */ }
        return consoleError.apply(this, args);
    };
    // Files that loaded first queued their reports in an array.
    const queued = Array.isArray(window.__freevideoHealth) ? window.__freevideoHealth : [];
    window.__freevideoHealth = {push: receive};
    for (const item of queued) receive(item);
}

const comfy = () => window.app?.graph ? window.app : window.comfyAPI?.app?.app?.graph ? window.comfyAPI.app.app : null;
const hasGenerate = graph => (graph?._nodes || graph?.nodes || []).some(n => n.comfyClass === 'FreeVideoGenerate' || n.type === 'FreeVideoGenerate');
const studioWorkflow = graph => hasGenerate(graph) && !!graph?.extra?.freevideo_studio;
const preferredView = () => { try { return localStorage.getItem('freevideo.view'); } catch { return null; } };
async function waitFor(test, ms) {
    const end = Date.now() + ms;
    for (;;) {
        const value = test();
        if (value || Date.now() >= end) return value;
        await sleep(200);
    }
}

async function probe(url) {
    try {
        const response = await fetch(url, {cache: 'no-store'});
        return {status: response.status, ok: response.ok, type: (response.headers.get('content-type') || '').split(';')[0].trim()};
    } catch (error) {
        return {status: 0, ok: false, type: '', error: describe(error), exception: error};
    }
}

const ADVICE = {
    files: t('FreeVideo’s page files are missing or unreadable. In the FreeVideo launcher, open Settings and click Repair, then open FreeVideo again. Without the launcher, update the FreeVideo node and restart ComfyUI.',
        'FreeVideo 的网页文件缺失或无法读取。请在 FreeVideo 启动器的“设置”里点击“修复”，完成后重新打开。没有使用启动器时，请更新 FreeVideo 节点并重启 ComfyUI。'),
    type: t('ComfyUI sent the file with the wrong type, so the browser refused it. Restart ComfyUI. If it persists, use Report a problem.',
        'ComfyUI 发给浏览器的文件类型不对，浏览器拒绝使用。请重启 ComfyUI；仍然出现时，请点“提交问题”。'),
    browser: t('This browser cannot run FreeVideo. Open the same address in a current Chrome or Edge.',
        '当前浏览器无法运行 FreeVideo。请用最新版 Chrome 或 Edge 打开同一个地址。'),
    mixed: t('The browser mixed files from two FreeVideo versions. Press Ctrl+F5 to reload without the cache.',
        '浏览器混用了新旧两个版本的 FreeVideo 文件。请按 Ctrl+F5 强制刷新。'),
    duplicate: t('Keep custom_nodes/FreeVideo, move the other FreeVideo folders out of custom_nodes, then restart ComfyUI.',
        '请保留 custom_nodes/FreeVideo，把其他 FreeVideo 文件夹移出 custom_nodes，然后重启 ComfyUI。'),
    plugin: t('Another ComfyUI extension reported an error at the same time. Turn it off in ComfyUI Settings › Extensions, then reload.',
        '同时有其他 ComfyUI 插件报错。可在 ComfyUI“设置 › 扩展”里暂时停用它，然后刷新页面。'),
    reload: t('Reload the page. If it happens again, use Report a problem.',
        '请刷新页面。仍然出现时，请点“提交问题”。'),
    cache: t('FreeVideo already reloaded this page once with fresh copies of its files. Press Ctrl+F5. If it still fails, turn off browser extensions that block scripts on this page or open the same address in another browser. If that does not help, use Report a problem.',
        'FreeVideo 已经用重新下载的文件自动刷新过一次页面。请按 Ctrl+F5 强制刷新；仍然出现时，请停用会拦截此页面脚本的浏览器插件，或换一个浏览器打开同一地址。仍未解决时，请点“提交问题”。'),
    relaunch: t('Click Start in the FreeVideo launcher again.', '请在 FreeVideo 启动器里重新点击“启动”。'),
};

// Requests url the way a script (modulepreload) or a stylesheet (preload) is
// loaded, without running or applying it. The result is for this file alone:
// its load event does not wait for what it imports. true when it loads, false
// when the browser stops it, null when this browser cannot tell.
function loadsAs(kind, url) {
    const link = document.createElement('link');
    const rel = kind === 'script' ? 'modulepreload' : 'preload';
    if (!link.relList?.supports?.(rel)) return Promise.resolve(null);
    link.rel = rel;
    if (kind === 'style') link.as = 'style';
    return new Promise(resolve => {
        const done = value => { clearTimeout(timer); link.remove(); resolve(value); };
        const timer = setTimeout(() => done(null), 10000);
        link.onload = () => done(true); link.onerror = () => done(false);
        link.href = url; document.head.append(link);
    });
}
// An address this page has never requested: no module-map entry, no cached copy.
const freshURL = name => `${new URL(name, base).href}?freevideo-check=${Date.now()}`;
// Blocked: a new address that downloads with fetch but will not load as a
// script or stylesheet, which is what an ad blocker's rule for a file name
// does. false when it loads; null when the browser cannot tell or the address
// does not download either.
async function blockedAs(kind, name) {
    const url = freshURL(name), loads = await loadsAs(kind, url);
    if (loads !== false) return loads === null ? null : false;
    return (await probe(url)).ok ? true : null;
}
// The scripts among names that import a blocked one, directly or through another of them.
async function importers(names, blocked) {
    const reach = new Set(blocked), imports = new Map();
    await Promise.all(names.map(async name => {
        let text = '';
        try { text = await (await fetch(new URL(name, base), {cache: 'no-store'})).text(); } catch { /* Imports nothing we know of. */ }
        imports.set(name, [...text.matchAll(/\b(?:import|export)\b[^'"`;]*?\bfrom\s*['"]\.\/([\w.-]+\.js)['"]|\bimport\s*['"]\.\/([\w.-]+\.js)['"]/g)]
            .map(match => match[1] || match[2]));
    }));
    for (let grew = true; grew;) {
        grew = false;
        for (const [name, list] of imports) if (!reach.has(name) && list.some(item => reach.has(item))) { reach.add(name); grew = true; }
    }
    return names.filter(name => reach.has(name));
}
const listNames = names => names.length > 3 ? `${names.slice(0, 3).join(cn ? '、' : ', ')}${t(` and ${names.length - 3} more`, ` 等 ${names.length} 个文件`)}` : names.join(cn ? '、' : ', ');

// What the HTTP cache holds for a file, which is what a module load reads.
async function cachedCopy(url) {
    try {
        const response = await fetch(url, {cache: 'only-if-cached', mode: 'same-origin'});
        const type = (response.headers.get('content-type') || '').split(';')[0].trim() || 'no type';
        return `HTTP ${response.status} ${type}, ${(await response.arrayBuffer()).byteLength} bytes`;
    } catch { return 'none'; }
}

// One sentence per finding, naming the file and the reason.
async function diagnoseModules() {
    const found = [], fetched = new Map();
    for (const name of MODULES) {
        const url = new URL(name, base).href, result = await probe(url);
        fetched.set(name, result);
        if (!result.ok) {
            found.push({text: result.status
                ? t(`Script ${name} could not be loaded: the server answered HTTP ${result.status}.`, `脚本 ${name} 无法加载：服务器返回 HTTP ${result.status}。`)
                : t(`Script ${name} could not be loaded: ${result.error}.`, `脚本 ${name} 无法加载：${result.error}。`), advice: 'files', error: result.exception});
        } else if (!/javascript|ecmascript/i.test(result.type)) {
            found.push({text: t(`Script ${name} was refused: the server sent it as “${result.type || 'no type'}”.`,
                `脚本 ${name} 被浏览器拒绝：服务器发送的类型是“${result.type || '未标明'}”。`), advice: 'type'});
        }
    }
    if (found.length) return found;
    // Every file is reachable, so the browser refused one while linking or running it.
    const failed = new Map();
    for (const name of MODULES) {
        try { await import(new URL(name, base).href); } catch (error) {
            const message = describe(error);
            failed.set(message, [...(failed.get(message) || []), name]);
        }
    }
    const kindOf = message => /does not provide an export/.test(message) ? 'mixed'
        : /SyntaxError|Unexpected token|Unexpected identifier|is not a function|is not defined|is not a constructor/.test(message) ? 'browser' : 'reload';
    // A file that downloads but will not load as a script even from a new
    // address is blocked, by an ad blocker or another browser extension, and a
    // reload cannot help. The scripts that import it fail with it.
    const unloaded = [...failed].filter(([message]) => kindOf(message) === 'reload').flatMap(([, names]) => names);
    const results = await Promise.all(unloaded.map(name => blockedAs('script', name)));
    const blocked = unloaded.filter((name, index) => results[index] === true);
    const checks = unloaded.map((name, index) => `${name}: fetch HTTP ${fetched.get(name).status} ${fetched.get(name).type || 'no type'}; `
        + `as a script from a new address: ${results[index] ? 'blocked' : results[index] === false ? 'loads' : 'unknown'}`);
    if (blocked.length) {
        const dependents = await importers(unloaded.filter(name => !blocked.includes(name)), blocked);
        const named = new Set([...blocked, ...dependents]);
        for (const [message, names] of failed) {
            const rest = names.filter(name => !named.has(name));
            if (rest.length) failed.set(message, rest); else failed.delete(message);
        }
        // The card says what happened; the copied details keep the evidence.
        const more = dependents.length;
        if (more) checks.push(`could not run without ${blocked.join(', ')}: ${dependents.join(', ')}`);
        found.push({text: t(`The browser blocked ${listNames(blocked)}${more ? `, so ${more} more ${more === 1 ? 'script' : 'scripts'} could not run.` : '.'}`,
            `浏览器拦截了 ${listNames(blocked)}${more ? `，因此另有 ${more} 个脚本无法运行。` : '。'}`), advice: 'blocked', retry: false, checks});
    }
    for (const [message, names] of failed) {
        const kind = kindOf(message);
        const cache = [];
        for (const name of names) cache.push(`${name}: ${await cachedCopy(new URL(name, base).href)}`);
        // Reachable files the browser would not run, or old and new copies mixed:
        // fresh copies and one reload resolve both, unless a file is blocked.
        const retry = kind !== 'browser' && !blocked.length;
        found.push({text: t(`The browser could not run ${listNames(names)}: ${message}`, `浏览器无法运行 ${listNames(names)}：${message}`),
            advice: retry && retried ? 'cache' : kind, retry, cache, checks: found.length ? [] : checks});
    }
    return found;
}

function themeApplied() {
    const probe = document.createElement('div'); probe.className = 'fv-panel'; probe.hidden = true;
    document.body.append(probe);
    const value = getComputedStyle(probe).getPropertyValue('--fv-bg').trim();
    probe.remove();
    return value === '#111720';
}

const blockedSheet = name => ({text: t(`The browser blocked the stylesheet ${name}.`, `浏览器拦截了样式文件 ${name}。`), advice: 'blocked',
    checks: [`${name}: downloads with fetch; as a stylesheet from a new address: blocked`]});

async function diagnoseStyles() {
    if (themeApplied()) return [];
    const found = [];
    const links = [...document.querySelectorAll('link[rel="stylesheet"]')].filter(link => ours(link.href));
    const failed = new Set(seen.filter(row => row.kind === 'resource').map(row => row.url));
    for (const link of links) {
        // A stylesheet that failed still gets an empty sheet; ours are never empty.
        let loaded = false;
        try { loaded = !!link.sheet && link.sheet.cssRules.length > 0; } catch { loaded = !!link.sheet; }
        if (loaded && !failed.has(link.href)) continue;
        const name = fileName(link.href), result = await probe(link.href);
        found.push(!result.ok
            ? {text: t(`Stylesheet ${name} could not be loaded: ${result.status ? `HTTP ${result.status}` : result.error}.`,
                `样式文件 ${name} 无法加载：${result.status ? `HTTP ${result.status}` : result.error}。`), advice: 'files', error: result.exception}
            : result.type !== 'text/css'
                ? {text: t(`Stylesheet ${name} was refused: the server sent it as “${result.type || 'no type'}”.`,
                    `样式文件 ${name} 被浏览器拒绝：服务器发送的类型是“${result.type || '未标明'}”。`), advice: 'type'}
                : await blockedAs('style', name) ? blockedSheet(name)
                    : {text: t(`Stylesheet ${name} did not load.`, `样式文件 ${name} 没有生效。`), advice: 'reload'});
    }
    if (!found.length) {
        found.push({text: links.length
            ? t('FreeVideo’s styles loaded but do not apply; another extension may be overriding them.', 'FreeVideo 的样式已下载但没有生效，可能被其他插件的样式覆盖。')
            : t('FreeVideo’s styles were never requested.', 'FreeVideo 的样式文件没有被加载。'), advice: links.length ? 'plugin' : 'reload'});
    }
    return found;
}

// Every custom-node folder that serves FreeVideo's page, from ComfyUI's own list.
async function freevideoFolders() {
    for (const path of ['../../api/extensions', '../../extensions']) {
        try {
            const response = await fetch(new URL(path, base), {cache: 'no-store'});
            if (!response.ok) continue;
            const folders = (await response.json()).map(url => /\/extensions\/([^/]+)\/freevideo\.js$/.exec(String(url))?.[1]).filter(Boolean);
            return [...new Set(folders.map(decodeURIComponent))];
        } catch { /* Try the older route. */ }
    }
    return [];
}

// Errors from other extensions explain a failure only when ours failed too.
function pluginErrors() {
    const names = new Map();
    for (const row of seen) {
        const match = /Error (?:calling|loading) extension '?([^' ]+)'?(?: method '([^']+)')?/.exec(row.text);
        if (!match || match[1].startsWith('FreeVideo') || ours(match[1])) continue;
        if (!names.has(match[1])) names.set(match[1], row.text);
    }
    return [...names].slice(0, 3).map(([name, text]) => ({
        text: t(`Extension ${fileName(name)} reported an error: ${text.slice(0, 240)}`, `插件 ${fileName(name)} 报错：${text.slice(0, 240)}`), advice: 'plugin'}));
}

// Whenever the workflow in front has a FreeVideo node, the creative workspace
// or the toolbar that opens it must be on screen. The nodes still generate
// without them, so a page missing both looks like it works.
function onScreen(element) {
    if (!element?.isConnected || element.hidden) return false;
    const style = getComputedStyle(element);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const box = element.getBoundingClientRect();
    return box.width > 0 && box.height > 0 && box.bottom > 0 && box.right > 0 && box.top < innerHeight && box.left < innerWidth;
}

// Unstyled, the toolbar sits in the page's flow under ComfyUI's full-window
// canvas: inside the window, sized, and invisible. Look at what is on top of
// its view switch; a dialog, menu, toast or this card only passes over it.
const OVERLAYS = 'dialog, [role="dialog"], [role="menu"], .p-dialog-mask, .p-overlay-mask, .p-toast, .litecontextmenu, .fv-health';
// Why a dialog that is open does not count as on screen, for the details of the report: which test it fails
// and the box and window it was measured against (no page content).
function whyOffScreen(element) {
    const style = getComputedStyle(element), box = element.getBoundingClientRect();
    const reason = !element.isConnected ? 'not in the page' : element.hidden ? 'hidden attribute'
        : style.display === 'none' ? 'display: none' : style.visibility === 'hidden' ? 'visibility: hidden'
        : Number(style.opacity) === 0 ? 'opacity: 0' : !(box.width > 0 && box.height > 0) ? 'no size' : 'outside the window';
    return `${reason}; box ${Math.round(box.left)},${Math.round(box.top)} ${Math.round(box.width)}x${Math.round(box.height)}; window ${innerWidth}x${innerHeight}`;
}
// A layer hides the toolbar only if it paints over it. ComfyUI's dimming
// backdrops and click-away layers (a dialog's shade, the help panel's) leave it
// visible underneath, so they are not a fault.
const SOLID = /^(canvas|video|img|iframe|object|embed|svg)$/i;
function alphaOf(color) {
    if (color === 'transparent') return 0;
    const slash = color.match(/\/\s*([\d.]+)(%?)\s*\)/);
    if (slash) return Number(slash[1]) / (slash[2] ? 100 : 1);
    const legacy = color.match(/^rgba\(([^)]*)\)/);
    const parts = legacy ? legacy[1].split(',') : [];
    return parts.length === 4 ? Number(parts[3]) : 1;
}
function paints(element) {
    const style = getComputedStyle(element);
    if (style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    return SOLID.test(element.tagName) || style.backgroundImage !== 'none'
        || alphaOf(style.backgroundColor) * Number(style.opacity) >= 0.9;
}
function coveredBy(bar) {
    const box = (bar.querySelector('.fv-view-switch') || bar).getBoundingClientRect();
    const stack = document.elementsFromPoint(Math.min(innerWidth - 1, Math.max(0, box.left + box.width / 2)),
        Math.min(innerHeight - 1, Math.max(0, box.top + box.height / 2)));
    for (const top of stack) {
        if (bar.contains(top) || top.closest(OVERLAYS)) return null;
        if (paints(top)) return top;
    }
    return null;
}
const nameOf = element => `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ''}`
    + `${[...element.classList].slice(0, 4).map(name => `.${name}`).join('')} (z-index ${getComputedStyle(element).zIndex})`;

// A video enlarged to full screen (the maximize button on a node's video, or
// the player's own) hides the rest of the page until it is left again.
function fullscreenOver(element) {
    const shown = document.fullscreenElement || document.webkitFullscreenElement;
    return !!shown && !shown.contains(element);
}

// Our sheets are same-origin and never empty: rules that cannot be read were not loaded.
function sheetLoaded(name) {
    const link = [...document.querySelectorAll('link[rel="stylesheet"]')].find(item => ours(item.href) && fileName(item.href) === name);
    if (!link || seen.some(row => row.kind === 'resource' && fileName(row.url || '') === name)) return false;
    try { return link.sheet.cssRules.length > 0; } catch { return false; }
}

function workspaceProblem(app) {
    // Scripts that never started are already named by the load check.
    if (!window.FreeVideoUI?.ready || !hasGenerate(app.graph)) return null;
    const opening = document.querySelector('dialog.fv-workspace-opening[open]');
    if (opening && onScreen(opening)) return null;
    // The editor shares the studio shell; either view replaces the floating toolbar.
    const studio = document.querySelector('dialog.fv-editor[open]') || document.querySelector('dialog.fv-studio[open]');
    if (studio) return onScreen(studio) ? null
        : {text: t('The creative workspace is open but cannot be seen on the page.', '创作面板已经打开，但页面上看不到。'), advice: 'reload',
            state: `${nameOf(studio)}: ${whyOffScreen(studio)}`};
    const bar = document.querySelector('.fv-view-navigation');
    if (!bar) return {text: t('The FreeVideo toolbar that opens the creative workspace is missing from the page.', '页面上没有打开创作面板的 FreeVideo 工具栏。'), advice: 'reload'};
    const cover = onScreen(bar) ? coveredBy(bar) : null;
    if (onScreen(bar) && !cover) return null;
    // Its own stylesheet places it above the canvas.
    return sheetLoaded('view_navigation.css')
        ? {text: t('The FreeVideo toolbar that opens the creative workspace is hidden or covered by other page elements.', '打开创作面板的 FreeVideo 工具栏被页面上的其他元素隐藏或遮挡了。'), advice: 'plugin',
            ...(cover && {cover: nameOf(cover)})}
        : {text: t('The FreeVideo toolbar that opens the creative workspace cannot be seen: its stylesheet view_navigation.css did not load.',
            '看不到打开创作面板的 FreeVideo 工具栏：样式文件 view_navigation.css 没有加载。'), advice: 'reload', sheet: 'view_navigation.css'};
}

// Looks again as the page changes, such as another workflow coming to the
// front. A miss counts once it persists after the toolbar was asked to refresh.
async function watchWorkspace(app) {
    let missed = '', shown = '';
    for (;;) {
        await sleep(2500);
        if (document.hidden) continue;
        if (fullscreenOver(document.querySelector('.fv-view-navigation') || document.body)) { missed = ''; continue; }
        const problem = workspaceProblem(app);
        if (!problem) { missed = ''; continue; }
        if (missed !== problem.text) {
            missed = problem.text;
            window.dispatchEvent(new CustomEvent('freevideo-navigation-refresh'));
            continue;
        }
        if (shown === problem.text) continue;
        const named = problem.sheet && await blockedAs('style', problem.sheet) ? blockedSheet(problem.sheet) : problem;
        // The page can change while the stylesheet is checked.
        if (document.hidden || fullscreenOver(document.querySelector('.fv-view-navigation') || document.body)
            || workspaceProblem(app)?.text !== problem.text) continue;
        shown = problem.text;
        const problems = distinct([...lastProblems, named, ...reports.filter(item => item.type === 'error').map(reportText), ...pluginErrors()]);
        show(problems); send('error', problems);
    }
}

function reportText(item) {
    const where = {studio: t('The creative workspace could not open', '创作面板打不开'),
        navigation: t('The FreeVideo toolbar could not be added', 'FreeVideo 工具栏没有加载'),
        progress: t('Live progress could not start', '生成进度无法更新'),
        updates: t('Update checks could not start', '更新检查无法启动'),
        launch: t('The FreeVideo workflow did not open', 'FreeVideo 工作流没有打开')}[item.where] || t('FreeVideo reported an error', 'FreeVideo 报错');
    return {text: `${where}${cn ? '：' : ': '}${describe(item.error)}`, error: item.error, advice: item.where === 'launch' ? 'relaunch' : 'reload'};
}

function extensionProblem(problem) {
    if (!extensionUI) return null;
    // Preserve the same known engine causes as the execution/queue cards.
    // A missing error-panel module must not disable this page check itself.
    if (problem.advice !== 'blocked' && errorUI) {
        const cause = errorUI.failureAdvice(errorUI.errorText(problem.error || problem.text), t,
            problem.error?.known_error ?? errorUI.createErrorReport(problem.error || problem.text).known_error);
        if (cause.kind !== 'unknown') return null;
    }
    const kind = problem.advice === 'blocked' ? 'blocked'
        : ['reload', 'cache', 'relaunch', 'plugin'].includes(problem.advice) ? 'unknown'
        : extensionUI.isNetworkTypeError(problem.error) ? 'network' : problem.advice;
    const options = {kind, blocked: problem.advice === 'blocked'};
    // blockedAs tells us a resource was stopped, not which extension did it.
    if (options.blocked) return extensionUI.detectBrowserExtension('', {...options, logs: []});
    const logs = extensionSeen.filter(row => Date.now() - row.at < 10000).map(row => row.text);
    return extensionUI.detectBrowserExtension(problem.error || problem.text, {...options, logs})
        || extensionUI.detectBrowserExtension(problem.error || problem.text, options);
}

const distinct = problems => problems.filter((problem, index) => problems.findIndex(other => other.text === problem.text) === index);
let card = null, lastProblems = [];
function details(problems) {
    const cache = problems.flatMap(p => p.cache || []);
    const checks = problems.flatMap(p => p.checks || []);
    const before = retried && Array.isArray(retryRecord.before) ? retryRecord.before : [];
    return [`FreeVideo page check · ${new Date().toISOString()}`,
        `Page: ${location.origin}${location.pathname}`,
        `Browser: ${navigator.userAgent}`,
        `ComfyUI frontend: ${window.__COMFYUI_FRONTEND_VERSION__ || 'unknown'}`,
        `FreeVideo UI: ${JSON.stringify(window.FreeVideoUI || {})}`,
        `Automatic reload: ${retried ? new Date(Number(retryRecord.at)).toISOString() : retryRecord ? 'not needed' : 'unavailable (no session storage)'}`,
        '', 'Problems:', ...problems.flatMap(p => [`- ${p.text}`, ...(p.cover ? [`  covered by: ${p.cover}`] : []), ...(p.state ? [`  state: ${p.state}`] : [])]),
        ...(checks.length ? ['', 'Load check:', ...checks.map(row => `- ${row}`)] : []),
        ...(cache.length ? ['', 'Browser cache:', ...cache.map(row => `- ${row}`)] : []),
        ...(before.length ? ['', 'Browser cache before the automatic reload:', ...before.map(row => `- ${row}`)] : []),
        '', 'Errors seen while loading:', ...(seen.length ? seen.map(row => `- [${row.kind}] ${row.text}`) : ['- none'])].join('\n');
}

// "Report a problem": the feedback form with the failed checks in it. This card runs when other files
// fail to load, so it builds the address itself (no shared module) and removes places and addresses.
const ISSUE_FORM = 'https://github.com/FlashML-org/FreeVideo/issues/new';
const forgetPlaces = text => String(text)
    .replace(/https?:\/\/[^\s'"<>)]+/g, '<url>')
    .replace(/\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b/g, '<address>')
    .replace(/(?:[A-Za-z]:[\\/]|\\\\|~[\\/])[^'"<>|\r\n]+/g, '<path>')
    .replace(/(^|[\s('"])\/[\w.-]+(?:\/[^\s'"<>)]*)+/g, '$1<path>');
function issueAddress(problems) {
    const summary = forgetPlaces(details(problems)).split('\n').filter(line => !line.startsWith('Page:')).join('\n');
    const feedback = ['FreeVideo showed an error.', 'Where: Page check',
        `Error: ${forgetPlaces(problems[0]?.text || '').slice(0, 300)}`].join('\n');
    const build = text => `${ISSUE_FORM}?${new URLSearchParams({template: 'feedback.yml', title: '[Feedback] Page check',
        feedback, diagnostics: text})}`;
    let text = summary.slice(0, 2500);
    while (build(text).length > 7000 && text) text = text.slice(0, Math.floor(text.length * 0.8));
    return build(text);
}

// The automatic reload: fresh copies into the HTTP cache, then the address the
// page was opened with, before launcher.js removed its launch flag. Reported to
// the launcher only if it fails again, so its card does not flash.
async function reloadOnce(problems) {
    if (!retryRecord || retried || touched) return false;
    try {
        sessionStorage.setItem(RETRY_KEY, JSON.stringify({at: Date.now(), before: problems.flatMap(p => p.cache || [])}));
    } catch { return false; }
    await Promise.all(MODULES.map(name => fetch(new URL(name, base), {cache: 'reload'}).catch(() => null)));
    if (touched) return false;
    location.replace(performance.getEntriesByType('navigation')[0]?.name || location.href);
    return true;
}

// Curly single quotes from a Latin face where Segoe UI is missing, as theme.css does: the CJK faces after it draw
// them full width. Added from here because theme.css may be one of the files that failed.
let quoteFaces = false;
function addQuoteFaces() {
    if (quoteFaces || !globalThis.FontFace || !document.fonts) return;
    quoteFaces = true;
    for (const [weight, names] of [['100 500', ['Noto Sans Regular', 'NotoSans-Regular', 'DejaVu Sans', 'Liberation Sans']],
        ['600 900', ['Noto Sans Bold', 'NotoSans-Bold', 'DejaVu Sans Bold', 'Liberation Sans Bold']]]) {
        try { document.fonts.add(new FontFace('FreeVideo Quotes', names.map(name => `local("${name}")`).join(), {weight, unicodeRange: 'U+2018-2019'})); }
        catch { /* The card still shows in the fonts the browser has. */ }
    }
}

function show(problems) {
    lastProblems = problems;
    addQuoteFaces();
    card?.remove();
    card = document.createElement('section');
    card.setAttribute('role', 'alert'); card.className = 'fv-health';
    Object.assign(card.style, {position: 'fixed', top: '16px', left: '50%', transform: 'translateX(-50%)', zIndex: '2147483000',
        width: 'min(600px, calc(100vw - 32px))', maxHeight: 'calc(100vh - 32px)', overflow: 'auto', boxSizing: 'border-box',
        padding: '18px 20px 16px', borderRadius: '12px', background: '#1a222e', color: '#e7edf7',
        border: '1px solid #5a3440', boxShadow: '0 18px 48px rgba(0,0,0,.45)',
        font: '13px/1.55 "Segoe UI","FreeVideo Quotes","Microsoft YaHei UI","Noto Sans CJK SC","Noto Sans SC",system-ui,sans-serif'});
    const title = document.createElement('div');
    const extension = problems.map(extensionProblem).find(Boolean);
    const extensionAdvice = extension && extensionUI.browserExtensionAdvice(extension, t,
        {blocked: problems.some(p => p.advice === 'blocked')});
    title.textContent = extensionAdvice?.title || t('FreeVideo did not load completely', 'FreeVideo 没有完整加载');
    Object.assign(title.style, {color: '#f09aa2', fontWeight: '600', fontSize: '16px', marginBottom: '8px'});
    const list = document.createElement('ul');
    Object.assign(list.style, {margin: '0 0 10px', paddingLeft: '18px'});
    for (const problem of problems) {
        const row = document.createElement('li'); row.textContent = problem.text;
        Object.assign(row.style, {margin: '2px 0', overflowWrap: 'anywhere', textWrap: 'balance'});
        list.append(row);
    }
    const advice = document.createElement('div');
    const adviceText = [...new Set(problems.filter(p => !extensionProblem(p)).map(p => ADVICE[p.advice] || ADVICE.reload))].join(' ');
    for (const [index, text] of adviceText.split('GitHub issue').entries()) {
        if (index) {
            const link = document.createElement('a');
            link.textContent = 'GitHub issue'; link.href = 'https://github.com/FlashML-org/FreeVideo/issues';
            link.target = '_blank'; link.rel = 'noopener noreferrer';
            Object.assign(link.style, {color: '#6db8fa', textDecoration: 'underline', font: 'inherit'});
            advice.append(link);
        }
        advice.append(document.createTextNode(text));
    }
    Object.assign(advice.style, {color: '#a5b3c6', marginBottom: '14px', textWrap: 'pretty'});
    const actions = document.createElement('div');
    Object.assign(actions.style, {display: 'flex', flexWrap: 'wrap', gap: '8px'});
    const action = (label, run, primary = false) => {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = label; button.onclick = run;
        Object.assign(button.style, {height: '32px', padding: '0 14px', borderRadius: '8px', cursor: 'pointer', font: 'inherit',
            border: primary ? '0' : '1px solid #2a3443', background: primary ? '#6db8fa' : '#212a37', color: primary ? '#0d1520' : '#e7edf7',
            fontWeight: primary ? '600' : '400'});
        actions.append(button); return button;
    };
    action(t('Reload page', '刷新页面'), () => location.reload(), true);
    // The extension card's steps end with a reload; its copy button matches the other error cards.
    const copy = action(extensionAdvice ? t('Copy error', '复制错误') : t('Copy details', '复制详情'), async () => {
        const text = details(problems);
        try { await navigator.clipboard.writeText(text); } catch {
            const area = document.createElement('textarea'); area.value = text; document.body.append(area); area.select();
            try { document.execCommand('copy'); } catch { /* The details remain in the console. */ } area.remove();
        }
        copy.textContent = t('Copied', '已复制');
    });
    action(t('Report a problem', '提交问题'), () => window.open(issueAddress(problems), '_blank', 'noopener'));
    action(t('Close', '关闭'), () => { card?.remove(); card = null; });
    if (extensionAdvice) {
        const first = document.createElement('p'); first.textContent = extensionAdvice.summary;
        const choose = document.createElement('p'); choose.textContent = extensionAdvice.action;
        const technical = document.createElement('details'), label = document.createElement('summary');
        label.textContent = t('Technical details', '技术详情'); technical.append(label, list);
        technical.style.margin = '12px 0';
        // Full URLs/IDs and the blocked filename remain in technical details.
        card.append(title, first, choose, extensionUI.extensionSteps(extensionAdvice), technical, advice, actions);
    } else card.append(title, list, advice, actions);
    document.body.append(card);
    if (typeof card.showPopover === 'function') {
        card.popover = 'manual';
        Object.assign(card.style, {right: 'auto', bottom: 'auto', margin: '0'});
        raise();
    }
    consoleError.call(console, '[FreeVideo] Page check:\n' + details(problems));
}
// A modal such as an unstyled workspace sits in the top layer; a manual
// popover joins it and returns to the front whenever a dialog opens. Not in
// front of a video watched full screen: the card waits until it is left.
function raise() {
    if (!card?.isConnected || typeof card.showPopover !== 'function' || fullscreenOver(card)) return;
    try {
        if (card.matches(':popover-open')) card.hidePopover();
        card.showPopover();
    } catch { /* Shown below the dialog instead. */ }
}
new MutationObserver(() => { if (document.querySelector('dialog[open]')) raise(); })
    .observe(document.documentElement, {subtree: true, attributeFilter: ['open']});
for (const name of ['fullscreenchange', 'webkitfullscreenchange']) document.addEventListener(name, raise);

let sent = '', latest = null;
const comfyApi = () => comfy()?.api || window.comfyAPI?.api?.api;
function send(state, problems) {
    // Kept with diagnostic reports, so a report from this computer names the cause.
    // 'loading' tells ComfyUI this page runs FreeVideo's scripts before the check ends.
    latest = {state, problems};
    if (state === 'error' && document.hidden) return;
    const body = JSON.stringify({state, problems: problems.map(p => p.text), errors: seen.map(row => `[${row.kind}] ${row.text}`),
        ready: !!window.FreeVideoUI?.ready, launch: launchVisit, view: preferredView() || '',
        agent: navigator.userAgent, frontend: String(window.__COMFYUI_FRONTEND_VERSION__ || ''), page: location.pathname,
        client: String(comfyApi()?.clientId || '')});
    if (body === sent) return;
    sent = body;
    fetch(new URL('../../api/freevideo/launcher/ui', base), {method: 'POST', headers: {'Content-Type': 'application/json'}, body, keepalive: true})
        .catch(() => { /* An older or stopped server; the card on the page still shows the problem. */ });
}
document.addEventListener('visibilitychange', async () => {
    if (document.hidden) return;
    visibleAt = Date.now();
    if (latest?.state !== 'error') return;
    await waitForVisible();
    if (!document.hidden && latest?.state === 'error') send(latest.state, latest.problems);
});

async function check() {
    [extensionUI, errorUI] = await Promise.all([import('./browser_extension.js').catch(() => null),
        import('./error_panel.js').catch(() => null)]);
    send('loading', []);
    const app = await waitFor(comfy, 60000);
    if (!app) {
        const problems = [{text: t('The ComfyUI page did not finish loading within a minute.', 'ComfyUI 页面在一分钟内没有加载完成。'), advice: 'browser'}];
        show(problems); send('error', problems); return;
    }
    // A restarted ComfyUI has not heard from this page; tell it again.
    comfyApi()?.addEventListener?.('reconnected', () => { if (latest) { sent = ''; send(latest.state, latest.problems); } });
    // freevideo.js marks itself ready in its setup; launcher.js reports once
    // the workflow is open and in front.
    await waitFor(() => window.FreeVideoUI?.ready, 10000);
    const launched = launchVisit ? await waitFor(() => reports.find(r => r.type === 'launch'), 30000) : null;
    // Opening the workspace waits for an animation frame.
    await sleep(1500);
    const problems = [];
    if (!window.FreeVideoUI?.ready) {
        problems.push(...await diagnoseModules());
        if (problems.length && problems.every(p => p.retry && !extensionProblem(p)) && await reloadOnce(problems)) return;
    }
    // Every file loads, so ComfyUI is most likely still working through other
    // extensions: it starts them one after another, and a slow one ahead of
    // FreeVideo's delays it. Give it the rest of the minute before naming it.
    if (!window.FreeVideoUI?.ready && !problems.length) await waitFor(() => window.FreeVideoUI?.ready, 50000);
    const notStarted = {text: t('FreeVideo’s scripts loaded, but ComfyUI did not start them.', 'FreeVideo 的脚本已下载，但 ComfyUI 没有启动它们。'), advice: 'reload'};
    if (!window.FreeVideoUI?.ready && !problems.length) {
        problems.push(notStarted);
        // A start later than that clears the card instead of leaving it up.
        const timer = setInterval(() => {
            if (!window.FreeVideoUI?.ready) return;
            clearInterval(timer);
            if (!card || !lastProblems.length || !lastProblems.every(p => p === notStarted || p.advice === 'plugin')) return;
            card.remove(); card = null; lastProblems = [];
            send('ready', []);
        }, 1000);
    }
    if (!problems.length) problems.push(...await diagnoseStyles());
    let checkStudio = false;
    if (launchVisit && !problems.length) {
        if (!launched) problems.push({text: t('The FreeVideo workflow did not open: launcher.js did not report back.', 'FreeVideo 工作流没有打开：launcher.js 没有响应。'), advice: 'reload'});
        else if (launched.error) problems.push(reportText(launched));
        else if (!studioWorkflow(app.graph)) problems.push({text: t('ComfyUI shows another workflow; the FreeVideo workflow is in another tab.', 'ComfyUI 显示的是另一个工作流，FreeVideo 工作流在别的标签页里。'), advice: 'relaunch'});
        else checkStudio = true;
    }
    for (const item of reports) if (item.type === 'error') problems.push(reportText(item));
    const copies = await freevideoFolders();
    if (copies.length > 1 || seen.some(row => /already registered/i.test(row.text) && /FreeVideo\./.test(row.text))) {
        const named = copies.map(name => `custom_nodes/${name}`).join(cn ? '、' : ', ');
        problems.push({text: copies.length > 1 ? t(`ComfyUI loaded FreeVideo more than once: ${named}.`, `ComfyUI 加载了多份 FreeVideo：${named}。`)
            : t('ComfyUI loaded FreeVideo more than once.', 'ComfyUI 加载了多份 FreeVideo。'), advice: 'duplicate'});
    }
    // Later failures, such as the workspace failing to open from a node.
    listener = item => {
        if (item.type !== 'error') return;
        const problems = distinct([...lastProblems, reportText(item)]);
        show(problems); send('error', problems);
    };
    if (document.hidden && problems.length) {
        // File and extension diagnostics still run at startup. Only their
        // launcher error status waits for the page to become visible.
        const found = distinct([...problems, ...pluginErrors()]);
        show(found); send('error', found);
    }
    await waitForVisible();
    if (checkStudio && studioWorkflow(app.graph) && preferredView() !== 'nodes' && !['dialog.fv-workspace-opening[open]', 'dialog.fv-editor[open]', 'dialog.fv-studio[open]'].some(selector => document.querySelector(selector))
        && !reports.some(item => item.type === 'error' && item.where === 'studio'))
        problems.unshift({text: t('The creative workspace did not open.', '创作面板没有打开。'), advice: 'reload'});
    problems.push(...lastProblems);
    // Started while this check was waiting for the page to be in front.
    if (window.FreeVideoUI?.ready && problems.includes(notStarted)) problems.splice(problems.indexOf(notStarted), 1);
    if (problems.length) {
        problems.push(...pluginErrors());
        show(distinct(problems)); send('error', distinct(problems));
    } else {
        send('ready', []);
    }
    watchWorkspace(app).catch(error => consoleError.call(console, '[FreeVideo] Workspace check failed:', error));
}

// A second FreeVideo folder loads this file again; one check covers both.
if (!window.__freevideoHealthStarted) {
    window.__freevideoHealthStarted = true;
    install();
    check().catch(error => consoleError.call(console, '[FreeVideo] Page check failed:', error));
}
