// Original and enhanced prompts are separate workflow drafts, never diagnostics.
export class PromptVersions {
    constructor(saved, text) {
        this.value = saved && typeof saved.original === 'string' && typeof saved.rewritten === 'string'
            ? {...saved} : {enabled: false, original: text, rewritten: '', selected: 'original', context: ''};
        if (!['original', 'rewritten'].includes(this.value.selected)) this.value.selected = 'original';
        if (this.text() !== text) this.edit(text);
    }
    text() { return this.value[this.value.selected]; }
    edit(text) {
        this.value.edits = (this.value.edits || 0) + 1;
        this.value[this.value.selected] = text;
        if (this.value.selected === 'original') this.value.context = '';
    }
    key(context) { return JSON.stringify({text: this.value.original, ...context}); }
    accept(key, context, text) {
        if (this.key(context) !== key || !this.value.enabled || this.value.useOriginal) return false;
        this.value.rewritten = text; this.value.selected = 'rewritten'; this.value.context = key;
        return true;
    }
    fresh(context) { return Boolean(this.value.rewritten && this.value.context === this.key(context)); }
    select(name) { if (name === 'original' || this.value.rewritten) { this.value.selected = name; this.value.useOriginal = name === 'original'; } }
}

export function createPromptEnhancer({node, input, editor, api, context, setText, t, onReport = () => {}}) {
    const versions = new PromptVersions(node.properties?.freevideo_prompt_versions, input.value);
    const el = (tag, text, cls) => { const e = document.createElement(tag); if (text) e.textContent = text; if (cls) e.className = cls; return e; };
    const element = el('div', null, 'fv-enhance');
    const bar = el('div', null, 'fv-enhance-bar'), label = el('label', null, 'fv-enhance-toggle');
    const toggle = el('input'); toggle.type = 'checkbox'; toggle.setAttribute('role', 'switch'); toggle.disabled = input.disabled;
    toggle.setAttribute('aria-label', t('Prompt enhancement', '提示词增强'));
    label.append(toggle, el('span', t('FreeToken enhancement', 'FreeToken 提示词增强')));
    label.title = t('Based on FreeToken. Rewrite locally with reference images, following MiniMax H3 guidelines.', '基于 FreeToken，结合参考图，在本机按 MiniMax H3 规则改写。');
    const tabs = el('div', null, 'fv-enhance-tabs'); tabs.setAttribute('role', 'group'); tabs.setAttribute('aria-label', t('Prompt version', '提示词版本'));
    const buttons = {};
    for (const [name, text] of [['original', t('Original', '原文')], ['rewritten', t('Enhanced', '改写')]]) {
        const b = el('button', text); b.type = 'button'; buttons[name] = b; tabs.append(b);
        b.onclick = () => { if (running) cancelJob().catch(() => {}); versions.select(name); apply(); };
    }
    const redo = el('button', t('Rewrite', '改写'), 'fv-quiet'); redo.type = 'button';
    redo.classList.add('fv-enhance-redo');
    redo.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 6M20 4v7h-7"/></svg>';
    const cancel = el('button', t('Cancel', '取消'), 'fv-quiet'); cancel.type = 'button';
    const status = el('div', null, 'fv-enhance-status'); status.setAttribute('role', 'status');
    const install = el('div', null, 'fv-enhance-install'); install.hidden = true;
    const detail = el('p');
    const agree = el('button', t('Download and enable', '下载并启用'), 'fv-primary'); agree.type = 'button';
    const decline = el('button', t('Not now', '暂不启用'), 'fv-quiet'); decline.type = 'button';
    const license = el('a', 'Apache 2.0'); license.href = 'https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct'; license.target = '_blank'; license.rel = 'noopener noreferrer';
    const actions = el('div', null, 'fv-enhance-install-actions'); actions.append(agree, decline, license);
    install.append(detail, actions);
    bar.append(label, tabs, redo, cancel); element.append(bar, editor || input, status, install);
    let disposed = false, token = '', job = '', running = null, consent = null, cancelling = false;
    const errorText = error => ({
        busy: t('Finish the current queue before enhancing this prompt.', '请等待当前队列完成后再改写。'),
        setup_required: t('Complete FreeVideo installation first.', '请先完成 FreeVideo 安装。'),
        model_missing: t('Download the optional model to enable enhancement.', '下载可选模型后即可启用。'),
        download_failed: t('Download paused. Retry to resume.', '下载已暂停，重试即可继续。'),
        disk_space: t('Not enough disk space for the optional model.', '可选模型的磁盘空间不足，请清理后重试。'),
        ram_space: t('This model needs about 11 GiB of available RAM to load. You can keep using the original.', '此模型加载需约 11 GiB 可用内存，可先使用原文生成。'),
        unsupported_media: t('Use images added in Media for enhancement; connected nodes, video and audio are not supported yet.', '增强暂支持在素材中添加的图片；连接节点、视频和音频暂不支持。'),
        too_many_images: t('Use up to 8 reference images for enhancement.', '增强暂支持最多 8 张参考图。'),
        invalid_prompt: t('Enter a prompt of up to 12,000 characters.', '请输入不超过 12,000 字符的提示词。'),
        invalid_output: t('The rewrite was incomplete. Retry or use the original.', '改写结果不完整，请重试或使用原文。'),
        context_length: t('The scene is too long for this model. Shorten it or use the original.', '内容超出改写模型容量，请精简或使用原文。'),
        out_of_memory: t('Not enough memory to rewrite. Close other GPU apps or use the original.', '改写时内存不足，请关闭其他显卡程序或使用原文。'),
        timeout: t('Rewriting took too long. Retry or use the original.', '改写超时，请重试或使用原文。'),
        stale: t('The prompt or references changed. Rewrite again for the new scene.', '原文或素材已改变，请按新内容重新改写。'),
        cancelled: t('Cancelled. Your original prompt is retained.', '已取消，原文已保留。'),
        worker_cleanup_failed: t('The rewrite process could not close. Restart FreeVideo before generating.', '改写进程未能退出，请重启 FreeVideo 后再生成。'),
    }[error?.message] || t('Could not enhance the prompt. Retry or switch enhancement off to use the original.', '暂时无法改写，请重试，或关闭增强使用原文。'));
    function save() {
        node.properties ||= {};
        node.properties.freevideo_prompt_versions = {...versions.value};
        node.graph?.change();
    }
    function render() {
        toggle.checked = Boolean(versions.value.enabled);
        tabs.hidden = !versions.value.rewritten;
        for (const [name, b] of Object.entries(buttons)) {
            b.setAttribute('aria-pressed', String(versions.value.selected === name));
            b.disabled = input.disabled || (name === 'rewritten' && !versions.value.enabled);
        }
        redo.hidden = !versions.value.enabled || Boolean(running); redo.disabled = input.disabled;
        redo.title = versions.value.rewritten ? t('Rewrite again', '重新改写') : t('Rewrite', '改写');
        redo.setAttribute('aria-label', redo.title);
        cancel.hidden = !running || install.hidden === false; cancel.disabled = cancelling;
        element.dataset.busy = String(Boolean(running));
        element.dataset.enabled = String(Boolean(versions.value.enabled));
    }
    function apply() { input.value = versions.text(); setText(input.value); save(); render(); }
    function changed() { versions.edit(input.value); save(); render(); }
    input.addEventListener('input', changed);
    async function post(action, body) {
        const reply = await api.fetchApi('/freevideo/prompt-vlm/' + action, {method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-FreeVideo-Prompt': token}, body: JSON.stringify(body)});
        const data = await reply.json();
        if (!reply.ok) throw new Error(data.error || 'request_failed');
        return data;
    }
    async function cancelJob() {
        cancelling = true; consent?.(false); consent = null; render();
        if (job) await post('cancel', {job});
    }
    cancel.onclick = () => cancelJob().catch(error => { status.textContent = errorText(error); });
    async function poll(action, body) {
        if (disposed || cancelling) throw new Error('cancelled');
        const started = await post(action, body); job = started.job;
        if (disposed || cancelling) await post('cancel', {job});
        try {
            for (;;) {
                const row = await post('status', {job});
                if (row.rewrite_report_id) {
                    node.properties.freevideo_prompt_report = row.rewrite_report_id;
                    node.graph?.change();
                    onReport(row);
                }
                if (row.phase === 'complete') return row;
                if (row.phase === 'failed') throw new Error(row.error || 'rewrite_failed');
                if (row.phase === 'cancelled') throw new Error('cancelled');
                if (disposed) throw new Error('cancelled');
                status.textContent = row.phase === 'download'
                    ? t(`Downloading · ${Math.round(100 * (row.done || 0) / (row.total || 1))}%`, `正在下载 · ${Math.round(100 * (row.done || 0) / (row.total || 1))}%`)
                    : row.phase === 'rewriting' ? t('Enhancing prompt…', '正在改写提示词…')
                    : t('Preparing enhancement…', '正在准备提示词增强…');
                await new Promise(resolve => setTimeout(resolve, 400));
            }
        } catch (error) {
            // Losing a status reply must not leave an invisible model running.
            await post('cancel', {job}).catch(() => {});
            throw error;
        } finally { job = ''; }
    }
    async function enhance() {
        if (running) return running;
        const execute = async () => {
            cancelling = false;
            versions.value.useOriginal = false;
            const scene = context(), key = versions.key(scene), original = versions.value.original, edits = versions.value.edits;
            const reply = await api.fetchApi('/freevideo/prompt-vlm');
            const info = await reply.json();
            if (!reply.ok) throw new Error(info.error);
            token = info.token;
            if (!info.ready) {
                detail.textContent = t(`Local model · ${(info.bytes / 2 ** 30).toFixed(1)} GiB download · needs 11 GiB free RAM. Your content stays local.`,
                    `本地模型 · 下载 ${(info.bytes / 2 ** 30).toFixed(1)} GiB · 需 11 GiB 可用内存。内容仅在本机处理。`);
                install.hidden = false; render();
                const accepted = await new Promise(resolve => { consent = resolve; agree.onclick = () => resolve(true); decline.onclick = () => resolve(false); });
                consent = null; install.hidden = true;
                if (!accepted || disposed || cancelling) {
                    versions.value.enabled = false;
                    versions.select('original'); apply();
                    throw new Error('cancelled');
                }
                await poll('install', {accept_download: true});
            }
            const result = await poll('rewrite', {text: original, ...scene});
            if (disposed || cancelling) throw new Error('cancelled');
            if (edits !== versions.value.edits || !versions.accept(key, context(), result.text)) throw new Error('stale');
            apply(); status.textContent = '';
        };
        running = Promise.resolve().then(execute).catch(error => {
            if (!disposed) status.textContent = errorText(error);
            throw error;
        }).finally(() => { running = null; install.hidden = true; render(); });
        render();
        return running;
    }
    toggle.onchange = () => {
        versions.value.enabled = toggle.checked;
        if (!toggle.checked) { cancelJob().catch(() => {}); versions.select('original'); apply(); }
        else { versions.value.useOriginal = false; save(); render(); enhance().catch(() => {}); }
    };
    redo.onclick = () => enhance().catch(() => {});
    save(); render();
    return {element, async beforeGenerate() {
        if (running) {
            try { await running; }
            catch (error) { if (versions.value.enabled && !versions.value.useOriginal) return false; }
        }
        if (!versions.value.enabled || versions.value.useOriginal) return;
        try { if (!versions.fresh(context())) await enhance(); }
        catch (error) { status.textContent = errorText(error); return false; }
    }, sync() { if (input.value !== versions.text()) changed(); }, dispose() {
        disposed = true; cancelJob().catch(() => {}); input.removeEventListener('input', changed);
    }};
}
