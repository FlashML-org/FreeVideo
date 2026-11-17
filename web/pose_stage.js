// The pose tool's scene: mannequins, props and the shot camera. The scene is kept as plain JSON in the sketch's pose
// layer, so undo, redo, saving and opening the sketch again all go through the editor's own document; this module makes
// a live three.js scene match that JSON, changes it on request and draws it as line art (black strokes on a clear
// ground) for the layer. It also says, in a few plain affirmative words, which way each person faces the camera and how
// much of them the frame holds; the image view adds those words to the prompt.
import * as THREE from './vendor/three/three.module.min.js';
import {buildFigure, bakeIds, setHandShape, HAND_SHAPES} from './pose_body.js';
import {PROPS, buildProp, bakePropIds} from './pose_props.js';

const DEG = Math.PI / 180;
const V3 = THREE.Vector3, Quat = THREE.Quaternion;
const X_AXIS = new V3(1, 0, 0);
const clamp = THREE.MathUtils.clamp;
const wrap = a => ((a + 180) % 360 + 360) % 360 - 180;
const tidy = v => Math.round(v * 1e4) / 1e4;
const finite = (v, d) => (typeof v === 'number' && Number.isFinite(v) ? v : d);
const vec = (a, d, lo = -50, hi = 50) => (Array.isArray(a) && a.length === 3 ? a.map((v, i) => clamp(finite(v, d[i]), lo, hi)) : d.slice());

export const MAX_FIGURES = 8;
export const MAX_PROPS = 16;
export const DEFAULT_CAMERA = Object.freeze({az: 0, elev: 7, dist: 3.9, aim: 1.0, tx: 0, tz: 0, fov: 40, roll: 0});

// ---------------------------------------------------------------- figures and poses
// Limb targets (wrist / ankle) and poles (where the elbow or knee points) are kept in the figure frame.
// Torso joints are Euler angles in degrees (order YXZ).
const LIMBS = {
    armL: {upper: 'shoulderL', lower: 'elbowL', end: 'wristL', arm: true},
    armR: {upper: 'shoulderR', lower: 'elbowR', end: 'wristR', arm: true},
    legL: {upper: 'hipL', lower: 'kneeL', end: 'ankleL', arm: false},
    legR: {upper: 'hipR', lower: 'kneeR', end: 'ankleR', arm: false},
};
const LIMB_OF = {};
for (const [l, d] of Object.entries(LIMBS)) for (const k of ['upper', 'lower', 'end']) LIMB_OF[d[k]] = {limb: l, role: k};

const BASE = () => ({
    heading: 0,
    root: {pos: [0, 0.953, 0], rot: [0, 0, 0]},
    spine: [0, 0, 0], chest: [0, 0, 0], neck: [0, 0, 0], head: [0, 0, 0],
    limbs: {
        armL: {target: [0.235, 0.88, 0.03], pole: [0.2, -0.1, -1]},
        armR: {target: [-0.235, 0.88, 0.03], pole: [-0.2, -0.1, -1]},
        legL: {target: [0.1, 0.075, 0], pole: [0.1, 0, 1]},
        legR: {target: [-0.1, 0.075, 0], pole: [-0.1, 0, 1]},
    },
    foot: {L: 0, R: 0},
    hands: {L: {rot: [0, 0, 0], shape: 'relax'}, R: {rot: [0, 0, 0], shape: 'relax'}},
    chair: false,
});

// label: [English, Chinese]. Each keeps the person's heading unless it says otherwise.
export const PRESETS = {
    stand: {label: ['Stand', '站立'], make: p => p},
    walk: {label: ['Walk', '行走'], make: p => {
        p.root.pos = [0, 0.91, 0]; p.spine = [3, 0, 0]; p.chest = [0, 6, 0];
        p.limbs.legL = {target: [0.09, 0.085, 0.3], pole: [0, 0, 1]}; p.foot.L = -14;
        p.limbs.legR = {target: [-0.09, 0.15, -0.27], pole: [0, 0, 1]}; p.foot.R = 28;
        p.limbs.armR = {target: [-0.2, 0.93, 0.2], pole: [-0.1, -0.2, -1]};
        p.limbs.armL = {target: [0.21, 0.93, -0.17], pole: [0.1, 0, -1]};
        return p;
    }},
    run: {label: ['Run', '奔跑'], make: p => {
        p.root.pos = [0, 0.9, 0]; p.spine = [12, 0, 0]; p.chest = [4, 10, 0]; p.head = [-14, 0, 0];
        p.limbs.legL = {target: [0.09, 0.3, 0.4], pole: [0, 0.3, 1]}; p.foot.L = 10;
        p.limbs.legR = {target: [-0.09, 0.3, -0.42], pole: [0, -0.5, 1]}; p.foot.R = 50;
        p.limbs.armR = {target: [-0.15, 1.24, 0.34], pole: [-0.1, -1, -0.4]};
        p.limbs.armL = {target: [0.2, 1.04, -0.14], pole: [0.1, -0.2, -1]};
        p.hands.L.shape = p.hands.R.shape = 'fist';
        return p;
    }},
    sit: {label: ['Sit', '坐下'], make: p => {
        p.chair = true; p.root.pos = [0, 0.54, 0];
        p.limbs.legL = {target: [0.13, 0.08, 0.47], pole: [0, 0.6, 1]};
        p.limbs.legR = {target: [-0.13, 0.08, 0.47], pole: [0, 0.6, 1]};
        p.limbs.armL = {target: [0.16, 0.63, 0.3], pole: [0.4, -0.3, -1]};
        p.limbs.armR = {target: [-0.16, 0.63, 0.3], pole: [-0.4, -0.3, -1]};
        return p;
    }},
    chin: {label: ['Hand on chin', '托腮'], make: p => {
        p.chair = true; p.root.pos = [0, 0.54, 0]; p.spine = [6, 0, 0]; p.chest = [8, 0, 0]; p.head = [6, 0, 0];
        p.limbs.legL = {target: [0.13, 0.08, 0.47], pole: [0, 0.6, 1]};
        p.limbs.legR = {target: [-0.13, 0.08, 0.47], pole: [0, 0.6, 1]};
        p.limbs.armL = {target: [0.16, 0.63, 0.3], pole: [0.4, -0.3, -1]};
        p.limbs.armR = {anchor: 'chin', offset: [0, -0.04, 0.02], pole: [-0.2, -0.6, 0.2]};
        p.hands.R.shape = 'cup';
        return p;
    }},
    turnBack: {label: ['Look back', '背身回头'], make: (p, ctx) => {
        p.heading = ctx.cameraAz + 180;
        p.root.rot = [0, 0, 3]; p.chest = [0, 22, 0]; p.neck = [4, 25, 0]; p.head = [6, 48, -4];
        p.limbs.legL.target = [0.13, 0.075, 0.02]; p.limbs.legR.target = [-0.09, 0.075, -0.02];
        return p;
    }},
    handsHips: {label: ['Hands on hips', '叉腰'], make: p => {
        p.limbs.armL = {target: [0.19, 1.0, -0.01], pole: [1, 0.2, -0.4]};
        p.limbs.armR = {target: [-0.19, 1.0, -0.01], pole: [-1, 0.2, -0.4]};
        p.limbs.legL.target = [0.14, 0.075, 0]; p.limbs.legR.target = [-0.14, 0.075, 0];
        p.hands.L.shape = p.hands.R.shape = 'fist';
        return p;
    }},
    // The arm reaches out and up, clear of the head and the body, and the person turns a quarter from the camera, so the
    // line art shows the whole arm (straight up beside the head, seen from the front, reads as a stripe the picture model drops).
    armRaise: {label: ['Raise a hand', '举手'], make: (p, ctx) => {
        p.heading = ctx.cameraAz - 35;
        p.limbs.armR = {target: [-0.5, 1.85, 0.05], pole: [-1, -0.2, -0.3]};
        p.limbs.armL = {target: [0.23, 0.9, 0.02], pole: [0.6, -0.2, -1]};
        p.hands.R.shape = 'open';
        return p;
    }},
    point: {label: ['Point', '指向'], make: p => {
        p.limbs.armR = {anchor: 'shoulderR', offset: [0, 0.02, 0.54], pole: [-0.3, -1, 0]};
        p.hands.R.shape = 'point';
        return p;
    }},
};

