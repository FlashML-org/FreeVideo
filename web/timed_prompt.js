// Subtitle-style timed lines in a description, turned into MiniMax H3's timed prompt notation.
// Same function as freevideo_engine/timed_prompt.py; both are checked against tests/fixtures/timed_prompt_cases.json.
const TIME = /^[ \t　]*(\d{1,3}):([0-5]\d)(?:\.(\d{1,3}))?(?![\d.:])[ \t　]*([^\r\n]*)$/;
const LOOSE = /^[ \t　]*\d{1,3}:\d{1,2}(?![\d:])/;
const CUT = /^(?:cut|CUT|Cut|切)[ \t　]*[:：][ \t　]*([^\r\n]*)$/;
const CUT_BARE = /^(?:cut|CUT|Cut|切)$/;
const SPEECH = /^@?([^:：\s　][^:：]{0,11}?)[ \t　]*[:：][ \t　]*(\S[^\r\n]*)$/;
const NAME_BREAK = /[,.;!?，。；！？]/;
const WRITTEN = /\[Shot \d+\]|\bAt \d{2,}:\d{2}\.\d{3}/;
const CJK = /[぀-ヿ㐀-鿿]/;
const ENDS = '.!?。！？…"”';

export function language(words) {
    if (/[぀-ヿ]/.test(words)) return 'Japanese';
    if (/[가-힯]/.test(words)) return 'Korean';
    if (/[一-鿿]/.test(words)) return 'Chinese';
    if (/[Ѐ-ӿ]/.test(words)) return 'Russian';
    return 'English';
}

const pad = (value, width) => String(value).padStart(width, '0');
export const clock = ms => `${pad(Math.floor(ms / 60000), 2)}:${pad(Math.floor(ms / 1000) % 60, 2)}.${pad(ms % 1000, 3)}`;

function sentence(words) {
    words = words.trim();
    if (!words || ENDS.includes(words[words.length - 1])) return words;
    return words + (CJK.test(words) ? '。' : '.');
}

export function parse(text) {
    const head = [], lines = [], errors = [];
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

export function convert(text, seconds, speakers = null, offset = 0) {
    if (typeof text !== 'string' || WRITTEN.test(text)) return {text, errors: [], timed: false};
    const [head, lines, errors] = parse(text);
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
export function problem(text, seconds, t, offset = null) {
    const {errors} = convert(text, seconds, null, offset ?? 0);
    if (!errors.length) return '';
    const {line, code} = errors[0], raw = text.split(/\r\n|\r|\n/)[line - 1] || '';
    const time = (/^[ \t　]*(\d{1,3}:\d{1,3}(?:\.\d{1,3})?)/.exec(raw) || [, ''])[1];
    const length = shortClock(seconds);
    if (code === 'out_of_range' && offset !== null) {
        const range = `${shortClock(offset)}\u2060–\u2060${shortClock(offset + seconds)}`;
        return t(`Line ${line} is at ${time}, outside this part (${range}). Change it to a time within this part.`,
            `第 ${line} 行的时间 ${time} 超出这一段（${range}），请改为这段内的时间。`);
    }
    if (code === 'out_of_range') return t(`Line ${line} is at ${time}, beyond the video length (${length}). Change it to a time within ${length}.`,
        `第 ${line} 行的时间 ${time} 超出视频时长（${length}），请改为 ${length} 以内的时间。`);
    if (code === 'empty_cut') return t(`Line ${line} has nothing after the cut. Describe the new shot.`, `第 ${line} 行的“切：”后面没有内容，请写出新镜头的画面。`);
    return t(`The time "${time}" on line ${line} is not valid. Write it as minutes:seconds, for example 0:05.`,
        `第 ${line} 行的时间“${time}”无法识别，请按“分:秒”书写，例如 0:05。`);
}

// What the model and the generator read: the converted text, or the typed text while a line is unusable.
export function timedText(text, seconds, offset = 0) {
    const done = convert(text, seconds, null, offset);
    return done.errors.length ? text : done.text;
}
