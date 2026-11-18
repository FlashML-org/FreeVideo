// Global search: the user's videos (the library), pictures (the image history) and cast, found by prompt, name or
// file name, in groups; Enter opens the one marked. It shows in the command palette, below the commands, and on its
// own (Ctrl/⌘+Shift+F). Today's interfaces have no search of their own, so this reads what they list and matches here:
//   videos   GET /freevideo/library (48 a page, newest first) and, for the newest, GET /freevideo/library/prompt;
//            prompts never change, so they are kept in this browser (freevideo.search.prompts);
//   pictures the image worker's history (file names) with the prompts the image view keeps (freevideo.imageView.meta);
//   cast     /freevideo/agent/cast for characters, scenes and props, when the assistant interface is on.
// Moving to an engine search (GET /freevideo/library?q=) replaces the fetchers and nothing else.
import { t, words, CN } from './context_menu.js';

const browser = typeof document !== 'undefined';
export const SEARCH_MIN = 2;
export const PROMPTS_KEY = 'freevideo.search.prompts';
const PROMPT_LIMIT = 400, PAGES = 4, PROMPT_FETCH = 96, STALE_MS = 60e3;

const fold = text => String(text ?? '').normalize('NFKC').toLowerCase();
const clean = text => String(text ?? '').replace(/\s+/g, ' ').trim();