const ANCHOR_LOCAL = {
    chin: ['head', [0, 0.005, 0.085]], headTop: ['head', [0, 0.24, 0]],
    shoulderL: ['shoulderL', [0, 0, 0]], shoulderR: ['shoulderR', [0, 0, 0]],
};

// Camera presets. Front, side and back turn about the selected person (else the first): 0 looks at their front. The
// angles keep the camera's way round (az null) and change only its height.
export const CAMERA_PRESETS = {
    front: {label: ['Front', '正面'], az: 0, elev: 7, fov: 40},
    side: {label: ['Side', '侧面'], az: 90, elev: 5, fov: 40},
    back: {label: ['Back', '背面'], az: 180, elev: 7, fov: 40},
    low: {label: ['Low angle', '低角度'], az: null, elev: -16, fov: 56},
    high: {label: ['High angle', '高角度'], az: null, elev: 42, fov: 44},
    top: {label: ['Overhead', '俯拍'], az: null, elev: 88, fov: 44},
};

const BODY_COLOR = 0xb8c4d8;

class Figure {
    constructor(id, gender, build) {
        this.id = id; this.gender = gender; this.build = build; this.scale = 1;
        this.pose = BASE();
        Object.assign(this, buildFigure({gender, build, color: BODY_COLOR}));
        this.group.userData.fig = this;
    }
    dispose() {
        this.group.traverse(o => { if (o.isMesh) o.geometry.dispose(); });
        for (const m of Object.values(this.mats)) m.dispose();
    }
    pf(obj) { return this.group.worldToLocal(obj.getWorldPosition(new V3())); }
    qf(obj) { return this.group.quaternion.clone().invert().multiply(obj.getWorldQuaternion(new Quat())); }
    // Analytic two-bone IK in the figure frame. The joint bends in the plane of the target direction and the pole.
    solveLimb(def, T, pole) {
        const J = this.J, upper = J[def.upper], lower = J[def.lower], end = J[def.end];
        const a = lower.position.length(), b = end.position.length();
        const S = this.pf(upper);
        const dir = T.clone().sub(S);
        const d = clamp(dir.length(), Math.abs(a - b) + 1e-3, (a + b) * 0.9995);
        dir.normalize();
        const perp = pole.clone().addScaledVector(dir, -pole.dot(dir));
        if (perp.lengthSq() < 1e-6) { perp.set(0, 0, def.arm ? -1 : 1); perp.addScaledVector(dir, -perp.dot(dir)); }
        if (perp.lengthSq() < 1e-6) perp.set(1, 0, 0);
        perp.normalize();
        const cosA = clamp((a * a + d * d - b * b) / (2 * a * d), -1, 1);
        const u = dir.clone().multiplyScalar(cosA).addScaledVector(perp, Math.sqrt(1 - cosA * cosA));
        const elbow = S.clone().addScaledVector(u, a);
        const v = S.clone().addScaledVector(dir, d).sub(elbow).normalize();
        const yAxis = u.clone().negate();
        const xAxis = def.arm ? new V3().crossVectors(dir, perp) : new V3().crossVectors(perp, dir);
        const zAxis = new V3().crossVectors(xAxis, yAxis).normalize();
        xAxis.crossVectors(yAxis, zAxis).normalize();
        const q = new Quat().setFromRotationMatrix(new THREE.Matrix4().makeBasis(xAxis, yAxis, zAxis));
        upper.quaternion.copy(this.qf(upper.parent).invert().multiply(q));
        upper.updateMatrixWorld(true);
        const vLocal = v.applyQuaternion(q.clone().invert());
        lower.quaternion.setFromAxisAngle(X_AXIS, Math.atan2(-vLocal.z, -vLocal.y));
        lower.updateMatrixWorld(true);
    }
    applyTorso() {
        const g = this.group, p = this.pose, J = this.J;
        g.rotation.set(0, p.heading * DEG, 0);
        g.scale.setScalar(this.baseScale * this.scale);
        J.root.position.set(...p.root.pos);
        setEuler(J.root, p.root.rot);
        for (const name of ['spine', 'chest', 'neck', 'head']) setEuler(J[name], p[name]);
        this.chair.visible = p.chair;
        g.updateMatrixWorld(true);
    }
    apply() {
        const p = this.pose, J = this.J;
        this.applyTorso();
        for (const [name, def] of Object.entries(LIMBS)) {
            const l = p.limbs[name];
            this.solveLimb(def, new V3(...l.target), new V3(...l.pole));
        }
        for (const side of ['L', 'R']) {
            // Feet stay level with the figure's heading (plus their own pitch); hands follow the forearm.
            const an = J['ankle' + side];
            const want = new Quat().setFromAxisAngle(X_AXIS, p.foot[side] * DEG);
            an.quaternion.copy(this.qf(an.parent).invert().multiply(want));
            setEuler(J['wrist' + side], p.hands[side].rot);
            setHandShape(this, side, p.hands[side].shape);
        }
        this.group.updateMatrixWorld(true);
    }
    anchor(name, offset = [0, 0, 0]) {
        const [jn, local] = ANCHOR_LOCAL[name];
        const f = this.group.worldToLocal(this.J[jn].localToWorld(new V3(...local)));
        return [f.x + offset[0], f.y + offset[1], f.z + offset[2]];
    }
    // Builds a pose in two passes: torso and legs first, then arms that reach for a body point.
    buildPose(make, ctx) {
        const keep = this.pose.heading;
        const p = make(BASE(), ctx);
        if (p.heading === 0 && keep) p.heading = keep;
        const anchored = {};
        for (const [name, l] of Object.entries(p.limbs)) {
            if (l.anchor) { anchored[name] = l; p.limbs[name] = {target: BASE().limbs[name].target, pole: l.pole}; }
        }
        this.pose = p;
        this.apply();
        for (const [name, l] of Object.entries(anchored)) p.limbs[name] = {target: this.anchor(l.anchor, l.offset), pole: l.pole};
        this.apply();
    }
}
function setEuler(obj, [x, y, z]) { obj.rotation.set(x * DEG, y * DEG, z * DEG, 'YXZ'); }
const eulerDeg = q => { const e = new THREE.Euler().setFromQuaternion(q, 'YXZ'); return [e.x / DEG, e.y / DEG, e.z / DEG]; };

