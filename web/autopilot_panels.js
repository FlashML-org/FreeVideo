// The Autopilot view's side panels. The conversation stays in the middle column; the right panel lists every task
// of the chat being read (what is being made first, then the finished ones, newest first), each with its prompt,
// time and state, searchable, and below it the details of the one picked. Pure helpers are exported for tests.
import {tip} from './autopilot_tips.js';

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function put(node, text) { if (node.textContent !== text) node.textContent = text; }
export function glyph(path, width = 2) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('stroke-width', String(width));
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.setAttribute('d', path); svg.append(p);
    return svg;
}
export const ICON = Object.freeze({
    panelLeft: 'M4 5h16v14H4zM9 5v14', panelRight: 'M4 5h16v14H4zM15 5v14',
    search: 'M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zM20 20l-4-4',
    locate: 'M12 3v3M12 18v3M3 12h3M18 12h3M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
    stop: 'M7 7h10v10H7z', warn: 'M12 4l9 16H3zM12 10v4M12 17h.01', check: 'M5 12l5 5 9-10',
    film: 'M4 6h16v12H4zM8 6v12M16 6v12M4 10h4M4 14h4M16 10h4M16 14h4', picture: 'M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4M15 9h.01',
    tasks: 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
});

const WORKING = ['running', 'queued', 'waiting', 'cancelling'];
// The words a task was made from: the engine's own prompt when it sends one (job.prompt), else the sentence that
// asked for it. The chat's task rows do not carry a prompt yet; the request is the stand-in until they do.
export function promptOf(job, request = '') {
    const own = typeof job?.prompt === 'string' ? job.prompt.trim() : '';
    if (own) return {text: own, source: 'job'};
    const asked = typeof request === 'string' ? request.trim() : '';
    return {text: asked, source: asked ? 'request' : null};
}
// One word for where a task is: being made, waiting its turn, stopping, finished, did not finish or stopped.
export function statusKey(job) {
    if (job.status === 'cancelling') return 'stopping';
    if (job.status === 'running') return 'running';
    if (WORKING.includes(job.status)) return 'queued';
    if (job.status === 'succeeded') return 'done';
    return job.status === 'cancelled' ? 'stopped' : 'failed';
}
const STATUS = {running: ['Making', '生成中'], queued: ['Queued', '排队中'], stopping: ['Stopping', '正在停止'],
    done: ['Done', '已完成'], failed: ['Did not finish', '没有完成'], stopped: ['Stopped', '已停止']};
export function statusText(job, t, fraction = null) {
    const key = statusKey(job), word = t(...STATUS[key]);
    return key === 'running' && fraction >= .01 ? `${word} ${Math.floor(fraction * 100)}%` : word;
}

// A time of day for today, with the day before it otherwise; nothing when the time is not known. `short` drops the
// hour from earlier days (a list where the date is enough).
export function localTime(value, t, now = new Date(), {short = false} = {}) {
    const at = value instanceof Date ? value : typeof value === 'number' ? new Date(value * (value < 1e12 ? 1000 : 1)) : value ? new Date(value) : null;
    if (!at || Number.isNaN(at.getTime())) return '';
    const hm = `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`;
    const day = d => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const days = Math.round((day(now) - day(at)) / 86400000);
    if (days === 0) return hm;
    if (days === 1) return short ? t('Yesterday', '昨天') : t(`Yesterday ${hm}`, `昨天 ${hm}`);
    const month = t(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][at.getMonth()], `${at.getMonth() + 1}月`);
    if (short) return at.getFullYear() === now.getFullYear() ? t(`${month} ${at.getDate()}`, `${month}${at.getDate()}日`) : t(`${month} ${at.getFullYear()}`, `${at.getFullYear()}年${month}`);
    return at.getFullYear() === now.getFullYear() ? t(`${month} ${at.getDate()}, ${hm}`, `${month}${at.getDate()}日 ${hm}`)
        : t(`${month} ${at.getDate()} ${at.getFullYear()}, ${hm}`, `${at.getFullYear()}年${month}${at.getDate()}日 ${hm}`);
}
// How long a finished task took, from its two times; '' when either is missing.
export function tookText(from, to, t) {
    const a = new Date(from).getTime(), b = new Date(to).getTime();
    if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return '';
    const s = Math.round((b - a) / 1000), m = Math.floor(s / 60);
    return s < 60 ? t(`${s} s`, `${s} 秒`) : t(`${m} min ${s % 60} s`, `${m} 分 ${s % 60} 秒`);
}

