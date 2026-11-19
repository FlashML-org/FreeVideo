const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; };
// The label and switch alone: the name, the hover text and nothing else.
// Shared by the studio and the editing view; neither pulls in the other.
export function firstFrameRow(t, className) {
    const row = el('label', className, t('First-frame reference', '首帧参考'));
    const input = el('input'); input.type = 'checkbox'; input.setAttribute('role', 'switch');
    input.setAttribute('aria-label', t('First-frame reference', '首帧参考'));
    row.append(input);
    row.dataset.fvTipName = t('First-frame reference', '首帧参考');
    row.dataset.fvTipUse = t('Each part starts from the first frame: less drift', '每段参照首帧，减小画面漂移');
    return {row, input, setDisabled(disabled) {
        if (input.disabled !== disabled) input.disabled = disabled;
        if (input.title !== '') input.title = '';
    }};
}
