// State the stylesheets need about a node's descendants, kept as attributes on the node.
// CSS :has() makes the browser re-check the whole dialog whenever any node is added, removed
// or changed, which made every view switch slow; these attributes change only where the state changes.
const dis = host => !!host.querySelector('input:disabled');
const shown = (host, selector) => !!host.querySelector(selector);
const kid = (host, test) => [...host.children].some(test);
const RULES = [
    ['dvs', 'dialog.fv-studio', host => kid(host, c => c.matches('.fv-header') && !!c.querySelector('.fv-view-switch'))],
    ['seldis', '.fv-select', host => kid(host, c => c.matches('select:disabled'))],
    ['selhid', '.fv-select', host => kid(host, c => c.matches('select[hidden]'))],
    ['tb', '.fv-view-navigation', host => kid(host, c => c.matches('.fv-topbar'))],
    ['vdot', '.fv-version-info', host => shown(host, '.fv-version-dot')],
    ['dis', '.fv-two-pass,.fv-first-frame,.fv-swap-sound,.fv-ed-first-frame,.fv-ed-duration,.fv-at-row,.fv-at-num,.fv-batch-lines', dis],
    ['prog', '.fv-stage', host => shown(host, '.fv-generation-progress:not([hidden])')],
    ['video', '.fv-stage', host => shown(host, 'video')],
    ['vs', '.fv-header', host => shown(host, '.fv-view-switch')],
    ['edit', '.fv-chain-part', host => shown(host, '.fv-chain-part-text:not(:disabled):not([hidden])')],
    ['textdis', '.fv-chain-part', host => shown(host, '.fv-chain-part-text:disabled')],
    ['queued', '.fv-chain-position', host => shown(host, '.fv-chain-queued')],
    ['note', '.fvs-console-status', host => kid(host, c => c.matches('.fv-add-video-note,.fv-length-why') && !c.hidden)],
    ['enh', '.fv-enhance-bar', host => kid(host, c => !c.hidden && !c.matches('.fv-enhance-toggle'))],
    ['cmp', '.fv-preview-column', host => shown(host, '.fv-stage[data-comparing=true]')],
    ['nextprog', '.fv-preview-column', host => shown(host, '.fv-stage[data-next=true],.fv-stage .fv-generation-progress:not([hidden])')],
    ['more', '.fv-runrow', host => kid(host, c => c.matches('.fv-key-more') && !c.hidden)],
    ['issue', '.fv-ed-gen-note,.fv-ed-notice,.fv-ed-voice-note,.fv-ed-voice-install-note', host => shown(host, '.fv-issue-button')],
    ['clippop', '.fv-editor', host => shown(host, '.fv-ed-clip-popover:not([hidden])')],
    ['lib', '.fv-editor', host => shown(host, '.fv-ed-library[data-open=true]')],
    ['open', '.fv-ed-track-heads', host => kid(host, c => c.matches('[data-open=true]'))],
    ['voice', '.fv-editor', host => shown(host, '.fv-ed-genbar[data-voice=true]')],
    ['tray', '.fv-ed-gen-tray', host => kid(host, c => !c.hidden)],
    ['descdis', '.fv-ed-description-box', host => shown(host, '.fv-ed-description:disabled')],
    ['pass', '.fv-ed-generate', host => !!host.nextElementSibling?.matches('.fv-ed-pass-options:not([hidden])')],
    ['nodev', '.fv-ed-record-side', host => shown(host, '.fv-ed-record-device[hidden]')],
    ['rec', '.fv-editor', host => shown(host, '.fv-ed-record:not([hidden])')],
    ['pop', '.fv-body,:root', host => shown(host, '.fvs-tray[data-fv-pop][open]')],
    ['fp', ':root', host => shown(host, '.fvp-pop[data-open=true]')],
    ['sec', '.fvp-pop-body section,.fvp-pop-body .fv-section,.fvp-pop-body .fv-sampling-settings',
        host => kid(host, c => !c.matches('[hidden],.fv-label,.fvs-size-fields,.fv-shapes,:empty'))],
    ['six', '.fvp-shapes', host => kid(host, c => c.matches('button:nth-of-type(6)'))],
    ['four', '.fvp-size-row', host => !!host.querySelector(':scope>.fvp-sizes>button:nth-of-type(4)')],
    ['extra', '.fvp-extra', host => shown(host, ':is(.fv-first-frame,.fv-length-plan,.fv-length-model):not([hidden])')],
    ['ppr', '.fv-panel[data-freevideo=result]', host => kid(host, c => c.matches('.fv-generation-progress:not([hidden])'))],
    ['now', '.fv-header,.fv-chat-side-running', host => shown(host, '.fv-autopilot-now:not([hidden])')],
    ['cmore', '.fv-chat-conversation-row', host => kid(host, c => c.matches('.fv-chat-more:not([hidden])'))],
    ['media', '.fv-chat-preview', host => kid(host, c => c.matches('img,video'))],
    ['prev', '.fv-chat-job', host => shown(host, '.fv-chat-preview')],
    ['fr', '.fv-result-links', host => kid(host, c => c.matches('.fv-image-failure-reason'))],
    ['img', '.fv-cast-choose,.fv-library-media', host => shown(host, 'img')],
    ['photo', '.fv-cast-dialog', host => shown(host, '.fv-cast-photo:not([hidden])')],
    ['foot', '.fv-cg-foot', host => kid(host, c => c.matches('.fv-cg-note,.fv-cp-rest') && !c.hidden)],
    ['pbn', '.fv-ed-speed-badges', host => !!host.nextElementSibling?.matches('.fv-ed-picture-badge')],
    ['pbc', '.fv-ed-clip', host => shown(host, '.fv-ed-picture-badge')],
    ['pbv', '.fv-ed-clip', host => shown(host, '.fv-ed-picture-badge:not([hidden])')],
    ['noted', '.fv-ed-track-view', host => shown(host, '.fv-ed-track-row[data-noted=true]')],
    ['a2', '.fv-ed-track-view', host => shown(host, '.fv-ed-track-row[data-track=a2]')],
    ['empty', '.sgf-rth', host => shown(host, '.sgf-img[data-empty=true]')],
    ['audtab', '.fv-ed-adjust', host => shown(host, '[data-adjust-tab=audio][aria-selected=true]')],
    ['seg', '.fv-ed-adjust-row', host => shown(host, '.fv-ed-adjust-segmented')],
    ['btn', '.fv-ed-adjust-row', host => shown(host, 'button')],
    ['axis', '.fv-ed-adjust-row', host => shown(host, '[data-axis]')],
    ['compat', '.fv-preview-space,.fv-stage,.fv-generation-progress', host => shown(host, '.fv-generation-compatibility:not([hidden])')],
    ['mb', '.fv-tour-pop', host => shown(host, '.fv-tour-mediabox')],
    ['still', '.fv-whatsnew-host', host => shown(host, '.fv-whatsnew-still')],
    ['wstage', '.fv-whatsnew-host', host => shown(host, '.fv-whatsnew-stage')],
    ['un', '.st-update-card', host => kid(host, c => c.matches('.fv-update-notice[hidden]'))],
    ['sw', '.st-row', host => kid(host, c => c.matches('.st-re') && c.children.length === 1 && c.firstElementChild.matches('input.st-sw'))],
    ['rm', '.fv-release-rich', host => kid(host, c => c.matches('.fv-release-top') && kid(c, m => m.matches('.fv-release-media:not([hidden])')))],
    ['tag', '.fv-release-list li', host => shown(host, '.fv-release-tag')],
    ['kids', '.fv-chat-rail-notices,.fv-chat-notice-row,.fv-mt>.fv-cast-libraries,.fv-library-dock', host => kid(host, c => !c.hidden)],
    ['dot', '.fv-library-menu-item', host => shown(host, '.fv-library-dot')],
    ['sempty', '.fv-library-caption', host => shown(host, 'strong:empty')],
    ['cn', '.fv-chat-slot', host => shown(host, '.fv-chat-notice:not([hidden])')],
    ['act', '.fv-chat-notice-row', host => kid(host, c => c.matches('.fv-issue-button,.fv-chat-notice-action') && !c.hidden)],
    ['hello', '.fv-chat-log', host => kid(host, c => c.matches('.fv-chat-hello'))],
    ['fh', '.fv-chat-job', host => kid(host, c => c.matches('.fv-chat-fail-head'))],
    ['flat', '.fv-chat-job', host => kid(host, c => c.matches('.fv-chat-preview:not([data-shape=tall])'))],
    ['font', '.fv-select', host => kid(host, c => c.matches('.fv-ed-text-font'))],
    ['libopen', ':root', host => shown(host, 'dialog.fv-library[open]')],
    ['cnt', '.fv-iv-prompt-bar', host => shown(host, '.fv-iv-prompt-count:not([hidden])')],
    ['chk', '.fv-image-enhance', host => shown(host, '.fv-enhance-toggle input:checked')],
    ['pin', '.fv-iv-li', host => shown(host, '.fv-iv-pin[aria-pressed=true]')],
    ['unl', '.fv-iv-tile', host => shown(host, 'img[data-loaded=false]')],
    ['miss', '.fv-iv-pic', host => shown(host, '.fv-iv-picture[data-missing=true]')],
    ['btn1', '.fv-iv-pic', host => kid(host, c => c.matches('button'))],
    ['es', '.fv-mt-cards', host => kid(host, c => c.matches('.fv-mt-slot-empty'))],
    ['sw2', 'label.fv-check', host => kid(host, c => c.matches('input[role=switch]'))],
];
const set = (host, id, on) => { if (host.hasAttribute('data-hx-' + id) !== on) host.toggleAttribute('data-hx-' + id, on); };
const refresh = (host, only) => {
    for (const [id, selector, test] of RULES) if ((!only || only.has(selector)) && host.matches(selector)) set(host, id, test(host));
};
const hosts = node => {
    const out = [];
    for (let at = node; at && at.nodeType === 1; at = at.parentElement) out.push(at);
    return out;
};
const scan = (root, found) => {
    for (const [, selector] of RULES) {
        if (root.nodeType !== 1 && root.nodeType !== 9) continue;
        if (root.matches?.(selector)) found.add(root);
        for (const host of root.querySelectorAll(selector)) found.add(host);
    }
};
const focusState = event => {
    const console = event.target.closest?.('.fvs-console');
    if (!console) return;
    const on = event.type === 'focusin';
    console.toggleAttribute('data-hx-focus', on && event.target.matches('textarea:focus-visible'));
    console.toggleAttribute('data-hx-ta', on && event.target.matches('.fv-prompt-editor textarea:focus'));
};

