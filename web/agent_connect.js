function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function item(tag, text) { const e = document.createElement(tag); e.textContent = text; return e; }

// The one place a page says whether an agent is connected: its name while it is, never a client id.
export function connectionLabel(agent, t) {
    if (agent?.connected !== true) return {color: 'gray', text: t('Not connected', '未连接')};
    return {color: 'green', text: String(agent.name || '').trim() || t('Connected', '已连接')};
}

export function connectionSentence(address, t) {
    const url = address + '/freevideo/agent';
    return t(`Connect to FreeVideo: open ${url} and follow the instructions.`, `请连接 FreeVideo：打开 ${url} 并按说明操作。`);
}

// Whether this FreeVideo has the Agent interface on. Any failure counts as off: the surface shows nothing.
export async function agentInterfaceEnabled(api) {
    try {
        const response = await api.fetchApi('/freevideo/agent', {cache: 'no-store'});
        return response.ok && (await response.json()).enabled === true;
    } catch { return false; }
}

const ICONS = {plug: ['M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0V7zM12 16v5', 1.8], copy: ['M11 9h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-7a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2zM5 15V5a2 2 0 0 1 2-2h8', 2]};
function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', ICONS[name][0]);
    svg.setAttribute('stroke-width', String(ICONS[name][1])); svg.append(path);
    return svg;
}

async function copyText(value, fallback) {
    try { await navigator.clipboard.writeText(value); fallback.hidden = true; return true; }
    catch {
        fallback.textContent = value; fallback.hidden = false;
        const range = document.createRange(); range.selectNodeContents(fallback);
        const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        return false;
    }
}

