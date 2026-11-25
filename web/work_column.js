// The right column shared by the image view and the creation panel: three pages (works, the shown work's facts,
// the queue of every view), the tab head with its collapse button, and the narrow rail that stands in when it is folded.
// It owns no data: the owner hands in the pages' elements and what to do on collapse, open and a rail thumbnail.
import { el, t, button, readStore, writeStore } from './image_view_ui.js';

export const TABS = ['works', 'info', 'queue'];

/**
 * @param {object} o
 * @param {{element: HTMLElement}} o.works   the works page
 * @param {HTMLElement} o.info               the info page's content
 * @param {{element: HTMLElement}} o.queue   the queue page
 * @param {string} o.storeKey                where the chosen page is kept ('info' is kept as 'works')
 * @param {() => void} o.onCollapse          the collapse button
 * @param {() => void} o.onOpen              the rail's open button
 * @param {(name: string) => void} [o.onTab] a page was chosen
 */
export function createWorkColumn({works, info, queue, storeKey, onCollapse, onOpen, onTab}) {
    const head = el('div', 'fv-iv-panel-head fv-iv-col-head');
    const tabs = el('div', 'fv-iv-seg fv-iv-col-tabs'); tabs.setAttribute('role', 'tablist');
    const tabWorks = button('', {label: t('Creations', '作品'), name: t('Creations', '作品'), use: t('Every image made here', '在这里生成的所有图片')});
    const tabInfo = button('', {label: t('Info', '信息'), name: t('Info', '信息'), use: t('Prompt, model, size and time of the shown image', '当前图片的提示词、模型、尺寸和用时'), key: 'I'});
    const tabQueue = button('', {label: t('Queue', '队列'), name: t('Queue', '队列'), use: t('What is generating in every view', '各个视图正在生成和排队的任务')});
    const worksCount = el('span', 'fv-iv-tab-n'), queueCount = el('span', 'fv-iv-tab-n'); tabWorks.append(worksCount); tabQueue.append(queueCount);
    for (const b of [tabWorks, tabInfo, tabQueue]) b.setAttribute('role', 'tab');
    tabs.append(tabWorks, tabInfo, tabQueue);
    const collapse = button('fv-iv-icon-btn fv-iv-collapse', {iconName: 'panelR', name: t('Hide creations', '收起作品栏'), use: t('More room for the picture', '给画面腾出空间')});
    head.append(tabs, collapse);
    const infoPane = el('div', 'fv-iv-col-pane fv-iv-info-pane'); infoPane.append(info);
    queue.element.classList.add('fv-iv-col-pane');
    const rail = el('div', 'fv-iv-rail fv-iv-rail-right');
    const railOpen = button('fv-iv-icon-btn', {iconName: 'panelR', name: t('Show creations', '展开作品栏'), use: t('Your images, newest first', '你的图片，最新在前')});
    const railThumbs = el('div', 'fv-iv-rail-thumbs');
    rail.append(railOpen, el('i', 'fv-iv-rail-sep'), railThumbs);
    let host = null, tab = readStore(storeKey, 'works');
    if (!TABS.includes(tab)) tab = 'works';
    function setTab(name) {
        tab = name; writeStore(storeKey, name === 'info' ? 'works' : name);
        if (host) host.dataset.tab = name;
        for (const [b, n] of [[tabWorks, 'works'], [tabInfo, 'info'], [tabQueue, 'queue']]) { b.setAttribute('aria-pressed', String(n === name)); b.setAttribute('aria-selected', String(n === name)); }
        works.element.hidden = name !== 'works'; infoPane.hidden = name !== 'info'; queue.element.hidden = name !== 'queue';
        onTab?.(name);
    }
    tabWorks.addEventListener('click', () => setTab('works'));
    tabInfo.addEventListener('click', () => setTab('info'));
    tabQueue.addEventListener('click', () => setTab('queue'));
    collapse.addEventListener('click', () => onCollapse?.());
    railOpen.addEventListener('click', () => onOpen?.());
    return {
        // Puts the column's parts into the shell's right slot; the queue page tells the slot its count.
        mount(slot) {
            host = slot;
            slot.addEventListener('fv-iv-queue', event => { queueCount.textContent = event.detail.active ? String(event.detail.active) : ''; });
            slot.append(head, works.element, infoPane, queue.element, rail);
            slot.dataset.tab = tab;
        },
        setTab, get tab() { return tab; },
        setWorksCount(text) { worksCount.textContent = text; },
        railThumbs, infoPane,
    };
}
