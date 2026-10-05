import { createErrorPanel, errorText, createErrorReport } from './error_panel.js';
import { app } from '../../scripts/app.js';
import { api } from '../../scripts/api.js';
import { openSetup } from './setup.js';
import { wordmark } from './branding.js';
import { createGenerationProgress } from './generation_progress.js';
import { viewSwitch, viewChanged } from './view_navigation.js';
import { createUpdateNotice, createVersionInfo } from './updates.js';
import { createPreviewScene } from './preview_scene.js?v=20260929-swell';
import { animateDetails, closeDialog } from './motion.js';
import { openLibrary, latestVideo } from './library.js';
import { createStudioQueue, randomSeed } from './studio_queue.js';
import { attachReferencePicker, referenceItems, syncReferencePrompt } from './prompt_references.js';
import { outputDownloadURL } from './output_download.js';
import { createSamplingEffort } from './sampling_effort.js';
import { shareButton } from './share.js';

const languageOverride = typeof location !== 'undefined'
    ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en'
    && String(navigator.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;
const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('./studio.css', import.meta.url).href; document.head.append(css);
const effortCss = document.createElement('link'); effortCss.rel = 'stylesheet'; effortCss.href = new URL('./sampling_effort.css', import.meta.url).href; document.head.append(effortCss);
const el = (tag, label, cls) => { const e = document.createElement(tag); if (label != null) e.textContent = label; if (cls) e.className = cls; return e; };
const button = (label, action, cls = '') => { const e = el('button', label, cls); e.type = 'button'; e.onclick = action; return e; };
const widget = (node, name) => node?.widgets?.find(w => w.name === name);
const linked = (node, name) => node?.inputs?.some(input => input.name === name && input.link != null);
const value = (node, name) => widget(node, name)?.value;
function set(node, name, value) {
    const w = widget(node, name); if (!w || linked(node, name)) return false;
    w.value = value; w.callback?.(value); node.graph?.change(); node.setDirtyCanvas?.(true, true); return true;
}
function upstream(node, name) {
    const input = node.inputs?.find(i => i.name === name);
    const link = input?.link != null ? node.graph.links[input.link] : null;
    return link ? node.graph.getNodeById(link.origin_id) : null;
}
function view(file, type = 'input') {
    const split = file.lastIndexOf('/');
    return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(split + 1), subfolder: split < 0 ? '' : file.slice(0, split), type}));
}
const number = (n, divisor = 1, unit = 's') => Number.isFinite(n) && n >= 0 ? `${(n / divisor).toFixed(1)} ${unit}` : '—';

export const loraWarning = () => t(
    'FreeVideo needs no acceleration LoRA.',
    'FreeVideo无需添加加速LoRA');

export function promptGuide() {
    const note = el('p', null, 'fv-prompt-guide');
    const link = el('a', 'MiniMax H3 Prompt Skill ↗');
    link.href = 'https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md';
    link.target = '_blank'; link.rel = 'noopener noreferrer';
    note.append(document.createTextNode(t('For better results, ask AI to rewrite your prompt using ', '让AI参考 ')),
        link, document.createTextNode(t('. We recommend ', ' 重写提示词效果会更好。推荐使用 ')));
    const freeToken = el('a', 'FreeToken ↗');
    freeToken.href = 'https://www.flashml.ai/';
    freeToken.target = '_blank'; freeToken.rel = 'noopener noreferrer';
    note.append(freeToken, document.createTextNode(t('.', '。')));
    return note;
}

export function loraPanel(node, panel) {
    const list = el('div', null, 'fv-lora-list'), note = el('p', '', 'fv-lora-note');
    const actions = el('div', null, 'fv-lora-add'); panel.append(list, actions, note);
    let names = [], disposed = false;
    const read = () => { const rows = JSON.parse(value(node, 'adapters') || '[]'); if (!Array.isArray(rows) || rows.length > 32) throw new Error(t('Use at most 32 LoRAs', '最多使用 32 个 LoRA')); return rows; };
    const write = rows => { set(node, 'adapters', JSON.stringify(rows)); window.dispatchEvent(new CustomEvent('freevideo-loras', {detail: node.id})); };
    function render() {
        if (disposed) return; list.replaceChildren();
        try {
            const rows = read();
            rows.forEach((row, index) => {
                const wrap = el('div', null, 'fv-lora-row'); wrap.dataset.enabled = String(row.enabled !== false);
                const enabled = el('input'); enabled.type = 'checkbox'; enabled.checked = row.enabled !== false;
                enabled.setAttribute('aria-label', t('Enable LoRA', '启用 LoRA')); enabled.onchange = () => { row.enabled = enabled.checked; write(rows); };
                const select = el('select'); select.setAttribute('aria-label', t('LoRA model', 'LoRA 模型'));
                for (const name of [...new Set([row.name, ...names])].filter(Boolean)) { const option = el('option', name); option.value = name; select.append(option); }
                select.value = row.name; select.title = row.name; select.onchange = () => { row.name = select.value; write(rows); };
                const strength = el('input'); strength.type = 'number'; strength.min = '-4'; strength.max = '4'; strength.step = '.05'; strength.value = row.strength ?? 1;
                strength.setAttribute('aria-label', t('LoRA strength', 'LoRA 强度')); strength.onchange = () => { if (strength.reportValidity() && strength.value !== '') { row.strength = Number(strength.value); write(rows); } };
                select.disabled = strength.disabled = row.enabled === false;
                const remove = button('×', () => { rows.splice(index, 1); write(rows); }); remove.setAttribute('aria-label', t('Remove LoRA', '移除 LoRA'));
                wrap.append(enabled, select, strength, remove);
                if (rows.length > 1) {
                    const order = el('div', null, 'fv-lora-order');
                    for (const [label, delta] of [['↑', -1], ['↓', 1]]) {
                        const move = button(label, () => { [rows[index], rows[index + delta]] = [rows[index + delta], rows[index]]; write(rows); });
                        move.disabled = index + delta < 0 || index + delta >= rows.length;
                        move.setAttribute('aria-label', delta < 0 ? t('Move LoRA earlier', 'LoRA 前移') : t('Move LoRA later', 'LoRA 后移')); order.append(move);
                    }
                    wrap.append(order);
                }
                list.append(wrap);
            });
            note.textContent = loraWarning();
        } catch (error) { note.textContent = error.message; }
    }
    const add = button(t('+ Add LoRA', '+ 添加 LoRA'), () => {
        try { const rows = read(); if (rows.length >= 32) throw new Error(t('Use at most 32 LoRAs', '最多使用 32 个 LoRA')); if (!names.length) throw new Error(t('Put H3 safetensors adapters in your ComfyUI LoRA folders, then refresh.', '将 H3 safetensors LoRA 放入 ComfyUI 的 LoRA 目录，然后刷新。')); rows.push({name: names[0], strength: 1, enabled: true}); write(rows); } catch (error) { note.textContent = error.message; }
    });
    async function refresh() {
        try { const response = await api.fetchApi('/object_info/FreeVideoLoRA'); if (!response.ok) throw new Error(response.statusText); const info = await response.json(); const spec = info.FreeVideoLoRA?.input?.required?.lora; const options = Array.isArray(spec?.[0]) ? spec[0] : spec?.[1]?.options || []; names = options.filter(n => typeof n === 'string' && n.toLowerCase().endsWith('.safetensors')); render(); }
        catch (error) { if (!disposed) note.textContent = error.message; }
    }
    actions.append(add, button(t('Refresh', '刷新'), refresh));
    const changed = e => { if (String(e.detail) === String(node.id)) render(); };
    window.addEventListener('freevideo-loras', changed);
    const configure = node.onConfigure;
    // Only the persistent node panel participates in graph loading.
    if (panel.classList.contains('fv-lora-panel')) node.onConfigure = function (...args) { const result = configure?.apply(this, args); render(); return result; };
    render(); refresh();
    return () => { disposed = true; window.removeEventListener('freevideo-loras', changed); };
}

