// One editor control. The queued request remains an immutable graph snapshot.
export const SAMPLING_EFFORTS = Object.freeze([
    {name: 'Light', steps: 8, color: '#90b9b2'},
    {name: 'Medium', steps: 12, color: '#8aafd3'},
    {name: 'High', steps: 16, color: '#a8a6d4'},
    {name: 'Max', steps: 20, color: '#c4ae8d'},
].map(Object.freeze));

export function createSamplingEffort(t, {onChange, onPreview = () => {}}) {
    const el = (tag, cls, text) => { const e = document.createElement(tag); e.className = cls; if (text) e.textContent = text; return e; };
    const element = el('div', 'fv-effort');
    const header = el('div', 'fv-effort-heading');
    const label = el('span', '', t('Quality', '质量'));
    const estimate = el('span', 'fv-effort-estimate');
    const selectedLabel = el('span', 'fv-effort-value');
    header.append(label, selectedLabel, estimate);
    const rail = el('div', 'fv-effort-rail'); rail.tabIndex = 0;
    rail.setAttribute('role', 'slider'); rail.setAttribute('aria-label', label.textContent);
    rail.setAttribute('aria-valuemin', '0'); rail.setAttribute('aria-valuemax', '3');
    rail.setAttribute('aria-orientation', 'horizontal');
    const track = el('span', 'fv-effort-track'); track.setAttribute('aria-hidden', 'true');
    const fill = el('span', 'fv-effort-fill'); track.append(fill); rail.append(track);
    for (let i=0;i<4;i++) { const tick=el('span','fv-effort-tick'); tick.style.left=`calc(10px + (100% - 20px) * ${i / 3})`; tick.setAttribute('aria-hidden','true'); rail.append(tick); }
    const pill = el('span', 'fv-effort-pill'); pill.setAttribute('aria-hidden', 'true'); rail.append(pill);
    const custom = el('span', 'fv-effort-custom', t('Custom steps', '自定义步数')); custom.hidden = true;
    element.append(header, rail, custom);
    let selected = 0, disabled = false, pointer = null, draft = 0, currentSteps = 8, currentRefine = 3, twoPass = true;
    function paint(position) {
        rail.style.setProperty('--fv-position', position / 3);
        fill.style.width = `${position / 3 * 100}%`;
        const index = Math.round(position), tier = SAMPLING_EFFORTS[index];
        element.style.setProperty('--fv-effort-color', tier.color);
        selectedLabel.textContent = tier.name;
        rail.setAttribute('aria-valuenow', String(index));
        rail.setAttribute('aria-valuetext', `${tier.name}, ${tier.steps}${twoPass && tier.steps === 8 ? ' + 3' : ''} ${t('steps', '步')}`);
    }
    function position(event) {
        const box = rail.getBoundingClientRect();
        return Math.max(0, Math.min(3, (event.clientX - box.left - 10) / (box.width - 20) * 3));
    }
    function commit(index) {
        selected = index; custom.hidden = true; delete element.dataset.custom;
        paint(index); onChange(SAMPLING_EFFORTS[index].steps, 3);
    }
    function cancel() {
        const id = pointer; pointer = null; delete rail.dataset.dragging;
        if (id !== null && rail.hasPointerCapture(id)) rail.releasePointerCapture(id);
        update({baseSteps: currentSteps, refineSteps: currentRefine, twoPass, disabled});
        onPreview(currentSteps);
    }
    rail.onpointerdown = event => {
        if (disabled || event.button !== 0 || pointer !== null) return;
        event.preventDefault(); rail.focus(); pointer = event.pointerId;
        rail.setPointerCapture(pointer); rail.dataset.dragging = 'true';
        draft = position(event); paint(draft); onPreview(SAMPLING_EFFORTS[Math.round(draft)].steps);
    };
    rail.onpointermove = event => {
        if (event.pointerId !== pointer) return;
        draft = position(event); paint(draft); onPreview(SAMPLING_EFFORTS[Math.round(draft)].steps);
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
        if (event.key === 'Escape' && pointer !== null) { event.preventDefault(); event.stopPropagation(); cancel(); return; }
        if (disabled || pointer !== null) return;
        const next = {ArrowRight: selected + 1, ArrowUp: selected + 1, ArrowLeft: selected - 1, ArrowDown: selected - 1, Home: 0, End: 3}[event.key];
        if (next === undefined) return;
        event.preventDefault(); commit(Math.max(0, Math.min(3, next)));
    };
    function update(state) {
        currentSteps = Number(state.baseSteps); currentRefine = Number(state.refineSteps);
        twoPass = state.twoPass; disabled = state.disabled;
        rail.tabIndex = disabled ? -1 : 0; rail.setAttribute('aria-disabled', String(disabled));
        element.dataset.disabled = String(disabled);
        if (pointer !== null) return;
        const exact = SAMPLING_EFFORTS.findIndex(v => v.steps === currentSteps);
        selected = exact < 0 ? SAMPLING_EFFORTS.reduce((best, v, i) => Math.abs(v.steps - currentSteps) < Math.abs(SAMPLING_EFFORTS[best].steps - currentSteps) ? i : best, 0) : exact;
        const isCustom = exact < 0 || (twoPass && currentRefine !== 3);
        element.dataset.custom = String(isCustom); custom.hidden = !isCustom;
        custom.textContent = `${t('Custom', '自定义')} · ${currentSteps}${twoPass ? ' + ' + currentRefine : ''} ${t('steps', '步')}`;
        paint(selected);
        if (isCustom) { rail.setAttribute('aria-valuetext', custom.textContent); selectedLabel.textContent=t('Custom','自定义'); }
        rail.title = disabled ? t('Controlled by connected nodes.', '由连接的节点控制。')
            : t('Same model. Higher effort uses more sampling steps; results vary by scene.', '使用同一模型，提高档位会增加采样步数；效果因场景而异。');
    }
    return {element, update, setEstimate(text, title = '') { estimate.textContent = text; estimate.title = title; }, dispose: cancel};
}
