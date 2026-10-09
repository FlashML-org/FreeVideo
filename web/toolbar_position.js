// Lets a floating panel be dragged anywhere on the page, the way a window is,
// and puts it back where it was on the next visit. The place lives in
// ComfyUI's user settings, so it survives a restart and follows the user to
// another browser; localStorage keeps a copy for frontends without settings.
import { app } from '../../scripts/app.js';

const SETTING = 'FreeVideo.Toolbar.Position';
const LOCAL = 'freevideo.toolbar-position';
// A press that moves no more than THRESHOLD is a click. A drop within EDGE of
// a side sticks to that side; within HOME of the default place, it goes home.
const THRESHOLD = 4, EDGE = 16, HOME = 12, MARGIN = 8;
const RESET = 'default';
const SIDES = ['left', 'right', 'center'];
const valid = value => !!value && typeof value === 'object' && SIDES.includes(value.side)
    && Number.isFinite(value.ratio) && Math.abs(value.ratio) <= 1 && Number.isFinite(value.top) && value.top >= 0;
const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

// ComfyUI's user settings: the settings store, the older settings dialog, or none.
function settings() {
    try {
        const store = app?.extensionManager?.setting;
        if (typeof store?.get === 'function' && typeof store?.set === 'function') return {get: id => store.get(id), set: (id, value) => store.set(id, value)};
        const dialog = app?.ui?.settings;
        if (typeof dialog?.getSettingValue === 'function' && typeof dialog?.setSettingValue === 'function') {
            return {get: id => dialog.getSettingValue(id), set: (id, value) => dialog.setSettingValue(id, value)};
        }
    } catch { /* No settings: localStorage only. */ }
    return null;
}

// What the user saved, from ComfyUI's settings first; null means the default place.
function readSaved() {
    let stored;
    try { stored = settings()?.get(SETTING); } catch { /* localStorage below. */ }
    if (stored !== undefined && stored !== null) return valid(stored) ? stored : null;
    try {
        const local = JSON.parse(localStorage.getItem(LOCAL) || 'null');
        return valid(local) ? local : null;
    } catch { return null; }
}

function save(value) {
    try {
        if (value) localStorage.setItem(LOCAL, JSON.stringify(value)); else localStorage.removeItem(LOCAL);
    } catch { /* Private browsing: ComfyUI's settings still keep it. */ }
    // The default is saved too, so another browser's older copy does not come back.
    try { Promise.resolve(settings()?.set(SETTING, value || RESET)).catch(() => {}); } catch { /* localStorage has it. */ }
}

// ComfyUI's workflow tabs and side toolbar: the panel keeps clear of them.
const CHROME = ['.workflow-tabs-container', '.side-tool-bar-container'];

// Where a panel of this size may go: inside the window with a margin, below
// ComfyUI's workflow tabs and clear of its side toolbar while it fits there.
function area(width, height) {
    let left = MARGIN, right = innerWidth - MARGIN, top = MARGIN;
    const bottom = innerHeight - MARGIN;
    const tabs = document.querySelector(CHROME[0])?.getBoundingClientRect();
    if (tabs && tabs.height > 0 && tabs.top < 4 && tabs.width > innerWidth / 2 && bottom - (tabs.bottom + MARGIN) >= height) top = tabs.bottom + MARGIN;
    const side = document.querySelector(CHROME[1])?.getBoundingClientRect();
    if (side && side.width > 0 && side.height > innerHeight / 2) {
        if (side.left < innerWidth / 2 && right - (side.right + MARGIN) >= width) left = side.right + MARGIN;
        else if (side.left >= innerWidth / 2 && side.left - MARGIN - left >= width) right = side.left - MARGIN;
    }
    return {left, right, top, bottom, maxX: Math.max(left, right - width), maxY: Math.max(top, bottom - height)};
}

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