// A task's time line: when it started, how long it has run and about how long is left; how long a finished one took.
const mmss = seconds => { const v = Math.max(0, Math.round(seconds)); return `${Math.floor(v / 60)}:${String(v % 60).padStart(2, '0')}`; };
export function taskTimes(job, t, {left = null, now = Date.now()} = {}) {
    const key = statusKey(job), start = localTime(job.created_at, t, new Date(now)), began = new Date(job.created_at).getTime();
    if (key === 'running' || key === 'stopping') {
        const parts = [start && t(`started ${start}`, `${start} 开始`)];
        if (Number.isFinite(began) && now > began) parts.push(t(`${mmss((now - began) / 1000)} so far`, `已用 ${mmss((now - began) / 1000)}`));
        if (left > 0) parts.push(left >= 60 ? t(`about ${Math.ceil(left / 60)} min left`, `约 ${Math.ceil(left / 60)} 分钟`) : t('under 1 min left', '不到 1 分钟'));
        return parts.filter(Boolean).join(' · ');
    }
    if (key === 'queued') return start ? t(`added ${start}`, `${start} 加入`) : '';
    if (key === 'done') {
        const took = Number.isFinite(began) ? (new Date(job.finished_at).getTime() - began) / 1000 : NaN;
        return [start && t(`started ${start}`, `${start} 开始`), Number.isFinite(took) && took >= 0 ? t(`took ${mmss(took)}`, `用时 ${mmss(took)}`) : ''].filter(Boolean).join(' · ');
    }
    return localTime(job.finished_at || job.created_at, t, new Date(now));
}

// Every word of the query is somewhere in the texts, ignoring case and width; an empty query matches everything.
export const fold = text => String(text || '').normalize('NFKC').toLowerCase();
export function matches(query, ...texts) {
    const words = fold(query).split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    const hay = fold(texts.join(' '));
    return words.every(word => hay.includes(word));
}
// Words with what a search found marked, as plain text nodes (never markup from the words themselves).
export function markText(node, text, query) {
    const words = fold(query).split(/\s+/).filter(Boolean);
    if (!words.length) { if (node.childElementCount || node.textContent !== text) node.textContent = text; return; }
    const folded = fold(text), parts = [];
    let at = 0;
    while (at < text.length) {
        let hit = -1, size = 0;
        for (const word of words) { const found = folded.indexOf(word, at); if (found >= 0 && (hit < 0 || found < hit)) { hit = found; size = word.length; } }
        if (hit < 0 || folded.length !== text.length) { parts.push(text.slice(at)); break; }
        if (hit > at) parts.push(text.slice(at, hit));
        parts.push(el('mark', 'fv-chat-hit', text.slice(hit, hit + size))); at = hit + size;
    }
    node.replaceChildren(...parts.filter(part => part !== ''));
}
// What is being made first (running, then in the order it will run), then the rest, newest first.
export function orderTasks(rows) {
    const rank = row => ({running: 0, stopping: 0, queued: 1}[statusKey(row.job)] ?? 2);
    const time = value => new Date(value || 0).getTime() || 0;
    return [...rows].sort((a, b) => rank(a) - rank(b)
        || (rank(a) < 2 ? time(a.job.created_at) - time(b.job.created_at) : time(b.job.finished_at || b.job.created_at) - time(a.job.finished_at || a.job.created_at))
        || a.order - b.order);
}

// A ring that fills with the task: drawn with a conic gradient, so a change is one custom property.
function ring() { const i = el('i', 'fv-chat-ring'); i.setAttribute('aria-hidden', 'true'); return i; }