// The card: who can connect, one button that copies the sentence to paste to the agent, and (folded)
// how to use a local LM Studio model with FreeVideo MCP on a small GPU. shown() says whether the
// user can see the card now; the surface calls sync() whenever that may have changed.
export function agentConnectCard(t, api, shown = () => true) {
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('./agent_connect.css', import.meta.url).href; document.head.append(css);
    const body = el('section', 'fv-agent-connect-body');
    const mark = el('span', 'fv-agent-connect-icon'); mark.append(icon('plug'));
    const title = el('h2', null, t('Connect an agent', '连接 Agent'));
    const subtitle = el('p', 'fv-muted', t('Claude Code, Codex, FreeToken, LM Studio and others', 'Claude Code、Codex、FreeToken、LM Studio 等'));
    const actions = el('div', 'fv-agent-connect-actions');
    const button = el('button', 'fv-primary'); button.type = 'button'; button.disabled = true;
    const label = el('span'); button.append(icon('copy'), label);
    const arrow = el('span', 'fv-agent-connect-arrow', '→'); arrow.setAttribute('aria-hidden', 'true');
    actions.append(button, arrow, el('span', 'fv-agent-connect-hint', t('Paste to your agent', '粘贴给 Agent')));
    const sentence = el('p', 'fv-agent-sentence'); sentence.hidden = true;

    const help = el('details', 'fv-agent-local');
    help.append(el('summary', null, t('Local LLM · LM Studio', '本地 LLM · LM Studio')));
    const helpBody = el('div', 'fv-agent-local-body');
    helpBody.append(
        el('p', null, t(
            'Autopilot submits FreeVideo jobs (prompt, size, duration, references). It does not build ComfyUI node graphs. Copy MCP JSON adds two servers: freevideo (jobs) and comfyui-expert (this ComfyUI node index, errors, tech support).',
            'Autopilot 向 FreeVideo 提交任务（提示词、尺寸、时长、参考素材），不会编写 ComfyUI 节点工作流。复制 MCP JSON 会加入两个服务：freevideo（任务）和 comfyui-expert（本机节点索引、报错、技术支持）。')),
        el('p', null, t(
            'Unload a GPU LLM in LM Studio before Generate only when that model runs on this same computer and VRAM is tight (for example 8 GB Pascal next to MiniMax H3). Skip unload for cloud agents (Claude, Gemini, Cursor) or when this GPU has spare VRAM.',
            '仅当 LLM 与 FreeVideo 跑在同一台电脑且显存紧张（例如 8 GB Pascal 与 MiniMax H3 争用）时，才在生成前于 LM Studio 卸载模型。云端 Agent（Claude、Gemini、Cursor）或显存充足时不必卸载。')),
        el('p', 'fv-agent-local-label', t('Models that fit a GTX 1070 Ti', '适合 GTX 1070 Ti 的模型')));
    const models = document.createElement('ul');
    models.append(
        item('li', t('Qwen2.5-7B-Instruct Q4_K_M — recommended (tools and JSON). Search this name in LM Studio.',
            'Qwen2.5-7B-Instruct Q4_K_M — 推荐（工具调用与 JSON）。在 LM Studio 中搜索此名称。')),
        item('li', t('Gemma 3 4B Instruct Q4_K_M — optional if you want the model to look at frames.',
            'Gemma 3 4B Instruct Q4_K_M — 可选，若希望模型直接看画面。')));
    const adult = el('div', 'fv-agent-adult');
    adult.append(
        el('p', 'fv-agent-local-label', t('Adult (+18) uncensored / abliterated — suggestions only',
            '成人（+18）无审查 / Abliterated — 仅供参考')),
        el('p', null, t(
            'For adults only. These GGUFs are listed because FreeVideo is uncensored; they are optional suggestions, not defaults. Abliteration removes refusal training. They can be weaker at MCP tool calls than Instruct — keep Qwen2.5-7B-Instruct for Autopilot jobs, and load one of these when you want adult scene writing.',
            '仅限成人。FreeVideo 本身无审查，故列出这些可选 GGUF，不是默认项。Abliteration 会去掉拒答训练。它们的 MCP 工具调用往往弱于 Instruct — Autopilot 任务请仍用 Qwen2.5-7B-Instruct，写成人剧情时再加载下列模型。')));
    const adultModels = document.createElement('ul');
    adultModels.append(
        item('li', t('Qwen2.5-7B-Instruct-abliterated Q4_K_M — closest +18 stand-in (still Qwen tools). Search this name.',
            'Qwen2.5-7B-Instruct-abliterated Q4_K_M — 最接近的 +18 替代（仍是 Qwen 工具格式）。搜索此名称。')),
        item('li', t('Dolphin 3.0 Llama 3.1 8B Q4_K_M — classic uncensored chat; weaker tools, better for writing adult prompts.',
            'Dolphin 3.0 Llama 3.1 8B Q4_K_M — 经典无审查对话；工具较弱，更适合写成人提示词。')),
        item('li', t('Llama-3.1-8B-Instruct-abliterated Q4_K_M — another +18 8B. Skip 14B+ on 8 GB.',
            'Llama-3.1-8B-Instruct-abliterated Q4_K_M — 另一个 +18 的 8B。8 GB 显存不要用 14B 以上。')));
    adult.append(adultModels);
    helpBody.append(models, adult, el('p', 'fv-agent-local-label', t('Install both MCPs in LM Studio', '在 LM Studio 中安装这两个 MCP')));
    const steps = document.createElement('ol');
    steps.append(
        item('li', t('Start FreeVideo and wait until the studio loads. Both MCPs talk to this running ComfyUI.',
            '先启动 FreeVideo，等到创作面板就绪。两个 MCP 都连接正在运行的 ComfyUI。')),
        item('li', t('Use Copy MCP JSON below. It contains two servers: freevideo (Autopilot jobs) and comfyui-expert (node index, Comfy errors, tech support).',
            '点击下方“复制 MCP JSON”。其中有两个服务：freevideo（Autopilot 任务）和 comfyui-expert（节点索引、Comfy 报错、技术支持）。')),
        item('li', t('In LM Studio open MCP / Integrations (or edit %USERPROFILE%\\.lmstudio\\mcp.json). Paste into mcpServers. If that file already has other servers, keep them and only add these two keys.',
            '在 LM Studio 打开 MCP / Integrations（或编辑 %USERPROFILE%\\.lmstudio\\mcp.json），把内容贴进 mcpServers。若已有其他服务，保留它们，只加入这两个键。')),
        item('li', t('Quit LM Studio fully and reopen it. freevideo and comfyui-expert should both show as running. If one is red, start FreeVideo first and read that server’s log.',
            '完全退出并重新打开 LM Studio。freevideo 与 comfyui-expert 都应显示为运行中。若有一个是红色，先启动 FreeVideo，再查看该服务的日志。')),
        item('li', t('Load a tool-capable GGUF (Qwen2.5-7B-Instruct Q4_K_M). GPU offload all layers, context 8192. Start a new chat and turn MCP / tools on.',
            '加载支持工具调用的 GGUF（Qwen2.5-7B-Instruct Q4_K_M）。GPU 卸载全部层，上下文 8192。开新对话并开启 MCP / 工具。')),
        item('li', t('Smoke test: ask for FreeVideo status (freevideo), then search this ComfyUI for FreeVideo or MiniMax nodes (comfyui-expert). The first node search may build the on-disk index.',
            '试一下：先问 FreeVideo 状态（freevideo），再搜索本机 FreeVideo 或 MiniMax 节点（comfyui-expert）。首次节点搜索可能会生成磁盘索引。')),
        item('li', t('A normal LM Studio chat does not keep this Autopilot page listening — use Claude Code or Codex for that. If the LLM shares this GPU and VRAM is tight, unload it before Generate, then load it again to review.',
            'LM Studio 普通一轮对话不会一直监听本页收件箱；要本页保持在线请用 Claude Code 或 Codex。若 LLM 占用同一块 GPU 且显存紧张，生成前卸载，片子出来后再加载查看。')));
    const mcpRow = el('div', 'fv-agent-mcp-row');
    const mcpButton = el('button', 'fv-quiet'); mcpButton.type = 'button'; mcpButton.disabled = true;
    const mcpLabel = el('span'); mcpButton.append(icon('copy'), mcpLabel);
    mcpRow.append(mcpButton, el('span', 'fv-agent-connect-hint', t('Paste into LM Studio MCP', '粘贴到 LM Studio MCP')));
    const mcpFallback = el('p', 'fv-agent-sentence'); mcpFallback.hidden = true;
    helpBody.append(steps, mcpRow, mcpFallback);
    help.append(helpBody);
    body.append(mark, title, subtitle, actions, sentence, help);

    let feedback = false, mcpFeedback = false, text = '', mcpJson = '';
    let timer, feedbackTimer, mcpTimer, controller, generation = 0;
    const active = () => body.isConnected && shown();
    const render = () => {
        label.textContent = feedback ? t('Copied', '已复制') : t('Copy', '复制');
        mcpLabel.textContent = mcpFeedback ? t('Copied', '已复制') : t('Copy MCP JSON', '复制 MCP JSON');
    };
    render();

    button.onclick = async () => {
        await copyText(text, sentence);
        if (!active()) return;
        feedback = true; render();
        clearTimeout(feedbackTimer);
        feedbackTimer = setTimeout(() => { feedback = false; render(); }, 2000);
    };
    mcpButton.onclick = async () => {
        if (!mcpJson) return;
        await copyText(mcpJson, mcpFallback);
        if (!active()) return;
        mcpFeedback = true; render();
        clearTimeout(mcpTimer);
        mcpTimer = setTimeout(() => { mcpFeedback = false; render(); }, 2000);
    };

    function stop() {
        generation++; clearTimeout(timer); timer = null;
        controller?.abort(); controller = null;
        clearTimeout(feedbackTimer); clearTimeout(mcpTimer); feedback = mcpFeedback = false; render();
    }
    // The sentence and MCP JSON are read here; who is connected comes from the surface that holds the card.
    async function load() {
        const current = generation;
        const request = new AbortController(); controller = request;
        try {
            const response = await api.fetchApi('/freevideo/agent/connect?lang=' + t('en', 'zh'), {cache: 'no-store', signal: request.signal});
            const connect = response.ok ? await response.json() : null;
            if (current !== generation || !connect?.address) return;
            text = connectionSentence(connect.address, t);
            mcpJson = connect.mcp_json ? JSON.stringify(connect.mcp_json, null, 2) : '';
            button.disabled = false;
            mcpButton.disabled = !mcpJson;
        } catch { /* Try again while the card is in sight. */ }
        finally {
            if (current === generation) {
                controller = null;
                if (!text && active()) timer = setTimeout(load, 3000);
            }
        }
    }
    function sync() {
        if (!active()) { stop(); return; }
        if (!text && !controller && !timer) load();
    }
    const enabled = agentInterfaceEnabled(api);
    return {element: body, enabled, sync, stop};
}
