// Two-pass sampling as three choices: off, on, or on and paused after a half-resolution first pass.
// The creative panel and the editor's generation bar show this one control over the same node settings.
const plainListen = (target, type, handler) => {
    target.addEventListener(type, handler);
    return () => target.removeEventListener(type, handler);
};

export function createPassMode({t, onChoose, listen = plainListen}) {
    const element = document.createElement('div');
    element.className = 'fv-pass-mode'; element.setAttribute('role', 'radiogroup');
    element.setAttribute('aria-label', t('Two-pass sampling', '二次采样'));
    const options = [['off', t('Off', '关闭')], ['on', t('On', '开启')], ['preview', t('Preview first', '先出预览')]].map(([mode, label]) => {
        const option = document.createElement('button');
        option.type = 'button'; option.textContent = label; option.dataset.mode = mode; option.setAttribute('role', 'radio');
        option.onclick = () => onChoose(mode);
        return option;
    });
    element.append(...options);
    const stop = listen(element, 'keydown', event => {
        const step = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[event.key];
        if (!step) return;
        event.preventDefault();
        const enabled = options.filter(option => !option.disabled && !option.hidden);
        const current = enabled.findIndex(option => option.getAttribute('aria-checked') === 'true');
        const next = enabled[(current + step + enabled.length) % enabled.length];
        if (next) { next.click(); next.focus(); }
    });
    // state: {mode, disabled: {off, on, preview}, hidden: {preview}, hint}; the hint is the preview choice's title.
    function sync(state) {
        for (const option of options) {
            const key = option.dataset.mode, selected = key === state.mode;
            option.setAttribute('aria-checked', String(selected)); option.tabIndex = selected ? 0 : -1;
            option.disabled = Boolean(state.disabled?.[key]);
            option.hidden = Boolean(state.hidden?.[key]);
            option.title = key === 'preview' ? state.hint || '' : '';
        }
    }
    return {element, options, sync, dispose: stop};
}
