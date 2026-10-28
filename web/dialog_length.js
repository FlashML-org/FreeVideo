// Spoken dialog ↔ clip duration. Film English is about 2.4 words/s; Mandarin about 4.5
// characters/s. Staging slack is action before or between lines. This is an estimate only.

export const RATES = Object.freeze({slow: 1.8, film: 2.4, fast: 3.2});
export const CJK_RATES = Object.freeze({slow: 3.5, film: 4.5, fast: 5.5});
export const MIN_CLIP = 1.625;
export const MIN_UTTERANCE = 0.35;
export const PAUSE = 0.3;
const DURATION_STEPS = [1.625, 2, 5, 10, 15];

const D_TAG = /<d>(?:\[.*?]\s*)?([\s\S]*?)<\/d>/gi;
const WORD = /[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu;
const CJK = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/gu;

export function spokenLines(text) {
    return [...String(text || '').matchAll(D_TAG)].map(row => row[1].replace(/\s+/g, ' ').trim()).filter(Boolean);
}

export function wordCount(text) {
    return (String(text || '').match(WORD) || []).length;
}

export function units(text) {
    const source = String(text || '');
    const cjk = (source.match(CJK) || []).length;
    const latin = wordCount(source.replace(CJK, ' '));
    return {latin, cjk};
}

function lineSeconds(line, rateKey) {
    const {latin, cjk} = units(line);
    const speech = latin / (RATES[rateKey] || RATES.film) + cjk / (CJK_RATES[rateKey] || CJK_RATES.film);
    return Math.max(speech > 0 ? speech : 0, line.trim() ? MIN_UTTERANCE : 0);
}

function rawLines(text) {
    return String(text || '').split(/(?<=[.!?。！？])\s+/).map(line => line.replace(/\s+/g, ' ').trim()).filter(Boolean);
}

export function estimateSpeech(text, {rate = 'film', slack = 0.5, raw = false} = {}) {
    const chunks = raw ? rawLines(text) : spokenLines(text);
    const staging = Math.max(0, Number(slack) || 0);
    if (!chunks.length) {
        return {empty: true, lines: 0, words: 0, speech: 0, pauses: 0, slack: staging, needed: staging};
    }
    const speech = chunks.reduce((sum, line) => sum + lineSeconds(line, rate), 0);
    const pauses = Math.max(0, chunks.length - 1) * PAUSE;
    const words = chunks.reduce((sum, line) => {
        const {latin, cjk} = units(line);
        return sum + latin + Math.round(cjk / 2);
    }, 0);
    const needed = speech + pauses + staging;
    return {empty: false, lines: chunks.length, words, speech, pauses, slack: staging, needed};
}

export function suggestDuration(needed) {
    const value = Math.max(0, Number(needed) || 0);
    for (const step of DURATION_STEPS) if (value <= step * 1.05) return step;
    return Math.max(MIN_CLIP, Math.ceil(value));
}

export function fitStatus(needed, duration) {
    const clip = Math.max(0, Number(duration) || 0);
    if (!(clip > 0) || !(needed >= 0)) return 'unknown';
    if (needed <= clip) return 'fits';
    if (needed <= clip * 1.15) return 'tight';
    return 'over';
}

export function wordsForDuration(duration, {rate = 'film', slack = 0.5} = {}) {
    const budget = Math.max(0, (Number(duration) || 0) - Math.max(0, Number(slack) || 0));
    return Math.max(0, Math.floor(budget * (RATES[rate] || RATES.film)));
}

export function createDialogLength({t, prompt, seconds, onApply = () => {}, animate}) {
    const el = (tag, cls, text) => {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text != null) node.textContent = text;
        return node;
    };
    const wrap = el('details', 'fv-dialog-length');
    wrap.append(el('summary', null, t('Spoken dialog estimate', '对白时长估算')));
    const body = el('div', 'fv-dialog-length-body');
    body.append(el('p', null, t(
        'Estimate only. Complex action before a line needs more time than the words. Spoken lines in the prompt use <d>…</d>.',
        '仅为估算。对白前的动作往往比字数更占时间。提示词里的台词写在 <d>…</d> 中。')));
    const extra = document.createElement('textarea');
    extra.rows = 2;
    extra.placeholder = t('Or type the spoken line here (e.g. Hi)', '或在此输入台词（例如：嗨）');
    extra.setAttribute('aria-label', t('Spoken line', '台词'));
    const row = el('div', 'fv-dialog-length-row');
    const rate = document.createElement('select');
    rate.setAttribute('aria-label', t('Speaking pace', '语速'));
    for (const [value, en, zh] of [['slow', 'Slow', '慢'], ['film', 'Film', '电影对白'], ['fast', 'Fast', '快']]) {
        const option = document.createElement('option'); option.value = value; option.textContent = t(en, zh); rate.append(option);
    }
    rate.value = 'film';
    const slack = document.createElement('select');
    slack.setAttribute('aria-label', t('Action before speech', '开口前的动作'));
    for (const [value, en, zh] of [['0', 'Speak immediately', '马上开口'], ['0.5', 'Short beat (0.5 s)', '短停 0.5 秒'],
        ['1', 'Some action (1 s)', '一些动作 1 秒'], ['2', 'Busy staging (2 s)', '较多动作 2 秒']]) {
        const option = document.createElement('option'); option.value = value; option.textContent = t(en, zh); slack.append(option);
    }
    slack.value = '0.5';
    row.append(rate, slack);
    const status = el('p', 'fv-dialog-length-status');
    status.setAttribute('role', 'status');
    const apply = document.createElement('button');
    apply.type = 'button'; apply.className = 'fv-quiet';
    apply.textContent = t('Use suggested duration', '使用建议时长');
    apply.hidden = true;
    let suggested = MIN_CLIP;
    const refresh = () => {
        const typed = extra.value.trim();
        const estimate = typed
            ? estimateSpeech(typed, {rate: rate.value, slack: slack.value, raw: true})
            : estimateSpeech(prompt?.value || '', {rate: rate.value, slack: slack.value});
        const duration = Number(seconds.value);
        apply.hidden = true;
        if (estimate.empty) {
            const room = wordsForDuration(duration, {rate: rate.value, slack: slack.value});
            status.textContent = t(
                `No <d> line yet. About ${room} English words fit ${duration || '—'} s at this pace, after staging.`,
                `还没有 <d> 台词。按此语速，去掉动作后约 ${room} 个英文词能放进 ${duration || '—'} 秒。`);
            status.dataset.fit = 'unknown';
            return;
        }
        suggested = suggestDuration(estimate.needed);
        const fit = fitStatus(estimate.needed, duration);
        status.dataset.fit = fit;
        const spoken = estimate.needed.toFixed(1);
        const label = fit === 'fits'
            ? t(`Spoken ~${spoken} s. Fits ${duration} s.`, `对白约 ${spoken} 秒，放得进 ${duration} 秒。`)
            : fit === 'tight'
                ? t(`Spoken ~${spoken} s. Tight in ${duration} s.`, `对白约 ${spoken} 秒，${duration} 秒偏紧。`)
                : t(`Spoken ~${spoken} s. Needs about ${suggested} s, not ${duration} s.`,
                    `对白约 ${spoken} 秒。需要约 ${suggested} 秒，而不是 ${duration} 秒。`);
        status.textContent = label;
        apply.hidden = !(suggested > 0) || Math.abs(suggested - duration) < 0.05;
    };
    apply.onclick = () => { seconds.value = String(suggested); onApply(suggested); refresh(); };
    extra.oninput = refresh; rate.onchange = refresh; slack.onchange = refresh;
    seconds.addEventListener('input', refresh);
    prompt?.addEventListener('input', refresh);
    body.append(extra, row, status, apply);
    wrap.append(body);
    const motion = animate?.(wrap);
    refresh();
    return {
        element: wrap,
        refresh,
        dispose() {
            seconds.removeEventListener('input', refresh);
            prompt?.removeEventListener('input', refresh);
            motion?.();
        },
    };
}
