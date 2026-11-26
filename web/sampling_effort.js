// One editor control. The queued request remains an immutable graph snapshot.
// Each level is a complete plan (freevideo_engine/tiers.py): Light and Medium are two-pass 8 + 3,
// told apart by the learned upscale factor (Medium starts from a larger first pass); High and Max
// sample 16 and 20 steps once, both with the guarded step cache. `upscale` is 2 for every single-pass level.
const frozen = rows => Object.freeze(rows.map(row => Object.freeze({...row})));
// Steps as words: "8 steps", "1 step", "8 + 3 steps" (the second pass's steps after the plus). One sentence each, so a
// language whose nouns change with the number writes plural forms for them.
export const stepsWords = (t, steps, twoPass = false, refine = 3) => twoPass
    ? t('{steps} + {refine} steps', '{steps} + {refine} 步', {steps, refine})
    : steps === 1 ? t('1 step', '1 步') : t('{steps} steps', '{steps} 步', {steps});
export const SAMPLING_EFFORTS = frozen([
    {name: 'Light', zh: '轻量', steps: 8, twoPass: true, stepCache: null, upscale: 2, color: '#3dd6b5'},
    {name: 'Medium', zh: '标准', steps: 8, twoPass: true, stepCache: null, upscale: 1.5, color: '#4ea8ff'},
    {name: 'High', zh: '精细', steps: 16, twoPass: false, stepCache: 'easycache-av', upscale: 2, color: '#9d7bff'},
    {name: 'Max', zh: '极致', steps: 20, twoPass: false, stepCache: 'easycache-av', upscale: 2, color: '#ffa94d'},
]);
// The Fast level, listed before Light where the planner admits it (grid.fast_mode). The engine takes its steps from the
// `fast_mode` input, so it is never matched from step counts. Its one line of description is here.
export const FAST_EFFORT = Object.freeze({name: 'Fast', zh: '极速', fast: true, steps: 2, twoPass: false, stepCache: null, upscale: 2, color: '#35d0e6'});
export const FAST_OPTION_TEXT = Object.freeze(['Faster; fine detail may flicker', '更快，画面细节可能闪烁']);
// What a capability grid offers of it (capabilities.fast_mode, listed by the planner, a Mac included).
export const fastOfferOf = grid => grid?.fast_mode?.listed === true ? grid.fast_mode : null;
// Mac runs the same plans; retain platform identity for its legacy High alias.
const macLevels = new WeakSet();
// A Mac's levels with Fast in front keep that identity.
export const withFastLevel = (levels, offer) => {
    if (!offer) return levels;
    const listed = frozen([FAST_EFFORT, ...levels]);
    if (macLevels.has(levels)) macLevels.add(listed);
    return listed;
};
export const SAMPLING_EFFORTS_MAC = frozen(SAMPLING_EFFORTS);
macLevels.add(SAMPLING_EFFORTS_MAC);
// Earlier Max sampled 20 plain steps, on every platform.
export const legacyMaxEffort = (steps, twoPass, stepCache = null, levels = SAMPLING_EFFORTS) =>
    steps === 20 && !twoPass && stepCacheOf(stepCache) === null ? levels.find(tier => tier.name === 'Max') : undefined;
// Mac results made before the cache came: High was 16 steps without one.
export const legacyMacEffort = (steps, twoPass, stepCache = null, levels = SAMPLING_EFFORTS_MAC) =>
    steps === 16 && !twoPass && stepCacheOf(stepCache) === null ? levels.find(tier => tier.name === 'High') : undefined;

export const effortName = (t, tier) => t(tier.name, tier.zh);
export const STEP_CACHES = Object.freeze([null, 'easycache-av', 'fbcache']);
// The two-pass learned upscale factors (two_pass.SCALES); the `upscale_factor` widget holds them as text.
export const UPSCALE_FACTORS = Object.freeze([2, 1.5, 1.2]);

