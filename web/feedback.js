// The "Feedback" entry: one sentence, optionally the page's picture and a
// diagnostic summary (both off until ticked). Sending opens the GitHub
// suggestion page with the text (and summary) filled in; the user submits it
// there. The picture cannot travel in a link, so it is put on the clipboard
// for pasting. The entry sits beside Settings in every view.
import { api } from '../../scripts/api.js';
import { closeDialog } from './motion.js';
import { isolateDialogKeys, routeDialogKey } from './dialog_keys.js';
import { productVersion } from './updates.js';

const ISSUE_FORM = 'https://github.com/FlashML-org/FreeVideo/issues/new';
const DRAFT_KEY = 'freevideo.feedback-draft';
const MAX_TEXT = 1500;
const MAX_DIAGNOSTICS = 3000;
const MAX_ADDRESS = 7000;
const SHOT_WIDTH = 1600;

for (const name of ['setup.css', 'feedback.css']) {
    if (document.head.querySelector(`link[href$="${name}"]`)) continue;
    const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = new URL('./' + name, import.meta.url).href;
    document.head.append(link);
}

// The GitHub page for a suggestion, with the text (and, when asked for, the
// summary) already in it.
export function feedbackIssueURL(text, version = null, diagnostics = '', title = '[Feedback]') {
    return feedbackIssueParts(text, version, diagnostics, title).url;
}

// Same, and whether the end of the text had to be left off to fit the address.
export function feedbackIssueParts(text, version = null, diagnostics = '', title = '[Feedback]') {
    const build = (body, extra) => {
        const params = new URLSearchParams({ template: 'feedback.yml', title, feedback: body });
        if (version) params.set('version', `v${version}`);
        if (extra) params.set('diagnostics', extra);
        return `${ISSUE_FORM}?${params}`;
    };
    const whole = String(text || '').slice(0, MAX_TEXT);
    let body = whole, extra = String(diagnostics || '').slice(0, MAX_DIAGNOSTICS);
    // GitHub refuses very long addresses; the summary gives way first, then the end of the text.
    while (build(body, extra).length > MAX_ADDRESS && extra) extra = extra.slice(0, Math.max(0, extra.length - 250));
    while (build(body, extra).length > MAX_ADDRESS && body.length > 1) body = body.slice(0, Math.floor(body.length * 0.9));
    return { url: build(body, extra), truncated: body.length < whole.length };
}

function readDraft() { try { return localStorage.getItem(DRAFT_KEY) || ''; } catch { return ''; } }
function writeDraft(text) {
    try { text ? localStorage.setItem(DRAFT_KEY, text) : localStorage.removeItem(DRAFT_KEY); } catch { /* The text stays in the box. */ }
}

// The current tab as one PNG: the browser asks the user to share it, and the
// dialog hides for the moment of capture so the page is what shows.
async function capturePage(hide) {
    const media = navigator.mediaDevices;
    if (!media?.getDisplayMedia) throw Object.assign(new Error('unsupported'), { name: 'NotSupportedError' });
    const stream = await media.getDisplayMedia({ video: { displaySurface: 'browser' }, audio: false,
        preferCurrentTab: true, selfBrowserSurface: 'include', surfaceSwitching: 'exclude' });
    try {
        const video = document.createElement('video'); video.muted = true; video.srcObject = stream;
        // A tab that never produces a frame must not leave the box waiting.
        await Promise.race([video.play(), new Promise((_, fail) => setTimeout(() => fail(new Error('no frame')), 5000))]);
        const restore = hide();
        try {
            await new Promise(done => setTimeout(done, 180));
            const width = video.videoWidth, height = video.videoHeight;
            if (!width || !height) throw new Error('empty');
            const scale = Math.min(1, SHOT_WIDTH / width);
            const canvas = document.createElement('canvas');
            canvas.width = Math.max(1, Math.round(width * scale)); canvas.height = Math.max(1, Math.round(height * scale));
            canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
            const blob = await new Promise(done => canvas.toBlob(done, 'image/png'));
            if (!blob) throw new Error('empty');
            return blob;
        } finally { restore(); }
    } finally { for (const track of stream.getTracks()) track.stop(); }
}

// The engine's redacted summary (hardware, settings, timings; no prompt or
// picture) as one compact line of text; '' when it cannot be read.
export async function readDiagnostics(fetchApi = p => api.fetchApi(p)) {
    try {
        const response = await fetchApi('/freevideo/feedback-info');
        if (!response.ok) return '';
        return JSON.stringify(await response.json()).slice(0, MAX_DIAGNOSTICS);
    } catch { return ''; }
}

