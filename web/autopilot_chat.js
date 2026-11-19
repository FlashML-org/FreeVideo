// The conversation with the connected agent: what the user says, what the agent answers, and the agent's jobs as
// cards at the place they happened. Reads and sends use the page session; the agent's token never enters the page.
import {agentGet, agentPost, retryAgentPost} from './agent_session.js';
import {emptyFrameIcon} from './empty_frame.js';
import {ATTACH, attachmentURL, pickPictures, pictureGone, picturesOf, releasePicture, releasePictures, thumbnail, uploadPicture} from './chat_attachments.js';
import {SCENE_TEXT, pickScene, sceneList, startScene, watchScene} from './whitebox_scenes.js';
import {chatData, chatDelay, chatReadRequest, emptyChat, chatMerge, chatEntries, canSend, newId, SEND_ATTEMPTS, WAIT_FOR_AGENT, sendRefused, sendError, messageTooLong} from './chat_data.js';
export {emptyChat, chatMerge, chatEntries, canSend} from './chat_data.js';
import {isolateDialogKeys, listenDialogKey, routeDialogKey} from './dialog_keys.js';
import {closeDialog} from './motion.js';
import { estimatedFraction, remainingAbove, remainingShown, createDiagnosticReportControl } from './generation_progress.js';
import {createErrorReport, failureAdvice, failureFold} from './error_panel.js';
import {canDeleteConversation, canRenameConversation, conversationMenu, emptyConversations, hasConversations, mergeConversations, removeConversation, runningElsewhere, selectConversation} from './autopilot_conversations.js';
import {renderImageFailure} from './image_generation.js';
import {issueButton} from './issue_button.js';
import {about} from './request_assets.js';
import {showEmptyPrompt} from './empty_prompt_notice.js';
import {createSideRail, ICON, localTime, markText, matches, promptOf, statusKey, statusText} from './autopilot_panels.js';
import {tip, WHY} from './autopilot_tips.js';
import {createHistoryMenu, forgetStoredSaid, saidIn} from './autopilot_history.js';

// The conversation's own sheet, then the glass layout over it (rails, the floating box to type in, the cards).
for (const name of ['./autopilot_chat.css', './autopilot_glass.css']) {
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL(name, import.meta.url).href; document.head.append(css);
}

const CHAT_ISSUE = ['Chat', '对话'];
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
// Rewriting a text node drops the reader's selection, so a refresh writes only what changed.
function put(node, text) { if (node.textContent !== text) node.textContent = text; }
function glyph(path, width = 2.2) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('stroke-width', String(width));
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.setAttribute('d', path); svg.append(p);
    return svg;
}
const press = (cls, text, action) => { const b = el('button', cls, text); b.type = 'button'; b.onclick = action; return b; };
// Frontend placeholder contract; Drew can rename the request field here, and Iris can replace the copy/icon here.
export const WHITEBOX_PREVIEW = Object.freeze({
    field: 'whitebox_preview', storageKey: 'freevideo.chat.whitebox-preview',
    label: ['Whitebox', '白模'], name: ['Preview a whitebox first', '先生成白模预览'],
    icon: 'M12 3l9 5v8l-9 5-9-5V8l9-5M3 8l9 5 9-5M12 13v8',
});
let chatNumber = 0;

// What the agent is doing before it answers, by the key it reports. Words the page does not know are not shown.
export const STAGE_TEXT = Object.freeze({
    reading: ['Reading the request', '正在理解要求'], scripting: ['Writing the script', '正在写剧本'],
    storyboarding: ['Planning the shots', '正在拆分镜头'], prompting: ['Writing the shot prompts', '正在写画面描述'],
    checking: ['Checking the results', '正在检查结果'], assembling: ['Joining the shots', '正在拼接镜头'],
});
const WORKING = ['running', 'queued', 'waiting', 'cancelling'], VIDEOS = ['generate', 'assemble'];
// Beside the dots while the agent names no stage: the sentence is still on its way, or the agent has it and the seconds count.
export function waitingText(s, t) {
    const m = Math.floor(s / 60), r = s % 60;
    return s < 60 ? t(`Waiting for a reply · ${s} s`, `正在等待回复 · ${s} 秒`)
        : t(`Waiting for a reply · ${m} min${r ? ` ${r} s` : ''}`, `正在等待回复 · ${m} 分${r ? ` ${r} 秒` : ''}`);
}
// What a job with no picture of its own is doing, in the reader's language.
const QUIET = {vlm: ['Checking', '检查中'], enhance: ['Enhancing prompt', '增强提示词']};
// Other jobs without a card keep their line while they run, and a finished one still marks the tab.
const BUSY = {music: ['Making music', '制作配乐']};
// A failed card's one action: go on keeping the finished parts, run the same request again, or say the words again.
const AGAIN = {continue: ['Continue generating', '继续生成'], retry: ['Retry', '重试'], resend: ['Send again', '再发一次']};
const WHITEBOX = {
    generate: ['Generate video', '生成视频'], edit: ['Edit prompt', '改描述'], decline: ['Skip', '跳过'],
    approved: ['Approved', '已确认'], superseded: ['Replaced', '已被替换'], declined: ['Skipped', '已跳过'],
    preview: ['Whitebox preview', '白模预览'], unavailable: ['Preview could not load', '预览加载失败'],
    play: ['Play', '播放'], pause: ['Pause', '暂停'], seconds: ['s', '秒'],
    failure: ['Could not start: ', '无法开始生成：'],
};
const GENERIC_FAILURE_ZH = '没有完成';
// The failures whose next step is the problem report (agent/errors.py FAILURE_NEXT_*).
const UNEXPECTED_FAILURES = ['internal_error', 'generation_failed'];
// The engine's next step for a reason, as the sentence after it.
const withNext = (reason, next, zh) => next ? reason.replace(/[。.]$/, '') + (zh ? '。' : '. ') + next : reason;
// Sends and failure cards use the same public reason, including the engine's Chinese mapping.
function failureReason(value, t) {
    const error = value.error, reason = error?.error_zh || value.error_zh, reasonEn = error?.error_en || value.error_en;
    if (error?.code === 'session_full' && (!reason || reason === GENERIC_FAILURE_ZH))
        return t('This chat is full. Click "New chat".', '这个对话已满，请点“新对话”。');
    // The next step belongs to the same object as the reason it follows.
    const own = error?.error_zh ? error : value;
    // `message` is written for programs; people read the engine's own English reason. A refusal the engine
    // did not classify keeps its sentence, as before.
    return t(withNext(reasonEn || error?.message || 'Did not finish', reasonEn && own.next_en),
        withNext(reason || GENERIC_FAILURE_ZH, reason && own.next_zh, true));
}

// A failed job's one reason line. The engine's classified sentence (both languages) is for people; anything else is
// program text and goes to the technical details, so the card then says only that the generation did not finish.
export function failureCardReason(value, t) {
    const error = value.error, zh = error?.error_zh || value.error_zh, en = error?.error_en || value.error_en;
    const said = zh && en && zh !== GENERIC_FAILURE_ZH && en !== 'Did not finish' && zh !== '生成没有完成';
    if (said || error?.code === 'session_full') return failureReason(value, t);
    // Unclassified: the creation panel's reading of the original text (memory, a missing file, a component that
    // did not load...) says the cause and the next step; only what it cannot place stays "did not finish".
    const raw = [error?.code, error?.message].filter(value => typeof value === 'string' && value).join('\n');
    const advice = raw ? failureAdvice(raw, t) : null;
    if (advice && advice.kind !== 'unknown' && advice.title)
        return [advice.title, advice.summary || advice.action].filter(Boolean).join(t('. ', '。'));
    return t('Generation did not finish.', '生成未完成。');
}

