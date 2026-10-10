let portFrom = null;

export function takePortMarker() {
    const url = new URL(location.href), raw = url.searchParams.get('freevideo_port_from');
    if (raw === null) return;
    url.searchParams.delete('freevideo_port_from');
    window.history.replaceState(window.history.state, '', url);
    if (portFrom !== null || !/^\d+$/.test(raw)) return;
    const port = Number(raw);
    if (Number.isInteger(port) && port >= 1 && port <= 65535) portFrom = port;
}

// Every panel opened during this page load shows it until it is closed: ComfyUI
// rebuilds the panel when it restores the workflow, right after the first one.
export function createPortNotice(cn) {
    takePortMarker();
    if (portFrom === null) return null;
    const oldPort = portFrom, newPort = location.port || (location.protocol === 'https:' ? '443' : '80');
    const element = document.createElement('aside'); element.className = 'fv-update-notice';
    element.setAttribute('role', 'status');
    const label = document.createElement('span');
    label.textContent = cn
        ? `FreeVideo 现在使用端口 ${newPort}，因为端口 ${oldPort} 已被其他程序占用。如果收藏了 127.0.0.1:${oldPort}，请改为当前地址。`
        : `FreeVideo now uses port ${newPort} because port ${oldPort} is in use by another program. If you bookmarked 127.0.0.1:${oldPort}, change the bookmark to this address.`;
    const close = document.createElement('button'); close.type = 'button';
    close.textContent = cn ? '关闭' : 'Close';
    close.onclick = () => { element.hidden = true; portFrom = null; };
    element.append(label, close);
    return element;
}
