import {isNetworkTypeError, connectionLostText, detectBrowserExtension, browserExtensionAdvice} from './browser_extension.js';

// Original and enhanced prompts are separate workflow drafts, never diagnostics.
export class PromptVersions {
    constructor(saved, text) {
        this.value = saved && typeof saved.original === 'string' && typeof saved.rewritten === 'string'
            ? {...saved} : {enabled: false, original: text, rewritten: '', selected: 'original', context: ''};
        if (!['original', 'rewritten'].includes(this.value.selected)) this.value.selected = 'original';
        if (this.text() !== text) this.edit(text);
    }
    text() { return this.value[this.value.selected]; }
    // Clearing or replacing all of the enhanced text starts a new scene rather than
    // a tweak of the rewrite, so a later rewrite starts from it, not the old original.
    edit(text, restart = false) {
        this.value.edits = (this.value.edits || 0) + 1;
        if (restart && this.value.selected === 'rewritten') {
            Object.assign(this.value, {original: text, rewritten: '', selected: 'original', context: '', useOriginal: false});
            return;
        }
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

// Best-effort imports have no UI state and never hold up enhancement or video.
export function promptPrestart(api, nodeId, enabled, alive = () => true) {
    let pending = null, completed = false;
    // Enhancement can be turned off, or the panel closed, while the token is fetched.
    const ready = () => alive() && enabled();
    function request() {
        if (!ready()) return Promise.resolve();
        if (pending) return pending;
        pending = (async () => {
            const reply = await api.fetchApi('/freevideo/prompt-vlm');
            if (!reply.ok || !ready()) return;
            const info = await reply.json();
            if (!ready()) return;
            await api.fetchApi('/freevideo/prompt-vlm/prestart', {method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-FreeVideo-Prompt': info.token}, body: '{}'});
        })().catch(() => {}).finally(() => { pending = null; });
        return pending;
    }
    const onExecuted = event => { if (String(event.detail?.node) === String(nodeId)) completed = true; };
    const onStatus = event => {
        const remaining = (event.detail?.status || event.detail)?.exec_info?.queue_remaining;
        if (completed && remaining === 0) {
            completed = false;
            if (enabled()) void request();
        }
    };
    api.addEventListener('executed', onExecuted);
    api.addEventListener('status', onStatus);
    return {request, dispose() {
        api.removeEventListener('executed', onExecuted);
        api.removeEventListener('status', onStatus);
    }};
}

// An empty box is never rewritten, the untouched starter prompt included:
// enhancement waits for the user's own scene.
// `prepare` turns the user's text into what the model reads (character mentions written out with their pictures).
export function createPromptEnhancer({node, input, editor, api, context, setText, t, starter = '', prepare = text => text, onReport = () => {}, onChange = () => {}, issueButton = null}) {
    const saved = node.properties?.freevideo_prompt_versions;
    // Drafts saved before the starter became a placeholder hold it as the original.
    const versions = new PromptVersions(starter && saved?.original === starter ? {...saved, original: ''} : saved, input.value);
    const el = (tag, text, cls) => { const e = document.createElement(tag); if (text) e.textContent = text; if (cls) e.className = cls; return e; };
    const element = el('div', null, 'fv-enhance');
    const bar = el('div', null, 'fv-enhance-bar'), label = el('label', null, 'fv-enhance-toggle');
    const toggle = el('input'); toggle.type = 'checkbox'; toggle.setAttribute('role', 'switch'); toggle.disabled = input.disabled;
    toggle.setAttribute('aria-label', t('Prompt enhancement', '提示词增强'));
    label.append(toggle, el('span', t('FreeToken enhancement', 'FreeToken 提示词增强')));
    label.title = t('Based on FreeToken. Rewrite locally with reference images, following MiniMax H3 guidelines.', '基于 FreeToken，结合参考图，在本机按 MiniMax H3 规则改写。');
    const enhancementHint = label.title;
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
    const statusText = el('span', null, 'fv-enhance-status-text');
    const meter = el('div', null, 'fv-enhance-meter'), fill = el('i');
    meter.setAttribute('role', 'progressbar'); meter.setAttribute('aria-valuemin', '0'); meter.setAttribute('aria-valuemax', '100');
    // The shared "Report a problem" button, handed in by the page.
    const report = issueButton ? issueButton(t, () => ({where: ['Prompt enhancement', '提示词增强'], message: statusText.textContent})) : document.createElement('span');
    report.hidden = true;
    meter.append(fill); status.append(statusText, meter, report);
    // One place sets what the status line says and how it looks.
    function show(text, state = '', fraction = null) {
        if (downloadOnly) { publishModel({text: text || ''}); return; }
        statusText.textContent = text || '';
        status.dataset.state = text ? state : '';
        report.hidden = !text || state !== 'error';
        meter.hidden = state !== 'progress';
        meter.dataset.indeterminate = String(fraction === null);
        if (fraction === null) { meter.removeAttribute('aria-valuenow'); fill.style.width = ''; }
        else { const percent = Math.round(100 * Math.max(0, Math.min(1, fraction))); meter.setAttribute('aria-valuenow', String(percent)); fill.style.width = percent + '%'; }
    }
    const install = el('div', null, 'fv-enhance-install'); install.hidden = true;
    const detail = el('p');
    const agree = el('button', t('Download and enable', '下载并启用'), 'fv-primary'); agree.type = 'button';
    const decline = el('button', t('Not now', '暂不启用'), 'fv-quiet'); decline.type = 'button';
    const license = el('a', 'Apache 2.0'); license.href = 'https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct'; license.target = '_blank'; license.rel = 'noopener noreferrer';
    const actions = el('div', null, 'fv-enhance-install-actions'); actions.append(agree, decline, license);
    install.append(detail, actions);
    bar.append(label, tabs, redo, cancel); element.append(bar, editor || input, status, install);
    let disposed = false, token = '', job = '', running = null, consent = null, cancelling = false, waiting = false, replacing = false;
    let retryWait = null;
    let downloadOnly = false, pausing = false, modelReading = null;
    const modelState = {info: null, text: '', busy: false}, modelListeners = new Set();
    function publishModel(change) {
        Object.assign(modelState, change);
        if (!disposed) for (const listener of modelListeners) listener(modelState);
    }
    // Share status with the length notice, including installs started by enhancement.
    function readModel() {
        if (modelReading) return modelReading;
        modelReading = (async () => {
            const reply = await api.fetchApi('/freevideo/prompt-vlm');
            const info = await reply.json();
            if (!reply.ok) throw new Error(info.error || 'request_failed');
            token = info.token;
            if (info.ram_bytes) ramBytes = info.ram_bytes;
            publishModel({info});
            return info;
        })().catch(error => { publishModel({info: null}); throw error; }).finally(() => { modelReading = null; });
        return modelReading;
    }
    const prestart = promptPrestart(api, node.id, () => versions.value.enabled, () => !disposed && !input.disabled);
    const onFocus = () => { if (versions.value.enabled) void prestart.request(); };
    input.addEventListener('focus', onFocus);
    if (versions.value.enabled) void prestart.request();
    // Available RAM the model needs; the server knows the usual case for this card.
    let ramBytes = 11 * 2 ** 30;
    const gib = value => String(Math.round(value / 2 ** 30));
    // Unknown codes keep the server's own reason, without paths, so the user can quote it.
    const reasonOf = error => String(error?.message || '').replace(/[A-Za-z]:[\\/][^\s'"]*|\/[^\s'"]+\/[^\s'"]*/g, '').replace(/\s+/g, ' ').trim().slice(0, 120);
    // A dropped request names a browser extension only when the error carries evidence of one.
    const networkText = error => {
        const found = detectBrowserExtension(error);
        return found ? browserExtensionAdvice(found, t).summary : connectionLostText(t);
    };
    const errorText = error => isNetworkTypeError(error) ? networkText(error) : ({
        busy: t('Finish the current queue before enhancing this prompt.', '请等待当前队列完成后再改写。'),
        prompt_required: t('Type a prompt first; enhancement rewrites it before generating.', '请先输入提示词，生成前会自动改写。'),
        setup_required: t('Complete FreeVideo installation first.', '请先完成 FreeVideo 安装。'),
        model_missing: t('Download the optional model to enable enhancement.', '下载可选模型后可启用。'),
        download_failed: t('Download paused. Retry to resume.', '下载已暂停，重试可继续。'),
        disk_space: t('Not enough disk space for the optional model.', '可选模型的磁盘空间不足，请清理后重试。'),
        ram_space: t(`This model needs about ${gib(error?.ramBytes || ramBytes)} GiB of available RAM to load. You can keep using the original.`, `此模型加载需约 ${gib(error?.ramBytes || ramBytes)} GiB 可用内存，可先使用原文生成。`),
        unsupported_media: t('Enhancement can read only images and videos added in Media. You can keep using the original.', '增强只能读取素材中添加的图片和视频，可先使用原文生成。'),
        connected_media: t('Enhancement cannot read connected nodes. Add images or videos in Media, or use the original.', '增强读不到连接节点的素材，请在素材中添加图片或视频，或使用原文。'),
        audio_media: t('Enhancement cannot read audio. Add images or videos in Media, or use the original.', '增强读不到音频，请在素材中添加图片或视频，或使用原文。'),
        unreadable_video: t('Could not read a frame from this video. Replace it with another video or an image, or use the original.', '读不出这个视频的画面，请换一个视频或图片，或使用原文。'),
        too_many_images: t('Use up to 8 reference images for enhancement.', '增强暂支持最多 8 张参考图。'),
        invalid_prompt: t('Enter a prompt of up to 12,000 characters.', '请输入不超过 12,000 字符的提示词。'),
        invalid_output: t('The rewrite was incomplete. Retry or use the original.', '改写结果不完整，请重试或使用原文。'),
        context_length: t('The scene is too long for this model. Shorten it or use the original.', '内容超出改写模型容量，请精简或使用原文。'),
        out_of_memory: t('Not enough memory to rewrite. Close other GPU apps or use the original.', '改写时内存不足，请关闭其他显卡程序或使用原文。'),
        timeout: t('Rewriting took too long. Retry or use the original.', '改写超时，请重试或使用原文。'),
        stale: t('The prompt or references changed. Rewrite again for the new scene.', '原文或素材已改变，请按新内容重新改写。'),
        cancelled: t('Cancelled. Your original prompt is retained.', '已取消，原文已保留。'),
        worker_cleanup_failed: t('The rewrite process could not close. Restart FreeVideo before generating.', '改写进程未能退出，请重启 FreeVideo 后再生成。'),
    }[error?.message] || t(`Could not enhance the prompt${reasonOf(error) ? `: ${reasonOf(error)}` : ''}. Retry or switch enhancement off to use the original.`,
        `暂时无法改写${reasonOf(error) ? `。原因：${reasonOf(error)}。` : '，'}请重试，或关闭增强使用原文。`));
    // After falling back the original is already in use, so only the reason and the next useful step are shown.
    const fallbackText = error => isNetworkTypeError(error) ? networkText(error) : ({
        busy: t('The prompt cannot be rewritten while the queue is running.', '队列未完成时不能改写。'),
        setup_required: t('Rewriting is available after FreeVideo installation is complete.', 'FreeVideo 安装完成后才能改写。'),
        model_missing: t('Rewriting needs the optional model. Download it to enable rewriting.', '改写需要可选模型，下载后可用。'),
        download_failed: t('The optional model download paused. Retry to resume.', '可选模型下载已暂停，重试可继续下载。'),
        disk_space: t('Not enough disk space for the optional model.', '磁盘空间不足，无法安装可选模型。'),
        ram_space: t(`The rewrite model needs about ${gib(error?.ramBytes || ramBytes)} GiB of available RAM to load.`, `改写模型加载需约 ${gib(error?.ramBytes || ramBytes)} GiB 可用内存。`),
        invalid_prompt: t('Rewriting supports prompts of up to 12,000 characters.', '改写只支持不超过 12,000 字符的提示词。'),
        invalid_output: t('The rewrite was incomplete.', '改写结果不完整。'),
        context_length: t('The scene is too long for the rewrite model.', '内容超出改写模型容量。'),
        out_of_memory: t('Not enough memory to rewrite. Close other GPU apps before rewriting again.', '改写时内存不足，关闭其他显卡程序后可再改写。'),
        timeout: t('Rewriting took too long.', '改写超时。'),
        unsupported_media: errorText(error), too_many_images: errorText(error), stale: errorText(error),
        connected_media: errorText(error), audio_media: errorText(error), unreadable_video: errorText(error),
        worker_cleanup_failed: errorText(error),
    }[error?.message] || (reasonOf(error) ? t(`The rewrite failed: ${reasonOf(error)}.`, `改写失败，原因：${reasonOf(error)}。`) : t('The rewrite failed.', '改写失败。')));
    function save(changed = true) {
        node.properties ||= {};
        node.properties.freevideo_prompt_versions = {...versions.value};
        node.graph?.change();
        if (changed) onChange();
    }
    function render() {
        toggle.checked = Boolean(versions.value.enabled);
        label.title = input.disabled ? '' : enhancementHint;
        tabs.hidden = !versions.value.rewritten;
        for (const [name, b] of Object.entries(buttons)) {
            b.setAttribute('aria-pressed', String(versions.value.selected === name));
            b.disabled = input.disabled || (name === 'rewritten' && !versions.value.enabled);
        }
        redo.hidden = !versions.value.enabled || Boolean(running); redo.disabled = input.disabled;
        const rewriteLabel = versions.value.rewritten ? t('Rewrite again', '重新改写') : t('Rewrite', '改写');
        redo.title = input.disabled ? '' : rewriteLabel;
        redo.setAttribute('aria-label', rewriteLabel);
        cancel.hidden = !running || install.hidden === false; cancel.disabled = cancelling;
        cancel.title = cancelling ? t('Cancelling…', '正在取消…') : '';
        element.dataset.busy = String(Boolean(running));
        element.dataset.enabled = String(Boolean(versions.value.enabled));
    }
    function apply() { input.value = versions.text(); setText(input.value); save(); render(); }
    function changed() {
        if (waiting) { waiting = false; show(''); }
        versions.edit(input.value, replacing || !input.value.trim()); replacing = false; save(); render();
    }
    const replace = () => { replacing = input.value.length > 0 && input.selectionStart === 0 && input.selectionEnd === input.value.length; };
    input.addEventListener('beforeinput', replace);
    input.addEventListener('input', changed);
    async function post(action, body) {
        const reply = await api.fetchApi('/freevideo/prompt-vlm/' + action, {method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-FreeVideo-Prompt': token}, body: JSON.stringify(body)});
        const data = await reply.json();
        if (!reply.ok) throw new Error(data.error || 'request_failed');
        return data;
    }
    async function cancelJob() {
        cancelling = true; retryWait?.(); consent?.(false); consent = null; render();
        const request = running;
        try { if (job) await post('cancel', {job}); }
        catch (error) { if (running === request) { cancelling = false; render(); } throw error; }
    }
    cancel.onclick = () => cancelJob().catch(error => { show(errorText(error), 'error'); });
    async function poll(action, body) {
        if (disposed || cancelling) throw new Error('cancelled');
        const started = await post(action, body); job = started.job;
        const since = performance.now(), elapsed = () => Math.floor((performance.now() - since) / 1000);
        let failures = 0;
        if (disposed || cancelling) await post('cancel', {job});
        try {
            for (;;) {
                if (disposed || cancelling) throw new Error('cancelled');
                let row;
                try { row = await post('status', {job}); failures = 0; }
                catch (error) {
                    if (disposed || cancelling) throw new Error('cancelled');
                    if (!isNetworkTypeError(error) || ++failures > 3) throw error;
                    // Retry status reads only; submitting again could start a second job.
                    await new Promise(resolve => {
                        const done = () => { clearTimeout(timer); retryWait = null; resolve(); };
                        const timer = setTimeout(done, 1000);
                        retryWait = done;
                    });
                    continue;
                }
                if (disposed || cancelling) throw new Error('cancelled');
                if (row.rewrite_report_id) {
                    node.properties.freevideo_prompt_report = row.rewrite_report_id;
                    node.graph?.change();
                    onReport(row);
                }
                if (row.phase === 'complete') return row;
                if (row.phase === 'failed') throw Object.assign(new Error(row.error || 'rewrite_failed'), {ramBytes: row.diagnostics?.ram_required_bytes});
                if (row.phase === 'cancelled') throw new Error(action === 'install' ? 'download_failed' : 'cancelled');
                if (disposed) throw new Error('cancelled');
                if (row.phase === 'download') {
                    showDownload(row.done || 0, row.total || 0);
                } else if (action !== 'install') {
                    // The ellipsis says "still going" until the seconds start counting.
                    const [en, zh] = row.phase === 'rewriting' ? ['Enhancing the prompt', '正在改写提示词'] : ['Preparing enhancement', '正在准备提示词增强'];
                    const s = elapsed(), m = Math.floor(s / 60), r = s % 60;
                    show(s < 1 ? t(en + '…', zh + '…') : s < 60 ? t(`${en} · ${s} s`, `${zh} · ${s} 秒`)
                        : t(`${en} · ${m} min${r ? ` ${r} s` : ''}`, `${zh} · ${m} 分${r ? ` ${r} 秒` : ''}`), 'progress');
                }
                await new Promise(resolve => setTimeout(resolve, 400));
            }
        } catch (error) {
            // Losing a status reply must not leave an invisible model running.
            await post('cancel', {job}).catch(() => {});
            throw error;
        } finally { job = ''; }
    }
    function showDownload(done, total) {
        const size = value => (value / 2 ** 30).toFixed(1);
        const text = t(`Downloading the model · ${size(done)} / ${size(total)} GiB`, `正在下载模型 · ${size(done)} / ${size(total)} GiB`);
        publishModel({text}); show(text, 'progress', done / (total || 1));
    }
    async function installModel(info) {
        if (info.ready) return true;
        const model = String(info.model || 'Qwen/Qwen3-VL-4B-Instruct').split('/').pop().replace(/-Instruct$/, '');
        detail.textContent = t(`Local model ${model} · ${(info.bytes / 2 ** 30).toFixed(1)} GiB download · needs ${gib(ramBytes)} GiB free RAM. Your prompt and images stay on this computer.`,
            `本地模型 ${model} · 下载 ${(info.bytes / 2 ** 30).toFixed(1)} GiB · 需 ${gib(ramBytes)} GiB 可用内存。提示词和图片只在本机处理。`);
        agree.textContent = downloadOnly ? t('Download', '下载') : t('Download and enable', '下载并启用');
        install.hidden = false; render();
        if (downloadOnly) install.scrollIntoView({block: 'nearest'});
        const accepted = await new Promise(resolve => { consent = resolve; agree.onclick = () => resolve(true); decline.onclick = () => resolve(false); });
        consent = null; install.hidden = true; render();
        if (!accepted || disposed || cancelling) return false;
        publishModel({busy: true}); showDownload(0, info.bytes || 0);
        try {
            await poll('install', {accept_download: true});
            // A complete install is already authoritative; refresh metadata as well.
            publishModel({info: {...info, ready: true}, text: ''});
            await readModel().catch(() => {});
        } catch (error) {
            publishModel({text: errorText(pausing ? {message: 'download_failed'} : error)});
            throw error;
        } finally { publishModel({busy: false}); }
        return true;
    }
    function downloadModel() {
        if (running || disposed) return;
        downloadOnly = true; cancelling = false;
        running = Promise.resolve().then(async () => {
            const info = await readModel();
            if (!disposed && !cancelling) await installModel(info);
        }).catch(error => { if (!disposed) show(errorText(pausing ? {message: 'download_failed'} : error)); }).finally(() => {
            running = null; downloadOnly = false; cancelling = false; pausing = false; install.hidden = true; render();
        });
        render();
        return running;
    }
    async function stopped(id) {
        // The engine is free again once the server reports the download job ended (at most 10 s).
        for (let i = 0; id && !disposed && i < 40; i++) {
            const row = await post('status', {job: id}).catch(() => null);
            if (!row || ['complete', 'failed', 'cancelled'].includes(row.phase)) return;
            await new Promise(resolve => setTimeout(resolve, 250));
        }
    }
    async function enhance() {
        if (running) return running;
        if (!versions.value.original.trim()) {
            waiting = true; show(errorText({message: 'prompt_required'}), 'note'); input.focus();
            throw new Error('prompt_required');
        }
        cancelling = false;
        const execute = async () => {
            if (disposed || cancelling) throw new Error('cancelled');
            versions.value.useOriginal = false;
            const scene = context(), key = versions.key(scene), original = versions.value.original, edits = versions.value.edits;
            const info = await readModel();
            if (disposed || cancelling) throw new Error('cancelled');
            if (!await installModel(info)) {
                versions.value.enabled = false;
                versions.select('original'); apply();
                throw new Error('cancelled');
            }
            const result = await poll('rewrite', {text: prepare(original), ...scene});
            if (disposed || cancelling) throw new Error('cancelled');
            if (edits !== versions.value.edits || !versions.accept(key, context(), result.text)) throw new Error('stale');
            apply(); show('');
        };
        running = Promise.resolve().then(execute).catch(error => {
            if (!disposed) show(errorText(error), error?.message === 'cancelled' ? '' : 'error');
            throw error;
        }).finally(() => { running = null; cancelling = false; install.hidden = true; render(); });
        render();
        return running;
    }
    toggle.onchange = () => {
        versions.value.enabled = toggle.checked;
        if (!toggle.checked) { cancelJob().catch(() => {}); versions.select('original'); apply(); }
        else { void prestart.request(); versions.value.useOriginal = false; save(); render(); enhance().catch(() => {}); }
    };
    redo.onclick = () => enhance().catch(() => {});
    show(''); save(false); render();
    function useOriginal(error) {
        console.warn('FreeVideo prompt rewrite failed; using the original prompt.', error?.message);
        versions.value.selected = 'original'; apply();
        show(t(`Generated with the original prompt. ${fallbackText(error)}`, `已用原文生成。${fallbackText(error)}`), 'note');
    }
    return {element, model: {read: readModel, download: downloadModel, subscribe(listener) {
        modelListeners.add(listener); listener(modelState);
        return () => modelListeners.delete(listener);
    }}, async beforeGenerate() {
        if (running && downloadOnly) {
            // The download holds the engine the video needs: pause it (Retry resumes) instead of waiting for it.
            const request = running, downloading = consent ? '' : job;
            pausing = true;
            await cancelJob().catch(() => {});
            await request;
            await stopped(downloading);
            return;
        }
        if (running) {
            try { await running; }
            catch (error) {
                if (versions.value.enabled && !versions.value.useOriginal) {
                    if (error?.message === 'cancelled' || cancelling || disposed) return false;
                    useOriginal(error); return;
                }
            }
        }
        // An untouched box generates the starter prompt as it is.
        if (!versions.value.enabled || versions.value.useOriginal || !versions.value.original.trim()) return;
        try { if (!versions.fresh(context())) await enhance(); }
        catch (error) {
            if (error?.message === 'cancelled' || cancelling || disposed) { show(errorText({message: 'cancelled'})); return false; }
            useOriginal(error);
        }
    }, sync() { if (input.value !== versions.text()) changed(); }, dispose() {
        disposed = true; modelListeners.clear(); prestart.dispose(); cancelJob().catch(() => {}); input.removeEventListener('input', changed);
        input.removeEventListener('beforeinput', replace);
        input.removeEventListener('focus', onFocus);
    }};
}
