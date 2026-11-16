// One muted line above Generate: what this request will download first.
export const about = (seconds, t) => seconds >= 60 ? t(`About ${Math.ceil(seconds / 60)} min`, `约 ${Math.ceil(seconds / 60)} 分钟`) : t('Under 1 min', '不到 1 分钟');
// ", about 5 min" after a size, only when a real download speed is known.
export function timeAfter(bytes, rate, t) {
    if (!(bytes > 0) || !(rate > 0)) return '';
    const text = about(bytes / rate, t).replaceAll(' ', '\u00a0'); // "about 2 min" never breaks across lines
    return t(', ' + text.charAt(0).toLowerCase() + text.slice(1), '，' + text);
}
// LoRA file names this request would load, through the whole `previous` chain.
export function enabledLoras(start, {upstream, widget, value}) {
    const names = [];
    for (let current = start, depth = 0; current && depth < 16; current = upstream(current, 'previous'), depth++) {
        try {
            if (widget(current, 'adapters')) {
                for (const row of JSON.parse(value(current, 'adapters') || '[]')) {
                    if (row.enabled !== false && Number(row.strength ?? 1) !== 0 && typeof row.name === 'string') names.push(row.name);
                }
            } else if (widget(current, 'lora')) {
                const name = value(current, 'lora');
                if (name && name !== 'None' && Number(value(current, 'strength') ?? 1) !== 0) names.push(name);
            }
        } catch {}
    }
    return names;
}

// Reference files as `kind:file`, so generation's own rule can tell whether a video has audio.
// A reference that is not a file in the input folder makes the task unknowable here: send none.
export function referenceFiles(refs) {
    return refs.length && refs.every(r => typeof r.file === 'string' && r.kind) ? refs.map(r => `${r.kind === 'video' && r.sound === false ? 'video-silent' : r.kind}:${r.file}`) : [];
}

// Keep the extension and the end of a long file name; shorten the middle.
const short = name => name.length <= 28 ? name
    : name.slice(0, 12) + '…' + name.slice(-15);

export function createRequestAssets({t, api, request, onError = () => {}}) {
    const element = document.createElement('div');
    element.className = 'fv-request-assets fv-muted';
    element.hidden = true;
    element.setAttribute('aria-live', 'polite');
    let timer = 0, controller = null, key = '', disposed = false;
    const size = bytes => bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + ' GB' : Math.max(1, Math.round(bytes / 1e6)) + ' MB';
    function lines(items, rate) {
        const rows = [];
        // One line names what Generate will fetch first: the two-pass model, then the sampling data.
        const names = {upscaler: t('two-pass model', '二采模型'), sampling: t('sampling data', '采样数据')};
        const bytes = {};
        for (const item of items) {
            if (item.action !== 'auto') continue;
            const kind = names[item.kind] ? item.kind : 'other';
            bytes[kind] = (bytes[kind] || 0) + item.bytes;
        }
        const parts = [['upscaler', names.upscaler], ['sampling', names.sampling], ['other', t('other model files', '其他模型文件')]]
            .filter(([kind]) => bytes[kind]).map(([kind, name]) => `${name} ${size(bytes[kind])}`);
        const wait = timeAfter(Object.values(bytes).reduce((sum, n) => sum + n, 0), rate, t);
        if (parts.length) rows.push(t(`Downloads before generating: ${parts.join(', ')}${wait}`, `生成前会先下载：${parts.join('、')}${wait}`));
        for (const item of items) {
            if (item.action === 'unavailable') continue;
            if (item.kind === 'prompt-vlm') {
                const wait = timeAfter(item.bytes, rate, t);
                rows.push(t(`First prompt rewrite downloads ${size(item.bytes)}${wait}`, `首次改写提示词会先下载 ${size(item.bytes)}${wait}`));
            } else if (item.kind === 'lora') {
                const names = (item.names || []).map(short);
                rows.push(t(`LoRA not found: ${names.join(', ')}`, `找不到 LoRA：${names.join('、')}`));
            }
        }
        return rows;
    }
    function show(items, rate = 0) {
        const rows = lines(items, rate);
        element.replaceChildren(...rows.map(text => Object.assign(document.createElement('div'), {textContent: text})));
        element.hidden = rows.length === 0;
    }
    function refresh(force = false) {
        const query = request();
        if (!query) { show([]); return; }
        const next = query.toString();
        if (next === key && !force) return;
        key = next;
        clearTimeout(timer); controller?.abort();
        timer = setTimeout(async () => {
            const current = controller = new AbortController();
            try {
                const reply = await api.fetchApi('/freevideo/request-assets?' + next, {signal: current.signal});
                if (!reply.ok) return;
                const result = await reply.json();
                if (disposed || current.signal.aborted || key !== next) return;
                show(Array.isArray(result.items) ? result.items : [], Number(result.bytes_per_second) || 0);
            } catch (error) { if (error?.name !== 'AbortError') onError(error); } // Advisory; never blocks Generate.
        }, 300);
    }
    return {element, refresh, dispose() { disposed = true; clearTimeout(timer); controller?.abort(); }};
}
