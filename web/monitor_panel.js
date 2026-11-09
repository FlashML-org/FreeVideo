const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('./monitor_panel.css', import.meta.url).href; document.head.append(css);
const shownKey = 'freevideo.monitor.shown', changed = 'freevideo-monitor-shown';
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const GIB = 2 ** 30, memoryLimit = 95, temperatureLimit = 85;
export function monitorShown() { try { return localStorage.getItem(shownKey) === '1'; } catch { return false; } }
export function setMonitorShown(value) {
    try { localStorage.setItem(shownKey, value ? '1' : '0'); } catch {}
    window.dispatchEvent(new Event(changed));
}
export function onMonitorShown(listener) {
    const storage = event => { if (event.key === shownKey) listener(); };
    window.addEventListener(changed, listener); window.addEventListener('storage', storage);
    return () => { window.removeEventListener(changed, listener); window.removeEventListener('storage', storage); };
}
const gib = bytes => (bytes / GIB).toFixed(1);
const svg = d => {
    const root = document.createElementNS('http://www.w3.org/2000/svg', 'svg'), path = document.createElementNS(root.namespaceURI, 'path');
    root.setAttribute('viewBox', '0 0 24 24'); root.setAttribute('aria-hidden', 'true'); path.setAttribute('d', d); root.append(path); return root;
};
// The top-bar capsule and its panel. Nothing is read from the server while the setting is off.
export function createMonitor(t, api) {
    const element = el('div', 'fv-monitor'), trigger = el('button', 'fv-monitor-trigger'), text = el('span', 'fv-monitor-text'), panel = el('section', 'fv-monitor-panel');
    const label = t('System status', '运行状态');
    trigger.type = 'button'; trigger.setAttribute('aria-label', label); trigger.setAttribute('aria-expanded', 'false'); trigger.setAttribute('aria-haspopup', 'dialog');
    trigger.append(svg('M3 12h4l3-8 4 16 3-8h4'), text);
    panel.hidden = true; panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', label);
    const name = el('strong', 'fv-monitor-name'), rows = el('div', 'fv-monitor-rows'), stop = el('button', 'fv-monitor-stop', t('Stop now', '立即停止'));
    stop.type = 'button';
    const stopped = el('div', 'fv-monitor-stopped'), stoppedText = el('span', '', t('Stopped. The queue is paused.', '已停止，队列已暂停。')), resume = el('button', 'fv-monitor-resume', t('Resume queue', '继续队列'));
    resume.type = 'button'; stopped.append(stoppedText, resume); stopped.hidden = true;
    panel.append(name, rows, stopped, stop); element.append(trigger, panel); element.hidden = true;
    let state = {}, timer = null, reading = false, disposed = false, confirming = null, active = false;
    const row = (title, value, bar, warn) => {
        const line = el('div', 'fv-monitor-row'), head = el('div', 'fv-monitor-line');
        const number = el('span', warn ? 'fv-monitor-value fv-monitor-warn' : 'fv-monitor-value', value);
        head.append(el('span', 'fv-monitor-title', title), number); line.append(head);
        if (bar != null) { const track = el('div', 'fv-monitor-bar'), fill = el('i'); fill.style.width = `${Math.min(100, bar)}%`; if (warn) fill.className = 'fv-monitor-warn'; track.append(fill); line.append(track); }
        return line;
    };
    const percent = value => state.memory_total ? Math.round(100 * state.memory_used / state.memory_total) : null;
    function render() {
        const memory = state.memory_total ? percent() : null, hot = state.temperature >= temperatureLimit, full = memory != null && memory >= memoryLimit;
        text.replaceChildren();
        const parts = [];
        if (memory != null) parts.push([`${t('VRAM', '显存')} `, `${memory}%`, full]);
        if (state.temperature != null) parts.push(['', `${state.temperature} °C`, hot]);
        parts.forEach(([label, value, warn], index) => {
            if (index) text.append(document.createTextNode(' · '));
            if (label) text.append(document.createTextNode(label));
            text.append(el('span', warn ? 'fv-monitor-warn' : '', value));
        });
        text.hidden = !parts.length;
        name.textContent = state.name || ''; name.hidden = !state.name;
        const list = [];
        if (memory != null) list.push(row(t('VRAM', '显存'), `${gib(state.memory_used)} / ${gib(state.memory_total)} GiB`, memory, full));
        if (state.utilization != null) list.push(row(t('Utilization', '使用率'), `${state.utilization}%`));
        if (state.temperature != null) list.push(row(t('Temperature', '温度'), `${state.temperature} °C`, null, hot));
        if (state.disk_free != null) list.push(row(t('Disk', '硬盘'), t(`${gib(state.disk_free)} GiB free`, `剩余 ${gib(state.disk_free)} GiB`)));
        rows.replaceChildren(...list);
        stopped.hidden = !state.paused;
        stop.disabled = !state.busy;
        if (!confirming) stop.textContent = t('Stop now', '立即停止');
    }
    async function read() {
        clearTimeout(timer);
        if (disposed || !active || reading) return;
        reading = true;
        try {
            const response = await api.fetchApi('/freevideo/monitor', {signal: AbortSignal.timeout(10000)});
            if (response.ok && !disposed && active) { state = await response.json(); render(); }
        } catch {}
        finally { reading = false; if (!disposed && active) timer = setTimeout(read, document.hidden ? 10000 : 2000); }
    }
    function position() {
        if (panel.hidden) return;
        const box = trigger.getBoundingClientRect(); panel.style.top = `${box.bottom + 8}px`;
        panel.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - 288))}px`;
    }
    function revert() { clearTimeout(confirming); confirming = null; stop.classList.remove('fv-monitor-confirm'); stop.textContent = t('Stop now', '立即停止'); }
    function close() { panel.hidden = true; trigger.setAttribute('aria-expanded', 'false'); revert(); }
    trigger.onclick = () => { if (panel.hidden) { panel.hidden = false; trigger.setAttribute('aria-expanded', 'true'); position(); } else close(); };
    stop.onclick = async () => {
        if (stop.disabled) return;
        if (!confirming) {
            stop.classList.add('fv-monitor-confirm'); stop.textContent = t('Confirm stop', '确认停止');
            confirming = setTimeout(revert, 4000); return;
        }
        revert(); stop.disabled = true;
        try { await api.fetchApi('/freevideo/monitor/stop', {method: 'POST'}); } catch {}
        state = {...state, paused: true, busy: false}; render(); read();
    };
    resume.onclick = async () => {
        resume.disabled = true;
        try { await api.fetchApi('/freevideo/monitor/resume', {method: 'POST'}); state = {...state, paused: false}; } catch {}
        resume.disabled = false; render(); read();
    };
    const outside = event => { if (!element.contains(event.target)) close(); };
    // Escape reaches the panel only through the dialog's cancel: the studio routes its keys itself.
    const dismiss = () => { if (panel.hidden) return false; close(); trigger.focus(); return true; };
    document.addEventListener('pointerdown', outside, true); window.addEventListener('resize', position);
    function apply() {
        const wanted = monitorShown();
        if (wanted === active) return;
        active = wanted; element.hidden = !wanted;
        if (wanted) read(); else { clearTimeout(timer); close(); state = {}; }
    }
    const stopWatching = onMonitorShown(apply); apply();
    return {element, dismiss, dispose() { disposed = true; active = false; clearTimeout(timer); revert(); stopWatching(); document.removeEventListener('pointerdown', outside, true); window.removeEventListener('resize', position); }};
}
