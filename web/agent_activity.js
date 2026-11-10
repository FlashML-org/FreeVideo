import {retryAgentPost} from './agent_session.js';
import {issueButton} from './issue_button.js';
// What agents connected to this FreeVideo are doing. Their requests share the
// studio's queue, so people can see them, follow progress and stop them.
const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('./agent_activity.css', import.meta.url).href; document.head.append(css);

const KINDS = {
    generate: ['Generating', '生成中', 'M4 7h11a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2zm13 4 5-3v8l-5-3'],
    assemble: ['Assembling', '拼接中', 'M3 6h7v12H3zM14 6h7v12h-7zM10 12h4'],
    vlm: ['Checking', '检查中', 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zm10 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'],
    export: ['Exporting', '导出中', 'M12 3v12m0 0 4-4m-4 4-4-4M4 17v3h16v-3'],
    listen: ['Transcribing', '识别中', 'M4 6h16M4 12h10M4 18h13'],
    enhance: ['Enhancing prompt', '增强提示词', 'M4 20l4-1 10-10-3-3L5 16zM14 7l3 3M18 3l1 2 2 1-2 1-1 2-1-2-2-1 2-1z'],
    image: ['Generating image', '生成图片', 'M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zm-1 12 5-5 4 4 3-3 5 5M16 9h.01'],
    image_edit: ['Saving image', '保存图片', 'M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zm-1 12 5-5 4 4 3-3 5 5M15 4l3 3'],
    music: ['Composing music', '生成配乐', 'M9 18V6l10-2v12M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0zm10-2a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z'],
    stems: ['Separating tracks', '分离音轨', 'M3 12h6m0 0 4-6h8M9 12l4 6h8M9 12h12'],
    finish_sound: ['Finishing sound', '声音收尾', 'M4 10v4h3l4 4V6l-4 4H4zm11-1a4 4 0 0 1 0 6m2.5-9a8 8 0 0 1 0 12'],
    voice_lines: ['Finding the lines', '识别台词', 'M4 5h16v10H9l-5 4V5zm4 3h8M8 11h5'],
    voice: ['Redubbing', '重新配音', 'M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM6 12a6 6 0 0 0 12 0M12 18v3m-3 0h6'],
    voice_say: ['Dubbing', '配音中', 'M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM6 12a6 6 0 0 0 12 0M12 18v3m-3 0h6M19 3v4m-2-2h4'],
};
const NAMES = {generate: ['Video', '视频'], assemble: ['Sequence', '成片'], vlm: ['Check', '检查'], enhance: ['Prompt', '提示词'], export: ['Export', '导出'], listen: ['Subtitles', '字幕'],
    image: ['Image', '图片'], image_edit: ['Edit image', '编辑图片'], music: ['Music', '配乐'], stems: ['Tracks', '音轨'], finish_sound: ['Sound', '声音'],
    voice_lines: ['Lines', '台词'], voice: ['Voice', '配音'], voice_say: ['New line', '新台词']};

// Short reasons for the failures people can act on; the engine's own message stays in the tooltip.
const REASONS = {
    out_of_memory: ['Out of memory', '显存或内存不足'],
    insufficient_memory: ['Not enough memory for this video', '显存或内存不够生成这段视频'],
    interrupted: ['ComfyUI restarted during this job', 'ComfyUI 中途重启'],
    lost: ['Ended without a result', '没有返回结果'],
    dependency_failed: ['An earlier step did not succeed', '前序任务未成功'],
};

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function glyph(path) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.classList.add('fv-agent-glyph');
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.setAttribute('d', path); svg.append(p);
    return svg;
}
// The same two wordings as the conversation's cards.
const left = (s, t) => s >= 60 ? t(`About ${Math.ceil(s / 60)} min`, `约 ${Math.ceil(s / 60)} 分钟`) : t('Under 1 min', '不到 1 分钟');
// The engine's phases in the reader's language.
function phaseText(p, t, kind) {
    const step = p.step;
    // The first step is still getting ready: the line does not count from zero. A row here does not know which
    // part of a long video it is on, and a later part loads nothing, so it does not say the model is loading.
    if (p.phase === 'sampling' && step?.total) return step.done >= 1 ? t(`Step ${step.done} / ${step.total}`, `生成 ${step.done} / ${step.total}`) : t('Preparing', '准备中');
    // Dubbing counts lines, not steps.
    if (p.phase === 'dubbing' && step?.total) return t(`Line ${step.done} / ${step.total}`, `第 ${step.done} / ${step.total} 句`);
    // The sound finish separates each source clip in turn.
    const clip = /^separating (\d+)\/(\d+)$/.exec(p.phase || '');
    if (clip) return t(`Separating tracks · clip ${clip[1]} / ${clip[2]}`, `分离音轨 · 第 ${clip[1]} / ${clip[2]} 段`);
    // A picture's stages share names with a video's, but it saves no sound.
    if (kind === 'image' || kind === 'image_edit') {
        const own = {text: t('Reading prompt', '读取提示词'), encode: t('Preparing images', '准备图片'),
            denoise: t('Drawing', '绘制中'), decode: t('Saving the image', '保存图片')}[p.phase];
        if (own) return own;
    }
    return {encoding: t('Reading prompt and media', '读取提示词和素材'), load: t('Loading the model', '加载模型'),
        decode: t('Saving video and audio', '保存视频与音频'), starting: t('Starting', '启动中'), start: t('Starting', '启动中'),
        loading: t('Loading the model', '加载模型'), answering: t('Answering', '回答中'), rewriting: t('Enhancing prompt', '增强提示词'),
        assembling: t('Joining clips', '拼接片段'), 'preparing images': t('Preparing images', '准备图片'),
        finishing: t('Finishing', '收尾中'),
        // Sound jobs: music, tracks, the sound finish and dubbing.
        downloading: t('Downloading the model', '下载模型'), waiting: t('Waiting for the GPU', '等待显卡'),
        composing: t('Composing music', '生成配乐'), separating: t('Separating tracks', '分离音轨'),
        recognizing: t('Finding the lines', '识别台词'), preparing: t('Preparing', '准备中'), dubbing: t('Dubbing', '配音中'),
        'measuring loudness': t('Measuring loudness', '测量响度'), 'balancing loudness': t('Balancing loudness', '统一响度'),
        'filling silence': t('Adding room tone', '补环境声'), placing: t('Placing on the timeline', '放入时间线')}[p.phase] || '';
}