export function installHasMirror(doc = document) {
    if (doc.__hasMirror) return;
    doc.__hasMirror = true;
    const queue = new Set();
    const flush = () => {
        for (const host of queue) refresh(host);
        queue.clear();
    };
    const observer = new MutationObserver(records => {
        for (const record of records) {
            if (record.type === 'childList') {
                for (const node of [...record.addedNodes]) if (node.nodeType === 1) { scan(node, queue); if (node.previousElementSibling) queue.add(node.previousElementSibling); }
                if (record.previousSibling?.nodeType === 1) queue.add(record.previousSibling);
                for (const host of hosts(record.target)) queue.add(host);
            } else if (record.target.nodeType === 1) {
                for (const host of hosts(record.target)) queue.add(host);
                if (record.target.previousElementSibling) queue.add(record.target.previousElementSibling);
            }
        }
        flush();
    });
    const start = () => {
        const all = new Set();
        scan(doc, all);
        for (const host of all) refresh(host);
        observer.observe(doc, {childList: true, subtree: true, attributes: true,
            attributeFilter: ['hidden', 'disabled', 'open', 'data-open', 'data-voice', 'data-next', 'data-comparing', 'data-shape', 'data-noted',
                'data-track', 'data-empty', 'data-loaded', 'data-missing', 'aria-pressed', 'aria-selected']});
    };
    start();
    doc.addEventListener('change', event => { for (const host of hosts(event.target)) refresh(host); }, true);
    let hovered = null;
    doc.addEventListener('pointerover', event => {
        const heads = event.target.closest?.('.fv-ed-track-head[data-track=a2]')?.closest('.fv-ed-track-heads') || null;
        if (heads === hovered) return;
        hovered?.removeAttribute('data-hx-a2hover');
        heads?.setAttribute('data-hx-a2hover', '');
        hovered = heads;
    }, true);
    doc.addEventListener('focusin', focusState, true);
    doc.addEventListener('focusout', focusState, true);
    doc.addEventListener('pointerover', event => {
        const inside = !!event.target.closest?.('.fvs-tray[data-fv-pop][open]');
        if (doc.documentElement.hasAttribute('data-hx-pophover') !== inside) doc.documentElement.toggleAttribute('data-hx-pophover', inside);
    }, true);
}
installHasMirror();
