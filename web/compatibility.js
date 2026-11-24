import { api } from '../../scripts/api.js';
import './fv_select.js';
import './glass/library.js';
import {userMessage} from './user_message.js';
import { isChinese, translator } from './i18n.js';

// FreeVideo's own reason keeps the lead-in that says what failed; browser or program wording becomes a plain sentence.
const saveFailure = (error, tt) => { const text = userMessage(error, tt, tt('Could not save. Try again.', '没能保存，请重试。'));
    return text === String(error?.message ?? '').trim() ? tt('Save failed: ', '保存失败：') + text : text; };
const readFailure = (error, tt) => { const text = userMessage(error, tt, tt('Could not read the setting. Try again.', '没能读取设置，请重试。'));
    return text === String(error?.message ?? '').trim() ? tt('The setting could not be read: ', '无法读取设置：') + text : text; };

const cn = isChinese();
const t = translator(cn);
const unavailable = () => t('Device information is unavailable; compatibility settings are off.', '无法获取设备信息，兼容性设置已关闭。');
let current;
const element = (tag, text) => { const node = document.createElement(tag); if (text) node.textContent = text; return node; };
async function client(source = api) {
    const response = await source.fetchApi('/freevideo/setup');
    const info = await response.json();
    if (!response.ok) throw new Error(info.error || response.statusText);
    return async (action, value = {}) => {
        const response = await source.fetchApi('/freevideo/setup/compatibility-' + action, {
            method: 'POST', headers: {'Content-Type':'application/json','X-FreeVideo-Setup':info.discovery.token},
            body: JSON.stringify({root:info.discovery.root, ...value}),
        });
        const result = await response.json();
        if (!response.ok || result.error) throw new Error(result.error || response.statusText);
        return result;
    };
}
// Same sheet as Settings: header with close, content, actions on the right.
function dialog(title) {
    const value = element('dialog');
    value.className = 'fv-setup fv-setup-small';
    const head = element('header'); head.className = 'fv-setup-head';
    const close = element('button', '×'); close.type = 'button'; close.className = 'fv-close';
    close.setAttribute('aria-label', t('Close', '关闭')); close.onclick = () => value.close();
    head.append(element('h2', title), close);
    const actions = element('div'); actions.className = 'fv-actions fv-end';
    value.append(head, actions); value.actions = actions;
    value.addEventListener('close', () => value.remove());
    return value;
}
function button(panel, label, action, primary = false) {
    const value = element('button', label); value.type = 'button';
    value.className = primary ? 'fv-primary' : 'fv-quiet';
    value.onclick = action; panel.actions.append(value); return value;
}
// Open once the first content is in place, so focus lands on a control.
function show(panel, focus) {
    document.body.append(panel); panel.showModal(); focus?.focus();
}
// Compatibility now lives in Settings › Advanced (compatibilityCard below); this opens that page.
export async function openCompatibility() {
    try { const {openSetup} = await import('./setup.js'); await openSetup({section: 'advanced'}); return; }
    catch { /* Settings could not open: the stand-alone sheet below still works. */ }
    return openCompatibilitySheet();
}