function status(job, t) {
    const [en, zh] = KINDS[job.kind] || KINDS.generate;
    if (job.status === 'running') return t(en, zh);
    if (job.status === 'cancelling') return t('Stopping', '正在停止');
    if (job.status === 'queued') return job.progress?.queue_position
        ? t(`In queue · #${job.progress.queue_position}`, `排队 · 第 ${job.progress.queue_position} 位`) : t('Queued', '排队中');
    if (job.status === 'waiting') return job.waiting_for ? t('Waiting for earlier tasks', '等待前面的任务') : t('Waiting for the GPU', '等待显卡');
    return {succeeded: t('Done', '已完成'), failed: t('Failed', '失败'), cancelled: t('Cancelled', '已取消')}[job.status] || job.status;
}
function title(job, t) {
    const name = NAMES[job.kind] || NAMES.generate;
    return job.label || (t(name[0], name[1]) + (job.take ? ' · ' + t(`Take ${job.take}`, `第 ${job.take} 条`) : ''));
}
function row(job, t, api, refresh, open, cancellationErrors) {
    const item = el('li', 'fv-agent-job'); item.dataset.status = job.status; item.dataset.job = job.id;
    item.append(glyph((KINDS[job.kind] || KINDS.generate)[2]));
    const body = el('div', 'fv-agent-body');
    const head = el('div', 'fv-agent-head');
    head.append(el('strong', null, title(job, t)), el('span', 'fv-agent-state', status(job, t)));
    body.append(head);
    const p = job.progress || {};
    if (job.status === 'running' || job.status === 'queued') {
        const bar = el('div', 'fv-agent-bar'), fill = el('i');
        const fraction = Math.max(0, Math.min(1, Number(p.fraction) || 0));
        fill.style.transform = `scaleX(${job.status === 'queued' ? 0 : Math.max(.02, fraction)})`;
        bar.append(fill); bar.setAttribute('role', 'progressbar'); bar.setAttribute('aria-valuemin', '0');
        bar.setAttribute('aria-valuemax', '100'); bar.setAttribute('aria-valuenow', String(Math.round(fraction * 100)));
        body.append(bar);
    }
    const meta = [];
    // A job's main phase is already its state; only the other phases add a line.
    const phase = job.status === 'running' ? phaseText(p, t, job.kind) : '';
    if (phase && phase !== status(job, t)) meta.push(phase);
    if (job.status === 'running' && p.remaining_seconds > 0) meta.push(left(p.remaining_seconds, t));
    const reason = job.status === 'failed' && REASONS[job.error?.code];
    if (reason) meta.push(t(...reason));
    else if (job.status === 'failed') meta.push(t(job.error?.error_en || 'Did not finish', job.error?.error_zh || '没有完成'));
    if (job.project) meta.push(job.project);
    const foot = el('div', 'fv-agent-foot');
    const details = el('p', 'fv-agent-meta', meta.join(' · '));
    if (reason && job.error?.message) details.title = job.error.message;
    foot.append(details);
    const issue = issueButton(t, () => ({where: ['Agent activity', 'Agent 任务'], message: details.textContent}));
    issue.hidden = !cancellationErrors.has(job.id); foot.append(issue);
    if (['waiting', 'queued', 'running'].includes(job.status)) {
        const stop = el('button', 'fv-agent-stop', t('Stop', '停止')); stop.type = 'button';
        stop.setAttribute('aria-label', t('Stop ', '停止 ') + title(job, t));
        stop.onclick = async () => {
            stop.disabled = true; stop.textContent = t('Stopping', '正在停止');
            try {
                const response = await retryAgentPost(api, '/freevideo/agent/activity/cancel', {id: job.id});
                if (!response?.ok) {
                    const message = t('Could not stop. Click again.', '没能停止，请再点一次。');
                    details.textContent = message;
                    cancellationErrors.set(job.id, message);
                    issue.hidden = false;
                } else { cancellationErrors.delete(job.id); issue.hidden = true; }
            } finally { refresh(); }
            stop.disabled = false; stop.textContent = t('Stop', '停止');
        };
        foot.append(stop);
    }
    if (job.work) {
        // The result is a work: show that one in Creations. (The file itself needs the Agent's token.)
        const show = el('button', 'fv-agent-open', t('Open', '打开')); show.type = 'button';
        show.setAttribute('aria-label', t('Open in Creations: ', '在作品中打开：') + title(job, t));
        show.onclick = () => open(job);
        foot.append(show);
    }
    body.append(foot);
    if (cancellationErrors.has(job.id)) details.textContent = cancellationErrors.get(job.id);
    item.append(body);
    return item;
}
// Running first, then the rest in the order they will run; finished ones after, newest first.
function rows(jobs, t, api, refresh, open, cancellationErrors) {
    const rank = {running: 0, cancelling: 0, queued: 1, waiting: 2};
    const active = jobs.filter(j => j.status in rank)
        .sort((a, b) => rank[a.status] - rank[b.status] || String(a.created_at).localeCompare(String(b.created_at)));
    const done = jobs.filter(j => !(j.status in rank))
        .sort((a, b) => String(b.finished_at).localeCompare(String(a.finished_at)));
    const items = active.map(job => row(job, t, api, refresh, open, cancellationErrors));
    if (done.length) {
        const divider = el('li', 'fv-agent-divider', t('Last 30 minutes', '最近 30 分钟'));
        divider.setAttribute('role', 'presentation');
        items.push(divider, ...done.map(job => row(job, t, api, refresh, open, cancellationErrors)));
    }
    return items;
}
// A re-render keeps keyboard focus on the same control.
function refill(root, list, items) {
    const focused = root.contains(document.activeElement) ? document.activeElement : null;
    const key = focused && [focused.closest('[data-job]')?.dataset.job, focused.className];
    list.replaceChildren(...items);
    return key;
}