// The cache a `step_cache` value names; 'none', '' and a missing value sample in full.
export const stepCacheOf = value => (typeof value === 'string' && value && value !== 'none' ? value : null);
// The factor an `upscale_factor` value (or a result's sampling_plan.scale) names; anything else, or none, is 2.
export function upscaleOf(value) {
    const factor = typeof value === 'number' ? value : typeof value === 'string' && value.trim() ? Number(value) : NaN;
    return UPSCALE_FACTORS.includes(factor) ? factor : 2;
}

// The levels of the machine a capability grid describes: its own table when the
// grid lists one, else the Mac table on a Mac, else the default table.
export function effortsFrom(grid) {
    const base = grid?.machine?.system === 'Darwin' ? SAMPLING_EFFORTS_MAC : SAMPLING_EFFORTS;
    const tiers = grid?.tiers;
    if (!tiers || typeof tiers !== 'object') return base;
    const levels = frozen(base.map(tier => {
        const row = tiers[tier.name.toLowerCase()];
        if (!row || !Number.isInteger(row.steps) || typeof row.two_pass !== 'boolean') return tier;
        return {...tier, steps: row.steps, twoPass: row.two_pass,
            stepCache: 'step_cache' in row ? stepCacheOf(row.step_cache) : tier.stepCache,
            upscale: !row.two_pass ? 2 : 'upscale' in row ? upscaleOf(row.upscale) : tier.upscale};
    }));
    if (base === SAMPLING_EFFORTS_MAC) macLevels.add(levels);
    return levels;
}

// The level a plan matches, or undefined for custom steps; the upscale factor tells the
// two-pass levels apart (a single pass has none).
export function effortFor(steps, twoPass, refine, stepCache = null, levels = SAMPLING_EFFORTS, upscale = 2) {
    const cache = stepCacheOf(stepCache), factor = twoPass ? upscaleOf(upscale) : 2;
    return levels.find(tier => !tier.fast && tier.steps === steps && tier.twoPass === twoPass && (!twoPass || refine === 3)
        && tier.stepCache === cache && tier.upscale === factor)
        || legacyMaxEffort(steps, twoPass, cache, levels)
        || (macLevels.has(levels) ? legacyMacEffort(steps, twoPass, cache, levels) : undefined);
}

// The level a result's sampling_plan names. Results made with the earlier 8 + 2 default count as
// Light; `scale` (absent when 2) tells Light from Medium; an earlier Mac result of 16 steps is High.
export function resultEffort(plan) {
    const p = plan || {}, steps = p.base_steps, refine = p.enabled ? p.refine_steps : 0;
    return effortFor(steps, !!p.enabled, p.enabled && steps === 8 && refine === 2 ? 3 : refine, p.step_cache ?? null, SAMPLING_EFFORTS, p.scale ?? 2)
        || legacyMacEffort(steps, !!p.enabled, p.step_cache ?? null);
}

// The steps and factor switching two-pass leaves. Off from an 8-step first pass samples the
// Max plan including its cache; on from a single-pass level gives Light without a cache. Typed steps stay.
export function passSwitch(on, steps, refine, stepCache = null, levels = SAMPLING_EFFORTS) {
    const max = levels.find(tier => tier.name === 'Max');
    if (!on) return {steps: steps === 8 ? max?.steps ?? 20 : steps, refine, upscale: 2,
        stepCache: steps === 8 ? max?.stepCache ?? null : null};
    const light = levels.find(tier => tier.twoPass && tier.upscale === 2);
    return effortFor(steps, false, refine, stepCache, levels) && light
        ? {steps: light.steps, refine: 3, upscale: 2, stepCache: null}
        : {steps, refine, upscale: 2, stepCache: null};
}

// A continuation samples 8 + 3 in two passes: it keeps the factor of a two-pass level, else 2 (widget text).
export const continuationUpscale = (steps, twoPass, refine, stepCache, upscale, levels = SAMPLING_EFFORTS) =>
    String(effortFor(Number(steps), twoPass !== false, Number(refine), stepCache, levels, upscale)?.twoPass ? upscaleOf(upscale) : 2);

