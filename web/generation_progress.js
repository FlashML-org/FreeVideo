// One whole-video estimate and the same five rows in Studio and the node view.
import { saveReport } from './report_issue.js';
const css = document.createElement('link');
css.rel = 'stylesheet'; css.href = new URL('./generation_progress.css', import.meta.url).href;
document.head.append(css);
const el = (tag, cls) => { const node = document.createElement(tag); node.className = cls; return node; };
const valid = value => Number.isFinite(value) && value >= 0;
const duration = value => {
    const seconds = Math.floor(value);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
};

const progressFields = ['phase', 'stage', 'timing_phase', 'label', 'done', 'total', 'block'];
// Only our status sentences, scoped to a request; no prompt or engine log.
// A reopened Studio/tab must not announce the replayed explanation again.
const announcementKey = 'freevideo.progress.announcements';
const requestAnnouncements = new Map();
function announcementsFor(message, reset) {
    const key = message.report_id || (message.stream_id && message.node != null ? `${message.stream_id}:${message.node}` : null);
    if (!key) return null;
    if (!requestAnnouncements.has(key)) {
        try {
            const saved = JSON.parse(sessionStorage.getItem(announcementKey) || '{}')[key];
            if (saved && Array.isArray(saved.messages)) requestAnnouncements.set(key, saved);
        } catch { /* Storage can be unavailable in private or embedded views. */ }
    }
    let record = requestAnnouncements.get(key);
    const marker = message.sequence ?? message.received_at;
    if (!record || reset && record.reset !== marker) record = {reset: marker, messages: []};
    requestAnnouncements.set(key, record);
    while (requestAnnouncements.size > 32) requestAnnouncements.delete(requestAnnouncements.keys().next().value);
    return record;
}
// Lane set these floors from 29 reports. A typical segment may need longer.
const STALL_SECONDS_BY_PHASE = Object.freeze({
    starting: 180, encoding: 180, load: 180, sampling: 180, refinement: 180,
    latent_upscale: 180, sample_finalize: 180, decode: 180, save: 180,
    dependencies: 180, recovery: 180, other: 180,
});
// Only these fixed stage sentences reach either progress view, with at most the
// sampling step or a download's size and speed. Engine labels, layer, block and
// frame counts and the original sampling plan remain available in the report.
// Stage words the product already uses: settings say 一采 / 二采, results say 解码.
// No "正在": the stage line sits beside a moving clock and a small bar.
const ACTIVITY = {
    prepare: ['Preparing', '准备中'],
    prompt: ['Encoding the prompt', '编码提示词'],
    combined: ['Encoding the prompt and references', '编码提示词和参考素材'],
    load: ['Loading the video model', '载入视频模型'],
    decode: ['Decoding the video', '解码视频'],
    save: ['Saving the video', '保存视频'],
};
const LABEL_ACTIVITY = {
    prepare: ['Starting generation process', 'Preparing video', 'Checking saved video', 'Resource forecast ready'],
    prompt: ['Starting text encoder', 'Preparing text encoder GPU', 'Checking text encoder cache',
        'Loading text encoder', 'Reading text encoder weights', 'Preparing text encoder model',
        'Loading text encoder onto GPU', 'Reusing text encoder', 'Using text encoder already on GPU',
        'Preparing text encoding', 'Encoding prompt', 'Releasing encoder weights after insufficient GPU memory',
        'Retrying text encoding with more GPU workspace', 'Leaving more GPU memory for text encoding',
        'Preparing prompt data', 'Saving prompt cache', 'Reusing prompt cache', 'Prompt ready',
        'Releasing idle models and checking available memory again'],
    combined: ['Encoding text and images', 'Preparing the prompt and images', 'Preparing text and image tokens'],
    reference: ['Preparing reference media', 'Retaining and checking input media', 'Encoding input media',
        'Encoding reference media', 'Encoding image/video references', 'Encoding reference audio',
        'Encoding long references', 'Encoding long references in smaller blocks'],
    load: ['Loading the video model', 'Loading video model', 'Loading native MPS model', 'Loading cached video model',
        'Preparing offloaded weight storage', 'Video model ready', 'Loading LoRAs', 'Preparing two-pass upscaler',
        'Low-memory mode for this GPU'],
    refine: ['Upscaling before the second pass', 'Continuing from the preview', 'Reusing the completed first pass'],
    decode: ['Loading video VAE', 'Decoding video and audio', 'Decoding the video', 'Decoding video tiles',
        'Loading and decoding audio', 'Reusing completed sampling · retrying video and audio decoding',
        'Checking completed sampling', 'Saving completed sampling', 'Releasing sampling buffers', 'Preparing video decoding'],
    save: ['Saving MP4 and audio'],
};
// Engine steps that run only when the request has reference media.
const REFERENCE_WORK = new Set(['Encoding reference media', 'Encoding image/video references', 'Encoding reference audio',
    'Encoding long references', 'Encoding long references in smaller blocks']);