// When the torso turns, the arms go with it: remember wrists and elbows relative to the chest, then put them back.
function captureArms(fig) {
    const c = fig.J.chest;
    fig.group.updateMatrixWorld(true);
    return ['L', 'R'].map(s => ({s, w: c.worldToLocal(fig.J['wrist' + s].getWorldPosition(new V3())), e: c.worldToLocal(fig.J['elbow' + s].getWorldPosition(new V3()))}));
}
function restoreArms(fig, cap) {
    const c = fig.J.chest, g = fig.group;
    for (const {s, w, e} of cap) {
        const wf = g.worldToLocal(c.localToWorld(w.clone())), ef = g.worldToLocal(c.localToWorld(e.clone()));
        fig.pose.limbs['arm' + s] = {target: wf.toArray(), pole: ef.sub(fig.pf(fig.J['shoulder' + s])).toArray()};
    }
}
// After a joint was turned directly, read the limb's target and pole back so the pose stays the source of truth.
function readLimb(fig, limb) {
    const d = LIMBS[limb];
    fig.group.updateMatrixWorld(true);
    const S = fig.pf(fig.J[d.upper]), E = fig.pf(fig.J[d.lower]), T = fig.pf(fig.J[d.end]);
    const pole = E.clone().sub(S);
    const dir = T.clone().sub(S).normalize();
    if (pole.clone().addScaledVector(dir, -pole.dot(dir)).lengthSq() < 1e-5) pole.fromArray(fig.pose.limbs[limb].pole);
    fig.pose.limbs[limb] = {target: T.toArray(), pole: pole.toArray()};
}

// A stored pose, checked and clamped field by field (a document can come from anywhere).
function cleanPose(p) {
    const b = BASE();
    if (!p || typeof p !== 'object') return b;
    const limbs = {};
    for (const name of Object.keys(LIMBS)) {
        const l = p.limbs?.[name];
        limbs[name] = {target: vec(l?.target, b.limbs[name].target, -3, 3), pole: vec(l?.pole, b.limbs[name].pole, -5, 5)};
    }
    const hand = side => ({rot: vec(p.hands?.[side]?.rot, [0, 0, 0], -360, 360),
        shape: Object.hasOwn(HAND_SHAPES, p.hands?.[side]?.shape) ? p.hands[side].shape : 'relax'});
    return {
        heading: wrap(finite(p.heading, 0)),
        root: {pos: vec(p.root?.pos, b.root.pos, -3, 3), rot: vec(p.root?.rot, b.root.rot, -360, 360)},
        spine: vec(p.spine, b.spine, -360, 360), chest: vec(p.chest, b.chest, -360, 360), neck: vec(p.neck, b.neck, -360, 360), head: vec(p.head, b.head, -360, 360),
        limbs, foot: {L: clamp(finite(p.foot?.L, 0), -90, 90), R: clamp(finite(p.foot?.R, 0), -90, 90)},
        hands: {L: hand('L'), R: hand('R')}, chair: p.chair === true,
    };
}
const tidyPose = p => JSON.parse(JSON.stringify(p, (k, v) => (typeof v === 'number' ? tidy(v) : v)));

export function emptyScene() {
    return {v: 1, figures: [], props: [], camera: {...DEFAULT_CAMERA}};
}
export function cleanScene(s) {
    const scene = emptyScene();
    if (!s || typeof s !== 'object') return scene;
    const ids = new Set();
    for (const f of Array.isArray(s.figures) ? s.figures.slice(0, MAX_FIGURES) : []) {
        if (!f || typeof f.id !== 'string' || ids.has(f.id)) continue;
        ids.add(f.id);
        scene.figures.push({id: f.id, gender: f.gender === 'male' ? 'male' : 'female', build: ['slim', 'strong'].includes(f.build) ? f.build : 'standard',
            x: clamp(finite(f.x, 0), -40, 40), y: clamp(finite(f.y, 0), -5, 5), z: clamp(finite(f.z, 0), -40, 40), scale: clamp(finite(f.scale, 1), 0.6, 1.5),
            pose: cleanPose(f.pose)});
    }
    const pids = new Set();
    for (const p of Array.isArray(s.props) ? s.props.slice(0, MAX_PROPS) : []) {
        if (!p || typeof p.id !== 'string' || pids.has(p.id) || !Object.hasOwn(PROPS, p.type)) continue;
        pids.add(p.id);
        const held = p.held && ids.has(p.held.fig) && ['L', 'R'].includes(p.held.side) && PROPS[p.type].hold ? {fig: p.held.fig, side: p.held.side} : null;
        scene.props.push({id: p.id, type: p.type, pos: vec(p.pos, [0, 0, 0], -40, 40), rot: vec(p.rot, [0, 0, 0], -360, 360), scale: clamp(finite(p.scale, 1), 0.2, 4), held});
    }
    const c = s.camera || {};
    scene.camera = {az: wrap(finite(c.az, DEFAULT_CAMERA.az)), elev: wrap(finite(c.elev, DEFAULT_CAMERA.elev)), dist: clamp(finite(c.dist, DEFAULT_CAMERA.dist), 0.3, 40),
        aim: clamp(finite(c.aim, DEFAULT_CAMERA.aim), -5, 5), tx: clamp(finite(c.tx, 0), -40, 40), tz: clamp(finite(c.tz, 0), -40, 40),
        fov: clamp(finite(c.fov, DEFAULT_CAMERA.fov), 15, 100), roll: clamp(finite(c.roll, 0), -45, 45)};
    return scene;
}

// ---------------------------------------------------------------- line art
// Every body part is drawn in a flat colour that encodes (person, part, region); a second pass draws a black stroke
// wherever those ids change: the full stroke round each person and prop, a medium one between limbs, a thin one
// between regions of one part. Hidden surfaces never reach the id image, so there are no hidden lines. The id image
// also carries a stroke scale in alpha: people far from the lens get finer strokes than people close to it.
const ID_VERTEX = `attribute vec3 aId; uniform float uK; varying vec3 vId; varying float vS;
    void main(){ vId = aId; vec4 mv = modelViewMatrix * vec4(position, 1.0); vS = clamp(uK / max(0.05, -mv.z), 0.4, 1.1) / 1.1; gl_Position = projectionMatrix * mv; }`;