export {about};
export function clock(seconds) { const s = Math.max(0, Math.round(seconds)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; }
export const jobName = (job, t) => job.label || (job.kind === 'image' ? t('Picture', '图片') : t('Video', '视频'));
export {promptOf};
export const jobFraction = job => Math.max(0, Math.min(1, Number(job.progress?.fraction) || 0));
// What a job's last progress stands for `age` seconds after it was made, by the creation panel's rule.
export function jobEstimate(job, age) {
    const e = job.progress?.estimate, sent = e?.overall ? estimatedFraction(e, e.overall, age, age) : null;
    return Math.max(jobFraction(job), sent ?? 0);
}
// The level a job shows, given the engine's number (`base`), the time it says is left and the clock. The engine's
// number can stand for a long while: a model loading for the first time runs far past the time the engine gave it,
// and a video in several parts has no timing at all. While it stands the level still rises, at the pace of the time
// left, slowing down, and by no more than a quarter of what remains. When the number moves again, what was added is
// kept only as far as the number has not caught up with it: the rest of the way is then drawn shorter, so the level
// neither waits nor goes back and is full at the end, and a run that goes as the engine said shows the engine's number.
const DRIFT = .25, RISE = .0005;
export const newLevel = () => ({value: 0, top: null, since: 0, scale: 1});
// The time left a running job shows, by the creation panel's rule: once measured never raised, and gone once its last
// minute has passed. `row` is what the job showed before (none yet: it starts at the engine's number); `seconds` is null
// from the moment the time has run out.
export function jobLeft(row, sent, source, time) {
    if (!row) {
        if (!(sent > 0)) return null;
        row = {seconds: sent, at: time, minute: null, last: 0, source: null, above: 0};
    }
    if (row.seconds === null) return row;
    // Before the run is measured the engine's number is an estimate from earlier runs: follow it up as well as down.
    const named = typeof source === 'string', initial = source === 'similar';
    // The first number that is not that estimate replaces it once, up or down, and its last minute starts over.
    if (named && !initial && row.source === 'similar' && sent > 0) { row.seconds = sent; row.minute = null; }
    if (named) row.source = source;
    const target = sent > 0 ? sent : null; row.above = remainingAbove(row.above, row.seconds, target, time - row.at);
    row.seconds = remainingShown(row.seconds, target, time - row.at, initial, row.above); row.at = time;
    if (row.seconds < 60 && row.minute === null) { row.minute = time; row.last = row.seconds; }
    // An estimate that rises out of its last minute starts that minute over.
    else if (row.seconds >= 60 && initial) row.minute = null;
    if (row.seconds <= 0 || row.minute !== null && time - row.minute >= row.last) row.seconds = null;
    return row;
}
export function jobLevel(row, base, left, time) {
    if (row.top === null || base > row.top + RISE) {
        if (row.top !== null && base < 1) row.scale = Math.min(row.scale, (1 - row.value) / (1 - base));
        row.top = base; row.since = time;
    }
    // The number itself, or where standing since it last moved has brought the level: whichever is further on.
    const from = 1 - row.scale * (1 - row.top), level = 1 - row.scale * (1 - Math.max(base, row.top));
    const more = left > 0 ? (1 - from) * DRIFT * (1 - Math.exp(-Math.max(0, time - row.since) / (DRIFT * left))) : 0;
    return row.value = Math.max(row.value, Math.min(.99, Math.max(level, from + more)));
}
// working, done, stopped or failed: which card a job shows.
export const jobMode = job => WORKING.includes(job.status) ? 'working' : job.status === 'succeeded' ? 'done' : job.status === 'cancelled' ? 'stopped' : 'failed';
const SAVING = ['decode', 'finishing', 'assembling'], stepped = p => p.phase === 'sampling' && p.step?.total && p.step.done >= 1;
// The one line that says where a job is. Sampling leaves it blank; the preview shows progress.
export function jobLine(job, t) {
    const p = job.progress || {};
    if (job.status === 'cancelling') return t('Stopping', '正在停止');
    if (job.status !== 'running') return t('Queued', '排队中');
    if (stepped(p)) return '';
    if (SAVING.includes(p.phase)) return t('Saving', '保存中');
    if (p.phase === 'encoding') return t('Reading the prompt', '读取提示词');
    // Before the first step of the first part the model is still settling in: the line does not turn back to "Preparing".
    if (['load', 'loading'].includes(p.phase) || p.phase === 'sampling' && !(p.block?.done > 0)) return t('Loading the model', '加载模型');
    return t('Preparing', '准备中');
}
// The line a card shows. After the last step, and again after saving, the engine has a moment with no step and no
// stage to report: a card that has started sampling or said "Saving" for a part keeps that line until there is a newer
// one, and does not go back to the lines from before the first step. `said` is the card's own memory.
export function cardLine(said, job, t) {
    const p = job.progress || {}, part = p.block?.index ?? 0;
    if (job.status !== 'running' || said.part !== part) said.last = null;
    said.part = part;
    if (job.status === 'running' && (stepped(p) || SAVING.includes(p.phase))) said.last = job;
    return jobLine(said.last || job, t);
}
// The generation page's rule: three minutes without a new step or stage and the card says it is still going.
export const STALL_MS = 180000;
export const progressKey = job => { const p = job.progress || {}; return JSON.stringify([job.status, p.phase, p.step, p.block, p.fraction]); };
// The one thing a hidden tab announces in its title, most pressing first: a question, a failure, a finished job.
// `seen` holds the finished jobs the user already had in front of them when the tab was hidden.
export function tabNotice(jobs, asked, seen) {
    if (asked) return 'ask';
    const fresh = Object.values(jobs).filter(job => !QUIET[job.kind] && !WORKING.includes(job.status) && !seen.has(job.id));
    if (fresh.some(job => jobMode(job) === 'failed')) return 'failed';
    return fresh.some(job => jobMode(job) === 'done') ? 'done' : null;
}
const TAB_PREFIX = {ask: ['Needs your OK · ', '要确认 · '], failed: ['Failed · ', '出错了 · '], done: ['Done · ', '已完成 · ']};
export function jobMeta(job, t) {
    const parts = [];
    if (job.width > 0 && job.height > 0) parts.push(`${job.width} × ${job.height}`);
    if (job.seconds > 0) parts.push(t(`${job.seconds} s`, `${job.seconds} 秒`));
    return parts.join(' · ');
}
// One number per part of a long video: 1 when made, how far along for the one being made, 0 for the rest.
// The engine's own fraction for the part carries on through decoding and saving; without it, only the steps count.
export function jobParts(job) {
    const block = job.progress?.block, step = job.progress?.step;
    if (!(block?.total > 1)) return [];
    const inside = Number.isFinite(block.fraction) ? Math.min(1, Math.max(0, block.fraction))
        : job.progress.phase === 'sampling' && step?.total ? Math.min(1, step.done / step.total) : 0;
    return Array.from({length: block.total}, (_, i) => i < (block.done || 0) ? 1 : i === block.index - 1 ? inside : 0);
}
// Each part's share of the row, by its frames: the percentage weighs the parts the same way. Equal shares without them.
export function partShares(job) {
    const block = job.progress?.block, frames = block?.frames;
    return Array.isArray(frames) && frames.length === block.total && frames.every(n => Number.isFinite(n) && n > 0) ? frames : null;
}

// Saves a picture through the browser's own download, under the file's name.
function saveImage(src) {
    const link = el('a'); link.href = src; link.download = new URL(src, location.href).searchParams.get('filename') || '';
    document.body.append(link); link.click(); link.remove();
}

// A picture is viewed here, independently of the video library. The image stays within reach while zoomed.
function imageViewer(t, src, name, opener, failed) {
    const dialog = el('dialog', 'fv-studio fv-chat-image-viewer'), actions = el('div', 'fv-chat-actions');
    dialog.setAttribute('aria-label', name);
    const close = press('fv-close', '×', () => closeDialog(dialog));
    close.setAttribute('aria-label', t('Close', '关闭')); close.title = t('Close', '关闭'); tip(close, 'close', t);
    actions.append(tip(press('fv-chat-save', t('Download', '下载'), () => saveImage(src)), 'download', t), close);
    const stage = el('div', 'fv-chat-image-stage'), image = el('img');
    image.alt = name; image.draggable = false; stage.append(image); dialog.append(actions, stage);
    let scale = 1, fit = 1, x = 0, y = 0, disposed = false;
    let frameWidth = 0, frameHeight = 0, pictureWidth = 0, pictureHeight = 0;
    const pointers = new Map();
    const draw = () => {
        const limitX = Math.max(0, (image.naturalWidth * scale - stage.clientWidth) / 2);
        const limitY = Math.max(0, (image.naturalHeight * scale - stage.clientHeight) / 2);
        x = Math.max(-limitX, Math.min(limitX, x)); y = Math.max(-limitY, Math.min(limitY, y));
        image.style.transform = `translate(-50%, -50%) translate(${x}px, ${y}px) scale(${scale})`;
    };
    const resize = () => {
        const w = stage.clientWidth, h = stage.clientHeight, iw = image.naturalWidth, ih = image.naturalHeight;
        if (!iw || !ih || !w || !h) return;
        // The observer's first notification can follow the first gesture; do not reset zoom for the same size.
        if (w === frameWidth && h === frameHeight && iw === pictureWidth && ih === pictureHeight) return;
        frameWidth = w; frameHeight = h; pictureWidth = iw; pictureHeight = ih;
        fit = Math.min(w / iw, h / ih);
        scale = fit; x = y = 0; draw();
    };
    const zoom = (factor, cx, cy) => {
        const next = Math.max(fit, Math.min(fit * 16, scale * factor)), ratio = next / scale;
        x = cx - (cx - x) * ratio; y = cy - (cy - y) * ratio; scale = next; draw();
    };
    stage.addEventListener('wheel', event => {
        event.preventDefault();
        const rect = stage.getBoundingClientRect(), unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? stage.clientHeight : 1;
        zoom(Math.exp(-event.deltaY * unit / 500), event.clientX - rect.left - rect.width / 2, event.clientY - rect.top - rect.height / 2);
    }, {passive: false});
    const gesture = () => {
        const [a, b] = [...pointers.values()], rect = stage.getBoundingClientRect();
        return {cx: (a.x + b.x) / 2 - rect.left - rect.width / 2, cy: (a.y + b.y) / 2 - rect.top - rect.height / 2,
            distance: Math.hypot(a.x - b.x, a.y - b.y)};
    };
    stage.onpointerdown = event => {
        if (event.button !== 0) return;
        pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
        stage.setPointerCapture(event.pointerId); stage.dataset.dragging = 'true';
    };
    stage.onpointermove = event => {
        const previous = pointers.get(event.pointerId); if (!previous) return;
        const before = pointers.size === 2 ? gesture() : null;
        pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
        if (before) {
            const after = gesture(); x += after.cx - before.cx; y += after.cy - before.cy;
            zoom(before.distance > 0 ? after.distance / before.distance : 1, after.cx, after.cy);
        } else { x += event.clientX - previous.x; y += event.clientY - previous.y; draw(); }
    };
    stage.onpointerup = stage.onpointercancel = stage.onlostpointercapture = event => {
        pointers.delete(event.pointerId); stage.dataset.dragging = String(pointers.size > 0);
    };
    // Keep ComfyUI's document shortcuts out of this modal while retaining native Tab/Enter behavior.
    const stopKeys = isolateDialogKeys(dialog, event => routeDialogKey(event, dialog));
    const observer = new ResizeObserver(resize);
    const cleanup = () => {
        if (disposed) return;
        disposed = true; observer.disconnect(); stopKeys(); pointers.clear(); image.onload = image.onerror = null;
        dialog.remove(); if (opener.isConnected && !opener.disabled) opener.focus({preventScroll: true});
    };
    dialog.addEventListener('cancel', event => { event.preventDefault(); closeDialog(dialog); });
    dialog.addEventListener('close', cleanup, {once: true});
    image.onload = () => { image.style.width = `${image.naturalWidth}px`; image.style.height = `${image.naturalHeight}px`; resize(); };
    image.onerror = () => { failed(); closeDialog(dialog); };
    document.body.append(dialog); dialog.showModal(); observer.observe(stage); close.focus(); image.src = src;
    return () => { if (dialog.open) dialog.close(); cleanup(); };
}

// open(job) shows a finished video in Creations, edit(job) takes it to the editing view. shown() says whether the
// user can see the conversation; the page calls sync() whenever that may have changed. changed(view) follows every
// answer: {known, agent, current, active, any}.
// An earlier version kept the last reading of the chats (with its messages) in this browser for a quick first visit.
// Nothing of a conversation is kept in the browser now: the view is kept while the page is open instead, and the
// shell holds the previous frame until the first reading is drawn. The old record is removed.
const CACHE_PREFIX = 'freevideo.autopilot.cache:';
export function readChatCache() {
    try {
        for (let i = localStorage.length - 1; i >= 0; i--) { const key = localStorage.key(i); if (key?.startsWith(CACHE_PREFIX)) localStorage.removeItem(key); }
    } catch { /* Nothing to remove. */ }
    return null;
}

export function agentChat(t, api, {open, edit, shown = () => true, changed = () => {}, interval = 1500, productVersion = () => null, initialPrompt = '', initialWhiteboxPreview = false, initialScene = '', hint = null, undoMs = 0} = {}) {
    const dataLayer = chatData(api);
    const element = el('section', 'fv-chat'), main = el('div', 'fv-chat-main'), scroller = el('div', 'fv-chat-scroll'), log = el('ol', 'fv-chat-log');
    log.setAttribute('role', 'log'); log.setAttribute('aria-label', t('Conversation', '对话'));
    const down = press('fv-chat-down', null, () => { scroller.scrollTo({top: scroller.scrollHeight, behavior: 'smooth'}); });
    down.append(glyph('M12 5v14M5 12l7 7 7-7')); down.setAttribute('aria-label', t('Latest', '最新')); down.hidden = true;
    const form = el('form', 'fv-chat-input'), input = el('textarea'), sendButton = el('button', 'fv-chat-send');
    input.rows = 1; input.maxLength = 8000; input.setAttribute('aria-label', t('Tell the assistant', '对助手说'));
    sendButton.type = 'submit'; sendButton.setAttribute('aria-label', t('Send', '发送')); sendButton.append(glyph('M12 19V5M5 12l7-7 7 7'));
    // While the agent is thinking and has started nothing, the send key is the stop key: same place, a solid square.
    let stopMode = false;
    const SEND_GLYPH = 'M12 19V5M5 12l7-7 7 7', STOP_GLYPH = 'M7 7h10v10H7z';
    function refreshSend() {
        const label = stopMode ? t('Stop', '停止') : t('Send', '发送');
        sendButton.classList.toggle('fv-chat-halt', stopMode);
        sendButton.setAttribute('aria-label', label); sendButton.title = stopMode ? label : '';
        sendButton.querySelector('path').setAttribute('d', stopMode ? STOP_GLYPH : SEND_GLYPH);
        sendButton.disabled = stopMode ? false : !composeAllowed() || draftSending() || !readyPictures().length && !input.value.trim() || stagedList().some(item => !item.attachment);
        const why = stopMode || !sendButton.disabled ? null : !composeAllowed() ? (creating || !inSelectedChat() ? WHY.loading : WHY.notConnected)
            : draftSending() ? WHY.sending : stagedList().some(item => !item.attachment) ? WHY.uploading : WHY.empty;
        tip(sendButton, stopMode ? 'halt' : 'send', t, {why: why ? t(...why) : ''});
    }
    async function abandon() {
        const session = state.session;
        if (!session || state.abandoned) return;
        state = {...state, abandoned: true, stage: null}; render();
        input.focus();
        let done = false;
        try { done = (await dataLayer.abandon(session)).ok; } catch {}
        if (!done && state.session === session) { state = {...state, abandoned: false}; render(); say(t('Could not stop. Click again; if it keeps failing, report the problem.', '没能停止。请再点一次；仍然失败时，请提交问题。')); }
    }
    let whiteboxPreview = false;
    try { whiteboxPreview = localStorage.getItem(WHITEBOX_PREVIEW.storageKey) === 'true'; } catch {}
    const whiteboxButton = press('fv-chat-whitebox', null, () => setWhiteboxPreview(!whiteboxPreview));
    whiteboxButton.append(glyph(WHITEBOX_PREVIEW.icon), el('span', null, t(...WHITEBOX_PREVIEW.label)));
    whiteboxButton.title = t(...WHITEBOX_PREVIEW.name); whiteboxButton.setAttribute('aria-label', whiteboxButton.title);
    function setWhiteboxPreview(enabled) {
        whiteboxPreview = enabled; whiteboxButton.setAttribute('aria-pressed', String(enabled));
        try { localStorage.setItem(WHITEBOX_PREVIEW.storageKey, String(enabled)); } catch {}
        queueMicrotask(() => { if (!stopped) render(); });
    }
    whiteboxButton.setAttribute('aria-pressed', String(whiteboxPreview));
    if (initialWhiteboxPreview) setWhiteboxPreview(true);
    const scenesButton = press('fv-chat-scenes', null, () => toggleScenes());
    scenesButton.append(glyph(SCENE_TEXT.icon), el('span', null, t(...SCENE_TEXT.label)));
    scenesButton.title = t(...SCENE_TEXT.name); scenesButton.setAttribute('aria-label', scenesButton.title);
    scenesButton.setAttribute('aria-haspopup', 'menu'); scenesButton.setAttribute('aria-expanded', 'false');
    const sceneMenu = el('div', 'fv-chat-scene-menu'); sceneMenu.setAttribute('role', 'menu'); sceneMenu.hidden = true;
    let sceneRows = null, entryScene = initialScene || null, sceneWaiting = false;
    function toggleScenes(open = sceneMenu.hidden) {
        sceneMenu.hidden = !open; scenesButton.setAttribute('aria-expanded', String(open));
        if (!open) return;
        // One ready-made scene needs no choosing: the button starts it.
        if (sceneRows?.length === 1) { toggleScenes(false); void runScene(sceneRows[0].id); return; }
        if (sceneRows) return;
        sceneRows = [];
        void sceneList(api).then(rows => {
            sceneRows = rows;
            if (rows.length === 1 && !sceneMenu.hidden) { toggleScenes(false); void runScene(rows[0].id); return; }
            sceneMenu.replaceChildren(...rows.map(row => {
                const item = press('fv-chat-scene-item', row.name, () => { toggleScenes(false); void runScene(row.id); });
                item.setAttribute('role', 'menuitem'); tip(item, 'scene', t); return item;
            }));
        }).catch(() => { sceneRows = null; toggleScenes(false); say(t(...SCENE_TEXT.failed) + t('Connection lost. Try again.', '连接中断，请重试。')); });
    }
    // `start` resolves with the scene's id; false means the video is made as usual (no ready-made scene fits).
    async function runScene(preset, session = state.session, start = () => startScene(api, preset, session)) {
        if (!session || stopped || sceneWaiting) return null;
        say(t(...SCENE_TEXT.preparing)); sceneWaiting = true;
        try {
            const id = await start();
            if (id === false) { sceneWaiting = false; say(''); return false; }
            const result = id ? await watchScene(api, id, {stopped: () => stopped, onState: value => { if (value.status === 'rendering') say(t(...SCENE_TEXT.rendering)); }}) : {status: 'ready'};
            if (result.status === 'failed') {
                sceneWaiting = false; const why = (result.error || '').replace(/[.。]+$/, '');
                say(why ? t(...SCENE_TEXT.failed) + why + t(...SCENE_TEXT.retry) : t(...SCENE_TEXT.bare));
            }
            else if (result.status === 'timeout') {
                sceneWaiting = false;
                say(t(...(result.last === 'rendering' ? SCENE_TEXT.timeout : result.page_open ? SCENE_TEXT.notStarted : SCENE_TEXT.noPage)));
            }
        } catch (failure) { sceneWaiting = false; say(t(...SCENE_TEXT.failed) + reason(failure)); return 'error'; }
        return true;
    }
    // Pictures waiting to be sent, per conversation draft: {key, url, attachment|null}. A picture is uploaded the moment it is added.
    const attachButton = press('fv-chat-attach', null, () => fileInput.click()), fileInput = el('input'), strip = el('ul', 'fv-chat-attached');
    attachButton.append(glyph(ATTACH.icon)); attachButton.title = t(...ATTACH.add); attachButton.setAttribute('aria-label', attachButton.title);
    fileInput.type = 'file'; fileInput.accept = ATTACH.types.join(','); fileInput.multiple = true; fileInput.hidden = true;
    strip.hidden = true; strip.setAttribute('aria-label', t('Attached pictures', '已添加的图片'));
    const staged = new Map(), uploads = new Set();
    const stagedList = (session = draftSession) => { if (!staged.has(session)) staged.set(session, []); return staged.get(session); };
    const readyPictures = () => stagedList().filter(item => item.attachment).map(item => ({...item.attachment, preview: item.url}));
    function renderStrip() {
        const list = stagedList();
        strip.hidden = !list.length;
        strip.replaceChildren(...list.map(item => {
            const li = el('li'), drop = press('fv-chat-attached-x', '×', () => removePicture(item));
            li.dataset.state = item.failed ? 'failed' : item.attachment ? 'ready' : 'uploading';
            drop.title = t(...ATTACH.remove); drop.setAttribute('aria-label', drop.title); tip(drop, 'dropPicture', t);
            li.append(thumbnail(item.url), drop);
            // A picture that did not upload is tried again by clicking it.
            if (item.failed === 'failed') {
                li.tabIndex = 0; li.setAttribute('role', 'button'); li.title = t(...ATTACH.retry); li.setAttribute('aria-label', li.title); tip(li, 'uploadAgain', t);
                li.addEventListener('click', event => { if (event.target === drop) return; void upload(item, list); });
                listenDialogKey(li, 'keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); void upload(item, list); } });
            }
            return li;
        }));
        grow();
    }
    // Removing a staged picture also tells the engine to free it, unless another draft still holds the same picture.
    function removePicture(item, release = true) {
        item.controller?.abort();
        for (const list of staged.values()) { const at = list.indexOf(item); if (at >= 0) list.splice(at, 1); }
        const name = item.attachment?.name;
        if (release && name && ![...staged.values()].some(list => list.some(other => other.attachment?.name === name))) void releasePicture(api, name);
        URL.revokeObjectURL?.(item.url); renderStrip(); input.focus();
    }
    // A send refused because its pictures were freed meanwhile uploads them once more from the staged files.
    // The stored pictures of a chat: the one being read from memory, another by one read; staged ones of its draft too.
    async function chatPictures(session) {
        const staged_ = (staged.get(session) || []).map(item => item.attachment?.name).filter(Boolean);
        let items = session === state.session ? [...state.items.values()] : [];
        if (session !== state.session) {
            try { const response = await dataLayer.read({session}); if (response.ok) items = (await response.json()).items || []; } catch { /* Released by the engine's own cleanup, if any. */ }
        }
        return [...new Set([...picturesOf(items), ...staged_])];
    }
    async function reupload(entry) {
        const pictures = [];
        for (const picture of entry.attachments || []) {
            const item = [...staged.values()].flat().find(other => other.attachment?.name === picture.name && other.file);
            if (!item) return false;
            try { item.attachment = await uploadPicture(api, item.file); } catch { return false; }
            pictures.push({...item.attachment, preview: picture.preview || item.url});
        }
        entry.attachments = pictures;
        return pictures.length > 0;
    }
    async function upload(item, list, session = draftSession) {
        item.controller = new AbortController(); item.failed = null; uploads.add(item); renderStrip();
        try {
            item.attachment = await uploadPicture(api, item.file, item.controller.signal);
            // The same picture twice is one stored picture; the copy is dropped.
            if (list.some(other => other !== item && other.attachment?.name === item.attachment.name)) removePicture(item, false);
        } catch (failure) {
            if (!failure?.problem) return;
            // Unreadable pictures are taken out; an upload that failed stays, marked, to be tried again.
            if (failure.problem === 'unreadable') { const at = list.indexOf(item); if (at >= 0) list.splice(at, 1); URL.revokeObjectURL?.(item.url); }
            else item.failed = 'failed';
            if (session === draftSession) say(t(...ATTACH[failure.problem]));
        } finally { uploads.delete(item); if (!stopped && session === draftSession) renderStrip(); }
    }
    function addPictures(files) {
        files = [...files || []];
        if (!files.length || !composeAllowed() || builtinScene()) return;
        const session = draftSession, list = stagedList(session), {accepted, problem} = pickPictures(files, ATTACH.count - list.length);
        say(problem ? t(...ATTACH[problem]) : '');
        for (const file of accepted) {
            const item = {file, url: URL.createObjectURL(file), attachment: null, failed: null, controller: null};
            list.push(item); void upload(item, list, session);
        }
        renderStrip();
    }
    fileInput.addEventListener('change', () => { addPictures(fileInput.files); fileInput.value = ''; });
    listenDialogKey(input, 'paste', event => {
        const files = [...event.clipboardData?.files || []].filter(file => file.type.startsWith('image/'));
        if (files.length) { event.preventDefault(); addPictures(files); }
    });
    for (const type of ['dragover', 'drop']) main.addEventListener(type, event => {
        if (![...event.dataTransfer?.types || []].includes('Files')) return;
        event.preventDefault(); if (type === 'drop') addPictures(event.dataTransfer.files);
    });
    // The box to type in floats on its own layer under the conversation: the words on top, the tools on one row
    // below, Send at the end of that row. Notices, the next download and the scene list are anchored above it, so
    // they come and go without moving the conversation.
    const composeTools = el('div', 'fv-chat-compose-tools'), spacer = el('span', 'fv-chat-grow');
    const historyButton = press('fv-chat-history-button', null, () => history.open());
    historyButton.append(glyph('M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2', 2), el('span', null, t('History and templates', '历史与模板')));
    historyButton.setAttribute('aria-label', t('History and templates', '历史与模板')); tip(historyButton, 'history', t);
    composeTools.append(attachButton, whiteboxButton, scenesButton, historyButton, spacer, sendButton);
    form.append(strip, input, composeTools, fileInput);
    // The slot: one line of fixed height right above the box, kept even when empty, for receipts, the next download
    // and failures; one shows at a time (a notice before the download line), so nothing ever moves the conversation.
    const dock = el('div', 'fv-chat-dock sgf-avoid'), above = el('div', 'fv-chat-above'), slot = el('div', 'fv-chat-slot');
    slot.setAttribute('aria-live', 'polite');
    scroller.append(log); dock.append(above, slot, form); main.append(scroller, dock); element.append(main);
    const notice = el('p', 'fv-chat-notice'); notice.hidden = true; notice.setAttribute('role', 'status');
    const noticeRow = el('div', 'fv-chat-notice-row'); noticeRow.append(notice);
    above.append(down, sceneMenu); slot.append(noticeRow);
    // Sentences said before (any chat, this browser) and templates; picking one fills the box and never sends.
    forgetStoredSaid();
    const history = createHistoryMenu(t, {button: historyButton, input, host: above, time: at => at ? localTime(at, t) : '', said: () => saidIn([...state.items.values()]),
        fill: text => { input.value = text; input.dispatchEvent(new Event('input')); }});
    // What the next video would download first, the studio's line, above the box the request is typed in.
    if (hint) slot.append(hint.element);
    tip(attachButton, 'attach', t); tip(whiteboxButton, 'whitebox', t); tip(scenesButton, 'scenes', t); tip(down, 'latest', t);
    // A quoted button name stays on one line, so “新对话” never wraps in the middle.
    function quoted(node, text) {
        if (node.textContent === text) return;
        const parts = String(text).split(/(“[^”]{1,12}”|"[^"]{1,24}")/);
        node.textContent = text;
        if (parts.length > 1) node.replaceChildren(...parts.flatMap((part, index) => part ? [el('span', index % 2 ? 'fv-chat-keep' : '', part)] : []));
    }
    // `failure`: the line reports a fault, so the button sits beside it.
    const noticeIssue = issueButton(t, () => ({where: CHAT_ISSUE, message: notice.textContent})); noticeIssue.hidden = true;
    // A receipt's one action (Undo) sits beside it on the same line.
    const noticeAction = press('fv-chat-notice-action', null, () => noticeAction.run?.()); noticeAction.hidden = true;
    noticeRow.append(noticeAction, noticeIssue);
    // The line says the message is too long until the words fit.
    let tooLong = false;
    const TOO_LONG = () => t('This message is too long; shorten it or send it in two parts', '这条消息太长，请缩短或分成两条发送');
    function say(text, failure = false) { if (text !== TOO_LONG()) tooLong = false; quoted(notice, text); notice.title = text || ''; notice.hidden = !text; noticeIssue.hidden = !text || !failure; noticeAction.hidden = true; noticeAction.run = null; }
    function receipt(text, label, key, run) {
        say(text); noticeAction.textContent = label; noticeAction.run = run; noticeAction.hidden = false; tip(noticeAction, key, t);
    }

    let dismissImage = null;
    let state = emptyChat(), pending = [], known = false, stopped = false, timer = null, busy = false, failures = 0;
    let conversations = emptyConversations(), selectedSession, selection = 0, reread = false, focusSession = null, creating = false;
    // The reading position is scoped to this server; drafts stay in this open page.
    let selectionKey = null;
    try {
        selectionKey = `freevideo.chat.selected:${new URL(api.apiURL('/freevideo/agent/chat'), location.href).href}`;
        selectedSession = localStorage.getItem(selectionKey) || undefined;
    } catch { /* Storage can be unavailable. Reading still works. */ }
    function rememberSelection(session) {
        try { if (selectionKey) { if (session) localStorage.setItem(selectionKey, session); else localStorage.removeItem(selectionKey); } } catch {}
    }
    const drafts = new Map(), unsaidSessions = new Set();
    let draftSession = selectedSession ?? null;
    let entryDraft = initialPrompt || null;
    input.value = initialPrompt;
    function saveDraft() {
        if (drafts.get(draftSession)?.text !== input.value) drafts.set(draftSession, {text: input.value});
        return drafts.get(draftSession);
    }
    function showDraft(session) {
        // Attach the entry example to the first resolved conversation, even if
        // the server selects a different one. This is a draft, never a send.
        if (entryScene !== null && session) { const preset = entryScene; entryScene = null; void runScene(preset, session); }
        if (entryDraft !== null) {
            draftSession = session; input.value = entryDraft; entryDraft = null;
            saveDraft(); grow(); return;
        }
        if (draftSession === session) return;
        saveDraft(); draftSession = session; input.value = drafts.get(session)?.text || ''; renderStrip();
    }
    const reason = error => error?.message || t('Connection lost. Try again.', '连接中断，请重试。');
    async function requestError(response) {
        const data = await response.json().catch(() => ({}));
        return Object.assign(new Error(data.error?.message || data.error?.code || `HTTP ${response.status}`), {code: data.error?.code});
    }
    // Work under way in another conversation while this one has none: {session, job, ids}. The page's header keeps it in sight.
    let away = null;
    let listBusy = false, listAgain = false, listAt = -Infinity;
    const listInterval = 10000;
    const sidebar = el('nav', 'fv-chat-conversations'), list = el('div', 'fv-chat-conversation-list'), tools = el('div', 'fv-chat-tools');
    const skippedConversations = el('div', 'fv-chat-unreadable'); skippedConversations.hidden = true;
    sidebar.id = `fv-chat-conversations-${++chatNumber}`; sidebar.setAttribute('aria-label', t('Chats', '对话')); sidebar.hidden = true;
    const newChat = press('fv-chat-new', t('New chat', '新对话'), () => void startChat());
    newChat.prepend(glyph('M12 5v14M5 12h14')); newChat.title = t('New chat', '新对话');
    const newError = el('p', 'fv-chat-error fv-chat-new-error'); newError.hidden = true; newError.setAttribute('role', 'alert');
    const newIssue = issueButton(t, () => ({where: CHAT_ISSUE, message: newError.textContent})); newIssue.hidden = true;
    // Why a deletion did not happen, with what can be done next; never left silent.
    const deleteError = el('div', 'fv-chat-error fv-chat-delete-error'); deleteError.hidden = true; deleteError.setAttribute('role', 'alert');
    function deleteFailed(session, error, stop = false, report = true) {
        const text = el('p', null, error), actions = el('div', 'fv-chat-delete-actions');
        if (stop) actions.append(tip(press('fv-chat-menu-item', t('Stop and delete', '停止并删除'), () => { deleteError.hidden = true; void deleteChat(session, true, true); }), 'stopDelete', t));
        if (report) actions.append(issueButton(t, () => ({where: CHAT_ISSUE, message: text.textContent})));
        deleteError.replaceChildren(text, actions); deleteError.hidden = false;
    }
    const chats = press('fv-chat-chats', null, () => drawer(chats.getAttribute('aria-expanded') !== 'true'));
    const chatsDot = el('i', 'fv-chat-running'); chatsDot.setAttribute('aria-hidden', 'true'); chatsDot.hidden = true;
    chats.append(glyph('M4 5h16v12H9l-5 4V5M8 9h8M8 13h5'), chatsDot);
    chats.title = t('Chats', '对话'); chats.setAttribute('aria-label', chats.title);
    chats.setAttribute('aria-controls', sidebar.id); chats.setAttribute('aria-expanded', 'false');
    tip(newChat, 'newChat', t); tip(chats, 'chats', t);
    // The left panel: the chats (with a search over their names) and, at its foot, whether an assistant is connected.
    // It stays in place with no chats yet, so the conversation keeps the middle of the window.
    const rail = el('aside', 'fv-chat-rail sgf-avoid'), railHead = el('header', 'fv-chat-panel-head');
    rail.setAttribute('aria-label', t('Chats', '对话'));
    // On a narrow window the panel is a drawer, and its toggle closes it.
    // From 900 to 1279 px the panel is a strip whose toggle floats the whole panel out over the conversation.
    const railToggle = press('fv-chat-panel-toggle', null, () => narrow() ? drawer(false, true) : compact() ? drawer(chats.getAttribute('aria-expanded') !== 'true', true) : setPanel('left', panels.left === 'closed'));
    railToggle.append(glyph(ICON.panelLeft, 2));
    railHead.append(el('h2', null, t('Chats', '对话')), newChat, railToggle);
    const searchRow = el('label', 'fv-chat-search'), searchBox = el('input');
    searchBox.type = 'search'; searchBox.placeholder = t('Search chats and shots', '搜索对话和镜头'); searchBox.setAttribute('aria-label', t('Search chats', '搜索对话'));
    searchBox.autocomplete = 'off'; searchBox.spellcheck = false; tip(searchBox, 'search', t, {title: false});
    const slashKey = el('kbd', 'fv-chat-key', '/'); slashKey.setAttribute('aria-hidden', 'true');
    searchRow.append(glyph(ICON.search, 2), searchBox, slashKey);
    const noMatch = el('p', 'fv-chat-rail-empty fv-chat-no-match', t('No chat matches.', '没有匹配的对话。')); noMatch.hidden = true;
    const railEmpty = el('p', 'fv-chat-rail-empty', t('No chats yet. Say what you want to make; the chat is kept here.', '还没有对话。说一句想做什么，对话会留在这里。'));
    const railFoot = el('footer', 'fv-chat-rail-foot');
    searchBox.addEventListener('input', () => renderConversations());
    listenDialogKey(searchBox, 'keydown', event => { if (event.key === 'Escape' && searchBox.value) { event.preventDefault(); event.stopPropagation(); searchBox.value = ''; renderConversations(); } });
    // The right panel's toggle on narrow windows sits opposite the chats toggle, so the head stays symmetric.
    const head = el('div', 'fv-chat-head'), headWords = el('div', 'fv-chat-head-words'), headTitle = el('b', 'fv-chat-head-title'), headSub = el('small', 'fv-chat-head-sub');
    headWords.append(headTitle, headSub);
    const tasksToggle = press('fv-chat-tasks-toggle', null, () => sideDrawer(tasksToggle.getAttribute('aria-expanded') !== 'true'));
    const tasksDot = el('i', 'fv-chat-running'); tasksDot.setAttribute('aria-hidden', 'true'); tasksDot.hidden = true;
    tasksToggle.append(glyph(ICON.tasks, 2), tasksDot); tasksToggle.setAttribute('aria-label', t('Tasks', '任务')); tasksToggle.setAttribute('aria-expanded', 'false');
    tip(tasksToggle, 'tasks', t);
    const headEnd = el('div', 'fv-chat-head-end'); headEnd.append(tasksToggle);
    tools.hidden = true; tools.append(chats); head.append(tools, headWords, headEnd); main.prepend(head);
    // Why a new chat, a rename or a deletion did not happen floats at the panel's lower edge, so the list never moves.
    const railNotices = el('div', 'fv-chat-rail-notices'); railNotices.append(newError, newIssue, deleteError);
    sidebar.append(searchRow, list, skippedConversations, noMatch);
    rail.append(railHead, sidebar, railEmpty, railFoot, railNotices); element.prepend(rail);
    // Both panels can fold away; the middle column keeps its place while either is open, so the conversation never jumps.
    let side_ = null;
    const PANELS_KEY = 'freevideo.autopilot.panels';
    let panels = {left: 'open', right: 'open'};
    try { panels = {...panels, ...JSON.parse(localStorage.getItem(PANELS_KEY) || '{}')}; } catch {}
    function setPanel(side, open) {
        panels = {...panels, [side]: open ? 'open' : 'closed'};
        try { localStorage.setItem(PANELS_KEY, JSON.stringify(panels)); } catch {}
        drawPanels();
        (side === 'left' ? railToggle : side_.element.querySelector('.fv-chat-panel-toggle'))?.focus({preventScroll: true});
    }
    function drawPanels() {
        element.dataset.left = panels.left === 'closed' ? 'closed' : 'open'; element.dataset.right = panels.right === 'closed' ? 'closed' : 'open';
        const leftOpen = element.dataset.left === 'open', rightOpen = element.dataset.right === 'open';
        railToggle.setAttribute('aria-label', leftOpen ? t('Hide chats', '收起对话栏') : t('Show chats', '展开对话栏'));
        railToggle.setAttribute('aria-expanded', String(leftOpen)); tip(railToggle, leftOpen ? 'collapseLeft' : 'expandLeft', t);
        const sideToggle = side_?.element.querySelector('.fv-chat-panel-toggle');
        if (sideToggle) { sideToggle.setAttribute('aria-label', rightOpen ? t('Hide tasks', '收起任务栏') : t('Show tasks', '展开任务栏')); sideToggle.setAttribute('aria-expanded', String(rightOpen)); tip(sideToggle, rightOpen ? 'collapseRight' : 'expandRight', t); }
    }
    // The right panel: this chat's tasks and the details of the one picked. Its buttons act as the cards' own do.
    const findCard = id => [...log.children].find(item => item.dataset?.job === id) || null;
    function showCard(id, focus = null) {
        drawStage();
        const item = findCard(id);
        if (!item) return;
        if (narrow()) sideDrawer(false);
        item.scrollIntoView?.({block: 'center', behavior: globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ? 'auto' : 'smooth'});
        item.dataset.flash = 'true'; setTimeout(() => { delete item.dataset.flash; }, 900);
        const target = focus && item.querySelector(focus);
        (target || item).focus?.({preventScroll: true});
    }
    side_ = createSideRail(t, {
        pick: id => showCard(id),
        stop: id => void cancelJobs([id]),
        why: id => showCard(id, '.fv-chat-retry:not([hidden]), .fv-report-button, button'),
        collapse: () => narrow() ? sideDrawer(false, true) : compact() ? sideDrawer(tasksToggle.getAttribute('aria-expanded') !== 'true', true) : setPanel('right', element.dataset.right === 'closed'),
        open: row => {
            const job = row.job;
            if (job.kind === 'image' && job.thumbnail) {
                const opener = document.activeElement;
                dismissImage?.(); dismissImage = imageViewer(t, api.apiURL(job.thumbnail), row.title, opener || element, () => {});
            } else if (job.work) open(job);
        },
        edit: row => { if (row.job.work) edit(row.job); },
    });
    element.append(side_.element);
    drawPanels();
    function sideDrawer(on, restore = false) {
        if (on) drawer(false);
        element.dataset.tasksOpen = String(on); tasksToggle.setAttribute('aria-expanded', String(on));
        main.inert = on && narrow();
        if (on) (side_.element.querySelector('[aria-current=true]') || side_.element.querySelector('button'))?.focus({preventScroll: true});
        else if (restore) (narrow() ? tasksToggle : side_.element.querySelector('.fv-chat-panel-toggle')).focus();
    }
    const listNodes = new Map(), narrow = () => window.matchMedia('(max-width: 899px)').matches;
    const compact = () => !narrow() && window.matchMedia('(max-width: 1279px)').matches;
    const GROUP_WORDS = {running: ['In progress', '进行中'], today: ['Today', '今天'], yesterday: ['Yesterday', '昨天'], earlier: ['Earlier', '更早']};
    // The words of the chat being read that a search also looks in: its tasks' names and prompts.
    let shotWords = [];
    const dayGroup = value => {
        const at = typeof value === 'number' ? new Date(value * (value < 1e12 ? 1000 : 1)) : new Date(value || 0), now = new Date();
        const day = d => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(), days = Math.round((day(now) - day(at)) / 86400000);
        return Number.isNaN(days) ? 'earlier' : days <= 0 ? 'today' : days === 1 ? 'yesterday' : 'earlier';
    };
    // The chat being read, in one line for its row and the column's head: {text, state}.
    let chatSummary = null;
    let menuState = null, changingMenu = false;
    const deleting = new Set(), deleted = new Set();
    // One overlay outside the scrolling list, positioned in the sidebar's own coordinates.
    const menu = el('div', 'fv-chat-menu'), first = el('div', 'fv-chat-menu-layer'), confirmation = el('div', 'fv-chat-menu-layer fv-chat-menu-confirm');
    menu.id = `${sidebar.id}-menu`; menu.setAttribute('role', 'menu'); menu.hidden = true;
    const rename = press('fv-chat-menu-item', t('Rename', '重命名'), () => { const session = menuState?.session; closeMenu(); if (session) startRename(session); });
    const remove = press('fv-chat-menu-item', t('Delete', '删除'), () => {
        menuState = conversationMenu(conversations, menuState, 'confirm');
        updateMenu(true);
    });
    const yes = press('fv-chat-menu-item fv-chat-menu-delete', t('Delete', '删除'), () => undoMs > 0 ? deleteLater(menuState?.session) : void deleteChat(menuState?.session));
    // With an undo window (the page's option), Delete hides the chat at once and says so with Undo; the chat is deleted
    // when the window ends, or at once when the view closes. Undo brings the row back where it was.
    const undoing = new Map();
    let receiptFor = null;
    function deleteLater(session) {
        if (!session || !menuState || menuState.session !== session || menuState.stage !== 'confirm' || deleting.has(session) || undoing.has(session)) return;
        const row = conversations.conversations.find(item => item.id === session), ids = conversations.conversations.map(item => item.id).filter(id => !undoing.has(id));
        const neighbor = ids[ids.indexOf(session) + 1] || ids[ids.indexOf(session) - 1];
        closeMenu();
        const timer = setTimeout(() => finishDelete(session), undoMs);
        undoing.set(session, timer);
        if (conversations.current === session && neighbor) chooseChat(neighbor); else renderConversations();
        (listNodes.get(neighbor)?.button || newChat).focus({preventScroll: true});
        receiptFor = session;
        receipt(t(`Deleted “${row?.title || 'chat'}”`, `已删除“${row?.title || '对话'}”`), t('Undo', '撤销'), 'undoDelete', () => {
            clearTimeout(undoing.get(session)); undoing.delete(session); receiptFor = null; say(''); renderConversations();
            listNodes.get(session)?.button.focus({preventScroll: true});
        });
    }
    function finishDelete(session) {
        if (!undoing.delete(session)) return;
        if (receiptFor === session) { receiptFor = null; if (!stopped) say(''); }
        // A closed or hidden view still deletes what it said it deleted; the reply only matters to an open one.
        if (stopped || !active()) {
            deleted.add(session);
            void chatPictures(session).then(pictures => dataLayer.remove(session).then(response => { if (response?.ok) void releasePictures(api, pictures); })).catch(() => {});
            if (!stopped) renderConversations(); return;
        }
        void deleteChat(session, false, true);
    }
    const cancel = press('fv-chat-menu-item', t('Cancel', '取消'), () => closeMenu(true));
    for (const button of [rename, remove, yes, cancel]) button.setAttribute('role', 'menuitem');
    tip(rename, 'rename', t); tip(remove, 'remove', t); tip(yes, 'removeYes', t); tip(cancel, 'removeNo', t);
    first.append(rename, remove); confirmation.append(yes, cancel); menu.append(first, confirmation); sidebar.append(menu);
    const menuOrigin = () => listNodes.get(menuState?.session)?.more;
    const inMenu = target => menu.contains(target) || menuOrigin()?.contains(target);
    const leaveMenu = event => { if (menuState && !changingMenu && !inMenu(event.relatedTarget)) closeMenu(); };
    menu.addEventListener('focusout', leaveMenu);
    // Where `keys` below moves focus on Tab itself (the narrow drawer; the open menu in the drawer, and its confirmation
    // everywhere), the shared trap in a11y.js stands aside, so each press moves focus once.
    function ownTab() {
        const own = (node, on) => on ? node.setAttribute('data-fv-own-tab', '') : node.removeAttribute('data-fv-own-tab');
        own(rail, narrow() && chats.getAttribute('aria-expanded') === 'true');
        own(menu, !!menuState && (narrow() || menuState.stage === 'confirm'));
    }
    function drawer(on, restore = false) {
        if (!on) closeMenu();
        element.dataset.chatsOpen = String(on); chats.setAttribute('aria-expanded', String(on)); ownTab();
        if (on) sideDrawer(false);
        // Only the overlay needs to keep keyboard focus in its list.
        main.inert = on && narrow();
        if (on) (list.querySelector('[aria-current]') || newChat).focus();
        else if (restore) (narrow() ? chats : railToggle).focus();
    }
    function positionMenu() {
        const node = listNodes.get(menuState?.session);
        if (!node) return;
        const bounds = sidebar.getBoundingClientRect(), anchor = node.more.getBoundingClientRect(), row = node.row.getBoundingClientRect();
        // Local dimensions also handle the enclosing dialog's entry scale without fixed-position offsets.
        const sx = bounds.width / (sidebar.offsetWidth || bounds.width) || 1, sy = bounds.height / (sidebar.offsetHeight || bounds.height) || 1;
        const left = bounds.left + (sidebar.clientLeft || 0) * sx, top = bounds.top + (sidebar.clientTop || 0) * sy;
        const width = sidebar.clientWidth, height = sidebar.clientHeight || bounds.height / sy;
        const mw = menu.offsetWidth || menu.getBoundingClientRect().width / sx, mh = menu.offsetHeight || menu.getBoundingClientRect().height / sy;
        const x0 = Math.max(0, -left / sx), x1 = Math.min(width, ((window.innerWidth || bounds.right) - left) / sx);
        const y0 = Math.max(0, -top / sy), y1 = Math.min(height, ((window.innerHeight || bounds.bottom) - top) / sy);
        const below = (row.bottom - top) / sy, above = (row.top - top) / sy - mh;
        menu.style.left = `${Math.max(x0, Math.min((anchor.right - left) / sx - mw, x1 - mw))}px`;
        menu.style.top = `${Math.max(y0, Math.min(below + mh <= y1 ? below : above, y1 - mh))}px`;
    }
    function updateMenu(focus = false) {
        changingMenu = true;
        try {
            menu.hidden = !menuState;
            const confirming = menuState?.stage === 'confirm';
            first.dataset.active = String(!confirming); confirmation.dataset.active = String(confirming);
            first.inert = !!confirming; confirmation.inert = !confirming;
            const target = conversations.conversations.find(row => row.id === menuState?.session);
            rename.hidden = !canRenameConversation(target); remove.hidden = !canDeleteConversation(target);
            rename.tabIndex = remove.tabIndex = confirming ? -1 : 0; yes.tabIndex = cancel.tabIndex = confirming ? 0 : -1;
            yes.disabled = cancel.disabled = deleting.has(menuState?.session);
            for (const [id, node] of listNodes) {
                const opened = menuState?.session === id;
                node.row.dataset.menuOpen = String(opened); node.more.setAttribute('aria-expanded', String(opened));
            }
            if (menuState) {
                positionMenu();
                if (focus) (confirming ? cancel : rename.hidden ? remove : rename).focus({preventScroll: true});
            }
            ownTab();
        } finally { changingMenu = false; }
    }
    function closeMenu(restore = false) {
        const origin = menuOrigin();
        menuState = conversationMenu(conversations, menuState, 'close'); updateMenu();
        if (restore && origin && !origin.hidden) origin.focus({preventScroll: true});
    }
    function openMenu(session) {
        if (menuState?.session === session) { closeMenu(); return; }
        if (deleting.has(session)) return;
        menuState = conversationMenu(conversations, menuState, 'open', session); updateMenu(true);
    }
    function renderConversations() {
        // Keep this page's unsent conversations reachable even though history only lists spoken ones.
        const rows = conversations.conversations.filter(row => !row.temporary);
        const listed = new Set(rows.map(row => row.id));
        for (const session of unsaidSessions) {
            if (listed.has(session)) { unsaidSessions.delete(session); continue; }
            if (!deleted.has(session) && (drafts.get(session)?.text.trim() || pending.some(entry => entry.session === session)))
                rows.unshift({id: session, title: t('New chat', '新对话'), running: 0, temporary: true});
        }
        conversations = {...conversations, conversations: rows};
        const previous = menuState;
        const lostFocus = previous && inMenu(document.activeElement);
        menuState = conversationMenu(conversations, menuState);
        // After the final deletion, keep New chat reachable as the keyboard destination.
        const unreadable = conversations.unreadable;
        skippedConversations.textContent = unreadable > 0 ? t(`${unreadable} ${unreadable === 1 ? 'conversation' : 'conversations'} could not be read`, `另有 ${unreadable} 个对话无法读取`) : '';
        skippedConversations.hidden = !unreadable;
        const visible = hasConversations(conversations) || conversations.supported === true && (deleted.size > 0 || unreadable > 0);
        element.dataset.conversations = String(visible); sidebar.hidden = tools.hidden = !visible; railEmpty.hidden = visible;
        if (!visible) drawer(false);
        const query = searchBox.value.trim();
        searchRow.hidden = conversations.conversations.length < 2 && !query;
        newChat.disabled = creating; newChat.hidden = conversations.supported !== true; tip(newChat, 'newChat', t, {why: creating ? t(...WHY.creating) : ''}); chatsDot.hidden = !conversations.conversations.some(row => row.running > 0);
        const keep = new Set(conversations.conversations.map(row => row.id));
        for (const [id, node] of listNodes) if (!keep.has(id)) { if (naming?.session === id) naming.finish(false); node.row.remove(); listNodes.delete(id); }
        // Chats making something come first, under In progress; the rest keep the server's order by day.
        const shown = [...conversations.conversations.filter(row => row.running > 0), ...conversations.conversations.filter(row => !(row.running > 0))];
        shown.forEach((row, index) => {
            let node = listNodes.get(row.id);
            if (!node) {
                const container = el('div', 'fv-chat-conversation-row');
                const button = press('fv-chat-conversation', null, () => chooseChat(row.id)), dot = el('i', 'fv-chat-running'), title = el('span');
                const words = el('span', 'fv-chat-conversation-words'), line = el('span', 'fv-chat-conversation-line'), when = el('small', 'fv-chat-conversation-when');
                const where = el('small', 'fv-chat-conversation-meta');
                line.append(title, when); words.append(line, where); tip(button, 'chat', t, {title: false});
                // Every row keeps the cover frame so titles line up whether or not a picture loads.
                const frame = el('i', 'fv-chat-cover');
                button.dataset.session = row.id; dot.setAttribute('aria-hidden', 'true'); frame.setAttribute('aria-hidden', 'true');
                button.append(dot, frame, words);
                const more = press('fv-chat-more', null, () => openMenu(row.id));
                more.title = t('More', '更多'); more.setAttribute('aria-haspopup', 'menu'); more.setAttribute('aria-controls', menu.id);
                more.append(glyph('M5 12h.01M12 12h.01M19 12h.01', 3.5));
                more.addEventListener('focusout', leaveMenu); container.append(button, more);
                listNodes.set(row.id, node = {row: container, button, dot, frame, title, when, where, more, cover: null, image: null});
                tip(more, 'more', t);
            }
            if (node.cover !== row.cover) {
                node.image?.remove(); node.image = null; node.cover = row.cover;
                delete node.row.dataset.coverError;
                if (row.cover) {
                    const image = el('img');
                    image.alt = ''; image.setAttribute('aria-hidden', 'true'); image.loading = 'lazy'; image.decoding = 'async';
                    image.width = 48; image.height = 27;
                    image.onerror = () => {
                        if (node.image !== image) return;
                        image.hidden = true; node.row.dataset.coverError = 'true';
                    };
                    node.image = image; image.src = row.cover; node.frame.append(image);
                }
            }
            node.row.dataset.temporary = String(!!row.temporary);
            markText(node.title, row.title, query); node.button.title = row.title; node.dot.hidden = row.running <= 0;
            // When it last moved, and whether something is being made in it.
            put(node.when, localTime(row.updated_at, t, new Date(), {short: true})); node.when.hidden = !node.when.textContent;
            // Where it stands: the chat being read says it from its own tasks; another says whether it is making something.
            const summary = row.id === state.session ? chatSummary : null;
            put(node.where, summary?.text || (row.running > 0 ? t('Making', '生成中') : ''));
            node.where.dataset.state = summary?.state || (row.running > 0 ? 'running' : ''); node.where.hidden = !node.where.textContent;
            // A search also finds the chat being read by its tasks' words; the line under the name then shows the one it found.
            const shot = query && row.id === state.session && !matches(query, row.title) ? shotWords.find(words => matches(query, words)) : null;
            if (shot) { markText(node.where, shot, query); node.where.dataset.state = 'found'; node.where.hidden = false; }
            // A chat on its way out stays hidden until the server answers; without an undo window its confirmation
            // stays open on the row until then.
            node.row.hidden = !matches(query, row.title) && !shot || undoing.has(row.id) || deleting.has(row.id) && menuState?.session !== row.id;
            const group = row.running > 0 ? 'running' : dayGroup(row.updated_at);
            node.row.dataset.group = group;
            node.row.dataset.groupLabel = t(...GROUP_WORDS[group]);
            node.more.setAttribute('aria-label', t(`More: ${row.title}`, `更多：${row.title}`));
            node.more.hidden = !canDeleteConversation(row) && !canRenameConversation(row) || naming?.session === row.id;
            if (row.id === conversations.current) node.button.setAttribute('aria-current', 'true'); else node.button.removeAttribute('aria-current');
            if (list.children[index] !== node.row) list.insertBefore(node.row, list.children[index] || null);
        });
        noMatch.hidden = !query || conversations.conversations.some(row => matches(query, row.title)) || shotWords.some(words => matches(query, words));
        // The first shown row of each day group carries the group's name above it.
        let lastGroup = null;
        for (const node of list.children) {
            if (node.hidden) continue;
            if (node.dataset.group !== lastGroup) node.dataset.groupStart = 'true'; else delete node.dataset.groupStart;
            lastGroup = node.dataset.group;
        }

        updateMenu();
        if (lostFocus && !menuState) (listNodes.get(previous.session)?.button || (visible ? newChat : input)).focus({preventScroll: true});
    }
    const renameFailure = error => error?.code === 'not_found' ? t('Could not rename: this chat no longer exists.', '没有重命名成功：这个对话已不存在。')
        : error?.code ? t('Could not rename.', '没有重命名成功。') : t('Could not rename: ', '没有重命名成功：') + t('Connection lost. Try again.', '连接中断，请重试。');
    // The row's name becomes a box: Enter keeps the new name, Escape leaves it as it was, an empty name gives back the first sentence.
    let naming = null;
    function startRename(session) {
        const node = listNodes.get(session), row = conversations.conversations.find(item => item.id === session);
        if (!node || !row || !canRenameConversation(row) || !active()) return;
        naming?.finish(false);
        newError.hidden = newIssue.hidden = true;
        const box = el('input', 'fv-chat-rename'); box.type = 'text'; box.maxLength = 60; box.value = row.title;
        box.setAttribute('aria-label', t('Chat name', '对话名称'));
        let over = false;
        const finish = async keep => {
            if (over) return; over = true; naming = null;
            const name = box.value.trim();
            box.remove(); node.button.hidden = false; node.more.hidden = false;
            if (!keep || name === row.title) { if (!keep) node.button.focus({preventScroll: true}); return; }
            try {
                const response = await dataLayer.rename(session, name);
                if (stopped) return;
                if (!response.ok) throw await requestError(response);
                void readConversations(true);
            } catch (error) {
                if (stopped) return;
                newError.textContent = renameFailure(error); newError.hidden = newIssue.hidden = false;
            }
            node.button.focus({preventScroll: true});
        };
        listenDialogKey(box, 'keydown', event => {
            if (event.key === 'Enter') { event.preventDefault(); void finish(true); }
            else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); void finish(false); }
        });
        box.addEventListener('blur', () => void finish(true));
        naming = {session, finish};
        node.button.hidden = true; node.more.hidden = true; node.row.prepend(box);
        box.focus(); box.select();
    }
    async function deleteChat(session, stop = false, again = false) {
        if (!again && (!menuState || deleting.has(session) || menuState.session !== session || menuState.stage !== 'confirm') || deleting.has(session) || !active()) return;
        deleteError.hidden = true;
        const ids = conversations.conversations.map(row => row.id), index = ids.indexOf(session);
        const neighbor = ids[index + 1] || ids[index - 1];
        deleting.add(session);
        // A disabled focused button can lose native focus. Keep it on the origin while awaiting the request.
        menuOrigin()?.focus({preventScroll: true}); updateMenu();
        try {
            // The pictures the chat names, read before it goes, are released with it (see releasePictures).
            const pictures = await chatPictures(session);
            const response = await dataLayer.remove(session, stop);
            if (stopped) return;
            const data = await response.json().catch(() => ({}));
            if (stopped) return;
            if (response.ok || response.status === 404 && data.error?.code === 'not_found') {
                void releasePictures(api, pictures);
                const current = conversations.current === session;
                unsaidSessions.delete(session); drafts.delete(session); pending = pending.filter(entry => entry.session !== session);
                if (current) { rememberSelection(null); draftSession = null; }
                deleted.add(session); conversations = removeConversation(conversations, session, data.here);
                if (menuState?.session === session) closeMenu();
                if (current) {
                    selectedSession = undefined; selection++; focusSession = null; away = null;
                    const agent = data.agent || state.agent;
                    resetChat(); state.agent = agent; input.value = ''; grow(); render(); report(); void poll();
                }
                renderConversations();
                (listNodes.get(neighbor)?.button || listNodes.values().next().value?.button || newChat).focus({preventScroll: true});
                void readConversations(true);
            } else if (response.status === 409) {
                if (menuState?.session === session) closeMenu();
                if (data.error?.code === 'not_connected') { state = {...state, agent: {connected: false, name: null}}; render(); report(); }
                else {
                    void readConversations(true);
                    if (stop) deleteFailed(session, t('Still stopping. Try deleting again in a moment.', '正在停止，请稍后再删除。'), false, false);
                    else deleteFailed(session, t('This chat still has work in progress, so it was not deleted.', '这段对话里还有在生成的内容，没有删除。'), true);
                }
            } else deleteFailed(session, t('Could not delete: ', '没有删除成功：') + (data.error?.message || data.error?.code || `HTTP ${response.status}`));
        } catch (error) { if (!stopped) deleteFailed(session, t('Could not delete: ', '没有删除成功：') + reason(error)); }
        finally {
            deleting.delete(session);
            if (menuState?.session === session) closeMenu();
            if (!stopped) renderConversations();
        }
    }
    function chooseChat(session, focus = false) {
        closeMenu(); say(''); deleteError.hidden = true;
        showDraft(session); rememberSelection(session);
        selectedSession = session; selection++; pinned = true; away = null;
        conversations = selectConversation(conversations, session); renderConversations();
        grow(); drawer(false);
        focusSession = focus ? session : null;
        if (focus && session === state.session) input.focus(); else if (narrow()) chats.focus();
        render(); void readConversations(true); void poll();
    }
    // `from` is the message whose "New chat" was clicked: a failure is also written there, where the reader is looking.
    async function startChat(from = null) {
        closeMenu();
        if (creating || !active() || conversations.supported !== true) return;
        creating = true; newError.hidden = newIssue.hidden = true; renderConversations(); render();
        try {
            const response = await dataLayer.create();
            if (stopped) return;
            if (response.ok) {
                const data = await response.json();
                if (typeof data.session !== 'string' || !data.session) throw new Error(t('The reply was incomplete. Try again.', '回复不完整，请重试。'));
                unsaidSessions.add(data.session); chooseChat(data.session, true);
            } else throw await requestError(response);
        } catch (error) {
            if (!stopped) {
                newError.textContent = t('Could not start a new chat: ', '没有新建成功：') + reason(error); newError.hidden = newIssue.hidden = false;
                if (from) from.error = newError.textContent;
            }
        }
        finally { creating = false; if (!stopped) { renderConversations(); render(); } }
    }
    async function readConversations(force = false) {
        if (!active() || conversations.supported === false) return;
        if (listBusy) { if (force) listAgain = true; return; }
        if (!force && performance.now() - listAt < listInterval) return;
        listBusy = true; listAt = performance.now();
        try {
            const response = await dataLayer.list({signal: closed.signal});
            if (stopped) return;
            // The body is awaited first: a conversation chosen meanwhile must stay the current one.
            const data = response.ok ? await response.json() : null;
            if (stopped) return;
            conversations = mergeConversations(conversations, data, response.status);
            // A list read started before a deletion must not put the deleted row back.
            conversations = {...conversations, conversations: conversations.conversations.filter(row => !deleted.has(row.id))};
            renderConversations();
            if (conversations.supported === false && selectedSession !== undefined) { selectedSession = undefined; rememberSelection(null); selection++; void poll(); }
        } catch { /* List failures do not interrupt reading or sending. */ }
        finally { listBusy = false; }
        if (listAgain) { listAgain = false; void readConversations(true); }
    }
    const outside = event => {
        if (menuState && !inMenu(event.target)) closeMenu();
        if (chats.getAttribute('aria-expanded') === 'true' && !rail.contains(event.target) && !chats.contains(event.target)) drawer(false, true);
        if (tasksToggle.getAttribute('aria-expanded') === 'true' && !side_.element.contains(event.target) && !tasksToggle.contains(event.target)) sideDrawer(false, true);
    };
    const keys = event => {
        // The name box owns its own Enter and Escape, so Escape here does not also close the drawer.
        if (String(event.target?.className || '').split(' ').includes('fv-chat-rename')) return;
        if (event.key === 'Escape' && menuState) {
            event.preventDefault(); event.stopImmediatePropagation(); closeMenu(true); return;
        }
        // Confirmation always cycles its two choices. In the drawer, the open menu owns Tab in both stages.
        if (menuState && (event.key === 'Tab' && (narrow() || menuState.stage === 'confirm')
            || menuState.stage === 'confirm' && ['ArrowLeft', 'ArrowRight'].includes(event.key))) {
            event.preventDefault(); event.stopPropagation();
            const buttons = (menuState.stage === 'confirm' ? [yes, cancel] : [rename, remove]).filter(button => !button.disabled && !button.hidden);
            const at = buttons.indexOf(document.activeElement), back = event.shiftKey || event.key === 'ArrowLeft';
            buttons[(at + (back ? -1 : 1) + buttons.length) % buttons.length]?.focus({preventScroll: true}); return;
        }
        if (tasksToggle.getAttribute('aria-expanded') === 'true' && event.key === 'Escape' && narrow()) { event.preventDefault(); event.stopPropagation(); sideDrawer(false, true); return; }
        if (chats.getAttribute('aria-expanded') !== 'true') return;
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); drawer(false, true); }
        else if (event.key === 'Tab' && narrow()) {
            const buttons = [...rail.querySelectorAll('button,input')].filter(button => !button.disabled && !button.hidden && !menu.contains(button) && !button.closest('[hidden]'));
            if (!buttons.length) return;
            if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons.at(-1).focus(); }
            else if (!event.shiftKey && document.activeElement === buttons.at(-1)) { event.preventDefault(); buttons[0].focus(); }
        }
    };
    const resize = () => { closeMenu(); if (!narrow()) { drawer(false); sideDrawer(false); } };
    const slash = event => {
        if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
        if (event.target?.closest?.('input,textarea,[contenteditable]') || sidebar.hidden) return;
        event.preventDefault(); if (narrow()) drawer(true); searchBox.focus();
    };
    const stopSlash = listenDialogKey(element, 'keydown', slash);
    const listScroll = () => closeMenu();
    list.addEventListener('scroll', listScroll);
    document.addEventListener('pointerdown', outside, true);
    // Through the dialog-key route: a modal beneath this one (the editor) takes document-level keys and hands them on only to local listeners.
    const stopMenuKeys = listenDialogKey(element, 'keydown', keys, true);
    window.addEventListener('resize', resize);
    // Whether the reader is at the newest entry. It holds while the conversation is out of sight, which forgets where it was scrolled.
    let pinned = true;
    const closed = new AbortController();
    const nodes = new Map(), answers = new Map(), stopping = new Set(), retrying = new Set(), confirming = new Set(), confirmErrors = new Map();
    const supersededWhiteboxes = new Set();
    let bulkStopping = false;
    const cardKey = (session, id) => JSON.stringify([session, id]);
    // The fraction each job shows: the estimate, never backwards, short of 100% until the video is saved.
    const levels = new Map();
    function fractionOf(job) {
        const p = job.progress || {}, {age_seconds: sent, ...sample} = p.estimate || {fraction: p.fraction};
        const key = JSON.stringify(sample), time = performance.now() / 1000;
        // A job that stopped or failed and is tried again starts from the beginning, not from where the last try got to.
        if (job.status !== 'running' && job.status !== 'succeeded') levels.delete(job.id);
        let row = levels.get(job.id);
        if (!row) levels.set(job.id, row = {key: null, at: 0, job: null, level: newLevel()});
        // remaining_seconds counts down between samples; the one that came with the sample is the one to move by.
        if (row.key !== key) { row.key = key; row.at = time - (sent > 0 ? sent : 0); row.job = job; }
        if (job.status === 'running') jobLevel(row.level, jobEstimate(row.job, time - row.at), row.job.progress?.remaining_seconds, time);
        return row.level.value;
    }
    const times = new Map();
    function leftOf(job) {
        if (job.status !== 'running') { times.delete(job.id); return null; }
        const row = jobLeft(times.get(job.id), job.progress?.remaining_seconds,
            job.progress?.estimate?.overall?.remaining_source, performance.now() / 1000);
        if (row) times.set(job.id, row);
        return row ? row.seconds : null;
    }
    const active = () => !stopped && element.isConnected && !document.hidden && shown();
    // A hidden page keeps reading, slowly, so that its title can tell what happened.
    const reading = () => !stopped && element.isConnected && shown();
    const inSelectedChat = () => !creating && (!selectedSession || selectedSession === state.session);
    const builtinScene = () => whiteboxPreview && state.agent.connected !== true;
    const composeAllowed = () => inSelectedChat() && (state.agent.connected === true || builtinScene() && !!state.session);
    const sendingAllowed = () => inSelectedChat() && canSend(state);

    const draftSending = () => pending.some(entry => entry.sending && entry.draft && entry.draft === drafts.get(draftSession));
    async function cancelJobs(ids) {
        if (!ids.length) { say(t('No tasks are running.', '当前没有在运行的任务')); return; }
        ids = ids.filter(id => !stopping.has(id));
        if (!ids.length) return;
        say(t('Stopping…', '正在停止…'));
        ids.forEach(id => stopping.add(id)); render(); report();
        const results = await Promise.all(ids.map(id => dataLayer.cancel(id)));
        results.forEach((response, index) => { if (!response?.ok) stopping.delete(ids[index]); });
        if (results.some(response => !response?.ok)) say(t('Could not stop. Click again; if it keeps failing, report the problem.', '没能停止。请再点一次；仍然失败时，请提交问题。'), true);
        else say('');
        if (!stopped) { render(); report(); void poll(); }
    }
    async function retryJob(key, text) {
        if (retrying.has(key) || !sendingAllowed()) return;
        retrying.add(key); render();
        try { await send(text); }
        finally { retrying.delete(key); if (!stopped) render(); }
    }
    // Why a card's job did not go on. A refusal turns the card to saying the words again; a lost connection keeps its button.
    const fallbacks = new Map();
    const againWay = (job, key) => fallbacks.get(key)?.resend ? 'resend' : job.again || 'resend';
    async function resumeJob(key, job) {
        if (retrying.has(key)) return;
        retrying.add(key); fallbacks.delete(key); render();
        const lost = {reason: t('Connection lost. Try again.', '连接中断，请重试。'), resend: false};
        try {
            const response = await dataLayer.resume(job.id);
            if (!response?.ok) fallbacks.set(key, response ? {reason: failureReason({error: await sendError(response)}, t), resend: true} : lost);
        } catch { fallbacks.set(key, lost); }
        finally { retrying.delete(key); if (!stopped) { render(); report(); void poll(); } }
    }
    function againJob(key, job, text) {
        if (againWay(job, key) === 'resend') { if (text) void retryJob(key, text); }
        else void resumeJob(key, job);
    }

    // ---- one entry of the conversation each ----
    function message(role) {
        const item = el('li', role === 'user' ? 'fv-chat-me' : 'fv-chat-ai'), text = el('p'); item.append(text);
        let status = null, error = null, retry = null, report = null, shown = '';
        let current = null, editRow = null, editButton = null, editor = null;
        // The newest sentence opens in place; the box at the bottom keeps its draft.
        function openEditor() {
            if (editor || !current) return;
            const box = el('textarea', 'fv-chat-edit-box'), note = el('span', 'fv-chat-error');
            const actions = el('div', 'fv-chat-actions fv-chat-edit-actions');
            const cancel = tip(press('fv-chat-edit-cancel', t('Cancel', '取消'), () => closeEditor()), 'editCancel', t);
            const go = tip(press('fv-chat-edit-send', t('Send', '发送'), () => void submit()), 'editSend', t);
            note.setAttribute('role', 'alert'); note.hidden = true;
            box.value = current.text; box.rows = 1; box.setAttribute('aria-label', t('Edit message', '编辑消息'));
            actions.append(note, cancel, go);
            editor = {box, actions, note, cancel, go, target: current, id: newId(), busy: false};
            const fit = () => { box.style.height = 'auto'; box.style.height = `${box.scrollHeight + 2}px`; go.disabled = editor.busy || !box.value.trim(); };
            box.addEventListener('input', fit);
            // The too-long line goes as soon as the words fit.
            box.addEventListener('input', () => { if (note.textContent === TOO_LONG() && !messageTooLong(box.value.trim())) note.hidden = true; });
            listenDialogKey(box, 'keydown', event => {
                if (event.key === 'Escape' && !editor.busy) { event.preventDefault(); event.stopPropagation(); closeEditor(); return; }
                if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
                event.preventDefault(); void submit();
            });
            item.dataset.editing = 'true'; text.hidden = true; editRow.hidden = true;
            text.after(box, actions);
            fit(); box.focus(); box.setSelectionRange?.(box.value.length, box.value.length);
        }
        function closeEditor() {
            if (!editor) return;
            editor.box.remove(); editor.actions.remove(); editor = null;
            delete item.dataset.editing; text.hidden = false; if (editRow) editRow.hidden = false;
            if (current) text.textContent = current.text;
        }
        async function submit() {
            const words = editor?.box.value.trim();
            if (!words || editor.busy) return;
            const open = editor;
            if (messageTooLong(words)) { editor.note.textContent = TOO_LONG(); editor.note.hidden = false; return; }
            open.busy = open.cancel.disabled = open.go.disabled = true; open.note.hidden = true;
            const problem = await rewrite(open.target, words, open.id);
            if (editor !== open) return;
            open.busy = open.cancel.disabled = false; open.go.disabled = false;
            if (problem) { open.note.textContent = problem; open.note.hidden = false; open.box.focus(); }
            else { current = {...current, text: words}; closeEditor(); }
        }
        return {element: item, update(entry, {editable = false} = {}) {
            current = entry;
            if (!editor) { put(text, entry.text); text.hidden = !entry.text && !!entry.attachments?.length; }
            const names = (entry.attachments || []).map(picture => picture.name).join('|');
            if (names !== shown) {
                shown = names; item.querySelector('.fv-chat-refs')?.remove();
                if (names) {
                    const row = el('ul', 'fv-chat-refs');
                    for (const picture of entry.attachments) { const li = el('li'); li.append(thumbnail(picture.preview || attachmentURL(api, picture.name))); row.append(li); }
                    item.prepend(row);
                }
            }
            if (role !== 'user') return;
            if ((editable || editor) && !editRow) {
                editRow = el('div', 'fv-chat-edit-row'); editButton = press('fv-chat-edit', t('Edit', '编辑'), openEditor);
                editRow.append(editButton); item.append(editRow);
            } else if (!editable && !editor && editRow) { editRow.remove(); editRow = editButton = null; }
            // A rewrite the server has not shown yet has no number to rewrite.
            if (editButton) {
                editButton.disabled = !composeAllowed() || !Number.isFinite(entry.seq);
                tip(editButton, 'editMessage', t, {why: !editButton.disabled ? '' : t(...(!composeAllowed() ? WHY.notConnected : WHY.sending))});
            }
            item.dataset.taken = String(entry.taken !== false);
            item.dataset.failed = String(!!entry.error);
            if (entry.error || entry.sending && entry.retried) {
                if (!status) {
                    status = el('div', 'fv-chat-actions');
                    error = el('span', 'fv-chat-error'); error.setAttribute('role', 'alert');
                    retry = press('fv-chat-retry', t('Retry', '重试'), () => {
                        const original = pending.find(value => value.id === entry.id);
                        if (entry.errorCode === 'session_full') { void startChat(original || entry); return; }
                        if (original) void send(original.text, original);
                    });
                    report = issueButton(t, () => {
                        const live = pending.find(value => value.id === entry.id) || entry;
                        return {where: CHAT_ISSUE, message: error.textContent, kind: live.failureKind || null, facts: live.failureFacts || null};
                    });
                    status.append(error, retry, report); item.append(status);
                }
                quoted(error, entry.error || ''); error.hidden = !entry.error;
                // Waiting for the agent to come back, or the user editing a queued message, is not a fault.
                const calm = !!entry.automatic || entry.errorCode === 'edited';
                report.hidden = !entry.error || calm;
                error.classList.toggle('fv-chat-calm', calm); error.setAttribute('role', calm ? 'status' : 'alert');
                const full = entry.errorCode === 'session_full';
                retry.textContent = full ? t('New chat', '新对话') : t('Retry', '重试');
                retry.disabled = entry.sending || (full ? creating : !sendingAllowed());
                tip(retry, full ? 'newChatError' : 'retry', t, {why: !retry.disabled ? '' : t(...(entry.sending ? WHY.sending : full ? WHY.creating : state.agent.connected === true ? WHY.notListening : WHY.notConnected))});
            } else if (status) { status.remove(); status = null; }
        }};
    }
    function preview(job) {
        const box = el('div', 'fv-chat-preview');
        const shape = (w, h) => {
            if (!(w > 0 && h > 0)) return;
            box.style.aspectRatio = `${w} / ${h}`; box.dataset.shape = w > h * 1.1 ? 'wide' : 'tall';
            if (job.kind === 'image') box.style.setProperty('--fv-chat-ratio', w / h);
        };
        shape(job.width, job.height);
        return {box, shape};
    }
    // A card's heading: its name with where it is, the prompt it was made from, then its size, length and time.
    function cardHead(job, words) {
        const head = el('div', 'fv-chat-card-head'), top = el('div', 'fv-chat-title-row'), name = el('h3'), chip = el('span', 'fv-chat-chip');
        const prompt = el('div', 'fv-chat-prompt fv-copyable'), facts = el('div', 'fv-chat-facts-row'), meta = el('p', 'fv-chat-meta'), when = el('span', 'fv-chat-when');
        top.append(name, chip); facts.append(meta, when); head.append(top, prompt, facts);
        const draw = (next, fraction = null) => {
            put(chip, statusText(next, t, fraction)); chip.dataset.state = statusKey(next);
            const said = words(); put(prompt, said); prompt.hidden = !said;
            prompt.title = said ? t('Prompt', '提示词') : '';
            put(when, localTime(statusKey(next) === 'done' && next.finished_at ? next.finished_at : next.created_at, t)); when.hidden = !when.textContent;
        };
        return {head, name, meta, chip, draw};
    }
    function working(item, job, words = () => '') {
        const {box} = preview(job); box.append(emptyFrameIcon());
        const info = el('div', 'fv-chat-info'), card = cardHead(job, words), head = card.head, name = card.name, meta = card.meta;
        const middle = el('div', 'fv-chat-where'), parts = el('div', 'fv-chat-parts'), line = el('p', 'fv-chat-line'), part = el('span'), step = el('span'); line.append(part, step);
        middle.append(parts, line);
        const foot = el('div', 'fv-chat-actions'), left = el('span', 'fv-chat-left');
        const stop = press('fv-chat-stop', null, () => void cancelJobs([job.id])); tip(stop, 'stop', t);
        foot.append(left, stop); info.append(head, middle, foot); item.replaceChildren(box, info);
        const said = {};
        // Three minutes without a new step or stage: the card says so, once, in the line that names the stage.
        let signature = null, changed = performance.now(), quiet = false;
        const stalled = next => {
            const key = progressKey(next);
            if (key !== signature) { signature = key; changed = performance.now(); }
            return next.status === 'running' && performance.now() - changed >= STALL_MS;
        };
        // The page header carries the one overall percentage; the card keeps the parts and the time left.
        const level = next => {
            const seconds = leftOf(next);
            left.textContent = seconds === null ? '' : about(seconds, t);
        };
        let current = job, draw = null;
        item.tick = () => { current = state.jobs[job.id] || job; level(current); card.draw(current, current.status === 'running' ? fractionOf(current) : null); if (stalled(current) !== quiet) draw?.(current); };
        return draw = next => {
            const cells = jobParts(next);
            level(next);
            put(name, jobName(next, t)); put(meta, jobMeta(next, t)); meta.hidden = !meta.textContent;
            card.draw(next, next.status === 'running' ? fractionOf(next) : null);
            while (parts.children.length > cells.length) parts.lastChild.remove();
            while (parts.children.length < cells.length) parts.append(el('i'));
            cells.forEach((value, index) => parts.children[index].style.setProperty('--w', `${value * 100}%`));
            const shares = cells.length ? partShares(next) : null;
            parts.style.gridTemplateColumns = shares ? shares.map(n => `minmax(0,${n}fr)`).join(' ') : '';
            parts.hidden = !cells.length;
            const block = next.progress?.block;
            const where = cardLine(said, next, t);
            quiet = stalled(next);
            const waiting = t('Progress has not updated recently; generation is still running.', '进度暂未更新，仍在生成中。');
            put(part, cells.length ? t(`Part ${block.index} / ${block.total}`, `第 ${block.index} / ${block.total} 段`) : quiet ? waiting : where);
            put(step, cells.length ? quiet ? waiting : where : '');
            const going = stopping.has(next.id) || next.status === 'cancelling';
            stop.disabled = going; stop.textContent = going ? t('Stopping…', '正在停止…') : t('Stop', '停止');
            stop.setAttribute('aria-busy', String(going));
            stop.setAttribute('aria-label', t('Stop ', '停止 ') + jobName(next, t)); tip(stop, 'stop', t, {why: going ? t(...WHY.stopping) : ''});
        };
    }
    function done(item, job, words = () => '') {
        const info = el('div', 'fv-chat-info'), card = cardHead(job, words), head = card.head, name = card.name, meta = card.meta;
        const foot = el('div', 'fv-chat-actions');
        const picture = job.kind === 'image';
        const show = press('fv-chat-open', t('Open', '打开'), () => {
            if (!picture) return open(job);
            dismissImage?.();
            dismissImage = imageViewer(t, api.apiURL(job.thumbnail), name.textContent, show, () => { show.disabled = true; });
        });
        const cut = press('fv-chat-edit', t('Edit', '剪辑'), () => edit(job));
        const save = press('fv-chat-save', t('Download', '下载'), () => saveImage(api.apiURL(job.thumbnail)));
        tip(show, picture ? 'openPicture' : 'open', t); tip(cut, 'edit', t); tip(save, 'download', t);
        if (picture && job.thumbnail) { show.disabled = save.disabled = true; foot.append(show, save); }
        else if (!picture && job.work) foot.append(show, cut);
        info.append(head); if (foot.childElementCount) info.append(foot);
        const children = [info];
        if (job.thumbnail || job.play) {
            const {box, shape} = preview(job), length = el('span', 'fv-chat-length', job.seconds > 0 ? clock(job.seconds) : '');
            if (job.thumbnail) {
                const image = el('img'); image.alt = '';
                const viewable = on => { show.disabled = save.disabled = !on; if (on) box.dataset.open = 'true'; else delete box.dataset.open; };
                image.onload = () => { if (!box.style.aspectRatio) shape(image.naturalWidth, image.naturalHeight); if (picture) viewable(true); };
                if (picture) { image.onerror = () => viewable(false); box.onclick = () => { if (!show.disabled) show.click(); }; }
                image.src = api.apiURL(job.thumbnail); box.append(image);
            }
            if (job.play) {
                // The video plays inside its frame, with its sound; the line under it follows and can be dragged.
                let video = null;
                let playing = false, loadRetried = false;
                const playbackFailure = () => {
                    if (item.dataset.mode !== 'done') return;
                    playback.textContent = t('Cannot play right now. Open it in Creations.', '暂时播放不了，可在作品库中打开'); playback.hidden = false;
                    show.textContent = t('Open in Creations', '在作品库打开'); if (!show.parentNode) foot.append(show);
                    if (!foot.parentNode) info.append(foot);
                };
                const playback = el('p', 'fv-chat-playback'); playback.hidden = true; info.append(playback);
                const play = async () => {
                    if (playing) return;
                    playing = true; box.dataset.loading = 'true';
                    try { await video.play(); playback.hidden = true; }
                    catch {
                        if (loadRetried) playbackFailure();
                        else try { loadRetried = true; video.load(); await video.play(); playback.hidden = true; } catch { playbackFailure(); }
                    }
                    finally { playing = false; delete box.dataset.loading; }
                };
                const toggle = press('fv-chat-play', null, () => {
                    if (!video) {
                        video = el('video'); video.playsInline = true; video.src = api.apiURL(job.play);
                        const seek = el('input', 'fv-chat-seek'); seek.type = 'range'; seek.min = '0'; seek.max = '1000'; seek.value = '0'; tip(seek, 'seek', t);
                        seek.setAttribute('aria-label', t('Position', '播放位置'));
                        video.onloadedmetadata = () => shape(video.videoWidth, video.videoHeight);
                        video.ontimeupdate = () => { if (video.duration) seek.value = String(Math.round(video.currentTime / video.duration * 1000)); };
                        video.onplay = video.onpause = video.onended = () => { box.dataset.playing = String(!video.paused && !video.ended); label(); };
                        video.onerror = () => {
                            if (playing) return;
                            if (loadRetried) { playbackFailure(); return; }
                            loadRetried = true; video.load(); void play();
                        };
                        seek.oninput = () => { if (video.duration) video.currentTime = seek.value / 1000 * video.duration; };
                        box.prepend(video); box.append(seek); box.dataset.video = 'true';
                    }
                    if (video.paused || video.ended) { loadRetried = false; void play(); } else video.pause();
                });
                tip(toggle, 'play', t);
                const label = () => toggle.setAttribute('aria-label', (box.dataset.playing === 'true' ? t('Pause ', '暂停 ') : t('Play ', '播放 ')) + jobName(job, t));
                label(); box.append(toggle);
            }
            if (length.textContent) box.append(length);
            children.unshift(box);
        }
        item.replaceChildren(...children);
        return (next, latest) => {
            put(name, jobName(next, t)); put(meta, jobMeta(next, t)); meta.hidden = !meta.textContent; card.draw(next);
            if (picture) { show.setAttribute('aria-label', t('Open ', '打开 ') + jobName(next, t)); save.setAttribute('aria-label', t('Download ', '下载 ') + jobName(next, t)); }
            show.classList.toggle('fv-primary', latest);
        };
    }
    function jobCard() {
        const item = el('li', 'fv-chat-job');
        let mode = null, apply = null, retry = null, said = '';
        const words = () => said;
        // A card picks its task in the right panel; its own buttons and the player keep their clicks.
        item.addEventListener('click', event => { if (!event.target?.closest?.('button,input,a,video') && item.dataset.job) { side_?.select(item.dataset.job); drawStage(); } });
        // The card's one button and, on its row, why the last press did not go on.
        const again = () => [el('span', 'fv-chat-error fv-chat-again-note'), press('fv-chat-retry', null, () => retry?.())];
        return {element: item, update(job, {latest, retry: action, key, text}) {
            // The sentence that asked for it is the message just above; a card repeats only the engine's own prompt.
            retry = action; said = promptOf(job, text).source === 'job' ? promptOf(job, text).text : '';
            // Jobs without a picture (a check, a prompt) are one quiet line while they run, and nothing after.
            // A picture takes seconds: the same quiet line while it is made, then the picture.
            const card = VIDEOS.includes(job.kind || 'generate') || (['image', 'production', 'cast'].includes(job.kind) && !WORKING.includes(job.status));
            // What the user stopped says so; a video with finished parts can go on from there.
            const byUser = jobMode(job) === 'stopped' && job.cancelled_by === 'user' && VIDEOS.includes(job.kind || 'generate');
            const next = card ? jobMode(job) + (jobMode(job) === 'done' ? `:${job.play}:${job.thumbnail}:${job.work}`
                : jobMode(job) === 'failed' && job.kind === 'image' ? `:${job.error?.code}:${job.error?.report_id}:${job.error?.memory || ''}:${job.error?.stage || ''}`
                : byUser ? `:user:${retry && job.again === 'continue' ? 'continue' : ''}` : '')
                : WORKING.includes(job.status) ? 'quiet' : 'none';
            if (next !== mode) {
                mode = next; item.dataset.job = job.id; item.dataset.mode = next.split(':')[0]; item.hidden = next === 'none';
                if (job.kind === 'image') item.dataset.kind = 'image'; else delete item.dataset.kind;
                if (!WORKING.includes(job.status)) stopping.delete(job.id);
                if (next === 'working') apply = working(item, job, words);
                else if (next.startsWith('done')) apply = done(item, job, words);
                else if (next === 'quiet') {
                    const label = el('span'); item.replaceChildren(el('span', 'fv-chat-dots'), label);
                    apply = next => { label.textContent = stopping.has(next.id) || next.status === 'cancelling'
                        ? t('Stopping', '正在停止') : t(...(QUIET[next.kind] || BUSY[next.kind] || ['Working', '进行中'])); };
                }
                else if (next === 'stopped:user:continue') {
                    // The failed card's place and button, without a report: nothing went wrong.
                    const actions = el('div', 'fv-chat-actions fv-actions'), links = el('div', 'fv-result-links');
                    actions.append(...again()); links.append(actions);
                    item.replaceChildren(el('p', null, t('Stopped', '已停止')), links); apply = null;
                }
                else if (next === 'stopped:user:' || next === 'stopped' && (!job.error || job.kind === 'image')) { item.replaceChildren(el('span', null, t('Stopped', '已停止'))); apply = null; }
                else if (next.startsWith('failed') && job.kind === 'image') {
                    // Picture failures name their step with the picture's own report card.
                    // "Send again" leads the card's own action row; its label and state are set below with the others.
                    const host = el('div');
                    const card = renderImageFailure(host, {error: job.error?.code || 'generation_failed', report_id: job.error?.report_id,
                        stage: job.error?.stage, step: job.error?.step || (job.error?.code ? '' : 'agent_job'), memory: job.error?.memory}, t,
                        {productVersion, fetchApi: (path, options) => api.fetchApi(path, options),
                            retry: retry ? {label: AGAIN.resend, onClick: () => retry?.()} : null});
                    item.replaceChildren(host);
                    // Without a diagnostic report the shared card folds the code, step and memory under its own Technical
                    // details; the program's text and request id join that fold. With a report, the chat's fold (as on a
                    // video failure card) sits between the reason and the buttons.
                    const facts = card?.querySelector('.fv-image-failure-details');
                    const raw = [job.error?.code, job.error?.message, job.error?.request_id].filter(Boolean).join('\n');
                    if (facts) for (const line of [job.error?.message, job.error?.request_id].filter(Boolean)) facts.append(el('div', null, line));
                    else if (card && raw) card.querySelector('.fv-image-failure-reason').after(failureFold(t, raw));
                    if (retry && card) {
                        // In the chat it is the same outlined button as on a video failure card.
                        const again = card.querySelector('.fv-image-retry');
                        again?.classList.remove('fv-action-primary');
                        again?.classList.add('fv-chat-retry');
                        card.append(el('span', 'fv-chat-error fv-chat-again-note'));
                    }
                    apply = null;
                }
                else if (next === 'failed' || next === 'stopped') {
                    const reason = el('p'), actions = el('div', 'fv-chat-actions fv-actions'), links = el('div', 'fv-result-links');
                    // The failure card's head: what did not finish, and when. The reason follows in its own line.
                    const failHead = el('div', 'fv-chat-fail-head'), failName = el('strong'), failChip = el('span', 'fv-chat-chip'), failWhen = el('span', 'fv-chat-when');
                    const mark = el('span', 'fv-chat-fail-mark'); mark.append(glyph(ICON.warn, 2));
                    failHead.append(mark, failName, failChip, failWhen);
                    let current = job;
                    const diagnostic = createDiagnosticReportControl(t, {api, tip: true, version: productVersion,
                        label: () => t('Download report', '下载报告'),
                        fallback: () => new Blob([JSON.stringify(createErrorReport({error: current.error, exception_type: current.error?.code,
                            details: current.error?.request_id}, {stage: 'execution'}), null, 2)], {type: 'application/json'})});
                    // The shared failure style: one reason line, the fold row, then the secondary buttons.
                    const fold = failureFold(t);
                    if (retry) actions.append(...again());
                    // An unexpected failure says to report it if it happens again: the card carries that button.
                    const issue = issueButton(t, () => ({where: CHAT_ISSUE, message: [reason.textContent, fold.querySelector('p').textContent].filter(Boolean).join('\n')}));
                    actions.append(diagnostic.report, issue);
                    links.append(actions); item.replaceChildren(failHead, reason, fold, links);
                    tip(diagnostic.report, 'report', t);
                    apply = next => { current = next; put(reason, failureCardReason(next, t));
                        issue.hidden = !UNEXPECTED_FAILURES.includes(next.error?.code);
                        put(failName, jobName(next, t)); put(failChip, statusText(next, t)); failChip.dataset.state = statusKey(next);
                        put(failWhen, localTime(next.finished_at || next.created_at, t)); failWhen.hidden = !failWhen.textContent;
                        const raw = [next.error?.code, next.error?.message, next.error?.request_id].filter(Boolean).join('\n');
                        fold.querySelector('p').textContent = raw; fold.hidden = !raw;
                        diagnostic.updateReport({report_id: next.report_id}); };
                } else { item.replaceChildren(); apply = null; }
            }
            apply?.(job, latest);
            if (WORKING.includes(job.status)) fallbacks.delete(key);
            const button = item.querySelector('.fv-chat-retry');
            if (button) {
                const way = againWay(job, key), note = item.querySelector('.fv-chat-again-note');
                button.textContent = t(...AGAIN[way]); tip(button, way, t);
                button.hidden = way === 'resend' && !text;
                // Going on needs no agent; saying the words again does.
                button.disabled = retrying.has(key) || !inSelectedChat() || way === 'resend' && !sendingAllowed();
                if (note) { put(note, fallbacks.get(key)?.reason || ''); note.hidden = !note.textContent; }
            }
        }};
    }
    function whiteboxState(entry) {
        const key = cardKey(state.session, entry.id);
        const value = answers.get(key) || state.confirms[entry.id] || entry.state || 'asked';
        if (value !== 'asked') return value;
        // A version is opaque. Timeline order replaces previews of the same work;
        // a later user message also retires the preview while its replacement is made.
        const replaced = [...state.items.values()].some(next => next.seq > entry.seq && (
            next.type === 'message' && next.role === 'user'
            || next.type === 'confirm' && next.whitebox && next.whitebox.work === entry.whitebox.work));
        return supersededWhiteboxes.has(key) || replaced ? 'superseded' : 'asked';
    }
    function whiteboxCard(item) {
        item.classList.add('fv-chat-whitebox-card');
        const preview = el('div', 'fv-whitebox-preview'), video = el('video');
        const unavailable = el('p', 'fv-chat-meta', t(...WHITEBOX.unavailable)); unavailable.hidden = true;
        video.muted = true; video.loop = true; video.playsInline = true; video.preload = 'metadata';
        video.disablePictureInPicture = true; video.setAttribute('aria-label', t(...WHITEBOX.preview));
        let entry, session, key, value, source = null, disabled = true;
        const play = press('fv-chat-play', '', () => {
            if (value === 'superseded') return;
            if (video.paused) void video.play().catch(() => {}); else video.pause();
        });
        const playing = () => {
            if (value === 'superseded' && !video.paused) video.pause();
            preview.dataset.playing = String(!video.paused);
            play.setAttribute('aria-label', t(...WHITEBOX[video.paused ? 'play' : 'pause']));
        };
        video.onplay = video.onpause = playing;
        const failed = () => { video.pause(); video.hidden = play.hidden = true; unavailable.hidden = false; };
        video.onerror = failed;
        preview.append(video, play, unavailable);
        const summary = el('p', 'fv-whitebox-summary'), seconds = el('p', 'fv-chat-meta');
        const buttons = el('div', 'fv-chat-actions'), status = el('p', 'fv-whitebox-status');
        status.setAttribute('role', 'status');
        const error = el('p', 'fv-chat-error'); error.setAttribute('role', 'alert');
        const write = (node, text) => { if (node.textContent !== text) node.textContent = text; node.hidden = !text; };
        const answer = (approve, allowEmpty = false) => async () => {
            if (disabled || confirming.has(key) || !inSelectedChat() || state.session !== session || whiteboxState(entry) !== 'asked') return;
            // A card with no description at all: say so, and start only when the user chooses to go on.
            if (approve && !allowEmpty && !(entry.whitebox.summary || entry.whitebox.scene_summary || '').trim()) {
                const notice = showEmptyPrompt({t, anchor: buttons, field: input, onContinue: answer(true, true)});
                // Where Skip has wrapped to its own row, the notice sits right under the button row, before Skip.
                const skip = buttons.querySelector('.fv-chat-no'), first = buttons.firstElementChild;
                if (skip && first !== skip && skip.offsetTop > first.offsetTop + 4) { notice.classList.add('fv-empty-prompt-inline'); skip.before(notice); }
                return;
            }
            const requestKey = key, requestSession = session, id = entry.id;
            confirming.add(requestKey); confirmErrors.delete(requestKey); render();
            try {
                const response = await agentPost(api, '/freevideo/agent/chat/confirm', {id, approve, session: requestSession});
                if (!response.ok) throw await requestError(response);
                answers.set(requestKey, (await response.json()).state || (approve ? 'approved' : 'declined'));
            } catch (failure) { confirmErrors.set(requestKey, t(...WHITEBOX.failure) + reason(failure)); }
            finally { confirming.delete(requestKey); }
            if (!stopped) { render(); void poll(); }
        };
        buttons.append(tip(press('fv-chat-yes fv-primary', t(...WHITEBOX.generate), answer(true)), 'whiteboxGo', t),
            press('fv-whitebox-edit', t(...WHITEBOX.edit), () => {
                if (disabled || whiteboxState(entry) !== 'asked') return;
                // The summary goes into an empty box with the cursor after it; what the user has already typed stays.
                const text = entry.whitebox.summary || entry.whitebox.scene_summary || '';
                if (text && !input.value.trim()) { input.value = text; input.dispatchEvent(new Event('input')); }
                input.focus();
                input.setSelectionRange(input.value.length, input.value.length);
                input.scrollTop = input.scrollHeight;
            }), tip(press('fv-chat-no fv-quiet', t(...WHITEBOX.decline), answer(false)), 'whiteboxSkip', t));
        tip(buttons.querySelector('.fv-whitebox-edit'), 'whiteboxEdit', t);
        item.replaceChildren(preview, summary, seconds, buttons, status, error);
        return {update(next) {
            entry = next; session = state.session; key = cardKey(session, entry.id);
            if (sceneWaiting) { sceneWaiting = false; say(''); }
            const previous = value; value = whiteboxState(entry);
            item.dataset.state = value;
            disabled = value !== 'asked' || confirming.has(key) || !inSelectedChat() || state.agent.connected !== true;
            item.setAttribute('aria-busy', String(confirming.has(key)));
            buttons.hidden = value !== 'asked'; buttons.querySelectorAll('button').forEach(button => { button.disabled = disabled; });
            write(status, value === 'asked' ? '' : t(...(WHITEBOX[value] || WHITEBOX.superseded)));
            write(summary, entry.whitebox.summary || entry.whitebox.scene_summary || '');
            write(seconds, Number(entry.whitebox.seconds) > 0 ? `${entry.whitebox.seconds} ${t(...WHITEBOX.seconds)}` : '');
            write(error, value === 'asked' ? confirmErrors.get(key) || '' : '');
            const url = entry.whitebox.video_url || (entry.whitebox.work ? api.apiURL('/freevideo/agent/chat/video?id=' + encodeURIComponent(entry.whitebox.work)) : '');
            if (url !== source) {
                source = url; video.pause(); video.hidden = play.hidden = !url; unavailable.hidden = !!url;
                video.autoplay = value === 'asked';
                if (url) video.src = url; else { video.removeAttribute('src'); video.load(); }
            }
            if (value !== 'asked') {
                video.autoplay = false;
                if (previous !== value) video.pause();
            }
            play.hidden = video.hidden || value === 'superseded';
            playing();
        }};
    }
    // The outline of a film about to be shot, one line a beat; past eight the rest fold under one line.
    const BEATS_SHOWN = 8;
    function confirmCard() {
        const item = el('li', 'fv-chat-confirm');
        let shownState = null;
        let whitebox = null;
        let allBeats = false;
        return {element: item, update(entry) {
            if (entry.whitebox) { whitebox ||= whiteboxCard(item); whitebox.update(entry); return; }
            const session = state.session, key = cardKey(session, entry.id);
            const value = answers.get(key) || state.confirms[entry.id] || entry.state || 'asked';
            const disabled = confirming.has(key) || !inSelectedChat() || state.agent.connected !== true, error = confirmErrors.get(key) || '';
            const signature = JSON.stringify([value, disabled, error, allBeats]);
            if (signature === shownState) return;
            shownState = signature; item.dataset.state = value === 'asked' ? 'asked' : 'answered';
            if (value !== 'asked') { item.replaceChildren(el('span', null, value === 'approved' ? t('Started', '已开始') : t('Not now', '先不拍'))); return; }
            const answer = approve => async () => {
                if (confirming.has(key) || !inSelectedChat() || state.agent.connected !== true || state.session !== session) return;
                item.querySelectorAll('button').forEach(button => { button.disabled = true; });
                confirming.add(key); confirmErrors.delete(key); render();
                try {
                    const response = await dataLayer.confirm({id: entry.id, approve, session});
                    if (!response.ok) throw await requestError(response);
                    answers.set(key, (await response.json()).state || (approve ? 'approved' : 'declined'));
                } catch (error) { confirmErrors.set(key, t('Could not start shooting: ', '没有开始拍摄：') + reason(error)); }
                finally { confirming.delete(key); }
                if (!stopped) { render(); void poll(); }
            };
            const buttons = el('div', 'fv-chat-actions');
            buttons.append(tip(press('fv-chat-yes fv-primary', t('Start', '开始'), answer(true)), 'start', t), tip(press('fv-chat-no', t('Not now', '先不'), answer(false)), 'notNow', t));
            const why = !disabled ? '' : t(...(confirming.has(key) ? WHY.sending : !inSelectedChat() ? WHY.loading : WHY.notConnected));
            buttons.querySelectorAll('button').forEach(button => { button.disabled = disabled; if (why) button.dataset.fvTipWhy = why; });
            const question = el('p', null, entry.question);
            const estimate = el('p', 'fv-chat-meta', entry.estimate_seconds > 0 ? about(entry.estimate_seconds, t) : ''); estimate.hidden = !estimate.textContent;
            // What is about to be made, written as on the job card; a question that carries no size or length shows only the time.
            const shape = el('p', 'fv-chat-meta', jobMeta(entry, t)); shape.hidden = !shape.textContent;
            const beats = Array.isArray(entry.beats) ? entry.beats : [];
            const outline = el('ol', 'fv-chat-beats');
            const folded = beats.length > BEATS_SHOWN && !allBeats;
            for (const beat of folded ? beats.slice(0, BEATS_SHOWN - 1) : beats) outline.append(el('li', null, beat));
            if (folded) {
                const rest = beats.length - BEATS_SHOWN + 1;
                const more = el('li', 'fv-chat-beats-more');
                more.append(tip(press('fv-chat-link', t(`${rest} more`, `另外 ${rest} 拍`), () => { allBeats = true; shownState = null; render(); }), 'more8', t));
                outline.append(more);
            }
            outline.hidden = !beats.length;
            item.replaceChildren(question, shape, estimate, outline, buttons);
            if (error) {
                const notice = el('p', 'fv-chat-error', error); notice.setAttribute('role', 'alert');
                item.append(notice, issueButton(t, () => ({where: CHAT_ISSUE, message: error})));
            }
        }};
    }
    // Before anything is said: what the agent can do, and example sentences. A click puts one in the box to change and
    // send; it is never sent by itself.
    function hello() {
        const item = el('li', 'fv-chat-hello'), mark = el('span', 'fv-chat-hello-mark');
        mark.setAttribute('aria-hidden', 'true'); mark.append(glyph('M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM19 15l.8 2.2 2.2.8-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z', 1.7));
        item.append(mark, el('p', null, t('I can make videos for you,', '我可以为您生成视频，')), el('p', null, t('or turn a story into a short film.', '也可以把一个故事拍成短片。')));
        return {element: item, update() {}};
    }
    function examples() {
        const item = el('li', 'fv-chat-examples'), list = el('div');
        const lines = [t('Make a 10-second video: a rainy night, a girl squeezes onto the last bus', '生成一段 10 秒的视频：雨夜，女孩挤上末班车'),
            t('Make a 1-minute vertical short film: a day in a coffee shop', '做一条 1 分钟的竖屏短片：咖啡店的一天')];
        const fill = line => { input.value = line; input.dispatchEvent(new Event('input')); input.focus(); input.setSelectionRange?.(line.length, line.length); };
        list.append(...lines.map(line => { const b = tip(press(null, line, () => fill(line)), 'example', t); b.prepend(glyph('M4 5h16v11H9l-5 4z', 1.8)); return b; }));
        item.append(el('p', 'fv-chat-meta', t('Try saying:', '可以这样说：')), list);
        return {element: item, update() { list.querySelectorAll('button').forEach(button => { button.disabled = !composeAllowed();
            tip(button, 'example', t, {why: button.disabled ? t(...WHY.notConnected) : ''}); }); }};
    }
    function dots() {
        const item = el('li', 'fv-chat-thinking'), mark = el('span', 'fv-chat-dots'); mark.append(el('i'), el('i'), el('i'));
        const activity = el('span', 'fv-chat-activity');
        item.setAttribute('role', 'status'); item.setAttribute('aria-label', t('Thinking', '正在思考')); item.append(mark, activity);
        let timer = null;
        const show = text => { activity.textContent = text || ''; activity.hidden = !text; };
        // A waiting line counts on its own; it stops once the dots leave the page or say something else.
        const count = since => {
            show(waitingText(Math.max(0, Math.floor((Date.now() - since) / 1000)), t));
            timer = item.isConnected || timer === null ? setTimeout(() => count(since), 500) : null;
        };
        return {element: item, update(text, {since = null} = {}) {
            clearTimeout(timer); timer = null;
            if (text == null && since != null) count(since); else show(text);
        }};
    }
    const MAKE = {hello, examples, dots, user: () => message('user'), agent: () => message('agent'), job: jobCard, confirm: confirmCard};

    // A rewritten sentence takes the place of the one it rewrites. What was said, asked and left unfinished for the old
    // words folds away; finished videos and pictures stay where they were. The session itself keeps every entry.
    let rewriting = null;
    let waitingFor = null;
    function rewritten(entries) {
        const list = [...entries];
        if (rewriting && list.some(entry => entry.id === rewriting.id)) rewriting = null;
        if (rewriting && rewriting.session === state.session)
            list.push({type: 'message', role: 'user', id: rewriting.id, text: rewriting.text, revises: rewriting.revises, seq: Infinity, taken: false});
        const home = new Map(), latest = new Map(), folded = [];
        for (const entry of list) if (entry.type === 'message' && entry.role === 'user' && Number.isFinite(entry.revises)) {
            // A rewrite of a rewrite still shows at the first sentence's place.
            const origin = home.get(entry.revises) ?? entry.revises;
            home.set(entry.seq, origin); latest.set(origin, entry); folded.push([entry.revises, entry.seq]);
        }
        if (!folded.length) return list;
        const kept = entry => entry.type === 'job' && state.jobs[entry.job]?.status === 'succeeded';
        return list.flatMap(entry => {
            if (entry.type === 'message' && latest.has(entry.seq)) return [{...latest.get(entry.seq), slot: entry.id}];
            return folded.some(([from, to]) => entry.seq > from && entry.seq <= to) && !kept(entry) ? [] : [entry];
        });
    }
    // Rewrites the newest sentence: the server stops what began after it and declines what it still asked, then says the new words.
    async function rewrite(entry, text, id) {
        const response = await dataLayer.send({id, text, session: state.session, revises: entry.seq});
        if (response?.ok) {
            rewriting = {session: state.session, id, text, revises: entry.seq};
            if (!stopped) { render(true); report(); void poll(); }
            return null;
        }
        const error = await sendError(response);
        if (!stopped) void poll();
        // Another sentence came first: the page reads it, and this one stays in the box.
        if (error?.code === 'invalid_request') return t('A newer message was sent first. This one was not sent.', '已有更新的消息，此⁠条⁠未⁠发⁠送⁠。');
        if (WAIT_FOR_AGENT.includes(error?.code) || error?.code === 'session_full') return t('Not sent: ', '没发出去：') + failureReason({error}, t);
        return t('Could not stop. Click again; if it keeps failing, report the problem.', '未能停止。请再点一次；仍然失败时，请提交问题。');
    }

    function render(follow = false) {
        whiteboxButton.title = t(...WHITEBOX_PREVIEW.name);
        whiteboxButton.setAttribute('aria-label', whiteboxButton.title);
        const entries = chatEntries(state), ids = new Set(entries.map(entry => entry.id).filter(Boolean));
        for (const entry of pending) if (entry.session === state.session && ids.has(entry.id)) acknowledge(entry);
        pending = pending.filter(entry => entry.sending || entry.session !== state.session || !ids.has(entry.id));
        const localPending = pending.filter(entry => entry.session === state.session && !ids.has(entry.id));
        const said = entries.some(entry => entry.type !== 'message' || entry.role === 'user') || localPending.length > 0;
        // The newest result, a finished video or a finished film, is the one whose Open leads.
        const film = entry => entry.type === 'production' && !state.failures[entry.entity] && state.finished?.[entry.entity]?.work;
        const finished = entries.filter(entry => film(entry) || (entry.type === 'job' && state.jobs[entry.job]?.status === 'succeeded' && (state.jobs[entry.job].work || (state.jobs[entry.job].kind === 'image' && state.jobs[entry.job].thumbnail)))).at(-1);
        const wanted = [];
        if (!entries.length && !localPending.length) wanted.push(['hello', 'hello', null]);
        let lastUser = null;
        // This chat's tasks for the right panel, each with the words it was made from.
        const tasks = [], task = (job, text) => {
            if (!job?.id || !(VIDEOS.includes(job.kind || 'generate') || ['image', 'production', 'cast'].includes(job.kind))) return;
            const words = promptOf(job, text);
            tasks.push({job, title: jobName(job, t), prompt: words.text, promptSource: words.source, meta: jobMeta(job, t), order: tasks.length,
                shape: job.width > 0 && job.height > 0 ? `${job.width} / ${job.height}` : null,
                thumbnailURL: job.thumbnail ? api.apiURL(job.thumbnail) : null,
                fraction: () => { const live = state.jobs[job.id] || job; return live.status === 'running' ? fractionOf(live) : null; },
                left: () => { const live = state.jobs[job.id] || job; return live.status === 'running' ? leftOf(live) : null; }});
        };
        const shownEntries = rewritten(entries);
        // Only the newest sentence the agent already has can be rewritten, and not while another waits to go.
        const editable = localPending.length ? null : shownEntries.findLast(entry => entry.type === 'message' && entry.role === 'user');
        for (const entry of shownEntries) {
            if (entry.type === 'message') {
                if (entry.role === 'user') lastUser = entry;
                wanted.push([entry.role === 'user' && entry.id ? `m:${entry.slot || entry.id}` : `s:${entry.seq}`, entry.role === 'user' ? 'user' : 'agent', entry,
                    {editable: entry === editable}]);
            } else if (entry.type === 'job') {
                const job = state.jobs[entry.job]; if (!job) continue;
                const text = lastUser?.text;
                const key = cardKey(state.session, job.id);
                wanted.push([`s:${entry.seq}`, 'job', job, {latest: entry === finished, key, text, retry: () => againJob(key, job, text)}]);
                task(job, text);
                const failure = state.failures[entry.entity];
                if (failure) wanted.push([`e:${entry.seq}`, 'job', failure]);
            } else if (entry.type === 'production' || entry.type === 'cast') {
                // A finished film is the same card as any finished video: open it, or edit it with its shots already cut.
                const card = state.failures[entry.entity] || (entry.type === 'production' && state.finished?.[entry.entity]);
                if (card) { wanted.push([`s:${entry.seq}`, 'job', card, {latest: entry === finished, text: lastUser?.text}]); task(card, lastUser?.text); }
            } else wanted.push([`s:${entry.seq}`, 'confirm', entry]);
        }
        for (const entry of localPending) wanted.push([`m:${entry.id}`, 'user', {...entry, taken: false}]);
        if (!said) wanted.push(['examples', 'examples', null]);
        // The agent has the sentence, or is waiting for one, and has not answered yet.
        const tail = localPending.at(-1) || entries.at(-1);
        const thinking = tail?.type !== 'job' && tail?.type !== 'confirm' && (localPending.some(entry => !entry.error) || !tail?.error && tail?.role === 'user') && state.agent.connected === true
            && (state.agent.listening !== false || tail.taken === true) && (localPending.length > 0 || !state.abandoned);
        stopMode = thinking && localPending.length === 0;
        // With no stage named: Sending… until the agent has taken the sentence, then the seconds since it took it.
        const sending = localPending.some(entry => !entry.error) || tail?.role === 'user' && tail.taken === false;
        const waitKey = thinking && !sending ? `${state.session}:${tail?.id ?? tail?.seq}` : null;
        if (waitKey !== waitingFor?.key) waitingFor = waitKey ? {key: waitKey, since: Date.now()} : null;
        if (thinking) wanted.push(['dots', 'dots', Object.hasOwn(STAGE_TEXT, state.stage) ? t(...STAGE_TEXT[state.stage])
            : sending ? t('Sending…', '正在发送…') : null, {since: waitingFor?.since ?? null}]);

        const stick = follow || pinned;
        const before = log.children.length, keep = new Set(wanted.map(([key]) => key));
        for (const [key, node] of nodes) if (!keep.has(key)) { node.element.remove(); nodes.delete(key); }
        wanted.forEach(([key, kind, value, extra], index) => {
            let node = nodes.get(key);
            if (!node) { node = MAKE[kind](); nodes.set(key, node); }
            node.update(value, extra || {});
            if (log.children[index] !== node.element) log.insertBefore(node.element, log.children[index] || null);
        });
        side_.update(tasks, {current: state.current});
        drawStage(); drawSummary(tasks);
        tasksDot.hidden = !tasks.some(row => WORKING.includes(row.job.status));
        const sendable = sendingAllowed();
        attachButton.disabled = builtinScene(); tip(attachButton, 'attach', t, {why: attachButton.disabled ? t(...WHY.sceneOnly) : ''});
        input.disabled = !(state.agent.connected === true || builtinScene() && !!state.session) || creating || !!selectedSession && selectedSession !== state.session;
        input.placeholder = builtinScene() ? t(...SCENE_TEXT.describe)
            : state.agent.connected === true && !sendable ? t('Ask the assistant to continue in its own window', '请在助手的窗口里让它继续') : t('Tell the assistant…', '对助手说…');
        refreshSend();
        if (focusSession === state.session && focusSession && !input.disabled && active()) { focusSession = null; input.focus(); }
        if (stick) { pinned = true; scroller.scrollTop = scroller.scrollHeight; down.hidden = true; }
        else if (log.children.length > before) down.hidden = false;
    }
    // One line for the chat being read: what is being made and how far, else what waits, what did not finish, what is done.
    function drawSummary(tasks) {
        const of = key => tasks.filter(row => statusKey(row.job) === key);
        const running = of('running')[0], queued = of('queued').length, failed = of('failed').length, done = of('done').length;
        chatSummary = !state.session || !tasks.length ? null
            : running ? {text: running.title + ((running.fraction?.() ?? 0) >= .01 ? ` · ${Math.floor(running.fraction() * 100)}%` : ''), state: 'running'}
            : queued ? {text: t(`${queued} queued`, `${queued} 项排队中`), state: 'queued'}
            : failed ? {text: t(`${failed} did not finish`, `${failed} 项没有完成`), state: 'failed'}
            : {text: t(`${done} done`, `${done} 项已完成`), state: 'done'};
        shotWords = tasks.flatMap(row => [row.title, row.prompt].filter(Boolean));
        const node = listNodes.get(state.session);
        if (node && !searchBox.value.trim()) { put(node.where, chatSummary?.text || ''); node.where.dataset.state = chatSummary?.state || ''; node.where.hidden = !chatSummary; }
        const row = conversations.conversations.find(item => item.id === state.session);
        put(headTitle, row?.title || (state.session && entries().length ? t('New chat', '新对话') : ''));
        put(headSub, [tasks.length ? t(`${tasks.length} tasks`, `${tasks.length} 个任务`) : '', chatSummary && chatSummary.state !== 'done' ? chatSummary.text : '', row ? localTime(row.updated_at, t) : ''].filter(Boolean).join(' · '));
        head.dataset.empty = String(!headTitle.textContent);
    }
    const entries = () => chatEntries(state);
    // The card of the task being made carries the generation mark, so the floating task window grows from it and
    // returns to it; only one element has it at a time. The picked task's card is marked too.
    function drawStage() {
        const picked = side_.selected();
        for (const item of log.children) {
            if (!item.dataset?.job) continue;
            if (item.dataset.job === picked) item.dataset.selected = 'true'; else delete item.dataset.selected;
            const box = item.querySelector('.fv-chat-preview');
            if (!box) continue;
            if (item.dataset.job && item.dataset.job === state.current && item.dataset.mode === 'working') box.dataset.fvTaskStage = 'autopilot';
            else delete box.dataset.fvTaskStage;
        }
    }
    scroller.addEventListener('scroll', () => {
        if (!scroller.clientHeight) return;
        pinned = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 48;
        if (pinned) down.hidden = true;
    }, {passive: true});

    async function readAway() {
        const row = state.current ? null : runningElsewhere(conversations, state.session);
        if (!row) { away = null; return; }
        try {
            // Past the last message: only that conversation's jobs come back.
            const response = await dataLayer.read({session: row.id, after: Number.MAX_SAFE_INTEGER, signal: closed.signal});
            if (!response.ok) { away = null; return; }
            const data = await response.json(), jobs = Object.values(data.jobs || {}).filter(job => WORKING.includes(job?.status));
            const job = data.jobs?.[data.current] || jobs[0];
            away = job ? {session: row.id, job, jobs: data.jobs, ids: jobs.map(job => job.id)} : null;
        } catch { /* The last reading stays until the next one. */ }
    }
    let reportedJob = null;
    function report() {
        const current = (state.current ? state.jobs[state.current] : away?.job) || null;
        // A job that starts or ends changes a dot in the list, so the list is read again right away.
        if ((current?.id || null) !== reportedJob) { reportedJob = current?.id || null; void readConversations(true); }
        changed({known, agent: state.agent, current, fraction: current ? fractionOf(current) : 0, left: current ? leftOf(current) : null, active: state.active,
            stopping: bulkStopping, any: state.items.size > 0 || pending.some(entry => entry.session === state.session)});
    }
    function acknowledge(entry) {
        entry.acknowledged = true; entry.error = null; entry.automatic = false;
        if (entry.session === state.session) say('');
        unsaidSessions.delete(entry.session);
        if (entry.draft && drafts.get(entry.session) === entry.draft) {
            drafts.delete(entry.session);
            if (draftSession === entry.session) { input.value = ''; grow(); }
        }
        if (entry.attachments?.length) {
            const list = stagedList(entry.session), sent = new Set(entry.attachments.map(item => item.name));
            staged.set(entry.session, list.filter(item => !sent.has(item.attachment?.name)));
            if (draftSession === entry.session) renderStrip();
        }
    }
    const waitingNotice = code => (code ? code === 'not_listening' : state.agent.connected === true)
        ? t('Will send when the assistant is back', '等助手回来后自动发送')
        : t('Will send when the assistant connects', '等助手连上后自动发送');
    async function send(text, original = null, draft = null, automatic = false, attachments = null) {
        text = String(text).trim();
        original ||= pending.find(entry => entry.session === (state.session ?? null) && entry.text === text && (entry.error || entry.sending || entry.automatic));
        if (!text && !(original?.attachments || attachments)?.length || original?.sending || original && original.session !== state.session) return false;
        if (!inSelectedChat()) {
            say(creating ? t('Creating a new chat…', '正在新建对话…') : t('Loading this chat. Please send again shortly.', '正在读取对话，请稍后再发一次'));
            return false;
        }
        // With no Agent connected the white-model button is the way in: the sentence goes to the built-in AI, which
        // picks a ready-made scene or says that none fits. It is never queued for an Agent.
        if (builtinScene()) {
            if (attachments?.length || original || !state.session) return false;
            const made = await runScene(null, state.session, () => pickScene(api, text, state.session));
            if (made === false) say(t(...SCENE_TEXT.failed) + t(...SCENE_TEXT.noFit));
            else if (made === true) { input.value = ''; grow(); }
            return made === true;
        }
        const entry = original || {id: newId(), text, attachments: attachments || [], session: state.session ?? null, draft, whiteboxPreview};
        if (draft) entry.draft = draft;
        if (!original) pending.push(entry);
        if (!automatic) entry.attempts = 0;
        entry.errorCode = null;
        if (!sendingAllowed()) {
            entry.automatic = true; entry.error = waitingNotice();
            say(entry.error); render(); report(); return false;
        }
        const whiteboxes = [...state.items.values()].filter(card => card.type === 'confirm' && card.whitebox && whiteboxState(card) === 'asked');
        entry.sending = true; entry.retried = !!original; entry.error = null; entry.automatic = false; render(true); report();
        try {
            const response = await dataLayer.sendMessage(entry, {sessionSupported: conversations.supported === true, stopped: () => stopped, previewField: WHITEBOX_PREVIEW.field});
            if (entry.acknowledged) return true;
            if (response?.ok) {
                const value = await response.json().catch(() => ({}));
                if (entry.session === state.session) for (const card of whiteboxes)
                    supersededWhiteboxes.add(cardKey(entry.session, card.id));
                acknowledge(entry);
                if (!entry.session && value.session) entry.session = value.session;
                if (!stopped) { void poll(); void readConversations(true); }
                return true;
            }
            const error = await sendError(response), refused = sendRefused(error, response);
            // Its pictures were freed meanwhile (the same picture removed from another draft): upload them again and
            // send once more, after this attempt has finished.
            if (!entry.reuploaded && entry.attachments?.length && pictureGone(error) && await reupload(entry)) {
                entry.reuploaded = true;
                setTimeout(() => { if (!stopped) void send(entry.text, entry, entry.draft); }, 0);
                return false;
            }
            entry.errorCode = error?.code;
            // What a feedback report carries for this failure: how the answer failed, never text or paths.
            entry.failureKind = error?.code || (response ? 'http_' + response.status : 'no_response');
            entry.failureFacts = {attempts: entry.attempts, http_status: response?.status ?? null, agent_connected: state.agent.connected === true,
                page_online: typeof navigator === 'object' ? navigator.onLine !== false : null};
            const waiting = !refused && (WAIT_FOR_AGENT.includes(error?.code) || !error && state.agent.connected !== true);
            const exhausted = entry.attempts >= SEND_ATTEMPTS;
            entry.automatic = waiting && !exhausted;
            const explanation = refused || error && !waiting ? t('Not sent: ', '没发出去：') + failureReason({error}, t)
                : exhausted ? t('Not sent. Click "Retry".', '没有发出，请点“重试”。')
                : waiting ? waitingNotice(error?.code) : t('The connection is unstable. Click "Retry".', '网络不稳，请点“重试”。');
            entry.error = waiting || /^(Not sent|没发出|没有发出)/.test(explanation) ? explanation : t('Could not send: ', '没有发送成功：') + explanation;
            if (entry.session === state.session) {
                if (waiting && error?.code === 'not_connected') state = {...state, agent: {connected: false, name: null}};
                if (waiting && error?.code === 'not_listening') state = {...state, agent: {...state.agent, listening: false}};
                say(entry.errorCode === 'session_full' ? '' : explanation);  // the failed bubble carries the report button
            }
            return false;
        } finally {
            entry.sending = false;
            if (!stopped) { render(); renderConversations(); report(); }
        }
    }
    function grow() {
        input.style.height = 'auto';
        // At most five lines; the box scrolls after that.
        const line = (typeof getComputedStyle === 'function' && parseFloat(getComputedStyle(input).lineHeight)) || 22;
        input.style.height = `${Math.min(input.scrollHeight, Math.round(line * 5) + 14)}px`;
        refreshSend();
    }
    input.addEventListener('input', () => {
        if (entryDraft !== null) entryDraft = input.value;
        saveDraft();
        // Editing a queued draft cancels its automatic delivery. Its original message remains available for manual retry.
        for (const entry of pending) if (entry.automatic && entry.session === draftSession && entry.draft !== drafts.get(draftSession)) {
            entry.automatic = false; entry.errorCode = 'edited';
            entry.error = t('The message changed. Click "Retry" to send the original.', '消息已修改，点“重试”发送原消息。');
            say('');
        }
        if (!input.value.trim() || tooLong && !messageTooLong(input.value.trim())) { tooLong = false; say(''); }
        grow(); if (unsaidSessions.has(draftSession)) renderConversations();
    });
    // Enter sends, Shift+Enter starts a new line; Enter that confirms an input-method candidate does neither.
    // Registered through listenDialogKey so Enter still reaches it when this
    // page opens above the creation panel, whose window gate routes upper keys.
    listenDialogKey(input, 'keydown', event => {
        if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
        event.preventDefault(); if (!stopMode) form.requestSubmit();
    });
    form.addEventListener('submit', event => {
        event.preventDefault();
        if (stopMode) { void abandon(); return; }
        const text = input.value;
        const draft = saveDraft();
        const pictures = readyPictures();
        if (!text.trim() && !pictures.length || !composeAllowed() || draftSending() || stagedList().some(item => !item.attachment)) return;
        // Too long for one message: nothing is sent, and the words stay in the box to shorten.
        if (messageTooLong(text.trim())) { say(TOO_LONG()); tooLong = true; return; }
        const failed = pending.find(entry => entry.session === state.session && entry.draft === draft && entry.error);
        void send(text, failed, draft, false, pictures);
    });

    // Both a server-side session change and a list selection reset the same reader. Job clocks stay keyed by job id.
    function resetChat() {
        dismissImage?.(); dismissImage = null;
        state = emptyChat(); nodes.clear(); side_?.reset();
        log.querySelectorAll('video').forEach(video => video.pause()); log.replaceChildren(); pinned = true;
    }
    async function poll() {
        clearTimeout(timer); timer = null;
        if (stopped) return;
        if (busy) { reread = true; return; }
        if (!reading()) return;
        busy = true; reread = false;
        if (!document.hidden) void readConversations();
        const readSelection = selection, requested = selectedSession;
        let again = false;
        try {
            // The first read after a cached start reads the whole chat once, so every card is brought up to date.
            const {after} = chatReadRequest(state, requested);
            const response = await dataLayer.read({session: requested, after, signal: closed.signal});
            if (stopped) return;
            if (readSelection !== selection) again = true;
            else if (response.ok) {
                const data = await response.json();
                if (stopped) return;
                if (readSelection !== selection) { again = true; }
                // Another conversation began: forget this one and read the new one whole.
                else if (after && (data.session ?? null) !== state.session) { resetChat(); again = true; }
                else {
                    // The first send can create the session before its HTTP response reaches the page.
                    // Adopt the page's unassigned draft/outbox when that session first appears in a read.
                    if (state.session == null && data.session && !requested) {
                        for (const entry of pending) if (entry.session == null) entry.session = data.session;
                        if (draftSession === null) {
                            const draft = saveDraft();
                            // Text entered during deletion's fallback read joins the target's older draft; neither is dropped.
                            const older = drafts.get(data.session)?.text;
                            if (draft.text && older && older !== draft.text) draft.text = `${older}\n\n${draft.text}`;
                            if (draft.text || !older) drafts.set(data.session, draft);
                            drafts.delete(null); draftSession = data.session;
                            input.value = drafts.get(data.session)?.text || ''; grow();
                        }
                    }
                    if ((data.session ?? null) !== state.session) resetChat();
                    state = chatMerge(state, data); failures = 0;
                    if (state.session && !chatEntries(state).some(entry => entry.type === 'message' && entry.role === 'user')) unsaidSessions.add(state.session);
                    showDraft(state.session); rememberSelection(state.session);
                    conversations = selectConversation(conversations, state.session); renderConversations();
                    await readAway();
                    if (stopped) return;
                    if (readSelection !== selection) { away = null; again = true; }
                }
            } else if (response.status === 404 && requested) {
                selectedSession = undefined; rememberSelection(null); selection++; resetChat(); again = true; void readConversations(true);
            } else failures++;
        } catch { failures++; }
        finally { busy = false; }
        if (stopped) return;
        if (again || reread) return poll();
        known = true;
        // The next read is booked before the page hears of this one, so the page's sync() does not start another.
        const idle = state.agent.connected !== true && !state.active;
        timer = setTimeout(poll, chatDelay({hidden: document.hidden, idle, interval, failures}));
        render(); report(); tab();
        if (sendingAllowed()) for (const entry of [...pending]) {
            if (entry.session !== state.session || !entry.automatic || entry.sending || entry.acknowledged) continue;
            await send(entry.text, entry, entry.draft, true);
            if (!sendingAllowed() || stopped) break;
        }
    }
    // Between reads the estimate keeps moving, on the cards and in the page's header alike.
    const moving = setInterval(() => {
        if (!active() || ![...Object.values(state.jobs), ...(away ? [away.job] : [])].some(job => job.status === 'running')) return;
        for (const item of log.children) item.tick?.();
        side_.tick(); report();
    }, 250);
    // The title of a hidden tab carries one fixed prefix: no flashing, no counts. It is the page's own title again when the user returns.
    let titleBase = null, titleShown = null, seen = null;
    const finished = () => new Set(Object.values(state.jobs).filter(job => !WORKING.includes(job.status)).map(job => job.id));
    const asking = () => [...state.items.values()].some(entry => entry.type === 'confirm'
        && (entry.whitebox ? whiteboxState(entry) : answers.get(cardKey(state.session, entry.id)) || state.confirms[entry.id] || entry.state || 'asked') === 'asked');
    function tab() {
        if (titleShown !== null && document.title !== titleShown) titleBase = titleShown = null;
        if (stopped || !document.hidden || !known) {
            if (titleShown !== null && document.title === titleShown) document.title = titleBase;
            titleBase = titleShown = null; seen = null; return;
        }
        seen ||= finished();
        const notice = tabNotice(state.jobs, asking(), seen);
        if (!notice) {
            if (titleShown !== null) document.title = titleBase;
            titleBase = titleShown = null; return;
        }
        if (titleBase === null) titleBase = document.title;
        // The browser trims and collapses spaces in a title; what it shows is what the next look compares with.
        document.title = t(...TAB_PREFIX[notice]) + titleBase; titleShown = document.title;
    }
    const visible = () => { if (document.hidden) { seen = finished(); tab(); } else { tab(); if (reading()) void poll(); } };
    document.addEventListener('visibilitychange', visible);
    function sync() {
        if (pinned) scroller.scrollTop = scroller.scrollHeight;
        if (active() && !timer && !busy) poll();
    }
    render();
    // connection: the left panel's foot; running: the right panel's head; head: the middle column's top row on narrow windows.
    // column: the conversation's centred column, the view's main block for the shell's centring and task window.
    return {element, input, sync, refresh: poll, column: log, slots: {connection: railFoot, running: side_.running, head: headEnd},
        scrollToJob(id) {
            // A job of another conversation: go to that conversation, which opens at its end where the job is.
            if (away?.job.id === id && !state.jobs[id]) chooseChat(away.session);
            else log.querySelector(`[data-job="${CSS.escape(id)}"]`)?.scrollIntoView({block: 'center', behavior: 'smooth'});
        },
        async stopAll() {
            if (bulkStopping) return;
            const ids = [...new Set([...Object.values(state.jobs).filter(job => WORKING.includes(job.status)).map(job => job.id), ...(away?.ids || [])])];
            bulkStopping = true; report();
            try { await cancelJobs(ids); }
            finally { bulkStopping = false; if (!stopped) { report(); void poll(); } }
        },
        stop() { closeMenu(); stopped = true; for (const [session, timer] of undoing) { clearTimeout(timer); finishDelete(session); } tab(); dismissImage?.(); closed.abort(); clearInterval(moving); clearTimeout(timer); timer = null;
            document.removeEventListener('visibilitychange', visible); document.removeEventListener('pointerdown', outside, true);
            stopMenuKeys(); stopSlash?.(); history.dispose(); window.removeEventListener('resize', resize); list.removeEventListener('scroll', listScroll);
            log.querySelectorAll('video').forEach(video => video.pause()); }};
}
