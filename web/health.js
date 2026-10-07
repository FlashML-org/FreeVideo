// Checks that FreeVideo's page loaded completely and says what is wrong when
// it did not: which file, why, and what to do. It imports nothing and styles
// itself inline, so it still runs when every other FreeVideo file fails.
// Other FreeVideo files report through `window.__freevideoHealth.push(...)`.
const base = new URL('./', import.meta.url);
const query = new URLSearchParams(location.search);
const language = query.get('freevideo_lang');
const cn = language === 'zh' || (language !== 'en' && String(navigator.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;
// launcher.js removes this flag during setup; read it before that.
const launchVisit = query.get('freevideo') === 'launch';
// Every script this folder ships; a test keeps the list in step with web/*.js.
export const MODULES = ['branding.js', 'compatibility.js', 'error_panel.js', 'freevideo.js', 'generation_progress.js',
    'health.js', 'launcher.js', 'library.js', 'motion.js', 'output_download.js', 'preview_scene.js',
    'progress_connection.js', 'prompt_draft.js', 'prompt_enhance.js', 'prompt_references.js', 'report_issue.js',
    'result_actions.js', 'sampling_effort.js', 'setup.js', 'share.js', 'studio.js', 'studio_queue.js',
    'updates.js', 'view_navigation.js'];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const fileName = url => decodeURIComponent(String(url).split(/[?#]/)[0].split('/').pop() || String(url));
const ours = url => typeof url === 'string' && url.startsWith(base.href);

// Errors seen while the page loads: failed files, and console errors from
// ComfyUI that name the extension or file that failed.
const seen = [];
const note = (kind, text, extra = {}) => { if (seen.length < 60) seen.push({kind, text: String(text).slice(0, 500), ...extra}); };
const describe = value => value instanceof Error ? `${value.name}: ${value.message}`
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
            if (/Error loading extension|Error calling extension|already registered|Failed to fetch dynamically imported module|does not provide an export/i.test(text)
                || text.includes(base.pathname)) note('console', text);
        } catch { /* Never interfere with the page's own logging. */ }
        return consoleError.apply(this, args);
    };
    // Files that loaded first queued their reports in an array.
    const queued = Array.isArray(window.__freevideoHealth) ? window.__freevideoHealth : [];
    window.__freevideoHealth = {push: receive};
    for (const item of queued) receive(item);
}

const comfy = () => window.app?.graph ? window.app : window.comfyAPI?.app?.app?.graph ? window.comfyAPI.app.app : null;
const studioWorkflow = graph => (graph?._nodes || graph?.nodes || []).some(n => n.comfyClass === 'FreeVideoGenerate' || n.type === 'FreeVideoGenerate')
    && !!graph?.extra?.freevideo_studio;
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
        return {status: 0, ok: false, type: '', error: describe(error)};
    }
}

const ADVICE = {
    files: t('FreeVideo’s page files are missing or unreadable. In the FreeVideo launcher, open Settings and click “Install / repair”, then open FreeVideo again.',
        'FreeVideo 的网页文件缺失或无法读取。请在 FreeVideo 启动器的“设置”里点击“安装 / 修复”，完成后重新打开。'),
    type: t('ComfyUI sent the file with the wrong type, so the browser refused it. Restart ComfyUI; if it persists, copy the details and send them to us.',
        'ComfyUI 发给浏览器的文件类型不对，浏览器拒绝使用。请重启 ComfyUI；仍然出现时，复制详情发给我们。'),
    browser: t('This browser cannot run FreeVideo. Open the same address in a current Chrome or Edge.',
        '当前浏览器无法运行 FreeVideo。请用最新版 Chrome 或 Edge 打开同一个地址。'),
    mixed: t('The browser mixed files from two FreeVideo versions. Press Ctrl+F5 to reload without the cache.',
        '浏览器混用了新旧两个版本的 FreeVideo 文件。请按 Ctrl+F5 强制刷新。'),
    duplicate: t('Keep custom_nodes/FreeVideo, move the other FreeVideo folders out of custom_nodes, then restart ComfyUI.',
        '请保留 custom_nodes/FreeVideo，把其他 FreeVideo 文件夹移出 custom_nodes，然后重启 ComfyUI。'),
    plugin: t('Another ComfyUI extension reported an error at the same time. Turn it off in ComfyUI Settings › Extensions, then reload.',
        '同时有其他 ComfyUI 插件报错。可在 ComfyUI“设置 › 扩展”里暂时停用它，然后刷新页面。'),
    reload: t('Reload the page. If it happens again, copy the details and send them to us.',
        '请刷新页面。仍然出现时，复制详情发给我们。'),
    relaunch: t('Click Start in the FreeVideo launcher again.', '请在 FreeVideo 启动器里重新点击“启动”。'),
};

