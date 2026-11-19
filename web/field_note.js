// A number field never raises the browser's own check bubble. A typed value outside the field's range is brought to
// the nearest allowed one, and a line under the field says so ("At most 31"); a value that cannot be brought in (a
// rule between two fields) is said on the same line. The line goes at the next edit.
let styled = false;
function style() {
    if (styled || typeof document === 'undefined') return;
    styled = true;
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('./field_note.css', import.meta.url).href;
    document.head.append(css);
}

const finite = text => text !== '' && text != null && Number.isFinite(Number(text));
// Enough digits for a step like .05 or .1, without the float noise of the arithmetic.
const tidy = (n, step) => {
    const places = Math.min(10, (String(step).split('.')[1] || '').length);
    return Number(n.toFixed(places));
};

// The allowed value nearest to the typed one, and which limit moved it: 'max', 'min' or 'step'. Null when the value
// is empty, not a number, or already allowed.
export function nearestAllowed(input) {
    if (!finite(input.value)) return null;
    const typed = Number(input.value);
    const min = finite(input.min) ? Number(input.min) : -Infinity, max = finite(input.max) ? Number(input.max) : Infinity;
    let value = typed, kind = null;
    if (value > max) { value = max; kind = 'max'; } else if (value < min) { value = min; kind = 'min'; }
    if (finite(input.step) && Number(input.step) > 0) {
        const step = Number(input.step), base = Number.isFinite(min) ? min : 0;
        let snapped = tidy(base + Math.round((value - base) / step) * step, step);
        if (snapped > max) snapped = tidy(snapped - step, step);
        if (snapped < min) snapped = tidy(snapped + step, step);
        if (snapped !== value) { value = snapped; kind ??= 'step'; }
    }
    return kind && value !== typed ? {value, kind} : null;
}

// note = fieldNote(input, {t, place, limits}); note.settle() → true when the field holds an allowed value.
// place: the element the line goes after (default: the field's label). limits: {max(n), min(n), step(n)} words.
// className: an extra class, for a line that sits among other lines styled as one (the console's status lines).
export function fieldNote(input, {t = (en, zh) => en, place = null, limits = {}, className = ''} = {}) {
    let line = null;
    const words = {
        max: n => t(`At most ${n}`, `最多 ${n}`),
        min: n => t(`At least ${n}`, `最少 ${n}`),
        step: n => t(`Changed to ${n}`, `已改为 ${n}`),
        ...limits,
    };
    function say(text) {
        if (!text) { clear(); return; }
        style();
        if (!line) {
            line = document.createElement('p'); line.className = `fv-field-why ${className}`.trim(); line.setAttribute('role', 'status');
            line.id = `fv-field-why-${Math.random().toString(36).slice(2, 10)}`;
        }
        const after = (typeof place === 'function' ? place() : place) || input.closest?.('label, .fv-field') || input;
        if (line.previousElementSibling !== after) after.after(line);
        line.textContent = text; line.hidden = false;
        const described = (input.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
        if (!described.includes(line.id)) input.setAttribute('aria-describedby', [...described, line.id].join(' '));
    }
    function clear() { if (line && !line.hidden) { line.hidden = true; line.textContent = ''; } }
    input.addEventListener('input', clear);
    return {
        say, clear,
        get line() { return line; },
        // Brings an out-of-range value in and says so; a rule it cannot satisfy is said, and false returned.
        // An empty field is false and left to the caller, which knows whether empty is allowed.
        settle({quiet = false} = {}) {
            const moved = nearestAllowed(input);
            if (moved) {
                input.value = String(moved.value);
                if (!quiet) say(words[moved.kind](moved.value));
            }
            if (input.value === '') return false;
            if (input.checkValidity()) return true;
            // A rule of ours carries its own words; anything else gets ours, not the browser's.
            if (!quiet) say(input.validity.customError ? input.validationMessage : t('Enter a number in range', '请输入范围内的数'));
            return false;
        },
    };
}
