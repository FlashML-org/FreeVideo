import {agentGet, agentSend} from './agent_session.js';
import {agentInterfaceState} from './agent_connect.js';

const PATH = '/freevideo/agent/v1/confirmations';
const DEFAULT_MINUTES = 5, MAX_MINUTES = 1440;
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

// What the controls show for the server's policy: whether a film asks first, and whether and after how many minutes
// a reshoot asks. `reshoot_seconds: null` is "never asks"; the minutes then stay at the last value.
export function confirmView(policy, minutes = DEFAULT_MINUTES) {
    const seconds = policy?.reshoot_seconds;
    const long = typeof seconds === 'number';
    return {start: policy?.start !== false, long, minutes: long ? Math.max(1, Math.round(seconds / 60)) : minutes};
}

// The one field that changed; the server keeps the other, so a policy set elsewhere is never overwritten.
export function confirmPatch(field, value) {
    if (field === 'start') return {start: value === true};
    if (field === 'long') return {reshoot_seconds: value.on ? value.minutes * 60 : null};
    throw new Error('Unknown confirmation field');
}

export function validMinutes(value) {
    const minutes = Number(value);
    return Number.isInteger(minutes) && minutes >= 1 && minutes <= MAX_MINUTES;
}

// Settings → AI Agent: when an agent has to wait for the user. The section stays hidden unless the Agent interface
// is on and the policy was read, so a page without it shows nothing half-working.
export function agentConfirmSettings(t, api) {
    const section = el('details'); section.hidden = true;
    section.append(el('summary', null, t('AI Agent', 'AI Agent')));
    const startBox = el('input'); startBox.type = 'checkbox';
    const startLabel = el('label', 'fv-check'); startLabel.append(startBox, el('span', null, t('Ask before an agent starts a film', 'Agent 开拍整部片前先询问')));
    const longBox = el('input'); longBox.type = 'checkbox';
    const minutesField = el('input', 'fv-number'); minutesField.type = 'number'; minutesField.min = '1'; minutesField.max = String(MAX_MINUTES); minutesField.step = '1';
    const longLabel = el('label', 'fv-check');
    longLabel.append(longBox, el('span', null, t('Ask before an agent reshoot that takes more than', 'Agent 重拍预计超过')), minutesField, el('span', null, t('minutes', '分钟时先询问')));
    const status = el('p', 'fv-muted'); status.setAttribute('role', 'status');
    section.append(startLabel, longLabel, status);

    let shown = {start: true, long: true, minutes: DEFAULT_MINUTES}, busy = false;
    const render = () => {
        startBox.checked = shown.start; longBox.checked = shown.long;
        minutesField.value = String(shown.minutes); minutesField.disabled = busy || !shown.long;
        startBox.disabled = longBox.disabled = busy;
    };
    async function save(patch, next) {
        busy = true; render(); status.textContent = '';
        try {
            const response = await agentSend(api, 'PUT', PATH, patch);
            if (!response.ok) throw new Error('Not saved');
            shown = confirmView(await response.json(), next.minutes);
            status.textContent = t('Saved', '已保存');
        } catch { status.textContent = t('Could not save. The previous setting is kept.', '保存失败，仍保留原设置。'); }
        busy = false; render();
    }
    startBox.onchange = () => save(confirmPatch('start', startBox.checked), shown);
    longBox.onchange = () => save(confirmPatch('long', {on: longBox.checked, minutes: shown.minutes}), shown);
    minutesField.onchange = () => {
        if (!validMinutes(minutesField.value)) { render(); status.textContent = t(`Enter a whole number from 1 to ${MAX_MINUTES}.`, `请输入 1 到 ${MAX_MINUTES} 的整数。`); return; }
        const minutes = Number(minutesField.value);
        return save(confirmPatch('long', {on: true, minutes}), {...shown, minutes});
    };

    const ready = (async () => {
        try {
            if (await agentInterfaceState(api) !== 'on') return;
            const response = await agentGet(api, PATH);
            if (!response.ok) return;
            shown = confirmView(await response.json()); render();
            section.hidden = false;
        } catch { /* The section simply stays hidden. */ }
    })();
    return {element: section, ready};
}
