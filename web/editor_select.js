// Selection, page-local clipboard and group gestures. No system clipboard access.
import * as model from './editor_model.js';
import * as actionsModel from './editor_actions.js';
import {copyContent, pasteContent, copyRange, pasteRange, playheadTargets, removableGaps as findGaps} from './editor_room.js';
export {copyContent, pasteContent} from './editor_room.js';
import {transportSnapPoints} from './editor_transport_model.js';
import {unlockedIds} from './editor_audio_model.js';
import {isTextTrack, textDropLocked} from './editor_text_model.js';

const end = clip => clip.start + clip.frames;
const busy = clip => ['queued', 'running'].includes(clip.pending);
const all = record => record.tracks.flatMap(track => track.clips);
const selection = ids => ids.length ? {kind: 'clip', ids} : null;
// Parts on a locked track cannot be selected by any gesture, Select all included.
const lockedIds = record => new Set(record.tracks.filter(track => track.locked).flatMap(track => track.clips.map(clip => clip.id)));
export const isTyping = target => !!(target.isContentEditable || target.closest?.('input, textarea, select, [contenteditable="true"], [contenteditable=""], [contenteditable="plaintext-only"]'));
export const selectedIds = (record, value) => actionsModel.linkedIds(record, value?.kind === 'clip' ? value.ids : []);
export function isMultiple(record, ids) {
    const members = all(record).filter(clip => ids.includes(clip.id)), seen = new Set();
    let count = 0;
    for (const clip of members) if (!seen.has(clip.id)) { count++; seen.add(clip.id); seen.add(clip.link); }
    return count > 1;
}
export function clickSelection(record, current, id, {toggle = false, shift = false} = {}) {
    const clip = model.find(record, id);
    if (!clip) return null;
    if (lockedIds(record).has(id)) return current;
    if (toggle || shift) {
        const ids = new Set(selectedIds(record, current)), pair = [...model.linkedIds(record, [id])];
        const remove = ids.has(id);
        for (const member of pair) remove ? ids.delete(member) : ids.add(member);
        return selection([...ids]);
    }
    return selection([...model.linkedIds(record, [id])]);
}
export function rectangleSelection(record, rectangles, box) {
    const locked = lockedIds(record), ids = rectangles.filter(item => !locked.has(item.id) && item.left <= box.right && item.right >= box.left
        && item.top <= box.bottom && item.bottom >= box.top).map(item => item.id);
    return selection([...model.linkedIds(record, ids)]);
}
export function playheadIds(record, value, frame) {
    return playheadTargets(record, selectedIds(record, value), frame);
}
export function splitSelection(record, value, frame) {
    // Keep the page helper's boundary no-op; commands check targets first.
    return playheadIds(record, value, frame).length ? actionsModel.splitAt(record, selectedIds(record, value), frame) : record;
}
export function trimSelection(record, value, frame, edge) {
    return playheadIds(record, value, frame).length ? actionsModel.trimAt(record, selectedIds(record, value), frame, edge) : record;
}
export function removableGaps(record, value) {
    return findGaps(record, value?.kind === 'gap' ? value : null);
}
export function removeGaps(record, value) {
    return actionsModel.removeGaps(record, value?.kind === 'gap' ? value : null);
}
// Share protection between the toolbar, keyboard and external commands.
export function deletionStatus(record, value) {
    if (!value) return {ok: false, reason: 'selection'};
    if (value.kind === 'gap') {
        const result = removeGaps(record, value);
        return result.removed ? {ok: true} : {ok: false, reason: result.skipped[0]?.code || 'no_gap'};
    }
    const ids = new Set(selectedIds(record, value));
    const members = value.kind === 'range' ? record.tracks.filter(track => value.tracks.includes(track.id))
        .flatMap(track => track.clips.filter(clip => clip.start < end(value) && end(clip) > value.start))
        : all(record).filter(clip => ids.has(clip.id));
    return members.some(clip => model.pair(record, clip.id).some(busy))
        ? {ok: false, reason: 'busy'} : {ok: true};
}
export function createClipboard() {
    let value = null;
    return {read: () => value && structuredClone(value), write: content => { value = structuredClone(content); }};
}
const pageClipboard = createClipboard();
export function createSelectionActions({state, t = en => en, actions = {}, clipboard = pageClipboard}) {
    function report(error) {
        if (!(error instanceof model.EditError)) throw error;
        if (error.code === 'not_generated' && actions.notify) actions.notify(t('Not generated yet', '还没生成'));
        else if (error.code !== 'clipboard_empty') actions.editError?.(error);
        return {ok: false, reason: error.code};
    }
    function run(key) {
        try {
            // Ripple delete only differs for parts; a selected gap or range is deleted as with Delete.
            if (key === 'ripple' && state.selection?.kind !== 'clip') key = 'delete';
            const ids = selectedIds(state.record, state.selection);
            if (key === 'a') { state.select(selection(unlockedIds(state.record))); return {ok: true}; }
            if (['b', 'q', 'w'].includes(key) && !playheadIds(state.record, state.selection, state.playhead).length) {
                actions.notify?.(t('Select a clip and move the playhead onto it.', '先选中片段，再把播放头移到片段上。'));
                return {ok: false, reason: 'playhead'};
            }
            if (key === 'b') { state.apply(t('Split', '剪开'), record => actionsModel.splitAt(record, ids, state.playhead)); return {ok: true}; }
            if (key === 'q' || key === 'w') {
                state.apply(key === 'q' ? t('Trim before playhead', '裁掉前面') : t('Trim after playhead', '裁掉后面'),
                    record => actionsModel.trimAt(record, ids, state.playhead, key === 'q' ? 'start' : 'end')); return {ok: true};
            }
            if (key === 'gaps' || (['delete', 'backspace'].includes(key) && state.selection?.kind === 'gap')) {
                const result = actionsModel.removeGaps(state.record, state.selection?.kind === 'gap' ? state.selection : null);
                state.apply(t('Remove gaps', '移除空隙'), () => result.record,
                    result.removed && state.selection?.kind === 'gap' ? {selectionAfter: null} : {});
                return {ok: !!result.removed, ...(result.removed ? {} : {reason: result.skipped[0]?.code || 'no_gap'}), removed: result.removed, skipped: result.skipped};
            }
            if (key === 'ripple') {
                const status = deletionStatus(state.record, state.selection);
                if (!status.ok) return status.reason === 'busy' ? report(new model.EditError(status.reason)) : status;
                const result = actionsModel.rippleRemoveParts(state.record, ids);
                state.apply(t('Ripple delete', '波纹删除'), () => result.record, {selectionAfter: null});
                if (result.skipped.length) actions.notify?.(t('Deleted. The content after it cannot move earlier, so the gap remains.', '已删除。后面的内容无法前移，空隙已保留。'));
                return {ok: true, closed: result.closed, skipped: result.skipped};
            }
            if (key === 'delete' || key === 'backspace') {
                const status = deletionStatus(state.record, state.selection);
                if (!status.ok) return status.reason === 'busy' ? report(new model.EditError(status.reason)) : status;
                if (ids.length) state.apply(t('Delete', '删除'), record => actionsModel.removeParts(record, ids), {selectionAfter: null});
                else if (state.selection?.kind === 'range') state.select(null, {history: true});
                return {ok: true};
            }
            if ((key === 'c' || key === 'x') && state.selection?.kind === 'range') {
                clipboard.write(copyRange(state.selection, state.rangeText));
                if (key === 'x') state.select(null, {history: true});
                return {ok: true};
            }
            const copied = key === 'v' ? clipboard.read() : null;
            if (copied?.range) {
                const pasted = pasteRange(state.record, copied.range, state.playhead), label = t('Paste', '粘贴');
                if (JSON.stringify(pasted.record) !== JSON.stringify(state.record)) state.apply(label, () => pasted.record, {selectionAfter: pasted.range, rangeText: copied.range.text});
                else state.select(pasted.range, {history: true, text: copied.range.text});
                state.seek(pasted.range.start + pasted.range.frames); return {ok: true};
            }
            if (key === 'c' || key === 'x') {
                const content = copyContent(state.record, ids);
                if (key === 'x') state.apply(t('Cut', '剪切'), record => actionsModel.removeParts(record, ids), {selectionAfter: null});
                clipboard.write(content); return {ok: true};
            }
            // Preserve the page clipboard's existing eligibility/error behavior.
            if (key === 'd') copyContent(state.record, ids);
            const pasted = key === 'd' ? actionsModel.duplicateParts(state.record, ids)
                : pasteContent(state.record, copied, state.playhead);
            state.apply(key === 'd' ? t('Duplicate', '创建副本') : t('Paste', '粘贴'), () => pasted.record, {selectionAfter: selection(pasted.ids)});
            state.seek(pasted.end); return {ok: true};
        } catch (error) { return report(error); }
    }
    return {run, report, canPaste: () => !!(clipboard.read()?.clips?.length || clipboard.read()?.range), keyboard(event) {
        if (event.defaultPrevented || event.isComposing || event.keyCode === 229 || isTyping(event.target) || event.altKey) return false;
        const key = event.key.toLowerCase(), command = (event.ctrlKey || event.metaKey) && !event.shiftKey && ['a', 'c', 'x', 'v', 'd', 'b'].includes(key);
        const deleting = ['delete', 'backspace'].includes(key) && ['clip', 'gap', 'range'].includes(state.selection?.kind);
        const trimming = !event.ctrlKey && !event.metaKey && !event.shiftKey && ['q', 'w'].includes(key);
        if (!command && !deleting && !trimming) return false;
        event.preventDefault(); event.stopPropagation(); run(deleting && event.shiftKey ? 'ripple' : key); return true;
    }};
}

