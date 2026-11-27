// The page's interface language, read one way for every module. The launcher passes its language in the address:
// freevideo_lang=zh for Simplified Chinese, en for English, a language file's tag for any other language (zh-Hant,
// pt-BR, ...); without it (a page not opened by the launcher) the browser's language decides (Chinese when it starts
// with "zh"). English and Simplified
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

// The tag each loaded language file was read for: its plural rules are that language's.
const catalogTags = new WeakMap();

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
            if (!catalog || typeof catalog !== 'object' || Array.isArray(catalog)) return null;
            catalogTags.set(catalog, tag);
            return catalog;
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
// The page's language as an element's lang attribute (zh-CN, en, zh-Hant, ja, ...): the browser picks its characters'
// shapes and its line-breaking rules by it.
export const pageLanguage = () => LANGUAGE === 'zh-Hans' ? 'zh-CN' : LANGUAGE;

// Names held together on screen. The language files and the code keep plain text; what the page shows holds a button's
// or a level's name on one line: a name in paired quotes (any language's marks, up to 32 characters) and a quality
// level's Chinese name get a word joiner (U+2060) between ideographs and no-break spaces, as the launcher's t() does.
// Copying and reports turn it back into plain text (plainText).
const ideograph = ch => { const n = ch.codePointAt(0); return n >= 0x2e80 && n <= 0x9fff || n >= 0xf900 && n <= 0xfaff || n >= 0x20000; };
const holdName = name => {
    const chars = Array.from(name.replace(/\u2060/g, '').replace(/ /g, '\u00a0'));  // held once, also when held text is filled into another
    return chars.map((c, i) => i && (ideograph(c) || ideograph(chars[i - 1])) ? '\u2060' + c : c).join('');
};
const QUOTED = /"([^"\n]{1,32})"|“([^“”\n]{1,32})”|「([^「」\n]{1,32})」|『([^『』\n]{1,32})』|«([^«»\n]{1,32})»|„([^„“”\n]{1,32})“/g;
// The quality levels' names as the Chinese files write them (Simplified and Traditional), held by value wherever they stand.
export const LEVEL_NAMES = ['极速档', '轻量档', '标准档', '精细档', '极致档', '極速檔', '輕量檔', '標準檔', '精細檔', '極致檔'];
const LEVELS = new RegExp(LEVEL_NAMES.join('|'), 'g');
// A level's quoted name and the 档 / 檔 / 等級 right after its closing mark: no break may come between them either.
const LEVEL_AFTER_QUOTE = /([”」』])\u2060?(档|檔|等\u2060?級)/g;
export function holdNames(text) {
    if (typeof text !== 'string' || !text) return text;
    return text.replace(QUOTED, (whole, ...groups) => {
        const name = groups.slice(0, 6).find(value => value !== undefined);
        return whole.slice(0, whole.length - name.length - 1) + holdName(name) + whole.slice(-1);
    }).replace(LEVELS, holdName).replace(LEVEL_AFTER_QUOTE, (whole, mark, word) => mark + '\u2060' + holdName(word));
}
/** A fixed phrase that follows a label (" · includes first-time setup") kept on one line as a whole, as names are. */
export const holdPhrase = text => typeof text === 'string' && text ? holdName(text) : text;
export const plainText = text => String(text ?? '').replace(/\u2060/g, '').replace(/\u00a0/g, ' ');
// What a person copies from the page is plain text: the marks added for the screen stay on the screen.
if (typeof globalThis.document?.addEventListener === 'function' && !globalThis.__fvPlainCopy) {
    globalThis.__fvPlainCopy = true;
    globalThis.document.addEventListener('copy', event => {
        const selected = globalThis.getSelection?.()?.toString() || '';
        if (!event.clipboardData || !/[\u2060\u00a0]/.test(selected)) return;
        event.clipboardData.setData('text/plain', plainText(selected));
        event.preventDefault();
    });
}

// t(en, zh, values) for a catalog (null: the text written in the code) and the language the module read.
export const translatorFor = (catalog, chinese) => catalog
    ? (en, zh, values) => holdNames(lookup(catalog, en, zh, values))
    : (en, zh, values) => holdNames(values ? fill(chinese ? zh : en, values) : chinese ? zh : en);

