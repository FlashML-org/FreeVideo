import {longGeneration} from './phone_model.js';
import {agentGet, agentPost} from './agent_session.js';
import {qrcodegen} from './qrcodegen.js';
const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('./lan_access.css', import.meta.url).href; document.head.append(css);
const seenKey = 'freevideo.phone.opened';
export function qrMatrix(link) { return qrcodegen.QrCode.encodeText(link, qrcodegen.QrCode.Ecc.MEDIUM); }
function drawQr(canvas, link) {
    const qr = qrMatrix(link), scale = Math.max(2, Math.floor(336 / (qr.size + 8))), size = (qr.size + 8) * scale;
    canvas.width = canvas.height = size; const ctx = canvas.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, size, size); ctx.fillStyle = '#000';
    for (let y = 0; y < qr.size; y++) for (let x = 0; x < qr.size; x++) if (qr.getModule(x, y)) ctx.fillRect((x + 4) * scale, (y + 4) * scale, scale, scale);
}
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const button = (cls, label, action) => { const b = el('button', cls, label); b.type = 'button'; b.onclick = action; return b; };
export function createLanAccess(t, api) {
    const element = el('div', 'fv-lan'), trigger = button('fv-lan-trigger', '', toggle), panel = el('section', 'fv-lan-panel');
    const label = t('Phone access', '手机连接'); trigger.setAttribute('aria-label', label); trigger.setAttribute('aria-expanded', 'false'); trigger.setAttribute('aria-haspopup', 'dialog');
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS(svg.namespaceURI || 'http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', 'M7 2h10a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM10 18h4'); svg.append(path); trigger.append(svg);
    const dot = el('i', 'fv-lan-dot'); dot.hidden = true; dot.setAttribute('aria-hidden', 'true'); trigger.append(dot);
    panel.hidden = true; panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', label);
    const head = el('div', 'fv-lan-heading'), toggleButton = button('fv-lan-switch', '', changeEnabled);
    toggleButton.setAttribute('role', 'switch'); toggleButton.setAttribute('aria-label', label); toggleButton.setAttribute('aria-checked', 'false'); toggleButton.disabled = true;
    head.append(el('strong', '', label), toggleButton);
    const starting = el('p', 'fv-lan-starting'), content = el('div', 'fv-lan-content'), qr = el('canvas', 'fv-lan-qr'); qr.setAttribute('role', 'img'); qr.setAttribute('aria-label', t('Phone access QR code', '手机连接二维码'));
    const copy = button('fv-lan-copy', t('Copy link', '复制链接'), async () => {
        try {
            if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(state.link);
            else { const area = el('textarea'); area.value = state.link; panel.append(area); area.select(); const ok = document.execCommand('copy'); area.remove(); if (!ok) throw new Error(); }
            copy.textContent = t('Copied', '已复制'); clearTimeout(copied); copied = setTimeout(() => { copy.textContent = t('Copy link', '复制链接'); }, 1500);
        } catch { fail(); }
    });
    const passwordRow = el('div', 'fv-lan-password'), passwordState = el('span'), set = button('fv-lan-set', t('Set', '设置'), () => { form.hidden = false; password.focus(); });
    passwordRow.append(passwordState, set);
    const form = el('form', 'fv-lan-form'), field = el('div', 'fv-lan-field'), password = el('input'); password.type = 'password'; password.autocomplete = 'new-password'; password.setAttribute('aria-label', t('New password', '新密码'));
    const eye = button('fv-lan-eye', '', () => { password.type = password.type === 'password' ? 'text' : 'password'; eye.setAttribute('aria-label', password.type === 'password' ? t('Show password', '显示密码') : t('Hide password', '隐藏密码')); }); eye.setAttribute('aria-label', t('Show password', '显示密码'));
    const eyeSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'), eyePath = document.createElementNS('http://www.w3.org/2000/svg', 'path'); eyeSvg.setAttribute('viewBox', '0 0 24 24'); eyeSvg.setAttribute('aria-hidden', 'true'); eyePath.setAttribute('d', 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zm10 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'); eyeSvg.append(eyePath); eye.append(eyeSvg); field.append(password, eye);
    const actions = el('div', 'fv-lan-actions'), cancel = button('', t('Cancel', '取消'), () => { form.hidden = true; password.value = ''; set.focus(); }), save = button('fv-lan-save', t('Save', '保存'), () => form.requestSubmit()); save.disabled = true;
    password.oninput = () => { save.disabled = [...password.value].length < (state.password_min_length || 6) || writing; };
    actions.append(cancel, save); form.append(field, actions); form.hidden = true;
    const error = el('p', 'fv-lan-error', t('Please try again.', '请重试。')); error.hidden = true; error.setAttribute('role', 'status');
    content.append(qr, el('p', 'fv-lan-scan', t('Connect to the same Wi-Fi, then scan', '手机连同一个 Wi-Fi 后扫码')), copy, passwordRow, form);
    const tip = el('span', 'fv-lan-tip', label); tip.setAttribute('role', 'tooltip'); tip.hidden = true;
    // Why phone access is not open: the launcher's reason, or no link after openWait.
    const problem = el('div', 'fv-lan-problem'), problemText = el('p', 'fv-lan-reason'), retry = button('fv-lan-retry', t('Try again', '重试'), () => open());
    problem.append(problemText, retry); problem.hidden = true; problemText.setAttribute('role', 'status');
    const reasons = {
        ports_in_use: t('The ports for phone access are in use or reserved by the system.', '手机连接所需的端口已被占用或被系统保留。'),
        comfy_on_network: t('This ComfyUI is already open to the network. Restart it on this computer only, then try again.', 'ComfyUI 已直接对网络开放。请改为仅在本机打开后重试。'),
        no_address: t('No LAN address found. Connect to Wi-Fi or a wired network first.', '未找到局域网地址，请先连接 Wi-Fi 或有线网络。'),
        failed: t('Phone access could not be opened.', '手机连接无法打开。'),
        late: t('Phone access has not opened yet. Make sure the FreeVideo launcher is running.', '手机连接尚未打开。请确认 FreeVideo 启动器正在运行。'),
        unavailable: t('Phone access needs FreeVideo started from its launcher on this computer.', '手机连接需要通过本机的 FreeVideo 启动器启动。'),
    };
    const openWait = 30000;
    panel.append(head, starting, problem, content, error); element.append(trigger, panel, tip);
    let state = {enabled: false, link: '', password_set: false}, seen = false, disposed = false, writing = false, desired = null, reading = false, timer = null, copied = null, drawn = '', since = 0;
    try { seen = localStorage.getItem(seenKey) === '1'; } catch {}
    const windows = /Win/i.test(globalThis.navigator?.platform || globalThis.navigator?.userAgent || '');
    function fail() { error.hidden = false; }
    function position() {
        if (panel.hidden) return;
        const box = trigger.getBoundingClientRect(); panel.style.top = `${box.bottom + 8}px`;
        panel.style.left = `${Math.max(8, Math.min(box.right - 264, window.innerWidth - 272))}px`;
    }
    function render() {
        // No launcher installation: the switch cannot work from this page, so say why instead of "Please try again".
        const unavailable = state.available === false, reason = unavailable ? 'unavailable' : reasons[state.reason] ? state.reason : '', on = desired ?? (state.enabled || state.starting);
        const linked = !!(state.enabled && state.link), waiting = on && !linked && !reason;
        if (!waiting) since = 0; else if (!since) since = Date.now();
        const late = waiting && Date.now() - since >= openWait, pending = waiting && !late, shown = reason || (late ? 'late' : '');
        toggleButton.setAttribute('aria-checked', String(!!on)); toggleButton.disabled = writing || desired != null || unavailable;
        content.hidden = !on || !linked; starting.hidden = !pending;
        problem.hidden = !shown; problemText.textContent = shown ? reasons[shown] : ''; retry.disabled = writing; retry.hidden = unavailable;
        starting.replaceChildren();
        if (pending && windows) {
            if (t('en', 'zh') === 'zh') { starting.append(document.createTextNode('Windows 会弹出确认框，'), el('br'), el('span', '', '请勾选“专用网络”，再点“允许访问”。')); }
            else starting.textContent = 'Windows will ask for permission. Check "Private networks", then click "Allow access".';
        } else if (pending) starting.textContent = t('Opening…', '正在打开…');
        passwordState.textContent = state.password_set ? t('Password: on', '密码：已设置') : t('Password: not set', '密码：未设置');
        set.textContent = state.password_set ? t('Change', '修改') : t('Set', '设置');
        if (!content.hidden && state.link !== drawn) { try { drawQr(qr, state.link); drawn = state.link; } catch { fail(); } }
        position();
    }
    async function read() {
        clearTimeout(timer); if (disposed || panel.hidden || reading) return; reading = true;
        try {
            const response = await agentGet(api, '/freevideo/lan', {signal: AbortSignal.timeout(10000)});
            if (!response.ok) throw new Error(); const value = await response.json(); if (disposed || writing) return;
            state = value; if (state.reason || desired === state.enabled && (!desired || state.link)) desired = null;
            error.hidden = true; render();
        } catch { if (!disposed) fail(); }
        finally { reading = false; if (!disposed && !panel.hidden) timer = setTimeout(read, document.hidden ? 10000 : 2000); }
    }
    trigger.onpointerenter = trigger.onfocus = () => { if (!panel.hidden) return; const box = trigger.getBoundingClientRect(); tip.style.top = `${box.bottom + 8}px`; tip.style.right = `${Math.max(8, window.innerWidth - box.right)}px`; tip.hidden = false; };
    trigger.onpointerleave = trigger.onblur = () => { tip.hidden = true; };
    function close() { tip.hidden = true; panel.hidden = true; trigger.setAttribute('aria-expanded', 'false'); clearTimeout(timer); form.hidden = true; password.value = ''; }
    function toggle() {
        tip.hidden = true;
        if (!panel.hidden) { close(); return; }
        panel.hidden = false; trigger.setAttribute('aria-expanded', 'true'); seen = true; dot.hidden = true;
        try { localStorage.setItem(seenKey, '1'); } catch {}
        window.dispatchEvent(new Event('freevideo-phone-opened'));
        render(); read();
    }
    async function write(body) {
        writing = true; error.hidden = true; render();
        try {
            const response = await agentPost(api, '/freevideo/lan', body);
            if (response.status === 409 && (await response.json().catch(() => null))?.error?.code === 'lan_unavailable') { if (!disposed) state = {...state, available: false}; return false; }
            if (!response.ok) throw new Error();
            const value = await response.json(); if (disposed) return false;
            if (typeof value.enabled === 'boolean') state = {...state, ...value}; return true;
        } catch { if (!disposed) fail(); return false; }
        finally { writing = false; if (!disposed) render(); }
    }
    async function changeEnabled() { await open(!(state.enabled || state.starting)); }
    async function open(enabled = true) { since = 0; desired = enabled; if (!await write({enabled})) desired = null; render(); read(); }
    form.onsubmit = async event => { event.preventDefault(); if (save.disabled || writing) return; save.disabled = true; if (await write({password: password.value})) { password.value = ''; form.hidden = true; set.focus(); read(); } else password.oninput(); };
    function observe(value) { if (!seen && longGeneration(value)) dot.hidden = false; }
    const opened = () => { seen = true; dot.hidden = true; };
    const storage = event => { if (event.key === seenKey && event.newValue === '1') opened(); };
    window.addEventListener('freevideo-phone-opened', opened); window.addEventListener('storage', storage);
    const progress = event => observe(event.detail), outside = event => { if (!element.contains(event.target)) close(); }, keys = event => { if (!panel.hidden && event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); trigger.focus(); } };
    api.addEventListener?.('freevideo_progress', progress); document.addEventListener('pointerdown', outside, true); document.addEventListener('keydown', keys, true); window.addEventListener('resize', position);
    return {element, observe, dispose() { disposed = true; close(); clearTimeout(copied); api.removeEventListener?.('freevideo_progress', progress); document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', keys, true); window.removeEventListener('resize', position); window.removeEventListener('freevideo-phone-opened', opened); window.removeEventListener('storage', storage); }};
}
