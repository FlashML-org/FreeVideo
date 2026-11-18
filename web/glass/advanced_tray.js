// The studio's Advanced window: groups the panel's own advanced controls (and the run mode from under Generate) into
// quiet sections that advanced_tray.css lays out as rows: the name on the left, the control on one right edge. It is a
// small window beside the toolbar's settings key, like Frame & size and Quality (tray_pop.js places it); every section
// is in sight, the sampling steps too, and nothing folds.
// Presentation only: every control stays studio.js's own node, with its handlers, values and states. What is added:
// a group title per section, − / + keys around number fields (they press the field's own step and send its events),
// a key that draws a new seed into the seed field, and Reset to defaults in the title row (it presses the panel's own
// controls back to their defaults). If this module does not load, the panel is exactly as studio.js built it.
import {TIPS} from '../tooltip_copy.js';
import {randomSeed} from '../studio_queue.js';
import './tray_pop.js';

const make = (tag, cls, text) => { const element = document.createElement(tag); if (cls) element.className = cls; if (text != null) element.textContent = text; return element; };
const say = (tray, en, zh) => tray.fvAtChinese ? zh : en;
const pick = (tray, pair) => (tray.fvAtChinese ? pair?.[1] : pair?.[0]) || '';
const tipOn = (element, name, use = '', why = '') => {
    element.dataset.fvTipName = name;
    if (use) element.dataset.fvTipUse = use; else delete element.dataset.fvTipUse;
    if (why) element.dataset.fvTipWhy = why; else delete element.dataset.fvTipWhy;
};
const send = (input, ...types) => { for (const type of types) input.dispatchEvent(new Event(type, {bubbles: true})); };

function group(tray, key, en, zh) {
    let section = tray.querySelector(`:scope .fv-at-group[data-at=${key}]`);
    if (section) return section;
    section = make('section', 'fv-at-group'); section.dataset.at = key;
    const title = make('h3', 'fv-at-title', say(tray, en, zh)); title.id = `fv-at-${key}-${Math.random().toString(36).slice(2, 8)}`;
    section.setAttribute('aria-labelledby', title.id); section.append(title);
    return section;
}

// A number field between − and + keys of the same height; the keys step the field and send its own input and change.
function stepper(tray, input, {unit = '', why = ''} = {}) {
    if (!input || input.closest('.fv-at-num')) return;
    const wrap = make('span', 'fv-at-num'); input.replaceWith(wrap);
    const name = input.getAttribute('aria-label') || '';
    const key = (direction) => {
        const button = make('button', `fv-at-step fv-at-${direction < 0 ? 'less' : 'more'}`); button.type = 'button'; button.tabIndex = -1;
        button.setAttribute('aria-label', `${direction < 0 ? say(tray, 'Less', '减少') : say(tray, 'More', '增加')} ${name}`.trim());
        button.onclick = () => {
            if (input.disabled) return;
            const before = input.value;
            try { if (input.value === '') input.value = input.min || '0'; else if (direction < 0) input.stepDown(); else input.stepUp(); } catch { return; }
            if (input.value !== before) send(input, 'input', 'change');
            paint();
        };
        return button;
    };
    const less = key(-1), more = key(1);
    // A unit reads after the number, inside the same box ("15 s").
    if (unit) { const middle = make('span', 'fv-at-mid'); middle.append(input, make('span', 'fv-at-unit', unit)); middle.querySelector('.fv-at-unit').setAttribute('aria-hidden', 'true'); wrap.append(less, middle, more); }
    else wrap.append(less, input, more);
    const paint = () => {
        const value = Number(input.value), min = input.min === '' ? -Infinity : Number(input.min), max = input.max === '' ? Infinity : Number(input.max);
        less.disabled = input.disabled || (input.value !== '' && value <= min);
        more.disabled = input.disabled || (input.value !== '' && value >= max);
        // A greyed field says why in its hover card (studio.js writes the reason into its title; else the tray's own).
        const reason = input.title || (input.disabled ? why : '');
        for (const button of [less, more]) { if (reason) button.dataset.fvTipWhy = reason; else delete button.dataset.fvTipWhy; }
    };
    new MutationObserver(paint).observe(input, {attributes: true, attributeFilter: ['disabled', 'min', 'max', 'value', 'title']});
    input.addEventListener('input', paint); input.addEventListener('change', paint);
    input.fvAtPaint = paint; paint();
}

// One row: a field's name on the left, its control (in a box of one width) on the right edge.
function row(label) { if (label) label.classList.add('fv-at-row'); return label; }

