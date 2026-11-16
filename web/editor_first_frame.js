import {firstFrameRow} from './first_frame_row.js';
import {tip} from './editor_tips.js';
// "First-frame reference": one switch for continued generation, kept with the project.
export const FIRST_FRAME_KEY = 'first_frame_reference';
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
export const firstFrameOn = record => record.settings?.[FIRST_FRAME_KEY] === true;
// actions.firstFrameVisible(): the selection continues earlier footage and this machine can;
// actions.setFirstFrame(value): saves the choice with the project.
export function createFirstFrameSwitch(host, {state, t, actions}) {
    const {row, input, setDisabled} = firstFrameRow(t, 'fv-ed-first-frame');
    row.dataset.tourId = 'editor-first-frame'; host.append(row);
    tip(row, {name: t('First-frame reference', '首帧参考'), use: t('Start from the last frame of the part before', '从上一段的最后一帧接着生成')});
    let shown = false;
    // While generating the row stays as it was and cannot be changed.
    function update(busy = false) {
        if (!busy) shown = !!actions.firstFrameVisible?.();
        row.hidden = !shown;
        input.checked = firstFrameOn(state.record);
        setDisabled(busy);
        tip(row, {name: t('First-frame reference', '首帧参考'), use: t('Start from the last frame of the part before', '从上一段的最后一帧接着生成'), why: busy ? t('This part is generating', '这一段正在生成') : ''});
    }
    input.onchange = () => { try { actions.setFirstFrame(input.checked); } catch (error) { actions.error?.(error); } finally { input.checked = firstFrameOn(state.record); } };
    update();
    return {element: row, update};
}
