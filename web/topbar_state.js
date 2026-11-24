// The top bar's words and its status capsule's state, apart from the page (tests/topbar_state.mjs drives them).
import { ACTIVE } from './task_store.js';
import { isChinese, translator } from './i18n.js';

export const CN = isChinese();
export const t0 = translator(CN);
export const MAC = /Mac|iPhone|iPad/i.test(globalThis.navigator?.platform || globalThis.navigator?.userAgent || '');

// The words of the bar, one table (TOPBAR.md §6). Names are what the item is; uses say what it does.
export function words(t = t0, mac = MAC) {
    const mod = mac ? '⌘' : 'Ctrl', alt = mac ? '⌥' : 'Alt';
    return {
        status: [t('Run status', '运行状态'), t('See running tasks and notices', '看进行中的任务和通知')],
        search: [t('Search and commands', '搜索与命令'), t('Search works and run any action', '搜索作品、提示词并执行操作'), `${mod} K`],
        works: [t('Creations', '作品库'), t('Open your works to sort and manage them', '打开作品库，筛选和管理')],
        phone: [t('Phone', '手机'), t('Open on a phone on this network', '在同一网络的手机上打开')],
        version: [t('Version', '版本'), t('See updates and what is new', '查看更新和新功能')],
        tutorial: [t('Tutorials', '教程'), t('Learn each feature step by step', '一步步学会每个功能')],
        feedback: [t('Feedback', '反馈'), t('Tell us a problem or an idea', '把问题或想法告诉我们')],
        settings: [t('Settings', '设置'), t('Models, downloads, storage, look', '模型、下载、存储和外观')],
        menu: [t('Menu', '菜单'), t('Search, phone, version, tutorials, feedback, settings', '搜索、手机、版本、教程、反馈和设置')],
        logo: [t('FreeVideo', 'FreeVideo'), t("Back to this view's start", '回到当前视图的首屏')],
        picker: [t('Switch view', '切换视图'), t('Choose one of the five views', '在五个视图之间切换')],
        views: {
            studio: [t('Studio', '创作面板'), t('Make a video from one sentence', '写一句话，生成一段视频'), `${alt} 1`],
            image: [t('Image', '图像视图'), t('Make and edit pictures', '生成和修改图片'), `${alt} 2`],
            nodes: [t('Nodes', '节点视图'), t('Combine generation steps freely with nodes', '用节点自由组合生成步骤'), `${alt} 3`],
            editor: [t('Edit', '剪辑视图'), t('Cut clips, add sound and captions', '剪片段，配声音和字幕'), `${alt} 4`],
            autopilot: [t('Autopilot', 'AI 全自动'), t('Describe it; the assistant films it', '说想法，助手分镜并拍完'), `${alt} 5`],
        },
    };
}

// ── The status capsule (TOPBAR.md §4): what is happening now, never engineering numbers ──
// Offline > running (percent; count of running, or of unseen failures in red) > unseen failures > ready (count of
// unseen finished). The machine line (video memory, temperature) goes in the capsule's tip, never on the bar.
export const isFailure = row => row.status === 'failed' || row.status === 'interrupted';
// notices: the notification centre's {unread, failed} (palette's noticeState); without it, unseen finished jobs count.
export function capsuleState(list = [], isOnline = true, notices = null) {
    const active = list.filter(row => ACTIVE.has(row.status));
    const failed = notices ? notices.failed || 0 : list.filter(row => isFailure(row) && !row.seen).length;
    const unread = notices ? notices.unread || 0 : failed + list.filter(row => row.status === 'done' && !row.seen).length;
    if (!isOnline) return {kind: 'off', count: 0, warn: true};
    // Only waiting (nothing generates yet): "Waiting 1" with how many wait.
    if (active.length && !active.some(row => row.status === 'running' || row.status === 'cancelling')) {
        return {kind: 'wait', waiting: active.length, count: failed ? failed : 0, warn: false, countWarn: failed > 0};
    }
    if (active.length) {
        const running = active.find(row => row.status === 'running') || active[0];
        const percent = Math.max(0, Math.min(100, Math.round(100 * (Number(running.percent) || 0))));
        return {kind: 'run', percent, count: failed ? failed : active.length > 1 ? active.length : 0, warn: false, countWarn: failed > 0};
    }
    // A description being written from a picture or video (notices.prompt: {fraction}, null while not measurable): shown like a task, after real tasks.
    if (notices?.prompt) return {kind: 'prompt', percent: Number.isFinite(notices.prompt.fraction) ? Math.max(0, Math.min(100, Math.round(100 * notices.prompt.fraction))) : null, count: failed ? failed : 0, warn: false, countWarn: failed > 0};
    // A failure: a warning-coloured dot and "Not finished 1" in plain words; no extra badge.
    if (failed) return {kind: 'fail', failed, count: 0, warn: true, countWarn: false};
    // A model download or install (notices.installing: {fraction}) when nothing else runs.
    const installing = notices?.installing;
    if (installing) return {kind: 'dl', percent: Math.max(0, Math.min(100, Math.round(100 * (Number(installing.fraction) || 0)))), count: unread, warn: false};
    return {kind: 'idle', count: unread, warn: false};
}
export function capsuleWords(t, state) {
    return {off: [t('Engine offline', '引擎已断开'), ''], run: [t('Generating', '生成中'), `${state.percent}%`], dl: [t('Downloading', '下载中'), `${state.percent}%`],
        prompt: [t('Writing prompt', '生成提示词'), state.percent == null ? '' : `${state.percent}%`],
        wait: [t('Waiting', '排队'), String(state.waiting || 1)],
        fail: [t('Not finished', '未完成'), String(state.failed || 1)], idle: [t('Ready', '就绪'), '']}[state.kind];
}
export function machineLine(state, t = t0) {
    if (!state) return '';
    const gb = bytes => (bytes / 2 ** 30).toFixed(1), parts = [];
    if (state.memory_total) parts.push(t(`Video memory ${gb(state.memory_used)} / ${gb(state.memory_total)} GB`, `显存 ${gb(state.memory_used)} / ${gb(state.memory_total)} GB`));
    if (state.temperature != null) parts.push(`${state.temperature} °C`);
    return parts.join(' · ');
}

// The idle capsule's figures from the engine's monitor reading: video memory in use (whole percent) and temperature.
// Either may be missing; `hot` and `full` mark values the old status capsule showed in the warning colour.
export function machineFigures(state) {
    if (!state) return null;
    const memory = state.memory_total > 0 && Number.isFinite(state.memory_used) ? Math.max(0, Math.min(100, Math.round(100 * state.memory_used / state.memory_total))) : null;
    const temperature = Number.isFinite(state.temperature) ? Math.round(state.temperature) : null;
    if (memory == null && temperature == null) return null;
    return {memory, temperature, full: memory != null && memory >= 95, hot: temperature != null && temperature >= 85};
}

// Settings › "Show video memory and temperature in the top bar" (the old status capsule's key, freevideo.monitor.shown):
// shown only when turned on ('1'); never set means off, as the old capsule had it (the capsule says Ready).
export const FIGURES_KEY = 'freevideo.monitor.shown';
export const figuresShown = stored => stored === '1';
