import {row, slider, number, button, disabledReason} from './editor_adjust.js';
import {PICTURE_DEFAULTS, pictureFields} from './editor_picture_fields.js';
import {pictureSession} from './editor_picture_session.js';
import {applyTip} from './editor_tracks_tips.js';
export const scaleFromSlider = value => Math.abs(value) < .025 ? 1 : 10 ** value;
export const quarterTurn = (angle, delta) => ((angle + delta + 540) % 360) - 180;
export function createAdjustPicture({state, sources, t, player, notes = null}) {
    const session = pictureSession(state, t), widgets = [], numbers = {}, tipped = [];
    let visibilityObserver, keysObserver, inputError, invalidClip;
    let context, container, clip, scale, reset, crop, cropActions, flips, scaleDrag = null;
    const keep = node => { if (node.tagName === 'BUTTON') node.setAttribute('aria-label', node.textContent); widgets.push(node); return node; };
    const change = fields => { if (clip && !container.disabled) session.change(clip.id, fields); };
    const numeric = (key, label, min, max, factor = 1, suffix = '') => {
        const control = keep(number({min, max, step: 'any', label: label + suffix,
            onChange: value => { if (!control.input.dataset.editClip || control.input.dataset.editClip === clip?.id) change({[key]: value / factor}); }}));
        control.input.dataset.pictureField = key;
        const input = control.input.oninput, commit = control.input.onchange;
        const remember = () => { control.input.dataset.editing = 'true'; control.input.dataset.editClip ||= clip?.id || ''; };
        const clearError = () => { if (inputError) inputError.hidden = true; invalidClip = null; control.input.removeAttribute('aria-invalid'); };
        const submit = event => {
            if (control.input.dataset.editClip === clip?.id && (control.input.value === '' || !Number.isFinite(Number(control.input.value))
                || Number(control.input.value) < min || Number(control.input.value) > max)) {
                inputError.textContent = t('{label}: enter a number from {min} to {max}{suffix}.', '{label}：请输入 {min} 至 {max}{suffix}。', { label, min, max, suffix });
                inputError.hidden = false; invalidClip = clip?.id; control.input.setAttribute('aria-invalid', 'true'); return;
            }
            clearError();
            // Native number inputs also emit change for spinner/arrow steps. Q13
            // commits only at blur/Enter, keeping the text's native undo intact.
            control.input.dataset.editing = 'false'; commit(event);
            delete control.input.dataset.editClip; update();
        };
        control.input.oninput = event => { clearError(); remember(); input(event); };
        control.input.onchange = () => {};
        control.input.onblur = submit;
        if (suffix) control.dataset.unit = suffix.trim();
        if (key.startsWith('offset_')) control.dataset.axis = key.endsWith('x') ? 'X' : 'Y';
        control.input.onkeydown = event => {
            if (event.isComposing || event.keyCode === 229) { event.stopPropagation(); return; }
            if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); submit(event); return; }
            if (event.key === 'Escape' && !session.draft) {
                event.preventDefault(); event.stopPropagation();
                clearError(); control.input.dataset.editing = 'false'; delete control.input.dataset.editClip; update(); return;
            }
            if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
            event.preventDefault(); event.stopPropagation(); clearError(); remember();
            control.input.value = String(Math.min(max, Math.max(min, Number(control.input.value) + (event.key === 'ArrowUp' ? 1 : -1) * (event.shiftKey ? 10 : 1))));
            input(event);
        };
        numbers[key] = {control, factor}; return control;
    };
    function update(ctx = context) {
        if (!ctx || !container) return; context = ctx;
        notes?.update(ctx);
        const next = ctx.clips.find(c => c.track.kind === 'video' && c.source);
        // A number typed for one part is kept for that part when the selection moves on while its box still has the focus
        // (a key that selects another part, say): it commits to the part it was typed for, never to the new one and never lost.
        if (clip && next?.id !== clip.id) for (const {control} of Object.values(numbers)) {
            if (control.input.dataset.editing === 'true' && control.input.dataset.editClip === clip.id) control.input.onblur?.();
        }
        clip = next;
        const seen = new Set(); let count = 0;
        for (const item of ctx.clips) { if (!seen.has(item.id)) count++; seen.add(item.id); if (item.link) seen.add(item.link); }
        const reason = disabledReason({...ctx, multiple: ctx.multiple || count > 1}, {needs: 'video', single: true}) || session.editReason(clip);
        if (container.disabled !== !!reason) container.disabled = !!reason; container.title = '';
        // Each control's hover help names it, says what it does, and while greyed says why (editor_tracks_tips.js).
        for (const [node, id] of tipped) applyTip(node, id, t, {why: reason || ''});
        if (inputError && invalidClip !== clip?.id) { inputError.hidden = true; invalidClip = null; }
        const current = {...PICTURE_DEFAULTS, ...session.clip(clip)};
        for (const [key, {control, factor}] of Object.entries(numbers)) {
            if (document.activeElement !== control.input || control.input.dataset.editing !== 'true' || control.input.dataset.editClip !== clip?.id || session.draft) { control.value = Number((current[key] * factor).toFixed(4)); control.input.removeAttribute('aria-invalid'); }
        }
        if (session.draft?.mode !== 'slider') scale.value = Math.log10(current.scale);
        scale.input.setAttribute('aria-valuetext', `${Math.round(current.scale * 100)}%`);
        flips.forEach(([key, control]) => control.setAttribute('aria-pressed', String(current[key])));
        cropActions.hidden = session.draft?.mode !== 'crop'; crop.setAttribute('aria-pressed', String(!cropActions.hidden));
        reset.hidden = !clip || !Object.keys(pictureFields({...current, fit: 'cover'})).length;
    }
    const dragScale = event => {
        if (!scaleDrag || event.pointerId !== scaleDrag.pointer) return;
        const value = Math.max(-1, Math.min(1, (event.clientX-scaleDrag.left-8) / Math.max(1,scaleDrag.width-16) * 2-1));
        scale.input.value = String(value); session.preview({scale: scaleFromSlider(value)});
    };
    const endScale = event => {
        if (!scaleDrag || event.pointerId !== scaleDrag.pointer) return;
        scaleDrag = null; event.type === 'pointercancel' ? session.cancel() : session.commit();
    };
    window.addEventListener('pointermove', dragScale, true); window.addEventListener('pointerup', endScale, true); window.addEventListener('pointercancel', endScale, true);
    const unsubscribe = session.subscribe(() => { if (!session.draft) scaleDrag = null; update(); });
    return {
        render(host, ctx) {
            container = host; context = ctx; host.classList.add('fv-ed-adjust-picture');
            // The description stays editable on blocks the picture controls cannot change yet (drafts).
            if (notes) host.parentNode.insertBefore(notes.element, host);
            // Observe the shell's existing visibility contract without changing its
            // lifecycle API (the shell is owned by the adjustment-panel task).
            const pane = host.closest('[role=tabpanel]'), panel = host.closest('.fv-ed-adjust-host');
            const visibility = () => session.setPanelOpen(!pane?.hidden && !panel?.hidden);
            visibilityObserver = typeof MutationObserver === 'function' ? new MutationObserver(visibility) : null;
            for (const node of [pane, panel]) if (node) visibilityObserver?.observe(node, {attributes: true, attributeFilter: ['hidden']});
            // Fill or Fit lives only in the switch at the preview's top left (one place per function).
            scale = keep(slider({min: -1, max: 1, step: .001, value: 0, label: t('Scale', '缩放'),
                onInput: value => { if (!clip) return; session.begin(clip.id, 'slider'); session.preview({scale: scaleFromSlider(value)}); },
                onChange: () => session.commit()}));
            scale.input.dataset.pictureField = 'scale-slider'; scale.output.hidden = true;
            scale.input.onpointerdown = event => {
                if (event.button !== 0 || !clip || container.disabled) return;
                event.preventDefault(); scale.input.focus();
                const rect = scale.input.getBoundingClientRect(); session.begin(clip.id, 'slider');
                scaleDrag = {pointer:event.pointerId,left:rect.left,width:rect.width};
                scale.input.setPointerCapture?.(event.pointerId); dragScale(event);
            };
            const scaleNumber = numeric('scale', t('Scale', '缩放'), 10, 1000, 100, ' %');
            const position = row(t('Position', '位置'), numeric('offset_x', 'X', -200, 200, 100, ' %'), numeric('offset_y', 'Y', -200, 200, 100, ' %'));
            // Short names that fit two to a row in a 400 px drawer; the hover names the angle (editor_tracks_tips.js).
            const turns = row('', ...[-90, 90].map(delta => keep(button(delta < 0 ? t('Rotate left', '左转 90°') : t('Rotate right', '右转 90°'),
                {onClick: () => change({rotate: quarterTurn(clip?.rotate || 0, delta)})}))));
            flips = [['flip_h', t('Flip horizontal', '水平翻转')], ['flip_v', t('Flip vertical', '垂直翻转')]].map(([key, label]) => {
                const b = keep(button(label, {onClick: () => change({[key]: !clip?.[key]})})); b.dataset.pictureField = key; b.setAttribute('aria-pressed', 'false'); return [key, b];
            });
            crop = keep(button(t('Crop', '裁剪'), {onClick: () => { if (clip) { player?.pause(); session.begin(clip.id, 'crop'); } }})); crop.classList.add('fv-ed-picture-crop');
            cropActions = row('', keep(button(t('Done', '完成'), {onClick: () => session.commit()})), keep(button(t('Cancel', '取消'), {onClick: () => session.cancel()})));
            cropActions.classList.add('fv-ed-picture-crop-actions');
            reset = keep(button(t('Reset', '恢复原样'), {onClick: () => change({...PICTURE_DEFAULTS, fit: clip?.fit || 'cover'})})); reset.classList.add('fv-ed-picture-reset');
            inputError = document.createElement('p'); inputError.className = 'fv-ed-picture-input-error'; inputError.setAttribute('role', 'status'); inputError.hidden = true;
            const [left, right] = turns.querySelectorAll('button'), [done, cancel] = cropActions.querySelectorAll('button');
            tipped.splice(0, tipped.length, [scale.input, 'scale'], [scaleNumber.input, 'scaleNumber'], [numbers.offset_x.control.input, 'offsetX'],
                [numbers.offset_y.control.input, 'offsetY'], [left, 'rotateLeft'], [right, 'rotateRight'], [flips[0][1], 'flipH'], [flips[1][1], 'flipV'],
                [crop, 'crop'], [done, 'cropDone'], [cancel, 'cropCancel'], [reset, 'pictureReset']);
            const flipRow = row('', ...flips.map(([, b]) => b));
            host.append(row(t('Scale', '缩放'), scale, scaleNumber), position,
                row(t('Rotation', '旋转'), numeric('rotate', t('Rotation', '旋转'), -180, 180, 1, ' °')), turns,
                flipRow, crop, cropActions, inputError, reset);
            // Rotate and flip are one group of four: two equal columns while every name fits on one line in half
            // the row, else one column for all four. Names are never cut or broken.
            turns.classList.add('fv-ed-picture-keys'); flipRow.classList.add('fv-ed-picture-keys');
            const keys = [left, right, ...flips.map(([, b]) => b)], keyRow = turns.querySelector('.fv-ed-adjust-row-controls');
            const fitKeys = () => {
                const width = keyRow?.clientWidth;
                if (!width || typeof document.createRange !== 'function') return;
                const gap = parseFloat(getComputedStyle(keyRow).columnGap) || 0, half = (width - gap) / 2;
                const fits = keys.every(key => {
                    const style = getComputedStyle(key), words = document.createRange(); words.selectNodeContents(key);
                    const edges = ['paddingLeft', 'paddingRight', 'borderLeftWidth', 'borderRightWidth'].reduce((sum, name) => sum + (parseFloat(style[name]) || 0), 0);
                    return words.getBoundingClientRect().width + edges <= half + .5;
                });
                if (fits) delete host.dataset.fvKeys; else host.dataset.fvKeys = 'column';
            };
            keysObserver = typeof ResizeObserver === 'function' && keyRow ? new ResizeObserver(fitKeys) : null;
            keysObserver?.observe(keyRow); fitKeys(); document.fonts?.ready?.then(fitKeys);
            tipped.push([numbers.rotate.control.input, 'rotate']);
            for (const [node, id] of tipped) applyTip(node, id, t);
            visibility();
        }, update, deactivate: () => session.setPanelOpen(false),
        dispose() { visibilityObserver?.disconnect(); keysObserver?.disconnect(); session.setPanelOpen(false); unsubscribe(); window.removeEventListener('pointermove', dragScale, true); window.removeEventListener('pointerup', endScale, true); window.removeEventListener('pointercancel', endScale, true); if (scale) scale.input.onpointerdown = null; for (const w of widgets) { if(w.input) {w.input.onkeydown = null; w.input.onblur = null;} w.dispose(); } },
    };
}
