import {capabilityPoints, interpolate} from './chain_support.js';
import {buildPlanRequest, readLengthPlan, classifyPlanError, singleLimitSeconds, defaultBlockSeconds} from './length_plan.js?v=20261011-length';

export const LENGTH_PROPERTIES = Object.freeze({seconds: 'freevideo_seconds', single: 'freevideo_single', block: 'freevideo_block_seconds'});
const positive = value => typeof value === 'number' && Number.isFinite(value) && value > 0;
export const savedSeconds = (node, fallback) => positive(node.properties?.[LENGTH_PROPERTIES.seconds]) ? node.properties[LENGTH_PROPERTIES.seconds] : fallback;

// Use the editor's capability adapter, including its new 30s display ceiling.
// Only the explicit single-run override reads the raw frame limit.
export async function readLengthCapability(api, context, signal, onReply = () => {}) {
    try {
        const {width, height, settings, task, adapters} = context;
        const response = await api.fetchApi('/freevideo/capabilities?' + new URLSearchParams({width, height,
            tier: settings.tier, base_steps: settings.base_steps, refine_steps: settings.refine_steps,
            two_pass: settings.two_pass ? '1' : '0', task, adapters: adapters ? '1' : '0'}), {signal});
        onReply();
        if (!response.ok) return null;
        const reply = await response.json();
        if (reply.status && reply.status !== 'ready') return null;
        const value = capabilityPoints(reply, {width, height, settings});
        if (value.maxRequestFrames === null) return null;
        return {maximum: value.maxSeconds, singleMaximum: value.maxRequestFrames / 24, maxFrames: value.maxRequestFrames,
            estimate: frames => interpolate(value.points, frames / 24)};
    } catch { return null; }
}

