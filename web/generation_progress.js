// One whole-video estimate shared by Studio and node view; real stage counters remain separate.
const css = document.createElement('link');
css.rel = 'stylesheet'; css.href = new URL('./generation_progress.css', import.meta.url).href;
document.head.append(css);
const el = (tag, cls) => { const node = document.createElement(tag); node.className = cls; return node; };
const valid = value => Number.isFinite(value) && value >= 0;
const duration = value => {
    const seconds = Math.floor(value);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
};

function encodingLabel(label, t) {
    const labels = {
        'Starting text encoder': ['Starting text encoder', '启动文本编码器'],
        'Preparing text encoder GPU': ['Preparing GPU', '准备显卡'],
        'Checking text encoder cache': ['Checking prompt cache', '检查提示词缓存'],
        'Checking saved video': ['Checking saved video', '检查已保存的视频'],
        'Loading text encoder': ['Loading text encoder', '加载文本编码器'],
        'Reading text encoder weights': ['Reading text encoder weights', '读取文本编码器权重'],
        'Preparing text encoder model': ['Preparing text encoder', '准备文本编码器'],
        'Loading text encoder onto GPU': ['Loading text encoder onto GPU', '将文本编码器加载到显卡'],
        'Reusing text encoder': ['Reusing text encoder', '复用文本编码器'],
        'Using text encoder already on GPU': ['Reusing text encoder', '复用文本编码器'],
        'Preparing text and image tokens': ['Processing prompt and images', '处理提示词与参考图片'],
        'Preparing text encoding': ['Preparing text encoding', '准备文本编码'],
        'Encoding text and images': ['Encoding prompt and images', '编码提示词与参考图片'],
        'Encoding prompt': ['Preparing prompt', '准备提示词'],
        'Preparing reference media': ['Preparing references', '准备参考素材'],
        'Encoding reference media': ['Encoding references', '编码参考素材'],
        'Releasing encoder weights after insufficient GPU memory': ['Adjusting encoder memory', '调整文本编码器显存'],
        'Retrying text encoding with more GPU workspace': ['Retrying prompt encoding', '重试提示词编码'],
        'Preparing prompt data': ['Preparing prompt data', '整理提示词编码结果'],
        'Saving prompt cache': ['Saving prompt cache', '保存提示词缓存'],
        'Reusing prompt cache': ['Reusing prompt cache', '复用提示词缓存'],
        'Prompt ready': ['Prompt ready', '提示词已就绪'],
        'Releasing idle models and checking available memory again': ['Preparing encoder memory', '准备编码器内存'],
        'Retaining and checking input media': ['Preparing references', '准备参考素材'],
    };
    return t(...(labels[label] || ['Preparing prompt and references', '准备提示词与参考素材']));
}

