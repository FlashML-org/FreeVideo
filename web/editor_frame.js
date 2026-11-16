// Resolution presentation; source dimensions remain authoritative for preview.
import * as model from './editor_model.js';
import {frameShapes, frameSize} from './frame_sizes.js';
import {plan} from './editor_geometry.js';
import {pictureSession} from './editor_picture_session.js';
import {applyTip} from './editor_tracks_tips.js';
const key = source => source && `${source.kind}:${source.id}`;
const info = (sources, source) => typeof sources === 'function' ? sources(key(source)) : sources.get?.(key(source)) || sources[key(source)];
export const validPictureSize = size => Number.isSafeInteger(size?.width) && Number.isSafeInteger(size?.height) && size.width >= 2 && size.height >= 2;
export function clipPictureSize(clip, sources) {
    const value = clip?.source && info(sources, clip.source);
    return value && !value.missing && validPictureSize(value) ? {width: value.width, height: value.height} : null;
}
// Threshold is the total surplus on either axis in output pixels, before rounding.
export function differentPictureShape(size, project) {
    if (!validPictureSize(size) || !validPictureSize(project)) return false;
    const scale = Math.max(project.width / size.width, project.height / size.height);
    return Math.max(size.width * scale - project.width, size.height * scale - project.height) > 1 + 1e-7;
}
// Shared with the future adjustment panel. The caller chooses the actual video part.
export const clipHasDifferentShape = (clip, record, sources) => differentPictureShape(clipPictureSize(clip, sources), record);
export function changePictureFit(state, clip, fit, t) {
    if (clip && (clip.fit || 'cover') !== fit)
        state.apply(t('Frame size', '画面大小'), record => model.setPictureFit(record, clip.id, fit));
}
export const croppedParts = (record, sources) => record.tracks[0].clips.filter(clip => clip.fit !== 'contain' && clipHasDifferentShape(clip, record, sources));
const create = (tag, cls, text) => {
    const e = document.createElement(tag); e.className = cls;
    if (text != null) e.textContent = text; return e;
};
const button = (label, action, cls) => { const e = create('button', cls, label); e.type = 'button'; e.onclick = action; return e; };
export function updatePictureBadge(node, clip, record, size, t) {
    let badge = node.querySelector('.fv-ed-picture-badge');
    const show = differentPictureShape(size, record);
    if (!badge && show) {
        badge = create('span', 'fv-ed-picture-badge');
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('aria-hidden', 'true');
        const path = document.createElementNS(svg.namespaceURI, 'path');
        svg.append(path); badge.append(svg); node.append(badge);
    }
    if (badge) {
        badge.hidden = !show; badge.dataset.fit = clip.fit || 'cover'; badge.setAttribute('aria-label', clip.fit === 'contain' ? t('Fit', '完整显示') : t('Filled', '已铺满'));
        badge.querySelector('path').setAttribute('d', clip.fit === 'contain' ? 'M2 2h12v12H2zM2 5h12M2 11h12' : 'M5 1v10h10M1 5h10v10M7 7h2v2H7z');
    }
}
export function createPictureGuide(host, frame, {state, t}) {
    host.append(frame);
    const shade = create('div', 'fv-ed-crop-shade'); shade.setAttribute('aria-hidden', 'true');
    const bands = ['top', 'bottom', 'left', 'right'].map(side => create('div', `fv-ed-crop-${side}`)); shade.append(...bands);
    const tools = create('div', 'fv-ed-picture-tools'); tools.setAttribute('role', 'group'); tools.setAttribute('aria-label', t('Export frame', '导出画面'));
    let current = null, view = null;
    const session = pictureSession(state, t);
    const fill = button(t('Fill', '铺满'), () => changePictureFit(state, current, 'cover', t), 'fv-ed-picture-fill');
    const fit = button(t('Fit', '完整显示'), () => changePictureFit(state, current, 'contain', t), 'fv-ed-picture-fit');
    applyTip(fill, 'fill', t); applyTip(fit, 'fitPicture', t);
    tools.append(fill, fit); frame.append(shade); host.append(tools);
    function updateTools() {
        if (!view) { tools.hidden = true; return; }
        const {size, record, clip, preview} = view;
        const adjusted = session.clip(clip);
        // Keep the cropped/rotated shape and live crop visibility rules after
        // moving the switch out of the picture, which no longer owns its tools.
        const rotated = clip?.source && validPictureSize(size) ? plan(adjusted, size.width, size.height, record.width, record.height).rotated : size;
        tools.hidden = !!preview || !clip?.source || session.draft?.mode === 'crop'
            || !differentPictureShape({width: Math.round(rotated.width), height: Math.round(rotated.height)}, record);
    }
    const unsubscribe = session.subscribe(updateTools);
    updateTools();
    return {update({size, record, clip, preview, playing}) {
        // The picture renderer owns the final dimensions. Do not overwrite them
        // with the source size on each transport tick and force layout again.
        if (view && view.record === record && view.clip === clip && view.preview === preview && view.playing === playing
            && view.size.width === size.width && view.size.height === size.height) return;
        current = clip; view = {size, record, clip, preview, playing};
        const show = !preview && !!clip?.source && differentPictureShape(size, record);
        const ratio = size.width / size.height, target = record.width / record.height;
        frame.style.setProperty('--fv-ed-width', String(size.width)); frame.style.setProperty('--fv-ed-height', String(size.height));
        // Narrow windows size the preview row by the project's shape, so the timeline does not move as the playhead crosses parts of other shapes.
        const host = frame.closest('.fv-ed-preview');
        if (host) { host.style.setProperty('--fv-ed-project-width', String(record.width)); host.style.setProperty('--fv-ed-project-height', String(record.height)); }
        frame.dataset.playing = String(!!playing);
        updateTools(); shade.hidden = !show || clip?.fit === 'contain';
        fill.setAttribute('aria-pressed', String(clip?.fit !== 'contain')); fit.setAttribute('aria-pressed', String(clip?.fit === 'contain'));
        const x = (1 - Math.min(1, target / ratio)) * 50, y = (1 - Math.min(1, ratio / target)) * 50;
        bands[0].style.height = bands[1].style.height = `${y}%`;
        bands[2].style.width = bands[3].style.width = `${x}%`;
    }, dispose() { unsubscribe(); tools.remove(); frame.remove(); }};
}
const evenSize = size => validPictureSize(size) && size.width <= 8193 && size.height <= 8193
    ? {width: Math.floor(size.width / 2) * 2, height: Math.floor(size.height / 2) * 2} : null;
