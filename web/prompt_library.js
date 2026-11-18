// Prompt history, templates and presets for the creative panel, kept in this browser (localStorage).
// History: prompts that were generated, newest first. Templates: read-only starting points. Presets: the panel's
// settings saved under a name (shape, resolution, length, quality, seed rule, LoRA choice, enhancement, and the
// prompt and seed value when asked); files are never stored, only names, and a missing LoRA is named when applied.
import {privacyOffered} from './private_offers.js';

export const HISTORY_KEY = 'freevideo.prompts.history', PRESET_KEY = 'freevideo.prompts.presets';
export const HISTORY_MAX = 60, PRESET_MAX = 30;

const read = (key, storage) => {
    try { const value = JSON.parse((storage ?? globalThis.localStorage).getItem(key) || '[]'); return Array.isArray(value) ? value : []; }
    catch { return []; }
};
const write = (key, rows, storage) => { try { (storage ?? globalThis.localStorage).setItem(key, JSON.stringify(rows)); return true; } catch { return false; } };

export function promptHistory(storage) { return read(HISTORY_KEY, storage).filter(row => row && typeof row.text === 'string' && row.text.trim()); }
// The same text moves to the top with its latest settings; the list keeps HISTORY_MAX entries.
export function rememberPrompt(entry, storage, now = Date.now()) {
    const text = String(entry?.text || '').trim();
    if (!text) return promptHistory(storage);
    const rows = [{...entry, text, at: now}, ...promptHistory(storage).filter(row => row.text.trim() !== text)].slice(0, HISTORY_MAX);
    write(HISTORY_KEY, rows, storage);
    return rows;
}
export function forgetPrompt(text, storage) {
    const rows = promptHistory(storage).filter(row => row.text !== text);
    write(HISTORY_KEY, rows, storage); return rows;
}
// The words of works deleted for good leave the history too, unless a kept work still has them (the engine knows which
// of the given prompts only deleted works had: POST /freevideo/private/forgotten {prompts} -> {forget: [indexes]}) or a
// preset keeps them. An engine without the call leaves the history as it is.
export async function forgetDeletedPrompts({storage, fetchImpl = (...args) => globalThis.fetch(...args), offered = privacyOffered} = {}) {
    const texts = promptHistory(storage).map(row => row.text).slice(0, 1000);
    if (!texts.length || !(await offered())) return 0;
    let answer;
    try {
        const response = await fetchImpl('/freevideo/private/forgotten', {method: 'POST', cache: 'no-store', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompts: texts})});
        if (!response?.ok) return 0;
        answer = await response.json();
    } catch { return 0; }
    const gone = new Set((answer?.forget || []).filter(i => Number.isInteger(i) && i >= 0 && i < texts.length).map(i => texts[i].trim()));
    const saved = new Set(presets(storage).map(row => String(row.settings?.prompt || '').trim()).filter(Boolean));
    // Read again: a prompt added while the engine answered stays (matched by text, not by place).
    const now = promptHistory(storage), kept = now.filter(row => !gone.has(row.text.trim()) || saved.has(row.text.trim()));
    if (kept.length !== now.length) write(HISTORY_KEY, kept, storage);
    return now.length - kept.length;
}
if (typeof window !== 'undefined') {
    // When the page opens, and after a creation is deleted for good (the engine notes its prompt once the undo time is over).
    setTimeout(() => { void forgetDeletedPrompts().catch(() => {}); }, 4000);
    window.addEventListener('freevideo-library-deleted', () => setTimeout(() => { void forgetDeletedPrompts().catch(() => {}); }, 16000));
    // A picture deleted for good takes its words from this history as well: the same words may have made a video.
    window.addEventListener('freevideo-works-removed', event => {
        if (event.detail?.undoable === false) setTimeout(() => { void forgetDeletedPrompts().catch(() => {}); }, 2000);
    });
}

