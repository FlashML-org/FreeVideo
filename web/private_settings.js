// Settings › Privacy. FreeVideo's own uploads and pictures that nothing uses any more, removed when the user confirms
// the list; and pictures earlier versions put in ComfyUI's input folder under the user's own file names, which cannot
// be told apart from the user's own files, so each one goes only when the user chooses it.
const amount = bytes => bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + ' GB' : (bytes / 1e6).toFixed(1) + ' MB';

export function privacySettings(t, api) {
    const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
    const json = async (path, body) => {
        const response = await api.fetchApi(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        const value = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(value.error || 'failed');
        return value;
    };
    // A second click within a few seconds confirms; the button says what the second click does.
    const confirming = (button, idle, ask, act) => {
        let armed = 0;
        button.onclick = async () => {
            if (!armed) { button.textContent = ask; armed = setTimeout(() => { armed = 0; button.textContent = idle; }, 5000); return; }
            clearTimeout(armed); armed = 0; button.textContent = idle; button.disabled = true;
            try { await act(); } finally { button.disabled = false; }
        };
    };
    const section = el('section'); section.dataset.section = 'privacy';
    section.append(el('h3', '', t('Privacy', '隐私')));

    const row = el('div', 'fv-storage-row'), size = el('span'), clean = el('button', '', t('Clean up', '清理'));
    size.setAttribute('role', 'status'); clean.type = 'button'; clean.disabled = true;
    const end = el('div', 'fv-storage-end'); end.append(size, clean);
    row.append(el('span', '', t('Uploads and pictures no longer used', '不再使用的上传和图片')), end);
    let tokens = [];
    const review = async () => {
        try {
            const value = await json('/freevideo/private/review');
            tokens = (value.items || []).map(item => item.token);
            size.textContent = tokens.length ? t(`${tokens.length} files, ${amount(value.bytes || 0)}`, `${tokens.length} 个文件，共 ${amount(value.bytes || 0)}`)
                : t('Nothing to clean up', '没有可清理的文件');
        } catch { tokens = []; size.textContent = t('The list cannot be read now.', '暂时无法读取清单。'); }
        clean.disabled = !tokens.length;
    };
    confirming(clean, t('Clean up', '清理'), t('Confirm clean up', '确认清理'), async () => {
        try {
            const value = await json('/freevideo/private/clean', {tokens});
            size.textContent = t(`Removed ${value.removed} files, ${amount(value.freed_bytes || 0)}`, `已删除 ${value.removed} 个文件，共 ${amount(value.freed_bytes || 0)}`);
        } catch { size.textContent = t('The clean up did not finish. Try again.', '清理没有完成，请再试一次。'); }
        await new Promise(done => setTimeout(done, 1500)); await review();
    });

    const old = el('details', 'fv-private-old');
    old.append(el('summary', '', t('Clean up old reference pictures', '清理旧参考图')),
        el('p', 'fv-muted', t('Pictures in ComfyUI’s input folder. Earlier versions saved reference pictures here under their own file names, which cannot be told apart from your own files, so each one is deleted only when you choose it.',
            'ComfyUI 输入文件夹中的图片。旧版本曾把参考图以原文件名存放在这里，无法与您自己的文件区分，因此只在您逐个选择后删除。')));
    const grid = el('div', 'fv-private-grid'), oldStatus = el('div', 'fv-muted fv-private-status'); oldStatus.setAttribute('role', 'status');
    old.append(grid, oldStatus);
    const view = name => api.apiURL('/view?' + new URLSearchParams({filename: name, subfolder: '', type: 'input'}));
    const listOld = async () => {
        grid.replaceChildren(); oldStatus.textContent = '';
        let items = [];
        try { items = (await json('/freevideo/private/legacy')).items || []; } catch { oldStatus.textContent = t('The list cannot be read now.', '暂时无法读取清单。'); return; }
        if (!items.length) { oldStatus.textContent = t('No pictures in the input folder.', '输入文件夹中没有图片。'); return; }
        for (const item of items) {
            const cell = el('figure', 'fv-private-cell'), image = el('img'), caption = el('figcaption', '', item.name);
            image.src = view(item.name); image.alt = ''; image.loading = 'lazy'; caption.title = item.name;
            const remove = el('button', 'fv-quiet', t('Delete', '删除')); remove.type = 'button';
            confirming(remove, t('Delete', '删除'), t('Confirm', '确认删除'), async () => {
                try {
                    const value = await json('/freevideo/private/legacy-delete', {name: item.name});
                    if (value.removed) cell.remove();
                    else oldStatus.textContent = value.reason === 'in-use' ? t('This picture is still used by a work or workflow, so it was kept.', '这张图片仍被作品或工作流使用，已保留。')
                        : t('This picture could not be deleted.', '这张图片无法删除。');
                } catch { oldStatus.textContent = t('This picture could not be deleted.', '这张图片无法删除。'); }
                if (!grid.childElementCount) oldStatus.textContent = t('No pictures in the input folder.', '输入文件夹中没有图片。');
            });
            cell.append(image, caption, el('span', 'fv-muted', amount(item.bytes || 0)), remove);
            grid.append(cell);
        }
    };
    old.addEventListener('toggle', () => { if (old.open) void listOld(); });

    // Encodings kept so a repeated prompt or picture is faster: their size, how much they may use, and one click to clear.
    const cacheRow = el('div', 'fv-storage-row'), cacheSize = el('span', 'fv-private-size'), limit = el('select', 'fv-private-limit'), clear = el('button', '', t('Clear cache', '清除缓存'));
    cacheSize.setAttribute('role', 'status'); clear.type = 'button'; clear.disabled = true;
    limit.setAttribute('aria-label', t('Cache limit', '缓存上限'));
    for (const gb of [1, 2, 5, 10, 20]) { const option = el('option', '', `${gb} GB`); option.value = String(gb * 1e9); limit.append(option); }
    const cacheEnd = el('div', 'fv-storage-end'); cacheEnd.append(cacheSize, limit, clear);
    cacheRow.append(el('span', '', t('Cache', '缓存')), cacheEnd);
    const usage = async () => {
        try {
            const value = await json('/freevideo/private/cache');
            cacheSize.textContent = amount(value.bytes || 0);
            if (![...limit.options].some(option => Number(option.value) === value.limit)) {
                const option = el('option', '', amount(value.limit)); option.value = String(value.limit); limit.append(option);
            }
            limit.value = String(value.limit);
            clear.disabled = !(value.bytes > 0) || value.busy === true;
            clear.title = value.busy === true ? t('Available when nothing is being generated.', '没有正在生成的任务时可用。') : '';
        } catch { cacheSize.textContent = ''; }
    };
    limit.onchange = async () => {
        try { await json('/freevideo/private/cache-limit', {bytes: Number(limit.value)}); } catch { /* the size shown is read again */ }
        await usage();
    };
    confirming(clear, t('Clear cache', '清除缓存'), t('Confirm clear', '确认清除'), async () => {
        try {
            const value = await json('/freevideo/private/cache-clear', {});
            cacheSize.textContent = t(`Cleared ${amount(value.freed_bytes || 0)}`, `已清除 ${amount(value.freed_bytes || 0)}`);
        } catch (error) {
            cacheSize.textContent = error.message === 'busy' ? t('Available when nothing is being generated.', '没有正在生成的任务时可用。')
                : t('The cache could not be cleared.', '缓存未能清除。');
        }
        await new Promise(done => setTimeout(done, 1500)); await usage();
    });

    section.append(row, cacheRow, old);
    void review(); void usage();
    return {element: section, refresh: review};
}
