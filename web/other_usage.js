// All occupancy sentences shown by the page live here; numbers join GB with a no-break space.
export const OTHER_USAGE_COPY = Object.freeze({
    gpu: ['Other programs are using {vram_gib}\u00a0GB of GPU memory, so this generation will be slower. Closing them speeds up generation.',
        '其他程序占用了 {vram_gib}\u00a0GB 显存，这次生成会变慢。关闭这些程序可加快生成。'],
    ram: ['Other programs are using {ram_gib}\u00a0GB of RAM, so this generation will be slower. Closing them speeds up generation.',
        '其他程序占用了 {ram_gib}\u00a0GB 内存，这次生成会变慢。关闭这些程序可加快生成。'],
    both: ['Other programs are using {vram_gib}\u00a0GB of GPU memory and {ram_gib}\u00a0GB of RAM, so this generation will be slower. Closing them speeds up generation.',
        '其他程序占用了 {vram_gib}\u00a0GB 显存和 {ram_gib}\u00a0GB 内存，这次生成会变慢。关闭这些程序可加快生成。'],
    gpuFact: ['Other programs are using {vram_gib} GB of GPU memory.', '其他程序占用了 {vram_gib} GB 显存。'],
    ramFact: ['Other programs are using {ram_gib} GB of RAM.', '其他程序占用了 {ram_gib} GB 内存。'],
    bothFact: ['Other programs are using {vram_gib} GB of GPU memory and {ram_gib} GB of RAM.',
        '其他程序占用了 {vram_gib} GB 显存和 {ram_gib} GB 内存。'],
});
export const OTHER_USAGE_STORAGE = 'freevideo.otherUsage.lastNotice';
export function otherUsageText(notice, t, fact = false) {
    if (!notice || !['vram_gib', 'ram_gib'].every(k => Number.isFinite(notice[k]) && notice[k] >= 0)) return '';
    const gpu = notice.kinds?.includes('gpu'), ram = notice.kinds?.includes('ram');
    if (!gpu && !ram) return '';
    const key = (gpu && ram ? 'both' : gpu ? 'gpu' : 'ram') + (fact ? 'Fact' : '');
    return t(...OTHER_USAGE_COPY[key]).replaceAll('{vram_gib}', notice.vram_gib.toFixed(1))
        .replaceAll('{ram_gib}', notice.ram_gib.toFixed(1));
}
export function claimOtherUsage(notice, storage) {
    try {
        storage ??= globalThis.sessionStorage;
        const previous = JSON.parse(storage.getItem(OTHER_USAGE_STORAGE) || 'null');
        if (!notice?.signature || previous?.signature === notice.signature) return false;
        // A floor-bucket boundary alone can be less than 1 GiB of actual change.
        if (previous && Math.abs(previous.vram_gib - notice.vram_gib) < 1
            && Math.abs(previous.ram_gib - notice.ram_gib) < 1) return false;
        storage.setItem(OTHER_USAGE_STORAGE, JSON.stringify(notice));
        return true;
    } catch { return false; } // Cannot persist the once-only rule: stay quiet.
}
export async function readOtherUsage(query, fetchApi = globalThis.fetch.bind(globalThis)) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 750);
    try {
        // Race also bounds adapters that ignore AbortSignal. No advisory can hold submission.
        return await Promise.race([
            (async () => {
                const response = await fetchApi('/freevideo/other-usage?' + new URLSearchParams(query),
                    {cache: 'no-store', signal: controller.signal});
                const value = response.ok ? await response.json() : null;
                return value?.status === 'ok' ? value.notice : null;
            })(),
            new Promise(resolve => controller.signal.addEventListener('abort', () => resolve(null), {once: true})),
        ]);
    } catch { return null; }
    finally { clearTimeout(timeout); }
}
export function createOtherUsageNotice(host, t, fetchApi) {
    const element = (host.ownerDocument || document).createElement('div');
    element.className = 'fv-muted fv-other-usage'; element.hidden = true; element.setAttribute('role', 'status');
    host.append(element);
    let revision = 0;
    return {element, async check(query) {
        const current = ++revision;
        element.hidden = true; element.textContent = '';
        if (!query) return;
        const notice = await readOtherUsage(query, fetchApi);
        if (current !== revision || !element.isConnected) return;
        const text = otherUsageText(notice, t);
        if (text && claimOtherUsage(notice)) { element.textContent = text; element.hidden = false; }
    }, dispose() { revision++; element.remove?.(); }};
}
