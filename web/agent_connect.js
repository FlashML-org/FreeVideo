import {issueButton} from './issue_button.js';
import {tip} from './autopilot_tips.js';
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

// The one place a page says whether an assistant is connected. Program names (and the ids some programs report)
// are not product words, so the label says only whether one is there; `name` stays for reports.
export function connectionLabel(agent, t) {
    if (agent?.connected !== true) return {color: 'gray', text: t('Not connected', '未连接'), name: null};
    return {color: 'green', text: t('Assistant connected', '助手已连接'), name: String(agent.name || '').trim() || null};
}

export function connectionSentence(address, t) {
    const url = address + '/freevideo/agent';
    return t(`Connect to FreeVideo: open ${url} and follow the instructions.`, `请连接 FreeVideo：打开 ${url} 并按说明操作。`);
}

// Whether this FreeVideo has the Agent interface on: 'on', 'off' or 'unknown'. Only an explicit `enabled: false`
// is off; a check that failed (no answer, an error status, an unreadable reply) is not an answer.
export async function agentInterfaceState(api) {
    try {
        const response = await api.fetchApi('/freevideo/agent', {cache: 'no-store'});
        if (!response.ok) return 'unknown';
        const enabled = (await response.json())?.enabled;
        return enabled === true ? 'on' : enabled === false ? 'off' : 'unknown';
    } catch { return 'unknown'; }
}

// What to tell the user when the lookup got no usable answer at all (no refusal came back): the request failed, the
// reply could not be read, or it held no address. The page cannot tell these apart for the user, and in each the
// way back is the same.
export function noAnswerHint(kind, t) {
    return kind ? t('Could not reach FreeVideo on this computer. Make sure it is still running, then retry.', '无法连接本机的 FreeVideo 服务，请确认它仍在运行后重试。') : '';
}

// How this page was opened, as a kind only: a computer or network name does not belong in a report that may be
// sent along. '' when the page has no address to tell.
export function addressKind(host) {
    const name = String(host || '').replace(/^\[|\]$/g, '').toLowerCase();
    if (!name) return '';
    if (name === 'localhost' || name === '::1' || /^127(\.\d{1,3}){3}$/.test(name)) return 'localhost';
    return /^\d{1,3}(\.\d{1,3}){3}$/.test(name) || name.includes(':') ? 'IP address' : 'name';
}

// What to tell the user when the address lookup is refused: only refusals that say what to do are named; any other
// failure keeps the plain "could not get address". A page opened by any address but localhost is refused for that
// alone when FreeVideo runs on the same computer, so then the hint names the localhost address to open, as plain
// text: a link would not open on another device.
export function localhostAddress(location = globalThis.location) {
    return location?.protocol ? `${location.protocol}//localhost${location.port ? ':' + location.port : ''}` : '';
}

export function refusalHint(code, t, kind = addressKind(globalThis.location?.hostname), address = localhostAddress()) {
    if (code === 'origin_forbidden') return t('Open FreeVideo on this computer with a localhost address, then retry.', '请在本机使用 localhost 地址打开 FreeVideo，然后重试。');
    if (code === 'local_only') return kind && kind !== 'localhost' && address
        ? t(`On the computer that runs FreeVideo, open ${address}, then retry.`, `请在运行 FreeVideo 的电脑上打开 ${address}，然后重试。`)
        : t('An assistant can only be connected on the computer that runs FreeVideo.', '只能在运行 FreeVideo 的电脑上连接助手。');
    if (code === 'not_ready') return t('The assistant connection service is not ready. Restart FreeVideo, then retry.', '助手连接服务尚未就绪，请重启 FreeVideo 后重试。');
    return '';
}

export async function agentInterfaceEnabled(api) { return await agentInterfaceState(api) === 'on'; }