// A module's t(en, zh) for the language it read.
export const translator = chinese => translatorFor(CATALOG, chinese);

// Words the page reads back from text: what a person types ("Cut:", "@Image2", "Subject 1") and what the page wrote itself
// in its own language. A language file names its own in _meta.keywords, e.g. {"cut": ["Corte"], "subject": ["Sujeto"],
// "image": ["Imagen"]}; the page then reads those and the built-in words alike (English and Chinese, Simplified and
// Traditional, always work). Adding a language is still adding one file. freevideo_engine/i18n.py has the same rule for
// the words; tests/i18n_keywords_cases.json holds the cases both must pass. A word of the language's own that names an
// image or a subject is also read only where it stands on its own (see keywordSource); the built-in words are read as always.
export const KEYWORD_KINDS = ['cut', 'subject', 'image'];
export const BUILT_IN_KEYWORDS = Object.freeze({
    // Chinese first: the collector (scripts/i18n_extract.py) reads an English text followed by a Chinese one as a pair to translate.
    cut: Object.freeze(['切', 'cut', 'CUT', 'Cut']),
    subject: Object.freeze(['主体', '主體', 'Subject']),
    image: Object.freeze(['图', '圖', 'Image']),
});
export const MAX_KEYWORDS = 8;
const MAX_KEYWORD_LENGTH = 24;
// A keyword is read before a number or a colon, so it holds no colon, digit, mark of a mention or tag, or line break.
const validKeyword = word => typeof word === 'string' && word.length >= 1 && word.length <= MAX_KEYWORD_LENGTH
    && !/[:：@<>\d\r\n\t]|^\s|\s$|\s{2,}/.test(word);

// A list of words as the page reads it: the valid ones, once each, at most MAX_KEYWORDS.
export const keywordList = list => Array.isArray(list) ? [...new Set(list.filter(validKeyword))].slice(0, MAX_KEYWORDS) : [];

// The words a language file names, {cut: [...], subject: [...], image: [...]}; a kind it does not name has none.
export function keywordsIn(catalog) {
    const given = catalog?._meta?.keywords, own = given && typeof given === 'object' && !Array.isArray(given) ? given : {};
    return Object.fromEntries(KEYWORD_KINDS.map(kind => [kind, Object.prototype.hasOwnProperty.call(own, kind) ? keywordList(own[kind]) : []]));
}

// The page's own words (those of the language it shows).
export const KEYWORDS = keywordsIn(CATALOG);

// A word as typed in capitals or with a capital first letter, and for "cut" in lower case too ("cut:" is a line's first word,
// before a colon). An image or subject word is not read in lower case unless the language file wrote it so: the built-in
// "Image" is read only with its capital, because "image 4" and "una imagen 4K" are ordinary sentences.
const spellings = (word, kind) => [...new Set(kind === 'cut' ? [word, word.toLowerCase(), word.toUpperCase(), word.charAt(0).toUpperCase() + word.slice(1)]
    : [word, word.toUpperCase(), word.charAt(0).toUpperCase() + word.slice(1)])];
const CJK_END = /[぀-ヿ㐀-鿿가-힯]$/;

// Every word of a kind that is read: the built-in ones, then the language's own (in the spellings above), longest first.
export function keywordWords(kind, own = KEYWORDS[kind]) {
    const words = [...BUILT_IN_KEYWORDS[kind], ...keywordList(own).flatMap(word => spellings(word, kind))];
    return [...new Set(words)].sort((a, b) => b.length - a.length || (a < b ? -1 : a > b ? 1 : 0));
}

const escapeRegExp = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// What may not stand before a word of the language's own: a letter or a digit, and for a word written in Han characters,
// kana or Hangul, a character of that script ("動画像2" is not "画像2", "この画像2" is). A Han word after other Han
// characters is not read ("参考画像2": a file that writes it so names "参考画像" itself).
const LEFT_EDGES = [[/^\p{Script=Han}/u, '\\p{Script=Han}'], [/^\p{Script=Hiragana}/u, '\\p{Script=Hiragana}'],
    [/^\p{Script=Katakana}/u, '\\p{Script=Katakana}ー'], [/^\p{Script=Hangul}/u, '\\p{Script=Hangul}']];
