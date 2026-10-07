import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {outputDownloadURL} from '../web/output_download.js';
import {regenerateResult, upscaleResult} from '../web/studio_queue.js';
import {knownPrismLevels, loadPrismLevels, prismTextToVideo, rememberPrismLevels} from '../web/sampling_effort.js';
import {modelGrid, preferInstalledModel, prismOnly, swapModelCanvas, trackModel, PRISM_STARTER} from '../web/model_canvas.js';

class Element {
    constructor(tag) {
        this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {};
        this.classList = {toggle() {}}; this.style = {};
    }
    append(...children) { this.children.push(...children); }
    prepend(...children) { this.children.unshift(...children); }
    replaceChildren(...children) { this.children = [...children]; }
    setAttribute(name, value) { this.attributes[name] = value; }
    querySelector() { return null; }
    querySelectorAll() { return []; }
    get childElementCount() { return this.children.length; }
    click() { this.clicked = true; }
}

test('main browser entry registers both views and preserves node hooks', async () => {
    const extensions = [], opened = [], installed = [], events = new EventTarget();
    let refreshed = 0, compatibilityChecks = 0, updateChecks = 0;
    const app = {
        registerExtension(extension) { extensions.push(extension); },
        graph: {extra: {freevideo_studio: true}, _nodes: [], getNodeById() {}},
    };
    const api = Object.assign(events, {apiURL: value => value});
    const document = {head: new Element('head'), createElement: tag => new Element(tag)};
    const dependencies = {
        app, api, outputDownloadURL, regenerateResult, upscaleResult, knownPrismLevels, loadPrismLevels, prismTextToVideo,
        modelGrid, preferInstalledModel, prismOnly, swapModelCanvas, trackModel,
        createErrorPanel: () => ({element: new Element('error'), show() {}, clear() {}}),
        errorText: value => String(value),
        openStudio: node => opened.push(node),
        loraPanel: () => () => {}, loraWarning: () => 'LoRA warning', promptGuide: () => new Element('guide'),
        createGenerationProgress: () => ({element: new Element('progress'), report: new Element('report'), updateReport() {}, update() {}, hide() {}, dispose() {}, noticeCount: () => 0}),
        referenceTrimText: row => `trimmed ${row.kind} ${row.number}`,
        createProgressConnection: () => ({start() {}, refresh() {}, reset() {}}),
        notifyCompatibility: async () => { compatibilityChecks++; },
        startUpdateChecks: () => { updateChecks++; },
        installNavigation: callback => installed.push(callback),
        refreshNavigation: () => refreshed++, preferredView: () => null,
        attachReferencePicker() {}, referenceItems: () => [], syncReferencePrompt() {},
        shareButton: () => new Element('button'),
        rememberPromptDraft() {}, savePromptDraft: async () => false,
    };
    const previous = new Map();
    for (const [name, value] of Object.entries({
        document, navigator: {language: 'en'}, window: events,
        CustomEvent: class extends Event { constructor(name, options) { super(name); this.detail = options?.detail; } },
        requestAnimationFrame: callback => callback(), __freevideoEntryTest: dependencies,
    })) {
        previous.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
        Object.defineProperty(globalThis, name, {value, configurable: true});
    }
    try {
        let source = await readFile(new URL('../web/freevideo.js', import.meta.url), 'utf8');
        source = source
            .replace("import { createErrorPanel, errorText } from './error_panel.js';", 'const {createErrorPanel,errorText} = globalThis.__freevideoEntryTest;')
            .replace('import { app } from "../../scripts/app.js";', 'const {app} = globalThis.__freevideoEntryTest;')
            .replace('import { api } from "../../scripts/api.js";', 'const {api} = globalThis.__freevideoEntryTest;')
            .replace("import { openStudio, loraPanel, loraWarning, promptGuide } from \"./studio.js\";", 'const {openStudio,loraPanel,loraWarning,promptGuide} = globalThis.__freevideoEntryTest;')
            .replace("import { createGenerationProgress, referenceTrimText } from './generation_progress.js';", 'const {createGenerationProgress,referenceTrimText} = globalThis.__freevideoEntryTest;')
            .replace("import { createProgressConnection } from './progress_connection.js';", 'const {createProgressConnection} = globalThis.__freevideoEntryTest;')
            .replace("import { notifyCompatibility } from './compatibility.js';", 'const {notifyCompatibility} = globalThis.__freevideoEntryTest;')
            .replace("import { startUpdateChecks } from './updates.js';", 'const {startUpdateChecks} = globalThis.__freevideoEntryTest;')
            .replace("import { attachReferencePicker, referenceItems, syncReferencePrompt } from './prompt_references.js';", 'const {attachReferencePicker,referenceItems,syncReferencePrompt} = globalThis.__freevideoEntryTest;')
            .replace("import { outputDownloadURL } from './output_download.js';", 'const {outputDownloadURL} = globalThis.__freevideoEntryTest;')
            .replace("import { shareButton } from './share.js';", 'const {shareButton} = globalThis.__freevideoEntryTest;')
            .replace("import { rememberPromptDraft, savePromptDraft } from './prompt_draft.js';", 'const {rememberPromptDraft,savePromptDraft} = globalThis.__freevideoEntryTest;')
            .replace("import { regenerateResult, upscaleResult } from './studio_queue.js';", 'const {regenerateResult,upscaleResult} = globalThis.__freevideoEntryTest;')
            .replace("import { modelGrid, preferInstalledModel, prismOnly, swapModelCanvas, trackModel } from './model_canvas.js';", 'const {modelGrid,preferInstalledModel,prismOnly,swapModelCanvas,trackModel} = globalThis.__freevideoEntryTest;')
            .replace("import { knownPrismLevels, loadPrismLevels, prismTextToVideo } from './sampling_effort.js';", 'const {knownPrismLevels,loadPrismLevels,prismTextToVideo} = globalThis.__freevideoEntryTest;')
            .replace("import { installNavigation, refreshNavigation, preferredView } from './view_navigation.js';", 'const {installNavigation,refreshNavigation,preferredView} = globalThis.__freevideoEntryTest;');
        await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
        const extension = extensions.find(row => row.name === 'FreeVideo.UnifiedMedia');
        assert.ok(extension, 'the main extension must register');
        await extension.setup();
        assert.equal(installed.length, 1, 'the Create / Nodes navigation must be installed');
        assert.equal(compatibilityChecks, 1);
        assert.equal(updateChecks, 1);

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
            addDOMWidget(name, type, element) { this.dom.push({name, type, element}); }
            addWidget(type, name, value, callback) { this.buttons.push({type, name, value, callback}); }
            setSize(size) { this.size = size; }
            computeSize() { return this.size; }
        }
        await extension.beforeRegisterNodeDef(GenerateNode, {name: 'FreeVideoGenerate'});
        const node = new GenerateNode(); app.graph._nodes = [node];
        assert.equal(node.onNodeCreated(), 'created');
        assert.equal(node.created, 1);
        assert.deepEqual(node.dom.map(row => row.name),
            ['freevideo_prompt_guide', 'freevideo_result']);
        assert.equal(node.buttons[0].name, 'Open creative workspace');
        assert.equal(node.onConfigure(), 'configured');
        assert.equal(node.configured, 1, 'the original ComfyUI node hook must run exactly once');
        assert.equal(node.widgets[0].value, 12, 'Keep sampling values from public workflows');
        assert.equal(node.widgets[2].serializeValue(), false);
        assert.deepEqual(node.widgets[2].computeSize(), [0, -4]);
        node.widgets[0].value = false; node.onConfigure();
        assert.equal(node.widgets[0].value, 8, 'Migrate the previous private force-checkbox slot');
        node.buttons[0].callback();
        extension.afterConfigureGraph();
        assert.deepEqual(opened, [node, node]);
        assert.ok(refreshed >= 2);
        const video = 'FreeVideo/2026-10-03/' + '1'.repeat(32) + '/video.mp4';
        node.freevideoShowResult({freevideo_summary: [{video, report: video.replace('.mp4', '.debug.json')}]});
        const links = node.dom.find(row => row.name === 'freevideo_result').element.children[1].children;
        assert.equal(links[0].href, '/freevideo/library/download?' + new URLSearchParams({id: '2026-10-03/'+'1'.repeat(32)}));
        assert.equal(links[0].download, '', 'Use the server filename instead of video.mp4');
        assert.match(links[1].href, /^\/view\?/);
        node.freevideoShowResult({freevideo_summary: [{video, result_cache_hit: true, sample_seconds: 99}]});
        const reused = node.dom.find(row => row.name === 'freevideo_result').element;
        assert.equal(reused.children[0].hidden, true, 'Do not show the old sampling time as current work');
        assert.ok(reused.children[1].children.some(e => e.textContent === 'Reused previous result'));
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
        node.freevideoShowResult({freevideo_summary: [{video, sample_seconds: 5}]});
        assert.equal(reused.children[0].hidden, false, 'Show statistics again only for a completed result');
        node.freevideoShowResult({freevideo_summary: [{video, sample_seconds: 5,
            reference_trims: [{kind: 'video', number: 1, seconds: 20.3, used_seconds: 5.17}]}]});
        assert.deepEqual(reused.children.filter(e => e.className === 'fv-note fv-trim-note').map(e => e.textContent),
            ['trimmed video 1'], 'Say which reference was shortened, never cut silently');

        // Changing Model on the node brings that model's canvas and steps, as the
        // creative workspace does; loading a workflow keeps every saved value.
        rememberPrismLevels([{id: 'light', en: 'Light', zh: '轻量', steps: 8, distilled: true},
                             {id: 'original', en: 'Original', zh: '原版', steps: 50, distilled: false, t2v: true}]);
        class ModelNode extends GenerateNode {
            constructor() {
                super(); this.id = 11;
                this.widgets = Object.entries({width: 1344, height: 768, seconds: 10, base_steps: 8, refine_steps: 3,
                    two_pass: true, model: 'MiniMax H3', prism_quality: 'light'}).map(([name, value]) => ({name, value, options: {}}));
            }
        }
        await extension.beforeRegisterNodeDef(ModelNode, {name: 'FreeVideoGenerate'});
        const switched = new ModelNode(); switched.onNodeCreated();
        const field = name => switched.widgets.find(w => w.name === name);
        const choose = name => { field('model').value = name; field('model').callback(name); };
        choose('Prism (preview)');
        assert.deepEqual(['width', 'height', 'seconds', 'two_pass', 'base_steps'].map(n => field(n).value), [1280, 720, 8.5, false, 8]);
        assert.equal(field('width').options.step2, 16, 'Prism sizes use a 16-pixel grid');
        field('prism_quality').value = 'original'; field('base_steps').value = 50;  // as the workspace sets Original
        choose('MiniMax H3');
        assert.deepEqual(['width', 'height', 'seconds', 'two_pass', 'base_steps', 'refine_steps'].map(n => field(n).value),
            [1344, 768, 10, true, 8, 3], 'MiniMax H3 gets its own canvas and steps back, never 50 steps');
        choose('Prism (preview)');
        assert.equal(field('base_steps').value, 50, 'Prism keeps the steps of its level');
        // A loaded workflow (and undo) keeps its saved values.
        extension.beforeConfigureGraph();
        for (const [name, value] of Object.entries({model: 'MiniMax H3', width: 640, height: 640, base_steps: 12})) field(name).value = value;
        switched.onConfigure({}); field('model').callback('MiniMax H3');
        extension.afterConfigureGraph();
        assert.deepEqual(['width', 'height', 'base_steps'].map(n => field(n).value), [640, 640, 12]);
        choose('Prism (preview)');
        assert.equal(field('width').value, 1280);
        choose('MiniMax H3');
        assert.deepEqual(['width', 'height', 'base_steps'].map(n => field(n).value), [640, 640, 12]);
        // MiniMax H3's untouched default prompt becomes Prism's starter and back; typed text stays.
        class PromptNode extends ModelNode {
            constructor() { super(); this.id = 12; this.widgets.push({name: 'text', value: 'H3 default script'}); }
        }
        await extension.beforeRegisterNodeDef(PromptNode, {name: 'FreeVideoGenerate',
            input: {required: {text: ['STRING', {multiline: true, default: 'H3 default script'}]}}});
        const prompted = new PromptNode(); prompted.onNodeCreated();
        const promptField = prompted.widgets.find(w => w.name === 'text');
        const pick = name => { const w = prompted.widgets.find(w => w.name === 'model'); w.value = name; w.callback(name); };
        pick('Prism (preview)');
        assert.equal(promptField.value, PRISM_STARTER.en);
        assert.match(promptField.value, /<speech>.+<\/speech>/); assert.match(promptField.value, /<sfx>.+<\/sfx>/);
        pick('MiniMax H3');
        assert.equal(promptField.value, 'H3 default script');
        promptField.value = 'My own scene';
        pick('Prism (preview)'); pick('MiniMax H3');
        assert.equal(promptField.value, 'My own scene', 'Never replace text the user typed');
        // A Prism-only installation cannot run MiniMax H3: a loaded workflow's node moves to Prism.
        const nodes = app.graph._nodes;
        app.graph._nodes = [switched]; app.graph.getNodeById = id => app.graph._nodes.find(n => n.id === id);
        api.fetchApi = async url => ({ok: url === '/freevideo/models', json: async () => ({installed: ['prism']})});
        switched.type = 'FreeVideoGenerate';
        extension.afterConfigureGraph();
        await new Promise(resolve => setTimeout(resolve, 0));
        assert.deepEqual(['model', 'width', 'height'].map(n => field(n).value), ['Prism (preview)', 1280, 720]);
        api.fetchApi = async () => ({ok: true, json: async () => ({installed: ['h3', 'prism']})});
        choose('MiniMax H3'); extension.afterConfigureGraph();
        await new Promise(resolve => setTimeout(resolve, 0));
        assert.equal(field('model').value, 'MiniMax H3', 'With MiniMax H3 installed, the saved model stays');
        app.graph._nodes = nodes; app.graph.getNodeById = () => {};

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
        const [toolbar, mode, note, audioHelp, connections] = panel.children;
        assert.match(mode.textContent, /Reference/);
        assert.equal(audioHelp.hidden, false);
        assert.match(audioHelp.textContent, /<Audio 1>/);
        assert.match(connections.children[0].textContent, /Reference audio.*Connected/);
        const audioButton = toolbar.children.find(e => e.tag === 'button' && e.textContent === 'Reference audio');
        const audioPicker = toolbar.children.find(e => e.tag === 'input' && e.accept.startsWith('audio/'));
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
        media.inputs = [{name: 'first', link: 1}]; media.onConnectionsChange();
        assert.match(note.textContent, /Choose keyframes or references/);
        media.widgets[0].value = '[]'; media.inputs = []; media.onConnectionsChange();
        assert.equal(audioHelp.hidden, true);
        assert.match(mode.textContent, /Text to video/);
        const imageRole = toolbar.children.find(e => e.tag === 'select');
        const imagePicker = toolbar.children.find(e => e.tag === 'input' && e.accept.startsWith('image/'));
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
    } finally {
        for (const [name, descriptor] of previous) {
            if (descriptor) Object.defineProperty(globalThis, name, descriptor);
            else delete globalThis[name];
        }
    }
});
