// Subtitle-style timed lines in a description, turned into MiniMax H3's timed prompt notation.
// Same function as freevideo_engine/timed_prompt.py; both are checked against tests/fixtures/timed_prompt_cases.json.
// A new shot is written "Cut: ..." or "切：..." in any language; a language file may name its own word for it
// (_meta.keywords.cut in web/locales/<tag>.json), which is read in that language's page as well.
import { KEYWORDS, keywordList, keywordSource } from './i18n.js';

const TIME = /^[ \t　]*(\d{1,3}):([0-5]\d)(?:\.(\d{1,3}))?(?![\d.:])[ \t　]*([^\r\n]*)$/;
const LOOSE = /^[ \t　]*\d{1,3}:\d{1,2}(?![\d:])/;
const SPEECH = /^@?([^:：\s　][^:：]{0,11}?)[ \t　]*[:：][ \t　]*(\S[^\r\n]*)$/;
const NAME_BREAK = /[,.;!?，。；！？]/;
const WRITTEN = /\[Shot \d+\]|\bAt \d{2,}:\d{2}\.\d{3}/;
const CJK = /[぀-ヿ㐀-鿿]/;
const ENDS = '.!?。！？…"”';

// Cyrillic lines: Ukrainian or Russian, by weighing the words; the same rule as freevideo_engine/timed_prompt.py, which says why.
const UKRAINIAN_LETTERS = /[\u0404\u0406\u0407\u0454\u0456\u0457\u0490\u0491]/;
const RUSSIAN_LETTERS = /[\u042b\u044b\u042d\u044d\u042a\u044a\u0401\u0451]/;
const OTHER_CYRILLIC = /[\u040e\u045e\u0492\u0493\u049a\u049b\u04a2\u04a3\u04ae\u04af\u04b0\u04b1\u04ba\u04bb\u04d8\u04d9\u04e8\u04e9\u0402\u0403\u0405\u0408-\u040c\u040f\u0452\u0453\u0455\u0458-\u045c\u045f]/;
const UKRAINIAN_WORDS = new Set('що щоб цей цього ця цю ці вже дуже щось немає тобі зараз дякую'.split(' '));
const RUSSIAN_WORDS = new Set('и что чтобы это этот эта как он она они его меня тебя где когда если нет есть но или только еще мне с к'.split(' '));
const CYRILLIC_WORD = /[\u0400-\u04ff](?:['\u2019\u02bc]?[\u0400-\u04ff])*/g;
const OPENING = /[ \t\n\r"'\u00ab\u00bb\u201c\u201d\u201e\u2018\u2019()[\]\u2014\u2013-]+$/;
const ROMAN = /^[\u0406\u0425]+$/;
const isUpper = ch => ch !== ch.toLowerCase();
const isLower = ch => ch !== ch.toUpperCase();

function ukrainian(words) {
    if (OTHER_CYRILLIC.test(words)) return false;
    if (!/[\u0430-\u045f\u0491]/.test(words)) words = words.toLowerCase();
    let ukrainianWords = 0, russianWords = 0;
    for (const match of words.matchAll(CYRILLIC_WORD)) {
        const word = match[0], lower = word.toLowerCase();
        if (RUSSIAN_LETTERS.test(word) || RUSSIAN_WORDS.has(lower)) russianWords += 1;
        else if (UKRAINIAN_WORDS.has(lower)) ukrainianWords += 1;
        else if (UKRAINIAN_LETTERS.test(word)) {
            const before = words.slice(0, match.index).replace(OPENING, '');
            const inside = Boolean(before) && !'.!?:;\u2026'.includes(before[before.length - 1]);
            if (ROMAN.test(word) && before) continue;
            if (inside && isUpper(word[0]) && (word.length === 1 || [...word.slice(1)].some(isLower))) continue;
            ukrainianWords += 1;
        }
    }
    return ukrainianWords > russianWords;
}

// The cut word of a line ("Cut:", "切：", and the language's own, see _meta.keywords.cut), compiled once for a list of words.
const cutPatterns = new Map();
function cuts(words) {
    const own = keywordList(words), key = own.join('\u0001');
    if (!cutPatterns.has(key)) {
        const source = keywordSource('cut', own);
        cutPatterns.set(key, {line: new RegExp(`^${source}[ \\t　]*[:：][ \\t　]*([^\\r\\n]*)$`), bare: new RegExp(`^${source}$`)});
    }
    return cutPatterns.get(key);
}

export function language(words) {
    if (/[぀-ヿ]/.test(words)) return 'Japanese';
    if (/[가-힯]/.test(words)) return 'Korean';
    if (/[一-鿿]/.test(words)) return 'Chinese';
    if (/[Ѐ-ӿ]/.test(words)) return ukrainian(words) ? 'Ukrainian' : 'Russian';
    return 'English';
}

const pad = (value, width) => String(value).padStart(width, '0');
export const clock = ms => `${pad(Math.floor(ms / 60000), 2)}:${pad(Math.floor(ms / 1000) % 60, 2)}.${pad(ms % 1000, 3)}`;

function sentence(words) {
    words = words.trim();
    if (!words || ENDS.includes(words[words.length - 1])) return words;
    return words + (CJK.test(words) ? '。' : '.');
}

export function parse(text, cutWords = KEYWORDS.cut) {
    const head = [], lines = [], errors = [], {line: CUT, bare: CUT_BARE} = cuts(cutWords);
    text.split(/\r\n|\r|\n/).forEach((raw, index) => {
        const n = index + 1, found = TIME.exec(raw);
        if (!found) {
            if (LOOSE.test(raw)) errors.push({line: n, code: 'bad_time'});
            else if (raw.trim()) head.push(raw.trim());
            return;
        }
        const [, minutes, seconds, fraction, rest0] = found, rest = rest0.trim();
        const ms = (Number(minutes) * 60 + Number(seconds)) * 1000 + Number((fraction || '').padEnd(3, '0') || 0);
        const row = {n, ms, kind: 'event', who: '', words: rest};
        const cut = CUT.exec(rest), speech = SPEECH.exec(rest);
        if (cut || CUT_BARE.test(rest)) Object.assign(row, {kind: 'cut', words: (cut ? cut[1] : '').trim()});
        else if (speech && !NAME_BREAK.test(speech[1])) Object.assign(row, {kind: 'speech', who: speech[1].trim(), words: speech[2].trim()});
        if (!row.words) { errors.push({line: n, code: row.kind === 'cut' ? 'empty_cut' : 'bad_time'}); return; }
        lines.push(row);
    });
    return [head, lines, errors];
}

export function convert(text, seconds, speakers = null, offset = 0, cutWords = KEYWORDS.cut) {
    if (typeof text !== 'string' || WRITTEN.test(text)) return {text, errors: [], timed: false};
    const [head, lines, errors] = parse(text, cutWords);
    if (!lines.length && !errors.length) return {text, errors: [], timed: false};
    const limit = Math.round(seconds * 1000), kept = [];
    const shift = Math.round(offset * 1000);
    for (const row of lines) {
        row.ms -= shift;
        if (row.ms < 0 || row.ms >= limit) errors.push({line: row.n, code: 'out_of_range'});
        else kept.push(row);
    }
    errors.sort((a, b) => a.line - b.line);
    const ids = Object.assign(Object.create(null), speakers);
    const shots = [{ms: 0, open: head.join(' '), parts: []}];
    const rank = row => [row.kind !== 'cut', row.kind === 'speech'];
    const order = kept.map((row, i) => [row, i]).sort(([a, i], [b, j]) => a.ms - b.ms
        || rank(a)[0] - rank(b)[0] || rank(a)[1] - rank(b)[1] || i - j);
    for (const [row] of order) {
        const last = shots[shots.length - 1];
        if (row.kind === 'cut' && row.ms > 0) {
            if (last.ms === row.ms) last.open = `${last.open} ${sentence(row.words)}`.trim();
            else shots.push({ms: row.ms, open: sentence(row.words), parts: []});
            continue;
        }
        const at = row.ms > 0 ? `At ${clock(row.ms)}, ` : '';
        if (row.kind === 'cut') shots[0].open = `${shots[0].open} ${sentence(row.words)}`.trim();
        else if (row.kind === 'event') last.parts.push(at + sentence(row.words));
        else {
            if (!(row.who in ids)) ids[row.who] = Math.max(0, ...Object.values(ids)) + 1;
            last.parts.push(`${at}${row.who} (S${ids[row.who]}) begins saying: <d>[${language(row.words)}] ${row.words}</d>`);
        }
    }
    const out = shots.map((shot, index) => {
        const label = index === 0 ? '[Shot 1]' : `[Shot ${index + 1}] At ${clock(shot.ms)},`;
        return [label, shot.open, ...shot.parts].filter(Boolean).join(' ');
    });
    return {text: out.join(' '), errors, timed: true};
}

const shortClock = seconds => `${Math.floor(seconds / 60)}:${pad(Math.floor(seconds % 60), 2)}`;

// The first line the user has to fix, in one sentence; '' when every timed line can be used.
export function problem(text, seconds, t, offset = null, cutWords = KEYWORDS.cut) {
    const {errors} = convert(text, seconds, null, offset ?? 0, cutWords);
    if (!errors.length) return '';
    const {line, code} = errors[0], raw = text.split(/\r\n|\r|\n/)[line - 1] || '';
    const time = (/^[ \t　]*(\d{1,3}:\d{1,3}(?:\.\d{1,3})?)/.exec(raw) || [, ''])[1];
    const length = shortClock(seconds);
    if (code === 'out_of_range' && offset !== null) {
        const range = `${shortClock(offset)}\u2060–\u2060${shortClock(offset + seconds)}`;
        return t('Line {line} is at {time}, outside this part ({range}). Change it to a time within this part.',
            '第 {line} 行的时间 {time} 超出这一段（{range}），请改为这段内的时间。', { line, time, range });
    }
    if (code === 'out_of_range') return t('Line {line} is at {time}, beyond the video length ({length}). Change it to a time within {length}.',
        '第 {line} 行的时间 {time} 超出视频时长（{length}），请改为 {length} 以内的时间。', { line, time, length });
    if (code === 'empty_cut') return t('Line {line} has nothing after the cut. Describe the new shot.', '第 {line} 行的“切：”后面没有内容，请写出新镜头的画面。', { line });
    return t('The time "{time}" on line {line} is not valid. Write it as minutes:seconds, for example 0:05.',
        '第 {line} 行的时间“{time}”无法识别，请按“分:秒”书写，例如 0:05。', { time, line });
}

// What the model and the generator read: the converted text, or the typed text while a line is unusable.
export function timedText(text, seconds, offset = 0, cutWords = KEYWORDS.cut) {
    const done = convert(text, seconds, null, offset, cutWords);
    return done.errors.length ? text : done.text;
}
