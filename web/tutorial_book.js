// The tutorial: a booklet over a light dim for reading (contents with search and groups, one chapter per page, its
// steps with "Take me there" on the step that has a control, the limits, previous / next), an Examples page (the
// feature examples: see one, fill it in, or be shown its control), and a follow-along card: "Follow along" folds the
// booklet into a small card beside the work, step by step, so nothing is blocked while the person does it.
import {TUTORIAL_CHAPTERS, TUTORIAL_ALIASES} from './tutorial_book_content.js';
import {FEATURE_TOUR, readyCards} from './feature_tour_content.js';
import {SHOWCASE_TEXT, SHOWCASE_ASSET} from './entry_showcase.js';
import {isolateDialogKeys, listenDialogKey, routeDialogKey} from './dialog_keys.js';
import {reducedMotion} from './context_menu.js';
import { isChinese } from './i18n.js';

const COPY = {
    zh: {book: '教程', chapters: '章节', examples: '示例', contents: '目录', close: '关闭', search: '搜索教程', steps: '步骤', limits: '注意',
        follow: '跟着做', tryIt: '试一试', walk: '演示', goto: '带我去', prev: '上一篇', next: '下一篇', read: '已看过', none: '没有找到',
        noneHint: '换个词，或看“出错了怎么办”。', start: '新手指引', startLine: '三步认识界面', again: '再看一遍', featured: '精选', getStarted: '开始使用',
        back: '示例', feature: '功能示例', fills: '描述、素材和参数会一起填好', prompt: '描述', newBadge: '新', play: '播放示例', playSound: '播放声音',
        stepOf: (a, b) => `第 ${a} / ${b} 步`, done: '全部完成', doneNext: '做完了。下一篇：', stepDone: '这步做好了', stepBack: '上一步', expand: '展开教程',
        noView: '无法打开剪辑视图，请点顶部的“剪辑视图”。', noTarget: '当前界面中找不到这一项，请按上面的步骤操作。', noHidden: '时间线上还没有带声音的片段，请先导入或生成一段。',
        showcaseLine: '用白模定动作和镜头，生成写实视频。',
        tips: {close: '收起教程，回到刚才的界面', chapters: '按步骤学每个功能', examples: '看成品，一键填入示例', toc: '看全部章节', chapter: '打开这一章',
            follow: '教程收成小卡片，边看边操作', tryIt: '把示例的描述和素材填进去', walk: '在真实界面上一步步走一遍', goto: '收起教程，标出界面上的位置',
            again: '在当前界面重放三步指引', search: '按标题、说明和步骤找', prev: '打开上一篇', next: '打开下一篇', example: '看这个示例', back: '回到全部示例',
            read: '打开讲这个功能的那一章', getStarted: '在 AI 全自动里填好这个示例', expand: '回到教程这一章', followClose: '收起，不影响当前操作',
            stepDone: '标记完成，看下一步', stepBack: '回到上一步', drag: '拖到别的角，松手吸附', play: '播放这段示例'}},
    en: {book: 'Tutorials', chapters: 'Chapters', examples: 'Examples', contents: 'Contents', close: 'Close', search: 'Search tutorials', steps: 'Steps', limits: 'Good to know',
        follow: 'Follow along', tryIt: 'Try it', walk: 'Walkthrough', goto: 'Take me there', prev: 'Previous', next: 'Next', read: 'Read', none: 'Nothing found',
        noneHint: 'Try another word, or see "When something goes wrong".', start: 'Getting started', startLine: 'Three steps around the screen', again: 'Show again',
        featured: 'Featured', getStarted: 'Get started', back: 'Examples', feature: 'Feature example', fills: 'Fills the prompt, media and settings together', prompt: 'Prompt',
        newBadge: 'New', play: 'Play example', playSound: 'Play with sound',
        stepOf: (a, b) => `Step ${a} of ${b}`, done: 'All done', doneNext: 'Done. Next: ', stepDone: 'Done this step', stepBack: 'Back', expand: 'Open the tutorial',
        noView: 'Could not open the Edit view. Choose "Edit" at the top.', noTarget: 'This item is not available here. Follow the steps above.', noHidden: 'The timeline has no clip with sound yet. Import or generate one first.',
        showcaseLine: 'A whitebox sets the motion and camera; the video comes out real.',
        tips: {close: 'Close and go back to where you were', chapters: 'Learn each feature step by step', examples: 'See results; fill one in', toc: 'See every chapter', chapter: 'Open this chapter',
            follow: 'Keep the steps beside your work', tryIt: 'Fill in this example to generate', walk: 'Step through it on the real screen', goto: 'Fold the book and mark the control',
            again: 'Replay the three steps here', search: 'Find by title, summary and steps', prev: 'Open the previous chapter', next: 'Open the next chapter', example: 'See this example', back: 'Back to all examples',
            read: 'Open the chapter about this', getStarted: 'Fill this example in Autopilot', expand: 'Back to this chapter', followClose: 'Hide; nothing else changes',
            stepDone: 'Mark it done and move on', stepBack: 'Go back one step', drag: 'Drag to another corner', play: 'Play this example'}},
};
const GROUPS = [
    [['Start', '入门'], ['quick-start', 'text-to-video', 'timed-lines', 'quality-tiers', 'refine', 'run-modes']],
    [['Pictures and people', '用图片和人物'], ['first-last-frame', 'middle-frames', 'reference-video', 'cast-sheet', 'scenes-props', 'replace-person', 'replace-two-people', 'lora']],
    [['Pictures', '图像'], ['image-gen', 'image-references', 'edit-image', 'local-image-edit', 'new-image-shape']],
    [['Longer videos', '更长的视频'], ['long-video', 'extend-video', 'continue-or-new-shot']],
    [['Edit and sound', '剪辑与声音'], ['editor', 'save-frame', 'editor-extend-refs', 'segment-prompt', 'separate-tracks', 'replace-music', 'beat-snap', 'voice-recording', 'narration', 'sound-to-picture', 'text-subtitles']],
    [['Autopilot', 'AI 全自动'], ['autopilot', 'drama-outline', 'film-project']],
    [['Library and system', '作品与运行'], ['library-reuse', 'upscale', 'library-export', 'library-organize', 'system-status', 'launcher-close', 'troubleshoot', 'offline-reimport']],
];
// The figure is a finished example, not a screenshot of the interface (screenshots go stale; "Take me there" points at the real control).
const FIG = {
    'quick-start': {k: 'one', p: 'c11-s1'}, 'text-to-video': {k: 'one', p: 'c1-s2'}, 'timed-lines': {k: 'one', p: 'cd-narration', w: 'cd-narration-wave'}, 'quality-tiers': {k: 'quad'}, refine: {k: 'pair', a: 'c7-src-s1', b: 'c7-s1'},
    'first-last-frame': {k: 'pair', a: 'anime-first', b: 'anime-last'}, 'reference-video': {k: 'pair', a: 'fox-sheet', b: 'c5-forest-s1'}, 'cast-sheet': {k: 'one', p: 'fox-sheet'}, 'scenes-props': {k: 'tiles', ps: ['sample-fjord', 'sample-window', 'sample-perfume']},
    'replace-person': {k: 'pair', a: 'c10-source', b: 'c10-s1'}, 'image-gen': {k: 'tiles', ps: ['sample-fox', 'sample-jazz', 'sample-perfume']}, 'image-references': {k: 'pair', a: 'sample-fox', b: 'sample-window'},
    'edit-image': {k: 'pair', a: 'c8-original', b: 'c8-s1'}, 'local-image-edit': {k: 'pair', a: 'c8-original', b: 'c8-s1'},
    'long-video': {k: 'pair', a: 'c5-forest-s1', b: 'c5-snow-s1'}, 'extend-video': {k: 'pair', a: 'c1-s2', b: 'c6-s1'}, 'continue-or-new-shot': {k: 'pair', a: 'c1-s2', b: 'c6-s1'},
    editor: {k: 'pair', a: 'c12-s1', b: 'c13-s1'}, 'save-frame': {k: 'one', p: 'c12-s1'}, 'editor-extend-refs': {k: 'pair', a: 'fox-sheet', b: 'c6-s1'}, 'segment-prompt': {k: 'pair', a: 'c5-forest-s1', b: 'c5-snow-s1'},
    'separate-tracks': {k: 'pair', a: 'cb-source', b: 'cb-dialogue'}, 'replace-music': {k: 'one', p: 'ca-music', w: 'ca-music-wave'}, 'beat-snap': {k: 'one', p: 'ca-music', w: 'ca-music-wave'}, 'voice-recording': {k: 'one', p: 'ca-dub', w: 'ca-dub-wave'},
    narration: {k: 'one', p: 'cd-narration', w: 'cd-narration-wave'}, 'sound-to-picture': {k: 'one', p: 'cc-speech', w: 'cc-speech-wave'}, 'text-subtitles': {k: 'one', p: 'c12-s1'},
    autopilot: {k: 'one', p: 'c9-s1'}, 'film-project': {k: 'one', p: 'c13-s1'}, 'library-reuse': {k: 'one', p: 'c1-s2'},
};
// Which step of a chapter has a control on screen to point at (data-tour-id).
const STEP_GO = {'quick-start': {0: 'prompt-box', 2: 'generate-btn'}, 'text-to-video': {0: 'prompt-box', 3: 'generate-btn'}, 'sound-to-picture': {0: 'video-empty-range'},
    'separate-tracks': {0: 'audio-a1-track', 1: 'audio-track-button'}, 'reference-video': {0: 'prompt-box'}, 'library-reuse': {0: 'works-entry'}, 'cast-sheet': {0: 'cast-add'}};
