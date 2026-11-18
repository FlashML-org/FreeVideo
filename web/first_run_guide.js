import {SHOWCASE_TEXT, showcaseChapter, loadShowcaseImage} from './entry_showcase.js';
import './glass/library.js';
import {openAnchor} from './feature_tour_layer.js';

const FILE = '/userdata/' + encodeURIComponent('freevideo/guide.json');
const attempted = new Set();
let current = null;
const guides = new Map();
const recordWrites = new WeakMap();

export const REPLAY_TEXT = {
    title: ['Getting started', '新手指引'],
    again: ['Show again', '再看一遍'],
    waiting: ['Waiting for the workspace to be ready. The guide will start automatically.', '正在等待创作面板就绪，随后会自动开始指引。'],
    cancel: ['Cancel', '取消'],
    unavailable: ['Open a creation workflow to view the guide.', '请先打开创作工作流，再查看指引。'],
    manual: ['User guide', '使用说明'],
};

// The guide: at most three steps per view, each pointing at the real control (the shared anchored layer, level 2).
// It never starts by itself; Settings › Getting started and the tutorial replay it in the view in front.
export const CHAPTERS = [
    {id: 'describe', anchor: '[data-fv-guide="describe"]', added: false, title: ['Write one sentence', '写一句描述'],
        en: 'Say who is where, doing what. Type @ to use added pictures and videos.',
        zh: '说清谁在哪里做什么。输入 @ 可引用添加的图片和视频。'},
    {id: 'generate', anchor: '[data-fv-guide="generate"]', added: false, title: ['Generate', '生成'],
        en: 'Shape, length and quality are on this row; progress shows on the picture.',
        zh: '画幅、时长和质量在这一行；进度显示在画面上。'},
    {id: 'longer', anchor: '[data-view="editor"]', target: dialog => tabsOrLength(dialog), added: true,
        title: ['Longer and more', '更长、更完整'],
        en: 'A longer duration is made in parts; continue part by part in Edit, or let Autopilot film it.',
        zh: '填更长的时长会自动分段；也可以到剪辑视图逐段接续，或交给 AI 全自动拍完。'},
    {id: 'editor-timeline', group: 'editor', anchor: '[data-fv-guide="editor-timeline"]', added: true, title: ['Drag it in', '拖进来'],
        en: 'Drag a video here.', zh: '把视频拖到这里。'},
    {id: 'editor-empty', group: 'editor', anchor: '.fv-ed-track-row[data-track="v1"]', added: true, ghost: true, title: ['Lengthen or add', '拉长或新增'],
        en: 'Drag the right edge to make a part longer; drag in an empty spot to add one.', zh: '拖右边缘，把这一段拉长；在空白处拖出新的一段。'},
    {id: 'editor-describe', group: 'editor', anchor: '[data-fv-guide="editor-describe"]',
        anchors: ['[data-fv-guide="editor-describe"]', '[data-fv-guide="editor-generate"]'], added: true, title: ['Describe and generate', '描述并生成'],
        en: 'Describe it, then choose "Generate".', zh: '填写描述，然后点“生成”。'},
];

// "Longer and more" points at the Edit and Autopilot tabs that are on screen, or at the length on a phone.
function tabsOrLength(dialog) {
    const shown = node => node?.isConnected && !node.closest('[hidden]') && node.getClientRects().length && node.getBoundingClientRect().width > 0;
    const tabs = [...dialog.querySelectorAll(':scope > .fv-header .fv-view-switch [data-view="editor"], :scope > .fv-header .fv-view-switch [data-view="autopilot"]')].filter(shown);
    const nodes = tabs.length ? tabs : [dialog.querySelector('[data-fv-guide="seconds"]')].filter(shown);
    if (!nodes.length) return null;
    const rects = nodes.map(node => node.getBoundingClientRect());
    const left = Math.min(...rects.map(r => r.left)), top = Math.min(...rects.map(r => r.top)), right = Math.max(...rects.map(r => r.right)), bottom = Math.max(...rects.map(r => r.bottom));
    return {node: nodes[0], rect: {left, top, right, bottom, width: right - left, height: bottom - top}};
}
const chaptersFor = (group, chapters = CHAPTERS) => chapters.filter(c => (c.group || 'studio') === group);

export function guideSteps({record, available = [], returning = false, blockers = {}, all = false, group = 'studio', chapters = CHAPTERS}) {
    if (Object.values(blockers).some(Boolean)) return [];
    return chaptersFor(group, chapters).filter(chapter => available.includes(chapter.id)
        && (all || (!record?.seen.includes(chapter.id) && (record || !returning || chapter.added))));
}

export function guideRecord(record, ids) {
    return {schema: 1, seen: [...new Set([...(record?.seen || []), ...ids])]};
}

export async function readGuideRecord(api) {
    try {
        // Switching workspaces can race a slow acknowledgement from the old
        // guide. Read after its writes so the next group retains those ids.
        await recordWrites.get(api);
        const reply = await api.fetchApi(FILE, {cache: 'no-store'});
        if (reply.status === 404) return {ok: true, record: null};
        if (!reply.ok) return {ok: false};
        const record = await reply.json();
        if (record?.schema !== 1 || !Array.isArray(record.seen) || !record.seen.every(id => typeof id === 'string')) return {ok: false};
        return {ok: true, record};
    } catch { return {ok: false}; }
}

