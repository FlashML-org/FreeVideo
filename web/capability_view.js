import { referenceItems } from './prompt_references.js';
import { effortFor, effortsFrom, stepCacheOf, upscaleOf } from './sampling_effort.js';

const PRESETS = [.5, .75, .98, 1.5, 2];
const COLUMN_SPACING = 'equal'; // 'equal' or 'width'
const FADE = .17;
const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
const positive = n => Number.isFinite(n) && n > 0;
const el = (tag, cls, text) => { const e = document.createElement(tag); e.className = cls; if (text != null) e.textContent = text; return e; };
const value = (node, name) => node?.widgets?.find(w => w.name === name)?.value;
const linked = (node, name) => node?.inputs?.some(i => i.name === name && i.link != null);
const upstream = (node, name) => node?.graph?.getNodeById(node.graph.links?.[node.inputs?.find(i => i.name === name)?.link]?.origin_id);

// Monotone cubic interpolation keeps both the surface and its edge continuous.
export function curve(xs, ys) {
    if (xs.length === 1) return () => ys[0];
    const h = xs.slice(1).map((x, i) => x - xs[i]);
    const d = h.map((v, i) => (ys[i + 1] - ys[i]) / v);
    const m = [d[0]];
    for (let i = 1; i < xs.length - 1; i++) {
        const a = 2 * h[i] + h[i - 1], b = h[i] + 2 * h[i - 1];
        m[i] = d[i - 1] * d[i] <= 0 ? 0 : (a + b) / (a / d[i - 1] + b / d[i]);
    }
    m.push(d.at(-1));
    return x => {
        // Clamp outside known points; never invent a hover estimate.
        if (x <= xs[0]) return ys[0];
        if (x >= xs.at(-1)) return ys.at(-1);
        let i = 0; while (x > xs[i + 1]) i++;
        const t = (x - xs[i]) / h[i], t2 = t * t, t3 = t2 * t;
        return (2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h[i] * m[i]
            + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h[i] * m[i + 1];
    };
}

export function aboutTime(seconds, t) {
    if (seconds < 55) { const n = Math.max(10, Math.round(seconds / 10) * 10); return t(`About ${n} s`, `约 ${n} 秒`); }
    const minutes = Math.round(seconds / 60);
    if (minutes < 100) return t(`About ${minutes} min`, `约 ${minutes} 分钟`);
    const hours = Math.round(seconds / 1800) / 2;
    return t(`About ${hours} h`, `约 ${hours} 小时`);
}

// Send material facts only. An incomplete reference list uses the server's
// documented task fallback, rather than pretending the known subset is all of it.
export function capabilityContext(node, state) {
    if (linked(node, 'conditioning') || linked(node, 'width') || linked(node, 'height')
            || ['base_steps', 'refine_steps', 'two_pass', 'step_cache', 'upscale_factor'].some(name => linked(node, name))) return null;
    let items, assets;
    try { items = referenceItems(node); assets = JSON.parse(value(upstream(node, 'media'), 'assets') || '[]'); }
    catch { return null; }
    const refs = items.filter(r => !r.role), first = items.some(r => r.role === 'first'), last = items.some(r => r.role === 'last');
    const rows = refs.map(item => {
        const source = assets[item.assetIndex] || {}, row = {kind: item.kind};
        if (item.kind !== 'audio') { row.width = source.width; row.height = source.height; }
        if (item.kind !== 'image') row.seconds = source.seconds;
        if (item.kind === 'video' && item.sound !== false && positive(source.audio_seconds)) row.audio_seconds = source.audio_seconds;
        return row;
    });
    const audio = refs.some((r, i) => r.kind === 'audio' || positive(rows[i].audio_seconds));
    const visual = refs.some(r => r.kind !== 'audio');
    const task = audio ? visual ? 'ref2va_av' : 'ref2va_audio' : visual ? 'ref2va'
        : first && last ? 'fl2va' : first ? 'i2va' : last ? 'l2va' : 't2va';
    const lora = upstream(node, 'loras');
    let adapters = false;
    try {
        if (lora?.widgets?.some(w => w.name === 'adapters')) adapters = JSON.parse(value(lora, 'adapters') || '[]')
            .some(r => r.enabled !== false && Number(r.strength ?? 1) !== 0);
        else if (lora) adapters = Boolean(value(lora, 'lora') && value(lora, 'lora') !== 'None' && Number(value(lora, 'strength') ?? 1) !== 0);
    } catch { return null; }
    const query = new URLSearchParams({task, adapters: adapters ? '1' : '0'});
    if (state.custom) { query.set('width', value(node, 'width')); query.set('height', value(node, 'height')); }
    else { if (!positive(state.ratio)) return null; query.set('aspect', `${state.ratio}:1`); }
    const complete = rows.length && rows.every(r => (r.kind === 'audio' || [r.width, r.height].every(n => Number.isInteger(n) && n > 0 && n <= 65536))
        && (r.kind === 'image' || positive(r.seconds)));
    if (complete) query.set('references', JSON.stringify(rows));
    return query.toString();
}

// Shared recognition includes legacy Max and the Mac-only legacy High.
const gridTier = (grid, steps, twoPass, refine, stepCache, upscale = 2) => {
    const level = effortFor(steps, twoPass, refine, stepCache, effortsFrom(grid), upscale);
    return grid.tiers?.[level?.name.toLowerCase()];
};

export function capabilitySurface(grid, state) {
    if (grid?.version !== 1 || grid.status !== 'ready' || typeof grid.revision !== 'string' || !grid.machine?.system
            || /darwin|mac|apple/i.test(grid.machine.system)
            || !Array.isArray(grid.sizes) || !grid.sizes.length || !Array.isArray(grid.lengths)) return null;
    const tier = gridTier(grid, state.steps, state.twoPass, state.refine, state.stepCache, state.upscale);
    if (!tier || !Array.isArray(tier.estimate_seconds) || !Array.isArray(tier.max_seconds)) return null;
    const lengths = grid.lengths.map(l => l.seconds);
    if (!lengths.length || lengths.some((s, i) => !Number.isInteger(s) || s < 2 || (i && s <= lengths[i - 1]))) return null;
    const columns = grid.sizes.map((size, i) => {
        if (!positive(size.pixels) || [size.width, size.height].some(n => !Number.isInteger(n) || n < 256 || n > 4096 || n % 32)) return null;
        const row = tier.estimate_seconds[i], max = tier.max_seconds[i];
        if (!Array.isArray(row) || row.length !== lengths.length) return null;
        const points = lengths.flatMap((s, j) => positive(row[j]) && s <= max ? [{seconds: s, estimate: row[j]}] : []);
        return {...size, points, max: points.at(-1)?.seconds || 0};
    });
    if (columns.some(c => !c) || !columns.some(c => c.points.length)) return null;
    // Only the five chart targets are shown, even if the API includes 1.00 MP.
    const sizes = state.custom ? columns : PRESETS.map(pixels => {
        const width = Math.round(Math.sqrt(pixels * 1024 ** 2 * state.ratio) / 32) * 32;
        const height = Math.round(Math.sqrt(pixels * 1024 ** 2 / state.ratio) / 32) * 32;
        const source = columns.find(c => c.width === width && c.height === height);
        return source ? {...source, pixels} : null;
    });
    if (sizes.some(s => !s)) return null;
    const widths = sizes.map(s => s.width), span = widths.at(-1) - widths[0];
    const xs = sizes.map((s, i) => COLUMN_SPACING === 'width' && span
        ? .075 + .85 * (s.width - widths[0]) / span : (i + .5) / sizes.length);
    const top = Math.max(...sizes.map(s => s.max)) > 15 ? 30 : 15;
    const edge = curve(xs, sizes.map(s => s.max));
    const curves = sizes.map(s => s.points.length ? curve(s.points.map(p => p.seconds), s.points.map(p => Math.log(p.estimate))) : null);
    const available = sizes.map((s, i) => s.max ? i : -1).filter(i => i >= 0);
    function snap(x, y) {
        if (x < 0 || x > 1 || y < 0 || y > 1) return null;
        const seconds = top * (1 - y);
        if (seconds >= edge(x) + FADE * top) return null;
        const i = xs.reduce((best, v, j) => Math.abs(v - x) < Math.abs(xs[best] - x) ? j : best, 0);
        if (!sizes[i].points.length) return null;
        const points = sizes[i].points.filter(p => p.seconds <= top);
        const point = points.reduce((best, p) => Math.abs(p.seconds - seconds) < Math.abs(best.seconds - seconds) ? p : best);
        return {...point, width: sizes[i].width, height: sizes[i].height, pixels: sizes[i].pixels, size: i};
    }
    function nearest(x, y) {
        if (![x, y].every(Number.isFinite) || x < 0 || x > 1 || y < 0 || y > 1) return null;
        let best = null, distance = Infinity;
        sizes.forEach((size, i) => {
            for (const point of size.points) {
                if (point.seconds > top) continue;
                const d = (xs[i] - x) ** 2 + (1 - point.seconds / top - y) ** 2;
                if (d < distance) {
                    distance = d;
                    best = {...point, width: size.width, height: size.height, pixels: size.pixels, size: i};
                }
            }
        });
        return best;
    }
    return {sizes, xs, top, edge, snap, nearest, at: seconds => curve(available.map(i => xs[i]), available.map(i => curves[i](seconds)))};
}

// Iris's blue ramp in OKLab, interpolated in linear light; transparent beyond
// the edge so the heatmap dissolves into whichever panel contains it.
const stops = [[0, .845, .085, 240], [.30, .760, .115, 245], [.64, .605, .140, 252], [1, .440, .128, 261]];
const lab = stops.map(([, l, c, h]) => [l, c * Math.cos(h * Math.PI / 180), c * Math.sin(h * Math.PI / 180)]);
const ramps = [0, 1, 2].map(k => curve(stops.map(s => s[0]), lab.map(v => v[k])));
const gamma = c => { c = clamp(c, 0, 1); return c <= .0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - .055; };
const colors = Array.from({length: 1025}, (_, i) => {
    const [L, a, b] = ramps.map(f => f(i / 1024));
    const l = (L + .3963377774 * a + .2158037573 * b) ** 3;
    const m = (L - .1055613458 * a - .0638541728 * b) ** 3;
    const s = (L - .0894841775 * a - 1.291485548 * b) ** 3;
    return [4.0767416621 * l - 3.3077115913 * m + .2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - .3413193965 * s,
        -.0041960863 * l - .7034186147 * m + 1.707614701 * s].map(c => gamma(c) * 255);
});
export function surfacePixels(surface, width, height) {
    const pixels = new Uint8ClampedArray(width * height * 4), edge = Array.from({length: width}, (_, x) => surface.edge((x + .5) / width));
    for (let y = 0; y < height; y++) {
        const seconds = surface.top * (1 - (y + .5) / height), across = surface.at(Math.max(2, seconds));
        for (let x = 0; x < width; x++) {
            const v = clamp((across((x + .5) / width) - Math.log(90)) / Math.log(320), 0, 1) * 1024;
            const i = Math.min(1023, Math.floor(v)), mix = v - i;
            const f = clamp((seconds - edge[x]) / (FADE * surface.top), 0, 1);
            // An entirely unavailable column stays clear all the way to zero.
            const a = (1 - f ** 3 * (f * (6 * f - 15) + 10)) * clamp(edge[x] / 2, 0, 1);
            const offset = (y * width + x) * 4;
            const noise = (((x * 1973 + y * 9277) % 101) / 100 - .5) * .75;
            for (let k = 0; k < 3; k++) pixels[offset + k] = colors[i][k] + (colors[i + 1][k] - colors[i][k]) * mix + noise;
            pixels[offset + 3] = 255 * a;
        }
    }
    return pixels;
}

let serial = 0;
export function createCapabilityView({node, api, t, getState, onPick, onEstimate, onGrid}) {
    if (!document.querySelector('link[data-fv-capability]')) {
        const css = el('link', ''); css.rel = 'stylesheet'; css.dataset.fvCapability = '';
        css.href = new URL('./capability_view.css', import.meta.url).href; document.head.append(css);
    }
    const element = el('div', 'fv-range'); element.hidden = true;
    const entry = el('button', 'fv-range-entry'); entry.type = 'button'; entry.dataset.fvGuide = 'range';
    entry.append(el('span', '', t('Available sizes and lengths', '可生成范围')));
    const longest = el('span', 'fv-range-longest'), arrow = el('i', 'fv-range-arrow'); arrow.setAttribute('aria-hidden', 'true'); entry.append(longest, arrow);
    const chart = el('div', 'fv-range-chart'), yAxis = el('div', 'fv-range-y'), plot = el('div', 'fv-range-plot'), xAxis = el('div', 'fv-range-x');
    chart.id = `fv-range-${++serial}`; entry.setAttribute('aria-controls', chart.id);
    const canvas = el('canvas', ''), previous = el('canvas', 'fv-range-previous');
    const now = el('i', 'fv-range-now'), mark = el('i', 'fv-range-mark'), tip = el('div', 'fv-range-tip'), time = el('strong', ''), configuration = el('span', '');
    for (const item of [canvas, previous, now, mark]) item.setAttribute('aria-hidden', 'true');
    tip.setAttribute('role', 'status'); tip.append(time, configuration); plot.append(canvas, previous, now, mark, tip);
    plot.tabIndex = 0; plot.setAttribute('role', 'group'); plot.setAttribute('aria-label', t('Available sizes and lengths', '可生成范围'));
    const notice = el('p', 'fv-muted'); notice.hidden = true; notice.setAttribute('role', 'status');
    chart.append(yAxis, plot, xAxis); element.append(entry, chart, notice);
    let opened = false;
    try { opened = localStorage.getItem('freevideo.range-open') === '1'; } catch {}
    const fold = () => { chart.hidden = !opened; entry.setAttribute('aria-expanded', String(opened)); };
    fold();
    let disposed = false, query = '', grid = null, surface = null, hover = null, controller = null, retry = null, request = 0, drawn = '', pointer = null;
    // While the engine is still working out the table the entry stays in place, greyed, saying so;
    // after three minutes of that it is treated like no table at all.
    let estimating = false, estimatingSince = 0;
    const ESTIMATE_LIMIT_MS = 3 * 60 * 1000;
    const position = (item, x, y) => { item.style.transform = `translate(${x}px,${y}px)`; };
    const hideHover = () => { hover = null; plot.classList.remove('is-hover'); highlight(); };
    function show(point, x, y) {
        hover = point; position(mark, x, y);
        time.textContent = aboutTime(point.estimate, t);
        const duration = point.seconds < 30 && point.seconds === surface.sizes[point.size].max
            ? t(`up to ${point.seconds} s`, `单次最长 ${point.seconds} 秒`) : t(`${point.seconds} s`, `${point.seconds} 秒`);
        configuration.textContent = `${point.width} × ${point.height} · ${duration}`;
        plot.classList.add('is-hover');
        highlight();
        const tw = tip.offsetWidth, th = tip.offsetHeight;
        const tx = clamp(x - tw / 2, 0, Math.max(0, plot.clientWidth - tw));
        const ty = y - th - 16 >= 0 ? y - th - 16 : Math.min(plot.clientHeight - th, y + 16);
        position(tip, tx, Math.max(0, ty));
    }
    function currentSize() {
        const matches = s => s.width === Number(value(node, 'width')) && s.height === Number(value(node, 'height'));
        const state = getState();
        // The chart marker and limit share the native column for old 1.00 MP sizes.
        if (!state.custom && matches({width: Math.round(Math.sqrt(1024 ** 2 * state.ratio) / 32) * 32,
            height: Math.round(Math.sqrt(1024 ** 2 / state.ratio) / 32) * 32})) return surface.sizes.findIndex(s => s.pixels === .98);
        const target = Number(node.properties?.freevideo_pixels);
        const exact = surface.sizes.findIndex(s => matches(s) && s.pixels === target);
        return exact >= 0 ? exact : surface.sizes.findIndex(matches);
    }
    function highlight() {
        const i = surface ? currentSize() : -1;
        [...xAxis.children].forEach((label, j) => {
            label.classList.toggle('is-current', j === i);
            label.classList.toggle('is-hover', j === hover?.size);
        });
    }
    function current() {
        if (!surface) return;
        const i = currentSize();
        const seconds = Number(value(node, 'seconds'));
        const point = i >= 0 && surface.sizes[i].points.find(p => p.seconds === Math.round(seconds));
        now.hidden = !point || seconds > surface.top || seconds > surface.sizes[i].max;
        if (!now.hidden) position(now, surface.xs[i] * plot.clientWidth, (1 - seconds / surface.top) * plot.clientHeight);
        const max = i >= 0 ? Math.min(30, surface.sizes[i].max) : 0;
        longest.textContent = !max ? '' : max < 30 ? t(`Up to ${max} s`, `单次最长 ${max} 秒`) : t('Single run: under 30 s', '单次不到 30 秒');
        plot.setAttribute('aria-disabled', String(getState().disabled));
        highlight();
    }
    function paint(animate = false) {
        if (!surface || !opened || element.hidden || !plot.clientWidth) return;
        const dpr = Math.max(1, window.devicePixelRatio || 1), width = Math.round(plot.clientWidth * dpr), height = Math.round(plot.clientHeight * dpr);
        const key = `${grid.revision}:${getState().steps}:${getState().twoPass}:${stepCacheOf(getState().stepCache)}:${upscaleOf(getState().upscale)}:${width}:${height}:${query}`;
        if (key === drawn) { current(); return; }
        drawn = key;
        if (animate && canvas.width && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
            previous.width = canvas.width; previous.height = canvas.height;
            previous.getContext('2d').drawImage(canvas, 0, 0);
            previous.getAnimations().forEach(a => a.cancel());
            previous.animate([{opacity: 1}, {opacity: 0}], {duration: 200});
        }
        canvas.width = width; canvas.height = height;
        const ctx = canvas.getContext('2d'), img = ctx.createImageData(width, height);
        img.data.set(surfacePixels(surface, width, height)); ctx.putImageData(img, 0, 0);
        yAxis.replaceChildren(); xAxis.replaceChildren();
        for (let s = 0; s <= surface.top; s += surface.top / 3) {
            const label = el('span', '', t(`${s} s`, `${s} 秒`)); label.style.top = `${(1 - s / surface.top) * 100}%`; yAxis.append(label);
        }
        surface.sizes.forEach((size, i) => {
            const label = el('span', 'fv-range-size', `${size.pixels.toFixed(2)} MP`);
            label.style.left = `${surface.xs[i] * 100}%`;
            label.append(el('small', '', `${size.width} × ${size.height}`)); xAxis.append(label);
        });
        xAxis.classList.toggle('is-narrow', plot.clientWidth / surface.sizes.length < 60);
        current(); hideHover();
    }
    function applyGrid(animate = false) {
        if (grid?.version === 1 && grid.status === 'ready') onGrid?.(grid);
        surface = capabilitySurface(grid, getState()); element.hidden = !surface && !estimating;
        entry.disabled = !surface;
        onEstimate?.();
        if (!surface) {
            hideHover();
            if (estimating) { chart.hidden = true; entry.setAttribute('aria-expanded', 'false'); longest.textContent = t('Estimating…', '正在估算…'); }
            return;
        }
        fold(); current(); paint(animate);
    }
    async function fetchGrid() {
        if (!query || disposed) return;
        clearTimeout(retry); controller?.abort(); controller = new AbortController();
        const signal = controller.signal, key = query, id = ++request;
        try {
            const reply = await api.fetchApi('/freevideo/capabilities?' + key, {signal});
            if (!reply.ok) throw new Error('unavailable');
            const next = await reply.json();
            if (disposed || signal.aborted || query !== key || request !== id) return;
            if (next.status === 'computing') {
                if (!estimating) { estimating = true; estimatingSince = Date.now(); }
                if (Date.now() - estimatingSince > ESTIMATE_LIMIT_MS) estimating = false;
                grid = null; applyGrid();
                if (estimating) retry = setTimeout(fetchGrid, clamp(Number(next.retry_after_seconds) || 1, .1, 30) * 1000);
                return;
            }
            estimating = false; grid = next; applyGrid(true);
        } catch {
            if (!disposed && !signal.aborted && query === key && request === id) { estimating = false; grid = null; applyGrid(); }
        }
    }
    function update() {
        if (disposed) return;
        const next = capabilityContext(node, getState()) || '';
        if (next !== query) {
            query = next; controller?.abort(); clearTimeout(retry); grid = null; drawn = ''; estimating = false; applyGrid();
            if (query) fetchGrid();
        } else applyGrid(true);
    }
    entry.onclick = () => {
        opened = !opened; fold();
        try { localStorage.setItem('freevideo.range-open', opened ? '1' : '0'); } catch {}
        if (opened) {
            paint(); fetchGrid();
            chart.scrollIntoView({block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
        }
        else hideHover();
    };
    function point(event) {
        if (!surface) return;
        const box = plot.getBoundingClientRect(), x = clamp(event.clientX - box.left, 0, box.width), y = clamp(event.clientY - box.top, 0, box.height);
        if (!box.width || !box.height) return;
        const snapped = surface.snap(x / box.width, y / box.height);
        if (snapped) show(snapped, x, y); else hideHover();
        return {x: x / box.width, y: y / box.height};
    }
    const pick = position => {
        if (!surface || getState().disabled) return;
        let selected = hover, adjusted = Boolean(selected?.adjusted);
        if (!selected) {
            const x = position?.x ?? surface.xs[Math.max(0, currentSize())];
            const y = position?.y ?? clamp(1 - Number(value(node, 'seconds')) / surface.top, 0, 1);
            selected = surface.snap(x, y);
            if (!selected) { selected = surface.nearest(x, y); adjusted = true; }
        }
        if (!selected) return;
        onPick(selected); current();
        notice.hidden = !adjusted;
        notice.textContent = adjusted ? t('Moved to the nearest available spot.', '已挪到最近的可用位置。') : '';
    };
    plot.onpointermove = point;
    plot.onpointerdown = event => {
        if (event.button !== 0) return;
        point(event); pointer = event.pointerId; plot.setPointerCapture(pointer);
    };
    plot.onpointerup = event => {
        if (event.pointerId !== pointer) return;
        const position = point(event); pointer = null; plot.releasePointerCapture(event.pointerId); pick(position);
    };
    plot.onpointercancel = () => { pointer = null; hideHover(); };
    plot.onlostpointercapture = () => { pointer = null; };
    plot.onpointerleave = event => { if (pointer === null && event.pointerType !== 'touch') hideHover(); };
    plot.onblur = hideHover;
    plot.onkeydown = event => {
        if (!surface || event.ctrlKey || event.metaKey || event.altKey) return;
        if (['Enter', ' '].includes(event.key)) { event.preventDefault(); pick(); return; }
        const delta = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1]}[event.key];
        if (!delta) return;
        event.preventDefault();
        const base = hover || {size: Math.max(0, currentSize()), seconds: Math.round(Number(value(node, 'seconds')))};
        let i = clamp(base.size + delta[0], 0, surface.sizes.length - 1);
        while (!surface.sizes[i].points.length && delta[0] && i + delta[0] >= 0 && i + delta[0] < surface.sizes.length) i += delta[0];
        const available = surface.sizes[i].points.length > 0;
        const seconds = clamp(base.seconds + delta[1], 2, available ? Math.min(surface.top, surface.sizes[i].max) : surface.top);
        const x = surface.xs[i], y = 1 - seconds / surface.top, snapped = surface.snap(x, y);
        const selected = snapped || surface.nearest(x, y);
        if (selected) show({...selected, adjusted: !snapped}, surface.xs[selected.size] * plot.clientWidth,
            (1 - selected.seconds / surface.top) * plot.clientHeight);
    };
    function changed(event) {
        if (event.detail?.revision === grid?.revision) return;
        const params = new URLSearchParams(query), detail = event.detail || {};
        if (!query || detail.task !== params.get('task') || Boolean(detail.adapters) !== (params.get('adapters') === '1')) return;
        if ((params.has('width') && (String(detail.width) !== params.get('width') || String(detail.height) !== params.get('height')))
            || (!params.has('width') && (detail.width != null || detail.aspect !== params.get('aspect')))) return;
        if (JSON.stringify(detail.references ?? null) !== JSON.stringify(params.has('references') ? JSON.parse(params.get('references')) : null)) return;
        fetchGrid();
    }
    const observer = new ResizeObserver(() => paint()); observer.observe(plot);
    api.addEventListener('freevideo_capabilities', changed);
    const outside = event => { if (!plot.contains(event.target)) hideHover(); };
    node && window.addEventListener('pointerdown', outside);
    return {element, update, estimate(steps, twoPass, refine, stepCache = null, upscale = 2) {
        if (!grid || !surface) return '';
        const tier = gridTier(grid, steps, twoPass, refine, stepCache, upscale);
        const i = grid.sizes.findIndex(s => s.width === Number(value(node, 'width')) && s.height === Number(value(node, 'height')));
        const j = grid.lengths.findIndex(l => l.seconds === Number(value(node, 'seconds')));
        const seconds = tier?.estimate_seconds?.[i]?.[j];
        if (!positive(seconds)) return '';
        // The suffix stays in one piece so a narrow line moves it whole to the next line.
        return aboutTime(seconds, t) + (grid.machine?.compile_owed === true ? t(' · \u2060includes\u00a0first-time\u00a0setup', ' · \u2060含\u2060首\u2060次\u2060准\u2060备') : '');
    }, warning(steps, twoPass, refine, stepCache = null, upscale = 2) {
        if (!grid || !surface) return '';
        const tier = gridTier(grid, steps, twoPass, refine, stepCache, upscale);
        const i = grid.sizes.findIndex(s => s.width === Number(value(node, 'width')) && s.height === Number(value(node, 'height')));
        const j = grid.lengths.findIndex(l => l.seconds === Number(value(node, 'seconds')));
        const code = tier?.plan?.[i]?.[j];
        if (!(code > 0) || !positive(tier.estimate_seconds?.[i]?.[j])) return '';
        const why = tier.slow_by?.[i]?.[j];
        const head = why === 'gpu_memory' ? t('Not enough graphics memory, so this will be slow.', '显存不足，这次会很慢。')
            : why === 'memory' ? t('Not enough RAM, so this will be slow.', '内存不足，这次会很慢。') : t('This will be slow.', '这次会很慢。');
        // One remedy only, and only when it really removes the slowdown.
        const levels = effortsFrom(grid), at = levels.findIndex(l => grid.tiers?.[l.name.toLowerCase()] === tier);
        const lower = at > 0 && levels.slice(0, at).some(l => { const row = grid.tiers?.[l.name.toLowerCase()]; return row?.plan?.[i]?.[j] === 0 && positive(row.estimate_seconds?.[i]?.[j]); });
        if (lower) return head + t(' ', '') + t('Choose a lower Quality level.', '请把质量降低一档。');
        const smaller = tier.plan.slice(0, i).some((row, k) => row[j] === 0 && positive(tier.estimate_seconds?.[k]?.[j]) && grid.sizes[k].pixels < grid.sizes[i].pixels);
        return smaller ? head + t(' ', '') + t('Choose a smaller size.', '请缩小尺寸。') : head;
    }, dispose() {
        disposed = true; controller?.abort(); clearTimeout(retry); observer.disconnect();
        api.removeEventListener('freevideo_capabilities', changed); window.removeEventListener('pointerdown', outside);
    }};
}