function markRunMode(tray, dialog) {
    const menu = dialog.querySelector('.fv-run-menu');
    const buttons = menu ? [...menu.querySelectorAll(':scope > button[data-mode]')] : [];
    if (!buttons.length) return null;
    const section = group(tray, 'mode', 'Generation mode', '生成方式');
    const count = menu.querySelector(':scope > label.fv-field'), help = menu.querySelector(':scope > p.fv-muted');
    const segments = make('div', 'fv-at-segments'); segments.setAttribute('role', 'group'); segments.setAttribute('aria-label', say(tray, 'Generation mode', '生成方式'));
    segments.append(...buttons);
    const parts = say(tray, 'Videos made in parts cannot use batch or loop.', '分段视频不能批量或循环生成。');
    for (const button of buttons) {
        const copy = TIPS[`studio.mode.${button.dataset.mode}`];
        const use = button.dataset.mode === 'loop' ? say(tray, 'New takes until stopped or this page closes', '一直生成新版本，直到停止或关闭网页') : pick(tray, copy?.use);
        tipOn(button, pick(tray, copy?.name) || button.textContent, use, button.dataset.mode === 'single' ? '' : parts);
    }
    section.append(segments);
    // The count row stays in place (greyed unless Batch), so the section keeps its height when the mode changes.
    if (count) { section.append(row(count)); count.classList.add('fv-at-keep'); stepper(tray, count.querySelector('input'), {why: say(tray, 'Choose Batch to set the number', '选“批量”后可设数量')}); }
    // One prompt per line (Batch only) sits under the count; greyed outside Batch, like the count.
    const lines = menu.querySelector(':scope > label.fv-batch-lines');
    if (lines) section.append(row(lines));
    // The mode's standing sentence is in the keys' hover cards; the line stays in the page for screen readers.
    if (help) { help.classList.add('fv-at-said'); section.append(help); }
    dialog.querySelector('.fv-runrow')?.classList.add('fv-at-moved');
    return section;
}

function markSeed(tray, fields) {
    const section = group(tray, 'seed', 'Random seed', '随机种子');
    fields.classList.add('fv-at-rows'); fields.style.marginTop = '';
    const labels = [...fields.querySelectorAll(':scope > label.fv-field')];
    labels.forEach(row);
    const seed = labels[0]?.querySelector('input[type=number]');
    if (seed && !seed.closest('.fv-at-seed')) {
        const wrap = make('span', 'fv-at-seed'); seed.replaceWith(wrap);
        const again = make('button', 'fv-at-reroll'); again.type = 'button';
        again.setAttribute('aria-label', say(tray, 'Random seed', '随机换一个'));
        tipOn(again, say(tray, 'Random seed', '随机换一个'));
        again.onclick = () => { if (seed.disabled) return; seed.value = String(randomSeed()); send(seed, 'input', 'change'); };
        const paint = () => { again.disabled = seed.disabled; };
        new MutationObserver(paint).observe(seed, {attributes: true, attributeFilter: ['disabled']}); paint();
        wrap.append(seed, again);
    }
    section.append(fields);
    return section;
}

// Sampling steps: a section like the others, always in sight. The caution line shows only while the steps are set by hand.
function markSteps(tray, fields, warning) {
    const section = group(tray, 'steps', 'Sampling steps', '采样步数');
    fields.classList.add('fv-at-rows'); fields.style.marginTop = '';
    const names = [say(tray, 'First-pass steps', '一采步数'), say(tray, 'Second-pass steps', '二采步数')];
    const caution = say(tray, 'Changing steps may lower quality.', '改步数可能降低质量。');
    for (const [index, label] of [...fields.querySelectorAll(':scope > label.fv-field')].entries()) {
        row(label); const input = label.querySelector('input'); stepper(tray, input);
        const caption = label.querySelector(':scope > span'); caption?.classList.add('fv-at-off');
        const name = make('span', 'fv-at-name', names[index] || caption?.textContent || ''); label.prepend(name);
        if (input) tipOn(label, name.textContent, caution);
    }
    section.append(fields);
    // One short caution, only while the steps are set by hand; the panel's longer line stays for screen readers.
    if (warning) { warning.classList.add('fv-at-off'); section.append(warning); }
    section.append(make('p', 'fv-at-note fv-at-caution', caution));
    return section;
}

