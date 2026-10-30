import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import * as modules from 'node:module';
import {resolve} from './editor_test_hooks.mjs';
if (modules.registerHooks) modules.registerHooks({resolve});
else modules.register('./editor_test_hooks.mjs', import.meta.url);
import {uploadMedia} from '../web/media_upload.js';
import {outputDownloadURL} from '../web/output_download.js';
import {regenerateResult, upscaleResult, createSecondPassQueue, enableNode} from '../web/studio_queue.js';
import {referenceInputs, mediaRows} from '../web/prompt_references.js';
import {expandCast} from '../web/cast_mention.js';
import {cleanAudioUse, cleanOptionalPrompt, cleanOptionalWidgets, optionalInputValue} from '../web/optional_inputs.js';

class Element {
    constructor(tag) {
        this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {};
        this.classList = {toggle() {}, add() {}};
        this.style = {setProperty(name, value) { this[name] = value; }}; this.offsetHeight = 66;
    }
    append(...children) { for (const child of children) { child.parentNode = this; this.children.push(child); } }
    after(...children) {
        if (!this.parentNode) return;
        const parent = this.parentNode, index = parent.children.indexOf(this);
        for (const child of children) { child.remove?.(); child.parentNode = parent; }
        parent.children.splice(index + 1, 0, ...children);
    }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this); this.parentNode = null; }
    prepend(...children) { this.children.unshift(...children); }
    replaceChildren(...children) { this.children = [...children]; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    querySelector() { return null; }
    querySelectorAll() { return []; }
    getClientRects() { return this.unrendered ? [] : [{}]; }
    get childElementCount() { return this.children.length; }
    click() { this.clicked = true; }
    showModal() { this.open = true; }
    close() { this.open = false; this.onclose?.(); }
}