export function creationPictureSizes(record, capabilities, t) {
    // Use the available pixel tier nearest this edit, with exactly studio's rounding.
    const sizes = (capabilities?.sizes || []).filter(validPictureSize);
    const tier = sizes.reduce((best, size) => !best || Math.abs(size.width * size.height - record.width * record.height)
        < Math.abs(best.width * best.height - record.width * record.height) ? size : best, null);
    return frameShapes(t).map(([id, label, ratio]) => {
        const size = tier && frameSize(tier.pixels || tier.width * tier.height / 1024 ** 2, ratio);
        return {id, label, size: size && size.width >= 256 && size.height >= 256 && size.width <= 4096 && size.height <= 4096 ? size : null};
    });
}
export function createPictureTools({state, sources, t, capabilities, error}) {
    const selected = () => {
        const ids = state.selection?.kind === 'clip' ? state.selection.ids : [];
        const clips = state.record.tracks[0].clips.filter(c => ids.includes(c.id));
        // Audio-only selection and selection of more than one logical part are disabled.
        return clips.length === 1 && ids.every(id => id === clips[0].id || id === clips[0].link) ? clips[0] : null;
    };
    let menuPanel = null, menuToggle = null;
    function renderMenu() {
        if (!menuPanel) return;
        menuToggle.textContent = t('Frame size', '画面大小');
        menuPanel.replaceChildren();
        const firstClip = state.record.tracks[0].clips.find(c => c.source);
        const first = evenSize(clipPictureSize(firstClip, sources)), current = evenSize(clipPictureSize(selected(), sources));
        const shapes = creationPictureSizes(state.record, capabilities(), t);
        const matches = size => size && size.width === state.record.width && size.height === state.record.height;
        const matchedShape = shapes.find(row => matches(row.size));
        const addSize = (size, label, cls, mode, checked, detail = '') => {
            const item = button(label, () => { if (!size) return; try {
                if (!matches(size) || state.record.size_mode !== mode)
                    state.apply(t('Frame size', '画面大小'), r => model.setPictureSize(r, size.width, size.height, mode));
                renderMenu();
            } catch (value) { error(value); } }, cls);
            if (detail) item.append(create('span', 'fv-ed-picture-size-detail', detail));
            item.disabled = !size; item.setAttribute('role', 'menuitemradio'); item.setAttribute('aria-checked', String(checked));
            menuPanel.append(item);
        };
        const firstLabel = t('Match the first part', '跟随第一段');
        addSize(first || (!firstClip ? state.record : null), `${firstLabel}${t(' (', '（')}${(first || state.record).width} × ${(first || state.record).height}${t(')', '）')}`,
            'fv-ed-picture-first', 'first', state.record.size_mode !== 'fixed');
        addSize(current, t('Match the selected part', '跟随选中的一段'), 'fv-ed-picture-match', 'fixed', state.record.size_mode === 'fixed' && !matchedShape);
        for (const row of shapes) addSize(row.size, row.label, 'fv-ed-picture-size-option', 'fixed', state.record.size_mode === 'fixed' && matchedShape === row,
            row.size ? `${row.size.width} × ${row.size.height}` : '');
    }
    return {update: renderMenu, appendMenu(menu) {
        menuToggle = button('', () => { menuPanel.hidden = !menuPanel.hidden; menuToggle.setAttribute('aria-expanded', String(!menuPanel.hidden)); renderMenu(); }, 'fv-ed-picture-size-toggle');
        menuToggle.setAttribute('role', 'menuitem'); menuToggle.setAttribute('aria-expanded', 'false');
        menuPanel = create('div', 'fv-ed-picture-size-menu'); menuPanel.hidden = true; menuPanel.setAttribute('role', 'menu');
        menuPanel.setAttribute('aria-label', t('Frame size', '画面大小'));
        menu.append(menuToggle, menuPanel); renderMenu();
    }};
}
