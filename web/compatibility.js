import { api } from '../../scripts/api.js';

const languageOverride = typeof location !== 'undefined'
    ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en'
    && String(navigator.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;
const unavailable = () => t('Device information is unavailable; compatibility settings are off.', '无法获取设备信息，兼容性设置已关闭。');
let current;
const element = (tag, text) => { const node = document.createElement(tag); if (text) node.textContent = text; return node; };
async function client() {
    const response = await api.fetchApi('/freevideo/setup');
    const info = await response.json();
    if (!response.ok) throw new Error(info.error || response.statusText);
    return async (action, value = {}) => {
        const response = await api.fetchApi('/freevideo/setup/compatibility-' + action, {
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
export async function openCompatibility() {
    if (current?.open) { current.focus(); return; }
    const panel = dialog(t('Compatibility', '兼容性')); current = panel;
    const status = element('p', t('Loading settings…', '正在读取设置…')); status.className = 'fv-muted';
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
        button(panel, t('Cancel', '取消'), () => panel.close());
        button(panel, t('Save', '保存'), async () => {
            try {
                const saved = await request('save', {level: Number(slider.value), automatic: automatic.checked});
                if (!saved.available) { slider.disabled = true; automatic.disabled = true; status.textContent = unavailable(); return; }
                panel.close();
            }
            catch (error) { status.textContent = error.message; }
        }, true);
    } catch (error) {
        status.textContent = error.message;
        button(panel, t('Close', '关闭'), () => panel.close(), true).focus();
    }
}
export async function notifyCompatibility() {
    try {
        const request=await client(), value=await request('check');
        if (!value.available || !value.notice) return;
        const panel = dialog(t('Compatibility enabled', '已开启兼容性设置'));
        const message = element('p', t(`The previous generation ended unexpectedly. Compatibility level ${value.level} is now enabled. You can adjust or disable it in settings.`,
            `上次生成未正常结束，已自动开启兼容性设置（第 ${value.level} 档）。你可以在设置里调节或关闭。`));
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