// One row against the query words: every word has to appear in one of its fields. The title field counts most,
// a match at the start of a field or of a word more than one inside a word. Returns {score, field, at} or null.
export function matchRow(fields, query) {
    const parts = fold(query).trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return {score: 0, field: 0, at: -1, length: 0};
    let total = 0, first = null;
    for (const part of parts) {
        let best = null;
        fields.forEach((value, index) => {
            const text = fold(value); if (!text) return;
            const at = text.indexOf(part); if (at < 0) return;
            const start = at === 0, word = start || /[\s\-_/·.,:(（、，。]/.test(text[at - 1]);
            const score = 100 - index * 20 + (start ? 40 : word ? 20 : 0) - Math.min(30, at / 10);
            if (!best || score > best.score) best = {score, field: index, at, length: part.length};
        });
        if (!best) return null;
        total += best.score; first ||= best;
    }
    return {...first, score: total};
}

// The line shown for a long prompt: the start, or the stretch around the match; ranges mark the match in it.
export function excerpt(text, at = -1, length = 0, width = 72) {
    const value = clean(text);
    if (value.length <= width || at < 0 || at + length <= width - 8) {
        const shown = value.length > width ? value.slice(0, width - 1) + '…' : value;
        return {title: shown, ranges: at >= 0 && at + length <= shown.length ? [[at, at + length]] : []};
    }
    const start = Math.max(0, at - 20), body = value.slice(start, start + width - 2);
    const title = '…' + body + (start + width - 2 < value.length ? '…' : '');
    return {title, ranges: [[at - start + 1, at - start + 1 + length]]};
}

const dateWords = (value, cn = CN) => {
    const date = new Date(value); if (Number.isNaN(date.getTime())) return '';
    const today = new Date(), sameDay = date.toDateString() === today.toDateString();
    const time = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
    if (sameDay) return cn ? `今天 ${time}` : `Today ${time}`;
    return cn ? `${date.getMonth() + 1}月${date.getDate()}日 ${time}` : `${date.toLocaleString('en-US', {month: 'short', day: 'numeric'})} ${time}`;
};
// The engine names pictures image-YYYYMMDD-HHMMSS-NN.
export function pictureTime(name) {
    const m = /image-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(name || '');
    return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime() : 0;
}

// The search over three fetchers. Each returns plain rows; nothing here touches the page, so tests drive it.
//   fetchVideos(before) → {items, next}; fetchPrompt(id) → text; fetchPictures() → [{file, name}];
//   pictureMeta(file) → {prompt, at} | null; fetchCast() → [{id, name, kind, description, thumbnail}].
export function createSearch({fetchVideos, fetchPrompt, fetchPictures, pictureMeta = () => null, fetchCast = async () => [], storage = null,
    now = () => Date.now(), urls = {}, open = {}, cn = CN} = {}) {
    let videos = [], pictures = [], cast = [], loadedAt = 0, loading = null;
    const prompts = (() => { try { const value = JSON.parse(storage?.getItem(PROMPTS_KEY) || '{}'); return value && typeof value === 'object' ? value : {}; } catch { return {}; } })();
    const savePrompts = () => {
        const ids = Object.keys(prompts);
        if (ids.length > PROMPT_LIMIT) for (const id of ids.slice(0, ids.length - PROMPT_LIMIT)) delete prompts[id];
        try { storage?.setItem(PROMPTS_KEY, JSON.stringify(prompts)); } catch { /* Kept for this page only. */ }
    };
    let promptsRunning = null;
    async function fillPrompts() {
        const missing = videos.slice(0, PROMPT_FETCH).filter(row => row.id && prompts[row.id] === undefined);
        let next = 0;
        const worker = async () => {
            while (next < missing.length) {
                const row = missing[next++];
                try { prompts[row.id] = clean(await fetchPrompt(row.id)); } catch { prompts[row.id] = ''; }
            }
        };
        await Promise.all([worker(), worker(), worker(), worker()]);
        if (missing.length) savePrompts();
    }
    async function load() {
        const settled = await Promise.allSettled([
            (async () => {
                const rows = []; let before = null;
                for (let page = 0; page < PAGES; page++) {
                    const answer = await fetchVideos(before);
                    rows.push(...(answer?.items || []));
                    before = answer?.next; if (!before) break;
                }
                videos = rows;
            })(),
            (async () => { pictures = (await fetchPictures()) || []; })(),
            (async () => { cast = (await fetchCast()) || []; })(),
        ]);
        loadedAt = now();
        // Prompts arrive in the background; the next keystroke sees them.
        promptsRunning ||= fillPrompts().finally(() => { promptsRunning = null; });
        if (settled.every(result => result.status === 'rejected')) throw settled[0].reason;
    }
    const ready = () => {
        if (loading) return loading;
        if (loadedAt && now() - loadedAt < STALE_MS) return Promise.resolve();
        loading = load().finally(() => { loading = null; });
        return loading;
    };

    const videoRow = row => {
        const prompt = prompts[row.id] || '';
        const label = clean(row.agent?.label || '');
        const file = String(row.video || row.id || '');
        const g = row.geometry || {}, seconds = g.seconds || (g.frames && g.frames / (g.fps || 24));
        const kind = cn ? '视频' : 'Video';
        return {kind: 'video', key: 'video:' + (row.id || file), time: Date.parse(row.created_at) || 0, fields: [prompt || label, label, file], prompt,
            fallback: label || kind, sub: [kind, seconds ? (cn ? `${Math.round(seconds)} 秒` : `${Math.round(seconds)} s`) : '', dateWords(row.created_at, cn)].filter(Boolean).join(' · '),
            badge: seconds ? `${Math.round(seconds)} s` : '', thumb: urls.video?.(row) || '', icon: 'film', use: cn ? '在作品库里打开' : 'Open in Creations', open: () => open.video?.(row)};
    };
    const pictureRow = row => {
        const meta = pictureMeta(row.file) || {};
        const time = meta.at || pictureTime(row.name || row.file);
        const kind = cn ? '图像' : 'Picture', size = meta.width && meta.height ? `${meta.width} × ${meta.height}` : '';
        return {kind: 'image', key: 'image:' + row.file, time, fields: [clean(meta.prompt || ''), '', String(row.name || row.file)], prompt: clean(meta.prompt || ''),
            fallback: String(row.name || row.file), sub: [kind, size, dateWords(time, cn)].filter(Boolean).join(' · '), badge: '', thumb: urls.picture?.(row) || '', icon: 'image',
            use: cn ? '在图像视图里打开' : 'Open in Images', open: () => open.picture?.(row)};
    };
    const KIND_WORDS = {character: ['Character', '角色'], setting: ['Scene', '场景'], prop: ['Prop', '道具']};
    const castRow = row => ({kind: 'cast', key: 'cast:' + row.id, time: 0, fields: [clean(row.name), clean(row.description || ''), ''], round: true,
        fallback: clean(row.name), sub: `${words(KIND_WORDS[row.kind] || KIND_WORDS.character, cn)} · ${cn ? `在描述里输入 @${clean(row.name)}` : `type @${clean(row.name)} in a prompt`}`,
        thumb: row.thumbnail?.file ? urls.cast?.(row) || '' : '', icon: 'person', use: cn ? '打开这个角色' : 'Open this cast member', open: () => open.cast?.(row)});
    // Every prompt used here once, newest use first, with how often it was used.
    const promptRows = () => {
        const seen = new Map();
        for (const row of [...videos.map(videoRow), ...pictures.map(pictureRow)].sort((a, b) => b.time - a.time)) {
            if (!row.prompt) continue;
            const key = fold(row.prompt);
            if (seen.has(key)) { seen.get(key).count++; continue; }
            seen.set(key, {prompt: row.prompt, time: row.time, kind: row.kind, count: 1});
        }
        return [...seen.values()].map(item => ({kind: 'prompt', key: 'prompt:' + fold(item.prompt).slice(0, 80), time: item.time, fields: [item.prompt, '', ''], fallback: item.prompt,
            sub: [item.count > 1 ? (cn ? `用过 ${item.count} 次` : `Used ${item.count} times`) : dateWords(item.time, cn), cn ? '填入描述框' : 'fills the prompt'].join(' · '),
            thumb: '', icon: 'clock', use: cn ? '把这段描述填进当前视图' : 'Put this prompt in the view in front', open: () => open.prompt?.(item.prompt, item.kind)}));
    };

    function present(row, hit) {
        const field = hit && hit.field === 0 && row.fields[0] ? excerpt(row.fields[0], hit.at, hit.length) : row.fields[0] ? excerpt(row.fields[0]) : null;
        const title = field?.title || row.fallback;
        // A match outside the title (a file name, a description) still says where it matched.
        const where = hit && hit.field > 0 && row.fields[hit.field] ? clean(row.fields[hit.field]).replace(/\/video\.mp4$/, '').split('/').pop() : '';
        const sub = where ? `${row.sub ? row.sub + ' · ' : ''}${where}` : row.sub;
        return {id: row.key, kind: row.kind, title, ranges: field?.ranges || [], sub, thumb: row.thumb, icon: row.icon, use: row.use, open: row.open, time: row.time,
            badge: row.badge || '', round: Boolean(row.round), prompt: row.prompt || ''};
    }
    const GROUPS = {works: ['Creations', '作品'], prompt: ['Prompts', '提示词'], cast: ['Cast', '角色'], recent: ['Recent creations', '最近作品']};

    return {
        ready, warm: () => { ready().catch(() => {}); },
        // A deleted work takes its kept prompt with it, now and on disk: by library id (videos) or the video's path.
        forget(ids) {
            const gone = new Set((ids || []).filter(id => typeof id === 'string' && id).flatMap(id => [id, id.replace(/^FreeVideo\//, '').replace(/\/video\.mp4$/, '')]));
            if (!gone.size) return;
            for (const id of gone) delete prompts[id];
            videos = videos.filter(row => !gone.has(row.id) && !gone.has(String(row.video || '')));
            savePrompts();
        },
        // Groups of results for a query: [{id, label, items, total}] for works (videos and pictures together), prompts
        // and cast. An empty query gives the newest works, then the newest prompts and the cast.
        async search(query, {limit = 6} = {}) {
            await ready();
            const q = clean(query);
            const works = [...videos.map(videoRow), ...pictures.map(pictureRow)];
            if (!q) {
                const newest = works.sort((a, b) => b.time - a.time);
                const prompts = promptRows(), people = cast.map(castRow);
                return [{id: 'works', label: GROUPS.recent, items: newest.slice(0, limit).map(row => present(row, null)), total: newest.length},
                    {id: 'prompt', label: GROUPS.prompt, items: prompts.slice(0, limit).map(row => present(row, null)), total: prompts.length},
                    {id: 'cast', label: GROUPS.cast, items: people.slice(0, limit).map(row => present(row, null)), total: people.length}];
            }
            const rank = rows => {
                const hits = rows.map(row => ({row, hit: matchRow(row.fields, q)})).filter(entry => entry.hit).sort((a, b) => b.hit.score - a.hit.score || b.row.time - a.row.time);
                return {items: hits.slice(0, limit).map(entry => present(entry.row, entry.hit)), total: hits.length};
            };
            return [
                {id: 'works', label: GROUPS.works, ...rank(works)},
                {id: 'prompt', label: GROUPS.prompt, ...rank(promptRows())},
                {id: 'cast', label: GROUPS.cast, ...rank(cast.map(castRow))},
            ];
        },
        promptsPending: () => Boolean(promptsRunning),
        // Resolves once the prompts being fetched have arrived (at once when none are).
        settled: () => promptsRunning || loading || Promise.resolve(),
        counts: () => ({videos: videos.length, pictures: pictures.length, cast: cast.length}),
    };
}

// ── The page's search, over today's interfaces ──
let pageSearch = null;
async function browserSearch() {
    if (pageSearch) return pageSearch;
    const [{api}, {createImageGenClient}, {agentGet}, {agentInterfaceEnabled}] = await Promise.all([
        import('../../scripts/api.js'), import('./image_generation.js'), import('./agent_session.js'), import('./agent_connect.js')]);
    const client = createImageGenClient();
    const view = (file, type = 'input') => { const at = file.lastIndexOf('/'); return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(at + 1), subfolder: at < 0 ? '' : file.slice(0, at), type})); };
    const shell = () => globalThis.FreeVideoShell || null;
    let storage = null; try { storage = localStorage; } catch { /* Prompts are fetched again next time. */ }
    const readMeta = () => { try { return JSON.parse(storage?.getItem('freevideo.imageView.meta') || '{}')?.items || {}; } catch { return {}; } };
    let meta = {}, metaAt = 0;
    pageSearch = createSearch({
        storage,
        fetchVideos: async before => {
            const query = new URLSearchParams({limit: '48'}); if (before) query.set('before', before);
            const response = await api.fetchApi('/freevideo/library?' + query, {cache: 'no-store'});
            if (!response.ok) throw new Error(t('Could not read the creations.', '无法读取作品。'));
            return response.json();
        },
        fetchPrompt: async id => {
            const response = await api.fetchApi('/freevideo/library/prompt?' + new URLSearchParams({id}), {cache: 'no-store'});
            return response.ok ? (await response.json()).prompt || '' : '';
        },
        fetchPictures: () => client.history(),
        pictureMeta: file => { if (Date.now() - metaAt > 2000) { meta = readMeta(); metaAt = Date.now(); } return meta[file] || null; },
        fetchCast: async () => {
            if (!await agentInterfaceEnabled(api)) return [];
            const kinds = ['character', 'setting', 'prop'];
            const lists = await Promise.all(kinds.map(async kind => {
                const response = await agentGet(api, `/freevideo/agent/cast?kind=${kind}&limit=200`);
                return response.ok ? ((await response.json()).cards || []).map(card => ({...card, kind})) : [];
            }));
            return lists.flat();
        },
        urls: {
            video: row => api.apiURL('/freevideo/library/thumbnail?' + new URLSearchParams({id: row.id || String(row.video || '').replace(/^FreeVideo\//, '').replace(/\/video\.mp4$/, '')})),
            // The engine's small copy of a picture of the history (work_media.js); any other picture as itself.
            picture: row => /^freevideo-image\/(?:image-\d{8}-\d{6}-\d{2}\.(?:png|jpg)|[0-9a-f]{32}\.png)$/.test(row.file || '')
                ? api.apiURL('/freevideo/library/picture-thumbnail?' + new URLSearchParams({file: row.file})) : view(row.file),
            cast: row => view(row.thumbnail.file),
        },
        open: {
            video: async row => (await import('./library.js')).openLibrary(t, {selectedWork: row.id || row.video}),
            // The image view shows the picture when it hears about it; it opens at its newest picture otherwise.
            picture: async row => {
                await shell()?.go?.('image').catch(() => {});
                dispatchEvent(new CustomEvent('freevideo-open-work', {detail: {kind: 'image', file: row.file}}));
            },
            // A prompt goes into the prompt box of the view in front (Images for a picture's prompt when no view takes it).
            prompt: (text, kind) => fillPrompt(text, {view: shell()?.view?.(), fallbackView: kind === 'image' ? 'image' : 'studio'}),
            // A cast member opens from its avatar in the view in front, or in Create.
            cast: async row => {
                const find = () => [...document.querySelectorAll('dialog[open] .fv-cast-library .fv-cast-avatar')].find(avatar => avatar.getAttribute('aria-label') === row.name);
                let avatar = find();
                if (!avatar && shell()?.view?.() !== 'studio') { await shell()?.go?.('studio').catch(() => {}); await new Promise(done => setTimeout(done, 400)); avatar = find(); }
                if (avatar) avatar.click();
                dispatchEvent(new CustomEvent('freevideo-open-work', {detail: {kind: 'cast', id: row.id, cast: row.kind}}));
            },
        },
    });
    // Deleting a work also deletes the prompt this search kept for it, at once.
    const removed = event => pageSearch.forget([...(event.detail?.ids || []), event.detail?.id, event.detail?.video]);
    addEventListener('freevideo-works-removed', removed);
    addEventListener('freevideo-library-deleted', removed);
    return pageSearch;
}

// Puts a prompt into a view's prompt box. The view that listens takes it (window 'freevideo-command', id 'prompt.fill',
// it sets detail.handled); otherwise the box is filled the way typing fills it, and as a last resort the text is copied.
export const PROMPT_BOXES = {studio: '[data-tour-id="prompt-box"]', image: '.fv-image textarea', editor: '[data-tour-id="segment-prompt"]', autopilot: '.fv-autopilot textarea'};
export async function fillPrompt(text, {view = null, fallbackView = 'studio'} = {}) {
    const shell = globalThis.FreeVideoShell;
    let target = view && PROMPT_BOXES[view] ? view : fallbackView;
    if (shell?.view?.() !== target) { try { await shell?.go?.(target); } catch { /* Stay where we are. */ } }
    target = shell?.view?.() || target;
    const detail = {id: 'prompt.fill', view: target, text, handled: false};
    dispatchEvent(new CustomEvent('freevideo-command', {detail}));
    const {toast} = await import('./notifications.js');
    if (detail.handled) return true;
    const box = [...document.querySelectorAll(`dialog[open] ${PROMPT_BOXES[target] || 'textarea'}`)].filter(node => node.getClientRects().length).at(-1);
    if (box && !box.disabled && !box.readOnly) {
        box.focus();
        const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(box), 'value')?.set;
        if (setter) setter.call(box, text); else box.value = text;
        box.dispatchEvent(new Event('input', {bubbles: true})); box.dispatchEvent(new Event('change', {bubbles: true}));
        toast(t('The prompt is in. Press Ctrl+Enter to generate.', '描述已填入，按 Ctrl+回车 生成。').replace('Ctrl', /Mac/.test(navigator.platform) ? '⌘' : 'Ctrl'), {tone: 'ok'});
        return true;
    }
    try { await navigator.clipboard.writeText(text); toast(t('Prompt copied. Paste it into the prompt box.', '描述已复制，请粘贴到描述框。'), {icon: 'copy'}); }
    catch { toast(t('Could not put the prompt in. Try again.', '没有填入描述，请重试。'), {tone: 'warn'}); }
    return false;
}

export async function searchEverything(query, options) { return (await browserSearch()).search(query, options); }
// Resolves when more can be found than a moment ago (prompts still arriving); false when nothing is on its way.
export async function searchSettled() { const search = await browserSearch(); if (!search.promptsPending()) return false; await search.settled(); return true; }
export function warmSearch() { if (browser) browserSearch().then(search => search.warm(), () => {}); }
