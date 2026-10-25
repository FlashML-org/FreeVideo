import {SHOWCASE_TEXT, showcaseChapter, loadShowcaseImage} from './entry_showcase.js';

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

export const CHAPTERS = [
    {id: 'describe', anchor: '[data-fv-guide="describe"]', added: false,
        en: 'Type your prompt here. You can have an agent follow the Skill at the top right, or turn on FreeToken enhancement.',
        zh: '在这里输入提示词。可以让 Agent 参⁠考 Skill，或开启 FreeToken 提示词增强。'},
    {id: 'media', anchor: '[data-fv-guide="media"]', added: true,
        en: 'Add reference images or a first or last frame here. Type @ in the prompt to use them.',
        zh: '在此添加参\u2060考\u2060图、首\u2060帧或尾\u2060帧，并在描述中输入 @ 引用。'},
    {id: 'range', anchor: '[data-fv-guide="range"]', added: true,
        en: 'See the available sizes and lengths here, and how long each takes.', zh: '在这里查看可生成的尺\u2060寸、时长和预计用时。'},
    {id: 'generate', anchor: '[data-fv-guide="generate"]', added: false,
        en: 'Choose "Generate video". Progress and the time left appear in the preview.', zh: '点“生成视频”，进度和剩\u2060余\u2060时\u2060间会显示在预览区。'},
    {id: 'longer', anchor: '[data-fv-guide="longer"]', added: true,
        en: 'For a longer video, enter a longer duration and it is made in parts automatically. You can also add parts one by one in "Edit".',
        zh: '填写更长的时长会自动分段生成，也可在“剪辑视图”中逐段接续。'},
    {id: 'agent', anchor: '[data-fv-guide="agent"]', added: true,
        en: 'To have an AI agent such as Claude Code or Codex make videos for you, choose "Autopilot".', zh: '要让 Claude Code、Codex 等 Agent 生\u2060成\u2060视\u2060频，点“AI 全自动”。'},
    {id: 'editor-timeline', group: 'editor', anchor: '[data-fv-guide="editor-timeline"]', added: true,
        en: 'Drag a video here.', zh: '把视频拖到这里。'},
    {id: 'editor-trim', group: 'editor', anchor: '[data-fv-guide="editor-trim"]', added: true,
        en: 'Drag the right edge to make this part longer.', zh: '拖右边缘，把这一段拉长。'},
    {id: 'editor-empty', group: 'editor', anchor: '[data-fv-guide="editor-empty"]', added: true,
        en: 'Drag in an empty spot to add a new part.', zh: '在空白处拖出新的一段。'},
    {id: 'editor-describe', group: 'editor', anchor: '[data-fv-guide="editor-describe"]',
        anchors: ['[data-fv-guide="editor-describe"]', '[data-fv-guide="editor-generate"]'], added: true,
        en: 'Describe it, then choose "Generate".', zh: '填写描述，然后点“\u2060生\u2060成\u2060”\u2060。'},
];

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
export function showHowTo(steps) {
    current = [...document.querySelectorAll('dialog[open]')].map(dialog => guides.get(dialog)).filter(Boolean).at(-1) || current;
    return current?.tour(steps) || Promise.resolve(false);
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

export function createGuideReplayEntry({dialog, cn, api, openWorkspace, replay = () => import('./tutorial_book.js').then(({openTutorialBook}) => openTutorialBook({chapter: 'quick-start'}))}) {
    const text = key => REPLAY_TEXT[key][cn ? 1 : 0];
    const section = el('section', null, 'fv-guide-replay'), row = el('div', null, 'fv-guide-replay-row');
    const button = el('button', text('again')); button.type = 'button';
    const status = el('div', null, 'fv-muted'); status.setAttribute('role', 'status'); status.hidden = true;
    const actions = el('div', null, 'fv-guide-replay-actions');
    // "User guide" lists a few how-tos; choosing one plays it over the real interface.
    const manual = el('button', text('manual')); manual.type = 'button'; manual.setAttribute('aria-haspopup', 'menu'); manual.setAttribute('aria-expanded', 'false');
    const menu = el('div', null, 'fv-guide-howtos'); menu.setAttribute('role', 'menu'); menu.hidden = true;
    const closeMenu = () => { menu.hidden = true; manual.setAttribute('aria-expanded', 'false'); };
    // The last row opens the whole tutorial book; Settings closes first so a how-to started from the book plays over the workspace.
    const openBook = el('button', cn ? '教程' : 'Tutorial'); openBook.type = 'button'; openBook.setAttribute('role', 'menuitem');
    openBook.onclick = async () => {
        closeMenu();
        try {
            const {openTutorialBook} = await import('./tutorial_book.js');
            await new Promise(resolve => { dialog.addEventListener('close', resolve, {once: true}); dialog.close(); });
            openTutorialBook();
        } catch { status.textContent = text('unavailable'); status.hidden = false; }
    };
    manual.onclick = async () => {
        if (!menu.hidden) { closeMenu(); return; }
        try {
            const {availableHowToList, startHowTo} = await import('./howto.js');
            menu.replaceChildren(...(await availableHowToList(cn)).map(item => {
                const choice = el('button', item.title); choice.type = 'button'; choice.setAttribute('role', 'menuitem');
                choice.onclick = async () => {
                    closeMenu();
                    try {
                        if (!await openWorkspace()) { status.textContent = text('unavailable'); status.hidden = false; return; }
                        await new Promise(resolve => { dialog.addEventListener('close', resolve, {once: true}); dialog.close(); });
                        await startHowTo(item.id, {openWorkspace});
                    } catch { status.textContent = text('unavailable'); status.hidden = false; }
                };
                return choice;
            }), openBook);
            menu.hidden = false; manual.setAttribute('aria-expanded', 'true');
        } catch { status.textContent = text('unavailable'); status.hidden = false; }
    };
    section.addEventListener('keydown', event => { if (event.key === 'Escape' && !menu.hidden) { event.stopPropagation(); closeMenu(); manual.focus(); } });
    const examples = el('button', cn ? '功能示例' : 'Feature examples'); examples.type = 'button';
    examples.onclick = async () => {
        const {openFeatureTour} = await import('./feature_tour.js');
        await new Promise(resolve => { dialog.addEventListener('close', resolve, {once: true}); dialog.close(); });
        await openFeatureTour({grid: true, cn, api, owner: null});
    };
    actions.append(manual, button, examples);
    row.append(el('h3', text('title')), actions); section.append(row, menu, status);
    button.onclick = async () => {
        button.disabled = true;
        try {
            if (!await openWorkspace()) { status.textContent = text('unavailable'); status.hidden = false; return; }
            // Wait for native focus restoration before focusing the guide.
            await new Promise(resolve => {
                dialog.addEventListener('close', resolve, {once: true});
                dialog.close();
            });
            await replay({all: true});
        } catch { status.textContent = text('unavailable'); status.hidden = false; }
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
    let disposed = false, touched = false, stopped = false, layer = null, bubble = null, ring = null, arrow = null;
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
        const focused = layer?.contains(document.activeElement);
        layer?.remove(); layer = bubble = ring = arrow = null;
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
    function position() {
        if (!layer) return;
        if (blocked()) { interrupt(); return; }
        if (showcase) return; // The entry card is centered with CSS at every size.
        let value = resolved(steps[index]);
        if (value?.reveal) { value.reveal(); value = resolved(steps[index]); }
        if (!value && steps[index].image && touring) {
            // No control to point at: the picture stands in for it, in the middle of the window.
            const frame = dialog.getBoundingClientRect(), wide = Math.min(440, frame.width - 48);
            ring.hidden = arrow.hidden = true; bubble.style.width = `${wide}px`;
            const box = bubble.getBoundingClientRect();
            Object.assign(bubble.style, {left: `${clamp(frame.left + (frame.width - box.width) / 2, 12, innerWidth - box.width - 12)}px`,
                top: `${clamp(frame.top + (frame.height - box.height) / 2, 12, innerHeight - box.height - 12)}px`});
            bubble.dataset.side = 'center';
            return;
        }
        if (!value) {
            steps.splice(index, 1);
            if (!steps.length) interrupt();
            else { index = Math.min(index, steps.length - 1); draw(); }
            return;
        }
        const {node, rect: raw} = value, bounds = dialog.getBoundingClientRect();
        const margin = editor ? 12 : 24;
        const xmin = Math.max(margin, bounds.left + 12), ymin = Math.max(margin, bounds.top + 12);
        const xmax = Math.min(innerWidth - margin, bounds.right - 12), ymax = Math.min(innerHeight - margin, bounds.bottom - 12);
        const r = editor ? {left: Math.max(0, raw.left), top: Math.max(0, raw.top),
            right: Math.min(innerWidth, raw.right), bottom: Math.min(innerHeight, raw.bottom)} : raw;
        r.width = r.right - r.left; r.height = r.bottom - r.top;
        if (r.width <= 0 || r.height <= 0) { interrupt(); return; }
        bubble.style.width = `${Math.min(340, xmax - xmin)}px`;
        const b = bubble.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        const x = clamp(cx - b.width / 2, xmin, xmax - b.width), y = clamp(cy - b.height / 2, ymin, ymax - b.height);
        const below = {side: 'below', x, y: r.bottom + 24}, above = {side: 'above', x, y: r.top - b.height - 24};
        const narrow = innerWidth <= 680;
        const candidates = editor
            ? steps[index].id === 'editor-refs' && narrow ? [below, above]  // the description box stays in view
                : [{...above, x: steps[index].id === 'editor-describe' ? clamp(r.left, xmin, xmax - b.width) : x}]
            : steps[index].id === 'longer'
            ? [{side: 'below', x: clamp(r.right - b.width, xmin, xmax - b.width), y: r.bottom + 24}, above, below]
            : narrow ? [{side: 'below', x, y: ymax - b.height}, above, below]
                : [{side: 'right', x: r.right + 24, y}, {side: 'left', x: r.left - b.width - 24, y}, below, above];
        const fits = c => c.x >= xmin && c.y >= ymin && c.x + b.width <= xmax + .5 && c.y + b.height <= ymax + .5
            && !(c.x < r.right + 8 && c.x + b.width > r.left - 8 && c.y < r.bottom + 8 && c.y + b.height > r.top - 8);
        // A how-to's ring may be a whole section; with no clear spot the bubble sits over its lower part rather than ending the walk-through.
        const place = candidates.find(fits) || (touring && !editor ? candidates[0] : null);
        if (!place) { interrupt(); return; }
        const style = getComputedStyle(node);
        const pad = editor ? 0 : 4;
        Object.assign(ring.style, {left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + 2 * pad}px`, height: `${r.height + 2 * pad}px`,
            borderRadius: ['borderTopLeftRadius', 'borderTopRightRadius', 'borderBottomRightRadius', 'borderBottomLeftRadius'].map(n => `${(parseFloat(style[n]) || 0) + pad}px`).join(' ')});
        Object.assign(bubble.style, {left: `${place.x}px`, top: `${place.y}px`}); bubble.dataset.side = place.side;
        arrow.style.left = arrow.style.top = '';
        if (['left', 'right'].includes(place.side)) arrow.style.top = `${clamp(cy - place.y - 6, 20, b.height - 32)}px`;
        else arrow.style.left = `${clamp(cx - place.x - 6, 20, b.width - 32)}px`;
    }
    const follow = () => { if (layer && frame === null) frame = requestAnimationFrame(() => { frame = null; position(); }); };
    function draw() {
        hide(false);
        if (showcase) {
            layer = el('div', null, 'fv-guide-layer fv-entry-showcase'); layer.dataset.chapter = showcase.id;
            bubble = el('section', null, 'fv-showcase-card'); bubble.setAttribute('role', 'dialog'); bubble.tabIndex = -1;
            bubble.setAttribute('aria-modal', 'true'); bubble.setAttribute('aria-labelledby', 'fv-showcase-title');
            const title = el('h2', t(...SHOWCASE_TEXT.title)); title.id = 'fv-showcase-title';
            const close = el('button', '×', 'fv-showcase-close'); close.type = 'button';
            close.setAttribute('aria-label', t(...SHOWCASE_TEXT.close)); close.onclick = finish;
            const next = el('button', t(...SHOWCASE_TEXT.try), 'fv-guide-next'); next.type = 'button';
            next.onclick = () => { finish(); onShowcaseTry(t(...SHOWCASE_TEXT.example)); };
            bubble.append(close, title, showcase.image, next); layer.append(bubble); dialog.append(layer);
            position();
            if (layer) { bubble.focus({preventScroll: true}); remember(); }
            return;
        }
        if (touring) try { steps[index]?.prepare?.(dialog); } catch { /* the step then falls back to its picture */ }
        resolved(steps[index])?.reveal?.();
        while (steps[index] && !shown(steps[index])) steps.splice(index, 1);
        if (!steps[index]) { interrupt(); return; }
        const found = resolved(steps[index]), bounds = dialog.getBoundingClientRect();
        if (found) {
            const {node, rect: r} = found;
            const clip = node.closest('.fv-controls, .fv-preview-column')?.getBoundingClientRect() || bounds;
            if (!editor && (innerWidth <= 680 || r.top < clip.top + 8 || r.bottom > clip.bottom - 8))
                node.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
        }
        layer = el('div', null, 'fv-guide-layer'); layer.dataset.chapter = steps[index].id; layer.dataset.group = group;
        ring = el('div', null, 'fv-guide-ring'); ring.setAttribute('aria-hidden', 'true');
        bubble = el('div', null, 'fv-guide-bubble'); bubble.setAttribute('role', 'dialog'); bubble.setAttribute('aria-modal', 'false');
        bubble.setAttribute('aria-label', t('Getting started', '使用指引')); bubble.setAttribute('aria-describedby', 'fv-guide-message');
        const message = el('p', cn ? steps[index].zh : steps[index].en, 'fv-guide-message'); message.id = 'fv-guide-message';
        message.setAttribute('aria-live', 'polite'); message.setAttribute('aria-atomic', 'true');
        const footer = el('div', null, 'fv-guide-footer'), dots = el('div', null, 'fv-guide-dots'), actions = el('div', null, 'fv-guide-actions');
        dots.setAttribute('aria-label', t('Guide progress', '指引进度'));
        steps.forEach((_, i) => { const dot = el('span', null, 'fv-guide-dot' + (i === index ? ' is-current' : '')); if (i === index) dot.setAttribute('aria-current', 'step'); dots.append(dot); });
        if (index < steps.length - 1) {
            const skip = el('button', t('Skip', '跳过'), 'fv-guide-skip'); skip.type = 'button'; skip.onclick = finish; actions.append(skip);
        }
        const next = el('button', index === steps.length - 1 ? t('Got it', '知道了') : t('Next', '下一步'), 'fv-guide-next');
        next.type = 'button'; next.onclick = () => { if (index === steps.length - 1) finish(); else { remember(); index++; draw(); } };
        actions.append(next); footer.append(dots, actions); arrow = el('span', null, 'fv-guide-arrow'); arrow.setAttribute('aria-hidden', 'true');
        if (!found && steps[index].image) {
            const picture = el('img', null, 'fv-guide-picture'); picture.alt = ''; picture.src = new URL('./howto/' + steps[index].image + (cn ? '-zh' : '-en') + '.png', import.meta.url).href;
            picture.onerror = () => { picture.remove(); follow(); }; picture.onload = follow; bubble.prepend(picture);
        }
        bubble.append(message, footer, arrow); layer.append(ring, bubble); dialog.append(layer);
        resizeObserver.disconnect(); if (found) resizeObserver.observe(found.node); resizeObserver.observe(dialog);
        position(); if (layer) next.focus({preventScroll: true});
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
    async function tour(list) {
        if (disposed || !dialog.open) return false;
        attempted.add(group); clearTimeout(startTimer); interrupt(); cancelReplay();
        stopped = false; serverBlocked = false; manual = true; touring = true; record = null;
        steps = list.map(step => ({...step})); index = 0; restoreFocus = document.activeElement;
        draw();
        return Boolean(layer);
    }
    const touch = event => {
        if (touring || !dialog.contains(event.target) || layer?.contains(event.target)) return;
        if (automatic) attempted.add(group);
        touched = true;
        if (layer) finish(); else stopped = true;
    };
    const key = event => {
        // While a how-to plays the person types and clicks as asked; only Escape belongs to the bubble.
        if (layer && touring && event.key !== 'Escape' && !bubble.contains(event.target)) return;
        if (layer) {
            heldKeys.add(event.key); event.stopImmediatePropagation();
            if (event.key === 'Escape' || event.key === 'Enter') {
                event.preventDefault();
                if (event.key === 'Escape') finish();
                else (showcase && document.activeElement === bubble.querySelector('.fv-showcase-close')
                    ? document.activeElement : bubble.querySelector('.fv-guide-next')).click();
            } else if (event.key === 'Tab') {
                event.preventDefault();
                const buttons = [...bubble.querySelectorAll('button')], at = buttons.indexOf(document.activeElement);
                buttons[(at + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length].focus();
            }
            return;
        }
        if (!dialog.contains(event.target)) return;
        if (!['Tab', 'Shift', 'Control', 'Alt', 'Meta'].includes(event.key)) touch(event);
    };
    const keyEnd = event => {
        if (layer || heldKeys.has(event.key)) event.stopImmediatePropagation();
        if (event.type === 'keyup') heldKeys.delete(event.key);
    };
    const status = event => {
        if (event.detail?.exec_info?.queue_remaining > 0) { serverBlocked = true; if (!editor || layer) interrupt(); }
        else if (layer) poll();
    };
    const execution = () => { serverBlocked = true; if (!editor || layer) interrupt(); };
    const observer = new MutationObserver(rows => {
        if (disposed || stopped) return;
        if (rows.every(row => layer?.contains(row.target))) return;
        if ((!editor || layer) && (blocked() || layer && [...document.querySelectorAll('.p-blockui-mask')].some(visible))) interrupt(); else follow();
    });
    observer.observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['hidden', 'open', 'class', 'style', 'inert', 'role', 'aria-modal']});
    const resizeObserver = new ResizeObserver(follow);
    document.addEventListener('pointerdown', touch, true); document.addEventListener('click', touch, true);
    document.addEventListener('dragstart', touch, true); document.addEventListener('dragenter', touch, true);
    document.addEventListener('input', touch, true); document.addEventListener('keydown', key, true);
    document.addEventListener('keypress', keyEnd, true); document.addEventListener('keyup', keyEnd, true);
    window.addEventListener('resize', follow); document.addEventListener('scroll', follow, true);
    // The editor can be opened with a bare request helper that has no events.
    const events = api?.addEventListener && api.removeEventListener;
    if (events) { api.addEventListener('status', status); api.addEventListener('execution_start', execution); }
    const handle = {show, replay, tour, suspend() { cancelReplay(); interrupt(); },
        handleKey(event) {
            if (!['keydown', 'keypress', 'keyup'].includes(event.type)) return false;
            const handled = !!layer || heldKeys.has(event.key);
            if (event.type === 'keydown') key(event); else keyEnd(event);
            return handled;
        }, dispose() {
        disposed = true; clearTimeout(startTimer); clearTimeout(pollTimer); cancelAnimationFrame(frame);
        cancelReplay();
        hide(); observer.disconnect(); resizeObserver.disconnect();
        document.removeEventListener('pointerdown', touch, true); document.removeEventListener('click', touch, true);
        document.removeEventListener('dragstart', touch, true); document.removeEventListener('dragenter', touch, true);
        document.removeEventListener('input', touch, true); document.removeEventListener('keydown', key, true);
        document.removeEventListener('keypress', keyEnd, true); document.removeEventListener('keyup', keyEnd, true);
        window.removeEventListener('resize', follow); document.removeEventListener('scroll', follow, true);
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
    if (!document.querySelector('link[data-fv-first-run-guide]')) {
        const css = el('link'); css.rel = 'stylesheet'; css.dataset.fvFirstRunGuide = '';
        css.href = new URL('./first_run_guide.css', import.meta.url).href; document.head.append(css);
    }
    let disposed = false, bubble = null, next = null, timer = null, pending = null, showing = false;
    const hideBubble = () => { bubble?.remove(); bubble = null; };
    const position = () => {
        if (!bubble) return;
        const target = dialog.querySelector('[data-fv-guide="generate"]') || prompt, r = target.getBoundingClientRect(), b = bubble.getBoundingClientRect();
        // Above the primary button, so neither the prompt nor the button itself is covered.
        // On a narrow screen the button is below the fold; the hint then waits at the bottom edge until it scrolls into view.
        const visible = r.bottom > 0 && r.top < innerHeight - 12, above = visible && r.top - b.height - 12 >= 12;
        bubble.classList.toggle('fv-first-start-above', above);
        bubble.classList.toggle('fv-first-start-dock', !visible);
        bubble.style.left = `${clamp(r.left + r.width / 2 - b.width / 2, 12, innerWidth - b.width - 12)}px`;
        bubble.style.top = `${!visible ? innerHeight - b.height - 12 : above ? r.top - b.height - 12 : Math.min(r.bottom + 12, innerHeight - b.height - 12)}px`;
    };
    const ready = prepareFirstStart(api).then(state => {
        if (disposed || !state?.bubble || !dialog.open || isBusy()) return state;
        // Never overwrite a draft that was typed while the record/library reads were in flight.
        if (!onPrefill(text('prompt'))) return state;
        bubble = el('aside', null, 'fv-first-start'); bubble.setAttribute('aria-label', text('describe'));
        const arrow = el('span', null, 'fv-first-start-arrow'); arrow.setAttribute('aria-hidden', 'true');
        bubble.append(arrow, el('p', text('hint'))); dialog.append(bubble); position();
        // The stylesheet and the dialog's opening motion settle after this first measurement.
        globalThis.requestAnimationFrame?.(() => requestAnimationFrame(position)); document.querySelector('link[data-fv-first-run-guide]')?.addEventListener?.('load', position, {once: true});
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
        next = el('aside', null, 'fv-next-step'); next.setAttribute('aria-label', text('next'));
        const actions = el('div', null, 'fv-next-actions'), close = el('button', null, 'fv-next-close'); close.type = 'button'; close.textContent = '✕'; close.setAttribute('aria-label', text('close'));
        const longer = el('button', text('longer'), 'fv-primary'), edit = el('button', text('edit')); longer.type = edit.type = 'button';
        const leave = async action => { next?.remove(); next = null; await action(); };
        // The real result button continues the clip directly; the tutorial is the fallback when it is not on screen.
        longer.onclick = () => leave(() => { const real = dialog.querySelector('.fv-action-extend'); return real && !real.disabled ? real.click() : onLonger(); });
        edit.onclick = () => leave(onEdit); close.onclick = () => { next?.remove(); next = null; };
        actions.append(longer, edit); next.append(close, actions); dialog.append(next); placeNext();
        window.addEventListener('resize', placeNext); longer.focus({preventScroll: true});
    }
    function placeNext() {
        if (!next) return;
        const anchor = dialog.querySelector('.fv-action-extend'), b = next.getBoundingClientRect(), r = anchor?.getBoundingClientRect();
        const left = r ? r.left + r.width / 2 - b.width / 2 : (innerWidth - b.width) / 2, top = r ? r.top - b.height - 12 : innerHeight - b.height - 24;
        next.style.left = `${clamp(left, 12, innerWidth - b.width - 12)}px`; next.style.top = `${clamp(top, 12, innerHeight - b.height - 12)}px`;
    }
    const dismiss = event => { if (event.key === 'Escape' || event.target?.closest?.('[data-fv-guide="generate"]')) hideBubble(); };
    dialog.addEventListener('click', dismiss); dialog.addEventListener('keydown', dismiss);
    window.addEventListener('resize', position); dialog.addEventListener('scroll', position, true);
    return {ready, dismiss: hideBubble,
        complete(result) { if (!result?.video || result.preview || result.partial || disposed) return; pending = result; void checkNext(); },
        dispose() {
            disposed = true; clearTimeout(timer); hideBubble(); next?.remove(); window.removeEventListener('resize', placeNext);
            dialog.removeEventListener('click', dismiss); dialog.removeEventListener('keydown', dismiss);
            window.removeEventListener('resize', position); dialog.removeEventListener('scroll', position, true);
        }};
}