// "Open" on a finished video leads to that work in Creations.
async function showWork(t, job) {
    (await import('./library.js')).openLibrary(t, {selectedWork: job.work});
}

export function createAgentActivity(t, api, host, {interval = [1500, 8000], open = job => showWork(t, job)} = {}) {
    const element = el('button', 'fv-agent-pill'); element.type = 'button'; element.hidden = true;
    element.setAttribute('aria-haspopup', 'dialog'); element.setAttribute('aria-expanded', 'false');
    const dot = el('span', 'fv-agent-dot'), label = el('span', 'fv-agent-label');
    element.append(dot, label);
    const panel = el('section', 'fv-agent-panel'); panel.hidden = true; panel.setAttribute('role', 'dialog'); panel.tabIndex = -1;
    panel.setAttribute('aria-label', t('Agent jobs', 'Agent 任务'));
    host.append(panel);
    let data = {active: 0, jobs: []}, timer = null, disposed = false, failures = 0;
    const cancellationErrors = new Map();

    function renderPanel() {
        const header = el('header');
        header.append(el('h2', null, t('Agent jobs', 'Agent 任务')));
        const list = el('ol');
        const key = refill(panel, list, rows(data.jobs, t, api, poll, job => { setOpen(false); open(job); }, cancellationErrors));
        const unreadable = Number.isSafeInteger(data.unreadable) && data.unreadable > 0 ? data.unreadable : 0;
        const skipped = el('div', 'fv-agent-unreadable', unreadable > 0
            ? t(`${unreadable} ${unreadable === 1 ? 'job' : 'jobs'} could not be read`, `另有 ${unreadable} 个任务无法读取`) : '');
        skipped.hidden = !unreadable;
        // A poll must not move a list the reader has scrolled.
        const scrolled = panel.querySelector('ol')?.scrollTop || 0;
        panel.replaceChildren();
        panel.append(header, list, skipped);
        list.scrollTop = scrolled;
        if (key) (panel.querySelector(`[data-job="${key[0]}"] .${key[1].split(' ')[0]}`) || panel).focus?.();
    }
    function render() {
        const jobs = data.jobs || [];
        for (const job of jobs) if (!['waiting', 'queued', 'running', 'cancelling'].includes(job.status)) cancellationErrors.delete(job.id);
        element.hidden = !jobs.length;
        if (!jobs.length) { setOpen(false); return; }
        const running = jobs.find(j => j.status === 'running'), active = data.active || 0;
        element.dataset.state = running ? 'running' : active ? 'waiting' : jobs.some(j => j.status === 'failed') ? 'failed' : 'done';
        // Narrow headers keep only "Agent" and the percentage, so the wordmark keeps its room.
        let state, percent = '', more = '';
        if (running) {
            const fraction = Number(running.progress?.fraction);
            state = t(KINDS[running.kind]?.[0] || 'Working', KINDS[running.kind]?.[1] || '进行中');
            if (Number.isFinite(fraction) && fraction > 0) percent = ` ${Math.round(fraction * 100)}%`;
            if (active > 1) more = ` · +${active - 1}`;
        } else if (active) state = t(`${active} waiting`, `${active} 个等待中`);
        else state = t('Done', '已完成');
        label.replaceChildren('Agent', el('span', 'fv-agent-state-text', ' · ' + state), percent, el('span', 'fv-agent-more', more));
        element.setAttribute('aria-label', label.textContent);
        element.title = t('Jobs from AI agents', '来自 AI Agent 的任务');
        if (!panel.hidden) renderPanel();
    }
    function setOpen(open) {
        panel.hidden = !open; element.setAttribute('aria-expanded', String(open));
        if (open) renderPanel();
    }
    element.onclick = () => setOpen(panel.hidden);
    const outside = event => { if (!panel.hidden && !panel.contains(event.target) && !element.contains(event.target)) setOpen(false); };
    // Escape closes the list, not the workspace: the dialog's own cancel follows the keydown.
    let swallow = false;
    const escape = event => { if (event.key === 'Escape' && !panel.hidden) { event.stopPropagation(); event.preventDefault(); swallow = true; setOpen(false); element.focus(); } };
    const cancel = event => { if (swallow) { event.preventDefault(); swallow = false; } };
    const settle = () => { swallow = false; };
    // On the document: after a re-render focus can sit outside the workspace.
    host.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', escape, true);
    host.addEventListener('cancel', cancel);
    document.addEventListener('keyup', settle, true);
    let on = false;
    async function poll() {
        clearTimeout(timer); timer = null;
        if (disposed || !on) return;
        try {
            const response = await api.fetchApi('/freevideo/agent/activity', {cache: 'no-store'});
            if (response.ok) { data = await response.json(); failures = 0; }
            else failures++;
        } catch { failures++; }
        if (disposed) return;
        render();
        // Fast while agents work; slow when idle or when an older FreeVideo has no activity route.
        const wait = data.active ? interval[0] : interval[1] * Math.min(4, 1 + failures);
        timer = setTimeout(poll, document.hidden ? Math.max(wait, interval[1]) : wait);
    }
    // Agent features can be off in this FreeVideo: ask once, and stay out of sight unless they are on.
    (async () => {
        try {
            const response = await api.fetchApi('/freevideo/agent', {cache: 'no-store'});
            on = response.ok && (await response.json()).enabled === true;
        } catch { on = false; }
        poll();
    })();
    return {element, panel, refresh: poll, dispose() {
        disposed = true; clearTimeout(timer);
        host.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', escape, true);
        host.removeEventListener('cancel', cancel); document.removeEventListener('keyup', settle, true);
        panel.remove();
    }};
}