let current;
const studioQueues = new WeakMap();
export function openStudio(node) {
    if (current?.node === node && current.dialog.open) { current.dialog.focus(); return; }
    current?.dialog.close();
    const dialog = el('dialog', null, 'fv-studio'); dialog.setAttribute('aria-label', t('FreeVideo creative workspace', 'FreeVideo 创作面板'));
    current = {node, dialog}; const cleanup = [];
    const header = el('header', null, 'fv-header'), brand = el('div', null, 'fv-brand');
    brand.append(wordmark());
    const tools = el('div', null, 'fv-header-actions');
    const versionInfo = createVersionInfo(cn); tools.append(versionInfo.element); cleanup.push(versionInfo.dispose);
    tools.append(button(t('Settings', '设置'), openSetup, 'fv-quiet'));
    const navigation = viewSwitch('studio', () => {}, () => {
        closeDialog(dialog);
        // Fit the graph in the visible canvas; never rewrite users' node positions.
        const nodes = node.graph?._nodes || [], canvas = app.canvas?.canvas;
        if (!nodes.length || !canvas) return;
        const left = Math.min(...nodes.map(n => n.pos[0])), top = Math.min(...nodes.map(n => n.pos[1] - 32));
        const width = Math.max(...nodes.map(n => n.pos[0] + n.size[0])) - left;
        const height = Math.max(...nodes.map(n => n.pos[1] + n.size[1])) - top;
        const scale = Math.min(1, (canvas.clientWidth - 96) / width, (canvas.clientHeight - 96) / height);
        if (scale > 0) {
            app.canvas.ds.scale = scale;
            app.canvas.ds.offset = [-left + (canvas.clientWidth / scale - width) / 2, -top + (canvas.clientHeight / scale - height) / 2];
            app.canvas.setDirty(true, true);
        }
    });
    header.append(brand, navigation, tools);
    const body = el('div', null, 'fv-body'), controls = el('div', null, 'fv-controls'), output = el('div', null, 'fv-preview-column');
    dialog.append(header, body); body.append(controls, output);
    const updateNotice = createUpdateNotice(cn);
    output.append(updateNotice.element); cleanup.push(updateNotice.dispose);
    const section = (label, parent = controls) => { const wrap = el('section', null, 'fv-section'); wrap.append(el('div', label, 'fv-label')); parent.append(wrap); return wrap; };
    const field = (name, input) => { const label = el('label', null, 'fv-field'); label.append(el('span', name), input); return label; };
    const expand = label => { const d = el('details', null, 'fv-section'); d.append(el('summary', label, 'fv-section-title')); const content = el('div', null, 'fv-expand'); d.append(content); controls.append(d); cleanup.push(animateDetails(d)); return [d, content]; };
    syncReferencePrompt(node, t);
    const prompt = el('textarea'); prompt.value = value(node, 'text') || ''; prompt.placeholder = t('Describe the scene… Type @ to reference media', '描述画面… 输入 @ 引用素材'); prompt.setAttribute('aria-label', t('Prompt', '提示词'));
    prompt.disabled = linked(node, 'text'); prompt.oninput = () => set(node, 'text', prompt.value);
    const promptEditor = el('div', null, 'fv-prompt-editor'); promptEditor.append(prompt);
    const references = attachReferencePicker(prompt, {items: () => referenceItems(node), t, view, host: dialog,
        addMedia: () => { mediaDetails.open = true; mediaDetails.scrollIntoView({block: 'nearest', behavior: 'smooth'}); mediaMount.querySelector('button')?.focus(); }});
    const mention = button('@', () => references.open(), 'fv-reference-trigger');
    mention.title = t('Reference media (@)', '引用素材（@）'); mention.setAttribute('aria-label', mention.title);
    mention.disabled = prompt.disabled; mention.onpointerdown = e => e.preventDefault(); promptEditor.append(mention);
    cleanup.push(() => references.dispose());
    const promptChanged = e => { if (String(e.detail) === String(node.id)) { if (prompt.value !== value(node, 'text')) prompt.value = value(node, 'text') || ''; references.refresh(); } };
    window.addEventListener('freevideo-reference-prompt', promptChanged);
    cleanup.push(() => window.removeEventListener('freevideo-reference-prompt', promptChanged));
    section(t('Describe your scene', '描述画面')).append(promptEditor, promptGuide());
    const canvas = section(t('Frame & duration', '画幅与时长'));
    const shapes = el('div', null, 'fv-shapes'); canvas.append(shapes);
    const dimensionsLinked = linked(node, 'width') || linked(node, 'height');
    let selected = node.properties?.freevideo_aspect || 'custom', ratio = Number(value(node, 'width')) / Number(value(node, 'height'));
    const initialWidth = value(node, 'width'), initialHeight = value(node, 'height');
    if (selected === 'custom') {
        const match = [['16:9', 16 / 9], ['9:16', 9 / 16], ['1:1', 1]].find(([, r]) => Math.abs(Math.log(ratio / r)) < .035);
        if (match) selected = match[0];
    }
    // Preserve the requested pixel target, not the rounded canvas area. Using
    // the latter repeatedly shrinks a followed image after save/reload.
    let pixels = selected !== 'custom' && Number.isFinite(node.properties?.freevideo_pixels)
        ? node.properties.freevideo_pixels : (initialWidth * initialHeight) / 1024 ** 2;
    let disposed = false;
    const mp = el('select'); mp.setAttribute('aria-label', t('Total pixels', '总像素'));
    for (const [n, label] of [[.5, '0.50 MP'], [.75, '0.75 MP'], [.98, t('0.98 MP · Native', '0.98 MP · 原生')], [1, '1.00 MP'], [1.5, '1.50 MP'], [2, '2.00 MP']]) { const option = el('option', label); option.value = n; mp.append(option); }
    const nearest = [...mp.options].find(o => Math.abs(Number(o.value) - pixels) < .03);
    if (nearest) mp.value = nearest.value; else { const option = el('option', `${pixels.toFixed(2)} MP`); option.value = pixels; mp.append(option); mp.value = pixels; }
    mp.title = t('1 MP = 1024 × 1024, as in ComfyUI.', '1 MP = 1024 × 1024，与 ComfyUI 一致。'); mp.disabled = dimensionsLinked;
    const seconds = el('input'); seconds.type = 'number'; seconds.min = '1.625'; seconds.max = '60'; seconds.step = 'any'; seconds.value = value(node, 'seconds'); seconds.disabled = linked(node, 'seconds'); seconds.setAttribute('aria-label', t('Duration in seconds', '时长（秒）'));
    const fields = el('div', null, 'fv-fields'); fields.append(field(t('Total pixels', '总像素'), mp), field(t('Duration · seconds', '时长 · 秒'), seconds)); canvas.append(fields);
    const canvasNote = el('div', null, 'fv-canvas-note'), dimensions = el('span'), duration = el('span'); canvasNote.append(dimensions, duration); canvas.append(canvasNote);
    const twoPass = el('input'); twoPass.type = 'checkbox'; twoPass.checked = value(node, 'two_pass') !== false;
    twoPass.setAttribute('role', 'switch');
    twoPass.disabled = linked(node, 'two_pass') || !widget(node, 'two_pass');
    // Two-pass switched off only because first-pass steps left 8; returning to 8 restores it.
    let autoSinglePass = false;
    twoPass.onchange = () => { autoSinglePass = false; set(node, 'two_pass', twoPass.checked); syncSamplingSteps(); };
    const twoPassLabel = el('label', null, 'fv-two-pass');
    twoPassLabel.title = t('Generate at a lower resolution, then upscale and finish sampling at the target size to save time.', '先以低分辨率生成，再放大到目标分辨率完成采样，缩短生成时间。');
    twoPass.title = twoPassLabel.title;
    const twoPassStatus = el('small', '', 'fv-two-pass-status');
    twoPassLabel.append(el('span', t('Two-pass acceleration', '二次采样加速')), twoPassStatus, twoPass);
    let effortEstimate = {}, effortEstimateKey = '', effortEstimateTimer = null, effortEstimateRequest = null;
    const effort = createSamplingEffort(t, {onChange: tier => {
        baseSteps.value = tier.steps;
        refineSteps.value = 3;
        twoPass.checked = tier.twoPass;
        autoSinglePass = false;
        set(node, 'two_pass', tier.twoPass);
        set(node, 'refine_steps', 3);
        syncSamplingSteps();
    }, onPreview: tier => tier ? showEffortEstimate(tier.steps, tier.twoPass) : showEffortEstimate()});
    const samplingSettings = el('div', null, 'fv-sampling-settings');
    samplingSettings.append(effort.element, twoPassLabel);
    canvas.append(samplingSettings); cleanup.push(() => effort.dispose());
    cleanup.push(() => { clearTimeout(effortEstimateTimer); effortEstimateRequest?.abort(); });
    const mediaNode = upstream(node, 'media');
    const [mediaDetails, mediaMount] = expand(t('Media', '素材'));
    if (mediaNode?.freevideoCreateMediaEditor) { cleanup.push(mediaNode.freevideoCreateMediaEditor(mediaMount)); mediaDetails.open = JSON.parse(value(mediaNode, 'assets') || '[]').some(r => r.enabled !== false) || mediaNode.inputs?.some(input => ['first', 'last', 'reference', 'reference_audio'].includes(input.name) && input.link != null); }
    else mediaMount.append(el('p', t('Connect a FreeVideo Media panel in node view to upload here.', '在节点视图连接 FreeVideo 素材面板，即可在这里上传。'), 'fv-legacy'));
    const loraNode = upstream(node, 'loras');
    if (loraNode) {
    const [loraDetails, loraMount] = expand(t('LoRAs', 'LoRA 组合'));
    if (widget(loraNode, 'adapters')) { cleanup.push(loraPanel(loraNode, loraMount)); loraDetails.open = JSON.parse(value(loraNode, 'adapters') || '[]').length > 0; }
    else if (widget(loraNode, 'lora')) {
        const select = el('select'); select.setAttribute('aria-label', t('LoRA model', 'LoRA 模型'));
        for (const name of widget(loraNode, 'lora').options?.values || ['None']) { const o = el('option', name); o.value = name; select.append(o); }
        select.value = value(loraNode, 'lora'); select.onchange = () => set(loraNode, 'lora', select.value);
        const strength = el('input'); strength.type = 'number'; strength.min = '-4'; strength.max = '4'; strength.step = '.05'; strength.value = value(loraNode, 'strength'); strength.onchange = () => { if (strength.reportValidity()) set(loraNode, 'strength', Number(strength.value)); };
        loraMount.append(field(t('LoRA', 'LoRA'), select), field(t('Strength', '强度'), strength), el('p', loraWarning(), 'fv-lora-note'));
    } else loraMount.append(el('p', loraWarning(), 'fv-lora-note'));
    }
    const [, advanced] = expand(t('Advanced', '高级选项'));
    const custom = el('div', null, 'fv-fields');
    const width = el('input'), height = el('input');
    for (const [w, name, label] of [[width, 'width', t('Width', '宽度')], [height, 'height', t('Height', '高度')]]) { w.type = 'number'; w.min = '256'; w.max = '4096'; w.step = '32'; w.value = value(node, name); w.disabled = dimensionsLinked; w.setAttribute('aria-label', label); custom.append(field(label, w)); w.onchange = () => { if (w.value && w.reportValidity()) { set(node, name, Number(w.value)); selected = 'custom'; node.properties.freevideo_aspect = selected; ratio = Number(width.value) / Number(height.value); updateCanvas(); } }; }
    advanced.append(custom);
    const samplingFields = el('div', null, 'fv-fields'); samplingFields.style.marginTop = '14px';
    const baseSteps = el('input'), refineSteps = el('input');
    const samplingWarning = el('p', t('Changing sampling steps may reduce generation quality. Defaults: 8 + 3 steps.', '修改采样步数可能降低生成质量。默认一采 8 步、二采 3 步。'), 'fv-muted');
    samplingWarning.id = `fv-sampling-warning-${node.id}`;
    samplingWarning.style.color = 'var(--fv-warning)';
    for (const [input, name, label, fallback, maximum] of [
        [baseSteps, 'base_steps', t('First-pass steps', '一采步数'), 8, 32],
        [refineSteps, 'refine_steps', t('Second-pass steps', '二采步数'), 3, 31],
    ]) {
        input.type = 'number'; input.min = '1'; input.max = String(maximum); input.step = '1'; input.required = true;
        input.value = value(node, name) ?? fallback;
        input.setAttribute('aria-label', label); input.setAttribute('aria-describedby', samplingWarning.id);
        input.oninput = () => syncSamplingSteps();
        input.onchange = () => input.reportValidity();
        samplingFields.append(field(label, input));
    }
    // Apply the two-pass rule to committed, valid values only, never while typing.
    baseSteps.onchange = () => {
        // Before validation: one step is valid only once two-pass is off.
        const steps = Number(baseSteps.value);
        if (baseSteps.value !== '' && Number.isInteger(steps) && steps >= 1 && steps <= 32
                && !linked(node, 'two_pass') && widget(node, 'two_pass')) {
            if (steps !== 8 && twoPass.checked) { twoPass.checked = false; set(node, 'two_pass', false); autoSinglePass = true; }
            else if (steps === 8 && autoSinglePass) { twoPass.checked = true; set(node, 'two_pass', true); autoSinglePass = false; }
        }
        syncSamplingSteps();
        baseSteps.reportValidity();
    };
    function syncSamplingSteps() {
        const baseLinked = linked(node, 'base_steps');
        // Two-pass is offered at 8 first-pass steps. A saved workflow that already
        // uses it with other steps is shown as custom and left unchanged.
        const unavailable = Number(baseSteps.value) !== 8 && !twoPass.checked;
        twoPass.disabled = unavailable || linked(node, 'two_pass') || !widget(node, 'two_pass');
        twoPassStatus.textContent = unavailable ? t('Coming soon', '即将推出') : '';
        twoPassLabel.classList.toggle('fv-two-pass-unavailable', unavailable);
        baseSteps.disabled = baseLinked || !widget(node, 'base_steps');
        refineSteps.disabled = !twoPass.checked || linked(node, 'refine_steps') || !widget(node, 'refine_steps');
        baseSteps.min = twoPass.checked ? '2' : '1';
        refineSteps.max = baseLinked ? '31' : String(Math.max(3, Math.min(31, Number(baseSteps.value || 8) - 1)));
        refineSteps.setCustomValidity(twoPass.checked && !baseLinked && !refineSteps.disabled && Number(refineSteps.value) !== 3 && Number(refineSteps.value) >= Number(baseSteps.value)
            ? t('Other second-pass counts must be fewer than first-pass steps.', '其他二采步数需小于一采步数。') : '');
        refineSteps.title = twoPass.checked ? t('Three steps use the independent refinement schedule.', '3 步使用独立二采时间表。')
            : t('Turn on two-pass acceleration to use this setting.', '开启二次采样加速后生效。');
        for (const [input, name] of [[baseSteps, 'base_steps'], [refineSteps, 'refine_steps']]) {
            if (!input.disabled && input.checkValidity() && Number(input.value) !== value(node, name)) {
                set(node, name, Number(input.value));
            }
        }
        effort.update({baseSteps: Number(baseSteps.value), refineSteps: Number(refineSteps.value),
            twoPass: twoPass.checked, disabled: baseSteps.disabled || linked(node, 'two_pass') || linked(node, 'refine_steps')});
        refreshEffortEstimate();
    }
    syncSamplingSteps();
    advanced.append(samplingFields, samplingWarning);
    function showEffortEstimate(steps = Number(baseSteps.value), twoPassPlan = twoPass.checked) {
        const mode = twoPassPlan ? 'two_pass' : 'single';
        const estimate = effortEstimate[mode]?.[steps];
        // A previewed level always refines three steps; the current plan may not.
        if (estimate?.status !== 'estimated' || (mode === 'two_pass' && !arguments.length && Number(refineSteps.value) !== 3)) {
            effort.setEstimate('', t('Estimates appear after a matching generation on this computer.', '本机有相近生成记录后显示预计耗时。'));
            return;
        }
        const minutes = estimate.high_seconds >= 90, scale = minutes ? 60 : 1;
        const low = Math.max(1, Math.floor(estimate.low_seconds / scale));
        const high = Math.max(low + 1, Math.ceil(estimate.high_seconds / scale));
        const unit = minutes ? t('min', '分钟') : t('s', '秒');
        const extra = estimate.includes_text_encoding ? '' : t(' + prompt', ' + 提示词准备');
        effort.setEstimate(`${t('~', '约 ')}${low}–${high} ${unit}${extra}`,
            t('Based on complete local runs. Includes loading and decoding; first use or other applications may take longer.',
                '根据本机完整生成记录估算，包含加载与解码；首次准备或其他程序占用可能延长时间。'));
    }
    function refreshEffortEstimate(force = false) {
        let task = 'external';
        try {
            if (!linked(node, 'conditioning')) {
                const media = referenceItems(node), refs = media.filter(r => !r.role);
                const audio = refs.some(r => r.kind === 'audio'), visual = refs.some(r => r.kind !== 'audio');
                const first = media.some(r => r.role === 'first'), last = media.some(r => r.role === 'last');
                task = audio ? visual ? 'ref2va_av' : 'ref2va_audio' : visual ? 'ref2va'
                    : first && last ? 'fl2va' : first ? 'i2va' : last ? 'l2va' : 't2va';
            }
        } catch {} // Unknown connected inputs must not reuse text-only timings.
        let adapters = false;
        try { adapters = JSON.parse(value(loraNode, 'adapters') || '[]').some(r => r.enabled !== false && Number(r.strength ?? 1) !== 0); } catch {}
        if (loraNode && !widget(loraNode, 'adapters')) adapters = value(loraNode, 'lora') !== 'None';
        const query = new URLSearchParams({width: value(node, 'width'), height: value(node, 'height'), seconds: value(node, 'seconds'), task, adapters: adapters ? '1' : '0'}).toString();
        if (query === effortEstimateKey && !force) { showEffortEstimate(); return; }
        clearTimeout(effortEstimateTimer);
        effortEstimateKey = query; effortEstimate = {}; showEffortEstimate(); effortEstimateRequest?.abort();
        effortEstimateTimer = setTimeout(async () => {
            const controller = new AbortController(); effortEstimateRequest = controller;
            try {
                const reply = await api.fetchApi('/freevideo/sampling-estimate?' + query, {signal: controller.signal});
                if (!reply.ok) return;
                const estimate = await reply.json();
                if (disposed || controller.signal.aborted || effortEstimateKey !== query) return;
                effortEstimate = estimate; showEffortEstimate();
            } catch {} // An unavailable estimate must not block the editor.
        }, 220);
    }
    const seedFields = el('div', null, 'fv-fields'); seedFields.style.marginTop = '14px';
    const seed = el('input'); seed.type = 'number'; seed.min = '0'; seed.max = String(2 ** 53 - 1); seed.step = '1'; seed.value = value(node, 'seed'); seed.disabled = linked(node, 'seed'); seed.setAttribute('aria-label', t('Seed', '种子'));
    seed.onchange = () => { if (seed.value && seed.reportValidity()) set(node, 'seed', Number(seed.value)); };
    const mode = el('select'); for (const [v, label] of [['randomize', t('New each run', '每次随机')], ['fixed', t('Keep fixed', '保持固定')], ['increment', t('Increment', '递增')], ['decrement', t('Decrement', '递减')]]) { const o = el('option', label); o.value = v; mode.append(o); }
    mode.value = value(node, 'control_after_generate') || 'fixed'; mode.onchange = () => set(node, 'control_after_generate', mode.value);
    seedFields.append(field(t('Seed', '种子'), seed), field(t('After generation', '生成后'), mode)); advanced.append(seedFields);
    const previewHead = el('div', null, 'fv-preview-head'); previewHead.append(el('strong', t('Preview', '预览')));
    const previewCaption = el('span'), previewTools = el('div', null, 'fv-preview-tools');
    previewTools.append(previewCaption, button(t('Creations', '作品'), () => {
        stage.querySelector('video')?.pause(); openLibrary(t);
    })); previewHead.append(previewTools); output.append(previewHead);
    const previewSpace = el('div', null, 'fv-preview-space'); output.append(previewSpace);
    const stage = el('div', null, 'fv-stage');
    stage.setAttribute('aria-label', t('Video preview', '视频预览'));
    const stageMedia = el('div', null, 'fv-stage-media');
    stage.append(stageMedia); previewSpace.append(stage);
    const progress = createGenerationProgress(t, undefined, {compact: true, api}); stage.append(progress.element);
    progress.updateReport({report_id: node.freevideoReportId});
    cleanup.push(() => progress.dispose());
    cleanup.push(createPreviewScene(stage, progress.element, stageMedia));
    let revealTimer = null;
    function hideProgress() {
        clearTimeout(revealTimer); revealTimer = null;
        delete stage.dataset.revealing; progress.hide();
    }
    cleanup.push(hideProgress);
    let previewRatio = Number(initialWidth) / Number(initialHeight);
    function fitPreview() {
        const {width, height} = previewSpace.getBoundingClientRect();
        if (!width || !height || !Number.isFinite(previewRatio) || previewRatio <= 0) return;
        const fittedWidth = Math.min(width, height * previewRatio);
        stage.style.width = `${fittedWidth}px`; stage.style.height = `${fittedWidth / previewRatio}px`;
        stage.dataset.short = String(fittedWidth / previewRatio < 150);
    }
    function previewSize(w, h) {
        if (!(w > 0 && h > 0)) return;
        previewRatio = w / h;
        dimensionText(previewCaption, `${w} × ${h}`);
        fitPreview();
    }
    function dimensionText(element, text) {
        if (element.textContent === text) return;
        const animate = element.textContent && !matchMedia('(prefers-reduced-motion: reduce)').matches;
        element.textContent = text;
        if (animate && element.animate) {
            for (const animation of element.getAnimations()) animation.cancel();
            element.animate([{opacity: .25, transform: 'translateY(3px)'}, {opacity: 1, transform: 'translateY(0)'}],
                {duration: 280, easing: 'ease-out'});
        }
    }
    const previewObserver = new ResizeObserver(fitPreview);
    previewObserver.observe(previewSpace); cleanup.push(() => previewObserver.disconnect());
    function showProgress(message) {
        reuseRow.hidden = true;
        stats.hidden = true;
        budget.textContent = '';
        links.replaceChildren();
        prewarm.textContent = '';
        clearTimeout(revealTimer); revealTimer = null; delete stage.dataset.revealing;
        if (progress.element.hidden) stage.querySelector('video')?.pause();
        progress.update(message);
    }
    const status = el('div', '', 'fv-status'); status.setAttribute('role', 'status');
    const stats = el('div', null, 'fv-stats'), budget = el('div', '', 'fv-budget'), links = el('div', null, 'fv-result-links');
    stats.hidden = true;
    const metrics = [], metricLabels = [];
    for (const label of [t('Sampling', '采样耗时'), t('Request total', '请求总计'), t('VRAM peak', '显存峰值'), t('RAM peak', '内存峰值')]) { const box = el('div', null, 'fv-stat'), n = el('strong', '—'), caption = el('span', label); box.append(n, caption); stats.append(box); metrics.push(n); metricLabels.push(caption); }
    const prewarm = el('div', node.freevideoPrewarm || '', 'fv-prewarm');
    const reuseRow = el('div', null, 'fv-reuse-result'); reuseRow.hidden = true;
    const reuseNotice = el('span', t('Reused previous result', '已复用上次结果'));
    const regenerate = button(t('Regenerate', '重新生成'), async () => {
        const saved = result;
        regenerate.disabled = true;
        try { await node.freevideoRegenerateResult(saved); regenerate.textContent = t('Queued', '已加入队列'); await syncQueue(); }
        catch (error) { regenerate.disabled = false; status.dataset.error = 'true'; status.textContent = error.message; }
    }, 'fv-quiet');
    reuseRow.append(reuseNotice, regenerate);
    output.append(status, reuseRow, stats, budget, links, progress.report, prewarm);
    const failure = createErrorPanel(t); output.append(failure.element);
    if (node.freevideoFailure) failure.show(node.freevideoFailureReport || node.freevideoFailure, false);
    let result = node.freevideoLastResult || app.nodeOutputs?.[node.id]?.freevideo_summary?.[0];
    function showResult(r) {
        if (!r?.video || disposed) return; result = r;
        refreshEffortEstimate(true);
        progress.updateReport({report_id: node.freevideoReportId});
        stage.querySelector('video')?.pause(); stageMedia.replaceChildren();
        const video = el('video'); video.src = view(r.video, 'output'); video.controls = true; video.preload = 'metadata'; video.playsInline = true; stageMedia.append(video);
        stats.hidden = !!r.result_cache_hit;
        reuseRow.hidden = !r.result_cache_hit;
        regenerate.disabled = false; regenerate.textContent = t('Regenerate', '重新生成');
        const unified = r.memory_model === 'unified';
        metricLabels[2].textContent = unified ? t('Unified memory', '统一内存总量') : t('VRAM peak', '显存峰值');
        metricLabels[3].textContent = unified ? t('Process RAM peak', '进程内存峰值') : t('RAM peak', '内存峰值');
        const shown = [number(r.sample_seconds), number(r.request_seconds), number(unified ? r.unified_total_bytes : r.vram_peak_bytes, 2 ** 30, 'GiB'), number(r.ram_peak_bytes, 2 ** 30, 'GiB')]; metrics.forEach((e, i) => e.textContent = shown[i]);
        budget.textContent = r.result_cache_hit ? '' : unified
            ? (Number.isFinite(r.unified_reserve_bytes) ? `${t('Reserved unified memory', '预留统一内存')} ${number(r.unified_reserve_bytes, 2 ** 30, 'GiB')}` : '')
            : (Number.isFinite(r.gpu_budget_bytes) ? `${t('VRAM budget', '可用显存预算')} ${number(r.gpu_budget_bytes, 2 ** 30, 'GiB')} · ${t('Device', '显卡总量')} ${number(r.gpu_total_bytes, 2 ** 30, 'GiB')}` : '');
        links.replaceChildren();
        for (const [label, file, cls] of [[t('Download video', '下载视频'), r.video, 'fv-primary'], [t('Report', '查看报告'), r.report, 'fv-quiet']]) { if (!file) continue; const a = el('a', label, cls); a.href = outputDownloadURL(api, file); a.download = file === r.video ? '' : file.split('/').pop(); links.append(a); }
        links.append(shareButton(r, t));
        const g = r.geometry; if (g?.width && g?.height) previewSize(g.width, g.height);
        if (!progress.element.hidden) {
            progress.update({phase: 'complete', overall: {status: 'complete', fraction: 1, remaining_seconds: 0}});
            stage.dataset.revealing = 'true';
            clearTimeout(revealTimer); revealTimer = setTimeout(hideProgress, 550);
        }
    }
    const runbar = el('div', null, 'fv-runbar');
    let busy = false, queued = false, cancelling = false, activePrompt = null, capturing = false;
    let runMode = 'single', queueState;
    if (!studioQueues.has(node)) studioQueues.set(node, createStudioQueue(api, node.id, {
        formatError: (error, snapshot) => createErrorReport(error, {stage: 'submission', snapshot, node, graph: app.graph}),
        // Direct snapshot submission still feeds the same graph result panel.
        onResult: (output, requestId) => {
            output = {...output, freevideo_summary: [{...output.freevideo_summary[0], request_id: requestId}]};
            if (app.graph?.getNodeById(node.id) !== node) { node.freevideoLastResult = output.freevideo_summary[0]; return; }
            app.nodeOutputs = {...app.nodeOutputs, [node.id]: output};
            if (node.freevideoLastResult?.request_id !== requestId) node.freevideoShowResult?.(output);
        },
    }));
    const queueController = studioQueues.get(node);
    if (!node.freevideoQueueCleanup) {
        node.freevideoQueueCleanup = true;
        const removed = node.onRemoved;
        node.onRemoved = function (...args) {
            queueController.stopLoop().catch(() => {}).finally(() => queueController.dispose());
            return removed?.apply(this, args);
        };
    }
    const runrow = el('div', null, 'fv-runrow');
    const runOptions = el('details', null, 'fv-run-options');
    const optionsToggle = el('summary'); optionsToggle.title = t('Generation mode', '生成方式');
    optionsToggle.setAttribute('aria-label', optionsToggle.title);
    optionsToggle.append(el('span', null, 'fv-chevron'));
    const optionsPanel = el('div', null, 'fv-run-menu');
    optionsPanel.append(el('strong', t('Generation mode', '生成方式')));
    const modeButtons = [];
    for (const [id, label] of [['single', t('One video', '单次生成')], ['batch', t('Batch', '批量生成')], ['loop', t('Loop', '循环生成')]]) {
        const b = button(label, () => { runMode = id; updateRunButton(); });
        b.dataset.mode = id; modeButtons.push(b); optionsPanel.append(b);
    }
    const count = el('input'); count.type = 'number'; count.min = '2'; count.max = '100'; count.step = '1'; count.value = '4';
    count.setAttribute('aria-label', t('Number of videos', '生成数量'));
    const countField = field(t('Number of videos', '生成数量'), count); optionsPanel.append(countField);
    const modeHelp = el('p', '', 'fv-muted'); optionsPanel.append(modeHelp);
    count.oninput = () => updateRunButton();
    runOptions.append(optionsToggle, optionsPanel);
    const generate = button('', submitDraft, 'fv-primary');
    runrow.append(generate, runOptions);
    const queueActions = el('div', null, 'fv-queue-actions');
    const queueDetails = el('details', null, 'fv-queue-details');
    const queueToggle = el('summary'), queueList = el('div', null, 'fv-queue-list');
    queueDetails.append(queueToggle, queueList);
    const cancelCurrent = button(t('Cancel current', '取消当前'), cancelGeneration, 'fv-quiet');
    const stopLoop = button(t('Stop after this video', '完成当前后停止'), async () => {
        stopLoop.disabled = true;
        try { await queueController.stopLoop(); }
        catch (error) { showQueueError(error); }
        finally { stopLoop.disabled = false; }
    }, 'fv-quiet');
    queueActions.append(queueDetails, cancelCurrent, stopLoop);
    runbar.append(runrow, queueActions); controls.append(runbar);
    const queueError = error => ({
        queue_unavailable: t('Could not read the queue. Any loop has been stopped; already submitted videos stay queued.', '暂时无法读取队列。循环已停止追加，已提交的任务仍在队列中。'),
        cancel_failed: t('Could not cancel. Try again.', '暂时未能取消，请重试。'),
        submit_failed: t('The workflow was not queued. Check the node settings.', '工作流未能提交，请检查节点设置。'),
        loop_active: t('Stop the current loop before starting another.', '请先停止当前循环。'),
        loop_stopped: t('The last request did not complete. The loop has stopped.', '上一条任务未完成，循环已停止。'),
        linked_seed: t('Use the seed in this panel for batch or loop generation.', '批量和循环生成需使用面板内的种子，请先断开种子输入的连接。'),
        missing_node: t('This node is disabled or no longer in the workflow.', '该节点已禁用或已从工作流移除。'),
    }[error?.message] || error?.message || String(error));
    let lastQueueError = null;
    function showQueueError(error) {
        status.dataset.error = 'true';
        // Polling and loop shutdown must not replace the original exception
        // with a generic "loop stopped" notice or reopen a dismissed dialog.
        if (['loop_stopped', 'queue_unavailable'].includes(error?.message)
            && node.freevideoFailureReport && node.freevideoFailureReport.stage !== 'queue') return;
        status.textContent = error?.schema === 'freevideo.error-report'
            ? t('Submission failed. See the details below.', '提交失败，具体原因见下方。') : queueError(error);
        const identity = error?.schema === 'freevideo.error-report' ? error : error?.message || error;
        if (lastQueueError === identity) return;
        lastQueueError = identity;
        const report = failure.show(error, error?.schema === 'freevideo.error-report', {stage: 'queue', node, graph: app.graph});
        node.freevideoFailureReport = report; node.freevideoFailure = errorText(report);
    }
    function updateRunButton() {
        const submitting = capturing || queueState?.submitting;
        generate.disabled = submitting || (runMode === 'loop' && queueState?.looping);
        generate.textContent = submitting ? t('Adding…', '正在添加…')
            : runMode === 'batch' ? t(`Add ${count.value || '…'} videos`, `添加 ${count.value || '…'} 条任务`)
            : runMode === 'loop' ? (queueState?.looping ? t('Looping', '循环中') : t('Start loop', '开始循环'))
            : busy ? t('Add to queue', '添加到队列') : t('Generate video', '生成视频');
        for (const b of modeButtons) b.setAttribute('aria-pressed', String(b.dataset.mode === runMode));
        countField.hidden = runMode !== 'batch'; count.disabled = runMode !== 'batch';
        modeHelp.hidden = runMode === 'single';
        modeHelp.textContent = runMode === 'loop'
            ? t('Use these settings with a new seed each time. Closing this page stops adding videos.', '固定当前设置，每条使用不同种子。关闭网页后不再追加。')
            : t('Use these settings with a different seed for each video.', '固定当前设置，每条使用不同种子。');
        cancelCurrent.hidden = !queueState?.running;
        cancelCurrent.disabled = cancelling;
        cancelCurrent.textContent = cancelling ? t('Cancelling…', '正在取消…') : t('Cancel current', '取消当前');
        stopLoop.hidden = !queueState?.looping;
    }
    function renderQueue(state) {
        if (disposed) return;
        queueState = state;
        const active = state.running || state.pending[0];
        activePrompt = active?.id || null;
        busy = Boolean(active); queued = !state.running && Boolean(state.pending.length);
        const g = active?.inputs;
        // The right-hand frame belongs to the submitted video. Draft edits on
        // the left must never reshape a video currently being generated.
        if (Number.isFinite(g?.width) && Number.isFinite(g?.height)) previewSize(g.width, g.height);
        queueToggle.textContent = t(`Queue · ${state.pending.length}`, `等待 ${state.pending.length} 条`);
        queueDetails.hidden = state.pending.length === 0;
        queueActions.hidden = !busy && !state.looping;
        const signature = JSON.stringify(state.pending.map(row => [row.id, row.inputs.width, row.inputs.height]));
        if (queueList.dataset.signature !== signature) {
            queueList.dataset.signature = signature; queueList.replaceChildren();
            state.pending.forEach((row, index) => {
                const item = el('div', null, 'fv-queue-item');
                const description = el('span', `${index + 1} · ${row.inputs.width} × ${row.inputs.height} · ${row.inputs.seconds} s`);
                const remove = button(t('Remove', '移除'), async () => {
                    remove.disabled = true;
                    try { await queueController.cancel(row.id); }
                    catch (error) { showQueueError(error); remove.disabled = false; }
                }, 'fv-quiet');
                remove.setAttribute('aria-label', t(`Remove queued video ${index + 1}`, `移除第 ${index + 1} 条排队任务`));
                item.append(description, remove); queueList.append(item);
            });
        }
        if (!busy) cancelling = false;
        if (state.error) showQueueError(state.error);
        updateRunButton();
    }
    const syncQueue = () => queueController.refresh();
    async function cancelGeneration() {
        if (cancelling) return;
        try {
            cancelling = true; updateRunButton();
            await syncQueue();
            const id = queueController.state().running?.id;
            if (!id) { cancelling = false; updateRunButton(); return; }
            cancelling = true; updateRunButton();
            const reply = await queueController.cancel(id);
            status.dataset.error = 'false';
            status.textContent = reply.status === 'cancelling' ? t('Stopping generation…', '正在停止生成…') : '';
        } catch (error) { cancelling = false; updateRunButton(); showQueueError(error); }
    }
    async function submitDraft() {
        if (capturing || queueState?.submitting) return;
        // Ctrl+Enter leaves focus in a field: commit its edit (change fires on blur) first.
        const editing = dialog.contains(document.activeElement) ? document.activeElement : null;
        if (editing && editing !== generate) { editing.blur(); editing.focus({preventScroll: true}); }
        if ([...dialog.querySelectorAll('input:not(:disabled)')].some(e => !e.reportValidity())) return;
        if (!prompt.disabled && !prompt.value.trim()) { prompt.focus(); status.textContent = t('Describe your scene first.', '请先描述画面。'); return; }
        const chosenMode = runMode, batchCount = chosenMode === 'batch' ? Number(count.value) : 1;
        const originalSeed = value(node, 'seed'), seedMode = mode.value;
        try {
            lastQueueError = null; failure.clear(); node.freevideoFailure = ''; node.freevideoFailureReport = null;
            capturing = true; updateRunButton(); status.dataset.error = 'false'; runOptions.open = false;
            // Only serialization briefly locks the editor. Network submission
            // and all GPU execution leave the next draft fully editable.
            for (const section of controls.querySelectorAll('.fv-section')) section.inert = true;
            if (selected === 'input' && !dimensionsLinked) await inputRatio(true);
            const snapshot = structuredClone(await app.graphToPrompt());
            const seedIndex = node.widgets.findIndex(w => w.name === 'seed');
            capturing = false;
            for (const section of controls.querySelectorAll('.fv-section')) section.inert = false;
            await queueController.start(snapshot, {count: batchCount, repeat: chosenMode === 'loop', seedIndex});
            // Preserve draft changes made while the POST was in flight.
            if (chosenMode === 'single' && !linked(node, 'seed') && value(node, 'seed') === originalSeed) {
                const max = Number.MAX_SAFE_INTEGER;
                const next = seedMode === 'randomize' ? randomSeed() : seedMode === 'increment'
                    ? (originalSeed >= max ? 0 : originalSeed + 1) : seedMode === 'decrement'
                    ? (originalSeed <= 0 ? max : originalSeed - 1) : originalSeed;
                set(node, 'seed', next); seed.value = next;
            }
            if (!queueController.state().error) status.textContent = '';
        } catch (error) { showQueueError(createErrorReport(error, {stage: 'submission', node, graph: app.graph})); }
        finally {
            capturing = false;
            for (const section of controls.querySelectorAll('.fv-section')) section.inert = false;
            updateRunButton();
        }
    }
    cleanup.push(queueController.subscribe(renderQueue));
    const closeMenus = event => {
        if (!runOptions.contains(event.target)) runOptions.open = false;
        if (!queueDetails.contains(event.target)) queueDetails.open = false;
    };
    dialog.addEventListener('pointerdown', closeMenus);
    function updateCanvas() {
        width.value = value(node, 'width'); height.value = value(node, 'height');
        dimensionText(dimensions, dimensionsLinked ? t('Size from connected nodes', '尺寸来自已连接节点') : `${width.value} × ${height.value}`);
        const requested = Math.ceil(Number(seconds.value) * 24), frames = requested + ((5 - requested) % 17 + 17) % 17;
        duration.textContent = Number.isFinite(frames) ? `${(frames / 24).toFixed(3)} s · 24 fps` : '—';
        for (const b of shapes.children) b.setAttribute('aria-pressed', String(b.dataset.ratio === selected));
        const shapeIndex = [...shapes.children].findIndex(b => b.dataset.ratio === selected);
        shapes.style.setProperty('--fv-shape-index', Math.max(0, shapeIndex));
        shapes.dataset.custom = String(shapeIndex < 0);
        if (!busy) previewSize(Number(width.value), Number(height.value));
        refreshEffortEstimate();
    }
    function applySize() {
        if (dimensionsLinked) return;
        const total = pixels * 1024 ** 2;
        const w = Math.round(Math.sqrt(total * ratio) / 32) * 32, h = Math.round(Math.sqrt(total / ratio) / 32) * 32;
        if (w < 256 || h < 256 || w > 4096 || h > 4096) throw new Error(t('This aspect ratio and pixel count exceed the supported dimensions. Use custom dimensions.', '该比例与像素数超出尺寸范围，请使用自定义宽高。'));
        set(node, 'width', w); set(node, 'height', h); node.properties.freevideo_aspect = selected; node.properties.freevideo_pixels = pixels; updateCanvas();
    }
    async function inputRatio(required = false) {
        const rows = mediaNode ? JSON.parse(value(mediaNode, 'assets') || '[]') : [];
        const active = rows.filter(r => r.enabled !== false);
        const source = active.find(r => r.role === 'first') || active.find(r => r.role === 'last') || active.find(r => /\.(png|jpe?g|webp|bmp|tiff?)$/i.test(r.file));
        if (!source) { if (required) throw new Error(t('Add an image in Media to follow its aspect ratio.', '请先在素材中添加图片，再跟随输入比例。')); return; }
        const image = new Image(); image.src = view(source.file);
        await image.decode();
        if (disposed || !image.naturalWidth || !image.naturalHeight) return;
        if (selected === 'input') { ratio = image.naturalWidth / image.naturalHeight; applySize(); }
    }
    for (const [id, label, r] of [['16:9', t('Landscape', '横屏'), 16 / 9], ['9:16', t('Portrait', '竖屏'), 9 / 16], ['1:1', t('Square', '方形'), 1], ['input', t('Match image', '跟随图片'), null]]) {
        const b = button('', async () => { const previous = selected; try { selected = id; pixels = Number(mp.value); if (id === 'input') await inputRatio(true); else { ratio = r; applySize(); } status.textContent = ''; status.dataset.error = 'false'; } catch (error) { selected = previous; updateCanvas(); status.dataset.error = 'true'; status.textContent = error.message; } }, 'fv-shape');
        b.dataset.ratio = id; b.setAttribute('aria-label', label); b.disabled = dimensionsLinked;
        b.append(el('span', null, 'fv-shape-icon'), el('span', label)); shapes.append(b);
    }
    mp.onchange = () => { try { pixels = Number(mp.value); applySize(); } catch (error) { status.dataset.error = 'true'; status.textContent = error.message; } };
    seconds.onchange = () => { if (seconds.value && seconds.reportValidity()) { set(node, 'seconds', Number(seconds.value)); updateCanvas(); } };
    const mediaChanged = async e => { if (String(e.detail) !== String(mediaNode?.id)) return; refreshEffortEstimate(); if (selected !== 'input') return; try { await inputRatio(); } catch (error) { status.textContent = error.message; } };
    const lorasChanged = e => { if (String(e.detail) === String(loraNode?.id)) refreshEffortEstimate(); };
    window.addEventListener('freevideo-loras', lorasChanged);
    cleanup.push(() => window.removeEventListener('freevideo-loras', lorasChanged));
    const resultChanged = e => { if (String(e.detail.node) === String(node.id)) { failure.clear(); showResult(e.detail.value); cancelling = false; status.textContent = ''; syncQueue().catch(() => {}); } };
    const prewarmChanged = e => { if (String(e.detail.node) === String(node.id)) prewarm.textContent = e.detail.label; };
    window.addEventListener('freevideo-prewarm', prewarmChanged);
    cleanup.push(() => window.removeEventListener('freevideo-prewarm', prewarmChanged));
    window.addEventListener('freevideo-media', mediaChanged); window.addEventListener('freevideo-result', resultChanged);
    cleanup.push(() => { window.removeEventListener('freevideo-media', mediaChanged); window.removeEventListener('freevideo-result', resultChanged); });
    const listen = (type, handler) => { api.addEventListener(type, handler); cleanup.push(() => api.removeEventListener(type, handler)); };
    listen('status', () => { syncQueue().catch(() => {}); });
    listen('executing', e => { const id = typeof e.detail === 'object' ? e.detail?.node : e.detail; if (String(id) === String(node.id)) { failure.clear(); node.freevideoFailure = ''; node.freevideoFailureReport = null; busy = true; queued = false; updateRunButton(); showProgress(node.freevideoProgress || {label: t('Preparing your video', '正在准备视频'), reset: true, new_request: true}); status.textContent = ''; } syncQueue().catch(() => {}); });
    const progressChanged = e => {
        if (String(e.detail.node) !== String(node.id)) return;
        progress.updateReport(e.detail);
        if (['failed', 'cancelled'].includes(e.detail.phase) || e.detail.result) {
            busy = cancelling = false; updateRunButton();
            if (e.detail.phase === 'cancelled') { hideProgress(); status.textContent = t('Cancelled', '已取消'); }
            else if (e.detail.phase === 'failed') { hideProgress(); status.textContent = t('Generation stopped', '生成已停止'); }
            else if (revealTimer === null) hideProgress();
            return;
        }
        busy = true; queued = false; updateRunButton(); status.dataset.error = 'false';
        if (cancelling) return;
        status.textContent = '';
        showProgress(e.detail);
    };
    window.addEventListener('freevideo-progress', progressChanged);
    cleanup.push(() => window.removeEventListener('freevideo-progress', progressChanged));
    listen('execution_error', e => {
        const seen = new Set(), pending = [node];
        while (pending.length) {
            const currentNode = pending.pop(); if (!currentNode || seen.has(String(currentNode.id))) continue;
            seen.add(String(currentNode.id));
            for (const input of currentNode.inputs || []) pending.push(upstream(currentNode, input.name));
        }
        if (!seen.has(String(e.detail?.node_id))) return;
        busy = cancelling = false; updateRunButton(); hideProgress(); status.dataset.error = 'true';
        node.freevideoFailureReport = failure.show(e.detail, true, {stage: 'execution', node, graph: app.graph, progress: node.freevideoProgress});
        node.freevideoFailure = errorText(node.freevideoFailureReport);
        status.textContent = t('Generation stopped. See the explanation below.', '生成已停止，原因和处理建议见下方。');
    });
    listen('execution_interrupted', e => {
        if (activePrompt ? e.detail?.prompt_id !== activePrompt : String(e.detail?.node_id) !== String(node.id)) return;
        busy = cancelling = false; activePrompt = null; updateRunButton(); hideProgress();
        failure.clear(); node.freevideoFailure = ''; node.freevideoFailureReport = null; status.dataset.error = 'false'; status.textContent = t('Cancelled', '已取消');
    });
    const escape = e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); generate.click(); } }; dialog.addEventListener('keydown', escape);
    dialog.addEventListener('cancel', e => { e.preventDefault(); closeDialog(dialog); });
    dialog.onclose = () => { disposed = true; for (const f of cleanup) f(); stage.querySelector('video')?.pause(); dialog.remove(); if (current?.dialog === dialog) { current = null; viewChanged('nodes', node); } };
    document.body.append(dialog); dialog.showModal(); viewChanged('studio', node); updateCanvas();
    if (node.freevideoProgress) showProgress(node.freevideoProgress);
    else if (result) showResult(result);
    // Do not change any saved width/height simply by opening a workflow.
    syncQueue().then(async () => {
        if (disposed) return;
        if (busy) { showProgress(node.freevideoProgress || {label: queued ? t('Waiting in queue', '正在排队') : t('Syncing generation progress', '正在同步生成进度')}); }
        else if (!result) {
            const saved = await latestVideo();
            if (!disposed && !busy && !result && saved) showResult(saved);
        }
    }).catch(() => {});
    requestAnimationFrame(() => generate.focus({preventScroll: true}));
}