// The level whose sampling table a plan needs, for naming a missing download (tiers.table_level):
// the exact level or legacy alias, else the first with those steps (16 is High, 20 is Max,
// Light for 8 + 3 whatever the factor).
export function tableLevel(steps, twoPass, refine, stepCache = null, upscale = 2) {
    return effortFor(steps, twoPass, refine, stepCache, SAMPLING_EFFORTS, upscale)
        || legacyMacEffort(steps, twoPass, stepCache) || SAMPLING_EFFORTS.find(tier => tier.steps === steps && tier.twoPass === twoPass && (!twoPass || refine === 3));
}

export function createSamplingEffort(t, {onChange, onPreview = () => {}, levels = SAMPLING_EFFORTS}) {
    let efforts = levels;
    const el = (tag, cls, text) => { const e = document.createElement(tag); e.className = cls; if (text) e.textContent = text; return e; };
    const element = el('div', 'fv-effort');
    const header = el('div', 'fv-effort-heading');
    const custom = el('span', 'fv-effort-custom', t('Custom', '自定义')); custom.hidden = true;
    const label = el('span', '', t('Quality', '质量'));
    const estimate = el('span', 'fv-effort-estimate');
    const warning = el('div', 'fv-effort-warning'); warning.hidden = true;
    // Ways out of what the warning says (the Fast level where a length does not fit): buttons under it.
    const ways = el('div', 'fv-effort-ways'); ways.hidden = true;
    // The names under the track show the level; the heading does not repeat it.
    header.append(label, custom, estimate);
    const rail = el('div', 'fv-effort-rail'); rail.tabIndex = 0;
    rail.setAttribute('role', 'slider'); rail.setAttribute('aria-label', label.textContent);
    rail.setAttribute('aria-valuemin', '0');
    rail.setAttribute('aria-orientation', 'horizontal');
    rail.dataset.fvTipName = t('Quality level', '质量档位'); rail.dataset.fvTipKey = '← →';
    rail.dataset.fvTipUse = t('More steps look finer and take longer', '步数越多越细，用时越长');
    // A thick track with the knob inside it; stops sit at the knob's centers.
    const track = el('span', 'fv-effort-track'); track.setAttribute('aria-hidden', 'true');
    const fill = el('span', 'fv-effort-fill'); track.append(fill);
    let stops = [], nameButtons = [], fastUse = '';
    const last = () => efforts.length - 1;
    const knob = el('span', 'fv-effort-knob'); track.append(knob); rail.append(track);
    knob.addEventListener('animationend', () => knob.classList.remove('fv-effort-pop'));
    const names = el('div', 'fv-effort-names');
    const nameUse = tier => tier.fast ? fastUse || t(...FAST_OPTION_TEXT) : tier.twoPass
        ? (tier.upscale < 2 ? t('{steps} + 3 steps from a larger first pass: more detail', '两遍 {steps} + 3 步，一采更大、细节更多', { steps: tier.steps })
            : efforts.some(level => level.fast) ? t('{steps} + 3 steps in two passes: quick', '两遍 {steps} + 3 步，速度较快', { steps: tier.steps })
            : t('{steps} + 3 steps in two passes: fastest', '两遍 {steps} + 3 步，最快', { steps: tier.steps }))
        : t('{steps} steps in one pass: finer, slower', '一遍 {steps} 步，更细也更慢', { steps: tier.steps });
    // One stop and one name per level; a machine's table can add the Fast level.
    function buildScale() {
        for (const stop of stops) stop.remove();
        stops = efforts.map((_, i) => { const stop = el('span', 'fv-effort-stop'); stop.style.setProperty('--fv-stop', i / last()); track.insertBefore(stop, knob); return stop; });
        names.replaceChildren();
        names.toggleAttribute('data-fast', efforts.some(tier => tier.fast));
        nameButtons = efforts.map((tier, i) => {
            const b = el('button', 'fv-effort-name', effortName(t, tier)); b.type = 'button'; b.tabIndex = -1;
            b.style.setProperty('--fv-stop', i / last());
            b.dataset.level = tier.name;  // the tooltip's words are found by level, whichever position it has
            b.dataset.fvTipName = effortName(t, tier);
            b.dataset.fvTipUse = nameUse(tier);
            b.onclick = () => { if (!disabled && pointer === null) commit(i); };
            names.append(b); return b;
        });
        rail.setAttribute('aria-valuemax', String(last()));
    }
    element.append(header, warning, ways, rail, names);
    let selected = 0, disabled = false, pointer = null, draft = 0, previewed = -1, currentSteps = 8, currentRefine = 3, twoPass = true, stepCache = null, upscale = 2, reason = '', fastNow = false;
    buildScale();
    function paint(position) {
        element.style.setProperty('--fv-position', position / last());
        const index = Math.round(position), tier = efforts[index];
        nameButtons.forEach((b, i) => b.toggleAttribute('data-active', i === index));
        stops.forEach((stop, i) => stop.toggleAttribute('data-filled', i <= position + .01));
        rail.setAttribute('aria-valuenow', String(index));
        rail.setAttribute('aria-valuetext', tier.fast ? effortName(t, tier) : `${effortName(t, tier)}, ${stepsWords(t, tier.steps, tier.twoPass, 3)}`);
    }
    function position(event) {
        const box = rail.getBoundingClientRect();
        // The knob's center travels between 18 px from either end (4 px inset, 28 px knob).
        return Math.max(0, Math.min(last(), (event.clientX - box.left - 18) / (box.width - 36) * last()));
    }
    // A drag tells the caller the level under the knob when it changes, not on every move.
    function preview(position) {
        const index = Math.round(position);
        if (index !== previewed) { previewed = index; onPreview(efforts[index]); }
    }
    function commit(index) {
        selected = index; custom.hidden = true; delete element.dataset.custom;
        paint(index); onChange(efforts[index]);
        knob.classList.remove('fv-effort-pop'); void knob.offsetWidth; knob.classList.add('fv-effort-pop');
    }
    function cancel() {
        const id = pointer; pointer = null; delete rail.dataset.dragging;
        if (id !== null && rail.hasPointerCapture(id)) rail.releasePointerCapture(id);
        update({baseSteps: currentSteps, refineSteps: currentRefine, twoPass, stepCache, upscale, disabled, reason, fast: fastNow});
        onPreview(null);
    }
    rail.onpointerdown = event => {
        delete rail.dataset.keyboard;  // The focus ring is for keyboard use only.
        if (disabled || event.button !== 0 || pointer !== null) return;
        event.preventDefault(); rail.focus({preventScroll: true, focusVisible: false}); pointer = event.pointerId;
        rail.setPointerCapture(pointer); rail.dataset.dragging = 'true';
        previewed = -1; draft = position(event); paint(draft); preview(draft);
    };
    rail.onpointermove = event => {
        if (event.pointerId !== pointer) return;
        draft = position(event); paint(draft); preview(draft);
    };
    rail.onpointerup = event => {
        if (event.pointerId !== pointer) return;
        draft = position(event); pointer = null; delete rail.dataset.dragging;
        rail.releasePointerCapture(event.pointerId); commit(Math.round(draft));
    };
    rail.onpointercancel = cancel;
    rail.onlostpointercapture = () => { if (pointer !== null) cancel(); };
    rail.onblur = () => { if (pointer !== null) cancel(); };
    rail.onkeydown = event => {
        rail.dataset.keyboard = 'true';
        if (event.key === 'Escape' && pointer !== null) { event.preventDefault(); event.stopPropagation(); cancel(); return; }
        if (disabled || pointer !== null) return;
        const next = {ArrowRight: selected + 1, ArrowUp: selected + 1, ArrowLeft: selected - 1, ArrowDown: selected - 1, Home: 0, End: last()}[event.key];
        if (next === undefined) return;
        event.preventDefault(); commit(Math.max(0, Math.min(last(), next)));
    };
    function update(state) {
        currentSteps = Number(state.baseSteps); currentRefine = Number(state.refineSteps);
        twoPass = state.twoPass; stepCache = stepCacheOf(state.stepCache); upscale = upscaleOf(state.upscale); disabled = state.disabled; reason = state.reason || ''; fastNow = Boolean(state.fast);
        rail.tabIndex = disabled ? -1 : 0; rail.setAttribute('aria-disabled', String(disabled));
        element.dataset.disabled = String(disabled);
        if (pointer !== null) return;
        const level = (fastNow && efforts.find(tier => tier.fast)) || effortFor(currentSteps, twoPass, currentRefine, stepCache, efforts, upscale);
        // Custom steps sit at the nearest level, preferring one with the same pass count.
        const distance = v => Math.abs(v.steps - currentSteps) * 2 + (v.twoPass !== twoPass);
        const plain = efforts.filter(tier => !tier.fast);
        selected = efforts.indexOf(level || plain.reduce((best, v) => distance(v) < distance(best) ? v : best));
        const isCustom = !level;
        element.dataset.custom = String(isCustom); custom.hidden = !isCustom;
        paint(selected);
        if (isCustom) nameButtons.forEach(b => b.removeAttribute('data-active'));  // No level is highlighted for custom steps.
        if (isCustom) rail.setAttribute('aria-valuetext', `${t('Custom', '自定义')} · ${stepsWords(t, currentSteps, twoPass, currentRefine)}`);
        rail.title = disabled ? reason || t('Controlled by connected nodes.', '由连接的节点控制。')
            : t('Same model. Higher effort uses more sampling steps; results vary by scene.', '使用同一模型，提高档位会增加采样步数；效果因场景而异。');
        if (disabled) rail.dataset.fvTipWhy = reason || t('Set by connected nodes', '由连接的节点决定'); else delete rail.dataset.fvTipWhy;
        for (const name of nameButtons) { name.disabled = disabled; name.title = ''; }
    }
    // The machine's own table (effortsFrom); the names stay, the plans follow the machine.
    function setLevels(next) {
        const same = (a, b) => a.steps === b.steps && a.twoPass === b.twoPass && a.stepCache === b.stepCache && a.upscale === b.upscale;
        if (!next || (next.length === efforts.length && macLevels.has(next) === macLevels.has(efforts)
            && next.every((tier, i) => same(tier, efforts[i])))) return;
        const rebuilt = next.length !== efforts.length;
        efforts = next;
        if (rebuilt) buildScale();
        update({baseSteps: currentSteps, refineSteps: currentRefine, twoPass, stepCache, upscale, disabled, reason, fast: fastNow});
    }
    // What the Fast level's option says: its download while the files are missing.
    function setFastUse(text) { fastUse = text || ''; efforts.forEach((tier, i) => { if (tier.fast) nameButtons[i].dataset.fvTipUse = nameUse(tier); }); }
    return {element, update, setLevels, setFastUse, levels: () => efforts, setWarning(text) { warning.textContent = text; warning.hidden = !text; },
        setWays(list = []) {
            ways.replaceChildren(...list.map(way => { const b = el('button', 'fv-effort-way', way.label); b.type = 'button'; b.dataset.way = way.id; b.onclick = way.run; return b; }));
            ways.hidden = !list.length;
        }, setEstimate(text, title = '', parts = 0) { estimate.textContent = text; if (parts > 1) estimate.dataset.parts = String(parts); else delete estimate.dataset.parts; if (title) estimate.title = title; else estimate.removeAttribute('title'); }, dispose: cancel};
}