export function saveGuideRecord(api, record) {
    const pending = (recordWrites.get(api) || Promise.resolve()).then(async () => {
        try {
            return (await api.fetchApi(FILE + '?overwrite=true', {method: 'POST', body: JSON.stringify(record), keepalive: true})).ok;
        } catch { return false; }
    });
    recordWrites.set(api, pending);
    return pending;
}

// An empty record checks write access before drawing anything. Never infer a
// new installation from an unreadable library, or consume an absent chapter.
export async function prepareGuide(api, available, group = 'studio', chapters = CHAPTERS) {
    const loaded = await readGuideRecord(api);
    if (!loaded.ok) return null;
    let record = loaded.record, returning = false;
    if (!record) {
        try {
            const reply = await api.fetchApi('/freevideo/library?limit=1');
            if (!reply.ok) return null;
            const data = await reply.json();
            if (!Array.isArray(data.items)) return null;
            returning = data.items.length > 0;
        } catch { return null; }
    }
    const steps = guideSteps({record, available, returning, group, chapters});
    record = guideRecord(record, returning ? chaptersFor(group, chapters).filter(c => !c.added && available.includes(c.id)).map(c => c.id) : []);
    if (!steps.length) {
        if (returning) await saveGuideRecord(api, record);
        return null;
    }
    if (!await saveGuideRecord(api, record)) return null;
    return {record, steps};
}

export async function prepareShowcase(api, version, available, group = 'studio') {
    const chapter = showcaseChapter(version);
    if (!chapter) return null;
    // Preserve the normal migration for a returning user without guide.json:
    // acknowledge available basic anchors, but leave every added chapter unseen.
    const inherited = chaptersFor(group).map(c => ({...c, group: 'showcase'}));
    const prepared = await prepareGuide(api, [chapter.id, ...available], 'showcase', [chapter, ...inherited]);
    return prepared?.steps.some(c => c.id === chapter.id) ? {...prepared, chapter, steps: [chapter]} : null;
}

export function showGuide({all = true} = {}) {
    current = [...document.querySelectorAll('dialog[open]')].map(dialog => guides.get(dialog)).filter(Boolean).at(-1) || current;
    return (all ? current?.replay() : current?.show()) || Promise.resolve(false);
}

// A how-to is a short list of steps played over the live interface. The same layer, ring and bubble as the guide are used,
// nothing is recorded, and a step whose control is not on screen shows its picture instead of a ring.
export function showHowTo(steps, options = {}) {
    current = [...document.querySelectorAll('dialog[open]')].map(dialog => guides.get(dialog)).filter(Boolean).at(-1) || current;
    return current?.tour(steps, options) || Promise.resolve(false);
}

const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const visible = e => {
    if (!e?.isConnected || e.closest('[hidden], [inert]') || !e.getClientRects().length) return false;
    for (let parent = e; parent; parent = parent.parentElement) {
        const style = getComputedStyle(parent);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
    }
    return true;
};
const clamp = (n, low, high) => Math.max(low, Math.min(high, n));

// The tutorial book's "walk me through it" buttons call this; the how-tos load only when one is asked for.
if (typeof window !== 'undefined') {
    const howto = window.freevideoHowTo ||= {};
    howto.start ||= id => import('./howto.js').then(({startHowTo}) => startHowTo(id));
    howto.available ||= id => import('./howto.js').then(({howToAvailable}) => howToAvailable(id));
    howto.tryIt = (example, options) => import('./feature_tour.js').then(({tryIt}) => tryIt(example, options));
}