// The card in Settings › Advanced: the level as four segments (off, 1, 2, 3) with one line saying what it does, the
// automatic raise as a switch, the note, Revert and Save, and a status line that is always there (nothing moves).
// States: loading, unavailable device, read failure, edited (unsaved), saving, save failure, saved. On a Mac (unified
// memory) the card says no level is needed. Returns {element, setUnified, dispose}.
export function compatibilityCard({t: tt = t, api: client0 = api} = {}) {
    const node = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
    let kit = null;
    const element = node('div', 'st-card st-compat');
    const head = node('div', 'st-row has-ic');
    const tile = node('span', 'st-ic'); const text = node('div', 'st-rt'); const title = node('b', '', tt('Compatibility level', '兼容档位')); const line = node('span', '', tt('Loading settings…', '正在读取设置…'));
    text.append(title, line); const end = node('div', 'st-re'); head.append(tile, text, end);
    const autoRow = node('div', 'st-row'); const autoText = node('div', 'st-rt');
    autoText.append(node('b', '', tt('Raise the level automatically after an unexpected interruption', '意外中断后自动提高档位')),
        node('span', '', tt('After raising it, a few smooth generations bring it back down', '提高后，连续顺利完成几次会再降回来')));
    const autoEnd = node('div', 'st-re'); autoRow.append(autoText, autoEnd);
    const note = node('p', 'st-note', tt('Applies from the next generation. Higher levels have the GPU handle less at a time, so generation is slower and the picture differs slightly; resolution, duration and steps stay the same.', '从下一次生成起生效。档位越高，显卡每次处理的数据越少，生成越慢，画面也会有细微差别；分辨率、时长和步数不变。'));
    const foot = node('div', 'st-foot'); const status = node('p', 'st-slot'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const revert = node('button', 'st-btn', tt('Revert', '还原')); revert.type = 'button';
    const save = node('button', 'st-btn is-fill', tt('Save', '保存')); save.type = 'button';
    foot.append(status, revert, save);
    const mac = node('div', 'st-row'); const macText = node('div', 'st-rt'); macText.append(node('b', '', tt('This computer needs no compatibility level', '这台电脑不需要兼容档位')), node('span', '', tt('Generation on this computer shares its memory, so the level does not apply.', '这台电脑生成时共用内存，兼容档位不适用。')));
    mac.append(macText, node('div', 'st-re')); mac.hidden = true;
    element.append(head, autoRow, note, foot, mac);
    const level = document.createElement('select'); level.setAttribute('aria-label', tt('Compatibility level', '兼容档位'));
    [tt('Off', '关闭'), tt('Level 1', '1 级'), tt('Level 2', '2 级'), tt('Level 3', '3 级')].forEach((name, i) => { const o = node('option', '', name); o.value = String(i); level.append(o); });
    const automatic = document.createElement('input'); automatic.type = 'checkbox';
    automatic.setAttribute('aria-label', tt('Raise the level automatically after an unexpected interruption', '意外中断后自动提高档位'));
    let value = null, request = null, saving = false, disposed = false;
    const tip = (el, name, use, why = '') => { el.dataset.fvTipName = name; el.dataset.fvTipUse = use; if (why) el.dataset.fvTipWhy = why; else delete el.dataset.fvTipWhy; };
    tip(revert, tt('Revert', '还原'), tt('Back to the saved setting', '回到已保存的设置'));
    tip(automatic, tt('Raise automatically', '自动提高'), tt('Raise the level after an unexpected interruption', '意外中断后自动提高档位'));
    const dirty = () => value && (Number(level.value) !== value.level || automatic.checked !== value.automatic);
    const paint = () => {
        const levelName = value?.levels?.[Number(level.value)]?.[cn ? 'zh' : 'en'];
        title.textContent = Number(level.value) ? `${tt('Compatibility level', '兼容档位')} · ${level.selectedOptions[0]?.textContent || ''}` : tt('Compatibility level', '兼容档位');
        if (value?.available) line.textContent = levelName || '';
        const changed = dirty();
        save.disabled = saving || !changed || !value?.available; revert.disabled = saving || !changed;
        level.disabled = automatic.disabled = saving || !value?.available;
        if (!saving && changed && !status.dataset.kind) status.textContent = tt('Not saved yet', '尚未保存');
        if (!saving && !changed && !status.dataset.kind) status.textContent = '';
        tip(save, tt('Save', '保存'), tt('Use this level from the next generation', '下次生成起使用这个档位'), save.disabled && !saving ? tt('Change the level or the switch first', '先修改档位或开关') : '');
    };
    const say = (text, kind = '') => { status.textContent = text; status.className = 'st-slot' + (kind ? ' is-' + kind : ''); if (kind) status.dataset.kind = kind; else delete status.dataset.kind; };
    import('./settings_kit.js').then(module => {
        kit = module;
        tile.append(kit.icon('shield', 'fv-i s'));
        end.append(kit.segmented(level, {label: tt('Compatibility level', '兼容档位'), tips: {'0': tt('Normal speed', '正常速度'), '1': tt('A little slower, steadier', '稍慢，更稳'), '2': tt('Slower, steadier', '更慢，更稳'), '3': tt('Slowest, steadiest', '最慢，最稳')}}));
        autoEnd.append(kit.switchify(automatic));
    });
    level.onchange = () => { if (Number(level.value) === 0) automatic.checked = false; say(''); paint(); };
    automatic.onchange = () => { say(''); paint(); };
    revert.onclick = () => { if (!value) return; level.value = String(value.level); automatic.checked = value.automatic; say(''); paint(); };
    save.onclick = async () => {
        if (saving || !request) return;
        saving = true; say(tt('Saving…', '正在保存…')); paint();
        try {
            const saved = await request('save', {level: Number(level.value), automatic: automatic.checked});
            if (!saved.available) { say(unavailable(), 'warn'); return; }
            value = {...value, ...saved, level: Number(level.value), automatic: automatic.checked};
            say(tt('Saved · applies to the next generation', '已保存 · 下次生成生效'), 'ok');
            setTimeout(() => { if (status.dataset.kind === 'ok') say(''); paint(); }, 2400);
        } catch (error) { say(saveFailure(error, tt), 'warn'); }
        finally { saving = false; paint(); }
    };
    (async () => {
        try {
            request = await client(client0); value = await request('check');
            if (disposed) return;
            if (!value.available) { line.textContent = unavailable(); say(''); paint(); return; }
            level.value = String(value.level); automatic.checked = Boolean(value.automatic); paint();
        } catch (error) { line.textContent = readFailure(error, tt); line.className = 'is-warn'; paint(); }
    })();
    return {
        element,
        setUnified(unified) { for (const part of [head, autoRow, note, foot]) part.hidden = unified; mac.hidden = !unified; },
        dispose() { disposed = true; },
    };
}

async function openCompatibilitySheet() {
    if (current?.open) { current.focus(); return; }
    const panel = dialog(t('Compatibility', '兼容性')); current = panel;
    const status = element('p', t('Loading settings…', '正在读取设置…')); status.className = 'fv-muted';
    status.setAttribute('role', 'status');
    panel.actions.before(status);
    show(panel);
    try {
        const request = await client(), value = await request('check');
        if (!value.available) {
            status.textContent = unavailable();
            button(panel, t('Close', '关闭'), () => panel.close(), true).focus();
            return;
        }
        status.textContent = '';
        const name = element('p'); name.className = 'fv-level';
        const slider = element('input');
        slider.type = 'range'; slider.min = 0; slider.max = 3; slider.step = 1; slider.value = value.level;
        slider.setAttribute('aria-label', t('Compatibility level', '兼容性档位'));
        const autoLabel = element('label'), automatic = element('input');
        autoLabel.className = 'fv-check'; automatic.type = 'checkbox'; automatic.checked = value.automatic;
        autoLabel.append(automatic, element('span', t('Raise the level automatically after an unexpected interruption', '异常中断后自动提高兼容档位')));
        const note = element('p', t('Applies from the next generation. Higher levels have the GPU handle less at a time, so generation is slower and the picture differs slightly; resolution, duration and steps stay the same.', '从下一次生成起生效。档位越高，显卡每次处理的数据越少，生成越慢，画面也会有细微差别；分辨率、时长和步数不变。'));
        note.className = 'fv-muted';
        status.before(name, slider, autoLabel, note);
        const render = () => { name.textContent = value.levels[Number(slider.value)][cn ? 'zh' : 'en']; }; render();
        slider.oninput = () => { render(); if (Number(slider.value) === 0) automatic.checked = false; };
        let saving = false;
        panel.addEventListener('cancel', event => { if (saving) event.preventDefault(); });
        const close = panel.querySelector('.fv-close');
        const cancel = button(panel, t('Cancel', '取消'), () => panel.close());
        const save = button(panel, t('Save', '保存'), async () => {
            if (saving) return;
            saving = true;
            for (const control of [slider, automatic, save, cancel, close]) control.disabled = true;
            save.textContent = t('Saving…', '正在保存…');
            panel.setAttribute('aria-busy', 'true');
            panel.tabIndex = -1; panel.focus();
            status.textContent = ''; status.className = 'fv-muted';
            try {
                const saved = await request('save', {level: Number(slider.value), automatic: automatic.checked});
                if (!saved.available) { status.className = 'fv-failure'; status.textContent = unavailable(); return; }
                panel.close();
            }
            catch (error) { status.className = 'fv-failure'; status.textContent = saveFailure(error, t); }
            finally {
                saving = false;
                for (const control of [slider, automatic, save, cancel, close]) control.disabled = false;
                save.textContent = t('Save', '保存');
                panel.setAttribute('aria-busy', 'false');
                panel.removeAttribute('tabindex');
                if (panel.open) save.focus();
            }
        }, true);
    } catch (error) {
        status.textContent = readFailure(error, t);
        button(panel, t('Close', '关闭'), () => panel.close(), true).focus();
    }
}
// inFront: the notice waits until FreeVideo is in front (a launcher visit or an open FreeVideo view).
export async function notifyCompatibility({inFront = () => true} = {}) {
    try {
        const request=await client(), value=await request('check');
        if (!value.available || !value.notice) return;
        while (!inFront()) await new Promise(resolve => setTimeout(resolve, 2000));
        const panel = dialog(t('Compatibility enabled', '已开启兼容性设置'));
        const message = element('p', value.recovery_disabled
            ? t('Generation is slowed automatically for stability; you can change this in Settings.', '为了稳定，生成已自动放慢；可在设置里调整。')
            : value.recovery_successes_required === 1
                ? t('Generation is slowed automatically for stability; it speeds up after the next successful generation, or you can change this in Settings.',
                    '为了稳定，生成已自动放慢；下一次生成完成后会加快一些，也可在设置里调整。')
                : t('Generation is slowed automatically for stability; it speeds up after 3 successful generations in a row, or you can change this in Settings.',
                    '为了稳定，生成已自动放慢；连续完成 3 次后会加快一些，也可在设置里调整。'));
        panel.actions.before(message);
        const finish = async (settings) => {
            try { await request('ack', {notice_id: value.notice.id}); panel.close(); if (settings) await openCompatibility(); }
            catch (error) { panel.actions.before(element('p', error.message)); }
        };
        button(panel, t('Adjust settings', '调整设置'), () => finish(true));
        const ok = button(panel, t('OK', '知道了'), () => finish(false), true);
        show(panel, ok);
    } catch(error) { console.warn('FreeVideo compatibility check:',error.message); }
}