// ComfyUI's controls that float over the canvas in a corner: the run bar when
// it is not docked, the canvas tools and the minimap. They are not edges of
// the area: a place that covers one moves up or down to the nearest free spot.
const FLOATING = ['.actionbar', '.graph-canvas-panel .p-buttongroup', '.minimap-main-container'];
const GAP = 8;
function clearOf(left, top, width, height, room) {
    const blocks = [];
    for (const selector of FLOATING) {
        for (const element of document.querySelectorAll(selector)) {
            const r = element.getBoundingClientRect();
            if (r.width > 0 && r.height > 0 && r.left < left + width && r.right > left) blocks.push(r);
        }
    }
    if (!blocks.some(r => top < r.bottom && top + height > r.top)) return top;
    const free = y => blocks.every(r => y + height + GAP <= r.top || y >= r.bottom + GAP);
    const spots = blocks.flatMap(r => [r.top - GAP - height, r.bottom + GAP])
        .filter(y => y >= room.top && y <= room.maxY && free(y)).sort((a, b) => Math.abs(a - top) - Math.abs(b - top));
    return spots.length ? Math.round(spots[0]) : top;
}

// Across, the place is kept relative to the nearest side or the middle, as a
// share of the window's width; down, as its distance from the top.
function describe(left, top, width, height) {
    const room = area(width, height);
    const fromLeft = left - room.left, fromRight = room.right - (left + width);
    const offCenter = left + width / 2 - (room.left + room.right) / 2;
    const side = fromLeft < EDGE ? 'left' : fromRight < EDGE ? 'right'
        : Math.abs(offCenter) <= Math.min(fromLeft, fromRight) ? 'center' : fromLeft <= fromRight ? 'left' : 'right';
    const distance = side === 'left' ? Math.max(0, fromLeft) : side === 'right' ? Math.max(0, fromRight) : offCenter;
    return {side, ratio: fromLeft < EDGE || fromRight < EDGE ? 0 : distance / innerWidth, top: Math.round(top)};
}

function resolve(saved, width, height) {
    const room = area(width, height), offset = saved.ratio * innerWidth;
    const left = saved.side === 'left' ? room.left + offset : saved.side === 'right' ? room.right - width - offset
        : (room.left + room.right) / 2 + offset - width / 2;
    return {left: Math.round(clamp(left, room.left, room.maxX)), top: Math.round(clamp(saved.top, room.top, room.maxY))};
}

const interactive = target => !!target?.closest?.('button, a, input, select, textarea, [role="button"]');

