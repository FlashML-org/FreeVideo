import {FEATURE_TOUR, readyCards, tourKey, unseenCards} from './feature_tour_content.js';
import {readGuideRecord, saveGuideRecord, guideRecord} from './first_run_guide.js';
import {isolateDialogKeys, listenDialogKey, routeDialogKey} from './dialog_keys.js';

export const TOUR_TEXT = {
    en: {examples: 'Feature examples', use: 'Use this example', next: 'Next', done: 'Done', close: 'Close'},
    zh: {examples: '功能示例', use: '用这个示例', next: '下一张', done: '完成', close: '关闭'},
};
const asset = name => new URL(`./assets/tour/${name}`, import.meta.url).href;
const el = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text != null) node.textContent = text; return node; };
const contexts = new Map();
let active = null, opening = false;
export function registerTourWorkspace(context) {
    contexts.set(context.dialog, context);
    return () => { contexts.delete(context.dialog); if (active?.owner === context.dialog) active.close(); };
}
const contextFor = () => [...contexts.values()].filter(c => c.dialog.isConnected).at(-1);
export async function tryIt(example, options = {}) {
    try {
        const {TOUR_EXAMPLES} = await import('./tour_try.js');
        // A tutorial chapter passes its example itself; a start clip is not supported yet, so such a chapter fills the prompt only.
        const given = example && typeof example === 'object' ? {assets: example.assets || [], prompt: example.prompt, settings: example.settings || {}} : null;
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
    if (!api || opening || active) return false;
    opening = true;
    const restore = document.activeElement;
    let loaded = [];
    try {
        const record = await readGuideRecord(api);
        if (!record.ok) return false;
        if (trigger || version) cards = unseenCards(record.record, {trigger, version, cards});
        // Contextual help always offers one card per use.
        if (trigger) cards = cards.slice(0, 1);
        if (grid) cards = [...cards].sort((a, b) => Number(record.record?.seen.includes(tourKey(a))) - Number(record.record?.seen.includes(tourKey(b))));
        loaded = (await Promise.all(cards.map(c => loadTourCard(c)))).filter(Boolean);
        if (!loaded.length || (owner && (!owner.open || !owner.isConnected || [...document.querySelectorAll('dialog[open]')].at(-1) !== owner))
            || ((trigger || version) && (isBusy?.() || document.querySelector(GUIDE_ON_SCREEN)))) { release(loaded); return false; }
        styles();
        const lang = cn ? 'zh' : 'en', text = TOUR_TEXT[lang];
        const dialog = el('dialog', 'fv-feature-tour'); dialog.lang = cn ? 'zh-CN' : 'en';
        dialog.setAttribute('aria-labelledby', 'fv-tour-title'); dialog.tabIndex = -1;
        const header = el('header', 'fv-tour-header'), title = el('h2', null); title.id = 'fv-tour-title';
        const close = el('button', 'fv-tour-close', '✕'); close.type = 'button'; close.setAttribute('aria-label', text.close);
        header.append(title, close);
        const content = el('div', 'fv-tour-content'); dialog.append(header, content);
        let index = Math.max(0, loaded.findIndex(item => item.card.id === start)), selected = !grid, disposed = false;
        let playing = [], syncTimer = null;
        const reduced = matchMedia('(prefers-reduced-motion: reduce)');
        const stop = () => { clearInterval(syncTimer); for (const video of playing) video.pause(); playing = []; };
        const play = () => {
            for (const video of playing) video.play().catch(() => {});
            clearInterval(syncTimer);
            if (playing.length > 1) syncTimer = setInterval(() => {
                const leader = playing[0];
                for (const video of playing.slice(1)) if (Math.abs(video.currentTime - leader.currentTime) > .12) video.currentTime = leader.currentTime;
            }, 200);
        };
        const end = () => new Promise(resolve => {
            if (!dialog.open) { resolve(); return; }
            dialog.addEventListener('close', resolve, {once: true}); dialog.close();
        });
        function media(video, range) {
            video.loop = !range; video.tabIndex = 0; video.setAttribute('role', 'button'); video.setAttribute('aria-label', loaded[index].card.title[lang]);
            video.onclick = play;
            video.onkeydown = event => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); play(); } };
            if (range) {
                video.currentTime = range[0];
                video.ontimeupdate = () => { if (video.currentTime >= range[1]) video.currentTime = range[0]; };
            }
            playing.push(video); return video;
        }
        function draw() {
            stop(); content.replaceChildren();
            const {card, videos} = loaded[index]; dialog.dataset.card = selected ? card.id : ''; dialog.dataset.mode = selected ? 'card' : 'grid';
            if (!selected) {
                title.textContent = text.examples;
                const tiles = el('div', 'fv-tour-grid');
                loaded.forEach(({card}, i) => {
                    const button = el('button', 'fv-tour-tile'), poster = el('img'); poster.src = asset(card.clip.poster); poster.alt = '';
                    button.append(poster, el('span', null, card.title[lang])); button.onclick = () => { index = i; selected = true; draw(); }; tiles.append(button);
                }); content.append(tiles); dialog.focus(); return;
            }
            title.textContent = card.title[lang];
            content.append(el('p', 'fv-tour-line', card.line[lang]));
            const pair = el('div', 'fv-tour-pair'), input = el('div', 'fv-tour-input'), output = el('div', 'fv-tour-output');
            card.input.forEach(item => {
                if (item.text) input.append(el('p', null, item.text[lang]));
                else if (item.mp4) input.append(media(videos.at(-1)));
                else { const image = el('img'); image.src = asset(item.src); image.alt = ''; input.append(image); }
            });
            const arrow = el('span', 'fv-tour-arrow'); arrow.setAttribute('aria-hidden', 'true');
            // A thin CSS line and two strokes keep this decorative arrow out of the reading order.
            if (card.quality) {
                pair.classList.add('fv-tour-quality'); output.classList.add('fv-tour-four');
                card.quality.forEach((preview, i) => { const cell = el('figure'); cell.append(media(videos[i]), el('figcaption', null, preview.label[lang])); output.append(cell); });
                videos[0].onended = () => { for (const video of playing) video.currentTime = 0; play(); };
                for (const video of videos) video.loop = false;
            } else if (card.sequence) {
                videos.forEach((video, i) => {
                    video.hidden = i !== 0; media(video); video.loop = false;
                    video.onended = () => { video.hidden = true; const next = videos[(i + 1) % videos.length]; next.hidden = false; next.currentTime = 0; playing = [next]; next.play().catch(() => {}); };
                    output.append(video);
                });
                playing = [videos[0]];
            } else output.append(media(videos[0], card.previewRange));
            pair.append(input, arrow, output); content.append(pair);
            const footer = el('footer', 'fv-tour-footer'), dots = el('div', 'fv-tour-dots'), actions = el('div', 'fv-tour-actions');
            loaded.forEach(({card}, i) => { const dot = el('button', 'fv-tour-dot'); dot.setAttribute('aria-label', card.title[lang]); dot.setAttribute('aria-current', String(i === index)); dot.onclick = () => { index = i; draw(); }; dots.append(dot); });
            const next = el('button', 'fv-tour-next', index === loaded.length - 1 ? text.done : text.next); next.onclick = () => { if (index === loaded.length - 1) end(); else { index++; draw(); } };
            const use = el('button', 'fv-tour-use fv-primary', text.use);
            const usable = card.tryit.status !== 'pending-assets' && contextFor()?.canTry?.(card.tryit) !== false;
            use.onclick = async () => { await end(); await tryIt(card.id); };
            usable ? actions.append(next, use) : actions.append(next); footer.append(dots, actions); content.append(footer);
            for (const video of videos) video.onerror = () => {
                stop(); const failed = loaded.splice(index, 1); release(failed);
                if (!loaded.length) { end(); return; }
                index = Math.min(index, loaded.length - 1); draw();
            };
            dialog.focus(); if (!reduced.matches) play();
        }
        const disposeKeys = isolateDialogKeys(dialog, event => routeDialogKey(event, dialog));
        const removeKeys = listenDialogKey(dialog, 'keydown', event => {
            if (event.key === 'Escape') { event.preventDefault(); end(); }
            else if (selected && ['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); index = (index + (event.key === 'ArrowLeft' ? -1 : 1) + loaded.length) % loaded.length; draw(); }
            else if (event.key === 'Tab') {
                const nodes = [...dialog.querySelectorAll('button:not(:disabled),video:not([hidden])')], at = nodes.indexOf(document.activeElement);
                event.preventDefault(); nodes[(at + (event.shiftKey ? -1 : 1) + nodes.length) % nodes.length]?.focus();
            }
        });
        close.onclick = end;
        dialog.addEventListener('click', event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) end(); } });
        dialog.addEventListener('cancel', event => { event.preventDefault(); end(); });
        const motion = () => { if (selected) { stop(); draw(); } };
        reduced.addEventListener('change', motion);
        dialog.addEventListener('close', () => {
            if (disposed) return; disposed = true; stop(); release(loaded); disposeKeys(); removeKeys(); reduced.removeEventListener('change', motion);
            // Merely opening the thumbnail grid does not consume cards. Closing a card acknowledges its whole group.
            if (selected) markTourSeen(api, loaded.map(item => item.card));
            dialog.remove(); active = null; if (restore?.isConnected) restore.focus({preventScroll: true});
        }, {once: true});
        document.body.append(dialog); active = {owner, close: end}; dialog.showModal(); draw(); return true;
    } finally { opening = false; }
}
export async function triggerFeatureTour(trigger, options = {}) {
    const context = {...contextFor(), ...options};
    if (!context.api || context.isBusy?.() || document.querySelector(GUIDE_ON_SCREEN)) return false;
    return openFeatureTour({...context, owner: context.owner || document.querySelector('dialog.fv-editor[open]') || context.dialog, trigger});
}
export async function offerVersionTour(context) {
    try {
        const reply = await context.api.fetchApi('/freevideo/updates?client=1');
        const version = reply.ok && (await reply.json()).current_release?.product_version;
        if (version) return openFeatureTour({...context, version});
    } catch { /* Optional guidance cannot block creation. */ }
    return false;
}