// Puts the picture on the clipboard; resolves to whether it got there.
export async function copyPicture(blob, clipboard = navigator.clipboard) {
    try {
        if (!clipboard?.write || typeof ClipboardItem === 'undefined') return false;
        await clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
        return true;
    } catch { return false; }
}

const el = (tag, cls, text) => {
    const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text;
    return e;
};

// True when the browser opened the page; false when it blocked it.
function openPage(address) {
    const page = window.open(address, '_blank');
    if (!page) return false;
    try { page.opener = null; } catch { /* Same-origin only; the page is GitHub's. */ }
    return true;
}

async function copyText(text, clipboard = navigator.clipboard) {
    try { await clipboard.writeText(text); return true; } catch { return false; }
}

let current = null;

export function openFeedback(t, { open = openPage, version = productVersion,
    fetchApi = p => api.fetchApi(p) } = {}) {
    if (current?.open) { current.querySelector('textarea')?.focus(); return current; }
    const dialog = el('dialog', 'fv-setup fv-setup-small fv-feedback'); current = dialog;
    dialog.setAttribute('aria-label', t('Feedback', '反馈'));
    dialog.style.pointerEvents = 'auto';
    const head = el('header', 'fv-setup-head'), close = el('button', 'fv-close', '×');
    close.type = 'button'; close.setAttribute('aria-label', t('Close', '关闭')); close.onclick = () => closeDialog(dialog);
    head.append(el('h2', null, t('Feedback', '反馈')), close);

    const field = el('textarea'); field.rows = 5; field.maxLength = MAX_TEXT;
    field.placeholder = t('What could be better?', '哪里可以做得更好？');
    field.setAttribute('aria-label', t('Feedback', '反馈'));
    field.value = readDraft();

    const check = label => {
        const row = el('label', 'fv-check'), box = el('input'); box.type = 'checkbox';
        row.append(box, el('span', null, label)); return { row, box };
    };
    const shotCheck = check(t('Attach a screenshot of this page', '附上这一页的截图'));
    const diagCheck = check(t('Include diagnostic info (no prompts or images)', '附上诊断信息（不含提示词和画面）'));

    const preview = el('div', 'fv-feedback-shot'); preview.hidden = true;
    const thumb = el('img'); thumb.alt = '';
    const remove = el('button', 'fv-quiet', '×'); remove.type = 'button'; remove.setAttribute('aria-label', t('Remove', '移除'));
    preview.append(thumb, remove);

    const send = el('button', 'fv-primary', t('Submit on GitHub', '在 GitHub 提交')); send.type = 'button';
    const status = el('p', 'fv-feedback-status'); status.setAttribute('role', 'status');
    const actions = el('div', 'fv-feedback-actions'); actions.append(status, send);
    dialog.append(head, field, shotCheck.row, preview, diagCheck.row, actions);

    let picture = null, previewURL = null, diagnostics = '';
    const setPicture = blob => {
        if (previewURL) URL.revokeObjectURL(previewURL);
        picture = blob; previewURL = blob ? URL.createObjectURL(blob) : null;
        preview.hidden = !blob; thumb.src = previewURL || '';
        shotCheck.box.checked = Boolean(blob);
    };
    const refresh = () => { send.disabled = !field.value.trim(); };
    field.addEventListener('input', () => { writeDraft(field.value); refresh(); });
    remove.onclick = () => setPicture(null);
    shotCheck.box.addEventListener('change', async () => {
        if (!shotCheck.box.checked) { setPicture(null); return; }
        shotCheck.box.disabled = true; status.replaceChildren();
        try {
            setPicture(await capturePage(() => { dialog.style.visibility = 'hidden'; return () => { dialog.style.visibility = ''; }; }));
        } catch (error) {
            setPicture(null);
            console.warn('FreeVideo feedback screenshot:', error);
            // Declining the browser's prompt is the user's own answer; anything else is said once.
            if (error?.name !== 'NotAllowedError') status.textContent = t("Couldn't capture this page. You can submit without it.", '没能截取这一页，可以不带截图提交。');
        } finally { shotCheck.box.disabled = false; }
    });
    // Read while the box is ticked, so the page can open in the same click as Send.
    diagCheck.box.addEventListener('change', async () => {
        diagnostics = '';
        if (!diagCheck.box.checked) return;
        status.replaceChildren(); diagCheck.box.disabled = true;
        diagnostics = await readDiagnostics(fetchApi);
        diagCheck.box.disabled = false;
        if (!diagnostics) { diagCheck.box.checked = false; status.textContent = t("Couldn't read the diagnostic info. You can submit without it.", '没能读取诊断信息，可以不带它提交。'); }
    });
    send.onclick = async () => {
        const text = field.value.trim();
        if (!text) return;
        const { url: address, truncated } = feedbackIssueParts(text, version(), diagCheck.box.checked ? diagnostics : '');
        // Opening and copying both start inside the click, which is what lets the browser allow them.
        const reached = open(address);
        const hadPicture = Boolean(picture);
        const copied = reached ? (picture ? copyPicture(picture) : truncated ? copyText(text) : Promise.resolve(false))
            : copyText(address);
        // A text too long for the address stays in the box, so nothing is lost with the dialog.
        if (reached && !truncated) writeDraft('');
        const done = await copied;
        status.textContent = reached && truncated
            ? (hadPicture || !done
                ? t('GitHub is open, but the text was too long and was cut short there. Your full text stays here.', '已打开 GitHub，但文字过长，那边被截短了，完整文字仍留在这里。')
                : t('GitHub is open, but the text was too long and was cut short there. The full text is copied; paste it in.', '已打开 GitHub，但文字过长，那边被截短了，完整文字已复制，请粘贴补全。'))
            : !reached
            ? (done ? t("Couldn't open GitHub. The link is copied; paste it into your browser.", '没能打开 GitHub，链接已复制，请粘贴到浏览器。')
                : t("Couldn't open GitHub. Please try again later.", '没能打开 GitHub，请稍后再试。'))
            : hadPicture && done
                ? t('GitHub is open and the screenshot is copied. Paste it in, then submit.', '已打开 GitHub，截图已复制，粘贴后提交。')
                : hadPicture
                ? t("GitHub is open with your text filled in, but the screenshot couldn't be copied. Submit it there without it.", '已打开 GitHub，文字已填好，但截图没能复制，可不带截图提交。')
                : t('GitHub is open with your text filled in. Submit it there.', '已打开 GitHub，文字已填好，确认后提交。');
    };

    const stopKeys = isolateDialogKeys(dialog, event => routeDialogKey(event, dialog));
    // A press that starts and ends on the backdrop closes; selecting text and releasing outside does not.
    let downOnBackdrop = false;
    dialog.addEventListener('pointerdown', event => { downOnBackdrop = event.target === dialog; });
    dialog.addEventListener('click', event => { if (event.target === dialog && downOnBackdrop) closeDialog(dialog); });
    dialog.addEventListener('close', () => {
        stopKeys(); setPicture(null); dialog.remove(); if (current === dialog) current = null;
    });
    document.body.append(dialog);
    dialog.showModal(); refresh(); field.focus();
    return dialog;
}

