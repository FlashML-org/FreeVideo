import { app } from '../../scripts/app.js';
import { openSetup } from './setup.js';
import { wordmark } from './branding.js';
import { createUpdateNotice, createVersionInfo } from './updates.js';

const languageOverride = typeof location !== 'undefined'
    ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en'
    && String(navigator.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;
const preference = 'freevideo.view';
let toolbar, open, selected, currentView = 'nodes';
const style = document.createElement('link');
style.rel = 'stylesheet'; style.href = new URL('./view_navigation.css', import.meta.url).href;
document.head.append(style);

export function preferredView() {
    try { return localStorage.getItem(preference); } catch { return null; }
}

export function viewChanged(view, node) {
    currentView = view;
    if (node) selected = node;
    try { localStorage.setItem(preference, view); } catch { /* Private browsing still works. */ }
    refreshNavigation();
}

export function viewSwitch(view, onStudio, onNodes) {
    const group = document.createElement('div'); group.className = 'fv-view-switch';
    group.setAttribute('role', 'group'); group.setAttribute('aria-label', t('FreeVideo view', 'FreeVideo 视图'));
    for (const [id, label, action] of [['studio', t('Create', '创作面板'), onStudio], ['nodes', t('Nodes', '节点视图'), onNodes]]) {
        const button = document.createElement('button'); button.type = 'button';
        button.textContent = label; button.dataset.view = id;
        button.setAttribute('aria-pressed', String(view === id)); button.onclick = action;
        group.append(button);
    }
    return group;
}

export function refreshNavigation() {
    if (!toolbar) return;
    const nodes = (app.graph?._nodes || []).filter(n => n.comfyClass === 'FreeVideoGenerate' || n.type === 'FreeVideoGenerate');
    if (!nodes.includes(selected)) selected = nodes[0];
    toolbar.hidden = !selected || currentView === 'studio';
    for (const button of toolbar.querySelectorAll('[data-view]')) {
        button.setAttribute('aria-pressed', String(button.dataset.view === currentView));
    }
}

export function installNavigation(openStudio) {
    open = openStudio;
    if (toolbar) { refreshNavigation(); return; }
    toolbar = document.createElement('aside'); toolbar.className = 'fv-view-navigation';
    toolbar.setAttribute('aria-label', t('FreeVideo workspace', 'FreeVideo 工作区'));
    const row = document.createElement('div'); row.className = 'fv-view-row';
    const brand = wordmark();
    const choose = () => {
        const focused = Object.values(app.canvas?.selected_nodes || {}).find(n => n.comfyClass === 'FreeVideoGenerate' || n.type === 'FreeVideoGenerate');
        refreshNavigation(); if (focused || selected) open(focused || selected);
    };
    const settings = document.createElement('button'); settings.type = 'button';
    settings.textContent = t('Settings', '设置'); settings.className = 'fv-view-settings'; settings.onclick = openSetup;
    row.append(brand, viewSwitch('nodes', choose, () => {}), createVersionInfo(cn).element, settings); toolbar.append(row);
    let shown = false;
    try { shown = localStorage.getItem('freevideo.view-guide') === '1'; } catch { /* Show once this session. */ }
    if (!shown) {
        const guide = document.createElement('div'); guide.className = 'fv-view-guide';
        const text = document.createElement('span');
        text.textContent = t('Create here, connect tools in Nodes. Switching keeps your inputs and results.', '在创作面板生成，在节点视图连接工具。切换会保留输入和结果。');
        const dismiss = document.createElement('button'); dismiss.type = 'button'; dismiss.textContent = t('Got it', '知道了');
        dismiss.onclick = () => {
            try { localStorage.setItem('freevideo.view-guide', '1'); } catch {}
            // Fold the tip away rather than letting the toolbar jump.
            const reduce = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (reduce || typeof guide.animate !== 'function') { guide.remove(); return; }
            // The tip set the toolbar's width; contract the bar in the same motion.
            const before = toolbar.getBoundingClientRect().width;
            guide.style.display = 'none';
            const after = toolbar.getBoundingClientRect().width;
            guide.style.display = '';
            const height = guide.getBoundingClientRect().height;
            guide.style.overflow = 'hidden'; guide.style.whiteSpace = 'nowrap';
            const timing = {duration: 260, easing: 'cubic-bezier(.4,0,.2,1)'};
            if (Math.abs(before - after) > 1) toolbar.animate([{width: `${before}px`}, {width: `${after}px`}], timing);
            // The text is gone before the bar has narrowed enough to clip it.
            guide.animate([{opacity: 1, height: `${height}px`, marginTop: '10px'}, {opacity: 0, offset: .4},
                {opacity: 0, height: '0px', marginTop: '0px', paddingTop: '0px', paddingBottom: '0px'}],
            {...timing, fill: 'forwards'}).finished.then(() => guide.remove(), () => guide.remove());
        };
        guide.append(text, dismiss); toolbar.append(guide);
    }
    toolbar.append(createUpdateNotice(cn).element);
    document.body.append(toolbar); refreshNavigation();
    // health.js asks for this before it reports the toolbar missing. A
    // workspace removed without closing must not keep the toolbar hidden.
    globalThis.window?.addEventListener('freevideo-navigation-refresh', () => {
        if (currentView === 'studio' && !document.querySelector('dialog.fv-studio[open]')) currentView = 'nodes';
        refreshNavigation();
    });
}