const labelActivities = new Map(Object.entries(LABEL_ACTIVITY).flatMap(([key, labels]) => labels.map(label => [label, key])));
const DOWNLOADS = {
    preset_download: ['Downloading sampling tables', '下载采样表'],
    dependencies: ['Downloading sampling tables', '下载采样表'],
    sources_download: ['Downloading model files', '下载模型文件'],
    reference_download: ['Downloading model files', '下载模型文件'],
};
const size = (bytes, unit) => (bytes / (unit === 'GiB' ? 2**30 : 2**20)).toFixed(1);
// The fixed stage key (for the small bar and replays) and its sentence. Each
// number appears once: sampling says the step, a download says its size and speed.
function activityLabel(message, t, plan = {}, secondPass = false, references = false) {
    const phase = message.phase || message.timing_phase || message.stage;
    const named = key => ({key, text: t(...ACTIVITY[key])});
    if (phase === 'dependencies' && message.stage === 'probe') return named('prepare');
    const download = DOWNLOADS[message.stage] || (phase === 'dependencies' ? DOWNLOADS.dependencies : null);
    if (download) {
        let detail = '';
        if (valid(message.total) && message.total > 0) {
            const unit = message.total >= 2**30 ? 'GiB' : 'MiB';
            const done = valid(message.done) ? Math.min(message.done, message.total) : 0;
            detail = ` · ${size(done, unit)} / ${size(message.total, unit)} ${unit}`;
            if (valid(message.bytes_per_second) && message.bytes_per_second > 0) {
                const speed = message.bytes_per_second / 2**20;
                detail += ` · ${speed >= 10 ? Math.round(speed) : speed.toFixed(1)} MiB/s`;
            }
        }
        return {key: 'download', text: t(download[0], download[1]) + detail};
    }
    const steps = (key, en, zh, step, total) => ({key, text: Number.isInteger(step) && Number.isInteger(total) && total > 0
        ? t(`${en} · step ${step} of ${total}`, `${zh} · 第 ${step} / ${total} 步`) : t(en, zh)});
    const base = Number.isInteger(plan.base_steps) && plan.base_steps > 0 ? plan.base_steps : null;
    const second = plan.second || plan.upscale_target;
    if (message.prediction || message.sampling_plan) return named('prepare');
    if (phase === 'recovery') return message.retry?.reuse_sampling === true ? named('decode')
        : message.retry?.reuse_first_pass === true ? steps('pass2', 'Second pass', '二采') : named('prepare');
    if (phase === 'sample_finalize') return named('decode');
    if (message.stage === 'latent_upscale' || phase === 'latent_upscale')
        return {key: 'upscale', text: Number.isInteger(second?.width) && Number.isInteger(second?.height)
            ? t(`Second pass · upscaling to ${second.width} × ${second.height}`,
                `二采 · 放大到 ${second.width} × ${second.height}`)
            : t('Second pass · upscaling', '二采 · 放大')};
    if (phase === 'sampling' || phase === 'refinement' || message.stage === 'preview_mismatch') {
        const done = Number.isInteger(message.done) ? message.done : null;
        const total = Number.isInteger(message.total) && message.total > 0 ? message.total : null;
        const twoPass = plan.enabled === true && base !== null;
        if ((secondPass || phase === 'refinement') && twoPass) {
            const refine = total !== null && total > base ? total - base : plan.refine_steps;
            const step = done === null ? null : Math.min(Math.max(1, done - base + 1), refine);
            return steps('pass2', 'Second pass', '二采', step, refine);
        }
        const count = twoPass ? base : total;
        const step = done === null || count === null ? null : Math.min(done + 1, count);
        if (twoPass && plan.preview) return steps('preview', 'Preview', '预览', step, count);
        return twoPass ? steps('pass1', 'First pass', '一采', step, count) : steps('pass1', 'Sampling', '采样', step, count);
    }
    const key = labelActivities.get(message.label) || {
        starting: 'prepare', prepare: 'prepare', prediction: 'prepare', encoding: 'combined',
        load: 'load', decode: 'decode', save: 'save',
    }[phase] || labelActivities.get(message.stage);
    if (!key) return null;
    if (key === 'refine') return {key: 'upscale', text: t('Second pass · upscaling', '二采 · 放大')};
    // One sentence for the whole encoding stage. The engine names images in its
    // steps either way; only a request with reference media says so.
    if (key === 'prompt' || key === 'combined' || key === 'reference')
        return {key: 'encode', text: t(...ACTIVITY[references ? 'combined' : 'prompt'])};
    // LoRA preparation counts LoRA files, then the model counts its own blocks:
    // the same sentence, but its own small bar, so a full LoRA count is not carried on.
    if (key === 'load' && message.label === 'Loading LoRAs') return {key: 'lora', text: t(...ACTIVITY.load)};
    return {key, text: t(...ACTIVITY[key])};
}

function retryText(retry, t) {
    const reason = {gpu_oom: ['Out of GPU memory', '显存不足'], ram_pressure: ['Out of RAM', '内存不足'],
        unified_memory: ['Out of unified memory', '统一内存不足']}[retry.kind]
        || ['Previous attempt did not finish', '上一次尝试没有完成'];
    if (retry.reuse_sampling === true) return t(`${reason[0]}; sampling kept, retrying decoding.`,
        `${reason[1]}，采样已保留，正在重试解码。`);
    const kept = retry.reuse_first_pass === true;
    const action = retry.kind === 'gpu_oom' ? ['in low-memory mode', '改用省显存方式']
        : ['using less memory', '改用更省内存的设置'];
    return t(`${reason[0]}; ${kept ? 'first pass kept, ' : ''}retrying ${kept ? 'the second pass ' : ''}${action[0]}.`,
        `${reason[1]}，${kept ? '第一遍已保留，' : ''}正在${action[1]}重试${kept ? '第二遍' : ''}。`);
}

