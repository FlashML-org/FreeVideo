import {issueButton} from './issue_button.js';
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

// The one place a page says whether an agent is connected: its name while it is, never a client id.
export function connectionLabel(agent, t) {
    if (agent?.connected !== true) return {color: 'gray', text: t('Not connected', '未连接')};
    return {color: 'green', text: String(agent.name || '').trim() || t('Connected', '已连接')};
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
        : t('Agents can only be connected on the computer that runs FreeVideo.', '只能在运行 FreeVideo 的电脑上连接 Agent。');
    if (code === 'not_ready') return t('The agent connection service is not ready. Restart FreeVideo, then retry.', 'Agent 连接服务尚未就绪，请重启 FreeVideo 后重试。');
    return '';
}

export async function agentInterfaceEnabled(api) { return await agentInterfaceState(api) === 'on'; }

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
    const title = el('h2', null, t('Connect an agent', '连接 Agent'));
    const intro = el('p', 'fv-agent-connect-intro', t('Once connected, describe the video you want. It generates and edits it for you.', '连上后，说想做什么视频，它来生成和剪辑。'));
    const subtitle = el('p', 'fv-muted', t('Claude Code, Codex, FreeToken and others', 'Claude Code、Codex、FreeToken 等'));
    const actions = el('div', 'fv-agent-connect-actions');
    const button = el('button', 'fv-primary'); button.type = 'button'; button.disabled = true;
    // The button's picture follows what it does: copy, or retry once the lookup has failed.
    const label = el('span'), pictures = {copy: icon('copy'), retry: icon('retry')}; button.append(pictures.copy, label);
    for (const [name, picture] of Object.entries(pictures)) picture.dataset.icon = name;
    let issue = null;
    const arrow = el('span', 'fv-agent-connect-arrow', '→'); arrow.setAttribute('aria-hidden', 'true');
    // The arrow and its words wrap as one piece.
    const next = el('span', 'fv-agent-connect-next'); next.append(arrow, el('span', 'fv-agent-connect-hint', t('Paste to your agent', '粘贴给 Agent')));
    actions.append(button, next);
    const sentence = el('p', 'fv-agent-sentence'); sentence.hidden = true;
    const status = el('p', 'fv-agent-connect-status', t('Getting the connection address…', '正在获取连接地址…')); status.setAttribute('role', 'status');
    body.append(mark, title, intro, subtitle, actions, sentence, status);

    let feedback = false, text = '', failures = 0, refusal = null, noAnswer = null;
    let timer, feedbackTimer, controller, generation = 0;
    const opened = addressKind(globalThis.location?.hostname);
    const active = () => body.isConnected && shown();
    const render = () => {
        label.textContent = !text && failures >= 3 ? t('Could not get address. Retry', '获取失败，重试') : feedback ? t('Copied', '已复制') : t('Copy', '复制');
        const picture = pictures[!text && failures >= 3 ? 'retry' : 'copy'];
        if (button.firstChild !== picture) button.firstChild.replaceWith(picture);
        if (!text && failures >= 3) {
            if (!issue) { issue = issueButton(t, () => ({where: ['Agent connection', 'Agent 连接'], message: refusal ? `${label.textContent} (HTTP ${refusal.status}${refusal.code ? ' ' + refusal.code : ''}${opened ? ', opened by ' + opened : ''})` : label.textContent, kind: refusal?.code || noAnswer})); button.after(issue); }
        } else if (issue) { issue.remove(); issue = null; }
        next.hidden = !!issue;
        button.disabled = !text && failures < 3;
        button.title = !text && failures < 3 ? t('Getting the connection address…', '正在获取连接地址…') : '';
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
    return {element: body, state: first, enabled, sync, stop};
}
