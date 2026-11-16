// Whether a video or picture that leaves this machine carries its prompt and ComfyUI workflow (which also names the
// files it read). Off unless the user turns it on; the library's own file always keeps both. An engine older than this
// page ignores the choice and sends the file as it is.
const WORKFLOW_KEY = 'freevideo.export.workflow';
export function exportKeepsWorkflow() { try { return localStorage.getItem(WORKFLOW_KEY) === '1'; } catch { return false; } }
export function setExportKeepsWorkflow(on) {
    try { if (on) localStorage.setItem(WORKFLOW_KEY, '1'); else localStorage.removeItem(WORKFLOW_KEY); } catch { /* this page only */ }
}
export function workflowToggle(t, onChange = () => {}) {
    const label = document.createElement('label'), box = document.createElement('input');
    label.className = 'fv-export-workflow'; box.type = 'checkbox'; box.checked = exportKeepsWorkflow();
    box.onchange = () => { setExportKeepsWorkflow(box.checked); onChange(box.checked); };
    label.append(box, t('Include prompt and workflow', '包含提示词和工作流（便于在 ComfyUI 中还原）'));
    label.input = box;
    return label;
}

// The server supplies the download name and streams the existing file, keeping
// large videos out of a browser-side Blob and preserving playback/asset paths.
export function outputDownloadPath(file) {
    const saved = /^FreeVideo\/(\d{4}-\d{2}-\d{2}\/[a-f0-9]{32})\/video\.mp4$/.exec(file);
    if (saved) return '/freevideo/library/download?' + new URLSearchParams({id: saved[1], ...(exportKeepsWorkflow() ? {workflow: '1'} : {})});
    const split = file.lastIndexOf('/');
    return '/view?' + new URLSearchParams({filename: file.slice(split + 1), subfolder: split < 0 ? '' : file.slice(0, split), type: 'output'});
}
export const outputDownloadURL = (api, file) => api.apiURL(outputDownloadPath(file));

// 'ok'; 'missing' only when the server answered 404 or 410; 'unknown' when offline, timed out or any other answer.
export async function fileState(api, path) {
    const gone = status => status === 404 || status === 410;
    let missing = false;
    try {
        const head = await api.fetchApi(path, {method: 'HEAD', signal: AbortSignal.timeout(10000)});
        if (head.ok) return 'ok';
        missing = gone(head.status);
    } catch { /* HEAD can fail even when the browser can still read the video. */ }
    try {
        const response = await api.fetchApi(path, {method: 'GET', headers: {Range: 'bytes=0-0'},
            signal: AbortSignal.timeout(10000)});
        // A server may ignore Range. Release its stream without downloading the video.
        await response.body?.cancel().catch(() => {});
        return response.ok ? 'ok' : gone(response.status) ? 'missing' : 'unknown';
    } catch { return missing ? 'missing' : 'unknown'; }
}

// The one sentence for a saved video that cannot be read, on every surface that offers it.
export const unreadableVideoText = t => t('This saved video cannot be read.', '无法读取这个已保存的视频。');

// A short status line by `anchor` that takes no room in any layout: it is fixed to the viewport at the
// anchor's own position, above it by default and below it when what lies above is data or text. It hangs
// from the anchor's dialog (a modal dialog covers the rest of the page) or the body, never from the row,
// so no scroll area or clip around the row grows, scrolls or cuts it. It is a polite live region. It
// stays while the pointer rests on it or the anchor, goes after `ms` otherwise, goes at once on Escape,
// a press elsewhere, a resize or a scroll, and a second call restarts it.
const notices = new WeakMap();
export function anchoredNotice(anchor, message, {below = false, ms = 5000} = {}) {
    notices.get(anchor)?.close();
    const root = anchor.closest?.('dialog') || document.body, gap = 6, margin = 8;
    const note = document.createElement('span'); note.className = 'fv-download-notice'; note.setAttribute('role', 'status');
    note.setAttribute('aria-live', 'polite'); note.textContent = message;
    Object.assign(note.style, {position: 'fixed', zIndex: '2147483000', padding: '6px 11px', borderRadius: '8px', whiteSpace: 'nowrap',
        fontSize: '12px', fontWeight: '500', lineHeight: '1.3', color: 'var(--fv-ink, #eee)', background: 'var(--fv-raised, #2a2f3a)',
        border: '1px solid color-mix(in srgb, var(--fv-ink, #eee) 16%, transparent)', boxShadow: '0 4px 14px rgba(0, 0, 0, .35)'});
    root.append(note);
    const place = () => {
        const rect = anchor.getBoundingClientRect(), width = note.offsetWidth || 0, height = note.offsetHeight || 0;
        const wide = window.innerWidth || 1000, high = window.innerHeight || 1000;
        let top = below ? rect.bottom + gap : rect.top - gap - height;
        if (height && (top < margin || top + height > high - margin)) top = below ? rect.top - gap - height : rect.bottom + gap;  // no room: the other side
        note.style.left = Math.max(margin, Math.min(rect.left, wide - width - margin)) + 'px';
        note.style.top = Math.max(0, top) + 'px'; note.dataset.placement = below ? 'below' : 'above';
    };
    place();
    let timer = 0;
    // A click leaves the pointer on the anchor, so no pointerenter will follow: ask where the pointer is.
    const resting = () => { try { return Boolean(anchor.matches?.(':hover') || note.matches?.(':hover')); } catch { return false; } };
    const start = () => { clearTimeout(timer); if (!resting()) timer = setTimeout(close, ms); };
    const hold = () => clearTimeout(timer);
    const onKey = event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); } };
    const onPress = event => { if (!note.contains?.(event.target) && !anchor.contains?.(event.target)) close(); };
    function close() {
        clearTimeout(timer); note.remove();
        document.removeEventListener('keydown', onKey, true); document.removeEventListener('pointerdown', onPress, true);
        window.removeEventListener?.('resize', close); window.removeEventListener?.('scroll', close, true); window.removeEventListener?.('wheel', close, true);
        for (const part of [note, anchor]) { part.removeEventListener?.('pointerenter', hold); part.removeEventListener?.('pointerleave', start); }
        if (notices.get(anchor)?.close === close) notices.delete(anchor);
    }
    for (const part of [note, anchor]) { part.addEventListener?.('pointerenter', hold); part.addEventListener?.('pointerleave', start); }
    document.addEventListener?.('keydown', onKey, true); document.addEventListener?.('pointerdown', onPress, true);
    window.addEventListener?.('resize', close); window.addEventListener?.('scroll', close, true); window.addEventListener?.('wheel', close, true);
    notices.set(anchor, {close}); start();
}

// A plain download link does nothing visible when the file is gone. Check it when clicked:
// a missing file calls `missing()` instead of downloading; anything else downloads as before,
// including a check that could not finish, because the browser then reports its own result.
export function checkedDownload(api, link, file, {missing, available = () => {}}) {
    let passing = false;
    link.onclick = async event => {
        if (passing) { passing = false; return; }
        event.preventDefault();
        if (link.getAttribute('aria-busy') === 'true') return;
        link.setAttribute('aria-busy', 'true');
        let state;
        try { state = await fileState(api, outputDownloadPath(file)); }
        finally { link.removeAttribute('aria-busy'); }
        if (state === 'missing') { missing(); return; }
        available();
        passing = true; link.click();
    };
}
