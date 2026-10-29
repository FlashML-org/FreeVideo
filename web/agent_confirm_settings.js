import {agentGet, agentSend} from './agent_session.js';
import {agentInterfaceState} from './agent_connect.js';

const PATH = '/freevideo/agent/v1/confirmations';
const DEFAULT_SECONDS = 300, MAX_MINUTES = 1440;
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

// What the controls show for the server's policy: whether a film asks first, and whether and after how long a
// reshoot asks. `reshoot_seconds: null` is "never asks"; the seconds then stay at the last value. The exact
// seconds are kept, so a limit set elsewhere that is not a whole minute survives switching the control off and on.
export function confirmView(policy, seconds = DEFAULT_SECONDS) {
    const limit = policy?.reshoot_seconds;
    const long = typeof limit === 'number';
    return {start: policy?.start !== false, long, seconds: long ? limit : seconds};
}

export const minutesOf = seconds => Number((seconds / 60).toFixed(2));

// The one field that changed; the server keeps the other, so a policy set elsewhere is never overwritten.
export function confirmPatch(field, value) {
    if (field === 'start') return {start: value === true};
    if (field === 'long') return {reshoot_seconds: value.on ? value.seconds : null};
    throw new Error('Unknown confirmation field');
}

// Minutes as typed: a number from 0 to 1440, decimals allowed, never an empty field.
export function validMinutes(value) {
    if (value === null || (typeof value === 'string' && !value.trim())) return false;
    const minutes = Number(value);
    return Number.isFinite(minutes) && minutes >= 0 && minutes <= MAX_MINUTES;
}

// Settings -> AI Agent: when an agent has to wait for the user. The section stays hidden unless the Agent interface
// is on and the policy was read, so a page without it shows nothing half-working.
export function agentConfirmSettings(t, api) {
    const section = el('details'); section.hidden = true;
    section.append(el('summary', null, t('AI Agent', 'AI Agent')));
    const startBox = el('input'); startBox.type = 'checkbox';
    const startLabel = el('label', 'fv-check'); startLabel.append(startBox, el('span', null, t('Ask before an agent starts a film', 'Agent 开拍整部片前先询问')));
    const longBox = el('input'); longBox.type = 'checkbox';
    const longLabel = el('label', 'fv-check'); longLabel.append(longBox, el('span', null, t('Ask before a long agent reshoot', 'Agent 重拍耗时较长时先询问')));
    const minutesField = el('input', 'fv-minutes'); minutesField.type = 'number'; minutesField.min = '0'; minutesField.max = String(MAX_MINUTES); minutesField.step = 'any';
    const minutesLabel = el('label', null, t('Long means at least', '达到')); minutesLabel.append(minutesField, el('span', null, t('minutes', '分钟')));
    const status = el('p', 'fv-muted'); status.setAttribute('role', 'status');
    section.append(startLabel, longLabel, minutesLabel, status);

    let shown = {start: true, long: true, seconds: DEFAULT_SECONDS}, busy = false;
    const render = () => {
        startBox.checked = shown.start; longBox.checked = shown.long;
        minutesField.value = String(minutesOf(shown.seconds)); minutesField.disabled = busy || !shown.long;
        startBox.disabled = longBox.disabled = busy;
    };
    const read = async () => {
        const response = await agentGet(api, PATH);
        if (!response.ok) throw new Error('Not read');
        return confirmView(await response.json(), shown.seconds);
    };
    async function save(patch) {
        // The control keeps what the user just chose while the save runs; it then shows what the server holds.
        busy = true; startBox.disabled = longBox.disabled = minutesField.disabled = true; status.textContent = '';
        try {
            const response = await agentSend(api, 'PUT', PATH, patch);
            if (!response.ok) throw new Error('Not saved');
            shown = confirmView(await response.json(), shown.seconds);
            status.textContent = t('Saved', '已保存');
        } catch {
            // A save whose answer was lost may still have taken effect, so the server is asked what it holds.
            try {
                shown = await read();
                status.textContent = t('Could not save. The setting shown is the one in effect.', '保存失败，下面显示的是当前生效的设置。');
            } catch { status.textContent = t('Could not confirm whether the change was saved.', '无法确认这次修改是否已保存。'); }
        }
        busy = false; render();
    }
    startBox.onchange = () => save(confirmPatch('start', startBox.checked));
    longBox.onchange = () => save(confirmPatch('long', {on: longBox.checked, seconds: shown.seconds}));
    minutesField.onchange = () => {
        if (!validMinutes(minutesField.value)) { render(); status.textContent = t(`Enter a number from 0 to ${MAX_MINUTES}.`, `请输入 0 到 ${MAX_MINUTES} 之间的数。`); return; }
        const seconds = Math.round(Number(minutesField.value) * 60);
        return save(confirmPatch('long', {on: true, seconds}));
    };

    const ready = (async () => {
        try {
            if (await agentInterfaceState(api) !== 'on') return;
            shown = await read(); render();
            section.hidden = false;
        } catch { /* The section simply stays hidden. */ }
    })();
    return {element: section, ready};
}