// A browser that cannot read a lookbehind (Safari before 16.4) reads such a word without the limit rather than failing to load.
const LOOKBEHIND = (() => { try { return new RegExp('(?<!a)b', 'u').test('b'); } catch { return false; } })();
const leftEdge = word => LOOKBEHIND ? '(?<![' + (LEFT_EDGES.find(([first]) => first.test(word))?.[1] ?? '\\p{L}\\p{N}') + '])' : '';
// A number that is a quantity, not a picture number: "4K", "3rd", "3.5", "3,5" (letters of the alphabets written with spaces;
// "画像2を" and "이미지2를" are numbers). A full stop or comma after the number ends a sentence unless a digit follows.
const QUANTITY = '\\d+(?!\\d)(?:[A-Za-z\\u00C0-\\u024F\\u0370-\\u04FF]|[.,]\\d)';

// A regular expression source that matches any word of a kind; build the expression with the `u` flag. With `separators`
// ({latin, cjk}) each word is followed by the separator for its script: a word written in letters needs a space before
// the number ("Subject 2"), one written in Chinese, Japanese or Korean characters may have none ("主体2").
// A subject or image word of the language's own must stand on its own: not after a letter or digit ("Vorbild 3" is not
// "Bild 3") and not before a quantity ("Imagen 4K"). The built-in words are matched as they always were, and so are the
// words of a cut, which a line's start and colon already bound.
export function keywordSource(kind, own = KEYWORDS[kind], separators = null) {
    const builtIn = new Set(BUILT_IN_KEYWORDS[kind]);
    return '(?:' + keywordWords(kind, own).map(word => {
        const text = escapeRegExp(word) + (separators ? (CJK_END.test(word) ? separators.cjk : separators.latin) : '');
        return kind === 'cut' || builtIn.has(word) ? text : leftEdge(word) + text + `(?!${separators ? '' : ' ?'}${QUANTITY})`;
    }).join('|') + ')';
}

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
    const own = key => {
        if (!catalog || !Object.prototype.hasOwnProperty.call(catalog, key)) return null;
        const value = catalog[key];
        return typeof value === 'string' ? value : pluralText(value, values, catalogLanguage(catalog));
    };
    const text = own(en + KEY_SEPARATOR + zh) ?? own(en) ?? (catalog?._meta?.fallback === 'zh-Hans' ? fromSimplified(catalog, zh) : en);
    return values ? fill(text, values) : text;
}

// The language whose plural rules a file follows: the tag it was loaded for, else its _meta.tag, else the page's.
export const catalogLanguage = catalog => catalogTags.get(catalog) ?? catalog?._meta?.tag ?? LANGUAGE;

const PLURAL_FORMS = ['zero', 'one', 'two', 'few', 'many', 'other'];

// An entry written as plural forms ({"one": ..., "few": ..., "many": ..., "other": ...}) as the text for the number in
// values: the placeholder its "_by" names, else n, else count. The language's plural rules pick the form; a form the
// entry lacks, or no number, gives "other". Null when the entry is neither a string nor such an object.
export function pluralText(entry, values, language) {
    if (typeof entry === 'string') return entry;
    if (!entry || typeof entry !== 'object' || Array.isArray(entry) || typeof entry.other !== 'string') return null;
    const name = typeof entry._by === 'string' ? entry._by : ['n', 'count'].find(key => values && Object.prototype.hasOwnProperty.call(values, key));
    const number = name && values ? Number(values[name]) : NaN;
    let form = 'other';
    if (Number.isFinite(number)) {
        try { form = new Intl.PluralRules(language || undefined).select(number); } catch { form = 'other'; }
    }
    return PLURAL_FORMS.includes(form) && typeof entry[form] === 'string' ? entry[form] : entry.other;
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
