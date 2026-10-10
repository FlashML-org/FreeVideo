import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

// Keep the production closure (including any request ownership state) intact.
// The deferred decoder supplies only the native Image boundary to order replies.
const source = await readFile(process.argv[2] || new URL('../web/studio.js', import.meta.url), 'utf8');
const start = source.indexOf('    async function inputRatio(');
const ownership = source.lastIndexOf('    let inputRatioGeneration', start);
const block = source.slice(ownership < 0 ? start : ownership, source.indexOf("    for (const [id, label, r]", start));
const buttons = source.slice(source.indexOf('    for (const [id, label, r]', start), source.indexOf('    mp.onchange', start));
const mediaChanged = source.slice(source.indexOf('    const mediaChanged = async e =>', start), source.indexOf('    const resultChanged', start));
function fixture() {
    let assets = '[]';
    const pending = [], ratios = [];
    class Image {
        decode() { return new Promise((resolve, reject) => pending.push({
            src: this.src,
            finish: (width, height) => { this.naturalWidth = width; this.naturalHeight = height; resolve(); },
            fail: () => reject(new Error('image decode failed')),
        })); }
    }
    const controller = new Function('mediaNode', 'value', 'view', 'Image', 't', 'ratios', `
        let selected = 'input', disposed = false, ratio = 1;
        let pixels = 1; const mp = {value: '1'}, dimensionsLinked = false;
        const shapes = {children: [], append(item) { this.children.push(item); }};
        const button = (_label, action) => ({action, dataset: {}, setAttribute() {}, append() {}});
        const el = () => ({}), status = {textContent: '', dataset: {}}, updateCanvas = () => {};
        function applySize() { ratios.push(ratio); }
        ${block}
        ${buttons}
        ${mediaChanged}
        return {inputRatio, mediaChanged, shapes, status, select(value) { selected = value; }, dispose() { disposed = true; }};
    `)({}, () => assets, value => value, Image, en => en, ratios);
    return {...controller, pending, ratios, media(file) { assets = file ? JSON.stringify([{file, role: 'first', enabled: true}]) : '[]'; }};
}

test('older image decode cannot overwrite the newly selected image ratio', async () => {
    const f = fixture();
    f.media('landscape.png'); const older = f.inputRatio(true);
    f.media('portrait.png'); const newer = f.inputRatio(true);
    f.pending[1].finish(320, 640); await newer;
    f.pending[0].finish(640, 320); await older;
    assert.deepEqual(f.ratios, [0.5]);
});

test('removing the source invalidates its pending decode', async () => {
    const f = fixture();
    f.media('landscape.png'); const older = f.inputRatio();
    f.media(null); await f.inputRatio();
    f.pending[0].finish(640, 320); await older;
    assert.deepEqual(f.ratios, []);
});

test('obsolete decode errors do not replace the latest image result', async () => {
    const f = fixture();
    f.media('broken.png'); const older = f.inputRatio(true);
    const handled = assert.doesNotReject(older);
    f.media('portrait.png'); const newer = f.inputRatio(true);
    f.pending[1].finish(320, 640); await newer;
    f.pending[0].fail(); await handled;
    assert.deepEqual(f.ratios, [0.5]);
});

test('changing aspect mode or closing the studio suppresses pending results', async () => {
    for (const action of ['select', 'dispose']) {
        const f = fixture();
        f.media('landscape.png'); const pending = f.inputRatio();
        if (action === 'select') f.select('9:16'); else f.dispose();
        f.pending[0].finish(640, 320); await pending;
        assert.deepEqual(f.ratios, []);
    }
});

test('current image and current decoder failures keep their original behavior', async () => {
    const f = fixture();
    f.media('landscape.png'); const success = f.inputRatio(true);
    f.pending[0].finish(640, 320); await success;
    assert.deepEqual(f.ratios, [2]);
    const failure = f.inputRatio(true), handled = assert.rejects(failure, /image decode failed/);
    f.pending[1].fail(); await handled;
    f.media(null); await assert.rejects(f.inputRatio(true), /Add an image/);
});

test('a stale Match image click cannot clear the newer decode error', async () => {
    const f = fixture();
    f.media('old.png'); const oldClick = f.shapes.children[3].action();
    f.media('broken.png'); const latest = f.mediaChanged({detail: undefined});
    f.pending[1].fail(); await latest;
    assert.equal(f.status.textContent, 'image decode failed');
    f.pending[0].finish(640, 320); await oldClick;
    assert.equal(f.status.textContent, 'image decode failed');
    assert.deepEqual(f.ratios, []);
});