function trimStatus(rows, t) {
    if (!rows.length) return '';
    const kinds = new Set(rows.map(row => row.kind));
    const video = kinds.size === 1 && kinds.has('video'), audio = kinds.size === 1 && kinds.has('audio');
    const many = rows.length > 1;
    const noun = video ? (many ? 'reference videos' : 'reference video') : audio ? 'reference audio' : 'reference media';
    const zh = video ? '参考视频' : audio ? '参考音频' : '参考素材';
    const used = Number(rows[0].used_seconds).toFixed(1);
    return t(`The ${noun} ${video && !many || audio ? 'is' : 'are'} longer than the output, so only ${many ? `the first ${used}\u00a0s of each is used` : `its first ${used}\u00a0s is used`}.`,
        `${zh}比生成的视频长，只使用了${many ? '每段的' : ''}前 ${used}\u00a0秒。`);
}

// A reference clip longer than the video being generated is cut to the same
// length, at most 15 s, as in the official pipeline. Say so; never cut silently.
// The driver held the clock down for heat for most of the run (summary.thermal).
export function thermalText(thermal, t) {
    const share = thermal.sm_clock_max_mhz ? Math.round(100 * thermal.sm_clock_mean_mhz / thermal.sm_clock_max_mhz) : null;
    const heat = Number.isFinite(thermal.temperature_mean_c) ? Math.round(thermal.temperature_mean_c) : null;
    const detail = [heat !== null ? `${heat} °C` : '', share !== null ? t(`${share}% of its peak clock`, `频率只有峰值的 ${share}%`) : ''].filter(Boolean).join(t(', ', '，'));
    return t(`The GPU spent most of this video in thermal slowdown${detail ? ` (${detail})` : ''}, so it took much longer than usual. Improve cooling; on a laptop, plug in and choose a high-performance power mode.`,
        `生成这段视频时，显卡大部分时间因过热降频${detail ? `（${detail}）` : ''}，所以比平时慢很多。请改善散热；笔记本请接通电源并选择高性能模式。`);
}

export function referenceTrimText(row, t) {
    const kind = row?.kind === 'audio' ? t('Reference audio', '参考音频') : t('Reference video', '参考视频');
    const name = Number.isInteger(row?.number) ? `${kind} ${row.number}` : kind;
    const used = Number(row?.used_seconds).toFixed(1);
    const rule = t('(references are cut to the generated length, up to 15 s)', '（参考素材只取与生成视频等长的部分，最长 15 秒）');
    return valid(row?.seconds)
        ? t(`${name} is ${Number(row.seconds).toFixed(1)} s: only its first ${used} s were used ${rule}`,
            `${name} 时长 ${Number(row.seconds).toFixed(1)} 秒，已截取前 ${used} 秒${rule}`)
        : t(`${name} is longer than this video: only its first ${used} s were used ${rule}`,
            `${name} 比生成的视频长，已截取前 ${used} 秒${rule}`);
}

