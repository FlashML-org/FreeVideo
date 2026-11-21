// Settings: a room inside the FreeVideo shell, not a sheet over it. The top bar stays (with "Settings" lit and the five
// view tabs still working); a glass dock on the left lists the pages, a dock on the right shows this computer, and the
// page in between is centred on the window. Pages: Models (installation and repair, picture models, add-on models,
// folders), Downloads, Generation, Appearance, Assistant, Storage, Page check, Advanced (compatibility, reserved memory,
// performance measurement), Version & updates, Getting started. Every function of the earlier settings sheet is here;
// the technical parts (folders, access token, speed tables, logs) are folded away. Esc closes and gives focus back.
// setup.js re-exports openSetup from here, so every caller (top bar, failure cards, palette) opens this room.
import { setTip } from './tip_words.js';
import { cutTip } from './cut_tip.js';
import { api } from "../../scripts/api.js";
import './fv_select.js';
import { compatibilityCard } from './compatibility.js';
import { listenDialogKey } from './dialog_keys.js';
import { animateDetails } from './motion.js';
import { createGuideReplayEntry } from './first_run_guide.js';
import { issueButton } from './issue_button.js';
import { agentConfirmSettings } from './agent_confirm_settings.js';
import { privacySettings } from './private_settings.js';
import { previewStyle, loadPreviewStyle, applyPreviewStyle, onPreviewStyle } from './preview_style.js';
import { setMonitorShown, onMonitorShown } from './monitor_panel.js';
import { preloadMode, setPreloadMode, onPreload } from './preload_setting.js';
import { createUpdateNotice, releaseNotesInto, productVersion } from './updates.js';
import { createImageGenClient } from './image_generation.js';
import { IMAGE_MODELS, modelState, pausedText, continueUse } from './image_view_models.js';
import { swapLoraFailureText } from './swap_lora.js';
import { MODULES } from './health.js';
import { userMessage } from './user_message.js';
import { createVerdicts, describeVerdict, feedbackDraft } from './lora_check.js';
import { feedbackIssueURL } from './feedback.js';
import { el, icon, tip, button, setText, row, card, groupHead, groupNote, slot, fold, switchify, checkify, segmented, radioList, stepper, field, bar } from './settings_kit.js';

if (typeof document !== 'undefined') {
    const css = document.createElement('link');
    css.rel = 'stylesheet'; css.href = new URL('./glass/settings.css', import.meta.url).href;
    document.head.append(css);
}

import { nearestAllowed } from './field_note.js';
import { cn, t, INSTALLED_HINT, gib, bytesText, atLeast, WAIT_REFUSAL, reportable, failureText, setupError, SECTIONS, sectionOf } from './settings_words.js';
export { failureText, sectionOf, SECTIONS, bytesText };

let current;
export const settingsOpen = () => Boolean(current?.open);

// Esc closes Settings at once and a save already sent is not taken back, so it can fail with Settings gone. A warning
// that lands in one of the closed room's status lines after it closed is shown as a failure notice instead: the
// setting's name with the line's own words, and a way back to its page (failures also stay in the notification list).
// Watching starts as Settings starts to close (a failure during the fade is not seen either) and stops after ten
// minutes; every save answers well before that.
const LATE_WATCH_MS = 10 * 60 * 1000;
const lineText = line => { const copy = line.cloneNode(true); copy.querySelectorAll('button, a').forEach(node => node.remove()); return copy.textContent.trim(); };
function watchLateFailures(room) {
    const seen = new Map([...room.querySelectorAll('[role=status]')].map(line => [line, lineText(line)]));
    const report = async (line, text) => {
        const page = line.closest('section.st-pane'), box = line.closest('.st-card');
        let head = box?.previousElementSibling;
        while (head && !head.matches('h3.st-gh')) head = head.previousElementSibling;
        // The setting's name, then the line's own words; "Open Settings" goes back to its page.
        const name = head?.textContent.trim() || page?.getAttribute('aria-label') || t('Settings', '设置');
        const {toast} = await import('./notifications.js');
        toast(`${name} · ${text}`, {tone: 'fail', action: {label: t('Open Settings', '打开设置'), use: t('Go back to this setting', '回到这项设置'),
            run: () => openSetup({section: page?.dataset.pane || null})}});
    };
    const observer = new MutationObserver(() => {
        for (const line of room.querySelectorAll('[role=status]')) {
            const text = lineText(line), before = seen.get(line);
            seen.set(line, text);
            if (text && text !== before && /\bis-(warn|fail)\b/.test(line.className)) report(line, text);
        }
    });
    observer.observe(room, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['class']});
    setTimeout(() => observer.disconnect(), LATE_WATCH_MS);
}

