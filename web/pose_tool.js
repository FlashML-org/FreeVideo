// The sketch editor's pose tool (人偶 / Mannequin): mannequins on a layer of their own under the strokes. This module and
// three.js load only when the tool is chosen, or when a sketch that has mannequins opens again. The scene itself is JSON
// in the layer (pose_stage.js), so every change is an ordinary editor step (undo, redo, saving). Here: the options row
// and its attached windows, the pointer (drag a part to pose it, drag elsewhere to turn the camera all the way round),
// and an overlay that shows the part under the pointer, the selected one and its rings.
import * as THREE from './vendor/three/three.module.min.js';
import {TransformControls, HIGHLIGHT} from './vendor/three/TransformControls.js';
import {createStage, emptyScene, PRESETS, CAMERA_PRESETS, MAX_FIGURES} from './pose_stage.js';
import {HAND_SHAPES} from './pose_body.js';
import {PROPS} from './pose_props.js';
import * as commands from './image_commands.js';
import {listenDialogKey} from './dialog_keys.js';

const css = document.createElement('link');
css.rel = 'stylesheet'; css.href = new URL('./pose_tool.css', import.meta.url).href;
document.head.append(css);

const TORSO = new Set(['root', 'spine', 'chest']);
const sideOf = joint => (/L$/.test(joint || '') ? 'L' : /R$/.test(joint || '') ? 'R' : null);
export const poseLayerOf = doc => doc?.layers?.find(l => l.kind === 'pose') || null;

