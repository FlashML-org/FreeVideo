// "Duration [n] s" in the generation bar: the length of the parts not generated yet, or of a range drawn on v1.
// A typed length is applied as dragging the right edge would apply it: whole seconds, the same part rules, one step to undo.
import * as model from './editor_model.js';
const {FPS, RULES} = model;
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const end = clip => clip.start + clip.frames;
const draft = clip => clip.pending != null && !clip.source;
const running = clip => ['queued', 'running'].includes(clip.pending);
const limitsOf = actions => actions.generationLimits?.() || {};

// What the field stands for: one run of parts not generated yet on v1, or a range on v1; null hides the field.
export function durationTarget(record, selection) {
    if (!selection) return null;
    if (selection.kind === 'range') return selection.tracks.includes('v1') ? {kind: 'range', frames: selection.frames, busy: false} : null;
    if (selection.kind !== 'clip') return null;
    const video = record.tracks.find(track => track.id === 'v1');
    const clips = (video?.clips || []).filter(clip => selection.ids.includes(clip.id)).sort((a, b) => a.start - b.start);
    if (!clips.length || !clips.every(draft) || !selection.ids.every(id => clips.some(clip => clip.id === id || clip.link === id))) return null;
    if (!clips.every((clip, i) => !i || end(clips[i - 1]) === clip.start && model.continuesBefore(record, clip.id))) return null;
    return {kind: 'parts', clips, frames: clips.reduce((sum, clip) => sum + clip.frames, 0), busy: clips.some(running)};
}

// The length a typed number becomes, why it was changed (`hint`), and the edit or selection that applies it.
export function durationPlan(record, selection, seconds, limits = {}) {
    const target = durationTarget(record, selection);
    if (!target || target.busy || typeof seconds !== 'number' || !Number.isFinite(seconds)) return null;
    const requested = Math.round(seconds), current = Math.round(target.frames / FPS);
    const clamp = (least, most) => {
        const value = Math.max(least, Math.min(most, requested));
        return {value, hint: requested < least ? {least} : requested > most ? {most} : null};
    };
    if (target.kind === 'range') {
        // A range drawn on its own starts a new video: at least the general minimum, at most the parts one video may have.
        const {value, hint} = clamp(RULES.minSeconds, Math.floor(model.maxRangeFrames(record, {...limits, continues: false}) / FPS));
        const {parts, ...rest} = selection;
        return {value, hint, current, selection: value === current ? null : {...rest, frames: value * FPS}};
    }
    const [first, ...later] = target.clips, laterIds = later.map(clip => clip.id);
    const base = laterIds.length ? model.dropPlanned(record, laterIds) : record;
    const oldIds = new Set(base.tracks[0].clips.map(clip => clip.id));
    const runOf = r => r.tracks[0].clips.filter(clip => clip.id === first.id || !oldIds.has(clip.id));
    // Ask the very same model operation as a right-edge drag, then report the length it actually accepted.
    const stop = Math.max(0, Math.min(1e7, first.start + requested * FPS));
    const refused = problem => Object.assign(new model.EditError(problem.code), problem);
    const planned = model.planPlannedEnd(base, first.id, stop, limits), resized = planned.record;
    // A failed resize returns its input. For several selected parts that input has already lost the later parts:
    // never submit it, even when Enter was pressed on the original value.
    if (resized === base) return {value: current, hint: null, current, change: null, error: refused(planned.problem)};
    const laidOut = r => {
        const base = laterIds.length ? model.dropPlanned(r, laterIds) : r;
        const next = model.planPlannedEnd(base, first.id, stop, limits);
        if (next.record === base) throw refused(next.problem);
        return next.record;
    };
    const value = runOf(resized).reduce((sum, clip) => sum + clip.frames, 0) / FPS;
    const hint = requested < planned.least ? {least: planned.least} : requested > planned.most ? {most: planned.most} : null;
    if (value === current) return {value, hint, current, change: null};
    // Each part laid out again keeps the description of the part that stood in its place; parts added at the end take the last one's.
    const change = r => keepWords(r, r === record ? resized : laidOut(r), target.clips.map(clip => clip.id), runOf);
    const result = change(record);
    const ids = runOf(result).flatMap(clip => model.pair(result, clip.id).map(member => member.id));
    return {value, hint, current, change, selectionAfter: {kind: 'clip', ids}};
}

