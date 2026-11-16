// Prompt history and settings presets, kept in this browser until the engine keeps them.
// History: the last prompts that were generated, newest first, the same text once. Presets: a name for a model,
// shape, size and seed (and optionally the prompt), applied in one click.
import { el, t, tip, button, readStore, writeStore } from './image_view_ui.js';
import { privacyOffered } from './private_offers.js';

export const HISTORY_KEY = 'freevideo.imageView.prompts';
export const PRESET_KEY = 'freevideo.imageView.presets';
const HISTORY_LIMIT = 60;

export function rememberPrompt(list, text, {model = '', at = Date.now()} = {}) {
    const clean = String(text || '').trim(); if (!clean) return list;
    const old = list.find(p => p.text === clean);
    const rest = list.filter(p => p.text !== clean);
    const kept = [{text: clean, model, at, count: (old?.count || 0) + 1, pinned: !!old?.pinned}, ...rest];
    // Pinned ones are never pushed out.
    let loose = 0;
    return kept.filter(p => p.pinned || ++loose <= HISTORY_LIMIT);
}
export function orderedPrompts(list, query = '') {
    const q = query.trim().toLowerCase();
    return list.filter(p => !q || p.text.toLowerCase().includes(q)).sort((a, b) => (b.pinned - a.pinned) || (b.at - a.at));
}
// The words of works deleted for good leave the history too, unless a kept work still has them (the engine knows) or
// the user saved them in a preset. The engine answers which of the given prompts only deleted works had
// (POST /freevideo/private/forgotten {prompts} → {forget: [indexes]}); an engine without the call leaves all as they are.
export function dropForgotten(list, texts, forget, presets = []) {
    const gone = new Set((forget || []).filter(i => Number.isInteger(i) && i >= 0 && i < texts.length).map(i => String(texts[i]).trim()));
    const saved = new Set(presets.map(p => String(p?.prompt || '').trim()).filter(Boolean));
    return list.filter(p => !gone.has(String(p.text).trim()) || saved.has(String(p.text).trim()));
}
export async function forgetDeletedPrompts({fetchImpl = (...args) => globalThis.fetch(...args), offered = () => privacyOffered(fetchImpl)} = {}) {
    const texts = readStore(HISTORY_KEY, []).map(p => p.text).filter(Boolean).slice(0, 1000);
    // An engine without the privacy calls is asked nothing (it would answer 404).
    if (!texts.length || !await offered()) return 0;
    let answer;
    try {
        const response = await fetchImpl('/freevideo/private/forgotten', {method: 'POST', cache: 'no-store', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompts: texts})});
        if (!response?.ok) return 0;
        answer = await response.json();
    } catch { return 0; }
    // Read again: a prompt added while the engine answered stays (it is matched by text, not by place).
    const now = readStore(HISTORY_KEY, []), kept = dropForgotten(now, texts, answer?.forget, readStore(PRESET_KEY, []));
    if (kept.length !== now.length) writeStore(HISTORY_KEY, kept);
    return now.length - kept.length;
}
export function savePreset(list, preset) {
    const name = String(preset.name || '').trim(); if (!name) return list;
    return [{...preset, name, at: preset.at || Date.now()}, ...list.filter(p => p.name !== name)].slice(0, 40);
}