export function createGenerationProgress(t, now = () => Date.now(), {compact = false, api = null, onLayout = null} = {}) {
    const element = el('section', 'fv-generation-progress'); element.hidden = true;
    if (compact) element.classList.add('fv-generation-compact');
    // Chinese lines break between phrases, not inside a word (see the CSS).
    element.lang = t('en', 'zh-CN');
    const heading = el('div', 'fv-generation-heading');
    const label = el('div', 'fv-generation-label'), count = el('strong', 'fv-generation-count');
    const mini = el('div', 'fv-generation-mini'), miniFill = el('div', 'fv-generation-mini-fill');
    mini.append(miniFill); mini.hidden = true; mini.setAttribute('aria-hidden', 'true');
    heading.append(count);
    const track = el('div', 'fv-generation-track'), fill = el('div', 'fv-generation-fill');
    track.setAttribute('role', 'progressbar'); track.setAttribute('aria-valuemin', '0'); track.append(fill);
    const times = el('div', 'fv-generation-times');
    const elapsed = el('span', ''), eta = el('span', ''); times.append(elapsed, eta);
    const status = el('div', 'fv-generation-status'); status.hidden = true;
    const live = el('div', 'fv-generation-live');
    live.setAttribute('aria-live', 'polite'); live.setAttribute('aria-atomic', 'true');
    const stall = el('p', 'fv-generation-note fv-generation-stall'); stall.hidden = true;
    stall.setAttribute('role', 'status');
    const stallText = el('span', ''); stall.append(stallText);
    const trims = new Map();
    element.append(heading, track, label, mini, times, status, live);
    let recovery = '', lowMemory = false, compatibility = null, samplingPlan = {}, secondPass = false, references = false;
    let hasHeartbeat = false;
    // Mac: the unified-memory notice of this request, and whether its first pass has ended.
    let memoryTight = null, firstPassOver = false;
    let state = {}, overall = {}, started = 0, sampledAt = 0, overallAt = 0, timer = null, elapsedShown = 0;
    let lastProgressAt = 0, previousProgress = null, segmentStartedAt = 0, segmentKey = '';
    let pauseAnnounced = false, lastExplanation = '', announcedMessages = new Set();
    let announcementRecord = null;
    let remainingSeconds = null;
    let completedSteps = 0, previousRemaining = null, remainingAt = 0, lastActivity = null;
    // Once the shown time reaches zero before the video is saved, it stays hidden for this request.
    // The last minute runs on real time even while an estimate holds the number.
    let remainingRetired = false, lastMinuteAt = null, lastMinute = 0;
    let shownFraction = null;
    // The visible percentage counts toward its value with the bar instead of
    // jumping. Accessible values are set exactly and immediately elsewhere.
    let countShown = null, countTarget = null, countFrom = 0, countStart = 0, countFrame = null, countPrefix = '';
    const moving = () => typeof requestAnimationFrame === 'function'
        && !(typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches);
    function stopCount() { if (countFrame !== null) cancelAnimationFrame(countFrame); countFrame = null; }
    function showPercent(percent, prefix, immediate = false) {
        countPrefix = prefix;
        if (countFrame !== null && percent === countTarget && !immediate) return;
        countTarget = percent;
        // A saved video reads 100% at once, never "99%" beside "Video saved".
        if (immediate || countShown === null || !moving() || Math.abs(percent - countShown) <= 1) {
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
    // The redacted diagnostic export; throws when it is unavailable.
    async function fetchReport() {
        const id = reportId;
        if (!id) throw new Error('Report unavailable');
        const path = `/freevideo/report/${id}`;
        const response = await (api ? api.fetchApi(path) : fetch(path));
        if (!response.ok) throw new Error('Report unavailable');
        return response.blob();
    }
    async function downloadReport() { saveReport(await fetchReport()); }
    report.onclick = async () => {
        if (!reportId || report.disabled) return;
        const id = reportId;
        report.disabled = true; report.textContent = t('Preparing report…', '正在整理报告…');
        try {
            await downloadReport();
            if (id === reportId) report.textContent = reportLabel();
        } catch {
            if (id === reportId) report.textContent = t('Report unavailable · retry', '报告暂不可用 · 重试');
        } finally { if (id === reportId) report.disabled = false; }
    };
    const counted = value => Number.isInteger(value.total) && value.total > 0 && Number.isInteger(value.done)
        && value.done >= 0 && value.done <= value.total;

    function drawOverall() {
        // Stage counters cannot substitute for a whole-video estimate.
        if (!Object.keys(overall).length) {
            stopCount(); countShown = null;
            heading.hidden = true;
            element.dataset.counted = 'false'; element.dataset.complete = 'false'; count.textContent = ''; fill.style.width = '';
            track.setAttribute('aria-label', t('Estimated overall video progress', '视频整体预估进度'));
            track.removeAttribute('aria-valuenow'); track.removeAttribute('aria-valuemax'); track.removeAttribute('aria-valuetext');
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
        // Below 1% a "0%" looks stuck: show no number and let the bar sweep,
        // as before any estimate. The row takes no space until it has a value;
        // the node view sizes its panel after each update (percentShown).
        const known = shownFraction !== null && (complete || shownFraction >= .01);
        heading.hidden = !known;
        element.dataset.counted = String(known);
        element.dataset.complete = String(complete);
        track.setAttribute('aria-label', t('Estimated overall video progress', '视频整体预估进度'));
        track.setAttribute('aria-valuemax', '100');
        if (known) {
            const percent = complete ? 100 : Math.floor(shownFraction * 100);
            showPercent(percent, '', complete);
            element.style.setProperty('--fv-progress', `${100 * shownFraction}%`);
            fill.style.width = `${100 * shownFraction}%`;
            track.setAttribute('aria-valuenow', String(percent));
            track.setAttribute('aria-valuetext', `${percent}%`);
        } else {
            stopCount(); countShown = null;
            count.textContent = '';
            fill.style.width = '';
            track.removeAttribute('aria-valuenow'); track.removeAttribute('aria-valuetext');
        }
    }

    // A segment can take several minutes. Extended silence gets a notice once;
    // leave the run going, and
    // offer the report, whose engine log shows the step it stopped at.
    let stallHasReport = null, stallLoading = null;
    function drawStall(age, ended) {
        const floor = STALL_SECONDS_BY_PHASE[state.phase || state.timing_phase] ?? STALL_SECONDS_BY_PHASE.other;
        const threshold = Math.max(floor, valid(overall.typical_seconds) ? 2 * overall.typical_seconds : 0);
        const shown = !ended && age >= threshold;
        const wasShown = !stall.hidden;
        const hasReport = reportId !== null;
        // Shown after 3 minutes, without saying so: the card shows two times only.
        const loading = (state.phase || state.timing_phase) === 'load';
        if (shown && (stall.hidden || hasReport !== stallHasReport || loading !== stallLoading)) {
            const first = loading
                ? t('No loading progress for a while. Generation is still running, so you can keep waiting.',
                    '已经有一段时间没有新的加载进度，生成仍在进行，可以继续等待。')
                : t('No progress for a while. Generation is still running, so you can keep waiting.',
                    '已经有一段时间没有新的进度，生成仍在进行，可以继续等待。');
            const sentence = hasReport ? first + t(
                ' You can also use Download report below and submit the report in a GitHub issue; we will look into it and work on a fix for you.',
                '也可以点击下方的“下载报告”并提交到 GitHub issue，我们会专门排查和优化，为您解决这个问题。') : first;
            stallLoading = loading;
            if (hasReport) {
                const buttonName = el('span', ''); buttonName.style.whiteSpace = 'nowrap';
                buttonName.textContent = t('Download report', '“下载报告”');
                const offset = sentence.indexOf(buttonName.textContent);
                // A copy edit that drops the name shows the sentence as it is.
                if (offset < 0) stallText.textContent = sentence;
                else stallText.replaceChildren(document.createTextNode(sentence.slice(0, offset)), buttonName,
                    document.createTextNode(sentence.slice(offset + buttonName.textContent.length)));
            } else stallText.textContent = sentence;
            stallHasReport = hasReport;
        }
        if (shown && hasReport) report.dataset.emphasis = 'true';
        else delete report.dataset.emphasis;
        stall.hidden = !shown;
        if (shown !== wasShown) layout();
    }
    function layout() {
        try { onLayout?.(); }
        catch (error) { console.warn('FreeVideo progress layout:', error?.message ?? error); }
    }

    // The stage the engine is in, timed from its first message (also in a
    // reopened view). Each download is its own stage, so a second file starts from zero.
    // The stage line names a stage once it has lasted 2 s. Leaving that stage and
    // coming back within 2 s, whatever came between was stray messages (e.g. the
    // sampling plan and the upscaler being prepared during encoding): the stage
    // resumes with its own clock and bar, and the stage line never shows the others.
    let stageId = null, stageSince = 0, labelId = null, labelText = '', away = null;
    // Small bar under the stage line. A counted stage shows its real share after
    // 5 s; a slow stage without counts sweeps after 10 s; a shown bar stays at
    // least 1 s, and a counted stage without progress for 15 s gets a moving light.
    // Nothing else carries into the next stage.
    let miniFraction = null, miniShownAt = null, miniMode = null, miniShown = null, miniShimmer = false;
    let holdUntil = 0, holdMode = null, holdFraction = null;
    function miniTarget() {
        const key = lastActivity?.key;
        const counted = value => Number.isFinite(value?.[0]) && Number.isFinite(value?.[1]) && value[1] > 0
            ? Math.max(0, Math.min(1, value[0] / value[1])) : null;
        if (key === 'download' || key === 'load' || key === 'lora' || key === 'decode' || key === 'encode') {
            const share = counted([state.done, state.total]);
            if (share !== null) return {mode: 'determinate', fraction: share};
            // Slow without a count (an older engine, an audio pass): sweep only.
            return {mode: 'indeterminate'};
        }
        if (key === 'upscale') return {mode: 'indeterminate'};
        if (key === 'pass1' || key === 'preview' || key === 'pass2') {
            const base = Number.isInteger(samplingPlan.base_steps) ? samplingPlan.base_steps : 0;
            const total = Number.isInteger(state.total) ? state.total : null;
            const done = Number.isInteger(state.done) ? state.done : null;
            const layer = state.stage === 'layers' && Number.isFinite(state.block) && Number.isFinite(state.blocks) && state.blocks > 0
                ? Math.max(0, Math.min(1, state.block / state.blocks)) : 0;
            if (done === null || total === null) return {mode: 'indeterminate'};
            // The first step compiles before its first layer: nothing to count yet.
            if (done === 0 && !layer) return {mode: 'indeterminate'};
            const [from, count] = key === 'pass2' ? [base, total - base] : [0, base > 0 && samplingPlan.enabled ? base : total];
            return count > 0 ? {mode: 'determinate', fraction: Math.max(0, Math.min(1, (done - from + layer) / count))} : null;
        }
        return null;
    }
    // The current stage has lasted 2 s: the stage line names it from now on.
    function settle(at) {
        if (stageId !== null && stageId !== labelId && at - stageSince >= 2000) {
            labelId = stageId; labelText = lastActivity?.text ?? labelText; away = null;
        }
    }
    function enterStage(id, at) {
        if (id === stageId) return;
        if (id === labelId && away && at - away.leftAt < 2000) {
            stageId = id; stageSince = away.since; miniFraction = away.fraction;
            away = null; holdUntil = 0; return;
        }
        if (stageId !== null && stageId === labelId) away = {since: stageSince, fraction: miniFraction, leftAt: at};
        // A bar shown under 1 s finishes its second; a longer one goes at once.
        holdUntil = !mini.hidden && miniShownAt !== null ? miniShownAt + 1000 : 0;
        holdMode = miniMode; holdFraction = miniShown;
        stageId = id; stageSince = at; miniFraction = null; miniShownAt = null;
    }
    function resetMini() {
        stageId = null; away = null; labelId = null; labelText = '';
        miniFraction = null; miniShownAt = null; miniMode = null; miniShown = null; miniShimmer = false; holdUntil = 0;
        mini.hidden = true; mini.dataset.mode = ''; mini.dataset.shimmer = 'false'; miniFill.style.width = '';
    }
    function drawMini(age, ended) {
        const target = ended || stageId === null ? null : miniTarget();
        const running = (now() - stageSince) / 1000;
        let show = !!target && running > (target.mode === 'determinate' ? 5 : 10);
        if (show) {
            miniMode = target.mode; holdUntil = 0;
            if (target.mode === 'determinate') miniFraction = Math.max(miniFraction ?? 0, target.fraction);
            miniShown = target.mode === 'determinate' ? miniFraction : null;
            if (miniShownAt === null) miniShownAt = now();
        } else if (!ended && miniShownAt !== null && now() - miniShownAt < 1000) show = true; // no one-frame flash
        else if (!ended && now() < holdUntil) { show = true; miniMode = holdMode; miniShown = holdFraction; }
        if (!show) { miniShownAt = null; miniMode = null; miniShown = null; }
        miniShimmer = show && miniMode === 'determinate' && age > 15;
        mini.hidden = !show;
        mini.dataset.mode = miniMode || '';
        mini.dataset.shimmer = String(miniShimmer);
        miniFill.style.width = show && miniMode === 'determinate' ? `${100 * (miniShown ?? 0)}%` : '';
    }
    // A new stage's sentence replaces the previous one once that stage has
    // lasted 2 s; within a stage it updates at once.
    function stageText() {
        // No activity yet, or a bare reset: the next stage is named at once.
        if (!lastActivity) { labelId = null; return t(...ACTIVITY.prepare); }
        settle(now());
        if (labelId === null) { labelId = stageId; away = null; }
        if (labelId === stageId) labelText = lastActivity.text;
        return labelText;
    }
    function drawActivity(age, ended) {
        const base = ended ? (overall.status === 'complete' || state.phase === 'complete' ? savedText() : t('Stopped', '已停止'))
            : stageText();
        const paused = !ended && age > 15;
        // Two times only (elapsed, remaining): a pause shows on the small bar, not as text.
        if (label.textContent !== base) label.textContent = base;
        drawMini(age, ended);
        const firstSlow = state.phase === 'sampling' && state.stage !== 'latent_upscale' && state.done === 0 && paused;
        const level = ended ? '' : recovery ? 'retry' : firstSlow ? 'first-step'
            : lowMemory ? 'memory' : memoryTight ? 'unified-memory' : compatibility ? 'compatibility' : trims.size ? 'notice' : '';
        const explanation = {retry: recovery,
            'first-step': t('The first run at this size, or after an update, takes longer to start.',
                '首次使用这个尺寸或更新后，开头要多花一些时间。'),
            memory: t('This generation uses low-memory mode; it is slower than usual but will finish.',
                '这次生成使用省显存方式，会比平时慢，但能完成。'),
            // Mac, level 3 with low-memory mode (never both). The measured numbers go to the
            // report only. Closing applications during the first pass can still give the
            // second pass room, so that advice shows only until then.
            'unified-memory': memoryTight?.phase === 'refinement' && !firstPassOver
                ? t('Less unified memory is available. Close memory-heavy applications before the first pass ends so the second pass is not slowed down.',
                    '可用的统一内存较少。在第一遍结束前关闭占用内存较多的程序，第二遍就不会变慢。')
                : t('Less unified memory is available, so this generation will be slower than usual.',
                    '可用的统一内存较少，这次生成会比平时慢。'),
            compatibility: compatibility ? t(`Compatibility setting ${compatibility.name_en} is on, so generation is slower than usual.`,
                `已开启兼容性设置（${compatibility.name_zh}档），生成会比平时慢。`) : '',
            notice: trimStatus([...trims.values()], t)}[level] || '';
        if (status.textContent !== explanation) status.textContent = explanation;
        status.hidden = !explanation; status.dataset.level = level;
        const announcements = [];
        if (paused && !pauseAnnounced) { announcements.push(base); pauseAnnounced = true; }
        if (explanation !== lastExplanation && explanation && !announcedMessages.has(explanation)) {
            announcements.push(explanation); announcedMessages.add(explanation);
            if (announcementRecord) {
                announcementRecord.messages = [...announcedMessages];
                try { sessionStorage.setItem(announcementKey, JSON.stringify(Object.fromEntries(requestAnnouncements))); }
                catch { /* In-memory deduplication still works. */ }
            }
        }
        lastExplanation = explanation;
        if (announcements.length) live.textContent = announcements.join(t(' ', ''));
    }

    // Preserve the server's heartbeat/floor policy for the raw target only.
    // The displayed estimate has its own clock and never increases.
    function rawRemaining() {
        if (!valid(overall.remaining_seconds)) return null;
        return hasHeartbeat ? overall.remaining_seconds : Math.max(
            valid(overall.remaining_floor_seconds) ? overall.remaining_floor_seconds : 0,
            overall.remaining_seconds - Math.max(0, (now() - overallAt) / 1000));
    }
    function observeRemaining(message) {
        if (completedSteps < 2 || remainingSeconds !== null || remainingRetired || !message.overall) return;
        const raw = valid(message.overall.remaining_seconds) ? rawRemaining() : null;
        // Compare distinct received messages, never repeated timer ticks.
        // Zero is stable only against another zero.
        if (raw !== null && previousRemaining !== null
            && Math.max(raw, previousRemaining) <= 1.25 * Math.min(raw, previousRemaining)) {
            remainingSeconds = raw; remainingAt = now();
        }
        previousRemaining = raw;
    }
    function resetRemaining() {
        completedSteps = 0; previousRemaining = null; remainingSeconds = null; remainingAt = now();
        remainingRetired = false; lastMinuteAt = null; lastMinute = 0;
    }
    function tick() {
        if (element.hidden) { drawStall(0, true); return; }
        const ended = ['complete', 'failed', 'cancelled'].includes(overall.status)
            || ['complete', 'failed', 'cancelled'].includes(state.phase);
        const age = Math.max(0, (now() - lastProgressAt) / 1000);
        const overallAge = ended ? 0 : Math.max(0, (now() - overallAt) / 1000);
        // The page counts from its first message until the engine's own elapsed time
        // arrives; that switch never moves the shown time back within a request.
        const seconds = elapsedShown = Math.max(elapsedShown,
            valid(overall.elapsed_seconds) ? overall.elapsed_seconds + overallAge : (now()-started)/1000);
        elapsed.textContent = `${t('Elapsed', '已用时')} ${duration(Math.max(0, seconds))}`;
        if (ended) remainingSeconds = null;
        if (remainingSeconds !== null) {
            // Never raised. An estimate at or above the shown time holds it, so
            // a slower run does not count down to "less than a minute" early.
            const dt = Math.max(0, (now() - remainingAt) / 1000);
            const target = rawRemaining();
            if (target === null) remainingSeconds = Math.max(0, remainingSeconds - dt);
            else if (target < remainingSeconds - dt) {
                const counted = Math.max(0, remainingSeconds - dt);
                remainingSeconds = counted + (target - counted) * (1 - Math.exp(-dt / 15));
            } else if (target < remainingSeconds) remainingSeconds = target;
            if (remainingSeconds < 60 && lastMinuteAt === null) { lastMinuteAt = now(); lastMinute = remainingSeconds; }
            // A run slower than estimated would leave "less than a minute" up for minutes:
            // once that minute has passed, or the time reaches zero, stop showing it.
            if (!ended && (remainingSeconds <= 0
                || lastMinuteAt !== null && (now() - lastMinuteAt) / 1000 >= lastMinute)) {
                remainingSeconds = null; remainingRetired = true;
            }
        }
        remainingAt = now();
        eta.hidden = remainingSeconds === null;
        eta.textContent = remainingSeconds === null ? '' : remainingSeconds >= 60
            ? t(`About ${Math.ceil(remainingSeconds / 60)}\u00a0min left`, `预计还需约 ${Math.ceil(remainingSeconds / 60)}\u00a0分钟`)
            : t('Less than a minute left', '预计还需不到 1\u00a0分钟');
        elapsed.hidden = false; times.hidden = elapsed.hidden && eta.hidden;
        drawStall(age, ended);
        drawActivity(age, ended);
        drawOverall();
    }
    // A finished first-pass preview is not the finished video.
    const savedText = () => state.label === 'Preview saved' ? t('Preview saved', '预览已保存') : t('Video saved', '视频已保存');
    function update(message) {
        updateReport(message);
        // Older servers may still forward this diagnostic-only event. It is
        // not a stage change or something the user needs to act on.
        if (message.warning === 'ram_budget_warning' && !message.phase) return;
        const reset = newRequest(message);
        if (element.hidden || reset) {
            state = {}; overall = {}; started = now(); overallAt = now(); shownFraction = null; elapsedShown = 0;
            lastProgressAt = valid(message.received_at) ? message.received_at : now();
            previousProgress = null; segmentKey = ''; segmentStartedAt = lastProgressAt;
            pauseAnnounced = false; lastExplanation = ''; announcedMessages.clear();
            live.textContent = ''; status.textContent = ''; resetRemaining(); lastActivity = null; resetMini();
            stopCount(); countShown = null;
            recovery = ''; lowMemory = false; compatibility = null; references = false; trims.clear();
            memoryTight = null; firstPassOver = false;
            samplingPlan = {}; secondPass = false; hasHeartbeat = false;
            announcementRecord = null;
        }
        const record = announcementsFor(message, reset);
        if (record) {
            announcementRecord = record;
            for (const text of record.messages) announcedMessages.add(text);
        }
        const receivedAt = valid(message.received_at) ? message.received_at : now();
        const heartbeat = message.overall?.heartbeat === true;
        if (message.reset === true) {
            hasHeartbeat = false; resetRemaining(); lastActivity = null;
            // A reset that would move a shown share back starts its stage afresh
            // (hidden, 5 s again). One before anything was counted, such as the
            // sampler's own start, keeps the stage's clock and the 2 s rule.
            if (miniFraction > 0) resetMini();
        }
        if (heartbeat) hasHeartbeat = true;
        if (!heartbeat && message.phase === 'sampling' && valid(message.done))
            completedSteps = Math.max(completedSteps, message.done);
        if (message.low_memory && typeof message.low_memory === 'object') lowMemory = true;
        if (message.compatibility && typeof message.compatibility === 'object') compatibility = message.compatibility;
        // Whether the request has reference media (first or last frame, references).
        if (typeof message.references === 'boolean') references = message.references;
        if (message.reference_trimmed || REFERENCE_WORK.has(message.label)) references = true;
        if (message.memory_tight && typeof message.memory_tight === 'object') memoryTight = message.memory_tight;
        // A reopened page only receives the latest progress message for this node.
        const firstPassSteps = memoryTight?.first_pass_steps;
        if (memoryTight?.first_pass_ended === true
            || ['latent_upscale', 'first_pass_reused'].includes(message.stage) || message.retry?.reuse_first_pass === true
            || (message.phase === 'sampling' && Number.isFinite(message.done) && Number.isFinite(firstPassSteps)
                && firstPassSteps > 0 && message.done >= firstPassSteps)
            || ['decode', 'complete'].includes(message.phase)) firstPassOver = true;
        if (message.reference_trimmed && valid(message.reference_trimmed.used_seconds)) {
            const row = message.reference_trimmed; trims.set(`${row.kind}:${row.number}`, row);
        }
        if (message.retry && typeof message.retry === 'object') recovery = retryText(message.retry, t);
        if (message.sampling_plan) samplingPlan = message.sampling_plan;
        // A reopened page gets only the latest row; its plan context names the pass.
        else if (message.plan_context && typeof message.plan_context === 'object' && !Object.keys(samplingPlan).length)
            samplingPlan = {...message.plan_context};
        // A heartbeat carries a newer estimate, never a newer engine event.
        // Keep sampledAt paired with the sampling counters/elapsed sample.
        if (heartbeat && previousProgress !== null) {
            overall = {...overall, ...message.overall}; overallAt = receivedAt;
            observeRemaining(message);
            tick(); return;
        }
        // Settings-only events do not replace the stage or refresh its clocks.
        const activityEvent = progressFields.some(key => key in message) || 'timing_phase' in message
            || message.prediction || message.sampling_plan || reset;
        if (!activityEvent && previousProgress !== null) {
            if (message.overall && typeof message.overall === 'object') {
                overall = {...message.overall}; overallAt = receivedAt; observeRemaining(message);
            }
            tick(); return;
        }
        if (!heartbeat && (previousProgress === null || progressFields.some(key => message[key] !== previousProgress[key])))
            lastProgressAt = previousProgress === null ? receivedAt : Math.max(lastProgressAt, receivedAt);
        if (!heartbeat) previousProgress = Object.fromEntries(progressFields.map(key => [key, message[key]]));
        if (message.stage === 'latent_upscale' || message.stage === 'first_pass_reused'
            || message.phase === 'refinement' || message.retry?.reuse_first_pass === true
            || (message.phase === 'sampling' && samplingPlan.enabled && !samplingPlan.preview
                && valid(samplingPlan.base_steps) && message.done >= samplingPlan.base_steps)) secondPass = true;
        if (message.phase === 'sampling' && message.done === 0 || message.stage === 'preview_mismatch') secondPass = false;
        const phase = message.phase || message.label;
        if (message.reset || phase !== (state.phase || state.label)) state = {};
        state = {...state, ...message}; sampledAt = receivedAt;
        // Events describe the current activity. In particular, the next sampling
        // event must not inherit latent_upscale or a previous step's layer count.
        for (const field of progressFields) if (!(field in message)) delete state[field];
        for (const field of ['prediction', 'sampling_plan']) if (!(field in message)) delete state[field];
        const activity = activityLabel(state, t, samplingPlan, secondPass, references);
        if (activity) {
            settle(receivedAt); // the stage just left may have lasted its 2 s
            enterStage(activity.key === 'download' ? `download:${state.stage || state.phase}` : activity.key, receivedAt);
            lastActivity = activity;
        }
        const key = JSON.stringify([state.phase || state.timing_phase, state.stage,
            state.phase === 'sampling' ? [state.done, state.total] : [state.label, lastActivity?.text]]);
        if (key !== segmentKey || message.reset) {
            segmentKey = key;
            segmentStartedAt = receivedAt - (state.phase === 'sampling' && valid(message.step_elapsed_seconds)
                ? message.step_elapsed_seconds * 1000 : 0);
            pauseAnnounced = false;
        }
        element.dataset.phase = message.phase || message.timing_phase || '';
        if (message.overall && typeof message.overall === 'object') {
            overall = {...message.overall}; overallAt = sampledAt;
            observeRemaining(message);
        }
        element.hidden = false;
        tick(); if (timer === null) timer = setInterval(tick, 250);
    }
    function hide() {
        element.hidden = true; status.hidden = true; recovery = ''; trims.clear(); lowMemory = false; compatibility = null; references = false; hasHeartbeat = false;
        memoryTight = null; firstPassOver = false;
        elapsedShown = 0;
        if (timer !== null) clearInterval(timer); timer = null; state = {}; overall = {}; shownFraction = null;
        stopCount(); countShown = null;
        resetRemaining(); lastActivity = null; resetMini(); previousProgress = null; segmentKey = ''; status.textContent = ''; live.textContent = '';
        // Last: the layout callback sizes the node from the cleared state.
        drawStall(0, true);
    }
    function snapshot() {
        const visible = !element.hidden;
        // This is the determinate CSS width target; RAF supplies the actually
        // displayed integer. Browser CSS transition geometry is not simulated.
        return {percent: visible ? countShown : null,
            bar: visible && element.dataset.counted === 'true' ? shownFraction : null,
            remaining_seconds: visible ? remainingSeconds : null,
            remaining_text: visible && !eta.hidden ? eta.textContent : null,
            label: visible ? label.textContent : '', status: visible ? status.textContent : '',
            long_stall: !stall.hidden,
            // stage_key is the engine's stage (the small bar's clock); label_key is the one the stage line names.
            stage_key: visible ? lastActivity?.key ?? null : null, label_key: visible && labelId !== null ? labelId.split(':')[0] : null,
            mini_visible: visible && !mini.hidden, mini_mode: visible && !mini.hidden ? miniMode : null,
            mini_fraction: visible && !mini.hidden && miniMode === 'determinate' ? miniShown : null,
            mini_shimmer: visible && !mini.hidden && miniShimmer};
    }
    return {element, stall, report, updateReport, fetchReport, downloadReport, hasReport: () => reportId !== null, update, hide, dispose: hide,
        noticeCount: () => trims.size, stallShown: () => !stall.hidden, percentShown: () => !heading.hidden, snapshot};
}
