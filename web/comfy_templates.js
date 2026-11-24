// ComfyUI's own Templates window, shown over the Nodes view: FreeVideo says its search box and its first category in the
// interface language (ComfyUI's Chinese file reads the search box as "搜索占位符", and the category name is not translated),
// and comfy_templates.css brings the window into the light theme. The window is added to the page body when it opens.
import { isChinese } from './i18n.js';
export const chinese = isChinese();

// English text of a ComfyUI-made label -> the Chinese FreeVideo shows in its place.
export const LABELS = {Popular: '热门'};
export const SEARCH = {'搜索占位符': '搜索模板'};

export function polish(window_) {
    if (!chinese) return;
    for (const input of window_.querySelectorAll('input')) if (SEARCH[input.placeholder]) input.placeholder = SEARCH[input.placeholder];
    for (const span of window_.querySelectorAll('span,h2,h3')) {
        if (span.children.length) continue;
        const text = span.textContent.trim();
        if (LABELS[text] && span.textContent !== LABELS[text]) span.textContent = LABELS[text];
    }
}

export function watchTemplates(body = document.body) {
    if (!body || body.__fvTemplates) return;
    body.__fvTemplates = true;
    const follow = node => {
        if (node.nodeType !== 1 || !node.matches?.('[role=dialog]') || node.__fvWatched) return;
        node.__fvWatched = true; polish(node);
        const inner = new MutationObserver(() => polish(node));
        inner.observe(node, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['placeholder']});
        new MutationObserver((_, self) => { if (!node.isConnected) { inner.disconnect(); self.disconnect(); } }).observe(body, {childList: true});
    };
    new MutationObserver(records => { for (const record of records) record.addedNodes.forEach(follow); }).observe(body, {childList: true});
    body.querySelectorAll(':scope>[role=dialog]').forEach(follow);
}
