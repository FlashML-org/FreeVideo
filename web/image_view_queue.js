// The works column's Queue page: what is running and waiting in every view, the latest results, and Stop.
// It reads the page-wide job store (task_store.js), as the floating window does; it never owns a job.
// Rows in the order of the store (running, then waiting, then finished); a row opens its own generation screen.
import { el, icon, t, tip, button } from './image_view_ui.js';

const VIEW_WORDS = {image: ['Images', '图像视图'], studio: ['Create', '创作面板'], editor: ['Edit', '剪辑视图'], autopilot: ['Autopilot', 'AI 全自动'], nodes: ['Nodes', '节点视图']};
const STATUS_WORDS = {submitting: ['Starting', '正在开始'], queued: ['Waiting', '排队中'], running: ['Generating', '正在生成'], cancelling: ['Stopping', '正在停止'],
    done: ['Done', '已完成'], failed: ['Failed', '失败'], cancelled: ['Stopped', '已停止'], interrupted: ['Interrupted', '已中断']};
const ACTIVE = new Set(['submitting', 'queued', 'running', 'cancelling']);
export const queueSummary = rows => {
    const running = rows.filter(r => r.status === 'running' || r.status === 'cancelling'), waiting = rows.filter(r => r.status === 'queued' || r.status === 'submitting');
    return {running, waiting, active: running.length + waiting.length, percent: running[0] && Number.isFinite(running[0].percent) ? running[0].percent : null};
};

export function queuePanel({jobs, confirmStop, pictureURL, onOpen}) {
    const root = el('div', 'fv-iv-queue');
    const body = el('div', 'fv-iv-queue-body'), foot = el('div', 'fv-iv-panel-foot fv-iv-queue-foot');
    const footText = el('span', 'fv-iv-queue-sum');
    const stop = button('fv-iv-text-btn fv-iv-danger', {iconName: 'stop', label: t('Stop', '停止'), name: t('Stop', '停止'), use: t('Stop what is generating; asks first', '停止正在生成的任务，会先确认')});
    foot.append(footText, el('span', 'fv-iv-grow'), stop);
    root.append(body, foot);
    let rows = [];
    stop.addEventListener('click', async () => {
        const {running} = queueSummary(rows); const row = running[0]; if (!row) return;
        if (!await confirmStop(stop, {kind: row.kind === 'image' ? 'image' : 'video', queued: false})) return;
        try { await jobs.cancel(row.id); } catch { /* the row says it is still running */ }
    });
    function line(row) {
        const item = el('div', 'fv-iv-qrow'); item.dataset.status = row.status;
        const open = el('button', 'fv-iv-qrow-open'); open.type = 'button';
        const th = el('span', 'fv-iv-qthumb');
        const preview = row.meta?.preview || (row.status === 'done' && row.kind === 'image' && row.result?.file ? pictureURL(row.result.file) : '');
        if (preview) { const i = el('img'); i.alt = ''; i.src = preview; i.draggable = false; th.append(i); } else th.append(icon(row.kind === 'image' ? 'asref' : 'single'));
        const tx = el('span', 'fv-iv-qtx');
        const title = (row.meta?.text || row.prompt || '').trim() || t(row.kind === 'image' ? 'Picture' : 'Video', row.kind === 'image' ? '图片' : '视频');
        const status = t(...(STATUS_WORDS[row.status] || STATUS_WORDS.running)) + (row.status === 'running' && Number.isFinite(row.percent) ? ` ${Math.floor(row.percent * 100)}%` : '');
        tx.append(el('b', '', title), el('small', '', [t(...(VIEW_WORDS[row.view] || VIEW_WORDS.studio)), status].join(' · ')));
        open.append(th, tx);
        tip(open, title.length > 40 ? title.slice(0, 40) + '…' : title, ACTIVE.has(row.status) ? t('Back to its generation screen', '回到它的生成画面') : t('Open the result', '打开结果'));
        open.addEventListener('click', () => onOpen(row));
        item.append(open);
        if (row.status === 'running') { const bar = el('i', 'fv-iv-qbar'); bar.style.setProperty('--p', String(row.percent || 0)); item.append(bar); }
        if (ACTIVE.has(row.status) && row.status !== 'cancelling') {
            const x = button('fv-iv-icon-btn fv-iv-qstop', {iconName: row.status === 'running' ? 'stop' : 'x',
                name: row.status === 'running' ? t('Stop', '停止') : t('Cancel', '取消排队'), use: row.status === 'running' ? t('Stop this one; asks first', '停止这一个，会先确认') : t('Take it out of the queue', '移出队列')});
            x.addEventListener('click', async event => {
                event.stopPropagation();
                if (!await confirmStop(x, {kind: row.kind === 'image' ? 'image' : 'video', queued: row.status !== 'running'})) return;
                try { await jobs.cancel(row.id); } catch { /* still listed */ }
            });
            item.append(x);
        }
        return item;
    }
    function paint() {
        const active = rows.filter(r => ACTIVE.has(r.status)), done = rows.filter(r => !ACTIVE.has(r.status)).slice(0, 8);
        const sections = [];
        const head = text => el('div', 'fv-iv-day', text);
        sections.push(head(t('Queue', '队列')));
        if (active.length) sections.push(...active.map(line));
        else { const e = el('div', 'fv-iv-empty fv-iv-empty-small'); e.append(icon('clock'), el('span', '', t('Nothing is generating', '没有正在生成的任务'))); sections.push(e); }
        if (done.length) { sections.push(head(t('Recent', '最近完成'))); sections.push(...done.map(line)); }
        body.replaceChildren(...sections);
        const {running, waiting, percent} = queueSummary(rows);
        footText.textContent = running.length ? [t(`Generating ${percent == null ? '' : Math.floor(percent * 100) + '%'}`.trim(), `正在生成 ${percent == null ? '' : Math.floor(percent * 100) + '%'}`.trim()), waiting.length ? t(`${waiting.length} waiting`, `排队 ${waiting.length}`) : ''].filter(Boolean).join(' · ')
            : waiting.length ? t(`${waiting.length} waiting`, `排队 ${waiting.length}`) : t('Idle', '空闲');
        stop.hidden = !running.length;
        root.dataset.count = String(active.length);
        root.dispatchEvent(new CustomEvent('fv-iv-queue', {bubbles: true, detail: {active: active.length}}));
    }
    const off = jobs.subscribe(list => { rows = list || []; paint(); });
    return {element: root, dispose: () => off?.(), count: () => rows.filter(r => ACTIVE.has(r.status)).length};
}
