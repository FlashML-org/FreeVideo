// The page's interface language, read one way for every module. The launcher passes its language in the address:
// freevideo_lang=zh for Simplified Chinese, a language file's tag for any other language (zh-Hant, pt-BR, ...), nothing
// for English; without it the browser's language decides (Chinese when it starts with "zh"). English and Simplified
// Chinese are written in the code as t(en, zh); any other language is read from web/locales/<tag>.json before the
// modules that use this one run, and a language whose file cannot be read shows as English (Simplified for zh-*).
// Each module still decides when it reads: most read once when they load, the tour and the tutorial when they open.
// No imports, and the DOM only to mark a page in a third language (data-fv-language on <html>, for its fonts in
// theme.css), so Node tests load it as they load the modules that use it.
export const LANGUAGE_PARAM = 'freevideo_lang';
export const KEY_SEPARATOR = '␟';

// The language asked for in the address, as written there ('zh', 'en', anything else), or null.
export function askedLanguage(search = globalThis.location?.search) {
    try { return new URLSearchParams(search || '').get(LANGUAGE_PARAM); } catch { return null; }
}

// Whether the page shows Chinese: the address decides when it names a language (zh, zh-Hant: yes; en, pt-BR: no),
// otherwise the browser's language.
export function isChinese(asked = askedLanguage(), browserLanguage = globalThis.navigator?.language) {
    return /^zh([-_]|$)/i.test(asked || '') || (!asked && String(browserLanguage || '').toLowerCase().startsWith('zh'));
}

const BUILT_IN = /^(en|zh|zh[-_]hans)$/i;
const TAG = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$/;

// The language file the address asks for: a tag other than English and Simplified Chinese, or null.
export function fileLanguage(asked = askedLanguage()) {
    const tag = String(asked || '').replace(/_/g, '-');
    return TAG.test(tag) && !BUILT_IN.test(tag) ? tag : null;
}

// How long the page waits for its language file before it opens as it would without one (a slow network must not keep
// the whole page blank).
export const CATALOG_WAIT_MS = 3000;

// A language file as an object of texts, or null when it cannot be read within the wait.
export async function loadCatalog(tag, base = import.meta.url, fetcher = globalThis.fetch, wait = CATALOG_WAIT_MS) {
    const stop = typeof AbortController === 'function' ? new AbortController() : null;
    let timer;
    const late = new Promise(resolve => { timer = setTimeout(() => { stop?.abort(); resolve(null); }, wait); });
    const read = (async () => {
        try {
            const response = await fetcher(new URL(`./locales/${tag}.json`, base), stop ? {signal: stop.signal} : undefined);
            if (!response.ok) return null;
            const catalog = await response.json();
            return catalog && typeof catalog === 'object' && !Array.isArray(catalog) ? catalog : null;
        } catch { return null; }
    })();
    try { return await Promise.race([read, late]); } finally { clearTimeout(timer); }
}

const wanted = fileLanguage();
// The texts of the page's language when it is neither English nor Simplified Chinese, otherwise null.
export const CATALOG = wanted && typeof globalThis.fetch === 'function' ? await loadCatalog(wanted) : null;
// The language the page shows, as a language file's tag.
export const LANGUAGE = CATALOG ? wanted : isChinese() ? 'zh-Hans' : 'en';
if (CATALOG && globalThis.document?.documentElement) globalThis.document.documentElement.dataset.fvLanguage = LANGUAGE;

// t(en, zh, values) for a catalog (null: the text written in the code) and the language the module read.
export const translatorFor = (catalog, chinese) => catalog
    ? (en, zh, values) => lookup(catalog, en, zh, values)
    : (en, zh, values) => values ? fill(chinese ? zh : en, values) : chinese ? zh : en;

// A module's t(en, zh) for the language it read.
export const translator = chinese => translatorFor(CATALOG, chinese);

// The address that opens this page in a language (Settings, the command palette, reloading into an update).
export function languageUrl(href, language) {
    const url = new URL(href);
    url.searchParams.set(LANGUAGE_PARAM, language);
    return url;
}

// A language file's text for a pair: the entry for the English and Chinese together (when the English has more than
// one meaning), then for the English alone, then the English itself. {name} placeholders are filled from values.
// Not used by the pages yet: English and Chinese are written in the code, and other languages come later. A missing
// entry falls back to English, so Simplified Chinese is never read through it: its text is the zh in the code.
// A file whose _meta.fallback is zh-Hans (Traditional Chinese) shows text it does not hold, such as a sentence the code
// builds with a number in it, as the code's Simplified Chinese in its own characters (_meta.chars: pairs of a Simplified
// character and the one it becomes), not as English.
export function lookup(catalog, en, zh, values) {
    const own = key => catalog && Object.prototype.hasOwnProperty.call(catalog, key) && typeof catalog[key] === 'string' ? catalog[key] : null;
    const text = own(en + KEY_SEPARATOR + zh) ?? own(en) ?? (catalog?._meta?.fallback === 'zh-Hans' ? fromSimplified(catalog, zh) : en);
    return values ? fill(text, values) : text;
}

const charTables = new WeakMap();

// Simplified Chinese text in the characters of a file's _meta.chars; the text itself when the file has none.
export function fromSimplified(catalog, text) {
    let table = charTables.get(catalog);
    if (!table) {
        const pairs = [...String(catalog?._meta?.chars || '')];
        table = new Map();
        for (let i = 0; i + 1 < pairs.length; i += 2) table.set(pairs[i], pairs[i + 1]);
        charTables.set(catalog, table);
    }
    return table.size ? [...String(text)].map(ch => table.get(ch) ?? ch).join('') : String(text);
}

// The language file to use for a list of wanted languages (the system's or the browser's, most wanted first), from the
// files there are (their tags, e.g. ['en', 'zh-Hans', 'zh-Hant', 'pt-BR']). For each wanted language in turn: the same
// tag; for Chinese, the script (Taiwan, Hong Kong and Macau read Traditional, everything else Simplified); a shorter
// form of the tag (es-419 -> es); then any file of the same language (pt-PT -> pt-BR). Nothing matches: English.
// freevideo_engine/i18n.py has the same rule; tests/i18n_negotiate_cases.json holds the cases both must pass.
export function negotiate(requested, available, fallback = 'en') {
    const have = new Map([...available].sort().map(tag => [tag.toLowerCase(), tag]));
    // A single tag (the launcher's "zh-TW") counts as a list of one. Only ASCII spaces are taken off, as in Python.
    for (const raw of typeof requested === 'string' ? [requested] : requested || []) {
        const tag = String(raw ?? '').replace(/^[ \t\n\r\f]+|[ \t\n\r\f]+$/g, '').replace(/_/g, '-').toLowerCase();
        if (!tag) continue;
        if (have.has(tag)) return have.get(tag);
        const parts = tag.split('-');
        if (parts[0] === 'zh') {
            const traditional = parts.includes('hant') || (!parts.includes('hans') && ['tw', 'hk', 'mo'].includes(parts[parts.length - 1]));
            const script = traditional ? 'zh-hant' : 'zh-hans';
            if (have.has(script)) return have.get(script);
        }
        for (let n = parts.length - 1; n >= 1; n--) {
            const shorter = parts.slice(0, n).join('-');
            if (have.has(shorter)) return have.get(shorter);
        }
        const same = [...have.keys()].find(key => key.split('-')[0] === parts[0]);
        if (same) return have.get(same);
    }
    return fallback;
}

export function fill(text, values) {
    return String(text).replace(/\{(\w+)\}/g, (whole, name) => Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : whole);
}