// handlers: pick(id) (find the card), stop(id), open(row), edit(row), why(id), collapse(), plus the slot for the
// running summary (the page puts its own control there).
export function createSideRail(t, handlers = {}) {
    const element = el('aside', 'fv-chat-side sgf-avoid');
    element.setAttribute('aria-label', t('Tasks', '任务'));
    const head = el('header', 'fv-chat-panel-head'), title = el('h2', null, t('Tasks', '任务')), count = el('span', 'fv-chat-count');
    const collapse = el('button', 'fv-chat-panel-toggle'); collapse.type = 'button'; collapse.append(glyph(ICON.panelRight));
    collapse.setAttribute('aria-label', t('Hide tasks', '收起任务栏')); tip(collapse, 'collapseRight', t);
    collapse.onclick = () => handlers.collapse?.();
    head.append(title, count, collapse);
    const running = el('div', 'fv-chat-side-running');
    const searchRow = el('label', 'fv-chat-search'), search = el('input');
    search.type = 'search'; search.placeholder = t('Search prompts', '搜索提示词'); search.setAttribute('aria-label', t('Search tasks', '搜索任务'));
    search.autocomplete = 'off'; search.spellcheck = false; tip(search, 'taskSearch', t, {title: false});
    searchRow.append(glyph(ICON.search), search);
    // Filters by state, each with its count: all, under way, done, did not finish.
    const FILTERS = [['all', ['All', '全部']], ['active', ['Active', '进行中']], ['done', ['Done', '完成']], ['failed', ['Failed', '失败']]];
    const inFilter = (id, row) => id === 'all' || (id === 'active' ? ['running', 'queued', 'stopping'] : [id]).includes(statusKey(row.job));
    const filters = el('div', 'fv-chat-seg fv-chat-filters'); filters.setAttribute('role', 'group'); filters.setAttribute('aria-label', t('Show', '筛选'));
    let filter = 'all';
    const counts = new Map();
    for (const [id, words] of FILTERS) {
        const b = el('button'), count = el('small'); b.type = 'button'; b.dataset.filter = id; b.append(el('span', null, t(...words)), count); counts.set(b, count);
        b.onclick = () => { filter = id; draw(); }; tip(b, 'filter', t); filters.append(b);
    }
    const list = el('ol', 'fv-chat-tasks'); list.setAttribute('aria-label', t('Tasks in this chat', '这段对话里的任务'));
    const empty = el('div', 'fv-chat-tasks-empty'), emptyMark = el('span', 'fv-chat-empty-mark'), emptyTitle = el('b'), emptyText = el('p');
    emptyMark.append(glyph(ICON.tasks, 1.7)); empty.append(emptyMark, emptyTitle, emptyText);
    const detail = el('section', 'fv-chat-detail'); detail.setAttribute('aria-label', t('Details', '详情'));
    element.append(head, running, searchRow, filters, list, empty, detail);

    let rows = [], selected = null, query = '', pinned = false;
    const nodes = new Map();
    search.addEventListener('input', () => { query = search.value; draw(); });
    search.addEventListener('keydown', event => { if (event.key === 'Escape' && search.value) { event.preventDefault(); event.stopPropagation(); search.value = ''; query = ''; draw(); } });

    function rowNode(row) {
        let node = nodes.get(row.job.id);
        if (!node) {
            const li = el('li'), button = el('button', 'fv-chat-task'); button.type = 'button';
            const thumb = el('span', 'fv-chat-task-thumb'), body = el('span', 'fv-chat-task-body');
            const top = el('span', 'fv-chat-task-top'), name = el('b'), chip = el('span', 'fv-chat-chip');
            const prompt = el('span', 'fv-chat-task-prompt'), meta = el('span', 'fv-chat-task-meta'), when = el('span');
            top.append(name, chip); meta.append(when); body.append(top, prompt, meta); button.append(thumb, body); li.append(button);
            tip(button, 'task', t, {title: false});
            button.onclick = () => { selected = row.job.id; pinned = true; unfold(); draw(); handlers.pick?.(row.job.id); };
            node = {li, button, thumb, name, chip, prompt, meta, when, image: null, ring: null, picture: null};
            nodes.set(row.job.id, node);
        }
        return node;
    }
    function drawThumb(node, row, fraction) {
        const picture = row.job.thumbnail ? row.thumbnailURL : null;
        if (picture !== node.picture) {
            node.picture = picture; node.thumb.replaceChildren(); node.image = node.ring = null;
            if (picture) { node.image = el('img'); node.image.alt = ''; node.image.loading = 'lazy'; node.image.src = picture; node.thumb.append(node.image); }
        }
        if (!picture) {
            if (!node.ring) { node.thumb.replaceChildren(node.ring = ring()); }
            node.ring.dataset.state = statusKey(row.job);
            node.ring.style.setProperty('--p', String(Math.round((fraction || 0) * 1000) / 10));
        }
    }
    // A list that has more below (or above) fades at that edge, so a cut row reads as "scroll for more".
    const edges = node => { node.dataset.more = node.scrollHeight - node.scrollTop - node.clientHeight > 2 ? (node.scrollTop > 2 ? 'both' : 'below') : node.scrollTop > 2 ? 'above' : ''; };
    list.addEventListener('scroll', () => edges(list), {passive: true}); detail.addEventListener('scroll', () => edges(detail), {passive: true});
    function draw() {
        const shown = rows.filter(row => inFilter(filter, row) && matches(query, row.title, row.prompt));
        for (const b of filters.children) { b.setAttribute('aria-pressed', String(b.dataset.filter === filter)); put(counts.get(b), String(rows.filter(row => inFilter(b.dataset.filter, row)).length)); }
        const keep = new Set(shown.map(row => row.job.id));
        for (const [id, node] of nodes) if (!rows.some(row => row.job.id === id)) { node.li.remove(); nodes.delete(id); }
        for (const [id, node] of nodes) node.li.hidden = !keep.has(id);
        shown.forEach((row, index) => {
            const node = rowNode(row), fraction = row.fraction?.() ?? null, key = statusKey(row.job);
            markText(node.name, row.title, query); put(node.chip, statusText(row.job, t, fraction)); node.chip.dataset.state = key;
            markText(node.prompt, row.prompt || '', query); node.prompt.hidden = !row.prompt;
            put(node.when, taskTimes(row.job, t, {left: row.left?.() ?? null}));
            node.li.dataset.state = key; node.button.setAttribute('aria-current', String(row.job.id === selected));
            node.button.setAttribute('aria-label', [row.title, statusText(row.job, t, fraction), row.prompt].filter(Boolean).join(' · '));
            drawThumb(node, row, fraction);
            if (list.children[index] !== node.li) list.insertBefore(node.li, list.children[index] || null);
        });
        const active = rows.filter(row => ['running', 'queued', 'stopping'].includes(statusKey(row.job))).length;
        put(count, rows.length ? String(rows.length) : ''); count.hidden = !rows.length;
        count.setAttribute('aria-label', t(`${rows.length} tasks, ${active} under way`, `${rows.length} 个任务，${active} 个进行中`));
        put(emptyTitle, rows.length ? (shown.length ? '' : t('No task matches', '没有匹配的任务')) : t('No tasks yet', '还没有任务'));
        put(emptyText, rows.length ? (shown.length ? '' : t('Try other words or another filter.', '换个词或换个筛选试试。')) : t('Each video and picture the assistant makes is listed here, with its prompt, time and state.', '助手生成的每段视频和每张图片都会列在这里，带描述、时间和状态。'));
        emptyMark.hidden = !!rows.length;
        empty.hidden = !emptyTitle.textContent; searchRow.hidden = rows.length < 2 && !query; filters.hidden = !rows.length;
        drawDetail(); edges(list); edges(detail);
    }

    // The details of the picked task: picture or progress, name and state, the whole prompt, sizes and times.
    let detailKey = null;
    const dPreview = el('div', 'fv-chat-detail-preview'), dHead = el('div', 'fv-chat-detail-head'), dName = el('h3'), dChip = el('span', 'fv-chat-chip');
    const dPrompt = el('p', 'fv-chat-detail-prompt fv-copyable'), dFacts = el('dl', 'fv-chat-facts'), dActions = el('div', 'fv-chat-detail-actions');
    // The inspector's head folds it away (kept for the next visit), so the list can have the room.
    const dLabel = el('button', 'fv-chat-detail-label'); dLabel.type = 'button';
    const dLabelText = el('span', null, t('Inspector', '素材检查器')), dLabelName = el('small'), dChevron = glyph('M6 9l6 6 6-6', 2);
    dLabel.append(dLabelText, dLabelName, dChevron); tip(dLabel, 'inspector', t);
    // Folded until a task is picked by hand (a row or a card), so the list keeps the room; the head folds it again.
    let folded = true;
    const unfold = () => { if (folded) { folded = false; detailKey = null; } };
    dLabel.onclick = () => { folded = !folded; detailKey = null; drawDetail(); };
    dHead.append(dName, dChip);
    function fact(label, value) { if (!value) return []; return [el('dt', null, label), el('dd', null, value)]; }
    function button(cls, text, key, action) {
        const b = el('button', cls, text); b.type = 'button'; b.onclick = action; tip(b, key, t); return b;
    }
    function drawDetail() {
        const row = rows.find(item => item.job.id === selected) || null;
        detail.hidden = !row;
        if (!row) { detailKey = null; return; }
        const job = row.job, key = statusKey(job), fraction = row.fraction?.() ?? null;
        detail.dataset.folded = String(folded); dLabel.setAttribute('aria-expanded', String(!folded)); put(dLabelName, row.title);
        put(dName, row.title); put(dChip, statusText(job, t, fraction)); dChip.dataset.state = key;
        const signature = JSON.stringify([job.id, key, job.thumbnail, row.prompt, job.created_at, job.finished_at, row.meta, !!job.work]);
        if (signature === detailKey) { dPreview.style.setProperty('--p', String((fraction || 0) * 100)); return; }
        detailKey = signature;
        dPreview.replaceChildren(); dPreview.dataset.state = key;
        if (job.thumbnail) { const image = el('img'); image.alt = ''; image.src = row.thumbnailURL; dPreview.append(image); }
        else { const mark = el('span', 'fv-chat-detail-mark'); mark.append(glyph(key === 'failed' ? ICON.warn : job.kind === 'image' ? ICON.picture : ICON.film, 1.6)); dPreview.append(mark); }
        dPreview.style.aspectRatio = row.shape || '';
        dPreview.dataset.shape = job.width > 0 && job.height > 0 && job.height > job.width * 1.1 ? 'tall' : 'wide';
        dPreview.style.setProperty('--p', String((fraction || 0) * 100));
        put(dPrompt, row.prompt || ''); dPrompt.hidden = !row.prompt;
        dPrompt.dataset.source = row.promptSource || 'request';
        dPrompt.title = row.promptSource === 'job' ? t('Prompt', '提示词') : t('What you asked for', '你提出的要求');
        dFacts.replaceChildren(...fact(t('Kind', '类型'), job.kind === 'image' ? t('Picture', '图片') : job.kind === 'production' ? t('Film', '成片') : t('Video', '视频')),
            ...fact(t('Size', '尺寸'), job.width > 0 && job.height > 0 ? `${job.width} × ${job.height}` : ''),
            ...fact(t('Length', '时长'), job.seconds > 0 ? t(`${job.seconds} s`, `${job.seconds} 秒`) : ''),
            ...fact(t('Started', '开始'), localTime(job.created_at, t)),
            ...fact(t('Took', '用时'), key === 'done' ? tookText(job.created_at, job.finished_at, t) : ''));
        dFacts.hidden = !dFacts.childElementCount;
        const actions = [];
        if (key === 'done' && (job.work || job.kind === 'image' && job.thumbnail)) actions.push(button('fv-primary', t('Open', '打开'), job.kind === 'image' ? 'openPicture' : 'open', () => handlers.open?.(row)));
        if (key === 'done' && job.work && job.kind !== 'image') actions.push(button('', t('Edit', '剪辑'), 'edit', () => handlers.edit?.(row)));
        if (['running', 'queued'].includes(key) && row.stoppable !== false) actions.push(button('fv-chat-stop', t('Stop', '停止'), 'stop', () => handlers.stop?.(job.id)));
        if (key === 'failed') actions.push(button('', t('See why', '查看原因'), 'why', () => handlers.why?.(job.id)));
        actions.push(button('fv-quiet', t('Show in chat', '在对话中定位'), 'locate', () => handlers.pick?.(job.id)));
        dActions.replaceChildren(...actions);
        detail.replaceChildren(...(folded ? [dLabel] : [dLabel, dPreview, dHead, dPrompt, dFacts, dActions]));
    }

    return {element, running, search, list, detail,
        // rows: [{job, title, prompt, promptSource, meta, shape, thumbnailURL, fraction(), order, stoppable}]; `current`
        // is picked while the reader has not picked anything, else the newest finished task.
        update(next, {current = null} = {}) {
            rows = orderTasks(next);
            if (!pinned || !rows.some(row => row.job.id === selected)) {
                pinned = pinned && rows.some(row => row.job.id === selected);
                if (!pinned) selected = (current && rows.some(row => row.job.id === current) ? current : rows.find(row => statusKey(row.job) === 'done')?.job.id) || rows[0]?.job.id || null;
            }
            draw();
        },
        tick() { draw(); },
        select(id) { if (rows.some(row => row.job.id === id)) { selected = id; pinned = true; unfold(); draw(); } },
        selected: () => selected,
        reset() { rows = []; selected = null; pinned = false; query = ''; search.value = ''; filter = 'all'; nodes.clear(); list.replaceChildren(); draw(); },
    };
}
