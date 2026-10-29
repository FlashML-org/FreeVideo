// One whole-video estimate and the same five rows in Studio and the node view.
import { saveReport, reportIssue, showReportTip, focusReportTip } from './report_issue.js';
import { issueButton } from './issue_button.js';
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
// Iris's approved substage sentences. Unknown engine labels never reach the UI.
const SUBACTIVITY = {
    start: ['Starting generation', '正在启动生成'],
    libraries: ["Preparing the prompt model", '正在准备提示词模型'],
    weights: ['Reading the prompt model', '正在读取提示词模型'],
    gpu: ['Loading the prompt model', '正在加载提示词模型'],
    prompt: ['Processing the prompt', '正在处理提示词'],
    reference: ['Processing reference images and videos', '正在处理参考图片和视频'],
    depth: ['Extracting depth from the reference video', '正在提取参考视频的深度'],
    load: ['Loading the video model', '正在载入视频模型'],
    first: ['Running the first step (it takes longer the first time a size is used or after an update)',
        '正在进行第一步（首次使用这个尺寸或更新后，第一步要多花一些时间）'],
    upscale: ['Upscaling the video', '正在放大画面'],
    decoder: ['Preparing video output', '正在准备输出视频'],
    decode: ['Rendering the video', '正在输出视频'],
    audio: ['Rendering the audio', '正在输出音频'],
    save: ['Saving the video', '正在保存视频'],
};
// [EN, ZH, EN with a step, ZH with a step]; a request that samples twice adds which pass.
const SAMPLING_NAME = {
    single: ['Sampling', '正在采样', 'Step', '正在生成 · 第 '],
    first: ['Generating video', '正在生成视频', 'Step', '正在生成 · 第 '],
    pass2: ['Generating video', '正在生成视频', 'Step', '正在生成 · 第 '],
    preview: ['Generating a preview', '正在生成预览', 'Step', '正在生成 · 第 '],
};
const SAMPLING_PASS = {first: ['first pass', '一采'], pass2: ['second pass', '二采'], preview: ['preview', '预览']};
const SUBACTIVITY_LABELS = {
    'Extracting depth from the reference video': 'depth',
    'Starting generation process': 'start', 'Starting text encoder': 'libraries',
    'Loading text encoder': 'libraries', 'Preparing text encoder GPU': 'gpu',
    'Reading text encoder weights': 'weights', 'Preparing text encoder model': 'weights',
    'Loading text encoder onto GPU': 'gpu', 'Encoding prompt': 'prompt', 'Encoding text and images': 'prompt',
    'Loading video VAE': 'decoder', 'Loading and decoding audio': 'audio', 'Saving MP4 and audio': 'save',
    'Decoding the video': 'decode', 'Processing video colors': 'decode', 'Decoding audio': 'audio',
    'Saving decoded video': 'save', 'Saving decoded audio': 'save',
};
function subactivity(message, activity, t) {
    const phase = message.phase || message.timing_phase || message.stage;
    const label = message.label;
    let key = SUBACTIVITY_LABELS[label];
    const sampling = ['pass1', 'pass2', 'preview'].includes(activity?.key);
    if (sampling) {
        // Engines send kernel_prepare before every pass's first step; its count is the first pass's steps.
        if (message.done === 0 || message.stage === 'kernel_prepare') key = 'first';
        // A paused stage line carries no counts; the normal line keeps the user's steps.
        else return {key: `sampling:${activity.key}`, text: t(...SAMPLING_NAME.single.slice(0, 2)), sampling: true};
    }
    key ||= {prepare: phase === 'starting' ? 'start' : null, encode: LABEL_ACTIVITY.reference.includes(label)
        ? 'reference' : 'prompt', load: 'load', decode: 'decode', save: 'save', upscale: 'upscale'}[activity?.key];
    if (key) return {key, text: t(...SUBACTIVITY[key]), sampling};
    // Only a download name needs the fallback frame; every other activity already reads as a sentence.
    const named = activity && activity.key !== 'prepare' ? activity.text : '';
    return {key: `other:${activity?.key || 'prepare'}`, sampling,
        text: !named ? t(...ACTIVITY.prepare) : activity.key === 'download' ? t(`Still working: ${named}`, `仍在进行：${named}`) : named};
}
const waitDuration = (seconds, t) => {
    const n = Math.floor(Math.max(0, seconds)), minutes = Math.floor(n / 60), rest = n % 60;
    return minutes ? t(`${minutes} min${rest ? ` ${rest} s` : ''}`, rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`)
        : t(`${rest} s`, `${rest} 秒`);
};
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
    // A long video's part descriptions: the server waits at most 10 min for the model and
    // writes for at most 15 min, then splits the description by sentence on its own.
    describe: 1500,
});
// Normal stage sentences keep the sampling step or a download's size and speed.
// After 15 s of engine silence SUBACTIVITY names the work without counts.
// Raw engine labels and internal units remain available in the report.
// Stage words the product already uses: settings say 一采 / 二采, results say 解码.
const ACTIVITY = {
    prepare: ['Preparing', '正在准备'],
    describe: ['Writing part descriptions', '正在写各段描述'],
    prompt: ['Processing the prompt', '正在处理提示词'],
    combined: ['Processing the prompt and references', '正在处理提示词和参考素材'],
    load: ['Loading the video model', '正在载入视频模型'],
    decode: ['Rendering the video', '正在输出视频'],
    save: ['Saving the video', '正在保存视频'],
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
    reference: ['Extracting depth from the reference video', 'Preparing reference media', 'Retaining and checking input media', 'Encoding input media',
        'Encoding reference media', 'Encoding image/video references', 'Encoding reference audio',
        'Encoding long references', 'Encoding long references in smaller blocks'],
    load: ['Loading the video model', 'Loading video model', 'Loading native MPS model', 'Loading cached video model',
        'Preparing offloaded weight storage', 'Video model ready', 'Loading LoRAs', 'Preparing two-pass upscaler',
        'Low-memory mode for this GPU'],
    refine: ['Upscaling before the second pass', 'Continuing from the preview', 'Reusing the completed first pass'],
    decode: ['Loading video VAE', 'Decoding video and audio', 'Decoding the video', 'Decoding video tiles',
        'Loading and decoding audio', 'Processing video colors', 'Decoding audio',
        'Reusing completed sampling · retrying video and audio decoding',
        'Checking completed sampling', 'Saving completed sampling', 'Releasing sampling buffers', 'Preparing video decoding'],
    save: ['Saving MP4 and audio', 'Saving decoded video', 'Saving decoded audio'],
};
// Engine steps that run only when the request has reference media.
const REFERENCE_WORK = new Set(['Encoding reference media', 'Encoding image/video references', 'Encoding reference audio',
    'Encoding long references', 'Encoding long references in smaller blocks']);
const labelActivities = new Map(Object.entries(LABEL_ACTIVITY).flatMap(([key, labels]) => labels.map(label => [label, key])));
const DOWNLOADS = {
    preset_download: ['Downloading files for generation', '正在下载生成所需文件'],
    dependencies: ['Downloading files for generation', '正在下载生成所需文件'],
    sources_download: ['Downloading model files', '下载模型文件'],
    reference_download: ['Downloading model files', '下载模型文件'],
};
const size = (bytes, unit) => (bytes / (unit === 'GiB' ? 2**30 : 2**20)).toFixed(1);
// Asked for two passes with a first pass other than 8 steps: the engine ran one pass.
const ranOnePass = plan => plan.requested === true && plan.enabled === false && plan.preview !== true
    && Number.isInteger(plan.base_steps) && plan.base_steps > 0 && plan.base_steps !== 8;
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
    const steps = (key, name, step, total) => {
        const [en, zh, enSteps, zhSteps] = SAMPLING_NAME[name];
        const pass = SAMPLING_PASS[name];
        return {key, text: Number.isInteger(step) && Number.isInteger(total) && total > 0
            ? t(`${enSteps} ${step} of ${total}${pass ? ` (${pass[0]})` : ''}`, `${zhSteps}${step} / ${total} 步${pass ? `（${pass[1]}）` : ''}`)
            : t(en, zh)};
    };
    const base = Number.isInteger(plan.base_steps) && plan.base_steps > 0 ? plan.base_steps : null;
    const second = plan.second || plan.upscale_target;
    if (message.prediction || message.sampling_plan) {
        if (!ranOnePass(plan)) return named('prepare');
        const n = plan.base_steps;
        return {key: 'prepare', text: t(`Two-pass acceleration needs 8 first-pass steps. This video uses a single ${n}-step pass.`,
            `只有一采为 8 步时才会使用二次采样加速，这次按 ${n} 步只采样一遍。`)};
    }
    if (phase === 'recovery') return message.retry?.reuse_sampling === true ? named('decode')
        : message.retry?.reuse_first_pass === true ? steps('pass2', 'pass2') : named('prepare');
    if (phase === 'sample_finalize') return named('decode');
    if (message.stage === 'latent_upscale' || phase === 'latent_upscale')
        return {key: 'upscale', text: Number.isInteger(second?.width) && Number.isInteger(second?.height)
            ? t(`Second pass · upscaling to ${second.width} × ${second.height}`,
                `二采 · 放大到 ${second.width} × ${second.height}`)
            : t('Second pass · upscaling', '二采 · 放大')};
    if (phase === 'sampling' || phase === 'refinement' || message.stage === 'preview_mismatch') {
        const done = Number.isInteger(message.done) ? message.done : null;
        const total = Number.isInteger(message.total) && message.total > 0 ? message.total : null;
        const twoPass = plan.enabled === true && base !== null;
        if ((secondPass || phase === 'refinement') && twoPass) {
            const refine = total !== null && total > base ? total - base : plan.refine_steps;
            const step = done === null ? null : Math.min(Math.max(1, done - base + 1), refine);
            return steps('pass2', 'pass2', step, refine);
        }
        const count = twoPass ? base : total;
        const step = done === null || count === null ? null : Math.min(done + 1, count);
        if (twoPass && plan.preview) return steps('preview', 'preview', step, count);
        const sampling = steps('pass1', twoPass ? 'first' : 'single', step, count);
        if (!twoPass && ranOnePass(plan))
            sampling.text += t(' (single pass; two-pass acceleration needs 8 first-pass steps)', '（只采样一遍；二次采样加速需一采 8 步）');
        return sampling;
    }
    const key = labelActivities.get(message.label) || {
        starting: 'prepare', prepare: 'prepare', prediction: 'prepare', describe: 'describe', encoding: 'combined',
        load: 'load', decode: 'decode', save: 'save',
    }[phase] || labelActivities.get(message.stage);
    if (!key) return null;
    if (key === 'refine') return {key: 'upscale', text: t('Second pass · upscaling', '二采 · 放大')};
    // Named encoder and post-sampling events stay distinct, even before a pause.
    const substage = SUBACTIVITY_LABELS[message.label] || (key === 'reference' ? 'reference' : null);
    if (substage) return {key: ['prompt', 'combined', 'reference'].includes(key) ? 'encode' : key,
        text: t(...SUBACTIVITY[substage])};
    // Older or unnamed encoding events keep the safe generic activity.
    if (key === 'prompt' || key === 'combined' || key === 'reference')
        return {key: 'encode', text: t(...ACTIVITY[references ? 'combined' : 'prompt'])};
    // LoRA preparation counts LoRA files, then the model counts its own blocks:
    // the same sentence, but its own small bar, so a full LoRA count is not carried on.
    if (key === 'load' && message.label === 'Loading LoRAs') return {key: 'lora', text: t(...ACTIVITY.load)};
    return {key, text: t(...ACTIVITY[key])};
}

function retryText(retry, t) {
    const reason = {gpu_oom: ['Out of GPU memory', '显存不足'], ram_pressure: ['Out of RAM', '内存不足'],
        unified_memory: ['Not enough memory', '内存不够']}[retry.kind]
        || ['Previous attempt did not finish', '上一次尝试没有完成'];
    if (retry.reuse_sampling === true) return t(`${reason[0]}; the generated result is kept, and a retry is in progress.`,
        `${reason[1]}，已保留生成结果，正在重试。`);
    return t(`${reason[0]}; adjusting settings and retrying.`, `${reason[1]}，正在调整设置后重试。`);
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
    const detail = [heat !== null ? `${heat}\u00a0°C` : '', share !== null ? t(`${share}% of its peak clock`, `频率只有峰值的 ${share}%`) : ''].filter(Boolean).join(t(', ', '，'));
    return t(`The GPU spent most of this video in thermal slowdown${detail ? ` (${detail})` : ''}, so it took much longer than usual. Improve cooling; on a laptop, plug in and choose a high-performance power mode.`,
        `生成这段视频时，显卡大部分时间因过热降频${detail ? `（${detail}）` : ''}，所以比平时慢很多。请改善散热；笔记本请接通电源并选择高性能模式。`);
}

// Other programs held a substantial share of the GPU during this run (summary.shared_gpu).
// The share is rounded to 5% and written as at most 95%; without a usable number there is no note.
export function sharedGpuText(shared, t) {
    const mean = shared?.other_mean_percent;
    if (typeof mean !== 'number' || !Number.isFinite(mean)) return '';
    const percent = Math.min(95, Math.round(mean / 5) * 5);
    return t(`Other programs were using about ${percent}% of the GPU.`,
        `其他程序占用了约 ${percent}% 的显卡。`);
}

// Replace person: the source video's sound was asked for and could not be put into the video.
export function keepSoundText(t) {
    return t('The source video sound could not be kept. The video uses generated sound.', '未能保留源视频声音，成片使用生成的声音。');
}

export function encoderFilePath(file) {
    return file?.offline_installation === true && typeof file.path === 'string' ? file.path : '';
}

export function encoderFileText(file, t) {
    let message = t('The prompt model file differs from the tested one, so results and speed may differ.',
        '提示词模型文件和测试过的版本不同，效果和速度可能不一样。');
    if (file.offline_installation === true) {
        const target = encoderFilePath(file) ? t('the file below', '下面这个文件') : t('this file', '这个文件');
        message += t(
            ` This offline-package installation cannot switch back in one step yet. Close FreeVideo, move or rename ${target}, then import the Prompt model package (FreeVideo-文本编码器.zip) again on the Installation page of the launcher.`,
            `离线包安装暂时不能一键换回。请先关闭 FreeVideo，把${target}移走或改名，再在启动器的“安装”页重新导入“提示词模型（FreeVideo-文本编码器.zip）”包。`);
    } else if (file.launcher === true) message += t(
        ' Select "Repair" in the launcher under Settings > Environment to restore the tested file; the current file is kept.',
        '可在启动器“设置 → 环境”中点“修复”恢复已验证的文件，原文件会保留。');
    else message += t(
        ' Select "Install / repair" in the page settings to restore the tested file; the current file is kept.',
        '可在页面设置中点“安装／修复”恢复已验证的文件，原文件会保留。');
    return message;
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

// The time left on show `dt` seconds on, given the engine's newest estimate (null when it sent none). Once measured, an
// estimate at or above the shown time holds it, so a slower run does not count down to "less than a minute"
// early; one that has stayed well above it for a minute (`above` seconds so far) raises it gently, or a run
// measured much slower would leave a low number standing for minutes. A lower one is eased into. An initial
// estimate (made before this run was measured) is followed both ways: a slow machine must not keep a low number.
export function remainingShown(shown, target, dt, initial = false, above = 0) {
    if (target === null) return Math.max(0, shown - dt);
    if (initial) return shown + (target - shown) * (1 - Math.exp(-dt / 15));
    if (target < shown - dt) {
        const counted = Math.max(0, shown - dt);
        return counted + (target - counted) * (1 - Math.exp(-dt / 15));
    }
    if (target < shown) return target;
    return above >= 60 ? Math.min(target, shown + Math.max(1, shown * .003) * dt) : shown;
}
// The seconds the engine's estimate has stayed well above the shown time (a quarter more), `dt` seconds on.
export const remainingAbove = (above, shown, target, dt) =>
    target !== null && target > Math.max(shown * 1.25, shown + 30) ? above + dt : 0;
// The whole-video fraction the engine's last message stands for now: its estimate moved on by the seconds since the
// step sample (stateAge) and the whole-video sample (overallAge) arrived. Null when the message carries none.
export function estimatedFraction(state, overall, stateAge, overallAge) {
    const fraction = value => Math.min(1, Math.max(0, value));
    const counted = Number.isInteger(state.total) && state.total > 0 && Number.isInteger(state.done)
        && state.done >= 0 && state.done <= state.total;
    let candidate = valid(overall.fraction) ? fraction(overall.fraction) : null;
    const canEstimate = overall.status !== 'failed' && overall.estimated === true
        && valid(overall.phase_start_fraction) && valid(overall.phase_weight);
    if (canEstimate) {
        let local = null;
        if (state.phase === 'sampling' && counted && !overall.sampling_time_weighted) {
            local = valid(state.display_fraction) ? fraction(state.display_fraction) : state.done / state.total;
            if (state.done < state.total && valid(state.estimated_step_seconds) && state.estimated_step_seconds > 0) {
                const stepElapsed = (valid(state.step_elapsed_seconds) ? state.step_elapsed_seconds : 0)
                    + Math.max(0, stateAge);
                local = Math.max(local, (state.done + Math.min(.88, .88 * stepElapsed / state.estimated_step_seconds)) / state.total);
            } else if (valid(overall.phase_seconds) && overall.phase_seconds > 0
                && valid(overall.phase_elapsed_seconds)) {
                // Before the first warm NFE supplies a per-step duration,
                // use the report-weighted sampling estimate as a smooth
                // visual guide. The synchronized NFE counter remains the
                // source of truth and this estimate stops short of 100%.
                const phaseElapsed = overall.phase_elapsed_seconds
                    + Math.max(0, overallAge);
                local = Math.max(local, Math.min(.95, phaseElapsed / overall.phase_seconds));
            }
        } else if (valid(overall.phase_seconds) && overall.phase_seconds > 0 && valid(overall.phase_elapsed_seconds)) {
            const phaseElapsed = overall.phase_elapsed_seconds + Math.max(0, overallAge);
            // Time can move the estimate, but only an engine transition
            // finishes a phase. Long phases stop short of their boundary.
            local = Math.min(.95, phaseElapsed / overall.phase_seconds);
        }
        if (local !== null) candidate = Math.max(candidate ?? 0,
            canEstimate ? overall.phase_start_fraction + overall.phase_weight * local : local);
    }
    return candidate;
}

// The video's report download, also used by Agent failure cards. A task that
// failed before creating an engine report supplies the shared redacted summary.
export function createDiagnosticReportControl(t, {api = null, fallback = null, tip = false, version = null,
    label = () => t('Download report', '下载报告')} = {}) {
    if (tip) {
        const href = new URL('./result_actions.css', import.meta.url).href;
        if (![...document.head.querySelectorAll('link')].some(link => link.href === href)) {
            const style = document.createElement('link'); style.rel = 'stylesheet'; style.href = href; document.head.append(style);
        }
    }
    const report = el('button', 'fv-report-download fv-quiet'); report.type = 'button'; report.hidden = true;
    let problem;
    // A finished video's report is fetched once and kept: the file saved and the one sent are the same.
    let reportId = null, revision = 0, pinned = false, pinnedBlob = null;
    report.textContent = label();
    function updateReport(message) {
        if (message.new_request === true || message.overall?.reset === true || message.reset === true && !message.phase) {
            problem?.remove();
            reportId = null; revision++; report.hidden = true; pinned = false; pinnedBlob = null;
        }
        if (typeof message.report_id === 'string' && /^[a-f0-9]{32}$/.test(message.report_id)) {
            if (reportId !== message.report_id) { problem?.remove(); revision++; pinned = false; pinnedBlob = null; report.disabled = false; report.textContent = label(); }
            reportId = message.report_id;
        }
        report.hidden = !reportId && !fallback;
        return reportId;
    }
    async function fetchReport() {
        if (reportId) {
            if (pinned && pinnedBlob) return pinnedBlob;
            const id = reportId, path = `/freevideo/report/${id}`;
            const response = await (api ? api.fetchApi(path) : fetch(path));
            if (!response.ok) throw new Error('Report unavailable');
            const blob = await response.blob();
            if (pinned && reportId === id) pinnedBlob = blob;
            return blob;
        }
        if (fallback) return fallback();
        throw new Error('Report unavailable');
    }
    async function downloadReport() { await saveReport(await fetchReport()); }
    report.onclick = async () => {
        if (report.disabled || report.hidden) return;
        if (tip && report.isConnected && focusReportTip(report.parentElement)) return;
        problem?.remove();
        const current = revision;
        report.disabled = true; report.textContent = t('Preparing report…', '正在整理报告…');
        try {
            await downloadReport();
            if (current === revision) {
                report.textContent = label();
                if (tip && report.isConnected) showReportTip(report.parentElement, report, t, {mode: 'downloaded',
                    submit: () => reportIssue(fetchReport, typeof version === 'function' ? version() : version, {save: false})});
            }
        } catch {
            if (current === revision) {
                report.textContent = t('Report unavailable · retry', '报告暂不可用 · 重试');
                problem = issueButton(t, () => ({where: ['Diagnostic report', '诊断报告'], message: report.textContent}));
                report.after(problem);
            }
        } finally { if (current === revision) report.disabled = false; }
    };
    return {report, updateReport, fetchReport, downloadReport, hasReport: () => reportId !== null, pin: () => { pinned = true; }};
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
    // The sampler's newest coarse frames; decorative, so it carries no text.
    const frames = el('img', 'fv-generation-frames'); frames.alt = ''; frames.hidden = true;
    frames.decoding = 'async'; frames.setAttribute('aria-hidden', 'true'); frames.draggable = false;
    const times = el('div', 'fv-generation-times');
    const elapsed = el('span', ''), eta = el('span', 'fv-generation-eta'); times.append(elapsed, eta);
    const late = el('div', 'fv-generation-late'); late.hidden = true;
    const status = el('div', 'fv-generation-status'); status.hidden = true;
    const compatibilityNote = el('div', 'fv-generation-compatibility'); compatibilityNote.hidden = true;
    const live = el('div', 'fv-generation-live');
    live.setAttribute('aria-live', 'polite'); live.setAttribute('aria-atomic', 'true');
    const stall = el('p', 'fv-generation-note fv-generation-stall'); stall.hidden = true;
    stall.setAttribute('role', 'status');
    const stallText = el('span', ''); stall.append(stallText);
    const trims = new Map();
    element.append(heading, track, label, mini, frames, times, late, compatibilityNote, status, live);
    let recovery = '', lowMemory = false, compatibility = null, samplingPlan = {}, secondPass = false, references = false, splitFailed = null;
    let hasHeartbeat = false;
    // Mac: the unified-memory notice of this request, and whether its first pass has ended.
    let memoryTight = null, firstPassOver = false;
    let state = {}, overall = {}, started = 0, sampledAt = 0, overallAt = 0, timer = null, elapsedShown = 0;
    // Where the bridge's remaining time comes from: 'similar' (no record of these
    // settings here yet), 'local' (this computer's record) or 'measured' (this run).
    // Absent from older bridges, which keep the two-steps rule.
    let estimateSource = null, etaChangedAt = -Infinity;
    // The page shows its own "preparing" row at the click; the engine's first
    // message for that request follows and must not restart the elapsed time.
    let placeholder = false, elapsedLead = 0;
    let lastProgressAt = 0, previousProgress = null, segmentStartedAt = 0, segmentKey = '';
    let pauseAnnounced = false, lastExplanation = '', announcedMessages = new Set();
    let announcementRecord = null;
    let remainingSeconds = null;
    let detail = null, localShare = null, decodeMinutes = null, decodeMinutesAt = -Infinity;
    const workShares = new Map();
    let completedSteps = 0, previousRemaining = null, remainingAt = 0, lastActivity = null;
    // Once the shown time reaches zero before the video is saved, it stays hidden for this request.
    // The last minute runs on real time even while an estimate holds the number.
    let remainingRetired = false, lastMinuteAt = null, lastMinute = 0, aboveSeconds = 0;
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
    const diagnostic = createDiagnosticReportControl(t, {api});
    const {report, updateReport, fetchReport, downloadReport} = diagnostic;
    // The newest frame picture. Any failure leaves the page as it was without one.
    // `framesToken` changes whenever a pending fetch stops being wanted, so a late answer is dropped.
    let framesKey = '', framesUrl = null, framesLoading = null, framesToken = 0;
    function clearFrames() {
        framesKey = ''; framesLoading = null; framesToken++; frames.hidden = true;
        if (framesUrl) { URL.revokeObjectURL(framesUrl); framesUrl = null; }
        frames.removeAttribute('src');
    }
    // In a wide column each frame is 64 px high; a narrow one keeps the picture's own size.
    function fitFrames() {
        const width = (element.parentElement || element).clientWidth;
        frames.dataset.wide = width > 480 ? 'true' : 'false';
    }
    function updateFrames(message, id) {
        const value = message.frame_preview;
        fitFrames();
        const usable = value && typeof value === 'object' && Number.isInteger(value.serial) && value.serial > 0
            && Number.isInteger(value.frames) && value.frames > 0 && value.frames <= 8;
        if (!usable || !id || ['complete', 'failed', 'cancelled'].includes(message.phase)) { if (framesKey || framesLoading) clearFrames(); return; }
        const key = `${id}:${value.serial}`;
        if (key === framesKey || key === framesLoading) return;
        framesLoading = key;
        const token = ++framesToken;
        const path = `/freevideo/frame-preview/${id}`;
        (async () => {
            try {
                const response = await (api ? api.fetchApi(path) : fetch(path));
                if (!response.ok) throw new Error('No picture');
                const blob = await response.blob();
                if (framesToken !== token) return;
                const url = URL.createObjectURL(blob);
                const old = framesUrl;
                frames.onload = () => { fitFrames(); if (framesUrl === url) frames.hidden = false; try { onLayout?.(); } catch {} };
                frames.onerror = () => { if (framesUrl === url) clearFrames(); };
                framesUrl = url; framesKey = key; frames.src = url;
                if (old) URL.revokeObjectURL(old);
            } catch {
                if (framesToken === token) clearFrames();
            }
        })();
    }
    const hasCount = value => Number.isInteger(value.total) && value.total > 0 && Number.isInteger(value.done)
        && value.done >= 0 && value.done <= value.total;

    function drawOverall() {
        // Stage counters cannot substitute for a whole-video estimate.
        if (!Object.keys(overall).length) {
            stopCount(); countShown = null;
            heading.hidden = true;
            element.dataset.counted = 'false'; element.dataset.complete = 'false'; count.textContent = ''; fill.style.width = '';
            track.setAttribute('aria-label', t('Generation progress', '生成进度'));
            track.removeAttribute('aria-valuenow'); track.removeAttribute('aria-valuemax'); track.removeAttribute('aria-valuetext');
            return;
        }
        const complete = overall.status === 'complete';
        const candidate = estimatedFraction(state, overall, (now() - sampledAt) / 1000, (now() - overallAt) / 1000);
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
        track.setAttribute('aria-label', t('Generation progress', '生成进度'));
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
    let stallHasReport = null;
    const segmentTypical = () => detail?.sampling && valid(state.estimated_step_seconds)
        ? state.estimated_step_seconds : overall.typical_seconds;
    function drawStall(age, ended) {
        const floor = STALL_SECONDS_BY_PHASE[state.phase || state.timing_phase] ?? STALL_SECONDS_BY_PHASE.other;
        const typical = segmentTypical();
        const threshold = Math.max(floor, valid(typical) ? 2 * typical : 0);
        const shown = !ended && age >= threshold;
        const wasShown = !stall.hidden;
        const hasReport = diagnostic.hasReport();
        // The separate notice never adds another clock or blocks the run.
        if (shown && (stall.hidden || hasReport !== stallHasReport)) {
            const first = t('Progress has not updated recently; generation is still running.',
                    '进度暂未更新，仍在生成中。');
            const sentence = hasReport ? first + t(
                ' You can also use Download report below and submit it in a GitHub issue; we will look into it.',
                '也可以点下方“下载报告”提交到 GitHub issue，我们会专门排查。') : first;
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
        const ratio = value => Number.isFinite(value?.[0]) && Number.isFinite(value?.[1]) && value[1] > 0
            ? Math.max(0, Math.min(1, value[0] / value[1])) : null;
        if (key === 'download' || key === 'load' || key === 'lora' || key === 'decode' || key === 'encode') {
            if (localShare !== null && hasCount(state) && ['load', 'decode'].includes(detail?.key))
                return {mode: 'determinate', fraction: localShare};
            const share = ratio([state.done, state.total]);
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
            labelId = stageId; labelText = lastActivity ? lastActivity.text : labelText; away = null;
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
        const paused = !ended && age > 15;
        const base = ended ? (overall.status === 'complete' || state.phase === 'complete' ? savedText() : t('Stopped', '已停止'))
            : paused ? detail?.text || stageText() : stageText();
        if (label.textContent !== base) label.textContent = base;
        drawMini(age, ended);
        const automatic = compatibility?.origin === 'automatic' && compatibility.level > 0;
        const compatibilityText = !ended && automatic ? compatibility.recovery_disabled
            ? t('Generation is slowed automatically for stability; you can change this in Settings.', '为了稳定，生成已自动放慢；可在设置里调整。')
            : t('Generation has slowed automatically and will gradually speed up as it stabilizes.',
                '生成已自动放慢，稳定后会逐步加快。') : '';
        // All explanations share one row, including automatic compatibility.
        compatibilityNote.hidden = true;
        let pauseText = '';
        if (paused) {
            const elapsed = waitDuration((now() - segmentStartedAt) / 1000, t);
            const typical = segmentTypical(), usual = valid(typical) && typical > 0 ? waitDuration(typical, t) : '';
            const similar = overall.typical_source === 'similar';
            pauseText = t(`${elapsed} so far`, `已进行 ${elapsed}`);
            if (usual) pauseText += similar ? t(` (about ${usual} on similar hardware)`, `（相近配置约 ${usual}）`)
                : t(` (usually about ${usual})`, `（通常约 ${usual}）`);
        }
        // Only an available engine estimate can supply this long-decode fallback.
        const remainingEstimate = rawRemaining();
        if (!ended && detail?.key === 'decode' && valid(remainingEstimate) && remainingEstimate >= 180) {
            const minutes = Math.ceil(remainingEstimate / 60);
            if (decodeMinutes === null || now() - decodeMinutesAt >= 60000 && Math.abs(minutes - decodeMinutes) >= 1) {
                decodeMinutes = minutes; decodeMinutesAt = now();
            }
        } else { decodeMinutes = null; decodeMinutesAt = -Infinity; }
        const decodeEta = decodeMinutes === null ? '' : t(`About ${decodeMinutes} min left`, `还需约 ${decodeMinutes} 分钟`);
        const progressText = [paused ? pauseText : '', decodeEta].filter(Boolean).join(t(' · ', '，'));
        const level = ended ? '' : recovery ? 'retry' : paused ? 'pause'
            : lowMemory ? 'memory' : memoryTight ? 'unified-memory' : automatic ? 'automatic'
                : compatibility && compatibility.level > 0 ? 'compatibility' : trims.size ? 'notice'
                    : splitFailed && !progressText ? 'split' : '';
        const explanation = {retry: recovery,
            pause: progressText,
            memory: t('This video\'s size and length do not fit in GPU memory, so some data is kept in RAM. It will take longer than usual, but it will finish.',
                '可用显存放不下这次的尺寸和时长，部分数据改放在内存中，生成会比平时慢，但能完成。'),
            // Mac, level 3 with low-memory mode (never both). The measured numbers go to the
            // report only. Closing applications during the first pass can still give the
            // second pass room, so that advice shows only until then.
            'unified-memory': memoryTight?.phase === 'refinement' && !firstPassOver
                ? t('Memory is low; closing memory-heavy applications will speed this up.',
                    '可用内存较少，关闭占用内存较多的程序可以更快。')
                : t('Less memory is available, so this generation will take longer.',
                    '可用内存较少，这次生成会较慢。'),
            compatibility: compatibility ? t('Compatibility settings are enabled, so generation will take longer.',
                '已开启兼容性设置，生成会较慢。') : '',
            automatic: compatibilityText,
            notice: trimStatus([...trims.values()], t),
            // A long video whose part descriptions could not be written: the server's own words stay in the report.
            split: splitFailed === 'model_missing' ? t('Prompt model not installed. The description is split across the parts by sentence.', '未安装提示词模型，描述按句子顺序分到各段')
                : t('The description is split across the parts by sentence.', '描述按句子顺序分到各段')}[level] || (!ended ? progressText : '');
        if (status.textContent !== explanation) status.textContent = explanation;
        status.hidden = !explanation; status.dataset.level = level;
        const announcements = [];
        const pauseKey = `pause:${segmentKey}`;
        if (paused && !pauseAnnounced) {
            if (!announcedMessages.has(pauseKey)) announcements.push(base);
            announcedMessages.add(pauseKey); pauseAnnounced = true;
        }
        // Seconds and local percentages are visual only. Announce the work once.
        const spoken = level === 'pause' || explanation === progressText ? '' : explanation;
        for (const text of [spoken !== lastExplanation ? spoken : '']) {
            if (!text || announcedMessages.has(text)) continue;
            announcements.push(text); announcedMessages.add(text);
        }
        if (announcementRecord && announcements.length) {
            announcementRecord.messages = [...announcedMessages];
            try { sessionStorage.setItem(announcementKey, JSON.stringify(Object.fromEntries(requestAnnouncements))); }
            catch { /* Optional storage; in-memory deduplication remains. */ }
        }
        lastExplanation = spoken;
        if (announcements.length) live.textContent = announcements.join(t(' ', ''));
    }

    // Preserve the server's heartbeat/floor policy for the raw target only.
    // The displayed estimate has its own clock; once it is no longer initial it never increases.
    function rawRemaining() {
        if (!valid(overall.remaining_seconds)) return null;
        return hasHeartbeat ? overall.remaining_seconds : Math.max(
            valid(overall.remaining_floor_seconds) ? overall.remaining_floor_seconds : 0,
            overall.remaining_seconds - Math.max(0, (now() - overallAt) / 1000));
    }
    function observeRemaining(message) {
        if (!message.overall) return;
        const source = typeof message.overall.remaining_source === 'string' ? message.overall.remaining_source : null;
        const raw = valid(message.overall.remaining_seconds) ? rawRemaining() : null;
        if (source) {
            // The bridge estimates the whole request from the first message:
            // show it at once. Leaving the initial (similar-settings) estimate replaces
            // the number once, up or down; after that it only falls.
            const leaving = estimateSource === 'similar' && source !== 'similar';
            estimateSource = source;
            // The correction starts the last minute over. Once the shown time has run
            // out it stays hidden for this attempt, as without an initial estimate.
            if (leaving && raw !== null && !remainingRetired) { lastMinuteAt = null; lastMinute = 0; }
            if (raw === null || remainingRetired) return;
            // A retry's fresh estimate still waits for the 5 s spacing of the shown
            // minutes; only a new request starts the line over (in update()).
            if (remainingSeconds === null || leaving) { remainingSeconds = raw; remainingAt = now(); }
            return;
        }
        if (completedSteps < 2 || remainingSeconds !== null || remainingRetired) return;
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
        remainingRetired = false; lastMinuteAt = null; lastMinute = 0; aboveSeconds = 0;
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
            valid(overall.elapsed_seconds) ? overall.elapsed_seconds + overallAge + elapsedLead : (now()-started)/1000);
        elapsed.textContent = `${t('Elapsed', '已用时')} ${duration(Math.max(0, seconds))}`;
        if (ended) remainingSeconds = null;
        if (remainingSeconds !== null) {
            const sent = rawRemaining(), passed = Math.max(0, (now() - remainingAt) / 1000);
            aboveSeconds = remainingAbove(aboveSeconds, remainingSeconds, sent, passed);
            remainingSeconds = remainingShown(remainingSeconds, sent, passed, estimateSource === 'similar', aboveSeconds);
            if (remainingSeconds < 60 && lastMinuteAt === null) { lastMinuteAt = now(); lastMinute = remainingSeconds; }
            // An initial estimate may rise again: its last minute starts over.
            else if (remainingSeconds >= 60 && estimateSource === 'similar') lastMinuteAt = null;
            // A run slower than estimated would leave "less than a minute" up for minutes:
            // once that minute has passed, or the time reaches zero, stop showing it.
            if (!ended && (remainingSeconds <= 0
                || lastMinuteAt !== null && (now() - lastMinuteAt) / 1000 >= lastMinute)) {
                remainingSeconds = null; remainingRetired = true;
            }
        }
        remainingAt = now();
        // The editor's bar reads this number, so both places always show the same time.
        eta.hidden = remainingSeconds === null;
        // Past the shown time the line says so once, instead of a number that only shrinks.
        late.hidden = !(remainingSeconds === null && remainingRetired && !ended);
        late.textContent = late.hidden ? '' : t('Taking longer than expected', '比预计久，仍在生成');
        const etaText = remainingSeconds === null ? '' : remainingSeconds >= 60
            ? t(`About ${Math.ceil(remainingSeconds / 60)}\u00a0min left`, `预计还需约 ${Math.ceil(remainingSeconds / 60)}\u00a0分钟`)
            : t('Less than a minute left', '预计还需不到 1\u00a0分钟');
        const initial = remainingSeconds !== null && estimateSource === 'similar';
        // While it follows the bridge, the shown minutes change at most every 5 s.
        if (eta.textContent !== etaText && (!initial || !eta.textContent || now() - etaChangedAt >= 5000)) {
            eta.textContent = etaText; etaChangedAt = now();
        }
        // The bar takes the number behind the words on screen: while the shown minutes wait out their
        // spacing, the seconds wait with them.
        if (remainingSeconds === null) delete eta.dataset.seconds;
        else if (eta.textContent === etaText) eta.dataset.seconds = String(Math.ceil(remainingSeconds));
        elapsed.hidden = false; times.hidden = elapsed.hidden && eta.hidden;
        drawStall(age, ended);
        drawActivity(age, ended);
        drawOverall();
    }
    // A finished first-pass preview is not the finished video.
    const savedText = () => state.label === 'Preview saved' ? t('Preview saved', '预览已保存') : t('Video saved', '视频已保存');
    function update(message) {
        updateFrames(message, updateReport(message));
        // Older servers may still forward this diagnostic-only event. It is
        // not a stage change or something the user needs to act on.
        if (message.warning === 'ram_budget_warning' && !message.phase) return;
        const reset = newRequest(message);
        const placeholderReset = element.hidden || reset;
        const terminal = ['complete', 'failed', 'cancelled'].includes(message.phase);
        if (placeholderReset) {
            // The engine's first message after the page's own placeholder is the
            // same click: keep counting from it. The placeholder lasts from this
            // node's own start until any engine message, its end, or hide().
            const sameClick = placeholder && message.stream_id !== undefined;
            // From then on the engine's elapsed time is offset by the wait before it began.
            const firstAt = valid(message.received_at) ? message.received_at : now();
            elapsedLead = sameClick ? Math.max(0, (firstAt - started) / 1000
                - (valid(message.overall?.elapsed_seconds) ? message.overall.elapsed_seconds : 0)) : 0;
            if (!sameClick) { started = now(); elapsedShown = 0; }
            placeholder = message.stream_id === undefined && !terminal;
            state = {}; overall = {}; overallAt = now(); shownFraction = null; estimateSource = null;
            eta.textContent = ''; etaChangedAt = -Infinity;
            lastProgressAt = valid(message.received_at) ? message.received_at : now();
            previousProgress = null; segmentKey = ''; segmentStartedAt = lastProgressAt;
            pauseAnnounced = false; lastExplanation = ''; announcedMessages.clear();
            live.textContent = ''; status.textContent = ''; resetRemaining(); lastActivity = null; resetMini();
            stopCount(); countShown = null;
            recovery = ''; lowMemory = false; compatibility = null; references = false; trims.clear(); splitFailed = null;
            memoryTight = null; firstPassOver = false;
            samplingPlan = {}; secondPass = false; hasHeartbeat = false;
            detail = null; localShare = null; workShares.clear(); decodeMinutes = null; decodeMinutesAt = -Infinity;
            announcementRecord = null;
        }
        if (!placeholderReset && (message.stream_id !== undefined || terminal)) placeholder = false;
        const record = announcementsFor(message, reset);
        if (record) {
            announcementRecord = record;
            for (const text of record.messages) announcedMessages.add(text);
        }
        const receivedAt = valid(message.received_at) ? message.received_at : now();
        // The estimate in `overall` can be newer than the engine message it rides
        // on: a heartbeat refreshes only that (overall_received_at, from publish).
        const overallReceivedAt = valid(message.overall_received_at) ? message.overall_received_at : receivedAt;
        const beat = message.overall?.heartbeat === true;
        // A heartbeat repeats the last engine message. A page that has not seen
        // that message yet (reopened, or opened mid-run) takes it as the message.
        const heartbeat = beat && previousProgress !== null;
        if (message.reset === true) {
            hasHeartbeat = false; lastActivity = null;
            workShares.clear(); localShare = null; decodeMinutes = null; decodeMinutesAt = -Infinity;
            // A stage restarting its own count (the sampler's start) leaves the
            // bridge's whole-request estimate standing; a retry starts afresh.
            if (estimateSource && !message.retry && message.phase !== 'recovery') { completedSteps = 0; previousRemaining = null; }
            else resetRemaining();
            // A reset that would move a shown share back starts its stage afresh
            // (hidden, 5 s again). One before anything was counted, such as the
            // sampler's own start, keeps the stage's clock and the 2 s rule.
            if (miniFraction > 0) resetMini();
        }
        if (!heartbeat && message.retry && message.phase === 'recovery') {
            // Resource retries do not carry reset=true. Clear only the new attempt's work.
            const retryId = JSON.stringify(message.retry);
            if (retryId !== JSON.stringify(state.retry)) {
                workShares.clear(); localShare = null; resetMini();
                decodeMinutes = null; decodeMinutesAt = -Infinity;
            }
        }
        if (beat) hasHeartbeat = true;
        if (!heartbeat && message.phase === 'sampling' && valid(message.done))
            completedSteps = Math.max(completedSteps, message.done);
        if (message.low_memory && typeof message.low_memory === 'object') lowMemory = true;
        if (message.compatibility && typeof message.compatibility === 'object') compatibility = message.compatibility;
        if (message.chain && typeof message.chain === 'object') splitFailed = typeof message.chain.split_failed === 'string' ? message.chain.split_failed : null;
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
        if (heartbeat) {
            overall = {...overall, ...message.overall}; overallAt = overallReceivedAt;
            observeRemaining(message);
            tick(); return;
        }
        // Settings-only events do not replace the stage or refresh its clocks.
        const activityEvent = progressFields.some(key => key in message) || 'timing_phase' in message
            || message.prediction || message.sampling_plan || reset;
        if (!activityEvent && previousProgress !== null) {
            if (message.overall && typeof message.overall === 'object') {
                overall = {...message.overall}; overallAt = overallReceivedAt; observeRemaining(message);
            }
            tick(); return;
        }
        // The upscale before the second pass has no counters; its seconds in
        // progress are the engine's proof of life.
        const counted = message.stage === 'latent_upscale' ? [...progressFields, 'step_elapsed_seconds'] : progressFields;
        if (!heartbeat && (previousProgress === null || counted.some(key => message[key] !== previousProgress[key])))
            lastProgressAt = previousProgress === null ? receivedAt : Math.max(lastProgressAt, receivedAt);
        if (!heartbeat) previousProgress = Object.fromEntries(counted.map(key => [key, message[key]]));
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
        detail = subactivity(state, lastActivity, t);
        localShare = null;
        if (['load', 'decode'].includes(detail.key) && lastActivity?.key !== 'lora') {
            const shareKey = `${detail.key}:${secondPass ? 'second' : 'first'}`;
            const share = hasCount(state) ? state.done / state.total
                : state.label === 'Video model ready' ? 1 : null;
            if (share !== null) workShares.set(shareKey, Math.max(workShares.get(shareKey) ?? 0, share));
            localShare = workShares.get(shareKey) ?? null;
        }
        const key = JSON.stringify([detail.key, detail.sampling ? [lastActivity?.key, state.done] : null]);
        if (key !== segmentKey || message.reset) {
            segmentKey = key;
            segmentStartedAt = receivedAt - (detail.sampling && valid(message.step_elapsed_seconds)
                ? message.step_elapsed_seconds * 1000 : 0);
            pauseAnnounced = false;
            if (detail.sampling && !('estimated_step_seconds' in message)) delete state.estimated_step_seconds;
        } else if (detail.sampling && valid(message.step_elapsed_seconds)) {
            segmentStartedAt = Math.min(segmentStartedAt, receivedAt - message.step_elapsed_seconds * 1000);
        }
        element.dataset.phase = message.phase || message.timing_phase || '';
        if (message.overall && typeof message.overall === 'object') {
            overall = {...message.overall}; overallAt = overallReceivedAt;
            observeRemaining(message);
        }
        element.hidden = false;
        tick(); if (timer === null) timer = setInterval(tick, 250);
    }
    function hide() {
        clearFrames();
        element.hidden = true; status.hidden = true; recovery = ''; placeholder = false; elapsedLead = 0; trims.clear(); lowMemory = false; compatibility = null; references = false; hasHeartbeat = false; splitFailed = null;
        compatibilityNote.hidden = true; compatibilityNote.textContent = '';
        memoryTight = null; firstPassOver = false;
        elapsedShown = 0;
        if (timer !== null) clearInterval(timer); timer = null; state = {}; overall = {}; shownFraction = null;
        stopCount(); countShown = null;
        resetRemaining(); lastActivity = null; resetMini(); previousProgress = null; segmentKey = ''; status.textContent = ''; live.textContent = '';
        detail = null; localShare = null; workShares.clear(); decodeMinutes = null; decodeMinutesAt = -Infinity;
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
    return {element, stall, report, updateReport, fetchReport, downloadReport, hasReport: diagnostic.hasReport, pinReport: diagnostic.pin, update, hide, dispose: hide,
        noticeCount: () => trims.size, stallShown: () => !stall.hidden, percentShown: () => !heading.hidden, snapshot};
}
