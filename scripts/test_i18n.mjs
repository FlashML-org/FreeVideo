import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import { resolveLocale, translateKey, translatePair, nextLocale, SUPPORTED_LOCALES } from '../web/i18n.js';
import { catalogs } from '../web/locales/catalogs.js';

test('locale priority and regional aliases', () => {
    assert.equal(resolveLocale('?freevideo_lang=pt-BR', 'zh-CN'), 'pt-BR');
    assert.equal(resolveLocale('?freevideo_lang=en', 'pt-BR'), 'en');
    assert.equal(resolveLocale('?freevideo_lang=zh', 'en-US'), 'zh');
    assert.equal(resolveLocale('', 'pt-br'), 'pt-BR');
    assert.equal(resolveLocale('', 'zh-TW'), 'zh');
    assert.equal(resolveLocale('', 'fr-FR'), 'en');
    assert.equal(resolveLocale('?freevideo_lang=invalid', 'en-US'), 'en');
});
test('catalogs agree with source and every translation is nonempty', () => {
    const keySet = Object.keys(catalogs.en).sort();
    assert.ok(keySet.length >= 140);
    for (const language of SUPPORTED_LOCALES) {
        assert.deepEqual(Object.keys(catalogs[language]).sort(), keySet);
        assert.deepEqual(catalogs[language], JSON.parse(readFileSync(new URL('../web/locales/'+language+'.json', import.meta.url), 'utf8')));
        for (const key of keySet) assert.ok(catalogs[language][key].trim());
    }
    execFileSync(process.execPath, [fileURLToPath(new URL('./build_locales.mjs', import.meta.url)), '--check']);
});
test('stable keys preserve native English and Chinese with Brazilian Portuguese', () => {
    assert.equal(translateKey('nav.create', 'en'), 'Create');
    assert.equal(translateKey('nav.create', 'zh'), '创作面板');
    assert.equal(translateKey('nav.create', 'pt-BR'), 'Criar');
    assert.equal(translateKey('ui.generate_video', 'pt-BR'), 'Gerar vídeo');
    assert.equal(translateKey('ui.generate_video', 'unsupported'), 'Generate video');
    assert.equal(translateKey('missing.key', 'pt-BR'), 'missing.key');
    assert.equal(translateKey('toString', 'pt-BR'), 'toString');
    assert.equal(translateKey('progress.estimate_minutes', 'pt-BR', {minutes: 18}), 'Cerca de 18 min');
});
test('legacy bridge preserves supplied English and Chinese and falls back for unknown Portuguese copy', () => {
    const old = globalThis.location;
    try {
        globalThis.location = {search: '?freevideo_lang=en'};
        assert.equal(translatePair('Settings','自定义',false), 'Settings');
        globalThis.location.search = '?freevideo_lang=zh';
        assert.equal(translatePair('Settings','自定义',true), '自定义');
        globalThis.location.search = '?freevideo_lang=pt-BR';
        assert.equal(translatePair('Settings','设置',false), 'Configurações');
        assert.equal(translatePair('About 18 min','约 18 分钟',false), 'Cerca de 18 min');
        assert.equal(translatePair('Generating 42%','正在生成 42%',false), 'Gerando 42%');
        assert.equal(translatePair('Light','浅色',false), 'Claro');
        assert.equal(translatePair('Light','光线',false), 'Luz');
        assert.equal(translatePair('Light','轻量',false), 'Leve');
        assert.equal(translatePair('Untranslated original','原文',false), 'Untranslated original');
        assert.equal(translatePair('Settings','设置',true), '设置');
    } finally { if (old === undefined) delete globalThis.location; else globalThis.location=old; }
});
test('all three locales are reachable from command palette', () => {
    assert.equal(nextLocale('en'), 'zh');
    assert.equal(nextLocale('zh'), 'pt-BR');
    assert.equal(nextLocale('pt-BR'), 'en');
});