export function createStudioLength({api, node, seconds, advanced, fields, t, field, setSeconds,
    onChange = () => {}, capability = readLengthCapability, delay = 220, capabilityTimeout = 10000}) {
    const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; };
    const controls = el('div', 'fv-length-controls'); controls.style.marginTop = '14px';
    const single = el('input'); single.type = 'checkbox'; single.dataset.fvLength = 'single';
    single.setAttribute('aria-label', t('Force a single run', '强制单次生成'));
    single.style.minHeight = '0'; single.style.marginRight = '8px';
    const toggle = el('label'); toggle.append(single, document.createTextNode(t('Force a single run', '强制单次生成')));
    const explanation = el('p', 'fv-muted', t('Generate in one run, even beyond 30 seconds.', '超过 30 秒时也一次生成。'));
    const unavailable = el('p', 'fv-muted'); unavailable.hidden = true;
    const block = el('input'); block.type = 'number'; block.min = '2'; block.step = '1'; block.required = true;
    block.dataset.fvLength = 'block'; block.setAttribute('aria-label', t('Part length · seconds', '每段时长 · 秒'));
    const pascalNote = el('p', 'fv-muted fv-pascal-length', t(
        'FreeVideo-Pascal: a clip can be as short as 1.625 s (2 s is the usual starting length). 5 s is only the default when splitting a long video into parts.',
        'FreeVideo-Pascal：单段最短 1.625 秒（常用 2 秒起步）。5 秒只是把长视频切段时的默认每段时长。'));
    const reset = el('button', 'fv-quiet', t('Restore default', '恢复默认')); reset.type = 'button';
    controls.append(toggle, explanation, pascalNote, unavailable, field(t('Part length · seconds', '每段时长 · 秒'), block), reset); advanced.append(controls);
    const note = el('div', 'fv-muted fv-length-plan');
    note.hidden = true; fields.after(note); note.setAttribute('role', 'status');
    let limits = null, planLimits = null, contextKey = '', revision = 0, timer = null, request = null, capabilityRequest = null, capabilityRevision = 0;
    let capabilityState = 'idle', waiting = false, waitingTimer = null, timeoutTimer = null, submitted = false;
    let segmentationEdited = node.properties?.[LENGTH_PROPERTIES.single] === true || Object.hasOwn(node.properties || {}, LENGTH_PROPERTIES.block);
    let plan = null, error = null, absent = false, disposed = false, pending = null, edited = false, blockEdited = false;
    const remember = (key, value) => { node.properties ||= {}; node.properties[key] = value; node.graph?.change(); node.setDirtyCanvas?.(true, true); };
    const total = () => Number(seconds.value);
    const effectiveSingle = () => node.properties?.[LENGTH_PROPERTIES.single] === true && total() <= (planLimits?.singleMaximum ?? 15);
    function options() {
        // Pin short fallback plans that would otherwise qualify for a single run.
        return {seconds: total(), single: effectiveSingle(), singleLimit: planLimits ? effectiveSingle() ? planLimits.singleMaximum : planLimits.maximum : null,
            maxFrames: planLimits?.maxFrames ?? null,
            blockSeconds: blockEdited || Object.hasOwn(node.properties || {}, LENGTH_PROPERTIES.block) ? Number(block.value)
                : !planLimits && total() > 15 && total() <= 30 ? defaultBlockSeconds(null) : null};
    }
    function paint() {
        const maximum = planLimits?.maximum, forcedMaximum = planLimits?.singleMaximum;
        const clamped = positive(maximum) && node.properties?.[LENGTH_PROPERTIES.block] > maximum;
        const cannot = total() > (forcedMaximum ?? 15);
        single.disabled = absent || seconds.disabled || cannot; single.checked = !absent && effectiveSingle();
        if (absent) seconds.max = '60';
        unavailable.hidden = absent || !planLimits || (!cannot && !clamped);
        unavailable.textContent = planLimits ? t(`Up to ${Math.floor(clamped ? maximum : forcedMaximum)} s in one run at this size.`, `当前画面大小单次最长 ${Math.floor(clamped ? maximum : forcedMaximum)} 秒。`) : '';
        if (positive(maximum)) block.max = String(maximum); else block.removeAttribute('max');
        if (!edited) block.value = Math.min(node.properties?.[LENGTH_PROPERTIES.block] || defaultBlockSeconds(maximum), maximum || Infinity);
        block.disabled = absent || seconds.disabled || effectiveSingle() || (positive(maximum) && maximum < 2); controls.hidden = absent;
        reset.hidden = !Object.hasOwn(node.properties || {}, LENGTH_PROPERTIES.block); reset.disabled = block.disabled;
        note.hidden = absent || !error;
        note.textContent = error ? errorMessage(error) : '';
    }
    function errorMessage(kind) {
        return {single_unavailable: t('This length is beyond a single run.', '这个长度超出了单次能生成的范围。'),
            continuation_unavailable: t('Choose a smaller picture size to make a longer video.', '如需生成更长的视频，请选择更小的画面大小。'),
            too_long: t('This video is too long. Choose a shorter duration.', '这条视频太长，请缩短时长。'),
            other: t('Could not plan this video. Try again.', '暂时无法安排这条视频的分段，请重试。')}[kind];
    }
    function invalidate() {
        revision++; plan = null; error = null; clearTimeout(timer); timer = null; request?.abort(); request = null; pending = null;
        paint(); onChange();
    }
    async function calculate(version = revision) {
        if (disposed || absent || !seconds.checkValidity() || (!block.disabled && !block.checkValidity())) return null;
        if (!effectiveSingle() && total() <= singleLimitSeconds(planLimits?.maximum) && !edited
            && !Object.hasOwn(node.properties || {}, LENGTH_PROPERTIES.block)) return null;
        const controller = new AbortController(); request = controller;
        try {
            const response = await api.fetchApi('/freevideo/chain/plan', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(buildPlanRequest(options())), signal: controller.signal});
            const reply = response.status === 404 ? null : await response.json();
            if (disposed || controller.signal.aborted || version !== revision) return null;
            if (response.status === 404) { absent = true; plan = null; error = null; }
            else if (response.status === 400) error = classifyPlanError(reply);
            else if (response.ok) { plan = readLengthPlan(reply); if (!plan) error = 'other'; }
            else error = 'other';
        } catch { if (!controller.signal.aborted && version === revision) error = 'other'; }
        if (!disposed && !controller.signal.aborted && version === revision) { paint(); onChange(); }
        return plan;
    }
    function schedule() { const version = revision; timer = setTimeout(() => { timer = null; pending = calculate(version); }, delay); }
    single.onchange = () => { remember(LENGTH_PROPERTIES.single, single.checked); segmentationEdited = true; edited = true; invalidate(); schedule(); };
    block.oninput = () => { segmentationEdited = true; edited = blockEdited = true; invalidate(); schedule(); };
    block.onchange = () => { if (block.reportValidity()) remember(LENGTH_PROPERTIES.block, Number(block.value)); };
    reset.onclick = () => {
        delete node.properties[LENGTH_PROPERTIES.block]; node.graph?.change(); submitted = false;
        segmentationEdited = true; planLimits = limits;
        edited = blockEdited = false; invalidate(); edited = true; schedule();
    };
    const input = () => { submitted = false; if (!segmentationEdited) planLimits = limits; edited = false; invalidate(); schedule(); };
    seconds.addEventListener('input', input);
    function commit() {
        if (!seconds.value || !seconds.reportValidity()) return false;
        const value = total() > 30 ? Math.round(total()) : total(); seconds.value = value;
        if (value > 60) remember(LENGTH_PROPERTIES.seconds, value);
        else if (node.properties && Object.hasOwn(node.properties, LENGTH_PROPERTIES.seconds)) { delete node.properties[LENGTH_PROPERTIES.seconds]; node.graph?.change(); }
        setSeconds(Math.min(60, value)); invalidate(); schedule(); return true;
    }
    paint();
    return {
        commit, options, get plan() { return plan; }, get absent() { return absent; },
        get needsPlan() { return !absent && !effectiveSingle() && (total() > singleLimitSeconds(planLimits?.maximum)
            || Object.hasOwn(node.properties || {}, LENGTH_PROPERTIES.block)); },
        get estimate() { return limits?.estimate || null; },
        get capabilityState() { return capabilityState; }, get estimating() { return waiting; },
        get blocked() { return !absent && Boolean(error); }, get message() { return error ? errorMessage(error) : ''; },
        fallback() { absent = true; invalidate(); },
        freeze() { submitted = true; },
        async ready() {
            while (!disposed) {
                const version = revision; clearTimeout(timer); timer = null;
                if (pending) await pending;
                if (version !== revision) continue;
                if (!plan && !absent) pending = calculate();
                if (pending) await pending;
                if (version === revision) return plan;
            }
            return null;
        },
        refresh(next, force = false) {
            const key = JSON.stringify(next); if ((!force && key === contextKey) || disposed) return;
            contextKey = key; limits = planLimits = null; edited = blockEdited = false; submitted = false;
            capabilityState = 'pending'; waiting = false;
            clearTimeout(waitingTimer); clearTimeout(timeoutTimer); invalidate(); schedule();
            capabilityRequest?.abort(); capabilityRequest = new AbortController();
            const controller = capabilityRequest, version = ++capabilityRevision;
            const settle = value => {
                if (disposed || controller.signal.aborted || version !== capabilityRevision) return;
                clearTimeout(waitingTimer); clearTimeout(timeoutTimer); waiting = false;
                limits = value; capabilityState = value ? 'ready' : 'unavailable';
                if (value && !segmentationEdited && !submitted) { planLimits = value; invalidate(); schedule(); }
                else { paint(); onChange(); }
            };
            timeoutTimer = setTimeout(() => { settle(null); controller.abort(); }, capabilityTimeout);
            let replied = false;
            const onReply = () => {
                if (disposed || controller.signal.aborted || version !== capabilityRevision) return;
                replied = true; clearTimeout(waitingTimer);
            };
            let reading;
            try { reading = capability(api, next, controller.signal, onReply); }
            catch { settle(null); return; }
            if (!replied) waitingTimer = setTimeout(() => { waiting = true; onChange(); }, 350);
            (async () => {
                try { settle(await reading); }
                catch { settle(null); }
            })();
        },
        dispose() { disposed = true; clearTimeout(timer); clearTimeout(waitingTimer); clearTimeout(timeoutTimer); request?.abort(); capabilityRequest?.abort(); seconds.removeEventListener('input', input); },
    };
}
