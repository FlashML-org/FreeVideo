// Settings › Storage › Privacy. FreeVideo's own uploads and pictures that nothing uses any more, removed when the user
// confirms the list; the encoding cache (its size, how much it may use, one click to clear); and pictures earlier
// versions put in ComfyUI's input folder under the user's own file names, which cannot be told apart from the user's own
// files, so each one goes only when the user chooses it. Built with the settings kit so it reads like the rest of the
// page. An engine older than this page has none of these calls: the whole group then stays hidden and nothing is asked.
import { el, tip, button, row, card, groupHead, groupNote, fold, segmented } from './settings_kit.js';
import { privacyOffered } from './private_offers.js';

const amount = bytes => bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + ' GB' : (bytes / 1e6).toFixed(1) + ' MB';

export function privacySettings(t, api) {
    const json = async (path, body) => {
        const response = await api.fetchApi(path, body === undefined ? {cache: 'no-store'} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        const value = await response.json().catch(() => ({}));
        if (!response.ok) throw Object.assign(new Error(value.error || 'failed'), {status: response.status});
        return value;
    };
    // A second click within a few seconds confirms; the button says what the second click does.
    const confirming = (node, idle, ask, act) => {
        let armed = 0;
        node.onclick = async () => {
            if (!armed) { node.textContent = ask; armed = setTimeout(() => { armed = 0; node.textContent = idle; }, 5000); return; }
            clearTimeout(armed); armed = 0; node.textContent = idle; node.disabled = true;
            try { await act(); } finally { node.disabled = false; }
        };
    };
    const group = el('div', 'st-privacy'); group.hidden = true;

    // Uploads and pictures no longer used.
    const size = el('span', 'st-sz'); size.setAttribute('role', 'status');
    const clean = button(t('Clean up', '清理'), {kind: 'fill'}); clean.disabled = true;
    tip(clean, t('Clean up', '清理'), t('Delete the listed files for good', '彻底删除清单上的文件'));
    const unused = row({iconName: 'shield', title: t('Uploads and pictures no longer used', '不再使用的上传和图片'),
        sub: t('Nothing that a work, character, conversation or open page uses', '作品、角色、对话和打开的页面都不再使用'), end: [size, clean]});
    let tokens = [];
    const review = async () => {
        try {
            const value = await json('/freevideo/private/review');
            group.hidden = false;
            tokens = (value.items || []).map(item => item.token);
            size.textContent = tokens.length ? t(`${tokens.length} files, ${amount(value.bytes || 0)}`, `${tokens.length} 个文件，共 ${amount(value.bytes || 0)}`)
                : t('Nothing to clean up', '没有可清理的文件');
        } catch (error) {
            tokens = [];
            if (error.status === 404 || error.status === 405) { group.hidden = true; return; }
            group.hidden = false; size.textContent = t('The list cannot be read now.', '暂时无法读取清单。');
        }
        clean.disabled = !tokens.length;
    };
    confirming(clean, t('Clean up', '清理'), t('Confirm clean up', '确认清理'), async () => {
        try {
            const value = await json('/freevideo/private/clean', {tokens});
            size.textContent = t(`Removed ${value.removed} files, ${amount(value.freed_bytes || 0)}`, `已删除 ${value.removed} 个文件，共 ${amount(value.freed_bytes || 0)}`);
        } catch { size.textContent = t('The clean up did not finish. Try again.', '清理没有完成，请再试一次。'); }
        await new Promise(done => setTimeout(done, 1500)); await review();
    });

    // The encoding cache.
    const cacheSize = el('span', 'st-sz'); cacheSize.setAttribute('role', 'status');
    const limit = el('select'); limit.setAttribute('aria-label', t('Cache limit', '缓存上限'));
    for (const gb of [1, 2, 5, 10, 20]) { const option = el('option', '', `${gb} GB`); option.value = String(gb * 1e9); limit.append(option); }
    limit.value = String(2e9);
    const clear = button(t('Clear cache', '清除缓存')); clear.disabled = true;
    const cache = row({iconName: 'disk', title: t('Cache', '缓存'), sub: t('Kept so a repeated prompt or picture starts sooner', '让重复的提示词和图片更快开始'), end: [cacheSize, clear]});
    const cacheLimit = row({title: t('Cache limit', '缓存上限'), sub: t('The oldest goes first when it is full', '满了先删最久没用的'), end: [segmented(limit, {label: t('Cache limit', '缓存上限')})]});
    const busyWhy = t('Available when nothing is being generated.', '没有正在生成的任务时可用。');
    const usage = async () => {
        try {
            const value = await json('/freevideo/private/cache');
            cacheSize.textContent = amount(value.bytes || 0);
            if ([...limit.options].some(option => Number(option.value) === value.limit)) limit.value = String(value.limit);
            clear.disabled = !(value.bytes > 0) || value.busy === true;
            tip(clear, t('Clear cache', '清除缓存'), t('Delete every cached encoding', '删除所有缓存'), {why: value.busy === true ? busyWhy : value.bytes > 0 ? '' : t('The cache is empty', '缓存是空的')});
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
        } catch (error) { cacheSize.textContent = error.message === 'busy' ? busyWhy : t('The cache could not be cleared.', '缓存未能清除。'); }
        await new Promise(done => setTimeout(done, 1500)); await usage();
    });

    // Old reference pictures in the input folder itself.
    const old = fold({iconName: 'img', title: t('Clean up old reference pictures', '清理旧参考图'),
        sub: t('Pictures in ComfyUI’s input folder, deleted one by one', 'ComfyUI 输入文件夹中的图片，逐张删除')});
    const grid = el('div', 'st-private-grid'), status = el('div', 'st-private-status'); status.setAttribute('role', 'status');
    old.body.append(groupNote(t('Earlier versions saved reference pictures here under their own file names, which cannot be told apart from your own files, so each one is deleted only when you choose it.',
        '旧版本曾把参考图以原文件名存放在这里，无法与您自己的文件区分，因此只在您逐张选择后删除。')), grid, status);
    const view = name => api.apiURL('/view?' + new URLSearchParams({filename: name, subfolder: '', type: 'input'}));
    const listOld = async () => {
        grid.replaceChildren(); status.textContent = '';
        let items = [];
        try { items = (await json('/freevideo/private/legacy')).items || []; } catch { status.textContent = t('The list cannot be read now.', '暂时无法读取清单。'); return; }
        if (!items.length) { status.textContent = t('No pictures in the input folder.', '输入文件夹中没有图片。'); return; }
        for (const item of items) {
            const cell = el('figure', 'st-private-cell'), image = el('img'), caption = el('figcaption', '', item.name);
            image.src = view(item.name); image.alt = ''; image.loading = 'lazy'; caption.title = item.name;
            const remove = button(t('Delete', '删除'));
            confirming(remove, t('Delete', '删除'), t('Confirm', '确认删除'), async () => {
                try {
                    const value = await json('/freevideo/private/legacy-delete', {name: item.name});
                    if (value.removed) cell.remove();
                    else status.textContent = value.reason === 'in-use' ? t('This picture is still used by a work or workflow, so it was kept.', '这张图片仍被作品或工作流使用，已保留。')
                        : t('This picture could not be deleted.', '这张图片无法删除。');
                } catch { status.textContent = t('This picture could not be deleted.', '这张图片无法删除。'); }
                if (!grid.childElementCount) status.textContent = t('No pictures in the input folder.', '输入文件夹中没有图片。');
            });
            cell.append(image, caption, el('span', 'st-private-size', amount(item.bytes || 0)), remove);
            grid.append(cell);
        }
    };
    old.element.addEventListener('toggle', () => { if (old.element.open) void listOld(); });

    group.append(groupHead(t('Privacy', '隐私')), card(unused.element, cache.element, cacheLimit.element), old.element);
    // Only an engine that says it has these calls is asked; an older one is sent nothing.
    const load = () => privacyOffered().then(yes => { if (yes) { void review(); void usage(); } });
    void load();
    return {element: group, refresh: () => { void load(); }};
}

if (typeof document !== 'undefined' && !document.querySelector('style[data-fv-private-settings]')) {
    const style = document.createElement('style'); style.dataset.fvPrivateSettings = '';
    style.textContent = `.st-privacy .st-sz{white-space:nowrap}
.st-private-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:var(--fv-sp-12,12px);margin-top:var(--fv-sp-8,8px);padding:0 var(--fv-sp-16,16px)}
.st-private-cell{display:flex;flex-direction:column;gap:var(--fv-sp-4,4px);margin:0;min-width:0}
.st-private-cell img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:var(--fv-rad-pic,12px);background:var(--fv-ctl-bg,#0002)}
.st-private-cell figcaption,.st-private-size{font-size:var(--fv-fs-1,12px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.st-private-status{font-size:var(--fv-fs-1,12px);color:var(--fv-fg2);min-height:16px;margin:var(--fv-sp-8,8px) 0 var(--fv-sp-12,12px);padding:0 var(--fv-sp-16,16px)}`;
    document.head.append(style);
}
