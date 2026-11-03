// Track menus share the selection commands; they never choose an edit's landing position.
import * as model from './editor_model.js';
import {clickSelection, createSelectionActions, isTyping, selectedIds, playheadIds} from './editor_select.js';

const registered = new Set();
// Groups appear in first-registration order; matching groups stay together.
// Built-ins use 'edit' and 'split'. Return the disposer when unloading an extension.
// label/shortcut accept a string or (context) => string, so extensions can use context.t.
export function registerMenuItem(item) {
    if (!['part', 'empty'].includes(item.where) || item.group == null || !item.label || typeof item.run !== 'function')
        throw new TypeError('Menu items require where, group, label and run');
    const entry = {...item}; registered.add(entry);
    return () => registered.delete(entry);
}

export function menuContext({state, commands, t = en => en, actions = {}, clip = null, frame = state.playhead, track = null,
    platform = globalThis.navigator?.userAgentData?.platform || globalThis.navigator?.platform || ''}) {
    const ids = selectedIds(state.record, state.selection), seen = new Set();
    let count = 0;
    for (const id of ids) if (!seen.has(id)) {
        count++;
        for (const member of model.pair(state.record, id)) seen.add(member.id);
    }
    return {where: clip ? 'part' : 'empty', clip, ids, count, frame, track, state, record: state.record,
        playhead: state.playhead, commands, actions, t, modifier: /Mac|iPhone|iPad/i.test(platform) ? '⌘' : 'Ctrl'};
}

export function menuItems(context) {
    const {record, ids, clip, playhead, commands, t, modifier} = context;
    const busy = item => ['queued', 'running'].includes(item.pending);
    const members = ids.map(id => model.find(record, id)).filter(Boolean);
    const generating = members.some(busy);
    const copyable = members.some(item => model.pair(record, item.id).every(member => member.source && !busy(member)));
    const command = (key, label, shortcut, enabled = true, group = 'edit') =>
        ({group, label, shortcut, enabled, run: () => commands.run(key)});
    const splitTargets = playheadIds(record, context.state.selection, playhead).map(id => model.find(record, id));
    const splitBusy = splitTargets.some(item => model.pair(record, item.id).some(busy));
    const canSplit = splitTargets.some(item => playhead > item.start && playhead < item.start + item.frames);
    const items = context.where === 'part' ? [
        command('c', t('Copy', '复制'), `${modifier}+C`, copyable),
        command('x', t('Cut', '剪切'), `${modifier}+X`, copyable && !generating),
        command('d', t('Duplicate', '创建副本'), `${modifier}+D`, copyable),
        command('delete', t('Delete', '删除'), 'Delete', !generating),
        command('b', t('Split', '剪开'), `${modifier}+B`, !splitBusy && canSplit, 'split'),
    ] : [
        command('v', t('Paste', '粘贴'), `${modifier}+V`, commands.canPaste()),
        command('a', t('Select all', '全选'), `${modifier}+A`, record.tracks.some(track => track.clips.length)),
    ];
    const value = field => typeof field === 'function' ? field(context) : field;
    for (const item of registered) if (item.where === context.where) items.push({...item,
        label: value(item.label), shortcut: value(item.shortcut) || '', enabled: item.enabled ? !!item.enabled(context) : true,
        run: () => item.run(context)});
    const groups = new Map();
    for (const item of items) {
        if (!groups.has(item.group)) groups.set(item.group, []);
        groups.get(item.group).push(item);
    }
    return [...groups.values()].flat();
}

