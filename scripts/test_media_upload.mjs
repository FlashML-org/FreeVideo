import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

// Execute the production media editor with only its browser/ComfyUI boundary
// supplied by this fixture. Card event handlers and upload serialization are real.
const source = await readFile(new URL('../web/freevideo.js', import.meta.url), 'utf8');
const editor = source.slice(source.indexOf('function el('), source.indexOf('\nfunction resultPanel('));
class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.classList = {toggle() {}}; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
    setAttribute(key, value) { this.attributes[key] = value; }
    querySelectorAll() { return []; }
    get childElementCount() { return this.children.length; }
}
function fixture(rows) {
    let finish, requests = 0;
    const api = {apiURL: value => value, fetchApi: () => { requests++; return new Promise(resolve => { finish = resolve; }); }};
    const events = new EventTarget();
    const CustomEvent = class extends Event { constructor(name, options) { super(name); this.detail = options.detail; } };
    const mediaPanel = new Function('api', 'document', 'window', 'CustomEvent', 'text', `${editor}\nreturn mediaPanel;`)(
        api, {createElement: tag => new Element(tag)}, events, CustomEvent, en => en);
    const assets = {name: 'assets', value: JSON.stringify(rows)};
    const node = {id: 17, widgets: [assets], inputs: [], graph: {change() {}}};
    const mount = new Element('mount');
    const dispose = mediaPanel(node, mount), panel = mount.children[0];
    const picker = panel.children[0].children[3], cards = panel.children[5];
    return {
        node, cards, dispose, rows: () => JSON.parse(assets.value),
        replace(rows) { assets.value = JSON.stringify(rows); events.dispatchEvent(new CustomEvent('freevideo-media', {detail: node.id})); },
        async upload() {
            picker.files = [new File(['image'], 'new.png', {type: 'image/png'})];
            const promise = picker.onchange();
            assert.equal(requests, 1); assert.equal(node.isUploading, true);
            return {complete: async () => { finish({ok: true, json: async () => ({file: 'uploads/new.png'})}); await promise; }};
        },
    };
}
const row = (file, enabled = true) => ({file, role: 'first', enabled});

for (const edit of ['remove', 'disable', 'reorder']) {
    test(`finishing an upload preserves ${edit} edits`, async () => {
        const f = fixture([row('a.png'), row('b.png')]);
        try {
            const upload = await f.upload();
            if (edit === 'remove') f.cards.children[0].children[2].children[2].onclick();
            if (edit === 'disable') {
                const checkbox = f.cards.children[0].children[0].children[0];
                checkbox.checked = false; checkbox.onchange();
            }
            if (edit === 'reorder') f.cards.children[0].children[2].children[1].onclick();
            const edited = f.rows();
            await upload.complete();
            assert.deepEqual(f.rows(), [...edited, {file: 'uploads/new.png', role: 'reference', enabled: true}], edit);
            assert.equal(f.node.isUploading, false);
        } finally { f.dispose(); }
    });
}

test('upload rechecks the 32-item capacity after another editor changes assets', async () => {
    const f = fixture([row('a.png')]);
    try {
        const upload = await f.upload();
        const full = Array.from({length: 32}, (_, i) => row(`${i}.png`));
        f.replace(full);
        await upload.complete();
        assert.deepEqual(f.rows(), full, 'never overwrite the current full media list');
        assert.equal(f.node.isUploading, false);
    } finally { f.dispose(); }
});

test('an ordinary upload still appends media and releases serialization', async () => {
    const f = fixture([row('a.png')]);
    try {
        const upload = await f.upload();
        assert.throws(() => f.node.widgets[0].serializeValue(), /Wait for media uploads/);
        await upload.complete();
        assert.deepEqual(f.rows().map(r => r.file), ['a.png', 'uploads/new.png']);
        assert.equal(f.node.widgets[0].serializeValue(), JSON.stringify(f.rows()));
    } finally { f.dispose(); }
});