// What a part says: its description, who wrote it and its note, on each of its tracks.
const WORDS = ['text', 'text_by', 'note'];
function keepWords(before, after, oldIds, runOf) {
    const trackId = (record, id) => record.tracks.find(track => track.clips.some(clip => clip.id === id)).id;
    const wordsOf = id => new Map(model.pair(before, id).map(clip => [trackId(before, clip.id), clip]));
    const olds = oldIds.map(wordsOf), run = runOf(after);
    const differs = run.some((clip, i) => model.pair(after, clip.id).some(member => {
        const old = olds[Math.min(i, olds.length - 1)].get(trackId(after, member.id));
        return old && WORDS.some(field => old[field] !== member[field]);
    }));
    if (!differs) return after;
    const next = model.serialize(after), clips = next.tracks.flatMap(track => track.clips.map(clip => [clip, track.id]));
    run.forEach((clip, i) => {
        const old = olds[Math.min(i, olds.length - 1)];
        for (const member of model.pair(after, clip.id)) {
            const [target, track] = clips.find(([item]) => item.id === member.id), from = old.get(track);
            if (!from) continue;
            for (const field of WORDS) { if (from[field] === undefined) delete target[field]; else target[field] = from[field]; }
        }
    });
    return model.parse(next);
}

// actions.generationLimits(): what this machine generates in one part; actions.editError(error): an edit that could not be made.
export function createDurationField({state, t, actions}) {
    const element = el('div', 'fv-ed-duration'), label = el('label');
    const input = el('input'); input.type = 'number'; input.step = '1'; input.min = '1'; input.inputMode = 'numeric';
    input.setAttribute('aria-label', t('Duration in seconds', '时长（秒）'));
    label.append(el('span', 'fv-ed-duration-word', t('Duration', '时长')), input, el('span', 'fv-ed-duration-unit', t('s', '秒')));
    // Styled as the timeline's drag label, with its sentences, above the field.
    const hint = el('div', 'fv-ed-duration-hint'); hint.hidden = true; hint.setAttribute('role', 'status');
    element.append(label, hint); element.hidden = true;
    // `typing`: a number typed and not applied yet, which a redraw of the bar keeps.
    let hintTimer = null, disposed = false, typing = false;
    const shown = () => { const target = durationTarget(state.record, state.selection); return target ? String(Math.round(target.frames / FPS)) : ''; };
    function say(text) {
        clearTimeout(hintTimer); hint.textContent = text; hint.hidden = false;
        hintTimer = setTimeout(() => { hint.hidden = true; }, 2000);
    }
    function commit() {
        if (disposed || element.hidden) return;
        typing = false;
        if (input.disabled) { input.value = shown(); return; }
        const typed = String(input.value ?? '').trim(), seconds = Number(typed);
        try {
            const plan = typed && Number.isFinite(seconds) ? durationPlan(state.record, state.selection, seconds, limitsOf(actions)) : null;
            if (!plan) { input.value = shown(); return; }
            clearTimeout(hintTimer); hint.hidden = true;
            if (plan.error) throw plan.error;
            if (plan.hint?.least != null) say(t(`At least ${plan.hint.least} s`, `至少 ${plan.hint.least} 秒`));
            else if (plan.hint?.most != null) say(t(`Max ${plan.hint.most} s`, `最长 ${plan.hint.most} 秒`));
            if (plan.change) state.apply(t('Extend', '拉长'), plan.change, {selectionAfter: plan.selectionAfter});
            else if (plan.selection) state.select(plan.selection, {history: true});
        } catch (error) {
            clearTimeout(hintTimer); hint.hidden = true;
            actions.editError?.(error);
        }
        input.value = shown();
    }
    // Typing alone leaves the timeline as it is: a "1" on the way to "14" is not applied.
    input.onkeydown = event => {
        if (event.key === 'Enter' && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); commit(); return; }
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
        event.preventDefault();
        const typed = Number(String(input.value ?? '').trim() || shown());
        input.value = String((Number.isFinite(typed) ? Math.round(typed) : Number(shown())) + (event.key === 'ArrowUp' ? 1 : -1));
        commit();
    };
    input.oninput = () => { typing = true; };
    input.onblur = () => commit();
    // The browser's own step buttons.
    input.onchange = () => commit();
    // While these parts are made the field stays in place, greyed like the description.
    function update(busy = false) {
        const target = durationTarget(state.record, state.selection);
        element.hidden = !target;
        if (!target) { clearTimeout(hintTimer); hint.hidden = true; return; }
        input.disabled = busy || target.busy;
        if (input.disabled) typing = false;
        if (!typing) input.value = String(Math.round(target.frames / FPS));
    }
    return {element, input, update, commit, dispose() { disposed = true; clearTimeout(hintTimer); }};
}