// Shows the card's way back (a quiet "Back to chats") while `action` is set; hides it with null.
export function connectCardBack(card, t, action = null) {
    const back = card?.element?.querySelector('.fv-agent-connect-back');
    if (!back) return;
    back.hidden = !action; back.onclick = action;
    back.textContent = t('Back to chats', '返回对话');
    tip(back, 'backToChats', t);
}

// Why no assistant is connected, from the server's reason, with the next step; null while one is connected.
// report: the state is a failure the user may want to send us (a refused key, a state the page cannot name); an
// assistant that went quiet has most likely finished or been closed, which is not one.
export function linkReason(agent, t) {
    if (agent?.connected === true) return null;
    const reconnect = t('Choose Reconnect and paste the steps to your assistant.', '请点“重新连接”，把连接说明粘贴给助手。');
    if (agent?.reason === 'user') return {reason: 'user', report: false,
        text: t('You disconnected the assistant.', '已断开与助手的连接。'),
        next: t('To connect it again, choose Reconnect, or ask it in its own window to continue.', '如需重新连接，请点“重新连接”，或在助手的窗口中让它继续。')};
    if (agent?.reason === 'refused') return {reason: 'refused', report: true,
        text: t('The assistant used a connection key that is no longer valid, so FreeVideo refused it.', '助手使用的连接密钥已失效，FreeVideo 拒绝了它。'),
        next: t('Choose Reconnect and paste the steps to it again. If it is still refused, restart the assistant.', '请点“重新连接”，把连接说明再粘贴给它一次；仍被拒绝时，请重启助手。')};
    if (agent?.reason === 'quiet') {
        const minutes = Math.max(10, Math.round((Number(agent.quiet_s) || 0) / 60)), hours = Math.round(minutes / 60);
        const span = minutes >= 120 ? t(`${hours} hours`, `${hours} 小时`) : t(`${minutes} minutes`, `${minutes} 分钟`);
        return {reason: 'quiet', report: false,
            text: t(`The assistant has not contacted FreeVideo for ${span}; it may have finished or been closed.`, `助手已有 ${span}没有联系 FreeVideo，可能已经结束或被关闭。`),
            next: reconnect};
    }
    if (agent?.reason === 'never') return {reason: 'never', report: false,
        text: t('No assistant has connected since FreeVideo started.', 'FreeVideo 启动后还没有助手连接。'), next: reconnect};
    return {reason: 'unknown', report: true, text: t('The assistant is not connected.', '助手未连接。'), next: reconnect};
}

// The reason above the card's button when the card stands in for a connection that ended (not on a first visit).
export function connectCardReason(card, t, agent) {
    const line = card?.element?.querySelector('.fv-agent-connect-reason');
    if (!line) return;
    const why = linkReason(agent, t);
    line.hidden = !why || why.reason === 'never' || why.reason === 'unknown';
    line.textContent = line.hidden ? '' : why.text;
}

