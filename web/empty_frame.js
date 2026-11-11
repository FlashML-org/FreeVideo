// The quiet picture frame shown where a video will appear; the editor preview uses the same drawing.
export const EMPTY_FRAME_PATH = 'M3 4h18v16H3zM3 16l5-6 5 6 3-4 5 6M15 8h.01';
export function emptyFrameIcon() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.classList.add('fv-empty-frame-icon');
    const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', EMPTY_FRAME_PATH); svg.append(path);
    return svg;
}