export function presets(storage) { return read(PRESET_KEY, storage).filter(row => row && typeof row.name === 'string' && row.settings && typeof row.settings === 'object'); }
// A preset with the same name is replaced; names are trimmed and at most 40 characters.
export function savePreset(name, settings, storage, now = Date.now()) {
    const clean = String(name || '').trim().slice(0, 40);
    if (!clean) return null;
    const row = {id: `p${now.toString(36)}`, name: clean, at: now, settings};
    const rows = [row, ...presets(storage).filter(item => item.name !== clean)].slice(0, PRESET_MAX);
    return write(PRESET_KEY, rows, storage) ? row : null;
}
export function removePreset(id, storage) {
    const rows = presets(storage).filter(row => row.id !== id);
    write(PRESET_KEY, rows, storage); return rows;
}

export const TEMPLATES = [
    {title: ['Product close-up', '产品特写'], text: ['A slow push-in on a glass perfume bottle on wet black stone, soft rim light, droplets catch the light, a low ambient hum.',
        '镜头缓慢推近湿润黑石上的玻璃香水瓶，柔和的轮廓光，水珠闪着光，低沉的环境声。']},
    {title: ['Two people talking', '两人对话'], text: ['Two friends sit by a café window at dusk. She says: "You came back." He smiles: "I promised." Warm light, quiet street sounds.',
        '黄昏，两位朋友坐在咖啡馆窗边。她说：“你回来了。”他笑着说：“我答应过。”暖色光，安静的街道声。']},
    {title: ['Aerial landscape', '风景航拍'], text: ['An aerial shot glides over misty karst peaks at sunrise, a river winds below, birds cross the frame, wind and distant water.',
        '日出时航拍掠过云雾缭绕的喀斯特山峰，河流在下方蜿蜒，飞鸟掠过画面，风声和远处的水声。']},
    {title: ['Action follow', '动作跟拍'], text: ['A cyclist races down a mountain trail, the camera follows low and close, dust kicks up, fast breathing and tyres on gravel.',
        '自行车手沿山路飞驰而下，镜头低角度紧跟，扬起尘土，急促的呼吸声和轮胎碾过碎石的声音。']},
    {title: ['Food', '美食'], text: ['Steam rises from a bowl of hand-pulled noodles as chopsticks lift them, warm kitchen light, sizzling and gentle chatter.',
        '筷子夹起一碗手拉面，热气升腾，厨房暖光，滋滋声和轻声交谈。']},
    {title: ['Animal portrait', '动物特写'], text: ['A red fox in fresh snow turns its head toward the camera, breath visible in the cold air, soft morning light, snow crunching.',
        '雪地里的红狐狸转头看向镜头，冷空气中呼出白气，清晨柔光，踩雪的咯吱声。']},
    {title: ['City at night', '城市夜景'], text: ['Neon signs reflect on a rain-soaked street at night, a taxi passes slowly, the camera tracks along the sidewalk, rain and distant music.',
        '雨夜的街道倒映着霓虹招牌，一辆出租车缓缓驶过，镜头沿人行道平移，雨声和远处的音乐。']},
    {title: ['Short drama scene', '短剧场景'], text: ['Indoor, evening. A woman reads a letter by the lamp, her hands tremble. She whispers: "So it was you." Close-up, then a slow pull-back.',
        '室内，傍晚。女人在台灯下读信，手微微发抖。她低声说：“原来是你。”先是特写，然后缓慢拉远。']},
];