// panel: a position:fixed element whose stylesheet gives its default place.
// Pressing anywhere on it and moving drags it; a press that does not move stays
// a click. Double-clicking its background puts it back in its default place.
export function makeMovable(panel) {
    let saved = readSaved(), press = null, frame = 0, suppressClick = false;
    const PLACED = ['left', 'top', 'transform', 'right', 'bottom'];

    const setPlace = ({left, top}) => Object.assign(panel.style, {left: `${left}px`, top: `${top}px`, transform: 'none', right: 'auto', bottom: 'auto'});
    const clearPlace = () => { for (const key of PLACED) panel.style.removeProperty(key); };
    // The stylesheet's place, below ComfyUI's run bar where that covers it (a narrow window).
    function placeHome() {
        clearPlace();
        const box = panel.getBoundingClientRect();
        if (!box.width) return;
        const top = clearOf(box.left, box.top, box.width, box.height, area(box.width, box.height));
        if (Math.abs(top - box.top) >= 1) panel.style.top = `${top}px`;
    }
    // The saved place as it fits the window now, clear of ComfyUI's controls.
    function spot(width, height) {
        const at = resolve(saved, width, height);
        return {left: at.left, top: clearOf(at.left, at.top, width, height, area(width, height))};
    }
    // Where home is for the panel as it is now.
    function home() {
        const kept = PLACED.map(key => panel.style.getPropertyValue(key));
        placeHome();
        const box = panel.getBoundingClientRect();
        clearPlace();
        PLACED.forEach((key, index) => { if (kept[index]) panel.style.setProperty(key, kept[index]); });
        return box;
    }
    // Moves to the new place in one short motion from where it is now.
    function glide(apply, duration) {
        const before = panel.getBoundingClientRect();
        apply();
        const after = panel.getBoundingClientRect();
        if (reduced() || typeof panel.animate !== 'function' || (Math.abs(before.left - after.left) < 1 && Math.abs(before.top - after.top) < 1)) return;
        panel.animate([{translate: `${before.left - after.left}px ${before.top - after.top}px`}, {translate: '0 0'}], {duration, easing: 'ease-out'});
    }

    function place() {
        if (!panel.isConnected || panel.hidden || press?.dragging) return;
        if (!saved) return placeHome();
        const box = panel.getBoundingClientRect();
        if (box.width) setPlace(spot(box.width, box.height));
    }

    function goHome(duration) {
        saved = null;
        glide(placeHome, duration);
        save(null);
    }

    function follow() {
        frame = 0;
        if (!press?.dragging) return;
        const room = area(press.width, press.height);
        const dx = clamp(press.left + press.dx, room.left, room.maxX) - press.left;
        const dy = clamp(press.top + press.dy, room.top, room.maxY) - press.top;
        panel.style.translate = `${dx}px ${dy}px`;
    }

    // Moves are followed on the window from the press on: a quick first move
    // can leave the panel before it counts as a drag. Capturing the pointer only
    // once it is a drag keeps a plain press a click on the button under it.
    const watch = on => {
        for (const [type, handler] of [['pointermove', move], ['pointerup', release], ['pointercancel', release]]) {
            (on ? addEventListener : removeEventListener)(type, handler, true);
        }
    };
    panel.addEventListener('pointerdown', event => {
        if (event.button !== 0 || !event.isPrimary) return;
        press = {id: event.pointerId, x: event.clientX, y: event.clientY, dx: 0, dy: 0, dragging: false};
        watch(true);
    });
    function move(event) {
        if (!press || event.pointerId !== press.id) return;
        press.dx = event.clientX - press.x; press.dy = event.clientY - press.y;
        if (!press.dragging) {
            if (Math.hypot(press.dx, press.dy) <= THRESHOLD) return;
            const box = panel.getBoundingClientRect();
            Object.assign(press, {dragging: true, left: box.left, top: box.top, width: box.width, height: box.height});
            setPlace({left: box.left, top: box.top});
            try { panel.setPointerCapture(event.pointerId); } catch { /* Still follows while over the panel. */ }
            panel.classList.add('fv-dragging');
            document.documentElement.classList.add('fv-toolbar-dragging');
        }
        event.preventDefault();
        if (!frame) frame = requestAnimationFrame(follow);
    }
    function release(event) {
        if (!press || event.pointerId !== press.id) return;
        const drag = press;
        press = null;
        watch(false);
        if (!drag.dragging) return;
        if (frame) { cancelAnimationFrame(frame); frame = 0; }
        panel.classList.remove('fv-dragging');
        document.documentElement.classList.remove('fv-toolbar-dragging');
        // The press ended a drag, not a click on whatever is under it.
        suppressClick = event.type === 'pointerup';
        setTimeout(() => { suppressClick = false; }, 0);
        const room = area(drag.width, drag.height);
        const left = clamp(drag.left + drag.dx, room.left, room.maxX), top = clamp(drag.top + drag.dy, room.top, room.maxY);
        panel.style.removeProperty('translate');
        setPlace({left, top});
        const start = home();
        if (Math.abs(left - start.left) < HOME && Math.abs(top - start.top) < HOME) return goHome(120);
        saved = describe(left, clearOf(left, top, drag.width, drag.height, room), drag.width, drag.height);
        glide(() => setPlace(spot(drag.width, drag.height)), 120);
        save(saved);
    }
    panel.addEventListener('click', event => {
        if (!suppressClick) return;
        suppressClick = false;
        event.preventDefault(); event.stopPropagation();
    }, true);
    panel.addEventListener('dblclick', event => { if (!interactive(event.target)) goHome(180); });

    addEventListener('resize', place);
    // Showing it again, a closed tip or a new notice changes its size; ComfyUI
    // draws its tabs and side toolbar after extensions start, and they can change.
    if (typeof ResizeObserver === 'function') {
        const sizes = new ResizeObserver(() => place()), watched = new Set();
        sizes.observe(panel);
        const hook = () => {
            for (const selector of CHROME) {
                const element = document.querySelector(selector);
                if (element && !watched.has(element)) { watched.add(element); sizes.observe(element); }
            }
            return watched.size >= CHROME.length;
        };
        if (!hook()) {
            let tries = 0;
            const timer = setInterval(() => { if (hook() || ++tries >= 60) clearInterval(timer); }, 1000);
        }
    }
    // The run bar shows up after extensions start and can be moved; the minimap
    // opens and closes. A panel they come to cover moves aside, and back after.
    setInterval(() => { if (!document.hidden) glide(place, 120); }, 2000);
    place();
    return {place, reset: () => goHome(0)};
}
