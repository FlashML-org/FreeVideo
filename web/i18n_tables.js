// Text tables that hold English and Chinese as two matching trees, {en: …, zh: …}: a text, a list of texts, or an object
// of them (the tutorial, the tour cards). A table marked with a line "// i18n-table" is collected for the language files
// by scripts/i18n_extract.py, which fails when its two trees do not match.
import * as i18n from './i18n.js';

const walk = (en, zh, t) => typeof en === 'string' ? t(en, typeof zh === 'string' ? zh : en)
    : Array.isArray(en) ? en.map((item, i) => walk(item, zh?.[i], t))
    : en && typeof en === 'object' ? Object.fromEntries(Object.entries(en).map(([key, item]) => [key, walk(item, zh?.[key], t)])) : en;

// The same tree in the page's language: each text is t(en, zh). Nothing there: ''.
export const localized = (value, t) => walk(value?.en, value?.zh, t) ?? '';

// An example's prompt: what the models read and people may edit, so it is not translated. Chinese on a Chinese page
// (in Traditional characters on a Traditional one), English on every other. cn: what the caller read, as isChinese().
export function promptText(pair, cn = i18n.isChinese()) {
    const [en, zh] = Array.isArray(pair) ? pair : [pair?.en, pair?.zh];
    if (!cn || !zh) return en ?? '';
    return /^zh-hant/i.test(i18n.LANGUAGE || '') && i18n.fromSimplified ? i18n.fromSimplified(i18n.CATALOG, zh) : zh;
}

// The English texts a shown text can stand for on a page of a language file (a label is matched against patterns written
// for English and Chinese); none on an English or a Chinese page.
let meanings = null;
export function englishOf(text, catalog = i18n.CATALOG) {
    if (!catalog || !text) return [];
    if (!meanings || meanings.catalog !== catalog) {
        const map = new Map();
        for (const [key, shown] of Object.entries(catalog)) if (key !== '_meta' && typeof shown === 'string') {
            const english = key.split(i18n.KEY_SEPARATOR)[0], list = map.get(shown);
            if (!list) map.set(shown, [english]); else if (!list.includes(english)) list.push(english);
        }
        meanings = {catalog, map};
    }
    return meanings.map.get(text) || [];
}

// The page's language as an element's lang attribute: the browser picks its characters' shapes by it.
export const pageLang = i18n.pageLanguage;
