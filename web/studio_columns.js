// The creation panel's settings column (three-column layout): the controls the bottom console kept behind chips and trays
// stand here as sections, top to bottom, in the order of the picture view's left column. Nothing is rebuilt: the same
// elements move, so every handler and the saved values stay where they were.
import { button as railButton, section as ivSection } from './image_view_ui.js';
const el = (cls, tag = 'div') => { const e = document.createElement(tag); e.className = cls; return e; };

export function layoutColumns({t, controls, consoleUI, parts, fold, rail}) {
    // Everything the column moves is remembered by its old parents' child lists, so restore() puts the console back as it was
    // (the editor borrows the console bar as it is) and apply() lays the column out again.
    const promptTools = consoleUI.element.querySelector('.fv-prompt-tools');
    const expand = promptTools?.querySelector('.fv-ed-expand'); // the prompt history key is added to promptTools after this runs, so the whole holder moves
    const movers = [promptTools, expand, parts.media, parts.mention, parts.enhance, parts.size, parts.duration, parts.quality, parts.pass, parts.runbar, consoleUI.slot, consoleUI.element.querySelector('.fvs-console-prompt'),
        ...consoleUI.trays.querySelectorAll('[data-tray]')].filter(Boolean);
    const parents = [...new Set([controls, ...movers.map(node => node.parentNode).filter(Boolean)])];
    const before = parents.map(parent => [parent, [...parent.childNodes]]);
    let built = null;
    const apply = () => { rail.querySelectorAll('.fv-sc-rail-icon').forEach(node => node.remove()); built = build(); };
    // The Enhance chip's own bar (switch, versions) follows the chip wherever it stands; studio.js sets it next to the chip.
    const barOf = () => { const next = parts.enhance?.nextElementSibling; return next?.classList.contains('fv-enhance-bar') ? next : null; };
    const dressed = [];
    const dress = (node, ...names) => { if (!node) return; node.classList.add(...names); dressed.push([node, names]); };
    const restore = () => {
        const bar = barOf();
        for (const [node, names] of dressed.splice(0)) node.classList.remove(...names);
        controls.querySelector('.fv-sc-tools')?.watch?.disconnect();
        for (const tray of controls.querySelectorAll('[data-pinned]')) { delete tray.dataset.pinned; tray.open = false; } /* the console starts with its trays put away */
        // Nodes that joined a parent after the snapshot was taken (the prompt history key, the reverse-prompt key) are no part of
        // what the column built, so they stay where they stand: put back after their old neighbour, or first when they led.
        const remembered = new Set(before.flatMap(([, nodes]) => nodes)), mine = new Set([built?.head, built?.scroll, built?.foot, fold, rail]);
        const late = before.map(([parent]) => [parent, [...parent.childNodes].filter(node => !remembered.has(node) && !mine.has(node) && node !== consoleUI.slot)]);
        const neighbours = new Map(late.flatMap(([parent, nodes]) => nodes.map(node => [node, node.previousSibling])));
        for (const [parent, nodes] of before) parent.replaceChildren(...nodes);
        for (const [parent, nodes] of late) for (const node of nodes) {
            const after = neighbours.get(node);
            if (after && after.parentNode === parent) after.after(node); else parent.prepend(node);
        }
        // The status line stands first in the dock, as it does before the column is built (studio.js).
        controls.prepend(consoleUI.slot);
        fold.remove(); rail.remove(); delete controls.dataset.columns; controls.classList.remove('fv-sc-col');
        parts.mention?.querySelector('.fv-sc-mention-name')?.remove(); parts.mention?.classList.remove('fv-sc-mention');
        if (bar) parts.enhance.after(bar);
    };
    compactHook(controls);
    apply();
    return {apply, restore, get parts() { return built; }};
    function build() {
    const box = consoleUI.element, scroll = el('div fv-sc-scroll fv-sc-col'), foot = el('div fv-sc-foot');
    // The sections are the picture view's own (image_view_ui.js section): an icon and a small grey title, a rule between them;
    // the advanced one folds, closed until opened, and the choice is kept under the studio's own key.
    const section = (name, label, iconName, content, options = {}) => {
        const made = ivSection(name, {title: label, iconName, store: 'freevideo.studio.sections', ...options});
        made.element.classList.add('fv-sc-section'); made.body.append(...content.filter(Boolean)); return made;
    };
    const head = el('div fv-sc-head'), headTitle = el('strong fv-sc-head-title', 'strong'); headTitle.textContent = t('Create', '生成');
    head.append(headTitle, fold);
    const prompt = box.querySelector('.fvs-console-prompt');
    const tools = el('div fv-sc-tools fv-iv-prompt-bar');
    if (parts.mention && !parts.mention.querySelector('.fv-sc-mention-name')) { // the @ stands with its name, so it reads as the character picker
        const name = el('span fv-sc-mention-name', 'span'); name.textContent = t('Character', '角色');
        name.classList.add('fv-iv-btn-label'); parts.mention.append(name); parts.mention.classList.add('fv-sc-mention');
    }
    // The keys of the row wear the picture view's text-key class, so one rule set draws both rows.
    for (const key of [parts.media, parts.mention, parts.enhance]) dress(key, 'fv-iv-text-btn');
    dress(parts.media?.querySelector('.fvs-chip-label'), 'fv-iv-btn-label'); dress(parts.enhance?.querySelector(':scope>span'), 'fv-iv-btn-label');
    const bar = barOf();
    tools.append(...[parts.media, parts.mention, parts.enhance].filter(Boolean), el('fv-iv-grow', 'span'));
    // Prompt history, the writing guide and the draft note stand at the head of the section, as the picture view puts its add key; Expand ends the row.
    if (expand) tools.append(expand);
    // The row stays on one line: when its words do not fit, its keys keep their icons only (their hover cards still name them).
    // The enhance bar (versions, cancel) wraps under the keys; only the keys and Expand are measured.
    const fitTools = () => {
        if (!tools.isConnected) return; delete tools.dataset.compact;
        const keys = [...tools.children].filter(node => !node.classList.contains('fv-enhance-bar') && !node.classList.contains('fv-iv-grow'));
        if (keys.reduce((sum, node) => sum + node.getBoundingClientRect().width, 0) + (parseFloat(getComputedStyle(tools).columnGap) || 0) * (keys.length - 1) > tools.clientWidth + 1) tools.dataset.compact = 'true';
    };
    tools.watch = typeof ResizeObserver === 'function' ? new ResizeObserver(fitTools) : null; tools.watch?.observe(tools); document.fonts?.ready?.then(fitTools).catch?.(() => {});
    const mediaTray = consoleUI.trays.querySelector('[data-tray=media]');
    const trays = ['lora', 'advanced'].map(id => consoleUI.trays.querySelector(`[data-tray=${id}]`));
    trays.forEach(tray => { // these two stay open in the column; the console's click-away closing leaves them be
        if (!tray) return;
        tray.open = true; tray.dataset.pinned = 'true';
    });
    const advanced = section('advanced', t('Advanced options', '高级选项'), 'sliders', trays, {fixed: false, open: false});
    // Opened at the foot of the column, the new rows would stand below the fold: bring the section's head to the top once it has grown.
    advanced.element.addEventListener('fv-iv-section', () => { if (advanced.isOpen()) setTimeout(() => advanced.element.scrollIntoView?.({block: 'start', behavior: 'smooth'}), 230); });
    scroll.append(
        section('prompt', t('Prompt', '描述'), 'prompt', [prompt, tools, bar, mediaTray], {fixed: true, actions: [promptTools].filter(Boolean)}).element,
        section('size', t('Frame & size', '画幅与尺寸'), 'size', [parts.size], {fixed: true}).element,
        section('duration', t('Duration', '时长'), 'clock', [parts.duration], {fixed: true}).element,
        section('quality', t('Quality', '质量'), 'gen', [parts.quality], {fixed: true}).element,
        section('pass', t('Second pass', '二采'), 'split', [parts.pass], {fixed: true}).element,
        advanced.element);
    // On the narrow rail every section has its own icon, in the column's order; it opens the drawer at that section.
    const railIcons = [['prompt', 'prompt', t('Prompt', '描述')], ['size', 'size', t('Frame & size', '画幅与尺寸')], ['duration', 'clock', t('Duration', '时长')],
        ['quality', 'gen', t('Quality', '质量')], ['pass', 'split', t('Second pass', '二采')], ['advanced', 'sliders', t('Advanced options', '高级选项')]];
    for (const [name, iconName, label] of railIcons) {
        rail.append(railButton('fv-iv-icon-btn fv-sc-rail-icon', {iconName, name: label, onClick: () => {
            const body = controls.closest('.fv-body'); body?.studioPanels?.setPanel('left', true);
            const target = scroll.querySelector(`[data-section=${name}]`);
            if (name === 'advanced') advanced.open(true);
            requestAnimationFrame(() => target.scrollIntoView({block: 'start'}));
        }}));
    }
    foot.append(consoleUI.slot, parts.runbar); // the status line (notes, what will download first) stands above Generate
    controls.replaceChildren(head, scroll, foot, rail);
    controls.dataset.columns = 'true';
    controls.classList.add('fv-sc-col'); // the narrow rail's keys are the picture view's icon keys: its rules reach the whole column, rail included
    return {scroll, foot, head};
    }
}

// Compact layout (Settings > Appearance): the column keeps the prompt and Create; the rest stays built, only not shown.
function compactHook(controls) {
    const paint = on => { controls.dataset.compact = String(!!on); };
    try { paint(localStorage.getItem('freevideo.studio.compact') === '1'); } catch { paint(false); }
    const onCompact = event => { if (controls.isConnected) paint(event.detail); else window.removeEventListener('fv-studio-compact', onCompact); };
    window.addEventListener('fv-studio-compact', onCompact);
}