// Build once per drag; each preview checks ordered intervals without cloning a
// project, dispatching selection events, or rebuilding clip DOM.
export function prepareMove(record, ids) {
    const selected = model.linkedIds(record, ids), members = all(record).filter(clip => selected.has(clip.id));
    const rows = record.tracks.map(track => ({locked: isTextTrack(track) && track.locked,
        moving: track.clips.filter(clip => selected.has(clip.id)), others: track.clips.filter(clip => !selected.has(clip.id))}));
    const start = Math.min(...members.map(clip => clip.start)), stop = Math.max(...members.map(end));
    return {ids: [...selected], start, end: stop, members, preview(delta, trackId = null) {
        delta = Math.max(-start, delta);
        let code = rows.some(row => row.locked && row.moving.length) || textDropLocked(record, members, trackId) ? 'locked'
            : members.some(busy) ? 'busy' : null;
        for (const {moving, others} of rows) {
            let i = 0;
            for (const clip of moving) {
                while (i < others.length && end(others[i]) <= clip.start + delta) i++;
                if (others[i]?.start < end(clip) + delta) code ||= 'overlap';
            }
        }
        return {delta, code};
    }};
}

export function createSelectionGestures({state, element, content, scroll, clips, rows, hitTest, snap, t, actions, renderSelection}) {
    const commands = createSelectionActions({state, t, actions});
    const make = className => { const node = document.createElement('div'); node.className = className; node.hidden = true; content.append(node); return node; };
    const marquee = make('fv-ed-marquee'), guide = make('fv-ed-group-guide'), outlines = make('fv-ed-marquee-outlines');
    const label = document.createElement('div'); label.className = 'fv-ed-group-label'; label.hidden = true; label.setAttribute('role', 'status'); element.append(label);
    let anchor = null, gesture = null, raf = null, lastEvent = null;
    const pixels = frame => frame * state.zoom / model.FPS;
    const point = event => { const bounds = content.getBoundingClientRect(); return {x: event.clientX - bounds.left, y: event.clientY - bounds.top}; };
    const paint = ids => {
        const selected = new Set(ids);
        for (const [id, item] of clips) {
            const value = selected.has(id);
            if ((item.element.getAttribute('aria-selected') === 'true') !== value) item.element.setAttribute('aria-selected', String(value));
        }
    };
    function choose(id, event = {}) {
        const previousAnchor = anchor;
        const next = clickSelection(state.record, state.selection, id, {toggle: event.ctrlKey || event.metaKey, shift: event.shiftKey, anchor});
        state.select(next); anchor = next?.ids.includes(id) ? id : next?.ids.includes(previousAnchor) ? previousAnchor : next?.ids.at(-1) ?? null;
    }
    function consume(event) { event.preventDefault(); event.stopImmediatePropagation(); }
    // Q2: a box drawn with Ctrl or Cmd adds to the selection; otherwise it replaces it.
    const adding = g => g.modifiers.ctrlKey || g.modifiers.metaKey;
    function begin(event) {
        if (event.button !== 0 || !event.isPrimary || gesture || event.target.closest('.fv-ed-ruler, .fv-ed-playhead-handle, .fv-ed-track-zoom, .fv-ed-clip-popover, .fv-ed-track-heads')) return false;
        const hit = hitTest(event), p = point(event), id = hit.item?.clip.id;
        if (id && lockedIds(state.record).has(id)) return false;
        const inScroll = scroll.contains(event.target), bottom = Math.max(...[...rows.values()].map(row => row.top + row.height));
        const below = inScroll && p.y > bottom;
        const modified = event.ctrlKey || event.metaKey || event.shiftKey;
        const ids = selectedIds(state.record, state.selection);
        const group = id && ids.includes(id) && isMultiple(state.record, ids) && !hit.edge && !hit.neighbor;
        if (id && !modified && !group) { anchor = id; return false; }
        const box = !id && inScroll && (event.shiftKey || below);
        if (!box && !(id && (modified || group))) return false;
        consume(event); element.focus({preventScroll: true}); actions.gestureStart?.({selectionGesture: true});
        gesture = {pointer: event.pointerId, record: state.record, previous: state.selection, start: p, x: event.clientX, y: event.clientY, moved: false,
            type: box ? 'box' : modified ? 'click' : 'move', id, modifiers: {ctrlKey: event.ctrlKey, metaKey: event.metaKey, shiftKey: event.shiftKey}};
        if (box) {
            gesture.rectangles = [...clips].map(([id, item]) => ({id, left: pixels(item.clip.start), right: pixels(item.clip.start) + Math.max(2, pixels(item.clip.frames)), top: rows.get(item.track).top, bottom: rows.get(item.track).top + rows.get(item.track).height}));
            // Preview lightweight outlines; changing selection attributes on
            // hundreds of media subtrees would recalculate their SVG styles.
            gesture.outlines = new Map(gesture.rectangles.map(rect => {
                const node = document.createElement('div'); node.className = 'fv-ed-marquee-item'; node.hidden = true;
                Object.assign(node.style, {left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.right - rect.left}px`, height: `${rect.bottom - rect.top}px`});
                return [rect.id, node];
            }));
            outlines.replaceChildren(...gesture.outlines.values());
        } else if (gesture.type === 'click') choose(id, gesture.modifiers);
        else {
            gesture.plan = prepareMove(state.record, ids); gesture.points = [0, state.playhead, ...model.snapPoints(state.record, ids)]; anchor = id;
            // Move one layer per track, not hundreds of SVG waveforms. Existing
            // clip nodes are reparented once and restored when the gesture ends.
            gesture.layers = [];
            for (const [track, row] of rows) {
                const members = ids.map(id => clips.get(id)).filter(item => item.track === track);
                if (!members.length) continue;
                const node = document.createElement('div'); node.className = 'fv-ed-group-layer';
                const children = [...row.element.children]; row.element.append(node);
                members.forEach(item => node.append(item.element)); gesture.layers.push({node, row: row.element, children});
            }
        }
        element.dataset.selectGesture = gesture.type; element.setPointerCapture(event.pointerId); return true;
    }
    function update(event) {
        const g = gesture;
        if (!g) return;
        const p = point(event), bounds = element.getBoundingClientRect();
        g.moved ||= Math.hypot(event.clientX - g.x, event.clientY - g.y) >= 4;
        if (!g.moved || g.type === 'click') return;
        if (g.type === 'box') {
            const box = {left: Math.max(0, Math.min(g.start.x, p.x)), right: Math.max(g.start.x, p.x), top: Math.min(g.start.y, p.y), bottom: Math.max(g.start.y, p.y)};
            Object.assign(marquee.style, {left: `${box.left}px`, top: `${box.top}px`, width: `${box.right - box.left}px`, height: `${box.bottom - box.top}px`}); marquee.hidden = false;
            if (!g.previewing) { paint([]); g.previewing = true; outlines.hidden = false; }
            const inside = rectangleSelection(g.record, g.rectangles, box)?.ids || [];
            g.selected = adding(g) ? [...new Set([...selectedIds(g.record, g.previous), ...inside])] : inside;
            const previewIds = new Set(g.selected);
            for (const [id, node] of g.outlines) if (node.hidden === previewIds.has(id)) node.hidden = !previewIds.has(id);
        } else {
            const clip = clips.get(g.id).clip, desired = clip.start + Math.round((p.x - g.start.x) * model.FPS / state.zoom);
            const points = [...g.points, ...[desired, desired + clip.frames].map(frame => Math.round(frame / (5 * model.FPS)) * 5 * model.FPS)];
            const aligned = snap(Math.max(0, desired), transportSnapPoints(state, points, event), state.zoom, {offsets: [0, clip.frames]});
            const track = [...rows].find(([, row]) => p.y >= row.top && p.y <= row.top + row.height)?.[0];
            const result = g.plan.preview(aligned.frame - clip.start, track); g.result = result;
            if (result.code === 'busy') {
                if (!g.warned) { g.warned = true; commands.report(new model.EditError('busy')); }
                return;
            }
            guide.hidden = aligned.guide == null || result.delta !== aligned.frame - clip.start;
            guide.style.left = `${pixels(aligned.guide ?? 0)}px`;
            for (const layer of g.layers) layer.node.style.transform = `translateX(${pixels(result.delta)}px)`;
            if (g.blocked !== !!result.code && (result.code || g.blocked)) for (const member of g.plan.members)
                clips.get(member.id).element.classList.toggle('fv-ed-cannot-drop', !!result.code);
            if (g.blocked !== !!result.code) element.dataset.selectBlocked = String(!!result.code);
            g.blocked = !!result.code;
            label.hidden = !!result.code;
            label.textContent = result.code ? ''
                : t(`${Math.round((g.plan.start + result.delta) / model.FPS)} – ${Math.round((g.plan.end + result.delta) / model.FPS)} seconds`, `${Math.round((g.plan.start + result.delta) / model.FPS)} – ${Math.round((g.plan.end + result.delta) / model.FPS)} 秒`);
            label.style.left = `${Math.max(8, Math.min(bounds.width - 220, event.clientX - bounds.left + 14))}px`;
            label.style.top = `${Math.max(26, Math.min(bounds.height - 32, event.clientY - bounds.top + 14))}px`; label.hidden = !!result.code;
        }
    }
    function tick() {
        raf = null;
        if (!gesture || !lastEvent) return;
        update(lastEvent);
        if (gesture.moved && gesture.type !== 'click') {
            const bounds = scroll.getBoundingClientRect(), x = lastEvent.clientX;
            const speed = x < bounds.left + 28 ? -12 : x > bounds.right - 28 ? 12 : 0;
            if (speed) { const before = scroll.scrollLeft; scroll.scrollLeft += speed; if (before !== scroll.scrollLeft) raf = requestAnimationFrame(tick); }
        }
    }
    function finish(event, cancel = false) {
        if (!gesture || (event && gesture.pointer !== event.pointerId)) return false;
        if (event) consume(event);
        if (!cancel && event) update(event);
        const g = gesture; gesture = null; lastEvent = null; cancelAnimationFrame(raf); raf = null;
        for (const layer of g.layers || []) { layer.children.forEach(node => layer.row.append(node)); layer.node.remove(); }
        for (const item of clips.values()) if (item.element.className.includes('fv-ed-cannot-drop')) item.element.classList.remove('fv-ed-cannot-drop');
        marquee.hidden = guide.hidden = label.hidden = outlines.hidden = true; outlines.replaceChildren(); delete element.dataset.selectGesture; delete element.dataset.selectBlocked;
        if (element.hasPointerCapture(g.pointer)) element.releasePointerCapture(g.pointer);
        try {
            if (cancel) state.select(g.previous);
            else if (g.type === 'box') { const ids = g.selected || (adding(g) ? selectedIds(g.record, g.previous) : []); state.select(selection(ids), {history: true}); anchor = ids.at(-1) ?? null; }
            else if (g.type === 'move' && !g.moved) choose(g.id);
            else if (g.type === 'move' && g.result?.code === 'locked') commands.report(new model.EditError('locked'));
            else if (g.type === 'move' && g.result && !g.result.code) state.apply(t('Move', '移动'), record => actionsModel.moveParts(record, g.plan.ids, {frames: g.result.delta, track: null}));
        } catch (error) { commands.report(error); }
        finally { paint(selectedIds(state.record, state.selection)); renderSelection(); actions.gestureEnd?.(); }
        return true;
    }
    const stop = state.subscribe(event => {
        if (gesture && (event.type === 'zoom' || (event.type === 'record' && state.record !== gesture.record))) finish(null, true);
        if (event.type === 'selection' && state.selection?.kind !== 'clip') anchor = null;
    });
    return {begin, choose,
        move(event) { if (!gesture) return false; if (gesture.pointer === event.pointerId) { consume(event); lastEvent = event; if (!raf) raf = requestAnimationFrame(tick); } return true; },
        finish,
        keyboard(event) {
            if (gesture) { consume(event); if (event.key.toLowerCase() === 'escape') finish(null, true); return true; }
            return commands.keyboard(event);
        },
        dispose() { finish(null, true); stop(); marquee.remove(); guide.remove(); label.remove(); outlines.remove(); },
    };
}