export function createTrackMenu(host, {state, t = en => en, actions = {}, hitTest, frameAt, trackAt, clipboard,
    choose = id => state.select(clickSelection(state.record, state.selection, id))} = {}) {
    const commands = createSelectionActions({state, t, actions, ...(clipboard ? {clipboard} : {})});
    const element = document.createElement('div');
    element.className = 'fv-ed-context-menu'; element.hidden = true; element.tabIndex = -1;
    element.setAttribute('role', 'menu'); host.append(element);
    let buttons = [], context = null, returnFocus = null;
    const listeners = [];
    function close(restore = false) {
        if (element.hidden) return false;
        element.hidden = true; context = null;
        if (restore) (returnFocus?.isConnected && host.contains(returnFocus) ? returnFocus : host).focus({preventScroll: true});
        buttons.forEach(button => { button.onclick = null; }); buttons = []; element.replaceChildren();
        return true;
    }
    function dismiss(event) {
        if (element.hidden) return false;
        event.preventDefault(); event.stopPropagation(); close(true); return true;
    }
    function keyboard(event) {
        if (element.hidden || isTyping(event.target)) return false;
        const key = event.key.toLowerCase();
        event.preventDefault(); event.stopPropagation();
        if (key === 'escape' || key === 'tab') close(true);
        else if (['arrowup', 'arrowdown', 'home', 'end'].includes(key)) {
            const index = buttons.indexOf(document.activeElement);
            const next = key === 'home' ? 0 : key === 'end' ? buttons.length - 1
                : (index + (key === 'arrowup' ? -1 : 1) + buttons.length) % buttons.length;
            buttons[next]?.focus({preventScroll: true});
        } else if (key === 'enter' || key === ' ') buttons.find(button => button === document.activeElement)?.onclick();
        return true;
    }
    // The editor's captured keyboard gate calls control onkeydown handlers.
    element.onkeydown = keyboard;
    const stop = event => event.stopPropagation();
    const contextMenu = event => {
        if (isTyping(event.target)) return;
        event.preventDefault(); event.stopPropagation();
        if (element.contains(event.target)) return;
        close();
        const clip = hitTest(event).item?.clip || null;
        if (clip && !selectedIds(state.record, state.selection).includes(clip.id))
            choose(clip.id);
        context = menuContext({state, commands, actions, t, clip, frame: frameAt(event), track: trackAt(event)});
        returnFocus = document.activeElement;
        if (context.where === 'part' && context.count > 1) {
            const heading = document.createElement('div'); heading.className = 'fv-ed-menu-count';
            heading.textContent = t(`${context.count} parts selected`, `已选 ${context.count} 段`); element.append(heading);
            element.setAttribute('aria-label', heading.textContent);
        } else element.removeAttribute('aria-label');
        let previousGroup;
        for (const item of menuItems(context)) {
            if (previousGroup !== undefined && item.group !== previousGroup) {
                const separator = document.createElement('div'); separator.setAttribute('role', 'separator'); element.append(separator);
            }
            previousGroup = item.group;
            const button = document.createElement('button'); button.type = 'button'; button.tabIndex = -1;
            button.setAttribute('role', 'menuitem'); button.setAttribute('aria-disabled', String(!item.enabled));
            const label = document.createElement('span'), shortcut = document.createElement('span');
            label.className = 'fv-ed-menu-label'; label.textContent = item.label;
            shortcut.className = 'fv-ed-menu-shortcut'; shortcut.textContent = item.shortcut;
            if (item.tourId) button.dataset.tourId = item.tourId;
            button.append(label, shortcut);
            // Keep disabled items discoverable without explanatory tooltips.
            button.onclick = () => {
                if (!item.enabled) return;
                close(true);
                try { item.run(); } catch (error) { commands.report(error); }
            };
            buttons.push(button); element.append(button);
        }
        element.hidden = false;
        const bounds = element.getBoundingClientRect();
        const width = window.innerWidth || document.documentElement?.clientWidth || 1440;
        const height = window.innerHeight || document.documentElement?.clientHeight || 900;
        element.style.left = `${Math.max(8, Math.min(event.clientX, width - bounds.width - 8))}px`;
        // Without room below the pointer the menu opens upward from it, so it never slides over the toolbar beside it.
        const below = event.clientY + bounds.height + 8 <= height;
        element.style.top = `${Math.max(8, below ? event.clientY : event.clientY - bounds.height)}px`;
        (buttons[0] || element).focus({preventScroll: true});
    };
    const outside = event => { if (!element.contains(event.target)) close(); }, closeMenu = () => close();
    // Event names stay literal so the shared key-listener guard can read every registration.
    element.addEventListener('pointerdown', stop); element.addEventListener('click', stop);
    host.addEventListener('contextmenu', contextMenu); host.addEventListener('scroll', outside, true);
    document.addEventListener('pointerdown', outside, true);
    window.addEventListener('blur', closeMenu); window.addEventListener('resize', closeMenu);
    listeners.push(() => {
        element.removeEventListener('pointerdown', stop); element.removeEventListener('click', stop);
        host.removeEventListener('contextmenu', contextMenu); host.removeEventListener('scroll', outside, true);
        document.removeEventListener('pointerdown', outside, true);
        window.removeEventListener('blur', closeMenu); window.removeEventListener('resize', closeMenu);
    });
    const unsubscribe = state.subscribe(event => {
        // Autosave acknowledgements replace the record just to adopt identity/revision.
        // Keep the menu usable while that happens; actual edits invalidate its actions.
        if (event.type === 'record' && context) {
            const content = ({id, revision, ...value}) => JSON.stringify(value);
            if (content(context.record) === content(state.record)) { context.record = state.record; return; }
        }
        if (['record', 'selection', 'playhead', 'zoom'].includes(event.type)) close(element.contains(document.activeElement));
    });
    return {element, keyboard, dismiss, dispose() {
        close(); unsubscribe(); listeners.forEach(remove => remove()); element.onkeydown = null; element.remove();
    }};
}
