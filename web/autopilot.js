import {createLanAccess} from './lan_access.js';
// The Agent's own page, beside the studio and the editing view: the card that connects one, then the conversation with it.
import { api } from '../../scripts/api.js';
import { agentConnectCard, agentLinkMenu, connectCardBack, connectCardReason, connectionLabel } from './agent_connect.js';
import { about, agentChat, jobName, readChatCache } from './autopilot_chat.js';
import { createRequestAssets } from './request_assets.js';
import { openEditorView } from './editor_loader.js';
import { closeDialog } from './motion.js';
import { createVersionInfo, productVersion } from './updates.js';
import { openSetup } from './setup.js';
import { feedbackButton, guideButton } from './feedback.js';
import { openExtendEditor, openStudio, showNodes } from './studio.js';
import { agentTabReady, openImage, viewChanged, viewSwitch } from './view_navigation.js';
import { wordmark } from './wordmark.js';
import { keepViewSwitchPlace } from './view_switch_place.js';
import { tip } from './autopilot_tips.js';
import { agentPost } from './agent_session.js';
import { isChinese, translator } from './i18n.js';

const cn = isChinese();
const t = translator(cn);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

let current = null, kept = null;
// Opens above the studio when one is open, as the editing view does, so going back finds it as it was.
// The view is kept after it closes: coming back shows it exactly as it was (no rebuild, no blank first frame) and
// brings it up to date in the background. A first visit starts from the last reading kept in this browser.
export function openAutopilotView(node, {studio = null, initialPrompt = '', initialScene = ''} = {}) {
    if (current?.open) { viewChanged('autopilot', node); return current; }
    studio?.querySelectorAll('video,audio').forEach(media => media.pause());
    if (kept && kept.node === node && !initialScene) return kept.reopen(studio, initialPrompt);
    kept?.dispose(); kept = null;
    const dialog = el('dialog', 'fv-studio fv-autopilot'); dialog.lang = cn ? 'zh' : 'en'; dialog.setAttribute('aria-label', t('Autopilot', 'AI 全自动'));
    // The shell's switch waits for the first screen: not ready until the card or the conversation is in place.
    dialog.dataset.fvReady = 'false';
    const header = el('header', 'fv-header'), brand = el('div', 'fv-brand'), tools = el('div', 'fv-header-actions');
    brand.append(wordmark());
    const settings = el('button', 'fv-quiet', t('Settings', '设置')); settings.type = 'button'; settings.onclick = openSetup;
    // Whether an assistant is connected (at the foot of the chats), and what it is making now (at the head of the
    // tasks). Both live inside the view, so the top bar is the same in every view.
    const lamp = el('button', 'fv-autopilot-lamp'), lampText = el('span'); lamp.type = 'button'; lamp.append(el('i'), lampText);
    const now = el('div', 'fv-autopilot-now'), go = el('button', 'fv-autopilot-now-go'), halt = el('button', 'fv-autopilot-now-stop'); now.hidden = true;
    const ring = el('i'), name = el('span', 'fv-autopilot-now-name'), more = el('span'), percent = el('b'), eta = el('em');
    go.type = halt.type = 'button'; go.append(ring, name, more, percent, eta); now.append(go, halt);
    halt.title = t('Stop all', '全部停止'); halt.setAttribute('aria-label', halt.title);
    tip(go, 'goRunning', t); tip(halt, 'stopAll', t, {title: false});
    const line = el('i', 'fv-autopilot-line');
    const phoneAccess = createLanAccess(t, api);
    const versionInfo = createVersionInfo(cn);
    tools.append(phoneAccess.element, versionInfo.element, guideButton(t), feedbackButton(t), settings);
    // The view chosen next takes over once this page has closed; Escape goes back to what is underneath.
    let next = () => studio?.open ? viewChanged('studio', node) : viewChanged('nodes', node);
    const leave = action => { next = action; closeDialog(dialog); };
    const navigation = viewSwitch('autopilot', () => leave(() => studio?.open ? viewChanged('studio', node) : openStudio(node)),
        () => leave(() => showNodes(node, studio)), () => leave(() => openEditorView(node, {studio: studio?.open ? studio : undefined})), () => {}, () => leave(() => openImage(node, {studio: studio?.open ? studio : undefined})));
    header.append(brand, navigation, tools);
    const place = keepViewSwitchPlace(header, navigation);
    const inSight = () => dialog.open && dialog.dataset.closing !== 'true';
    const body = el('div', 'fv-autopilot-body');
    // The progress of what is being made, along the top edge of the view.
    body.append(line);
    const card = agentConnectCard(t, api, () => inSight() && !card.element.hidden, value => {
        on = value; show();
        if (value !== 'unknown') agentTabReady(value);
        if (value === 'on') chat.sync();
    });
    readChatCache();
    // Leaving for another view waits until this page has closed, so the view underneath is never covered twice.
    const closed = () => dialog.open ? new Promise(done => { dialog.addEventListener('close', done, {once: true}); closeDialog(dialog); }) : null;
    // The next video at the studio's current settings; the agent may choose others, so this is only what is missing now.
    const assets = createRequestAssets({t, api, request: () => {
        const value = Object.fromEntries((node.widgets || []).filter(widget => widget.name).map(widget => [widget.name, widget.value]));
        if (!(value.width > 0 && value.height > 0 && value.seconds > 0)) return null;
        return new URLSearchParams({width: value.width, height: value.height, seconds: value.seconds, task: 't2va',
            base_steps: value.base_steps ?? 8, refine_steps: value.refine_steps ?? 3, two_pass: value.two_pass === false ? '0' : '1',
            enhance: node.properties?.freevideo_prompt_versions?.enabled ? '1' : '0'});
    }});
    // The showcase's example opts into preview, but remains an editable, unsent draft.
    // A deleted chat can be brought back for 8 s (the receipt above the box offers Undo).
    const chat = agentChat(t, api, {shown: inSight, productVersion, hint: assets, initialPrompt, initialWhiteboxPreview: !!initialPrompt, initialScene, undoMs: 8000,
        // Creations continues the work on the editor timeline; this page gives way first.
        open: async job => (await import('./library.js')).openLibrary(t, {selectedWork: job.work, back: t('Back to chat', '返回对话'),
            extend: async (record, options) => { await closed(); return openExtendEditor(record, node, {studio, ...options}); }}),
        edit: job => leave(() => openEditorView(node, {studio: studio?.open ? studio : undefined, work: {id: job.work, video: `FreeVideo/${job.work}/video.mp4`, geometry: {width: job.width, height: job.height, seconds: job.seconds}}})),
        changed: view => { last = view; show(); }});
    // With no agent the card stands alone in the middle. Once one has connected the conversation stays, also after
    // it disconnects; the light then leads back to the card.
    // The empty card shows only once the first reading confirms it; until then the view is not ready (the shell holds).
    let last = {known: false, agent: {}, current: null, active: 0, any: false};
    let on = null, wantCard = false, currentId = null, readyTimer = null, wasConnected = false;
    function show() {
        const connected = last.agent.connected === true, label = connectionLabel(last.agent, t);
        // An assistant that has just connected closes the card it was opened from; once connected, the connection row
        // can open the card again (how to connect, or connect another), with the way back to the chats.
        if (connected && !wasConnected) wantCard = false;
        wasConnected = connected;
        linkMenu.update(last.agent);
        const ready = on === 'on' && last.known, talking = ready && (connected || last.any) && !wantCard;
        // The card gives way to the conversation: typing can start at once.
        const arrived = talking && chat.element.hidden && (card.element.contains(document.activeElement) || document.activeElement === lamp);
        // A check that failed keeps the card up: its retry is the way back.
        card.element.hidden = !(ready || on === 'unknown') || talking; chat.element.hidden = !talking;
        body.dataset.view = talking ? 'chat' : 'connect';
        if (talking) chat.sync();
        if (arrived) chat.input.focus();
        lamp.hidden = !ready; lamp.dataset.color = label.color; lampText.textContent = lamp.title = label.text; lamp.setAttribute('aria-label', label.text); lamp.disabled = !connected && !last.any;
        tip(lamp, connected ? 'connected' : 'disconnected', t, {title: false, why: lamp.disabled ? t('Connect an assistant first; the steps are in the middle', '先连接助手，步骤在中间的卡片里') : ''});
        if (lamp.disabled || !ready) linkMenu.close();
        const job = talking ? last.current : null;
        phoneAccess.observe(job);
        now.hidden = !job; line.dataset.on = String(!!job);
        halt.disabled = !!last.stopping;
        halt.setAttribute('aria-busy', String(!!last.stopping));
        halt.textContent = last.stopping ? t('Stopping…', '正在停止…') : '';
        halt.title = last.stopping ? t('Stopping…', '正在停止…') : t('Stop all', '全部停止'); halt.setAttribute('aria-label', halt.title);
        if (job) {
            const fraction = last.fraction || 0, running = job.status === 'running';
            currentId = job.id; name.textContent = jobName(job, t); more.textContent = last.active > 1 ? t(`${last.active - 1} more`, `另有 ${last.active - 1} 项`) : '';
            percent.textContent = running && fraction >= .01 ? `${Math.floor(fraction * 100)}%` : '';
            eta.textContent = running && last.left != null ? about(last.left, t) : '';
            ring.style.setProperty('--p', String(fraction * 100)); line.style.width = `${fraction * 100}%`;
        }
        // Opened from a chat whose assistant has gone: the card offers the way back to the chats.
        connectCardBack(card, t, (connected || last.any) && wantCard ? () => { wantCard = false; show(); chat.input.focus(); } : null);
        connectCardReason(card, t, last.known ? last.agent : {connected: true});
        card.sync();
        // The main block is whichever is in front: the connect card or the conversation's column. The first screen is
        // final once the interface has answered and, when it is on, the conversation has been read once.
        const front = !card.element.hidden ? card.element : !chat.element.hidden ? chat.column : null;
        for (const block of [card.element, chat.column]) if (block !== front) delete block.dataset.fvMain;
        if (front) front.dataset.fvMain = '';
        // Ready once the main content is drawn (the shell waits for this before fading the view in).
        if (front && on !== null && (on !== 'on' || last.known)) markReady();
    }
    function markReady() { clearTimeout(readyTimer); if (dialog.dataset.fvReady !== 'true') dialog.dataset.fvReady = 'true'; }
    // The light opens its menu (disconnect, reconnect, why it is off); with the card opened from it, it closes the card.
    const linkMenu = agentLinkMenu(t, body, lamp, {disconnect: () => agentPost(api, '/freevideo/agent/connect/disconnect', {}),
        steps: () => { wantCard = true; show(); card.focus(); },
        changed: agent => { if (agent) { last = {...last, agent}; show(); } chat.refresh(); }});
    lamp.onclick = () => { if (wantCard) { wantCard = false; show(); } else linkMenu.toggle(last.agent); };
    go.onclick = () => chat.scrollToJob(currentId);
    halt.onclick = () => chat.stopAll();
    body.append(card.element, chat.element);
    // The connection sits at the foot of the chats; on a narrow window the chats are a drawer, so it moves to the
    // middle column's top row and stays in sight.
    chat.slots.running.append(now);
    const narrow = globalThis.matchMedia?.('(max-width: 899px)');
    const placeLamp = () => narrow?.matches ? chat.slots.head.insertBefore(lamp, chat.slots.head.lastElementChild) : chat.slots.connection.append(lamp);
    placeLamp(); narrow?.addEventListener?.('change', placeLamp);
    card.state.then(value => { on = value; show(); if (value === 'on') chat.sync(); });
    show();
    dialog.append(header, body);
    dialog.addEventListener('cancel', event => { event.preventDefault(); closeDialog(dialog); });
    const goBack = next;
    // Closing hides the view and keeps it: reading stops by itself while it is out of sight, playback pauses.
    function hidden() {
        clearTimeout(readyTimer);
        // A kept view is closed (its sheet keeps a closed dialog out of sight) and inert, whatever view comes next.
        dialog.inert = true;
        if (current === dialog) current = null;
        dialog.querySelectorAll('video,audio').forEach(media => media.pause());
        linkMenu.close(); card.stop();
        const then = next; next = goBack; then();
    }
    // Any other view in front (the node view has no dialog of its own) keeps a closed Autopilot out of sight and out of reach.
    const otherView = event => { if (event.detail?.view !== 'autopilot' && !dialog.open) dialog.inert = true; };
    globalThis.addEventListener?.('freevideo-view', otherView);
    function dispose() {
        globalThis.removeEventListener?.('freevideo-view', otherView);
        narrow?.removeEventListener?.('change', placeLamp); place.dispose(); phoneAccess.dispose(); versionInfo.dispose(); card.stop(); chat.stop(); assets.dispose();
        if (dialog.open) dialog.close();
        dialog.remove();
    }
    function reopen(nextStudio, prompt = '') {
        studio = nextStudio;
        // What the last close left on the dialog (its fade-out, the closing mark) goes, so it shows as it was.
        dialog.getAnimations?.().forEach(animation => animation.cancel());
        delete dialog.dataset.closing; delete dialog.dataset.fvLeaving;
        dialog.onclose = hidden; next = goBack; current = dialog; dialog.inert = false;
        if (!dialog.isConnected) document.body.append(dialog);
        dialog.showModal(); viewChanged('autopilot', node); show(); chat.sync(); card.sync(); assets.refresh(true);
        if (prompt) { chat.input.value = prompt; chat.input.dispatchEvent(new Event('input')); }
        navigation.querySelector('[data-view=autopilot]').focus();
        return dialog;
    }
    dialog.onclose = hidden;
    kept = {node, reopen, dispose, dialog};
    current = dialog; document.body.append(dialog); dialog.showModal(); viewChanged('autopilot', node); show(); assets.refresh(true);
    navigation.querySelector('[data-view=autopilot]').focus();
    return dialog;
}
