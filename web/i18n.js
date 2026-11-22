import { catalogs } from './locales/catalogs.js';

// A single locale negotiation policy, shared by the native views.
// Keep English as fallback and the existing URL as the explicit override.
export const SUPPORTED_LOCALES = Object.freeze(['en','zh','pt-BR']);
function normalize(value) {
    const language = String(value || '').toLowerCase();
    return language === 'en' || language.startsWith('en-') ? 'en'
        : language === 'zh' || language.startsWith('zh-') ? 'zh'
        : language === 'pt' || language.startsWith('pt-') ? 'pt-BR' : null;
}
export function resolveLocale(search = globalThis.location?.search || '', browserLanguage = globalThis.navigator?.language || '') {
    return normalize(new URLSearchParams(search).get('freevideo_lang')) || normalize(browserLanguage) || 'en';
}
export const currentLocale = () => resolveLocale();
export function translateKey(key, locale = currentLocale(), values = {}) {
    const language = normalize(locale) || 'en';
    const value = (Object.hasOwn(catalogs[language], key) ? catalogs[language][key] : undefined)
        ?? (Object.hasOwn(catalogs.en, key) ? catalogs.en[key] : undefined) ?? key;
    return value.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);
}
// Transitional adapter: preserve the exact English/Chinese text in the original call,
// including contextual/dynamic messages. Untranslated Portuguese strings fall back to English.
const aliases = new Map(Object.entries(catalogs.en).filter(([key]) => key.startsWith('ui.') || key.startsWith('nav.')).map(([key, english]) => [english, key]));
export function translatePair(en, zh, chinese = currentLocale() === 'zh') {
    if (chinese) return zh;
    if (currentLocale() !== 'pt-BR') return en;
    if (en === 'Light') {
        const key = zh === '浅色' ? 'theme.light' : zh === '光线' ? 'ui.light' : 'quality.light';
        return translateKey(key, 'pt-BR');
    }
    const key = aliases.get(en);
    if (key) return translateKey(key, 'pt-BR');
    if (typeof en === 'string') {
        const estimate = /^About (\d+) min$/.exec(en);
        const generating = /^Generating (\d+)%$/.exec(en);
        if (estimate) return translateKey('progress.estimate_minutes', 'pt-BR', {minutes: estimate[1]});
        if (generating) return translateKey('progress.generating_percent', 'pt-BR', {percent: generating[1]});
    }
    return en;
}
export function nextLocale(locale = currentLocale()) {
    return SUPPORTED_LOCALES[(SUPPORTED_LOCALES.indexOf(locale)+1)%SUPPORTED_LOCALES.length];
}