// The group's body: two tabs, a list each, and "save current settings" under presets.
export function promptLibrary({onUsePrompt, onApplyPreset, currentSettings, describe}) {
    let prompts = readStore(HISTORY_KEY, []), presets = readStore(PRESET_KEY, []), tab = readStore('freevideo.imageView.libraryTab', 'history');
    const root = el('div', 'fv-iv-library');
    const tabs = el('div', 'fv-iv-seg'); tabs.setAttribute('role', 'tablist');
    const historyTab = button('', {label: t('Prompts', '提示词'), name: t('Prompt history', '提示词历史'), use: t('Prompts you generated before', '以前生成用过的提示词')});
    const presetTab = button('', {label: t('Presets', '预设'), name: t('Presets', '参数预设'), use: t('Saved model, shape and size', '保存的模型、画幅和尺寸')});
    tabs.append(historyTab, presetTab);
    const list = el('div', 'fv-iv-library-list');
    // The whole history can be cleared in one press (the presets are kept).
    const clearAll = button('fv-iv-text-btn fv-iv-library-clear', {iconName: 'trash', label: t('Clear history', '清空'), name: t('Clear prompt history', '清空提示词历史'), use: t('Remove every prompt in this list from this browser; presets stay', '从这个浏览器删掉列表里所有提示词；预设保留')});
    clearAll.addEventListener('click', () => { prompts = []; writeStore(HISTORY_KEY, prompts); paint(); });
    const saveRow = el('form', 'fv-iv-preset-save');
    const nameInput = el('input'); nameInput.placeholder = t('Name these settings', '给当前设置起个名字'); nameInput.maxLength = 40;
    tip(nameInput, t('Preset name', '预设名称'), t('Then press Save', '输入后点保存'));
    const withPrompt = el('label', 'fv-iv-check'); const withPromptBox = el('input'); withPromptBox.type = 'checkbox'; withPrompt.append(withPromptBox, el('span', '', t('Include prompt', '包含提示词')));
    tip(withPromptBox, t('Include prompt', '包含提示词'), t('The preset also fills in the prompt', '应用预设时也填入提示词'));
    const save = button('fv-iv-text-btn fv-iv-accent', {iconName: 'save', label: t('Save', '保存'), name: t('Save preset', '保存预设'), use: t('Keep the current model, shape and size', '保存当前模型、画幅和尺寸')});
    save.type = 'submit'; saveRow.append(nameInput, save, withPrompt);
    root.append(tabs, list, clearAll, saveRow);
    historyTab.addEventListener('click', () => { tab = 'history'; writeStore('freevideo.imageView.libraryTab', tab); paint(); });
    presetTab.addEventListener('click', () => { tab = 'presets'; writeStore('freevideo.imageView.libraryTab', tab); paint(); });
    saveRow.addEventListener('submit', event => {
        event.preventDefault(); const name = nameInput.value.trim(); if (!name) { nameInput.focus(); return; }
        const settings = currentSettings(); if (!withPromptBox.checked) delete settings.prompt;
        presets = savePreset(presets, {name, ...settings}); writeStore(PRESET_KEY, presets); nameInput.value = ''; paint();
    });

    function row({title, sub, pinned, onUse, onPin, onRemove, useName, useText}) {
        const item = el('div', 'fv-iv-li');
        const main = el('button', 'fv-iv-li-main'); main.type = 'button';
        main.append(el('span', 'fv-iv-li-t', title), el('span', 'fv-iv-li-s', sub));
        tip(main, useName, useText); main.addEventListener('click', onUse);
        const end = el('span', 'fv-iv-li-end');
        if (onPin) { const pin = button('fv-iv-icon-btn fv-iv-pin', {iconName: 'star', name: pinned ? t('Unpin', '取消置顶') : t('Pin', '置顶'), use: pinned ? t('Let it age out', '不再固定在最上面') : t('Keep it at the top', '固定在列表最上面')}); pin.setAttribute('aria-pressed', String(!!pinned)); pin.addEventListener('click', onPin); end.append(pin); }
        const remove = button('fv-iv-icon-btn', {iconName: 'x', name: t('Remove', '移除'), use: t('Take it off this list', '从列表里去掉')}); remove.addEventListener('click', onRemove); end.append(remove);
        item.append(main, end); return item;
    }
    function paint() {
        // Another page (or the module that files finished pictures) may have added to the lists.
        prompts = readStore(HISTORY_KEY, prompts); presets = readStore(PRESET_KEY, presets);
        historyTab.setAttribute('aria-selected', String(tab === 'history')); historyTab.setAttribute('aria-pressed', String(tab === 'history'));
        presetTab.setAttribute('aria-selected', String(tab === 'presets')); presetTab.setAttribute('aria-pressed', String(tab === 'presets'));
        saveRow.hidden = tab !== 'presets';
        clearAll.hidden = tab !== 'history' || !prompts.length;
        if (tab === 'history') {
            const items = orderedPrompts(prompts);
            list.replaceChildren(...items.slice(0, 30).map(p => row({title: p.text, sub: p.count > 1 ? t(`used ${p.count}×`, `用过 ${p.count} 次`) : '', pinned: p.pinned,
                useName: t('Use this prompt', '使用这条提示词'), useText: t('Puts it in the prompt box', '填入描述框'),
                onUse: () => onUsePrompt(p.text),
                onPin: () => { prompts = prompts.map(x => x.text === p.text ? {...x, pinned: !x.pinned} : x); writeStore(HISTORY_KEY, prompts); paint(); },
                onRemove: () => { prompts = prompts.filter(x => x.text !== p.text); writeStore(HISTORY_KEY, prompts); paint(); }})));
            // Nothing yet: nothing is said (the group opens on what it holds).
        } else {
            list.replaceChildren(...presets.map(p => row({title: p.name, sub: describe(p),
                useName: t('Apply preset', '应用预设'), useText: t('Sets model, shape and size', '设置模型、画幅和尺寸'),
                onUse: () => onApplyPreset(p),
                onRemove: () => { presets = presets.filter(x => x.name !== p.name); writeStore(PRESET_KEY, presets); paint(); }})));

        }
    }
    paint();
    return {element: root, paint, remember(text, model) { prompts = rememberPrompt(readStore(HISTORY_KEY, prompts), text, {model}); writeStore(HISTORY_KEY, prompts); paint(); },
        count: () => ({prompts: prompts.length, presets: presets.length})};
}