// Long video: two choices, Auto parts or One run (One run is the panel's own "Force a single run" box, pressed through
// its own click); with Auto parts, the part length and one grey line that says what this duration becomes.
function markLength(tray, controls) {
    const section = group(tray, 'length', 'Long video', '长视频');
    controls.classList.add('fv-at-rows'); controls.style.marginTop = '';
    const toggle = controls.querySelector(':scope > label:not(.fv-field)'), box = toggle?.querySelector('input[type=checkbox]');
    // The always-true sentence under the box is in the keys' hover cards; the reason it cannot be used stays as a line.
    const [explanation, ...reasons] = controls.querySelectorAll(':scope > p.fv-muted');
    explanation?.classList.add('fv-at-said');
    for (const p of reasons) p.classList.add('fv-at-note');
    if (toggle && box) {
        toggle.classList.add('fv-at-off');
        const segments = make('div', 'fv-at-segments'); segments.dataset.at = 'length'; segments.setAttribute('role', 'group'); segments.setAttribute('aria-label', say(tray, 'Long video', '长视频'));
        for (const [id, en, zh, useEn, useZh] of [['auto', 'Auto parts', '自动分段', 'Longer videos are made in parts and joined', '较长的视频分段生成后接起来'],
            ['single', 'One run', '一次生成', 'Made in one run, without parts', '不分段，一次生成整段']]) {
            const key = make('button', '', say(tray, en, zh)); key.type = 'button'; key.dataset.length = id;
            tipOn(key, say(tray, en, zh), say(tray, useEn, useZh));
            key.onclick = () => { if ((id === 'single') !== box.checked && !box.disabled) box.click(); };
            segments.append(key);
        }
        toggle.before(segments);
    }
    const field = controls.querySelector(':scope > label.fv-field');
    if (field) {
        row(field); field.classList.add('fv-at-part');
        const caption = field.querySelector(':scope > span'); caption?.classList.add('fv-at-off');
        field.prepend(make('span', 'fv-at-name', say(tray, 'Longest part', '每段最长')));
        stepper(tray, field.querySelector('input'), {unit: say(tray, 's', '秒')});
        tipOn(field, say(tray, 'Longest part', '每段最长'), say(tray, 'A longer video is made in parts of up to this length', '更长的视频按这个长度分段生成'));
        // Back to automatic: a small key in front of the field, only while a length of its own is set.
        const reset = controls.querySelector(':scope > button.fv-quiet');
        if (reset) { reset.classList.add('fv-at-unset'); reset.setAttribute('aria-label', reset.textContent.trim()); field.querySelector('.fv-at-num')?.before(reset); }
        const plan = make('p', 'fv-at-note fv-at-plan'); plan.setAttribute('aria-live', 'polite'); field.after(plan);
    }
    // A problem that stops Generate is said here, on the row it concerns, in the words of the Generate key's hover card.
    const problem = make('p', 'fv-at-note fv-at-problem fv-at-gone'); problem.setAttribute('role', 'status'); controls.append(problem);
    section.append(controls);
    return section;
}

// What this duration becomes with Auto parts: the planned number of parts when the Generate key already shows it,
// else the duration over the part length. Writes only what changed (the tray's watcher sees pressed and disabled).
function paintLength(tray, dialog) {
    const section = tray.querySelector('.fv-at-group[data-at=length]'); if (!section) return;
    const box = section.querySelector('input[type=checkbox]'), block = section.querySelector('.fv-at-part input');
    const one = Boolean(box?.checked), reason = box?.title || '';
    for (const key of section.querySelectorAll('.fv-at-segments[data-at=length] > button')) {
        const pressed = String((key.dataset.length === 'single') === one), off = Boolean(box?.disabled) && pressed === 'false';
        if (key.getAttribute('aria-pressed') !== pressed) key.setAttribute('aria-pressed', pressed);
        if (key.disabled !== off) key.disabled = off;
        if (off && reason) key.dataset.fvTipWhy = reason; else delete key.dataset.fvTipWhy;
    }
    const part = section.querySelector('.fv-at-part');
    if (part && part.classList.contains('fv-at-gone') !== one) part.classList.toggle('fv-at-gone', one);
    const toggle = (element, cls, on) => { if (element && element.classList.contains(cls) !== on) element.classList.toggle(cls, on); };
    const seconds = Number(dialog.querySelector('.fvs-duration input[type=number]')?.value), each = Number(block?.value);
    // The parts this duration becomes: the planned number when the Generate key already shows it, else the duration over
    // the longest part. A duration that fits in one part leaves the field greyed and says nothing.
    let count = 0;
    if (!one && block && seconds > 0 && each > 0) {
        const shown = (dialog.querySelector('.fvs-key-eta')?.textContent || '').match(/分\s*(\d+)\s*段|(\d+)\s*parts/);
        count = shown ? Number(shown[1] || shown[2]) : seconds <= each ? 1 : Math.ceil(seconds / each);
    }
    const idle = !one && count <= 1;
    toggle(part, 'fv-at-idle', idle);
    const field = part?.querySelector('.fv-at-num');
    if (field && field.hasAttribute('inert') !== idle) { field.toggleAttribute('inert', idle); field.setAttribute('aria-disabled', String(idle)); }
    const plan = section.querySelector('.fv-at-plan');
    if (plan) {
        const time = Number.isInteger(seconds) ? String(seconds) : seconds.toFixed(1);
        // More than eight parts is refused by the planner, which says so itself (below, as the problem line).
        const text = !one && block && !block.disabled && count > 1 && count <= 8
            ? say(tray, `${time} s this time · made in ${count} parts and joined`, `这次 ${time} 秒 · 分 ${count} 段生成后接起来`) : '';
        if (plan.textContent !== text) plan.textContent = text;
        toggle(plan, 'fv-at-gone', !text);
    }
    // The planner's refusal (the line studio_length writes beside the duration), on the row it concerns.
    const refusal = dialog.querySelector('.fv-length-plan:not([hidden])'), said = refusal ? (refusal.firstChild?.textContent || refusal.textContent || '').trim() : '';
    const problem = section.querySelector('.fv-at-problem');
    if (problem) { if (problem.textContent !== said) problem.textContent = said; toggle(problem, 'fv-at-gone', !said); }
    toggle(part, 'fv-at-bad', Boolean(said) && /每段时长|part length/i.test(said));
    toggle(section.querySelector('.fv-at-segments[data-at=length]'), 'fv-at-bad', Boolean(said) && /单次|single run/i.test(said));
}