// One sentence per finding, naming the file and the reason.
async function diagnoseModules() {
    const found = [];
    for (const name of MODULES) {
        const url = new URL(name, base).href, result = await probe(url);
        if (!result.ok) {
            found.push({text: result.status
                ? t(`Script ${name} could not be loaded: the server answered HTTP ${result.status}.`, `脚本 ${name} 无法加载：服务器返回 HTTP ${result.status}。`)
                : t(`Script ${name} could not be loaded: ${result.error}.`, `脚本 ${name} 无法加载：${result.error}。`), advice: 'files'});
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
    for (const [message, names] of failed) {
        const list = names.length > 3 ? `${names.slice(0, 3).join(cn ? '、' : ', ')}${t(` and ${names.length - 3} more`, ` 等 ${names.length} 个文件`)}` : names.join(cn ? '、' : ', ');
        found.push({text: t(`The browser could not run ${list}: ${message}`, `浏览器无法运行 ${list}：${message}`),
            advice: /does not provide an export/.test(message) ? 'mixed'
                : /SyntaxError|Unexpected token|Unexpected identifier|is not a function|is not defined|is not a constructor/.test(message) ? 'browser' : 'reload'});
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
                `样式文件 ${name} 无法加载：${result.status ? `HTTP ${result.status}` : result.error}。`), advice: 'files'}
            : result.type !== 'text/css'
                ? {text: t(`Stylesheet ${name} was refused: the server sent it as “${result.type || 'no type'}”.`,
                    `样式文件 ${name} 被浏览器拒绝：服务器发送的类型是“${result.type || '未标明'}”。`), advice: 'type'}
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

function reportText(item) {
    const where = {studio: t('The creative workspace could not open', '创作面板打不开'),
        navigation: t('The FreeVideo toolbar could not be added', 'FreeVideo 工具栏没有加载'),
        progress: t('Live progress could not start', '生成进度无法更新'),
        updates: t('Update checks could not start', '更新检查无法启动'),
        launch: t('The FreeVideo workflow did not open', 'FreeVideo 工作流没有打开')}[item.where] || t('FreeVideo reported an error', 'FreeVideo 报错');
    return {text: `${where}${cn ? '：' : ': '}${describe(item.error)}`, advice: item.where === 'launch' ? 'relaunch' : 'reload'};
}

const distinct = problems => problems.filter((problem, index) => problems.findIndex(other => other.text === problem.text) === index);
let card = null, lastProblems = [];
function details(problems) {
    return [`FreeVideo page check · ${new Date().toISOString()}`,
        `Page: ${location.origin}${location.pathname}`,
        `Browser: ${navigator.userAgent}`,
        `ComfyUI frontend: ${window.__COMFYUI_FRONTEND_VERSION__ || 'unknown'}`,
        `FreeVideo UI: ${JSON.stringify(window.FreeVideoUI || {})}`,
        '', 'Problems:', ...problems.map(p => `- ${p.text}`),
        '', 'Errors seen while loading:', ...(seen.length ? seen.map(row => `- [${row.kind}] ${row.text}`) : ['- none'])].join('\n');
}

function show(problems) {
    lastProblems = problems;
    card?.remove();
    card = document.createElement('section');
    card.setAttribute('role', 'alert'); card.className = 'fv-health';
    Object.assign(card.style, {position: 'fixed', top: '16px', left: '50%', transform: 'translateX(-50%)', zIndex: '2147483000',
        width: 'min(600px, calc(100vw - 32px))', maxHeight: 'calc(100vh - 32px)', overflow: 'auto', boxSizing: 'border-box',
        padding: '18px 20px 16px', borderRadius: '12px', background: '#1a222e', color: '#e7edf7',
        border: '1px solid #5a3440', boxShadow: '0 18px 48px rgba(0,0,0,.45)',
        font: '13px/1.55 "Segoe UI","Microsoft YaHei UI","Noto Sans CJK SC","Noto Sans SC",system-ui,sans-serif'});
    const title = document.createElement('div');
    title.textContent = t('FreeVideo did not load completely', 'FreeVideo 没有完整加载');
    Object.assign(title.style, {color: '#f09aa2', fontWeight: '600', fontSize: '16px', marginBottom: '8px'});
    const list = document.createElement('ul');
    Object.assign(list.style, {margin: '0 0 10px', paddingLeft: '18px'});
    for (const problem of problems) {
        const row = document.createElement('li'); row.textContent = problem.text;
        Object.assign(row.style, {margin: '2px 0', overflowWrap: 'anywhere'});
        list.append(row);
    }
    const advice = document.createElement('div');
    advice.textContent = [...new Set(problems.map(p => ADVICE[p.advice] || ADVICE.reload))].join(' ');
    Object.assign(advice.style, {color: '#a5b3c6', marginBottom: '14px'});
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
    const copy = action(t('Copy details', '复制详情'), async () => {
        const text = details(problems);
        try { await navigator.clipboard.writeText(text); } catch {
            const area = document.createElement('textarea'); area.value = text; document.body.append(area); area.select();
            try { document.execCommand('copy'); } catch { /* The details remain in the console. */ } area.remove();
        }
        copy.textContent = t('Copied', '已复制');
    });
    action(t('Close', '关闭'), () => { card?.remove(); card = null; });
    card.append(title, list, advice, actions);
    document.body.append(card);
    // A modal such as an unstyled workspace sits in the top layer; a manual
    // popover joins it and returns to the front whenever a dialog opens.
    if (typeof card.showPopover === 'function') {
        card.popover = 'manual';
        Object.assign(card.style, {right: 'auto', bottom: 'auto', margin: '0'});
        card.showPopover();
    }
    consoleError.call(console, '[FreeVideo] Page check:\n' + details(problems));
}
new MutationObserver(() => {
    if (!card?.isConnected || typeof card.showPopover !== 'function' || !document.querySelector('dialog[open]')) return;
    try { card.hidePopover(); card.showPopover(); } catch { /* Shown below the dialog instead. */ }
}).observe(document.documentElement, {subtree: true, attributeFilter: ['open']});

let sent = '';
function send(state, problems) {
    // Kept with diagnostic reports, so a report from this computer names the cause.
    const body = JSON.stringify({state, problems: problems.map(p => p.text), errors: seen.map(row => `[${row.kind}] ${row.text}`),
        ready: !!window.FreeVideoUI?.ready, launch: launchVisit, view: preferredView() || '',
        agent: navigator.userAgent, frontend: String(window.__COMFYUI_FRONTEND_VERSION__ || ''), page: location.pathname});
    if (body === sent) return;
    sent = body;
    fetch(new URL('../../api/freevideo/launcher/ui', base), {method: 'POST', headers: {'Content-Type': 'application/json'}, body, keepalive: true})
        .catch(() => { /* An older or stopped server; the card on the page still shows the problem. */ });
}

async function check() {
    const app = await waitFor(comfy, 60000);
    if (!app) {
        const problems = [{text: t('The ComfyUI page did not finish loading within a minute.', 'ComfyUI 页面在一分钟内没有加载完成。'), advice: 'browser'}];
        show(problems); send('error', problems); return;
    }
    // freevideo.js marks itself ready in its setup; launcher.js reports once
    // the workflow is open and in front.
    await waitFor(() => window.FreeVideoUI?.ready, 10000);
    const launched = launchVisit ? await waitFor(() => reports.find(r => r.type === 'launch'), 30000) : null;
    // Opening the workspace waits for an animation frame.
    await sleep(1500);
    const problems = [];
    const ui = window.FreeVideoUI || {};
    if (!ui.ready) problems.push(...await diagnoseModules());
    if (!ui.ready && !problems.length) problems.push({text: t('FreeVideo’s scripts loaded, but ComfyUI did not start them.', 'FreeVideo 的脚本已下载，但 ComfyUI 没有启动它们。'), advice: 'reload'});
    if (!problems.length) problems.push(...await diagnoseStyles());
    if (launchVisit && !problems.length) {
        if (!launched) problems.push({text: t('The FreeVideo workflow did not open: launcher.js did not report back.', 'FreeVideo 工作流没有打开：launcher.js 没有响应。'), advice: 'reload'});
        else if (launched.error) problems.push(reportText(launched));
        else if (!studioWorkflow(app.graph)) problems.push({text: t('ComfyUI shows another workflow; the FreeVideo workflow is in another tab.', 'ComfyUI 显示的是另一个工作流，FreeVideo 工作流在别的标签页里。'), advice: 'relaunch'});
        else if (preferredView() !== 'nodes' && !document.querySelector('dialog.fv-studio[open]')
            && !reports.some(item => item.type === 'error' && item.where === 'studio'))
            problems.push({text: t('The creative workspace did not open.', '创作面板没有打开。'), advice: 'reload'});
    }
    for (const item of reports) if (item.type === 'error') problems.push(reportText(item));
    const copies = await freevideoFolders();
    if (copies.length > 1 || seen.some(row => /already registered/i.test(row.text) && /FreeVideo\./.test(row.text))) {
        const named = copies.map(name => `custom_nodes/${name}`).join(cn ? '、' : ', ');
        problems.push({text: copies.length > 1 ? t(`ComfyUI loaded FreeVideo more than once: ${named}.`, `ComfyUI 加载了多份 FreeVideo：${named}。`)
            : t('ComfyUI loaded FreeVideo more than once.', 'ComfyUI 加载了多份 FreeVideo。'), advice: 'duplicate'});
    }
    if (problems.length) {
        problems.push(...pluginErrors());
        show(distinct(problems)); send('error', distinct(problems));
    } else {
        send('ready', []);
    }
    // Later failures, such as the workspace failing to open from a node.
    listener = item => {
        if (item.type !== 'error') return;
        const problems = distinct([...lastProblems, reportText(item)]);
        show(problems); send('error', problems);
    };
}

// A second FreeVideo folder loads this file again; one check covers both.
if (!window.__freevideoHealthStarted) {
    window.__freevideoHealthStarted = true;
    install();
    check().catch(error => consoleError.call(console, '[FreeVideo] Page check failed:', error));
}
