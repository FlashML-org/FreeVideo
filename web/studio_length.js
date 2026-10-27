import {capabilityPoints, interpolate} from './chain_support.js';
import {issueButton} from './issue_button.js';
import {buildPlanRequest, readLengthPlan, classifyPlanError, singleLimitSeconds, defaultBlockSeconds} from './length_plan.js?v=20261027-length';

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

// onEditor and onAutopilot are the creation panel's own view-switch actions; autopilot says whether that tab shows.
export function createStudioLength({api, node, seconds, advanced, fields, t, field, setSeconds,
    onChange = () => {}, capability = readLengthCapability, delay = 220, capabilityTimeout = 10000,
    onEditor = () => {}, onAutopilot = () => {}, autopilot = false, promptModel = null}) {
    const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; };
    const controls = el('div', 'fv-length-controls'); controls.style.marginTop = '14px';
    const single = el('input'); single.type = 'checkbox'; single.dataset.fvLength = 'single';
    single.setAttribute('aria-label', t('Force a single run', '强制单次生成'));
    single.style.minHeight = '0'; single.style.marginRight = '8px';
    const toggle = el('label'); toggle.append(single, document.createTextNode(t('Force a single run', '强制单次生成')));
    const explanation = el('p', 'fv-muted', t('Generate in one run, even beyond 30 seconds.', '超过 30 秒时也一次生成。'));
    const unavailable = el('p', 'fv-muted'); unavailable.hidden = true;
    const block = el('input'); block.type = 'number'; block.min = '5'; block.step = '1'; block.required = true;
    block.dataset.fvLength = 'block'; block.setAttribute('aria-label', t('Part length · seconds', '每段时长 · 秒'));
    const reset = el('button', 'fv-quiet', t('Restore default', '恢复默认')); reset.type = 'button';
    controls.append(toggle, explanation, unavailable, field(t('Part length · seconds', '每段时长 · 秒'), block), reset); advanced.append(controls);
    const note = el('div', 'fv-muted fv-length-plan');
    note.hidden = true; fields.after(note); note.setAttribute('role', 'status');
    // The limits are stated before anything is typed, not only when a request is refused.
    const NO_PARTS = t('This device cannot make longer videos in parts.', '此设备不支持分段生成长视频。');
    let noParts = false;
    // A storyboard goes to the same views as the tabs above; Autopilot is named only while its tab shows.
    let autopilotShown = Boolean(autopilot);
    const viewLink = (label, action) => { const link = el('button', 'fv-length-link', label); link.type = 'button'; link.onclick = action; return link; };
    function storyboard() {
        const parts = [t('For a storyboard, use ', '有分镜剧本时，请用'), viewLink(t('Edit', '剪辑视图'), onEditor)];
        if (autopilotShown) {
            const link = viewLink(t('Autopilot', 'AI 全自动'), onAutopilot); link.setAttribute('translate', 'no');
            parts.push(t(' or ', '或'), link);
        }
        return [...parts, t('.', '。')];
    }
    const storyboardText = () => storyboard().map(part => typeof part === 'string' ? part : part.textContent).join('');
    const TOO_LONG = () => t('This shot is too long. Choose a shorter duration. ', '这个镜头太长，请缩短时长。');
    const hint = el('p', 'fv-muted fv-length-hint');
    const writeHint = () => noParts ? hint.textContent = NO_PARTS
        : hint.replaceChildren(t('Each run makes one continuous shot, split into up to 8 parts when longer. ', '每次生成一个连续镜头，较长时自动分段，最多 8 段。'), ...storyboard());
    writeHint();
    fields.after(hint);
    const modelNotice = el('div', 'fv-muted fv-length-model'); modelNotice.hidden = true; modelNotice.setAttribute('role', 'status');
    const modelText = el('span');
    const download = el('button', 'fv-quiet', t('Download prompt model', '下载提示词模型')); download.type = 'button';
    modelNotice.append(modelText, download); hint.after(modelNotice);
    download.onclick = () => { void promptModel?.download(); };
    let modelState = {}, modelChecked = false;

    let limits = null, planLimits = null, contextKey = '', revision = 0, timer = null, request = null, capabilityRequest = null, capabilityRevision = 0;
    let capabilityState = 'idle', waiting = false, waitingTimer = null, timeoutTimer = null, submitted = false;
    let plan = null, error = null, absent = false, disposed = false, pending = null, edited = false, blockEdited = false, plannedSeconds = null;
    const remember = (key, value) => { node.properties ||= {}; node.properties[key] = value; node.graph?.change(); node.setDirtyCanvas?.(true, true); };
    const total = () => Number(seconds.value);
    let forced = false;
    const effectiveSingle = () => forced || (node.properties?.[LENGTH_PROPERTIES.single] === true && total() <= (planLimits?.singleMaximum ?? 15));
    function options() {
        // Pin short fallback plans that would otherwise qualify for a single run.
        return {seconds: total(), single: effectiveSingle(), singleLimit: planLimits ? effectiveSingle() ? planLimits.singleMaximum : planLimits.maximum : null,
            maxFrames: planLimits?.maxFrames ?? null,
            blockSeconds: blockEdited || Object.hasOwn(node.properties || {}, LENGTH_PROPERTIES.block) ? Number(block.value)
                : !planLimits && total() > 15 && total() <= 30 ? defaultBlockSeconds(null) : null};
    }
    function paintModel() {
        const multiple = !absent && !error && plan?.count >= 2;
        modelNotice.hidden = !multiple || modelState.info?.ready !== false;
        modelText.textContent = modelState.text || t('The prompt model is not installed, so the description will be split across the parts by sentence.', '未安装提示词模型，描述将按句子顺序分到各段。');
        download.hidden = Boolean(modelState.busy); download.disabled = Boolean(modelState.busy);
        // Pending plans do not reset the check on every keystroke. A known single part does.
        if (plan?.count === 1 || (!plan && !effectiveSingle() && total() <= singleLimitSeconds(planLimits?.maximum)
            && !Object.hasOwn(node.properties || {}, LENGTH_PROPERTIES.block))) modelChecked = false;
        if (multiple && !modelChecked && promptModel) {
            modelChecked = true;
            promptModel.read().catch(() => {});
        }
    }
    const unsubscribeModel = promptModel?.subscribe(state => { modelState = state; if (!disposed) paintModel(); });
    function paint() {
        const maximum = planLimits?.maximum, forcedMaximum = planLimits?.singleMaximum;
        const clamped = positive(maximum) && node.properties?.[LENGTH_PROPERTIES.block] > maximum;
        const cannot = total() > (forcedMaximum ?? 15);
        single.disabled = absent || seconds.disabled || cannot || forced; single.checked = !absent && effectiveSingle();
        if (absent) seconds.max = '60';
        const reason = cannot ? positive(forcedMaximum)
            ? t(`Up to ${Math.floor(forcedMaximum)}\u00a0s in one run at this size.`, `当前画面大小单次最长 ${Math.floor(forcedMaximum)}\u00a0秒。`)
            : t('The maximum length is unknown for these settings.', '当前设置下无法确定最长时长。')
            : seconds.disabled ? '' : clamped
                ? t(`Up to ${Math.floor(maximum)}\u00a0s in one run at this size.`, `当前画面大小单次最长 ${Math.floor(maximum)}\u00a0秒。`) : '';
        unavailable.hidden = absent || !reason; unavailable.textContent = reason;
        single.title = single.disabled ? reason : ''; toggle.title = single.title;
        if (positive(maximum)) block.max = String(maximum); else block.removeAttribute('max');
        if (!edited) block.value = Math.min(node.properties?.[LENGTH_PROPERTIES.block] || defaultBlockSeconds(maximum), maximum || Infinity);
        block.disabled = absent || seconds.disabled || effectiveSingle() || (positive(maximum) && maximum < 5); controls.hidden = absent;
        reset.hidden = !Object.hasOwn(node.properties || {}, LENGTH_PROPERTIES.block); reset.disabled = block.disabled;
        block.title = !block.disabled || absent || seconds.disabled || effectiveSingle() ? ''
            : t('These settings cannot generate in parts.', '当前设置不能分段生成。');
        reset.title = block.title;
        // The same sentence is the problem note once a length needs parts: say it once.
        hint.hidden = absent || (noParts && Boolean(error));
        note.hidden = absent || !error;
        if (error === 'too_long') note.replaceChildren(TOO_LONG(), ...storyboard());
        else note.textContent = error ? errorMessage(error) : '';
        if (error === 'other') note.append(issueButton(t, () => ({where: ['Video length planning', '视频分段规划'], message: errorMessage('other')})));
        paintModel();
        note.style.margin = noParts && error === 'continuation_unavailable' ? '12px 0' : ''; // as spaced as the line it replaces
    }
    function errorMessage(kind) {
        return {single_unavailable: t('This length is beyond a single run.', '这个长度超出了单次能生成的范围。'),
            continuation_unavailable: noParts ? NO_PARTS
                : t('Choose a smaller picture size to make a longer video.', '如需生成更长的视频，请选择更小的画面大小。'),
            too_long: TOO_LONG() + storyboardText(),
            block_too_short: t('This part length is too short. Lengthen the part length.', '每段时长过短，请加长每段时长。'),
            block_too_long: t('One run cannot make a part this long at this size. Shorten the part length.', '当前画面大小下单次生成不了这么长的分段，请缩短每段时长。'),
            other: t('Could not plan this video. Try again.', '暂时无法安排这条视频的分段，请重试。')}[kind];
    }
    function invalidate() {
        revision++; plan = null; error = null; clearTimeout(timer); timer = null; request?.abort(); request = null; pending = null; plannedSeconds = total();
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
    single.onchange = () => { remember(LENGTH_PROPERTIES.single, single.checked); edited = true; invalidate(); schedule(); };
    block.oninput = () => { edited = blockEdited = true; invalidate(); schedule(); };
    block.onchange = () => { if (block.reportValidity()) remember(LENGTH_PROPERTIES.block, Number(block.value)); };
    reset.onclick = () => {
        delete node.properties[LENGTH_PROPERTIES.block]; node.graph?.change(); submitted = false;
        planLimits = limits;
        edited = blockEdited = false; invalidate(); edited = true; schedule();
    };
    const input = () => { submitted = false; planLimits = limits; edited = false; invalidate(); schedule(); };
    seconds.addEventListener('input', input);
    function commit({report = true, exact = false} = {}) {
        const valid = report ? seconds.reportValidity() : seconds.checkValidity();
        if (!seconds.value || !valid) return false;
        const value = !exact && total() > 30 ? Math.round(total()) : total(); seconds.value = value;
        if (value > 60) remember(LENGTH_PROPERTIES.seconds, value);
        else if (node.properties && Object.hasOwn(node.properties, LENGTH_PROPERTIES.seconds)) { delete node.properties[LENGTH_PROPERTIES.seconds]; node.graph?.change(); }
        setSeconds(Math.min(60, value));
        // Leaving the field with the length already planned keeps that plan and its note, so a link in the note can be pressed.
        if (value !== plannedSeconds || error === 'other') { invalidate(); schedule(); }
        return true;
    }
    paint();
    // Devices that cannot continue a video say so up front. An unreadable answer keeps the general line.
    Promise.resolve().then(() => api.fetchApi('/freevideo/chains')).then(reply => reply?.ok ? reply.json() : null).then(value => {
        if (disposed || value?.continuation !== false) return;
        noParts = true; writeHint(); invalidate(); schedule();
    }).catch(() => {});
    return {
        // Short original audio keeps its single run; long selections can use parts.
        forceSingle(on) { if (forced === on) return; forced = on; submitted = false; invalidate(); schedule(); },
        commit, options, get plan() { return plan; }, get absent() { return absent; },
        get needsPlan() { return !absent && !effectiveSingle() && (total() > singleLimitSeconds(planLimits?.maximum)
            || Object.hasOwn(node.properties || {}, LENGTH_PROPERTIES.block)); },
        get estimate() { return limits?.estimate || null; },
        get capabilityState() { return capabilityState; }, get estimating() { return waiting; },
        get blocked() { return !absent && Boolean(error); }, get message() { return error ? errorMessage(error) : ''; },
        fallback() { absent = true; invalidate(); },
        // Follows the Autopilot tab when its state settles after the panel opened.
        set autopilot(shown) { if (disposed || Boolean(shown) === autopilotShown) return; autopilotShown = Boolean(shown); writeHint(); paint(); onChange(); },
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
                // Draft edits choose segmentation, not a permanent device limit.
                // A submitted job keeps the exact bounds used for its snapshot.
                if (value && !submitted) { planLimits = value; invalidate(); schedule(); }
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
        dispose() { disposed = true; unsubscribeModel?.(); clearTimeout(timer); clearTimeout(waitingTimer); clearTimeout(timeoutTimer); request?.abort(); capabilityRequest?.abort(); seconds.removeEventListener('input', input); },
    };
}