// ── Reset to defaults: shown only while something differs; it presses the panel's own controls. ──
function state(tray, dialog) {
    const mode = tray.querySelector('.fv-at-group[data-at=mode] .fv-at-segments button[aria-pressed=true]')?.dataset.mode || 'single';
    const single = tray.querySelector('.fv-at-group[data-at=length] input[type=checkbox]');
    const unset = tray.querySelector('.fv-at-unset');
    const seedMode = tray.querySelector('.fv-at-group[data-at=seed] select');
    // The Quality key in the console's row says Custom while the steps are set by hand (its small window, or the older tray chip).
    const quality = (dialog.querySelector(`.fvs-chips .fvp-chip[aria-label="${say(tray, 'Quality', '质量')}"] .fvp-chip-label`)
        || dialog.querySelector('.fvs-chip[data-chip=quality] .fvs-chip-label'))?.textContent.trim() || '';
    return {
        mode: mode !== 'single',
        single: Boolean(single && single.checked && !single.disabled),
        length: Boolean(unset && !unset.hidden && !unset.disabled),
        seed: Boolean(seedMode && !seedMode.disabled && seedMode.value !== 'randomize'),
        steps: /^(Custom|自定义)$/.test(quality),
    };
}
function paintReset(tray, dialog) {
    paintLength(tray, dialog);
    const reset = tray.fvAtReset; if (!reset) return;
    const now = state(tray, dialog), changed = Object.values(now).some(Boolean);
    // Written only when it changes: the tray's watcher sees "hidden", and an equal write would call it again.
    if (reset.hidden === changed) reset.hidden = !changed;
    const steps = tray.querySelector('.fv-at-group[data-at=steps]');
    if (steps && steps.classList.contains('fv-at-custom') !== now.steps) steps.classList.toggle('fv-at-custom', now.steps);
}
function resetAll(tray, dialog) {
    const now = state(tray, dialog), byKeys = tray.fvAtReset.matches(':focus-visible');
    if (now.mode) tray.querySelector('.fv-at-group[data-at=mode] button[data-mode=single]')?.click();
    if (now.single) tray.querySelector('.fv-at-group[data-at=length] input[type=checkbox]')?.click();
    if (now.length) tray.querySelector('.fv-at-unset')?.click();
    if (now.seed) { const select = tray.querySelector('.fv-at-group[data-at=seed] select'); select.value = 'randomize'; send(select, 'change'); }
    if (now.steps) {
        // Steps typed by hand go back to the default quality level, through the Quality tray's own first level key.
        const level = dialog.querySelector('.fvs-tray[data-tray=quality] .fv-effort-name');
        if (level && !level.disabled) level.click();
        else {
            const [base, refine] = tray.querySelectorAll('.fv-at-group[data-at=steps] input[type=number]');
            if (base && !base.disabled) { base.value = '8'; send(base, 'input', 'change'); }
            if (refine && !refine.disabled) { refine.value = '3'; send(refine, 'input', 'change'); }
        }
    }
    // A key that goes away while used from the keyboard hands its focus to the tray's Close key, so it stays in the tray.
    requestAnimationFrame(() => { paintReset(tray, dialog); if (byKeys && tray.fvAtReset.hidden) tray.querySelector('.fvs-tray-close')?.focus({preventScroll: true}); });
}

