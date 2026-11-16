// LoRA for the picture page: a button in the panel that opens a small panel right beside it (the shared setting
// panel of glass/frame_picker.js), holding the image LoRA rows every picture surface uses (image_lora.js: switch, file,
// strength, trigger words; the list is kept in this browser, shared with the picture editor). The engine applies
// LoRA files to Qwen-Image 2.1 only (MiniMax H3 Image takes none), so the button answers for that model and the
// request carries the LoRAs only then. Trigger words are written in by the engine (it adds the ones a prompt lacks),
// so the description box stays the user's own.
import { createPopChip } from './glass/frame_picker.js';
import { createImageLoras } from './image_lora.js';
import { el, t, tip } from './image_view_ui.js';

export const LORA_MODEL = 'qwen-image-2.1-turbo';
// Two stacked layers: one style laid over another.
const GLYPH = 'M8 2.5l5.5 3-5.5 3-5.5-3zM2.5 8.5l5.5 3 5.5-3M2.5 11.5l5.5 3 5.5-3';

export function imageViewLora({host, fetchApi, modelName = () => 'Qwen-Image 2.1', onChange = () => {}}) {
    let model = '';
    // The panel opens above the button (as the frame and size panel does here), so it stays clear of Generate; as rows
    // are added it grows upward from the button.
    const chip = createPopChip({host, name: 'LoRA', icon: GLYPH, kind: 'image-lora', placement: 'above', onOpen: () => { void picker.refresh(); }});
    const picker = createImageLoras({t, fetchApi, onChange: () => { paint(); chip.place(); onChange(); }});
    // The rows live in the panel (they leave the picker's own fold, which this page does not use).
    const rows = picker.element.querySelector('.fv-ie-lora-panel');
    if (rows) chip.body.append(rows);
    const element = el('div', 'fv-iv-lora'); element.append(chip.element);
    const count = () => picker.count();
    function paint(next = model) {
        model = next;
        const on = model === LORA_MODEL, n = count();
        chip.setLabel(n ? `LoRA · ${n}` : 'LoRA');
        chip.element.disabled = !on;
        chip.element.dataset.active = String(on && n > 0);
        tip(chip.element, 'LoRA', t('Add a style or character model', '叠加风格或人物模型'));
        if (on) delete chip.element.dataset.fvTipWhy;
        else { chip.element.dataset.fvTipWhy = t(`LoRA works with ${modelName()}; choose it to use one`, `LoRA 用于 ${modelName()}，选用该模型后可添加`); chip.close(); }
    }
    paint();
    return {element, paint,
        /** The LoRAs to send for this model: the switched-on rows with Qwen-Image 2.1, none otherwise. */
        value: (forModel = model) => forModel === LORA_MODEL ? picker.value() : [],
        dispose() { picker.dispose(); chip.dispose(); }};
}