const HOWTOS = new Set(['reference-video', 'long-video', 'editor-extend-refs', 'separate-tracks', 'replace-music', 'replace-voice']);
const EX_GROUPS = [[['Video', '视频'], ['first-video', 'text-to-video', 'quality-tiers', 'reference-image', 'two-pass', 'first-last-frame', 'product-showcase', 'replace-person', 'character']],
    [['Edit and sound', '剪辑与声音'], ['edit-view', 'extend', 'segment-prompt', 'separate-sound', 'add-sound', 'narration', 'sound-to-picture']], [['Pictures and library', '图片与作品'], ['edit-image', 'library-reuse', 'autopilot']]];
const CARD_CHAPTER = {'first-video': 'quick-start', 'text-to-video': 'text-to-video', 'first-last-frame': 'first-last-frame', 'quality-tiers': 'quality-tiers', 'reference-image': 'reference-video',
    'replace-person': 'replace-person', character: 'cast-sheet', 'edit-view': 'editor', 'separate-sound': 'separate-tracks', extend: 'extend-video', 'two-pass': 'refine', autopilot: 'autopilot',
    'sound-to-picture': 'sound-to-picture', narration: 'narration', 'add-sound': 'replace-music', 'segment-prompt': 'segment-prompt', 'library-reuse': 'library-reuse', 'edit-image': 'edit-image'};
const VIDEOS = new Set(['cb-dialogue', 'cb-music', 'cb-ambience', 'ca-record', 'c4-high', 'c11-s1', 'c1-s2', 'cd-narration', 'c7-s1', 'c7-src-s1', 'c5-forest-s1', 'c5-snow-s1', 'c6-s1', 'c13-s1', 'c12-s1', 'cb-source', 'ca-music', 'ca-dub', 'cc-speech', 'c9-s1', 'c10-s1', 'c8-s1']);

const PATHS = {book: 'M3 3.5h4.5a1.5 1.5 0 0 1 1.5 1.5v8.5a1.5 1.5 0 0 0-1.5-1.5H3zM13 3.5H8.5A1.5 1.5 0 0 0 7 5v8.5A1.5 1.5 0 0 1 8.5 12H13z', x: 'm4 4 8 8M12 4l-8 8',
    list: 'M5.5 4.5h7M5.5 8h7M5.5 11.5h7M3 4.5h.01M3 8h.01M3 11.5h.01', search: 'M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM14 14l-3.5-3.5', check: 'm3.5 8.5 3 3 6-7',
    target: 'M8 13.5A5.5 5.5 0 1 0 8 2.5a5.5 5.5 0 0 0 0 11zM8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4z', steps: 'M3 4.5h1.5M3 8h1.5M3 11.5h1.5M6.5 4.5H13M6.5 8H13M6.5 11.5H13',
    wand: 'm3 13 7-7M9.5 3.5l.5 1.5 1.5.5-1.5.5-.5 1.5-.5-1.5L7.5 5.5 9 5zM12.5 8l.3.8.8.3-.8.3-.3.8-.3-.8-.8-.3.8-.3z', play: 'M5 3.2v9.6l7.6-4.8z', pause: 'M4.5 3h2.5v10H4.5zM9 3h2.5v10H9z',
    left: 'M10 3.5 5.5 8l4.5 4.5', right: 'M6 3.5 10.5 8 6 12.5', arrow: 'M3 8h10M9 4l4 4-4 4', expand: 'M9.5 3H13v3.5M6.5 13H3V9.5M13 3 9 7M3 13l4-4'};
