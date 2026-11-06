import {chatData, chatPoller, chatDelay, chatReadRequest, emptyChat, chatMerge, chatEntries, newId} from './chat_data.js';
import {phoneText, jobGroups, mergePhoneJobs, retryWords, queueWords, remainingWords, mediaPath, saveVideo, applyPhoneChain, phonePartWords, phoneJobKey} from './phone_model.js';
import {wordmark} from './wordmark.js';

export function mountPhone(root) {
const t = phoneText(navigator.language);
document.documentElement.lang = t('en', 'zh-CN');
const el = (tag, cls = '', text) => { const e = document.createElement(tag); e.className = cls; if (text != null) e.textContent = text; return e; };
const button = (cls, label, run) => { const b = el('button', cls, label); b.type = 'button'; b.onclick = run; return b; };
const icon = path => { const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true'); const p = document.createElementNS(s.namespaceURI, 'path'); p.setAttribute('d', path); s.append(p); return s; };
const closed = new AbortController();
let locked = false, stopped = false, sending = false, retryUntil = 0, countdown = null;
let chat = emptyChat(), jobs = {}, selected, revision = 0, cursor = null, historyStarted = false, limit = 30, loadingMore = false, tab = location.hash === '#jobs' ? 'jobs' : 'chat', result = null;
let lastSend = null, toastTimer = null, flash = null, toastJob = null, permissionAsked = false;
try { permissionAsked = localStorage.getItem('freevideo.phone.notification-asked') === '1'; } catch {}
const drafts = new Map(), rows = new Map(), scrollPositions = {chat: 0, jobs: 0};
const top = el('header', 'phone-top'), offline = el('div', 'phone-offline', t('Connection lost. Reconnecting…', '连接断了，正在重新连接…'));
offline.hidden = true; offline.setAttribute('role', 'status');
const main = el('main', 'phone-main'), log = el('ol', 'phone-log'); log.setAttribute('role', 'log'); log.setAttribute('aria-label', t('Chat', '对话'));
let convRows = [], convCurrent = '';
const chatsButton = button('phone-chats', '', () => openSheet()); chatsButton.setAttribute('aria-label', t('Chats', '对话')); chatsButton.setAttribute('aria-haspopup', 'dialog'); chatsButton.append(icon('M4 4h16v12H9l-5 4V4zM12 7v6M9 10h6'));
const sheet = el('div', 'phone-sheet'), sheetPanel = el('section', 'phone-sheet-panel'); sheet.hidden = true; sheetPanel.setAttribute('role', 'dialog'); sheetPanel.setAttribute('aria-label', t('Chats', '对话')); sheet.append(sheetPanel);
const clock = seconds => { const d = new Date(Number(seconds) * 1000); if (!Number.isFinite(d.getTime()) || !seconds) return ''; const now = new Date(); return d.toDateString() === now.toDateString() ? d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', hour12: false}) : d.toLocaleDateString(t('en-US', 'zh-CN'), {month: 'long', day: 'numeric'}); };
function closeSheet() { sheet.hidden = true; chatsButton.setAttribute('aria-expanded', 'false'); chatsButton.focus(); }
function renderSheet() {
    const fresh = button('phone-sheet-new', t('New chat', '新对话'), async () => {
        fresh.disabled = true;
        try { const data = await json(await dataLayer.create()); if (!data.session) throw new Error(); closeSheet(); choose(data.session); }
        catch { closeSheet(); notice(t('Could not start a new chat. Try again.', '没有新建成功，请重试。')); }
    });
    const list = el('ul', 'phone-sheet-list');
    for (const row of convRows.some(row => row.id === convCurrent) || !convCurrent ? convRows : [{id: convCurrent}, ...convRows]) {
        const item = button('phone-sheet-row', '', () => { closeSheet(); if (row.id !== convCurrent) choose(row.id); });
        const check = el('span', 'phone-sheet-check', row.id === convCurrent ? '✓' : ''); check.setAttribute('aria-hidden', 'true');
        if (row.id === convCurrent) item.setAttribute('aria-current', 'true');
        item.append(check, el('span', 'phone-sheet-title', row.title || t('New chat', '新对话')), el('span', 'phone-sheet-time', clock(row.updated_at)));
        const li = el('li'); li.append(item); list.append(li);
    }
    const grip = el('div', 'phone-sheet-grip'); grip.setAttribute('aria-hidden', 'true');
    sheetPanel.replaceChildren(grip, fresh, list);
}
function openSheet() { renderSheet(); sheet.hidden = false; chatsButton.setAttribute('aria-expanded', 'true'); sheetPanel.querySelector('button').focus(); }
top.append(wordmark(), chatsButton);
sheet.addEventListener('pointerdown', event => { if (event.target === sheet) closeSheet(); });
let dragFrom = null;
sheetPanel.addEventListener('touchstart', event => { dragFrom = sheetPanel.scrollTop <= 0 ? event.touches[0].clientY : null; }, {passive: true});
sheetPanel.addEventListener('touchend', event => { if (dragFrom !== null && event.changedTouches[0].clientY - dragFrom > 60) closeSheet(); dragFrom = null; }, {passive: true});
sheet.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); closeSheet(); } });
const generations = el('div', 'phone-generations');
main.append(log, generations);
const compose = el('form', 'phone-compose'), input = el('textarea'), send = button('phone-send', '', () => compose.requestSubmit());
input.rows = 1; input.maxLength = 8000; input.placeholder = t('What would you like to make?', '想做什么视频？'); input.setAttribute('aria-label', input.placeholder);
send.append(icon('M12 19V5M5 12l7-7 7 7')); send.setAttribute('aria-label', t('Send', '发送')); send.disabled = true; compose.append(input, send);
const warning = el('p', 'phone-notice'); warning.hidden = true; warning.setAttribute('role', 'status');
const tabs = el('nav', 'phone-tabs'); tabs.setAttribute('aria-label', t('Pages', '页面'));
const chatTab = button('phone-tab', '', () => show('chat')), jobsTab = button('phone-tab', '', () => show('jobs'));
for (const [b, label, path] of [[chatTab, t('Chat', '对话'), 'M4 4h16v12H9l-5 4V4z'], [jobsTab, t('Generations', '生成'), 'M4 7h11v11H4zM15 11l6-4v11l-6-4']]) { b.append(icon(path), el('span', '', label)); b.setAttribute('role', 'tab'); tabs.append(b); }
tabs.setAttribute('role', 'tablist');
const viewer = el('section', 'phone-result'), toast = el('div', 'phone-toast'); toast.hidden = viewer.hidden = true; toast.setAttribute('role', 'status');
const pw = el('form', 'phone-password'), field = el('div', 'phone-password-field'), password = el('input'), pwError = el('p', 'phone-error');
password.type = 'password'; password.autocomplete = 'current-password'; password.setAttribute('aria-label', t('Password', '密码'));
const eye = button('phone-eye', '', () => { password.type = password.type === 'password' ? 'text' : 'password'; eye.setAttribute('aria-label', password.type === 'password' ? t('Show password', '显示密码') : t('Hide password', '隐藏密码')); });
eye.append(icon('M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zm10 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6z')); eye.setAttribute('aria-label', t('Show password', '显示密码'));
const enter = button('phone-primary', t('Open', '进入'), () => pw.requestSubmit()); enter.disabled = true;
field.append(password, eye); pwError.hidden = true; pwError.setAttribute('role', 'alert'); pw.append(wordmark(), el('h1', '', t('Enter password', '输入密码')), field, pwError, enter); pw.hidden = true;
root.append(top, offline, main, warning, compose, viewer, toast, tabs, pw, sheet);
function notice(text = '') { warning.textContent = text; warning.hidden = !text; }
function connection(ok) { offline.hidden = ok || locked; }
function requirePassword() { if (locked) return; locked = true; document.querySelectorAll('dialog.phone-confirm').forEach(dialog => dialog.close()); viewer.querySelector('video')?.pause(); renderShell(); password.focus(); }
// AbortSignal.any and .timeout are missing before iOS Safari 17.4 / 16 and Firefox 124 / 100, so the signals are joined by hand.
function requestSignal(signals, ms) {
    const control = new AbortController(), off = [];
    const stop = reason => { off.splice(0).forEach(remove => remove()); if (!control.signal.aborted) control.abort(reason); };
    for (const source of signals) {
        if (source.aborted) { stop(source.reason); break; }
        const on = () => stop(source.reason);
        source.addEventListener('abort', on, {once: true}); off.push(() => source.removeEventListener('abort', on));
    }
    setTimeout(() => stop(new DOMException('The operation timed out.', 'TimeoutError')), ms);
    return control.signal;
}
const api = {apiURL: path => path, async fetchApi(path, options = {}) {
    const signals = [closed.signal, options.signal].filter(Boolean);
    // The proxy hands each signed-in device its write proof at /m/csrf; the desktop page asks the agent route.
    const response = await fetch(path === '/freevideo/agent/session' ? '/m/csrf' : path, {...options, signal: requestSignal(signals, 10000), credentials: 'same-origin'});
    // Without a valid device cookie the proxy answers every request with its password page, never JSON.
    if ([401, 429].includes(response.status) && (response.headers.get('Content-Type') || '').startsWith('text/html')) {
        requirePassword(); const wait = Number(response.headers.get('X-FreeVideo-Retry-After')); if (wait > 0) lockFor(wait);
    }
    return response;
}};
const dataLayer = chatData(api);
async function json(response) { const data = await response.json(); if (!response.ok) throw Object.assign(new Error(), {data, status: response.status}); return data; }
function lockFor(seconds) { retryUntil = Date.now() + Math.max(0, Number(seconds) || 0) * 1000; clearInterval(countdown); tickPassword(); countdown = setInterval(tickPassword, 250); }
function tickPassword() {
    const remaining = Math.ceil((retryUntil - Date.now()) / 1000);
    enter.disabled = remaining > 0 || !password.value || pw.dataset.busy === 'true';
    if (remaining > 0) { pwError.textContent = retryWords(remaining, t); pwError.hidden = false; field.classList.add('bad'); }
    else if (retryUntil) { retryUntil = 0; clearInterval(countdown); pwError.hidden = true; field.classList.remove('bad'); }
}
password.oninput = tickPassword;
pw.onsubmit = async event => {
    event.preventDefault(); if (enter.disabled) return; pw.dataset.busy = 'true'; tickPassword();
    try {
        // The proxy owns the sign-in: a form post, answered by a redirect that sets the device cookie.
        const response = await api.fetchApi('/m/login', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: new URLSearchParams({password: password.value, remember: '1'}).toString()});
        const wait = Number(response.headers.get('X-FreeVideo-Retry-After'));
        if (response.ok) { locked = false; password.value = ''; pwError.hidden = true; field.classList.remove('bad'); renderShell(); await poller.poll(); }
        else if (response.status === 429 && wait > 0) lockFor(wait);
        else if (response.status === 401) { pwError.textContent = t('Incorrect password.', '密码不正确。'); pwError.hidden = false; field.classList.add('bad'); password.focus(); password.select(); }
        else { pwError.textContent = t('Connection lost. Reconnecting…', '连接断了，正在重新连接…'); pwError.hidden = false; }
    } catch { pwError.textContent = t('Connection lost. Reconnecting…', '连接断了，正在重新连接…'); pwError.hidden = false; }
    finally { pw.dataset.busy = 'false'; tickPassword(); }
};
function renderShell() {
    pw.hidden = !locked; top.hidden = main.hidden = tabs.hidden = locked;
    compose.hidden = warning.hidden = locked || result != null || tab !== 'chat'; warning.hidden ||= !warning.textContent;
    viewer.hidden = locked || !result; main.hidden ||= !!result; tabs.hidden ||= !!result;
    offline.hidden ||= locked; toast.hidden = locked || !!result || !toastJob;
    chatsButton.hidden = log.hidden = tab !== 'chat'; generations.hidden = tab !== 'jobs';
    chatTab.setAttribute('aria-selected', String(tab === 'chat')); jobsTab.setAttribute('aria-selected', String(tab === 'jobs'));
}
function show(value) { if (!result) scrollPositions[tab] = main.scrollTop; viewer.querySelector('video')?.pause(); result = null; top.replaceChildren(wordmark(), chatsButton); tab = value; renderShell(); if (value === 'jobs') renderJobs(); main.scrollTop = scrollPositions[value]; }
function choose(session) { drafts.set(selected ?? chat.session, input.value); selected = session; revision++; chat = emptyChat(); input.value = drafts.get(session) || ''; lastSend = null; rows.clear(); log.replaceChildren(); grow(); poller.poll(); }
function grow() { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 134)}px`; send.disabled = sending || !input.value.trim(); }
input.oninput = () => { if (lastSend?.text !== input.value.trim()) lastSend = null; grow(); notice(); };
input.onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); compose.requestSubmit(); } };
compose.onsubmit = async event => {
    event.preventDefault(); const text = input.value.trim(); if (sending || !text) return;
    const body = lastSend?.text === text && lastSend.session === (chat.session ?? null) ? lastSend : {id: newId(), text, session: chat.session ?? null};
    lastSend = body; sending = true; const version = revision; grow(); notice();
    try {
        body.attempts = 0;
        const response = await dataLayer.sendMessage(body, {stopped: () => locked || stopped});
        if (!response?.ok) throw new Error();
        if (version === revision && input.value.trim() === text) { input.value = ''; lastSend = null; }
        await poller.poll();
    } catch { notice(t('Could not send. Please try again.', '没有发送成功，请重试。')); }
    finally { sending = false; grow(); }
};
// Large previews play their strip forward as a loop, 4 to 8 frames a second over the video's length. The position
// follows the clock, so a redraw that builds a new image carries on from the same place; with reduced motion, or in the
// small list picture, the last frame stands still.
const playing = new Set();
let playTimer = null;
const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
function playStrips() {
    playTimer = null;
    for (const item of playing) {
        if (!item.img.isConnected) { playing.delete(item); continue; }
        const index = Math.min(item.count - 1, Math.floor((Date.now() / 1000 * item.fps / item.count) % 1 * item.count));
        item.img.style.transform = `translateX(-${index * 100 / item.count}%)`;
    }
    if (playing.size && !reducedMotion() && typeof requestAnimationFrame === 'function') playTimer = requestAnimationFrame(playStrips);
}
function picture(job, preview = false, play = false) {
    const box = el('div', 'phone-picture');
    const p = job.progress || {}, frame = p.frame_preview;
    const path = preview && frame?.serial > 0 && frame.frames > 0 && frame.frames <= 16 && job.node != null
        ? `/freevideo/frame-preview/${encodeURIComponent(job.node)}?serial=${frame.serial}` : mediaPath(job.thumbnail, 'image');
    if (path) {
        const img = el('img'); img.alt = ''; img.loading = 'lazy'; img.src = path; box.append(img);
        if (preview && frame?.frames > 0) { box.classList.add('strip'); img.style.width = `${frame.frames * 100}%`; img.style.transform = `translateX(-${(frame.frames - 1) * 100 / frame.frames}%)`;
            if (play && frame.frames > 1 && !reducedMotion()) {
                const seconds = frame.duration_ms > 0 ? frame.duration_ms / 1000 : null;
                playing.add({img, count: frame.frames, fps: Math.min(8, Math.max(4, seconds ? frame.frames / seconds : 6))});
                if (playTimer === null && typeof requestAnimationFrame === 'function') playTimer = requestAnimationFrame(playStrips);
            } }
        img.onerror = () => { img.remove(); box.closest('.phone-frame')?.classList.add('no-frame'); };
    }
    return box;
}
const fraction = job => Math.max(0, Math.min(1, Number(job.progress?.fraction) || 0));
function bar(job) { const b = el('div', 'phone-bar'), fill = el('i'); fill.style.width = `${fraction(job) * 100}%`; b.append(fill); b.setAttribute('role', 'progressbar'); b.setAttribute('aria-valuenow', String(Math.floor(fraction(job) * 100))); b.setAttribute('aria-label', job.label || t('Generating', '正在生成')); return b; }
function openJob(job) { if (job.status === 'succeeded') openResult(job); else { show('jobs'); generations.querySelector(`[data-job="${CSS.escape(job.id)}"]`)?.scrollIntoView({block: 'center'}); } }
function mini(job) {
    const b = button('phone-mini', '', () => openJob(jobs[job.id] || job)), body = el('div', 'phone-mini-body');
    body.append(el('div', 'phone-mini-title', job.label || t('Video', '视频')));
    if (job.status === 'succeeded') body.append(el('span', 'phone-link', t('View', '查看')));
    else if (['waiting', 'queued'].includes(job.status)) body.append(el('span', 'phone-mini-meta', t('Queued', '排队中')));
    else if (['failed', 'cancelled'].includes(job.status)) body.append(el('span', 'phone-mini-meta', job.status === 'failed' ? t('Failed', '失败') : t('Stopped', '已停止')));
    else { const meta = el('div', 'phone-mini-meta'); meta.append(el('b', '', `${Math.floor(fraction(job) * 100)}%`), el('span', 'phone-eta', remainingWords(job.progress?.remaining_seconds, t))); body.append(bar(job), meta); }
    b.append(picture(job, true), body); return b;
}
function renderChat() {
    const pinned = main.scrollHeight - main.clientHeight - main.scrollTop < 60;
    for (const entry of chatEntries(chat)) {
        if (lastSend && entry.id === lastSend.id) { lastSend.acknowledged = true; if (input.value.trim() === lastSend.text) { input.value = ''; grow(); } lastSend = null; notice(); }
        const job = jobs[entry.job], key = String(entry.seq), signature = JSON.stringify([entry, phoneJobKey(job), chat.confirms[entry.id]]);
        if (rows.get(key)?.signature === signature) continue;
        const row = el('li');
        if (entry.type === 'message') { row.className = 'phone-message ' + (entry.role === 'user' ? 'user' : 'agent'); row.textContent = entry.text; }
        else if (entry.type === 'job' && job) row.append(mini(job));
        else if (entry.type === 'confirm') {
            row.className = 'phone-confirm'; row.append(el('p', '', entry.question));
            const state = chat.confirms[entry.id] || entry.state;
            if (state === 'approved' || state === 'declined') row.append(el('span', '', state === 'approved' ? t('Approved', '已同意') : t('Declined', '已拒绝')));
            else for (const approve of [true, false]) {
                const b = button('phone-quiet', approve ? t('Start', '开始') : t('Cancel', '取消'), async () => {
                    const version = revision, session = chat.session; row.querySelectorAll('button').forEach(e => e.disabled = true);
                    try { const answer = await json(await dataLayer.confirm({id: entry.id, approve, ...(session ? {session} : {})})); if (revision === version) { chat.confirms[entry.id] = answer.state; renderChat(); } await poller.poll(); }
                    catch { row.querySelectorAll('button').forEach(e => e.disabled = false); notice(t('Please try again.', '请重试。')); }
                }); row.append(b);
            }
        } else continue;
        const old = rows.get(key); if (old) old.element.replaceWith(row); else log.append(row); rows.set(key, {element: row, signature});
    }
    if (pinned && tab === 'chat') main.scrollTop = main.scrollHeight;
}
async function cancel(job, confirm) {
    if (confirm) {
        const dialog = el('dialog', 'fv-phone phone-confirm'); dialog.append(el('p', '', t('Stop this generation?', '停止这个生成？')));
        const actions = el('div', 'phone-confirm-actions');
        const yes = button('phone-quiet', t('Stop', '停止'), async () => { yes.disabled = true; if (await cancel(job, false)) dialog.close(); else yes.disabled = false; });
        const no = button('phone-quiet', t('Keep generating', '继续生成'), () => dialog.close()); actions.append(yes, no); dialog.append(actions); document.body.append(dialog); dialog.onclose = () => dialog.remove(); dialog.showModal(); no.focus(); return;
    }
    try { await json(await dataLayer.cancel(job.id)); await poller.poll(); return true; }
    catch { connection(false); return false; }
}
let jobsSignature = '';
function renderJobs() {
    const signature = JSON.stringify([Object.values(jobs).map(phoneJobKey), limit]); if (signature === jobsSignature) return; jobsSignature = signature;
    const active = document.activeElement?.dataset.action;
    const {running, queued, done} = jobGroups(jobs); generations.replaceChildren();
    if (!running.length && !queued.length && !done.length) generations.append(el('p', 'phone-empty', t('Nothing yet. Tell the Agent what to make in Chat.', '还没有作品，在对话里说想做什么。')));
    if (running.length) generations.append(el('h2', 'phone-section', t('Generating', '正在生成')));
    for (const job of running) {
        const card = el('article', 'phone-running'), frame = el('div', 'phone-frame'), info = el('div', 'phone-info'); card.dataset.job = job.id;
        const img = picture(job, true, true); img.style.aspectRatio = `${job.width || 9}/${job.height || 16}`; frame.append(img); frame.classList.toggle('no-frame', !img.firstChild);
        if (!img.firstChild) frame.append(el('span', 'phone-frame-pct', `${Math.floor(fraction(job) * 100)}%`));
        const row = el('div', 'phone-job-row'), numbers = el('div'); numbers.append(el('span', 'phone-pct', `${Math.floor(fraction(job) * 100)}%`), document.createTextNode(' '), el('span', 'phone-eta', remainingWords(job.progress?.remaining_seconds, t)));
        const stop = button('phone-quiet', t('Stop', '停止'), () => cancel(job, true)); stop.disabled = job.status === 'cancelling'; stop.dataset.action = 'stop-' + job.id; row.append(numbers, stop);
        info.append(el('p', 'phone-prompt', job.label)); if (phonePartWords(job, t)) info.append(el('span', 'phone-eta', phonePartWords(job, t))); info.append(bar(job), row); card.append(frame, info); generations.append(card);
    }
    if (queued.length) generations.append(el('h2', 'phone-section', t('Queued', '排队中')));
    queued.forEach((job, index) => {
        const row = el('div', 'phone-queue'), remove = button('phone-quiet', t('Remove', '移除'), async () => { remove.disabled = true; if (!await cancel(job, false)) remove.disabled = false; }); remove.hidden = true;
        row.tabIndex = 0; row.setAttribute('aria-label', `${job.label}, ${queueWords(index, t)}`); row.dataset.job = job.id;
        row.append(el('i', 'ph'), el('span', 'name', job.label), el('span', 'position', queueWords(index, t)), remove);
        let hold, x, y; const reveal = () => { remove.hidden = false; };
        row.onpointerdown = event => { x = event.clientX; y = event.clientY; hold = setTimeout(reveal, 500); };
        row.onpointermove = event => { if (Math.abs(event.clientY - y) > 12) clearTimeout(hold); if (x - event.clientX > 40 && Math.abs(event.clientY - y) < 25) { clearTimeout(hold); reveal(); } };
        row.onpointerup = row.onpointercancel = row.onlostpointercapture = () => clearTimeout(hold);
        row.oncontextmenu = event => { event.preventDefault(); reveal(); };
        row.onkeydown = event => { if (['Delete', 'Enter', ' '].includes(event.key)) { event.preventDefault(); reveal(); remove.focus(); } };
        generations.append(row);
    });
    if (done.length) generations.append(el('h2', 'phone-section', t('Done', '已完成')));
    // Keep chronological order, grouping only adjacent works of the same orientation.
    let grid, landscape;
    for (const job of done.slice(0, limit)) {
        const wide = job.width > job.height; if (!grid || landscape !== wide) { landscape = wide; grid = el('div', 'phone-grid' + (wide ? ' landscape' : '')); generations.append(grid); }
        const card = button('phone-done', '', () => openResult(job)); card.setAttribute('aria-label', job.label || t('View', '查看')); card.dataset.job = job.id;
        const time = job.finished_at ? new Date(job.finished_at * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', hour12: false}) : '';
        card.append(picture(job), el('span', 'phone-caption', [job.seconds > 0 ? `${job.seconds} s` : '', time].filter(Boolean).join(' · '))); grid.append(card);
    }
    if (active) generations.querySelector(`[data-action="${CSS.escape(active)}"]`)?.focus({preventScroll: true});
}
function openResult(job) {
    scrollPositions[tab] = main.scrollTop; result = job; toastJob = null; clearTimeout(toastTimer); const back = button('phone-back', '', () => show('jobs')); back.setAttribute('aria-label', t('Back', '返回')); back.append(icon('M15 5l-7 7 7 7')); top.replaceChildren(back);
    viewer.replaceChildren(); const stage = el('div', 'phone-viewer'), video = el('video'); video.controls = true; video.playsInline = true; video.preload = 'metadata'; video.src = mediaPath(job.play); const poster = mediaPath(job.thumbnail, 'image'); if (poster) video.poster = poster; stage.append(video);
    const actions = el('div', 'phone-result-actions'), error = el('p', 'phone-error'); error.hidden = true;
    const download = button('phone-primary', t('Download', '下载'), async () => {
        download.disabled = true; error.hidden = true;
        try { await saveVideo(job, {fetch: api.fetchApi, download(path, filename) { const a = el('a'); a.href = path; a.download = filename; document.body.append(a); a.click(); a.remove(); }}); }
        catch { error.textContent = t('Please try again.', '请重试。'); error.hidden = false; }
        finally { download.disabled = false; }
    });
    actions.append(el('p', 'phone-prompt', job.label), download, error); viewer.append(stage, actions); renderShell();
}
function completed(job) {
    toastJob = job; toast.replaceChildren(picture(job), el('span', '', t('Your video is ready', '生成好了')), button('phone-link', t('View', '查看'), () => { show('jobs'); openResult(job); }));
    main.classList.add('toast-space'); renderShell(); clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastJob = null; toast.hidden = true; main.classList.remove('toast-space'); }, 6000);
    clearInterval(flash); let lit = false; flash = setInterval(() => { document.title = (lit = !lit) ? t('Your video is ready', '生成好了') : 'FreeVideo'; }, 1000);
    setTimeout(() => { clearInterval(flash); document.title = 'FreeVideo'; }, 6000);
    if ('Notification' in window) {
        if (document.hidden && Notification.permission === 'granted') { try { const n = new Notification(t('Your video is ready', '生成好了')); n.onclick = () => { window.focus(); show('jobs'); openResult(job); n.close(); }; } catch {} }
        if (!permissionAsked && Notification.permission === 'default') { permissionAsked = true; try { localStorage.setItem('freevideo.phone.notification-asked', '1'); } catch {} Notification.requestPermission().catch(() => {}); }
    }
}
async function refresh() {
    if (stopped || locked) return;
    const version = revision, {after} = chatReadRequest(chat, selected);
    try {
        const [data, activity, progress, now] = await Promise.all([dataLayer.read({session: selected, after}).then(json), dataLayer.activity().then(json), api.fetchApi('/freevideo/progress', {cache: 'no-store'}).then(json), dataLayer.now().then(json)]);
        if (stopped || locked || version !== revision) return;
        if (after && data.session !== chat.session) { drafts.set(chat.session, input.value); input.value = drafts.get(data.session) || ''; grow(); chat = emptyChat(); rows.clear(); log.replaceChildren(); await refresh(); return; }
        if (data.session !== chat.session) { chat = emptyChat(); rows.clear(); log.replaceChildren(); }
        chat = chatMerge(chat, data); const previous = jobs;
        jobs = mergePhoneJobs(jobs, {...chat.jobs, ...(now.current?.id ? {[now.current.id]: now.current} : {})}, activity.jobs || [], progress.progress || []);
        if (!historyStarted) cursor = activity.next_cursor ?? null;
        const running = jobGroups(jobs).running;
        await Promise.all(running.filter(job => job.chain_id).map(async job => {
            const {chain} = await json(await api.fetchApi('/freevideo/chain/' + encodeURIComponent(job.chain_id), {cache: 'no-store'}));
            if (stopped || version !== revision || !chain) return;
            jobs[job.id] = applyPhoneChain(job, chain);
        }));
        if (stopped || version !== revision) return;
        for (const job of Object.values(jobs)) if (job.status === 'succeeded' && previous[job.id] && ['running', 'queued', 'waiting', 'cancelling'].includes(previous[job.id].status)) completed(job);
        connection(true); renderChat(); if (tab === 'jobs') renderJobs();
        const response = await dataLayer.list();
        if (response.ok) { const list = await response.json(); if (version !== revision || stopped) return; convRows = list.conversations || []; convCurrent = selected || chat.session || list.here || ''; if (!sheet.hidden) renderSheet(); }
    } catch { if (!stopped) connection(false); }
}
const poller = chatPoller(refresh, {delay: () => chatDelay({phone: true, hidden: document.hidden})});
main.addEventListener('scroll', async () => {
    if (tab !== 'jobs' || main.scrollTop + main.clientHeight < main.scrollHeight - 150 || loadingMore) return;
    if (jobGroups(jobs).done.length > limit) { limit += 30; renderJobs(); return; }
    if (!cursor) return;
    loadingMore = true;
    try { const data = await json(await dataLayer.activity(cursor)); jobs = mergePhoneJobs(jobs, {}, data.jobs || []); cursor = data.next_cursor ?? null; historyStarted = true; limit += 30; renderJobs(); }
    catch { connection(false); } finally { loadingMore = false; }
}, {passive: true});
document.addEventListener('visibilitychange', () => { if (!document.hidden) { clearInterval(flash); document.title = 'FreeVideo'; poller.poll(); } else poller.reschedule(); });
window.addEventListener('pagehide', event => { if (!event.persisted) { stopped = true; closed.abort(); poller.dispose(); clearInterval(countdown); clearInterval(flash); clearTimeout(toastTimer); } });
window.addEventListener('pageshow', event => { if (event.persisted) poller.poll(); });
renderShell(); poller.poll();

return {refresh: () => poller.poll()};
}
if (document.querySelector('#phone')) mountPhone(document.querySelector('#phone'));
