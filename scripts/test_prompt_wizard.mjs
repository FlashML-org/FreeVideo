import assert from 'node:assert/strict';
import test from 'node:test';
import {composePrompt, emptyDraft, filledChecks} from '../web/prompt_wizard.js';

test('composePrompt writes H3 sections, camera prose, and <d> dialogue', () => {
    const draft = emptyDraft();
    Object.assign(draft, {
        placeKind: 'inside', place: 'home', time: 'morning', lighting: 'soft natural window light',
        style: 'cinematic', shot: 'medium-wide', motion: 'push-in', motionSpeed: 'slow',
        action: 'She turns toward the door', sounds: 'Quiet room tone and a distant kettle',
        music: 'N/A', captions: 'none',
    });
    draft.characters[0] = {who: 'a young woman', outfit: 'a cream hoodie', mood: 'smiling, relaxed',
        line: 'Hi', language: 'English'};
    const text = composePrompt(draft);
    assert.match(text, /^integrated_multimodal_description:/);
    assert.match(text, /\noverall_soundscape: Quiet room tone/);
    assert.match(text, /\nnon_diegetic_music: N\/A/);
    assert.match(text, /\[Shot 1] Live-action, cinematic, a medium-wide shot frames an interior home in the morning/);
    assert.match(text, /The camera pushes in with small amplitude at slow speed/);
    assert.match(text, /a young woman \(S1\) says: <d>\[English] Hi<\/d>/);
    assert.match(text, /No subtitles, no on-screen text/);
    assert.equal(text.includes('weather'), false);
    const checks = filledChecks(draft);
    assert.equal(Object.values(checks).every(Boolean), true);
});

test('two speakers get stable S1 and S2 ids', () => {
    const draft = emptyDraft();
    draft.characters = [
        {who: 'the woman', outfit: '', mood: '', line: 'Hi', language: 'English'},
        {who: 'the man', outfit: '', mood: '', line: 'Hey', language: 'English'},
    ];
    const text = composePrompt(draft);
    assert.match(text, /the woman \(S1\) says: <d>\[English] Hi<\/d>/);
    assert.match(text, /the man \(S2\) says: <d>\[English] Hey<\/d>/);
});
