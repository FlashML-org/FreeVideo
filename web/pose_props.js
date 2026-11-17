// Props of the sketch editor's pose tool, generated in code in the same plain style as the mannequin. Scene props stand
// on the floor (origin at floor level); hand props have their origin at the grip and go into a hand. The list follows
// a count of the objects named in example prompts. label: [English, Chinese].
import * as THREE from './vendor/three/three.module.min.js';

const DEG = Math.PI / 180;
const box = (w, h, d) => new THREE.BoxGeometry(w, h, d);
const cyl = (r0, r1, h, seg = 24) => new THREE.CylinderGeometry(r0, r1, h, seg);

// Each builder receives add(geometry, [x, y, z], [rx, ry, rz] in degrees, dark?).
export const PROPS = {
    table: { label: ['Table', '桌子'], kind: 'scene', build(add) {
        add(box(1.2, 0.04, 0.7), [0, 0.74, 0]);
        for (const x of [-0.54, 0.54]) for (const z of [-0.29, 0.29]) add(box(0.05, 0.72, 0.05), [x, 0.36, z]);
    } },
    window: { label: ['Wall and window', '墙和窗'], kind: 'scene', build(add) {
        add(box(0.7, 2.5, 0.1), [-0.85, 1.25, 0]); add(box(0.7, 2.5, 0.1), [0.85, 1.25, 0]);
        add(box(1.0, 0.9, 0.1), [0, 0.45, 0]); add(box(1.0, 0.5, 0.1), [0, 2.25, 0]);
        add(box(1.1, 0.04, 0.2), [0, 0.9, 0.03]);
        add(box(0.03, 1.1, 0.04), [0, 1.45, 0], null, true); add(box(1.0, 0.03, 0.04), [0, 1.45, 0], null, true);
    } },
    door: { label: ['Wall and door', '墙和门'], kind: 'scene', build(add) {
        add(box(0.65, 2.5, 0.1), [-0.775, 1.25, 0]); add(box(0.65, 2.5, 0.1), [0.775, 1.25, 0]);
        add(box(0.9, 0.45, 0.1), [0, 2.275, 0]);
        const leaf = add(box(0.86, 2.03, 0.04), [-0.45 + 0.43 * Math.cos(38 * DEG), 1.015, 0.43 * Math.sin(38 * DEG)], [0, -38, 0]);
        add(new THREE.SphereGeometry(0.03, 16, 12), [-0.45 + 0.78 * Math.cos(38 * DEG), 1.0, 0.78 * Math.sin(38 * DEG) + 0.04], null, true);
        return leaf;
    } },
    bed: { label: ['Bed', '床'], kind: 'scene', build(add) {
        add(box(1.5, 0.28, 2.0), [0, 0.14, 0]); add(box(1.44, 0.16, 1.94), [0, 0.36, 0]);
        add(box(1.5, 0.95, 0.06), [0, 0.475, -1.0]); add(box(0.56, 0.1, 0.36), [0, 0.49, -0.72]);
    } },
    sofa: { label: ['Sofa', '沙发'], kind: 'scene', build(add) {
        add(box(1.8, 0.4, 0.85), [0, 0.2, 0]); add(box(1.8, 0.48, 0.2), [0, 0.64, -0.325]);
        for (const x of [-0.8, 0.8]) add(box(0.2, 0.26, 0.85), [x, 0.53, 0]);
        for (const x of [-0.35, 0.35]) add(box(0.68, 0.1, 0.6), [x, 0.45, 0.1]);
    } },
    mirror: { label: ['Mirror', '镜子'], kind: 'scene', build(add) {
        add(box(0.62, 1.72, 0.04), [0, 0.9, 0], [-6, 0, 0]);
        add(box(0.52, 1.6, 0.012), [0, 0.9, 0.022], [-6, 0, 0], true);
        add(box(0.04, 1.1, 0.04), [0, 0.56, -0.22], [14, 0, 0]);
    } },
    car: { label: ['Car', '汽车'], kind: 'scene', build(add) {
        const side = pts => { const s = new THREE.Shape(pts.map(p => new THREE.Vector2(...p))); return new THREE.ExtrudeGeometry(s, { depth: 1.7, bevelEnabled: false }).translate(0, 0, -0.85); };
        add(side([[-2.1, 0.3], [2.1, 0.3], [2.1, 0.72], [1.95, 0.86], [-2.0, 0.9], [-2.1, 0.75]]), [0, 0, 0]);
        add(side([[-1.5, 0.9], [-1.0, 1.42], [0.5, 1.42], [1.15, 0.88]]), [0, 0, 0], null, false, [1, 1, 0.9]);
        for (const x of [-1.3, 1.3]) for (const z of [-0.78, 0.78]) add(cyl(0.33, 0.33, 0.22, 28), [x, 0.33, z], [90, 0, 0], true);
    } },
    // ---- hand props. With the arm hanging, -Y runs along the fingers and +Z forward; a gripped stick lies along Z.
    camera: { label: ['Camera', '相机'], kind: 'hand', hold: { pos: [0.065, -0.07, 0], hand: 'cup' }, build(add) {
        add(box(0.135, 0.06, 0.09), [0, 0, 0]); add(cyl(0.036, 0.036, 0.06), [0, -0.058, 0], null, true);
        add(box(0.05, 0.04, 0.02), [0, 0, 0.055]);
    } },
    bag: { label: ['Bag', '包'], kind: 'hand', hold: { pos: [0.02, -0.085, 0], hand: 'fist' }, build(add) {
        add(box(0.11, 0.26, 0.36), [0, -0.3, 0]);
        add(new THREE.TorusGeometry(0.1, 0.008, 8, 28, Math.PI), [0, -0.17, 0], [0, 90, 0], true);
    } },
    phone: { label: ['Phone', '手机'], kind: 'hand', hold: { pos: [0.02, -0.07, 0.005], hand: 'cup' }, build(add) {
        add(box(0.009, 0.15, 0.072), [0, 0, 0]);
    } },
    cup: { label: ['Cup', '杯子'], kind: 'hand', hold: { pos: [0.045, -0.07, 0.02], hand: 'cup' }, build(add) {
        add(cyl(0.04, 0.034, 0.1), [0, 0, 0], [90, 0, 0]);
        add(new THREE.TorusGeometry(0.028, 0.007, 8, 20), [0, 0.045, 0], [0, 90, 0]);
    } },
    sword: { label: ['Sword', '剑'], kind: 'hand', hold: { pos: [0.03, -0.075, 0], hand: 'fist' }, build(add) {
        add(cyl(0.014, 0.014, 0.16, 12), [0, 0, 0], [90, 0, 0], true);
        add(box(0.02, 0.15, 0.022), [0, 0, 0.09]); add(box(0.008, 0.045, 0.82), [0, 0, 0.51]);
    } },
    mic: { label: ['Microphone', '话筒'], kind: 'hand', hold: { pos: [0.03, -0.075, 0], hand: 'fist' }, build(add) {
        add(cyl(0.013, 0.017, 0.17, 16), [0, 0, 0.01], [90, 0, 0], true);
        add(new THREE.SphereGeometry(0.03, 20, 14), [0, 0, 0.12]);
    } },
    book: { label: ['Book', '书'], kind: 'hand', hold: { pos: [0.03, -0.075, 0.01], hand: 'cup' }, build(add) {
        add(box(0.03, 0.21, 0.15), [0, 0, 0]);
    } },
};

export function buildProp(type, mats) {
    const def = PROPS[type], group = new THREE.Group(), meshes = [];
    let pid = 1;
    def.build((geom, pos, rot, dark, scale) => {
        const m = new THREE.Mesh(geom, dark ? mats.dark : mats.light);
        m.position.set(...pos);
        if (rot) m.rotation.set(rot[0] * DEG, rot[1] * DEG, rot[2] * DEG);
        if (scale) m.scale.set(...scale);
        m.castShadow = true;
        m.userData = { pid: pid++, dark: !!dark };
        group.add(m); meshes.push(m);
        return m;
    });
    return { group, meshes };
}

// Outline ids for a prop: its own "person" number, so its silhouette gets the full stroke.
export function bakePropIds(prop, number) {
    for (const m of prop.meshes) {
        const n = m.geometry.attributes.position.count, a = new Float32Array(n * 3);
        for (let i = 0; i < n; i++) { a[i * 3] = number / 255; a[i * 3 + 1] = m.userData.pid / 255; }
        m.geometry.setAttribute('aId', new THREE.BufferAttribute(a, 3));
    }
}