const EDGE_FRAGMENT = `precision highp float;
    uniform sampler2D tId; uniform vec2 uPx; uniform float uR; uniform float uMid; uniform float uThin; uniform float uMin; varying vec2 vUv;
    void main() {
        vec4 c = texture2D(tId, vUv);
        float line = 0.0;
        for (int k = 1; k <= 14; k++) {
            float r = float(k);
            if (r > uR * 1.1 + 1.0) break;
            for (int j = 0; j < 12; j++) {
                float a = 6.2831853 * (float(j) + 0.37 * r) / 12.0;
                vec4 s = texture2D(tId, vUv + vec2(cos(a), sin(a)) * r * uPx);
                float ga = floor(c.g * 255.0 + 0.5), gb = floor(s.g * 255.0 + 0.5);
                bool hipLine = (ga == 1.0 && (gb == 5.0 || gb == 6.0)) || (gb == 1.0 && (ga == 5.0 || ga == 6.0));
                float w = abs(s.r - c.r) > 0.002 ? uR : abs(s.g - c.g) > 0.002 ? (hipLine ? 0.0 : uMid) : abs(s.b - c.b) > 0.002 ? uThin : 0.0;
                if (w > 0.0) {
                    w = max(uMin, w * max(c.a, s.a) * 1.1);
                    line = max(line, 1.0 - smoothstep(w - 0.5, w + 0.5, r));
                }
            }
        }
        gl_FragColor = vec4(0.0, 0.0, 0.0, line);
    }`;

