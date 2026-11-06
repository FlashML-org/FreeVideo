// The dubbing model that changes a voice, downloaded from the place where the voice would be changed: a stretch of
// speech is chosen, the model is not there, the bar offers "Download" and then shows the progress. The engine does the
// download (resumable, cancellable); this keeps the page's side of it: what to say, the progress and the one job.
// The first half is pure (sentences, percent); the second half talks to the engine's /freevideo/dubbing routes.
import {issueButton} from './issue_button.js';

const ROOT = '/freevideo/dubbing';
const NBSP = ' ';
const DEFAULT_BYTES = 4.9e9;
const JOB_KEY = 'fv-dubbing-install-job';

// "about 4.9 GB": the size the engine reports for what is still missing, the full model when it reports nothing.
export const sizeText = bytes => `${((Number.isFinite(bytes) && bytes > 0 ? bytes : DEFAULT_BYTES) / 1e9).toFixed(1)}${NBSP}GB`;

// 0..99 while the engine works (100 is only the finished model); null when the engine has not said how far it is.
export function percent(row) {
    if (!row || !(row.total > 0) || !Number.isFinite(row.done)) return null;
    return Math.max(0, Math.min(99, Math.floor(row.done / row.total * 100)));
}

export const offerText = (bytes, t) => t(`Voice change needs the dubbing model (about ${sizeText(bytes)}).`, `换人声需要配音模型，约 ${sizeText(bytes)}。`);
export const progressText = (value, t) => value == null ? t('Downloading the dubbing model', '正在下载配音模型')
    : t(`Downloading the dubbing model ${value}%`, `正在下载配音模型 ${value}%`);

// What a failed download says; the code never reaches the words. `report` is whether the problem can be reported.
export function failureText(code, bytes, t) {
    switch (code) {
        case 'disk_space': return {report: true, text: t(`There is not enough disk space. The dubbing model needs about ${sizeText(bytes)}.`, `磁盘空间不足，配音模型需要约 ${sizeText(bytes)}。`)};
        case 'download_failed': case 'network': return {report: true, text: t('The dubbing model could not be downloaded. Check the connection and try again.', '配音模型下载失败，请检查网络后重试。')};
        default: return {report: true, text: t('The dubbing model could not be installed. Try again.', '配音模型未能安装，请重试。')};
    }
}

const readJob = storage => { try { return storage?.getItem(JOB_KEY) || null; } catch { return null; } };
const writeJob = (storage, value) => { try { value ? storage?.setItem(JOB_KEY, value) : storage?.removeItem(JOB_KEY); } catch { /* the job is simply not remembered */ } };
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

/**
 * The download of the dubbing model. `available()` says whether the engine offers it (capabilities); `target(selection)`
 * whether the chosen part is a stretch of speech; `onReady()` asks the page to read the capabilities again and returns
 * whether the model is there now. A download that runs when the page opens is taken up again: by the job this browser
 * remembers, else only as "downloading" (the engine does not hand its job to anyone else).
 */
