import assert from 'node:assert/strict';
import test from 'node:test';
import {estimateSpeech, fitStatus, spokenLines, suggestDuration, wordsForDuration} from '../web/dialog_length.js';

test('Hi fits a 1.625 s clip', () => {
    const hi = estimateSpeech('Hi', {rate: 'film', slack: 0.5, raw: true});
    assert.ok(hi.needed < 1.625, hi);
    assert.equal(suggestDuration(hi.needed), 1.625);
    assert.equal(fitStatus(hi.needed, 1.625), 'fits');
});

test('a long greeting needs about 5 s', () => {
    const line = 'Hi, how are you today, I have not seen you lately!';
    const estimate = estimateSpeech(line, {rate: 'film', slack: 0.5, raw: true});
    assert.ok(estimate.needed > 1.625, estimate);
    assert.ok(estimate.needed < 8, estimate);
    assert.equal(suggestDuration(estimate.needed), 5);
    assert.equal(fitStatus(estimate.needed, 1.625), 'over');
    assert.equal(fitStatus(estimate.needed, 5), 'tight');
    assert.equal(fitStatus(estimateSpeech(line, {rate: 'film', slack: 0, raw: true}).needed, 5), 'fits');
});

test('counts <d> lines from an H3 prompt and ignores the rest', () => {
    const prompt = 'summary: a room\n\nThe woman (S1) says: <d>[English] Hi</d>\n\noverall_soundscape: quiet';
    assert.deepEqual(spokenLines(prompt), ['Hi']);
    const estimate = estimateSpeech(prompt, {rate: 'film', slack: 0});
    assert.equal(estimate.lines, 1);
    assert.equal(estimate.empty, false);
    assert.ok(estimate.needed < 1, estimate);
});

test('a 1.625 s clip has room for only a few English words', () => {
    assert.ok(wordsForDuration(1.625, {rate: 'film', slack: 0.5}) <= 3);
    assert.ok(wordsForDuration(5, {rate: 'film', slack: 0.5}) >= 8);
});
