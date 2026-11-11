// What's new in 0.5: the editor's own track view drawn with the editor's own classes and played once.
// Nothing is recorded; the picture is a placeholder, the tracks, clips, progress and waveforms are the real styles.
import {PAGES, LINES} from './whatsnew_pages.js';
import {segmented} from './editor_adjust.js';
import {PEAKS} from './whatsnew_peaks.js';

const KEY = 'freevideo.whatsnew-seen';
const SERIES = '0.5';
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const svg = (tag, attrs = {}) => { const e = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
const ICON = {video: 'M2 4h16v12H2z M4 13l4-5 4 4 2-2 3 4', audio: 'M2 8h4l4-4v12l-4-4H2z M13 6c4 2 4 6 0 8 M13 9c1 1 1 1 0 2'};
const icon = kind => { const s = svg('svg', {viewBox: '0 0 20 20', 'aria-hidden': 'true'}); s.append(svg('path', {d: ICON[kind]})); return s; };

// A waveform in the editor's 200x40 box from real peaks, drawn as the editor draws them.
function wave(id) {
    const peaks = PEAKS[id];
    let d = '';
    peaks.forEach((peak, i) => { const h = Math.max(.5, Math.min(1, peak) * 17); d += `M${(i + .5) * 2} ${20 - h}v${h * 2}`; });
    const s = svg('svg', {viewBox: '0 0 200 40', preserveAspectRatio: 'none', 'aria-hidden': 'true'});
    s.classList.add('fv-ed-waveform'); s.append(svg('path', {d})); return s;
}

const frame = fn => requestAnimationFrame(() => requestAnimationFrame(fn));

// One page's scene: the editor's track view under its generation bar, changed only by the ops of its steps.
export function createStage(cn, page) {
    const t = pair => pair[cn ? 1 : 0];
    const root = el('div', null, 'fv-whatsnew-stage'); root.setAttribute('aria-hidden', 'true'); root.dataset.lang = cn ? 'zh' : 'en';
    let ticker = 0, dragTicker = 0, instantLayout = false, need = 0;
    // The generation bar, the lines and the recording bar are the editor's own, under the editor's styles; they share one box above the tracks.
    const top = el('div', null, 'fv-whatsnew-top fv-whatsnew-ed fv-studio fv-editor');
    const view = el('section', null, 'fv-ed-track-view'); view.dataset.trackSwitches = 'false';
    const heads = el('div', null, 'fv-ed-track-heads'), scroll = el('div', null, 'fv-ed-track-scroll'), content = el('div', null, 'fv-ed-track-content');
    const ruler = el('div', null, 'fv-ed-ruler');
    for (let s = 0; s <= 10; s++) {
        const tick = el('div', null, 'fv-ed-tick' + (s % 2 ? '' : ' fv-ed-tick-major')); tick.style.left = `${s * 10}%`;
        if (s % 2 === 0) tick.append(el('span', `0:${String(s).padStart(2, '0')}`));
        ruler.append(tick);
    }
    content.append(ruler);
    let bottom = 24;
    const rows = {}, names = {}, headNodes = {}, bodies = {};
    for (const row of page.rows) {
        const body = el('div', null, 'fv-ed-track-row'); body.dataset.track = row.id; body.style.top = `${bottom}px`; body.style.height = `${row.height}px`;
        const head = el('div', null, 'fv-ed-track-head'); head.dataset.track = row.id; head.style.top = `${bottom}px`; head.style.height = `${row.height}px`;
        const name = el('span', t(row.name)); head.append(icon(row.kind), name); heads.append(head); content.append(body);
        rows[row.id] = body; names[row.id] = {row, name}; headNodes[row.id] = head; bodies[row.id] = body; bottom += row.height + 6;
    }
    // The view and the playhead reach the last track that is showing, and follow it when a track appears.
    const fit = () => {
        let low = 24;
        for (const id in names) if (headNodes[id].dataset.on !== 'false') low = Math.max(low, parseFloat(bodies[id].style.top) + names[id].row.height + 6);
        low = Math.max(low, menu.offsetHeight + 40);
        content.style.height = `${low}px`; playhead.style.bottom = 'auto'; playhead.style.height = `${low}px`;
        // What the frame needs in this state: the bar and the tracks, or the bar and the lines laid over the lower tracks with the dialogue track left showing.
        need = Math.max(top.offsetHeight + low + 4, linesBox.hidden ? 0 : top.offsetHeight + 122 + linesBox.offsetHeight);
    };
    const clips = {};
    for (const spec of page.clips) {
        const c = el('div', null, 'fv-ed-clip'); c.dataset.name = spec.id; c.tabIndex = -1;
        const media = el('span', null, 'fv-ed-clip-media');
        if (spec.picture) media.classList.add('fv-whatsnew-picture');
        if (spec.wave) media.append(wave(spec.wave));
        c.append(media, el('span', null, 'fv-ed-progress-fill'));
        rows[spec.row].append(c); clips[spec.id] = {spec, node: c};
    }
    const playhead = el('div', null, 'fv-whatsnew-playhead'), range = el('div', null, 'fv-whatsnew-range'), rangeText = el('span');
    range.append(rangeText); content.append(range, playhead);
    // The clip's right-click menu, as the editor draws it.
    const menuBox = el('div', null, 'fv-whatsnew-menu fv-whatsnew-ed fv-studio fv-editor'), menu = el('div', null, 'fv-ed-context-menu');
    for (const item of page.menuItems || []) {
        if (item === '-') { const rule = el('div'); rule.setAttribute('role', 'separator'); menu.append(rule); continue; }
        const b = el('button'); b.type = 'button'; b.tabIndex = -1; b.setAttribute('role', 'menuitem'); b.append(el('span', t(item), 'fv-ed-menu-label')); menu.append(b);
    }
    menuBox.append(menu); root.append(menuBox);
    scroll.append(content); view.append(heads, scroll);

    const SRC = page.lines || LINES;
    const button = (label, cls) => { const b = el('button', label, cls); b.type = 'button'; b.tabIndex = -1; return b; };
    const genbar = el('div', null, 'fv-ed-genbar');
    const voice = el('div', null, 'fv-ed-voice'), voiceTop = el('div', null, 'fv-ed-voice-top'), voiceList = el('div', null, 'fv-ed-voice-lines');
    voiceTop.append(el('span', t(['Lines', '台词']), 'fv-ed-voice-name'));
    const lineNodes = SRC.map((_, i) => {
        const line = el('div', null, 'fv-ed-voice-line'), main = el('div', null, 'fv-ed-voice-main'), time = button(`0:0${[0, 4, 7][i]}`, 'fv-ed-voice-time'), body = el('div', null, 'fv-ed-voice-text');
        main.append(el('span', null, 'fv-ed-voice-mark'), time, body); line.append(main); voiceList.append(line); return {line, body};
    });
    // The three lines lie over the lower tracks in a box of their own; the bar keeps one row, with the name and the button.
    const linesBox = el('div', null, 'fv-whatsnew-lines fv-whatsnew-top fv-whatsnew-ed fv-studio fv-editor'); linesBox.hidden = true; linesBox.append(voiceList);
    voice.append(voiceTop);
    const presets = el('div', null, 'fv-ed-music-presets');
    const picker = (label, options) => {
        const row = el('div', null, 'fv-ed-music-row'), control = segmented(options.map(([value, text]) => ({value, label: t(text)})));
        control.setAttribute('aria-label', label); control.querySelectorAll('button').forEach(b => { b.tabIndex = -1; });
        row.append(el('span', label, 'fv-ed-music-label'), control); presets.append(row); return control;
    };
    const styles = picker(t(['Style', '风格']), [['piano', ['Piano', '钢琴']], ['folk', ['Folk', '民谣']], ['ambient', ['Ambient', '氛围']]]);
    const moods = picker(t(['Mood', '情绪']), [['calm', ['Calm', '舒缓']], ['upbeat', ['Upbeat', '明快']], ['tense', ['Tense', '紧张']], ['warm', ['Warm', '温暖']], ['sad', ['Sad', '悲伤']]]);
    const description = el('div', null, 'fv-ed-description-box'), text = el('textarea', null, 'fv-ed-description');
    text.rows = 1; text.style.height = '36px'; text.readOnly = true; text.tabIndex = -1; description.append(text);
    const hint = el('div', null, 'fv-ed-gen-hint'), go = button('', 'fv-ed-generate fv-primary'), cancel = button(t(['Cancel', '取消']));
    genbar.append(voice, presets, description, hint, go, cancel);
    const record = el('div', null, 'fv-ed-record'), recMain = el('button', null, 'fv-ed-record-main'), recNote = el('span', null, 'fv-ed-record-note');
    recMain.type = 'button'; recMain.tabIndex = -1;
    const recSide = el('div', null, 'fv-ed-record-side'), recCenter = el('div', null, 'fv-ed-record-center'), recEnd = el('div', null, 'fv-ed-record-side fv-ed-record-end');
    recSide.append(el('span', t(['Record', '录音']))); recCenter.append(recMain); recEnd.append(recNote); record.append(recSide, recCenter, recEnd);
    top.append(genbar, record); root.append(top, view, linesBox);
    const list = el('ol', null, 'fv-whatsnew-list'); for (const item of page.list || []) list.append(el('li', t(item)));

    let typing = 0, timers = [];
    const place = (c, at, to) => { c.node.style.left = `${at * 100}%`; c.node.style.width = `${(to - at) * 100}%`; };
    const bar = {}, linesState = {};
    const typeInto = (node, full, done) => {
        const put = value => { if (node.tagName === 'TEXTAREA') node.value = value; else node.textContent = value; };
        clearInterval(typing); const per = cn ? 70 : 35; let n = 0; put('');
        typing = setInterval(() => { n++; put(full.slice(0, n)); if (n >= full.length) { clearInterval(typing); done?.(); } }, per);
    };
    // The box above the tracks grows and shrinks to its content over 240 ms.
    const morph = change => {
        if (instantLayout) { change(); return; }
        const from = top.offsetHeight, shown = [voice.hidden, record.hidden, genbar.hidden].join();
        top.style.transition = 'none'; top.style.height = ''; change(); const to = top.offsetHeight;
        // A panel that appears or goes is swapped at once, so no half-open frame shows; the other changes grow or shrink over 240 ms.
        if (from === to || shown !== [voice.hidden, record.hidden, genbar.hidden].join()) { top.style.transition = ''; return; }
        top.style.height = `${from}px`; top.getBoundingClientRect(); top.style.transition = ''; top.style.height = `${to}px`;
    };
    const paintLines = () => {
        lineNodes.forEach(({line, body: node}, i) => {
            const [full, old, fresh] = SRC[i].map(x => t(x));
            line.dataset.changed = String(i === 1 && !!linesState.edit);
            if (i !== 1) { node.textContent = full; return; }
            if (linesState.edit === 'strike') node.replaceChildren(...full.split(old).flatMap((p, k, a) => k < a.length - 1 ? [document.createTextNode(p), el('s', old)] : [document.createTextNode(p)]));
            else if (linesState.edit === 'typing') { const [head, tail] = full.split(old); node.replaceChildren(document.createTextNode(head), el('s', old), el('b', null, 'fv-whatsnew-new'), document.createTextNode(tail)); typeInto(node.querySelector('b'), fresh); }
            else if (linesState.edit === 'done') node.textContent = full.replace(old, fresh);
            else node.textContent = full;
        });
    };
    const paintBar = () => {
        const mode = bar.mode || 'idle', running = mode === 'running', recording = mode === 'record', open = mode === 'selected' || mode === 'lines';
        genbar.hidden = recording; record.hidden = !recording;
        genbar.dataset.mode = running ? 'running' : open ? 'selected' : 'idle';
        voice.hidden = linesBox.hidden = !linesState.shown; if (linesState.shown) genbar.dataset.voice = 'true'; else genbar.removeAttribute('data-voice');
        presets.hidden = !bar.presets; genbar.classList.toggle('fv-ed-genbar-music', !!bar.presets);
        styles.value = bar.style; moods.value = bar.mood; styles.disabled = moods.disabled = running;
        text.placeholder = bar.ph ? t(bar.ph) : ''; text.disabled = running;
        hint.hidden = !running; hint.textContent = running ? (bar.hint || []).map(t).join(' · ') : '';
        go.hidden = running; go.disabled = !open; go.textContent = bar.button ? t(bar.button) : ''; cancel.hidden = !running;
        record.dataset.phase = bar.phase || 'idle'; recMain.textContent = bar.phase === 'recording' ? t(['Stop', '停止']) : t(['Start recording', '开始录音']);
    };
    const reset = () => {
        clearInterval(typing); clearInterval(ticker); clearInterval(dragTicker); timers.forEach(clearTimeout); timers = []; recNote.textContent = '';
        for (const key of Object.keys(bar)) delete bar[key];
        for (const key of Object.keys(linesState)) delete linesState[key];
        for (const c of Object.values(clips)) {
            place(c, c.spec.at, c.spec.at + (c.spec.grow ? .02 : c.spec.to - c.spec.at));
            c.node.dataset.on = String(!c.spec.off); c.node.dataset.state = 'ready'; c.node.removeAttribute('data-selected'); c.node.removeAttribute('data-hl');
            c.node.style.removeProperty('--fv-ed-progress'); c.node.style.clipPath = ''; c.node.style.transition = ''; c.node.style.opacity = '';
        }
        for (const id in names) { names[id].name.textContent = t(names[id].row.name); headNodes[id].dataset.on = String(!names[id].row.late); bodies[id].dataset.on = String(!names[id].row.late); }
        const faders = [...Object.values(clips).map(c => c.node), ...Object.values(headNodes), ...Object.values(bodies)];
        faders.forEach(n => { n.style.transition = 'none'; }); frame(() => faders.forEach(n => { n.style.transition = ''; }));
        playhead.style.cssText = ''; playhead.dataset.on = 'false'; range.dataset.on = 'false'; range.style.cssText = ''; menuBox.dataset.on = 'false';
        text.value = ''; list.querySelectorAll('li').forEach(li => li.removeAttribute('data-lit'));
        top.style.height = ''; top.style.transition = '';
        paintLines(); paintBar(); fit();
    };
    const set = (ops, instant = false) => {
        if (ops.split) {
            const faders = [...Object.values(clips).map(c => c.node), ...Object.values(headNodes), ...Object.values(bodies)];
            faders.forEach(n => { n.style.transition = 'none'; }); frame(() => faders.forEach(n => { n.style.transition = ''; }));
            for (const c of Object.values(clips)) { if (c.spec.splits) c.node.dataset.on = 'true'; if (c.spec.id === 'mix') c.node.dataset.on = 'false'; }
            for (const id in names) if (names[id].row.late && !names[id].row.record) { headNodes[id].dataset.on = 'true'; bodies[id].dataset.on = 'true'; }
            if (names.a1.row.dialogue) names.a1.name.textContent = t(names.a1.row.dialogue);
        }
        if (ops.rowShow) { headNodes[ops.rowShow].dataset.on = 'true'; bodies[ops.rowShow].dataset.on = 'true'; }
        for (const id of ops.hide || []) clips[id].node.dataset.on = 'false';
        for (const id of ops.show || []) clips[id].node.dataset.on = 'true';
        if ('select' in ops) for (const c of Object.values(clips)) { if (c.spec.id === ops.select) c.node.dataset.selected = 'true'; else c.node.removeAttribute('data-selected'); }
        if ('hl' in ops) for (const c of Object.values(clips)) { if (ops.hl.includes(c.spec.id)) c.node.dataset.hl = 'true'; else c.node.removeAttribute('data-hl'); }
        if (ops.lit) list.querySelectorAll('li').forEach((li, i) => { if (ops.lit.includes(i)) li.dataset.lit = 'true'; else li.removeAttribute('data-lit'); });
        if (ops.menu) {
            menuBox.dataset.on = String(!!ops.menu.show);
            menuBox.style.left = `${view.offsetLeft + scroll.offsetLeft + Math.max(8, Math.min(scroll.clientWidth * .38, scroll.clientWidth - menu.offsetWidth - 8))}px`; menuBox.style.top = `${view.offsetTop + 34}px`;
            menu.querySelectorAll('button').forEach((b, i) => { b.dataset.hover = String(ops.menu.hover === i); });
        }
        morph(() => {
            if (ops.bar) {
                const wasType = ops.bar.type;
                Object.assign(bar, ops.bar);
                if ('text' in ops.bar) { text.value = ops.bar.text ? t(ops.bar.text) : ''; }
                if (wasType) { delete bar.type; typeInto(text, t(wasType), paintBar); }
            }
            if (ops.lines) { Object.assign(linesState, ops.lines); paintLines(); }
            paintBar();
        });
        fit();
        if ('at' in ops) { playhead.dataset.on = 'true'; playhead.style.left = `${ops.at * 100}%`; }
        if (ops.run && !instant) {
            const {id, ms} = ops.run, node = clips[id].node, fill = node.querySelector('.fv-ed-progress-fill');
            node.dataset.state = 'running'; fill.style.transition = 'none'; node.style.setProperty('--fv-ed-progress', '0');
            frame(() => { fill.style.transition = `width ${ms}ms linear`; node.style.setProperty('--fv-ed-progress', '1'); });
        }
        if (ops.done) { const c = clips[ops.done].node; c.dataset.state = 'ready'; c.style.removeProperty('--fv-ed-progress'); }
        if (ops.reveal && !instant) {
            const {id, ms} = ops.reveal, node = clips[id].node;
            node.style.transition = 'none'; node.style.clipPath = 'inset(0 100% 0 0)';
            frame(() => { node.style.transition = `clip-path ${ms}ms var(--fv-ease)`; node.style.clipPath = 'inset(0 0 0 0)'; });
        }
        if (ops.flash && !instant) { const node = clips[ops.flash].node; node.dataset.flash = 'true'; timers.push(setTimeout(() => node.removeAttribute('data-flash'), 900)); }
        if (ops.play) {
            const [from, to, ms] = ops.play; playhead.dataset.on = 'true'; playhead.style.transition = 'none'; playhead.style.left = `${(instant ? to : from) * 100}%`;
            if (!instant) frame(() => { playhead.style.transition = `left ${ms}ms linear`; playhead.style.left = `${to * 100}%`; });
        }
        if (ops.rec) {
            const {id, from, to, ms} = ops.rec, c = clips[id]; c.node.style.transition = 'none'; place(c, from, instant ? to : from + .01); c.node.dataset.on = 'true';
            if (!instant) {
                frame(() => { c.node.style.transition = `width ${ms}ms linear`; place(c, from, to); });
                const started = performance.now(); clearInterval(ticker);
                ticker = setInterval(() => { const sec = Math.min(Math.floor((performance.now() - started) / 1000), Math.floor(ms / 1000)); recNote.textContent = `0:0${sec}`; }, 250);
            }
        }
        if (instant) recNote.textContent = '';
    };
    reset();
    // Reduced motion shows the finished page: every step applied at once, typed text complete.
    const settle = ops => { const o = {...ops}; if (o.bar?.type) o.bar = {...o.bar, text: o.bar.type, type: null}; if (o.lines?.edit === 'typing') o.lines = {...o.lines, edit: 'done'}; set(o, true); };
    const finish = () => { instantLayout = true; for (const [, ops] of page.steps) settle(ops); range.dataset.on = 'false'; instantLayout = false; };
    // The tallest the demo gets over the steps: the frame has that height from the start to the end, so nothing below it moves.
    const tallest = () => {
        root.classList.add('fv-whatsnew-measure'); instantLayout = true; reset(); let max = need;
        for (const [, ops] of page.steps) { settle(ops); max = Math.max(max, need); }
        instantLayout = false; reset(); root.classList.remove('fv-whatsnew-measure'); return max;
    };
    return {element: root, list, set, reset, finish, tallest, height: value => { root.style.height = `${value}px`; }};
}

// Plays a page's steps once and stays on the finished state; reduced motion shows that state at once.
export function play(stage, page, reduce) {
    let timers = [], dead = false;
    if (reduce) { stage.finish(); return () => {}; }
    stage.reset();
    for (const [at, ops] of page.steps) timers.push(setTimeout(() => !dead && stage.set(ops), at));
    return () => { dead = true; timers.forEach(clearTimeout); };
}

// Two records: the 0.5 sound page, and the 0.5.1 picture page, which people who already saw the sound page get once more.
const IMAGE_KEY = 'freevideo.whatsnew-seen-image';
const read = key => { try { return localStorage.getItem(key); } catch { return null; } };
const seen = page => page.id === 'image' ? read(IMAGE_KEY) === '1' : read(KEY) === SERIES;
const remember = pages => { try { for (const page of pages) localStorage.setItem(page.id === 'image' ? IMAGE_KEY : KEY, page.id === 'image' ? '1' : SERIES); } catch { /* Shown again next time. */ } };
const patch = version => Number((/^0\.5\.(\d+)/.exec(version || '') || [])[1] ?? -1);
// The pages a version of the product has: the sound page from 0.5.0, the picture page from 0.5.1.
const forVersion = version => PAGES.filter(page => patch(version) >= (page.patch || 0));
const unseen = version => forVersion(version).filter(page => !seen(page));

export function showWhatsNew(cn, {openedBy = 'user', pages = PAGES} = {}) {
    const t = (en, zh) => cn ? zh : en;
    const css = document.getElementById('fv-whatsnew-css') || Object.assign(document.createElement('link'), {id: 'fv-whatsnew-css', rel: 'stylesheet', href: new URL('./whatsnew.css', import.meta.url).href});
    if (!css.isConnected) document.head.append(css);
    for (const sheet of ['editor.css', 'editor_expand.css', 'editor_adjust.css', 'editor_tracks.css', 'editor_menu.css', 'editor_voice.css', 'editor_record.css']) {
        const id = 'fv-whatsnew-' + sheet;
        if (!document.getElementById(id)) document.head.append(Object.assign(document.createElement('link'), {id, rel: 'stylesheet', href: new URL('./' + sheet, import.meta.url).href}));
    }
    const dialog = el('dialog', null, 'fv-whatsnew'); dialog.setAttribute('aria-label', t("What's new", '新功能'));
    const title = el('h2'), stageHost = el('div', null, 'fv-whatsnew-host'), foot = el('div', null, 'fv-whatsnew-foot');
    const close = el('button', t('Close', '关闭'), 'fv-whatsnew-close'); close.type = 'button';
    const next = el('button', t('Next page', '下一页'), 'fv-whatsnew-next'), open = el('button', t('Open Images', '打开图像视图'), 'fv-whatsnew-open fv-primary');
    next.type = open.type = 'button'; open.hidden = true;
    // The Images view opens from the generate node; without one the button is not offered.
    let host = null;
    const imageNode = () => (host?.app?.graph?._nodes || []).find(item => item.type === 'FreeVideoGenerate');
    import('../../scripts/app.js').then(module => { host = module; if (!closed && pages[index].kind === 'still') open.hidden = !imageNode(); }).catch(() => {});
    dialog.append(title, stageHost, foot);
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let stop = () => {}, closed = false, listNode = null, index = 0;
    // A page is either a still (a prompt, a picture and a list) or the editor demo, which plays once on arrival.
    function show(at) {
        stop(); stop = () => {}; listNode?.remove();
        index = at; const page = pages[at];
        title.textContent = page.title[cn ? 1 : 0]; next.hidden = at === pages.length - 1; open.hidden = page.kind !== 'still' || !imageNode();
        if (page.kind === 'still') {
            const figure = el('div', null, 'fv-whatsnew-still'), prompt = el('div', null, 'fv-whatsnew-prompt'), bar = el('i'), barHost = el('div', null, 'fv-whatsnew-bar'), row = el('div', null, 'fv-whatsnew-pictures');
            const image = el('img'); image.alt = ''; image.draggable = false; row.append(image); barHost.append(bar);
            figure.append(prompt, row, barHost); stageHost.replaceChildren(figure); stageHost.style.visibility = '';
            const timers = [], later = (ms, run) => timers.push(setTimeout(run, ms));
            stop = () => timers.forEach(clearTimeout);
            // The pictures play in order, round and round: the prompt is typed, a bar fills, the picture arrives and stays a while.
            const play = at => {
                const one = page.pictures[at % page.pictures.length], text = one.prompt[cn ? 1 : 0] || one.prompt[0];
                image.src = new URL('./' + one.src, import.meta.url).href; image.style.aspectRatio = one.ratio;
                const next = () => play(at + 1);
                if (reduce) { prompt.textContent = text; image.style.opacity = '1'; barHost.hidden = true; if (page.pictures.length > 1) later(4000, next); return; }
                image.style.opacity = '0'; prompt.textContent = ''; bar.style.transition = 'none'; bar.style.width = '0'; barHost.dataset.on = 'false';
                const step = Math.min(60, 1600 / text.length);
                for (let i = 1; i <= text.length; i++) later(200 + i * step, () => { prompt.textContent = text.slice(0, i); });
                later(1900, () => { barHost.dataset.on = 'true'; });
                later(1950, () => { bar.style.transition = 'width 2.2s linear'; bar.style.width = '100%'; });
                later(4200, () => { barHost.dataset.on = 'false'; image.style.opacity = '1'; });
                if (page.pictures.length > 1) { later(7200, () => { image.style.opacity = '0'; }); later(7700, next); }
            };
            play(0);
            listNode = el('ul', null, 'fv-whatsnew-list'); listNode.dataset.plain = ''; for (const item of page.list) listNode.append(el('li', item[cn ? 1 : 0]));
            dialog.insertBefore(listNode, foot); return;
        }
        const stage = createStage(cn, page);
        stageHost.replaceChildren(stage.element); listNode = stage.list; dialog.insertBefore(stage.list, foot);
        // The demo frame is as tall as its tallest state for the whole run, so the list under it and the dialog never move.
        stageHost.style.visibility = 'hidden';
        const sheets = [...document.querySelectorAll('link[id^="fv-whatsnew-"]')].filter(link => !link.sheet);
        Promise.all(sheets.map(link => new Promise(done => { link.addEventListener('load', done, {once: true}); link.addEventListener('error', done, {once: true}); }))).then(() => {
            if (closed || index !== at) return;
            stage.height(stage.tallest());
            stageHost.style.visibility = ''; stop = play(stage, page, reduce);
        });
    }
    foot.append(close, ...pages.length > 1 ? [next] : [], open); show(0);
    next.onclick = () => { show(index + 1); close.focus(); };
    close.onclick = () => dialog.close();
    open.onclick = async () => {
        try {
            const node = imageNode();
            if (!node) return;
            dialog.close(); (await import('./view_navigation.js')).openImage(node);
        } catch (error) { console.warn('FreeVideo image view:', error); }
    };
    dialog.addEventListener('keydown', event => event.stopPropagation());
    dialog.addEventListener('close', () => { closed = true; stop(); remember(pages); dialog.remove(); }, {once: true});
    document.body.append(dialog); dialog.showModal(); dialog.tabIndex = -1; dialog.focus();
    return () => { if (dialog.open) dialog.close(); };
}

// The first open after upgrading to 0.5 or 0.5.1: the pages not yet seen, once, and not while something is being generated.
export function maybeShowWhatsNew({cn, version, busy}) {
    const pages = unseen(version);
    if (!pages.length) return false;
    if (busy() || document.querySelector('dialog[open]:not(.fv-studio)')) return false;
    showWhatsNew(cn, {openedBy: 'upgrade', pages});
    return true;
}

// A page that holds settings from an earlier version is an upgrade; a first launch has none.
const usedBefore = () => {
    try {
        for (let index = 0; index < localStorage.length; index++) {
            const key = localStorage.key(index) || '';
            if (key !== KEY && key !== IMAGE_KEY && key !== 'freevideo.page-retry' && (key.startsWith('freevideo.') || key.startsWith('fv-'))) return true;
        }
    } catch { /* Unreadable storage: treat as an upgrade rather than hide the news. */ return true; }
    return false;
};

// Waits for the version answer and for an idle server, then shows the pages once.
export function startWhatsNew({api, cn, productVersion}) {
    if (!usedBefore()) { remember(PAGES); return; }
    let queued = 0, done = false;
    const status = event => { queued = event?.detail?.exec_info?.queue_remaining || 0; };
    try { api.addEventListener('status', status); } catch { /* A page without the event still waits for dialogs. */ }
    const timer = setInterval(() => {
        const version = productVersion();
        if (done || (version && !unseen(version).length)) { done = true; clearInterval(timer); return; }
        if (maybeShowWhatsNew({cn, version, busy: () => queued > 0})) { done = true; clearInterval(timer); }
    }, 5000);
}

export const hasWhatsNew = version => !!version && version.startsWith(SERIES + '.');
