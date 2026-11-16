// What was said before and fill-in templates, for the box to type in. Picking one fills the box; it is never sent.
// What was said comes from the chat being read (the engine keeps it, and deleting the chat deletes it); nothing of
// what was said is kept in this browser. Templates mark the words to replace with 【】 (or [] in English); the
// first mark is selected.
import {tip} from './autopilot_tips.js';
import {glyph} from './autopilot_panels.js';
import {listenDialogKey} from './dialog_keys.js';

export const SAID_KEY = 'freevideo.autopilot.said';
export const SAID_MAX = 30;
export const TEMPLATES = Object.freeze([
    {name: ['Product', '产品展示'], text: ['A [length]-second product film: [product] on [background], the camera circles once.', '做一条【时长】秒的产品短片：【产品】放在【背景】上，镜头环绕一周。']},
    {name: ['Monologue', '人物独白'], text: ['[person] at [place] says to the camera: “[line]”, in [shots] shots.', '【人物】在【地点】对着镜头说：“【台词】”，共【镜头数】个镜头。']},
    {name: ['Story', '故事短片'], text: ['A [length]-second short: [start], then [turn], and finally [end].', '做一条【时长】秒的短片：【开头】，然后【转折】，最后【结尾】。']},
]);
const HISTORY_ICON = 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2', TEMPLATE_ICON = 'M5 4h14v16H5zM9 8h6M9 12h6M9 16h3';

// The newest first, one entry per sentence; the same words said again move to the top.
export function recordSaid(list, text, at = Date.now()) {
    const words = String(text || '').trim();
    if (!words) return list;
    return [{text: words, at}, ...list.filter(item => item.text !== words)].slice(0, SAID_MAX);
}
// The sentences of a chat's own entries, newest first, one each: {text, at} with `at` in milliseconds.
export function saidIn(entries) {
    let list = [];
    for (const entry of [...entries || []].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0)))
        if (entry?.type === 'message' && entry.role === 'user' && typeof entry.text === 'string')
            list = recordSaid(list, entry.text, Number.isFinite(entry.at) ? entry.at * (entry.at < 1e12 ? 1000 : 1) : 0);
    return list;
}
// An earlier version kept the last sentences in this browser; that record is removed.
export function forgetStoredSaid(storage = globalThis.localStorage) { try { storage?.removeItem(SAID_KEY); } catch { /* Nothing to remove. */ } }
// Where the first word to replace sits in a template, or null.
export function firstMark(text) {
    const at = text.search(/[【\[]/), end = text.search(/[】\]]/);
    return at >= 0 && end > at ? [at, end + 1] : null;
}

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

// button: the tool that opens it; input: the box; host: where the menu floats (above the box); time(at): a short time.
export function createHistoryMenu(t, {button, input, host, time = () => '', said = () => [], fill}) {
    const menu = el('div', 'fv-chat-history'); menu.hidden = true; menu.setAttribute('role', 'dialog'); menu.setAttribute('aria-label', t('Prompt history and templates', '历史与模板'));
    const tabs = el('div', 'fv-chat-seg'); tabs.setAttribute('role', 'tablist');
    const list = el('div', 'fv-chat-history-list'), foot = el('p', 'fv-chat-history-foot', t('Picking one fills the box; it is not sent', '选中后放进输入框，不会直接发送'));
    let tab = 'said';
    const tabButton = (id, text) => {
        const b = el('button', null, text); b.type = 'button'; b.setAttribute('role', 'tab'); b.dataset.tab = id;
        b.onclick = () => { tab = id; draw(); }; return b;
    };
    tabs.append(tabButton('said', t('Recent', '最近说过')), tabButton('templates', t('Templates', '模板')));
    menu.append(tabs, list, foot); host.append(menu);
    function row(icon, title, sub, action) {
        const b = el('button', 'fv-chat-history-row'); b.type = 'button';
        const words = el('span', 'fv-chat-history-words'), name = el('span', 'fv-chat-history-text', title);
        words.append(name); if (sub) words.append(el('small', null, sub));
        b.append(glyph(icon, 1.8), words); b.onclick = action; return b;
    }
    function draw() {
        for (const b of tabs.children) b.setAttribute('aria-selected', String(b.dataset.tab === tab));
        if (tab === 'said') {
            const items = said();
            list.replaceChildren(...items.slice(0, 8).map(item => tip(row(HISTORY_ICON, item.text, time(item.at), () => pick(item.text)), 'historyRow', t)));
            if (!items.length) list.append(el('p', 'fv-chat-history-empty', t('What you send shows up here.', '发过的话会出现在这里。')));
        } else list.replaceChildren(...TEMPLATES.map(item => tip(row(TEMPLATE_ICON, t(...item.name), t(...item.text), () => pick(t(...item.text), true)), 'templateRow', t)));
    }
    function pick(text, template = false) {
        open(false);
        fill(text);
        input.focus();
        const mark = template && firstMark(text);
        if (mark) input.setSelectionRange?.(...mark);
    }
    function open(on = menu.hidden) {
        menu.hidden = !on; button.setAttribute('aria-expanded', String(on));
        if (on) { draw(); (list.querySelector('button') || tabs.querySelector('[aria-selected=true]'))?.focus({preventScroll: true}); }
    }
    listenDialogKey(menu, 'keydown', event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); open(false); button.focus(); } });
    const outside = event => { if (!menu.hidden && !menu.contains(event.target) && !button.contains(event.target)) open(false); };
    document.addEventListener('pointerdown', outside, true);
    button.setAttribute('aria-haspopup', 'dialog'); button.setAttribute('aria-expanded', 'false');
    return {element: menu, open, close: () => open(false), dispose() { document.removeEventListener('pointerdown', outside, true); }};
}
