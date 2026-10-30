// Evidence and copy shared by every FreeVideo error card. No notice is shown
// merely because an extension is installed or fetch has been wrapped.
// Store listings checked 2026-10-10. Firefox UUIDs and Safari identifiers are
// deliberately not mapped: they do not identify a store installation.
const chrome = (id, name) => ({id, name, source: `https://chromewebstore.google.com/detail/${id}`});
const edge = (id, name) => ({id, name, source: `https://microsoftedge.microsoft.com/addons/detail/${id}`});
export const KNOWN_EXTENSIONS = Object.freeze([
    chrome('ndcooeababalnlpkfedmmbbbgkljhpjf', ['ScriptCat', '脚本猫 ScriptCat']),
    edge('liilgpjgabokdklappibcjfablkpcekh', ['ScriptCat', '脚本猫 ScriptCat']),
    chrome('dhdgffkkebhmkfjojejmpbldmpobfkfo', ['Tampermonkey', 'Tampermonkey']),
    edge('iikmkjmpaadaobahmlepeloendndfphd', ['Tampermonkey', 'Tampermonkey']),
    chrome('gcalenpjmijncebpfijmoaglllgpjagf', ['Tampermonkey BETA', 'Tampermonkey BETA']),
    edge('fcmfnpggmnlmfebfghbfnillijihnkoh', ['Tampermonkey BETA', 'Tampermonkey BETA']),
    chrome('jinjaccalgkegednnccohejagnlnfdag', ['Violentmonkey', 'Violentmonkey']),
    edge('eeagobfjdenkkddmbclomhiblgggliao', ['Violentmonkey', 'Violentmonkey']),
    chrome('jfedfbgedapdagkghmgibemcoggfppbb', ['cat-catch', '猫抓']),
    edge('oohmdefbjalncfplafanlagojlakmjci', ['cat-catch', '猫抓']),
    chrome('cjpalhdlnbpafiamejdnhcphjbkeiagm', ['uBlock Origin', 'uBlock Origin']),
    edge('odfafepnkmbhccpbejgmiehpchacaeak', ['uBlock Origin', 'uBlock Origin']),
    chrome('ddkjiahejlhfcafbddmgiahcphecmpfh', ['uBlock Origin Lite', 'uBlock Origin Lite']),
    edge('cimighlppcgcoapaliogpjjdehbnofhn', ['uBlock Origin Lite', 'uBlock Origin Lite']),
    chrome('bgnkhhnnamicmpeenaelnjfhikgbkllg', ['AdGuard', 'AdGuard']),
    edge('pdffkfellgipmhklpdmokmckkkfcopbh', ['AdGuard', 'AdGuard']),
    chrome('gighmmpiobklfepjocnamgkkbiglidom', ['AdBlock', 'AdBlock']),
    edge('ndcileolkflehcjpmjnfbnaibdcgglog', ['AdBlock', 'AdBlock']),
    chrome('cfhdojbkjhnklbpkdaibdccddilifddb', ['Adblock Plus', 'Adblock Plus']),
    edge('gmgoamodcdcjnbaobigkjelfplakmdhh', ['Adblock Plus', 'Adblock Plus']),
]);
const byId = new Map(KNOWN_EXTENSIONS.map(row => [row.id, row]));
const marker = /(?:chrome|moz|safari-web)-extension:\/\/|\.user\.js\b|ERR_BLOCKED_BY_CLIENT/i;
const string = value => typeof value === 'string' ? value : '';
export function extensionErrorText(error, seen = new WeakSet()) {
    if (typeof error === 'string') return error;
    if (!error || typeof error !== 'object') return '';
    if (seen.has(error)) return '';
    seen.add(error);
    // Do not search workflow/input objects or arbitrary user text for evidence.
    return [error.name, error.message, error.stack, error.details, error.error_details,
        error.exception_type, error.exception_message,
        ...(Array.isArray(error.traceback) ? error.traceback : [error.traceback]),
        extensionErrorText(error.error, seen), extensionErrorText(error.response, seen), extensionErrorText(error.cause, seen)]
        .map(string).filter(Boolean).join('\n');
}
export function isNetworkTypeError(error) {
    const type = error?.name || error?.exception_type;
    const text = typeof error === 'string' ? error : string(error?.message) || string(error?.exception_message);
    return (type === 'TypeError' || /^TypeError:\s*/i.test(text))
        && /Failed to fetch|fetch failed|NetworkError(?: when attempting to fetch resource)?|network request failed|Load failed/i.test(text);
}
// One sentence for a dropped connection (engine closed, network lost); the raw
// browser wording stays in the technical details.
export function connectionLostText(t) { return t('Lost the connection to FreeVideo. Try again once it is back.', '与 FreeVideo 的连接已断开，恢复后请重试。'); }
export function nativeFetch(fetchFunction = globalThis.fetch) {
    try { return typeof fetchFunction === 'function' && /\[native code\]/.test(Function.prototype.toString.call(fetchFunction)); }
    catch { return false; }
}
const nativeXHR = () => {
    try {
        return ['open', 'send'].every(method => /\[native code\]/.test(Function.prototype.toString.call(XMLHttpRequest.prototype[method])));
    } catch { return false; }
};
export function userScriptName(text) {
    const file = [...String(text).matchAll(/[^\s"'<>()[\]]+/g)]
        .map(match => match[0].split(/[?#]/)[0].replace(/:\d+(?::\d+)?$/, '').split(/[/\\]/).pop())
        .find(name => /^.+\.user\.js$/i.test(name));
    if (!file) return null;
    let decoded = file;
    try { decoded = decodeURIComponent(file); } catch { /* A malformed escape remains literal. */ }
    // Count Unicode characters, without splitting surrogate pairs.
    const chars = Array.from(decoded);
    return chars.length > 40 ? chars.slice(0, 20).join('') + '…' + chars.slice(-19).join('') : decoded;
}

// Keep only recent extension-related console errors. No normal logging or user
// data is retained, and an old failure must not label a later network outage.
const consoleEvidence = [];
let listening = false;
export function installExtensionEvidence() {
    if (listening || typeof window === 'undefined') return;
    listening = true;
    const original = console.error;
    console.error = function (...args) {
        try {
            const text = args.map(value => extensionErrorText(value)).join('\n');
            if (marker.test(text)) {
                consoleEvidence.push({at: Date.now(), text: text.slice(0, 8192)});
                if (consoleEvidence.length > 20) consoleEvidence.shift();
            }
        } catch { /* Never break logging. */ }
        return original.apply(this, args);
    };
}
// An extension frame or a user script, as a stack line names it.
const extensionFrame = /(?:chrome|moz|safari-web)-extension:\/\/|\.user\.js\b/i;
// The frame that names the culprit: a user script inside an extension first.
const namingFrame = text => {
    const lines = text.split(/\r?\n/);
    return lines.find(line => /chrome-extension:\/\/[a-p]{32}\//i.test(line) && /\.user\.js\b/i.test(line))
        || lines.find(line => /chrome-extension:\/\/[a-p]{32}\//i.test(line))
        || lines.find(line => extensionFrame.test(line)) || null;
};
// `scrub` removes text the request itself supplied (prompt, inputs) before the
// search, so an echoed input is never evidence.
export function detectBrowserExtension(error, {kind = 'unknown', known = null, blocked = false,
    logs = consoleEvidence.filter(row => Date.now() - row.at < 10000).map(row => row.text),
    fetchFunction = globalThis.fetch, scrub = text => text} = {}) {
    if (known || !['unknown', 'network', 'blocked'].includes(kind)) return null;
    const ownText = scrub(extensionErrorText(error));
    const logText = logs.map(value => scrub(extensionErrorText(value))).join('\n');
    const text = [ownText, logText].join('\n');
    const evidence = [];
    if (extensionFrame.test(text)) evidence.push('extension-stack');
    if (blocked || /ERR_BLOCKED_BY_CLIENT/i.test(text)) evidence.push('blocked-resource');
    if (isNetworkTypeError(error) && !nativeFetch(fetchFunction)) evidence.push('wrapped-fetch');
    if (!evidence.length) return null;
    // This error's own frame names the culprit. A recent console error is used
    // only when this error has none, and the script and extension always come
    // from one frame: never pair names from different sources.
    const frame = namingFrame(ownText) ?? namingFrame(logText);
    const id = frame?.match(/chrome-extension:\/\/([a-p]{32})\//i)?.[1]?.toLowerCase();
    return {evidence, extension: byId.get(id)?.name ?? null, script: frame ? userScriptName(frame) : null};
}

// A wrapped window.fetch alone does not say that the request was blocked: any script that
// wraps fetch makes it true, also while the engine is merely unreachable for a moment.
export const wrappedFetchOnly = result => result?.evidence?.length === 1 && result.evidence[0] === 'wrapped-fetch';

export const EXTENSION_COPY = Object.freeze({
    title: ['A browser extension is interfering with FreeVideo', '浏览器扩展干扰了 FreeVideo'],
    // English uses straight quotes: curly ones widen in the CJK font stack.
    both: ['The script "{script}" in the browser extension "{extension}" interfered with this page, so this action did not finish.',
        '浏览器扩展“{extension}”里的脚本“{script}”干扰了页面，这次操作没有完成。'],
    extension: ['The browser extension "{extension}" interfered with this page, so this action did not finish.',
        '浏览器扩展“{extension}”干扰了页面，这次操作没有完成。'],
    unknown: ['A browser extension interfered with this page, so this action did not finish.',
        '浏览器里的某个扩展干扰了页面，这次操作没有完成。'],
    // The browser says a client blocked the file: an ad-blocking extension, though not which one.
    blocked: ['An ad-blocking extension in this browser, such as uBlock Origin, AdGuard or Adblock Plus, blocked one of FreeVideo\'s page files, so the page cannot work properly.',
        '浏览器里的广告拦截类扩展（例如 uBlock Origin、AdGuard、Adblock Plus）拦下了 FreeVideo 的页面文件，页面无法正常运行。'],
    manager: ['a user script manager', '用户脚本管理器'],
    choose: ['Choose one of these, then reload the page:', '请任选一种做法，然后刷新页面：'],
    steps: [
        ['Turn the extension off for this page, or set its site access to "On click".', '在这个页面关闭这个扩展，或把它的网站访问权限改成“点击时”。'],
        ["Add this page's address {address} to the extension's exclusion list or allowlist.", '把这个页面的地址 {address} 加进扩展的排除列表或白名单。'],
        ['Open FreeVideo in a window without extensions, such as a private window, or in another browser.', '用不运行扩展的窗口（例如无痕窗口）或另一个浏览器打开 FreeVideo。'],
    ],
});
export function browserExtensionAdvice(result, t, {blocked = false, host = globalThis.location?.host || ''} = {}) {
    const extension = result.extension ? t(...result.extension) : t(...EXTENSION_COPY.manager);
    const template = result.script ? EXTENSION_COPY.both : result.extension ? EXTENSION_COPY.extension
        : blocked || result.evidence?.includes('blocked-resource') ? EXTENSION_COPY.blocked : EXTENSION_COPY.unknown;
    const summary = t(...template).replace(/\{(extension|script)\}/g, (_, key) => key === 'extension' ? extension : result.script);
    return {kind: 'browser-extension', title: t(...EXTENSION_COPY.title), summary,
        action: t(...EXTENSION_COPY.choose), steps: EXTENSION_COPY.steps.map(row => t(...row)), host};
}
export function extensionSteps(advice) {
    const list = document.createElement('ol'); list.className = 'fv-extension-steps';
    Object.assign(list.style, {paddingLeft: '24px', margin: '8px 0', lineHeight: '1.6'});
    for (const text of advice.steps) {
        const row = document.createElement('li'); row.style.margin = '8px 0';
        if (text.includes('{address}')) {
            const [before, after] = text.split('{address}');
            const code = document.createElement('code'); code.textContent = advice.host;
            Object.assign(code.style, {fontFamily: 'monospace', userSelect: 'all', background: '#ffffff12', padding: '1px 4px', borderRadius: '3px'});
            row.append(document.createTextNode(before), code, document.createTextNode(after));
        } else row.textContent = text;
        list.append(row);
    }
    return list;
}

// Only the queue controller's read-only GETs opt into this fallback. POSTs,
// including cancellation, cannot enter it. Preserve apiURL and the headers
// fetchApi adds (Comfy-User); options are passed in explicitly so mutations to
// the header object by fetchApi also survive.
// The representative sniffer can also replace XHR.open: real-browser testing
// showed both transports fail then. If open/send is wrapped, only show advice;
// this page's alternative transport is no longer a reliable fallback.
export async function fetchQueueRead(api, path, options = {}) {
    const request = {...options, headers: new Headers(options.headers)};
    try { return await api.fetchApi(path, request); }
    catch (error) {
        if ((options.method || 'GET').toUpperCase() !== 'GET' || !/^\/(?:queue(?:\?|$)|history(?:[/?]|$))/.test(path)
            || !isNetworkTypeError(error) || !detectBrowserExtension(error)
            || typeof XMLHttpRequest === 'undefined') throw error;
        if (!nativeXHR()) {
            error.details = [string(error.details), 'XMLHttpRequest fallback not attempted: open/send is wrapped by page code.'].filter(Boolean).join('\n');
            throw error;
        }
        try {
            const response = await new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open('GET', api.apiURL(path), true);
                request.headers.set('Comfy-User', api.user ?? '');
                // fetchApi defaults to no-cache; XHR has no cache option.
                if (!request.headers.has('Cache-Control')) request.headers.set('Cache-Control', options.cache === 'no-store' ? 'no-store' : 'no-cache');
                for (const [name, value] of request.headers) xhr.setRequestHeader(name, value);
                xhr.withCredentials = options.credentials === 'include';
                xhr.timeout = 10000;
                const abort = () => xhr.abort();
                const finish = () => options.signal?.removeEventListener('abort', abort);
                xhr.onload = () => {
                    finish();
                    if (!xhr.status) { reject(new TypeError('Failed to fetch with XMLHttpRequest')); return; }
                    try {
                        const headers = new Headers();
                        for (const line of xhr.getAllResponseHeaders().trim().split(/[\r\n]+/)) {
                            const colon = line.indexOf(':');
                            if (colon > 0) headers.append(line.slice(0, colon), line.slice(colon + 1).trim());
                        }
                        resolve(new Response([204, 205, 304].includes(xhr.status) ? null : xhr.responseText,
                            {status: xhr.status, statusText: xhr.statusText, headers}));
                    } catch (failure) { reject(failure); }
                };
                xhr.onerror = xhr.ontimeout = xhr.onabort = () => { finish(); reject(new TypeError('Failed to fetch with XMLHttpRequest')); };
                if (options.signal?.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
                options.signal?.addEventListener('abort', abort, {once: true});
                try { xhr.send(); } catch (failure) { finish(); reject(failure); }
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response;
        } catch (retryError) {
            // Preserve the actual extension frame in the first exception.
            // A successful fallback never publishes/logs the fetch failure.
            error.details = [string(error.details), `XMLHttpRequest retry failed: ${retryError.name}: ${retryError.message}`].filter(Boolean).join('\n');
            throw error;
        }
    }
}
installExtensionEvidence();