export function createStage() {
    const scene = new THREE.Scene();
    const figures = new Map(), props = new Map();
    let order = [], propOrder = [], structure = '';
    const cam = {...DEFAULT_CAMERA};
    const shot = new THREE.PerspectiveCamera(cam.fov, 1, 0.05, 120);
    let last = '';           // the JSON the live scene matches
    let seq = 0;
    const newId = prefix => `${prefix}${Date.now().toString(36)}${(seq++).toString(36)}`;

    // ---- JSON ⇄ live scene
    function sync(json) {
        const text = JSON.stringify(json);
        if (text === last) return;
        last = text;
        const s = cleanScene(json);
        const want = new Map(s.figures.map(f => [f.id, f]));
        for (const [id, fig] of figures) {
            const f = want.get(id);
            if (!f || f.gender !== fig.gender || f.build !== fig.build) dropFigure(id);
        }
        for (const f of s.figures) {
            let fig = figures.get(f.id);
            if (!fig) { fig = new Figure(f.id, f.gender, f.build); figures.set(f.id, fig); scene.add(fig.group); }
            fig.group.position.set(f.x, f.y, f.z); fig.scale = f.scale; fig.pose = structuredClone(f.pose);
            fig.apply();
        }
        order = s.figures.map(f => f.id);
        const wantProps = new Map(s.props.map(p => [p.id, p]));
        for (const [id, prop] of props) if (!wantProps.has(id) || wantProps.get(id).type !== prop.type) dropProp(id);
        for (const p of s.props) {
            let prop = props.get(p.id);
            if (!prop) prop = makeProp(p.id, p.type);
            placeProp(prop, p);
        }
        propOrder = s.props.map(p => p.id);
        Object.assign(cam, s.camera);
        renumber();
    }
    function json() {
        const out = {v: 1,
            figures: order.map(id => { const f = figures.get(id), g = f.group.position;
                return {id, gender: f.gender, build: f.build, x: tidy(g.x), y: tidy(g.y), z: tidy(g.z), scale: tidy(f.scale), pose: tidyPose(f.pose)}; }),
            props: propOrder.map(id => { const p = props.get(id), g = p.group;
                return {id, type: p.type, pos: g.position.toArray().map(tidy), rot: [g.rotation.x, g.rotation.y, g.rotation.z].map(r => tidy(r / DEG)),
                    scale: tidy(g.scale.x), held: p.held ? {...p.held} : null}; }),
            camera: Object.fromEntries(Object.entries(cam).map(([k, v]) => [k, tidy(v)]))};
        last = JSON.stringify(out);
        return out;
    }
    function dropFigure(id) {
        const fig = figures.get(id);
        if (!fig) return;
        for (const p of props.values()) if (p.held?.fig === id) { scene.attach(p.group); p.held = null; }
        scene.remove(fig.group); fig.dispose(); figures.delete(id);
        order = order.filter(x => x !== id);
    }
    function makeProp(id, type) {
        const def = PROPS[type];
        const mats = {light: new THREE.MeshBasicMaterial(), dark: new THREE.MeshBasicMaterial()};
        const prop = {id, type, kind: def.kind, held: null, mats, ...buildProp(type, mats)};
        prop.group.userData.prop = prop;
        scene.add(prop.group); props.set(id, prop);
        return prop;
    }
    function placeProp(prop, p) {
        const fig = p.held && figures.get(p.held.fig);
        if (fig) fig.J['wrist' + p.held.side].add(prop.group); else scene.add(prop.group);
        prop.held = fig ? {...p.held} : null;
        prop.group.position.set(...p.pos);
        prop.group.rotation.set(p.rot[0] * DEG, p.rot[1] * DEG, p.rot[2] * DEG);
        prop.group.scale.setScalar(p.scale);
        prop.group.updateMatrixWorld(true);
    }
    function dropProp(id) {
        const prop = props.get(id);
        if (!prop) return;
        prop.group.removeFromParent();
        prop.group.traverse(o => { if (o.isMesh) o.geometry.dispose(); });
        for (const m of Object.values(prop.mats)) m.dispose();
        props.delete(id); propOrder = propOrder.filter(x => x !== id);
    }
    // The outline ids: each person and each prop has its own number, so their silhouettes get the full stroke.
    function renumber() {
        const key = order.map(id => `${id}:${figures.get(id).gender}:${figures.get(id).build}`).join() + '|' + propOrder.join();
        if (key === structure) return;
        structure = key;
        order.forEach((id, i) => bakeIds(figures.get(id), i + 1, true));
        propOrder.forEach((id, i) => bakePropIds(props.get(id), 100 + i));
    }

    // ---- cameras
    function orbitPose(c, az, elev, dist, target, roll = 0) {
        const a = az * DEG, e = elev * DEG;
        c.position.set(target.x + Math.cos(e) * Math.sin(a) * dist, target.y + Math.sin(e) * dist, target.z + Math.cos(e) * Math.cos(a) * dist);
        c.up.set(-Math.sin(e) * Math.sin(a), Math.cos(e), -Math.sin(e) * Math.cos(a));   // stays continuous over the top
        c.lookAt(target);
        if (roll) c.rotateZ(-roll * DEG);
        c.updateMatrixWorld(true);
    }
    const target = () => new V3(cam.tx, cam.aim, cam.tz);
    function placeShot(aspect) {
        orbitPose(shot, cam.az, cam.elev, cam.dist, target(), cam.roll);
        shot.fov = cam.fov; shot.aspect = aspect; shot.clearViewOffset(); shot.updateProjectionMatrix();
        return shot;
    }
    // The shot camera seen through a stage: `rect` is the picture's place in a stage of w × h, so the overlay drawn
    // with this camera lines up with the layer under it.
    function viewCamera(camera, rect, w, h) {
        placeShot(rect.w / rect.h);
        camera.position.copy(shot.position); camera.quaternion.copy(shot.quaternion); camera.up.copy(shot.up);
        camera.fov = 2 * Math.atan(Math.tan(cam.fov * DEG / 2) * h / rect.h) / DEG;
        camera.aspect = w / h; camera.near = shot.near; camera.far = shot.far;
        camera.setViewOffset(w, h, w / 2 - (rect.x + rect.w / 2), h / 2 - (rect.y + rect.h / 2), w, h);
        camera.updateProjectionMatrix(); camera.updateMatrixWorld(true);
        return camera;
    }
    const cameraAzFrom = pos => Math.atan2(shot.position.x - pos.x, shot.position.z - pos.z) / DEG;

    // ---- line art
    let gl = null;
    function lineGL() {
        if (gl) return gl;
        const renderer = new THREE.WebGLRenderer({antialias: false, alpha: true, premultipliedAlpha: true, preserveDrawingBuffer: false});
        renderer.setPixelRatio(1);
        const idMat = new THREE.ShaderMaterial({uniforms: {uK: {value: 3.4}}, vertexShader: ID_VERTEX,
            fragmentShader: 'varying vec3 vId; varying float vS; void main(){ gl_FragColor = vec4(vId, vS); }'});
        const edgeMat = new THREE.ShaderMaterial({
            uniforms: {tId: {value: null}, uPx: {value: new THREE.Vector2()}, uR: {value: 4}, uMid: {value: 3}, uThin: {value: 2}, uMin: {value: 0.75}},
            vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }',
            fragmentShader: EDGE_FRAGMENT, depthTest: false, depthWrite: false, transparent: true, blending: THREE.NoBlending});
        const quad = new THREE.Scene();
        const mesh = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), edgeMat); mesh.frustumCulled = false; quad.add(mesh);
        const idRT = new THREE.WebGLRenderTarget(4, 4, {minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter, depthBuffer: true});
        gl = {renderer, idMat, edgeMat, quad, quadCam: new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1), idRT, w: 0, h: 0};
        return gl;
    }
    // The layer's picture at w × h: black strokes on a clear ground, drawn larger and made smaller so the strokes are smooth
    // (up to twice the size: about 3 MP on screen, 6 MP for the saved picture, `fine`).
    function lineArt(w, h, fine = false, into = null) {
        w = Math.max(1, Math.round(w)); h = Math.max(1, Math.round(h));
        const out = into && into.width === w && into.height === h ? into : Object.assign(document.createElement('canvas'), {width: w, height: h});
        const ctx = out.getContext('2d');
        ctx.clearRect(0, 0, w, h);
        if (!figures.size && !props.size) return out;
        const g = lineGL();
        const k = Math.min(4096 / Math.max(w, h), Math.max(1, Math.min(2, Math.sqrt((fine ? 6e6 : 3e6) / (w * h)))));
        const W = Math.max(1, Math.round(w * k)), H = Math.max(1, Math.round(h * k));
        if (g.w !== W || g.h !== H) { g.renderer.setSize(W, H, false); g.idRT.setSize(W, H); g.w = W; g.h = H; }
        const camera = placeShot(w / h);
        for (const f of figures.values()) f.group.updateMatrixWorld(true);
        g.idMat.uniforms.uK.value = (H / Math.min(W, H)) * 0.5 / Math.tan(camera.fov * DEG / 2) / 0.407;
        scene.overrideMaterial = g.idMat;
        g.renderer.setRenderTarget(g.idRT); g.renderer.setClearColor(0x000000, 0); g.renderer.clear();
        g.renderer.render(scene, camera);
        scene.overrideMaterial = null;
        const R = Math.max(2.4, 0.0085 * Math.min(W, H));
        g.edgeMat.uniforms.tId.value = g.idRT.texture; g.edgeMat.uniforms.uPx.value.set(1 / W, 1 / H);
        g.edgeMat.uniforms.uR.value = R; g.edgeMat.uniforms.uMid.value = Math.max(1.0, R * 0.62); g.edgeMat.uniforms.uThin.value = Math.max(0.75, R * 0.36);
        g.renderer.setRenderTarget(null); g.renderer.setClearColor(0x000000, 0); g.renderer.clear();
        g.renderer.render(g.quad, g.quadCam);
        ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(g.renderer.domElement, 0, 0, W, H, 0, 0, w, h);
        return out;
    }

    // ---- picking and dragging (rays from any camera that looks through the shot camera)
    const raycaster = new THREE.Raycaster();
    function pick(ndc, camera) {
        raycaster.setFromCamera(ndc, camera);
        const roots = [...order.map(id => figures.get(id).group), ...propOrder.map(id => props.get(id)).filter(p => !p.held).map(p => p.group)];
        for (const h of raycaster.intersectObjects(roots, true)) {
            if (!h.object.isMesh) continue;
            let o = h.object;
            while (o && !o.userData.fig && !o.userData.prop) o = o.parent;
            if (!o) continue;
            if (o.userData.prop) return {prop: o.userData.prop.id, point: h.point};
            // A prop in a hand is part of that hand.
            let held = h.object;
            while (held && !held.userData.prop && held !== o) held = held.parent;
            if (held?.userData.prop) return {prop: held.userData.prop.id, point: h.point};
            return {fig: o.userData.fig.id, joint: h.object.userData.joint || 'root', point: h.point};
        }
        return null;
    }
    function ikHandle(joint) {
        const m = LIMB_OF[joint];
        if (m) return m.role === 'upper' ? {kind: 'pole', limb: m.limb, at: LIMBS[m.limb].lower} : {kind: 'target', limb: m.limb, at: LIMBS[m.limb].end};
        if (joint === 'root' || joint === 'spine' || joint === 'chest') return {kind: 'hips', at: 'root'};
        return {kind: 'look', at: 'head'};
    }
    // Dragging a part: hands and feet follow the pointer, upper arm and thigh swing the elbow or knee, the torso moves the
    // pelvis (feet stay), the head looks at the pointer. whole: the person moves on the floor.
    function beginDrag(hit, camera, whole = false) {
        if (hit.prop) {
            const prop = props.get(hit.prop);
            if (!prop || prop.held) return null;
            const plane = new THREE.Plane(new V3(0, 1, 0), -hit.point.y), start = hit.point.clone(), from = prop.group.position.clone();
            return {move(ndc) {
                raycaster.setFromCamera(ndc, camera);
                const p = raycaster.ray.intersectPlane(plane, new V3());
                if (p) { prop.group.position.set(from.x + p.x - start.x, from.y, from.z + p.z - start.z); prop.group.updateMatrixWorld(true); }
            }};
        }
        const fig = figures.get(hit.fig);
        if (!fig) return null;
        if (whole) {
            const plane = new THREE.Plane(new V3(0, 1, 0), -hit.point.y), start = hit.point.clone(), from = fig.group.position.clone();
            return {move(ndc) {
                raycaster.setFromCamera(ndc, camera);
                const p = raycaster.ray.intersectPlane(plane, new V3());
                if (p) { fig.group.position.set(from.x + p.x - start.x, from.y, from.z + p.z - start.z); fig.apply(); }
            }};
        }
        const h = ikHandle(hit.joint), at = fig.J[h.at].getWorldPosition(new V3());
        const normal = camera.getWorldDirection(new V3());
        const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(normal, hit.point), offset = at.clone().sub(hit.point);
        const base = structuredClone(fig.pose), start = fig.group.worldToLocal(at.clone());
        return {move(ndc) {
            raycaster.setFromCamera(ndc, camera);
            const hitP = raycaster.ray.intersectPlane(plane, new V3());
            if (!hitP) return;
            const p = fig.pose, f = fig.group.worldToLocal(hitP.clone().add(offset));
            if (h.kind === 'target') p.limbs[h.limb].target = f.toArray();
            else if (h.kind === 'pole') p.limbs[h.limb].pole = f.sub(fig.pf(fig.J[LIMBS[h.limb].upper])).toArray();
            else if (h.kind === 'hips') {
                const d = f.sub(start);
                p.root.pos = base.root.pos.map((v, i) => v + d.getComponent(i));
                for (const arm of ['armL', 'armR']) p.limbs[arm].target = base.limbs[arm].target.map((v, i) => v + d.getComponent(i));
            } else {
                // Aim the head at the pointer: 30% of the turn goes to the neck, 70% to the head.
                const chestQ = fig.J.chest.getWorldQuaternion(new Quat()).invert();
                const from = fig.J.head.localToWorld(new V3(0, 0.1, 0));
                const v = hitP.clone().addScaledVector(normal, -0.45).sub(from).applyQuaternion(chestQ);
                const yaw = clamp(Math.atan2(v.x, v.z) / DEG, -95, 95), pitch = clamp(-Math.atan2(v.y, Math.hypot(v.x, v.z)) / DEG, -50, 50);
                p.neck = [pitch * 0.3, yaw * 0.3, p.neck[2]]; p.head = [pitch * 0.7, yaw * 0.7, p.head[2]];
            }
            fig.apply();
        }};
    }
    // A joint turned with the rings: what it did, written back into the pose.
    function jointTurned(figId, joint, cap = null) {
        const fig = figures.get(figId);
        if (!fig) return;
        const p = fig.pose, m = LIMB_OF[joint];
        if (joint === 'root' || joint === 'spine' || joint === 'chest') {
            if (joint === 'root') p.root.rot = eulerDeg(fig.J.root.quaternion); else p[joint] = eulerDeg(fig.J[joint].quaternion);
            fig.applyTorso(); if (cap) restoreArms(fig, cap);
        } else if (joint === 'neck' || joint === 'head') p[joint] = eulerDeg(fig.J[joint].quaternion);
        else if (joint.startsWith('wrist')) p.hands[joint.slice(-1)].rot = eulerDeg(fig.J[joint].quaternion);
        else if (joint.startsWith('ankle')) { fig.group.updateMatrixWorld(true); p.foot[joint.slice(-1)] = clamp(eulerDeg(fig.qf(fig.J[joint]))[0], -80, 80); }
        else if (m) readLimb(fig, m.limb);
        fig.apply();
    }
    // The person turned with the floor ring.
    function figureTurned(figId) {
        const fig = figures.get(figId);
        if (!fig) return;
        const fwd = new V3(0, 0, 1).applyQuaternion(fig.group.quaternion);
        fig.pose.heading = wrap(Math.atan2(fwd.x, fwd.z) / DEG);
        fig.apply();
    }

    // ---- editing
    const subject = id => figures.get(id) || figures.get(order[0]) || null;
    function addFigure(gender) {
        if (figures.size >= MAX_FIGURES) return null;
        placeShot(1);
        // Beside the others, in a row across the picture, facing the camera.
        const right = new V3().setFromMatrixColumn(shot.matrixWorld, 0).setY(0).normalize();
        const centre = new V3(cam.tx, 0, cam.tz);
        let k = 0, spot = centre.clone();
        const taken = p => [...figures.values()].some(f => Math.hypot(f.group.position.x - p.x, f.group.position.z - p.z) < 0.7);
        while (taken(spot) && k < 40) { k++; spot = centre.clone().addScaledVector(right, (k % 2 ? 1 : -1) * Math.ceil(k / 2) * 0.9); }
        const id = newId('f'), fig = new Figure(id, gender, 'standard');
        figures.set(id, fig); order.push(id); scene.add(fig.group);
        fig.group.position.set(spot.x, 0, spot.z);
        fig.pose.heading = wrap(cameraAzFrom(spot));
        fig.buildPose(PRESETS.stand.make, {cameraAz: fig.pose.heading});
        renumber();
        if (!framed()) frameAll();
        return id;
    }
    function removeFigure(id) {
        for (const p of [...props.values()]) if (p.held?.fig === id) dropProp(p.id);
        dropFigure(id); renumber();
    }
    function applyPreset(name, ids) {
        const preset = PRESETS[name];
        if (!preset) return;
        placeShot(1);
        for (const id of ids) {
            const fig = figures.get(id);
            if (!fig) continue;
            const held = [...props.values()].filter(p => p.held?.fig === id).map(p => [p.held.side, fig.pose.hands[p.held.side].shape]);
            fig.buildPose(preset.make, {cameraAz: cameraAzFrom(fig.group.position)});
            for (const [side, shape] of held) fig.pose.hands[side].shape = shape;
            fig.apply();
        }
    }
    function setHands(shape, ids, side = null) {
        if (!Object.hasOwn(HAND_SHAPES, shape)) return;
        for (const id of ids) {
            const fig = figures.get(id);
            if (!fig) continue;
            for (const s of side ? [side] : ['L', 'R']) fig.pose.hands[s].shape = shape;
            fig.apply();
        }
    }
    function addProp(type, holder = null) {
        const def = PROPS[type];
        if (!def || props.size >= MAX_PROPS) return null;
        const id = newId('p'), prop = makeProp(id, type);
        propOrder.push(id);
        const fig = def.kind === 'hand' ? subject(holder?.fig) : null;
        if (fig) {
            // Into the hand that was picked, else the right hand when it is free, else the left.
            const busy = s => [...props.values()].some(p => p !== prop && p.held?.fig === fig.id && p.held.side === s);
            const side = holder?.side || (busy('R') && !busy('L') ? 'L' : 'R');
            for (const p of [...props.values()]) if (p !== prop && p.held?.fig === fig.id && p.held.side === side) dropProp(p.id);
            const s = side === 'L' ? 1 : -1, h = def.hold;
            fig.J['wrist' + side].add(prop.group);
            prop.group.position.set(-s * h.pos[0], h.pos[1], h.pos[2]);
            prop.held = {fig: fig.id, side};
            fig.pose.hands[side].shape = h.hand;
            fig.apply();
        } else {
            // On the floor, in front of the camera's aim, a little behind the people for furniture.
            placeShot(1);
            const fwd = new V3().setFromMatrixColumn(shot.matrixWorld, 2).negate().setY(0).normalize();
            const at = new V3(cam.tx, 0, cam.tz).addScaledVector(fwd, def.kind === 'scene' ? 1.1 : -0.4);
            prop.group.position.set(at.x, def.kind === 'hand' ? 0.9 : 0, at.z);
            prop.group.rotation.y = Math.atan2(-fwd.x, -fwd.z);
        }
        prop.group.updateMatrixWorld(true);
        renumber();
        return id;
    }
    function removeProp(id) { dropProp(id); renumber(); }
    // Back to the start: every person standing in a row facing the camera, the camera in front, no props.
    function reset() {
        for (const id of [...propOrder]) dropProp(id);
        Object.assign(cam, DEFAULT_CAMERA);
        placeShot(1);
        const n = order.length;
        order.forEach((id, i) => {
            const fig = figures.get(id);
            fig.group.position.set((i - (n - 1) / 2) * 0.9, 0, 0);
            fig.scale = 1;
            fig.pose = BASE(); fig.pose.heading = 0;
            fig.buildPose(PRESETS.stand.make, {cameraAz: 0});
        });
        renumber();
        if (n && !framed()) frameAll();
    }
    function cameraPreset(name, figId = null) {
        const c = CAMERA_PRESETS[name];
        if (!c) return;
        const fig = subject(figId);
        Object.assign(cam, {az: c.az === null ? cam.az : wrap((fig ? fig.pose.heading : 0) + c.az), elev: c.elev, fov: c.fov, roll: 0});
        frameAll();
    }
    // Every person whole in the picture, at the camera's angle (az, elev and lens kept).
    function frameAll(aspect = lastAspect) {
        const box = new THREE.Box3();
        for (const f of figures.values()) { f.group.updateMatrixWorld(true); box.expandByObject(f.group); }
        if (box.isEmpty()) { Object.assign(cam, {tx: 0, tz: 0, aim: DEFAULT_CAMERA.aim}); return; }
        const sphere = box.getBoundingSphere(new THREE.Sphere());
        cam.tx = sphere.center.x; cam.tz = sphere.center.z; cam.aim = sphere.center.y;
        const v = cam.fov * DEG / 2, hFov = Math.atan(Math.tan(v) * aspect);
        cam.dist = clamp(sphere.radius / Math.sin(Math.min(v, hFov)) * 1.04, 0.6, 40);
    }
    let lastAspect = 1;
    function setAspect(aspect) { lastAspect = aspect > 0 ? aspect : 1; }
    // Whether every person is in the picture from head to foot.
    function framed() {
        const camera = placeShot(lastAspect);
        for (const f of figures.values()) for (const p of keyPoints(f)) {
            const q = p.clone().project(camera);
            if (Math.abs(q.x) > 0.98 || Math.abs(q.y) > 0.98 || q.z > 1) return false;
        }
        return true;
    }
    function orbit(dAz, dElev) {
        const flip = Math.cos(cam.elev * DEG) < 0 ? -1 : 1;
        cam.az = wrap(cam.az - dAz * flip); cam.elev = wrap(cam.elev + dElev);
    }
    function dolly(k) { cam.dist = clamp(cam.dist * k, 0.3, 40); }
    function pan(dx, dy, viewHeight) {
        placeShot(lastAspect);
        const k = 2 * Math.tan(cam.fov * DEG / 2) * cam.dist / Math.max(1, viewHeight);
        const right = new V3().setFromMatrixColumn(shot.matrixWorld, 0), up = new V3().setFromMatrixColumn(shot.matrixWorld, 1);
        const t = target().addScaledVector(right, -dx * k).addScaledVector(up, dy * k);
        cam.tx = t.x; cam.aim = t.y; cam.tz = t.z;
    }

    // ---- words for the prompt
    function keyPoints(fig) {
        const J = fig.J, at = (j, local = [0, 0, 0]) => J[j].localToWorld(new V3(...local));
        return [at('head', [0, 0.24, 0]), at('head', [0, 0.01, 0.08]), at('chest', [0, 0.12, 0]), at('root'),
            at('kneeL'), at('kneeR'), at('ankleL', [0, -0.07, 0.1]), at('ankleR', [0, -0.07, 0.1]), at('ankleL', [0, -0.07, -0.05]), at('ankleR', [0, -0.07, -0.05])];
    }
    // One clause per person (all affirmative), the framing, and the angle of the shot. aspect: the picture's width / height.
    function words(aspect) {
        setAspect(aspect);
        const camera = placeShot(aspect);
        const shown = order.map(id => figures.get(id));
        if (!shown.length) return null;
        const camRight = new V3().setFromMatrixColumn(camera.matrixWorld, 0).setY(0).normalize();
        const flat = v => v.setY(0).normalize();
        const people = shown.map(fig => {
            fig.group.updateMatrixWorld(true);
            const pelvis = fig.J.root.getWorldPosition(new V3()), headAt = fig.J.head.getWorldPosition(new V3());
            const body = flat(new V3(0, 0, 1).transformDirection(fig.J.chest.matrixWorld));
            const head = flat(new V3(0, 0, 1).transformDirection(fig.J.head.matrixWorld));
            const left = flat(new V3(1, 0, 0).transformDirection(fig.J.chest.matrixWorld));
            const toCam = flat(camera.position.clone().sub(pelvis)), toCamHead = flat(camera.position.clone().sub(headAt));
            const angle = (a, b) => Math.acos(clamp(a.dot(b), -1, 1)) / DEG;
            const side = v => (v.dot(camRight) >= 0 ? '画面右侧' : '画面左侧');
            const x = pelvis.clone().project(camera).x;
            const raised = ['wristL', 'wristR'].filter(j => fig.J[j].getWorldPosition(new V3()).y > headAt.y + 0.24).length;
            return {x, raised, a: angle(body, toCam), head: angle(head, toCamHead), turn: angle(head, body), bodySide: side(body), headSide: side(head),
                own: toCam.dot(left) > 0 ? '左' : '右', sitting: fig.pose.chair || fig.pose.root.pos[1] < 0.72, gender: fig.gender};
        }).sort((p, q) => p.x - q.x);
        // genders: each mannequin's body, left to right as the picture shows them (the page names them when the description does not).
        return {facing: facingWords(people), arms: armWords(people), framing: framingWords(shown, camera), angle: angleWords(), count: people.length, genders: people.map(p => p.gender)};
    }
    function angleWords() {
        const e = wrap(cam.elev);
        return e > 70 && e < 110 ? '从正上方俯拍' : e > 28 ? '高角度俯拍' : e < -12 ? '低角度仰拍' : '';
    }
    function framingWords(shown, camera) {
        const inside = p => { const q = p.clone().project(camera); return Math.abs(q.x) <= 1.01 && Math.abs(q.y) <= 1.01 && q.z < 1; };
        let level = 4;
        for (const fig of shown) {
            const k = keyPoints(fig).map(inside);
            if (!k[0]) return '';
            const own = k.slice(6).every(Boolean) ? 4 : k[4] && k[5] ? 3 : k[3] ? 2 : k[2] ? 1 : 0;
            level = Math.min(level, own);
        }
        const n = shown.length, who = n === 1 ? '人物' : `${COUNT[n]}个人`;
        if (n === 1) return ['脸部特写，人物的脸占满画面', '近景，人物胸部以上在画面里', '半身中景，人物从头到腰部在画面里', '中景，人物从头到膝盖在画面里', '全身照，人物从头到脚都在画面里'][level];
        return [`脸部特写，${who}的脸都在画面里`, `近景，${who}胸部以上都在画面里`, `半身中景，${who}从头到腰部都在画面里`, `中景，${who}从头到膝盖都在画面里`, `全身中景，${who}从头到脚都在画面里`][level];
    }
    return {
        scene, sync, json, lineArt, pick, beginDrag, jointTurned, figureTurned, captureArms: id => (figures.get(id) ? captureArms(figures.get(id)) : null),
        addFigure, removeFigure, applyPreset, setHands, addProp, removeProp, reset, cameraPreset, frameAll, framed, orbit, dolly, pan, setAspect, words,
        viewCamera, placeShot, camera: cam,
        figure: id => figures.get(id) || null, prop: id => props.get(id) || null, figureIds: () => order.slice(), propIds: () => propOrder.slice(),
        jointObject: (figId, joint) => figures.get(figId)?.J[joint] || null,
        dispose() {
            for (const id of [...figures.keys()]) dropFigure(id);
            for (const id of [...props.keys()]) dropProp(id);
            if (gl) { gl.idRT.dispose(); gl.idMat.dispose(); gl.edgeMat.dispose(); gl.renderer.dispose(); gl.renderer.forceContextLoss(); gl = null; }
        },
    };
}