export function createVoiceInstall({api, available = () => true, target = () => true, onChange = () => {}, onReady = async () => false,
    storage = globalThis.localStorage, delay = wait, interval = 1000} = {}) {
    const view = {phase: 'loading', percent: null, cancelling: false, error: null, bytes: 0};
    let token = '', job = readJob(storage), disposed = false, polling = false, initial = null;
    const set = patch => { Object.assign(view, patch); if (!disposed) onChange(); };
    const read = async response => { try { return await response.json(); } catch { return null; } };
    const info = async () => {
        const response = await api.fetchApi(ROOT, {cache: 'no-store'});
        const value = await read(response);
        if (!response.ok || !value || value.error) throw new Error(value?.error || 'unavailable');
        token = value.token || token;
        return value;
    };
    async function call(action, body, again = true) {
        const response = await api.fetchApi(`${ROOT}/${action}`, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-FreeVideo-Dubbing': token}, body: JSON.stringify(body)});
        const value = await read(response);
        // A restarted engine has a new token: ask for it and send the same call once more.
        if (response.status === 403 && again) { await info(); return call(action, body, false); }
        if (!response.ok) throw Object.assign(new Error(value?.error || 'failed'), {code: value?.error || 'failed'});
        return value;
    }
    const remember = value => { const moved = job !== value; job = value; writeJob(storage, value); if (moved && !disposed) onChange(); };
    async function finish() {
        remember(null);
        let ready = false;
        try { ready = await onReady(); } catch { /* the capabilities are read again with the next page */ }
        if (!disposed) set({phase: ready ? 'ready' : 'idle', percent: null, cancelling: false, error: null});
    }
    function fail(code, keep = false) { if (!keep) remember(null); set({phase: 'failed', error: code, cancelling: false, percent: null}); }
    // The engine's answer for the job; false once the job is over.
    function apply(row) {
        switch (row?.phase) {
            case 'complete': return false;
            case 'failed': fail(row.error || 'failed'); return null;
            case 'cancelled': remember(null); set({phase: 'idle', percent: null, cancelling: false}); return null;
            case 'download': case 'unpack': set({percent: percent(row)}); return true;
            default: return true;
        }
    }
    async function poll() {
        if (polling) return;
        polling = true;
        let lost = 0;
        try {
            while (!disposed && view.phase === 'downloading') {
                try {
                    if (job) {
                        const outcome = apply(await call('status', {job}));
                        if (outcome === false) return await finish();
                        if (outcome === null) return;
                    } else {
                        // Not this page's job: only whether the engine is still at it.
                        const value = await info();
                        if (!value.busy) { if (value.ready) return await finish(); set({phase: 'idle'}); return; }
                    }
                    lost = 0;
                } catch (error) {
                    if (error.code === 'unknown_job') { remember(null); continue; }
                    // The engine cannot be reached: the job is kept, so Retry takes up the same download.
                    if (++lost >= 10) return fail('network', true);
                }
                await delay(interval);
            }
        } finally { polling = false; }
    }
    async function load() {
        try {
            const value = await info();
            set({bytes: value.bytes});
            if (value.ready) { set({phase: 'ready'}); await finish(); return; }
            if (value.supported === false || value.published === false) { set({phase: 'unsupported'}); return; }
            if (value.busy) { set({phase: 'downloading', percent: null}); poll(); return; }
            remember(null); set({phase: 'idle'});
        } catch { set({phase: 'unsupported'}); }
    }
    return {
        view,
        init() { return initial || (initial = load()); },
        // Whether the engine offers the download at all (the panel exists only then).
        offered() { return !disposed && available() && ['idle', 'downloading', 'failed'].includes(view.phase); },
        // Whether the bar offers the download for this selection: the engine offers it and a stretch of speech is chosen.
        visible(selection) { return !disposed && available() && ['idle', 'downloading', 'failed'].includes(view.phase) && !!target(selection); },
        async start() {
            if (view.phase === 'downloading') return;
            set({phase: 'downloading', percent: 0, cancelling: false, error: null});
            try {
                if (!token) await info();
                const value = await call('install', {accept_download: true});
                remember(value.job);
                poll();
            } catch (error) {
                if (error.code !== 'busy') return fail(error.code || 'network');
                // The engine says it is busy: it is downloading already.
                try {
                    const value = await info();
                    if (value.busy) { set({percent: null}); poll(); } else fail('failed');
                } catch { fail('network'); }
            }
        },
        async cancel() {
            if (view.phase !== 'downloading' || !job || view.cancelling) return;
            set({cancelling: true});
            try { await call('cancel', {job}); } catch (error) { if (error.code === 'unknown_job') { remember(null); set({phase: 'idle', cancelling: false, percent: null}); } else set({cancelling: false}); }
        },
        hasJob: () => !!job,
        dispose() { disposed = true; },
    };
}

const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

// The panel in the generation bar, where the lines would be: a sentence, and one button; or the progress and Cancel.
export function createInstallPanel(host, install, {t, selection}) {
    const element = el('div', 'fv-ed-voice-install'), note = el('div', 'fv-ed-voice-install-note'), bar = el('div', 'fv-ed-voice-bar'), fill = el('span');
    const actions = el('div', 'fv-actions');
    const make = (label, onclick, cls = '') => { const b = el('button', `fv-action ${cls}`.trim(), label); b.type = 'button'; b.onclick = onclick; return b; };
    const download = make(t('Download', '下载'), () => install.start(), 'fv-primary'), retry = make(t('Retry', '重试'), () => install.start(), 'fv-primary');
    const cancel = make(t('Cancel', '取消'), () => install.cancel());
    note.setAttribute('role', 'status'); bar.setAttribute('role', 'progressbar'); bar.setAttribute('aria-valuemin', '0'); bar.setAttribute('aria-valuemax', '100');
    bar.append(fill); actions.append(download, retry, cancel); element.append(note, bar, actions); element.hidden = true; host.append(element);
    let shownText = null;
    return {element, shown: () => !element.hidden, update() {
        const view = install.view, shown = install.visible(selection());
        element.hidden = !shown;
        if (!shown) return;
        const state = view.phase === 'downloading' ? 'downloading' : view.phase === 'failed' ? 'failed' : 'offer';
        element.dataset.state = state;
        const failure = state === 'failed' ? failureText(view.error, view.bytes, t) : null;
        const text = state === 'downloading' ? progressText(view.percent, t) : failure ? failure.text : offerText(view.bytes, t);
        if (shownText !== text) {
            note.replaceChildren(text); shownText = text;
            if (failure?.report) note.append(issueButton(t, () => ({where: ['Dubbing model download', '配音模型下载'], message: failure.text, kind: view.error || null})));
        }
        bar.hidden = state !== 'downloading';
        if (state === 'downloading') {
            if (view.percent == null) { bar.removeAttribute('aria-valuenow'); bar.dataset.indeterminate = 'true'; fill.style.width = ''; } else { bar.setAttribute('aria-valuenow', String(view.percent)); delete bar.dataset.indeterminate; fill.style.width = view.percent + '%'; }
        }
        download.hidden = state !== 'offer'; retry.hidden = state !== 'failed';
        // Without the job (a download another page started) there is nothing to stop from here.
        cancel.hidden = state !== 'downloading' || !install.hasJob?.(); cancel.disabled = view.cancelling;
    }, dispose() { element.remove(); }};
}