function mark(tray) {
    const dialog = tray.closest('dialog') || document;
    const body = tray.querySelector(':scope > .fv-expand'); if (!body) return;
    tray.fvAtChinese ??= /[一-鿿]/.test(tray.querySelector('.fvs-tray-title')?.textContent || '');
    tray.classList.add('fv-at');
    body.classList.add('fv-at-body');
    const length = body.querySelector(':scope > .fv-length-controls');
    const fieldsets = [...body.querySelectorAll(':scope > .fv-fields')];
    const steps = fieldsets.find(fields => fields.querySelector('input[aria-describedby^="fv-sampling-warning"]'));
    const seed = fieldsets.find(fields => fields !== steps && fields.querySelector('select'));
    const warning = steps && body.querySelector(':scope > p[id^="fv-sampling-warning"]');
    const sections = [markRunMode(tray, dialog)].filter(Boolean);
    if (seed) sections.push(markSeed(tray, seed));
    if (length) sections.push(markLength(tray, length));
    if (steps) sections.push(markSteps(tray, steps, warning));
    // Sections in reading order at the top; anything else the panel adds later stays below them, as it was.
    let at = null;
    for (const section of sections) { if (at) at.after(section); else if (body.firstElementChild !== section) body.prepend(section); at = section; }
    if (!tray.fvAtReset) {
        const head = tray.querySelector(':scope > .fvs-tray-head');
        const reset = tray.fvAtReset = make('button', 'fv-at-reset', say(tray, 'Reset to defaults', '恢复默认')); reset.type = 'button'; reset.hidden = true;
        tipOn(reset, say(tray, 'Reset to defaults', '恢复默认'), say(tray, 'Single, random seed, standard steps, auto parts', '单次、随机种子、默认步数、自动分段'));
        reset.onclick = () => resetAll(tray, dialog);
        head?.querySelector('.fvs-tray-close')?.before(reset);
        const repaint = () => requestAnimationFrame(() => paintReset(tray, dialog));
        for (const type of ['input', 'change', 'click']) tray.addEventListener(type, repaint);
        // The duration lives in the Frame & size tray; the parts line follows it.
        const duration = dialog.querySelector('.fvs-duration input[type=number]');
        for (const type of ['input', 'change']) duration?.addEventListener(type, repaint);
        tray.addEventListener('toggle', () => {
            if (!tray.open) return;
            for (const input of tray.querySelectorAll('.fv-at-num input')) input.fvAtPaint?.();
            repaint();
        });
        new MutationObserver(repaint).observe(tray, {subtree: true, attributes: true, attributeFilter: ['aria-pressed', 'hidden', 'disabled']});
    }
    paintReset(tray, dialog);
}

function scan() {
    for (const tray of document.querySelectorAll('dialog.fv-create .fvs-tray[data-tray=advanced]')) {
        if (tray.fvAtDone) continue;
        // Wait until the run row exists too (it is built after the tray's fields).
        if (!tray.closest('dialog')?.querySelector('.fv-run-menu > button[data-mode]')) continue;
        tray.fvAtDone = true;
        try { mark(tray); } catch (error) {
            console.warn('FreeVideo advanced tray:', error);
            // The run mode not moved in: it stays usable under Generate.
            const dialog = tray.closest('dialog');
            if (dialog?.querySelector('.fv-run-menu > button[data-mode]')) dialog.querySelector('.fv-runrow')?.classList.remove('fv-at-moved');
        }
    }
}

if (!globalThis.FreeVideoAdvancedTray) {
    globalThis.FreeVideoAdvancedTray = {scan};
    if (!document.querySelector('link[data-fv-sheet="advanced-tray"]')) {
        const sheet = document.createElement('link'); sheet.rel = 'stylesheet'; sheet.dataset.fvSheet = 'advanced-tray';
        sheet.href = new URL('./advanced_tray.css', import.meta.url).href; document.head.append(sheet);
    }
    let timer = 0;
    new MutationObserver(() => { if (!timer) timer = setTimeout(() => { timer = 0; scan(); }, 150); }).observe(document.body, {childList: true, subtree: true});
    scan();
}