const SVG_NS = 'http://www.w3.org/2000/svg';
function icon(name, cls = '') {
    const svg = document.createElementNS(SVG_NS, 'svg'); svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('class', `fv-book-i${name === 'play' || name === 'pause' ? ' is-solid' : ''}${cls ? ' ' + cls : ''}`);
    const path = document.createElementNS(SVG_NS, 'path'); path.setAttribute('d', PATHS[name]); svg.append(path); return svg;
}
const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
};
const tip = (node, name, use, key = '') => { node.dataset.fvTipName = name; node.dataset.fvTipUse = use; if (key) node.dataset.fvTipKey = key; return node; };
const button = (cls, label, [name, use, key = ''], ...icons) => { const node = el('button', cls); node.type = 'button'; node.append(...icons); if (label != null) node.append(document.createTextNode(label)); return tip(node, name, use, key); };
const language = () => isChinese() ? 'zh' : 'en';
const asset = name => new URL(`./assets/tour/${name}`, import.meta.url).href;
const poster = name => asset(`${name}.webp`);
const READ_KEY = 'freevideo.tutorial.read';
const readSet = () => { try { return new Set(JSON.parse(localStorage.getItem(READ_KEY) || '[]')); } catch { return new Set(); } };
const markRead = id => { try { const set = readSet(); set.add(id); localStorage.setItem(READ_KEY, JSON.stringify([...set])); } catch { /* per-viewer convenience only */ } };
const chapterOf = id => TUTORIAL_CHAPTERS.find(c => c.id === id);
// The groups in reading order, keeping only written chapters; a chapter in no group joins the last one.
export function bookGroups(chapters = TUTORIAL_CHAPTERS) {
    const ids = new Set(chapters.map(c => c.id)), grouped = new Set(GROUPS.flatMap(g => g[1]));
    const groups = GROUPS.map(([title, list]) => [title, list.filter(id => ids.has(id))]);
    groups.at(-1)[1].push(...chapters.map(c => c.id).filter(id => !grouped.has(id)));
    return groups.filter(([, list]) => list.length);
}
export const bookOrder = (chapters = TUTORIAL_CHAPTERS) => bookGroups(chapters).flatMap(([, list]) => list);
// Search: every word must appear in the title, summary, steps or another name for the chapter (either language's own text).
export function searchChapters(query, lang, chapters = TUTORIAL_CHAPTERS, aliases = TUTORIAL_ALIASES) {
    const words = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return null;
    return chapters.filter(c => { const hay = [c.title[lang], c.summary[lang], ...(c.steps[lang] || []), ...(aliases[c.id]?.[lang] || [])].join(' ').toLowerCase(); return words.every(word => hay.includes(word)); }).map(c => c.id);
}

function styles() {
    if (!document.querySelector('link[href*="theme.css"]')) {
        const theme = el('link'); theme.rel = 'stylesheet'; theme.href = new URL('./theme.css', import.meta.url).href; document.head.append(theme);
    }
    for (const name of ['feature_tour', 'tutorial_book']) if (!document.querySelector(`link[data-fv-book-${name}]`)) {
        const css = el('link'); css.rel = 'stylesheet'; css.setAttribute(`data-fv-book-${name}`, ''); css.href = new URL(`./${name}.css`, import.meta.url).href; document.head.append(css);
    }
}

// A figure: the poster first; a play button swaps in the example's video (with sound when it has a waveform).
function figure(spec, lang, text) {
    if (!spec) return null;
    const fig = el('figure', 'fv-book-fig');
    const still = reducedMotion();
    const media = (name, wave, playable = true) => {
        const box = el('div', 'fv-book-media'), img = el('img'); img.alt = ''; img.src = poster(name); img.decoding = 'async'; box.append(img);
        // A finished example plays as a few seconds' silent loop (the poster until it can; a still with reduced motion).
        if (VIDEOS.has(name) && !still) {
            const loop = el('video'); loop.src = asset(`${name}.mp4`); loop.muted = true; loop.loop = true; loop.playsInline = true; loop.autoplay = true; loop.poster = poster(name);
            loop.setAttribute('aria-hidden', 'true'); loop.tabIndex = -1; box.append(loop);
            if (!wave) return box;
        }
        if (wave) { const strip = el('div', 'fv-book-wave'), w = el('img'); w.alt = ''; w.src = poster(wave); strip.append(w); box.append(strip); }
        if (playable && VIDEOS.has(name)) {
            const play = button('fv-book-play', wave ? text.playSound : text.play, [wave ? text.playSound : text.play, text.tips.play, lang === 'zh' ? '空格' : 'Space'], icon('play'));
            let video = null;
            play.onclick = () => {
                if (video && !video.paused && !video.muted) { video.muted = true; play.replaceChildren(icon('play'), document.createTextNode(wave ? text.playSound : text.play)); return; }
                video ||= box.querySelector('video');
                if (!video) { video = el('video'); video.src = asset(`${name}.mp4`); video.loop = true; video.playsInline = true; video.setAttribute('aria-label', name); box.insertBefore(video, img.nextSibling); }
                video.muted = !wave; if (wave) video.currentTime = 0;
                video.play().catch(() => {}); play.replaceChildren(icon('pause'), document.createTextNode(lang === 'zh' ? '暂停' : 'Pause'));
            };
            box.append(play);
        }
        return box;
    };
    if (spec.k === 'one') fig.append(media(spec.p, spec.w));
    else if (spec.k === 'pair') { const pair = el('div', 'fv-book-pair'); const to = el('span', 'fv-book-to'); to.append(icon('arrow')); pair.append(media(spec.a, null, false), to, media(spec.b)); fig.append(pair); }
    else if (spec.k === 'quad') {
        const quad = el('div', 'fv-book-quad'), card = FEATURE_TOUR.find(c => c.id === 'quality-tiers');
        for (const q of card.quality) { const cell = el('figure'); const img = el('img'); img.alt = ''; img.src = asset(q.poster); cell.append(img, el('figcaption', null, q.label[lang])); quad.append(cell); }
        fig.append(quad);
    } else if (spec.k === 'tiles') { const tiles = el('div', 'fv-book-tiles'); for (const p of spec.ps) { const cell = el('figure'); const img = el('img'); img.alt = ''; img.src = poster(p); cell.append(img); tiles.append(cell); } fig.append(tiles); }
    return fig;
}

