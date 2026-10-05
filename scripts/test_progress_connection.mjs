import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
const source = await readFile(new URL('../web/progress_connection.js', import.meta.url), 'utf8');
const {createProgressConnection} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; }
function fixture() {
    const reply = deferred(), consumed = [];
    const connection = createProgressConnection({fetchApi: async () => ({ok: true, json: () => reply.promise}), addEventListener() {}, removeEventListener() {}}, row => { consumed.push(row); });
    return {connection, reply, consumed};
}
const row = (stream_id, sequence, node = 1) => ({node, stream_id, sequence, phase: 'sampling'});
test('a delayed old-server snapshot cannot replace a new live stream', async () => {
    const {connection, reply, consumed} = fixture(); const pending = connection.refresh();
    connection.receive(row('new-server', 1)); reply.resolve({progress: [row('old-server', 99)]}); await pending;
    assert.deepEqual(consumed.map(r => r.stream_id), ['new-server']);
});
for (const action of ['stop', 'reset']) test(`${action} invalidates an in-flight snapshot`, async () => {
    const {connection, reply, consumed} = fixture(); const pending = connection.refresh(); connection[action]();
    reply.resolve({progress: [row('old-server', 1)]}); await pending; assert.equal(consumed.length, 0);
});
test('a concurrent live update does not suppress other nodes in the snapshot', async () => {
    const {connection, reply, consumed} = fixture(); const pending = connection.refresh();
    connection.receive(row('new-server', 1)); reply.resolve({progress: [row('old-server', 99), row('new-server', 2, 2)]}); await pending;
    assert.deepEqual(consumed.map(r => r.node), [1, 2]);
});
test('unconsumed snapshots remain eligible for recovery', async () => {
    let available = false, count = 0;
    const message = row('server', 1);
    const connection = createProgressConnection({fetchApi: async () => ({ok: true, json: async () => ({progress: [message]})})}, () => { if (!available) return false; count++; });
    await connection.refresh(); available = true; await connection.refresh(); assert.equal(count, 1);
    await connection.refresh(); assert.equal(count, 1);
});

for (const action of ['stop', 'reset']) test(`${action} permits a new poll before the old response settles`, async () => {
    const replies = [deferred(), deferred()], signals = [], consumed = [];
    const connection = createProgressConnection({
        fetchApi: async (_path, options) => {
            const index = signals.length; signals.push(options.signal);
            return {ok: true, json: () => replies[index].promise};
        }, addEventListener() {}, removeEventListener() {},
    }, message => { consumed.push(message); });
    const old = connection.refresh(); connection[action]();
    assert.equal(signals[0].aborted, true);
    if (action === 'stop') connection.start();
    const current = connection.refresh(); assert.equal(signals.length, 2);
    // An obsolete request finishing must not release the newer poll's lock.
    replies[0].resolve({progress: [row('old-server', 99)]}); await old;
    const joined = connection.refresh(); assert.equal(signals.length, 2);
    replies[1].resolve({progress: [row('new-server', 1)]}); await Promise.all([current, joined]);
    connection.stop(); assert.deepEqual(consumed.map(message => message.stream_id), ['new-server']);
});
