// What's new in 0.5: the editor's own track view drawn with the editor's own classes and played as a short loop.
// Nothing is recorded; the picture is a placeholder, the tracks, clips, progress and waveforms are the real styles.
const KEY = 'freevideo.whatsnew-seen';
const SERIES = '0.5';
const PAGES = [
    {id: 'story', title: ['Edit picture and sound, track by track', '画面和声音，逐轨编辑'], steps: [[0, {split: 0, music: 'old', voice: 'old'}], [1800, {split: 1}],
        [3000, {music: 'running', progress: .3}], [4200, {progress: .8}], [5000, {music: 'new', progress: 0}],
        [6600, {voice: 'running', progress: .5}], [7600, {progress: .9}], [8300, {voice: 'new', progress: 0}]], length: 11000},
    {id: 'stems', title: ['Split the sound into three tracks', '把声音拆成三条轨道'], howto: 'separate-tracks',
        steps: [[0, {split: 0, music: 'old', voice: 'old'}], [1400, {split: 1}]], length: 5200},
    {id: 'music', title: ['Replace the music', '换一段配乐'], howto: 'replace-music',
        steps: [[0, {split: 1, music: 'old', voice: 'old'}], [1200, {music: 'running', progress: .35}], [2000, {progress: .8}], [2700, {music: 'new', progress: 0}]], length: 5200},
    {id: 'voice', title: ['Regenerate a line', '重新生成一句台词'], howto: 'replace-voice',
        steps: [[0, {split: 1, music: 'old', voice: 'old'}], [1200, {voice: 'running', progress: .5}], [2200, {progress: .9}], [2800, {voice: 'new', progress: 0}]], length: 5200},
];
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const svg = (tag, attrs = {}) => { const e = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
const ICON = {video: 'M2 4h16v12H2z M4 13l4-5 4 4 2-2 3 4', audio: 'M2 8h4l4-4v12l-4-4H2z M13 6c4 2 4 6 0 8 M13 9c1 1 1 1 0 2'};
const icon = kind => { const s = svg('svg', {viewBox: '0 0 20 20', 'aria-hidden': 'true'}); s.append(svg('path', {d: ICON[kind]})); return s; };

// A deterministic waveform in the editor's 200x40 box; shape decides how the sound reads.
function wave(seed, shape) {
    let x = seed * 9301 + 49297, d = '';
    const random = () => (x = (x * 9301 + 49297) % 233280) / 233280;
    for (let i = 0; i < 100; i++) {
        const at = i / 100;
        const level = shape === 'speech' ? (Math.sin(at * 23) > -.2 ? .25 + random() * .75 : .06) * (.5 + .5 * Math.sin(at * 7 + 1))
            : shape === 'music' ? .35 + .4 * Math.sin(at * 9 + seed) ** 2 + random() * .15
            : shape === 'ambience' ? .12 + random() * .12 : .3 + random() * .7;
        const h = Math.max(.5, Math.min(1, level) * 17);
        d += `M${(i + .5) * 2} ${20 - h}v${h * 2}`;
    }
    const s = svg('svg', {viewBox: '0 0 200 40', preserveAspectRatio: 'none', 'aria-hidden': 'true'});
    s.classList.add('fv-ed-waveform'); s.append(svg('path', {d})); return s;
}

const ROWS = [['v1', 'video', 64], ['a1', 'audio', 40], ['a2', 'audio', 40], ['a3', 'audio', 40]];

export function createStage(cn) {
    const t = (en, zh) => cn ? zh : en;
    const root = el('div', null, 'fv-whatsnew-stage'); root.setAttribute('aria-hidden', 'true');
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
    const rows = {}, heading = {};
    for (const [id, kind, height] of ROWS) {
        const row = el('div', null, 'fv-ed-track-row'); row.dataset.track = id; row.style.top = `${top}px`; row.style.height = `${height}px`;
        const head = el('div', null, 'fv-ed-track-head'); head.dataset.track = id; head.style.top = `${top}px`; head.style.height = `${height}px`;
        const name = el('span'); head.append(icon(kind), name); heads.append(head); content.append(row);
        rows[id] = row; heading[id] = name; top += height + 6;
    }
    const clip = (row, name, node) => { const c = el('div', null, 'fv-ed-clip'); c.dataset.name = name; c.style.left = '0'; c.style.width = '100%';
        const media = el('span', null, 'fv-ed-clip-media'); if (node) media.append(node); c.append(media);
        const fill = el('span', null, 'fv-ed-progress-fill'); c.append(fill); row.append(c); return c; };
    const video = clip(rows.v1, 'video'); video.querySelector('.fv-ed-clip-media').classList.add('fv-whatsnew-picture');
    const mixed = clip(rows.a1, 'mixed', wave(3, 'mixed'));
    const voice = [clip(rows.a1, 'voice-old', wave(5, 'speech')), clip(rows.a1, 'voice-new', wave(11, 'speech'))];
    const music = [clip(rows.a2, 'music-old', wave(7, 'music')), clip(rows.a2, 'music-new', wave(13, 'music'))];
    clip(rows.a3, 'ambience', wave(9, 'ambience'));
    for (const c of [video, mixed, ...voice, ...music]) c.tabIndex = -1;
    scroll.append(content); view.append(heads, scroll); root.append(view);
    const names = () => {
        const split = root.dataset.split === '1';
        heading.v1.textContent = t('Video', '画面');
        heading.a1.textContent = split ? t('Dialogue', '人声') : t('Audio', '声音');
        heading.a2.textContent = t('Music', '音乐'); heading.a3.textContent = t('Ambience', '环境声');
    };
    const set = state => {
        for (const [k, v] of Object.entries(state)) { if (k === 'progress') root.style.setProperty('--fv-ed-progress', String(v)); else root.dataset[k] = String(v); }
        for (const [pair, key] of [[voice, 'voice'], [music, 'music']]) {
            pair[0].dataset.state = root.dataset[key] === 'running' ? 'running' : 'ready';
            if (root.dataset[key] === 'running') pair[0].style.setProperty('--fv-ed-progress', root.style.getPropertyValue('--fv-ed-progress') || '0');
        }
        names();
    };
    set({split: 0, music: 'old', voice: 'old', progress: 0});
    return {element: root, set};
}

// Plays a page's steps in a loop; reduced motion shows the finished state once.
export function play(stage, page, reduce) {
    let timers = [], dead = false;
    const run = () => {
        if (dead) return;
        for (const [at, state] of page.steps) timers.push(setTimeout(() => !dead && stage.set(state), at));
        timers.push(setTimeout(run, page.length));
    };
    if (reduce) { for (const [, state] of page.steps) stage.set(state); return () => {}; }
    run();
    return () => { dead = true; timers.forEach(clearTimeout); };
}

const seenSeries = () => { try { return localStorage.getItem(KEY) === SERIES; } catch { return false; } };
const remember = () => { try { localStorage.setItem(KEY, SERIES); } catch { /* Shown again next time. */ } };

export function showWhatsNew(cn, {openedBy = 'user', start} = {}) {
    const t = (en, zh) => cn ? zh : en;
    const css = document.getElementById('fv-whatsnew-css') || Object.assign(document.createElement('link'), {id: 'fv-whatsnew-css', rel: 'stylesheet', href: new URL('./whatsnew.css', import.meta.url).href});
    if (!css.isConnected) document.head.append(css);
    for (const sheet of ['editor_tracks.css']) {
        const id = 'fv-whatsnew-' + sheet;
        if (!document.getElementById(id)) document.head.append(Object.assign(document.createElement('link'), {id, rel: 'stylesheet', href: new URL('./' + sheet, import.meta.url).href}));
    }
    const dialog = el('dialog', null, 'fv-whatsnew'); dialog.setAttribute('aria-label', t("What's new", '新功能'));
    const title = el('h2'), close = el('button', '×', 'fv-whatsnew-close'), stageHost = el('div', null, 'fv-whatsnew-host'), foot = el('div', null, 'fv-whatsnew-foot');
    const dots = el('div', null, 'fv-whatsnew-dots'), take = el('button', t('Take me there', '带我去'), 'fv-whatsnew-take'), next = el('button', null, 'fv-whatsnew-next');
    take.type = next.type = close.type = 'button'; take.hidden = true; close.setAttribute('aria-label', t('Close', '关闭'));
    foot.append(dots, take, next); dialog.append(close, title, stageHost, foot);
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let index = 0, stop = () => {}, token = 0;
    const dotButtons = PAGES.map((page, i) => { const b = el('button', null, 'fv-whatsnew-dot'); b.type = 'button'; b.setAttribute('aria-label', page.title[cn ? 1 : 0]); b.onclick = () => show(i); dots.append(b); return b; });
    function show(i) {
        stop(); index = i; const page = PAGES[i], mine = ++token;
        title.textContent = page.title[cn ? 1 : 0];
        const stage = createStage(cn); stageHost.replaceChildren(stage.element);
        stop = play(stage, page, reduce);
        dotButtons.forEach((b, n) => b.setAttribute('aria-current', String(n === i)));
        next.textContent = i === PAGES.length - 1 ? t('Done', '完成') : t('Next', '下一页');
        take.hidden = true;
        // A page that has no guided tour yet shows no button.
        if (page.howto) Promise.resolve(window.freevideoHowTo?.available?.(page.howto)).then(ok => { if (ok && token === mine) take.hidden = false; }).catch(() => {});
    }
    close.onclick = () => dialog.close();
    next.onclick = () => index === PAGES.length - 1 ? dialog.close() : show(index + 1);
    take.onclick = () => { const id = PAGES[index].howto; dialog.close(); void window.freevideoHowTo?.start?.(id); };
    dialog.addEventListener('keydown', event => {
        event.stopPropagation();
        if (event.key === 'ArrowRight' && index < PAGES.length - 1) show(index + 1);
        if (event.key === 'ArrowLeft' && index > 0) show(index - 1);
    });
    dialog.addEventListener('close', () => { stop(); remember(); dialog.remove(); }, {once: true});
    document.body.append(dialog); dialog.showModal(); show(start ?? 0); dialog.tabIndex = -1; dialog.focus();
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