export async function openSetup(options) {
    // A click passes its event here; only a plain object can name a section to show.
    const section = options?.constructor === Object ? options.section : null;
    if (current?.open) { current.dispatchEvent(new CustomEvent('fv-setup-section', {detail: section || 'focus'})); return current; }
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = el('dialog', 'fv-set'); current = dialog;
    dialog.setAttribute('aria-label', t('FreeVideo settings', 'FreeVideo 设置'));
    dialog.dataset.fvTipScope = '';
    const disposers = [];
    const later = fn => disposers.push(fn);
    // Text cut short by an ellipsis (graphics card, install folder, a row's line) shows in full on hover. Runs in the
    // window's capture phase, before the tip layer's document listener picks the control.
    const CLIPPED = '.st-gpu,.st-facts3 dd,.st-facts dd,.st-rt span,.st-ht h2,.st-ht p,.st-detail,.st-ni .lbl';
    const onClipHover = event => {
        const node = event.target instanceof Element && dialog.contains(event.target) ? event.target.closest(CLIPPED) : null;
        if (!node || (node.dataset.fvTipName && !node.dataset.fvTipClip)) return;
        const text = node.textContent.trim();
        if (text && node.scrollWidth > node.clientWidth + 1) {
            const label = node.matches('dd') ? node.previousElementSibling?.textContent : node.matches('.st-gpu') ? t('Graphics card', '显卡')
                : node.matches('.st-rt span') ? node.parentElement.querySelector('b')?.textContent : '';
            node.dataset.fvTipClip = ''; node.dataset.fvTipName = (label || '').trim() || text; node.dataset.fvTipUse = text;
        } else if (node.dataset.fvTipClip != null) { delete node.dataset.fvTipClip; delete node.dataset.fvTipName; delete node.dataset.fvTipUse; }
    };
    window.addEventListener('pointerover', onClipHover, true);
    later(() => window.removeEventListener('pointerover', onClipHover, true));

    // ── The top bar: the same bar as every view (topbar.js takes the header over); a tab goes to that view ──
    const header = el('div', 'fv-header');
    try {
        const [{viewSwitch}, {currentView}] = await Promise.all([import('./view_navigation.js'), import('./shell.js')]);
        const tabs = viewSwitch(currentView(), () => {}, () => {}, () => {}, () => {}, () => {});
        tabs.addEventListener('click', event => {
            const tab = event.target.closest?.('[data-view]');
            if (!tab) return;
            event.preventDefault(); event.stopImmediatePropagation();
            const target = tab.dataset.view;
            close().then(async () => { try { await (await import('./shell.js')).go(target); } catch { /* The view stays. */ } });
        }, true);
        header.append(tabs);
    } catch { header.hidden = true; }

    // ── Left dock: title, search, pages ──
    const left = el('aside', 'st-dock l fv-glass fv-g1');
    const dockHead = el('div', 'st-dh'); const title = el('h2', '', t('Settings', '设置'));
    const closeButton = button('', {iconName: 'x', kind: 'icon', cls: 'st-close'});
    closeButton.setAttribute('aria-label', t('Close settings', '关闭设置'));
    tip(closeButton, t('Close', '关闭'), t('Back to where you were', '回到打开设置前的画面'), {key: 'Esc'});
    dockHead.append(title, closeButton);
    const search = el('input'); search.type = 'search'; search.placeholder = t('Search settings', '搜索设置');
    search.setAttribute('aria-label', t('Search settings', '搜索设置')); search.spellcheck = false; search.autocomplete = 'off';
    const searchBox = el('label', 'st-search'); searchBox.append(icon('search', 'fv-i s'), search);
    tip(search, t('Search settings', '搜索设置'), t('Find a setting by its name', '按名称查找设置'));
    const railSearch = button('', {iconName: 'search', kind: 'icon', cls: 'st-rail-search'}); railSearch.setAttribute('aria-label', t('Search settings', '搜索设置'));
    tip(railSearch, t('Search settings', '搜索设置'), t('Find a setting by its name', '按名称查找设置'));
    railSearch.onclick = () => { left.classList.add('is-wide'); search.focus(); };
    search.addEventListener('blur', () => { if (!search.value) left.classList.remove('is-wide'); });
    const nav = el('nav', 'st-nav'); nav.setAttribute('aria-label', t('Settings pages', '设置页面'));
    const navItems = new Map();
    for (const group of SECTIONS) {
        const block = el('div', 'st-ngroup'); block.append(el('div', 'st-ng', t(...group.group)));
        for (const item of group.items) {
            const entry = el('button', 'st-ni'); entry.type = 'button'; entry.dataset.section = item.id;
            const label = el('span', 'lbl'), word = el('span', 'w', t(...item.name)); word.dataset.t = t(...item.name); label.append(word);
            entry.append(icon(item.icon, 'fv-i s'), label, el('span', 'end'));
            entry.setAttribute('aria-label', t(...item.name));
            tip(entry, t(...item.name), t('Show this page', '打开这一页'));
            entry.onclick = () => show(item.id);
            block.append(entry); navItems.set(item.id, entry);
        }
        nav.append(block);
    }
    // Up and down move between the pages of the dock.
    listenDialogKey(nav, 'keydown', event => {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        const items = [...navItems.values()].filter(e => !e.hidden), i = items.indexOf(document.activeElement);
        if (i < 0) return;
        event.preventDefault();
        const next = items[(i + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length];
        next.focus(); show(next.dataset.section);
    });
    const searchEmpty = el('p', 'st-nav-empty', t('No setting matches', '没有找到相关设置')); searchEmpty.hidden = true; nav.append(searchEmpty);
    const dockFoot = el('div', 'st-df'); const versionLine = el('span', 'st-ver', 'FreeVideo');
    const escKey = el('kbd', 'st-kbd', 'Esc'); dockFoot.append(versionLine, escKey);
    left.append(dockHead, railSearch, searchBox, nav, dockFoot);

    // ── Right dock: this computer ──
    const right = el('aside', 'st-dock r fv-glass fv-g1'); right.setAttribute('aria-label', t('This computer', '本机'));

    // ── Main column and pages; the phone width shows a page picker at the top instead of the dock ──
    const main = el('main', 'st-main'); const column = el('div', 'st-col');
    const phoneHead = el('div', 'st-mhead');
    const picker = button('', {cls: 'st-picker'}); picker.setAttribute('aria-haspopup', 'menu'); picker.setAttribute('aria-expanded', 'false');
    const pickerText = el('span', 'st-btn-t'); picker.replaceChildren(pickerText, icon('chev', 'fv-i s'));
    tip(picker, t('Settings pages', '设置页面'), t('Choose another settings page', '切换到其他设置页'));
    const phoneClose = button('', {iconName: 'x', kind: 'icon', cls: 'st-close'}); phoneClose.setAttribute('aria-label', t('Close settings', '关闭设置'));
    tip(phoneClose, t('Close', '关闭'), t('Back to where you were', '回到打开设置前的画面'), {key: 'Esc'});
    const pickerMenu = el('div', 'st-menu fv-glass fv-g3'); pickerMenu.setAttribute('role', 'menu'); pickerMenu.hidden = true;
    phoneHead.append(picker, phoneClose, pickerMenu);
    column.append(phoneHead); main.append(column);
    const panes = new Map();
    const pane = (id, heading, acts = []) => {
        const page = el('section', 'st-pane'); page.dataset.pane = id; page.hidden = true;
        page.setAttribute('aria-label', heading);
        const head = el('div', 'st-ph'); const h = el('h1', '', heading); h.tabIndex = -1;
        const a = el('div', 'acts'); a.append(...acts);
        head.append(h, a); page.append(head);
        column.append(page); panes.set(id, page);
        return page;
    };

    // The shared report button, with this room's hover words.
    const reportTip = node => tip(node, t('Report a problem', '提交问题'), t('Send the problem with its diagnostics', '附上诊断信息提交问题'));
    // With the caught error passed too, text that only a browser or a program would say is replaced by a plain sentence;
    // the report keeps what was said before.
    const fault = (target, message, error, fallback) => {
        const known = failureText(message), text = error && known === String(message || '') ? userMessage(error, t, fallback) : known;
        target.textContent = text;
        if (text && reportable(message)) target.append(reportTip(issueButton(t, () => ({where: ['Settings / installation', '设置／安装'], message: known}))));
    };

    // ════════════ Models ════════════
    // When the video models are ready the card is not shown; Check installation stays in the page head.
    const headInspect = button(t('Check installation', '检查安装'), {iconName: 'cube', kind: 'fill'}); headInspect.hidden = true;
    tip(headInspect, t('Check installation', '检查安装'), t('Check models and fix what is missing', '检查模型并修复缺少的部分'));
    const models = pane('models', t('Models', '模型'), [headInspect]);
    // The video-generation card: one fixed height in every state; only its content changes.
    const hero = el('div', 'st-card st-hero'); hero.dataset.state = 'loading';
    const heroTop = el('div', 'st-hero-top');
    const heroIcon = el('span', 'st-hic'); heroIcon.append(icon('cube', 'fv-i'));
    const heroText = el('div', 'st-ht'); const heroTitle = el('h2', '', t('Reading installation settings…', '正在读取安装设置…'));
    const heroSub = el('p', ''); heroText.append(heroTitle, heroSub);
    const heroActs = el('div', 'st-ha');
    heroTop.append(heroIcon, heroText, heroActs);
    const heroBody = el('div', 'st-hero-body');
    hero.append(heroTop, heroBody);
    // The installer's own parts (state sentence, progress, counters), placed in the card's body.
    const state = el("p", 'st-state'); state.setAttribute("role", "status");
    const overall = el("div", 'st-prog'); overall.hidden = true;
    const overallText = el("div", 'l'); const overallBar = bar();
    overall.append(overallText, overallBar);
    const fileBar = bar('thin'); fileBar.hidden = true;
    const counters = el("div", 'st-count'); counters.hidden = true;
    const detail = el('p', 'st-detail'); detail.hidden = true;
    const plan = el("div", 'st-plan');
    // The card shows what needs doing, or the progress while working.
    heroBody.append(state, overall, counters, fileBar, detail);
    // The native progress interface (max/value) on the drawn bars, so render() below sets them as before.
    for (const meter of [overallBar, fileBar]) {
        let max = 1, value = 0;
        Object.defineProperty(meter, 'max', {get: () => max, set: v => { max = Number(v) || 1; meter.set(value / max); }});
        Object.defineProperty(meter, 'value', {get: () => value, set: v => { value = Number(v) || 0; meter.set(value / max); }});
    }
    const inspect = button(t("Check installation", "检查安装"), {kind: 'fill'});
    const use = button(t("Use existing", "使用已有安装"));
    const cancel = button(t("Stop setup", "停止安装"), {iconName: 'stop'}); cancel.hidden = true;
    const reviewPlan = button(t('Review plan', '查看计划'), {kind: 'primary'}); reviewPlan.hidden = true;
    heroActs.append(use, inspect, reviewPlan, cancel);
    const more = fold({title: t('Details and logs', '详细信息与日志'), sub: t('Kept for reporting a problem', '提交问题时可附上'), cls: 'st-logs'});
    const log = el("pre", 'st-pre fv-copyable'); more.body.append(log);
    const logCopy = button(t('Copy', '复制'), {iconName: 'copy', cls: 'is-sm'});
    logCopy.onclick = event => { event.preventDefault(); event.stopPropagation(); navigator.clipboard?.writeText(log.textContent).then(() => { setText(logCopy, t('Copied', '已复制')); setTimeout(() => setText(logCopy, t('Copy', '复制')), 1500); }, () => {}); };
    tip(logCopy, t('Copy', '复制'), t('Copy the log for a report', '复制日志，用于提交问题'));
    more.end.append(logCopy); more.element.hidden = true;
    models.append(hero, more.element);

    // Picture models: both always listed, each with its own state (installed, not installed, downloading, failed).
    const imageCard = card(); const imageRows = new Map();
    const imageHead = groupHead(t('Picture models', '图像模型'), el('span', '', ''));
    const IMAGE_WORDS = {
        'minimax-h3-image': t('Default for the image view; clear lettering in pictures', '图像视图默认模型，图中文字清晰'),
        'qwen-image-2.1-turbo': t('Another look, up to about 4 MP', '另一种画风，最大约 4 MP'),
        'moss-image-sft': t('Pictures from a text prompt, up to about 4 MP', '根据文字提示出图，最大约 4 MP'),
    };
    for (const model of IMAGE_MODELS) {
        const r = row({iconName: 'img', title: model.short, sub: IMAGE_WORDS[model.id] || '', cls: 'st-mrow'});
        const size = el('span', 'st-sz'); const chip = el('span', 'st-chip'); chip.hidden = true;
        const act = button(t('Install', '安装'), {iconName: 'down', kind: 'fill'}); act.hidden = true;
        const meter = bar('st-mbar'); meter.hidden = true;
        r.end.append(size, chip, act); r.element.append(meter);
        imageRows.set(model.id, {row: r, size, chip, act, meter, model});
        imageCard.append(r.element);
    }
    // Add-on models: Replace person's model, and every quality level prepared ahead.
    const addCard = card();
    const swapRow = row({iconName: 'person', title: t('Replace person model', '替换人物模型'), sub: t('Downloaded when Replace person is first used', '打开“替换人物”时自动下载'), cls: 'st-mrow'});
    const swapSize = el('span', 'st-sz', t('About 76 MiB', '约 76 MiB')); const swapChip = el('span', 'st-chip'); swapChip.hidden = true;
    const swapAct = button(t('Install', '安装'), {iconName: 'down', kind: 'fill'}); swapAct.hidden = true;
    const swapMeter = bar('st-mbar'); swapMeter.hidden = true;
    tip(swapAct, t('Install', '安装'), t('Download the Replace person model now', '现在下载替换人物模型'));
    swapRow.end.append(swapSize, swapChip, swapAct); swapRow.element.append(swapMeter);
    const samplingCaches = switchify(el('input')); samplingCaches.type = 'checkbox';
    samplingCaches.setAttribute('aria-label', t('Prepare all quality levels', '提前下载全部质量档位'));
    const samplingRow = row({iconName: 'layers', title: t('All quality levels', '全部质量档位'), sub: t('Prepared ahead, so switching levels needs no download; applies at the next check', '提前准备，切换档位不用再等下载；下次检查安装时生效'), end: [samplingCaches]});
    const samplingSize = el('span', 'st-sz'); samplingRow.end.prepend(samplingSize);
    tip(samplingCaches, t('All quality levels', '全部质量档位'), t('Include every quality level in the next installation', '下次安装时一并准备全部质量档位'));
    addCard.append(swapRow.element, samplingRow.element);
    // The first-use download is said on hover only, not as a standing line.
    const addHead = tip(groupHead(t('Add-on models', '附加模型')), t('Add-on models', '附加模型'), t('Also downloaded the first time they are used', '第一次用到时也会自动下载'));
    models.append(imageHead, imageCard, addHead, addCard);

    // Folders: set once and rarely again, folded away.
    const folders = fold({iconName: 'folder', title: t('Advanced: installation and model folders', '高级：安装位置与模型文件夹'), sub: ''});
    const inputs = el("div", 'st-sub');
    const rootLabel = el('label', 'st-label'); rootLabel.append(el('span', '', t("Installation folder", "安装位置")));
    const root = el("input"); root.type = "text"; root.spellcheck = false; root.autocomplete = 'off';
    rootLabel.append(field(root, {mono: true}));
    const extraLabel = el('label', 'st-label'); extraLabel.append(el('span', '', t("Other model folders (optional, one per line)", "其他模型文件夹（可选，每行一个）")));
    const extra = el("textarea"); extra.rows = 3; extra.spellcheck = false;
    extraLabel.append(field(extra, {mono: true}));
    const saveFolders = button(t('Save folders', '保存文件夹'), {kind: 'fill'});
    const folderStatus = slot();
    const folderRow = el('div', 'st-actions'); folderRow.append(folderStatus, saveFolders);
    const libraries = el('details', 'st-tfold'); const libSummary = el('summary'); libSummary.append(icon('chev', 'fv-i'), el('span', '', t("Folders found automatically", "自动发现的文件夹")));
    const paths = el("pre", 'st-pre fv-copyable'); libraries.append(libSummary, paths);
    const copy = switchify(el("input")); copy.type = "checkbox";
    copy.setAttribute('aria-label', t('Make separate copies when reusing models', '复用模型时创建独立副本'));
    const copyRow = row({title: t('Make separate copies when reusing models', '复用模型时创建独立副本'), sub: t('Uses extra disk space', '额外占用磁盘空间'), end: [copy]});
    inputs.append(rootLabel, extraLabel, folderRow, libraries);
    folders.body.append(inputs, copyRow.element);
    models.append(folders.element);

    // The plan sheet: what will be installed, the licenses and the agreement, over the room (nothing in the page moves).
    const sheet = el('dialog', 'st-dlg fv-glass fv-g3'); sheet.setAttribute('aria-labelledby', 'fv-set-plan-title');
    const sheetHead = el('header'); const sheetIcon = el('span', 'st-hic is-accent'); sheetIcon.append(icon('down', 'fv-i'));
    const sheetText = el('div'); const sheetTitle = el('h2', '', t('Confirm installation', '确认安装')); sheetTitle.id = 'fv-set-plan-title';
    const sheetLine = el('p', '', t('These files are downloaded or reused. Closing Settings does not stop the installation.', '将下载或复用以下文件。关闭设置不会中断安装。'));
    sheetText.append(sheetTitle, sheetLine);
    const sheetClose = button('', {iconName: 'x', kind: 'icon'}); sheetClose.setAttribute('aria-label', t('Close', '关闭'));
    tip(sheetClose, t('Close', '关闭'), t('Back to Settings; the plan stays', '回到设置，计划保留'), {key: 'Esc'});
    sheetHead.append(sheetIcon, sheetText, sheetClose);
    const sheetBody = el('div', 'bd'); sheetBody.append(plan);
    const acceptLabel = el("label", 'st-agree'); const accept = checkify(el("input")); accept.type = "checkbox";
    const acceptText = el('span', '', t("I accept this installation plan and the model and toolkit licenses", "我同意当前展示的安装计划和模型／工具包许可证"));
    acceptLabel.append(accept, acceptText); acceptLabel.hidden = true; sheetBody.append(acceptLabel);
    const sheetFoot = el('footer'); const blocked = slot('grow');
    const sheetCancel = button(t('Not now', '暂不安装'));
    const install = button(t("Install", "安装"), {kind: 'primary'}); install.disabled = true;
    const recheck = button(t('Check again', '重新检查'), {kind: 'primary'}); recheck.hidden = true;
    sheetFoot.append(blocked, sheetCancel, recheck, install);
    sheet.append(sheetHead, sheetBody, sheetFoot);
    sheet.addEventListener('cancel', event => { event.preventDefault(); sheet.close(); });
    listenDialogKey(sheet, 'keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); event.preventDefault(); sheet.close(); } });
    sheet.addEventListener('close', () => { if (dialog.open) (reviewPlan.hidden ? inspect : reviewPlan).focus?.({preventScroll: true}); });
    sheetClose.onclick = sheetCancel.onclick = () => sheet.close();
    const openSheet = () => {
        if (!sheet.open) { try { sheet.showModal(); } catch { return; } }
        (planId ? accept : recheck).focus?.();
    };
    reviewPlan.onclick = () => openSheet();

    // ════════════ Downloads ════════════
    const downloads = pane('downloads', t('Downloads', '下载'));
    const proxyMode = el('select');
    for (const [id, name] of [['auto', t('Auto', '自动')], ['proxy', t('Proxy only', '仅代理')], ['direct', t('Direct only', '仅直连')]]) {
        const option = el('option', '', name); option.value = id; proxyMode.append(option);
    }
    const proxySeg = segmented(proxyMode, {label: t('Connection', '连接方式'), tips: {
        auto: t('With a proxy, the faster route is chosen', '有代理时自动选择更快的一种'),
        proxy: t('Always download through the proxy', '始终通过代理下载'), direct: t('Never use the proxy', '不使用代理')}});
    const connRow = row({title: t('Connection', '连接方式'), sub: t('With a proxy, the faster route is chosen', '有代理时自动选择更快的一种'), end: [proxySeg]});
    const source = el('select');
    const SOURCES = [['auto', t('Automatic', '自动选择'), t('Recommended · picks the fastest measured source', '推荐 · 按测速选择最快的源')],
        ['official', t('International source', '国际源'), t('Where the models are first published', '模型的原始发布站点')],
        ['hf-mirror', t('International mirror', '国际镜像'), t('A mirror of the international source', '国际源的镜像站点')],
        ['modelscope', t('China source', '国内源'), t('Usually faster on networks in China', '国内网络通常更快')]];
    for (const [id, name] of SOURCES) { const option = el('option', '', name); option.value = id; source.append(option); }
    let measuredRates = {}, autoBest = '';
    const sourceList = radioList(source, {label: t('Download source', '下载源'), describe: value => {
        const sub = value === 'auto' && autoBest ? t(`Recommended · now the ${autoBest}`, `推荐 · 当前用${autoBest}`) : SOURCES.find(s => s[0] === value)?.[2] || '';
        const rate = measuredRates[value];
        if (rate == null) return {sub};
        const speedText = rate < 0 ? t('Unavailable', '未连通') : rate < .005 ? '<0.01 MiB/s' : `${rate.toFixed(rate < 1 ? 2 : 1)} MiB/s`;
        const line = el('span', rate < 0 ? 'off' : '', speedText);
        if (rate < 0) return {sub, end: [line]};
        const meter = bar(); meter.set(Math.min(1, rate / Math.max(...Object.values(measuredRates), .01)));
        return {sub, end: [line, meter]};
    }});
    const probeButton = button(t('Test again', '重新测速'), {iconName: 'retry', kind: 'quiet', cls: 'is-sm'});
    tip(probeButton, t('Test again', '重新测速'), t('Measure each download source again', '重新测量各下载源的速度'));
    const sourceStatus = slot('st-under'), speeds = el('div', 'st-speeds');
    const speedDetails = fold({title: t('Results for each file', '各文件的测速结果'), sub: t('Each kind of file downloads from its fastest source', '每类文件会从最快的源下载'), body: [speeds]});
    // Access token: optional and technical, folded.
    const auth = fold({iconName: 'key', title: t('Advanced: access token', '高级：访问令牌'), sub: t('Optional · kept for this session only', '可选 · 只在本次运行中有效')});
    const authIntro = el('p', 'st-p', t('Use your account’s download quota on the international source instead of the anonymous limit.', '在国际源上使用账号的下载额度，避开匿名限流。'));
    const hfToken = el('input'); hfToken.type = 'password'; hfToken.autocomplete = 'off'; hfToken.spellcheck = false;
    hfToken.setAttribute('aria-label', t('Access token', '访问令牌')); hfToken.placeholder = t('Paste a read token', '粘贴只读令牌');
    const applyToken = button(t('Use token', '应用令牌'), {kind: 'fill'});
    const clearToken = button(t('Clear', '清除本次令牌'));
    const tokenLink = el('a', 'st-link', t('Get a read token', '获取只读令牌'));
    tokenLink.href = 'https://huggingface.co/settings/tokens'; tokenLink.target = '_blank'; tokenLink.rel = 'noopener noreferrer';
    tokenLink.append(icon('right', 'fv-i s'));
    tip(tokenLink, t('Get a read token', '获取只读令牌'), t('Opens the account page in the browser', '在浏览器中打开账号页面'));
    const authStatus = slot();
    const tokenRow = el('div', 'st-actions'); tokenRow.append(tokenLink, el('span', 'grow'), clearToken, applyToken);
    const authBody = el('div', 'st-sub'); authBody.append(authIntro, field(hfToken, {mono: true}), tokenRow, authStatus);
    auth.body.append(authBody);
    downloads.append(groupHead(t('Connection', '连接')), card(connRow.element),
        groupHead(t('Download source', '下载源'), probeButton), sourceList, sourceStatus,
        speedDetails.element, auth.element);

    // ════════════ LoRA ════════════
    // The files in the LoRA folder and what the check says about each (usable, partly applied, cannot be used, why).
    const lora = pane('lora', 'LoRA');
    const loraRescan = button(t('Check again', '重新检查'), {iconName: 'retry', kind: 'fill'});
    tip(loraRescan, t('Check again', '重新检查'), t('Read the LoRA folder and check every file again', '重新读取 LoRA 文件夹并逐个检查'));
    lora.querySelector('.acts').append(loraRescan);
    const loraHead = groupHead(t('Files in the LoRA folder', 'LoRA 文件夹中的文件'), el('span', '', ''));
    const loraCard = card(); const loraStatus = slot('st-under');
    lora.append(loraHead, loraCard, loraStatus,
        groupNote(t('Add LoRAs in the create panel. FreeVideo models are already accelerated, so no speed LoRA is needed.', '在创作面板里添加 LoRA。FreeVideo 的模型已经加速，不需要加速 LoRA。')));
    const loraVerdicts = createVerdicts((path, options) => api.fetchApi(path, options));
    let loraLoaded = false, loraRun = 0;
    const loraRow = name => {
        const r = row({iconName: 'layers', title: name.split('/').pop(), sub: t('Checking…', '正在检查…'), cls: 'st-lrow'});
        setTip(r.title, {name});
        const chip = el('span', 'st-chip', t('Checking', '检查中')); r.end.append(chip);
        const more = el('div', 'st-sub st-ldetail'); more.hidden = true;
        r.paint = verdict => {
            const described = describeVerdict(verdict, t);
            r.element.dataset.state = !verdict ? 'unknown' : verdict.usable === false ? 'bad' : described?.partial ? 'partial' : 'ok';
            if (!verdict) { chip.className = 'st-chip'; chip.textContent = t('Not checked', '未能检查'); r.sub.className = ''; r.sub.textContent = t('The check did not answer; check again later', '检查没有回应，请稍后重新检查'); return; }
            if (verdict.usable === false) {
                chip.className = 'st-chip is-warn'; chip.textContent = t('Cannot be used', '不能使用');
                r.sub.className = 'is-warn'; r.sub.textContent = described?.reason || '';
                const report = button(t('Report a mistake', '报告误判'), {cls: 'is-sm', kind: 'fill'});
                tip(report, t('Report a mistake', '报告误判'), t('Open a prefilled report; the file itself is not sent', '打开预填好的问题报告，不会发送文件本身'));
                report.onclick = () => { const draft = feedbackDraft(name, verdict, described, t); window.open(feedbackIssueURL(draft.text, productVersion(), draft.diagnostics, draft.title), '_blank', 'noopener'); };
                more.replaceChildren(el('pre', 'st-pre fv-copyable', (described?.lines || []).join('\n') || t('No details', '没有更多信息')), Object.assign(el('div', 'st-actions'), {}));
                more.lastChild.append(report);
                r.element.tabIndex = 0; r.element.setAttribute('role', 'button'); r.element.setAttribute('aria-expanded', 'false');
                tip(r.element, t('Details', '详情'), t('Show why it cannot be used', '查看不能使用的原因'));
                const toggle = () => { more.hidden = !more.hidden; r.element.setAttribute('aria-expanded', String(!more.hidden)); };
                r.element.onclick = toggle;
                listenDialogKey(r.element, 'keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(); } });
            } else if (described?.partial) {
                chip.className = 'st-chip is-caution'; chip.textContent = t('Partly applied', '部分生效');
                r.sub.className = 'is-caution'; r.sub.textContent = t('Some layers apply on this model version', '在当前模型版本上部分层生效');
            } else { chip.className = 'st-chip is-ok'; chip.textContent = t('Usable', '可用'); r.sub.className = ''; r.sub.textContent = t('Works with the video model', '可用于视频模型'); }
        };
        return {r, more};
    };
    async function loadLoras() {
        const run = ++loraRun; loraLoaded = true; loraRescan.disabled = true;
        loraStatus.className = 'st-slot st-under'; loraStatus.textContent = t('Reading the LoRA folder…', '正在读取 LoRA 文件夹…');
        let names = [];
        try { const response = await api.fetchApi('/models/loras', {cache: 'no-store'}); if (!response.ok) throw new Error(response.statusText); names = await response.json(); }
        catch { loraStatus.className = 'st-slot st-under is-warn'; loraStatus.textContent = t('The LoRA folder could not be read. Check again.', '无法读取 LoRA 文件夹，请重新检查。'); loraRescan.disabled = false; loraCard.replaceChildren(); return; }
        const list = (Array.isArray(names) ? names : []).filter(n => typeof n === 'string');
        const usable = list.filter(n => n.toLowerCase().endsWith('.safetensors')), other = list.length - usable.length;
        loraVerdicts.clear(); loraCard.replaceChildren();
        loraHead.querySelector('.r').textContent = usable.length ? t(`${usable.length} files`, `${usable.length} 个文件`) : '';
        if (!usable.length) {
            loraCard.append(row({iconName: 'layers', title: t('No LoRA files yet', '还没有 LoRA 文件'), sub: t('Put .safetensors files in models/loras, then check again', '把 .safetensors 文件放进 models/loras 文件夹，再点“重新检查”')}).element);
            loraStatus.textContent = other ? t(`${other} files of other formats are not read.`, `另有 ${other} 个其他格式的文件不会被读取。`) : '';
            loraRescan.disabled = false; return;
        }
        const rows = usable.slice(0, 200).map(name => { const made = loraRow(name); loraCard.append(made.r.element, made.more); return [name, made.r]; });
        loraStatus.textContent = t('Checking each file…', '正在逐个检查…');
        let at = 0, bad = 0;
        const worker = async () => { while (at < rows.length && run === loraRun && dialog.open) { const [name, r] = rows[at++]; const verdict = await loraVerdicts.get(name); if (run !== loraRun) return; r.paint(verdict); if (verdict?.usable === false) bad++; } };
        await Promise.all([worker(), worker(), worker()]);
        if (run !== loraRun) return;
        loraRescan.disabled = false;
        loraStatus.textContent = [bad ? t(`${bad} cannot be used; open a row for the reason.`, `${bad} 个不能使用，点开该行查看原因。`) : t('Every file was checked.', '已检查全部文件。'),
            usable.length > 200 ? t('The first 200 files are listed.', '只列出前 200 个文件。') : '',
            other ? t(`${other} files of other formats are not read.`, `另有 ${other} 个其他格式的文件不会被读取。`) : ''].filter(Boolean).join(' ');
        navItems.get('lora').querySelector('.end').replaceChildren(...(bad ? [el('i', 'st-dot')] : []));
    }
    loraRescan.onclick = () => loadLoras();

    // ════════════ Generation ════════════
    const generation = pane('generation', t('Generation', '生成'));
    // The preview while generating is a frame sheet; the other choice plays it as a loop of the video in progress.
    // It applies to the preview on screen at once, on this page and on the phone page.
    const loopSwitch = el('input'); loopSwitch.type = 'checkbox'; loopSwitch.hidden = true;
    const choices = el('div', 'st-choices'); choices.setAttribute('role', 'radiogroup'); choices.setAttribute('aria-label', t('Preview during generation', '生成中的预览'));
    const choiceSheet = el('button', 'st-choice'); choiceSheet.type = 'button'; choiceSheet.setAttribute('role', 'radio');
    const sheetPic = el('span', 'pv st-pv-sheet'); for (let i = 0; i < 12; i++) sheetPic.append(el('i'));
    choiceSheet.append(sheetPic, el('span', 'cap', t('Frame sheet', '多帧拼图')));
    const choiceLoop = el('button', 'st-choice'); choiceLoop.type = 'button'; choiceLoop.setAttribute('role', 'radio');
    const loopPic = el('span', 'pv st-pv-loop'); const loopBadge = el('span', 'st-loop'); loopBadge.append(icon('retry', 'fv-i'), document.createTextNode(t('Loop', '循环')));
    loopPic.append(el('i'), loopBadge);
    choiceLoop.append(loopPic, el('span', 'cap', t('Loop playback', '循环播放')));
    tip(choiceSheet, t('Frame sheet', '多帧拼图'), t('Show several frames of the video in progress', '同时显示进行中视频的多帧'));
    tip(choiceLoop, t('Loop playback', '循环播放'), t('Play the video in progress as a loop', '把进行中的视频循环播放'));
    choices.append(choiceSheet, choiceLoop);
    const previewStatus = slot();
    const paintPreview = () => {
        loopSwitch.checked = previewStyle() === 'loop';
        choiceSheet.setAttribute('aria-checked', String(!loopSwitch.checked)); choiceLoop.setAttribute('aria-checked', String(loopSwitch.checked));
        choiceSheet.tabIndex = loopSwitch.checked ? -1 : 0; choiceLoop.tabIndex = loopSwitch.checked ? 0 : -1;
    };
    paintPreview();
    const pickPreview = wanted => { if (loopSwitch.disabled || loopSwitch.checked === wanted) return; loopSwitch.checked = wanted; loopSwitch.onchange(); };
    choiceSheet.onclick = () => pickPreview(false); choiceLoop.onclick = () => pickPreview(true);
    listenDialogKey(choices, 'keydown', event => { if (/^Arrow/.test(event.key)) { event.preventDefault(); pickPreview(!loopSwitch.checked); (loopSwitch.checked ? choiceLoop : choiceSheet).focus(); } });
    loopSwitch.onchange = async () => {
        const wanted = loopSwitch.checked ? 'loop' : 'sheet';
        loopSwitch.disabled = true; choices.setAttribute('aria-busy', 'true'); previewStatus.className = 'st-slot';
        previewStatus.textContent = t('When the loop is off, a frame sheet is shown.', '关闭循环时显示多帧拼图。');
        try {
            const saved = await request('preview-style', {style: wanted});
            applyPreviewStyle(saved.style, saved.chosen === true);
        } catch (error) {
            const known = failureText(error.message);
            previewStatus.textContent = known === String(error.message || '') ? userMessage(error, t, t('Could not save. Try again.', '没能保存，请重试。')) : known; previewStatus.className = 'st-slot is-warn';
        } finally { loopSwitch.disabled = false; choices.removeAttribute('aria-busy'); paintPreview(); }
    };
    previewStatus.textContent = t('Applies at once to the preview on screen, also on the phone page.', '立即应用到屏幕上的预览，手机页面也一样。');
    later(onPreviewStyle(paintPreview));
    loadPreviewStyle(api).then(paintPreview);
    // The 8-bit video is always written; this adds a 10-bit copy beside it. The row stays hidden where the packaged
    // encoder cannot write 10-bit, so no switch is shown that would do nothing.
    const tenSwitch = switchify(el('input')); tenSwitch.type = 'checkbox';
    tenSwitch.setAttribute('aria-label', t('Also save a high color depth copy', '同时保存高色深版本'));
    const tenRow = row({iconName: 'film', title: t('Also save a high color depth copy', '同时保存高色深版本'), sub: t('Adds a 10-bit video beside it, for color grading', '另存一份 10 位视频，适合调色'), end: [tenSwitch]});
    tip(tenSwitch, t('High color depth copy', '高色深版本'), t('Adds a 10-bit video beside it, for color grading', '另存一份 10 位视频，适合调色'));
    // Every finished picture is also copied into the output folder; the user can switch that off. The 10-bit row stays hidden.
    const imageSwitch = switchify(el('input')); imageSwitch.type = 'checkbox'; imageSwitch.checked = true;
    imageSwitch.setAttribute('aria-label', t('Also save pictures to the output folder', '同时将图片保存到 output 文件夹'));
    const imageRow = row({iconName: 'img', title: t('Also save pictures to the output folder', '同时将图片保存到 output 文件夹'), end: [imageSwitch]});
    tip(imageSwitch, t('Also save pictures to the output folder', '同时将图片保存到 output 文件夹'));
    tenRow.element.hidden = true;
    const outputCard = card(imageRow.element, tenRow.element); const outputHead = groupHead(t('Finished videos and pictures', '成片与图片'));
    let tenSaved = false, imageSaved = true;
    tenSwitch.onchange = async () => {
        const wanted = tenSwitch.checked; tenSwitch.disabled = true;
        try { tenSaved = (await request('output-settings', {ten_bit: wanted})).ten_bit === true; }
        catch { /* The switch returns to the saved state. */ }
        finally { tenSwitch.checked = tenSaved; tenSwitch.disabled = false; }
    };
    imageSwitch.onchange = async () => {
        const wanted = imageSwitch.checked; imageSwitch.disabled = true;
        try { imageSaved = (await request('output-settings', {image_copy: wanted})).image_copy !== false; }
        catch { /* The switch returns to the saved state. */ }
        finally { imageSwitch.checked = imageSaved; imageSwitch.disabled = false; }
    };
    (async () => {
        try {
            const response = await api.fetchApi('/freevideo/output-settings');
            const value = response.ok ? await response.json() : null;
            if (value) { imageSaved = value.image_copy !== false; imageSwitch.checked = imageSaved; }
            if (value?.available === true) { tenSaved = value.ten_bit === true; tenSwitch.checked = tenSaved; tenRow.element.hidden = false; }
        } catch { /* Without an answer the picture row shows its default and the 10-bit row stays hidden. */ }
    })();
    const previewFoot = el('div', 'st-foot'); previewFoot.append(previewStatus);
    generation.append(groupHead(t('Preview during generation', '生成中的预览')), card(choices, previewFoot), outputHead, outputCard);

    // ════════════ Appearance ════════════
    const appearance = pane('appearance', t('Appearance', '外观'));
    const themeSelect = el('select');
    for (const [id, name] of [['dark', t('Dark', '深色')], ['light', t('Light', '浅色')]]) { const o = el('option', '', name); o.value = id; themeSelect.append(o); }
    themeSelect.value = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
    themeSelect.onchange = async () => {
        try { (await import('./shell.js')).setTheme(themeSelect.value); } catch { document.documentElement.dataset.theme = themeSelect.value; }
        try { localStorage.setItem('freevideo.theme', themeSelect.value); } catch { /* This page only. */ }
    };
    const themeRow = row({iconName: 'moon', title: t('Theme', '主题'), sub: t('Applies at once to every view', '立即应用到所有视图'),
        end: [segmented(themeSelect, {label: t('Theme', '主题'), tips: {dark: t('Dark panels, best for pictures', '深色界面，适合看画面'), light: t('Light panels', '浅色界面')}})]});
    const solidSwitch = switchify(el('input')); solidSwitch.type = 'checkbox';
    solidSwitch.setAttribute('aria-label', t('Compatibility view', '兼容界面'));
    const solidNow = () => document.documentElement.dataset.glass === 'solid' || document.documentElement.classList.contains('lite');
    solidSwitch.checked = solidNow();
    solidSwitch.onchange = async () => {
        try { (await import('./shell.js')).setGlass(!solidSwitch.checked); }
        catch { if (solidSwitch.checked) document.documentElement.dataset.glass = 'solid'; else delete document.documentElement.dataset.glass; }
    };
    tip(solidSwitch, t('Compatibility view', '兼容界面'), t('For older computers', '适合较旧的电脑'));
    const solidRow = row({iconName: 'layers', title: t('Compatibility view', '兼容界面'), sub: t('Solid panels, fewer animations, no autoplay', '实体背景，减少动画，视频不自动播放'), end: [solidSwitch]});
    // The top bar's status capsule follows the same key (freevideo.monitor.shown): on only when set to "1"; never set
    // reads as off. Writing goes through setMonitorShown, whose event repaints the capsule in the same frame.
    const statusShown = () => { try { return localStorage.getItem('freevideo.monitor.shown') === '1'; } catch { return false; } };
    const statusSwitch = switchify(el('input')); statusSwitch.type = 'checkbox'; statusSwitch.checked = statusShown();
    statusSwitch.setAttribute('aria-label', t('Show this computer in the status', '在运行状态中显示本机数据'));
    statusSwitch.onchange = () => setMonitorShown(statusSwitch.checked);
    tip(statusSwitch, t('Show this computer', '显示本机数据'), t('Graphics memory and temperature in the top bar', '在顶栏显示显存和温度'));
    later(onMonitorShown(() => { statusSwitch.checked = statusShown(); }));
    const statusRow = row({iconName: 'gpu', title: t('Show this computer in the status', '在运行状态中显示本机数据'), sub: t('Graphics memory and temperature in the top bar', '在顶栏显示显存和温度'), end: [statusSwitch]});
    const langSelect = el('select');
    for (const [id, name] of [['zh', '中文'], ['en', 'English']]) { const o = el('option', '', name); o.value = id; langSelect.append(o); }
    langSelect.value = cn ? 'zh' : 'en';
    const langNote = slot(); const langReload = button(t('Reload now', '立即重新载入'), {kind: 'fill', cls: 'is-sm'}); langReload.hidden = true;
    langNote.textContent = t('Changing the language reloads the page; generations keep running.', '切换语言会重新载入页面，正在进行的生成不受影响。');
    langSelect.onchange = () => { langReload.hidden = langSelect.value === (cn ? 'zh' : 'en'); };
    langReload.onclick = () => { const url = new URL(location.href); url.searchParams.set('freevideo_lang', langSelect.value); location.replace(url.href); };
    tip(langReload, t('Reload now', '立即重新载入'), t('Reload the page in the chosen language', '以所选语言重新载入页面'));
    const langRow = row({iconName: 'globe', title: t('Interface language', '界面语言'), end: [segmented(langSelect, {label: t('Language', '语言'), tips: {zh: t('Show the interface in Chinese', '界面显示为中文'), en: t('Show the interface in English', '界面显示为英文')}})]});
    const langFoot = el('div', 'st-foot'); langFoot.append(langNote, langReload);
    appearance.append(groupHead(t('Look', '外观')), card(themeRow.element, solidRow.element),
        groupHead(t('Top bar', '顶栏')), card(statusRow.element), groupHead(t('Language', '语言')), card(langRow.element, langFoot));

    // ════════════ Assistant ════════════
    const assistant = pane('assistant', t('Assistant', '助手'));
    const agentConfirm = agentConfirmSettings(t, api);
    {
        const source = agentConfirm.element, boxes = [...source.querySelectorAll('label.fv-check > input[type=checkbox]')];
        const minutesField = source.querySelector('input.fv-minutes'), agentStatus = source.querySelector('[role=status]');
        const assistantCard = card();
        for (const box of boxes) {
            const words = box.parentElement.querySelector('span')?.textContent || '';
            switchify(box); box.setAttribute('aria-label', words);
            tip(box, words, t('The assistant waits for an answer first', '助手先等确认再继续'));
            assistantCard.append(row({iconName: 'auto', title: words, end: [box]}).element);
        }
        if (minutesField) {
            minutesField.classList.remove('fv-minutes');
            assistantCard.append(row({iconName: 'clock', title: t('A reshoot counts as long from', '耗时较长的标准'), sub: t('Minutes, 0 to 1440', '分钟，0 到 1440'), end: [stepper(minutesField, t('min', '分钟'), {label: t('Minutes', '分钟')})]}).element);
            tip(minutesField, t('Minutes', '分钟'), t('Reshoots this long ask first', '重拍达到这个时长时先询问'));
        }
        const foot = el('div', 'st-foot');
        if (agentStatus) { agentStatus.className = 'st-slot'; agentStatus.setAttribute('aria-live', 'polite'); foot.append(agentStatus); }
        assistantCard.append(foot);
        assistant.append(groupHead(t('Ask before', '先询问')), assistantCard,
            groupNote(t('The assistant makes films in the Autopilot view.', '助手在“AI 全自动”视图中拍片。')));
        navItems.get('assistant').hidden = true;
        agentConfirm.ready?.then(() => { navItems.get('assistant').hidden = source.hidden; if (!source.hidden && sectionOf(section) === 'assistant' && dialog.open) show('assistant', {focus: true}); });
    }

    // ════════════ Storage ════════════
    const storage = pane('storage', t('Storage', '存储'));
    const storageSize = el('span', 'st-sz'); storageSize.setAttribute('role', 'status');
    const storageClear = button(t('Clear up', '清理'), {kind: 'fill'}); storageClear.disabled = true;
    const storageRow = row({iconName: 'disk', title: t('Temporary files from generating', '生成过程中的临时文件'), sub: t('Left by finished videos; your videos are not touched', '已完成视频留下的中间文件，不会删除作品'), end: [storageSize, storageClear]});
    storage.append(groupHead(t('Clear up', '清理')), card(storageRow.element));
    const privacy = privacySettings(t, api); storage.append(privacy.element);

    // ════════════ Page check ════════════
    const health = pane('health', t('Page check', '页面检查'));
    const healthRun = button(t('Check now', '立即检查'), {kind: 'fill'});
    const healthReload = button(t('Reload page', '刷新页面'));
    const healthRow = row({iconName: 'shield', title: t('Page files', '页面文件'), sub: t('Not checked yet', '尚未检查'), end: [healthReload, healthRun]});
    tip(healthRun, t('Check now', '立即检查'), t('Confirm every page file loads', '确认页面的每个文件都能载入'));
    tip(healthReload, t('Reload page', '刷新页面'), t('Load the page again; generations keep running', '重新载入页面，生成不受影响'));
    const healthList = el('div', 'st-sub st-hlist'); healthList.hidden = true;
    health.append(groupHead(t('This page', '本页面')), card(healthRow.element, healthList),
        groupNote(t('If FreeVideo does not open completely, a card with the cause and the next step appears on its own.', '如果 FreeVideo 没有完整打开，会自动出现一张说明原因和下一步的卡片。')));
    healthReload.onclick = () => location.reload();
    healthRun.onclick = async () => {
        healthRun.disabled = true; healthRow.sub.className = ''; healthRow.sub.textContent = t('Checking…', '正在检查…');
        const base = new URL('./', import.meta.url), failed = [];
        await Promise.all(MODULES.map(async name => {
            try { const response = await fetch(new URL(name, base), {cache: 'no-store'}); if (!response.ok) failed.push(`${name} · HTTP ${response.status}`); }
            catch (error) { failed.push(`${name} · ${error.message || error}`); }
        }));
        healthRun.disabled = false;
        if (!failed.length) {
            healthRow.sub.className = 'is-ok';
            healthRow.sub.textContent = t(`All ${MODULES.length} files load`, `全部 ${MODULES.length} 个文件都能载入`);
            healthList.hidden = true; return;
        }
        healthRow.sub.className = 'is-warn';
        healthRow.sub.textContent = t(`${failed.length} files could not be loaded. Reload the page; if it stays, report it.`, `${failed.length} 个文件无法载入。请刷新页面；仍然出现时请提交问题。`);
        const list = el('pre', 'st-pre fv-copyable', failed.join('\n'));
        const report = reportTip(issueButton(t, () => ({where: ['Page check', '页面检查'], message: failed.join('\n')})));
        healthList.replaceChildren(list, report); healthList.hidden = false;
    };

    // ════════════ Advanced ════════════
    const advanced = pane('advanced', t('Advanced', '高级'));
    const compat = compatibilityCard({t, api});
    const compatHead = groupHead(t('Compatibility', '兼容性'));
    advanced.append(compatHead, compat.element);
    // Reserved graphics memory (unified memory on a Mac).
    const reserveHead = groupHead(t('Reserved VRAM', '预留显存'));
    const reserveAuto = switchify(el('input')); reserveAuto.type = 'checkbox'; reserveAuto.checked = true;
    reserveAuto.setAttribute('aria-label', t('Automatic optimization', '自动优化'));
    tip(reserveAuto, t('Automatic optimization', '自动优化'), t('FreeVideo picks the amount for each generation', '每次生成由 FreeVideo 选择数值'));
    const autoRow = row({title: t('Automatic optimization', '自动优化'), sub: t('FreeVideo picks the amount for each generation', '每次生成由 FreeVideo 选择数值'), end: [reserveAuto]});
    const reserve = el('input'); reserve.type = 'number'; reserve.min = '0.2'; reserve.step = '0.1';
    reserve.value = '1.0'; reserve.disabled = true;
    const reserveStep = stepper(reserve, 'GiB', {label: t('Reserved VRAM', '预留显存')});
    tip(reserve, t('Amount to keep free', '预留数值'), t('Type a number or use − and +', '输入数值，或用 − 和 + 调整'), {why: ''});
    const reserveRow = row({title: t('Amount to keep free', '预留数值'), sub: t('Extra VRAM to keep free. Applies from the next generation.', '额外保留的空闲显存，下次生成起生效。'), end: [reserveStep]});
    const reserveHelp = reserveRow.sub;
    const saveReserve = button(t('Save', '保存'), {kind: 'fill'}), reserveStatus = slot('st-saved');
    // A damaged saved file was set aside by the engine; this stays until a new value is saved.
    const reserveDamaged = el('div', 'st-notice st-notice-in'); reserveDamaged.append(icon('warn', 'fv-i'), el('p', '', t('The saved setting was damaged, so Automatic optimization is in use. The original file was renamed and kept.',
        '保存的设置已损坏，现按“自动优化”运行，原文件已改名保留。')));
    reserveDamaged.hidden = true;
    // A failed save is explained in the card's status line, beside the Save button.
    const reserveFailure = reserveStatus;
    const reserveFoot = el('div', 'st-foot'); reserveFoot.append(reserveStatus, saveReserve);
    advanced.append(reserveHead, card(reserveDamaged, autoRow.element, reserveRow.element, reserveFoot));
    let savedTimer;
    const clearReserveStatus = () => { clearTimeout(savedTimer); reserveStatus.replaceChildren(); reserveStatus.className = 'st-slot st-saved'; reserveStep.classList.remove('is-bad'); };
    reserve.oninput = clearReserveStatus;
    reserveAuto.onchange = () => { reserve.disabled = reserveAuto.checked; clearReserveStatus(); };
    // Model loading: on = load early when a generation looks near (eager); off = conservative, nothing is loaded
    // before a generation, while models already used stay loaded for the next one.
    const preloadSwitch = switchify(el('input')); preloadSwitch.type = 'checkbox'; preloadSwitch.checked = preloadMode() === 'eager';
    preloadSwitch.setAttribute('aria-label', t('Preload ahead', '积极预加载'));
    tip(preloadSwitch, t('Preload ahead', '积极预加载'), t('Load the models early when a generation looks near', '判断快要生成时提前加载模型'));
    preloadSwitch.onchange = () => setPreloadMode(preloadSwitch.checked ? 'eager' : 'conservative');
    later(onPreload(mode => { preloadSwitch.checked = mode === 'eager'; }));
    const preloadRow = row({iconName: 'gauge', title: t('Preload ahead', '积极预加载'),
        sub: t('When off, nothing takes VRAM before a generation; models already used stay loaded for the next one.',
            '关闭后不会在生成前主动占用显存；用过的模型仍会保留，改提示词继续生成不受影响。'), end: [preloadSwitch]});
    advanced.append(groupHead(t('Model loading', '模型加载')), card(preloadRow.element));
    // Performance measurement. A measurement, not a setting: it changes nothing about this installation, it reports
    // what this machine measures so the choices that are currently limited to one platform can be settled.
    const calibrateRepeats = el('select');
    for (const n of [1, 2, 3, 4]) { const o = el('option', '', String(n)); o.value = String(n); calibrateRepeats.append(o); }
    calibrateRepeats.value = '1';
    const repeatsSeg = segmented(calibrateRepeats, {label: t('Passes', '轮数'), tips: Object.fromEntries([1, 2, 3, 4].map(n => [String(n), t(`About ${n * 30} minutes`, `约 ${n * 30} 分钟`)]))});
    const repeatsRow = row({iconName: 'clock', title: t('Passes', '轮数'), sub: t('About 30 minutes each; nothing can be generated meanwhile', '每轮约 30 分钟，期间不能生成'), end: [repeatsSeg]});
    const calibrateButton = button(t('Start measuring', '开始测量'), {iconName: 'gauge', kind: 'fill'});
    const calibrateStatus = slot();
    calibrateStatus.textContent = t('Measures this computer and writes a report; no setting changes.', '测量本机性能并生成报告，不改变任何设置。');
    const calibrateFoot = el('div', 'st-foot'); calibrateFoot.append(calibrateStatus, calibrateButton);
    advanced.append(groupHead(t('Performance measurement', '性能测量')), card(repeatsRow.element, calibrateFoot));

    // ════════════ Version & updates ════════════
    const about = pane('about', t('Version & updates', '版本与更新'));
    const news = button(t("What's new", '新功能'), {iconName: 'auto', kind: 'fill'});
    tip(news, t("What's new", '新功能'), t('Play the tour of this version', '播放这一版的新功能介绍'));
    news.onclick = async () => { await close(); import('./whatsnew.js').then(({showWhatsNew}) => showWhatsNew(cn)); };
    about.querySelector('.acts').append(news);
    const notice = createUpdateNotice(cn); notice.element.classList.add('st-update');
    const noticeCard = card(notice.element); noticeCard.classList.add('st-update-card');
    later(notice.dispose);
    const notesBox = el('div', 'st-notes');
    const notesWatch = releaseNotesInto(notesBox, cn, {onNews: available => { news.hidden = !available; }});
    later(notesWatch.dispose);
    about.append(noticeCard, groupHead(t('Release notes', '更新说明')), card(notesBox));

    // ════════════ Getting started ════════════
    const guide = pane('guide', t('Getting started', '新手指引'));
    const guideReplay = createGuideReplayEntry({dialog, cn, api, openWorkspace: async () => {
        const {openCreativeWorkspace} = await import('./view_navigation.js');
        return openCreativeWorkspace();
    }});
    {
        // The guide module builds its rows (icon, name, line, control); they are set into this room's cards.
        const lift = (b, kind) => { b.classList.add('st-btn'); if (kind) b.classList.add('is-' + kind); return b; };
        const toRow = item => {
            const svg = item.querySelector(':scope > svg'), name = item.querySelector('b')?.textContent || '', small = item.querySelector('small')?.textContent || '';
            const control = item.lastElementChild;
            const r = row({title: name, sub: small, end: []}); r.element.classList.add('has-ic');
            const tile = el('span', 'st-ic'); if (svg) { svg.setAttribute('class', 'fv-i s st-i16'); tile.append(svg); } r.element.prepend(tile);
            const box = control?.querySelector?.('input[type=checkbox]');
            if (box) { switchify(box); r.end.append(box); }
            else if (control?.tagName === 'BUTTON') r.end.append(lift(control, r.end.childElementCount ? '' : 'fill'));
            return r.element;
        };
        const groups = [...guideReplay.querySelectorAll('.fv-guide-rows')];
        const menu = guideReplay.querySelector('.fv-guide-howtos'), status = guideReplay.querySelector('[role=status]');
        const heads = [guideReplay.querySelector('h3')?.textContent || t('Getting started', '上手'), guideReplay.querySelector('h4')?.textContent || t('Hints', '提示')];
        groups.forEach((group, i) => {
            const box = card();
            for (const item of [...group.querySelectorAll(':scope > .fv-guide-row')]) {
                const made = toRow(item); box.append(made);
                const opener = made.querySelector('[aria-haspopup=menu]');
                if (menu && opener) {
                    menu.classList.add('st-howtos'); box.append(menu);
                    // Keys reach this room only through dialog_keys while a view is open under it: the list's own keys here.
                    listenDialogKey(menu, 'keydown', event => {
                        const items = [...menu.querySelectorAll('button')], at = items.indexOf(event.target);
                        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); opener.click(); opener.focus(); }
                        else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); items[(at + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus(); }
                    });
                }
            }
            guide.append(groupHead(heads[i] || ''), box);
        });
        if (status) { status.className = 'st-slot st-under'; guide.append(status); }
        if (!groups.length) guide.append(card(guideReplay));
    }

    // ── Right dock content: this computer, what FreeVideo keeps on disk, quick actions ──
    const machineHead = el('div', 'st-rh', t('This computer', '本机'));
    const gpuName = el('div', 'st-gpu'); gpuName.hidden = true;
    const meterRow = label => {
        const box = el('div', 'st-meter'); const line = el('div', 'l'); const value = el('b', '', '—');
        line.append(el('span', '', label), value); const m = bar(); box.append(line, m); return {box, value, m};
    };
    const vram = meterRow(t('VRAM', '显存'));
    const tempBox = el('div', 'st-meter'); const tempLine = el('div', 'l'); const tempValue = el('b', '', '—');
    tempLine.append(el('span', '', t('Temperature', '温度')), tempValue); tempBox.append(tempLine);
    const disk = el('div', 'st-meter'); const diskLine = el('div', 'l'); const diskValue = el('b', '', '—');
    diskLine.append(el('span', '', t('Disk free', '磁盘可用')), diskValue); disk.append(diskLine);
    const usageHead = el('div', 'st-rh', t('FreeVideo keeps', 'FreeVideo 占用'));
    const usageLine = el('div', 'st-meter'); const usageL = el('div', 'l'); const usageValue = el('b', '', '—');
    usageL.append(el('span', '', t('Temporary files', '临时文件')), usageValue); usageLine.append(usageL);
    const rightBody = el('div', 'st-rb'); rightBody.append(gpuName, vram.box, tempBox, disk);
    const usageBody = el('div', 'st-rb'); usageBody.append(usageLine);
    right.append(machineHead, rightBody, usageHead, usageBody);
    let monitorTimer = 0;
    const readMachine = async () => {
        clearTimeout(monitorTimer);
        if (!dialog.open) return;
        try {
            const response = await api.fetchApi('/freevideo/monitor', {signal: AbortSignal.timeout(6000)});
            const s = response.ok ? await response.json() : {};
            gpuName.textContent = s.name || ''; gpuName.hidden = !s.name;
            if (s.memory_total) { vram.value.textContent = `${gib(s.memory_used)} / ${gib(s.memory_total)}`; vram.m.set(s.memory_used / s.memory_total); }
            else { vram.value.textContent = '—'; vram.m.set(0); }
            tempValue.textContent = s.temperature != null ? `${s.temperature} °C` : '—';
            diskValue.textContent = s.disk_free != null ? gib(s.disk_free) : '—';
        } catch { /* The dock keeps the last reading. */ }
        monitorTimer = setTimeout(readMachine, 3000);
    };

    const imageClient = createImageGenClient((path, options) => api.fetchApi(path, options));
    let imageInfo = null, imageTimer = 0, imageBusy = false, swapTimer = 0;
    // ── Assemble ──
    dialog.append(header, left, main, right);

    // ── Page switching (the page rises 4 px and fades in; reduced motion swaps at once) and search ──
    let shown = null;
    const NAMES = new Map(SECTIONS.flatMap(g => g.items.map(i => [i.id, t(...i.name)])));
    function show(id, {focus = false} = {}) {
        id = sectionOf(id) || 'models';
        if (navItems.get(id)?.hidden) id = 'models';
        for (const [key, entry] of navItems) { if (key === id) entry.setAttribute('aria-current', 'page'); else entry.removeAttribute('aria-current'); }
        pickerText.textContent = NAMES.get(id);
        if (shown !== id) {
            const next = panes.get(id), before = shown && panes.get(shown);
            if (before) { before.hidden = true; before.classList.remove('is-in'); }
            next.hidden = false;
            void next.offsetWidth; next.classList.add('is-in');
            main.scrollTop = 0;
            shown = id; dialog.dataset.section = id;
            if (id === 'models') pollImages();
            if (id === 'lora' && !loraLoaded) loadLoras();
        }
        if (focus) panes.get(id).querySelector('h1')?.focus({preventScroll: true});
    }
    const index = new Map([...panes].map(([id, page]) => [id, () => page.textContent.toLowerCase()]));
    search.oninput = () => {
        const words = search.value.trim().toLowerCase();
        let first = null, any = false;
        for (const [id, entry] of navItems) {
            const hit = !words || NAMES.get(id).toLowerCase().includes(words) || index.get(id)().includes(words);
            entry.toggleAttribute('data-miss', !hit);
            if (hit && !entry.hidden) { any = true; first ??= id; }
        }
        searchEmpty.hidden = any;
        if (words && first && first !== shown) show(first);
    };
    listenDialogKey(search, 'keydown', event => {
        if (event.key === 'Enter') { const first = [...navItems].find(([, e]) => !e.hasAttribute('data-miss') && !e.hidden); if (first) show(first[0], {focus: true}); }
        if (event.key === 'Escape' && search.value) { event.preventDefault(); event.stopPropagation(); search.value = ''; search.oninput(); }
    });
    // The phone width's page picker.
    const closePicker = () => { pickerMenu.hidden = true; picker.setAttribute('aria-expanded', 'false'); };
    picker.onclick = () => {
        if (!pickerMenu.hidden) { closePicker(); return; }
        pickerMenu.replaceChildren(...[...navItems].filter(([, e]) => !e.hidden).map(([id]) => {
            const item = el('button', 'st-mi'); item.type = 'button'; item.setAttribute('role', 'menuitemradio');
            item.setAttribute('aria-checked', String(id === shown));
            const def = SECTIONS.flatMap(g => g.items).find(i => i.id === id);
            item.append(icon(def.icon, 'fv-i s'), el('span', '', NAMES.get(id)), icon('check', 'fv-i s ck'));
            item.onclick = () => { closePicker(); show(id); picker.focus(); };
            return item;
        }));
        pickerMenu.hidden = false; picker.setAttribute('aria-expanded', 'true');
        pickerMenu.querySelector('[aria-checked=true]')?.focus();
    };
    listenDialogKey(pickerMenu, 'keydown', event => {
        const items = [...pickerMenu.querySelectorAll('.st-mi')], i = items.indexOf(document.activeElement);
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); items[(i + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus(); }
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closePicker(); picker.focus(); }
    });
    dialog.addEventListener('pointerdown', event => { if (!pickerMenu.hidden && !phoneHead.contains(event.target)) closePicker(); });

    // ── Closing: Esc (unless a menu inside takes it first) or the close buttons; focus goes back to where it was ──
    let closing = null, watching = false;
    const watchAfterClose = () => { if (!watching) { watching = true; watchLateFailures(dialog); } };
    function close() {
        if (closing) return closing;
        closing = new Promise(resolve => {
            if (!dialog.open) { resolve(); return; }
            watchAfterClose();
            if (sheet.open) sheet.close();
            dialog.dataset.closing = 'true';
            const finish = () => { if (dialog.open) dialog.close(); resolve(); };
            const reduce = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
            if (reduce || !dialog.animate) finish();
            else dialog.animate([{opacity: 1}, {opacity: 0}], {duration: 160, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'forwards'}).finished.then(finish, finish);
        });
        return closing;
    }
    closeButton.onclick = phoneClose.onclick = () => close();
    dialog.addEventListener('cancel', event => { event.preventDefault(); if (!sheet.open && pickerMenu.hidden) close(); });
    // Escape closes Settings also while the view underneath routes keys for its own shortcuts (dialog_keys.js);
    // an open menu inside (the page picker, the how-to list, a top-bar menu) takes it first.
    const onKey = event => {
        if (event.key !== 'Escape' || !dialog.open || sheet.open || event.isComposing) return;
        if (!pickerMenu.hidden) return;
        if (dialog.querySelector('.fv-guide-howtos:not([hidden])')) return;
        if (document.querySelector('.fv-tb-pop:popover-open, .fv-lan-panel:not([hidden]):popover-open')) return;
        event.preventDefault(); close();
    };
    listenDialogKey(dialog, 'keydown', onKey);
    // Tab stays in Settings: when Tab or Shift+Tab would take the focus out (to the browser's own bar), it comes back
    // to the first control (or the last) instead.
    const FOCUSABLE = 'button:not([disabled]),[href],input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex]:not([tabindex="-1"])';
    const stops = () => [...dialog.querySelectorAll(FOCUSABLE)].filter(node => node.tabIndex >= 0 && !node.closest('[hidden],[inert]') && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
    let tabbing = 0;
    // Keys reach the room through dialog_keys.js (a window capture routes them), so the handler is registered there.
    listenDialogKey(dialog, 'keydown', event => {
        tabbing = event.key === 'Tab' && !sheet.open && !event.altKey && !event.ctrlKey && !event.metaKey ? (event.shiftKey ? -1 : 1) : 0;
        if (!tabbing) return;
        const list = stops(), at = document.activeElement;
        if (!list.length) return;
        if (tabbing > 0 && at === list[list.length - 1]) { event.preventDefault(); tabbing = 0; list[0].focus(); }
        else if (tabbing < 0 && at === list[0]) { event.preventDefault(); tabbing = 0; list[list.length - 1].focus(); }
    }, true);
    dialog.addEventListener('focusout', event => {
        const direction = tabbing; tabbing = 0;
        if (!direction || !dialog.open || (event.relatedTarget && dialog.contains(event.relatedTarget))) return;
        const list = stops(); const target = direction > 0 ? list[0] : list[list.length - 1];
        if (target) requestAnimationFrame(() => { if (dialog.open && !dialog.contains(document.activeElement)) target.focus(); });
    });
    dialog.addEventListener('fv-setup-section', event => {
        const name = event.detail;
        if (name === 'repair') spotlight();
        else if (name && name !== 'focus') show(name, {focus: true});
        else (dialog.querySelector('.st-ni[aria-current=page]') || closeButton).focus();
    });
    // 'repair': a failure card's "Open settings" lands on Check installation, the first step its hint names.
    // The card's button is hidden while the installer state is still being read (and the page head's one stands in
    // when the models are ready), and it is hidden again while a check starts, so for a few seconds each repaint puts
    // the focus back on whichever one shows; a key or click of the person's own ends that.
    let spotWait = 0;
    function spotlight() {
        show('models');
        clearTimeout(spotWait);
        spotWait = setTimeout(() => { spotWait = 0; }, 6000);
        landSpot();
    }
    function landSpot() {
        const target = headInspect.hidden ? inspect : headInspect;
        if (target.hidden || !dialog.open) return false;
        if (target === document.activeElement) return true;
        target.scrollIntoView({block: 'center'});
        target.focus({preventScroll: true, focusVisible: true});  // a mouse click on Open settings still gets the ring
        target.classList.add('is-spot'); setTimeout(() => target.classList.remove('is-spot'), 2400);
        return true;
    }
    for (const type of ['keydown', 'pointerdown']) dialog.addEventListener(type, () => { clearTimeout(spotWait); spotWait = 0; }, true);

    // ── Hover tips for the installer's actions ──
    for (const [control, name, purpose] of [
        [inspect, t('Check installation', '检查安装'), t('Check this computer and list what would be installed', '检查本机，列出要安装或修复的内容')],
        [use, t('Use existing', '使用已有安装'), t('Use models already on this computer', '直接用本机上已有的模型')],
        [install, t('Install', '安装'), t('Download and fix what the plan lists', '按计划下载并修复缺少的部分')],
        [recheck, t('Check again', '重新检查'), t('Check again after fixing the problems', '处理问题后重新检查')],
        [cancel, t('Stop setup', '停止安装'), t('Pause; downloaded files are kept', '暂停安装，已下载的文件会保留')],
        [reviewPlan, t('Review plan', '查看计划'), t('See what will be installed and agree', '查看要安装的内容并确认')],
        [saveFolders, t('Save folders', '保存文件夹'), t('Look for models in these folders', '按这些文件夹查找模型')],
        [storageClear, t('Clear up', '清理'), t('Delete temporary files of finished videos', '删除已完成视频的临时文件')],
        [calibrateButton, t('Start measuring', '开始测量'), t('Measure this computer; about 30 min per pass', '测量本机性能，每轮约 30 分钟')],
        [applyToken, t('Use token', '应用令牌'), t('Download with your account this session', '本次运行用账号额度下载')],
        [clearToken, t('Clear', '清除本次令牌'), t('Stop using the token entered', '不再使用填写的令牌')],
        [saveReserve, t('Save', '保存'), t('Keep this amount of VRAM free', '按这个数值预留显存')],
        [sheetCancel, t('Not now', '暂不安装'), t('Close the plan without installing', '关闭计划，不安装')],
        [copy, t('Separate copies', '独立副本'), t('Copy reused models instead of linking them', '复用模型时复制一份，而不是链接')],
        [hfToken, t('Access token', '访问令牌'), t('Kept for this session only', '只在本次运行中有效')],
        [root, t('Installation folder', '安装位置'), t('Where FreeVideo keeps its models', 'FreeVideo 存放模型的位置')],
        [extra, t('Other model folders', '其他模型文件夹'), t('Folders with models to reuse', '可复用模型所在的文件夹')],
        [accept, t('Agree', '同意'), t('Required before installing', '安装前需要同意')],
    ]) tip(control, name, purpose);

    // Folds open and close by animating their height (motion.js), keeping the native details behaviour.
    for (const details of dialog.querySelectorAll('details')) later(animateDetails(details));
    // The logs fold appears only once there is a log: at the end of the page, so appearing moves nothing above it.
    models.append(more.element);
    document.body.append(dialog);
    dialog.showModal();
    dialog.append(sheet);
    // Light the Settings entry of this room's top bar once topbar.js has taken the header over; the version entry
    // opens the Version page here.
    const lightEntry = () => {
        const entry = header.querySelector('.fv-settings-entry');
        if (!entry) return false;
        entry.setAttribute('aria-current', 'page');
        entry.onclick = () => (dialog.querySelector('.st-ni[aria-current=page]') || closeButton).focus();
        const version = header.querySelector('.fv-version-info');
        if (version) version.onclick = () => show('about', {focus: true});
        return true;
    };
    if (!lightEntry()) { requestAnimationFrame(() => { if (!lightEntry()) setTimeout(lightEntry, 100); }); }
    show(section || 'models');
    if (sectionOf(section) === 'models' && section === 'repair') spotlight();
    else panes.get(shown).querySelector('h1').focus({preventScroll: true});
    readMachine();
    const pv = productVersion(); versionLine.textContent = pv ? `FreeVideo ${pv}` : 'FreeVideo';

    // ── Storage ──
    // One unit family across the room (GiB / MiB, like the plan and the machine panel).
    const storageAmount = bytes => bytes > 0 ? bytesText(bytes) : '0 MiB';
    const storageSay = (text, again) => {
        storageSize.textContent = text; storageClear.disabled = !again;
        tip(storageClear, t('Clear up', '清理'), t('Delete temporary files of finished videos', '删除已完成视频的临时文件'), {why: again ? '' : t('Nothing to clear up now', '现在没有可清理的文件')});
    };
    storageSay(t('Reading…', '正在读取…'), false);
    const storageCheck = async () => {
        try {
            const response = await api.fetchApi('/freevideo/library/working-files');
            const {bytes} = await response.json();
            if (!response.ok || !Number.isFinite(bytes)) throw new Error();
            usageValue.textContent = storageAmount(bytes);
            if (bytes > 0) storageSay(storageAmount(bytes), true); else storageSay(t('Nothing to clear up', '没有可清理的文件'), false);
        } catch { storageSay(t('The output folder cannot be accessed.', '无法访问输出文件夹'), true); }
    };
    storageClear.onclick = async () => {
        storageClear.disabled = true;
        try {
            const response = await api.fetchApi('/freevideo/library/working-files/clear', {method: 'POST'});
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error();
            const {freed_bytes: freed, left} = result;
            if (left > 0) storageSay(cn ? (freed > 0 ? `已释放 ${storageAmount(freed)}，` : '') + `${left} 项未能删除`
                : (freed > 0 ? `Freed ${storageAmount(freed)}. ` : '') + `${left} ${left === 1 ? 'item' : 'items'} could not be deleted.`, true);
            else if (freed > 0) storageSay(t(`Freed ${storageAmount(freed)}`, `已释放 ${storageAmount(freed)}`), false);
            else storageSay(t('Nothing to clear up', '没有可清理的文件'), false);
            if (!(left > 0)) usageValue.textContent = storageAmount(0);
        } catch { storageSay(t('The output folder cannot be accessed.', '无法访问输出文件夹'), true); }
    };
    storageCheck();

    // ── Picture models ──
    const gb = n => (n / 2 ** 30).toFixed(1);
    function paintImages() {
        let installedBytes = 0;
        for (const [id, item] of imageRows) {
            const s = modelState(imageInfo, id);
            const {row: r, size, chip, act, meter} = item;
            r.element.dataset.state = s.state; r.element.removeAttribute('aria-disabled');
            chip.hidden = act.hidden = meter.hidden = true; r.sub.className = '';
            r.sub.textContent = IMAGE_WORDS[id] || '';
            r.element.querySelector('.st-ic').classList.toggle('is-ok', s.state === 'ready');
            size.textContent = s.total && s.state !== 'ready' ? t(`About ${gb(s.total)} GiB`, `约 ${gb(s.total)} GiB`) : '';
            // Installed is the normal case and says nothing on the row; its size is on hover.
            const kept = s.row?.bytes_total || s.total || 0;
            if (s.state === 'ready' && kept) tip(r.title, item.model.short, t(`Installed · ${gb(kept)} GiB`, `已安装 · ${gb(kept)} GiB`));
            else { delete r.title.dataset.fvTipName; delete r.title.dataset.fvTipUse; }
            if (s.state === 'ready') {
                installedBytes += s.row?.bytes_total || s.total || 0;
            } else if (s.state === 'downloading') {
                meter.hidden = false; meter.set(s.fraction);
                r.sub.className = 'is-accent';
                r.sub.textContent = s.paused ? pausedText(s)
                    : `${Math.floor(s.fraction * 100)}% · ${gb(s.done)} / ${gb(s.total)} GiB`;
                act.hidden = false;
                if (s.paused) {
                    // Waiting for a video or a picture: the user may go on with the download now.
                    act.className = 'st-btn is-fill'; act.replaceChildren(icon('down', 'fv-i s'), el('span', 'st-btn-t', t('Continue download', '继续下载')));
                    act.onclick = () => imageAction(id, 'install');
                    tip(act, t('Continue download', '继续下载'), continueUse());
                } else {
                    act.className = 'st-btn'; act.replaceChildren(icon('stop', 'fv-i s'), el('span', 'st-btn-t', t('Stop', '停止')));
                    act.onclick = () => imageAction(id, 'cancel');
                    tip(act, t('Stop', '停止'), t('Stop the download; the part downloaded is kept', '停止下载，已下载的部分会保留'));
                }
            } else if (s.state === 'failed') {
                r.sub.className = 'is-warn'; r.sub.textContent = t(`${item.model.short} download failed${s.reason ? ': ' + s.reason.replace(/[.。]$/, '') : ''}. Retry to continue; if it keeps failing, report the problem.`, `${item.model.short} 下载失败${s.reason ? '：' + s.reason.replace(/[.。]$/, '') : ''}。请重试以继续下载；仍然失败时，请提交问题。`);
                act.hidden = false; act.className = 'st-btn is-fill'; act.replaceChildren(icon('retry', 'fv-i s'), el('span', 'st-btn-t', t('Retry', '重试')));
                act.onclick = () => imageAction(id, 'install');
                tip(act, t('Retry', '重试'), t('Download again; the part downloaded is kept', '重新下载，已下载的部分会保留'));
            } else if (s.state === 'missing') {
                if (s.resume) { r.sub.className = 'is-caution'; r.sub.textContent = t(`Stopped · ${gb(s.done)} of ${gb(s.total)} GiB kept`, `已停止 · 已下载 ${gb(s.done)} / ${gb(s.total)} GiB`); }
                act.hidden = false; act.className = 'st-btn is-fill'; act.replaceChildren(icon('down', 'fv-i s'), el('span', 'st-btn-t', s.resume ? t('Resume', '继续') : t('Install', '安装')));
                act.onclick = () => imageAction(id, 'install');
                tip(act, s.resume ? t('Resume', '继续') : t('Install', '安装'), t('Download this picture model', '下载这个图像模型'));
            } else if (s.state === 'unavailable') {
                r.sub.textContent = t('Not available on this computer', '这台电脑不支持'); r.element.setAttribute('aria-disabled', 'true');
            } else r.sub.textContent = t('Checking…', '正在检查…');
            act.disabled = imageBusy;
        }
        imageHead.querySelector('.r').textContent = '';
    }
    async function imageAction(id, what) {
        imageBusy = true; paintImages();
        try { imageInfo = what === 'cancel' ? await imageClient.packCancel(id) : await imageClient.packInstall(id); }
        catch { /* The next reading shows the state. */ }
        imageBusy = false; paintImages(); pollImages();
    }
    async function pollImages() {
        clearTimeout(imageTimer);
        if (!dialog.open) return;
        try { imageInfo = await imageClient.info(); } catch { imageInfo ??= {models: []}; }
        paintImages();
        const downloading = IMAGE_MODELS.some(m => modelState(imageInfo, m.id).state === 'downloading');
        if (downloading && shown === 'models') imageTimer = setTimeout(pollImages, 2000);
    }
    paintImages(); pollImages();

    // ── Replace person's model ──
    async function swapCall(options) {
        const response = await api.fetchApi('/freevideo/swap-lora', {cache: 'no-store', ...options});
        if (!response.ok) throw new Error(response.statusText);
        return response.json();
    }
    function paintSwap(value) {
        const install = value?.install || {state: 'idle'}, present = !!value?.present;
        swapChip.hidden = swapAct.hidden = swapMeter.hidden = true; swapRow.sub.className = ''; swapSize.hidden = present && install.state !== 'running';
        if (swapSize.hidden) tip(swapRow.title, t('Replace person model', '替换人物模型'), t('Installed · about 76 MiB', '已安装 · 约 76 MiB'));
        else { delete swapRow.title.dataset.fvTipName; delete swapRow.title.dataset.fvTipUse; }
        swapRow.sub.textContent = t('Downloaded when Replace person is first used', '打开“替换人物”时自动下载');
        swapRow.element.querySelector('.st-ic').classList.toggle('is-ok', present && install.state !== 'running');
        if (install.state === 'running') {
            const total = install.total || 0;
            swapMeter.hidden = false; swapMeter.set(total ? (install.done || 0) / total : null);
            swapRow.sub.className = 'is-accent';
            swapRow.sub.textContent = total ? `${Math.round((install.done || 0) / 2 ** 20)} / ${Math.round(total / 2 ** 20)} MiB` : t('Downloading…', '正在下载…');
            clearTimeout(swapTimer); swapTimer = setTimeout(readSwap, 500);
        } else if (present) {
            // Installed is the normal case: nothing on the row (the size is on hover).
        } else if (install.state === 'failed' || install.state === 'cancelled') {
            swapRow.sub.className = 'is-warn'; swapRow.sub.textContent = swapLoraFailureText(install.reason, t);
            swapAct.hidden = false; swapAct.replaceChildren(icon('retry', 'fv-i s'), el('span', 'st-btn-t', t('Retry', '重试')));
            tip(swapAct, t('Retry', '重试'), t('Download the Replace person model again', '重新下载替换人物模型'));
        } else { swapAct.hidden = false; swapAct.replaceChildren(icon('down', 'fv-i s'), el('span', 'st-btn-t', t('Install', '安装'))); }
    }
    async function readSwap() { if (!dialog.open) return; try { paintSwap(await swapCall()); } catch { swapRow.sub.textContent = t('Not available right now', '暂时无法读取'); } }
    swapAct.onclick = async () => {
        swapAct.disabled = true;
        try { paintSwap(await swapCall({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'install'})})); }
        catch { paintSwap({install: {state: 'failed', reason: 'failed'}}); }
        finally { swapAct.disabled = false; }
    };
    readSwap();

    // ════════════ The installer (its behaviour as before; its parts now live in the Models page and the plan sheet) ════════════
    let savedFolders = "", savedMissing = false;
    let calibrateWait = false;
    let token, planId, busy = false, probeBusy = false, pollTimer, lastPlanId, planErrors = false, planStale = false, ready = false, waitSince;
    let lastTask = {}, loaded = false, readFailed = false, wantSheet = false, busyLabel = '';
    // A click names what is happening at once, before the server has answered.
    const doing = (en, zh) => { waitSince ??= Date.now(); state.className = 'st-state'; state.textContent = t(en, zh); paintHero(); };
    const fail = error => { waitSince = undefined; fault(state, error.message, error); state.className = "st-state is-fail"; if (shown !== 'models' && !busy) { /* The dot on Models says where to look. */ } paintHero(); };
    const request = async (action, value) => {
        const response = await api.fetchApi("/freevideo/setup" + (action ? "/" + action : root.value ? '?root='+encodeURIComponent(root.value) : ""), action ? {
            method: "POST", headers: { "Content-Type": "application/json", "X-FreeVideo-Setup": token }, body: JSON.stringify(value || {}),
        } : {});
        const result = await response.json(); if (!response.ok || result.error) throw new Error(result.error || response.statusText); return result;
    };
    // The card's headline, icon and actions follow the installer's state; its size never changes.
    function paintHero() {
        const failed = state.classList.contains('is-fail');
        const kind = !loaded && !failed ? 'loading' : busy ? 'busy' : readFailed ? 'readerr' : failed ? 'failed' : planErrors ? 'problem' : planId ? 'plan'
            : planStale ? 'stale' : lastTask.status === 'cancelled' ? 'stopped' : ready ? 'ready' : 'none';
        hero.dataset.state = kind;
        heroTitle.textContent = {
            loading: t('Reading installation settings…', '正在读取安装设置…'),
            busy: lastTask.action === 'calibrate' ? t('Measuring performance', '正在测量性能') : lastTask.action === 'plan' || lastTask.action === 'inspect' ? t('Checking the installation', '正在检查安装') : t('Installing', '正在安装'),
            readerr: t('Installation settings could not be read', '无法读取安装设置'),
            failed: t('The last step did not finish', '上一步没有完成'),
            problem: t('Fix these before installing', '安装前需要先处理'),
            plan: t('The installation plan is ready', '安装计划已就绪'),
            stale: t('Settings changed', '设置已更改'),
            stopped: t('Stopped; downloaded files are kept', '已停止，已下载的文件保留'),
            ready: t('Ready to generate', '已就绪，可以开始生成'),
            none: t('Video models are not installed yet', '视频模型还没有安装'),
        }[kind];
        const iconName = {ready: 'check', failed: 'warn', readerr: 'warn', problem: 'warn', stale: 'info', plan: 'down', busy: 'down', stopped: 'stop'}[kind] || 'cube';
        heroIcon.className = 'st-hic' + ({ready: ' is-ok', failed: ' is-warn', readerr: ' is-warn', problem: ' is-warn', stale: ' is-caution', plan: ' is-accent', busy: ' is-accent is-run', stopped: ' is-caution'}[kind] || '');
        heroIcon.replaceChildren(icon(iconName, 'fv-i'));
        // The folder is said once, under Advanced: install location and model folders.
        heroSub.textContent = kind === 'busy' && busyLabel ? busyLabel : t('Video models', '视频模型');
        hero.hidden = kind === 'ready'; headInspect.hidden = kind !== 'ready';
        setText(inspect, kind === 'failed' || kind === 'readerr' ? t('Retry', '重试') : kind === 'stopped' ? t('Continue', '继续安装') : kind === 'none' ? t('Check and install', '检查并安装') : t('Check installation', '检查安装'));
        const primary = kind === 'none' || kind === 'stale' || kind === 'stopped';
        inspect.classList.toggle('is-primary', primary); inspect.classList.toggle('is-fill', !primary);
        use.hidden = !(kind === 'none' || kind === 'failed');
        reviewPlan.hidden = kind !== 'plan' && kind !== 'problem';
        setText(reviewPlan, kind === 'problem' ? t('See problems', '查看问题') : t('Review plan', '查看计划'));
        inspect.hidden = busy || kind === 'loading';
        if (spotWait) landSpot();
        const extraCount = extra.value.split('\n').map(s => s.trim()).filter(Boolean).length;
        state.hidden = kind === 'busy' || (kind === 'ready' && !state.textContent);
        if (kind === 'none' && !state.textContent) state.textContent = t('Check first: models already on this computer are used as they are.', '先检查一遍，电脑上已有的模型会直接使用。');
        folders.sub.textContent = [root.value, extraCount ? t(`${extraCount} more folders`, `另 ${extraCount} 个模型文件夹`) : ''].filter(Boolean).join(' · ');
        folders.sub.hidden = !folders.sub.textContent;
        navItems.get('models').querySelector('.end').replaceChildren(...(kind === 'failed' || kind === 'problem' || kind === 'readerr' ? [el('i', 'st-dot is-warn')] : busy ? [el('i', 'st-dot is-run')] : []));
    }
    let reserveField = 'gpu_reserve_gib';
    const showResources = value => {
        const unified = value && Object.hasOwn(value, 'ram_reserve_gib');
        reserveField = unified ? 'ram_reserve_gib' : 'gpu_reserve_gib';
        reserveHead.firstChild.textContent = unified ? t('Reserved unified memory', '预留统一内存') : t('Reserved VRAM', '预留显存');
        // Mac generation shares unified memory and has no compatibility levels.
        compat.setUnified(unified);
        reserve.setAttribute('aria-label', reserveHead.firstChild.textContent);
        reserve.min = unified ? '1' : '0.2';
        reserveAuto.checked = value?.[reserveField] == null;
        reserve.disabled = reserveAuto.checked;
        reserve.value = reserveAuto.checked ? (unified ? '2.0' : '1.0') : String(value[reserveField]);
        reserveHelp.textContent = unified
            ? t('Extra memory to keep for the system and other apps. Applies from the next generation.', '额外保留给系统和其他程序的内存，下次生成起生效。')
            : t('Extra VRAM to keep free. Applies from the next generation.', '额外保留的空闲显存，下次生成起生效。');
    };
    calibrateButton.onclick = async () => {
        calibrateButton.disabled = true;
        calibrateStatus.className = 'st-slot';
        calibrateStatus.textContent = t('Starting…', '正在启动…');
        try {
            render(await request('calibrate', {repeats: Number(calibrateRepeats.value)}));
            poll();
            calibrateStatus.textContent = t('Running; progress is under Models. Closing Settings keeps it running.', '正在运行，进度见“模型”。关闭设置不会中断。');
        } catch (error) {
            // A refusal that names the running task is a wait, not a fault: shown quietly, with the state polled
            // so the progress it points to is visible.
            calibrateWait = WAIT_REFUSAL.test(String(error.message || ''));
            calibrateStatus.className = calibrateWait ? 'st-slot' : 'st-slot is-warn';
            fault(calibrateStatus, error.message, error, t('Could not start the measurement. Try again.', '没能开始测量，请重试。'));
            if (calibrateWait) poll();
        } finally { calibrateButton.disabled = busy; }
    };
    saveReserve.onclick = async () => {
        clearReserveStatus();
        if (!reserveAuto.checked && !reserve.value) {
            reserveFailure.className = 'st-slot is-warn'; reserveStep.classList.add('is-bad');
            reserveFailure.textContent = t('Enter a value.', '请输入数值。');
            reserve.focus();
            return;
        }
        if (!reserveAuto.checked && reserve.validity.rangeUnderflow) {
            reserveFailure.className = 'st-slot is-warn'; reserveStep.classList.add('is-bad');
            reserveFailure.textContent = atLeast(reserve.min);
            reserve.focus();
            return;
        }
        // Past the top or between steps: the nearest allowed amount goes in the box and the line says so; Save again keeps it.
        const moved = reserveAuto.checked ? null : nearestAllowed(reserve);
        if (moved || (!reserveAuto.checked && !reserve.checkValidity())) {
            if (moved) reserve.value = String(moved.value);
            reserveFailure.className = 'st-slot is-warn'; reserveStep.classList.add('is-bad');
            reserveFailure.textContent = !moved ? t('Enter a number in range', '请输入范围内的数')
                : moved.kind === 'max' ? t(`At most ${moved.value}`, `最多 ${moved.value}`)
                : moved.kind === 'min' ? t(`At least ${moved.value}`, `最少 ${moved.value}`) : t(`Changed to ${moved.value}`, `已改为 ${moved.value}`);
            reserve.focus();
            return;
        }
        saveReserve.disabled = true;
        try {
            showResources(await request('resources', {[reserveField]: reserveAuto.checked ? null : Number(reserve.value)}));
            reserveDamaged.hidden = true;
            reserveStatus.className = 'st-slot st-saved is-ok';
            reserveStatus.replaceChildren(icon('check', 'fv-i'), document.createTextNode(t('Saved · applies to the next generation', '已保存 · 下次生成生效')));
            // The note fades after two seconds.
            savedTimer = setTimeout(() => { reserveStatus.classList.add('is-fade'); savedTimer = setTimeout(clearReserveStatus, 400); }, 2000);
        } catch (error) {
            // As on the compatibility card: a known refusal is already a sentence, a server's own words follow "Save
            // failed", and a lost connection or browser wording becomes a plain sentence; the report keeps what was said.
            reserveFailure.className = 'st-slot is-warn';
            fault(reserveFailure, error.message, error, t('Could not save. Try again.', '没能保存，请重试。'));
            const said = String(error.message || '').trim(), shown = reserveFailure.firstChild;
            if (said && setupError(said) === undefined && shown?.nodeType === Node.TEXT_NODE && shown.data === said) {
                shown.data = t('Save failed: ', '保存失败：') + said;
            }
        }
        finally { saveReserve.disabled = false; }
    };
    const SOURCE_NAMES = {official: t('International source', '国际源'), huggingface: t('International source', '国际源'), 'hf-mirror': t('International mirror', '国际镜像'), modelscope: t('China source', '国内源')};
    function showDownloads(value) {
        if (!value) return;
        proxyMode.value = value.preferences?.proxy_mode || 'auto';
        authStatus.textContent = busy ? t('Stop setup to change the token, then continue; downloaded files are kept.', '先停止安装，再修改令牌并继续；已下载文件会保留。') :
            value.hf_token_set ? t('A token is set for this session. Clearing it falls back to credentials already on this computer.', '已设置本次令牌；清除后仍可使用本机已有的凭据。') :
            t('No token for this session. Credentials already on this computer still apply.', '未填写本次令牌；仍可使用本机已有的凭据。');
        auth.end.replaceChildren(...(value.hf_token_set ? [Object.assign(el('span', 'st-chip is-ok'), {textContent: t('Set', '已设置')})] : []));
        source.value = value.preferences?.source || 'auto';
        probeBusy = value.probe?.status === 'running'; probeButton.disabled = probeBusy;
        setText(probeButton, probeBusy ? t('Testing…', '正在测速…') : t('Test again', '重新测速'));
        const progress = value.probe?.progress;
        sourceStatus.className = 'st-slot st-under';
        sourceStatus.textContent = failureText(value.probe?.error) || (probeBusy ? t('Testing sources… ', '正在测速… ') + `${progress?.done || 0}/${progress?.total || '…'}` : '');
        const measured = value.probe?.status === 'complete' ? value.probe : value.preferences?.probe;
        if (measured?.network_unavailable && !probeBusy) sourceStatus.textContent = t('No source can be reached. Check the network or the connection mode.', '下载源均无法连接，请检查网络或切换连接方式。');
        if (value.probe?.error || measured?.network_unavailable && !probeBusy) { sourceStatus.className = 'st-slot st-under is-warn'; fault(sourceStatus, sourceStatus.textContent); }
        if (!sourceStatus.textContent) sourceStatus.textContent = measured?.sources ? t('Speeds are a guide; actual downloads may differ.', '测速只作参考，实际下载速度可能不同。') : t('Not measured yet. Test again to compare the sources.', '还没有测速，点“重新测速”比较各下载源。');
        speeds.replaceChildren();
        measuredRates = {}; autoBest = '';
        speedDetails.element.hidden = !measured?.sources;
        if (!measured?.sources) { sourceList.fvPaint(); return; }
        const table = el('table', 'st-table');
        const head = el('tr');
        for (const title of [t('Files', '文件'), t('Source · route', '来源 · 连接'), t('Speed', '速度')]) head.append(el('th', '', title));
        table.append(head);
        const sums = {};
        for (const [family, rows] of Object.entries(measured.sources)) for (const r of rows) {
            const tr = el('tr');
            const names = {'edge-models': t('Video model', '视频模型'), 'vdn-models': t('Decoder', '解码器'), 'encoder-models': t('Text understanding', '文本理解'), models: t('Prompt model', '提示词模型'), pypi: t('Runtime packages', '运行环境'), github: t('Tools', '安装工具'), git: t('Tools', '安装工具'), cuda: t('GPU runtime', '显卡运行环境')};
            if (family.startsWith('torch-')) names[family] = t('GPU runtime', '显卡运行环境');
            const rate = r.bytes_per_second / 2 ** 20;
            const speed = !r.ok ? t('Unavailable', '未连通') : r.speed_measured === false || !Number.isFinite(rate) || rate <= 0 ?
                t('Connected; speed not measured yet', '已连通，速度还没测出') : rate < .005 ? '<0.01 MiB/s' : `${rate.toFixed(rate < 1 ? 2 : 1)} MiB/s`;
            for (const text of [names[family] || family, `${SOURCE_NAMES[r.id] || r.id} · ${r.route === 'direct' ? t('Direct', '直连') : t('Current connection', '当前连接')}`, speed]) tr.append(el('td', '', text));
            if (!r.ok) tr.lastChild.className = 'off';
            table.append(tr);
            const key = r.id === 'huggingface' ? 'official' : r.id;
            sums[key] ||= {sum: 0, n: 0, ok: false};
            if (r.ok && Number.isFinite(rate) && rate > 0) { sums[key].sum += rate; sums[key].n++; }
            sums[key].ok ||= Boolean(r.ok);
        }
        for (const [key, s] of Object.entries(sums)) { if (s.n) measuredRates[key] = s.sum / s.n; else if (!s.ok) measuredRates[key] = -1; }
        const best = Object.entries(measuredRates).filter(([, r]) => r > 0).sort((a, b) => b[1] - a[1])[0];
        if (best) { measuredRates.auto = best[1]; autoBest = SOURCES.find(x => x[0] === best[0])?.[1] || ''; }
        speeds.append(table);
        sourceList.fvPaint();
    }
    source.onchange = async () => {
        source.disabled = true;
        try { showDownloads(await request('network-source', {root: root.value, source: source.value})); sourceStatus.className = 'st-slot st-under'; sourceStatus.textContent = t('Switching sources; downloaded parts are kept where possible.', '正在切换下载源，能续传的部分会保留。'); }
        catch (error) { fail(error); }
        finally { source.disabled = false; }
    };
    proxyMode.onchange = async () => {
        proxyMode.disabled = true;
        try { showDownloads(await request('network-source', {root: root.value, proxy_mode: proxyMode.value})); }
        catch (error) { fail(error); }
        finally { proxyMode.disabled = false; }
    };
    probeButton.onclick = async () => {
        probeButton.disabled = true;
        try { showDownloads(await request('network-probe', {root: root.value})); poll(); }
        catch (error) { probeButton.disabled = false; fail(error); }
    };
    const controls = () => {
        for (const e of [root, extra, copy, samplingCaches, saveFolders, inspect, use, hfToken, applyToken, clearToken, calibrateButton]) e.disabled = busy;
        cancel.hidden = !busy; install.disabled = busy || !planId || !accept.checked; accept.disabled = busy;
        const why = busy ? "" : planStale ? t("Settings changed. Click Check installation again.", "设置已更改，请重新点“检查安装”。")
            : planErrors ? t("Fix the problems listed above, then check again.", "请先处理上面列出的问题，再重新检查。")
            : ready && !planId ? t(INSTALLED_HINT.en, INSTALLED_HINT.zh)
            : !planId ? ""
            : !accept.checked ? t("Tick the agreement to enable Install.", "勾选同意后，可点击“安装”。") : "";
        blocked.textContent = why;
        tip(install, t('Install', '安装'), t('Download and fix what the plan lists', '按计划下载并修复缺少的部分'), {why: install.disabled ? why || t('Wait for the current task to finish', '请等待当前任务完成') : ''});
        recheck.hidden = !planErrors; install.hidden = planErrors; acceptLabel.hidden = busy || !planId;
        tip(calibrateButton, t('Start measuring', '开始测量'), t('Measure this computer; about 30 min per pass', '测量本机性能，每轮约 30 分钟'), {why: busy ? t('Wait for the current setup task to finish', '请等待当前安装任务完成') : ''});
        paintHero();
    };
    // A changed setting makes a shown plan outdated: clear it rather than leave it beside a disabled button.
    const invalidate = (stale = false) => {
        if (stale && lastPlanId && !busy) { planStale = true; planErrors = false; plan.replaceChildren(); if (!state.classList.contains("is-fail")) state.textContent = ""; }
        planId = undefined; accept.checked = false; acceptLabel.hidden = true; controls();
    };
    async function setToken(value) {
        applyToken.disabled = clearToken.disabled = true;
        try {
            await request('network-token', {hf_token: value}); hfToken.value = '';
            invalidate(true); await poll();
        } catch (error) { fail(error); }
        finally { controls(); }
    }
    applyToken.onclick = () => setToken(hfToken.value);
    clearToken.onclick = () => setToken('');
    for (const e of [root, extra, copy, samplingCaches]) e.oninput = () => invalidate(true);
    extra.oninput = () => { folderStatus.textContent = ""; folderStatus.className = "st-slot"; savedFolders = ""; savedMissing = false; invalidate(true); };
    accept.onchange = controls;
    recheck.onclick = () => { sheet.close(); inspect.click(); };
    function showPlan(value) {
        plan.replaceChildren();
        const gpu = value.inventory?.selected_gpu || {}, local = value.local_models || {};
        const disksNeeded = value.disks?.reduce((sum, d) => sum + d.needed_bytes, 0);
        const facts = [
            [t("To download", "需要下载"), bytesText(value.model_download_bytes)],
            [t("Already here", "已有模型"), bytesText(value.existing_model_bytes_size_matched || 0)],
            [t("Reused from this computer", "复用本地模型"), bytesText(local.reused_bytes || 0), t(`Linked ${bytesText(local.linked_bytes || 0)} · copied ${bytesText(local.copy_bytes || 0)}`, `链接 ${bytesText(local.linked_bytes || 0)} · 复制 ${bytesText(local.copy_bytes || 0)}`)],
            [t("Extra disk space", "额外占用磁盘"), bytesText(disksNeeded)],
            [t("Graphics card", "显卡"), gpu.name || "—"],
        ];
        if (value.prepared_model) facts.push([t("Model version", "模型版本"), value.prepared_model.scale_granularity === "int8_convrot" ? t("Slim int8", "精简 int8 版") : t("Slim FP8", "精简 FP8 版"),
            t("No conversion on this computer", "无需本地转换")]);
        const list = el('dl', 'st-facts');
        for (const [key, shownValue, small] of facts) {
            const box = el('div'); const dd = el('dd', '', shownValue); cutTip(dd, shownValue);
            if (small) dd.append(el('small', '', small));
            box.append(el('dt', '', key), dd); list.append(box);
        }
        plan.append(list);
        // The package name stays in Details and logs (and the report), not on the plan sheet.
        for (const line of value.replace_models_text?.[cn ? 'zh' : 'en'] || []) {
            const replacement = el('div', 'st-notice'); replacement.append(icon('info', 'fv-i'));
            const words = el('div'); words.append(el('p', '', line.text), el('p', 'st-path fv-copyable', line.path));
            replacement.append(words); plan.append(replacement);
        }
        if (value.prepared_model?.private) {
            const note = el('div', 'st-notice is-info'); note.append(icon('key', 'fv-i'), el('p', '', t("This model needs an account with access. Add an access token under Downloads.", "这个模型需要有访问权限的账号，请在“下载”里填写访问令牌。")));
            plan.append(note);
        }
        const licenses = el('details', 'st-tfold'); const ls = el('summary'); ls.append(icon('chev', 'fv-i'), el('span', '', t(`Model and toolkit licenses · ${value.licenses?.length || 0}`, `模型与工具包许可证 · ${value.licenses?.length || 0}`)));
        licenses.append(ls, el("pre", 'st-pre fv-copyable', value.licenses?.join("\n") || "")); plan.append(licenses);
        if (value.errors?.length) {
            const e = el('div', 'st-notice is-warn is-fail'); e.append(icon('warn', 'fv-i'));
            const items = el('ul'); for (const line of value.errors.map(failureText)) items.append(el('li', '', line));
            e.append(items); plan.prepend(e);
        }
        const problem = Boolean(value.errors?.length);
        sheetTitle.textContent = problem ? t('Fix these before installing', '安装前需要先处理') : t('Confirm installation', '确认安装');
        sheetIcon.className = problem ? 'st-hic is-warn' : 'st-hic is-accent';
        sheetIcon.replaceChildren(icon(problem ? 'warn' : 'down', 'fv-i'));
        sheetLine.textContent = problem ? t('Installation cannot start until these are fixed.', '处理完这些问题后才能开始安装。')
            : t('These files are downloaded or reused. Closing Settings does not stop the installation.', '将下载或复用以下文件。关闭设置不会中断安装。');
        setText(install, value.model_download_bytes > 0 ? t(`Install · ${bytesText(value.model_download_bytes)}`, `安装 · ${bytesText(value.model_download_bytes)}`) : t('Install', '安装'));
    }
    function render(task) {
        lastTask = task || {}; loaded = true; readFailed = false;
        busy = Boolean(task.busy); if (!busy) waitSince = undefined; else waitSince ??= Date.now();
        // The wait sentence ends with the task it named.
        if (!busy && calibrateWait) { calibrateWait = false; calibrateStatus.className = 'st-slot'; calibrateStatus.textContent = ''; }
        ready = Boolean(task.ready) || ready && task.status !== 'failed';
        setText(cancel, task.action === 'calibrate' ? t('Stop', '停止') : t('Stop setup', '停止安装'));
        inputs.hidden = busy;
        if (task.plan_id && task.plan_id !== lastPlanId) {
            lastPlanId = task.plan_id; planErrors = Boolean(task.plan?.errors?.length); planStale = false;
            planId = planErrors ? undefined : task.plan_id;
            accept.checked = false; showPlan(task.plan);
            wantSheet = dialog.open;
        }
        if (busy && sheet.open) sheet.close();
        const p = task.progress || {};
        state.className = 'st-state' + (task.status === "failed" ? " is-fail" : task.ready && !savedMissing ? " fv-ready" : "");
        state.textContent = failureText(task.error) || (task.ready ? t("Ready to generate.", "准备就绪，可以开始生成。") : busy ? p.label || t("Checking configuration and local models…", "正在检查配置和本地模型…") : task.status === "complete" && task.action === "plan" ? (planId ? t("Review the plan, then install.", "请确认计划后安装。") : "") : task.status === "cancelled" ? t("Stopped. Files retained for resuming.", "已停止，文件保留，可继续安装。") : "");
        if (task.error) fault(state, task.error);
        const phases = task.phase_progress || {}, valid = Number.isFinite(p.total) && p.total > 0 && Number.isFinite(p.done) && p.done >= 0 && p.done <= p.total;
        overall.hidden = !busy;
        overallBar.max = phases.total > 0 ? phases.total : 1; overallBar.value = phases.done || 0;
        overallText.replaceChildren(el('b', '', phases.total > 0 ? `${t("Overall", "整体进度")} · ${Math.floor(100 * phases.done / phases.total)}%` : t("Preparing installation…", "正在准备安装…")),
            el('span', '', phases.total > 0 ? `${phases.done} / ${phases.total} ${t("stages complete", "阶段已完成")}` : ''));
        fileBar.hidden = !busy; fileBar.max = valid ? p.total : 1; fileBar.value = valid ? p.done : 0;
        if (busy && !valid) fileBar.set(null);
        const amount = n => p.total >= 2 ** 30 ? gib(n) : `${(n / 2 ** 20).toFixed(1)} MiB`;
        const count = valid ? `${Math.floor(100 * p.done / p.total)}% · ${p.unit === "bytes" ? `${amount(p.done)} / ${amount(p.total)}` : `${p.done} / ${p.total}`}` : t("Preparing…", "正在准备…");
        const eta = valid && Number.isFinite(p.remaining_seconds) && p.remaining_seconds > 0 ? `${p.unit === "bytes" ? t("File time left", "本文件预计剩余") : t("Time left", "预计剩余")} ~${Math.ceil(p.remaining_seconds)} s` : valid ? t("Estimating remaining time…", "正在估算剩余时间…") : `${t("Elapsed", "已用时")} ${Math.floor((Date.now() - (waitSince || Date.now())) / 1000)} s`;
        counters.textContent = busy ? `${count} · ${eta}` : "";
        counters.hidden = !busy;
        const others = (task.active_tasks || []).slice(1).map(p => p.label).filter(Boolean);
        const progressDetail = (p.detail || '').replace(/^Keeping the original file: /, t('Keeping the original file: ', '正在保留原文件：'));
        detail.textContent = [progressDetail, p.resource, others.length ? `${t("Also running", "同时进行")}: ${others.join(" / ")}` : "", busy ? t('Closing Settings does not stop it.', '关闭设置不会中断。') : ''].filter(Boolean).join(" · ");
        detail.hidden = !busy;
        const repo = task.plan?.prepared_model?.repo;
        log.textContent = [repo ? `${t('Model package', '模型包')}: ${repo}` : '', task.log, task.tail].filter(Boolean).join("\n");
        more.element.hidden = !log.textContent;
        busyLabel = busy ? (p.label || '') : '';
        controls();
        if (wantSheet) { wantSheet = false; show('models'); openSheet(); }
    }
    async function poll() {
        clearTimeout(pollTimer);
        try { const info = await request(); token = info.discovery.token; render(info.task); showDownloads(info.discovery.downloads); }
        catch (error) { fail(error); }
        if (dialog.open && (busy || probeBusy)) pollTimer = setTimeout(poll, 750);
    }
    async function saveModelFolders() {
        const result = await request('model-directories', {extra_libraries: extra.value.split("\n")});
        extra.value = result.extra_libraries.join("\n");
        const missing = result.missing_libraries || [];
        savedMissing = missing.length > 0;
        savedFolders = !missing.length ? t('Saved.', '已保存。')
            : missing.length === 1 ? t('Saved. This folder was not found:', '已保存。找不到这个文件夹：')
            : t(`Saved. ${missing.length} folders were not found:`, `已保存。有 ${missing.length} 个文件夹找不到：`);
        folderStatus.className = savedMissing ? 'st-slot is-caution' : 'st-slot is-ok';
        folderStatus.replaceChildren(savedFolders);
        if (savedMissing) {
            // The full path is in the box above; show its start and last folder on one line. The start gives way to the room
            // there is (cut with an ellipsis), the last 26 characters stay; copying takes the whole path.
            const first = missing[0], cut = Math.max(0, first.length - 26), path = el('span', 'st-path st-path-cut fv-copyable');
            const head = el('span', 'st-path-head', first.slice(0, cut));
            path.append(head, el('span', 'st-path-tail', first.slice(cut)));
            // The cut is in the start (the line itself never overflows), so that is what decides the whole-path tip.
            cutTip(path, first, head); folderStatus.append(path);
        }
        return result;
    }
    saveFolders.onclick = async () => {
        invalidate(true); busy = true; controls();
        const before = [[...state.childNodes], state.className]; doing('Saving model folders…', '正在保存模型文件夹…');
        try { await saveModelFolders(); }
        catch (error) { savedFolders = ''; savedMissing = false; fault(folderStatus, error.message, error, t('Could not save the model folders. Try again.', '没能保存模型文件夹，请重试。')); folderStatus.className = 'st-slot is-warn'; }
        finally { busy = false; waitSince = undefined; state.replaceChildren(...before[0]); state.className = before[1]; controls(); }
    };
    headInspect.onclick = () => { if (!busy) inspect.click(); };
    inspect.onclick = async () => {
        invalidate(); busy = true; lastTask = {action: 'inspect'}; readFailed = false; controls(); doing('Checking installation…', '正在检查安装…');
        try { await saveModelFolders(); render(await request("inspect", {root: root.value, extra_libraries: extra.value.split("\n").map(s => s.trim()).filter(Boolean), copy: copy.checked, sampling_caches: samplingCaches.checked})); poll(); }
        catch (error) { busy = false; waitSince = undefined; controls(); fail(error); }
    };
    install.onclick = async () => {
        doing('Starting installation…', '正在开始安装…');
        try { render(await request("install", {plan_id: planId, accept_licenses: accept.checked})); invalidate(); poll(); }
        catch (error) { fail(error); }
    };
    use.onclick = async () => {
        try { const result = await request("use", {root: root.value}); ready = Boolean(result.ready); if (result.ready) { state.textContent = t("Existing installation connected. Ready to generate.", "已连接现有安装，可以生成。"); state.className = "st-state fv-ready"; lastTask = {}; }
              else { fault(state, result.detail); state.className = "st-state is-fail"; }
              invalidate(); }
        catch (error) { fail(error); }
    };
    cancel.onclick = async () => { try { render(await request("cancel")); poll(); } catch (error) { fail(error); } };
    dialog.addEventListener('close', () => {
        clearTimeout(pollTimer); clearTimeout(monitorTimer); clearTimeout(imageTimer); clearTimeout(swapTimer);
        for (const dispose of disposers) { try { dispose(); } catch { /* Gone already. */ } }
        compat.dispose?.();
        watchAfterClose();
        dialog.remove(); if (current === dialog) current = undefined;
        if (returnFocus?.isConnected) { try { returnFocus.focus({preventScroll: true}); } catch { /* Focus stays where the browser put it. */ } }
    });
    doing('Reading installation settings…', '正在读取安装设置…');
    try {
        const info = await request(); token = info.discovery.token; root.value = info.discovery.root || '';
        extra.value = (info.discovery.extra_libraries || []).join("\n");
        showDownloads(info.discovery.downloads);
        showResources(info.discovery.resources);
        if (info.discovery.resources_damaged) reserveDamaged.hidden = false;
        samplingSize.textContent = info.discovery.sampling_cache_bytes ? t(`About ${gib(info.discovery.sampling_cache_bytes)}`, `约 ${gib(info.discovery.sampling_cache_bytes)}`) : '';
        // New installations prepare every quality level; a ready one adds them only when chosen.
        samplingCaches.checked = !info.discovery.ready;
        const libs = info.discovery.libraries || [];
        paths.textContent = libs.join("\n") || t("No model folders found; add one above.", "没有发现模型文件夹，可在上方添加。");
        libSummary.lastChild.textContent = t(`Folders found automatically · ${libs.length}`, `自动发现的文件夹 · ${libs.length}`);
        paths.dataset.count = String(libs.length);
        ready = Boolean(info.discovery.ready);
        render(info.task || {});
        // A failure the last task left stays readable; only a quiet ready installation says it is ready.
        if (info.discovery.ready && !busy && info.task?.status !== 'failed') state.textContent = t("Ready to generate.", "已就绪，可以开始生成。");
        if (busy || probeBusy) poll();
        paintHero();
    } catch (error) { loaded = true; readFailed = true; fail(error); }
    return dialog;
}
