import * as model from './editor_model.js';
import {addMarker, hasMarker, moveMarker, removeMarker, selectedRange, shuttleRate, setSnapping,
    snappingEnabled, transportSnapPoints} from './editor_transport_model.js';

// Other editor features can register rows before the sheet opens.
export const shortcuts = [];
export function registerShortcuts(rows) {
    for (const row of rows) {
        if (!row.id || !row.key || !row.en || !row.zh || !row.group) throw new Error('Invalid shortcut');
        const index = shortcuts.findIndex(value => value.id === row.id);
        if (index < 0) shortcuts.push({...row}); else shortcuts[index] = {...row};
    }
}
const row = (id, key, en, zh, group, match) => ({id, key, en, zh, group, match});
const plain = key => event => !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey && event.key.toLowerCase() === key;
registerShortcuts([
    row('reverse', 'J', 'Play backward', '向后播放', 'play', plain('j')),
    row('stop', 'K', 'Pause', '暂停', 'play', plain('k')),
    row('forward', 'L', 'Play forward', '向前播放', 'play', plain('l')),
    row('stepBack', 'K + J', 'Previous frame', '上一帧', 'play'),
    row('stepForward', 'K + L', 'Next frame', '下一帧', 'play'),
    row('home', 'Home', 'Start', '开头', 'play', plain('home')),
    row('end', 'End', 'End', '结尾', 'play', plain('end')),
    row('loop', 'Shift + L', 'Loop', '循环播放', 'play', event => !event.ctrlKey && !event.metaKey && !event.altKey && event.shiftKey && event.key.toLowerCase() === 'l'),
    row('range', 'Shift + Space', 'Play the selected range only', '只播选中的范围', 'play', event => !event.ctrlKey && !event.metaKey && !event.altKey && event.shiftKey && event.key === ' '),
    row('fullscreen', 'F', 'Full screen', '全屏', 'play', plain('f')),
    row('snapping', 'S', 'Snapping', '吸附', 'timeline', plain('s')),
    row('invertSnapping', 'Alt', 'Invert snapping while dragging', '拖动时反转吸附', 'timeline'),
    row('marker', 'M', 'Marker', '标记', 'timeline', plain('m')),
    row('previousMarker', 'Shift + M', 'Previous marker', '上一个标记', 'timeline', event => !event.ctrlKey && !event.metaKey && !event.altKey && event.shiftKey && event.key.toLowerCase() === 'm'),
    row('nextMarker', 'Alt + M', 'Next marker', '下一个标记', 'timeline', event => !event.ctrlKey && !event.metaKey && event.altKey && !event.shiftKey && event.key.toLowerCase() === 'm'),
    row('zoomIn', '+ / = / Ctrl / Cmd + +', 'Zoom in', '放大', 'timeline', event => !event.altKey && ['+', '=', 'Add'].includes(event.key)),
    row('zoomOut', '− / Ctrl / Cmd + −', 'Zoom out', '缩小', 'timeline', event => !event.altKey && ['-', 'Subtract'].includes(event.key)),
    row('togglePlay', 'Space', 'Play / pause', '播放 / 暂停', 'play'),
    row('frames', '← / →', 'Previous / next frame', '上一帧 / 下一帧', 'play'),
    row('cuts', '↑ / ↓', 'Previous / next cut', '上一个 / 下一个剪辑点', 'play'),
    row('exitFullscreen', 'Esc', 'Exit full screen', '退出全屏', 'play'),
    row('undo', 'Ctrl / Cmd + Z', 'Undo', '撤销', 'edit'),
    row('redo', 'Ctrl / Cmd + Shift + Z', 'Redo', '重做', 'edit'),
    row('split', 'Ctrl / Cmd + B', 'Split', '剪开', 'edit'),
    row('delete', 'Delete / Backspace', 'Delete', '删除', 'edit'),
    row('ripple', 'Shift + Delete / Backspace', 'Delete the selected clip and close the gap', '删除选中的片段并合上空隙', 'edit'),
    row('fit', 'Shift + Z', 'Fit all', '看全部', 'timeline', event => !event.ctrlKey && !event.metaKey && !event.altKey && event.shiftKey && event.key.toLowerCase() === 'z'),
]);
export function registerEditorShortcuts(audioRows = [], speedRows = []) {
    registerShortcuts([
        ...[['copy', 'C', 'Copy', '复制'], ['cut', 'X', 'Cut', '剪切'], ['paste', 'V', 'Paste', '粘贴'],
            ['duplicate', 'D', 'Duplicate', '创建副本'], ['selectAll', 'A', 'Select all', '全选']]
            .map(([id, key, en, zh]) => row(id, `Ctrl / Cmd + ${key}`, en, zh, 'edit')),
        row('addText', 'T', 'Add text', '添加文字', 'text'),
        row('nextSubtitle', 'Enter', 'Next subtitle while editing', '编辑时添加下一条字幕', 'text'),
        row('cancelSelection', 'Esc', 'Cancel selection or dragging, or close a popup', '取消选择、拖动或关闭弹窗', 'edit'),
        row('selectPart', 'Enter', 'Select the highlighted part', '选中高亮的段', 'edit'),
        row('toggleSelection', 'Ctrl / Cmd + click', 'Add or remove a part from selection', '添加或移出选择', 'edit'),
        row('rangeSelection', 'Shift + click', 'Select parts between two points', '选中两处之间的段', 'edit'),
        row('boxSelection', 'Shift + drag', 'Select parts in a box', '框选多段', 'edit'),
        row('closeGap', 'Delete / Backspace', 'Close the selected gap', '移除选中的空隙', 'edit'),
        ...audioRows.map(value => row(`audio.${value.key}`, value.key === 'escape' ? 'Esc' : value.key === 'delete' ? 'Delete' : 'Backspace',
            value.action === 'clearCrossfade' ? 'Deselect crossfade' : 'Remove selected crossfade',
            value.action === 'clearCrossfade' ? '取消选择交叉淡化' : '移除选中的交叉淡化', 'audio')),
        ...speedRows,
    ]);
}
export const shortcutAction = event => shortcuts.find(value => value.match?.(event))?.id ?? null;
const el = (tag, cls) => { const value = document.createElement(tag); if (cls) value.className = cls; return value; };
const icons = {
    loop: 'M4 6h12l-3-3m3 3-3 3M16 14H4l3 3m-3-3 3-3',
    'play-range': 'M3 3v14m14-14v14M7 6l6 4-6 4z', fullscreen: 'M7 3H3v4m10-4h4v4M3 13v4h4m10-4v4h-4',
    snapping: 'M4 3v8a6 6 0 0 0 12 0V3h-4v8a2 2 0 0 1-4 0V3zM4 7h4m4 0h4',
    'save-frame': 'M3 6h3l1.5-2h5L14 6h3v10H3zM10 13.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
    marker: 'm10 3 6 7-6 7-6-7z', shortcuts: 'M2 5h16v10H2zM5 8h1m3 0h1m3 0h1M5 11h1m3 0h5',
};
export function createEditorTransport({element, preview, transport, toolbar, editTools, zoomHost, state, player, t, notify, saveFrame = null}) {
    let tracks = null, align = null, layer = null, markerSelection = null, markerStamp = null, drag = null, kHeld = false, disposed = false;
    const clock = player.playback, listeners = [], nodes = [];
    const on = (target, type, callback, options) => { target.addEventListener(type, callback, options); listeners.push(() => target.removeEventListener(type, callback, options)); };
    function button(id, title, text = '') {
        const value = el('button', `fv-ed-${id}`); value.type = 'button'; value.title = title; value.setAttribute('aria-label', title);
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'), path = document.createElementNS(svg.namespaceURI, 'path');
        svg.setAttribute('viewBox', '0 0 20 20'); svg.setAttribute('aria-hidden', 'true'); path.setAttribute('d', icons[id]); svg.append(path); value.append(svg);
        if (text) { const label = el('span'); label.textContent = text; value.append(label); }
        nodes.push(value); return value;
    }
    const marker = button('marker', t('Marker (M)', '标记（M）'));
    const snapping = button('snapping', t('Snapping (S)', '吸附（S）'));
    const sheetButton = button('shortcuts', t('Shortcuts', '快捷键'));
    editTools.append(marker); toolbar.insertBefore(snapping, zoomHost); toolbar.append(sheetButton);
    const tools = el('div', 'fv-ed-transport-tools');
    const loop = button('loop', t('Loop (Shift + L)', '循环播放（Shift + L）'));
    const range = button('play-range', t('Play selected range (Shift + Space)', '播放选中范围（Shift + 空格）'));
    const fullscreen = button('fullscreen', t('Full screen (F)', '全屏（F）'));
    // Keeps the frame under the playhead as a picture; the button is off on an empty timeline and while the editor saves it.
    const frame = saveFrame && button('save-frame', t('Save current frame', '保存当前帧'));
    let savingFrame = false;
    if (frame) on(frame, 'click', async () => {
        if (frame.disabled) return;
        savingFrame = true; frame.disabled = true; frame.setAttribute('aria-busy', 'true');
        try { await saveFrame(); } finally { savingFrame = false; frame.removeAttribute('aria-busy'); refresh(); }
    });
    tools.append(loop, range, ...(frame ? [frame] : []), fullscreen); transport.append(tools); nodes.push(tools);
    const sheet = el('div', 'fv-ed-shortcut-sheet'); sheet.hidden = true; sheet.setAttribute('role', 'region'); sheet.setAttribute('aria-label', t('Shortcuts', '快捷键'));
    element.append(sheet); nodes.push(sheet);
    const fullHost = el('div', 'fv-ed-fullscreen-host'); preview.parentNode.insertBefore(fullHost, preview); fullHost.append(preview, transport); nodes.push(fullHost);
    const playButton = transport.querySelector('.fv-ed-play');
    playButton.title = t('Play / pause (Space)', '播放 / 暂停（空格）');
    // Backward playback is silent; the note shows only while the clock runs backward (the player keeps data-rate current).
    const reverseNote = el('span', 'fv-ed-reverse-note'); reverseNote.setAttribute('role', 'status');
    reverseNote.textContent = t('No sound', '无声'); nodes.push(reverseNote);
    transport.insertBefore(reverseNote, tools);
    const selection = () => selectedRange(state.record, state.selection, model.duration(state.record));
    function refresh() {
        if (disposed) return;
        loop.setAttribute('aria-pressed', String(clock.loop));
        snapping.setAttribute('aria-pressed', String(snappingEnabled(state)));
        snapping.title = t('Snapping (S)', '吸附（S）');
        snapping.setAttribute('aria-label', snapping.title);
        const bounds = selection(); range.disabled = !bounds;
        range.title = t('Play selected range (Shift + Space)', '播放选中范围（Shift + 空格）');
        range.setAttribute('aria-label', range.title); range.setAttribute('aria-pressed', String(!!clock.range));
        if (frame) frame.disabled = savingFrame || !model.exportFrames(state.record);
        fullscreen.setAttribute('aria-pressed', String(document.fullscreenElement === fullHost));
        transport.dataset.rate = String(player.playing ? clock.rate : 0);
        if (layer && !drag) renderMarkers();
    }
    function renderMarkers() {
        if (markerStamp?.zoom === state.zoom && markerStamp.rows === state.record.markers && markerStamp.selection === markerSelection) return;
        markerStamp = {zoom: state.zoom, rows: state.record.markers, selection: markerSelection};
        const previous = new Map([...layer.children].map(node => [Number(node.dataset.frame), node])), rows = [];
        for (const value of state.record.markers || []) {
            const mark = previous.get(value.frame) || el('button', 'fv-ed-timeline-marker'); previous.delete(value.frame); mark.type = 'button'; mark.dataset.frame = String(value.frame);
            mark.style.left = `${value.frame * state.zoom / 24}px`; mark.title = t('Marker (M)', '标记（M）');
            mark.setAttribute('aria-label', mark.title); mark.setAttribute('aria-pressed', String(markerSelection === value.frame));
            mark.onpointerdown = event => {
                if (event.button !== 0 || drag) return;
                event.preventDefault(); event.stopPropagation(); mark.focus({preventScroll: true});
                drag = {from: value.frame, to: value.frame, x: event.clientX, node: mark, pointer: event.pointerId}; mark.setPointerCapture(event.pointerId);
                player.pause(); selectMarker(value.frame); state.seek(Math.min(value.frame, model.duration(state.record)));
                refresh(); mark.setAttribute('aria-pressed', 'true');
            };
            mark.onpointermove = event => {
                if (!drag || drag.pointer !== event.pointerId) return;
                event.stopPropagation();
                const frame = Math.min(model.duration(state.record), Math.max(0, value.frame + Math.round((event.clientX - drag.x) * 24 / state.zoom)));
                const points = transportSnapPoints(state, [0, ...model.snapPoints(state.record, [])], event).filter(frame => frame !== drag.from);
                drag.to = align(frame, points, state.zoom).frame;
                mark.style.left = `${drag.to * state.zoom / 24}px`; mark.dataset.blocked = String(drag.to !== drag.from && hasMarker(state.record, drag.to));
            };
            mark.onpointerup = event => { event.stopPropagation(); finishMarker(); };
            mark.onpointercancel = event => { event.stopPropagation(); finishMarker(true); };
            mark.onlostpointercapture = () => { if (drag?.node === mark) finishMarker(true); };
            mark.onclick = event => {
                event.stopPropagation(); if (event.detail !== 0) return;
                selectMarker(value.frame); player.pause(); state.seek(Math.min(value.frame, model.duration(state.record))); refresh();
            };
            rows.push(mark);
        }
        rows.forEach((mark, index) => { if (layer.children[index] !== mark) layer.insertBefore(mark, layer.children[index] || null); });
        previous.forEach(mark => mark.remove());
    }
    // Only one kind of thing is selected at a time: choosing a marker lets go of any part, gap or fade junction.
    function selectMarker(frame) {
        if (state.selection) state.select(null);
        markerSelection = frame; markerStamp = null;
    }
    function finishMarker(cancel = false) {
        if (!drag) return;
        const value = drag; drag = null; markerStamp = null; value.node.removeAttribute('data-blocked');
        // A marker cannot sit on another marker: the drop is refused and it goes back.
        if (!cancel && value.from !== value.to && !hasMarker(state.record, value.to)) {
            markerSelection = value.to; state.apply(t('Marker', '标记'), record => moveMarker(record, value.from, value.to));
        }
        if (value.node.hasPointerCapture?.(value.pointer)) value.node.releasePointerCapture(value.pointer);
        refresh();
    }
    function add() {
        // On a frame that already has a marker, M selects that one instead of adding another.
        const frame = state.playhead;
        if (!hasMarker(state.record, frame)) state.apply(t('Marker', '标记'), record => addMarker(record, frame));
        selectMarker(frame); refresh();
    }
    function playRange() {
        const bounds = selection(); if (!bounds) return {ok: false, reason: 'range'};
        player.pause(); clock.range = bounds; clock.rate = 1; state.seek(bounds.start); player.play(); refresh();
    }
    function toggleLoop() { clock.loop = !clock.loop; refresh(); }
    function zoom(direction) {
        if (!tracks) return;
        const scroll = tracks.element.querySelector('.fv-ed-track-scroll');
        const offset = state.playhead * state.zoom / 24 - scroll.scrollLeft;
        state.setZoom(Math.max(1, Math.min(160, state.zoom * (direction > 0 ? 1.25 : .8))));
        tracks.setScrollLeft(Math.max(0, state.playhead * state.zoom / 24 - offset));
    }
    async function toggleFullscreen() {
        try {
            if (document.fullscreenElement === fullHost) await document.exitFullscreen();
            else { sheet.hidden = true; await fullHost.requestFullscreen(); }
        } catch { notify?.(t('Full screen is not available here.', '这里不能全屏。')); return {ok: false, reason: 'fullscreen'}; }
        refresh();
    }
    const handlers = {
        reverse: () => shuttle(-1), forward: () => shuttle(1), stop: () => { kHeld = true; player.pause(); },
        home: () => { player.pause(); state.seek(0); }, end: () => { player.pause(); state.seek(model.duration(state.record)); },
        loop: toggleLoop, range: playRange, fullscreen: toggleFullscreen,
        snapping: () => { const next = !snappingEnabled(state); setSnapping(state, next); tracks?.setSnapping?.(next); refresh(); }, marker: add,
        fit: () => toolbar.querySelector('.fv-ed-fit')?.click(),
        previousMarker: () => jumpMarker(-1), nextMarker: () => jumpMarker(1), zoomIn: () => zoom(1), zoomOut: () => zoom(-1),
    };
    function shuttle(direction) {
        if (kHeld) { player.pause(); state.seek(Math.max(0, state.playhead + direction)); return; }
        if (player.playing && clock.rate === direction) return;
        const rate = shuttleRate(player.playing ? clock.rate : 0, direction);
        player.pause(); clock.rate = rate; player.play();
    }
    function jumpMarker(direction) {
        const markers = state.record.markers || [];
        const value = direction < 0 ? [...markers].reverse().find(marker => marker.frame < state.playhead) : markers.find(marker => marker.frame > state.playhead);
        if (value) { player.pause(); state.seek(Math.min(value.frame, model.duration(state.record))); }
    }
    marker.onclick = add; snapping.onclick = handlers.snapping; loop.onclick = toggleLoop; fullscreen.onclick = toggleFullscreen;
    range.onclick = () => { if (clock.range && player.playing) { player.pause(); clock.range = null; refresh(); } else playRange(); };
    sheetButton.onclick = () => {
        sheet.hidden = !sheet.hidden; sheetButton.setAttribute('aria-expanded', String(!sheet.hidden));
        if (sheet.hidden) return;
        const table = el('table'), heading = el('caption'); heading.textContent = t('Shortcuts', '快捷键'); table.append(heading);
        for (const group of ['play', 'timeline', 'edit', ...new Set(shortcuts.map(row => row.group))]) {
            if (table.dataset[group]) continue; table.dataset[group] = 'true';
            for (const value of shortcuts.filter(value => value.group === group)) {
                const line = el('tr'), key = el('td'), action = el('td'); key.textContent = value.key; action.textContent = t(value.en, value.zh); line.append(key, action); table.append(line);
            }
        }
        sheet.replaceChildren(table);
    };
    on(document, 'fullscreenchange', refresh); on(window, 'blur', () => { kHeld = false; finishMarker(true); });
    on(element, 'pointerdown', event => { if (!event.target.closest('.fv-ed-timeline-marker')) { markerSelection = null; refresh(); } }, true);
    on(transport, 'click', refresh);
    const unsubscribe = state.subscribe(event => {
        if (event.type === 'selection') markerSelection = null;
        if (event.type === 'selection' && clock.range) {
            clock.range = selection(); if (!clock.range) player.pause();
        }
        if (event.type === 'record' && markerSelection !== null && !state.record.markers?.some(marker => marker.frame === markerSelection)) markerSelection = null;
        if (event.type === 'record' && clock.range) clock.range = selection();
        refresh();
    });
    setSnapping(state, true); refresh();
    return {
        attachTracks(value, snapFrame) {
            tracks = value; align = snapFrame; layer = el('div', 'fv-ed-marker-layer'); tracks.element.querySelector('.fv-ed-track-content').append(layer); refresh();
        },
        keyboard(event, {ready = true} = {}) {
            if (event.type === 'keyup') { if (event.key.toLowerCase() === 'k') kHeld = false; return false; }
            if (event.type !== 'keydown') return false;
            if (event.target.closest('input,textarea,select,[contenteditable="true"],[contenteditable=""]')) return false;
            if (event.key === 'Escape' && document.fullscreenElement === fullHost) { event.preventDefault(); document.exitFullscreen().catch(() => {}); return true; }
            if (event.key === 'Escape' && (drag || !sheet.hidden)) { event.preventDefault(); finishMarker(true); sheet.hidden = true; return true; }
            if (!ready) return false;
            // Escape lets go of a selected marker; later steps (the part selection) are not touched in the same press.
            if (event.key === 'Escape' && markerSelection !== null) { event.preventDefault(); markerSelection = null; markerStamp = null; refresh(); return true; }
            if (markerSelection !== null && ['Delete', 'Backspace'].includes(event.key)) {
                event.preventDefault(); const frame = markerSelection; markerSelection = null;
                state.apply(t('Delete', '删除'), record => removeMarker(record, frame)); refresh(); return true;
            }
            const action = shortcutAction(event);
            if (handlers[action]) {
                event.preventDefault();
                if (!event.repeat || !['reverse', 'forward', 'stop', 'marker', 'snapping', 'loop', 'fullscreen', 'range'].includes(action)) handlers[action]();
                refresh(); return true;
            }
            if (['ArrowUp', 'ArrowDown'].includes(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey) {
                tracks?.keyboard(event); return !!tracks;
            }
            if (event.key === ' ' || ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
                // The existing handlers own these keys; reset a paused shuttle first.
                if (event.key === ' ' && !player.playing) clock.rate = 1;
            }
            return false;
        },
        dispose() {
            disposed = true; unsubscribe(); listeners.forEach(remove => remove()); finishMarker(true); layer?.remove();
            if (document.fullscreenElement === fullHost) document.exitFullscreen().catch(() => {});
            element.insertBefore(preview, fullHost); element.insertBefore(transport, fullHost); nodes.forEach(node => node.remove());
        },
    };
}