// A step's words with each control it names in quotes (“生成视频”, "Generate video") drawn as a small sample of that
// control, in the product's own control style, so the reader recognises it on screen.
export function stepParts(step) {
    const parts = [], re = /[“"]([^”"]{1,24})[”"]/g;
    let at = 0, m;
    while ((m = re.exec(step))) { if (m.index > at) parts.push({text: step.slice(at, m.index)}); parts.push({control: m[1]}); at = m.index + m[0].length; }
    if (at < step.length) parts.push({text: step.slice(at)});
    return parts;
}
function stepLine(step) {
    const line = el('span', 'fv-book-step');
    for (const part of stepParts(step)) line.append(part.control ? el('span', 'fv-book-ctl', part.control) : document.createTextNode(part.text));
    return line;
}

let current = null, follow = null;

/** Open the tutorial. chapter: an id in tutorial_book_content.js; tab: 'chapters' | 'examples'; example: a feature card id. */
export function openTutorialBook({chapter = TUTORIAL_CHAPTERS[0].id, tab = 'chapters', example = null, note = ''} = {}) {
    if (chapter === 'separate-tracks') import('./sound_bubble.js').then(({markSoundBubbleSeen}) => markSoundBubbleSeen(), () => {});
    if (current?.dialog.isConnected) { current.go({tab, chapter, example}); return current.dialog; }
    styles();
    const lang = language(), text = COPY[lang], L = pair => Array.isArray(pair) ? pair[lang === 'zh' ? 1 : 0] : pair?.[lang] ?? '';
    const restore = document.activeElement;
    const state = {tab, chapter: chapterOf(chapter) ? chapter : TUTORIAL_CHAPTERS[0].id, example, query: '', tocOnly: false, note};
    const dialog = el('dialog', 'fv-book'); dialog.lang = lang === 'zh' ? 'zh-CN' : 'en';
    dialog.setAttribute('aria-labelledby', 'fv-book-title'); dialog.dataset.fvTipScope = '';
    // Head: name, the two pages (Chapters, Examples), contents (phones) and close.
    const head = el('header', 'fv-book-h'), name = el('h2'); name.id = 'fv-book-title'; name.append(icon('book'), el('span', 'fv-book-name', text.book));
    const tabs = el('div', 'fv-book-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', text.book);
    const tabButton = (id, label, use) => { const node = button('', label, [label, use]); node.setAttribute('role', 'tab'); node.dataset.tab = id; node.onclick = () => go({tab: id, example: null}); return node; };
    const chaptersTab = tabButton('chapters', text.chapters, text.tips.chapters), examplesTab = tabButton('examples', text.examples, text.tips.examples);
    tabs.append(chaptersTab, examplesTab);
    const end = el('div', 'fv-book-end');
    const tocButton = button('fv-book-icon fv-book-tocbtn', null, [text.contents, text.tips.toc], icon('list')); tocButton.setAttribute('aria-label', text.contents);
    tocButton.onclick = () => go({tocOnly: !state.tocOnly});
    const close = button('fv-book-icon', null, [text.close, text.tips.close, 'Esc'], icon('x')); close.setAttribute('aria-label', text.close); close.onclick = () => dialog.close();
    end.append(tocButton, close); head.append(name, tabs, end);
    // Body: contents on the left (search, groups, the guide's replay), the page on the right.
    const body = el('div', 'fv-book-b'), nav = el('nav', 'fv-book-nav'); nav.setAttribute('aria-label', text.contents);
    const find = el('label', 'fv-book-find'), input = el('input'), count = el('span', 'fv-book-count');
    input.type = 'search'; input.placeholder = text.search; input.setAttribute('aria-label', text.search); tip(input, text.search, text.tips.search);
    find.append(icon('search'), input, count);
    const toc = el('div', 'fv-book-toc');
    const start = el('div', 'fv-book-start'), startText = el('span', 'fv-book-start-t'); startText.append(el('b', null, text.start), document.createTextNode(text.startLine));
    const again = button('fv-book-btn', text.again, [text.again, text.tips.again]); again.dataset.kind = 'fill';
    again.onclick = async () => { dialog.close(); const {showGuide} = await import('./first_run_guide.js'); if (!await showGuide({all: true})) openTutorialBook({chapter: 'quick-start'}); };
    start.append(startText, again); nav.append(find, toc, start);
    const page = el('div', 'fv-book-page'); page.tabIndex = -1;
    body.append(nav, page); dialog.append(head, body);
    function drawToc() {
        const hits = searchChapters(state.query, lang), read = readSet();
        count.textContent = hits ? String(hits.length) : '';
        toc.replaceChildren();
        if (hits && !hits.length) { const none = el('div', 'fv-book-none'); none.append(el('b', null, text.none), document.createTextNode(text.noneHint)); toc.append(none); return; }
        for (const [title, ids] of bookGroups()) {
            const list = ids.filter(id => !hits || hits.includes(id));
            if (!list.length) continue;
            const group = el('div', 'fv-book-g'); group.append(el('span', null, L(title)), el('span', 'fv-book-n', `${list.filter(id => read.has(id)).length} / ${list.length}`));
            toc.append(group);
            for (const id of list) {
                const c = chapterOf(id), item = button('fv-book-ti', null, [c.title[lang], c.summary[lang] || text.tips.chapter]);
                item.dataset.chapter = id;
                const t = el('span', 'fv-book-t');
                const words = String(state.query || '').trim();
                if (words) { const at = c.title[lang].toLowerCase().indexOf(words.toLowerCase()); if (at >= 0) { t.append(c.title[lang].slice(0, at), el('mark', null, c.title[lang].slice(at, at + words.length)), c.title[lang].slice(at + words.length)); } else t.textContent = c.title[lang]; }
                else t.textContent = c.title[lang];
                item.append(t, read.has(id) ? icon('check', 'fv-book-ok') : el('span'));
                if (state.tab === 'chapters' && id === state.chapter) item.setAttribute('aria-current', 'page');
                item.onclick = () => go({tab: 'chapters', chapter: id, tocOnly: false, example: null});
                toc.append(item);
            }
        }
    }
    function drawChapter() {
        const order = bookOrder(), id = state.chapter, c = chapterOf(id), at = order.indexOf(id), read = readSet();
        const group = bookGroups().find(([, ids]) => ids.includes(id));
        const inner = el('article', 'fv-book-in');
        const eyebrow = el('div', 'fv-book-eyebrow'); eyebrow.append(el('span', null, group ? L(group[0]) : ''), el('span', null, '·'), el('span', 'fv-book-num', `${at + 1} / ${order.length}`));
        if (read.has(id)) { const ok = el('span', 'fv-book-read'); ok.append(icon('check'), document.createTextNode(text.read)); eyebrow.append(ok); }
        const h1 = el('h1', null, c.title[lang]);
        inner.append(eyebrow, h1, el('p', 'fv-book-sum', c.summary[lang]));
        const fig = figure(FIG[id], lang, text); if (fig) inner.append(fig);
        const acts = el('div', 'fv-book-acts');
        const followButton = button('fv-book-btn', text.follow, [text.follow, text.tips.follow], icon('steps')); followButton.dataset.kind = 'primary';
        followButton.onclick = () => { dialog.close(); openFollow(id); };
        acts.append(followButton);
        if (c.example) {
            const tryButton = button('fv-book-btn', text.tryIt, [text.tryIt, text.tips.tryIt], icon('wand')); tryButton.dataset.kind = 'fill';
            tryButton.hidden = typeof window.freevideoHowTo?.tryIt !== 'function';
            tryButton.onclick = () => { dialog.close(); window.freevideoHowTo?.tryIt?.(c.example, {chapter: id}); };
            acts.append(tryButton);
        }
        if (HOWTOS.has(id) && typeof window.freevideoHowTo?.start === 'function') {
            const walk = button('fv-book-btn', text.walk, [text.walk, text.tips.walk], icon('play')); walk.dataset.kind = 'fill';
            Promise.resolve(window.freevideoHowTo.available?.(id)).then(ok => { if (ok === false) walk.hidden = true; }).catch(() => {});
            walk.onclick = () => { dialog.close(); window.freevideoHowTo?.start?.(id); };
            acts.append(walk);
        }
        inner.append(acts);
        const note = el('div', 'fv-book-note'); note.setAttribute('role', 'status'); note.hidden = !state.note; note.textContent = state.note; inner.append(note);
        const steps = el('section', 'fv-book-sec'); steps.id = 'fv-book-steps'; steps.append(el('h2', null, text.steps));
        const ol = el('ol', 'fv-book-steps');
        const marks = STEP_GO[id] || (c.goto?.length ? {0: c.goto[0]} : {});
        (c.steps[lang] || []).forEach((step, i) => {
            const li = el('li'); li.append(stepLine(step));
            if (marks[i]) {
                const go = button('fv-book-go', text.goto, [text.goto, text.tips.goto], icon('target'));
                go.onclick = () => takeMeThere(c, marks[i], go);
                li.append(go);
            } else li.append(el('span'));
            ol.append(li);
        });
        steps.append(ol); inner.append(steps);
        if (c.limits?.[lang]?.length) {
            const lims = el('section', 'fv-book-sec'); lims.append(el('h2', null, text.limits));
            const ul = el('ul', 'fv-book-lims'); for (const line of c.limits[lang]) ul.append(el('li', null, line));
            lims.append(ul); inner.append(lims);
        }
        const pn = el('nav', 'fv-book-pn'); pn.setAttribute('aria-label', lang === 'zh' ? '翻页' : 'Pages');
        const prevId = order[at - 1], nextId = order[at + 1];
        const side = (target, label, cls, arrowFirst) => {
            const node = button(`fv-book-pnb ${cls}${target ? '' : ' is-none'}`, null, [label, target ? chapterOf(target).title[lang] : '']);
            const small = el('small'); if (arrowFirst) small.append(icon('left'), document.createTextNode(label)); else small.append(document.createTextNode(label), icon('right'));
            node.append(small, el('b', null, target ? chapterOf(target).title[lang] : ''));
            if (target) node.onclick = () => go({chapter: target}); else node.tabIndex = -1;
            return node;
        };
        pn.append(side(prevId, text.prev, 'is-prev', true), side(nextId, text.next, 'is-next', false));
        inner.append(pn);
        page.replaceChildren(inner);
        markRead(id);
    }
    // Whether another view now lies over the book.
    const covered = () => { const r = dialog.getBoundingClientRect(), hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!hit && !dialog.contains(hit); };
    async function takeMeThere(c, id, go) {
        const {goToScene} = await import('./tour_goto.js');
        go.disabled = true; state.note = '';
        const result = await goToScene({view: c.view, ids: [id], select: c.select, beforeRing: () => dialog.close()});
        go.disabled = false;
        if (!result.ok && dialog.isConnected && covered()) {
            // The view opened over the book: bring the book back on top so its note is seen.
            const note = text[{view: 'noView', hidden: 'noHidden'}[result.reason] || 'noTarget'], chapter = state.chapter;
            dialog.addEventListener('close', () => openTutorialBook({chapter, note}), {once: true});
            dialog.close(); return;
        }
        if (!result.ok && dialog.isConnected) { state.note = text[{view: 'noView', hidden: 'noHidden'}[result.reason] || 'noTarget']; const note = page.querySelector('.fv-book-note'); if (note) { note.textContent = state.note; note.hidden = false; note.scrollIntoView({block: 'nearest'}); } }
    }
    // A view that has just opened may not have turned its box on yet (the chat does once it has read the conversation);
    // the caret goes there as soon as it is on, unless the user has put focus somewhere else meanwhile.
    function focusWhenOn(box) {
        if (!box.disabled) { box.focus(); return; }
        const watch = new MutationObserver(() => {
            if (box.disabled) return;
            watch.disconnect(); clearTimeout(stop);
            const at = document.activeElement;
            if (!at || at === document.body || at.closest('.fv-view-switch')) box.focus();
        });
        const stop = setTimeout(() => watch.disconnect(), 5000);
        watch.observe(box, {attributes: true, attributeFilter: ['disabled']});
    }
    function drawExamples() {
        const box = el('div', 'fv-book-ex');
        const hero = el('div', 'fv-book-hero'), art = el('div', 'fv-book-art'), img = el('img'); img.alt = ''; img.src = SHOWCASE_ASSET; art.append(img);
        const words = el('div'); words.append(el('span', 'fv-book-badge', text.featured), el('h3', null, L(SHOWCASE_TEXT.title)), el('p', null, text.showcaseLine));
        const begin = button('fv-book-btn', text.getStarted, [text.getStarted, text.tips.getStarted]); begin.dataset.kind = 'primary';
        begin.onclick = async () => {
            dialog.close();
            await globalThis.FreeVideoShell?.go?.('autopilot');
            const box = document.querySelector('dialog.fv-autopilot[open] textarea');
            if (box && !box.value.trim()) { box.value = L(SHOWCASE_TEXT.example); box.dispatchEvent(new Event('input', {bubbles: true})); focusWhenOn(box); }
        };
        words.append(begin); hero.append(art, words); box.append(hero);
        const grid = el('div', 'fv-book-grid'), ready = new Set(readyCards().map(c => c.id));
        for (const [title, ids] of EX_GROUPS) {
            const list = ids.map(id => FEATURE_TOUR.find(c => c.id === id)).filter(c => c && ready.has(c.id));
            if (!list.length) continue;
            grid.append(el('h4', null, L(title)));
            for (const card of list) {
                const tile = button('fv-book-tile', null, [card.title[lang], card.line[lang]]);
                const pic = el('span', 'fv-book-pic'), image = el('img'); image.alt = ''; image.src = asset(card.clip.poster); image.loading = 'lazy';
                const play = el('span', 'fv-book-tplay'); play.append(icon('play')); pic.append(image, play);
                tile.append(pic, el('span', 'fv-book-tt', card.title[lang]), el('span', 'fv-book-ts', card.line[lang]));
                tile.onclick = () => go({example: card.id});
                grid.append(tile);
            }
        }
        box.append(grid);
        page.replaceChildren(box);
    }
    function drawExample() {
        const card = FEATURE_TOUR.find(c => c.id === state.example);
        if (!card) { state.example = null; drawExamples(); return; }
        const box = el('div', 'fv-book-card');
        const back = button('fv-book-back', text.back, [text.back, text.tips.back], icon('left')); back.onclick = () => go({example: null});
        const eyebrow = el('div', 'fv-book-eyebrow'); eyebrow.append(el('span', null, text.feature));
        const h1 = el('h1', null, card.title[lang]);
        const pairBig = el('div', 'fv-book-pairbig'), inputs = el('div', 'fv-book-inputs');
        for (const item of card.input) {
            if (item.text) { const quote = el('div', 'fv-book-in-card'); quote.append(el('small', null, text.prompt), document.createTextNode(item.text[lang])); inputs.append(quote); }
            else if (item.src) { const m = el('div', 'fv-book-media'), img = el('img'); img.alt = ''; img.src = asset(item.src); m.append(img); inputs.append(m); }
            else if (item.mp4) { const m = el('div', 'fv-book-media'), img = el('img'); img.alt = ''; img.src = asset(item.poster); m.append(img); inputs.append(m); }
        }
        const to = el('span', 'fv-book-to'); to.append(icon('arrow'));
        const out = el('div', 'fv-book-media'), video = el('video'); video.src = asset(card.clip.mp4); video.poster = asset(card.clip.poster); video.muted = true; video.loop = true; video.playsInline = true;
        video.setAttribute('aria-label', card.title[lang]);
        if (!reducedMotion()) video.autoplay = true;
        out.append(video);
        pairBig.append(inputs, to, out);
        const strip = el('div', 'fv-book-strip'), acts = el('div', 'fv-book-acts');
        const usable = card.tryit && card.tryit.status !== 'pending-assets' && typeof window.freevideoHowTo?.tryIt === 'function';
        if (usable) { const t = button('fv-book-btn', text.tryIt, [text.tryIt, text.tips.tryIt], icon('wand')); t.dataset.kind = 'primary'; t.onclick = () => { dialog.close(); window.freevideoHowTo?.tryIt?.(card.id); }; acts.append(t); }
        if (card.goto?.length || card.target) {
            const g = button('fv-book-btn', text.goto, [text.goto, text.tips.goto], icon('target')); g.dataset.kind = usable ? 'fill' : 'primary';
            g.onclick = async () => { dialog.close(); const {showFeatureCard} = await import('./feature_tour.js'); await showFeatureCard(card.id); };
            acts.append(g);
        }
        const chapter = CARD_CHAPTER[card.id];
        if (chapter && chapterOf(chapter)) { const r = button('fv-book-btn', lang === 'zh' ? '看教程' : 'Read the tutorial', [lang === 'zh' ? '看教程' : 'Read the tutorial', text.tips.read]); r.onclick = () => go({tab: 'chapters', chapter, example: null}); acts.append(r); }
        strip.append(acts, el('span', 'fv-book-hint', text.fills));
        box.append(back, eyebrow, h1, el('p', 'fv-book-sum', card.line[lang]), pairBig, strip);
        page.replaceChildren(box);
    }
    function go(next = {}) {
        const changedPage = (next.chapter && next.chapter !== state.chapter) || (next.tab && next.tab !== state.tab) || ('example' in next && next.example !== state.example);
        Object.assign(state, next);
        if (next.chapter && !chapterOf(next.chapter)) state.chapter = TUTORIAL_CHAPTERS[0].id;
        if (changedPage) state.note = '';
        dialog.dataset.tab = state.tab; dialog.toggleAttribute('data-toc', !!state.tocOnly);
        chaptersTab.setAttribute('aria-selected', String(state.tab === 'chapters')); examplesTab.setAttribute('aria-selected', String(state.tab === 'examples'));
        tocButton.setAttribute('aria-pressed', String(!!state.tocOnly));
        page.querySelectorAll('video').forEach(video => video.pause());
        drawToc();
        if (state.tab === 'examples') state.example ? drawExample() : drawExamples(); else drawChapter();
        if (changedPage) { page.scrollTop = 0; }
    }
    input.addEventListener('input', () => { state.query = input.value; drawToc(); });
    // Up and Down move through the contents; the views' own shortcuts never run while reading.
    const disposeKeys = isolateDialogKeys(dialog, event => routeDialogKey(event, dialog));
    const removeKeys = listenDialogKey(dialog, 'keydown', event => {
        if (!['ArrowDown', 'ArrowUp'].includes(event.key) || !event.target.closest?.('.fv-book-toc')) return;
        const items = [...toc.querySelectorAll('.fv-book-ti')], at = items.indexOf(event.target.closest('.fv-book-ti'));
        if (at < 0) return; event.preventDefault(); items[Math.max(0, Math.min(items.length - 1, at + (event.key === 'ArrowDown' ? 1 : -1)))].focus();
    });
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => {
        dialog.querySelectorAll('video').forEach(video => video.pause());
        disposeKeys(); removeKeys(); dialog.remove(); current = null;
        if (restore?.isConnected && !document.querySelector('.fv-tour-layer')) restore.focus({preventScroll: true});
    }, {once: true});
    document.body.append(dialog);
    current = {dialog, go};
    go({});
    dialog.showModal();
    if (note) page.querySelector('.fv-book-note')?.scrollIntoView({block: 'nearest'});
    // On phones the contents are folded behind the list button (tutorial_book.css, 719px), so focus starts on that
    // button: a hidden entry cannot hold focus, and it would fall to the page behind the book.
    const folded = state.tab === 'chapters' && !state.tocOnly && matchMedia('(max-width: 719px)').matches;
    (folded ? tocButton : state.tab === 'chapters' ? toc.querySelector('[aria-current=page]') : examplesTab)?.focus({preventScroll: true});
    toc.querySelector('[aria-current=page]')?.scrollIntoView({block: 'center'});
    return dialog;
}

