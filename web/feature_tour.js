import {FEATURE_TOUR, readyCards, tourKey, unseenCards} from './feature_tour_content.js';
import {readGuideRecord, saveGuideRecord, guideRecord, CARDS_OFF} from './first_run_guide.js';
import './glass/library.js';
import {openAnchor, anchorOpen, closeAnchor} from './feature_tour_layer.js';
import {reducedMotion} from './context_menu.js';
import { isChinese } from './i18n.js';

export const TOUR_TEXT = {
    en: {examples: 'Feature examples', use: 'Use example', next: 'Next', goto: 'Take me there', noView: 'Could not open the Edit view. Choose "Edit" at the top.', noTarget: 'This item is not available here. Follow the steps above.', noHidden: 'The timeline has no clip with sound yet. Import or generate one first.', book: 'Read the tutorial', done: 'Done', close: 'Close', gotIt: 'Got it',
        closeUse: 'Put this card away', nextUse: 'Show the next feature', useUse: 'Fill the creation panel with this example', gotoUse: 'Point at the control on screen', bookUse: 'Open the tutorial chapter', segmentUse: 'Play this version with sound', dotUse: 'Show this feature'},
    zh: {examples: '功能示例', use: '填入示例', next: '下一张', goto: '带我去', noView: '无法打开剪辑视图，请点顶部的“剪辑视图”。', noTarget: '当前界面中找不到这一项，请按上面的步骤操作。', noHidden: '时间线上还没有带声音的片段，请先导入或生成一段。', book: '看教程', done: '完成', close: '关闭', gotIt: '知道了',
        closeUse: '收起这张卡片', nextUse: '看下一项功能', useUse: '把示例填进创作面板', gotoUse: '在界面上指出这个控件', bookUse: '打开教程里的这一章', segmentUse: '带声音播放这一版', dotUse: '看这一项功能'},
};
// The chapter of the tutorial book that teaches the same thing as the card.
const BOOK_CHAPTER = {'first-video': 'quick-start', 'text-to-video': 'text-to-video', 'first-last-frame': 'first-last-frame', 'quality-tiers': 'quality-tiers',
    'reference-image': 'reference-video', 'replace-person': 'replace-person', character: 'cast-sheet', 'edit-view': 'editor', 'separate-sound': 'separate-tracks', extend: 'extend-video', 'two-pass': 'refine', autopilot: 'autopilot'};
const asset = name => new URL(`./assets/tour/${name}`, import.meta.url).href;
const el = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text != null) node.textContent = text; return node; };
// Hover tips (tooltip.js): name, what it does, key.
const tip = (node, name, use = '', key = '') => { node.dataset.fvTipName = name; if (use) node.dataset.fvTipUse = use; if (key) node.dataset.fvTipKey = key; return node; };
const contexts = new Map();
let active = null, opening = false;
export function registerTourWorkspace(context) {
    contexts.set(context.dialog, context);
    return () => { contexts.delete(context.dialog); if (active?.owner === context.dialog) active.close(); if (anchorOpen()?.owner === context.dialog) closeAnchor('now'); };
}
const contextFor = () => [...contexts.values()].filter(c => c.dialog.isConnected).at(-1);
export async function tryIt(example, options = {}) {
    try {
        const {TOUR_EXAMPLES} = await import('./tour_try.js');
        // A tutorial chapter passes its example itself; a start clip goes onto the editor timeline.
        const given = example && typeof example === 'object' ? {assets: example.assets || [], prompt: example.prompt, settings: example.settings || {}, startClip: example.startClip} : null;
        const card = given ? null : FEATURE_TOUR.find(c => c.id === example);
        const choice = given || (card ? card.tryit : TOUR_EXAMPLES[example]);
        const entry = choice?.example && choice.status !== 'pending-assets' ? TOUR_EXAMPLES[choice.example] : choice;
        if (!entry) return {ok: false, reason: 'unknown_example'};
        if (entry.status === 'pending-assets') return {ok: false, reason: 'pending-assets'};
        let context = contextFor();
        if (!context || !context.dialog.open || document.querySelector('dialog.fv-editor[open]')) {
            const {openCreativeWorkspace} = await import('./view_navigation.js');
            if (!await openCreativeWorkspace({studioOnly: true})) return {ok: false, reason: 'no_workspace'};
            context = contextFor();
        }
        if (!context) return {ok: false, reason: 'no_workspace'};
        if (entry.startClip) { if (active) await active.close(); return await context.onStartClip(entry); }
        if (context.canTry?.(entry) === false) return context.onTry(entry, options);
        if (active) await active.close();
        return await context.onTry(entry, options);
    } catch (error) { contextFor()?.onError?.(error); return {ok: false, reason: 'apply', error}; }
}