globalThis.__permissionAsks = [];
test('main browser entry registers both views and preserves node hooks', async t => {
    const extensions = [], opened = [], installed = [], events = new EventTarget();
    const issueURLs = [];
    let refreshed = 0, compatibilityChecks = 0, updateChecks = 0, doneNotifyStarts = 0, rememberedView = null;
    const openingEvents = [];
    let restoreReleases = 0;
    let launchReady = null;
    const opening = {setName: name => openingEvents.push(['name', name]), cover: () => openingEvents.push(['cover']), finish: () => openingEvents.push(['finish'])};
    const casts = [];
    let progressView, progressOptions, realProgressFactory, studioView, doneNotifyOptions;
    const app = {
        registerExtension(extension) { extensions.push(extension); },
        graph: {extra: {freevideo_studio: true}, _nodes: [], getNodeById() {}},
    };
    const api = Object.assign(events, {apiURL: value => value});
    const document = {head: new Element('head'), body: new Element('body'), createElement: tag => new Element(tag),
        createElementNS: (namespaceURI, tag) => Object.assign(new Element(tag), {namespaceURI}),
        createRange: () => ({createContextualFragment: () => new Element('span')})};
    const dependencies = {
        app, api, uploadMedia, outputDownloadURL, regenerateResult, upscaleResult, createSecondPassQueue, enableNode, referenceInputs, mediaRows, expandCast, cleanAudioUse, cleanOptionalPrompt, cleanOptionalWidgets, optionalInputValue,
        createCastLibrary: options => {
            const cast = {element: new Element('cast'), options, destroyed: false, destroy() { this.destroyed = true; }};
            casts.push(cast); return cast;
        },
        secondPassAction: () => new Element('button'),
        createErrorPanel: () => ({element: new Element('error'), show() {}, clear() {}}),
        createErrorReport: () => ({}), adviceFor: () => ({advice: {}}),
        errorText: value => String(value),
        openStudio: node => { opened.push(node); return studioView; },
        loraPanel: () => () => {}, loraWarning: () => 'LoRA warning', promptGuide: () => new Element('guide'),
        createGenerationProgress: (_t, _now, options) => {
            if (realProgressFactory) {
                progressView = realProgressFactory(_t, _now, options);
                return progressView;
            }
            progressOptions = options;
            progressView = {element: new Element('progress'), stall: new Element('stall'), report: new Element('report'),
                updateReport() {}, update() {}, hide() {}, dispose() {}, noticeCount: () => 0,
                stallShown: () => !progressView.stall.hidden, percentShown: () => progressView.percent !== false};
            progressView.stall.hidden = true; progressView.report.hidden = true;
            return progressView;
        },
        referenceTrimText: row => `trimmed ${row.kind} ${row.number}`,
        thermalText: thermal => `thermal ${thermal.sm_clock_mean_mhz}`,
        sharedGpuText: shared => `shared GPU ${shared.other_mean_percent}%`,
        encoderFileText: file => `encoder ${file.sizes.join(' / ')} GiB`,
        encoderFilePath: file => file?.offline_installation ? file.path || '' : '',
        createProgressConnection: () => ({start() {}, refresh() {}, reset() {}}),
        notifyCompatibility: async () => { compatibilityChecks++; },
        startUpdateChecks: () => { updateChecks++; },
        startWhiteboxWorker: () => ({stop() {}}),
        startDoneNotify: options => { doneNotifyStarts++; doneNotifyOptions = options; },
        installNavigation: callback => installed.push(callback),
        refreshNavigation: () => refreshed++, preferredView: () => rememberedView,
        beginWorkspaceRestore: () => { openingEvents.push(['begin']); return opening; }, finishWorkspaceRestore: () => { restoreReleases++; }, waitForLauncherRestore: () => launchReady,
        attachReferencePicker() {}, referenceItems: () => [], syncReferencePrompt() {},
        shareButton: () => new Element('button'), reportFileName: () => 'FreeVideo-report-20261008-101530.json',
        rememberPromptDraft() {}, savePromptDraft: async () => false, openImageEditor: async () => null, icon: () => '',
        AUDIO_USE: {reference: 'reference', original: 'original'}, usesOriginal: row => row?.use === 'original', checkOriginal: async () => ({ok: true}),
        checkedLength: () => null, failureText: () => '', multipleText: () => '', createUseSwitch: () => globalThis.document.createElement('div'), originalBlocked: () => false,
    };
    const previous = new Map();
    for (const [name, value] of Object.entries({
        document, navigator: {language: 'en'}, window: events, open: url => issueURLs.push(new URL(url)), addEventListener: (type, fn, options) => events.addEventListener(type, fn, options),
        getComputedStyle: element => ({marginTop: '8px', marginBottom: '8px', display: element.hidden ? 'none' : 'block', ...element.style}),
        CustomEvent: class extends Event { constructor(name, options) { super(name); this.detail = options?.detail; } },
        requestAnimationFrame: callback => callback(), __freevideoEntryTest: dependencies,
    })) {
        previous.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
        Object.defineProperty(globalThis, name, {value, configurable: true});
    }
    try {
        let source = await readFile(new URL('../web/freevideo.js', import.meta.url), 'utf8');
        source = source.replace("import { beginWorkspaceRestore, finishWorkspaceRestore, waitForLauncherRestore } from './workspace_restore.js';", 'const {beginWorkspaceRestore,finishWorkspaceRestore,waitForLauncherRestore} = globalThis.__freevideoEntryTest;');
        source = source.replace("import { openEditorView } from './editor_loader.js';", 'const openEditorView = (...args) => globalThis.__freevideoEntryTest.openEditorView?.(...args);');
        source = source.replace("from './issue_button.js'", `from '${new URL('../web/issue_button.js', import.meta.url).href}'`);
        source = source.replace("from './prompt_sections.js'", `from '${new URL('../web/prompt_sections.js', import.meta.url).href}'`);
        source = source
            .replace("import { createErrorPanel, errorText, createErrorReport, adviceFor } from './error_panel.js';", 'const {createErrorPanel,errorText,createErrorReport,adviceFor} = globalThis.__freevideoEntryTest;')
            .replace('import { app } from "../../scripts/app.js";', 'const {app} = globalThis.__freevideoEntryTest;')
            .replace('import { api } from "../../scripts/api.js";', 'const {api} = globalThis.__freevideoEntryTest;')
            .replace("import { openStudio, loraPanel, loraWarning, promptGuide } from \"./studio.js\";", 'const {openStudio,loraPanel,loraWarning,promptGuide} = globalThis.__freevideoEntryTest;')
            .replace("import { createGenerationProgress, referenceTrimText, thermalText, sharedGpuText, keepSoundText, encoderFileText, encoderFilePath } from './generation_progress.js';", 'const {createGenerationProgress,referenceTrimText,thermalText,sharedGpuText,encoderFileText,encoderFilePath} = globalThis.__freevideoEntryTest; const keepSoundText = () => "";')
            .replace("import { createProgressConnection } from './progress_connection.js';", 'const {createProgressConnection} = globalThis.__freevideoEntryTest;')
            .replace("import { notifyCompatibility } from './compatibility.js';", 'const {notifyCompatibility} = globalThis.__freevideoEntryTest;')
            .replace("import { startWhiteboxWorker } from './whitebox_worker.js';", 'const {startWhiteboxWorker} = globalThis.__freevideoEntryTest;')
            .replace("import { startUpdateChecks } from './updates.js';", 'const {startUpdateChecks} = globalThis.__freevideoEntryTest;')
            .replace("import { startDoneNotify, askDesktopPermission, DESKTOP_SETTING } from './done_notify.js';", 'const {startDoneNotify} = globalThis.__freevideoEntryTest; const askDesktopPermission = (...args) => globalThis.__permissionAsks.push(args); const DESKTOP_SETTING = "FreeVideo.DoneNotify.Desktop";')
            .replace("import { attachReferencePicker, referenceItems, syncReferencePrompt, referenceInputs, mediaRows, mediaNodeOf } from './prompt_references.js';", 'const {attachReferencePicker,referenceItems,syncReferencePrompt,referenceInputs,mediaRows} = globalThis.__freevideoEntryTest; const mediaNodeOf = () => null;')
            .replace("import { expandCast } from './cast_mention.js';", 'const {expandCast} = globalThis.__freevideoEntryTest;')
            .replace("import { outputDownloadURL } from './output_download.js';", 'const {outputDownloadURL} = globalThis.__freevideoEntryTest;')
            .replace("import { shareButton } from './share.js';", 'const {shareButton} = globalThis.__freevideoEntryTest;')
            .replace("import { reportFileName } from './report_issue.js';", 'const {reportFileName} = globalThis.__freevideoEntryTest;')
            .replace("import { rememberPromptDraft, savePromptDraft, trackPromptDraft, watchPromptHistory, promptHistoryRestoring } from './prompt_draft.js';", 'const {rememberPromptDraft,savePromptDraft} = globalThis.__freevideoEntryTest; const trackPromptDraft = () => {}; const watchPromptHistory = () => {}; const promptHistoryRestoring = () => false;')
            .replace("import { cleanOptionalWidgets, cleanOptionalPrompt, optionalInputValue, cleanAudioUse } from './optional_inputs.js';", 'const {cleanOptionalWidgets,cleanOptionalPrompt,optionalInputValue,cleanAudioUse} = globalThis.__freevideoEntryTest;')
            .replace("import { regenerateResult, upscaleResult } from './studio_queue.js';", 'const {regenerateResult,upscaleResult} = globalThis.__freevideoEntryTest;')
            .replace("import { regenerateResult, createSecondPassQueue, enableNode } from './studio_queue.js';", 'const {regenerateResult,createSecondPassQueue,enableNode} = globalThis.__freevideoEntryTest;')
            .replace("import { secondPassAction } from './result_actions.js';", 'const {secondPassAction} = globalThis.__freevideoEntryTest;')
            .replace("import { openImageEditor, icon } from './image_editor.js';", 'const {openImageEditor,icon} = globalThis.__freevideoEntryTest;')
            .replace("import { installNavigation, refreshNavigation, preferredView, collapseWorkspaces } from './view_navigation.js';", 'const {installNavigation,refreshNavigation,preferredView} = globalThis.__freevideoEntryTest; const collapseWorkspaces = () => { globalThis.__freevideoEntryTest.collapsed = (globalThis.__freevideoEntryTest.collapsed || 0) + 1; };')
            .replace("import { isLaunchVisit } from './launch_visit.js';", 'const isLaunchVisit = () => false;')
            .replace("import { openFreeVideoWorkflow } from './launcher.js';", 'const openFreeVideoWorkflow = (...args) => globalThis.__freevideoEntryTest.openFreeVideoWorkflow?.(...args);')
            .replace("import { imageGenerationSupported } from './image_generation.js';", 'const imageGenerationSupported = () => true;')
            .replace("import { uploadMedia } from './media_upload.js';", 'const {uploadMedia} = globalThis.__freevideoEntryTest;')
            .replace("import { createCastLibrary } from './cast_library.js';", 'const {createCastLibrary} = globalThis.__freevideoEntryTest;')
            .replace("import { swapState, requestRows, swapMissing, withPrefix } from './replace_state.js';", 'const swapState = () => null, requestRows = () => [], swapMissing = () => [], withPrefix = body => body;')
            .replace("import { swapMissingText } from './replace_person.js';", 'const swapMissingText = () => "";')
            .replace("import { agentInterfaceState } from './agent_connect.js';", "const agentInterfaceState = async () => 'off';")
            .replace("import { installNativeErrorGuard } from './native_errors.js';", 'const installNativeErrorGuard = () => {};')
            .replace("import { installTranslateGuard } from './translate_guard.js';", 'const installTranslateGuard = () => {};')
            .replace("import { AUDIO_USE, usesOriginal, checkOriginal, checkedLength, failureText, multipleText, createUseSwitch, originalBlocked } from './audio_original.js';", 'const {AUDIO_USE,usesOriginal,checkOriginal,checkedLength,failureText,multipleText,createUseSwitch,originalBlocked} = globalThis.__freevideoEntryTest;');
        await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
        const extension = extensions.find(row => row.name === 'FreeVideo.UnifiedMedia');
        assert.ok(extension, 'the main extension must register');
        await extension.setup();
        assert.equal(installed.length, 1, 'the Create / Nodes navigation must be installed');
        assert.equal(compatibilityChecks, 1);
        assert.equal(updateChecks, 1);
        assert.equal(doneNotifyStarts, 1);

        await t.test('desktop notification uses the registered default without overwriting stored opt-outs', () => {
            const setting = extension.settings.find(row => row.id === 'FreeVideo.DoneNotify.Desktop');
            assert.equal(setting.defaultValue, true);
            assert.equal(typeof setting.onChange, 'function', 'turning the setting on is a user request');
            globalThis.__permissionAsks.length = 0;
            setting.onChange(true, undefined);
            setting.onChange(true, true);
            setting.onChange(false, true);
            assert.equal(globalThis.__permissionAsks.length, 0, 'registration and unrelated changes never ask');
            setting.onChange(true, false);
            assert.equal(globalThis.__permissionAsks.length, 1, 'switching from off to on asks once');
            assert.equal(doneNotifyOptions.desktop(), true, 'missing values use the new default');
            // ComfyUI keeps explicit values separately and get() uses value ?? default.
            // Registration must not write true over a previously stored false.
            const values = new Map();
            app.extensionManager = {setting: {get: key => values.get(key) ?? setting.defaultValue}};
            app.ui = {settings: {getSettingValue: () => true}};
            assert.equal(doneNotifyOptions.desktop(), true);
            assert.equal(values.size, 0, 'an untouched default is not persisted');
            values.set(setting.id, false);
            assert.equal(doneNotifyOptions.desktop(), false, 'modern false wins over legacy true');
            assert.equal(values.get(setting.id), false);
            app.extensionManager.setting.get = () => undefined;
            app.ui.settings.getSettingValue = () => false;
            assert.equal(doneNotifyOptions.desktop(), false, 'legacy false is also preserved');
            app.ui.settings.getSettingValue = () => undefined;
            assert.equal(doneNotifyOptions.desktop(), true);
            app.graph._nodes.push({type: 'KSampler'});
            assert.equal(doneNotifyOptions.isFreeVideoWorkflow(), false);
            app.graph._nodes.push({comfyClass: 'FreeVideoGenerate'});
            assert.equal(doneNotifyOptions.isFreeVideoWorkflow(), true);
            app.graph._nodes.length = 0;
            delete app.extensionManager; delete app.ui;
        });

        class GenerateNode {
            constructor() {
                this.id = 7; this.type = 'FreeVideoGenerate';
                this.widgets = [{name: 'base_steps', value: 12}, {name: 'refine_steps', value: 3},
                    {name: 'force_regenerate', value: true}];
                this.inputs = []; this.size = [400, 300]; this.dom = []; this.buttons = [];
                this.graph = app.graph;
            }
            onNodeCreated() { this.created = (this.created || 0) + 1; return 'created'; }
            onConfigure() { this.configured = (this.configured || 0) + 1; return 'configured'; }
            onConnectionsChange() { this.connections = (this.connections || 0) + 1; }
            onRemoved(...args) { this.removed = (this.removed || 0) + 1; this.removedArgs = args; return 'removed'; }
            addDOMWidget(name, type, element, options) { this.dom.push({name, type, element, options}); }
            addWidget(type, name, value, callback) { this.buttons.push({type, name, value, callback}); }
            setSize(size) { this.size = size; }
            arrange() { this.arranged = (this.arranged || 0) + 1; }
            computeSize() { return [this.size[0], this.dom.reduce((height, row) => height + (row.options?.getMinHeight?.() || 0), 0)]; }
        }
        for (const [language, tooltip] of [
            ['en', 'Changing sampling steps may lower generation quality. Default: 8 first-pass and 3 second-pass steps. Two-pass acceleration runs only with 8 first-pass steps. Other step counts use a single pass. Other second-pass counts must be fewer than first-pass steps.'],
            ['zh', '修改采样步数可能降低生成质量。默认一采 8 步、二采 3 步；只有一采为 8 步时才会使用二次采样加速，其他步数只采样一遍；其他二采步数需小于一采步数。'],
        ]) {
            await t.test(`${language} node step tooltips include the three-step exception for valid plans`, async () => {
                navigator.language = language;
                try {
                    await import('data:text/javascript;base64,' + Buffer.from(source + `\n// tooltip language: ${language}`).toString('base64'));
                    const localized = extensions.at(-1);
                    class LocalizedGenerateNode extends GenerateNode {}
                    await localized.beforeRegisterNodeDef(LocalizedGenerateNode, {name: 'FreeVideoGenerate'});
                    for (const [base, refine] of [[1, 3], [2, 3], [3, 3], [8, 3], [8, 2]]) {
                        const node = new LocalizedGenerateNode();
                        node.widgets[0].value = base; node.widgets[1].value = refine;
                        node.onNodeCreated();
                        assert.equal(node.widgets[0].tooltip, tooltip, `${base} + ${refine}: first-pass tooltip`);
                        assert.equal(node.widgets[1].tooltip, tooltip, `${base} + ${refine}: refine tooltip`);
                        assert.deepEqual(node.widgets.slice(0, 2).map(widget => widget.value), [base, refine]);
                        node.onRemoved();
                    }
                } finally { navigator.language = 'en'; }
            });
            await t.test(`${language} advanced node labels preserve keys, values and untranslated copy`, async () => {
                navigator.language = language;
                try {
                    await import('data:text/javascript;base64,' + Buffer.from(source + `\n// label language: ${language}`).toString('base64'));
                    const localized = extensions.at(-1);
                    class LocalizedGenerateNode extends GenerateNode {}
                    await localized.beforeRegisterNodeDef(LocalizedGenerateNode, {name: 'FreeVideoGenerate'});
                    const rows = [
                        ['width', 'width', '宽度', 1344],
                        ['height', 'height', '高度', 768],
                        ['seconds', 'Duration (s)', '时长（秒）', 10],
                        ['seed', 'seed', '种子', 2026090903],
                        ['force_regenerate', 'Force regeneration', '重新生成', false, '跳过已保存结果的缓存。'],
                        ['preview', 'Two-pass: preview first pass', '先出预览', false,
                            '先出半分辨率预览，满意后在结果里点“继续二采”，成片与直接生成一致。'],
                        ['refine_from', 'Second pass of preview', '继续二采', 'preview.mp4', '从这个预览继续生成全分辨率视频。'],
                        ['continue_from', 'Continue from video', '接着生成', 'previous.mp4'],
                        ['frames', 'Total frames', '帧数', 141],
                        ['first_frame_reference', 'First-frame reference', '首帧参考', true, '减小漂移'],
                        ['step_cache', 'Step cache', '步间缓存', 'none',
                            '复用相近采样步之间的计算，更快完成，结果会有细微变化。'],
                        ['upscale_factor', 'Upscale factor', '放大倍数', '2', '二次采样时成片相对首遍的放大倍数。'],
                        ['voice_reference', 'Voice reference', 'Voice reference', true],
                        ['hide_tail', 'Hide dialogue tail', 'Hide dialogue tail', true],
                    ];
                    const node = new LocalizedGenerateNode();
                    node.widgets = rows.map(([name, label, , value]) => ({name, label, value, tooltip: `original ${name}`}));
                    node.onNodeCreated();
                    assert.deepEqual(node.widgets.map(w => [w.name, w.value]), rows.map(([name, , , value]) => [name, value]));
                    for (const [name, en, zh, , tooltip] of rows) {
                        const widget = node.widgets.find(w => w.name === name);
                        assert.equal(widget.label, language === 'zh' ? zh : en, name);
                        assert.equal(widget.tooltip, language === 'zh' && tooltip ? tooltip : `original ${name}`, name);
                    }
                    assert.equal(node.widgets.find(w => w.name === 'force_regenerate').type, 'hidden');
                    assert.equal(node.widgets.find(w => w.name === 'force_regenerate').serializeValue(), false);
                    node.onRemoved();
                } finally { navigator.language = 'en'; }
            });
        }
        await extension.beforeRegisterNodeDef(GenerateNode, {name: 'FreeVideoGenerate'});
        const node = new GenerateNode(); app.graph._nodes = [node];
        assert.equal(node.onNodeCreated(), 'created');
        assert.equal(node.created, 1);
        assert.deepEqual(node.dom.map(row => row.name),
            ['freevideo_prompt_guide', 'freevideo_result']);
        assert.deepEqual(node.dom.find(row => row.name === 'freevideo_result').element.children.slice(0, 3).map(child => child.tag),
            ['progress', 'stall', 'report'], 'The separate stall belongs between the progress card and report');
        assert.equal(node.buttons[0].name, 'Open creative workspace');
        assert.equal(node.onConfigure(), 'configured');
        assert.equal(node.configured, 1, 'the original ComfyUI node hook must run exactly once');
        assert.equal(node.widgets[0].value, 12, 'Keep sampling values from public workflows');
        assert.equal(node.widgets[2].serializeValue(), false);
        assert.deepEqual(node.widgets[2].computeSize(), [0, -4]);
        node.widgets[0].value = false; node.onConfigure();
        assert.equal(node.widgets[0].value, 8, 'Migrate the previous private force-checkbox slot');
        node.buttons[0].callback();
        const quiet = opened.length;
        const releasesBefore = restoreReleases;
        extension.afterConfigureGraph();
        assert.equal(opened.length, quiet, 'a tab restored with the page does not open the workspace on its own');
        assert.equal(restoreReleases, releasesBefore + 1, 'the page-load cover is released when nothing opens');
        events.dispatchEvent(new Event('pointerdown'));
        extension.afterConfigureGraph();
        assert.deepEqual(opened, [node, node]);
        // A workflow without FreeVideo nodes is the user's own: every workspace makes way and none opens.
        const nodes = app.graph._nodes, collapsed = dependencies.collapsed || 0;
        app.graph._nodes = [];
        extension.afterConfigureGraph();
        assert.equal(dependencies.collapsed, collapsed + 1); assert.deepEqual(opened, [node, node]);
        // The menu entry opens the workspace of the workflow in front, or FreeVideo's own workflow when it has no node.
        const entry = extension.commands.find(row => row.id === 'FreeVideo.Open');
        assert.equal(entry.label, 'FreeVideo'); assert.deepEqual(extension.menuCommands[0].commands, ['FreeVideo.Open']);
        let workflows = 0;
        dependencies.openFreeVideoWorkflow = async () => { workflows++; return node; };
        const before = opened.length;
        await entry.function();
        assert.equal(workflows, 1); assert.equal(opened.length, before + 1); assert.equal(app.graph._nodes.length, 0);
        app.graph._nodes = nodes;
        await entry.function();
        assert.equal(workflows, 1); assert.equal(opened.length, before + 2);
        app.graph._nodes = [];
        dependencies.openFreeVideoWorkflow = async () => { throw new Error('workflow fixture unavailable'); };
        const warn = console.warn; console.warn = () => {};
        try { await entry.function(); } finally { console.warn = warn; }
        const menuFailure = document.body.children.find(child => child.className?.includes('fv-menu-failure'));
        assert.ok(menuFailure.open);
        const menuIssue = menuFailure.children[1].children[0];
        assert.ok(menuIssue.className.includes('fv-issue-button')); assert.notEqual(menuIssue.hidden, true);
        await menuIssue.onclick();
        assert.match(issueURLs.at(-1).searchParams.get('feedback'), /Error: Could not complete\. Try again\./);
        app.graph._nodes = nodes; await entry.function();
        assert.equal(document.body.children.includes(menuFailure), false, 'successful opening clears the failure and its report button');
        rememberedView = 'editor';
        const restored = [];
        let finishLaunch, finishEditor;
        launchReady = new Promise(resolve => { finishLaunch = resolve; });
        const ready = new Promise(resolve => { finishEditor = resolve; });
        dependencies.openEditorView = (value, options) => { restored.push({node:value, studio:options.studio}); options.onProjectLoaded({name: 'saved project'}); return {ready}; };
        const beforeRestore = opened.length;
        extension.afterConfigureGraph();
        assert.equal(restored.length, 0, 'wait for launcher/session restoration before choosing the workspace');
        finishLaunch(); await Promise.resolve(); await Promise.resolve();
        assert.equal(restored[0].node, node);
        assert.equal(restored[0].studio, undefined, 'restore enters the editor directly');
        assert.equal(opened.length, beforeRestore, 'the creation panel never flashes during a successful recovery');
        assert.deepEqual(openingEvents, [['begin'], ['name', 'saved project'], ['cover']]);
        finishEditor(); await Promise.resolve(); await Promise.resolve();
        assert.deepEqual(openingEvents.at(-1), ['finish'], 'keep the opening status and update suppression until editor.ready');
        launchReady = null;
        await t.test('a graph replaced while launcher restoration waits cannot reopen its editor', async () => {
            let release, calls = 0;
            launchReady = new Promise(resolve => { release = resolve; });
            dependencies.openEditorView = () => { calls++; return {ready: Promise.resolve()}; };
            extension.beforeConfigureGraph(); extension.afterConfigureGraph();
            extension.beforeConfigureGraph(); app.graph._nodes = [];
            const releases = restoreReleases; extension.afterConfigureGraph();
            assert.equal(restoreReleases, releases + 1);
            release(); await Promise.resolve(); await Promise.resolve();
            assert.equal(calls, 0, 'a stale launcher callback must not import or open an editor');
            launchReady = null; app.graph._nodes = nodes;
        });
        await t.test('a graph replaced while the editor import waits cannot cover the new workflow', async () => {
            let release, options;
            dependencies.openEditorView = async (_node, value) => {
                options = value;
                await new Promise(resolve => { release = resolve; });
                return value.isCurrent() ? {ready: Promise.resolve()} : null;
            };
            extension.beforeConfigureGraph(); extension.afterConfigureGraph();
            assert.equal(options.isCurrent(), true);
            extension.beforeConfigureGraph(); app.graph._nodes = []; extension.afterConfigureGraph();
            const covered = openingEvents.filter(row => row[0] === 'cover').length;
            assert.equal(options.isCurrent(), false);
            release(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
            assert.equal(openingEvents.filter(row => row[0] === 'cover').length, covered);
            app.graph._nodes = nodes;
        });
        // The lazy loader owns failed imports: it notifies, reports through onFailure and resolves null.
        const studioStatus = new Element('div'); studioView = {querySelector: () => studioStatus};
        dependencies.openEditorView = async (node, options) => { options.onFailure(new Error('editor unavailable')); return null; };
        extension.afterConfigureGraph(); for (let i = 0; i < 10; i++) await Promise.resolve();
        assert.ok(window.__freevideoHealth.some(row => row.where === 'editor'));
        assert.equal(opened.at(-1), node, 'editor failure leaves the restored studio available');
        const editorIssue = studioStatus.children.find(child => child.className?.includes('fv-issue-button'));
        assert.ok(editorIssue); assert.notEqual(editorIssue.hidden, true);
        await editorIssue.onclick();
        assert.match(issueURLs.at(-1).searchParams.get('feedback'), /Where: Open editor\nError: Could not complete\. Try again\./);
        studioStatus.replaceChildren(); dependencies.openEditorView = async () => ({ready: Promise.resolve()});
        extension.afterConfigureGraph(); for (let i = 0; i < 10; i++) await Promise.resolve();
        assert.equal(studioStatus.children.length, 0, 'successful editor opening adds no issue button');
        studioView = null;
        rememberedView = 'nodes';
        const count = opened.length; extension.afterConfigureGraph();
        assert.equal(opened.length, count, 'remembered Nodes does not open either dialog');
        rememberedView = null;
        assert.ok(refreshed >= 2);
        node.freevideoShowProgress({phase: 'load'});
        // A node already tall enough lays its widgets out again, so the panel grows.
        assert.ok(node.arranged >= 1, 'showing progress lays the node out again');
        const resultWidget = node.dom.find(row => row.name === 'freevideo_result');
        const normalPanelHeight = resultWidget.options.getMinHeight();
        // Before the first percentage the panel reserves less, so the card sits higher.
        progressView.percent = false;
        assert.equal(resultWidget.options.getMinHeight(), normalPanelHeight - 48);
        delete progressView.percent;
        const [width, normalNodeHeight] = node.size;
        progressView.stall.hidden = false; progressOptions.onLayout();
        assert.equal(resultWidget.options.getMinHeight(), normalPanelHeight + 90,
            'Reserve notice space even when the timer changes visibility without an engine event');
        assert.equal(resultWidget.options.getMaxHeight(), normalPanelHeight + 90);
        assert.deepEqual(node.size, [width, normalNodeHeight + 90], 'The layout callback must grow the node and keep its width');
        progressView.stall.scrollHeight = 108;
        assert.equal(resultWidget.options.getMinHeight(), normalPanelHeight + 126,
            'Narrow nodes reserve the measured wrapped notice height and spacing');
        delete progressView.stall.scrollHeight;
        progressView.stall.hidden = true; progressOptions.onLayout();
        assert.equal(resultWidget.options.getMinHeight(), normalPanelHeight);
        assert.equal(resultWidget.options.getMaxHeight(), normalPanelHeight);
        assert.deepEqual(node.size, [width, normalNodeHeight + 90], 'Hiding the notice must preserve the existing node size');
        const video = 'FreeVideo/2026-10-03/' + '1'.repeat(32) + '/video.mp4';
        node.freevideoShowResult({freevideo_summary: [{video, report: video.replace('.mp4', '.debug.json')}]});
        const links = node.dom.find(row => row.name === 'freevideo_result').element.children[1].children;
        assert.deepEqual(node.dom.find(row => row.name === 'freevideo_result').element.children.slice(2, 4).map(child => child.tag),
            ['stall', 'report'], 'Completed results retain the same notice/report order');
        assert.equal(links[0].href, '/freevideo/library/download?' + new URLSearchParams({id: '2026-10-03/'+'1'.repeat(32)}));
        assert.equal(links[0].download, '', 'Use the server filename instead of video.mp4');
        assert.match(links[1].href, /^\/view\?/);
        const encoder_file = {bytes: 27141342152, expected_bytes: 15687142551, sizes: ['25.3', '14.6'],
            launcher: true, offline_installation: false};
        const resultWidth = node.size[0];
        node.setSize([resultWidth, 100]);
        node.freevideoShowResult({freevideo_summary: [{video, result_cache_hit: true, sample_seconds: 99, encoder_file,
            thermal: {sm_clock_mean_mhz: 900}, shared_gpu: {other_mean_percent: 42}}]});
        const reused = node.dom.find(row => row.name === 'freevideo_result').element;
        assert.equal(reused.children[0].hidden, true, 'Do not show the old sampling time as current work');
        assert.ok(reused.children[1].children.some(e => e.textContent === 'Reused previous result'));
        assert.ok(reused.children.some(e => e.className === 'fv-note' && e.textContent === 'encoder 25.3 / 14.6 GiB'),
            'A cached result still came from the same encoder file');
        assert.ok(!reused.children.some(e => e.textContent === 'shared GPU 42%'), 'A cached result did not share the GPU this time');
        assert.deepEqual(node.size, [resultWidth, node.computeSize()[1]], 'Grow the node after appending the encoder notice');
        const again = reused.children[1].children.at(-1);
        assert.equal(again.textContent, 'Regenerate');
        let submitted;
        api.fetchApi = async () => ({ok: true, json: async () => ({})});
        app.graphToPrompt = async () => ({output: {'7': {class_type: 'FreeVideoGenerate', inputs: {text: 'current draft', seed: 42}}}});
        api.queuePrompt = async (_, prompt) => { submitted = prompt; return {prompt_id: 'new'}; };
        await again.onclick();
        assert.equal(submitted.output['7'].inputs.force_regenerate, true);
        assert.equal(submitted.output['7'].inputs.text, 'current draft');
        assert.equal(again.textContent, 'Queued');
        node.freevideoShowProgress({label: 'Preparing video', new_request: true, reset: true});
        assert.ok(!reused.children.some(e => ['fv-stats', 'fv-links'].includes(e.className)),
            'The new video must not display the previous result statistics or downloads');
        const progressHeight = node.size[1];
        node.freevideoShowResult({freevideo_summary: [{video, sample_seconds: 5}]});
        assert.equal(reused.children[0].hidden, false, 'Show statistics again only for a completed result');
        assert.ok(!reused.children.some(e => e.textContent === 'encoder 25.3 / 14.6 GiB'), 'Clear the previous encoder notice');
        assert.ok(node.computeSize()[1] < progressHeight);
        assert.deepEqual(node.size, [resultWidth, progressHeight], 'Shorter results must not shrink the node');
        node.freevideoShowResult({freevideo_summary: [{video, sample_seconds: 5, encoder_file, thermal: {sm_clock_mean_mhz: 900},
            shared_gpu: {other_mean_percent: 42},
            reference_trims: [{kind: 'video', number: 1, seconds: 20.3, used_seconds: 5.17}]}]});
        assert.deepEqual(reused.children.filter(e => e.className === 'fv-note fv-trim-note').map(e => e.textContent),
            ['trimmed video 1'], 'Say which reference was shortened, never cut silently');
        assert.deepEqual(reused.children.filter(e => e.className === 'fv-note' && !e.hidden).map(e => e.textContent),
            ['thermal 900', 'shared GPU 42%', 'encoder 25.3 / 14.6 GiB'], 'Show thermal, shared GPU and encoder notices in order');
        assert.ok(node.computeSize()[1] > progressHeight);
        assert.deepEqual(node.size, [resultWidth, node.computeSize()[1]], 'Measure the result height after all notices are appended');

        await t.test('result widget measures all visible content and keeps other state heights', () => {
            const node = new GenerateNode(); node.onNodeCreated();
            const {element, options} = node.dom.find(row => row.name === 'freevideo_result');
            element.style = {paddingTop: '11px', paddingBottom: '17px', borderTopWidth: '1px', borderBottomWidth: '2px'};
            assert.equal(options.margin, 10);
            assert.equal(options.getMinHeight(), 140);
            node.freevideoShowResult({freevideo_summary: [{video}]});
            assert.equal(options.getMinHeight(), 215, 'Measure statistics, actions, margins, padding and borders');
            element.children[1].offsetHeight = 101;
            element.children[1].style = {marginTop: '3px', marginBottom: '5px'};
            assert.equal(options.getMinHeight(), 242, 'Wrapped actions contribute their actual height');
            node.freevideoShowResult({freevideo_summary: [{video, encoder_file}]});
            assert.equal(options.getMinHeight(), 297, 'Include the notice and both margins');
            assert.equal(options.getMaxHeight(), options.getMinHeight());
            element.unrendered = true;
            assert.equal(options.getMinHeight(), 180, 'Estimate the notices while the canvas is hidden');
            element.unrendered = false;
            node.freevideoShowResult({freevideo_summary: [{video, encoder_file, result_cache_hit: true}]});
            assert.equal(options.getMinHeight(), 215, 'Hidden statistics do not reserve height or margins');
            const path = String.raw`D:\FreeVideo models\文本编码器.safetensors`;
            node.freevideoShowResult({freevideo_summary: [{video, encoder_file: {...encoder_file, offline_installation: true, path}}]});
            const note = element.children.find(e => e.className === 'fv-note' && !e.hidden);
            assert.equal(note.children[0].className, 'fv-encoder-path');
            assert.equal(note.children[0].textContent, path, 'Keep the selectable path separate and unchanged');
            note.offsetHeight = 112;
            assert.equal(options.getMinHeight(), 343, 'Include the wrapped path in its note height');
            node.freevideoShowResult({freevideo_summary: [{video, encoder_file: {...encoder_file, offline_installation: true}}]});
            assert.equal(element.children.find(e => e.className === 'fv-note' && !e.hidden).children.length, 0,
                'A library result without a path has no path line');
            node.freevideoShowResult({freevideo_summary: [{video}]});
            assert.equal(options.getMinHeight(), 215, 'Replacing a result clears its measured notices');
            node.freevideoShowResult({freevideo_summary: [{video, encoder_file}]});
            node.freevideoShowProgress({label: 'Preparing video', new_request: true, reset: true});
            assert.equal(options.getMinHeight(), 330, 'Progress keeps its existing height');
            node.freevideoStopProgress();
            assert.equal(options.getMinHeight(), 140, 'Clearing a result drops its notice height');
            node.freevideoShowFailure({exception_message: 'Failed'});
            assert.equal(options.getMinHeight(), 420, 'Failure keeps its existing height');
            node.freevideoShowResult({freevideo_summary: [{video, low_memory: true, thermal: {sm_clock_mean_mhz: 900},
                shared_gpu: {other_mean_percent: 42},
                reference_trims: [{kind: 'video', number: 1}]}]});
            assert.equal(options.getMinHeight(), 543, 'Add 82 px for the shared GPU notice: 66 px text height and two 8 px margins');
        });

        class MediaNode extends GenerateNode {
            constructor() {
                super(); this.id = 8; this.type = 'FreeVideoMedia';
                this.widgets = [{name: 'assets', value: '[]'}];
                this.inputs = [{name: 'reference_audio', link: 1}];
                this.graph = {links: {1: {origin_id: 9}}, getNodeById: () => ({title: 'Load Audio'}), change() {}};
            }
        }
        await extension.beforeRegisterNodeDef(MediaNode, {name: 'FreeVideoMedia'});
        const media = new MediaNode(); media.onNodeCreated();
        const panel = media.dom.find(row => row.name === 'freevideo_media').element;
        const [castRow, toolbar, mode, note, audioHelp, connections] = panel.children;
        assert.equal(castRow, casts[0].element);
        assert.equal(casts[0].options.api, api);
        assert.equal(typeof casts[0].options.uploadOne, 'function');
        assert.equal(typeof casts[0].options.viewURL, 'function');
        assert.match(mode.textContent, /Reference/);
        assert.equal(audioHelp.hidden, false);
        assert.match(audioHelp.textContent, /<Audio 1>/);
        assert.match(connections.children[0].textContent, /Reference audio.*Connected/);
        const audioButton = toolbar.children.find(e => e.tag === 'button' && e.textContent === 'Reference audio');
        const audioPicker = toolbar.children.find(e => e.tag === 'input' && e.accept.startsWith('.wav'));
        audioButton.onclick(); assert.equal(audioPicker.clicked, true);
        api.fetchApi = async (url, options) => {
            assert.equal(url, '/freevideo/media/upload');
            assert.equal(options.body.get('file').name, 'voice.wav');
            return {ok: true, json: async () => ({file: 'voice.wav'})};
        };
        audioPicker.files = [new File(['wave'], 'voice.wav', {type: 'audio/wav'})];
        await audioPicker.onchange();
        assert.deepEqual(JSON.parse(media.widgets[0].value), [{file: 'voice.wav', role: 'reference', enabled: true}]);
        assert.equal(media.isUploading, false);
        media.inputs = [{name: 'first', link: 1}]; media.onConnectionsChange(); await Promise.resolve();
        assert.match(note.textContent, /Choose keyframes or references/);
        media.widgets[0].value = '[]'; media.inputs = []; media.onConnectionsChange(); await Promise.resolve();
        assert.equal(audioHelp.hidden, true);
        assert.match(mode.textContent, /Text to video/);
        const imageRole = toolbar.children.find(e => e.tag === 'select');
        const imagePicker = toolbar.children.find(e => e.tag === 'input' && e.accept.startsWith('.png'));
        assert.equal(imageRole.value, 'reference');
        api.fetchApi = async () => ({ok: true, json: async () => ({file: 'image.png'})});
        imagePicker.files = [new File(['image'], 'image.png', {type: 'image/png'})];
        await imagePicker.onchange();
        assert.equal(JSON.parse(media.widgets[0].value)[0].role, 'reference');
        imageRole.value = 'first';
        imagePicker.files = [new File(['image'], 'image.png', {type: 'image/png'})];
        await imagePicker.onchange();
        assert.deepEqual(JSON.parse(media.widgets[0].value).map(row => row.role), ['reference', 'first']);
        media.onRemoved();
        assert.equal(casts[0].destroyed, true);
        const mounted = new Element('mount'), disposeMedia = media.freevideoCreateMediaEditor(mounted);
        assert.equal(mounted.children[0].children[0], casts[1].element);
        disposeMedia(); assert.equal(casts[1].destroyed, true);

        await t.test('Generate node removal preserves the original hook when setSize throws', async () => {
            const previousInterval = globalThis.setInterval, previousClear = globalThis.clearInterval;
            const previousWarn = console.warn;
            const timers = new Set(), cleared = [], warnings = [];
            let tick, timerId = 0, removalNode;
            globalThis.setInterval = callback => { tick = callback; timers.add(++timerId); return timerId; };
            globalThis.clearInterval = id => { cleared.push(id); timers.delete(id); };
            console.warn = (...args) => warnings.push(args);
            try {
                const {createGenerationProgress} = await import('../web/generation_progress.js');
                const now = 181000;
                realProgressFactory = (t, _now, options) => createGenerationProgress(t, () => now, options);
                removalNode = new GenerateNode(); removalNode.id = 10; removalNode.onNodeCreated();
                removalNode.freevideoShowProgress({phase: 'load', received_at: 0});
                const progress = progressView;
                assert.deepEqual(progress.element.children.slice(0, 10).map(child => child.className), [
                    'fv-generation-heading', 'fv-generation-track', 'fv-generation-label',
                    'fv-generation-mini', 'fv-generation-frames', 'fv-generation-times', 'fv-generation-late', 'fv-generation-compatibility', 'fv-generation-status', 'fv-generation-live']);
                const activity = progress.snapshot().label;
                removalNode.freevideoShowProgress({compatibility: {level: 2, name_en: 'Compatible', name_zh: '兼容'}});
                assert.equal(progress.snapshot().label, activity, 'The node must not replace the activity for settings-only events');
                assert.match(progress.snapshot().status, /so far|已进行/, 'pause has priority over a settings-only compatibility notice');
                removalNode.freevideoShowProgress({phase: 'recovery', retry: {kind: 'ram_pressure'}});
                assert.match(progress.snapshot().status, /Out of RAM|内存不足/);
                removalNode.freevideoShowProgress({new_request: true, phase: 'load', received_at: 0});
                assert.match(progress.snapshot().status, /so far|已进行/, 'An aged new request clears retry and compatibility but shows its own pause');
                assert.doesNotMatch(progress.snapshot().status, /Out of RAM|内存不足|Compatibility setting|兼容档/);
                assert.equal(progress.stall.hidden, false);
                assert.equal(timers.size, 1);
                let layoutCalls = 0, timersDuringLayout;
                removalNode.setSize = () => {
                    layoutCalls++; timersDuringLayout = timers.size;
                    throw new Error('setSize failed during removal');
                };
                const beforeRemoval = refreshed;
                assert.equal(removalNode.onRemoved('removed from graph'), 'removed');
                assert.equal(removalNode.removed, 1, 'The original hook must run with the node as this');
                assert.deepEqual(removalNode.removedArgs, ['removed from graph']);
                assert.ok(refreshed > beforeRemoval, 'The outer removal hook must also finish');
                assert.equal(progress.element.hidden, true);
                assert.equal(progress.stall.hidden, true);
                assert.equal(timersDuringLayout, 0);
                assert.equal(timers.size, 0);
                assert.deepEqual(cleared, [1]);
                assert.equal(layoutCalls, 1);
                assert.equal(warnings.length, 1);
                assert.match(warnings[0].join(' '), /layout.*setSize failed during removal/i);
                assert.doesNotThrow(() => { tick(); tick(); });
                assert.equal(layoutCalls, 1, 'Queued ticks must not resize the deleted node');
                assert.equal(warnings.length, 1);
            } finally {
                try { progressView?.dispose(); }
                finally {
                    realProgressFactory = null;
                    console.warn = previousWarn;
                    globalThis.setInterval = previousInterval; globalThis.clearInterval = previousClear;
                }
            }
        });
    } finally {
        for (const [name, descriptor] of previous) {
            if (descriptor) Object.defineProperty(globalThis, name, descriptor);
            else delete globalThis[name];
        }
    }
});

test('every node report placement and the Studio output put stall immediately before report', async () => {
    const [entry, studio] = await Promise.all([
        readFile(new URL('../web/freevideo.js', import.meta.url), 'utf8'),
        readFile(new URL('../web/studio.js', import.meta.url), 'utf8'),
    ]);
    const placements = [...entry.matchAll(/panel\.(?:append|replaceChildren)\(([^;]*?\bprogress\.report)\);/g)];
    assert.equal(placements.length, 4, 'Check initial, resumed, reattached and completed node output');
    for (const [, argumentsText] of placements) assert.match(argumentsText, /progress\.stall,\s*progress\.report$/);
    assert.match(studio, /output\.append\([^;]*progress\.stall,\s*progress\.report[,)]/);
});
