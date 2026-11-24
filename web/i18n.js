// The page's interface language, read one way for every module. The launcher passes its language in the address as
// freevideo_lang=zh or freevideo_lang=en; without it the browser's language decides (Chinese when it starts with "zh").
// Each module still decides when it reads: most read once when they load, the tour and the tutorial when they open.
// No imports and no DOM, so Node tests load it as they load the modules that use it.
export const LANGUAGE_PARAM = 'freevideo_lang';
export const KEY_SEPARATOR = '␟';

// The language asked for in the address, as written there ('zh', 'en', anything else), or null.
export function askedLanguage(search = globalThis.location?.search) {
    try { return new URLSearchParams(search || '').get(LANGUAGE_PARAM); } catch { return null; }
}

// Whether the page shows Chinese: the address decides when it says zh or en, otherwise the browser's language.
export function isChinese(asked = askedLanguage(), browserLanguage = globalThis.navigator?.language) {
    return asked === 'zh' || (asked !== 'en' && String(browserLanguage || '').toLowerCase().startsWith('zh'));
}

// A module's t(en, zh) for the language it read.
export const translator = chinese => (en, zh) => chinese ? zh : en;

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
export function lookup(catalog, en, zh, values) {
    const own = key => catalog && Object.prototype.hasOwnProperty.call(catalog, key) && typeof catalog[key] === 'string' ? catalog[key] : null;
    const text = own(en + KEY_SEPARATOR + zh) ?? own(en) ?? en;
    return values ? fill(text, values) : text;
}

export function fill(text, values) {
    return String(text).replace(/\{(\w+)\}/g, (whole, name) => Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : whole);
}