// Decode every preview before offering a card. Broken or absent local media never produces an empty card.
export async function loadTourCard(card, {timeout = 5000} = {}) {
    const previews = [...(card.quality || card.sequence || [card.clip]), ...card.input.filter(item => item.mp4)];
    const videos = [];
    try {
        await Promise.all(previews.map(preview => new Promise((resolve, reject) => {
            const video = el('video', 'fv-tour-video'); videos.push(video);
            video.muted = true; video.playsInline = true; video.preload = 'auto'; video.poster = asset(preview.poster);
            const timer = setTimeout(() => reject(new Error('timeout')), timeout);
            const done = ok => { clearTimeout(timer); video.onloadeddata = video.onerror = null; ok ? resolve() : reject(new Error('media')); };
            video.onloadeddata = () => done(true); video.onerror = () => done(false);
            video.src = asset(preview.mp4); video.load();
        })));
        return {card, videos};
    } catch {
        for (const video of videos) { video.pause(); video.removeAttribute('src'); video.load(); }
        return null;
    }
}
const release = loaded => { for (const item of loaded) for (const video of item.videos) { video.pause(); video.removeAttribute('src'); video.load(); } };
export async function markTourSeen(api, cards) {
    const loaded = await readGuideRecord(api);
    if (loaded.ok) return saveGuideRecord(api, guideRecord(loaded.record, cards.map(tourKey)));
    return false;
}
function styles() {
    for (const name of ['theme', 'feature_tour']) if (!document.querySelector(`link[data-fv-tour-${name}]`)) {
        const css = el('link'); css.rel = 'stylesheet'; css.setAttribute(`data-fv-tour-${name}`, ''); css.href = new URL(`./${name}.css`, import.meta.url).href; document.head.append(css);
    }
}
// The guide, its next-step hint and a how-to that is still opening its view all keep automatic tour cards closed.
const GUIDE_ON_SCREEN = '.fv-guide-layer,.fv-first-start,.fv-next-step,body[data-fv-how-to]';
export async function openFeatureTour({api = contextFor()?.api, cn = contextFor()?.cn, cards = readyCards(), start, grid = false, trigger = null, version, owner = contextFor()?.dialog, isBusy = contextFor()?.isBusy} = {}) {
    // The examples someone asked to see live on the tutorial's Examples page.
    if (!trigger && !version) { const {openTutorialBook} = await import('./tutorial_book.js'); openTutorialBook({tab: 'examples', example: start || null}); return true; }
    if (!api || opening || active) return false;
    opening = true;
    const restore = document.activeElement;
    let loaded = [];
    try {
        const record = await readGuideRecord(api);
        if (!record.ok) return false;
        // Settings › Getting started can turn off the cards that come by themselves; the tutorial still shows them all.
        if ((trigger || version) && record.record?.seen.includes(CARDS_OFF)) return false;
        if (trigger || version) cards = unseenCards(record.record, {trigger, version, cards});
        // Contextual help always offers one card per use.
        if (trigger) cards = cards.slice(0, 1);
        if (trigger && cards.length && !cardTarget(cards[0], owner?.isConnected ? owner : document)) return false;
        if (grid) cards = [...cards].sort((a, b) => Number(record.record?.seen.includes(tourKey(a))) - Number(record.record?.seen.includes(tourKey(b))));
        loaded = (await Promise.all(cards.map(c => loadTourCard(c)))).filter(Boolean);
        if (!loaded.length || (owner && (!owner.open || !owner.isConnected || [...document.querySelectorAll('dialog[open]')].filter(d => d.matches(':modal')).at(-1) !== owner))
            || ((trigger || version) && (isBusy?.() || document.querySelector(GUIDE_ON_SCREEN)))) { release(loaded); return false; }
        styles();
        // Cards that come by themselves (entering a view, after an update) never block the view: a small card beside
        // what it explains. The full sheet is only for the examples someone asked to see.
        if ((trigger || version) && !grid) {
            // A card that comes by itself on entering a view needs its control on screen; it waits (unseen) otherwise.
            // The cards of an update without a control on screen are centred.
            if (trigger && !cardTarget(loaded[0].card, owner?.isConnected ? owner : document)) { release(loaded); return false; }
            return openAnchorCard({api, cn, loaded, owner, badge: version ? CARD_TEXT[cn ? 'zh' : 'en'].newIn(version) : ''});
        }
        return false;
    } finally { opening = false; }
}
// ── Cards that come by themselves: an anchored card beside the control it explains ──────────────────────────────
// What each card points at on screen. A card with no target here points at its "Take me there" controls; a card whose
// target is not on screen does not come by itself (it stays unseen and comes when its control is there).
const tourSelector = id => /^[.#[]|\s/.test(id) ? id : `[data-tour-id="${CSS.escape(id)}"]`;
const shownBox = node => {
    if (!node?.isConnected || node.closest('[hidden],[inert]')) return null;
    const r = node.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth ? r : null;
};
// "Generate a picture from sound": the empty stretch of the picture track that lies over a sound, which is where the
// person drags. The sound under it is kept in view (the card never covers it).
export function soundRange(scope) {
    const row = scope.querySelector('.fv-ed-track-row[data-track="v1"]'), lane = shownBox(row);
    if (!lane) return null;
    // The timeline may mark that stretch itself (a transparent element on the picture track); it wins when on screen.
    const mark = row.querySelector('[data-tour-id="video-empty-range"]'), marked = shownBox(mark);
    if (marked) {
        const a1 = [...scope.querySelectorAll('.fv-ed-clip[data-track="a1"]')].find(clip => { const r = shownBox(clip); return r && r.right > marked.left && r.left < marked.right; });
        return {node: mark, keep: a1 ? [a1] : [], rect: marked};
    }
    const scroll = shownBox(row.closest('.fv-ed-track-scroll')) || lane, heads = shownBox(scope.querySelector('.fv-ed-track-heads'));
    let from = Math.max(lane.left, scroll.left), to = Math.min(lane.right, scroll.right);
    if (heads && heads.right > from && heads.left <= from + 1) from = heads.right;
    const pictures = [...scope.querySelectorAll('.fv-ed-clip[data-track="v1"]')].map(shownBox).filter(Boolean);
    let best = null;
    for (const clip of scope.querySelectorAll('.fv-ed-clip[data-track="a1"]')) {
        const r = shownBox(clip); if (!r) continue;
        let spans = [[Math.max(r.left, from), Math.min(r.right, to)]];
        for (const p of pictures) spans = spans.flatMap(([a, b]) => p.right <= a || p.left >= b ? [[a, b]] : [[a, Math.min(b, p.left)], [Math.max(a, p.right), b]]);
        // The first such stretch from the left (the one a person meets first).
        for (const [a, b] of spans) if (b - a >= 32 && (!best || a < best.a)) best = {a, b, clip};
    }
    if (!best) return null;
    return {node: row, keep: [best.clip], rect: {left: best.a, right: best.b, top: lane.top, bottom: lane.bottom, width: best.b - best.a, height: lane.height}};
}
const TARGETS = {'sound-range': soundRange};
export function cardTarget(card, scope = document) {
    const want = card.target ?? card.goto;
    if (!want) return null;
    for (const item of Array.isArray(want) ? want : [want]) {
        if (TARGETS[item]) { const found = TARGETS[item](scope); if (found) return found; continue; }
        const node = [...scope.querySelectorAll(tourSelector(item))].find(shownBox);
        if (node) return {node, rect: node.getBoundingClientRect(), keep: []};
    }
    return null;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
const ICONS = {x: 'm4 4 8 8M12 4l-8 8', play: 'M5 3.2v9.6l7.6-4.8z', pause: 'M4.5 3h2.5v10H4.5zM9 3h2.5v10H9z', right: 'M3 8h10M9 4l4 4-4 4'};
function icon(name) {
    const svg = document.createElementNS(SVG_NS, 'svg'); svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('class', `fv-tour-i${name === 'play' || name === 'pause' ? ' is-solid' : ''}`);
    const path = document.createElementNS(SVG_NS, 'path'); path.setAttribute('d', ICONS[name]); svg.append(path); return svg;
}
const CARD_TEXT = {
    en: {gotIt: 'Got it', next: 'Next', close: 'Close', book: 'Read the tutorial', tryIt: 'Try it', play: 'Play with sound', pause: 'Pause', newIn: v => `New in ${v}`,
        xUse: 'Hide this; nothing else changes', gotItUse: 'Hide it; it will not show again', bookUse: 'Open the chapter about this', nextUse: 'See the next one',
        tryUse: 'Fill in this example to generate', playUse: 'Play this example with sound', segUse: 'Play this version with sound', key: 'Space'},
    zh: {gotIt: '知道了', next: '下一张', close: '关闭', book: '看教程', tryIt: '试一试', play: '播放声音', pause: '暂停', newIn: v => `${v} 新功能`,
        xUse: '收起说明，不影响当前操作', gotItUse: '收起，下次不再出现', bookUse: '打开讲这个功能的那一章', nextUse: '看下一个功能',
        tryUse: '把示例的描述和素材填进去', playUse: '有声播放这段示例', segUse: '有声播放这一版', key: '空格'},
};
const button = (text, kind, [name, use, key = '']) => {
    const node = el('button', 'fv-tour-btn', text); node.type = 'button'; if (kind) node.dataset.kind = kind;
    return tip(node, name, use, key);
};

// The card's example: one muted loop (or four for the quality levels); a card about sound shows the waveform and plays
// with sound from one button; versions of the same example are a segmented choice that plays the chosen one with sound.
function cardMedia(card, videos, lang, text, reduced) {
    const box = el('div', 'fv-tour-mediabox');
    const loop = video => { video.muted = true; video.loop = true; video.hidden = false; video.tabIndex = -1; video.removeAttribute('role'); video.onclick = null; video.onkeydown = null; video.setAttribute('aria-label', card.title[lang]); return video; };
    if (card.quality) {
        const quad = el('div', 'fv-tour-quad');
        card.quality.forEach((preview, i) => { const cell = el('figure'); cell.append(loop(videos[i]), el('figcaption', null, preview.label[lang])); quad.append(cell); });
        box.append(quad);
        return {box, playing: () => videos.slice(0, card.quality.length)};
    }
    const figure = el('figure', 'fv-tour-media');
    const wave = card.input.find(item => item.src && /wave/.test(item.src));
    const labelled = card.sequence?.filter(item => item.label) || [];
    let shown = 0, sound = false;
    const playButton = el('button', 'fv-tour-play'); playButton.type = 'button';
    const setPlay = () => { playButton.replaceChildren(icon(sound ? 'pause' : 'play'), document.createTextNode(sound ? text.pause : labelled.length === 1 ? labelled[0].label[lang] : text.play)); };
    const all = (card.sequence ? videos.slice(0, card.sequence.length) : [videos[0]]).map(loop);
    all.forEach((video, i) => { video.hidden = i !== shown; figure.append(video); });
    let waveImage = null;
    if (wave) { const strip = el('div', 'fv-tour-wave'); waveImage = el('img'); waveImage.alt = ''; waveImage.src = asset(wave.src); strip.append(waveImage); figure.append(strip); }
    const show = (i, withSound) => {
        shown = i; sound = withSound;
        all.forEach((video, j) => { video.hidden = j !== i; if (j !== i) { video.pause(); video.muted = true; } });
        const video = all[i]; video.currentTime = 0; video.muted = !withSound;
        if (withSound || !reduced) video.play().catch(() => {}); else video.pause();
        if (waveImage && card.sequence?.[i]?.wave) waveImage.src = asset(card.sequence[i].wave);
        segments.forEach((segment, j) => segment.setAttribute('aria-pressed', String(j === i)));
        setPlay();
    };
    const segments = [];
    // A sound card or a set of versions has its own play control; a silent example just loops.
    if (wave || labelled.length) {
        tip(playButton, text.play, text.playUse, text.key);
        playButton.onclick = () => { if (sound) { all[shown].pause(); all[shown].muted = true; sound = false; setPlay(); } else show(shown, true); };
        setPlay(); figure.append(playButton);
    }
    box.append(figure);
    if (labelled.length > 1) {
        const group = el('div', 'fv-tour-seg'); group.setAttribute('role', 'group'); group.setAttribute('aria-label', card.title[lang]);
        card.sequence.forEach((item, i) => {
            const segment = el('button', null, item.label[lang]); segment.type = 'button'; tip(segment, item.label[lang], text.segUse);
            segment.setAttribute('aria-pressed', String(i === 0)); segment.onclick = () => show(i, true); segments.push(segment); group.append(segment);
        });
        box.append(group);
        // Without a choice the versions take turns, silently.
        all.forEach((video, i) => { video.loop = false; video.onended = () => { if (!sound) show((i + 1) % all.length, false); else { video.currentTime = 0; video.play().catch(() => {}); } }; });
    }
    return {box, playing: () => [all[shown]], stop: () => { for (const video of all) { video.pause(); video.muted = true; } sound = false; setPlay(); }};
}

/**
 * Show cards as an anchored card. automatic: the card came by itself (entering a view, adding media, after an update):
 * it does not take focus, and closing it counts as seen. Returns false when nothing could be shown.
 */
function openAnchorCard({api, cn, loaded, owner, automatic = true, badge = ''}) {
    const lang = cn ? 'zh' : 'en', text = CARD_TEXT[lang], reduced = reducedMotion();
    const scope = owner?.isConnected ? owner : document;
    let index = 0, media = null, layer = null, finished = false;
    const stopAll = () => { media?.stop?.(); for (const {videos} of loaded) for (const video of videos) video.pause(); };
    const finish = reason => {
        if (reason === 'replace' || finished) return; finished = true; stopAll(); release(loaded); active = null;
        // Putting a card away counts as seen; a card that left with its view, or whose control went away, comes again.
        if (!['leave', 'gone', 'dialog'].includes(reason)) markTourSeen(api, loaded.slice(0, index + 1).map(item => item.card));
    };
    function draw(focus = false) {
        stopAll();
        const {card, videos} = loaded[index];
        const found = cardTarget(card, scope);
        media = cardMedia(card, videos, lang, text, reduced);
        const body = el('div', 'fv-tour-body');
        if (badge) { const eyebrow = el('div', 'fv-tour-eyebrow'); eyebrow.append(el('span', 'fv-tour-badge', badge)); body.append(eyebrow); }
        const head = el('div', 'fv-tour-head'), title = el('h3', null, card.title[lang]); title.id = 'fv-tour-card-title';
        const x = el('button', 'fv-tour-x'); x.type = 'button'; x.setAttribute('aria-label', text.close); x.append(icon('x')); tip(x, text.close, text.xUse, 'Esc');
        x.onclick = () => layer?.close('fade', 'dismiss');
        head.append(title, x);
        const live = el('span', 'fv-tour-live', card.title[lang]); live.setAttribute('aria-live', 'polite');
        const foot = el('div', 'fv-tour-foot');
        const chapter = BOOK_CHAPTER[card.id];
        if (chapter) {
            const read = button(text.book, 'link', [text.book, text.bookUse]);
            read.onclick = async () => { await layer?.close('now', 'dismiss'); const {openTutorialBook} = await import('./tutorial_book.js'); openTutorialBook({chapter}); };
            foot.append(read);
        }
        foot.append(el('span', 'fv-tour-grow'));
        if (loaded.length > 1) {
            const pips = el('span', 'fv-tour-pips'); pips.setAttribute('aria-label', `${index + 1} / ${loaded.length}`);
            loaded.forEach((_, i) => pips.append(el('i', i === index ? 'is-on' : '')));
            foot.append(pips);
        }
        const more = index < loaded.length - 1;
        if (more) { const next = button(text.next, 'fill', [text.next, text.nextUse, '→']); next.onclick = () => { index++; draw(true); }; foot.append(next); }
        const usable = card.tryit && card.tryit.status !== 'pending-assets' && contextFor()?.canTry?.(card.tryit) !== false;
        if (usable) {
            const go = button(text.tryIt, 'primary', [text.tryIt, text.tryUse]); go.dataset.fvTourPrimary = '';
            go.onclick = async () => { await layer?.close('now', 'dismiss'); await tryIt(card.id); };
            foot.append(go);
        } else if (!more) {
            const done = button(text.gotIt, 'fill', [text.gotIt, text.gotItUse, 'Esc']); done.dataset.fvTourPrimary = '';
            done.onclick = () => layer?.close('fade', 'dismiss'); foot.append(done);
        }
        body.append(head, el('p', 'fv-tour-text', card.line[lang]), live, foot);
        const keep = found?.keep || [];
        layer = openAnchor({owner, target: found ? (TARGETS[card.target] || found.node) : null, keep, level: 2, shapes: card.quality ? ['stand'] : ['stand', 'wide'],
            content: [media.box, body], ghost: card.target === 'sound-range', focus, label: title.id, onClose: finish});
        if (!reduced) for (const video of media.playing()) video.play().catch(() => {});
        for (const video of videos) video.onerror = () => { if (loaded[index]?.videos.includes(video)) layer?.close('now', 'gone'); };
    }
    draw(!automatic);
    active = {owner, close: () => layer?.close('fade', 'dismiss') || Promise.resolve()};
    return true;
}
const pageZh = () => isChinese();
/** One card on request (the tutorial's examples, a chapter's card): beside its control when that control is on screen,
 * otherwise centred; the keyboard moves into it. */
export async function showFeatureCard(id, {owner = globalThis.FreeVideoShell?.viewDialog?.() || contextFor()?.dialog || null, cn = contextFor()?.cn ?? pageZh(), api = contextFor()?.api} = {}) {
    const card = FEATURE_TOUR.find(c => c.id === id);
    if (!card) return false;
    if (active) await active.close();
    const loaded = await loadTourCard(card);
    if (!loaded) return false;
    styles();
    return openAnchorCard({api, cn, loaded: [loaded], owner, automatic: false});
}
// Wait until the view has drawn its content (the editor marks data-fv-loaded), then a little longer, so a card never
// arrives during the view's own entrance.
async function settled(owner) {
    if (!owner) return;
    for (let i = 0; i < 30 && owner.isConnected && owner.dataset.fvLoaded === 'false'; i++) await new Promise(resolve => setTimeout(resolve, 100));
    await new Promise(resolve => setTimeout(resolve, 600));
}
// Busy: a run in this view, a drag, another dialog or menu over it, the guide or a how-to on screen.
function busyOver(owner, isBusy) {
    if (isBusy?.() || document.querySelector(GUIDE_ON_SCREEN) || document.documentElement.classList.contains('fv-dragging')) return true;
    const top = [...document.querySelectorAll('dialog[open]')].filter(dialog => !dialog.matches('.fv-tip')).at(-1);
    if (owner && top && top !== owner && !owner.contains(top)) return true;
    return !!owner?.querySelector('.fv-ed-genbar[data-mode=running],[role=menu]:not([hidden]):popover-open');
}
export async function triggerFeatureTour(trigger, options = {}) {
    const context = {...contextFor(), ...options};
    const owner = context.owner || globalThis.FreeVideoShell?.viewDialog?.() || document.querySelector('dialog.fv-editor[open]') || context.dialog;
    if (!context.api || busyOver(owner, context.isBusy)) return false;
    await settled(owner);
    if (busyOver(owner, context.isBusy) || (owner && !owner.open)) return false;
    return openFeatureTour({...context, owner, trigger});
}
export async function offerVersionTour(context) {
    try {
        const reply = await context.api.fetchApi('/freevideo/updates?client=1');
        const version = reply.ok && (await reply.json()).current_release?.product_version;
        if (version) return openFeatureTour({...context, version});
    } catch { /* Optional guidance cannot block creation. */ }
    return false;
}