const replayInFront = async () => (await showGuide({all: true})) || import('./tutorial_book.js').then(({openTutorialBook}) => openTutorialBook({chapter: 'quick-start'}));
// Settings › Getting started: replay the three steps, pick a walkthrough, open the examples, and whether feature cards
// come by themselves. The switch is kept in the guide record (an entry in its seen list), so it follows the installation.
export const CARDS_OFF = 'option:feature-cards-off';
const ROW_ICONS = {flag: 'M4 13.5V3M4 3.5h7.5l-1.5 2.5 1.5 2.5H4', play: 'M5 3.2v9.6l7.6-4.8z', img: 'M2.5 3.5h11v9h-11zM2.5 10.5l3-3 3 3 2-2 3 3M10.5 6.5h.01', info: 'M8 13.5A5.5 5.5 0 1 0 8 2.5a5.5 5.5 0 0 0 0 11zM8 7.5v3.5M8 5h.01'};
const rowIcon = name => { const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', ROW_ICONS[name]); svg.append(path); return svg; };
export function createGuideReplayEntry({dialog, cn, api, openWorkspace, replay = replayInFront}) {
    const text = key => REPLAY_TEXT[key][cn ? 1 : 0], t = (en, zh) => cn ? zh : en;
    const tipped = (node, name, use) => { node.dataset.fvTipName = name; node.dataset.fvTipUse = use; return node; };
    const section = el('section', null, 'fv-guide-replay'), row = el('div', null, 'fv-guide-replay-row'), rows = el('div', null, 'fv-guide-rows');
    const line = (icon, name, small, control) => { const item = el('div', null, 'fv-guide-row'), words = el('span'); words.append(el('b', name), el('small', small)); item.append(rowIcon(icon), words, control); return item; };
    const status = el('div', null, 'fv-muted'); status.setAttribute('role', 'status'); status.hidden = true;
    const fail = () => { status.textContent = text('unavailable'); status.hidden = false; };
    const closeSettings = () => new Promise(resolve => { if (!dialog.open) { resolve(); return; } dialog.addEventListener('close', resolve, {once: true}); dialog.close(); });
    const button = tipped(el('button', text('again')), text('again'), t('Close Settings and replay it here', '关闭设置，在当前视图重放')); button.type = 'button';
    // "User guide" lists the walkthroughs; choosing one plays it over the real interface.
    const manual = tipped(el('button', t('Choose', '选择')), text('manual'), t('List the walkthroughs', '列出可演示的操作')); manual.type = 'button';
    manual.setAttribute('aria-haspopup', 'menu'); manual.setAttribute('aria-expanded', 'false');
    const menu = el('div', null, 'fv-guide-howtos'); menu.setAttribute('role', 'menu'); menu.hidden = true;
    const closeMenu = () => { menu.hidden = true; manual.setAttribute('aria-expanded', 'false'); };
    const openBook = tipped(el('button', t('All tutorials', '全部教程')), t('All tutorials', '全部教程'), t('Open every chapter', '打开全部章节')); openBook.type = 'button'; openBook.setAttribute('role', 'menuitem');
    openBook.onclick = async () => {
        closeMenu();
        try { const {openTutorialBook} = await import('./tutorial_book.js'); await closeSettings(); openTutorialBook(); } catch { fail(); }
    };
    manual.onclick = async () => {
        if (!menu.hidden) { closeMenu(); return; }
        try {
            const {availableHowToList, startHowTo} = await import('./howto.js');
            menu.replaceChildren(...(await availableHowToList(cn)).map(item => {
                const choice = tipped(el('button', item.title), item.title, t('Step through it on the real screen', '在真实界面上一步步演示')); choice.type = 'button'; choice.setAttribute('role', 'menuitem');
                choice.onclick = async () => {
                    closeMenu();
                    try {
                        if (!await openWorkspace()) { fail(); return; }
                        await closeSettings();
                        await startHowTo(item.id, {openWorkspace});
                    } catch { fail(); }
                };
                return choice;
            }), el('hr'), openBook);
            menu.hidden = false; manual.setAttribute('aria-expanded', 'true'); menu.querySelector('button')?.focus();
        } catch { fail(); }
    };
    section.addEventListener('keydown', event => {
        if (menu.hidden) return;
        if (event.key === 'Escape') { event.stopPropagation(); event.preventDefault(); closeMenu(); manual.focus(); }
        else if (['ArrowDown', 'ArrowUp'].includes(event.key) && menu.contains(event.target)) {
            const items = [...menu.querySelectorAll('button')], at = items.indexOf(event.target);
            event.preventDefault(); items[(at + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus();
        }
    });
    const examples = tipped(el('button', t('Open', '打开')), t('Feature examples', '功能示例'), t('Open the examples page', '打开教程的示例页')); examples.type = 'button';
    examples.onclick = async () => {
        try { const {openTutorialBook} = await import('./tutorial_book.js'); await closeSettings(); openTutorialBook({tab: 'examples'}); } catch { fail(); }
    };
    // Feature cards that come by themselves: on by default.
    const cards = el('input'); cards.type = 'checkbox'; cards.setAttribute('role', 'switch'); cards.checked = true; cards.disabled = true;
    cards.setAttribute('aria-label', t('Show feature hints', '自动显示功能说明'));
    tipped(cards, t('Show feature hints', '自动显示功能说明'), t('Off: only in the tutorial', '关掉后只在教程里看'));
    readGuideRecord(api).then(loaded => { if (!loaded.ok) return; cards.checked = !loaded.record?.seen.includes(CARDS_OFF); cards.disabled = false; });
    cards.onchange = async () => {
        cards.disabled = true;
        const loaded = await readGuideRecord(api);
        if (loaded.ok) {
            const seen = (loaded.record?.seen || []).filter(id => id !== CARDS_OFF);
            await saveGuideRecord(api, {schema: 1, seen: cards.checked ? seen : [...seen, CARDS_OFF]});
        }
        cards.disabled = false;
    };
    const cardsLabel = el('label', null, 'fv-guide-switch'); cardsLabel.append(cards);
    rows.append(line('flag', text('title'), t('Three steps around Create or Edit', '三步认识创作面板或剪辑视图'), button),
        line('play', text('manual'), t('Pick one to see it on the real screen', '选一项，在真实界面上演示'), manual),
        line('img', t('Feature examples', '功能示例'), t('See results; fill one in', '看成品，一键填入'), examples));
    const hints = el('div', null, 'fv-guide-rows');
    hints.append(line('info', t('Show feature hints', '自动显示功能说明'), t('Explain a feature once, beside it, the first time', '第一次用到某个功能时，在它旁边说明一次'), cardsLabel));
    row.append(el('h3', text('title')), rows, menu, el('h4', t('Hints', '提示'), 'fv-guide-sub'), hints); section.append(row, status);
    button.onclick = async () => {
        button.disabled = true;
        try {
            if (!await openWorkspace()) { fail(); return; }
            // Wait for native focus restoration before focusing the guide.
            await closeSettings();
            await replay({all: true});
        } catch { fail(); }
        finally { button.disabled = false; }
    };
    return section;
}

// Custom targets return viewport rectangles on every layout pass. They never
// insert hit targets or change the editor's pointer handling.
export function guideTarget(dialog, chapter, targets = {}) {
    if (chapter.target) return chapter.target(dialog);
    if (targets[chapter.id]) return targets[chapter.id]();
    const nodes = (chapter.anchors || [chapter.anchor]).map(selector => dialog.querySelector(selector));
    if (nodes.some(node => !visible(node))) return null;
    const rects = nodes.map(node => node.getBoundingClientRect());
    const left = Math.min(...rects.map(r => r.left)), top = Math.min(...rects.map(r => r.top));
    const right = Math.max(...rects.map(r => r.right)), bottom = Math.max(...rects.map(r => r.bottom));
    return {node: nodes[0], rect: {left, top, right, bottom, width: right - left, height: bottom - top}};
}

export function createFirstRunGuide({dialog, api, cn, isBusy = () => false, group = 'studio', targets = {}, onShowcaseTry = null}) {
    const t = (en, zh) => cn ? zh : en;
    const automatic = !attempted.has(group), editor = group === 'editor';
    const chapters = chaptersFor(group);
    if (!document.querySelector('link[data-fv-first-run-guide]')) {
        const css = el('link'); css.rel = 'stylesheet'; css.dataset.fvFirstRunGuide = '';
        css.href = new URL('./first_run_guide.css', import.meta.url).href; document.head.append(css);
    }
    let disposed = false, touched = false, stopped = false, layer = null, bubble = null, ring = null, arrow = null, anchor = null, tourTitle = '';
    let steps = [], index = 0, record = null, manual = false, restoreFocus = null, pollTimer = null, frame = null;
    let serverBlocked = false, checking = false, preparing = false;
    let waiting = null, replayTimer = null, touring = false;
    let showcase = null;
    const heldKeys = new Set();
    const resolved = chapter => {
        const value = guideTarget(dialog, chapter, targets);
        return value && visible(value.node) && value.rect.width > 0 && value.rect.height > 0 ? value : null;
    };
    const available = () => chapters.filter(c => resolved(c)).map(c => c.id);
    const shown = step => resolved(step) || (step.image && touring);
    function blocked() {
        return !dialog.open || !dialog.isConnected || dialog.inert || serverBlocked || isBusy()
            // Create stays open underneath Edit. It is an inactive workspace,
            // while settings, export options and other dialogs still block.
            || [...document.querySelectorAll('dialog[open]')].some(e => e !== dialog
                && !(editor && e.matches('dialog.fv-studio:not(.fv-editor)')))
            || [...document.querySelectorAll('[role="dialog"], [aria-modal="true"], .p-dialog-mask, .p-overlay-mask:not(.p-blockui-mask)')]
                .some(e => e !== dialog && !layer?.contains(e) && visible(e))
            || [...document.querySelectorAll('.fv-failure, .fv-health, .fv-update-notice, .fv-prewarm, [data-fv-guide-blocker]')]
                .some(e => visible(e) && (!e.matches('.fv-prewarm') || e.textContent.trim()));
    }
    async function serverState() {
        try {
            const replies = await Promise.all([api.fetchApi('/queue'), api.fetchApi('/freevideo/setup')]);
            if (replies.some(r => !r.ok)) return false;
            const [queue, setup] = await Promise.all(replies.map(r => r.json()));
            return Array.isArray(queue.queue_running) && Array.isArray(queue.queue_pending)
                && !queue.queue_running.length && !queue.queue_pending.length
                && setup.discovery?.ready === true && !setup.task?.busy;
        } catch { return false; }
    }
    function hide(restore = true) {
        const focused = layer?.contains(document.activeElement), shown = anchor;
        anchor = null; layer = bubble = ring = arrow = null;
        shown?.close('now', 'replace');
        if (restore && focused && restoreFocus?.isConnected) restoreFocus.focus({preventScroll: true});
    }
    function interrupt() { if (automatic && dialog.open) attempted.add(group); stopped = true; touring = false; clearTimeout(pollTimer); hide(); }
    function remember() {
        if (manual) return;
        // Empty projects acknowledge the optional trim chapter too, so importing
        // later cannot turn a completed three-step guide into a second prompt.
        record = guideRecord(record, showcase ? [showcase.id] : editor ? chapters.map(c => c.id) : steps.filter(c => resolved(c)).map(c => c.id));
        // A failed write stays quiet. This page's single attempt is already used.
        saveGuideRecord(api, record).then(ok => { if (!ok && !disposed) interrupt(); });
    }
    function finish() { remember(); stopped = true; touring = false; clearTimeout(pollTimer); hide(); }
    // The layer places and follows the card itself; this only checks that the guide may stay.
    function position() {
        if (!layer) return;
        if (blocked()) { interrupt(); return; }
        anchor?.place();
    }
    const follow = () => { if (layer && frame === null) frame = requestAnimationFrame(() => { frame = null; position(); }); };
    const button = (label, kind, name, use, key = '') => {
        const node = el('button', label, 'fv-tour-btn'); node.type = 'button'; if (kind) node.dataset.kind = kind;
        node.dataset.fvTipName = name; node.dataset.fvTipUse = use; if (key) node.dataset.fvTipKey = key; return node;
    };
    function draw() {
        hide(false);
        if (showcase) {
            // The entry showcase: one card over a light dim, beside the place it starts from when that is on screen.
            const body = el('div', null, 'fv-tour-body');
            const head = el('div', null, 'fv-tour-head'), title = el('h3', t(...SHOWCASE_TEXT.title)); title.id = 'fv-showcase-title';
            const close = el('button', null, 'fv-tour-x'); close.type = 'button'; close.setAttribute('aria-label', t(...SHOWCASE_TEXT.close));
            close.dataset.fvTipName = t(...SHOWCASE_TEXT.close); close.dataset.fvTipUse = t('Hide this; nothing else changes', '收起说明，不影响当前操作'); close.dataset.fvTipKey = 'Esc';
            close.innerHTML = '<svg class="fv-tour-i" viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"/></svg>'; close.onclick = finish;
            head.append(title, close);
            const foot = el('div', null, 'fv-tour-foot'); foot.append(el('span', null, 'fv-tour-grow'));
            const next = button(t(...SHOWCASE_TEXT.try), 'primary', t(...SHOWCASE_TEXT.try), t('Fill this example in Autopilot', '在 AI 全自动里填好这个示例'));
            next.dataset.fvTourPrimary = ''; next.onclick = () => { finish(); onShowcaseTry(t(...SHOWCASE_TEXT.example)); };
            foot.append(next); body.append(head, foot);
            const media = el('div', null, 'fv-tour-mediabox'), figure = el('figure', null, 'fv-tour-media fv-showcase-media'); figure.append(showcase.image); media.append(figure);
            anchor = openAnchor({owner: dialog, target: null, level: 2, shapes: ['stand'], content: [media, body], focus: true, label: title.id,
                onClose: reason => { if (reason !== 'replace') finish(); }});
            layer = anchor.layer; bubble = anchor.pop; layer.dataset.chapter = showcase.id; layer.classList.add('fv-guide-layer');
            remember();
            return;
        }
        if (touring) try { steps[index]?.prepare?.(dialog); } catch { /* the step then falls back to its picture */ }
        resolved(steps[index])?.reveal?.();
        while (steps[index] && !shown(steps[index])) steps.splice(index, 1);
        if (!steps[index]) { interrupt(); return; }
        const step = steps[index], found = resolved(step);
        if (found) {
            const {node, rect: r} = found, bounds = dialog.getBoundingClientRect();
            const clip = node.closest('.fv-controls, .fv-preview-column')?.getBoundingClientRect() || bounds;
            if (!editor && (innerWidth <= 680 || r.top < clip.top + 8 || r.bottom > clip.bottom - 8))
                node.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
        }
        // Words: an eyebrow (where in the guide or walkthrough), a title, one line; Skip / Back / Next.
        const body = el('div', null, 'fv-tour-body'), count = steps.length, last = index === count - 1;
        const eyebrow = el('div', null, 'fv-tour-eyebrow');
        eyebrow.append(el('span', touring ? t('Walkthrough', '演示') : t('Getting started', '新手指引')), el('span', '·'), el('span', `${index + 1} / ${count}`));
        const head = el('div', null, 'fv-tour-head'), title = el('h3', touring ? tourTitle || t('Walkthrough', '演示') : t(...(step.title || ['Getting started', '新手指引'])));
        title.id = 'fv-guide-title';
        head.append(title);
        const message = el('p', cn ? step.zh : step.en, 'fv-tour-text fv-guide-message'); message.id = 'fv-guide-message';
        message.setAttribute('aria-live', 'polite'); message.setAttribute('aria-atomic', 'true');
        const foot = el('div', null, 'fv-tour-foot');
        if (count > 1) {
            const pips = el('span', null, 'fv-tour-pips'); pips.setAttribute('aria-label', `${index + 1} / ${count}`);
            for (let i = 0; i < count; i++) pips.append(el('i', null, i === index ? 'is-on' : ''));
            foot.append(pips);
        }
        foot.append(el('span', null, 'fv-tour-grow'));
        if (!last) {
            const skip = button(touring ? t('Skip', '跳过演示') : t('Skip', '跳过'), '', touring ? t('Skip', '跳过演示') : t('Skip', '跳过'),
                touring ? t('End the walkthrough', '结束演示，回到正常界面') : t('Hide the guide; replay it in Settings', '收起指引，可在设置里重看'), 'Esc');
            skip.classList.add('fv-guide-skip'); skip.onclick = finish; foot.append(skip);
        }
        if (touring && index > 0) {
            const back = button(t('Back', '上一步'), 'fill', t('Back', '上一步'), t('Go back one step', '回到上一步'), '←');
            back.onclick = () => { index--; draw(); }; foot.append(back);
        }
        const next = button(last ? (touring ? t('Done', '完成') : t('Got it', '知道了')) : t('Next', '下一步'), last ? 'fill' : 'primary',
            last ? (touring ? t('Done', '完成') : t('Got it', '知道了')) : t('Next', '下一步'), last ? t('Finish and hide', '结束并收起') : t('See the next step', '看下一步'), last ? '' : '→');
        next.classList.add('fv-guide-next'); next.dataset.fvTourPrimary = '';
        next.onclick = () => { if (last) finish(); else { remember(); index++; draw(); } };
        foot.append(next);
        body.append(eyebrow, head, message, foot);
        const content = [body];
        if (!found && step.image) {
            const media = el('div', null, 'fv-tour-mediabox'), figure = el('figure', null, 'fv-tour-media fv-guide-figure');
            const picture = el('img', null, 'fv-guide-picture'); picture.alt = ''; picture.src = new URL('./howto/' + step.image + (cn ? '-zh' : '-en') + '.png', import.meta.url).href;
            picture.onerror = () => { media.remove(); follow(); }; picture.onload = follow; figure.append(picture); media.append(figure); content.unshift(media);
        }
        const target = found ? () => resolved(step) : null;
        anchor = openAnchor({owner: dialog, target, level: touring ? 1 : 2, shapes: !found && step.image ? ['stand'] : ['text'], ...(found && step.prefer ? {prefer: step.prefer} : {}), content, ghost: !!step.ghost && !touring,
            focus: true, label: title.id, outside: !touring,
            onClose: reason => {
                if (reason === 'replace') return;
                anchor = null; layer = bubble = null;
                // A press elsewhere, Escape or closing the card ends the guide; the view going away or the control
                // disappearing only stops it.
                if (['outside', 'escape', 'api'].includes(reason)) finish(); else interrupt();
            }});
        layer = anchor.layer; bubble = anchor.pop; layer.dataset.chapter = step.id; layer.dataset.group = group; layer.classList.add('fv-guide-layer');
        resizeObserver.disconnect(); if (found) resizeObserver.observe(found.node); resizeObserver.observe(dialog);
    }
    async function poll() {
        if (disposed || stopped || checking) return;
        checking = true; serverBlocked = !await serverState(); checking = false;
        if (disposed || stopped) return;
        if (blocked()) interrupt();
        else pollTimer = setTimeout(poll, 1500);
    }
    async function show(all = false, request = null) {
        const cancelled = () => request && waiting !== request;
        if (all || editor) serverBlocked = false;
        if (disposed || preparing || layer || !dialog.open || blocked() || (!all && (touched || stopped))) return false;
        preparing = true; manual = all;
        await document.fonts.ready;
        if (cancelled() || [...document.querySelectorAll('.p-blockui-mask')].some(visible)) { preparing = false; return false; }
        const ready = await serverState();
        if (editor) serverBlocked = !ready;
        let prepared = null;
        // The showcase is an added chapter, including for returning installations.
        // Manual settings replay stays the ordinary guide. The shared controller
        // supplies queue checks, focus isolation, interruption and disposal.
        if (ready && !all && onShowcaseTry && visible(dialog.querySelector('[data-fv-guide="agent"]')) && !disposed && !blocked() && !touched) {
            try {
                const reply = await api.fetchApi('/freevideo/updates?client=1');
                if (reply.ok) {
                    const entry = await prepareShowcase(api, (await reply.json()).current_release?.product_version, available(), group);
                    if (entry) {
                        const image = await loadShowcaseImage();
                        if (image) { showcase = {...entry.chapter, image}; prepared = entry; }
                    }
                }
            } catch { /* Optional entry presentation never blocks the workspace. */ }
        }
        if (ready && !disposed && !cancelled() && !blocked() && (all || !touched)) {
            if (all) { showcase = null; prepared = {record, steps: guideSteps({available: available(), all: true, group})}; }
            else prepared ||= await prepareGuide(api, available(), group);
        }
        // Media/storage reads can overlap a generation submitted from another tab.
        if (showcase && !all && !await serverState()) { preparing = false; return false; }
        preparing = false;
        if (!prepared?.steps.length || disposed || cancelled() || blocked() || (!all && (touched || stopped))) return false;
        stopped = false; serverBlocked = false; record = prepared.record; steps = prepared.steps; index = 0;
        restoreFocus = document.activeElement; draw();
        if (layer) { clearTimeout(pollTimer); pollTimer = setTimeout(poll, 1500); return true; }
        return false;
    }
    function cancelReplay() { clearTimeout(replayTimer); waiting?.remove(); waiting = null; }
    async function retryReplay() {
        const request = waiting;
        if (!request || disposed || !dialog.open) { cancelReplay(); return; }
        const shown = await show(true, request);
        if (waiting !== request || disposed) return;
        if (shown) cancelReplay();
        else replayTimer = setTimeout(retryReplay, 1500);
    }
    async function replay() {
        if (disposed || !dialog.open) return false;
        attempted.add(group); manual = true; clearTimeout(startTimer); interrupt(); cancelReplay();
        waiting = el('div', null, 'fv-guide-wait');
        const message = el('p', t(...REPLAY_TEXT.waiting)); message.setAttribute('role', 'status');
        const cancel = el('button', t(...REPLAY_TEXT.cancel)); cancel.type = 'button'; cancel.onclick = cancelReplay;
        waiting.append(message, cancel); dialog.append(waiting);
        await retryReplay();
        return true;
    }
    async function tour(list, {title = null} = {}) {
        if (disposed || !dialog.open) return false;
        tourTitle = title ? t(...title) : '';
        attempted.add(group); clearTimeout(startTimer); interrupt(); cancelReplay();
        stopped = false; serverBlocked = false; manual = true; touring = true; record = null;
        steps = list.map(step => ({...step})); index = 0; restoreFocus = document.activeElement;
        draw();
        return Boolean(layer);
    }
    const touch = event => {
        if (touring || !dialog.contains(event.target) || bubble?.contains(event.target)) return;
        if (automatic) attempted.add(group);
        touched = true;
        if (layer) finish(); else stopped = true;
    };
    const key = event => {
        if (!layer) { if (dialog.contains(event.target) && !['Tab', 'Shift', 'Control', 'Alt', 'Meta'].includes(event.key)) touch(event); return; }
        // While a how-to plays the person types and clicks as asked; the card's own keys belong to the layer.
        if (touring) return;
        if (event.key === 'Enter' && bubble?.contains(event.target)) { event.preventDefault(); event.stopImmediatePropagation(); (event.target.closest('button') || bubble.querySelector('[data-fv-tour-primary]'))?.click(); return; }
        if (['Tab', 'Escape', 'F6', 'Shift', 'Control', 'Alt', 'Meta'].includes(event.key) || bubble?.contains(event.target)) return;
        touch(event);
    };
    const keyEnd = () => {};
    const status = event => {
        if (event.detail?.exec_info?.queue_remaining > 0) { serverBlocked = true; if (!editor || layer) interrupt(); }
        else if (layer) poll();
    };
    const execution = () => { serverBlocked = true; if (!editor || layer) interrupt(); };
    // The page can change many times a second (a playing video moves its own controls), and each check reads layout:
    // the first change is judged at once, later ones at most four times a second, so playback stays smooth.
    const CHECK_GAP = 250;
    let checkedAt = -Infinity, checkTimer = 0;
    const check = () => {
        checkTimer = 0; checkedAt = performance.now();
        if (disposed || stopped) return;
        if ((!editor || layer) && (blocked() || layer && [...document.querySelectorAll('.p-blockui-mask')].some(visible))) interrupt(); else follow();
    };
    const observer = new MutationObserver(rows => {
        if (disposed || stopped) return;
        if (rows.every(row => layer?.contains(row.target))) return;
        if (checkTimer) return;
        const wait = checkedAt + CHECK_GAP - performance.now();
        if (wait <= 0) check(); else checkTimer = setTimeout(check, wait);
    });
    observer.observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['hidden', 'open', 'class', 'style', 'inert', 'role', 'aria-modal']});
    const resizeObserver = new ResizeObserver(follow);
    document.addEventListener('pointerdown', touch, true); document.addEventListener('click', touch, true);
    document.addEventListener('dragstart', touch, true); document.addEventListener('dragenter', touch, true);
    document.addEventListener('input', touch, true); document.addEventListener('keydown', key, true);
    document.addEventListener('keypress', keyEnd, true); document.addEventListener('keyup', keyEnd, true);
    window.addEventListener('resize', follow); document.addEventListener('scroll', follow, true);
    // A walk-through belongs to its view: leaving the view ends it, so its ring never stays over another view.
    const leaving = event => { if (layer && event.detail?.element === dialog) interrupt(); };
    window.addEventListener('freevideo-view-leave', leaving);
    // The editor can be opened with a bare request helper that has no events.
    const events = api?.addEventListener && api.removeEventListener;
    if (events) { api.addEventListener('status', status); api.addEventListener('execution_start', execution); }
    const handle = {show, replay, tour, suspend() { cancelReplay(); interrupt(); },
        handleKey(event) {
            if (event.type !== 'keydown' || !layer) return false;
            key(event);
            return event.defaultPrevented;
        }, dispose() {
        disposed = true; clearTimeout(startTimer); clearTimeout(pollTimer); clearTimeout(checkTimer); cancelAnimationFrame(frame);
        cancelReplay();
        hide(); observer.disconnect(); resizeObserver.disconnect();
        document.removeEventListener('pointerdown', touch, true); document.removeEventListener('click', touch, true);
        document.removeEventListener('dragstart', touch, true); document.removeEventListener('dragenter', touch, true);
        document.removeEventListener('input', touch, true); document.removeEventListener('keydown', key, true);
        document.removeEventListener('keypress', keyEnd, true); document.removeEventListener('keyup', keyEnd, true);
        window.removeEventListener('resize', follow); document.removeEventListener('scroll', follow, true);
        window.removeEventListener('freevideo-view-leave', leaving);
        if (events) { api.removeEventListener('status', status); api.removeEventListener('execution_start', execution); }
        guides.delete(dialog);
        if (current === handle) current = [...guides.values()].at(-1) || null;
    }};
    guides.set(dialog, handle);
    const foreground = [...document.querySelectorAll('dialog[open]')].at(-1);
    if (!foreground || foreground === dialog) { current?.suspend(); current = handle; }
    let startTimer = null;
    // Automatic rings and the legacy showcase are retired; tutorials call tour/replay explicitly.
    return handle;
}

// First-run copy stays together for editorial review. The old rings above are only explicit walkthroughs.
export const FIRST_RUN_TEXT = {
    prompt: ['A fox walks through a sunlit forest.', '一只狐狸走过洒满阳光的森林。'],
    describe: ['Describe the scene', '描述画面'],
    generate: ['Generate video', '生成视频'],
    hint: ['Click "Generate video" to get your first video.', '点“生成视频”，得到第一段视频。'],
    close: ['Close', '关闭'],
    next: ['Continue this video', '接着生成这条视频'],
    longer: ['Extend', '接着生成'],
    edit: ['Edit', '剪辑'],
};
export const FIRST_START = 'first-run:started:0.4.21';
export const FIRST_NEXT = 'first-run:next:0.4.21';

export async function prepareFirstStart(api) {
    const loaded = await readGuideRecord(api);
    if (!loaded.ok) return null;
    if (loaded.record) return {firstRun: loaded.record.seen.includes(FIRST_START) && !loaded.record.seen.includes(FIRST_NEXT), bubble: false};
    try {
        const reply = await api.fetchApi('/freevideo/library?limit=1');
        if (!reply.ok) return null;
        const data = await reply.json();
        if (!Array.isArray(data.items) || data.items.length) return null;
    } catch { return null; }
    if (!await saveGuideRecord(api, guideRecord(null, [FIRST_START]))) return null;
    return {firstRun: true, bubble: true};
}

export async function openFirstNextLonger() {
    const tutorial = window.freevideoTutorial;
    if (typeof tutorial === 'function') return tutorial({chapter: 'long-video'});
    if (typeof tutorial?.open === 'function') return tutorial.open({chapter: 'long-video'});
    try {
        const {openTutorialBook} = await import('./tutorial_book.js');
        return openTutorialBook({chapter: 'long-video'});
    } catch {
        return (await import('./feature_tour.js')).openFeatureTour({start: 'extend'});
    }
}

export function createFirstStart({dialog, api, cn, prompt, onPrefill, onGenerate, onEdit,
    onLonger = openFirstNextLonger, isBusy = () => false}) {
    const text = key => FIRST_RUN_TEXT[key][cn ? 1 : 0];
    let disposed = false, hint = null, next = null, timer = null, pending = null, showing = false;
    const hideBubble = () => { const shown = hint; hint = null; shown?.close('fade', 'replace'); };
    const tipped = (node, name, use, key = '') => { node.dataset.fvTipName = name; node.dataset.fvTipUse = use; if (key) node.dataset.fvTipKey = key; return node; };
    // A one-line hint above "Generate video" (level 0: no dim, no ring); a press anywhere or Escape puts it away.
    const ready = prepareFirstStart(api).then(state => {
        if (disposed || !state?.bubble || !dialog.open || isBusy()) return state;
        // Never overwrite a draft that was typed while the record/library reads were in flight.
        if (!onPrefill(text('prompt'))) return state;
        const body = el('div', null, 'fv-tour-body'); body.append(el('p', text('hint'), 'fv-tour-text'));
        hint = openAnchor({owner: dialog, target: () => {
            const node = dialog.querySelector('[data-fv-guide="generate"]') || prompt, r = node?.getBoundingClientRect();
            return r && r.width > 0 ? {node, rect: r} : null;
        }, level: 0, shapes: ['hint'], prefer: ['above', 'left', 'right', 'below'], content: body,
            onClose: reason => { if (reason !== 'replace') hint = null; }});
        hint.layer.classList.add('fv-first-start'); hint.pop.setAttribute('aria-label', text('describe'));
        prompt.focus({preventScroll: true}); return state;
    });
    async function checkNext() {
        clearTimeout(timer); timer = null;
        const state = await ready;
        if (disposed || showing || !pending || !state?.firstRun) return;
        const top = [...document.querySelectorAll('dialog[open]')].at(-1);
        if (!dialog.open || top !== dialog || isBusy()) { timer = setTimeout(checkNext, 250); return; }
        showing = true; hideBubble();
        const loaded = await readGuideRecord(api);
        if (disposed || !loaded.ok || loaded.record?.seen.includes(FIRST_NEXT)) return;
        if (!await saveGuideRecord(api, guideRecord(loaded.record, [FIRST_NEXT])) || disposed) return;
        // The first video is done: the next step, beside "Extend" (it does not take the keyboard).
        const body = el('div', null, 'fv-tour-body'), head = el('div', null, 'fv-tour-head'), title = el('h3', text('next')); title.id = 'fv-next-title';
        const close = el('button', null, 'fv-tour-x'); close.type = 'button'; close.setAttribute('aria-label', text('close'));
        close.innerHTML = '<svg class="fv-tour-i" viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"/></svg>';
        tipped(close, text('close'), cn ? '收起说明，不影响当前操作' : 'Hide this; nothing else changes', 'Esc');
        head.append(title, close);
        const foot = el('div', null, 'fv-tour-foot'); foot.append(el('span', null, 'fv-tour-grow'));
        const edit = tipped(el('button', text('edit'), 'fv-tour-btn'), text('edit'), cn ? '放进剪辑视图继续改' : 'Open it in Edit');
        const longer = tipped(el('button', text('longer'), 'fv-tour-btn fv-primary'), text('longer'), cn ? '从结尾生成下一段' : 'Make the next part from the end');
        edit.type = longer.type = 'button'; edit.dataset.kind = 'fill'; longer.dataset.kind = 'primary'; longer.dataset.fvTourPrimary = '';
        foot.append(edit, longer);
        body.append(head, el('p', cn ? '从结尾继续下一段，或到剪辑视图里排列。' : 'Continue from the end, or arrange it in Edit.', 'fv-tour-text'), foot);
        const leave = async action => { next?.close('now', 'replace'); next = null; await action(); };
        // The real result button continues the clip directly; the tutorial is the fallback when it is not on screen.
        longer.onclick = () => leave(() => { const real = dialog.querySelector('.fv-action-extend'); return real && !real.disabled ? real.click() : onLonger(); });
        edit.onclick = () => leave(onEdit); close.onclick = () => next?.close('fade', 'api');
        const anchorNode = dialog.querySelector('.fv-action-extend');
        next = openAnchor({owner: dialog, target: anchorNode && anchorNode.getClientRects().length ? anchorNode : null, level: 0, shapes: ['text'], prefer: ['above', 'right', 'left', 'below'],
            content: body, label: title.id, onClose: reason => { if (reason !== 'replace') next = null; }});
        next.layer.classList.add('fv-next-step');
    }
    const dismiss = event => { if (event.key === 'Escape' || event.target?.closest?.('[data-fv-guide="generate"]')) hideBubble(); };
    dialog.addEventListener('click', dismiss); dialog.addEventListener('keydown', dismiss);
    return {ready, dismiss: hideBubble,
        complete(result) { if (!result?.video || result.preview || result.partial || disposed) return; pending = result; void checkNext(); },
        dispose() {
            disposed = true; clearTimeout(timer); hideBubble(); next?.close('now', 'replace'); next = null;
            dialog.removeEventListener('click', dismiss); dialog.removeEventListener('keydown', dismiss);
        }};
}