// The header entry, in the style of the Settings button beside it.
export function feedbackButton(t, className = 'fv-quiet') {
    const label = t('Feedback', '反馈');
    const button = el('button', `${className} fv-feedback-entry`); button.type = 'button';
    button.setAttribute('aria-label', label); button.title = label;
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 16 16'); icon.setAttribute('aria-hidden', 'true'); icon.setAttribute('focusable', 'false');
    const path = document.createElementNS(icon.namespaceURI, 'path');
    path.setAttribute('d', 'M13.5 2.5h-11a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h2v3l3.5-3h5.5a1 1 0 0 0 1-1v-7a1 1 0 0 0-1-1Z');
    icon.append(path); button.append(icon, el('span', 'fv-feedback-label', label));
    button.onclick = () => openFeedback(t);
    return button;
}

// The tutorial entry, left of Feedback: same style, icon only where Feedback is.
export function guideButton(t, className = 'fv-quiet') {
    const label = t('Tutorial', '教程');
    const button = el('button', `${className} fv-feedback-entry fv-guide-entry`); button.type = 'button';
    button.setAttribute('aria-label', label); button.title = label;
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 16 16'); icon.setAttribute('aria-hidden', 'true'); icon.setAttribute('focusable', 'false');
    const path = document.createElementNS(icon.namespaceURI, 'path');
    path.setAttribute('d', 'M2.5 3.2c1.9-.5 3.7-.3 5.5.8v9c-1.8-1.1-3.6-1.3-5.5-.8ZM13.5 3.2c-1.9-.5-3.7-.3-5.5.8v9c1.8-1.1 3.6-1.3 5.5-.8Z');
    icon.append(path); button.append(icon, el('span', 'fv-feedback-label', label));
    button.onclick = () => import('./tutorial_book.js').then(({openTutorialBook}) => openTutorialBook({chapter: 'quick-start'})).catch(() => {});
    return button;
}
