// What's new in 0.5: the editor's own track view drawn with the editor's own classes and played as a short loop.
// Nothing is recorded; the picture is a placeholder, the tracks, clips, progress and waveforms are the real styles.
import {PAGES, LINES} from './whatsnew_pages.js';

const KEY = 'freevideo.whatsnew-seen';
const SERIES = '0.5';
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const svg = (tag, attrs = {}) => { const e = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
const ICON = {video: 'M2 4h16v12H2z M4 13l4-5 4 4 2-2 3 4', audio: 'M2 8h4l4-4v12l-4-4H2z M13 6c4 2 4 6 0 8 M13 9c1 1 1 1 0 2'};
const icon = kind => { const s = svg('svg', {viewBox: '0 0 20 20', 'aria-hidden': 'true'}); s.append(svg('path', {d: ICON[kind]})); return s; };

// A deterministic waveform in the editor's 200x40 box; shape decides how the sound reads. `duck` lists the spans where music sits lower.
function wave(seed, shape, rate = 1, duck = []) {
    let x = seed * 9301 + 49297, d = '';
    const random = () => (x = (x * 9301 + 49297) % 233280) / 233280;
    for (let i = 0; i < 100; i++) {
        const at = i / 100;
        let level = shape === 'speech' ? (Math.sin(at * 23 * rate + seed) > -.2 ? .25 + random() * .75 : .06) * (.5 + .5 * Math.sin(at * 7 * rate + 1))
            : shape === 'piano' ? .12 + .7 * Math.abs(Math.sin(at * Math.PI * 8.3 * rate)) ** 1.6
            : shape === 'guitar' ? .4 + .5 * Math.abs(Math.sin(at * Math.PI * 40)) ** 1.5 * (.7 + random() * .3)
            : shape === 'ambient' ? .2 + .3 * Math.abs(Math.sin(at * Math.PI * 4 * rate)) + random() * .08
            : shape === 'ambience' ? .12 + random() * .12 : .3 + random() * .7;
        if (duck.some(([from, to]) => at >= from && at <= to)) level *= .5;
        const h = Math.max(.5, Math.min(1, level) * 17);
        d += `M${(i + .5) * 2} ${20 - h}v${h * 2}`;
    }
    const s = svg('svg', {viewBox: '0 0 200 40', preserveAspectRatio: 'none', 'aria-hidden': 'true'});
    s.classList.add('fv-ed-waveform'); s.append(svg('path', {d})); return s;
}

const frame = fn => requestAnimationFrame(() => requestAnimationFrame(fn));

// One page's scene: the editor's track view, generation bar, lines panel and toolbar, changed only by the ops of its steps.
export function createStage(cn, page) {
    const t = pair => pair[cn ? 1 : 0];
    const root = el('div', null, 'fv-whatsnew-stage'); root.setAttribute('aria-hidden', 'true');
    const tools = el('div', null, 'fv-whatsnew-tools');
    const buttons = {};
    for (const id of page.toolbar || []) {
        const b = el('span', id === 'stems' ? t(['Separate tracks', '分离音轨']) : t(['Start recording', '开始录音']), 'fv-whatsnew-tool'); b.dataset.tool = id; b.hidden = true; buttons[id] = b; tools.append(b);
    }
    const recTime = el('span', null, 'fv-whatsnew-rectime'); tools.append(recTime);
    if (page.toolbar) root.append(tools);
    let ticker = 0, dragTicker = 0;
    const view = el('section', null, 'fv-ed-track-view'); view.dataset.trackSwitches = 'false';
    const heads = el('div', null, 'fv-ed-track-heads'), scroll = el('div', null, 'fv-ed-track-scroll'), content = el('div', null, 'fv-ed-track-content');
    const ruler = el('div', null, 'fv-ed-ruler');
    for (let s = 0; s <= 10; s++) {
        const tick = el('div', null, 'fv-ed-tick' + (s % 2 ? '' : ' fv-ed-tick-major')); tick.style.left = `${s * 10}%`;
        if (s % 2 === 0) tick.append(el('span', `0:${String(s).padStart(2, '0')}`));
        ruler.append(tick);
    }
    content.append(ruler);
    let top = 24;
    const rows = {}, names = {}, headNodes = {}, bodies = {};
    for (const row of page.rows) {
        const body = el('div', null, 'fv-ed-track-row'); body.dataset.track = row.id; body.style.top = `${top}px`; body.style.height = `${row.height}px`;
        const head = el('div', null, 'fv-ed-track-head'); head.dataset.track = row.id; head.style.top = `${top}px`; head.style.height = `${row.height}px`;
        const name = el('span', t(row.name)); head.append(icon(row.kind), name); heads.append(head); content.append(body);
        rows[row.id] = body; names[row.id] = {row, name}; headNodes[row.id] = head; bodies[row.id] = body; top += row.height + 6;
    }
    const clips = {};
    for (const spec of page.clips) {
        const c = el('div', null, 'fv-ed-clip'); c.dataset.name = spec.id; c.tabIndex = -1;
        const media = el('span', null, 'fv-ed-clip-media');
        if (spec.picture) media.classList.add('fv-whatsnew-picture');
        if (spec.wave) media.append(wave(...spec.wave));
        c.append(media, el('span', null, 'fv-ed-progress-fill'));
        rows[spec.row].append(c); clips[spec.id] = {spec, node: c};
    }
    const playhead = el('div', null, 'fv-whatsnew-playhead'), range = el('div', null, 'fv-whatsnew-range'), rangeText = el('span');
    range.append(rangeText); content.append(range, playhead);
    const menu = el('div', null, 'fv-whatsnew-menu');
    for (const item of page.menuItems || []) menu.append(el('span', t(item)));
    content.append(menu);
    view.style.height = `${top + 4}px`;
    scroll.append(content); view.append(heads, scroll); root.append(view);

    // Lines panel and generation bar share one fixed area so nothing below the tracks jumps.
    const below = el('div', null, 'fv-whatsnew-below');
    const panel = el('div', null, 'fv-whatsnew-lines'), panelHead = el('div', t(['Lines', '台词']), 'fv-whatsnew-lines-head');
    const SRC = page.lines || LINES;
    const lineNodes = SRC.map((_, i) => { const row = el('div', null, 'fv-whatsnew-line'); row.append(el('span', `0:0${[0, 3, 6][i]}`, 'fv-whatsnew-time'), el('span', null, 'fv-whatsnew-line-text')); return row; });
    const redubButton = el('span', null, 'fv-whatsnew-go');
    panel.append(panelHead, ...lineNodes, redubButton);
    const genbar = el('div', null, 'fv-whatsnew-genbar');
    const hint = el('span', null, 'fv-whatsnew-hint'), presets = el('div', null, 'fv-whatsnew-presets');
    const picker = (label, options) => { const g = el('div', null, 'fv-whatsnew-picker'); g.append(el('span', label)); for (const [key, text] of options) { const o = el('span', t(text), 'fv-whatsnew-opt'); o.dataset.key = key; g.append(o); } return g; };
    const styles = picker(t(['Style', '风格']), [['piano', ['Piano', '钢琴']], ['folk', ['Folk', '民谣']], ['ambient', ['Ambient', '氛围']]]);
    const moods = picker(t(['Mood', '情绪']), [['calm', ['Calm', '舒缓']], ['upbeat', ['Upbeat', '明快']], ['tense', ['Tense', '紧张']], ['warm', ['Warm', '温暖']], ['sad', ['Sad', '悲伤']]]);
    presets.append(styles, moods);
    const voiceRow = el('div', null, 'fv-whatsnew-picker'), voiceName = el('span', t(['Speaker 1', '说话人 1']), 'fv-whatsnew-opt'), listen = el('span', '▶', 'fv-whatsnew-opt');
    voiceName.dataset.on = 'false'; voiceRow.append(el('span', t(['Voice', '音色'])), voiceName, listen);
    const field = el('div', null, 'fv-whatsnew-field'), text = el('span', null, 'fv-whatsnew-text'), ph = el('span', null, 'fv-whatsnew-ph'), go = el('span', null, 'fv-whatsnew-go');
    const cancel = el('span', t(['Cancel', '取消']), 'fv-whatsnew-cancel');
    field.append(ph, text);
    genbar.append(presets, voiceRow, el('div', null, 'fv-whatsnew-row'));
    genbar.lastChild.append(field, go, hint, cancel);
    below.append(panel, genbar); root.append(below);
    let list = null;
    if (page.list) {
        list = el('ol', null, 'fv-whatsnew-list'); for (const item of page.list) list.append(el('li', t(item))); root.append(list);
    }

    let typing = 0, timers = [];
    const place = (c, at, to) => { c.node.style.left = `${at * 100}%`; c.node.style.width = `${(to - at) * 100}%`; };
    const bar = {}, linesState = {};
    const typeInto = (node, full, done) => {
        clearInterval(typing); const per = cn ? 70 : 35; let n = 0; node.textContent = '';
        typing = setInterval(() => { n++; node.textContent = full.slice(0, n); if (n >= full.length) { clearInterval(typing); done?.(); } }, per);
    };
    const paintLines = () => {
        panel.dataset.shown = String(!!linesState.shown);
        lineNodes.forEach((row, i) => {
            row.dataset.active = String(linesState.active === i);
            const node = row.lastChild, [full, old, fresh] = SRC[i].map(x => t(x));
            if (i !== 1) { node.textContent = full; return; }
            if (linesState.edit === 'strike') node.replaceChildren(...full.split(old).flatMap((p, k, a) => k < a.length - 1 ? [document.createTextNode(p), el('s', old)] : [document.createTextNode(p)]));
            else if (linesState.edit === 'typing') { const [head, tail] = full.split(old); node.replaceChildren(document.createTextNode(head), el('s', old), el('b', null, 'fv-whatsnew-new'), document.createTextNode(tail)); typeInto(node.querySelector('b'), fresh); }
            else if (linesState.edit === 'done') node.textContent = full.replace(old, fresh);
            else node.textContent = full;
        });
    };
    const paintBar = () => {
        const mode = bar.mode || 'none';
        genbar.dataset.mode = mode; genbar.hidden = mode === 'none' || mode === 'lines';
        presets.hidden = !bar.presets; voiceRow.hidden = !bar.voice; presets.dataset.busy = String(mode === 'running');
        for (const o of styles.querySelectorAll('.fv-whatsnew-opt')) o.dataset.on = String(o.dataset.key === bar.style);
        for (const o of moods.querySelectorAll('.fv-whatsnew-opt')) o.dataset.on = String(o.dataset.key === bar.mood);
        hint.textContent = mode === 'running' ? (bar.hint || []).map(t).join(' · ') : '';
        hint.hidden = mode !== 'running'; field.hidden = mode === 'running'; go.hidden = mode === 'running'; cancel.hidden = mode !== 'running';
        ph.textContent = bar.ph ? t(bar.ph) : ''; ph.hidden = !!text.textContent;
        go.textContent = bar.button ? t(bar.button) : '';
        redubButton.textContent = bar.button ? t(bar.button) : ''; redubButton.hidden = !linesState.shown || mode === 'running' || !bar.button;
    };
    const reset = () => {
        clearInterval(typing); clearInterval(ticker); clearInterval(dragTicker); timers.forEach(clearTimeout); timers = []; recTime.textContent = '';
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
        for (const b of Object.values(buttons)) { b.hidden = true; b.removeAttribute('data-press'); }
        if (buttons.record) buttons.record.textContent = t(['Start recording', '开始录音']);
        playhead.style.cssText = ''; playhead.dataset.on = 'false'; range.dataset.on = 'false'; range.style.cssText = ''; menu.dataset.on = 'false';
        text.textContent = ''; list?.querySelectorAll('li').forEach(li => li.removeAttribute('data-lit'));
        paintLines(); paintBar();
    };
    const set = ops => {
        if (ops.split) {
            const faders = [...Object.values(clips).map(c => c.node), ...Object.values(headNodes), ...Object.values(bodies)];
            faders.forEach(n => { n.style.transition = 'none'; }); frame(() => faders.forEach(n => { n.style.transition = ''; }));
            for (const c of Object.values(clips)) { if (c.spec.splits) c.node.dataset.on = 'true'; if (c.spec.id === 'mix') c.node.dataset.on = 'false'; }
            for (const id in names) if (names[id].row.late) { headNodes[id].dataset.on = 'true'; bodies[id].dataset.on = 'true'; }
            if (names.a1.row.dialogue) names.a1.name.textContent = t(names.a1.row.dialogue);
        }
        if (ops.rowShow) { headNodes[ops.rowShow].dataset.on = 'true'; bodies[ops.rowShow].dataset.on = 'true'; }
        for (const id of ops.hide || []) clips[id].node.dataset.on = 'false';
        for (const id of ops.show || []) clips[id].node.dataset.on = 'true';
        if ('select' in ops) for (const c of Object.values(clips)) { if (c.spec.id === ops.select) c.node.dataset.selected = 'true'; else c.node.removeAttribute('data-selected'); }
        if ('hl' in ops) for (const c of Object.values(clips)) { if (ops.hl.includes(c.spec.id)) c.node.dataset.hl = 'true'; else c.node.removeAttribute('data-hl'); }
        if (ops.lit && list) list.querySelectorAll('li').forEach((li, i) => { if (ops.lit.includes(i)) li.dataset.lit = 'true'; else li.removeAttribute('data-lit'); });
        if (ops.tb) {
            if (ops.tb.show) buttons[ops.tb.show].hidden = false;
            if ('press' in ops.tb) { for (const b of Object.values(buttons)) b.removeAttribute('data-press'); if (ops.tb.press) buttons[ops.tb.press].dataset.press = 'true'; }
            if ('rec' in ops.tb && buttons.record) { buttons.record.textContent = t(ops.tb.rec ? ['Stop', '停止'] : ['Start recording', '开始录音']); if (!ops.tb.rec) recTime.textContent = ''; }
            if (ops.tb.hide) for (const b of Object.values(buttons)) b.hidden = b.dataset.tool !== 'record' || b.hidden;
        }
        if (ops.menu) {
            menu.dataset.on = String(!!ops.menu.show);
            if (page.menuAt) { menu.style.left = page.menuAt[0]; menu.style.top = page.menuAt[1]; } else if (clips.mix) { menu.style.left = '38%'; menu.style.top = '34px'; }
            menu.querySelectorAll('span').forEach((s, i) => { s.dataset.hover = String(ops.menu.hover === i); });
        }
        if (ops.bar) {
            const wasType = ops.bar.type;
            Object.assign(bar, ops.bar);
            if ('text' in ops.bar) { text.textContent = ops.bar.text ? t(ops.bar.text) : ''; }
            if (wasType) { delete bar.type; typeInto(text, t(wasType), paintBar); }
        }
        if (ops.lines) { Object.assign(linesState, ops.lines); paintLines(); }
        paintBar();
        if (ops.run) {
            const {id, ms} = ops.run, node = clips[id].node, fill = node.querySelector('.fv-ed-progress-fill');
            node.dataset.state = 'running'; fill.style.transition = 'none'; node.style.setProperty('--fv-ed-progress', '0');
            frame(() => { fill.style.transition = `width ${ms}ms linear`; node.style.setProperty('--fv-ed-progress', '1'); });
        }
        if (ops.done) { const c = clips[ops.done].node; c.dataset.state = 'ready'; c.style.removeProperty('--fv-ed-progress'); }
        if (ops.reveal) {
            const {id, ms} = ops.reveal, node = clips[id].node;
            node.style.transition = 'none'; node.style.clipPath = 'inset(0 100% 0 0)';
            frame(() => { node.style.transition = `clip-path ${ms}ms var(--fv-ease)`; node.style.clipPath = 'inset(0 0 0 0)'; });
        }
        if (ops.flash) { const node = clips[ops.flash].node; node.dataset.flash = 'true'; timers.push(setTimeout(() => node.removeAttribute('data-flash'), 900)); }
        if (ops.play) {
            const [from, to, ms] = ops.play; playhead.dataset.on = 'true'; playhead.style.transition = 'none'; playhead.style.left = `${from * 100}%`;
            frame(() => { playhead.style.transition = `left ${ms}ms linear`; playhead.style.left = `${to * 100}%`; });
        }
        if ('drag' in ops) {
            if (!ops.drag) range.dataset.on = 'false';
            else {
                const {row, from, to, ms} = ops.drag; range.dataset.on = 'true'; rangeText.textContent = '';
                const started = performance.now(); clearInterval(dragTicker);
                dragTicker = setInterval(() => { const sec = Math.round(Math.min(1, (performance.now() - started) / ms) * (to - from) * 10); rangeText.textContent = sec >= 2 ? t([`${sec} s`, `${sec} 秒`]) : ''; }, 100);
                range.style.top = bodies[row].style.top; range.style.height = bodies[row].style.height; range.style.transition = 'none'; range.style.left = `${from * 100}%`; range.style.width = '0%';
                frame(() => { range.style.transition = `width ${ms}ms linear`; range.style.width = `${(to - from) * 100}%`; });
            }
        }
        if (ops.rec) {
            const {id, from, to, ms} = ops.rec, c = clips[id]; c.node.style.transition = 'none'; place(c, from, from + .01); c.node.dataset.on = 'true';
            frame(() => { c.node.style.transition = `width ${ms}ms linear`; place(c, from, to); });
            const started = performance.now(); clearInterval(ticker);
            ticker = setInterval(() => { const sec = Math.min(Math.floor((performance.now() - started) / 1000), Math.floor(ms / 1000)); recTime.textContent = `0:0${sec}`; }, 250);
        }
        if (ops.addAudio) { const b = tools; b.dataset.add = 'true'; }
    };
    reset();
    // Reduced motion shows the finished page: every step applied at once, typed text complete.
    const finish = () => {
        for (const [, ops] of page.steps) { const o = {...ops}; if (o.bar?.type) o.bar = {...o.bar, text: o.bar.type, type: null}; delete o.reveal; delete o.run; delete o.play; if (o.lines?.edit === 'typing') o.lines = {...o.lines, edit: 'done'}; set(o); }
        range.dataset.on = 'false';
    };
    return {element: root, set, reset, finish};
}

// Plays a page's steps in a loop; reduced motion shows the finished state once.
export function play(stage, page, reduce) {
    let timers = [], dead = false;
    const run = () => {
        if (dead) return;
        stage.reset();
        for (const [at, ops] of page.steps) timers.push(setTimeout(() => !dead && stage.set(ops), at));
        timers.push(setTimeout(run, page.length));
    };
    if (reduce) { stage.finish(); return () => {}; }
    run();
    return () => { dead = true; timers.forEach(clearTimeout); };
}

const seenSeries = () => { try { return localStorage.getItem(KEY) === SERIES; } catch { return false; } };
const remember = () => { try { localStorage.setItem(KEY, SERIES); } catch { /* Shown again next time. */ } };

export function showWhatsNew(cn, {openedBy = 'user'} = {}) {
    const t = (en, zh) => cn ? zh : en;
    const css = document.getElementById('fv-whatsnew-css') || Object.assign(document.createElement('link'), {id: 'fv-whatsnew-css', rel: 'stylesheet', href: new URL('./whatsnew.css', import.meta.url).href});
    if (!css.isConnected) document.head.append(css);
    for (const sheet of ['editor_tracks.css']) {
        const id = 'fv-whatsnew-' + sheet;
        if (!document.getElementById(id)) document.head.append(Object.assign(document.createElement('link'), {id, rel: 'stylesheet', href: new URL('./' + sheet, import.meta.url).href}));
    }
    const dialog = el('dialog', null, 'fv-whatsnew'); dialog.setAttribute('aria-label', t("What's new", '新功能'));
    const title = el('h2'), stageHost = el('div', null, 'fv-whatsnew-host'), foot = el('div', null, 'fv-whatsnew-foot');
    const close = el('button', t('Close', '关闭'), 'fv-whatsnew-close'); close.type = 'button';
    foot.append(close); dialog.append(title, stageHost, foot);
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const page = PAGES[0], stage = createStage(cn, page);
    title.textContent = page.title[cn ? 1 : 0]; stageHost.replaceChildren(stage.element);
    const stop = play(stage, page, reduce);
    close.onclick = () => dialog.close();
    dialog.addEventListener('keydown', event => event.stopPropagation());
    dialog.addEventListener('close', () => { stop(); remember(); dialog.remove(); }, {once: true});
    document.body.append(dialog); dialog.showModal(); dialog.tabIndex = -1; dialog.focus();
    return () => { if (dialog.open) dialog.close(); };
}

// The first open after upgrading to 0.5: once, and not while something is being generated.
export function maybeShowWhatsNew({cn, version, busy}) {
    if (seenSeries() || !version || !version.startsWith(SERIES + '.')) return false;
    if (busy() || document.querySelector('dialog[open]:not(.fv-studio)')) return false;
    showWhatsNew(cn, {openedBy: 'upgrade'});
    return true;
}

// A page that holds settings from an earlier version is an upgrade; a first launch has none.
const usedBefore = () => {
    try {
        for (let index = 0; index < localStorage.length; index++) {
            const key = localStorage.key(index) || '';
            if (key !== KEY && key !== 'freevideo.page-retry' && (key.startsWith('freevideo.') || key.startsWith('fv-'))) return true;
        }
    } catch { /* Unreadable storage: treat as an upgrade rather than hide the news. */ return true; }
    return false;
};

// Waits for the version answer and for an idle server, then shows the page once.
export function startWhatsNew({api, cn, productVersion}) {
    if (seenSeries()) return;
    if (!usedBefore()) { remember(); return; }
    let queued = 0, done = false;
    const status = event => { queued = event?.detail?.exec_info?.queue_remaining || 0; };
    try { api.addEventListener('status', status); } catch { /* A page without the event still waits for dialogs. */ }
    const timer = setInterval(() => {
        if (done || seenSeries()) { done = true; clearInterval(timer); return; }
        if (maybeShowWhatsNew({cn, version: productVersion(), busy: () => queued > 0})) { done = true; clearInterval(timer); }
    }, 5000);
}

export const hasWhatsNew = version => !!version && version.startsWith(SERIES + '.');
