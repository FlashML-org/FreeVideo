// The interface language in Settings: a button with the globe and the language's own name, and a list of the languages
// ready to offer (Lane-5x, review/i18n/language-switch.md, direction A). The choice is saved with the launcher's settings
// (/freevideo/language), so the launcher and the page show the same language; the page reloads to change it.
import { el, icon } from './settings_kit.js';
import { listenDialogKey } from './dialog_keys.js';
import { agentPost } from './agent_session.js';
import { LANGUAGE } from './i18n.js';

// The order of the list: the languages FreeVideo's users read most first (plan.md 9.3), then any other by name.
const ORDER = ['zh-Hans', 'zh-Hant', 'en', 'es', 'pt-BR', 'ru', 'uk', 'ja', 'ko', 'fr', 'de', 'tr', 'id', 'vi', 'it', 'ar', 'fa'];
// English names, so a search for "spanish" finds Español.
const ENGLISH = {'zh-Hans': 'Chinese Simplified', 'zh-Hant': 'Chinese Traditional', en: 'English', es: 'Spanish', 'pt-BR': 'Portuguese Brazil',
    ru: 'Russian', uk: 'Ukrainian', ja: 'Japanese', ko: 'Korean', fr: 'French', de: 'German', tr: 'Turkish', id: 'Indonesian',
    vi: 'Vietnamese', it: 'Italian', ar: 'Arabic', fa: 'Persian'};
const AUTO = 'auto';
// A list longer than this gets a search box at its top.
const SEARCH_FROM = 10;
// The most rows the list shows before it scrolls.
const MAX_ROWS = 10;

// The address value a language opens with: zh and en for the two in the code, the tag for any other.
export const addressLanguage = tag => tag === 'zh-Hans' ? 'zh' : tag;

export function sortLanguages(rows) {
    const rank = tag => { const i = ORDER.indexOf(tag); return i < 0 ? ORDER.length : i; };
    return [...rows].sort((a, b) => rank(a.tag) - rank(b.tag) || a.name.localeCompare(b.name));
}

// A language matches when its tag, its own name or its English name, or any word of them, starts with what was typed
// (es and spanish find Español; es does not find Chinese or Portuguese).
export function matches(row, query) {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    const names = [row.tag, row.name, ENGLISH[row.tag] || ''].map(name => name.toLowerCase());
    return names.some(name => name.startsWith(q) || name.split(/[\s()\-]+/).some(word => word && word.startsWith(q)));
}

