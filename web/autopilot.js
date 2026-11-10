import {createLanAccess} from './lan_access.js';
// The Agent's own page, beside the studio and the editing view: the card that connects one, then the conversation with it.
import { api } from '../../scripts/api.js';
import { agentConnectCard, connectionLabel } from './agent_connect.js';
import { about, agentChat, jobName } from './autopilot_chat.js';
import { createRequestAssets } from './request_assets.js';
import { openEditorView } from './editor_loader.js';
import { closeDialog } from './motion.js';
import { createVersionInfo, productVersion } from './updates.js';
import { openSetup } from './setup.js';
import { feedbackButton, guideButton } from './feedback.js';
import { openExtendEditor, openStudio, showNodes } from './studio.js';
import { agentTabReady, viewChanged, viewSwitch } from './view_navigation.js';
import { wordmark } from './wordmark.js';

const languageOverride = typeof location !== 'undefined'
    ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en'
    && String(navigator.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

let current = null;
// Opens above the studio when one is open, as the editing view does, so going back finds it as it was.
export function openAutopilotView(node, {studio = null, initialPrompt = '', initialScene = ''} = {}) {
    if (current?.open) { viewChanged('autopilot', node); return current; }
    studio?.querySelectorAll('video,audio').forEach(media => media.pause());
    const dialog = el('dialog', 'fv-studio fv-autopilot'); dialog.lang = cn ? 'zh' : 'en'; dialog.setAttribute('aria-label', t('Autopilot', 'AI 全自动'));
    const header = el('header', 'fv-header'), brand = el('div', 'fv-brand'), tools = el('div', 'fv-header-actions');
    brand.append(wordmark());
    const settings = el('button', 'fv-quiet', t('Settings', '设置')); settings.type = 'button'; settings.onclick = openSetup;
    // The one place this page says who is connected, and beside it what that agent is making now.
    const lamp = el('button', 'fv-autopilot-lamp'), lampText = el('span'); lamp.type = 'button'; lamp.append(el('i'), lampText);
    const now = el('div', 'fv-autopilot-now'), go = el('button', 'fv-autopilot-now-go'), halt = el('button', 'fv-autopilot-now-stop'); now.hidden = true;
    const ring = el('i'), name = el('span', 'fv-autopilot-now-name'), more = el('span'), percent = el('b'), eta = el('em');
    go.type = halt.type = 'button'; go.append(ring, name, more, percent, eta); now.append(go, halt);
    halt.title = t('Stop all', '全部停止'); halt.setAttribute('aria-label', halt.title);
    const line = el('i', 'fv-autopilot-line');
    const phoneAccess = createLanAccess(t, api);
    const versionInfo = createVersionInfo(cn);
    tools.append(now, lamp, phoneAccess.element, versionInfo.element, guideButton(t), feedbackButton(t), settings);
    // The view chosen next takes over once this page has closed; Escape goes back to what is underneath.
    let next = () => studio?.open ? viewChanged('studio', node) : viewChanged('nodes', node);
    const leave = action => { next = action; closeDialog(dialog); };
    const navigation = viewSwitch('autopilot', () => leave(() => studio?.open ? viewChanged('studio', node) : openStudio(node)),
        () => leave(() => showNodes(node, studio)), () => leave(() => openEditorView(node, {studio: studio?.open ? studio : undefined})), () => {});
    header.append(brand, navigation, tools, line);
    const inSight = () => dialog.open && dialog.dataset.closing !== 'true';
    const body = el('div', 'fv-autopilot-body');
    const card = agentConnectCard(t, api, () => inSight() && !card.element.hidden, value => {
        on = value; show();
        if (value !== 'unknown') agentTabReady(value);
        if (value === 'on') chat.sync();
    });
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
    const chat = agentChat(t, api, {shown: inSight, productVersion, hint: assets, initialPrompt, initialWhiteboxPreview: !!initialPrompt, initialScene,
        // Creations continues the work on the editor timeline; this page gives way first.
        open: async job => (await import('./library.js')).openLibrary(t, {selectedWork: job.work, back: t('Back to chat', '返回对话'),
            extend: async (record, options) => { await closed(); return openExtendEditor(record, node, {studio, ...options}); }}),
        edit: job => leave(() => openEditorView(node, {studio: studio?.open ? studio : undefined, work: {id: job.work, video: `FreeVideo/${job.work}/video.mp4`, geometry: {width: job.width, height: job.height, seconds: job.seconds}}})),
        changed: view => { last = view; show(); }});
    // With no agent the card stands alone in the middle. Once one has connected the conversation stays, also after
    // it disconnects; the light then leads back to the card.
    let last = {known: false, agent: {}, current: null, active: 0, any: false}, on = null, wantCard = false, currentId = null;
    function show() {
        const connected = last.agent.connected === true, label = connectionLabel(last.agent, t);
        if (connected) wantCard = false;
        const ready = on === 'on' && last.known, talking = ready && (connected || (last.any && !wantCard));
        // The card gives way to the conversation: typing can start at once.
        const arrived = talking && chat.element.hidden && (card.element.contains(document.activeElement) || document.activeElement === lamp);
        // A check that failed keeps the card up: its retry is the way back.
        card.element.hidden = !(ready || on === 'unknown') || talking; chat.element.hidden = !talking;
        body.dataset.view = talking ? 'chat' : 'connect';
        if (talking) chat.sync();
        if (arrived) chat.input.focus();
        lamp.hidden = !ready; lamp.dataset.color = label.color; lampText.textContent = lamp.title = label.text; lamp.setAttribute('aria-label', label.text); lamp.disabled = connected || !last.any;
        const job = talking ? last.current : null;
        phoneAccess.observe(job);
        now.hidden = !job; line.dataset.on = String(!!job);
        halt.disabled = !!last.stopping;
        halt.setAttribute('aria-busy', String(!!last.stopping));
        halt.textContent = last.stopping ? t('Stopping…', '正在停止…') : '';
        halt.title = last.stopping ? t('Stopping…', '正在停止…') : t('Stop all', '全部停止'); halt.setAttribute('aria-label', halt.title);
        if (job) {
            const fraction = last.fraction || 0, running = job.status === 'running';
            currentId = job.id; name.textContent = jobName(job, t); more.textContent = last.active > 1 ? `+${last.active - 1}` : '';
            percent.textContent = running && fraction >= .01 ? `${Math.floor(fraction * 100)}%` : '';
            eta.textContent = running && last.left != null ? about(last.left, t) : '';
            ring.style.setProperty('--p', String(fraction * 100)); line.style.width = `${fraction * 100}%`;
        }
        card.sync();
    }
    lamp.onclick = () => { wantCard = !wantCard; show(); };
    go.onclick = () => chat.scrollToJob(currentId);
    halt.onclick = () => chat.stopAll();
    body.append(card.element, chat.element);
    card.state.then(value => { on = value; show(); if (value === 'on') chat.sync(); });
    show();
    dialog.append(header, body);
    dialog.addEventListener('cancel', event => { event.preventDefault(); closeDialog(dialog); });
    dialog.onclose = () => {
        phoneAccess.dispose(); versionInfo.dispose(); card.stop(); chat.stop(); assets.dispose(); dialog.remove();
        if (current === dialog) current = null;
        next();
    };
    current = dialog; document.body.append(dialog); dialog.showModal(); viewChanged('autopilot', node); show(); assets.refresh(true);
    navigation.querySelector('[data-view=autopilot]').focus();
    return dialog;
}
