// Compare two videos on the stage: side by side, or one over the other with a divider to drag. Both play together.
// The video on screen is A; B is the next older one, and any card picked in the history rail becomes B.
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const svg = d => `<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
export const COMPARE_ICON = '<rect x="1.8" y="3" width="5.4" height="10" rx="1.4"/><rect x="8.8" y="3" width="5.4" height="10" rx="1.4"/>';
const PLAY = '<path d="M5 3.5v9l7.5-4.5Z"/>', PAUSE = '<path d="M5.5 3.5v9M10.5 3.5v9"/>', CLOSE = '<path d="m4.5 4.5 7 7M11.5 4.5l-7 7"/>';
function tip(node, name, use = '', key = '') {
    node.dataset.fvTipName = name; if (use) node.dataset.fvTipUse = use; if (key) node.dataset.fvTipKey = key;
    node.title = [name, use].filter(Boolean).join(' · ') + (key ? ` (${key})` : '');
}

// source(row) → {src, poster}; describe(row) → short label; onChange(active) when it opens or closes.
export function createCompare({t, host, source, describe, onChange = () => {}}) {
    const element = el('div', 'fvs-compare'); element.hidden = true; element.setAttribute('role', 'group');
    element.setAttribute('aria-label', t('Compare two videos', '对比两条视频'));
    const panes = el('div', 'fvs-compare-panes');
    const make = side => {
        const pane = el('div', `fvs-compare-pane fvs-compare-${side}`), video = el('video');
        video.muted = side === 'b'; video.loop = true; video.playsInline = true; video.preload = 'auto';
        const tag = el('span', 'fvs-compare-tag'); pane.append(video, tag);
        return {pane, video, tag};
    };
    const a = make('a'), b = make('b');
    const divider = el('div', 'fvs-compare-divider'); divider.setAttribute('role', 'slider');
    divider.setAttribute('aria-label', t('Divider between the two videos', '两条视频的分界')); divider.tabIndex = 0;
    divider.setAttribute('aria-valuemin', '0'); divider.setAttribute('aria-valuemax', '100');
    tip(divider, t('Divider', '分界'), t('Drag to see more of either video', '拖动来多看一边'), '← →');
    panes.append(a.pane, b.pane, divider);
    const bar = el('div', 'fvs-compare-bar');
    const modes = el('div', 'fvs-compare-modes'); modes.setAttribute('role', 'radiogroup'); modes.setAttribute('aria-label', t('Layout', '方式'));
    const modeButton = (id, label, use) => {
        const button = el('button', 'fvs-compare-mode', label); button.type = 'button'; button.setAttribute('role', 'radio'); button.dataset.mode = id;
        tip(button, label, use); button.onclick = () => setMode(id); modes.append(button); return button;
    };
    modeButton('side', t('Side by side', '并排'), t('Both videos next to each other', '两条视频左右并排'));
    modeButton('slider', t('Slider', '滑块'), t('One over the other, drag the divider', '叠在一起，拖动分界线'));
    const play = el('button', 'fvs-icon-button'); play.type = 'button';
    const hint = el('span', 'fvs-compare-hint', t('Ctrl-click or check two videos in the history to compare them', '在右侧作品栏按住 Ctrl 点或勾选两条视频来对比'));
    const close = el('button', 'fvs-icon-button'); close.type = 'button'; close.innerHTML = svg(CLOSE);
    close.setAttribute('aria-label', t('Close compare', '关闭对比')); tip(close, t('Close compare', '关闭对比'), t('Back to the single video', '回到单条视频'), 'Esc');
    bar.append(modes, hint, play, close);
    element.append(panes, bar);
    host.append(element);
    let mode = 'side', split = .5, rows = [null, null], dragging = null;
    const paintPlay = () => {
        const paused = a.video.paused;
        play.innerHTML = svg(paused ? PLAY : PAUSE);
        const label = paused ? t('Play both', '同时播放') : t('Pause both', '同时暂停');
        play.setAttribute('aria-label', label); tip(play, label, t('The two videos keep the same time', '两条视频保持同一时间'), 'Space');
    };
    function setMode(next) {
        mode = next; element.dataset.mode = mode;
        for (const button of modes.children) button.setAttribute('aria-checked', String(button.dataset.mode === mode));
        paintSplit();
    }
    function paintSplit() {
        element.style.setProperty('--fvs-split', `${(split * 100).toFixed(2)}%`);
        divider.setAttribute('aria-valuenow', String(Math.round(split * 100)));
    }
    function load(side, row) {
        const target = side === 'a' ? a : b; rows[side === 'a' ? 0 : 1] = row;
        if (!row) { target.video.removeAttribute('src'); target.tag.textContent = ''; return; }
        const {src, poster} = source(row);
        target.video.poster = poster || ''; target.video.src = src; const about = describe(row); target.tag.textContent = about ? `${side.toUpperCase()} · ${about}` : side.toUpperCase();
        target.video.load();
    }
    // A leads: B follows its play state and time.
    const follow = () => { if (Math.abs((b.video.currentTime || 0) - (a.video.currentTime || 0)) > .15) try { b.video.currentTime = a.video.currentTime; } catch {} };
    a.video.addEventListener('play', () => { b.video.play().catch(() => {}); paintPlay(); });
    a.video.addEventListener('pause', () => { b.video.pause(); paintPlay(); });
    a.video.addEventListener('seeked', follow); a.video.addEventListener('timeupdate', follow);
    play.onclick = () => { if (a.video.paused) a.video.play().catch(() => {}); else a.video.pause(); };
    const toSplit = event => { const box = panes.getBoundingClientRect(); split = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)); paintSplit(); };
    panes.addEventListener('pointerdown', event => {
        if (mode !== 'slider' || event.button !== 0) return;
        dragging = event.pointerId; panes.setPointerCapture(dragging); toSplit(event); event.preventDefault();
    });
    panes.addEventListener('pointermove', event => { if (event.pointerId === dragging) toSplit(event); });
    const stop = event => { if (event.pointerId === dragging) { dragging = null; } };
    panes.addEventListener('pointerup', stop); panes.addEventListener('pointercancel', stop);
    divider.addEventListener('keydown', event => {
        const step = event.shiftKey ? .1 : .02;
        if (event.key === 'ArrowLeft') split = Math.max(0, split - step); else if (event.key === 'ArrowRight') split = Math.min(1, split + step); else return;
        event.preventDefault(); paintSplit();
    });
    close.onclick = () => api.close();
    const api = {
        element, get active() { return !element.hidden; },
        open(first, second) {
            load('a', first); load('b', second); setMode(mode); paintPlay();
            element.hidden = false; onChange(true); play.focus({preventScroll: true});
        },
        setB(row) { if (!element.hidden && row) { load('b', row); if (!a.video.paused) b.video.play().catch(() => {}); follow(); } },
        rows: () => rows.slice(),
        close() {
            if (element.hidden) return false;
            a.video.pause(); b.video.pause(); element.hidden = true; onChange(false);
            return true;
        },
        dispose() { api.close(); load('a', null); load('b', null); element.remove(); },
    };
    setMode('side');
    return api;
}