// state: the answer of GET /freevideo/language ({setting, language, system, languages}). t is the module's t(en, zh).
// onSaved(state) runs after a choice is saved, with the new answer.
export function languagePicker({api, t, state, onSaved, onError}) {
    const root = el('div', 'st-lang');
    const trigger = el('button', 'st-lang-btn'); trigger.type = 'button';
    trigger.setAttribute('aria-haspopup', 'listbox'); trigger.setAttribute('aria-expanded', 'false');
    const name = el('span', 'w');
    trigger.append(icon('globe'), name, icon('chev'));
    const pop = el('div', 'st-lang-pop'); pop.hidden = true;
    const search = el('input', 'st-lang-find'); search.type = 'search';
    search.placeholder = t('Search languages', '搜索语言'); search.setAttribute('aria-label', t('Search languages', '搜索语言'));
    const find = el('label', 'st-lang-search'); find.append(icon('search', 'fv-i st-lang-glass'), search);
    const list = el('div', 'st-lang-list'); list.setAttribute('role', 'listbox'); list.setAttribute('aria-label', t('Interface language', '界面语言'));
    const none = el('div', 'st-lang-none', t('No language found', '没有找到这种语言')); none.hidden = true;
    // Its own scroll bar, drawn by the page: system scroll bars that appear only while scrolling (macOS) would not show
    // that the list goes on.
    const track = el('div', 'st-lang-track'), thumb = el('i'); track.append(thumb); track.setAttribute('aria-hidden', 'true');
    const scrolled = () => {
        const over = list.scrollHeight - list.clientHeight;
        track.hidden = over <= 1;
        if (track.hidden) return;
        track.style.top = `${list.offsetTop}px`; track.style.height = `${list.clientHeight}px`;
        const size = Math.max(24, list.clientHeight * list.clientHeight / list.scrollHeight);
        thumb.style.height = `${size}px`;
        thumb.style.transform = `translateY(${(list.clientHeight - size) * list.scrollTop / over}px)`;
    };
    list.addEventListener('scroll', scrolled, {passive: true});
    pop.append(find, list, none, track);
    root.append(trigger, pop);
    let rows = [], active = -1, saving = false;

    const nameOf = tag => state.languages.find(row => row.tag === tag)?.name || tag;
    function paintButton() { name.textContent = nameOf(state.language); trigger.title = t('Interface language', '界面语言'); }
    function items() {
        const query = find.hidden ? '' : search.value;
        const offered = sortLanguages(state.languages).filter(row => matches(row, query));
        const auto = {tag: AUTO, name: t('Follow system ({name})', '跟随系统（{name}）', {name: nameOf(state.system)})};
        return query && !matches(auto, query) ? offered : [auto, ...offered];
    }
    function paintList() {
        rows = items();
        none.hidden = rows.length > 0;
        requestAnimationFrame(scrolled);
        list.replaceChildren(...rows.map((row, index) => {
            const option = el('div', 'st-lang-opt' + (row.tag === AUTO ? ' is-auto' : ''));
            option.setAttribute('role', 'option'); option.id = `st-lang-${row.tag}`;
            option.setAttribute('aria-selected', String(row.tag === state.setting));
            if (row.tag !== AUTO) option.lang = row.tag;
            option.append(icon('check', 'fv-i st-lang-mark'), el('span', 'w', row.name));
            option.onmousemove = () => { if (active !== index) { active = index; paintActive(); } };
            option.onclick = () => choose(row.tag);
            return option;
        }));
        active = Math.max(0, rows.findIndex(row => row.tag === state.setting));
        paintActive();
    }
    function paintActive() {
        [...list.children].forEach((node, index) => node.classList.toggle('is-active', index === active));
        const node = list.children[active];
        if (node) { list.setAttribute('aria-activedescendant', node.id); node.scrollIntoView({block: 'nearest'}); }
    }
    function open() {
        if (saving) return;
        find.hidden = state.languages.length + 1 <= SEARCH_FROM; search.value = '';
        paintList();
        pop.hidden = false; trigger.setAttribute('aria-expanded', 'true');
        // Below the button when it fits, otherwise on the side with more room; the list scrolls within that room.
        list.style.maxHeight = '';
        // The room is the settings pane's, not the window's: the top bar lies over the top of the window.
        const box = trigger.getBoundingClientRect(), margin = 12;
        const area = (root.closest('.st-pane') || document.documentElement).getBoundingClientRect();
        const below = Math.min(innerHeight, area.bottom) - box.bottom - margin, above = box.top - Math.max(0, area.top) - margin;
        const up = pop.offsetHeight > below && above > below;
        pop.classList.toggle('is-up', up);
        const chrome = pop.offsetHeight - list.offsetHeight, room = Math.max(96, Math.min(420, (up ? above : below) - chrome));
        // When the list scrolls, its last visible row is cut in half, so it reads as more below (Lane 11-24).
        // At most ten rows (Iris 11-24); when it scrolls, its bottom edge runs through the middle of a row, so the list
        // reads as going on (Lane 11-24). Measured from the rows themselves: the line under Follow system adds its gap.
        // Positions from the boxes on screen (not offsetTop, which depends on which ancestor is positioned).
        const rows = [...list.children], origin = list.getBoundingClientRect().top - list.scrollTop;
        const top = node => node.getBoundingClientRect().top - origin;
        const fits = rows.length <= MAX_ROWS && list.scrollHeight <= room;
        if (fits) list.style.maxHeight = '';
        else {
            let cut = 0;
            for (const [index, node] of rows.entries()) {
                const middle = top(node) + node.getBoundingClientRect().height / 2;
                if (index >= MAX_ROWS || middle > room) break;
                if (index >= 2) cut = middle;
            }
            list.style.maxHeight = `${Math.max(cut, 80)}px`;
        }
        scrolled();
        (find.hidden ? list : search).focus();
        document.addEventListener('pointerdown', outside, true);
    }
    function close(focus = true) {
        if (pop.hidden) return;
        pop.hidden = true; trigger.setAttribute('aria-expanded', 'false');
        document.removeEventListener('pointerdown', outside, true);
        if (focus) trigger.focus();
    }
    function outside(event) { if (!root.contains(event.target)) close(false); }
    async function choose(tag) {
        close();
        if (tag === state.setting) return;
        saving = true; trigger.disabled = true;
        try {
            const response = await agentPost(api, '/freevideo/language', {language: tag});
            if (!response.ok) throw new Error(String(response.status));
            state = await response.json();
            paintButton();
            onSaved?.(state);
        } catch {
            onError?.(t('Could not save the language. Try again.', '未能保存语言，请重试。'));
        } finally {
            saving = false; trigger.disabled = false;
        }
    }
    trigger.onclick = () => pop.hidden ? open() : close();
    list.tabIndex = -1;
    search.oninput = () => paintList();
    listenDialogKey(root, 'keydown', event => {
        if (pop.hidden) {
            if (event.target === trigger && ['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); open(); }
            return;
        }
        const step = {ArrowDown: 1, ArrowUp: -1}[event.key];
        if (step) { event.preventDefault(); active = (active + step + rows.length) % rows.length; paintActive(); return; }
        if (event.key === 'Enter') { event.preventDefault(); if (rows[active]) choose(rows[active].tag); return; }
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); return; }
        if (event.key === 'Tab') { close(false); return; }
        // Without a search box, typing a letter jumps to the next language that starts with it.
        if (find.hidden && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
            const letter = event.key.toLowerCase();
            for (let k = 1; k <= rows.length; k++) {
                const index = (active + k) % rows.length, row = rows[index];
                if (row.tag !== AUTO && [row.name, ENGLISH[row.tag] || ''].some(word => word.toLowerCase().startsWith(letter))) {
                    active = index; paintActive(); break;
                }
            }
        }
    });
    paintButton();
    return {element: root, get state() { return state; }, close: () => close(false),
        // Whether the page, as it is open, shows another language than the saved choice.
        differs: () => state.language !== LANGUAGE};
}