// editor: {t, dialog, host (the stage element), el, button, iconButton, doc(), preview(doc), commit(doc), sheetRect(),
//   renderOptions(), message(text)}.
export function createPoseTool(editor) {
    const {t, dialog, host, el, button, iconButton} = editor;
    const stage = createStage();
    let sel = null, hover = null, active = false, pop = null, delButton = null;

    // ---- the overlay: drawn with the shot camera seen through the stage, so it lines up with the layer under it
    const canvas = document.createElement('canvas'); canvas.className = 'fv-pose-overlay'; canvas.hidden = true;
    host.querySelector('.fv-ie-ants')?.after(canvas) ?? host.append(canvas);
    const camera = new THREE.PerspectiveCamera();
    const gizmoScene = new THREE.Scene();
    let gl = null, gizmo = null, tint = null, glow = null, depth = null, frame = 0, size = '';
    function accent() {
        const probe = document.createElement('span'); probe.style.color = 'var(--fv-accent)'; probe.hidden = true; dialog.append(probe);
        const value = getComputedStyle(probe).color; probe.remove();
        const [r, g, b] = (value.match(/\d+(\.\d+)?/g) || [61, 157, 255]).map(Number);
        return (r << 16) | (g << 8) | b;
    }
    function ensureGL() {
        if (gl) return;
        HIGHLIGHT.color = accent();
        gl = new THREE.WebGLRenderer({canvas, antialias: true, alpha: true, premultipliedAlpha: true});
        gl.autoClear = false;
        tint = new THREE.MeshBasicMaterial({color: HIGHLIGHT.color, transparent: true, opacity: 0.38, depthWrite: false, depthFunc: THREE.LessEqualDepth});
        glow = tint.clone(); glow.opacity = 0.16;
        depth = new THREE.MeshBasicMaterial({colorWrite: false});
        gizmo = new TransformControls(camera, canvas);
        gizmo.setMode('rotate'); gizmo.setSize(0.8);
        gizmoScene.add(gizmo);
        gizmo.addEventListener('change', soon);
        let cap = null, before = null;
        gizmo.addEventListener('mouseDown', () => {
            before = editor.doc();
            cap = sel?.fig && !sel.whole && TORSO.has(sel.joint) ? stage.captureArms(sel.fig) : null;
        });
        gizmo.addEventListener('objectChange', () => {
            if (!sel) return;
            if (sel.fig && sel.whole) stage.figureTurned(sel.fig);
            else if (sel.fig) stage.jointTurned(sel.fig, sel.joint, cap);
            push(false);
        });
        gizmo.addEventListener('mouseUp', () => { cap = null; if (before) push(true); before = null; });
    }
    function attach() {
        if (!gizmo) return;
        gizmo.detach();
        if (!sel || !active) return;
        gizmo.showX = gizmo.showY = gizmo.showZ = true;
        if (sel.prop) {
            const prop = stage.prop(sel.prop);
            if (!prop) return;
            gizmo.setSpace(prop.held ? 'local' : 'world');
            if (!prop.held) gizmo.showX = gizmo.showZ = false;
            gizmo.attach(prop.group);
        } else if (sel.whole) {
            const fig = stage.figure(sel.fig);
            if (!fig) return;
            gizmo.setSpace('world'); gizmo.showX = gizmo.showZ = false;
            gizmo.attach(fig.group);
        } else {
            const joint = stage.jointObject(sel.fig, sel.joint);
            if (!joint) return;
            gizmo.setSpace('local');
            if (sel.joint.startsWith('ankle')) gizmo.showY = gizmo.showZ = false;
            gizmo.attach(joint);
        }
    }
    function meshesOf(s) {
        if (!s) return [];
        if (s.prop) return stage.prop(s.prop)?.meshes || [];
        const fig = stage.figure(s.fig);
        if (!fig) return [];
        return s.whole ? fig.meshes : fig.meshes.filter(m => (m.userData.joint || 'root') === s.joint);
    }
    function soon() { if (!frame) frame = requestAnimationFrame(() => { frame = 0; paint(); }); }
    function paint() {
        canvas.hidden = !active;
        if (!active) return;
        ensureGL();
        const w = Math.max(1, host.clientWidth), h = Math.max(1, host.clientHeight), dpr = Math.min(2, devicePixelRatio || 1);
        if (size !== `${w}x${h}x${dpr}`) { size = `${w}x${h}x${dpr}`; gl.setPixelRatio(dpr); gl.setSize(w, h, false); }
        follow();
        const sheet = editor.sheetRect();
        stage.viewCamera(camera, sheet, w, h);
        gl.setClearColor(0x000000, 0); gl.clear();
        const marked = [[meshesOf(sel), tint], [hover && !same(hover, sel) ? meshesOf(hover) : [], glow]];
        if (marked.some(([list]) => list.length)) {
            // The marks stay on the paper, like the layer under them; the rings may reach past it.
            gl.setScissor(sheet.x, h - sheet.y - sheet.h, sheet.w, sheet.h); gl.setScissorTest(true);
            camera.layers.set(0);
            stage.scene.overrideMaterial = depth; gl.render(stage.scene, camera);
            for (const [list, material] of marked) {
                if (!list.length) continue;
                for (const m of list) m.layers.enable(1);
                camera.layers.set(1); stage.scene.overrideMaterial = material; gl.render(stage.scene, camera);
                for (const m of list) m.layers.disable(1);
                camera.layers.set(0);
            }
            stage.scene.overrideMaterial = null;
            gl.setScissorTest(false);
        }
        if (gizmo.object) gl.render(gizmoScene, camera);
    }
    const same = (a, b) => !!a && !!b && a.fig === b.fig && a.prop === b.prop && a.joint === b.joint && !!a.whole === !!b.whole;
    // The live scene follows the document (after undo or redo); a selection whose person or prop went is dropped.
    function follow() {
        const layer = poseLayerOf(editor.doc());
        if (!layer) { if (sel) { sel = null; attach(); } return; }
        stage.setAspect(layer.nw / layer.nh);
        stage.sync(layer.pose);
        const gone = s => s && (s.prop ? !stage.prop(s.prop) : !stage.figure(s.fig));
        if (gone(hover)) hover = null;
        if (gone(sel)) { sel = null; attach(); updateRow(); }
        else if (sel && gizmo && !gizmo.object) attach();
    }

    // ---- the document
    function push(final) {
        const doc = editor.doc(), layer = poseLayerOf(doc);
        if (!layer) return;
        const next = commands.updatePose(doc, layer.id, stage.json());
        if (final) editor.commit(next, false); else editor.preview(next);
    }
    let settle = 0;
    function pushSoon() { push(false); clearTimeout(settle); settle = setTimeout(() => { settle = 0; push(true); }, 450); }
    // The first visit puts one person on the sheet; the layer goes right above the paper.
    function ensureLayer() {
        const doc = editor.doc();
        if (poseLayerOf(doc) || !doc.layers.length) return;
        const r = editor.sheetRect();
        stage.sync(emptyScene()); stage.setAspect(r.w / r.h || 1);
        stage.addFigure('female');
        editor.commit(commands.addPoseLayer(doc, {name: t('Mannequin', '人偶'), pose: stage.json()}), false);
    }
    function select(next) {
        sel = next; attach(); updateRow(); paint();
    }
    function updateRow() {
        if (delButton) delButton.disabled = !sel;
        for (const b of dialog.querySelectorAll('.fv-pose-add')) b.disabled = stage.figureIds().length >= MAX_FIGURES;
    }

    // ---- pointer
    function ndc([x, y]) { return new THREE.Vector2(x / Math.max(1, host.clientWidth) * 2 - 1, -(y / Math.max(1, host.clientHeight)) * 2 + 1); }
    function view() { return stage.viewCamera(camera, editor.sheetRect(), host.clientWidth, host.clientHeight); }
    function gesture(event, p) {
        if (gizmo?.dragging || gizmo?.axis) return null;
        closePop();
        follow();
        const cam = view(), hit = stage.pick(ndc(p), cam), started = editor.doc();
        const moves = q => Math.hypot(q[0] - p[0], q[1] - p[1]) >= 3;
        if (hit) {
            const whole = !!hit.fig && sel?.fig === hit.fig && !!sel.whole;
            select(hit.prop ? {prop: hit.prop} : whole ? {fig: hit.fig, whole: true} : {fig: hit.fig, joint: hit.joint});
            const drag = stage.beginDrag(hit, cam, whole);
            host.dataset.poseDrag = 'true';
            let moved = false;
            return {
                move(q) { if (!drag || (!moved && !moves(q))) return; moved = true; drag.move(ndc(q)); push(false); },
                up() { delete host.dataset.poseDrag; if (moved) push(true); },
                cancel() { delete host.dataset.poseDrag; editor.preview(started); },
            };
        }
        // The empty part of the sheet: turn the camera round the people (Shift moves it sideways instead).
        const before = {...stage.camera}, height = editor.sheetRect().h;
        let moved = false;
        host.dataset.poseDrag = 'true';
        return {
            move(q) {
                if (!moved && !moves(q)) return;
                moved = true;
                Object.assign(stage.camera, before);
                if (event.shiftKey) stage.pan(q[0] - p[0], q[1] - p[1], height); else stage.orbit((q[0] - p[0]) * 0.35, (q[1] - p[1]) * 0.35);
                push(false);
            },
            up() { delete host.dataset.poseDrag; if (moved) push(true); else select(null); },
            cancel() { delete host.dataset.poseDrag; editor.preview(started); },
        };
    }
    function hoverAt(p) {
        if (!active || gizmo?.dragging) return;
        let next = null;
        if (p && !gizmo?.axis) {
            follow();
            const hit = stage.pick(ndc(p), view());
            next = hit ? (hit.prop ? {prop: hit.prop} : {fig: hit.fig, joint: hit.joint}) : null;
        }
        host.dataset.poseHover = String(!!next);
        if (!same(next, hover) && (next || hover)) { hover = next; soon(); }
    }
    function wheel(event) {
        if (event.ctrlKey || event.metaKey || !poseLayerOf(editor.doc())) return false;
        follow();
        stage.dolly(Math.exp(event.deltaY * 0.0012));
        pushSoon();
        return true;
    }
    function dblclick(p) {
        follow();
        const hit = stage.pick(ndc(p), view());
        if (hit?.fig) select({fig: hit.fig, whole: true});
    }

    // ---- actions
    const targets = () => (sel?.fig ? [sel.fig] : stage.figureIds());
    function act(change, keep = sel) {
        follow();
        change();
        sel = keep; attach(); push(true); updateRow();
    }
    function addFigure(gender) {
        follow();
        const id = stage.addFigure(gender);
        if (id) act(() => {}, {fig: id, whole: true});
    }
    function addProp(type) {
        follow();
        const holder = sel?.fig ? {fig: sel.fig, side: sideOf(sel.joint)} : null;
        const id = stage.addProp(type, holder);
        if (id) act(() => {}, {prop: id});
    }
    function removeSelected() {
        if (!sel) return false;
        const s = sel;
        act(() => { if (s.prop) stage.removeProp(s.prop); else stage.removeFigure(s.fig); }, null);
        return true;
    }

    // ---- the options row and its attached windows
    const PLUS = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
    const DOWN = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
    function options(add, sep, note) {
        for (const [gender, label, tip] of [['female', t('Female', '女'), t('Add a female mannequin', '添加女性人偶')], ['male', t('Male', '男'), t('Add a male mannequin', '添加男性人偶')]]) {
            const b = button('', tip, () => addFigure(gender), 'fv-ie-button fv-pose-add');
            b.innerHTML = PLUS; b.append(el('span', label)); b.setAttribute('aria-label', tip);
            add(b);
        }
        add(sep(),
            popButton('pose', t('Pose', '姿势'), posePanel),
            popButton('props', t('Props', '道具'), propsPanel),
            popButton('camera', t('Camera', '机位'), cameraPanel),
            sep(),
            button(t('Reset', '重置'), t('Everyone standing, the camera in front', '人物回到站立，机位回到正面'), () => act(() => stage.reset(), null)));
        delButton = iconButton('trash', t('Delete the selected (Delete)', '删除选中的人物或道具（Delete）'), () => removeSelected());
        add(delButton);
        add(note(t('Drag a part to pose it; drag the paper to turn the camera.', '拖动身体摆姿势，拖动空白处转动机位。')));
        updateRow();
    }
    function popButton(id, label, build) {
        const b = button('', null, () => (pop?.id === id ? closePop() : openPop(id, b, build)), 'fv-ie-button fv-pose-pop-button');
        b.append(el('span', label)); b.insertAdjacentHTML('beforeend', DOWN);
        b.dataset.pop = id; b.setAttribute('aria-haspopup', 'true'); b.setAttribute('aria-expanded', String(pop?.id === id));
        // The row drawn again (undo, a wider window) keeps an open window on the new button.
        if (pop?.id === id) { pop.anchor = b; queueMicrotask(refreshPop); }
        return b;
    }
    function chips(list, onPick, pressed = () => false) {
        const row = el('div', null, 'fv-pose-chips');
        // One row for five or fewer, else rows of three (six, nine) or four.
        const n = list.length, per = n <= 5 ? n : n === 6 || n === 9 ? 3 : 4;
        row.style.setProperty('--per', String(per));
        for (const [id, label] of list) {
            const b = button(label, null, () => { onPick(id); refreshPop(); }, 'fv-pose-chip');
            b.dataset.value = id; b.setAttribute('aria-pressed', String(pressed(id)));
            row.append(b);
        }
        if (n % per) row.lastElementChild.style.gridColumn = `span ${per - n % per + 1}`;
        return row;
    }
    const group = (title, ...body) => { const g = el('section', null, 'fv-pose-group'); g.append(el('h4', title), ...body); return g; };
    const words = ([en, zh]) => t(en, zh);
    function posePanel() {
        const fig = stage.figure(sel?.fig || stage.figureIds()[0]);
        const side = sideOf(sel?.joint);
        const shape = fig ? fig.pose.hands[side || 'R'].shape : null;
        return [
            group(t('Pose', '姿势'), chips(Object.entries(PRESETS).map(([id, p]) => [id, words(p.label)]), id => act(() => stage.applyPreset(id, targets())))),
            group(side === 'L' ? t('Left hand', '左手') : side === 'R' ? t('Right hand', '右手') : t('Hands', '手型'),
                chips(Object.entries(HAND_SHAPES).map(([id, h]) => [id, words(h.label)]), id => act(() => stage.setHands(id, targets(), side)), id => id === shape)),
        ];
    }
    function propsPanel() {
        const of = kind => Object.entries(PROPS).filter(([, p]) => p.kind === kind).map(([id, p]) => [id, words(p.label)]);
        return [group(t('In the scene', '场景'), chips(of('scene'), addProp)), group(t('In a hand', '手持'), chips(of('hand'), addProp))];
    }
    function cameraPanel() {
        return [group(t('Camera', '机位'), chips(Object.entries(CAMERA_PRESETS).map(([id, c]) => [id, words(c.label)]), id => act(() => stage.cameraPreset(id, sel?.fig))),
            chips([['fit', t('Fit everyone in the picture', '全部人物入画')]], () => act(() => stage.frameAll())))];
    }
    function openPop(id, anchor, build) {
        closePop();
        const box = el('div', null, 'fv-pose-pop'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', anchor.textContent);
        dialog.append(box);
        pop = {id, box, anchor, build};
        anchor.setAttribute('aria-expanded', 'true');
        refreshPop();
        const away = event => { if (pop?.box === box && !box.contains(event.target) && !anchor.contains(event.target)) closePop(); };
        document.addEventListener('pointerdown', away, true);
        pop.off = () => document.removeEventListener('pointerdown', away, true);
        pop.keys = listenDialogKey(box, 'keydown', event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closePop(true); } });
        requestAnimationFrame(() => box.querySelector('button')?.focus({preventScroll: true}));
    }
    function refreshPop() {
        if (!pop) return;
        follow();
        // Drawn again after each choice; the key that had the focus keeps it (Esc and Tab go on from there).
        const at = [...pop.box.querySelectorAll('button')].indexOf(document.activeElement);
        pop.box.replaceChildren(...pop.build());
        if (at >= 0) pop.box.querySelectorAll('button')[at]?.focus({preventScroll: true});
        // Attached to its button: under it, kept inside the window.
        const a = pop.anchor.getBoundingClientRect(), b = pop.box.getBoundingClientRect();
        pop.box.style.left = `${Math.round(Math.max(8, Math.min(a.left, innerWidth - b.width - 8)))}px`;
        pop.box.style.top = `${Math.round(a.bottom + 6)}px`;
    }
    function closePop(focus = false) {
        if (!pop) return false;
        const {box, anchor, off, keys} = pop;
        pop = null; off?.(); keys?.(); box.remove();
        if (anchor.isConnected) { anchor.setAttribute('aria-expanded', 'false'); if (focus) anchor.focus({preventScroll: true}); }
        return true;
    }

    return {
        // The renderer's painter for the layer (the editor sets it on its renderer).
        painter(pose, w, h, fine, into) { stage.sync(pose); stage.setAspect(w / h); return stage.lineArt(w, h, fine, into); },
        activate() { active = true; ensureLayer(); follow(); attach(); host.dataset.pose = 'true'; paint(); },
        deactivate() { active = false; closePop(); hover = null; sel = null; attach(); delete host.dataset.pose; delete host.dataset.poseHover; paint(); },
        options, gesture, wheel, dblclick, hover: hoverAt,
        draw() { if (active) soon(); },
        // Keys while the tool is on: Delete takes out the selected person or prop.
        key(event) { return (event.key === 'Delete' || event.key === 'Backspace') && removeSelected(); },
        // Esc closes an open window, then clears the selection; false lets the editor go on (leave).
        escape() { if (closePop(true)) return true; if (sel) { select(null); return true; } return false; },
        // The words for the prompt: which way each person faces and how much of them the picture holds.
        words(doc) {
            const layer = poseLayerOf(doc);
            if (!layer?.visible || !layer.pose?.figures?.length) return null;
            stage.sync(layer.pose);
            return stage.words(layer.nw / layer.nh);
        },
        dispose() {
            closePop(); cancelAnimationFrame(frame); clearTimeout(settle);
            if (gizmo) { gizmo.detach(); gizmo.dispose(); }
            for (const m of [tint, glow, depth]) m?.dispose();
            if (gl) { gl.dispose(); gl.forceContextLoss(); }
            stage.dispose(); canvas.remove();
        },
        state: () => ({selected: sel, figures: stage.figureIds().length, props: stage.propIds().length, camera: {...stage.camera}}),
        // For automation: where a joint of the n-th person is on screen (client pixels).
        point(index, joint) {
            follow();
            const object = stage.jointObject(stage.figureIds()[index], joint);
            if (!object) return null;
            const q = object.getWorldPosition(new THREE.Vector3()).project(view()), box = host.getBoundingClientRect();
            return [box.left + (q.x + 1) / 2 * host.clientWidth, box.top + (1 - q.y) / 2 * host.clientHeight];
        },
    };
}
