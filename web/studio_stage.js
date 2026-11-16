// The bar under the picture: what the picture is and where it sits in the history on the left, what can be done
// with it on the right. The run's numbers and notes fold into Details, so the bar keeps one line and the picture
// never moves when a note arrives.
import {tip} from './studio_console.js';

const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const svg = d => `<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const INFO = '<circle cx="8" cy="8" r="6"/><path d="M8 7.2v3.6M8 5.2v.1"/>';
const LEFT = '<path d="m9.8 4-4 4 4 4"/>', RIGHT = '<path d="m6.2 4 4 4-4 4"/>';

export function createStageBar(t, {details = [], onStep = () => {}}) {
    const element = el('div', 'fvs-stagebar');
    const start = el('div', 'fvs-stagebar-start'), end = el('div', 'fvs-stagebar-end');
    // Details: a popover above its button with the run's numbers and every note about this video.
    const info = el('details', 'fvs-info');
    const infoButton = el('summary', 'fvs-icon-button'); infoButton.innerHTML = svg(INFO);
    infoButton.setAttribute('aria-label', t('Details', '详情'));
    tip(infoButton, t('Details', '详情'), t('Time, memory and notes for this video', '用时、显存和这条视频的说明'), 'I');
    const panel = el('div', 'fvs-info-panel'); panel.setAttribute('role', 'group'); panel.setAttribute('aria-label', t('Details', '详情'));
    const empty = el('p', 'fvs-info-empty', t('No details for this video.', '这条视频没有详情。'));
    panel.append(...details, empty);
    info.append(infoButton, panel);
    const outside = event => { if (info.open && !info.contains(event.target)) info.open = false; };
    info.addEventListener('toggle', () => {
        if (info.open) document.addEventListener('pointerdown', outside, true);
        else document.removeEventListener('pointerdown', outside, true);
    });
    info.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || !info.open) return;
        event.preventDefault(); event.stopPropagation(); info.open = false; infoButton.focus();
    });
    // What is on screen: the work's name (its prompt's opening words) over the quieter facts (size · length · level).
    const summary = el('span', 'fvs-summary'), summaryName = el('span', 'fvs-summary-name'), summaryMeta = el('span', 'fvs-summary-meta');
    summary.append(summaryName, summaryMeta);
    // The level block goes whole when the facts do not fit; what still does not fit ends in an ellipsis.
    const fitFacts = () => {
        const level = summaryMeta.querySelector('[data-part=level]'), over = () => summary.scrollWidth > summary.clientWidth;
        if (level) level.hidden = false;
        delete summaryMeta.dataset.tight;
        if (level && over()) level.hidden = true;
        if (over()) summaryMeta.dataset.tight = 'true';
    };
    const factsWatch = typeof ResizeObserver === 'function' ? new ResizeObserver(() => fitFacts()) : null;
    factsWatch?.observe(summary);
    // History: the previous and next video of the recent list, with the place of this one.
    const history = el('div', 'fvs-history'); history.setAttribute('role', 'group'); history.setAttribute('aria-label', t('History', '历史'));
    const prev = el('button', 'fvs-icon-button'); prev.type = 'button'; prev.innerHTML = svg(LEFT);
    const next = el('button', 'fvs-icon-button'); next.type = 'button'; next.innerHTML = svg(RIGHT);
    const place = el('span', 'fvs-history-place');
    prev.setAttribute('aria-label', t('Newer video', '较新的一条')); next.setAttribute('aria-label', t('Older video', '较早的一条'));
    tip(prev, t('Newer', '较新'), t('Show the newer video', '显示较新的一条视频'), '←');
    tip(next, t('Older', '较早'), t('Show the older video', '显示较早的一条视频'), '→');
    prev.onclick = () => onStep(-1); next.onclick = () => onStep(1);
    history.append(prev, place, next); history.hidden = true;
    start.append(info, summary, history);
    element.append(start, end);
    function sync() {
        const shown = [...panel.children].filter(item => item !== empty && !item.hidden && item.textContent.trim());
        empty.hidden = shown.length > 0;
        // A note (not just the numbers) marks the button, so a slower run's reason is not missed.
        const notes = shown.filter(item => item.classList.contains('fv-note') || item.classList.contains('fv-reuse-result'));
        infoButton.dataset.note = String(notes.length > 0);
    }
    return {element, start, end, summary, sync,
        // name: the work's name ('' while it is still being read, or for a run); full: the whole prompt for the hover.
        // parts: the facts as blocks ({key, text}), so a narrow bar can drop the least important one (the level) whole.
        // The next generation's empty frame is on the stage: the bar names it and its pixels, and hides the work's actions.
        setNext(text) { if (text) element.dataset.next = 'true'; else delete element.dataset.next; },
        setSummary(text, {name = '', full = '', parts = null} = {}) {
            summaryName.textContent = name; summaryName.hidden = !name;
            if (parts?.length) summaryMeta.replaceChildren(...parts.map(part => { const block = el('span', 'fvs-summary-part', part.text); block.dataset.part = part.key; return block; }));
            else summaryMeta.textContent = text || '';
            summaryMeta.hidden = !text; fitFacts();
            summary.title = full || [name, text].filter(Boolean).join(' · ');
        },
        setHistory(index, count) {
            history.hidden = !(count > 1) || index < 0;
            place.textContent = index >= 0 ? `${index + 1} / ${count}` : '';
            prev.disabled = index <= 0; next.disabled = index < 0 || index >= count - 1;
        },
        dispose() { document.removeEventListener('pointerdown', outside, true); factsWatch?.disconnect(); },
    };
}
