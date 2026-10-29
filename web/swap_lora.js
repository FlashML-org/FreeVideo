// Replace person's model, downloaded on demand into the LoRA folder (GET/POST /freevideo/swap-lora).
// The download starts on its own once the option is on and the model is not found (about 80 MB, part of the
// option, like the upscaler that a two-pass run fetches); progress, failure and retry show in the panel.
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const mb = bytes => `${Math.max(0, Math.round((bytes || 0) / 1e6))} MB`;
const POLL_MS = 500, DONE_MS = 4000;

export function swapLoraFailureText(reason, t) {
    const text = {
        offline: ['The network is unavailable, so the Replace person model could not be downloaded.', '网络不通，无法下载替换人物模型。'],
        source_failed: ["The Replace person model download didn't finish. Check the network and try again.", '替换人物模型没有下载完成，请检查网络后重试。'],
        integrity_failed: ['The downloaded Replace person model did not pass verification. Try again.', '下载的替换人物模型未通过校验，请重试。'],
        write_failed: ['The Replace person model could not be saved to disk.', '替换人物模型无法保存到磁盘。'],
        disk_space: ['Not enough disk space to download the Replace person model.', '磁盘空间不足，无法下载替换人物模型。'],
        busy: ['The Replace person model is being downloaded elsewhere. Try again shortly.', '替换人物模型正在别处下载，请稍后重试。'],
    }[reason] || ["The Replace person model download didn't finish. Try again.", '替换人物模型没有下载完成，请重试。'];
    return t(...text);
}

// onReady runs once a download has finished, so the caller can add the model to the run.
export function swapLoraDownload({t, api, onReady = () => {}}) {
    const box = el('div', 'fv-swap-download'); box.hidden = true; box.setAttribute('role', 'status');
    const line = el('div', 'fv-swap-download-line'), text = el('span', 'fv-swap-download-text');
    const retry = el('button', 'fv-swap-retry', t('Retry', '重试')); retry.type = 'button'; retry.hidden = true;
    const meter = el('div', 'fv-swap-meter'), fill = el('i'); meter.append(fill);
    line.append(text, retry); box.append(line, meter);
    let install = {state: 'idle'}, timer = 0, doneTimer = 0, disposed = false;
    const running = () => install.state === 'running';
    function paint() {
        const failed = install.state === 'failed' || install.state === 'cancelled', done = install.state === 'done';
        box.hidden = !(running() || failed || (done && doneTimer));
        box.dataset.state = install.state;
        retry.hidden = !failed; meter.hidden = !running();
        const total = install.total || 0;
        meter.dataset.indeterminate = String(!total);
        fill.style.width = total ? `${Math.min(100, 100 * (install.done || 0) / total).toFixed(1)}%` : '';
        text.textContent = running()
            ? (total ? t(`Downloading the Replace person model · ${mb(install.done)} / ${mb(total)}`, `正在下载替换人物模型 · ${mb(install.done)} / ${mb(total)}`)
                : t('Downloading the Replace person model…', '正在下载替换人物模型…'))
            : failed ? swapLoraFailureText(install.reason, t)
            : t('The Replace person model is ready.', '替换人物模型已就绪。');
    }
    function take(value) {
        const before = install.state;
        install = value?.install || {state: 'idle'};
        if (value?.present && !running() && install.state !== 'done') install = {state: 'idle'};
        if (install.state === 'done' && before === 'running') {
            clearTimeout(doneTimer); doneTimer = setTimeout(() => { doneTimer = 0; paint(); }, DONE_MS);
            paint(); onReady();
        } else paint();
        clearTimeout(timer);
        if (running() && !disposed) timer = setTimeout(poll, POLL_MS);
    }
    async function call(options) {
        const response = await api.fetchApi('/freevideo/swap-lora', {cache: 'no-store', ...options});
        if (!response.ok) throw new Error(response.statusText);
        return response.json();
    }
    async function poll() {
        if (disposed) return;
        try { take(await call()); }
        catch { if (!disposed) timer = setTimeout(poll, POLL_MS * 4); }
    }
    async function start() {
        install = {state: 'running', done: 0, total: 0}; paint();
        try { take(await call({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'install'})})); }
        catch { install = {state: 'failed', reason: 'failed'}; paint(); }
    }
    retry.onclick = () => start();
    return {
        element: box,
        // The model is not among the LoRAs: start the download unless it runs, or ended in a failure the panel shows
        // (retry: start again, for a Generate click or the option switched on). Returns the line a Generate click shows.
        async need(retry = false) {
            const failed = install.state === 'failed' || install.state === 'cancelled';
            if (!running() && (retry || !failed)) await start();
            return running() ? t('The Replace person model is still downloading. Generate when it finishes.', '替换人物模型正在下载，下载完成后才能生成。')
                : install.state === 'failed' || install.state === 'cancelled' ? swapLoraFailureText(install.reason, t) : '';
        },
        // True while the panel shows the download or its failure, so no second line repeats it.
        get shown() { return !box.hidden && install.state !== 'done'; },
        dispose() { disposed = true; clearTimeout(timer); clearTimeout(doneTimer); },
    };
}
