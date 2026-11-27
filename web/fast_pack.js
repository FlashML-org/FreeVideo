// The Fast level's files, downloaded on request into the model folder (GET/POST /freevideo/fast-pack).
// The level stays listed without them; choosing it offers the download, and the level in use does not change.
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
// As the editor writes a download's size (editor_genbar.js).
const size = bytes => bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(0, Math.round((bytes || 0) / 1e6))} MB`;
const POLL_MS = 500, DONE_MS = 4000;

function failureText(reason, t) {
    return t(...({
        offline: ['The network is unavailable, so the Fast level could not be downloaded. Connect to the network and try again.', '网络未连接，无法下载极速档。请连接网络后重试。'],
        source_failed: ['The Fast level did not finish downloading. Check the network and try again.', '极速档未能下载完成，请检查网络后重试。'],
        integrity_failed: ['The downloaded Fast level did not pass verification. Try again.', '下载的极速档未通过校验，请重试。'],
        write_failed: ['The Fast level could not be saved to disk. Check the disk and try again.', '极速档未能保存到磁盘，请检查磁盘后重试。'],
        disk_space: ['Not enough disk space to download the Fast level. Free up space and try again.', '磁盘空间不足，无法下载极速档。请释放空间后重试。'],
        busy: ['The Fast level is already downloading. Try again shortly.', '极速档正在下载，请稍后重试。'],
        not_published: ['The Fast level files are not published yet, so they cannot be downloaded. Switched back to Light.', '极速档的文件尚未发布，目前无法下载。已改回轻量档。'],
    }[reason] || ['The Fast level did not finish downloading. Try again.', '极速档未能下载完成，请重试。']));
}

export function fastPackControl({t, api, onChange = () => {}, onResize = () => {}}) {
    // The download line's styles apply inside .fv-swap-on.
    const element = el('div', 'fv-swap-on fv-fast-pack');
    const box = el('div', 'fv-swap-download'); box.hidden = true; box.setAttribute('role', 'status');
    const line = el('div', 'fv-swap-download-line'), text = el('span', 'fv-swap-download-text');
    const action = el('button', 'fv-swap-retry'); action.type = 'button';
    const meter = el('div', 'fv-swap-meter'), fill = el('i'); meter.append(fill);
    line.append(text, action); box.append(line, meter); element.append(box);
    // published: null until the first answer, then whether the files are on the download sources (GET .published).
    let install = {state: 'idle'}, present = false, published = null, bytes = 0, asked = false, doneTimer = 0, timer = 0, disposed = false;
    // Each request is numbered; a state read before the last Download or Cancel answers an earlier moment and is dropped.
    let sent = 0, acted = 0;
    const running = () => install.state === 'running';
    const needs = () => bytes ? t('Needs a download of about {size}', '需下载约 {size}', { size: size(bytes) }) : '';
    // The line stands beside another level, so it names this one.
    const needsLine = () => bytes ? t('Fast level download: about {size}, one time only.', '极速档需下载约 {size}，只需下载一次。', { size: size(bytes) }) : '';
    function paint() {
        const failed = install.state === 'failed', done = Boolean(doneTimer);
        // A failure is the server's state until the next download; it is shown only where this page asked for one.
        box.hidden = present ? !done : !(running() || (failed && asked) || (asked && bytes));
        box.dataset.state = install.state;
        meter.hidden = !running();
        // Retrying cannot fetch files that are not published.
        action.hidden = done || present || (failed && install.reason === 'not_published');
        action.textContent = running() ? t('Cancel', '取消') : failed ? t('Retry', '重试') : t('Download', '下载');
        const total = install.total || 0;
        meter.dataset.indeterminate = String(!total);
        fill.style.width = total ? `${Math.min(100, 100 * (install.done || 0) / total).toFixed(1)}%` : '';
        text.textContent = done ? t('The Fast level is ready.', '极速档已就绪。')
            : running() ? (total ? t('Downloading Fast · {done} / {total}', '正在下载极速档 · {done} / {total}', { done: size(install.done), total: size(total) })
                : t('Downloading Fast…', '正在下载极速档…'))
            : failed ? failureText(install.reason, t) : needsLine();
        onResize();  // the line changes the height of the window it sits in
    }
    function take(value) {
        const before = install.state;
        install = value?.install || {state: 'idle'};
        present = value?.present === true; bytes = value?.bytes ?? bytes;  // what is still missing, from the catalog
        published = value?.published !== false;
        if (install.state === 'cancelled') install = {state: 'idle'};
        if (install.state === 'done' && before === 'running') {
            clearTimeout(doneTimer); doneTimer = setTimeout(() => { doneTimer = 0; asked = false; paint(); }, DONE_MS);
        }
        paint(); onChange();
        clearTimeout(timer);
        if (running() && !disposed) timer = setTimeout(poll, POLL_MS);
    }
    async function call(options) {
        const response = await api.fetchApi('/freevideo/fast-pack', {cache: 'no-store', ...options});
        if (!response.ok) throw new Error(response.statusText);
        return response.json();
    }
    const post = body => call({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    async function poll() {
        if (disposed) return;
        const n = ++sent;
        try { const value = await call(); if (!disposed && n > acted) take(value); }
        catch {
            if (disposed || n <= acted) return;
            if (running()) timer = setTimeout(poll, POLL_MS * 4);
            // Without an answer the files are not known to be here: the page does not send the level.
            else { present = false; onChange(); }
        }
    }
    action.onclick = async () => {
        acted = ++sent; asked = true;
        if (running()) { try { const value = await post({action: 'cancel'}); acted = ++sent; if (!disposed) take(value); } catch {} return; }
        install = {state: 'running', done: 0, total: 0}; paint();
        try { const value = await post({action: 'install'}); acted = ++sent; if (!disposed) take(value); }
        catch { if (!disposed) { install = {state: 'failed', reason: 'failed'}; paint(); } }
    };
    return {
        element,
        get present() { return present; },
        get published() { return published; },
        // What the level's option says now: its download while the files are missing.
        needs: () => present ? '' : needs(),
        refresh: poll,
        // The level was chosen without its files: show the download; nothing starts until it is pressed.
        ask() { asked = true; paint(); },
        // Another level was chosen: the download line and a failure are no longer about what is in use.
        forget() { if (!asked || running()) return; asked = false; paint(); },
        dispose() { disposed = true; clearTimeout(timer); clearTimeout(doneTimer); },
    };
}