// The connection light's menu. Connected: Disconnect, and the connection steps. Not connected: why, the next step,
// Reconnect and, for a failure, Report a problem. disconnect() sends the page's request (with its page session, which
// this card module never takes itself) and returns the response; steps() opens the connect card; changed() gets the
// state after a disconnect. The menu lives in `host` and opens beside `anchor` (the light).
export function agentLinkMenu(t, host, anchor, {disconnect: send, steps = () => {}, changed = () => {}} = {}) {
    const menu = el('div', 'fv-agent-link-menu'); menu.hidden = true;
    menu.setAttribute('role', 'dialog'); menu.setAttribute('aria-label', t('Assistant connection', '助手连接'));
    menu.id = `fv-agent-link-menu-${Math.random().toString(36).slice(2, 8)}`;
    const why = el('p', 'fv-agent-link-why'), next = el('p', 'fv-agent-link-next'), error = el('p', 'fv-agent-link-error');
    error.setAttribute('role', 'alert');
    const item = (text, key, action, kind = '') => {
        const button = el('button', `fv-agent-link-item${kind ? ' fv-agent-link-' + kind : ''}`, text); button.type = 'button';
        button.onclick = action; tip(button, key, t); return button;
    };
    const disconnect = item(t('Disconnect', '断开'), 'disconnect', () => void drop(), 'danger');
    const howTo = item(t('Connection steps', '连接方式'), 'connectSteps', () => { close(); steps(); });
    // The way out of a lost connection is the menu's main action, before Report a problem.
    const reconnect = item(t('Reconnect', '重新连接'), 'reconnect', () => { close(); steps(); }, 'main');
    menu.append(why, next, error, disconnect, howTo, reconnect);
    host.append(menu);
    anchor.setAttribute('aria-haspopup', 'dialog'); anchor.setAttribute('aria-controls', menu.id); anchor.setAttribute('aria-expanded', 'false');
    let agent = {}, issue = null, issueText = '', failure = null, busy = false;
    const report = message => issueButton(t, () => ({where: ['Assistant connection', '助手连接'], message, kind: failure?.code || agent?.reason || null}));
    function render() {
        const connected = agent?.connected === true, reason = linkReason(agent, t);
        why.hidden = next.hidden = connected; why.textContent = reason?.text || ''; next.textContent = reason?.next || '';
        disconnect.hidden = howTo.hidden = !connected; reconnect.hidden = connected;
        disconnect.disabled = busy; disconnect.setAttribute('aria-busy', String(busy));
        disconnect.textContent = busy ? t('Disconnecting…', '正在断开…') : t('Disconnect', '断开');
        error.hidden = !failure; error.textContent = failure?.text || '';
        const message = failure ? failure.text : !connected && reason?.report ? reason.text : '';
        // Kept while its sentence stays the same, so a reading that changes nothing keeps the focus where it was.
        if (message !== issueText) { issue?.remove(); issue = message ? report(message) : null; issueText = message; if (issue) menu.append(issue); }
    }
    // A refusal says what to do when it can; otherwise the status and the way to report it.
    async function drop() {
        if (busy) return;
        busy = true; failure = null; render();
        try {
            const response = await send();
            const value = await response.json().catch(() => null);
            if (!response.ok) {
                const code = String(value?.error?.code || '').slice(0, 40) || null;
                failure = {code, text: refusalHint(code, t) || t(`Could not disconnect (HTTP ${response.status}). Retry, or report it.`, `断开失败（HTTP ${response.status}）。请重试，或提交问题。`)};
                return;
            }
            close(); changed(value?.agent || null);
        } catch {
            failure = {code: 'unreachable', text: noAnswerHint('unreachable', t)};
        } finally { busy = false; if (!menu.hidden) render(); }
    }
    function place() {
        const box = host.getBoundingClientRect?.(), at = anchor.getBoundingClientRect?.();
        if (!box || !at) return;
        // Local dimensions also handle the view's entry scale.
        const sx = box.width / (host.offsetWidth || box.width) || 1, sy = box.height / (host.offsetHeight || box.height) || 1;
        const width = menu.offsetWidth || 0, height = menu.offsetHeight || 0, room = host.clientHeight || box.height / sy;
        const top = (at.top - box.top) / sy, bottom = (at.bottom - box.top) / sy;
        // Up from a light at the foot of the chats, down from one at the top of a narrow window.
        const up = bottom > room / 2;
        menu.style.left = `${Math.max(8, Math.min((at.left - box.left) / sx, (host.clientWidth || box.width / sx) - width - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(up ? top - height - 8 : bottom + 8, room - height - 8))}px`;
    }
    const outside = event => { if (!menu.contains(event.target) && !anchor.contains(event.target)) close(); };
    const keys = event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); } };
    function open(state) {
        agent = state || {}; failure = null; menu.hidden = false; anchor.setAttribute('aria-expanded', 'true');
        render(); place();
        globalThis.document?.addEventListener?.('pointerdown', outside, true);
        menu.addEventListener('keydown', keys);
        [disconnect, howTo, reconnect].find(button => !button.hidden)?.focus?.({preventScroll: true});
    }
    function close(restore = false) {
        if (menu.hidden) return;
        menu.hidden = true; anchor.setAttribute('aria-expanded', 'false');
        globalThis.document?.removeEventListener?.('pointerdown', outside, true);
        menu.removeEventListener('keydown', keys);
        if (restore) anchor.focus?.({preventScroll: true});
    }
    // The page's reading changed while the menu is open: a disconnect elsewhere, or the assistant coming back.
    function update(state) { if (menu.hidden) return; agent = state || {}; render(); place(); }
    return {element: menu, open, close, update, toggle: state => menu.hidden ? open(state) : close(true), get isOpen() { return !menu.hidden; }};
}