export function createGenerationProgress(t, now = () => Date.now(), {compact = false, api = null} = {}) {
    const element = el('section', 'fv-generation-progress'); element.hidden = true;
    if (compact) element.classList.add('fv-generation-compact');
    const heading = el('div', 'fv-generation-heading'); heading.setAttribute('role', 'status');
    const label = el('span', 'fv-generation-label'), count = el('strong', 'fv-generation-count');
    heading.append(label, count);
    const track = el('div', 'fv-generation-track'), fill = el('div', 'fv-generation-fill');
    track.setAttribute('role', 'progressbar'); track.setAttribute('aria-valuemin', '0'); track.append(fill);
    const detail = el('div', 'fv-generation-detail');
    const times = el('div', 'fv-generation-times');
    const elapsed = el('span', ''), eta = el('span', ''); times.append(elapsed, eta);
    const note = el('p', 'fv-generation-note');
    const recovery = el('p', 'fv-generation-note fv-generation-retry'); recovery.hidden = true;
    recovery.setAttribute('role', 'status');
    const silence = el('div', 'fv-generation-silence'); silence.hidden = true;
    element.append(heading, track, detail, times, silence, recovery, note);
    let state = {}, overall = {}, started = 0, sampledAt = 0, overallAt = 0, timer = null;
    let shownFraction = null;
    // The visible percentage counts toward its value with the bar instead of
    // jumping. Accessible values are set exactly and immediately elsewhere.
    let countShown = null, countTarget = null, countFrom = 0, countStart = 0, countFrame = null, countPrefix = '';
    const moving = () => typeof requestAnimationFrame === 'function'
        && !(typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches);
    function stopCount() { if (countFrame !== null) cancelAnimationFrame(countFrame); countFrame = null; }
    function showPercent(percent, prefix) {
        countPrefix = prefix;
        if (countFrame !== null && percent === countTarget) return;
        countTarget = percent;
        if (countShown === null || !moving() || Math.abs(percent - countShown) <= 1) {
            stopCount(); countShown = percent; count.textContent = `${prefix}${percent}%`; return;
        }
        countFrom = countShown; countStart = performance.now();
        const step = time => {
            const k = Math.min(1, Math.max(0, (time - countStart) / 600)), eased = 1 - Math.pow(1 - k, 4);
            countShown = Math.round(countFrom + (countTarget - countFrom) * eased);
            count.textContent = `${countPrefix}${countShown}%`;
            countFrame = k < 1 ? requestAnimationFrame(step) : null;
        };
        if (countFrame === null) countFrame = requestAnimationFrame(step);
    }

    const fraction = value => Math.min(1, Math.max(0, value));
    const newRequest = message => message.new_request === true || message.overall?.reset === true
        || (message.reset === true && !message.phase);
    const report = el('button', 'fv-report-download fv-quiet'); report.type = 'button'; report.hidden = true;
    let reportId = null;
    const reportLabel = () => t('Download report', '下载报告');
    report.textContent = reportLabel();
    function updateReport(message) {
        if (newRequest(message)) { reportId = null; report.hidden = true; }
        if (typeof message.report_id === 'string' && /^[a-f0-9]{32}$/.test(message.report_id)) {
            if (reportId !== message.report_id) { report.disabled = false; report.textContent = reportLabel(); }
            reportId = message.report_id; report.hidden = false;
        }
        // The saved video's request record is different from the redacted
        // diagnostic download. Keep this token available after completion;
        // only a new request clears it.
        return reportId;
    }
    report.onclick = async () => {
        if (!reportId || report.disabled) return;
        const id = reportId;
        report.disabled = true; report.textContent = t('Preparing report…', '正在整理报告…');
        try {
            const path = `/freevideo/report/${id}`;
            const response = await (api ? api.fetchApi(path) : fetch(path));
            if (!response.ok) throw new Error('Report unavailable');
            const blob = await response.blob(), url = URL.createObjectURL(blob);
            const link = document.createElement('a'); link.href = url; link.download = 'video.debug.json';
            document.body.append(link); link.click(); link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            if (id === reportId) report.textContent = reportLabel();
        } catch {
            if (id === reportId) report.textContent = t('Report unavailable · retry', '报告暂不可用 · 重试');
        } finally { if (id === reportId) report.disabled = false; }
    };
    const counted = value => Number.isInteger(value.total) && value.total > 0 && Number.isInteger(value.done)
        && value.done >= 0 && value.done <= value.total;

    function drawOverall() {
        // Keep compatibility with older workers while they are being updated.
        // Once `overall` is present, this branch is never used and the bar is
        // the monotonic whole-video estimate.
        if (!Object.keys(overall).length) {
            if (!counted(state)) {
                element.dataset.counted = 'false'; element.dataset.complete = 'false'; count.textContent = ''; fill.style.width = '';
                track.removeAttribute('aria-valuenow'); track.removeAttribute('aria-valuemax');
                return;
            }
            const raw = valid(state.display_fraction) ? fraction(state.display_fraction) : state.done / state.total;
            const percent = Math.floor(100 * raw);
            element.dataset.counted = 'true'; element.dataset.complete = 'false'; count.textContent = compact ? `${percent}%` : `${state.done} / ${state.total}`;
            element.style.setProperty('--fv-progress', `${100 * raw}%`);
            fill.style.width = `${100 * raw}%`; track.setAttribute('aria-valuemax', state.total);
            track.setAttribute('aria-valuenow', state.done); track.setAttribute('aria-label', t('Completed sampling steps', '已完成采样步数'));
            const base = detail.dataset.baseText || '';
            if (base) detail.textContent = `${base} · ${state.estimated ? '~' : ''}${percent}%`;
            return;
        }
        const complete = overall.status === 'complete';
        let candidate = valid(overall.fraction) ? fraction(overall.fraction) : null;
        const canEstimate = overall.status !== 'failed' && overall.estimated === true
            && valid(overall.phase_start_fraction) && valid(overall.phase_weight);
        if (canEstimate) {
            let local = null;
            if (state.phase === 'sampling' && counted(state) && !overall.sampling_time_weighted) {
                local = valid(state.display_fraction) ? fraction(state.display_fraction) : state.done / state.total;
                if (state.done < state.total && valid(state.estimated_step_seconds) && state.estimated_step_seconds > 0) {
                    const stepElapsed = (valid(state.step_elapsed_seconds) ? state.step_elapsed_seconds : 0)
                        + Math.max(0, (now() - sampledAt) / 1000);
                    local = Math.max(local, (state.done + Math.min(.88, .88 * stepElapsed / state.estimated_step_seconds)) / state.total);
                } else if (valid(overall.phase_seconds) && overall.phase_seconds > 0
                    && valid(overall.phase_elapsed_seconds)) {
                    // Before the first warm NFE supplies a per-step duration,
                    // use the report-weighted sampling estimate as a smooth
                    // visual guide. The synchronized NFE counter remains the
                    // source of truth and this estimate stops short of 100%.
                    const phaseElapsed = overall.phase_elapsed_seconds
                        + Math.max(0, (now() - overallAt) / 1000);
                    local = Math.max(local, Math.min(.95, phaseElapsed / overall.phase_seconds));
                }
            } else if (valid(overall.phase_seconds) && overall.phase_seconds > 0 && valid(overall.phase_elapsed_seconds)) {
                const phaseElapsed = overall.phase_elapsed_seconds + Math.max(0, (now() - overallAt) / 1000);
                // Time can move the estimate, but only an engine transition
                // finishes a phase. Long phases stop short of their boundary.
                local = Math.min(.95, phaseElapsed / overall.phase_seconds);
            }
            if (local !== null) candidate = Math.max(candidate ?? 0,
                canEstimate ? overall.phase_start_fraction + overall.phase_weight * local : local);
        }
        // A retry or a new phase cannot rewind the whole-video bar. Even a
        // complete sampling phase is not a completed/saved video.
        if (complete) shownFraction = 1;
        else if (candidate !== null) shownFraction = Math.max(shownFraction ?? 0, Math.min(.99, fraction(candidate)));
        const known = shownFraction !== null;
        element.dataset.counted = String(known);
        element.dataset.complete = String(complete);
        track.setAttribute('aria-label', t('Estimated overall video progress', '视频整体预估进度'));
        track.setAttribute('aria-valuemax', '100');
        if (known) {
            const percent = complete ? 100 : Math.floor(shownFraction * 100);
            showPercent(percent, complete || compact ? '' : '≈ ');
            element.style.setProperty('--fv-progress', `${100 * shownFraction}%`);
            fill.style.width = `${100 * shownFraction}%`;
            track.setAttribute('aria-valuenow', String(percent));
            track.setAttribute('aria-valuetext', `${complete || compact ? '' : '≈ '}${percent}%`);
        } else {
            stopCount(); countShown = null;
            count.textContent = '';
            fill.style.width = '';
            track.removeAttribute('aria-valuenow'); track.removeAttribute('aria-valuetext');
        }
    }

    function tick() {
        if (element.hidden) return;
        const ended = overall.status === 'complete' || overall.status === 'failed';
        if (state.phase === 'dependencies') {
            elapsed.hidden = eta.hidden = silence.hidden = true;
            drawOverall(); return;
        }
        const age = Math.max(0, (now() - sampledAt) / 1000);
        const overallAge = ended ? 0 : Math.max(0, (now() - overallAt) / 1000);
        const seconds = valid(overall.elapsed_seconds) ? overall.elapsed_seconds + overallAge : (now()-started)/1000;
        elapsed.textContent = `${t('Elapsed', '已用时')} ${duration(Math.max(0, seconds))}`;
        let left = valid(overall.remaining_seconds) ? Math.max(
            valid(overall.remaining_floor_seconds) ? overall.remaining_floor_seconds : 0,
            overall.remaining_seconds - overallAge) : null;
        if (!Object.keys(overall).length && left === null && state.phase === 'sampling' && counted(state)
            && state.uniform_remaining_steps !== false
            && valid(state.estimated_step_seconds) && state.estimated_step_seconds > 0) {
            const stepElapsed = (valid(state.step_elapsed_seconds) ? state.step_elapsed_seconds : 0)
                + Math.max(0, (now() - sampledAt) / 1000);
            left = Math.max(0, (state.total - state.done) * state.estimated_step_seconds - stepElapsed);
        }
        eta.hidden = compact && (ended || left === null || left <= 0);
        eta.textContent = compact && left > 0
            ? left >= 60 ? t(`About ${Math.ceil(left / 60)} min left`, `约剩 ${Math.ceil(left / 60)} 分钟`)
                : t(`About ${Math.ceil(left)} sec left`, `约剩 ${Math.ceil(left)} 秒`)
            : overall.status === 'complete' ? t('Video saved', '视频已保存')
            : overall.status === 'failed' ? t('Stopped', '已停止')
            : left !== null && left > 0 ? `${t('Remaining', '预计剩余')} ≈ ${duration(left)}`
            : t('Estimating remaining time…', '正在估算剩余时间…');
        elapsed.hidden = compact && (ended || (left !== null && left > 0));
        silence.hidden = ended || age < (compact ? 45 : 15);
        silence.textContent = t(`Last engine update ${duration(age)} ago`, `距上次引擎进度更新 ${duration(age)}`);
        drawOverall();
    }
    function update(message) {
        updateReport(message);
        // Older servers may still forward this diagnostic-only event. It is
        // not a stage change or something the user needs to act on.
        if (message.warning === 'ram_budget_warning' && !message.phase) return;
        const reset = newRequest(message);
        if (element.hidden || reset) {
            state = {}; overall = {}; started = now(); overallAt = now(); shownFraction = null;
            stopCount(); countShown = null;
            recovery.hidden = true; recovery.textContent = '';
        }
        if (!reset && message.retry && typeof message.retry === 'object') {
            const retry = message.retry;
            const previous = retry.failed_phase === 'decode' ? t('The previous decode', '上一次解码')
                : retry.failed_phase === 'sample_finalize' ? t('Finalizing the previous sampling result', '上一次采样后的收尾')
                : ['sample', 'sampling'].includes(retry.failed_phase) ? t('The previous sampling attempt', '上一次采样')
                : t('The previous attempt', '上一次尝试');
            const reason = retry.kind === 'gpu_oom' ? t(`${previous} did not fit in GPU memory.`, `${previous}的显存空间不足。`)
                : retry.kind === 'ram_pressure' ? t(`${previous} did not have enough RAM.`, `${previous}的内存空间不足。`)
                : t(`${previous} could not finish.`, `${previous}未完成。`);
            const action = t('Adjusting memory use and retrying this video.', '正在调整内存安排并重试本次生成。');
            const sampling = retry.reuse_sampling === true ? t('Sampling is saved; retrying decoding only.', '采样已保留，仅重试解码。')
                : retry.reuse_sampling === false ? t('Sampling will run again before decoding.', '将重新采样后解码。') : '';
            const attempt = Number.isInteger(retry.attempt) && retry.attempt > 1
                && Number.isInteger(retry.max_attempts) && retry.max_attempts >= retry.attempt
                ? t(`Attempt ${retry.attempt} of ${retry.max_attempts}.`, `第 ${retry.attempt} / ${retry.max_attempts} 次尝试。`) : '';
            const explanation = [reason, action, sampling, attempt].filter(Boolean).join(' ');
            // Reopening a view may replay the retry on every progress event.
            // Keep its live region quiet until the explanation actually changes.
            if (recovery.textContent !== explanation) recovery.textContent = explanation;
            recovery.hidden = false;
        }
        const phase = message.phase || message.label;
        if (message.reset || phase !== (state.phase || state.label)) state = {};
        state = {...state, ...message}; sampledAt = valid(message.received_at) ? message.received_at : now();
        element.dataset.phase = message.phase || message.timing_phase || '';
        if (message.overall && typeof message.overall === 'object') {
            overall = {...message.overall}; overallAt = sampledAt;
        }
        element.hidden = false;
        const sampling = message.phase === 'sampling';
        const complete = message.phase === 'complete' || overall.status === 'complete';
        const hasCount = counted(message);
        const downloadLabel = message.stage === 'reference_download'
            ? t('Preparing reference media resources', '正在准备参考音视频资源')
            : message.stage === 'preset_download' ? t('Preparing sampling preset', '正在准备采样档位') : null;
        label.textContent = complete ? t('Video saved', '视频已保存') : sampling ? t('Sampling', '采样')
            : downloadLabel ? downloadLabel
            : message.phase === 'recovery' ? t('Retrying this video', '正在重试本次生成')
            : message.phase === 'sample_finalize' ? {
                latent_validation: t('Checking completed sampling', '正在检查采样结果'),
                latent_save: t('Saving completed sampling', '正在保存采样结果'),
                offload_release: t('Releasing sampling buffers', '正在释放采样缓冲'),
                transformer_release: t('Preparing video decoding', '正在准备视频解码'),
            }[message.stage] || t('Preparing video decoding', '正在准备视频解码')
            : message.timing_phase === 'encoding' ? encodingLabel(message.label, t)
            : message.label || t('Preparing video', '正在准备视频');
        if (compact && !complete) {
            const phase = message.phase || message.timing_phase;
            label.textContent = sampling ? `${t('Sampling', '采样')} ${message.done} / ${message.total}`
                : phase === 'encoding' ? encodingLabel(message.label, t)
                : phase === 'load' ? downloadLabel || t('Loading model', '加载模型')
                : phase === 'decode' ? message.label === 'Saving MP4 and audio' ? t('Saving video', '保存视频')
                    : message.label === 'Loading and decoding audio' ? t('Decoding audio', '解码音频')
                    : t('Decoding video', '解码视频')
                : phase === 'sample_finalize' ? t('Preparing output', '准备输出')
                : phase === 'recovery' ? t('Adjusting memory · retrying', '调整内存 · 重试中')
                : t('Preparing video', '准备生成');
        }
        if (message.phase === 'dependencies') label.textContent = message.stage === 'probe'
            ? t('Installing sampling cache · testing sources', '补安装采样缓存 · 正在测速')
            : t('Installing sampling cache', '补安装采样缓存');
        if (sampling && Number.isInteger(message.block)) {
            detail.textContent = t(`Step ${message.done+1} · processing layer ${message.block} / ${message.blocks}`,
                `第 ${message.done+1} 步 · 正在处理第 ${message.block} / ${message.blocks} 层`);
        } else if (sampling) {
            detail.textContent = message.done === message.total ? t('Sampling complete · preparing output', '采样完成 · 正在准备输出')
                : message.stage === 'inputs' ? t('Preparing sampling inputs', '正在准备采样输入')
                : message.done > 0 ? t(`${message.done} steps complete`, `已完成 ${message.done} 步`)
                : t('Starting the first step', '正在开始第一步');
        } else detail.textContent = message.detail || '';
        if ((downloadLabel || message.phase === 'dependencies') && valid(message.done) && valid(message.total)) {
            detail.textContent = `${(message.done/2**20).toFixed(1)} / ${(message.total/2**20).toFixed(1)} MiB`
                + (valid(message.bytes_per_second) && message.bytes_per_second > 0 ? ` · ${(message.bytes_per_second/2**20).toFixed(1)} MiB/s` : '');
        } else if (hasCount) detail.textContent = `${message.done} / ${message.total}${detail.textContent ? ' · ' + detail.textContent : ''}`;
        detail.dataset.baseText = detail.textContent;
        note.hidden = !(sampling && message.done === 0);
        note.textContent = t('New kernels may compile on the first step. Disk caches speed up later runs.',
            '首次遇到的新内核可能在第一步编译，缓存会保存在硬盘上供后续复用。');
        tick(); if (timer === null) timer = setInterval(tick, 250);
    }
    function hide() {
        element.hidden = true; recovery.hidden = true; recovery.textContent = '';
        if (timer !== null) clearInterval(timer); timer = null; state = {}; overall = {}; shownFraction = null;
        stopCount(); countShown = null;
    }
    return {element, report, updateReport, update, hide, dispose: hide};
}