// A hand held above the head is said in words (a description of the place or the clothes otherwise pulls the arm down).
function armWords(people) {
    const n = people.length;
    const names = n === 1 ? ['人物'] : n === 2 ? ['左边的人', '右边的人'] : n === 3 ? ['左边的人', '中间的人', '右边的人'] : people.map((_, i) => `从左数第${COUNT[i + 1]}个人`);
    return people.map((p, i) => (p.raised === 1 ? `${names[i]}一只手臂向斜上方抬起，手高过头顶` : p.raised === 2 ? `${names[i]}双臂向上举起，手高过头顶` : '')).filter(Boolean).join('，');
}
const COUNT = ['', '一', '两', '三', '四', '五', '六', '七', '八'];
// Which way each person faces. Single: "人物面对镜头". Several of one kind: "两个人都侧身对着镜头，左边的人朝向画面右侧，…".
function facingWords(people) {
    const n = people.length;
    const names = n === 1 ? ['人物'] : n === 2 ? ['左边的人', '右边的人'] : n === 3 ? ['左边的人', '中间的人', '右边的人'] : people.map((_, i) => `从左数第${COUNT[i + 1]}个人`);
    const kind = p => (p.a < 30 ? 'front' : p.a < 65 ? 'oblique' : p.a < 115 ? 'side' : p.a < 155 ? 'away' : 'back');
    // The head, when it is turned from the body (or a side view, where the profile is what the picture shows).
    const headWords = (p, k, alone) => {
        if (k === 'back' || k === 'away') {
            if (p.head < 45) return '回头看向镜头';
            if (p.head < 120) return `头转向${p.headSide}回头看，露出侧脸`;
            return '';
        }
        if (k === 'side') {
            if (p.head < 35) return '脸转向镜头';
            if (p.head < 120 && (alone || p.turn > 30)) return `侧脸对着镜头，目光看向${p.headSide}`;
            return p.turn > 30 ? `头转向${p.headSide}` : '';
        }
        if (p.turn < 30) return '';
        return p.head < 35 ? '脸转向镜头' : p.head < 120 ? `头转向${p.headSide}，露出侧脸` : `头转向${p.headSide}`;
    };
    const sit = p => (p.sitting ? '坐着' : '');
    const one = (p, name, alone) => {
        const k = kind(p), extra = headWords(p, k, alone);
        const body = {
            front: `${name}面对镜头${sit(p)}`,
            oblique: `${name}斜对镜头${sit(p)}，身体朝向${p.bodySide}`,
            side: `${name}侧身${sit(p)}，身体${p.own}侧对着镜头${alone ? '' : `，朝向${p.bodySide}`}`,
            away: `${name}的身体斜背对镜头${sit(p)}，能看到后背`,
            back: `${name}的身体背对镜头${sit(p)}，能看到后背`,
        }[k];
        return extra ? `${body}，${extra}` : body;
    };
    if (n === 1) return one(people[0], '人物', true);
    const kinds = people.map(kind);
    if (kinds.every(k => k === kinds[0])) {
        const k = kinds[0], seated = people.every(p => p.sitting) ? '坐着' : '';
        const lead = {front: `都面对镜头${seated}`, oblique: `都斜对镜头${seated}`, side: `都侧身${seated}对着镜头`,
            away: `的身体都斜背对镜头${seated}，能看到后背`, back: `的身体都背对镜头${seated}，能看到后背`}[k];
        const turned = k === 'oblique' || k === 'side';
        const extras = people.map(p => headWords(p, k, false));
        // Everyone turned the same way, nobody seated alone and no head turned: said once.
        if (turned && people.every(p => p.bodySide === people[0].bodySide) && !extras.some(Boolean) && (seated || !people.some(p => p.sitting)))
            return `${COUNT[n]}个人${lead}，身体都朝向${people[0].bodySide}`;
        const tails = people.map((p, i) => {
            const parts = [];
            if (p.sitting && !seated) parts.push('坐着');
            if (turned) parts.push(`朝向${p.bodySide}`);
            if (extras[i]) parts.push(extras[i]);
            return parts.length ? names[i] + parts.join('，') : '';
        }).filter(Boolean);
        return [`${COUNT[n]}个人${lead}`, ...tails].join('，');
    }
    return people.map((p, i) => one(p, names[i], false)).join('；');
}
