import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
const source = await readFile(new URL('../web/studio_queue.js', import.meta.url), 'utf8');
const {createStudioQueue} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const snapshot = {output: {'1': {class_type: 'FreeVideoGenerate', inputs: {seed: 3}}}, workflow: {nodes: []}};
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; }
function api(queuePrompt) {
    return {queuePrompt: (_number, snapshot) => queuePrompt(snapshot), fetchApi: async () => ({ok: true, json: async () => ({queue_running: [], queue_pending: []})}),
        addEventListener() {}, removeEventListener() {}};
}
test('disposing during an acknowledgement stops the remaining GPU batch', async () => {
    const reply = deferred(); let submissions = 0;
    const queue = createStudioQueue(api(async () => { submissions++; return submissions === 1 ? reply.promise : {prompt_id: String(submissions)}; }), '1', {pollMs: 0, random: () => 4});
    const started = queue.start(snapshot, {count: 3});
    assert.equal(submissions, 1);
    queue.dispose(); reply.resolve({prompt_id: '1'}); await started;
    assert.equal(submissions, 1);
});
test('disposed controllers reject new work', async () => {
    let submissions = 0;
    const queue = createStudioQueue(api(async () => { submissions++; return {prompt_id: '1'}; }), '1', {pollMs: 0});
    queue.dispose(); await assert.rejects(queue.start(snapshot), /disposed/); assert.equal(submissions, 0);
});
test('live controllers still submit the entire batch with isolated seeds', async () => {
    const submitted = [];
    const queue = createStudioQueue(api(async value => { submitted.push(value); return {prompt_id: String(submitted.length)}; }), '1', {pollMs: 0, random: () => 4});
    await queue.start(snapshot, {count: 3}); queue.dispose();
    assert.equal(submitted.length, 3); assert.deepEqual(submitted.map(p => p.output['1'].inputs.seed), [4, 5, 4]);
    assert.equal(snapshot.output['1'].inputs.seed, 3);
});