const ICONS = {plug: ['M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0V7zM12 16v5', 1.8], copy: ['M11 9h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-7a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2zM5 15V5a2 2 0 0 1 2-2h8', 2],
    retry: ['M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5', 2]};
function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', ICONS[name][0]);
    svg.setAttribute('stroke-width', String(ICONS[name][1])); svg.append(path);
    return svg;
}

// The card alone: who can connect and one button that copies the sentence to paste to the agent. The sentence
// stays out of sight; it shows, selected, only when the browser refuses the clipboard. shown() says whether the
// user can see the card now; the surface calls sync() whenever that may have changed.
// A failed check leaves the card up, so its own retry is the way back; an address that loads then asks again and
// reports the answer to changed().
export function agentConnectCard(t, api, shown = () => true, changed = () => {}) {
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('./agent_connect.css', import.meta.url).href; document.head.append(css);
    const body = el('section', 'fv-agent-connect-body');
    const mark = el('span', 'fv-agent-connect-icon'); mark.append(icon('plug'));
    const title = el('h2', null, t('Connect an assistant', '连接助手'));
    const intro = el('p', 'fv-agent-connect-intro', t('Once connected, describe the video you want. It generates and edits it for you.', '连上后，说想做什么视频，它来生成和剪辑。'));
    const actions = el('div', 'fv-agent-connect-actions');
    const button = el('button', 'fv-primary'); button.type = 'button'; button.disabled = true;
    // The button's picture follows what it does: copy, or retry once the lookup has failed.
    const label = el('span'), pictures = {copy: icon('copy'), retry: icon('retry')}; button.append(pictures.copy, label);
    for (const [name, picture] of Object.entries(pictures)) picture.dataset.icon = name;
    let issue = null;
    const arrow = el('span', 'fv-agent-connect-arrow', '→'); arrow.setAttribute('aria-hidden', 'true');
    // The arrow and its words wrap as one piece.
    const next = el('span', 'fv-agent-connect-next'); next.append(arrow, el('span', 'fv-agent-connect-hint', t('Paste it to your assistant', '粘贴给助手')));
    actions.append(button, next);
    const sentence = el('p', 'fv-agent-sentence'); sentence.hidden = true;
    const status = el('p', 'fv-agent-connect-status', t('Getting the connection address…', '正在获取连接地址…')); status.setAttribute('role', 'status');
    // The way back to the chats when the card was opened from a disconnected chat (see connectCardBack).
    const back = el('button', 'fv-agent-connect-back fv-quiet'); back.type = 'button'; back.hidden = true;
    // Why the last connection ended, when the card stands in for it (see connectCardReason).
    const reason = el('p', 'fv-agent-connect-reason'); reason.hidden = true;
    body.append(mark, title, intro, reason, actions, sentence, status, back);

    let feedback = false, text = '', failures = 0, refusal = null, noAnswer = null, wantFocus = false;
    let timer, feedbackTimer, controller, generation = 0;
    const opened = addressKind(globalThis.location?.hostname);
    const active = () => body.isConnected && shown();
    const render = () => {
        label.textContent = !text && failures >= 3 ? t('Could not get address. Retry', '获取失败，重试') : feedback ? t('Copied', '已复制') : t('Copy', '复制');
        const picture = pictures[!text && failures >= 3 ? 'retry' : 'copy'];
        if (button.firstChild !== picture) button.firstChild.replaceWith(picture);
        if (!text && failures >= 3) {
            if (!issue) { issue = issueButton(t, () => ({where: ['Assistant connection', '助手连接'], message: refusal ? `${label.textContent} (HTTP ${refusal.status}${refusal.code ? ' ' + refusal.code : ''}${opened ? ', opened by ' + opened : ''})` : label.textContent, kind: refusal?.code || noAnswer})); button.after(issue); }
        } else if (issue) { issue.remove(); issue = null; }
        next.hidden = !!issue;
        button.disabled = !text && failures < 3;
        button.title = !text && failures < 3 ? t('Getting the connection address…', '正在获取连接地址…') : '';
        tip(button, !text && failures >= 3 ? 'retryAddress' : 'copyConnect', t, {why: button.disabled ? t('Getting the connection address…', '正在获取连接地址…') : ''});
        // Asked for focus (Reconnect from the light) while the address was still loading: it goes there once usable.
        if (wantFocus && !button.disabled && active()) { wantFocus = false; button.focus?.({preventScroll: true}); }
        status.hidden = !!text; status.textContent = !text && failures < 3 ? t('Getting the connection address…', '正在获取连接地址…') : !text ? refusalHint(refusal?.code, t) || noAnswerHint(noAnswer, t) : '';
    };
    render();

    button.onclick = async () => {
        if (!text) { failures = 0; render(); await load(); return; }
        try { await navigator.clipboard.writeText(text); sentence.hidden = true; }
        catch {
            sentence.textContent = text; sentence.hidden = false;
            const range = document.createRange(); range.selectNodeContents(sentence);
            const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        }
        if (!active()) return;
        feedback = true; render();
        clearTimeout(feedbackTimer);
        feedbackTimer = setTimeout(() => { feedback = false; render(); }, 2000);
    };

    function stop() {
        generation++; clearTimeout(timer); timer = null;
        controller?.abort(); controller = null;
        clearTimeout(feedbackTimer); feedback = false; render();
    }
    // Only the sentence is read here, until it is known; who is connected comes from the surface that holds the card.
    async function load() {
        const current = generation;
        const request = new AbortController(); controller = request; refusal = null; noAnswer = null;
        // What the lookup lacked if it fails without a refusal: the request itself fails first, then the reply is read.
        let lacked = 'unreachable';
        try {
            // The sentence carries no parameter: the server gives the agent the language this card showed.
            const response = await api.fetchApi('/freevideo/agent/connect?lang=' + t('en', 'zh'), {cache: 'no-store', signal: request.signal});
            const connect = await response.json().catch(() => null);
            if (current !== generation) return;
            if (!response.ok || !connect?.address) {
                refusal = !response.ok ? {status: response.status, code: String(connect?.error?.code || '').slice(0, 40) || null} : null;
                lacked = connect ? 'no_address' : 'unreadable';
                throw new Error('Missing address');
            }
            text = connectionSentence(connect.address, t);
            failures = 0; refusal = null; noAnswer = null; render();
            if (await state === 'unknown') { state = agentInterfaceState(api); changed(await state); }
        } catch { if (current === generation) { noAnswer = refusal ? null : lacked; failures++; render(); } }
        finally {
            if (current === generation) {
                controller = null;
                if (!text && active() && failures < 3) timer = setTimeout(() => { timer = null; void load(); }, 3000);
            }
        }
    }
    function sync() {
        if (!active()) { stop(); return; }
        if (!text && failures < 3 && !controller && !timer) load();
    }
    // `state` is the first answer; `enabled` resolves to whether it is on.
    let state = agentInterfaceState(api);
    const first = state, enabled = first.then(value => value === 'on');
    // Puts the keyboard on the card's button now, or as soon as the address has loaded.
    function focus() { wantFocus = true; render(); }
    return {element: body, state: first, enabled, sync, stop, focus};
}