// ── Follow along: the chapter's steps in a small card beside the work ──────────────────────────────────────────────
export function openFollow(id, at = 0) {
    follow?.close();
    styles();
    const lang = language(), text = COPY[lang], c = chapterOf(id);
    if (!c) return null;
    const steps = c.steps[lang] || [], marks = STEP_GO[id] || (c.goto?.length ? {0: c.goto[0]} : {});
    const card = el('aside', 'fv-follow fv-glass fv-g2 sgf-avoid'); card.setAttribute('role', 'region'); card.setAttribute('aria-label', `${text.follow} · ${c.title[lang]}`);
    card.dataset.fvTipScope = ''; card.lang = lang === 'zh' ? 'zh-CN' : 'en';
    if ('popover' in HTMLElement.prototype) card.popover = 'manual';
    let step = at, corner = 'tr', closed = false;
    let wasPhone = null;
    const place = () => {
        const phone = innerWidth < 720; card.toggleAttribute('data-phone', phone);
        card.dataset.corner = phone ? 'bottom' : corner;
        // On a phone the capsule sits just above a bar that runs along the bottom of the view (the console, the
        // generate bar), so it never covers its controls.
        card.style.bottom = '';
        if (phone) {
            const scope = card.closest('dialog') || document;
            const bars = [...scope.querySelectorAll('.sgf-avoid')].filter(node => node !== card && !card.contains(node)).map(node => node.getBoundingClientRect())
                .filter(r => r.width > innerWidth / 2 && r.bottom > innerHeight - 32 && r.top > innerHeight / 2);
            if (bars.length) card.style.bottom = `${Math.round(innerHeight - Math.min(...bars.map(r => r.top)) + 8)}px`;
        }
        if (wasPhone !== null && wasPhone !== phone) draw();
        wasPhone = phone;
    };
    // Phones: one capsule at the bottom (progress ring, chapter and step, Take me there, done, the book, close).
    function drawCapsule() {
        const done = step >= steps.length, p = Math.min(1, step / Math.max(1, steps.length)), R = 9, C = 2 * Math.PI * R;
        const ring = document.createElementNS(SVG_NS, 'svg'); ring.setAttribute('viewBox', '0 0 24 24'); ring.setAttribute('class', 'fv-follow-ring'); ring.setAttribute('aria-hidden', 'true');
        ring.innerHTML = `<circle cx="12" cy="12" r="${R}" class="bg"/><circle cx="12" cy="12" r="${R}" class="fg" stroke-dasharray="${C.toFixed(2)}" stroke-dashoffset="${(C * (1 - p)).toFixed(2)}"/>`;
        const t = el('span', 'fv-follow-t'); t.append(el('b', null, c.title[lang]), el('small', null, done ? text.done : text.stepOf(step + 1, steps.length)));
        card.append(ring, t);
        if (!done && marks[step]) { const g = button('fv-follow-icon', null, [text.goto, text.tips.goto], icon('target')); g.setAttribute('aria-label', text.goto); g.onclick = async () => { const {goToScene} = await import('./tour_goto.js'); await goToScene({view: c.view, ids: [marks[step]], select: c.select}); }; card.append(g); }
        if (!done) { const ok = button('fv-follow-icon', null, [text.stepDone, text.tips.stepDone], icon('check')); ok.setAttribute('aria-label', text.stepDone); ok.onclick = () => { step++; draw(); }; card.append(ok); }
        const expand = button('fv-follow-icon', null, [text.expand, text.tips.expand], icon('expand')); expand.setAttribute('aria-label', text.expand); expand.onclick = () => { close(); openTutorialBook({chapter: id}); };
        const x = button('fv-follow-icon', null, [text.close, text.tips.followClose, 'Esc'], icon('x')); x.setAttribute('aria-label', text.close); x.onclick = close;
        card.append(expand, x);
    }
    function draw() {
        const done = step >= steps.length; card.classList.toggle('is-done', done);
        card.replaceChildren();
        if (innerWidth < 720) { drawCapsule(); return; }
        const head = el('div', 'fv-follow-h'); tip(head, lang === 'zh' ? '拖动' : 'Drag', text.tips.drag);
        const ic = el('span', 'fv-follow-ic'); ic.append(icon(done ? 'check' : 'steps'));
        const t = el('span', 'fv-follow-t'); t.append(el('b', null, c.title[lang]), el('small', null, done ? text.done : text.stepOf(step + 1, steps.length)));
        const expand = button('fv-follow-icon', null, [text.expand, text.tips.expand], icon('expand')); expand.setAttribute('aria-label', text.expand);
        expand.onclick = () => { close(); openTutorialBook({chapter: id}); };
        const x = button('fv-follow-icon', null, [text.close, text.tips.followClose, 'Esc'], icon('x')); x.setAttribute('aria-label', text.close); x.onclick = close;
        head.append(ic, t, expand, x);
        const bar = el('div', 'fv-follow-bar'), fill = el('i'); fill.style.width = `${Math.round(100 * Math.min(step, steps.length) / Math.max(1, steps.length))}%`; bar.append(fill);
        const ol = el('ol');
        steps.forEach((line, i) => {
            const li = el('li', i < step ? 'is-done' : i === step ? 'is-cur' : '');
            const n = el('span', 'fv-follow-n'); if (i < step) n.append(icon('check')); else n.textContent = String(i + 1);
            li.append(n, stepLine(line));
            if (i === step && marks[i]) { const g = button('fv-follow-go', text.goto, [text.goto, text.tips.goto]); g.onclick = async () => { const {goToScene} = await import('./tour_goto.js'); await goToScene({view: c.view, ids: [marks[i]], select: c.select}); }; li.append(g); }
            else li.append(el('span'));
            if (i === step) li.setAttribute('aria-current', 'step');
            ol.append(li);
        });
        const foot = el('div', 'fv-follow-f');
        if (done) {
            const order = bookOrder(), nextId = order[order.indexOf(id) + 1];
            foot.append(el('span', 'fv-follow-next', nextId ? text.doneNext + chapterOf(nextId).title[lang] : text.done), el('span', 'fv-follow-grow'));
            if (nextId) { const n = button('fv-book-btn', text.next, [text.next, text.tips.next]); n.dataset.kind = 'primary'; n.onclick = () => { close(); openTutorialBook({chapter: nextId}); }; foot.append(n); }
        } else {
            const back = button('fv-book-btn', text.stepBack, [text.stepBack, text.tips.stepBack]); back.disabled = step === 0; back.onclick = () => { step = Math.max(0, step - 1); draw(); };
            const ok = button('fv-book-btn', text.stepDone, [text.stepDone, text.tips.stepDone]); ok.dataset.kind = 'fill'; ok.onclick = () => { step++; draw(); };
            foot.append(back, el('span', 'fv-follow-grow'), ok);
        }
        card.append(head, bar, ol, foot);
        drag(head);
    }
    // Drag the head; on release the card settles in the nearest corner (no bounce).
    function drag(head) {
        head.onpointerdown = event => {
            if (event.button !== 0 || event.target.closest('button') || card.hasAttribute('data-phone')) return;
            const r = card.getBoundingClientRect(), dx = event.clientX - r.left, dy = event.clientY - r.top;
            head.setPointerCapture(event.pointerId); card.dataset.dragging = '';
            const move = e => { card.style.left = `${e.clientX - dx}px`; card.style.top = `${e.clientY - dy}px`; card.style.right = card.style.bottom = 'auto'; };
            const up = e => {
                head.removeEventListener('pointermove', move); head.removeEventListener('pointerup', up); head.removeEventListener('pointercancel', up);
                delete card.dataset.dragging; card.style.left = card.style.top = card.style.right = card.style.bottom = '';
                corner = `${e.clientY < innerHeight / 2 ? 't' : 'b'}${e.clientX < innerWidth / 2 ? 'l' : 'r'}`; place();
            };
            head.addEventListener('pointermove', move); head.addEventListener('pointerup', up); head.addEventListener('pointercancel', up);
        };
    }
    // The card floats over whichever view is in front: it moves into that view when the view changes.
    const host = () => globalThis.FreeVideoShell?.overlayHost?.() || [...document.querySelectorAll('dialog[open]')].at(-1) || document.body;
    const rehost = () => { if (closed) return; const to = host(); if (card.parentElement !== to) { try { card.hidePopover?.(); } catch { /* not shown */ } to.append(card); place(); try { card.showPopover?.(); } catch { /* shown in place */ } } };
    const onView = () => requestAnimationFrame(rehost);
    const key = event => { if (event.key === 'Escape' && card.contains(document.activeElement)) { event.preventDefault(); close(); } };
    function close() {
        if (closed) return; closed = true;
        removeEventListener('freevideo-view', onView); removeEventListener('resize', place); card.removeEventListener('keydown', key);
        try { card.hidePopover?.(); } catch { /* not shown */ } card.remove(); if (follow?.card === card) follow = null;
    }
    addEventListener('freevideo-view', onView); addEventListener('resize', place); card.addEventListener('keydown', key);
    draw(); host().append(card); place(); try { card.showPopover?.(); } catch { /* shown in place */ }
    const first = marks[0];
    if (first) import('./tour_goto.js').then(({goToScene}) => goToScene({view: c.view, ids: [first], select: c.select})).catch(() => {});
    follow = {card, close};
    return follow;
}

/** Append an entry to a settings row, or return it for the caller to place.
 * Example: mountBookEntry({container: row, chapter: 'reference-video'}).
 * Callers that own another modal may close it in beforeOpen.
 */
export function mountBookEntry({container, chapter, beforeOpen} = {}) {
    const lang = language();
    const entry = el('button', 'fv-book-entry', COPY[lang].book); entry.type = 'button';
    tip(entry, COPY[lang].book, COPY[lang].tips.read);
    entry.onclick = () => { beforeOpen?.(); openTutorialBook({chapter}); };
    container?.append(entry);
    return entry;
}