const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const svg = d => `<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const CLOCK = '<circle cx="8" cy="8" r="5.8"/><path d="M8 4.8V8l2.2 1.4"/>';
const CLOSE = '<path d="m4.5 4.5 7 7M11.5 4.5l-7 7"/>';
function setTip(node, name, use = '', key = '') {
    node.dataset.fvTipName = name; if (use) node.dataset.fvTipUse = use; if (key) node.dataset.fvTipKey = key;
    node.title = [name, use].filter(Boolean).join(' · ') + (key ? ` (${key})` : '');
}
function when(at, t) {
    const date = new Date(at); if (Number.isNaN(date.getTime())) return '';
    const pad = n => String(n).padStart(2, '0');
    return t(date.toLocaleString('en-US', {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}), `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`);
}

// host: the element the panel opens in (positioned); fill(text): put a prompt in the box (returns an undo function);
// snapshot(): the panel's settings now; apply(settings): set them (returns a line about what could not be applied).
export function createPromptLibrary({t, host, fill, snapshot, apply, describe = () => '', notify = () => {}, storage, guide = ''}) {
    const button = el('button', 'fvs-library-button'); button.type = 'button'; button.innerHTML = svg(CLOCK);
    const mod = globalThis.navigator?.platform?.startsWith('Mac') ? '⌘' : 'Ctrl';
    button.setAttribute('aria-label', t('Prompt history, templates and presets', '提示词历史、模板和预设'));
    button.setAttribute('aria-haspopup', 'dialog'); button.setAttribute('aria-expanded', 'false');
    setTip(button, t('History and presets', '历史与预设'), t('Reuse a prompt, a template or saved settings', '复用描述、模板或保存的设置'), `${mod} ↑`);
    const panel = el('section', 'fvs-library'); panel.hidden = true; panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', button.getAttribute('aria-label'));
    const tabs = el('div', 'fvs-library-tabs'); tabs.setAttribute('role', 'tablist');
    const search = el('input', 'fvs-library-search'); search.type = 'search';
    search.placeholder = t('Search', '搜索'); search.setAttribute('aria-label', t('Search prompts', '搜索描述'));
    const close = el('button', 'fvs-icon-button'); close.type = 'button'; close.innerHTML = svg(CLOSE);
    close.setAttribute('aria-label', t('Close', '关闭')); setTip(close, t('Close', '关闭'), t('Hide this panel', '收起这个面板'), 'Esc');
    const head = el('div', 'fvs-library-head'); head.append(tabs, search, close);
    const list = el('div', 'fvs-library-list'); list.setAttribute('role', 'listbox');
    const foot = el('div', 'fvs-library-foot');
    panel.append(head, list, foot);
    host.append(panel);
    let tab = 'history', query = '';
    const TABS = [['history', t('History', '历史')], ['templates', t('Templates', '模板')], ['presets', t('Presets', '预设')]];
    const tabButtons = TABS.map(([id, label]) => {
        const b = el('button', 'fvs-library-tab', label); b.type = 'button'; b.setAttribute('role', 'tab'); b.dataset.tab = id;
        b.onclick = () => { tab = id; render(); };
        tabs.append(b); return b;
    });
    setTip(tabButtons[0], t('History', '历史'), t('Prompts you generated, newest first', '生成过的描述，最新在前'));
    setTip(tabButtons[1], t('Templates', '模板'), t('Starting points to edit', '可修改的起点描述'));
    setTip(tabButtons[2], t('Presets', '预设'), t('Settings saved under a name', '按名称保存的一组设置'));
    const matches = text => !query || text.toLowerCase().includes(query);
    // One row shape for every tab, left-aligned: the main line, then one grey line (cut with an ellipsis). A named item
    // (a template, a preset) has its name as the main line, in medium weight.
    function row(text, meta, onUse, onRemove, removeLabel, {named = false} = {}) {
        const item = el('div', 'fvs-library-row'); item.setAttribute('role', 'option');
        const use = el('button', 'fvs-library-use'); use.type = 'button';
        use.append(el('span', named ? 'fvs-library-text fvs-library-name' : 'fvs-library-text', text)); if (meta) use.append(el('small', 'fvs-library-meta', meta));
        use.onclick = onUse;
        item.append(use);
        if (onRemove) {
            const remove = el('button', 'fvs-icon-button fvs-library-remove'); remove.type = 'button'; remove.innerHTML = svg(CLOSE);
            remove.setAttribute('aria-label', removeLabel); setTip(remove, removeLabel, t('Take it off this list', '从列表里移除'));
            remove.onclick = event => { event.stopPropagation(); onRemove(); };
            item.append(remove);
        }
        return item;
    }
    function empty(text) { list.append(el('p', 'fvs-library-empty', text)); }
    function use(text) { const undo = fill(text); hide(); notify(t('Prompt filled in', '已填入描述'), undo); }
    function render() {
        for (const b of tabButtons) { b.setAttribute('aria-selected', String(b.dataset.tab === tab)); b.tabIndex = b.dataset.tab === tab ? 0 : -1; }
        search.hidden = tab === 'presets';
        list.replaceChildren(); foot.replaceChildren();
        if (tab === 'history') {
            const rows = promptHistory(storage).filter(item => matches(item.text));
            for (const item of rows) list.append(row(item.text, [when(item.at, t), describe(item)].filter(Boolean).join(' · '), () => use(item.text),
                () => { forgetPrompt(item.text, storage); render(); }, t('Remove from history', '从历史移除')));
            if (!rows.length) empty(query ? t('No prompt matches.', '没有匹配的描述。') : t('Prompts you generate appear here.', '生成过的描述会出现在这里。'));
        } else if (tab === 'templates') {
            const rows = TEMPLATES.filter(item => matches(t(...item.title)) || matches(t(...item.text)));
            for (const item of rows) list.append(row(t(...item.title), t(...item.text), () => use(t(...item.text)), null, '', {named: true}));
            if (!rows.length) empty(t('No template matches.', '没有匹配的模板。'));
            if (guide) {
                const link = el('a', 'fvs-library-guide', t('How to write a prompt ↗', '描述写法 ↗'));
                link.href = guide; link.target = '_blank'; link.rel = 'noopener noreferrer';
                setTip(link, t('How to write a prompt', '描述写法'), t('Open the guide to writing a good prompt', '打开描述写法指南，写得更准'));
                foot.append(link);
            }
        } else {
            const rows = presets(storage);
            for (const item of rows) list.append(row(item.name, describe(item.settings), () => {
                hide();
                Promise.resolve(apply(item.settings)).then(problem => problem ? notify(problem, null, 'warn') : notify(t(`Preset applied: ${item.name}`, `已套用预设：${item.name}`)),
                    () => notify(t('Could not apply the preset.', '预设没能套用。'), null, 'fail'));
            }, () => { removePreset(item.id, storage); render(); }, t('Delete preset', '删除预设'), {named: true}));
            if (!rows.length) empty(t('Save the current settings to use them again in one step.', '把当前设置存成预设，下次一步套用。'));
            const form = el('form', 'fvs-preset-form');
            const name = el('input', 'fvs-preset-name'); name.type = 'text'; name.maxLength = 40; name.required = true;
            name.placeholder = t('Preset name', '预设名称'); name.setAttribute('aria-label', name.placeholder);
            const withPrompt = el('label', 'fvs-preset-check'), promptBox = el('input'); promptBox.type = 'checkbox';
            withPrompt.append(promptBox, document.createTextNode(t('Include the prompt', '包含描述')));
            const withSeed = el('label', 'fvs-preset-check'), seedBox = el('input'); seedBox.type = 'checkbox';
            withSeed.append(seedBox, document.createTextNode(t('Include the seed', '包含种子值')));
            const save = el('button', 'fvs-preset-save', t('Save', '保存')); save.type = 'submit';
            setTip(save, t('Save preset', '保存预设'), t('Save the current settings under this name', '用这个名称保存当前设置'));
            form.append(name, withPrompt, withSeed, save);
            form.onsubmit = event => {
                event.preventDefault();
                const settings = snapshot({prompt: promptBox.checked, seed: seedBox.checked});
                if (savePreset(name.value, settings, storage)) { name.value = ''; render(); notify(t('Preset saved', '预设已保存')); }
                else notify(t('Could not save the preset.', '预设没能保存。'), null, 'fail');
            };
            foot.append(form);
        }
    }
    const outside = event => { if (!panel.hidden && !panel.contains(event.target) && !button.contains(event.target)) hide(); };
    function show(which) {
        if (which) tab = which;
        query = ''; search.value = '';
        panel.hidden = false; button.setAttribute('aria-expanded', 'true'); render();
        document.addEventListener('pointerdown', outside, true);
        (tab === 'presets' ? panel.querySelector('.fvs-preset-name') : search)?.focus({preventScroll: true});
    }
    function hide() {
        if (panel.hidden) return false;
        panel.hidden = true; button.setAttribute('aria-expanded', 'false');
        document.removeEventListener('pointerdown', outside, true);
        return true;
    }
    button.onclick = () => { if (panel.hidden) show(); else hide(); };
    close.onclick = () => { hide(); button.focus({preventScroll: true}); };
    search.oninput = () => { query = search.value.trim().toLowerCase(); render(); };
    panel.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); hide(); button.focus({preventScroll: true}); }
    });
    return {button, panel, show, hide, get open() { return !panel.hidden; },
        dispose() { hide(); panel.remove(); }};
}
